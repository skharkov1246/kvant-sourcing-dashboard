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

Очередь «что делать» — исключения по заказам и сделкам, у каждого шаг регламента ОРК,
хозяин и действие. Хозяин — ИСПОЛНИТЕЛЬ ШАГА по регламенту (решение владельца 26.10.2026,
п. 1; ork.ITEM, ork.item_owner), при эскалации КАМ — в копии (escalateTo). Сроки менеджера
— нормы регламента в рабочих днях по календарю РФ (ork.NORM_WD, period.workdays_ru), «без
пауз»: s1Over, s2Over, s3Over, s6Over и noUtd (≥ 11 р.д. от доставки по сырым датам) — одно
правило с таблицей сделок, ведомостью и W2 (ork.judged: шаг ork.step_now, гигиена, приёмка
только у сделок «Реализации»). noUtd — «ждём УПД от клиента» (ork.judged.waitUtd, решение
владельца 27.10.2026, Б): пункт у менеджера ОРК с эскалацией КАМу, но не «сверх нормы».

Пункт заказа несёт его номер, как в портале (ordLbl «486/1», ordNo — ork.order_label), и срок,
по которому он считан (ref, order_ref: срок клиента, прогноз, план, срок оплаты; у сроков шагов —
«с 22.10 (назначение)», since); пункты одной сделки — подряд и по номеру заказа (group_sort).
Владелец 29.10.2026: «сделка одна — заказы разные 1/2/3 — разные поставщики и сроки». Приёмка
кончается назначением менеджера ОРК, если оно раньше выхода из «Заказ создан» (ork.assign_at):
такой заказ — шаг 4, пункт s2Over от назначения, а не s1Over.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

import ork
import period as period_mod

RISK_DAYS = 14          # до срока клиента меньше двух недель, а поставщик не отгрузил
UTD_WD = ork.NORM_WD[12]   # УПД — 10 р.д. от доставки клиенту; на 11-й — пункт и эскалация КАМу
ORDER_WD = ork.NORM_WD[1]  # приёмка — 1 р.д.: сделка в реализации дольше без заказа — пункт
PAY_GRACE = 30          # без планового срока оплаты: платёж клиента ждём 30 дней после доставки
WARRANTY_MONTHS = 12    # допущение до решения владельца: 12 мес. от доставки клиенту
CLOSE_DAYS = ork.CLOSE_DAYS   # УПД и оплату клиента ждём по доставкам за год, как зонд v53
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
    # производство: бюджетный конец прошёл, заказ ни разу не входил в «Заказ готов» или дальше
    # (по всем переходам) — отклонение шага 7; плановую отгрузку переписывают под факт (§8)
    "prodDev": (4, "срок конца производства прошёл, заказ не готов", "запросить у поставщика статус и новую дату; эскалировать руководителю ОРК и КАМу (Рег. 9.2.5)"),
    "planAfterDl": (5, "план доставки позже срока клиента", "предупредить клиента или сменить маршрут"),
    # «платежа нет» и «УПД нет» — в карточке заказа нет файла: оплату в деньгах портал не
    # видит (решение Р-2), подпись говорит ровно то, что известно
    # долг клиента урегулирует КАМ, ОСС в копии (решение владельца 26.10.2026, п. 4)
    "unpaid": (6, "доставлено, срок оплаты клиента прошёл, платёжки клиента в заказе нет",
               "урегулировать долг: получить у клиента оплату или дату оплаты; пришла — приложить платёжку"),
    # пункт — с 11-го р.д. (Рег. 14.3.1–14.3.2): запросить у ОСС статус УПД и эскалировать КАМу
    "noUtd": (7, f"доставлено {UTD_WD + 1}+ р.д. назад, УПД в заказе нет", "запросить у ОСС статус УПД и эскалировать КАМу; получен — приложить"),
    "noPayPlan": (8, "доставлено, нет планового срока оплаты", "внести срок оплаты из договора"),
    "dealOpen": (9, "все заказы закрыты, сделка открыта", "закрыть сделку"),
    # «Оплата получена», а сделка не закрыта (решение 26.10.2026, п. 4): успех в доставке в
    # срок и деньгах остаётся, но закрыть сделку — работа ОСС (Рег. 15.5)
    "dealClose": (9, "оплата получена, сделка не закрыта", "закрыть сделку"),
    "noOrder": (10, f"в реализации больше {ORDER_WD} р.д. без заказа поставщику", "принять сделку и разместить заказ"),
    "gone": (11, "ведёт уволенный", "переназначить"),
    "noDl": (12, "нет срока клиента", "внести срок из договора"),
    "noPlanDates": (13, "не заполнены даты размещения", "внести даты производства и доставки клиенту по бюджету"),
    "noFact": (14, "заказ закрыт без даты доставки клиенту", "внести фактическую дату доставки"),
    # сверх нормы шага регламента (р.д., «без пауз»): срок менеджера, а не поставщика
    "s1Over": (4, f"приёмка дольше {ork.NORM_WD[1]} р.д.", "принять сделку и назначить менеджера ОРК"),
    "s2Over": (5, f"договор с поставщиком дольше {ork.NORM_WD[4]} р.д.", "подписать договор или зафиксировать, что ждём"),
    "s3Over": (5, f"PO/OC, спецификация и предоплата дольше {ork.NORM_WD[5]} р.д.", "довести PO, OC, спецификацию и предоплату"),
    "s6Over": (5, f"подготовка к отгрузке дольше {ork.NORM_WD[8]} р.д.", "собрать отгрузочные документы и передать логистике"),
    # оплата поставщику без договора с заказчиком (Рег. 2.4, 7.6.4): сделка ни разу не была на
    # стадии «Договор с заказчиком подписан» или дальше, и договора заказчика нет в разборе договоров
    "payNoContract": (3, "оплачено поставщику, договор с заказчиком не подписан по стадии и не найден", "проверить договор заказчика; без него оплату не проводить"),
}
# обязательные даты размещения (решение владельца 19.10.2026, п. 9)
PLAN_DATES = (("prodStart", "начало производства"), ("prodEnd", "конец производства"),
              ("delBudget", "доставка клиенту"))
