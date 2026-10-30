#!/usr/bin/env python3
"""Вклад отдела поиска поставщиков в выигрыш последних сделок, в деньгах, — PDF владельцу в Telegram.

ВОПРОС ВЛАДЕЛЬЦА (30.10.2026, с уточнениями того же дня). «Сколько денег принёс компании
отдел сорсинга — на последних 50 сделках, перешедших в реализацию. Меня интересуют только те
сделки, где поставщиков нашли сорсеры. Премии не интересуют. Какое преимущество получила
компания от работы отдела сорсинга, сколько они принесли дополнительного бизнеса». Затем:
«из последних 50 сделок, переведённых в реализацию, какой вклад и пользу принёс отдел в то,
что мы их выиграли; в деньги пересчитай все уровни — сколько пользы от существования отдела».

ГДЕ СЧИТАЕТСЯ. Только внутри прогона sourcing-value.yml: суммы — закрытые сведения, журнал
Actions публичный (CLAUDE.md, правило 5 и распоряжение 30.09.2026). Файл ведомости остаётся
на раннере и уходит владельцу ботом оповещений (scripts/premia_telegram.py), артефактов нет.
В журнал — только счётчики и доли: ни сумм, ни названий сделок, поставщиков и людей.

ИСТОЧНИКИ.
  • Строки «Реализации» (main.py --data-json → contracts.rows) — продажа и закупка сделки в €
    без НДС тем же каскадом, что на вкладке: сверенный файл экономики первым, иначе «Сумма»
    по правилу НДС; закупка — файл экономики или заказы СП-172 по курсу ЦБ. Свои формулы денег
    сделки скрипт не заводит (правило 14: одна вставка формулы).
  • Битрикс: история стадий сделок (вход в реализацию), карточки СП-166 и заказы СП-172 сделок,
    история заказов и выбранных запросов поставщиков выборки (новый ли поставщик), состав отдела
    поиска поставщиков (config.DEPT_SOURCING_ID с дочерними, вместе с ушедшими), поле «Сорсер».
  • Закрытая база: цены разобранных КП поставщиков (lib_prices_live, поток «разбор КП»).
  • Курсы ЦБ РФ на дату цены и заказа (base.fx.load_cbr).

ОПРЕДЕЛЕНИЯ (пороги — таблицей ПОРОГИ, правило 4).
  • СДЕЛКА, ПЕРЕШЕДШАЯ В РЕАЛИЗАЦИЮ — первый вход в воронку реализации (кат. 0) ПОСЛЕ входа в
    предпродажную воронку (не техническую, people.TECH_CATS), не проигранная. Кат. 0 — и
    воронка по умолчанию: сделка, заведённая в ней и перенесённая в предпродажу, по первому
    входу выглядела бы старой (probe_conversion).
  • ПОБЕДИТЕЛЬ — поставщик непроваленного заказа СП-172 сделки; заказов нет — поставщик
    карточки СП-166 в стадии Selected.
  • ВЕДУЩИЙ ЗАПРОС — ответственный карточки СП-166; карточка робота (config.service_accounts) —
    сорсер из поля сделки, как main.py; нет и его — создатель карточки, если он не робот.
  • ПОСТАВЩИКА НАШЁЛ ОТДЕЛ — у победителя есть карточка СП-166 этой сделки, которую ведёт
    сотрудник отдела поиска поставщиков. ВЫБОРКА — N последних по входу в реализацию сделок.
  • УРОВЕНЬ ВКЛАДА (УРОВНИ, один на сделку, по старшинству): поставщика заказа нашёл отдел до
    выигрыша; КП от отдела до выигрыша (файл или стадия КП), заказ у другого; запрашивал до
    выигрыша без КП; поставщика заказа нашёл после выигрыша; подключился только после; без отдела.
    «До выигрыша» — карточка заведена раньше входа сделки в реализацию.
  • ДЕНЬГИ УРОВНЕЙ. Влияние — отдел дал цену до выигрыша или нашёл поставщика заказа; контроль —
    на поставщика и цену сделки отдел не повлиял. Польза уровня влияния = (маржа уровня − маржа
    контроля) × продажа уровня; верхняя граница — вся продажа − закупка уровней влияния. Маржа —
    продажа − закупка к продаже по сделкам с обеими суммами. Контроля меньше КОНТРОЛЬ_МИН — оценки
    нет. В месяц — делением на длину окна входа выборки; рубли — по курсу сборки «Реализации».
  • НОВЫЙ ПОСТАВЩИК — до первой карточки запроса ему по этой сделке (нет карточки — до входа
    сделки в реализацию) у него не было ни одного непроваленного заказа СП-172 и ни одного
    выбранного запроса по другим сделкам. Глубина — с первого заказа СП-172 в портале (печатается).
  • ЗАКУПКА У ПОСТАВЩИКА — закупка сделки × его доля в суммах непроваленных заказов (курс ЦБ на
    дату заказа); сумм нет — поровну между поставщиками заказов.
  • ЭКОНОМИЯ ПО ЦЕНАМ КП — по позициям одного кода, где у победителя и конкурента есть цена:
    Э1 — выбор против следующего по цене КП, Э2 — торг (первая цена победителя против итоговой),
    ЭС — большее из двух по позиции (не сумма); Э3 — отторговано с помощью новых: лучшая первая
    цена прежних поставщиков против заплаченной, когда выбран новый или прежний снизил цену после
    КП нового. Цена — за единицу в € без НДС, сравнимы — один базис (или не указан) и разница не
    больше ПОРОГИ["раз"] раз; деньги позиции — сумма строки КП победителя, лот не умножается.

    python scripts/sourcing_value.py --data modules.json --out DIR [--n 50] [--parts 10]

В DIR: doc.html (исходник PDF) и ids.txt (номера реализаций — их проверяет pdf_check.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.append(str(ROOT / "library"))   # price_norm и его соседи — последними: не затеняют корень

MSK = dt.timezone(dt.timedelta(hours=3))
N_DEALS = 50
# Пороги — таблицей (правило 4): изменение порога — правка одной строки с причиной.
ПОРОГИ = {
    "кандидаты_дней": 400,      # строки «Реализации» с входом или выигрышем не старше — кандидаты
    "раз": 5.0,                 # цены одной позиции у разных поставщиков дальше — не та позиция/единица
    "сумма_строки": 0.02,       # цена × количество сходится с суммой строки КП в пределах доли
    "код_мин": 4,               # код позиции короче — не код (номер строки, «шт»)
    "покрытие_цель": 0.70,      # доля закупки под сравнением цен КП, к которой вести (предложение)
    "кп_мин": 3,                # КП файлом на сделку — минимум конкурентной закупки (предложение)
}
FEED = "разбор КП"
SELECTED = "selected"
ROBOT = "~"                     # ведущий запроса не определён: карточка робота без сорсера в сделке


# ───────────────────────────── время и разбор ─────────────────────────────

def ts(v) -> dt.datetime | None:
    """Метка портала или ISO-дата → время с часовым поясом (без пояса — Москва)."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"(\d\d)\.(\d\d)\.(\d{4})$", s)
        if not m:
            return None
        t = dt.datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return t if t.tzinfo else t.replace(tzinfo=MSK)


def day(v) -> dt.date | None:
    t = ts(v)
    return t.astimezone(MSK).date() if t else None


def funnel(stage_id) -> str:
    """'C2:NEW' → '2', 'NEW' → '0' (как probe_conversion.воронка_стадии)."""
    s = str(stage_id or "")
    if s.startswith("C") and ":" in s:
        head = s[1:s.index(":")]
        return head if head.isdigit() else "0"
    return "0"


def realization_entry(hist: list[tuple[str, str]], tech: set[str]) -> tuple[dt.datetime | None, str]:
    """(время входа в реализацию, вид): «moved» — первый вход в кат. 0 после предпродажной
    воронки; «direct» — заведена в кат. 0 и в предпродаже не была; «none» — в реализацию после
    предпродажи не входила."""
    ev = sorted((t, s) for s, raw in hist if (t := ts(raw)))
    first_pre = next((t for t, s in ev if funnel(s) != "0" and funnel(s) not in tech), None)
    if first_pre is None:
        return (next((t for t, s in ev if funnel(s) == "0"), None), "direct")
    entry = next((t for t, s in ev if funnel(s) == "0" and t > first_pre), None)
    return (entry, "moved") if entry else (None, "none")


