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
                with_people: bool = False, deal_owner: dict | None = None,
                deal_sale: dict | None = None) -> dict:
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
            select=["ID", "TITLE", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "ASSIGNED_BY_ID"])
    if orders is None:
        orders = client.list_items(172, filter={">=createdTime": YEAR_START},
            select=["id", "title", "stageId", "opportunity", "currencyId", "parentId2", "assignedById"])
        orders = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    # карта владельца сделки (id→uid): из main передаётся полная (вкл. родителей старше 2026),
    # иначе строим из created (тогда заказы на сделки до 2026 уйдут в fallback на assignedById)
    if deal_owner is None:
        deal_owner = {str(d["ID"]): str(d.get("ASSIGNED_BY_ID")) for d in created}
    if deal_sale is None:
        deal_sale = {str(d["ID"]): eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d in created}

    def blank():
        return {"deals": 0, "open": 0, "pipeline": 0.0, "orders": 0, "buy": 0.0, "closed": 0}
    g = defaultdict(blank)
    ppl = defaultdict(blank)
    contracts_g: dict[str, set] = defaultdict(set)   # группа → {id сделки-контракта (есть заказ поставщику)}
    contracts_p: dict[str, set] = defaultdict(set)
    deal_details: list[dict] = []
    order_details: list[dict] = []

    for d in created:
        owner = str(d.get("ASSIGNED_BY_ID"))
        grp = owner_group.get(owner)
        if not grp:
            continue
        is_open = (d.get("STAGE_SEMANTIC_ID") or "").upper() not in ("S", "F")
        amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))   # сумма сделки = ПРОДАЖА
        for bucket in (g[grp], ppl[owner]):
            bucket["deals"] += 1
            if is_open:
                bucket["open"] += 1
                bucket["pipeline"] += amt
        deal_details.append({"id": str(d["ID"]), "t": (d.get("TITLE") or f'Сделка #{d["ID"]}')[:90],
                             "amt": _money(amt), "raw": round(amt), "grp": grp,
                             "owner": owner, "own": owner_name.get(owner, owner), "open": is_open})

    for o in orders:
        did = str(o.get("parentId2") or "")
        owner = deal_owner.get(did) or str(o.get("assignedById"))
        grp = owner_group.get(owner)
        if not grp:
            continue
        won_closed = str(o.get("stageId", "")).endswith(":SUCCESS")
        buy = eur(o.get("opportunity"), o.get("currencyId"))    # opportunity заказа СП-172 = ЗАКУПКА
        for bucket in (g[grp], ppl[owner]):
            bucket["orders"] += 1
            bucket["buy"] += buy
            if won_closed:
                bucket["closed"] += 1
        if did:
            contracts_g[grp].add(did); contracts_p[owner].add(did)
        order_details.append({"id": str(o.get("id")), "t": (o.get("title") or f'Заказ #{o.get("id")}')[:90],
                              "amt": _money(buy), "raw": round(buy), "grp": grp,
                              "owner": owner, "own": owner_name.get(owner, owner), "closed": won_closed})

    def sales_of(cset): return sum(deal_sale.get(did, 0.0) for did in cset)

    def fmt_rows(src, cmap, label_key, label_get):
        out = []
        for key, v in src.items():
            sales = sales_of(cmap.get(key, set())); margin = sales - v["buy"]
            out.append({
                label_key: label_get(key), "uid": str(key), "grp": owner_group.get(key, "") if label_key == "name" else "",
                "deals": v["deals"], "open": v["open"], "pipeline": _money(v["pipeline"]),
                "contracts": len(cmap.get(key, set())), "orders": v["orders"],
                "sales": _money(sales), "salesRaw": round(sales),
                "buy": _money(v["buy"]), "buyRaw": round(v["buy"]),
                "margin": _money(margin), "marginRaw": round(margin),
                "marginPct": (round(margin / sales * 100) if sales else None),
                "closed": v["closed"],
            })
        out.sort(key=lambda r: r["salesRaw"], reverse=True)
        return out

    rows = fmt_rows(g, contracts_g, "group", lambda k: k)
    people = fmt_rows({u: v for u, v in ppl.items() if v["deals"] or v["orders"]}, contracts_p, "name", lambda u: owner_name.get(u, u)) if with_people else []

    pipe_sum = sum(g[r["group"]]["pipeline"] for r in rows)
    sales_sum = sum(r["salesRaw"] for r in rows); buy_sum = sum(r["buyRaw"] for r in rows)
    margin_sum = sales_sum - buy_sum; tot_contracts = sum(r["contracts"] for r in rows)
    tot = {"deals": sum(r["deals"] for r in rows), "open": sum(r["open"] for r in rows),
           "pipeline": _money(pipe_sum), "contracts": tot_contracts, "orders": sum(r["orders"] for r in rows),
           "sales": _money(sales_sum), "buy": _money(buy_sum), "margin": _money(margin_sum),
           "marginPct": (round(margin_sum / sales_sum * 100) if sales_sum else 0),
           "closed": sum(r["closed"] for r in rows)}
    kpis = [
        ("Сделок", str(tot["deals"]), "создано (YTD)", "", "deals"),
        ("Активный пайплайн", tot["pipeline"], "Σ открытых продаж, €", "ok", "open"),
        ("Контрактов", str(tot_contracts), "сделок с заказами поставщикам", "ok", ""),
        ("Выручка (продажи)", tot["sales"], "Σ сумм сделок-контрактов, €", "ok", ""),
        ("Закупка", tot["buy"], "Σ заказов поставщикам, €", "amber", "orders"),
        ("Маржа", tot["margin"], f"{tot['marginPct']}% валовая", "ok" if margin_sum >= 0 else "warn", ""),
    ]
    return {
        "label": f"01.01 – {today.strftime('%d.%m.%Y')}",
        "rows": rows,
        "people": people,
        "totals": tot,
        "kpis": [{"lbl": l, "val": vv, "meta": me, "clz": c, "drill": dr} for l, vv, me, c, dr in kpis],
        "byRevenue": [{"group": r["group"], "v": r["salesRaw"], "label": r["sales"]} for r in rows],
        "deals": deal_details,
        "orders": order_details,
    }
