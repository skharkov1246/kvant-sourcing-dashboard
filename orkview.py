"""Раздел «ork» вкладки «Реализация»: дашборд руководителя ОРК по регламенту (решения
владельца 26.10.2026). Чистые функции поверх уже посчитанного — заказов, приведённых к
фактам (contracts.life_orders), строк сделок, очереди lifecycle и сегментов S1–S10 по всем
переходам (ork.segments). Портал не читает.

Подвкладки:
  week    — неделя руководителя ОРК: W1 приёмка, W2 сверх нормы (только шаги ОРК), W3 зона
            риска, W4 эскалации и паузы (нет данных), W5 контрольные точки недели; ведомость
            13 шагов, сверх нормы по исполнителям, ждут смежников, гигиена карточек;
  month   — M1 своевременность S1/S2/S3/S6, M2 полнота карточки, M3 загрузка, доставка в
            срок по месяцам (результат поставщика и логистики), показатели Ан. §4 без данных;
  quarter — Q1 первичный срок поставщика, Q2 сроки S5/S7/S8 и цикла (медиана и P80), Q3
            схема оплаты, скоркард без данных — «Нет данных», индекс и A/B/C скрыты;
  how     — по каждому показателю формула, поля, вердикт ворот, n и покрытие; нарушения
            порядка; заполненность артефактов; поля «добавить» с адресатом.

Каждая плитка и ячейка — ork.metric_state: {state: show | prelim | nodata | hidden, …};
ноль за неизвестное не выдаётся. Показатели менеджеров ОРК поимённо («Месяц») — отдельным
массивом (второй элемент ответа build): в страницу — только под правом руководителей.
Сроки менеджера — «без пауз»: паузы в Битриксе не ведутся. Пороги ворот — ork.GATES,
предложение до решения владельца.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

import ork
import period as period_mod

COUNT_GATES = ork.GATES      # счёт живых пунктов: n-ворота n_count = 1, покрытие — как у всех


def _d(v) -> dt.date | None:
    return ork._d(v)


def _pct(k: int, n: int) -> int | None:
    return round(k / n * 100) if n else None


def _ms(value, n, cover, *, n_key="n_min", extra: dict | None = None, **kw) -> dict:
    """ork.metric_state + поля показателя (k, by, label…)."""
    out = ork.metric_state(value, n, cover, ork.GATES, n_key=n_key, **kw)
    if extra:
        out.update(extra)
    return out


def _count(value: int, n: int, cover: float | None, *, src: str, extra: dict | None = None) -> dict:
    """Плитка-счёт «сейчас»: на шагах никого — известный ноль, а не «нет данных»."""
    if not n:
        out = {"state": "show", "value": 0, "n": 0, "cover": None, "why": "сейчас на шагах никого",
               "src": src, "need": ""}
        if extra:
            out.update(extra)
        return out
    return _ms(value, n, cover, n_key="n_count", src=src, extra=extra)


def _probe_cap(code: str, st: dict) -> dict:
    """Показатель ждёт вердикта зонда (ork.PROBE_PENDING) — не выше «предварительно»."""
    probe = ork.PROBE_PENDING.get(code)
    if probe and st["state"] == "show":
        st["state"] = "prelim"
        st["why"] = f"предварительно: до вердикта зонда {probe}"
    return st


def _months(today: dt.date) -> list[dt.date]:
    out, y, m = [], today.year, today.month
    for _ in range(ork.MONTHS_BACK):
        out.append(dt.date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def _quarter(d: dt.date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def _end(seg: dict) -> dt.date | None:
    return _d(seg.get("end"))


def _clean(seg: dict) -> bool:
    """Сегмент годится для своевременности: без «пачки», возврата и обратного порядка меток."""
    return not (seg.get("burst") or seg.get("ret") or seg.get("neg"))


def _timeliness(segs_list: list[dict], code: str, *, n_key="n_min", agree=None) -> dict:
    """Доля сегментов `code` в норме среди законченных; покрытие — доля посчитанных (год в
    календаре); «пачки» — ворота burst_max."""
    ended = [s for s in segs_list]
    if not ended:
        return _ms(None, 0, None, n_key=n_key, src=code, need="сегменты закончатся — появятся")
    burst = sum(1 for s in ended if s.get("burst")) / len(ended)
    clean = [s for s in ended if _clean(s)]
    known = [s for s in clean if s.get("wd") is not None]
    k = sum(1 for s in known if s.get("inNorm"))
    cover = len(known) / len(clean) if clean else None
    return _ms(_pct(k, len(known)) if known else None, len(known), cover, n_key=n_key, burst=burst,
               agree=agree, src=code,
               extra={"k": k, "ended": len(ended), "excluded": len(ended) - len(clean),
                      "norm": ork.SEG_NORM.get(code), "label": ork.NO_PAUSE})


def _dist(vals: list[float], *, n_key="n_min", src="") -> dict:
    """Медиана и P80 в календарных днях и неделях."""
    vals = [v for v in vals if v is not None and v >= 0]
    med, p8 = ork.median(vals), ork.p80(vals)
    st = _ms(med, len(vals), None, n_key=n_key, src=src)
    if st["state"] in ("show", "prelim"):
        st.update(med=round(med, 1), p80=round(p8, 1), medW=round(med / 7, 1), p80W=round(p8 / 7, 1))
    return st


def build(orders: list[dict], deals: list[dict], queue: list[dict], today: dt.date, *,
          measure: list[dict] | None = None, otd: dict | None = None,
          ork_head: str = "") -> tuple[dict, list[dict]]:
    """orders — заказы вида (contracts.life_orders: stage, stepSince, t0, факты, _segs,
    _reach, art, orkMgr, respOrk, prepayShare, assignDate…); deals — строки сделок вида;
    queue — очередь lifecycle вида; measure — все заказы (мерило квартала: свойства
    поставщиков и процесса, а не вида). → (раздел «ork», показатели менеджеров поимённо)."""
    measure = orders if measure is None else measure
    deal_by = {d["deal"]: d for d in deals}
    U = [o for o in orders if o.get("live") and (d := deal_by.get(o.get("deal")))
         and not d.get("lost") and not d.get("closed")]
    mon = today - dt.timedelta(days=today.weekday())
    sun = mon + dt.timedelta(days=6)
    unknown: Counter = Counter()
    step = {o["id"]: ork.step_of(o.get("stage"), o, unknown=unknown) if o.get("stage") else ork.fact_floor(o)
            for o in U}
    ov = {o["id"]: ork.overrun(o, today) for o in U}
    hy = {o["id"]: ork.hygiene(o, today) for o in U}
    qcodes = Counter(q["code"] for q in queue)
    q_by: dict[str, list] = defaultdict(list)
    for q in queue:
        q_by[q["code"]].append(q)
    no_order = [d for d in deals if not d.get("closed") and not d.get("lost") and d.get("inCat0", True)
                and not d.get("norders")]

    # --- ведомость 13 шагов -----------------------------------------------------------------
    over_codes = {v: k for k, v in ork.OVER_CODE.items()}
    over_by_step = Counter()
    for code, st in over_codes.items():
        over_by_step[st] = len({q.get("order") for q in q_by.get(code, [])})
    over_by_step[12] = len({q.get("order") for q in q_by.get("noUtd", [])})
    reach_of = {o["id"]: o.get("_reach") or {} for o in orders}
    ledger, adjacent = [], Counter()
    for s in ork.STEPS:
        no = s["no"]
        here = [o for o in U if step[o["id"]] == no]
        waits = s["role"] if s["role"] in ork.ADJACENT else (
            s["waits"] if s["norm"] is None and s["waits"] in ork.ADJACENT else None)
        req = miss = 0
        for a in ork.ARTIFACTS:
            if a["step"] != no:
                continue
            for o in U:
                if ork.art_required(o, a, reach_of.get(o["id"])):
                    req += 1
                    miss += not (o.get("art") or {}).get(a["key"])
        if waits:
            adjacent[waits] += len(here)
        ledger.append({"no": no, "name": s["name"], "role": s["role"], "roleName": ork.ROLES[s["role"]],
                       "also": [ork.ROLES[r] for r in s["also"]], "norm": s["norm"], "now": len(here),
                       "over": over_by_step.get(no, 0) if no in ork.NORM_WD else None,
                       "artReq": req, "artMiss": miss, "waits": waits or "",
                       "waitsName": ork.ROLES.get(waits or "", ""), "seg": s["seg"] or ""})
    unk_n = sum(1 for o in U if step[o["id"]] is None)

    # --- W1 приёмка ------------------------------------------------------------------------
    s1_now = [o for o in U if step[o["id"]] == 1]
    s1_known = sum(1 for o in s1_now if ov[o["id"]]["wd"] is not None)
    no_known = sum(1 for d in no_order
                   if d.get("cat0At") and period_mod.workdays_ru(_d(d["cat0At"]), today) is not None)
    w1_n = len(s1_now) + len(no_order)
    w1_v = len({q.get("order") for q in q_by.get("s1Over", [])}) + qcodes.get("noOrder", 0)
    # «в срок за 20 р.д.»: приёмка заказов вида, закончившаяся за последние 20 рабочих дней
    back = period_mod.add_workdays_ru(today, -ork.WEEK_BACK_WD)
    s1_recent = [o["_segs"]["segs"]["S1"] for o in orders
                 if (o.get("_segs") or {}).get("segs", {}).get("S1")
                 and back and _end(o["_segs"]["segs"]["S1"]) and _end(o["_segs"]["segs"]["S1"]) > back]
    agree_s1, agree_n = _s1_agree(orders)
    W1 = _count(w1_v, w1_n, (s1_known + no_known) / w1_n if w1_n else None, src="s1Over + noOrder",
                extra={"orders": w1_v - qcodes.get("noOrder", 0), "noOrder": qcodes.get("noOrder", 0),
                       "label": ork.NO_PAUSE, "recent": _timeliness(s1_recent, "S1", agree=agree_s1),
                       "agree": agree_s1, "agreeN": agree_n})

    # --- W2 сверх нормы: только шаги ОРК (1, 4, 5, 8) и УПД по факту доставки (12) ------------
    w2_pop = [o for o in U if step[o["id"]] in ork.OVER_CODE]
    w2_known = sum(1 for o in w2_pop if ov[o["id"]]["wd"] is not None)
    utd_pop = [o for o in U if o.get("delFact") and not o.get("utd")]
    utd_known = sum(1 for o in utd_pop if period_mod.workdays_ru(o["delFact"], today) is not None)
    w2_n = len(w2_pop) + len(utd_pop)
    by_step = {no: over_by_step.get(no, 0) for no in (1, 4, 5, 8, 12)}
    W2 = _count(sum(by_step.values()), w2_n, (w2_known + utd_known) / w2_n if w2_n else None,
                src="s1Over, s2Over, s3Over, s6Over, noUtd",
                extra={"byStep": by_step, "label": "по стадии, " + ork.NO_PAUSE})

    # --- W3 зона риска: срок клиента (результат поставщика и логистики) ---------------------
    undeliv = [o for o in U if not (o.get("delFact") or o.get("utdDate"))]
    with_dl = sum(1 for o in undeliv if o.get("dl"))
    risk_q = [q for q in queue if q["code"] in ("late", "risk", "forecast")]
    W3 = _count(len({q.get("order") for q in risk_q}), len(undeliv), with_dl / len(undeliv) if undeliv else None,
                src="late, risk, forecast",
                extra={"by": {c: qcodes.get(c, 0) for c in ("late", "risk", "forecast")},
                       "label": "результат поставщика и логистики"})
    risk_list = [{"deal": q.get("deal"), "seq": q.get("seq"), "order": q.get("order"), "code": q["code"],
                  "days": q.get("days"), "dl": q.get("dl") or "", "fc": q.get("fc") or "",
                  "who": q.get("who"), "copy": q.get("escalateTo") or ""} for q in risk_q]

    # --- W4 эскалации и паузы — полей нет ------------------------------------------------------
    W4 = _ms(None, 0, None, src="—", need="поля «Эскалация» и «Пауза срока менеджера» (Ан. §2.1) — "
                                          "заявка администратору Битрикса")

    # --- W5 контрольные точки недели (шаг 7) --------------------------------------------------
    prod = [o for o in U if step[o["id"]] in (6, 7)]
    with_dates = sum(1 for o in prod if o.get("prodStart") and o.get("prodEnd"))
    cps = []
    for o in U:
        for p in ork.checkpoints(o, today):
            cps.append({"deal": o.get("deal"), "seq": o.get("seq"), "order": o["id"], "kind": p["kind"],
                        "date": p["date"], "who": (o.get("own") or {}).get("who") or ""})
    cps.sort(key=lambda x: (x["date"], x["kind"]))
    W5 = _count(len(cps), len(prod), with_dates / len(prod) if prod else None, src="ork.checkpoints",
                extra={"noStatus": "пройдена без запроса статуса — нет данных: поле «Запрос статуса поставщику»"})

    # --- сверх нормы по исполнителям (W2 поимённо: срок менеджера — его работа) ---------------
    mgr: dict[str, dict] = {}
    for code in list(ork.OVER_CODE.values()) + ["noUtd"]:
        for q in q_by.get(code, []):
            w = q.get("who") or "не назначен"
            m = mgr.setdefault(w, {"who": w, "gone": bool(q.get("whoGone")), "role": q.get("role") or "",
                                   "roleName": q.get("roleName") or "", "n": 0, "byStep": Counter()})
            m["n"] += 1
            m["byStep"][q.get("step")] += 1
    live_by_who = Counter((o.get("own") or {}).get("who") or "не назначен" for o in U)
    over_mgr = sorted(({**m, "byStep": {str(k): v for k, v in m["byStep"].items()}, "live": live_by_who.get(w, 0)}
                       for w, m in mgr.items()), key=lambda x: (-x["n"], x["who"]))

    # --- гигиена карточек -------------------------------------------------------------------
    hyg_items, hc = [], Counter()
    for o in U:
        h = hy[o["id"]]
        kinds = [k for k in ("behind", "abandoned", "unknown") if h.get(k)]
        if (o.get("own") or {}).get("role") == "orkMgr" and (o.get("own") or {}).get("who") == "не назначен":
            kinds.append("noMgr")
        for k in kinds:
            hc[k] += 1
        if kinds:
            hyg_items.append({"deal": o.get("deal"), "seq": o.get("seq"), "order": o["id"], "kinds": kinds,
                              "why": h.get("why") or ("менеджер ОРК не назначен" if "noMgr" in kinds else "")})
    hyg_items.sort(key=lambda x: (-(x["seq"] or 0), x["order"]))

    week = {"mon": mon.isoformat(), "sun": sun.isoformat(),
            "tiles": {"W1": W1, "W2": W2, "W3": W3, "W4": W4, "W5": W5},
            "ledger": ledger, "unknown": unk_n, "unknownStages": dict(unknown.most_common(20)),
            "live": len(U), "dealsNoOrder": len(no_order),
            "adjacent": {r: adjacent.get(r, 0) for r in ork.ADJACENT},
            "overByWho": over_mgr, "risk": risk_list, "checkpoints": cps,
            "hygiene": {"n": len(hyg_items), "by": dict(hc), "items": hyg_items},
            "notChecked": "не проверяется: паузы и эскалации (полей нет), запросы статуса поставщику, "
                          "содержимое файлов (есть ли подпись), проверка контрагента (СП-1066 не читается)"}

    month, month_people = _month(orders, U, today, otd, ov)
    quarter = _quarter_view(measure, today)
    how = _how(orders, U, deals, queue, week, month, quarter, today, step)
    trust = {"calYears": list(period_mod.workcal_years()),
             "yearsNoCal": [y for y in range(int(_hist_year(orders)), today.year + 1)
                            if y not in period_mod.workcal_years()],
             "histSince": "2024-06-01", "note": "паузы и эскалации в Битриксе не ведутся — у сроков менеджера "
                                                "подпись «без пауз»",
             "mapped": sum(1 for o in U if step[o["id"]] is not None), "unknown": unk_n,
             "noCalendar": sum(1 for o in U if ov[o["id"]]["why"] == "год без производственного календаря"),
             "ownerRule": ork.OWNER_RULE, "orkHead": bool(ork_head)}
    return ({"asOf": today.isoformat(), "trust": trust, "week": week, "month": month, "quarter": quarter,
             "how": how, "gatesNote": ork.GATES_NOTE}, month_people)


def _hist_year(orders: list[dict]) -> int:
    ys = [_d(o.get("created")).year for o in orders if _d(o.get("created"))]
    return max(2024, min(ys)) if ys else 2024


def _s1_agree(orders: list[dict]) -> tuple[float | None, int]:
    """Совпадение двух опор конца приёмки (в пределах agree_wd р.д.): выход заказа из «Заказ
    создан» по переходам и «Дата записи нового ответственного» (с 28.08.2026)."""
    pairs = ok = 0
    for o in orders:
        s1 = (o.get("_segs") or {}).get("segs", {}).get("S1")
        b = _d(o.get("assignDate"))
        if not s1 or not b or b < ork.S1_ANCHOR_SINCE:
            continue
        wd = period_mod.workdays_ru(_end(s1), b)
        if wd is None:
            continue
        pairs += 1
        ok += abs(wd) <= ork.GATES["agree_wd"]
    return (ok / pairs if pairs else None), pairs


def _month(orders: list[dict], U: list[dict], today: dt.date, otd: dict | None, ov: dict):
    """«Месяц»: M1–M3 по заказам ОРК-эпохи (созданы с ork.ORK_SINCE). Поимённо — отдельно."""
    era = [o for o in orders if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    agree_s1, _n = _s1_agree(era)
    otd_by = {m["m"]: m for m in (otd or {}).get("months") or []}
    rows, people = [], defaultdict(lambda: {"segs": defaultdict(list), "req": 0, "have": 0})
    for m0 in _months(today):
        m1 = dt.date(m0.year + (m0.month == 12), m0.month % 12 + 1, 1)
        by_seg: dict[str, list] = defaultdict(list)
        req = have = 0
        for o in era:
            for code in ("S1", "S2", "S3", "S6"):
                s = (o.get("_segs") or {}).get("segs", {}).get(code)
                if s and _end(s) and m0 <= _end(s) < m1:
                    by_seg[code].append(s)
                    if o.get("respOrk") and code != "S1" and m0 == _months(today)[0]:
                        people[o.get("orkMgr") or "не назначен"]["segs"][code].append(s)
            for a in ork.ARTIFACTS:
                at = _d((o.get("_reach") or {}).get(a["need"]))
                if at and m0 <= at < m1 and ork.art_required(o, a, o.get("_reach")):
                    req += 1
                    got = bool((o.get("art") or {}).get(a["key"]))
                    have += got
                    if o.get("respOrk") and m0 == _months(today)[0]:
                        p = people[o.get("orkMgr") or "не назначен"]
                        p["req"] += 1
                        p["have"] += got
        m1s = {c: _probe_cap("M1", _timeliness(by_seg[c], c, agree=agree_s1 if c == "S1" else None))
               for c in ("S1", "S2", "S3", "S6")}
        allm = [s for c in ("S2", "S3", "S6") for s in by_seg[c]]
        m1s["mgr"] = _probe_cap("M1", _timeliness(allm, "S2+S3+S6"))
        m2 = _ms(_pct(have, req) if req else None, req, None, src="ork.ARTIFACTS",
                 extra={"have": have, "label": "файл есть, содержимое не проверено", "target": 95})
        o_m = otd_by.get(m0.strftime("%m.%Y")) or {}
        otd_st = _ms(o_m.get("pct"), o_m.get("n") or 0, None, src="life.otd",
                     extra={"ontime": o_m.get("ontime"), "label": "результат поставщика и логистики"})
        rows.append({"m": m0.strftime("%m.%Y"), "M1": m1s, "M2": m2, "otd": otd_st, "target": 95})

    # M3 загрузка: живые заказы ОРК-эпохи на менеджера ОРК против среднего по менеджерам
    live_era = [o for o in U if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    load = Counter(o.get("orkMgr") for o in live_era if o.get("respOrk") and o.get("orkMgr"))
    not_ork = sum(1 for o in live_era if not o.get("respOrk"))
    mean = sum(load.values()) / len(load) if load else None
    out_band = sum(1 for v in load.values() if mean and abs(v / mean - 1) > ork.LOAD_BAND)
    M3 = _ms(round(mean, 1) if mean is not None else None, len(load), None, n_key="n_count",
             src="ответственный заказа из отдела ОРК",
             extra={"managers": len(load), "outBand": out_band, "notOrk": not_ork, "band": ork.LOAD_BAND,
                    "label": "без поправки на сложность", "need": "" if load else
                    "отдел ОРК в справочнике портала (department.get) или ответственные заказов из ОРК"})
    month = {"rows": rows, "M3": M3, "since": ork.ORK_SINCE.isoformat(), "target": 95,
             "note": "без поправки на сложность; рейтинга нет — только счёт",
             "an4": [{"name": n, "state": "nodata", "need": need} for n, need in ork.AN4_NODATA]}
    # поимённо — только руководителям ОРК и владельцу (решение 26.10.2026, п. 3)
    mp = []
    for who in sorted(set(load) | set(people)):
        p = people.get(who) or {"segs": {}, "req": 0, "have": 0}
        allm = [s for c in ("S2", "S3", "S6") for s in (p["segs"].get(c) or [])]
        mp.append({"who": who, "gone": str(who).endswith("(уволен)"), "live": load.get(who, 0),
                   "loadDev": round(load.get(who, 0) / mean - 1, 2) if mean else None,
                   "M1": {c: _probe_cap("M1", _timeliness(p["segs"].get(c) or [], c, n_key="n_manager_month"))
                          for c in ("S2", "S3", "S6")}
                   | {"mgr": _probe_cap("M1", _timeliness(allm, "S2+S3+S6", n_key="n_manager_month"))},
                   "M2": _ms(_pct(p["have"], p["req"]) if p["req"] else None, p["req"], None,
                             n_key="n_manager_month", extra={"have": p["have"]})})
    return month, mp


def _quarter_view(measure: list[dict], today: dt.date) -> dict:
    """«Квартал» — мерило по ВСЕМ заказам (свойства поставщиков и процесса, не вида)."""
    cur = _quarter(today)
    prev = (cur[0], cur[1] - 1) if cur[1] > 1 else (cur[0] - 1, 4)
    qs = []
    for y, q in (cur, prev):
        n = ok = pef = stock = 0
        sup: dict[str, list] = defaultdict(lambda: [0, 0])
        for o in measure:
            r = o.get("_reach") or {}
            ready, pe = _d(r.get(8)), o.get("prodEnd")
            if not ready or not pe or _quarter(ready) != (y, q):
                continue
            s5 = (o.get("_segs") or {}).get("segs", {}).get("S5")
            if s5 and s5.get("cd") is not None and s5["cd"] < 1:
                stock += 1                     # «в наличии»: производства не было — отдельно
                continue
            n += 1
            ok += ready <= pe
            pef += ready == pe
            sk = o.get("sup") or "—"
            sup[sk][0] += 1
            sup[sk][1] += ready <= pe
        share_pef = pef / n if n else None
        st = _ms(_pct(ok, n) if n else None, n, None, n_key="n_supplier_quarter", plan_eq_fact=share_pef,
                 src="вход в «Заказ готов» против «Production end date, budget»",
                 extra={"k": ok, "stock": stock, "planEqFact": round(share_pef, 3) if share_pef is not None else None,
                        "target": 90, "weight": 25})
        sup_rows = sorted(({"sup": s, **_ms(_pct(v[1], v[0]), v[0], None, n_key="n_supplier_quarter",
                                            plan_eq_fact=share_pef, extra={"k": v[1]})}
                           for s, v in sup.items()), key=lambda x: (-(x["n"] or 0), x["sup"]))
        qs.append({"q": f"{q} кв. {y}", "Q1": st, "suppliers": sup_rows})

    # Q2: S5, S7, S8 и полный цикл (создание → доставка), закончившиеся за год
    year_from = today - dt.timedelta(days=ork.YEAR_DAYS)
    seg_vals: dict[str, list] = defaultdict(list)
    for o in measure:
        for code in ("S5", "S7", "S8"):
            s = (o.get("_segs") or {}).get("segs", {}).get(code)
            if s and _clean(s) and _end(s) and year_from <= _end(s) <= today:
                seg_vals[code].append(s["cd"])
        c, f = o.get("created"), o.get("delFact")
        if c and f and year_from <= f <= today and f >= c:
            seg_vals["cycle"].append((f - c).days)
    q2 = {k: _dist(seg_vals[k], src=k) for k in ("S5", "S7", "S8", "cycle")}
    dirs = Counter(o.get("direction") for o in measure if o.get("direction"))
    q2cut = {"direction": _ms(None, 0, None, need="подписи «Направления поставки» не сверены (зонд v60)",
                              extra={"variants": len(dirs)}),
             "category": _ms(None, 0, None, need="категории оборудования на заказе нет — поле «добавить»"),
             "country": _ms(None, 0, None, need="страны происхождения нет — поле «добавить»")}

    # Q3: схема оплаты по «Доле первичной оплаты» (с 02.04.2026)
    era = [o for o in measure if _d(o.get("created")) and _d(o["created"]) >= ork.PREPAY_SINCE]
    vals = [o["prepayShare"] for o in era if o.get("prepayShare") is not None]
    full = sum(1 for v in vals if v >= 100)
    q3 = _ms(_pct(full, len(vals)) if vals else None, len(vals), len(vals) / len(era) if era else None,
             src="«Доля первичной оплаты, %»",
             extra={"k": full, "meanP": round(sum(vals) / len(vals), 1) if vals else None,
                    "since": ork.PREPAY_SINCE.isoformat()})
    score = [{"block": b, "name": nm, "weight": w,
              **({"state": "see", "src": "Q1"} if not need else {"state": "nodata", "need": need})}
             for b, nm, w, need in ork.SCORECARD]
    return {"quarters": qs, "Q2": q2, "Q2cut": q2cut, "Q3": q3, "scorecard": score,
            "index": {"state": "hidden", "why": "не показываем: данных нет по пяти блокам из шести"},
            "rating": {"state": "hidden", "why": "A/B/C — только из индекса"},
            "q2Window": ork.YEAR_DAYS}


def _how(orders, U, deals, queue, week, month, quarter, today, step) -> dict:
    """«Как считается»: формулы, поля, вердикт ворот, n и покрытие; нарушения порядка;
    заполненность артефактов; поля «добавить» с адресатом."""
    t = week["tiles"]
    m0 = month["rows"][0] if month["rows"] else {}
    q0 = quarter["quarters"][0] if quarter["quarters"] else {}

    def row(code, name, formula, fields, st):
        return {"code": code, "name": name, "formula": formula, "fields": fields,
                "state": st.get("state"), "why": st.get("why") or "", "n": st.get("n"),
                "cover": round(st["cover"], 3) if isinstance(st.get("cover"), float) else st.get("cover")}
    metrics = [
        row("W1", "Приёмка (S1)", "живые заказы в «Заказ создан» с р.д.(t0, сегодня) > 1 + сделки без заказа > 1 р.д.; "
            "t0 = max(создание заказа, вход сделки в реализацию)", "createdTime, crm.stagehistory", t["W1"]),
        row("W2", "Сверх нормы, только ОРК", "шаги 1, 4, 5, 8: р.д. от входа в шаг > нормы; шаг 12: р.д.(доставка, "
            "сегодня) > 10 и УПД нет; «без пауз»", "crm.stagehistory, Customer delivery date actual, UTD", t["W2"]),
        row("W3", "Зона риска", "срок клиента прошёл / ≤ 14 дн. без отгрузки / прогноз позже срока",
            "Date of deadline to customer, Supplier Shipment Date", t["W3"]),
        row("W4", "Эскалации и паузы", "—", "нет полей", t["W4"]),
        row("W5", "Контрольные точки недели", "P50 = начало + ½(конец − начало); P14 = отгрузка − 14 дн. при "
            "производстве > 30 дн.", "Production start/end date, budget; Supplier Shipment Date, planned", t["W5"]),
        row("M1", "Своевременность S2+S3+S6", "сегменты, закончившиеся в месяце, без «пачек» и возвратов: в норме / "
            "все", "crm.stagehistory (все переходы)", (m0.get("M1") or {}).get("mgr") or {}),
        row("M2", "Полнота карточки", "Σ есть / Σ требуется по парам (заказ, артефакт) у заказов, прошедших шаг",
            ", ".join(a["field"] for a in ork.ARTIFACTS), m0.get("M2") or {}),
        row("M3", "Загрузка", "живые заказы на менеджера ОРК / среднее по менеджерам; ±25 %",
            "assignedById, department.get", month.get("M3") or {}),
        row("Q1", "Первичный срок поставщика", "вход в «Заказ готов» ≤ Production end date, budget",
            "crm.stagehistory, ufCrm20_1724941935", q0.get("Q1") or {}),
        row("Q2", "Сроки S5 · S7 · S8 · цикл", "медиана и P80 = s[⌈0,8n⌉−1], календарные дни, за год",
            "crm.stagehistory, createdTime, Customer delivery date actual", quarter["Q2"]["S5"]),
        row("Q3", "Схема оплаты", "доля p = 100 % и среднее p", "ufCrm20_1775138654290", quarter["Q3"]),
    ]
    # нарушения порядка
    ret = sum(1 for o in orders if (o.get("_segs") or {}).get("returns"))
    bur = sum(1 for o in orders if (o.get("_segs") or {}).get("burst"))
    neg = sum(1 for o in orders if any(s.get("neg") for s in ((o.get("_segs") or {}).get("segs") or {}).values()))
    behind_deal = 0
    by_deal = defaultdict(list)
    for o in U:
        if step[o["id"]] is not None:
            by_deal[o.get("deal")].append(step[o["id"]])
    for d in deals:
        ds = d.get("dealStep")
        if ds is not None and by_deal.get(d["deal"]) and ds < min(by_deal[d["deal"]]):
            behind_deal += 1
    violations = {"returns": ret, "bursts": bur, "negative": neg,
                  "payNoContract": sum(1 for q in queue if q["code"] == "payNoContract"),
                  "dealBehindOrders": behind_deal,
                  "customsRf": {"state": "nodata", "need": "подписи «Направления поставки» не сверены (зонд v60)"},
                  "behindFacts": week["hygiene"]["by"].get("behind", 0),
                  "unknownStage": week["unknown"]}
    # заполненность артефактов у заказов, прошедших шаг (все заказы вида, не только живые)
    arts = []
    for a in ork.ARTIFACTS:
        req = have = 0
        for o in orders:
            if ork.art_required(o, a, o.get("_reach")):
                req += 1
                have += bool((o.get("art") or {}).get(a["key"]))
        arts.append({"key": a["key"], "name": a["name"], "field": a["field"], "step": a["step"],
                     "since": a["since"], "cond": a["cond"] or "", "req": req, "have": have,
                     "pct": _pct(have, req)})
    kam_pairs = [(o.get("kamOrder"), o.get("kamDeal")) for o in orders if o.get("kamOrder") and o.get("kamDeal")]
    return {"metrics": metrics, "violations": violations, "artifacts": arts,
            "kamAgree": {"n": len(kam_pairs), "same": sum(1 for a, b in kam_pairs if a == b)},
            "fieldsAdd": [{"kind": k, "name": n, "for": f, "to": ork.FIELDS_TO, "status": "добавить"}
                          for k, n, f in ork.FIELDS_ADD],
            "fieldsNote": "список для администратора Битрикса; никуда не отправлен",
            "gates": dict(ork.GATES), "gatesNote": ork.GATES_NOTE,
            "norms": {str(k): v for k, v in ork.NORM_WD.items()}, "noPause": ork.NO_PAUSE}
