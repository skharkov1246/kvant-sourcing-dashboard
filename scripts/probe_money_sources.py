#!/usr/bin/env python3
"""Зонд v59 «Источники денег реализации»: откуда может прийти каждая сумма вкладки
«Реализация» и насколько источники сходятся между собой.

ПОВОД (25.10.2026). Владелец видит у сделки около €107K продажи при настоящих
около €30K. Зонд v55 показал: курс AED в справочнике Битрикса есть, сделок
реализации в AED нет — ошибка сидит в самой «Сумме» (OPPORTUNITY). Её видно только
сверкой с независимой опорой: спецификацией с заказчиком, бюджетом, товарными
строками, счетами. Зонд меряет все опоры сразу, чтобы каскад вкладки (проект v59,
раздел 2) строился на замере, а не на догадке.

РЕШЕНИЯ ВЛАДЕЛЬЦА 25.10.2026, по которым считает зонд:
  • все суммы — в евро по курсу ЦБ РФ на дату ИТОГОВОГО ТКП: поле
    UF_CRM_1720337258485 «ТКП согласован» (96 %); запасные даты по порядку —
    последний вход в стадию предложения до выигрыша (и у близнеца в пресейле),
    первый вход в кат. 0, DATE_CREATE. Дата — по Москве. Выигрыш — первый вход в кат. 0
    не раньше первого КП (сделку, заведённую в кат. 0 и выигранную обратно, первый вход
    в день создания не обманывает). Ряды ЦБ — с cbr.ru; не ответил — зеркало
    cbr-xml-daily.ru запросом на каждую нужную дату (A12, предел ЗЕРКАЛО_ЗАПРОСОВ);
  • файл экономики зонд НЕ скачивает: предел 30 файлов в сутки ведёт база читателя,
    читатель идёт от новых сделок к старым. Опоры «файл» в v59 нет — она в v59b.

ИСТОЧНИКИ (коды — в журнале):
  продажа: SB «Сумма» × курс Битрикса (то, что на вкладке) · SC «Сумма» × ЦБ на дату
  ТКП · SC0 — × ЦБ сегодня · SR товарные строки сделки (без НДС / с НДС) · SBud бюджет
  из data/budget_snapshot.json (привязка — contracts._load_budget_plans) · SSpec
  спецификация со стороны заказчика (СП-1174 и СП-132) · SInv счета клиенту (СП-31);
  закупка: BO живые заказы СП-172 × ЦБ на дату заказа (BO_kp — на дату ТКП, BO_bx — по
  Битриксу) · BOR товарные строки заказов · BCost «Cost (rub)» · BOffer «Offer sum» ·
  BBud бюджет (закупка в книге со знаком минус — по модулю, как шаблон) · BPaid СП-137
  «Счета на оплату»: «₽ по ЦБ» ÷ EUR ЦБ на дату заведения (так подписано поле), оплаченные
  и в работе — к заказам целиком; BPaid_done — только оплаченные, к заказам × «Оплачено, %».
  Бюджет к сделке — по номеру в названии сделки, иначе первого живого заказа (как вкладка).

СРАВНЕНИЯ. Отношение r = A/B; корзины — по модулю логарифма (≤5 / 5–10 / 10–20 /
> 20 %), ноль и минус — отдельно. Дальние (> 20 %) разбираются объяснителями
(допуски — таблицей, правило 4): НДС 1,20/1,22 по дате отгрузки; курс — только
валют самой сделки (её валюта и валюты её заказов), окном курса между нижней и
верхней датой ТКП; курс × НДС; ×1000; часть 0,1–0,8; больше 1,25–10. Сколько
«объясняется» случайно, показывает фон: те же пары с перемешанной опорой.

ВЫБОРКА: сделки кат. 0 кроме проигранных. U1 — в работе (не «Оплата получена»),
U2 — «Оплата получена», U3 — успех за последние 365 дней по входу в стадию успеха
(запасное — MOVED_TIME; CLOSEDATE — плановая дата, только сверка). Старше — счётчик.
Basra (воронка 16 и её компании) — отдельный сегмент, вне сравнений; тестовые
воронки 22, 26, 28 — вне всего. Шаги по сделкам — PROBE_PARTS смежными частями
(по умолчанию 10); упавшая часть даёт строку «частей прочитано k из n», выводы —
только по прочитанным.

ЖУРНАЛ ПУБЛИЧНЫЙ (правило 17, распоряжение 30.09): счёт, доли, корзины, коды валют
(только трёхбуквенные — текст money-поля идёт счётчиком), публичные курсы ЦБ и ЕЦБ,
номера воронок (не названия) и коды полей. Ни номеров сделок, компаний и
заказов, ни названий, ни сумм, ни ссылок. Падение — только тип ошибки и место в коде.
Распределение закупки к продаже — грубыми корзинами, как v55 (это наценка).

НАГРУЗКА: ≈ 350–450 запросов при 1/с (probe.yml), предел 550: необязательные блоки
пропускаются вслух, если смета не помещается. Последняя строка — сводка_нагрузки().
"""
from __future__ import annotations

import collections
import contextlib
import datetime as dt
import math
import os
import random
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bitrix_client as bc  # noqa: E402
import contracts as ct  # noqa: E402
import probe_conversion as pc  # noqa: E402
import probe_money as pm  # noqa: E402
from base import fx  # noqa: E402
from library import company_names as cn  # noqa: E402

# ── поля ────────────────────────────────────────────────────────────────────
TKP_FINAL = "UF_CRM_1720337258485"       # «ТКП согласован (конкурс выигран)» — дата итогового ТКП
KP_STAGE_DATE = "UF_CRM_DATE_PEREHODA_V_STADIU"   # переход в «Supplier's Offer Received | Preparing Quotation»
PAID_DATE = "UF_CRM_1717489142010"       # «Счёт оплачен (дата)»
OFFER_FILE = "UF_CRM_1585568303498"      # «Offer from us» — файл КП клиенту
ECON_FILE_BX = "UF_CRM_1699270486"       # «Economics of the project» — файл в Битриксе
ORDER_DEAL_ID = "ufCrm20_1723628543"     # «ID сделки» в заказе
SPEC132_SUM = "ufCrm8_1646387395"        # «Summ of Specification» (без валюты)
SPEC132_TYPE = "ufCrm8_1646387112"       # «Contract type»
INV_RUB = "ufCrm24_1712230757"           # «Сумма в рублях по курсу ЦБ РФ на дату заведения процесса»
INV_AMOUNT = "ufCrm24_1712231572"        # «Сумма оплаты» — число без валюты
INV_DATE = "ufCrm24_1713279621934"       # «Дата счета»
INV_PAYER = "ufCrm24_1712230322"         # «Юр. лицо, с которого платим»
INV_PAYEE = "ufCrm24_1712230372"         # «Юридическое наименование контрагента, кому платим»
INV_WHAT = "ufCrm24_1712230399"          # «За что платим»
INV_TYPE = "ufCrm24_1712230412"          # «Тип оплаты»
INV_DEALS = "ufCrm24_1713443158"         # «Сделки (если счёт относится к нескольким сделкам)»
INV_ORDERS = "ufCrm24_1733863187"        # «Заказы поставщикам (множ)»
SPEC_ENTITIES = (1174, 132)              # 2025+ и прежняя спецификация
ORDER_ENTITY, INVOICE_ENTITY, CLIENT_INVOICE_ENTITY = 172, 137, 31

TEST_CATS = frozenset({"22", "26", "28"})
BASRA_CAT = "16"
SINCE_137 = "2024-06-01T00:00:00"
U3_DAYS = 365
БЮДЖЕТ_ЗАПРОСОВ = 550
ЧАСТЕЙ_МИН = 10
PAY_SAMPLE, PAY_STOP_EMPTY = 30, 5
NDS_SWITCH = dt.date(2026, 1, 1)         # 20 % → 22 %: по дате отгрузки (критика C20)
CBR_LEAD_DAYS = 30                       # ряд ЦБ — с запасом до самой ранней даты (A11)

# Допуски и полосы (правило 4: порог — таблицей).
КОРЗИНЫ_ПАРЫ = ((0.05, "≤5 %"), (0.10, "5–10 %"), (0.20, "10–20 %"), (math.inf, ">20 %"))
ПОРЯДОК_КОРЗИН = ("ноль/минус", "≤5 %", "5–10 %", "10–20 %", ">20 %")
ДОПУСК = {"НДС": 0.015, "курс": 0.01, "×1000": 0.02, "совпадение": 0.02}
СТАВКИ_НДС = (1.20, 1.22)
ЧАСТЬ = (0.1, 0.8)
БОЛЬШЕ = (1.25, 10.0)
ОСОБЫЕ = ("НДС", "курс", "курс × НДС", "×1000", "неоднозначно")
ПОРЯДОК_ОБЪЯСНИТЕЛЕЙ = ОСОБЫЕ + ("часть", "больше", "прочее")
КОРЗИНЫ_СДВИГА = ((0.01, "≤1 %"), (0.05, "1–5 %"), (0.10, "5–10 %"), (0.50, "10–50 %"), (math.inf, ">50 %"))
ПОВТОРОВ_ФОНА = 20
ФОН_МИН = 5

ЗАКАЗЧИК_RX = re.compile(r"заказчик|покупател|клиент|customer|buyer|client|продаж|\bsale", re.I)
ПОСТАВЩИК_RX = re.compile(r"поставщик|supplier|vendor|закуп|purchase", re.I)
ТИП_1174_RX = re.compile(r"тип\s+документ|contract\s*type|тип\s+договор|сторон|чей\s+шаблон", re.I)
ВИДЫ_ПЛАТЕЖА = (("поставщ", "поставщик"), ("логист", "логистика"), ("тамож", "таможня"),
                ("агент", "агент"), ("аванс", "аванс"))
ФОРМЫ_ЛИЦА = re.compile(r"\b(ооо|оао|зао|пао|ао|ип|тоо|llc|ltd|limited|fze|fzco|fz|llp|gmbh|inc|co|company)\b", re.I)
ДОЛЯ_ВАЛЮТЫ = ("RUB", "EUR", "USD", "CNY")
# Код валюты в журнал — только трёхбуквенный латинский (ISO 4217): валюта money-поля
# вводится руками («123|ООО …»), и её текст иначе ушёл бы в публичный журнал.
КОД_ВАЛЮТЫ_RX = re.compile(r"[A-Z]{3}")
# Близнец в пресейле: КП не раньше TWIN_DAYS до создания карточки, сам пресейл заведён
# до своего КП — ищем пресейл, созданный не раньше 2 × TWIN_DAYS до самой ранней карточки.
ОКНО_БЛИЗНЕЦА_ДН = 2 * pc.TWIN_DAYS
БЛИЗНЕЦОВ_МАКС = 1000                    # 20 запросов списка; дальше список обрезается вслух
ТКП_У_СОЗДАНИЯ_ДН = 1                    # «ТКП согласован» = DATE_CREATE ±1 дн. — похоже на умолчание
РАЗНОС_ДАТ_СЧЁТА_ДН = 7                  # «Дата счета» против даты заведения СП-137
ЗЕРКАЛО_ЗАПРОСОВ = 900                   # резерв ЦБ: зеркало cbr-xml-daily.ru, запрос на дату (A12)


class ОшибкаЧасти(RuntimeError):
    """Шаг части не прошёл проверку полноты; сообщение — константа кода со счётчиками."""


# ── чистые функции ──────────────────────────────────────────────────────────

def доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else f"{a} из 0"


def пр(a: float, b: float) -> str:
    return f"{100 * a / b:.0f} %" if b else "—"


def счёт(c, порядок=None) -> str:
    ключи = [k for k in (порядок or []) if c.get(k)] + [k for k, _ in c.most_common() if k not in (порядок or [])]
    return " · ".join(f"{k} {c[k]}" for k in ключи if c.get(k)) or "—"


def код_валюты(c) -> str | None:
    """Код валюты, если он трёхбуквенный латинский; иначе None (ревью приватности v59)."""
    s = str(c or "").strip().upper()
    return s if КОД_ВАЛЮТЫ_RX.fullmatch(s) else None


def вал_подпись(c) -> str:
    """Подпись валюты для журнала: код, «пусто» или «нестандартный код» — без текста поля."""
    return код_валюты(c) or ("пусто" if not str(c or "").strip() else "нестандартный код")


def корзина_пары(a, b) -> str | None:
    """Корзина отношения двух сумм по модулю логарифма; ноль и минус — отдельно."""
    if a is None or b is None:
        return None
    if a <= 0 or b <= 0:
        return "ноль/минус"
    g = math.exp(abs(math.log(a / b))) - 1
    return next(n for t, n in КОРЗИНЫ_ПАРЫ if g <= t + 1e-12)


def корзина_сдвига(новое: float, было: float) -> str:
    g = math.exp(abs(math.log(новое / было))) - 1
    имя = next(n for t, n in КОРЗИНЫ_СДВИГА if g <= t + 1e-12)
    return имя if g <= КОРЗИНЫ_СДВИГА[0][0] else f"{имя} {'вверх' if новое > было else 'вниз'}"


