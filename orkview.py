"""Раздел «ork» вкладки «Реализация»: дашборд руководителя ОРК по регламенту (решения
владельца 26.10.2026). Чистые функции поверх уже посчитанного — заказов, приведённых к
фактам (contracts.life_orders), строк сделок, очереди lifecycle и сегментов S1–S10 по всем
переходам (ork.segments). Портал не читает.

Подвкладки:
  week    — неделя руководителя ОРК: W1 приёмка, W2 сверх нормы (только шаги ОРК), W3 зона
            риска, W4 эскалации и паузы (нет данных), W5 контрольные точки недели; ведомость
            13 шагов и строка 0 «Подтверждение передачи в ОРК» (решение владельца 31.10.2026:
            до передачи в ОРК — не шаг ОРК: в W2, «сверх нормы по менеджерам» и Σ «Пульса» не
            идёт, свой столбец «дольше нормы» — по judged.preOver), сверх нормы по исполнителям,
            ждут смежников, гигиена карточек;
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
J_ASSIGN = "не в счёте M1 S1: назначение менеджера ОРК на новый заказ уже принятой сделки (приёмка — одна на сделку)"
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
           unknown: int = 0, unk_txt: str = "живых заказов") -> dict:
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
                   need=f"стадия не из таблицы шагов у {unknown} {unk_txt} — сверить ork.STAGE_STEP")
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
          ork_head: str = "", confirm: dict | None = None) -> tuple[dict, dict]:
    """orders — заказы вида (contracts.life_orders: stage, stepSince, t0, факты, _segs,
    _reach, art, orkMgr, respOrk, prepayShare, assignDate…); deals — строки сделок вида;
    queue — очередь lifecycle вида; measure — все заказы (мерило квартала: свойства
    поставщиков и процесса, а не вида). → (раздел «ork» без «Месяца», «Месяц» под правом:
    {month — показатели отдела, people — менеджеры ОРК поимённо, how — строки M1–M3 «Как
    считается»}).

    confirm — сводка чтения задач подтверждения передачи (contracts.head.confirm: read true |
    false | "empty", why): не прочитаны или прочитано 0 при живых заказах — строка 0 ведомости
    «Нет данных», а не ноль (правило 15); None — как не прочитаны."""
    measure = orders if measure is None else measure
    confirm = confirm or {"read": False, "why": "задачи подтверждения передачи не прочитаны"}
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
    # пункты в единицах «заказ × код» (lifecycle.per_order): пункт сделки, свёрнутый из заказов
    # (unpaid, noPayPlan, приёмка s1Over — решение владельца 30.10.2026), — по копии на заказ
    qcodes = Counter(q["code"] for q in lifecycle.per_order(queue))
    q_by: dict[str, list] = defaultdict(list)
    for q in lifecycle.per_order(queue):
        q_by[q["code"]].append(q)
    no_order = [d for d in deals if not d.get("closed") and not d.get("lost") and d.get("inCat0", True)
                and not d.get("norders")]

    # --- ведомость 13 шагов -----------------------------------------------------------------
    # «сверх нормы» шага — живые заказы вида на этом шаге с judged.over; пункт очереди того же
    # заказа (s2Over, s3Over, s6Over) ставит то же правило — помечаем его w2 (отбор плитки).
    # Шаг 1 — приёмка СДЕЛКИ (решение владельца 30.10.2026, В1 а): в W2 не идёт; пункт s1Over
    # заказа на приёмке сделки — w1 (сделка в W1), заказа уже принятой сделки — w1o (W1o,
    # «назначить менеджера ОРК на новый заказ»)
    # шаг 0 (решение владельца 31.10.2026): «дольше нормы» строки 0 — judged.preOver (передача в ОРК
    # не подтверждена), over у него False; STEP_CODES шага 0 не знают — флагов w1/w2 от него нет
    over_ids: dict[int, set] = {st: {o["id"] for o in L if step[o["id"]] == st
                                     and (ov[o["id"]]["over"] or ov[o["id"]].get("preOver"))}
                                for st in ork.NORM_WD}
    DS = deal_steps(orders, deals, U, step, queue)
    for st, codes in ork.STEP_CODES.items():       # шаг 1 — приёмка сделки (s1Over) и ordAssign
        for q in (q for c in codes for q in q_by.get(c, [])):
            if q.get("order") not in over_ids.get(st, set()):
                continue
            if st == 1:
                # s1Over после свёртки (lifecycle._fold_deal_codes) — пункт СДЕЛКИ на приёмке (W1),
                # ordAssign — заказ уже принятой сделки (W1o); копия per_order помечает и пункт сделки
                flag = "w1o" if q.get("code") in ASSIGN_CODES else "w1"
                q[flag] = True
                if "_of" in q:
                    q["_of"][flag] = True
            else:
                q["w2"] = True                     # пункт в счёте W2 — отбор плитки
                if "_of" in q:
                    q["_of"]["w2"] = True          # и сам пункт сделки (копия — по заказу)
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
    # приёмка сделки (шаг 1) — по сделке: в счёте, срок неизвестен, не в счёте, сверх нормы
    acc_q = {q.get("deal") for c in ACCEPT_DEAL_CODES for q in q_by.get(c, []) if not q.get("order")}
    s1_of = {did: [o for o in DS["uBy"].get(did, []) if step[o["id"]] == 1] for did in DS["acceptL"]}
    dl_by = {d["deal"]: d for d in deals}
    d1: dict[str, dict] = {}
    for did in DS["noOrder"]:
        d = dl_by.get(did) or {}
        known = bool(d.get("cat0At")) and period_mod.workdays_ru(_d(d["cat0At"]), today) is not None
        c = d.get("stepCell") or {}
        d1[did] = {"counted": known, "unk": not known, "nc": False, "over": did in acc_q,
                   "wd": c.get("wd") if known else None}
    for did, os1 in s1_of.items():
        cnt = any(ov[o["id"]]["counted"] for o in os1)
        tu = not cnt and any(ov[o["id"]]["termUnknown"] for o in os1)
        d1[did] = {"counted": cnt, "unk": tu, "nc": not cnt and not tu,
                   "over": any(o["id"] in over_ids[1] for o in os1),
                   "wd": max((ov[o["id"]]["wd"] for o in os1 if ov[o["id"]]["counted"]
                              and ov[o["id"]]["wd"] is not None), default=None)}
    deals_at = {1: DS["noOrder"] + DS["acceptL"], 2: DS["contract"][2], 3: DS["contract"][3],
                13: DS["close"]}
    ledger, adjacent = [], {r: {"orders": 0, "deals": 0} for r in ork.ADJACENT}
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
        doc = ork.NORM_DOC.get(no, ("", "none", ""))
        normed = no in ork.NORM_WD
        if no in DEAL_STEPS:
            # шаг сделки: «сделок на шаге»; живые заказы на этом шаге — рядом (ordersNow), не в now
            ids = deals_at[no]
            if waits:
                adjacent[waits]["deals"] += len(ids)
            row1 = [d1[x] for x in ids] if no == 1 else []
            ledger.append({"no": no, "name": s["name"], "role": s["role"], "roleName": ork.ROLES[s["role"]],
                           "also": [ork.ROLES[r] for r in s["also"]], "norm": s["norm"], "unit": "deal",
                           "now": len(ids), "ordersNow": len(here),
                           "normDoc": doc[0], "normState": doc[1], "normNeed": doc[2],
                           "over": sum(1 for x in row1 if x["over"]) if normed else None,
                           "ordersOver": len(over_ids[1]) if no == 1 else None,
                           "waitUtd": None,
                           "overUnknown": sum(1 for x in row1 if x["unk"]) if normed else None,
                           "notCounted": sum(1 for x in row1 if x["nc"]) if normed else None,
                           "artReq": req, "artMiss": miss, "artOrders": len(miss_o), "artUnit": "file",
                           "waits": waits or "",
                           "maxWd": max((x["wd"] for x in row1 if x["counted"] and x["wd"] is not None),
                                        default=None) if normed else None,
                           "longP80": None, "waitsName": ork.ROLES.get(waits or "", ""), "seg": s["seg"] or "",
                           # шаг 3 «Подписание договора с заказчиком» своей стадии сделки не имеет
                           "noStage": no == 3 and not any(v == 3 for v in ork.STAGE_STEP["deal"].values())})
            continue
        if waits:
            adjacent[waits]["orders"] += len(here)
        # строка 0: задачи подтверждения не прочитаны (или прочитано 0 при живых заказах) — «Нет
        # данных», а не «на шаге 0 никого» (правило 15); прочитаны не все (предел страниц, «partial»)
        # — счёт есть, но с оговоркой: у части заказов шаг 0 может не стоять
        rd = confirm.get("read")
        pre_nodata = ({} if no != ork.PRE_STEP or rd is True
                      else {"partial": True, "why": confirm.get("why") or ""} if rd == "partial"
                      else {"nodata": True, "why": confirm.get("why") or "задачи подтверждения передачи не прочитаны"})
        ledger.append({"no": no, "name": s["name"], "role": s["role"], "roleName": ork.ROLES[s["role"]],
                       "also": [ork.ROLES[r] for r in s["also"]], "norm": s["norm"], "unit": "order",
                       "now": len(here),
                       "normDoc": doc[0], "normState": doc[1], "normNeed": doc[2],
                       "over": over_by_step.get(no, 0) if normed else None,
                       # шаг 12: срок ждёт клиента — «ждём УПД от клиента N», а не «сверх нормы»
                       "waitUtd": len(wait_ids[no]) if no in wait_ids else None,
                       "overUnknown": sum(1 for o in here if ov[o["id"]]["termUnknown"]) if normed else None,
                       # на шаге, но не в счёте «сверх нормы»: гигиена, приёмка вне «Реализации»
                       "notCounted": sum(1 for o in here if not_counted(o)) if normed else None,
                       "artReq": req, "artMiss": miss, "artOrders": len(miss_o), "artUnit": "file",
                       "waits": waits or "",
                       # Д-6: дольше всех на шаге с нормой — р.д. самого долгого заказа в счёте
                       "maxWd": max((ov[o["id"]]["wd"] for o in here if ov[o["id"]]["counted"]
                                     and ov[o["id"]]["wd"] is not None), default=None) if normed else None,
                       # шаг 7 регламент не нормирует: «дольше P80 S5» — сигнал, не «сверх нормы»
                       "longP80": (long_p80["k"] if long_p80["state"] in ("show", "prelim") else None)
                       if no == 7 else None,
                       "waitsName": ork.ROLES.get(waits or "", ""), "seg": s["seg"] or "", **pre_nodata})

    # --- W1 приёмка СДЕЛКИ (В1 а, решение владельца 30.10.2026) и W1o — назначение менеджера ОРК
    # на новый заказ уже принятой сделки. W1 — в сделках: n — сделки на шаге 1 в счёте (без
    # заказа; с заказами только на шаге 1, у которых хоть один в счёте или срок неизвестен) и
    # сделки, где живые заказы только со стадией не из таблицы («срок неизвестен», снижают
    # покрытие); value — из них дольше 1 р.д. (хоть один заказ сверх нормы или пункт «без
    # заказа»). W1o — в заказах: заказы на шаге 1 у принятых сделок. Сумма W1 и W1o — не число
    # (разные единицы): прежний W1 (заказы + сделки) ≥ W1 + W1o
    w1_ids = [x for x in deals_at[1] if d1[x]["counted"] or d1[x]["unk"]]
    w1_known = sum(1 for x in w1_ids if d1[x]["counted"])
    w1_n = len(w1_ids) + len(DS["unk"])
    w1_over = [x for x in deals_at[1] if d1[x]["over"]]
    o1 = [o for o in U if step[o["id"]] == 1 and o.get("deal") in DS["accepted"]]
    o1_unk = [o for o in U if step[o["id"]] is None and o.get("deal") in DS["accepted"]]
    o1_pop = [o for o in o1 if ov[o["id"]]["counted"] or ov[o["id"]]["termUnknown"]]
    w1o_n = len(o1_pop) + len(o1_unk)
    w1o_over = [o for o in o1 if o["id"] in over_ids[1]]
    # «в срок за 20 р.д.»: приёмка сделок вида (один отрезок S1 на сделку — у первого заказа,
    # first_accept), закончившаяся за последние 20 рабочих дней; приёмка сделки вне воронки
    # «Реализация» не в счёте (Рег. 3.3) — как у ork.judged и «Месяца»
    back = period_mod.add_workdays_ru(today, -ork.WEEK_BACK_WD)
    in_cat = {d["deal"]: d.get("inCat0", True) for d in deals}
    first = first_accept(orders)
    s1_recent_o = [o for o in orders
                   if str(o["id"]) in first and (o.get("_segs") or {}).get("segs", {}).get("S1")
                   and in_cat.get(o.get("deal"), True)
                   and back and _end(o["_segs"]["segs"]["S1"]) and _end(o["_segs"]["segs"]["S1"]) > back]
    s1_recent = [o["_segs"]["segs"]["S1"] for o in s1_recent_o]      # первый заказ сделки — в список
    # «закончено назначением» — среди тех же отрезков, что в n своевременности (_timeliness: без
    # пропуска, «пачки», возврата, обратного порядка меток, р.д. известны): k ≤ n по построению
    by_assign = sum(1 for s in s1_recent if _clean(s) and s.get("wd") is not None
                    and s.get("endBy") == "назначение")
    # дата записи у «Заказ создан» не в счёте: раньше начала приёмки / ответственный не из ОРК
    # заказ на шаге 0 (задача подтверждения открыта) приёмку не начал — не в этих счётах
    st1 = [o for o in U if ork.stage_step(o.get("stage")) == 1 and step[o["id"]] != ork.PRE_STEP]
    agree_s1, agree_n = _s1_agree(orders)
    recent = (_timeliness(s1_recent, "S1", agree=agree_s1) if back else
              _ms(None, 0, None, src="S1", need=f"нет производственного календаря на {today.year} год "
                                                "(data/workcal_ru.json) — 20 р.д. назад не отсчитать"))
    recent["unit"] = "deal"
    n_acc = sum(1 for x in DS["acceptL"] if d1[x]["over"])
    n_no = sum(1 for x in DS["noOrder"] if d1[x]["over"])
    W1 = _count(len(w1_over), w1_n, w1_known / w1_n if w1_n else None, src="приёмка сделки: s1Over + noOrder",
                unknown=len(DS["unk"]), unk_txt="сделок, где у живых заказов стадия не из таблицы",
                extra={"unit": "deal", "accept": n_acc, "noOrder": n_no,
                       "label": ork.NO_PAUSE, "recent": recent,
                       "agree": agree_s1, "agreeN": agree_n,
                       # приёмка кончилась назначением менеджера ОРК раньше выхода из «Заказ создан»
                       # (за 20 р.д., из n своевременности — сделки); у живых «Заказ создан» дата
                       # записи не в счёте — раньше начала приёмки (assignStale) или ответственный
                       # не из ОРК (assignNoMgr): приёмка не сделана, счётом ЗАКАЗОВ
                       "byAssign": by_assign,
                       "assignStale": sum(1 for o in st1 if ork.assign_stale(o)),
                       "assignNoMgr": sum(1 for o in st1 if ork.assign_no_mgr(o)),
                       "units": {"value": "deal", "n": "deal", "accept": "deal", "noOrder": "deal",
                                 "byAssign": "deal", "excluded": "deal", "assignStale": "order",
                                 "assignNoMgr": "order"},
                       "excluded": sum(1 for x in deals_at[1] if d1[x]["nc"])})
    W1o = _count(len(w1o_over), w1o_n, (sum(1 for o in o1_pop if ov[o["id"]]["counted"]) / w1o_n)
                 if w1o_n else None, src="s1Over (ordAssign) у принятой сделки", unknown=len(o1_unk),
                 extra={"unit": "order", "label": ork.NO_PAUSE,
                        "what": "назначить менеджера ОРК на новый заказ уже принятой сделки",
                        "excluded": sum(1 for o in o1 if not_counted(o))})

    # --- W2 сверх нормы: только шаги ОРК по заказу (4, 5, 8) ----------------------------------
    # n — заказы этих шагов в счёте (judged.counted) и со сроком, который не посчитать
    # (termUnknown: нет даты входа, года нет в календаре), плюс стадии не из таблицы; гигиена —
    # рядом (excluded), не в n. Шаг 1 — приёмка сделки (В1 а): только в W1 (сделки) и W1o
    # (заказы принятых сделок), не в W2. Шаг 12 (УПД) — не в W2 и не в n: «ждём УПД от клиента»
    # (решение владельца 27.10.2026, Б) — рядом, waitUtd
    W2_STEPS = tuple(no for no in ork.OVER_CODE if no not in DEAL_STEPS)
    w2_pop = [o for o in L if step[o["id"]] in W2_STEPS
              and (ov[o["id"]]["counted"] or ov[o["id"]]["termUnknown"])]
    w2_known = sum(1 for o in w2_pop if ov[o["id"]]["counted"])
    w2_n = len(w2_pop) + unk_n
    by_step = {no: over_by_step.get(no, 0) for no in W2_STEPS}
    W2 = _count(sum(by_step.values()), w2_n, w2_known / w2_n if w2_n else None,
                src="s2Over, s3Over, s6Over", unknown=unk_n,
                extra={"unit": "order", "byStep": by_step, "label": "по стадии, " + ork.NO_PAUSE, "waitUtd": wait_n,
                       "excluded": sum(1 for o in L if step[o["id"]] in W2_STEPS and not_counted(o))})

    # --- W3 зона риска: срок клиента (результат поставщика и логистики) ---------------------
    undeliv = [o for o in U if not (o.get("delFact") or o.get("utdDate"))]
    with_dl = sum(1 for o in undeliv if o.get("dl"))
    risk_q = [q for q in queue if q["code"] in ("late", "risk", "forecast")]
    W3 = _count(len({q.get("order") for q in risk_q}), len(undeliv), with_dl / len(undeliv) if undeliv else None,
                src="late, risk, forecast",
                extra={"unit": "order", "by": {c: qcodes.get(c, 0) for c in ("late", "risk", "forecast")},
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
    # единицы (спецификация §1.8): value — точки (звонки поставщику), n и покрытие — заказы;
    # «3 звонка по 2 заказам» — orders: разные заказы с точкой недели
    w5_units = {"unit": "point", "nUnit": "order", "orders": len({c["order"] for c in cps}),
                "units": {"value": "point", "n": "order", "orders": "order", "prod": "order", "noDates": "order"}}
    W5 = _count(len(cps), w5_n, with_dates / w5_n if w5_n else None, src="ork.checkpoints", unknown=unk_n,
                extra={"noStatus": "пройдена без запроса статуса — нет данных: поле «Запрос статуса поставщику»",
                       "prod": len(prod), "noDates": len(prod) - with_dates, **w5_units})
    if prod and not with_dates and W5["state"] != "nodata":
        # в производстве есть, плановых дат нет ни у кого — это «нет данных», а не «покрытие 0 %»
        W5 = _ms(None, 0, None, src="ork.checkpoints", need="нет плановых дат производства у заказов шагов "
                 "6–7 (Production start/end date, budget) — внести даты размещения (ОСС)",
                 extra={"noStatus": W5.get("noStatus"), "unknown": unk_n, "prod": len(prod),
                        "noDates": len(prod) - with_dates, **w5_units})

    # --- сверх нормы по исполнителям (W2 поимённо: срок менеджера — его работа) ---------------
    # n — пункты сверх нормы (w2, тот же счёт, что у W2 и ведомости: заказы шагов 4, 5, 8);
    # waitUtd — свой столбец «ждём УПД от клиента» (пункты noUtd с waitUtd), в n не идёт (решение
    # 27.10.2026, Б); accept — СДЕЛКИ, чья приёмка у человека дольше 1 р.д. (пункты s1Over заказов
    # сделки на приёмке и «без заказа»: разные сделки); assign — заказы принятых сделок без
    # менеджера ОРК дольше 1 р.д. (W1o). Единицы разные — столбцы не складываются
    mgr: dict[str, dict] = {}
    acc_who: dict[str, set] = defaultdict(set)
    asg_who: dict[str, set] = defaultdict(set)

    def mrow(q: dict) -> dict:
        w = q_who(q)
        return mgr.setdefault(w, {"who": w, "gone": bool(q.get("whoGone")) and not q.get("whoOut"),
                                  "role": q.get("role") or "",
                                  "roleName": q.get("roleName") or "", "n": 0, "waitUtd": 0,
                                  "accept": 0, "assign": 0, "byStep": Counter(),
                                  "units": {"n": "order", "waitUtd": "order", "accept": "deal",
                                            "assign": "order"}})
    for code, flag in [(c, "w2") for c in ork.OVER_CODE.values()] + [(c, "waitUtd") for c in ork.WAIT_CODE.values()]:
        for q in q_by.get(code, []):
            if not q.get(flag):
                continue
            m = mrow(q)
            if flag == "w2":
                m["n"] += 1
                m["byStep"][q.get("step")] += 1
            else:
                m["waitUtd"] += 1
    for q in queue:
        c = q.get("code")
        if (c == ork.OVER_CODE[1] and q.get("w1")) or (c in ACCEPT_DEAL_CODES and not q.get("order")
                                                       and q.get("deal") in d1 and d1[q["deal"]]["over"]):
            mrow(q)
            acc_who[q_who(q)].add(q.get("deal"))
        elif (c == ork.OVER_CODE[1] and q.get("w1o")) or c in ASSIGN_CODES:
            mrow(q)
            asg_who[q_who(q)].add(q.get("order"))
    for w, ids in acc_who.items():
        mgr[w]["accept"] = len(ids)
    for w, ids in asg_who.items():
        mgr[w]["assign"] = len(ids)
    # по алфавиту, а не по числу: рейтинга людей нет (решение п. 3 — «без сортировки по баллу»);
    # «не назначен» — последней строкой
    over_mgr = sorted(({**m, "byStep": {str(k): v for k, v in m["byStep"].items()}}
                       for w, m in mgr.items()), key=lambda x: (x["who"] == "не назначен", x["who"]))

    # --- гигиена карточек -------------------------------------------------------------------
    hyg_items, hc = [], Counter()
    for o in L:
        h = hy[o["id"]]
        # confirmOpen — задача подтверждения передачи открыта, а заказ уже дальше шага 0 (решение
        # владельца 31.10.2026, а): замечание, из счёта не исключает
        kinds = [k for k in HYG_KINDS if h.get(k)]
        # менеджера ОРК нет: исполнитель — «не назначен» или ответственный заказа не из ОРК (out)
        _own = o.get("own") or {}
        if _own.get("role") == "orkMgr" and (_own.get("who") == "не назначен" or _own.get("out")):
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
    # «Клиент не оплатил в срок» — главное число в СДЕЛКАХ (долг по договору), заказы — в скобках,
    # деньги — доля продажи этих заказов (оценка по доле закупки), не сумма сделок (§1.5, E20)
    unpaid = _unpaid(q_by.get("unpaid", []), deal_by, today)
    late_who = Counter(q_who(q) for q in risk_q
                       if heavy.get(q.get("order")) == "late" and q["code"] == "late")
    late_tile = {"hist": late_hist(orders, deal_by, today), "fridays": [d.isoformat() for d in _fridays(today)],
                 "label": "переносы срока не хранятся — по нынешнему сроку клиента", "unit": "order"}
    for r in ledger:
        if r["no"] == 13:
            r["unpaidDeals"] = unpaid["deals"]          # «клиент не оплатил» — сделки, своим столбцом
    week = {"mon": mon.isoformat(), "sun": sun.isoformat(),
            "tiles": {"W1": W1, "W1o": W1o, "W2": W2, "W3": W3, "W4": W4, "W5": W5, "unpaid": unpaid,
                      "late": late_tile},
            "lateByWho": dict(late_who),
            "ledger": ledger, "unknown": unk_n, "unknownStages": dict(unknown.most_common(20)),
            # шаги сделки (1–3, 13) — в сделках, шаги заказа (4–12) — в заказах; живые заказы на
            # шагах сделки — ordersNow строки (Σ now заказных строк + Σ ordersNow + unknown = live)
            "ledgerUnits": {"deal": list(DEAL_STEPS), "order": [s["no"] for s in ork.STEPS
                                                               if s["no"] not in DEAL_STEPS]},
            # живые заказы ведомости: открытых сделок и шаг 12 закрытых (closed12 из них)
            "live": len(L), "closed12": len(C12), "dealsNoOrder": len(no_order),
            # из них дольше 1 р.д. — пункты noOrder (сделки, они в W1)
            "dealsNoOrderOver": n_no,
            # «Оплата получена», сделка не закрыта — пункты dealClose (шаг 13, ОСС): сделки, не заказы
            "dealClose": qcodes.get("dealClose", 0),
            # сделки к закрытию (шаг 13): dealClose + dealOpen, разные сделки
            "dealsToClose": len(DS["close"]),
            "orkHead": ork_head or "",
            # кого ждём: заказы на шагах заказа и сделки на шагах сделки — двумя числами (E23)
            "adjacent": adjacent,
            "longP80": long_p80,
            "overByWho": over_mgr, "risk": risk_list, "checkpoints": cps,
            # шаг 12 больше 10 р.д. без УПД — «ждём УПД от клиента»: не «сверх нормы» менеджера
            # (решение владельца 27.10.2026, Б); пункт noUtd — у менеджера ОРК, КАМ в копии
            "waitUtd": {"n": wait_n, "unknown": wait_unknown, "label": ork.WAIT_UTD, "src": "noUtd",
                        "norm": ork.NORM_WD[12], "unit": "order"},
            # n — заказы, remarks — замечания (пары «заказ × вид»): «N заказов, K замечаний» (E26)
            "hygiene": {"n": len(hyg_items), "unit": "order", "remarks": sum(len(x["kinds"]) for x in hyg_items),
                        "by": dict(hc), "excluded": hyg_excl, "byExcl": dict(hc_excl),
                        "noMgrOnly": sum(1 for x in hyg_items if x["kinds"] == ["noMgr"]), "items": hyg_items},
            "notChecked": "не проверяется: паузы и эскалации (полей нет), запросы статуса поставщику, "
                          "содержимое файлов (есть ли подпись), проверка контрагента (СП-1066 не читается)"}
    # заказы за числами «Недели» и «Пульс ОРК» (решение владельца 29.10.2026) — те же множества
    # вид заказа — тем же правилом, что «Пульс ОРК» (pulse_who): «без менеджера ОРК» сделки
    # делится на заказы без ответственного и с ответственным вне ОРК
    # заказ на шаге 0 (до передачи в ОРК) — вид «pre»: менеджера ОРК у него ещё не должно быть
    own = no_owner(deals, today, {str(o["id"]): ("pre" if ork.step_now(o) == ork.PRE_STEP
                                                 else pulse_who(o, deal_by.get(o.get("deal")))[1])
                                  for o in orders if o.get("live")})
    week.update(_week_drill(L=L, U=U, C12=C12, deals=deals, queue=queue, step=step, ov=ov,
                            over_ids=over_ids, wait_ids=wait_ids, long_p80=long_p80, cps=cps,
                            hyg_items=hyg_items, no_order=no_order, risk_q=risk_q, reach_of=reach_of,
                            measure=measure, today=today, ledger=ledger, s1_recent_o=s1_recent_o,
                            recent=recent, DS=DS, d1=d1, deals_at=deals_at, o1=o1, w1o_over=w1o_over,
                            unpaid=unpaid, own_ids=[r["deal"] for r in own["rows"]]))
    # Д-9 и R4: ключи за счётами подписей «Недели» (каждый — непустой список при счёте > 0);
    # у каждого списка — единица (unit): сделки — ключами «d:<id>», заказы — id заказа
    D = week["drill"]
    D["W3p"] = {c: _u("order", v["k"], v["t"]) for c, v in w3_parts(risk_q, heavy).items()}
    # «Сделки без хозяина» (решение 30.10.2026, п. 3): число = строки = ключи «d:» — одно множество
    week["noOwner"] = own
    D["noOwner"] = _u("deal", [r["k"] for r in own["rows"]], [owner_gap_text(r["miss"]) for r in own["rows"]],
                      "Без хозяина")
    # «Клиент не оплатил»: unpaidDeals — сделки (главное число, tiles.unpaid.deals), unpaid — заказы
    # этих сделок (tiles.unpaid.orders; ключ прежний — страница открывает его у числа заказов)
    D["unpaidDeals"] = _u("deal", unpaid.pop("dkeys"), unpaid.pop("dtags"))
    D["unpaid"] = _u("order", unpaid.pop("keys"), unpaid.pop("tags"))
    # первый заказ сделки (одна приёмка на сделку): строка списка — заказ, число — сделки
    D["W1byAssign"] = _u("order", [str(o["id"]) for o in s1_recent_o
                                   if _clean(o["_segs"]["segs"]["S1"]) and o["_segs"]["segs"]["S1"].get("wd") is not None
                                   and o["_segs"]["segs"]["S1"].get("endBy") == "назначение"],
                         head="первый заказ сделки: приёмка закончена назначением менеджера ОРК")
    D["W1assignStale"] = _u("order", [str(o["id"]) for o in st1 if ork.assign_stale(o)])
    D["W1assignNoMgr"] = _u("order", [str(o["id"]) for o in st1 if ork.assign_no_mgr(o)])
    D["noCal"] = _u("order", [str(o["id"]) for o in L if step[o["id"]] in ork.OVER_CODE
                              and ov[o["id"]]["why"] == "год без производственного календаря"])
    D["W5prod"] = _u("order", [str(o["id"]) for o in prod])
    D["W5noDates"] = _u("order", [str(o["id"]) for o in prod if not (o.get("prodStart") and o.get("prodEnd"))])
    # Д-7: сорванный срок по исполнителю пункта — ключи рядом с «дольше нормы» и «ждём УПД»
    for q in risk_q:
        if q["code"] == "late" and heavy.get(q.get("order")) == "late":
            D["who"].setdefault(q_who(q), {"over": [], "wait": []}) \
                .setdefault("late", []).append(str(q.get("order") or ""))
    for m in over_mgr:                       # строка человека только с приёмкой — пустые списки заказов
        D["who"].setdefault(m["who"], {"over": [], "wait": []})
    D["whoAccept"] = {"unit": "deal", **{w: [f"d:{x}" for x in ids] for w, ids in acc_who.items()}}
    D["whoAssign"] = {"unit": "order", **{w: [str(x) for x in ids] for w, ids in asg_who.items()}}

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
             "oldNoUtd": sum(1 for o in L if hy[o["id"]].get("old")),
             # шаг 0 (решение владельца 31.10.2026): задачи подтверждения передачи прочитаны (true |
             # false | "empty" | "partial") и живых заказов на шаге 0
             "confirmRead": confirm.get("read"),
             "pre": sum(1 for o in L if step[o["id"]] == ork.PRE_STEP)}
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


def _jrow(o: dict, code: str, s: dict, month: str, *, counted: bool, head: str, note: str = "") -> dict:
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
            "counted": counted, "note": "" if counted else (note or J_OUT),
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
    # приёмка — СДЕЛКИ (В1 а, решение владельца 30.10.2026): отрезок S1 первого заказа сделки;
    # S1 прочих заказов — назначение менеджера ОРК на новый заказ, в M1 S1 не идёт (журнал — с
    # отметкой J_ASSIGN)
    first = first_accept(orders)
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
        art_o: dict[str, list] = defaultdict(list)       # заказ → есть ли файл (по каждому требуемому)
        for o in era:
            for code in JOURNAL_SEGS:
                s = (o.get("_segs") or {}).get("segs", {}).get(code)
                if not (s and _end(s) and m0 <= _end(s) < m1):
                    continue
                # приёмка — только у сделок «Реализации» и только первая у сделки: иначе в M1 не
                # идёт, в журнале — с отметкой
                is_acc = code != "S1" or str(o["id"]) in first
                counted = code != "S1" or (deal_ok_all.get(o.get("deal"), True) and is_acc)
                jm[code].append(_jrow(o, code, s, m0.strftime("%m.%Y"), counted=counted, head=ork_head,
                                      note="" if is_acc else J_ASSIGN))
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
                    art_o[str(o["id"])].append(got)
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
        # S1 — приёмка СДЕЛКИ (решение 30.10.2026, п. 1; first_accept): отрезок — один на сделку,
        # счёт k из n — сделки (страница подписывает «Приёмка сделки», а не «по заказам»)
        m1s["S1"]["unit"] = "deal"
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
        # E45: M2 — пары «заказ × обязательный файл» (единица — файл); рядом — заказы: у скольких
        # разных заказов файлы требовались (ordersN) и у скольких все на месте (ordersFull)
        m2 = _people_gate(_ms(_pct(have, req) if req else None, req, None, src="ork.ARTIFACTS",
                              extra={"have": have, "label": "файл есть, содержимое не проверено", "target": 95,
                                     "unit": "file", "ordersN": len(art_o),
                                     "ordersFull": sum(1 for v in art_o.values() if all(v))}),
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
                            "open": o_m.get("dueOpenLate") or 0, "target": 95, "warn": 80, "unit": "order",
                            "label": "из всех, у кого срок клиента наступил в месяце"})
        # E44: та же доля в СДЕЛКАХ — главное число «Месяца» («сделок к сроку k из n»); сделка — в
        # месяце срока своего последнего заказа (lifecycle.otd.months[].dueDeals)
        dd = o_m.get("dueDeals") or {}
        due_deals_st = _ms(dd.get("pct"), dd.get("n") or 0, None, src="life.otd.dueDeals",
                           extra={"k": dd.get("ontime") or 0, "late": dd.get("late") or 0,
                                  "open": dd.get("open") or 0, "orders": dd.get("orders") or 0,
                                  "target": 95, "warn": 80, "unit": "deal",
                                  "label": "сделки, у которых срок последнего заказа наступил в месяце: "
                                           "к сроку — все заказы сделки доставлены не позже своего срока"})
        # текущий месяц идёт — неполный: заголовок «Месяца» — последний ПОЛНЫЙ месяц
        # первый месяц эпохи ОРК (24.08 — середина месяца) неполный: в счёте только отрезки
        # заказов, созданных с 24.08 и успевших закончиться, — сдвиг к «в норме»
        rows.append({"m": m0.strftime("%m.%Y"), "M1": m1s, "M2": m2, "otd": otd_st, "otdDue": due_st,
                     "otdDueDeals": due_deals_st,
                     "_due": o_m.get("keys") or {"k": [], "t": []},
                     "_dueDeals": dd.get("keys") or {"k": [], "t": []}, "target": 95,
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
    d90 = (otd or {}).get("deals90") or {}
    month = {"rows": rows, "M3": M3, "since": ork.ORK_SINCE.isoformat(), "target": 95,
             # E44: «сделка в срок» за 90 дней (lifecycle.otd.deals90) — на «Месяце» рядом с заказами
             "deals90": {k: v for k, v in d90.items() if k != "keys"},
             "_deals90": d90.get("keys") or {"k": [], "t": []},
             # E53: совокупности столбцов — M1, M2 и S1 — заказы ОРК-эпохи; доставка — все заказы вида
             "pop": {"M1": f"заказы, созданные с {ork.ORK_SINCE:%d.%m.%Y}",
                     "M2": f"заказы, созданные с {ork.ORK_SINCE:%d.%m.%Y}",
                     "otd": "все заказы вида", "otdDueDeals": "все сделки вида"},
             "units": {"M1": "segment", "S1": "deal", "M2": "file", "otdDue": "order", "otdDueDeals": "deal",
                       "deals90": "deal", "M3": "order"},
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
    # E47: «Обещайте клиенту» — цикл СДЕЛКИ: от входа сделки в «Реализацию» (dealCat0) до последней
    # доставки клиенту среди её заказов; сделка — когда доставлены все её заказы, кроме проваленных,
    # и последняя доставка за год. Рядом (cycle) — цикл одного заказа: от создания до доставки
    for did, v in deal_cycles(measure, today, year_from).items():
        seg_vals["cycleDeal"].append({"cd": v["cd"]})
    q2["cycleDeal"] = _dist(seg_vals["cycleDeal"], src="cycleDeal")
    q2["cycleDeal"]["unit"] = "deal"
    q2["cycle"]["unit"] = "order"
    for k in ("S5", "S7", "S8"):
        q2[k]["unit"] = "segment"
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


def deal_cycles(measure: list[dict], today: dt.date, year_from: dt.date) -> dict:
    """Сделка → {cd, last, n}: календарные дни от входа сделки в «Реализацию» (dealCat0 заказа)
    до последней доставки (delFact) среди её заказов. Только сделки, у которых все заказы, кроме
    проваленных (не живые и не исполненные), доставлены, вход известен и последняя доставка — за
    год до сегодня (то же окно, что Q2)."""
    by: dict = defaultdict(list)
    for o in measure:
        if o.get("live") or o.get("success"):
            by[str(o.get("deal"))].append(o)
    out = {}
    for did, os_ in by.items():
        if not os_ or not all(o.get("delFact") for o in os_):
            continue
        c0 = min((_d(o.get("dealCat0")) for o in os_ if _d(o.get("dealCat0"))), default=None)
        last = max(o["delFact"] for o in os_)
        if c0 and year_from <= last <= today and last >= c0:
            out[did] = {"cd": (last - c0).days, "last": last, "n": len(os_)}
    return out


def _how(orders, U, deals, queue, week, month, quarter, today, step) -> dict:
    """«Как считается»: формулы, поля, вердикт ворот, n и покрытие; нарушения порядка;
    заполненность артефактов; поля «добавить» с адресатом."""
    t = week["tiles"]
    m0 = month["rows"][month.get("head", 0)] if month["rows"] else {}    # последний полный месяц
    q0 = quarter["quarters"][0] if quarter["quarters"] else {}

    def row(code, name, formula, fields, st):
        # E49: «В расчёте» с единицей — n показателя в своей единице (HOW_UNIT)
        return {"code": code, "name": name, "formula": formula, "fields": fields,
                "state": st.get("state"), "why": st.get("why") or "", "n": st.get("n"),
                "unit": HOW_UNIT.get(code, "order"),
                "cover": round(st["cover"], 3) if isinstance(st.get("cover"), float) else st.get("cover")}
    metrics = [
        row("W1", "Приёмка сделки (S1)", "СДЕЛКИ на шаге 1 (одна приёмка на сделку, решение владельца 30.10.2026): "
            "без заказа > 1 р.д. от входа в реализацию или с живыми заказами только в «Заказ создан», из которых хоть "
            "один без назначения менеджера ОРК с р.д.(t0, сегодня) > 1; заказ на шаге 1 у сделки, где другой заказ "
            "ушёл дальше или исполнен, — не приёмка, а назначение менеджера ОРК на новый заказ (W1o, в заказах); "
            "t0 = max(создание заказа, вход сделки в реализацию) или закрытие задачи подтверждения передачи, "
            "если оно позже (шаг 0: пока задача открыта, заказ на шаге 0 и в W1 не идёт); приёмка кончается "
            "раньше из двух: выход из «Заказ создан» по стадии или «Дата записи нового ответственного» (не раньше "
            "дня t0 и ответственный заказа из отдела ОРК, иначе не в счёте) — после назначения заказ на шаге 4, срок "
            "договора с поставщиком (2 р.д.) — от назначения; брошенные карточки и сделки вне «Реализации» — не в "
            "счёте; сделка, где у живых заказов стадия не из таблицы, — в n как «срок неизвестен»",
            "createdTime, crm.stagehistory, ufCrm20_1787924508120", t["W1"]),
        row("W2", "Сверх нормы, только ОРК", "шаги 4, 5, 8 (заказы; приёмка сделки — W1): р.д. от входа в шаг > "
            "нормы (шаг 4 — от назначения "
            "менеджера ОРК, если приёмку закончило оно, ork.clock); стадия позади фактов, "
            "брошенная карточка — гигиена, не в n; стадия не из таблицы — в n как «срок неизвестен»; «без пауз». "
            f"Шаг 12 (УПД) — не в W2 и не в n (решение владельца 27.10.2026): р.д.(доставка, сегодня) > "
            f"{ork.NORM_WD[12]} и УПД нет — «{ork.WAIT_UTD}» отдельным столбцом (подпись УПД — за заказчиком), "
            f"пункт noUtd у менеджера ОРК с эскалацией КАМу; доставка старше {ork.CLOSE_DAYS} дн. без УПД — гигиена",
            "crm.stagehistory (W2); «ждём УПД» рядом с W2 — Customer delivery date actual, UTD", t["W2"]),
        row("W3", "Зона риска", "срок клиента прошёл / ≤ 14 дн. без отгрузки / прогноз позже срока",
            "Date of deadline to customer, Supplier Shipment Date", t["W3"]),
        row("W4", "Эскалации и паузы", "—", "нет полей", t["W4"]),
        row("W5", "Контрольные точки недели", "точки (звонки поставщику) у заказов шагов 6–7: P50 = начало + "
            "½(конец − начало); P14 = отгрузка − 14 дн. при производстве > 30 дн.; n — заказы",
            "Production start/end date, budget; Supplier Shipment Date, planned", t["W5"]),
        row("M1", "Своевременность S2+S3+S6", "сегменты, закончившиеся в месяце, без «пачек» и возвратов: в норме / "
            f"все; за ячейкой меньше {ork.GATES['n_managers_min']} ответственных заказа — не показываем (раскрыло бы "
            "человека; поимённо — под правом руководителей). S2 начинается с назначения менеджера ОРК («Дата записи "
            "нового ответственного»), если оно раньше выхода из «Заказ создан»; стадия прыгнула сразу на шаг 5 и "
            "дальше — S2 пропущен и не в счёте, как и без назначения", "crm.stagehistory (все переходы), "
            "ufCrm20_1787924508120", (m0.get("M1") or {}).get("mgr") or {}),
        row("M2", "Полнота карточки", "Σ есть / Σ требуется по парам (заказ, артефакт) — n в файлах, рядом — "
            "у скольких заказов (ordersN) и у скольких все файлы (ordersFull); у заказов, прошедших шаг "
            "(шаги 2–4 — и назначением менеджера ОРК, если оно раньше стадии: ork.passed_at)",
            ", ".join(a["field"] for a in ork.ARTIFACTS), m0.get("M2") or {}),
        row("M3", "Загрузка", "живые заказы на менеджера ОРК / среднее по менеджерам; ±25 %",
            "assignedById, department.get", month.get("M3") or {}),
        row("Q1", "Первичный срок поставщика", "вход в «Заказ готов» ≤ Production end date, budget",
            "crm.stagehistory, ufCrm20_1724941935", q0.get("Q1") or {}),
        row("Q2", "Сроки S5 · S7 · S8 · цикл", "медиана и P80 = s[⌈0,8n⌉−1], календарные дни, за год; цикл сделки "
            "(cycleDeal) — от входа сделки в «Реализацию» до последней доставки её заказов, цикл заказа (cycle) — от "
            "создания до доставки", "crm.stagehistory, createdTime, Customer delivery date actual", quarter["Q2"]["S5"]),
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
                  "payNoContract": len({str(q.get("deal")) for q in queue if q["code"] == "payNoContract"}),
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
    # E46: «стадии двигали неправильно» — два итога в своих единицах: по заказам (возвраты, «пачки»,
    # обратный порядок меток, стадия позади фактов, стадия не из таблицы) и по сделкам (оплата без
    # договора с клиентом, стадия сделки позади заказов). cases — сумма случаев (заказ бывает в
    # двух видах), n — разные объекты своей единицы. Сумма двух итогов — не число
    hy_items = (week.get("hygiene") or {}).get("items") or []
    o_sets = {"returns": {str(o["id"]) for o in orders if (o.get("_segs") or {}).get("returns")},
              "bursts": {str(o["id"]) for o in orders if (o.get("_segs") or {}).get("burst")},
              "negative": {str(o["id"]) for o in orders
                           if any(x.get("neg") for x in ((o.get("_segs") or {}).get("segs") or {}).values())},
              "behindFacts": {str(x["order"]) for x in hy_items if "behind" in (x.get("kinds") or [])},
              "unknownStage": {str(o["id"]) for o in U if step[o["id"]] is None}}
    d_sets = {"payNoContract": {str(q.get("deal")) for q in queue if q["code"] == "payNoContract"},
              "dealBehindOrders": {str(d["deal"]) for d in deals if by_deal.get(d["deal"])
                                   and ork.deal_behind(d.get("dealStep"), by_deal[d["deal"]])}}
    viol = {"orders": {**{k: len(v) for k, v in o_sets.items()}, "cases": sum(len(v) for v in o_sets.values()),
                       "n": len(set().union(*o_sets.values())), "unit": "order"},
            "deals": {**{k: len(v) for k, v in d_sets.items()}, "cases": sum(len(v) for v in d_sets.values()),
                      "n": len(set().union(*d_sets.values())), "unit": "deal"}}
    kam_pairs = [(o.get("kamOrder"), o.get("kamDeal")) for o in orders if o.get("kamOrder") and o.get("kamDeal")]
    drill = _how_drill(orders, U, deals, queue, week, step, violations, arts,
                       len(kam_pairs) - sum(1 for a, b in kam_pairs if a == b))
    return {"metrics": metrics, "violations": violations, "viol": viol, "artifacts": arts, "drill": drill,
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

    def put(key: str, ks: list, ts: list | None, n, head: str, unit: str = "order") -> None:
        if ks and len(ks) == n:
            D[key] = {"unit": unit, "k": ks, "t": ts, "h": head}
    sg = [(o, o.get("_segs") or {}) for o in orders]
    put("returns", [str(o["id"]) for o, g in sg if g.get("returns")], None, violations.get("returns"),
        "Заказ вернули на стадию назад")
    put("bursts", [str(o["id"]) for o, g in sg if g.get("burst")], None, violations.get("bursts"),
        "Стадии прокликали разом: 3 и больше смены за 5 минут")
    put("negative", [str(o["id"]) for o, g in sg if any(x.get("neg") for x in (g.get("segs") or {}).values())],
        None, violations.get("negative"), "Даты стадий в обратном порядке")
    # оплата без договора с клиентом — нарушение СДЕЛКИ: одна строка на сделку (E46)
    pnc = list(dict.fromkeys(str(q.get("deal")) for q in queue if q.get("code") == "payNoContract"))
    put("payNoContract", ["d:" + d for d in pnc], None, violations.get("payNoContract"),
        "Оплатили поставщику без договора с клиентом", unit="deal")
    by_deal: dict = defaultdict(list)
    for o in U:
        if step[o["id"]] is not None:
            by_deal[o.get("deal")].append(step[o["id"]])
    beh = [d for d in deals if by_deal.get(d["deal"]) and ork.deal_behind(d.get("dealStep"), by_deal[d["deal"]])]
    put("dealBehindOrders", ["d:" + str(d["deal"]) for d in beh], None, violations.get("dealBehindOrders"),
        "Стадия сделки отстала от заказов", unit="deal")
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
        if no == ork.PRE_STEP:
            # шаг 0 меряет задача Битрикса «Подтверждение готовности передачи…» (решение владельца
            # 31.10.2026): своих стадий у него нет — заказ в «Заказ создан», пока задача открыта
            meas, stages, nxt = "task", [], ""
        else:
            # где меряем: стадии какой карточки ведут шаг (у шага 1 — «Заказ создан» заказа)
            meas = "order" if any(v == no for v in ork.STAGE_STEP["order"].values()) else "deal"
            table = ork.STAGE_STEP[meas]
            stages = [k for k, v in table.items() if v == no]
            nxt = next((k for k, v in table.items() if v > no), "")
        # E48: чей шаг по регламенту — 1–3 сделки (приёмка, договор с заказчиком), 4–12 заказа,
        # 13 — сделки и заказа (закрытие заказа и сделки); measure — где портал его меряет
        kind = STEP_KIND.get(no, "order")
        out.append({"no": no, "kind": kind, "kindName": KIND_NAME[kind], "measure": meas,
                    "measureName": KIND_NAME[meas], "stages": stages, "next": nxt,
                    "unit": "deal" if no in DEAL_STEPS else "order",
                    "esc": ork.ROLES.get(s["esc"] or "", "")})
    return out


# чей шаг по регламенту (E48): 1–3 — сделка, 4–12 — заказ, 13 — сделка и заказ; шаг 0 — заказ
# (задача подтверждения передачи привязана к заказу), меряет его задача Битрикса (task)
STEP_KIND = {0: "order", **{n: "deal" for n in (1, 2, 3)}, **{n: "order" for n in range(4, 13)}, 13: "both"}
KIND_NAME = {"deal": "сделка", "order": "заказ", "both": "сделка и заказ", "task": "задача Битрикса"}
# единица n показателя «Как считается» (E49): W1 — сделки, M1 — отрезки, M2 — файлы, M3 — менеджеры
HOW_UNIT = {"W1": "deal", "W2": "order", "W3": "order", "W4": "order", "W5": "order", "M1": "segment",
            "M2": "file", "M3": "manager", "Q1": "order", "Q2": "segment", "Q3": "order"}


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
# заказ на шаге 0 — ещё не передан в ОРК (решение владельца 31.10.2026): своя строка «Пульса»
# (делает исполнитель задачи подтверждения, в курсе КАМ); не в «не назначен» и не у менеджера
PULSE_PRE = "до передачи в ОРК"
PULSE_ORDER = {"mgr": 0, "out": 1, "none": 2, "pre": 3}
# виды замечаний гигиены карточки (ork.hygiene); noMgr — отдельно (хозяин шага)
HYG_KINDS = ("behind", "abandoned", "old", "unknown", "confirmOpen")


def order_no(o: dict) -> str:
    """Номер заказа, как его называет портал, — тот же, что у пункта очереди (ordLbl), таблиц
    недели и карточки: ork.order_label («900/1» в начале названия; дата в начале — не номер;
    номера нет — «id <id СП-172>»). Строка заказа вида уже несёт его (contracts: lbl)."""
    return o.get("lbl") or ork.order_label(o.get("title"), o.get("id"))[0] or f"id {o.get('id')}"


def _nm(v) -> str:
    return "" if v in (None, "", "—", PULSE_NONE) else str(v)


def q_who(q: dict) -> str:
    """Человек пункта очереди в счётах «по людям» («Сверх нормы по менеджерам», приёмки, сорванный
    срок): who; ответственный заказа не из ОРК (whoOut — заказ не передан в ОРК, решение владельца
    31.10.2026) — «не назначен», как и прежде: менеджером ОРК он не числится."""
    return PULSE_NONE if q.get("whoOut") else (q.get("who") or PULSE_NONE)


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
        head: str = "", unit: str = "order") -> None:
    """Показатель с заказами за ним: ключи — drill[key] ({unit, k, t, h}: единица строки списка,
    ключи, подписи строк, заголовок списка), у показателя — dk «<prefix>:<key>». Только у
    показанного (show, prelim): у «не показываем» и «Нет данных» списка нет. Единица (§1.8):
    order — строка на заказ; deal — на сделку (ключ «d:<id>»); segment — на отрезок шага (ключ —
    заказ отрезка); file — на пару «заказ × обязательный файл» (ключ — заказ)."""
    if not isinstance(st, dict) or st.get("state") not in ("show", "prelim"):
        return
    drill[key] = {"unit": unit, "k": keys, "t": tags, "h": head}
    # у каждого dk число на странице (validate_dashboard: список собран — число выведено)
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


def _deal_rows(ids, orders: list[dict]) -> list[dict]:
    """Строки сделок за числами в сделках «Месяца» и «Квартала» (ключ «d:<id>»): номер сделки и
    номера её заказов — без денег и людей (страница добирает остальное из таблицы сделок)."""
    by: dict = defaultdict(list)
    for o in orders:
        if str(o.get("deal")) in ids:
            by[str(o.get("deal"))].append(o)
    out = []
    for did in sorted(ids, key=lambda x: (len(x), x)):
        os_ = by.get(did) or []
        out.append(_compact({"k": f"d:{did}", "deal": did, "seq": next((o.get("seq") for o in os_ if o.get("seq")), 0),
                             "step": None, "ords": [order_no(o) for o in os_]}))
    return out


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
OUR_CODES = frozenset(c for cs in ork.STEP_CODES.values() for c in cs) | {"noOrder"}
# причина заказа (Д-4, порядок — решение R4, «Как считается»): первая подходящая — cause, вторая —
# cause2. our — шаг ОРК дольше нормы; sup — производство дольше плана или шаг ждёт поставщика;
# log / kam / oss — шаг ждёт этот отдел; nodata — нет плановых дат или срока, шаг неизвестен;
# ничего не подошло — мяч у ОРК на его шаге в норме (our)
CAUSES = ("our", "sup", "log", "kam", "oss", "nodata", "pre")


def order_cause(codes, step, over: bool = False) -> tuple[str, str]:
    """(cause, cause2) заказа «Недели»: одна причина на заказ — сумма причин по любому списку
    равна числу заказов в нём (инвариант Д-4). Порядок: our (шаг ОРК дольше нормы) → sup → log →
    kam → oss → nodata; ни одна не подошла — ork (на шаге ОРК, срок в норме). Шаг 0 — pre: заказ
    ещё не передан в ОРК (решение владельца 31.10.2026), причина у исполнителя задачи, не у ОРК."""
    if step == ork.PRE_STEP:
        return "pre", ""
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


# шаги регламента, которые идут по СДЕЛКЕ целиком (решение владельца 30.10.2026, В1 а; регламент:
# приёмка сделки, договор с заказчиком, закрытие) — ведомость считает их в сделках; остальные — в
# заказах. Шаг 13 «Контроль закрытия» — сделки к закрытию; живые заказы на шаге 13 — рядом счётом
DEAL_STEPS = (1, 2, 3, 13)
# пункты очереди «сделка без заказа дольше нормы» (приёмка сделки): noOrder и — после свёртки
# приёмки по сделке (PY-2) — dealAccept; «назначить менеджера ОРК на новый заказ» — ordAssign
ACCEPT_DEAL_CODES = ("noOrder", "dealAccept")
ASSIGN_CODES = ("ordAssign",)
CLOSE_CODES = ("dealClose", "dealOpen")
# единицы списков за числами (спецификация 30.10.2026, §1.8): ключ сделки — «d:<id>»
UNITS = ("order", "deal", "segment", "file", "point")


def _u(unit: str, keys, tags=None, head: str | None = None, **kw) -> dict:
    """Список за числом одной единицы: {unit, k, t?, h?} (страница: _odKeys читает k, t, h)."""
    out: dict = {"unit": unit, "k": [str(k) for k in keys]}
    if tags is not None:
        out["t"] = list(tags)
    if head is not None:
        out["h"] = head
    out.update(kw)
    return out


def deal_steps(orders: list[dict], deals: list[dict], U: list[dict], step: dict, queue: list[dict]) -> dict:
    """Сделки на шагах сделки (1–3, 13) — одна сделка на одном шаге (приоритет 13 → 1 → 2–3).

    Шаг 1 «Приёмка сделки» (В1 а): открытая сделка «Реализации» без заказа (no_order) или с
    живыми заказами, из которых ни один не ушёл дальше шага 1 и ни один не исполнен (accept);
    сделка, где живые заказы только со стадией не из таблицы, — unk (может быть на приёмке).
    Сделка с заказом дальше шага 1 или исполненным — принята (то же правило, что у очереди:
    lifecycle._deal_accept — по нему s1Over заказа принятой сделки становится ordAssign): её заказ
    на шаге 1 — назначение менеджера ОРК на новый заказ (W1o), а не приёмка сделки. Шаги 2–3 — открытые сделки на стадии
    договора с заказчиком (dealStep 2 или 3; у шага 3 своей стадии сделки в Битриксе нет). Шаг 13
    — сделки к закрытию: пункты dealClose («Оплата получена», сделка не закрыта) и dealOpen (все
    заказы исполнены, сделка открыта).

    Шаг 0 (решение владельца 31.10.2026): сделка, где все живые заказы на шаге 0 (задача
    подтверждения передачи открыта), — pre: не на приёмке (в W1 и его n не идёт) и не «срок
    неизвестен»; заказ на шаге 0 сделку принятой не делает."""
    u_by: dict = defaultdict(list)
    for o in U:
        u_by[o.get("deal")].append(o)
    done = {o.get("deal") for o in orders if o.get("success")}
    all_by: dict = defaultdict(list)                    # все заказы сделки — как у lifecycle.build
    for o in orders:
        all_by[o.get("deal")].append(o)
    close: dict = {}
    for q in queue:
        if q.get("code") in CLOSE_CODES and q.get("deal") is not None and not q.get("order"):
            close.setdefault(q["deal"], q["code"])
    no_order = [d["deal"] for d in deals if not d.get("closed") and not d.get("lost") and d.get("inCat0", True)
                and not d.get("norders") and d["deal"] not in close]
    accepted, accept, unk, pre, contract = set(), [], [], [], {2: [], 3: []}
    for d in deals:
        did = d["deal"]
        if d.get("closed") or d.get("lost") or did in close:
            continue
        os_ = u_by.get(did) or []
        # принята — по фактам шагов; у сделки с заказом на шаге 1 — тем же правилом, что очередь
        # (lifecycle._deal_accept: по нему срок шага 1 — s1Over приёмки или ordAssign нового
        # заказа), чтобы W1/W1o и пункты очереди не расходились. Без заказа на шаге 1 стадия не из
        # таблицы — «срок неизвестен» (unk), а не «принята» (правило 15)
        past = did in done or any(step.get(o["id"]) not in (ork.PRE_STEP, 1, None) for o in os_) or (
            any(step.get(o["id"]) == 1 for o in os_) and lifecycle._deal_accept(all_by.get(did) or []))
        if past:
            accepted.add(did)
        at1 = False
        if os_ and not past:
            if any(step.get(o["id"]) == 1 for o in os_):
                accept.append(did)
                at1 = True
            elif all(step.get(o["id"]) == ork.PRE_STEP for o in os_):
                pre.append(did)
            else:
                unk.append(did)
        if not at1 and did not in no_order and d.get("dealStep") in contract:
            contract[d["dealStep"]].append(did)
    at: dict = {did: 13 for did in close}
    at.update({did: 1 for did in no_order + accept})
    for no, ids in contract.items():
        at.update({did: no for did in ids})
    return {"noOrder": no_order, "accept": set(accept), "acceptL": accept, "unk": unk, "pre": pre,
            "contract": contract, "close": list(close), "closeKind": close, "accepted": accepted, "at": at,
            "uBy": u_by}


def first_accept(orders: list[dict]) -> set:
    """Заказы, чья приёмка (S1) — приёмка СДЕЛКИ (В1 а): у сделки один отрезок — тот, что кончился
    раньше всех её отрезков S1 (первое назначение менеджера ОРК по сделке; при равенстве — меньший
    id). Отрезки S1 прочих заказов — назначение менеджера ОРК на новый заказ уже принятой сделки:
    в приёмку («Месяц» S1, «в срок за 20 р.д.», журнал) не идут. Сделка без такого отрезка не
    получает приёмки, если у неё есть заказ, созданный раньше, без отрезка S1 и ушедший дальше
    шага 1 или исполненный: сделку приняли раньше, чем видит история стадий."""
    best: dict = {}
    for o in orders:
        s = (o.get("_segs") or {}).get("segs", {}).get("S1")
        e = _end(s) if s else None
        if e is None:
            continue
        sid = str(o["id"])
        key = (e, len(sid), sid)
        if o.get("deal") not in best or key < best[o.get("deal")][0]:
            best[o.get("deal")] = (key, sid, _d(o.get("created")))
    out = set()
    for did, (_k, sid, created) in best.items():
        early = [o for o in orders if o.get("deal") == did and str(o["id"]) != sid
                 and not (o.get("_segs") or {}).get("segs", {}).get("S1")
                 and created and _d(o.get("created")) and _d(o.get("created")) < created
                 and (o.get("success") or (o.get("reachMax") or 0) >= 2)]
        if not early:
            out.add(sid)
    return out


def _unpaid(items: list[dict], deal_by: dict, today: dt.date) -> dict:
    """Д-3, E20: «Клиент не оплатил в срок» — главное число в СДЕЛКАХ (долг по договору с
    клиентом: один на сделку, решение 30.10.2026), заказы — в скобках (разные заказы пунктов
    unpaid). Деньги — rev: доля продажи этих заказов (orders[].revEur — оценка по доле закупки,
    без НДС), а не сумма сделок; saleEur — продажа сделок целиком, мелко (это не долг: оплату в
    деньгах портал не видит — решение Р-2). Пункт unpaid — заказа (до свёртки PY-2) или сделки с
    ords (после): заказы берутся из ords, иначе из order пункта. Ключи и подписи — в drill (их
    забирает build): сделки (dkeys) и заказы (keys)."""
    seen: dict = {}
    for q in items:
        subs = q.get("ords") or ([{"id": q.get("order"), "days": q.get("days"), "lbl": q.get("ordLbl")}]
                                 if q.get("order") else [])
        for x in subs:
            k = x.get("id")
            r = {"deal": q.get("deal"), "days": x.get("days") if x.get("days") is not None else q.get("days"),
                 "ref": q.get("ref") or "", "lbl": x.get("lbl") or q.get("ordLbl") or ""}
            if k and (k not in seen or (r["days"] or 0) > (seen[k]["days"] or 0)):
                seen[k] = r
    deals: dict[str, list] = {}
    for k, r in seen.items():
        deals.setdefault(str(r["deal"]), []).append((k, r))
    sale = sum((deal_by.get(d) or {}).get("saleEur") or 0 for d in deals
               if (deal_by.get(d) or {}).get("saleKnown", True))
    rev_of = {str(o.get("id")): o.get("revEur") or 0
              for d in deals for o in ((deal_by.get(d) or {}).get("orders") or [])}
    rev = sum(rev_of.get(str(k), 0) for k in seen)
    days = [r["days"] for r in seen.values() if r["days"] is not None]
    oldest = (today - dt.timedelta(days=max(days))).isoformat() if days else ""
    dtags = []
    for d, os_ in deals.items():
        dd = max((r["days"] or 0) for _k, r in os_)
        lbls = [r["lbl"] or f"id {k}" for k, r in os_]
        dtags.append(f"{len(os_)} {_pl(len(os_), 'заказ', 'заказа', 'заказов')}: "
                     + ", ".join(lbls[:2]) + (f" и ещё {len(lbls) - 2}" if len(lbls) > 2 else "")
                     + f" · срок оплаты прошёл {dd} дн. назад")
    return {"unit": "deal", "deals": len(deals), "orders": len(seen), "rev": round(rev),
            "revLabel": "доля продажи неоплаченных заказов — оценка по доле закупки, без НДС, не сумма долга",
            "saleEur": round(sale), "oldest": oldest,
            "label": "продажа сделок без НДС, не сумма долга",
            "units": {"deals": "deal", "orders": "order", "rev": "eur", "saleEur": "eur"},
            "keys": [str(k) for k in seen], "tags": [r["ref"] for r in seen.values()],
            "dkeys": [f"d:{d}" for d in deals], "dtags": dtags}


# ---- «Сделки без хозяина» (решение владельца 30.10.2026, п. 3) ---------------------------------
# Открытая сделка воронки реализации, у которой набор хозяев неполон: нет КАМа (поле «КАМ»), нет
# ОСС сделки (OSS_DEAL), нет менеджера ОРК (ни «Ответственного за реализацию» сделки, ни менеджера
# ОРК у её живых заказов) или хозяин ушёл (уволен по справочнику людей). Единица — сделка; один
# список у счётчика «Недели», фильтра «Сделок и денег» и «Разбора числа».
GONE_MARK = "(уволен)"                    # подпись уволенного у contracts.compute (who)
OWNER_ROLES = ("kam", "ossDeal", "orkMgr", "dealResp")
OWNER_ROLE_NAME = {"kam": "КАМ", "ossDeal": "ОСС сделки", "orkMgr": "менеджер ОРК",
                   "dealResp": "ответственный сделки"}
# кто назначает: КАМа — руководитель КАМ (хозяин пункта «назначить КАМа» очереди КАМа,
# kamqueue.HEAD); ОСС сделки, менеджера ОРК и ответственного сделки — руководитель ОРК (хозяин
# пункта «ведёт уволенный» gone, ork.ITEM["gone"]; шаг 1 «принять сделку и назначить менеджера ОРК»)
OWNER_ASSIGN = {"kam": "руководитель КАМ", "ossDeal": ork.ROLES["orkHead"],
                "orkMgr": ork.ROLES["orkHead"], "dealResp": ork.ROLES["orkHead"]}
# дни «без хозяина» — от входа сделки в «Реализацию» (cat0At): когда поле опустело, Битрикс не
# хранит; дату увольнения справочник людей тоже не хранит — у ушедшего дней нет (правило 16)
OWNER_SINCE = "вход сделки в «Реализацию»"


def _gone_nm(v) -> bool:
    return str(v or "").endswith(GONE_MARK)


def owner_gaps(d: dict, kind_of: dict | None = None) -> list[dict]:
    """Строка сделки (contracts.compute) → роли без хозяина [{role, state: none | out | gone, by}]
    в порядке OWNER_ROLES. Не открытая сделка воронки реализации (закрыта, «Оплата получена»,
    проиграна, вне кат. 0) — пусто: хозяева нужны только живой работе.

    Менеджер ОРК — «Ответственный за реализацию» сделки и менеджеры ОРК живых заказов
    (orders[].orkMgr): нет ни одного — «нет»; все есть, но ушли — «ушёл». «Нет» делится по
    живым заказам так же, как «Пульс ОРК» (pulse_who; kind_of — id заказа → вид mgr | out | none):
    у каждого живого заказа ответственный есть, но не из ОРК, — «out» («менеджер ОРК не назначен:
    ответственный заказа вне ОРК»); иначе «none». У роли orkMgr «нет» / «out» — счёт живых
    заказов: ordNone — без ответственного совсем (они же в «Заказах без менеджера ОРК» и в строке
    «не назначен» «Пульса»), ordOut — с ответственным вне ОРК (строка «ответственный вне ОРК»).
    Без kind_of вид заказа не известен — все считаются «без ответственного». Ответственный
    сделки бывает только «ушёл» (поле в Битриксе обязательное)."""
    if d.get("closed") or d.get("lost") or not d.get("inCat0", True):
        return []
    out: list[dict] = []

    def add(role: str, state: str) -> None:
        out.append({"role": role, "state": state, "by": OWNER_ASSIGN[role]})
    for role, v in (("kam", d.get("kam")), ("ossDeal", d.get("ossDeal"))):
        if not _nm(v):
            add(role, "none")
        elif _gone_nm(v):
            add(role, "gone")
    live = [o for o in (d.get("orders") or []) if o.get("live")]
    mgrs = {m for m in [_nm(d.get("orkResp"))] + [_nm(o.get("orkMgr")) for o in live] if m}
    kinds = [(kind_of or {}).get(str(o.get("id")), "none") for o in live]
    # заказы на шаге 0 (до передачи в ОРК, решение владельца 31.10.2026) менеджера ОРК не ждут:
    # все живые заказы такие — пропуска «менеджер ОРК» нет
    rest = [k for k in kinds if k != "pre"]
    if not mgrs:
        if rest or not kinds:
            n_out = sum(1 for k in rest if k == "out")
            n_none = len(rest) - n_out
            add("orkMgr", "out" if rest and not n_none else "none")
            out[-1].update(ordNone=n_none, ordOut=n_out)
    elif all(_gone_nm(m) for m in mgrs):
        add("orkMgr", "gone")
    if d.get("managerGone"):
        add("dealResp", "gone")
    return out


OWNER_OUT_TEXT = "менеджер ОРК не назначен: ответственный заказа вне ОРК"


def owner_gap_text(gaps: list[dict]) -> str:
    """«нет: КАМ, ОСС сделки · ушёл: менеджер ОРК» — подпись строки списка за числом; у «out» —
    своя подпись (OWNER_OUT_TEXT): ответственный у заказов есть, но не из ОРК."""
    none = [OWNER_ROLE_NAME[g["role"]] for g in gaps if g["state"] == "none"]
    gone = [OWNER_ROLE_NAME[g["role"]] for g in gaps if g["state"] == "gone"]
    out = any(g["state"] == "out" for g in gaps)
    return " · ".join(p for p in (("нет: " + ", ".join(none)) if none else "",
                                  OWNER_OUT_TEXT if out else "",
                                  ("ушёл: " + ", ".join(gone)) if gone else "") if p)


def no_owner(deals: list[dict], today: dt.date, kind_of: dict | None = None) -> dict:
    """«Сделки без хозяина» вида: n — сделки (= строки rows = ключи drill.noOwner), byRole —
    сделки по роли (сделка бывает в нескольких: сумма больше n), byState — сделки, где чего-то
    нет / где кто-то ушёл. Строка: сделка, роли, дни от входа в «Реализацию» у «нет» (у «ушёл» —
    None: дата ухода не хранится), заказов живых и всего (без проваленных). Имён и клиента нет:
    клиента страница берёт из строк сделок (CONTRACTS.rows), где он уже показан."""
    rows = []
    for d in deals:
        gaps = owner_gaps(d, kind_of)
        if not gaps:
            continue
        c0 = _d(d.get("cat0At"))
        dd = (today - c0).days if c0 else None
        miss = [{**g, "days": dd if g["state"] in ("none", "out") else None} for g in gaps]
        os_ = [o for o in (d.get("orders") or []) if not o.get("lost")]
        rows.append({"k": f"d:{d['deal']}", "deal": str(d["deal"]), "seq": d.get("seq") or 0,
                     "miss": miss, "days": max((g["days"] for g in miss if g["days"] is not None), default=None),
                     "orders": sum(1 for o in os_ if o.get("live")), "ordersAll": len(os_)})
    rows.sort(key=lambda r: (-(r["days"] if r["days"] is not None else -1), -int(r["seq"] or 0)))
    by_role = {r: sum(1 for x in rows if any(g["role"] == r for g in x["miss"])) for r in OWNER_ROLES}
    by_state = {s: sum(1 for x in rows if any(g["state"] == s for g in x["miss"])) for s in ("none", "out", "gone")}
    return {"n": len(rows), "unit": "deal", "rows": rows, "byRole": by_role, "byState": by_state,
            "roles": OWNER_ROLE_NAME, "assign": OWNER_ASSIGN, "since": OWNER_SINCE, "outText": OWNER_OUT_TEXT,
            "units": {"n": "deal", "byRole": "deal", "byState": "deal", "days": "day", "orders": "order",
                      "ordNone": "order", "ordOut": "order"}}


def _pl(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


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
                no_order, risk_q, reach_of, measure, today, ledger, s1_recent_o, recent, DS, d1, deals_at,
                o1, w1o_over, unpaid, own_ids=None) -> dict:
    """Строки заказов и сделок «Недели», ключи каждого числа и «Пульс ОРК». Множества — те же,
    что у чисел в build (over_ids, wait_ids, ведомость, пункты очереди с w2 и waitUtd)."""
    deal_by = {d["deal"]: d for d in deals}
    q_by: dict[str, list] = defaultdict(list)
    codes_of: dict[str, list] = defaultdict(list)       # заказ → коды пунктов очереди (life.queue)
    dcodes: dict[str, list] = defaultdict(list)          # сделка → коды пунктов уровня сделки
    for q in lifecycle.per_order(queue):           # пункт сделки из заказов — по заказу
        q_by[q["code"]].append(q)
        if q.get("order"):
            codes_of[str(q["order"])].append(q["code"])
    # коды сделки — по свёрнутой очереди: пункт сделки (unpaid, s1Over, payNoContract, noPayPlan…)
    # остаётся у строки d: сам, а не только у копий по заказам (иначе «Дел по сделке нет»)
    for q in queue:
        if q.get("unit", "order" if q.get("order") else "deal") == "deal":
            did = str(q.get("deal") or "")
            if q["code"] not in dcodes[did]:
                dcodes[did].append(q["code"])
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
        # шаг 0 — до передачи в ОРК (решение владельца 31.10.2026): не у менеджера ОРК и не «не
        # назначен» — своя строка «Пульса»
        who, kind = (PULSE_PRE, "pre") if st == ork.PRE_STEP else pulse_who(o, d)
        own = o.get("own") or {}
        h = hyg_of.get(oid) or {}
        r = {"k": str(oid), "no": order_no(o), "deal": str(o.get("deal") or ""), "seq": o.get("seq") or 0,
             "sup": o.get("sup") or "", "stage": o.get("stage") or "", "step": st,
             "wd": v.get("wd"), "norm": v.get("norm"), "mgr": who, "mk": kind,
             "gone": who.endswith("(уволен)"),
             "own": own.get("who") or "", "ownR": own.get("role") or "", "kam": _nm(d.get("kam")),
             # исполнитель — ответственный заказа не из ОРК: заказ не передан в ОРК (ork.owner, out)
             "ownOut": bool(own.get("out")),
             "dl": _iso(o.get("dl")), "fc": _iso(lifecycle.forecast(o, today, med, sup_slip)),
             "q": codes_of.get(str(oid), []),
             # от какого дня считаны р.д. шага — «с 14.10 (назначение)», как у пункта очереди
             # (lifecycle.order_ref → since_text, тот же ork.judged): список и очередь говорят одно
             "since": lifecycle.since_text(v.get("since"), v.get("sinceBy") or "", o, today)
             if v.get("wd") is not None else "",
             # срок шага (judged): сверх нормы, ждём УПД, срок неизвестен, не в счёте (гигиена,
             # приёмка вне «Реализации»); гигиена — виды и почему
             # сверх нормы на шаге заказа (W2, «Пульс»); на шаге 1 — over1: приёмка сделки или
             # назначение менеджера ОРК на новый заказ (W1 / W1o), не «дольше нормы» менеджера
             "over": v.get("over") is True and st not in DEAL_STEPS,
             "over1": v.get("over") is True and st == 1,
             # шаг 1 заказа уже принятой сделки — назначение менеджера ОРК на новый заказ (ordAssign,
             # W1o), не приёмка сделки: путь заказа и карточка говорят то же, что очередь
             "asg": st == 1 and o.get("deal") in DS["accepted"],
             "wu": bool(v.get("waitUtd")), "tu": bool(v.get("termUnknown")),
             # шаг 0 дольше нормы — передача в ОРК не подтверждена (judged.preOver): не over
             "po": bool(v.get("preOver")),
             "nc": nc(o), "hyg": h.get("kinds") or [], "hw": h.get("why") or "",
             "waits": waits_of.get(st, "") if st is not None else "",
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

    def O(it, tags=None, head=None) -> dict:          # список заказов
        return _u("order", keys(it), tags, head)

    def Dl(ids, tags=None, head=None) -> dict:         # список сделок: «d:<id>»
        return _u("deal", [f"d:{x}" for x in ids], tags, head)
    # live — все живые заказы ведомости; mapped — на шаге («Итого сейчас»); unk — стадия не из таблицы
    D: dict = {"live": O(L), "mapped": O(o for o in L if step[o["id"]] is not None),
               "unk": O(o for o in U if step[o["id"]] is None)}
    # W1 — сделки, чья приёмка дольше 1 р.д. (В1 а); W1o — заказы принятых сделок без менеджера
    # ОРК дольше 1 р.д.: одна единица на список (E16)
    D["W1"] = Dl(x for x in deals_at[1] if d1[x]["over"])
    D["W1o"] = O(w1o_over)
    D["W1oPop"] = O(o1)
    D["W2"] = O(o for o in L if any(o["id"] in over_ids[s] for s in ork.OVER_CODE if s not in DEAL_STEPS))
    w3k, w3t, seen = [], [], set()
    for q in risk_q:
        k = str(q.get("order") or "")
        if k and k not in seen:
            seen.add(k)
            w3k.append(k)
            w3t.append(q["code"])
    D["W3"] = _u("order", w3k, w3t)                     # подпись — код пункта (late, risk, forecast)
    # точки недели — строка на точку (ключ — её заказ): единица — точка («3 звонка по 2 заказам»)
    D["W5"] = _u("point", [str(c["order"]) for c in cps], [f"{c['kind']} · {_d(c['date']):%d.%m}" for c in cps])
    S: dict = {}
    for s in ledger:
        no = s["no"]
        here = [o for o in L if step[o["id"]] == no]
        if s.get("unit") == "deal":
            ids = deals_at.get(no) or []
            e: dict = {"now": Dl(ids), "orders": O(here)}
            if no == 1:
                e["over"] = Dl(x for x in ids if d1[x]["over"])
                e["unk"] = Dl(x for x in ids if d1[x]["unk"])
                e["nc"] = Dl(x for x in ids if d1[x]["nc"])
                e["ordersOver"] = O(o for o in here if o["id"] in over_ids.get(1, set()))
            if no == 13:
                # «к закрытию»: пункты dealClose («Оплата получена») — прежний список; now — и dealOpen
                e["close"] = Dl(x for x in ids if DS["closeKind"].get(x) == "dealClose")
        else:
            e = {"now": O(here)}
            if no in ork.NORM_WD:
                e["over"] = O(o for o in here if o["id"] in over_ids.get(no, set()))
                e["unk"] = O(o for o in here if ov[o["id"]]["termUnknown"])
                e["nc"] = O(o for o in here if nc(o))
            if no in wait_ids:
                e["wait"] = O(o for o in here if o["id"] in wait_ids[no])
        # «нет артефактов» — пары (заказ, артефакт), как в ведомости: строка — пара (единица — файл)
        ak, at = [], []
        for a in ork.ARTIFACTS:
            if a["step"] != no:
                continue
            for o in U:
                if ork.art_required(o, a, reach_of.get(o["id"])) and not (o.get("art") or {}).get(a["key"]):
                    ak.append(str(o["id"]))
                    at.append("нет: " + a["name"])
        e["art"] = _u("file", ak, at)
        # Д-5а: те же пары — по заказу (ledger.artOrders): строка — заказ, подпись — каких файлов нет
        ao: dict[str, list] = {}
        for k, t in zip(ak, at):
            ao.setdefault(k, []).append(t.removeprefix("нет: "))
        e["artO"] = _u("order", list(ao), ["нет: " + ", ".join(v) for v in ao.values()])
        if no == 12:
            e["closed"] = O(C12)
        if no == 7:
            e["p80"] = _u("order", p80)
        S[str(no)] = e
    D["step"] = S
    # «Кого ждём»: заказы шагов заказа (adj) и сделки шагов сделки (adjDeals) — по роли; у карты —
    # единица её списков (страница перебирает роли ork.ADJACENT, а не ключи карты)
    D["adj"] = {"unit": "order", **{r: [k for s in ledger if s["waits"] == r and s.get("unit") != "deal"
                                       for k in S[str(s["no"])]["now"]["k"]] for r in ork.ADJACENT}}
    D["adjDeals"] = {"unit": "deal", **{r: [k for s in ledger if s["waits"] == r and s.get("unit") == "deal"
                                           for k in S[str(s["no"])]["now"]["k"]] for r in ork.ADJACENT}}
    D["p80"] = _u("order", p80)
    D["noOrder"] = Dl(DS["noOrder"])
    D["noOrderOver"] = Dl(x for x in DS["noOrder"] if d1[x]["over"])
    wait_all = set().union(*wait_ids.values()) if wait_ids else set()
    D["waitUtd"] = O(o for o in L if o["id"] in wait_all)
    # «Сверх нормы по менеджерам» — пункты с w2 и waitUtd у хозяина пункта (тот же счёт); заказы
    who_d: dict[str, dict] = {}
    for code, flag, mark in ([(c, "over", "w2") for cs in ork.STEP_CODES.values() for c in cs]
                             + [(c, "wait", "waitUtd") for c in ork.WAIT_CODE.values()]):
        for q in q_by.get(code, []):
            if q.get(mark):
                w = q_who(q)
                who_d.setdefault(w, {"over": [], "wait": []})[flag].append(str(q.get("order") or ""))
    D["who"] = {"unit": "order", **who_d}
    H: dict = {"all": _u("order", [str(x["order"]) for x in hyg_items]),
               "excl": _u("order", [str(x["order"]) for x in hyg_items if x["excl"]]),
               "noMgr": _u("order", [str(x["order"]) for x in hyg_items if "noMgr" in x["kinds"]])}
    for k in ("behind", "abandoned", "old", "unknown"):
        H["x" + k] = _u("order", [str(x["order"]) for x in hyg_items if x["excl"] and k in x["kinds"]])
    # задача подтверждения открыта, а заказ уже дальше шага 0 — из счёта не исключает (без «x»)
    H["confirmOpen"] = _u("order", [str(x["order"]) for x in hyg_items if "confirmOpen" in x["kinds"]])
    D["hyg"] = H
    # «Моя приёмка»: «в срок за 20 р.д.: k из n» — отрезки S1 в счёте (_timeliness: не пропущен,
    # без «пачки», возврата и обратного порядка меток, р.д. посчитаны); скрытая — без списка.
    # Один отрезок на сделку (first_accept): строка — первый заказ сделки
    if isinstance(recent, dict) and recent.get("state") in ("show", "prelim"):
        rk, rt = [], []
        for o in s1_recent_o:
            s = o["_segs"]["segs"]["S1"]
            if s.get("skip") or not _clean(s) or s.get("wd") is None:
                continue
            rk.append(str(o["id"]))
            rt.append(f"{'в норме' if s.get('inNorm') else 'сверх нормы'} · {s['wd']} р.д.")
        D["W1recent"] = _u("order", rk, rt, "первый заказ сделки: одна приёмка на сделку")
    live_k = {r["k"] for r in rows}
    want = set((D.get("W1recent") or {}).get("k") or [])
    # законченные и не из ведомости заказы за числами — строкой без менеджера ОРК
    hist = [_hist_row(o, with_mgr=False) for o in s1_recent_o
            if str(o["id"]) in want and str(o["id"]) not in live_k]

    # сделки за числами уровня сделки — строка на сделку: шаг сделки (1 — без заказа или на
    # приёмке, 2–3 — договор с заказчиком, 13 — к закрытию) и «клиент не оплатил». kind —
    # шаг ведомости (одна сделка — один шаг), kinds — все списки, где сделка есть
    deals_out: dict[str, dict] = {}
    u_by = DS["uBy"]

    def drow(did: str, kind: str, st: int | None) -> dict:
        d = deal_by.get(did) or {}
        if did not in deals_out:
            c = (d.get("stepCell") or {}) if kind == "noOrder" else {}
            os_ = u_by.get(did) or []
            deals_out[did] = {"k": f"d:{did}", "deal": str(did), "seq": d.get("seq") or 0,
                              "stage": d.get("stageName") or "", "kind": kind, "kinds": [],
                              "step": st, "dealStep": d.get("dealStep"),
                              "wd": c.get("wd"), "norm": ork.NORM_WD[1] if st == 1 else None,
                              "own": c.get("who") or "", "ownR": c.get("role") or "",
                              "mgr": _nm(d.get("orkResp")) or PULSE_NONE, "kam": _nm(d.get("kam")),
                              # заказы сделки (живые) — номерами: «шаги заказов 4–12» в строке сделки
                              "ords": [str(o["id"]) for o in os_],
                              "q": dcodes.get(str(did), [])}
        r = deals_out[did]
        if kind not in r["kinds"]:
            r["kinds"].append(kind)
        return r
    for did in DS["noOrder"]:
        r = drow(did, "noOrder", 1)
        if d1[did]["over"]:
            r.update(over=True)
    for q in q_by.get("noOrder", []) + q_by.get("dealAccept", []):
        if q.get("order") or q.get("deal") not in deals_out:
            continue
        r = deals_out[q["deal"]]
        r.update(wd=q.get("days") if q.get("days") is not None else r["wd"],
                 own=q.get("who") or r["own"], ownR=q.get("role") or r["ownR"])
    for did in DS["acceptL"]:
        r = drow(did, "accept", 1)
        x = d1[did]
        r.update(wd=x["wd"], over=x["over"], tu=x["unk"], nc=x["nc"],
                 own=next((q.get("who") for q in q_by.get(ork.OVER_CODE[1], []) if q.get("deal") == did
                           and q.get("who")), "") or "",
                 ownR="orkHead")
    for no in (2, 3):
        for did in deals_at.get(no) or []:
            d = deal_by.get(did) or {}
            drow(did, "contract", no).update(own=_nm(d.get("kam")), ownR="kam")
    for did in DS["close"]:
        kind = DS["closeKind"][did]
        r = drow(did, kind, 13)
        q = next((q for q in q_by.get(kind, []) if q.get("deal") == did), {})
        r.update(own=q.get("who") or r["own"], ownR=q.get("role") or r["ownR"], cd=q.get("days"))
    for k in unpaid.get("dkeys") or []:
        drow(k[2:], "unpaid", None)
    for did in own_ids or []:                       # «Сделки без хозяина» — строка сделки за ключом «d:»
        drow(did, "noOwner", None)

    # «Пульс ОРК» (решение владельца 29.10.2026, п. 4): живые заказы по менеджеру ОРК; всем с
    # вкладкой, как «Сверх нормы по менеджерам» (решение 27.10.2026, А) — счёт, без среднего и
    # отклонения (M3 «Загрузка» — под правом ork_heads), без сортировки по числу
    live_n = len(L)
    G: dict[str, dict] = {}
    for r, o in zip(rows, L):
        d = deal_by.get(o.get("deal")) or {}
        g = G.setdefault(r["mgr"], {"who": r["mgr"], "kind": r["mk"], "gone": bool(r.get("gone")),
                                    "keys": [], "overKeys": [], "waitKeys": [], "preKeys": [], "deals": set(),
                                    "cust": set(), "byStep": Counter()})
        g["keys"].append(r["k"])
        if r.get("over"):
            g["overKeys"].append(r["k"])
        if r.get("po"):
            g["preKeys"].append(r["k"])
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
             # строка «до передачи в ОРК»: из них дольше нормы шага 0 — не over (Σ over = W2)
             **({"preOver": len(g["preKeys"])} if g["kind"] == "pre" else {}),
             "byStep": dict(sorted(g["byStep"].items()))}
            for g in sorted(G.values(), key=lambda g: (PULSE_ORDER[g["kind"]], g["who"].casefold()))]
    # заказы строки «Пульса» ключами не дублируются: это строки week.orders с mgr == who (сверх
    # нормы — и over, ждём УПД — и wu) — ровно та группировка, что выше (страница: _odResolve)
    all_deals = {r["deal"] for r in rows}
    # «сделок» строки «Пульса» — сделки с ЕГО заказами: у сделки бывают заказы двух менеджеров, Σ
    # строк больше итога (E24) — итог pulse.deals — разные сделки, не сумма строк
    pulse = {"rows": prow, "live": live_n, "deals": len(all_deals),
             "dealsLabel": "сделок с его заказами", "dealsSum": sum(p["deals"] for p in prow),
             "dealsNote": "у сделки бывают заказы нескольких менеджеров — сумма строк больше итога",
             "units": {"n": "order", "deals": "deal", "customers": "customer", "over": "order", "wait": "order"},
             "customers": len({(deal_by.get(x) or {}).get("customer") for x in all_deals} - {None, "", "—"}),
             "over": sum(p["over"] for p in prow), "wait": sum(p["wait"] for p in prow),
             "out": PULSE_OUT, "none": PULSE_NONE, "pre": PULSE_PRE}
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
                    drill[f"{i}.{nm}"] = {"unit": "order", "k": [str(o["id"]) for o in os_], "t": tg,
                                          "h": f"{Q.get('q')}: {heads[nm]}"}
        for j, srow in enumerate(Q.get("suppliers") or []):
            sk, st_ = by_sup.get(srow.get("sup"), ([], []))
            _dk(srow, drill, f"{i}.s{j}", sk, st_, prefix="q",
                head=f"{srow.get('sup')} · {Q.get('q')}: готовность к обещанному сроку")
            if "dk" not in srow and srow.get("n") and len(sk) == srow["n"]:
                # доля «в срок» скрыта («пачки», «план = факт»), а число готовностей видно — список за
                # ним без подписей «в срок / позже»: скрытая доля из него не складывается
                drill[f"{i}.s{j}n"] = {"unit": "order", "k": sk, "t": None,
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
        su = "order" if code == "cycle" else "segment"
        _dk(st, drill, f"Q2.{code}", [str(o["id"]) for _v, o in rows_], [fmt(v) for v, _o in rows_], prefix="q",
            head=f"Сроки за год · {nm}: заказы в расчёте, длинные сверху", unit=su)
        if st.get("dk"):
            for _v, o in rows_:
                used[str(o["id"])] = o
            tail = [(v, o) for v, o in rows_ if st.get("p80") is not None and v > st["p80"]]
            if tail:
                drill[f"Q2.{code}.tail"] = {"unit": su, "k": [str(o["id"]) for _v, o in tail],
                                            "t": [fmt(v) for v, _o in tail],
                                            "h": f"{_cap(nm)}: дольше, чем у 8 из 10 ({round(st['p80'])} дн.)"}
    # E47: цикл сделки — сделки за медианой и P80 (длинные сверху), ключ «d:<id>»
    cyc = sorted(deal_cycles(measure, today, year_from).items(), key=lambda x: -x[1]["cd"])
    st = Q2.get("cycleDeal") or {}
    _dk(st, drill, "Q2.cycleDeal", [f"d:{d}" for d, _v in cyc],
        [f"{v['cd']} дн. · заказов {v['n']} · последняя доставка {v['last']:%d.%m.%Y}" for _d0, v in cyc],
        prefix="q", head="Сроки за год · сделка: от входа в «Реализацию» до последней доставки", unit="deal")
    if st.get("dk") and st.get("p80") is not None:
        tail = [(d, v) for d, v in cyc if v["cd"] > st["p80"]]
        if tail:
            drill["Q2.cycleDeal.tail"] = {"unit": "deal", "k": [f"d:{d}" for d, _v in tail],
                                          "t": [f"{v['cd']} дн." for _d0, v in tail],
                                          "h": f"Сделка: дольше, чем у 8 из 10 ({round(st['p80'])} дн.)"}
    # строки — только заказов из показанных списков
    want = {k for v in drill.values() for k in (v["k"] if isinstance(v, dict) else v)}
    quarter["drill"] = drill
    # без менеджера ОРК: «Квартал» видят все с вкладкой (поимённо — только «Месяц», под правом)
    quarter["orders"] = [_hist_row(o, with_mgr=False) for k, o in used.items() if k in want]
    quarter["deals"] = _deal_rows({k[2:] for k in want if k.startswith("d:")}, measure)
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
            drill[f"{key}.{name}"] = {"unit": "segment", "k": [str(o["id"]) for o, _s in rows],
                                      "t": [tag(s) for _o, s in rows],
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
    first = first_accept(orders)              # приёмка — одна на сделку (как _month)

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
                if code == "S1" and (not deal_ok_all.get(o.get("deal"), True) or str(o["id"]) not in first):
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
                head=(f"Приёмка сделки (шаг 1) · {mw}: сделки в счёте" if c == "S1"
                      else f"{SEG_TXT[c]} · {mw}: шаги в счёте"), unit="segment")
            _excl_keys(M1.get(c) or {}, segs[c], drill, f"{mi}.{c}", f"{SEG_TXT[c]} · {mw}", used)
        # приёмка — одна на сделку (решение 30.10.2026): плитка S1 считает сделки — и список за ней
        # в сделках (ключ «d:», строка сделки с её заказами), а не отрезки заказов
        for part in (f"{mi}.S1", f"{mi}.S1.excl", f"{mi}.S1.skip"):
            e = drill.get(part)
            if e:
                e["unit"] = "deal"
                e["k"] = [f"d:{(by_id.get(k) or {}).get('deal') or ''}" for k in e["k"]]
        allm = segs["S2"] + segs["S3"] + segs["S6"]
        _dk(M1.get("mgr") or {}, drill, f"{mi}.mgr", *seg_keys(allm), prefix="m",
            head=f"Шаги ОРК (шаги 4, 5, 8) · {mw}: шаги в счёте", unit="segment")
        _excl_keys(M1.get("mgr") or {}, allm, drill, f"{mi}.mgr", f"Шаги ОРК (шаги 4, 5, 8) · {mw}", used)
        _dk(row.get("M2") or {}, drill, f"{mi}.M2", *art_keys(arts), prefix="m",
            head=f"Полнота карточки · {mw}: пары «заказ, файл шага»", unit="file")
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
                    drill[f"{mi}.{part}"] = {"unit": "order", "k": [k for k, _ in op], "t": [t for _, t in op],
                                             "h": head}
        # E44: сделки, у которых срок последнего заказа наступил в месяце — главная доля «Месяца»
        dd_k = row.pop("_dueDeals", None) or {}
        DD = row.get("otdDueDeals") or {}
        if DD.get("n") == len(dd_k.get("k") or []):
            _dk(DD, drill, f"{mi}.dueDeals", list(dd_k.get("k") or []), list(dd_k.get("t") or []) or None,
                prefix="m", head=f"Срок последнего заказа сделки · {mw}: сделки к сроку, позже и не доставлены",
                unit="deal")
    live_era = [o for o in U if _d(o.get("created")) and _d(o["created"]) >= ork.ORK_SINCE]
    for pi, p in enumerate(people):
        who = p.get("who")
        nm = str(who or "").replace(" (уволен)", "")
        live = [o for o in live_era if o.get("respOrk") and o.get("orkMgr") == who]
        if live and len(live) == p.get("live"):
            for o in live:
                used[str(o["id"])] = o
            drill[f"p{pi}.live"] = {"unit": "order", "k": [str(o["id"]) for o in live], "t": None,
                                    "h": f"{nm} · заказы в работе, созданные с {ork.ORK_SINCE:%d.%m.%Y}"}
            p["liveDk"] = f"m:p{pi}.live"
        for mi, part in ((1, p), (0, p.get("cur") or {})):
            src = pp.get((mi, who)) or {"segs": {}, "art": []}
            pm, mm = part.get("M1") or {}, part.get("m") or ""
            # текущий (неполный) месяц человека страница показывает суммой S2+S3+S6 и полнотой —
            # отрезки по отдельности у него не видны, и ключей за ними нет (каждый dk — число на странице)
            for c in (("S2", "S3", "S6") if mi == 1 else ()):
                _dk(pm.get(c) or {}, drill, f"p{pi}.{mi}.{c}", *seg_keys(src["segs"].get(c) or []), prefix="m",
                    head=f"{nm} · {SEG_TXT[c]} · {_mm_word(mm)}", unit="segment")
            _dk(pm.get("mgr") or {}, drill, f"p{pi}.{mi}.mgr",
                *seg_keys([x for c in ("S2", "S3", "S6") for x in (src["segs"].get(c) or [])]), prefix="m",
                head=f"{nm} · шаги ОРК (шаги 4, 5, 8) · {_mm_word(mm)}", unit="segment")
            _dk(part.get("M2") or {}, drill, f"p{pi}.{mi}.M2", *art_keys(src["art"]), prefix="m",
                head=f"{nm} · полнота карточки · {_mm_word(mm)}", unit="file")
    # Д-2, Д-10 (R4): столбец «Доставки к сроку по месяцам» → заказы, у кого срок наступил в месяце
    for r12 in month.pop("_otd", None) or []:
        kk = r12.get("keys") or {}
        ks = [str(k) for k in kk.get("k") or []]
        if ks and len(ks) == r12.get("due"):
            for k in ks:
                if k in by_id:
                    used[k] = by_id[k]
            mm = str(r12.get("m") or "")
            drill[f"y.{mm}"] = {"unit": "order", "k": ks, "t": list(kk.get("t") or []) or None,
                                "h": f"Срок клиента наступил · {_mm_word(mm)}: к сроку, позже и не доставлены"}
    # Д-10 (R4): «ответственный заказа не из ОРК» у загрузки M3 — список этих заказов (счёт M3.notOrk)
    no = [o for o in live_era if not o.get("respOrk")]
    if no and len(no) == (month.get("M3") or {}).get("notOrk"):
        for o in no:
            used[str(o["id"])] = o
        drill["notOrk"] = {"unit": "order", "k": [str(o["id"]) for o in no], "t": None,
                           "h": "Заказы в работе: ответственный заказа не из отдела ОРК"}
    # E44: «сделка в срок» за 90 дней — сделки за числом (lifecycle.otd.deals90.keys)
    d90 = month.pop("_deals90", None) or {}
    if (month.get("deals90") or {}).get("n") and len(d90.get("k") or []) == month["deals90"]["n"]:
        drill["deals90"] = {"unit": "deal", "k": list(d90["k"]), "t": list(d90.get("t") or []) or None,
                            "h": "Сделки в срок за 90 дней: все заказы сделки доставлены не позже своего срока"}
        month["deals90"]["dk"] = "m:deals90"
    want = {k for v in drill.values() for k in (v["k"] if isinstance(v, dict) else v)}
    month["drill"] = drill
    month["orders"] = [_hist_row(o) for k, o in used.items() if k in want]
    # строки сделок за числами в сделках (dueDeals, deals90): номер сделки и её заказы
    month["deals"] = _deal_rows({k[2:] for k in want if k.startswith("d:")}, orders)
    month["strs"] = _pack(month["orders"])
