"""Задание на добор цены не должно посылать разведку туда, где цены не будет.

Написано 18.09.2026 по замеру: строк, где канал назван, а цены нет, — 137 на
1,9 млн USD. Но 21 из них на 458 тыс. USD ждёт ответа заказчика, и самая
дорогая — VS-4-57 на 138 000 USD, где под одним обозначением в заявке идут два
разных изделия, а само обозначение — модель котла, а не номер детали. Послать
туда разведку — списать её труд в ноль: продавец вернёт вопрос, а не цену.

Второе правило здесь — то же, что и в rv_brief: ни одно число задания не
набирается руками. Одиннадцать из двенадцати вписанных руками вилок оказались
неверными, а разведчик сверяет найденное именно с вилкой.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def load():
    ask = ROOT / "gt/data/ship_lukoil.json"
    rv = ROOT / "gt/data/ship_reverify.json"
    if not ask.exists() or not rv.exists():
        pytest.skip("наборов нет")
    import rv_pricehunt as ph

    rows = json.loads(ask.read_text(encoding="utf-8"))["rows"]
    rvx = {ph.key(r["pn"]): r
           for r in json.loads(rv.read_text(encoding="utf-8"))["rows"]}
    return ph, rows, rvx


def test_в_отбор_не_попадает_строка_с_уже_найденной_ценой():
    ph, rows, rvx = load()
    for _e, r, x in ph.candidates(rows, rvx):
        assert not isinstance(x.get("price_low"), (int, float)), r.get("pn")


def test_в_отбор_не_попадает_строка_без_канала():
    ph, rows, rvx = load()
    for _e, r, x in ph.candidates(rows, rvx):
        assert str(x.get("channel") or "").strip(), r.get("pn")


def test_квотируемый_канал_в_отбор_не_попадает():
    """Там цену не публикуют в принципе — такая строка закрывается письмом."""
    ph, rows, rvx = load()
    from ship_coverage import quote_only

    for _e, r, x in ph.candidates(rows, rvx):
        assert not quote_only(x), r.get("pn")


def test_строки_ждущие_ответа_заказчика_отсеиваются():
    ph, rows, rvx = load()
    asked = ph.asked_keys()
    assert asked, "список вопросов заказчику пуст — отсев работать не будет"
    sel = ph.candidates(rows, rvx)
    free = [t for t in sel if ph.key(t[1].get("pn")) not in asked]
    assert len(free) < len(sel), "ни одна строка не отсеяна — проверь разбор pn вопроса"


def test_задание_печатает_вилку_из_данных_а_не_из_памяти():
    ph, rows, rvx = load()
    e, r, x = ph.candidates(rows, rvx)[0]
    text = ph.brief(1, e, r, x)
    assert str(r.get("pn")) in text
    if r.get("usd_lo") not in (None, ""):
        assert f"{r['usd_lo']}–{r['usd_hi']} USD" in text
    assert str(r.get("qty")) in text


def test_уже_доборанная_строка_второй_раз_не_выдаётся():
    """Отрицательный результат добора — это измерение, а не пустота.

    Разведка записывает, какие именно страницы закрыты и где номера нет в
    перечне. Выдать такую строку в задание второй раз значит потратить проход
    на уже отвеченный вопрос. Пометку ставит rv_merge.py в режиме --update.
    """
    ph, rows, rvx = load()
    for _e, r, x in ph.candidates(rows, rvx):
        assert not x.get("price_hunt"), r.get("pn")
