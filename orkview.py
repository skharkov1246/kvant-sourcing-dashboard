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
            «Журнал шагов» — срок каждого заказа на S1/S2/S3/S6 в окне M1 (journal);
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
import re
from collections import Counter, defaultdict

import lifecycle
import ork
import period as period_mod

COUNT_GATES = ork.GATES      # счёт живых пунктов: n-ворота n_count = 1, покрытие — как у всех
# показатели «Месяца» — под правом «Реализация: показатели менеджеров ОРК» (ork_heads)
MONTH_CODES = ("M1", "M2", "M3")
RIGHT_NOTE = "под правом «Реализация: показатели менеджеров ОРК» — подвкладка «Месяц»"
# «Журнал шагов» «Месяца» (просьба владельца 29.10.2026: «где приёмка дольше 1 р.д. — списком в
# Excel»): строка на заказ и отрезок с нормой — те же S1, S2, S3, S6, окно и совокупность, что у
# M1; под тем же правом (массив ORKMONTH). Отметки — почему срок отрезка не в счёте M1, словами
JOURNAL_SEGS = ("S1", "S2", "S3", "S6")
SEG_STEP = {s["seg"]: s for s in ork.STEPS if s["seg"] in JOURNAL_SEGS}
J_FLAGS = (("burst", "пачкой — срок не мерится"), ("ret", "возврат на шаг раньше"),
           ("skip", "шаг пропущен"), ("neg", "порядок меток нарушен"))
J_NOCAL = "нет календаря"
J_OUT = "не в счёте M1: сделка вне воронки"
J_NOMGR = "не назначен"


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
    _quarter_drill(quarter, measure, today)                 # заказы за Q1 и Q3
    long_p80 = _long_p80([o for o in U if step[o["id"]] == 7], quarter["Q2"]["S5"], today)
    ledger, adjacent = [], Counter()
    for s in ork.STEPS:
        no = s["no"]
        here = [o for o in L if step[o["id"]] == no]
        waits = s["role"] if s["role"] in ork.ADJACENT else (
            s["waits"] if s["norm"] is None and s["waits"] in ork.ADJACENT else None)
        req = miss = 0
        miss_o: set = set()                 # Д-5а: разные заказы без файла (пар может быть больше)
        for a in ork.ARTIFACTS:
            if a["step"] != no:
                continue
            for o in U:
                if ork.art_required(o, a, reach_of.get(o["id"])):
                    req += 1
                    if not (o.get("art") or {}).get(a["key"]):
                        miss += 1
                        miss_o.add(o["id"])
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
                       "artReq": req, "artMiss": miss, "artOrders": len(miss_o), "waits": waits or "",
                       # Д-6: дольше всех на шаге с нормой — р.д. самого долгого заказа в счёте
                       "maxWd": max((ov[o["id"]]["wd"] for o in here if ov[o["id"]]["counted"]
                                     and ov[o["id"]]["wd"] is not None), default=None) if normed else None,
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
    s1_recent_o = [o for o in orders
                   if (o.get("_segs") or {}).get("segs", {}).get("S1") and in_cat.get(o.get("deal"), True)
                   and back and _end(o["_segs"]["segs"]["S1"]) and _end(o["_segs"]["segs"]["S1"]) > back]
    s1_recent = [o["_segs"]["segs"]["S1"] for o in s1_recent_o]      # заказы — в список за числом
    # «закончено назначением» — среди тех же отрезков, что в n своевременности (_timeliness: без
    # пропуска, «пачки», возврата, обратного порядка меток, р.д. известны): k ≤ n по построению
    by_assign = sum(1 for s in s1_recent if _clean(s) and s.get("wd") is not None
                    and s.get("endBy") == "назначение")
    # дата записи у «Заказ создан» не в счёте: раньше начала приёмки / ответственный не из ОРК
    st1 = [o for o in U if ork.stage_step(o.get("stage")) == 1]
    agree_s1, agree_n = _s1_agree(orders)
    recent = (_timeliness(s1_recent, "S1", agree=agree_s1) if back else
              _ms(None, 0, None, src="S1", need=f"нет производственного календаря на {today.year} год "
                                                "(data/workcal_ru.json) — 20 р.д. назад не отсчитать"))
    W1 = _count(w1_v, w1_n, (s1_known + no_known) / w1_n if w1_n else None, src="s1Over + noOrder",
                unknown=unk_n,
                extra={"orders": len(over_ids[1]), "noOrder": qcodes.get("noOrder", 0),
                       "label": ork.NO_PAUSE, "recent": recent,
                       "agree": agree_s1, "agreeN": agree_n,
                       # приёмка кончилась назначением менеджера ОРК раньше выхода из «Заказ создан»
                       # (за 20 р.д., из n своевременности); у живых «Заказ создан» дата записи не
                       # в счёте — раньше начала приёмки (assignStale) или ответственный не из ОРК
                       # (assignNoMgr): приёмка не сделана, счётом
                       "byAssign": by_assign,
                       "assignStale": sum(1 for o in st1 if ork.assign_stale(o)),
                       "assignNoMgr": sum(1 for o in st1 if ork.assign_no_mgr(o)),
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
    # Д-1 (R4): плитки «Срок клиента сорван» и «Срок под угрозой» — каждый заказ один раз, по
    # самому тяжёлому виду (важность lifecycle.EXC); сумма частей = W3.value (тот же ключ заказа)
    heavy = w3_heavy(risk_q)
    w3_deals: dict[str, set] = defaultdict(set)
    deal_of = {q.get("order"): q.get("deal") for q in risk_q}
    for k, c in heavy.items():
        w3_deals[c].add(str(deal_of.get(k)))
    if W3.get("state") in ("show", "prelim"):
        W3["byOrders"] = {c: sum(1 for v in heavy.values() if v == c) for c in W3_CODES}
        W3["byDeals"] = {c: len(w3_deals.get(c, ())) for c in W3_CODES}
    risk_list = [{"deal": q.get("deal"), "seq": q.get("seq"), "order": q.get("order"), "code": q["code"],
                  "ordLbl": q.get("ordLbl") or "", "sup": q.get("sup") or "", "ref": q.get("ref") or "",
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
                        "ordLbl": o.get("lbl") or "", "sup": o.get("sup") or "",
                        "date": p["date"], "who": (o.get("own") or {}).get("who") or "",
                        # Д-8: плановые даты точки — «середина производства 10.10 (план 01.09–20.10)»
                        "prodStart": _iso(o.get("prodStart")), "prodEnd": _iso(o.get("prodEnd")),
                        "shipPlan": _iso(o.get("shipPlan"))})
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
                              "ordLbl": o.get("lbl") or "", "ordNo": o.get("ordNo"), "sup": o.get("sup") or "",
                              "step": step[o["id"]],
                              "excl": bool(h["exclude"] or step[o["id"]] is None),
                              "why": h.get("why") or ("менеджер ОРК не назначен" if "noMgr" in kinds else "")})
    # заказы одной сделки — подряд и по номеру заказа («933/2» раньше «933/10»)
    hyg_items.sort(key=lambda x: (-(x["seq"] or 0), str(x["deal"]),
                                  x["ordNo"] if x["ordNo"] is not None else 10 ** 6, x["order"]))
    # не в счёте «сверх нормы»: стадия позади фактов, брошенная (шаги 1, 4, 5, 8), старая
    # доставка без УПД (шаг 12), стадия не из таблицы; «менеджер ОРК не назначен» в счёте ЕСТЬ —
    # назначить (решение п. 1)
    hyg_excl = sum(1 for x in hyg_items if x["excl"])
    # «в счёт не идут: N (…)» — разбивка только по заказам вне счёта: доставленный без УПД с
    # отставшей стадией (шаг 12) в счёте ЕСТЬ и в скобки не идёт
    hc_excl = Counter(k for x in hyg_items if x["excl"] for k in x["kinds"])

    # --- R4: неоплата (Д-3), сорванный срок по исполнителю (Д-7), история сорванных (Д-12) ---
    unpaid = _unpaid(q_by.get("unpaid", []), deal_by, today)
    late_who = Counter((q.get("who") or PULSE_NONE) for q in risk_q
                       if heavy.get(q.get("order")) == "late" and q["code"] == "late")
    late_tile = {"hist": late_hist(orders, deal_by, today), "fridays": [d.isoformat() for d in _fridays(today)],
                 "label": "переносы срока не хранятся — по нынешнему сроку клиента"}
    week = {"mon": mon.isoformat(), "sun": sun.isoformat(),
            "tiles": {"W1": W1, "W2": W2, "W3": W3, "W4": W4, "W5": W5, "unpaid": unpaid, "late": late_tile},
            "lateByWho": dict(late_who),
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
    # заказы за числами «Недели» и «Пульс ОРК» (решение владельца 29.10.2026) — те же множества
    week.update(_week_drill(L=L, U=U, C12=C12, deals=deals, queue=queue, step=step, ov=ov,
                            over_ids=over_ids, wait_ids=wait_ids, long_p80=long_p80, cps=cps,
                            hyg_items=hyg_items, no_order=no_order, risk_q=risk_q, reach_of=reach_of,
                            measure=measure, today=today, ledger=ledger, s1_recent_o=s1_recent_o,
                            recent=recent))
    # Д-9 и R4: ключи за счётами подписей «Недели» (каждый — непустой список при счёте > 0)
    D = week["drill"]
    D["W3p"] = w3_parts(risk_q, heavy)
    D["unpaid"] = {"k": unpaid.pop("keys"), "t": unpaid.pop("tags")}
    D["W1byAssign"] = [str(o["id"]) for o in s1_recent_o
                       if _clean(o["_segs"]["segs"]["S1"]) and o["_segs"]["segs"]["S1"].get("wd") is not None
                       and o["_segs"]["segs"]["S1"].get("endBy") == "назначение"]
    D["W1assignStale"] = [str(o["id"]) for o in st1 if ork.assign_stale(o)]
    D["W1assignNoMgr"] = [str(o["id"]) for o in st1 if ork.assign_no_mgr(o)]
    D["noCal"] = [str(o["id"]) for o in L if step[o["id"]] in ork.OVER_CODE
                  and ov[o["id"]]["why"] == "год без производственного календаря"]
    D["W5prod"] = [str(o["id"]) for o in prod]
    D["W5noDates"] = [str(o["id"]) for o in prod if not (o.get("prodStart") and o.get("prodEnd"))]
    # Д-7: сорванный срок по исполнителю пункта — ключи рядом с «дольше нормы» и «ждём УПД»
    for q in risk_q:
        if q["code"] == "late" and heavy.get(q.get("order")) == "late":
            D["who"].setdefault(q.get("who") or PULSE_NONE, {"over": [], "wait": []}) \
                .setdefault("late", []).append(str(q.get("order") or ""))

    month, month_people, journal = _month(orders, U, today, otd, ov, deal_ok_all={
        d["deal"]: d.get("inCat0", True) for d in deals}, ork_head=ork_head)
    # заказы за числами «Месяца» — в массив ORKMONTH (под правом ork_heads)
    _month_drill(month, month_people, orders, U, today, deal_ok_all={d["deal"]: d.get("inCat0", True) for d in deals})
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
            {"month": month, "people": month_people, "how": m_rows, "journal": journal})


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
                          # Д-8: с какого дня в производстве и плановый конец
                          "since": start.isoformat(), "prodEnd": _iso(o.get("prodEnd")),
                          "ordLbl": o.get("lbl") or "", "ordNo": o.get("ordNo"),
                          "sup": o.get("sup") or "", "who": (o.get("own") or {}).get("who") or ""})
    # заказы одной сделки — подряд (по самому долгому из них), затем по номеру заказа
    top: dict = {}
    for x in items:
        top[str(x["deal"])] = max(top.get(str(x["deal"]), 0), x["days"])
    items.sort(key=lambda x: (-top[str(x["deal"])], str(x["deal"]),
                              x["ordNo"] if x["ordNo"] is not None else 10 ** 6, -x["days"], x["order"]))
    known = len(prod) - unknown
    return {"state": s5["state"], "value": len(items), "k": len(items), "n": known, "unknown": unknown,
            "p80": p8, "items": items, "label": label, "src": "Q2 S5",
            "why": s5.get("why") or ""}


