#!/usr/bin/env python3
"""Зонд v60 «Реализация по регламенту ОРК»: чем портал подтверждает 13 шагов регламента
и сегменты S1–S10 «Системы аналитики ОРК» — до того, как вкладка «Реализация» покажет
по ним числа руководителю ОРК.

ПОВОД (26.10.2026). Вкладка «Реализация» пересобирается по регламенту ОРК (ядро — ork.py,
календарь — period.workdays_ru). Решения владельца 26.10.2026 (docs/agents/DECISIONS.md):
хозяин пункта — исполнитель шага; нормы — регламент в рабочих днях по календарю РФ
(S1 1, S2 2, шаг 5 ≤ 9, S6 2, УПД 10 р.д.; S5 и S7 — медиана и P80); менеджер ОРК в
людских показателях — ответственный заказа из отдела ОРК, заказы с 24.08.2026. Пороги
доверия (ork.GATES) — предложение, ждут решения владельца: зонд меряет, пройдёт ли их
каждый показатель, и печатает вердикт SHOW / PRELIM / HIDE.

ЧТО ПЕЧАТАЕТ (раздел «Зонд v60» итогового проекта):
   1. покрытие производственным календарём;
   2. границы S1–S10: заполненность опор по полугодиям создания заказа и совпадение двух
      опор в пределах 1 р.д.;
   3. сегменты S1–S10: n, пропуски с причиной, медиана и P80 в рабочих и календарных днях,
      доля в норме («по стадии, без пауз»), за 365 дней и по направлению поставки;
   4. порядок против регламента: возвраты, «пачки», пропуск шагов, оплата поставщику
      раньше договора с заказчиком, «Таможня» по направлению, стадия сделки впереди или
      позади её заказов;
   5. подписи «Направления поставки» и «Supply end point» (белый список, счёт);
   6. живые заказы: ответственный не из отдела ОРК; совпадение с полем сделки
      «Ответственный за реализацию»; загрузка без имён;
   7. UTD_DATE пусто, а файл УПД есть;
   8. «план = факт» по PROD_END и SHIP_PLAN;
   9. поставщики с ≥ 5 заказами за квартал;
  10. СП-137: создание → оплачено, и что значит «Дата оплаты счета» — двумя способами;
  11. СП-1066 и СП-1174/1192: стадии и доля связей;
  12. право вебхука на tasks.* (отказ — одной строкой);
  13. вердикт по каждому показателю (ork.metric_state, ворота ork.GATES).

ЧАСТИ. Вход PROBE_PARTS ∈ {10, 25, 50}, меньше 10 — 10 (распоряжение 22.09). Разбиение
одно — по ID сделки смежными срезами: все заказы сделки в одной части. Заказы без сделки
только считаются. Упавшая часть даёт строку «частей прочитано k из n»; когорта (полугодие
создания), задетая упавшей частью, из выводов уходит целиком — срезы по ID идут со временем.

ЖУРНАЛ ПУБЛИЧНЫЙ (правило 17, распоряжение 30.09): счёт, доли, медианы и P80, номера шагов,
коды полей, подписи значений только из белого списка и только похожие на подпись (иначе
«вариант k»), имена стадий смарт-процессов — так же. Ни людей, ни компаний, ни сделок, ни
заказов, ни сумм, ни текста ошибок портала — у отказа только код, у падения — тип и место.

НАГРУЗКА: ≈ 830 запросов при 1/с (probe.yml), предел 1 100: часть, которой не хватает
сметы, не читается вслух; необязательные блоки (история счетов, задачи) — тоже. Первая
строка — «ожидается: …», последняя — сводка_нагрузки().
"""
from __future__ import annotations

import collections
import contextlib
import datetime as dt
import math
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bitrix_client as bc  # noqa: E402
import ork  # noqa: E402
import period as period_mod  # noqa: E402
import probe_conversion as pc  # noqa: E402

MSK = period_mod.MSK

# ── сущности и поля ─────────────────────────────────────────────────────────
ORDER_ENTITY, INVOICE_ENTITY, CHECK_ENTITY = 172, 137, 1066
CONTRACT_ENTITIES = (1174, 1192)
HIST_SINCE = "2024-06-01T00:00:00"       # contracts.HIST_SINCE — глубина истории стадий
ORK_SINCE = dt.date(2026, 8, 24)         # роботы ОРК; менеджер ОРК в людских показателях — с этой даты
Q3_SINCE = dt.date(2026, 4, 2)           # «Доля первичной оплаты» заведена 02.04.2026

# заказ СП-172 (зонды v49, v53; contracts.py)
З = {
    "dl": "ufCrm20_1728900218435",       # Date of deadline to customer
    "prodStart": "ufCrm20_1724941908",   # Production start date, budget
    "prodEnd": "ufCrm20_1724941935",     # Production end date, budget
    "shipPlan": "ufCrm20_1723235828",    # Supplier Shipment Date, planned
    "shipFact": "ufCrm20_1723236324",    # Supplier Shipment Date, actual
    "inbFact": "ufCrm20_1723236306",     # Inbound Delivery Date, actual
    "delFact": "ufCrm20_1723236501",     # Customer delivery date, actual
    "utdDate": "ufCrm20_1723236848",     # Date of signing of the UTD — сырой, без подстановки
    "utdFile": "ufCrm20_1749654314976",  # Universal Transfer Document (UTD) — файл
    "supPaid": "ufCrm20_1755759056275",  # Оплачено поставщику, %
    "prodConf": "ufCrm20_1723236426",    # Production confirmation — файл
    "prodConfNf": "ufCrm20_1723372033545",  # Production confirmation, no file — флажок
    "dir": "ufCrm20_1769424743906",      # Направление поставки
    "kam": "ufCrm20_1781171449",         # КАМ заказа
    "oss": "ufCrm20_1723236618",         # Deal support specialist
    "respDate": "ufCrm20_1787924508120",  # Дата записи нового ответственного (с 28.08.2026)
    "respInit": "ufCrm20_1787561031",    # Изначальный ответственный по заказу
    "taskAssign": "ufCrm20_1787561355",  # ID задачи «Назначить менеджера ОРК»
    "taskStatus": "ufCrm20_1787572106",  # ID задачи «Актуализировать статус производства»
    "firstPay": "ufCrm20_1775138654290",  # Доля первичной оплаты, %
    "done": "ufCrm20_1783458705227",     # Дата завершения карточки Заказа
}
# сделка кат. 0
Д = {
    "orkField": "UF_CRM_1781858627",     # Ответственный за реализацию
    "kam": "UF_CRM_1740390857",          # КАМ
    "oss": "UF_CRM_1715604558",          # Сопровождение сделки
    "moved": "UF_CRM_1738690462874",     # Дата переноса в воронку Реализация
    "endPoint": "UF_CRM_1769424979",     # Supply end point
    "supContract": "UF_CRM_1720337289483",  # веха «договор с поставщиком»
    "supPaid": "UF_CRM_1720337304531",   # веха «оплата поставщику»
    "invPaid": "UF_CRM_1717489142010",   # Счёт оплачен (дата)
    "confirm": "UF_CRM_1720337312884",   # веха «подтверждение получено»
    "produced": "UF_CRM_1720337321894",  # веха «товар произведён»
    "shipped": "UF_CRM_1720337330743",   # веха «товар отгружен»
    "delivered": "UF_CRM_1720337338894",  # веха «доставлен»
}
# счёт на оплату СП-137 (зонд v54)
INV_TYPE = "ufCrm24_1712230412"          # Тип оплаты
INV_PAY_DATE = "ufCrm24_1712231249"      # Дата оплаты счета
INV_ORDERS = "ufCrm24_1733863187"        # Заказы поставщикам (множ)
CHECK_COMPANY = "ufCrm36_1738688380"     # СП-1066: компания

ORDER_SELECT = (["id", "parentId2", "parentId1174", "stageId", "createdTime", "movedTime", "assignedById",
                 "companyId"] + list(З.values()) + [a["field"] for a in ork.ARTIFACTS])
DEAL_SELECT = ["ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "CATEGORY_ID", "DATE_CREATE", "ASSIGNED_BY_ID"] + list(Д.values())

# Метки значений — только у полей белого списка и только похожие на подпись: в метках
# списков бывают имена компаний («Схема поставки» в журнал не идёт вовсе).
БЕЛЫЙ_СПИСОК = {З["dir"]: "Направление поставки", Д["endPoint"]: "Supply end point", INV_TYPE: "Тип оплаты"}
МЕТКА_RX = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё .,/()%+\-–]{0,59}")
ЛИЦО_RX = re.compile(r"\b(ооо|оао|зао|пао|ао|ип|тоо|llc|ltd|limited|fze|fzco|gmbh|inc|corp|ag|bv|spa)\b", re.I)
ОТДЕЛ_ОРК_RX = re.compile(r"реализаци\w*\s+контракт|\bОРК\b", re.I)
КОД_ОШИБКИ_RX = re.compile(r"^[\w.]+: ([A-Za-z][A-Za-z0-9_]{2,40})\b")
СКЛАДСКИЕ = ("Отгрузка", "Таможня", "Доставка до склада", "Приемка на складе")
ТАМОЖНЯ = "Таможня"

# Смета (правило 4: числа — таблицей). Единицы — запросов; «на 50» — на пачку из 50 записей.
БЮДЖЕТ = {"ожидается": 830, "предел": 1100}
СМЕТА = {
    "основа": 20,                  # счёт, справочники стадий и полей, отдел ОРК
    "список_на_50": 1,             # заказы и сделки списком по ключу
    "история_заказов_на_50": 14,   # ≈ 10–15 переходов на заказ
    "история_сделок_на_50": 8,
    "сделки_вне_кат0_на_50": 1,
    "задачи_на_50": 1,
    "договоры_и_проверки": 30,     # СП-1174, СП-1192, СП-1066
    # СП-137 с 01.06.2024: всего счетов ~6,3 тыс. (навык contract-review) — до ~127 страниц по 50
    "счета_137": 130,
    "история_137": 4,
}
ЧАСТИ = (10, 25, 50)
СЧЕТОВ_В_ВЫБОРКЕ = 100             # «Дата оплаты счета»: выборка истории оплаченных
# что значит «Дата оплаты счета» (правило 4)
ДАТА_ОПЛАТЫ = {"факт_совпадение": 0.90, "план_у_неоплаченных": 0.30}
МИН_ПАР_ОПОР = 8                   # меньше пар — совпадение опор не меряется (как ячейка)
ДНЕЙ_ГОД = 365
КВАРТАЛОВ = 4
ДОПУСК_ЗАГРУЗКИ = 0.25             # ±25 % от средней загрузки менеджеров ОРК (Ан. §4)
МИН_ЛЮДЕЙ = 3                      # меньше людей — мин/медиана/макс загрузки в журнал не пишем (нагрузка человека)

