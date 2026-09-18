#!/usr/bin/env python3
"""Задание на снятие СПОРНОГО остатка: открыть названную страницу и решить.

ЗАЧЕМ ОТДЕЛЬНОЕ ЗАДАНИЕ. После исправления признака «остаток числом» (18.09.2026)
появился третий разряд: строки, где в поле остатка стоит И прямой отказ, И
названное количество, потому что продавцов несколько. Разряд двигается БЕЗ
поиска: искать ничего не надо, продавец и страница уже названы, надо открыть её и
решить одно — остаток это или срок поставки.

Счёт на 18.09.2026: разряд начинался с 197 строк на 2 757 775 USD, после двух
уточнений признака сузился до 110 на 653 956, шесть проходов сняли 60 самых
дорогих, осталось 81 на 218 044 USD. Текущее число печатает сам инструмент —
здесь оно приведено, чтобы был виден порядок работы, а не как справка.

ЧЕМ ОТЛИЧАЕТСЯ ОТ ДРУГИХ ЗАДАНИЙ. rv_brief выдаёт неразобранные строки и просит
разобрать с нуля. rv_pricehunt выдаёт разобранные и просит найти цену. Здесь
опознание и цена уже есть, вопрос ровно один: подтверждается ли остаток числом
на сегодня и покрывает ли он наше количество.

ПОЧЕМУ ЭТО ВАЖНО ПО ДЕНЬГАМ. Ступень «отгружаема» — единственная, по которой
решают, можно ли везти. Пока остаток спорный, строка в неё не идёт и висит между
«есть цена и адрес» и «отгружаема».

Как читается остаток и семь ловушек подмены — docs/ПРАВИЛА-ОСТАТКА.md.

    python gt/tools/stock_brief.py --top 20
    python gt/tools/stock_brief.py --top 10 --skip 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ship_coverage import stock_state  # noqa: E402


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def rows() -> list[tuple[float, dict, dict]]:
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r.get("pn")): r for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]}
    out = []
    for a in ask:
        x = rv.get(key(a.get("pn")))
        if not x:
            continue
        if stock_state(x.get("stock")) != "спорно":
            continue
        out.append((expo(a), a, x))
    out.sort(key=lambda t: -t[0])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--skip", type=int, default=0)
    a = ap.parse_args()
    all_rows = rows()
    total = sum(e for e, _, _ in all_rows)
    print(f"СПОРНЫЙ ОСТАТОК ВСЕГО: {len(all_rows)} строк на {total:,.0f} USD"
          .replace(",", " "))
    part = all_rows[a.skip:a.skip + a.top]
    print(f"СТРОКИ ЗАДАНИЯ ({len(part)}). Всё ниже напечатано из наборов репозитория "
          f"инструментом gt/tools/stock_brief.py и НЕ переписано руками. Вопрос по каждой "
          f"строке РОВНО ОДИН: подтверждается ли остаток числом на сегодня и покрывает ли "
          f"он наше количество. Опознание и цену не переоткрывай — они уже сделаны.\n")
    for i, (e, r, x) in enumerate(part, a.skip + 1):
        print(f"{i}. НОМЕР (посимвольно как в заявке): {r.get('pn')}")
        print(f"   наименование в заявке дословно: {r.get('name')}")
        print(f"   НУЖНО количество: {r.get('qty')} шт")
        print(f"   наша вилка: {r.get('usd_lo')}–{r.get('usd_hi')} USD за штуку   "
              f"экспозиция: {e:,.0f} USD".replace(",", " "))
        print(f"   найденная цена: {x.get('price_low')}–{x.get('price_high')} USD "
              f"({str(x.get('price_kind') or '')[:90]})")
        print(f"   источник цены: {str(x.get('price_source') or '')[:300]}")
        print(f"   ЧТО ЗАПИСАНО ПРО ОСТАТОК (в этом и спор): {str(x.get('stock') or '')[:700]}")
        print(f"   срок: {str(x.get('lead_time') or '')[:200]}")
        print(f"   канал: {str(x.get('channel') or '')[:400]}")
        print(f"   адреса и контакты: {str(x.get('contacts') or '')[:400]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
