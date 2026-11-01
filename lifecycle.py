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

Пункт заказа несёт его номер, как в портале (ordLbl «900/1», ordNo — ork.order_label), и срок,
по которому он считан (ref, order_ref: срок клиента, прогноз, план, срок оплаты; у сроков шагов —
«с 14.10 (назначение)», since); пункты одной сделки — подряд и по номеру заказа (group_sort).
Владелец 29.10.2026: «сделка одна — заказы разные 1/2/3 — разные поставщики и сроки». Приёмка
кончается назначением менеджера ОРК, если оно раньше выхода из «Заказ создан» (ork.assign_at):
такой заказ — шаг 4, пункт s2Over от назначения, а не s1Over.

Сделка и заказ — разные единицы (жалоба и решения владельца 30.10.2026, спецификация «сделка и
заказ»): у каждого пункта unit (order — заказ СП-172, deal — сделка) и bucket (exec — дела по
заказам, deal — дела по сделкам, hyg — порядок в карточках Битрикса, вне дневного счёта) по
таблицам UNIT и BUCKET. Приёмка — одна на сделку (s1Over — пункт сделки, заказы строками ords;
заказ, добавленный в принятую сделку, — ordAssign), долг клиента (unpaid) и срок оплаты
(noPayPlan) — один пункт сделки (_fold_deal_codes). Счёты — в своих единицах: ordCounts (разные
заказы), dealCounts (разные сделки), bucketCounts; прежний counts (пункт × код до свёртки) — на
один выпуск. per_order — те же пункты в прежних единицах для счётов, которые меряют заказы.
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
    # у пункта ЗАКАЗА — действие исполнения (менеджер ОРК); разговор с клиентом — один на сделку,
    # в шапке сделки у КАМа (ACT_CLIENT, спецификация «сделка и заказ» E7)
    "late": (1, "срок клиента прошёл, не доставлено", "ускорить поставку, назвать новую дату доставки"),
    "risk": (2, f"до срока клиента ≤ {RISK_DAYS} дн., поставщик не отгрузил", "эскалировать поставщику, проверить готовность"),
    "forecast": (3, "прогноз доставки позже срока клиента", "ускорить сейчас: поставщик, маршрут"),
    # производство: бюджетный конец прошёл, заказ ни разу не входил в «Заказ готов» или дальше
    # (по всем переходам) — отклонение шага 7; плановую отгрузку переписывают под факт (§8)
    "prodDev": (4, "срок конца производства прошёл, заказ не готов", "запросить у поставщика статус и новую дату; эскалировать руководителю ОРК и КАМу (Рег. 9.2.5)"),
    "planAfterDl": (5, "план доставки позже срока клиента", "сменить маршрут или ускорить поставку"),
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
    # уволен ответственный или ОСС ЗАКАЗА — пункт заказа (gone — только люди сделки)
    "goneOrd": (11, "заказ ведёт уволенный", "переназначить заказ"),
    "noDl": (12, "нет срока клиента", "внести срок из договора"),
    "noPlanDates": (13, "не заполнены даты размещения", "внести даты производства и доставки клиенту по бюджету"),
    "noFact": (14, "заказ закрыт без даты доставки клиенту", "внести фактическую дату доставки"),
    # сверх нормы шага регламента (р.д., «без пауз»): срок менеджера, а не поставщика
    # приёмка — одна на сделку (решение владельца 30.10.2026, п. 1): пункт сделки, заказы — в ords
    "s1Over": (4, f"приёмка сделки дольше {ork.NORM_WD[1]} р.д.", "принять сделку и назначить менеджера ОРК"),
    # заказ, добавленный в уже принятую сделку: свой пункт заказа, слова «сделка» в нём нет
    "ordAssign": (4, f"менеджер ОРК на новый заказ не назначен дольше {ork.NORM_WD[1]} р.д.",
                  "назначить менеджера ОРК на новый заказ"),
    "s2Over": (5, f"договор с поставщиком дольше {ork.NORM_WD[4]} р.д.", "подписать договор или зафиксировать, что ждём"),
    "s3Over": (5, f"PO/OC, спецификация и предоплата дольше {ork.NORM_WD[5]} р.д.", "довести PO, OC, спецификацию и предоплату"),
    "s6Over": (5, f"подготовка к отгрузке дольше {ork.NORM_WD[8]} р.д.", "собрать отгрузочные документы и передать логистике"),
    # оплата поставщику без договора с заказчиком (Рег. 2.4, 7.6.4): сделка ни разу не была на
    # стадии «Договор с заказчиком подписан» или дальше, и договора заказчика нет в разборе договоров
    "payNoContract": (3, "оплачено поставщику, договор с заказчиком не подписан по стадии и не найден", "проверить договор заказчика; без него оплату не проводить"),
    # шаг 0 (решение владельца 31.10.2026): задача «Подтверждение готовности передачи…» открыта
    # дольше нормы — хозяин исполнитель задачи, КАМ сделки в копии (ork.ITEM); важность — как у
    # приёмки: заказ не идёт в работу ОРК
    "s0Over": (4, f"передача в ОРК не подтверждена дольше {ork.NORM_WD[ork.PRE_STEP]} р.д.",
               "закрыть задачу подтверждения или назвать, чего не хватает: проверка поставщика, "
               "свежий оффер, смена поставщика"),
    # задача подтверждения открыта, а заказ по стадии или фактам уже дальше шага 0 (решение
    # владельца 31.10.2026, а) — порядок в карточках, из счёта срока не исключает
    "confirmOpen": (12, "задача подтверждения передачи открыта, а заказ уже дальше «Заказ создан»",
                    "закрыть задачу подтверждения или вернуть стадию"),
}
# действие для шапки сделки над строками её заказов (§1.7 спецификации «сделка и заказ»):
# разговор с клиентом — один на сделку, у КАМа; в счёт пунктов не идёт
ACT_CLIENT = {
    "late": "согласовать с клиентом новый срок",
    "risk": "предупредить клиента: срок под угрозой",
    "forecast": "предупредить клиента: доставка позже срока",
    "planAfterDl": "предупредить клиента: план доставки позже срока",
    "noUtd": "попросить клиента подписать УПД",
}
# единица пункта очереди (спецификация «сделка и заказ» §1.4): order — заказ поставщику (СП-172),
# deal — сделка. Пункт сделки, рождённый фактами заказов, несёт их строками ords
UNIT = {
    **{c: "order" for c in ("late", "risk", "forecast", "planAfterDl", "prodDev", "noUtd",
                            "s2Over", "s3Over", "s6Over", "ordAssign", "goneOrd",
                            "noDl", "noPlanDates", "noFact", "s0Over", "confirmOpen")},
    **{c: "deal" for c in ("unpaid", "noPayPlan", "s1Over", "noOrder", "payNoContract",
                           "dealClose", "dealOpen", "gone",
                           # пункты закрытой части (dealctx): договор поставщика с заказом — order
                           "chatReply", "chatRisk", "chatDue", "contractMissing", "contractRisk",
                           "econGap", "contractUnreadable")},
}
# часть очереди (§2): exec — дела по заказам (исполнение поставки), deal — дела по сделкам
# (обязательство перед клиентом), hyg — порядок в карточках Битрикса, ВНЕ дневного счёта
# (решения владельца 30.10.2026, п. 2 и 3: dealClose и noPayPlan — сюда)
BUCKET = {
    **{c: "exec" for c in ("late", "risk", "forecast", "planAfterDl", "prodDev", "noUtd",
                           "s2Over", "s3Over", "s6Over", "ordAssign", "s0Over")},
    **{c: "deal" for c in ("unpaid", "noOrder", "s1Over", "payNoContract",
                           "chatReply", "chatRisk", "chatDue", "contractRisk",
                           "contractMissing:customer", "contractMissing:both")},
    **{c: "hyg" for c in ("dealClose", "dealOpen", "noPayPlan", "gone", "goneOrd",
                          "noPlanDates", "noFact", "noDl", "econGap", "contractUnreadable",
                          "contractMissing", "contractMissing:supplier", "confirmOpen")},
}
# пункт закрытой части с заказом (договор поставщика, сверка договоров с f.order) — дело заказа
BUCKET_ORDER = {"contractMissing": "exec", "contractMissing:supplier": "exec", "contractRisk": "exec"}
# пункты заказов, которые сворачиваются в один пункт сделки (решение 30.10.2026, п. 1; E5, E6):
# долг клиента и срок оплаты — по договору сделки, приёмка — одна на сделку
FOLD_CODES = ("unpaid", "noPayPlan", "s1Over")
# в прежнем счёте counts (оставлен на один выпуск для тестов и KV) ordAssign — это s1Over
LEGACY_CODE = {"ordAssign": "s1Over"}


