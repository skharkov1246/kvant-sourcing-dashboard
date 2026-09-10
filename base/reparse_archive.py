#!/usr/bin/env python3
"""OCR сканов, лежащих ВНУТРИ архивов.

Зачем отдельный модуль. Обычное дочитывание (reparse.py) качает файл по его
собственной ссылке urlMachine и потому работает только с самостоятельными
вложениями — вложенные документы оно пропускает по условию `fid NOT LIKE '%#%'`.
А внутри архивов осталось почти пять тысяч PDF без текстового слоя: тендерные
пакеты заказчиков приходят одним архивом, и сканы спецификаций лежат там же.
Скачать такой документ поштучно нельзя — приходится качать архив целиком.

Как работает: берёт архивы, у которых есть пустые вложенные документы, качает
архив по свежей ссылке, распаковывает в память и распознаёт нужные документы.
Соответствие «строка базы ↔ файл в архиве» — по порядковому номеру из
идентификатора, см. member_index().

    python base/reparse_archive.py --db base/kvant.db --workers 3
"""
from __future__ import annotations

import argparse
import hashlib
import queue
import sqlite3
import threading
import time
import warnings
from collections import defaultdict
from pathlib import Path

import requests

from fetch_files import CHUNK
from parse_archives import fresh_links, members, webhook
from reparse import ocr_image, ocr_pdf

warnings.filterwarnings("ignore")

MIN_OCR_CHARS = 40
# Один и тот же документ лежит в архиве по нескольку раз: тендерный пакет
# раскладывают по папкам «Позиция 1»…«Позиция 8», и в каждой полный комплект.
# Распознавать копии заново — впустую часы OCR, поэтому текст берётся из кеша
# по хешу содержимого, а хеш считается ДО распознавания.
_CACHE: dict[str, tuple] = {}
_CACHE_LOCK = threading.Lock()
SCAN_EXT = (".pdf",)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


def member_index(fid: str) -> int:
    """Порядковый номер документа внутри архива из идентификатора «<архив>#<номер>».

    Сопоставлять по имени нельзя: у части архивов имена внутри записаны в базу с
    битой кодировкой («ä«¬π¼Ñ¡Γáµ¿∩» вместо «Документация процедуры»), а свежая
    распаковка декодирует их правильно. Номер же и есть то, чем строка была
    заведена: parse_archives нумерует документы позицией в списке распаковки, а
    список для одного и того же архива воспроизводится."""
    return int(fid.rsplit("#", 1)[1])


def run(db_path: str, limit: int | None, workers: int, kind: str) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    exts = SCAN_EXT if kind == "scan" else IMAGE_EXT
    cond = " OR ".join("lower(filename) LIKE ?" for _ in exts)
    rows = con.execute(f"""SELECT fid, deal_id, filename FROM files
                           WHERE status='empty' AND fid LIKE '%#%' AND ({cond})""",
                       [f"%{e}" for e in exts]).fetchall()
    by_arch: dict[str, list] = defaultdict(list)
    for fid, deal, filename in rows:
        by_arch[fid.split("#", 1)[0]].append((fid, deal, filename))
    archives = list(by_arch.items())
    if limit:
        archives = archives[:limit]
    print(f"архивов с нераспознанными документами: {len(archives)} "
          f"(документов {sum(len(v) for _, v in archives)})", flush=True)
    if not archives:
        return {}

    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    deal_ids = sorted({int(v[0][1]) for _, v in archives if v[0][1]})
    print("собираю свежие ссылки…", flush=True)
    links = fresh_links(sess, base, deal_ids)
    print(f"ссылок получено: {len(links)}", flush=True)

    inq: queue.Queue = queue.Queue()
    outq: queue.Queue = queue.Queue()
    for item in archives:
        inq.put(item)

    def worker() -> None:
        s = requests.Session()
        while True:
            try:
                arch_fid, wanted = inq.get(timeout=2)
            except queue.Empty:
                return
            url = links.get(str(arch_fid))
            if not url:
                outq.put((arch_fid, [], "нет ссылки"))
                inq.task_done()
                continue
            try:
                r = s.get(url, timeout=300)
                if r.status_code != 200 or not r.content:
                    outq.put((arch_fid, [], f"http {r.status_code}"))
                    continue
                arch_name = wanted[0][2].split(" :: ", 1)[0]
                inner = members(r.content, arch_name)
                done = []
                for fid, _deal, _filename in wanted:
                    idx = member_index(fid)
                    if idx >= len(inner):
                        continue
                    blob = inner[idx][1]
                    sha = hashlib.sha1(blob).hexdigest()
                    with _CACHE_LOCK:
                        hit = _CACHE.get(sha)
                    if hit is not None:
                        text, pages, ext = hit
                    else:
                        if kind == "scan":
                            text, pages = ocr_pdf(blob)
                            ext = "ocr-pdf"
                        else:
                            text, pages = ocr_image(blob), 0
                            ext = "ocr-image"
                        text = (text or "").strip()
                        with _CACHE_LOCK:
                            _CACHE[sha] = (text, pages, ext)
                    if len(text) >= MIN_OCR_CHARS:
                        done.append((fid, text, pages, ext, len(blob), sha))
                outq.put((arch_fid, done, None))
            except Exception as e:
                outq.put((arch_fid, [], type(e).__name__))
            finally:
                inq.task_done()

    for _ in range(workers):
        threading.Thread(target=worker, daemon=True).start()

    n = ok = chars = err = 0
    t0 = time.time()
    pending = len(archives)
    while pending > 0:
        try:
            arch_fid, done, errmsg = outq.get(timeout=1800)
        except queue.Empty:
            break
        pending -= 1
        n += 1
        if errmsg:
            err += 1
        for fid, text, pages, ext, size, sha in done:
            ok += 1
            chars += len(text)
            con.execute("""UPDATE files SET status='parsed', ext=?, pages=?, chars=?, bytes=?, sha1=?
                           WHERE fid=?""", (ext, pages, len(text), size, sha, fid))
            con.execute("DELETE FROM file_text WHERE fid=?", (fid,))
            for i in range(0, len(text), CHUNK):
                con.execute("INSERT INTO file_text VALUES (?,?,?)", (fid, i // CHUNK, text[i:i + CHUNK]))
            deal = con.execute("SELECT deal_id FROM files WHERE fid=?", (fid,)).fetchone()
            con.execute("INSERT INTO search (body, kind, ref, deal_id) VALUES (?,'file',?,?)",
                        (text[:CHUNK * 3], fid, deal[0] if deal else None))
        con.commit()
        if n % 20 == 0:
            sp = n / max(time.time() - t0, 1)
            print(f"  {n}/{len(archives)} архивов · распознано {ok} · {chars/1e6:.1f} млн знаков · "
                  f"ошибок {err} · осталось ~{(len(archives)-n)/max(sp,0.01)/60:.0f} мин", flush=True)
    con.commit()
    con.close()
    print(f"готово: архивов {n}, распознано документов {ok}, {chars/1e6:.1f} млн знаков, "
          f"ошибок {err}, за {(time.time()-t0)/60:.1f} мин", flush=True)
    return {"archives": n, "docs": ok, "chars": chars}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--kind", default="scan", choices=("scan", "image"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    run(a.db, a.limit, a.workers, a.kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
