#!/usr/bin/env python3
"""Зонд v62 «Пресейл по регламенту сорсера»: чем портал подтверждает инструкцию сорсера v1.4
по воронке сделок «Пресейл» — до того, как дашборд пересоберётся на её стадиях, нормах и правилах.

ПОВОД (30.10.2026). Владелец дал инструкцию сорсера v1.4 (29.10.2026). Стадии воронки «Пресейл»
по порядку, кто закрывает и норма в РАБОЧИХ днях по производственному календарю РФ
(period.workdays_ru):
   1 Новый запрос — КАМ — без нормы
   2 Назначение сорсера — РОС (руководитель сорсинга) — без нормы
   3 Проработка ТЗ и вопросы заказчику — сорсер — 2
   4 Поиск поставщиков и сбор КП — сорсер — 1 (разослать запросы) + 1 (собрать КП)
   5 Анализ КП и фиксация поставщиков — сорсер — 3
   6 Сравнение предложений и расчёт ЭП — сорсер + КАМ + продукт — 2
   7 Экономика готова | Можно подавать — КАМ (отправляет ТКП) — без нормы
   8 Доработка по обратной связи — сорсер + КАМ — 1
   9 Сорсинг завершен / Ожидаем решение заказчика — КАМ — без нормы (вход = ТКП отправлено)
  10 Согласование финальной спецификации — продукт
  11 Сделка выиграна — успех; стадии отказа — с причиной.
Тип сделки «Расценка» (поле «Deal type (Presale)») пропускает шаги 3, 5, 6. Норма КП: ≥ 3 у сделки с
флажком «ЗИП», ≥ 1 у сложного оборудования, 1 у «Расценки». Запрос поставщику (СП-166 «Запросы
поставщикам», воронка 24): нет ответа — письмо-напоминание на 2-й р.д., звонок на 3-й, после 10 р.д. —
стадия «Ответ не получен (в срок)». КП засчитывается только файлом в поле config.RFQ_OFFER_FIELD.

СТАДИЯ → ШАГ — по имени стадии портала (таблица РЕГЛАМЕНТ, первое совпадение); успех — шаг 11,
отказ — без шага; рабочая стадия, не узнанная ни одним образцом, — «вне регламента v1.4» и
печатается в п. 2 (тендерные стадии, «Техническая часть | Квалификация» и т. п.). Шаг регламента без
стадии в воронке печатается отдельно.

ЧТО ПЕЧАТАЕТ:
   1. поля сделки по подписи (РУ/EN, без учёта регистра; таблица ПОЛЯ): код, подпись, тип,
      множественность; подписи вариантов — только у полей процесса (тип сделки, ЗИП, вопросы
      заказчику, обновить экономику, «Техника согласована», «Экономика готова», «Финальную
      спецификацию подтверждаю», поля «ТКП») и только похожие на подпись; заполненность среди
      сделок пресейла — всего и по текущему шагу;
   2. стадии воронки: STAGE_ID по SORT, имя, семантика, шаг регламента, кто закрывает, норма,
      сделок сейчас;
   3. история стадий сделок, созданных за ОКНО_СДЕЛОК_ДН: по шагу — входов, р.д. в стадии (медиана,
      P80), доля в норме, сейчас идёт и сверх нормы; разрез «Расценка» / остальные; пропуск 3/5/6 у
      «Расценки» (и у остальных); возвраты (шаг назад) по парам;
   4. КП → ТКП: (а) вошли в «Экономика готова» → позже шаг 9+ (доля, р.д. между), по группам
      «Расценка» / «ЗИП» / прочие; поле «Экономика готова» = «Да» как вторая опора; (б) сделки с ≥ 1
      КП-файлом под ними → дошли до 7 и до 9+ по истории; сколько из них не видит старый способ
      (stages.deal_reached_tkp по текущей стадии) и сколько он видит лишних;
   5. дожим запросов (карточки СП-166 воронки 24 под сделками пресейла, созданные за
      ОКНО_КАРТОЧЕК_ДН): дела карточек по виду (коды), первое исходящее письмо, второе письмо к
      2-му р.д., звонок к 3-му р.д. (дела TYPE_ID 2) и звонки на всех карточках СП-166 за окно,
      р.д. от первого письма до ответа и до КП (история стадий СП-166; второй способ — movedTime, их
      совпадение печатается), «Request Sent» старше 10 р.д., «отправлен» → «Ответ не получен»,
      шаг 4 → первое письмо (норма 1 р.д.), КП на сделку против нормы по группам;
   6. срок заказчика («End date and time for receiving offer»): заполненность, доля дошедших до
      «Экономика готова» раньше срока, запас в р.д., открытые с прошедшим сроком;
   7. право вебхука на tasks.task.list — «есть» или код отказа.
Каждый раздел кончается строками «вердикт: показать / предварительно / скрыть» по воротам ВОРОТА
(n ≥ 30 и покрытие данных ≥ 80 % — показать; n ≥ 10 — предварительно; иначе скрыть; покрытие —
меньшее из покрытия данных и покрытия частей), в конце — сводка вердиктов.

ЧАСТИ. Вход PROBE_PARTS ∈ {10, 25, 50}, меньше 10 — 10 (распоряжение 22.09). Разбиение одно — по ID
сделки смежными срезами (все карточки сделки в её части): карточки СП-166, история стадий сделок
окна, история и дела карточек за ОКНО_КАРТОЧЕК_ДН. Упавшая часть выпадает из выводов вслух
(«частей прочитано k из n; сделок вне выводов N») и снижает покрытие каждого вердикта.

ИСТОРИЯ СДЕЛОК — входы в стадии воронки пресейла по STAGE_ID массивом (справочник DEAL_STAGE_<cid>),
а не по CATEGORY_ID: прогон v60 по CATEGORY_ID получил записи чужой воронки (урок — в шапке
scripts/probe_regulation.py и в probe_conversion.читать_историю). Сделка, ушедшая из воронки,
последнего выхода не имеет — такое пребывание считается «выход неизвестен».
ИСТОРИЯ КАРТОЧЕК — crm.stagehistory.list, entityTypeId 166, OWNER_ID массивом (тот же
probe_conversion.читать_историю). Не читается (код отказа, фильтр не сработал) — печатается, какой
способ взят: сроки карточек — по movedTime (вход в текущую стадию), с оговоркой.
ДЕЛА КАРТОЧЕК — crm.activity.list, OWNER_TYPE_ID 166, OWNER_ID массивом по 50, по ключу >ID; каждая
страница сверяется с фильтром. Фильтр массивом не сработал — один раз читаются все дела СП-166 за
окно по ключу (как main._mail_activities), если помещаются в предел, и отбираются свои карточки.
Письмо — PROVIDER_ID CRM_EMAIL, исходящее — DIRECTION 2 (main._send_stats); звонок — TYPE_ID 2.
Время прикрепления файла КП портал в карточке не отдаёт: «КП отмечено в тот же день» не меряется.

ЖУРНАЛ ПУБЛИЧНЫЙ (правило 17, распоряжение 30.09): счёт, доли, медианы и P80, коды полей и стадий,
подписи полей, имена стадий воронки пресейла и СП-166 (только похожие на подпись), подписи вариантов
полей процесса. Ни людей, ни компаний, ни сделок, ни сумм, ни текста ошибок портала — у отказа
только код, у падения — тип и место.

НАГРУЗКА: при ≈ 250 сделках и 10 частях ≈ 430 запросов при 1/с (probe.yml), ориентир 600, предел
1 000: смета больше предела — прогон не начат; часть, которой не хватает сметы, не читается вслух;
необязательные блоки (дела окном, звонки, задачи) — тоже. Первая строка — «ожидается: …»,
последняя — сводка_нагрузки().

    python scripts/probe_presale_reg.py        # нужен BITRIX_WEBHOOK_URL
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
import config  # noqa: E402
import period as period_mod  # noqa: E402
import presale  # noqa: E402
import probe_conversion as pc  # noqa: E402
from stages import KP_STAGES, classify_stage, deal_reached_tkp  # noqa: E402

MSK = period_mod.MSK
SPA = config.SPA_ENTITY_TYPE_ID          # 166 «Запросы поставщикам»
SPA_CAT = config.SPA_CATEGORY_ID         # 24 — воронка запросов
OFFER_F = config.RFQ_OFFER_FIELD         # засчитанное КП — файл в этом поле (правило 19.10.2026)

# ── регламент (инструкция сорсера v1.4, 29.10.2026) ─────────────────────────────────────────
# (шаг, стадия по инструкции, образец имени стадии портала (нижний регистр, ё → е), кто закрывает,
#  норма р.д. или None, из чего норма)
РЕГЛАМЕНТ = (
    (1, "Новый запрос", r"новый\s+запрос", "КАМ", None, ""),
    (2, "Назначение сорсера", r"назначени\w*\s+сорсер", "РОС", None, ""),
    (3, "Проработка ТЗ и вопросы заказчику", r"проработк|вопрос\w*\s+заказчик", "сорсер", 2, "2"),
    (4, "Поиск поставщиков и сбор КП", r"поиск\s+поставщ|сбор\s+кп", "сорсер", 2, "1 + 1"),
    (5, "Анализ КП и фиксация поставщиков", r"анализ\s+кп|фиксаци\w*\s+поставщ", "сорсер", 3, "3"),
    (6, "Сравнение предложений и расчёт ЭП", r"сравнени\w*\s+предложен|расчет\s+эп", "сорсер + КАМ + продукт",
     2, "2"),
    (7, "Экономика готова | Можно подавать", r"экономика\s+готова|можно\s+подавать", "КАМ", None, ""),
    (8, "Доработка по обратной связи", r"доработк", "сорсер + КАМ", 1, "1"),
    (9, "Сорсинг завершен / Ожидаем решение заказчика", r"сорсинг\s+заверш|ожидаем\w*\s+решени", "КАМ", None,
     ""),
    (10, "Согласование финальной спецификации", r"согласовани\w*\s+финальн|финальн\w*\s+спецификац", "продукт",
     None, ""),
    (11, "Сделка выиграна", None, "—", None, ""),
)
ШАГ = {r[0]: r for r in РЕГЛАМЕНТ}
_ОБРАЗЦЫ = [(r[0], re.compile(r[2])) for r in РЕГЛАМЕНТ if r[2]]
ШАГ_ПОИСК, ШАГ_АНАЛИЗ, ШАГ_ЭКОНОМИКА, ШАГ_ДОРАБОТКА, ШАГ_ТКП, ШАГ_УСПЕХ = 4, 5, 7, 8, 9, 11
ПРОПУСК_РАСЦЕНКИ = (3, 5, 6)
ГРУППЫ = ("Расценка", "ЗИП", "прочие")
НОРМА_КП = {"Расценка": 1, "ЗИП": 3, "прочие": 1}
РАСЦЕНКА_RX = re.compile(r"расценк")
ДА_RX = re.compile(r"^(да|yes|y|1|true)$")

# ── карточка запроса (stages.py: корзины по правилам 19.10.2026) ────────────────────────────
ОТВЕТ = frozenset({"dialog", "paw", "selected", "notsel", "refused"})   # поставщик ответил (с ценой или без)
ДОЖИМ_ПИСЬМО_РД, ДОЖИМ_ЗВОНОК_РД, НЕТ_ОТВЕТА_РД = 2, 3, 10
ОТПРАВКА_РД = 1                          # шаг 4: разослать запросы — 1 р.д. от входа в шаг
ЗВОНОК_ТИП = "2"                         # crm.enum.activitytype: 2 — звонок
ПИСЬМО_ПРОВАЙДЕР, ИСХОДЯЩЕЕ = "CRM_EMAIL", "2"
КОД_RX = re.compile(r"^[A-Z0-9_]{2,40}$")

# ── окна, ворота, смета (правило 4: числа — таблицей) ───────────────────────────────────────
ОКНО_СДЕЛОК_ДН = 180                     # история стадий — сделки, созданные за столько дней
ОКНО_КАРТОЧЕК_ДН = 90                    # дожим — карточки СП-166, созданные за столько дней
ЗРЕЛОСТЬ_РД = 10                         # п. 4: «зрелая» — вход в «Экономика готова» не позже стольких р.д. назад
ВОРОТА = {"n_show": 30, "cover_show": 0.80, "n_prelim": 10}
БЮДЖЕТ = {"ожидается": 600, "предел": 1000}
СМЕТА = {
    "основа": 10,                        # воронки, два счёта, стадии, поля сделки, стадии СП-166, звонки, задачи
    "карточек_на_сделку": 9,             # зонд v48: 878 запросов под 95 сделками
    "доля_карточек_окна": 0.8,           # карточек за ОКНО_КАРТОЧЕК_ДН из всех под сделками
    "переходов_сделки": 8,
    "переходов_карточки": 3,
    "дел_на_карточку": 3,
}
ЧАСТИ = (10, 25, 50)
СТРАНИЦ_КАРТОЧЕК = 60                    # на пачку из 50 сделок; больше — фильтр, похоже, не сработал
СТРАНИЦ_ДЕЛ = 40                         # на пачку из 50 карточек
МИН_ЧУЖИХ = 3                            # чужих строк на странице не меньше и больше половины — фильтр не сработал
ВАРИАНТОВ_В_СТРОКЕ = 15

# ── поля сделки по подписи ──────────────────────────────────────────────────────────────────
# (ключ, подпись в журнале, образец подписи поля (нижний регистр, ё → е), коды наверняка,
#  поле процесса — подписи его вариантов печатаются)
ПОЛЯ = (
    ("dealType", "Deal type", r"deal\s*type", (), True),
    ("zip", "ЗИП", r"(?<![a-zа-я])(зип|zip)(?![a-zа-я])", (), True),
    ("deadline", "End date and time for receiving offer", r"end\s+date\s+and\s+time\s+for\s+receiving\s+offer", (),
     False),
    ("questions", "Status of questions for the customer", r"status\s+of\s+questions\s+for\s+the\s+customer", (),
     True),
    ("renew", "Need to renew economics", r"need\s+to\s+renew\s+economics", (), True),
    ("techOk", "Техника согласована", r"техника\s+согласован", (), True),
    ("tkp", "ТКП / TKP", r"(?<![a-zа-я])(ткп|tkp)(?![a-zа-я])", (), True),
    ("offer", "Offer from supplier(s)", r"offer\s+from\s+supplier", (), False),
    ("offerSum", "Offer sum and currency of selected supplier", r"offer\s+sum\s+and\s+currency", (), False),
    ("suppliers", "Suppliers", r"^(suppliers?|поставщики)$", (), False),
    ("econ", "Economics of the project", r"economics\s+of\s+the\s+project", (), False),
    ("econReady", "Экономика готова", r"экономика\s+готова", (config.ECON_READY_FIELD,), True),
    ("finalSpec", "Финальную спецификацию подтверждаю", r"финальн\w*\s+спецификаци\w*\s+подтвержда", (), True),
    ("sourcer", "Sourcer", r"^(sourcer|сорсер)$", (presale.SOURCER_F,), False),
    ("product", "Product leader", r"product\s+leader", (presale.PO_F,), False),
)
_ПОЛЯ_RX = {k: re.compile(rx) for k, _p, rx, _c, _f in ПОЛЯ}
СДЕЛКА_SELECT = ["ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "CATEGORY_ID", "DATE_CREATE", "MOVED_TIME"]
КАРТОЧКА_SELECT = ["id", "parentId2", "categoryId", "stageId", "createdTime", "movedTime", OFFER_F]
ДЕЛО_SELECT = ["ID", "OWNER_ID", "OWNER_TYPE_ID", "TYPE_ID", "PROVIDER_ID", "DIRECTION", "CREATED"]

# подпись для журнала: буквы, цифры, знаки — без формы юрлица, адресов и длинных номеров
МЕТКА_RX = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё .,/()%+\-–—|:«»\"'№?!&]{0,99}")
ЛИЦО_RX = re.compile(r"\b(ооо|оао|зао|пао|ао|ип|тоо|llc|ltd|limited|fze|fzco|gmbh|inc|corp|ag|bv|spa)\b", re.I)
АДРЕС_RX = re.compile(r"@|https?:|www\.|\d{5,}", re.I)
КОД_ОШИБКИ_RX = re.compile(r"^[\w.]+: ([A-Za-z][A-Za-z0-9_]{2,40})\b")


class ОшибкаЧасти(RuntimeError):
    """Шаг части не прошёл проверку полноты; сообщение — константа кода."""


class ФильтрНеСработал(ОшибкаЧасти):
    """Список по номерам вернул чужие строки: фильтр массивом портал не применил."""


# ── чистые функции ──────────────────────────────────────────────────────────────────────────

def доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else f"{a} из 0"


def пр(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f} %"


def мед(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def p80(xs) -> float | None:
    """P80 = s[⌈0,8·n⌉ − 1] (как зонд v60)."""
    s = sorted(x for x in xs if x is not None)
    return s[math.ceil(0.8 * len(s)) - 1] if s else None


def чс(x) -> str:
    return "—" if x is None else f"{x:g}" if isinstance(x, int) or float(x).is_integer() else f"{x:.1f}"


def счёт(c: collections.Counter, порядок=()) -> str:
    ключи = [k for k in порядок if c.get(k)] + [k for k, _ in c.most_common() if k not in порядок]
    return " · ".join(f"{k} {c[k]}" for k in ключи if c.get(k)) or "—"


def дн(xs) -> str:
    """n, медиана и P80 ряда р.д. одной строкой."""
    xs = [x for x in xs if x is not None]
    return f"n {len(xs)}, медиана {чс(мед(xs))}, P80 {чс(p80(xs))}"


def норм(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower().replace("ё", "е")


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


def _uid(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else None
    s = str(v or "").strip()
    return s if s.isdigit() and s != "0" else ""


def рд(a, b) -> int | None:
    """Рабочих дней от a до b по календарю РФ (period.workdays_ru); нет опоры — None."""
    if a is None or b is None:
        return None
    return period_mod.workdays_ru(a, b)


def похоже_на_подпись(txt: str) -> bool:
    return bool(МЕТКА_RX.fullmatch(txt)) and not ЛИЦО_RX.search(txt) and not АДРЕС_RX.search(txt)


def имя_стадии(name) -> str:
    """Имя стадии для журнала — похожее на подпись, иначе «(имя скрыто)»."""
    txt = re.sub(r"\s+", " ", str(name or "")).strip()
    return txt if похоже_на_подпись(txt) else "(имя скрыто)"


def подпись_поля(label) -> str:
    txt = re.sub(r"\s+", " ", str(label or "")).strip()
    return txt if похоже_на_подпись(txt) else "(подпись скрыта)"


def метки_вариантов(items) -> dict[str, str]:
    """Справочник списка → {ID: подпись для журнала}: похожая на подпись — как есть, иначе
    «вариант k» (k — по порядку справочника). Печатается только у полей процесса."""
    out = {}
    for k, it in enumerate(items or [], 1):
        txt = re.sub(r"\s+", " ", str(it.get("VALUE") or "")).strip()
        out[str(it.get("ID"))] = txt if похоже_на_подпись(txt) else f"вариант {k}"
    return out


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


def разбить(сделки, n: int) -> list[list[str]]:
    """ID сделок → не больше n смежных срезов по возрастанию номера (карточки сделки — в её срезе)."""
    ids = sorted({str(x) for x in сделки}, key=int)
    if not ids:
        return []
    шаг = max(1, math.ceil(len(ids) / n))
    return [ids[i:i + шаг] for i in range(0, len(ids), шаг)]


def пачками(ids, n: int = 50):
    ids = sorted({str(x) for x in ids if _uid(x)}, key=int)
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def п50(x: float) -> int:
    return math.ceil(x / 50) if x > 0 else 0


def смета_части(сделок: int, окна: int, карт: float | None = None, карт90: float | None = None) -> int:
    """Запросов на часть: карточки (страницы + по одному на пачку), история сделок окна, история и
    дела карточек окна. Без карт и карт90 — по оценке СМЕТА."""
    карт = сделок * СМЕТА["карточек_на_сделку"] if карт is None else карт
    карт90 = карт * СМЕТА["доля_карточек_окна"] if карт90 is None else карт90
    return (п50(карт) + п50(сделок) + п50(окна * СМЕТА["переходов_сделки"]) + п50(окна)
            + п50(карт90 * СМЕТА["переходов_карточки"]) + п50(карт90)
            + п50(карт90 * СМЕТА["дел_на_карточку"]) + п50(карт90))


def смета(сделок: int, окна: int, частей_n: int) -> int:
    k = max(1, min(частей_n, сделок))
    return (СМЕТА["основа"] + п50(сделок) + 1
            + k * смета_части(math.ceil(сделок / k), math.ceil(окна / k)))


def вердикт(n: int, покрытие: float | None, gates: dict = ВОРОТА) -> str:
    """Ворота показа: n ≥ n_show и покрытие ≥ cover_show — показать; n ≥ n_prelim — предварительно;
    иначе скрыть. Покрытие None — не меряется (не ограничивает)."""
    if n >= gates["n_show"] and (покрытие is None or покрытие >= gates["cover_show"]):
        return "показать"
    if n >= gates["n_prelim"]:
        return "предварительно"
    return "скрыть"


def шаг_стадии(name, sem) -> int | None:
    """Стадия пресейла → шаг инструкции v1.4: успех — 11, отказ — None, рабочая — по образцу имени
    (первое совпадение), не узнанная — None («вне регламента»)."""
    sem = (sem or "P").upper()[:1]
    if sem == "S":
        return ШАГ_УСПЕХ
    if sem == "F":
        return None
    n = норм(name)
    for шаг, rx in _ОБРАЗЦЫ:
        if rx.search(n):
            return шаг
    return None


def первые_входы(переходы, шаги: dict[str, int | None]) -> dict[int, dt.datetime]:
    """История стадий → {шаг: время первого входа}."""
    out: dict[int, dt.datetime] = {}
    for s, t in переходы or ():
        k, tt = шаги.get(str(s)), _вр(t)
        if k is None or tt is None:
            continue
        if k not in out or tt < out[k]:
            out[k] = tt
    return out


def пребывания(переходы, шаги: dict[str, int | None], стадия_сейчас: str, сегодня: dt.date) -> list[dict]:
    """Все входы (стадия, время) по времени → пребывания {stage, шаг, вход, выход, идёт, рд}.
    Выход — следующий вход; последний вход: «идёт», если сделка и сейчас в этой стадии (р.д. до
    сегодня), иначе выход неизвестен (сделка ушла из воронки: история — только стадий пресейла)."""
    tr = sorted(((str(s), _вр(t)) for s, t in переходы or () if _вр(t)), key=lambda x: x[1])
    out = []
    for i, (s, t) in enumerate(tr):
        выход = tr[i + 1][1] if i + 1 < len(tr) else None
        идёт = выход is None and s == str(стадия_сейчас or "")
        out.append({"stage": s, "шаг": шаги.get(s), "вход": t, "выход": выход, "идёт": идёт,
                    "рд": рд(t, выход) if выход else (рд(t, сегодня) if идёт else None)})
    return out


def возвраты(переходы, шаги: dict[str, int | None]) -> list[tuple[int, int]]:
    """Шаги назад: соседние по времени входы в шаги 1–10 (стадии вне регламента пропускаются)."""
    tr = sorted(((шаги.get(str(s)), _вр(t)) for s, t in переходы or () if _вр(t)), key=lambda x: x[1])
    seq = [k for k, _t in tr if k is not None and k < ШАГ_УСПЕХ]
    return [(a, b) for a, b in zip(seq, seq[1:]) if b < a]


def значения(v) -> list[str]:
    vs = v if isinstance(v, list) else [v]
    return [str(x).strip() for x in vs if str(x if x is not None else "").strip() not in ("", "0")]


def расценка(v, подписи: dict[str, str]) -> bool:
    """Тип сделки — «Расценка»: подпись варианта (у списка) или сам текст (у строки)."""
    return any(РАСЦЕНКА_RX.search(норм(подписи.get(x, x))) for x in значения(v))


def да(v, тип: str, подписи: dict[str, str]) -> bool:
    """Флажок или список «Да/Нет» включён."""
    vs = значения(v)
    if тип == "boolean":
        return any(норм(x) in ("1", "y", "true", "да") for x in vs)
    if подписи:
        return any(ДА_RX.match(норм(подписи.get(x, ""))) for x in vs)
    return any(ДА_RX.match(норм(x)) for x in vs)


def до_срока(t: dt.datetime | None, raw) -> bool | None:
    """Вход t не позже срока raw. Срок без времени (или ровно 00:00) — сравнение по дню МСК.
    Срока нет или он не дата — None."""
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    s = str(raw or "").strip()
    срок = _вр(s)
    if t is None or срок is None:
        return None
    м = срок.astimezone(MSK)
    if ("T" not in s and " " not in s) or (м.hour, м.minute, м.second) == (0, 0, 0):
        return t.astimezone(MSK).date() <= м.date()
    return t <= срок


def чужих_много(свои: set[str], значения_) -> bool:
    """Фильтр по номерам не сработал: чужих значений на странице не меньше МИН_ЧУЖИХ и больше
    половины (немного чужих бывает: дело привязано к нескольким владельцам, в строке — основной)."""
    vs = [str(v) for v in значения_]
    чужих = sum(1 for v in vs if v not in свои)
    return чужих >= МИН_ЧУЖИХ and 2 * чужих > len(vs)


def разбор_карточки(письма: list, звонки: list, переходы, сегодня: dt.date) -> dict:
    """Карточка запроса → сроки дожима от ПЕРВОГО исходящего письма (р.д. по календарю РФ):
    second — до второго письма, call — до первого звонка после письма, reply — до первого входа в
    стадию ответа (переписка, Price at Work, Selected, не выбран, отказ), kp — до первого входа в
    стадию КП; age — от письма до сегодня. Ответ раньше письма (день раньше) — before, срок
    ответа тогда не считается. переходы None — истории нет: reply и kp — None."""
    r = {"first": None, "second": None, "call": None, "reply": None, "kp": None, "age": None, "before": False,
         "hist": переходы is not None}
    письма = sorted(t for t in письма if t)
    if not письма:
        return r
    first = письма[0]
    r["first"], r["age"] = first, рд(first, сегодня)
    if len(письма) > 1:
        r["second"] = рд(first, письма[1])
    после = sorted(t for t in звонки if t and t >= first)
    if после:
        r["call"] = рд(first, после[0])
    if переходы is not None:
        tr = sorted(((classify_stage(str(s)), _вр(t)) for s, t in переходы if _вр(t)), key=lambda x: x[1])
        for ключ, корзины in (("reply", ОТВЕТ), ("kp", KP_STAGES)):
            t = next((t for b, t in tr if b in корзины), None)
            w = рд(first, t)
            if w is not None and w < 0:
                r["before"] = r["before"] or ключ == "reply"
            elif w is not None:
                r[ключ] = w
    return r


def без_ответа_к(r: dict, к_рд: int, корзина_сейчас: str) -> bool:
    """Карточка без ответа к к_рд-му р.д. от первого письма. С историей — по первому входу в стадию
    ответа; без истории — карточка и сейчас в «отправлен» или «нет ответа» (приближение)."""
    if r["before"]:
        return False
    if r["hist"]:
        return r["reply"] is None or r["reply"] > к_рд
    return корзина_сейчас in ("sent", "no_answer")


def корзина_кп(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2" if n == 2 else "3–5" if n <= 5 else "6+"


# ── учёт запросов ───────────────────────────────────────────────────────────────────────────

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


# ── прогон ──────────────────────────────────────────────────────────────────────────────────

class Прогон:
    def __init__(self, client, сегодня: dt.date):
        self.client, self.сегодня = client, сегодня
        self.учёт = Учёт()
        self.частей, self.замечание_частей = частей()
        self.с_окна = сегодня - dt.timedelta(days=ОКНО_СДЕЛОК_ДН)
        self.с_карточек = сегодня - dt.timedelta(days=ОКНО_КАРТОЧЕК_ДН)
        self.cid = ""
        self.ожид = (0, 0)
        self.смета = 0
        self.стадии: dict[str, dict] = {}          # STAGE_ID → {name, sem, sort, шаг}
        self.поля: dict[str, list[dict]] = {}       # ключ ПОЛЯ → [{code, label, type, multiple, items}]
        self.мета_полей: dict[str, dict] = {}       # код → то же
        self.сделки: dict[str, dict] = {}
        self.стадии_сп: dict[str, dict] = {}       # стадии СП-166 воронки 24
        self.карточки: list[dict] = []
        self.ист_с: dict[str, list] = {}
        self.ист_к: dict[str, list] = {}
        self.дела: dict[str, list[dict]] = collections.defaultdict(list)
        self.ист166: str | None = None             # None — не пробовали; «да»; иначе причина
        self.дела_способ: str | None = None        # None — не пробовали; «массив»; «окно»; иначе причина
        self.звонков_всего: int | None = None
        self.задачи_право: str | None = None
        self.части: list[list[str]] = []
        self.прочитанные: set[str] = set()
        self.упавшие: list[int] = []
        self.покрытие_частей: float | None = None  # None — все части прочитаны
        self.вердикты: list[tuple[str, str, str]] = []

    @property
    def полно(self) -> bool:
        return not self.упавшие

    def в_окне(self, did: str) -> bool:
        d = _дн((self.сделки.get(did) or {}).get("DATE_CREATE"))
        return bool(d and d >= self.с_окна)

    def карточка_окна(self, c: dict) -> bool:
        d = _дн(c.get("createdTime"))
        return str(c.get("categoryId")) == str(SPA_CAT) and bool(d and d >= self.с_карточек)

    def поле(self, ключ: str) -> dict | None:
        """Главное поле ключа: тип сделки — с вариантом «Расценка», ЗИП — флажок, срок — дата."""
        вс = self.поля.get(ключ) or []
        if not вс:
            return None
        if ключ == "dealType":
            return next((f for f in вс if any(РАСЦЕНКА_RX.search(норм(i.get("VALUE"))) for i in f["items"])), вс[0])
        if ключ == "zip":
            return next((f for f in вс if f["type"] == "boolean"), вс[0])
        if ключ == "deadline":
            return next((f for f in вс if f["type"] in ("datetime", "date")), вс[0])
        if ключ == "econReady":
            return next((f for f in вс if f["code"] == config.ECON_READY_FIELD), вс[0])
        return вс[0]

    def подписи(self, f: dict | None) -> dict[str, str]:
        return {str(i.get("ID")): str(i.get("VALUE") or "") for i in (f or {}).get("items") or []}

    def группа(self, d: dict) -> str:
        ft, fz = self.поле("dealType"), self.поле("zip")
        if ft and расценка(d.get(ft["code"]), self.подписи(ft)):
            return "Расценка"
        if fz and да(d.get(fz["code"]), fz["type"], self.подписи(fz)):
            return "ЗИП"
        return "прочие"

    def шаги(self) -> dict[str, int | None]:
        return {sid: m["шаг"] for sid, m in self.стадии.items()}

    def шаг_сделки(self, d: dict) -> int | None:
        return (self.стадии.get(str(d.get("STAGE_ID") or "")) or {}).get("шаг")

    def сем(self, d: dict) -> str:
        m = self.стадии.get(str(d.get("STAGE_ID") or ""))
        return (m or {}).get("sem") or str(d.get("STAGE_SEMANTIC_ID") or "P").upper()[:1] or "P"

    def покрытие(self, данных: float | None) -> float | None:
        вс = [x for x in (данных, self.покрытие_частей) if x is not None]
        return min(вс) if вс else None

    def вердикт(self, раздел: str, имя: str, n: int, покрытие_данных: float | None) -> str:
        cov = self.покрытие(покрытие_данных)
        v = вердикт(n, cov)
        self.вердикты.append((раздел, имя, v))
        print(f"    вердикт «{имя}»: {v} · n {n}" + (f" · покрытие {пр(cov)}" if cov is not None else ""))
        return v


def _total(client, method: str, params: dict) -> int:
    env = client.call_envelope(method, dict(params, start=0)) or {}
    return int(env.get("total") or 0)


def шаг_ожидание(п: Прогон) -> bool:
    """Первая строка журнала: воронка, сколько ждём сделок, частей и запросов."""
    try:
        res = п.client.call("crm.category.list", {"entityTypeId": 2}) or {}
        cats = (res.get("categories") if isinstance(res, dict) else res) or []
        имена = {str(c.get("id") if c.get("id") is not None else c.get("ID")): str(c.get("name") or c.get("NAME") or "")
                 for c in cats}
        найдено = presale.find_category(имена)
        if not найдено:
            print("ожидается: — (воронка сделок «Пресейл» по имени не найдена); прогон не начат")
            return False
        п.cid = найдено[0]
        всего = _total(п.client, "crm.deal.list", {"filter": {"CATEGORY_ID": п.cid}, "select": ["ID"]})
        окна = _total(п.client, "crm.deal.list", {"filter": {"CATEGORY_ID": п.cid,
                                                            ">=DATE_CREATE": п.с_окна.isoformat() + "T00:00:00"},
                                                 "select": ["ID"]})
    except Exception as e:  # noqa: BLE001 — печатается только код
        print(f"ожидается: — (число сделок не прочитано: {код_ошибки(e)}); прогон не начат")
        return False
    п.ожид = (всего, окна)
    п.смета = смета(всего, окна, п.частей)
    print(f"ожидается: воронка пресейла #{п.cid}, сделок {всего} (создано за {ОКНО_СДЕЛОК_ДН} дн. {окна}), "
          f"карточек СП-{SPA} ≈ {всего * СМЕТА['карточек_на_сделку']} (оценка {СМЕТА['карточек_на_сделку']} на "
          f"сделку), частей {п.частей}{(' (' + п.замечание_частей + ')') if п.замечание_частей else ''}, смета ≈ "
          f"{п.смета} запросов (ориентир {БЮДЖЕТ['ожидается']}, предел {БЮДЖЕТ['предел']}; при 1/с ≈ "
          f"{math.ceil(п.смета / 60)} мин)")
    if п.смета > БЮДЖЕТ["предел"]:
        print(f"смета {п.смета} больше предела {БЮДЖЕТ['предел']} — прогон не начат (меньше частей — меньше смета)")
        return False
    return True


def _стадии(client, cid: str) -> dict[str, dict]:
    ent = "DEAL_STAGE" if str(cid) == "0" else f"DEAL_STAGE_{cid}"
    out: dict[str, dict] = {}
    for s in client.list_paged("crm.status.list", {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"}}):
        sid = str(s.get("STATUS_ID") or "")
        extra = s.get("EXTRA") if isinstance(s.get("EXTRA"), dict) else {}
        sem = (str(s.get("SEMANTICS") or extra.get("SEMANTICS") or "P").upper()[:1]) or "P"
        sem = sem if sem in ("S", "F") else "P"
        name = str(s.get("NAME") or sid)
        out[sid] = {"name": name, "sem": sem, "sort": int(s.get("SORT") or 0), "шаг": шаг_стадии(name, sem)}
    return out


def найти_поля(fields: dict) -> dict[str, list[dict]]:
    """crm.deal.fields → {ключ ПОЛЯ: [поле…]} по подписи (formLabel, listLabel, filterLabel, title) и
    по кодам наверняка."""
    out: dict[str, list[dict]] = {}
    for code, meta in (fields or {}).items():
        if not isinstance(meta, dict):
            continue
        подписи = [str(meta.get(k) or "") for k in ("formLabel", "listLabel", "filterLabel", "title")]
        label = next((x for x in подписи if x and x != code), code)
        f = {"code": code, "label": label, "type": str(meta.get("type") or ""),
             "multiple": bool(meta.get("isMultiple")), "items": list(meta.get("items") or [])}
        for ключ, _п, _rx, коды, _proc in ПОЛЯ:
            if code in коды or any(_ПОЛЯ_RX[ключ].search(норм(x)) for x in подписи if x and x != code):
                out.setdefault(ключ, []).append(f)
    return out


def шаг_основа(п: Прогон) -> bool:
    """Справочник стадий, поля сделки, сделки воронки списком (сверка с ожидаемым), стадии СП-166."""
    client = п.client
    try:
        with п.учёт.блок("основа"):
            п.стадии = _стадии(client, п.cid)
            fields = client.call("crm.deal.fields", {}) or {}
            п.поля = найти_поля(fields)
            п.мета_полей = {f["code"]: f for вс in п.поля.values() for f in вс}
            select = СДЕЛКА_SELECT + sorted(п.мета_полей)
            сделки = client.list_deals_fast(filter={"CATEGORY_ID": п.cid}, select=select)
            try:
                п.стадии_сп = client.spa_stage_meta(SPA, SPA_CAT)
            except Exception as e:  # noqa: BLE001 — имена стадий СП-166 нужны только для печати
                print(f"стадии СП-{SPA}: справочник не прочитан ({код_ошибки(e)}) — корзины по коду стадии")
    except Exception as e:  # noqa: BLE001
        print(f"основа не прочитана: {код_ошибки(e)} · {место(e)}")
        return False
    if len(сделки) < п.ожид[0]:
        print(f"сделок прочитано {len(сделки)} из {п.ожид[0]} — обход неполон, прогон остановлен")
        return False
    п.сделки = {str(d["ID"]): d for d in сделки}
    print(f"основа: стадий в воронке {len(п.стадии)}, полей сделки найдено по подписи {len(п.мета_полей)}, "
          f"сделок {len(п.сделки)}, стадий СП-{SPA} воронки {SPA_CAT} {len(п.стадии_сп)}")
    return True


def _карточки(client, ids: list[str]) -> list[dict]:
    """Карточки СП-166 под сделками: @parentId2 пачками по 50, по ключу >id; каждая страница
    сверяется с фильтром."""
    out: list[dict] = []
    for пачка in пачками(ids):
        свои, last = set(пачка), 0
        for _ in range(СТРАНИЦ_КАРТОЧЕК):
            res = client.call("crm.item.list", {
                "entityTypeId": SPA, "order": {"id": "ASC"}, "select": КАРТОЧКА_SELECT, "start": -1,
                "filter": {"@parentId2": [int(x) for x in пачка], ">id": last}})
            items = (res or {}).get("items", []) if isinstance(res, dict) else []
            if not items:
                break
            if any(_uid(x.get("parentId2")) not in свои for x in items):
                raise ФильтрНеСработал("crm.item.list: фильтр @parentId2 не сработал — обход остановлен")
            out.extend(items)
            last = int(items[-1]["id"])
            if len(items) < 50:
                break
        else:
            raise ОшибкаЧасти(f"crm.item.list: больше {СТРАНИЦ_КАРТОЧЕК} страниц на пачку — фильтр, похоже, "
                              "не сработал")
    return out


def _по_ключу(client, method: str, фильтр: dict, select, проверка=None, страниц: int = СТРАНИЦ_ДЕЛ) -> list[dict]:
    """Список по ключу >ID со start −1 (без подсчёта total); проверка(страница) — фильтр сработал."""
    out: list[dict] = []
    last = 0
    for _ in range(страниц):
        f = dict(фильтр)
        f[">ID"] = last
        ch = client.call(method, {"filter": f, "select": list(select), "order": {"ID": "ASC"}, "start": -1}) or []
        if isinstance(ch, dict):
            ch = list(ch.values())
        if not ch:
            return out
        if проверка is not None and not проверка(ch):
            raise ФильтрНеСработал(f"{method}: фильтр по номерам не сработал — обход остановлен")
        out.extend(ch)
        last = int(ch[-1]["ID"])
        if len(ch) < 50:
            return out
    raise ФильтрНеСработал(f"{method}: список длиннее {страниц} страниц — фильтр, похоже, не сработал")


def _дела(client, card_ids: list[str]) -> list[dict]:
    """Дела карточек СП-166: OWNER_ID массивом по 50; страница с чужими владельцами — фильтр не сработал."""
    out: list[dict] = []
    for пачка in пачками(card_ids):
        свои = set(пачка)

        def проверка(ch, свои=свои):
            return not чужих_много(свои, [r.get("OWNER_ID") if str(r.get("OWNER_TYPE_ID")) == str(SPA) else None
                                          for r in ch])
        out += _по_ключу(client, "crm.activity.list", {"OWNER_TYPE_ID": SPA, "OWNER_ID": [int(x) for x in пачка]},
                         ДЕЛО_SELECT, проверка)
    return out


def _часть(п: Прогон, часть: list[str], окно: list[str]) -> dict:
    client = п.client
    loc: dict = {"карточки": [], "ист_с": {}, "ист_к": {}, "дела": []}
    карточки = _карточки(client, часть)
    loc["карточки"] = карточки
    к90 = [str(c["id"]) for c in карточки if п.карточка_окна(c)]
    оценка = смета_части(0, len(окно), 0, len(к90))
    if п.учёт.сейчас() + оценка > п.учёт.предел:
        raise ОшибкаЧасти("смета части после чтения карточек не помещается в предел")
    if окно:
        ист, ош = pc.читать_историю(client, окно, стадии=set(п.стадии))
        if ош:
            raise ОшибкаЧасти(ош)
        loc["ист_с"] = ист
    if к90 and п.ист166 in (None, "да"):
        try:
            ист, ош = pc.читать_историю(client, к90, entity=SPA)
        except bc.BitrixLimitError:
            raise
        except bc.BitrixError as e:
            if п.ист166 is not None:
                raise
            ист, ош = {}, f"отказ портала {код_ошибки(e)}"
        if ош and п.ист166 is None:
            п.ист166 = ош
            print(f"история стадий СП-{SPA}: не читается ({ош}) — сроки карточек по movedTime")
        elif ош:
            raise ОшибкаЧасти(ош)
        else:
            if п.ист166 is None:
                п.ист166 = "да"
                print(f"история стадий СП-{SPA} (entityTypeId {SPA}): читается — сроки карточек по истории")
            loc["ист_к"] = ист
    if к90 and п.дела_способ in (None, "массив"):
        try:
            loc["дела"] = _дела(client, к90)
            п.дела_способ = "массив"
        except ФильтрНеСработал:
            if п.дела_способ is not None:
                raise
            п.дела_способ = "окно"
            print("дела карточек: фильтр OWNER_ID массивом не сработал — дела читаются одним обходом окна")
    return loc


def шаг_части(п: Прогон) -> None:
    п.части = разбить(list(п.сделки), п.частей)
    for k, часть in enumerate(п.части, 1):
        окно = [d for d in часть if п.в_окне(d)]
        оценка = смета_части(len(часть), len(окно))
        if п.учёт.сейчас() + оценка > п.учёт.предел:
            п.упавшие.append(k)
            print(f"часть {k} из {len(п.части)}: не читается — смета {оценка} не помещается в предел")
            continue
        try:
            with п.учёт.блок("части"):
                loc = _часть(п, часть, окно)
        except ОшибкаЧасти as e:
            п.упавшие.append(k)
            print(f"часть {k} из {len(п.части)}: не прочитана — {e}")
            continue
        except Exception as e:  # noqa: BLE001 — печатаются только тип и место
            п.упавшие.append(k)
            print(f"часть {k} из {len(п.части)}: сбой {type(e).__name__} ({код_ошибки(e)}) · {место(e)}")
            continue
        п.карточки += loc["карточки"]
        п.ист_с.update(loc["ист_с"])
        п.ист_к.update(loc["ист_к"])
        _разложить_дела(п, loc["дела"], {str(c["id"]) for c in loc["карточки"]})
        п.прочитанные.update(часть)
    вне = sum(len(п.части[k - 1]) for k in п.упавшие)
    п.покрытие_частей = (1 - вне / len(п.сделки)) if п.упавшие and п.сделки else None
    print(f"частей прочитано {len(п.части) - len(п.упавшие)} из {len(п.части)}; сделок вне выводов {вне}"
          + (f"; покрытие частей {пр(п.покрытие_частей)} — ворота вердиктов" if п.покрытие_частей is not None
             else ""))
    if п.дела_способ == "окно":
        шаг_дела_окном(п)


def _разложить_дела(п: Прогон, дела: list[dict], свои: set[str]) -> None:
    for a in дела:
        o = str(a.get("OWNER_ID") or "")
        if str(a.get("OWNER_TYPE_ID")) != str(SPA) or o not in свои:
            continue
        п.дела[o].append({"t": _вр(a.get("CREATED")), "type": str(a.get("TYPE_ID") or ""),
                          "prov": str(a.get("PROVIDER_ID") or ""), "dir": str(a.get("DIRECTION") or "")})


def шаг_дела_окном(п: Прогон) -> None:
    """Запасной путь: все дела СП-166 за окно по ключу (как main._mail_activities), свои — отбором."""
    since = п.с_карточек.isoformat() + "T00:00:00"
    фильтр = {"OWNER_TYPE_ID": SPA, ">=CREATED": since}
    try:
        with п.учёт.блок("дела окном"):
            всего = _total(п.client, "crm.activity.list", {"filter": фильтр, "select": ["ID"]})
            оценка = п50(всего) + 1
            if not п.учёт.можно("дела карточек окном", оценка):
                п.дела_способ = "не прочитаны: не помещаются в предел"
                return
            дела = _по_ключу(п.client, "crm.activity.list", фильтр, ДЕЛО_SELECT, страниц=оценка + 5)
    except Exception as e:  # noqa: BLE001
        п.дела_способ = f"не прочитаны: {код_ошибки(e)}"
        print(f"дела карточек окном: {п.дела_способ}")
        return
    свои = {str(c["id"]) for c in п.карточки if п.карточка_окна(c)}
    _разложить_дела(п, дела, свои)
    print(f"дела карточек окном: дел СП-{SPA} за {ОКНО_КАРТОЧЕК_ДН} дн. {len(дела)} (по счёту портала {всего}), "
          f"у карточек пресейла {sum(len(v) for v in п.дела.values())}")


def шаг_звонки(п: Прогон) -> None:
    """Звонки (TYPE_ID 2) на всех карточках СП-166 за окно — одним счётом портала."""
    if not п.учёт.можно("звонки СП-166", 1):
        return
    try:
        with п.учёт.блок("звонки"):
            п.звонков_всего = _total(п.client, "crm.activity.list", {
                "filter": {"OWNER_TYPE_ID": SPA, "TYPE_ID": int(ЗВОНОК_ТИП),
                           ">=CREATED": п.с_карточек.isoformat() + "T00:00:00"}, "select": ["ID"]})
    except Exception as e:  # noqa: BLE001
        print(f"звонки СП-{SPA}: счёт не прочитан ({код_ошибки(e)})")


def шаг_задачи(п: Прогон) -> None:
    """Право вебхука на tasks.task.list: один запрос с пустым отбором."""
    if not п.учёт.можно("задачи", 1):
        return
    try:
        with п.учёт.блок("задачи"):
            п.client.call("tasks.task.list", {"filter": {"ID": 0}, "select": ["ID"]})
        п.задачи_право = "есть"
    except bc.BitrixLimitError:
        п.задачи_право = "не проверено (лимит портала)"
    except Exception as e:  # noqa: BLE001
        п.задачи_право = код_ошибки(e)


# ── отчёт ───────────────────────────────────────────────────────────────────────────────────

def _группа_стадии(п: Прогон, d: dict) -> str:
    if п.сем(d) == "F":
        return "отказ"
    k = п.шаг_сделки(d)
    return f"[{k}]" if k else "вне рег."


def _порядок_групп() -> list[str]:
    return [f"[{k}]" for k in range(1, ШАГ_УСПЕХ + 1)] + ["вне рег.", "отказ"]


def отчёт_поля(п: Прогон) -> None:
    сделки = list(п.сделки.values())
    n = len(сделки)
    print(f"\n1. ПОЛЯ СДЕЛКИ (по подписи; сделок пресейла {n}; по стадиям — [шаг] текущей стадии)")
    for ключ, подпись, _rx, _коды, процесс in ПОЛЯ:
        найдено = п.поля.get(ключ) or []
        if not найдено:
            print(f"  «{подпись}»: поле не найдено")
            п.вердикты.append(("1", подпись, "скрыть"))
            print(f"    вердикт «{подпись}»: скрыть · поля нет")
            continue
        for f in найдено:
            code, тип = f["code"], f["type"]
            print(f"  «{подпись}» → {code} «{подпись_поля(f['label'])}» · тип {тип} · "
                  f"{'множественное' if f['multiple'] else 'одно значение'}"
                  + (f" · вариантов {len(f['items'])}" if f["items"] else ""))
            подп = {str(i.get("ID")): str(i.get("VALUE") or "") for i in f["items"]}
            if тип == "boolean":
                есть = [d for d in сделки if да(d.get(code), тип, подп)]
                что = "«да»"
            else:
                есть = [d for d in сделки if _есть(d.get(code))]
                что = "заполнено"
            по = collections.Counter(_группа_стадии(п, d) for d in сделки)
            по_есть = collections.Counter(_группа_стадии(п, d) for d in есть)
            print(f"    {что}: {доля(len(есть), n)}; по стадиям: "
                  + (" · ".join(f"{g} {по_есть.get(g, 0)}/{по[g]}" for g in _порядок_групп() if по.get(g)) or "—"))
            if процесс and (f["items"] or тип == "boolean"):
                метки = метки_вариантов(f["items"])
                знач = collections.Counter()
                for d in сделки:
                    vs = значения(d.get(code))
                    if тип == "boolean":
                        знач["да" if да(d.get(code), тип, подп) else "нет / пусто"] += 1
                        continue
                    if not vs:
                        знач["пусто"] += 1
                    for x in vs:
                        знач[метки.get(x, "нет в справочнике")] += 1
                свои = list(dict.fromkeys(метки.values()))
                служебные = ["да", "нет / пусто", "нет в справочнике", "пусто"]
                части = [f"«{k}» {знач[k]}" for k in свои if знач.get(k)] + \
                        [f"{k} {знач[k]}" for k in служебные if знач.get(k)]
                хвост = len(части) - ВАРИАНТОВ_В_СТРОКЕ
                print("    значения: " + (" · ".join(части[:ВАРИАНТОВ_В_СТРОКЕ]) or "—")
                      + (f" · … ещё {хвост}" if хвост > 0 else ""))
            п.вердикт("1", f"{подпись} ({code})", n, 1.0 if тип == "boolean" else (len(есть) / n if n else None))
    ft = п.поле("dealType")
    if ft:
        k = sum(1 for d in сделки if расценка(d.get(ft["code"]), п.подписи(ft)))
        print(f"  группы для разрезов: «Расценка» по {ft['code']} — {k}; " + счёт(
            collections.Counter(п.группа(d) for d in сделки), ГРУППЫ))
    else:
        print("  группы для разрезов: поля «Deal type» нет — «Расценка» не выделяется; "
              + счёт(collections.Counter(п.группа(d) for d in сделки), ГРУППЫ))


def отчёт_стадии(п: Прогон) -> None:
    сделки = list(п.сделки.values())
    print("\n2. СТАДИИ ВОРОНКИ «ПРЕСЕЙЛ» (порядок SORT; шаг — инструкция сорсера v1.4)")
    сейчас = collections.Counter(str(d.get("STAGE_ID") or "") for d in сделки)
    for sid, m in sorted(п.стадии.items(), key=lambda kv: kv[1]["sort"]):
        k = m["шаг"]
        if m["sem"] == "F":
            роль = "отказ"
        elif k == ШАГ_УСПЕХ:
            роль = f"шаг {k} · успех"
        elif k:
            r = ШАГ[k]
            роль = f"шаг {k} · закрывает {r[3]} · " + (f"норма {r[4]} р.д. ({r[5]})" if r[4] else "без нормы")
        else:
            роль = "вне регламента v1.4"
        print(f"  [{m['sort']}] {sid} «{имя_стадии(m['name'])}» · {m['sem']} · {роль} · сейчас {сейчас.get(sid, 0)}")
    чужие = sum(v for s, v in сейчас.items() if s not in п.стадии)
    нет = [k for k in range(1, ШАГ_УСПЕХ + 1) if not any(m["шаг"] == k for m in п.стадии.values())]
    много = collections.Counter(m["шаг"] for m in п.стадии.values() if m["шаг"])
    print(f"  шаги регламента без стадии в воронке: "
          + (", ".join(f"{k} «{ШАГ[k][1]}»" for k in нет) or "нет"))
    print(f"  шаг узнан у нескольких стадий: " + (", ".join(f"шаг {k} — {v}" for k, v in sorted(много.items())
                                                         if v > 1) or "нет"))
    print(f"  сделок сейчас: {счёт(collections.Counter(п.сем(d) for d in сделки), ('P', 'S', 'F'))}; "
          f"в стадии вне справочника {чужие}")
    узнаны = sum(1 for d in сделки if п.сем(d) == "F" or п.шаг_сделки(d))
    print(f"  стадия узнана (шаг или отказ): {доля(узнаны, len(сделки))}")
    п.вердикт("2", "текущий шаг сделки", len(сделки), узнаны / len(сделки) if сделки else None)


def _окно(п: Прогон) -> list[dict]:
    return [d for did, d in п.сделки.items() if did in п.прочитанные and п.в_окне(did)]


def отчёт_история(п: Прогон) -> dict:
    окно = _окно(п)
    с_ист = [d for d in окно if п.ист_с.get(str(d["ID"]))]
    cov = len(с_ист) / len(окно) if окно else None
    шаги = п.шаги()
    print(f"\n3. ИСТОРИЯ СТАДИЙ (сделки, созданные с {п.с_окна:%d.%m.%Y}; входы по STAGE_ID стадий пресейла; "
          "р.д. — календарь РФ)")
    print(f"  сделок в окне {len(окно)}, с историей {доля(len(с_ист), len(окно))}, переходов "
          f"{sum(len(п.ист_с.get(str(d['ID'])) or []) for d in с_ист)}")
    ft = п.поле("dealType")
    все: list[dict] = []
    for d in с_ист:
        for x in пребывания(п.ист_с[str(d["ID"])], шаги, str(d.get("STAGE_ID") or ""), п.сегодня):
            x["did"], x["расц"] = str(d["ID"]), п.группа(d) == "Расценка"
            все.append(x)
    неизв = sum(1 for x in все if x["выход"] is None and not x["идёт"] and x["шаг"] not in (None, ШАГ_УСПЕХ))
    print(f"  пребываний {len(все)}; выход неизвестен (сделка ушла из воронки) {неизв}")
    for k in range(1, ШАГ_УСПЕХ):
        r = ШАГ[k]
        рр = [x for x in все if x["шаг"] == k]
        норма = r[4]
        зав = [x for x in рр if x["выход"] is not None and x["рд"] is not None]
        идут = [x for x in рр if x["идёт"]]
        без_кал = sum(1 for x in рр if (x["выход"] is not None or x["идёт"]) and x["рд"] is None)
        wd = [x["рд"] for x in зав]
        в_норме = (sum(1 for w in wd if w <= норма) / len(wd)) if (норма and wd) else None
        сверх = sum(1 for x in идут if норма and x["рд"] is not None and x["рд"] > норма)
        print(f"  шаг {k} «{r[1]}» ({r[3]}; " + (f"норма {норма} р.д." if норма else "без нормы") + "): "
              f"входов {len(рр)} (сделок {len({x['did'] for x in рр})}), завершённых {len(зав)}, сейчас идёт "
              f"{len(идут)}" + (f" (сверх нормы {сверх})" if норма else "") + f"; р.д.: {дн(wd)}"
              + (f"; в норме {пр(в_норме)}" if норма else "") + (f"; без календаря {без_кал}" if без_кал else ""))
        if ft:
            части = []
            for имя, флаг in (("Расценка", True), ("остальные", False)):
                ww = [x["рд"] for x in зав if x["расц"] is флаг]
                нн = (sum(1 for w in ww if w <= норма) / len(ww)) if (норма and ww) else None
                части.append(f"{имя}: {дн(ww)}" + (f", в норме {пр(нн)}" if норма else ""))
            print("    по типу: " + "; ".join(части))
        п.вердикт("3", f"шаг {k}: р.д. в стадии", len(зав), cov)
    # пропуск 3/5/6 у «Расценки» и у остальных
    print(f"  пропуск шагов {', '.join(map(str, ПРОПУСК_РАСЦЕНКИ))} (по истории, у дошедших до шага "
          f"{ШАГ_ЭКОНОМИКА}+):")
    for имя, флаг in (("Расценка", True), ("остальные", False)):
        дошли = []
        for d in с_ист:
            if (п.группа(d) == "Расценка") is not флаг:
                continue
            вх = первые_входы(п.ист_с[str(d["ID"])], шаги)
            if max(вх, default=0) >= ШАГ_ЭКОНОМИКА:
                дошли.append({k for k in ПРОПУСК_РАСЦЕНКИ if k in вх})
        все_три = sum(1 for s in дошли if not s)
        хоть_один = sum(1 for s in дошли if len(s) < len(ПРОПУСК_РАСЦЕНКИ))
        print(f"    {имя}: дошли {len(дошли)}; пропустили все три {доля(все_три, len(дошли))}; "
              f"пропустили хотя бы один {доля(хоть_один, len(дошли))}")
        if флаг and ft:
            п.вердикт("3", "«Расценка» пропускает 3/5/6", len(дошли), cov)
    if not ft:
        print("    поля «Deal type» нет — «Расценка» не выделена: все сделки в «остальных»")
    # возвраты
    пары = collections.Counter()
    сделок_с = 0
    for d in с_ист:
        вв = возвраты(п.ист_с[str(d["ID"])], шаги)
        пары.update(f"{a}→{b}" for a, b in вв)
        сделок_с += bool(вв)
    с_восьмым = sum(v for k, v in пары.items() if str(ШАГ_ДОРАБОТКА) in k.split("→"))
    print(f"  возвраты (шаг назад): {sum(пары.values())} у {доля(сделок_с, len(с_ист))} сделок; пары: {счёт(пары)}; "
          f"с участием шага {ШАГ_ДОРАБОТКА} «Доработка» {с_восьмым}")
    п.вердикт("3", "возвраты", len(с_ист), cov)
    return {"окно": окно, "с_ист": с_ист, "cov": cov}


def _входы(п: Прогон, d: dict) -> dict[int, dt.datetime]:
    return первые_входы(п.ист_с.get(str(d["ID"])) or [], п.шаги())


def _ткп_после(п: Прогон, d: dict, t: dt.datetime) -> dt.datetime | None:
    шаги = п.шаги()
    return min((tt for s, x in п.ист_с.get(str(d["ID"])) or []
                if (k := шаги.get(str(s))) and k >= ШАГ_ТКП and (tt := _вр(x)) and tt >= t), default=None)


def отчёт_ткп(п: Прогон, ист: dict) -> None:
    окно, с_ист, cov = ист["окно"], ист["с_ист"], ист["cov"]
    print(f"\n4. КП → ТКП (сделки окна; шаг {ШАГ_ТКП}+ — ТКП отправлено, инструкция v1.4)")
    # (а) «Экономика готова» → позже 9+
    вошли = [(d, _входы(п, d)[ШАГ_ЭКОНОМИКА]) for d in с_ист if ШАГ_ЭКОНОМИКА in _входы(п, d)]

    def строка(рр, имя):
        после = [(d, t, _ткп_после(п, d, t)) for d, t in рр]
        дошли = [(d, t, t9) for d, t, t9 in после if t9]
        зрелые = [(d, t, t9) for d, t, t9 in после if (рд(t, п.сегодня) or 0) >= ЗРЕЛОСТЬ_РД]
        на7 = sum(1 for d, _t, t9 in после if not t9 and п.сем(d) == "P" and п.шаг_сделки(d) == ШАГ_ЭКОНОМИКА)
        отказ = sum(1 for d, _t, t9 in после if not t9 and п.сем(d) == "F")
        print(f"    {имя}: вошли {len(рр)}; позже шаг {ШАГ_ТКП}+ {доля(len(дошли), len(рр))}; р.д. от {ШАГ_ЭКОНОМИКА} до "
              f"{ШАГ_ТКП}+: {дн([рд(t, t9) for _d, t, t9 in дошли])}; сейчас на шаге {ШАГ_ЭКОНОМИКА} {на7}, в отказе без "
              f"{ШАГ_ТКП}+ {отказ}, прочее {len(рр) - len(дошли) - на7 - отказ}; зрелые (вход ≥ {ЗРЕЛОСТЬ_РД} р.д. "
              f"назад): {доля(sum(1 for x in зрелые if x[2]), len(зрелые))}")
        return len(рр)
    print(f"  (а) вошли в «Экономика готова» (шаг {ШАГ_ЭКОНОМИКА}) → позже шаг {ШАГ_ТКП}+:")
    n = строка(вошли, "все")
    for g in ГРУППЫ:
        строка([(d, t) for d, t in вошли if п.группа(d) == g], g)
    п.вердикт("4", f"шаг {ШАГ_ЭКОНОМИКА} → ТКП отправлено", n, cov)
    без7 = sum(1 for d in с_ист if max(_входы(п, d), default=0) >= ШАГ_ТКП and ШАГ_ЭКОНОМИКА not in _входы(п, d))
    print(f"    шаг {ШАГ_ТКП}+ без входа в шаг {ШАГ_ЭКОНОМИКА}: {без7} сделок")
    fe = п.поле("econReady")
    if fe:
        подп = п.подписи(fe)
        вош_id = {str(d["ID"]) for d, _t in вошли}
        a = sum(1 for d, _t in вошли if да(d.get(fe["code"]), fe["type"], подп))
        прочие = [d for d in с_ист if str(d["ID"]) not in вош_id]
        b = sum(1 for d in прочие if да(d.get(fe["code"]), fe["type"], подп))
        print(f"    поле «Экономика готова» ({fe['code']}) = «Да»: у вошедших в шаг {ШАГ_ЭКОНОМИКА} {доля(a, len(вошли))}, "
              f"у не входивших {доля(b, len(прочие))}")
    else:
        print("    поля «Экономика готова» нет — вторая опора не сверяется")
    # (б) КП-файл под сделкой → 7 и 9+
    кп = collections.Counter(_uid(c.get("parentId2")) for c in п.карточки if _есть(c.get(OFFER_F)))
    с_кп = [d for d in окно if кп.get(str(d["ID"]))]
    print(f"  (б) сделки окна с ≥ 1 КП (файл в {OFFER_F} карточки СП-{SPA}): {доля(len(с_кп), len(окно))}")

    def строка_б(рр, имя):
        до7 = до9 = мимо = лишние = 0
        for d in рр:
            вх = _входы(п, d)
            m = max(вх, default=0)
            до7 += ШАГ_ЭКОНОМИКА in вх or m >= ШАГ_ЭКОНОМИКА
            ткп = m >= ШАГ_ТКП
            до9 += ткп
            стадия = п.стадии.get(str(d.get("STAGE_ID") or "")) or {}
            старый = deal_reached_tkp(str(d.get("STAGE_ID") or ""), п.сем(d), стадия.get("name"))
            мимо += ткп and not старый
            лишние += старый and not ткп
        print(f"    {имя}: {len(рр)}; дошли до шага {ШАГ_ЭКОНОМИКА}+ {доля(до7, len(рр))}, до {ШАГ_ТКП}+ "
              f"{доля(до9, len(рр))}; старый способ (stages.deal_reached_tkp) не видит {мимо} из {до9}, видит лишних "
              f"{лишние}")
    строка_б(с_кп, "все")
    for g in ГРУППЫ:
        строка_б([d for d in с_кп if п.группа(d) == g], g)
    без_кп7 = sum(1 for d in с_ист if not кп.get(str(d["ID"])) and max(_входы(п, d), default=0) >= ШАГ_ЭКОНОМИКА)
    print(f"    без КП-файла, но дошли до шага {ШАГ_ЭКОНОМИКА}+: {без_кп7} сделок")
    п.вердикт("4", "КП → ТКП по истории", len(с_кп), cov)


def отчёт_дожим(п: Прогон, ист: dict) -> None:
    к90 = [c for c in п.карточки if п.карточка_окна(c)]
    n = len(к90)
    print(f"\n5. ДОЖИМ ЗАПРОСОВ (карточки СП-{SPA} воронки {SPA_CAT} под сделками пресейла, созданные с "
          f"{п.с_карточек:%d.%m.%Y})")
    print(f"  карточек под сделками всех воронок СП-{SPA}: {len(п.карточки)} ("
          + счёт(collections.Counter(f"#{c.get('categoryId')}" for c in п.карточки)) + f"); в разборе {n}")
    print(f"  сейчас по корзинам: " + счёт(collections.Counter(classify_stage(str(c.get('stageId') or '')) for c in к90)))
    hist = п.ист166 == "да"
    print(f"  способ сроков: " + (f"история стадий СП-{SPA} (crm.stagehistory.list, entityTypeId {SPA})" if hist
                                 else f"movedTime — история СП-{SPA} не прочитана ({п.ист166 or 'не пробовали'})"))
    print(f"  дела карточек: " + {"массив": "OWNER_ID массивом", "окно": "одним обходом окна"}.get(
        п.дела_способ or "", п.дела_способ or "не читались"))
    виды = collections.Counter()
    for c in к90:
        for a in п.дела.get(str(c["id"]), []):
            prov = a["prov"] if КОД_RX.match(a["prov"]) else "другой"
            виды[f"TYPE {a['type'] or '—'}/{prov}/{'исх' if a['dir'] == '2' else 'вх' if a['dir'] == '1' else '—'}"] += 1
    print(f"  дела по виду: {счёт(виды)}")
    if п.звонков_всего is not None:
        print(f"  звонков (TYPE_ID {ЗВОНОК_ТИП}) на всех карточках СП-{SPA} за {ОКНО_КАРТОЧЕК_ДН} дн. (счёт портала): "
              f"{п.звонков_всего}")
    дела_есть = п.дела_способ in ("массив", "окно")
    строки = []
    for c in к90:
        дд = п.дела.get(str(c["id"]), [])
        письма = [a["t"] for a in дд if a["prov"] == ПИСЬМО_ПРОВАЙДЕР and a["dir"] == ИСХОДЯЩЕЕ and a["t"]]
        звонки = [a["t"] for a in дд if (a["type"] == ЗВОНОК_ТИП or a["prov"].startswith("VOXIMPLANT")) and a["t"]]
        r = разбор_карточки(письма, звонки, п.ист_к.get(str(c["id"])) if hist else None, п.сегодня)
        r["c"], r["корз"], r["звонков"] = c, classify_stage(str(c.get("stageId") or "")), len(звонки)
        строки.append(r)
    ушли = [r for r in строки if r["корз"] != "new"]
    с_письмом = [r for r in ушли if r["first"]]
    cov_письма = (len(с_письмом) / len(ушли)) if ушли else None
    print(f"  ушли из «Новый»: {len(ушли)}; с исходящим письмом в CRM {доля(len(с_письмом), len(ушли))}; со звонком "
          f"{sum(1 for r in строки if r['звонков'])}")
    if not дела_есть:
        print("  дела карточек не прочитаны — письма и звонки не меряются")
    п.вердикт("5", "письмо в CRM у отправленных", len(ушли), None if дела_есть else 0.0)
    # письмо-напоминание к 2-му р.д.
    м2 = [r for r in строки if r["first"] and (r["age"] or 0) >= ДОЖИМ_ПИСЬМО_РД
          and без_ответа_к(r, ДОЖИМ_ПИСЬМО_РД, r["корз"])]
    корз2 = collections.Counter("второго нет" if r["second"] is None else "тот же день" if r["second"] == 0
                                else f"1–{ДОЖИМ_ПИСЬМО_РД} р.д." if r["second"] <= ДОЖИМ_ПИСЬМО_РД
                                else "3–5 р.д." if r["second"] <= 5 else "> 5 р.д." for r in м2)
    вовремя = sum(1 for r in м2 if r["second"] is not None and 1 <= r["second"] <= ДОЖИМ_ПИСЬМО_РД)
    print(f"  письмо-напоминание (без ответа к {ДОЖИМ_ПИСЬМО_РД}-му р.д. от первого письма): {len(м2)} карточек; "
          f"второе письмо к {ДОЖИМ_ПИСЬМО_РД}-му р.д. {доля(вовремя, len(м2))}; второе письмо через: "
          f"{счёт(корз2, ('тот же день', f'1–{ДОЖИМ_ПИСЬМО_РД} р.д.', '3–5 р.д.', '> 5 р.д.', 'второго нет'))}"
          + ("" if hist else " (без истории: «без ответа» — сейчас в «отправлен» / «нет ответа»)"))
    print(f"    р.д. от первого письма до второго (все с двумя письмами): "
          f"{дн([r['second'] for r in строки if r['second'] is not None])}")
    п.вердикт("5", f"напоминание к {ДОЖИМ_ПИСЬМО_РД}-му р.д.", len(м2), cov_письма)
    м3 = [r for r in строки if r["first"] and (r["age"] or 0) >= ДОЖИМ_ЗВОНОК_РД
          and без_ответа_к(r, ДОЖИМ_ЗВОНОК_РД, r["корз"])]
    звонок3 = sum(1 for r in м3 if r["call"] is not None and r["call"] <= ДОЖИМ_ЗВОНОК_РД)
    print(f"  звонок (без ответа к {ДОЖИМ_ЗВОНОК_РД}-му р.д.): {len(м3)} карточек; звонок к {ДОЖИМ_ЗВОНОК_РД}-му р.д. "
          f"{доля(звонок3, len(м3))}; звонок когда-либо {доля(sum(1 for r in м3 if r['звонков']), len(м3))}")
    п.вердикт("5", f"звонок к {ДОЖИМ_ЗВОНОК_РД}-му р.д.", len(м3), cov_письма)
    # до ответа и до КП
    if hist:
        cov_ист = (sum(1 for c in к90 if п.ист_к.get(str(c["id"]))) / n) if n else None
        отв = [r["reply"] for r in строки if r["reply"] is not None]
        print(f"  р.д. от первого письма до ответа (переписка, Price at Work, Selected, не выбран, отказ): {дн(отв)}; "
              f"ответ раньше первого письма в CRM {sum(1 for r in строки if r['before'])}")
        print(f"  р.д. от первого письма до КП (Price at Work, Selected, не выбран): "
              f"{дн([r['kp'] for r in строки if r['kp'] is not None])}")
        # второй способ: последний вход по истории ↔ movedTime (вход в текущую стадию)
        пары = []
        for c in к90:
            tr = sorted(((str(s), _вр(t)) for s, t in п.ист_к.get(str(c["id"])) or [] if _вр(t)),
                        key=lambda x: x[1])
            mv = _дн(c.get("movedTime"))
            if tr and mv:
                пары.append(tr[-1][0] == str(c.get("stageId")) and tr[-1][1].astimezone(MSK).date() == mv)
        print(f"  история ↔ movedTime (последний вход = текущая стадия в тот же день): "
              f"{доля(sum(пары), len(пары))}")
    else:
        cov_ист = 0.0
        отв = [рд(r["first"], _вр(r["c"].get("movedTime"))) for r in строки
               if r["first"] and r["корз"] in ОТВЕТ]
        отв = [x for x in отв if x is not None and x >= 0]
        print(f"  р.д. от первого письма до входа в текущую стадию ответа (movedTime; позже первого ответа, если "
              f"карточка шла дальше): {дн(отв)}")
    п.вердикт("5", "до ответа поставщика", len(отв), cov_ист)
    # «Request Sent» сейчас
    отпр = [r for r in строки if r["корз"] == "sent"]
    от_входа = [рд(_вр(r["c"].get("movedTime")), п.сегодня) for r in отпр]
    от_письма = [r["age"] for r in отпр if r["first"]]
    print(f"  сейчас «Request Sent»: {len(отпр)}; старше {НЕТ_ОТВЕТА_РД} р.д. от входа в стадию (movedTime) "
          f"{доля(sum(1 for x in от_входа if x is not None and x > НЕТ_ОТВЕТА_РД), len(отпр))}, от первого письма "
          f"{доля(sum(1 for x in от_письма if x is not None and x > НЕТ_ОТВЕТА_РД), len(от_письма))}")
    п.вердикт("5", f"«Request Sent» > {НЕТ_ОТВЕТА_РД} р.д.", len(отпр), None)
    # «отправлен» → «Ответ не получен (в срок)»
    сроки = []
    for r in строки:
        c = r["c"]
        if hist:
            tr = sorted(((classify_stage(str(s)), _вр(t)) for s, t in п.ист_к.get(str(c["id"])) or [] if _вр(t)),
                        key=lambda x: x[1])
            t_na = next((t for b, t in tr if b == "no_answer"), None)
            t_s = next((t for b, t in tr if b == "sent"), None)
        else:
            t_na = _вр(c.get("movedTime")) if r["корз"] == "no_answer" else None
            t_s = None
        начало = min((x for x in (t_s, r["first"]) if x), default=None)
        if t_na and начало:
            сроки.append(рд(начало, t_na))
    сроки = [x for x in сроки if x is not None]
    кс = collections.Counter(f"< {НЕТ_ОТВЕТА_РД}" if x < НЕТ_ОТВЕТА_РД else f"{НЕТ_ОТВЕТА_РД}–{НЕТ_ОТВЕТА_РД + 1}"
                             if x <= НЕТ_ОТВЕТА_РД + 1 else f"> {НЕТ_ОТВЕТА_РД + 1}" for x in сроки)
    print(f"  «отправлен» → «Ответ не получен (в срок)», р.д. от отправки (вход в «отправлен» или первое письмо, "
          f"что раньше): {дн(сроки)}; {счёт(кс, (f'< {НЕТ_ОТВЕТА_РД}', f'{НЕТ_ОТВЕТА_РД}–{НЕТ_ОТВЕТА_РД + 1}', f'> {НЕТ_ОТВЕТА_РД + 1}'))}")
    п.вердикт("5", f"«отправлен» → «нет ответа» на {НЕТ_ОТВЕТА_РД}-й р.д.", len(сроки), cov_ист)
    # шаг 4 → первое письмо по сделке (норма 1 р.д.)
    первое: dict[str, dt.datetime] = {}
    for r in строки:
        p = _uid(r["c"].get("parentId2"))
        if r["first"] and (p not in первое or r["first"] < первое[p]):
            первое[p] = r["first"]
    вош4 = [(d, _входы(п, d)[ШАГ_ПОИСК]) for d in ист["с_ист"] if ШАГ_ПОИСК in _входы(п, d)]
    вош4 = [(d, t) for d, t in вош4 if t.astimezone(MSK).date() >= п.с_карточек]
    сроки4 = [рд(t, первое.get(str(d["ID"]))) for d, t in вош4]
    есть4 = [x for x in сроки4 if x is not None]
    print(f"  шаг {ШАГ_ПОИСК} → первое письмо поставщику по сделке (норма {ОТПРАВКА_РД} р.д.; вход в шаг с "
          f"{п.с_карточек:%d.%m.%Y}): сделок {len(вош4)}, с письмом {len(есть4)}; р.д.: "
          f"{дн([x for x in есть4 if x >= 0])}; письмо раньше входа в шаг {sum(1 for x in есть4 if x < 0)}; "
          f"в норме {доля(sum(1 for x in есть4 if x <= ОТПРАВКА_РД), len(есть4))}")
    п.вердикт("5", f"шаг {ШАГ_ПОИСК} → первое письмо", len(есть4), (len(есть4) / len(вош4)) if вош4 else None)
    # КП на сделку против нормы
    кп = collections.Counter(_uid(c.get("parentId2")) for c in п.карточки if _есть(c.get(OFFER_F)))
    print(f"  КП на сделку (файлы {OFFER_F}) у сделок окна, закончивших сбор КП (шаг {ШАГ_АНАЛИЗ}+, у «Расценки» — "
          f"{ШАГ_ЭКОНОМИКА}+); норма: " + ", ".join(f"{g} ≥ {НОРМА_КП[g]}" for g in ГРУППЫ) + ":")
    всего_n = 0
    for g in ГРУППЫ:
        порог_шага = ШАГ_ЭКОНОМИКА if g == "Расценка" else ШАГ_АНАЛИЗ
        рр = [d for d in ист["с_ист"] if п.группа(d) == g and max(_входы(п, d), default=0) >= порог_шага]
        чис = [кп.get(str(d["ID"]), 0) for d in рр]
        всего_n += len(рр)
        кк = collections.Counter(корзина_кп(x) for x in чис)
        print(f"    {g}: сделок {len(рр)}; по числу КП: "
              + (" · ".join(f"{k} КП — {кк[k]}" for k in ("0", "1", "2", "3–5", "6+") if кк.get(k)) or "—")
              + f"; в норме {доля(sum(1 for x in чис if x >= НОРМА_КП[g]), len(чис))}; медиана {чс(мед(чис))}")
    п.вердикт("5", "КП на сделку против нормы", всего_n, None)


def отчёт_срок(п: Прогон, ист: dict) -> None:
    f = п.поле("deadline")
    print("\n6. СРОК ЗАКАЗЧИКА («End date and time for receiving offer»)")
    if not f:
        print("  поле не найдено")
        п.вердикты.append(("6", "срок заказчика", "скрыть"))
        print("    вердикт «срок заказчика»: скрыть · поля нет")
        return
    code = f["code"]
    сделки = list(п.сделки.values())
    окно = ист["окно"]
    print(f"  поле {code} · тип {f['type']}; заполнено у {доля(sum(1 for d in сделки if _есть(d.get(code))), len(сделки))} "
          f"сделок пресейла, в окне {доля(sum(1 for d in окно if _есть(d.get(code))), len(окно))}; не дата "
          f"{sum(1 for d in сделки if _есть(d.get(code)) and _вр(d.get(code)) is None)}")
    вошли = [(d, _входы(п, d)[ШАГ_ЭКОНОМИКА]) for d in ист["с_ист"] if ШАГ_ЭКОНОМИКА in _входы(п, d)]
    со_сроком = [(d, t) for d, t in вошли if _вр(d.get(code))]
    до = [до_срока(t, d.get(code)) for d, t in со_сроком]
    запас = [рд(t, _вр(d.get(code))) for d, t in со_сроком]
    print(f"  вошли в шаг {ШАГ_ЭКОНОМИКА}: {len(вошли)}, со сроком {len(со_сроком)}; раньше срока "
          f"{доля(sum(1 for x in до if x), len(до))}, позже {sum(1 for x in до if x is False)}; запас р.д. "
          f"(срок − вход): {дн(запас)}")
    по_гр = collections.defaultdict(list)
    for (d, _t), x in zip(со_сроком, до):
        по_гр[п.группа(d)].append(x)
    print("    по группам: " + "; ".join(f"{g} {доля(sum(1 for x in по_гр[g] if x), len(по_гр[g]))}" for g in ГРУППЫ))
    открытые = [d for d in окно if п.сем(d) == "P" and _вр(d.get(code)) and ШАГ_ЭКОНОМИКА not in _входы(п, d)
                and max(_входы(п, d), default=0) < ШАГ_ЭКОНОМИКА]
    сорвано = sum(1 for d in открытые if _дн(d.get(code)) < п.сегодня)
    print(f"  открытые сделки окна до шага {ШАГ_ЭКОНОМИКА} со сроком: {len(открытые)}; срок уже прошёл {сорвано}")
    п.вердикт("6", f"шаг {ШАГ_ЭКОНОМИКА} раньше срока заказчика", len(со_сроком),
              (len(со_сроком) / len(вошли)) if вошли else None)


def отчёт_задачи(п: Прогон) -> None:
    print("\n7. ЗАДАЧИ (tasks.task.list)")
    if п.задачи_право is None:
        print("  право не проверялось")
        return
    print(f"  право вебхука: {п.задачи_право if п.задачи_право == 'есть' else 'нет (' + п.задачи_право + ')'}")
    v = "показать" if п.задачи_право == "есть" else "скрыть"
    п.вердикты.append(("7", "задачи", v))
    print(f"    вердикт «задачи»: {v}")


def отчёт(п: Прогон) -> None:
    print(f"\nВ ВЫВОДАХ: сделок {len(п.прочитанные)} из {len(п.сделки)} (в окне {ОКНО_СДЕЛОК_ДН} дн. "
          f"{len(_окно(п))}), карточек СП-{SPA} {len(п.карточки)} (воронки {SPA_CAT} за {ОКНО_КАРТОЧЕК_ДН} дн. "
          f"{sum(1 for c in п.карточки if п.карточка_окна(c))}); ворота: n ≥ {ВОРОТА['n_show']} и покрытие ≥ "
          f"{пр(ВОРОТА['cover_show'])} — показать, n ≥ {ВОРОТА['n_prelim']} — предварительно, иначе скрыть")
    отчёт_поля(п)
    отчёт_стадии(п)
    ист = отчёт_история(п)
    отчёт_ткп(п, ист)
    отчёт_дожим(п, ист)
    отчёт_срок(п, ист)
    отчёт_задачи(п)
    print("\nСВОДКА ВЕРДИКТОВ: " + счёт(collections.Counter(v for _r, _и, v in п.вердикты),
                                        ("показать", "предварительно", "скрыть")))
    for раздел in sorted({r for r, _и, _v in п.вердикты}, key=int):
        print(f"  {раздел}. " + " · ".join(f"{и} — {v}" for r, и, v in п.вердикты if r == раздел))


def main(client=None, сегодня: dt.date | None = None) -> int:
    п = Прогон(client, сегодня or period_mod.now(MSK).date())
    if п.client is None:
        п.client = bc.BitrixClient(config.Settings.load().bitrix_webhook_url)
    if not шаг_ожидание(п) or not шаг_основа(п):
        print(bc.сводка_нагрузки())
        return 1
    шаг_части(п)
    шаг_звонки(п)
    шаг_задачи(п)
    отчёт(п)
    print("\nНАГРУЗКА ПО БЛОКАМ: " + (" · ".join(f"{k} {v}" for k, v in п.учёт.по_блокам.items()) or "—")
          + f" · всего {п.учёт.сейчас()} · смета была {п.смета}")
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
