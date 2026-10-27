"""Реализация по регламенту ОРК: 13 шагов, нормы в рабочих днях, сегменты S1–S10, ворота доверия.

Чистые функции, портал не читают. Основа — регламент работы отдела реализации контрактов
(13 шагов, исполнители, стадии, нормы, эскалации) и «Система аналитики ОРК» (сегменты
S1–S10, контракт показателя). Решения владельца 26.10.2026 (docs/agents/DECISIONS.md):

  1. хозяин пункта очереди — ИСПОЛНИТЕЛЬ ШАГА по регламенту (OWNER_RULE = 'step');
     при эскалации КАМ — в копии (escalateTo). Прежнее правило 19.10 «хозяин —
     ответственный заказа» заменено;
  2. нормы — регламент в РАБОЧИХ днях по производственному календарю РФ
     (period.workdays_ru): S1 приёмка 1, S2 договор с поставщиком 2, шаг 5 (три стадии
     заказа) ≤ 9, S6 подготовка к отгрузке 2, УПД 10 р.д. от доставки (на 11-й —
     эскалация КАМу); S5 производство и S7 логистика не нормируются — медиана и P80.
     Паузы в Битриксе не ведутся — у каждого срока менеджера подпись «без пауз»;
  4. «Оплата получена», а сделка не закрыта — пункт dealClose (ОСС); долг клиента —
     хозяин КАМ, ОСС в копии.

Решение владельца 27.10.2026, Б: УПД дольше 10 р.д. от доставки — «ждём УПД от клиента»
(judged.waitUtd, WAIT_CODE), а не «сверх нормы» менеджера ОРК (OVER_CODE — шаги 1, 4, 5, 8).

Пороги доверия (GATES) — предложение, ждут решения владельца.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

import period as period_mod

# --- роли (исполнители по регламенту, п. 1.6) -------------------------------------------
ROLES = {
    "orkHead": "руководитель ОРК",
    "orkMgr": "менеджер ОРК",
    "kam": "КАМ",
    "oss": "ОСС",
    "log": "логистика",
    "sup": "поставщик",
}
# смежники ОРК: пункт, где мяч у них, — «ждёт смежника», а не «сверх нормы» менеджера
ADJACENT = ("kam", "oss", "log", "sup")
OWNER_RULE = "step"          # хозяин пункта — исполнитель шага (решение владельца 26.10.2026, п. 1)
NO_PAUSE = "без пауз"        # паузы срока менеджера в Битриксе не ведутся (W4 — нет данных)

# --- 13 шагов регламента -----------------------------------------------------------------
# no, name, role — исполнитель; also — кто ещё участвует; norm — норма шага в рабочих днях,
# которую портал меряет (None — не меряет); seg — сегмент «Системы аналитики», который меряет
# шаг; waits — смежник, от которого шаг зависит; esc — кому эскалирует исполнитель (в копии
# пункта очереди). Норма по документу — NORM_DOC: регламент её задаёт, но портал её не
# меряет («нет данных», шаг 6) — это не «не нормируется».
STEPS = (
    {"no": 1, "name": "Приёмка сделки", "role": "orkHead", "also": (), "norm": 1, "seg": "S1",
     "waits": "kam", "esc": "kam"},
    {"no": 2, "name": "Подготовка и согласование договора с заказчиком", "role": "kam",
     "also": ("oss",), "norm": None, "seg": None, "waits": "kam", "esc": None},
    {"no": 3, "name": "Подписание договора с заказчиком", "role": "kam", "also": (), "norm": None,
     "seg": None, "waits": "kam", "esc": None},
    {"no": 4, "name": "Подготовка договора с поставщиком", "role": "orkMgr", "also": ("oss",),
     "norm": 2, "seg": "S2", "waits": None, "esc": "orkHead"},
    {"no": 5, "name": "Подписание договора, PO/OC, спецификация и предоплата", "role": "orkMgr",
     "also": ("oss",), "norm": 9, "seg": "S3", "waits": None, "esc": "orkHead"},
    {"no": 6, "name": "Запуск производства", "role": "orkMgr", "also": ("oss",), "norm": None,
     "seg": "S4", "waits": "oss", "esc": "orkHead"},
    {"no": 7, "name": "Контроль производства", "role": "orkMgr", "also": ("sup",), "norm": None,
     "seg": "S5", "waits": "sup", "esc": "kam"},
    {"no": 8, "name": "Подготовка к отгрузке от поставщика", "role": "orkMgr", "also": (),
     "norm": 2, "seg": "S6", "waits": None, "esc": "orkHead"},
    {"no": 9, "name": "Передача груза отделу логистики", "role": "orkMgr", "also": ("log",),
     "norm": None, "seg": "S7", "waits": "log", "esc": None},
    {"no": 10, "name": "Согласование доставки заказчику", "role": "kam", "also": ("log",),
     "norm": None, "seg": "S8", "waits": "kam", "esc": None},
    {"no": 11, "name": "Получение транспортной накладной", "role": "log", "also": (), "norm": None,
     "seg": "S8", "waits": "log", "esc": None},
    {"no": 12, "name": "Получение УПД", "role": "orkMgr", "also": ("oss",), "norm": 10, "seg": "S9",
     "waits": "kam", "esc": "kam"},
    {"no": 13, "name": "Контроль закрытия", "role": "oss", "also": ("kam",), "norm": None,
     "seg": "S10", "waits": "kam", "esc": "kam"},
)
# норма по регламенту (правило 4 — таблица): шаг → (подпись нормы, состояние, чего нет).
# norm — портал меряет; nodata — регламент задаёт норму, а данных нет (Рег. 8.2.6, Ан. S4:
# шаг 6 — 1–2 р.д. после OC и оплаты; даты OC (4 %) и оплаты поставщику в заказе нет); none —
# регламент или решение владельца нормы не задают (2–3, 7, 9–11, 13: медиана и P80 или шаг
# смежника)
NORM_DOC = {
    1: ("1 р.д.", "norm", ""), 2: ("", "none", ""), 3: ("", "none", ""), 4: ("2 р.д.", "norm", ""),
    5: ("≤ 9 р.д.", "norm", ""),
    6: ("1–2 р.д.", "nodata", "даты Order confirmation и оплаты поставщику в заказе"),
    7: ("", "none", ""), 8: ("2 р.д.", "norm", ""), 9: ("", "none", ""), 10: ("", "none", ""),
    11: ("", "none", ""), 12: ("10 р.д.", "norm", ""), 13: ("", "none", ""),
}
STEP = {s["no"]: s for s in STEPS}
NORM_WD = {s["no"]: s["norm"] for s in STEPS if s["norm"] is not None}
# исполнитель шага (решение 26.10.2026, п. 1): шаг → роль хозяина пункта очереди
EXECUTOR = {s["no"]: s["role"] for s in STEPS}

# --- стадия → шаг (правило 4: таблица, а не ветвления) ------------------------------------
# Стадии заказа СП-172 (воронка 26, зонд v53) и сделки кат. 0 (зонд v49). Имена
# сравниваются после схлопывания пробелов, casefold и замены латиницы-двойника на кириллицу
# («PO» и «РО»); у сделки — ещё и по части до «|». Неизвестная стадия — None и счёт.
STAGE_STEP = {
    "order": {
        "Заказ создан": 1,
        "Заказ направлен": 4,                       # шаги 2–4 идут параллельно; у заказа — 4
        "Сформирован РО/Подготовка описаний": 5,
        "Получен инвойс / подготовка описаний": 5,  # прежнее имя (регламент, подпись флажка)
        "Подготовка спецификации и инвойса": 5,
        "Согласование спецификации и оплаты": 5,
        "Оплата поставщику / агенту": 6,
        "Производство заказа": 7,
        "Заказ готов": 8,
        "Подготовка к отгрузке": 9,
        "Отгрузка": 9,
        "Таможня": 9,
        "Доставка до склада": 9,
        "Приемка на складе": 9,
        "Подготовка груза/Поиск транспорта": 10,
        "Заказ отгружен": 10,
        "Заказ получен клиентом": 11,
        "Ожидание оплаты": 12,                      # с подписанным УПД — 13 (fact_floor)
        "Закрытие заказа": 13,
    },
    "deal": {
        # у сделки шаг 1 по стадии не отличить от шага 2: приёмку меряют по заказу
        # («Заказ создан») и по сделке без заказа
        "ТКП согласован (конкурс выигран) | Согласование договора/спецификации с заказчиком": 2,
        "Договор с заказчиком подписан | Согласование спецификаций/инвойсов с агентами/поставщиками": 4,
        "Бюджет сделки согласован | Оплата поставщикам": 5,
        "Бюджет согласован | Оплата поставщикам": 5,          # имя по регламенту, п. 7.1
        "Оплата товара произведена | Начато производство": 6,
        "Начато производство": 6,                             # имя по регламенту, п. 8.1
        "Производство завершено | Подготовка к отгрузке": 8,
        "Товар отгружен | Доставка клиенту": 9,
        "Доставка клиенту произведена | Ожидание оплаты": 11,
        "Оплата получена | Закрытие сделки": 13,
    },
}
# стадия, которая покрывает несколько шагов: (шаг по стадии, наибольший шаг стадии).
# «Ожидание оплаты» — шаг 12 без УПД и шаг 13 с УПД (живой заказ на шаге 13 только в ней:
# «Закрытие заказа» — успех); УПД подписан — стадия не «позади фактов»
STAGE_SPAN = {"order": {"Ожидание оплаты": 13}}
# шаг стадии сделки → шаги заказа (наименьший, наибольший), которые регламент допускает при
# ней (Рег. 1.7: одна стадия — несколько операций). Стадия сделки грубее заказа: по таблицам
# стадий шагов (п. 3.1–15.1) и параллельной работе до договора с заказчиком (п. 2.2–2.3, 6.2:
# договор с поставщиком и спецификация — шаги 4–5 — без ожидания договора с заказчиком;
# оплата без него — отдельный пункт payNoContract). «Бюджет согласован | Оплата поставщикам»
# — и сама оплата: заказ в «Оплата поставщику / агенту» (шаг 6) при ней не отстаёт.
# Сделка «позади заказов» — наибольший шаг её стадии ниже наименьшего шага живых заказов;
# «впереди» — наименьший выше наибольшего. Одно правило у вкладки (orkview) и зонда v60
DEAL_SPAN = {
    2: (1, 5),      # «ТКП согласован»: шаги 1–2 (п. 3.1, 4.1) и параллельно 4–5 (п. 2.2–2.3)
    4: (3, 5),      # «Договор с заказчиком подписан»: шаги 3–5 (п. 5.1, 6.1, 7.1)
    5: (5, 6),      # «Бюджет согласован | Оплата поставщикам»: шаг 5 (п. 7.1) и оплата (п. 8.1)
    6: (6, 7),      # «Начато производство»: шаг 6, заказ → «Производство заказа» (п. 8.1)
    8: (7, 8),      # «Производство завершено | Подготовка к отгрузке»: шаги 7–8 (п. 9.1, 10.1)
    9: (9, 10),     # «Товар отгружен | Доставка клиенту»: шаги 9–10 (п. 11.1, 12.1)
    11: (11, 13),   # «Доставка клиенту произведена | Ожидание оплаты»: шаги 11–12 (п. 13.1, 14.1)
                    # и шаг 13 — УПД подписан, ждём оплату клиента: «Оплата получена» —
                    # только после денег (Рег. 15.5; решения 19.10, 25.10 K1)
    13: (13, 13),   # «Оплата получена | Закрытие сделки»: шаг 13 (п. 15.1)
}


def deal_behind(deal_step: int | None, order_steps) -> bool:
    """Стадия сделки позади всех живых заказов (DEAL_SPAN): наибольший допустимый шаг стадии
    сделки ниже наименьшего шага заказов. Шага сделки или заказов нет — не нарушение."""
    st = [s for s in order_steps if s is not None]
    span = DEAL_SPAN.get(deal_step) if deal_step is not None else None
    return bool(span and st and span[1] < min(st))


# нижняя граница шага по фактам: (поля, хоть одно заполнено → шаг не ниже)
FACT_FLOOR = (
    (("supPaidPct", "prodConf"), 6),           # оплата поставщику или подтверждение производства
    (("shipFact", "inbFact"), 9),              # отгрузка поставщика или приход на склад
    (("delFact",), 11),                        # доставлено клиенту
    (("utd",), 13),                            # УПД подписан (сырые UTD_DATE / файл) — шаг 12 пройден
)

# --- сегменты S1–S10 «Системы аналитики» ----------------------------------------------------
# начало и конец — первый выход заказа на шаг не ниже указанного (по всем переходам);
# "t0" — max(создание заказа, вход сделки в кат. 0); "delFact"/"utd" — даты фактов.
SEGMENTS = (
    ("S1", "Приёмка", "t0", 2, "orkHead"),
    ("S2", "Договор с поставщиком", 4, 5, "orkMgr"),
    ("S3", "Оформление заказа", 5, 6, "orkMgr"),
    ("S4", "Запуск производства", 6, 7, "orkMgr"),
    ("S5", "Производство", 7, 8, "sup"),
    ("S6", "Подготовка к отгрузке", 8, 9, "orkMgr"),
    ("S7", "Логистика до склада", 9, 10, "log"),
    ("S8", "Доставка заказчику", 10, 11, "kam"),
    ("S9", "Получение УПД", "delFact", "utdRaw", "orkMgr"),
    ("S10", "Оплата заказчика", "utdRaw", 13, "oss"),
)
SEG_NORM = {"S1": 1, "S2": 2, "S3": 9, "S6": 2, "S9": 10}   # р.д.; прочие — медиана и P80
BURST = {"n": 3, "sec": 300}           # «пачка»: 3 и больше переходов за 5 минут

# --- артефакты шагов (поля карточки заказа) ------------------------------------------------
# key — признак в заказе contracts.life_orders; need — с какого шага поле уже обязано быть
# (заказ прошёл шаг поля); since — дата создания поля по его коду: заказ, прошедший шаг
# раньше, поле не обязан; cond — требуется только при условии (импорт — после сверки
# «импорт/РФ»; prepay — при доле первичной оплаты > 0; paid — оплата поставщику была;
# closed — заказ закрыт успехом).
ARTIFACTS = (
    {"key": "dl", "name": "срок клиента", "field": "ufCrm20_1728900218435", "step": 1, "need": 2,
     "since": "2024-10-14", "cond": None},
    {"key": "po", "name": "Purchase order", "field": "ufCrm20_1723385385957", "step": 5, "need": 6,
     "since": "2024-08-11", "cond": None},
    {"key": "specs", "name": "спецификации и инвойсы", "field": "ufCrm20_1723236635", "step": 5,
     "need": 6, "since": "2024-08-09", "cond": None},
    {"key": "oc", "name": "Order confirmation", "field": "ufCrm20_1787562161354", "step": 5,
     "need": 6, "since": "2026-08-24", "cond": "import"},
    {"key": "proforma", "name": "проформа / счёт", "field": "ufCrm20_1787562201347", "step": 5,
     "need": 6, "since": "2026-08-24", "cond": "prepay"},
    {"key": "supPayDoc", "name": "платёжка поставщику", "field": "ufCrm20_1731178548", "step": 6,
     "need": 7, "since": "2024-11-09", "cond": "paid"},
    {"key": "prodConf", "name": "подтверждение производства", "field": "ufCrm20_1723236426", "step": 6,
     "need": 7, "since": "2024-08-09", "cond": None},
    {"key": "planDates", "name": "плановые даты производства", "field": "ufCrm20_1724941935", "step": 6,
     "need": 7, "since": "2024-08-29", "cond": None},
    {"key": "packing", "name": "Packing list", "field": "ufCrm20_1723236691", "step": 8, "need": 9,
     "since": "2024-08-09", "cond": None},
    {"key": "photo", "name": "фото груза", "field": "ufCrm20_1723630327", "step": 8, "need": 9,
     "since": "2024-08-14", "cond": None},
    {"key": "docsLog", "name": "Documents for logistics", "field": "ufCrm20_1723236676", "step": 8,
     "need": 9, "since": "2024-08-09", "cond": None},
    {"key": "pickup", "name": "адрес и контакт забора", "field": "ufCrm20_1725544786", "step": 9,
     "need": 10, "since": "2024-09-05", "cond": None},
    {"key": "delFact", "name": "факт доставки клиенту", "field": "ufCrm20_1723236501", "step": 11,
     "need": 12, "since": "2024-08-09", "cond": None},
    {"key": "utd", "name": "подписанный УПД", "field": "ufCrm20_1723236848", "step": 12, "need": 13,
     "since": "2024-08-09", "cond": None},
    # платёжка клиента (шаг 13, Рег. 15): требуется у закрытого успехом заказа — живой на шаге
    # 13 оплату ещё ждёт
    {"key": "clientPo", "name": "платёжка клиента", "field": "ufCrm20_1751555578101", "step": 13,
     "need": 13, "since": "2025-07-03", "cond": "closed"},
)

# --- ворота доверия (контракт показателя) — до решения владельца ---------------------------
GATES = {
    "cover_show": 0.80,          # покрытие ≥ 80 % — показываем
    "cover_prelim": 0.50,        # 50–80 % — предварительно; ниже — не показываем
    "agree_min": 0.90,           # совпадение двух опор ≥ 90 % …
    "agree_wd": 1,               # … в пределах 1 р.д.
    "burst_max": 0.10,           # «пачек» ≤ 10 %, иначе не показываем
    "n_min": 30,                 # общий показатель
    "n_manager_month": 10,       # менеджер за месяц
    "n_supplier_quarter": 5,     # поставщик за квартал
    "n_cell": 8,                 # ячейка «категория × страна × тип»
    "plan_eq_fact_max": 0.30,    # «план = факт» > 30 % — первичный срок поставщика не показываем
    "abandoned_wd": 60,          # > 60 р.д. на шаге с нормой ≤ 2 — «брошенная карточка»
    "abandoned_norm_max": 2,
    "agree_pairs_min": 10,       # совпадение опор меряется от 10 пар; меньше — ворота не применяются
    "n_count": 1,                # счёт живых пунктов «сейчас» — не выборка: n-ворот у него нет
    "n_managers_min": 3,         # M3 без права: среднее меньше чем по трём менеджерам — не показываем
}
GATES_NOTE = "пороги — предложение, ждут решения владельца"
STATES = ("show", "prelim", "nodata", "hidden")
CLOSE_DAYS = 365             # УПД и оплату клиента ждём по доставкам за год (как зонд v53); старше — гигиена
CHECKPOINT = {"half": 0.5, "before_days": 14, "long_days": 30}   # контрольные точки шага 7

# латиница-двойник → кириллица: «Сформирован PO» и «Сформирован РО» — одна стадия
_LOOKALIKE = str.maketrans("aceopxykmthb", "асеорхукмтнв")


def norm_name(name) -> str:
    """Имя стадии для сравнения: пробелы схлопнуты, casefold, двойники латиницы — кириллицей,
    пробелы вокруг «/» и «|» выровнены."""
    s = re.sub(r"\s+", " ", str(name or "")).strip().casefold().translate(_LOOKALIKE)
    s = re.sub(r"\s*/\s*", "/", s)
    return re.sub(r"\s*\|\s*", " | ", s)


_STAGE_IDX = {kind: {norm_name(k): v for k, v in m.items()} for kind, m in STAGE_STEP.items()}
_STAGE_HEAD = {kind: {norm_name(k).split(" | ")[0]: v for k, v in m.items()}
               for kind, m in STAGE_STEP.items()}
_STAGE_SPAN = {kind: {norm_name(k): v for k, v in m.items()} for kind, m in STAGE_SPAN.items()}


def stage_step(stage_name, kind: str = "order") -> int | None:
    """Шаг по одному имени стадии, без фактов; неизвестная стадия — None."""
    n = norm_name(stage_name)
    if not n:
        return None
    hit = _STAGE_IDX[kind].get(n)
    if hit is None and kind == "deal":
        hit = _STAGE_HEAD[kind].get(n.split(" | ")[0])
    return hit


def fact_floor(facts: dict | None) -> int:
    """Нижняя граница шага по фактам заказа (FACT_FLOOR); фактов нет — 1."""
    floor = 1
    for keys, step in FACT_FLOOR:
        if facts and any(facts.get(k) for k in keys):
            floor = max(floor, step)
    return floor


def step_of(stage_name, facts: dict | None = None, *, kind: str = "order",
            unknown: Counter | None = None) -> int | None:
    """Шаг регламента: по стадии (STAGE_STEP), но не ниже фактов (fact_floor). Неизвестная
    стадия — None, имя считается в `unknown` (если передан): по стадии шаг не угадываем."""
    base = stage_step(stage_name, kind)
    if base is None:
        if unknown is not None:
            unknown[norm_name(stage_name) or "—"] += 1
        return None
    return max(base, fact_floor(facts))


def step_now(order: dict, *, unknown: Counter | None = None) -> int | None:
    """Шаг живого заказа — ОДИН для ведомости, W2, очереди, таблицы сделок, карточки заказа,
    «Замедлено» и хозяина пункта: по стадии и фактам (step_of; стадии нет — по фактам).
    Доставлено клиенту, УПД нет — шаг 12 (УПД, менеджер ОРК; срок — от факта доставки,
    Рег. 14.2.5), даже если стадия — «Заказ получен клиентом» (шаг 11) или ниже, и даже
    если стадии нет в таблице шагов: срок шага 12 стадии не требует. Стадия не из таблицы и
    доставки нет — None (имя — в `unknown`)."""
    if order.get("stage"):
        st = step_of(order.get("stage"), order)
    else:
        st = fact_floor(order)
    if (st is None or st == 11) and order.get("delFact") and not order.get("utd"):
        return 12
    # имя стадии — в счёт «не из таблицы», только если заказ так и остался без шага
    # (доставленный без УПД со стадией не из таблицы — шаг 12, он не «неизвестен»)
    if st is None and unknown is not None:
        unknown[norm_name(order.get("stage")) or "—"] += 1
    return st


# --- сегменты по полной истории переходов --------------------------------------------------
def _ts(v) -> dt.datetime | None:
    """Время перехода Битрикса (ISO с поясом) → datetime с поясом (без пояса — МСК)."""
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=period_mod.MSK)
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day, tzinfo=period_mod.MSK)
    s = str(v or "").strip()
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=period_mod.MSK)


def bursts(times: list) -> set[int]:
    """Номера переходов, входящих в «пачку»: BURST['n'] и больше переходов за BURST['sec'] с.
    Пачка — признак «прокликали задним числом»: границы сегментов в ней ничего не значат."""
    ts = [_ts(t) for t in times]
    out: set[int] = set()
    k, sec = BURST["n"], BURST["sec"]
    for i in range(len(ts) - k + 1):
        a, b = ts[i], ts[i + k - 1]
        if a and b and (b - a).total_seconds() <= sec:
            out.update(range(i, i + k))
    return out


def _sorted_tr(transitions: list) -> list[tuple[str, dt.datetime]]:
    return sorted(((s, _ts(t)) for s, t in transitions if _ts(t)), key=lambda x: x[1])


def _reach(tr: list, steps: list) -> dict[int, tuple[dt.datetime, int]]:
    """Шаг → (время первого выхода заказа на шаг не ниже этого, № перехода). Пропущенная
    стадия не теряет шаг: выход сразу на 5 — это и выход на 2, 3 и 4."""
    reach: dict[int, tuple[dt.datetime, int]] = {}
    hi = 0
    for i, ((_, t), st) in enumerate(zip(tr, steps)):
        if st is None or st <= hi:
            continue
        for k in range(hi + 1, st + 1):
            reach.setdefault(k, (t, i))
        hi = st
    if 1 not in reach and tr:
        reach[1] = (tr[0][1], 0)
    return reach


def reach_times(transitions: list, names: dict) -> dict[int, str]:
    """{шаг: ISO-время первого выхода на шаг не ниже} по всем переходам заказа — тот же
    счёт, что у границ сегментов (segments)."""
    tr = _sorted_tr(transitions)
    steps = [stage_step(names.get(s, s)) for s, _ in tr]
    return {k: t.isoformat() for k, (t, _i) in _reach(tr, steps).items()}


def segments(transitions: list, names: dict, workdays_fn=period_mod.workdays_ru, *,
             t0=None, facts: dict | None = None) -> dict:
    """Переходы одного заказа [(STAGE_ID, CREATED_TIME), …] (все, с повторами, по времени)
    → сегменты S1–S10.

    names — {STAGE_ID: имя стадии}. Граница сегмента — ПЕРВЫЙ выход заказа на шаг не ниже
    нужного (пропущенная стадия не теряет сегмент). t0 — начало приёмки (max(создание
    заказа, вход сделки в кат. 0)); без него — первый переход. facts — {delFact, utdRaw}:
    даты доставки и подписания УПД (сырые, без подстановки) для S9 и S10.

    → {"segs": {S: {start, end, wd, cd, norm, inNorm, neg, ret, burst, burstEnd, skip}}, "open": {S, start} | None,
       "returns": n, "returnPairs": [(шаг из, шаг в)], "burst": bool, "unknown": n,
       "noCalendar": bool}. wd — рабочие дни (None — года нет в календаре), cd — календарные.
    skip — шаг пропущен: начало и конец сегмента дал один переход (стадия перепрыгнула шаг,
    Рег. 6.13–6.14; Ан. S2 — рамочный договор уже есть). Такой сегмент не «в норме за 0 р.д.»:
    в своевременность и сроки он не идёт, а считается отдельно."""
    tr = _sorted_tr(transitions)
    steps = [stage_step(names.get(s, s)) for s, _ in tr]
    unknown = sum(1 for x in steps if x is None)
    in_burst = bursts([t for _, t in tr])
    returns, pairs, ret_times = 0, [], []
    prev = None
    for (_, t), st in zip(tr, steps):
        if st is None:
            continue
        if prev is not None and st < prev:
            returns += 1
            pairs.append((prev, st))
            ret_times.append(t)
        prev = st
    reach = _reach(tr, steps)
    facts = facts or {}
    point = {"t0": (_ts(t0), None) if t0 else reach.get(1, (None, None)),
             "delFact": (_ts(facts.get("delFact")), None),
             "utdRaw": (_ts(facts.get("utdRaw")), None)}

    def at(p):
        return point[p] if isinstance(p, str) else reach.get(p, (None, None))

    segs: dict[str, dict] = {}
    open_seg = None
    no_cal = False
    for code, _nm, a, b, _role in SEGMENTS:
        (ta, ia), (tb, ib) = at(a), at(b)
        if ta is None:
            continue
        if tb is None:
            if open_seg is None:                      # первый начатый и не законченный
                open_seg = {"seg": code, "start": ta.isoformat()}
            continue
        wd = workdays_fn(ta.astimezone(period_mod.MSK).date(), tb.astimezone(period_mod.MSK).date())
        no_cal = no_cal or wd is None
        norm = SEG_NORM.get(code)
        segs[code] = {
            "start": ta.isoformat(), "end": tb.isoformat(), "wd": wd,
            "cd": round((tb - ta).total_seconds() / 86400, 2), "norm": norm,
            "inNorm": (0 <= wd <= norm) if (norm is not None and wd is not None) else None,
            "neg": tb < ta,                           # конец раньше начала — порядок меток нарушен
            "ret": any(ta < r <= tb for r in ret_times),
            "burst": (ia in in_burst) or (ib in in_burst),
            "burstEnd": ib in in_burst,               # конец сегмента прокликан «пачкой» (Q1 — вход в «Заказ готов»)
            "skip": ia is not None and ia == ib,
        }
    return {"segs": segs, "open": open_seg, "returns": returns, "returnPairs": pairs,
            "burst": bool(in_burst), "unknown": unknown, "noCalendar": no_cal}


def step_since(transitions: list, names: dict) -> tuple[int | None, str]:
    """(шаг текущей стадии, вход в этот шаг): начало последнего непрерывного пребывания на
    шаге текущей стадии. Шаг 5 — три стадии заказа, и норма ≤ 9 р.д. меряется от входа в
    шаг, а не в текущую из трёх стадий."""
    tr = sorted(((s, _ts(t)) for s, t in transitions if _ts(t)), key=lambda x: x[1])
    if not tr:
        return None, ""
    cur = stage_step(names.get(tr[-1][0], tr[-1][0]))
    if cur is None:
        return None, tr[-1][1].isoformat()
    since = tr[-1][1]
    for s, t in reversed(tr):
        if stage_step(names.get(s, s)) != cur:
            break
        since = t
    return cur, since.isoformat()


# --- «сверх нормы» по живому заказу --------------------------------------------------------
def _d(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.astimezone(period_mod.MSK).date() if v.tzinfo else v.date()
    if isinstance(v, dt.date):
        return v
    t = _ts(v)
    return t.astimezone(period_mod.MSK).date() if t else None


def overrun(order: dict, today: dt.date, workdays_fn=None) -> dict:
    """Срок шага живого заказа против нормы регламента (р.д., «без пауз»).

    order: stage (имя стадии), stepSince (вход в шаг, ork.step_since; без него —
    stageSince, вход в текущую стадию), t0 (начало приёмки) и факты (supPaidPct, prodConf,
    shipFact, inbFact, delFact, utd). Шаг — step_now; шаг 1 меряется от t0, шаг 12 — от
    доставки клиенту. Без стадии меряется только шаг 12 (срок — от факта, стадия не нужна).
    → {step, role, norm, normDoc, normState, wd, over, by, since, label, why}; over None —
    нормы нет, портал её не меряет (normState nodata) или срок не посчитать (why)."""
    fn = workdays_fn or period_mod.workdays_ru
    step = step_now(order)
    doc = NORM_DOC.get(step, ("", "none", ""))
    out = {"step": step, "role": EXECUTOR.get(step) if step else None, "norm": None, "wd": None,
           "normDoc": doc[0], "normState": doc[1],
           "over": None, "by": None, "since": "", "label": NO_PAUSE, "why": ""}
    if step is None:
        out["why"] = "стадия не из таблицы шагов"
        return out
    norm = NORM_WD.get(step)
    out["norm"] = norm
    if norm is None:
        out["why"] = (f"норма {doc[0]} — нет данных: {doc[2]}" if doc[1] == "nodata"
                      else "шаг не нормируется")
        return out
    if step == 12:
        since = _d(order.get("delFact"))
    elif not order.get("stage"):
        out["why"] = "стадии нет — срок шага не посчитать"
        return out
    elif step == 1:
        since = _d(order.get("t0")) or _d(order.get("stepSince")) or _d(order.get("stageSince"))
    else:
        since = _d(order.get("stepSince")) or _d(order.get("stageSince"))
    if since is None:
        out["why"] = "нет факта доставки" if step == 12 else "нет даты входа в шаг"
        return out
    out["since"] = since.isoformat()
    wd = fn(since, today)
    if wd is None:
        out["why"] = "год без производственного календаря"
        return out
    out["wd"] = wd
    out["over"] = wd > norm
    out["by"] = wd - norm
    return out


def judged(order: dict, today: dt.date, *, in_cat0: bool = True, workdays_fn=None) -> dict:
    """«Сверх нормы» живого заказа — ОДНО правило для таблицы сделок, карточки, «Замедлено»,
    очереди (s1Over…s6Over, noUtd), ведомости, «Сверх нормы по менеджерам» и W2: overrun, а в
    счёт не идут гигиена карточки (hygiene: стадия позади фактов, брошенная, старая доставка
    без УПД) и приёмка заказа сделки вне воронки «Реализация» (Рег. 3.3).

    Шаг 12 (УПД) — не «сверх нормы» менеджера (решение владельца 27.10.2026, Б): больше 10 р.д.
    от доставки без УПД — waitUtd («ждём УПД от клиента»), over — False. Пункт noUtd очередь
    ставит по waitUtd; W2, ведомость (столбец «сверх нормы»), «Сверх нормы по менеджерам» и
    «Замедлено» — по over.

    → overrun + {rawOver — over до отбора, hyg, hygWhy, counted — в счёте срока (over
    True/False), termUnknown — шаг с нормой в счёте, а срок не посчитать, waitUtd — ждём УПД от
    клиента}."""
    ov = overrun(order, today, workdays_fn)
    hy = hygiene(order, today, workdays_fn)
    out = {**ov, "rawOver": ov["over"], "hyg": bool(hy["exclude"]), "hygWhy": hy["why"],
           "counted": False, "termUnknown": False, "waitUtd": False}
    if ov["norm"] is None:
        return out
    if ov["step"] == 1 and not in_cat0:
        out.update(over=None, why="приёмка — только у сделок воронки «Реализация»")
        return out
    if hy["exclude"]:
        out.update(over=None, why="гигиена карточки: " + hy["why"])
        return out
    out["counted"] = ov["over"] is not None
    out["termUnknown"] = ov["over"] is None
    if ov["step"] in WAIT_CODE and ov["over"]:
        # срок ждёт клиента, а не работу менеджера. rawOver (True) и by (wd − норма > 0) у такого
        # заказа остаются — сырой счёт overrun; «сверх нормы» читать только по over, ни по rawOver,
        # ни по by > 0 (решение владельца 27.10.2026, Б)
        out.update(over=False, waitUtd=True)
    return out


# --- контрольные точки недели (шаг 7, п. 9.2.4 регламента) --------------------------------
def checkpoints(order: dict, week) -> list[dict]:
    """Контрольные точки производства, попадающие в неделю [пн, вс] даты `week`:
    P50 — prodStart + ½(prodEnd − prodStart), для всех заказов; P14 — (shipPlan, иначе
    prodEnd) − 14 дн., если производство дольше 30 дн. Заказ уже готов или отгружен
    (шаг ≥ 8 по стадии и фактам) — точек нет: контроль производства пройден."""
    ps, pe = _d(order.get("prodStart")), _d(order.get("prodEnd"))
    if not ps or not pe or pe < ps:
        return []
    if any(order.get(k) for k in ("shipFact", "inbFact", "delFact")):
        return []
    st = step_of(order.get("stage"), order) if order.get("stage") else None
    if st is not None and st >= 8:
        return []
    w = _d(week)
    mon = w - dt.timedelta(days=w.weekday())
    sun = mon + dt.timedelta(days=6)
    pts = [("P50", ps + dt.timedelta(days=int((pe - ps).days * CHECKPOINT["half"])))]
    if (pe - ps).days > CHECKPOINT["long_days"]:
        base = _d(order.get("shipPlan")) or pe
        pts.append(("P14", base - dt.timedelta(days=CHECKPOINT["before_days"])))
    return [{"kind": k, "date": d.isoformat()} for k, d in pts if mon <= d <= sun]


# --- гигиена карточки ----------------------------------------------------------------------
def hygiene(order: dict, today: dt.date, workdays_fn=None) -> dict:
    """Карточка, которой нельзя верить в счёте: стадия позади фактов (оплачено, а стадия до
    шага 6; отгружено, а стадия до 9; доставлено, а до 11), > 60 р.д. на шаге с нормой ≤ 2 —
    «брошенная карточка», доставлено больше CLOSE_DAYS дней назад без УПД — «старая доставка
    без УПД» (пробел учёта, а не срок менеджера). Такие заказы уходят в блок «гигиена» и в
    счёт «сверх нормы» и сегментов не идут. Срок УПД (шаг 12) идёт от факта доставки:
    отставшая стадия его не портит — у шага 12 исключает только старая доставка.
    Вход в шаг раньше первого года календаря — нижняя граница р.д. от 1 января этого года.
    → {behind, abandoned, old, unknown, exclude, why}."""
    fn = workdays_fn or period_mod.workdays_ru
    base = stage_step(order.get("stage"))
    floor = fact_floor(order)
    now = step_now(order)
    top = _STAGE_SPAN["order"].get(norm_name(order.get("stage")), base) if base is not None else None
    behind = base is not None and top < floor
    abandoned = old = False
    why = []
    if base is None and order.get("stage"):
        why.append("стадия не из таблицы шагов")
    if behind:
        why.append(f"стадия — шаг {base}, по фактам — не ниже {floor}")
    norm = NORM_WD.get(base) if base else None
    if norm is not None and norm <= GATES["abandoned_norm_max"] and not behind:
        since = (_d(order.get("t0")) if base == 1 else None) or _d(order.get("stepSince")) \
            or _d(order.get("stageSince"))
        wd = fn(since, today) if since else None
        if wd is None and since:
            years = period_mod.workcal_years()
            y0 = min(years) if years else None
            if y0 and since.year < y0:
                lo = fn(dt.date(y0, 1, 1), today)     # нижняя граница: календаря на год входа нет
                wd = lo if lo is not None and lo > GATES["abandoned_wd"] else None
        if wd is not None and wd > GATES["abandoned_wd"]:
            abandoned = True
            why.append(f"{wd} р.д. на шаге {base} при норме {norm}")
    df = _d(order.get("delFact"))
    if now == 12 and df and (today - df).days > CLOSE_DAYS:
        old = True
        why.append(f"старая доставка без УПД: {(today - df).days} дн. назад (> {CLOSE_DAYS})")
    exclude = old or ((behind or abandoned) and now != 12)
    return {"behind": behind, "abandoned": abandoned, "old": old,
            "unknown": base is None and bool(order.get("stage")),
            "exclude": exclude, "why": "; ".join(why)}


# --- контракт показателя -------------------------------------------------------------------
def metric_state(value, n: int | None, cover: float | None, gates: dict = GATES, *,
                 n_key: str = "n_min", agree: float | None = None, burst: float | None = None,
                 plan_eq_fact: float | None = None, src: str = "", need: str = "") -> dict:
    """Состояние плитки или ячейки: {state, value, n, cover, why, src, need}.

    nodata — значения нет (или n = 0): «Нет данных: чего нет · откуда придёт (need)»; ноль за
    неизвестное не выдаётся. hidden — не пройдены ворота: покрытие < cover_prelim, опоры
    расходятся (agree < agree_min), «пачек» больше burst_max, «план = факт» больше
    plan_eq_fact_max; значение не отдаётся. prelim — покрытие от cover_prelim до cover_show
    или n меньше порога `n_key`. Иначе show."""
    out = {"state": "nodata", "value": None, "n": n, "cover": cover, "why": "", "src": src, "need": need}
    if value is None or not n:
        out["why"] = "нет данных"
        return out
    hide = []
    if cover is not None and cover < gates["cover_prelim"]:
        hide.append(f"покрытие {round(cover * 100)} % < {round(gates['cover_prelim'] * 100)} %")
    if agree is not None and agree < gates["agree_min"]:
        hide.append(f"опоры совпадают у {round(agree * 100)} % < {round(gates['agree_min'] * 100)} %")
    if burst is not None and burst > gates["burst_max"]:
        hide.append(f"«пачек» {round(burst * 100)} % > {round(gates['burst_max'] * 100)} %")
    if plan_eq_fact is not None and plan_eq_fact > gates["plan_eq_fact_max"]:
        hide.append(f"«план = факт» у {round(plan_eq_fact * 100)} % > "
                    f"{round(gates['plan_eq_fact_max'] * 100)} %")
    if hide:
        out.update(state="hidden", why="не показываем: " + "; ".join(hide))
        return out
    prelim = []
    if cover is not None and cover < gates["cover_show"]:
        prelim.append(f"покрытие {round(cover * 100)} %")
    if n < gates[n_key]:
        prelim.append(f"n = {n} < {gates[n_key]}")
    out["value"] = value
    if prelim:
        out.update(state="prelim", why="предварительно: " + "; ".join(prelim))
    else:
        out["state"] = "show"
    return out


# --- хозяин пункта по шагу (решение 26.10.2026, п. 1) ---------------------------------------
_ESC_STEP = object()


def owner(step: int | None, people: dict, *, debt: bool = False, esc=_ESC_STEP,
          role: str | None = None) -> dict:
    """Хозяин пункта очереди — исполнитель шага. people — уже найденные люди заказа и
    сделки: orkMgr (ответственный заказа, если он из отдела ОРК), dealOrk (поле сделки
    «Ответственный за реализацию»), orkHead (руководитель отдела ОРК), kam, ossOrder,
    ossDeal. Долг клиента (debt) — КАМ, ОСС в копии. esc — роль в копии для пункта со своей
    эскалацией (по умолчанию — STEP[шаг]['esc']); role — роль хозяина, если пункт задаёт её
    сам (ITEM), иначе исполнитель шага. → {role, who, copy, copyRole}; who — «не назначен»,
    если человека роли нет (логистика — подпись роли)."""
    def nm(*keys):
        for k in keys:
            v = people.get(k)
            if v not in (None, "", "—"):
                return v
        return ""

    find = {"orkMgr": lambda: nm("orkMgr", "dealOrk"),
            "orkHead": lambda: nm("orkHead") or ROLES["orkHead"],
            "kam": lambda: nm("kam"),
            "oss": lambda: nm("ossOrder", "ossDeal"),
            "log": lambda: ROLES["log"],
            "sup": lambda: ROLES["sup"]}
    if debt:
        role, copy_role = "kam", "oss"
    else:
        role = role or (EXECUTOR.get(step) if step else "orkHead")
        copy_role = (STEP[step]["esc"] if step else None) if esc is _ESC_STEP else esc
        if copy_role == role:
            copy_role = None
    who = find[role]() or "не назначен"
    copy = (find[copy_role]() or "не назначен") if copy_role else ""
    return {"role": role, "who": who, "copy": copy, "copyRole": copy_role or ""}


# --- пункт очереди → шаг, хозяин и копия (решение 26.10.2026, п. 1; правило 4) -------------
# код → (шаг регламента, роль хозяина, роль в копии). Шаг None — текущий шаг заказа (срок
# клиента сорван на любом шаге). Хозяин — исполнитель шага, кроме пунктов, которые
# регламент отдаёт другой роли: долг клиента урегулирует КАМ, ОСС — в копии (п. 4); приёмку,
# заказ без заказа и оплату без договора разбирает руководитель ОРК; даты размещения вносит
# ОСС (Рег. 8.2.3), ТН — логистика. При эскалации КАМ — в копии.
ITEM = {
    # срок клиента и производство — шаг заказа сейчас (step_now: тот же, что в ведомости и
    # отборе по шагу), хозяин — менеджер ОРК двигает поставку, КАМ в копии (срок с клиентом)
    "late": (None, "orkMgr", "kam"),
    "risk": (None, "orkMgr", "kam"),
    "forecast": (None, "orkMgr", "kam"),
    "prodDev": (None, "orkMgr", "kam"),
    "planAfterDl": (None, "orkMgr", "kam"),
    "unpaid": (13, "kam", "oss"),
    "noUtd": (12, "orkMgr", "kam"),
    "noPayPlan": (13, "oss", "kam"),
    "dealOpen": (13, "oss", None),
    "dealClose": (13, "oss", None),
    "noOrder": (1, "orkHead", "kam"),
    "s1Over": (1, "orkHead", "kam"),
    "s2Over": (4, "orkMgr", "orkHead"),
    "s3Over": (5, "orkMgr", "orkHead"),
    "s6Over": (8, "orkMgr", "orkHead"),
    "payNoContract": (5, "orkHead", "kam"),
    "gone": (1, "orkHead", None),
    "noDl": (1, "orkHead", "kam"),
    "noPlanDates": (6, "oss", None),
    "noFact": (11, "log", None),
    # чаты и договоры (dealctx): «сигнал из чата, не эскалация»; ОСС их не получает — со
    # сторонами не говорит (Рег. 1.11). Срок из чата — по виду срока: отгрузка и документы —
    # менеджеру ОРК, доставка и оплата — КАМу; прочие сроки — менеджеру ОРК, КАМ в копии
    "chatDue": (None, "orkMgr", "kam"),
    "chatDue:отгрузка": (9, "orkMgr", "kam"),
    # «документы» из чата — и отгрузочные (шаги 8–9), и УПД (шаг 12): шаг — по месту сделки
    # (ячейка «Шаг»), а не догадка «УПД»
    "chatDue:документы": (None, "orkMgr", "kam"),
    "chatDue:доставка": (10, "kam", None),
    "chatDue:оплата": (13, "kam", None),
    "chatRisk": (None, "orkMgr", "kam"),
    # договор стороны не найден: заказчика — КАМ (шаги 2–3), поставщика — менеджер ОРК (шаг 4)
    "contractMissing:customer": (2, "kam", None),
    "contractMissing:supplier": (4, "orkMgr", None),
    "contractMissing:both": (2, "kam", "orkMgr"),
    # сверка договоров заказчик ↔ поставщик — допсоглашение с поставщиком: руководитель ОРК
    "contractRisk": (4, "orkHead", None),
}
# роли пунктов, которых регламент не описывает (чаты, договоры, экономика — dealctx): хозяин —
# человек сделки, подпись роли — для отбора очереди по роли
ROLES_EXTRA = {"dealResp": "ответственный сделки", "src": "сорсинг"}
# «сверх нормы» шага ОРК → код пункта (срок работы менеджера: W2, «Сверх нормы по менеджерам»,
# ведомость, «Замедлено»)
OVER_CODE = {1: "s1Over", 4: "s2Over", 5: "s3Over", 8: "s6Over"}
# срок шага, который ждёт клиента, → код пункта (решение владельца 27.10.2026, Б): шаг 12 —
# больше 10 р.д. от доставки без подписанного УПД — «ждём УПД от клиента» (подпись УПД — за
# заказчиком, Рег. 14.2.2): пункт noUtd у менеджера ОРК с эскалацией КАМу на 11-й р.д. есть, а в
# «сверх нормы» менеджера (W2, по менеджерам, ведомость, «Замедлено») он не идёт
WAIT_CODE = {12: "noUtd"}
WAIT_UTD = "ждём УПД от клиента"
# все сроки шагов регламента в р.д. → код пункта очереди (inWd у пункта)
TERM_CODE = {**OVER_CODE, **WAIT_CODE}


def item_owner(code: str, step_now: int | None, people: dict, *, key: str | None = None) -> dict:
    """Хозяин пункта очереди по таблице ITEM: {step, role, who, copy, copyRole}. key —
    строка ITEM с уточнением (вид срока из чата, сторона договора), нет её — по коду. Код не
    из таблицы — исполнитель текущего шага и его эскалация."""
    k = key if key in ITEM else code
    st, role, copy = ITEM.get(k, (None, None, _ESC_STEP))
    step = st if st is not None else step_now
    out = owner(step, people, role=role, esc=copy if k in ITEM else _ESC_STEP)
    return {"step": step, **out}


def p80(xs: list) -> float | None:
    """80-й процентиль «Системы аналитики»: s[⌈0,8·n⌉ − 1] по возрастанию."""
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    k = -(-8 * len(s) // 10) - 1
    return float(s[max(0, k)])


def median(xs: list) -> float | None:
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    n = len(s)
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# --- окна показателей ------------------------------------------------------------------------
ORK_SINCE = dt.date(2026, 8, 24)      # роботы ОРК и «менеджер ОРК = ответственный заказа» (решение п. 3)
S1_ANCHOR_SINCE = dt.date(2026, 8, 28)  # вторая опора S1 — «Дата записи нового ответственного»
PREPAY_SINCE = dt.date(2026, 4, 2)    # «Доля первичной оплаты, %» заведена
PROBE_PENDING = {"M1": "v60"}         # показатель не выше «предварительно» до вердикта зонда
WEEK_BACK_WD = 20                     # «в срок за 20 р.д.» под плиткой приёмки
MONTHS_BACK = 3                       # «Месяц»: текущий и два прошлых
LOAD_BAND = 0.25                      # загрузка менеджера — отклонение от среднего ≤ ±25 % (Ан. §4)
YEAR_DAYS = 365                       # сроки S5/S7/S8 и цикла — по завершённым за год


def art_required(o: dict, a: dict, reach: dict | None = None) -> bool:
    """Обязан ли заказ иметь артефакт `a` (ARTIFACTS): заказ прошёл шаг need (по стадии и
    фактам) ПОСЛЕ даты создания поля (дизайн: «артефакт требуется только у заказов, прошедших
    шаг после даты создания поля»), условие выполнено.

    Дата прохождения — первый выход на шаг need по переходам (reach — {шаг: ISO}). Её нет
    (шаг пройден по фактам, история его не дала) — требуем, только если заказ создан не
    раньше поля: тогда шаг пройден заведомо после; заказ старше поля или без даты создания —
    не требуем (неизвестно — не «нет артефакта»). OC — только у импорта: o["import"] is True
    (явного поля «импорт/РФ» нет, подписи «Направления поставки» не сверены — зонд v60), до
    сверки не требуется; проформа — при доле первичной оплаты > 0; платёжка поставщику —
    когда оплата была; платёжка клиента — у закрытого успехом."""
    # шаг — ork.step_now, как у ведомости: доставленный без УПД при неизвестной стадии — шаг 12
    st = step_now(o)
    if st is None or st < a["need"]:
        return False
    if a["cond"] == "import" and o.get("import") is not True:
        return False
    # проформа — только при предоплате по «Доле первичной оплаты»: доли нет — не требуем
    # (оплата после отгрузки — не предоплата); платёжка поставщику — когда оплата была
    if a["cond"] == "prepay" and not (o.get("prepayShare") or 0) > 0:
        return False
    if a["cond"] == "paid" and not ((o.get("prepayShare") or 0) > 0 or (o.get("supPaidPct") or 0) > 0):
        return False
    if a["cond"] == "closed" and not o.get("success"):
        return False
    since = dt.date.fromisoformat(a["since"])
    at = _d((reach or {}).get(a["need"]))
    if at is None:
        made = _d(o.get("created"))
        return bool(made and made >= since)
    return at >= since


# --- чего в портале нет: показатели «Нет данных» с полем, которое их даст -------------------
AN4_NODATA = (
    ("«Чистая» длительность договора с поставщиком (S2 минус паузы)", "поле «Пауза срока менеджера»"),
    ("Дисциплина статусов (шаг 7): запросы статуса и контрольные точки", "поле «Запрос статуса поставщику и ответ»"),
    ("Скорость эскалации", "поле «Эскалация: дата, тип, причина, решение»"),
    ("Раннее выявление отклонений", "поле «История планового срока готовности»"),
    ("Скорость подготовки документов (шаг 8)", "даты запроса документов, сертификации и завершающей оплаты"),
    ("Контроль УПД: эскалация на 11-й р.д. (шаг 12)", "поле «Эскалация: дата, тип, причина, решение»"),
)
SCORECARD = (
    # блок, показатель, вес, есть ли данные (иначе — поле, которое их даст)
    ("Срок", "Соблюдение первично подтверждённого срока", 25, ""),
    ("Срок", "Стабильность срока (переносы)", 10, "поле «История планового срока готовности»"),
    ("Отзывчивость", "Ответ на запрос статуса за 3 р.д.", 15, "поле «Запрос статуса поставщику и ответ»"),
    ("Документы", "Комплект с первого раза", 15, "отметка повторного запроса документов"),
    ("Качество", "Поставка без претензий", 20, "смарт-процесс «Рекламации»: тип и дата претензии"),
    ("Дисциплина", "Договор и платежи", 15, "раунды разногласий, «другое юрлицо», дата OC"),
)
# заявка администратору Битрикса (Ан. §2.1–2.3): поля «добавить» — ничего не отправлено
FIELDS_ADD = (
    ("событие", "Эскалация: дата, тип, причина из справочника, дата и результат решения", "W4, скорость эскалации"),
    ("событие", "Пауза срока менеджера: начало, конец, причина", "W4, «чистые» сроки S2"),
    ("событие", "История планового срока готовности: дата, новая дата, причина", "стабильность срока, раннее выявление"),
    ("событие", "Запрос статуса поставщику и его ответ: две даты", "дисциплина статусов, отзывчивость"),
    ("событие", "Сканирование завершено (дата); скан ТН файлом; дата поступления оплаты клиента", "шаги 9, 11, 13"),
    ("атрибут", "Категория оборудования на заказе, из справочника", "Q2 по категориям"),
    ("атрибут", "Тип поставки «импорт / РФ» — явным полем", "Q2 по типу, OC обязателен при импорте"),
    ("атрибут", "Страна происхождения товара", "Q2 по стране"),
    ("атрибут", "Базис (Инкотермс) по заказу", "сроки логистики по базису"),
    ("атрибут", "Флаги: в наличии, прямая отгрузка, перевод техдокументации, сертификаты своими силами", "сложность"),
    ("атрибут", "Флаги: Сорсер подключён, другое юрлицо, Письмо о принадлежности", "сложность, дисциплина"),
    ("атрибут", "Претензия: тип и дата (смарт-процесс «Рекламации»)", "качество в скоркарде"),
    ("справочник", "Категории оборудования, 8–15 групп", "Q2"),
    ("справочник", "Причины переноса срока", "стабильность срока"),
    ("справочник", "Причины эскалации", "эскалации"),
    ("справочник", "Типы претензий", "качество"),
)
FIELDS_TO = "администратор Битрикса"
