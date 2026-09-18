"""Замер «уверенность против собственной проверки»: правило отбора должно быть строгим.

Находка родилась из одной строки: у 1701/05 стоит уверенность «A», основание
ссылается на страницу поиска по ДРУГОМУ номеру, а проверка того же файла по
этому же номеру говорит «dead_link, цену не увидел ни одну». Замер показал, что
случай не единичный. Тест держит границы находки, чтобы она не раздулась:
вердикт `confirmed` дефектом не считается, уверенность C в отбор не идёт, а
классы считаются раздельно — цена ошибки у них разная.

Корпуса придуманные (правило 18).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import confidence_audit as ca  # noqa: E402

SRC = ROOT / "gt/data/ship_confidence.json"


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("набора нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_числа_набора_совпадают_с_замером():
    m = ca.measure()
    d = doc()
    assert d["totals"] == m["totals"]
    assert d["classes"] == m["classes"]


def test_вердикты_только_из_закрытого_списка():
    for x in doc()["items"]:
        assert x["verdict"] in ca.DEFECTS, f"{x['pn']}: вердикт вне закрытого списка"


def test_подтверждённая_проверка_не_считается_дефектом():
    """Иначе находка раздувается на строки, где цена как раз подтверждена."""
    assert "confirmed" not in ca.DEFECTS
    for x in doc()["items"]:
        assert x["verdict"] != "confirmed"


def test_в_отбор_идёт_только_сильная_уверенность():
    """Уверенность C сама говорит «это оценка» — претензии к ней нет."""
    assert ca.STRONG == {"A", "B"}
    for x in doc()["items"]:
        assert x["conf"] in ca.STRONG, f"{x['pn']}: уверенность {x['conf']} не должна попадать"


def test_у_каждого_класса_своя_расшифровка():
    """Складывать классы одной суммой нельзя: цена ошибки у них разная."""
    d = doc()
    assert set(d["classes"]) == set(ca.DEFECTS)
    for v, c in d["classes"].items():
        assert c["means"] == ca.DEFECTS[v]
        assert c["rows"] >= 0 and c["exposure"] >= 0
    assert sum(c["rows"] for c in d["classes"].values()) == d["totals"]["rows"]
    assert sum(c["exposure"] for c in d["classes"].values()) == d["totals"]["exposure"]


def test_доля_считается_от_всей_экспозиции():
    t = doc()["totals"]
    assert 0 < t["exposure"] <= t["exposure_total"]
    assert abs(t["share_pct"] - round(t["exposure"] / t["exposure_total"] * 100, 1)) < 0.05


def test_у_каждой_находки_есть_примечание_проверки():
    """Без примечания находка не действие, а упрёк набору."""
    bad = [x["pn"] for x in doc()["items"] if not (x.get("note") or "").strip()]
    assert not bad, f"находки без примечания проверки: {bad}"


def test_расшифровка_класса_не_обещает_большего_чем_есть():
    """«Цена расходится» не значит «цены нет»: уровень может быть верным."""
    assert "уровень может быть верен" in ca.DEFECTS["price_differs"]
    assert "не видел" in ca.DEFECTS["dead_link"]
