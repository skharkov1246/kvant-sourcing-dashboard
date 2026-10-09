#!/usr/bin/env python3
"""Зонд: конверсия выданных ТКП в контракты по клиентским холдингам.

Вопрос владельца 08.10.2026: «какое количество контрактов мы получаем относительно
того количества предложений, которые мы выдаём» — по Норникелю и в сравнении.
Версия 4 (по сумме) с правкой 09.10.2026: история стадий — все входы; наценки в журнале нет.

МОДЕЛЬ — ТА ЖЕ, ЧТО У history.py И reps.py (проверено на портале 08.09.2026):
  • ВОРОНКА сделки — первая НЕНУЛЕВАЯ воронка в истории стадий. Кат. 0 — и воронка
    реализации, и воронка по умолчанию: карточка, заведённая в кат. 0 и перенесённая
    в предпродажу, — предпродажная сделка, а не «только реализация».
  • КОНТРАКТ — сделка не проиграна (STAGE_SEMANTIC_ID ≠ F) и хотя бы одно из:
    вход в кат. 0 ПОСЛЕ предпродажной воронки; непроигранный заказ поставщику СП-172;
    номер реализации в названии («871. …»); семантика S — как reps.classify.
  • ВЫДАННОЕ ПРЕДЛОЖЕНИЕ — по истории стадий:
      – «с датой»: стадия, чьё имя говорит, что предложение выдано или отправлено
        («ТКП выдано/отправлено», «Тендерное предложение выдано», «Quotation issued»,
        «КП отправлено», «Заявка подана»). «ТКП готово», «Отправка ТКП»,
        «Подготовка ТКП», сбор КП у поставщиков — ещё не выдано; суффикс EXECUTING
        сам по себе не засчитывается;
      – «без даты»: кат. 0 после предпродажи, успех, переторжка и торги, отгрузка,
        проигрыш с именем «не прошли по цене / по технике», «не выиграли».
  • ГЛАВНАЯ КОНВЕРСИЯ — СИММЕТРИЧНАЯ: в числителе и знаменателе только сделки с
    датированной стадией предложения. Выигрыш без отметки предложения доказывает
    предложение переездом в кат. 0, а такой же проигрыш — ничем, и широкая
    конверсия (с отметками «без даты») поэтому завышена; она печатается рядом.
  • ЗРЕЛАЯ — те же сделки, но предложение выдано раньше, чем STUCK_DAYS назад:
    исход у них успел определиться (медиана предложение→контракт печатается рядом).
  • ВЫИГРЫШ НОВОЙ КАРТОЧКОЙ. До 2025 г. реализацию заводили отдельной карточкой в
    кат. 0, а предпродажная оставалась открытой: первый прогон дал 0 контрактов
    Норникеля за 2024 г. и 61 карточку «только реализации». Такой карточке ищется
    предпродажная сделка той же компании с предложением, выданным не позже её
    создания и не раньше чем за 180 дней; найденные печатаются как вероятные
    выигрыши — оценка, а не факт.
  • ХОЛДИНГ — kam.client_dir плюс шаблон юрлиц группы, которых разметка КАМ не
    знает (только для названий, которые разметка КАМ не отнесла к другому холдингу).
    Вторая разметка — по КАМ сделки (поле «КАМ», иначе ответственный) из отдела 110.

ЧТО В ЖУРНАЛ. Репозиторий публичный (CLAUDE.md, правило 17; распоряжение 30.09):
счётчики, доли, медианы и названия холдингов разметки КАМ. Названия компаний не
печатаются вовсе; в названиях воронок и стадий остаются только слова, которые
встречаются в стадиях трёх и более воронок (общая лексика процесса), прочие слова —
«…». Ни сумм, ни сделок, ни людей. Падение — тип ошибки и места в коде.

НАГРУЗКА (навык bitrix-ingest): сделки — по ключу >ID; компании — пачками по 50;
история стадий — пачками по 50 сделок (OWNER_ID массивом) со сверкой с total и
проверкой фильтра; заказы СП-172 — по ключу со сверкой с total. Около 900 запросов.
"""
from __future__ import annotations

import collections
import datetime as dt
import os
import re
import statistics
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SINCE = os.environ.get("CONV_SINCE", "2024-06-01T00:00:00")   # глубина, как HIST_SINCE в contracts.py
HOLDING = "Норникель"
DEPT_HOLDING = "110"           # kam.CLIENT_GROUPS: «Норникель»
ORDER_ENTITY = 172
STUCK_DAYS = 120               # history.STUCK_DAYS
OUTLIER_EUR = 20_000_000       # history.OUTLIER_EUR: выше — почти всегда ошибка ввода
TWIN_DAYS = 180                # окно поиска предпродажной сделки для карточки реализации
MIN_N = 30                     # меньше — доля печатается с пометкой «мало данных»
MSK = ZoneInfo("Europe/Moscow")

