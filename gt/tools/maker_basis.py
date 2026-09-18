#!/usr/bin/env python3
"""На чём стоит «изготовитель» в базе номеров — на документе или на догадке по классу.

Звено «исполнитель» в цепочке отвечает на вопрос «у кого спрашивать». Ответ
стоит ровно столько, сколько стоит его основание, и основания у нас разные:
таможенная декларация — это факт, а «сегмент фильтров» — предположение по
классу изделия.

ПОВОД ДЛЯ ЗАМЕРА. 18.09.2026 разведка по двум строкам открыла каталоги
изготовителей и опровергла нашу же разметку дважды:
  · масляному фильтру Fleetguard были приписаны «Pall/Donaldson/Parker/
    PECOFacet (по типу)» с основанием «сегмент фильтров» — на деле Fleetguard
    (Cummins Filtration), кросс изготовителя напечатан на карточке;
  · наконечнику свечи Jenbacher был приписан «Champion Aerospace (тип.)» с
    основанием «OE-поставщик зажигания Solar» — на деле Jenbacher/INNIO, и это
    вообще не турбина.
Оба раза механизм один и тот же, которым VS-4-57 стал виброизолятором:
изготовитель выведен из КЛАССА изделия, а не из документа.

Инструмент считает, сколько таких строк и сколько на них денег НАШЕЙ заявки.
Он ничего не исправляет: исправление — это открытый каталог по каждой строке.

    python gt/tools/maker_basis.py            # замер
    python gt/tools/maker_basis.py --write    # записать набор
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "gt/data/pn_db.json"
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_maker_basis.json"

# Классы основания, от сильного к слабому. Порядок важен: строка получает
# первый подошедший класс, поэтому документ всегда побеждает догадку.
BASIS = [
    ("документ", r"таможенн\w+ деклараци|открытый стандарт|паспорт|сертификат",
     "декларация, открытый стандарт или паспорт — это факт, а не вывод"),
    ("заказной код", r"заказной код|это и есть заказной",
     "номер сам по себе является кодом изготовителя: проверять нечего"),
    ("назван заказчиком", r"назван в позиции заказчика|в позиции заказчика",
     "изготовитель взят из наименования заявки — это слова заказчика, а не каталог"),
    ("чертёж или узел пакета", r"чертёжная деталь|покупной узел|оснастка, не деталь",
     "принадлежность к чертежу или к пакету поставки; изготовитель узла может быть чужим"),
    ("тренировочная выборка", r"тренировочная выборка",
     "разметка для обучения правила, а не проверенный факт — сверять с первичным документом"),
    ("догадка по классу", r"сегмент |типовой субпоставщик|OE-поставщик|коммерческая химия"
     r"|П4: электрика|узел насоса, OEM-канал|по типу|\(тип\.\)",
     "изготовитель выведен из КЛАССА изделия. Это тот же приём, которым VS-4-57 стал "
     "виброизолятором; за 18.09.2026 опровергнут дважды открытыми каталогами"),
    ("спецификация частичная", r"спецификация частичная",
     "изделие не доопределено — изготовителя называть ещё не на чем"),
]


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").upper())


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def basis_of(row: dict) -> str:
    text = f"{row.get('ev') or ''} {row.get('mk') or ''}"
    for name, pat, _ in BASIS:
        if re.search(pat, text, re.I):
            return name
    return "основание не записано"


def expo(r: dict) -> float:
    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    if lo in (None, "") or hi in (None, ""):
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(r.get("qty") or 0)


def measure() -> dict:
    rows = (load(DB) or {}).get("rows") or []
    lk = (load(SUMMARY) or {}).get("rows") or []
    band = {key(r.get("pn")): r for r in lk}

    named = [r for r in rows if str(r.get("mk") or "").strip()]
    by = collections.Counter(basis_of(r) for r in named)
    usd = collections.Counter()
    hit = collections.Counter()
    for r in named:
        b = basis_of(r)
        row = band.get(key(r.get("pn")))
        if row is None:
            continue
        hit[b] += 1
        usd[b] += expo(row)
    classes = []
    for name, _, why in BASIS + [("основание не записано", "", "поле основания пустое")]:
        if not by.get(name) and not hit.get(name):
            continue
        classes.append({"basis": name, "rows_in_db": by.get(name, 0),
                        "rows_in_request": hit.get(name, 0),
                        "usd_in_request": round(usd.get(name, 0.0), 2), "why": why})
    weak = next((c for c in classes if c["basis"] == "догадка по классу"), {})
    return {
        "db_rows": len(rows),
        "maker_named": len(named),
        "request_rows_with_band": sum(1 for r in lk if expo(r) > 0),
        "request_exposure": round(sum(map(expo, lk)), 2),
        "weak_rows_in_request": weak.get("rows_in_request", 0),
        "weak_usd_in_request": weak.get("usd_in_request", 0.0),
        "classes": classes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    if not DB.exists():
        print("нет базы номеров", file=sys.stderr)
        return 1
    m = measure()
    print(f"в базе номеров {m['db_rows']}, изготовитель назван у {m['maker_named']}")
    for c in m["classes"]:
        print(f"  {c['basis']:<24} в базе {c['rows_in_db']:>5} | в заявке "
              f"{c['rows_in_request']:>4} | {c['usd_in_request']:>12,.0f} USD".replace(",", " "))
    share = (100 * m["weak_usd_in_request"] / m["request_exposure"]
             if m["request_exposure"] else 0)
    print(f"догадка по классу держит {m['weak_usd_in_request']:,.0f} USD экспозиции заявки "
          f"({share:.1f} %)".replace(",", " "))
    if a.write:
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": "На чём стоит «изготовитель» в gt/data/pn_db.json: на документе или на "
                      "догадке по классу изделия. Считает gt/tools/maker_basis.py.",
            "method": "Класс основания определяется по полям ev и mk строки базы, от сильного к "
                      "слабому: первый подошедший класс и записывается, поэтому документ всегда "
                      "побеждает догадку. Деньги считаются по пересечению с заявкой "
                      "(gt/data/ship_lukoil.json) — середина нашей вилки на количество.",
            "why": "Звено «исполнитель» отвечает на вопрос «у кого спрашивать», и ответ стоит "
                   "ровно столько, сколько его основание. 18.09.2026 разведка открыла каталоги "
                   "изготовителей и опровергла нашу разметку дважды: масляный фильтр Fleetguard "
                   "был приписан четырём фильтровым домам «по типу», а наконечник свечи "
                   "Jenbacher — авиационному поставщику зажигания «типично». Оба раза "
                   "изготовитель был выведен из КЛАССА изделия, а не из документа.",
            "totals": {k: v for k, v in m.items() if k != "classes"},
            "classes": m["classes"],
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