def unit_bucket(code: str, key: str | None = None, order=None) -> tuple[str, str]:
    """(unit, bucket) пункта по таблицам UNIT и BUCKET. key — строка с уточнением (сторона
    договора: «contractMissing:supplier»); order — заказ пункта: у договора поставщика и сверки
    договоров с заказом пункт — заказа и в делах по заказам. Код не из таблиц — по наличию заказа
    (запасной вывод шаблона: x.unit || (x.order ? 'order' : 'deal')), часть — по единице."""
    base = str(code or "").split(":")[0]
    k = key if key in BUCKET or key in BUCKET_ORDER else code
    if order not in (None, "", 0) and (k in BUCKET_ORDER or base in BUCKET_ORDER):
        return "order", BUCKET_ORDER.get(k) or BUCKET_ORDER[base]
    unit = UNIT.get(code) or UNIT.get(base) or ("order" if order not in (None, "", 0) else "deal")
    bucket = BUCKET.get(k) or BUCKET.get(code) or BUCKET.get(base) or ("exec" if unit == "order" else "deal")
    return unit, bucket


# обязательные даты размещения (решение владельца 19.10.2026, п. 9)
PLAN_DATES = (("prodStart", "начало производства"), ("prodEnd", "конец производства"),
              ("delBudget", "доставка клиенту"))
# начало счёта срока шага (ork.clock) → как его назвать у пункта: по этой подписи день
# проверяется в Битриксе (создание заказа, история стадий, «Дата записи нового
# ответственного», «Customer delivery date, actual»)
SINCE_WORD = {"назначение": "назначение", "вход в шаг": "вход в шаг", "доставка": "доставка",
              "задача подтверждения": "создана задача подтверждения"}
# кто из хозяев сделки уволен — признаки строки сделки contracts.py (справочник людей). Прежний
# счёт counts.gone — сделки с любым из трёх; пункты с 30.10.2026 разведены: gone — люди СДЕЛКИ
# (ответственный, ОСС сделки), goneOrd — ответственный или ОСС ЗАКАЗА (orders[] строки сделки)
GONE_ROLES = (("managerGone", "ответственный сделки"), ("ossGone", "ОСС"),
              ("respGone", "ответственный заказа"))
GONE_ORDER_ROLES = (("respGone", "ответственный заказа"), ("ossGone", "ОСС заказа"))


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


# опора прогноза (forecast_parts → baseKind): факт или план самого заказа — дату можно назвать
# клиенту; «created» — дата создания + медиана «заказ → отгрузка», то есть из одних медиан
FC_BASE = {
    "inbFact": "приход на склад (факт)",
    "shipFact": "отгрузка поставщика (факт)",
    "shipPlan": "плановая отгрузка поставщика",
    "prodEnd": "бюджетный конец производства",
    "created": "дата создания заказа + обычный срок до отгрузки",
}
FC_SOLID = ("inbFact", "shipFact", "shipPlan", "prodEnd")


def forecast_parts(o: dict, today: dt.date, med: dict, sup_slip: dict) -> dict | None:
    """Прогноз доставки клиенту живого заказа по частям (правило 16 — храни, почему получилось
    значение; спецификация R4, Д-1а): от последнего факта (приход на склад, отгрузка) или — не
    отгружен — от позднейшей из дат: плановая отгрузка, бюджетный конец производства + обычное
    опоздание ЭТОГО поставщика, сегодня; плюс медианы оставшихся звеньев. None — не из чего
    считать.

    → {date, base, baseKind, baseRaw, transit, lastMile, supSlip, toShip, pushed, clamped}:
    base — дата, от которой пошли звенья (после «не раньше сегодня»), baseRaw — сама опора;
    baseKind — ключ FC_BASE; transit и lastMile — звенья в целых днях, transit + lastMile =
    прибавленное к base (transit None — звено не нужно: заказ уже на складе); supSlip —
    обычное опоздание поставщика в днях, прибавленное к концу производства (0 — не
    прибавлялось или поставщик в срок); toShip — дни «заказ → отгрузка» у опоры «created»; pushed — опора в прошлом, а факта нет: считаем от сегодня;
    clamped — опора и звенья дали прошедший день, прогноз поднят до сегодня."""
    if not o.get("live") or delivered(o):
        return None
    tr, lm = med.get("transit"), med.get("lastMile")
    slip = 0
    to_ship = None
    pushed = False
    if o.get("inbFact"):
        base, rest, kind = o["inbFact"], lm, "inbFact"
        raw, tr_used = base, None
    elif o.get("shipFact"):
        base, rest, kind = o["shipFact"], (tr + lm if tr is not None and lm is not None else None), "shipFact"
        raw, tr_used = base, tr
    else:
        rest = tr + lm if tr is not None and lm is not None else None
        кандидаты = []
        if o.get("shipPlan"):
            кандидаты.append((o["shipPlan"], "shipPlan", 0.0))
        if o.get("prodEnd"):
            s = max(0.0, sup_slip.get(o.get("sup") or "", 0.0) or 0.0)
            кандидаты.append((o["prodEnd"] + dt.timedelta(days=s), "prodEnd", s))
        if not кандидаты and o.get("created") and med.get("toShip") is not None:
            кандидаты.append((o["created"] + dt.timedelta(days=med["toShip"]), "created", 0.0))
        if not кандидаты:
            return None
        # позднейшая опора; при равных датах — первая по порядку (как max() по датам)
        raw, kind, _ = max(кандидаты, key=lambda c: c[0])
        # в днях, которые реально прибавились к дате (timedelta к date берёт целые сутки)
        if kind == "prodEnd":
            slip = (raw - o["prodEnd"]).days
        elif kind == "created":
            to_ship = (raw - o["created"]).days
        # не отгружен: путь к клиенту не начнётся раньше сегодняшнего дня — иначе
        # прошедшая плановая отгрузка тянула прогноз назад и сигнал молчал там,
        # где он нужнее всего
        base = max(raw, today)
        pushed = base > raw
        tr_used = tr
    if rest is None:
        return None
    end = base + dt.timedelta(days=round(rest))
    # звенья в целых днях так, чтобы сумма сходилась с прибавленным (round(транзит + миля)):
    # миля — остаток, иначе «12 + 3» на странице и 16 дней в дате
    tr_d = round(tr_used) if tr_used is not None else None
    lm_d = round(rest) - (tr_d or 0)
    return {"date": max(today, end), "base": base, "baseKind": kind, "baseRaw": raw,
            "transit": tr_d, "lastMile": lm_d, "supSlip": slip, "toShip": to_ship,
            "pushed": pushed, "clamped": end < today}


