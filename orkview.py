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
ноль за неизвестное не выдаётся. «Месяц» целиком (показатели отдела и менеджеров ОРК
поимённо) — отдельным массивом (второй элемент ответа build): в страницу — только под
правом руководителей ОРК (решение владельца 26.10.2026, п. 3).
Сроки менеджера — «без пауз»: паузы в Битриксе не ведутся. Пороги ворот — ork.GATES,
предложение до решения владельца.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

import ork
import period as period_mod

COUNT_GATES = ork.GATES      # счёт живых пунктов: n-ворота n_count = 1, покрытие — как у всех
# показатели «Месяца» — под правом «Реализация: показатели менеджеров ОРК» (ork_heads)
MONTH_CODES = ("M1", "M2", "M3")
RIGHT_NOTE = "под правом «Реализация: показатели менеджеров ОРК» — подвкладка «Месяц»"


def _d(v) -> dt.date | None:
    return ork._d(v)


def _pct(k: int, n: int) -> int | None:
    return round(k / n * 100) if n else None


# поля, по которым скрытое значение восстанавливается (k из n, «есть», среднее): у hidden их нет
# слагаемые плиток-счётов (W1 orders + noOrder, W2 byStep, W3 by) — их сумма и есть значение
VALUE_KEYS = ("k", "have", "ontime", "meanP", "outBand", "med", "p80", "medW", "p80W",
              "orders", "noOrder", "byStep", "by")


def _strip(out: dict) -> dict:
    """Скрытый показатель (hidden) — без значения и без чисел, из которых оно складывается:
    «не показываем» не должно читаться как «k из n»."""
    if out.get("state") == "hidden":
        out["value"] = None
        for k in VALUE_KEYS:
            out.pop(k, None)
    return out


def _hide(st: dict, why: str) -> dict:
    st.update(state="hidden", why=why)
    return _strip(st)


def _ms(value, n, cover, *, n_key="n_min", extra: dict | None = None, **kw) -> dict:
    """ork.metric_state + поля показателя (k, by, label…); hidden — без чисел значения."""
    out = ork.metric_state(value, n, cover, ork.GATES, n_key=n_key, **kw)
    if extra:
        out.update(extra)
    return _strip(out)


def _count(value: int, n: int, cover: float | None, *, src: str, extra: dict | None = None,
           unknown: int = 0) -> dict:
    """Плитка-счёт «сейчас»: на шагах никого (и стадий не из таблицы нет) — известный ноль.
    unknown — живые заказы со стадией не из таблицы шагов: они в n как «срок неизвестен» и
    снижают покрытие; все заказы в n такие — «Нет данных», а не ноль за неизвестное. На шаге
    есть заказы, но все не в счёте (excluded в extra: гигиена, приёмка вне «Реализации») —
    «в счёте никого, не в счёте N», а не «на шагах никого»."""
    if not n:
        exc = (extra or {}).get("excluded") or 0
        out = {"state": "show", "value": 0, "n": 0, "cover": None,
               "why": (f"в счёте никого; не в счёте {exc} (гигиена карточки или сделка вне «Реализации»)"
                       if exc else "сейчас на шагах никого"),
               "src": src, "need": ""}
        if extra:
            out.update(extra)
        return out
    if unknown and unknown >= n:
        return _ms(None, 0, None, src=src, extra={**(extra or {}), "unknown": unknown},
                   need=f"стадия не из таблицы шагов у {unknown} живых заказов — сверить ork.STAGE_STEP")
    return _ms(value, n, cover, n_key="n_count", src=src, extra={**(extra or {}), "unknown": unknown})


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
    """Сегмент годится для своевременности и сроков: шаг не пропущен (skip — начало и конец дал
    один переход), без «пачки», возврата и обратного порядка меток."""
    return not (seg.get("skip") or seg.get("burst") or seg.get("ret") or seg.get("neg"))


def _burst_hide(st: dict, burst: float | None) -> dict:
    """Все отрезки — в «пачках»: значения нет, но это «не показываем», а не «нет данных»."""
    if st["state"] == "nodata" and burst is not None and burst > ork.GATES["burst_max"]:
        _hide(st, f"не показываем: «пачек» {round(burst * 100)} % > {round(ork.GATES['burst_max'] * 100)} %")
    return st


def _timeliness(segs_list: list[dict], code: str, *, n_key="n_min", agree=None) -> dict:
    """Доля сегментов `code` в норме среди законченных; покрытие — доля посчитанных (год в
    календаре); «пачки» — ворота burst_max. Пропущенный шаг (skip) — не «в норме за 0 р.д.»:
    в счёт не идёт и считается отдельно (skipped), «пачки» меряются среди не пропущенных."""
    ended = [s for s in segs_list]
    if not ended:
        return _ms(None, 0, None, n_key=n_key, src=code, need="сегменты закончатся — появятся")
    skipped = sum(1 for s in ended if s.get("skip"))
    done = [s for s in ended if not s.get("skip")]
    burst = sum(1 for s in done if s.get("burst")) / len(done) if done else None
    clean = [s for s in done if _clean(s)]
    known = [s for s in clean if s.get("wd") is not None]
    k = sum(1 for s in known if s.get("inNorm"))
    cover = len(known) / len(clean) if clean else None
    st = _ms(_pct(k, len(known)) if known else None, len(known), cover, n_key=n_key, burst=burst,
             agree=agree, src=code,
             extra={"k": k, "ended": len(ended), "excluded": len(done) - len(clean), "skipped": skipped,
                    "norm": ork.SEG_NORM.get(code), "label": ork.NO_PAUSE})
    st = _burst_hide(st, burst)
    # все не пропущенные отрезки — с возвратом или обратным порядком меток: это не «нет данных»
    if st["state"] == "nodata" and done and not clean:
        _hide(st, f"не показываем: все {len(done)} отрезков — с возвратом или обратным порядком меток")
    return st


