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

BASE = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
DB = os.environ["SUPABASE_DB_URL"]
SHARDS = int(os.environ.get("SHARDS", "1"))
SHARD = int(os.environ.get("SHARD", "0"))
WORKERS = int(os.environ.get("WORKERS", "12"))
DAYS = int(os.environ.get("DAYS", "365"))
LIMIT = int(os.environ.get("LIMIT", "0"))          # 0 — без ограничения

SEGMENTS: dict[str, tuple[str, list[str]]] = {
    "gpu": ("ГПУ — газопоршневые", ["газопоршн", "cummins", "камминз", "jenbacher", "waukesha",
                                    "mwm", "innio", "qsk", "g3512", "g3516"]),
    "gtu": ("ГТУ — газотурбинные", ["газотурб", "турбин", "sgt", "lm6000", "lm2500", "taurus",
                                    "centaur", "solar turbines", "гпа"]),
    "gsho": ("ГШО — горно-шахтное", ["перфоратор", "буров", "epiroc", "atlas copco", "sandvik",
                                     "tamrock", "normet", "пдм", "коронк", "проходческ"]),
    "pumps": ("Насосное оборудование", ["насос", "flowserve", "sulzer", "ksb", "grundfos", "цнс",
                                        "шламов", "warman", "weir", "рабочее колесо"]),
    "compressors": ("Компрессоры", ["компрессор", "винтов", "ingersoll", "kaeser", "воздуходув",
                                    "ресивер", "осушитель воздух"]),
    "beneficiation": ("Дробление и обогащение", ["дробилк", "мельниц", "грохот", "флотац",
                                                 "гидроциклон", "metso", "outotec", "футеровк"]),
    "valves": ("Трубопроводная арматура", ["задвижк", "затвор", "клапан", "кран шаров", "вентиль",
                                           "арматур", "фланц"]),
    "electro": ("Электротехника и приводы", ["трансформатор", "кру", "ктп", "частотн", "чрп",
                                             "электродвигател", "schneider", "ячейк", "кабель"]),
    "instrumentation": ("КИПиА и автоматизация", ["датчик", "расходомер", "манометр", "термопар",
                                                  "emerson", "endress", "yokogawa", "уровнемер"]),
    "conveying": ("Подъёмно-транспортное", ["конвейер", "транспортёр", "лебёдк", "кран мостов",
                                            "редуктор", "тельфер", "роликоопор"]),
    "heat": ("Теплообмен и котельное", ["теплообменник", "alfa laval", "котёл", "котел", "градирн",
                                        "экономайзер", "калорифер"]),
    "mining_machines": ("Карьерная спецтехника", ["самосвал", "экскаватор", "белаз", "komatsu",
                                                  "бульдозер", "автогрейдер"]),
    "bearings": ("Подшипники и уплотнения", ["подшипник", "skf", "timken", "манжет", "сальник",
                                             "john crane", "уплотнени"]),
    "water": ("Водоподготовка и фильтрация", ["мембран", "ультрафильтрац", "осмос", "фильтрующ",
                                              "фильтроэлемент", "водоподготовк", "картридж"]),
    "steel": ("Металлопрокат и трубы", ["швеллер", "двутавр", "металлопрокат", "лист стальн",
                                        "отвод", "тройник", "труба"]),
    "welding": ("Сварка и инструмент", ["сварочн", "электрод", "проволок", "абразив", "сверло", "фреза"]),
}

# Колонки спецификации. Спецификации у всех заказчиков свои, но заголовки повторяются.
COLS = {
    "item_name": ["наименование", "номенклатура", "описание", "предмет", "позиция", "материал",
                  "запчаст", "description", "item", "наимен"],
    "part_number": ["артикул", "парт", "part", "p/n", "обозначение", "каталожн", "код"],
    "oem": ["производител", "изготовител", "бренд", "марка", "oem", "завод", "manufacturer"],
    "qty": ["кол-во", "количество", "кол.", "qty", "quantity"],
    "unit": ["ед.изм", "ед. изм", "единица", "unit", "ед-ца", "ед."],
}
PN_RE = re.compile(r"\b(?=[A-Z0-9]*[0-9])[A-Z0-9][A-Z0-9\-./]{4,24}\b")
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


def classify(text: str) -> str | None:
    t = text.lower().replace("ё", "е")
    best, score = None, 0
    for sid, (_n, words) in SEGMENTS.items():
        n = sum(t.count(w.replace("ё", "е")) for w in words)
        if n > score:
            best, score = sid, n
    return best


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


