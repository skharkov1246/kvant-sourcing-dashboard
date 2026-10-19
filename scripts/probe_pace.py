#!/usr/bin/env python3
"""Зонд v50 «Темп компании»: помесячный ряд с января 2025 по текущий месяц.

Вопрос владельца 13.10.2026: «Оцени темп компании в целом. Что у нас изменилось
с апреля». Годовые итоги уже дал probe_2526.py; здесь — тот же разбор сделок, но
по МЕСЯЦАМ, и рядом сорсинг (СП-166), заказы поставщикам (СП-172), реализация
(воронка 0) и люди. Сравнение — апрель–сентябрь 2026 против января–марта 2026 и
против тех же месяцев 2025 (сезонность).

МОДЕЛЬ СДЕЛКИ — probe_conversion.классифицировать + probe_2526.разметить (O0 —
заявка, O1 — датированная стадия «предложение выдано», контракт v4 и строгий).
Даты:
  • заявка — DATE_CREATE; ТКП — первый вход в стадию «выдано»; контракт — вход в
    кат. 0 после предпродажи, иначе первый живой заказ СП-172;
  • проигрыш — последний вход в текущую проигрышную стадию (история стадий);
  • открыта на конец месяца — создана до конца месяца и не закрыта (ни контракта,
    ни проигрыша) к нему.
Причина проигрыша — имя проигрышной стадии (иного поля портал не хранит), классы
ПРИЧИНЫ ниже; имена стадий — общие слова процесса.

РЕАЛИЗАЦИЯ — первый вход сделки в стадию воронки 0 класса ВЕХИ (договор подписан,
оплата поставщику, доставка клиенту, оплата получена). История есть у сделок,
созданных с SINCE; открытых сделок реализации, заведённых раньше, — отдельным
счётом (они в ряд не попадают).

СОРСИНГ — карточки СП-166 воронки 24 с января первого года: всего, робот
(служебные записи config.service_accounts), сорсер (отдел 172 с дочерними),
прочие; с файлом КП поставщика (config.RFQ_QUOTE_FIELDS, как дашборд); заявок с
карточкой и дней от заявки до первой карточки.

ЛЮДИ — окно работы как в metrics.py: с приёма (UF_EMPLOYMENT_DATE, иначе
регистрация) по последний вход у отключённых. Роль КАМа и продуктового — по
people.resolve_role. Рядом — «работавших»: КАМов с новой заявкой в месяце и
сорсеров-ответственных карточек в месяце.

ЧТО В ЖУРНАЛ. Репозиторий публичный (CLAUDE.md, правило 5; распоряжение 30.09):
счётчики, доли, медианы дней, число людей. Деньги — только доли кварталов в
сумме за весь ряд и отношения медиан; квартал, где контрактов меньше MIN_VALUE_N,
не печатается. Ни сумм, ни названий компаний, поставщиков и брендов, ни ID и
названий сделок, ни имён людей.

НАГРУЗКА: чтения probe_2526 (≈ 900 запросов при 1/с) плюс карточки СП-166 с
января первого года (по 50) и справочник людей; ≈ 25–30 мин при 1/с.
"""
from __future__ import annotations

import collections
import datetime as dt
import importlib.util
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _модуль(имя, файл):
    spec = importlib.util.spec_from_file_location(имя, ROOT / "scripts" / файл)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p26 = _модуль("kvant_probe_2526", "probe_2526.py")
pc = p26.pc

FIRST_MONTH = os.environ.get("PACE_FROM", "2025-01")
SPA = 166
SPA_CAT = 24
MIN_VALUE_N = p26.MIN_VALUE_N
STUCK_DAYS = pc.STUCK_DAYS