def _dist(segs: list[dict], *, n_key="n_min", src="", val=lambda s: s.get("cd")) -> dict:
    """Медиана и P80 в календарных днях и неделях по законченным сегментам: пропущенный шаг,
    «пачка», возврат и обратный порядок меток — не в счёт (excluded, skipped); «пачек» больше
    burst_max — не показываем."""
    skipped = sum(1 for s in segs if s.get("skip"))
    done = [s for s in segs if not s.get("skip")]
    burst = sum(1 for s in done if s.get("burst")) / len(done) if done else None
    vals = [v for v in (val(s) for s in done if _clean(s)) if v is not None and v >= 0]
    med, p8 = ork.median(vals), ork.p80(vals)
    st = _ms(med, len(vals), None, n_key=n_key, src=src, burst=burst,
             extra={"excluded": len(done) - len(vals), "skipped": skipped})
    if st["state"] in ("show", "prelim"):
        st.update(med=round(med, 1), p80=round(p8, 1), medW=round(med / 7, 1), p80W=round(p8 / 7, 1))
    return _burst_hide(st, burst)


def build(orders: list[dict], deals: list[dict], queue: list[dict], today: dt.date, *,
          measure: list[dict] | None = None, otd: dict | None = None,
          ork_head: str = "") -> tuple[dict, dict]:
    """orders — заказы вида (contracts.life_orders: stage, stepSince, t0, факты, _segs,
    _reach, art, orkMgr, respOrk, prepayShare, assignDate…); deals — строки сделок вида;
    queue — очередь lifecycle вида; measure — все заказы (мерило квартала: свойства
    поставщиков и процесса, а не вида). → (раздел «ork» без «Месяца», «Месяц» под правом:
    {month — показатели отдела, people — менеджеры ОРК поимённо, how — строки M1–M3 «Как
    считается»})."""
    measure = orders if measure is None else measure
    deal_by = {d["deal"]: d for d in deals}
    U = [o for o in orders if o.get("live") and (d := deal_by.get(o.get("deal")))
         and not d.get("lost") and not d.get("closed")]
    # шаг 12 (УПД) — на менеджере ОРК при любой стадии оплаты сделки (Рег. 14.2.4–14.3.2):
    # живой заказ закрытой выигранной сделки (в том числе «Оплата получена»), доставленный без
    # УПД, — в совокупности шага 12 (ведомость, столбец «ждём УПД от клиента» у «Сверх нормы по
    # менеджерам»), как в очереди (noUtd), очереди КАМа и карточке. Остальные шаги у закрытой
    # сделки — уже не отклонения
    C12 = [o for o in orders if o.get("live") and (d := deal_by.get(o.get("deal")))
           and not d.get("lost") and d.get("closed") and ork.step_now(o) == 12]
    mon = today - dt.timedelta(days=today.weekday())
    sun = mon + dt.timedelta(days=6)
    unknown: Counter = Counter()
    L = U + C12                             # совокупность ведомости: живые + шаг 12 закрытых
    deal_ok = {o["id"]: (deal_by.get(o.get("deal")) or {}).get("inCat0", True) for o in L}
    # шаг — ork.step_now (доставлено без УПД — шаг 12), «сверх нормы» — ork.judged: одно правило
    # с таблицей сделок, карточкой, «Замедлено» и очередью (гигиена и приёмка вне «Реализации»
    # в счёт не идут). Одна совокупность у «сейчас», «сверх нормы» и W2
    step = {o["id"]: ork.step_now(o, unknown=unknown) for o in L}
    ov = {o["id"]: ork.judged(o, today, in_cat0=deal_ok[o["id"]]) for o in L}
    hy = {o["id"]: ork.hygiene(o, today) for o in L}
    unk = [o for o in U if step[o["id"]] is None]           # стадия не из таблицы шагов
    unk_n = len(unk)
    qcodes = Counter(q["code"] for q in queue)
    q_by: dict[str, list] = defaultdict(list)
    for q in queue:
        q_by[q["code"]].append(q)
    no_order = [d for d in deals if not d.get("closed") and not d.get("lost") and d.get("inCat0", True)
                and not d.get("norders")]

    # --- ведомость 13 шагов -----------------------------------------------------------------
    # «сверх нормы» шага — живые заказы вида на этом шаге с judged.over; пункт очереди того же
    # заказа (s1Over…s6Over) ставит то же правило — помечаем его w2 (отбор плитки)
    over_ids: dict[int, set] = {st: {o["id"] for o in L if step[o["id"]] == st and ov[o["id"]]["over"]}
                                for st in ork.NORM_WD}
    for st, code in ork.OVER_CODE.items():
        for q in q_by.get(code, []):
            if q.get("order") in over_ids.get(st, set()):
                q["w2"] = True                     # пункт в счёте W2 — отбор плитки
    over_by_step = Counter({st: len(ids) for st, ids in over_ids.items()})
    # «ждём УПД от клиента» (решение владельца 27.10.2026, Б): шаг 12 больше 10 р.д. от доставки
    # без УПД — judged.waitUtd; не «сверх нормы» менеджера (не в W2, не в столбце «сверх нормы»
    # ведомости и по менеджерам); пункт noUtd того же заказа — waitUtd (свой столбец, отбор)
    wait_ids: dict[int, set] = {st: {o["id"] for o in L if step[o["id"]] == st and ov[o["id"]]["waitUtd"]}
                                for st in ork.WAIT_CODE}
    for st, code in ork.WAIT_CODE.items():
        for q in q_by.get(code, []):
            if q.get("order") in wait_ids[st]:
                q["waitUtd"] = True
    wait_n = sum(len(ids) for ids in wait_ids.values())
    # срок УПД не посчитать (нет факта доставки, года нет в календаре): в W2 шаг 12 не входит,
    # покрытие W2 их не снижает — счёт рядом с «ждём УПД», а не ноль за неизвестное (правило 15)
    wait_unknown = sum(1 for o in L if step[o["id"]] in ork.WAIT_CODE and ov[o["id"]]["termUnknown"])

    def not_counted(o) -> bool:
        """Шаг с нормой, а срок не в счёте: гигиена карточки или приёмка вне «Реализации»."""
        v = ov[o["id"]]
        return v["norm"] is not None and not v["counted"] and not v["termUnknown"]
    reach_of = {o["id"]: o.get("_reach") or {} for o in orders}
    # «Квартал» — раньше недели: P80 производства (S5) по ВСЕМ заказам — порог сигнала шага 7
    quarter = _quarter_view(measure, today)
    long_p80 = _long_p80([o for o in U if step[o["id"]] == 7], quarter["Q2"]["S5"], today)
    ledger, adjacent = [], Counter()
    for s in ork.STEPS:
        no = s["no"]
        here = [o for o in L if step[o["id"]] == no]
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
        doc = ork.NORM_DOC.get(no, ("", "none", ""))
        normed = no in ork.NORM_WD
        ledger.append({"no": no, "name": s["name"], "role": s["role"], "roleName": ork.ROLES[s["role"]],
                       "also": [ork.ROLES[r] for r in s["also"]], "norm": s["norm"], "now": len(here),
                       "normDoc": doc[0], "normState": doc[1], "normNeed": doc[2],
                       "over": over_by_step.get(no, 0) if normed else None,
                       # шаг 12: срок ждёт клиента — «ждём УПД от клиента N», а не «сверх нормы»
                       "waitUtd": len(wait_ids[no]) if no in wait_ids else None,
                       "overUnknown": sum(1 for o in here if ov[o["id"]]["termUnknown"]) if normed else None,
                       # на шаге, но не в счёте «сверх нормы»: гигиена, приёмка вне «Реализации»
                       "notCounted": sum(1 for o in here if not_counted(o)) if normed else None,
                       "artReq": req, "artMiss": miss, "waits": waits or "",
                       # шаг 7 регламент не нормирует: «дольше P80 S5» — сигнал, не «сверх нормы»
                       "longP80": (long_p80["k"] if long_p80["state"] in ("show", "prelim") else None)
                       if no == 7 else None,
                       "waitsName": ork.ROLES.get(waits or "", ""), "seg": s["seg"] or ""})

    # --- W1 приёмка ------------------------------------------------------------------------
    # n — заказы на шаге 1 в счёте (без гигиены и сделок вне «Реализации»), сделки без заказа и
    # заказы со стадией не из таблицы («срок неизвестен» — снижают покрытие)
    s1_now = [o for o in U if step[o["id"]] == 1 and (ov[o["id"]]["counted"] or ov[o["id"]]["termUnknown"])]
    s1_known = sum(1 for o in s1_now if ov[o["id"]]["counted"])
    no_known = sum(1 for d in no_order
                   if d.get("cat0At") and period_mod.workdays_ru(_d(d["cat0At"]), today) is not None)
    w1_n = len(s1_now) + len(no_order) + unk_n
    w1_v = len(over_ids[1]) + qcodes.get("noOrder", 0)
    # «в срок за 20 р.д.»: приёмка заказов вида, закончившаяся за последние 20 рабочих дней
    # приёмка сделки вне воронки «Реализация» не в счёте (Рег. 3.3) — как у ork.judged и «Месяца»
    back = period_mod.add_workdays_ru(today, -ork.WEEK_BACK_WD)
    in_cat = {d["deal"]: d.get("inCat0", True) for d in deals}
    s1_recent = [o["_segs"]["segs"]["S1"] for o in orders
                 if (o.get("_segs") or {}).get("segs", {}).get("S1") and in_cat.get(o.get("deal"), True)
                 and back and _end(o["_segs"]["segs"]["S1"]) and _end(o["_segs"]["segs"]["S1"]) > back]
    agree_s1, agree_n = _s1_agree(orders)
    recent = (_timeliness(s1_recent, "S1", agree=agree_s1) if back else
              _ms(None, 0, None, src="S1", need=f"нет производственного календаря на {today.year} год "
                                                "(data/workcal_ru.json) — 20 р.д. назад не отсчитать"))
    W1 = _count(w1_v, w1_n, (s1_known + no_known) / w1_n if w1_n else None, src="s1Over + noOrder",
                unknown=unk_n,
                extra={"orders": len(over_ids[1]), "noOrder": qcodes.get("noOrder", 0),
                       "label": ork.NO_PAUSE, "recent": recent,
                       "agree": agree_s1, "agreeN": agree_n,
                       "excluded": sum(1 for o in U if step[o["id"]] == 1 and not_counted(o))})

    # --- W2 сверх нормы: только шаги ОРК (1, 4, 5, 8) -----------------------------------------
    # n — заказы этих шагов в счёте (judged.counted) и со сроком, который не посчитать
    # (termUnknown: нет даты входа, года нет в календаре), плюс стадии не из таблицы; гигиена и
    # приёмка вне «Реализации» — рядом (excluded), не в n. Шаг 12 (УПД) — не в W2 и не в n:
    # «ждём УПД от клиента» (решение владельца 27.10.2026, Б) — рядом, waitUtd
    w2_pop = [o for o in L if step[o["id"]] in ork.OVER_CODE
              and (ov[o["id"]]["counted"] or ov[o["id"]]["termUnknown"])]
    w2_known = sum(1 for o in w2_pop if ov[o["id"]]["counted"])
    w2_n = len(w2_pop) + unk_n
    by_step = {no: over_by_step.get(no, 0) for no in ork.OVER_CODE}
    W2 = _count(sum(by_step.values()), w2_n, w2_known / w2_n if w2_n else None,
                src="s1Over, s2Over, s3Over, s6Over", unknown=unk_n,
                extra={"byStep": by_step, "label": "по стадии, " + ork.NO_PAUSE, "waitUtd": wait_n,
                       "excluded": sum(1 for o in L if step[o["id"]] in ork.OVER_CODE and not_counted(o))})

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
    # точки — у тех же заказов, что в n: шаги 6–7 (оплачено и в производстве); стадии не из
    # таблицы — в n как «неизвестно» (могут быть в производстве)
    prod = [o for o in U if step[o["id"]] in (6, 7)]
    with_dates = sum(1 for o in prod if o.get("prodStart") and o.get("prodEnd"))
    cps = []
    for o in prod:
        for p in ork.checkpoints(o, today):
            cps.append({"deal": o.get("deal"), "seq": o.get("seq"), "order": o["id"], "kind": p["kind"],
                        "date": p["date"], "who": (o.get("own") or {}).get("who") or ""})
    cps.sort(key=lambda x: (x["date"], x["kind"]))
    w5_n = len(prod) + unk_n
    # prod — в производстве (шаги 6–7), без дат — у них точки неизвестны; стадии не из таблицы — отдельно
    W5 = _count(len(cps), w5_n, with_dates / w5_n if w5_n else None, src="ork.checkpoints", unknown=unk_n,
                extra={"noStatus": "пройдена без запроса статуса — нет данных: поле «Запрос статуса поставщику»",
                       "prod": len(prod), "noDates": len(prod) - with_dates})
    if prod and not with_dates and W5["state"] != "nodata":
        # в производстве есть, плановых дат нет ни у кого — это «нет данных», а не «покрытие 0 %»
        W5 = _ms(None, 0, None, src="ork.checkpoints", need="нет плановых дат производства у заказов шагов "
                 "6–7 (Production start/end date, budget) — внести даты размещения (ОСС)",
                 extra={"noStatus": W5.get("noStatus"), "unknown": unk_n, "prod": len(prod),
                        "noDates": len(prod) - with_dates})

    # --- сверх нормы по исполнителям (W2 поимённо: срок менеджера — его работа) ---------------
    # n — пункты сверх нормы (w2, тот же счёт, что у W2 и ведомости); waitUtd — свой столбец
    # «ждём УПД от клиента» (пункты noUtd с waitUtd), в n не идёт (решение 27.10.2026, Б)
    mgr: dict[str, dict] = {}
    for code, flag in [(c, "w2") for c in ork.OVER_CODE.values()] + [(c, "waitUtd") for c in ork.WAIT_CODE.values()]:
        for q in q_by.get(code, []):
            if not q.get(flag):
                continue
            w = q.get("who") or "не назначен"
            m = mgr.setdefault(w, {"who": w, "gone": bool(q.get("whoGone")), "role": q.get("role") or "",
                                   "roleName": q.get("roleName") or "", "n": 0, "waitUtd": 0,
                                   "byStep": Counter()})
            if flag == "w2":
                m["n"] += 1
                m["byStep"][q.get("step")] += 1
            else:
                m["waitUtd"] += 1
    # по алфавиту, а не по числу: рейтинга людей нет (решение п. 3 — «без сортировки по баллу»);
    # «не назначен» — последней строкой
    over_mgr = sorted(({**m, "byStep": {str(k): v for k, v in m["byStep"].items()}}
                       for w, m in mgr.items()), key=lambda x: (x["who"] == "не назначен", x["who"]))

    # --- гигиена карточек -------------------------------------------------------------------
    hyg_items, hc = [], Counter()
    for o in L:
        h = hy[o["id"]]
        kinds = [k for k in ("behind", "abandoned", "old", "unknown") if h.get(k)]
        if (o.get("own") or {}).get("role") == "orkMgr" and (o.get("own") or {}).get("who") == "не назначен":
            kinds.append("noMgr")
        for k in kinds:
            hc[k] += 1
        if kinds:
            hyg_items.append({"deal": o.get("deal"), "seq": o.get("seq"), "order": o["id"], "kinds": kinds,
                              "step": step[o["id"]],
                              "excl": bool(h["exclude"] or step[o["id"]] is None),
                              "why": h.get("why") or ("менеджер ОРК не назначен" if "noMgr" in kinds else "")})
    hyg_items.sort(key=lambda x: (-(x["seq"] or 0), x["order"]))
    # не в счёте «сверх нормы»: стадия позади фактов, брошенная (шаги 1, 4, 5, 8), старая
    # доставка без УПД (шаг 12), стадия не из таблицы; «менеджер ОРК не назначен» в счёте ЕСТЬ —
    # назначить (решение п. 1)
    hyg_excl = sum(1 for x in hyg_items if x["excl"])
    # «в счёт не идут: N (…)» — разбивка только по заказам вне счёта: доставленный без УПД с
    # отставшей стадией (шаг 12) в счёте ЕСТЬ и в скобки не идёт
    hc_excl = Counter(k for x in hyg_items if x["excl"] for k in x["kinds"])

    week = {"mon": mon.isoformat(), "sun": sun.isoformat(),
            "tiles": {"W1": W1, "W2": W2, "W3": W3, "W4": W4, "W5": W5},
            "ledger": ledger, "unknown": unk_n, "unknownStages": dict(unknown.most_common(20)),
            # живые заказы ведомости: открытых сделок и шаг 12 закрытых (closed12 из них)
            "live": len(L), "closed12": len(C12), "dealsNoOrder": len(no_order),
            # из них дольше 1 р.д. — пункты noOrder (они в W1)
            "dealsNoOrderOver": qcodes.get("noOrder", 0),
            # «Оплата получена», сделка не закрыта — пункты dealClose (шаг 13, ОСС): сделки, не заказы
            "dealClose": qcodes.get("dealClose", 0),
            "orkHead": ork_head or "",
            "adjacent": {r: adjacent.get(r, 0) for r in ork.ADJACENT},
            "longP80": long_p80,
            "overByWho": over_mgr, "risk": risk_list, "checkpoints": cps,
            # шаг 12 больше 10 р.д. без УПД — «ждём УПД от клиента»: не «сверх нормы» менеджера
            # (решение владельца 27.10.2026, Б); пункт noUtd — у менеджера ОРК, КАМ в копии
            "waitUtd": {"n": wait_n, "unknown": wait_unknown, "label": ork.WAIT_UTD, "src": "noUtd",
                        "norm": ork.NORM_WD[12]},
            "hygiene": {"n": len(hyg_items), "by": dict(hc), "excluded": hyg_excl, "byExcl": dict(hc_excl),
                        "noMgrOnly": sum(1 for x in hyg_items if x["kinds"] == ["noMgr"]), "items": hyg_items},
            "notChecked": "не проверяется: паузы и эскалации (полей нет), запросы статуса поставщику, "
                          "содержимое файлов (есть ли подпись), проверка контрагента (СП-1066 не читается)"}

    month, month_people = _month(orders, U, today, otd, ov, deal_ok_all={
        d["deal"]: d.get("inCat0", True) for d in deals})
    how = _how(orders, U, deals, queue, week, month, quarter, today, step)
    # «Месяц» целиком — под правом руководителей ОРК (решение владельца 26.10.2026, п. 3:
    # «подвкладка «Месяц» — только руководителям и владельцу»): показатели отдела, ячейка S1
    # (одна роль — один человек) и поимённо — во второй элемент ответа (массив ORKMONTH), в
    # «Как считается» у M1–M3 — только формула и поля, вердикт и n — там же, под правом
    m_rows = [m for m in how["metrics"] if m["code"] in MONTH_CODES]
    how["metrics"] = [m if m["code"] not in MONTH_CODES else
                      {**m, "state": "right", "why": RIGHT_NOTE, "n": None, "cover": None}
                      for m in how["metrics"]]
    # годы сроков, которые считаются сейчас: от самой ранней даты входа в шаг живого заказа (и
    # факта доставки у шага 12) до сегодня — без подрезки снизу (правило 15)
    used = [_d(ov[o["id"]]["since"]) for o in L if ov[o["id"]]["since"]]   # у шага 12 since — доставка
    y0 = min((d.year for d in used if d), default=today.year)
    trust = {"calYears": list(period_mod.workcal_years()),
             "yearsNoCal": [y for y in range(y0, today.year + 1) if y not in period_mod.workcal_years()],
             "nextYearCal": (today.year + 1) in period_mod.workcal_years(),
             "histSince": "2024-06-01", "note": "паузы и эскалации в Битриксе не ведутся — у сроков менеджера "
                                                "подпись «без пауз»",
             "mapped": sum(1 for o in L if step[o["id"]] is not None), "unknown": unk_n,
             # сроки «сверх нормы» (шаги W2, ork.OVER_CODE), которые не посчитать: года нет в
             # календаре; шаг 12 («ждём УПД») в W2 не входит — его неизвестные в week.waitUtd.unknown
             "noCalendar": sum(1 for o in L if step[o["id"]] in ork.OVER_CODE
                               and ov[o["id"]]["why"] == "год без производственного календаря"),
             "ownerRule": ork.OWNER_RULE, "orkHead": bool(ork_head),
             # ответственный живого заказа — из отдела ОРК по справочнику (people.ORK_DEPT): ноль —
             # название отдела не совпало, «менеджер ОРК не назначен» затопит очередь
             "respOrk": sum(1 for o in U if o.get("respOrk")),
             "oldNoUtd": sum(1 for o in L if hy[o["id"]].get("old"))}
    return ({"asOf": today.isoformat(), "trust": trust, "week": week, "quarter": quarter,
             "how": how, "gatesNote": ork.GATES_NOTE},
            {"month": month, "people": month_people, "how": m_rows})


