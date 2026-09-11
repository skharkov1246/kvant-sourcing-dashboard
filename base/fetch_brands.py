#!/usr/bin/env python3
"""Справочник марок из смарт-процесса 176 «Brands» портала.

Зачем отдельно. Производитель у позиции спецификации опознаётся по словарю, и
раньше словарь собирался из поля «Brands» у сделок. После пересборки снимка это
поле оказалось пустым — его заполнял отдельный проход, которого в снимке нет, и
производитель не проставился ни у одной из 628 тысяч позиций. Справочник самого
портала надёжнее: он не зависит от того, чем наполнены карточки, и содержит
2 449 марок против шестисот, которые набирались из сделок.

    python base/fetch_brands.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

import requests

from parse_archives import webhook

DDL = """
CREATE TABLE IF NOT EXISTS brands (
  id INTEGER PRIMARY KEY, title TEXT, company_id INTEGER, created TEXT
);
"""


def run(db_path: str) -> int:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.executescript(DDL)
    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    rows, last = [], 0
    while True:
        r = None
        for attempt in range(6):
            try:
                r = sess.post(base + "crm.item.list.json", json={
                    "entityTypeId": 176, "select": ["id", "title", "companyId", "createdTime"],
                    "filter": {">id": last}, "order": {"id": "asc"}, "start": -1}, timeout=180).json()
            except Exception:
                time.sleep(2 * (attempt + 1))
                continue
            if "operation time limit" in (r.get("error_description") or ""):
                time.sleep(min(30 * (attempt + 1), 180))
                continue
            break
        items = ((r or {}).get("result") or {}).get("items") or []
        if not items:
            break
        for it in items:
            rows.append((int(it["id"]), (it.get("title") or "").strip(),
                         int(it.get("companyId") or 0), it.get("createdTime")))
        last = int(items[-1]["id"])
        time.sleep(0.25)
    con.execute("DELETE FROM brands")
    con.executemany("INSERT OR REPLACE INTO brands VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    print(f"марок в справочнике: {len(rows)}", flush=True)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    a = ap.parse_args()
    run(a.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
