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

Вопрос владельца 29.10.2026 «как считает срок»: приёмка (шаг 1, Рег. 3) — принять сделку и
назначить менеджера ОРК. Она кончается РАНЬШЕ из двух: выход заказа на шаг ≥ 2 по стадии или
«Дата записи нового ответственного» заказа (assignDate) — если она не раньше дня начала
приёмки по МСК, не позже конца окна (contracts) и ответственный заказа из отдела ОРК (orkMgr):
assign_at. После назначения идёт шаг 4 «Подготовка договора с поставщиком» (менеджер ОРК,
2 р.д.), а стадия законно остаётся «Заказ создан» до направления заказа: это мягкая граница
шага (ASSIGN_FLOOR) — её видят step_now, overrun, «брошенная карточка» на мягкой границе,
полнота карточки (passed_at) и сегменты S1/S2, но не «стадия позади фактов» (hygiene.behind).
Срок шага и откуда он меряется — clock: шаг 4 — от назначения, и после направления заказа
(«Заказ направлен» — тот же шаг 4), возврат — от повторного входа.

Решение владельца 31.10.2026 (зонд v63): шаг 0 «Подтверждение передачи в ОРК» — задача Битрикса
«Подтверждение готовности передачи…», привязанная к заказу (ork_confirm). Пока задача открыта, заказ
в «Заказ создан» стоит на шаге 0 (PRE_STEP): хозяин — исполнитель задачи (confirmResp), КАМ в копии,
норма 5 р.д.; это не приёмка ОРК и не «сверх нормы» ОРК: judged отдаёт preOver, а не over (как waitUtd
у шага 12), пункт s0Over (PRE_CODE). Приёмка (шаг 1) начинается с закрытия задачи: t0 заказа =
max(прежнее начало, закрытие задачи), прежнее — t0Raw. Стадия или факты выше шага 1 сильнее открытой
задачи — шаг по ним, задача — замечание гигиены confirmOpen. Шаг 0 — первый в STEPS, а 0 ложно:
проверять шаг только на None (tests/test_step_zero_guard.py).

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
    # шаг 0 (решение владельца 31.10.2026): исполнитель задачи Битрикса «Подтверждение готовности
    # передачи…» — кто бы он ни был (из ОРК таких 3 из 22, зонд v63)
    "confirmResp": "исполнитель задачи подтверждения передачи",
}
# смежники ОРК: пункт, где мяч у них, — «ждёт смежника», а не «сверх нормы» менеджера
ADJACENT = ("kam", "oss", "log", "sup")
OWNER_RULE = "step"          # хозяин пункта — исполнитель шага (решение владельца 26.10.2026, п. 1)
NO_PAUSE = "без пауз"        # паузы срока менеджера в Битриксе не ведутся (W4 — нет данных)