def _long_p80(prod: list[dict], s5: dict, today: dt.date) -> dict:
    """Шаг 7 (контроль производства) регламент не нормирует (медиана и P80): живой заказ в
    производстве дольше P80 отрезка S5 по ВСЕМ заказам («Квартал», Q2) — сигнал в ведомость и
    «Ждут смежников» (мяч у поставщика), а не нарушение нормы менеджера. Дней в производстве —
    календарные от первого выхода на шаг 7 (как у S5), нет перехода — от входа в шаг.
    P80 не показан (мало отрезков, «пачки») — «Нет данных», а не ноль."""
    label = "дольше P80 производства (S5) — сигнал, не нарушение нормы: шаг 7 регламент не нормирует"
    p8 = s5.get("p80") if s5.get("state") in ("show", "prelim") else None
    if p8 is None:
        return {"state": "nodata", "value": None, "k": None, "n": len(prod), "p80": None, "items": [],
                "label": label, "src": "Q2 S5",
                "why": "P80 производства не посчитан: " + (s5.get("why") or "нет законченных отрезков S5")}
    items, unknown = [], 0
    for o in prod:
        start = _d((o.get("_reach") or {}).get(7)) or _d(o.get("stepSince")) or _d(o.get("stageSince"))
        if start is None:
            unknown += 1
            continue
        days = (today - start).days
        if days > p8:
            items.append({"deal": o.get("deal"), "seq": o.get("seq"), "order": o["id"], "days": days,
                          "sup": o.get("sup") or "", "who": (o.get("own") or {}).get("who") or ""})
    items.sort(key=lambda x: (-x["days"], x["order"]))
    known = len(prod) - unknown
    return {"state": s5["state"], "value": len(items), "k": len(items), "n": known, "unknown": unknown,
            "p80": p8, "items": items, "label": label, "src": "Q2 S5",
            "why": s5.get("why") or ""}


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
    # меньше agree_pairs_min пар — совпадение не меряется: одна несовпавшая пара из двух не
    # прячет показатель (и не подтверждает его)
    return (ok / pairs if pairs >= ork.GATES["agree_pairs_min"] else None), pairs