def forecast_inputs(measure: list[dict], today: dt.date) -> tuple[dict, dict]:
    """Мерило прогноза: медианы звеньев (дн.) и обычное опоздание поставщиков (от SUP_MIN
    отгрузок) по заказам `measure` — то, что build передаёт в forecast. Одна функция у очереди
    и карточки заказа (contracts), чтобы дата прогноза в них была одна."""
    lead, sup_rel = speeds(measure, today)
    med = {k: _median(v) for k, v in lead.items()}
    sup_slip = {k: _median(v) for k, v in sup_rel.items() if len(v) >= SUP_MIN}
    return med, sup_slip


def forecast(o: dict, today: dt.date, med: dict, sup_slip: dict) -> dt.date | None:
    """Прогноз доставки клиенту живого заказа — дата из forecast_parts (одно правило, части —
    там). None — не из чего считать."""
    p = forecast_parts(o, today, med, sup_slip)
    return p["date"] if p else None


def _dd(d: dt.date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def fc_why(p: dict | None) -> str:
    """Из чего сложен прогноз — словами, для пункта очереди и карточки заказа: «плановая
    отгрузка поставщика 05.11.2026 + транзит 12 дн. + последняя миля 3 дн.». Пусто — прогноза
    нет."""
    if not p:
        return ""
    kind = p["baseKind"]
    if kind == "prodEnd" and p.get("supSlip"):
        head = (f"бюджетный конец производства {_dd(p['baseRaw'] - dt.timedelta(days=p['supSlip']))}"
                f" + обычное опоздание поставщика {p['supSlip']} дн.")
    elif kind == "created":
        head = (f"дата создания заказа {_dd(p['baseRaw'] - dt.timedelta(days=p.get('toShip') or 0))}"
                f" + обычный срок до отгрузки {p.get('toShip') or 0} дн.")
    else:
        head = f"{FC_BASE[kind]} {_dd(p['baseRaw'])}"
    if p.get("pushed"):
        head += f" — прошла, считаем от {_dd(p['base'])}"
    parts = [head]
    if p.get("transit") is not None:
        parts.append(f"транзит {p['transit']} дн.")
    if p.get("lastMile") is not None:
        parts.append(f"последняя миля {p['lastMile']} дн.")
    s = " + ".join(parts)
    if kind not in FC_SOLID:
        s += " — дата из одних медиан"
    if p.get("clamped"):
        s += " — должно было прийти раньше, ставим сегодня"
    return s


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
                     nocal: Counter | None = None, in_cat0: bool = True,
                     accepted: bool = False) -> list[tuple[str, int | None]]:
    """[(код, дней)] — исключения одного заказа. Закрытый провалом заказ сюда не попадает.
    fc — прогноз доставки (forecast): позже срока клиента — ранний сигнал. Дни у
    s1Over…s6Over и noUtd — РАБОЧИЕ (календарь РФ), у прочих — календарные. Года нет в
    календаре — пункта по сроку менеджера нет, счёт — в nocal (ноль за неизвестное не ставим).
    in_cat0 — сделка в воронке «Реализация»: вне её приёмки (s1Over) нет. accepted — сделка уже
    принята (другой её заказ прошёл приёмку, _deal_accept): срок шага 1 этого заказа — не приёмка
    сделки, а «назначить менеджера ОРК на новый заказ» (ordAssign; решение владельца 30.10.2026, п. 1)."""
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
        # сверх нормы шага регламента (ork.OVER_CODE), «ждём УПД от клиента» (ork.WAIT_CODE,
        # шаг 12 — noUtd) и передача в ОРК не подтверждена (ork.PRE_CODE, шаг 0 — s0Over, решение
        # владельца 31.10.2026): ork.judged — то же правило, что у таблицы сделок, ведомости и W2.
        # Шаги 1, 4, 5, 8 — только со стадией из портала; шаг 12 — 10 р.д. от СЫРОЙ даты
        # доставки (delFact), на 11-й р.д. — пункт (Рег. 14.2.5, 14.3.1). Гигиена (стадия позади
        # фактов, брошенная карточка, доставка старше CLOSE_DAYS без УПД) в счёт не идёт
        ov = ork.judged(o, today, in_cat0=in_cat0)
        code = ork.TERM_CODE.get(ov["step"])
        if code == "s1Over" and accepted:
            code = "ordAssign"
        if code and ov["termUnknown"] and ov["why"] == "год без производственного календаря" \
                and nocal is not None:
            nocal[LEGACY_CODE.get(code, code)] += 1
        if code and (ov["over"] or ov["waitUtd"] or ov.get("preOver")):
            out.append((code, ov["wd"]))
        # задача подтверждения передачи открыта, а заказ уже дальше шага 0 (ork.hygiene): порядок
        # в карточке; дни — календарные от создания задачи
        if ork.confirm_open(o) and ork.hygiene(o, today)["confirmOpen"]:
            c0 = _date_of((o.get("confirm") or {}).get("from"))
            out.append(("confirmOpen", (today - c0).days if c0 else None))
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
    """«с 14.10 (назначение)» — день (МСК), от которого считаны дни срока шага (ork.clock), и чем
    он дан; год — только если не текущий. Начало приёмки t0 — «создан заказ» или «вход в
    реализацию» (t0 = позднее из двух), а с закрытия задачи подтверждения передачи (t0Src
    confirm, решение владельца 31.10.2026) — «подтверждена передача»; шаг 0 — «создана задача
    подтверждения». Дня нет — пустая строка."""
    d = _date_of(since)
    if d is None:
        return ""
    s = d.strftime("%d.%m") if d.year == today.year else d.strftime("%d.%m.%Y")
    if by == "начало приёмки" and o.get("t0Src") == "confirm":
        word = "подтверждена передача"
    elif by == "начало приёмки":
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
    if code in ork.TERM_CODES and ov:
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
    """Пункт очереди уровня сделки (без заказа). Шаг — стадии сделки (dealStep), а не ork.ITEM
    (спецификация «сделка и заказ» §4.3: у пункта сделки step — dealStep или None)."""
    return {"code": code, "sev": sev, "what": what, "act": act, "days": days, **own,
            "step": d.get("dealStep"),
            "inWd": code == "noOrder", "ordLbl": "", "ordNo": None, "ref": "",
            "kam": (d.get("kam") or "") if d.get("kam") not in (None, "—") else "",
            "kamId": (d.get("kamId") or "") if d.get("kamId") not in (None, "—") else "",
            "deal": d["deal"], "seq": d.get("seq") or 0, "customer": d.get("customer") or "—",
            "order": "", "title": d.get("title") or "", "sup": "", "dl": "", "phase": ""}