# Границы сегментов: (код, подпись, шаг — заказ прошёл точку, основная опора, вторые опоры).
# Опоры «deal_*» — поля сделки: сверяются по сделке с ранней из её заказов.
ГРАНИЦЫ = (
    ("S1.начало", "приёмка начата", 1, "t0_hist", ("t0_field",)),
    ("S1.конец", "менеджер назначен (выход из «Заказ создан»)", 2, "reach2", ("resp_date", "task_assign")),
    ("S2.начало", "заказ направлен", 4, "reach4", ()),
    ("S2.конец", "договор с поставщиком", 5, "reach5", ("deal_supContract",)),
    ("S3.конец", "оплата поставщику", 6, "reach6", ("deal_supPaid", "deal_invPaid")),
    ("S4.конец", "производство начато", 7, "reach7", ("deal_confirm",)),
    ("S5.конец", "заказ готов", 8, "reach8", ("deal_produced",)),
    ("S6.конец", "передан к отгрузке", 9, "reach9", ("ship_fact", "deal_shipped")),
    ("S7.конец", "склад пройден, поиск транспорта", 10, "reach10", ("inb_fact",)),
    ("S8.конец", "доставлено клиенту", 11, "reach11", ("del_fact", "deal_delivered")),
    ("S9.конец", "УПД подписан", 13, "utd_date", ()),
    ("S10.конец", "оплата клиента, закрытие", 13, "reach13", ("deal_paidEntry", "done_date")),
)
ОПОРЫ = {
    "t0_hist": "max(создание заказа, вход сделки в кат. 0 по истории)",
    "t0_field": "max(создание заказа, «Дата переноса в воронку Реализация»)",
    "resp_date": "«Дата записи нового ответственного»", "task_assign": "закрытие задачи «Назначить менеджера ОРК»",
    "deal_supContract": "веха сделки «договор с поставщиком»", "deal_supPaid": "веха сделки «оплата поставщику»",
    "deal_invPaid": "«Счёт оплачен (дата)» сделки", "deal_confirm": "веха сделки «подтверждение»",
    "deal_produced": "веха сделки «произведён»", "ship_fact": "Supplier Shipment Date, actual",
    "deal_shipped": "веха сделки «отгружен»", "inb_fact": "Inbound Delivery Date, actual",
    "del_fact": "Customer delivery date, actual", "deal_delivered": "веха сделки «доставлен»",
    "utd_date": "Date of signing of the UTD", "deal_paidEntry": "вход сделки в «Оплата получена»",
    "done_date": "«Дата завершения карточки Заказа»",
}
for _k in range(2, 14):
    ОПОРЫ[f"reach{_k}"] = f"первый выход заказа на шаг {_k} (история стадий)"


class ОшибкаЧасти(RuntimeError):
    """Шаг части не прошёл проверку полноты; сообщение — константа кода."""


# ── чистые функции ──────────────────────────────────────────────────────────

def доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else f"{a} из 0"


def пр(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f} %"


def мед(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def p80(xs) -> float | None:
    """P80 = s[⌈0,8·n⌉ − 1] (итоговый проект, «Показатели»)."""
    s = sorted(x for x in xs if x is not None)
    return s[math.ceil(0.8 * len(s)) - 1] if s else None


def чс(x) -> str:
    return "—" if x is None else f"{x:g}" if isinstance(x, int) or float(x).is_integer() else f"{x:.1f}"


def счёт(c: collections.Counter, порядок=()) -> str:
    ключи = [k for k in порядок if c.get(k)] + [k for k, _ in c.most_common() if k not in порядок]
    return " · ".join(f"{k} {c[k]}" for k in ключи if c.get(k)) or "—"


def _вр(v) -> dt.datetime | None:
    """Метка портала → datetime с поясом (без пояса — МСК); пусто и мусор — None."""
    if isinstance(v, list):
        return _вр(v[0]) if v else None
    if v in (None, "", 0, "0", False):
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=MSK)
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day, tzinfo=MSK)
    s = str(v).strip()
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        m = re.match(r"(\d{4}-\d\d-\d\d)", s)
        if not m:
            return None
        t = dt.datetime.fromisoformat(m.group(1))
    return t if t.tzinfo else t.replace(tzinfo=MSK)


def _дн(v) -> dt.date | None:
    t = _вр(v)
    return t.astimezone(MSK).date() if t else None


def _есть(v) -> bool:
    if isinstance(v, list):
        return any(_есть(x) for x in v)
    if isinstance(v, dict):
        return bool(v)
    return v not in (None, "", 0, "0", False, "N", "0.0")


