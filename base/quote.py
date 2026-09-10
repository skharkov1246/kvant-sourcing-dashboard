#!/usr/bin/env python3
"""Карточка сорсинга по артикулу: что это, почём, у кого спрашивать.

Ради этого и собиралась база. Приходит запрос заказчика — «нужен NU2216 E» —
и вопрос всегда один и тот же: что это за железка, брали ли мы её раньше,
почём её давали поставщики, кому писать сейчас, чтобы ответили. Раньше на это
уходил час переписки и памяти сорсера. Здесь это один запрос к базе.

Что показывается:
  * артикул: марка, наименование, сколько раз просили, чем кончались сделки;
  * цены: медиана, край, отдельно цена поставщика и наша, наценка;
  * кто давал цену: поставщик, его цена, когда, и как часто он вообще
    отвечает на запросы — цена бесполезна, если по этому адресу молчат;
  * кому писать ещё: поставщики, которые возят эту марку, даже если по
    этому артикулу цены от них не было.

    python base/quote.py NU2216
    python base/quote.py "торцевое уплотнение" --limit 5
    python base/quote.py 3049577 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

NORM = re.compile(r"[^0-9A-ZА-Я]")


def key_of(pn: str) -> str:
    return NORM.sub("", (pn or "").upper())


def find_items(con: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    """Артикулы по запросу: сперва точное совпадение ключа, потом вхождение,
    потом поиск по наименованию — чтобы работало и «NU2216», и «уплотнение»."""
    k = key_of(query)
    sql = "SELECT * FROM catalog_items WHERE {} ORDER BY deals DESC, mentions DESC LIMIT ?"
    if len(k) >= 4:
        rows = con.execute(sql.format("pn_key = ?"), (k, limit)).fetchall()
        if rows:
            return rows
        rows = con.execute(sql.format("pn_key LIKE ?"), (f"%{k}%", limit)).fetchall()
        if rows:
            return rows
    like = f"%{query.strip().lower()}%"
    return con.execute(sql.format("lower(name) LIKE ? OR lower(brand) LIKE ?"),
                       (like, like, limit)).fetchall()


def card(con: sqlite3.Connection, it: sqlite3.Row) -> dict:
    sup = con.execute("""
        SELECT s.supplier, s.cur, s.price_med, s.price_min, s.price_n, s.last_seen,
               t.requests, t.answered, t.silent, t.selected
        FROM supplier_prices s
        LEFT JOIN supplier_stats t ON t.supplier = s.supplier
        WHERE s.pn_key = ? ORDER BY s.price_med""", (it["pn_key"],)).fetchall()
    known = {r["supplier"] for r in sup}
    # кто возит эту марку вообще: пригодится, когда по самому артикулу цен нет
    more = []
    if it["brand"]:
        more = con.execute("""
            SELECT b.supplier, b.positions, b.priced, b.deals, b.won,
                   t.requests, t.answered, t.silent, t.selected
            FROM brand_suppliers b LEFT JOIN supplier_stats t ON t.supplier = b.supplier
            WHERE b.brand = ? ORDER BY b.priced DESC, b.deals DESC LIMIT 8""",
            (it["brand"],)).fetchall()
        more = [r for r in more if r["supplier"] not in known]
    same = con.execute("""SELECT pn, name, deals, price_med, cur FROM catalog_items
                          WHERE brand = ? AND pn_key <> ? ORDER BY deals DESC LIMIT 5""",
                       (it["brand"], it["pn_key"])).fetchall() if it["brand"] else []
    return {"item": it, "suppliers": sup, "brand_suppliers": more, "same_brand": same}


def rate(req, ans, sil) -> str:
    if not req:
        return "нет данных"
    return f"{100*(req-(sil or 0))/req:.0f}% ответов из {req}"


def show(c: dict) -> None:
    it = c["item"]
    print(f"\n{'='*78}")
    print(f"{it['pn']}   {it['brand'] or 'марка не определена'}")
    if it["name"]:
        print(f"  {it['name']}")
    seg = it["seg"] or "—"
    print(f"  сегмент: {seg}    просили в {it['deals']} сделках "
          f"(выиграно {it['won']}, проиграно {it['lost']})    последний раз {it['last_seen'] or '—'}")
    if it["price_med"]:
        line = (f"  цена: медиана {it['price_med']:,.0f} {it['cur']}"
                f"  край {it['price_min']:,.0f}–{it['price_max']:,.0f}  по {it['price_n']} ценам")
        print(line.replace(",", " "))
    if it["sup_med"] and it["our_med"]:
        print(f"  поставщик {it['sup_med']:,.0f} → мы {it['our_med']:,.0f} {it['cur']}"
              .replace(",", " ") + (f"   наценка ×{it['markup']}" if it["markup"] else ""))
    if c["suppliers"]:
        print("\n  кто давал цену:")
        for r in c["suppliers"]:
            price = f"{r['price_med']:,.0f} {r['cur']}".replace(",", " ") if r["price_med"] else "—"
            print(f"    {str(r['supplier'])[:38]:40} {price:>16}  {r['last_seen'] or '—':10} "
                  f" {rate(r['requests'], r['answered'], r['silent'])}")
    if c["brand_suppliers"]:
        print(f"\n  ещё возят {it['brand']}:")
        for r in c["brand_suppliers"]:
            print(f"    {str(r['supplier'])[:38]:40} цен {r['priced'] or 0:5}  сделок {r['deals'] or 0:3}"
                  f"  {rate(r['requests'], r['answered'], r['silent'])}")
    if c["same_brand"]:
        print(f"\n  другие артикулы {it['brand']}: " +
              ", ".join(str(r["pn"]) for r in c["same_brand"]))


def run(db_path: str, query: str, limit: int, as_json: bool) -> int:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=300)
    con.row_factory = sqlite3.Row
    items = find_items(con, query, limit)
    if not items:
        print(f"по запросу «{query}» в справочнике ничего нет", flush=True)
        return 1
    cards = [card(con, it) for it in items]
    if as_json:
        print(json.dumps([{k: ([dict(x) for x in v] if isinstance(v, list) else dict(v))
                           for k, v in c.items()} for c in cards],
                         ensure_ascii=False, indent=1, default=str))
    else:
        for c in cards:
            show(c)
        print()
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="артикул, марка или кусок наименования")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    return run(a.db, a.query, a.limit, a.json)


if __name__ == "__main__":
    raise SystemExit(main())