def ставки_ндс(отгрузка: dt.date | None) -> tuple[float, ...]:
    """Ставка — по дате отгрузки: 20 % до 2026 года, 22 % с него; даты нет — обе."""
    if отгрузка is None:
        return СТАВКИ_НДС
    return (СТАВКИ_НДС[1],) if отгрузка >= NDS_SWITCH else (СТАВКИ_НДС[0],)


def _около(r: float, x: float, tol: float) -> bool:
    return r > 0 and x > 0 and abs(math.log(r) - math.log(x)) <= math.log1p(tol)


def _в_окне(r: float, окно: tuple[float, float], tol: float) -> bool:
    return окно[0] * (1 - tol) <= r <= окно[1] * (1 + tol)


def объяснить(r: float, окна: dict, ставки: tuple[float, ...]) -> str:
    """Объяснитель дальнего расхождения r = A/B (A — проверяемая сумма, B — опора).

    окна — {пара валют: (min, max)} курса «единиц одной за единицу другой» на окне
    дат ТКП, только для валют самой сделки (критика E31); ставки — НДС по дате
    отгрузки. Подошло несколько особых — «неоднозначно» (USD 1,20 против НДС 1,20)."""
    особые = set()
    for f in ставки:
        if _около(r, f, ДОПУСК["НДС"]) or _около(r, 1 / f, ДОПУСК["НДС"]):
            особые.add("НДС")
    for окно in окна.values():
        for x in (r, 1 / r):
            if _в_окне(x, окно, ДОПУСК["курс"]):
                особые.add("курс")
            for f in ставки:
                if _в_окне(x / f, окно, ДОПУСК["курс"]) or _в_окне(x * f, окно, ДОПУСК["курс"]):
                    особые.add("курс × НДС")
    if _около(r, 1000.0, ДОПУСК["×1000"]) or _около(r, 0.001, ДОПУСК["×1000"]):
        особые.add("×1000")
    if len(особые) == 1:
        return особые.pop()
    if особые:
        return "неоднозначно"
    if ЧАСТЬ[0] <= r <= ЧАСТЬ[1]:
        return "часть"
    if БОЛЬШЕ[0] <= r <= БОЛЬШЕ[1]:
        return "больше"
    return "прочее"


def _дата(v) -> dt.date | None:
    """Метка или дата портала → дата по Москве (A11: перевод до поиска курса)."""
    if v in (None, "", 0, "0", False):
        return None
    s = pc.дата_мск(v)
    try:
        return dt.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def выигрыш(предложения: list[dt.date], входы_кат0: list[dt.date] | None) -> dt.date | None:
    """Дата выигрыша — первый вход в кат. 0 не раньше первого КП; такого нет — последний вход.

    Сделка, заведённая в кат. 0, переведённая в пресейл и выигранная обратно в кат. 0,
    первый вход в кат. 0 имеет в день создания (разбор 09.10.2026, ревью v59): взять
    его за выигрыш — отбросить все КП."""
    if not входы_кат0:
        return None
    if предложения:
        после = [d for d in входы_кат0 if d >= min(предложения)]
        if после:
            return min(после)
    return max(входы_кат0)


def дата_ткп(сделка: dict, предложения: list[dt.date], входы_кат0: list[dt.date] | None,
             близнец: dt.date | None) -> tuple[dt.date | None, str]:
    """Дата курса по решению владельца 25.10.2026 → (дата, источник).

    1) «ТКП согласован»; 2) последний вход в стадию предложения до выигрыша (выигрыш —
    см. выигрыш()), у карточки только реализации — у близнеца в пресейле;
    3) первый вход в кат. 0; 4) DATE_CREATE. входы_кат0 — все входы в стадии кат. 0."""
    f = _дата(сделка.get(TKP_FINAL))
    if f:
        return f, "ТКП согласован"
    w = выигрыш(предложения, входы_кат0)
    до = [d for d in предложения if not w or d <= w]
    if до:
        return max(до), "последнее КП до выигрыша"
    if близнец:
        return близнец, "КП близнеца в пресейле"
    if входы_кат0:
        return min(входы_кат0), "вход в кат. 0"
    return _дата(сделка.get("DATE_CREATE")), "создание сделки"


def окно_дат(дата: dt.date | None, предложения: list[dt.date], нижняя: dt.date | None) -> tuple:
    """Окно, в котором мог быть взят курс: от первой отметки КП до итогового ТКП."""
    if not дата:
        return None, None
    ds = [дата] + [d for d in (min(предложения) if предложения else None, нижняя) if d]
    return min(ds), max(ds)


def окна_курсов(курсы, валюты, lo: dt.date | None, hi: dt.date | None) -> dict:
    """Окна кросс-курсов для валют сделки (всегда с евро)."""
    if not (курсы and lo and hi):
        return {}
    вал = sorted({str(v).upper() for v in валюты if v} | {"EUR"})
    out = {}
    for i, a in enumerate(вал):
        for b in вал[i + 1:]:
            w = курсы.cross_range(a, b, lo, hi)
            if w:
                out[f"{a}/{b}"] = w
    if {"AED", "USD"} <= set(вал):
        out["AED/USD привязка"] = (fx.AED_USD, fx.AED_USD)
    return out


def фон_случайных(ряды: list[tuple], rng: random.Random, повторов: int = ПОВТОРОВ_ФОНА) -> float | None:
    """Доля «объяснённых» особыми объяснителями среди перемешанных пар > 20 %.

    ряды — [(A, B, окна, ставки)]; опора B переставляется между сделками, контекст
    (валюты, даты, ставка) остаётся своим — так меряется, сколько попаданий даёт
    сама густота окон на оси отношений (критика E31)."""
    if len(ряды) < ФОН_МИН:
        return None
    попало = всего = 0
    for _ in range(повторов):
        опоры = [r[1] for r in ряды]
        rng.shuffle(опоры)
        for (a, _b, ок, ст), b in zip(ряды, опоры):
            if корзина_пары(a, b) != ">20 %":
                continue
            всего += 1
            попало += объяснить(a / b, ок, ст) in ОСОБЫЕ
    return попало / всего if всего else None


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _g(ряд: dict, *ключи):
    for k in ключи:
        if ряд.get(k) not in (None, ""):
            return ряд.get(k)
    return None


def свод_строк(ряды: list[dict] | None) -> dict | None:
    """Товарные строки (сделки — PRICE…, заказа — price…) → суммы без и с НДС в валюте
    владельца. PRICE — с налогом и скидкой, PRICE_EXCLUSIVE — со скидкой без налога."""
    if not ряды:
        return None
    net = gross = 0.0
    incl = 0
    ставки = collections.Counter()
    for r in ряды:
        q = _f(_g(r, "QUANTITY", "quantity")) or 0.0
        net += (_f(_g(r, "PRICE_EXCLUSIVE", "priceExclusive")) or 0.0) * q
        gross += (_f(_g(r, "PRICE", "price")) or 0.0) * q
        incl += str(_g(r, "TAX_INCLUDED", "taxIncluded") or "").upper() in ("Y", "TRUE", "1")
        t = _f(_g(r, "TAX_RATE", "taxRate"))
        ставки["нет" if t is None else f"{t:g} %"] += 1
    return {"net": net, "gross": gross, "n": len(ряды), "incl": incl, "rates": ставки}


def _имя_лица(s) -> str:
    s = ФОРМЫ_ЛИЦА.sub(" ", str(s or "").lower().replace("ё", "е"))
    return " ".join(re.sub(r"[^\w]+", " ", s).split())


def свой_получатель(получатель, свои: set[str]) -> bool:
    """Получатель платежа — своё юрлицо (внутригрупповой платёж, критика B17)."""
    p = _имя_лица(получатель)
    if len(p) < 4:
        return False
    return any(q and (q == p or (len(q) >= 4 and (q in p or p in q))) for q in свои)


def вид_платежа(*подписи) -> str:
    t = " ".join(str(x or "") for x in подписи).lower()
    return next((имя for корень, имя in ВИДЫ_ПЛАТЕЖА if корень in t), "прочее")


def сторона_спецификации(item: dict, поле_типа: str | None, подписи: dict, компания: str) -> tuple[str, str]:
    """Сторона документа → (сторона, способ): по перечислению типа, иначе по связи с
    заказом поставщику (поставщик), иначе по совпадению компании со сделкой (заказчик)."""
    v = item.get(поле_типа) if поле_типа else None
    vals = v if isinstance(v, list) else [v]
    текст = " ".join(str(подписи.get(str(x), x if isinstance(x, str) else "")) for x in vals if x not in (None, ""))
    з, п = bool(ЗАКАЗЧИК_RX.search(текст)), bool(ПОСТАВЩИК_RX.search(текст))
    if з != п:
        return ("заказчик" if з else "поставщик"), "тип"
    if str(item.get("parentId172") or "0") not in ("", "0"):
        return "поставщик", "связь с заказом"
    if компания not in ("", "0") and str(item.get("companyId") or "0") == компания:
        return "заказчик", "компания сделки"
    return "?", "не определена"


def сумма_спецификации(item: dict, entity: int, валюта_сделки: str) -> tuple[float | None, str | None, str]:
    """(сумма, валюта, откуда): системная opportunity; у СП-132 — «Summ of Specification»,
    когда системной нет (валюты у поля нет — валюта сделки, с пометкой)."""
    o = _f(item.get("opportunity"))
    cur = str(item.get("currencyId") or "") or None
    if o and o > 0:
        return o, cur or валюта_сделки, "opportunity" if cur else "opportunity, валюта сделки"
    if entity == 132:
        s = _f(item.get(SPEC132_SUM))
        if s and s > 0:
            return s, cur or валюта_сделки, "Summ of Specification" + ("" if cur else ", валюта сделки")
    return None, None, "нет суммы"


def дубли_заказов(заказы: list[dict]) -> set[str]:
    """Вероятные дубли: один поставщик, суммы в пределах 1 %, оба живые (одна сделка)."""
    out: set[str] = set()
    for i, a in enumerate(заказы):
        va = _f(a.get("opportunity")) or 0
        for b in заказы[i + 1:]:
            vb = _f(b.get("opportunity")) or 0
            if (va > 0 and vb > 0 and str(a.get("companyId") or "0") not in ("", "0")
                    and str(a.get("companyId")) == str(b.get("companyId"))
                    and abs(va - vb) / max(va, vb) <= 0.01):
                out.update({str(a.get("id")), str(b.get("id"))})
    return out


def отгрузка_заказа(o: dict) -> dt.date | None:
    for f in (ct.DEL_FACT, ct.DEL_PLAN, ct.DEL_BUDGET, ct.SHIP_FACT, ct.DL_CUSTOMER):
        d = _дата(o.get(f))
        if d:
            return d
    return None


def воронка_заказа(stage_id) -> str:
    """'DT172_26:NEW' → '26' (как ocat_ids в contracts.compute)."""
    m = re.match(r"DT\d+_(\d+):", str(stage_id or ""))
    return m.group(1) if m else "?"


def номера_сделок(v) -> set[str]:
    """Поле-связь «сделки» (список «D_123» или чисел) → номера."""
    vals = v if isinstance(v, list) else [v]
    out = set()
    for x in vals:
        m = re.search(r"(\d+)", str(x or ""))
        if m and m.group(1) != "0":
            out.add(m.group(1))
    return out


# ── курс Битрикса с тем же лицом, что fx.CbrRates ─────────────────────────────

class КурсБитрикса:
    """Ручные курсы crm.currency.list: value — единиц базовой валюты за 1 единицу."""

    def __init__(self, список: list[dict]):
        self.база = next((x.get("CURRENCY") for x in список if x.get("BASE") == "Y"), "EUR")
        self.курс: dict[str, float] = {}
        self.даты: dict[str, str] = {}
        for x in список:
            try:
                self.курс[str(x.get("CURRENCY")).upper()] = float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            self.даты[str(x.get("CURRENCY")).upper()] = str(x.get("DATE_UPDATE") or "")[:10]
        self.курс.setdefault(str(self.база).upper(), 1.0)

    def codes(self) -> set[str]:
        return set(self.курс)

    def value(self, code, day=None) -> float | None:
        return self.курс.get(str(code or "").upper())

    def cross(self, a, b, day=None) -> float | None:
        va, vb = self.value(a), self.value(b)
        return vb / va if (va and vb) else None

    def to_eur(self, amount, code, day=None) -> float | None:
        if amount is None or not code:
            return None
        k = self.cross(code, "EUR")
        return float(amount) / k if k else None

    def cross_range(self, a, b, lo=None, hi=None):
        x = self.cross(a, b)
        return (x, x) if x else None


# ── учёт нагрузки по блокам ─────────────────────────────────────────────────

