"""Вкладка «Реализация» — сделки ВОРОНКИ РЕАЛИЗАЦИИ (категория 0) + их заказы поставщикам.

Вселенная: все сделки в воронке реализации (кат. 0, номера «N. …» до текущего максимума),
плюс сделки с заказами СП-172, ещё числящиеся в других воронках (помечаются «вне воронки»).

Модель данных Bitrix:
  • СП-172 «Заказы» = заказы ПОСТАВЩИКАМ → их opportunity = ЗАКУПКА, companyId = поставщик.
  • Продажа клиенту и сам клиент — на родительской СДЕЛКЕ (parentId2).
  • ФИНАЛЬНАЯ ЭКОНОМИКА: сверенный файл «Экономика проекта» (свод dealctx, ctx_econ) — первым:
    продажа и закупка в евро по курсам файла; «Сумма» и заказы — сверка (решение владельца
    25.10.2026). «Сумму» меняли после чтения файла — до перечтения Битрикс (dealctx.econ_stale).
  • КУРС (решение владельца 25.10.2026): «Сумма» → € по ЦБ РФ на дату итогового ТКП
    («ТКП согласован», запасные — последнее КП до выигрыша, вход в кат. 0, создание сделки;
    _kp_date), заказы — по ЦБ на дату заказа, бюджет — на дату книги. ЦБ и зеркало недоступны —
    курсы Битрикса с плашкой и счётом (head «fx»), 1:1 не подставляется никогда.
  • НДС (решение владельца 26.10.2026): продажа без сверенного файла — без НДС по правилу:
    покупатель из РФ или без реквизитов — «Сумма» ÷ 1,20 (ТКП до 2026) или ÷ 1,22 (с 2026),
    иностранный — как есть (vatRule строки). Продажа файла уже без НДС — не трогается.
  • ЗАКУПКА — Σ заказов СП-172, иначе «нет данных». Money-поля сделки из экономики проекта
    («Cost (rub)» / «Offer sum … of selected supplier», см. BUY_*) — только сверка (discrep):
    итерация расчёта в них не проверена (ревизия 25.10.2026). Маржа считается ТОЛЬКО при
    закупке из заказов (никаких фиктивных 100%).
  • ИТОГИ: Σ продажи — открытые сделки с известной суммой; Σ закупки и Σ маржи — одно
    множество (сделки с маржой), и Σ продажи этого множества − Σ закупки = Σ маржи.
  • СКОРОСТЬ — по crm.stagehistory (первый вход в каждую стадию, полное время):
    тайминг стадий сделки в кат.0, лаг заведения первого заказа, стадии заказов СП-172.
  • ЗАМЕДЛЕННОСТЬ: возраст текущей стадии > max(2×медиана по этой стадии, SLOW_MIN_DAYS).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from collections import Counter, defaultdict

import chat_activity
import dealctx
import lifecycle
import ork
import orkview
import people as people_mod
import period as period_mod
from base import fx as fx_mod
import bitrix_client
from bitrix_client import BitrixClient

_SYM = {"EUR": "€", "USD": "$", "CNY": "¥", "RUB": "₽", "INR": "₹", "GBP": "£", "AED": "AED ", "JPY": "¥"}

# money-поля сделки, продублированные из «экономики проекта» (см. зонд crm.deal.fields)
BUY_ECON_RUB = "UF_CRM_1710446284794"   # «Cost (rub)» — себестоимость в рублях
BUY_ECON_VAL = "UF_CRM_1704981063967"   # «Offer sum and currency of selected supplier»
ECON_MARGIN = "UF_CRM_1704981084196"    # «Margin»
ECON_PAID = "UF_CRM_1713874110281"      # «Оплачено»
ECON_REST = "UF_CRM_1713874579940"      # «Остаток к оплате»
ECON_LINK = "UF_CRM_1740133235324"      # ссылка (clck.ru) на файл «Экономика проекта» в Я.Диске
BUDGET_LINK = "UF_CRM_1577100780"       # «Transaction budget + product conditions» — бюджет
                                        # сделки в Google Sheets: единственное место, где
                                        # может лежать ПОЛНАЯ себестоимость (логистика,
                                        # таможня, НДС). Заполнен у ~90% открытых сделок.

# плановые даты заказа СП-172 (см. зонд v9: поле дедлайна подтверждено на 72/72 строк
# выгрузки «Контроль дедлайнов» — коллеги контролируют именно его)
DL_CUSTOMER = "ufCrm20_1728900218435"   # «Date of deadline to customer» — обещано клиенту
PROD_END = "ufCrm20_1724941935"         # «Production end date, budget» — плановый конец производства
PROD_START = "ufCrm20_1724941908"       # «Production start date, budget» — обязательна при размещении
INBOUND_PLAN = "ufCrm20_1709294315471"  # «Inbound Delivery Date, planned» — плановое поступление
CITY_F = "ufCrm20_1742552190053"        # «Город доставки заказчику по бюджету»
OSS_F = "ufCrm20_1723236618"            # «Deal support specialist» — ОСС заказа (совпал 73/74)
OSS_DEAL = "UF_CRM_1715604558"          # «Сопровождение сделки» — тот же человек на сделке (74/74)
KAM_F = "UF_CRM_1740390857"             # «КАМ» — КАМ клиента (people.py); ответственный сделки — не он
KAM_OLD = "UF_CRM_1736926032"           # «KAM (старое)» — запасное, как в people.py
# факты исполнения заказа (зонд v49: заполнены у 85–100 % заказов, прошедших точку) —
# жизненный цикл «размещение → производство → в пути → закрытие → гарантия» (lifecycle.py)
SHIP_PLAN = "ufCrm20_1723235828"        # «Supplier Shipment Date, planned»
SHIP_FACT = "ufCrm20_1723236324"        # «Supplier Shipment Date, actual»
INB_FACT = "ufCrm20_1723236306"         # «Inbound Delivery Date, actual» — пришло на склад
DEL_PLAN = "ufCrm20_1723236261"         # «Customer delivery date, planned»
DEL_BUDGET = "ufCrm20_1724942005"       # «Customer delivery date, budget»
DEL_FACT = "ufCrm20_1723236501"         # «Customer delivery date, actual» — доставлено клиенту
UTD_DATE = "ufCrm20_1723236848"         # «Date of signing of the Universal Transfer Document (UTD)»
UTD_FILE = "ufCrm20_1749654314976"      # «Universal Transfer Document (UTD)» — файл
PAY_PLAN = "ufCrm20_1723236853"         # «Planned payment term» — когда клиент должен заплатить
CLIENT_PO = "ufCrm20_1751555578101"     # «Payment Order (client)» — платёжка клиента, файл
SUP_PAID = "ufCrm20_1755759056275"      # «Оплачено поставщику, %»
PROD_CONF = "ufCrm20_1723236426"        # «Production confirmation» — файл
PROD_CONF_NF = "ufCrm20_1723372033545"  # «Production confirmation, no file» — флажок
SCHEME_F = "ufCrm20_1724941500"         # «Схема поставки»
# доход КАМов и продуктов (kaminc.py): дата выигрыша и отсрочка оплаты клиента — поля сделки
# (зонд v49: «ТКП согласован (конкурс выигран)» заполнено у 96 % открытых сделок реализации,
# «Количество дней отсрочки платежа» — у 83–84 %)
WON_F = "UF_CRM_1720337258485"          # «ТКП согласован (конкурс выигран)» — дата выигрыша
PAY_DAYS_F = "UF_CRM_1727962588"        # «Количество дней отсрочки платежа» клиента
# регламент ОРК (ork.py, решения владельца 26.10.2026): артефакты шагов (ork.ARTIFACTS — коды
# полей там), КАМ и направление поставки заказа, вторая опора приёмки, доля первичной оплаты;
# менеджер ОРК на сделке. Поля — в те же выборки, новых запросов нет
KAM_ORDER = "ufCrm20_1781171449"        # «КАМ» на заказе (78 %, карта B §E) — сверка с полем сделки
DIRECTION_F = "ufCrm20_1769424743906"   # «Направление поставки» (2 значения, подписи не сверены)
ASSIGN_AT = "ufCrm20_1787924508120"     # «Дата записи нового ответственного» — вторая опора S1 (с 28.08.2026)
PREPAY_F = "ufCrm20_1775138654290"      # «Доля первичной оплаты, %» (с 02.04.2026)
ORK_RESP = "UF_CRM_1781858627"          # «Ответственный за реализацию» — менеджер ОРК на сделке (33 %)
ART_FIELDS = sorted({a["field"] for a in ork.ARTIFACTS})

PAID_RX = re.compile(r"оплата\s+получена", re.I)   # стадия «Оплата получена | Закрытие сделки»
# Отдельные направления (решение владельца 25.10.2026): сложности их сделок известны, ими
# занимаются, ежедневного внимания руководителя реализации они не требуют. Сделка — в
# направлении, если она в его воронке сделок или её компания — компания сделок этой
# воронки (сделки реализации того же клиента). Такие сделки идут на свою вкладку
# «Реализации», а в очередь «что делать», плитки и итоги основного вида не попадают.
# Задано НОМЕРОМ воронки сделок: имя вкладки — имя воронки из портала (client.categories()),
# в коде его нет (репозиторий публичный).
ОТДЕЛЬНЫЕ_ВОРОНКИ = (16,)
CAP_RATE = 0.20          # стоимость денег, годовых: по чему считаем упущенную доходность
                         # от поздней отгрузки (решение владельца)

# снимок «Бюджетов сделок» из папки Google Drive (см. scripts/budget_snapshot.py):
# лист «Бюджет» каждой книги содержит ПОЛНУЮ плановую экономику — закуп, логистику,
# таможню, НДС, премии — и ссылку на сделку Битрикса. Суммы в книгах — в РУБЛЯХ.
_BUDGET_SNAP = pathlib.Path(__file__).resolve().parent / "data" / "budget_snapshot.json"


def _load_budget_plans() -> tuple[dict[int, dict], dict[str, dict], str]:
    """Снимок → (по №, по deal_id, дата снимка).

    Привязка К СДЕЛКЕ — в первую очередь по НОМЕРУ в названии книги («899. …»):
    ссылка на Битрикс в шапке бывает скопирована из чужой книги (зафиксировано:
    книга «729.» ссылается на сделку книги «899.»), поэтому по deal_id матчим
    только книги вовсе без номера. Дубли на один номер (например, «909.» и
    «Об 909.») схлопываются: побеждает книга с заполненной выручкой, при
    равенстве — изменённая позже."""
    try:
        snap = json.loads(_BUDGET_SNAP.read_text())
    except (OSError, ValueError):
        return {}, {}, ""

    def _better(new: dict, old: dict | None) -> bool:
        if old is None:
            return True
        nk = (1 if (new.get("revenue") or 0) else 0, str(new.get("modified") or ""))
        ok_ = (1 if (old.get("revenue") or 0) else 0, str(old.get("modified") or ""))
        return nk > ok_

    by_seq: dict[int, dict] = {}
    by_id: dict[str, dict] = {}
    for b in snap.get("budgets", []):
        seq = _regno(b.get("deal_name"), b.get("drive_title"))
        if seq:
            if _better(b, by_seq.get(seq)):
                by_seq[seq] = b
        elif b.get("deal_id"):
            did = str(b["deal_id"])
            if _better(b, by_id.get(did)):
                by_id[did] = b
    return by_seq, by_id, str(snap.get("generatedAt") or "")

SLOW_MIN_DAYS = 7        # порог замедленности не бывает ниже недели
SLOW_MULT = 2.0          # … и не ниже 2× медианы по стадии
HIST_SINCE = "2024-06-01T00:00:00"  # глубина истории стадий (таймлайны сделок и заказов)
# Бенчмарки стадий («сколько обычно длится стадия», порог замедленности, медиана цикла)
# считаются по последним 12 месяцам, а не по всей истории с 2024 года: команда и процесс
# меняются — люди приходят и уходят, — и медиана двухлетней давности мерит не нынешний
# отдел. Стадия, по которой за год меньше BENCH_MIN переходов, берёт всю историю.
BENCH_DAYS = 365
BENCH_MIN = 5


def _order_card(lo: dict, today: dt.date, med: dict, sup_slip: dict) -> dict:
    """Поля карточки заказа (У4 «Заказ», У5 «Шаг»; спецификация R4, Д-5 и Д-1а) живого заказа —
    из уже прочитанных полей заказа (life_orders), без новых чтений портала. Ни имён, ни денег.

    - плановые и фактические даты (ISO; пустые не пишутся): отгрузка поставщика план/факт,
      начало производства, приход на склад факт, доставка клиенту план, срок оплаты клиента;
      paid — платёжка клиента в заказе есть (файлом — без даты);
    - art — файлы шагов строкой по одному знаку в порядке ork.ARTIFACTS (каталог — artCat
      данных вкладки): «y» есть, «n» нет и обязателен (ork.art_required — одно правило с
      ведомостью и M2), «-» нет и не требуется;
    - fcp — прогноз доставки по частям (lifecycle.forecast_parts); слова «из чего» складывает
      страница из частей (у пункта очереди — fcWhy); дата — та же, что у пункта очереди и строки
      «Недели» (мерило одно — forecast_inputs);
    - cps — контрольные точки производства (ork.checkpoint_dates): середина срока и «за 14 дней
      до отгрузки»."""
    iso = (lambda d: d.isoformat() if d else "")
    out: dict = {}
    for k in ("shipPlan", "shipFact", "prodStart", "inbFact", "delPlan", "payPlan"):
        if lo.get(k):
            out[k] = iso(lo[k])
    if lo.get("clientPaid"):
        out["paid"] = True
    have = lo.get("art") or {}
    out["art"] = "".join("y" if have.get(a["key"]) else ("n" if ork.art_required(lo, a) else "-")
                         for a in ork.ARTIFACTS)
    p = lifecycle.forecast_parts(lo, today, med, sup_slip)
    if p:
        out["fcp"] = {"date": iso(p["date"]), "base": iso(p["base"]), "baseKind": p["baseKind"],
                      "baseRaw": iso(p["baseRaw"]), "transit": p["transit"], "lastMile": p["lastMile"],
                      "supSlip": p["supSlip"], "toShip": p["toShip"], "pushed": p["pushed"],
                      "clamped": p["clamped"], "solid": p["baseKind"] in lifecycle.FC_SOLID}
    cps = ork.checkpoint_dates(lo)
    if cps:
        out["cps"] = [{"kind": k, "date": d.isoformat()} for k, d in cps]
    return out


# каталог файлов шагов для карточки заказа (У5): ключ, имя поля, шаг, с какого шага обязателен
ART_CAT = [{"key": a["key"], "name": a["name"], "step": a["step"], "need": a["need"]} for a in ork.ARTIFACTS]


def _regno(*titles) -> int:
    """Порядковый номер сделки в реализации — число В НАЧАЛЕ названия, за которым идёт точка:
    «871. …» у сделки, «871/1. …» у заказа. 0 — если номера нет."""
    for t in titles:
        m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(t or ""))
        if m:
            return int(m.group(1))
    return 0


def _money(v: float) -> str:
    v = round(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}€{a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}€{a/1_000:.0f}K"
    return f"{sign}€{a}"


def _amount(v) -> float | None:
    """Поле суммы Битрикса → число; пусто — 0 (сверка скажет «суммы нет»), не число — None:
    нечитаемая сумма не «0» (правило Р-2: ноль ≠ неизвестно)."""
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        return None
    return round(x, 2) if x == x and abs(x) != float("inf") else None


def _orig(v, cur: str) -> str:
    v = round(float(v or 0))
    s = f"{v:,}".replace(",", " ")
    sym = _SYM.get(cur)
    return f"{sym}{s}" if sym else f"{s} {cur or ''}".strip()


_MSK = dt.timezone(dt.timedelta(hours=3))


def _parse_dt(s: str) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return d.replace(tzinfo=_MSK) if d.tzinfo is None else d   # метки Bitrix — портал (МСК)


def _days_between(a: str, b: str) -> float | None:
    da, db = _parse_dt(a), _parse_dt(b)
    if not da or not db:
        return None
    return max((db - da).total_seconds() / 86400.0, 0.0)


def _pdate(v) -> dt.date | None:
    """Плановая дата из поля СП-172 ('2026-03-31T03:00:00+03:00' или '2026-03-31') → date."""
    s = str(v or "")[:10]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _pay_days(v) -> int | None:
    """«Количество дней отсрочки платежа» → целое ≥ 0; пусто или не число — None
    (неизвестно — не ноль: без отсрочки срок оплаты считается по PAY_GRACE)."""
    try:
        n = int(float(str(v).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 720 else None


def _has(v) -> bool:
    """Файл, флажок или дата в поле карточки есть."""
    if isinstance(v, (list, tuple)):
        return any(_has(x) for x in v)
    return v not in (None, "", 0, "0", False, "N", {})


def _median(vals: list) -> float | None:
    sv = sorted(v for v in vals if v is not None)
    if not sv:
        return None
    n = len(sv)
    return sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2.0


def _parse_econ_money(raw, rate) -> float:
    """Bitrix money-поле '1234.56|RUB' → €. 0 если пусто/мусор или курса валюты нет в
    справочнике Битрикса: до 23.10.2026 такая валюта считалась 1:1 к базовой."""
    s = str(raw or "")
    if "|" not in s:
        return 0.0
    amt, cur = s.split("|", 1)
    if cur and cur not in rate:
        return 0.0
    try:
        return float(amt or 0) * rate.get(cur, 1.0)
    except ValueError:
        return 0.0


def _econ_cur(raw) -> str:
    """Валюта money-поля «сумма|ВАЛЮТА» с ненулевой суммой; иначе пусто."""
    s = str(raw or "")
    if "|" not in s:
        return ""
    amt, cur = s.split("|", 1)
    try:
        return cur if float(amt or 0) else ""
    except ValueError:
        return ""


#: Закупка несоизмерима с продажей — данные полей, а не сделка: в разы больше или
#: почти ноль. Числом не показывается и в итоги не идёт (случай 23.10.2026: продажа
#: €107K, закупка €645K из полей экономики при «поставка ~30к, закуп ~15к»).
BUY_SANE = (0.03, 3.0)


#: правило НДС строки (vatRule): net — продажа сверенного файла, уже без НДС; gross —
#: «Сумма» с НДС, снят по правилу 26.10.2026; foreign — покупатель не из РФ, как есть;
#: none — суммы нет или курса нет, снимать нечего
VAT_RULES = ("net", "gross", "foreign", "none")
#: откуда дата КП (kpSrc): «ТКП согласован», последнее КП до выигрыша, вход в кат. 0,
#: создание сделки; today — у сделки нет ни одной даты (курс — на день сборки)
KP_SRCS = ("tkp", "offer", "cat0", "created", "today")
_KP_SRC = {"ТКП согласован": "tkp", "последнее КП до выигрыша": "offer", "КП близнеца в пресейле": "offer",
           "вход в кат. 0": "cat0", "создание сделки": "created"}


def _зонд():
    """Правила зонда v59 — дата итогового ТКП, выигрыш, страна покупателя по реквизитам,
    ставка НДС по дате — импортом, а не копией (правило 14: две вставки правятся вместе)."""
    import sys
    root = pathlib.Path(__file__).resolve().parent
    for p in (str(root), str(root / "scripts")):
        if p not in sys.path:
            sys.path.append(p)
    # зонд ставит scripts/ в начало пути — в сборке дашборда это затенило бы модули корня
    # с тем же именем: после импорта путь возвращается как был (scripts/ — в конце)
    was = list(sys.path)
    try:
        import probe_money_sources
    finally:
        sys.path[:] = was
    return probe_money_sources


def _to_eur(amount, cur, rates: dict) -> float | None:
    """Сумма → € по курсам «€ за единицу». Ноль — 0; валюта не указана — базовая валюта
    портала (как до 26.10.2026); курса валюты нет — None: сумма неизвестна, не 1:1."""
    try:
        a = float(amount or 0)
    except (TypeError, ValueError):
        return None
    if not a:
        return 0.0
    if not cur:
        return a
    k = rates.get(str(cur))
    return a * k if k else None


class Курсы:
    """€ за единицу валюты на дату: ряды ЦБ РФ (base.fx.CbrRates) или, если ЦБ и зеркало
    недоступны, справочник Битрикса — один на все даты (запасной путь, с плашкой).

    failed — валюты, ряд которых в этой сборке не получен ни с cbr.ru, ни с зеркала (при
    годном евро): у них — курс Битрикса (patch, строка с rateSrc «bitrix» и плашка
    «ряд ЦБ не получен»), а нет его и в Битриксе — сумма неизвестна, как прежде."""

    def __init__(self, bitrix: dict[str, float], cbr: fx_mod.CbrRates | None, src: str = "",
                 failed=()):
        self.bitrix, self.cbr = bitrix, cbr
        self.src = (src or "cbr") if cbr is not None else "bitrix"
        self.failed = {str(c) for c in failed or ()} if cbr is not None else set()
        self.patch = {c for c in self.failed if self.bitrix.get(c)}
        self._at: dict[dt.date, dict[str, float]] = {}
        self.days: set[dt.date] = set()

    def at(self, day: dt.date | None) -> dict[str, float]:
        if self.cbr is None:
            return self.bitrix
        if day is None:
            return {}
        if day not in self._at:
            out = {}
            for c in self.cbr.codes():
                k = self.cbr.cross(c, "EUR", day)          # единиц валюты за 1 €
                if k:
                    out[c] = 1.0 / k
            if out.get("EUR"):                             # ряд ЦБ на дату есть — дыры валют
                for c in self.patch:                       # без ряда закрывает Битрикс
                    out.setdefault(c, self.bitrix[c])
            self._at[day] = out
        return self._at[day]

    def src_of(self, curs) -> str:
        """Источник курса сумм в этих валютах: cbr или bitrix (ЦБ нет или ряд не получен)."""
        if self.cbr is None or ({str(c) for c in curs if c} & self.patch):
            return "bitrix"
        return "cbr"

    def eur(self, amount, cur, day: dt.date | None) -> float | None:
        v = _to_eur(amount, cur, self.at(day))
        if v and self.cbr is not None and day is not None:
            self.days.add(day)
        return v


def _дата_или_нет(z, v) -> dt.date | None:
    """Дата портала по Москве (зонд v59, _дата); метка с годом 1 при переводе часового пояса
    даёт OverflowError — это опечатка, а не дата: None."""
    try:
        return z._дата(v)
    except (OverflowError, ValueError):
        return None


def _kp_date(d: dict, offers: list[dt.date], cat0: list[dt.date], *, z=None,
             today: dt.date | None = None) -> tuple[dt.date | None, str]:
    """Дата КП для курса (решения владельца 25.10.2026) → (дата, kpSrc).

    Порядок — зонд v59 (probe_money_sources.дата_ткп): «ТКП согласован» → последний вход в
    стадию предложения до выигрыша → первый вход в кат. 0 → DATE_CREATE. «ТКП согласован» в
    день создания сделки (±ТКП_У_СОЗДАНИЯ_ДН, у 75 из 532 — похоже на умолчание) уступает
    входу в стадию предложения, если он есть. КП близнеца в пресейле сборка не ищет.
    Дата вне base.fx.day_ok (опечатка года: «0024», «2205») — как пустая: следующая по порядку."""
    z = z or _зонд()
    ok = (lambda x: fx_mod.day_ok(x, today)) if today else bool
    for k in (WON_F, "DATE_CREATE"):
        if d.get(k) and not ok(_дата_или_нет(z, d.get(k))):
            d = {**d, k: None}
    offers = [x for x in offers or [] if ok(x)]
    cat0 = [x for x in cat0 or [] if ok(x)]
    f, c = z._дата(d.get(WON_F)), z._дата(d.get("DATE_CREATE"))
    offers = sorted(offers)
    if f and c and offers and abs((f - c).days) <= z.ТКП_У_СОЗДАНИЯ_ДН:
        w = z.выигрыш(offers, cat0 or None)
        до = [x for x in offers if not w or x <= w]
        if до:
            return max(до), "offer"
    day, src = z.дата_ткп(d, offers, cat0 or None, None)
    return day, _KP_SRC.get(src, "created") if day else "today"


def _vat(buyer: str | None, kp_day: dt.date | None, *, z=None, sep: bool = False) -> tuple[str, float]:
    """Правило НДС продажи без сверенного файла (решение владельца 26.10.2026) → (vatRule,
    делитель). buyer — страна покупателя по реквизитам: «РФ», «не РФ», None — реквизитов нет
    (как РФ). Ставка — по дате итогового ТКП: 1,20 до 2026 года, 1,22 с него.
    sep — сделка отдельного направления (Басра, воронка 16, и сделки тех же компаний): без
    реквизитов покупатель там иностранный, «Сумма» как есть; реквизиты РФ — правило общее
    (решение владельца 26.10.2026)."""
    if buyer == "не РФ" or (sep and buyer is None):
        return "foreign", 1.0
    z = z or _зонд()
    return "gross", z.ставки_ндс(kp_day)[0] if kp_day else z.СТАВКИ_НДС[0]


def _utd_date(o: dict, today: dt.date) -> dt.date | None:
    """Дата подписания УПД заказа, если она уже прошла: замена даты доставки, когда факта
    нет (решение владельца 25.10.2026, lifecycle.delivered). Будущая дата — опечатка, не
    доставка."""
    d = _pdate(o.get(UTD_DATE))
    return d if d and d <= today else None


def compute(client: BitrixClient, *, as_of: dt.date | None = None,
            people: dict[str, dict] | None = None, service_ids: set[str] | None = None,
            chat_reader: chat_activity.Reader | None = None,
            ctx_chats: dict[str, dict] | None = None,
            ctx_econ: dict[str, dict] | None = None,
            ctx_contracts: dict[str, dict] | None = None,
            departments: list[dict] | None = None,
            fx_loader=None, stage_meta: dict[str, dict] | None = None) -> dict:
    """people — справочник {uid: {name, active}} с ушедшими (main._people): без него
    уволенный ответственный или ОСС показывался заглушкой «user#N», а его живые
    сделки и заказы не были видны как работа без хозяина.
    chat_reader — общий на сборку читатель чатов (main.py: реализация читает первой,
    пресейл — остатком бюджета); None — свой бюджет, как раньше.
    ctx_chats — чаты сделок из закрытого свода (dealctx.load()["chats"]): оценка хода
    моделью и последнее чтение прогоном оценки; None — «подвисла» как до 23.10.2026.
    ctx_econ — финальная экономика из того же свода (dealctx.load()["econ"], только сверенные
    файлы): продажа и закупка из файла — первыми; None — как до 25.10.2026.
    ctx_contracts — разбор договоров из того же свода (dealctx.load()["contracts"]): договор
    заказчика найден — пункта «оплачено поставщику без договора с заказчиком» нет, даже если
    стадия сделки отстала (дизайн, шаг 5); None — только по стадии.
    departments — уже прочитанный справочник отделов (BitrixClient.departments(), кэш сборки):
    отдел ОРК и его руководитель (people.ork_department) — хозяева пунктов по регламенту ОРК
    (решение владельца 26.10.2026, п. 1); None — менеджер ОРК только из поля сделки,
    руководитель — подписью роли.
    fx_loader — (валюты, даты, today) → (base.fx.CbrRates | None, учёт): курсы ЦБ РФ на
    нужные даты (main.py — base.fx.load_cbr); None или ЦБ недоступен — курсы Битрикса с
    плашкой «курсы Битрикса (ЦБ недоступен)» и счётом в head «fx».
    stage_meta — справочник стадий сделок (client.deal_stage_meta), если сборка его уже
    прочитала (пресейл): для стадий «предложение выдано» второй раз портал не спрашивается."""
    today = as_of or dt.date.today()
    now_iso = period_mod.now(_MSK).isoformat()
    people = people or {}
    gone_ids = {u for u, x in people.items() if not x.get("active", True)}

    def who(v) -> tuple[str, bool]:
        """(подпись, ушёл ли). Поле «сотрудник» бывает списком — берём первого."""
        if isinstance(v, (list, tuple)):
            v = next((x for x in v if str(x or "") not in ("", "0")), "")
        u = str(v or "")
        if u in ("", "0", "None"):
            return "", False
        if u in gone_ids:
            return f"{people[u].get('name') or 'user#' + u} (уволен)", True
        return client.user_name(u), False
    def uid_of(v) -> str:
        """id сотрудника из поля-«сотрудник» (бывает списком) — ключ человека без подписи."""
        if isinstance(v, (list, tuple)):
            v = next((x for x in v if str(x or "") not in ("", "0")), "")
        u = str(v or "")
        return "" if u in ("", "0", "None") else u
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    # курсы в Битриксе заданы вручную и не тянутся из внешнего источника — показываем дату,
    # чтобы расхождение с любой другой отчётностью было объяснимо, а не спорно
    rate_dates = [str(x.get("DATE_UPDATE") or "")[:10] for x in curlist
                  if x.get("CURRENCY") in ("RUB", "USD", "EUR") and x.get("DATE_UPDATE")]
    _asof = min(rate_dates) if rate_dates else ""
    _d = _pdate(_asof)
    rate_asof = _d.strftime("%d.%m.%Y") if _d else _asof
    base_cur = next((x.get("CURRENCY") for x in curlist if x.get("BASE") == "Y"), "EUR")

    # 1. все непроигранные заказы поставщикам (закупки)
    orders = client.list_items(172, filter={}, select=["id", "title", "companyId", "parentId2",
                                                        "opportunity", "currencyId", "stageId",
                                                        "createdTime", "movedTime",
                                                        "assignedById",
                                                        DL_CUSTOMER, PROD_END, INBOUND_PLAN,
                                                        CITY_F, OSS_F, SHIP_PLAN, SHIP_FACT, INB_FACT,
                                                        DEL_PLAN, DEL_BUDGET, DEL_FACT, UTD_DATE, UTD_FILE,
                                                        PAY_PLAN, CLIENT_PO, SUP_PAID, PROD_CONF,
                                                        PROD_CONF_NF, SCHEME_F, PROD_START,
                                                        KAM_ORDER, DIRECTION_F, ASSIGN_AT, PREPAY_F,
                                                        *[f for f in ART_FIELDS if f not in (
                                                            DL_CUSTOMER, PROD_END, DEL_FACT, UTD_DATE,
                                                            PROD_CONF)]])
    # стадии всех воронок СП-172 с семантикой: провал — по семантике стадии, а не по
    # суффиксу «:FAIL» (свои стадии провала UC_* с семантикой F считались живыми);
    # воронки — по префиксу стадии «DT172_<воронка>:…» (categoryId в выборке нет)
    ostages: dict[str, str] = {}
    osem: dict[str, str] = {}
    ocat_ids = sorted({int(m.group(1)) for o in orders
                       if (m := re.match(r"DT172_(\d+):", str(o.get("stageId") or "")))} | {26})
    for cid in ocat_ids:
        try:
            for sid, m in client.spa_stage_meta(172, cid).items():
                ostages[sid] = m["name"]
                osem[sid] = m["sem"]
        except Exception:
            pass

    def _osem(sid: str) -> str:
        sid = str(sid or "")
        if sid in osem:
            return osem[sid]
        return "S" if sid.endswith(":SUCCESS") else "F" if sid.endswith(":FAIL") else "P"

    orders = [o for o in orders if _osem(o.get("stageId")) != "F"]
    supl = client.companies_by_ids({str(o["companyId"]) for o in orders if o.get("companyId")})

    # 2. группировка заказов по сделке
    by_deal: dict[str, list] = defaultdict(list)
    orphan = []
    for o in orders:
        did = str(o.get("parentId2") or "")
        (by_deal[did] if did else orphan).append(o)

    # 3. вселенная: сделки ВОРОНКИ РЕАЛИЗАЦИИ (кат.0) + родители заказов вне её
    DSEL = ["ID", "TITLE", "OPPORTUNITY", "CURRENCY_ID", "COMPANY_ID", "STAGE_ID",
            "STAGE_SEMANTIC_ID", "CATEGORY_ID", "DATE_CREATE", "ASSIGNED_BY_ID", OSS_DEAL, KAM_F, KAM_OLD,
            chat_activity.CHAT_FIELD,
            BUY_ECON_RUB, BUY_ECON_VAL, ECON_MARGIN, ECON_PAID, ECON_REST, ECON_LINK,
            BUDGET_LINK,
            # НДС в «Сумме»: сверка с финальной экономикой файла — без НДС (dealctx.econ_ratio)
            "TAX_VALUE", WON_F, PAY_DAYS_F, ORK_RESP]
    plan_by_seq, plan_by_id, plan_asof = _load_budget_plans()

    cat0 = client.list_deals_fast(filter={"CATEGORY_ID": 0}, select=DSEL)
    deals: dict[str, dict] = {str(d["ID"]): d for d in cat0}
    extra_ids = [d for d in by_deal if d and d not in deals]
    if extra_ids:
        deals.update(client.deals_by_ids(extra_ids, select=DSEL))
    clients = client.companies_by_ids({str(d.get("COMPANY_ID")) for d in deals.values() if d.get("COMPANY_ID")})
    # 3а. отдельные направления: компании сделок их воронок — один обход на воронку
    # (только ID и компания; у направления десятки сделок, не тысячи)
    sep_cats = {str(c) for c in ОТДЕЛЬНЫЕ_ВОРОНКИ}
    sep_company: dict[str, str] = {}               # COMPANY_ID → номер воронки направления
    for cid in ОТДЕЛЬНЫЕ_ВОРОНКИ:
        for x in client.list_deals_fast(filter={"CATEGORY_ID": cid}, select=["ID", "CATEGORY_ID", "COMPANY_ID"]):
            if str(x.get("CATEGORY_ID") or cid) != str(cid):
                continue
            co = str(x.get("COMPANY_ID") or "")
            if co not in ("", "0"):
                sep_company.setdefault(co, str(cid))

    def _segment(d: dict) -> str | None:
        """Номер воронки отдельного направления сделки; None — основной вид."""
        cat = str(d.get("CATEGORY_ID") or "0")
        if cat in sep_cats:
            return cat
        return sep_company.get(str(d.get("COMPANY_ID") or ""))

    # 4. справочники стадий + история стадий (полное время первого входа)
    dstages = client.deal_stages_cat(0)                    # все стадии — для подписей
    dproc = client.deal_stages_process(0)                  # только рабочие: без успеха и проигрышей
    dorder = {sid: i for i, sid in enumerate(dproc)}
    paid_stages = {sid for sid, nm in dstages.items() if PAID_RX.search(str(nm or ""))}
    dlast: dict[str, tuple[str, str]] = {}                 # последний переход = вход в текущую стадию
    olast: dict[str, tuple[str, str]] = {}
    # все переходы с повторными входами — сегменты S1–S10 регламента ОРК (ork.segments): тот
    # же ответ тех же запросов, без отбрасывания повторов
    dtrans: dict[str, list[tuple[str, str]]] = {}
    otrans: dict[str, list[tuple[str, str]]] = {}
    dhist = client.stage_history(2, category_id=0, since=HIST_SINCE, last=dlast, transitions=dtrans)
    ohist = client.stage_history(172, since=HIST_SINCE, last=olast, transitions=otrans)
    bench_from = (period_mod.now(_MSK) - dt.timedelta(days=BENCH_DAYS)).isoformat()

    def _stage_name(sid: str, cat=None) -> str:
        if sid in dstages:
            return dstages[sid]
        return ostages.get(sid) or sid

    # 5. бенчмарки: медианная длительность каждой стадии ЗАКАЗОВ (по истории переходов)
    #    — за последние BENCH_DAYS; стадия с редкими переходами берёт всю историю
    def _durations(hist: dict) -> tuple[dict, dict]:
        recent: dict[str, list[float]] = defaultdict(list)
        full: dict[str, list[float]] = defaultdict(list)
        for entries in hist.values():
            for (s1, t1), (_s2, t2) in zip(entries, entries[1:]):
                d = _days_between(t1, t2)
                if d is None:
                    continue
                full[s1].append(d)
                if str(t1) >= bench_from:
                    recent[s1].append(d)
        return {s: (recent[s] if len(recent.get(s, ())) >= BENCH_MIN else v) for s, v in full.items()}, recent

    odur, odur_recent = _durations(ohist)
    omed = {s: round(_median(v) or 0, 1) for s, v in odur.items() if v}
    # …и стадий СДЕЛОК в кат.0
    ddur, ddur_recent = _durations(dhist)
    dmed = {s: round(_median(v) or 0, 1) for s, v in ddur.items() if v}

    def _slow_threshold(stage_id: str, med: dict) -> float:
        return max(SLOW_MULT * med.get(stage_id, 0.0), float(SLOW_MIN_DAYS))

    # 5а. деньги: дата КП, страна покупателя, курсы ЦБ (решения владельца 25 и 26.10.2026)
    z = _зонд()

    def _in_universe(did: str, d: dict) -> bool:
        ords = by_deal.get(did, [])
        in_cat0 = str(d.get("CATEGORY_ID") or "0") == "0"
        if not in_cat0 and not ords:
            return False                  # чужая воронка без заказов — не наша вселенная
        seq = _regno(d.get("TITLE"), ords[0].get("title") if ords else "")
        return not (in_cat0 and not ords and seq == 0 and not d.get("OPPORTUNITY"))   # пустышки

    uni = {did: d for did, d in deals.items() if _in_universe(did, d)}
    def _tkp_at_create(d: dict) -> bool:
        f, c = _дата_или_нет(z, d.get(WON_F)), _дата_или_нет(z, d.get("DATE_CREATE"))
        return bool(f and c and abs((f - c).days) <= z.ТКП_У_СОЗДАНИЯ_ДН)

    # Запасные даты КП — только у сделок без годного «ТКП согласован» или с ним в день создания
    # (проигранные тоже: их «Сумма» в сводке ушедших). Одним чтением истории (пачка на 50 сделок,
    # probe_conversion.читать_историю, как шаг «запасные даты» зонда v59) — ВСЕ входы в стадии
    # «предложение выдано» (вид_стадии) и в стадии кат. 0, без отсечки по дате: dhist начинается
    # с HIST_SINCE и хранит только первый вход в стадию — у сделки 2023 года «вход в кат. 0»
    # оказался бы в 2024-м, а возврат в NEW при выигрыше терялся бы (ревью 26.10.2026).
    def _tkp_ok(d: dict) -> bool:
        return fx_mod.day_ok(_дата_или_нет(z, d.get(WON_F)), today)

    need_offer = sorted((did for did, d in uni.items()
                         if str(did).isdigit() and (not _tkp_ok(d) or _tkp_at_create(d))), key=int)
    offers: dict[str, list[dt.date]] = {}
    cat0_full: dict[str, list[dt.date]] | None = None
    kp_err = ""
    calls: dict[str, int] = {}
    n0 = bitrix_client.запросов_портала()
    if need_offer:
        try:
            smeta = stage_meta or client.deal_stage_meta()
            offer_st = {sid for sid in smeta if z.pc.вид_стадии(sid, smeta)[0] == "offer"}
            cat0_st = set(dstages) | {sid for sid, m in smeta.items()
                                      if str((m or {}).get("cat") or "") == "0"}
            if not offer_st:
                kp_err = "нет стадий предложения"
            ist, ош = z.pc.читать_историю(client, need_offer, стадии=offer_st | cat0_st)
            if ош:                  # неполная история — не «последнее КП»: запасные даты
                kp_err = "история неполна"
            else:
                cat0_full = {}
                for did, ряд in ist.items():
                    of = sorted({x for x in (_дата_или_нет(z, t) for sid, t in ряд if sid in offer_st) if x})
                    c0 = sorted({x for x in (_дата_или_нет(z, t) for sid, t in ряд
                                             if sid in cat0_st and z.pc.воронка_стадии(sid) == "0") if x})
                    if of:
                        offers[did] = of
                    if c0:
                        cat0_full[did] = c0
        except Exception as e:  # noqa: BLE001 — без истории даты КП берутся запасные
            kp_err = type(e).__name__
    calls["hist"] = bitrix_client.запросов_портала() - n0
    hist_from = dt.date.fromisoformat(HIST_SINCE[:10])

    def _cat0_of(did: str, d: dict) -> list[dt.date]:
        """Входы в кат. 0: полная история; не прочитана — dhist (первые входы с HIST_SINCE),
        но только у сделок, заведённых не раньше HIST_SINCE: у старших она обрезана."""
        if cat0_full is not None:
            return cat0_full.get(did, [])
        c = _дата_или_нет(z, d.get("DATE_CREATE"))
        if not c or c < hist_from:
            return []
        return sorted({x for x in (_дата_или_нет(z, t) for _s, t in dhist.get(did, [])) if x})

    kp_of: dict[str, tuple[dt.date, str]] = {}
    for did, d in uni.items():
        day, src = _kp_date(d, offers.get(did, []), _cat0_of(did, d), z=z, today=today)
        kp_of[did] = (day or today, src)
    n_tkp_create = sum(1 for did, d in uni.items() if kp_of[did][1] == "tkp" and _tkp_at_create(d))
    # страна покупателя по реквизитам (зонд v59: РФ 86, без реквизитов 34 из 120 компаний)
    buyer_co = sorted({str(d.get("COMPANY_ID") or "") for d in uni.values()} - {"", "0"},
                      key=lambda c: int(c) if c.isdigit() else 0)
    country: dict[str, str] = {}
    req_err = ""
    n0 = bitrix_client.запросов_портала()
    if buyer_co:
        try:
            presets = {str(x.get("ID")): str(x.get("COUNTRY_ID") or "") for x in (
                client.call("crm.requisite.preset.list", {"select": ["ID", "COUNTRY_ID"]}) or [])
                if isinstance(x, dict)}
            country = z.страна_по_реквизитам(
                z.cn.реквизиты(client, [c for c in buyer_co if c.isdigit()], поля=("PRESET_ID", "RQ_INN")),
                presets)
        except Exception as e:  # noqa: BLE001 — страна не прочитана: НДС снимается у всех (как у РФ)
            country, req_err = {}, type(e).__name__
    calls["req"] = bitrix_client.запросов_портала() - n0
    # курсы ЦБ: валюты сумм сделок и заказов; даты КП, заказов, книг бюджета и сегодня
    fx_codes_all = ({str(d.get("CURRENCY_ID") or "") for d in uni.values()}
                    | {str(o.get("currencyId") or "") for o in orders} | {"USD", "EUR"}) - {""}
    plan_day_default = _pdate(plan_asof) or today
    # дата снимка бюджета — тоже: у книги без «изменена» курс на неё (на зеркале иначе взялся бы
    # курс соседней прочитанной даты)
    fx_days = {k for k, _s in kp_of.values()} | {today, plan_day_default}
    fx_days |= {x for x in (_дата_или_нет(z, o.get("createdTime")) for o in orders) if x}
    fx_days |= {x for x in (_pdate(b.get("modified"))
                            for b in list(plan_by_seq.values()) + list(plan_by_id.values())) if x}
    cbr, fx_info, fx_why = None, {}, "ЦБ не запрашивался"
    if fx_loader is not None:
        try:
            cbr, fx_info = fx_loader(fx_codes_all, sorted(fx_days), today)
        except Exception as e:  # noqa: BLE001 — сбой загрузчика = ЦБ недоступен, не падение вкладки
            cbr, fx_info = None, {"why": type(e).__name__}
        fx_why = "ЦБ недоступен"
    K = Курсы(rate, cbr, (fx_info or {}).get("src") or "", failed=(fx_info or {}).get("failed") or ())

    def o_day(o: dict, kp_day: dt.date) -> dt.date:
        """Дата заказа для курса: создание заказа (по Москве); нет или опечатка — дата КП сделки."""
        x = _дата_или_нет(z, o.get("createdTime"))
        return x if fx_mod.day_ok(x, today) else kp_day


    # 5б. ЖИЗНЕННЫЙ ЦИКЛ И РЕГЛАМЕНТ ОРК: заказы, приведённые к фактам, → фазы, очередь «что
    # делать», доставка в срок, сроки звеньев, надёжность поставщиков (lifecycle.py); шаг
    # регламента, вход в шаг, сегменты S1–S10 по всем переходам, артефакты (ork.py)
    ork_ids, ork_head_uid = people_mod.ork_department(departments)
    ork_head = who(ork_head_uid)[0] if ork_head_uid else ""

    def is_ork(uid: str) -> bool:
        """Сотрудник отдела ОРК (или его дочернего отдела) по справочнику людей."""
        return bool(uid) and bool(set((people.get(uid) or {}).get("depts") or []) & ork_ids)

    # вход сделки в реализацию (кат. 0) — первый переход её истории; шаг сделки по стадии
    # (ork.STAGE_STEP["deal"]) и наибольший шаг по всем её переходам
    deal_cat0 = {did: tl[0][1] for did, tl in dhist.items() if tl}

    def _dstep(sid: str) -> int | None:
        return ork.stage_step(dstages.get(sid) or "", "deal")
    deal_step_max: dict[str, int] = {}
    for did, tr in dtrans.items():
        st = [x for x in (_dstep(sid) for sid, _ in tr) if x is not None]
        if st:
            deal_step_max[did] = max(st)

    def _share(v) -> float | None:
        """«Доля первичной оплаты, %» → число 0…100; пусто или не число — None (не 0)."""
        if v in (None, ""):
            return None
        try:
            x = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return x if 0 <= x <= 100 else None

    def _t0(created: str, cat0: str) -> str:
        """Начало приёмки: max(создание заказа, вход сделки в реализацию)."""
        a, b = _parse_dt(created), _parse_dt(cat0)
        if a and b:
            return max(a, b).isoformat()
        return (a or b).isoformat() if (a or b) else ""

    try:
        _ofields = (client.call("crm.item.fields", {"entityTypeId": 172}) or {}).get("fields") or {}
    except Exception:
        _ofields = {}
    schemes = {str(i.get("ID")): str(i.get("VALUE") or "")
               for i in ((_ofields.get(SCHEME_F) or {}).get("items") or [])}
    recent_from = today - dt.timedelta(days=BENCH_DAYS)
    life_orders = []
    for o in orders:
        osid = str(o.get("stageId") or "")
        sm = _osem(osid)
        oid = str(o.get("id"))
        try:
            paid = float(o.get(SUP_PAID) or 0)
        except (TypeError, ValueError):
            paid = 0.0
        _who = who(o.get(OSS_F))
        _resp = who(o.get("assignedById"))
        moved = _pdate(o.get("movedTime"))
        did = str(o.get("parentId2") or "")
        # вход в ТЕКУЩУЮ стадию: последний переход истории, иначе время последнего движения
        otl = ohist.get(oid, [])
        _ol = olast.get(oid)
        oin = (_ol[1] if _ol and _ol[0] == osid
               else str(o.get("movedTime") or (otl[-1][1] if otl else "") or o.get("createdTime") or ""))
        tr = otrans.get(oid) or []
        # вход в шаг (шаг 5 — три стадии заказа): по всем переходам, если последний из них —
        # вход в текущую стадию; иначе — вход в стадию
        _since = ork.step_since(tr, ostages)[1] if (_ol and _ol[0] == osid and tr) else ""
        t0 = _t0(str(o.get("createdTime") or ""), deal_cat0.get(did, ""))
        utd_raw = _pdate(o.get(UTD_DATE))            # сырая дата УПД — без подстановки
        _rs = [x for x in (ork.stage_step(ostages.get(sid, sid)) for sid, _ in tr) if x is not None]
        # номер заказа, как его пишет портал («900/1»), — у пункта очереди, в таблицах недели,
        # очереди КАМа и карточке (владелец 29.10.2026: «сделка одна — заказы разные 1/2/3»)
        _lbl, _no = ork.order_label(o.get("title"), oid)
        # «Дата записи нового ответственного» позже конца окна (today = p.end — вчера): на конец
        # окна назначения ещё не было — как не записана (иначе шаг 4 с р.д. −1 и пункт пропадал
        # из s1Over и s2Over)
        assign = _pdate(o.get(ASSIGN_AT))
        assign = assign if assign and assign <= today else None
        _ork_mgr = _resp[0] if is_ork(uid_of(o.get("assignedById"))) else ""
        lo = {
            "id": oid, "deal": did, "seq": 0, "lbl": _lbl, "ordNo": _no,
            "title": str(o.get("title") or "")[:70], "sup": supl.get(str(o.get("companyId"))) or "",
            "oss": _who[0], "ossGone": _who[1],
            # Responsible person заказа; менеджер ОРК — он, если он из отдела ОРК (решение 26.10.2026)
            "resp": _resp[0], "respOrk": is_ork(uid_of(o.get("assignedById"))),
            "orkMgr": _ork_mgr,
            # id Битрикса тех же людей — отбор «Смотреть как» (R4, Д-11а); в страницу не идёт
            # (life_orders — внутренний список)
            "_pid": {"orkMgr": uid_of(o.get("assignedById")) if _ork_mgr else "",
                     "ossOrder": uid_of(o.get(OSS_F))},
            "live": sm == "P", "success": sm == "S", "closedRecent": bool(sm == "S" and moved and moved >= recent_from),
            "created": _pdate(o.get("createdTime")), "dl": _pdate(o.get(DL_CUSTOMER)),
            "shipPlan": _pdate(o.get(SHIP_PLAN)), "shipFact": _pdate(o.get(SHIP_FACT)),
            "prodEnd": _pdate(o.get(PROD_END)), "prodStart": _pdate(o.get(PROD_START)),
            "inbFact": _pdate(o.get(INB_FACT)),
            "delPlan": _pdate(o.get(DEL_PLAN)), "delBudget": _pdate(o.get(DEL_BUDGET)),
            "delFact": _pdate(o.get(DEL_FACT)),
            # дата подписания УПД — дата доставки, когда факта нет (lifecycle.delivered)
            "utdDate": _utd_date(o, today),
            # сырая UTD_DATE (S9 и пункт noUtd — по ней, а не по подставленной дате)
            "utdRaw": utd_raw,
            "utd": bool(utd_raw or _has(o.get(UTD_FILE))),
            "payPlan": _pdate(o.get(PAY_PLAN)), "clientPaid": _has(o.get(CLIENT_PO)),
            "supPaidPct": paid, "prodConf": _has(o.get(PROD_CONF)) or _has(o.get(PROD_CONF_NF)),
            "scheme": schemes.get(str(o.get(SCHEME_F) or ""), ""),
            # регламент ОРК: стадия, вход в неё и в шаг, начало приёмки, вход сделки в реализацию
            "stage": _stage_name(osid), "stageId": osid, "stageSince": oin, "stepSince": _since or oin,
            "t0": t0, "dealCat0": deal_cat0.get(did, ""),
            "reachMax": max(_rs) if _rs else None,
            "kamOrder": who(o.get(KAM_ORDER))[0], "direction": str(o.get(DIRECTION_F) or ""),
            # «Дата записи нового ответственного»: приёмка кончается назначением, если оно раньше
            # выхода из «Заказ создан» и ответственный из ОРК (ork.assign_at, вопрос владельца 29.10.2026)
            "prepayShare": _share(o.get(PREPAY_F)), "assignDate": assign,
            # артефакты шагов (ork.ARTIFACTS): файл, флажок, дата есть — содержимое не проверено
            "art": {a["key"]: _has(o.get(a["field"])) for a in ork.ARTIFACTS},
            # только для расчёта (в страницу не идут): первый выход на шаги и сегменты S1–S10
            "_reach": ork.reach_times(tr, ostages),
            # назначение — в сегменты только у ответственного из ОРК (то же правило, что ork.assign_at)
            "_segs": ork.segments(tr, ostages, t0=t0 or None,
                                  facts={"delFact": _pdate(o.get(DEL_FACT)), "utdRaw": utd_raw,
                                         "assignAt": assign if _ork_mgr else None}),
        }
        a = lo["art"]
        a["planDates"] = bool(lo["prodStart"] and lo["prodEnd"])
        a["prodConf"], a["utd"], a["dl"] = lo["prodConf"], lo["utd"], bool(lo["dl"])
        life_orders.append(lo)
    lo_by_id = {lo["id"]: lo for lo in life_orders}
    # прогноз доставки по частям для карточки заказа (Д-1а): то же мерило, что у очереди и
    # «Недели» (lifecycle.build и orkview меряют звенья и опоздание поставщика по всем заказам,
    # measure = life_orders) — дата в карточке совпадает с датой пункта и строки списка
    _fc_med, _fc_slip = lifecycle.forecast_inputs(life_orders, today)
    # 6. строки: одна строка = одна сделка воронки реализации (или с заказами)
    rows = []
    n_plan_norate = 0
    for did, d in uni.items():
        ords = by_deal.get(did, [])
        in_cat0 = str(d.get("CATEGORY_ID") or "0") == "0"
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        seq = _regno(d.get("TITLE"), ords[0].get("title") if ords else "")
        kp_day, kp_src = kp_of[did]
        rate_s = K.at(kp_day)                  # € за единицу на дату КП
        o_eur = {str(o.get("id")): K.eur(o.get("opportunity"), o.get("currencyId"), o_day(o, kp_day))
                 for o in ords}

        # Валюта без курса (ЦБ на дату или справочник Битрикса) — сумма НЕИЗВЕСТНА, а не 1:1 к
        # базовой (так до 23.10.2026 было: 107 тыс. AED показывались как €107K)
        sale_cur = str(d.get("CURRENCY_ID") or "")
        # поля экономики в список не идут: они только сверка и в итоги не входят
        fx_miss = sorted(({sale_cur} if sale_cur and sale_cur not in rate_s else set())
                         | {str(o.get("currencyId")) for o in ords if o_eur[str(o.get("id"))] is None})
        sale_known = not (sale_cur and sale_cur not in rate_s)
        sale_gross = (K.eur(d.get("OPPORTUNITY"), sale_cur, kp_day) or 0.0) if sale_known else 0.0
        # НДС по правилу 26.10.2026: покупатель из РФ или без реквизитов — ÷ ставку на дату ТКП
        vat_buyer = country.get(str(d.get("COMPANY_ID") or ""))
        vat_rule, vat_k = _vat(vat_buyer, kp_day, z=z, sep=bool(_segment(d)))
        if not sale_gross:
            vat_rule, vat_k = "none", 1.0
        sale = sale_gross / vat_k
        vat_bx = vat_rule                      # правило для «Суммы» Битрикса (и у строки с файлом)
        orders_known = all(v is not None for v in o_eur.values())
        # --- закупка: заказы → нет данных. Money-поля экономики («Cost (rub)», «Offer sum») —
        # только сверка (discrep): итерация расчёта в них не проверена, в случае 23.10.2026 они
        # дали закупку вшестеро больше продажи (ревизия денег 25.10.2026)
        buy_orders = sum(o_eur.values()) if orders_known else 0.0
        buy_econ = (_parse_econ_money(d.get(BUY_ECON_RUB), rate_s)
                    or _parse_econ_money(d.get(BUY_ECON_VAL), rate_s))
        if not orders_known:
            buy, buy_src = 0.0, "fx"          # заказ в валюте без курса: закупка неизвестна
        elif buy_orders > 0:
            buy, buy_src = buy_orders, "orders"
        else:
            buy, buy_src = 0.0, "none"
        # --- финальная экономика сверенного файла — первой (решение владельца 25.10.2026):
        # продажа и закупка в евро по курсам файла; «Сумма» и заказы остаются сверкой
        sale_bx = round(sale) if (sale_known and sale) else None     # нет суммы — не «€0»
        e = (ctx_econ or {}).get(did)
        if e:
            _amt, _tax = _amount(d.get("OPPORTUNITY")), _amount(d.get("TAX_VALUE"))
            _r, _n = (dealctx.econ_ratio(_amt, sale_cur, e, tax=_tax) if _amt is not None and _tax is not None
                      else (None, ""))
            if dealctx.econ_stale(e, _r, _n):
                e = None                       # «Сумму» меняли после чтения — до перечтения Битрикс
        if e:
            sale, sale_known, sale_src = e["saleEur"], True, "file"
            buy, buy_src = e["buyEur"], "file"
            vat_rule = "net"                   # продажа файла уже без НДС (сверено прогоном)
        else:
            sale_src = "bitrix"
        # закупка несоизмерима с продажей — показывается текстом, в маржу и итоги не идёт
        buy_susp = bool(sale > 0 and buy > 0 and not BUY_SANE[0] <= buy / sale <= BUY_SANE[1])
        # источники закупки расходятся больше чем на 20 %: у файла — файл против заказов, иначе
        # заказы против money-полей (ячейка закупки показывает второй источник рядом)
        _alt = buy_orders if buy_src == "file" else buy_econ
        _main = buy if buy_src == "file" else buy_orders
        discrep = (_main > 0 and _alt > 0 and abs(_main - _alt) / max(_main, _alt) > 0.2)
        margin = (sale - buy) if (sale and buy_src in ("orders", "file") and not buy_susp) else None
        # красный флаг: экономики проекта нет ни ссылкой на файл, ни money-полями
        econ_link = str(d.get(ECON_LINK) or "").strip()
        no_econ = not econ_link and buy_econ <= 0 and sale_src != "file"

        # --- плановая экономика из бюджета сделки (снимок Google Drive, суммы в ₽) ---
        plan = (plan_by_seq.get(seq) if seq else None) or plan_by_id.get(did)
        plan_md = plan_pct = plan_profit = plan_ppct = plan_rev = None
        plan_susp = False
        plan_psrc = None
        plan_break = None
        # ₽ бюджета → € по ЦБ на дату книги (изменена); нет даты — на дату снимка
        plan_day = _pdate(plan.get("modified")) if plan else None
        if plan and not fx_mod.day_ok(plan_day, today):
            plan_day = plan_day_default
        rub = K.at(plan_day).get("RUB", 0.0) if plan else 0.0
        if plan and not rub:
            n_plan_norate += 1              # книга есть, курса на её дату нет: плана нет, счётом
        if rub and K.cbr is not None:
            K.days.add(plan_day)
        if plan and rub:
            _rev = plan.get("revenue_net") or plan.get("revenue")
            _md = plan.get("margin_income_net") if plan.get("margin_income_net") is not None else plan.get("margin_income")
            # «Прибыль по сделке» заполнена лишь у 118 книг, «Операционная прибыль» — у 234.
            # Берём каскадом: обе уже ПОСЛЕ прямых переменных и прочих прямых расходов.
            def _nz(v):
                return v if isinstance(v, (int, float)) and v else None
            _pf = _nz(plan.get("deal_profit_net")) or _nz(plan.get("deal_profit"))
            plan_psrc = "deal" if _pf is not None else None
            if _pf is None:
                _pf = _nz(plan.get("op_profit_net")) or _nz(plan.get("op_profit"))
                plan_psrc = "op" if _pf is not None else None
            plan_rev = _rev * rub if _rev else None
            plan_md = _md * rub if _md is not None else None
            plan_profit = _pf * rub if _pf is not None else None
            plan_pct = (round(plan.get("margin_pct") * 100, 1) if plan.get("margin_pct") is not None
                        else (round(_md / _rev * 100, 1) if (_md is not None and _rev) else None))
            plan_ppct = round(plan.get("profit_pct") * 100, 1) if plan.get("profit_pct") is not None else None
            # бюджет не бьётся с суммой сделки в разы — вероятно, книга-заготовка или не та сделка
            plan_susp = bool(sale > 1000 and plan_rev and not 0.25 <= plan_rev / sale <= 4.0)
            def _e(*names):
                for n in names:
                    v = plan.get(n)
                    if isinstance(v, (int, float)) and v:
                        return round(v * rub)
                return None
            plan_break = {
                "rev": _e("revenue_net", "revenue"),
                "buy": _e("purchase_net", "purchase"),
                "logi": _e("intl_logistics_net", "intl_logistics"),
                "direct": _e("direct_costs_net", "direct_costs"),
                "md": _e("margin_income_net", "margin_income"),
                "other": _e("other_costs_net", "other_costs"),
                "op": _e("op_profit_net", "op_profit"),
                "deal": _e("deal_profit_net", "deal_profit"),
                "normMd": plan.get("margin_norm"), "normPf": plan.get("profit_norm"),
                "status": plan.get("status") or "", "executor": plan.get("executor") or "",
            }

        # --- тайминг стадий сделки в кат.0
        tl = dhist.get(did, [])
        timeline = []
        for i, (sid, ts) in enumerate(tl):
            nxt = tl[i + 1][1] if i + 1 < len(tl) else now_iso
            timeline.append({"stage": sid, "name": _stage_name(sid), "at": str(ts)[:16].replace("T", " "),
                             "days": round(_days_between(ts, nxt) or 0, 1)})
        entered_real = tl[0][1] if tl else ""
        cur_stage = str(d.get("STAGE_ID") or "")
        stage_days = None
        if tl:
            # возраст ТЕКУЩЕЙ стадии — от последнего перехода, если он и есть вход в
            # неё; первые входы врут, когда сделка вернулась в стадию, где уже была
            _ls = dlast.get(did)
            stage_days = round(_days_between(_ls[1] if _ls and _ls[0] == cur_stage else tl[-1][1],
                                             now_iso) or 0, 1)
        # «Оплата получена | Закрытие сделки» — успех (решение владельца 19.10.2026, п. 5):
        # работа и оплата закончены, сделка не «в работе», хотя стадия не финальная
        paid = in_cat0 and cur_stage in paid_stages and sem not in ("S", "F")
        done = sem == "S" or paid or (bool(ords) and all(_osem(o.get("stageId")) == "S" for o in ords))
        closed = sem in ("S", "F") or paid

        # --- заказы: стадия, возраст стадии, дедлайн клиенту, замедленность
        ords_det = []
        slow_orders = 0            # живых заказов сверх нормы шага регламента (р.д., «без пауз»)
        slow_old = 0               # … и прежним порогом 2× медианы — только для сравнения
        # люди сделки для хозяина шага (ork.owner): менеджер ОРК на сделке, КАМ, ОСС
        d_people = {"dealOrk": who(d.get(ORK_RESP))[0], "orkHead": ork_head,
                    "kam": who(d.get(KAM_F) or d.get(KAM_OLD))[0], "ossDeal": who(d.get(OSS_DEAL))[0]}
        late_orders = 0            # заказов с просроченным обещанием клиенту
        worst_late = None          # самая глубокая просрочка по сделке, дн
        late_buy = 0.0             # закупка под просроченными заказами, €
        late_money_days = 0.0      # €×дни закупки: сколько закуплено и как долго висит
        late_nosum = 0             # просроченных заказов без суммы (в деньги не попадают)
        late_rev = 0.0             # стоимость отгрузки, зависшей из-за просрочки, €
        late_rev_days = 0.0        # €×дни выручки — база расчёта упущенной доходности
        no_dl = 0                  # живых заказов без плановой даты клиенту
        first_order_ts = min((str(o.get("createdTime") or "") for o in ords), default="")
        n_live_ords = sum(1 for o in ords if _osem(o.get("stageId")) == "P")
        resp_gone = set()          # уволенные ответственные живых заказов (Responsible person)
        for o in ords:
            osid = str(o.get("stageId") or "")
            olive = _osem(osid) == "P"
            otl = ohist.get(str(o.get("id")), [])
            _ol = olast.get(str(o.get("id")))
            # вход в ТЕКУЩУЮ стадию: последний переход истории, иначе время последнего
            # движения карточки; первый вход в последнюю новую стадию — не то же самое
            oin = (_ol[1] if _ol and _ol[0] == osid
                   else str(o.get("movedTime") or (otl[-1][1] if otl else "") or o.get("createdTime") or ""))
            odays = round(_days_between(oin, now_iso) or 0, 1) if oin else None
            othr = _slow_threshold(osid, omed)
            slow_old += bool(olive and odays is not None and odays > othr)
            # замедлен — сверх нормы шага регламента (решение 26.10.2026, п. 2), а не 2× медианы.
            # ork.judged — одно правило с очередью, ведомостью и W2: шаг ork.step_now
            # (доставлено без УПД — шаг 12), гигиена и приёмка вне «Реализации» в счёт не идут;
            # «ждём УПД от клиента» (waitUtd, решение 27.10.2026, Б) — не простой: не замедлен
            lo = lo_by_id[str(o.get("id"))]
            ov = ork.judged(lo, today, in_cat0=in_cat0)
            ostep = ov["step"]
            lo["kamDeal"] = d_people["kam"]
            lo["own"] = own = ork.owner(ostep, {**d_people, "orkMgr": lo["orkMgr"], "ossOrder": lo["oss"]}) \
                if ostep else {"role": "", "who": "", "copy": "", "copyRole": ""}
            # закрытая выигранная сделка (в том числе «Оплата получена») — в счёте только шаг 12
            # (УПД, Рег. 14.2.4–14.3.2), проигранная — ничего: та же совокупность, что ведомость,
            # W2 и очередь (C12 orkview, lifecycle). Остальные шаги закрытой сделки — не
            # отклонения: ни красной строки заказа, ни «замедлена» в карточке
            ojudge = bool(olive and (not closed or (ostep == 12 and sem != "F")))
            oslow = bool(ojudge and ov["over"])
            ostep_closed = bool(olive and not ojudge and ostep and ov["norm"] is not None)
            if oslow:
                slow_orders += 1
            # обещание клиенту: та же дата, по которой снабжение ведёт «Контроль дедлайнов»
            dl = _pdate(o.get(DL_CUSTOMER))
            dl_days = (today - dl).days if dl else None       # >0 — просрочено, <0 — есть запас
            # доставлено клиенту, а заказ не закрыт — это закрытие (УПД, оплата), не просрочка;
            # даты доставки нет, а УПД подписан — доставлен по дате УПД (решение 25.10.2026)
            delivered = _pdate(o.get(DEL_FACT))
            del_utd = None if delivered else _utd_date(o, today)
            delivered = delivered or del_utd
            # срок клиента — только у открытой сделки: у закрытой просрочка не в счёте (как
            # «late» в lifecycle и признак строки «late»), иначе 🚩 в колонке «Срок» и красная
            # плашка заказа при строке «в срок»
            olate = bool(olive and not closed and dl_days is not None and dl_days > 0 and not delivered)
            obuy = o_eur[str(o.get("id"))] or 0.0     # курса нет — закупка заказа неизвестна
            # стоимость отгрузки этого заказа: доля сделки пропорционально закупке
            # (как в экономике проекта); если сумм закупки нет НИ У ОДНОГО заказа — поровну
            # между живыми. Заказ без суммы при суммах у других доли не получает: доли
            # заказов с суммой уже дают 100 % сделки (до 23.10.2026 выходило до 200 %)
            if buy_orders > 0:
                o_rev = sale * (obuy / buy_orders) if obuy > 0 else 0.0
            elif n_live_ords and olive:
                o_rev = sale / n_live_ords
            else:
                o_rev = 0.0
            if olate:
                late_orders += 1
                late_buy += obuy
                late_money_days += obuy * dl_days
                late_rev += o_rev
                late_rev_days += o_rev * dl_days
                if obuy <= 0:
                    late_nosum += 1
                worst_late = dl_days if worst_late is None else max(worst_late, dl_days)
            if olive and not dl and not delivered:
                no_dl += 1
            # ответственный заказа (Responsible person, assignedById СП-172) — хозяин исполнения
            # (решение владельца 19.10.2026, п. 1): уволен — живой заказ ведёт никто. Признак —
            # тот же справочник людей, что у ответственного сделки и ОСС (who)
            o_resp, o_resp_gone = who(o.get("assignedById"))
            if olive and o_resp_gone:
                resp_gone.add(o_resp)
            pend = _pdate(o.get(PROD_END))
            ords_det.append({
                "id": str(o.get("id")), "title": str(o.get("title") or "")[:70], "lbl": lo["lbl"],
                "ordNo": lo["ordNo"],
                "sup": supl.get(str(o.get("companyId"))) or "—",
                "stage": osid, "stageName": _stage_name(osid),
                "days": odays, "thr": round(othr, 1), "slow": oslow, "live": olive,
                "buyLbl": _money(obuy), "buyRaw": round(obuy),
                "created": str(o.get("createdTime") or "")[:10],
                "dl": dl.strftime("%d.%m.%Y") if dl else "",
                "dlDays": dl_days, "late": olate,
                "revEur": round(o_rev), "revLbl": _money(o_rev),
                "lossEur": round(o_rev * dl_days / 365 * CAP_RATE) if olate else 0,
                "burnDay": round(o_rev * CAP_RATE / 365, 1) if olive else 0,
                "city": str(o.get(CITY_F) or ""),
                "oss": who(o.get(OSS_F))[0],
                "ossGone": who(o.get(OSS_F))[1],
                # подпись ответственного заказа в строку не идёт: только признак ухода
                "respGone": o_resp_gone,
                "prodEnd": pend.strftime("%d.%m.%Y") if pend else "",
                # план производства заканчивается позже обещания клиенту — план сам себе противоречит
                "prodAfterDl": bool(pend and dl and pend > dl),
                "inb": (_pdate(o.get(INBOUND_PLAN)).strftime("%d.%m.%Y")
                        if _pdate(o.get(INBOUND_PLAN)) else ""),
                "delivered": delivered.strftime("%d.%m.%Y") if delivered else "",
                "delUtd": bool(del_utd),            # дата доставки — по подписи УПД
                # шаг регламента ОРК · исполнитель · р.д. на шаге / норма («без пауз»)
                "step": ostep, "stepRole": own["role"], "stepWho": own["who"],
                "stepWd": ov["wd"] if olive else None, "stepNorm": ov["norm"],
                # с какого дня считаны р.д. шага (МСК) и чем он дан (ork.clock): «назначение» —
                # приёмку закончило назначение менеджера ОРК, шаг 4 идёт с него
                "stepFrom": ov["since"] if olive else "", "stepFromBy": ov["sinceBy"] if olive else "",
                "stepOver": ov["over"] if ojudge else None,
                # шаг 12 больше 10 р.д. без УПД — «ждём УПД от клиента», не «сверх нормы»
                "stepWaitUtd": bool(ojudge and ov["waitUtd"]),
                # шаг живого заказа закрытой сделки вне счёта (не шаг 12 выигранной) — подпись
                # «сделка закрыта, не в счёте» вместо оценки срока
                "stepClosed": ostep_closed,
                # норма по регламенту, которую портал не меряет (шаг 6 — «нет данных»), и почему
                # срок не в счёте (гигиена, приёмка вне «Реализации», срок не посчитать)
                "stepNormDoc": ov["normDoc"], "stepNormState": ov["normState"],
                "stepHyg": bool(ojudge and ov["hyg"] and ov["norm"] is not None),
                # подпись закрытой сделки — только там, где есть норма: у шага без нормы
                # остаётся его собственное «почему» («шаг не нормируется», «нет данных»)
                "stepWhy": (("сделка закрыта: в счёте только шаг 12 (УПД)" if sem != "F" else "сделка проиграна")
                            if ostep_closed else ov["why"] if olive else ""),
            })
            if olive:
                # путь живого заказа по шагам регламента (ork.path, владелец 29.10.2026: «провалиться
                # в процесс, где зависло»): [[шаг, день входа ISO МСК, р.д. на шаге], …] — последняя
                # запись = step, stepFrom, stepWd; asg — назначение менеджера ОРК, которому верим
                # (ork.assign_at), utdD — дата подписания УПД (сырая). Ни имён, ни денег
                _od = ords_det[-1]
                _od["path"] = ork.path(lo, otrans.get(str(o.get("id"))) or [], ostages, today)
                _asg = ork.assign_at(lo)
                if _asg:
                    _od["asg"] = _asg.isoformat()
                if lo.get("utdRaw"):
                    _od["utdD"] = lo["utdRaw"].isoformat()
                _od.update(_order_card(lo, today, _fc_med, _fc_slip))
        # карточка: живые раньше, затем по номеру заказа («900/1», «900/2», «900/10»; владелец
        # 29.10.2026: «сделка одна — заказы разные 1/2/3»), без номера — в конце, по сроку
        ords_det.sort(key=lambda x: (not x["live"], x["ordNo"] is None, x["ordNo"] or 0,
                                     -(x["dlDays"] if x["dlDays"] is not None else -9999),
                                     -(x["days"] or 0)))

        # --- лаг заведения первого заказа (вход в кат.0 → первый заказ)
        first_lag = None
        if entered_real and first_order_ts:
            first_lag = round(_days_between(entered_real, first_order_ts) or 0, 1)
        elif entered_real and not ords and not closed:
            first_lag = round(_days_between(entered_real, now_iso) or 0, 1)  # заказа ещё нет — лаг растёт

        # --- замедленность сделки: сверх нормы шага регламента ОРК (решение владельца
        # 26.10.2026, п. 2) — живой заказ сверх нормы своего шага или сделка без заказа дольше
        # нормы приёмки (1 р.д.). Стадия сделки отдельно не нормируется: единица исполнения —
        # заказ (раздел 1 навыка), стадия сделки отстаёт от заказов
        dthr = _slow_threshold(cur_stage, dmed)
        no_order_wd = (period_mod.workdays_ru(_pdate(entered_real), today)
                       if (not closed and in_cat0 and not ords and entered_real) else None)
        no_orders_slow = bool(no_order_wd is not None and no_order_wd > ork.NORM_WD[1])
        late = bool(not closed and late_orders)
        # простой по стадии — только кандидат: «подвисла» решает чат сделки (ниже)
        stuck = bool(slow_orders > 0 or no_orders_slow)
        # прежний кандидат (2× медианы, неделя без заказа) — для «сумма прячет потерю»
        stuck_old = bool((not closed and in_cat0 and stage_days is not None and stage_days > dthr)
                         or slow_old > 0
                         or (not closed and in_cat0 and not ords and first_lag is not None
                             and first_lag > SLOW_MIN_DAYS))
        slow = stuck or late
        why = []
        if late:
            why.append(f"просрочено обещание клиенту: {late_orders} заказ(ов), макс. {worst_late} дн")
        if slow_orders:
            why.append(f"сверх нормы шага: {slow_orders} заказ(ов), {ork.NO_PAUSE}")
        if no_orders_slow:
            why.append(f"нет ни одного заказа {no_order_wd} р.д. после входа в реализацию")

        suppliers = sorted({supl.get(str(o.get("companyId"))) for o in ords if o.get("companyId")} - {None})
        entered = (str(entered_real)[:10] if entered_real
                   else min((str(o.get("createdTime") or "")[:10] for o in ords if o.get("createdTime")), default=""))
        dtitle = d.get("TITLE") or (ords[0].get("title") if ords else "")
        # дата выигрыша: «ТКП согласован» → первый вход в воронку реализации → первый заказ
        # (источник — рядом, правило 16)
        won_d = _pdate(d.get(WON_F))
        won_src = "tkp" if won_d else ("cat0" if entered_real else ("order" if entered else ""))
        won_d = won_d or _pdate(entered)
        # ячейка «шаг · исполнитель · р.д./норма»: заказ сверх нормы своего шага — первым
        # (почему строка «замедлена»), затем «ждём УПД от клиента» (шаг 12 больше 10 р.д. без
        # УПД, решение 27.10.2026, Б — не сверх нормы), иначе самый ранний шаг среди живых
        # заказов (там сделка и стоит); сделка без заказа — приёмка (шаг 1) от входа в
        # реализацию. У закрытой выигранной сделки (в том числе «Оплата получена») в счёте только
        # шаг 12 живых заказов (УПД, Рег. 14.2.4–14.3.2) — та же совокупность, что ведомость и
        # очередь (C12 orkview): «ждём УПД» — первым, с пометкой «и закрыть сделку». Иначе
        # «Оплата получена» или все заказы закрыты, а сделка открыта — шаг 13, ОСС закрывает
        # сделку (решение 26.10.2026, п. 4; dealClose / dealOpen очереди)
        d_step = _dstep(cur_stage) if in_cat0 else None
        _live_st = [x for x in ords_det if x["live"] and x["step"]]
        _cand = ([x for x in _live_st if x["step"] == 12] if (closed and sem != "F")
                 else [] if closed else _live_st)
        _all_done = bool(not closed and ords and all(_osem(o.get("stageId")) == "S" for o in ords))
        # оговорка к «закрыть сделку» (Рег. 15.6, 14.6): та же, что у пункта dealClose очереди
        _close_note = lifecycle.close_note([lo_by_id[str(o.get("id"))] for o in ords]) if paid else ""
        if _cand and (any(x["stepOver"] or x["stepWaitUtd"] for x in _cand) or not paid):
            c = min(_cand, key=lambda x: (not x["stepOver"], not x["stepWaitUtd"], x["step"]))
            step_cell = {"step": c["step"], "role": c["stepRole"], "who": c["stepWho"], "wd": c["stepWd"],
                         "norm": c["stepNorm"], "over": c["stepOver"], "waitUtd": c["stepWaitUtd"],
                         "normDoc": c["stepNormDoc"],
                         "normState": c["stepNormState"], "hyg": c["stepHyg"], "why": c["stepWhy"],
                         "label": ork.NO_PAUSE, "close": "paid" if paid else "", "closeNote": _close_note}
        elif paid or _all_done:
            _o = ork.owner(13, d_people)
            step_cell = {"step": 13, "role": _o["role"], "who": _o["who"], "wd": None, "norm": None,
                         "over": None, "waitUtd": False, "normDoc": "", "normState": "none", "hyg": False, "why": "",
                         "label": "закрыть сделку", "close": "paid" if paid else "open", "closeNote": _close_note}
        elif not closed and in_cat0 and not ords:
            _h = ork.owner(1, d_people)
            step_cell = {"step": 1, "role": _h["role"], "who": _h["who"], "wd": no_order_wd,
                         "norm": ork.NORM_WD[1], "over": no_orders_slow if no_order_wd is not None else None, "waitUtd": False,
                         "normDoc": ork.NORM_DOC[1][0], "normState": "norm", "hyg": False, "why": "",
                         "label": ork.NO_PAUSE, "close": "", "closeNote": ""}
        else:
            step_cell = None
        _dmax = [x for x in (deal_step_max.get(did), d_step) if x is not None]
        rows.append({
            "deal": did, "seq": seq, "entered": entered,
            "customer": clients.get(str(d.get("COMPANY_ID"))) or "—",
            "title": (dtitle or f"Сделка #{did}")[:90],
            # суммы в сделке нет — «суммы нет», а не «€0» (правило Р-2: ноль ≠ неизвестно)
            "saleEur": round(sale),
            "saleLbl": ("курса нет" if not sale_known else _money(sale) if sale else "суммы нет"),
            # откуда продажа: сверенный файл экономики (дата чтения) или «Сумма» Битрикса; у
            # файла «Сумма» в евро — рядом, для сверки
            "saleSrc": sale_src, "econAt": e["at"].strftime("%d.%m.%Y") if e else "",
            "saleBitrixEur": sale_bx,
            # курс и НДС (решения владельца 25 и 26.10.2026): источник курса «Суммы», дата КП и
            # откуда она, правило НДС и делитель (1,20 / 1,22; 1 — как есть), страна покупателя
            "rateSrc": K.src_of([sale_cur] + [str(o.get("currencyId") or "") for o in ords]),
            "kpDate": kp_day.strftime("%d.%m.%Y") if kp_day else "", "kpSrc": kp_src,
            "vatRule": vat_rule, "vatBx": vat_bx, "vatK": vat_k if vat_bx == "gross" else 1.0,
            "vatBuyer": ("ru" if vat_buyer == "РФ" else "foreign" if vat_buyer == "не РФ" else "noreq"),
            "planFxDay": plan_day.strftime("%d.%m.%Y") if (plan and plan_day) else "",
            # ₽ за € того курса, которым пересчитаны книга бюджета (planFxDay) и «Сумма» (kpDate):
            # премии возвращают € в ₽ ТЕМ ЖЕ курсом, а не курсом Битрикса (иначе ПЗ из книги
            # сдвинулась бы на разницу курсов, ~12 %: ревью 26.10.2026)
            "planEurRub": round(1 / rub, 6) if (plan and rub) else None,
            "kpEurRub": round(1 / rate_s["RUB"], 6) if rate_s.get("RUB") else None,
            # валюты без курса в справочнике Битрикса (сделка, заказы, поля экономики):
            # такие суммы не пересчитываются и в итоги не идут
            "fxMissing": fx_miss, "saleKnown": sale_known, "buySusp": buy_susp,
            # почему курса нет (только когда нет): bitrix — ЦБ не получен, в справочнике Битрикса
            # валюты нет; series — ряд этой валюты в сборке не получен; date — у ЦБ на дату нет
            **({"fxWhy": ("bitrix" if K.cbr is None else "series" if set(fx_miss) & K.failed else "date")}
               if fx_miss else {}),
            "saleOrig": _orig(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")), "saleCur": d.get("CURRENCY_ID") or "",
            # «Сумма» в валюте сделки, числом (то же, что saleOrig), и НДС в ней (TAX_VALUE):
            # dealctx.econ_ratio сверяет «Сумму» без НДС с финальной экономикой файла
            # (решение владельца 23.10.2026); не число — None, сверки нет
            "saleAmt": _amount(d.get("OPPORTUNITY")), "saleTax": _amount(d.get("TAX_VALUE")),
            "buyEur": round(buy),
            "buyLbl": ("курса нет" if buy_src == "fx" else "не сходится" if buy_susp
                       else _money(buy) if buy_src != "none" else "—"),
            "buySrc": buy_src, "buyOrdersEur": round(buy_orders), "buyEconEur": round(buy_econ),
            "discrep": discrep, "econLink": bool(econ_link), "noEcon": no_econ,
            "hasBudget": str(d.get(BUDGET_LINK) or "").startswith("http"),
            # округлённая продажа − округлённая закупка: «продажа − закупка = маржа» сходится
            # до евро и в строке, и в итогах
            "marginEur": round(sale) - round(buy) if margin is not None else None,
            "marginLbl": _money(margin) if margin is not None else "—",
            "marginPct": round(margin / sale * 100) if (margin is not None and sale) else None,
            # план из бюджета сделки: МД — после логистики, таможни и НДС; прибыль — итог
            "planMdEur": round(plan_md) if plan_md is not None else None,
            "planMdLbl": _money(plan_md) if plan_md is not None else "—",
            "planMdPct": plan_pct,
            "planProfitEur": round(plan_profit) if plan_profit is not None else None,
            "planProfitLbl": _money(plan_profit) if plan_profit is not None else "—",
            "planProfitPct": plan_ppct,
            "planRevEur": round(plan_rev) if plan_rev is not None else None,
            "planSusp": plan_susp, "planPSrc": plan_psrc, "planBreak": plan_break,
            "planMod": str((plan or {}).get("modified") or ""),
            "econPaid": _money(_parse_econ_money(d.get(ECON_PAID), rate_s)) if d.get(ECON_PAID) else "",
            "econRest": _money(_parse_econ_money(d.get(ECON_REST), rate_s)) if d.get(ECON_REST) else "",
            "norders": len(ords), "suppliers": ", ".join(suppliers)[:70],
            # разрезы, которые ведёт снабжение: ответственный сделки, схема поставки, город
            "manager": who(d.get("ASSIGNED_BY_ID"))[0] or "—",
            "oss": (", ".join(sorted({o["oss"] for o in ords_det if o["oss"]}))[:60]
                    or who(d.get(OSS_DEAL))[0] or "—"),
            # ОСС из поля сделки — хозяин пунктов очереди уровня сделки (lifecycle);
            # «oss» выше — склейка ОСС всех заказов, для таблицы
            "ossDeal": who(d.get(OSS_DEAL))[0],
            # КАМ клиента — из поля «КАМ» (запасное — старое поле), не ответственный сделки
            "kam": who(d.get(KAM_F) or d.get(KAM_OLD))[0],
            # id КАМа — ключ очереди КАМа (kamqueue.py): подписи реализации и пресейла
            # берутся из разных справочников и по имени не сводятся
            "kamId": uid_of(d.get(KAM_F) or d.get(KAM_OLD)),
            # сделку или её живые заказы ведёт уволенный: работа без хозяина
            "managerGone": who(d.get("ASSIGNED_BY_ID"))[1],
            "ossGone": (any(o["ossGone"] for o in ords_det if o["live"])
                        or (not any(o["oss"] for o in ords_det) and who(d.get(OSS_DEAL))[1])),
            # ответственный хотя бы одного живого заказа уволен (до 23.10.2026 не проверялся)
            "respGone": bool(resp_gone),
            "_respGone": resp_gone,
            "cities": ", ".join(sorted({o["city"] for o in ords_det if o["city"]}))[:50],
            "done": done, "closed": closed, "lost": sem == "F", "paidStage": paid,
            "inCat0": in_cat0,
            # отдельное направление (ОТДЕЛЬНЫЕ_ВОРОНКИ): номер его воронки; None — основной вид
            "seg": _segment(d),
            "stage": cur_stage,
            "stageName": _stage_name(cur_stage) if in_cat0 else "вне воронки",
            "stageDays": stage_days, "stageThr": round(dthr, 1),
            "slow": slow, "why": " · ".join(why), "stuck": stuck,
            "_chat": chat_activity.chat_id(d),
            # дедлайн клиенту (поле «Date of deadline to customer» заказов СП-172)
            "late": late, "lateN": late_orders, "lateDays": worst_late,
            "lateBuy": round(late_buy), "lateBuyLbl": _money(late_buy),
            "lateMoneyDays": round(late_money_days), "lateNoSum": late_nosum,
            "lateRev": round(late_rev), "lateRevLbl": _money(late_rev),
            "lateRevDays": round(late_rev_days),
            "lossEur": round(late_rev_days / 365 * CAP_RATE),
            "lossLbl": _money(late_rev_days / 365 * CAP_RATE),
            "burnDay": round(late_rev * CAP_RATE / 365),
            "noDl": no_dl,
            "dlNext": min((o["dl"] for o in ords_det if o["live"] and o["dl"]),
                          key=lambda s: s[6:] + s[3:5] + s[:2], default=""),
            "firstLag": first_lag,
            "timeline": timeline, "orders": ords_det,
            "hasSale": bool(d.get("OPPORTUNITY")),
            "wonAt": won_d.strftime("%d.%m.%Y") if won_d else "", "wonSrc": won_src,
            "payDays": _pay_days(d.get(PAY_DAYS_F)),
            # регламент ОРК: вход в реализацию, менеджер ОРК на сделке, шаг сделки по стадии и
            # наибольший по всем переходам, ячейка шага для таблицы сделок
            "cat0At": str(entered_real or ""), "orkResp": d_people["dealOrk"],
            # id людей сделки для whoId / escalateToId пунктов очереди (R4, Д-11а); снимается
            # до выдачи в страницу (ниже, вместе с _stuckOld)
            "_pid": {"dealOrk": uid_of(d.get(ORK_RESP)), "kam": uid_of(d.get(KAM_F) or d.get(KAM_OLD)),
                     "ossDeal": uid_of(d.get(OSS_DEAL))},
            "dealStep": d_step, "dealStepMax": max(_dmax) if _dmax else None,
            "stepCell": step_cell, "_stuckOld": stuck_old, "_stuckNew": stuck,
        })
    rows.sort(key=lambda r: -r["seq"])

    # --- аномально высокая валовая маржа = скорее всего заведены не все заказы поставщикам.
    # Порог берём от самих данных: полторы медианы по сделкам, где закупка собрана из заказов,
    # но не ниже 65% — ниже этого высокая маржа ещё правдоподобна.
    _gross = [r["marginPct"] for r in rows
              if r["buySrc"] == "orders" and r["marginPct"] is not None and r["saleEur"] > 1000]
    med_gross = _median(_gross)
    gross_thr = max(65.0, round((med_gross or 0) * 1.5, 1))
    for r in rows:
        r["grossSusp"] = bool(
            not r["closed"] and r["buySrc"] == "orders" and r["norders"] > 0
            and r["marginPct"] is not None and r["saleEur"] > 1000
            and r["marginPct"] > gross_thr)
        r["grossWhy"] = (f"валовая {r['marginPct']}% при типичной {round(med_gross or 0)}% — "
                         f"похоже, заведены не все заказы поставщикам "
                         f"(сейчас {r['norders']})") if r["grossSusp"] else ""
        # маржа при неполной закупке завышена — числом не показываем и в итог не берём
        # (ревизия 23.10.2026: у трети сделок с заказами закупка меньше 30 % продажи)
        if r["grossSusp"]:
            r["marginEur"], r["marginPct"], r["marginLbl"] = None, None, "закупка неполная?"

    # ПОДВИСЛА — только после чата (решение владельца 19.10.2026): простой по стадии
    # снимается, если в чате сделки за неделю писал человек; не прочитали чат —
    # простой не подтверждён и в замедленные не идёт. С 23.10.2026 решает и оценка
    # хода по чату (dealctx.decide): пишут, но ход за нами, стоит или откат — подвисла;
    # чат сборка не прочла, а прогон оценки читал его недавно — решает он
    cands = [(r["deal"], r["_chat"], r["saleEur"]) for r in rows if not r["closed"] and r["stuck"]]
    chat = (chat_reader.check(cands, today) if chat_reader is not None
            else chat_activity.check(client, cands, today, service_ids))
    chat_src: dict[str, str] = {}          # чем решено по кандидату (dealctx.decide) — по сделке
    resp_gone_by = {r["deal"]: r.pop("_respGone", set()) for r in rows}
    for r in rows:
        r.pop("_chat", None)
        ch = chat.get(r["deal"])
        if not ch:
            r["chat"] = None
            continue
        r["chat"] = ch
        stuck, note, _подробно, src = dealctx.decide(ch, (ctx_chats or {}).get(str(r["deal"])), today)
        chat_src[r["deal"]] = src
        if not stuck:
            r["stuck"] = False
            r["slow"] = bool(r["late"])
            # «не проверено» — не «всё хорошо»: серая корзина, сделка видна отдельным фильтром
            r["chatUnverified"] = stuck is None
        r["why"] += (" · " if r["why"] else "") + note

    # № реализации заказа — по строке его сделки (заказ без сделки — по своему названию)
    seq_by_deal = {r["deal"]: r["seq"] for r in rows}
    for lo in life_orders:
        lo["seq"] = seq_by_deal.get(lo["deal"]) or _regno(lo["title"])
    # Вид вкладки: основной и отдельного направления (решение владельца 25.10.2026). Очередь,
    # фазы, доставка в срок, хозяева и гарантия — по заказам и сделкам вида; нормы звеньев,
    # обычное опоздание и надёжность поставщиков меряются по ВСЕМ заказам (measure):
    # это свойства поставщиков и процесса, а не вида — и прогноз у заказа один в обоих видах
    sep_ids = {r["deal"] for r in rows if r["seg"]}
    main_rows = [r for r in rows if not r["seg"]]
    sep_rows = [r for r in rows if r["seg"]]
    main_orders = [o for o in life_orders if o["deal"] not in sep_ids]
    sep_orders = [o for o in life_orders if o["deal"] in sep_ids]
    # разбор договоров не загружен — None: «договор не найден» не проверялся (пункт так и пишет)
    cust_contract = dealctx.customer_contract_deals(ctx_contracts) if ctx_contracts is not None else None
    life = lifecycle.build(main_orders, main_rows, today, measure=life_orders, ork_head=ork_head,
                           cust_contract=cust_contract, ork_head_id=ork_head_uid or "")
    life_sep = (lifecycle.build(sep_orders, sep_rows, today, measure=life_orders, ork_head=ork_head,
                                cust_contract=cust_contract, ork_head_id=ork_head_uid or "")
                if sep_rows else None)

    # 7. скорость: сводные метрики
    # полный цикл заказа: создание карточки → SUCCESS, по заказам, закрытым за последние
    # BENCH_DAYS. Начало — дата создания карточки: у заказа, начатого до HIST_SINCE,
    # первая запись истории обрезана, и цикл выходил короче настоящего
    o_created = {str(o.get("id")): str(o.get("createdTime") or "") for o in orders}
    cycle_days = []
    for oid, entries in ohist.items():
        succ = next((t for s, t in entries if _osem(s) == "S"), None)
        if succ and entries and str(succ) >= bench_from:
            d0 = _days_between(o_created.get(oid) or entries[0][1], succ)
            if d0 is not None:
                cycle_days.append(d0)
    lag_vals = [r["firstLag"] for r in rows if r["firstLag"] is not None and r["norders"] > 0]
    med_cycle = _median(cycle_days)
    med_lag = _median(lag_vals)

    maxseq = max((r["seq"] for r in rows), default=0)
    # заказы без сделки и сводка поставщиков — по ЦБ на дату заказа; курса нет — не в сумме
    # (счёт рядом), а не 1:1 к базовой, как до 26.10.2026
    def _o_eur(o: dict) -> float | None:
        return K.eur(o.get("opportunity"), o.get("currencyId"), o_day(o, today))
    orphan_eur = [_o_eur(o) for o in orphan]
    orphan_buy = sum(v for v in orphan_eur if v)
    orphan_norate = sum(1 for v in orphan_eur if v is None)

    ork_month: dict[str, dict] = {}         # «Месяц» (отдел и поимённо) — по виду, под правом

    def _view(rows_v: list[dict], life_v: dict | None, orders_v: list[dict] | None = None,
              key: str = "main") -> dict:
        """Сводка вида вкладки по его сделкам rows_v: всё, что просит внимания или
        суммирует портфель, — плитки, итоги, воронка, сроки клиента, работа уволенных,
        чаты кандидатов в «подвисшие», сейчас в стадии и сверх нормы. Нормы стадий
        (медианы dmed, omed), медиана цикла и лага, порог валовой маржи — по всем
        сделкам и заказам: это мерило, а не портфель."""
        live_rows = [r for r in rows_v if not r["closed"]]
        slow_rows = [r for r in live_rows if r["slow"]]
        ids_v = {r["deal"] for r in rows_v}
        chat_sum = chat_activity.summary({k: v for k, v in chat.items() if k in ids_v})
        chat_by = Counter(src for k, src in chat_src.items() if k in ids_v)

        # текущая просрочка по стадиям: сколько живых сидит в стадии, сколько из них за порогом
        # и сколько дней в среднем теряется сверх порога
        o_now: dict[str, list] = defaultdict(lambda: [0, 0, 0.0])   # stage → [в стадии, просрочено, Σ дней сверх порога]
        d_now: dict[str, list] = defaultdict(lambda: [0, 0, 0.0])
        for r in rows_v:
            if not r["closed"] and r["inCat0"] and r["stageDays"] is not None:
                a = d_now[r["stage"]]
                a[0] += 1
                if r["stageDays"] > r["stageThr"]:
                    a[1] += 1
                    a[2] += r["stageDays"] - r["stageThr"]
            for o in r["orders"]:
                if o["live"] and o["days"] is not None:
                    a = o_now[o["stage"]]
                    a[0] += 1
                    if o["days"] > o["thr"]:
                        a[1] += 1
                        a[2] += o["days"] - o["thr"]

        def _now_stats(s: str, now: dict) -> dict:
            live_n, over_n, lost = now.get(s, [0, 0, 0.0])
            return {"live": live_n, "over": over_n,
                    "lostAvg": round(lost / over_n, 1) if over_n else 0}

        # бенчмарки стадий заказов для графика «где копится время» (только осмысленные стадии)
        bench = [{"stage": s, "name": _stage_name(s), "med": m, "n": len(odur[s]),
                  **_now_stats(s, o_now)}
                 for s, m in sorted(omed.items(), key=lambda kv: -kv[1])
                 if len(odur[s]) >= 5 and _osem(s) != "F"][:14]
        dbench = [{"stage": s, "name": _stage_name(s), "med": m, "n": len(ddur[s]),
                   **_now_stats(s, d_now)}
                  for s, m in dmed.items() if len(ddur[s]) >= 5 and s in dproc]
        dbench.sort(key=lambda x: dorder.get(x["stage"], 99))

        # --- ВОРОНКА: где стоят открытые сделки, на какие деньги и сколько с сорванным сроком ---
        fn_agg: dict[str, dict] = defaultdict(lambda: {"n": 0, "sale": 0.0, "late": 0,
                                                       "lateSale": 0.0, "slow": 0, "days": []})
        for r in live_rows:
            if not r["inCat0"]:
                continue
            a = fn_agg[r["stage"]]
            a["n"] += 1
            a["sale"] += r["saleEur"]
            if r["late"]:
                a["late"] += 1
                a["lateSale"] += r["saleEur"]
            if r["slow"]:
                a["slow"] += 1
            if r["stageDays"] is not None:
                a["days"].append(r["stageDays"])
        funnel = []
        for sid, nm in dproc.items():                   # только рабочие стадии, по порядку воронки
            a = fn_agg.get(sid)
            if not a or not a["n"]:
                continue
            funnel.append({"stage": sid, "name": nm, "n": a["n"],
                           "sale": round(a["sale"]), "saleLbl": _money(a["sale"]),
                           "late": a["late"], "lateSale": round(a["lateSale"]),
                           "slow": a["slow"], "med": dmed.get(sid, 0),
                           "nowMed": round(_median(a["days"]) or 0, 1)})

        # Итоги — по ОТКРЫТЫМ сделкам (до 23.10.2026 — по всем, с проигранными и закрытыми
        # за всю историю): «Σ продажи» стоит рядом с «В работе» и читается как портфель.
        # Сумма в валюте без курса и закупка, несоизмеримая с продажей, в итоги не идут —
        # их число показывается рядом.
        sale_sum = sum(r["saleEur"] for r in live_rows if r["saleKnown"])
        # закупка и маржа — ОДНО множество: сделки, где и продажа, и закупка достоверны (без
        # «не сходится» и «закупка неполная?»). До 25.10.2026 Σ закупки шла по своему множеству
        # (с закупкой без продажи), и «продажа − закупка» не давала маржу
        m_rows = [r for r in live_rows if r["marginEur"] is not None]
        margin_sum = sum(r["marginEur"] for r in m_rows)
        sale_m = sum(r["saleEur"] for r in m_rows)
        buy_sum = sum(r["buyEur"] for r in m_rows)
        n_nobuy = sum(1 for r in live_rows if r["buySrc"] == "none")
        # не вошли в Σ продажи: валюта суммы сделки без курса; в Σ маржи: ещё и закупка
        # в валюте без курса (заказы)
        n_fx = sum(1 for r in live_rows if not r["saleKnown"])
        n_buy_fx = sum(1 for r in live_rows if r["saleKnown"] and r["buySrc"] == "fx")
        n_nosale = sum(1 for r in live_rows if r["saleKnown"] and not r["saleEur"])
        # продажа и закупка из сверенного файла экономики (решение владельца 25.10.2026)
        n_file = sum(1 for r in live_rows if r["saleSrc"] == "file")
        n_buy_susp = sum(1 for r in live_rows if r["buySusp"])
        fx_codes = sorted({r["saleCur"] for r in live_rows if not r["saleKnown"] and r["saleCur"]})
        buy_fx_codes = sorted({c for r in live_rows if r["saleKnown"] and r["buySrc"] == "fx"
                               for c in r["fxMissing"]})
        n_m_file = sum(1 for r in m_rows if r["buySrc"] == "file")
        n_contracts = sum(1 for r in rows_v if r["norders"] > 0)
        n_budget = sum(1 for r in rows_v if r["hasBudget"])
        n_budget_live = sum(1 for r in live_rows if r["hasBudget"])

        # --- плановая экономика по бюджетам: суммы по ОТКРЫТЫМ сделкам с внятным бюджетом ---
        plan_live_rows = [r for r in live_rows if r["planMdEur"] is not None and not r["planSusp"]]
        plan_md_live = sum(r["planMdEur"] for r in plan_live_rows)
        # процент МД — от выручки бюджета, а не от суммы сделки в Битриксе: у сделки она
        # бывает нулевой или в валюте без курса, и процент завышался
        plan_sale_live = sum(r["planRevEur"] for r in plan_live_rows if r["planRevEur"])
        plan_profit_live = sum(r["planProfitEur"] for r in plan_live_rows if r["planProfitEur"] is not None)
        n_plan = sum(1 for r in rows_v if r["planMdEur"] is not None)
        n_plan_live = sum(1 for r in live_rows if r["planMdEur"] is not None)
        n_plan_susp = sum(1 for r in live_rows if r["planMdEur"] is not None and r["planSusp"])
        n_plan_neg = sum(1 for r in plan_live_rows if r["planMdEur"] < 0)

        # --- дедлайны клиенту: сводка по живым заказам (то же поле, что в «Контроле дедлайнов») ---
        late_rows = [r for r in live_rows if r["late"]]
        late_buy_sum = sum(r["lateBuy"] for r in late_rows)
        late_orders_n = sum(r["lateN"] for r in late_rows)
        nodl_orders_n = sum(r["noDl"] for r in live_rows)
        live_orders_n = sum(1 for r in live_rows for o in r["orders"] if o["live"])
        depth = {"<30": 0, "30-90": 0, "90-180": 0, ">180": 0}
        for r in live_rows:
            for o in r["orders"]:
                if o["late"]:
                    dd = o["dlDays"]
                    depth["<30" if dd < 30 else "30-90" if dd < 90 else "90-180" if dd < 180 else ">180"] += 1
        prod_after = sum(1 for r in live_rows for o in r["orders"] if o["live"] and o["prodAfterDl"])

        # --- ЦЕНА ПРОСРОЧКИ В ДЕНЬГАХ ---
        # 1) евро-дни: сколько денег закупки и сколько дней сверх обещания клиенту они висят.
        #    Это база: умножив на годовую ставку/365, получаем стоимость замороженного капитала.
        # 2) выручка и маржа сделок, где есть хоть один просроченный заказ — деньги, которые
        #    должны были закрыться, но ещё не закрылись (риск, а не потеря).
        money_days = sum(r["lateMoneyDays"] for r in late_rows)
        # ПОТЕРИ НА ИНВЕСТИЦИЯХ: стоимость отгрузки, которую клиент оплатил бы раньше,
        # умноженная на дни задержки и на ставку — упущенная доходность (методика владельца)
        rev_days = sum(r["lateRevDays"] for r in late_rows)
        loss_total = rev_days / 365 * CAP_RATE
        late_rev_total = sum(r["lateRev"] for r in late_rows)
        burn_day = late_rev_total * CAP_RATE / 365          # сгорает за каждый следующий день простоя
        # что отгружать первым: заказы с наибольшей ценой одного дня задержки
        ship_first = []
        for r in live_rows:
            for o in r["orders"]:
                if o["live"] and o["burnDay"] > 0:
                    ship_first.append({
                        "seq": r["seq"], "deal": r["deal"], "customer": r["customer"], "lbl": o["lbl"],
                        "title": o["title"], "sup": o["sup"], "stageName": o["stageName"],
                        "dl": o["dl"], "dlDays": o["dlDays"], "late": o["late"],
                        "revLbl": o["revLbl"], "burnDay": o["burnDay"],
                        "lossLbl": _money(o["lossEur"]), "lossEur": o["lossEur"]})
        ship_first.sort(key=lambda x: -x["burnDay"])
        ship_first = ship_first[:12]
        late_nosum_n = sum(r["lateNoSum"] for r in late_rows)
        rev_at_risk = sum(r["saleEur"] for r in late_rows)
        margin_at_risk = sum(r["marginEur"] for r in late_rows if r["marginEur"] is not None)
        avg_late = round(money_days / late_buy_sum, 1) if late_buy_sum else 0   # средневзвеш. просрочка, дн
        # топ сделок по евро-дням — где деньги стоят дольше и больше всего
        top_cost = sorted(late_rows, key=lambda r: -r["lateMoneyDays"])[:10]
        cost_top = [{"deal": r["deal"], "seq": r["seq"], "customer": r["customer"],
                     "days": r["lateDays"], "buyLbl": r["lateBuyLbl"], "buy": r["lateBuy"],
                     "moneyDays": r["lateMoneyDays"], "saleLbl": r["saleLbl"],
                     "manager": r["manager"], "oss": r["oss"]} for r in top_cost]

        # ЖИВАЯ РАБОТА УВОЛЕННЫХ: сделку ведёт ушедший ответственный, её живые заказы —
        # ушедший ОСС или ушедший ответственный заказа (Responsible person). Это не просрочка,
        # а работа без хозяина: её переназначают.
        gone_rows = [r for r in live_rows if r["managerGone"] or r["ossGone"] or r["respGone"]]

        def _gone_o(r, o):
            # заказ ждёт переназначения: уволен ответственный сделки (ведёт всё), ОСС или
            # ответственный этого заказа, или ОСС из поля сделки, когда у заказов своего нет
            return o["live"] and (r["managerGone"] or o["ossGone"] or o["respGone"]
                                  or (r["ossGone"] and not any(x["oss"] for x in r["orders"])))
        gone = {
            "deals": len(gone_rows),
            "byManager": sum(1 for r in gone_rows if r["managerGone"]),
            "byOss": sum(1 for r in gone_rows if r["ossGone"]),
            "byResp": sum(1 for r in gone_rows if r["respGone"]),
            "orders": sum(1 for r in gone_rows for o in r["orders"] if _gone_o(r, o)),
            "late": sum(1 for r in gone_rows if any(_gone_o(r, o) and o.get("late") for o in r["orders"])),
            "sale": _money(sum(r["saleEur"] for r in gone_rows)),
            "saleNum": round(sum(r["saleEur"] for r in gone_rows)),
            "people": len({r["manager"] for r in gone_rows if r["managerGone"]}
                          | {o["oss"] for r in gone_rows for o in r["orders"] if o["live"] and o["ossGone"]}
                          | {w for r in gone_rows for w in resp_gone_by.get(r["deal"], ())}
                          | {r["ossDeal"] for r in gone_rows if r["ossGone"] and r["ossDeal"]
                             and not any(o["oss"] for o in r["orders"])}),
        }

        deadlines = {
            "lateDeals": len(late_rows), "lateOrders": late_orders_n,
            "lateBuy": _money(late_buy_sum), "lateBuyNum": round(late_buy_sum), "depth": depth,
            "noDl": nodl_orders_n, "liveOrders": live_orders_n,
            "prodAfterDl": prod_after,
            "field": "Date of deadline to customer",
            "moneyDays": round(money_days), "avgLate": avg_late,
            "rate": round(CAP_RATE * 100),
            "revDays": round(rev_days), "lateRev": _money(late_rev_total),
            "loss": _money(loss_total), "lossNum": round(loss_total),
            "burnDay": _money(burn_day), "burnDayNum": round(burn_day),
            "shipFirst": ship_first,
            "revAtRisk": _money(rev_at_risk), "marginAtRisk": _money(margin_at_risk),
            "lateNoSum": late_nosum_n, "costTop": cost_top,
            # разрезы просрочки по ответственному и ОСС (byManager, byOss) сняты 27.10.2026:
            # страница их не рисовала — просрочку по людям показывает очередь по исполнителю шага
        }

        # аддитивно: все прежние KPI сохранены, новые добавлены следом
        kpis = [
            ("Последний №", str(maxseq), "макс. номер в реализации", "ok"),
            ("Контрактов", str(n_contracts), f"сделок с заказами · всего в воронке {len(rows_v)}", ""),
            ("Σ продажи", _money(sale_sum), "открытые сделки (выручка), €", "ok"),
            ("Σ закупки", _money(buy_sum),
             f"сделок с маржой {len(m_rows)}: файл экономики {n_m_file} · заказы СП-172 {len(m_rows) - n_m_file}"
             f" · без закупки {n_nobuy}", "amber"),
            ("Σ валовая маржа", _money(margin_sum),
             "продажа − закупка · БЕЗ логистики, таможни и НДС", "ok" if margin_sum >= 0 else "warn"),
            ("Ср. маржа", f"{round(margin_sum/sale_m*100) if sale_m else 0}%", "по сумме, валовая", ""),
            ("📋 План МД по бюджетам", _money(plan_md_live),
             f"{round(plan_md_live/plan_sale_live*100) if plan_sale_live else 0}% · "
             f"{len(plan_live_rows)} из {len(live_rows)} открытых · ПОСЛЕ логистики, таможни и НДС",
             "ok" if plan_md_live >= 0 else "warn"),
            ("В работе", f"{len(live_rows)}", "открытых сделок в воронке", ""),
            ("⚠ Замедлено", str(len(slow_rows)),
             f"{round(len(slow_rows)/len(live_rows)*100) if live_rows else 0}% открытых", "warn" if slow_rows else "ok"),
            ("Медиана цикла заказа", f"{round(med_cycle) if med_cycle else '—'} дн", "создание → закрытие заказа", ""),
            ("🚩 Просрочен дедлайн", str(len(late_rows)),
             f"сделок · заказов {late_orders_n} · закупка {_money(late_buy_sum)}", "warn" if late_rows else "ok"),
            ("💸 Потери от просрочки", _money(loss_total),
             f"упущенная доходность при {round(CAP_RATE*100)}% годовых · сгорает {_money(burn_day)}/день",
             "warn" if loss_total else "ok"),
        ]

        # «сумма прячет потерю»: кандидаты в «Замедлено» (до чата) по норме шага против прежних
        # 2× медианы — у скольких открытых сделок картина стала хуже и у скольких лучше
        _open = [r for r in live_rows if r["inCat0"]]
        slow_cmp = {"old": sum(1 for r in _open if r["_stuckOld"]), "new": sum(1 for r in _open if r["_stuckNew"]),
                    "worse": sum(1 for r in _open if r["_stuckNew"] and not r["_stuckOld"]),
                    "better": sum(1 for r in _open if r["_stuckOld"] and not r["_stuckNew"])}
        # регламент ОРК: неделя, месяц, квартал, как считается (orkview.py); поимённые
        # «Месяц» (показатели отдела и менеджеров поимённо) — отдельно (ork_month), в страницу
        # только под правом руководителей ОРК
        ork_v = None
        if life_v is not None and orders_v is not None:
            ork_v, ork_month[key] = orkview.build(orders_v, rows_v, life_v.get("queue") or [], today,
                                                  measure=life_orders, otd=life_v.get("otd"),
                                                  ork_head=ork_head)
            ork_v["slowCmp"] = slow_cmp

        return {
            "funnel": funnel,
            # сводка для верхнего блока — чтобы вёрстка не разбирала подписи KPI строками
            "head": {
                "sale": _money(sale_sum), "margin": _money(margin_sum), "marginNum": round(margin_sum),
                "marginPct": round(margin_sum / sale_m * 100) if sale_m else 0,
                "live": len(live_rows), "total": len(rows_v), "maxseq": maxseq,
                "contracts": n_contracts, "buy": _money(buy_sum), "buyNum": round(buy_sum),
                # множество Σ закупки и Σ маржи: сделок и их продажа (saleM − buy = margin)
                "nM": len(m_rows), "saleM": _money(sale_m), "saleMNum": round(sale_m),
                "nNoBuy": n_nobuy, "nBuyFx": n_buy_fx, "buyFxCodes": buy_fx_codes,
                "nMFile": n_m_file,       # из них закупка из сверенного файла экономики
                # не вошли в итоги: сумма в валюте без курса; закупка несоизмерима с продажей
                "nFx": n_fx, "fxCodes": fx_codes, "nBuySusp": n_buy_susp, "sumsOver": "live",
                "nNoSale": n_nosale,      # открытых сделок без суммы в Битриксе
                "nFile": n_file,          # открытых сделок с продажей из сверенного файла
                "medCycle": round(med_cycle) if med_cycle else None,
                "medLag": round(med_lag, 1) if med_lag is not None else None,
                "loss": _money(loss_total), "burnDay": _money(burn_day),
                "budget": n_budget, "budgetLive": n_budget_live,
                "grossThr": gross_thr, "grossMed": round(med_gross or 0),
                "grossSusp": sum(1 for r in live_rows if r["grossSusp"]),
                "slow": len(slow_rows), "lateDeals": len(late_rows),
                # чат сделки: сколько кандидатов в «подвисшие» проверено и что нашли
                "chat": chat_sum, "chatQuietDays": chat_activity.QUIET_DAYS,
                # чем решено по кандидатам (dealctx.decide): quiet, active, unverified — как
                # раньше; model — по оценке хода, ctxQuiet и ctxActive — по чтению прогона оценки
                "chatBy": dict(chat_by),
                # сделки на «Оплата получена | Закрытие сделки» — успех, не «в работе»
                "paidStage": sum(1 for r in rows_v if r.get("paidStage")),
                # план по бюджетам сделок (снимок Google Drive)
                "planMd": _money(plan_md_live), "planMdNum": round(plan_md_live),
                "planMdPct": round(plan_md_live / plan_sale_live * 100) if plan_sale_live else 0,
                "planProfit": _money(plan_profit_live), "planProfitNum": round(plan_profit_live),
                "planN": len(plan_live_rows), "planLive": n_plan_live, "planTotal": n_plan,
                "planSusp": n_plan_susp, "planNeg": n_plan_neg, "planAsOf": plan_asof,
            },
            "kpis": [{"lbl": l, "val": v, "meta": m, "clz": c} for l, v, m, c in kpis],
            "deadlines": deadlines,
            "speed": {
                "medCycle": round(med_cycle, 1) if med_cycle else None,
                "medLag": round(med_lag, 1) if med_lag is not None else None,
                "orderBench": bench,          # медианы стадий заказов («где копится время»)
                "dealBench": dbench,          # медианы стадий сделки в кат.0
                "slowMin": SLOW_MIN_DAYS, "slowMult": SLOW_MULT,
                # окно бенчмарков и сколько стадий посчитано по нему, а не по всей истории
                "benchDays": BENCH_DAYS,
                "benchRecent": sum(1 for v in odur_recent.values() if len(v) >= BENCH_MIN)
                               + sum(1 for v in ddur_recent.values() if len(v) >= BENCH_MIN),
            },
            "gone": gone,
            "life": life_v,
            "ork": ork_v,
        }

    main_view = _view(main_rows, life, main_orders, "main")
    sep_view = _view(sep_rows, life_sep, sep_orders, "sep") if sep_rows else None
    for r in rows:                         # кандидаты «Замедлено» — только для сравнения выше
        r.pop("_stuckOld", None)
        r.pop("_pid", None)
        r.pop("_stuckNew", None)
    if sep_cats:
        # сводка направления для шапки основного вида и подписи его вкладки: имя — имя
        # воронки из портала; числа — те же правила, что у основного вида. Сделок
        # направления нет (закрылись, ушли из вселенной вкладки) — сводка всё равно есть,
        # с нулями: вкладка остаётся и говорит «Нет данных», а не пропадает молча (portal-ui §2)
        sep_head = sep_view["head"] if sep_view is not None else {}
        cats = sorted(sep_cats, key=int)
        try:
            cat_names = client.categories()
        except Exception:                    # noqa: BLE001 — подпись, без неё вид тот же
            cat_names = {}
        sep_late = (life_sep or {}).get("counts", {}).get("late", 0)
        main_view["head"]["sep"] = {
            "cat": cats[0], "cats": cats,
            "name": ", ".join(str(cat_names.get(c) or f"воронка {c}") for c in cats),
            "deals": len(sep_rows), "live": sep_head.get("live", 0),
            "orders": sum(1 for r in sep_rows if not r["closed"] for o in r["orders"] if o["live"]),
            "late": sep_late, "lateDeals": sep_head.get("lateDeals", 0),
            "queue": len((life_sep or {}).get("queue") or []),
            "sale": sep_head.get("sale", _money(0)),
            "companies": len({c for c, k in sep_company.items() if k in cats}),
        }

    # --- агрегация по ПОСТАВЩИКАМ (без изменений) ---
    sup_agg: dict[str, dict] = defaultdict(lambda: {"buy": 0.0, "norders": 0, "noRate": 0, "deals": set(),
                                                    "clients": set(), "last": "", "curs": set()})
    for o in orders:
        sid = str(o.get("companyId") or "")
        if not sid:
            continue
        a = sup_agg[sid]
        ov = _o_eur(o)
        if ov is None:
            a["noRate"] += 1
        a["buy"] += ov or 0.0
        a["norders"] += 1
        did = str(o.get("parentId2") or "")
        if did:
            a["deals"].add(did)
            cl = clients.get(str(deals.get(did, {}).get("COMPANY_ID")))
            if cl:
                a["clients"].add(cl)
        ct = str(o.get("createdTime") or "")[:10]
        if ct > a["last"]:
            a["last"] = ct
        if o.get("currencyId"):
            a["curs"].add(o.get("currencyId"))
    sup_total = sum(a["buy"] for a in sup_agg.values()) or 1.0
    sup_rows = sorted(({
        "id": sid, "name": supl.get(sid) or f"company #{sid}",
        "buy": round(a["buy"]), "buyLbl": _money(a["buy"]),
        "share": round(a["buy"] / sup_total * 100, 1),
        "norders": a["norders"], "ncontracts": len(a["deals"]), "nclients": len(a["clients"]),
        "clients": ", ".join(sorted(a["clients"]))[:80],
        "avg": _money(a["buy"] / a["norders"] if a["norders"] else 0),
        "last": a["last"], "curs": ", ".join(sorted(a["curs"])),
        # заказов без курса на дату заказа — не в обороте; только когда есть: нулевое поле у
        # каждого поставщика живая сверка судит как «новое поле пришло пустым»
        **({"noRate": a["noRate"]} if a["noRate"] else {}),
        # отгрузка к своей же плановой дате за год (lifecycle): выбор поставщика
        # сорсингом опирается на то, как он реально отгружает
        # доля «в срок» — от SUP_MIN отгрузок, как в рейтинге пульта: 0 % из одной
        # отгрузки на день позже — не оценка поставщика
        "otN": (life["supAll"].get(supl.get(sid) or "") or {}).get("n", 0),
        "otPct": ((life["supAll"].get(supl.get(sid) or "") or {}).get("pct")
                  if (life["supAll"].get(supl.get(sid) or "") or {}).get("n", 0) >= lifecycle.SUP_MIN else None),
        "otSlip": (life["supAll"].get(supl.get(sid) or "") or {}).get("medSlip"),
    } for sid, a in sup_agg.items()), key=lambda r: -r["buy"])
    top10 = sum(r["buy"] for r in sup_rows[:10])
    sup_kpis = [
        ("Поставщиков", str(len(sup_rows)), "с заказами (СП-172)", ""),
        ("Σ оборот", _money(sup_total), "сумма закупок, €", "amber"),
        ("Топ-10 доля", f"{round(top10 / sup_total * 100)}%", "концентрация закупок", "warn" if top10 / sup_total > 0.6 else ""),
        ("Ср. на поставщика", _money(sup_total / len(sup_rows) if sup_rows else 0), "€/поставщик", ""),
        ("Заказов всего", str(sum(r["norders"] for r in sup_rows)), "закупок", ""),
        ("Разовых", str(sum(1 for r in sup_rows if r["norders"] == 1)), "поставщиков с 1 заказом", "amber"),
    ]
    fx_head = _fx_head(K, rate_asof, base_cur, today, fx_info, fx_why, rows,
                       kp_err=kp_err, req_err=req_err, n_need_offer=len(need_offer), n_offers=len(offers),
                       n_tkp_create=n_tkp_create, n_buyers=len(buyer_co), n_country=len(country),
                       calls=calls, n_plan_norate=n_plan_norate)
    return {
        "label": f"на {today.strftime('%d.%m.%Y')}",
        # все сделки вкладки — и основного вида, и отдельных направлений (признак seg):
        # премии, очередь КАМа и письма о новых сделках читают их все
        "rows": rows,
        # файлы шагов регламента (ork.ARTIFACTS) — подписи к rows[].orders[].art в карточке заказа
        "artCat": ART_CAT,
        **{k: main_view[k] for k in ("funnel", "head", "kpis", "deadlines")},
        # источник курса (ЦБ РФ на дату КП или курсы Битрикса с плашкой), период дат пересчёта,
        # дата КП и НДС по правилу — счётчиками (_fx_head)
        "fx": fx_head,
        "speed": main_view["speed"],
        "gone": main_view["gone"],
        "life": life,
        # вид отдельного направления (решение владельца 25.10.2026): те же разделы по его
        # сделкам; сводка для шапки и подписи вкладки — head["sep"]
        # «Квартал» мерится по ВСЕМ заказам — у направления он тот же, что у основного вида: в
        # данные вида не копируется (страница берёт его из основного, quarterFrom)
        "sep": ({**{k: sep_view[k] for k in ("funnel", "head", "kpis", "deadlines", "speed", "gone", "life")},
                 "ork": ({**sep_view["ork"], "quarter": None, "quarterFrom": "main"} if sep_view.get("ork")
                         else sep_view.get("ork"))}
                if sep_view is not None else None),
        # регламент ОРК основного вида (вид направления — sep.ork)
        "ork": main_view["ork"],
        # «Месяц» — показатели отдела и менеджеров ОРК поимённо (решение владельца 26.10.2026,
        # п. 3: подвкладка — только руководителям и владельцу): main.py забирает раздел до
        # записи страницы — в страницу только своим массивом под правом руководителей ОРК
        "orkMonth": ork_month,
        # руководитель ОРК по справочнику отделов — хозяин пунктов dealctx (main.py забирает)
        "_orkHead": ork_head,
        # заказов без курса на дату заказа — не в сумме, счётом (поле — только когда есть)
        "orphan": {"n": len(orphan), "buy": _money(orphan_buy),
                   **({"noRate": orphan_norate} if orphan_norate else {})},
        "suppliers": {
            "label": f"на {today.strftime('%d.%m.%Y')}",
            "rows": sup_rows,
            "kpis": [{"lbl": l, "val": v, "meta": m, "clz": c} for l, v, m, c in sup_kpis],
        },
    }


def _fx_head(K: Курсы, rate_asof: str, base_cur: str, today: dt.date, info: dict, why: str,
             rows: list[dict], **kw) -> dict:
    """Шапка курсов «Реализации»: источник, период дат пересчёта и запасной путь.

    src — cbr (ряды cbr.ru), mirror (зеркало курсов ЦБ), bitrix (ЦБ недоступен или не
    запрашивался: справочник Битрикса; fallback — плашка и счёт сделок). usdRub и eurUsd —
    по справочнику Битрикса, как до 25.10.2026: их читают премии (premia.compute), а перевод
    премий на ЦБ — отдельная правка; cbrUsdRub — сегодня по ЦБ, для шапки. asOf у ЦБ — день
    сборки, у Битрикса — дата правки справочника; bitrixAsOf — всегда дата правки
    справочника. Только коды и счётчики: сумм и номеров сделок здесь нет."""
    now, bx = K.at(today), K.bitrix
    f = lambda d: d.strftime("%d.%m.%Y")  # noqa: E731
    info = info or {}
    out = {
        "src": K.src, "asOf": f(today) if K.cbr is not None else rate_asof, "base": base_cur,
        "bitrixAsOf": rate_asof,
        "usdRub": round(bx["USD"] / bx["RUB"], 2) if bx.get("RUB") and bx.get("USD") else None,
        "eurUsd": round(bx.get("EUR", 0) / bx["USD"], 4) if bx.get("USD") else None,
        "cbrUsdRub": (round(now["USD"] / now["RUB"], 2)
                      if K.cbr is not None and now.get("RUB") and now.get("USD") else None),
        "lo": f(min(K.days)) if K.days else "", "hi": f(max(K.days)) if K.days else "",
        "requests": int(info.get("requests") or 0),
        "noCode": sorted(info.get("noCode") or []), "failed": sorted(info.get("failed") or []),
        "fallback": ({"n": sum(1 for r in rows if r.get("rateSrc") == "bitrix"), "why": why,
                      "err": str(info.get("why") or "")} if K.cbr is None else None),
        # без курса на свою дату (валюты нет у ЦБ, дата раньше ряда): сумма неизвестна
        "noRate": sum(1 for r in rows if r.get("fxMissing")),
        "kpSrc": dict(Counter(r.get("kpSrc") or "" for r in rows)),
        "vat": dict(Counter(r.get("vatRule") or "" for r in rows)),
        "vatBuyer": dict(Counter(r.get("vatBuyer") or "" for r in rows if r.get("vatRule") == "gross")),
        "kpErr": kw.get("kp_err") or "", "reqErr": kw.get("req_err") or "",
        "nNeedOffer": kw.get("n_need_offer", 0), "nOffers": kw.get("n_offers", 0),
        "nTkpAtCreate": kw.get("n_tkp_create", 0),
        "nBuyers": kw.get("n_buyers", 0), "nCountry": kw.get("n_country", 0),
        # ряд валюты cbr.ru не отдал, взят с зеркала (patched); не получен нигде — по курсу
        # Битрикса (partial.bx, строки rateSrc «bitrix») или сумма неизвестна (partial.lost)
        "patched": sorted(info.get("patched") or []),
        "partial": ({"codes": sorted(K.failed), "bx": sorted(K.patch),
                     "n": sum(1 for r in rows if r.get("rateSrc") == "bitrix"),
                     "lost": sum(1 for r in rows if set(r.get("fxMissing") or []) & K.failed)}
                    if K.failed else None),
        # время на курсы вышло / даты-опечатки / зеркало: недочитано и сбоев — счётчики
        "timeUp": bool(info.get("timeUp")), "badDays": int(info.get("badDays") or 0),
        "mirrorCut": int((info.get("mirror") or {}).get("обрезано") or 0),
        "mirrorErr": int((info.get("mirror") or {}).get("ошибок") or 0),
        # книга бюджета есть, курса ЦБ на её дату нет — план не пересчитан (счётом)
        "nPlanNoRate": int(kw.get("n_plan_norate") or 0),
        # отдельные направления (Басра, воронка 16): покупатель без реквизитов — иностранный,
        # «Сумма» как есть (решение владельца 26.10.2026) — счётом
        "nVatNoreqSep": sum(1 for r in rows if r.get("seg") and r.get("vatBx") == "foreign"
                            and r.get("vatBuyer") == "noreq"),
        # запросов к порталу у новых чтений: история КП и кат. 0, реквизиты покупателей
        "calls": dict(kw.get("calls") or {}),
    }
    if K.cbr is None:
        out["lbl"] = f"курсы Битрикса ({why}) от {rate_asof or '—'}"
    else:
        src = "ЦБ РФ" if K.src == "cbr" else "ЦБ РФ (зеркало cbr-xml-daily)"
        out["lbl"] = (f"{src} на дату КП, заказы — на дату заказа"
                      + (f" ({out['lo']}–{out['hi']})" if out["lo"] else ""))
    return out


def life_all(data: dict | None) -> dict | None:
    """Раздел «life» основного вида вместе с очередью отдельных направлений — для тех, кто
    смотрит не вкладку, а людей и сделки: очередь КАМа (kamqueue.py) — его клиент тот же,
    разговор о сроке и оплате никуда не делся; «что делать первым» по сделке (dealctx).
    Пункты те же, что до 25.10.2026 в одной очереди: нормы обоих видов — по всем заказам."""
    if not data or data.get("life") is None:
        return None
    life = data["life"]
    sep_life = (data.get("sep") or {}).get("life")
    if not sep_life:
        return life
    # порядок — тот же, что у очереди вида (lifecycle.group_sort: пункты сделки подряд, по № заказа)
    queue = list(life.get("queue") or []) + list(sep_life.get("queue") or [])
    lifecycle.group_sort(queue)
    counts = Counter(life.get("counts") or {})
    counts.update(sep_life.get("counts") or {})
    return {**life, "queue": queue, "counts": dict(counts)}
