"""Жизненный цикл заказа в реализации: от размещения у поставщика до гарантии.

Чистые функции поверх заказов СП-172, уже приведённых к фактам (см. contracts.py):
фаза заказа определяется ФАКТАМИ — датами и файлами в карточке, а не названием
стадии (у заказов несколько воронок со своими стадиями, и стадия часто отстаёт от
факта). Зонд v49: фактические даты отгрузки поставщика, прихода на склад и
доставки клиенту заполнены у 85–100 % заказов, прошедших эти точки.

Фазы (порядок значим):
  place     — размещение: нет ни оплаты поставщику, ни подтверждения производства;
  make      — производство: оплата или подтверждение есть, поставщик не отгрузил;
  move      — в пути: поставщик отгрузил, клиенту не доставлено;
  close     — закрытие: доставлено, нет УПД или платежа клиента;
  warranty  — гарантия: доставлено, УПД и платёж есть, гарантийный срок идёт;
  done      — завершено: гарантийный срок истёк.

Очередь «что делать» — исключения по заказам и сделкам, у каждого хозяин (ОСС
заказа, иначе ОСС сделки, иначе ответственный) и действие.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

RISK_DAYS = 14          # до срока клиента меньше двух недель, а поставщик не отгрузил
UTD_GRACE = 14          # УПД после доставки ждём две недели
PAY_GRACE = 30          # без планового срока оплаты: платёж клиента ждём 30 дней после доставки
WARRANTY_MONTHS = 12    # допущение до решения владельца: 12 мес. от доставки клиенту
CLOSE_DAYS = 365        # УПД и оплату клиента ждём по доставкам за год, как зонд v53
OTD_MONTHS = 12         # история «доставлено в срок» — помесячно за год
SUP_MIN = 3             # поставщик в рейтинге надёжности — от 3 отгрузок с планом и фактом

PHASES = (
    ("place", "Размещение у поставщика"),
    ("make", "Производство"),
    ("move", "В пути к клиенту"),
    ("close", "Закрытие: УПД и оплата"),
    ("warranty", "Гарантия"),
)

# код → (важность: меньше — важнее, что случилось, что сделать)
EXC = {
    "late": (1, "срок клиента прошёл, не доставлено", "согласовать с клиентом новый срок, ускорить поставку"),
    "risk": (2, f"до срока клиента ≤ {RISK_DAYS} дн., поставщик не отгрузил", "эскалировать поставщику, проверить готовность"),
    "forecast": (3, "прогноз доставки позже срока клиента", "ускорить сейчас: поставщик, маршрут; предупредить клиента"),
    "shipSlip": (4, "плановая отгрузка поставщика прошла, факта нет", "запросить у поставщика статус и новую дату"),
    "planAfterDl": (5, "план доставки позже срока клиента", "предупредить клиента или сменить маршрут"),
    # «платежа нет» и «УПД нет» — в карточке заказа нет файла: оплату в деньгах портал не
    # видит (решение Р-2), подпись говорит ровно то, что известно
    "unpaid": (6, "доставлено, срок оплаты клиента прошёл, платёжки клиента в заказе нет",
               "проверить оплату; пришла — приложить платёжку, нет — напомнить клиенту"),
    "noUtd": (7, f"доставлено больше {UTD_GRACE} дн. назад, УПД в заказе нет", "получить подписанный УПД и приложить"),
    "noPayPlan": (8, "доставлено, нет планового срока оплаты", "внести срок оплаты из договора"),
    "dealOpen": (9, "все заказы закрыты, сделка открыта", "закрыть сделку"),
    "noOrder": (10, "в реализации больше недели без заказа поставщику", "разместить заказ"),
    "gone": (11, "ведёт уволенный", "переназначить"),
    "noDl": (12, "нет срока клиента", "внести срок из договора"),
    "noPlanDates": (13, "не заполнены даты размещения", "внести даты производства и доставки клиенту по бюджету"),
    "noFact": (14, "заказ закрыт без даты доставки клиенту", "внести фактическую дату доставки"),
}
# обязательные даты размещения (решение владельца 19.10.2026, п. 9)
PLAN_DATES = (("prodStart", "начало производства"), ("prodEnd", "конец производства"),
              ("delBudget", "доставка клиенту"))
# кто из хозяев сделки уволен — признаки строки сделки contracts.py (справочник людей)
GONE_ROLES = (("managerGone", "ответственный сделки"), ("ossGone", "ОСС"),
              ("respGone", "ответственный заказа"))


def _add_months(d: dt.date, m: int) -> dt.date:
    y, mo = divmod(d.month - 1 + m, 12)
    y += d.year
    mo += 1
    for day in (d.day, 30, 29, 28):
        try:
            return dt.date(y, mo, day)
        except ValueError:
            continue
    return d


def _median(xs: list) -> float | None:
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    n = len(s)
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _p75(xs: list) -> float | None:
    s = sorted(x for x in xs if x is not None)
    return float(s[min(len(s) - 1, int(round(0.75 * (len(s) - 1))))]) if s else None


def phase(o: dict, today: dt.date, warranty_months: int = WARRANTY_MONTHS) -> str:
    """Фаза заказа по фактам. o — заказ, приведённый к фактам (поля ниже). Заказ,
    закрытый успехом, закрытие прошёл, даже если УПД или платёжки нет файлом:
    платёжка клиента стала полем только в июле 2025, старые заказы её не имеют."""
    if o.get("delFact"):
        if o.get("live") and (not o.get("utd") or not o.get("clientPaid")):
            return "close"
        return "warranty" if _add_months(o["delFact"], warranty_months) >= today else "done"
    if o.get("success"):
        return "done"                     # закрыт без даты доставки: гарантию не от чего считать
    if o.get("shipFact") or o.get("inbFact"):
        return "move"
    if (o.get("supPaidPct") or 0) > 0 or o.get("prodConf"):
        return "make"
    return "place"


def speeds(orders: list[dict], today: dt.date) -> tuple[dict, dict]:
    """По заказам, доставленным и отгруженным за год: длительности звеньев (дн.) и
    сдвиг отгрузки каждого поставщика (дн.) против БЮДЖЕТНОГО срока окончания
    производства. Плановую дату отгрузки переписывают под факт (зонд v53: медиана
    и 75 % сдвига к ней — 0 дн.), с ней поставщик всегда «в срок»; бюджетную дату
    не трогают (доставка к бюджету: медиана +30 дн.). Нет бюджетной — отгрузка в
    надёжность не идёт: с плановой поставщик выходил бы «вовремя» всегда (ревизия
    достоверности 23.10.2026)."""
    lead: dict[str, list] = defaultdict(list)
    sup_rel: dict[str, list] = defaultdict(list)
    for o in orders:
        f = o.get("delFact")
        if f and f <= today and (today - f).days <= 365:
            c, s, i_ = o.get("created"), o.get("shipFact"), o.get("inbFact")
            if c and f >= c:
                lead["total"].append((f - c).days)
            if c and s and s >= c:
                lead["toShip"].append((s - c).days)
            if s and i_ and i_ >= s:
                lead["transit"].append((i_ - s).days)
            if i_ and f >= i_:
                lead["lastMile"].append((f - i_).days)
        s, sp = o.get("shipFact"), o.get("prodEnd")
        if s and sp and (today - s).days <= 365 and o.get("sup"):
            sup_rel[o["sup"]].append((s - sp).days)
            lead["shipSlip"].append((s - sp).days)
    return lead, sup_rel


def forecast(o: dict, today: dt.date, med: dict, sup_slip: dict) -> dt.date | None:
    """Прогноз доставки клиенту живого заказа: от последнего факта (приход на склад,
    отгрузка) или — не отгружен — от позднейшей из дат: плановая отгрузка, бюджетный
    конец производства + обычное опоздание ЭТОГО поставщика, сегодня; плюс медианы
    оставшихся звеньев. None — не из чего считать."""
    if not o.get("live") or o.get("delFact"):
        return None
    tr, lm = med.get("transit"), med.get("lastMile")
    if o.get("inbFact"):
        base, rest = o["inbFact"], lm
    elif o.get("shipFact"):
        base, rest = o["shipFact"], (tr + lm if tr is not None and lm is not None else None)
    else:
        rest = tr + lm if tr is not None and lm is not None else None
        кандидаты = []
        if o.get("shipPlan"):
            кандидаты.append(o["shipPlan"])
        if o.get("prodEnd"):
            кандидаты.append(o["prodEnd"] + dt.timedelta(
                days=max(0.0, sup_slip.get(o.get("sup") or "", 0.0) or 0.0)))
        if not кандидаты and o.get("created") and med.get("toShip") is not None:
            кандидаты.append(o["created"] + dt.timedelta(days=med["toShip"]))
        if not кандидаты:
            return None
        base = max(кандидаты)
        # не отгружен: путь к клиенту не начнётся раньше сегодняшнего дня — иначе
        # прошедшая плановая отгрузка тянула прогноз назад и сигнал молчал там,
        # где он нужнее всего
        base = max(base, today)
    if rest is None:
        return None
    return max(today, base + dt.timedelta(days=round(rest)))


def order_exceptions(o: dict, today: dt.date, fc: dt.date | None = None) -> list[tuple[str, int | None]]:
    """[(код, дней)] — исключения одного заказа. Закрытый провалом заказ сюда не попадает.
    fc — прогноз доставки (forecast): позже срока клиента — ранний сигнал."""
    out: list[tuple[str, int | None]] = []
    dl, fact = o.get("dl"), o.get("delFact")
    shipped = o.get("shipFact") or o.get("inbFact")      # пришёл на склад — значит, отгружен
    if o.get("live"):
        if dl and not fact:
            if dl < today:
                out.append(("late", (today - dl).days))
            elif (dl - today).days <= RISK_DAYS and not shipped:
                out.append(("risk", (dl - today).days))
            elif fc and fc > dl:
                out.append(("forecast", (fc - dl).days))
            plan = o.get("delPlan") or o.get("delBudget")
            if plan and plan > dl and dl >= today:
                out.append(("planAfterDl", (plan - dl).days))
        sp = o.get("shipPlan")
        if sp and not shipped and not fact and sp < today:
            out.append(("shipSlip", (today - sp).days))
        if not dl and not fact:
            out.append(("noDl", None))
        if any(not o.get(k) for k, _ in PLAN_DATES):
            out.append(("noPlanDates", None))
        # закрытие — только у живого заказа и только за CLOSE_DAYS: закрытый заказ
        # закрытие прошёл, а старые доставки без файла платёжки — пробел учёта до
        # появления поля, а не долг клиента
        if fact and (today - fact).days <= CLOSE_DAYS:
            since = (today - fact).days
            if not o.get("utd") and since > UTD_GRACE:
                out.append(("noUtd", since))
            if not o.get("clientPaid"):
                due = o.get("payPlan") or (fact + dt.timedelta(days=PAY_GRACE))
                if due < today:
                    out.append(("unpaid", (today - due).days))
            if not o.get("payPlan"):
                out.append(("noPayPlan", since))
    elif o.get("success") and o.get("closedRecent") and not fact:
        out.append(("noFact", None))
    return out


def build(orders: list[dict], deals: list[dict], today: dt.date,
          warranty_months: int = WARRANTY_MONTHS) -> dict:
    """orders — заказы, приведённые к фактам; deals — строки сделок contracts.py
    (deal, seq, customer, closed, done, norders, firstLag, managerGone, ossGone,
    respGone, manager, oss). → раздел «life» вкладки «Реализация»."""
    deal_by = {d["deal"]: d for d in deals}
    queue: list[dict] = []
    counts: Counter = Counter()
    ph: dict[str, dict] = {k: {"n": 0, "late": 0, "risk": 0, "orders": 0, "over": 0} for k, _ in PHASES}
    ph_deals: dict[str, set] = defaultdict(set)

    def _nm(v) -> str:
        return v if v not in (None, "", "—") else ""

    def owner(o: dict, d: dict | None) -> str:
        """Ответственный заказа (Responsible person — хозяин исполнения, решение владельца
        19.10.2026, п. 1) → ОСС заказа → ОСС сделки (поле сделки) → ответственный сделки."""
        d = d or {}
        return (_nm(o.get("resp")) or _nm(o.get("oss")) or _nm(d.get("ossDeal"))
                or _nm(d.get("manager")) or "—")

    def gone(name: str) -> bool:
        return name.endswith("(уволен)")      # contracts.who подписывает ушедших так

    lead, sup_rel = speeds(orders, today)
    med = {k: _median(v) for k, v in lead.items()}
    sup_slip = {k: _median(v) for k, v in sup_rel.items() if len(v) >= SUP_MIN}
    # нормы звеньев — 75-й процентиль за год (решение владельца 19.10.2026, п. 4)
    norm_ship = _p75(lead.get("toShip", []))
    _tr, _lm = _p75(lead.get("transit", [])), _p75(lead.get("lastMile", []))
    norm_path = _tr + _lm if _tr is not None and _lm is not None else None

    def over_norm(o: dict, p: str) -> bool:
        if not o.get("live"):
            return False
        if p in ("place", "make") and norm_ship is not None and o.get("created"):
            return (today - o["created"]).days > norm_ship
        base = o.get("shipFact") or o.get("inbFact")
        if p == "move" and norm_path is not None and base:
            return (today - base).days > norm_path
        return False
    # вселенная — та же, что у таблицы сделок: заказы известных сделок, без
    # проигранных; у закрытой (выигранной) сделки сроки заказов уже не отклонения
    СРОКИ = {"late", "risk", "forecast", "shipSlip", "planAfterDl", "noDl", "noPlanDates"}
    ОПЛАТА = {"unpaid", "noPayPlan"}       # у закрытой выигранной сделки оплата получена
    in_scope = []
    for o in orders:
        d = deal_by.get(o.get("deal"))
        if not d or d.get("lost"):
            continue
        in_scope.append((o, d))
        p = phase(o, today, warranty_months)
        fc = forecast(o, today, med, sup_slip)
        exc = order_exceptions(o, today, fc)
        if d.get("closed"):
            exc = [(c, n) for c, n in exc if c not in СРОКИ | ОПЛАТА]
        in_tile = p in ph and (o.get("live") or p == "warranty") and not (d.get("closed") and o.get("live"))
        if in_tile:
            ph[p]["orders"] += 1
            ph_deals[p].add(o.get("deal"))
            ph[p]["late"] += any(c == "late" for c, _ in exc)
            ph[p]["risk"] += any(c in ("risk", "forecast") for c, _ in exc)
            ph[p]["over"] += over_norm(o, p)
        for code, days in exc:
            counts[code] += 1
            sev, what, act = EXC[code]
            if code == "noPlanDates":
                what = what + ": " + ", ".join(nm for k, nm in PLAN_DATES if not o.get(k))
            w = owner(o, d)
            queue.append({"code": code, "sev": sev, "what": what, "act": act, "days": days,
                          "who": w, "whoGone": gone(w),
                          "deal": o.get("deal"), "seq": (d or {}).get("seq") or o.get("seq") or 0,
                          "customer": (d or {}).get("customer") or "—", "order": o.get("id"),
                          "title": o.get("title") or "", "sup": o.get("sup") or "—",
                          "dl": o["dl"].strftime("%d.%m.%Y") if o.get("dl") else "",
                          "fc": fc.strftime("%d.%m.%Y") if fc else "",
                          # КАМ клиента (поле «КАМ») — в копию: оплата и перенос срока — его разговор с клиентом
                          "kam": _nm(d.get("kam")), "kamId": _nm(d.get("kamId")),
                          "phase": p if in_tile else ""})
    for k in ph:
        ph[k]["n"] = len(ph_deals[k])

    by_deal_orders: dict[str, list] = defaultdict(list)
    for o in orders:
        by_deal_orders[o.get("deal")].append(o)
    for d in deals:
        if d.get("closed"):
            continue
        codes = []
        os_ = by_deal_orders.get(d["deal"], [])
        if os_ and all(o.get("success") for o in os_):
            codes.append(("dealOpen", None))
        if d.get("inCat0", True) and not os_ and (d.get("firstLag") or 0) > 7:
            codes.append(("noOrder", round(d["firstLag"])))
        # ведёт уволенный: ответственный сделки, ОСС или ответственный живого заказа
        ушли = [nm for k, nm in GONE_ROLES if d.get(k)]
        if ушли:
            codes.append(("gone", None))
        w = _nm(d.get("ossDeal")) or _nm(d.get("manager")) or "—"
        for code, days in codes:
            counts[code] += 1
            sev, what, act = EXC[code]
            if code == "gone":
                what = what + ": " + ", ".join(ушли)
            queue.append({"code": code, "sev": sev, "what": what, "act": act, "days": days,
                          "who": w, "whoGone": gone(w), "kam": _nm(d.get("kam")),
                          "kamId": _nm(d.get("kamId")),
                          "deal": d["deal"], "seq": d.get("seq") or 0, "customer": d.get("customer") or "—",
                          "order": "", "title": d.get("title") or "", "sup": "", "dl": "", "phase": ""})
    queue.sort(key=lambda q: (q["sev"], -(q["days"] or 0), -q["seq"]))

    # доставлено в срок: помесячно по месяцу фактической доставки клиенту
    m0 = dt.date(today.year, today.month, 1)
    months = []
    for i in range(OTD_MONTHS - 1, -1, -1):
        y, m = divmod(m0.month - 1 - i, 12)
        months.append(dt.date(m0.year + y, m + 1, 1))
    otd: dict[dt.date, list] = {m: [0, 0, []] for m in months}     # [со сроком, в срок, задержки]
    last90 = [0, 0]
    for o in orders:
        f, dl = o.get("delFact"), o.get("dl")
        if f and f <= today:
            mk = dt.date(f.year, f.month, 1)
            if dl and mk in otd:
                otd[mk][0] += 1
                otd[mk][1] += f <= dl
                if f > dl:
                    otd[mk][2].append((f - dl).days)
            if dl and (today - f).days <= 90:
                last90[0] += 1
                last90[1] += f <= dl
    # «в срок» по сделке: все её заказы доставлены не позже срока (решение владельца
    # 19.10.2026, п. 3); сделка — в окне 90 дней по последней доставке
    по_сделке: dict[str, list] = defaultdict(list)
    for o, d in in_scope:
        по_сделке[o.get("deal")].append(o)
    deals90 = [0, 0]
    for did, os_ in по_сделке.items():
        if not os_ or not all(o.get("delFact") and o.get("dl") for o in os_):
            continue
        last = max(o["delFact"] for o in os_)
        if 0 <= (today - last).days <= 90:
            deals90[0] += 1
            deals90[1] += all(o["delFact"] <= o["dl"] for o in os_)
    otd_rows = [{"m": m.strftime("%m.%Y"), "n": v[0], "ontime": v[1],
                 "pct": round(v[1] / v[0] * 100) if v[0] else None,
                 "medLate": _median(v[2])} for m, v in otd.items()]
    lead_out = {k: {"med": _median(v), "p75": _p75(v), "n": len(v)} for k, v in lead.items()}
    sup_all = {k: {"n": len(v), "ontime": sum(1 for x in v if x <= 0),
                   "pct": round(sum(1 for x in v if x <= 0) / len(v) * 100),
                   "medSlip": _median(v)} for k, v in sup_rel.items()}
    suppliers = [{"sup": k, **v} for k, v in sup_all.items() if v["n"] >= SUP_MIN]
    suppliers.sort(key=lambda x: (x["pct"], -(x["medSlip"] or 0)))

    # хозяева: живые заказы, исключения и доставка в срок за 90 дней
    people: dict[str, dict] = defaultdict(lambda: {"live": 0, "exc": 0, "late": 0, "n90": 0, "ok90": 0})
    for o, d in in_scope:
        w = owner(o, d)
        p = people[w]
        p["live"] += bool(o.get("live"))
        f, dl = o.get("delFact"), o.get("dl")
        if f and dl and 0 <= (today - f).days <= 90:
            p["n90"] += 1
            p["ok90"] += f <= dl
    for q in queue:
        people[q["who"]]["exc"] += 1
        people[q["who"]]["late"] += q["code"] == "late"
    people_rows = sorted(({"who": k, **v, "gone": gone(k),
                           "pct90": round(v["ok90"] / v["n90"] * 100) if v["n90"] else None}
                          for k, v in people.items() if v["live"] or v["exc"]),
                         key=lambda x: (-x["exc"], -x["live"]))

    # гарантия — тот же набор, что плитка фазы «Гарантия»
    in_w = [o for o, _ in in_scope if phase(o, today, warranty_months) == "warranty"]
    warranty = {"months": warranty_months, "assumed": True, "active": len(in_w),
                "ending30": sum(1 for o in in_w
                                if _add_months(o["delFact"], warranty_months) <= today + dt.timedelta(days=30)),
                "claims": None}
    return {
        "asOf": today.strftime("%d.%m.%Y"),
        "phases": [{"key": k, "name": nm, **ph[k]} for k, nm in PHASES],
        "queue": queue,
        "counts": dict(counts),
        "labels": {k: v[1] for k, v in EXC.items()},
        "otd": {"months": otd_rows, "last90": {"n": last90[0], "ontime": last90[1],
                                               "pct": round(last90[1] / last90[0] * 100) if last90[0] else None},
                "deals90": {"n": deals90[0], "ontime": deals90[1],
                            "pct": round(deals90[1] / deals90[0] * 100) if deals90[0] else None}},
        "norms": {"toShip": norm_ship, "path": norm_path},
        "lead": lead_out,
        "suppliers": suppliers[:20],
        "supAll": sup_all,          # по каждому поставщику — для вкладки «Поставщики»
        "people": people_rows[:20],
        "warranty": warranty,
        "rules": {"riskDays": RISK_DAYS, "utdGrace": UTD_GRACE, "payGrace": PAY_GRACE, "supMin": SUP_MIN},
    }
