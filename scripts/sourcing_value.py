#!/usr/bin/env python3
"""Деньги сорсинга: последние N сделок, перешедших в реализацию, — PDF владельцу в Telegram.

ВОПРОС ВЛАДЕЛЬЦА (30.10.2026). «Посчитай, сколько денег принёс компании отдел сорсинга —
на последних 50 сделках, перешедших в реализацию. Новые поставщики: на сколько они
отторгованы с помощью новых поставщиков».

ГДЕ СЧИТАЕТСЯ. Только внутри прогона sourcing-value.yml: суммы — закрытые сведения, журнал
Actions публичный (CLAUDE.md, правило 5 и распоряжение 30.09.2026). Файл ведомости остаётся
на раннере и уходит владельцу ботом оповещений (scripts/premia_telegram.py), артефактов нет.
В журнал — только счётчики и доли: ни сумм, ни названий сделок, поставщиков и людей.

ИСТОЧНИКИ.
  • Строки «Реализации» (main.py --data-json → contracts.rows) — продажа и закупка сделки в €
    без НДС тем же каскадом, что на вкладке: сверенный файл экономики первым, иначе «Сумма»
    по правилу НДС; закупка — файл экономики или заказы СП-172 по курсу ЦБ. Свои формулы денег
    сделки скрипт не заводит (правило 14: одна вставка формулы).
  • Битрикс: история стадий сделок (вход в реализацию), карточки СП-166 сделок (поставщик,
    стадия, файл КП), заказы СП-172 сделок (поставщик и сумма — для доли поставщика в закупке),
    история заказов и выбранных запросов тех же поставщиков (новый ли поставщик), поле «Сорсер».
  • Закрытая база: цены разобранных КП поставщиков (lib_prices_live, поток «разбор КП») по
    номерам карточек СП-166 этих сделок.
  • Курсы ЦБ РФ на дату цены (base.fx.load_cbr).

ОПРЕДЕЛЕНИЯ (все пороги — таблицей ПОРОГИ, правило 4).
  • СДЕЛКА, ПЕРЕШЕДШАЯ В РЕАЛИЗАЦИЮ — первый вход в воронку реализации (кат. 0) ПОСЛЕ входа в
    предпродажную воронку (не техническую, people.TECH_CATS). Кат. 0 — и воронка по умолчанию:
    сделка, заведённая в ней и перенесённая в предпродажу, по первому входу выглядела бы старой
    (probe_conversion). Заведённые сразу в реализацию и проигранные — отдельными счётчиками.
  • ПОБЕДИТЕЛЬ — поставщик непроваленного заказа СП-172 сделки; заказов нет — поставщик
    карточки СП-166 в стадии Selected.
  • НОВЫЙ ПОСТАВЩИК — до первой карточки запроса ему по этой сделке (нет карточки — до входа
    сделки в реализацию) у него не было ни одного непроваленного заказа СП-172 и ни одного
    выбранного запроса по другим сделкам. Глубина — с первого заказа СП-172 в портале (печатается).
  • ПОЗИЦИЯ — код из строки КП (буквы и цифры, верхний регистр). Цена — за единицу в € без НДС:
    «с НДС» в примечании у рублёвой цены снимается по ставке РФ на дату (price_norm), у прочих —
    позиция не сравнивается. Деньги позиции — сумма строки КП победителя (если цена × количество
    сходится с ней), иначе цена × количество; цена лота на количество не умножается.
  • СРАВНИМЫ — базис совпадает или не указан хотя бы у одного (разные известные коды
    Инкотермс — не сравниваются) и цены отличаются не больше чем в ПОРОГИ["раз"] раз.
  • Э1 ЭКОНОМИЯ НА ВЫБОРЕ — (лучшая итоговая цена прочих поставщиков − цена победителя) ×
    деньги позиции / цена победителя, если больше нуля. Меньше нуля — «выбрано дороже».
  • Э2 ТОРГ — (первая цена победителя − его итоговая цена) в долях итоговой × деньги позиции.
  • Э1 и Э2 не складываются: по позиции берётся большее (ЭС — нижняя граница общего эффекта).
  • Э3 ОТТОРГОВАНО С ПОМОЩЬЮ НОВЫХ — позиции, где КП дали и новый, и прежний поставщик:
    (лучшая ПЕРВАЯ цена прежних − заплаченная итоговая) × деньги / заплаченная, если больше нуля,
    и только когда выбран новый поставщик или прежний снизил цену после КП нового.

ЛЮДИ И ПРЕМИЯ (уточнение владельца 30.10.2026: «оценить работу тех, кто создавал процесс, и
на сколько они зарабатывают премию»).
  • ВЕДУЩИЙ ЗАПРОСЫ — ответственный карточки СП-166; карточка робота (config.service_accounts) —
    сорсер из поля сделки, как main.py. Ему засчитываются его карточки, КП файлом, новые
    победители по его карточкам и ЭС, Э3, «выбрано дороже» позиций, выигранных поставщиком его
    карточки.
  • ПРЕМИЯ — строки «Премий» той же сборки (premia.compute, положение ред. 2): пул сделки при
    К = 1 × К по умолчанию × доля роли; оплаченная сделка — «заработано», остальные —
    «ожидается»; выброс (big) — без премии, как во вкладке. Своей формулы премии здесь нет
    (правило 14). Сорсеру положение премии не назначает — это видно в таблице числом.

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
    "покрытие_цель": 0.70,      # доля закупки под сравнением КП, к которой вести (предложение)
    "кп_мин": 3,                # КП с ценой на позицию — минимум конкурентной закупки (предложение)
}
# Пример ставки премии сорсеру от доказанной экономии — только иллюстрация для решения
# владельца (положение ред. 2 сорсинг не оплачивает), не норма.
СТАВКА_ПРИМЕР = 0.10
FEED = "разбор КП"
SELECTED = "selected"
TOP_ROWS = 15                   # строк в таблицах «выбрано дороже» и «новые поставщики»


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


def pick(rows: list[dict], entries: dict[str, tuple[dt.datetime | None, str]], n: int) -> tuple[list[dict], dict]:
    """N последних по входу в реализацию после предпродажи, не проигранных. → (строки, счётчики
    пропущенных в том же окне: заведены сразу в реализацию, проиграны после входа)."""
    moved = [(entries[r["deal"]][0], r) for r in rows
             if entries.get(r["deal"], (None, ""))[1] == "moved" and entries[r["deal"]][0]]
    moved.sort(key=lambda x: (x[0], int(r_seq(x[1]))), reverse=True)
    live = [(t, r) for t, r in moved if not r.get("lost")]
    chosen = live[:n]
    lo = chosen[-1][0] if chosen else None
    skipped = {
        "lost": sum(1 for t, r in moved if r.get("lost") and lo and t >= lo),
        "direct": sum(1 for r in rows if entries.get(r["deal"], (None, ""))[1] == "direct"
                      and entries[r["deal"]][0] and lo and entries[r["deal"]][0] >= lo),
    }
    out = []
    for t, r in chosen:
        x = dict(r)
        x["_entry"] = t
        out.append(x)
    return out, skipped


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


def build(deals: list[dict], cards_by_deal: dict[str, list[dict]], orders: list[dict],
          osem: dict[str, str], new_by_deal: dict[str, set[str]], offers: list[dict],
          sourcer: dict[str, str], fx, service: set[str] | None = None,
          pnames: dict[str, str] | None = None) -> dict:
    """Итог по сделкам, сводам и людям. Все суммы — € без НДС. sourcer — сделка → uid поля
    «Сорсер»; service — служебные записи (роботы); pnames — uid → имя."""
    service, pnames = service or set(), pnames or {}
    people: dict[str, dict] = defaultdict(lambda: {"deals": set(), "cards": 0, "kpFiles": 0, "newFound": 0,
                                                   "es": 0.0, "e3": 0.0, "over": 0.0, "base": 0.0})
    shares = order_shares(orders, osem, fx)
    off_by_deal: dict[str, list[dict]] = defaultdict(list)
    for o in offers:
        off_by_deal[o["deal"]].append(o)
    rows, over_all, e3_all = [], [], []
    for r in deals:
        did = r["deal"]
        cards = cards_by_deal.get(did, [])
        sh = shares.get(did, {})
        winners = set(sh) or {str(c.get("companyId")) for c in cards
                              if c.get("_bucket") == SELECTED and str(c.get("companyId") or "0") != "0"}
        new = new_by_deal.get(did, set())
        card_sups = {str(c.get("companyId")) for c in cards if str(c.get("companyId") or "0") != "0"}
        pos = deal_positions(off_by_deal.get(did, []), winners, new)
        over_all += pos.pop("overList")
        e3_all += pos.pop("e3List")
        by_card = pos.pop("byCard")
        who = {str(c.get("id")): card_person(c, sourcer.get(did, ""), service) or ROBOT for c in cards}
        for c in cards:
            x = people[who[str(c.get("id"))]]
            x["deals"].add(did)
            x["cards"] += 1
            x["kpFiles"] += 1 if c.get("_offer") else 0
        for s_new in winners & new:            # новый победитель — тому, кто вёл запрос ему
            for u in {who[str(c.get("id"))] for c in cards if str(c.get("companyId")) == s_new}:
                people[u]["newFound"] += 1
        for cid, m in by_card.items():
            x = people[who.get(cid, "")]
            for k, v in m.items():
                x[k] += v
        lead = Counter(who.values()).most_common(1)
        lead_uid = lead[0][0] if lead else sourcer.get(did, "")
        lead_uid = "" if lead_uid == ROBOT else lead_uid
        buy = _f(r.get("buyEur")) if r.get("buySrc") not in ("none", "fx") and not r.get("buySusp") else 0.0
        sale = _f(r.get("saleEur")) if r.get("saleKnown", True) else 0.0
        new_share = sum(v for s, v in sh.items() if s in new) if sh else (1.0 if winners and winners <= new else 0.0)
        via_share = sum(v for s, v in sh.items() if s in card_sups) if sh else (1.0 if winners & card_sups else 0.0)
        rows.append({
            "deal": did, "seq": r_seq(r), "title": r.get("title") or f"Сделка #{did}",
            "customer": r.get("customer") or "—", "entry": r["_entry"].astimezone(MSK).date(),
            "sale": sale, "buy": buy, "saleSrc": r.get("saleSrc") or "", "buySrc": r.get("buySrc") or "",
            "margin": (sale - buy) if (sale and buy) else None,
            "planMd": r.get("planMdEur"), "planProfit": r.get("planProfitEur"),
            "cards": len(cards), "kpFiles": sum(1 for c in cards if c.get("_offer")),
            "sups": len(card_sups), "winners": len(winners), "newWinners": len(winners & new),
            "newShare": new_share, "viaShare": via_share,
            "sourcer": (pnames.get(lead_uid) or (f"сотрудник #{lead_uid}" if lead_uid else "")
                        or ("запросы робота без сорсера" if cards else "запросов нет")),
            **pos,
        })
    return {"rows": rows, "over": sorted(over_all, key=lambda x: -x["over"]),
            "e3": sorted(e3_all, key=lambda x: -x["e3"]), "people": dict(people)}


def premium(premia: dict | None, deal_ids: set[str], k: float) -> tuple[dict[str, dict], dict]:
    """Премия по положению ред. 2 (premia.py) по сделкам выборки: uid → {earned (оплаченные
    сделки), expected (в реализации), roles, deals} в ₽ при К = k. Строку «Премий» имеют
    открытые и оплаченные сделки; выбросы (big) — как во вкладке, без премии. → (люди, охват)."""
    out: dict[str, dict] = defaultdict(lambda: {"earned": 0.0, "expected": 0.0, "roles": set(), "deals": set()})
    cov = {"rows": 0, "big": 0, "eurRub": None}
    if not premia:
        return {}, cov
    cov["eurRub"] = (premia.get("params") or {}).get("eurRub")
    for d in premia.get("deals") or []:
        if str(d.get("id")) not in deal_ids:
            continue
        cov["rows"] += 1
        if "big" in (d.get("fl") or []):
            cov["big"] += 1
            continue
        for uid, role, share in d.get("parts") or []:
            x = out[str(uid or "")]
            x["roles"].add(role)
            x["deals"].add(str(d.get("id")))
            x["earned" if d.get("st") == "d" else "expected"] += float(d.get("p1") or 0) * k * float(share)
    return dict(out), cov


ROLE = {"ex": "исполнитель", "kam": "КАМ", "po": "продукт-оунер"}
ROBOT = "~"                     # ключ людей: запрос робота, сорсер в сделке не указан


def people_rows(ppl: dict[str, dict], prem: dict[str, dict], pnames: dict[str, str],
                eur_rub: float | None) -> list[dict]:
    """Один человек — одна строка: запросы и деньги, которые он принёс, и его премия."""
    rows = []
    for uid in set(ppl) | set(prem):
        a, b = ppl.get(uid) or {}, prem.get(uid) or {}
        roles = (["вёл запросы"] if a.get("cards") else []) + [ROLE.get(r, r) for r in sorted(b.get("roles", ()))]
        prize = (b.get("earned") or 0.0) + (b.get("expected") or 0.0)
        rows.append({
            "uid": uid, "name": ("запросы робота, сорсер в сделке не указан" if uid == ROBOT else pnames.get(uid)
                                 or (f"сотрудник #{uid}" if uid else "роль не закреплена")),
            "roles": ", ".join(roles), "deals": len((a.get("deals") or set()) | (b.get("deals") or set())),
            "cards": a.get("cards", 0), "kpFiles": a.get("kpFiles", 0), "newFound": a.get("newFound", 0),
            "es": a.get("es", 0.0), "e3": a.get("e3", 0.0), "over": a.get("over", 0.0),
            "earned": b.get("earned", 0.0), "expected": b.get("expected", 0.0), "prize": prize,
            # премия к доказанной экономии его запросов — в одной валюте (₽)
            "prizePerEs": (prize / (a.get("es", 0.0) * eur_rub)) if (eur_rub and a.get("es")) else None,
        })
    return sorted(rows, key=lambda r: (-r["es"], -r["prize"]))


def totals(rows: list[dict]) -> dict:
    t: dict = defaultdict(float)
    for r in rows:
        for k in ("sale", "buy", "e1", "e2", "es", "e3", "e3NewWin", "e3OldDrop", "over", "missNew",
                  "baseWin", "baseComp", "baseSolo"):
            t[k] += r[k] or 0.0
        for k in ("cards", "kpFiles", "pos", "posWin", "posComp", "posSolo", "e3n", "overN",
                  "basisSkip", "ratioSkip"):
            t[k] += r[k] or 0
        if r["margin"] is not None:
            t["margin"] += r["margin"]
            t["marginSale"] += r["sale"]
            t["marginBuy"] += r["buy"]
            t["nMargin"] += 1
        if r["planMd"] is not None:
            t["planMd"] += r["planMd"]
            t["nPlan"] += 1
        if r["saleSrc"] == "file":
            t["nFile"] += 1
        if r["cards"]:
            t["nCards"] += 1
        if r["posComp"]:
            t["nComp"] += 1
        if r["newWinners"]:
            t["nNew"] += 1
            t["buyNew"] += r["buy"] * r["newShare"]
            if r["margin"] is not None:
                t["marginNewDeals"] += r["margin"]
        t["buyVia"] += r["buy"] * r["viaShare"]
        t["newWinners"] += r["newWinners"]
        t["winners"] += r["winners"]
    t["n"] = len(rows)
    return dict(t)


# ───────────────────────────── выводы и предложения ─────────────────────────────

def proposals(t: dict, meta: dict | None = None) -> list[str]:
    """Прикладные предложения — из чисел итога, без общих слов. Оценки подстановкой помечены."""
    out = []
    meta = meta or {}
    eur_rub = meta.get("eurRub")
    src = [r for r in meta.get("people") or [] if r["cards"]]
    if src and eur_rub:
        es = sum(r["es"] for r in src)
        prize = sum(r["prize"] for r in src)
        unpaid = [r for r in src if r["es"] > 0 and r["prize"] <= 0]
        out.append(
            f"Ведущие запросы ({len(src)} чел.) доказали экономию {_eur(es)} ({_rub(es * eur_rub)}), а их премия "
            f"по положению ред. 2 на этих сделках — {_rub(prize)}, и только за роли исполнителя, КАМа или "
            f"продукт-оунера: сорсинг положение не оплачивает; с экономией и без премии — {len(unpaid)} чел. "
            f"Предложение: премия ведущему запросы — доля доказанной экономии ЭС его запросов; при ставке "
            f"{_pct(СТАВКА_ПРИМЕР)} это {_rub(es * eur_rub * СТАВКА_ПРИМЕР)} на эти {int(t.get('n') or 0)} сделок "
            f"(ставку решает владелец; пример, не норма). Начислять только по позициям с КП файлом и ценой "
            f"конкурента — это сразу поднимет долю закупки под сравнением.")
    buy = t.get("buy") or 0.0
    comp_share = (t.get("baseComp") or 0.0) / buy if buy else 0.0
    rate = (t.get("es") or 0.0) / t["baseComp"] if t.get("baseComp") else 0.0
    if comp_share < ПОРОГИ["покрытие_цель"]:
        rest = max(buy - (t.get("baseComp") or 0.0), 0.0)
        out.append(
            f"Под сравнением КП — {_pct(comp_share)} закупки ({_eur(t.get('baseComp') or 0)} из {_eur(buy)}). "
            f"Цель — {_pct(ПОРОГИ['покрытие_цель'])}: каждое КП — файлом в «Offer from supplier» "
            f"(правило 19.10.2026) и в разбор цен; без этого экономию не доказать. "
            + (f"Если на закупке без сравнения ({_eur(rest)}) сохранится та же доля экономии "
               f"({_pct(rate)}), это ещё около {_eur(rest * rate)} — оценка подстановкой, не факт."
               if rate > 0 else ""))
    if t.get("posSolo"):
        out.append(
            f"Позиций у победителя без сравнимого конкурента — {int(t['posSolo'])} на {_eur(t.get('baseSolo') or 0)}. "
            f"Минимум {ПОРОГИ['кп_мин']} КП с ценой на позицию до выбора: на позициях с конкурентом "
            f"экономия составила {_pct(rate)} закупки.")
    if t.get("overN"):
        out.append(
            f"Выбрано дороже лучшего сравнимого КП — {int(t['overN'])} позиций, разница {_eur(t.get('over') or 0)}. "
            f"По каждой из раздела 6 записать причину в карточке СП-166 (срок, техника, базис, надёжность); "
            f"без причины — переторжка с победителем по цене лучшего КП.")
    if t.get("missNew"):
        out.append(
            f"КП новых поставщиков дешевле выбранного на {_eur(t['missNew'])}: проверить новых "
            f"(образец, документы, срок) и вернуть их в следующую закупку тех же кодов.")
    if t.get("n"):
        nn = int(t.get("nNew") or 0)
        e3 = t.get("e3") or 0.0
        out.append(
            f"Новый поставщик выиграл в {nn} сделках из {int(t['n'])}; отторговано с их помощью {_eur(e3)}. "
            f"Держать долю новых в запросах каждой сделки: не меньше одного нового поставщика на "
            f"позицию, если прежних меньше {ПОРОГИ['кп_мин']}.")
    nc = int(t.get("n", 0) - t.get("nCards", 0))
    if nc > 0:
        out.append(
            f"Сделок без единого запроса СП-166 — {nc} из {int(t['n'])}: поставщика в них выбирали без "
            f"сорсинга, сравнения нет. Включить запрос через СП-166 обязательным шагом до ТКП.")
    return out


# ───────────────────────────── вёрстка ─────────────────────────────

def _eur(v: float) -> str:
    s = f"{abs(round(v)):,}".replace(",", " ")
    return ("−" if round(v) < 0 else "") + s + " €"


def _rub(v: float) -> str:
    s = f"{abs(round(v)):,}".replace(",", "\u00a0")
    return ("−" if round(v) < 0 else "") + s + "\u00a0₽"


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


def render(rep: dict, t: dict, meta: dict, names: dict[str, str]) -> str:
    rows = rep["rows"]
    lo = min((r["entry"] for r in rows), default=None)
    hi = max((r["entry"] for r in rows), default=None)
    span = f"{lo:%d.%m.%Y} — {hi:%d.%m.%Y}" if lo else "—"
    buy = t.get("buy") or 0.0
    marg = t.get("margin") or 0.0
    rate = (t.get("es") or 0.0) / t["baseComp"] if t.get("baseComp") else 0.0
    nm = lambda s: names.get(s) or f"компания #{s}"   # noqa: E731
    title_of = {r["deal"]: r for r in rows}
    p: list[str] = []
    a = p.append
    a(f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Деньги сорсинга</title>"
      f"<style>{CSS}</style></head><body>")
    a(f"<h1>Деньги сорсинга: {t['n']} последних сделок, перешедших в реализацию</h1>")
    a(f"<div class='sub'>сборка {meta['today']:%d.%m.%Y}; вход в реализацию {span}; "
      f"суммы — евро без НДС, по курсу ЦБ РФ</div>")

    a("<h2>1. Итог</h2>")
    a("<table class='t'><thead><tr><th style='width:52%'>показатель</th><th style='width:16%'>€</th>"
      "<th style='width:32%'>база и охват</th></tr></thead><tbody>")

    def row(label, val, note):
        a(f"<tr><td>{_e(label)}</td><td class='r'>{val}</td><td>{_e(note)}</td></tr>")
    row("Продажа сделок", _num(t.get("sale") or 0),
        f"{t['n']} сделок; из сверенного файла экономики — {int(t.get('nFile') or 0)}")
    row("Закупка у поставщиков", _num(buy), "по строкам «Реализации»: файл экономики или заказы СП-172")
    row("Продажа − закупка (наценка над поставщиком; логистика и таможня не вычтены)", _num(marg),
        f"{int(t.get('nMargin') or 0)} сделок с обеими суммами; "
        + (_pct(marg / t['marginSale']) + " продажи" if t.get("marginSale") else "—"))
    if t.get("nPlan"):
        row("Маржинальный доход по книгам бюджета", _num(t.get("planMd") or 0),
            f"{int(t['nPlan'])} сделок с книгой бюджета")
    row("Экономия сорсинга ЭС: большее из Э1 и Э2 по каждой позиции", _num(t.get("es") or 0),
        f"{_pct(rate)} закупки под сравнением ({_eur(t.get('baseComp') or 0)})" if t.get("baseComp")
        else "сравнимых позиций нет")
    row("в т. ч. Э1 — выбор против следующего по цене сравнимого КП", _num(t.get("e1") or 0),
        f"{int(t.get('posComp') or 0)} позиций с конкурентом в {int(t.get('nComp') or 0)} сделках")
    row("в т. ч. Э2 — торг: первая цена победителя против итоговой", _num(t.get("e2") or 0),
        f"{int(t.get('posWin') or 0)} позиций победителя с ценой")
    row("Э3 — отторговано с помощью новых поставщиков", _num(t.get("e3") or 0),
        f"{int(t.get('e3n') or 0)} позиций: выбран новый — {_eur(t.get('e3NewWin') or 0)}; "
        f"прежний снизил цену после КП нового — {_eur(t.get('e3OldDrop') or 0)}")
    row("Закупка у новых поставщиков", _num(t.get("buyNew") or 0),
        f"{_pct((t.get('buyNew') or 0) / buy) if buy else '—'} закупки; новых победителей "
        f"{int(t.get('newWinners') or 0)} из {int(t.get('winners') or 0)}; сделок с новым — {int(t.get('nNew') or 0)}")
    row("Продажа − закупка сделок с новым поставщиком", _num(t.get("marginNewDeals") or 0),
        "сделки, где хотя бы один победитель — новый")
    row("Закупка у поставщиков, пришедших через запросы СП-166 этой сделки", _num(t.get("buyVia") or 0),
        f"{_pct((t.get('buyVia') or 0) / buy) if buy else '—'} закупки; сделок с запросами — "
        f"{int(t.get('nCards') or 0)} из {t['n']}")
    _pp, _er = meta.get("people") or [], meta.get("eurRub")
    if _pp and _er:
        _all = sum(x["prize"] for x in _pp)
        _src = sum(x["prize"] for x in _pp if x["cards"])
        row(f"Премия по положению ред. 2 на этих сделках (К = {str(meta.get('k')).replace('.', ',')})",
            _num(_all / _er),
            f"{_rub(_all)}: заработано {_rub(sum(x['earned'] for x in _pp))}, ожидается "
            f"{_rub(sum(x['expected'] for x in _pp))}; из неё ведущим запросы — {_rub(_src)}")
    row("Выбрано дороже лучшего сравнимого КП", _num(t.get("over") or 0),
        f"{int(t.get('overN') or 0)} позиций; КП новых дешевле выбранного — {_eur(t.get('missNew') or 0)}")
    a("</tbody></table>")
    a(f"<p>Прямые деньги сорсинга на этих сделках — экономия ЭС {_eur(t.get('es') or 0)}: на столько "
      f"закупка дешевле, чем у следующего поставщика или по первой цене победителя (по позиции берётся "
      f"большее, суммы Э1 и Э2 не складываются). Из неё отторговано с помощью новых поставщиков "
      f"{_eur(t.get('e3') or 0)}. Продажа − закупка {_eur(marg)} — деньги сделок, которые сорсинг "
      f"обеспечил поставщиком: {_pct((t.get('buyVia') or 0) / buy) if buy else '—'} закупки пришло от "
      f"поставщиков из запросов СП-166. Экономия доказана ценами разобранных КП и считается только по "
      f"позициям, где у победителя и конкурента есть цена одного кода; закупка вне сравнения "
      f"({_eur(max(buy - (t.get('baseComp') or 0), 0))}) в экономию не входит.</p>")

    a("<h2>2. Люди: кто вёл запросы, что принёс, какая премия</h2>")
    k, cov = meta.get("k"), meta.get("premCov") or {}
    a(f"<p>Ведущий запросы — ответственный карточки СП-166; у карточки робота — сорсер из поля сделки. Ему "
      f"засчитываются его запросы, КП файлом, новые поставщики, выигравшие по его запросу, и экономия ЭС и Э3 "
      f"по позициям, которые выиграл поставщик его карточки. Премия — по положению о мотивации ред. 2, тем же "
      f"расчётом, что вкладка «Премии» (premia.py): пул 20 % результата сделки × К, исполнителю (ответственному "
      f"сделки) 7/20, КАМу и продукт-оунеру 13/20; К = {str(k).replace('.', ',') if k else '—'}. Заработано — "
      f"по оплаченным сделкам, ожидается — по сделкам в реализации. Строк премии у сделок выборки — "
      f"{cov.get('rows', 0)} из {t['n']} (закрытых сделок в расчёте премий нет), из них без премии как выброс — "
      f"{cov.get('big', 0)}. Сорсеру положение премии не назначает: у ведущего запросы она есть, только если он "
      f"же исполнитель, КАМ или продукт-оунер сделки.</p>")
    a("<table class='t'><thead><tr><th style='width:18%'>сотрудник</th><th style='width:10%'>роль</th>"
      "<th style='width:5%'>сде&shy;лок</th><th style='width:6%'>запро&shy;сов</th><th style='width:6%'>КП файлом</th>"
      "<th style='width:7%'>новых победи&shy;телей</th><th style='width:7%'>ЭС, €</th><th style='width:7%'>Э3, €</th>"
      "<th style='width:7%'>дороже лучшего КП, €</th><th style='width:9%'>премия заработана, ₽</th>"
      "<th style='width:9%'>премия ожидается, ₽</th><th style='width:9%'>премия на 1 ₽ экономии</th>"
      "</tr></thead><tbody>")
    for x in meta.get("people") or []:
        ratio = (f"{x['prizePerEs']:.2f}".replace(".", ",") if x["prizePerEs"] is not None else "—")
        a(f"<tr><td>{_e(x['name'])}</td><td>{_e(x['roles'])}</td><td class='r'>{x['deals']}</td>"
          f"<td class='r'>{x['cards']}</td><td class='r'>{x['kpFiles']}</td><td class='r'>{x['newFound']}</td>"
          f"<td class='r'>{_num(x['es'])}</td><td class='r'>{_num(x['e3'])}</td><td class='r'>{_num(x['over'])}</td>"
          f"<td class='r'>{_num(x['earned'])}</td><td class='r'>{_num(x['expected'])}</td>"
          f"<td class='r'>{ratio}</td></tr>")
    a("</tbody></table>")

    a("<h2>3. Предложения</h2><ol>")
    for s in proposals(t, meta):
        a(f"<li>{_e(s)}</li>")
    a("</ol>")

    a("<h2>4. Сделки</h2>")
    a("<table class='t'><thead><tr>"
      "<th style='width:5%'>№ реал.</th><th style='width:15%'>сделка</th><th style='width:6%'>вход</th>"
      "<th style='width:8%'>продажа</th><th style='width:5%'>источ&shy;ник</th><th style='width:8%'>закупка</th><th style='width:8%'>продажа − закупка</th>"
      "<th style='width:5%'>запро&shy;сов</th><th style='width:5%'>КП файлом</th><th style='width:6%'>победи&shy;телей / новых</th>"
      "<th style='width:7%'>под срав&shy;нением</th><th style='width:7%'>ЭС</th><th style='width:7%'>Э3</th>"
      "<th style='width:8%'>вёл запросы</th></tr></thead><tbody>")
    for r in sorted(rows, key=lambda x: (x["entry"], x["seq"]), reverse=True):
        src = "файл" if r["saleSrc"] == "file" else "Битрикс"
        a(f"<tr><td class='c'>{r['seq'] or '—'}</td><td>{_e(r['title'])}</td>"
          f"<td class='c'>{r['entry']:%d.%m.%y}</td>"
          f"<td class='r'>{_num(r['sale'])}</td><td class='c'>{src}</td><td class='r'>{_num(r['buy'])}</td>"
          f"<td class='r'>{_num(r['margin']) if r['margin'] is not None else '—'}</td>"
          f"<td class='r'>{r['cards']}</td><td class='r'>{r['kpFiles']}</td>"
          f"<td class='c'>{r['winners']} / {r['newWinners']}</td>"
          f"<td class='r'>{_num(r['baseComp'])}</td><td class='r'>{_num(r['es'])}</td>"
          f"<td class='r'>{_num(r['e3'])}</td><td>{_e(r['sourcer'])}</td></tr>")
    a(f"<tr><td></td><td>Итого</td><td></td><td class='r'>{_num(t.get('sale') or 0)}</td><td></td>"
      f"<td class='r'>{_num(buy)}</td><td class='r'>{_num(marg)}</td><td class='r'>{int(t.get('cards') or 0)}</td>"
      f"<td class='r'>{int(t.get('kpFiles') or 0)}</td>"
      f"<td class='c'>{int(t.get('winners') or 0)} / {int(t.get('newWinners') or 0)}</td>"
      f"<td class='r'>{_num(t.get('baseComp') or 0)}</td><td class='r'>{_num(t.get('es') or 0)}</td>"
      f"<td class='r'>{_num(t.get('e3') or 0)}</td><td></td></tr>")
    a("</tbody></table>")

    a("<h2>5. Отторговано с помощью новых поставщиков</h2>")
    if rep["e3"]:
        a("<table class='t'><thead><tr><th style='width:6%'>№ реал.</th><th style='width:26%'>сделка</th>"
          "<th style='width:16%'>код</th><th style='width:22%'>поставщик (победитель)</th>"
          "<th style='width:14%'>как</th><th style='width:8%'>€</th><th style='width:8%'>ниже прежних</th>"
          "</tr></thead><tbody>")
        for x in rep["e3"][:TOP_ROWS]:
            r = title_of.get(x["deal"], {})
            a(f"<tr><td class='c'>{r.get('seq') or '—'}</td><td>{_e(r.get('title', ''))}</td>"
              f"<td class='pn'>{_e(x['key'])}</td><td>{_e(nm(x['sup']))}</td><td>{_e(x['how'])}</td>"
              f"<td class='r'>{_num(x['e3'])}</td><td class='r'>{_pct(x['pct'])}</td></tr>")
        a("</tbody></table>")
        if len(rep["e3"]) > TOP_ROWS:
            a(f"<p>Показаны {TOP_ROWS} крупнейших из {len(rep['e3'])} позиций; итог раздела 1 — по всем.</p>")
    else:
        a("<p>Позиций, где КП дали и новый, и прежний поставщик одного кода, в выборке нет.</p>")

    a("<h2>6. Выбрано дороже лучшего сравнимого КП</h2>")
    if rep["over"]:
        a("<table class='t'><thead><tr><th style='width:6%'>№ реал.</th><th style='width:28%'>сделка</th>"
          "<th style='width:16%'>код</th><th style='width:26%'>победитель</th><th style='width:8%'>КП</th>"
          "<th style='width:8%'>€ сверх</th><th style='width:8%'>дороже на</th></tr></thead><tbody>")
        for x in rep["over"][:TOP_ROWS]:
            r = title_of.get(x["deal"], {})
            a(f"<tr><td class='c'>{r.get('seq') or '—'}</td><td>{_e(r.get('title', ''))}</td>"
              f"<td class='pn'>{_e(x['key'])}</td><td>{_e(nm(x['sup']))}</td><td class='r'>{x['n']}</td>"
              f"<td class='r'>{_num(x['over'])}</td><td class='r'>{_pct(x['pct'])}</td></tr>")
        a("</tbody></table>")
    else:
        a("<p>Позиций, где победитель дороже сравнимого КП, нет.</p>")

    a("<h2>7. Как посчитано</h2>")
    why = meta.get("why") or {}
    a(f"<p>Сделки — {t['n']} последних по первому входу в воронку реализации после предпродажной воронки, "
      f"кроме проигранных; в том же окне пропущено проигранных после входа — {meta['skipped'].get('lost', 0)}, "
      f"заведённых сразу в реализацию — {meta['skipped'].get('direct', 0)}. Продажа и закупка — строки вкладки "
      f"«Реализация» на день сборки (файл экономики первым). Новый поставщик — без заказа СП-172 и без "
      f"выбранного запроса по другим сделкам до первого запроса ему по этой сделке; заказы СП-172 в портале — "
      f"с {meta.get('ordersSince') or '—'}. Цены — разобранные КП поставщиков из карточек СП-166 сделок: строк "
      f"{meta.get('priceRows', 0)}, взято {meta.get('offers', 0)}; не взято — "
      + "; ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda kv: -kv[1]))
      + f". Позиция — код из строки КП; сравнимы цены одного базиса (или без базиса хотя бы у одной) и не "
      f"дальше чем в {ПОРОГИ['раз']:g} раза: отброшено пар по базису {int(t.get('basisSkip') or 0)}, по "
      f"разнице цен {int(t.get('ratioSkip') or 0)}. Деньги позиции — сумма строки КП победителя, цена лота на "
      f"количество не умножается. Курсы ЦБ: {meta.get('fxSrc') or 'не получены'}.</p>")
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
    ap = argparse.ArgumentParser(description="Деньги сорсинга: PDF-ведомость по последним сделкам реализации")
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
    from probe_conversion import читать_историю
    ids = sorted({r["deal"] for r in cand}, key=int)
    hist: dict[str, list] = {}
    fails = 0
    for part in _parts(ids, a.parts):
        h, err = читать_историю(client, part)
        if err:
            fails += 1
            print(f"  часть истории не прочитана: {err}")
            continue
        hist.update(h)
    if fails:
        print(f"::error::частей истории стадий не прочитано: {fails} — выборка была бы неполной")
        return 1
    entries = {d: realization_entry(hist.get(d, []), set(people.TECH_CATS)) for d in ids}
    kinds = Counter(k for _, k in entries.values())
    print("вход в реализацию: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    deals, skipped = pick(cand, entries, a.n)
    print(f"выбрано сделок: {len(deals)} из {a.n}; в окне пропущено: проигранных {skipped['lost']}, "
          f"заведённых сразу в реализацию {skipped['direct']}")
    if len(deals) < a.n:
        print(f"::warning::сделок меньше запрошенного: {len(deals)}")
    did_list = [r["deal"] for r in deals]

    # карточки СП-166 и заказы СП-172 сделок — частями по сделкам
    card_sel = ["id", "companyId", "stageId", "createdTime", "parentId2", "assignedById", "createdBy",
                config.RFQ_OFFER_FIELD]
    cards, orders = [], []
    for part in _parts(did_list, a.parts):
        cards += read_items(client, config.SPA_ENTITY_TYPE_ID, "parentId2", part, card_sel)
        orders += read_items(client, 172, "parentId2", part,
                             ["id", "companyId", "parentId2", "opportunity", "currencyId", "stageId", "createdTime"])
    for c in cards:
        c["_bucket"] = stages.classify_stage(str(c.get("stageId") or ""))
        c["_offer"] = bool(c.get(config.RFQ_OFFER_FIELD))
    print(f"карточек СП-166: {len(cards)}; заказов СП-172: {len(orders)}")
    cards_by_deal: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        cards_by_deal[str(c.get("parentId2") or "")].append(c)

    # история поставщиков: заказы и выбранные запросы по всем сделкам
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
    fi = ((first.get("result") or {}).get("items") or [{}])[0]
    orders_since = day(fi.get("createdTime"))
    orders_hist: dict[str, list[dict]] = defaultdict(list)
    for o in oh:
        orders_hist[str(o.get("companyId"))].append(o)
    sel_hist: dict[str, list[dict]] = defaultdict(list)
    for c in sh:
        sel_hist[str(c.get("companyId"))].append(c)
    new_by_deal: dict[str, set[str]] = {}
    for r in deals:
        did = r["deal"]
        fc = first_contact(cards_by_deal.get(did, []), r["_entry"])
        ds = {str(c.get("companyId")) for c in cards_by_deal.get(did, [])} | \
             {str(o.get("companyId")) for o in orders if str(o.get("parentId2") or "") == did}
        new_by_deal[did] = {s for s in ds if s not in ("", "0", "None")
                            and is_new(s, did, fc.get(s, r["_entry"]), orders_hist, sel_hist, osem)}
    print(f"поставщиков в сделках: {len(sups)}; заказов этих поставщиков в истории: {len(oh)}; "
          f"выбранных запросов: {len(sh)}; заказы СП-172 в портале с {orders_since or '—'}")

    sourcer_f = config.DEAL_SOURCER_FIELDS[0][0]
    dd = client.deals_by_ids(did_list, select=["ID", sourcer_f])
    users = client.users()
    sourcer = {d: str(x.get(sourcer_f)) for d, x in dd.items() if str(x.get(sourcer_f) or "0") != "0"}
    service = config.service_accounts(users)
    print(f"сделок с полем «Сорсер»: {len(sourcer)} из {len(did_list)}")

    # цены разобранных КП
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

    rep = build(deals, cards_by_deal, orders, osem, new_by_deal, offers, sourcer, fx,
                service=service, pnames=users)
    import premia as premia_mod
    prem, cov = premium(data.get("premia"), set(did_list), premia_mod.K_DEFAULT)
    ppl = people_rows(rep["people"], prem, users, cov.get("eurRub"))
    print(f"люди: вели запросы {sum(1 for x in ppl if x['cards'])}, с премией по положению "
          f"{sum(1 for x in ppl if x['prize'])}; строк премии у сделок выборки {cov['rows']} из {len(did_list)}; "
          f"запросов робота без сорсера {(rep['people'].get(ROBOT) or {}).get('cards', 0)}")
    t = totals(rep["rows"])
    names = client.companies_by_ids({x["sup"] for x in rep["over"][:TOP_ROWS] + rep["e3"][:TOP_ROWS]})
    # журнал — только счётчики и доли (правило 17)
    buy = t.get("buy") or 0.0
    print(f"сделок с запросами: {int(t.get('nCards') or 0)}; с позициями под сравнением: {int(t.get('nComp') or 0)}; "
          f"позиций: {int(t.get('pos') or 0)}, у победителя {int(t.get('posWin') or 0)}, с конкурентом "
          f"{int(t.get('posComp') or 0)}; доля закупки под сравнением "
          f"{(t.get('baseComp') or 0) / buy * 100 if buy else 0:.1f} %; новых победителей {int(t.get('newWinners') or 0)} "
          f"из {int(t.get('winners') or 0)}; позиций Э3 {int(t.get('e3n') or 0)}; выбрано дороже {int(t.get('overN') or 0)}")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"today": today, "skipped": skipped, "ordersSince": orders_since.strftime("%d.%m.%Y") if orders_since else "",
            "priceRows": len(prices), "offers": len(offers), "why": dict(why),
            "fxSrc": {"cbr": "ряды cbr.ru", "mirror": "зеркало ЦБ"}.get(info.get("src") or "", ""),
            "people": ppl, "k": premia_mod.K_DEFAULT, "premCov": cov, "eurRub": cov.get("eurRub")}
    (out / "doc.html").write_text(render(rep, t, meta, names), encoding="utf-8")
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
