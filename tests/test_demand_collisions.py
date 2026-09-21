"""Один номер на нескольких строках — это два разных случая, и считать их надо врозь.

Написано вместе с замером 18.09.2026. Сводка заявки складывает строки с одним
номером в одну позицию, и её количество — сумма, а не замер. Пока эти случаи
не разделены, любая цифра по такой строке спорна: «одна позиция, расписанная
по машинам» и «разные изделия с протянутым по столбцу номером» дают
противоположные выводы о количестве и о цене.

Здесь закреплено три правила. Первое: доля экспозиции на повторных вхождениях
не может превышать экспозицию всей заявки — иначе где-то двойной счёт. Второе:
счёт по двум случаям сходится с общим числом. Третье: набор обязан говорить,
чем он НЕ является, иначе его прочитают как список дублей и строки сведут.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_demand_collisions.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("замера нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_счёт_по_двум_случаям_сходится_с_общим():
    d = doc()
    assert d["same_category"]["pns"] + d["diff_category"]["pns"] == d["pns_more_than_once"]
    assert len(d["items"]) == d["pns_more_than_once"]
    s = d["same_category"]["usd_on_repeats"] + d["diff_category"]["usd_on_repeats"]
    assert abs(s - d["usd_on_repeats"]) < 1.0, "деньги по двум случаям не сходятся с итогом"


def test_доля_не_превышает_экспозицию_всей_заявки():
    d = doc()
    assert 0 <= d["usd_on_repeats"] <= d["usd_exposure_all_rows"], (
        "экспозиция на повторных вхождениях больше экспозиции всей заявки — двойной счёт")


def test_каждая_строка_несёт_и_количества_и_категории():
    for it in doc()["items"]:
        assert len(it["qty_by_row"]) == it["rows"] == len(it["categories"]), (
            f'{it["pn"]}: число строк не сходится с числом количеств и категорий')
        assert it["one_category"] == (len({c for c in it["categories"]}) == 1)
        assert it["qty_in_repeats"] == sum(it["qty_by_row"][1:])


def test_набор_говорит_чем_он_не_является():
    t = (doc().get("what_it_is_not") or "").lower()
    assert "не" in t and ("дубл" in t or "сводить" in t or "сводить строки" in t), (
        "набор обязан сказать, что это не список дублей: иначе строки сведут по нему")


def test_разные_категории_под_одним_номером_не_считаются_одной_позицией():
    """Сам замер обязан отделять случай, где номер изделие не опознаёт."""
    import demand_collisions as dc

    rows = [
        {"sheet": "Л", "pn": "A-1", "qty": 2, "cat": "фильтры", "name": "Фильтр"},
        {"sheet": "Л", "pn": "A 1", "qty": 3, "cat": "фильтры", "name": "Фильтр той же машины"},
        {"sheet": "Л", "pn": "B-2", "qty": 4, "cat": "клапаны", "name": "Клапан"},
        {"sheet": "Л", "pn": "B-2", "qty": 5, "cat": "датчики", "name": "Датчик"},
    ]
    import json as _j
    tmp = ROOT / "gt/data/_collisions_test_demand.json"
    keep_d = dc.DEMAND
    keep_s = dc.SUMMARY
    tmp.write_text(_j.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    sm = ROOT / "gt/data/_collisions_test_summary.json"
    sm.write_text(_j.dumps({"rows": [{"pn": "A-1", "qty": 5, "usd_lo": 10, "usd_hi": 20},
                                     {"pn": "B-2", "qty": 9, "usd_lo": 100, "usd_hi": 200}]},
                           ensure_ascii=False), encoding="utf-8")
    try:
        dc.DEMAND, dc.SUMMARY = tmp, sm
        m = dc.measure("Л")
        assert m["pns_more_than_once"] == 2
        assert m["same_category"]["pns"] == 1 and m["diff_category"]["pns"] == 1
        # A-1: экспозиция 5 × 15 = 75, на повтор приходится 3 из 5 → 45
        same = [x for x in m["items"] if x["one_category"]][0]
        assert abs(same["usd_on_repeats"] - 45.0) < 0.01
        # нормализация номера обязана склеить «A-1» и «A 1»
        assert same["rows"] == 2
    finally:
        dc.DEMAND, dc.SUMMARY = keep_d, keep_s
        tmp.unlink(missing_ok=True)
        sm.unlink(missing_ok=True)
