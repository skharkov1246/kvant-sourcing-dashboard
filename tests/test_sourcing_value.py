"""Деньги сорсинга (scripts/sourcing_value.py): отбор сделок, новый поставщик, экономия по позициям.

Корпус придуман (CLAUDE.md, правило 18): ни сделок, ни поставщиков, ни цен портала здесь нет.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sourcing_value as sv  # noqa: E402

D = dt.date(2026, 10, 1)


class FX:
    """Курс придуманный: 1 USD = 0,9 €, 1 € = 100 ₽."""
    K = {"EUR": 1.0, "USD": 0.9, "RUB": 0.01}

    def to_eur(self, amount, code, day):
        k = self.K.get(str(code or "").upper())
        return amount * k if (k and amount is not None) else None


def off(sup, key, eur, base, day=D, basis=None, i=0, deal="1"):
    return {"id": i, "deal": deal, "sup": sup, "card": "c", "key": key, "eur": eur, "base": base,
            "baseSrc": "сумма строки", "day": day, "basis": basis}


# ───── вход в реализацию ─────

def test_entry_moved_after_presale():
    h = [("C2:NEW", "2026-09-01T10:00:00+03:00"), ("WON", "2026-09-20T10:00:00+03:00")]
    t, k = sv.realization_entry(h, set())
    assert k == "moved" and t.date() == dt.date(2026, 9, 20)


def test_entry_created_in_cat0_then_presale_then_won():
    # заведена в кат. 0 (воронка по умолчанию), перенесена в предпродажу и выиграна обратно:
    # вход в реализацию — второй вход в кат. 0, а не день заведения
    h = [("NEW", "2026-01-05T10:00:00+03:00"), ("C2:NEW", "2026-01-06T10:00:00+03:00"),
         ("PREPARATION", "2026-08-01T10:00:00+03:00")]
    t, k = sv.realization_entry(h, set())
    assert k == "moved" and t.date() == dt.date(2026, 8, 1)


def test_entry_direct_and_tech_funnel():
    assert sv.realization_entry([("NEW", "2026-05-01T00:00:00+03:00")], set())[1] == "direct"
    # техническая воронка предпродажей не считается
    assert sv.realization_entry([("C22:NEW", "2026-05-01T00:00:00+03:00"),
                                 ("NEW", "2026-05-02T00:00:00+03:00")], {"22"})[1] == "direct"
    assert sv.realization_entry([("C2:NEW", "2026-05-01T00:00:00+03:00")], set()) == (None, "none")


def test_moved_live_latest_first_without_lost_and_direct():
    rows = [{"deal": str(i), "seq": 100 + i, "lost": i == 3} for i in range(1, 6)]
    e = {str(i): (dt.datetime(2026, 9, i, tzinfo=sv.MSK), "moved") for i in range(1, 6)}
    e["5"] = (dt.datetime(2026, 9, 30, tzinfo=sv.MSK), "direct")
    assert [r["deal"] for r in sv.moved_live(rows, e)] == ["4", "2", "1"]


def test_parts_single_split():
    p = sv._parts(list(range(23)), 10)
    assert len(p) == 10 and sum(len(x) for x in p) == 23 and p[0] == [0, 1, 0 + 2][:3]
    assert sv._parts([1, 2], 10) == [[1], [2]]


# ───── новый поставщик ─────

def test_is_new_by_orders_and_selected():
    since = dt.datetime(2026, 9, 1, tzinfo=sv.MSK)
    oh = {"7": [{"parentId2": "9", "stageId": "DT172_26:FAIL", "createdTime": "2026-01-01T00:00:00+03:00"}]}
    osem = {"DT172_26:FAIL": "F", "DT172_26:SUCCESS": "S"}
    assert sv.is_new("7", "1", since, oh, {}, osem)            # проваленный заказ не в счёт
    oh["7"].append({"parentId2": "1", "stageId": "DT172_26:SUCCESS", "createdTime": "2026-01-01T00:00:00+03:00"})
    assert sv.is_new("7", "1", since, oh, {}, osem)            # заказ этой же сделки не в счёт
    oh["7"].append({"parentId2": "8", "stageId": "DT172_26:SUCCESS", "createdTime": "2026-03-01T00:00:00+03:00"})
    assert not sv.is_new("7", "1", since, oh, {}, osem)
    sel = {"5": [{"parentId2": "8", "createdTime": "2026-02-01T00:00:00+03:00"}]}
    assert not sv.is_new("5", "1", since, {}, sel, osem)
    assert sv.is_new("5", "1", dt.datetime(2026, 1, 1, tzinfo=sv.MSK), {}, sel, osem)


# ───── позиции ─────

def test_e1_e2_es_take_larger_not_sum():
    # победитель A: первая цена 120, итоговая 100; конкурент B — 110; деньги позиции 1 000
    offers = [off("A", "K1", 120, 1200, D, i=1), off("A", "K1", 100, 1000, D + dt.timedelta(days=5), i=2),
              off("B", "K1", 110, 1100, D, i=3)]
    r = sv.deal_positions(offers, {"A"}, set())
    assert round(r["e1"]) == 100           # (110 − 100) / 100 × 1 000
    assert round(r["e2"]) == 200           # (120 − 100) / 100 × 1 000
    assert round(r["es"]) == 200           # большее, не сумма
    assert r["posComp"] == 1 and round(r["baseComp"]) == 1000


def test_over_when_winner_not_cheapest():
    offers = [off("A", "K1", 120, 1200, i=1), off("B", "K1", 100, 1000, i=2)]
    r = sv.deal_positions(offers, {"A"}, set())
    assert r["e1"] == 0 and r["overN"] == 1 and round(r["over"]) == 200
    assert abs(r["overList"][0]["pct"] - 0.2) < 1e-9


def test_e3_new_winner_against_old_first_price():
    # прежний O дал 150, потом 130; новый N выиграл со 100; деньги 1 000
    offers = [off("O", "K1", 150, 1500, D, i=1), off("O", "K1", 130, 1300, D + dt.timedelta(days=3), i=2),
              off("N", "K1", 100, 1000, D + dt.timedelta(days=1), i=3)]
    r = sv.deal_positions(offers, {"N"}, {"N"})
    assert round(r["e3"]) == 500 and round(r["e3NewWin"]) == 500 and r["e3n"] == 1
    assert round(r["e1"]) == 300           # против итоговой цены прежнего


def test_e3_old_dropped_after_new_quote():
    # прежний O (победитель) дал 150, после КП нового N (140) снизил до 120
    offers = [off("O", "K1", 150, 1500, D, i=1), off("N", "K1", 140, 1400, D + dt.timedelta(days=2), i=2),
              off("O", "K1", 120, 1200, D + dt.timedelta(days=4), i=3)]
    r = sv.deal_positions(offers, {"O"}, {"N"})
    assert round(r["e3OldDrop"]) == 300    # (150 − 120) / 120 × 1 200
    # снижение ДО КП нового — не заслуга нового
    offers2 = [off("O", "K1", 150, 1500, D, i=1), off("O", "K1", 120, 1200, D + dt.timedelta(days=1), i=2),
               off("N", "K1", 140, 1400, D + dt.timedelta(days=3), i=3)]
    assert sv.deal_positions(offers2, {"O"}, {"N"})["e3"] == 0


def test_basis_and_ratio_guards():
    offers = [off("A", "K1", 100, 1000, basis="EXW", i=1), off("B", "K1", 90, 900, basis="DDP", i=2),
              off("C", "K1", 10, 100, i=3)]
    r = sv.deal_positions(offers, {"A"}, set())
    assert r["basisSkip"] == 1 and r["ratioSkip"] == 1 and r["posSolo"] == 1 and r["e1"] == 0


def test_offer_rows_vat_and_money_base():
    cards = {"11": {"id": 11, "companyId": 5, "parentId2": 1, "createdTime": "2026-09-01T00:00:00+03:00"}}
    prices = [
        # рубли с НДС 22 %: 12 200 ₽ → 10 000 ₽ без НДС → 100 €; сумма строки сходится — база из неё
        {"id": 1, "rfq_id": "11", "part_number": "ab-1234", "price": 12200, "currency": "RUB",
         "qty": 2, "total": 24400, "price_date": "2026-09-02", "note": "цена с НДС 22%"},
        # доллары с НДС — не сравнивается
        {"id": 2, "rfq_id": "11", "part_number": "CD5678", "price": 10, "currency": "USD",
         "qty": 1, "total": None, "price_date": "2026-09-02", "note": "включая НДС"},
        # сумма строки не сходится с ценой × количество — база по цене × количеству
        {"id": 3, "rfq_id": "11", "part_number": "EF9012", "price": 10, "currency": "USD",
         "qty": 3, "total": 999, "price_date": "2026-09-02", "note": ""},
        {"id": 4, "rfq_id": "11", "part_number": "12", "price": 10, "currency": "EUR", "qty": 1},
        {"id": 5, "rfq_id": "99", "part_number": "GH3456", "price": 10, "currency": "EUR", "qty": 1},
    ]
    out, why = sv.offer_rows(prices, cards, FX(), dt.date(2026, 10, 30))
    assert len(out) == 2
    a = next(o for o in out if o["key"] == "AB1234")
    assert round(a["eur"], 2) == 100.0 and round(a["base"], 2) == 200.0 and a["baseSrc"] == "сумма строки"
    e = next(o for o in out if o["key"] == "EF9012")
    assert round(e["base"], 2) == 27.0 and e["baseSrc"] == "цена × количество"
    assert why["с НДС не в рублях"] == 1 and why["нет кода"] == 1 and why["карточка не из выборки"] == 1


# ───── сборка и вёрстка ─────

def _deal(did, seq, sale, buy, entry):
    return {"deal": did, "seq": seq, "title": f"{seq}. Тестовая поставка {seq}", "saleEur": sale, "buyEur": buy,
            "buySrc": "orders", "saleSrc": "file", "saleKnown": True, "customer": "Покупатель",
            "_entry": dt.datetime(2026, 9, entry, tzinfo=sv.MSK)}


def test_card_person_robot_goes_to_deal_sourcer():
    robot = {"1"}
    assert sv.card_person({"assignedById": 5, "createdBy": 1}, "7", robot) == "5"
    assert sv.card_person({"assignedById": 1, "createdBy": 1}, "7", robot) == "7"
    assert sv.card_person({"assignedById": 1, "createdBy": 9}, "", robot) == "9"
    assert sv.card_person({"assignedById": 1, "createdBy": 1}, "", robot) == ""


def _card(cid, sup, uid, created, offer=False, bucket="sent", deal="1"):
    return {"id": cid, "companyId": sup, "parentId2": deal, "assignedById": uid, "createdTime": created,
            "_offer": offer, "_bucket": bucket}


ENTRY = dt.datetime(2026, 9, 10, tzinfo=sv.MSK)
PRE, POST = "2026-09-01T10:00:00+03:00", "2026-09-20T10:00:00+03:00"
KP = {"paw", "selected", "notsel"}


def test_level_by_seniority():
    dept = [_card(1, "W", "7", PRE, offer=True), _card(2, "X", "7", PRE)]
    assert sv.level(dept, {"W": {"7"}}, ENTRY, KP) == "found_pre"
    assert sv.level(dept, {}, ENTRY, KP) == "kp_pre"                       # КП файлом до выигрыша
    assert sv.level([_card(2, "X", "7", PRE, bucket="paw")], {}, ENTRY, KP) == "kp_pre"   # стадия КП
    assert sv.level([_card(2, "X", "7", PRE)], {}, ENTRY, KP) == "ask_pre"
    # победителя нашёл после выигрыша: карточка ему — позже входа в реализацию
    assert sv.level([_card(3, "W", "7", POST)], {"W": {"7"}}, ENTRY, KP) == "found_post"
    assert sv.level([_card(3, "Z", "7", POST)], {}, ENTRY, KP) == "post"
    assert sv.level([], {}, ENTRY, KP) == "none"


def test_found_by_dept_only_dept_cards_to_winner():
    cards = [_card(1, "W", "7", PRE), _card(2, "W", "9", PRE), _card(3, "X", "7", PRE)]
    who = {"1": "7", "2": "9", "3": "7"}
    assert sv.found_by_dept(cards, {"W"}, who, {"7"}) == {"W": {"7"}}
    assert sv.found_by_dept(cards, {"W"}, who, {"8"}) == {}


def _row(did, seq, sale, buy, entry_day):
    return {"deal": did, "seq": seq, "title": f"{seq}. Тестовая поставка {seq}", "saleEur": sale, "buyEur": buy,
            "buySrc": "orders", "saleSrc": "file", "saleKnown": True,
            "_entry": dt.datetime(2026, 9, entry_day, tzinfo=sv.MSK)}


def test_money_per_level_against_control():
    # влияние: сделка 1 — отдел нашёл поставщика до выигрыша (маржа 50 %);
    # контроль: сделки 2–4 без отдела (маржа 20 %)
    sample = [_row("1", 901, 1000, 500, 12)] + [_row(str(i), 900 + i, 1000, 800, 11) for i in (2, 3, 4)]
    cards = {"1": [_card(11, "W", "7", PRE, offer=True, bucket="selected")]}
    orders = [{"parentId2": "1", "companyId": "W", "stageId": "S1", "opportunity": 500, "currencyId": "EUR",
               "createdTime": "2026-09-12T00:00:00+03:00"}]
    rep = sv.build(sample, cards, orders, {"S1": "P"}, {"11": "7"}, {"7"}, {"1": {"W"}}, [], FX(), KP)
    t = sv.totals(rep["rows"])
    lv = t["levels"]
    assert lv["found_pre"]["n"] == 1 and lv["none"]["n"] == 3
    assert abs(t["ctrlRate"] - 0.2) < 1e-9
    assert round(lv["found_pre"]["benefit"]) == 300          # (0,5 − 0,2) × 1 000
    assert round(t["benefit"]) == 300 and round(t["upper"]) == 500
    assert t["nNew"] == 1 and round(t["marginNew"]) == 500
    a = rep["people"]["7"]
    assert a["kpDeals"] == {"1"} and a["foundDeals"] == {"1"} and a["newFound"] == 1 and round(a["margin"]) == 500
    t["months"], t["eurRub"] = 1.0, 100.0
    dr = sv.dept_rows(rep["people"], {"7": {"name": "Сорсер А", "active": True},
                                      "8": {"name": "Сорсер Б", "active": True},
                                      "9": {"name": "Ушедший В", "active": False}})
    assert [x["name"] for x in dr] == ["Сорсер А", "Сорсер Б"]   # ушедший без запросов — не в таблице
    meta = {"today": dt.date(2026, 10, 30), "head": "Руководитель Г", "deptN": 3, "dept": dr, "lost": 0,
            "ordersSince": "01.06.2024", "priceRows": 0, "offers": 0, "why": {}, "fxSrc": "ряды cbr.ru"}
    h = sv.render(rep, t, meta, {"W": "Новый поставщик"}, {"7": "Сорсер А"})
    assert "901" in h and "904" in h and "Новый поставщик" in h and "Руководитель Г" in h
    assert "30 000 ₽" in h                         # польза 300 € × 100 ₽
    assert "<b>" not in h and "<strong" not in h and h.count("background") == 1
    pr = sv.proposals(t, dr)
    assert any(x.startswith("Окупаемость отдела") for x in pr)
    assert any("без единого запроса" in x for x in pr)       # Сорсер Б без запросов


def test_no_estimate_when_control_too_small():
    sample = [_row("1", 901, 1000, 500, 12), _row("2", 902, 1000, 800, 11)]
    cards = {"1": [_card(11, "W", "7", PRE, offer=True)]}
    rep = sv.build(sample, cards, [], {}, {"11": "7"}, {"7"}, {}, [], FX(), KP)
    t = sv.totals(rep["rows"])
    assert t["ctrlN"] == 1 and t["ctrlRate"] is None and t["benefit"] is None and round(t["upper"]) == 500


def test_negative_benefit_says_no_margin_gain():
    # влияние с маржой 10 % против контроля 20 %: прибавки нет, польза — только верхней границей
    sample = [_row("1", 901, 1000, 900, 12)] + [_row(str(i), 900 + i, 1000, 800, 11) for i in (2, 3, 4)]
    cards = {"1": [_card(11, "W", "7", PRE, offer=True)]}
    rep = sv.build(sample, cards, [], {}, {"11": "7"}, {"7"}, {}, [], FX(), KP)
    t = sv.totals(rep["rows"])
    t["months"], t["eurRub"] = 1.0, None
    assert round(t["benefit"]) == -100 and round(t["upper"]) == 100
    assert sv.proposals(t, [])[0].startswith("Прибавки к марже от отдела на этих сделках нет")
    meta = {"today": dt.date(2026, 10, 30), "head": "", "deptN": 1, "dept": [], "lost": 0,
            "ordersSince": "", "priceRows": 0, "offers": 0, "why": {}, "fxSrc": ""}
    assert "Прибавки к марже от отдела" in sv.render(rep, t, meta, {}, {})
