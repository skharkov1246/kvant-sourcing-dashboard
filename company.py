"""Вкладка «Пульс компании» — управленческий инструмент (YTD).
Чистый расчёт из Bitrix → dict для window.__COMPANY__.

МОДЕЛЬ (важно): статус сделки «выиграна» (STAGE_SEMANTIC_ID=S) в портале почти не ставится.
Признак РЕАЛИЗАЦИИ = у сделки есть заказ поставщику (СП-172, parentId2) ИЛИ номер реализации
в начале названия («871. …»). Поэтому каждая созданная-2026 сделка классифицируется так:
  • lost  — проиграна (semantic F);
  • real  — в реализации (есть заказ/номер);
  • early — ранний пайплайн (открыта, ещё не в реализации).
Инвариант: создано = early + real + lost. «Нагрузка» сотрудника = активные (early) сделки,
а не все. Каждый показатель кликабелен (drill-down в список сделок).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

import config
from bitrix_client import BitrixClient

YEAR_START = "2026-01-01T00:00:00"
COHORT_START = "2025-01-01T00:00:00"  # когорты смотрят шире Пульса: 2025 + 2026
MLBL = {1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
        7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек"}


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


def _pctile(sorted_vals: list, p: float):
    """p-й перцентиль (линейная интерполяция) из ОТСОРТИРОВАННОГО списка. None если пусто."""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k); c = min(f + 1, len(sorted_vals) - 1)
    return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def compute(client: BitrixClient, *, as_of: dt.date | None = None,
            created: list[dict] | None = None, orders: list[dict] | None = None,
            deal_sale: dict | None = None) -> dict:
    today = as_of or dt.date.today()
    months = [f"2026-{m:02d}" for m in range(1, today.month + 1)]
    cur_part = today.strftime("%Y-%m")
    today_iso = today.isoformat()

    cats = client.categories()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)
    catname = lambda k: cats.get(str(k), f"cat#{k}")

    if created is None:
        created = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START},
            select=["ID", "TITLE", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "CLOSEDATE", "ASSIGNED_BY_ID"])
    if orders is None:
        orders = client.list_items(172, filter={">=createdTime": YEAR_START},
            select=["id", "title", "stageId", "opportunity", "currencyId", "createdTime", "parentId2"])
    orders = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    unames = client.users()
    order_parents = {str(o.get("parentId2")) for o in orders if o.get("parentId2")}
    buy_by_deal: dict[str, float] = defaultdict(float); first_order: dict[str, str] = {}
    for o in orders:
        did_o = str(o.get("parentId2") or "")
        if not did_o:
            continue
        buy_by_deal[did_o] += eur(o.get("opportunity"), o.get("currencyId"))
        ct = str(o.get("createdTime") or "")[:10]
        if ct and (did_o not in first_order or ct < first_order[did_o]):
            first_order[did_o] = ct
    deal_date = {str(d["ID"]): str(d.get("DATE_CREATE", ""))[:10] for d in created}
    if deal_sale is None:
        deal_sale = {str(d["ID"]): eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d in created}

    # классификация каждой созданной-2026 сделки: lost / real (в реализации) / early (ранний пайплайн)
    by_mon = Counter(); by_mon_real = Counter()
    by_cat = Counter(); by_cat_real = Counter(); by_cat_lost = Counter()
    by_mon_cat = defaultdict(Counter)
    early_n = real_n = lost_n = overdue_n = 0
    open_sum = 0.0
    owners = set(); created_ids = set()
    own = defaultdict(lambda: {"created": 0, "early": 0, "real": 0, "lost": 0, "pipe": 0.0})
    deal_details: list[dict] = []
    for d in created:
        did = str(d["ID"]); created_ids.add(did)
        mm = str(d.get("DATE_CREATE", ""))[:7]
        cid = str(d.get("CATEGORY_ID"))
        owner = str(d.get("ASSIGNED_BY_ID") or "")
        if owner:
            owners.add(owner)
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        seq = _regno(d.get("TITLE"))
        in_real = (did in order_parents) or seq > 0
        if sem == "F":
            cls = "lost"; lost_n += 1; by_cat_lost[cid] += 1
        elif in_real:
            cls = "real"; real_n += 1; by_mon_real[mm] += 1; by_cat_real[cid] += 1
        else:
            cls = "early"; early_n += 1; open_sum += amt
        cd = str(d.get("CLOSEDATE", ""))[:10]
        ovd = (sem not in ("S", "F")) and bool(cd) and cd < today_iso
        if ovd:
            overdue_n += 1
        by_mon[mm] += 1; by_cat[cid] += 1; by_mon_cat[mm][cid] += 1
        ob = own[owner]; ob["created"] += 1; ob[cls] += 1
        if cls == "early":
            ob["pipe"] += amt
        deal_details.append({
            "id": did, "t": (d.get("TITLE") or f"Сделка #{did}")[:90],
            "raw": round(amt), "amt": _money(amt), "mon": mm, "date": str(d.get("DATE_CREATE", ""))[:10],
            "cid": cid, "c": catname(cid),
            "uid": owner, "o": unames.get(owner, f"user#{owner}") if owner else "—",
            "cls": cls, "seq": seq, "sem": sem or "P", "ovd": ovd,
        })

    total = len(created)
    contract_deals = order_parents
    real_sale = sum(deal_sale.get(did, 0.0) for did in contract_deals)
    buy_sum = sum(eur(o.get("opportunity"), o.get("currencyId")) for o in orders)
    margin = real_sale - buy_sum
    conv = round(real_n / total * 100) if total else 0
    lostpct = round(lost_n / total * 100) if total else 0

    # рост клиентской базы по месяцам
    def monthly(method, field):
        out = {}
        for m in range(1, today.month + 1):
            lo = f"2026-{m:02d}-01T00:00:00"; hi = f"2026-{m+1:02d}-01T00:00:00" if m < 12 else "2027-01-01T00:00:00"
            out[f"2026-{m:02d}"] = client.count(method, {f">={field}": lo, f"<{field}": hi})
        return out
    comp = monthly("crm.company.list", "DATE_CREATE")
    cont = monthly("crm.contact.list", "DATE_CREATE")
    leads = monthly("crm.lead.list", "DATE_CREATE")
    rfqs = client.list_items(config.SPA_ENTITY_TYPE_ID, filter={"categoryId": config.SPA_CATEGORY_ID, ">=createdTime": YEAR_START}, select=["id", "parentId2"])
    rfq_parents = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
    covered = len(created_ids & rfq_parents)

    complete = [m for m in months if m != cur_part]
    avg_mo = round(sum(by_mon[m] for m in complete) / len(complete)) if complete else by_mon.get(cur_part, 0)
    prev = months[-2] if len(months) >= 2 else None
    mom = round((by_mon[cur_part] - by_mon[prev]) / by_mon[prev] * 100) if prev and by_mon.get(prev) else 0
    tender = round(by_cat.get("2", 0) / total * 100) if total else 0

    kpis = [
        ("Создано", str(total), "сделок YTD, все воронки", "", "all"),
        ("В работе", str(early_n), "активный пайплайн (ранние)", "", "early"),
        ("Активный пайплайн", _money(open_sum), "Σ ранних сделок, €", "ok", "early"),
        ("В реализации", str(real_n), f"конверсия {conv}% от созданных", "ok", "real"),
        ("Выручка (продажи)", _money(real_sale), "Σ сделок-контрактов, €", "ok", "real"),
        ("Маржа", _money(margin), f"{round(margin/real_sale*100) if real_sale else 0}% вал · закупка {_money(buy_sum)}", "ok" if margin >= 0 else "warn", "real"),
        ("Проиграно", str(lost_n), f"{lostpct}% от созданных", "warn", "lost"),
        ("Просрочено", str(overdue_n), "открытые с прошедшим сроком", "warn" if overdue_n else "", "overdue"),
    ]
    params = [
        ("Среднемесячно", str(avg_mo), "создаётся сделок/мес", "", ""),
        ("Рост MoM", f"{'+' if mom>=0 else ''}{mom}%", "посл. полный месяц к пред.", "ok" if mom >= 0 else "warn", ""),
        ("Ср. размер раннего", _money(open_sum / early_n if early_n else 0), "потенциал/сделку", "", "early"),
        ("Воронок активно", str(len([k for k, v in by_cat.items() if v])), "источников сделок", "", ""),
        ("Доля «Тендеры»", f"{tender}%", "тендеро-зависимость входа", "amber", "cat:2"),
        ("RFQ сорсинг", str(len(rfqs)), "запросов поставщикам YTD", "teal", ""),
        ("Покрытие сорсингом", f"{round(covered/total*100) if total else 0}%", "сделок с ≥1 RFQ", "teal", ""),
        ("Новых компаний", str(sum(comp.values())), "рост базы YTD", "", ""),
        ("Новых контактов", str(sum(cont.values())), "рост базы YTD", "", ""),
        ("Новых лидов", str(sum(leads.values())), "верх воронки YTD", "", ""),
    ]
    top_cats = [k for k, _ in by_cat.most_common(8)]
    top5 = [k for k, _ in by_cat.most_common(5)]
    by_owner = sorted(
        [{"uid": u, "name": unames.get(u, f"user#{u}"),
          "active": v["early"], "activeLbl": _money(v["pipe"]), "pipeRaw": round(v["pipe"]),
          "real": v["real"], "lost": v["lost"], "created": v["created"],
          "conv": round(v["real"] / v["created"] * 100) if v["created"] else 0}
         for u, v in own.items() if u and v["created"]],
        key=lambda x: -x["active"])

    # --- когортный анализ: сделки по кварталу/месяцу создания → успехи (реализация) ---
    # Окно шире Пульса: с 2025-01-01 (Пульс выше — строго YTD-2026). 2026 уже загружен,
    # дочитываем только 2025 и объединяем. Заказы поставщикам: 2025-е + уже загруженные 2026-е
    # (заказ всегда позже своей сделки, поэтому пол 2025 покрывает все заказы когорт ≥2025).
    pre_created = client.list_deals_fast(
        filter={">=DATE_CREATE": COHORT_START, "<DATE_CREATE": YEAR_START},
        select=["ID", "TITLE", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "ASSIGNED_BY_ID"])
    pre_orders = client.list_items(172, filter={">=createdTime": COHORT_START, "<createdTime": YEAR_START},
        select=["id", "stageId", "opportunity", "currencyId", "createdTime", "parentId2"])
    pre_orders = [o for o in pre_orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    coh_all_orders = orders + pre_orders
    coh_parents = {str(o.get("parentId2")) for o in coh_all_orders if o.get("parentId2")}
    coh_buy: dict[str, float] = defaultdict(float); coh_first: dict[str, str] = {}
    for o in coh_all_orders:
        did_o = str(o.get("parentId2") or "")
        if not did_o:
            continue
        coh_buy[did_o] += eur(o.get("opportunity"), o.get("currencyId"))
        ct = str(o.get("createdTime") or "")[:10]
        if ct and (did_o not in coh_first or ct < coh_first[did_o]):
            coh_first[did_o] = ct
    coh_date = dict(deal_date)
    coh_date.update({str(d["ID"]): str(d.get("DATE_CREATE", ""))[:10] for d in pre_created})

    def _coh_detail(d):
        did = str(d["ID"]); cid = str(d.get("CATEGORY_ID")); owner = str(d.get("ASSIGNED_BY_ID") or "")
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")); seq = _regno(d.get("TITLE"))
        cls = "lost" if sem == "F" else ("real" if (did in coh_parents or seq > 0) else "early")
        return {"id": did, "t": (d.get("TITLE") or f"Сделка #{did}")[:90], "raw": round(amt),
                "amt": _money(amt), "mon": str(d.get("DATE_CREATE", ""))[:7], "date": str(d.get("DATE_CREATE", ""))[:10],
                "cid": cid, "c": catname(cid),
                "uid": owner, "o": unames.get(owner, f"user#{owner}") if owner else "—",
                "cls": cls, "seq": seq, "sem": sem or "P", "ovd": False}
    # 2026-детали уже посчитаны выше (deal_details) — переиспользуем; для 2026-сделок членство в
    # coh_parents идентично order_parents (их заказы все ≥2026), поэтому cls совпадает.
    coh_details = deal_details + [_coh_detail(d) for d in pre_created]

    def _cblank(): return {"created": 0, "real": 0, "lost": 0, "early": 0, "sales": 0.0, "buy": 0.0,
                           "ttr": [], "csum": 0.0, "esum": 0.0, "lsum": 0.0}
    coh_q = defaultdict(_cblank); coh_m = defaultdict(_cblank)
    for d in coh_details:
        did = d["id"]; mm = d["mon"]
        if len(mm) < 7:
            continue
        q = f"{mm[:4]}-Q{(int(mm[5:7]) - 1) // 3 + 1}"; cls = d["cls"]
        for c in (coh_q[q], coh_m[mm]):
            c["created"] += 1; c[cls] += 1; c["csum"] += d["raw"]  # csum — сумма ВСЕХ созданных
            if cls == "real":
                c["sales"] += d["raw"]; c["buy"] += coh_buy.get(did, 0.0)
                fo, dd0 = coh_first.get(did), coh_date.get(did)
                if fo and dd0:
                    try:
                        days = (dt.date.fromisoformat(fo) - dt.date.fromisoformat(dd0)).days
                        if days >= 0:
                            c["ttr"].append(days)
                    except Exception:
                        pass
            elif cls == "early":
                c["esum"] += d["raw"]
            else:
                c["lsum"] += d["raw"]

    # --- распределение срока выхода в реализацию (TTR): за сколько дней доходят сделки ---
    # каждая реализованная сделка попадает ровно в один квартал → собираем все TTR из coh_q
    ttr_all = sorted(t for c in coh_q.values() for t in c["ttr"])
    _n = len(ttr_all)
    _THRESH = [10, 20, 30, 45, 60, 90, 120, 180, 270, 365]
    ttr_dist = {
        "n": _n,
        "thresholds": [{"d": t, "pct": round(sum(1 for x in ttr_all if x <= t) / _n * 100) if _n else 0,
                        "cnt": sum(1 for x in ttr_all if x <= t)} for t in _THRESH],
        "p25": _pctile(ttr_all, 25), "p50": _pctile(ttr_all, 50),
        "p75": _pctile(ttr_all, 75), "p90": _pctile(ttr_all, 90),
        "mean": round(sum(ttr_all) / _n) if _n else None,
        "max": ttr_all[-1] if ttr_all else None,
    }

    def _coh_rows(agg, lblfn):
        out = []
        for k in sorted(agg):
            c = agg[k]; sales = c["sales"]; margin = sales - c["buy"]; cr = c["created"]
            csum = c["csum"]
            out.append({
                "key": k, "label": lblfn(k), "created": cr, "real": c["real"], "lost": c["lost"], "early": c["early"],
                "conv": round(c["real"] / cr * 100) if cr else 0,
                "lostPct": round(c["lost"] / cr * 100) if cr else 0,
                "earlyPct": round(c["early"] / cr * 100) if cr else 0,
                "sales": _money(sales), "salesRaw": round(sales), "buy": _money(c["buy"]),
                "margin": _money(margin), "marginRaw": round(margin),
                "ttr": (round(sum(c["ttr"]) / len(c["ttr"])) if c["ttr"] else None),
                # измерение ПО СУММЕ (€): доля суммы созданных сделок по исходам — корреляция со штучной
                "createdSum": _money(csum), "createdSumRaw": round(csum),
                "convVal": round(sales / csum * 100) if csum else 0,        # реализация ÷ сумма создано
                "realValPct": round(sales / csum * 100) if csum else 0,
                "earlyValPct": round(c["esum"] / csum * 100) if csum else 0,
                "lostValPct": round(c["lsum"] / csum * 100) if csum else 0,
            })
        return out
    cohorts = {
        "label": f"01.01.2025 – {today.strftime('%d.%m.%Y')}",
        "quarters": _coh_rows(coh_q, lambda k: k.replace("-Q", " · Q")),
        "months": _coh_rows(coh_m, lambda k: f"{MLBL[int(k[5:7])]} {k[:4]}"),
        "deals": coh_details,
        "ttrDist": ttr_dist,
    }

    return {
        "label": f"01.01 – {today.strftime('%d.%m.%Y')}",
        "kpis": [{"lbl": l, "val": v, "meta": me, "clz": c, "drill": dr} for l, v, me, c, dr in kpis],
        "params": [{"lbl": l, "val": v, "meta": me, "clz": c, "drill": dr} for l, v, me, c, dr in params],
        "funnel": {"created": total, "early": early_n, "real": real_n, "lost": lost_n,
                   "realPct": conv, "lostPct": lostpct, "earlyPct": round(early_n / total * 100) if total else 0,
                   "sales": _money(real_sale), "buy": _money(buy_sum), "margin": _money(margin)},
        "byMon": [{"m": MLBL[int(m[5:7])], "mk": m, "v": by_mon[m], "real": by_mon_real[m]} for m in months],
        "byCat": [{"cat": catname(k), "cid": str(k), "v": by_cat[k], "real": by_cat_real[k], "lost": by_cat_lost[k],
                   "conv": round(by_cat_real[k] / by_cat[k] * 100) if by_cat[k] else 0} for k in top_cats],
        "matrix": {"months": [MLBL[int(m[5:7])] for m in months], "mks": list(months),
                   "rows": [{"cat": catname(k), "cid": str(k), "cells": [by_mon_cat[m].get(k, 0) for m in months]} for k in top5]},
        "byOwner": by_owner,
        "growth": [{"m": MLBL[int(m[5:7])], "comp": comp.get(m, 0), "cont": cont.get(m, 0), "leads": leads.get(m, 0)} for m in months],
        "deals": deal_details,
        "cohorts": cohorts,
    }