# начало счёта срока шага (ork.clock) → как его назвать у пункта: по этой подписи день
# проверяется в Битриксе (создание заказа, история стадий, «Дата записи нового
# ответственного», «Customer delivery date, actual»)
SINCE_WORD = {"назначение": "назначение", "вход в шаг": "вход в шаг", "доставка": "доставка"}
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


def delivered(o: dict) -> dt.date | None:
    """Дата доставки клиенту для фазы и очереди: факт («Customer delivery date, actual»), а
    без него — дата подписания УПД (utdDate, только прошедшая): решение владельца 25.10.2026
    — «доставлено, даты доставки нет»: прежде чем ставить пункт, искать УПД и брать дату из
    него с пометкой «по УПД». Доставка в срок, нормы звеньев и хозяева за 90 дней меряются
    только по факту: подпись УПД бывает позже приёмки, и срок по ней выходил бы сорванным."""
    return o.get("delFact") or o.get("utdDate")


def close_note(orders) -> str:
    """Оговорка к «закрыть сделку» (Рег. 15.6 — закрывать после исполнения обязательств):
    живые заказы сделки, не доставленные клиенту (delivered: факт, а без него — дата УПД,
    решение 25.10.2026), и доставленные без подписанного УПД (шаг 12 не закончен, Рег. 14.6).
    Нет таких — пустая строка. Одна оговорка у пункта очереди и ячейки шага сделки."""
    live = [o for o in orders if o.get("live")]
    undel = sum(1 for o in live if not delivered(o))
    noutd = sum(1 for o in live if delivered(o) and not o.get("utd"))
    parts = ([f"живых заказов не доставлено: {undel}"] if undel else []) + (
        [f"доставлено без УПД: {noutd}"] if noutd else [])
    if not parts:
        return ""
    reg = "Рег. 15.6" + (", 14.6" if noutd else "")
    return f"{', '.join(parts)} — закрыть после исполнения ({reg})"