ПРИЧИНЫ = (   # класс проигрыша по имени стадии; порядок значим
    ("поставщик не ответил", re.compile(r"не\s*ответ|не\s*присл", re.I)),
    ("бюджетный запрос", p26.БЮДЖЕТНЫЙ),
    ("нам неинтересно", re.compile(r"неинтерес", re.I)),
    ("не нашли производителя", re.compile(r"не\s*нашл", re.I)),
    ("проиграли после ТКП", pc.ПРОИГРЫШ_ПОСЛЕ_ПРЕДЛОЖЕНИЯ),
    ("отмена заказчиком", re.compile(r"отмен|снят|больше\s+не\s+надо|не\s*состоял", re.I)),
)
ВЕХИ = (      # стадии воронки 0, первый вход в которые — веха реализации
    ("договор подписан", re.compile(r"договор\s+с\s+заказчиком\s+подписан", re.I)),
    ("оплата поставщику", re.compile(r"оплата\s+товара\s+произведена|оплата\s+поставщик", re.I)),
    ("доставлено клиенту", re.compile(r"доставка\s+клиенту\s+произведена", re.I)),
    ("оплата получена", re.compile(r"оплата\s+получена", re.I)),
)


# ----------------------------------------------------------------------------- календарь

def месяц(iso) -> str | None:
    s = str(iso or "")
    return s[:7] if re.match(r"\d{4}-\d\d", s) else None


