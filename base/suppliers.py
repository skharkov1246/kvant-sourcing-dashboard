#!/usr/bin/env python3
"""Кто из поставщиков отвечает, а кто молчит — и кто чем возит.

Зачем. Самый сильный признак проигранной сделки — молчание поставщика: сюжет
«поставщик не ответил» встречается у проигранных втрое чаще, чем у выигранных
(loss_reasons.py). При этом почти половина запросов в портале так и остаётся
в стадии «отправлено»: 10 005 из 21 225. Но пока это одно общее число, делать
с ним нечего. Нужен счёт по каждому поставщику: сколько ему написали, сколько
раз он ответил файлом, сколько раз его выбрали — и по каким маркам.

Две таблицы:
  supplier_stats  — строка на поставщика: запросы, ответы, молчание, отказы,
                    выбор, сделки и победы, срок до движения по стадии;
  brand_suppliers — строка на пару «марка + поставщик»: кто реально присылал
                    цены по этой марке. Это ответ на вопрос «кому писать по
                    Epiroc», с которого начинается сорсинг.

    python base/suppliers.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

# Стадии запроса, разложенные по смыслу. Названия в портале смешанные —
# английские от коробочного шаблона и русские, добавленные позже.
SILENT = ("Request Sent", "New Request", "Ответ не получен (в срок)")
TALKING = ("In Correspondance", "Unread Messages", "Price at Work")
GOT = ("Selected", "КП получено")
REFUSED = ("Отказ в КП",)
LOST = ("Not Selected", "Не подошло по технике", "Не прошли по цене")


def med(v: list[float]) -> float | None:
    return round(statistics.median(v), 1) if v else None


def run(db_path: str) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.executescript("""
      DROP TABLE IF EXISTS supplier_stats;
      CREATE TABLE supplier_stats (
        supplier   TEXT PRIMARY KEY,
        requests   INTEGER,      -- сколько запросов ему отправлено
        answered   INTEGER,      -- в скольких появился его файл
        moved_on   INTEGER,      -- в скольких запрос ушёл из стадии «отправлено»
        silent     INTEGER,      -- ни файла, ни движения по стадии
        talking    INTEGER,      -- переписка идёт
        refused    INTEGER,      -- отказался считать
        selected   INTEGER,      -- его КП выбрали
        chosen     INTEGER,      -- отмечен выбранным в карточке запроса
        answer_rate REAL,        -- доля запросов, где он вообще отозвался
        select_rate REAL,        -- доля ответов, дошедших до выбора
        deals      INTEGER,
        won_deals  INTEGER,      -- сделки с его участием, которые мы выиграли
        days_med   REAL,         -- медиана дней до последнего движения по стадии
        positions  INTEGER,      -- позиций из его оферт
        priced     INTEGER,      -- из них с ценой
        brands     TEXT,
        first_seen TEXT,
        last_seen  TEXT
      );
      DROP TABLE IF EXISTS brand_suppliers;
      CREATE TABLE brand_suppliers (
        brand    TEXT, supplier TEXT,
        positions INTEGER, priced INTEGER, deals INTEGER, won INTEGER,
        cur TEXT, price_med REAL, last_seen TEXT
      );
    """)
    con.commit()

    deal_won = {d: (w == 1) for d, w in con.execute("SELECT id, won FROM deals")}
    deal_date = dict(con.execute("SELECT id, substr(date_create,1,10) FROM deals"))

    agg: dict[str, dict] = defaultdict(lambda: {
        "req": 0, "ans": 0, "sil": 0, "moved": 0, "talk": 0, "ref": 0, "sel": 0, "ch": 0,
        "deals": set(), "won": set(), "days": [], "dates": []})
    sql = """SELECT supplier, stage, files, chosen, deal_id,
                    substr(created,1,10), substr(moved,1,10)
             FROM rfq WHERE supplier IS NOT NULL AND supplier<>''"""
    for sup, stage, files, chosen, did, created, moved in con.execute(sql):
        a = agg[sup.strip()]
        a["req"] += 1
        if files:
            a["ans"] += 1
        if stage not in SILENT:
            a["moved"] += 1
        # молчанием считаем только полное молчание: и стадия не сдвинулась, и
        # файла нет. Иначе поставщик, приславший КП, но оставшийся в «отправлено»
        # (стадию не всегда двигают руками), попадал бы в молчуны
        if files == 0 and stage in SILENT:
            a["sil"] += 1
        if stage in TALKING:
            a["talk"] += 1
        elif stage in REFUSED:
            a["ref"] += 1
        elif stage in GOT:
            a["sel"] += 1
        if chosen:
            a["ch"] += 1
        if did:
            a["deals"].add(did)
            if deal_won.get(did):
                a["won"].add(did)
        if created:
            a["dates"].append(created)
        if created and moved:
            try:
                from datetime import date
                d0 = date.fromisoformat(created)
                d1 = date.fromisoformat(moved)
                if 0 <= (d1 - d0).days < 400:
                    a["days"].append((d1 - d0).days)
            except ValueError:
                pass

    # позиции и марки из документов поставщика: сколько цен он реально прислал
    pos_by_sup: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    brands_by_sup: dict[str, Counter] = defaultdict(Counter)
    pair: dict[tuple, dict] = defaultdict(lambda: {
        "n": 0, "priced": 0, "deals": set(), "won": set(), "p": defaultdict(list), "dates": []})
    sql2 = """SELECT c.supplier, p.manufacturer, p.price, p.currency, p.deal_id
              FROM positions p JOIN file_cards c ON c.fid = p.fid
              WHERE c.supplier IS NOT NULL AND c.supplier<>''"""
    for sup, brand, price, cur, did in con.execute(sql2):
        sup = sup.strip()
        pos_by_sup[sup][0] += 1
        if price:
            pos_by_sup[sup][1] += 1
        if not brand:
            continue
        brands_by_sup[sup][brand] += 1
        e = pair[(brand, sup)]
        e["n"] += 1
        if price and cur and 0 < price < 1e9:
            e["priced"] += 1
            e["p"][cur].append(price)
        if did:
            e["deals"].add(did)
            if deal_won.get(did):
                e["won"].add(did)
            if deal_date.get(did):
                e["dates"].append(deal_date[did])

    rows = []
    for sup, a in agg.items():
        n, ans, sil = a["req"], a["ans"], a["sil"]
        pos, priced = pos_by_sup.get(sup, [0, 0])
        rows.append((
            sup, n, ans, a["moved"], sil, a["talk"], a["ref"], a["sel"], a["ch"],
            round(1 - sil / n, 3) if n else None,
            round(a["sel"] / ans, 3) if ans else None,
            len(a["deals"]), len(a["won"]), med(a["days"]), pos, priced,
            ", ".join(b for b, _ in brands_by_sup[sup].most_common(5)),
            min(a["dates"]) if a["dates"] else None,
            max(a["dates"]) if a["dates"] else None))
    con.executemany(f"INSERT OR REPLACE INTO supplier_stats VALUES ({','.join('?'*19)})", rows)

    prows = []
    for (brand, sup), e in pair.items():
        cur = max(e["p"], key=lambda k: len(e["p"][k])) if e["p"] else None
        prows.append((brand, sup, e["n"], e["priced"], len(e["deals"]), len(e["won"]),
                      cur, med(e["p"].get(cur, [])) if cur else None,
                      max(e["dates"]) if e["dates"] else None))
    con.executemany(f"INSERT INTO brand_suppliers VALUES ({','.join('?'*9)})", prows)
    con.execute("CREATE INDEX IF NOT EXISTS ix_bs_brand ON brand_suppliers(brand)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_bs_sup ON brand_suppliers(supplier)")
    con.commit()

    q = lambda s: con.execute(s).fetchone()[0]      # noqa: E731
    out = {
        "suppliers": len(rows),
        "requests": q("SELECT sum(requests) FROM supplier_stats"),
        "silent": q("SELECT sum(silent) FROM supplier_stats"),
        "answered": q("SELECT sum(answered) FROM supplier_stats"),
        "pairs": len(prows),
        "workhorses": q("""SELECT count(*) FROM supplier_stats
                           WHERE requests>=10 AND answer_rate>=0.6"""),
        "deadweight": q("""SELECT count(*) FROM supplier_stats
                           WHERE requests>=10 AND answer_rate<0.3"""),
    }
    print(f"поставщиков: {out['suppliers']}, запросов {out['requests']}, "
          f"без ответа {out['silent']} ({100*out['silent']/max(out['requests'],1):.0f}%)", flush=True)
    print(f"пар «марка + поставщик»: {out['pairs']}", flush=True)
    print(f"рабочие (≥10 запросов, отвечают чаще 60 %): {out['workhorses']}", flush=True)
    print(f"мёртвые (≥10 запросов, отвечают реже 30 %): {out['deadweight']}", flush=True)
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    a = ap.parse_args()
    run(a.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
