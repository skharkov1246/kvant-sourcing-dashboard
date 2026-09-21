#!/usr/bin/env python3
"""Выгрузка ЧАТОВ сделок — там идёт живое обсуждение, а не в почте.

В этом портале переписка карточки почти вся в чате сделки: писем на 2 928 сделок
года всего 2 485, и половина из них — типовые шапки рассылки. Разбор «почему
проиграли» без чатов слепой.

Скорость: один запрос im.chat.get и один im.dialog.messages.get на сделку — это
почти шесть тысяч вызовов. Поэтому оба этапа идут пачками по 50 команд через
метод batch: 2 928 сделок укладываются в ~120 запросов вместо ~5 900.

    python base/fetch_chats.py --db base/kvant_chats.db --source-db base/kvant.db
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

TAGS = re.compile(r"(?is)<[^>]+>")
BB = re.compile(r"\[/?[A-Za-z][^\]]*\]")     # [B]…[/B], [URL=…], [DISK=…] и прочая разметка Битрикса
WS = re.compile(r"[ \t\xa0]+")
CHUNK = 50
MSG_LIMIT = 200          # сообщений на чат: длиннее ветки редки, а объём растёт быстро

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chats (deal_id INTEGER PRIMARY KEY, chat_id TEXT, n_msg INTEGER);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, deal_id INTEGER, chat_id TEXT, author TEXT, at TEXT, text TEXT
);
CREATE INDEX IF NOT EXISTS ix_msg_deal ON messages(deal_id);
CREATE TABLE IF NOT EXISTS chat_done (deal_id INTEGER PRIMARY KEY);
"""


def clean(s: str) -> str:
    s = BB.sub(" ", TAGS.sub(" ", s or ""))
    return WS.sub(" ", s).strip()


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


def batch(sess: requests.Session, base: str, cmd: dict[str, str]) -> dict:
    """До 50 команд одним запросом. Возвращает {ключ: результат}."""
    out: dict = {}
    keys = list(cmd)
    for i in range(0, len(keys), 50):
        part = {k: cmd[k] for k in keys[i:i + 50]}
        for attempt in range(5):
            try:
                d = sess.post(base + "batch.json", json={"halt": 0, "cmd": part}, timeout=180).json()
            except Exception:
                time.sleep(1.5 * (attempt + 1))
                continue
            if isinstance(d, dict) and d.get("error"):
                if d["error"] in ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                print("ошибка batch:", d.get("error"), file=sys.stderr)
                break
            out.update(((d or {}).get("result") or {}).get("result") or {})
            break
        time.sleep(0.35)
    return out


def run(db_path: str, src_db: str) -> None:
    src = sqlite3.connect(src_db, timeout=300)
    src.execute("PRAGMA busy_timeout=300000")
    ids = [r[0] for r in src.execute("SELECT id FROM deals ORDER BY id")]
    src.close()

    con = sqlite3.connect(db_path, timeout=180)
    con.executescript(DDL)
    done = {r[0] for r in con.execute("SELECT deal_id FROM chat_done")}
    todo = [i for i in ids if i not in done]
    print(f"сделок {len(ids)}, чаты уже собраны у {len(done)}, к выгрузке {len(todo)}", flush=True)

    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    t0 = time.time()
    n_chats = n_msgs = 0

    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        # 1. идентификатор чата сделки
        got = batch(sess, base, {f"c{d}": f"im.chat.get?ENTITY_TYPE=CRM&ENTITY_ID=DEAL|{d}" for d in part})
        chat_of: dict[int, str] = {}
        for k, v in got.items():
            cid = (v or {}).get("ID") if isinstance(v, dict) else None
            if cid:
                chat_of[int(k[1:])] = str(cid)
        # 2. сообщения чатов
        msgs = batch(sess, base, {f"m{d}": f"im.dialog.messages.get?DIALOG_ID=chat{c}&LIMIT={MSG_LIMIT}"
                                  for d, c in chat_of.items()})
        rows, crows = [], []
        for k, v in msgs.items():
            did = int(k[1:])
            items = (v or {}).get("messages") if isinstance(v, dict) else None
            items = items or []
            for m in items:
                txt = clean(str(m.get("text") or ""))
                if not txt:
                    continue
                rows.append((str(m.get("id")), did, chat_of.get(did), str(m.get("author_id") or ""),
                             str(m.get("date") or "")[:19], txt))
            crows.append((did, chat_of.get(did), len(items)))
            n_msgs += len(items)
        n_chats += len(crows)
        if rows:
            con.executemany("INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?)", rows)
        if crows:
            con.executemany("INSERT OR REPLACE INTO chats VALUES (?,?,?)", crows)
        con.executemany("INSERT OR REPLACE INTO chat_done VALUES (?)", [(x,) for x in part])
        con.commit()
        print(f"  сделок {min(i + CHUNK, len(todo))}/{len(todo)} · чатов {n_chats} · сообщений {n_msgs} · "
              f"{(i + CHUNK) / max(time.time() - t0, 1):.0f} сделок/с", flush=True)
    print(f"готово: чатов {n_chats}, сообщений {n_msgs}, за {(time.time() - t0) / 60:.1f} мин", flush=True)
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--db", default=str(here / "kvant_chats.db"))
    ap.add_argument("--source-db", default=str(here / "kvant.db"))
    a = ap.parse_args()
    run(a.db, a.source_db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
