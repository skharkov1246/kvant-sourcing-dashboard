"""Сплошной разбор вложений Битрикса → библиотека рынков (Supabase).

Задача владельца: прочитать ВСЕ файлы, привязанные к сделкам, разобрать и
проиндексировать — это фундамент для машины коммерческих предложений. В карточках
Битрикса строк номенклатуры нет (проверено: штатный метод вернул ноль на 2926
сделках), вся номенклатура — в спецификациях-вложениях.

УСТРОЙСТВО. Работа делится на части (SHARDS × SHARD): каждая часть берёт свою
долю файлов по остатку от деления хеша. Разобранное отмечается в lib_files, поэтому
повторный запуск продолжает с места остановки, а не начинает заново. Файлы качаются
и разбираются в несколько потоков — последовательный обход 22 тысяч вложений идёт
часами.

КУДА ПИШЕМ. Только в Supabase, напрямую по SUPABASE_DB_URL. Ни в репозиторий, ни в
артефакты сборки ничего не попадает: репозиторий публичный, а артефакты открытого
репозитория может скачать кто угодно. В журнал прогона выводятся только агрегаты —
ни имён файлов, ни названий сделок, ни содержимого спецификаций.
"""
from __future__ import annotations

import hashlib
import io
import os
import random
import re
import sys
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docfilter  # noqa: E402  (после sys.path)
from segments import SEGMENTS, classify, name_of  # noqa: E402

# Секреты читаются лениво: без них модуль всё равно импортируется — иначе его
# нельзя ни собрать py_compile, ни импортировать из тестов гейта.
BASE = os.environ.get("BITRIX_WEBHOOK_URL", "").rstrip("/")
DB = os.environ.get("SUPABASE_DB_URL", "")
SHARDS = int(os.environ.get("SHARDS", "1"))
SHARD = int(os.environ.get("SHARD", "0"))
WORKERS = int(os.environ.get("WORKERS", "12"))
DAYS = int(os.environ.get("DAYS", "365"))
LIMIT = int(os.environ.get("LIMIT", "0"))          # 0 — без ограничения
RETRY_FAILED = os.environ.get("RETRY_FAILED", "") not in ("", "0", "false")
# Ворота спецификации можно выключить без выката кода — на случай, если правило
# начнёт отбрасывать нужное. Выключение видно в журнале прогона.
SPECGATE = os.environ.get("SPECGATE", "1") not in ("", "0", "false")
PARSER_VERSION = 2

# Колонки спецификации. Спецификации у всех заказчиков свои, но заголовки повторяются.
COLS = {
    "item_name": ["наименование", "номенклатура", "описание", "предмет", "позиция", "материал",
                  "запчаст", "description", "item", "наимен"],
    "part_number": ["артикул", "парт", "part", "p/n", "обозначение", "каталожн", "код"],
    "oem": ["производител", "изготовител", "бренд", "марка", "oem", "завод", "manufacturer"],
    "qty": ["кол-во", "количество", "кол.", "qty", "quantity"],
    "unit": ["ед.изм", "ед. изм", "единица", "unit", "ед-ца", "ед."],
}
NOISE_ROW = re.compile(r"^(итого|всего|подпись|примечан|№|n\s*п/п|приложение)", re.I)


# Session pooler Supabase допускает лишь 15 одновременных клиентов (EMAXCONNSESSION).
# Поэтому соединение не удерживается на весь прогон: открываем на время записи и сразу
# закрываем, а при отказе ждём и пробуем снова — потерять двадцать минут разбора из-за
# занятого пула недопустимо.
def connect(attempts: int = 12):
    last = None
    for i in range(attempts):
        try:
            c = psycopg2.connect(DB, connect_timeout=20)
            c.autocommit = False
            return c
        except psycopg2.OperationalError as e:
            last = e
            if "EMAXCONNSESSION" not in str(e) and "too many" not in str(e).lower():
                raise
            time.sleep(min(30, 2 ** min(i, 4)) + random.random() * 3)
    raise last


