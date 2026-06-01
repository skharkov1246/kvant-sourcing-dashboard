"""Движок метрик ядра дашборда. Чистые вычисления из уже выгруженных данных.

Вход — записи СП-166 за период, сделки периода, индекс родительских сделок, состав отдела.
Выход — единый dict `metrics`, который шаблон кладёт в window.__DATA__.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean

from period import Period, parse_dt
from stages import BUCKETS, CLOSED, classify_stage, deal_reached_tkp


def _round(x: float, n: int = 1) -> float:
    return round(x, n)


def _pct(part: int, whole: int) -> int:
    return round(part / whole * 100) if whole else 0


def _days(created: str, moved: str) -> int | None:
    a, b = parse_dt(created), parse_dt(moved)
    if not a or not b:
        return None
    return max((b - a).days, 0)


def build(
    period: Period,
    rfqs: list[dict],
    deal_index: dict[str, dict],
    period_deals: list[dict],
    dept_a_ids: set[str],
    names: dict[str, str],
    since: dict[str, str],
    deal_stage_names: dict[str, str],
    category_names: dict[str, str],
) -> dict:
    weeks = period.weeks
    n_weeks = len(weeks)

    # ---- индексация RFQ
    by_user: dict[str, list[dict]] = defaultdict(list)
    for r in rfqs:
        by_user[str(r.get("assignedById"))].append(r)

    def deal_state(parent_id) -> str:
        """early | tkp | lost  — состояние родительской сделки RFQ."""
        d = deal_index.get(str(parent_id)) if parent_id else None
        if not d:
            return "early"
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        if sem == "F":
            return "lost"
        if deal_reached_tkp(d.get("STAGE_ID", ""), sem, deal_stage_names.get(d.get("STAGE_ID", ""))):
            return "tkp"
        return "early"

    # ---- per-sourcer (блок A)
    sourcers_a: list[dict] = []
    for uid in dept_a_ids:
        items = by_user.get(uid, [])
        if not items:
            continue
        b = Counter(classify_stage(r.get("stageId", "")) for r in items)
        buckets = {k: b.get(k, 0) for k in BUCKETS}
        c = len(items)
        closed = sum(buckets[k] for k in CLOSED)
        durs = [d for r in items if classify_stage(r.get("stageId", "")) in CLOSED
                and (d := _days(r.get("createdTime", ""), r.get("movedTime", ""))) is not None]
        wk = [0] * n_weeks
        for r in items:
            wi = period.week_index(parse_dt(r.get("createdTime", "")))
            if wi is not None:
                wk[wi] += 1
        tkp = sum(1 for r in items if deal_state(r.get("parentId2")) == "tkp")
        details = []
        for r in items:
            wi = period.week_index(parse_dt(r.get("createdTime", "")))
            details.append({
                "subj": (r.get("title") or "—")[:90],
                "sup": r.get("_supplier", "—"),
                "st": classify_stage(r.get("stageId", "")),
                "wk": wi if wi is not None else -1,
                "dt": (r.get("createdTime") or "")[:10],
            })
        by_supplier = [{"sup": s, "n": n} for s, n in Counter(d["sup"] for d in details).most_common()]
        sourcers_a.append({
            "id": uid,
            "n": names.get(uid, f"user#{uid}"),
            "since": since.get(uid, ""),
            "c": c,
            "perDay": _round(c / period.days) if period.days else 0,
            "wk": wk,
            "buckets": buckets,
            "closed": closed,
            "kp": buckets["selected"],
            "refusedCol": buckets["refused"] + buckets["other"],
            "noAnswer": buckets["no_answer"],
            "avgDays": _round(mean(durs)) if durs else 0,
            "tkp": tkp,
            "tkpP": _pct(tkp, c),
            "details": details,
            "bySupplier": by_supplier,
        })
    sourcers_a.sort(key=lambda s: s["c"], reverse=True)

    # ---- недельная динамика A vs B
    weekly = []
    for i, w in enumerate(weeks):
        a = b = 0
        for r in rfqs:
            if period.week_index(parse_dt(r.get("createdTime", ""))) == i:
                if str(r.get("assignedById")) in dept_a_ids:
                    a += 1
                else:
                    b += 1
        weekly.append({"w": w.label, "d": w.days, "A": a, "B": b})

    total = len(rfqs)
    a_total = sum(1 for r in rfqs if str(r.get("assignedById")) in dept_a_ids)
    open_all = sum(1 for r in rfqs if classify_stage(r.get("stageId", "")) not in CLOSED)
    closed_all = total - open_all

    # блок A агрегаты для KPI
    closed_a = sum(s["closed"] for s in sourcers_a)
    kp_a = sum(s["kp"] for s in sourcers_a)

    # ---- цепочка: исход связанных сделок (по всем RFQ) + воронки
    chain = Counter(deal_state(r.get("parentId2")) for r in rfqs)
    cat_counter: Counter = Counter()
    for r in rfqs:
        d = deal_index.get(str(r.get("parentId2")))
        if d:
            cat_counter[str(d.get("CATEGORY_ID"))] += 1
    cat_list = [
        {"cat": category_names.get(cid, f"cat#{cid}"), "v": v, "p": _pct(v, total)}
        for cid, v in cat_counter.most_common(8)
    ]
    tkp_all = chain.get("tkp", 0)

    # ---- покрытие сделок периода сорсингом
    rfq_by_deal: Counter = Counter(str(r.get("parentId2")) for r in rfqs if r.get("parentId2"))
    period_ids = [str(d["ID"]) for d in period_deals]
    covered = [did for did in period_ids if rfq_by_deal.get(did, 0) > 0]
    n_deals = len(period_ids)
    with_req = len(covered)

    def hbucket(n: int) -> str:
        return "1 пост." if n == 1 else "2–3" if n <= 3 else "4–5" if n <= 5 else "6+"

    hist_c = Counter(hbucket(rfq_by_deal[d]) for d in covered)
    hist = [{"l": l, "v": hist_c.get(l, 0)} for l in ("1 пост.", "2–3", "4–5", "6+")]

    cat_of = {str(d["ID"]): str(d.get("CATEGORY_ID")) for d in period_deals}
    by_cat = defaultdict(lambda: {"deals": 0, "withReq": 0, "reqs": 0})
    for d in period_deals:
        cid = str(d.get("CATEGORY_ID"))
        by_cat[cid]["deals"] += 1
        did = str(d["ID"])
        if rfq_by_deal.get(did, 0) > 0:
            by_cat[cid]["withReq"] += 1
            by_cat[cid]["reqs"] += rfq_by_deal[did]
    cov_by_cat = []
    for cid, v in sorted(by_cat.items(), key=lambda kv: kv[1]["deals"], reverse=True):
        cov_by_cat.append({
            "cat": category_names.get(cid, f"cat#{cid}"),
            "deals": v["deals"],
            "withReq": v["withReq"],
            "covPct": _pct(v["withReq"], v["deals"]),
            "reqs": v["reqs"],
            "avgSup": _round(v["reqs"] / v["withReq"]) if v["withReq"] else 0,
        })

    avg_suppliers = _round(mean(rfq_by_deal[d] for d in covered)) if covered else 0

    return {
        "period": {
            "label": period.label,
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
            "days": period.days,
        },
        "kpi": {
            "total": total,
            "deptA": a_total,
            "outside": total - a_total,
            "respCount": len({str(r.get("assignedById")) for r in rfqs}),
            "openCount": open_all,
            "inWorkPct": _pct(open_all, total),
            "closedCountA": closed_a,
            "kpPctOfClosedA": _pct(kp_a, closed_a),
            "tkpPct": _pct(tkp_all, total),
        },
        "weekly": weekly,
        "sourcersA": sourcers_a,
        "chain": {
            "early": chain.get("early", 0),
            "tkp": tkp_all,
            "lost": chain.get("lost", 0),
        },
        "catList": cat_list,
        "coverage": {
            "deals": n_deals,
            "withReq": with_req,
            "withoutReq": n_deals - with_req,
            "covPct": _pct(with_req, n_deals),
            "avgSuppliers": avg_suppliers,
            "hist": hist,
            "byCat": cov_by_cat,
        },
        "totals_closed_all": closed_all,
    }