def candidates(rows: list[dict], today: dt.date, days: int) -> list[dict]:
    """Строки «Реализации» с входом в кат. 0 или выигрышем не старше days дней (вместе с
    проигранными — их считаем отдельно)."""
    out = []
    for r in rows:
        ds = [d for d in (day(r.get("cat0At")), day(r.get("wonAt"))) if d]
        if ds and (today - max(ds)).days <= days:
            out.append(r)
    return out


def moved_live(rows: list[dict], entries: dict[str, tuple[dt.datetime | None, str]]) -> list[dict]:
    """Непроигранные строки, вошедшие в реализацию после предпродажи, — новые первыми."""
    out = []
    for r in rows:
        t, k = entries.get(r["deal"], (None, ""))
        if k == "moved" and t and not r.get("lost"):
            x = dict(r)
            x["_entry"] = t
            out.append(x)
    out.sort(key=lambda x: (x["_entry"], r_seq(x)), reverse=True)
    return out


def deal_winners(cards: list[dict], orders: list[dict], osem: dict[str, str]) -> set[str]:
    """Поставщики непроваленных заказов сделки; заказов нет — поставщики Selected-карточек."""
    w = {str(o.get("companyId")) for o in orders
         if osem.get(str(o.get("stageId") or "")) != "F" and str(o.get("companyId") or "0") != "0"}
    return w or {str(c.get("companyId")) for c in cards
                 if c.get("_bucket") == SELECTED and str(c.get("companyId") or "0") != "0"}


def found_by_dept(cards: list[dict], winners: set[str], who: dict[str, str], dept: set[str]) -> dict[str, set[str]]:
    """Победитель → сотрудники отдела, чей запрос СП-166 ему по этой сделке был. Пусто — поставщиков
    сделки отдел не находил."""
    out: dict[str, set[str]] = defaultdict(set)
    for c in cards:
        s, u = str(c.get("companyId") or ""), who.get(str(c.get("id")), "")
        if s in winners and u in dept:
            out[s].add(u)
    return dict(out)


def money(r: dict) -> tuple[float, float]:
    """(продажа, закупка) строки «Реализации» в €; без курса или подозрительная закупка — 0."""
    buy = _f(r.get("buyEur")) if r.get("buySrc") not in ("none", "fx") and not r.get("buySusp") else 0.0
    sale = _f(r.get("saleEur")) if r.get("saleKnown", True) else 0.0
    return sale, buy


def r_seq(r: dict) -> int:
    try:
        return int(r.get("seq") or 0)
    except (TypeError, ValueError):
        return 0


def code_key(pn) -> str:
    """Код позиции: буквы и цифры в верхнем регистре; короче ПОРОГИ["код_мин"] — не код."""
    k = re.sub(r"[^0-9A-ZА-ЯЁ]", "", str(pn or "").upper())
    return k if len(k) >= ПОРОГИ["код_мин"] else ""


# ───────────────────────────── новые поставщики ─────────────────────────────

def first_contact(cards: list[dict], deal_entry: dt.datetime) -> dict[str, dt.datetime]:
    """Поставщик → время первой карточки запроса ему по сделке (нет карточки — вход сделки)."""
    out: dict[str, dt.datetime] = {}
    for c in cards:
        s, t = str(c.get("companyId") or ""), ts(c.get("createdTime"))
        if s in ("", "0") or not t:
            continue
        if s not in out or t < out[s]:
            out[s] = t
    return out


def is_new(sup: str, deal: str, since: dt.datetime, orders_hist: dict[str, list[dict]],
           sel_hist: dict[str, list[dict]], osem: dict[str, str]) -> bool:
    """Нет ни заказа (непроваленного), ни выбранного запроса по ДРУГИМ сделкам раньше since."""
    for o in orders_hist.get(sup, []):
        if str(o.get("parentId2") or "") == deal or osem.get(str(o.get("stageId") or "")) == "F":
            continue
        t = ts(o.get("createdTime"))
        if t and t < since:
            return False
    for c in sel_hist.get(sup, []):
        if str(c.get("parentId2") or "") == deal:
            continue
        t = ts(c.get("createdTime"))
        if t and t < since:
            return False
    return True


# ───────────────────────────── цены и позиции ─────────────────────────────

def offer_rows(prices: list[dict], cards: dict[str, dict], fx, today: dt.date) -> tuple[list[dict], Counter]:
    """Строки КП → предложения {deal, sup, key, eur, base, day, basis, id} в € без НДС.
    Счётчик — почему строка не взята (только числа в журнал)."""
    import price_norm
    why: Counter = Counter()
    out = []
    for p in prices:
        card = cards.get(str(p.get("rfq_id") or ""))
        if not card:
            why["карточка не из выборки"] += 1
            continue
        sup = str(card.get("companyId") or "") or str(p.get("rfq_company") or "")
        if sup in ("", "0"):
            why["поставщик не указан"] += 1
            continue
        key = code_key(p.get("part_number"))
        if not key:
            why["нет кода"] += 1
            continue
        price = _f(p.get("price"))
        cur = str(p.get("currency") or "").strip().upper()
        if not price or price <= 0:
            why["нет цены"] += 1
            continue
        if not cur:
            why["нет валюты"] += 1
            continue
        d = day(p.get("price_date")) or day(card.get("createdTime")) or today
        if d > today:
            d = today
        vat = price_norm.ндс_в_цене(p.get("note"))
        if vat:
            if cur != "RUB":
                why["с НДС не в рублях"] += 1
                continue
            rate = price_norm.ставка_ндс("RU", d)
            price = price / (1 + rate)
        k = fx.to_eur(1.0, cur, d) if fx else (1.0 if cur == "EUR" else None)
        if not k:
            why["нет курса"] += 1
            continue
        eur = price * k
        qty = _f(p.get("qty"))
        total = _f(p.get("total"))
        if total and vat and cur == "RUB":
            total = total / (1 + price_norm.ставка_ндс("RU", d))
        if total and qty and abs(_f(p.get("price")) * qty - _f(p.get("total"))) <= ПОРОГИ["сумма_строки"] * _f(p.get("total")):
            base, base_src = total * k, "сумма строки"
        elif qty and qty > 0:
            base, base_src = eur * qty, "цена × количество"
        else:
            base, base_src = eur, "количество не указано"
        code, _std = price_norm.базис(p.get("basis"))
        out.append({"id": int(p.get("id") or 0), "deal": str(card.get("parentId2") or ""), "sup": sup,
                    "card": str(p.get("rfq_id")), "key": key, "eur": eur, "base": base, "baseSrc": base_src,
                    "day": d, "basis": code})
    return out, why


