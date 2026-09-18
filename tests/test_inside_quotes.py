"""Цену ищут в интернете, когда она уже лежит в почте.

Опись вложений сделок помнит, какие артикулы стоят в каждом присланном
предложении поставщика и сколько там строк с ценой. Сверка 18.09.2026 показала:
номера заявки стоят в этих файлах сотнями, причём среди них две самые дорогие
строки всей заявки, которые отчёт до сих пор относил к «каналу, где цены нет в
принципе».

Правило, закреплённое здесь: короткий чисто числовой ряд сверке не подлежит. Он
совпадает со случайной цифрой таблицы — датой, количеством, суммой, — и такое
совпадение дороже пропуска: оно отправляет исполнителя искать цену, которой в
файле нет.

Корпус придуман.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_inside_quotes.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def test_короткий_числовой_ряд_к_сверке_не_допускается():
    import inside_quotes as iq

    assert not iq.usable(iq.key("10-12"))
    assert not iq.usable(iq.key("2026"))
    assert not iq.usable(iq.key("404"))
    # длинный числовой — можно: это уже похоже на артикул
    assert iq.usable(iq.key("1008364"))
    # короткий, но с буквами — можно
    assert iq.usable(iq.key("MW2231"))


def test_нормализация_снимает_разделители_и_скобки():
    import inside_quotes as iq

    assert iq.key("MW-223 16/B01") == "MW22316B01"
    assert iq.key("1701/15 (Bently Nevada 141379-01)") == "170115"


def test_замер_не_выдаёт_цену_за_адрес():
    """Набор обязан говорить, что цен в нём нет: иначе «нашлось в предложении»
    прочитают как «цена известна»."""
    if not SRC.exists():
        pytest.skip("замера нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    assert "цен здесь нет" in d["what_it_gives"].lower() or \
           "самих цен" in d["what_it_gives"].lower()
    assert d["caveat"]
    for r in d["rows"]:
        assert "price" not in r and "цена" not in json.dumps(r, ensure_ascii=False).lower() \
            or "rows_with_price" in json.dumps(r)


def test_в_выдачу_идут_только_строки_без_нашей_цены():
    if not SRC.exists():
        pytest.skip("замера нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    assert len(d["rows"]) == d["rows_without_our_price"]
    for r in d["rows"]:
        assert r["we_already_have_price"] is False, r["pn"]
        assert r["found_in"], r["pn"]
        for f in r["found_in"]:
            assert (f["rows_with_price"] or 0) > 0, r["pn"]