def phase(o: dict, today: dt.date, warranty_months: int = WARRANTY_MONTHS) -> str:
    """Фаза заказа по фактам. o — заказ, приведённый к фактам (поля ниже). Заказ,
    закрытый успехом, закрытие прошёл, даже если УПД или платёжки нет файлом:
    платёжка клиента стала полем только в июле 2025, старые заказы её не имеют. Даты
    доставки нет, а УПД подписан — доставлен по дате УПД (delivered)."""
    f = delivered(o)
    if f:
        if o.get("live") and (not o.get("utd") or not o.get("clientPaid")):
            return "close"
        return "warranty" if _add_months(f, warranty_months) >= today else "done"
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
    if not o.get("live") or delivered(o):
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


# даты заказа, от которых прогноз доставки годится для срока оплаты (pay_eta): без них
# forecast() считает от даты создания и медианы «заказ → отгрузка» — то есть из одних медиан
PAY_ANCHORS = ("inbFact", "shipFact", "shipPlan", "prodEnd")
# источник ожидаемой даты оплаты клиента (pay_eta): код → подпись (правило 16 — почему так)
PAY_SRC = {
    "plan": "плановый срок оплаты заказа",
    "factDays": "доставлено + отсрочка оплаты из сделки",
    "fcDays": "прогноз доставки по медианам звеньев + отсрочка оплаты из сделки",
    "factGrace": f"доставлено + {PAY_GRACE} дн. (отсрочки в сделке нет)",
    "fcGrace": f"прогноз доставки по медианам звеньев + {PAY_GRACE} дн. (отсрочки в сделке нет)",
    "none": "нет ни срока оплаты, ни доставки, ни даты заказа, от которой считать прогноз",
    "noLive": "живых заказов нет",
    "po": "платёжка клиента во всех живых заказах, стадия не «Оплата получена»",
}


def pay_eta(orders: list[dict], pay_days: int | None, today: dt.date, med: dict,
            sup_slip: dict) -> tuple[dt.date | None, str]:
    """Когда клиент заплатит по сделке — по самому позднему из её живых неоплаченных
    заказов: ① плановый срок оплаты заказа → ② доставка (факт, иначе прогноз forecast) +
    отсрочка оплаты сделки → ③ доставка + PAY_GRACE → ④ без даты. Прогноз доставки берётся
    только от даты самого заказа — прихода на склад, отгрузки (факт или план) или конца
    производства; путь от неё до клиента — медианы звеньев, и источник это говорит
    («прогноз по медианам звеньев»). Заказ без единой такой даты прогнозом из одних медиан
    («создан + медиана до отгрузки») срок не получает: дата без опоры — не дата. Хоть один
    живой неоплаченный заказ без даты — у сделки даты нет (последний платёж неизвестен).
    → (дата, код PAY_SRC)."""
    live = [o for o in orders if o.get("live")]
    if not live:
        return None, "noLive"
    due = [o for o in live if not o.get("clientPaid")]
    if not due:
        return None, "po"
    best: tuple[dt.date, str] | None = None
    for o in due:
        if o.get("payPlan"):
            d, src = o["payPlan"], "plan"
        else:
            if not o.get("delFact") and not any(o.get(k) for k in PAY_ANCHORS):
                return None, "none"
            base, kind = (o["delFact"], "fact") if o.get("delFact") else (forecast(o, today, med, sup_slip), "fc")
            if base is None:
                return None, "none"
            if pay_days is not None:
                d, src = base + dt.timedelta(days=pay_days), kind + "Days"
            else:
                d, src = base + dt.timedelta(days=PAY_GRACE), kind + "Grace"
        if best is None or d > best[0]:
            best = (d, src)
    return best if best else (None, "none")


def _step_now(o: dict) -> int | None:
    """Текущий шаг заказа — ork.step_now (одно правило для всей вкладки): по стадии, не ниже
    фактов; доставлено без УПД — шаг 12; стадии нет — по фактам; не из таблицы — None."""
    return ork.step_now(o)