ГРУППА_ШИРЕ = re.compile(r"норникел|nornickel|гипроникел|норильско[-\s]*таймырск|норметимпэкс", re.I)
ПРЕДЛОЖЕНИЕ = re.compile(
    r"ТКП\s*(выда|отправл|соглас)|тендерн\w*\s+предложен\w*\s+выда|quotation\s+issued|"
    r"(?<![А-Яа-яЁё])КП\s*(отправл|выда|направл)|коммерческ\w*\s+предложен\w*\s+(выда|отправл|направл)|"
    r"предложени\w*\s+(направл|отправл)\w*\s+клиент|заявк\w*\s+подан", re.I)
ЕЩЁ_НЕ_ВЫДАНО = re.compile(r"ТКП\s*готов|отправка\s+ТКП|подготовк\w*\s+ТКП|сбор\s+КП|поиск\s|не\s*выда|"
                           r"не\s*ответ|не\s*присл|закрыл\s+выдачу|неинтересно", re.I)
ПОСЛЕ_ПРЕДЛОЖЕНИЯ = re.compile(r"переторжк|(?<![А-Яа-яЁё])торги(?![А-Яа-яЁё])|ожидаем\w*\s+решени", re.I)
ПРОИГРЫШ_ПОСЛЕ_ПРЕДЛОЖЕНИЯ = re.compile(
    r"не\s*прошл\w*\s*по\s*(цен|тех|срок)|проигра|не\s*выигра|выбрал\w*\s*друг|победител|победил\w*\s*друг|"
    r"цена\s*выше", re.I)
ПОСЛЕ_РЕАЛИЗАЦИИ = re.compile(r"ОТГРУ|ДОСТАВ|ПРОИЗВОДСТВО\s*ЗАВЕРШЕНО|ПОДГОТОВКА\s*К\s*ОТГРУЗКЕ", re.I)
ОБЩИЕ_СЛОВА = {"общая", "тендеры", "тендер", "запросы", "запрос", "пресейл", "реклама", "адаптационная",
               "реализация", "сделки", "сделка", "ткп", "кп", "клиент", "клиентом", "заказчик"}


def номер_реализации(title) -> int:
    """Номер реализации в начале названия («871. …»), как company._regno."""
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(title or ""))
    return int(m.group(1)) if m else 0


def воронка_стадии(stage_id) -> str:
    """'C2:NEW' → '2', 'NEW' → '0' (history._cat_of_stage)."""
    s = str(stage_id or "")
    if s.startswith("C") and ":" in s:
        head = s[1:s.index(":")]
        return head if head.isdigit() else "0"
    return "0"


def дата_мск(s) -> str | None:
    """Метка портала → дата по Москве (история и заказы приходят с разными смещениями)."""
    try:
        d = dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return str(s)[:10] if re.match(r"\d{4}-\d\d-\d\d", str(s or "")) else None
    if d.tzinfo is None:
        d = d.replace(tzinfo=MSK)
    return d.astimezone(MSK).date().isoformat()


def вид_стадии(stage_id, meta) -> tuple[str, str]:
    """Стадия → (вид, правило): 'offer' — предложение выдано (даёт дату), 'beyond' —
    дальше предложения (факт без даты), 'none'. Правило — константа кода."""
    м = meta.get(stage_id) or {}
    имя, sem = str(м.get("name") or ""), str(м.get("sem") or "").upper()
    if воронка_стадии(stage_id) == "0":
        return "beyond", "воронка реализации"
    if sem == "S":
        return "beyond", "успех"
    if sem == "F":
        if ПРОИГРЫШ_ПОСЛЕ_ПРЕДЛОЖЕНИЯ.search(имя):
            return "beyond", "проигрыш после предложения"
        return "none", "проигрыш"
    if ПОСЛЕ_РЕАЛИЗАЦИИ.search(имя):
        return "beyond", "отгрузка/производство"
    if ЕЩЁ_НЕ_ВЫДАНО.search(имя):
        return "none", "ещё не выдано"
    if ПРЕДЛОЖЕНИЕ.search(имя):
        return "offer", "имя: предложение выдано"
    if ПОСЛЕ_ПРЕДЛОЖЕНИЯ.search(имя):
        return "beyond", "торги/ожидание решения"
    if str(stage_id).endswith(":EXECUTING"):
        return "none", "до предложения (суффикс EXECUTING не засчитан)"
    return "none", "до предложения"


def медиана(xs):
    return round(statistics.median(xs)) if xs else None


def перцентиль(xs, p):
    if not xs:
        return None
    v = sorted(xs)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def доля(a, b):
    return round(100 * a / b) if b else None


def _дата(s):
    return dt.date.fromisoformat(s) if s else None


