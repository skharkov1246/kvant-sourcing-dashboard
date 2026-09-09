#!/usr/bin/env python3
"""Дочитывание вложений, которые в первый проход не дали текста.

Из 25 ГБ вложений корпуса 18,4 ГБ ушли в «пусто». Разбор по видам:
  * ole   — старые .doc и .xls, 1 450 файлов: парсера тогда не было, теперь есть
            (xlrd и antiword);
  * scan  — PDF без текстового слоя, 1 449 файлов на 3,7 ГБ: нужен OCR;
  * image — фотографии и сканы страниц, 3 715 файлов: тоже OCR.
Архивы разбирает отдельный модуль parse_archives.py.

Файлы скачиваются заново: бинарники после первого прохода не хранятся, а
ссылки urlMachine одноразовые, поэтому собираются свежие.

    python base/reparse.py --kind ole   --db base/kvant.db --limit 200
    python base/reparse.py --kind scan  --db base/kvant.db --limit 100
"""
from __future__ import annotations

import argparse
import hashlib
import io
import queue
import sqlite3
import threading
import time
import warnings
from pathlib import Path

import requests

from fetch_files import CHUNK, extract, filename_from
from parse_archives import fresh_links, webhook

warnings.filterwarnings("ignore")

WHERE = {
    "ole": "status='empty' AND (lower(filename) LIKE '%.doc' OR lower(filename) LIKE '%.xls')",
    "scan": "status='empty' AND lower(filename) LIKE '%.pdf'",
    "image": "status='empty' AND (lower(filename) LIKE '%.jpg' OR lower(filename) LIKE '%.jpeg'"
             " OR lower(filename) LIKE '%.png' OR lower(filename) LIKE '%.tif'"
             " OR lower(filename) LIKE '%.tiff')",
}
# Поля, где лежит содержательный документ. Скриншоты сложности тендерной
# площадки (2 386 картинок) и вложения бота распознавать смысла нет.
VALUE_FIELDS = ("Техническая спецификация", "Offer from us", "Offer from supplier(s)",
                "Offer from supplier (Техническое поле.Заполняется автоматически)",
                "Customer request for automatic processing", "Technical data from customer",
                "Processed file for supplier")
OCR_LANG = "rus+eng"
OCR_MAX_PAGES = 12          # сканы спецификаций редко длиннее; дальше растёт только время
MIN_OCR_CHARS = 40          # меньше — это шум распознавания, а не текст


def ocr_image(blob: bytes) -> str:
    import pytesseract
    from PIL import Image
    img = Image.open(io.BytesIO(blob))
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    if max(img.size) < 900:                       # мелкие картинки распознаются плохо
        img = img.resize((img.width * 2, img.height * 2))
    return pytesseract.image_to_string(img, lang=OCR_LANG)


def ocr_pdf(blob: bytes) -> tuple[str, int]:
    """Растеризуем страницы и распознаём. pdf2image требует poppler, поэтому при
    его отсутствии пробуем вытащить встроенные изображения через pypdf."""
    try:
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(blob, dpi=200, first_page=1, last_page=OCR_MAX_PAGES)
    except Exception:
        return "", 0
    import pytesseract
    out = []
    for p in pages:
        try:
            out.append(pytesseract.image_to_string(p, lang=OCR_LANG))
        except Exception:
            continue
    return "\n".join(out), len(pages)


def parse_one(kind: str, blob: bytes, name: str) -> tuple[str, int, str]:
    if kind == "ole":
        return extract(blob, name)
    if kind == "image":
        t = ocr_image(blob)
        return (t.strip(), 0, "ocr-image") if len(t.strip()) >= MIN_OCR_CHARS else ("", 0, "image")
    t, n = ocr_pdf(blob)
    return (t.strip(), n, "ocr-pdf") if len(t.strip()) >= MIN_OCR_CHARS else ("", n, "pdf")


