#!/usr/bin/env python3
"""Задание на добор ЦЕНЫ по строкам, где канал уже известен, а цены нет.

ЗАЧЕМ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ. Перепроверка закрыла опознание: по 489 строкам
известно, что это за изделие и кому писать. Но у 137 из них цены нет, хотя
канал НЕ квотируемый — то есть цена в принципе публикуется, её просто не нашли.
Это 1,9 млн USD, и это самая дорогая незакрытая работа: опознавать заново
ничего не надо, надо найти цифру.

ЧЕМ ОТЛИЧАЕТСЯ ОТ rv_brief.py. Тот выдаёт строки, которых перепроверка ещё не
касалась, и просит разобрать их с нуля. Этот выдаёт УЖЕ РАЗОБРАННЫЕ строки и
отдаёт разведчику всё, что про них известно: изделие, изготовителя, канал,
адреса, что именно помешало прошлому проходу. Задача сужена до одной: цена.

ПОЧЕМУ ЗАДАНИЕ ПЕЧАТАЕТСЯ ИЗ ДАННЫХ. 18.09.2026 вилки в задания вписывались
руками, и одиннадцать из двенадцати оказались неверными (40–69 против
настоящих 14–95 и так далее). Разведчик сверяет найденное с вилкой, поэтому
неверная вилка в задании — это прямой путь к неверному вердикту. Здесь ни одно
число не набирается руками.

    python gt/tools/rv_pricehunt.py --top 12
    python gt/tools/rv_pricehunt.py --top 12 --skip 12
    python gt/tools/rv_pricehunt.py --sheet Энергосети --top 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ship_coverage import quote_only  # noqa: E402

ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
QUESTIONS = ROOT / "gt/data/ship_questions.json"


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def candidates(ask: list, rv: dict) -> list:
    """Строка годится, если канал назван, цены нет и канал не квотируемый.

    Квотируемый канал исключён намеренно: там цену не публикуют в принципе, и
    посылать туда разведку — значит списать её труд в ноль. Такие строки
    закрываются письмом, а не поиском.
    """
    out = []
    for r in ask:
        x = rv.get(key(r.get("pn")))
        if not x or not str(x.get("channel") or "").strip():
            continue
        if isinstance(x.get("price_low"), (int, float)) or quote_only(x):
            continue
        out.append((expo(r), r, x))
    out.sort(key=lambda t: -t[0])
    return out


def asked_keys() -> set:
    """Номера, по которым уже стоит вопрос заказчику.

    Такую строку искать бесполезно: пока заказчик не назвал фасовку, исполнение
    или настоящий номер, продавец вернёт вопрос, а не цену. Самый дорогой
    пример — VS-4-57 на 138 000 USD: под одним обозначением в заявке идут два
    разных изделия, и это обозначение МОДЕЛИ котла, а не номер детали.
    """
    if not QUESTIONS.exists():
        return set()
    out = set()
    for q in json.loads(QUESTIONS.read_text(encoding="utf-8")).get("questions", []):
        for part in re.split(r"[,/·]| и ", str(q.get("pn") or "")):
            k = key(part)
            if len(k) >= 4:
                out.add(k)
    return out


def brief(n: int, e: float, r: dict, x: dict) -> str:
    def f(src: dict, name: str) -> str:
        return str(src.get(name) or "").strip()

    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    band = f"{lo}–{hi} USD за штуку" if lo not in (None, "") else "нашей оценки нет"
    lines = [
        f"{n}. НОМЕР (посимвольно как в заявке): {r.get('pn')}",
        f"   наименование в заявке дословно: {f(r, 'name')}",
        f"   количество: {r.get('qty')} {f(r, 'unit') or 'шт'}",
        f"   наша вилка: {band}   экспозиция по середине: {e:,.0f} USD".replace(",", " "),
        f"   лист: {f(r, 'sheet')}   категория: {f(r, 'category')}",
        "",
        "   ЧТО ПО ЭТОЙ СТРОКЕ УЖЕ УСТАНОВЛЕНО ПЕРЕПРОВЕРКОЙ — не переоткрывай:",
        f"   изделие: {f(x, 'what_it_is')}",
        f"   изготовитель: {f(x, 'real_maker')}",
        f"   номер изготовителя: {f(x, 'real_pn')}",
        f"   состояние позиции: {f(x, 'lifecycle')}",
        f"   канал: {f(x, 'channel')}",
        f"   адреса: {f(x, 'contacts')}",
        f"   что помешало найти цену: {f(x, 'blocker')}",
        f"   вердикт по вилке: {f(x, 'band_verdict')}",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--sheet", default="")
    ap.add_argument("--all", action="store_true",
                    help="не отсеивать строки, по которым уже стоит вопрос заказчику")
    a = ap.parse_args()

    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r["pn"]): r for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]}
    sel = candidates(ask, rv)
    if a.sheet:
        sel = [t for t in sel if str(t[1].get("sheet") or "") == a.sheet]
    print(f"ВСЕГО строк «канал есть, цены нет, канал не квотируемый»: {len(sel)} "
          f"на {sum(t[0] for t in sel):,.0f} USD".replace(",", " "), file=sys.stderr)
    if not a.all:
        asked = asked_keys()
        blocked = [t for t in sel if key(t[1].get("pn")) in asked]
        sel = [t for t in sel if key(t[1].get("pn")) not in asked]
        print(f"  из них отсеяно как ждущие ответа заказчика: {len(blocked)} на "
              f"{sum(t[0] for t in blocked):,.0f} USD — пока заказчик не ответил, "
              f"продавец вернёт вопрос, а не цену".replace(",", " "), file=sys.stderr)
        print(f"  остаётся к добору цены: {len(sel)} на "
              f"{sum(t[0] for t in sel):,.0f} USD".replace(",", " "), file=sys.stderr)

    take = sel[a.skip:a.skip + a.top]
    print(f"СТРОКИ ЗАЯВКИ ({len(take)}). Всё ниже напечатано из "
          f"gt/data/ship_lukoil.json и gt/data/ship_reverify.json инструментом "
          f"gt/tools/rv_pricehunt.py и НЕ переписано руками. Если найденное "
          f"противоречит написанному здесь — так и напиши в разборе, не подгоняй.\n")
    for i, (e, r, x) in enumerate(take, start=a.skip + 1):
        print(brief(i, e, r, x))
        print()


if __name__ == "__main__":
    main()
