"""CI-зонд v20: Jiangsu Tongyi Technology — все заказы СП-172 (2026 и вся история).

Владелец: «Конан» = Jiangsu Tongyi. Ищем компании по маскам (tongyi/tongui/тонги),
берём ВСЕ их заказы без фильтра по названию, считаем 2026 отдельно и историю целиком:
штуки, суммы в валютах и EUR, стадии, месяцы, сделки/клиенты, ответственные.
"""
from __future__ import annotations

import os
from collections import defaultdict

import requests

MASKS = ("tongyi", "tongui", "тонги")


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

    print("=== 1. Компании Tongyi ===")
    ids = {}
    for m in MASKS:
        for c in bx_all("crm.company.list", {"filter": {"%TITLE": m}, "select": ["ID", "TITLE"]}):
            ids[str(c["ID"])] = c.get("TITLE") or ""
    for cid, t in sorted(ids.items(), key=lambda kv: int(kv[0])):
        print(f"  компания {cid}: «{t}»")
    if not ids:
        print("  не найдено")
        return 0

    print("\n=== 2. Все их заказы СП-172 (без фильтра по дате) ===")
    orders = []
    for cid in ids:
        orders += bx_all("crm.item.list", {"entityTypeId": 172,
            "filter": {"companyId": cid},
            "select": ["id", "title", "companyId", "parentId2", "opportunity", "currencyId",
                       "stageId", "createdTime", "assignedById"]})
    print(f"  всего заказов за всю историю: {len(orders)}")

    stages = {}
    for s in bx("crm.status.list", {"filter": {"ENTITY_ID": "DYNAMIC_172_STAGE_26"}}).get("result") or []:
        stages[s.get("STATUS_ID")] = s.get("NAME")
    def stname(sid): return stages.get(sid) or str(sid)

    users = {str(u["ID"]): f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
             for u in bx_all("user.get", {})}
    dids = {str(o.get("parentId2")) for o in orders if o.get("parentId2")}
    deals, cname = {}, {}
    if dids:
        for d in bx_all("crm.deal.list", {"filter": {"ID": sorted(dids)},
                "select": ["ID", "TITLE", "COMPANY_ID", "CATEGORY_ID"]}):
            deals[str(d["ID"])] = d
        cids = {str(d.get("COMPANY_ID")) for d in deals.values() if d.get("COMPANY_ID")}
        for c in bx_all("crm.company.list", {"filter": {"ID": sorted(cids)}, "select": ["ID", "TITLE"]}):
            cname[str(c["ID"])] = c.get("TITLE") or ""

    def block(name, oo):
        print(f"\n=== {name}: {len(oo)} заказов ===")
        tot = 0.0
        by_cur = defaultdict(float); by_mon = defaultdict(lambda: [0, 0.0])
        by_stage = defaultdict(lambda: [0, 0.0]); by_cli = defaultdict(lambda: [0, 0.0])
        for o in oo:
            v, c = float(o.get("opportunity") or 0), o.get("currencyId")
            e = eur(v, c); tot += e; by_cur[c] += v
            m = str(o.get("createdTime") or "")[:7]
            by_mon[m][0] += 1; by_mon[m][1] += e
            sn = stname(o.get("stageId")); by_stage[sn][0] += 1; by_stage[sn][1] += e
            d = deals.get(str(o.get("parentId2") or ""), {})
            cl = cname.get(str(d.get("COMPANY_ID")), "—")
            by_cli[cl][0] += 1; by_cli[cl][1] += e
        print(f"  Σ ≈ {tot:,.0f} € (по курсам Битрикса)")
        print("  по валютам: " + " · ".join(f"{c}: {v:,.0f}" for c, v in sorted(by_cur.items())))
        print("  по месяцам: " + " · ".join(f"{m}: {n}шт/{e:,.0f}€" for m, (n, e) in sorted(by_mon.items())))
        print("  по стадиям:")
        for sn, (n, e) in sorted(by_stage.items(), key=lambda kv: -kv[1][1]):
            print(f"    {sn[:38]:40s} {n:>3} шт · {e:>11,.0f} €")
        print("  по клиентам:")
        for cl, (n, e) in sorted(by_cli.items(), key=lambda kv: -kv[1][1]):
            print(f"    {cl[:38]:40s} {n:>3} шт · {e:>11,.0f} €")
        print("  заказы:")
        for o in sorted(oo, key=lambda o: str(o.get("createdTime"))):
            d = deals.get(str(o.get("parentId2") or ""), {})
            print(f"    #{o.get('id')} {str(o.get('createdTime'))[:10]} "
                  f"{float(o.get('opportunity') or 0):>12,.0f} {o.get('currencyId')} ≈{eur(o.get('opportunity'), o.get('currencyId')):>9,.0f}€ "
                  f"· {stname(o.get('stageId'))[:22]:24s} · {users.get(str(o.get('assignedById')), '—')[:18]:20s}"
                  f" · сд.{d.get('ID','—')} · «{str(o.get('title') or '')[:46]}»")

    y26 = [o for o in orders if str(o.get("createdTime") or "").startswith("2026")]
    old = [o for o in orders if not str(o.get("createdTime") or "").startswith("2026")]
    block("2026 ГОД", y26)
    if old:
        block("ДО 2026 (история)", old)

    print("\n✓ зонд v20 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
