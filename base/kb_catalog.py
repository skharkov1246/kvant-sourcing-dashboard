#!/usr/bin/env python3
"""Справочник оборудования: артикул → марка, цены, поставщики, исходы.

Зачем. Позиции лежат россыпью — 758 тысяч строк, по строке на каждое
упоминание в каждом документе. Работать с ними нельзя: чтобы ответить «почём
мы обычно берём этот подшипник и кто его нам предлагал», пришлось бы
перебирать сделки руками. Модуль сворачивает россыпь в справочник: строка на
артикул, а в ней — как часто его просят, в каких сегментах, по какой цене
дают поставщики, по какой мы продаём, кто из поставщиков его вообще возит и
чем кончились сделки с ним.

Ключ — артикул, приведённый к сравнимому виду: NU-2216 E, NU2216E и nu 2216 e
в документах разных заказчиков означают один подшипник, а в базе лежали тремя
строками. Приведение снимает пробелы, дефисы и регистр; исходное написание
сохраняется, чтобы артикул можно было показать так, как его пишет заказчик.

Цена берётся медианой, а не средним: в выборке всегда есть строки, где в
колонку цены попала сумма по позиции или курс валюты, и одно такое значение
перекашивает среднее в разы.

Рядом строится supplier_prices — строка на пару «артикул + поставщик»: у кого
этот артикул вообще брали и почём. Справочник отвечает «сколько стоит», эта
таблица — «у кого дешевле», а расчёт предложения начинается со второго вопроса.

    python base/kb_catalog.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

MIN_LEN = 4          # артикулы короче четырёх знаков — почти всегда номер строки
# В формах КП заказчика рядом с кодом строки стоит не наименование, а условие
# поставки или адрес склада — в справочнике такие «наименования» бесполезны.
BAD_NAME = re.compile(
    r"(DDP|DAP|FCA|EXW|CIP|CPT|инкотермс|пункт назначения|склад\b|адрес|"
    r"тема уведомлени|тело уведомлени|услови\w* поставк|грузополучател|"
    r"РФ,\s*\d|\b\d{6},|российская федерац|обл\w*,|край,|район,|г\.\s?[А-Я])", re.I)
# В колонку артикула тоже попадает срок поставки: «1-2weeks», «30 дней».
BAD_PN = re.compile(r"^\d{1,2}\s*-?\s*\d{0,2}\s*(weeks?|days?|нед|дн|мес)", re.I)
MARKUP_RANGE = (0.5, 12.0)      # за этими границами — не наценка, а разные строки
# Строки типовой формы закупки (её присылают сотни заказчиков): «Пояснение
# внеплановости закупки», «Notification Subject for Rating». Это поля бланка,
# а не оборудование, но у них есть код в колонке артикула — и в справочнике
# они выходили на первое место с 854 сделками.
FORM_ROW = re.compile(
    r"(уведомлени|пояснени\w* внеплан|обоснован|комментарий к|причин\w* разрыв|"
    r"риск несени|график выполнени|notification|for rating|for evaluation|"
    r"внеплановост|закрыт\w* процедур|превышени\w* (?:сроков|стоимости))", re.I)
NORM = re.compile(r"[^0-9A-ZА-Я]")


def key_of(pn: str) -> str | None:
    """Сравнимый вид артикула: без пробелов, дефисов и регистра."""
    k = NORM.sub("", (pn or "").upper())
    if len(k) < MIN_LEN or k.isdigit() and len(k) < 6:
        return None
    return k


def pn_num(pn: str) -> float | None:
    """Числовое значение артикула, если он состоит из одних цифр."""
    s = (pn or "").strip()
    return float(s) if s.isdigit() and len(s) <= 15 else None


def med(vals: list[float]) -> float | None:
    return round(statistics.median(vals), 2) if vals else None


def run(db_path: str) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.executescript("""
      DROP TABLE IF EXISTS catalog_items;
      CREATE TABLE catalog_items (
        pn_key    TEXT PRIMARY KEY,   -- артикул, приведённый к сравнимому виду
        pn        TEXT,               -- как его пишут в документах
        brand     TEXT,
        name      TEXT,               -- самое частое наименование
        seg       TEXT,               -- сегмент оборудования
        mentions  INTEGER,            -- упоминаний в документах
        docs      INTEGER,
        deals     INTEGER,
        won       INTEGER,            -- сделок с этим артикулом выиграно
        lost      INTEGER,            -- проиграно
        first_seen TEXT,
        last_seen  TEXT,
        cur        TEXT,              -- валюта, в которой чаще всего дана цена
        price_min  REAL,
        price_med  REAL,
        price_max  REAL,
        price_n    INTEGER,           -- по скольким ценам посчитано
        sup_med    REAL,              -- медиана цены поставщика
        our_med    REAL,              -- медиана нашей цены
        markup     REAL,              -- наша к поставщику, раз
        qty_total  REAL,              -- сколько всего просили
        suppliers  TEXT,              -- кто предлагал
        customers  TEXT               -- кто просил
      );
    """)
    con.commit()

    # сторона документа нужна, чтобы отличить цену поставщика от нашей: в самих
    # позициях этого признака нет, он есть только у документа
    side = dict(con.execute("SELECT fid, side FROM file_cards"))
    sup_of = dict(con.execute("""SELECT c.fid, c.supplier FROM file_cards c
                                 WHERE c.supplier IS NOT NULL AND c.supplier<>''"""))
    deal = {}
    for did, comp, won, closed, dc, seg in con.execute(
            "SELECT id, company, won, closed, date_create, seg FROM deals"):
        deal[did] = (comp, 1 if won else (0 if closed == "Y" else None), (dc or "")[:10], seg)

    agg: dict[str, dict] = defaultdict(lambda: {
        "pn": Counter(), "brand": Counter(), "name": Counter(), "seg": Counter(),
        "deals": set(), "docs": set(), "won": set(), "lost": set(),
        "dates": [], "prices": defaultdict(list), "sup": defaultdict(list),
        "our": defaultdict(list), "qty": 0.0, "suppliers": Counter(), "customers": Counter(),
        "n": 0})

    # наценку считаем только внутри одной сделки и одной валюты: медиана по
    # всем годам сравнивает цену поставщика 2024 года с нашей ценой 2026-го и
    # выдаёт вместо наценки инфляцию
    pair: dict[tuple, tuple[list, list]] = defaultdict(lambda: ([], []))

    sql = """SELECT part_number, manufacturer, name, qty, price, currency, deal_id, fid, seg
             FROM positions WHERE part_number IS NOT NULL"""
    for pn, brand, name, qty, price, cur, did, fid, pseg in con.execute(sql):
        k = key_of(pn)
        if not k or BAD_PN.match(pn.strip()) or (name and FORM_ROW.search(name)):
            continue
        a = agg[k]
        a["n"] += 1
        a["pn"][pn.strip()] += 1
        if brand:
            a["brand"][brand] += 1
        if name and len(name) > 6 and not BAD_NAME.search(name):
            a["name"][re.sub(r"\s+", " ", name).strip()[:80]] += 1
        if pseg:
            a["seg"][pseg] += 1
        if fid:
            a["docs"].add(fid)
        if qty and 0 < qty < 1e6:
            a["qty"] += qty
        comp, won, dc, dseg = deal.get(did, (None, None, None, None))
        if did:
            a["deals"].add(did)
            (a["won"] if won == 1 else a["lost"] if won == 0 else set()).add(did)
        if comp:
            a["customers"][comp] += 1
        if dc:
            a["dates"].append(dc)
        if dseg and not pseg:
            a["seg"][dseg] += 1
        s = sup_of.get(fid)
        if s:
            a["suppliers"][s] += 1
        # цена, равная самому артикулу, — это номер, уехавший в колонку цены:
        # у Grundfos 96525458 «максимальная цена» выходила 96 525 458 EUR
        if price and pn_num(pn) is not None and abs(price - pn_num(pn)) < 0.5:
            price = None
        if price and 0 < price < 1e9 and cur:
            a["prices"][cur].append(price)
            who = side.get(fid)
            if who == "поставщик":
                a["sup"][cur].append(price)
                pair[(k, did, cur)][0].append(price)
            elif who == "мы":
                a["our"][cur].append(price)
                pair[(k, did, cur)][1].append(price)

    # наценка по артикулу — медиана отношений «наша цена ÷ цена поставщика»
    # по тем сделкам, где известны обе
    ratios: dict[str, list[float]] = defaultdict(list)
    for (k, _did, _cur), (sup_p, our_p) in pair.items():
        if not sup_p or not our_p:
            continue
        s, o = statistics.median(sup_p), statistics.median(our_p)
        if s > 0 and MARKUP_RANGE[0] <= o / s <= MARKUP_RANGE[1]:
            ratios[k].append(o / s)

    rows = []
    for k, a in agg.items():
        cur = max(a["prices"], key=lambda c: len(a["prices"][c])) if a["prices"] else None
        pr = sorted(a["prices"].get(cur, []))
        sup, our = med(a["sup"].get(cur, [])), med(a["our"].get(cur, []))
        rows.append((
            k, a["pn"].most_common(1)[0][0],
            a["brand"].most_common(1)[0][0] if a["brand"] else None,
            a["name"].most_common(1)[0][0] if a["name"] else None,
            a["seg"].most_common(1)[0][0] if a["seg"] else None,
            a["n"], len(a["docs"]), len(a["deals"]), len(a["won"]), len(a["lost"]),
            min(a["dates"]) if a["dates"] else None, max(a["dates"]) if a["dates"] else None,
            cur, pr[0] if pr else None, med(pr), pr[-1] if pr else None, len(pr),
            sup, our, round(statistics.median(ratios[k]), 2) if ratios.get(k) else None,
            round(a["qty"], 2),
            ", ".join(s for s, _ in a["suppliers"].most_common(5)),
            ", ".join(c for c, _ in a["customers"].most_common(5))))
    con.executemany(f"INSERT OR REPLACE INTO catalog_items VALUES ({','.join('?'*23)})", rows)
    con.execute("CREATE INDEX IF NOT EXISTS ix_cat_brand ON catalog_items(brand)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_cat_pn ON catalog_items(pn)")
    con.commit()

    # ── кто и почём предлагал: строка на пару «артикул + поставщик»
    # Без этой таблицы справочник отвечает «сколько стоит», но не отвечает
    # «у кого дешевле» — а расчёт предложения начинается со второго вопроса.
    con.executescript("""
      DROP TABLE IF EXISTS supplier_prices;
      CREATE TABLE supplier_prices (
        pn_key   TEXT, pn TEXT, brand TEXT, supplier TEXT, cur TEXT,
        price_med REAL, price_min REAL, price_n INTEGER,
        first_seen TEXT, last_seen TEXT, deals INTEGER, won INTEGER
      );
    """)
    sp: dict[tuple, dict] = defaultdict(lambda: {"p": [], "d": set(), "w": set(), "dates": []})
    rows_sp = con.execute("""
        SELECT p.part_number, p.manufacturer, c.supplier, p.price, p.currency, p.deal_id, p.name
        FROM positions p JOIN file_cards c ON c.fid = p.fid
        WHERE p.part_number IS NOT NULL AND p.price IS NOT NULL
          AND c.side='поставщик' AND c.supplier IS NOT NULL AND c.supplier<>''""")
    for pn, brand, supplier, price, cur, did, name in rows_sp:
        k = key_of(pn)
        # те же отсевы, что и в справочнике: без них в таблицу цен попадают
        # сроки поставки («2-3weeks») и строки бланка закупки
        if not k or BAD_PN.match(pn.strip()) or (name and FORM_ROW.search(name)):
            continue
        if not cur or not (0 < price < 1e9):
            continue
        if pn_num(pn) is not None and abs(price - pn_num(pn)) < 0.5:
            continue
        e = sp[(k, supplier, cur)]
        e["p"].append(price)
        e.setdefault("pn", pn.strip())
        e.setdefault("brand", brand)
        if did:
            e["d"].add(did)
            if deal.get(did, (None, None, None, None))[1] == 1:
                e["w"].add(did)
            dc = deal.get(did, (None, None, None, None))[2]
            if dc:
                e["dates"].append(dc)
    con.executemany(f"INSERT INTO supplier_prices VALUES ({','.join('?'*12)})", [
        (k, e.get("pn"), e.get("brand"), supplier, cur, med(e["p"]), min(e["p"]), len(e["p"]),
         min(e["dates"]) if e["dates"] else None, max(e["dates"]) if e["dates"] else None,
         len(e["d"]), len(e["w"]))
        for (k, supplier, cur), e in sp.items()])
    con.execute("CREATE INDEX IF NOT EXISTS ix_sp_pn ON supplier_prices(pn_key)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_sp_sup ON supplier_prices(supplier)")
    con.commit()

    q = lambda s: con.execute(s).fetchone()[0]      # noqa: E731
    out = {
        "items": len(rows),
        "with_price": q("SELECT count(*) FROM catalog_items WHERE price_med IS NOT NULL"),
        "with_brand": q("SELECT count(*) FROM catalog_items WHERE brand IS NOT NULL"),
        "repeat": q("SELECT count(*) FROM catalog_items WHERE deals>1"),
        "with_markup": q("SELECT count(*) FROM catalog_items WHERE markup IS NOT NULL"),
        "sup_prices": q("SELECT count(*) FROM supplier_prices"),
        "sup_multi": q("""SELECT count(*) FROM (SELECT pn_key FROM supplier_prices
                          GROUP BY pn_key HAVING count(DISTINCT supplier)>1)"""),
    }
    print(f"артикулов в справочнике: {out['items']}", flush=True)
    print(f"  с ценой:            {out['with_price']}", flush=True)
    print(f"  с маркой:           {out['with_brand']}", flush=True)
    print(f"  просили не раз:     {out['repeat']}", flush=True)
    print(f"  с наценкой:         {out['with_markup']}", flush=True)
    print(f"цен поставщиков:      {out['sup_prices']} "
          f"(артикулов с двумя и более поставщиками: {out['sup_multi']})", flush=True)
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
