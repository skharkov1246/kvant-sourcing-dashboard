#!/usr/bin/env python3
"""Выгрузка базы знаний наружу: CSV + готовая схема PostgreSQL.

Зачем. Всё, что разобрано из портала, живёт в одном файле kvant.db на машине
агента. Чтобы этим пользовались люди и другие системы, нужен переносимый
слепок: таблицы в CSV и DDL, который создаёт их в PostgreSQL (Supabase) одной
командой, вместе с индексами и комментариями к колонкам.

Выгружаются семь наборов:
  files      — карточка на документ (что за файл, чей, о чём)   file_cards.py
  catalog    — справочник оборудования: артикул, цены, поставщики kb_catalog.py
  positions  — номенклатура из документов с ценами и марками     extract_positions.py
  deals      — сделки с исходом, суммой и сегментом
  rfq        — запросы поставщикам (смарт-процесс 166)
  price_pairs— пары «цена поставщика ↔ наша цена» по одной позиции
  brands     — справочник марок из портала
  companies  — контрагенты

    python base/export_kb.py --db base/kvant.db --out kb/
    psql "$SUPABASE_DB_URL" -f kb/schema.sql      # создать таблицы
    psql "$SUPABASE_DB_URL" -c "\\copy kb_files from program 'zcat kb/kb_files.csv.gz' csv header"
"""
from __future__ import annotations

import argparse
import csv
import gzip
import sqlite3
from pathlib import Path

# Тип колонки в PostgreSQL задаётся здесь, а не угадывается по данным: в SQLite
# типов почти нет, и выгрузка «всё text» сделала бы базу неудобной — по такой
# не отфильтровать сделки дороже миллиона и не сложить количество.
NUM = {"bytes", "pages", "chars", "copies", "deals", "deal_id", "rfq_id", "won",
       "deal_sum", "positions", "priced", "blank", "id", "qty", "price", "price_total",
       "sum_eur", "sum_orig", "age_days", "amount", "supplier_id", "company_id",
       "contact_id", "assigned_id", "chosen", "files", "hits", "main",
       "price_sup", "price_our", "ratio", "price_sup_eur", "price_our_eur",
       "delivery_days", "prepay_pct", "defer_days", "warranty_mo", "penalty_pct", "nmck",
       "mentions", "docs", "lost", "price_min", "price_med", "price_max", "price_n",
       "sup_med", "our_med", "markup", "qty_total"}
INT = {"bytes", "pages", "chars", "copies", "deals", "deal_id", "rfq_id", "won",
       "positions", "priced", "blank", "id", "age_days", "supplier_id", "company_id",
       "contact_id", "assigned_id", "chosen", "files", "hits", "main",
       "mentions", "docs", "lost", "price_n"}

EXPORTS: list[tuple[str, str, str]] = [
    # (имя набора, SQL, комментарий к таблице)
    ("files", """SELECT * FROM file_cards""",
     "Карточка документа: род, сторона, язык, даты, ИНН, номенклатура. Ключ — sha1 содержимого"),
    ("catalog", """SELECT * FROM catalog_items""",
     "Справочник оборудования: артикул → марка, цены, поставщики, исходы сделок"),
    ("positions", """
        SELECT p.id, p.deal_id, p.fid, p.seg, p.part_number, p.manufacturer, p.name,
               p.qty, p.unit, p.price, p.currency, p.price_total, p.source,
               d.company, d.won, d.closed, d.date_create AS deal_date,
               c.side, c.kind AS doc_kind
        FROM positions p
        LEFT JOIN deals d ON d.id = p.deal_id
        LEFT JOIN file_cards c ON c.fid = p.fid""",
     "Номенклатура из документов: артикул, марка, количество, цена + исход сделки"),
    ("deals", """
        SELECT id, title, category, origin_cat, stage, semantic, won, won_date,
               date_create, closedate, closed, age_days, sum_eur, currency,
               company_id, company, assigned, seg, item, brand
        FROM deals""",
     "Сделки: воронка, стадия, исход, сумма, заказчик, ответственный, сегмент"),
    ("rfq", """
        SELECT id, deal_id, title, stage, supplier_id, supplier, company_id,
               currency, amount, created, updated, closed, chosen, files
        FROM rfq""",
     "Запросы поставщикам (смарт-процесс 166): кому, на что, чем кончилось"),
    ("price_pairs", """SELECT * FROM price_pairs""",
     "Пары «цена поставщика ↔ наша цена» по одной позиции: наценка"),
    ("brands", """SELECT id, title, company_id, created FROM brands""",
     "Справочник марок из портала (смарт-процесс 176)"),
    ("companies", """SELECT id, title, industry FROM companies""",
     "Контрагенты портала"),
]


