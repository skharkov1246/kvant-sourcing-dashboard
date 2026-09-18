#!/usr/bin/env python3
"""Где наша выданная цена существенно ВЫШЕ найденной — риск отказа по цене.

ЗАЧЕМ ОТДЕЛЬНО ОТ ЗАНИЖЕННЫХ. Это другой риск и другое решение. Заниженная
строка теряет деньги на исполнении: цены заказчику твёрдые, торг идёт вниз, и
дешевле купить уже не выйдет. Завышенная строка денег не теряет — она теряет
сделку: закупщик заказчика сверяет позиции с рынком выборочно, и одна позиция
дороже рынка на порядок ставит под сомнение всё предложение. Разбор выкладки по
ЛУКОЙЛу это уже показал: 3 135 USD в нашем предложении против примерно 290 USD
за все 38 штук.

ПОЧЕМУ НЕ ВСЯКОЕ ПРЕВЫШЕНИЕ — РИСК. Наценка на сложную логистику, растаможку и
гарантию нормальна и составляет разы, а не проценты. Поэтому здесь не «выше
найденной», а «выше найденной КРАТНО»: порог назван числом и печатается рядом с
итогом, чтобы его можно было оспорить. Одна цена продавца сама по себе рынком
тоже не является — разряды те же, что у заниженных, и по той же причине.

    python gt/tools/ship_overpriced.py [--times 3] [--print]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ship_underpriced import ASK, INDEP, RV, SUBST, key, num, one_operator, tier, withdrawn  # noqa: E402
from verdicts import vkey  # noqa: E402

OUT = ROOT / "gt/data/ship_overpriced.json"
#: Во сколько раз наш ПОЛ должен превышать найденную цену, чтобы строка
#: считалась риском. Три — не истина, а порог: наценка в разы бывает
#: обоснованной, а на порядок — почти никогда.
TIMES = 3.0


def build(times: float = TIMES) -> dict:
    ask = {}
    for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]:
        k = key(r.get("pn"))
        if k:
            ask.setdefault(k, r)
    rv = json.loads(RV.read_text(encoding="utf-8"))["rows"]
    solo = one_operator(json.loads(INDEP.read_text(encoding="utf-8")))
    gone = withdrawn(json.loads(SUBST.read_text(encoding="utf-8")))

    rows, skipped = [], {"цена снята как подстановка": 0, "строки нет в сводке заявки": 0,
                         "нет числа по одну из сторон": 0, "превышение меньше порога": 0}
    for x in rv:
        if vkey(x) != "ЗАВЫШЕНА":
            continue
        k = key(x.get("pn"))
        if k in gone:
            skipped["цена снята как подстановка"] += 1
            continue
        a = ask.get(k)
        if not a:
            skipped["строки нет в сводке заявки"] += 1
            continue
        lo, hi, qty = num(a.get("usd_lo")), num(a.get("usd_hi")), num(a.get("qty"))
        found = num(x.get("price_high")) or num(x.get("price_low"))
        if not lo or not found or not qty:
            skipped["нет числа по одну из сторон"] += 1
            continue
        if lo / found < times:
            skipped["превышение меньше порога"] += 1
            continue
        t, why = tier(a, x, k in solo)
        rows.append({
            "pn": x.get("pn"), "name": str(a.get("name") or "")[:120], "sheet": a.get("sheet"),
            "qty": qty, "our_lo": lo, "our_hi": hi, "found_unit": found,
            "times": round(lo / found, 1), "tier": t, "why_tier": why,
            "found_kind": str(x.get("price_kind") or "")[:200],
            "our_exposure": round((lo + (hi or lo)) / 2 * qty, 2),
            "source": str(x.get("price_source") or "")[:300],
        })
    rows.sort(key=lambda r: -r["our_exposure"])
    tiers = {}
    for t in ("в сумму идёт", "покрытие не подтверждено", "цена не закупочная"):
        part = [r for r in rows if r["tier"] == t]
        tiers[t] = {"rows": len(part),
                    "our_exposure_usd": round(sum(r["our_exposure"] for r in part), 2)}
    return {
        "updated": date.today().isoformat(),
        "source": "Вердикты «ЗАВЫШЕНА» перепроверки против нашей вилки в сводке заявки. "
                  "Считает gt/tools/ship_overpriced.py.",
        "threshold_times": times,
        "what_it_is": "Строки, где наш ПОЛ кратно выше найденной цены. Риск здесь не в "
                      "деньгах, а в сделке: одна позиция дороже рынка на порядок ставит под "
                      "сомнение всё предложение при выборочной сверке закупщиком.",
        "what_it_is_not": "НЕ обвинение в наценке. Наценка на логистику, растаможку и "
                          "гарантию нормальна и измеряется разами. Порог назван числом "
                          "(threshold_times) именно для того, чтобы его можно было оспорить.",
        "rows_total": len(rows),
        "our_exposure_usd": round(sum(r["our_exposure"] for r in rows), 2),
        "tiers": tiers, "skipped": skipped, "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--times", type=float, default=TIMES, help="порог кратности")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    doc = build(a.times)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    money = f"{doc['our_exposure_usd']:,.0f}".replace(",", " ")
    print(f"строк, где наш пол выше найденной цены в {a.times:g}+ раза: {doc['rows_total']} · "
          f"наша экспозиция по ним {money} USD")
    for name, v in doc["tiers"].items():
        ours = f"{v['our_exposure_usd']:,.0f}".replace(",", " ")
        print(f"  {name:<26} {v['rows']:3d} строк · наша экспозиция {ours} USD")
    print("отсеяно:", ", ".join(f"{k} — {v}" for k, v in doc["skipped"].items() if v) or "ничего")
    if a.print:
        print()
        for r in doc["rows"][:30]:
            print(f"{r['our_exposure']:11,.0f} | {r['times']:5.1f}× | пол {r['our_lo']:9,.2f} | "
                  f"найдено {r['found_unit']:9,.2f} | {r['tier'][:12]:<12} "
                  f"{str(r['pn'])[:20]}".replace(",", " "))
    print(f"\n{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
