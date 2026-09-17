#!/usr/bin/env python3
"""Выгрузка ТКП по сделке из Bitrix24: цены из ВЛОЖЕННЫХ ФАЙЛОВ, не из товарных строк.

Зачем. Владелец выставил заказчику твёрдые цены, и защищать предстоит именно их.
В товарных строках сделки этих цен нет (проверено владельцем), они лежат в
приложенных файлах — ТКП, прайсах, входящих КП поставщиков. Разведочные вилки из
gt/data/rfq_prices.json ими НЕ являются: там в основаниях дословно стоит
«дистрибьюторы», «аналог», «экспертная вилка» — это наша оценка рынка.

Что делает (нужен BITRIX_WEBHOOK_URL в окружении):
1) находит сделки по ключевому слову в названии (по умолчанию ЛУКОЙЛ);
2) собирает файлы из трёх мест, а не из одного: файловые UF-поля сделки,
   вложения таймлайна и файловые поля привязанных записей СП-166 «Запросы
   поставщикам» — входящие КП живут именно там;
3) качает каждый файл тремя стратегиями подряд (disk.file.get → DOWNLOAD_URL,
   disk.attachedObject.get, готовый urlMachine из объекта);
4) разбирает xlsx, xls, csv, txt и pdf в строки таблицы;
5) из строк достаёт пары «артикул — цена», сохраняя исходную строку целиком и
   происхождение: файл, лист, номер строки, способ разбора.

ДВА ВЫХОДА, и это не прихоть. Репозиторий публичный, а цены заказчику —
коммерческие данные (SECURITY.md, правило 5 CLAUDE.md):
  --out-full <путь>  полная выгрузка с ценами. НЕ коммитится, уезжает артефактом.
  --out-index <путь> опись БЕЗ цен: какие файлы есть, сколько строк разобрано,
                     по каким артикулам цена найдена. Безопасна для репозитория.

В журнал прогона идут только агрегаты: ни наименований позиций, ни цен, ни имён
файлов заказчика — по правилу 17 CLAUDE.md.

Запуск: Actions → «Bitrix ТКП по сделке» → Run workflow.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402

SPA_RFQ = 166  # смарт-процесс «Запросы поставщикам»

# артикул: те же паттерны, что уже используются в gt/tools/bitrix_attachments.py,
# плюс общий вид «буквы-цифры от пяти знаков» для неопознанных номенклатур
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
# число с пробелами/запятыми как разделителями: «1 234,56», «1,234.56», «86.89»
NUM_RE = re.compile(r"(?<![\dA-Za-z./-])(\d{1,3}(?:[  ]\d{3})+(?:[.,]\d{1,2})?"
                    r"|\d+[.,]\d{1,2}|\d{2,9})(?![\dA-Za-z/-])")
CUR_RE = re.compile(r"\b(USD|EUR|RUB|RUR|GBP|CNY|руб|долл|евро|\$|€|₽)\b", re.I)
# заголовки колонок, по которым опознаём, где цена, а где количество
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


# ------------------------------------------------------- вытаскивание цен из строк

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


# ---------------------------------------------------------------------- скачивание

def download(bx: BitrixClient, fobj: dict):
    """Три стратегии подряд. Возвращает (bytes|None, чем получилось)."""
    import requests
    fid = fobj.get("id") or fobj.get("ID")
    for how, getter in (
        ("disk.file.get", lambda: (bx.call("disk.file.get", {"id": fid}) or {}).get("DOWNLOAD_URL")),
        ("disk.attachedObject.get",
         lambda: ((bx.call("disk.attachedObject.get", {"id": fid}) or {}).get("DOWNLOAD_URL"))),
        ("urlMachine", lambda: fobj.get("urlMachine") or fobj.get("downloadUrl")),
    ):
        try:
            url = getter()
            if not url:
                continue
            rr = requests.get(url, timeout=90)
            if rr.status_code == 200 and len(rr.content) > 200:
                return rr.content, how
        except Exception:
            continue
    return None, "недоступно"


def file_objs(v) -> list[dict]:
    if not v:
        return []
    items = v if isinstance(v, list) else [v]
    return [x for x in items if isinstance(x, dict) and (x.get("id") or x.get("ID"))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="ЛУКОЙЛ", help="подстрока в названии сделки")
    ap.add_argument("--out-full", default="tkp_full.json", help="полная выгрузка С ЦЕНАМИ")
    ap.add_argument("--out-index", default="gt/data/bitrix_tkp_index.json",
                    help="опись БЕЗ цен для репозитория")
    a = ap.parse_args()

    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        return 1
    bx = BitrixClient(wh)

    deals = {str(d["ID"]): d for d in bx.list_paged(
        "crm.deal.list", {"filter": {"%TITLE": a.keyword},
                          "select": ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID"]})}
    print(f"сделок по ключу: {len(deals)}")

    uf = bx.call("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}) or {}
    ufl = uf.get("result", uf) if isinstance(uf, dict) else uf
    deal_file_fields = [u["FIELD_NAME"] for u in (ufl or [])
                        if u.get("USER_TYPE_ID") == "file"]
    print(f"файловых полей сделки: {len(deal_file_fields)}")

    # источники файлов: сделки + привязанные СП-166
    sources: list[tuple[str, str, dict]] = []
    ids = sorted(int(k) for k in deals)
    for i in range(0, len(ids), 50):
        got = bx.call("crm.deal.list", {"filter": {"ID": ids[i:i + 50]},
                                        "select": ["ID", "TITLE"] + deal_file_fields}) or {}
        for x in (got.get("result", got) if isinstance(got, dict) else got):
            for fld in deal_file_fields:
                for fo in file_objs(x.get(fld)):
                    sources.append((f"сделка {x['ID']}", fld, fo))

    n_rfq = 0
    for did in ids:
        try:
            items = bx.list_items(SPA_RFQ, filter={"parentId2": did})
        except Exception:
            items = []
        n_rfq += len(items)
        for it in items:
            for k, v in it.items():
                for fo in file_objs(v):
                    sources.append((f"СП-166 {it.get('id')}", k, fo))
    print(f"привязанных запросов СП-166: {n_rfq}")
    print(f"файлов-кандидатов: {len(sources)}")

    full, index = [], []
    n_dl = n_rows = n_price = 0
    for origin, field, fo in sources:
        name = fo.get("fileName") or fo.get("name") or ""
        content, how = download(bx, fo)
        rec = {"origin": origin, "field": field,
               "file_id": fo.get("id") or fo.get("ID"), "file_name": name,
               "download": how, "size": len(content) if content else 0}
        if not content:
            index.append(dict(rec, rows=0, priced=0, parse_path=""))
            continue
        n_dl += 1
        rows, how_parsed = parse(name, content)
        hdr = header_map(rows) if rows else {}
        pr = price_rows(rows, hdr) if rows else []
        n_rows += len(rows)
        n_price += len(pr)
        # статус файла не должен врать (правило 15 CLAUDE.md)
        status = ("пусто" if not content else
                  "разобран" if pr else
                  "текст без цен" if rows else
                  "не разобрался")
        full.append(dict(rec, parse_path=how_parsed, header=hdr, status=status, prices=pr))
        index.append(dict(rec, parse_path=how_parsed, status=status,
                          rows=len(rows), priced=len(pr),
                          pns=sorted({p["pn"] for p in pr})[:400]))

    Path(a.out_full).write_text(json.dumps(
        {"updated": date.today().isoformat(),
         "source": f"Bitrix24: вложения сделок по ключу «{a.keyword}» и привязанных СП-166",
         "warning": "СОДЕРЖИТ КОММЕРЧЕСКИЕ ЦЕНЫ. В публичный репозиторий не коммитить.",
         "files": full}, ensure_ascii=False, indent=1), encoding="utf-8")

    outi = Path(a.out_index)
    outi.parent.mkdir(parents=True, exist_ok=True)
    outi.write_text(json.dumps(
        {"updated": date.today().isoformat(),
         "source": f"Bitrix24: опись вложений по ключу «{a.keyword}», БЕЗ цен",
         "method": "цены намеренно исключены: репозиторий публичный. Здесь только "
                   "происхождение файла, способ разбора, число строк и артикулы, по "
                   "которым цена найдена — этого достаточно, чтобы свести выгрузку с "
                   "заявкой, не раскрывая коммерческих условий.",
         "deals": len(deals), "rfq_items": n_rfq, "files": len(sources),
         "downloaded": n_dl, "inventory": index}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # только агрегаты в журнал (правило 17 CLAUDE.md)
    print(f"скачано файлов: {n_dl} из {len(sources)}")
    print(f"строк разобрано: {n_rows}")
    print(f"строк с парой «артикул — цена»: {n_price}")
    print(f"уникальных артикулов с ценой: {len({p['pn'] for f in full for p in f['prices']})}")
    print(f"полная выгрузка → {a.out_full} (артефакт, не коммитится)")
    print(f"опись без цен → {a.out_index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