def _person(o: dict) -> str:
    """Человек за отрезком или артефактом заказа — ответственный заказа (любой отдел)."""
    w = o.get("resp") or o.get("orkMgr") or ""
    return "" if w in ("", "—", "не назначен") else str(w)


# у ячейки, скрытой воротами людей, нет и чисел, из которых её восстановить вычитанием
PEOPLE_KEYS = ("n", "ended", "excluded", "skipped", "people", "cover")


def _people_hide(st: dict, why: str) -> dict:
    _hide(st, why)
    for k in PEOPLE_KEYS:
        st.pop(k, None)
    st["peopleGate"] = True
    return st


def _people_gate(st: dict, who: set) -> dict:
    """Показатель отдела по одному-двум людям — это показатель человека (решение п. 3: поимённо
    — только под правом руководителей): людей за ячейкой меньше n_managers_min — не
    показываем; значение, k, n и прочие счёты не отдаём. «Нет данных» остаётся «нет данных»."""
    st["people"] = len(who)
    if st.get("state") != "nodata" and len(who) < ork.GATES["n_managers_min"]:
        _people_hide(st, f"не показываем: ответственных заказа {len(who)} < {ork.GATES['n_managers_min']} — "
                         "показатель раскрыл бы человека")
    return st


def _month(orders: list[dict], U: list[dict], today: dt.date, otd: dict | None, ov: dict, *,
           deal_ok_all: dict | None = None):
    """«Месяц»: M1–M3 по заказам ОРК-эпохи (созданы с ork.ORK_SINCE). Поимённо — отдельно.
    deal_ok_all — сделка в воронке «Реализация»: приёмка (S1) сделки вне воронки не в счёте
    (Рег. 3.3), как у ork.judged и W1."""
    deal_ok_all = deal_ok_all or {}
    era = [o for o in orders if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    agree_s1, _n = _s1_agree(era)
    otd_by = {m["m"]: m for m in (otd or {}).get("months") or []}
    # поимённо — текущий (неполный) и прошлый полный месяц: в начале месяца у текущего данных нет
    rows, people = [], defaultdict(lambda: {"segs": defaultdict(list), "req": 0, "have": 0})
    months = _months(today)
    for mi, m0 in enumerate(months):
        m1 = dt.date(m0.year + (m0.month == 12), m0.month % 12 + 1, 1)
        by_seg: dict[str, list] = defaultdict(list)
        seg_who: dict[str, set] = defaultdict(set)      # люди за ячейкой — ворота «раскрыл бы человека»
        art_who: set = set()
        req = have = 0
        for o in era:
            for code in ("S1", "S2", "S3", "S6"):
                s = (o.get("_segs") or {}).get("segs", {}).get(code)
                if code == "S1" and not deal_ok_all.get(o.get("deal"), True):
                    continue                       # приёмка — только у сделок «Реализации»
                if s and _end(s) and m0 <= _end(s) < m1:
                    by_seg[code].append(s)
                    if _person(o):
                        seg_who[code].add(_person(o))
                    if o.get("respOrk") and code != "S1" and mi < 2:
                        people[(mi, o.get("orkMgr") or "не назначен")]["segs"][code].append(s)
            for a in ork.ARTIFACTS:
                at = _d((o.get("_reach") or {}).get(a["need"]))
                if at and m0 <= at < m1 and ork.art_required(o, a, o.get("_reach")):
                    req += 1
                    got = bool((o.get("art") or {}).get(a["key"]))
                    have += got
                    if _person(o):
                        art_who.add(_person(o))
                    if o.get("respOrk") and mi < 2:
                        p = people[(mi, o.get("orkMgr") or "не назначен")]
                        p["req"] += 1
                        p["have"] += got
        # S1 — работа руководителя ОРК (одна роль, не менеджеры): без ворот людей; S2, S3, S6,
        # их сумма и M2 — работа менеджеров: меньше трёх человек за ячейкой — не показываем
        m1s = {c: _probe_cap("M1", _timeliness(by_seg[c], c, agree=agree_s1 if c == "S1" else None))
               for c in ("S1", "S2", "S3", "S6")}
        for c in ("S2", "S3", "S6"):
            _people_gate(m1s[c], seg_who[c])
        allm = [s for c in ("S2", "S3", "S6") for s in by_seg[c]]
        m1s["mgr"] = _people_gate(_probe_cap("M1", _timeliness(allm, "S2+S3+S6")),
                                  seg_who["S2"] | seg_who["S3"] | seg_who["S6"])
        # сумма минус показанные = скрытая: хоть одна из S2/S3/S6 скрыта — скрыта и сумма
        hid = [c for c in ("S2", "S3", "S6") if m1s[c].get("state") == "hidden"]
        if hid and m1s["mgr"].get("state") != "hidden":
            _people_hide(m1s["mgr"], f"не показываем: скрыта ячейка {', '.join(hid)} — сумма раскрыла бы "
                                     "её вычитанием")
        m2 = _people_gate(_ms(_pct(have, req) if req else None, req, None, src="ork.ARTIFACTS",
                              extra={"have": have, "label": "файл есть, содержимое не проверено", "target": 95}),
                          art_who)
        o_m = otd_by.get(m0.strftime("%m.%Y")) or {}
        otd_st = _ms(o_m.get("pct"), o_m.get("n") or 0, None, src="life.otd",
                     extra={"ontime": o_m.get("ontime"), "label": "результат поставщика и логистики"})
        # текущий месяц идёт — неполный: заголовок «Месяца» — последний ПОЛНЫЙ месяц
        # первый месяц эпохи ОРК (24.08 — середина месяца) неполный: в счёте только отрезки
        # заказов, созданных с 24.08 и успевших закончиться, — сдвиг к «в норме»
        rows.append({"m": m0.strftime("%m.%Y"), "M1": m1s, "M2": m2, "otd": otd_st, "target": 95,
                     "partial": mi == 0, "eraPartial": m0 < ork.ORK_SINCE < m1})

    # M3 загрузка: живые заказы ОРК-эпохи на менеджера ОРК против среднего по менеджерам
    live_era = [o for o in U if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    load = Counter(o.get("orkMgr") for o in live_era if o.get("respOrk") and o.get("orkMgr"))
    not_ork = sum(1 for o in live_era if not o.get("respOrk"))
    mean = sum(load.values()) / len(load) if load else None
    out_band = sum(1 for v in load.values() if mean and abs(v / mean - 1) > ork.LOAD_BAND)
    M3 = _ms(round(mean, 1) if mean is not None else None, len(load), None, n_key="n_count",
             src="ответственный заказа из отдела ОРК",
             extra={"managers": len(load), "outBand": out_band, "notOrk": not_ork, "band": ork.LOAD_BAND,
                    "label": "без поправки на сложность"})
    if not load:
        M3["need"] = ("живых заказов, созданных с 24.08.2026, в этом виде нет" if not live_era else
                      "ответственные живых заказов не из отдела ОРК по справочнику (department.get) — "
                      "сверить people.ORK_DEPT с названием отдела")
    # среднее по одному-двум менеджерам — это нагрузка конкретного человека: без права не показываем
    if load and len(load) < ork.GATES["n_managers_min"]:
        _hide(M3, f"не показываем: менеджеров ОРК {len(load)} < {ork.GATES['n_managers_min']} — "
                  "среднее раскрыло бы нагрузку человека")
    month = {"rows": rows, "M3": M3, "since": ork.ORK_SINCE.isoformat(), "target": 95,
             # заголовок — последний полный месяц (rows[1]); текущий (rows[0]) — рядом, «идёт, неполный»
             "head": 1 if len(rows) > 1 else 0,
             "note": "без поправки на сложность; рейтинга нет — только счёт",
             "an4": [{"name": n, "state": "nodata", "need": need} for n, need in ork.AN4_NODATA]}
    # поимённо — только руководителям ОРК и владельцу (решение 26.10.2026, п. 3)
    def per_month(p: dict) -> dict:
        allm = [s for c in ("S2", "S3", "S6") for s in (p["segs"].get(c) or [])]
        return {"M1": {c: _probe_cap("M1", _timeliness(p["segs"].get(c) or [], c, n_key="n_manager_month"))
                       for c in ("S2", "S3", "S6")}
                | {"mgr": _probe_cap("M1", _timeliness(allm, "S2+S3+S6", n_key="n_manager_month"))},
                "M2": _ms(_pct(p["have"], p["req"]) if p["req"] else None, p["req"], None,
                          n_key="n_manager_month", extra={"have": p["have"]})}
    empty = {"segs": {}, "req": 0, "have": 0}
    mp = []
    for who in sorted(set(load) | {w for _mi, w in people}):
        cur, full = people.get((0, who)) or empty, people.get((1, who)) or empty
        mp.append({"who": who, "gone": str(who).endswith("(уволен)"), "live": load.get(who, 0),
                   "loadDev": round(load.get(who, 0) / mean - 1, 2) if mean else None,
                   # главное — последний полный месяц; текущий (идёт, неполный) — рядом
                   "m": months[1].strftime("%m.%Y") if len(months) > 1 else "",
                   **per_month(full),
                   "cur": {"m": months[0].strftime("%m.%Y"), "partial": True, **per_month(cur)}})
    return month, mp


def _quarter_view(measure: list[dict], today: dt.date) -> dict:
    """«Квартал» — мерило по ВСЕМ заказам (свойства поставщиков и процесса, не вида)."""
    cur = _quarter(today)
    prev = (cur[0], cur[1] - 1) if cur[1] > 1 else (cur[0] - 1, 4)
    qs = []
    for y, q in (cur, prev):
        n = ok = pef = stock = bad = 0
        sup: dict[str, list] = defaultdict(lambda: [0, 0, 0])      # [в счёте, в срок, не в счёте]
        for o in measure:
            r = o.get("_reach") or {}
            ready, pe = _d(r.get(8)), o.get("prodEnd")
            if not ready or not pe or _quarter(ready) != (y, q):
                continue
            sk = o.get("sup") or "—"
            sg = o.get("_segs") or {}
            s5 = sg.get("segs", {}).get("S5")
            # вход в «Заказ готов» прокликан «пачкой», метки в обратном порядке или заказ потом
            # вернули с шага 8+ назад — готовность не первичная: в счёт не идёт, доля — ворота
            # «пачек» (у показателя и у строки поставщика)
            back = any(a >= 8 > b for a, b in sg.get("returnPairs") or [])
            if (s5 and (s5.get("burstEnd") or s5.get("neg"))) or back:
                bad += 1
                sup[sk][2] += 1
                continue
            if s5 and s5.get("cd") is not None and s5["cd"] < 1:
                stock += 1                     # «в наличии»: производства не было — отдельно
                continue
            n += 1
            ok += ready <= pe
            pef += ready == pe
            sup[sk][0] += 1
            sup[sk][1] += ready <= pe
        share_pef = pef / n if n else None
        share_bad = bad / (n + bad) if (n + bad) else None
        # отдел целиком — общий показатель (n_min); поставщик за квартал — n_supplier_quarter
        st = _ms(_pct(ok, n) if n else None, n, None, n_key="n_min", plan_eq_fact=share_pef,
                 burst=share_bad, src="вход в «Заказ готов» против «Production end date, budget»",
                 extra={"k": ok, "stock": stock, "planEqFact": round(share_pef, 3) if share_pef is not None else None,
                        "burstN": bad, "target": 90, "weight": 25})
        st = _burst_hide(st, share_bad)
        sup_rows = sorted(({"sup": s, **_burst_hide(_ms(_pct(v[1], v[0]) if v[0] else None, v[0], None,
                                                        n_key="n_supplier_quarter", plan_eq_fact=share_pef,
                                                        burst=v[2] / (v[0] + v[2]), extra={"k": v[1], "burstN": v[2]}),
                                                    v[2] / (v[0] + v[2]))}
                           for s, v in sup.items()), key=lambda x: (-(x["n"] or 0), x["sup"]))
        qs.append({"q": f"{q} кв. {y}", "Q1": st, "suppliers": sup_rows})

    # Q2: S5, S7, S8 и полный цикл (создание → доставка), закончившиеся за год
    year_from = today - dt.timedelta(days=ork.YEAR_DAYS)
    seg_vals: dict[str, list] = defaultdict(list)
    for o in measure:
        for code in ("S5", "S7", "S8"):
            s = (o.get("_segs") or {}).get("segs", {}).get(code)
            if s and _end(s) and year_from <= _end(s) <= today:
                seg_vals[code].append(s)
        c, f = o.get("created"), o.get("delFact")
        if c and f and year_from <= f <= today and f >= c:
            seg_vals["cycle"].append({"cd": (f - c).days})     # по датам карточки: переходы не нужны
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
    m0 = month["rows"][month.get("head", 0)] if month["rows"] else {}    # последний полный месяц
    q0 = quarter["quarters"][0] if quarter["quarters"] else {}

    def row(code, name, formula, fields, st):
        return {"code": code, "name": name, "formula": formula, "fields": fields,
                "state": st.get("state"), "why": st.get("why") or "", "n": st.get("n"),
                "cover": round(st["cover"], 3) if isinstance(st.get("cover"), float) else st.get("cover")}
    metrics = [
        row("W1", "Приёмка (S1)", "живые заказы в «Заказ создан» с р.д.(t0, сегодня) > 1 + сделки без заказа > 1 р.д.; "
            "t0 = max(создание заказа, вход сделки в реализацию); брошенные карточки и сделки вне «Реализации» — "
            "не в счёте; стадия не из таблицы — в n как «срок неизвестен»", "createdTime, crm.stagehistory", t["W1"]),
        row("W2", "Сверх нормы, только ОРК", "шаги 1, 4, 5, 8: р.д. от входа в шаг > нормы; стадия позади фактов, "
            "брошенная карточка — гигиена, не в n; стадия не из таблицы — в n как «срок неизвестен»; «без пауз». "
            f"Шаг 12 (УПД) — не в W2 и не в n (решение владельца 27.10.2026): р.д.(доставка, сегодня) > "
            f"{ork.NORM_WD[12]} и УПД нет — «{ork.WAIT_UTD}» отдельным столбцом (подпись УПД — за заказчиком), "
            f"пункт noUtd у менеджера ОРК с эскалацией КАМу; доставка старше {ork.CLOSE_DAYS} дн. без УПД — гигиена",
            "crm.stagehistory (W2); «ждём УПД» рядом с W2 — Customer delivery date actual, UTD", t["W2"]),
        row("W3", "Зона риска", "срок клиента прошёл / ≤ 14 дн. без отгрузки / прогноз позже срока",
            "Date of deadline to customer, Supplier Shipment Date", t["W3"]),
        row("W4", "Эскалации и паузы", "—", "нет полей", t["W4"]),
        row("W5", "Контрольные точки недели", "P50 = начало + ½(конец − начало); P14 = отгрузка − 14 дн. при "
            "производстве > 30 дн.", "Production start/end date, budget; Supplier Shipment Date, planned", t["W5"]),
        row("M1", "Своевременность S2+S3+S6", "сегменты, закончившиеся в месяце, без «пачек» и возвратов: в норме / "
            f"все; за ячейкой меньше {ork.GATES['n_managers_min']} ответственных заказа — не показываем (раскрыло бы "
            "человека; поимённо — под правом руководителей)", "crm.stagehistory (все переходы)",
            (m0.get("M1") or {}).get("mgr") or {}),
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
    # стадия сделки позади заказов — по диапазону шагов стадии сделки (ork.DEAL_SPAN, Рег. 1.7),
    # а не по одному её шагу: «Начато производство» при «Производство заказа» — норма (п. 8.1)
    for d in deals:
        if by_deal.get(d["deal"]) and ork.deal_behind(d.get("dealStep"), by_deal[d["deal"]]):
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
            "norms": {str(k): v for k, v in ork.NORM_WD.items()}, "segNorms": dict(ork.SEG_NORM),
            "noPause": ork.NO_PAUSE}
