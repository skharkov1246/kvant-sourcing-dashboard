"""Опровержение и переклассификация — разные события, и складывать их нельзя.

Пересуд отказов дал 33 изменения вердикта из 128 строк. Соблазн назвать это
«26 % отказов не выдержали проверки» велик и неверен: 14 изменений — это смена
одного вида отказа другим («нечем проверить» на «не подтверждена»), где вывод тот
же, а уточнилось основание. Денег такая смена не стоит. Настоящих опровержений,
где отказ сменился найденной ценой, — 19, то есть 14,8 %.

Разница вдвое, и она ровно в ту сторону, которая приукрашивает нашу же работу по
пересуду. Поэтому замер их разделяет, а проверка следит, чтобы разделение не
слиплось обратно.

Корпус придуман.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_rejudge_rate.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def test_прежний_вердикт_читается_по_началу_текста():
    import rejudge_rate as rr

    assert rr.head("НЕЧЕМ ПРОВЕРИТЬ: цены нет") == "НЕЧЕМ ПРОВЕРИТЬ"
    assert rr.head("  ЗАНИЖЕНА — в 2,9 раза") == "ЗАНИЖЕНА"
    # вердикт, названный не в начале, вердиктом не считается
    assert rr.head("цена найдена, поэтому ВЕРНА") == ""
    assert rr.head("") == ""


def test_смена_вида_отказа_опровержением_не_считается():
    if not SRC.exists():
        pytest.skip("замера нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    for move in d["moves"]:
        was, _, now = move.partition(" → ")
        if was in ("НЕ ПОДТВЕРЖДЕНА", "НЕЧЕМ ПРОВЕРИТЬ") and \
           now in ("НЕ ПОДТВЕРЖДЕНА", "НЕЧЕМ ПРОВЕРИТЬ"):
            # такой переход должен сидеть в переклассификации, а не в опровержениях
            assert d["reclassified"] > 0, move


def test_части_складываются_в_число_пересуженных():
    """Четыре части, а не три. Четвёртая добавлена 18.09.2026 по замеру: у
    тридцати строк вердикт не изменился, а ЦИФРА появилась — вилки по ним в
    заявке нет, сравнивать не с чем, но число для запроса теперь есть. Без этой
    категории пересуд выглядел бесплодным там, где он дал больше всего."""
    if not SRC.exists():
        pytest.skip("замера нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    if "price_gained_same_verdict" not in d:
        pytest.skip("замер старой сборки")
    total = (d["overturned"] + d["reclassified"] + d["unchanged"]
             + d["price_gained_same_verdict"])
    assert total == d["rejudged"], (total, d["rejudged"])


def test_появившаяся_цифра_опровержением_не_считается():
    if not SRC.exists():
        pytest.skip("замера нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    if "price_gained_same_verdict" not in d:
        pytest.skip("замер старой сборки")
    assert d["price_gained_same_verdict"] > 0
    # опровержения перечислены поимённо и в их числе нет строк с тем же вердиктом
    for r in d["rows"]:
        assert r["was"] != r["now"], r


def test_в_опровержения_попадают_только_ценовые_вердикты():
    if not SRC.exists():
        pytest.skip("замера нет")
    from verdicts import PRICED

    d = json.loads(SRC.read_text(encoding="utf-8"))
    for r in d["rows"]:
        assert r["now"] in PRICED, r
        assert r["was"] in ("НЕ ПОДТВЕРЖДЕНА", "НЕЧЕМ ПРОВЕРИТЬ"), r