class Учёт:
    def __init__(self, предел: int = БЮДЖЕТ_ЗАПРОСОВ):
        self.по_блокам: collections.Counter = collections.Counter()
        self.предел = предел

    @staticmethod
    def сейчас() -> int:
        return int(bc._НАГРУЗКА.get("запросов", 0))

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
        print(f"блок «{имя}» пропущен: смета {оценка} запросов не помещается в предел {self.предел}")
        return False


def место(e: BaseException) -> str:
    места = []
    tb = e.__traceback__
    while tb:
        места.append(f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}")
        tb = tb.tb_next
    return " → ".join(места[-3:])


# ── чтение портала ──────────────────────────────────────────────────────────

def _total(client, method: str, params: dict) -> int:
    env = client.call_envelope(method, dict(params, start=0)) or {}
    return int(env.get("total") or 0)


def по_родителю(client, entity: int, ids: list[str], select: list[str]) -> list[dict]:
    """Записи смарт-процесса, привязанные к сделкам (@parentId2 пачками по 50, по ключу >id).
    Фильтр проверяется по ответу: чужой parentId2 — ошибка части, а не молчаливый мусор."""
    out = []
    for i in range(0, len(ids), 50):
        пачка = [int(x) for x in ids[i:i + 50]]
        items = client.list_items(entity, filter={"@parentId2": пачка}, select=select)
        свои = {str(x) for x in пачка}
        if any(str(x.get("parentId2") or "") not in свои for x in items):
            raise ОшибкаЧасти(f"фильтр @parentId2 СП-{entity} не сработал")
        out.extend(items)
    return out


def поля_сп(client, entity: int) -> dict:
    res = client.call("crm.item.fields", {"entityTypeId": entity}) or {}
    return (res.get("fields") if isinstance(res, dict) else None) or {}


def подписи_перечисления(поле: dict) -> dict[str, str]:
    return {str(i.get("ID")): str(i.get("VALUE") or "") for i in (поле or {}).get("items") or []}


# ── сборка записей по сделкам ───────────────────────────────────────────────

def запись_сделки(did: str, d: dict, сег: str, ctx: dict, К, БХ, сегодня: dt.date) -> dict:
    """Все источники одной сделки в евро (или None). Чистая функция над прочитанным."""
    cur = str(d.get("CURRENCY_ID") or "").upper() or БХ.база
    opp = _f(d.get("OPPORTUNITY"))
    предл = sorted(ctx["предл"].get(did, []))
    kp, kp_src = дата_ткп(d, предл, ctx["кат0"].get(did), ctx["близнец"].get(did))
    lo, hi = окно_дат(kp, предл, _дата(d.get(KP_STAGE_DATE)))
    живые = ctx["заказы"].get(did, [])
    валюты = {cur} | {str(o.get("currencyId") or "").upper() for o in живые if o.get("currencyId")}
    отгр = [x for x in (отгрузка_заказа(o) for o in живые) if x]
    ship = max(отгр) if отгр else None

    def к_евро(сумма, валюта, день=kp):
        if сумма is None or not валюта or not день:
            return None
        return К.to_eur(сумма, валюта, день)

    r = {"id": did, "seg": сег, "cur": cur, "company": str(d.get("COMPANY_ID") or "0"),
         "kp": kp, "kp_src": kp_src, "lo": lo, "hi": hi, "ship": ship, "валюты": валюты,
         "opp": opp, "n_orders": len(живые)}
    r["SB"] = БХ.to_eur(opp, cur) if opp and opp > 0 else None
    r["SC"] = к_евро(opp, cur) if opp and opp > 0 else None
    r["SC0"] = К.to_eur(opp, cur, сегодня) if (opp and opp > 0 and ctx["цб"]) else None
    sr = ctx["строки"].get(did)
    r["SR_net"] = к_евро(sr["net"], cur) if sr and sr["net"] > 0 else None
    r["SR_gross"] = к_евро(sr["gross"], cur) if sr and sr["gross"] > 0 else None
    b = ctx["бюджет"].get(did)
    r["bud"] = b
    r["SBud_net"] = к_евро(_f(b.get("revenue_net")), "RUB") if b and _f(b.get("revenue_net")) else None
    r["SBud_gross"] = к_евро(_f(b.get("revenue")), "RUB") if b and _f(b.get("revenue")) else None
    r["SBud101"] = БХ.to_eur(_f(b.get("revenue_net")), "RUB") if b and _f(b.get("revenue_net")) else None
    # закупка в книге бюджета — расход со знаком минус (233 из 236 книг): по модулю, как
    # шаблон вкладки (Math.abs(B.buy), dashboard_core.html:1763)
    r["BBud_net"] = к_евро(закупка_бюджета(b, "purchase_net"), "RUB")
    r["BBud_gross"] = к_евро(закупка_бюджета(b, "purchase"), "RUB")
    спец = ctx["спец"].get(did) or []
    sv = [к_евро(a, c) for a, c, _src in спец]
    r["SSpec"] = sum(sv) if sv and all(x is not None for x in sv) else None
    r["n_spec"] = len(спец)
    сч = ctx["сч31"].get(did) or []
    iv = [к_евро(a, c) for a, c in сч]
    r["SInv"] = sum(iv) if iv and all(x is not None for x in iv) else None

    # закупка: живые заказы — по ЦБ на дату заказа, на дату ТКП и по Битриксу
    bo = bo_kp = bo_bx = 0.0
    полно, без_суммы, минус, без_курса = True, 0, 0, 0
    оплачено = 0.0
    оплачено_есть = False
    for o in живые:
        v = _f(o.get("opportunity")) or 0.0
        c = str(o.get("currencyId") or "").upper() or cur
        if v == 0:
            без_суммы += 1
            полно = False
            continue
        минус += v < 0
        e1 = К.to_eur(v, c, _дата(o.get("createdTime")) or kp) if (_дата(o.get("createdTime")) or kp) else None
        e2 = к_евро(v, c)
        e3 = БХ.to_eur(v, c)
        if e1 is None or e2 is None or e3 is None:
            без_курса += 1
            полно = False
            continue
        bo, bo_kp, bo_bx = bo + e1, bo_kp + e2, bo_bx + e3
        pct = _f(o.get(ct.SUP_PAID))
        if pct is not None:
            оплачено += e1 * max(0.0, min(pct, 100.0)) / 100
            оплачено_есть = True
    r["BO"] = bo if живые and bo else None
    r["BO_kp"] = bo_kp if живые and bo_kp else None
    r["BO_bx"] = bo_bx if живые and bo_bx else None
    r["BOpaid"] = оплачено if оплачено_есть and оплачено > 0 else None
    r["bo_full"] = bool(живые) and полно
    r["bo_nosum"], r["bo_neg"], r["bo_nofx"] = без_суммы, минус, без_курса
    r["bo_dup"] = bool(дубли_заказов(живые))
    # поля экономики: только сверка (дали €645K 23.10)
    for код, ключ in ((ct.BUY_ECON_RUB, "BCost"), (ct.BUY_ECON_VAL, "BOffer")):
        кл, v, c = pm._поле(d.get(код))
        c = код_валюты(c)
        r[ключ] = к_евро(v, c) if кл == "сумма" and v and v > 0 and c else None
    # счета СП-137: «оплаченные и в работе» — к заказам целиком; «только оплаченные» —
    # к заказам × «Оплачено поставщику, %». Сумма — всё или ничего, как у спецификаций.
    оп = ctx["оплаты"].get(did) or []
    r["BPaid"] = сумма_оплат(К, оп)
    r["BPaid_done"] = сумма_оплат(К, [x for x in оп if x[2] == "S"])
    r["bpaid_nofx"] = bool(оп) and r["BPaid"] is None
    r["окна"] = окна_курсов(К, валюты, lo, hi)
    r["ставки"] = ставки_ндс(ship)
    return r


ПАРЫ_ПРОДАЖИ = (
    ("SB", "SC", "Сумма: курс Битрикса против ЦБ на дату ТКП", True),
    ("SC0", "SC", "Сумма: ЦБ сегодня против ЦБ на дату ТКП", True),
    ("SC", "SBud_net", "Сумма против бюджета без НДС", False),
    ("SC", "SBud_gross", "Сумма против бюджета с НДС", False),
    ("SC", "SSpec", "Сумма против спецификации с заказчиком", False),
    ("SC", "SR_net", "Сумма против товарных строк без НДС", False),
    ("SC", "SR_gross", "Сумма против товарных строк с НДС", False),
    ("SC", "SInv", "Сумма против счетов клиенту (СП-31)", False),
    ("SBud_net", "SSpec", "бюджет без НДС против спецификации (две опоры)", False),
    ("SB", "SBud101", "как на вкладке: Сумма и бюджет по курсу Битрикса", False),
)
ПАРЫ_ЗАКУПКИ = (
    ("BO_kp", "BBud_net", "заказы против бюджета без НДС (оба на дату ТКП)", False),
    ("BO_kp", "BBud_gross", "заказы против бюджета с НДС", False),
    ("BCost", "BO_kp", "«Cost (rub)» против заказов", False),
    ("BOffer", "BO_kp", "«Offer sum» против заказов", False),
    ("BPaid", "BO", "счета СП-137 оплаченные и в работе против заказов", False),
    ("BPaid_done", "BOpaid", "оплаченные счета СП-137 против заказов × «Оплачено поставщику, %»", False),
    ("BO", "BO_kp", "закупка: ЦБ на дату заказа против ЦБ на дату ТКП", True),
    ("BO", "BO_bx", "закупка: ЦБ на дату заказа против курса Битрикса", True),
)


def закупка_бюджета(b: dict | None, ключ: str) -> float | None:
    """Закупка книги бюджета в ₽ по модулю (в книге — расход со знаком минус)."""
    v = _f(b.get(ключ)) if b else None
    return abs(v) if v else None


def сумма_оплат(К, ряд: list[tuple]) -> float | None:
    """Счета СП-137 [(₽, дата, семантика)] → €; хоть один без курса — None (не тихий недосчёт)."""
    pv = [К.to_eur(rub, "RUB", day) if day else None for rub, day, _sem in ряд]
    return sum(pv) if pv and all(x is not None for x in pv) else None


def разбор_пары(записи: list[dict], a: str, b: str, rng: random.Random) -> dict:
    ряды = [(r[a], r[b], r["окна"], r["ставки"], r["seg"]) for r in записи
            if r.get(a) is not None and r.get(b) is not None]
    корз = collections.Counter(корзина_пары(x, y) for x, y, *_ in ряды)
    сег = collections.Counter(s for *_, s in ряды)
    объясн = collections.Counter(объяснить(x / y, ок, ст) for x, y, ок, ст, _s in ряды
                                 if корзина_пары(x, y) == ">20 %")
    дальних = sum(объясн.values())
    особых = sum(объясн[k] for k in ОСОБЫЕ)
    фон = фон_случайных([(x, y, ок, ст) for x, y, ок, ст, _s in ряды if x > 0 and y > 0], rng)
    return {"n": len(ряды), "корз": корз, "сег": сег, "объясн": объясн, "дальних": дальних,
            "особых": особых, "фон": фон}


def строка_пары(a: str, b: str, имя: str, р: dict) -> str:
    if not р["n"]:
        return f"  {a}–{b} ({имя}): обе суммы есть у 0"
    фон = "—" if р["фон"] is None else f"{100 * р['фон']:.0f} %"
    return (f"  {a}–{b} ({имя}): обе есть {р['n']} ({счёт(р['сег'], ('U1', 'U2', 'U3'))})"
            f" · {счёт(р['корз'], ПОРЯДОК_КОРЗИН)}"
            + (f" | дальние: {счёт(р['объясн'], ПОРЯДОК_ОБЪЯСНИТЕЛЕЙ)}; особыми объяснено "
               f"{пр(р['особых'], р['дальних'])} при фоне {фон}" if р["дальних"] else ""))


def гипотеза_aed(записи: list[dict], курсы, ключ: str = "SB") -> tuple[int, int, collections.Counter]:
    """EUR-сделки, у которых сумма к лучшей независимой опоре попала в полосу курса
    AED за EUR на окне дат ТКП (гипотеза владельца: число в AED под валютой EUR)."""
    n = m = 0
    по_опоре: collections.Counter = collections.Counter()
    for r in записи:
        if r["cur"] != "EUR" or not r.get(ключ) or not r["lo"]:
            continue
        опора = next(((v, имя) for v, имя in ((r["SSpec"], "спецификация"), (r["SBud_net"], "бюджет"),
                                              (r["SR_net"], "строки")) if v), None)
        if not опора:
            continue
        m += 1
        окно = курсы.cross_range("AED", "EUR", r["lo"], r["hi"])
        if окно and _в_окне(r[ключ] / опора[0], окно, ДОПУСК["курс"]):
            n += 1
            по_опоре[опора[1]] += 1
    return n, m, по_опоре


