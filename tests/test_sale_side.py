"""Продажная сторона: где она лежит и почему её нет числом.

Владелец спросил прямо: какой объём в продаже и какой в закупке. Отчёт отвечал
«не знаем, в каком поле сделки лежит выставленная цена» — верно как признание и
бесполезно как задача. Замер 18.09.2026 показал другое: поле известно, вложение
по нему есть у каждой разобранной сделки, но подавляющее большинство этих
вложений — ОДИН И ТОТ ЖЕ незаполненный образец, приложенный к десяткам сделок.

Правило, которое здесь закреплено: образец отличается от заполненного файла
совпадением размера ДО БАЙТА у файлов с одним именем. Признак грубый, поэтому
он ничего не обвиняет — он лишь отделяет то, что стоит разбирать.

Корпус придуман.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_sale_side.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def fake(tmp_path, rows):
    p = tmp_path / "idx.json"
    p.write_text(json.dumps({"updated": "2026-01-01",
                             "scopes": {"проба": {"deals": 1, "inventory": rows}}},
                            ensure_ascii=False), encoding="utf-8")
    return p


def rec(name, size, status="текст без цен", origin="сделка 1", priced=0):
    return {"origin": origin, "field": "ufCrm_1", "field_name": "Economics of the project",
            "direction": "наша цена", "file_name": name, "size": size,
            "status": status, "priced": priced}


def test_одинаковый_размер_у_одного_имени_это_образец(tmp_path, monkeypatch):
    import sale_side as ss

    monkeypatch.setattr(ss, "SRC", fake(tmp_path, [
        rec("образец.xlsx", 1000, origin="сделка 1"),
        rec("образец.xlsx", 1000, origin="сделка 2"),
        rec("образец.xlsx", 1000, origin="сделка 3"),
    ]))
    m = ss.measure()
    assert m["records_same_file_in_many_deals"] == 3
    assert m["records_own_file"] == 0


def test_разный_размер_у_одного_имени_это_заполненные_файлы(tmp_path, monkeypatch):
    import sale_side as ss

    monkeypatch.setattr(ss, "SRC", fake(tmp_path, [
        rec("result.xlsx", 5059, origin="сделка 1"),
        rec("result.xlsx", 5060, origin="сделка 2"),
    ]))
    m = ss.measure()
    assert m["records_own_file"] == 2
    assert m["records_same_file_in_many_deals"] == 0


def test_единственное_вложение_образцом_не_считается(tmp_path, monkeypatch):
    """Один файл в одной сделке сравнивать не с чем — обвинять его нельзя."""
    import sale_side as ss

    monkeypatch.setattr(ss, "SRC", fake(tmp_path, [rec("один.xlsx", 777)]))
    m = ss.measure()
    assert m["records_same_file_in_many_deals"] == 0
    assert m["records_own_file"] == 1


def test_нечитаемые_вложения_названы_номерами_сделок(tmp_path, monkeypatch):
    import sale_side as ss

    monkeypatch.setattr(ss, "SRC", fake(tmp_path, [
        rec("result.xlsx", 5059, status="не разобрался", origin="сделка 42"),
        rec("result.xlsx", 5060, status="не разобрался", origin="сделка 43"),
    ]))
    m = ss.measure()
    assert m["records_own_file_unparsed"] == 2
    assert m["unparsed_deals"] == ["сделка 42", "сделка 43"]


def test_замер_записан_и_отвечает_на_вопрос_владельца():
    if not SRC.exists():
        pytest.skip("замера нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    assert d["fields"], "поле выставленной цены должно быть названо"
    total = d["records_same_file_in_many_deals"] + d["records_own_file"]
    assert total == d["records_total"], (total, d["records_total"])
    assert d["records_own_file_unparsed"] <= d["records_own_file"]
    assert d["next_step"]