def классифицировать(сделки, история, мета, заказы, холдинг_сделки, сегодня, деньги_сделок=None):
    """Сделки → строка на сделку. Чистая функция.

    история — {deal_id: [(stage_id, iso_time), …]} всех входов в стадии;
    заказы — crm.item СП-172 без проигранных; холдинг_сделки — {deal_id: имя}.
    """
    первый_заказ: dict[str, str] = {}
    for о in заказы:
        d = str(о.get("parentId2") or "")
        t = дата_мск(о.get("createdTime"))
        if d and d != "0" and t and (d not in первый_заказ or t < первый_заказ[d]):
            первый_заказ[d] = t
    out = []
    for д in сделки:
        did = str(д["ID"])
        ряд = sorted(история.get(did, []), key=lambda r: str(r[1]))
        ненулевые = [(s, t) for s, t in ряд if воронка_стадии(s) != "0"]
        if ненулевые:
            origin = воронка_стадии(ненулевые[0][0])
            после = str(ненулевые[0][1])
            # Вход в кат. 0 после предпродажи, после которого сделка в предпродажу
            # не возвращалась (возврат — не выигрыш, а перекладка карточки).
            в_кат0 = next((дата_мск(t) for s, t in ряд if воронка_стадии(s) == "0" and str(t) > после
                           and not any(воронка_стадии(s2) != "0" and str(t2) > str(t) for s2, t2 in ряд)), None)
        else:
            origin = "0" if ряд or str(д.get("CATEGORY_ID") or "0") == "0" else str(д.get("CATEGORY_ID"))
            в_кат0 = None
        sem = str(д.get("STAGE_SEMANTIC_ID") or "").upper()
        сигналы = {"кат0": bool(в_кат0), "заказ": did in первый_заказ,
                   "номер": номер_реализации(д.get("TITLE")) > 0, "успех": sem == "S"}
        cls = "lost" if sem == "F" else ("contract" if any(сигналы.values()) else "open")
        виды = [(вид_стадии(s, мета)[0], дата_мск(t)) for s, t in ряд if воронка_стадии(s) != "0"]
        if в_кат0:
            виды.append(("beyond", в_кат0))
        дата_предложения = min((t for v, t in виды if v == "offer" and t), default=None)
        if дата_предложения:
            откуда = "history"
        elif any(v == "beyond" for v, _ in виды):
            откуда = "beyond"
        elif cls == "contract":
            откуда = "contract_only"
        else:
            откуда = "none"
        дата_контракта = (в_кат0 or первый_заказ.get(did)) if cls == "contract" else None
        создана = дата_мск(д.get("DATE_CREATE"))
        опорная = дата_предложения or создана
        возраст = (сегодня - _дата(опорная)).days if опорная else None
        out.append({
            "id": did, "company": str(д.get("COMPANY_ID") or "0"),
            "holding": холдинг_сделки.get(did) or "Без клиента", "origin": origin,
            "realization_only": origin == "0", "cls": cls, "signals": сигналы,
            "offer": откуда in ("history", "beyond"), "offer_dated": откуда == "history",
            "offer_from": откуда, "offer_date": дата_предложения, "contract_date": дата_контракта,
            "created": создана, "cohort": (опорная or "")[:4] or "?",
            "mature": откуда == "history" and возраст is not None and возраст > STUCK_DAYS,
            "stuck": cls == "open" and возраст is not None and возраст > STUCK_DAYS,
            "young": cls == "open" and (возраст is None or возраст <= STUCK_DAYS),
            "twin_of": None,
            "amount": деньги_сделок.get(did) if деньги_сделок else None,
        })
    return out


def найти_выигрыши_новой_карточкой(строки):
    """Карточке «только реализации» — предпродажная сделка той же компании, не
    контракт, с датированным предложением за 0…TWIN_DAYS дней до её создания.
    Помечает найденную предпродажную сделку полем twin_of. → число пар."""
    по_компании = collections.defaultdict(list)
    for r in строки:
        if not r["realization_only"] and r["offer_dated"] and r["cls"] != "contract" and r["company"] != "0":
            по_компании[r["company"]].append(r)
    for сп in по_компании.values():
        сп.sort(key=lambda r: r["offer_date"])
    пар = 0
    for real in sorted((r for r in строки if r["realization_only"] and r["company"] != "0"),
                       key=lambda r: r["created"] or ""):
        c = _дата(real["created"])
        if not c:
            continue
        кандидаты = [r for r in по_компании.get(real["company"], [])
                     if r["twin_of"] is None and 0 <= (c - _дата(r["offer_date"])).days <= TWIN_DAYS]
        if кандидаты:
            кандидаты[-1]["twin_of"] = real["id"]        # ближайшее по времени предложение
            пар += 1
    return пар