def _ч(v) -> float:
    try:
        return float(str(v).split("|")[0].replace(",", ".").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _uid(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else None
    s = str(v or "").strip()
    return s if s.isdigit() and s != "0" else ""


def _id_из(v) -> str:
    """Привязка к компании в поле смарт-процесса: 123, «CO_123», [«CO_123»] → «123»."""
    if isinstance(v, list):
        v = v[0] if v else None
    m = re.search(r"(\d+)$", str(v or "").strip())
    return m.group(1) if m and m.group(1) != "0" else ""


def полугодие(t: dt.datetime | dt.date | None) -> str:
    if t is None:
        return "—"
    d = t.astimezone(MSK).date() if isinstance(t, dt.datetime) else t
    return f"{d.year}-П{1 if d.month <= 6 else 2}"


def квартал(d: dt.date) -> str:
    return f"{d.year}-К{(d.month - 1) // 3 + 1}"


def последние_кварталы(сегодня: dt.date, n: int = КВАРТАЛОВ) -> list[str]:
    y, q = сегодня.year, (сегодня.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append(f"{y}-К{q}")
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out[::-1]


def метка(поле: str, знач, подписи: dict[str, str]) -> str:
    """Подпись значения списка для журнала: только у полей белого списка и только похожая
    на подпись (буквы, цифры, знаки, без формы юрлица); иначе «вариант k» по номеру."""
    if isinstance(знач, list):
        знач = знач[0] if знач else None
    if знач in (None, "", 0, "0"):
        return "пусто"
    k = str(знач)
    if k not in подписи:
        return "нет в справочнике"
    номер = sorted(подписи, key=lambda x: (len(x), x)).index(k) + 1
    txt = re.sub(r"\s+", " ", str(подписи[k] or "")).strip()
    if поле in БЕЛЫЙ_СПИСОК and _похоже_на_подпись(txt):
        return txt
    return f"вариант {номер}"


def _похоже_на_подпись(txt: str) -> bool:
    return bool(МЕТКА_RX.fullmatch(txt)) and not ЛИЦО_RX.search(txt)


def имя_стадии(name) -> str:
    """Имя стадии смарт-процесса для журнала — как метка: похожее на подпись или «скрыто»."""
    txt = re.sub(r"\s+", " ", str(name or "")).strip()
    return txt if _похоже_на_подпись(txt) else "(имя скрыто)"


def код_ошибки(e: BaseException) -> str:
    """Код отказа портала («ACCESS_DENIED») без описания: в описании бывают имена."""
    m = КОД_ОШИБКИ_RX.match(str(e))
    return m.group(1) if m else type(e).__name__


def место(e: BaseException) -> str:
    места = []
    tb = e.__traceback__
    while tb:
        места.append(f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}")
        tb = tb.tb_next
    return " → ".join(места[-3:])


def частей() -> tuple[int, str]:
    """PROBE_PARTS → (число частей, замечание): 10, 25 или 50; меньше 10 — 10; между —
    ближайшее большее из допустимых; больше 50 — 50."""
    сырое = os.environ.get("PROBE_PARTS") or str(ЧАСТИ[0])
    try:
        n = int(сырое)
    except ValueError:
        return ЧАСТИ[0], f"PROBE_PARTS={сырое!r} не число — взято {ЧАСТИ[0]}"
    if n < ЧАСТИ[0]:
        return ЧАСТИ[0], f"PROBE_PARTS={n} меньше {ЧАСТИ[0]} — взято {ЧАСТИ[0]} (распоряжение 22.09)"
    if n in ЧАСТИ:
        return n, ""
    взято = next((k for k in ЧАСТИ if k >= n), ЧАСТИ[-1])
    return взято, f"PROBE_PARTS={n} не из {ЧАСТИ} — взято {взято}"


def разбить(сделки: list[str], n: int) -> list[list[str]]:
    """ID сделок → n смежных срезов по возрастанию номера (все заказы сделки — в её срезе)."""
    ids = sorted({str(x) for x in сделки}, key=int)
    if not ids:
        return []
    шаг = max(1, math.ceil(len(ids) / n))
    return [ids[i:i + шаг] for i in range(0, len(ids), шаг)]


def смета(заказов: int, сделок: int, частей_n: int) -> int:
    п50 = lambda x: math.ceil(x / 50)  # noqa: E731
    return (СМЕТА["основа"] + СМЕТА["список_на_50"] * (п50(заказов) + п50(сделок))
            + СМЕТА["история_заказов_на_50"] * п50(заказов) + СМЕТА["история_сделок_на_50"] * п50(сделок)
            + частей_n + СМЕТА["задачи_на_50"] * п50(заказов * 0.3) + СМЕТА["договоры_и_проверки"]
            + СМЕТА["счета_137"] + СМЕТА["история_137"])


def смета_части(заказов: int, сделок: int) -> int:
    п50 = lambda x: math.ceil(x / 50)  # noqa: E731
    return (СМЕТА["история_заказов_на_50"] * п50(заказов) + СМЕТА["история_сделок_на_50"] * п50(сделок)
            + СМЕТА["сделки_вне_кат0_на_50"] + СМЕТА["задачи_на_50"] * п50(заказов * 0.3))


def достижения(transitions: list, names: dict, kind: str = "order") -> tuple[dict, dict]:
    """Все переходы → ({шаг: время первого выхода на шаг не ниже}, {шаг: № перехода}).
    Та же граница, что у ork.segments: пропущенная стадия не теряет шаг (тест сверяет)."""
    tr = sorted(((s, _вр(t)) for s, t in transitions if _вр(t)), key=lambda x: x[1])
    reach: dict[int, dt.datetime] = {}
    idx: dict[int, int] = {}
    hi = 0
    for i, (s, t) in enumerate(tr):
        st = ork.stage_step(names.get(s, s), kind)
        if st is None or st <= hi:
            continue
        for k in range(hi + 1, st + 1):
            reach.setdefault(k, t)
            idx.setdefault(k, i)
        hi = st
    if 1 not in reach and tr:
        reach[1], idx[1] = tr[0][1], 0
    return reach, idx


def диапазоны_сделки() -> dict[int, tuple[int, int]]:
    """Шаг стадии сделки → шаги заказа, которые она покрывает: стадия сделки грубее заказа
    («Товар отгружен» — шаги 9–10). Первая — с шага 1."""
    шаги = sorted(set(ork.STAGE_STEP["deal"].values()))
    out = {}
    for i, s in enumerate(шаги):
        lo = 1 if i == 0 else s
        hi = (шаги[i + 1] - 1) if i + 1 < len(шаги) else 13
        out[s] = (lo, hi)
    return out


def сверить(a: dt.datetime | None, b: dt.datetime | None) -> int | None:
    """Расхождение двух опор в р.д. по модулю; None — опоры нет или года нет в календаре."""
    if a is None or b is None:
        return None
    wd = period_mod.workdays_ru(a, b)
    return abs(wd) if wd is not None else None


def значение_даты_оплаты(совп_movedTime: float | None, совп_истории: float | None,
                         у_неоплаченных: float | None) -> str:
    """Что значит «Дата оплаты счета» СП-137 — по двум способам и неоплаченным счетам."""
    if у_неоплаченных is not None and у_неоплаченных >= ДАТА_ОПЛАТЫ["план_у_неоплаченных"]:
        return "похоже на план: поле заполнено у неоплаченных"
    if (совп_movedTime is not None and совп_истории is not None
            and min(совп_movedTime, совп_истории) >= ДАТА_ОПЛАТЫ["факт_совпадение"]):
        return "похоже на факт оплаты: оба способа сходятся"
    return "неясно: способы расходятся или данных мало"


ВЕРДИКТ = {"show": "SHOW", "prelim": "PRELIM", "hidden": "HIDE", "nodata": "НЕТ ДАННЫХ"}


# ── учёт запросов ───────────────────────────────────────────────────────────

class Учёт:
    """Запросы этого прогона — по общему счётчику клиента (bc._НАГРУЗКА), от старта."""

    def __init__(self, предел: int = БЮДЖЕТ["предел"]):
        self.предел = предел
        self.старт = int(bc._НАГРУЗКА.get("запросов", 0))
        self.по_блокам: collections.Counter = collections.Counter()

    def сейчас(self) -> int:
        return int(bc._НАГРУЗКА.get("запросов", 0)) - self.старт

    @contextlib.contextmanager
    def блок(self, имя: str):
        a = self.сейчас()
        try:
            yield
        finally:
            self.по_блокам[имя] += self.сейчас() - a

    def можно(self, имя: str, оценка: int) -> bool:
        if self.сейчас() + оценка <= self.предел:
            return True
        print(f"блок «{имя}» не читается: смета {оценка} запросов не помещается в предел {self.предел} "
              f"(уже {self.сейчас()})")
        return False


# ── прогон ──────────────────────────────────────────────────────────────────

class Прогон:
    def __init__(self, client, сегодня: dt.date):
        self.client, self.сегодня = client, сегодня
        self.учёт = Учёт()
        self.частей, self.замечание_частей = частей()
        self.смета = 0
        self.имена_заказа: dict[str, str] = {}
        self.сем_заказа: dict[str, str] = {}
        self.имена_сделки: dict[str, str] = {}
        self.подписи: dict[str, dict[str, str]] = {}
        self.заказы: list[dict] = []
        self.сделки: dict[str, dict] = {}
        self.без_сделки = self.проигранных = 0
        self.орк: set[str] = set()
        self.орк_глава = ""
        self.орк_отделов = 0
        self.ист_з: dict[str, list] = {}
        self.ист_с: dict[str, list] = {}
        self.задачи: dict[str, dict] = {}
        self.задачи_право: str | None = None      # None — не проверяли; "да" — есть; иначе код отказа
        self.части: list[list[str]] = []
        self.прочитанные: set[str] = set()
        self.упавшие: list[int] = []
        self.неполные: set[str] = set()
        self.сп: dict[int, dict] = {}             # СП-1174/1192/1066/137: {items, meta, ошибка}
        self.ист_137: dict[str, list] = {}
        self.поставщики_1192: set[str] = set()     # компании с карточкой СП-1192 (рамочный договор?)

    @property
    def полно(self) -> bool:
        return not self.упавшие


def _total(client, method: str, params: dict) -> int:
    env = client.call_envelope(method, dict(params, start=0)) or {}
    return int(env.get("total") or 0)


def шаг_ожидание(п: Прогон) -> bool:
    """Первая строка журнала: сколько ждём сделок, заказов, частей и запросов."""
    try:
        сделок = _total(п.client, "crm.deal.list", {"filter": {"CATEGORY_ID": 0, "!STAGE_SEMANTIC_ID": "F"},
                                                     "select": ["ID"]})
        заказов = _total(п.client, "crm.item.list", {"entityTypeId": ORDER_ENTITY, "filter": {},
                                                      "select": ["id"]})
    except Exception as e:  # noqa: BLE001 — печатается только тип
        print(f"ожидается: — (число записей не прочитано: {type(e).__name__}); прогон не начат")
        return False
    п.ожид = (сделок, заказов)
    п.смета = смета(заказов, сделок, п.частей)
    print(f"ожидается: сделок кат. 0 (не проигранных) {сделок}, заказов СП-{ORDER_ENTITY} {заказов}, "
          f"частей {п.частей}{(' (' + п.замечание_частей + ')') if п.замечание_частей else ''}, "
          f"смета ≈ {п.смета} запросов (ориентир {БЮДЖЕТ['ожидается']}, предел {БЮДЖЕТ['предел']})")
    if п.смета > БЮДЖЕТ["предел"]:
        print(f"смета {п.смета} больше предела {БЮДЖЕТ['предел']} — прогон не начат")
        return False
    return True


def _категории(client, entity: int) -> list[int]:
    res = client.call("crm.category.list", {"entityTypeId": entity}) or {}
    cats = res.get("categories") if isinstance(res, dict) else res
    return sorted({int(c.get("id") if c.get("id") is not None else c.get("ID")) for c in (cats or [])})


def _мета_сп(client, entity: int, cats) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cid in cats:
        out.update(client.spa_stage_meta(entity, cid))
    return out


def _подписи_сп(client, entity: int, поле: str) -> dict[str, str]:
    res = client.call("crm.item.fields", {"entityTypeId": entity}) or {}
    fields = (res.get("fields") if isinstance(res, dict) else None) or {}
    return {str(i.get("ID")): str(i.get("VALUE") or "") for i in (fields.get(поле) or {}).get("items") or []}


def шаг_основа(п: Прогон) -> bool:
    """Справочники стадий и подписей, заказы и сделки списком, сверка с ожидаемым."""
    client = п.client
    with п.учёт.блок("основа"):
        п.имена_сделки = client.deal_stages_cat(0)
        заказы = client.list_items(ORDER_ENTITY, filter={}, select=ORDER_SELECT)
        if len(заказы) < п.ожид[1]:
            print(f"заказов прочитано {len(заказы)} из {п.ожид[1]} — обход неполон, прогон остановлен")
            return False
        cats = sorted({int(m.group(1)) for o in заказы
                       if (m := re.match(rf"DT{ORDER_ENTITY}_(\d+):", str(o.get("stageId") or "")))})
        for sid, m in _мета_сп(client, ORDER_ENTITY, cats).items():
            п.имена_заказа[sid] = m["name"]
            п.сем_заказа[sid] = m["sem"]
        п.подписи[З["dir"]] = _подписи_сп(client, ORDER_ENTITY, З["dir"])
        поля_сделки = client.call("crm.deal.fields", {}) or {}
        п.подписи[Д["endPoint"]] = {str(i.get("ID")): str(i.get("VALUE") or "")
                                    for i in (поля_сделки.get(Д["endPoint"]) or {}).get("items") or []}
        сделки = client.list_deals_fast(filter={"CATEGORY_ID": 0, "!STAGE_SEMANTIC_ID": "F"}, select=DEAL_SELECT)
        if len(сделки) < п.ожид[0]:
            print(f"сделок прочитано {len(сделки)} из {п.ожид[0]} — обход неполон, прогон остановлен")
            return False
    п.сделки = {str(d["ID"]): d for d in сделки}
    по_версии = collections.Counter()
    for o in заказы:
        sid = str(o.get("stageId") or "")
        sem = п.сем_заказа.get(sid) or ("F" if sid.endswith(":FAIL") else "S" if sid.endswith(":SUCCESS") else "P")
        создан = _вр(o.get("createdTime"))
        if sem == "F":
            п.проигранных += 1
            continue
        if sem != "P" and (создан is None or создан < _вр(HIST_SINCE)):
            по_версии["старше истории"] += 1
            continue
        if not _uid(o.get("parentId2")):
            п.без_сделки += 1
            continue
        o["_sem"] = sem
        п.заказы.append(o)
    print(f"заказов: всего {len(заказы)}, проигранных {п.проигранных}, закрытых старше {HIST_SINCE[:10]} "
          f"{по_версии['старше истории']}, без сделки (только счёт) {п.без_сделки}, в разборе {len(п.заказы)}; "
          f"воронок заказов {len(cats)}, стадий в справочнике {len(п.имена_заказа)}")
    return True


def шаг_отдел(п: Прогон) -> None:
    """Отдел ОРК по названию (имя в журнал не идёт) → его люди и руководитель."""
    try:
        with п.учёт.блок("отдел ОРК"):
            deps = п.client.departments()
            орк = [d for d in deps if ОТДЕЛ_ОРК_RX.search(str(d.get("NAME") or ""))]
            п.орк_отделов = len(орк)
            for d in орк:
                п.орк |= {str(x) for x in п.client.dept_member_ids(d["ID"])}
            п.орк_глава = _uid(орк[0].get("UF_HEAD")) if орк else ""
    except Exception as e:  # noqa: BLE001
        print(f"отдел ОРК: не прочитан ({код_ошибки(e)})")


def _читать_задачи(client, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        пачка = [int(x) for x in ids[i:i + 50]]
        свои = {str(x) for x in пачка}
        start = 0
        while True:
            env = client.call_envelope("tasks.task.list", {
                "filter": {"ID": пачка}, "select": ["ID", "CREATED_DATE", "CLOSED_DATE", "STATUS", "DEADLINE"],
                "start": start}) or {}
            res = env.get("result") or {}
            tasks = (res.get("tasks") if isinstance(res, dict) else res) or []
            for t in tasks:
                tid = str(t.get("id") or t.get("ID") or "")
                if tid not in свои:
                    raise ОшибкаЧасти("фильтр ID задач не сработал")
                out[tid] = {"closed": _вр(t.get("closedDate") or t.get("CLOSED_DATE")),
                            "created": _вр(t.get("createdDate") or t.get("CREATED_DATE")),
                            "status": str(t.get("status") or t.get("STATUS") or "")}
            nxt = env.get("next")
            if not nxt or not tasks:
                break
            start = nxt
    return out


def _часть(п: Прогон, сделки_части: list[str], заказы_части: list[dict]) -> dict:
    client = п.client
    loc = {"ист_з": {}, "ист_с": {}, "сделки": {}, "задачи": {}}
    вне = [d for d in сделки_части if d not in п.сделки]
    if вне:
        loc["сделки"] = client.deals_by_ids(вне, select=DEAL_SELECT)
    ids_з = [str(o["id"]) for o in заказы_части]
    if ids_з:
        ист, ош = pc.читать_историю(client, ids_з, entity=ORDER_ENTITY)
        if ош:
            raise ОшибкаЧасти(ош)
        loc["ист_з"] = ист
    ист, ош = pc.читать_историю(client, сделки_части, категория=0)
    if ош:
        raise ОшибкаЧасти(ош)
    loc["ист_с"] = ист
    задачи = sorted({_uid(o.get(З[k])) for o in заказы_части for k in ("taskAssign", "taskStatus")} - {""}, key=int)
    if задачи and п.задачи_право in (None, "да"):
        try:
            loc["задачи"] = _читать_задачи(client, задачи)
            if п.задачи_право is None:
                п.задачи_право = "да"
                print("tasks.*: вебхук читает задачи — опоры по задачам считаются")
        except bc.BitrixLimitError:
            raise                                   # лимит портала — не отказ в праве: часть падает вслух
        except bc.BitrixError as e:
            if п.задачи_право is None:
                п.задачи_право = код_ошибки(e)
                print(f"tasks.*: отказ портала ({п.задачи_право}) — опоры по задачам не считаются")
            else:
                raise
    return loc


def шаг_части(п: Прогон) -> None:
    по_сделке: dict[str, list[dict]] = collections.defaultdict(list)
    for o in п.заказы:
        по_сделке[str(o["parentId2"])].append(o)
    п.части = разбить(list(set(п.сделки) | set(по_сделке)), п.частей)
    for k, часть in enumerate(п.части, 1):
        заказы_части = [o for d in часть for o in по_сделке.get(d, [])]
        оценка = смета_части(len(заказы_части), len(часть))
        if п.учёт.сейчас() + оценка > п.учёт.предел:
            п.упавшие.append(k)
            print(f"часть {k} из {len(п.части)}: не читается — смета {оценка} не помещается в предел")
            continue
        try:
            with п.учёт.блок("части"):
                loc = _часть(п, часть, заказы_части)
        except ОшибкаЧасти as e:
            п.упавшие.append(k)
            print(f"часть {k} из {len(п.части)}: не прочитана — {e}")
            continue
        except Exception as e:  # noqa: BLE001 — печатаются только тип и место
            п.упавшие.append(k)
            print(f"часть {k} из {len(п.части)}: сбой {type(e).__name__} · {место(e)}")
            continue
        п.сделки.update({str(i): d for i, d in loc["сделки"].items()})
        п.ист_з.update(loc["ист_з"])
        п.ист_с.update(loc["ист_с"])
        п.задачи.update(loc["задачи"])
        п.прочитанные.update(часть)
    упавшие_сделки = {d for k in п.упавшие for d in п.части[k - 1]}
    п.неполные = {полугодие(_вр(o.get("createdTime"))) for d in упавшие_сделки for o in по_сделке.get(d, [])}
    print(f"частей прочитано {len(п.части) - len(п.упавшие)} из {len(п.части)}"
          + (f"; когорты, задетые упавшими частями, вне выводов: {', '.join(sorted(п.неполные))}" if п.неполные else ""))


def шаг_смарт(п: Прогон) -> None:
    """СП-1174, СП-1192 (договоры), СП-1066 (проверка контрагента), СП-137 (счета)."""
    client = п.client
    наборы = [(e, ["id", "stageId", "parentId2", f"parentId{ORDER_ENTITY}", "companyId", "createdTime"], {})
              for e in CONTRACT_ENTITIES]
    наборы.append((CHECK_ENTITY, ["id", "stageId", "companyId", CHECK_COMPANY, "createdTime"], {}))
    if п.учёт.можно("договоры и проверки", СМЕТА["договоры_и_проверки"]):
        for e, sel, f in наборы:
            try:
                with п.учёт.блок(f"СП-{e}"):
                    cats = _категории(client, e)
                    п.сп[e] = {"meta": _мета_сп(client, e, cats), "items": client.list_items(e, filter=f, select=sel),
                               "cats": len(cats)}
            except Exception as e2:  # noqa: BLE001
                п.сп[e] = {"ошибка": код_ошибки(e2)}
    if not п.учёт.можно("счета СП-137", СМЕТА["счета_137"]):
        return
    try:
        with п.учёт.блок("СП-137"):
            cats = _категории(client, INVOICE_ENTITY)
            meta = _мета_сп(client, INVOICE_ENTITY, cats)
            items = client.list_items(INVOICE_ENTITY, filter={">=createdTime": HIST_SINCE},
                                      select=["id", "stageId", "createdTime", "movedTime", "parentId2",
                                              f"parentId{ORDER_ENTITY}", INV_TYPE, INV_PAY_DATE, INV_ORDERS])
            п.подписи[INV_TYPE] = _подписи_сп(client, INVOICE_ENTITY, INV_TYPE)
            п.сп[INVOICE_ENTITY] = {"meta": meta, "items": items, "cats": len(cats)}
    except Exception as e:  # noqa: BLE001
        п.сп[INVOICE_ENTITY] = {"ошибка": код_ошибки(e)}
        return
    оплаченные = sorted((x for x in items if (meta.get(str(x.get("stageId"))) or {}).get("sem") == "S"),
                        key=lambda x: str(x.get("movedTime") or ""), reverse=True)[:СЧЕТОВ_В_ВЫБОРКЕ]
    if оплаченные and п.учёт.можно("история выборки счетов", СМЕТА["история_137"]):
        try:
            with п.учёт.блок("СП-137 история"):
                ист, ош = pc.читать_историю(client, [str(x["id"]) for x in оплаченные], entity=INVOICE_ENTITY)
            if ош:
                print(f"история выборки счетов: {ош}")
            else:
                п.ист_137 = ист
        except Exception as e:  # noqa: BLE001
            print(f"история выборки счетов: сбой {код_ошибки(e)}")


# ── запись заказа ───────────────────────────────────────────────────────────

def запись(o: dict, п: Прогон) -> dict:
    """Заказ → всё, что зонду нужно о нём: шаг, факты, точки границ, сегменты, люди."""
    oid, did = str(o["id"]), str(o.get("parentId2") or "")
    сделка = п.сделки.get(did) or {}
    tr = п.ист_з.get(oid, [])
    names = п.имена_заказа
    создан = _вр(o.get("createdTime"))
    дист = п.ист_с.get(did, [])
    вход0 = min((t for t in (_вр(x) for _s, x in дист) if t), default=None)
    t0 = max((x for x in (создан, вход0) if x), default=None)
    перенос = _вр(сделка.get(Д["moved"]))
    t0_поле = max((x for x in (создан, перенос) if x), default=None) if перенос else None
    факты = {"supPaidPct": _ч(o.get(З["supPaid"])) > 0,
             "prodConf": _есть(o.get(З["prodConf"])) or _есть(o.get(З["prodConfNf"])),
             "shipFact": o.get(З["shipFact"]) if _есть(o.get(З["shipFact"])) else None,
             "inbFact": o.get(З["inbFact"]) if _есть(o.get(З["inbFact"])) else None,
             "delFact": o.get(З["delFact"]) if _есть(o.get(З["delFact"])) else None,
             "utd": _есть(o.get(З["utdDate"])) or _есть(o.get(З["utdFile"]))}
    stage = names.get(str(o.get("stageId") or ""), "")
    reach, idx = достижения(tr, names)
    seg = ork.segments(tr, names, t0=t0, facts={"delFact": факты["delFact"], "utdRaw": o.get(З["utdDate"])})
    шаг_сейчас, с_шага = ork.step_since(tr, names)
    dreach, _ = достижения(дист, п.имена_сделки, "deal")
    задача = п.задачи.get(_uid(o.get(З["taskAssign"]))) or {}
    точки = {"t0_hist": t0, "t0_field": t0_поле, "resp_date": _вр(o.get(З["respDate"])),
             "task_assign": задача.get("closed"), "ship_fact": _вр(факты["shipFact"]),
             "inb_fact": _вр(факты["inbFact"]), "del_fact": _вр(факты["delFact"]),
             "utd_date": _вр(o.get(З["utdDate"])), "deal_paidEntry": dreach.get(13),
             "done_date": _вр(o.get(З["done"]))}
    for k in range(2, 14):
        точки[f"reach{k}"] = reach.get(k)
    for k in ("supContract", "supPaid", "invPaid", "confirm", "produced", "shipped", "delivered"):
        точки[f"deal_{k}"] = _вр(сделка.get(Д[k]))
    ord_ork = dict(факты, stage=stage, stepSince=с_шага if шаг_сейчас is not None else "",
                   stageSince=(sorted(tr, key=lambda x: str(x[1]))[-1][1] if tr else o.get("movedTime")),
                   t0=t0, prodStart=o.get(З["prodStart"]), prodEnd=o.get(З["prodEnd"]),
                   shipPlan=o.get(З["shipPlan"]))
    имена_шагов = [names.get(s, s) for s, _t in sorted(tr, key=lambda x: str(x[1]))]
    return {
        "id": oid, "did": did, "sem": o.get("_sem") or "P", "live": (o.get("_sem") or "P") == "P",
        "создан": создан, "пг": полугодие(создан), "stage": stage,
        "step": ork.step_of(stage, факты), "base": ork.stage_step(stage), "факты": факты,
        "reach": reach, "idx": idx, "seg": seg, "точки": точки, "ork": ord_ork,
        "напр": метка(З["dir"], o.get(З["dir"]), п.подписи.get(З["dir"], {})),
        "точка": метка(Д["endPoint"], сделка.get(Д["endPoint"]), п.подписи.get(Д["endPoint"], {})),
        "таможня": any(ork.norm_name(x) == ork.norm_name(ТАМОЖНЯ) for x in имена_шагов),
        "склад": any(ork.norm_name(x) in {ork.norm_name(s) for s in СКЛАДСКИЕ} for x in имена_шагов),
        "первая": ork.stage_step(имена_шагов[0]) if имена_шагов else None,
        "шаги_истории": {ork.stage_step(x) for x in имена_шагов} - {None},
        "resp": _uid(o.get("assignedById")), "dealOrk": _uid(сделка.get(Д["orkField"])),
        "respInit": _есть(o.get(З["respInit"])), "company": _uid(o.get("companyId")),
        "dreach": dreach, "dealStage": п.имена_сделки.get(str(сделка.get("STAGE_ID") or ""), ""),
        "dealSem": str(сделка.get("STAGE_SEMANTIC_ID") or "P"),
        "o": o,
    }


# ── отчёт ───────────────────────────────────────────────────────────────────

def _в_когортах(п: Прогон) -> list[dict]:
    return [запись(o, п) for o in п.заказы
            if str(o["parentId2"]) in п.прочитанные and полугодие(_вр(o.get("createdTime"))) not in п.неполные]


def отчёт_календарь(п: Прогон, строки: list[dict]) -> dict:
    годы = period_mod.workcal_years()
    нужно = range(int(HIST_SINCE[:4]), п.сегодня.year + 2)
    нет = [y for y in нужно if y not in годы]
    без = sum(1 for r in строки if r["seg"]["noCalendar"])
    print("\n1. КАЛЕНДАРЬ")
    print(f"  производственный календарь РФ: годы {', '.join(map(str, годы)) or '—'}; нужно {нужно.start}–"
          f"{нужно.stop - 1}; не покрыто {', '.join(map(str, нет)) or 'ничего'}; заказов с сегментом без "
          f"календаря {без} (должно быть 0; Пн–Пт не подставляется)")
    return {"нет_лет": нет, "без": без}


def _проходит(r: dict, шаг: int) -> bool:
    return r["sem"] == "S" or (r["step"] or 0) >= шаг


def _прошли_конец(r: dict, b) -> bool:
    """Заказ прошёл конец сегмента: шаг — по стадии и фактам; конец S9 (УПД) ждём у доставленных."""
    if isinstance(b, int):
        return _проходит(r, b)
    return bool(r["факты"]["delFact"]) if b == "utdRaw" else False


def отчёт_границы(строки: list[dict]) -> dict:
    """2. Заполненность опор по полугодиям и совпадение двух опор в пределах 1 р.д."""
    print("\n2. ГРАНИЦЫ СЕГМЕНТОВ: опоры по полугодиям создания заказа; совпадение ≤ "
          f"{ork.GATES['agree_wd']} р.д.")
    пг = sorted({r["пг"] for r in строки})
    итог = {}
    for код, подпись, шаг, осн, вторые in ГРАНИЦЫ:
        прошли = [r for r in строки if _проходит(r, шаг)]
        части = []
        for h in пг:
            рр = [r for r in прошли if r["пг"] == h]
            if not рр:
                continue
            опоры = " / ".join(f"{sum(1 for r in рр if r['точки'][k])}" for k in (осн,) + вторые)
            части.append(f"{h}: {len(рр)} → {опоры}")
        print(f"  {код} {подпись} — прошли: опоры [{', '.join((осн,) + вторые)}]: {'; '.join(части) or '—'}")
        лучшее = None
        for k in вторые:
            if k.startswith("deal_"):
                ранние: dict[str, dt.datetime] = {}
                втор: dict[str, dt.datetime] = {}
                for r in прошли:
                    a, b = r["точки"][осн], r["точки"][k]
                    if a and (r["did"] not in ранние or a < ранние[r["did"]]):
                        ранние[r["did"]] = a
                    if b:
                        втор[r["did"]] = b
                пары = [сверить(ранние[d], втор[d]) for d in ранние if d in втор]
                ед = "сделок"
            else:
                пары = [сверить(r["точки"][осн], r["точки"][k]) for r in прошли]
                ед = "заказов"
            пары = [x for x in пары if x is not None]
            if not пары:
                print(f"    {осн} ↔ {k}: пар нет")
                continue
            совп = sum(1 for x in пары if x <= ork.GATES["agree_wd"]) / len(пары)
            print(f"    {осн} ↔ {k} ({ОПОРЫ[k]}): пар {len(пары)} {ед}, совпадают {пр(совп)}, "
                  f"медиана расхождения {чс(мед(пары))} р.д., P80 {чс(p80(пары))}")
            if len(пары) >= МИН_ПАР_ОПОР and (лучшее is None or совп > лучшее):
                лучшее = совп
        покрытие = (sum(1 for r in прошли if r["точки"][осн]) / len(прошли)) if прошли else None
        итог[код] = {"agree": лучшее, "cover": покрытие, "n": len(прошли)}
    return итог


def отчёт_сегменты(п: Прогон, строки: list[dict], границы: dict) -> dict:
    """3. Сегменты S1–S10: n, пропуски с причиной, медиана и P80, доля в норме."""
    print(f"\n3. СЕГМЕНТЫ S1–S10 (по стадии, {ork.NO_PAUSE}; р.д. — календарь РФ, кал. — календарные дни)")
    год_назад = п.сегодня - dt.timedelta(days=ДНЕЙ_ГОД)
    итог = {}
    for код, имя, a, b, роль in ork.SEGMENTS:
        норма = ork.SEG_NORM.get(код)
        все = [(r, r["seg"]["segs"][код]) for r in строки if код in r["seg"]["segs"]]
        причины = collections.Counter()
        чистые = []
        for r, s in все:
            if s["wd"] is None:
                причины["без календаря"] += 1
            elif s["neg"]:
                причины["конец раньше начала"] += 1
            elif s["burst"]:
                причины["«пачка»"] += 1
            else:
                чистые.append((r, s))
            if s["ret"]:
                причины["с возвратом"] += 1
            if isinstance(a, int) and isinstance(b, int) and r["idx"].get(a) is not None \
                    and r["idx"].get(a) == r["idx"].get(b):
                причины["стадия пропущена (0 дн.)"] += 1
        if код == "S5":
            причины["короче суток («в наличии»?)"] = sum(1 for _r, s in чистые if s["cd"] < 1)
        if код == "S7":
            причины["без складских стадий (прямая отгрузка?)"] = sum(1 for r, _s in чистые if not r["склад"])
        if код == "S2":
            рамочн = п.поставщики_1192
            причины["у поставщика есть карточка СП-1192 (рамочный?)"] = sum(
                1 for r, _s in чистые if r["company"] in рамочн)
        открыто = sum(1 for r in строки if r["live"] and (r["seg"]["open"] or {}).get("seg") == код)
        прошли_конец = [r for r in строки if _прошли_конец(r, b)]
        wd = [s["wd"] for _r, s in чистые]
        cd = [s["cd"] for _r, s in чистые]
        в_норме = (sum(1 for _r, s in чистые if s["inNorm"]) / len(чистые)) if (норма and чистые) else None
        год = [(r, s) for r, s in чистые if _дн(s["end"]) and _дн(s["end"]) >= год_назад]
        год_н = (sum(1 for _r, s in год if s["inNorm"]) / len(год)) if (норма and год) else None
        по_напр = collections.defaultdict(list)
        for r, s in год:
            по_напр[r["напр"]].append(s["wd"])
        print(f"  {код} {имя} ({ork.ROLES[роль]}; норма {f'{норма} р.д.' if норма else 'не нормируется'}): "
              f"n {len(все)}, чистых {len(чистые)}, сейчас идёт {открыто}; медиана {чс(мед(wd))} р.д. / "
              f"{чс(мед(cd))} кал., P80 {чс(p80(wd))} р.д. / {чс(p80(cd))} кал."
              + (f"; в норме {пр(в_норме)}" if норма else ""))
        print(f"    за {ДНЕЙ_ГОД} дн.: n {len(год)}, медиана {чс(мед([s['wd'] for _r, s in год]))} р.д., "
              f"P80 {чс(p80([s['wd'] for _r, s in год]))} р.д." + (f", в норме {пр(год_н)}" if норма else "")
              + "; по направлению: " + ("; ".join(f"{k} n {len(v)}, медиана {чс(мед(v))}, P80 {чс(p80(v))}"
                                                 for k, v in sorted(по_напр.items())) or "—"))
        print(f"    пропуски и пометки: {счёт(причины)}")
        burst_share = (причины["«пачка»"] / len(все)) if все else None
        итог[код] = {"value": мед(wd), "n": len(год), "cover": (len(все) / len(прошли_конец)) if прошли_конец else None,
                     "burst": burst_share, "inNorm": год_н, "agree": (границы.get(f"{код}.конец") or {}).get("agree")}
    цикл = [(r["точки"]["del_fact"] - r["точки"]["t0_hist"]).total_seconds() / 86400 for r in строки
            if r["точки"]["del_fact"] and r["точки"]["t0_hist"] and r["точки"]["del_fact"] >= r["точки"]["t0_hist"]]
    print(f"  полный цикл (начало приёмки → доставка клиенту): n {len(цикл)}, медиана {чс(мед(цикл))} кал. дн. "
          f"({чс((мед(цикл) or 0) / 7) if цикл else '—'} нед.), P80 {чс(p80(цикл))} кал. дн.")
    итог["цикл"] = {"value": мед(цикл), "n": len(цикл)}
    return итог


def отчёт_порядок(строки: list[dict]) -> dict:
    """4. Порядок против регламента."""
    print("\n4. ПОРЯДОК ПРОТИВ РЕГЛАМЕНТА (доля заказов)")
    n = len(строки)
    с_историей = [r for r in строки if r["reach"]]
    возвраты = [r for r in с_историей if r["seg"]["returns"]]
    пары = collections.Counter(f"шаг {a} → {b}" for r in с_историей for a, b in r["seg"]["returnPairs"])
    пачки = sum(1 for r in с_историей if r["seg"]["burst"])
    print(f"  с историей стадий {доля(len(с_историей), n)}; возвраты на шаг раньше {доля(len(возвраты), len(с_историей))}; "
          f"частые пары: {', '.join(f'{k} ({v})' for k, v in пары.most_common(5)) or '—'}")
    print(f"  «пачка» (≥ {ork.BURST['n']} переходов за {ork.BURST['sec'] // 60} мин) {доля(пачки, len(с_историей))}; "
          f"история начинается не с шага 1 {доля(sum(1 for r in с_историей if r['первая'] != 1), len(с_историей))}; "
          f"стадия не из таблицы шагов — переходов {sum(r['seg']['unknown'] for r in с_историей)}")
    пропуски = collections.Counter()
    for r in с_историей:
        верх = max(r["reach"])
        for k in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12):
            if k < верх and k not in r["шаги_истории"]:
                пропуски[f"шаг {k}"] += 1
    print(f"  пропуск шага (дошёл дальше, на шаге не был): {счёт(пропуски)}")
    оплачены = [r for r in с_историей if r["reach"].get(6)]
    раньше = sum(1 for r in оплачены if r["dreach"].get(4) and r["reach"][6] < r["dreach"][4])
    без_договора = sum(1 for r in оплачены if not r["dreach"].get(4))
    print(f"  оплата поставщику (выход на шаг 6) раньше входа сделки в «Договор с заказчиком подписан» "
          f"{доля(раньше, len(оплачены))}; сделка до этой стадии не дошла {доля(без_договора, len(оплачены))} "
          f"(п. 2.4 регламента; договор в портале — проверка вкладки по dealctx)")
    предопл = [r for r in с_историей if _ч(r["o"].get(З["firstPay"])) > 0 and r["reach"].get(7)]
    мимо_оплаты = sum(1 for r in предопл if 6 not in r["шаги_истории"])
    print(f"  производство без стадии оплаты при предоплате > 0 {доля(мимо_оплаты, len(предопл))}")
    тамож = collections.defaultdict(lambda: [0, 0])
    for r in с_историей:
        тамож[r["напр"]][0] += 1
        тамож[r["напр"]][1] += r["таможня"]
    print("  «Таможня» по направлению поставки: " + "; ".join(
        f"{k}: прошли {v[1]} из {v[0]}" for k, v in sorted(тамож.items())))
    диап = диапазоны_сделки()
    по_сделке: dict[str, list[dict]] = collections.defaultdict(list)
    for r in строки:
        if r["live"]:
            по_сделке[r["did"]].append(r)
    впереди = позади = без_шага = 0
    for рр in по_сделке.values():
        ds = ork.stage_step(рр[0]["dealStage"], "deal")
        шаги = [r["step"] for r in рр if r["step"]]
        if ds is None or not шаги:
            без_шага += 1
            continue
        lo, hi = диап[ds]
        впереди += lo > max(шаги)
        позади += hi < min(шаги)
    print(f"  сделки с живыми заказами {len(по_сделке)}: стадия сделки впереди всех заказов {впереди}, позади "
          f"{позади}, шаг не определить {без_шага}")
    return {"burst": (пачки / len(с_историей)) if с_историей else None, "returns": len(возвраты)}


def отчёт_метки(строки: list[dict]) -> None:
    print("\n5. ПОДПИСИ ЗНАЧЕНИЙ (белый список; иначе «вариант k»)")
    print("  Направление поставки (заказ): " + счёт(collections.Counter(r["напр"] for r in строки)))
    по_сделке = {r["did"]: r["точка"] for r in строки}
    print("  Supply end point (сделка): " + счёт(collections.Counter(по_сделке.values())))
    прямые = collections.defaultdict(lambda: [0, 0])
    for r in строки:
        if r["reach"].get(10):
            прямые[r["точка"]][0] += 1
            прямые[r["точка"]][1] += not r["склад"]
    print("  без складских стадий по Supply end point: " + ("; ".join(
        f"{k}: {v[1]} из {v[0]}" for k, v in sorted(прямые.items())) or "—"))


def отчёт_люди(п: Прогон, строки: list[dict]) -> dict:
    """6. Ответственный живого заказа и отдел ОРК; поле сделки; загрузка без имён."""
    print("\n6. МЕНЕДЖЕР ОРК (без имён)")
    живые = [r for r in строки if r["live"]]
    if not п.орк:
        print(f"  отдел ОРК по названию не найден (совпало отделов {п.орк_отделов}) — доли не считаются")
        return {"cover": None, "n": len(живые)}
    for подпись, рр in (("все живые", живые),
                        (f"созданы с {ORK_SINCE:%d.%m.%Y}", [r for r in живые if r["создан"] and _дн(r["создан"]) >= ORK_SINCE]),
                        (f"созданы раньше {ORK_SINCE:%d.%m.%Y}", [r for r in живые if r["создан"] and _дн(r["создан"]) < ORK_SINCE])):
        вне = sum(1 for r in рр if r["resp"] and r["resp"] not in п.орк)
        поле = [r for r in рр if r["dealOrk"]]
        равно = sum(1 for r in поле if r["dealOrk"] == r["resp"])
        print(f"  {подпись}: {len(рр)}; ответственный не из отдела ОРК {доля(вне, len(рр))}; поле сделки "
              f"«Ответственный за реализацию» есть {доля(len(поле), len(рр))}, совпадает с ответственным заказа "
              f"{доля(равно, len(поле))}; переназначены (есть «Изначальный ответственный») "
              f"{доля(sum(1 for r in рр if r['respInit']), len(рр))}")
    print(f"  отделов ОРК по названию {п.орк_отделов}, людей {len(п.орк)}; руководитель отдела в справочнике "
          f"{'есть' if п.орк_глава else 'нет'}; живых заказов на руководителе "
          f"{sum(1 for r in живые if п.орк_глава and r['resp'] == п.орк_глава)}")
    нагрузка = collections.Counter(r["resp"] for r in живые if r["resp"] in п.орк)
    if нагрузка and len(нагрузка) < МИН_ЛЮДЕЙ:
        print(f"  загрузка (M3): менеджеров ОРК с живыми заказами {len(нагрузка)} < {МИН_ЛЮДЕЙ} — "
              "мин, медиана и макс не пишем: это нагрузка человека")
    elif нагрузка:
        ср = sum(нагрузка.values()) / len(нагрузка)
        вне = sum(1 for v in нагрузка.values() if abs(v - ср) > ДОПУСК_ЗАГРУЗКИ * ср)
        print(f"  загрузка (M3): менеджеров ОРК с живыми заказами {len(нагрузка)}; заказов на человека: мин "
              f"{min(нагрузка.values())}, медиана {чс(мед(list(нагрузка.values())))}, макс {max(нагрузка.values())}; "
              f"вне ±{ДОПУСК_ЗАГРУЗКИ:.0%} от среднего {вне}")
    в_орк = sum(1 for r in живые if r["resp"] in п.орк)
    return {"cover": (в_орк / len(живые)) if живые else None, "n": len(живые), "людей": len(нагрузка)}




def отчёт_упд(п: Прогон, строки: list[dict]) -> dict:
    """7. УПД: сырой UTD_DATE против файла; новый noUtd (> 10 р.д.) против прежнего (14 кал. дн.)."""
    print("\n7. УПД (сырой UTD_DATE, без подстановки даты доставки)")
    дост = [r for r in строки if r["факты"]["delFact"]]
    файл_без_даты = sum(1 for r in строки if _есть(r["o"].get(З["utdFile"])) and not _есть(r["o"].get(З["utdDate"])))
    дата_без_файла = sum(1 for r in строки if _есть(r["o"].get(З["utdDate"])) and not _есть(r["o"].get(З["utdFile"])))
    print(f"  UTD_DATE пусто, а файл УПД есть: {файл_без_даты} заказов; дата есть, файла нет {дата_без_файла}; у "
          f"доставленных ({len(дост)}): дата {доля(sum(1 for r in дост if _есть(r['o'].get(З['utdDate']))), len(дост))}, "
          f"файл {доля(sum(1 for r in дост if _есть(r['o'].get(З['utdFile']))), len(дост))}")
    новый = старый = 0
    for r in дост:
        if not r["live"] or r["факты"]["utd"]:
            continue
        d = _дн(r["факты"]["delFact"])
        wd = period_mod.workdays_ru(d, п.сегодня)
        новый += wd is not None and wd > ork.NORM_WD[12]
        старый += (п.сегодня - d).days > 14
    print(f"  живые доставленные без УПД (ни даты, ни файла): по регламенту > {ork.NORM_WD[12]} р.д. — {новый}; "
          f"по прежнему правилу > 14 кал. дн. — {старый}")
    return {"новый": новый, "старый": старый}


def отчёт_план_факт(п: Прогон, строки: list[dict]) -> dict:
    """8. «План = факт» по PROD_END (против входа в «Заказ готов») и по SHIP_PLAN."""
    print("\n8. «ПЛАН = ФАКТ»")
    год_назад = п.сегодня - dt.timedelta(days=ДНЕЙ_ГОД)
    готовы = [r for r in строки if r["reach"].get(8)]
    с_планом = [r for r in готовы if _дн(r["o"].get(З["prodEnd"]))]
    равно = sum(1 for r in с_планом if _дн(r["reach"][8]) == _дн(r["o"].get(З["prodEnd"])))
    отгр = [r for r in строки if _дн(r["o"].get(З["shipPlan"])) and _дн(r["o"].get(З["shipFact"]))]
    равно_о = sum(1 for r in отгр if _дн(r["o"].get(З["shipPlan"])) == _дн(r["o"].get(З["shipFact"])))
    print(f"  PROD_END = день входа в «Заказ готов»: {доля(равно, len(с_планом))} (PROD_END есть у "
          f"{доля(len(с_планом), len(готовы))} готовых); SHIP_PLAN = SHIP_FACT: {доля(равно_о, len(отгр))}")
    год = [r for r in готовы if _дн(r["reach"][8]) >= год_назад]
    год_п = [r for r in год if _дн(r["o"].get(З["prodEnd"]))]
    в_срок = sum(1 for r in год_п if _дн(r["reach"][8]) <= _дн(r["o"].get(З["prodEnd"])))
    print(f"  Q1 первичный срок поставщика за {ДНЕЙ_ГОД} дн.: готовы не позже PROD_END {доля(в_срок, len(год_п))}")
    return {"value": (в_срок / len(год_п)) if год_п else None, "n": len(год_п),
            "cover": (len(год_п) / len(год)) if год else None,
            "plan_eq_fact": (равно / len(с_планом)) if с_планом else None}


def отчёт_поставщики(п: Прогон, строки: list[dict]) -> dict:
    """9. Поставщики с ≥ 5 заказами за квартал (скоркард Ан. §5)."""
    порог = ork.GATES["n_supplier_quarter"]
    print(f"\n9. ПОСТАВЩИКИ С ≥ {порог} ЗАКАЗАМИ ЗА КВАРТАЛ (по созданию заказа)")
    итог = {}
    for q in последние_кварталы(п.сегодня):
        по_пост = collections.defaultdict(list)
        for r in строки:
            if r["создан"] and квартал(_дн(r["создан"])) == q and r["company"]:
                по_пост[r["company"]].append(r)
        крупн = {c: рр for c, рр in по_пост.items() if len(рр) >= порог}
        их = [r for рр in крупн.values() for r in рр]
        отгр = [r for r in их if _дн(r["o"].get(З["shipPlan"])) and _дн(r["o"].get(З["shipFact"]))]
        равно = sum(1 for r in отгр if _дн(r["o"].get(З["shipPlan"])) == _дн(r["o"].get(З["shipFact"])))
        print(f"  {q}: поставщиков {len(по_пост)}, из них с ≥ {порог} заказами {len(крупн)} ({len(их)} заказов); "
              f"у их заказов вход в «Заказ готов» {доля(sum(1 for r in их if r['reach'].get(8)), len(их))}, "
              f"PROD_END {доля(sum(1 for r in их if _дн(r['o'].get(З['prodEnd']))), len(их))}, "
              f"SHIP_PLAN = SHIP_FACT {доля(равно, len(отгр))}")
        итог[q] = len(крупн)
    return итог


def отчёт_счета(п: Прогон) -> dict:
    """10. СП-137: создание → оплачено и что значит «Дата оплаты счета»."""
    print(f"\n10. СЧЕТА НА ОПЛАТУ СП-{INVOICE_ENTITY} (с {HIST_SINCE[:10]})")
    d = п.сп.get(INVOICE_ENTITY)
    if not d:
        print("  не читались (предел запросов)")
        return {}
    if d.get("ошибка"):
        print(f"  не прочитаны ({d['ошибка']})")
        return {}
    items, meta = d["items"], d["meta"]

    def sem(x):
        return (meta.get(str(x.get("stageId"))) or {}).get("sem") or "P"
    подпись_сем = {"S": "оплачен (S)", "F": "провал (F)"}
    print(f"  карточек {len(items)}, воронок {d['cats']}; по семантике: "
          + счёт(collections.Counter(подпись_сем.get(sem(x), "в работе (P)") for x in items))
          + "; стадии: " + счёт(collections.Counter(имя_стадии((meta.get(str(x.get("stageId"))) or {}).get("name"))
                                                    for x in items)))
    тип = {str(x["id"]): метка(INV_TYPE, x.get(INV_TYPE), п.подписи.get(INV_TYPE, {})) for x in items}
    print("  «Тип оплаты»: " + счёт(collections.Counter(тип.values())))
    с_заказом = sum(1 for x in items if _есть(x.get(f"parentId{ORDER_ENTITY}")) or _есть(x.get(INV_ORDERS)))
    print(f"  связь с заказом (parentId{ORDER_ENTITY} или «Заказы поставщикам») {доля(с_заказом, len(items))}; "
          f"со сделкой {доля(sum(1 for x in items if _есть(x.get('parentId2'))), len(items))}")
    опл = [x for x in items if sem(x) == "S"]
    по_типу, по_кв = collections.defaultdict(list), collections.defaultdict(list)
    for x in опл:
        a, b = _вр(x.get("createdTime")), _вр(x.get("movedTime"))
        wd = period_mod.workdays_ru(a, b) if a and b else None
        if wd is None or wd < 0:
            continue
        по_типу[тип[str(x["id"])]].append(wd)
        по_кв[квартал(_дн(a))].append(wd)
    print("  создание → оплачено (movedTime оплаченных), р.д.: по типу — " + ("; ".join(
        f"{k} n {len(v)}, медиана {чс(мед(v))}, P80 {чс(p80(v))}" for k, v in sorted(по_типу.items())) or "—"))
    print("    по кварталу создания — " + ("; ".join(
        f"{k} n {len(по_кв[k])}, медиана {чс(мед(по_кв[k]))}, P80 {чс(p80(по_кв[k]))}"
        for k in последние_кварталы(п.сегодня) if по_кв.get(k)) or "—"))
    # способ 1: поле против movedTime оплаченных
    с_полем = [x for x in опл if _вр(x.get(INV_PAY_DATE))]
    разн = [x for x in (сверить(_вр(x.get(INV_PAY_DATE)), _вр(x.get("movedTime"))) for x in с_полем) if x is not None]
    позже = sum(1 for x in с_полем if _дн(x.get("movedTime")) and _дн(x.get(INV_PAY_DATE)) > _дн(x.get("movedTime")))
    с1 = (sum(1 for x in разн if x <= ork.GATES["agree_wd"]) / len(разн)) if разн else None
    # способ 2: поле против входа в стадию успеха по истории выборки
    s_стадии = {sid for sid, m in meta.items() if m.get("sem") == "S"}
    по_ист = []
    for x in с_полем:
        ряд = п.ист_137.get(str(x["id"])) or []
        вход = min((t for t in (_вр(tt) for s, tt in ряд if s in s_стадии) if t), default=None)
        if вход:
            по_ист.append(сверить(_вр(x.get(INV_PAY_DATE)), вход))
    по_ист = [x for x in по_ист if x is not None]
    с2 = (sum(1 for x in по_ист if x <= ork.GATES["agree_wd"]) / len(по_ист)) if по_ист else None
    неопл = [x for x in items if sem(x) == "P"]
    у_неопл = (sum(1 for x in неопл if _вр(x.get(INV_PAY_DATE))) / len(неопл)) if неопл else None
    в_будущем = sum(1 for x in неопл if _дн(x.get(INV_PAY_DATE)) and _дн(x.get(INV_PAY_DATE)) > п.сегодня)
    print(f"  «Дата оплаты счета»: способ 1 (против movedTime оплаченных) — пар {len(разн)}, ≤ "
          f"{ork.GATES['agree_wd']} р.д. {пр(с1)}, поле позже movedTime {позже}; способ 2 (против входа в стадию "
          f"успеха, история выборки {len(п.ист_137)} счетов) — пар {len(по_ист)}, ≤ {ork.GATES['agree_wd']} р.д. "
          f"{пр(с2)}; у неоплаченных поле есть {пр(у_неопл)}, в будущем {в_будущем}")
    вывод = значение_даты_оплаты(с1, с2, у_неопл)
    print(f"  вывод: {вывод}")
    return {"вывод": вывод}


def отчёт_договоры(п: Прогон, строки: list[dict]) -> None:
    """11. СП-1066 и СП-1174/1192: стадии и доля связей."""
    print(f"\n11. ДОГОВОРЫ СП-{'/'.join(map(str, CONTRACT_ENTITIES))} И ПРОВЕРКА КОНТРАГЕНТА СП-{CHECK_ENTITY}")
    поставщики = {r["company"] for r in строки if r["live"] and r["company"]}
    подпись_сем = {"S": "успех", "F": "провал"}
    for e in CONTRACT_ENTITIES + (CHECK_ENTITY,):
        d = п.сп.get(e)
        if not d:
            print(f"  СП-{e}: не читался (предел запросов)")
            continue
        if d.get("ошибка"):
            print(f"  СП-{e}: не прочитан ({d['ошибка']})")
            continue
        items, meta = d["items"], d["meta"]
        sem = collections.Counter(подпись_сем.get((meta.get(str(x.get("stageId"))) or {}).get("sem"), "в работе")
                                  for x in items)
        стадии = collections.Counter(имя_стадии((meta.get(str(x.get("stageId"))) or {}).get("name")) for x in items)
        if e == CHECK_ENTITY:
            комп = {_id_из(x.get(CHECK_COMPANY)) or _uid(x.get("companyId")) for x in items} - {""}
            связи = (f"компания (поле) {доля(sum(1 for x in items if _id_из(x.get(CHECK_COMPANY))), len(items))}, "
                     f"companyId {доля(sum(1 for x in items if _uid(x.get('companyId'))), len(items))}")
        else:
            комп = {_uid(x.get("companyId")) for x in items} - {""}
            связи = (f"со сделкой {доля(sum(1 for x in items if _есть(x.get('parentId2'))), len(items))}, "
                     f"с заказом {доля(sum(1 for x in items if _есть(x.get(f'parentId{ORDER_ENTITY}'))), len(items))}, "
                     f"с компанией {доля(sum(1 for x in items if _uid(x.get('companyId'))), len(items))}")
        print(f"  СП-{e}: карточек {len(items)}, воронок {d['cats']}; {счёт(sem)}; стадии: {счёт(стадии)}; связи: "
              f"{связи}; поставщиков живых заказов с карточкой {доля(len(поставщики & комп), len(поставщики))}")


def отчёт_задачи(п: Прогон, строки: list[dict]) -> None:
    print("\n12. ЗАДАЧИ (tasks.*)")
    с_назн = [r for r in строки if _uid(r["o"].get(З["taskAssign"]))]
    с_стат = [r for r in строки if _uid(r["o"].get(З["taskStatus"]))]
    if п.задачи_право is None:
        print(f"  ID задач в полях заказов прочитанных частей нет — право не проверялось "
              f"(«Назначить менеджера ОРК» {len(с_назн)}, «Актуализировать статус» {len(с_стат)})")
        return
    if п.задачи_право != "да":
        print(f"  отказ портала ({п.задачи_право}): задачи не читаются; заказов с ID задачи «Назначить менеджера "
              f"ОРК» {len(с_назн)}, «Актуализировать статус производства» {len(с_стат)}")
        return

    def закрыто(рр, k):
        return sum(1 for r in рр if (п.задачи.get(_uid(r["o"].get(З[k]))) or {}).get("closed"))
    найдено = sum(1 for r in с_назн if _uid(r["o"].get(З["taskAssign"])) in п.задачи)
    print(f"  «Назначить менеджера ОРК»: заказов с ID {len(с_назн)}, задача прочитана {доля(найдено, len(с_назн))}, "
          f"закрыта {доля(закрыто(с_назн, 'taskAssign'), len(с_назн))}")
    произв = [r for r in строки if r["live"] and r["step"] == 7]
    print(f"  «Актуализировать статус производства»: заказов с ID {len(с_стат)}, закрыта "
          f"{доля(закрыто(с_стат, 'taskStatus'), len(с_стат))}; у живых на шаге 7 ID есть "
          f"{доля(sum(1 for r in произв if _uid(r['o'].get(З['taskStatus']))), len(произв))}")


def _артефакты(строки: list[dict]) -> tuple[int, int, list[str]]:
    """M2: Σ есть / Σ требуется по парам (заказ, артефакт) — заказ прошёл шаг после даты поля."""
    есть = нужно = 0
    части = []
    for a in ork.ARTIFACTS:
        since = dt.date.fromisoformat(a["since"])
        рр = [r for r in строки if _проходит(r, a["need"]) and r["создан"] and _дн(r["создан"]) >= since]
        if a["cond"] == "import":
            части.append(f"{a['name']} — до сверки «импорт/РФ» не требуется")
            continue
        if a["cond"] == "prepay":
            рр = [r for r in рр if _ч(r["o"].get(З["firstPay"])) > 0]
        k = sum(1 for r in рр if _есть(r["o"].get(a["field"])))
        есть, нужно = есть + k, нужно + len(рр)
        части.append(f"{a['name']} {доля(k, len(рр))}")
    return есть, нужно, части


def _показатели(п: Прогон, строки: list[dict], гр: dict, сег: dict, люди: dict, пф: dict) -> list[tuple]:
    """Входы ворот по каждому показателю: (код, имя, value, n, cover, {agree, burst, plan_eq_fact, need})."""
    живые = [r for r in строки if r["live"]]
    гиг = [ork.hygiene(r["ork"], п.сегодня) for r in живые]
    print(f"  живых заказов {len(живые)}; гигиена: стадия позади фактов {sum(g['behind'] for g in гиг)}, брошенных "
          f"(> {ork.GATES['abandoned_wd']} р.д. на шаге с нормой ≤ {ork.GATES['abandoned_norm_max']}) "
          f"{sum(g['abandoned'] for g in гиг)}, стадия не из таблицы {sum(g['unknown'] for g in гиг)} — вне счёта W2")
    в_счёт = [r for r, g in zip(живые, гиг) if not g["exclude"]]
    нормир = [x for x in (ork.overrun(r["ork"], п.сегодня) for r in в_счёт) if x["norm"] is not None]
    сверх = [x for x in нормир if x["over"]]
    по_шагам = collections.Counter("шаг " + str(x["step"]) for x in сверх)
    print(f"  W2 сверх нормы ({ork.NO_PAUSE}): {len(сверх)} из {len(нормир)} на нормируемых шагах; по шагам: "
          f"{счёт(по_шагам)}; срок не посчитать {sum(1 for x in нормир if x['over'] is None)}")
    шаг1 = [x for x in нормир if x["step"] == 1]
    dl = [r for r in живые if _дн(r["o"].get(З["dl"]))]
    поздно = sum(1 for r in dl if not r["факты"]["delFact"] and _дн(r["o"].get(З["dl"])) < п.сегодня)
    кп = [c for r in живые for c in ork.checkpoints(r["ork"], п.сегодня)]
    произв = [r for r in живые if r["step"] in (6, 7)]
    с_датами = sum(1 for r in произв if _дн(r["o"].get(З["prodStart"])) and _дн(r["o"].get(З["prodEnd"])))
    м1 = [s for r in строки if r["создан"] and _дн(r["создан"]) >= ORK_SINCE
          for k, s in r["seg"]["segs"].items() if k in ("S1", "S2", "S3", "S6")]
    м1_чист = [s for s in м1 if s["inNorm"] is not None and not s["burst"] and not s["ret"] and not s["neg"]]
    есть, нужно, арт = _артефакты(строки)
    print(f"  полнота карточки по артефактам: {'; '.join(арт)}")
    q3 = [r for r in строки if r["создан"] and _дн(r["создан"]) >= Q3_SINCE]
    q3_есть = [r for r in q3 if _есть(r["o"].get(З["firstPay"]))]

    def дл(a, b):
        return (a / b) if b else None
    out = [
        ("W1", "приёмка S1 сверх 1 р.д.", sum(1 for x in шаг1 if x["over"]), len(живые), 1.0 if живые else None,
         {"agree": (гр.get("S1.конец") or {}).get("agree")}),
        ("W2", "сверх нормы, только ОРК", len(сверх), len(нормир),
         дл(sum(1 for x in нормир if x["over"] is not None), len(нормир)), {}),
        ("W3", "зона риска: срок клиента прошёл", поздно, len(живые), дл(len(dl), len(живые)), {}),
        ("W4", "открытые эскалации и паузы", None, 0, None,
         {"need": "поля «Эскалация» и «Пауза» — заявка администратору Битрикса"}),
        ("W5", "контрольные точки недели", len(кп), len(произв), дл(с_датами, len(произв)), {}),
        ("M1", f"своевременность S1/S2/S3/S6 (заказы с {ORK_SINCE:%d.%m.%Y})",
         дл(sum(1 for s in м1_чист if s["inNorm"]), len(м1_чист)), len(м1_чист), дл(len(м1_чист), len(м1)),
         {"burst": дл(sum(1 for s in м1 if s["burst"]), len(м1))}),
        ("M2", "полнота карточки", дл(есть, нужно), нужно, None, {}),
        ("M3", "загрузка менеджеров ОРК (людей)", люди.get("людей"), люди.get("n"), люди.get("cover"), {}),
        ("Q1", "первичный срок поставщика", пф.get("value"), пф.get("n"), пф.get("cover"),
         {"plan_eq_fact": пф.get("plan_eq_fact")}),
        ("Q2", "полный цикл, медиана кал. дн.", (сег.get("цикл") or {}).get("value"), (сег.get("цикл") or {}).get("n"),
         None, {}),
        ("Q3", f"схема оплаты: доля 100 % предоплаты (с {Q3_SINCE:%d.%m.%Y})",
         дл(sum(1 for r in q3_есть if _ч(r["o"].get(З["firstPay"])) >= 100), len(q3_есть)), len(q3_есть),
         дл(len(q3_есть), len(q3)), {}),
    ]
    for код, *_x in ork.SEGMENTS:
        s = сег.get(код) or {}
        out.append((код, f"сегмент: медиана р.д. за {ДНЕЙ_ГОД} дн.", s.get("value"), s.get("n"), s.get("cover"),
                    {"agree": s.get("agree"), "burst": s.get("burst")}))
    return out


def отчёт_вердикты(п: Прогон, строки: list[dict], гр: dict, сег: dict, люди: dict, пф: dict) -> dict:
    """13. Вердикт по каждому показателю: ork.metric_state на воротах ork.GATES."""
    print(f"\n13. ВЕРДИКТ (ворота ork.GATES — {ork.GATES_NOTE})")
    итог = {}
    for код, имя, value, n, cover, доп in _показатели(п, строки, гр, сег, люди, пф):
        st = ork.metric_state(value, n, cover, agree=доп.get("agree"), burst=доп.get("burst"),
                              plan_eq_fact=доп.get("plan_eq_fact"), need=доп.get("need", ""))
        хвост = [f"n {n}" if n else "", f"покрытие {пр(cover)}" if cover is not None else "",
                 f"опоры {пр(доп.get('agree'))}" if доп.get("agree") is not None else "",
                 f"«пачек» {пр(доп.get('burst'))}" if доп.get("burst") is not None else "",
                 f"«план = факт» {пр(доп.get('plan_eq_fact'))}" if доп.get("plan_eq_fact") is not None else "",
                 st["why"], f"нужно: {доп['need']}" if доп.get("need") else ""]
        print(f"  {код} {имя}: {ВЕРДИКТ[st['state']]}" + "".join(f" · {x}" for x in хвост if x))
        итог[код] = st["state"]
    return итог


def отчёт(п: Прогон) -> dict:
    строки = _в_когортах(п)
    print(f"\nВ ВЫВОДАХ: заказов {len(строки)} (живых {sum(1 for r in строки if r['live'])}), сделок "
          f"{len({r['did'] for r in строки})}; по полугодиям создания: "
          + счёт(collections.Counter(r["пг"] for r in строки), sorted({r["пг"] for r in строки})))
    п.поставщики_1192 = {_uid(x.get("companyId")) for x in (п.сп.get(1192) or {}).get("items") or []} - {""}
    отчёт_календарь(п, строки)
    гр = отчёт_границы(строки)
    сег = отчёт_сегменты(п, строки, гр)
    отчёт_порядок(строки)
    отчёт_метки(строки)
    люди = отчёт_люди(п, строки)
    отчёт_упд(п, строки)
    пф = отчёт_план_факт(п, строки)
    отчёт_поставщики(п, строки)
    отчёт_счета(п)
    отчёт_договоры(п, строки)
    отчёт_задачи(п, строки)
    return отчёт_вердикты(п, строки, гр, сег, люди, пф)


def main(client=None, сегодня: dt.date | None = None) -> int:
    п = Прогон(client, сегодня or period_mod.now(MSK).date())
    if п.client is None:
        import config
        п.client = bc.BitrixClient(config.Settings.load().bitrix_webhook_url)
    if not шаг_ожидание(п) or not шаг_основа(п):
        print(bc.сводка_нагрузки())
        return 1
    шаг_отдел(п)
    шаг_части(п)
    шаг_смарт(п)
    отчёт(п)
    print("\nНАГРУЗКА ПО БЛОКАМ: " + (" · ".join(f"{k} {v}" for k, v in п.учёт.по_блокам.items()) or "—")
          + f" · смета была {п.смета}")
    print(bc.сводка_нагрузки())
    return 0 if п.полно else 1


if __name__ == "__main__":
    # Трассировка с содержимым карточек в публичный журнал не уходит: только тип ошибки и
    # места в коде (файл:строка), без сообщения.
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"::error::зонд упал: {type(e).__name__} · {место(e)}")
        print(bc.сводка_нагрузки())
        raise SystemExit(1)
