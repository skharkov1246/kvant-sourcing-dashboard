#!/usr/bin/env python3
"""Объём продаж против объёма закупки — и по какой доле объёма закупка вообще посчитана.

ВОПРОС ВЛАДЕЛЬЦА ДОСЛОВНО: какие строки берём, какие нет, какой объём продаж и
какой объём закупки. Первые два вопроса отчёт закрывал таблицей решений, вторые
два — нет, и это был пробел: сумма предложения печаталась, а сумма закупки под
ней не считалась нигде.

ПОЧЕМУ ПРОСТО СЛОЖИТЬ НЕЛЬЗЯ. Закупочная цена есть далеко не у всех строк, а у
части она есть в виде, который в сумму не идёт: запрос брокера, витрина одного
оператора под несколькими доменами, цена без подтверждённого покрытия объёма.
Сложить всё вместе — получить «объём закупки», который на три четверти состоит
из цен, за которые никто не продаст. Поэтому разряды те же, что в
gt/tools/ship_underpriced.py, плюс четвёртый: строка, чью цену перепроверка не
смотрела вовсе, в сумму закупки не идёт независимо от покрытия — происхождение
её цены не проверял никто.

ЧТО ЭТО ДАЁТ. Одну честную строку для руководства: по какой доле объёма продаж
закупка установлена так, что её можно сложить, и какая на этой доле маржа.
Остальное — не «маржа неизвестна», а названный числом объём работы.

    python gt/tools/ship_margin.py [--print]
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

OUT = ROOT / "gt/data/ship_margin.json"

NO_PRICE = "закупочной цены нет"
UNCHECKED = "цена не проверена перепроверкой"
ORDER = ("в сумму идёт", "покрытие не подтверждено", "цена не закупочная", UNCHECKED, NO_PRICE)

MEANING = {
    "в сумму идёт": "Покрытие объёма подтверждено, цена не брокерская, свидетель не один, "
                    "происхождение цены проверено перепроверкой. Только эта доля складывается "
                    "в объём закупки.",
    "покрытие не подтверждено": "Цена найдена и проверена, но продавец не подтверждал, что "
                                "закроет наш объём. Закрывается одним письмом на строку.",
    "цена не закупочная": "Запрос брокера либо один оператор под несколькими доменами. Нужен "
                          "второй независимый свидетель.",
    UNCHECKED: "Цена стоит в сводке заявки, но перепроверка её не смотрела: кто продавец, та "
               "ли это деталь и не заглушка ли цена — не проверял никто.",
    NO_PRICE: "Закупочной цены нет вовсе. Это не пробел отчёта, а объём работы, названный "
              "числом.",
}


def build() -> dict:
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r.get("pn")): r for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]}
    solo = one_operator(json.loads(INDEP.read_text(encoding="utf-8")))
    gone = withdrawn(json.loads(SUBST.read_text(encoding="utf-8")))

    acc = {t: {"rows": 0, "sales_usd": 0.0, "purchase_usd": 0.0} for t in ORDER}
    sales_total, rows_total = 0.0, 0
    good_rows: list[dict] = []          # строки счётного разряда — поимённо, для оговорки
    for a in ask:
        lo, hi, qty = num(a.get("usd_lo")), num(a.get("usd_hi")), num(a.get("qty"))
        if not qty or not (lo or hi):
            continue                       # без нашей цены строка не в объёме продаж
        sale = ((lo or hi) + (hi or lo)) / 2 * qty
        sales_total += sale
        rows_total += 1
        k = key(a.get("pn"))
        x = rv.get(k)
        found = None
        if x and k not in gone:
            found = num(x.get("price_low")) or num(x.get("price_high"))
        if found:
            t, _ = tier(a, x, k in solo)
        else:
            swept = None if k in gone else (num(a.get("unit_price_usd")) or num(a.get("price_usd")))
            # Цена из сводки без разбора перепроверки в сумму закупки не идёт
            # НИКОГДА: её происхождение не проверял никто, а именно там и жили
            # заглушки — 0.00 при «нет в наличии», одно значение на 132
            # разнородных изделиях, нижняя граница вилки на класс.
            t, found = (UNCHECKED, swept) if swept else (NO_PRICE, None)
        acc[t]["rows"] += 1
        acc[t]["sales_usd"] += sale
        acc[t]["purchase_usd"] += (found or 0) * qty
        if t == "в сумму идёт":
            good_rows.append({"pn": a.get("pn"), "qty": qty,
                              "sales_usd": round(sale, 2),
                              "purchase_usd": round(found * qty, 2),
                              "delta_usd": round(sale - found * qty, 2)})

    for t in acc:
        acc[t]["sales_usd"] = round(acc[t]["sales_usd"], 2)
        acc[t]["purchase_usd"] = round(acc[t]["purchase_usd"], 2)
        acc[t]["share_of_sales"] = round(100 * acc[t]["sales_usd"] / sales_total, 1) if sales_total else 0.0
        acc[t]["what_it_means"] = MEANING[t]
    good = acc["в сумму идёт"]
    good_rows.sort(key=lambda r: r["delta_usd"])
    worst = good_rows[0] if good_rows else {}
    above = sum(1 for r in good_rows if r["delta_usd"] > 0)
    return {
        "updated": date.today().isoformat(),
        "source": "Сводка заявки ЛУКОЙЛ (наша цена) против найденных закупочных цен "
                  "перепроверки, с теми же разрядами годности цены. Считает "
                  "gt/tools/ship_margin.py.",
        "sales_rows": rows_total,
        "sales_usd": round(sales_total, 2),
        "countable": {
            "rows": good["rows"],
            "sales_usd": good["sales_usd"],
            "purchase_usd": good["purchase_usd"],
            "margin_usd": round(good["sales_usd"] - good["purchase_usd"], 2),
            "margin_pct": round(100 * (good["sales_usd"] - good["purchase_usd"]) / good["sales_usd"], 1)
            if good["sales_usd"] else 0.0,
            "share_of_sales": good["share_of_sales"],
        },
        "what_it_is_not": "Разряды складывать нельзя. «Объём закупки» по заявке в целом здесь "
                          "НЕ назван и назван быть не может: по 54 % объёма закупочной цены "
                          "нет вовсе, а ещё по части она есть в виде, за который никто не "
                          "продаст. Названа доля, по которой счёт возможен.",
        "bias_warning": {
            "why": "Сальдо счётного разряда НЕ является маржой сделки, и читать его так нельзя. "
                   "Выборка смещена по построению: полное покрытие объёма подтверждается там, "
                   "где мы специально спрашивали продавца, а спрашивали мы по дорогим и "
                   "спорным строкам. Тринадцать строк из восьмисот пятидесяти семи — не "
                   "выборка, а перечень.",
            "rows_sales_above_purchase": above,
            "rows_sales_below_purchase": len(good_rows) - above,
            "largest_single_row": worst,
            "what_it_does_say": "Одно: там, где закупка установлена твёрдо, она у части строк "
                                "оказывается дороже нашей цены — и сальдо делает одна строка, "
                                "а не тенденция.",
        },
        "countable_rows": good_rows,
        "by_grade": acc,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    doc = build()
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    m = lambda v: f"{v:,.0f}".replace(",", " ")  # noqa: E731
    print(f"ОБЪЁМ ПРОДАЖ: {m(doc['sales_usd'])} USD по {doc['sales_rows']} строкам с нашей ценой")
    c = doc["countable"]
    print(f"ЗАКУПКА ПОДДАЁТСЯ СЧЁТУ по {c['rows']} строкам — это {c['share_of_sales']} % объёма: "
          f"продажи {m(c['sales_usd'])} против закупки {m(c['purchase_usd'])}, "
          f"сальдо {m(c['margin_usd'])} USD ({c['margin_pct']} %)")
    b = doc["bias_warning"]
    print(f"  ОГОВОРКА: это не маржа сделки. Продажа выше закупки в "
          f"{b['rows_sales_above_purchase']} строках, ниже в "
          f"{b['rows_sales_below_purchase']}; сальдо определяет одна строка "
          f"{b['largest_single_row'].get('pn')} ({m(b['largest_single_row'].get('sales_usd', 0))} "
          f"против {m(b['largest_single_row'].get('purchase_usd', 0))})")
    if a.print:
        print()
        print(f"{'разряд годности цены':<32} {'строк':>6} {'продажи':>13} {'закупка':>13} {'доля':>7}")
        for t in ORDER:
            v = doc["by_grade"][t]
            if not v["rows"]:
                continue
            print(f"{t:<32} {v['rows']:6d} {m(v['sales_usd']):>13} "
                  f"{m(v['purchase_usd']):>13} {v['share_of_sales']:6.1f} %")
    print(f"\n{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