def _f(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


def comparable(a: dict, b: dict) -> bool:
    if a["basis"] and b["basis"] and a["basis"] != b["basis"]:
        return False
    hi, lo = max(a["eur"], b["eur"]), min(a["eur"], b["eur"])
    return lo > 0 and hi / lo <= ПОРОГИ["раз"]


def deal_positions(offers: list[dict], winners: set[str], new: set[str]) -> dict:
    """Позиции одной сделки → Э1, Э2, ЭС, Э3, «выбрано дороже», покрытие. Деньги — €."""
    by: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for o in offers:
        by[o["key"]][o["sup"]].append(o)
    res = {"pos": 0, "posWin": 0, "posComp": 0, "posSolo": 0, "baseWin": 0.0, "baseComp": 0.0,
           "baseSolo": 0.0, "e1": 0.0, "e2": 0.0, "es": 0.0, "e3": 0.0, "e3n": 0, "e3NewWin": 0.0,
           "e3OldDrop": 0.0, "over": 0.0, "overN": 0, "missNew": 0.0, "basisSkip": 0, "ratioSkip": 0,
           "kpPerPos": [], "overList": [], "e3List": [],
           # карточка СП-166 победителя → её деньги: кто вёл запрос, тому и зачёт
           "byCard": defaultdict(lambda: defaultdict(float))}
    for key, sups in by.items():
        res["pos"] += 1
        q = {}
        for s, lst in sups.items():
            lst.sort(key=lambda o: (o["day"], o["id"]))
            q[s] = {"first": lst[0], "final": lst[-1]}
        res["kpPerPos"].append(len(q))
        ws = [s for s in q if s in winners]
        if not ws:
            continue
        w = min(ws, key=lambda s: q[s]["final"]["eur"])
        fw, lw = q[w]["first"], q[w]["final"]
        pw, mw = lw["eur"], lw["base"]
        res["posWin"] += 1
        res["baseWin"] += mw
        cr = res["byCard"][lw["card"]]
        cr["base"] += mw
        alts = {}
        for s, v in q.items():
            if s == w:
                continue
            if not comparable(lw, v["final"]):
                if lw["basis"] and v["final"]["basis"] and lw["basis"] != v["final"]["basis"]:
                    res["basisSkip"] += 1
                else:
                    res["ratioSkip"] += 1
                continue
            alts[s] = v
        e2 = (fw["eur"] - pw) / pw * mw if comparable(fw, lw) and fw["eur"] > pw else 0.0
        res["e2"] += e2
        if not alts:
            res["posSolo"] += 1
            res["baseSolo"] += mw
            res["es"] += e2
            cr["es"] += e2
            continue
        res["posComp"] += 1
        res["baseComp"] += mw
        amin = min(v["final"]["eur"] for v in alts.values())
        d1 = (amin - pw) / pw * mw
        e1 = d1 if d1 > 0 else 0.0
        if d1 < 0:
            res["over"] += -d1
            cr["over"] += -d1
            res["overN"] += 1
            res["overList"].append({"deal": lw["deal"], "key": key, "sup": w, "over": -d1,
                                    "pct": (pw - amin) / amin, "n": len(alts) + 1})
        res["e1"] += e1
        res["es"] += max(e1, e2)
        cr["es"] += max(e1, e2)
        # «выбрано дороже» нового поставщика: КП нового дешевле заплаченного
        newalt = [v["final"]["eur"] for s, v in alts.items() if s in new]
        if newalt and min(newalt) < pw:
            res["missNew"] += (pw - min(newalt)) / pw * mw
        # Э3: прежние против заплаченного, при участии нового
        olds = {s: v for s, v in alts.items() if s not in new}
        news = {s: v for s, v in alts.items() if s in new}
        if w in new:
            refs = [v["first"]["eur"] for v in olds.values() if comparable(lw, v["first"])]
            if refs:
                ref = min(refs)
                d3 = (ref - pw) / pw * mw
                if d3 > 0:
                    res["e3"] += d3
                    cr["e3"] += d3
                    res["e3NewWin"] += d3
                    res["e3n"] += 1
                    res["e3List"].append({"deal": lw["deal"], "key": key, "sup": w, "e3": d3,
                                          "pct": (ref - pw) / ref, "how": "выбран новый"})
        elif news:
            first_new = min(v["first"]["day"] for v in news.values())
            if fw["eur"] > pw and lw["day"] >= first_new:
                ref = min([fw["eur"]] + [v["first"]["eur"] for v in olds.values() if comparable(lw, v["first"])])
                d3 = (ref - pw) / pw * mw
                if d3 > 0:
                    res["e3"] += d3
                    cr["e3"] += d3
                    res["e3OldDrop"] += d3
                    res["e3n"] += 1
                    res["e3List"].append({"deal": lw["deal"], "key": key, "sup": w, "e3": d3,
                                          "pct": (ref - pw) / ref, "how": "прежний снизил после КП нового"})
    return res


# ───────────────────────────── сборка отчёта ─────────────────────────────

def order_shares(orders: list[dict], osem: dict[str, str], fx) -> dict[str, dict[str, float]]:
    """Сделка → поставщик → доля закупки по суммам непроваленных заказов (курс ЦБ на дату
    заказа). Сумм нет — доли поровну между поставщиками заказов."""
    eur: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cnt: dict[str, Counter] = defaultdict(Counter)
    for o in orders:
        if osem.get(str(o.get("stageId") or "")) == "F":
            continue
        d, s = str(o.get("parentId2") or ""), str(o.get("companyId") or "")
        if not d or s in ("", "0"):
            continue
        cnt[d][s] += 1
        amt, cur, od = _f(o.get("opportunity")), str(o.get("currencyId") or ""), day(o.get("createdTime"))
        k = fx.to_eur(1.0, cur, od) if (fx and amt and cur and od) else None
        if k:
            eur[d][s] += amt * k
    out: dict[str, dict[str, float]] = {}
    for d, c in cnt.items():
        tot = sum(eur[d].values())
        if tot > 0:
            out[d] = {s: eur[d].get(s, 0.0) / tot for s in c}
        else:
            out[d] = {s: 1 / len(c) for s in c}
    return out


def card_person(card: dict, deal_sourcer: str, service: set[str]) -> str:
    """Кто вёл запрос: ответственный карточки; карточка робота (служебная запись) — сорсер
    сделки (поле «Сорсер», как main.py); нет и его — создатель карточки, если он не робот."""
    for uid in (str(card.get("assignedById") or ""), deal_sourcer, str(card.get("createdBy") or "")):
        if uid and uid != "0" and uid not in service:
            return uid
    return ""


#: Вклад отдела в выигрыш сделки — один уровень на сделку, по старшинству (код, подпись).
УРОВНИ = (
    ("found_pre", "поставщика заказа нашёл отдел до выигрыша"),
    ("kp_pre", "КП от отдела до выигрыша, заказ у другого поставщика или заказа ещё нет"),
    ("ask_pre", "отдел запрашивал до выигрыша, КП не получено"),
    ("found_post", "поставщика заказа нашёл отдел после выигрыша"),
    ("post", "отдел подключился только после выигрыша"),
    ("none", "без отдела"),
)
ВКЛАД_В_ВЫИГРЫШ = ("found_pre", "kp_pre")   # КП от отдела было до выигрыша
# Пересчёт уровней в деньги (просьба владельца 30.10.2026: «сколько пользы от существования отдела»).
# ВЛИЯНИЕ — отдел дал цену до выигрыша или нашёл поставщика заказа; КОНТРОЛЬ — на поставщика и цену
# сделки отдел не повлиял. Польза уровня влияния = (маржа уровня − маржа контроля) × продажа уровня;
# верхняя граница — вся продажа − закупка уровней влияния (если без отдела их не выиграли бы).
ВЛИЯНИЕ = ("found_pre", "kp_pre", "found_post")
КОНТРОЛЬ = ("ask_pre", "post", "none")
КОНТРОЛЬ_МИН = 3                # сделок контроля с обеими суммами — меньше: оценку не считаем


def has_kp(card: dict, kp_buckets: set[str]) -> bool:
    """КП получено: файл в «Offer from supplier» или стадия КП (Price at Work, Selected, Not Selected)."""
    return bool(card.get("_offer")) or card.get("_bucket") in kp_buckets


def level(dept_cards: list[dict], found: dict[str, set[str]], entry: dt.datetime, kp_buckets: set[str]) -> str:
    """Уровень вклада отдела в выигрыш сделки (УРОВНИ). dept_cards — карточки сделки, которые вёл
    отдел; found — победитель → нашедшие его сотрудники отдела; entry — вход в реализацию."""
    pre = [c for c in dept_cards if (t := ts(c.get("createdTime"))) and t < entry]
    pre_found = {str(c.get("companyId")) for c in pre} & set(found)
    if pre_found:
        return "found_pre"
    if any(has_kp(c, kp_buckets) for c in pre):
        return "kp_pre"
    if pre:
        return "ask_pre"
    if found:
        return "found_post"
    return "post" if dept_cards else "none"


def build(sample: list[dict], cards_by_deal: dict[str, list[dict]], orders: list[dict], osem: dict[str, str],
          who: dict[str, str], dept: set[str], new_by_deal: dict[str, set[str]], offers: list[dict], fx,
          kp_buckets: set[str]) -> dict:
    """Сделки выборки, вклад отдела, люди отдела и новые поставщики. Все суммы — € без НДС."""
    shares = order_shares(orders, osem, fx)
    ord_by_deal: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        ord_by_deal[str(o.get("parentId2") or "")].append(o)
    off_by_deal: dict[str, list[dict]] = defaultdict(list)
    for o in offers:
        off_by_deal[o["deal"]].append(o)
    people: dict[str, dict] = defaultdict(lambda: {"kpDeals": set(), "foundDeals": set(), "cards": 0, "kpFiles": 0,
                                                   "newFound": 0, "sale": 0.0, "margin": 0.0})
    rows, news, e3_all = [], [], []
    for r in sample:
        did = r["deal"]
        cards = cards_by_deal.get(did, [])
        winners = deal_winners(cards, ord_by_deal.get(did, []), osem)
        found = found_by_dept(cards, winners, who, dept)
        new = new_by_deal.get(did, set())
        sh = shares.get(did) or ({s: 1 / len(winners) for s in winners} if winners else {})
        sale, buy = money(r)
        margin = (sale - buy) if (sale and buy) else None
        pos = deal_positions(off_by_deal.get(did, []), winners, new)
        pos.pop("byCard")
        pos.pop("overList")
        e3_all += pos.pop("e3List")
        dept_cards = [c for c in cards if who.get(str(c.get("id"))) in dept]
        lvl = level(dept_cards, found, r["_entry"], kp_buckets)
        contrib: set[str] = set()              # сотрудники с вкладом в выигрыш этой сделки
        for c in dept_cards:
            u = who[str(c.get("id"))]
            x = people[u]
            x["cards"] += 1
            x["kpFiles"] += 1 if c.get("_offer") else 0
            t = ts(c.get("createdTime"))
            if t and t < r["_entry"] and has_kp(c, kp_buckets):
                x["kpDeals"].add(did)
                contrib.add(u)
        for s, us in found.items():
            for u in us:
                people[u]["foundDeals"].add(did)
                contrib.add(u)
                if s in new:
                    people[u]["newFound"] += 1
            if s in new:
                news.append({"deal": did, "sup": s, "finders": sorted(us), "buy": buy * sh.get(s, 0.0)})
        for u in contrib:
            people[u]["sale"] += sale
            people[u]["margin"] += margin or 0.0
        src_new = set(found) & new
        rows.append({
            "deal": did, "seq": r_seq(r), "title": r.get("title") or f"Сделка #{did}",
            "entry": r["_entry"].astimezone(MSK).date(), "sale": sale, "buy": buy, "margin": margin,
            "saleSrc": r.get("saleSrc") or "", "level": lvl, "cards": len(cards), "cardsDept": len(dept_cards),
            "kpFiles": sum(1 for c in cards if c.get("_offer")),
            "winners": len(winners), "srcWinners": len(found), "srcNew": len(src_new),
            "buySrc": buy * sum(sh.get(s, 0.0) for s in found), "buySrcNew": buy * sum(sh.get(s, 0.0) for s in src_new),
            "finders": sorted({u for us in found.values() for u in us}),
            **pos,
        })
    return {"rows": rows, "people": dict(people), "news": sorted(news, key=lambda x: -x["buy"]),
            "e3": sorted(e3_all, key=lambda x: -x["e3"])}


def totals(rows: list[dict]) -> dict:
    t: dict = defaultdict(float)
    lv: dict[str, dict] = {k: {"n": 0, "sale": 0.0, "margin": 0.0, "nMargin": 0, "saleM": 0.0} for k, _ in УРОВНИ}
    for r in rows:
        for k in ("sale", "buy", "buySrc", "buySrcNew", "es", "e3", "baseComp", "baseWin"):
            t[k] += r[k] or 0.0
        for k in ("cards", "cardsDept", "kpFiles", "winners", "srcWinners", "srcNew", "posComp", "posWin", "e3n"):
            t[k] += r[k] or 0
        x = lv[r["level"]]
        x["n"] += 1
        x["sale"] += r["sale"]
        if r["margin"] is not None:
            t["margin"] += r["margin"]
            t["nMargin"] += 1
            t["saleM"] += r["sale"]
            x["margin"] += r["margin"]
            x["nMargin"] += 1
            x["saleM"] += r["sale"]
        if r["saleSrc"] == "file":
            t["nFile"] += 1
        if r["srcNew"]:
            t["nNew"] += 1
            t["saleNew"] += r["sale"]
            t["marginNew"] += r["margin"] or 0.0
        if r["kpFiles"] >= 2:
            t["nKp2"] += 1
    t["n"] = len(rows)
    t["levels"] = lv
    w = [lv[k] for k in ВКЛАД_В_ВЫИГРЫШ]
    t["winN"] = sum(x["n"] for x in w)
    t["winSale"] = sum(x["sale"] for x in w)
    t["winMargin"] = sum(x["margin"] for x in w)
    for x in lv.values():
        x["rate"] = x["margin"] / x["saleM"] if x["saleM"] else None
    # деньги уровней: польза против контроля и верхняя граница
    c = [lv[k] for k in КОНТРОЛЬ]
    t["ctrlN"] = sum(x["nMargin"] for x in c)
    c_sale = sum(x["saleM"] for x in c)
    t["ctrlRate"] = (sum(x["margin"] for x in c) / c_sale) if (c_sale and t["ctrlN"] >= КОНТРОЛЬ_МИН) else None
    for k in ВЛИЯНИЕ:
        x = lv[k]
        x["upper"] = x["margin"]
        x["benefit"] = ((x["rate"] - t["ctrlRate"]) * x["saleM"]
                        if (x["rate"] is not None and t["ctrlRate"] is not None) else None)
    for k in КОНТРОЛЬ:
        lv[k]["upper"] = 0.0
        lv[k]["benefit"] = None
    t["benefit"] = (sum(lv[k]["benefit"] for k in ВЛИЯНИЕ if lv[k]["benefit"] is not None)
                    if t["ctrlRate"] is not None else None)
    t["upper"] = sum(lv[k]["upper"] for k in ВЛИЯНИЕ)
    t["inflN"] = sum(lv[k]["n"] for k in ВЛИЯНИЕ)
    t["inflSale"] = sum(lv[k]["sale"] for k in ВЛИЯНИЕ)
    t["inflSaleM"] = sum(lv[k]["saleM"] for k in ВЛИЯНИЕ)
    t["inflRate"] = (t["upper"] / t["inflSaleM"]) if t["inflSaleM"] else None
    return dict(t)


def dept_rows(ppl: dict[str, dict], members: dict[str, dict]) -> list[dict]:
    """Строка на сотрудника отдела: действующие — все, даже без запросов в выборке; ушедшие —
    только если у них есть запросы в выборке. members — uid → {name, active}."""
    out = []
    for uid, m in members.items():
        a = ppl.get(uid)
        if not a and not m.get("active"):
            continue
        a = a or {}
        out.append({"uid": uid, "name": m["name"] + ("" if m.get("active") else " (уволен)"),
                    "kpDeals": len(a.get("kpDeals") or ()), "foundDeals": len(a.get("foundDeals") or ()),
                    "newFound": a.get("newFound", 0), "cards": a.get("cards", 0), "kpFiles": a.get("kpFiles", 0),
                    "sale": a.get("sale", 0.0), "margin": a.get("margin", 0.0)})
    return sorted(out, key=lambda x: (-x["margin"], -x["kpDeals"], -x["cards"], x["name"]))


# ───────────────────────────── выводы и предложения ─────────────────────────────

def _share(a: float, b: float) -> str:
    return _pct(a / b) if b else "—"


def proposals(t: dict, dr: list[dict]) -> list[str]:
    """Прикладные предложения — из чисел итога, без общих слов. Оценки подстановкой помечены."""
    out = []
    lv = t["levels"]
    if t.get("benefit") is not None and t.get("months") and t["benefit"] <= 0:
        out.append(
            f"Прибавки к марже от отдела на этих сделках нет: маржа сделок с его влиянием "
            f"{_pct(t['inflRate'] or 0)} против {_pct(t['ctrlRate'])} у сделок без влияния. Польза отдела — в "
            f"самих выигрышах: до {_eur(t['upper'] / t['months'])} продажи − закупки в месяц (верхняя граница). "
            f"Чтобы получить точное число, замерить конверсию пресейла: доля выигранных сделок с КП отдела до "
            f"ТКП и без него; польза = прирост выигрышей × средняя продажа − закупка.")
    elif t.get("benefit") is not None and t.get("months"):
        per_m = t["benefit"] / t["months"]
        out.append(
            f"Окупаемость отдела: польза по оценке — {_eur(per_m)} в месяц"
            + (f" ({_rub(per_m * t['eurRub'])})" if t.get("eurRub") else "")
            + ". Если месячный фонд оплаты отдела с налогами меньше этой суммы — отдел окупается уже на "
              "сделках реализации; верхняя граница в месяц — "
            + _eur(t["upper"] / t["months"]) + ".")
    late = lv["found_post"]["n"] + lv["post"]["n"]
    if late:
        out.append(
            f"В {late} сделках из {t['n']} отдел подключился только после выигрыша: продажа "
            f"{_eur(lv['found_post']['sale'] + lv['post']['sale'])}. ТКП по ним выпускали на ценах, полученных "
            f"без отдела. Правило: запрос СП-166 от отдела — до выпуска ТКП по каждой сделке пресейла.")
    if lv["none"]["n"]:
        out.append(
            f"Без отдела выиграно {lv['none']['n']} сделок: продажа {_eur(lv['none']['sale'])}, продажа − закупка "
            f"{_eur(lv['none']['margin'])}. По крупнейшим — повторный запрос отдела по тем же кодам до заказа "
            f"поставщику, чтобы проверить цену действующего поставщика.")
    if lv["ask_pre"]["n"]:
        out.append(
            f"В {lv['ask_pre']['n']} сделках отдел запрашивал до выигрыша, но КП не получено (нет файла в «Offer "
            f"from supplier» и стадии КП): либо КП не положили файлом, либо поставщики не ответили. Разобрать по "
            f"карточкам и класть каждое КП файлом в день получения (правило 19.10.2026).")
    if t.get("srcNew"):
        out.append(
            f"Новые поставщики от отдела — {int(t['srcNew'])} в {int(t.get('nNew') or 0)} сделках: продажа этих "
            f"сделок {_eur(t.get('saleNew') or 0)}, продажа − закупка {_eur(t.get('marginNew') or 0)}. В каждой "
            f"сделке запрашивать хотя бы одного нового поставщика рядом с прежними; нового проверять до заказа "
            f"(документы, образец, срок).")
    n_low = t["n"] - int(t.get("nKp2") or 0)
    if n_low:
        out.append(
            f"Сделок, где КП файлом меньше двух, — {n_low} из {t['n']}: цене поставщика не с чем сравниться. "
            f"Минимум {ПОРОГИ['кп_мин']} КП файлом до выбора поставщика.")
    comp = (t.get("baseComp") or 0.0) / t["buy"] if t.get("buy") else 0.0
    if comp < ПОРОГИ["покрытие_цель"]:
        out.append(
            f"Экономию отдела в евро по ценам КП сейчас доказать нечем: под сравнением цен двух поставщиков на "
            f"один код — {_pct(comp)} закупки ({int(t.get('posComp') or 0)} позиций): цены из КП этих сделок "
            f"почти не разобраны. Включить разбор цен КП (поток «разбор КП») по сделкам пресейла и реализации, "
            f"цель — {_pct(ПОРОГИ['покрытие_цель'])} закупки под сравнением.")
    idle = [x for x in dr if not x["cards"]]
    if idle:
        out.append(
            f"Действующих сотрудников отдела без единого запроса в этих {t['n']} сделках — {len(idle)} из "
            f"{len(dr)}: проверить, чем они заняты (пресейл без выигрыша, торги), или перераспределить запросы.")
    return out


# ───────────────────────────── вёрстка ─────────────────────────────

def _eur(v: float) -> str:
    s = f"{abs(round(v)):,}".replace(",", " ")
    return ("−" if round(v) < 0 else "") + s + " €"


def _rub(v: float) -> str:
    s = f"{abs(round(v)):,}".replace(",", "\u00a0")
    return ("−" if round(v) < 0 else "") + s + "\u00a0₽"


def _num1(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def _num(v: float) -> str:
    s = f"{abs(round(v)):,}".replace(",", " ")
    return ("−" if round(v) < 0 else "") + s


def _pct(v: float) -> str:
    return f"{v * 100:.1f}".replace(".", ",") + " %"


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


CSS = """
@page { size: A4 landscape; margin: 12mm 12mm 12mm 14mm; }
body { font-family: "Times New Roman", "Liberation Serif", "FreeSerif", serif; font-size: 10.5pt;
       line-height: 1.28; color: #000; background: #fff; margin: 0; }
h1 { font-size: 13pt; font-weight: normal; text-align: center; text-transform: uppercase; margin: 0 0 6pt; }
h2 { font-size: 11pt; font-weight: normal; margin: 12pt 0 5pt; }
p { text-align: justify; margin: 0 0 6pt; }
ol { margin: 0 0 6pt 16pt; padding: 0; } li { text-align: justify; margin: 0 0 3pt; }
.sub { text-align: center; margin-bottom: 10pt; }
table.t + p { margin-top: 6pt; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 9pt; }
.t thead { display: table-header-group; }
.t tr { page-break-inside: avoid; }
.t th, .t td { border: 0.5pt solid #000; padding: 2pt 3pt; vertical-align: top;
               word-wrap: break-word; overflow-wrap: anywhere; }
.t th { font-weight: normal; text-align: center; }
.r { text-align: right; white-space: nowrap; }
.c { text-align: center; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; }
"""


def render(rep: dict, t: dict, meta: dict, names: dict[str, str], pnames: dict[str, str]) -> str:
    rows = rep["rows"]
    lo = min((r["entry"] for r in rows), default=None)
    hi = max((r["entry"] for r in rows), default=None)
    span = f"{lo:%d.%m.%Y} — {hi:%d.%m.%Y}" if lo else "—"
    sup = lambda s: names.get(s) or f"компания #{s}"                         # noqa: E731
    who = lambda u: pnames.get(u) or f"сотрудник #{u}"                       # noqa: E731
    lvl_name = dict(УРОВНИ)
    seq_of = {r["deal"]: r for r in rows}
    dr = meta.get("dept") or []
    lv = t["levels"]
    p: list[str] = []
    a = p.append
    a(f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Вклад сорсинга в выигрыш сделок</title>"
      f"<style>{CSS}</style></head><body>")
    a(f"<h1>Вклад отдела поиска поставщиков в {t['n']} последних выигранных сделок</h1>")
    a(f"<div class='sub'>сделки, перешедшие в реализацию {span}; отдел — руководитель "
      f"{_e(meta.get('head') or 'не указан в портале')}, сотрудников {meta.get('deptN', 0)}; сборка "
      f"{meta['today']:%d.%m.%Y}; суммы — евро без НДС, по курсу ЦБ РФ</div>")

    a("<h2>1. Итог: польза отдела в деньгах</h2>")
    er = t.get("eurRub")
    rub = lambda v: f" ({_rub(v * er)})" if er else ""                     # noqa: E731
    ben, up, mo = t.get("benefit"), t.get("upper") or 0.0, t.get("months") or 0.0
    if ben is not None and ben <= 0:
        a(f"<p>Прибавки к марже от отдела на {t['n']} сделках, вошедших в реализацию за {span} (≈ {_num1(mo)} "
          f"мес.), нет: оценка {_eur(ben)}. Маржа {t['inflN']} сделок, где отдел дал цену поставщика до выигрыша "
          f"или нашёл поставщика заказа, — {_pct(t['inflRate'] or 0)} продажи, у сделок без влияния отдела — "
          f"{_pct(t['ctrlRate'])} ({t['ctrlN']} сделок). Польза отдела — в самих выигрышах: верхняя граница "
          f"{_eur(up)}{rub(up)}, в месяц {_eur(up / mo if mo else 0)}{rub(up / mo if mo else 0)} — вся продажа − "
          f"закупка этих {t['inflN']} сделок, если без отдела их не выиграли бы. Доказано ценами КП — "
          f"{_eur(t.get('es') or 0)}: цены из КП этих сделок почти не разобраны (раздел 3).</p>")
    elif ben is not None:
        a(f"<p>Польза отдела на {t['n']} сделках, вошедших в реализацию за {span} (≈ {_num1(mo)} мес.), — "
          f"{_eur(ben)}{rub(ben)} по оценке, в месяц {_eur(ben / mo if mo else 0)}{rub(ben / mo if mo else 0)}. "
          f"Это на столько больше продажа − закупка {t['inflN']} сделок, где отдел дал цену поставщика до "
          f"выигрыша или нашёл поставщика заказа (маржа {_pct(t['inflRate'] or 0)} продажи), чем если бы у них "
          f"была маржа сделок, на которые отдел не повлиял ({_pct(t['ctrlRate'])}, {t['ctrlN']} сделок). "
          f"Верхняя граница — {_eur(up)}{rub(up)}: вся продажа − закупка этих {t['inflN']} сделок, если без "
          f"отдела их не выиграли бы. Доказано ценами КП — {_eur(t.get('es') or 0)}: цены из КП этих сделок "
          f"почти не разобраны (раздел 3).</p>")
    else:
        a(f"<p>Оценку пользы против сделок без влияния отдела посчитать нельзя: таких сделок с обеими суммами "
          f"{t.get('ctrlN', 0)}, меньше {КОНТРОЛЬ_МИН}. Верхняя граница — {_eur(up)}{rub(up)}: вся продажа − "
          f"закупка {t['inflN']} сделок, где отдел дал цену поставщика до выигрыша или нашёл поставщика заказа. "
          f"Доказано ценами КП — {_eur(t.get('es') or 0)}.</p>")
    a("<table class='t'><thead><tr><th style='width:30%'>вклад отдела</th><th style='width:6%'>сделок</th>"
      "<th style='width:12%'>продажа, €</th><th style='width:12%'>продажа − закупка, €</th>"
      "<th style='width:8%'>маржа</th><th style='width:16%'>польза против сделок без влияния отдела, €</th>"
      "<th style='width:16%'>верхняя граница, €</th></tr></thead><tbody>")
    for k, label in УРОВНИ:
        x = lv[k]
        ctrl = k in КОНТРОЛЬ
        a(f"<tr><td>{_e(label)}</td><td class='r'>{x['n']}</td><td class='r'>{_num(x['sale'])}</td>"
          f"<td class='r'>{_num(x['margin'])}</td><td class='r'>{_pct(x['rate']) if x['rate'] is not None else '—'}</td>"
          f"<td class='r'>{'контроль' if ctrl else (_num(x['benefit']) if x['benefit'] is not None else '—')}</td>"
          f"<td class='r'>{_num(x['upper'])}</td></tr>")
    a(f"<tr><td>Итого</td><td class='r'>{t['n']}</td><td class='r'>{_num(t.get('sale') or 0)}</td>"
      f"<td class='r'>{_num(t.get('margin') or 0)}</td>"
      f"<td class='r'>{_pct((t.get('margin') or 0) / t['saleM']) if t.get('saleM') else '—'}</td>"
      f"<td class='r'>{_num(ben) if ben is not None else '—'}</td><td class='r'>{_num(up)}</td></tr>")
    a("</tbody></table>")
    a(f"<p>Маржа — продажа − закупка к продаже, по сделкам с обеими суммами ({int(t.get('nMargin') or 0)} из "
      f"{t['n']}); логистика и таможня не вычтены. Оценка исходит из того, что без отдела эти сделки выиграли бы "
      f"с маржой сделок, на которые отдел не повлиял; верхняя граница — что не выиграли бы вовсе. Истина между "
      f"ними. На новых поставщиках от отдела, у которых компания раньше не покупала, — {int(t.get('nNew') or 0)} "
      f"сделок: продажа {_eur(t.get('saleNew') or 0)}, продажа − закупка {_eur(t.get('marginNew') or 0)}.</p>")

    a("<h2>2. Поставщики и цены</h2>")
    a("<table class='t'><thead><tr><th style='width:50%'>показатель</th><th style='width:15%'>€</th>"
      "<th style='width:35%'>доля и база</th></tr></thead><tbody>")

    def row(label, val, note):
        a(f"<tr><td>{_e(label)}</td><td class='r'>{val}</td><td>{_e(note)}</td></tr>")
    row("Закупка у поставщиков, найденных отделом", _num(t.get("buySrc") or 0),
        f"{_share(t.get('buySrc') or 0, t.get('buy') or 0)} закупки {t['n']} сделок ({_eur(t.get('buy') or 0)}); "
        f"победителей от отдела {int(t.get('srcWinners') or 0)} из {int(t.get('winners') or 0)}")
    row("в т. ч. у новых поставщиков, найденных отделом (до этого закупок у компании не было)",
        _num(t.get("buySrcNew") or 0),
        f"{int(t.get('srcNew') or 0)} новых поставщиков в {int(t.get('nNew') or 0)} сделках; "
        f"{_share(t.get('buySrcNew') or 0, t.get('buy') or 0)} закупки")
    row("Продажа − закупка сделок с новым поставщиком от отдела", _num(t.get("marginNew") or 0),
        f"продажа {_eur(t.get('saleNew') or 0)}; {_share(t.get('marginNew') or 0, t.get('margin') or 0)} продажи − закупки")
    comp = _share(t.get("baseComp") or 0, t.get("buy") or 0)
    row("Экономия по ценам КП (выбор или торг, большее по позиции)", _num(t.get("es") or 0),
        f"под сравнением цен — {comp} закупки, {int(t.get('posComp') or 0)} позиций")
    row("Отторговано с помощью новых поставщиков", _num(t.get("e3") or 0),
        f"{int(t.get('e3n') or 0)} позиций с ценами нового и прежнего поставщика")
    row("Конкуренция: сделок с двумя и более КП файлом", "—",
        f"{int(t.get('nKp2') or 0)} из {t['n']}; КП файлом {int(t.get('kpFiles') or 0)}; запросов "
        f"{int(t.get('cards') or 0)}, из них вёл отдел {int(t.get('cardsDept') or 0)}")
    a("</tbody></table>")

    a("<h2>3. Предложения</h2><ol>")
    for s in proposals(t, dr):
        a(f"<li>{_e(s)}</li>")
    a("</ol>")

    a("<h2>4. Сотрудники отдела</h2>")
    a("<p>Вклад в выигрыш — КП по запросу сотрудника получено до выигрыша или поставщик его запроса стал "
      "победителем. Сделка засчитывается каждому, кто внёс вклад, поэтому строки не складываются в итог "
      "(он — в разделе 1).</p>")
    a("<table class='t'><thead><tr><th style='width:22%'>сотрудник отдела</th><th style='width:10%'>сделок с его КП "
      "до выигрыша</th><th style='width:10%'>сделок, где нашёл поставщика заказа</th><th style='width:9%'>новых "
      "постав&shy;щиков</th><th style='width:8%'>запросов</th><th style='width:9%'>КП файлом</th>"
      "<th style='width:16%'>продажа сделок с его вкладом, €</th><th style='width:16%'>продажа − закупка, €</th>"
      "</tr></thead><tbody>")
    for x in dr:
        a(f"<tr><td>{_e(x['name'])}</td><td class='r'>{x['kpDeals']}</td><td class='r'>{x['foundDeals']}</td>"
          f"<td class='r'>{x['newFound']}</td><td class='r'>{x['cards']}</td><td class='r'>{x['kpFiles']}</td>"
          f"<td class='r'>{_num(x['sale'])}</td><td class='r'>{_num(x['margin'])}</td></tr>")
    a("</tbody></table>")

    a("<h2>5. Новые поставщики, найденные отделом</h2>")
    if rep["news"]:
        a("<table class='t'><thead><tr><th style='width:7%'>№ реал.</th><th style='width:30%'>сделка</th>"
          "<th style='width:28%'>поставщик</th><th style='width:20%'>нашёл</th><th style='width:15%'>закупка у него, €</th>"
          "</tr></thead><tbody>")
        for x in rep["news"]:
            r = seq_of.get(x["deal"], {})
            a(f"<tr><td class='c'>{r.get('seq') or '—'}</td><td>{_e(r.get('title', ''))}</td><td>{_e(sup(x['sup']))}</td>"
              f"<td>{_e(', '.join(who(u) for u in x['finders']))}</td><td class='r'>{_num(x['buy'])}</td></tr>")
        a("</tbody></table>")
    else:
        a("<p>Новых поставщиков-победителей от отдела в этих сделках нет.</p>")

    sec = 6
    if rep["e3"]:
        a(f"<h2>{sec}. Отторговано с помощью новых поставщиков</h2>")
        a("<table class='t'><thead><tr><th style='width:7%'>№ реал.</th><th style='width:27%'>сделка</th>"
          "<th style='width:16%'>код</th><th style='width:22%'>поставщик (победитель)</th>"
          "<th style='width:14%'>как</th><th style='width:7%'>€</th><th style='width:7%'>ниже прежних</th>"
          "</tr></thead><tbody>")
        for x in rep["e3"]:
            r = seq_of.get(x["deal"], {})
            a(f"<tr><td class='c'>{r.get('seq') or '—'}</td><td>{_e(r.get('title', ''))}</td>"
              f"<td class='pn'>{_e(x['key'])}</td><td>{_e(sup(x['sup']))}</td><td>{_e(x['how'])}</td>"
              f"<td class='r'>{_num(x['e3'])}</td><td class='r'>{_pct(x['pct'])}</td></tr>")
        a("</tbody></table>")
        sec += 1
    a(f"<h2>{sec}. Сделки</h2>")
    a("<table class='t'><thead><tr>"
      "<th style='width:5%'>№ реал.</th><th style='width:17%'>сделка</th><th style='width:6%'>вход</th>"
      "<th style='width:8%'>продажа</th><th style='width:5%'>источ&shy;ник</th><th style='width:8%'>продажа − закупка</th>"
      "<th style='width:17%'>вклад отдела</th><th style='width:6%'>запросов отдела / всего</th>"
      "<th style='width:5%'>КП файлом</th><th style='width:7%'>победи&shy;телей от отдела / новых</th>"
      "<th style='width:16%'>нашёл</th></tr></thead><tbody>")
    for r in sorted(rows, key=lambda x: (x["entry"], x["seq"]), reverse=True):
        a(f"<tr><td class='c'>{r['seq'] or '—'}</td><td>{_e(r['title'])}</td><td class='c'>{r['entry']:%d.%m.%y}</td>"
          f"<td class='r'>{_num(r['sale'])}</td><td class='c'>{'файл' if r['saleSrc'] == 'file' else 'Битрикс'}</td>"
          f"<td class='r'>{_num(r['margin']) if r['margin'] is not None else '—'}</td>"
          f"<td>{_e(lvl_name[r['level']])}</td><td class='c'>{r['cardsDept']} / {r['cards']}</td>"
          f"<td class='r'>{r['kpFiles']}</td><td class='c'>{r['srcWinners']} / {r['srcNew']}</td>"
          f"<td>{_e(', '.join(who(u) for u in r['finders']))}</td></tr>")
    a("</tbody></table>")

    a(f"<h2>{sec + 1}. Как посчитано</h2>")
    why = meta.get("why") or {}
    a(f"<p>Сделки — {t['n']} последних по первому входу в воронку реализации после предпродажной воронки, не "
      f"проигранные; в том же окне пропущено проигранных — {meta.get('lost', 0)}. Запрос отдела — карточка СП-166, "
      f"которую ведёт сотрудник отдела поиска поставщиков с дочерними подразделениями (вместе с ушедшими): "
      f"ответственный карточки, у карточки робота — сорсер из поля сделки. До выигрыша — карточка заведена раньше "
      f"входа сделки в реализацию; КП — файл в «Offer from supplier» или стадия КП (Price at Work, Selected, Not "
      f"Selected). Поставщика заказа нашёл отдел — у поставщика непроваленного заказа СП-172 (нет заказов — у "
      f"выбранного в СП-166) есть запрос отдела этой сделки. Продажа и закупка — строки вкладки «Реализация» на день "
      f"сборки (файл экономики первым, иначе «Сумма» без НДС по правилу); закупка у поставщика — по его доле в суммах "
      f"заказов. Новый поставщик — без заказа СП-172 и без выбранного запроса по другим сделкам до первого запроса "
      f"ему по этой сделке; заказы СП-172 в портале — с {meta.get('ordersSince') or '—'}. Цены КП: строк "
      f"{meta.get('priceRows', 0)}, взято {meta.get('offers', 0)}; не взято — "
      + ("; ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda kv: -kv[1])) or "нет")
      + f". Курсы ЦБ: {meta.get('fxSrc') or 'не получены'}.</p>")
    a("</body></html>")
    return "".join(p)


# ───────────────────────────── ввод-вывод ─────────────────────────────

def _parts(seq: list, parts: int) -> list[list]:
    """Ровно одно разбиение на parts почти равных смежных частей (распоряжение 22.09.2026)."""
    parts = max(1, min(parts, len(seq))) if seq else 1
    k, m = divmod(len(seq), parts)
    out, i = [], 0
    for j in range(parts):
        n = k + (1 if j < m else 0)
        out.append(seq[i:i + n])
        i += n
    return [x for x in out if x]


def read_items(client, entity: int, key: str, ids: list[str], select: list[str], extra: dict | None = None) -> list[dict]:
    """crm.item.list по @key пачками по 50, со сверкой фильтра на ответе."""
    out = []
    for i in range(0, len(ids), 50):
        part = [int(x) for x in ids[i:i + 50]]
        flt = {f"@{key}": part, **(extra or {})}
        got = client.list_items(entity, filter=flt, select=select)
        own = {str(x) for x in part}
        if any(str(x.get(key) or "") not in own for x in got):
            raise RuntimeError(f"фильтр @{key} СП-{entity} не сработал")
        out += got
    return out


def read_prices(card_ids: list[str]) -> list[dict]:
    import psycopg2
    import psycopg2.extras
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        raise RuntimeError("нет SUPABASE_DB_URL")
    conn = psycopg2.connect(dsn, connect_timeout=20, options="-c statement_timeout=300000")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                select id, rfq_id, rfq_company, part_number, price, currency, qty, total, basis,
                       price_date, note
                  from lib_prices_live
                 where feed = %s and rfq_id = any(%s) and price > 0""", (FEED, card_ids))
            return [dict(x) for x in cur.fetchall()]
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Вклад отдела поиска поставщиков в выигрыш сделок: PDF-ведомость")
    ap.add_argument("--data", required=True, help="main.py --data-json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=N_DEALS)
    ap.add_argument("--parts", type=int, default=int(os.environ.get("PROBE_PARTS") or 10))
    a = ap.parse_args(argv)

    import config
    import people
    import stages
    from base import fx as fx_mod
    from bitrix_client import BitrixClient
    from probe_conversion import читать_историю

    today = dt.datetime.now(MSK).date()
    data = json.loads(Path(a.data).read_text(encoding="utf-8"))
    rows = (data.get("contracts") or {}).get("rows") or []
    print(f"строк «Реализации»: {len(rows)}")
    if not rows:
        print("::error::в данных сборки нет строк «Реализации»")
        return 1
    cand = candidates(rows, today, ПОРОГИ["кандидаты_дней"])
    print(f"кандидатов (вход или выигрыш за {ПОРОГИ['кандидаты_дней']} дн.): {len(cand)}")

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    ids = sorted({r["deal"] for r in cand}, key=int)
    hist: dict[str, list] = {}
    for part in _parts(ids, a.parts):
        h, err = читать_историю(client, part)
        if err:
            print(f"::error::часть истории стадий не прочитана: {err}")
            return 1
        hist.update(h)
    entries = {d: realization_entry(hist.get(d, []), set(people.TECH_CATS)) for d in ids}
    print("вход в реализацию: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(k for _, k in entries.values()).items())))
    moved = moved_live(cand, entries)
    sample = moved[:a.n]
    if not sample:
        print("::error::нет сделок, перешедших в реализацию")
        return 1
    lo = sample[-1]["_entry"]
    lost = sum(1 for r in cand if r.get("lost") and entries.get(r["deal"], (None, ""))[1] == "moved"
               and entries[r["deal"]][0] and entries[r["deal"]][0] >= lo)
    print(f"выборка: {len(sample)} из {a.n}; в том же окне проиграно после входа: {lost}")
    sids = [r["deal"] for r in sample]

    # отдел поиска поставщиков: с дочерними подразделениями и ушедшими (как main.py)
    roster = people.roster(client)
    tree = client.dept_tree_ids(config.DEPT_SOURCING_ID)
    dept = client.dept_member_ids(config.DEPT_SOURCING_ID) | {u for u, x in roster.items() if set(x["depts"]) & tree}
    members = {u: {"name": (roster.get(u) or {}).get("name") or f"сотрудник #{u}",
                   "active": (roster.get(u) or {}).get("active", True)} for u in dept}
    pnames = {u: x["name"] + ("" if x["active"] else " (уволен)") for u, x in roster.items()}
    head_uid = next((str(d.get("UF_HEAD") or "") for d in client.departments()
                     if str(d.get("ID")) == str(config.DEPT_SOURCING_ID)), "")
    head = (roster.get(head_uid) or {}).get("name", "")
    print(f"отдел: сотрудников {len(dept)} (действующих {sum(1 for x in members.values() if x['active'])}); "
          f"руководитель в портале {'указан' if head else 'не указан'}")
    service = config.service_accounts({u: x["name"] for u, x in roster.items() if x["active"]})
    sourcer_f = config.DEAL_SOURCER_FIELDS[0][0]
    dd = client.deals_by_ids(sids, select=["ID", sourcer_f])
    sourcer = {d: str(x.get(sourcer_f)) for d, x in dd.items() if str(x.get(sourcer_f) or "0") != "0"}

    # карточки СП-166 и заказы СП-172 сделок выборки — частями по сделкам
    card_sel = ["id", "companyId", "stageId", "createdTime", "parentId2", "assignedById", "createdBy",
                config.RFQ_OFFER_FIELD]
    cards, orders = [], []
    for part in _parts(sids, a.parts):
        cards += read_items(client, config.SPA_ENTITY_TYPE_ID, "parentId2", part, card_sel)
        orders += read_items(client, 172, "parentId2", part,
                             ["id", "companyId", "parentId2", "opportunity", "currencyId", "stageId", "createdTime"])
    for c in cards:
        c["_bucket"] = stages.classify_stage(str(c.get("stageId") or ""))
        c["_offer"] = bool(c.get(config.RFQ_OFFER_FIELD))
    cards_by_deal: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        cards_by_deal[str(c.get("parentId2") or "")].append(c)
    ord_by_deal: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        ord_by_deal[str(o.get("parentId2") or "")].append(o)
    who = {str(c["id"]): (card_person(c, sourcer.get(str(c.get("parentId2") or ""), ""), service) or ROBOT)
           for c in cards}
    print(f"карточек СП-166: {len(cards)}, из них вёл отдел {sum(1 for c in cards if who[str(c['id'])] in dept)}; "
          f"заказов СП-172: {len(orders)}")

    # новый ли поставщик — история заказов и выбранных запросов поставщиков выборки
    sups = sorted({str(x.get("companyId")) for x in cards + orders if str(x.get("companyId") or "0") != "0"}, key=int)
    oh = read_items(client, 172, "companyId", sups, ["id", "companyId", "parentId2", "stageId", "createdTime"])
    # выбранные запросы — только стадия Selected воронки по умолчанию: всех карточек этих
    # поставщиков за всё время — тысячи страниц (CLAUDE.md, «Битрикс не перегружать»)
    sh = read_items(client, config.SPA_ENTITY_TYPE_ID, "companyId", sups,
                    ["id", "companyId", "parentId2", "stageId", "createdTime"],
                    extra={"stageId": f"DT{config.SPA_ENTITY_TYPE_ID}_{config.SPA_CATEGORY_ID}:SUCCESS"})
    osem = client.spa_semantics(172, oh + orders)
    for o in oh + orders:                      # стадия без справочника — по суффиксу
        sid = str(o.get("stageId") or "")
        osem.setdefault(sid, "S" if sid.endswith(":SUCCESS") else "F" if sid.endswith(":FAIL") else "P")
    first = client.call_envelope("crm.item.list", {"entityTypeId": 172, "order": {"id": "ASC"},
                                                   "select": ["id", "createdTime"], "start": 0}) or {}
    orders_since = day((((first.get("result") or {}).get("items") or [{}])[0]).get("createdTime"))
    orders_hist: dict[str, list[dict]] = defaultdict(list)
    for o in oh:
        orders_hist[str(o.get("companyId"))].append(o)
    sel_hist: dict[str, list[dict]] = defaultdict(list)
    for c in sh:
        sel_hist[str(c.get("companyId"))].append(c)
    new_by_deal: dict[str, set[str]] = {}
    for r in sample:
        did = r["deal"]
        fc = first_contact(cards_by_deal.get(did, []), r["_entry"])
        ds = {str(x.get("companyId")) for x in cards_by_deal.get(did, []) + ord_by_deal.get(did, [])}
        new_by_deal[did] = {s for s in ds if s not in ("", "0", "None")
                            and is_new(s, did, fc.get(s, r["_entry"]), orders_hist, sel_hist, osem)}
    print(f"поставщиков выборки: {len(sups)}; их заказов в истории: {len(oh)}; выбранных запросов: {len(sh)}; "
          f"заказы СП-172 в портале с {orders_since or '—'}")

    # цены разобранных КП по карточкам выборки
    card_map = {str(c["id"]): c for c in cards}
    try:
        prices = read_prices(sorted(card_map, key=int))
    except Exception as e:                     # noqa: BLE001 — без цен ведомость без экономии
        print(f"::warning::цены КП не прочитаны: {type(e).__name__}")
        prices = []
    codes = {str(p.get("currency") or "").upper() for p in prices} | \
            {str(o.get("currencyId") or "").upper() for o in orders}
    days = {day(p.get("price_date")) for p in prices} | {day(c.get("createdTime")) for c in cards} | \
           {day(o.get("createdTime")) for o in orders}
    fx, info = fx_mod.load_cbr({c for c in codes if c}, {d for d in days if d}, today=today)
    print(f"курсы: {info.get('src') or 'нет'}; запросов {info.get('requests', 0)}; без ряда {len(info.get('failed') or [])}")
    offers, why = offer_rows(prices, card_map, fx, today)
    print(f"строк цен КП: {len(prices)}; взято предложений: {len(offers)}; не взято: "
          + ", ".join(f"{k} {v}" for k, v in sorted(why.items())))

    rep = build(sample, cards_by_deal, orders, osem, who, dept, new_by_deal, offers, fx, set(stages.KP_STAGES))
    t = totals(rep["rows"])
    span_days = max((sample[0]["_entry"] - lo).days, 1)
    t["months"] = span_days / 30.44
    cfx = (data.get("contracts") or {}).get("fx") or {}
    t["eurRub"] = (float(cfx["eurUsd"]) * float(cfx["usdRub"])) if (cfx.get("eurUsd") and cfx.get("usdRub")) else None
    dr = dept_rows(rep["people"], members)
    names = client.companies_by_ids({x["sup"] for x in rep["news"] + rep["e3"]})
    # журнал — только счётчики и доли (правило 17)
    print("уровни вклада: " + ", ".join(f"{k} {t['levels'][k]['n']}" for k, _ in УРОВНИ)
          + f"; контроль с обеими суммами {t.get('ctrlN', 0)}; оценка пользы "
          + ("посчитана" if t.get("benefit") is not None else "не посчитана")
          + f"; новых поставщиков от отдела {int(t.get('srcNew') or 0)}; позиций под сравнением цен "
            f"{int(t.get('posComp') or 0)}; сотрудников в таблице отдела {len(dr)}")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"today": today, "head": head, "deptN": len(dept), "dept": dr, "lost": lost,
            "ordersSince": orders_since.strftime("%d.%m.%Y") if orders_since else "",
            "priceRows": len(prices), "offers": len(offers), "why": dict(why),
            "fxSrc": {"cbr": "ряды cbr.ru", "mirror": "зеркало ЦБ"}.get(info.get("src") or "", "")}
    (out / "doc.html").write_text(render(rep, t, meta, names, pnames), encoding="utf-8")
    (out / "ids.txt").write_text("".join(f"{r['seq']}\n" for r in rep["rows"] if r["seq"]), encoding="utf-8")
    print(f"ведомость: сделок {len(rep['rows'])}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                     # noqa: BLE001 — в публичный журнал только класс ошибки
        # свои RuntimeError — постоянный текст без данных; прочие — только класс
        print(f"::error::сбой: {type(e).__name__}" + (f": {e}" if type(e) is RuntimeError else ""))
        sys.exit(1)