def _deal_accept(os_: list[dict]) -> bool:
    """Сделка принята (решение владельца 30.10.2026, п. 1: приёмка — одна на сделку, у
    руководителя ОРК): хотя бы один её заказ прошёл приёмку — исполнен, ушёл со шага 1 по стадии
    или фактам (ork.step_now, в том числе назначением менеджера ОРК — ork.assign_at), хоть раз
    выходил на шаг 2 и дальше (reachMax) или стоит на стадии не из таблицы шагов (не «Заказ
    создан»). Проигранные заказы не в счёт. Принята — срок шага 1 следующего заказа уже не
    приёмка сделки, а ordAssign «назначить менеджера ОРК на новый заказ»."""
    for o in os_:
        if o.get("success"):
            return True
        if not o.get("live"):
            continue
        if ork.assign_at(o) or (o.get("reachMax") or 0) > 1:
            return True
        st = ork.step_now(o)
        if (st is not None and st > 1) or (st is None and o.get("stage")):
            return True
    return False


# поля пункта заказа, которые строка ords пункта сделки хранит: по ним per_order восстанавливает
# пункты заказов (очередь КАМа, W2, «Сверх нормы по менеджерам», «Клиент не оплатил» — по заказам)
ORD_FIELDS = ("ordNo", "sup", "dl", "due", "days", "who", "ref", "since", "inWd", "step", "role",
              "roleName", "whoGone", "whoId", "escalateTo", "copyRole", "escalateToId", "title", "fc",
              "fcWhy", "byUtd", "phase", "whoOut")


def _n_ord(n: int) -> str:
    m10, m100 = n % 10, n % 100
    w = "заказ" if m10 == 1 and m100 != 11 else ("заказа" if 2 <= m10 <= 4 and not 12 <= m100 <= 14 else "заказов")
    return f"{n} {w}"


def _lbls(rows: list[dict], k: int = 2) -> str:
    """«955/2, 955/3 и ещё 2» — первые k номеров и счёт остальных (§1.6 п. 2)."""
    ls = [r.get("lbl") or "" for r in rows]
    return ", ".join(ls[:k]) + (f" и ещё {len(ls) - k}" if len(ls) > k else "")


def _ord_key(r: dict):
    return (r.get("ordNo") if r.get("ordNo") is not None else 10 ** 6, str(r.get("lbl") or ""))


def _fold_deal_codes(queue: list[dict], deal_by: dict, ord_by: dict, owner_fn,
                     codes=FOLD_CODES) -> list[dict]:
    """Пункты заказов одного кода одной сделки → один пункт сделки (E3, E5, E6; образец —
    kamqueue.build): долг клиента (unpaid) и срок оплаты из договора (noPayPlan) — по договору
    сделки, приёмка (s1Over) — одна на сделку. У пункта unit «deal», заказы — строками ords
    [{id, lbl, sup, dl, due, days, who, revEur, …ORD_FIELDS}] по номеру; дни, срок (ref) и «с
    дд.мм» — по худшему заказу (больше дней), unpaidRev — Σ доли продажи заказов (оценка по
    доле закупки, orders[].revEur строки сделки). Хозяин — роль СДЕЛКИ (owner_fn(code, o, d):
    КАМ, ОСС сделки, руководитель ОРК; ОСС сделки нет — общий ОСС её заказов, если он один).
    Пункты прочих кодов и без заказа — как были."""
    groups: dict[tuple, list] = {}
    out: list = []
    for q in queue:
        if q.get("code") in codes and q.get("order"):
            k = (str(q.get("deal")), q["code"])
            if k not in groups:
                groups[k] = []
                out.append(k)
            groups[k].append(q)
        else:
            out.append(q)
    res = []
    for x in out:
        if not isinstance(x, tuple):
            res.append(x)
            continue
        items = groups[x]
        d = deal_by.get(items[0].get("deal")) or {"deal": items[0].get("deal"), "seq": items[0].get("seq"),
                                                    "customer": items[0].get("customer"),
                                                    "kam": items[0].get("kam"), "kamId": items[0].get("kamId")}
        code = x[1]
        worst = max(items, key=lambda q: (q.get("days") is not None, q.get("days") or 0))
        rev = {str(o.get("id")): o.get("revEur") for o in (d.get("orders") or [])}
        rows = sorted(({"id": q["order"], "lbl": q.get("ordLbl") or "",
                        "revEur": rev.get(str(q["order"])),
                        **{f: q.get(f) for f in ORD_FIELDS if f in q}} for q in items), key=_ord_key)
        # хозяин — роль сделки: ОСС сделки, а без него — общий ОСС её заказов (один на все)
        os_ = [ord_by.get(str(q["order"])) or {} for q in items]
        oss = {str(o.get("oss") or "") for o in os_}
        o_own = None if d.get("ossDeal") not in (None, "", "—") else (
            os_[0] if len(oss) == 1 and "" not in oss and "—" not in oss else None)
        sev, what, act = EXC[code]
        if len(rows) > 1:
            what = f"{what} — {_n_ord(len(rows))}: {_lbls(rows)}"
        it = _deal_item(code, sev, what, act, worst.get("days"), d, owner_fn(code, o_own, d))
        it.update({"inWd": bool(worst.get("inWd")), "ref": worst.get("ref") or "",
                   "since": worst.get("since") or "", "ords": rows,
                   # дата доставки хоть одного заказа — из подписи УПД (пометка «по УПД»)
                   "byUtd": any(r.get("byUtd") for r in rows)})
        if code == "unpaid":
            it["unpaidRev"] = round(sum(r["revEur"] or 0 for r in rows))
        res.append(it)
    return res


def per_order(queue: list[dict]) -> list[dict]:
    """Пункты очереди в прежних единицах «заказ × код» — для счётов, которые по замыслу считают
    заказы (W2, «Сверх нормы по менеджерам», «Клиент не оплатил» в заказах, очередь КАМа: заказы
    пункта сделки строками). Пункт сделки, свёрнутый из заказов (FOLD_CODES, есть ords), — копией
    на каждый заказ с его полями (ORD_FIELDS, order, ordLbl); у копии `_of` — сам пункт сделки
    (пометку w2 копии ставят и ему). Прочие пункты — те же объекты."""
    out: list[dict] = []
    for q in queue:
        rows = q.get("ords") if q.get("code") in FOLD_CODES and q.get("unit") == "deal" else None
        if not rows:
            out.append(q)
            continue
        base = {k: v for k, v in q.items() if k not in ("ords", "unpaidRev")}
        for r in rows:
            out.append({**base, **{f: r[f] for f in ORD_FIELDS if f in r},
                        "order": r["id"], "ordLbl": r.get("lbl") or "", "unit": "order", "_of": q})
    return out


