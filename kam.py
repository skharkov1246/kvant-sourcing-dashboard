"""Данные вкладки «КАМы» — эффективность коммерческих направлений (групп КАМ).
По ответственным группы: сделки, активный пайплайн (€), связанные заказы (СП-172) = выигранные,
контрактная выручка (€), закрытые заказы, конверсия. Σ — к базовой валюте по курсам Bitrix.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import config
from bitrix_client import BitrixClient

YEAR_START = "2026-01-01T00:00:00"
# группы коммерческого департамента (отдел → название направления)
KAM_GROUPS = {
    "112": "Лукойл", "110": "Норникель", "116": "Шельф/Роснефть", "114": "Нефтегаз",
    "126": "Сибур", "132": "Динамическое об.", "160": "Газотурбины", "162": "Фильтрация",
}


def _money(v: float) -> str:
    v = round(v)
    if abs(v) >= 1_000_000:
        return f"€{v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"€{v/1_000:.0f}K"
    return f"€{v}"


def compute(client: BitrixClient, *, as_of: dt.date | None = None,
            created: list[dict] | None = None, orders: list[dict] | None = None) -> dict:
    today = as_of or dt.date.today()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    owner_group: dict[str, str] = {}
    for did, nm in KAM_GROUPS.items():
        for u in client.list_paged("user.get", {"FILTER": {"UF_DEPARTMENT": int(did)}}):
            owner_group[str(u["ID"])] = nm

    if created is None:
        created = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START},
            select=["ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "ASSIGNED_BY_ID"])
    if orders is None:
        orders = client.list_items(172, filter={">=createdTime": YEAR_START},
            select=["id", "stageId", "opportunity", "currencyId", "parentId2", "assignedById"])

    deal_owner = {str(d["ID"]): str(d.get("ASSIGNED_BY_ID")) for d in created}

    g = defaultdict(lambda: {"deals": 0, "open": 0, "pipeline": 0.0, "orders": 0, "revenue": 0.0, "closed": 0})
    for d in created:
        grp = owner_group.get(str(d.get("ASSIGNED_BY_ID")))
        if not grp:
            continue
        g[grp]["deals"] += 1
        if (d.get("STAGE_SEMANTIC_ID") or "").upper() not in ("S", "F"):
            g[grp]["open"] += 1
            g[grp]["pipeline"] += eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))

    for o in orders:
        owner = deal_owner.get(str(o.get("parentId2"))) or str(o.get("assignedById"))
        grp = owner_group.get(owner)
        if not grp:
            continue
        g[grp]["orders"] += 1
        g[grp]["revenue"] += eur(o.get("opportunity"), o.get("currencyId"))
        if str(o.get("stageId", "")).endswith(":SUCCESS"):
            g[grp]["closed"] += 1

    rows = []
    for grp, v in g.items():
        rows.append({
            "group": grp, "deals": v["deals"], "open": v["open"],
            "pipeline": _money(v["pipeline"]), "pipelineRaw": round(v["pipeline"]),
            "orders": v["orders"], "revenue": _money(v["revenue"]), "revenueRaw": round(v["revenue"]),
            "closed": v["closed"], "winPct": round(v["orders"] / v["deals"] * 100) if v["deals"] else 0,
        })
    rows.sort(key=lambda r: r["revenueRaw"], reverse=True)

    tot = {
        "deals": sum(r["deals"] for r in rows), "open": sum(r["open"] for r in rows),
        "pipeline": _money(sum(g[r["group"]]["pipeline"] for r in rows)),
        "orders": sum(r["orders"] for r in rows),
        "revenue": _money(sum(g[r["group"]]["revenue"] for r in rows)),
        "closed": sum(r["closed"] for r in rows),
    }
    kpis = [
        ("Направлений", str(len(rows)), "групп КАМ", ""),
        ("Сделок (их)", str(tot["deals"]), "у направлений", ""),
        ("Активный пайплайн", tot["pipeline"], "Σ открытых, €", "ok"),
        ("Заказы (выигр.)", str(tot["orders"]), "СП-172", "ok"),
        ("Контрактная выручка", tot["revenue"], "Σ заказов, €", "ok"),
        ("Закрыто заказов", str(tot["closed"]), "поставлено/оплачено", ""),
    ]
    return {
        "label": f"01.01 – {today.strftime('%d.%m.%Y')}",
        "rows": rows,
        "totals": tot,
        "kpis": [{"lbl": l, "val": vv, "meta": me, "clz": c} for l, vv, me, c in kpis],
        "byRevenue": [{"group": r["group"], "v": r["revenueRaw"], "label": r["revenue"]} for r in rows],
    }
