"""Замер «один номер против разных деталей» и его согласие с заявкой.

Проверка появилась вместе с самим замером: в заявке «Энергосети» номер VS-6-82
стоит у шести разных изделий, а наша сводка собрана ПО НОМЕРУ — количества
сложились в одну строку и к сумме применилась одна вилка. Такую строку нельзя
ни защищать ценой, ни складывать в закупку, и тест держит именно это: класс
выносится с основанием, а дефектом объявляются только доказанные классы.

Корпуса придуманные (правило 18); живая заявка используется как источник тех
чисел, которые набор обязан повторять.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import collisions  # noqa: E402

SRC = ROOT / "gt/data/ship_collisions.json"


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("набора совпадений нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_числа_набора_совпадают_с_замером():
    assert doc()["totals"] == collisions.measure()["totals"]


def test_разные_категории_узла_это_разные_изделия():
    """Категория узла — доказательство: клапан и датчик не одно изделие."""
    cls, why = collisions.classify(["Клапан BOV", "Датчик положения"],
                                   {"арматура", "датчики и КИП"})
    assert cls == collisions.CLS_DIFF
    assert "категор" in why


def test_противоположные_исполнения_выделены_отдельно():
    """Первичный против вторичного — вопрос заказчику, а не наш вердикт."""
    cls, why = collisions.classify(
        ["Первичный запорный клапан газового топлива", "Вторичный запорный клапан газового топлива"],
        {"арматура"})
    assert cls == collisions.CLS_OPP
    assert "первичн" in why


def test_одна_позиция_разными_словами_не_дефект():
    """Усечённое наименование той же детали дефектом объявлять нельзя."""
    cls, _ = collisions.classify(
        ["Блок развязки Солар Taurus 60S", "Блок развязки Солар Taurus 70"], {"электрика"})
    assert cls == collisions.CLS_WORDING


def test_у_каждой_находки_есть_основание():
    for cell in doc()["classes"].values():
        for item in cell["items"]:
            assert (item.get("reason") or "").strip(), f"{item['pn']}: класс без основания"


def test_дефект_только_из_доказанных_классов():
    """В дефект не попадает класс, который автоматически не разбирается."""
    d = doc()
    cls = d["classes"]
    assert d["totals"]["defect_exposure"] == (
        cls[collisions.CLS_DIFF]["exposure"] + cls[collisions.CLS_OPP]["exposure"])
    assert cls[collisions.CLS_WORDING]["exposure"] > 0
    assert d["totals"]["defect_exposure"] <= d["totals"]["exposure_total"]


def test_каждая_находка_разложена_по_деталям():
    """Без разложения по деталям находка не действие, а упрёк заявке."""
    for cell in doc()["classes"].values():
        for item in cell["items"]:
            assert len(item["parts"]) >= 2, f"{item['pn']}: меньше двух наименований"
            assert all(p.get("qty") is not None for p in item["parts"]), \
                f"{item['pn']}: деталь без количества"


def test_нулевые_количества_посчитаны_отдельно():
    """Позиция с нулевым количеством — дефект самой заявки, и его надо назвать.

    Замер на неё наткнулся: у номера 64/04012024/3 «НАБОР ДЛЯ СЛИВА» стоит 0 шт
    при 16 шт у второго наименования того же номера.
    """
    d = doc()
    assert "zero_qty_parts" in d["totals"], "нулевые количества не посчитаны"
    seen = sum(1 for cell in d["classes"].values() for item in cell["items"]
               for p in item["parts"] if not p["qty"])
    assert d["totals"]["zero_qty_parts"] == seen


def test_количество_в_сводке_равно_сумме_деталей():
    """Главное число находки: сводка сложила количества разных изделий."""
    bad = []
    for cell in doc()["classes"].values():
        for item in cell["items"]:
            s = sum(p["qty"] for p in item["parts"])
            if abs(s - item["qty_in_summary"]) > 0.5:
                bad.append(f"{item['pn']}: сводка {item['qty_in_summary']}, детали {s}")
    assert not bad, ("количество в сводке не равно сумме по деталям — значит сводка собрана "
                     f"не по номеру, и вывод о сложении неверен: {bad[:5]}")


def test_вопросы_дозаписываются_идемпотентно():
    """Повторный прогон не должен плодить дубликаты вопросов.

    Первая версия плодила: метка «__БЕЗ_АРТИКУЛА__» кириллическая, и
    нормализация ключа обнуляла её, поэтому вопрос добавлялся заново каждым
    прогоном. Сверка идёт двумя ключами — нормализованным номером и сырым
    именем.
    """
    m = collisions.measure()
    fresh, skip = collisions.to_questions(m)
    assert not fresh, f"эти вопросы ещё не в наборе: {[x['pn'] for x in fresh]}"
    assert len(skip) == m["totals"]["defect_articles"], (
        f"вопросов по находкам {len(skip)}, а находок {m['totals']['defect_articles']}")


def test_у_дозаписанного_вопроса_есть_и_вопрос_и_основание():
    import json as _json
    doc = _json.loads((ROOT / "gt/data/ship_questions.json").read_text(encoding="utf-8"))
    mine = [q for q in doc["questions"] if q.get("source") == "gt/tools/collisions.py"]
    assert mine, "дозаписанных вопросов нет вовсе"
    for q in mine:
        assert (q.get("ask") or "").strip(), f"{q['pn']}: вопрос пустой"
        assert (q.get("known") or "").strip(), f"{q['pn']}: нет того, что уже установлено"
        assert "арифметика" in (q.get("cost") or ""), (
            f"{q['pn']}: не сказано, что количество в сводке сложено по разным изделиям")


def test_строки_без_артикула_спрашиваются_одним_вопросом():
    """Прочерк в колонке артикула — не повод спрашивать «уточните номер N раз»."""
    import json as _json
    doc = _json.loads((ROOT / "gt/data/ship_questions.json").read_text(encoding="utf-8"))
    labels = [q for q in doc["questions"] if q["pn"] == collisions.NO_PN]
    assert len(labels) == 1, f"вопросов про строки без артикула {len(labels)}, должен быть один"
    assert "прочерк" in labels[0]["ask"]