def ндс_по_валютам(записи: list[dict], страна: dict) -> dict:
    """«Сумма» против бюджета с НДС и без: RUB — в рублях напрямую, прочие — по ЦБ."""
    out: dict = collections.defaultdict(collections.Counter)
    for r in записи:
        b = r["bud"]
        if not b or not r["opp"] or r["opp"] <= 0:
            continue
        if r["cur"] == "RUB":
            a, g, n = r["opp"], _f(b.get("revenue")), _f(b.get("revenue_net"))
        else:
            a, g, n = r["SC"], r["SBud_gross"], r["SBud_net"]
        if not a or not (g or n):
            continue
        tol = ДОПУСК["совпадение"]
        ok_g = bool(g and _около(a, g, tol))
        ok_n = bool(n and _около(a, n, tol))
        if ok_g and ok_n:
            кл = "бюджет без НДС (сходится с обеими)"
        elif ok_g:
            кл = "≈ с НДС"
        elif ok_n:
            кл = "≈ без НДС"
        else:
            кл = "ни то ни другое"
        out[(r["cur"] if r["cur"] in ДОЛЯ_ВАЛЮТЫ else "прочие", страна.get(r["company"], "?"))][кл] += 1
    return out


def правила_вкладки(записи: list[dict], БХ, все: list[dict]) -> dict[str, dict]:
    """Приближение нынешних правил contracts.py (:391-419, :686-710) по каждой сделке:
    продажа SB, каскад закупки «заказы → поля экономики», buySusp, grossSusp."""
    rate = БХ.курс
    out = {}
    for r in все:
        d = r["_d"]
        sale = r["SB"] or 0.0
        bo = sum(БХ.to_eur(_f(o.get("opportunity")) or 0.0, str(o.get("currencyId") or "").upper() or r["cur"]) or 0.0
                 for o in r["_orders"])
        econ = ct._parse_econ_money(d.get(ct.BUY_ECON_RUB), rate) or ct._parse_econ_money(d.get(ct.BUY_ECON_VAL), rate)
        src, buy = ("orders", bo) if bo > 0 else ("econ", econ) if econ > 0 else ("none", 0.0)
        susp = bool(sale > 0 and buy > 0 and not ct.BUY_SANE[0] <= buy / sale <= ct.BUY_SANE[1])
        margin = (sale - buy) if (sale and src != "none" and not susp) else None
        pct = round(margin / sale * 100) if (margin is not None and sale) else None
        out[r["id"]] = {"sale": sale, "buy": buy, "src": src, "susp": susp, "margin": margin, "pct": pct}
    gross = [x["pct"] for x in out.values() if x["src"] == "orders" and x["pct"] is not None and x["sale"] > 1000]
    thr = max(65.0, round((statistics.median(gross) if gross else 0) * 1.5, 1))
    for r in все:
        x = out[r["id"]]
        if (r["seg"] == "U1" and x["src"] == "orders" and r["n_orders"] and x["pct"] is not None
                and x["sale"] > 1000 and x["pct"] > thr):
            x["margin"] = None
    return {r["id"]: out[r["id"]] for r in записи}


def сверка_итогов(строки: dict[str, dict]) -> dict:
    """Множества Σ продажи / Σ закупки / Σ маржи (contracts.py:865-870) и невязка."""
    S = {k for k, x in строки.items() if x["sale"] > 0}
    B = {k for k, x in строки.items() if x["src"] != "none" and not x["susp"]}
    M = {k for k, x in строки.items() if x["margin"] is not None}
    ss = sum(строки[k]["sale"] for k in S)
    sb = sum(строки[k]["buy"] for k in B)
    sm = sum(строки[k]["margin"] for k in M)
    return {"S": len(S), "B": len(B), "M": len(M), "SB": len(S & B), "SBM": len(S & B & M),
            "невязка": (ss - sb - sm) / ss * 100 if ss else None}


def тень(записи: list[dict], нынешние: dict[str, dict]) -> dict:
    """Теневой каскад (проект v59, §2) по U1 без файла экономики: продажа — «Сумма» по ЦБ
    на дату ТКП, если ни спецификация, ни бюджет не дают кластер курса или ×1000;
    иначе «под сомнением» (числа нет); бюджет — только когда суммы нет. Закупка —
    заказы по ЦБ на дату заказа, если все живые заказы с суммой, без дублей и в
    коридоре BUY_SANE; иначе бюджет. Маржа — только из одного семейства."""
    ист_п: collections.Counter = collections.Counter()
    ист_з: collections.Counter = collections.Counter()
    сдвиг: collections.Counter = collections.Counter()
    хуже: collections.Counter = collections.Counter()
    строки: dict[str, dict] = {}
    for r in записи:
        sale, src = None, "суммы нет"
        if r["SC"]:
            подозр = False
            for опора in (r["SSpec"], r["SBud_net"]):
                if опора and корзина_пары(r["SC"], опора) == ">20 %":
                    if объяснить(r["SC"] / опора, r["окна"], r["ставки"]) in ("курс", "курс × НДС", "×1000"):
                        подозр = True
            sale, src = (None, "под сомнением") if подозр else (r["SC"], "Битрикс по ЦБ")
        elif r["SBud_net"]:
            sale, src = r["SBud_net"], "бюджет"
        ист_п[src] += 1
        buy, bsrc = None, "нет"
        if r["n_orders"]:
            if not r["bo_full"]:
                bsrc = "заказы неполные"
            elif r["bo_dup"]:
                bsrc = "заказы с дублем"
            elif r["BO"] and sale and not ct.BUY_SANE[0] <= r["BO"] / sale <= ct.BUY_SANE[1]:
                bsrc = "заказы вне коридора"
            elif r["BO"]:
                buy, bsrc = r["BO"], "заказы по ЦБ"
        if buy is None and bsrc in ("нет",) and r["BBud_net"]:
            buy, bsrc = r["BBud_net"], "бюджет"
        ист_з[bsrc] += 1
        семья = ("валовая (Битрикс)" if src == "Битрикс по ЦБ" and bsrc == "заказы по ЦБ"
                 else "план (бюджет)" if src == "бюджет" and bsrc == "бюджет" else None)
        строки[r["id"]] = {"sale": sale or 0.0, "buy": buy or 0.0,
                           "src": "none" if buy is None else bsrc, "susp": False,
                           "margin": (sale - buy) if (семья == "валовая (Битрикс)") else None,
                           "family": семья}
        было = r["SB"]
        if было and sale:
            сдвиг[корзина_сдвига(sale, было)] += 1
        elif было and not sale:
            сдвиг["убрано из итога"] += 1
        elif sale and not было:
            сдвиг["добавлено"] += 1
        # опора «хуже/лучше» — не тот источник, что показан (критика D27)
        опора = r["SSpec"] or (r["SBud_net"] if src != "бюджет" else None)
        if опора and опора > 0 and было and было > 0 and sale and sale > 0:
            d_new, d_old = abs(math.log(sale / опора)), abs(math.log(было / опора))
            хуже["дальше от опоры" if d_new > d_old + 1e-3 else "ближе к опоре" if d_new < d_old - 1e-3 else "так же"] += 1
    сейчас = сверка_итогов(нынешние)
    Σ_было_п = sum(x["sale"] for x in нынешние.values() if x["sale"] > 0)
    Σ_стало_п = sum(x["sale"] for x in строки.values() if x["sale"] > 0)
    Σ_было_з = sum(x["buy"] for x in нынешние.values() if x["src"] != "none" and not x["susp"])
    Σ_стало_з = sum(x["buy"] for x in строки.values() if x["src"] != "none")
    один = {k: x for k, x in строки.items() if x["family"] == "валовая (Битрикс)"}
    # Сверка тени — по ВСЕМ её строкам, теми же множествами, что у нынешних правил: по
    # одному семейству невязка была бы нулём по построению (ревью v59).
    return {"ист_п": ист_п, "ист_з": ист_з, "сдвиг": сдвиг, "хуже": хуже,
            "Δпрод": (Σ_стало_п - Σ_было_п) / Σ_было_п * 100 if Σ_было_п else None,
            "Δзак": (Σ_стало_з - Σ_было_з) / Σ_было_з * 100 if Σ_было_з else None,
            "маржа_было": сейчас["M"], "маржа_стало": len(один),
            "план": sum(1 for x in строки.values() if x["family"] == "план (бюджет)"),
            "сейчас": сейчас, "тень_итоги": сверка_итогов(строки) if строки else None}


# ── главное ─────────────────────────────────────────────────────────────────


def _частей() -> int:
    try:
        n = int(os.environ.get("PROBE_PARTS") or ЧАСТЕЙ_МИН)
    except ValueError:
        n = ЧАСТЕЙ_МИН
    if n < ЧАСТЕЙ_МИН:
        print(f"::warning::PROBE_PARTS={n} меньше {ЧАСТЕЙ_МИН} — беру {ЧАСТЕЙ_МИН} (распоряжение 22.09)")
        n = ЧАСТЕЙ_МИН
    return min(n, 50)


def страна_по_реквизитам(реквизиты: dict, пресеты: dict[str, str]) -> dict[str, str]:
    """Компания → «РФ» / «не РФ»: по стране шаблона реквизита (COUNTRY_ID 1 — Россия),
    без шаблона — по верному российскому ИНН. Хоть один российский реквизит — РФ."""
    out: dict[str, str] = {}
    for c, qs in реквизиты.items():
        for q in qs:
            страна_шаблона = пресеты.get(str(q.get("PRESET_ID") or ""))
            ру = страна_шаблона == "1" or (not страна_шаблона and cn.инн_верен(q.get("RQ_INN")))
            if out.get(str(c)) != "РФ":
                out[str(c)] = "РФ" if ру else "не РФ"
    return out


class Прогон:
    """Состояние одного прогона: что прочитано из портала и посчитано по пути."""

    def __init__(self, client, сегодня: dt.date):
        self.client, self.сегодня = client, сегодня
        self.учёт = Учёт()
        self.частей = _частей()
        self.коды_цб: dict | None = None
        self.ctx = {"предл": {}, "кат0": {}, "близнец": {}, "строки": {}, "спец": {}, "сч31": {}, "оплаты": {},
                    "заказы": collections.defaultdict(list), "бюджет": {}, "цб": False}
        self.s_вход: dict[str, dt.date] = {}
        self.прочитанные: set[str] = set()
        self.спец_счёт: collections.Counter = collections.Counter()
        self.спец_сделка: dict[str, str] = {}
        self.частей_всего = self.упавшие = 0
        self.без_поля: list[str] = []
        self.без_кп: list[str] = []
        self.близнецов_искали = 0
        self.близнецы_обрезаны = False
        self.страна: dict[str, str] = {}
        self.оплаты_счёт: collections.Counter = collections.Counter()
        self.виды: collections.Counter = collections.Counter()
        self.sem137: collections.Counter = collections.Counter()
        self.строки_зак: dict[str, list] = {}
        self.живые_u: list[str] = []
        self.оплаты_кл: collections.Counter = collections.Counter()
        self.прочие: list[str] = []
        self.книга_сделок: collections.Counter = collections.Counter()
        self.знак_закупки: collections.Counter = collections.Counter()
        self.нестандартных_валют = 0
        self.зеркало: str | None = None          # зеркало ЦБ доступно (A12): «да» / тип ошибки
        self.источник_цб = "ЦБ"
        self.plan_asof = ""
        self.даты: list[dt.date] = []
        self.ецб: list[str] = []
        self.К = None

    @property
    def полно(self) -> bool:
        return self.упавшие == 0


def _проверить_зеркало() -> str:
    """Зеркало cbr-xml-daily.ru одним запросом → «да» или тип ошибки."""
    try:
        fx.cbr_mirror_codes()
        return "да"
    except Exception as e:  # noqa: BLE001 — печатается только тип
        return type(e).__name__


def шаг_цб(п: Прогон) -> None:
    """Доступен ли ЦБ с раннера — первой строкой журнала; нет — проверка зеркала (A12)."""
    try:
        п.коды_цб = fx.cbr_codes()
        print(f"ЦБ РФ доступен с раннера: справочник валют — {len(п.коды_цб)} кодов")
        return
    except Exception as e:  # noqa: BLE001 — любая причина значит «рядов с cbr.ru нет»
        п.коды_цб = None
        причина = type(e).__name__
    п.зеркало = _проверить_зеркало()
    if п.зеркало == "да":
        print(f"ЦБ РФ недоступен с раннера ({причина}) — курсы берутся с зеркала курсов ЦБ на нужные "
              f"даты (A12); пересчёт 1:1 не подставляется")
    else:
        print(f"ЦБ РФ недоступен с раннера ({причина}), зеркало курсов ЦБ тоже ({п.зеркало}) — колонки "
              f"«по ЦБ» не считаются, пересчёт 1:1 не подставляется; сравнения ниже — по курсу Битрикса")