def месяцы(с: str, по: str) -> list[str]:
    y, m = int(с[:4]), int(с[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= по:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def границы(м: str) -> tuple[dt.date, dt.date]:
    a = dt.date(int(м[:4]), int(м[5:7]), 1)
    b = (a.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return a, b


def рабочие_дни(a: dt.date, b: dt.date) -> int:
    """Пн–Пт включительно (как period.workdays; праздники не вычитаются)."""
    if b < a:
        return 0
    return sum(1 for i in range((b - a).days + 1) if (a + dt.timedelta(days=i)).weekday() < 5)


def квартал(м: str) -> str:
    return f"{м[:4]}-Q{(int(м[5:7]) - 1) // 3 + 1}"


def _мед(xs):
    return round(statistics.median(xs)) if xs else None


def _дн(a, b):
    return p26._дн(a, b)


# ----------------------------------------------------------------------------- сделки

def причина(имя_стадии: str) -> str:
    for класс, rx in ПРИЧИНЫ:
        if rx.search(имя_стадии or ""):
            return класс
    return "прочее"


def даты_закрытия(строки, сделки, история, мета):
    """Строкам v4 — дата проигрыша, причина и дата закрытия. Чистая функция."""
    по_id = {str(д["ID"]): д for д in сделки}
    for r in строки:
        д = по_id[r["id"]]
        ряд = sorted(история.get(r["id"], []), key=lambda x: str(x[1]))
        r["lost_date"], r["lost_reason"] = None, None
        if r["cls"] == "lost":
            стадия = str(д.get("STAGE_ID") or "")
            входы = [t for s, t in ряд if s == стадия] or [t for _, t in ряд[-1:]]
            r["lost_date"] = pc.дата_мск(входы[-1]) if входы else None
            r["lost_reason"] = причина(str((мета.get(стадия) or {}).get("name") or ""))
        закрыта = r["lost_date"] or r["contract_date"]
        if r["cls"] == "contract" and not закрыта and ряд:
            закрыта = pc.дата_мск(ряд[-1][1])       # контракт по номеру или успеху без даты
        r["closed"] = закрыта
    return строки


def ряд_сделок(строки, мес):
    """Строки (после разметки и даты_закрытия) → {месяц: счётчики}. Чистая функция."""
    out = {м: collections.Counter() for м in мес}
    дни_ткп = collections.defaultdict(list)
    дни_контр = collections.defaultdict(list)
    первая_у_компании: dict[str, str] = {}
    for r in sorted((r for r in строки if r["o0"]), key=lambda r: r["created"] or ""):
        c = r["company"]
        if c != "0" and c not in первая_у_компании and r["created"]:
            первая_у_компании[c] = r["created"]
    for r in строки:
        if not r["o0"]:
            continue
        нн = r["seg_company"]
        м = месяц(r["created"])
        if м in out:
            o = out[м]
            o["заявки"] += 1
            o["заявки НН" if нн else "заявки прочие"] += 1
            o["бюджетные"] += r["budget"]
            if r["company"] != "0" and первая_у_компании.get(r["company"]) == r["created"]:
                o["новые клиенты"] += 1
        м = месяц(r["offer_date"]) if r["o1"] else None
        if м in out:
            out[м]["ТКП"] += 1
            out[м]["ТКП НН" if нн else "ТКП прочие"] += 1
            d = _дн(r["created"], r["offer_date"])
            if d is not None and d >= 0:
                дни_ткп[м].append(d)
        м = месяц(r["contract_date"]) if r["cls"] == "contract" else None
        if м in out:
            out[м]["контракты"] += 1
            out[м]["контракты НН" if нн else "контракты прочие"] += 1
            out[м]["строгие"] += r["strict"]
            d = _дн(r["offer_date"], r["contract_date"])
            if d is not None and d >= 0:
                дни_контр[м].append(d)
        м = месяц(r["lost_date"])
        if м in out:
            out[м]["проиграно"] += 1
            out[м]["проиграно после ТКП" if (r["o1"] or r["o2"] or r["offer"]) else "проиграно до ТКП"] += 1
            out[м]["причина: " + (r["lost_reason"] or "прочее")] += 1
    for м in мес:
        _, конец = границы(м)
        к = конец.isoformat()
        открыто = [r for r in строки if r["o0"] and r["created"] and r["created"] <= к
                   and not (r["closed"] and r["closed"] <= к)]
        out[м]["открыто на конец"] = len(открыто)
        out[м]["открыто старше 120 дн"] = sum(1 for r in открыто if (конец - pc._дата(r["created"])).days > STUCK_DAYS)
        out[м]["дн заявка→ТКП мед"] = _мед(дни_ткп[м]) or 0
        out[м]["дн ТКП→контракт мед"] = _мед(дни_контр[м]) or 0
    return out


def когорты_ткп(строки, мес, сегодня):
    """По месяцу выдачи ТКП: выдано, контрактов к сегодняшнему дню, проиграно,
    открыто, зрелая ли когорта (> STUCK_DAYS дней от конца месяца). Чистая функция."""
    out = {}
    for м in мес:
        xs = [r for r in строки if r["o0"] and r["o1"] and месяц(r["offer_date"]) == м]
        _, конец = границы(м)
        out[м] = {"n": len(xs), "c": sum(1 for r in xs if r["cls"] == "contract"),
                  "lost": sum(1 for r in xs if r["cls"] == "lost"),
                  "open": sum(1 for r in xs if r["cls"] == "open"),
                  "ripe": (сегодня - конец).days > STUCK_DAYS}
    return out


def вехи_реализации(история, мета, мес):
    """Первый вход сделки в стадию-веху воронки 0 → {месяц: {веха: сделок}}. Чистая функция."""
    класс_стадии = {}
    for sid, m in мета.items():
        if str(m.get("cat")) != "0":
            continue
        имя = str(m.get("name") or "")
        for веха, rx in ВЕХИ:
            if rx.search(имя):
                класс_стадии[sid] = веха
                break
        else:
            if str(m.get("sem") or "") == "S":
                класс_стадии[sid] = "оплата получена"
    out = {м: collections.Counter() for м in мес}
    for _, ряд in история.items():
        первый: dict[str, str] = {}
        for s, t in sorted(ряд, key=lambda x: str(x[1])):
            веха = класс_стадии.get(s)
            if веха and веха not in первый:
                первый[веха] = pc.дата_мск(t)
        for веха, d in первый.items():
            м = месяц(d)
            if м in out:
                out[м][веха] += 1
    return out, len(класс_стадии)


# ----------------------------------------------------------------------------- сорсинг

def есть_кп(item) -> bool:
    import config
    for f in config.RFQ_QUOTE_FIELDS:
        v = item.get(f)
        if (any(v) if isinstance(v, list) else bool(v)):
            return True
    return False


def ряд_карточек(карточки, строки, мес, служебные, отдел):
    """Карточки СП-166 → {месяц: счётчики}; плюс охват заявок карточками по месяцу
    создания заявки. Чистая функция (есть_кп читает только список полей)."""
    out = {м: collections.Counter() for м in мес}
    ответственные = collections.defaultdict(set)
    первая: dict[str, str] = {}
    for к in карточки:
        t = pc.дата_мск(к.get("createdTime"))
        d = str(к.get("parentId2") or "0")
        if d != "0" and t and (d not in первая or t < первая[d]):
            первая[d] = t
        м = месяц(t)
        if м not in out:
            continue
        o = out[м]
        автор, отв = str(к.get("createdBy") or ""), str(к.get("assignedById") or "")
        o["карточки"] += 1
        o["робот" if автор in служебные else "сорсер" if автор in отдел else "прочие"] += 1
        o["с КП поставщика"] += есть_кп(к)
        if отв in отдел:
            ответственные[м].add(отв)
    for м in мес:
        out[м]["сорсеров-ответственных"] = len(ответственные[м])
        сд = {str(к.get("parentId2")) for к in карточки
              if месяц(pc.дата_мск(к.get("createdTime"))) == м and str(к.get("parentId2") or "0") != "0"}
        out[м]["сделок с карточками"] = len(сд)
    охват = {}
    for м in мес:
        xs = [r for r in строки if r["o0"] and not r["budget"] and месяц(r["created"]) == м]
        с = [r for r in xs if r["id"] in первая]
        лаг = [d for r in с if (d := _дн(r["created"], первая[r["id"]])) is not None and d >= 0]
        охват[м] = {"n": len(xs), "with": len(с), "lag_med": _мед(лаг), "lag_p75": pc.перцентиль(лаг, 75)}
    return out, охват


# ----------------------------------------------------------------------------- заказы

def ряд_заказов(заказы_разм, мес):
    """Заказы СП-172 (p26.разметить_заказы) → {месяц: счётчики}. Чистая функция."""
    out = {м: collections.Counter() for м in мес}
    дни_отгр = collections.defaultdict(list)
    первые = collections.defaultdict(set)
    for о in заказы_разм:
        м = месяц(о["created"])
        if м in out:
            out[м]["заказов"] += 1
            out[м]["провал" if о["sem"] == "F" else "живых"] += 1
        if о["deal"] and месяц(о["first_live"]) in out:
            первые[месяц(о["first_live"])].add(о["deal"])
        if о["sem"] == "F":
            continue
        м = месяц(о["ship_fact"])
        if м in out:
            out[м]["отгружено поставщиком"] += 1
            d = _дн(о["created"], о["ship_fact"])
            if d is not None and d >= 0:
                дни_отгр[м].append(d)
        м = месяц(о["cust_fact"])
        if м in out:
            out[м]["доставлено клиенту"] += 1
            срок = о["deadline"] or о["cust_plan"]
            if срок:
                out[м]["доставлено со сроком"] += 1
                out[м]["доставлено позже срока"] += о["cust_fact"] > срок
    for м in мес:
        out[м]["первый заказ сделки"] = len(первые[м])
        out[м]["дн заказ→отгрузка мед"] = _мед(дни_отгр[м]) or 0
    return out


# ----------------------------------------------------------------------------- люди

def в_окне(чел, м) -> bool:
    a, b = границы(м)
    h = чел.get("hired")
    if h and h > b:
        return False
    if чел.get("active"):
        return True
    last = чел.get("last")
    return bool(last) and last >= a


def ряд_людей(состав, мес, отдел, роль, строки, служебные):
    """{месяц: счётчики людей}: в штате по окну, пришли, ушли, работавших. Чистая функция."""
    out = {м: collections.Counter() for м in мес}
    for uid, чел in состав.items():
        if uid in служебные:
            continue
        группа = "сорсинг" if uid in отдел else {"kam": "КАМ", "prod": "продукт"}.get(роль.get(uid, ""))
        if not группа:
            continue
        for м in мес:
            if в_окне(чел, м):
                out[м][f"{группа}: в штате"] += 1
            if чел.get("hired") and месяц(чел["hired"].isoformat()) == м:
                out[м][f"{группа}: пришли"] += 1
            if not чел.get("active") and чел.get("last") and месяц(чел["last"].isoformat()) == м:
                out[м][f"{группа}: ушли (посл. вход)"] += 1
    кам_мес = collections.defaultdict(set)
    for r in строки:
        if r["o0"] and r.get("kam") and месяц(r["created"]) in out:
            кам_мес[месяц(r["created"])].add(r["kam"])
    for м in мес:
        out[м]["КАМов с новой заявкой"] = len(кам_мес[м])
    return out


# ----------------------------------------------------------------------------- деньги (только доли)

def доли_денег(строки, мес):
    """Доли кварталов в сумме контрактов и выданных ТКП за весь ряд; отношение
    медианы контракта квартала к медиане всего ряда. Квартал, где контрактов
    меньше MIN_VALUE_N, — без доли. Чистая функция."""
    кварталы = sorted({квартал(м) for м in мес})
    к = collections.defaultdict(list)
    т = collections.defaultdict(list)
    for r in строки:
        if not r["o0"]:
            continue
        if r["cls"] == "contract" and r.get("v_contract") and месяц(r["contract_date"]) in мес:
            к[квартал(месяц(r["contract_date"]))].append(r["v_contract"])
        if r["o1"] and r.get("v_offer") and месяц(r["offer_date"]) in мес:
            т[квартал(месяц(r["offer_date"]))].append(r["v_offer"])
    всего_к = sum(sum(v) for v in к.values())
    всего_т = sum(sum(v) for v in т.values())
    мед_все = statistics.median([x for v in к.values() for x in v]) if к else None
    out = {}
    for q in кварталы:
        n = len(к[q])
        ok = n >= MIN_VALUE_N
        out[q] = {"n": n, "share": pc.доля(sum(к[q]), всего_к) if ok else None,
                  "size": round(statistics.median(к[q]) / мед_все, 2) if ok and мед_все else None,
                  "offers_n": len(т[q]), "offers_share": pc.доля(sum(т[q]), всего_т) if len(т[q]) >= MIN_VALUE_N else None}
    return out


# ----------------------------------------------------------------------------- печать

def печать_ряда(заголовок, ряд, мес, ключи, рд=None):
    print(f"\n{заголовок}")
    print("  месяц | " + (" рд |" if рд else "") + " | ".join(ключи))
    for м in мес:
        vals = " | ".join(str(ряд[м].get(k, 0)) for k in ключи)
        print(f"  {м} | " + (f"{рд[м]} | " if рд else "") + vals)


def отчёт(мес, рд, сд, ког, вех, кар, охв, зак, люд, деньги, до_since_открыто):
    причины = [f"причина: {k}" for k, _ in ПРИЧИНЫ] + ["причина: прочее"]
    печать_ряда("СДЕЛКИ ПРЕДПРОДАЖИ (O0 без технических воронок; НН — Норникель по компании)", сд, мес,
                ["заявки", "заявки НН", "заявки прочие", "бюджетные", "новые клиенты", "ТКП", "ТКП НН",
                 "ТКП прочие", "контракты", "контракты НН", "контракты прочие", "строгие"], рд)
    печать_ряда("ПРОИГРЫШИ ПО МЕСЯЦУ ЗАКРЫТИЯ", сд, мес,
                ["проиграно", "проиграно до ТКП", "проиграно после ТКП", *причины])
    печать_ряда("ПОРТФЕЛЬ И СКОРОСТЬ", сд, мес,
                ["открыто на конец", "открыто старше 120 дн", "дн заявка→ТКП мед", "дн ТКП→контракт мед"])
    print("\nКОГОРТЫ ТКП (месяц выдачи → исход к сегодняшнему дню; зрелая — старше 120 дн)")
    print("  месяц | ТКП | контрактов | проиграно | открыто | конверсия % | зрелая")
    for м in мес:
        к = ког[м]
        print(f"  {м} | {к['n']} | {к['c']} | {к['lost']} | {к['open']} | {pc.доля(к['c'], к['n'])} | {'да' if к['ripe'] else 'нет'}")
    печать_ряда("РЕАЛИЗАЦИЯ — ПЕРВЫЙ ВХОД В ВЕХУ ВОРОНКИ 0", вех, мес, [в for в, _ in ВЕХИ])
    print(f"  открытых сделок воронки 0, созданных до {pc.SINCE[:10]} (в ряд не попадают): {до_since_открыто}")
    печать_ряда("СОРСИНГ — КАРТОЧКИ СП-166 ВОРОНКИ 24", кар, мес,
                ["карточки", "робот", "сорсер", "прочие", "с КП поставщика", "сделок с карточками",
                 "сорсеров-ответственных"], рд)
    print("\nОХВАТ ЗАЯВОК КАРТОЧКАМИ (месяц заявки; без бюджетных; лаг — дней до первой карточки)")
    print("  месяц | заявок | с карточкой | доля % | лаг мед | лаг p75")
    for м in мес:
        о = охв[м]
        print(f"  {м} | {о['n']} | {о['with']} | {pc.доля(о['with'], о['n'])} | {о['lag_med']} | {о['lag_p75']}")
    печать_ряда("ЗАКАЗЫ ПОСТАВЩИКАМ СП-172", зак, мес,
                ["заказов", "живых", "провал", "первый заказ сделки", "отгружено поставщиком",
                 "доставлено клиенту", "доставлено со сроком", "доставлено позже срока", "дн заказ→отгрузка мед"])
    ключи = [f"{г}: {в}" for г in ("сорсинг", "КАМ", "продукт") for в in ("в штате", "пришли", "ушли (посл. вход)")]
    печать_ряда("ЛЮДИ (окно — с приёма по последний вход у отключённых)", люд, мес, ключи + ["КАМов с новой заявкой"])
    print("\nДЕНЬГИ — ТОЛЬКО ДОЛИ (квартал в сумме ряда; размер — медиана контракта квартала к медиане ряда)")
    print("  квартал | контрактов | доля суммы контрактов % | размер × | ТКП с суммой | доля суммы ТКП %")
    for q, д in деньги.items():
        print(f"  {q} | {д['n']} | {д['share']} | {д['size']} | {д['offers_n']} | {д['offers_share']}")


# ----------------------------------------------------------------------------- чтение

def _дата_поля(raw):
    s = str(raw or "")
    try:
        return dt.date.fromisoformat(s[:10]) if re.match(r"\d{4}-\d\d-\d\d", s) else None
    except ValueError:
        return None


def люди(client) -> dict[str, dict]:
    """Действующие и отключённые: {uid: {active, depts, pos, hired, last}} (как main._people)."""
    out: dict[str, dict] = {}
    for params, active in (({}, True), ({"ADMIN_MODE": True, "FILTER": {"ACTIVE": "N"}}, False)):
        for u in client.list_paged("user.get", params):
            uid = str(u.get("ID") or "")
            if not uid or uid in out:
                continue
            act = u.get("ACTIVE")
            dd = u.get("UF_DEPARTMENT") or []
            out[uid] = {"active": (str(act).lower() in ("y", "true", "1")) if act is not None else active,
                        "depts": [str(x) for x in (dd if isinstance(dd, list) else [dd]) if str(x)],
                        "pos": str(u.get("WORK_POSITION") or "").strip(),
                        "hired": _дата_поля(u.get("UF_EMPLOYMENT_DATE")) or _дата_поля(u.get("DATE_REGISTER")),
                        "last": _дата_поля(u.get("LAST_LOGIN"))}
    return out


def main() -> int:
    import config
    import kam
    import people
    from bitrix_client import BitrixClient, бюджет_портала, сводка_нагрузки

    техн = frozenset(people.TECH_CATS)
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    SINCE = pc.SINCE
    сегодня = dt.datetime.now(pc.MSK).date()
    мес = месяцы(FIRST_MONTH, сегодня.isoformat()[:7])
    рд = {м: рабочие_дни(границы(м)[0], min(границы(м)[1], сегодня - dt.timedelta(days=1))) for м in мес}
    с_карточек = f"{FIRST_MONTH}-01T00:00:00"
    ф_карточек = {"categoryId": SPA_CAT, ">=createdTime": с_карточек}

    env = client.call_envelope("crm.deal.list", {"filter": {">=DATE_CREATE": SINCE}, "select": ["ID"], "start": 0}) or {}
    всего = int(env.get("total") or 0)
    env = client.call_envelope("crm.item.list", {"entityTypeId": p26.ORDER_ENTITY, "filter": {}, "select": ["id"], "start": 0}) or {}
    ждём_заказов = int(env.get("total") or 0)
    env = client.call_envelope("crm.item.list", {"entityTypeId": SPA, "filter": ф_карточек, "select": ["id"], "start": 0}) or {}
    ждём_карточек = int(env.get("total") or 0)
    env = client.call_envelope("crm.deal.list", {"filter": {"CATEGORY_ID": 0, "<DATE_CREATE": SINCE, "STAGE_SEMANTIC_ID": "P"},
                                                 "select": ["ID"], "start": 0}) or {}
    до_since_открыто = int(env.get("total") or 0)
    if всего <= 0 or ждём_заказов <= 0 or ждём_карточек <= 0:
        print(f"::error::не получено число сделок ({всего}), заказов ({ждём_заказов}) или карточек ({ждём_карточек})")
        return 1
    rps, par = бюджет_портала()
    n = 4 + всего // 50 + 1 + всего // 150 + (всего // 50 + 1) * 6 + ждём_заказов // 50 + ждём_карточек // 50 + 120
    print(f"зонд v50 · {сегодня} · месяцев {len(мес)} ({мес[0]} … {мес[-1]}) · ожидается: сделок с {SINCE[:10]} — {всего}, "
          f"заказов — {ждём_заказов}, карточек СП-166 с {FIRST_MONTH} — {ждём_карточек}; "
          f"оценка ≈ {n} запросов, ≈ {n * par / rps / 60:.0f} мин при {rps:g}/с")

    сделки = client.list_deals_fast(filter={">=DATE_CREATE": SINCE}, select=[
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE",
        "COMPANY_ID", "ASSIGNED_BY_ID", people.KAM_F, people.KAM_OLD, "OPPORTUNITY", "CURRENCY_ID", *p26.ПОЛЯ_КП])
    print(f"прочитано сделок: {len(сделки)} из {всего}")
    if len(сделки) < всего:
        print("::error::обход сделок оборвался — итог был бы неполным")
        return 1

    сырые = client.list_items(p26.ORDER_ENTITY, filter={}, select=p26.ORDER_SELECT)
    if len(сырые) < ждём_заказов:
        print(f"::error::заказы СП-172 прочитаны не все: {len(сырые)} из {ждём_заказов}")
        return 1
    семантика, _ = p26.семантика_заказов(client, {str(о.get("categoryId")) for о in сырые if о.get("categoryId")})

    по_выборке = {str(д["ID"]) for д in сделки}
    вне = sorted({str(о.get("parentId2")) for о in сырые
                  if str(о.get("parentId2") or "0") != "0" and str(о.get("parentId2")) not in по_выборке}, key=int)
    старые = client.deals_by_ids(вне, select=["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID",
                                              "COMPANY_ID", "DATE_CREATE", "OPPORTUNITY", "CURRENCY_ID"])
    все_сделки = {str(д["ID"]): д for д in сделки} | старые
    нужны = {str(д.get("COMPANY_ID")) for д in все_сделки.values() if str(д.get("COMPANY_ID") or "0") != "0"}
    компании = client.companies_by_ids(нужны)
    известные = {имя for _, имя in kam.CLIENT_HOLDINGS}

    def холдинг_компании(cid):
        if not cid or cid == "0":
            return "Без клиента"
        имя = компании.get(cid, "")
        h = kam.client_dir(имя)
        return pc.HOLDING if h not in известные and pc.ГРУППА_ШИРЕ.search(имя) else h
    холдинг = {k: холдинг_компании(str(д.get("COMPANY_ID") or "0")) for k, д in все_сделки.items()}

    мета = client.deal_stage_meta()
    cats = client.categories()
    клиентские = {c for c, имя in cats.items() if kam.client_dir(str(имя)) == pc.HOLDING or pc.ГРУППА_ШИРЕ.search(str(имя))}

    история, ошибка = pc.читать_историю(client, [str(д["ID"]) for д in сделки])
    if ошибка:
        print(f"::error::{ошибка}")
        return 1
    без = len(сделки) - len(история)
    print(f"история стадий: сделок с записями {len(история)} из {len(сделки)}")
    if без > len(сделки) // 100:
        print("::error::без истории стадий больше 1 % сделок — итог был бы неполным")
        return 1

    валюты = client.call("crm.currency.list", {}) or []
    курс = {}
    for x in валюты:
        try:
            курс[x.get("CURRENCY")] = float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    база_валюта = next((x.get("CURRENCY") for x in валюты if x.get("BASE") == "Y"), "EUR")

    def в_евро(сумма, валюта):
        try:
            v = float(сумма or 0)
        except (TypeError, ValueError):
            return None
        k = 1.0 if (валюта or база_валюта) == база_валюта else курс.get(валюта)
        return v * k if (k and v > 0) else None
    деньги_сделок = {str(д["ID"]): в_евро(д.get("OPPORTUNITY"), д.get("CURRENCY_ID")) for д in сделки}
    бюджет = pc.читать_бюджеты(в_евро)

    состав = люди(client)
    if not состав:
        print("::error::справочник людей (user.get) не получен")
        return 1
    отдел_kam = {uid for uid, p in состав.items() if pc.DEPT_HOLDING in (p.get("depts") or [])}

    def кам_uid(д):
        for f in (people.KAM_F, people.KAM_OLD, "ASSIGNED_BY_ID"):
            u = people._uid(д.get(f))
            if u:
                return u
        return ""
    кам_сделки = {str(д["ID"]) for д in сделки if кам_uid(д) in отдел_kam}

    заказы_v4 = [о for о in сырые if not str(о.get("stageId", "")).endswith(":FAIL")]
    строки = pc.классифицировать(сделки, история, мета, заказы_v4, холдинг, сегодня, деньги_сделок)
    p26.разметить(строки, сделки, история, мета, сырые, семантика, клиентские, кам_сделки, бюджет, в_евро, сегодня, техн)
    даты_закрытия(строки, сделки, история, мета)
    по_id = {str(д["ID"]): д for д in сделки}
    for r in строки:
        r["kam"] = кам_uid(по_id[r["id"]])
    заказы_разм = p26.разметить_заказы(сырые, семантика, все_сделки, холдинг, {r["id"]: r for r in строки},
                                       клиентские, в_евро, бюджет)

    карточки = client.list_items(SPA, filter=ф_карточек, select=[
        "id", "createdBy", "assignedById", "createdTime", "parentId2", "stageId", *config.RFQ_QUOTE_FIELDS])
    print(f"карточек СП-166 с {FIRST_MONTH}: {len(карточки)} из {ждём_карточек}")
    if len(карточки) < ждём_карточек:
        print("::error::карточки СП-166 прочитаны не все — итог был бы неполным")
        return 1

    служебные = config.service_accounts(client.users())
    отдел = client.dept_member_ids(config.DEPT_SOURCING_ID)
    отделы = {str(d.get("ID")): str(d.get("NAME") or "") for d in client.departments()}
    роль = {uid: people.resolve_role(p, отделы)[0] for uid, p in состав.items()}
    print(f"людей в справочнике {len(состав)} (отключённых {sum(1 for p in состав.values() if not p['active'])}); "
          f"отдел поиска поставщиков (с отключёнными) {len(отдел)}; КАМ по роли {sum(1 for r in роль.values() if r == 'kam')}, "
          f"продукт {sum(1 for r in роль.values() if r == 'prod')}; служебных записей {len(служебные)}; "
          f"без даты приёма {sum(1 for p in состав.values() if not p['hired'])}")

    сд = ряд_сделок(строки, мес)
    ког = когорты_ткп(строки, мес, сегодня)
    вех, стадий_вех = вехи_реализации(история, мета, мес)
    print(f"стадий-вех воронки 0: {стадий_вех}")
    кар, охв = ряд_карточек(карточки, строки, мес, служебные, отдел)
    зак = ряд_заказов(заказы_разм, мес)
    люд = ряд_людей(состав, мес, отдел, роль, строки, служебные)
    деньги = доли_денег(строки, мес)
    отчёт(мес, рд, сд, ког, вех, кар, охв, зак, люд, деньги, до_since_открыто)
    print(сводка_нагрузки())
    return 0


if __name__ == "__main__":
    # Трассировка с содержимым сделки в публичный журнал не уходит (как в v4).
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        места = []
        tb = e.__traceback__
        while tb:
            места.append(f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}")
            tb = tb.tb_next
        print(f"::error::зонд упал: {type(e).__name__} · " + " → ".join(места[-4:]))
        raise SystemExit(1)
