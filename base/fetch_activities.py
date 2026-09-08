#!/usr/bin/env python3
"""Выгрузка ВСЕЙ переписки и активностей сделок в отдельную базу.

Письма, звонки и задачи карточки — единственное место, где написано, ПОЧЕМУ
сделка не пошла: что ответил поставщик, что запросил заказчик, где встали.
Тело письма приходит прямо в crm.activity.list (поле DESCRIPTION), скачивать
ничего не нужно — это самый дешёвый источник смысла в портале.

Берём активности ПО СПИСКУ СДЕЛОК, а не по дате: у сделки, заведённой год
назад, переписка идёт до сих пор, и фильтр по дате её обрезал бы.

Пишем в ОТДЕЛЬНЫЙ файл базы (по умолчанию kvant_acts.db), а не в общий:
качалка вложений держит долгие транзакции в основной базе, и параллельная
запись туда падала на «database is locked» даже с busy_timeout. Сведение —
командой merge, когда обе выгрузки закончены.

    python base/fetch_activities.py --db base/kvant_acts.db
    python base/fetch_activities.py --merge-into base/kvant.db --db base/kvant_acts.db
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

TAGS = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>|<[^>]+>")
WS = re.compile(r"[ \t\xa0]+")
NL = re.compile(r"\n{3,}")
ENT = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&mdash;": "—"}
TYPES = {"1": "звонок", "2": "встреча", "3": "задача", "4": "письмо", "6": "уведомление"}
MAX_BODY = 20_000       # длиннее — это уже цитирование всей ветки, смысла не добавляет
CHUNK = 50              # сделок в одном фильтре crm.activity.list

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY, deal_id INTEGER, type TEXT, direction TEXT,
  subject TEXT, created TEXT, body TEXT, n_files INTEGER
);
CREATE INDEX IF NOT EXISTS ix_act_deal ON activities(deal_id);
CREATE TABLE IF NOT EXISTS act_done (deal_id INTEGER PRIMARY KEY);
"""


def strip_html(s: str) -> str:
    s = TAGS.sub(" ", s or "")
    for a, b in ENT.items():
        s = s.replace(a, b)
    return NL.sub("\n\n", WS.sub(" ", s)).strip()


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


def bx(sess: requests.Session, base: str, method: str, params: dict) -> dict:
    for i in range(5):
        try:
            d = sess.post(base + method + ".json", json=params, timeout=120).json()
        except Exception:
            time.sleep(1.5 * (i + 1))
            continue
        if isinstance(d, dict) and d.get("error"):
            if d["error"] in ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT", "INTERNAL_SERVER_ERROR"):
                time.sleep(1.5 * (i + 1))
                continue
            print("ошибка:", d["error"], str(d.get("error_description"))[:120], file=sys.stderr)
            return {}
        time.sleep(0.3)
        return d
    return {}


def deal_ids(src_db: str) -> list[int]:
    con = sqlite3.connect(src_db)
    con.execute("PRAGMA busy_timeout=120000")
    ids = [r[0] for r in con.execute("SELECT id FROM deals ORDER BY id")]
    con.close()
    return ids


def run(db_path: str, src_db: str) -> None:
    ids = deal_ids(src_db)
    con = sqlite3.connect(db_path, timeout=120)
    con.executescript(DDL)
    done = {r[0] for r in con.execute("SELECT deal_id FROM act_done")}
    todo = [i for i in ids if i not in done]
    print(f"сделок всего {len(ids)}, переписка уже собрана у {len(done)}, к выгрузке {len(todo)}", flush=True)

    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    kept = seen = 0
    t0 = time.time()
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        start = 0
        while True:
            d = bx(sess, base, "crm.activity.list", {
                "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": part},
                "select": ["ID", "OWNER_ID", "TYPE_ID", "DIRECTION", "SUBJECT", "DESCRIPTION",
                           "CREATED", "FILES"],
                "order": {"ID": "ASC"}, "start": start})
            res = d.get("result") or []
            if not res:
                break
            batch = []
            for a in res:
                seen += 1
                body = strip_html(a.get("DESCRIPTION") or "")[:MAX_BODY]
                subj = strip_html(a.get("SUBJECT") or "")
                if not body and not subj:
                    continue
                files = a.get("FILES") or []
                batch.append((str(a["ID"]), int(a.get("OWNER_ID") or 0),
                              TYPES.get(str(a.get("TYPE_ID")), str(a.get("TYPE_ID"))),
                              str(a.get("DIRECTION") or ""), subj, str(a.get("CREATED"))[:19], body,
                              len(files) if isinstance(files, list) else 0))
            if batch:
                con.executemany("INSERT OR REPLACE INTO activities VALUES (?,?,?,?,?,?,?,?)", batch)
                kept += len(batch)
            if not d.get("next"):
                break
            start = d["next"]
        con.executemany("INSERT OR REPLACE INTO act_done VALUES (?)", [(x,) for x in part])
        con.commit()
        print(f"  сделок {min(i + CHUNK, len(todo))}/{len(todo)} · записей {kept} · "
              f"{kept / max(time.time() - t0, 1):.0f} шт/с", flush=True)
    print(f"готово: просмотрено {seen}, записано {kept}, за {(time.time() - t0) / 60:.1f} мин", flush=True)
    con.close()


def merge(src: str, dst: str) -> None:
    """Переносит собранную переписку в основную базу и в полнотекстовый индекс."""
    con = sqlite3.connect(dst, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.execute("ATTACH DATABASE ? AS acts", (src,))
    n = con.execute("SELECT count(*) FROM acts.activities").fetchone()[0]
    con.execute("INSERT OR REPLACE INTO activities SELECT * FROM acts.activities")
    con.execute("DELETE FROM search WHERE kind='act'")
    con.execute("INSERT INTO search (body, kind, ref, deal_id) "
                "SELECT subject || char(10) || body, 'act', id, deal_id FROM acts.activities")
    con.commit()
    con.execute("DETACH DATABASE acts")
    con.close()
    print(f"перенесено записей переписки: {n}")


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--db", default=str(here / "kvant_acts.db"))
    ap.add_argument("--source-db", default=str(here / "kvant.db"),
                    help="база со списком сделок (и цель для --merge-into)")
    ap.add_argument("--merge-into", default=None, help="перенести собранное в эту базу и выйти")
    a = ap.parse_args()
    if a.merge_into:
        merge(a.db, a.merge_into)
        return 0
    run(a.db, a.source_db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
