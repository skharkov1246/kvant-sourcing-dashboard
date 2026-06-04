"""Данные вкладки «Пульс компании» (YTD): динамика создания сделок по воронкам + 20 параметров.
Чистый расчёт из Bitrix → dict для window.__COMPANY__. Используется main.py для второй вкладки борда.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

import config
from bitrix_client import BitrixClient

YEAR_START = "2026-01-01T00:00:00"
MLBL = {1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
        7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек"}


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
    months = [f"2026-{m:02d}" for m in range(1, today.month + 1)]
    cur_part = today.strftime("%Y-%m")

    cats = client.categories()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    if created is None:
        created = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START},
            select=["ID", "TITLE", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "ASSIGNED_BY_ID"])
    unames = client.users()
    catname = lambda k: cats.get(str(k), f"cat#{k}")
    by_mon = Counter(); by_cat = Counter(); by_mon_cat = defaultdict(Counter)
    openc = 0; open_sum = 0.0; won_c = 0; lost_c = 0; owners = set(); created_ids = set()
    deal_details: list[dict] = []
    for d in created:
        created_ids.add(str(d["ID"]))
        mm = str(d.get("DATE_CREATE", ""))[:7]
        cid = str(d.get("CATEGORY_ID"))
        by_mon[mm] += 1; by_cat[cid] += 1; by_mon_cat[mm][cid] += 1
        owner = str(d.get("ASSIGNED_BY_ID") or "")
        if owner:
            owners.add(owner)
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        is_open = sem not in ("S", "F")
        amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        if is_open:
            openc += 1; open_sum += amt
        elif sem == "S":
            won_c += 1
        else:  # sem == "F"
            lost_c += 1
        deal_details.append({"id": str(d["ID"]), "t": (d.get("TITLE") or f'Сделка #{d["ID"]}')[:90],
                             "raw": round(amt), "amt": _money(amt), "mon": mm, "cid": cid, "c": catname(cid),
                             "o": unames.get(owner, f"user#{owner}") if owner else "—", "uid": owner,
                             "sem": sem or "P", "op": is_open})
    # воронка считается по ЕДИНОЙ когорте «создано в 2026»: created = open + won_c + lost_c (инвариант)
    lost = lost_c

    # «Заказы» (СП-172) = выигранные сделки в исполнении → реальная контрактная выручка.
    # Исключаем проигранные (стадия …:FAIL) — это не контрактная выручка.
    if orders is None:
        orders = client.list_items(172, filter={">=createdTime": YEAR_START},
                                   select=["id", "title", "stageId", "opportunity", "currencyId", "createdTime"])
    orders = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    orders_sum = sum(eur(o.get("opportunity"), o.get("currencyId")) for o in orders)
    orders_closed = sum(1 for o in orders if str(o.get("stageId", "")).endswith(":SUCCESS"))

    # overdue среди открытых (по созданным в 2026); CLOSEDATE с временем → сравниваем с концом дня
    overdue = client.count_deals({"<CLOSEDATE": today.isoformat() + "T23:59:59", "!=STAGE_SEMANTIC_ID": ["S", "F"], ">=DATE_CREATE": YEAR_START})

    rfqs = client.list_items(config.SPA_ENTITY_TYPE_ID, filter={"categoryId": config.SPA_CATEGORY_ID, ">=createdTime": YEAR_START},
                             select=["id", "parentId2"])
    rfq_parents = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
    covered = len(created_ids & rfq_parents)

    def monthly(method, field):
        out = {}
        for m in range(1, today.month + 1):
            lo = f"2026-{m:02d}-01T00:00:00"; hi = f"2026-{m+1:02d}-01T00:00:00" if m < 12 else "2027-01-01T00:00:00"
            out[f"2026-{m:02d}"] = client.count(method, {f">={field}": lo, f"<{field}": hi})
        return out
    comp = monthly("crm.company.list", "DATE_CREATE")
    cont = monthly("crm.contact.list", "DATE_CREATE")
    leads = monthly("crm.lead.list", "DATE_CREATE")

    total = len(created)
    complete = [m for m in months if m != cur_part]
    avg_mo = round(sum(by_mon[m] for m in complete) / len(complete)) if complete else by_mon.get(cur_part, 0)
    prev = months[-2] if len(months) >= 2 else None
    mom = round((by_mon[cur_part] - by_mon[prev]) / by_mon[prev] * 100) if prev and by_mon.get(prev) else 0
    tender = round(by_cat.get("2", 0) / total * 100) if total else 0
    name = lambda k: cats.get(k, k)

    params = [
        ("Сделок создано (YTD)", str(total), "клиентских, все воронки", ""),
        ("Среднемесячно", str(avg_mo), "сделок/мес (полные)", ""),
        ("Рост MoM", f"{'+' if mom>=0 else ''}{mom}%", "посл. полный к пред.", "ok" if mom >= 0 else "warn"),
        ("Воронок активно", str(len([k for k, v in by_cat.items() if v])), "источников сделок", ""),
        ("Доля «Тендеры»", f"{tender}%", "тендеро-зависимость", "amber"),
        ("В работе (open)", str(openc), "открытых сделок", ""),
        ("Активный пайплайн", _money(open_sum), "Σ открытых, €", "ok"),
        ("Ср. размер открытой", _money(open_sum / openc if openc else 0), "потенциал/сделку", ""),
        ("Заказы (выигр.)", str(len(orders)), "сделки в исполнении (СП-172)", "ok"),
        ("Σ заказов", _money(orders_sum), "контрактная выручка, €", "ok"),
        ("Закрыто заказов", str(orders_closed), "поставлено и оплачено", "ok"),
        ("Ср. чек заказа", _money(orders_sum / len(orders) if orders else 0), "€/заказ", ""),
        ("Закрыто-минус", str(lost), "проигрыш (из созданных 2026)", "warn"),
        ("Просрочены (open)", str(overdue), "CLOSEDATE прошёл", "warn" if overdue else ""),
        ("Новых компаний", str(sum(comp.values())), "рост базы YTD", ""),
        ("Новых контактов", str(sum(cont.values())), "рост базы YTD", ""),
        ("Новых лидов", str(sum(leads.values())), "верх воронки YTD", ""),
        ("RFQ сорсинг", str(len(rfqs)), "запросов поставщикам", "teal"),
        ("Покрытие сорсингом", f"{round(covered/total*100) if total else 0}%", "сделок с ≥1 RFQ", "teal"),
        ("Исполнителей", str(len(owners)), "ответственных по сделкам", ""),
    ]
    top_cats = by_cat.most_common(6)
    top5 = [k for k, _ in by_cat.most_common(5)]
    own_count = Counter(d["uid"] for d in deal_details if d["uid"])
    own_open = defaultdict(float)
    for d in deal_details:
        if d["op"] and d["uid"]:
            own_open[d["uid"]] += d["raw"]
    by_owner = sorted(
        [{"uid": u, "name": unames.get(u, f"user#{u}"), "deals": c,
          "open": sum(1 for x in deal_details if x["uid"] == u and x["op"]),
          "openLbl": _money(own_open[u])} for u, c in own_count.items()],
        key=lambda x: -x["deals"])
    return {
        "label": f"01.01 – {today.strftime('%d.%m.%Y')}",
        "byMon": [{"m": MLBL[int(m[5:7])], "mk": m, "v": by_mon[m]} for m in months],
        "byCat": [{"cat": name(k), "cid": str(k), "v": v, "p": round(v / total * 100) if total else 0} for k, v in top_cats],
        "matrix": {"months": [MLBL[int(m[5:7])] for m in months], "mks": list(months),
                   "rows": [{"cat": name(k), "cid": str(k), "cells": [by_mon_cat[m].get(k, 0) for m in months]} for k in top5]},
        "params": [{"lbl": l, "val": v, "meta": me, "clz": c} for l, v, me, c in params],
        "flow": {"created": total, "open": openc, "won": won_c, "lost": lost_c, "orders": len(orders)},
        "growth": [{"m": MLBL[int(m[5:7])], "comp": comp.get(m, 0), "cont": cont.get(m, 0), "leads": leads.get(m, 0)} for m in months],
        "deals": deal_details,
        "byOwner": by_owner,
    }