def _s1_agree(orders: list[dict]) -> tuple[float | None, int]:
    """Совпадение двух опор конца приёмки (в пределах agree_wd р.д.): выход заказа из «Заказ
    создан» по переходам и «Дата записи нового ответственного» (с 28.08.2026).

    С 29.10.2026 приёмка кончается раньше из двух (ork.segments, вопрос владельца): пара
    меряется только там, где стадия сдвинулась первой (S1.endBy «стадия»). Где приёмку закончило
    назначение, конец S1 и есть эта дата — пара совпала бы по построению, а поздний выход из
    «Заказ создан» — уже шаг 4 (заказ законно стоит в стадии до направления), не расхождение
    опор: такие пары не идут ни в совпавшие, ни в число пар. Дата записи раньше дня начала
    приёмки — скопирована или устарела (ork.assign_stale): в S1 её нет, в пары — тоже."""
    pairs = ok = 0
    for o in orders:
        s1 = (o.get("_segs") or {}).get("segs", {}).get("S1")
        b = _d(o.get("assignDate"))
        if not s1 or not b or b < ork.S1_ANCHOR_SINCE:
            continue
        if s1.get("endBy") == "назначение" or ork.assign_stale(o):
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


def _dmy(v) -> str:
    """Дата отрезка по Москве — дд.мм.гггг (как видит её руководитель в Битриксе)."""
    d = _d(v)
    return d.strftime("%d.%m.%Y") if d else ""


def _jrow(o: dict, code: str, s: dict, month: str, *, counted: bool, head: str) -> dict:
    """Строка «Журнала шагов»: заказ × отрезок. Срок — тот же сегмент ork.segments, что считает
    M1 (р.д. по календарю РФ, по Москве); отметки — словами, почему срок не мерится (ни одной —
    срок в счёте M1, если сделка в воронке). Хозяин — исполнитель шага по регламенту: S1 —
    руководитель ОРК, S2, S3, S6 — менеджер ОРК заказа (ответственный из отдела ОРК)."""
    st, norm, wd = SEG_STEP[code], ork.SEG_NORM[code], s.get("wd")
    flags = [lbl for k, lbl in J_FLAGS if s.get(k)] + ([J_NOCAL] if wd is None else [])
    return {"deal": o.get("deal") or "", "seq": o.get("seq") or 0, "order": o.get("id") or "",
            "title": o.get("title") or "", "sup": o.get("sup") or "",
            "seg": code, "step": st["no"], "stepName": st["name"],
            "start": _dmy(s.get("start")), "end": _dmy(s.get("end")), "_e": (_end(s) or dt.date.min).isoformat(),
            "wd": wd, "norm": norm, "over": wd - norm if wd is not None and wd > norm else None,
            "inNorm": s.get("inNorm"), "flags": "; ".join(flags), "measurable": not flags,
            "counted": counted, "note": "" if counted else J_OUT,
            # имени руководителя ОРК в справочнике отделов нет — имя пустое, роль — в ownerRole
            # (иначе в ячейке «руководитель ОРК руководитель ОРК»)
            "owner": (head or "") if code == "S1" else (o.get("orkMgr") or J_NOMGR),
            "ownerRole": ork.ROLES[st["role"]], "ownerHidden": False, "ownerWhy": "", "m": month}


