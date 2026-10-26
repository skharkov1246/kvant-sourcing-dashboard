"""Доход КАМов и продуктов — подменю «Доход» вкладки «Key-аккаунты и продукты».

ЗАЧЕМ (просьба владельца 25.10.2026). Где формируется доход по ключевым клиентам и
продуктам: что уже получено, что придёт по выигранным сделкам реализации и когда, что
под угрозой, и что могут принести невыигранные сделки. Модуль — чистые функции поверх
уже посчитанного (contracts, lifecycle, people, presale, kamqueue, premia): портал он
не читает.

РЕШЕНИЯ ВЛАДЕЛЬЦА 25.10.2026 (docs/agents/DECISIONS.md), которые здесь в коде:
  • деньги — только у сделок, сверенных файлом экономики: строка «Реализации» с
    saleSrc == "file" (класс A). Остальные — класс C: число, причина, хозяин, действие,
    без евро. saleSrc нет (до «файла экономики первым» в contracts.compute) — "bitrix";
  • «Получено» (K1) — стадия «Оплата получена | Закрытие сделки» (contracts.PAID_RX,
    paidStage); дата — день сборки минус дни в стадии; нарастающим за квартал: закрытая
    потом сделка из K1 не уходит (дата — вход в «Оплата получена» по пути стадий); нет
    платёжки клиента во всех непроигранных заказах — «не подтверждено платёжкой».
    Платёжка во всех заказах без перевода стадии — «оплачено по платёжке» (paidFile): ни в
    K1, ни в K2, пункт «перевести стадию»;
  • сумма в денежной плитке — только при доле класса A не ниже COVER_MIN (70 %) сделок
    блока, иначе «в цифрах n из N»; по сделке значение класса A показывается всегда;
  • все деньги — отдельным массивом (compute → money), его воркер оставляет только праву
    «Деньги КАМов» (kam_money) и админу. Счёт, даты, стадии и действия — открытый массив;
  • линия продукта — «Категория оборудования» сделки, без прокси по отделу; нет — «линия
    не указана» и пункт «проставить категорию»;
  • план по людям — этап 2 (вводит владелец в /admin), личный фильтр по зрителю — этап 2.

ЕДИНИЦЫ.
  • Продажа — saleEur строки «Реализации» (у класса A — из файла экономики).
  • R — «результат по нормативу положения ред. 2» (не «прибыль»): premia.result_of над
    ПЗ из premia.pz_of (закупка из файла или заказов × 1,10 → прямые затраты книги).
    Поправка премий берётся только при калибровке не меньше чем по CALIB_MIN сделкам —
    иначе R нет нигде.
  • Одно правило атрибуции — credit(): КАМ — поле «КАМ» (→ «KAM (старое)») → ответственный,
    если по должности он КАМ («прокси») → «КАМ не указан»; уволенный в поле — «не
    закреплено»; продукт-оунер — «Product leader»; исполнитель — ответственный. Источник
    роли хранится в строке (правило 16). Человеку сделка засчитывается целиком по каждой
    его роли; итог компании — по сделкам, суммы разных ролей не складываются.

KPI (дизайн «Доход», 25.10.2026): K1 получено · K2 к получению по выигранным (по дате
ожидаемой оплаты — lifecycle.pay_eta) · K3 под угрозой (задержано клиентом / срок под
риском — не складываются) · K4 выигрыши квартала · K5 пайплайн невыигранных по классу
стадии (тихие потери — вне пайплайна) · K6 в цифрах · K7 моя часть по положению
(только в закрытом массиве).

ДВОЙНАЯ ПРОВЕРКА (правило 26.09.2026) — разными путями: Σ R по сделкам = Σ по людям
через доли пула (вместе с «не закреплено») с точностью до €1; Σ частей K7 = Σ пулов;
итоги плиток = Σ опубликованных значений сделок по корзине; счёт плиток = число сделок
списка; Σ по строкам каждого разреза (с «без КАМа», «не закреплено», «линия не указана»)
= итог — по числу сделок и по деньгам. Любое расхождение — деньги в этой сборке не
публикуются (money = None). K1 по стадии против K1 по платёжкам — пометка (poLow).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

import contracts as contracts_mod
import history
import kam as kam_mod
import kamqueue
import lifecycle
import premia
import presale as presale_mod

COVER_MIN = 0.70          # K6: сумма в плитке — при доле сверенных файлом сделок не ниже (решение 25.10, п. 7)
CALIB_MIN = 30            # поправка премий — только при калибровке по стольким сделкам, иначе R нет
QUIET_DAYS = history.STUCK_DAYS   # «тихая потеря»: больше 120 дн. без смены стадии и без людей в чате
ACTIONS_MAX = 15          # пунктов «что открыть или сдвинуть» на человека
LADDER_BACK, LADDER_FWD = 3, 12   # лестница по месяцам: 3 назад (получено), 12 вперёд (к получению)
HOLDINGS_TOP = 15         # холдингов в разрезе; остальные — «прочие клиенты»
TOL_EUR = 1.0             # допуск двойной проверки денег (на сделку: суммы — из округлённых до €1)
PO_CONFIRM_MIN = 0.50     # K1 по стадии против K1 по платёжкам: ниже этой доли — пометка (не гейт: файл
                          # платёжки в заказе заполняют не всегда, гейт снимал бы деньги каждой сборки)

KAM_NONE = kamqueue.KAM_NONE
GONE = "не закреплено (уволен)"
PO_NONE = "Product leader не указан"
LINE_NONE = "линия не указана"
HOLD_NONE = "без клиента"
HOLD_OTHER = "прочие клиенты"
GONE_KEY = "-"            # ключ строки «не закреплено» в разрезах людей

# классы стадии невыигранной сделки (K5) — порядок воронки
STAGE_CLASSES = (("pre", "до ТКП"), ("offer", "ТКП выдано"), ("tender", "торги и переторжка"),
                 ("wait", "ожидание решения"), ("spec", "финальная спецификация"))
# Имена стадий клиентских воронок — дословно как в scripts/probe_conversion.py (зонд
# конверсии ТКП → контракт; совпадение сверяет tests/test_kaminc.py — правило 14: копия
# правится вместе с источником). Стадии «Пресейла» — по таблицам мяча presale.BALL_RULES и
# порогов КАМа kamqueue.BALL_KAM, в одном месте — stage_class().
ПРЕДЛОЖЕНИЕ = re.compile(
    r"ТКП\s*(выда|отправл|соглас)|тендерн\w*\s+предложен\w*\s+выда|quotation\s+issued|"
    r"(?<![А-Яа-яЁё])КП\s*(отправл|выда|направл)|коммерческ\w*\s+предложен\w*\s+(выда|отправл|направл)|"
    r"предложени\w*\s+(направл|отправл)\w*\s+клиент|заявк\w*\s+подан", re.I)
ЕЩЁ_НЕ_ВЫДАНО = re.compile(r"ТКП\s*готов|отправка\s+ТКП|подготовк\w*\s+ТКП|сбор\s+КП|поиск\s|не\s*выда|"
                           r"не\s*ответ|не\s*присл|закрыл\s+выдачу|неинтересно", re.I)
ПОСЛЕ_ПРЕДЛОЖЕНИЯ = re.compile(r"переторжк|(?<![А-Яа-яЁё])торги(?![А-Яа-яЁё])|ожидаем\w*\s+решени", re.I)
# классы стадий с мячом у КАМа — по строкам kamqueue.BALL_KAM, в их порядке
_BALL_CLASS = ("pre", "offer", "tender", "wait", "spec")
_KAM_RX = [re.compile(rx, re.I) for rx, *_x in kamqueue.BALL_KAM]
_WAIT_RX, _SPEC_RX = _KAM_RX[3], _KAM_RX[4]

# коды очереди «Реализации», которые делают деньги K2 «под угрозой» (K3): два вида, не складываются
DELAY_CODES = ("unpaid", "noUtd")                          # задержано клиентом
RISK_CODES = ("late", "risk", "forecast", "planAfterDl")   # срок под риском

# пробелы денег — пункты «что открыть или сдвинуть» сверх очереди КАМа: код → (важность, что, что сделать)
GAPS = {
    "econ": (20, "«Сумма» не сверена с файлом экономики", "сверить «Сумму» сделки с файлом «Экономика проекта»"),
    "noBuy": (21, "нет закупки: заказ поставщику не заведён или без суммы", "завести заказ поставщику или его сумму"),
    "noPayPlan": (22, "доставлено, нет планового срока оплаты", "внести плановый срок оплаты из договора"),
    "moveStage": (23, "платёжка клиента во всех заказах, стадия не «Оплата получена»",
                  "перевести сделку в «Оплата получена | Закрытие сделки»"),
    "paidNoPo": (24, "стадия «Оплата получена», платёжки клиента в заказах нет",
                 "приложить платёжку клиента к заказу"),
    "noLine": (25, "не проставлена «Категория оборудования»", "проставить категорию оборудования сделки"),
    "noPo": (26, "не указан «Product leader»", "назначить продукт-оунера сделки"),
    "goneKam": (27, "КАМ в поле сделки уволен", "переназначить КАМа"),
}
# класс C: почему у сделки нет денег (подпись — в шаблоне и в «Как считается»)
WHY = {"noFile": "«Сумма» не сверена файлом экономики", "noSale": "суммы нет или валюта без курса",
       "noPz": "закупки нет: ПЗ не посчитать", "calib": "поправка не откалибрована (меньше 30 сделок)"}


def _date(s) -> dt.date | None:
    s = str(s or "")
    try:
        return (dt.datetime.strptime(s, "%d.%m.%Y").date() if len(s) == 10 and s[2] == "."
                else dt.date.fromisoformat(s[:10]))
    except ValueError:
        return None


def _fmt(d: dt.date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _quarter(d: dt.date, shift: int = 0) -> tuple[dt.date, dt.date, str]:
    """(начало, конец, подпись) квартала даты d со сдвигом на shift кварталов."""
    q = (d.month - 1) // 3 + shift
    y, q = d.year + q // 4, q % 4
    start = dt.date(y, 3 * q + 1, 1)
    nxt = dt.date(y + (q == 3), (3 * q + 3) % 12 + 1, 1)
    return start, nxt - dt.timedelta(days=1), f"{('I', 'II', 'III', 'IV')[q]} кв. {y}"


def _month(d: dt.date, shift: int) -> dt.date:
    y, m = divmod(d.month - 1 + shift, 12)
    return dt.date(d.year + y, m + 1, 1)


def stage_class(name: str, *, presale_funnel: bool) -> str:
    """Класс стадии невыигранной сделки: pre | offer | tender | wait | spec.
    Воронка «Пресейл» — по мячу (presale.ball_of): не у КАМа — «до ТКП», у КАМа — по строке
    kamqueue.BALL_KAM. Прочие воронки — по именам стадий зонда конверсии."""
    n = name or ""
    if presale_funnel:
        if presale_mod.ball_of(n) != "kam":
            return "pre"
        for rx, cls in zip(_KAM_RX, _BALL_CLASS):
            if rx.search(n):
                return cls
        return "offer"
    if _SPEC_RX.search(n):
        return "spec"
    if ЕЩЁ_НЕ_ВЫДАНО.search(n):
        return "pre"
    if ПРЕДЛОЖЕНИЕ.search(n):
        return "offer"
    if ПОСЛЕ_ПРЕДЛОЖЕНИЯ.search(n):
        return "wait" if _WAIT_RX.search(n) else "tender"
    return "pre"


def credit(*, kam_field: str = "", kam_proxy: str = "", pl: str = "", own: str = "",
           line: list[str] | None = None, active=lambda u: True) -> dict:
    """Одно правило атрибуции дохода. → {kam, kamSrc, po, poSrc, ex, line, lineSrc}.
    kam_field — поле «КАМ» (или «KAM (старое)»); kam_proxy — ответственный сделки, если по
    должности он КАМ (people.attribute: «владелец»); pl — «Product leader»; own —
    ответственный (исполнитель); line — подписи «Категории оборудования».
    Источник: "поле" | "прокси" | "уволен" | "" (не указан). Уволенный в поле — роль не
    закреплена (ключ GONE_KEY), история сделки остаётся за ним в подписи источника."""
    out = {"ex": own or ""}
    if kam_field:
        out["kam"], out["kamSrc"] = (kam_field, "поле") if active(kam_field) else (GONE_KEY, "уволен")
    elif kam_proxy and active(kam_proxy):
        out["kam"], out["kamSrc"] = kam_proxy, "прокси"
    else:
        out["kam"], out["kamSrc"] = "", ""
    if pl:
        out["po"], out["poSrc"] = (pl, "поле") if active(pl) else (GONE_KEY, "уволен")
    else:
        out["po"], out["poSrc"] = "", ""
    ln = [x for x in (line or []) if x]
    out.update(line=ln[0] if ln else LINE_NONE, lineSrc="поле" if ln else "")
    if len(ln) > 1:
        out["lineMore"] = len(ln) - 1       # сделка с несколькими категориями — в первой
    return out


def trust(row: dict, calib: dict | None) -> tuple[str, float | None, float | None, str, str]:
    """Класс доверия строки «Реализации» и её деньги: (класс, продажа, R, коэффициент к ПЗ,
    причина). A — «Сумма» сверена файлом экономики (saleSrc == "file"); иначе C с причиной.
    У A без ПЗ или без калибровки поправки R нет — причина в пятом поле."""
    sale = float(row.get("saleEur") or 0)
    if (row.get("saleSrc") or "bitrix") != "file":
        return "C", None, None, None, "noFile"
    if not row.get("saleKnown", True) or sale <= 0:
        return "C", None, None, None, "noSale"
    ok_buy = (row.get("buySrc") in ("file", "orders") and not row.get("buySusp")
              and not row.get("fxMissing"))
    pz, pay, _src = premia.pz_of(float(row.get("buyEur") or 0) if ok_buy else 0.0, row, econ=False)
    if pz <= 0:
        return "A", sale, None, None, "noPz"
    if not calib or int(calib.get("calibN") or 0) < CALIB_MIN:
        return "A", sale, None, round(sale / pz, 3), "calib"
    return "A", sale, premia.result_of(sale, pz, pay, float(calib.get("plug") or 0)), round(sale / pz, 3), ""


def paid_entry(row: dict) -> dt.date | None:
    """Последний вход сделки в «Оплата получена | Закрытие сделки» по пути стадий
    (contracts.rows → timeline). Имя стадии — по contracts.PAID_RX, той же регулярке, что
    ставит paidStage (правило 14). None — в стадии сделка не была."""
    at = None
    for t in row.get("timeline") or []:
        if contracts_mod.PAID_RX.search(str(t.get("name") or "")):
            at = _date(t.get("at")) or at
    return at


def _blank() -> dict:
    """Счёт строки разреза (открытая часть). xA — сделок класса A, xR — с известным R:
    по ним K6 решает, показывать ли сумму плитки (COVER_MIN)."""
    return {"k1": 0, "k1A": 0, "k1R": 0, "k1po": 0, "k1cl": 0, "k1old": 0, "k1nd": 0, "k1pf": 0,
            "due": 0, "dueA": 0, "dueR": 0,
            "dueB": {"overdue": 0, "q0": 0, "q1": 0, "later": 0, "none": 0},
            "k3c": 0, "k3cA": 0, "k3cR": 0, "k3r": 0, "k3rA": 0, "k3rR": 0, "k4": 0, "k4A": 0,
            "pipe": 0, "pipeC": {k: 0 for k, _ in STAGE_CLASSES}, "quiet": 0,
            "n": 0, "nA": 0, "nR": 0}


def _mblank() -> dict:
    """Деньги строки разреза (закрытая часть): S — продажа класса A, R — результат."""
    return {"k1S": 0.0, "k1R": 0.0, "dueS": 0.0, "dueR": 0.0,
            "dueBR": {"overdue": 0.0, "q0": 0.0, "q1": 0.0, "later": 0.0, "none": 0.0},
            "k3cR": 0.0, "k3rR": 0.0, "k4S": 0.0}


def compute(contracts: dict | None, people: dict | None, *, today: dt.date,
            presale: dict | None = None, kamq: dict | None = None, calib: dict | None = None,
            cat_labels: dict[str, str] | None = None, companies: dict[str, str] | None = None,
            roster: dict[str, dict] | None = None) -> dict | None:
    """contracts — contracts.compute (строки, life с разделом pay); people — people.compute
    (открытые сделки с сырьём дохода, inSrc, names); presale — presale.compute (cid, чаты
    openDeals); kamq — kamqueue.build; calib — premia.compute()["params"] (plug, calibN);
    cat_labels — подписи «Категории оборудования»; companies — {id компании: название};
    roster — справочник людей {uid: {name, active}}.
    → {"head": открытая часть, "money": закрытая часть или None}. None — нет «Реализации»."""
    if not contracts or not isinstance(contracts.get("rows"), list):
        return None
    people = people or {}
    cat_labels = cat_labels or {}
    companies = companies or {}
    names: dict[str, list] = {u: [x.get("name") or f"user#{u}", x.get("active", True) is not False]
                              for u, x in (roster or {}).items()}
    for u, v in (people.get("names") or {}).items():
        names.setdefault(str(u), list(v))

    def active(u: str) -> bool:
        return bool(names.get(u, [None, True])[1])

    q0s, q0e, q0l = _quarter(today)
    q1s, q1e, q1l = _quarter(today, 1)
    life = contracts.get("life") or {}
    sep_life = (contracts.get("sep") or {}).get("life") or {}
    pay = {**(life.get("pay") or {}), **(sep_life.get("pay") or {})}
    codes: dict[str, set] = defaultdict(set)
    for x in list(life.get("queue") or []) + list(sep_life.get("queue") or []):
        codes[str(x.get("deal"))].add(x.get("code"))
    pdeals = {str(d.get("id")): d for d in (people.get("deals") or [])}
    in_src = {str(d.get("id")): d for d in (people.get("inSrc") or [])}

    def labels(ids) -> list[str]:
        return [cat_labels.get(str(i)) or f"значение #{i}" for i in (ids or [])]

    def hold_of(name: str) -> str:
        n = (name or "").strip()
        if not n or n == "—":
            return HOLD_NONE
        return kam_mod.client_dir(n)

    deals: list[dict] = []          # открытая часть: без евро
    money_deals: dict[str, dict] = {}
    reasons: Counter = Counter()

    # 1. выигранные: строки «Реализации» без проигранных
    for r in contracts["rows"]:
        if r.get("lost"):
            continue
        did = str(r.get("deal"))
        p = pdeals.get(did) or {}
        cr = credit(kam_field=str(r.get("kamId") or ""),
                    kam_proxy=str(p.get("kam") or "") if p.get("kamSrc") == "владелец" else "",
                    pl=str(p.get("pl") or ""), own=str(p.get("own") or ""),
                    line=labels(p.get("line")), active=active)
        pi = pay.get(did) or {}
        eta = _date(pi.get("eta"))
        won = _date(r.get("wonAt"))
        sd = r.get("stageDays")
        po_ok = bool(pi.get("orders")) and pi.get("po") == pi.get("orders")
        entry = today - dt.timedelta(days=round(sd)) if isinstance(sd, (int, float)) else None
        closed_q = False
        if r.get("paidStage"):
            paid_at = entry
            b = "paidQ" if paid_at and paid_at >= q0s else ("paidOld" if paid_at else "paidNoDate")
        elif r.get("closed"):
            # закрыта успехом: K1 — нарастающим за квартал (решение 25.10, п. 3), поэтому
            # сделка, вошедшая в «Оплата получена» в квартале и потом закрытая, из K1 не
            # уходит. Дата — вход в «Оплата получена» по пути стадий, иначе вход в финальную
            paid_at = paid_entry(r) or entry
            closed_q = bool(paid_at and paid_at >= q0s)
            b = "paidQ" if closed_q else "closed"
        elif pi.get("src") == "po" or (pi.get("src") == "noLive" and po_ok):
            # платёжка клиента во всех заказах, стадия не переведена: деньги, скорее всего,
            # пришли — ни в K1 (K1 — по стадии), ни в K2 (не «к получению»); пункт — перевести
            paid_at = None
            b = "paidFile"
        else:
            paid_at = None
            b = "due"
        win_q = bool(won and q0s <= won <= today)
        if b == "closed" and not win_q:
            continue                         # закрытая успехом давно — не доход периода
        cls, sale, res, coef, why = trust(r, calib)
        if b in ("paidQ", "due"):           # те же сделки, что в n и nA строки доверия
            reasons[why or "ok"] += 1
        due_b = ""
        if b == "due":
            due_b = ("none" if not eta else "overdue" if eta < today else "q0" if eta <= q0e
                     else "q1" if eta <= q1e else "later")
        cs = codes.get(did, set())
        rec = {"id": did, "t": str(r.get("title") or "")[:70], "k": "r", "b": b, "seq": r.get("seq") or 0,
               "kam": cr["kam"], "kamS": cr["kamSrc"], "po": cr["po"], "poS": cr["poSrc"], "ex": cr["ex"],
               "h": hold_of(r.get("customer")), "ln": cr["line"], "lnS": cr["lineSrc"],
               "st": str(r.get("stageName") or "")[:40], "sd": round(sd) if isinstance(sd, (int, float)) else None,
               "won": _fmt(won), "ws": r.get("wonSrc") or "", "wq": win_q,
               "eta": _fmt(eta) if b == "due" else "", "es": pi.get("src") or "", "db": due_b,
               "paid": _fmt(paid_at), "po1": po_ok, "cl": closed_q,
               "risk": sorted(c for c in cs if c in DELAY_CODES + RISK_CODES) if b == "due" else [],
               "cls": cls, "why": why, "rk": res is not None, "seg": bool(r.get("seg")),
               "_r": r, "_pi": pi}
        deals.append(rec)
        if cls == "A":
            # коэффициент к ПЗ в массив не идёт: с продажей он дал бы закупку — нужен он только K7
            money_deals[did] = {"s": round(sale), "r": round(res) if res is not None else None}
            rec["_res"], rec["_sale"], rec["_coef"] = res, sale, coef

    # 2. невыигранные: открытые сделки вне реализации (people.deals) и пресейл у сорсинга
    ps_cid = str((presale or {}).get("cid") or "")
    ps_chat = {str(d.get("id")): (d.get("chat") or {}) for d in ((presale or {}).get("openDeals") or [])}
    outside = 0                      # «выиграна» по правилу people, но вне вселенной «Реализации»
    real_ids = {str(r.get("deal")) for r in contracts["rows"]}
    for p in list(pdeals.values()) + [x for i, x in in_src.items() if i not in pdeals]:
        did = str(p.get("id"))
        if p.get("state") == "real":
            outside += did not in real_ids
            continue
        cr = credit(kam_field=str(p.get("kam") or "") if p.get("kamSrc") == "поле" else "",
                    kam_proxy=str(p.get("kam") or "") if p.get("kamSrc") == "владелец" else "",
                    pl=str(p.get("pl") or ""), own=str(p.get("own") or ""),
                    line=labels(p.get("line")), active=active)
        idle = p.get("idle")
        quiet = bool(isinstance(idle, (int, float)) and idle > QUIET_DAYS
                     and (ps_chat.get(did) or {}).get("status") != "active")
        deals.append({"id": did, "t": str(p.get("t") or "")[:70], "k": "p", "b": "quiet" if quiet else "pipe",
                      "kam": cr["kam"], "kamS": cr["kamSrc"], "po": cr["po"], "poS": cr["poSrc"], "ex": cr["ex"],
                      "h": hold_of(companies.get(str(p.get("comp") or ""), "")),
                      "ln": cr["line"], "lnS": cr["lineSrc"], "st": str(p.get("stage") or "")[:40],
                      "sd": idle if isinstance(idle, (int, float)) else None,
                      "cs": stage_class(p.get("stage") or "", presale_funnel=bool(ps_cid) and str(p.get("catId")) == ps_cid),
                      "f": str(p.get("cat") or "")[:40], "cls": "C", "why": "noFile", "rk": False})

    # холдинги: топ по числу сделок реализации, затем пайплайна; хвост — «прочие клиенты»
    hc = Counter()
    for d in deals:
        hc[d["h"]] += 3 if d["k"] == "r" else 1
    top = {h for h, _n in hc.most_common(HOLDINGS_TOP) if h != HOLD_NONE}
    for d in deals:
        if d["h"] not in top and d["h"] != HOLD_NONE:
            d["hFull"], d["h"] = d["h"], HOLD_OTHER

    # 3. разрезы: КАМ, продукт-оунер, холдинг, линия — каждая сделка ровно в одной строке
    cut_keys = {"kam": lambda d: d["kam"], "po": lambda d: d["po"], "hold": lambda d: d["h"],
                "line": lambda d: d["ln"]}
    agg: dict[str, dict[str, dict]] = {c: defaultdict(_blank) for c in cut_keys}
    magg: dict[str, dict[str, dict]] = {c: defaultdict(_mblank) for c in cut_keys}
    tot, mtot = _blank(), _mblank()
    # [получено, к получению, R получено, R к получению, nR…, nA…] — по nA ячейка решает о сумме (COVER_MIN)
    ladder: dict[str, list] = defaultdict(lambda: [0, 0, 0.0, 0.0, 0, 0, 0, 0])
    m_from, m_to = _month(today, -LADDER_BACK), _month(today, LADDER_FWD)

    def put(a: dict, m: dict, d: dict) -> None:
        res, sale = d.get("_res"), d.get("_sale")
        b = d["b"]
        isA, hasR = d["cls"] == "A", res is not None
        if b in ("paidQ", "paidOld", "paidNoDate"):
            if b == "paidQ":
                a["k1"] += 1
                a["k1A"] += isA
                a["k1R"] += hasR
                a["k1po"] += d["po1"]
                a["k1cl"] += d["cl"]
                a["n"] += 1
                a["nA"] += isA
                a["nR"] += hasR
                if sale is not None:
                    m["k1S"] += sale
                if hasR:
                    m["k1R"] += res
            elif b == "paidOld":
                a["k1old"] += 1
            else:
                a["k1nd"] += 1
        elif b == "due":
            a["due"] += 1
            a["dueA"] += isA
            a["dueR"] += hasR
            a["dueB"][d["db"]] += 1
            a["n"] += 1
            a["nA"] += isA
            a["nR"] += hasR
            dl = any(c in DELAY_CODES for c in d["risk"])
            rk = any(c in RISK_CODES for c in d["risk"])
            a["k3c"] += dl
            a["k3cA"] += dl and isA
            a["k3cR"] += dl and hasR
            a["k3r"] += rk
            a["k3rA"] += rk and isA
            a["k3rR"] += rk and hasR
            if sale is not None:
                m["dueS"] += sale
            if hasR:
                m["dueR"] += res
                m["dueBR"][d["db"]] += res
                m["k3cR"] += res if dl else 0.0
                m["k3rR"] += res if rk else 0.0
        elif b == "paidFile":
            a["k1pf"] += 1                   # только счёт: ни в K1, ни в K2
        elif b == "pipe":
            a["pipe"] += 1
            a["pipeC"][d["cs"]] += 1
        elif b == "quiet":
            a["quiet"] += 1
        if d.get("wq"):
            a["k4"] += 1
            a["k4A"] += isA
            if sale is not None:
                m["k4S"] += sale

    for d in deals:
        put(tot, mtot, d)
        for c, fn in cut_keys.items():
            put(agg[c][fn(d)], magg[c][fn(d)], d)
        # лестница: получено — по месяцу оплаты, к получению — по месяцу ожидаемой оплаты
        res = d.get("_res")
        if d["b"] in ("paidQ", "paidOld") and d.get("paid"):
            pm = _date(d["paid"]).replace(day=1)
            if m_from <= pm < _month(today, 1):
                L = ladder[pm.strftime("%m.%Y")]
                L[0] += 1
                L[6] += d["cls"] == "A"
                if res is not None:
                    L[2] += res
                    L[4] += 1
        elif d["b"] == "due":
            key = ("none" if d["db"] == "none" else "overdue" if d["db"] == "overdue"
                   else _date(d["eta"]).replace(day=1).strftime("%m.%Y"))
            if key not in ("none", "overdue") and _date(d["eta"]).replace(day=1) >= m_to:
                key = "later"
            L = ladder[key]
            L[1] += 1
            L[7] += d["cls"] == "A"
            if res is not None:
                L[3] += res
                L[5] += 1
    months = [_month(today, i) for i in range(-LADDER_BACK, LADDER_FWD)]
    # «срок прошёл» и «без даты» — первыми: это то, что надо делать, и на узком экране они видны
    lad_keys = ["overdue", "none"] + [m.strftime("%m.%Y") for m in months] + ["later"]

    def nm(cut: str, key: str) -> tuple[str, bool]:
        if cut in ("kam", "po"):
            if key == GONE_KEY:
                return GONE, False
            if not key:
                return KAM_NONE if cut == "kam" else PO_NONE, True
            n = names.get(key)
            return (n[0] if n else f"user#{key}"), bool(n[1]) if n else True
        return key, True

    def cover(n: int, k: int) -> int | None:
        return round(k / n * 100) if n else None

    cuts, mcuts = {}, {}
    for c in cut_keys:
        rows = []
        for key, a in agg[c].items():
            name, act = nm(c, key)
            rows.append({"key": key, "name": name, **({} if act else {"gone": True}), **a,
                         "cover": cover(a["n"], a["nA"])})
        rows.sort(key=lambda x: (x["key"] in ("", GONE_KEY, LINE_NONE, HOLD_NONE, HOLD_OTHER),
                                 -(x["due"] + x["k1"]), -x["pipe"], x["name"]))
        cuts[c] = rows
        # списком, как открытые строки: ключ строки — имя клиента или линии, а ключи словаря
        # живая сверка печатает в «образцах» (журнал публичный)
        mcuts[c] = [{"key": key, **_round_m(m)} for key, m in magg[c].items()]

    # 4. двойная проверка (правило 26.09.2026) — разными путями, а не одним put():
    #    ① Σ R по сделкам = Σ по людям через доли пула (с «не закреплено»);
    #    ② K7: Σ частей людей = Σ пулов сделок × К;
    #    ③ итог плиток = Σ по опубликованным значениям сделок, сгруппированным по корзине;
    #    ④ счёт плиток = число сделок списка по корзине;
    #    ⑤ Σ строк каждого разреза = итог — по числу и по деньгам.
    #    K1 по стадии против K1 по платёжкам — пометка (PO_CONFIRM_MIN), не гейт.
    checks = []
    with_r = [d for d in deals if d.get("_res") is not None and d["b"] in ("paidQ", "due")]
    total_r = sum(d["_res"] for d in with_r)

    def _k7() -> dict:
        return {"paidQ": 0.0, "due": 0.0, "dueQ0": 0.0, "ids": set(), "idsA": set(), "idsR": set(), "roles": set()}

    k7: dict[str, dict] = defaultdict(_k7)
    # знаменатель K7 у человека — все его сделки K1 и K2 по любой роли (правило 70 %, п. 7)
    for d in deals:
        if d["b"] not in ("paidQ", "due"):
            continue
        for role, uid in (("kam", d["kam"]), ("po", d["po"]), ("ex", d["ex"])):
            x = k7["" if uid in ("", GONE_KEY) else uid]
            x["ids"].add(d["id"])
            x["roles"].add(role)
            if d["cls"] == "A":
                x["idsA"].add(d["id"])
            if d.get("_res") is not None:
                x["idsR"].add(d["id"])
    by_people = pools = 0.0
    for d in with_r:
        res, coef = d["_res"], d["_coef"] or 0.0
        pool = premia.pool_of(res, coef) * premia.K_DEFAULT
        pools += pool
        # доли — по настоящим держателям ролей: уволенный КАМ — всё ещё КАМ сделки, и правило
        # «PO при КАМе — эксперт 20 %» не переключается на «PO завёл сделку 80 %»; его доля —
        # «не закреплено» уже после раскладки
        for uid, _role, share in premia._parts({"kam": d["kam"], "prod": d["po"], "own": d["ex"]}, res <= 0):
            by_people += res * share
            x = k7["" if uid in ("", GONE_KEY) else uid]
            if d["b"] == "paidQ":
                x["paidQ"] += pool * share
            else:
                x["due"] += pool * share
                if d["db"] in ("overdue", "q0"):
                    x["dueQ0"] += pool * share
    checks.append({"name": "Σ R по сделкам = Σ по людям через доли", "ok": abs(total_r - by_people) <= TOL_EUR,
                   "n": len(with_r)})
    k7_sum = sum(x["paidQ"] + x["due"] for x in k7.values())
    checks.append({"name": "K7: Σ частей людей = Σ пулов сделок", "ok": abs(k7_sum - pools) <= TOL_EUR * max(1, len(with_r)),
                   "n": len(with_r)})
    for f, bk in (("k1R", "paidQ"), ("dueR", "due")):
        per = [round(d["_res"]) for d in with_r if d["b"] == bk]
        checks.append({"name": f"итог «{f}» = Σ по сделкам корзины", "n": len(per),
                       "ok": abs(sum(per) - round(mtot[f])) <= TOL_EUR * max(1, len(per))})
    by_b = Counter(d["b"] for d in deals)
    n_ok = (by_b["paidQ"], by_b["due"], by_b["pipe"], by_b["quiet"], by_b["paidFile"]) == (
        tot["k1"], tot["due"], tot["pipe"], tot["quiet"], tot["k1pf"])
    checks.append({"name": "счёт плиток = число сделок по корзинам", "ok": n_ok, "n": len(deals)})
    for c in cut_keys:
        n_ok = all(sum(x[f] for x in cuts[c]) == tot[f] for f in ("k1", "due", "k4", "pipe", "quiet", "n", "k1pf"))
        m_ok = all(abs(sum(m[f] for m in mcuts[c]) - round(mtot[f])) <= TOL_EUR * max(1, len(mcuts[c]))
                   for f in ("k1R", "dueR", "k4S"))
        checks.append({"name": f"Σ строк разреза «{c}» = итог", "ok": n_ok and m_ok, "n": len(cuts[c])})
    money_ok = all(x["ok"] for x in checks)
    po_low = bool(tot["k1"]) and tot["k1po"] / tot["k1"] < PO_CONFIRM_MIN

    # 5. что открыть или сдвинуть: очередь КАМа + пробелы денег; до ACTIONS_MAX на человека
    acts: dict[str, list] = defaultdict(list)
    for q in (kamq or {}).get("queue") or []:
        acts[str(q.get("kamId") or "")].append({
            "code": q.get("code"), "sev": q.get("sev", 9), "what": q.get("what") or "", "act": q.get("act") or "",
            "deal": str(q.get("deal")), "seq": q.get("seq") or 0, "t": str(q.get("title") or "")[:70],
            "days": q.get("days"), "who": q.get("owner") or "", "src": "kam",
            # дни рабочие (р.д.) у сроков регламента ОРК (kamqueue inWd), иначе календарные
            **({"wd": True} if q.get("inWd") else {})})
    by_id = {d["id"]: d for d in deals if d["k"] == "r"}

    def gap(code: str, d: dict, key: str, who: str = "") -> None:
        sev, what, act = GAPS[code]
        acts[key].append({"code": code, "sev": sev, "what": what, "act": act, "deal": d["id"],
                          "seq": d.get("seq") or 0, "t": d["t"], "days": d.get("sd"), "who": who, "src": "inc"})

    gaps = Counter()
    for d in by_id.values():
        r, pi = d.pop("_r"), d.pop("_pi")
        kam_key = d["kam"] if d["kam"] != GONE_KEY else ""
        owner = r.get("ossDeal") or r.get("manager") or ""
        if d["b"] == "due":
            todo = []
            if d["cls"] == "C" and d["why"] == "noFile":
                todo.append(("econ", kam_key, r.get("manager") or ""))
            if r.get("buySrc") == "none":
                todo.append(("noBuy", kam_key, owner))
            if pi.get("delivered") and pi.get("src") != "plan":
                todo.append(("noPayPlan", kam_key, owner))
            if d["kam"] == GONE_KEY:
                todo.append(("goneKam", "", "руководитель КАМ"))
            if not d["lnS"]:
                todo.append(("noLine", d["po"] if d["po"] not in ("", GONE_KEY) else "", "продукт-оунер сделки"))
            if not d["po"]:
                todo.append(("noPo", "", "руководитель продуктов"))
            for code, key, who in todo:
                gap(code, d, key, who)
                gaps[code] += 1
        elif d["b"] == "paidFile":
            gap("moveStage", d, kam_key, r.get("manager") or "")
            gaps["moveStage"] += 1
        elif d["b"] in ("paidQ", "paidOld") and not d["po1"] and not d["cl"]:
            gap("paidNoPo", d, kam_key, owner)      # закрытая сделка — не пункт: работа по ней кончена
            gaps["paidNoPo"] += 1
    actions = {}
    for key, items in acts.items():
        items.sort(key=lambda x: (not (by_id.get(x["deal"]) or {}).get("rk"), x["sev"], -(x["days"] or 0), x["deal"]))
        actions[key] = {"n": len(items), "items": items[:ACTIONS_MAX]}

    # 6. открытая часть и закрытая
    for d in deals:
        for k in ("_res", "_sale", "_coef", "_r", "_pi"):
            d.pop(k, None)
    n_all = tot["n"]
    head = {
        "asOf": _fmt(today),
        "quarter": {"label": q0l, "from": _fmt(q0s), "to": _fmt(q0e)},
        "next": {"label": q1l, "from": _fmt(q1s), "to": _fmt(q1e)},
        "tot": tot,
        "trust": {"n": n_all, "nA": tot["nA"], "nR": tot["nR"], "pct": cover(n_all, tot["nA"]),
                  "reasons": dict(reasons), "fxAsOf": (contracts.get("fx") or {}).get("asOf") or "",
                  "poLow": po_low, "poMin": round(PO_CONFIRM_MIN * 100),
                  "calibN": int((calib or {}).get("calibN") or 0), "calibOk": int((calib or {}).get("calibN") or 0) >= CALIB_MIN},
        "ladder": [{"m": k, "paid": ladder[k][0], "due": ladder[k][1], "paidA": ladder[k][6], "dueA": ladder[k][7]}
                   for k in lad_keys],
        "cuts": cuts,
        "deals": deals,
        "actions": actions,
        "gaps": dict(gaps),
        "names": {u: names[u] for u in sorted({d[k] for d in deals for k in ("kam", "po", "ex")} | set(actions))
                  if u in names},
        "checks": checks,
        "money": {"ok": money_ok, "fail": [x["name"] for x in checks if not x["ok"]]},
        "recon": {"outside": outside, "deals": len(deals), "real": sum(1 for d in deals if d["k"] == "r"),
                  "pipe": sum(1 for d in deals if d["k"] == "p")},
        "rules": {"cover": round(COVER_MIN * 100), "calibMin": CALIB_MIN, "quietDays": QUIET_DAYS,
                  "actionsMax": ACTIONS_MAX, "payGrace": lifecycle.PAY_GRACE,
                  "k": premia.K_DEFAULT, "rate": premia.RATE, "priceMin": premia.PRICE_MIN},
        "labels": {"stage": dict(STAGE_CLASSES), "why": WHY, "gaps": {k: v[1] for k, v in GAPS.items()},
                   "paySrc": lifecycle.PAY_SRC, "risk": {c: lifecycle.EXC[c][1] for c in DELAY_CODES + RISK_CODES}},
    }
    if not money_ok:
        return {"head": head, "money": None}
    money = {
        "deals": money_deals,
        "tot": _round_m(mtot),
        "cuts": mcuts,
        "ladder": [{"m": k, "paidR": round(ladder[k][2]), "paidN": ladder[k][4],
                    "dueR": round(ladder[k][3]), "dueN": ladder[k][5]} for k in lad_keys],
        # K7 — только своё, после серверного фильтра (этап 2); до него — владельцу
        "k7": {u: {"paidQ": round(x["paidQ"]), "due": round(x["due"]), "dueQ0": round(x["dueQ0"]),
                   "n": len(x["ids"]), "nA": len(x["idsA"]), "nR": len(x["idsR"]), "roles": sorted(x["roles"])}
               for u, x in k7.items()},
        "k": premia.K_DEFAULT,
    }
    return {"head": head, "money": money}


def _round_m(m: dict) -> dict:
    return {k: ({kk: round(vv) for kk, vv in v.items()} if isinstance(v, dict) else
                (round(v) if isinstance(v, float) else v)) for k, v in m.items()}


def strip_inputs(contracts: dict | None) -> None:
    """Убрать из «Реализации» то, что нужно только расчёту дохода: раздел pay (ожидаемая
    дата оплаты клиента) и отсрочку оплаты клиента payDays у строк. Массив «Реализации»
    открыт всем с вкладками «Реализация» и «Поставщики», а условие оплаты — сведения под
    именным правом (решение Р-4, 19.10.2026). Вызывается в main.py после compute()."""
    if not isinstance(contracts, dict):
        return
    for lf in (contracts.get("life"), (contracts.get("sep") or {}).get("life")):
        if isinstance(lf, dict):
            lf.pop("pay", None)
    for rows in (contracts.get("rows"), (contracts.get("sep") or {}).get("rows")):
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                r.pop("payDays", None)
