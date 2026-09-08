#!/usr/bin/env python3
"""Выгрузка переписки и активностей сделок в base/kvant.db.

Письма, звонки и задачи карточки — единственное место, где написано, ПОЧЕМУ
сделка не пошла: что ответил поставщик, что запросил заказчик, где встали.
Тело письма приходит прямо в crm.activity.list (поле DESCRIPTION) — скачивать
ничего не нужно, поэтому это самый дешёвый источник смысла в портале.

    python base/fetch_activities.py --db base/kvant.db --since 2025-09-08
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


def strip_html(s: str) -> str:
    s = TAGS.sub(" ", s or "")
    for a, b in ENT.items():
        s = s.replace(a, b)
    return NL.sub("\n\n", WS.sub(" ", s)).strip()


def run(db_path: str, since: str, webhook: str) -> None:
    con = sqlite3.connect(db_path, timeout=120)
    # качалка вложений пишет в ту же базу параллельно: ждём её транзакцию, а не падаем
    con.execute("PRAGMA busy_timeout=120000")
    con.executescript((Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8"))
    deals = {r[0] for r in con.execute("SELECT id FROM deals")}
    have = {r[0] for r in con.execute("SELECT id FROM activities")}
    sess = requests.Session()
    base = webhook.rstrip("/") + "/"

    start, n, kept, t0 = 0, 0, 0, time.time()
    while True:
        for attempt in range(5):
            try:
                d = sess.post(base + "crm.activity.list.json", json={
                    "filter": {"OWNER_TYPE_ID": 2, ">=CREATED": f"{since}T00:00:00"},
                    "select": ["ID", "OWNER_ID", "TYPE_ID", "DIRECTION", "SUBJECT", "DESCRIPTION",
                               "CREATED", "FILES"],
                    "order": {"ID": "ASC"}, "start": start}, timeout=120).json()
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        else:
            print("портал недоступен, останавливаюсь", file=sys.stderr)
            break
        if d.get("error"):
            if d["error"] in ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"):
                time.sleep(1.5)
                continue
            print("ошибка:", d["error"], file=sys.stderr)
            break
        res = d.get("result") or []
        if not res:
            break
        batch = []
        for a in res:
            n += 1
            did = int(a.get("OWNER_ID") or 0)
            if did not in deals or str(a["ID"]) in have:
                continue
            body = strip_html(a.get("DESCRIPTION") or "")[:MAX_BODY]
            subj = strip_html(a.get("SUBJECT") or "")
            if not body and not subj:
                continue
            files = a.get("FILES") or []
            batch.append((str(a["ID"]), did, TYPES.get(str(a.get("TYPE_ID")), str(a.get("TYPE_ID"))),
                          str(a.get("DIRECTION") or ""), subj, str(a.get("CREATED"))[:19], body,
                          len(files) if isinstance(files, list) else 0))
        if batch:
            con.executemany("INSERT OR REPLACE INTO activities VALUES (?,?,?,?,?,?,?,?)", batch)
            con.executemany("INSERT INTO search (body, kind, ref, deal_id) VALUES (?,'act',?,?)",
                            [(f"{b[4]}\n{b[6]}", b[0], b[1]) for b in batch])
            kept += len(batch)
            con.commit()
        if n % 2000 < 50:
            print(f"  просмотрено {n}, записано {kept}, {n/max(time.time()-t0,1):.0f} шт/с", flush=True)
        nxt = d.get("next")
        if not nxt:
            break
        start = nxt
        time.sleep(0.3)
    print(f"готово: просмотрено {n}, записано в базу {kept}, за {(time.time()-t0)/60:.1f} мин", flush=True)
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--since", default="2025-09-08")
    a = ap.parse_args()
    hook = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not hook:
        env = Path(__file__).resolve().parent.parent / ".env"
        for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
            if line.startswith("BITRIX_WEBHOOK_URL="):
                hook = line.split("=", 1)[1].strip()
    if not hook:
        raise SystemExit("BITRIX_WEBHOOK_URL не задан")
    run(a.db, a.since, hook)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
