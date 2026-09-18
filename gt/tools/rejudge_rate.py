#!/usr/bin/env python3
"""Сколько наших отказов не выдерживает пересуда. Главное число о доверии.

ЗАЧЕМ. Отказ «цены нет ни у кого» читается как измерение. Он им является только
если пересуд по нынешнему стандарту его подтверждает. Пока доля опровержений
неизвестна, неизвестна и цена всех остальных отказов в отчёте.

ОТКУДА БЕРЁТСЯ ПРЕЖНИЙ ВЕРДИКТ. Приёмник набора (gt/tools/rv_merge.py в режиме
--update) записывает его в пояснение строки дословно: «Было по цене: «…»». Это
единственное место, где прошлое значение сохраняется, и замер читает его оттуда,
а не из памяти.

ДВА РАЗНЫХ СОБЫТИЯ, И ПУТАТЬ ИХ НЕЛЬЗЯ.
 * ОПРОВЕРЖЕНИЕ: отказ сменился ценовым вердиктом — цена нашлась, прежний вывод
   был неверен. Это стоит денег.
 * ПЕРЕКЛАССИФИКАЦИЯ: один вид отказа сменился другим («нечем проверить» на «не
   подтверждена»). Вывод тот же, уточнилось основание. Денег не стоит.
 * ЦИФРА ПОЯВИЛАСЬ ПРИ ТОМ ЖЕ ВЕРДИКТЕ: цена прочитана и записана, а вердикт
   остался «нечем проверить», потому что вилки по строке в заявке нет вовсе и
   сравнивать не с чем. Это ЦЕННЕЕ переклассификации и НЕ является
   опровержением: прежний вывод был верен, но бесполезен — теперь у строки есть
   число для запроса и для будущей вилки.
Складывать их в одну долю — приукрашивать работу пересуда вдвое. Не считать
третью категорию вовсе — обесценивать её: 18.09.2026 двадцать четыре строки
получили цифру, не меняя вердикта, и по замеру пересуд выглядел бесплодным.

    python gt/tools/rejudge_rate.py [--write]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verdicts import PRICED, vkey  # noqa: E402

SRC = ROOT / "gt/data/ship_reverify.json"
ASK = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_rejudge_rate.json"

WAS = re.compile(r"Было по цене: «([^»]{0,240})")
NEGATIVE = {"НЕ ПОДТВЕРЖДЕНА", "НЕЧЕМ ПРОВЕРИТЬ"}
NAMES = ("НЕ ПОДТВЕРЖДЕНА", "НЕЧЕМ ПРОВЕРИТЬ", "ЗАНИЖЕНА", "ЗАВЫШЕНА", "ВЕРНА", "ДУБЛЬ")


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def head(text: str) -> str:
    """Вердикт по НАЧАЛУ текста — тем же правилом, что и основной классификатор."""
    t = (text or "").lstrip()
    for n in NAMES:
        if t.startswith(n):
            return n
    return ""


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def measure() -> dict:
    rows = json.loads(SRC.read_text(encoding="utf-8"))["rows"]
    ask = {key(r.get("pn")): r for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]}

    seen, over, recl, same, gained = [], [], [], [], []
    moves: collections.Counter = collections.Counter()
    for r in rows:
        m = WAS.search(str(r.get("note") or ""))
        if not m:
            continue
        was, now = head(m.group(1)), vkey(r)
        if not was:
            continue
        money = expo(ask.get(key(r.get("pn")), {}))
        has_price = isinstance(r.get("price_low"), (int, float))
        item = {"pn": r.get("pn"), "was": was, "now": now,
                "usd_exposure": round(money, 2), "price_found": has_price}
        seen.append(item)
        if was == now:
            (gained if has_price else same).append(item)
            continue
        moves[f"{was} → {now}"] += 1
        if was in NEGATIVE and now in PRICED:
            over.append(item)
        else:
            recl.append(item)

    def usd(items):
        return round(sum(x["usd_exposure"] for x in items), 2)

    n = len(seen) or 1
    return {
        "updated": "2026-09-18",
        "source": ("Прежний вердикт читается из пояснения строки, куда его записывает "
                   "gt/tools/rv_merge.py в режиме --update. Считает "
                   "gt/tools/rejudge_rate.py."),
        "why": ("Отказ «цены нет ни у кого» читается как измерение. Он им является только "
                "если пересуд по нынешнему стандарту его подтверждает. Пока доля "
                "опровержений неизвестна, неизвестна и цена всех остальных отказов."),
        "rejudged": len(seen),
        "overturned": len(over),
        "overturned_pct": round(len(over) / n * 100, 1),
        "overturned_usd": usd(over),
        "reclassified": len(recl),
        "reclassified_pct": round(len(recl) / n * 100, 1),
        "unchanged": len(same),
        "price_gained_same_verdict": len(gained),
        "price_gained_pct": round(len(gained) / n * 100, 1),
        "what_price_gained_means": (
            "Вердикт остался тем же, а цена прочитана и записана. Так бывает, когда вилки "
            "по строке в заявке нет вовсе: сравнивать не с чем, поэтому ценового вердикта "
            "быть не может, но число для запроса и для будущей вилки теперь есть. "
            "Опровержением это не является — прежний вывод был верен, но бесполезен."),
        "moves": dict(moves.most_common()),
        "what_counts_as_overturned": (
            "Отказ сменился ценовым вердиктом: цена нашлась, прежний вывод был неверен. "
            "Смена одного вида отказа другим («нечем проверить» на «не подтверждена») "
            "опровержением НЕ считается — вывод тот же, уточнилось основание. Складывать "
            "их в одну долю значит приукрашивать работу пересуда вдвое."),
        "rows": sorted(over, key=lambda x: -x["usd_exposure"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = measure()
    print(f"пересужено строк: {m['rejudged']}")
    print(f"  ОПРОВЕРГНУТО (отказ → цена): {m['overturned']} = {m['overturned_pct']} % "
          f"на {m['overturned_usd']:,.0f} USD".replace(",", " "))
    print(f"  переклассифицировано:        {m['reclassified']} = {m['reclassified_pct']} % "
          f"(один вид отказа сменился другим, вывод тот же)")
    print(f"  ЦИФРА ПОЯВИЛАСЬ при том же вердикте: {m['price_gained_same_verdict']} = "
          f"{m['price_gained_pct']} % (вилки в заявке нет, сравнивать не с чем, "
          f"но число есть)")
    print(f"  без изменений:               {m['unchanged']}")
    for move, cnt in m["moves"].items():
        print(f"      {cnt:>3} | {move}")
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
