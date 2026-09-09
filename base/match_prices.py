#!/usr/bin/env python3
"""Сшивка цен: оферта поставщика ↔ наше ТКП по одной и той же позиции.

Зачем: без этой связки нельзя измерить ни наценку, ни то, на чём мы теряем в
цене. Сшивать по артикулу почти не на чем — в наших ТКП артикул проставлен лишь
в пятой части документов, и совпадений по номеру набирается около сотни на весь
год. Поэтому основной ключ — НАИМЕНОВАНИЕ, приведённое к сопоставимому виду.

Нормализация: нижний регистр, латиница и кириллица раздельно не различаются по
регистру, выкидываются знаки препинания, единицы измерения и служебные слова
(«поз.», «шт», «ндс»), числа сохраняются — в наименованиях запчастей размер и
типоразмер несут смысл. Сравнение — по доле общих значимых токенов.

ВАЛЮТА. Отношение цен в разных валютах — это курс, а не наценка, поэтому пара
считается только когда обе цены приведены к одной валюте. Совпала валюта —
берём отношение как есть; разошлась (поставщик в долларах или юанях, мы в евро
или рублях — так в 200 сделках из 269) — обе цены переводятся в евро по курсу
ЦБ на дату создания сделки, модуль base/fx.py. Если валюта не распозналась
вовсе, стороны считаются одновалютными, как раньше, и такая пара помечена
пустой валютой — на ней наценку мерить нельзя.

    python base/match_prices.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import fx

PUNCT = re.compile(r"[^\w\s./-]+", re.U)
SPACES = re.compile(r"\s+")
STOPW = {"поз", "позиция", "шт", "штук", "компл", "ндс", "итого", "всего", "ед", "изм",
         "наименование", "артикул", "цена", "сумма", "кол", "во", "количество", "for",
         "the", "and", "или", "для", "тип", "type"}
CUR_NORM = {"РУБ": "RUB", "РУБЛЬ": "RUB", "РУБЛИ": "RUB", "ЕВРО": "EUR", "ЮАНЬ": "CNY",
            "ДОЛЛАР": "USD", "RUR": "RUB", "RMB": "CNY"}
MIN_TOKENS = 2
MIN_SIM = 0.6

DDL = """
CREATE TABLE IF NOT EXISTS price_pairs (
  deal_id INTEGER, key TEXT, kind TEXT,          -- kind: 'pn' или 'name'
  name_sup TEXT, name_our TEXT,
  price_sup REAL, price_our REAL, qty REAL, ratio REAL, currency TEXT,
  cur_sup TEXT, cur_our TEXT,                    -- как было в документе
  price_sup_eur REAL, price_our_eur REAL         -- приведённые, если валюты разошлись
);
CREATE INDEX IF NOT EXISTS ix_pp_deal ON price_pairs(deal_id);
"""


def norm(s: str) -> list[str]:
    s = PUNCT.sub(" ", str(s or "").lower().replace("ё", "е"))
    toks = [t for t in SPACES.sub(" ", s).split() if len(t) > 1 and t not in STOPW]
    return toks


def sim(a: set[str], b: set[str]) -> float:
    """Доля общих токенов от меньшего набора: наименование у поставщика обычно
    длиннее нашего (добавлены материал, стандарт), поэтому пересечение делим
    на короткий набор, а не на объединение."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def run(db_path: str) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.executescript(DDL)
    con.execute("DELETE FROM price_pairs")
    fx.ensure(con)
    day = {int(d): fx.day_of(c) for d, c in con.execute("SELECT id, date_create FROM deals")}

    rows = con.execute("""SELECT p.deal_id, f.field_name, p.part_number, p.name, p.price, p.qty,
                                 p.currency
                          FROM positions p JOIN files f ON f.fid = p.fid
                          WHERE p.price IS NOT NULL AND p.price > 0""").fetchall()
    sup: dict[int, list] = defaultdict(list)
    our: dict[int, list] = defaultdict(list)
    for did, field, pn, name, price, qty, cur in rows:
        rec = (pn, name, price, qty, set(norm(name)), CUR_NORM.get(str(cur or "").upper(), cur))
        if str(field).startswith("Offer from supplier"):
            sup[did].append(rec)
        elif field == "Offer from us":
            our[did].append(rec)

    out, stats = [], defaultdict(int)
    for did in set(sup) & set(our):
        used_our: set[int] = set()
        for pn_s, nm_s, pr_s, qty_s, tok_s, cur_s in sup[did]:
            best, best_sim, best_kind, best_i = None, 0.0, None, -1
            for i, (pn_o, nm_o, pr_o, _q, tok_o, cur_o) in enumerate(our[did]):
                if i in used_our:
                    continue
                # разные валюты допустимы: ниже обе цены приводятся к евро
                if pn_s and pn_o and pn_s == pn_o:
                    best, best_sim, best_kind, best_i = (nm_o, pr_o, cur_o), 1.0, "pn", i
                    break
                if len(tok_s) >= MIN_TOKENS and len(tok_o) >= MIN_TOKENS:
                    s = sim(tok_s, tok_o)
                    if s > best_sim:
                        best, best_sim, best_kind, best_i = (nm_o, pr_o, cur_o), s, "name", i
            if best and (best_kind == "pn" or best_sim >= MIN_SIM):
                nm_o, pr_o, cur_o = best
                eur_s = eur_o = None
                if cur_s and cur_o and cur_s != cur_o:
                    d = day.get(did) or fx.day_of(None)
                    eur_s, eur_o = fx.to_eur(con, pr_s, cur_s, d), fx.to_eur(con, pr_o, cur_o, d)
                    if eur_s is None or eur_o is None or not eur_s:
                        stats["без курса"] += 1        # курса на дату нет — пара несопоставима
                        continue
                    ratio, cur = round(eur_o / eur_s, 3), "EUR"
                    stats["приведено к евро"] += 1
                else:
                    ratio = round(pr_o / pr_s, 3) if pr_s else None
                    cur = cur_s or cur_o or ""
                used_our.add(best_i)
                stats[best_kind] += 1
                out.append((did, pn_s or nm_s[:60], best_kind, nm_s[:200], nm_o[:200],
                            pr_s, pr_o, qty_s, ratio, cur,
                            cur_s or "", cur_o or "", eur_s, eur_o))
    con.execute("DROP TABLE IF EXISTS price_pairs")
    con.executescript(DDL)
    con.executemany("INSERT INTO price_pairs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    con.commit()
    stats["pairs"] = len(out)
    stats["deals"] = len({r[0] for r in out})
    con.close()
    return dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    a = ap.parse_args()
    st = run(a.db)
    print(f"пар найдено {st.get('pairs', 0)} по {st.get('deals', 0)} сделкам "
          f"(по артикулу {st.get('pn', 0)}, по наименованию {st.get('name', 0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
