"""Эффективность направлений (группы отделов) по модели «сделки → заказы → выручка».
Один движок compute_set() для трёх вкладок:
  • КАМы — клиентские направления (с детализацией до человека);
  • Инжиниринг — инженерные/проектные группы (с детализацией до человека);
  • Продукт-оунеры — продуктовые линии (оборудование).
Заказ (СП-172) = выигранная сделка в исполнении; выручка — Σ заказов к базовой € по курсам Bitrix.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from bitrix_client import BitrixClient

YEAR_START = "2026-01-01T00:00:00"

# клиентские направления (КАМы). Один отдел может вести и направление, и клиента —
# отдел 132 «Группа по динамическому оборудованию + Сибур» относится и к продукту (Компрессоры),
# и к клиенту Сибур, поэтому он есть и в CLIENT_GROUPS (Сибур), и в PRODUCT_GROUPS (Компрессоры).
CLIENT_GROUPS = {"112": "Лукойл", "110": "Норникель", "116": "Шельф/Роснефть", "114": "Нефтегаз",
                 "126": "Сибур", "132": "Сибур"}
# продуктовые линии (продукт-оунеры)
PRODUCT_GROUPS = {"132": "Компрессоры", "160": "Газотурбины", "162": "Фильтрация",
                  "130": "Водяные насосы", "166": "Технологич. насосы", "124": "Грануляция"}
# инжиниринг (технические/проектные группы)
ENG_GROUPS = {"68": "Инжиниринг (общая)", "122": "Конструкторы", "108": "КИП/СКУТ",
              "168": "КИНЕФ/АКРОН", "158": "Сервис", "154": "Ключевые заказчики ЦР"}

# КАМ-направления по КЛИЕНТУ (холдингу), а не по отделу-исполнителю: сделка относится к
# направлению по своей компании-клиенту. Дочки сводятся в холдинг (по согласованию с CCO):
# Казань/Нижнекамск → Сибур, ТАИФ-НК — отдельно; Сахалинская Энергия + Новатэк (Миловидов);
# остальные — каждый своим направлением (имя компании). Порядок важен (проверяем сверху вниз).
CLIENT_HOLDINGS = [
    (r"лукойл", "Лукойл"),
    (r"норильск|кольская\s*гмк|быстринск", "Норникель"),
    (r"таиф", "ТАИФ"),
    (r"сибур|ставролен|запсибнефтехим|амурский\s*гхк|воронежсинтезкаучук|биаксплен|пэст|казаньоргсинтез|нижнекамскнефтехим|тольяттикаучук", "Сибур"),
    (r"\bрн-|роснефт|башнефт", "Роснефть"),
    (r"сахалинская\s*энергия|ямал\s*спг|арктик\s*спг|новат[эе]к", "Новатэк/Сахалин"),
    (r"газпром|няганьгазпереработка", "Газпром"),
]


def client_dir(name: str) -> str:
    """Направление КАМ по названию компании-клиента: холдинг или (если не холдинг) сама компания."""
    n = (name or "").lower()
    for pat, dirn in CLIENT_HOLDINGS:
        if re.search(pat, n):
            return dirn
    return (name or "Без клиента").strip()


def _money(v: float) -> str:
    v = round(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}€{a/1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}€{a/1_000:.0f}K"
    return f"{sign}€{a}"


def _regno(title) -> int:
    """Номер реализации в начале названия сделки («871. …»); требуем точку (не путать с PO-кодами)."""
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(title or ""))
    return int(m.group(1)) if m else 0


def compute_set(client: BitrixClient, groups: dict[str, str] | None, *, as_of: dt.date | None = None,
                created: list[dict] | None = None, orders: list[dict] | None = None,
                with_people: bool = False, deal_owner: dict | None = None,
                deal_sale: dict | None = None, deal_group: dict | None = None) -> dict:
    # Два режима группировки:
    #   • по отделу-исполнителю (groups: deptId→имя) — для Инжиниринга/Продукта;
    #   • по КЛИЕНТУ сделки (deal_group: deal_id→направление) — для КАМ (передаётся из main).
    by_client = deal_group is not None
    today = as_of or dt.date.today()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    owner_group: dict[str, str] = {}
    owner_name: dict[str, str] = {}
    if by_client:
        owner_name = client.users()   # имена всех (владелец сделки может быть из любого отдела)
    else:
        for did, nm in (groups or {}).items():
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

    # КОГОРТА 2026: классифицируем созданные сделки (early/real/lost), контракты = real ⊆ созданных.
    # Сигналы реализации из заказов 2026: какие сделки имеют заказ + Σ закупки на сделку + признак SUCCESS.
    order_parents = set(); buy_by_deal: dict[str, float] = defaultdict(float); succ_by_deal: dict[str, bool] = {}
    for o in orders:
        did = str(o.get("parentId2") or "")
        if not did:
            continue
        order_parents.add(did)
        buy_by_deal[did] += eur(o.get("opportunity"), o.get("currencyId"))
        if str(o.get("stageId", "")).endswith(":SUCCESS"):
            succ_by_deal[did] = True

    def blank():
        return {"deals": 0, "early": 0, "pipeline": 0.0, "real": 0, "sales": 0.0, "buy": 0.0, "closed": 0}
    g = defaultdict(blank)
    ppl = defaultdict(blank)
    deal_grp: dict[str, str] = {}; deal_own: dict[str, str] = {}
    deal_details: list[dict] = []
    order_details: list[dict] = []

    for d in created:
        owner = str(d.get("ASSIGNED_BY_ID"))
        did = str(d["ID"])
        grp = deal_group.get(did) if by_client else owner_group.get(owner)
        if not grp:
            continue
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))   # сумма сделки = ПРОДАЖА
        seq = _regno(d.get("TITLE"))
        is_real = (did in order_parents) or seq > 0   # в реализации = есть заказ или номер реализации
        cls = "lost" if sem == "F" else ("real" if is_real else "early")
        for b in (g[grp], ppl[owner]):
            b["deals"] += 1
            if cls == "early":
                b["early"] += 1; b["pipeline"] += amt
            elif cls == "real":
                b["real"] += 1; b["sales"] += amt; b["buy"] += buy_by_deal.get(did, 0.0)
                if succ_by_deal.get(did):
                    b["closed"] += 1
        if cls == "real":
            deal_grp[did] = grp; deal_own[did] = owner
        deal_details.append({"id": did, "t": (d.get("TITLE") or f'Сделка #{did}')[:90],
                             "amt": _money(amt), "raw": round(amt), "grp": grp,
                             "owner": owner, "own": owner_name.get(owner, owner),
                             "date": str(d.get("DATE_CREATE", ""))[:10],
                             "open": cls == "early", "cls": cls, "seq": seq})

    # заказы — только по сделкам-контрактам когорты 2026 (для drill «Закупка» и согласованных сумм)
    for o in orders:
        did = str(o.get("parentId2") or "")
        grp = deal_grp.get(did)
        if not grp:
            continue
        buy = eur(o.get("opportunity"), o.get("currencyId"))
        order_details.append({"id": str(o.get("id")), "t": (o.get("title") or f'Заказ #{o.get("id")}')[:90],
                              "amt": _money(buy), "raw": round(buy), "grp": grp,
                              "owner": deal_own.get(did, ""), "own": owner_name.get(deal_own.get(did, ""), ""),
                              "date": str(o.get("createdTime", ""))[:10],
                              "closed": str(o.get("stageId", "")).endswith(":SUCCESS")})

    def fmt_rows(src, label_key, label_get):
        out = []
        for key, v in src.items():
            sales = v["sales"]; margin = sales - v["buy"]
            out.append({
                label_key: label_get(key), "uid": str(key), "grp": owner_group.get(key, "") if label_key == "name" else "",
                "deals": v["deals"], "open": v["early"], "pipeline": _money(v["pipeline"]),
                "contracts": v["real"], "orders": v["real"],
                "sales": _money(sales), "salesRaw": round(sales),
                "buy": _money(v["buy"]), "buyRaw": round(v["buy"]),
                "margin": _money(margin), "marginRaw": round(margin),
                "marginPct": (round(margin / sales * 100) if sales else None),
                "conv": (round(v["real"] / v["deals"] * 100) if v["deals"] else 0),
                "closed": v["closed"],
            })
        out.sort(key=lambda r: r["salesRaw"], reverse=True)
        return out

    # client-режим: схлопнуть «хвост» направлений (вне топ-40 по продажам) в «Прочие», чтобы таблица не была на сотни строк
    if by_client and len(g) > 40:
        keep = {k for k, _ in sorted(g.items(), key=lambda kv: -kv[1]["sales"])[:40]}
        rest = {"deals": 0, "early": 0, "pipeline": 0.0, "real": 0, "sales": 0.0, "buy": 0.0, "closed": 0}
        for k in [x for x in g if x not in keep]:
            for fld in rest:
                rest[fld] += g[k][fld]
            del g[k]
        if any(rest.values()):
            g["Прочие"] = rest; keep.add("Прочие")
        for d in deal_details:
            if d["grp"] not in keep:
                d["grp"] = "Прочие"
        for o in order_details:
            if o["grp"] not in keep:
                o["grp"] = "Прочие"

    rows = fmt_rows(g, "group", lambda k: k)
    people = fmt_rows({u: v for u, v in ppl.items() if v["deals"]}, "name", lambda u: owner_name.get(u, u)) if with_people else []

    pipe_sum = sum(g[r["group"]]["pipeline"] for r in rows)
    sales_sum = sum(r["salesRaw"] for r in rows); buy_sum = sum(r["buyRaw"] for r in rows)
    margin_sum = sales_sum - buy_sum; tot_contracts = sum(r["contracts"] for r in rows)
    tot_deals = sum(r["deals"] for r in rows)
    tot = {"deals": tot_deals, "open": sum(r["open"] for r in rows),
           "pipeline": _money(pipe_sum), "contracts": tot_contracts,
           "sales": _money(sales_sum), "buy": _money(buy_sum), "margin": _money(margin_sum),
           "marginPct": (round(margin_sum / sales_sum * 100) if sales_sum else 0),
           "conv": (round(tot_contracts / tot_deals * 100) if tot_deals else 0),
           "closed": sum(r["closed"] for r in rows)}
    kpis = [
        ("Сделок", str(tot["deals"]), "создано (YTD)", "", "deals"),
        ("Активный пайплайн", tot["pipeline"], "Σ открытых продаж, €", "ok", "open"),
        ("Контрактов", str(tot_contracts), f"в реализации · конв. {tot['conv']}%", "ok", "real"),
        ("Выручка (продажи)", tot["sales"], "Σ сумм сделок-контрактов, €", "ok", "real"),
        ("Закупка", tot["buy"], "Σ заказов поставщикам, €", "amber", "orders"),
        ("Маржа", tot["margin"], f"{tot['marginPct']}% валовая", "ok" if margin_sum >= 0 else "warn", "real"),
    ]
    return {
        "label": f"01.01 – {today.strftime('%d.%m.%Y')}",
        "rows": rows,
        "people": people,
        "totals": tot,
        "kpis": [{"lbl": l, "val": vv, "meta": me, "clz": c, "drill": dr} for l, vv, me, c, dr in kpis],
        "byRevenue": [{"group": r["group"], "v": r["salesRaw"], "label": r["sales"]} for r in rows if r["salesRaw"] > 0][:12],
        "deals": deal_details,
        "orders": order_details,
    }