def order_exceptions(o: dict, today: dt.date, fc: dt.date | None = None, *,
                     nocal: Counter | None = None, in_cat0: bool = True) -> list[tuple[str, int | None]]:
    """[(код, дней)] — исключения одного заказа. Закрытый провалом заказ сюда не попадает.
    fc — прогноз доставки (forecast): позже срока клиента — ранний сигнал. Дни у
    s1Over…s6Over и noUtd — РАБОЧИЕ (календарь РФ), у прочих — календарные. Года нет в
    календаре — пункта по сроку менеджера нет, счёт — в nocal (ноль за неизвестное не ставим).
    in_cat0 — сделка в воронке «Реализация»: вне её приёмки (s1Over) нет."""
    out: list[tuple[str, int | None]] = []
    dl, fact = o.get("dl"), delivered(o)                  # без факта — по УПД (решение 25.10.2026)
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
        # производство (шаг 7): бюджетный конец прошёл, а заказ ни разу не выходил на шаг 8
        # «Заказ готов» или дальше — по всем переходам (reachMax), стадии и фактам
        pe, cur = o.get("prodEnd"), _step_now(o)
        hi = max(o.get("reachMax") or 0, cur or 0)
        if pe and pe < today and not shipped and not fact and hi < 8:
            out.append(("prodDev", (today - pe).days))
        if not dl and not fact:
            out.append(("noDl", None))
        if any(not o.get(k) for k, _ in PLAN_DATES):
            out.append(("noPlanDates", None))
        # сверх нормы шага регламента (ork.OVER_CODE) и «ждём УПД от клиента» (ork.WAIT_CODE,
        # шаг 12 — noUtd): ork.judged — то же правило, что у таблицы сделок, ведомости и W2.
        # Шаги 1, 4, 5, 8 — только со стадией из портала; шаг 12 — 10 р.д. от СЫРОЙ даты
        # доставки (delFact), на 11-й р.д. — пункт (Рег. 14.2.5, 14.3.1). Гигиена (стадия позади
        # фактов, брошенная карточка, доставка старше CLOSE_DAYS без УПД) в счёт не идёт
        ov = ork.judged(o, today, in_cat0=in_cat0)
        code = ork.TERM_CODE.get(ov["step"])
        if code and ov["termUnknown"] and ov["why"] == "год без производственного календаря" \
                and nocal is not None:
            nocal[code] += 1
        if code and (ov["over"] or ov["waitUtd"]):
            out.append((code, ov["wd"]))
        # закрытие — только у живого заказа и только за CLOSE_DAYS: закрытый заказ
        # закрытие прошёл, а старые доставки без файла платёжки — пробел учёта до
        # появления поля, а не долг клиента
        if fact and (today - fact).days <= CLOSE_DAYS:
            since = (today - fact).days
            if not o.get("clientPaid"):
                due = o.get("payPlan") or (fact + dt.timedelta(days=PAY_GRACE))
                if due < today:
                    out.append(("unpaid", (today - due).days))
            if not o.get("payPlan"):
                out.append(("noPayPlan", since))
    elif o.get("success") and o.get("closedRecent") and not fact:
        out.append(("noFact", None))
    return out


def _date_of(v) -> dt.date | None:
    """Дата из date, datetime или ISO-строки (вход сделки в реализацию)."""
    if isinstance(v, dt.datetime):
        return (v.astimezone(period_mod.MSK) if v.tzinfo else v).date()
    if isinstance(v, dt.date):
        return v
    try:
        t = dt.datetime.fromisoformat(str(v or ""))
    except ValueError:
        return None
    return (t.astimezone(period_mod.MSK) if t.tzinfo else t).date()