def шаг_основа(п: Прогон) -> bool:
    """Валюты, стадии, сделки кат. 0, ожидаемые числа, воронка 16, сегменты, смета."""
    client, учёт = п.client, п.учёт
    with учёт.блок("валюты"):
        п.БХ = КурсБитрикса(client.call("crm.currency.list", {}) or [])
    п.К = п.БХ
    with учёт.блок("справочник стадий"):
        мета = client.deal_stage_meta()
    п.предл_стадии = {sid for sid in мета if pc.вид_стадии(sid, мета)[0] == "offer"}
    п.успех0 = {sid for sid, m in мета.items() if m.get("cat") == "0" and m.get("sem") == "S"}
    if not п.предл_стадии or not п.успех0:
        # без фильтра по стадиям история читалась бы целиком — сотни лишних запросов
        print("::error::в справочнике нет стадий предложения или успеха кат. 0 — история не читается")
        return False
    оплата0 = {sid for sid, m in мета.items() if m.get("cat") == "0" and ct.PAID_RX.search(str(m.get("name") or ""))}
    print(f"стадий сделок {len(мета)}: «предложение выдано» {len(п.предл_стадии)} в "
          f"{len({pc.воронка_стадии(s) for s in п.предл_стадии})} воронках · успех кат. 0 {len(п.успех0)} · "
          f"«Оплата получена» {len(оплата0)}")

    sel = ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID",
           "TAX_VALUE", "IS_MANUAL_OPPORTUNITY", "OPPORTUNITY_ACCOUNT", "ACCOUNT_CURRENCY_ID",
           "CLOSEDATE", "MOVED_TIME", "DATE_CREATE", "COMPANY_ID", TKP_FINAL, KP_STAGE_DATE, PAID_DATE,
           OFFER_FILE, ECON_FILE_BX, ct.BUY_ECON_RUB, ct.BUY_ECON_VAL, ct.ECON_MARGIN, ct.ECON_PAID,
           ct.ECON_REST, ct.ECON_LINK, ct.BUDGET_LINK]
    фильтр = {"CATEGORY_ID": 0, "!STAGE_SEMANTIC_ID": "F"}
    with учёт.блок("сделки"):
        ждём = _total(client, "crm.deal.list", {"filter": фильтр, "select": ["ID"]})
        сырые = [d for d in client.list_deals_fast(filter=фильтр, select=sel)
                 if str(d.get("CATEGORY_ID") or "0") == "0"]
    with учёт.блок("ожидаемые числа"):
        п.ждём_заказов = _total(client, "crm.item.list", {"entityTypeId": ORDER_ENTITY, "filter": {}, "select": ["id"]})
        п.ждём_137 = _total(client, "crm.item.list", {"entityTypeId": INVOICE_ENTITY,
                                                      "filter": {">=createdTime": SINCE_137}, "select": ["id"]})
        п.ждём_31 = _total(client, "crm.item.list", {"entityTypeId": CLIENT_INVOICE_ENTITY, "filter": {},
                                                     "select": ["id"]})
    print(f"сделки кат. 0 кроме проигранных: прочитано {len(сырые)} из {ждём}")
    if ждём <= 0 or len(сырые) < ждём:
        print("::error::обход сделок неполон — выводы были бы неполными")
        return False
    with учёт.блок(f"воронка {BASRA_CAT}"):
        basra_комп = {str(d.get("COMPANY_ID")) for d in client.list_deals_fast(
            filter={"CATEGORY_ID": int(BASRA_CAT)}, select=["ID", "COMPANY_ID"])
            if str(d.get("COMPANY_ID") or "0") != "0"}

    п.порог_u3 = п.сегодня - dt.timedelta(days=U3_DAYS)
    п.сег = {}
    сег_счёт: collections.Counter = collections.Counter()
    basra: collections.Counter = collections.Counter()
    for d in сырые:
        sem = str(d.get("STAGE_SEMANTIC_ID") or "").upper()
        if sem == "S":
            moved = _дата(d.get("MOVED_TIME"))
            # MOVED_TIME не раньше входа в успех: старше порога — точно старше (историю не читаем)
            s = "U3?" if (moved is None or moved >= п.порог_u3) else "старше 365"
        elif str(d.get("STAGE_ID") or "") in оплата0:
            s = "U2"
        else:
            s = "U1"
        if str(d.get("COMPANY_ID") or "0") in basra_комп:
            basra[s.rstrip("?")] += 1
            continue
        сег_счёт[s] += 1
        if s != "старше 365":
            п.сег[str(d["ID"])] = s
    п.сделки = {str(d["ID"]): d for d in сырые}

    n_u = len(п.сег)
    rps, par = bc.бюджет_портала()
    смета = {
        "основа": 1 + 12 + 4 + ждём // 50 + 1 + 2,
        "заказы СП-172": п.ждём_заказов // 50 + 1 + 6,
        "история стадий": (n_u // 50 + п.частей) * 2,
        "строки сделок": n_u // 50 + п.частей,
        "спецификации 1174/132": 2 + 2 * (n_u // 50 + п.частей),
        "счета клиенту СП-31": (n_u // 50 + п.частей) if п.ждём_31 else 0,
        "запасные даты и близнецы": 12,
        "строки заказов": п.ждём_заказов // 60 + 6,
        "СП-137": п.ждём_137 // 50 + 4,
        "реквизиты": 2 * (n_u // 100 + 1) + 1,
        "оплаты": PAY_SAMPLE, "прочие источники": 6,
    }
    п.смета = sum(смета.values())
    print(f"ожидается: сделок {ждём}, в выборке U {n_u}; заказов СП-172 {п.ждём_заказов}; счетов СП-137 с "
          f"{SINCE_137[:10]} {п.ждём_137}; счетов клиенту СП-31 {п.ждём_31}; частей {п.частей}; смета ≈ {п.смета} "
          f"запросов (предел {БЮДЖЕТ_ЗАПРОСОВ}), ≈ {п.смета * par / rps / 60:.0f} мин при {rps:g}/с")
    print("сегменты: " + счёт(сег_счёт, ("U1", "U2", "U3?", "старше 365"))
          + f" · воронка {BASRA_CAT} и её компании (отдельный сегмент, вне сравнений): "
          + счёт(basra, ("U1", "U2", "U3", "старше 365")))
    return True


def шаг_заказы(п: Прогон) -> bool:
    """СП-172 полным обходом по >id: родители из чужих воронок, сироты, флажки."""
    client = п.client
    with п.учёт.блок("заказы СП-172"):
        п.заказы_все = client.list_items(ORDER_ENTITY, filter={}, select=[
            "id", "title", "parentId2", ORDER_DEAL_ID, "opportunity", "currencyId", "taxValue", "stageId",
            "createdTime", "companyId", ct.SUP_PAID, ct.DEL_FACT, ct.DEL_PLAN, ct.DEL_BUDGET,
            ct.SHIP_FACT, ct.DL_CUSTOMER])
        провал = bc.lost_stage(client, ORDER_ENTITY, п.заказы_все)
    print(f"заказы СП-172: прочитано {len(п.заказы_все)} из {п.ждём_заказов}")
    if len(п.заказы_все) < п.ждём_заказов:
        print("::error::обход заказов неполон — закупка была бы неполной")
        return False
    живые = [o for o in п.заказы_все if not провал(o.get("stageId"))]
    сироты = [o for o in живые if str(o.get("parentId2") or "0") in ("", "0")]
    for o in живые:
        p = str(o.get("parentId2") or "0")
        if p not in ("", "0"):
            п.ctx["заказы"][p].append(o)
    чужие = sorted({p for p in п.ctx["заказы"] if p not in п.сделки})
    with п.учёт.блок("заказы СП-172"):
        родители = client.deals_by_ids(чужие, select=["ID", "CATEGORY_ID", "STAGE_SEMANTIC_ID"]) if чужие else {}
    вид_род: collections.Counter = collections.Counter()
    for p in чужие:
        cat = str((родители.get(p) or {}).get("CATEGORY_ID") or "?")
        вид_род["тестовые 22/26/28" if cat in TEST_CATS else f"воронка {BASRA_CAT}" if cat == BASRA_CAT
                else "кат. 0 проигранные" if cat == "0" else "не найдены" if cat == "?" else "другие воронки"] += 1
    расхожд = sum(1 for o in живые if str(o.get(ORDER_DEAL_ID) or "").strip() not in ("", "0")
                  and str(o.get("parentId2") or "0") not in ("", "0")
                  and номера_сделок(o.get(ORDER_DEAL_ID)) != {str(o.get("parentId2"))})
    сироты_с_ид = [o for o in сироты if номера_сделок(o.get(ORDER_DEAL_ID))]
    на_выборку = sum(1 for o in сироты_с_ид if номера_сделок(o.get(ORDER_DEAL_ID)) & set(п.сег))
    print(f"живые заказы {len(живые)} (провал по семантике стадии: {len(п.заказы_все) - len(живые)}); "
          f"валюта: {счёт(collections.Counter(вал_подпись(o.get('currencyId')) for o in живые))}")
    print(f"  воронки заказов: {счёт(collections.Counter(воронка_заказа(o.get('stageId')) for o in живые))}"
          f" · без суммы {sum(1 for o in живые if not _f(o.get('opportunity')))} · с минусом "
          f"{sum(1 for o in живые if (_f(o.get('opportunity')) or 0) < 0)} · taxValue заполнен "
          f"{sum(1 for o in живые if _f(o.get('taxValue')))}")
    print(f"  родители заказов вне кат. 0 (непроигранные сделки): {len(чужие)} — {счёт(вид_род)}; "
          f"«ID сделки» ≠ parentId2: {расхожд}; сирот без parentId2 {len(сироты)}, из них с «ID сделки» "
          f"{len(сироты_с_ид)} (на сделку выборки {на_выборку})")
    return True


def _поле_стороны(п: Прогон) -> None:
    поля = {}
    with п.учёт.блок("спецификации 1174/132"):
        for ent in SPEC_ENTITIES:
            поля[ent] = поля_сп(п.client, ent)
    п.тип_поле = {132: SPEC132_TYPE if SPEC132_TYPE in поля[132] else None,
                  1174: next((k for k, f in поля[1174].items() if str(f.get("type")) == "enumeration"
                              and ТИП_1174_RX.search(str(f.get("title") or ""))), None)}
    п.подписи_тип = {ent: подписи_перечисления(поля[ent].get(п.тип_поле[ent])) if п.тип_поле[ent] else {}
                     for ent in SPEC_ENTITIES}
    print(f"поле стороны спецификации: СП-132 {п.тип_поле[132] or 'нет'} ({len(п.подписи_тип[132])} значений), "
          f"СП-1174 {п.тип_поле[1174] or 'нет'} ({len(п.подписи_тип[1174])} значений)")


def _часть(п: Прогон, часть: list[str]) -> dict:
    """Шаги одной части: история стадий, строки, спецификации, счета клиенту. Чтение
    идёт в своё хранилище части: упавшая часть ничего не оставляет в общих итогах."""
    client, учёт = п.client, п.учёт
    loc = {"предл": {}, "s": {}, "строки": {}, "спец": collections.defaultdict(list),
           "сч31": collections.defaultdict(list), "спец_счёт": collections.Counter(), "спец_сделка": {}}
    with учёт.блок("история стадий"):
        ист, ош = pc.читать_историю(client, часть, стадии=п.предл_стадии | п.успех0)
    if ош:
        raise ОшибкаЧасти(ош)
    for did, ряд in ист.items():
        for sid, t in ряд:
            dd = _дата(t)
            if dd and sid in п.успех0:
                loc["s"][did] = max(loc["s"].get(did, dd), dd)       # последний вход в успех
            elif dd and sid in п.предл_стадии:
                loc["предл"].setdefault(did, []).append(dd)
    живые = []
    for did in часть:
        if п.сег[did] == "U3?":
            dd = loc["s"].get(did) or _дата(п.сделки[did].get("MOVED_TIME"))
            if dd and dd < п.порог_u3:
                continue
        живые.append(did)
    loc["живые"] = живые
    with учёт.блок("строки сделок"):
        for did, ряды in pc.строки_сделок(client, живые).items():
            loc["строки"][did] = свод_строк(ряды)
    with учёт.блок("спецификации 1174/132"):
        for ent in SPEC_ENTITIES:
            sel = ["id", "parentId2", "parentId172", "companyId", "opportunity", "currencyId", "stageId"]
            sel += [f for f in (п.тип_поле[ent], SPEC132_SUM if ent == 132 else None) if f]
            for it in по_родителю(client, ent, живые, sel):
                did = str(it.get("parentId2"))
                d = п.сделки[did]
                loc["спец_сделка"][f"{ent}:{it.get('id')}"] = did
                сторона, как = сторона_спецификации(it, п.тип_поле[ent], п.подписи_тип[ent],
                                                    str(d.get("COMPANY_ID") or "0"))
                loc["спец_счёт"][(ent, сторона, как)] += 1
                if сторона != "заказчик":
                    continue
                a, c, src = сумма_спецификации(it, ent, str(d.get("CURRENCY_ID") or "").upper())
                loc["спец_счёт"][(ent, "сумма", src)] += 1
                if a:
                    loc["спец"][did].append((a, c, src))
    if п.ждём_31:
        with учёт.блок("счета клиенту СП-31"):
            for it in по_родителю(client, CLIENT_INVOICE_ENTITY, живые,
                                  ["id", "parentId2", "opportunity", "currencyId", "stageId"]):
                v = _f(it.get("opportunity"))
                if v and v > 0:
                    loc["сч31"][str(it.get("parentId2"))].append((v, str(it.get("currencyId") or "").upper()))
    return loc


def шаг_части(п: Прогон) -> None:
    """Шаги по сделкам — PROBE_PARTS смежными частями по номеру, по очереди."""
    _поле_стороны(п)
    ids = sorted(п.сег, key=int)
    шаг = max(1, math.ceil(len(ids) / п.частей)) if ids else 1
    части = [ids[i:i + шаг] for i in range(0, len(ids), шаг)]
    п.частей_всего = len(части)
    for k, часть in enumerate(части, 1):
        try:
            loc = _часть(п, часть)
        except ОшибкаЧасти as e:
            п.упавшие += 1
            print(f"часть {k} из {len(части)}: не прочитана — {e}")
            continue
        except Exception as e:  # noqa: BLE001 — печатаются только тип и место
            п.упавшие += 1
            print(f"часть {k} из {len(части)}: сбой {type(e).__name__} · {место(e)}")
            continue
        for did in часть:
            if did not in loc["живые"]:
                п.сег[did] = "старше 365"
                continue
            п.прочитанные.add(did)
            if п.сег[did] == "U3?":
                п.сег[did] = "U3"
        for ключ in ("предл", "строки", "спец", "сч31"):
            п.ctx[ключ].update(loc[ключ])
        п.s_вход.update(loc["s"])
        п.спец_счёт.update(loc["спец_счёт"])
        п.спец_сделка.update(loc["спец_сделка"])
    print(f"частей прочитано {п.частей_всего - п.упавшие} из {п.частей_всего}; сделок в выводах {len(п.прочитанные)}")


def шаг_запасные_даты(п: Прогон) -> None:
    """Для сделок без «ТКП согласован»: вход в кат. 0 и КП близнеца в пресейле (A7)."""
    client, сделки = п.client, п.сделки
    п.без_поля = [did for did in sorted(п.прочитанные, key=int) if not _дата(сделки[did].get(TKP_FINAL))]
    п.без_кп = [did for did in п.без_поля if not п.ctx["предл"].get(did)]
    try:
        with п.учёт.блок("запасные даты и близнецы"):
            if п.без_поля:
                ист0, ош = pc.читать_историю(client, п.без_поля, категория=0)
                if ош:
                    raise ОшибкаЧасти(ош)
                for did, ряд in ист0.items():
                    ds = sorted({x for x in (_дата(t) for _s, t in ряд) if x})
                    if ds:
                        п.ctx["кат0"][did] = ds          # все входы: выигрыш — см. выигрыш()
            комп = sorted({str(сделки[d].get("COMPANY_ID") or "0") for d in п.без_кп} - {"0", ""}, key=int)
            создания = [c for c in (_дата(сделки[d].get("DATE_CREATE")) for d in п.без_кп
                                    if str(сделки[d].get("COMPANY_ID") or "0") in комп) if c]
            if not (комп and создания and п.учёт.можно("близнецы в пресейле", 2 + len(комп) // 10)):
                return
            # пресейл тех же компаний — только заведённый в окне карточек (ревью v59: без окна
            # список и его история у крупных клиентов шли на 60–150 запросов мимо сметы)
            фильтр = {"@COMPANY_ID": [int(c) for c in комп], "!CATEGORY_ID": 0,
                      ">=DATE_CREATE": (min(создания) - dt.timedelta(days=ОКНО_БЛИЗНЕЦА_ДН)).isoformat(),
                      "<=DATE_CREATE": (max(создания) + dt.timedelta(days=1)).isoformat()}
            пресейл = client.list_deals_fast(filter=фильтр, select=["ID", "COMPANY_ID", "CATEGORY_ID"],
                                             max_items=БЛИЗНЕЦОВ_МАКС)
            п.близнецы_обрезаны = len(пресейл) >= БЛИЗНЕЦОВ_МАКС
            пресейл = [p for p in пресейл if str(p.get("CATEGORY_ID") or "0") not in TEST_CATS | {"0"}]
            п.близнецов_искали = len(пресейл)
            if not (пресейл and п.учёт.можно("история близнецов", 2 * (len(пресейл) // 50 + 1))):
                return
            ист_п, ош = pc.читать_историю(client, [str(p["ID"]) for p in пресейл], стадии=п.предл_стадии)
            if ош:
                raise ОшибкаЧасти(ош)
            кп_комп = collections.defaultdict(list)
            for p in пресейл:
                for _s, t in ист_п.get(str(p["ID"]), []):
                    if _дата(t):
                        кп_комп[str(p.get("COMPANY_ID"))].append(_дата(t))
            for did in п.без_кп:
                c = _дата(сделки[did].get("DATE_CREATE"))
                кандидаты = [x for x in кп_комп.get(str(сделки[did].get("COMPANY_ID") or "0"), [])
                             if c and 0 <= (c - x).days <= pc.TWIN_DAYS]
                if кандидаты:
                    п.ctx["близнец"][did] = max(кандидаты)      # ближайшее к созданию карточки
    except ОшибкаЧасти as e:
        print(f"запасные даты: не прочитаны — {e}; такие сделки идут по дате создания")
    except Exception as e:  # noqa: BLE001
        print(f"запасные даты: сбой {type(e).__name__} · {место(e)}; такие сделки идут по дате создания")


def шаг_реквизиты(п: Прогон) -> None:
    """Страна покупателя по реквизитам: НДС зависит от покупателя, а не от валюты (C19)."""
    комп = sorted({str(п.сделки[d].get("COMPANY_ID") or "0") for d in п.прочитанные} - {"0", ""}, key=int)
    if комп and п.учёт.можно("реквизиты", 2 * (len(комп) // 50 + 1) + 1):
        try:
            with п.учёт.блок("реквизиты"):
                пресеты = {str(p.get("ID")): str(p.get("COUNTRY_ID") or "") for p in (
                    п.client.call("crm.requisite.preset.list", {"select": ["ID", "COUNTRY_ID"]}) or [])}
                п.страна = страна_по_реквизитам(
                    cn.реквизиты(п.client, комп, поля=("PRESET_ID", "RQ_INN")), пресеты)
        except Exception as e:  # noqa: BLE001
            п.страна = {}
            print(f"реквизиты: сбой {type(e).__name__} · {место(e)} — страна покупателя не определена")
    print(f"реквизиты покупателей: компаний {len(комп)} — " + счёт(collections.Counter(
        п.страна.get(c, "реквизитов нет") for c in комп), ("РФ", "не РФ", "реквизитов нет")))


def _связь_счёта(s: dict, заказ_сделка: dict, спец_сделка: dict) -> tuple[str, str]:
    """Счёт СП-137 → (сделка, способ связи): сделка, иначе заказ, иначе спецификация (B17)."""
    did = str(s.get("parentId2") or "0")
    if did not in ("", "0"):
        return did, "сделка"
    did = заказ_сделка.get(str(s.get("parentId172") or "0"), "0")
    if did not in ("", "0"):
        return did, "через заказ"
    if str(s.get("parentId132") or "0") not in ("", "0"):
        did = спец_сделка.get(f"132:{s.get('parentId132')}", "0")
        if did not in ("", "0"):
            return did, "через спецификацию"
    return "0", "только рамочный договор" if str(s.get("parentId175") or "0") not in ("", "0") else "без связи"


def шаг_счета_137(п: Прогон) -> None:
    """СП-137 «Счета на оплату» по >id с 2024-06-01: ₽ по ЦБ ÷ EUR ЦБ на дату заведения;
    общие на несколько сделок и внутригрупповые — не в сумме (B17, D23)."""
    client = п.client
    поля_ролей = (INV_RUB, INV_AMOUNT, INV_DATE, INV_PAYER, INV_PAYEE, INV_WHAT, INV_TYPE, INV_DEALS)
    try:
        with п.учёт.блок("СП-137"):
            поля = поля_сп(client, INVOICE_ENTITY)
            sel = ["id", "parentId2", "parentId172", "parentId132", "parentId175", "stageId", "createdTime",
                   "opportunity", "currencyId"] + [f for f in поля_ролей + (INV_ORDERS,) if f in поля]
            счета = client.list_items(INVOICE_ENTITY, filter={">=createdTime": SINCE_137}, select=sel)
            сем = client.spa_semantics(INVOICE_ENTITY, счета) if hasattr(client, "spa_semantics") else {}
        print(f"СП-137: прочитано {len(счета)} из {п.ждём_137}; поля найдены: "
              f"{sum(1 for f in поля_ролей if f in поля)} из {len(поля_ролей)}")
        if len(счета) < п.ждём_137:
            raise ОшибкаЧасти(f"обход СП-137 неполон: {len(счета)} из {п.ждём_137}")
        свои = {_имя_лица(v) for v in подписи_перечисления(поля.get(INV_PAYER)).values()}
        if not свои:
            свои = {_имя_лица(s.get(INV_PAYER)) for s in счета if isinstance(s.get(INV_PAYER), str)}
        свои.discard("")
        подп = {f: подписи_перечисления(поля.get(f)) for f in (INV_WHAT, INV_TYPE)}
        заказ_сделка = {str(o.get("id")): str(o.get("parentId2") or "0") for o in п.заказы_все}
        оплаты: dict[str, list] = {}
        for s in счета:
            st = str(s.get("stageId") or "")
            sem = сем.get(st) or ("F" if st.endswith(":FAIL") else "S" if st.endswith(":SUCCESS") else "P")
            п.sem137[sem] += 1
            rub = _f(s.get(INV_RUB))
            п.оплаты_счёт["₽ по ЦБ не ноль"] += bool(rub and rub > 0)
            п.оплаты_счёт["«Сумма оплаты» не ноль"] += bool(_f(s.get(INV_AMOUNT)))
            if sem == "F":
                continue
            did, как = _связь_счёта(s, заказ_сделка, п.спец_сделка)
            п.оплаты_счёт[f"связь: {как}"] += 1
            if did not in п.прочитанные:
                continue
            if len(номера_сделок(s.get(INV_DEALS))) > 1:
                п.оплаты_счёт["общие на несколько сделок (не в сумме)"] += 1
                continue
            if свои and свой_получатель(s.get(INV_PAYEE), свои):
                п.оплаты_счёт["внутригрупповые (не в сумме)"] += 1
                continue
            п.виды[вид_платежа(*(подп[f].get(str(s.get(f)), "") for f in (INV_TYPE, INV_WHAT)))] += 1
            # «₽ по ЦБ» — по курсу на дату ЗАВЕДЕНИЯ процесса (подпись поля): ÷ EUR на эту же
            # дату; «Дата счета» — запасная и сверка разноса дат (ревью v59)
            заведён, дата_сч = _дата(s.get("createdTime")), _дата(s.get(INV_DATE))
            п.оплаты_счёт["дата заведения есть" if заведён else "даты заведения нет — дата счёта"] += 1
            if заведён and дата_сч and abs((дата_сч - заведён).days) > РАЗНОС_ДАТ_СЧЁТА_ДН:
                п.оплаты_счёт[f"«Дата счета» дальше {РАЗНОС_ДАТ_СЧЁТА_ДН} дн. от заведения"] += 1
            if rub and rub > 0:
                п.оплаты_счёт["оплачен (S)" if sem == "S" else "в работе (P)"] += 1
                оплаты.setdefault(did, []).append((rub, заведён or дата_сч, sem))
        п.ctx["оплаты"] = оплаты
    except ОшибкаЧасти as e:
        print(f"СП-137: не учтён — {e}")
    except Exception as e:  # noqa: BLE001
        print(f"СП-137: не учтён — {type(e).__name__} · {место(e)}")
    print(f"  стадии по семантике: {счёт(п.sem137, ('S', 'P', 'F'))}; {счёт(п.оплаты_счёт)}; "
          f"сделок выборки со счетами {len(п.ctx['оплаты'])}; вид платежа: {счёт(п.виды)}")


def шаг_прочие(п: Прогон) -> None:
    """Строки заказов, оплаты, СП-1074/1136, предложения, старые счета — если в смете."""
    client, учёт = п.client, п.учёт
    п.живые_u = [str(o.get("id")) for did in п.прочитанные for o in п.ctx["заказы"].get(did, [])]
    if п.живые_u and учёт.можно("строки заказов", len(п.живые_u) // 50 + 6):
        try:
            import publish_deals
            with учёт.блок("строки заказов"):
                п.строки_зак = publish_deals.строки_заказов(client, п.живые_u)
        except Exception as e:  # noqa: BLE001
            print(f"строки заказов: сбой {type(e).__name__} · {место(e)}")
    if учёт.можно("оплаты", PAY_SAMPLE):
        with учёт.блок("оплаты"):
            выборка = [d for d in sorted(п.прочитанные, key=int) if п.сег[d] in ("U2", "U3")][:PAY_SAMPLE]
            for i, did in enumerate(выборка, 1):
                try:
                    res = client.call("crm.item.payment.list", {"entityTypeId": 2, "entityId": int(did)}) or []
                except Exception as e:  # noqa: BLE001
                    п.оплаты_кл[f"ошибка {type(e).__name__}"] += 1
                    break
                п.оплаты_кл["есть" if res else "пусто"] += 1
                if i >= PAY_STOP_EMPTY and not п.оплаты_кл["есть"]:
                    break                     # пять пустых подряд с начала — дальше не тратим (G41)
    if учёт.можно("прочие источники", 6):
        with учёт.блок("прочие источники"):
            for ent in (1074, 1136):
                try:
                    поля = поля_сп(client, ent)
                    n = _total(client, "crm.item.list", {"entityTypeId": ent, "filter": {}, "select": ["id"]})
                    курсовые = sum(1 for f in поля.values()
                                   if re.search(r"курс|rate|валют|currency", str(f.get("title") or ""), re.I))
                    п.прочие.append(f"СП-{ent}: записей {n}, полей {len(поля)}, «курсовых» по подписи {курсовые}")
                except Exception as e:  # noqa: BLE001
                    п.прочие.append(f"СП-{ent}: {type(e).__name__}")
            for метод, имя in (("crm.quote.list", "предложения crm.quote"),
                               ("crm.invoice.list", "старые счета crm.invoice")):
                try:
                    п.прочие.append(f"{имя}: записей {_total(client, метод, {'select': ['ID'], 'filter': {}})}")
                except Exception as e:  # noqa: BLE001
                    п.прочие.append(f"{имя}: недоступно ({type(e).__name__})")


def шаг_бюджеты(п: Прогон) -> None:
    """Бюджеты сделок — той же привязкой, что вкладка (contracts._load_budget_plans)."""
    by_seq, by_id, п.plan_asof = ct._load_budget_plans()
    for did in п.прочитанные:
        # номер — из названия сделки, иначе из названия первого живого заказа, как
        # contracts.compute (:377): сделки с номером только у заказов иначе теряли бюджет
        заказы = п.ctx["заказы"].get(did) or []
        seq = ct._regno(п.сделки[did].get("TITLE"), заказы[0].get("title") if заказы else "")
        plan = (by_seq.get(seq) if seq else None) or by_id.get(did)
        if plan:
            п.ctx["бюджет"][did] = plan
            п.книга_сделок[str(plan.get("drive_id") or id(plan))] += 1
            for ключ in ("purchase_net", "purchase"):
                v = _f(plan.get(ключ))
                if v:
                    п.знак_закупки["минус" if v < 0 else "плюс"] += 1
                    break


def _нужные_даты_и_валюты(п: Прогон) -> tuple[list[dt.date], set[str]]:
    """Даты и валюты, на которые нужен курс. Валюта — только трёхбуквенным кодом:
    прочее (текст money-поля после «|») — счётчиком п.нестандартных_валют, не текстом."""
    даты: list[dt.date] = []
    сырые: list = ["EUR", "USD", "AED"]
    ctx = п.ctx
    for did in п.прочитанные:
        d = п.сделки[did]
        kp, _ = дата_ткп(d, sorted(ctx["предл"].get(did, [])), ctx["кат0"].get(did), ctx["близнец"].get(did))
        даты += [x for x in (kp, min(ctx["предл"][did]) if ctx["предл"].get(did) else None,
                             _дата(d.get(KP_STAGE_DATE))) if x]
        сырые.append(d.get("CURRENCY_ID"))
        for o in ctx["заказы"].get(did, []):
            сырые.append(o.get("currencyId"))
            даты += [x for x in (_дата(o.get("createdTime")),) if x]
        сырые += [c for _a, c, _s in ctx["спец"].get(did, [])]
        сырые += [c for _a, c in ctx["сч31"].get(did, [])]
        for f in (ct.BUY_ECON_RUB, ct.BUY_ECON_VAL):
            кл, _v, c = pm._поле(d.get(f))
            if кл == "сумма":
                сырые.append(c)
        даты += [day for _rub, day, _sem in ctx["оплаты"].get(did, []) if day]
    валюты = {код_валюты(c) for c in сырые if str(c or "").strip()}
    п.нестандартных_валют = sum(1 for c in сырые if str(c or "").strip() and not код_валюты(c))
    return даты, валюты - {None, "RUB"}


def _ряды_зеркала(п: Прогон, валюты: set[str]) -> tuple[dict, list[str]]:
    """Курсы ЦБ с зеркала на нужные даты и сегодня (запрос на дату, от новых к старым)."""
    try:
        ряды, стат = fx.cbr_mirror_series(set(п.даты) | {п.сегодня}, валюты | {"EUR"},
                                          max_requests=ЗЕРКАЛО_ЗАПРОСОВ)
    except Exception as e:  # noqa: BLE001
        print(f"зеркало ЦБ: сбой {type(e).__name__} · {место(e)}")
        return {}, []
    print(f"курсы ЦБ по зеркалу: нужных дат {стат['дат']}, запросов {стат['запросов']} (предел "
          f"{ЗЕРКАЛО_ЗАПРОСОВ}), без курса {стат['без курса']}, не прочитано из-за предела {стат['обрезано']}; окно "
          f"курса между датами — только по прочитанным датам (уже, чем по ряду)")
    return ряды, [f"{c} — нет у зеркала" for c in sorted(валюты | {"EUR"}) if c not in ряды]


def шаг_курсы(п: Прогон) -> None:
    """Ряды ЦБ одним запросом на валюту, с запасом до самой ранней даты (A11); cbr.ru не
    ответил — зеркало на нужные даты (A12); сверка с ЕЦБ (A12)."""
    п.даты, валюты = _нужные_даты_и_валюты(п)
    if п.нестандартных_валют:
        print(f"нестандартных кодов валют в суммах выборки: {п.нестандартных_валют} — курс для них не ищется")
    if not п.даты:
        return
    d1, d2 = min(п.даты) - dt.timedelta(days=CBR_LEAD_DAYS), п.сегодня
    ряды, нет = {}, []
    if п.коды_цб is not None:
        for c in sorted(валюты):
            sid = п.коды_цб.get(c)
            if not sid:
                нет.append(f"{c} — нет в справочнике ЦБ")
                continue
            try:
                ряды[c] = fx.cbr_series(sid, d1, d2)
            except Exception as e:  # noqa: BLE001
                нет.append(f"{c} — {type(e).__name__}")
        if not ряды.get("EUR"):
            print(f"ряд ЦБ EUR не получен ({', '.join(нет) or 'пусто'})")
            п.зеркало = п.зеркало or _проверить_зеркало()
    if not ряды.get("EUR") and п.зеркало == "да":
        ряды, нет = _ряды_зеркала(п, валюты)
        п.источник_цб = "зеркало"
    if not ряды.get("EUR"):
        print("курса ЦБ к евро нет — колонки «по ЦБ» не считаются, пересчёт 1:1 не подставляется")
        return
    п.К = fx.CbrRates(ряды)
    п.ctx["цб"] = True
    if п.источник_цб == "ЦБ":
        print(f"ряды ЦБ (с запасом {CBR_LEAD_DAYS} дн. до самой ранней нужной даты): "
              + " · ".join(f"{c} {len(v)} записей" for c, v in sorted(ряды.items()))
              + (f"; без ряда: {', '.join(нет)}" if нет else ""))
    elif нет:
        print(f"  без курса у зеркала: {', '.join(нет)}")
    try:
        ецб = fx.ecb_history()
    except Exception as e:  # noqa: BLE001
        п.ецб.append(f"ЕЦБ недоступен ({type(e).__name__})")
        return
    # у зеркала записи только на прочитанные даты: сверка — ровно на них, иначе курс
    # «последней записи не позже даты» отстоял бы от даты ЕЦБ на недели
    дни_зеркала = set(п.К.days("EUR")) if п.источник_цб == "зеркало" else None
    for c in sorted(валюты - {"EUR"}):
        src = ецб.get(c) or ([(d, v * fx.AED_USD) for d, v in ецб.get("USD", [])] if c == "AED" else None)
        откл = []
        for d, v in src or []:
            if дни_зеркала is not None and d not in дни_зеркала:
                continue
            x = п.К.cross(c, "EUR", d) if min(п.даты) <= d <= d2 else None
            if x:
                откл.append(abs(math.log(x / v)))
        if откл:
            п.ецб.append(f"{c} {len(откл)} дат: медиана {100 * statistics.median(откл):.1f} %, больше 2 % — "
                         f"{пр(sum(1 for x in откл if x > math.log(1.02)), len(откл))}")


# ── отчёт ───────────────────────────────────────────────────────────────────

def _отчёт_курсы(п: Прогон) -> None:
    БХ, К = п.БХ, п.К
    print("\nКУРСЫ")
    нестанд = sum(1 for c in БХ.codes() if not код_валюты(c))
    for c in sorted(c for c in БХ.codes() - {БХ.база} if код_валюты(c)):
        bx = БХ.cross(c, "EUR")
        if not bx:
            print(f"  {c}: в Битриксе курс 0 или пуст (правлен {БХ.даты.get(c) or '—'})")
            continue
        cb = К.cross(c, "EUR", п.сегодня) if п.ctx["цб"] else None
        окно = К.cross_range(c, "EUR", min(п.даты), п.сегодня) if (п.ctx["цб"] and п.даты) else None
        print(f"  {c}: Битрикс 1 EUR = {bx:.4g} (правлен {БХ.даты.get(c) or '—'})"
              + (f" · ЦБ сегодня {cb:.4g} ({100 * (bx / cb - 1):+.1f} %)" if cb and bx else "")
              + (f" · ЦБ за период {окно[0]:.4g}–{окно[1]:.4g}" if окно else ""))
    print(f"  база портала {вал_подпись(БХ.база)}" + (f"; нестандартных кодов в справочнике {нестанд}" if нестанд else "")
          + (f"; курсы ЦБ — {п.источник_цб}" if п.ctx["цб"] else "")
          + (f"; сверка ЦБ с ЕЦБ (кросс к евро): {'; '.join(п.ецб)}" if п.ецб else ""))


def _отчёт_даты(п: Прогон, все: list[dict]) -> None:
    предл = п.ctx["предл"]
    print("\nДАТА КУРСА (решение 25.10: итоговое ТКП)")
    print("  источник: " + счёт(collections.Counter(r["kp_src"] for r in все),
                                ("ТКП согласован", "последнее КП до выигрыша", "КП близнеца в пресейле",
                                 "вход в кат. 0", "создание сделки")))
    с_полем = [r for r in все if r["kp_src"] == "ТКП согласован"]
    дни = [(r["kp"] - min(предл[r["id"]])).days for r in с_полем if предл.get(r["id"])]
    после = sum(1 for r in с_полем if any(x > r["kp"] for x in предл.get(r["id"], [])))
    нижн = [r for r in все if _дата(r["_d"].get(KP_STAGE_DATE))]
    ширина = [(r["hi"] - r["lo"]).days for r in все if r["lo"]]
    у_создания = sum(1 for r in с_полем if _дата(r["_d"].get("DATE_CREATE"))
                     and abs((r["kp"] - _дата(r["_d"].get("DATE_CREATE"))).days) <= ТКП_У_СОЗДАНИЯ_ДН)
    print(f"  «ТКП согласован» {доля(len(с_полем), len(все))}, из них в день создания сделки "
          f"(±{ТКП_У_СОЗДАНИЯ_ДН} дн., похоже на умолчание) {доля(у_создания, len(с_полем))}; со входом в стадию предложения "
          f"{доля(sum(1 for r in все if предл.get(r['id'])), len(все))}; первое КП → ТКП согласован: "
          f"медиана {statistics.median(дни) if дни else '—'} дн. (по {len(дни)}), согласован раньше первого КП "
          f"{sum(1 for x in дни if x < 0)}; вход в стадию предложения ПОСЛЕ согласования {после}")
    print(f"  нижняя граница {KP_STAGE_DATE}: {доля(len(нижн), len(все))}, позже итоговой даты "
          f"{sum(1 for r in нижн if _дата(r['_d'].get(KP_STAGE_DATE)) > r['kp'])}; ширина окна курса: медиана "
          f"{statistics.median(ширина) if ширина else '—'} дн.; без «ТКП согласован» {len(п.без_поля)}, из них без "
          f"входа в стадию предложения (карточка только реализации) {len(п.без_кп)}, близнец найден "
          f"{len(п.ctx['близнец'])} (пресейл тех же компаний, заведённый не раньше {ОКНО_БЛИЗНЕЦА_ДН} дн. до карточки, — "
          f"{п.близнецов_искали} сделок{f'; список обрезан на {БЛИЗНЕЦОВ_МАКС}' if п.близнецы_обрезаны else ''})")
    u3 = [r for r in все if r["seg"] == "U3"]
    сходится = 0
    for r in u3:
        cd, s = _дата(r["_d"].get("CLOSEDATE")), п.s_вход.get(r["id"])
        сходится += bool(cd and s and abs((cd - s).days) <= 7)
    print(f"  U3: вход в стадию успеха по истории {доля(sum(1 for r in u3 if r['id'] in п.s_вход), len(u3))}; "
          f"CLOSEDATE (плановая) в пределах 7 дн. от него {сходится}")


def _отчёт_поля(все: list[dict]) -> None:
    print("\nПОЛЯ СДЕЛКИ (заполненность по выборке)")
    oa = [r for r in все if r["SB"] and _f(r["_d"].get("OPPORTUNITY_ACCOUNT"))]
    ручные = sum(1 for r in все if str(r["_d"].get("IS_MANUAL_OPPORTUNITY")) == "Y")
    сходятся = sum(1 for r in oa if _около(_f(r["_d"]["OPPORTUNITY_ACCOUNT"]), r["SB"], 0.01))
    учёта = collections.Counter(str(r["_d"].get("ACCOUNT_CURRENCY_ID") or "пусто") for r in все)
    print(f"  TAX_VALUE не ноль {sum(1 for r in все if _f(r['_d'].get('TAX_VALUE')))} · ручная сумма "
          f"(IS_MANUAL_OPPORTUNITY=Y) {доля(ручные, len(все))} · OPPORTUNITY_ACCOUNT ≈ SB (±1 %) "
          f"{доля(сходятся, len(oa))} (валюта учёта: {счёт(учёта)})")
    поля = ((OFFER_FILE, "«Offer from us» (файл)"), (ECON_FILE_BX, "«Economics of the project» (файл)"),
            (ct.ECON_LINK, "ссылка на файл экономики"), (ct.BUDGET_LINK, "ссылка на бюджет"),
            (PAID_DATE, "«Счёт оплачен (дата)»"), (ct.ECON_PAID, "«Оплачено»"), (ct.ECON_REST, "«Остаток»"),
            (ct.ECON_MARGIN, "«Margin»"))
    print("  " + " · ".join(f"{имя} {доля(sum(1 for r in все if ct._has(r['_d'].get(f))), len(все))}"
                            for f, имя in поля))
    print("  валюта сделки: " + счёт(collections.Counter(вал_подпись(r["cur"]) for r in все)) + "; файл экономики зонд не "
          "скачивает (предел 30 в сутки ведёт читатель) — опоры «файл» нет, она в v59b")


def _отчёт_источники(п: Прогон, все: list[dict]) -> None:
    ctx = п.ctx
    print("\nИСТОЧНИКИ (у скольких сделок сумма есть)")
    for ключи in (("SB", "SC", "SR_net", "SBud_net", "SSpec", "SInv"), ("BO", "BCost", "BOffer", "BBud_net", "BPaid", "BPaid_done")):
        print("  " + " · ".join(f"{k} {sum(1 for r in все if r.get(k))}" for k in ключи))
    sr = [ctx["строки"][r["id"]] for r in все if ctx["строки"].get(r["id"])]
    ставки = sum((x["rates"] for x in sr), collections.Counter())
    print(f"  товарные строки сделок: есть у {доля(len(sr), len(все))}; строк с TAX_INCLUDED=Y "
          f"{пр(sum(x['incl'] for x in sr), sum(x['n'] for x in sr))}; ставка: {счёт(ставки)}")
    спец = collections.Counter({f"СП-{e} {a} ({b})": n for (e, a, b), n in п.спец_счёт.items()})
    print(f"  спецификации: {счёт(спец)}; сделок с несколькими спецификациями заказчика "
          f"{sum(1 for r in все if r['n_spec'] > 1)}")
    print(f"  бюджеты: снимок от {п.plan_asof or '—'}, привязано {sum(1 for r in все if r['bud'])}; одна книга у "
          f"нескольких сделок {sum(1 for n in п.книга_сделок.values() if n > 1)} книг (двойной счёт плана); "
          f"закупка в книге со знаком: {счёт(п.знак_закупки, ('минус', 'плюс'))} (берётся по модулю, как шаблон)")
    print(f"  заказы сделок выборки: неполные (есть живой без суммы) "
          f"{sum(1 for r in все if r['n_orders'] and r['bo_nosum'])} · с минусом {sum(1 for r in все if r['bo_neg'])}"
          f" · вероятный дубль {sum(1 for r in все if r['bo_dup'])} · без курса {sum(1 for r in все if r['bo_nofx'])}"
          f"; счета СП-137 без курса хоть у одного (сумма не считается) {sum(1 for r in все if r['bpaid_nofx'])}")
    if п.строки_зак:
        своды = {k: свод_строк(v) for k, v in п.строки_зак.items() if v}
        по_заказу = {str(o.get("id")): o for did in п.прочитанные for o in ctx["заказы"].get(did, [])}
        кн = collections.Counter(корзина_пары(x["net"], _f(по_заказу[k].get("opportunity"))) for k, x in своды.items())
        кг = collections.Counter(корзина_пары(x["gross"], _f(по_заказу[k].get("opportunity"))) for k, x in своды.items())
        print(f"  строки заказов: есть у {доля(len(своды), len(п.живые_u))}; к сумме заказа без НДС "
              f"{счёт(кн, ПОРЯДОК_КОРЗИН)}; с НДС {счёт(кг, ПОРЯДОК_КОРЗИН)}; taxIncluded=Y "
              f"{пр(sum(x['incl'] for x in своды.values()), sum(x['n'] for x in своды.values()))}")
    print(f"  оплаты crm.item.payment.list (U2/U3): {счёт(п.оплаты_кл)}; счета клиенту СП-31 в портале {п.ждём_31}")
    for s in п.прочие:
        print(f"  {s}")


def _отчёт_сравнения(п: Прогон, все: list[dict], метка: str) -> None:
    rng = random.Random(59)
    for заголовок, пары in ((f"СРАВНЕНИЯ ПРОДАЖИ (€ — {метка}; r = первая / вторая)", ПАРЫ_ПРОДАЖИ),
                            ("СРАВНЕНИЯ ЗАКУПКИ", ПАРЫ_ЗАКУПКИ)):
        print(("\n" if пары is ПАРЫ_ПРОДАЖИ else "") + заголовок)
        for a, b, имя, нужен_цб in пары:
            if not нужен_цб or п.ctx["цб"]:
                print(строка_пары(a, b, имя, разбор_пары(все, a, b, rng)))
    for имя, признак in (("валюте заказов (C21)", lambda o: вал_подпись(o.get("currencyId"))),
                         ("воронке заказов (D23)", lambda o: "воронка " + воронка_заказа(o.get("stageId")))):
        группы = collections.defaultdict(list)
        for r in все:
            знач = {признак(o) for o in r["_orders"]}
            группы[next(iter(знач)) if len(знач) == 1 else "смешанная"].append(r)
        части = []
        for c, рр in sorted(группы.items()):
            р = разбор_пары(рр, "BO_kp", "BBud_net", rng)
            if р["n"]:
                части.append(f"{c}: n {р['n']}, ≤5 % {р['корз'].get('≤5 %', 0)}, НДС {р['объясн'].get('НДС', 0)}")
        print(f"  заказы против бюджета без НДС по {имя}: " + ("; ".join(части) or "—"))
    отн = collections.Counter(pm._корзина(r["BO"] / r["SC"]) for r in все if r["BO"] and r["SC"] and r["BO"] > 0)
    print(f"  закупка/продажа (BO/SC, грубо, как v55): {' · '.join(f'{n} {отн[n]}' for _lo, _hi, n in pm.КОРЗИНЫ)}")
    print("\nГИПОТЕЗА ВЛАДЕЛЬЦА: число в AED под валютой EUR")
    for ключ in ("SB", "SC"):
        n, m, по = гипотеза_aed(все, п.К, ключ)
        print(f"  EUR-сделок, у которых {ключ} к лучшей независимой опоре (спецификация > бюджет > строки) "
              f"в полосе курса AED/EUR на окне дат ТКП: {n} из {m}" + (f" ({счёт(по)})" if n else ""))
    print("\nНДС: «Сумма» против бюджета с НДС и без (RUB — в рублях, прочие — по ЦБ; ±2 %)")
    for (c, стр), cnt in sorted(ндс_по_валютам(все, п.страна).items()):
        print(f"  {c} · покупатель {стр}: {счёт(cnt)}")


def _отчёт_тень(п: Прогон, все: list[dict]) -> None:
    u1 = [r for r in все if r["seg"] == "U1"]
    print(f"\nТЕНЬ КАСКАДА ПО U1 ({len(u1)} сделок; без файла экономики)")
    if not п.полно:
        print(f"  частей прочитано {п.частей_всего - п.упавшие} из {п.частей_всего} — изменения Σ и сверка итогов "
              f"не печатаются (доля по прочитанным частям смещена)")
    т = тень(u1, правила_вкладки(u1, п.БХ, все))
    print(f"  продажа: {счёт(т['ист_п'], ('Битрикс по ЦБ', 'под сомнением', 'бюджет', 'суммы нет'))}")
    print("  закупка: " + счёт(т["ист_з"], ("заказы по ЦБ", "заказы неполные", "заказы с дублем",
                                         "заказы вне коридора", "бюджет", "нет")))
    print(f"  показанная продажа против SB: {счёт(т['сдвиг'])}; относительно независимой опоры "
          f"(спецификация > бюджет): {счёт(т['хуже'])}")
    if not п.полно:
        return

    def пц(x):
        return "—" if x is None else f"{x:+.1f} %"
    print(f"  Σ продажи U1 {пц(т['Δпрод'])}; Σ закупки {пц(т['Δзак'])}; сделок в Σ маржи было {т['маржа_было']}, "
          f"стало {т['маржа_стало']} (валовая из Битрикса и заказов; план бюджета — отдельно, {т['план']})")
    с = т["сейчас"]
    print(f"  сверка итогов по нынешним правилам (contracts.py:865-870): в Σ продажи {с['S']}, в Σ закупки {с['B']}, "
          f"в Σ маржи {с['M']}; продажа∩закупка {с['SB']}, во всех трёх {с['SBM']}; "
          f"(Σпродажи − Σзакупки − Σмаржи) / Σпродажи = {пц(с['невязка'])}")
    if т["тень_итоги"]:
        о = т["тень_итоги"]
        print(f"  сверка итогов тени (те же множества): в Σ продажи {о['S']}, в Σ закупки {о['B']}, в Σ маржи "
              f"{о['M']}; продажа∩закупка {о['SB']}, во всех трёх {о['SBM']}; невязка {пц(о['невязка'])}; "
              f"на одном множестве все три Σ шли бы по {о['SBM']} сделкам")


def отчёт(п: Прогон) -> None:
    все = []
    for did in sorted(п.прочитанные, key=int):
        r = запись_сделки(did, п.сделки[did], п.сег[did], п.ctx, п.К, п.БХ, п.сегодня)
        r["_d"], r["_orders"] = п.сделки[did], п.ctx["заказы"].get(did, [])
        все.append(r)
    метка = (("ЦБ на дату ТКП" + (" (зеркало)" if п.источник_цб == "зеркало" else "")) if п.ctx["цб"]
             else "курс Битрикса (ЦБ недоступен)")
    print(f"\nВЫБОРКА В ВЫВОДАХ: {счёт(collections.Counter(r['seg'] for r in все), ('U1', 'U2', 'U3'))}; € — {метка}")
    _отчёт_курсы(п)
    _отчёт_даты(п, все)
    _отчёт_поля(все)
    _отчёт_источники(п, все)
    _отчёт_сравнения(п, все, метка)
    _отчёт_тень(п, все)


def main(client=None, сегодня: dt.date | None = None) -> int:
    п = Прогон(client, сегодня or dt.datetime.now(pc.MSK).date())
    шаг_цб(п)                                   # первой строкой: доступен ли ЦБ с раннера
    if п.client is None:
        import config
        п.client = bc.BitrixClient(config.Settings.load().bitrix_webhook_url)
    if not шаг_основа(п) or not шаг_заказы(п):
        print(bc.сводка_нагрузки())
        return 1
    шаг_части(п)
    шаг_запасные_даты(п)
    шаг_реквизиты(п)
    шаг_счета_137(п)
    шаг_прочие(п)
    шаг_бюджеты(п)
    шаг_курсы(п)
    отчёт(п)
    print("\nНАГРУЗКА ПО БЛОКАМ: " + " · ".join(f"{k} {v}" for k, v in п.учёт.по_блокам.items())
          + f" · смета была {п.смета}")
    print(bc.сводка_нагрузки())
    return 0 if п.полно else 1


if __name__ == "__main__":
    # Трассировка с содержимым сделки в публичный журнал не уходит: печатаются
    # только тип ошибки и места в коде (файл:строка), без сообщения (критика F36).
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"::error::зонд упал: {type(e).__name__} · {место(e)}")
        print(bc.сводка_нагрузки())
        raise SystemExit(1)
