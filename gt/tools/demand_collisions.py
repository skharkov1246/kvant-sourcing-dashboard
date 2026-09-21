#!/usr/bin/env python3
"""Один номер на нескольких строках заявки: сколько это денег и чем это может быть.

ЧТО ВСКРЫТО. В листе «Энергосети» 234 номера из 1 198 стоят более чем на одной
строке, и 7 907 штук приходится на вторые и последующие вхождения. Сводка
заявки складывает такие строки в одну позицию, поэтому её количество — сумма,
а не замер. На повторные вхождения приходится 2,05 млн USD расчётной
экспозиции при 9,28 млн по всей заявке, то есть каждый пятый доллар.

ДВА РАЗНЫХ СЛУЧАЯ, И ПУТАТЬ ИХ НЕЛЬЗЯ.

  1. Номер повторяется, а категория изделия одна. Скорее всего одна и та же
     позиция расписана несколькими строками — например, по машинам площадки.
     Складывать количество тут, вероятно, правильно.

  2. Номер повторяется, а категории РАЗНЫЕ. Тогда под одним номером стоят
     разные изделия, и номер их не опознаёт. Самый ясный пример — 4007922: он
     стоит пять раз, по 18 штук, и называется «Газовый регистр»,
     «Предохранительные клапаны», «Газоэлектрический запальник», «Реле
     давления», «Сканер факела». Это не одна деталь в пяти видах, а пять
     разных изделий с одним номером в поле артикула — так бывает, когда номер
     протянут по столбцу. У таких строк недостоверно И количество, И само
     опознание, а значит и цена.

ЧЕГО ЗДЕСЬ НЕТ. Решения. Инструмент не сводит строки и ничего не удаляет: он
считает и называет. Свести количество или разделить строки может только
заказчик, и вопрос ему уже поставлен.

    python gt/tools/demand_collisions.py [--sheet Энергосети] [--write]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMAND = ROOT / "gt/data/rfq_demand.json"
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_demand_collisions.json"


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def measure(sheet: str) -> dict:
    rows = [r for r in json.loads(DEMAND.read_text(encoding="utf-8"))["rows"]
            if r.get("sheet") == sheet and str(r.get("pn") or "").strip()]
    band = {key(r.get("pn")): r for r in json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]}

    by: dict[str, list] = collections.defaultdict(list)
    for r in rows:
        by[key(r["pn"])].append(r)

    items, tot = [], {"same_cat": [0, 0.0], "diff_cat": [0, 0.0]}
    repeat_qty = 0
    for k, v in by.items():
        if len(v) < 2:
            continue
        cats = [str(x.get("cat") or "").strip().lower() for x in v]
        one_cat = len(set(cats)) == 1
        q = [int(x.get("qty") or 0) for x in v]
        repeat_qty += sum(q[1:])
        b = band.get(k)
        e = expo(b) if b else 0.0
        share = (e * sum(q[1:]) / sum(q)) if (e and sum(q)) else 0.0
        bucket = "same_cat" if one_cat else "diff_cat"
        tot[bucket][0] += 1
        tot[bucket][1] += share
        items.append({
            "pn": v[0].get("pn"),
            "rows": len(v),
            "qty_by_row": q,
            "qty_in_repeats": sum(q[1:]),
            "categories": cats,
            "one_category": one_cat,
            "names": [str(x.get("name") or "")[:90] for x in v],
            "usd_exposure": round(e, 2),
            "usd_on_repeats": round(share, 2),
        })
    items.sort(key=lambda x: (-x["usd_exposure"], x["pn"]))
    all_expo = sum(expo(r) for r in json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"])
    return {
        "sheet": sheet,
        "rows_on_sheet": len(rows),
        "distinct_pns": len(by),
        "pns_more_than_once": len(items),
        "qty_in_repeats": repeat_qty,
        "usd_on_repeats": round(tot["same_cat"][1] + tot["diff_cat"][1], 2),
        "usd_exposure_all_rows": round(all_expo, 2),
        "same_category": {"pns": tot["same_cat"][0], "usd_on_repeats": round(tot["same_cat"][1], 2)},
        "diff_category": {"pns": tot["diff_cat"][0], "usd_on_repeats": round(tot["diff_cat"][1], 2)},
        "items": items,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default="Энергосети")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = measure(a.sheet)
    print(f"лист «{m['sheet']}»: строк {m['rows_on_sheet']}, номеров {m['distinct_pns']}, "
          f"из них более чем на одной строке {m['pns_more_than_once']}")
    print(f"  штук во вторых и далее вхождениях: {m['qty_in_repeats']}")
    print(f"  расчётной экспозиции на повторных вхождениях: {m['usd_on_repeats']:,.0f} USD "
          f"при {m['usd_exposure_all_rows']:,.0f} USD по всей заявке "
          f"({100 * m['usd_on_repeats'] / m['usd_exposure_all_rows']:.0f} %)".replace(",", " "))
    print(f"  категория одна (вероятно, одна позиция несколькими строками): "
          f"{m['same_category']['pns']} номеров на "
          f"{m['same_category']['usd_on_repeats']:,.0f} USD".replace(",", " "))
    print(f"  категории РАЗНЫЕ (номер не опознаёт изделие): {m['diff_category']['pns']} номеров "
          f"на {m['diff_category']['usd_on_repeats']:,.0f} USD".replace(",", " "))
    for it in [x for x in m["items"] if not x["one_category"]][:8]:
        print(f"    {str(it['pn']):16} {it['qty_by_row']} :: " +
              " | ".join(f"{c}" for c in it["categories"]))
    if a.write:
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": ("Сверка номеров листа заявки по нормализованному номеру. Считает "
                       "gt/tools/demand_collisions.py по gt/data/rfq_demand.json и "
                       "gt/data/ship_lukoil.json."),
            "what_it_is_not": ("Это НЕ список дублей и не повод сводить строки. Один номер на "
                               "нескольких строках может означать и одну позицию, расписанную "
                               "по машинам, и разные изделия с протянутым по столбцу номером. "
                               "Различить их может только заказчик; инструмент считает и "
                               "называет, но не решает."),
            "why_money": ("Сводка заявки складывает такие строки в одну позицию, поэтому её "
                          "количество — сумма, а не замер. Доля экспозиции, приходящаяся на "
                          "повторные вхождения, и есть цена вопроса."),
            **{k: v for k, v in m.items() if k != "items"},
            "items": m["items"],
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