def свод(строки):
    """Строки одной выборки → счётчики конверсии предложение → контракт."""
    база = [r for r in строки if not r["realization_only"]]
    д = [r for r in база if r["offer_dated"]]                    # симметричная база
    дк = [r for r in д if r["cls"] == "contract"]
    дл = [r for r in д if r["cls"] == "lost"]
    шир = [r for r in база if r["offer"]]
    шк = [r for r in шир if r["cls"] == "contract"]
    зр = [r for r in д if r["mature"]]
    зк = [r for r in зр if r["cls"] == "contract"]
    близнецы = [r for r in д if r["twin_of"]]
    дни = []
    for r in дк:
        a, b = _дата(r["offer_date"]), _дата(r["contract_date"])
        if a and b and b >= a:
            дни.append((b - a).days)
    без_даты = [r for r in шир if r["offer_from"] == "beyond"]
    return {
        "offers": len(д), "contracts": len(дк), "lost": len(дл),
        "stuck": sum(1 for r in д if r["stuck"]), "young": sum(1 for r in д if r["young"]),
        "conv": доля(len(дк), len(д)),
        "conv_mature": доля(len(зк), len(зр)), "mature_n": len(зр),
        "conv_twins": доля(len(дк) + len(близнецы), len(д)), "twins": len(близнецы),
        "offers_wide": len(шир), "contracts_wide": len(шк), "conv_wide": доля(len(шк), len(шир)),
        "undated_won": sum(1 for r in без_даты if r["cls"] == "contract"),
        "undated_lost": sum(1 for r in без_даты if r["cls"] == "lost"),
        "contracts_only": sum(1 for r in база if r["cls"] == "contract" and r["offer_from"] == "contract_only"),
        "realization_only": sum(1 for r in строки if r["realization_only"]),
        "days_median": медиана(дни), "days_p90": перцентиль(дни, 90), "days_n": len(дни),
    }