def _month(orders: list[dict], U: list[dict], today: dt.date, otd: dict | None, ov: dict, *,
           deal_ok_all: dict | None = None, ork_head: str = ""):
    """«Месяц»: M1–M3 по заказам ОРК-эпохи (созданы с ork.ORK_SINCE). Поимённо — отдельно.
    deal_ok_all — сделка в воронке «Реализация»: приёмка (S1) сделки вне воронки не в счёте
    (Рег. 3.3), как у ork.judged и W1. → (month, people, journal): journal — «Журнал шагов»,
    строка на заказ и отрезок S1/S2/S3/S6, закончившийся в окне «Месяца» (те же совокупность и
    окно, что у M1; приёмка сделки вне воронки — строкой с отметкой «не в счёте M1»)."""
    deal_ok_all = deal_ok_all or {}
    era = [o for o in orders if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    agree_s1, _n = _s1_agree(era)
    otd_by = {m["m"]: m for m in (otd or {}).get("months") or []}
    # поимённо — текущий (неполный) и прошлый полный месяц: в начале месяца у текущего данных нет
    rows, people = [], defaultdict(lambda: {"segs": defaultdict(list), "req": 0, "have": 0})
    journal: list[dict] = []
    months = _months(today)
    for mi, m0 in enumerate(months):
        m1 = dt.date(m0.year + (m0.month == 12), m0.month % 12 + 1, 1)
        by_seg: dict[str, list] = defaultdict(list)
        seg_who: dict[str, set] = defaultdict(set)      # люди за ячейкой — ворота «раскрыл бы человека»
        jm: dict[str, list] = defaultdict(list)         # строки журнала месяца по отрезку
        art_who: set = set()
        req = have = 0
        for o in era:
            for code in JOURNAL_SEGS:
                s = (o.get("_segs") or {}).get("segs", {}).get(code)
                if not (s and _end(s) and m0 <= _end(s) < m1):
                    continue
                # приёмка — только у сделок «Реализации»: в M1 не идёт, в журнале — с отметкой
                counted = code != "S1" or deal_ok_all.get(o.get("deal"), True)
                jm[code].append(_jrow(o, code, s, m0.strftime("%m.%Y"), counted=counted, head=ork_head))
                if not counted:
                    continue
                by_seg[code].append(s)
                if _person(o):
                    seg_who[code].add(_person(o))
                if o.get("respOrk") and code != "S1" and mi < 2:
                    people[(mi, o.get("orkMgr") or "не назначен")]["segs"][code].append(s)
            for a in ork.ARTIFACTS:
                # день прохождения шага — как у ведомости недели (ork.passed_at: на шагах 2–4 и
                # назначение менеджера ОРК, если оно раньше выхода из «Заказ создан»)
                at = ork.passed_at(o, a["need"])
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
        # ячейка отдела скрыта воротами людей («раскрыл бы человека») — в её строках журнала
        # имени нет, с той же причиной; заказы и сроки остаются (по ним работать)
        for c in ("S2", "S3", "S6"):
            if m1s[c].get("peopleGate"):
                for r in jm[c]:
                    r.update(owner="", ownerHidden=True, ownerWhy=m1s[c].get("why") or "")
        journal += [r for c in JOURNAL_SEGS for r in jm[c]]
        m2 = _people_gate(_ms(_pct(have, req) if req else None, req, None, src="ork.ARTIFACTS",
                              extra={"have": have, "label": "файл есть, содержимое не проверено", "target": 95}),
                          art_who)
        o_m = otd_by.get(m0.strftime("%m.%Y")) or {}
        otd_st = _ms(o_m.get("pct"), o_m.get("n") or 0, None, src="life.otd",
                     extra={"ontime": o_m.get("ontime"), "label": "результат поставщика и логистики"})
        # Д-2 (R4): главная доля «Месяца» — к сроку из ВСЕХ, у кого срок клиента наступил в месяце
        # (lifecycle.otd: due = к сроку + доставлены позже + не доставлены после срока); прежняя
        # доля по доставленным (otd) остаётся в данных, на странице её нет
        due_n = o_m.get("due") or 0
        due_st = _ms(o_m.get("duePct"), due_n, None, src="life.otd.due",
                     extra={"k": o_m.get("dueOnTime") or 0, "late": o_m.get("dueLateDelivered") or 0,
                            "open": o_m.get("dueOpenLate") or 0, "target": 95, "warn": 80,
                            "label": "из всех, у кого срок клиента наступил в месяце"})
        # текущий месяц идёт — неполный: заголовок «Месяца» — последний ПОЛНЫЙ месяц
        # первый месяц эпохи ОРК (24.08 — середина месяца) неполный: в счёте только отрезки
        # заказов, созданных с 24.08 и успевших закончиться, — сдвиг к «в норме»
        rows.append({"m": m0.strftime("%m.%Y"), "M1": m1s, "M2": m2, "otd": otd_st, "otdDue": due_st,
                     "_due": o_m.get("keys") or {"k": [], "t": []}, "target": 95,
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
             # ключи «Доставки к сроку по месяцам» (12 месяцев lifecycle.otd) — _month_drill кладёт их
             # в drill «y.<мм.гггг>» и убирает это поле
             "_otd": [{"m": m.get("m"), "due": m.get("due") or 0, "keys": m.get("keys") or {}}
                      for m in (otd or {}).get("months") or []],
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
    # журнал: сначала сверх нормы — по убыванию превышения, затем по дате конца (раньше — выше)
    journal.sort(key=lambda r: (r["over"] is None, -(r["over"] or 0), r["_e"], -(r["seq"] or 0),
                                str(r["order"]), r["seg"]))
    for r in journal:
        r.pop("_e", None)
    jour = {"rows": journal, "months": [m.strftime("%m.%Y") for m in months], "since": ork.ORK_SINCE.isoformat(),
            "segs": [{"seg": c, "step": SEG_STEP[c]["no"], "name": SEG_STEP[c]["name"], "norm": ork.SEG_NORM[c],
                      "role": ork.ROLES[SEG_STEP[c]["role"]]} for c in JOURNAL_SEGS],
            "label": ork.NO_PAUSE}
    return month, mp, jour


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
        row("W1", "Приёмка (S1)", "живые заказы в «Заказ создан» без назначения менеджера ОРК с р.д.(t0, сегодня) > 1 "
            "+ сделки без заказа > 1 р.д.; t0 = max(создание заказа, вход сделки в реализацию); приёмка кончается "
            "раньше из двух: выход из «Заказ создан» по стадии или «Дата записи нового ответственного» (не раньше "
            "дня t0 и ответственный заказа из отдела ОРК, иначе не в счёте) — после назначения заказ на шаге 4, срок "
            "договора с поставщиком (2 р.д.) — от назначения; брошенные карточки и сделки вне «Реализации» — не в "
            "счёте; стадия не из таблицы — в n как «срок неизвестен»", "createdTime, crm.stagehistory, ufCrm20_1787924508120", t["W1"]),
        row("W2", "Сверх нормы, только ОРК", "шаги 1, 4, 5, 8: р.д. от входа в шаг > нормы (шаг 4 — от назначения "
            "менеджера ОРК, если приёмку закончило оно, ork.clock); стадия позади фактов, "
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
            "человека; поимённо — под правом руководителей). S2 начинается с назначения менеджера ОРК («Дата записи "
            "нового ответственного»), если оно раньше выхода из «Заказ создан»; стадия прыгнула сразу на шаг 5 и "
            "дальше — S2 пропущен и не в счёте, как и без назначения", "crm.stagehistory (все переходы), "
            "ufCrm20_1787924508120", (m0.get("M1") or {}).get("mgr") or {}),
        row("M2", "Полнота карточки", "Σ есть / Σ требуется по парам (заказ, артефакт) у заказов, прошедших шаг "
            "(шаги 2–4 — и назначением менеджера ОРК, если оно раньше стадии: ork.passed_at)",
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
    drill = _how_drill(orders, U, deals, queue, week, step, violations, arts,
                       len(kam_pairs) - sum(1 for a, b in kam_pairs if a == b))
    return {"metrics": metrics, "violations": violations, "artifacts": arts, "drill": drill,
            "kamAgree": {"n": len(kam_pairs), "same": sum(1 for a, b in kam_pairs if a == b)},
            "fieldsAdd": [{"kind": k, "name": n, "for": f, "to": ork.FIELDS_TO, "status": "добавить"}
                          for k, n, f in ork.FIELDS_ADD],
            "fieldsNote": "список для администратора Битрикса; никуда не отправлен",
            "gates": dict(ork.GATES), "gatesNote": ork.GATES_NOTE,
            "norms": {str(k): v for k, v in ork.NORM_WD.items()}, "segNorms": dict(ork.SEG_NORM),
            "noPause": ork.NO_PAUSE, "steps": _how_steps()}


def _how_drill(orders, U, deals, queue, week, step, violations: dict, arts: list, kam_miss: int) -> dict:
    """Д-10 (R4): заказы за числами «Как считается» — нарушения порядка стадий, «КАМ заказа ≠ КАМ
    сделки», заказы без обязательного файла шага. Те же отборы, что у счёта в _how; ключ — только
    когда длина списка равна числу на странице (страница открывает его кнопкой у числа). Сделка —
    «d:<id>», как строки сделок «Недели»."""
    D: dict = {}

    def put(key: str, ks: list, ts: list | None, n, head: str) -> None:
        if ks and len(ks) == n:
            D[key] = {"k": ks, "t": ts, "h": head}
    sg = [(o, o.get("_segs") or {}) for o in orders]
    put("returns", [str(o["id"]) for o, g in sg if g.get("returns")], None, violations.get("returns"),
        "Заказ вернули на стадию назад")
    put("bursts", [str(o["id"]) for o, g in sg if g.get("burst")], None, violations.get("bursts"),
        "Стадии прокликали разом: 3 и больше смены за 5 минут")
    put("negative", [str(o["id"]) for o, g in sg if any(x.get("neg") for x in (g.get("segs") or {}).values())],
        None, violations.get("negative"), "Даты стадий в обратном порядке")
    pnc = [q for q in queue if q.get("code") == "payNoContract"]
    put("payNoContract", [("d:" + str(q.get("deal"))) if not q.get("order") else str(q["order"]) for q in pnc],
        None, violations.get("payNoContract"), "Оплатили поставщику без договора с клиентом")
    by_deal: dict = defaultdict(list)
    for o in U:
        if step[o["id"]] is not None:
            by_deal[o.get("deal")].append(step[o["id"]])
    beh = [d for d in deals if by_deal.get(d["deal"]) and ork.deal_behind(d.get("dealStep"), by_deal[d["deal"]])]
    put("dealBehindOrders", ["d:" + str(d["deal"]) for d in beh], None, violations.get("dealBehindOrders"),
        "Стадия сделки отстала от заказов")
    hy = (week.get("hygiene") or {}).get("items") or []
    put("behindFacts", [str(x["order"]) for x in hy if "behind" in (x.get("kinds") or [])], None,
        violations.get("behindFacts"), "Стадия заказа отстала от фактов: оплата, отгрузка, доставка")
    put("unknownStage", [str(o["id"]) for o in U if step[o["id"]] is None], None, violations.get("unknownStage"),
        "Стадия неизвестна регламенту: сверить стадию в Битриксе")
    miss = [o for o in orders if o.get("kamOrder") and o.get("kamDeal") and o["kamOrder"] != o["kamDeal"]]
    put("kamMiss", [str(o["id"]) for o in miss], None, kam_miss, "КАМ в заказе не совпадает с КАМом сделки")
    for a, row in zip(ork.ARTIFACTS, arts):
        no = [o for o in orders if ork.art_required(o, a, o.get("_reach")) and not (o.get("art") or {}).get(a["key"])]
        put("art." + a["key"], [str(o["id"]) for o in no], [f"нет: {a['name']}"] * len(no),
            (row.get("req") or 0) - (row.get("have") or 0), f"Нет файла «{a['name']}» в карточке заказа")
    return D


def _how_steps() -> list[dict]:
    """Справочник шагов для «Как считается» (просьба владельца 29.10.2026: «чтобы логика читалась
    внятно»): у шага — стадии Битрикса, на которых он идёт, стадия, которой он заканчивается, и кому
    исполнитель эскалирует. Из тех же таблиц, что считают шаг (ork.STAGE_STEP, ork.STEPS), — не из
    вёрстки (правило 4). Шаги 2–3 у заказа своей стадии не имеют — стадии сделки."""
    out = []
    for s in ork.STEPS:
        no = s["no"]
        kind = "order" if any(v == no for v in ork.STAGE_STEP["order"].values()) else "deal"
        table = ork.STAGE_STEP[kind]
        stages = [k for k, v in table.items() if v == no]
        nxt = next((k for k, v in table.items() if v > no), "")
        out.append({"no": no, "kind": kind, "stages": stages, "next": nxt,
                    "esc": ork.ROLES.get(s["esc"] or "", "")})
    return out


# =============================================================================================
# Списки заказов за числами (решение владельца 29.10.2026, «Неделя», п. 4–5: «не раскрывает
# данные для анализа, какие это конкретно заказы»; «пульс по сотрудникам ОРК — по аналогии с
# сорсом»). Каждое число «Недели», «Месяца» и «Квартала», за которым стоят заказы, получает
# список их ключей — из тех же множеств, по которым число посчитано; страница открывает по нему
# список заказов (дровер), и строк в нём столько же, сколько в числе (тесты test_ork_drill):
#   week.orders — живые заказы «Недели» (та же совокупность, что week.live: U + C12);
#   week.deals  — сделки за числами уровня сделки (без заказа — W1; «закрыть сделку» — шаг 13);
#   week.hist   — законченные заказы за «в срок за 20 р.д.» «Моей приёмки» (без менеджера ОРК);
#   week.drill  — число → ключи; week.pulse — «Пульс ОРК» по людям (Σ строк = week.live,
#                 по шагам — ведомость; заказы строки — week.orders с mgr == who, без ключей);
#   quarter.orders, quarter.drill — Q1 по кварталу и поставщику, Q3; у показателя — dk; строки
#                 без менеджера ОРК (поимённо — только «Месяц» и «Пульс»);
#   month.orders, month.drill (массив ORKMONTH — только праву ork_heads) — M1, M2, доставка в
#                 срок по месяцам и поимённо.
# Ключ — id заказа СП-172 строкой; сделка — «d:<id>». Список с подписью строки — {k, t, h}: подпись
# начинается классом («в срок», «в норме», «P50») — до « · » по нему список группирует строки.
# Подписи строк (стадия, поставщик, люди) — номером
# в словаре strs своей части (_pack; развернуть — unpack). Скрытая ячейка («не показываем»,
# «Нет данных») ключей не получает: иначе список раскрыл бы скрытое число (ворота людей
# «Месяца»); исключение — строка поставщика «Квартала» со скрытой долей: её видимое число
# готовностей получает список без подписей «в срок / позже» (nDk). Поставщик — только там, где
# он уже есть у вида (CONTRACTS: rows[].orders[].sup, очередь); денег в строках нет. Коды
# закрытой части (DEALCTX: чаты, договоры) в строки не идут — страница добавляет их сама, если
# право есть.
# =============================================================================================
PULSE_OUT = "ответственный вне ОРК"      # менеджера ОРК нет, ответственный заказа — не из ОРК
PULSE_NONE = "не назначен"               # ни менеджера ОРК, ни ответственного заказа
PULSE_ORDER = {"mgr": 0, "out": 1, "none": 2}


def order_no(o: dict) -> str:
    """Номер заказа, как его называет портал, — тот же, что у пункта очереди (ordLbl), таблиц
    недели и карточки: ork.order_label («900/1» в начале названия; дата в начале — не номер;
    номера нет — «id <id СП-172>»). Строка заказа вида уже несёт его (contracts: lbl)."""
    return o.get("lbl") or ork.order_label(o.get("title"), o.get("id"))[0] or f"id {o.get('id')}"


def _nm(v) -> str:
    return "" if v in (None, "", "—", PULSE_NONE) else str(v)


def pulse_who(o: dict, d: dict | None) -> tuple[str, str]:
    """Менеджер ОРК заказа — правилом хозяина (ork.owner, роль orkMgr): ответственный заказа из
    отдела ОРК, иначе поле сделки «Ответственный за реализацию». Нет — «ответственный вне ОРК»
    (ответственный заказа есть, но не из ОРК) или «не назначен» (ответственного нет). В очереди
    и «Сверх нормы по менеджерам» обе группы — «не назначен». → (подпись, вид mgr|out|none)."""
    w = _nm(o.get("orkMgr")) or _nm((d or {}).get("orkResp"))
    if w:
        return w, "mgr"
    return (PULSE_OUT, "out") if _nm(o.get("resp")) else (PULSE_NONE, "none")


def _iso(v) -> str:
    d = _d(v)
    return d.isoformat() if d else ""


def _compact(r: dict) -> dict:
    """Строка списка без пустых полей (None, "", [], False): payload — компактный."""
    return {k: v for k, v in r.items()
            if k in ("k", "step") or not (v is None or v is False or v == "" or v == [])}


def _dk(st: dict, drill: dict, key: str, keys: list, tags: list | None = None, *, prefix: str,
        head: str = "") -> None:
    """Показатель с заказами за ним: ключи — drill[key] ({k, t, h}: ключи, подписи строк,
    заголовок списка), у показателя — dk «<prefix>:<key>». Только у показанного (show, prelim):
    у «не показываем» и «Нет данных» списка нет."""
    if not isinstance(st, dict) or st.get("state") not in ("show", "prelim"):
        return
    drill[key] = {"k": keys, "t": tags, "h": head}
    st["dk"] = f"{prefix}:{key}"


def _hist_row(o: dict, *, with_mgr: bool = True) -> dict:
    """Строка заказа «Месяца», «Квартала» и приёмки за 20 р.д. (живого и законченного): номер,
    сделка, поставщик, стадия; шаг — у живого. Менеджер ОРК (по ответственному заказа: поле
    сделки у мерила «по всем заказам» не читается) — только в «Месяце»: он под правом ork_heads.
    «Квартал» и «Неделю» видят все с вкладкой, а поимённо там — только «Пульс ОРК» живых заказов
    (with_mgr=False): иначе список Q1 дал бы поимённый счёт «в срок / позже»."""
    live = bool(o.get("live"))
    r = {"k": str(o["id"]), "no": order_no(o), "deal": str(o.get("deal") or ""),
         "seq": o.get("seq") or 0, "sup": o.get("sup") or "", "stage": o.get("stage") or "",
         "step": ork.step_now(o) if live else None, "closed": not live,
         # Д-10 (R4): срок клиента и факт доставки — список за числом говорит «к сроку / позже»
         "dl": _iso(o.get("dl")), "delFact": _iso(o.get("delFact"))}
    if with_mgr:
        r["mgr"] = pulse_who(o, None)[0]
    return _compact(r)


# повторяющиеся подписи строк (стадия, поставщик, люди) — номером в словаре своей части данных
# (strs): строка весит вдвое меньше; страница разворачивает их в одном месте (_odRows)
PACKED = ("stage", "sup", "mgr", "own", "kam")


def _pack(*lists: list[dict]) -> list[str]:
    # номер 0 — пустая подпись (её в строках нет): номера подписей ≥ 1, и столбец номеров не
    # приходит «весь нулевой» в живой сверке, когда у вида одна стадия или один менеджер
    strs: list[str] = [""]
    idx: dict[str, int] = {"": 0}

    def put(r: dict) -> None:
        for f in PACKED:
            v = r.get(f)
            if isinstance(v, str) and v:
                if v not in idx:
                    idx[v] = len(strs)
                    strs.append(v)
                r[f] = idx[v]
    for rows in lists:
        for r in rows:
            put(r)
    return strs


def unpack(rows: list[dict], strs: list[str]) -> list[dict]:
    """Строки части данных с подписями вместо номеров словаря strs (читателям и тестам) — так
    же, как их разворачивает страница."""
    def get(r: dict) -> dict:
        o = dict(r)
        for f in PACKED:
            v = o.get(f)
            if isinstance(v, int) and not isinstance(v, bool):
                o[f] = strs[v]
        return o
    return [get(r) for r in rows]


# --- R4 «Неделя» (WP-B): части W3, неоплата, история сорванных, причина заказа ----------------
W3_CODES = ("late", "risk", "forecast")
# кого ждёт шаг (ork.STEPS[].waits): kam — шаги 1–3, 10, 12, 13; oss — 6; sup — 7; log — 9, 11
STEP_WAITS = {s["no"]: s["waits"] or "" for s in ork.STEPS}
OUR_CODES = frozenset(ork.OVER_CODE.values()) | {"noOrder"}
# причина заказа (Д-4, порядок — решение R4, «Как считается»): первая подходящая — cause, вторая —
# cause2. our — шаг ОРК дольше нормы; sup — производство дольше плана или шаг ждёт поставщика;
# log / kam / oss — шаг ждёт этот отдел; nodata — нет плановых дат или срока, шаг неизвестен;
# ничего не подошло — мяч у ОРК на его шаге в норме (our)
CAUSES = ("our", "sup", "log", "kam", "oss", "nodata")


def order_cause(codes, step, over: bool = False) -> tuple[str, str]:
    """(cause, cause2) заказа «Недели»: одна причина на заказ — сумма причин по любому списку
    равна числу заказов в нём (инвариант Д-4). Порядок: our (шаг ОРК дольше нормы) → sup → log →
    kam → oss → nodata; ни одна не подошла — ork (на шаге ОРК, срок в норме)."""
    cs = set(codes or ())
    w = STEP_WAITS.get(step, "") if step is not None else ""
    hit = [c for c, ok in (("our", over or bool(cs & OUR_CODES)),
                           ("sup", "prodDev" in cs or w == "sup"),
                           ("log", w == "log"), ("kam", w == "kam"), ("oss", w == "oss"),
                           ("nodata", step is None or bool(cs & {"noPlanDates", "noDl"}))) if ok]
    # остаток — заказ на шаге ОРК, срок шага в норме (или шаг без нормы): не «дольше нормы» (ревью
    # раунда 3: «our» без превышения читался как «наш шаг дольше нормы»)
    if not hit:
        hit = ["ork"]
    return hit[0], (hit[1] if len(hit) > 1 else "")


def w3_heavy(risk_q: list[dict]) -> dict:
    """Заказ → самый тяжёлый вид пункта W3 (важность lifecycle.EXC: late < risk < forecast)."""
    sev = {c: lifecycle.EXC[c][0] for c in W3_CODES}
    out: dict = {}
    for q in risk_q:
        k, c = q.get("order"), q["code"]
        if k not in out or sev[c] < sev[out[k]]:
            out[k] = c
    return out


def w3_parts(risk_q: list[dict], heavy: dict) -> dict:
    """Ключи частей W3 по заказам (Д-1): late, risk, forecast и threat = risk + forecast."""
    P: dict = {c: {"k": [], "t": []} for c in (*W3_CODES, "threat")}
    seen: set = set()
    for q in risk_q:
        k = q.get("order")
        if k in seen or heavy.get(k) != q["code"]:
            continue
        seen.add(k)
        for part in (q["code"], "threat") if q["code"] != "late" else ("late",):
            P[part]["k"].append(str(k or ""))
            P[part]["t"].append(q["code"])
    return P


def _unpaid(items: list[dict], deal_by: dict, today: dt.date) -> dict:
    """Д-3: «Клиент не оплатил в срок» — разные заказы пунктов unpaid, их сделки, продажа этих
    сделок в € без НДС (это не долг: оплату в деньгах портал не видит — решение Р-2), старейший
    срок оплаты; ключи и подписи — в drill (их забирает build)."""
    seen: dict = {}
    for q in items:
        k = q.get("order")
        if k and (k not in seen or (q.get("days") or 0) > (seen[k].get("days") or 0)):
            seen[k] = q
    deals = {str(q.get("deal")) for q in seen.values()}
    sale = sum((deal_by.get(d) or {}).get("saleEur") or 0 for d in deals
               if (deal_by.get(d) or {}).get("saleKnown", True))
    days = [q.get("days") for q in seen.values() if q.get("days") is not None]
    oldest = (today - dt.timedelta(days=max(days))).isoformat() if days else ""
    return {"orders": len(seen), "deals": len(deals), "saleEur": round(sale), "oldest": oldest,
            "label": "продажа сделок без НДС, не сумма долга", "keys": [str(k) for k in seen],
            "tags": [q.get("ref") or "" for q in seen.values()]}


def _fridays(today: dt.date, n: int = 12) -> list[dt.date]:
    """n прошлых пятниц (строго раньше сегодня), от старой к новой."""
    last = today - dt.timedelta(days=(today.weekday() - 4) % 7 or 7)
    return [last - dt.timedelta(weeks=i) for i in range(n - 1, -1, -1)]


def late_hist(orders: list[dict], deal_by: dict, today: dt.date) -> list[int]:
    """Д-12: «срок клиента сорван» на каждую из 12 прошлых пятниц t — заказы непроигранных
    сделок, не проваленные, созданные не позже t, у которых срок клиента раньше t, а доставки
    к t нет. Переносы срока не хранятся — по нынешнему сроку (оговорка у спарклайна)."""
    out = []
    for t in _fridays(today):
        n = 0
        for o in orders:
            d = deal_by.get(o.get("deal"))
            if not d or d.get("lost") or not (o.get("live") or o.get("success")):
                continue
            cr, dl = _d(o.get("created")), _d(o.get("dl"))
            fact = _d(o.get("delFact")) or _d(o.get("utdDate"))
            if dl and dl < t and (cr is None or cr <= t) and (fact is None or fact > t):
                n += 1
        out.append(n)
    return out


def _week_drill(*, L, U, C12, deals, queue, step, ov, over_ids, wait_ids, long_p80, cps, hyg_items,
                no_order, risk_q, reach_of, measure, today, ledger, s1_recent_o, recent) -> dict:
    """Строки заказов и сделок «Недели», ключи каждого числа и «Пульс ОРК». Множества — те же,
    что у чисел в build (over_ids, wait_ids, ведомость, пункты очереди с w2 и waitUtd)."""
    deal_by = {d["deal"]: d for d in deals}
    q_by: dict[str, list] = defaultdict(list)
    codes_of: dict[str, list] = defaultdict(list)       # заказ → коды пунктов очереди (life.queue)
    dcodes: dict[str, list] = defaultdict(list)          # сделка → коды пунктов уровня сделки
    for q in queue:
        q_by[q["code"]].append(q)
        if q.get("order"):
            codes_of[str(q["order"])].append(q["code"])
        else:
            dcodes[str(q.get("deal") or "")].append(q["code"])
    # прогноз доставки — как у очереди (lifecycle.build): звенья по всем заказам, обычное
    # опоздание поставщика — от SUP_MIN отгрузок
    lead, sup_rel = lifecycle.speeds(measure, today)
    med = {k: ork.median(v) for k, v in lead.items()}
    sup_slip = {k: ork.median(v) for k, v in sup_rel.items() if len(v) >= lifecycle.SUP_MIN}
    hyg_of = {x["order"]: x for x in hyg_items}
    c12 = {o["id"] for o in C12}
    p80_ok = long_p80.get("state") in ("show", "prelim")
    p80 = [str(x["order"]) for x in (long_p80.get("items") or [])] if p80_ok else []
    p80s = set(p80)
    waits_of = {s["no"]: s["waits"] for s in ledger}

    def nc(o) -> bool:
        v = ov[o["id"]]
        return v["norm"] is not None and not v["counted"] and not v["termUnknown"]

    rows = []
    for o in L:
        oid = o["id"]
        d = deal_by.get(o.get("deal")) or {}
        v, st = ov[oid], step[oid]
        who, kind = pulse_who(o, d)
        own = o.get("own") or {}
        h = hyg_of.get(oid) or {}
        r = {"k": str(oid), "no": order_no(o), "deal": str(o.get("deal") or ""), "seq": o.get("seq") or 0,
             "sup": o.get("sup") or "", "stage": o.get("stage") or "", "step": st,
             "wd": v.get("wd"), "norm": v.get("norm"), "mgr": who, "mk": kind,
             "gone": who.endswith("(уволен)"),
             "own": own.get("who") or "", "ownR": own.get("role") or "", "kam": _nm(d.get("kam")),
             "dl": _iso(o.get("dl")), "fc": _iso(lifecycle.forecast(o, today, med, sup_slip)),
             "q": codes_of.get(str(oid), []),
             # от какого дня считаны р.д. шага — «с 14.10 (назначение)», как у пункта очереди
             # (lifecycle.order_ref → since_text, тот же ork.judged): список и очередь говорят одно
             "since": lifecycle.since_text(v.get("since"), v.get("sinceBy") or "", o, today)
             if v.get("wd") is not None else "",
             # срок шага (judged): сверх нормы, ждём УПД, срок неизвестен, не в счёте (гигиена,
             # приёмка вне «Реализации»); гигиена — виды и почему
             "over": v.get("over") is True, "wu": bool(v.get("waitUtd")), "tu": bool(v.get("termUnknown")),
             "nc": nc(o), "hyg": h.get("kinds") or [], "hw": h.get("why") or "",
             "waits": waits_of.get(st, "") if st else "",
             "c12": oid in c12, "p80": str(oid) in p80s,
             # почему срок не посчитать / не в счёте (ork.judged, гигиена) — Д-9
             "why": v.get("why") or "" if (v.get("termUnknown") or nc(o)) else "",
             "ncWhy": (h.get("why") or v.get("why") or "") if nc(o) else ""}
        # одна причина на заказ (Д-4): у WP-C — «Почему» списка, у WP-D — путь заказа
        r["cause"], r["cause2"] = order_cause(codes_of.get(str(oid), []), st, v.get("over") is True)
        if v.get("wd") is None and st is not None:
            since = _d(o.get("stepSince")) or _d(o.get("stageSince"))
            if since:
                r["cd"] = (today - since).days          # календарные дни на шаге без нормы
        rows.append(_compact(r))

    def keys(it) -> list[str]:
        return [str(o["id"]) for o in it]
    # live — все живые заказы ведомости; mapped — на шаге («Итого сейчас»); unk — стадия не из таблицы
    D: dict = {"live": keys(L), "mapped": keys(o for o in L if step[o["id"]] is not None),
               "unk": keys(o for o in U if step[o["id"]] is None)}
    no_order_q = q_by.get("noOrder", [])
    D["W1"] = keys(o for o in L if o["id"] in over_ids[1]) + [f"d:{q['deal']}" for q in no_order_q]
    D["W2"] = keys(o for o in L if any(o["id"] in over_ids[s] for s in ork.OVER_CODE))
    w3k, w3t, seen = [], [], set()
    for q in risk_q:
        k = str(q.get("order") or "")
        if k and k not in seen:
            seen.add(k)
            w3k.append(k)
            w3t.append(q["code"])
    D["W3"] = {"k": w3k, "t": w3t}                      # подпись — код пункта (late, risk, forecast)
    D["W5"] = {"k": [str(c["order"]) for c in cps],
               "t": [f"{c['kind']} · {_d(c['date']):%d.%m}" for c in cps]}
    S: dict = {}
    for s in ledger:
        no = s["no"]
        here = [o for o in L if step[o["id"]] == no]
        e: dict = {"now": keys(here)}
        if no in ork.NORM_WD:
            e["over"] = keys(o for o in here if o["id"] in over_ids.get(no, set()))
            e["unk"] = keys(o for o in here if ov[o["id"]]["termUnknown"])
            e["nc"] = keys(o for o in here if nc(o))
        if no in wait_ids:
            e["wait"] = keys(o for o in here if o["id"] in wait_ids[no])
        # «нет артефактов» — пары (заказ, артефакт), как в ведомости: строка — пара
        ak, at = [], []
        for a in ork.ARTIFACTS:
            if a["step"] != no:
                continue
            for o in U:
                if ork.art_required(o, a, reach_of.get(o["id"])) and not (o.get("art") or {}).get(a["key"]):
                    ak.append(str(o["id"]))
                    at.append("нет: " + a["name"])
        e["art"] = {"k": ak, "t": at}
        # Д-5а: те же пары — по заказу (ledger.artOrders): строка — заказ, подпись — каких файлов нет
        ao: dict[str, list] = {}
        for k, t in zip(ak, at):
            ao.setdefault(k, []).append(t.removeprefix("нет: "))
        e["artO"] = {"k": list(ao), "t": ["нет: " + ", ".join(v) for v in ao.values()]}
        if no == 12:
            e["closed"] = keys(C12)
        if no == 7:
            e["p80"] = p80
        if no == 13:
            e["close"] = [f"d:{q['deal']}" for q in q_by.get("dealClose", [])]
        S[str(no)] = e
    D["step"] = S
    D["adj"] = {r: [k for s in ledger if s["waits"] == r for k in S[str(s["no"])]["now"]] for r in ork.ADJACENT}
    D["p80"] = p80
    D["noOrder"] = [f"d:{d['deal']}" for d in no_order]
    D["noOrderOver"] = [f"d:{q['deal']}" for q in no_order_q]
    wait_all = set().union(*wait_ids.values()) if wait_ids else set()
    D["waitUtd"] = keys(o for o in L if o["id"] in wait_all)
    # «Сверх нормы по менеджерам» — пункты с w2 и waitUtd у хозяина пункта (тот же счёт)
    who_d: dict[str, dict] = {}
    for code, flag, mark in ([(c, "over", "w2") for c in ork.OVER_CODE.values()]
                             + [(c, "wait", "waitUtd") for c in ork.WAIT_CODE.values()]):
        for q in q_by.get(code, []):
            if q.get(mark):
                w = q.get("who") or "не назначен"
                who_d.setdefault(w, {"over": [], "wait": []})[flag].append(str(q.get("order") or ""))
    D["who"] = who_d
    H: dict = {"all": [str(x["order"]) for x in hyg_items],
               "excl": [str(x["order"]) for x in hyg_items if x["excl"]],
               "noMgr": [str(x["order"]) for x in hyg_items if "noMgr" in x["kinds"]]}
    for k in ("behind", "abandoned", "old", "unknown"):
        H["x" + k] = [str(x["order"]) for x in hyg_items if x["excl"] and k in x["kinds"]]
    D["hyg"] = H
    # «Моя приёмка»: «в срок за 20 р.д.: k из n» — отрезки S1 в счёте (_timeliness: не пропущен,
    # без «пачки», возврата и обратного порядка меток, р.д. посчитаны); скрытая — без списка
    if isinstance(recent, dict) and recent.get("state") in ("show", "prelim"):
        rk, rt = [], []
        for o in s1_recent_o:
            s = o["_segs"]["segs"]["S1"]
            if s.get("skip") or not _clean(s) or s.get("wd") is None:
                continue
            rk.append(str(o["id"]))
            rt.append(f"{'в норме' if s.get('inNorm') else 'сверх нормы'} · {s['wd']} р.д.")
        D["W1recent"] = {"k": rk, "t": rt}
    live_k = {r["k"] for r in rows}
    want = set((D.get("W1recent") or {}).get("k") or [])
    # законченные и не из ведомости заказы за числами — строкой без менеджера ОРК
    hist = [_hist_row(o, with_mgr=False) for o in s1_recent_o
            if str(o["id"]) in want and str(o["id"]) not in live_k]

    # сделки за числами уровня сделки: без заказа (W1, подпись ведомости) и «закрыть сделку»
    deals_out: dict[str, dict] = {}

    # сделка без заказа (не закрыта) и «к закрытию» («Оплата получена» — contracts: paid ⇒ closed)
    # — разные сделки: строка сделки одна на вид (test_ork_drill проверяет, что множества не пересекаются)
    def drow(did: str, kind: str) -> dict:
        d = deal_by.get(did) or {}
        if did not in deals_out:
            c = (d.get("stepCell") or {}) if kind == "noOrder" else {}
            deals_out[did] = {"k": f"d:{did}", "deal": str(did), "seq": d.get("seq") or 0,
                              "stage": d.get("stageName") or "", "kind": kind,
                              "step": c.get("step") if kind == "noOrder" else 13,
                              "wd": c.get("wd"), "norm": ork.NORM_WD[1] if kind == "noOrder" else None,
                              "own": c.get("who") or "", "ownR": c.get("role") or "",
                              "mgr": _nm(d.get("orkResp")) or PULSE_NONE, "kam": _nm(d.get("kam")),
                              "q": dcodes.get(str(did), [])}
        return deals_out[did]
    for d in no_order:
        drow(d["deal"], "noOrder")
    for q in no_order_q:
        r = drow(q["deal"], "noOrder")
        r.update(over=True, wd=q.get("days") if q.get("days") is not None else r["wd"],
                 own=q.get("who") or r["own"], ownR=q.get("role") or r["ownR"])
    for q in q_by.get("dealClose", []):
        r = drow(q["deal"], "dealClose")
        r.update(own=q.get("who") or r["own"], ownR=q.get("role") or r["ownR"], cd=q.get("days"))

    # «Пульс ОРК» (решение владельца 29.10.2026, п. 4): живые заказы по менеджеру ОРК; всем с
    # вкладкой, как «Сверх нормы по менеджерам» (решение 27.10.2026, А) — счёт, без среднего и
    # отклонения (M3 «Загрузка» — под правом ork_heads), без сортировки по числу
    live_n = len(L)
    G: dict[str, dict] = {}
    for r, o in zip(rows, L):
        d = deal_by.get(o.get("deal")) or {}
        g = G.setdefault(r["mgr"], {"who": r["mgr"], "kind": r["mk"], "gone": bool(r.get("gone")),
                                    "keys": [], "overKeys": [], "waitKeys": [], "deals": set(),
                                    "cust": set(), "byStep": Counter()})
        g["keys"].append(r["k"])
        if r.get("over"):
            g["overKeys"].append(r["k"])
        if r.get("wu"):
            g["waitKeys"].append(r["k"])
        g["deals"].add(r["deal"])
        if _nm(d.get("customer")):
            g["cust"].add(d["customer"])
        g["byStep"][str(r["step"]) if r["step"] is not None else "?"] += 1
    prow = [{"who": g["who"], "kind": g["kind"], "gone": g["gone"], "n": len(g["keys"]),
             "pct": round(len(g["keys"]) * 100 / live_n, 1) if live_n else None,
             "deals": len(g["deals"]), "customers": len(g["cust"]),
             "over": len(g["overKeys"]), "wait": len(g["waitKeys"]),
             "byStep": dict(sorted(g["byStep"].items()))}
            for g in sorted(G.values(), key=lambda g: (PULSE_ORDER[g["kind"]], g["who"].casefold()))]
    # заказы строки «Пульса» ключами не дублируются: это строки week.orders с mgr == who (сверх
    # нормы — и over, ждём УПД — и wu) — ровно та группировка, что выше (страница: _odResolve)
    all_deals = {r["deal"] for r in rows}
    pulse = {"rows": prow, "live": live_n, "deals": len(all_deals),
             "customers": len({(deal_by.get(x) or {}).get("customer") for x in all_deals} - {None, "", "—"}),
             "over": sum(p["over"] for p in prow), "wait": sum(p["wait"] for p in prow),
             "out": PULSE_OUT, "none": PULSE_NONE}
    for r in rows:
        r.pop("mk", None)                               # вид — у строки «Пульса» (kind)
    dl_rows = [_compact(x) for x in deals_out.values()]
    strs = _pack(rows, dl_rows, hist)
    return {"orders": rows, "deals": dl_rows, "hist": hist, "strs": strs, "drill": D, "pulse": pulse}


def _quarter_drill(quarter: dict, measure: list[dict], today: dt.date) -> None:
    """Заказы за Q1 (квартал и поставщик) и Q3 — теми же отборами, что _quarter_view: готовность
    по первому входу в «Заказ готов» в квартале при бюджетном конце производства, без «пачки»,
    обратного порядка меток и возврата с шага 8+, без «в наличии»; Q3 — заказы с 02.04.2026 с
    «Долей первичной оплаты». Ключи — у показанного; строки — quarter.orders."""
    drill: dict = {}
    used: dict[str, dict] = {}
    cur = _quarter(today)
    prev = (cur[0], cur[1] - 1) if cur[1] > 1 else (cur[0] - 1, 4)
    for i, ((y, q), Q) in enumerate(zip((cur, prev), quarter.get("quarters") or [])):
        ks, ts = [], []
        by_sup: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
        # Д-10 (R4): не в счёте Q1 — «в наличии» (stock), готовность «пачкой» или с возвратом (burst),
        # и в счёте с плановой датой, равной факту (pef): каждое число у показателя — список
        extra: dict[str, tuple[list, list]] = {"stock": ([], []), "burst": ([], []), "pef": ([], [])}
        for o in measure:
            r = o.get("_reach") or {}
            ready, pe = _d(r.get(8)), o.get("prodEnd")
            if not ready or not pe or _quarter(ready) != (y, q):
                continue
            sg = o.get("_segs") or {}
            s5 = sg.get("segs", {}).get("S5")
            back = any(a >= 8 > b for a, b in sg.get("returnPairs") or [])
            if (s5 and (s5.get("burstEnd") or s5.get("neg"))) or back:
                extra["burst"][0].append(o)
                extra["burst"][1].append("не считаем: " + ("возврат с шага 8 и дальше" if back
                                                           else "готовность прокликали разом"))
                continue
            if s5 and s5.get("cd") is not None and s5["cd"] < 1:
                extra["stock"][0].append(o)
                extra["stock"][1].append(f"из наличия, без производства · готов {ready:%d.%m}")
                continue
            if ready == pe:
                extra["pef"][0].append(o)
                extra["pef"][1].append(f"плановая дата равна факту · {pe:%d.%m}")
            t = (("в срок" if ready <= pe else f"позже · на {(ready - pe).days} дн.")
                 + f" · готов {ready:%d.%m} · бюджет {pe:%d.%m}")
            ks.append(str(o["id"]))
            ts.append(t)
            sk, st_ = by_sup[o.get("sup") or "—"]
            sk.append(str(o["id"]))
            st_.append(t)
            used[str(o["id"])] = o
        Q1 = Q.get("Q1") or {}
        _dk(Q1, drill, f"{i}.Q1", ks, ts, prefix="q",
            head=f"Готово к обещанному сроку · {Q.get('q')}: заказы в счёте")
        # счёты рядом с долей: только у показанной (у скрытой «пачками» — без списка, как без числа)
        if Q1.get("state") in ("show", "prelim"):
            want_n = {"stock": Q1.get("stock"), "burst": Q1.get("burstN"),
                      "pef": round((Q1.get("planEqFact") or 0) * (Q1.get("n") or 0))}
            heads = {"stock": "из наличия, без производства — не в счёте",
                     "burst": "готовность прокликали разом или вернули назад — не в счёте",
                     "pef": "плановую дату переписали под факт"}
            for nm, (os_, tg) in extra.items():
                if os_ and len(os_) == want_n[nm]:
                    for o in os_:
                        used[str(o["id"])] = o
                    drill[f"{i}.{nm}"] = {"k": [str(o["id"]) for o in os_], "t": tg,
                                          "h": f"{Q.get('q')}: {heads[nm]}"}
        for j, srow in enumerate(Q.get("suppliers") or []):
            sk, st_ = by_sup.get(srow.get("sup"), ([], []))
            _dk(srow, drill, f"{i}.s{j}", sk, st_, prefix="q",
                head=f"{srow.get('sup')} · {Q.get('q')}: готовность к обещанному сроку")
            if "dk" not in srow and srow.get("n") and len(sk) == srow["n"]:
                # доля «в срок» скрыта («пачки», «план = факт»), а число готовностей видно — список за
                # ним без подписей «в срок / позже»: скрытая доля из него не складывается
                drill[f"{i}.s{j}n"] = {"k": sk, "t": None,
                                       "h": f"{srow.get('sup')} · {Q.get('q')}: готовые заказы "
                                            "(доля «к сроку» скрыта)"}
                srow["nDk"] = f"q:{i}.s{j}n"
    era = [o for o in measure if _d(o.get("created")) and _d(o["created"]) >= ork.PREPAY_SINCE]
    q3 = [o for o in era if o.get("prepayShare") is not None]
    for o in q3:
        used[str(o["id"])] = o
    _dk(quarter.get("Q3") or {}, drill, "Q3", [str(o["id"]) for o in q3],
        [f"первичная оплата {o['prepayShare']:g} %" for o in q3], prefix="q",
        head="Сколько платим вперёд: заказы с «Долей первичной оплаты»")
    # Д-10 (R4): Q2 — отрезки за медианой и P80 по длительности (длинные сверху); P80 — только хвост
    year_from = today - dt.timedelta(days=ork.YEAR_DAYS)
    Q2 = quarter.get("Q2") or {}
    for code in ("S5", "S7", "S8", "cycle"):
        rows_: list[tuple[int, dict]] = []
        for o in measure:
            if code == "cycle":
                c, f = o.get("created"), o.get("delFact")
                if c and f and year_from <= f <= today and f >= c:
                    rows_.append(((f - c).days, o))
                continue
            sg = (o.get("_segs") or {}).get("segs", {}).get(code)
            if not (sg and _end(sg) and year_from <= _end(sg) <= today) or sg.get("skip") or not _clean(sg):
                continue
            v = sg.get("cd")
            if v is not None and v >= 0:
                rows_.append((v, o))
        rows_.sort(key=lambda x: -x[0])
        st = Q2.get(code) or {}
        nm = SEG_TXT[code]
        fmt = (lambda v: f"{round(v)} дн.")
        _dk(st, drill, f"Q2.{code}", [str(o["id"]) for _v, o in rows_], [fmt(v) for v, _o in rows_], prefix="q",
            head=f"Сроки за год · {nm}: заказы в расчёте, длинные сверху")
        if st.get("dk"):
            for _v, o in rows_:
                used[str(o["id"])] = o
            tail = [(v, o) for v, o in rows_ if st.get("p80") is not None and v > st["p80"]]
            if tail:
                drill[f"Q2.{code}.tail"] = {"k": [str(o["id"]) for _v, o in tail], "t": [fmt(v) for v, _o in tail],
                                            "h": f"{_cap(nm)}: дольше, чем у 8 из 10 ({round(st['p80'])} дн.)"}
    # строки — только заказов из показанных списков
    want = {k for v in drill.values() for k in (v["k"] if isinstance(v, dict) else v)}
    quarter["drill"] = drill
    # без менеджера ОРК: «Квартал» видят все с вкладкой (поимённо — только «Месяц», под правом)
    quarter["orders"] = [_hist_row(o, with_mgr=False) for k, o in used.items() if k in want]
    quarter["strs"] = _pack(quarter["orders"])


# шаг словами для заголовков и подписей списков (R4: S-код — только с номером шага, «S3 (шаг 5)»)
SEG_TXT = {"S1": "приёмка (шаг 1)", "S2": "договор с поставщиком (шаг 4)",
           "S3": "заказ и подтверждение поставщика (шаг 5)", "S5": "производство (шаг 7)",
           "S6": "подготовка к отгрузке (шаг 8)", "S7": "логистика до склада (шаг 9)",
           "S8": "доставка клиенту (шаг 10)", "cycle": "весь заказ: от создания до доставки"}
MON_WORD = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
            "октябрь", "ноябрь", "декабрь")
MON_IN = ("январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
          "октябре", "ноябре", "декабре")


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:] if t else t


def _mon_word(m: dt.date) -> str:
    return f"{MON_WORD[m.month - 1]} {m.year}"


def _mon_in(m: dt.date) -> str:
    return f"{MON_IN[m.month - 1]} {m.year}"


def _mm_word(mm: str) -> str:
    """«09.2026» → «сентябрь 2026»; иное — как есть."""
    m = re.fullmatch(r"(\d\d)\.(\d{4})", str(mm or ""))
    return _mon_word(dt.date(int(m[2]), int(m[1]), 1)) if m and 1 <= int(m[1]) <= 12 else str(mm or "")


def _excl_keys(st: dict, pairs: list, drill: dict, key: str, head: str, used: dict) -> None:
    """Д-10 (R4): заказы за «не считаем» ячейки M1 — «пачки», возвраты и обратный порядок меток
    (excluded) и пропущенный шаг (skipped). Только у показанной ячейки и только когда счёт ключей
    равен числу у ячейки: у скрытой («раскрыл бы человека») счётов нет — нет и списка."""
    if not isinstance(st, dict) or st.get("state") not in ("show", "prelim"):
        return
    ex = [(o, s) for o, s, _c in pairs if not s.get("skip") and not _clean(s)]
    sk = [(o, s) for o, s, _c in pairs if s.get("skip")]

    def why(s: dict) -> str:
        return ("стадии прокликали разом" if s.get("burst") else "возврат на шаг раньше" if s.get("ret")
                else "даты стадий в обратном порядке")
    for name, rows, cnt, tag in (("excl", ex, st.get("excluded"), why),
                                 ("skip", sk, st.get("skipped"), lambda s: "шаг пропущен: стадия перепрыгнула")):
        if rows and cnt == len(rows):
            for o, _s in rows:
                used[str(o["id"])] = o
            drill[f"{key}.{name}"] = {"k": [str(o["id"]) for o, _s in rows], "t": [tag(s) for _o, s in rows],
                                      "h": f"{head}: " + ("не считаем — даты недостоверны" if name == "excl"
                                                          else "шаг пропущен")}


def _month_drill(month: dict, people: list[dict], orders: list[dict], U: list[dict], today: dt.date, *,
                 deal_ok_all: dict) -> None:
    """Заказы за M1 (отрезки S1, S2, S3, S6 и их сумма), M2 (пары «заказ, артефакт»), доставкой в
    срок по месяцам и поимённо — теми же отборами, что _month (заказы ОРК-эпохи, отрезок
    закончился в месяце, без пропуска шага, «пачки», возврата и обратного порядка меток; S1 —
    только у сделок «Реализации»). Массив ORKMONTH — только праву ork_heads; скрытая ячейка
    ключей не получает."""
    drill: dict = {}
    used: dict[str, dict] = {}
    by_id = {str(o["id"]): o for o in orders}
    era = [o for o in orders if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    months = _months(today)

    def seg_keys(pairs: list) -> tuple[list, list]:
        ks, ts = [], []
        for o, s, code in pairs:
            if s.get("skip") or not _clean(s) or s.get("wd") is None:
                continue
            ks.append(str(o["id"]))
            # подпись строки: класс первым (по нему список группирует), шаг словами, раб. дн. против нормы
            ts.append(f"{'в норме' if s.get('inNorm') else 'дольше нормы'} · {SEG_TXT.get(code, code)} · "
                      f"{s['wd']} раб. дн. при норме {ork.SEG_NORM.get(code, '—')}")
            used[str(o["id"])] = o
        return ks, ts

    def art_keys(pairs: list) -> tuple[list, list]:
        for o, _a, _g in pairs:
            used[str(o["id"])] = o
        return ([str(o["id"]) for o, _a, _g in pairs],
                [("есть: " if g else "нет: ") + a["name"] for _o, a, g in pairs])

    pp: dict = defaultdict(lambda: {"segs": defaultdict(list), "art": []})   # (месяц, кто) → пары
    for mi, (m0, row) in enumerate(zip(months, month.get("rows") or [])):
        m1 = dt.date(m0.year + (m0.month == 12), m0.month % 12 + 1, 1)
        segs: dict[str, list] = defaultdict(list)
        arts: list = []
        for o in era:
            for code in ("S1", "S2", "S3", "S6"):
                s = (o.get("_segs") or {}).get("segs", {}).get(code)
                if code == "S1" and not deal_ok_all.get(o.get("deal"), True):
                    continue
                if s and _end(s) and m0 <= _end(s) < m1:
                    segs[code].append((o, s, code))
                    if o.get("respOrk") and code != "S1" and mi < 2:
                        pp[(mi, o.get("orkMgr") or "не назначен")]["segs"][code].append((o, s, code))
            for a in ork.ARTIFACTS:
                # день прохождения шага — та же дата, что у M2 в _month (ork.passed_at: на шагах
                # 2–4 и назначение менеджера ОРК, если оно раньше стадии), иначе строк в списке
                # меньше, чем в числе
                at = ork.passed_at(o, a["need"])
                if at and m0 <= at < m1 and ork.art_required(o, a, o.get("_reach")):
                    got = bool((o.get("art") or {}).get(a["key"]))
                    arts.append((o, a, got))
                    if o.get("respOrk") and mi < 2:
                        pp[(mi, o.get("orkMgr") or "не назначен")]["art"].append((o, a, got))
        M1, mm = row.get("M1") or {}, row.get("m") or ""
        mw = _mon_word(m0)
        for c in ("S1", "S2", "S3", "S6"):
            _dk(M1.get(c) or {}, drill, f"{mi}.{c}", *seg_keys(segs[c]), prefix="m",
                head=f"{SEG_TXT[c]} · {mw}: шаги в счёте")
            _excl_keys(M1.get(c) or {}, segs[c], drill, f"{mi}.{c}", f"{SEG_TXT[c]} · {mw}", used)
        allm = segs["S2"] + segs["S3"] + segs["S6"]
        _dk(M1.get("mgr") or {}, drill, f"{mi}.mgr", *seg_keys(allm), prefix="m",
            head=f"Шаги ОРК (шаги 4, 5, 8) · {mw}: шаги в счёте")
        _excl_keys(M1.get("mgr") or {}, allm, drill, f"{mi}.mgr", f"Шаги ОРК (шаги 4, 5, 8) · {mw}", used)
        _dk(row.get("M2") or {}, drill, f"{mi}.M2", *art_keys(arts), prefix="m",
            head=f"Полнота карточки · {mw}: пары «заказ, файл шага»")
        # Д-2 (R4): доставлено к сроку — все заказы, у кого срок клиента наступил в месяце (ключи —
        # lifecycle.otd, та же совокупность, что доля); не доставленные после срока — своим списком
        due_k = row.pop("_due", None) or {}
        ks, ts = [str(k) for k in due_k.get("k") or []], list(due_k.get("t") or [])
        D0 = row.get("otdDue") or {}
        if D0.get("n") == len(ks):
            for k in ks:
                if k in by_id:
                    used[k] = by_id[k]
            _dk(D0, drill, f"{mi}.due", ks, ts or None, prefix="m",
                head=f"Срок клиента наступил · {mw}: к сроку, позже и не доставлены")
        if D0.get("dk"):
            for part, pref, cnt, head in (("dueOpen", "не доставлен", D0.get("open"),
                                           f"Срок клиента прошёл в {_mon_in(m0)} — не доставлено"),
                                          ("dueLate", "позже срока", D0.get("late"),
                                           f"Срок клиента в {_mon_in(m0)} — доставлено позже срока")):
                op = [(k, t) for k, t in zip(ks, ts) if str(t).startswith(pref)]
                if op and len(op) == cnt:
                    drill[f"{mi}.{part}"] = {"k": [k for k, _ in op], "t": [t for _, t in op], "h": head}
    live_era = [o for o in U if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    for pi, p in enumerate(people):
        who = p.get("who")
        nm = str(who or "").replace(" (уволен)", "")
        live = [o for o in live_era if o.get("respOrk") and o.get("orkMgr") == who]
        if live and len(live) == p.get("live"):
            for o in live:
                used[str(o["id"])] = o
            drill[f"p{pi}.live"] = {"k": [str(o["id"]) for o in live], "t": None,
                                    "h": f"{nm} · заказы в работе, созданные с {ork.ORK_SINCE:%d.%m.%Y}"}
            p["liveDk"] = f"m:p{pi}.live"
        for mi, part in ((1, p), (0, p.get("cur") or {})):
            src = pp.get((mi, who)) or {"segs": {}, "art": []}
            pm, mm = part.get("M1") or {}, part.get("m") or ""
            # текущий (неполный) месяц человека страница показывает суммой S2+S3+S6 и полнотой —
            # отрезки по отдельности у него не видны, и ключей за ними нет (каждый dk — число на странице)
            for c in (("S2", "S3", "S6") if mi == 1 else ()):
                _dk(pm.get(c) or {}, drill, f"p{pi}.{mi}.{c}", *seg_keys(src["segs"].get(c) or []), prefix="m",
                    head=f"{nm} · {SEG_TXT[c]} · {_mm_word(mm)}")
            _dk(pm.get("mgr") or {}, drill, f"p{pi}.{mi}.mgr",
                *seg_keys([x for c in ("S2", "S3", "S6") for x in (src["segs"].get(c) or [])]), prefix="m",
                head=f"{nm} · шаги ОРК (шаги 4, 5, 8) · {_mm_word(mm)}")
            _dk(part.get("M2") or {}, drill, f"p{pi}.{mi}.M2", *art_keys(src["art"]), prefix="m",
                head=f"{nm} · полнота карточки · {_mm_word(mm)}")
    # Д-2, Д-10 (R4): столбец «Доставки к сроку по месяцам» → заказы, у кого срок наступил в месяце
    for r12 in month.pop("_otd", None) or []:
        kk = r12.get("keys") or {}
        ks = [str(k) for k in kk.get("k") or []]
        if ks and len(ks) == r12.get("due"):
            for k in ks:
                if k in by_id:
                    used[k] = by_id[k]
            mm = str(r12.get("m") or "")
            drill[f"y.{mm}"] = {"k": ks, "t": list(kk.get("t") or []) or None,
                                "h": f"Срок клиента наступил · {_mm_word(mm)}: к сроку, позже и не доставлены"}
    # Д-10 (R4): «ответственный заказа не из ОРК» у загрузки M3 — список этих заказов (счёт M3.notOrk)
    no = [o for o in live_era if not o.get("respOrk")]
    if no and len(no) == (month.get("M3") or {}).get("notOrk"):
        for o in no:
            used[str(o["id"])] = o
        drill["notOrk"] = {"k": [str(o["id"]) for o in no], "t": None,
                           "h": "Заказы в работе: ответственный заказа не из отдела ОРК"}
    want = {k for v in drill.values() for k in (v["k"] if isinstance(v, dict) else v)}
    month["drill"] = drill
    month["orders"] = [_hist_row(o) for k, o in used.items() if k in want]
    month["strs"] = _pack(month["orders"])