def pg_type(col: str) -> str:
    if col in INT:
        return "integer"
    if col in NUM:
        return "double precision"
    return "text"


def dump(con: sqlite3.Connection, name: str, sql: str, out: Path) -> tuple[int, list[str]]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    path = out / f"kb_{name}.csv.gz"
    n = 0
    with gzip.open(path, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        while True:
            batch = cur.fetchmany(20_000)
            if not batch:
                break
            w.writerows(batch)
            n += len(batch)
    return n, cols


def run(db_path: str, out_dir: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")

    ddl: list[str] = ["-- База знаний КВАНТ: схема для PostgreSQL / Supabase.",
                      "-- Создана export_kb.py. Загрузка данных — \\copy из kb_*.csv.gz.", ""]
    copy: list[str] = []
    stat = {}
    for name, sql, note in EXPORTS:
        try:
            n, cols = dump(con, name, sql, out)
        except sqlite3.OperationalError as e:
            print(f"  {name}: пропущено ({e})", flush=True)
            continue
        stat[name] = n
        tbl = f"kb_{name}"
        body = ",\n".join(f"  {c:14} {pg_type(c)}" for c in cols)
        ddl.append(f"DROP TABLE IF EXISTS {tbl};\nCREATE TABLE {tbl} (\n{body}\n);")
        ddl.append(f"COMMENT ON TABLE {tbl} IS '{note}';")
        copy.append(f"\\copy {tbl} FROM PROGRAM 'zcat kb_{name}.csv.gz' CSV HEADER")
        print(f"  {tbl:16} {n:>9} строк", flush=True)

    ddl += ["", "-- индексы под обычные вопросы к базе"]
    ddl += [
        "CREATE INDEX IF NOT EXISTS ix_files_kind ON kb_files(kind);",
        "CREATE INDEX IF NOT EXISTS ix_files_deal ON kb_files(deal_id);",
        "CREATE INDEX IF NOT EXISTS ix_cat_brand ON kb_catalog(brand);",
        "CREATE INDEX IF NOT EXISTS ix_cat_pn ON kb_catalog(pn);",
        "CREATE INDEX IF NOT EXISTS ix_pos_pn ON kb_positions(part_number);",
        "CREATE INDEX IF NOT EXISTS ix_pos_oem ON kb_positions(manufacturer);",
        "CREATE INDEX IF NOT EXISTS ix_pos_deal ON kb_positions(deal_id);",
        "CREATE INDEX IF NOT EXISTS ix_rfq_sup ON kb_rfq(supplier);",
        "", "-- загрузка (запускать из каталога с выгрузкой):",
    ] + [f"-- {c}" for c in copy]
    (out / "schema.sql").write_text("\n".join(ddl) + "\n", encoding="utf-8")
    (out / "load.sh").write_text(
        "#!/bin/sh\n# Загрузка базы знаний в PostgreSQL. Нужна переменная PGURL.\n"
        "set -e\npsql \"$PGURL\" -f schema.sql\n" + "\n".join(f'psql "$PGURL" -c "{c}"' for c in copy) + "\n",
        encoding="utf-8")
    con.close()
    total = sum(stat.values())
    print(f"\nвыгружено {total} строк в {out}", flush=True)
    return stat


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--out", default="kb")
    a = ap.parse_args()
    run(a.db, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