def item_orders(q: dict) -> list[str]:
    """Заказы пункта: у пункта заказа — он сам, у пункта сделки — заказы строк ords."""
    if q.get("unit", "order" if q.get("order") else "deal") == "order":
        return [str(q.get("order"))] if q.get("order") else []
    return [str(r.get("id")) for r in (q.get("ords") or []) if r.get("id")]


def _stamp(queue: list[dict]) -> None:
    """Единица и часть у каждого пункта (unit_bucket), действие шапки сделки (ACT_CLIENT)."""
    for q in queue:
        q["unit"], q["bucket"] = unit_bucket(q["code"], order=q.get("order"))
        if q["code"] in ACT_CLIENT:
            q["actClient"] = ACT_CLIENT[q["code"]]


def queue_counts(queue: list[dict]) -> dict:
    """Счёты очереди в своих единицах (спецификация «сделка и заказ» E2, §4.3): ordCounts — разных
    заказов на код (пункты заказа), dealCounts — разных сделок на код (пункты сделки),
    bucketCounts — по частям: exec {orders, deals, people} — разные заказы, их сделки и хозяева;
    deal {deals, people}; hyg {orders, deals} — заказы пунктов заказа и сделки пунктов сделки.
    Люди — разные хозяева (whoId, иначе who), кроме «не назначен». Заказы и сделки не
    складываются. Единственное определение счёта по частям: dealctx.bucket_counts и
    contracts.life_bucket_counts зовут эту функцию."""
    oc: dict[str, set] = defaultdict(set)
    dc: dict[str, set] = defaultdict(set)
    B: dict[str, dict[str, set]] = {"exec": {"orders": set(), "deals": set(), "people": set()},
                                    "deal": {"deals": set(), "people": set()},
                                    "hyg": {"orders": set(), "deals": set()}}
    for q in queue:
        unit, bucket = q.get("unit"), q.get("bucket")
        if unit is None or bucket is None:
            unit, bucket = unit_bucket(q.get("code"), order=q.get("order"))
        did = str(q.get("deal") or "")
        if unit == "order":
            oc[q["code"]].add(str(q.get("order")))
        else:
            dc[q["code"]].add(did)
        b = B.get(bucket)
        if b is None:
            continue
        if unit == "order" and "orders" in b:
            b["orders"].add(str(q.get("order")))
        if "deals" in b and (unit == "deal" or bucket == "exec"):
            b["deals"].add(did)
        # человек — whoId, иначе имя; «не назначен» человеком не считается (одно определение
        # с dealctx.bucket_counts: тот вызывает эту функцию)
        w = str(q.get("whoId") or "") or (str(q.get("who") or "") if q.get("who") != "не назначен" else "")
        if "people" in b and w:
            b["people"].add(w)
    return {"ordCounts": {k: len(v) for k, v in sorted(oc.items())},
            "dealCounts": {k: len(v) for k, v in sorted(dc.items())},
            "bucketCounts": {k: {f: len(v) for f, v in b.items()} for k, b in B.items()}}


def _life_ord_key(o: dict):
    lbl, no = ((o.get("lbl"), o.get("ordNo")) if o.get("lbl") else ork.order_label(o.get("title"), o.get("id")))
    return (no if no is not None else 10 ** 6, str(lbl or ""))


def _ord_brief(o: dict, **kw) -> dict:
    """Строка заказа у пункта сделки (ords): id, номер «955/2», поставщик, срок клиента и поля kw."""
    lbl, no = ((o["lbl"], o.get("ordNo")) if o.get("lbl") else ork.order_label(o.get("title"), o.get("id")))
    dl = o.get("dl")
    return {"id": o.get("id"), "lbl": lbl, "ordNo": no, "sup": o.get("sup") or "",
            "dl": dl.strftime("%d.%m.%Y") if isinstance(dl, dt.date) else "", **kw}