# --- 13 шагов регламента и шаг 0 до передачи в ОРК -----------------------------------------
# no, name, role — исполнитель; also — кто ещё участвует; norm — норма шага в рабочих днях,
# которую портал меряет (None — не меряет); seg — сегмент «Системы аналитики», который меряет
# шаг; waits — смежник, от которого шаг зависит; esc — кому эскалирует исполнитель (в копии
# пункта очереди). Норма по документу — NORM_DOC: регламент её задаёт, но портал её не
# меряет («нет данных», шаг 6) — это не «не нормируется».
STEPS = (
    # шаг 0 — до передачи в ОРК (решение владельца 31.10.2026): задача подтверждения передачи
    # открыта; не шаг ОРК — смежником (ADJACENT) не считается, в W2 и «сверх нормы» не идёт
    {"no": 0, "name": "Подтверждение передачи в ОРК", "role": "confirmResp", "also": (), "norm": 5,
     "seg": None, "waits": None, "esc": "kam"},
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
    0: ("5 р.д.", "norm", ""),
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
# мягкая нижняя граница (вопрос владельца 29.10.2026): назначение менеджера ОРК записано
# («Дата записи нового ответственного», contracts.ASSIGN_AT → assignDate заказа, assign_at) —
# приёмка сделана, заказ на шаге 4 «Подготовка договора с поставщиком», хотя стадия ещё «Заказ
# создан»: до направления заказа она так и стоит (Рег. 3.3, 6.1; «Заказ направлен» — шаг 4 у
# заказа). Только у стадии шага 1 и только там, где меряется срок (step_now, overrun, clock,
# брошенная карточка, сегменты S1/S2): в fact_floor её нет, «стадия позади фактов» её не видит —
# стадия при назначении не отстала
ASSIGN_FLOOR = 4
# шаг 0 (решение владельца 31.10.2026): задача подтверждения передачи открыта, стадия — шага 1
PRE_STEP = 0


def confirm_open(order: dict) -> bool:
    """Задача подтверждения передачи заказа в ОРК открыта (ork_confirm, contracts: order["confirm"])."""
    return bool((order.get("confirm") or {}).get("open"))


def pre_open(order: dict) -> bool:
    """Заказ на шаге 0 по задаче: задача подтверждения открыта, стадия — шага 1 и факты шаг не подняли
    (как step_now). Стадия или факты дальше шага 1 при открытой задаче — не шаг 0 (решение владельца
    31.10.2026, а: стадия сильнее, замечание confirmOpen): назначение и сегменты — как без задачи."""
    return confirm_open(order) and bool(order.get("stage")) and step_of(order.get("stage"), order) == 1

# --- сегменты S1–S10 «Системы аналитики» ----------------------------------------------------
# начало и конец — первый выход заказа на шаг не ниже указанного (по всем переходам);
# "t0" — max(создание заказа, вход сделки в кат. 0); "delFact"/"utd" — даты фактов. Выход на
# шаги 2–4 — и назначение менеджера ОРК, если оно раньше выхода из «Заказ создан» по стадии
# (ASSIGN_FLOOR, segments): конец S1 и начало S2 — раньше из двух (endBy / startBy).
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


# «900/1» в начале названия; дата («15/10/2025», «15/10.2025») — не номер заказа
_ORDER_NO = re.compile(r"\s*(\d{1,4})\s*/\s*(\d{1,3})(?!\d|\s*[/.]\d)")


def order_label(title, oid="") -> tuple[str, int | None]:
    """Номер заказа, как его пишет портал: «900/1» — № сделки реализации и № заказа в начале
    названия карточки СП-172 («900/1. …»; владелец 29.10.2026: «сделка одна — заказы разные
    1/2/3»). Номера в названии нет (или в начале дата) — «id <id карточки>»: по нему заказ
    находится в Битриксе. Номер сделки в названии не сверяется с № реализации (карточку могли
    скопировать из другой сделки): несовпадение показывает страница — «№<№ сделки> · <номер>».
    → (подпись, № заказа для сортировки; нет номера — None)."""
    m = _ORDER_NO.match(str(title or ""))
    if m:
        return f"{int(m.group(1))}/{int(m.group(2))}", int(m.group(2))
    return (f"id {oid}" if oid not in (None, "") else ""), None


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


def _start_day(order: dict) -> dt.date | None:
    """День начала заказа по МСК для сверки «Даты записи нового ответственного»: t0Raw
    (max(создание заказа, вход сделки в реализацию), без закрытия задачи подтверждения — счёт
    assign_stale и assign_no_mgr прежний), без него — t0, без него — день создания заказа."""
    return _d(order.get("t0Raw") or order.get("t0")) or _d(order.get("created"))


def assign_at(order: dict) -> dt.date | None:
    """Назначение менеджера ОРК, которому верим (вопрос владельца 29.10.2026): «Дата записи
    нового ответственного» заказа (assignDate) не раньше дня начала приёмки (_start_day, МСК), и
    ответственный заказа — из отдела ОРК (orkMgr, contracts: решение 26.10.2026). Раньше начала
    — дата скопирована или устарела (assign_stale); ответственный не из ОРК — менеджер ОРК не
    назначен, приёмка не сделана (assign_no_mgr): не в счёт, оба — счётом. Дата позже конца окна
    отброшена при чтении (contracts: на конец окна назначения ещё не было). Дня начала нет —
    None: сверить не с чем.

    Шаг 0 (решение владельца 31.10.2026): пока заказ на шаге 0 (pre_open: задача подтверждения
    открыта, стадия — шага 1) — None (приёмки ещё нет); стадия ушла дальше при открытой задаче —
    назначение как без задачи (стадия сильнее, решение 31.10.2026, а); назначение раньше закрытия
    задачи — с дня закрытия (max(дата, t0)): приёмка идёт с закрытия, раньше неё не кончается."""
    if pre_open(order):
        return None
    a, s = _d(order.get("assignDate")), _start_day(order)
    if not (a and s and a >= s and order.get("orkMgr")):
        return None
    t0 = _d(order.get("t0"))
    return max(a, t0) if t0 else a


def seg_assign(order: dict) -> dt.date | None:
    """«Дата записи нового ответственного» для сегментов S1/S2 (segments, facts.assignAt): нет
    даты, ответственный не из ОРК или заказ на шаге 0 (pre_open) — None; дата не раньше начала
    заказа (t0Raw) — max(дата, t0): с закрытия задачи; раньше — сырая дата (segments сам пометит
    её «раньше начала», assignIgnored). Без задачи — та же дата, что раньше."""
    a = _d(order.get("assignDate"))
    if a is None or not order.get("orkMgr") or pre_open(order):
        return None
    s = _start_day(order)
    if s is not None and a >= s:
        t0 = _d(order.get("t0"))
        return max(a, t0) if t0 else a
    return a


def assign_stale(order: dict) -> bool:
    """«Дата записи нового ответственного» есть, но раньше дня начала приёмки — не в счёт (счёт)."""
    a, s = _d(order.get("assignDate")), _start_day(order)
    return bool(a and s and a < s)


def assign_no_mgr(order: dict) -> bool:
    """Дата записи есть и не раньше начала приёмки, а ответственный заказа не из отдела ОРК
    (orkMgr пуст): назначения менеджера ОРК нет — не в счёт (счёт). Поле сделки «Ответственный
    за реализацию» дату не даёт: она пишется при смене ответственного заказа."""
    a, s = _d(order.get("assignDate")), _start_day(order)
    return bool(a and s and a >= s and not order.get("orkMgr"))


def step_now(order: dict, *, unknown: Counter | None = None) -> int | None:
    """Шаг живого заказа — ОДИН для ведомости, W2, очереди, таблицы сделок, карточки заказа,
    «Замедлено» и хозяина пункта: по стадии и фактам (step_of; стадии нет — по фактам).
    Доставлено клиенту, УПД нет — шаг 12 (УПД, менеджер ОРК; срок — от факта доставки,
    Рег. 14.2.5), даже если стадия — «Заказ получен клиентом» (шаг 11) или ниже, и даже
    если стадии нет в таблице шагов: срок шага 12 стадии не требует. Стадия не из таблицы и
    доставки нет — None (имя — в `unknown`).

    Стадия шага 1 («Заказ создан»), факты шаг не подняли, а назначение менеджера ОРК записано
    (assign_at) — шаг 4 «Подготовка договора с поставщиком» (мягкая граница ASSIGN_FLOOR, вопрос
    владельца 29.10.2026): приёмка сделана, срок шага 4 — от назначения (clock), хозяин —
    менеджер ОРК. Это не «стадия позади фактов»: до направления заказа стадия законно стоит.

    Стадия шага 1, а задача подтверждения передачи открыта (confirm_open) — шаг 0 (PRE_STEP,
    решение владельца 31.10.2026): заказ ещё не передан в ОРК. Стадия или факты выше шага 1
    сильнее открытой задачи (замечание гигиены confirmOpen)."""
    if order.get("stage"):
        st = step_of(order.get("stage"), order)
        if st == 1 and confirm_open(order):
            st = PRE_STEP
        elif st == 1 and assign_at(order):
            st = ASSIGN_FLOOR
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
    счёт, что у границ сегментов (segments). Только переходы: назначение менеджера ОРК
    (ASSIGN_FLOOR) сюда не входит — по нему clock узнаёт, что вход в шаг 4 по стадии первый."""
    tr = _sorted_tr(transitions)
    steps = [stage_step(names.get(s, s)) for s, _ in tr]
    return {k: t.isoformat() for k, (t, _i) in _reach(tr, steps).items()}


def segments(transitions: list, names: dict, workdays_fn=period_mod.workdays_ru, *,
             t0=None, facts: dict | None = None, pre_open: bool = False) -> dict:
    """Переходы одного заказа [(STAGE_ID, CREATED_TIME), …] (все, с повторами, по времени)
    → сегменты S1–S10.

    names — {STAGE_ID: имя стадии}. Граница сегмента — ПЕРВЫЙ выход заказа на шаг не ниже
    нужного (пропущенная стадия не теряет сегмент). t0 — начало приёмки (max(создание
    заказа, вход сделки в кат. 0)); без него — первый переход. facts — {delFact, utdRaw,
    assignAt}: даты доставки и подписания УПД (сырые, без подстановки) для S9 и S10 и
    «Дата записи нового ответственного» для S1 и S2.

    Приёмка (S1) кончается РАНЬШЕ из двух (вопрос владельца 29.10.2026): выход на шаг ≥ 2 по
    стадии или назначение менеджера ОРК (assignAt) — оно же начало S2 (шаг 4 идёт с назначения,
    стадия до направления заказа законно стоит в «Заказ создан»; ASSIGN_FLOOR). Назначение
    раньше дня начала приёмки (МСК) — скопировано или устарело: не в счёт (assignIgnored). Р.д. —
    по датам, как у всех сегментов; в один день со стадией граница — стадия (у перехода есть
    время, у даты записи — только день; р.д. те же); дата без времени в день начала — не раньше
    начала (конец не «раньше начала»).

    → {"segs": {S: {start, end, wd, cd, norm, inNorm, neg, ret, burst, burstEnd, skip, startBy,
       endBy}}, "open": {S, start} | None, "returns": n, "returnPairs": [(шаг из, шаг в)],
       "burst": bool, "unknown": n, "noCalendar": bool, "assignUsed": bool, "assignIgnored": bool}.
    wd — рабочие дни (None — года нет в календаре), cd — календарные. startBy / endBy — чем
    дана граница: «стадия» (переход), «назначение» (assignAt), «начало приёмки» (t0), «факт»
    (доставка, УПД). skip — шаг пропущен: начало и конец сегмента по СТАДИИ дал один переход
    (стадия перепрыгнула шаг, Рег. 6.13–6.14; Ан. S2 — рамочный договор уже есть). Такой
    сегмент не «в норме за 0 р.д.»: в своевременность и сроки он не идёт, а считается отдельно.
    Назначение пропуск не отменяет: стадия прыгнула из «Заказ создан» сразу на шаг ≥ 5 — S2
    пропущен и при начале с назначения (startBy «назначение», wd — от него; в своевременность
    S2 не идёт, как и без назначения), — тот же счёт пропусков, что у зонда v60 (по переходам).

    pre_open — заказ на шаге 0 (задача подтверждения передачи открыта, решение владельца
    31.10.2026): приёмка не начата — S1 не начат и открытым не считается."""
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
    point = {"t0": (None, None) if pre_open else (_ts(t0), None) if t0 else reach.get(1, (None, None)),
             "delFact": (_ts(facts.get("delFact")), None),
             "utdRaw": (_ts(facts.get("utdRaw")), None)}
    # назначение менеджера ОРК раньше выхода из «Заказ создан» (по дню МСК) — выход на шаги 2–4
    soft: dict[int, tuple] = {}
    asg, start = _ts(facts.get("assignAt")), point["t0"][0]
    ignored = False
    if asg is not None:
        day = asg.astimezone(period_mod.MSK).date()
        if start is None or day < start.astimezone(period_mod.MSK).date():
            ignored = True
        else:
            asg = max(asg, start)
            r2 = reach.get(2)
            if r2 is None or day < r2[0].astimezone(period_mod.MSK).date():
                soft = {k: (asg, None) for k in range(2, ASSIGN_FLOOR + 1)}
    by_pt = {"t0": "начало приёмки", "delFact": "факт", "utdRaw": "факт"}

    def at(p):
        return point[p] if isinstance(p, str) else soft.get(p) or reach.get(p, (None, None))

    def by(p) -> str:
        return by_pt[p] if isinstance(p, str) else ("назначение" if p in soft else "стадия")

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
            # пропуск — по переходам стадии (не по назначению): прыжок 1 → 5 пропускает S2 и
            # тогда, когда S2 начат назначением
            "skip": ib is not None and ib == (reach.get(a, (None, None))[1] if isinstance(a, int) else ia),
            "startBy": by(a), "endBy": by(b),        # чем дана граница (правило 16)
        }
    return {"segs": segs, "open": open_seg, "returns": returns, "returnPairs": pairs,
            "burst": bool(in_burst), "unknown": unknown, "noCalendar": no_cal,
            "assignUsed": bool(soft), "assignIgnored": ignored}


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


def _first_stay(order: dict, step: int) -> bool:
    """Текущее пребывание на шаге — первое: вход в шаг (stepSince) и есть первый выход заказа
    на этот шаг по переходам (_reach, ork.reach_times). Переходов нет — неизвестно: False."""
    r = order.get("_reach") or {}
    first = _ts(r.get(step) or r.get(str(step)))
    entry = _ts(order.get("stepSince"))
    return bool(first and entry and first == entry)


def clock(order: dict, step: int | None) -> tuple[dt.date | None, str]:
    """(день, от которого меряется срок шага живого заказа, чем он дан) — один у overrun,
    очереди («с дд.мм» у пункта), карточки заказа и брошенной карточки (hygiene):
      шаг 0 — от создания открытой задачи подтверждения передачи («задача подтверждения»);
      шаг 12 — от факта доставки клиенту («доставка», Рег. 14.2.5);
      шаг 1 — от начала приёмки t0 («начало приёмки»), без t0 — от входа в шаг;
      шаг 4 — от назначения менеджера ОРК («назначение», assign_at; вопрос владельца
        29.10.2026), если стадия ещё шага 1 (мягкая граница ASSIGN_FLOOR) или заказ впервые
        вышел на шаг 4 по стадии позже назначения: шаг 4 начался с назначения, и направление
        заказа («Заказ направлен» — тот же шаг 4) счёт не обнуляет; возврат на шаг 4 — от
        повторного входа, в том числе возврат в «Заказ создан» после шагов ≥ 2 (по переходам
        _reach): от возврата, а назначение позже возврата — от него;
      иначе — от входа в шаг по стадии (stepSince, без него — stageSince; «вход в шаг»)."""
    if step == PRE_STEP:
        return _d((order.get("confirm") or {}).get("from")), "задача подтверждения"
    entry = _d(order.get("stepSince")) or _d(order.get("stageSince"))
    if step == 12:
        return _d(order.get("delFact")), "доставка"
    if step == 1:
        t0 = _d(order.get("t0"))
        return (t0, "начало приёмки") if t0 else (entry, "вход в шаг")
    if step == ASSIGN_FLOOR:
        a = assign_at(order)
        if a and stage_step(order.get("stage")) == 1:
            r = order.get("_reach") or {}
            back = bool(r.get(2) or r.get("2"))       # стадия уже выходила на шаг ≥ 2 — возврат
            if back and entry and entry >= a:
                return entry, "вход в шаг"
            return a, "назначение"
        if a and entry and a < entry and _first_stay(order, ASSIGN_FLOOR):
            return a, "назначение"
    return entry, "вход в шаг"


def overrun(order: dict, today: dt.date, workdays_fn=None) -> dict:
    """Срок шага живого заказа против нормы регламента (р.д., «без пауз»).

    order: stage (имя стадии), stepSince (вход в шаг, ork.step_since; без него —
    stageSince, вход в текущую стадию), t0 (начало приёмки), assignDate (назначение менеджера
    ОРК), _reach (первые выходы на шаги) и факты (supPaidPct, prodConf, shipFact, inbFact,
    delFact, utd). Шаг — step_now; откуда меряется срок — clock: шаг 1 — от t0, шаг 4 — от
    назначения (если приёмку закончило оно), шаг 12 — от доставки клиенту. Без стадии меряется
    только шаг 12 (срок — от факта, стадия не нужна).
    → {step, role, norm, normDoc, normState, wd, over, by, since, sinceBy, label, why}; over
    None — нормы нет, портал её не меряет (normState nodata) или срок не посчитать (why)."""
    fn = workdays_fn or period_mod.workdays_ru
    step = step_now(order)
    doc = NORM_DOC.get(step, ("", "none", ""))
    out = {"step": step, "role": EXECUTOR.get(step) if step is not None else None, "norm": None, "wd": None,
           "normDoc": doc[0], "normState": doc[1],
           "over": None, "by": None, "since": "", "sinceBy": "", "label": NO_PAUSE, "why": ""}
    if step is None:
        out["why"] = "стадия не из таблицы шагов"
        return out
    norm = NORM_WD.get(step)
    out["norm"] = norm
    if norm is None:
        out["why"] = (f"норма {doc[0]} — нет данных: {doc[2]}" if doc[1] == "nodata"
                      else "шаг не нормируется")
        return out
    if step != 12 and not order.get("stage"):
        out["why"] = "стадии нет — срок шага не посчитать"
        return out
    since, since_by = clock(order, step)
    if since is None:
        out["why"] = "нет факта доставки" if step == 12 else "нет даты входа в шаг"
        return out
    out["since"], out["sinceBy"] = since.isoformat(), since_by
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

    Приёмку вне «Реализации» не меряем, а шаг 4 — меряем у любой сделки, как у «Заказ
    направлен»: заказ такой сделки в «Заказ создан» с назначением менеджера ОРК (мягкая граница
    ASSIGN_FLOOR) — шаг 4 и в счёте (до 29.10.2026 был шагом 1 вне счёта).

    Шаг 12 (УПД) — не «сверх нормы» менеджера (решение владельца 27.10.2026, Б): больше 10 р.д.
    от доставки без УПД — waitUtd («ждём УПД от клиента»), over — False. Пункт noUtd очередь
    ставит по waitUtd; W2, ведомость (столбец «сверх нормы»), «Сверх нормы по менеджерам» и
    «Замедлено» — по over.

    Шаг 0 (решение владельца 31.10.2026) — тоже не «сверх нормы» ОРК: дольше 5 р.д. — preOver
    («передача в ОРК не подтверждена»), over — False. Пункт s0Over (PRE_CODE) очередь ставит по
    preOver; «Замедлено» сделки его учитывает (contracts, решение 31.10.2026, г), W2, ведомость
    (столбец «сверх нормы» шагов ОРК), «Сверх нормы по менеджерам» и Σ «Пульса» — нет.

    → overrun + {rawOver — over до отбора, hyg, hygWhy, counted — в счёте срока (over
    True/False), termUnknown — шаг с нормой в счёте, а срок не посчитать, waitUtd — ждём УПД от
    клиента, preOver — шаг 0 дольше нормы}."""
    ov = overrun(order, today, workdays_fn)
    hy = hygiene(order, today, workdays_fn)
    out = {**ov, "rawOver": ov["over"], "hyg": bool(hy["exclude"]), "hygWhy": hy["why"],
           "counted": False, "termUnknown": False, "waitUtd": False, "preOver": False}
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
    if ov["step"] in PRE_CODE and ov["over"]:
        # передача в ОРК не подтверждена дольше нормы — работа исполнителя задачи, не ОРК: в
        # «сверх нормы» ОРК не идёт (решение владельца 31.10.2026, п. 1)
        out.update(over=False, preOver=True)
    return out


# --- контрольные точки недели (шаг 7, п. 9.2.4 регламента) --------------------------------
# --- путь живого заказа по шагам («провалиться в процесс, где зависло», владелец 29.10.2026) --
# датированные факты, которые поднимают шаг (как FACT_FLOOR у step_now): поле → шаг не ниже.
# Без даты (оплата поставщику, подтверждение производства, файл УПД) — только у текущего шага
PATH_FACTS = (("shipFact", 9), ("inbFact", 9), ("delFact", 11), ("utdRaw", 13))


def path(order: dict, transitions: list, names: dict, today: dt.date, workdays_fn=None) -> list[list]:
    """Путь живого заказа по шагам регламента: [[шаг, день входа (ISO, МСК), р.д. на шаге], …]
    по времени — р.д. до входа в следующую запись, у последней — до today (None — года нет в
    календаре).

    Шаг в каждый момент — то же правило, что step_now сейчас: по стадии (все переходы истории,
    STAGE_STEP; стадия не из таблицы шаг не меняет), не ниже датированных фактов (PATH_FACTS:
    отгрузка или приход на склад — 9, доставка клиенту — 11, УПД — 13), доставлено без УПД —
    шаг 12; стадия шага 1 после назначения менеджера ОРК (assign_at) — шаг 4 (ASSIGN_FLOOR:
    приёмку закончило назначение); стадия шага 1 в дни, когда задача подтверждения передачи была
    открыта (confirm.spans, по дням МСК), — шаг 0. Соседние одинаковые шаги — одна запись; возврат
    — новая.

    Последняя запись — текущий шаг заказа (step_now). У шага с нормой — с днём, от которого
    меряется его срок (clock), — тем же, что stepFrom и stepWd строки заказа: путь и строка
    говорят одно (шаг 12 — от доставки, шаг 1 — от начала приёмки, шаг 4 — от назначения);
    записи, начатые позже этого дня, поглощаются им. У шага без нормы — вход по истории (факт
    поднял шаг — с дня факта), истории шага нет — clock. Истории нет — одна запись: текущий шаг от clock, без него — от создания
    заказа. Шага нет (step_now — None: стадия не из таблицы, доставки без УПД нет) — путь по истории
    до дня входа в эту стадию, последний известный шаг кончается в этот день, а последняя запись —
    [None, день входа, р.д.]: «стадия вне регламента», шаг и норма не выдумываются."""
    fn = workdays_fn or period_mod.workdays_ru
    msk = period_mod.MSK
    day = lambda d: dt.datetime(d.year, d.month, d.day, tzinfo=msk)   # noqa: E731
    tr = _sorted_tr(transitions)
    pts: list[tuple[dt.datetime, int | None]] = [(t, stage_step(names.get(s, s))) for s, t in tr]
    facts = [(day(d), fl, key) for key, fl in PATH_FACTS
             if (d := _d(order.get(key))) is not None and d <= today]
    asg = assign_at(order)
    asg_t = day(asg) if asg else None
    utd_t = next((t for t, _fl, k in facts if k == "utdRaw"), None)
    del_t = next((t for t, _fl, k in facts if k == "delFact"), None)
    # дни, когда задача подтверждения передачи была открыта: [(с, по или None — открыта)]
    spans = []
    for a, b in ((order.get("confirm") or {}).get("spans") or []):
        da, db = _d(a), _d(b)
        if da is not None and da <= today:
            spans.append((da, db if db is not None and db <= today else None))

    def at(t: dt.datetime) -> int | None:
        st = None
        for tt, s in pts:
            if tt > t:
                break
            if s is not None:
                st = s
        td = t.astimezone(msk).date()
        if st == 1 and any(a <= td and (b is None or td < b) for a, b in spans):
            st = PRE_STEP
        if st == 1 and asg_t is not None and asg_t <= t:
            st = ASSIGN_FLOOR
        fl = [f for tt, f, _k in facts if tt <= t]
        if fl:
            st = max(st if st is not None else 1, max(fl))
        if (st is None or st == 11) and del_t is not None and del_t <= t and not (utd_t and utd_t <= t):
            st = 12
        return st

    times = sorted({t for t, _ in pts} | {t for t, _f, _k in facts} | ({asg_t} if asg_t else set())
                   | {day(d) for sp in spans for d in sp if d is not None})
    out: list[list] = []                                   # [шаг, день]
    for t in times:
        st = at(t)
        if st is None or (out and out[-1][0] == st):
            continue
        out.append([st, t.astimezone(msk).date()])
    cur = step_now(order)
    if cur is None:
        # стадия не из таблицы шагов (и доставки без УПД нет): последний известный шаг кончается в
        # день входа в эту стадию, дальше — запись [None, день, р.д.] «стадия вне регламента», без
        # нормы и без выдуманного шага «→ сейчас» (ревью 30.10.2026)
        u = None
        for t, st in reversed(pts):
            if st is not None:
                break
            u = t
        u_day = (u.astimezone(msk).date() if u is not None
                 else _d(order.get("stageSince")) or _d(order.get("created")) or (out[-1][1] if out else today))
        out = [e for e in out if e[1] <= u_day]
        out.append([None, u_day])
    elif not (cur not in NORM_WD and out and out[-1][0] == cur):
        # шаг с нормой — день счёта срока (clock), как stepFrom; без нормы — вход по истории
        since = clock(order, cur)[0]
        if since is None:
            since = (out[-1][1] if out and out[-1][0] == cur
                     else _d(order.get("stepSince")) or _d(order.get("stageSince")) or _d(order.get("created")))
        if out and out[-1][0] == cur:
            out[-1][1] = since or out[-1][1]
        elif since is not None:
            out.append([cur, since])
        while len(out) > 1 and out[-2][1] > out[-1][1]:
            out.pop(-2)                                    # начатые позже дня счёта срока — в нём
    rows = []
    for i, (st, d) in enumerate(out):
        end = out[i + 1][1] if i + 1 < len(out) else today
        rows.append([st, d.isoformat(), fn(d, end)])
    return rows


def checkpoints(order: dict, week) -> list[dict]:
    """Контрольные точки производства, попадающие в неделю [пн, вс] даты `week`:
    P50 — prodStart + ½(prodEnd − prodStart), для всех заказов; P14 — (shipPlan, иначе
    prodEnd) − 14 дн., если производство дольше 30 дн. Заказ уже готов или отгружен
    (шаг ≥ 8 по стадии и фактам) — точек нет: контроль производства пройден."""
    w = _d(week)
    mon = w - dt.timedelta(days=w.weekday())
    sun = mon + dt.timedelta(days=6)
    return [{"kind": k, "date": d.isoformat()} for k, d in checkpoint_dates(order) if mon <= d <= sun]


def checkpoint_dates(order: dict) -> list[tuple[str, dt.date]]:
    """Все контрольные точки производства заказа — [(P50 | P14, дата)], без окна недели: то же
    правило, что у checkpoints (одна функция). Карточка заказа берёт из них ближайшую
    («Следующая проверка»), неделя — попавшие в неделю. Готов или отгружен — точек нет."""
    ps, pe = _d(order.get("prodStart")), _d(order.get("prodEnd"))
    if not ps or not pe or pe < ps:
        return []
    if any(order.get(k) for k in ("shipFact", "inbFact", "delFact")):
        return []
    st = step_of(order.get("stage"), order) if order.get("stage") else None
    if st is not None and st >= 8:
        return []
    pts = [("P50", ps + dt.timedelta(days=int((pe - ps).days * CHECKPOINT["half"])))]
    if (pe - ps).days > CHECKPOINT["long_days"]:
        base = _d(order.get("shipPlan")) or pe
        pts.append(("P14", base - dt.timedelta(days=CHECKPOINT["before_days"])))
    return pts


# --- гигиена карточки ----------------------------------------------------------------------
def hygiene(order: dict, today: dt.date, workdays_fn=None) -> dict:
    """Карточка, которой нельзя верить в счёте: стадия позади фактов (оплачено, а стадия до
    шага 6; отгружено, а стадия до 9; доставлено, а до 11), > 60 р.д. на шаге с нормой ≤ 2 —
    «брошенная карточка», доставлено больше CLOSE_DAYS дней назад без УПД — «старая доставка
    без УПД» (пробел учёта, а не срок менеджера). Такие заказы уходят в блок «гигиена» и в
    счёт «сверх нормы» и сегментов не идут. Срок УПД (шаг 12) идёт от факта доставки:
    отставшая стадия его не портит — у шага 12 исключает только старая доставка.
    Вход в шаг раньше первого года календаря — нижняя граница р.д. от 1 января этого года.
    «Заказ создан» с назначением менеджера ОРК (мягкая граница ASSIGN_FLOOR) — не «позади
    фактов» (fact_floor её не знает): шаг 4, «брошенная» меряется на нём от назначения (clock) —
    назначение и есть последнее движение карточки. Шаг по стадии (в том числе «Заказ направлен»)
    — застой карточки от последнего движения стадии (stepSince), а не от назначения: срок шага 4
    (overrun) у направленного заказа идёт от назначения, но карточку, которую двигали 7 р.д.
    назад, брошенной не назовёшь.
    Шаг 0 (задача подтверждения передачи открыта) меряется от создания задачи (clock) при норме 5
    р.д. — брошенной на нём не бывает. confirmOpen — задача открыта, а заказ по стадии или фактам
    уже не на шаге 0 (решение владельца 31.10.2026, а): замечание, из счёта срока не исключает.
    → {behind, abandoned, old, unknown, confirmOpen, exclude, why}."""
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
    # шаг карточки — по стадии; при стадии шага 1 и назначении — шаг 4, при открытой задаче
    # подтверждения — шаг 0 (step_now)
    eff = now if (base == 1 and now in (PRE_STEP, ASSIGN_FLOOR)) else base
    norm = NORM_WD.get(eff) if eff is not None else None
    if norm is not None and norm <= GATES["abandoned_norm_max"] and not behind:
        # мягкая граница — от назначения (clock); шаг по стадии — от последнего движения стадии
        since = (clock(order, eff)[0] if eff != base else
                 (_d(order.get("t0")) if base == 1 else None) or _d(order.get("stepSince"))
                 or _d(order.get("stageSince")))
        wd = fn(since, today) if since else None
        if wd is None and since:
            years = period_mod.workcal_years()
            y0 = min(years) if years else None
            if y0 and since.year < y0:
                lo = fn(dt.date(y0, 1, 1), today)     # нижняя граница: календаря на год входа нет
                wd = lo if lo is not None and lo > GATES["abandoned_wd"] else None
        if wd is not None and wd > GATES["abandoned_wd"]:
            abandoned = True
            why.append(f"{wd} р.д. на шаге {eff} при норме {norm}")
    df = _d(order.get("delFact"))
    if now == 12 and df and (today - df).days > CLOSE_DAYS:
        old = True
        why.append(f"старая доставка без УПД: {(today - df).days} дн. назад (> {CLOSE_DAYS})")
    # задача подтверждения открыта, а стадия или факты увели заказ с шага 0 — замечание
    confirm_left = confirm_open(order) and now != PRE_STEP
    if confirm_left:
        why.append("задача подтверждения передачи открыта, а заказ уже на шаге "
                   + (str(now) if now is not None else "вне таблицы"))
    exclude = old or ((behind or abandoned) and now != 12)
    return {"behind": behind, "abandoned": abandoned, "old": old,
            "unknown": base is None and bool(order.get("stage")),
            "confirmOpen": confirm_left, "exclude": exclude, "why": "; ".join(why)}


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
# роль → поля people, из которых берётся человек роли, по порядку (первое непустое). Одна таблица у
# подписи (owner) и у id человека (role_id): «Смотреть как» (R4, Д-11а) отбирает пункты по id, и id
# обязан быть id того же человека, чьё имя показано. Логистика и поставщик — подписи ролей, без id.
# Менеджер ОРК: ответственный заказа из ОРК (orkMgr), иначе «Ответственный за реализацию» сделки
# (dealOrk; руководитель ОРК в этом поле менеджером не считается — contracts, решение владельца
# 31.10.2026), иначе ответственный заказа не из ОРК (resp): заказ не передан в ОРК, исполнитель шага
# — он, с пометкой (owner: out), руководителю ОРК — принять заказ и назначить менеджера ОРК (OUT_ACT)
ROLE_KEYS = {"orkMgr": ("orkMgr", "dealOrk", "resp"), "orkHead": ("orkHead",), "kam": ("kam",),
             "oss": ("ossOrder", "ossDeal"), "log": (), "sup": (), "confirmResp": ("confirmResp",)}
# исполнитель шага — ответственный заказа не из ОРК (решение владельца 31.10.2026, заказ 968/2)
OUT_MARK = "не из ОРК — заказ не передан в ОРК"
OUT_ACT = "принять заказ и назначить менеджера ОРК"


def role_id(role: str | None, people: dict, ids: dict) -> str:
    """id Битрикса человека роли: id того поля people, чьё имя выбрал owner (ROLE_KEYS), иначе ''.
    ids — те же ключи, что у people (orkMgr, dealOrk, resp, orkHead, kam, ossOrder, ossDeal,
    confirmResp)."""
    for k in ROLE_KEYS.get(role or "", ()):
        if people.get(k) not in (None, "", "—"):
            v = ids.get(k)
            return str(v) if v not in (None, "", "0", "—") else ""
    return ""


def owner(step: int | None, people: dict, *, debt: bool = False, esc=_ESC_STEP,
          role: str | None = None, pre: bool = False) -> dict:
    """Хозяин пункта очереди — исполнитель шага. people — уже найденные люди заказа и
    сделки: orkMgr (ответственный заказа, если он из отдела ОРК), dealOrk (поле сделки
    «Ответственный за реализацию», не руководитель ОРК), resp (ответственный заказа не из ОРК),
    orkHead (руководитель отдела ОРК), kam, ossOrder, ossDeal, confirmResp (исполнитель задачи
    подтверждения передачи, шаг 0). Долг клиента (debt) — КАМ, ОСС в копии. esc — роль в копии
    для пункта со своей эскалацией (по умолчанию — STEP[шаг]['esc']); role — роль хозяина, если
    пункт задаёт её сам (ITEM), иначе исполнитель шага. → {role, who, copy, copyRole, out?}; who
    — «не назначен», если человека роли нет (логистика — подпись роли).

    out (решение владельца 31.10.2026, заказ 968/2): менеджера ОРК нет, исполнитель — ответственный
    заказа не из ОРК (заказ не передан в ОРК, OUT_MARK); копии у пункта нет — в копии руководитель
    ОРК (ему — OUT_ACT); у пункта своя копия — руководитель ОРК вторым в копии (alsoCopy,
    alsoCopyRole).

    pre — заказ на шаге 0 (PRE_STEP: задача подтверждения передачи открыта, решение владельца
    31.10.2026, пп. 1–2): заказ ещё не в ОРК — пункт, который регламент отдаёт менеджеру ОРК (срок
    клиента, производство, чат, договор поставщика), делает исполнитель задачи подтверждения, КАМ
    сделки — в копии; «принять заказ и назначить менеджера ОРК» (out) до закрытия задачи не ставится."""
    def nm(*keys):
        for k in keys:
            v = people.get(k)
            if v not in (None, "", "—"):
                return v
        return ""

    find = {"orkMgr": lambda: nm(*ROLE_KEYS["orkMgr"]),
            "orkHead": lambda: nm(*ROLE_KEYS["orkHead"]) or ROLES["orkHead"],
            "kam": lambda: nm(*ROLE_KEYS["kam"]),
            "oss": lambda: nm(*ROLE_KEYS["oss"]),
            "log": lambda: ROLES["log"],
            "sup": lambda: ROLES["sup"],
            "confirmResp": lambda: nm(*ROLE_KEYS["confirmResp"])}
    if debt:
        role, copy_role = "kam", "oss"
    else:
        role = role or (EXECUTOR.get(step) if step is not None else "orkHead")
        copy_role = (STEP[step]["esc"] if step is not None else None) if esc is _ESC_STEP else esc
        if pre and role == "orkMgr":
            role, copy_role = EXECUTOR[PRE_STEP], STEP[PRE_STEP]["esc"]
        if copy_role == role:
            copy_role = None
    who = find[role]() or "не назначен"
    # менеджера ОРК нет ни у заказа, ни у сделки — исполнитель ответственный заказа не из ОРК
    out = not pre and role == "orkMgr" and not nm("orkMgr", "dealOrk") and bool(nm("resp"))
    if out and not copy_role:
        copy_role = "orkHead"
    copy = (find[copy_role]() or "не назначен") if copy_role else ""
    res = {"role": role, "who": who, "copy": copy, "copyRole": copy_role or ""}
    if out:
        res["out"] = True
        # у пункта своя копия (КАМ: срок клиента, производство) — руководитель ОРК вторым в копии:
        # ему — OUT_ACT, и «Смотреть как» руководителя ОРК пункт не теряет
        if copy_role != "orkHead":
            res["alsoCopy"], res["alsoCopyRole"] = find["orkHead"](), "orkHead"
    return res


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
    # приёмка — одна на сделку (решение владельца 30.10.2026, п. 1): пункт сделки у руководителя
    # ОРК, КАМ в копии; заказ, добавленный в уже принятую сделку, — пункт заказа ordAssign
    # «назначить менеджера ОРК на новый заказ» у руководителя ОРК, без КАМа (не разговор с клиентом)
    "s1Over": (1, "orkHead", "kam"),
    "ordAssign": (1, "orkHead", None),
    # шаг 0 (решение владельца 31.10.2026): передача в ОРК не подтверждена дольше 5 р.д. — хозяин
    # исполнитель задачи подтверждения, КАМ сделки в копии; задача открыта, а заказ уже дальше шага
    # 0 по стадии или фактам — порядок в карточке: закрыть задачу (исполнитель), руководитель ОРК в
    # копии (стадию двигает ОРК)
    "s0Over": (0, "confirmResp", "kam"),
    "confirmOpen": (None, "confirmResp", "orkHead"),
    "s2Over": (4, "orkMgr", "orkHead"),
    "s3Over": (5, "orkMgr", "orkHead"),
    "s6Over": (8, "orkMgr", "orkHead"),
    "payNoContract": (5, "orkHead", "kam"),
    "gone": (1, "orkHead", None),
    # уволен ответственный или ОСС заказа — пункт заказа (единица — заказ, номер в строке)
    "goneOrd": (None, "orkHead", None),
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
    # «главное» по сделке (dealctx.IMP_OWNER, решение владельца 25.10.2026) — оценка модели, не
    # пункт очереди; хозяин — тем же правилом: срок клиента и поставщик — менеджер ОРК (КАМ в
    # копии), УПД — шаг 12 менеджера ОРК, деньги и недовольство клиента — КАМ, договор и
    # решение — руководитель ОРК. ОСС не получает (Рег. 1.11). Расхождение суммы и «иное» —
    # ответственный сделки (dealResp, вне регламента)
    "imp:срыв_срока_клиента": (None, "orkMgr", "kam"),
    "imp:поставщик": (None, "orkMgr", "kam"),
    "imp:документы_упд": (12, "orkMgr", "kam"),
    "imp:деньги_оплата": (13, "kam", None),
    "imp:клиент_недоволен": (None, "kam", None),
    "imp:договор_условия": (None, "orkHead", "kam"),
    "imp:решение_руководителя": (None, "orkHead", None),
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
# срок шага до передачи в ОРК → код пункта (решение владельца 31.10.2026): шаг 0 дольше 5 р.д. —
# передача не подтверждена (judged.preOver); не «сверх нормы» ОРК: OVER_CODE и STEP_CODES (W2,
# «Сверх нормы по менеджерам», noCalendar, lifecycle.over_norm) его не знают
PRE_CODE = {0: "s0Over"}
PRE_WHAT = "передача в ОРК не подтверждена"
# все сроки шагов регламента в р.д. → код пункта очереди (inWd у пункта). Шаг 1 — ПЕРВАЯ приёмка
# сделки (s1Over, пункт сделки: решение владельца 30.10.2026, п. 1)
TERM_CODE = {**OVER_CODE, **WAIT_CODE, **PRE_CODE}
# тот же срок шага 1 у заказа, добавленного в уже принятую сделку: назначить менеджера ОРК на
# новый заказ (код → шаг). Дни — рабочие, как у TERM_CODE
TERM_EXTRA = {"ordAssign": 1}
# коды пунктов со сроком шага в р.д. (inWd, «с дд.мм (…)»)
TERM_CODES = frozenset(TERM_CODE.values()) | frozenset(TERM_EXTRA)
# шаг «сверх нормы» → все коды пунктов этого срока (W2, «Сверх нормы по менеджерам»): шаг 1 —
# приёмка сделки (s1Over) и назначение менеджера ОРК на новый заказ (ordAssign)
STEP_CODES = {st: (code, *(c for c, s in TERM_EXTRA.items() if s == st)) for st, code in OVER_CODE.items()}


def item_owner(code: str, step_now: int | None, people: dict, *, key: str | None = None) -> dict:
    """Хозяин пункта очереди по таблице ITEM: {step, role, who, copy, copyRole}. key —
    строка ITEM с уточнением (вид срока из чата, сторона договора), нет её — по коду. Код не
    из таблицы — исполнитель текущего шага и его эскалация. step_now — шаг заказа сейчас: шаг 0
    — пункт менеджера ОРК у исполнителя задачи подтверждения (owner: pre)."""
    k = key if key in ITEM else code
    st, role, copy = ITEM.get(k, (None, None, _ESC_STEP))
    step = st if st is not None else step_now
    out = owner(step, people, role=role, esc=copy if k in ITEM else _ESC_STEP, pre=step_now == PRE_STEP)
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


def passed_at(o: dict, step: int, reach: dict | None = None) -> dt.date | None:
    """День (МСК) первого выхода заказа на шаг не ниже `step`: по переходам (reach — {шаг: ISO},
    без него — o["_reach"]), а на шагах 2–ASSIGN_FLOOR — назначение менеджера ОРК (assign_at),
    если оно раньше: то же правило, что у конца S1 и начала S2 (segments). Одна дата у ведомости
    недели (art_required) и полноты карточки «Месяца» (M2)."""
    r = (o.get("_reach") if reach is None else reach) or {}
    at = _d(r.get(step) or r.get(str(step)))
    if 2 <= step <= ASSIGN_FLOOR:
        a = assign_at(o)
        if a and (at is None or a < at):
            return a
    return at


def art_required(o: dict, a: dict, reach: dict | None = None) -> bool:
    """Обязан ли заказ иметь артефакт `a` (ARTIFACTS): заказ прошёл шаг need (по стадии и
    фактам) ПОСЛЕ даты создания поля (дизайн: «артефакт требуется только у заказов, прошедших
    шаг после даты создания поля»), условие выполнено.

    Дата прохождения — passed_at: первый выход на шаг need по переходам (reach — {шаг: ISO}), а
    на шагах 2–4 — и назначение менеджера ОРК, если оно раньше (мягкая граница ASSIGN_FLOOR: тот
    же шаг, что у step_now, и та же дата, что у «Месяца», M2). Её нет (шаг пройден по фактам,
    история его не дала) — требуем, только если заказ создан не раньше поля: тогда шаг пройден
    заведомо после; заказ старше поля или без даты создания — не требуем (неизвестно — не «нет
    артефакта»). OC — только у импорта: o["import"] is True
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
    at = passed_at(o, a["need"], reach)
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
