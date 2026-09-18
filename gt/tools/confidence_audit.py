#!/usr/bin/env python3
"""Уверенность против собственной проверки: где «A» ничем не обеспечена.

Откуда взялось. Проверка на опровержение поймала это на одной строке: у 1701/05
стоит уверенность «A», её основание ссылается на страницу ПОИСКА eBay по другому
номеру, а блок проверок того же файла по этому же номеру говорит «dead_link,
цену не увидел ни одну». Набор противоречит сам себе, а в документ уходит буква
уверенности.

Замер по всему набору цен показывает, что это не единичный случай. Правило
отбора строгое, чтобы не раздувать находку:
  берём только записи с уверенностью A или B;
  ищем проверку ТОГО ЖЕ номера в блоке `checks` того же файла;
  дефектом считаем только закрытый список вердиктов проверки — мёртвая ссылка,
    ссылка на другой артикул, продавец оказался не продавцом, цена расходится;
  вердикт `confirmed` дефектом НЕ считается, даже если в примечании к нему
    упомянут отказ какого-то второстепенного источника.

    python gt/tools/confidence_audit.py            # показать замер
    python gt/tools/confidence_audit.py --write    # записать набор
    python gt/tools/confidence_audit.py --check    # сверить набор с замером
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRICES = ROOT / "gt/data/rfq_prices.json"
SHIP = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_confidence.json"

# Закрытый список: что в проверке означает «цены по этой строке на самом деле
# нет». Каждый вердикт — своя цена ошибки, поэтому считаем их раздельно.
DEFECTS = {
    "dead_link": "ссылка мёртвая или закрыта антиботом — цену никто не видел",
    "pn_not_found": "ссылка ведёт на ДРУГОЙ артикул: цена чужая",
    "not_a_seller": "по адресу не продавец: число не является предложением",
    "price_differs": "на странице другая цена, чем записана: число неверно, уровень "
                     "может быть верен",
}
STRONG = {"A", "B"}


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").upper())


def exposure(row: dict) -> float:
    lo, hi = row.get("usd_lo"), row.get("usd_hi")
    if lo in (None, "") or hi in (None, ""):
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(row.get("qty") or 0)


def measure() -> dict:
    doc = json.loads(PRICES.read_text(encoding="utf-8"))
    ship = {key(r["pn"]): r for r in json.loads(SHIP.read_text(encoding="utf-8"))["rows"]}
    checks: dict[str, list[dict]] = collections.defaultdict(list)
    for c in doc.get("checks") or []:
        checks[key(c.get("pn"))].append(c)

    items = []
    for p in doc.get("prices") or []:
        if (p.get("conf") or "").upper() not in STRONG:
            continue
        for c in checks.get(key(p.get("pn")), []):
            v = (c.get("verdict") or "").strip()
            if v not in DEFECTS:
                continue
            row = ship.get(key(p.get("pn"))) or {}
            items.append({
                "pn": p.get("pn"),
                "conf": (p.get("conf") or "").upper(),
                "verdict": v,
                "what_it_means": DEFECTS[v],
                "qty": row.get("qty"),
                "exposure": int(round(exposure(row))),
                "man": (row.get("man") or "").strip(),
                "note": str(c.get("note") or "")[:400],
            })
            break
    items.sort(key=lambda x: -x["exposure"])
    tot = int(round(sum(exposure(r) for r in ship.values())))
    hit = sum(x["exposure"] for x in items)
    classes = {}
    for v in DEFECTS:
        g = [x for x in items if x["verdict"] == v]
        classes[v] = {"rows": len(g), "exposure": sum(x["exposure"] for x in g),
                      "means": DEFECTS[v]}
    return {
        "updated": "2026-09-18",
        "source": "Замер по gt/data/rfq_prices.json: строки с уверенностью A или B, чья "
                  "СОБСТВЕННАЯ проверка в том же файле говорит, что цены никто не видел.",
        "method": "Уверенность A или B, вердикт проверки того же номера из закрытого списка "
                  "(мёртвая ссылка, ссылка на другой артикул, по адресу не продавец, цена "
                  "расходится). Вердикт confirmed дефектом не считается, даже если в "
                  "примечании упомянут отказ второстепенного источника. Считает и сверяет "
                  "gt/tools/confidence_audit.py.",
        "why_it_matters": "Буква уверенности идёт в документ и читается как «проверено». Там, "
                          "где проверка того же набора записала дефект, буква обязана быть "
                          "снижена, а строка перепроверена. ЧИТАТЬ ПО КЛАССАМ, А НЕ ОДНОЙ "
                          "СУММОЙ: у мёртвой ссылки цены не видел никто; у ссылки на другой "
                          "артикул цена чужая; «цена расходится» значит, что записанное число "
                          "неверно, но уровень может быть верным; «по адресу не продавец» — что "
                          "число не является предложением. Цена ошибки у этих классов разная.",
        "totals": {
            "exposure_total": tot,
            "rows": len(items),
            "exposure": hit,
            "share_pct": round(hit / tot * 100, 1) if tot else 0.0,
        },
        "classes": classes,
        "items": items,
    }


def report(m: dict) -> str:
    ru = lambda n: f"{int(n):,}".replace(",", " ")  # noqa: E731
    t = m["totals"]
    out = [f"строк с необеспеченной уверенностью: {t['rows']} · экспозиция "
           f"{ru(t['exposure'])} из {ru(t['exposure_total'])} USD = {t['share_pct']} %"]
    for v, c in sorted(m["classes"].items(), key=lambda kv: -kv[1]["exposure"]):
        out.append(f"  {v:16} {c['rows']:4} строк {ru(c['exposure']):>12} USD — {c['means']}")
    out.append("  самые дорогие: " + " · ".join(
        f"{x['pn']} ({ru(x['exposure'])} USD, {x['verdict']})" for x in m["items"][:5]))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    m = measure()
    print(report(m))
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"записано в {OUT.relative_to(ROOT)}")
        return 0
    if a.check:
        if not OUT.exists():
            print("набора нет — соберите: --write", file=sys.stderr)
            return 1
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if old.get("totals") != m["totals"] or old.get("classes") != m["classes"]:
            print(f"набор устарел: в наборе {old.get('totals')}, замер {m['totals']}",
                  file=sys.stderr)
            return 1
        print("✓ числа набора совпадают с замером")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
