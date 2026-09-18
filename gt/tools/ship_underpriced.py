#!/usr/bin/env python3
"""Где наша выданная цена НИЖЕ найденной закупочной — и на сколько.

ЗАЧЕМ. Цены владельца заказчику твёрдые: он торгуется от них вниз. Значит
строка, где закупка дороже нашего потолка, — это не «уточнить», а прямой убыток
по этой позиции, и до отгрузки её надо либо перецени́ть, либо снять. Перепроверка
такие строки уже помечает вердиктом «ЗАНИЖЕНА», но денег на них никто не считал:
вердикт стоял строкой в таблице, а сколько он стоит — нигде.

ГЛАВНАЯ ЛОВУШКА, И ОНА СРАБОТАЛА НА МНЕ. Первый же подсчёт дал 2 782 352 USD
разрыва, и половину суммы принесли две строки: брокерский ask на половину
объёма (737 600) и цена единственной московской витрины перекупщика, у которой
два домена и один телефон (999 652). Правило владельца прямо запрещает так
считать: цена действует на тот остаток, который продавец подтвердил, и если
covers_qty не full — строка в сумму закупки не идёт. Поэтому здесь два разряда,
и складывать их нельзя:

  «в сумму идёт»              — покрытие полное, цена не брокерская, свидетель не один;
  «покрытие не подтверждено»  — цена годная, но продавец не подтвердил объём;
  «цена не закупочная»        — брокерский запрос или единственный оператор.

В сумму закупки идёт только первый разряд. Но главное число этого набора — не он,
а НАША экспозиция по этим строкам: сколько денег нашего же предложения стоит на
позициях, где единственная найденная цена выше нашего потолка. Это наше число,
без домножения чужого лота на весь объём, и решение принимается по нему: такую
строку до отгрузки перецени́ть или снять.

    python gt/tools/ship_underpriced.py            # пересчитать набор
    python gt/tools/ship_underpriced.py --print    # и показать таблицу
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verdicts import vkey  # noqa: E402

ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
INDEP = ROOT / "gt/data/ship_seller_independence.json"
SUBST = ROOT / "gt/data/ship_price_substitutions.json"
OUT = ROOT / "gt/data/ship_underpriced.json"
OFFERS = ROOT / "gt/data/ship_offer_stats.json"

#: Язык брокерской витрины — закрытым списком, из самого поля «вид цены».
#: Обвинять по глагольным формам нельзя (правило репозитория), поэтому только
#: существительные, которыми продавец сам себя называет.
BROKER = re.compile(r"брокер|перекупщ|вторичн|ask\b|маркетплейс|торговая площадка|ebay", re.I)


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def num(v):
    return v if isinstance(v, (int, float)) and v > 0 else None


def one_operator(indep: dict) -> set[str]:
    """Строки, где цена стоит на одном операторе под разными доменами."""
    out = set()
    for r in indep.get("rows") or []:
        if r.get("marked") and key(r.get("pn")):
            out.add(key(r["pn"]))
    return out


def withdrawn(subst: dict) -> set[str]:
    """Цены, уже снятые как подстановка по классу: в опору не идут."""
    return {key(i.get("pn")) for i in subst.get("items") or [] if i.get("pn")}


def tier(a: dict, x: dict, solo: bool) -> tuple[str, str]:
    """Разряд строки и причина. Порядок разбора — от самой тяжёлой оговорки.

    Сначала качество цены, потом покрытие: брокерский запрос остаётся
    брокерским и при полном покрытии, а «покрытие не подтверждено» — это
    оговорка к годной цене, и мешать её с негодной нельзя.
    """
    if BROKER.search(str(x.get("price_kind") or "")):
        return "цена не закупочная", "цена брокерская: это запрос продавца, а не закупочная цена"
    if solo:
        return "цена не закупочная", "цена стоит на одном операторе под разными доменами — свидетель один"
    if str(a.get("covers_qty") or "") != "full":
        return "покрытие не подтверждено", f"покрытие количества «{a.get('covers_qty') or 'не указано'}», а не полное"
    return "в сумму идёт", "покрытие полное, цена не брокерская, свидетель не один"


def build() -> dict:
    ask = {}
    for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]:
        k = key(r.get("pn"))
        if k:
            ask.setdefault(k, r)
    rv = json.loads(RV.read_text(encoding="utf-8"))["rows"]
    solo = one_operator(json.loads(INDEP.read_text(encoding="utf-8")))
    gone = withdrawn(json.loads(SUBST.read_text(encoding="utf-8")))

    rows, skipped = [], {"цена снята как подстановка": 0, "строки нет в сводке заявки": 0,
                         "нет числа по одну из сторон": 0}
    for x in rv:
        if vkey(x) != "ЗАНИЖЕНА":
            continue
        k = key(x.get("pn"))
        if k in gone:
            skipped["цена снята как подстановка"] += 1
            continue
        a = ask.get(k)
        if not a:
            skipped["строки нет в сводке заявки"] += 1
            continue
        hi, found, qty = num(a.get("usd_hi")), num(x.get("price_low")), num(a.get("qty"))
        if not hi or not found or not qty or found <= hi:
            skipped["нет числа по одну из сторон"] += 1
            continue
        t, why = tier(a, x, k in solo)
        lo = num(a.get("usd_lo")) or hi
        ours = round((lo + hi) / 2 * qty, 2)          # наша экспозиция: середина вилки × количество
        rows.append({
            "pn": x.get("pn"), "name": str(a.get("name") or "")[:120], "sheet": a.get("sheet"),
            "qty": qty, "our_hi": hi, "our_lo": num(a.get("usd_lo")),
            "found_unit": found, "found_kind": str(x.get("price_kind") or "")[:200],
            "covers_qty": a.get("covers_qty"), "tier": t, "why_tier": why,
            "gap_unit": round(found - hi, 2), "gap_total": round((found - hi) * qty, 2),
            "our_exposure": ours,
            "source": str(x.get("price_source") or "")[:300],
            "channel": str(x.get("channel") or "")[:200],
        })
    rows.sort(key=lambda r: -r["gap_total"])
    # ПИСЬМЕННОЕ ПРЕДЛОЖЕНИЕ — ДОВОД СИЛЬНЕЕ ВИТРИНЫ, и по нему заниженных
    # строк втрое больше. Этот набор считает только те, где цену нашла
    # перепроверка в открытом доступе; счётчики прогона по вложениям сделок
    # говорят, что предложение поставщика выше нашего потолка по 253 строкам.
    # Суммы по ним здесь нет и быть не может: цены из вложений остаются в
    # артефакте прогона, репозиторий публичный. Без этой ссылки два числа об
    # одном предмете читались бы как противоречие.
    offers = {}
    if OFFERS.exists():
        od = json.loads(OFFERS.read_text(encoding="utf-8"))
        for name, sc in (od.get("scopes") or {}).items():
            offers[name] = {"rows_with_written_offer": sc.get("rows_with_offer"),
                            "offer_above_our_ceiling": sc.get("above_ceiling"),
                            "offer_below_our_floor": sc.get("below_floor"),
                            "offer_inside_our_band": sc.get("inside_band")}
            if sc.get("stale_warning"):
                offers[name]["stale_warning"] = sc["stale_warning"]
            if sc.get("price_grades"):
                offers[name]["price_grades"] = sc["price_grades"]
    tiers = {}
    for t in ("в сумму идёт", "покрытие не подтверждено", "цена не закупочная"):
        part = [r for r in rows if r["tier"] == t]
        tiers[t] = {"rows": len(part),
                    "gap_usd": round(sum(r["gap_total"] for r in part), 2),
                    "our_exposure_usd": round(sum(r["our_exposure"] for r in part), 2)}
    return {
        "updated": date.today().isoformat(),
        "source": "Вердикты «ЗАНИЖЕНА» перепроверки (gt/data/ship_reverify.json) против нашей "
                  "вилки в сводке заявки (gt/data/ship_lukoil.json). Считает "
                  "gt/tools/ship_underpriced.py.",
        "what_it_is": "Разрыв «найденная закупочная цена за штуку минус наш потолок» × количество. "
                      "Цены владельца заказчику твёрдые, торг идёт вниз, поэтому такая строка — "
                      "убыток по позиции, а не повод уточнить.",
        "what_it_is_not": "НЕ смета закупки. Разряды складывать нельзя: в закупочную сумму идёт "
                          "только «в сумму идёт». Разрыв по двум остальным — верхняя граница при "
                          "допущении, что весь объём купится по найденной цене, а её никто не "
                          "подтверждал.",
        "headline": "Решение принимается по нашей экспозиции: сколько денег НАШЕГО предложения "
                    "стоит на строках, где единственная найденная цена выше нашего потолка.",
        "our_exposure_usd": round(sum(r["our_exposure"] for r in rows), 2),
        "written_offers": {
            "why_it_matters": "Письменное предложение поставщика из вложения сделки — довод "
                              "сильнее карточки витрины: продавец адресовал его нам.",
            "correction_18_09": "Первая редакция этого поля говорила «заниженных по письменным "
                                "предложениям втрое больше» — 222 строки по «Энергосети». Это "
                                "было неверно: ценой считалось и значение, взятое правилом "
                                "«последнее число строки», а оно брало НОМЕРА ПОЗИЦИЙ (в "
                                "Quotation p76057.pdf: 94, 101, 104, 105, 107 подряд; 187 пар "
                                "из 474 идут с шагом ровно 1) и количество из файлов-заявок. "
                                "После разделения по градусу: 104 строки с предложением и 33 "
                                "выше потолка. Из 6 619 значений выгрузки по колонке «цена» "
                                "взято 317.",
            "why_no_money_here": "Суммы по ним в этом наборе нет и быть не может: цены из "
                                 "вложений остаются в артефакте прогона, репозиторий "
                                 "публичный. Считает их прогон «Bitrix входящие КП» "
                                 "документом «Свод выставленных цен с рынком».",
            "by_scope": offers,
            "note": "Охваты не складываются: это разные заявки.",
        },
        "tiers": tiers, "skipped": skipped, "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="показать таблицу")
    a = ap.parse_args()
    doc = build()
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    t = doc["tiers"]
    print(f"строк с вердиктом «ЗАНИЖЕНА» и числами по обе стороны: {len(doc['rows'])}")
    # пробел-разделитель ставим ТОЛЬКО в числе: прошлая версия заменяла запятые
    # и в самой фразе, и предложение рассыпалось на «строках  где»
    money = f"{doc['our_exposure_usd']:,.0f}".replace(",", " ")
    print(f"наша экспозиция по ним: {money} USD — столько денег нашего предложения стоит "
          f"на строках, где найденная цена выше нашего потолка")
    for name, v in t.items():
        ours = f"{v['our_exposure_usd']:,.0f}".replace(",", " ")
        gap = f"{v['gap_usd']:,.0f}".replace(",", " ")
        print(f"  {name:<26} {v['rows']:3d} строк · наша экспозиция {ours} USD "
              f"· верхняя граница разрыва {gap} USD")
    print("отсеяно:", ", ".join(f"{k} — {v}" for k, v in doc["skipped"].items() if v) or "ничего")
    if a.print:
        print()
        print(f"{'разрыв USD':>12} | {'крат':>5} | {'потолок':>9} | {'найдено':>10} | {'кол':>4} | разряд · номер")
        for r in doc["rows"][:40]:
            print(f"{r['gap_total']:12,.0f} | {r['found_unit']/r['our_hi']:4.1f}× | "
                  f"{r['our_hi']:9,.2f} | {r['found_unit']:10,.2f} | {r['qty']:4} | "
                  f"{r['tier'][:12]:<12} {str(r['pn'])[:20]}".replace(",", " "))
    print(f"\n{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