def run(db_path: str, kind: str, limit: int | None, workers: int,
        only_value_fields: bool = True) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    cond, params = WHERE[kind], []
    if only_value_fields:
        cond += " AND field_name IN (" + ",".join("?" * len(VALUE_FIELDS)) + ")"
        params = list(VALUE_FIELDS)
    rows = con.execute(f"""SELECT fid, deal_id, field, field_name, filename
                           FROM files WHERE {cond} AND fid NOT LIKE '%#%'
                           ORDER BY bytes DESC""", params).fetchall()
    if limit:
        rows = rows[:limit]
    print(f"файлов вида «{kind}» к дочитыванию: {len(rows)}", flush=True)
    if not rows:
        return {}
    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    print("собираю свежие ссылки…", flush=True)
    links = fresh_links(sess, base, sorted({int(r[1]) for r in rows}))
    print(f"ссылок получено: {len(links)}", flush=True)

    inq: queue.Queue = queue.Queue()
    outq: queue.Queue = queue.Queue()
    for r in rows:
        inq.put(r)

    def worker() -> None:
        s = requests.Session()
        while True:
            try:
                fid, deal, _field, _fname, filename = inq.get(timeout=2)
            except queue.Empty:
                return
            url = links.get(str(fid))
            if not url:
                outq.put((fid, None, "нет ссылки"))
                inq.task_done()
                continue
            try:
                rr = s.get(url, timeout=300)
                if rr.status_code != 200 or not rr.content:
                    outq.put((fid, None, f"http {rr.status_code}"))
                    continue
                name = filename or filename_from(rr, fid)
                text, pages, ext = parse_one(kind, rr.content, name)
                outq.put((fid, (text, pages, ext, len(rr.content),
                                hashlib.sha1(rr.content).hexdigest()), None))
            except Exception as e:
                outq.put((fid, None, type(e).__name__))
            finally:
                inq.task_done()

    for _ in range(workers):
        threading.Thread(target=worker, daemon=True).start()

    n = ok = still = err = 0
    chars = 0
    t0 = time.time()
    pending = len(rows)
    while pending > 0:
        try:
            fid, payload, errmsg = outq.get(timeout=1200)
        except queue.Empty:
            break
        pending -= 1
        n += 1
        if errmsg or not payload:
            err += 1
            continue
        text, pages, ext, size, sha = payload
        if not text:
            still += 1
            con.execute("UPDATE files SET ext=?, pages=?, sha1=? WHERE fid=?", (ext, pages, sha, fid))
            continue
        ok += 1
        chars += len(text)
        con.execute("UPDATE files SET status='parsed', ext=?, pages=?, chars=?, bytes=?, sha1=? WHERE fid=?",
                    (ext, pages, len(text), size, sha, fid))
        con.execute("DELETE FROM file_text WHERE fid=?", (fid,))
        for i in range(0, len(text), CHUNK):
            con.execute("INSERT INTO file_text VALUES (?,?,?)", (fid, i // CHUNK, text[i:i + CHUNK]))
        deal = con.execute("SELECT deal_id FROM files WHERE fid=?", (fid,)).fetchone()
        con.execute("INSERT INTO search (body, kind, ref, deal_id) VALUES (?,'file',?,?)",
                    (text[:CHUNK * 3], fid, deal[0] if deal else None))
        if n % 25 == 0:
            con.commit()
            sp = n / max(time.time() - t0, 1)
            print(f"  {n}/{len(rows)} · прочитано {ok} · пусто {still} · ошибок {err} · "
                  f"{chars/1e6:.1f} млн знаков · осталось ~{pending/max(sp,0.01)/60:.0f} мин", flush=True)
    con.commit()
    con.close()
    print(f"готово: обработано {n}, прочитано {ok}, осталось пустыми {still}, ошибок {err}, "
          f"{chars/1e6:.1f} млн знаков за {(time.time()-t0)/60:.1f} мин", flush=True)
    return {"n": n, "ok": ok, "chars": chars}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=sorted(WHERE))
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--all-fields", action="store_true",
                    help="не ограничиваться полями с содержательными документами")
    a = ap.parse_args()
    run(a.db, a.kind, a.limit, a.workers, not a.all_fields)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
