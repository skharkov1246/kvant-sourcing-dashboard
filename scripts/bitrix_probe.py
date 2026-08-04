"""CI-зонд v3: прогон НОВОГО contracts.compute() на живых данных (валидация до мержа).

Запускается ТОЛЬКО вручную (workflow_dispatch, .github/workflows/probe.yml).
Печатает сводку: вселенная кат.0 (до №917), источники закупки, тайминги, замедленные.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import contracts
from bitrix_client import BitrixClient


def hdr(t):
    print(f"\n{'='*12} {t} {'='*12}")


def main() -> int:
    s = config.Settings.load()
    c = BitrixClient(s.bitrix_webhook_url)
    t0 = dt.datetime.now()
    X = contracts.compute(c)
    print(f"compute() за {(dt.datetime.now()-t0).total_seconds():.0f} сек")

    rows = X["rows"]
    hdr("Вселенная")
    print(f"строк: {len(rows)} | max seq: {max((r['seq'] for r in rows), default=0)}")
    print(f"открытых: {sum(1 for r in rows if not r['closed'])} | закрытых: {sum(1 for r in rows if r['closed'])} "
          f"| вне кат.0: {sum(1 for r in rows if not r['inCat0'])} | без номера: {sum(1 for r in rows if not r['seq'])}")

    hdr("KPI")
    for k in X["kpis"]:
        print(f"  {k['lbl']}: {k['val']}  ({k['meta']})")

    hdr("Закупка: источники")
    from collections import Counter
    print(Counter(r["buySrc"] for r in rows))
    print(f"расхождение заказы≠экономика >20%: {sum(1 for r in rows if r['discrep'])}")

    hdr("Стадии сделок кат.0 (распределение открытых)")
    stc = Counter(r["stageName"] for r in rows if not r["closed"])
    for name, n in stc.most_common(12):
        print(f"  {n:4} · {name}")

    hdr("Скорость")
    sp = X["speed"]
    print(f"медиана цикла заказа: {sp['medCycle']} дн | медиана лага 1-го заказа: {sp['medLag']} дн")
    print("медианы стадий ЗАКАЗОВ (топ по длительности):")
    for b in sp["orderBench"][:10]:
        print(f"  {b['med']:6.1f} дн · {b['name'][:44]} (n={b['n']})")
    print("медианы стадий СДЕЛКИ в кат.0:")
    for b in sp["dealBench"][:10]:
        print(f"  {b['med']:6.1f} дн · {b['name'][:44]} (n={b['n']})")

    hdr("Замедленные (топ-10 открытых)")
    slow = [r for r in rows if r["slow"] and not r["closed"]]
    print(f"всего замедленных: {len(slow)}")
    for r in slow[:10]:
        print(f"  №{r['seq']:4} · {r['title'][:42]} · {r['stageName'][:24]} {r['stageDays']} дн · {r['why'][:80]}")

    hdr("Примеры строк (2 шт, компакт)")
    for r in rows[:2]:
        cp = {k: v for k, v in r.items() if k not in ("timeline", "orders")}
        print(json.dumps(cp, ensure_ascii=False)[:800])
        print(f"  timeline: {len(r['timeline'])} стадий; orders: {len(r['orders'])}")
        for t in r["timeline"][:5]:
            print(f"    {t['at']} → {t['name'][:34]} ({t['days']} дн)")
        for o in r["orders"][:3]:
            print(f"    заказ {o['id']} · {o['stageName'][:30]} · {o['days']} дн (порог {o['thr']}) {'SLOW' if o['slow'] else ''}")

    print("\n✓ зонд v3 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
