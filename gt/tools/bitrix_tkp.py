#!/usr/bin/env python3
"""Входящие КП по сделке из Bitrix24: адресный обход, файл за файлом.

Распоряжение владельца 17.09.2026: «Никаких массовых выгрузок не нужно делать.
Они бесполезны и только тратят ресурс. Ты смотришь, читаешь сделку, читаешь
запросы поставщикам, а потом уже видишь файлы и потом их загружаешь. Причём
файлы должны быть не наши исходящие запросы поставщикам, а те, которые появляются
от сорсеров или от контрагентов». Этот инструмент переписан под такой порядок.

ЧЕМ ОН ОТЛИЧАЕТСЯ ОТ ПЕРВОЙ ПОПЫТКИ, которая повисла на 28 минутах и не дала ни
одного файла. Разбор 17.09.2026 вскрыл четыре ошибки, и все четыре учтены здесь:

1. crm.deal.list для файлов НЕ ГОДИТСЯ. Его файловые поля отдают ссылки вида
   crm_show_file.php, которым нужна сессия портала, — вебхук получает СТРАНИЦУ
   ВХОДА с кодом 200 и разбирает её как спецификацию. Рабочий путь один:
   crm.item.list с entityTypeId=2 и ссылка urlMachine. Это измерено в
   base/collect_attachments.py и base/fetch_files.py, здесь оно повторено.
2. Маска select=["*"] файловых полей НЕ ВОЗВРАЩАЕТ. Поля берутся из
   crm.item.fields по type == "file" — одним вызовом за прогон.
3. Ссылка urlMachine уже лежит в объекте, и спрашивать её у disk.* незачем.
   Прежний порядок платил два дросселированных вызова на каждый файл, а при
   ошибке уходил в шесть повторов с паузой до 3,5 минут.
4. Результат писался только в конце, поэтому таймаут унёс всё. Здесь запись
   идёт по ходу, после каждого файла.

НАПРАВЛЕНИЕ ФАЙЛА определяется ИМЕНЕМ ПОЛЯ, а не догадкой по названию файла.
У смарт-процесса 166 «Запросы поставщикам» восемь файловых полей (их состав
записан в base/fetch_rfq.py), и они прямо делятся на наши и чужие: «КП
поставщика» и «Offer from supplier» — входящие, «Request file» — наш исходящий
запрос. Это и есть та очевидность из контекста, которую требовал владелец.

Где ещё искать, кроме полей карточки: комментарии таймлайна, дела (у дела есть
поле DIRECTION — готовый признак входящего письма) и чат сделки, включая
открытые линии с внешним контрагентом.

ДВА ВЫХОДА, потому что репозиторий публичный, а цены заказчику коммерческие
(правило 5 CLAUDE.md, SECURITY.md):
  --out-full   полная выгрузка с ценами. Артефактом, в репозиторий не идёт.
  --out-index  опись БЕЗ цен: что за файл, откуда, чем разобран, сколько строк.

Холостой прогон обязателен перед скачиванием (правило 3 CLAUDE.md):
  python gt/tools/bitrix_tkp.py --keyword ЛУКОЙЛ --dry-run
показывает, сколько файлов нашлось и каких, не скачивая ни одного.

В журнал идут только агрегаты и имена полей (правило 17): ни наименований
позиций, ни цен, ни имён файлов заказчика.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402

DEAL_ENTITY = 2
SPA_RFQ = 166

# Восемь файловых полей СП-166 — состав из base/fetch_rfq.py, там он выверен на
# живом портале. Направление проставлено по смыслу названия: это и есть признак,
# который владелец назвал «очевидным из контекста».
RFQ_FILE_FIELDS = {
    "ufCrm18_1700698211875": ("КП поставщика", "входящее"),
    "ufCrm18_1731179998": ("Offer from supplier", "входящее"),
    "ufCrm18_1703711961310": ("Offer, old", "входящее"),
    "ufCrm18_1703712059311": ("Processed offer", "входящее"),
    "ufCrm18_1703712074559": ("Processed offer with descriptions / archive", "входящее"),
    "ufCrm18_1727423346": ("Request file", "наш запрос"),
    "ufCrm18_1730999038678": ("Мануал, чертеж, шильд", "не цены"),
    "ufCrm18_1730999106096": ("Bank Details", "не цены"),
}
RFQ_SELECT = ["id", "title", "stageId", "parentId2", "ufCrm18Supplier", "companyId",
              "createdTime", "assignedById"]

# что берём в разбор: входящее — обязательно, остальное только с --all-files
WANTED = {"входящее"}

PN_RES = [
    r"\b\d{6,7}-\d{1,4}(?:-\d{1,4})?\b", r"\b\d{6}C\d\b", r"\b64/\d{8}/\d{1,4}\b",
    r"\b[MR][WTU]\d{4,5}[A-Z]?(?:/\d+)?\b", r"\bCT\d{3,5}[A-Z]?/\d+\b",
    r"\bSP0\d{5}\b", r"\bESP0\d{5}\b", r"\bE\d{6}-\d{3}\b",
    r"\b\d{3,4}[A-Z]{1,2}\d{4}[PG]\d{3,4}\b", r"\b\d{8}P\d{3}\b",
    r"\b\d{4}M\d{2}P\d{2}\b", r"\b\d{5,6}/\d{2,3}\b",
    r"\b6ES7[\dA-Z-]{8,}\b", r"\b1794-[A-Z0-9]{2,8}\b", r"\b1756-[A-Z0-9]{2,8}\b",
    r"\b[A-Z0-9]{2,}[-/][A-Z0-9]{2,}(?:[-/][A-Z0-9]{1,})?\b",
]
NBSP = "\u00a0"
CYR = str.maketrans({"А": "A", "В": "B", "С": "C", "Е": "E", "К": "K", "М": "M",
                     "Н": "H", "О": "O", "Р": "P", "Т": "T", "Х": "X"})
NUM_RE = re.compile(r"(?<![\dA-Za-z./-])(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?"
                    r"|\d+[.,]\d{1,2}|\d{2,9})(?![\dA-Za-z/-])")
CUR_RE = re.compile(r"\b(USD|EUR|RUB|RUR|GBP|CNY|руб|долл|евро|\$|€|₽)\b", re.I)
H_PRICE = re.compile(r"цена|стоим|price|amount|сумма|тариф|ставка", re.I)
H_QTY = re.compile(r"кол-?в|количест|qty|quantity|шт\b|ед\b", re.I)
H_PN = re.compile(r"артик|парт|номер|part|p/?n|каталож|обознач", re.I)
H_NAME = re.compile(r"наимен|назван|описан|name|descr|номенкл", re.I)


def norm_pn(s: str) -> str:
    return re.sub(r"\s+", "", str(s).upper().translate(CYR))


def find_pns(text: str) -> list[str]:
    """Артикулы из свободного текста.

    Ищем в двух видах строки сразу. В виде с пробелами работают границы слова:
    «прокладка 304649-100» отдаёт номер, а в склеенном «ПРОКЛАДКА304649-100»
    граница \b перед цифрами пропадает и номер теряется. Склеенный вид нужен
    для pdf, где номер рвётся переносом строки.
    """
    spaced = str(text).upper().translate(CYR)
    glued = re.sub(r"\s+", "", spaced)
    out: list[str] = []
    seen = set()
    for t in (spaced, glued):
        for rx in PN_RES:
            for m in re.findall(rx, t):
                if len(m) >= 5 and m not in seen:
                    seen.add(m)
                    out.append(m)
    return out


def pn_from_cell(v) -> str:
    """Артикул из ячейки, на которую указал заголовок.

    Здесь нельзя опираться на паттерны: у Cummins и Solar номера чисто цифровые
    — 3420932, 1017891, 199515. В свободном тексте такие паттерны хватали бы
    количества и цены, поэтому там их нет намеренно. Но если колонка названа
    «Артикул», доверяем заголовку, а не догадке.
    """
    if v in (None, ""):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = norm_pn(v)
    if not t or len(t) < 5 or len(t) > 40:
        return ""
    if not re.search(r"\d", t):        # без цифры это слово, а не артикул
        return ""
    return t


def to_num(s):
    """Число из ячейки.

    «3 299,51 USD» и «86,89 руб/шт» — тоже числа: в ТКП валюту и единицу пишут
    в той же ячейке, и без отсечения цена теряется целиком.
    """
    if s in (None, "") or isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s) if s > 0 else None
    t = str(s).replace(NBSP, " ").strip()
    t = CUR_RE.sub(" ", t)                                   # USD, руб, $, € — вон
    t = re.sub(r"(?i)\b(шт|ед|компл|за|вкл|без|ндс|vat|pcs?|each)\b", " ", t)
    t = re.sub(r"[^\d., -]", " ", t).strip(" -/")
    t = re.sub(r"[ ](?=\d{3}(?:\D|$))", "", t).strip()       # 1 234 → 1234
    if not t:
        return None
    t = t.split()[0]                    # осталось несколько чисел — берём первое
    t = t.replace(",", ".") if t.count(",") == 1 and t.count(".") == 0 else t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


# ------------------------------------------------------------------ разбор файлов

def rows_from_xlsx(content: bytes) -> list[tuple[str, int, list]]:
    import openpyxl
    out = []
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    for ws in wb.worksheets:
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            if row and any(c not in (None, "") for c in row):
                out.append((ws.title, i, list(row)))
    return out


def rows_from_xls(content: bytes) -> list[tuple[str, int, list]]:
    import xlrd
    out = []
    bk = xlrd.open_workbook(file_contents=content)
    for sh in bk.sheets():
        for i in range(sh.nrows):
            vals = sh.row_values(i)
            if any(v not in (None, "") for v in vals):
                out.append((sh.name, i + 1, vals))
    return out


def rows_from_text(content: bytes) -> list[tuple[str, int, list]]:
    txt = content.decode("utf-8", errors="ignore")
    out = []
    for i, line in enumerate(txt.splitlines(), 1):
        if line.strip():
            cells = re.split(r"[\t;]|(?:\s{2,})", line.strip())
            out.append(("text", i, [c for c in cells if c != ""]))
    return out


def rows_from_pdf(content: bytes) -> list[tuple[str, int, list]]:
    from pypdf import PdfReader
    out = []
    rd = PdfReader(io.BytesIO(content))
    for pno, page in enumerate(rd.pages, 1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            # в pdf нет колонок: строка приходит текстом. Отдать её одной
            # ячейкой нельзя — тогда из «964587C1 109 2,93» ценой оказывался
            # сам артикул. Режем по двум пробелам, а если их нет — по одному.
            cells = re.split(r"\s{2,}", line)
            if len(cells) < 2:
                cells = line.split()
            out.append((f"стр.{pno}", i, cells))
    return out


PARSERS = [
    ((".xlsx", ".xlsm"), rows_from_xlsx, "xlsx"),
    ((".xls",), rows_from_xls, "xls"),
    ((".csv", ".txt", ".tsv"), rows_from_text, "текст"),
    ((".pdf",), rows_from_pdf, "pdf"),
]


def parse(name: str, content: bytes):
    """Возвращает (строки, способ). Отказ выносится ПО ФАЙЛУ, а не по строке —
    правило 13 CLAUDE.md: построчный отказ теряет до 40 % позиций."""
    low = (name or "").lower()
    for exts, fn, tag in PARSERS:
        if low.endswith(exts):
            try:
                return fn(content), tag
            except Exception as e:
                return [], f"{tag}: не разобрался ({type(e).__name__})"
    return [], "формат не поддержан"


def header_map(rows: list) -> dict:
    """Ищем строку заголовков в первых 25 строках и запоминаем, какая колонка чем."""
    best, best_hits = {}, 0
    for _sheet, _i, cells in rows[:25]:
        m, hits = {}, 0
        for j, c in enumerate(cells):
            s = str(c or "")
            if H_PRICE.search(s) and "price" not in m:
                m["price"] = j; hits += 1
            elif H_QTY.search(s) and "qty" not in m:
                m["qty"] = j; hits += 1
            elif H_PN.search(s) and "pn" not in m:
                m["pn"] = j; hits += 1
            elif H_NAME.search(s) and "name" not in m:
                m["name"] = j; hits += 1
        if hits > best_hits:
            best, best_hits = m, hits
    return best if best_hits >= 2 else {}


def price_rows(rows: list, hdr: dict) -> list[dict]:
    """Пары «артикул — цена». Если заголовки нашлись, берём колонки по ним; иначе
    ищем в строке артикул и число. Исходная строка сохраняется целиком: без неё
    следующая ошибка снова будет неизмеримой (правило 16 CLAUDE.md)."""
    out = []
    for sheet, i, cells in rows:
        joined = " | ".join(str(c) for c in cells if c not in (None, ""))
        if not joined:
            continue
        pn = price = None
        rule = ""
        if hdr.get("pn") is not None and hdr["pn"] < len(cells):
            pn = pn_from_cell(cells[hdr["pn"]]) or None
        if pn is None:
            cand = find_pns(joined)
            pn = cand[0] if cand else None
        if pn is None and hdr.get("pn") is None:
            # в ТКП артикул почти всегда в первых двух колонках; смотрим только
            # их, чтобы не принять за номер количество из середины строки
            for c in cells[:2]:
                cand2 = pn_from_cell(c)
                if cand2 and (to_num(c) is None or re.fullmatch(r"\d{6,9}", cand2)):
                    pn = cand2
                    break
        if hdr.get("price") is not None and hdr["price"] < len(cells):
            price = to_num(cells[hdr["price"]])
            rule = "колонка «цена» по заголовку"
        if price is None:
            # без заголовка: берём НАИБОЛЬШЕЕ число строки, но не то, что стоит в
            # колонке количества — иначе цена и количество путаются местами
            # из кандидатов вон и колонка количества, и та ячейка, откуда взят
            # артикул: без этого цифровой номер 3420932 объявляется ценой
            skip = {hdr.get("qty"), hdr.get("pn")}
            if pn:
                skip |= {j for j, c in enumerate(cells)
                         if c not in (None, "") and pn_from_cell(c) == pn}
            nums = [to_num(c) for j, c in enumerate(cells) if j not in skip]
            nums = [n for n in nums if n is not None]
            if nums:
                price = nums[-1]
                rule = "последнее число строки (заголовок не опознан)"
        if pn and price:
            out.append({
                "pn": pn, "price": price, "sheet": sheet, "row": i,
                "currency": (CUR_RE.search(joined) or [""])[0] if CUR_RE.search(joined) else "",
                "raw": joined[:400], "class_rule": rule,
            })
    return out



# ------------------------------------------------------------ сбор кандидатов
# Порядок ровно такой, какой задал владелец: сначала читаем сделку, потом
# запросы поставщикам, потом видим файлы — и лишь затем качаем.


def say(msg: str) -> None:
    """Прогресс в журнал. flush обязателен: stdout в Actions буферизован
    блоками, и без него прогон выглядит повисшим, а не идущим."""
    print(msg, flush=True)


def file_fields(bx: BitrixClient, entity: int) -> dict:
    """Файловые поля сущности: код → название. Один вызов за прогон.

    Маска select=["*"] файловые поля НЕ возвращает — это и было причиной, по
    которой первая версия не получила ни одного файла.
    """
    r = bx.call("crm.item.fields", {"entityTypeId": entity}) or {}
    fields = (r.get("fields") if isinstance(r, dict) else None) or {}
    return {k: (v.get("title") or k) for k, v in fields.items()
            if isinstance(v, dict) and v.get("type") == "file"}


def objs(v) -> list:
    """Файловые объекты из значения поля.

    Требуем urlMachine, а не просто id: это единственная рабочая ссылка для
    вебхука, и ровно этой проверки не хватало в первой версии. Все четыре
    сборщика вложений в репозитории её делают.
    """
    if not v:
        return []
    out = []
    for o in (v if isinstance(v, list) else [v]):
        if isinstance(o, dict) and (o.get("urlMachine") or o.get("downloadUrl")):
            out.append(o)
    return out


def cand(origin: str, field: str, field_name: str, direction: str, o: dict,
         extra: str = "") -> dict:
    return {
        "origin": origin, "field": field, "field_name": field_name,
        "direction": direction,
        "file_id": str(o.get("id") or o.get("ID") or ""),
        "file_name": o.get("name") or o.get("fileName") or o.get("NAME") or "",
        "url": o.get("urlMachine") or o.get("downloadUrl") or "",
        "context": extra,
    }


def from_deal(bx: BitrixClient, did: int, ffields: dict) -> list:
    """Шаг 1: файловые поля самой карточки сделки.

    Через crm.item.list с entityTypeId=2, а НЕ crm.deal.list: у последнего
    ссылки ведут на crm_show_file.php, где нужна сессия портала, и вебхук
    получает страницу входа с кодом 200.
    """
    if not ffields:
        return []
    r = bx.call("crm.item.list", {"entityTypeId": DEAL_ENTITY, "filter": {"@id": [did]},
                                  "select": ["id", "title"] + list(ffields)}) or {}
    items = (r.get("items") if isinstance(r, dict) else None) or []
    out = []
    for it in items:
        for code, title in ffields.items():
            for o in objs(it.get(code)):
                # у сделки направление по имени поля не читается — помечаем как
                # неизвестное и решаем уже по содержимому файла
                out.append(cand(f"сделка {did}", code, title, "неизвестно", o))
    return out


def from_rfq(bx: BitrixClient, did: int) -> tuple[list, int]:
    """Шаг 2: привязанные записи СП-166 «Запросы поставщикам».

    Здесь направление читается прямо: имя файлового поля говорит, наш это
    запрос или присланное поставщиком КП.
    """
    try:
        items = bx.list_items(SPA_RFQ, filter={"parentId2": did},
                              select=RFQ_SELECT + list(RFQ_FILE_FIELDS))
    except Exception as e:
        say(f"    СП-166: не прочитались ({type(e).__name__})")
        return [], 0
    out = []
    for it in items:
        rid = it.get("id")
        sup = str(it.get("ufCrm18Supplier") or "")
        for code, (title, direction) in RFQ_FILE_FIELDS.items():
            for o in objs(it.get(code)):
                out.append(cand(f"СП-166 {rid}", code, title, direction, o,
                                extra=f"поставщик {sup}" if sup else ""))
    return out, len(items)


def from_timeline(bx: BitrixClient, did: int) -> list:
    """Шаг 3: комментарии таймлайна. Сюда сорсер кладёт полученное КП."""
    out = []
    try:
        r = bx.call("crm.timeline.comment.list", {
            "filter": {"ENTITY_ID": did, "ENTITY_TYPE": "deal"},
            "select": ["ID", "COMMENT", "FILES", "AUTHOR_ID", "CREATED"]}) or {}
        rows = (r.get("result") if isinstance(r, dict) else None) or (
            r if isinstance(r, list) else [])
    except Exception as e:
        say(f"    таймлайн: не прочитался ({type(e).__name__})")
        return out
    for c in rows:
        txt = str(c.get("COMMENT") or "")[:200]
        for o in objs(c.get("FILES")):
            out.append(cand(f"комментарий {c.get('ID')}", "FILES", "вложение комментария",
                            direction_from_text(txt), o, extra=txt))
    return out


INCOMING_WORDS = re.compile(
    r"получ|присл|прише?л|во влож|предлож|оффер|offer|quotat|proposal|"
    r"ответ|от поставщик|цены|прайс", re.I)
OUTGOING_WORDS = re.compile(
    r"отправ|направ|запрос|высла|разосла|наше тз|наш запрос|rfq|request", re.I)


def direction_from_text(txt: str) -> str:
    """Направление по тексту рядом с файлом. Слабый признак, но лучше, чем ничего.

    Асимметрия цены ошибки: ошибочно отброшенное КП мы не увидим никогда, а
    ошибочно взятый наш же запрос виден сразу по отсутствию цен. Поэтому при
    любом сомнении — «неизвестно», а не «наш запрос».
    """
    if not txt:
        return "неизвестно"
    inc, out = bool(INCOMING_WORDS.search(txt)), bool(OUTGOING_WORDS.search(txt))
    if inc and not out:
        return "входящее"
    if out and not inc:
        return "наш запрос"
    return "неизвестно"


def from_activities(bx: BitrixClient, did: int) -> list:
    """Шаг 4: дела сделки — письма и звонки. У дела есть DIRECTION."""
    out = []
    try:
        rows = bx.list_paged("crm.activity.list", {
            "filter": {"OWNER_ID": did, "OWNER_TYPE_ID": DEAL_ENTITY},
            "select": ["ID", "SUBJECT", "DIRECTION", "FILES", "STORAGE_ELEMENT_IDS",
                       "PROVIDER_TYPE_ID", "AUTHOR_ID"]})
    except Exception as e:
        say(f"    дела: не прочитались ({type(e).__name__})")
        return out
    for a in rows:
        # DIRECTION: 1 — входящее, 2 — исходящее (документация crm.activity)
        d = str(a.get("DIRECTION") or "")
        direction = "входящее" if d == "1" else ("наш запрос" if d == "2" else "неизвестно")
        subj = str(a.get("SUBJECT") or "")[:200]
        if direction == "неизвестно":
            direction = direction_from_text(subj)
        for o in objs(a.get("FILES")):
            out.append(cand(f"дело {a.get('ID')}", "FILES", "вложение дела",
                            direction, o, extra=subj))
    return out


def from_chat(bx: BitrixClient, did: int) -> list:
    """Шаг 5: чат сделки, включая открытые линии с внешним контрагентом.

    Владелец прямо сказал, что КП кидают в переписку. Скоуп im может быть не
    выдан — тогда фиксируем причину, а не молчим.
    """
    out = []
    try:
        cid = bx.deal_chat_id(did)
    except Exception as e:
        say(f"    чат: id не получен ({type(e).__name__})")
        return out
    if not cid:
        return out
    try:
        msgs = bx.chat_messages(cid, limit=200)
    except Exception as e:
        say(f"    чат {cid}: сообщения не прочитались ({type(e).__name__})")
        return out
    for m in msgs or []:
        txt = str(m.get("text") or m.get("TEXT") or "")[:200]
        direction = direction_from_text(txt)
        for key in ("files", "FILES", "attach", "ATTACH"):
            for o in objs(m.get(key)):
                out.append(cand(f"чат {cid}", key, "файл чата", direction, o, extra=txt))
    return out


# ---------------------------------------------------------------- скачивание

def fetch(url: str, timeout: int = 45):
    """Байты по машинной ссылке. Прямой GET, без вызовов disk.*.

    urlMachine уже лежит в объекте и содержит одноразовый токен. Прежняя версия
    спрашивала ссылку у disk.file.get и disk.attachedObject.get ПЕРЕД тем как
    посмотреть в объект — два дросселированных вызова на каждый файл впустую, а
    при ошибке шесть повторов с паузой до 3,5 минут. Отсюда и 28 минут.
    """
    import requests
    try:
        rr = requests.get(url, timeout=timeout)
    except Exception as e:
        return None, f"сеть: {type(e).__name__}"
    if rr.status_code != 200:
        return None, f"HTTP {rr.status_code}"
    body = rr.content
    if len(body) < 200:
        return None, "пусто"
    # вебхук без прав получает страницу входа с кодом 200 — это измерено в
    # base/collect_attachments.py. Такую «спецификацию» разбирать нельзя.
    head = body[:600].lower()
    if b"<html" in head and (b"login" in head or b"auth" in head or b"bitrix" in head):
        return None, "страница входа вместо файла (нет прав на ссылку)"
    return body, "ок"


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deal", type=int, help="одна сделка по id")
    g.add_argument("--keyword", help="подстрока в названии сделки")
    ap.add_argument("--dry-run", action="store_true",
                    help="холостой прогон: показать найденные файлы, ничего не качая")
    ap.add_argument("--all-files", action="store_true",
                    help="качать и наши запросы тоже, не только входящие")
    ap.add_argument("--max-files", type=int, default=400, help="предел числа файлов")
    ap.add_argument("--out-full", default="tkp_full.json")
    ap.add_argument("--out-index", default="gt/data/bitrix_tkp_index.json")
    a = ap.parse_args()

    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        say("нет BITRIX_WEBHOOK_URL")
        return 1
    bx = BitrixClient(wh)

    if a.deal:
        ids = [a.deal]
    else:
        deals = bx.list_paged("crm.deal.list", {"filter": {"%TITLE": a.keyword},
                                                "select": ["ID", "TITLE"]})
        ids = sorted(int(d["ID"]) for d in deals)
    say(f"сделок к обходу: {len(ids)}")

    ffields = file_fields(bx, DEAL_ENTITY)
    say(f"файловых полей у сделки: {len(ffields)}")

    # --- сбор кандидатов: сделка за сделкой, с прогрессом ---
    cands, seen, rfq_total = [], set(), 0
    for n, did in enumerate(ids, 1):
        say(f"[{n}/{len(ids)}] сделка {did}")
        got = []
        got += from_deal(bx, did, ffields)
        rq, cnt = from_rfq(bx, did)
        rfq_total += cnt
        got += rq
        got += from_timeline(bx, did)
        got += from_activities(bx, did)
        got += from_chat(bx, did)
        fresh = 0
        for c in got:
            key = c["file_id"] or c["url"][:120]
            if key in seen:
                continue
            seen.add(key)
            cands.append(c)
            fresh += 1
        by_dir = {}
        for c in got:
            by_dir[c["direction"]] = by_dir.get(c["direction"], 0) + 1
        say(f"    запросов СП-166: {cnt} · файлов найдено {len(got)}, новых {fresh}"
            + (f" · по направлению: {by_dir}" if by_dir else ""))

    say(f"итого кандидатов: {len(cands)} (дублей снято {len(seen) - len(cands) if len(seen) > len(cands) else 0})")
    by_field = {}
    for c in cands:
        k = f'{c["field_name"]} [{c["direction"]}]'
        by_field[k] = by_field.get(k, 0) + 1
    for k in sorted(by_field, key=lambda x: -by_field[x]):
        say(f"    {by_field[k]:>4}  {k}")

    take = [c for c in cands if a.all_files or c["direction"] in WANTED
            or c["direction"] == "неизвестно"]
    say(f"к разбору: {len(take)} (входящие и неопознанные; наши запросы "
        f"{'включены' if a.all_files else 'исключены'})")

    if a.dry_run:
        Path(a.out_index).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out_index).write_text(json.dumps(
            {"updated": "holostoy", "deals": len(ids), "rfq_items": rfq_total,
             "files": len(cands), "downloaded": 0,
             "inventory": [{k: v for k, v in c.items() if k != "url"} for c in cands]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        say("холостой прогон: ничего не скачано, опись записана")
        return 0

    # --- скачивание и разбор: по одному, с записью ПОСЛЕ КАЖДОГО файла ---
    full, index = [], []
    n_dl = n_rows = n_price = 0
    t0 = time.time()

    def dump():
        Path(a.out_full).write_text(json.dumps(
            {"updated": date.today().isoformat(),
             "source": "Bitrix24: входящие КП по сделке, адресный обход",
             "warning": "СОДЕРЖИТ КОММЕРЧЕСКИЕ ЦЕНЫ. В публичный репозиторий не коммитить.",
             "deals": ids, "files": full}, ensure_ascii=False, indent=1), encoding="utf-8")
        Path(a.out_index).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out_index).write_text(json.dumps(
            {"updated": date.today().isoformat(),
             "source": "Bitrix24: опись входящих КП по сделке, БЕЗ цен",
             "method": "цены исключены намеренно: репозиторий публичный. Здесь "
                       "происхождение файла, направление, способ разбора, число строк "
                       "и артикулы с ценой — этого хватает, чтобы свести с заявкой.",
             "deals": len(ids), "rfq_items": rfq_total, "files": len(cands),
             "downloaded": n_dl, "inventory": index}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    for n, c in enumerate(take[:a.max_files], 1):
        body, how = fetch(c["url"])
        rec = {k: v for k, v in c.items() if k != "url"}
        rec["download"] = how
        rec["size"] = len(body) if body else 0
        if not body:
            index.append(dict(rec, rows=0, priced=0, parse_path="", status="не скачан"))
            if n % 10 == 0 or n == len(take):
                say(f"  [{n}/{min(len(take), a.max_files)}] скачано {n_dl}, "
                    f"строк {n_rows}, с ценой {n_price}, {(time.time()-t0)/60:.1f} мин")
                dump()
            continue
        n_dl += 1
        rows, how_parsed = parse(c["file_name"], body)
        hdr = header_map(rows) if rows else {}
        pr = price_rows(rows, hdr) if rows else []
        n_rows += len(rows)
        n_price += len(pr)
        status = ("пусто" if not body else "разобран" if pr
                  else "текст без цен" if rows else "не разобрался")
        full.append(dict(rec, parse_path=how_parsed, header=hdr, status=status, prices=pr))
        index.append(dict(rec, parse_path=how_parsed, status=status, rows=len(rows),
                          priced=len(pr), pns=sorted({p["pn"] for p in pr})[:400]))
        if n % 10 == 0 or n == min(len(take), a.max_files):
            say(f"  [{n}/{min(len(take), a.max_files)}] скачано {n_dl}, "
                f"строк {n_rows}, с ценой {n_price}, {(time.time()-t0)/60:.1f} мин")
            dump()

    dump()
    say(f"скачано файлов: {n_dl} из {min(len(take), a.max_files)}")
    say(f"строк разобрано: {n_rows}")
    say(f"строк с парой «артикул — цена»: {n_price}")
    say(f"уникальных артикулов с ценой: {len({p['pn'] for f in full for p in f['prices']})}")
    st = {}
    for r in index:
        st[r["status"]] = st.get(r["status"], 0) + 1
    say(f"по статусу файлов: {st}")
    if len(take) > a.max_files:
        say(f"ВНИМАНИЕ: предел --max-files {a.max_files}, не разобрано "
            f"{len(take) - a.max_files} файлов — это не «всё покрыто»")
    return 0


if __name__ == "__main__":
    sys.exit(main())
