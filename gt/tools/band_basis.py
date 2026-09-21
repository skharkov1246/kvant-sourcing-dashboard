#!/usr/bin/env python3
"""На чём держится наша оценка заявки: доля денег по основанию вилки.

ЗАЧЕМ. Отчёт владельцу называет объём заявки числом — «оценка есть у 857 строк
на 9,28 млн долларов». Число читается как замер, но замер оно только на части
строк. У каждой записи разведки цен (gt/data/rfq_prices.json) есть поле
основания, и оно написано честно: где-то «retail: <продавец> $326.50», а
где-то прямо «экспертная вилка». Пока доля вторых не названа, отчёт
показывает уверенность, которой нет.

ЧТО СЧИТАЕТСЯ. Каждая запись разведки относится к одному из классов по тексту
её собственного основания, и класс взвешивается ДЕНЬГАМИ, а не числом строк:
одна строка на 720 тысяч важнее сорока строк по тысяче.

ЧЕГО ЗДЕСЬ НЕТ. Суждения о том, что «экспертная вилка» плоха. Она нормальна
как рабочая оценка на этапе подготовки; недопустимо другое — выдавать её за
найденную цену. Этот замер существует, чтобы рядом с итоговой суммой стояла
доля, которая под ней не подтверждена ничем, кроме мнения.

    python gt/tools/band_basis.py [--write]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRICES = ROOT / "gt/data/rfq_prices.json"
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_band_basis.json"

# Порядок важен: основание проверяется сверху вниз, первое совпадение и берётся.
# «Экспертная вилка» стоит первой намеренно: если запись назвала себя мнением,
# никакие другие слова в той же строке этого не отменяют.
CLASSES = [
    ("мнение: экспертная вилка", ("эксперт",),
     "Основание прямо называет себя оценкой специалиста. Найденной цены за ней нет."),
    ("перенос по аналогу или классу", ("аналог", "по классу", "класс ", "по типу", "типовая"),
     "Цена взята у похожего изделия или у класса изделий, а не у этой детали."),
    ("цена продавца: розница или дистрибьютор", ("retail", "дистрибьютор", "дилер",
                                                 "distributor", "розниц"),
     "За вилкой стоит цена с карточки продавца или дистрибьютора."),
    ("цена вторичного рынка", ("ebay", "вторичн", "брокер", "сток"),
     "За вилкой стоит цена торговой площадки или брокерского перечня."),
]
UNCLASSIFIED = ("основание записано, но не отнесено к классу", "")
NO_BASIS = ("основание не записано", "У записи разведки нет поля основания вовсе.")


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def classify(basis: str) -> tuple[str, str]:
    b = str(basis or "").lower()
    if not b.strip():
        return NO_BASIS
    for name, keys, what in CLASSES:
        if any(x in b for x in keys):
            return name, what
    return UNCLASSIFIED


def rekey(pn) -> str:
    """Номер перепроверки может нести пояснение в скобках — оно в ключ не идёт."""
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").split("(")[0].upper())


def measure() -> dict:
    prices = json.loads(PRICES.read_text(encoding="utf-8"))["prices"]
    rows = {key(r.get("pn")): r for r in json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]}
    # Строки, по которым перепроверка УЖЕ нашла цену числом. Это и есть та часть
    # мнения, которая перестала быть мнением: считать её отдельно обязательно,
    # иначе доля «не найденной ценой» будет описывать вчерашнее состояние.
    rv = ROOT / "gt/data/ship_reverify.json"
    found = set()
    if rv.exists():
        for r in json.loads(rv.read_text(encoding="utf-8"))["rows"]:
            v = r.get("price_low")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                found.add(rekey(r.get("pn")))
    by_basis: dict[str, list] = collections.defaultdict(lambda: [0, 0.0, "", 0, 0.0])
    by_conf: dict[str, list] = collections.defaultdict(lambda: [0, 0.0])
    closed_rows, closed_usd = 0, 0.0
    for p in prices:
        name, what = classify(p.get("basis"))
        row = rows.get(key(p.get("pn")))
        e = expo(row) if row else 0.0
        by_basis[name][0] += 1
        by_basis[name][1] += e
        by_basis[name][2] = what
        if key(p.get("pn")) in found:
            by_basis[name][3] += 1
            by_basis[name][4] += e
            if name.startswith(("мнение", "перенос")):
                closed_rows += 1
                closed_usd += e
        c = str(p.get("conf") or "не указана")
        by_conf[c][0] += 1
        by_conf[c][1] += e
    total = sum(v[1] for v in by_basis.values())
    opinion = by_basis.get("мнение: экспертная вилка", [0, 0.0, ""])[1]
    transfer = by_basis.get("перенос по аналогу или классу", [0, 0.0, ""])[1]
    return {
        "records": len(prices),
        "usd_exposure": round(total, 2),
        "usd_on_opinion": round(opinion, 2),
        "usd_on_transfer": round(transfer, 2),
        "share_not_a_found_price": round(100 * (opinion + transfer) / total, 1) if total else 0.0,
        "closed_by_reverify": {
            "records": closed_rows,
            "usd_exposure": round(closed_usd, 2),
            "what_it_means": ("Строки, где основанием было мнение или перенос по классу, а "
                              "перепроверка нашла цену числом. Столько мнения уже заменено "
                              "замером."),
        },
        "by_basis": {k: {"records": v[0], "usd_exposure": round(v[1], 2),
                         "what_it_means": v[2],
                         "records_with_found_price": v[3],
                         "usd_with_found_price": round(v[4], 2)}
                     for k, v in sorted(by_basis.items(), key=lambda x: -x[1][1])},
        "by_confidence": {k: {"records": v[0], "usd_exposure": round(v[1], 2)}
                          for k, v in sorted(by_conf.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = measure()
    print(f"записей разведки цен {m['records']}, экспозиция по ним "
          f"{m['usd_exposure']:,.0f} USD".replace(",", " "))
    for k, v in m["by_basis"].items():
        share = 100 * v["usd_exposure"] / m["usd_exposure"] if m["usd_exposure"] else 0
        print(f"  {v['records']:>4} строк | {v['usd_exposure']:>11,.0f} USD | {share:5.1f} % | {k}"
              .replace(",", " "))
    print(f"НЕ НАЙДЕННОЙ ЦЕНОЙ обосновано {m['share_not_a_found_price']} % денег заявки "
          f"({m['usd_on_opinion'] + m['usd_on_transfer']:,.0f} USD)".replace(",", " "))
    cl = m["closed_by_reverify"]
    print(f"из них перепроверка уже закрыла найденной ценой {cl['records']} строк на "
          f"{cl['usd_exposure']:,.0f} USD".replace(",", " "))
    for k, v in m["by_confidence"].items():
        print(f"  уверенность {k}: {v['records']:>4} строк на {v['usd_exposure']:>11,.0f} USD"
              .replace(",", " "))
    if a.write:
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": ("Классификация поля основания у записей разведки цен "
                       "gt/data/rfq_prices.json, взвешенная деньгами по gt/data/ship_lukoil.json. "
                       "Считает gt/tools/band_basis.py."),
            "why": ("Итоговая сумма заявки читается как замер, но замер она только на части "
                    "строк. Эта доля и считается здесь, чтобы она стояла рядом с суммой."),
            "what_it_is_not": ("Это НЕ упрёк разведке. Экспертная вилка нормальна как рабочая "
                               "оценка на этапе подготовки; недопустимо выдавать её за "
                               "найденную цену."),
            **m,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
