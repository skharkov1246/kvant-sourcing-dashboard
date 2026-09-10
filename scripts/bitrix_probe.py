#!/usr/bin/env python3
"""Зонд v42: сплошная выгрузка и разбор файлов по запросам поставщикам (СП-166).

ЗАЧЕМ. Сорсинг утверждает, что по сделке на 2,5 млрд прокотированы все позиции и на
всё есть живое КП. По стадиям воронки это не подтверждается. Но стадия — отметка
человека. Истина в файлах, которые поставщики реально прислали: зонд их достаёт,
разбирает и считает, сколько строк заказчика действительно имеют цену.

ЧТО ДЕЛАЕТ.
 1. Тянет все записи СП-166 за период вместе с восемью файловыми полями.
 2. Отмечает запросы по нашей номенклатуре (бренды обоих RFQ) и по нашим сделкам.
 3. Собирает файловые объекты из трёх мест: поля записи, дела и письма таймлайна
    запроса, письма таймлайна наших сделок.
 4. Качает каждый файл в двенадцать потоков — это ожидание сети, процессор свободен.
    Способ скачивания выбран по замеру v35: годится только urlMachine.
 5. Разбирает xlsx / xls / docx / pdf сразу, а картинки — отдельной фазой в два потока:
    tesseract съедает ядро целиком, и двенадцать копий на двухъядерном раннере просто
    не укладываются в таймаут (так провалился v41 — три скана из пятидесяти восьми).
 6. Считает покрытие строк заказчика и показывает, ЧЕЙ документ его даёт: в тех же
    вложениях лежат наше исходящее ТКП и бюджет заказчика, и по одним числам их от
    ответа поставщика не отличить.

КУДА РЕЗУЛЬТАТ. Репозиторий публичный, поэтому цены в журнал не печатаются: только
агрегаты и метаданные запросов. Построчный разбор выгружается по флагу PROBE_SEAL=1 —
сжатым и зашифрованным на открытый ключ scripts/probe_pubkey.pem.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402

SPA = 166
PERIOD_FROM = os.getenv("PROBE_FROM", "2026-01-01")
PUBKEY = ROOT / "scripts/probe_pubkey.pem"
DEADLINE = time.monotonic() + float(os.getenv("PROBE_BUDGET_SEC", "600"))
ACT_DEADLINE = DEADLINE - 360          # таймлайн не должен съесть скачивание и OCR
OCR_DEADLINE = DEADLINE - 40           # чтобы итоги успели напечататься
MAX_FILES = int(os.getenv("PROBE_MAX_FILES", "2500"))
OCR_TIMEOUT = int(os.getenv("PROBE_OCR_TIMEOUT", "45"))   # секунд на одну картинку
OCR_WORKERS = int(os.getenv("PROBE_OCR_WORKERS", "2"))    # tesseract занимает ядро целиком
OCR_MAX_BYTES = 12_000_000
OCR_MAX_SIDE = 2000

GOT_QUOTE = {"Selected", "Not Selected", "Price at Work"}
OUR_DEALS = {"22566", "22564", "22568", "22016", "21926", "22292", "22282", "18016"}

TOPICS = {
    "Solar/Taurus": ["solar", "солар", "taurus", "таурус", "centaur", "titan"],
    "Siemens SGT": ["siemens", "сименс", "sgt", "simatic", "симатик", "innomotics"],
    "Cummins/ГПЭС": ["cummins", "камминз", "qsk", "qsv", "гпэс"],
    "Jenbacher/INNIO": ["jenbacher", "дженбах", "innio"],
    "Bently Nevada": ["bently", "бентли", "nevada", "3500/"],
    "Fleetguard/фильтры": ["fleetguard", "флитгард", "фильтр", "filter", "af25", "lf3"],
    "ABB": ["abb", "абб"],
    "Буровое НВН": ["bentec", "бентек", "m-i swaco", "swaco", "totco", "нвн", "буров"],
    "SLB/Cameron": ["cameron", "камерон", "slb", "schlumberger", "grove"],
    "КИП/автоматика": ["allen bradley", "allen-bradley", "rockwell", "pepperl", "det-tronics",
                       "auma", "аума", "hirschmann", "comatreleco", "asco", "saex"],
    "Насосы": ["grundfos", "грундфос", "pompetravaini", "bornemann", "weir", "gabbioneta",
               "ingersoll", "leroy-somer"],
    "ЛУКОЙЛ (прямо)": ["лукойл", "энергосети", "нижневолжскнефть", "lukoil"],
}

COLS = {
    "pn": ["артикул", "парт", "part", "p/n", "pn", "обозначение", "каталожн", "код", "номер детали",
           "item code", "ref", "reference"],
    "name": ["наименование", "номенклатура", "описание", "description", "item", "предмет", "наимен",
             "designation", "product"],
    "qty": ["кол-во", "количество", "кол.", "qty", "quantity", "q-ty", "шт"],
    "price": ["цена", "price", "unit price", "стоимость", "amount", "сумма", "total", "eur", "usd",
              "руб", "rmb", "cny"],
    "lead": ["срок", "lead", "delivery", "поставк", "готовност", "eta", "days", "weeks"],
}
PN_RE = re.compile(r"\b(?=[A-Z0-9]*[0-9])[A-Z0-9][A-Z0-9\-./]{4,24}\b")
NUM_RE = re.compile(r"\d[\d\s.,]*")
NOISE = re.compile(r"^(итого|всего|подпись|примечан|n\s*п/п|приложение|total|subtotal)", re.I)
DOCS = ("xlsx/docx", "pdf", "xls/doc")


def topics_of(text: str) -> list[str]:
    t = (text or "").lower()
    return [n for n, ws in TOPICS.items() if any(w in t for w in ws)]


def sniff(b: bytes) -> str:
    if not b:
        return "пусто"
    if b[:2] == b"PK":
        return "xlsx/docx"
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls/doc"
    if b[:3] == b"\xff\xd8\xff" or b[:4] == b"\x89PNG":
        return "изображение"
    if b[:4] == b"Rar!" or b[:2] == b"\x1f\x8b" or b[:2] == b"7z":
        return "архив"
    head = b[:500].lower()
    if b"<html" in head or b"<!doctype" in head:
        return "страница входа"
    return "прочее"


def rows_xlsx(b: bytes) -> list[list[str]]:
    import openpyxl
    out: list[list[str]] = []
    wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                out.append(cells)
            if len(out) > 12000:
                return out
    return out


def rows_xls(b: bytes) -> list[list[str]]:
    import xlrd
    out: list[list[str]] = []
    wb = xlrd.open_workbook(file_contents=b)
    for ws in wb.sheets():
        for i in range(ws.nrows):
            cells = [str(c.value).strip() for c in ws.row(i)]
            if any(cells):
                out.append(cells)
            if len(out) > 12000:
                return out
    return out


def text_docx(b: bytes) -> str:
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        if "word/document.xml" in z.namelist():
            raw = z.read("word/document.xml").decode("utf-8", "ignore")
            raw = raw.replace("</w:p>", "\n")
            return " ".join(re.findall(r"<w:t[^>]*>([^<]{1,400})</w:t>", raw))
    except Exception:
        return ""
    return ""


def text_pdf(b: bytes) -> str:
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(b))
        return "\n".join((p.extract_text() or "") for p in rd.pages[:80])
    except Exception:
        return ""


def text_image(b: bytes, lang: str) -> tuple[str, str]:
    """Скан или фото КП: текстового слоя нет, нужен распознаватель. Возвращает
    (текст, пометка). Текст ошибки отдаём целиком: по одному «RuntimeError» не
    отличить таймаут от непоставленного языкового пакета."""
    if len(b) > OCR_MAX_BYTES:
        return "", "пропущено: тяжелее лимита"
    try:
        import pytesseract
        from PIL import Image
        im = Image.open(io.BytesIO(b))
        if im.mode not in ("L", "RGB"):
            im = im.convert("RGB")
        side = max(im.size)
        if side < 1400:
            k = 1400 / side
            im = im.resize((int(im.width * k), int(im.height * k)))
        elif side > OCR_MAX_SIDE:
            k = OCR_MAX_SIDE / side
            im = im.resize((int(im.width * k), int(im.height * k)))
        return pytesseract.image_to_string(im, lang=lang, timeout=OCR_TIMEOUT), "ок"
    except Exception as e:
        return "", f"сбой: {type(e).__name__}: {str(e)[:80]}"


def num(s: str) -> float | None:
    m = NUM_RE.search(str(s or ""))
    if not m:
        return None
    t = m.group(0).replace(" ", "").replace(" ", "")
    if t.count(",") and t.count("."):
        t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except Exception:
        return None


def header_map(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows[:50]):
        low = [str(c).lower() for c in row]
        found: dict[str, int] = {}
        for key, words in COLS.items():
            for j, c in enumerate(low):
                if c and any(w in c for w in words):
                    found.setdefault(key, j)
                    break
        if len(found) >= 2 and ("name" in found or "pn" in found):
            return i, found
    return -1, {}


def items_from_rows(rows: list[list[str]]) -> list[dict]:
    hi, cols = header_map(rows)
    body = rows[hi + 1:] if hi >= 0 else rows
    out: list[dict] = []
    for row in body:
        joined = " ".join(str(c) for c in row).strip()
        if len(joined) < 5 or NOISE.match(joined):
            continue

        def get(key: str) -> str:
            j = cols.get(key, -1)
            return str(row[j]).strip() if 0 <= j < len(row) else ""

        pn = get("pn")
        name = get("name")
        if not pn:
            m = PN_RE.search(joined.upper())
            pn = m.group(0) if m else ""
        if not name:
            txt = [str(c) for c in row if not str(c).replace(".", "").replace(",", "").isdigit()]
            name = max(txt, key=len) if txt else ""
        rec = {"pn": pn[:60], "name": name[:200], "qty": num(get("qty")),
               "price": num(get("price")), "lead": get("lead")[:60], "row": joined[:300]}
        if not rec["pn"] and len(rec["name"]) < 5:
            continue
        out.append(rec)
        if len(out) >= 2500:
            break
    return out


def items_from_text(text: str) -> list[dict]:
    """Из PDF, docx и распознанных сканов таблица приходит строками. Цену берём как
    последнее денежное число строки — в КП цена почти всегда в конце."""
    money = re.compile(r"(?<![\w.-])\d{1,3}(?:[  ]?\d{3})*(?:[.,]\d{1,2})?(?![\w-])")
    out: list[dict] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if len(ln) < 8 or NOISE.match(ln):
            continue
        m = PN_RE.search(ln.upper())
        if not m:
            continue
        price = None
        nums = money.findall(ln)
        if len(nums) >= 2:
            price = num(nums[-1])
            if price is not None and price < 1:
                price = None
        out.append({"pn": m.group(0)[:60], "name": ln[:200], "qty": None, "price": price,
                    "lead": "", "row": ln[:300]})
        if len(out) >= 2500:
            break
    return out


def norm_pn(s: str) -> str:
    cyr = str.maketrans({"А": "A", "В": "B", "С": "C", "Е": "E", "К": "K", "М": "M",
                         "Н": "H", "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y"})
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper().translate(cyr))


def main() -> None:
    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pypdf", "xlrd==1.2.0",
                    "pytesseract", "Pillow"], check=False)
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False, timeout=240,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "tesseract-ocr",
                    "tesseract-ocr-rus"], check=False, timeout=240,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ocr_ok = subprocess.run(["tesseract", "--version"], check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    langs = ""
    try:
        langs = subprocess.run(["tesseract", "--list-langs"], check=False,
                               capture_output=True, text=True).stdout
    except Exception:
        pass
    OCR_LANG = "rus+eng" if "rus" in langs else "eng"
    for nm in ("pypdf", "pypdf._cmap", "pypdf.generic", "pypdf._reader"):
        logging.getLogger(nm).setLevel(logging.ERROR)

    bx = BitrixClient(wh)
    sess = requests.Session()

    print("=== Зонд v42: файлы по запросам поставщикам — выгрузка, разбор, сканы ===")
    print(f"период с {PERIOD_FROM} · распознаватель: {'есть' if ocr_ok else 'НЕТ'} · "
          f"язык: {OCR_LANG} · {OCR_WORKERS} потока по {OCR_TIMEOUT} с\n", flush=True)

    fl = bx.call("crm.item.fields", {"entityTypeId": SPA}) or {}
    fields = fl.get("fields") or {}
    ffields = [n for n, m in fields.items() if str(m.get("type")) == "file"]

    sel = ["id", "title", "stageId", "parentId2", "createdTime", "assignedById"] + ffields
    items = bx.list_items(SPA, filter={">=createdTime": PERIOD_FROM}, select=sel)
    stages = bx.spa_stages(SPA, 24)
    print(f"всего записей СП-166: {len(items)}", flush=True)

    recs: dict[str, dict] = {}
    for it in items:
        iid = str(it["id"])
        title = str(it.get("title") or "")
        tp = topics_of(title)
        deal = str(it.get("parentId2") or "")
        stage = stages.get(str(it.get("stageId")), str(it.get("stageId")))
        ours = bool(tp) or deal in OUR_DEALS
        recs[iid] = {"id": iid, "title": title[:160], "deal": deal, "stage": stage,
                     "created": str(it.get("createdTime"))[:10], "topics": tp,
                     "kp": stage in GOT_QUOTE, "ours": ours, "files": []}
        for f in ffields:
            v = it.get(f)
            if not v:
                continue
            for fo in (v if isinstance(v, list) else [v]):
                if isinstance(fo, dict) and fo.get("urlMachine"):
                    recs[iid]["files"].append({"src": "поле записи", "fo": fo})

    ours_ids = [k for k, v in recs.items() if v["ours"]]
    kp_ids = [k for k in ours_ids if recs[k]["kp"]]
    print(f"по нашей номенклатуре / сделкам: {len(ours_ids)} · из них с КП: {len(kp_ids)}",
          flush=True)

    def pull_acts(f: dict) -> list[dict]:
        out: list[dict] = []
        last = 0
        while True:
            try:
                res = bx.call("crm.activity.list", {
                    "filter": dict(f, **{">ID": last}),
                    "select": ["ID", "OWNER_ID", "PROVIDER_ID", "SUBJECT", "FILES"],
                    "order": {"ID": "ASC"}, "start": -1}) or []
            except Exception:
                return out
            if not res:
                return out
            out.extend(res)
            last = int(res[-1]["ID"])
            if len(res) < 50:
                return out

    def activities(owner_type: int, owner_ids: list[str]) -> list[dict]:
        batch: list[dict] = []
        for i in range(0, len(owner_ids), 50):
            if time.monotonic() > ACT_DEADLINE:
                break
            batch += pull_acts({"OWNER_TYPE_ID": owner_type, "OWNER_ID": owner_ids[i:i + 50]})
        if batch:
            return batch
        one: list[dict] = []
        for oid in owner_ids:
            if time.monotonic() > ACT_DEADLINE:
                break
            one += pull_acts({"OWNER_TYPE_ID": owner_type, "OWNER_ID": oid})
        return one

    order = kp_ids + [k for k in ours_ids if k not in set(kp_ids)]
    acts = activities(SPA, order)
    n_act = 0
    for a in acts:
        fs = a.get("FILES") or []
        if isinstance(fs, dict):
            fs = list(fs.values())
        oid = str(a.get("OWNER_ID"))
        for fo in fs:
            if isinstance(fo, dict) and fo.get("urlMachine") and oid in recs:
                recs[oid]["files"].append({"src": f"таймлайн запроса/{a.get('PROVIDER_ID')}",
                                           "fo": fo})
                n_act += 1
    print(f"дел/писем в таймлайне запросов: {len(acts)} · файлов из них: {n_act}", flush=True)

    deal_files: list[dict] = []
    dacts = activities(2, sorted(OUR_DEALS))
    for a in dacts:
        fs = a.get("FILES") or []
        if isinstance(fs, dict):
            fs = list(fs.values())
        for fo in fs:
            if isinstance(fo, dict) and fo.get("urlMachine"):
                deal_files.append({"deal": str(a.get("OWNER_ID")), "prov": str(a.get("PROVIDER_ID")),
                                   "subj": str(a.get("SUBJECT") or "")[:120], "fo": fo})
    print(f"дел/писем в таймлайне наших сделок: {len(dacts)} · файлов из них: {len(deal_files)}",
          flush=True)

    queue: list[dict] = []
    for k in ours_ids:
        for f in recs[k]["files"]:
            queue.append({"req": k, "deal": recs[k]["deal"], "kp": recs[k]["kp"],
                          "src": f["src"], "fo": f["fo"]})
    for f in deal_files:
        queue.append({"req": None, "deal": f["deal"], "kp": None,
                      "src": f"таймлайн сделки/{f['prov']}", "subj": f["subj"], "fo": f["fo"]})
    seen: set[str] = set()
    uniq: list[dict] = []
    for q in queue:
        fid = str(q["fo"].get("id"))
        if fid in seen:
            continue
        seen.add(fid)
        uniq.append(q)
    queue = uniq[:MAX_FILES]
    print(f"\nуникальных файлов в очереди: {len(queue)}\n", flush=True)

    stat: Counter = Counter()
    ocr_stat: Counter = Counter()
    parsed: list[dict] = []
    done = [0]

    # Фаза 1: скачать и разобрать всё, кроме картинок. Ожидание сети, процессор свободен.
    def work(q: dict) -> dict | None:
        if time.monotonic() > DEADLINE:
            return None
        u = str(q["fo"].get("urlMachine"))
        try:
            r = sess.get(u, timeout=90)
        except Exception as e:
            stat[f"сеть: {type(e).__name__}"] += 1
            return None
        if r.status_code != 200:
            stat[f"http {r.status_code}"] += 1
            return None
        b = r.content
        kind = sniff(b)
        stat[kind] += 1
        rec = {"req": q["req"], "deal": q["deal"], "kp": q["kp"], "src": q["src"],
               "subj": q.get("subj", ""), "fid": str(q["fo"].get("id")),
               "name": str(q["fo"].get("name") or "")[:160], "size": len(b), "kind": kind,
               "items": []}
        try:
            if kind == "xlsx/docx":
                nm = (rec["name"] or "").lower()
                if nm.endswith(".docx") or b"word/document.xml" in b[:4000]:
                    rec["items"] = items_from_text(text_docx(b))
                else:
                    rec["items"] = items_from_rows(rows_xlsx(b))
            elif kind == "xls/doc":
                try:
                    rec["items"] = items_from_rows(rows_xls(b))
                except Exception:
                    rec["items"] = []
            elif kind == "pdf":
                rec["items"] = items_from_text(text_pdf(b))
            elif kind == "изображение" and ocr_ok:
                rec["_img"] = b          # распознаем отдельной фазой
        except Exception as e:
            rec["parse_error"] = type(e).__name__
        done[0] += 1
        if done[0] % 100 == 0:
            print(f"  скачано и разобрано {done[0]} файлов…", flush=True)
        return rec

    with ThreadPoolExecutor(max_workers=12) as ex:
        for rec in ex.map(work, queue):
            if rec:
                parsed.append(rec)

    # Фаза 2: распознавание. tesseract занимает ядро целиком, поэтому всего два потока.
    todo = [p for p in parsed if p.get("_img")]
    print(f"\nраспознавание {len(todo)} картинок в {OCR_WORKERS} потока…", flush=True)
    ocr_done = [0]

    def ocr_one(rec: dict) -> None:
        if time.monotonic() > OCR_DEADLINE:
            ocr_stat["не дошло: бюджет"] += 1
            rec.pop("_img", None)
            return
        txt, mark = text_image(rec["_img"], OCR_LANG)
        ocr_stat[mark] += 1
        rec["ocr_chars"] = len(txt)
        rec["items"] = items_from_text(txt)
        rec.pop("_img", None)
        ocr_done[0] += 1
        if ocr_done[0] % 15 == 0:
            print(f"  распознано {ocr_done[0]}…", flush=True)

    with ThreadPoolExecutor(max_workers=OCR_WORKERS) as ex:
        list(ex.map(ocr_one, todo))

    print("\n--- ЧТО ПРИШЛО ---")
    for k, v in stat.most_common():
        print(f"  {v:>5}  {k}")
    real = [p for p in parsed if p["kind"] in DOCS]
    scans = [p for p in parsed if p["kind"] == "изображение"]
    scans_ok = [p for p in scans if p["items"]]
    print(f"\nдокументы: {len(real)} · с позициями: {len([p for p in real if p['items']])} · "
          f"позиций: {sum(len(p['items']) for p in real)}")
    print("сканы: результат распознавания")
    for k, v in ocr_stat.most_common():
        print(f"  {v:>5}  {k}")
    print(f"сканы с позициями: {len(scans_ok)} из {len(scans)} · позиций из сканов: "
          f"{sum(len(p['items']) for p in scans_ok)} · символов: "
          f"{sum(p.get('ocr_chars', 0) for p in scans)}")

    def pnset(doc: dict) -> set[str]:
        return {p for p in (norm_pn(i.get("pn")) for i in doc.get("items") or []) if len(p) >= 5}

    try:
        allp = real + scans_ok
        for d in allp:
            d["_pns"] = pnset(d)
            d["_ni"] = len(d.get("items") or [])
            d["_npriced"] = sum(1 for i in (d.get("items") or []) if i.get("price"))

        cand = sorted([d for d in real if d["req"] is None or str(d["deal"]) in OUR_DEALS],
                      key=lambda d: -len(d["_pns"]))
        rfq: set[str] = cand[0]["_pns"] if cand else set()

        def overlap(d: dict) -> float:
            return len(d["_pns"] & rfq) / len(d["_pns"]) if d["_pns"] else 0.0

        for d in allp:
            d["_copy"] = overlap(d) > 0.6 and d["_npriced"] < max(1, 0.05 * d["_ni"])

        def priced_set(docs: list[dict]) -> set[str]:
            s: set[str] = set()
            for d in docs:
                for i in d.get("items") or []:
                    p = norm_pn(i.get("pn"))
                    if len(p) >= 5 and i.get("price"):
                        s.add(p)
            return s

        doc_ans = [d for d in real if d["req"] and d["kp"] and not d["_copy"]]
        scan_ans = [d for d in scans_ok if d["req"] and d["kp"] and not d["_copy"]]
        doc_q = set().union(*[d["_pns"] for d in doc_ans]) if doc_ans else set()
        scan_q = set().union(*[d["_pns"] for d in scan_ans]) if scan_ans else set()
        doc_p = priced_set(doc_ans)
        scan_p = priced_set(scan_ans)

        n = max(1, len(rfq))
        print("\n--- ПОКРЫТИЕ СТРОК ЗАКАЗЧИКА ---")
        print(f"  артикулов в эталонной спецификации        : {len(rfq)}")
        print(f"  ответов поставщиков: документов {len(doc_ans)} · сканов {len(scan_ans)}")
        print(f"  встречается в документах                  : {len(rfq & doc_q)}"
              f"  = {100 * len(rfq & doc_q) / n:.1f}%")
        print(f"  имеет цену в документах                   : {len(rfq & doc_p)}"
              f"  = {100 * len(rfq & doc_p) / n:.1f}%")
        print(f"  ДОБАВИЛИ СКАНЫ: встречается                : {len((rfq & scan_q) - doc_q)}")
        print(f"  ДОБАВИЛИ СКАНЫ: с ценой                   : {len((rfq & scan_p) - doc_p)}")
        print(f"  ИТОГО с ценой (документы + сканы)          : {len(rfq & (doc_p | scan_p))}"
              f"  = {100 * len(rfq & (doc_p | scan_p)) / n:.1f}%")

        if scans_ok:
            print("\n--- САМЫЕ СОДЕРЖАТЕЛЬНЫЕ СКАНЫ ---")
            for d in sorted(scans_ok, key=lambda x: -x["_ni"])[:14]:
                r = recs.get(str(d["req"])) if d["req"] else None
                title = (r or {}).get("title") or "(вложение сделки)"
                stage = (r or {}).get("stage", "—")
                print(f"  позиций {d['_ni']:>4} · с ценой {d['_npriced']:>4} · "
                      f"символов {d.get('ocr_chars', 0):>6} · {d['size']:>8} б · "
                      f"из эталона {len(d['_pns'] & rfq):>3} · {stage[:16]:16} · {title[:46]}")
    except Exception as e:
        print(f"\nсопоставление не выполнено: {type(e).__name__}: {e}")

    if not os.getenv("PROBE_SEAL"):
        print("\n(построчный разбор не выгружался: PROBE_SEAL не задан)")
        print("\n✓ зонд v42 завершён")
        return

    for d in parsed:
        for k in ("_pns", "_ni", "_npriced", "_copy", "_img"):
            d.pop(k, None)
    payload = {
        "period_from": PERIOD_FROM,
        "requests": [dict(recs[k], files=len(recs[k]["files"])) for k in ours_ids],
        "docs": parsed,
        "stat": dict(stat),
        "ocr": dict(ocr_stat),
    }
    raw = gzip.compress(json.dumps(payload, ensure_ascii=False).encode(), 9)
    key, iv = secrets.token_bytes(32), secrets.token_bytes(16)
    enc = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-K", key.hex(), "-iv", iv.hex()],
                         input=raw, capture_output=True, check=True).stdout
    sealed = subprocess.run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(PUBKEY),
                             "-pkeyopt", "rsa_padding_mode:oaep",
                             "-pkeyopt", "rsa_oaep_md:sha256"],
                            input=key + iv, capture_output=True, check=True).stdout
    b64 = base64.b64encode(enc).decode()
    print(f"\nзашифрованный разбор: {len(raw)} б сжато → {len(enc)} б шифра")
    print("-----KVANT SEALED KEY-----")
    print(base64.b64encode(sealed).decode())
    print("-----KVANT SEALED DATA-----")
    for i in range(0, len(b64), 4000):
        print(b64[i:i + 4000])
    print("-----KVANT END-----")
    print("\n✓ зонд v42 завершён")


if __name__ == "__main__":
    main()