def свод_денег(строки, закупка, бюджет, товарные):
    """Конверсия ПО СУММЕ и по строкам, только доли и отношения (журнал публичный).

    закупка — {deal_id: Σ заказов поставщикам, €}; бюджет — {deal_id: выручка без НДС, €};
    товарные — {deal_id: число товарных строк сделки}. База — сделки с датированным
    предложением (как у симметричной конверсии); суммы выше OUTLIER_EUR — ошибки ввода.
    """
    база = [r for r in строки if not r["realization_only"] and r["offer_dated"]]
    с_суммой = [r for r in база if r.get("amount") and 0 < r["amount"] <= OUTLIER_EUR]
    выбросов = sum(1 for r in база if r.get("amount") and r["amount"] > OUTLIER_EUR)
    к = [r for r in с_суммой if r["cls"] == "contract"]
    п = [r for r in с_суммой if r["cls"] == "lost"]
    всего = sum(r["amount"] for r in с_суммой)
    взято = sum(r["amount"] for r in к)
    # контракт по выручке бюджета, где бюджет есть, иначе — по сумме сделки
    взято_б = sum(бюджет.get(r["id"]) or r["amount"] for r in к)
    к_б = [r for r in к if бюджет.get(r["id"])]
    к_з = [r for r in к if закупка.get(r["id"])]
    отн_б = sorted(r["amount"] / бюджет[r["id"]] for r in к_б)
    отн_з = sorted(r["amount"] / закупка[r["id"]] for r in к_з)
    стр = [r for r in база if товарные.get(r["id"])]
    стр_к = [r for r in стр if r["cls"] == "contract"]
    def мед(xs):
        return round(statistics.median(xs), 2) if xs else None
    return {
        "n": len(база), "with_amount": len(с_суммой), "outliers": выбросов,
        "conv_value": доля(взято, всего), "conv_value_budget": доля(взято_б, всего),
        "won_vs_lost_size": (round(statistics.median([r["amount"] for r in к]) /
                                   statistics.median([r["amount"] for r in п]), 2) if к and п else None),
        "share_top10": доля(sum(sorted((r["amount"] for r in с_суммой), reverse=True)[:max(1, len(с_суммой) // 10)]), всего),
        "budget_n": len(к_б), "deal_to_budget_med": мед(отн_б),
        "deal_over_2x_budget": sum(1 for x in отн_б if x > 2), "deal_under_half_budget": sum(1 for x in отн_б if x < 0.5),
        "purchase_n": len(к_з), "deal_to_purchase_med": мед(отн_з), "deal_over_3x_purchase": sum(1 for x in отн_з if x > 3),
        "rows_n": len(стр), "rows_contracts": len(стр_к),
        "conv_rows": доля(sum(товарные[r["id"]] for r in стр_к), sum(товарные[r["id"]] for r in стр)),
        "rows_med_won": мед([товарные[r["id"]] for r in стр_к]),
        "rows_med_lost": мед([товарные[r["id"]] for r in стр if r["cls"] == "lost"]),
    }


def строка_денег(имя, д):
    def f(v, n=None):
        return "—" if v is None else (f"{v}%" + (" (мало данных)" if n is not None and n < MIN_N else ""))
    return (f"  {str(имя)[:30]:30} ПО СУММЕ {f(д['conv_value'], д['with_amount'])}"
            f" · по сумме с выручкой бюджета вместо суммы сделки {f(д['conv_value_budget'])}"
            f" | сумма есть у {д['with_amount']} из {д['n']} (выбросов > {OUTLIER_EUR / 1e6:g} млн € {д['outliers']})"
            f" | выигранная/проигранная по медиане суммы ×{д['won_vs_lost_size']}"
            f" · 10 % крупнейших предложений = {f(д['share_top10'])} всей суммы"
            f" | у контрактов сумма сделки / выручка бюджета: медиана ×{д['deal_to_budget_med']} (по {д['budget_n']};"
            f" больше ×2 у {д['deal_over_2x_budget']}, меньше ×0,5 у {д['deal_under_half_budget']})"
            # «сумма сделки / закупка» — по сути наценка, коммерческое сведение: в
            # публичный журнал не печатается (разбор 09.10.2026), считается для тестов.
            f" | ПО СТРОКАМ {f(д['conv_rows'], д['rows_n'])} (товарные строки есть у {д['rows_n']} предложений,"
            f" {д['rows_contracts']} контрактов; медиана строк: выигр. {д['rows_med_won']}, проигр. {д['rows_med_lost']})")


def строка_свода(имя, с):
    def f(v, n):
        if v is None:
            return "—"
        return f"{v}%" + (" (мало данных)" if n < MIN_N else "")
    dm = "—" if с["days_median"] is None else f"медиана {с['days_median']} / 90% {с['days_p90']} дн. (по {с['days_n']})"
    return (f"  {str(имя)[:30]:30} ПРЕДЛОЖЕНИЙ {с['offers']:5} · КОНТРАКТОВ {с['contracts']:4} · КОНВЕРСИЯ {f(с['conv'], с['offers'])}"
            f" | проиграно {с['lost']} · тихих потерь {с['stuck']} · молодых {с['young']}"
            f" | зрелая {f(с['conv_mature'], с['mature_n'])} (по {с['mature_n']})"
            f" | с выигрышами новой карточкой {f(с['conv_twins'], с['offers'])} (+{с['twins']})"
            f" | широкая {f(с['conv_wide'], с['offers_wide'])} ({с['contracts_wide']}/{с['offers_wide']}; без даты: "
            f"выигр. {с['undated_won']}, проигр. {с['undated_lost']})"
            f" | предложение→контракт {dm} | контрактов без отметки {с['contracts_only']},"
            f" карточек только реализации {с['realization_only']}")


def общая_лексика(мета):
    """Слова, встречающиеся в названиях стадий трёх и более воронок: язык процесса,
    а не названия клиентов и не фамилии."""
    где = collections.defaultdict(set)
    for sid, m in мета.items():
        for w in re.findall(r"[A-Za-zА-Яа-яЁё]+", str(m.get("name") or "")):
            где[w.lower()].add(воронка_стадии(sid))
    return {w for w, cats in где.items() if len(cats) >= 3} | ОБЩИЕ_СЛОВА


def обезличить(текст, лексика):
    """Слова вне общей лексики → «…»; цифры и знаки остаются."""
    return re.sub(r"[A-Za-zА-Яа-яЁё]+", lambda m: m.group(0) if m.group(0).lower() in лексика else "…",
                  str(текст or ""))


def имя_воронки(cat, cats, лексика):
    имя = str(cats.get(cat) or "")
    слова = re.findall(r"[A-Za-zА-Яа-яЁё]+", имя)
    if слова and all(w.lower() in лексика for w in слова):
        return f"воронка {cat} «{имя}»"
    return f"воронка {cat}"


def отчёт(строки, история, кам_сделки, мета, cats, закупка=None, бюджет=None, товарные=None):
    import kam
    лексика = общая_лексика(мета)
    print(f"\nВЫБОРКА: сделки, созданные с {SINCE[:10]}; одна сделка — одно предложение; год — по дате "
          f"предложения, иначе создания; {SINCE[:4]} — неполный год. КОНВЕРСИЯ — симметричная (только сделки "
          f"с датированной стадией предложения); зрелая — предложения старше {STUCK_DAYS} дн.")

    прошли = collections.Counter()
    for ряд in история.values():
        for s in {s for s, _ in ряд}:
            прошли[s] += 1
    по_воронке = collections.defaultdict(list)
    for sid, m in мета.items():
        if прошли.get(sid):
            v, why = вид_стадии(sid, мета)
            по_воронке[воронка_стадии(sid)].append(
                (int(m.get("sort") or 0), f"{sid} {обезличить(m.get('name'), лексика)} [{v} · {why} · {прошли[sid]}]"))
    чужие = sorted(s for s in прошли if s not in мета)
    print("\nКлассификация стадий (ID имя [вид · правило · сделок прошло]):")
    for cat in sorted(по_воронке, key=int):
        print(f"  {имя_воронки(cat, cats, лексика)}: " + "; ".join(t for _, t in sorted(по_воронке[cat])))
    if чужие:
        print(f"  стадий в истории без справочника (удалены): {len(чужие)}, сделок через них "
              f"{sum(прошли[s] for s in чужие)} — считаются «до предложения»")

    print("\nКОНВЕРСИЯ ПРЕДЛОЖЕНИЕ → КОНТРАКТ")
    print(строка_свода("Все клиенты", свод(строки)))
    hn = [r for r in строки if r["holding"] == HOLDING]
    print(строка_свода(HOLDING, свод(hn)))

    известные = {имя for _, имя in kam.CLIENT_HOLDINGS} | {"Без клиента"}
    по_холдингу = collections.defaultdict(list)
    for r in строки:
        по_холдингу[r["holding"] if r["holding"] in известные else "Прочие клиенты"].append(r)
    print("\nДля сравнения — холдинги разметки КАМ, прочие клиенты одной строкой:")
    for h in sorted((h for h in по_холдингу if h != HOLDING), key=lambda h: -свод(по_холдингу[h])["offers"]):
        print(строка_свода(h, свод(по_холдингу[h])))

    for заголовок, выборка in ((HOLDING, hn), ("Все клиенты", строки)):
        print(f"\n{заголовок} — по году:")
        годы = sorted({r["cohort"] for r in выборка})
        for год in годы:
            print(строка_свода(год, свод([r for r in выборка if r["cohort"] == год])))
        if sum(свод([r for r in выборка if r["cohort"] == г])["offers"] for г in годы) != свод(выборка)["offers"]:
            raise RuntimeError("годы не сходятся с итогом")

    if закупка is not None:
        import kam as _kam
        print("\nКОНВЕРСИЯ ПО СУММЕ И ПО СТРОКАМ (доли и отношения; сумм в журнале нет)")
        hn_ = [r for r in строки if r["holding"] == HOLDING]
        print(строка_денег("Все клиенты", свод_денег(строки, закупка, бюджет, товарные)))
        print(строка_денег(HOLDING, свод_денег(hn_, закупка, бюджет, товарные)))
        изв = {имя for _, имя in _kam.CLIENT_HOLDINGS}
        for h in sorted(изв - {HOLDING}):
            вы = [r for r in строки if r["holding"] == h]
            if вы:
                print(строка_денег(h, свод_денег(вы, закупка, бюджет, товарные)))
        for заголовок, выборка in ((HOLDING, hn_), ("Все клиенты", строки)):
            print(f"{заголовок} — по сумме, по году:")
            for год in sorted({r["cohort"] for r in выборка}):
                print(строка_денег(год, свод_денег([r for r in выборка if r["cohort"] == год], закупка, бюджет, товарные)))

    print(f"\n{HOLDING} — по воронке, где сделка заведена:")
    for cat in sorted({r["origin"] for r in hn}, key=lambda x: -свод([r for r in hn if r["origin"] == x])["offers"]):
        с = свод([r for r in hn if r["origin"] == cat])
        if с["offers"] or с["offers_wide"]:
            print(строка_свода(имя_воронки(cat, cats, лексика), с))

    for заголовок, выборка in ((HOLDING, hn), ("Все клиенты", строки)):
        к = [r for r in выборка if r["cls"] == "contract" and not r["realization_only"]]
        только_р = [r for r in выборка if r["realization_only"]]
        print(f"\n{заголовок} — признаки контракта: " + ", ".join(
            f"{s} {sum(1 for r in к if r['signals'][s])}" for s in ("кат0", "заказ", "номер", "успех"))
            + f"; без переезда в кат. 0 {sum(1 for r in к if not r['signals']['кат0'])}"
            + f". Карточек только реализации {len(только_р)}: с заказом/номером "
            + f"{sum(1 for r in только_р if r['signals']['заказ'] or r['signals']['номер'])}, проиграно "
            + f"{sum(1 for r in только_р if r['cls'] == 'lost')}; найдено предпродажных пар {sum(1 for r in выборка if r['twin_of'])}")

    по_каму = [r for r in строки if r["id"] in кам_сделки]
    общие = {r["id"] for r in hn} & кам_сделки
    print(f"\nПроверка разметки {HOLDING}: по компании {len(hn)} сделок, по КАМ из отдела {DEPT_HOLDING} "
          f"{len(по_каму)}, в обеих {len(общие)}; у КАМ-сделок без компании "
          f"{sum(1 for r in по_каму if r['holding'] == 'Без клиента')}, другой компании "
          f"{sum(1 for r in по_каму if r['holding'] not in (HOLDING, 'Без клиента'))}")
    print(строка_свода(f"{HOLDING} (по КАМ)", свод(по_каму)))
    print(строка_свода(f"{HOLDING} (обе разметки)", свод([r for r in hn if r["id"] in общие])))


def читать_бюджеты(в_евро):
    """Выручка без НДС из снимка бюджетов сделок (data/budget_snapshot.json, суммы в
    рублях; пишет бот scripts/budget_snapshot.py) → {deal_id: €}."""
    import json
    p = Path(__file__).resolve().parents[1] / "data" / "budget_snapshot.json"
    try:
        снимок = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for b in снимок.get("budgets") or []:
        m = re.search(r"/deal/details/(\d+)", str(b.get("deal_url") or ""))
        v = в_евро(b.get("revenue_net"), "RUB")
        if m and v:
            out[m.group(1)] = v
    return out


def товарные_строки(client, ids):
    """Число товарных строк сделок пакетом batch по 50 команд → {deal_id: строк}."""
    from urllib import parse
    out: dict[str, int] = {}
    for i in range(0, len(ids), 50):
        часть = ids[i:i + 50]
        cmd = {f"d{d}": "crm.deal.productrows.get?" + parse.urlencode({"id": d}) for d in часть}
        res = client.call("batch", {"halt": 0, "cmd": cmd}) or {}
        рез = res.get("result") or {}
        for d in часть:
            ряды = рез.get(f"d{d}") if isinstance(рез, dict) else None
            out[d] = len(ряды) if isinstance(ряды, list) else 0
    return out


def читать_историю(client, ids):
    """История стадий пачками по 50 сделок, со сверкой с total. → (история, ошибка)."""
    история: dict[str, list] = {}
    for i in range(0, len(ids), 50):
        часть = [int(x) for x in ids[i:i + 50]]
        свои = {str(x) for x in часть}
        start, total, got = 0, None, 0
        while True:
            data = client.call_envelope("crm.stagehistory.list", {
                "entityTypeId": 2, "filter": {"OWNER_ID": часть},
                "select": ["ID", "OWNER_ID", "CREATED_TIME", "STAGE_ID"],
                "order": {"ID": "ASC"}, "start": start}) or {}
            res = data.get("result") or {}
            items = (res.get("items") if isinstance(res, dict) else res) or []
            if total is None:
                if "total" not in data:
                    return история, "в ответе истории стадий нет total — полноту не проверить"
                total = int(data.get("total") or 0)
                if ({str(x.get("OWNER_ID")) for x in items} - свои) or total > 50 * 80:
                    return история, "фильтр OWNER_ID массивом не сработал — обход остановлен"
            got += len(items)
            for x in items:
                o, s = str(x.get("OWNER_ID")), str(x.get("STAGE_ID") or "")
                t = str(x.get("CREATED_TIME") or "")
                # Все входы, а не только первый в стадию: сделка, заведённая в кат. 0
                # (NEW), переведённая в предпродажу и выигранная обратно в NEW, иначе
                # теряла признак «кат0» (разбор 09.10.2026); повторные входы в стадию
                # предложения — переторжки и ревизии ТКП.
                if o in свои and s and (s, t) not in история.get(o, []):
                    история.setdefault(o, []).append((s, t))
            nxt = data.get("next")
            if not nxt or not items:
                break
            start = nxt
        if got < total:
            return история, f"история стадий неполна в пачке {i // 50 + 1}: {got} из {total}"
    return история, None


def main() -> int:
    import config
    import kam
    import people
    from bitrix_client import BitrixClient, бюджет_портала, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    env = client.call_envelope("crm.deal.list", {"filter": {">=DATE_CREATE": SINCE}, "select": ["ID"], "start": 0}) or {}
    всего = int(env.get("total") or 0)
    env = client.call_envelope("crm.item.list", {"entityTypeId": ORDER_ENTITY, "filter": {},
                                                 "select": ["id"], "start": 0}) or {}
    ждём_заказов = int(env.get("total") or 0)
    if всего <= 0 or ждём_заказов <= 0:
        print(f"::error::не получено число сделок ({всего}) или заказов ({ждём_заказов}) — полноту не проверить")
        return 1
    rps, par = бюджет_портала()
    n = 2 + всего // 50 + 1 + всего // 150 + (всего // 50 + 1) * 6 + ждём_заказов // 50 + 1 + 60
    print(f"ожидается: сделок с {SINCE[:10]} — {всего}, заказов поставщикам — {ждём_заказов}; "
          f"оценка ≈ {n} запросов, ≈ {n * par / rps / 60:.0f} мин при {rps:g}/с без учёта задержек сети")

    сделки = client.list_deals_fast(filter={">=DATE_CREATE": SINCE}, select=[
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE",
        "COMPANY_ID", "ASSIGNED_BY_ID", people.KAM_F, people.KAM_OLD, "OPPORTUNITY", "CURRENCY_ID"])
    print(f"прочитано сделок: {len(сделки)} из {всего}")
    if len(сделки) < всего:
        print("::error::обход сделок оборвался — итог был бы неполным")
        return 1

    нужны = {str(д.get("COMPANY_ID")) for д in сделки if str(д.get("COMPANY_ID") or "0") != "0"}
    компании = client.companies_by_ids(нужны)
    print(f"компаний у сделок: запрошено {len(нужны)}, получено {len(компании)}; сделок с компанией, "
          f"которую портал не вернул: {sum(1 for д in сделки if str(д.get('COMPANY_ID') or '0') in нужны - set(компании))}")

    известные = {имя for _, имя in kam.CLIENT_HOLDINGS}

    def холдинг_компании(cid):
        if not cid or cid == "0":
            return "Без клиента"
        имя = компании.get(cid, "")
        h = kam.client_dir(имя)
        if h not in известные and ГРУППА_ШИРЕ.search(имя):
            return HOLDING
        return h
    холдинг = {str(д["ID"]): холдинг_компании(str(д.get("COMPANY_ID") or "0")) for д in сделки}
    по_разметке = sum(1 for n in компании.values() if kam.client_dir(n) == HOLDING)
    шире = sum(1 for n in компании.values() if kam.client_dir(n) not in известные and ГРУППА_ШИРЕ.search(n))
    print(f"юрлиц {HOLDING}: по разметке КАМ {по_разметке}, добавлено шаблоном группы {шире} (названия не печатаются)")

    состав = people.roster(client)
    if not состав:
        print("::error::состав портала (user.get) не получен — проверку по КАМ не сделать")
        return 1
    отдел = {uid for uid, p in состав.items() if DEPT_HOLDING in (p.get("depts") or [])}

    def кам_сделки_(д):
        for f in (people.KAM_F, people.KAM_OLD, "ASSIGNED_BY_ID"):
            u = people._uid(д.get(f))
            if u:
                return u
        return ""
    кам_сделки = {str(д["ID"]) for д in сделки if кам_сделки_(д) in отдел}
    print(f"отдел {DEPT_HOLDING}: сотрудников (вкл. уволенных) {len(отдел)}, сделок по КАМ {len(кам_сделки)}")

    мета = client.deal_stage_meta()
    cats = client.categories()

    история, ошибка = читать_историю(client, [str(д["ID"]) for д in сделки])
    if ошибка:
        print(f"::error::{ошибка}")
        return 1
    без = len(сделки) - len(история)
    print(f"история стадий: сделок с записями {len(история)} из {len(сделки)}, без записей {без}")
    if без > len(сделки) // 100:
        print("::error::без истории стадий больше 1 % сделок — итог был бы неполным")
        return 1

    сырые = client.list_items(ORDER_ENTITY, filter={}, select=["id", "stageId", "createdTime", "parentId2",
                                                                "opportunity", "currencyId"])
    if len(сырые) < ждём_заказов:
        print(f"::error::заказы СП-172 прочитаны не все: {len(сырые)} из {ждём_заказов}")
        return 1
    заказы = [о for о in сырые if not str(о.get("stageId", "")).endswith(":FAIL")]
    print(f"заказов поставщикам: {len(сырые)}, непроигранных {len(заказы)}")

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
    закупка: dict[str, float] = collections.defaultdict(float)
    for о in заказы:
        v = в_евро(о.get("opportunity"), о.get("currencyId"))
        if v and str(о.get("parentId2") or "0") != "0":
            закупка[str(о.get("parentId2"))] += v
    бюджет = читать_бюджеты(в_евро)
    print(f"курсы валют: {len(курс)}, база {база_валюта}; бюджетов сделок с выручкой: {len(бюджет)}")

    строки = классифицировать(сделки, история, мета, заказы, холдинг, dt.datetime.now(MSK).date(), деньги_сделок)
    пар = найти_выигрыши_новой_карточкой(строки)
    print(f"карточек только реализации с найденной предпродажной парой: {пар}")
    предложения = [r["id"] for r in строки if r["offer_dated"] and not r["realization_only"]]
    товарные = товарные_строки(client, предложения)
    print(f"товарные строки: прочитано по {len(предложения)} сделкам с предложением, есть у {sum(1 for v in товарные.values() if v)}")
    отчёт(строки, история, кам_сделки, мета, cats, dict(закупка), бюджет, товарные)
    print(сводка_нагрузки())
    return 0


if __name__ == "__main__":
    # Трассировка с содержимым сделки в публичный журнал не уходит: печатаются
    # только тип ошибки и места в коде (файл:строка), без сообщения.
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
