"""Тесты вкладки «Премии» (premia.py) — без сети и без Bitrix.

Корпус придуман (правило 18: тестовые данные не копируются из базы); форма строк —
как у people.compute → deals и contracts.compute → rows. Проверяется то, что задаёт
положение о мотивации команды КАМа, ред. 2: пул, доли участников, минимум цены,
убыточная сделка, а также калибровка поправки и каскад источников ПЗ.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import premia  # noqa: E402

EUR_RUB = 100.0


def deal(did, raw, *, buy=0, state="real", kam="1", prod="", own="3", big=False):
    return {"id": str(did), "t": f"Сделка {did}", "cat": "Реализация", "stage": "Договор",
            "raw": raw, "buyRaw": buy, "state": state, "kam": kam, "prod": prod, "own": own,
            "big": big, "idle": 5}


def people(*deals, win=40):
    return {"label": "на 12.10.2026", "deals": list(deals),
            "roles": {"kam": {"totals": {"winRate": win}}},
            "names": {"1": ["КАМ Первый", True], "2": ["Продукт Второй", True], "3": ["Исполнитель Третий", False]}}


def m0(rev, buy):
    """Результат до поправки по спецификации модуля: ПЗ = заказы × 1,10."""
    return rev - buy * (1 + premia.PZ_OVER) - premia.FIN * rev - premia.PAY_AGENT * buy


def rows_by_id(out):
    return {r["id"]: r for r in out["deals"]}


# ---------------------------------------------------------------- калибровка поправки
def test_поправка_приводит_результат_сделок_с_закупкой_к_уровню_калибровки_положения():
    p = people(deal("A", 10_000, buy=5_000), deal("B", 20_000, buy=9_000))
    out = premia.compute(p, eur_rub=EUR_RUB)
    revs = {"A": 1_000_000, "B": 2_000_000}
    expected_plug = (m0(1_000_000, 500_000) + m0(2_000_000, 900_000)) / 3_000_000 - premia.RHO_TARGET
    assert out["params"]["plug"] == pytest.approx(expected_plug, abs=1e-4)
    rs = rows_by_id(out)
    share = sum(rs[i]["res"] for i in "AB") / sum(revs.values())
    assert share == pytest.approx(premia.RHO_TARGET, abs=1e-5)


def test_поправка_не_бывает_отрицательной():
    # маржа по Битриксу уже ниже уровня калибровки — вычитать нечего
    p = people(deal("A", 10_000, buy=7_900))
    out = premia.compute(p, eur_rub=EUR_RUB)
    assert out["params"]["plug"] == 0.0


# ---------------------------------------------------------------- пул и доли
def test_пул_двадцать_процентов_результата_и_доли_исполнителя_и_кама():
    out = premia.compute(people(deal("A", 10_000, buy=5_000), deal("B", 20_000, buy=9_000)), eur_rub=EUR_RUB)
    for r in out["deals"]:
        assert r["p1"] == pytest.approx(premia.RATE * r["res"], abs=0.2)   # res округлён до рубля
        shares = {role: s for _u, role, s in r["parts"]}
        assert shares == {"ex": pytest.approx(0.35), "kam": pytest.approx(0.65)}
        assert sum(s for *_x, s in r["parts"]) == pytest.approx(1.0)


def test_продукт_оунер_эксперт_получает_пятую_часть_доли_лпр():
    out = premia.compute(people(deal("A", 10_000, buy=5_000, prod="2")), eur_rub=EUR_RUB)
    parts = {(u, role): s for u, role, s in out["deals"][0]["parts"]}
    assert parts[("1", "kam")] == pytest.approx(0.65 * 0.8)
    assert parts[("2", "po")] == pytest.approx(0.65 * 0.2)


def test_продукт_оунер_без_кама_завёл_сделку_а_пятая_часть_не_закреплена():
    out = premia.compute(people(deal("A", 10_000, buy=5_000, kam="", prod="2")), eur_rub=EUR_RUB)
    parts = {(u, role): s for u, role, s in out["deals"][0]["parts"]}
    assert parts[("2", "po")] == pytest.approx(0.65 * 0.8)
    assert parts[("", "kam")] == pytest.approx(0.65 * 0.2)       # видно, что КАМ не закреплён
    assert "nokam" not in out["deals"][0]["fl"]


def test_цена_ниже_минимума_даёт_пул_ноль_но_сделка_видна():
    # поправка нулевая (маржа ниже уровня калибровки), поэтому результат остаётся положительным
    out = premia.compute(people(deal("L", 10_000, buy=7_900)), eur_rub=EUR_RUB)
    r = out["deals"][0]
    assert r["coef"] < premia.PRICE_MIN and r["res"] > 0
    assert r["p1"] == 0 and "low" in r["fl"]


def test_убыточная_сделка_даёт_отрицательную_часть_лпр_и_ноль_исполнителю():
    p = people(deal("A", 10_000, buy=5_000), deal("N", 10_000, buy=9_500))
    r = rows_by_id(premia.compute(p, eur_rub=EUR_RUB))["N"]
    assert r["res"] < 0 and "neg" in r["fl"]
    assert r["p1"] == pytest.approx(premia.LPR_RATE * r["res"], abs=0.2)
    assert [role for _u, role, _s in r["parts"]] == ["kam"]


def test_предел_пула_не_достигается_в_границах_к_поэтому_пул_линеен():
    assert premia.RATE * premia.K_MAX <= premia.CAP


# ---------------------------------------------------------------- источники ПЗ
def test_сделка_без_закупки_оценивается_по_медианному_коэффициенту():
    p = people(deal("A", 10_000, buy=5_000), deal("B", 20_000, buy=9_000),
               deal("C", 5_000, state="presale", prod="2"))
    out = premia.compute(p, eur_rub=EUR_RUB)
    c = rows_by_id(out)["C"]
    assert c["src"] == "est" and "est" in c["fl"] and c["st"] == "p"
    assert c["pz"] == pytest.approx(500_000 / out["params"]["coefMed"], rel=1e-3)   # coefMed округлён


def test_книга_бюджета_берётся_когда_заказов_нет_а_подозрительная_книга_нет():
    p = people(deal("A", 10_000, buy=5_000), deal("K", 10_000), deal("S", 10_000))
    contracts = {"rows": [
        {"deal": "K", "planBreak": {"direct": -6_000, "buy": -5_000}, "planSusp": False},
        {"deal": "S", "planBreak": {"direct": -6_000, "buy": -5_000}, "planSusp": True},
    ]}
    rs = rows_by_id(premia.compute(p, contracts, eur_rub=EUR_RUB))
    assert rs["K"]["src"] == "book" and rs["K"]["pz"] == 600_000
    assert rs["S"]["src"] == "est"


def test_выбросы_не_входят_в_итоги_но_остаются_в_списке():
    p = people(deal("A", 10_000, buy=5_000), deal("X", 1_000_000, buy=500_000, big=True))
    out = premia.compute(p, eur_rub=EUR_RUB)
    a = rows_by_id(out)["A"]
    assert "big" in rows_by_id(out)["X"]["fl"]
    assert out["totals"]["poolReal"] == round(a["p1"] * premia.K_DEFAULT)
    assert out["totals"]["big"] == 1


# ---------------------------------------------------------------- данные на входе
def test_нет_сделок_или_курса_вкладка_пустая():
    assert premia.compute(None, eur_rub=EUR_RUB) is None
    assert premia.compute(people(), eur_rub=EUR_RUB) is None
    assert premia.compute(people(deal("A", 10_000, buy=5_000))) is None


def test_курс_берётся_из_реализации_если_общий_пул_не_собрался():
    out = premia.compute(people(deal("A", 10_000, buy=5_000)), {"fx": {"eurUsd": 1.25, "usdRub": 80.0}})
    assert out["params"]["eurRub"] == pytest.approx(100.0)


def test_имена_и_доля_выигрыша_переходят_в_данные_вкладки():
    out = premia.compute(people(deal("A", 10_000, buy=5_000), win=35), eur_rub=EUR_RUB)
    assert out["names"]["3"] == ["Исполнитель Третий", False]
    assert out["params"]["winRate"] == 35


def test_синтетика_дашборда_даёт_непустую_вкладку():
    from tests import fixture
    out = fixture.build_premia()
    assert out and out["deals"] and out["names"]
    assert out["totals"]["real"] >= 1 and out["totals"]["presale"] >= 1