def text_from_docx(b: bytes) -> str:
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        if "word/document.xml" in z.namelist():
            raw = z.read("word/document.xml").decode("utf-8", "ignore")
            return " ".join(re.findall(r"<w:t[^>]*>([^<]{1,400})</w:t>", raw))
    except Exception:
        return ""
    return ""


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
            m = PN_RE.search(joined.upper())
            rec["part_number"] = m.group(0) if m else ""
        rec["_row"] = joined[:600]
        out.append(rec)
        if len(out) >= 3000:
            break
    return out


def collect_refs(days: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00+03:00")
    uf = bx("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}).get("result") or []
    ffields = [str(u["FIELD_NAME"]) for u in uf if u.get("USER_TYPE_ID") == "file"]
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID"], "order": {"ID": "ASC"}})
    ids = [str(d["ID"]) for d in deals]
    print(f"сделок за {days} дн.: {len(ids)} · файловых полей: {len(ffields)}", flush=True)

    refs: list[dict] = []
    for i in range(0, len(ids), 50):
        j = bx("crm.deal.list", {"filter": {"ID": ids[i:i + 50]}, "select": ["ID"] + ffields})
        for x in j.get("result") or []:
            for f in ffields:
                v = x.get(f)
                if not v:
                    continue
                for fo in (v if isinstance(v, list) else [v]):
                    if isinstance(fo, dict) and (fo.get("id") or fo.get("ID")):
                        refs.append({"deal": str(x["ID"]), "field": f, "origin": "поле сделки", "fo": fo})
    print(f"вложений в полях сделок: {len(refs)}", flush=True)
    return refs


def download(fo: dict) -> bytes | None:
    for key in ("urlMachine", "downloadUrl", "url", "URL_MACHINE", "DOWNLOAD_URL"):
        u = fo.get(key)
        if u:
            try:
                r = requests.get(str(u), timeout=90)
                if r.status_code == 200 and len(r.content) > 200:
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
           "chars": 0, "rows_found": 0, "segment_id": None, "sha256": None}
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
    elif text:
        for line in text.splitlines():
            line = line.strip()
            if len(line) > 8 and not NOISE_ROW.match(line):
                m = PN_RE.search(line.upper())
                items.append({"item_name": line[:300], "part_number": m.group(0) if m else "",
                              "oem": "", "unit": "", "qty": None, "_row": line[:600]})
            if len(items) >= 2000:
                break

    rec["chars"] = len(text)
    rec["rows_found"] = len(items)
    rec["segment_id"] = classify(text) if text else None
    if not items:
        rec["status"] = "пусто"
        rec["reason"] = "нет текстового слоя" if kind in ("pdf", "изображение") else "позиции не распознаны"
        return rec, []
    rec["status"] = "разобран"
    for it in items:
        it["segment_id"] = classify(it.get("_row", "")) or rec["segment_id"]
        it["deal_id"] = ref["deal"]
        it["source_file"] = fid
    return rec, items


def main() -> int:
    if SHARDS > 1:
        print(f"часть {SHARD + 1} из {SHARDS}", flush=True)
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("select file_id from lib_files")
        done = {r[0] for r in cur.fetchall()}
    conn.close()
    print(f"уже разобрано ранее: {len(done)}", flush=True)

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
                psycopg2.extras.execute_values(cur, """
                    insert into lib_demand
                      (segment_id, deal_id, item_name, oem, part_number, qty, unit, source, source_file)
                    values %s""", buf_items, page_size=500)
            if buf_files:
                psycopg2.extras.execute_values(cur, """
                    insert into lib_files
                      (file_id, deal_id, origin, field, kind, size_bytes, status, reason,
                       chars, rows_found, segment_id, sha256)
                    values %s
                    on conflict (file_id) do update set
                      status = excluded.status, reason = excluded.reason, chars = excluded.chars,
                      rows_found = excluded.rows_found, segment_id = excluded.segment_id,
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
                              rec["size_bytes"], rec["status"], rec["reason"], rec["chars"],
                              rec["rows_found"], rec["segment_id"], rec["sha256"]))
            for it in items:
                buf_items.append((it["segment_id"], it["deal_id"], it["item_name"][:500],
                                  (it.get("oem") or "")[:200], (it.get("part_number") or "")[:120],
                                  it.get("qty"), (it.get("unit") or "")[:40],
                                  "спецификация сделки", it["source_file"]))
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
        print(f"    {SEGMENTS.get(sid, (sid, []))[0]:32s} {n:>8d}")
    print("\n✓ разбор части завершён")
    return 0


if __name__ == "__main__":
    sys.exit(main())