def _dmy(d: dt.date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def since_text(since, by: str, o: dict, today: dt.date) -> str:
    """«с 22.10 (назначение)» — день (МСК), от которого считаны дни срока шага (ork.clock), и чем
    он дан; год — только если не текущий. Начало приёмки t0 — «создан заказ» или «вход в
    реализацию» (t0 = позднее из двух). Дня нет — пустая строка."""
    d = _date_of(since)
    if d is None:
        return ""
    s = d.strftime("%d.%m") if d.year == today.year else d.strftime("%d.%m.%Y")
    if by == "начало приёмки":
        word = "создан заказ" if _date_of(o.get("created")) == d else "вход в реализацию"
    else:
        word = SINCE_WORD.get(by, by)
    return f"с {s} ({word})" if word else f"с {s}"


def order_ref(code: str, o: dict, today: dt.date, *, fc: dt.date | None = None,
              ov: dict | None = None) -> str:
    """Срок заказа, по которому считан пункт (владелец 29.10.2026: «заказы разные — разные
    поставщики и сроки»): срок клиента, прогноз, план доставки, конец производства по бюджету,
    срок оплаты, дата доставки; у сроков шагов регламента (ork.TERM_CODE) — «с дд.мм (…)»,
    день, от которого считаны рабочие дни (ov — ork.judged заказа). Нечего сказать — ""."""
    dl = o.get("dl")
    if code in ("late", "risk"):
        return f"срок {_dmy(dl)}"
    if code == "forecast":
        return f"срок {_dmy(dl)}, прогноз {_dmy(fc)}"
    if code == "planAfterDl":
        return f"срок {_dmy(dl)}, план {_dmy(o.get('delPlan') or o.get('delBudget'))}"
    if code == "prodDev":
        return f"конец пр-ва {_dmy(o.get('prodEnd'))}"
    if code == "unpaid":
        f = delivered(o)
        due = o.get("payPlan") or (f + dt.timedelta(days=PAY_GRACE) if f else None)
        return f"оплата до {_dmy(due)}"
    if code == "noPayPlan":
        return f"доставлено {_dmy(delivered(o))}"
    if code in ork.TERM_CODE.values() and ov:
        return since_text(ov.get("since"), ov.get("sinceBy") or "", o, today)
    return ""


def group_sort(queue: list[dict]) -> None:
    """Порядок очереди: важность → пункты одной сделки подряд (по самому долгому из них) →
    № реализации → по номеру заказа (владелец 29.10.2026: «сделка одна — заказы 1/2/3»)."""
    top: dict = {}
    for q in queue:
        k = (q["sev"], str(q.get("deal")))
        top[k] = max(top.get(k, 0), q.get("days") or 0)
    queue.sort(key=lambda q: (q["sev"], -top[(q["sev"], str(q.get("deal")))], -(q.get("seq") or 0),
                              str(q.get("deal")), q.get("ordNo") if q.get("ordNo") is not None else 10 ** 6,
                              str(q.get("ordLbl") or ""), -(q.get("days") or 0), str(q.get("code"))))


def _deal_item(code: str, sev: int, what: str, act: str, days, d: dict, own: dict) -> dict:
    """Пункт очереди уровня сделки (без заказа)."""
    return {"code": code, "sev": sev, "what": what, "act": act, "days": days, **own,
            "inWd": code == "noOrder", "ordLbl": "", "ordNo": None, "ref": "",
            "kam": (d.get("kam") or "") if d.get("kam") not in (None, "—") else "",
            "kamId": (d.get("kamId") or "") if d.get("kamId") not in (None, "—") else "",
            "deal": d["deal"], "seq": d.get("seq") or 0, "customer": d.get("customer") or "—",
            "order": "", "title": d.get("title") or "", "sup": "", "dl": "", "phase": ""}


def build(orders: list[dict], deals: list[dict], today: dt.date,
          warranty_months: int = WARRANTY_MONTHS, *, measure: list[dict] | None = None,
          ork_head: str = "", cust_contract: set | None = None) -> dict:
    """orders — заказы, приведённые к фактам; deals — строки сделок contracts.py
    (deal, seq, customer, closed, done, norders, cat0At, managerGone, ossGone,
    respGone, manager, oss, ossDeal, orkResp, kam, dealStepMax, paidStage). → раздел
    «life» вкладки «Реализация».

    Хозяин пункта — исполнитель шага (ork.item_owner, решение владельца 26.10.2026, п. 1):
    менеджер ОРК — ответственный заказа из отдела ОРК (orkMgr заказа), иначе поле сделки
    «Ответственный за реализацию» (orkResp), иначе «не назначен»; руководитель ОРК —
    ork_head (руководитель отдела по справочнику), иначе подпись роли; КАМ — поле сделки;
    ОСС — ОСС заказа, иначе сделки; логистика — подпись роли.

    cust_contract — сделки, у которых договор заказчика найден в разборе договоров
    (dealctx.customer_contract_deals): им payNoContract не ставится, как бы ни отстала стадия.

    measure — заказы, по которым МЕРЯЮТСЯ нормы: сроки звеньев (медиана, 75 %), обычное
    опоздание и надёжность поставщиков. None — те же orders. Вид вкладки (основной или
    отдельного направления, решение владельца 25.10.2026) передаёт сюда ВСЕ заказы: норма
    звена и надёжность поставщика — свойства поставщиков и процесса, а не вида; очередь,
    фазы, доставка в срок, хозяева и гарантия считаются по orders и deals вида."""
    deal_by = {d["deal"]: d for d in deals}
    queue: list[dict] = []
    counts: Counter = Counter()
    ph: dict[str, dict] = {k: {"n": 0, "late": 0, "risk": 0, "orders": 0, "over": 0} for k, _ in PHASES}
    ph_deals: dict[str, set] = defaultdict(set)

    def _nm(v) -> str:
        return v if v not in (None, "", "—") else ""

    def ppl(o: dict | None, d: dict | None) -> dict:
        """Люди заказа и сделки для ork.owner."""
        o, d = o or {}, d or {}
        return {"orkMgr": _nm(o.get("orkMgr")), "dealOrk": _nm(d.get("orkResp")), "orkHead": _nm(ork_head),
                "kam": _nm(d.get("kam")), "ossOrder": _nm(o.get("oss")), "ossDeal": _nm(d.get("ossDeal"))}

    def item(code: str, o: dict | None, d: dict | None) -> dict:
        """Шаг, роль, исполнитель и копия пункта (ork.ITEM): who — хозяин пункта."""
        ow = ork.item_owner(code, _step_now(o) if o else None, ppl(o, d))
        return {"step": ow["step"], "role": ow["role"], "roleName": ork.ROLES.get(ow["role"], ""),
                "who": ow["who"], "whoGone": gone(ow["who"]),
                "escalateTo": ow["copy"], "copyRole": ow["copyRole"]}

    def gone(name: str) -> bool:
        return name.endswith("(уволен)")      # contracts.who подписывает ушедших так

    lead, sup_rel = speeds(orders if measure is None else measure, today)
    med = {k: _median(v) for k, v in lead.items()}
    sup_slip = {k: _median(v) for k, v in sup_rel.items() if len(v) >= SUP_MIN}
    # 75-й процентиль звена за год — только справочное число: «сверх нормы» с 26.10.2026 —
    # по нормам регламента в рабочих днях (over_norm, ork.NORM_WD)
    norm_ship = _p75(lead.get("toShip", []))
    _tr, _lm = _p75(lead.get("transit", [])), _p75(lead.get("lastMile", []))
    norm_path = _tr + _lm if _tr is not None and _lm is not None else None

    nocal: Counter = Counter()              # сроки менеджера, которые не посчитать: года нет в календаре

    def over_norm(o: dict, p: str) -> bool:
        """Сверх нормы шага регламента (решение 26.10.2026, п. 2: нормы регламента в р.д.
        вместо 75-го процентиля звена): шаги 1, 4, 5, 8 по стадии; производство и логистику
        регламент не нормирует — их «сверх нормы» нет; шаг 12 — «ждём УПД от клиента», не
        «сверх нормы» (решение 27.10.2026, Б)."""
        if not o.get("live") or not o.get("stage"):
            return False
        ov = ork.judged(o, today, in_cat0=(deal_by.get(o.get("deal")) or {}).get("inCat0", True))
        return bool(ov["over"]) and ov["step"] in ork.OVER_CODE
    # вселенная — та же, что у таблицы сделок: заказы известных сделок, без
    # проигранных; у закрытой (выигранной) сделки сроки заказов уже не отклонения
    СРОКИ = {"late", "risk", "forecast", "prodDev", "planAfterDl", "noDl", "noPlanDates",
             "s1Over", "s2Over", "s3Over", "s6Over"}
    ОПЛАТА = {"unpaid", "noPayPlan"}       # у закрытой выигранной сделки оплата получена
    in_scope = []
    for o in orders:
        d = deal_by.get(o.get("deal"))
        if not d or d.get("lost"):
            continue
        in_scope.append((o, d))
        p = phase(o, today, warranty_months)
        fc = forecast(o, today, med, sup_slip)
        # приёмка — от входа сделки в «Реализацию» (Рег. 3.3): сделка вне воронки — не приёмка
        exc = order_exceptions(o, today, fc, nocal=nocal, in_cat0=d.get("inCat0", True))
        if d.get("closed"):
            exc = [(c, n) for c, n in exc if c not in СРОКИ | ОПЛАТА]
        in_tile = p in ph and (o.get("live") or p == "warranty") and not (d.get("closed") and o.get("live"))
        if in_tile:
            ph[p]["orders"] += 1
            ph_deals[p].add(o.get("deal"))
            ph[p]["late"] += any(c == "late" for c, _ in exc)
            ph[p]["risk"] += any(c in ("risk", "forecast") for c, _ in exc)
            ph[p]["over"] += over_norm(o, p)
        # номер заказа, как его пишет портал («486/1»), — у каждого пункта заказа
        lbl, no = ((o["lbl"], o.get("ordNo")) if o.get("lbl")
                   else ork.order_label(o.get("title"), o.get("id")))
        ov = (ork.judged(o, today, in_cat0=d.get("inCat0", True))
              if any(c in ork.TERM_CODE.values() for c, _ in exc) else None)
        for code, days in exc:
            counts[code] += 1
            sev, what, act = EXC[code]
            if code == "noPlanDates":
                what = what + ": " + ", ".join(nm for k, nm in PLAN_DATES if not o.get(k))
            queue.append({"code": code, "sev": sev, "what": what, "act": act, "days": days,
                          **item(code, o, d),
                          # р.д. — у сроков менеджера по календарю РФ, «без пауз»
                          "inWd": code in ork.TERM_CODE.values(),
                          # заказ пункта и срок, по которому пункт считан (order_ref): «срок
                          # 15.10.2025», «с 22.10 (назначение)» — проверяется в Битриксе
                          "ordLbl": lbl, "ordNo": no, "ref": order_ref(code, o, today, fc=fc, ov=ov),
                          "since": ((ov or {}).get("since") or "") if code in ork.TERM_CODE.values() else "",
                          "deal": o.get("deal"), "seq": (d or {}).get("seq") or o.get("seq") or 0,
                          "customer": (d or {}).get("customer") or "—", "order": o.get("id"),
                          "title": o.get("title") or "", "sup": o.get("sup") or "—",
                          "dl": o["dl"].strftime("%d.%m.%Y") if o.get("dl") else "",
                          "fc": fc.strftime("%d.%m.%Y") if fc else "",
                          # КАМ клиента (поле «КАМ») — в копию: оплата и перенос срока — его разговор с клиентом
                          "kam": _nm(d.get("kam")), "kamId": _nm(d.get("kamId")),
                          # дата доставки взята из подписи УПД (delivered) — пометка «по УПД»
                          "byUtd": bool(not o.get("delFact") and o.get("utdDate")),
                          "phase": p if in_tile else ""})
    for k in ph:
        ph[k]["n"] = len(ph_deals[k])

    by_deal_orders: dict[str, list] = defaultdict(list)
    for o in orders:
        by_deal_orders[o.get("deal")].append(o)
    for d in deals:
        codes = []
        os_ = by_deal_orders.get(d["deal"], [])
        # «Оплата получена», сделка не закрыта — закрыть сделку (решение 26.10.2026, п. 4)
        if d.get("paidStage") and not d.get("lost"):
            sd = d.get("stageDays")
            codes.append(("dealClose", round(sd) if isinstance(sd, (int, float)) else None))
        if d.get("closed"):
            for code, days in codes:
                counts[code] += 1
                sev, what, act = EXC[code]
                # Рег. 15.6: сделка закрывается после исполнения обязательств — живые
                # неисполненные заказы рядом с пунктом, а не молча (пункт остаётся: решение п. 4)
                note = close_note(os_)
                if code == "dealClose" and note:
                    what = f"{what}; {note}"
                queue.append(_deal_item(code, sev, what, act, days, d, item(code, None, d)))
            continue
        if os_ and all(o.get("success") for o in os_):
            codes.append(("dealOpen", None))
        # приёмка — 1 р.д. (Рег. 3.3): сделка в реализации дольше без заказа поставщику
        if d.get("inCat0", True) and not os_ and d.get("cat0At"):
            wd = period_mod.workdays_ru(_date_of(d["cat0At"]), today)
            if wd is None:
                nocal["noOrder"] += 1
            elif wd > ORDER_WD:
                codes.append(("noOrder", wd))
        # оплачено поставщику, а сделка ни разу не была на шаге «договор с заказчиком
        # подписан» (шаг 4) или дальше — по всем переходам сделки (dealStepMax) — и договора
        # заказчика нет в разборе договоров (cust_contract): стадия сделки отстаёт от дела
        if (d.get("dealStepMax") is not None and d["dealStepMax"] < 4
                and str(d["deal"]) not in (cust_contract or set())
                and any(o.get("live") and (o.get("supPaidPct") or 0) > 0 for o in os_)):
            codes.append(("payNoContract", None))
        # ведёт уволенный: ответственный сделки, ОСС или ответственный живого заказа
        ушли = [nm for k, nm in GONE_ROLES if d.get(k)]
        if ушли:
            codes.append(("gone", None))
        for code, days in codes:
            counts[code] += 1
            sev, what, act = EXC[code]
            if code == "gone":
                what = what + ": " + ", ".join(ушли)
            if code == "payNoContract" and cust_contract is None:
                # разбор договоров не загружен: «не найден» не проверялось — так и пишем
                what = "оплачено поставщику, договор с заказчиком не подписан по стадии; разбор договоров не загружен — не проверено"
            queue.append(_deal_item(code, sev, what, act, days, d, item(code, None, d)))
    group_sort(queue)

    # оплата клиента по сделке (доход КАМов и продуктов, kaminc.py): ожидаемая дата и её
    # источник, платёжки клиента файлом — по непроигранным заказам. В страницу не идёт:
    # main.py забирает раздел после расчёта дохода
    pay: dict[str, dict] = {}
    for d in deals:
        os_ = [o for o in by_deal_orders.get(d["deal"], []) if o.get("live") or o.get("success")]
        eta, src = pay_eta(os_, d.get("payDays"), today, med, sup_slip)
        pay[d["deal"]] = {"eta": eta.isoformat() if eta else "", "src": src,
                          "orders": len(os_), "po": sum(1 for o in os_ if o.get("clientPaid")),
                          "delivered": any(o.get("delFact") for o in os_ if o.get("live"))}

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

    # «хозяева процесса» поимённо (живые заказы, доставка в срок за 90 дней) с 26.10.2026 в
    # страницу не идут: доставка в срок — результат поставщика и логистики, не менеджера (Ан.
    # §4), а нагрузка менеджера поимённо — только под правом руководителей ОРК (решение п. 3)

    # гарантия — тот же набор, что плитка фазы «Гарантия»
    in_w = [o for o, _ in in_scope if phase(o, today, warranty_months) == "warranty"]
    warranty = {"months": warranty_months, "assumed": True, "active": len(in_w),
                "ending30": sum(1 for o in in_w
                                if _add_months(delivered(o), warranty_months) <= today + dt.timedelta(days=30)),
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
        # заказов вида, у которых даты доставки нет и она взята из подписи УПД (delivered)
        "byUtd": sum(1 for o, _ in in_scope if not o.get("delFact") and o.get("utdDate")),
        "lead": lead_out,
        "suppliers": suppliers[:20],
        "supAll": sup_all,          # по каждому поставщику — для вкладки «Поставщики»
        "warranty": warranty,
        "rules": {"riskDays": RISK_DAYS, "utdWd": UTD_WD, "orderWd": ORDER_WD, "payGrace": PAY_GRACE,
                  "supMin": SUP_MIN, "norms": dict(ork.NORM_WD), "noPause": ork.NO_PAUSE,
                  "ownerRule": ork.OWNER_RULE},
        # сроки менеджера, которые не посчитать: года нет в производственном календаре
        "noCalendar": dict(nocal),
        "pay": pay,
    }