def build(orders: list[dict], deals: list[dict], today: dt.date,
          warranty_months: int = WARRANTY_MONTHS, *, measure: list[dict] | None = None,
          ork_head: str = "", cust_contract: set | None = None, ork_head_id: str = "") -> dict:
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
        """Люди заказа и сделки для ork.owner. dealOrk — поле сделки «Ответственный за реализацию»
        (руководителя ОРК в нём contracts уже не пишет: решение владельца 31.10.2026); resp —
        ответственный заказа (исполнитель шага, если менеджера ОРК нет: заказ не передан в ОРК);
        confirmResp — исполнитель задачи подтверждения передачи (шаг 0)."""
        o, d = o or {}, d or {}
        return {"orkMgr": _nm(o.get("orkMgr")), "dealOrk": _nm(d.get("orkResp")), "orkHead": _nm(ork_head),
                "kam": _nm(d.get("kam")), "ossOrder": _nm(o.get("oss")), "ossDeal": _nm(d.get("ossDeal")),
                "resp": _nm(o.get("resp")), "confirmResp": _nm(o.get("confirmWho"))}

    def pid(o: dict | None, d: dict | None) -> dict:
        """id Битрикса людей заказа и сделки — те же ключи, что у ppl (R4, Д-11а)."""
        o, d = o or {}, d or {}
        oi, di = o.get("_pid") or {}, d.get("_pid") or {}
        return {"orkMgr": oi.get("orkMgr"), "dealOrk": di.get("dealOrk"), "orkHead": ork_head_id,
                "kam": d.get("kamId") or di.get("kam"), "ossOrder": oi.get("ossOrder"),
                "ossDeal": di.get("ossDeal"), "resp": oi.get("resp"), "confirmResp": oi.get("confirmResp")}

    def item(code: str, o: dict | None, d: dict | None) -> dict:
        """Шаг, роль, исполнитель и копия пункта (ork.ITEM): who — хозяин пункта. whoId и
        escalateToId — id Битрикса тех же людей (отбор «Смотреть как», R4 Д-11а): '' — у
        «не назначен» и у подписей ролей (логистика, руководитель ОРК не из справочника)."""
        pp = ppl(o, d)
        ow = ork.item_owner(code, _step_now(o) if o else None, pp)
        ids = pid(o, d)
        r = {"step": ow["step"], "role": ow["role"], "roleName": ork.ROLES.get(ow["role"], ""),
             "who": ow["who"], "whoGone": gone(ow["who"]),
             "whoId": ork.role_id(ow["role"], pp, ids),
             "escalateTo": ow["copy"], "copyRole": ow["copyRole"],
             "escalateToId": ork.role_id(ow["copyRole"], pp, ids) if ow["copyRole"] else ""}
        # «не назначен» — одно определение на экране (E25): менеджера ОРК нет И ответственного
        # заказа нет (orkview.pulse_who, вид none). Менеджера ОРК нет, а ответственный заказа есть,
        # но не из ОРК, — whoOut, своим счётом: исполнитель шага — он (who, whoId; заказ не передан
        # в ОРК, решение владельца 31.10.2026, заказ 968/2), руководителю ОРК — outAct «принять заказ
        # и назначить менеджера ОРК» (в копии, если у пункта своей копии нет, иначе вторым в копии —
        # alsoCopy: ork.owner). Заказ на шаге 0 (задача подтверждения открыта) — не whoOut: пункт
        # менеджера ОРК у исполнителя задачи, КАМ в копии (ork.owner: pre)
        if ow.get("out"):
            r["whoOut"] = True
            r["outAct"] = ork.OUT_ACT
            if ow.get("alsoCopyRole"):
                r.update(alsoCopy=ow["alsoCopy"], alsoCopyRole=ow["alsoCopyRole"],
                         alsoCopyId=ork.role_id(ow["alsoCopyRole"], pp, ids))
        return r

    def gone(name: str) -> bool:
        return name.endswith("(уволен)")      # contracts.who подписывает ушедших так

    lead, sup_rel = speeds(orders if measure is None else measure, today)
    med, sup_slip = forecast_inputs(orders if measure is None else measure, today)
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
             "s1Over", "s2Over", "s3Over", "s6Over", "s0Over", "confirmOpen"}
    ОПЛАТА = {"unpaid", "noPayPlan"}       # у закрытой выигранной сделки оплата получена
    # заказы сделки и принятые сделки (_deal_accept): срок шага 1 заказа принятой сделки — не
    # приёмка, а ordAssign (решение владельца 30.10.2026, п. 1)
    by_deal_orders: dict[str, list] = defaultdict(list)
    for o in orders:
        by_deal_orders[o.get("deal")].append(o)
    accepted = {k for k, os_ in by_deal_orders.items() if _deal_accept(os_)}
    in_scope = []
    for o in orders:
        d = deal_by.get(o.get("deal"))
        if not d or d.get("lost"):
            continue
        in_scope.append((o, d))
        p = phase(o, today, warranty_months)
        fcp = forecast_parts(o, today, med, sup_slip)
        fc = fcp["date"] if fcp else None
        fcw = fc_why(fcp)
        # приёмка — от входа сделки в «Реализацию» (Рег. 3.3): сделка вне воронки — не приёмка
        exc = order_exceptions(o, today, fc, nocal=nocal, in_cat0=d.get("inCat0", True),
                               accepted=o.get("deal") in accepted)
        if d.get("closed"):
            exc = [(c, n) for c, n in exc if c not in СРОКИ | ОПЛАТА | {"ordAssign"}]
        in_tile = p in ph and (o.get("live") or p == "warranty") and not (d.get("closed") and o.get("live"))
        if in_tile:
            ph[p]["orders"] += 1
            ph_deals[p].add(o.get("deal"))
            ph[p]["late"] += any(c == "late" for c, _ in exc)
            ph[p]["risk"] += any(c in ("risk", "forecast") for c, _ in exc)
            ph[p]["over"] += over_norm(o, p)
        # номер заказа, как его пишет портал («900/1»), — у каждого пункта заказа
        lbl, no = ((o["lbl"], o.get("ordNo")) if o.get("lbl")
                   else ork.order_label(o.get("title"), o.get("id")))
        ov = (ork.judged(o, today, in_cat0=d.get("inCat0", True))
              if any(c in ork.TERM_CODES for c, _ in exc) else None)
        for code, days in exc:
            # прежний счёт «заказ × код» (counts): ordAssign в нём — s1Over, как до 30.10.2026
            counts[LEGACY_CODE.get(code, code)] += 1
            sev, what, act = EXC[code]
            if code == "noPlanDates":
                what = what + ": " + ", ".join(nm for k, nm in PLAN_DATES if not o.get(k))
            queue.append({"code": code, "sev": sev, "what": what, "act": act, "days": days,
                          **item(code, o, d),
                          # р.д. — у сроков менеджера по календарю РФ, «без пауз»
                          "inWd": code in ork.TERM_CODES,
                          # заказ пункта и срок, по которому пункт считан (order_ref): «срок
                          # 01.09.2026», «с 14.10 (назначение)» — проверяется в Битриксе
                          "ordLbl": lbl, "ordNo": no, "ref": order_ref(code, o, today, fc=fc, ov=ov),
                          "since": ((ov or {}).get("since") or "") if code in ork.TERM_CODES else "",
                          # срок оплаты клиента — у строки заказа пункта сделки «не оплатил» (ords)
                          **({"due": _dmy(o.get("payPlan") or (delivered(o) + dt.timedelta(days=PAY_GRACE)))}
                             if code == "unpaid" else {}),
                          "deal": o.get("deal"), "seq": (d or {}).get("seq") or o.get("seq") or 0,
                          "customer": (d or {}).get("customer") or "—", "order": o.get("id"),
                          "title": o.get("title") or "", "sup": o.get("sup") or "—",
                          "dl": o["dl"].strftime("%d.%m.%Y") if o.get("dl") else "",
                          "fc": fc.strftime("%d.%m.%Y") if fc else "",
                          # из чего сложен прогноз (Д-1а): опора и звенья словами
                          "fcWhy": fcw,
                          # КАМ клиента (поле «КАМ») — в копию: оплата и перенос срока — его разговор с клиентом
                          "kam": _nm(d.get("kam")), "kamId": _nm(d.get("kamId")),
                          # дата доставки взята из подписи УПД (delivered) — пометка «по УПД»
                          "byUtd": bool(not o.get("delFact") and o.get("utdDate")),
                          "phase": p if in_tile else ""})
    for k in ph:
        ph[k]["n"] = len(ph_deals[k])

    ord_by = {str(o.get("id")): o for o in orders}
    for d in deals:
        codes = []
        os_ = by_deal_orders.get(d["deal"], [])
        extra: dict[str, dict] = {}             # код → поля пункта сверх _deal_item (note, ords)
        # «Оплата получена», сделка не закрыта — закрыть сделку (решение 26.10.2026, п. 4).
        # Порядок в карточках Битрикса, вне дневного счёта (решение владельца 30.10.2026, п. 2)
        if d.get("paidStage") and not d.get("lost"):
            sd = d.get("stageDays")
            codes.append(("dealClose", round(sd) if isinstance(sd, (int, float)) else None))
            # Рег. 15.6: сделка закрывается после исполнения обязательств — оговорка отдельным
            # полем note (не в what: на экране она пропадала, E12), неисполненные заказы — ords
            note = close_note(os_)
            undone = [o for o in os_ if o.get("live") and (not delivered(o) or not o.get("utd"))]
            extra["dealClose"] = {**({"note": note} if note else {}),
                                  **({"ords": [_ord_brief(o, why="не доставлен" if not delivered(o)
                                                          else "доставлено без УПД") for o in
                                               sorted(undone, key=_life_ord_key)]} if undone else {})}
        if d.get("closed"):
            for code, days in codes:
                counts[code] += 1
                sev, what, act = EXC[code]
                queue.append({**_deal_item(code, sev, what, act, days, d, item(code, None, d)),
                              **extra.get(code, {})})
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
        # заказчика нет в разборе договоров (cust_contract): стадия сделки отстаёт от дела.
        # Какие заказы и каким поставщикам оплачены — строками ords (E11)
        paid = [o for o in os_ if o.get("live") and (o.get("supPaidPct") or 0) > 0]
        if (d.get("dealStepMax") is not None and d["dealStepMax"] < 4
                and str(d["deal"]) not in (cust_contract or set()) and paid):
            codes.append(("payNoContract", None))
            extra["payNoContract"] = {"ords": [_ord_brief(o, supPaidPct=o.get("supPaidPct"))
                                               for o in sorted(paid, key=_life_ord_key)]}
        # ведёт уволенный (E8): gone — люди СДЕЛКИ (ответственный сделки, ОСС сделки); goneOrd —
        # пункт каждого живого заказа, у которого уволен ответственный или ОСС заказа (orders[]
        # строки сделки). Прежний счёт counts.gone — сделки с любым из трёх признаков
        rows_o = [r for r in (d.get("orders") or []) if r.get("live")]
        g_ord = [(r, [nm for k, nm in GONE_ORDER_ROLES if r.get(k)]) for r in rows_o]
        g_ord = [(r, w) for r, w in g_ord if w]
        ушли = []
        if d.get("managerGone"):
            ушли.append("ответственный сделки")
        if d.get("ossGone") and not any(r.get("ossGone") for r in rows_o):
            ушли.append("ОСС сделки")
        if d.get("respGone") and not any(r.get("respGone") for r in rows_o):
            # заказ строки не назван (строка без orders[]) — пункт сделки, как до 30.10.2026
            ушли.append("ответственный заказа (заказ не определён)")
        if any(d.get(k) for k, _ in GONE_ROLES):
            counts["gone"] += 1
        if ушли:
            codes.append(("gone", None))
        for code, days in codes:
            if code != "gone":
                counts[code] += 1
            sev, what, act = EXC[code]
            if code == "gone":
                what = what + ": " + ", ".join(ушли)
            if code == "payNoContract" and cust_contract is None:
                # разбор договоров не загружен: «не найден» не проверялось — так и пишем
                what = "оплачено поставщику, договор с заказчиком не подписан по стадии; разбор договоров не загружен — не проверено"
            queue.append({**_deal_item(code, sev, what, act, days, d, item(code, None, d)),
                          **extra.get(code, {})})
        for r, who_left in g_ord:
            o = ord_by.get(str(r.get("id"))) or {"id": r.get("id"), "deal": d["deal"], "title": r.get("title") or "",
                                                 "lbl": r.get("lbl") or "", "sup": r.get("sup") or ""}
            lbl, no = ((o["lbl"], o.get("ordNo")) if o.get("lbl") else ork.order_label(o.get("title"), o.get("id")))
            sev, what, act = EXC["goneOrd"]
            queue.append({**_deal_item("goneOrd", sev, f"{what}: {', '.join(who_left)}", act, None, d,
                                       item("goneOrd", o, d)),
                          "step": _step_now(o) if o.get("stage") or o.get("delFact") else None,
                          "inWd": False, "ordLbl": lbl, "ordNo": no, "order": o.get("id"),
                          "title": o.get("title") or "", "sup": o.get("sup") or "—",
                          "dl": o["dl"].strftime("%d.%m.%Y") if isinstance(o.get("dl"), dt.date) else ""})
    # пункты заказов одного кода одной сделки → пункт сделки (unpaid, noPayPlan, приёмка s1Over);
    # у каждого пункта — единица и часть очереди (unit, bucket), у «клиентских» — actClient
    queue = _fold_deal_codes(queue, deal_by, ord_by, item)
    _stamp(queue)
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
    d90k: list[str] = []
    d90t: list[str] = []
    for did, os_ in по_сделке.items():
        if not os_ or not all(o.get("delFact") and o.get("dl") for o in os_):
            continue
        last = max(o["delFact"] for o in os_)
        if 0 <= (today - last).days <= 90:
            on = all(o["delFact"] <= o["dl"] for o in os_)
            deals90[0] += 1
            deals90[1] += on
            d90k.append(f"d:{did}")
            d90t.append(f"{'к сроку' if on else 'позже срока'} · заказов {len(os_)} · последняя доставка {last:%d.%m}")
    # Д-2 (R4, 30.10.2026): «доставлено к сроку» со знаменателем «срок наступил» — по месяцу СРОКА
    # клиента, а не доставки. Доля по доставленным (n, ontime выше) прятала сорванные: заказ после
    # срока, ещё не доставленный, в неё не входил, и рядом с «100 % в срок» стояли 19 сорванных
    # сроков. Срок наступил — dl раньше сегодня; доставлен — delivered (факт, без него — дата УПД,
    # как у пункта late): к сроку (≤ dl) или позже; не доставлен — живой заказ открытой сделки
    # (ровно пункт late очереди). Прочие (заказ закрыт без даты доставки, сделка закрыта) — не в
    # знаменателе, счётом dueSkip. Инвариант: due == dueOnTime + dueLateDelivered + dueOpenLate.
    due: dict[dt.date, dict] = {m: {"due": 0, "dueOnTime": 0, "dueLateDelivered": 0, "dueOpenLate": 0,
                                    "dueSkip": 0, "k": [], "t": []} for m in months}
    for o, d in in_scope:
        dl = o.get("dl")
        if not dl or dl >= today:
            continue
        b = due.get(dt.date(dl.year, dl.month, 1))
        if b is None:
            continue
        f = delivered(o)
        if f:
            # дата доставки есть (как у пункта late: с ней заказ не «сорван»); вбитая на будущее —
            # позже срока, раз срок уже прошёл
            on = f <= dl
            b["dueOnTime" if on else "dueLateDelivered"] += 1
            t = (("к сроку" if on else f"позже срока · на {(f - dl).days} дн.")
                 + f" · доставлен {f:%d.%m}{' (дата в будущем)' if f > today else ''} · срок {dl:%d.%m}")
        elif o.get("live") and not d.get("closed"):
            b["dueOpenLate"] += 1
            t = f"не доставлен · срок прошёл {(today - dl).days} дн. назад · срок {dl:%d.%m}"
        else:
            b["dueSkip"] += 1
            continue
        b["due"] += 1
        b["k"].append(str(o.get("id")))
        b["t"].append(t)
    # E44 (30.10.2026, «сделка и заказ»): доставка к сроку в СДЕЛКАХ — сделка в месяце срока своего
    # последнего заказа (наибольший срок клиента среди её заказов со сроком; как Д-2 — срок наступил,
    # раньше сегодня). К сроку — все её заказы со сроком доставлены не позже своего срока (решение
    # 19.10.2026, п. 3, как deals90); иначе позже: доставлен после срока или не доставлен (открытый
    # заказ после срока — open). Заказ закрыт без даты доставки или сделка закрыта без неё — сделка
    # не в знаменателе (skip, как dueSkip). Инвариант: n == ontime + late; open ≤ late.
    # Ключи — «d:<id>», подпись — заказы сделки с номерами
    по_сделке_dl: dict[str, list] = defaultdict(list)
    for o, d in in_scope:
        if o.get("dl"):
            по_сделке_dl[o.get("deal")].append((o, d))
    due_deals: dict[dt.date, dict] = {m: {"n": 0, "ontime": 0, "late": 0, "open": 0, "skip": 0, "orders": 0,
                                          "k": [], "t": []} for m in months}
    for did, od in по_сделке_dl.items():
        last = max(o["dl"] for o, _d in od)
        if last >= today:
            continue
        b = due_deals.get(dt.date(last.year, last.month, 1))
        if b is None:
            continue
        st = []
        for o, d in od:
            f = delivered(o)
            st.append("on" if f and f <= o["dl"] else "late" if f else
                      "open" if o.get("live") and not d.get("closed") else "skip")
        if "skip" in st:
            b["skip"] += 1
            continue
        on = all(x == "on" for x in st)
        b["n"] += 1
        b["orders"] += len(od)
        b["ontime" if on else "late"] += 1
        b["open"] += "open" in st
        lbl = ", ".join((o.get("lbl") or f"id {o.get('id')}") for o, _d in od[:2]) + (
            f" и ещё {len(od) - 2}" if len(od) > 2 else "")
        b["k"].append(f"d:{did}")
        b["t"].append(("к сроку" if on else "не доставлен" if "open" in st else "позже срока")
                      + f" · заказов {len(od)}: {lbl} · последний срок {last:%d.%m}")
    otd_rows = [{"m": m.strftime("%m.%Y"), "n": v[0], "ontime": v[1],
                 "pct": round(v[1] / v[0] * 100) if v[0] else None,
                 "medLate": _median(v[2]),
                 "due": due[m]["due"], "dueOnTime": due[m]["dueOnTime"],
                 "dueLateDelivered": due[m]["dueLateDelivered"], "dueOpenLate": due[m]["dueOpenLate"],
                 "dueSkip": due[m]["dueSkip"],
                 "duePct": round(due[m]["dueOnTime"] / due[m]["due"] * 100) if due[m]["due"] else None,
                 # заказы за долей — ключи и подписи строк списка («Разбор числа»), класс первым
                 "keys": {"unit": "order", "k": due[m]["k"], "t": due[m]["t"]},
                 # сделки, у которых срок последнего заказа наступил в месяце (E44) — главная доля
                 # «Месяца»; заказы — рядом (due); единица — сделка
                 "dueDeals": {"n": due_deals[m]["n"], "ontime": due_deals[m]["ontime"],
                              "late": due_deals[m]["late"], "open": due_deals[m]["open"],
                              "skip": due_deals[m]["skip"], "orders": due_deals[m]["orders"],
                              "pct": round(due_deals[m]["ontime"] / due_deals[m]["n"] * 100)
                              if due_deals[m]["n"] else None, "unit": "deal",
                              "keys": {"unit": "deal", "k": due_deals[m]["k"], "t": due_deals[m]["t"]}}}
                for m, v in otd.items()]
    lead_out = {k: {"med": _median(v), "p75": _p75(v), "n": len(v)} for k, v in lead.items()}
    sup_all = {k: {"n": len(v), "ontime": sum(1 for x in v if x <= 0),
                   "pct": round(sum(1 for x in v if x <= 0) / len(v) * 100),
                   "medSlip": _median(v)} for k, v in sup_rel.items()}
    suppliers = [{"sup": k, **v} for k, v in sup_all.items() if v["n"] >= SUP_MIN]
    suppliers.sort(key=lambda x: (x["pct"], -(x["medSlip"] or 0)))
    # Д-10 (R4): заказы за строкой «Проверка другим способом» — те же отгрузки, что speeds() кладёт
    # в sup_rel (факт отгрузки за 365 дн. и бюджетный конец производства); счёт строки = число ключей
    sup_keys: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    for o in (orders if measure is None else measure):
        s, sp = o.get("shipFact"), o.get("prodEnd")
        if s and sp and (today - s).days <= 365 and o.get("sup"):
            ks, ts = sup_keys[o["sup"]]
            ks.append(str(o.get("id")))
            ts.append(("вовремя" if s <= sp else f"позже · на {(s - sp).days} дн.")
                      + f" · отгружен {s:%d.%m} · бюджет {sp:%d.%m}")
    for x in suppliers:
        ks, ts = sup_keys.get(x["sup"], ([], []))
        if len(ks) == x["n"]:
            x["keys"] = {"k": ks, "t": ts}

    # «хозяева процесса» поимённо (живые заказы, доставка в срок за 90 дней) с 26.10.2026 в
    # страницу не идут: доставка в срок — результат поставщика и логистики, не менеджера (Ан.
    # §4), а нагрузка менеджера поимённо — только под правом руководителей ОРК (решение п. 3)

    # гарантия — тот же набор, что плитка фазы «Гарантия»
    in_w = [o for o, _ in in_scope if phase(o, today, warranty_months) == "warranty"]
    w_end = {str(o.get("id")): _add_months(delivered(o), warranty_months) for o in in_w}
    end30 = [o for o in in_w if w_end[str(o.get("id"))] <= today + dt.timedelta(days=30)]
    warranty = {"months": warranty_months, "assumed": True, "active": len(in_w),
                "ending30": len(end30),
                # E54: «5 заказов (N сделок) на гарантии» — active/ending30 в заказах, deals — сделки
                "deals": len({str(o.get("deal")) for o in in_w}),
                "deals30": len({str(o.get("deal")) for o in end30}),
                "units": {"active": "order", "ending30": "order", "deals": "deal", "deals30": "deal"},
                "claims": None,
                # Д-10 (R4): заказы за числами — «гарантия до ДД.ММ.ГГГГ (допущение N мес.)»
                "keys": {"unit": "order", "k": [str(o.get("id")) for o in in_w],
                         "t": [f"гарантия до {w_end[str(o.get('id'))]:%d.%m.%Y} (допущение {warranty_months} мес.)"
                               for o in in_w]},
                "keys30": {"unit": "order", "k": [str(o.get("id")) for o in end30],
                           "t": [f"гарантия до {w_end[str(o.get('id'))]:%d.%m.%Y} (допущение {warranty_months} мес.)"
                                 for o in end30]}}
    return {
        "asOf": today.strftime("%d.%m.%Y"),
        "phases": [{"key": k, "name": nm, **ph[k]} for k, nm in PHASES],
        "queue": queue,
        # прежний счёт «пункт × код» до свёртки по сделке (на один выпуск — тесты и KV; шаблон
        # его не читает); счёты в своих единицах — ordCounts, dealCounts, bucketCounts (E2)
        "counts": dict(counts),
        **queue_counts(queue),
        "labels": {k: v[1] for k, v in EXC.items()},
        "otd": {"months": otd_rows, "last90": {"n": last90[0], "ontime": last90[1],
                                               "pct": round(last90[1] / last90[0] * 100) if last90[0] else None},
                "deals90": {"n": deals90[0], "ontime": deals90[1],
                            "pct": round(deals90[1] / deals90[0] * 100) if deals90[0] else None,
                            "unit": "deal", "keys": {"unit": "deal", "k": d90k, "t": d90t}},
                # единицы долей «Месяца»: months[].n/ontime/due — заказы; months[].dueDeals — сделки
                "units": {"months": "order", "last90": "order", "deals90": "deal", "dueDeals": "deal"}},
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
