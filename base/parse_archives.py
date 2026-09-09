#!/usr/bin/env python3
"""Разбор вложений-архивов: две трети содержимого корпуса лежат внутри них.

Замер по базе: из 25 ГБ вложений 18,4 ГБ не дали ни одного знака текста, и
самый крупный кусок — архивы: 901 zip на 11,1 ГБ, 149 rar на 0,9 ГБ и 63
семёрки на 0,7 ГБ. Внутри лежат те же спецификации и оферты, просто упакованные.

Что делает: берёт вложения, помеченные как «пусто», скачивает заново по свежей
ссылке urlMachine, распаковывает в память и разбирает каждый вложенный документ
теми же извлекателями, что и обычные файлы. Каждый вложенный документ попадает
в базу отдельной строкой files с идентификатором «<fid>#<номер>», поэтому
номенклатура из архивов ложится в общий индекс без особых случаев.

Ссылки одноразовые и живут недолго, поэтому собираются заново для тех сделок,
чьи архивы разбираются.

    python base/parse_archives.py --db base/kvant.db --workers 6
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import queue
import re
import sqlite3
import tempfile
import threading
import time
import warnings
import zipfile
from pathlib import Path

import requests

from fetch_files import CHUNK, extract, filename_from

warnings.filterwarnings("ignore")          # pypdf шумит на каждый нестандартный шрифт

def safe(s: str) -> str:
    """Строка, которую примет SQLite: имена внутри архивов и текст из битых
    кодировок содержат одиночные суррогаты, и на них падает вся запись."""
    return (s or "").encode("utf-8", "ignore").decode("utf-8", "ignore")


MEMBER_EXT = {"xlsx", "xls", "docx", "doc", "pdf", "csv", "txt", "xlsm", "rtf"}
MAX_MEMBERS = 60          # больше в одном архиве — это фотоотчёт, а не спецификация
MAX_MEMBER_BYTES = 80_000_000
ARCH_RE = re.compile(r"\.(zip|rar|7z)$", re.I)


def webhook() -> str:
    hook = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if hook:
        return hook
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("BITRIX_WEBHOOK_URL="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("BITRIX_WEBHOOK_URL не задан")


def fresh_links(sess: requests.Session, base: str, deal_ids: list[int]) -> dict[str, str]:
    """Свежие ссылки urlMachine по сделкам: старые протухают."""
    d = sess.post(base + "crm.item.fields.json", json={"entityTypeId": 2}, timeout=120).json()
    fields = ((d or {}).get("result") or {}).get("fields") or {}
    ffields = [k for k, v in fields.items() if v.get("type") == "file"]
    out: dict[str, str] = {}
    for i in range(0, len(deal_ids), 50):
        part = deal_ids[i:i + 50]
        for attempt in range(4):
            try:
                r = sess.post(base + "crm.item.list.json", json={
                    "entityTypeId": 2, "filter": {"@id": part},
                    "select": ["id"] + ffields, "start": 0}, timeout=120).json()
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        else:
            continue
        for it in ((r or {}).get("result") or {}).get("items") or []:
            for f in ffields:
                v = it.get(f)
                if not v:
                    continue
                for o in (v if isinstance(v, list) else [v]):
                    if isinstance(o, dict) and o.get("urlMachine"):
                        out[str(o.get("id"))] = o["urlMachine"]
        time.sleep(0.3)
    return out


def members(blob: bytes, name: str) -> list[tuple[str, bytes]]:
    """Содержимое архива: [(имя файла, байты)] — только документы, без картинок."""
    low = name.lower()
    got: list[tuple[str, bytes]] = []
    if low.endswith(".zip") or blob[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(blob))
        except Exception:
            return []
        for info in z.infolist()[:400]:
            mname = info.filename
            if not info.flag_bits & 0x800:      # имя не в UTF-8 — это cp866 под видом cp437
                try:
                    mname = mname.encode("cp437").decode("cp866")
                except Exception:
                    pass
            ext = mname.rsplit(".", 1)[-1].lower() if "." in mname else ""
            if ext not in MEMBER_EXT or info.file_size > MAX_MEMBER_BYTES:
                continue
            try:
                got.append((mname, z.read(info)))
            except Exception:
                continue
            if len(got) >= MAX_MEMBERS:
                break
        return got
    # rar и 7z распаковываем внешними утилитами во временный каталог
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / ("a" + (".rar" if low.endswith(".rar") else ".7z"))
        src.write_bytes(blob)
        cmd = f'cd {td} && (unrar x -inul -o+ "{src}" . 2>/dev/null || 7z x -y -bso0 -bsp0 "{src}" -o. >/dev/null 2>&1)'
        os.system(cmd)
        for p in sorted(Path(td).rglob("*")):
            if not p.is_file() or p == src:
                continue
            ext = p.suffix.lower().lstrip(".")
            if ext not in MEMBER_EXT or p.stat().st_size > MAX_MEMBER_BYTES:
                continue
            try:
                got.append((p.name, p.read_bytes()))
            except Exception:
                continue
            if len(got) >= MAX_MEMBERS:
                break
    return got


def run(db_path: str, limit: int | None = None, workers: int = 6) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    rows = con.execute("""SELECT fid, deal_id, field, field_name, filename, bytes
                          FROM files
                          WHERE status='empty' AND (lower(filename) LIKE '%.zip'
                                OR lower(filename) LIKE '%.rar' OR lower(filename) LIKE '%.7z')
                          ORDER BY bytes DESC""").fetchall()
    done = {r[0] for r in con.execute("SELECT DISTINCT substr(fid,1,instr(fid,'#')-1) FROM files WHERE fid LIKE '%#%'")}
    rows = [r for r in rows if r[0] not in done]
    if limit:
        rows = rows[:limit]
    print(f"архивов к разбору: {len(rows)}", flush=True)
    if not rows:
        return {}

    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    deal_ids = sorted({int(r[1]) for r in rows})
    print("собираю свежие ссылки…", flush=True)
    links = fresh_links(sess, base, deal_ids)
    print(f"ссылок получено: {len(links)}", flush=True)

    n = ok = empty = err = 0
    total_chars = 0
    t0 = time.time()

    # скачивание и разбор идут в потоках, запись в базу — только в главном:
    # SQLite не любит нескольких писателей, а разбор PDF внутри архива долгий
    inq: queue.Queue = queue.Queue()
    outq: queue.Queue = queue.Queue()
    for r in rows:
        inq.put(r)

    def worker() -> None:
        s = requests.Session()
        while True:
            try:
                fid, deal, field, fname, filename, _b = inq.get(timeout=2)
            except queue.Empty:
                return
            url = links.get(str(fid))
            if not url:
                outq.put((fid, deal, field, fname, None, "нет ссылки"))
                inq.task_done()
                continue
            try:
                rr = s.get(url, timeout=300)
                if rr.status_code != 200 or not rr.content:
                    outq.put((fid, deal, field, fname, None, f"http {rr.status_code}"))
                    continue
                name = filename or filename_from(rr, fid)
                inner = members(rr.content, name)
                parsed = [(mn, *extract(mb, mn)) for mn, mb in inner]
                sizes = [len(mb) for _mn, mb in inner]
                outq.put((fid, deal, field, fname, (name, parsed, sizes), None))
            except Exception as e:
                outq.put((fid, deal, field, fname, None, type(e).__name__))
            finally:
                inq.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()

    pending = len(rows)
    while pending > 0:
        try:
            fid, deal, field, fname, payload, errmsg = outq.get(timeout=900)
        except queue.Empty:
            break
        pending -= 1
        n += 1
        if errmsg or not payload:
            err += 1
            continue
        name, parsed, sizes = payload
        if not parsed:
            empty += 1
            continue
        name = safe(name)
        got_text = 0
        for idx, (mname, text, pages, ext) in enumerate(parsed):
            mlen = sizes[idx] if idx < len(sizes) else 0
            mname, text = safe(mname), safe(text)
            sub = f"{fid}#{idx}"
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sub, int(deal), field, fname, f"{name} :: {mname}"[:250], ext, mlen,
                         hashlib.sha1(f"{fid}#{idx}#{mname}".encode()).hexdigest(), "", "",
                         "parsed" if text else "empty", "", pages, len(text)))
            if text:
                got_text += 1
                total_chars += len(text)
                for i in range(0, len(text), CHUNK):
                    con.execute("INSERT INTO file_text VALUES (?,?,?)", (sub, i // CHUNK, text[i:i + CHUNK]))
                con.execute("INSERT INTO search (body, kind, ref, deal_id) VALUES (?,'file',?,?)",
                            (f"{mname}\n{text[:CHUNK * 3]}", sub, int(deal)))
        if got_text:
            ok += 1
        try:
            con.commit()
        except Exception as e:                 # один битый документ не должен рушить прогон
            err += 1
            con.rollback()
            print(f"  ! запись не прошла ({type(e).__name__}), архив пропущен", flush=True)
        if n % 20 == 0:
            sp = n / max(time.time() - t0, 1)
            print(f"  {n}/{len(rows)} · с текстом {ok} · пусто {empty} · ошибок {err} · "
                  f"{total_chars/1e6:.1f} млн знаков · осталось ~{(len(rows)-n)/max(sp,0.01)/60:.0f} мин", flush=True)
    con.commit()
    con.close()
    print(f"готово: архивов {n}, с текстом {ok}, пустых {empty}, ошибок {err}, "
          f"извлечено {total_chars/1e6:.1f} млн знаков за {(time.time()-t0)/60:.1f} мин", flush=True)
    return {"n": n, "ok": ok, "chars": total_chars}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    run(a.db, a.limit, a.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