def bx(method: str, params: dict) -> dict:
    for _ in range(4):
        try:
            r = requests.post(f"{BASE}/{method}.json", json=params, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return {}


def bx_all(method: str, params: dict) -> list:
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j:
            return out
        start = j["next"]


def sniff(b: bytes) -> str:
    if b[:2] == b"PK":
        return "xlsx/docx"
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "старый office"
    if b[:4] in (b"\x89PNG",) or b[:3] == b"\xff\xd8\xff":
        return "изображение"
    if b[:4] == b"Rar!" or b[:2] == b"\x1f\x8b":
        return "архив"
    return "прочее"


def rows_from_xlsx(b: bytes) -> list[list[str]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    out: list[list[str]] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                out.append(cells)
            if len(out) > 20000:
                return out
    return out


def rows_from_xls(b: bytes) -> list[list[str]]:
    import xlrd
    wb = xlrd.open_workbook(file_contents=b)
    out: list[list[str]] = []
    for ws in wb.sheets():
        for i in range(ws.nrows):
            cells = [str(c.value).strip() for c in ws.row(i)]
            if any(cells):
                out.append(cells)
    return out


def docx_xml(b: bytes) -> str:
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        if "word/document.xml" in z.namelist():
            return z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return ""
    return ""


def text_from_docx(b: bytes) -> str:
    """Весь текст документа. ВНИМАНИЕ: это одна строка без переводов.

    Спецификация в .docx почти всегда лежит таблицей, и её надо читать
    rows_from_docx. Эта функция годится только для определения сегмента по
    тексту файла целиком: склейка всего документа в одну строку означает, что
    text.splitlines() даст ровно один элемент, и построчный разбор потеряет всё.
    Именно так до 12.09.2026 терялись ячейки таблиц во всех .docx."""
    raw = docx_xml(b)
    return " ".join(re.findall(r"<w:t[^>]*>([^<]{1,400})</w:t>", raw)) if raw else ""


def rows_from_docx(b: bytes) -> list[list[str]]:
    """Таблицы документа построчно: <w:tr> — строка, <w:tc> — ячейка.

    Без этого спецификация в .docx не читается вовсе: текстовый путь склеивает
    документ в одну строку. Разметка Word разносит текст ячейки по нескольким
    <w:t> (правки, форматирование, автозамена), поэтому куски ячейки склеиваем."""
    raw = docx_xml(b)
    if not raw:
        return []
    out: list[list[str]] = []
    for tr in re.findall(r"<w:tr[\s>].*?</w:tr>", raw, re.S):
        cells = []
        for tc in re.findall(r"<w:tc[\s>].*?</w:tc>", tr, re.S):
            cells.append(" ".join(re.findall(r"<w:t[^>]*>([^<]{0,400})</w:t>", tc)).strip())
        if any(cells):
            out.append(cells)
        if len(out) >= 4000:
            break
    return out


def text_from_pdf(b: bytes) -> str:
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(b))
        parts = []
        for pg in rd.pages[:60]:
            parts.append(pg.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def header_map(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Ищем строку заголовков: в ней должно найтись хотя бы два известных названия."""
    for i, row in enumerate(rows[:40]):
        low = [c.lower() for c in row]
        found: dict[str, int] = {}
        for key, words in COLS.items():
            for j, c in enumerate(low):
                if c and any(w in c for w in words):
                    found.setdefault(key, j)
                    break
        if "item_name" in found and len(found) >= 2:
            return i, found
    return -1, {}


def items_from_rows(rows: list[list[str]]) -> list[dict]:
    """Позиции из таблицы. Если заголовков нет — берём самую длинную текстовую
    ячейку строки как наименование: у большинства спецификаций это работает."""
    hi, cols = header_map(rows)
    out: list[dict] = []
    body = rows[hi + 1:] if hi >= 0 else rows
    for row in body:
        if not row:
            continue
        joined = " ".join(row).strip()
        if len(joined) < 6 or NOISE_ROW.match(joined):
            continue
        rec: dict = {}
        if cols:
            def get(key: str) -> str:
                j = cols.get(key, -1)
                return row[j].strip() if 0 <= j < len(row) else ""
            rec["item_name"] = get("item_name")
            rec["part_number"] = get("part_number")
            rec["oem"] = get("oem")
            rec["unit"] = get("unit")
            q = get("qty").replace(",", ".")
            try:
                rec["qty"] = float(re.sub(r"[^\d.]", "", q)) if q else None
            except Exception:
                rec["qty"] = None
        else:
            cand = max(row, key=lambda c: len(c)) if row else ""
            rec["item_name"] = cand
            rec["part_number"] = ""
            rec["oem"] = ""
            rec["unit"] = ""
            rec["qty"] = None
        name = (rec.get("item_name") or "").strip()
        if len(name) < 4 or name.isdigit():
            continue
        if not rec.get("part_number"):
            # Прежнее выражение искало по joined.upper() и принимало за артикул
            # дату 01.09.2026, номер закона 223-ФЗ и любое пятизначное число.
            # docfilter.part_number_of отбрасывает даты, номера пунктов, годы и
            # разряды тысяч. Замена действует только на новые разборы: пересчёт
            # накопленных 113 769 парт-номеров — это UPDATE на полтора миллиона
            # строк с пересчётом fts, отдельная задача.
            rec["part_number"] = docfilter.part_number_of(joined)
        rec["_row"] = joined[:600]
        out.append(rec)
        if len(out) >= 3000:
            break
    return out


def collect_refs(days: int) -> list[dict]:
    """Ссылки на все вложения сделок за период.

    Берём их через crm.item.list (entityTypeId=2), а НЕ через crm.deal.list.
    Разница решающая: crm.deal.list отдаёт у файловых полей только ссылки на
    страницы портала (`crm.deal.show/show_file.php`, `crm_show_file.php`), а те
    требуют сессии пользователя и вебхуку возвращают страницу входа с кодом 200 —
    отсюда прежние «не скачался» на всей выборке. Универсальный item-метод отдаёт
    у тех же полей `urlMachine`: REST-ссылку с одноразовым токеном, по которой
    файл приходит как есть. Проверено на портале 08.09.2026: 22 181 вложение
    у 2 928 сделок года, скачивание отдаёт PDF, XLSX и DOCX.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00+03:00")
    fields = ((bx("crm.item.fields", {"entityTypeId": 2}).get("result") or {}).get("fields") or {})
    ffields = [k for k, v in fields.items() if v.get("type") == "file"]
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID"], "order": {"ID": "ASC"}})
    ids = [int(d["ID"]) for d in deals]
    print(f"сделок за {days} дн.: {len(ids)} · файловых полей: {len(ffields)}", flush=True)

    refs: list[dict] = []
    for i in range(0, len(ids), 50):
        j = bx("crm.item.list", {"entityTypeId": 2, "filter": {"@id": ids[i:i + 50]},
                                 "select": ["id"] + ffields, "start": 0})
        for x in (j.get("result") or {}).get("items") or []:
            for f in ffields:
                v = x.get(f)
                if not v:
                    continue
                for fo in (v if isinstance(v, list) else [v]):
                    if isinstance(fo, dict) and fo.get("urlMachine"):
                        refs.append({"deal": str(x["id"]), "field": f, "origin": "поле сделки", "fo": fo})
    print(f"вложений в полях сделок: {len(refs)}", flush=True)
    return refs


def is_login_page(b: bytes) -> bool:
    """Портал отдаёт страницу входа с кодом 200 — по коду ответа её не отличить.
    Отличаем по содержимому, иначе HTML формы логина уходит в разбор как файл."""
    head = b.lstrip()[:512].lower()
    return head.startswith(b"<!doctype htm") or head.startswith(b"<html")


def download(fo: dict) -> bytes | None:
    for key in ("urlMachine", "downloadUrl", "url", "URL_MACHINE", "DOWNLOAD_URL"):
        u = fo.get(key)
        if u:
            try:
                r = requests.get(str(u), timeout=90)
                if r.status_code == 200 and len(r.content) > 200 and not is_login_page(r.content):
                    return r.content
            except Exception:
                pass
    fid = fo.get("id") or fo.get("ID")
    if fid:
        try:
            u = (bx("disk.file.get", {"id": fid}).get("result") or {}).get("DOWNLOAD_URL")
            if u:
                r = requests.get(u, timeout=90)
                if r.status_code == 200 and len(r.content) > 200:
                    return r.content
        except Exception:
            pass
    return None


def handle(ref: dict) -> tuple[dict, list[dict]]:
    fo = ref["fo"]
    fid = str(fo.get("id") or fo.get("ID"))
    rec = {"file_id": fid, "deal_id": ref["deal"], "origin": ref["origin"], "field": ref["field"],
           "kind": None, "size_bytes": None, "status": "не скачался", "reason": None,
           "chars": 0, "rows_found": 0, "segment_id": None, "sha256": None,
           "parse_path": None, "header_found": None, "doc_class": None,
           "class_rule": None, "text_lines": None, "item_lines": None}
    b = download(fo)
    if not b:
        return rec, []
    rec["size_bytes"] = len(b)
    rec["sha256"] = hashlib.sha256(b).hexdigest()
    kind = sniff(b)
    rec["kind"] = kind
    rows: list[list[str]] = []
    text = ""
    try:
        if kind == "xlsx/docx":
            try:
                rows = rows_from_xlsx(b)
            except Exception:
                # Это .docx. Спецификация в нём лежит таблицей; берём таблицу
                # только при найденной шапке, иначе ветка «самая длинная ячейка»
                # превратит таблицу реквизитов и подписей в строки номенклатуры.
                drows = rows_from_docx(b)
                if drows and header_map(drows)[0] >= 0:
                    rows = drows
                else:
                    text = text_from_docx(b)
        elif kind == "старый office":
            rows = rows_from_xls(b)
        elif kind == "pdf":
            text = text_from_pdf(b)
    except Exception as e:
        rec["status"] = "формат не читаем"
        rec["reason"] = type(e).__name__
        return rec, []

    items: list[dict] = []
    if rows:
        items = items_from_rows(rows)
        text = " ".join(r.get("_row", "") for r in items)[:200000]
        rec["parse_path"] = "таблица"
        rec["header_found"] = header_map(rows)[0] >= 0
    elif text:
        # ВОРОТА СПЕЦИФИКАЦИИ. До 12.09.2026 здесь любая строка длиннее восьми
        # знаков становилась позицией номенклатуры, и в спрос лёг текст извещений
        # о закупке, проектов договоров и форм КП — 369 171 строка, четверть базы.
        #
        # Отказ выносится ЦЕЛИКОМ ПО ФАЙЛУ, а не по строке. Построчный отказ стоил
        # бы до сорока процентов позиций даже в заведомо хорошей спецификации:
        # продолжение наименования, перенесённое на вторую строку, никаких
        # признаков позиции не несёт. Внутри принятого файла пишутся все строки.
        lines = [ln.strip() for ln in text.splitlines()
                 if len(ln.strip()) > 8 and not NOISE_ROW.match(ln.strip())]
        spec_n = prose_n = 0
        for ln in lines:
            sp, pr = docfilter.row_marks(ln[:300])
            spec_n += bool(sp)
            prose_n += bool(pr and not sp)
        rec["parse_path"], rec["header_found"] = "текст", False
        rec["text_lines"], rec["item_lines"] = len(lines), spec_n
        verdict = docfilter.file_verdict(len(lines), spec_n, prose_n)
        if verdict == "документация" and SPECGATE:
            rec["chars"] = len(text)
            rec["status"] = "текст без спецификации"
            rec["reason"] = (f"строк {len(lines)} · с признаками позиции {spec_n} · "
                             f"с признаками текста {prose_n}")      # только агрегаты
            rec["doc_class"], rec["class_rule"] = "документация", docfilter.RULE_VERSION
            return rec, []
        for ln in lines[:2000]:
            items.append({"item_name": ln[:300], "part_number": docfilter.part_number_of(ln),
                          "oem": "", "unit": "", "qty": None, "_row": ln[:600]})

    rec["chars"] = len(text)
    rec["rows_found"] = len(items)
    rec["segment_id"] = classify(text) if text else None
    if not items:
        # «Пусто» перестало врать: тендерный PDF с текстовым слоем, но без позиций,
        # раньше ложился как «нет текстового слоя», и оценка объёма распознавания
        # сканов по этому статусу была завышена.
        rec["status"] = "пусто" if not rec["chars"] else "текст без спецификации"
        rec["reason"] = ("нет текстового слоя" if not rec["chars"]
                         else "позиции не распознаны")
        return rec, []
    rec["status"] = "разобран"
    for it in items:
        # Сегмент СТРОКИ — по самой строке. Наследование от файла помечается
        # отдельно: пока оно молчаливо, segment_id нельзя использовать как эталон
        # (текст строки — подмножество текста файла, поэтому файл либо весь
        # классифицирован, либо весь нет, и любое измерение по нему тавтологично).
        own = classify(it.get("_row", ""))
        it["segment_id"] = own or rec["segment_id"]
        it["segment_rule"] = "строка" if own else ("файл" if rec["segment_id"] else None)
        it["deal_id"] = ref["deal"]
        it["source_file"] = fid
    return rec, items


NULLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def pg(s) -> str:
    """Строка, пригодная для PostgreSQL.

    В тексте, извлечённом из PDF и старых .xls, попадаются нулевые байты и другие
    управляющие символы. PostgreSQL их в text не принимает — psycopg2 падает с
    «A string literal cannot contain NUL (0x00) characters», и вместе с одной
    строкой теряется весь пакет разобранных файлов."""
    return NULLS.sub(" ", str(s or ""))


def ensure_segments(cur) -> None:
    """Справочник сегментов в базе должен существовать ДО записи номенклатуры.

    lib_demand.segment_id ссылается на lib_segments(id); при пустом справочнике
    вся запись падает с ForeignKeyViolation, а разобранные файлы теряются —
    именно так оборвались все двенадцать частей прогона 08.09.2026.
    Источник истины — словарь SEGMENTS в library/segments.py, поэтому справочник
    наполняем из него, а не поддерживаем вручную в двух местах."""
    psycopg2.extras.execute_values(
        cur,
        "insert into lib_segments (id, name) values %s on conflict (id) do nothing",
        [(sid, name) for sid, (name, _words) in SEGMENTS.items()],
    )


def main() -> int:
    for var in ("BITRIX_WEBHOOK_URL", "SUPABASE_DB_URL"):
        if not os.environ.get(var):
            print(f"нет переменной {var}", file=sys.stderr)
            return 2
    if SHARDS > 1:
        print(f"часть {SHARD + 1} из {SHARDS}", flush=True)
    conn = connect()
    with conn.cursor() as cur:
        ensure_segments(cur)
        conn.commit()
        # Повторная попытка для не скачавшихся: «не скачался» — сетевая осечка,
        # а не свойство файла. «Пусто» и «формат не читаем» повторять незачем:
        # результат будет тот же, а прогон подорожает.
        cur.execute("select file_id from lib_files"
                    + (" where status <> 'не скачался'" if RETRY_FAILED else ""))
        done = {r[0] for r in cur.fetchall()}
    conn.close()
    print(f"уже разобрано ранее: {len(done)}"
          + (" (файлы со статусом «не скачался» пойдут заново)" if RETRY_FAILED else ""), flush=True)

    refs = collect_refs(DAYS)
    mine = [r for r in refs
            if int(hashlib.sha1(str(r["fo"].get("id") or r["fo"].get("ID")).encode()).hexdigest(), 16) % SHARDS == SHARD
            and str(r["fo"].get("id") or r["fo"].get("ID")) not in done]
    if LIMIT:
        mine = mine[:LIMIT]
    print(f"к разбору в этой части: {len(mine)}\n", flush=True)
    if not mine:
        print("нечего делать")
        return 0

    stat: Counter = Counter()
    kinds: Counter = Counter()
    segs: Counter = Counter()
    total_items = 0
    buf_files: list[tuple] = []
    buf_items: list[tuple] = []

    def flush() -> None:
        nonlocal buf_files, buf_items
        if not buf_files and not buf_items:
            return
        conn = connect()
        with conn.cursor() as cur:
            if buf_items:
                try:
                    psycopg2.extras.execute_values(cur, """
                        insert into lib_demand
                          (segment_id, deal_id, item_name, oem, part_number, qty, unit, source,
                           source_file, segment_rule)
                        values %s""", buf_items, page_size=500)
                except (psycopg2.Error, ValueError) as e:
                    # одна испорченная строка не должна стоить всего пакета: двадцать минут
                    # разбора уже потрачены, поэтому досылаем построчно и пропускаем битые
                    conn.rollback()
                    bad = 0
                    for row in buf_items:
                        try:
                            cur.execute("""insert into lib_demand
                                (segment_id, deal_id, item_name, oem, part_number, qty, unit, source,
                                 source_file, segment_rule)
                                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
                        except (psycopg2.Error, ValueError):
                            conn.rollback()
                            bad += 1
                    print(f"  ⚠ пакет номенклатуры не прошёл ({type(e).__name__}); "
                          f"построчно записано {len(buf_items) - bad}, пропущено {bad}", flush=True)
            if buf_files:
                psycopg2.extras.execute_values(cur, """
                    insert into lib_files
                      (file_id, deal_id, origin, field, kind, size_bytes, status, reason,
                       chars, rows_found, segment_id, sha256,
                       parse_path, header_found, doc_class, class_rule,
                       text_lines, item_lines, parser_version)
                    values %s
                    on conflict (file_id) do update set
                      status = excluded.status, reason = excluded.reason, chars = excluded.chars,
                      rows_found = excluded.rows_found, segment_id = excluded.segment_id,
                      parse_path = excluded.parse_path, header_found = excluded.header_found,
                      doc_class = excluded.doc_class, class_rule = excluded.class_rule,
                      text_lines = excluded.text_lines, item_lines = excluded.item_lines,
                      parser_version = excluded.parser_version,
                      processed_at = now()""", buf_files, page_size=500)
        conn.commit()
        conn.close()
        buf_files, buf_items = [], []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, (rec, items) in enumerate(pool.map(handle, mine), 1):
            stat[rec["status"]] += 1
            if rec["kind"]:
                kinds[rec["kind"]] += 1
            if rec["segment_id"]:
                segs[rec["segment_id"]] += rec["rows_found"]
            total_items += rec["rows_found"]
            buf_files.append((rec["file_id"], rec["deal_id"], rec["origin"], rec["field"], rec["kind"],
                              rec["size_bytes"], rec["status"], pg(rec["reason"]), rec["chars"],
                              rec["rows_found"], rec["segment_id"], rec["sha256"],
                              rec["parse_path"], rec["header_found"], rec["doc_class"],
                              rec["class_rule"], rec["text_lines"], rec["item_lines"],
                              PARSER_VERSION))
            for it in items:
                buf_items.append((it["segment_id"], it["deal_id"], pg(it["item_name"])[:500],
                                  pg(it.get("oem"))[:200], pg(it.get("part_number"))[:120],
                                  it.get("qty"), pg(it.get("unit"))[:40],
                                  "спецификация сделки", it["source_file"],
                                  it.get("segment_rule")))
            if len(buf_files) >= 200 or len(buf_items) >= 4000:
                flush()
            if n % 200 == 0:
                print(f"  обработано {n} из {len(mine)} · позиций {total_items}", flush=True)
    flush()

    print("\n=== ИТОГ ЧАСТИ ===")
    print(f"файлов: {sum(stat.values())} · позиций номенклатуры: {total_items}")
    print(f"по состоянию: {dict(stat.most_common())}")
    print(f"по формату:   {dict(kinds.most_common())}")
    print("позиции по сегментам:")
    for sid, n in segs.most_common():
        print(f"    {name_of(sid):32s} {n:>8d}")
    print("\n✓ разбор части завершён")
    return 0


if __name__ == "__main__":
    sys.exit(main())
