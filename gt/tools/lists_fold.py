#!/usr/bin/env python3
"""Открытые перечни ЗИП против номеров заявки: опознание оптом, а не по строке.

ЗАЧЕМ. Номер, по которому продавец ничего не находит, выглядит как «детали не
существует». Но такие номера ходят СПИСКАМИ: у торговцев запасными частями,
у ремонтных предприятий, в ведомостях ЗИП. Один снятый перечень закрывает
десятки строк заявки за проход, и закрывает он самое дорогое — ОПОЗНАНИЕ:
дословное наименование изделия и держателя номенклатуры.

ЧТО ЭТОТ НАБОР ДАЁТ И ЧЕГО НЕ ДАЁТ. Даёт: наименование по номеру, адрес
источника, число независимых перечней, подтвердивших номер. Не даёт: цену.
Перечни такого рода цену не печатают — вместо неё стоит «запросить». Поэтому
ни одна строка отсюда не становится ценовым вердиктом: она переводит строку из
состояния «номер не найден» в состояние «изделие опознано, цену спрашивать у
названного держателя».

ДВА ПЕРЕЧНЯ ОДНОГО ВЛАДЕЛЬЦА — ОДИН СВИДЕТЕЛЬ. Число подтверждений считается
по РАЗНЫМ источникам, а не по разным страницам: у одного держателя может быть
и страница каталога, и файл перечня, и это одно и то же свидетельство.

ПРИЗНАК НАЛИЧИЯ ЗДЕСЬ НЕ ОСТАТОК. «in stock» в таком перечне означает «у нас
это бывает», а не подтверждённое число на наш объём. В сумму закупки не идёт.

    python gt/tools/lists_fold.py <выдача.json> --machine Solar [--write]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_parts_lists.json"


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def host(url: str) -> str:
    """Держатель перечня — по домену второго уровня: разные страницы одного
    сайта не образуют двух независимых свидетельств."""
    h = (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def build(src: dict, machine: str) -> dict:
    rows = {key(r.get("pn")): r for r in json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]}
    by_pn: dict[str, dict] = {}
    for m in src.get("matches") or []:
        k = key(m.get("pn_ask"))
        row = rows.get(k)
        if row is None:
            continue
        rec = by_pn.setdefault(k, {
            "pn": row.get("pn"),
            "name_in_ask": str(row.get("name") or ""),
            "qty": row.get("qty"),
            "usd_exposure": round(expo(row), 2),
            "verdict_before": row.get("verdict"),
            "descriptions": [],
            "sources": [],
        })
        d = str(m.get("desc") or "").strip()
        if d and d not in rec["descriptions"]:
            rec["descriptions"].append(d)
        h = host(m.get("url"))
        if h and h not in rec["sources"]:
            rec["sources"].append(h)
        if str(m.get("stock") or "").strip():
            rec["stock_claim"] = str(m["stock"]).strip()
    items = sorted(by_pn.values(), key=lambda x: (-len(x["sources"]), -x["usd_exposure"]))
    for it in items:
        it["independent_sources"] = len(it["sources"])
    was_lost = [x for x in items if x["verdict_before"] == "pn_not_found"]
    two_plus = [x for x in items if x["independent_sources"] >= 2]
    return {
        "machine": machine,
        "lists": src.get("lists") or [],
        "pns_matched": len(items),
        "pns_confirmed_by_two_or_more": len(two_plus),
        "pns_that_were_not_found": len(was_lost),
        "usd_exposure_matched": round(sum(x["usd_exposure"] for x in items), 2),
        "usd_exposure_that_was_not_found": round(sum(x["usd_exposure"] for x in was_lost), 2),
        "by_source": dict(collections.Counter(s for x in items for s in x["sources"])),
        "not_found_in_lists": src.get("not_found"),
        "notes": src.get("notes", ""),
        "items": items,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--machine", required=True)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    src = json.loads(Path(a.src).read_text(encoding="utf-8"))
    m = build(src, a.machine)
    print(f"{a.machine}: перечней {len(m['lists'])}, номеров заявки закрыто {m['pns_matched']} "
          f"на {m['usd_exposure_matched']:,.0f} USD".replace(",", " "))
    print(f"  подтверждены двумя и более независимыми держателями: "
          f"{m['pns_confirmed_by_two_or_more']}")
    print(f"  было «номер не найден»: {m['pns_that_were_not_found']} строк на "
          f"{m['usd_exposure_that_was_not_found']:,.0f} USD — теперь у них есть дословное "
          f"наименование и адрес источника".replace(",", " "))
    for h, n in sorted(m["by_source"].items(), key=lambda x: -x[1]):
        print(f"    {n:>4} номеров | {h}")
    if a.write:
        prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
        machines = prev.get("machines") or {}
        machines[a.machine] = m
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": ("Открытые перечни запасных частей, снятые целиком и сверенные с номерами "
                       "заявки. Считает gt/tools/lists_fold.py, по одной машине за прогон, с "
                       "слиянием по машине."),
            "what_it_gives": ("Опознание: дословное наименование изделия по номеру, адрес "
                              "источника и число НЕЗАВИСИМЫХ держателей, подтвердивших номер."),
            "what_it_does_not_give": ("Цену. Перечни такого рода цену не печатают — вместо неё "
                                      "стоит «запросить». Поэтому ни одна строка отсюда не "
                                      "становится ценовым вердиктом. И признак наличия здесь не "
                                      "остаток: «in stock» означает «у нас это бывает», а не "
                                      "подтверждённое число на наш объём; в сумму закупки не "
                                      "идёт. Замеры по разным машинам НЕ СКЛАДЫВАЮТСЯ: один "
                                      "номер может стоять в перечнях по нескольким машинам."),
            "machines": machines,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
