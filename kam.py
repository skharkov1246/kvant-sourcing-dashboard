"""Эффективность направлений (группы отделов) по модели «сделки → заказы → выручка».
Один движок compute_set() для трёх вкладок:
  • КАМы — клиентские направления (с детализацией до человека);
  • Инжиниринг — инженерные/проектные группы (с детализацией до человека);
  • Продукт-оунеры — продуктовые линии (оборудование).
Заказ (СП-172) = выигранная сделка в исполнении; выручка — Σ заказов к базовой € по курсам Bitrix.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import config
from bitrix_client import BitrixClient

YEAR_START = "2026-01-01T00:00:00"

# клиентские направления (КАМы)
CLIENT_GROUPS = {"112": "Лукойл", "110": "Норникель", "116": "Шельф/Роснефть", "114": "Нефтегаз", "126": "Сибур"}
# продуктовые линии (продукт-оунеры)
PRODUCT_GROUPS = {"132": "Компрессоры", "160": "Газотурбины", "162": "Фильтрация",
                  "130": "Водяные насосы", "166": "Технологич. насосы", "124": "Грануляция"}
# инжиниринг (технические/проектные группы)
ENG_GROUPS = {"68": "Инжиниринг (общая)", "122": "Конструкторы", "108": "КИП/СКУТ",
              "168": "КИНЕФ/АКРОН", "158": "Сервис", "154": "Ключевые заказчики ЦР"}


def _money(v: float) -> str:
    v = round(v)
    if abs(v) >= 1_000_000:
        return f"€{v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"€{v/1_000:.0f}K"
    return f"€{v}"


def _ratio(orders: int, deals: int) -> str:
    """Заказов на одну созданную в 2026 сделку (заказ может ссылаться на сделку-родителя старше 2026,
    поэтому значение может быть >1×; это нормальный показатель повторных заказов, а не процент-конверсия)."""
    if not deals:
        return "—"
    r = orders / deals
    return f"{r:.1f}×" if r < 10 else f"{round(r)}×"


def compute_set(client: BitrixClient, groups: dict[str, str], *, as_of: dt.date | None = None,
                created: list[dict] | None = None, orders: list[dict] | None = None,
                with_people: bool = False) -> dict:
    today = as_of or dt.date.today()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    owner_group: dict[str, str] = {}
    owner_name: dict[str, str] = {}
    for did, nm in groups.items():
        for u in client.list_paged("user.get", {"FILTER": {"UF_DEPARTMENT": int(did)}}):
            uid = str(u["ID"])
            owner_group[uid] = nm
            owner_name[uid] = f"{u.get('LAST_NAME', '')} {u.get('NAME', '')}".strip() or f"user#{uid}"

    if created is None:
        created = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START},
            select=["ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "ASSIGNED_BY_ID"])
    if orders is None:
        orders = client.list_items(172, filter={">=createdTime": YEAR_START},
            select=["id", "stageId", "opportunity", "currencyId", "parentId2", "assignedById"])
    deal_owner = {str(d["ID"]): str(d.get("ASSIGNED_BY_ID")) for d in created}

    def blank():
        return {"deals": 0, "open": 0, "pipeline": 0.0, "orders": 0, "revenue": 0.0, "closed": 0}
    g = defaultdict(blank)
    ppl = defaultdict(blank)

    for d in created:
        owner = str(d.get("ASSIGNED_BY_ID"))
        grp = owner_group.get(owner)
        if not grp:
            continue
        for bucket in (g[grp], ppl[owner]):
            bucket["deals"] += 1
            if (d.get("STAGE_SEMANTIC_ID") or "").upper() not in ("S", "F"):
                bucket["open"] += 1
                bucket["pipeline"] += eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))

    for o in orders:
        owner = deal_owner.get(str(o.get("parentId2"))) or str(o.get("assignedById"))
        grp = owner_group.get(owner)
        if not grp:
            continue
        won_closed = str(o.get("stageId", "")).endswith(":SUCCESS")
        amt = eur(o.get("opportunity"), o.get("currencyId"))
        for bucket in (g[grp], ppl[owner]):
            bucket["orders"] += 1
            bucket["revenue"] += amt
            if won_closed:
                bucket["closed"] += 1

    def fmt_rows(src: dict, label_key: str, label_get):
        out = []
        for key, v in src.items():
            out.append({
                label_key: label_get(key), "grp": owner_group.get(key, "") if label_key == "name" else "",
                "deals": v["deals"], "open": v["open"], "pipeline": _money(v["pipeline"]),
                "orders": v["orders"], "revenue": _money(v["revenue"]), "revenueRaw": round(v["revenue"]),
                "closed": v["closed"], "conv": _ratio(v["orders"], v["deals"]),
            })
        out.sort(key=lambda r: r["revenueRaw"], reverse=True)
        return out

    rows = fmt_rows(g, "group", lambda k: k)
    people = fmt_rows({u: v for u, v in ppl.items() if v["deals"] or v["orders"]}, "name", lambda u: owner_name.get(u, u)) if with_people else []

    pipe_sum = sum(g[r["group"]]["pipeline"] for r in rows)
    rev_sum = sum(g[r["group"]]["revenue"] for r in rows)
    tot_deals = sum(r["deals"] for r in rows); tot_orders = sum(r["orders"] for r in rows)
    tot = {"deals": tot_deals, "open": sum(r["open"] for r in rows),
           "pipeline": _money(pipe_sum), "orders": tot_orders,
           "revenue": _money(rev_sum), "closed": sum(r["closed"] for r in rows),
           "conv": _ratio(tot_orders, tot_deals)}
    kpis = [
        ("Групп", str(len(rows)), "в наборе", ""),
        ("Сделок", str(tot["deals"]), "у группы (YTD)", ""),
        ("Активный пайплайн", tot["pipeline"], "Σ открытых, €", "ok"),
        ("Заказы (выигр.)", str(tot["orders"]), "СП-172", "ok"),
        ("Контрактная выручка", tot["revenue"], "Σ заказов, €", "ok"),
        ("Закрыто заказов", str(tot["closed"]), "поставлено/оплачено", ""),
    ]
    return {
        "label": f"01.01 – {today.strftime('%d.%m.%Y')}",
        "rows": rows,
        "people": people,
        "totals": tot,
        "kpis": [{"lbl": l, "val": vv, "meta": me, "clz": c} for l, vv, me, c in kpis],
        "byRevenue": [{"group": r["group"], "v": r["revenueRaw"], "label": r["revenue"]} for r in rows],
    }
