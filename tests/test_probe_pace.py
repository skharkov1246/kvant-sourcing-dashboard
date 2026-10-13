"""Зонд темпа: календарь, причины проигрыша, помесячные ряды и доли денег.

Корпус придуман (CLAUDE.md, правило 18): сделки, стадии и даты — не с портала.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from scripts import probe_pace as зонд  # noqa: E402


def test_календарь():
    assert зонд.месяцы("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert зонд.границы("2026-02") == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
    assert зонд.рабочие_дни(dt.date(2026, 10, 5), dt.date(2026, 10, 11)) == 5
    assert зонд.квартал("2026-04") == "2026-Q2"


def test_причина_по_имени_стадии():
    assert зонд.причина("Пост-щик не ответил/не прислал ТКП") == "поставщик не ответил"
    assert зонд.причина("Бюджетирование / Мониторинг цен") == "бюджетный запрос"
    assert зонд.причина("Экономически неинтересно – не выдаем ТКП") == "нам неинтересно"
    assert зонд.причина("Не прошли по цене") == "проиграли после ТКП"
    assert зонд.причина("Отмена Заказчиком закупки") == "отмена заказчиком"
    assert зонд.причина("Что-то иное") == "прочее"


def _строка(id_, created, *, cls="open", offer=None, contract=None, lost=None, reason=None,
            nn=False, company="7", budget=False, strict=None):
    return {"id": id_, "o0": True, "o1": bool(offer), "o2": False, "offer": bool(offer),
            "created": created, "offer_date": offer, "contract_date": contract, "cls": cls,
            "strict": cls == "contract" if strict is None else strict, "lost_date": lost,
            "lost_reason": reason, "closed": lost or contract, "seg_company": nn,
            "company": company, "budget": budget}


def test_ряд_сделок_по_месяцам():
    строки = [
        _строка("1", "2026-03-10", offer="2026-03-20", contract="2026-04-15", cls="contract", nn=True),
        _строка("2", "2026-04-02", offer="2026-04-12", lost="2026-05-03", cls="lost",
                reason="проиграли после ТКП", company="8"),
        _строка("3", "2026-04-05", lost="2026-04-20", cls="lost", reason="поставщик не ответил", company="7"),
        _строка("4", "2026-04-06", company="9"),
    ]
    р = зонд.ряд_сделок(строки, ["2026-03", "2026-04", "2026-05"])
    assert (р["2026-03"]["заявки"], р["2026-04"]["заявки"]) == (1, 3)
    assert р["2026-03"]["заявки НН"] == 1 and р["2026-03"]["новые клиенты"] == 1
    assert р["2026-04"]["новые клиенты"] == 2, "компания 7 впервые — в марте"
    assert (р["2026-03"]["ТКП"], р["2026-04"]["ТКП"]) == (1, 1)
    assert р["2026-04"]["контракты"] == 1 and р["2026-04"]["дн ТКП→контракт мед"] == 26
    assert р["2026-04"]["проиграно до ТКП"] == 1 and р["2026-04"]["причина: поставщик не ответил"] == 1
    assert р["2026-05"]["проиграно после ТКП"] == 1
    # конец марта: открыта 1; конец апреля: 2 и 4 (1 — контракт, 3 — проиграна); конец мая: 4
    assert [р[м]["открыто на конец"] for м in ("2026-03", "2026-04", "2026-05")] == [1, 2, 1]
    assert р["2026-04"]["дн заявка→ТКП мед"] == 10


def test_когорта_зрелая_по_концу_месяца():
    строки = [_строка("1", "2026-01-05", offer="2026-01-10", contract="2026-03-01", cls="contract"),
              _строка("2", "2026-01-06", offer="2026-01-11", lost="2026-02-01", cls="lost")]
    к = зонд.когорты_ткп(строки, ["2026-01", "2026-09"], dt.date(2026, 10, 13))
    assert к["2026-01"] == {"n": 2, "c": 1, "lost": 1, "open": 0, "ripe": True}
    assert к["2026-09"]["ripe"] is False


def test_вехи_реализации_первый_вход():
    мета = {"C4:NEW": {"name": "Новый", "cat": "4", "sem": "P"},
            "PREPARATION": {"name": "Договор с заказчиком подписан | Согласование", "cat": "0", "sem": "P"},
            "UC_1": {"name": "Оплата получена | Закрытие сделки", "cat": "0", "sem": "P"},
            "WON": {"name": "Сделка успешна", "cat": "0", "sem": "S"}}
    история = {"1": [("C4:NEW", "2026-03-01T10:00:00+03:00"), ("PREPARATION", "2026-04-02T10:00:00+03:00"),
                     ("UC_1", "2026-05-02T10:00:00+03:00"), ("PREPARATION", "2026-05-20T10:00:00+03:00"),
                     ("WON", "2026-06-01T10:00:00+03:00")]}
    в, n = зонд.вехи_реализации(история, мета, ["2026-04", "2026-05", "2026-06"])
    assert n == 3
    assert в["2026-04"]["договор подписан"] == 1 and в["2026-05"]["договор подписан"] == 0, "повторный вход не считается"
    assert в["2026-05"]["оплата получена"] == 1 and в["2026-06"]["оплата получена"] == 0


def test_ряд_заказов_и_опоздания():
    заказы = [
        {"created": "2026-04-01", "sem": "S", "deal": "1", "first_live": "2026-04-01", "ship_fact": "2026-05-01",
         "cust_fact": "2026-05-20", "deadline": "2026-05-10", "cust_plan": None},
        {"created": "2026-04-03", "sem": "P", "deal": "1", "first_live": "2026-04-01", "ship_fact": None,
         "cust_fact": None, "deadline": None, "cust_plan": None},
        {"created": "2026-04-05", "sem": "F", "deal": "2", "first_live": None, "ship_fact": "2026-04-20",
         "cust_fact": None, "deadline": None, "cust_plan": None},
    ]
    р = зонд.ряд_заказов(заказы, ["2026-04", "2026-05"])
    assert (р["2026-04"]["заказов"], р["2026-04"]["живых"], р["2026-04"]["провал"]) == (3, 2, 1)
    assert р["2026-04"]["первый заказ сделки"] == 1
    assert р["2026-04"]["отгружено поставщиком"] == 0, "проваленный заказ не отгрузка"
    assert р["2026-05"]["доставлено позже срока"] == 1 and р["2026-05"]["дн заказ→отгрузка мед"] == 30


def test_окно_работы_человека():
    ушёл = {"active": False, "hired": dt.date(2026, 2, 1), "last": dt.date(2026, 5, 10)}
    assert [зонд.в_окне(ушёл, м) for м in ("2026-01", "2026-02", "2026-05", "2026-06")] == [False, True, True, False]
    assert зонд.в_окне({"active": True, "hired": None, "last": None}, "2025-01")


def test_деньги_только_доли_и_порог():
    строки = []
    for i in range(6):
        строки.append({"o0": True, "o1": True, "cls": "contract", "v_contract": 100.0 + i, "contract_date": "2026-04-10",
                       "v_offer": 100.0, "offer_date": "2026-04-01"})
    строки.append({"o0": True, "o1": True, "cls": "contract", "v_contract": 50.0, "contract_date": "2026-01-10",
                   "v_offer": None, "offer_date": "2026-01-01"})
    д = зонд.доли_денег(строки, ["2026-01", "2026-04"])
    assert д["2026-Q1"]["share"] is None, "один контракт — доля не печатается"
    assert д["2026-Q2"]["share"] == 92 and д["2026-Q2"]["n"] == 6
    assert all(isinstance(v, (int, float, type(None))) for q in д.values() for v in q.values())
