"""Зонд v25: есть ли у нас направление горнопроходческой техники и перфораторов.

Ищем по сделкам, запросам поставщикам (СП-166) и заказам (СП-172):
буровые установки, перфораторы, погрузочно-доставочные машины, комбайны,
крепь, а также изготовителей отрасли (Epiroc, Sandvik, Normet, Boart Longyear и др.).
Считаем: сколько сделок и на какие суммы, у кого закупали, кто вёл.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

import requests

RU = ("перфоратор", "буровая установ", "буровой станок", "бурильн", "проходческ",
      "горнопроход", "погрузочно-доставочн", "пдм", "самоходн", "анкер",
      "крепь", "комбайн", "штрек", "забой", "рудник", "шахтн", "буровзрывн",
      "коронк", "буровая штанга", "долото")
BRANDS = ("epiroc", "sandvik", "atlas copco", "boart longyear", "normet", "maclean",
          "komatsu mining", "joy global", "caterpillar underground", "gh h", "herrenknecht",
          "robbins", "aramine", "paus", "berco", "montabert", "furukawa", "tamrock",
          "sany", "xcmg mining", "cre", "kaiyuan", "chuangli")
ALL = RU + BRANDS


def bx(method: str, params: dict | None = None) -> dict:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    for _ in range(4):
        try:
            r = requests.post(f"{base}/{method}.json", json=params or {}, timeout=90)
            r.raise_for_status()
            j = r.json()
            if isinstance(j, dict) and j.get("error") in ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"):
                continue
            return j
        except Exception:
            continue
    return {}


def bx_all(method: str, params: dict) -> list:
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j:
            return out
        start = j["next"]


def hit(text: str) -> str | None:
    t = (text or "").lower()
    for w in ALL:
        if w in t:
            return w
    return None


def main() -> int:
    cur = bx("crm.currency.list", {}).get("result") or []
    rate = {c.get("CURRENCY"): float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1) for c in cur}
    def eur(v, c): return float(v or 0) * rate.get(c, 1.0)

    print("=== 1. СДЕЛКИ (все воронки, вся история) ===")
    deals = bx_all("crm.deal.list", {"select": ["ID", "TITLE", "OPPORTUNITY", "CURRENCY_ID",
                                                "CATEGORY_ID", "DATE_CREATE", "COMPANY_ID", "ASSIGNED_BY_ID"]})
    print(f"  всего сделок в портале: {len(deals)}")
    dh = [(d, hit(d.get("TITLE"))) for d in deals]
    dm = [(d, w) for d, w in dh if w]
    print(f"  с горной тематикой в названии: {len(dm)}")
    if dm:
        tot = sum(eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d, _ in dm)
        print(f"  суммарно: {tot:,.0f} €")
        print("  по словам: " + " · ".join(f"{w}:{n}" for w, n in Counter(w for _, w in dm).most_common(12)))
        print("  по годам: " + " · ".join(f"{y}:{n}" for y, n in sorted(Counter(str(d.get('DATE_CREATE'))[:4] for d, _ in dm).items())))
        print("  крупнейшие:")
        for d, w in sorted(dm, key=lambda x: -eur(x[0].get("OPPORTUNITY"), x[0].get("CURRENCY_ID")))[:12]:
            print(f"    №{d['ID']:>6} {eur(d.get('OPPORTUNITY'), d.get('CURRENCY_ID')):>12,.0f} € "
                  f"кат.{d.get('CATEGORY_ID')} · «{str(d.get('TITLE'))[:64]}» [{w}]")

    print("\n=== 2. ЗАПРОСЫ ПОСТАВЩИКАМ (СП-166) ===")
    rq = bx_all("crm.item.list", {"entityTypeId": 166, "filter": {"categoryId": 24},
                                  "select": ["id", "title", "companyId", "stageId", "createdTime"]})
    rm = [(r, hit(r.get("title"))) for r in rq]
    rm = [(r, w) for r, w in rm if w]
    print(f"  всего запросов: {len(rq)} · с горной тематикой: {len(rm)}")
    if rm:
        print("  по словам: " + " · ".join(f"{w}:{n}" for w, n in Counter(w for _, w in rm).most_common(10)))
        print("  примеры:")
        for r, w in rm[:10]:
            print(f"    #{r['id']} {str(r.get('createdTime'))[:10]} «{str(r.get('title'))[:66]}» [{w}]")

    print("\n=== 3. ЗАКАЗЫ ПОСТАВЩИКАМ (СП-172) ===")
    orders = bx_all("crm.item.list", {"entityTypeId": 172,
                                      "select": ["id", "title", "companyId", "opportunity", "currencyId", "createdTime"]})
    om = [(o, hit(o.get("title"))) for o in orders]
    om = [(o, w) for o, w in om if w]
    print(f"  всего заказов: {len(orders)} · с горной тематикой: {len(om)}")
    if om:
        tot = sum(eur(o.get("opportunity"), o.get("currencyId")) for o, _ in om)
        print(f"  суммарно закуплено: {tot:,.0f} €")
        for o, w in sorted(om, key=lambda x: -eur(x[0].get("opportunity"), x[0].get("currencyId")))[:10]:
            print(f"    #{o['id']} {str(o.get('createdTime'))[:10]} "
                  f"{eur(o.get('opportunity'), o.get('currencyId')):>11,.0f} € «{str(o.get('title'))[:60]}» [{w}]")

    print("\n=== 4. КОМПАНИИ отрасли в базе ===")
    comp = bx_all("crm.company.list", {"select": ["ID", "TITLE"]})
    cm = [(c, hit(c.get("TITLE"))) for c in comp]
    cm = [(c, w) for c, w in cm if w]
    print(f"  всего компаний: {len(comp)} · профильных по названию: {len(cm)}")
    for c, w in cm[:25]:
        print(f"    {c['ID']:>6} «{str(c.get('TITLE'))[:62]}» [{w}]")

    print("\n✓ зонд v25 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
