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


def compute(client: BitrixClient, *, as_of: dt.date | None = None) -> dict:
    today = as_of or dt.date.today()
    months = [f"2026-{m:02d}" for m in range(1, today.month + 1)]
    cur_part = today.strftime("%Y-%m")

    cats = client.categories()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    created = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START},
        select=["ID", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "ASSIGNED_BY_ID"])
    by_mon = Counter(); by_cat = Counter(); by_mon_cat = defaultdict(Counter)
    openc = 0; open_sum = 0.0; owners = set(); created_ids = set()
    for d in created:
        created_ids.add(str(d["ID"]))
        mm = str(d.get("DATE_CREATE", ""))[:7]
        by_mon[mm] += 1; by_cat[str(d.get("CATEGORY_ID"))] += 1; by_mon_cat[mm][str(d.get("CATEGORY_ID"))] += 1
        if d.get("ASSIGNED_BY_ID"):
            owners.add(str(d["ASSIGNED_BY_ID"]))
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        if sem not in ("S", "F"):
            openc += 1; open_sum += eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))

    won = client.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "S", ">=CLOSEDATE": YEAR_START},
        select=["ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "CLOSEDATE"])
    won_sum = sum(eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d in won)
    cyc = []
    for d in won:
        try:
            a = dt.date.fromisoformat(str(d["DATE_CREATE"])[:10]); b = dt.date.fromisoformat(str(d["CLOSEDATE"])[:10])
            cyc.append((b - a).days)
        except Exception:
            pass
    lost = client.count_deals({"STAGE_SEMANTIC_ID": "F", ">=CLOSEDATE": YEAR_START})

    # overdue среди открытых (по созданным в 2026)
    overdue = client.count_deals({"<CLOSEDATE": today.isoformat(), "!=STAGE_SEMANTIC_ID": ["S", "F"], ">=DATE_CREATE": YEAR_START})

    rfqs = client.list_items(config.SPA_ENTITY_TYPE_ID, filter={"categoryId": config.SPA_CATEGORY_ID, ">=createdTime": YEAR_START},
                             select=["id", "parentId2"])
    rfq_parents = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
    covered = len(created_ids & rfq_parents)

    def monthly(method, field):
        out = {}
        for m in range(1, today.month + 1):
            lo = f"2026-{m:02d}-01T00:00:00"; hi = f"2026-{m+1:02d}-01T00:00:00" if m < 12 else "2027-01-01T00:00:00"
            out[f"2026-{m:02d}"] = client.call(method, {"filter": {f">={field}": lo, f"<{field}": hi}, "select": ["ID"], "start": 0})
        return out
    comp = {k: (v or {}).get("total") or 0 if isinstance(v, dict) else 0 for k, v in monthly("crm.company.list", "DATE_CREATE").items()}
    cont = {k: (v or {}).get("total") or 0 if isinstance(v, dict) else 0 for k, v in monthly("crm.contact.list", "DATE_CREATE").items()}
    leads = {k: (v or {}).get("total") or 0 if isinstance(v, dict) else 0 for k, v in monthly("crm.lead.list", "DATE_CREATE").items()}

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
        ("Выиграно (YTD)", str(len(won)), "по дате закрытия", "ok"),
        ("Σ выручки (won)", _money(won_sum), "закрыто в плюс, €", "ok"),
        ("Ср. чек победы", _money(won_sum / len(won) if won else 0), "won, €", ""),
        ("Ср. цикл победы", f"{round(sum(cyc)/len(cyc)) if cyc else 0} дн", "создание→закрытие", ""),
        ("Закрыто-минус", str(lost), "отсев/проигрыш", "warn"),
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
    return {
        "label": f"01.01 – {today.strftime('%d.%m.%Y')}",
        "byMon": [{"m": MLBL[int(m[5:7])], "v": by_mon[m]} for m in months],
        "byCat": [{"cat": name(k), "v": v, "p": round(v / total * 100) if total else 0} for k, v in top_cats],
        "matrix": {"months": [MLBL[int(m[5:7])] for m in months],
                   "rows": [{"cat": name(k), "cells": [by_mon_cat[m].get(k, 0) for m in months]} for k in top5]},
        "params": [{"lbl": l, "val": v, "meta": me, "clz": c} for l, v, me, c in params],
        "flow": {"created": total, "open": openc, "won": len(won), "lost": lost},
        "growth": [{"m": MLBL[int(m[5:7])], "comp": comp.get(m, 0), "cont": cont.get(m, 0), "leads": leads.get(m, 0)} for m in months],
    }
