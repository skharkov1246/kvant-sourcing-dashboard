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
    user_depts: dict[str, str] | None = None,
    service_ids: set[str] | None = None,
) -> dict:
    weeks = period.weeks
    n_weeks = len(weeks)

    # ---- КОМУ ЗАСЧИТАТЬ ЗАПРОС
    # В портале работает воронка пресейла: карточки заводит робот от имени
    # служебной учётной записи. Если считать исполнителем того, кто записан в
    # карточке, работа сорсера, запустившего кампанию, исчезает из его статистики
    # и оседает на роботе — а вместе с ней и возможность мерить людей.
    #
    # Инициатор восстанавливается цепочкой: ответственный карточки → владелец
    # родительской сделки → автор карточки. Первый, кто не служебный, и есть
    # исполнитель. Родительская сделка здесь ключевая: её ведёт живой сорсер,
    # и именно в его очередь встаёт запрос, кем бы он ни был заведён.
    #
    # Пока список служебных записей пуст, цепочка возвращает ответственного —
    # то есть ровно прежнее поведение. Разница видна в разделе «Кто заводит
    # запросы» до того, как список заполнен: сначала мерим, потом применяем.
    service = set(service_ids or ())

    def owner_of(r: dict) -> tuple[str, str]:
        """(идентификатор исполнителя, чем определён)."""
        a = str(r.get("assignedById") or "")
        if a and a not in service:
            return a, "ответственный"
        d = deal_index.get(str(r.get("parentId2"))) if r.get("parentId2") else None
        o = str((d or {}).get("ASSIGNED_BY_ID") or "")
        if o and o not in service:
            return o, "владелец сделки"
        c = str(r.get("createdBy") or "")
        if c and c not in service:
            return c, "автор карточки"
        return "", "не определён"

    # Исполнитель считается один раз и кладётся в саму запись: дальше по модулю
    # он нужен в семи местах, и расхождение между ними уже однажды дало разные
    # цифры нагрузки на одних и тех же людей.
    for r in rfqs:
        r["_owner"], r["_ownerBy"] = owner_of(r)

    # ---- индексация RFQ
    by_user: dict[str, list[dict]] = defaultdict(list)
    for r in rfqs:
        by_user[r["_owner"]].append(r)

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
                "id": str(r.get("id")),
                "subj": (r.get("title") or "—")[:90],
                "sup": r.get("_supplier", "—"),
                "st": classify_stage(r.get("stageId", "")),
                "wk": wi if wi is not None else -1,
                "dt": (r.get("createdTime") or "")[:10],
                "dtx": (r.get("createdTime") or ""),   # полная метка для хронологической сортировки
            })
        by_supplier = [{"sup": s, "n": n} for s, n in Counter(d["sup"] for d in details).most_common()]
        nsup = sum(1 for x in by_supplier if x["sup"] not in ("—", "", None))
        sourcers_a.append({
            "nsup": nsup,
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

    # ---- разнообразие поставщиков (блок A): топ запрошенных + общий охват
    sup_a = Counter()
    for r in rfqs:
        if r["_owner"] in dept_a_ids:
            sup_a[r.get("_supplier") or "—"] += 1
    named = Counter({s: n for s, n in sup_a.items() if s not in ("—", "", None)})
    _topn = named.most_common(12)
    _named_total = sum(named.values())
    supplier_mix = {
        "top": [{"sup": s, "n": n, "p": _pct(n, _named_total)} for s, n in _topn],
        "other": _named_total - sum(n for _, n in _topn),
        "distinct": len(named),
        "total": _named_total,
        "unknown": sup_a.get("—", 0),
    }

    # ---- недельная динамика A vs B
    weekly = []
    for i, w in enumerate(weeks):
        a = b = 0
        for r in rfqs:
            if period.week_index(parse_dt(r.get("createdTime", ""))) == i:
                if r["_owner"] in dept_a_ids:
                    a += 1
                else:
                    b += 1
        weekly.append({"w": w.label, "d": w.days, "A": a, "B": b})

    total = len(rfqs)
    a_total = sum(1 for r in rfqs if r["_owner"] in dept_a_ids)
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
    # Запрос ОЖИДАЕТСЯ только у живых оценённых сделок: есть сумма (оценили) и сделка не закрыта-минус
    # (не отказ/отмена/проигрыш, статус ≠ F), и это не сделка-RFQ самого сорсинга (тайтл с «RFQ»).
    # €0 (ещё не оценили) / бюджетные оценки / отказы — это «запрос не требуется (пока)», а не пропуск.
    def _req_expected(d):
        t = str(d.get("TITLE") or "")
        if float(d.get("OPPORTUNITY") or 0) <= 0:
            return False
        if (d.get("STAGE_SEMANTIC_ID") or "").upper() == "F":
            return False
        if t.strip().upper().startswith("RFQ"):
            return False
        if "test" in t.lower() or "тест" in t.lower():
            return False
        return True
    rfq_by_deal: Counter = Counter(str(r.get("parentId2")) for r in rfqs if r.get("parentId2"))
    expected = [d for d in period_deals if _req_expected(d)]
    period_ids = [str(d["ID"]) for d in expected]
    covered = [did for did in period_ids if rfq_by_deal.get(did, 0) > 0]
    n_deals = len(period_ids)
    with_req = len(covered)

    def hbucket(n: int) -> str:
        return "1 пост." if n == 1 else "2–3" if n <= 3 else "4–5" if n <= 5 else "6+"

    hist_c = Counter(hbucket(rfq_by_deal[d]) for d in covered)
    hist = [{"l": l, "v": hist_c.get(l, 0)} for l in ("1 пост.", "2–3", "4–5", "6+")]

    by_cat = defaultdict(lambda: {"deals": 0, "withReq": 0, "reqs": 0})
    for d in expected:
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

    # ---- КТО ЗАВОДИТ ЗАПРОСЫ
    # Раньше в дашборде было одно число «вне блока A», и считалось оно по
    # ОТВЕТСТВЕННОМУ. Но грузит очередь не тот, на кого карточку записали, а тот,
    # кто её завёл: запрос может создать инженер или КАМ и тут же назначить
    # сорсера ответственным. Поэтому здесь разбор по createdBy, с подразделением.
    depts = user_depts or {}
    made: dict[str, dict] = {}
    # разбор карточек, заведённых служебными записями: кому они в итоге засчитаны
    via_service = 0
    service_resolved = 0
    by_service: Counter = Counter()
    resolved_to: Counter = Counter()
    how: Counter = Counter()
    for r in rfqs:
        uid = str(r.get("createdBy") or "")
        rec = made.setdefault(uid, {"n": 0, "tkp": 0, "toSourcing": 0})
        rec["n"] += 1
        if deal_state(r.get("parentId2")) == "tkp":
            rec["tkp"] += 1
        owner, by_what = r["_owner"], r["_ownerBy"]
        if owner in dept_a_ids:
            rec["toSourcing"] += 1
        if uid in service:
            via_service += 1
            by_service[uid] += 1
            how[by_what] += 1
            if owner:
                service_resolved += 1
                resolved_to[owner] += 1

    by_creator: list[dict] = []
    n_sourcing = n_outside = n_auto = 0
    handoff = 0
    by_dept_cnt: Counter = Counter()
    for uid, rec in made.items():
        auto = uid in ("", "0", "None") or uid in service
        in_src = (not auto) and uid in dept_a_ids
        if uid in service:
            dept = "служебная запись (робот пресейла)"
        elif auto:
            dept = "автоматизация портала"
        else:
            dept = depts.get(uid) or "подразделение не указано"
        if auto:
            n_auto += rec["n"]
        elif in_src:
            n_sourcing += rec["n"]
        else:
            n_outside += rec["n"]
            handoff += rec["toSourcing"]
        by_dept_cnt[dept] += rec["n"]
        if uid in service:
            who = names.get(uid) or f"служебная запись #{uid}"
        elif auto:
            who = "автоматизация портала"
        else:
            who = names.get(uid, f"user#{uid}")
        by_creator.append({
            "uid": uid,
            "name": who,
            "dept": dept,
            "src": bool(in_src),
            "auto": bool(auto),
            "n": rec["n"],
            "pct": _pct(rec["n"], total),
            "tkpPct": _pct(rec["tkp"], rec["n"]),
            "toSourcingPct": _pct(rec["toSourcing"], rec["n"]),
        })
    by_creator.sort(key=lambda x: x["n"], reverse=True)
    dept_max = max(by_dept_cnt.values(), default=1)
    by_dept = [{"dept": d, "n": n, "pct": _pct(n, total), "w": round(n / dept_max * 100)}
               for d, n in by_dept_cnt.most_common()]

    # Кандидаты в служебные записи. Служебную запись от человека отличает то, что
    # она не числится ни в одном подразделении и при этом заводит много карточек:
    # живой сотрудник без подразделения — это непорядок в справочнике, а не поток
    # в тысячу запросов. Список не применяется сам: он показывается владельцу,
    # чтобы тот внёс подтверждённые записи в SERVICE_ACCOUNT_IDS. Автоматически
    # выключать людей из статистики нельзя — цена ошибки здесь выше цены ожидания.
    cand_floor = max(10, round(total * 0.02))
    candidates = [c for c in by_creator
                  if not c["auto"] and c["uid"] not in service
                  and c["dept"] == "подразделение не указано" and c["n"] >= cand_floor]

    # Разрез по подразделению ИСПОЛНИТЕЛЯ, а не автора карточки. Первый отвечает
    # на вопрос «чья это работа», второй — «кто её завёл». Для карточек робота
    # они расходятся: завела служебная запись, работает по ним живой отдел.
    owner_dept_cnt: Counter = Counter()
    for r in rfqs:
        u = r["_owner"]
        owner_dept_cnt[(depts.get(u) or "подразделение не указано") if u
                       else "исполнитель не определён"] += 1
    odept_max = max(owner_dept_cnt.values(), default=1)
    by_owner_dept = [{"dept": d, "n": n, "pct": _pct(n, total),
                      "w": round(n / odept_max * 100)}
                     for d, n in owner_dept_cnt.most_common()]

    service_list = [{"uid": u, "name": names.get(u) or f"служебная запись #{u}", "n": n,
                     "pct": _pct(n, total)} for u, n in by_service.most_common()]
    resolved_list = [{"uid": u, "name": names.get(u, f"user#{u}"), "n": n,
                      "dept": depts.get(u) or "подразделение не указано",
                      "src": u in dept_a_ids} for u, n in resolved_to.most_common(30)]

    return {
        "origin": {
            "byCreator": by_creator,
            "outsideCreators": [c for c in by_creator if not c["src"] and not c["auto"]],
            "byDept": by_dept,
            "byOwnerDept": by_owner_dept,
            "service": service_list,
            "serviceCandidates": candidates,
            "resolvedTo": resolved_list,
            "resolvedHow": [{"how": k, "n": v, "pct": _pct(v, via_service)}
                            for k, v in how.most_common()],
            "summary": {
                "total": total,
                "sourcing": n_sourcing,
                "outside": n_outside,
                "auto": n_auto,
                "sourcingPct": _pct(n_sourcing, total),
                "outsidePct": _pct(n_outside, total),
                "autoPct": _pct(n_auto, total),
                "handoff": handoff,
                "handoffPct": _pct(handoff, n_outside),
                "people": len([c for c in by_creator if not c["auto"]]),
                "outsidePeople": len([c for c in by_creator if not c["src"] and not c["auto"]]),
                "viaService": via_service,
                "viaServicePct": _pct(via_service, total),
                "serviceResolved": service_resolved,
                "serviceResolvedPct": _pct(service_resolved, via_service),
                "serviceAccounts": len(service_list),
                "serviceConfigured": len(service),
                "candidates": len(candidates),
                "candidateFloor": cand_floor,
            },
        },
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
            "respCount": len({r["_owner"] for r in rfqs if r["_owner"]}),
            "openCount": open_all,
            "inWorkPct": _pct(open_all, total),
            "closedCountA": closed_a,
            "kpPctOfClosedA": _pct(kp_a, closed_a),
            "tkpPct": _pct(tkp_all, total),
        },
        "weekly": weekly,
        "sourcersA": sourcers_a,
        "supplierMix": supplier_mix,
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
