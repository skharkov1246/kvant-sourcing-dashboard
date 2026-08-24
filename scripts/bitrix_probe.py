"""CI-зонд v19: сколько заказов и денег мы разместили у поставщика «Конан»
(Suzhou Conan / FlowServe и пр.) в 2026 году.

Ищем компании-поставщики по маскам (конан/conan/сучжоу/suzhou/flowserve),
плюс заказы СП-172, где flowserve упомянут в названии, но компания другая.
Считаем: штуки, суммы в валютах заказа и в EUR, по месяцам, по стадиям,
по сделкам/клиентам; место поставщика в общем рейтинге закупок 2026.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

import requests

Y = "2026-01-01"
MASKS = ("конан", "conan", "сучжоу", "suzhou", "flowserve", "флоусерв", "флаусерв")


def bx(method: str, params: dict | None = None) -> dict:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    for _ in range(3):
        try:
            r = requests.post(f"{base}/{method}.json", json=params or {}, timeout=60)
            r.raise_for_status()
            return r.json()
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


def main() -> int:
    cur = bx("crm.currency.list", {}).get("result") or []
    rate = {c.get("CURRENCY"): float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1) for c in cur}
    def eur(v, c): return float(v or 0) * rate.get(c, 1.0)

    print("=== 1. Компании по маскам ===")
    comp = bx_all("crm.company.list", {"select": ["ID", "TITLE"]})
    cname = {str(c["ID"]): c.get("TITLE") or "" for c in comp}
    hits = {cid: t for cid, t in cname.items() if any(m in t.lower() for m in MASKS)}
    for cid, t in sorted(hits.items(), key=lambda kv: int(kv[0])):
        print(f"  компания {cid}: «{t}»")
    if not hits:
        print("  по маскам не найдено ни одной компании")

    print("\n=== 2. Заказы СП-172 за 2026 ===")
    orders = bx_all("crm.item.list", {"entityTypeId": 172,
        "filter": {">=createdTime": Y},
        "select": ["id", "title", "companyId", "parentId2", "opportunity", "currencyId",
                   "stageId", "createdTime", "assignedById"]})
    print(f"  всего заказов, заведённых с {Y}: {len(orders)}")

    stages = {}
    for s in bx("crm.status.list", {"filter": {"ENTITY_ID": "DYNAMIC_172_STAGE_26"}}).get("result") or []:
        stages[s.get("STATUS_ID")] = s.get("NAME")
    def stname(sid): return stages.get(sid) or str(sid)

    konan = [o for o in orders if str(o.get("companyId")) in hits]
    fsw = [o for o in orders if str(o.get("companyId")) not in hits
           and any(m in str(o.get("title") or "").lower() for m in ("flowserve", "флоусерв", "флаусерв"))]

    users = {str(u["ID"]): f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
             for u in bx_all("user.get", {})}

    dids = {str(o.get("parentId2")) for o in konan + fsw if o.get("parentId2")}
    deals = {}
    if dids:
        for d in bx_all("crm.deal.list", {"filter": {"ID": sorted(dids)},
                "select": ["ID", "TITLE", "COMPANY_ID", "CATEGORY_ID", "STAGE_ID"]}):
            deals[str(d["ID"])] = d

    def block(name, oo):
        print(f"\n=== {name}: {len(oo)} заказов ===")
        tot_eur = 0.0
        by_cur = defaultdict(float)
        by_mon = defaultdict(lambda: [0, 0.0])
        by_stage = defaultdict(lambda: [0, 0.0])
        by_deal = defaultdict(lambda: [0, 0.0])
        for o in oo:
            v, c = float(o.get("opportunity") or 0), o.get("currencyId")
            e = eur(v, c)
            tot_eur += e
            by_cur[c] += v
            m = str(o.get("createdTime") or "")[:7]
            by_mon[m][0] += 1; by_mon[m][1] += e
            sn = stname(o.get("stageId"))
            by_stage[sn][0] += 1; by_stage[sn][1] += e
            did = str(o.get("parentId2") or "—")
            by_deal[did][0] += 1; by_deal[did][1] += e
        print(f"  Σ в EUR (по курсам Битрикса): {tot_eur:,.0f} €")
        print("  по валютам заказов: " + " · ".join(f"{c}: {v:,.0f}" for c, v in sorted(by_cur.items())))
        print("  по месяцам: " + " · ".join(f"{m}: {n} шт / {e:,.0f}€" for m, (n, e) in sorted(by_mon.items())))
        print("  по стадиям:")
        for sn, (n, e) in sorted(by_stage.items(), key=lambda kv: -kv[1][1]):
            print(f"    {sn[:40]:42s} {n:>3} шт · {e:>12,.0f} €")
        print("  по сделкам:")
        for did, (n, e) in sorted(by_deal.items(), key=lambda kv: -kv[1][1])[:15]:
            d = deals.get(did) or {}
            cl = cname.get(str(d.get("COMPANY_ID")), "—")
            print(f"    сделка {did} «{str(d.get('TITLE') or '—')[:44]}» клиент «{cl[:28]}» "
                  f"кат.{d.get('CATEGORY_ID','—')}: {n} шт · {e:,.0f} €")
        print("  сами заказы:")
        for o in sorted(oo, key=lambda o: -eur(o.get("opportunity"), o.get("currencyId")))[:20]:
            print(f"    #{o.get('id')} {str(o.get('createdTime'))[:10]} "
                  f"{float(o.get('opportunity') or 0):>14,.0f} {o.get('currencyId')} "
                  f"· {stname(o.get('stageId'))[:24]:26s} · отв. {users.get(str(o.get('assignedById')), '—')[:20]:22s}"
                  f" · «{str(o.get('title') or '')[:52]}»")

    block("ЗАКАЗЫ У «КОНАНА» (по компании)", konan)
    if fsw:
        block("FLOWSERVE в названии, но компания другая", fsw)
        for o in fsw[:10]:
            print(f"    ↳ #{o.get('id')} компания: «{cname.get(str(o.get('companyId')), '—')[:50]}»")

    print("\n=== 3. Место в рейтинге закупок 2026 ===")
    agg = defaultdict(float)
    cnt = defaultdict(int)
    for o in orders:
        cid = str(o.get("companyId") or "—")
        agg[cid] += eur(o.get("opportunity"), o.get("currencyId"))
        cnt[cid] += 1
    rank = sorted(agg.items(), key=lambda kv: -kv[1])
    total = sum(agg.values())
    print(f"  всего закупок 2026 (все поставщики): {total:,.0f} € · поставщиков: {len(agg)}")
    for i, (cid, e) in enumerate(rank[:12], 1):
        mark = "  ← ОН" if cid in hits else ""
        print(f"  {i:>2}. {cname.get(cid, '—')[:44]:46s} {cnt[cid]:>4} шт · {e:>12,.0f} € · {e/total*100:4.1f}%{mark}")
    for i, (cid, e) in enumerate(rank, 1):
        if cid in hits and i > 12:
            print(f"  {i:>2}. {cname.get(cid, '—')[:44]:46s} {cnt[cid]:>4} шт · {e:>12,.0f} € · {e/total*100:4.1f}%  ← ОН")

    print("\n✓ зонд v19 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
