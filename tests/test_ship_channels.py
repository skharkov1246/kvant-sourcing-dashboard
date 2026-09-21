"""Карта каналов не должна врать: числа сверяются с заявкой, бренды не пересекаются.

Проверка появилась после конкретной ошибки: сумма экспозиции по брендам в карте
дала 9 955 456 USD при всей экспозиции заявки 9 277 410. Причина — двойной счёт:
Bently Nevada, Allen-Bradley, Pepperl+Fuchs и Det-Tronics стоят в заявке под
шильдиком Solar и Siemens и попадали в две строки карты сразу. Тест держит
правило «одна строка заявки — один бренд» и сверяет числа набора с замером.

Корпуса здесь придуманные (правило 18); живая заявка используется только как
источник тех самых чисел, которые набор и обязан повторять.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import channels  # noqa: E402

STATES = {"прайс открыт", "прайс открыт у дистрибьюторов", "прайс открыт у продавцов",
          "по регистрации", "только запрос", "не проверено"}


def doc() -> dict:
    return json.loads((ROOT / "gt/data/ship_channels.json").read_text(encoding="utf-8"))


def rows() -> list[dict]:
    return json.loads((ROOT / "gt/data/ship_lukoil.json").read_text(encoding="utf-8"))["rows"]


def test_numbers_match_measurement():
    """Числа набора совпадают с замером по заявке — иначе набор устарел."""
    m = channels.measure(rows())
    for item in doc()["brands"]:
        cell = m.get(item["brand"])
        assert cell is not None, f"бренда {item['brand']} нет в замере"
        for key in ("rows", "usd", "no_estimate"):
            assert item[key] == cell[key], (
                f"{item['brand']}.{key}: в наборе {item[key]}, замер {cell[key]}")


def test_every_rule_has_an_entry():
    """Каждый бренд правила описан в карте: замер без канала бесполезен."""
    named = {b["brand"] for b in doc()["brands"]}
    assert named == {n for n, _ in channels.RULES}


def test_exposure_does_not_double_count():
    """Сумма по брендам не превышает всю экспозицию заявки."""
    d = doc()
    total = d["measure"]["exposure_total"]
    assert sum(b["usd"] for b in d["brands"]) == d["measure"]["exposure_mapped"]
    assert d["measure"]["exposure_mapped"] <= total


def test_one_row_one_brand():
    """Строка попадает ровно в один бренд, и сумма строк сходится с заявкой."""
    rs = rows()
    m = channels.measure(rs)
    assert sum(c["rows"] for c in m.values()) == len(rs)


def test_component_brand_beats_nameplate():
    """Компонент под шильдиком OEM уходит своему изготовителю, а не OEM.

    Именно на этом строится вывод карты: магазин Solar не закрывает чужие
    компоненты, которые лежат под его номером.
    """
    under_solar = {"pn": "1794-IE8XT", "man": "Solar", "name": "МОДУЛЬ ВВОДА Солар Taurus 60S"}
    assert channels.brand_of(under_solar) == "Rockwell Automation (Allen-Bradley)"
    own = {"pn": "1019431-1600", "man": "Solar", "name": "КЛАПАН РЕГУЛИРУЮЩИЙ Солар Taurus 70"}
    assert channels.brand_of(own) == "Solar Turbines"


def test_notes_do_not_steal_a_row():
    """Название альтернативы в заметке не меняет бренд строки.

    Иначе строка ушла бы тому, кого я предложил ВЗАМЕН, — и карта показала бы
    канал не по тому изготовителю.
    """
    row = {"pn": "1078695-50", "man": "Solar", "name": "ДАТЧИК ПЛАМЕНИ Солар Taurus 70MD",
           "note": "аналог Pepperl+Fuchs и Allen-Bradley рассматривался",
           "substitute": "Bently Nevada 3500/xx"}
    assert channels.brand_of(row) == "Solar Turbines"


def test_unknown_brand_is_named_not_hidden():
    """Неопознанная строка попадает в явный остаток, а не растворяется."""
    assert channels.brand_of({"pn": "X-1", "man": "Неизвестный завод", "name": "Прокладка"}) \
        == channels.UNMAPPED


def test_states_are_from_closed_list():
    for b in doc()["brands"]:
        assert b["state"] in STATES, f"{b['brand']}: состояние «{b['state']}» вне списка"


def test_checked_field_is_filled_where_state_is_claimed():
    """Если состояние канала заявлено — обязано быть написано, чем оно проверено."""
    for b in doc()["brands"]:
        if b["state"] == "не проверено":
            continue
        assert (b.get("checked") or "").strip(), f"{b['brand']}: состояние без проверки"
        assert (b.get("action") or "").strip(), f"{b['brand']}: канал без действия"


def test_exposure_is_declared_as_our_band():
    """В наборе прямо сказано, что экспозиция — наша вилка, а не цена заказчику."""
    d = doc()
    assert "вилк" in d["measure"]["exposure_is"]
    assert "не цена заказчику" in d["measure"]["exposure_is"]
