#!/usr/bin/env python3
"""Загрузка годовой выгрузки Bitrix в единую базу base/kvant.db.

Вход  — JSON-снимок из base/export_bitrix.py (сделки, история стадий, справочники).
Выход — SQLite с полнотекстовым индексом: одна файловая БД, ноль инфраструктуры.

    python base/build_db.py <export.json> [--db base/kvant.db]

Повторный запуск идемпотентен: таблицы ядра перезаписываются, вложения и
извлечённый текст (их пишут download_files.py и parse_files.py) — нет.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SCHEMA = BASE_DIR / "schema.sql"


def _date(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def connect(db_path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def load(export_path: str | Path, db_path: str | Path, *, as_of: dt.date | None = None) -> dict:
    snap = json.loads(Path(export_path).read_text(encoding="utf-8"))
    as_of = as_of or dt.date.today()
    con = connect(db_path)
    cur = con.cursor()

    cats: dict = snap.get("cats") or {}
    stages: dict = snap.get("stages") or {}
    users: dict = snap.get("users") or {}
    comps: dict = snap.get("companies") or {}
    rates: dict = snap.get("currency") or {}
    hist: dict = snap.get("history") or {}

    # --- воронка происхождения и факт перехода в реализацию (кат. 0).
    # Победа в тендере в этом портале = ПЕРЕЕЗД той же карточки в воронку реализации,
    # стадия C2:WON почти не используется. Поэтому исход считаем по истории стадий.
    origin: dict[str, str] = {}
    won_date: dict[str, str] = {}
    for did, rows in hist.items():
        rows = sorted(rows, key=lambda r: r[2])
        if rows:
            origin[did] = rows[0][0]
        for c, _st, t in rows:
            if str(c) == "0" and did not in won_date:
                won_date[did] = str(t)[:10]

    for tbl in ("deals", "stage_events", "companies", "users", "stages", "categories"):
        cur.execute(f"DELETE FROM {tbl}")

    cur.executemany("INSERT OR REPLACE INTO categories VALUES (?,?)", list(cats.items()))
    cur.executemany("INSERT OR REPLACE INTO users VALUES (?,?)", list(users.items()))
    cur.executemany("INSERT OR REPLACE INTO companies VALUES (?,?,?)",
                    [(k, v.get("title"), v.get("industry")) for k, v in comps.items()])
    cur.executemany("INSERT OR REPLACE INTO stages VALUES (?,?,?,?,?)",
                    [(k, v.get("name"), v.get("sem"), v.get("sort"), v.get("cat")) for k, v in stages.items()])

    rows = []
    for d in snap.get("deals") or []:
        did = str(d["ID"])
        cid = str(d.get("CATEGORY_ID"))
        ocid = origin.get(did, cid)
        sid = str(d.get("STAGE_ID") or "")
        st = stages.get(sid) or {}
        created = _date(d.get("DATE_CREATE"))
        opp = float(d.get("OPPORTUNITY") or 0)
        curr = d.get("CURRENCY_ID") or "RUB"
        rows.append((
            int(did), d.get("TITLE"), cid, cats.get(cid, cid), ocid, cats.get(ocid, ocid),
            sid, st.get("name"), st.get("sem"), 1 if did in won_date else 0, won_date.get(did),
            str(d.get("DATE_CREATE"))[:19], str(d.get("DATE_MODIFY"))[:19],
            str(d.get("CLOSEDATE"))[:19], d.get("CLOSED"),
            (as_of - created).days if created else None,
            opp * float(rates.get(curr, 1)), opp, curr,
            str(d.get("COMPANY_ID") or ""), (comps.get(str(d.get("COMPANY_ID")), {}) or {}).get("title"),
            str(d.get("ASSIGNED_BY_ID") or ""), users.get(str(d.get("ASSIGNED_BY_ID")), ""),
            None, None, None,
        ))
    cur.executemany("INSERT OR REPLACE INTO deals VALUES (" + ",".join("?" * 26) + ")", rows)

    ev = []
    for did, hrows in hist.items():
        for c, s, t in hrows:
            st = stages.get(s) or {}
            ev.append((int(did), str(c), s, st.get("name"), st.get("sem"), t))
    cur.executemany("INSERT INTO stage_events VALUES (?,?,?,?,?,?)", ev)

    cur.execute("DELETE FROM search WHERE kind='deal'")
    cur.executemany("INSERT INTO search (body, kind, ref, deal_id) VALUES (?,'deal',?,?)",
                    [(f"{r[1]} {r[20] or ''}", str(r[0]), r[0]) for r in rows])

    con.commit()
    stats = {
        "deals": len(rows),
        "stage_events": len(ev),
        "companies": len(comps),
        "users": len(users),
        "won": sum(1 for r in rows if r[9]),
    }
    con.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--db", default=str(BASE_DIR / "kvant.db"))
    a = ap.parse_args()
    st = load(a.export, a.db)
    print(f"загружено: сделок {st['deals']}, событий стадий {st['stage_events']}, "
          f"компаний {st['companies']}, сотрудников {st['users']}, дошло до реализации {st['won']}")
    print(f"база: {a.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
