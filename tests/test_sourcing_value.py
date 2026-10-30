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


def test_pick_latest_without_lost_and_counts_skipped():
    rows = [{"deal": str(i), "seq": 100 + i, "lost": i == 3} for i in range(1, 6)]
    e = {str(i): (dt.datetime(2026, 9, i, tzinfo=sv.MSK), "moved") for i in range(1, 6)}
    e["5"] = (dt.datetime(2026, 9, 30, tzinfo=sv.MSK), "direct")
    got, skipped = sv.pick(rows, e, 2)
    assert [r["deal"] for r in got] == ["4", "2"]
    assert skipped == {"lost": 1, "direct": 1}


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


def test_premium_by_person_earned_expected_and_big():
    premia = {"params": {"eurRub": 100.0}, "deals": [
        {"id": "1", "st": "d", "p1": 1000, "fl": [], "parts": [["5", "ex", 0.35], ["6", "kam", 0.65]]},
        {"id": "2", "st": "r", "p1": 2000, "fl": [], "parts": [["5", "ex", 0.35], ["", "kam", 0.65]]},
        {"id": "3", "st": "r", "p1": 9000, "fl": ["big"], "parts": [["5", "ex", 0.35]]},
        {"id": "4", "st": "d", "p1": 500, "fl": [], "parts": [["5", "ex", 0.35]]},   # не из выборки
    ]}
    out, cov = sv.premium(premia, {"1", "2", "3"}, 0.6)
    assert cov == {"rows": 3, "big": 1, "eurRub": 100.0}
    assert round(out["5"]["earned"]) == 210 and round(out["5"]["expected"]) == 420
    assert out["6"]["roles"] == {"kam"} and round(out[""]["expected"]) == 780


def test_build_totals_people_and_render():
    deals = [_deal("1", 901, 2000, 1000, 10), _deal("2", 902, 500, 0, 11)]
    cards = {"1": [{"id": 11, "companyId": "N", "parentId2": "1", "assignedById": 1, "_bucket": "selected", "_offer": True},
                   {"id": 12, "companyId": "O", "parentId2": "1", "assignedById": 8, "_bucket": "notsel", "_offer": True}]}
    orders = [{"parentId2": "1", "companyId": "N", "stageId": "DT172_26:NEW", "opportunity": 1000,
               "currencyId": "EUR", "createdTime": "2026-09-12T00:00:00+03:00"}]
    offers = [off("O", "K1", 150, 1500, i=1, deal="1"), off("N", "K1", 100, 1000, i=2, deal="1")]
    offers[0]["card"], offers[1]["card"] = "12", "11"
    pn = {"77": "Сорсер А", "8": "Сорсер Б"}
    # карточка 11 — робота (1): засчитывается сорсеру сделки 77
    rep = sv.build(deals, cards, orders, {"DT172_26:NEW": "P"}, {"1": {"N"}}, offers, {"1": "77"}, FX(),
                   service={"1"}, pnames=pn)
    t = sv.totals(rep["rows"])
    assert t["n"] == 2 and t["sale"] == 2500 and t["nMargin"] == 1 and t["margin"] == 1000
    assert t["nNew"] == 1 and t["buyNew"] == 1000 and round(t["e3"]) == 500 and t["nCards"] == 1
    a = rep["people"]["77"]
    assert a["cards"] == 1 and a["newFound"] == 1 and round(a["e3"]) == 500 and round(a["es"]) == 500
    assert rep["people"]["8"]["es"] == 0 and rep["people"]["8"]["cards"] == 1
    prem, cov = sv.premium({"params": {"eurRub": 100.0}, "deals": [
        {"id": "1", "st": "d", "p1": 10000, "fl": [], "parts": [["9", "ex", 0.35], ["8", "kam", 0.65]]}]}, {"1", "2"}, 0.6)
    ppl = sv.people_rows(rep["people"], prem, {**pn, "9": "Исполнитель В"}, 100.0)
    assert ppl[0]["name"] == "Сорсер А" and ppl[0]["prize"] == 0
    b = next(x for x in ppl if x["uid"] == "8")
    assert b["roles"] == "вёл запросы, КАМ" and round(b["earned"]) == 3900
    meta = {"today": dt.date(2026, 10, 30), "skipped": {"lost": 0, "direct": 0}, "ordersSince": "01.06.2024",
            "priceRows": 2, "offers": 2, "why": {}, "fxSrc": "ряды cbr.ru", "people": ppl, "k": 0.6,
            "premCov": cov, "eurRub": 100.0}
    h = sv.render(rep, t, meta, {"N": "Новый поставщик"})
    assert "901" in h and "902" in h and "Сорсер А" in h and "Исполнитель В" in h and "Новый поставщик" in h
    # стиль ведомости: ни жирного, ни плашек (навык tkp-vedomost)
    assert "<b>" not in h and "<strong" not in h and h.count("background") == 1
    pr = sv.proposals(t, meta)
    assert any("положение не оплачивает" in x for x in pr)
