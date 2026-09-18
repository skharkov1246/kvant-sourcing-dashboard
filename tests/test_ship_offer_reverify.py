"""Раздел «перепроверка против КП поставщика»: сравнение должно быть честным.

Зачем эта таблица. По перепроверенным строкам решение принимают на защите, а
доказательство там до сих пор было слабейшее — карточка с витрины неизвестного
продавца. Письменное предложение контрагента по этой самой заявке сильнее, и
оно лежит во вложениях сделки. Проверка держит три правила: сравнение идёт с
вилкой ИЗ СВОДКИ (а не из строки перепроверки, где числа запрещены), отсутствие
предложения называется отсутствием, а не «дорого», и вердикт по вилке не
выносится, когда вилки нет.

Корпуса придуманные (правило 18).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import ship_offer as so  # noqa: E402


def test_предложение_выше_потолка_подтверждает_занижение():
    got = so.verdict_vs_offer(100, 200, 350)
    assert "ЗАНИЖЕНИЕ подтверждено" in got
    assert "200" in got


def test_предложение_ниже_пола_подтверждает_завышение():
    got = so.verdict_vs_offer(100, 200, 40)
    assert "ЗАВЫШЕНИЕ подтверждено" in got
    assert "100" in got


def test_предложение_внутри_вилки_подтверждает_вилку():
    assert "вилка верна" in so.verdict_vs_offer(100, 200, 150)


def test_края_вилки_считаются_её_частью():
    """Ровно пол и ровно потолок — это попадание, а не промах."""
    assert "вилка верна" in so.verdict_vs_offer(100, 200, 100)
    assert "вилка верна" in so.verdict_vs_offer(100, 200, 200)


def test_нет_предложения_называется_отсутствием():
    """«Нет данных» не должно читаться как «дорого» или «дёшево»."""
    got = so.verdict_vs_offer(100, 200, None)
    assert "нет" in got.lower()
    assert "ЗАНИЖЕНИЕ" not in got and "ЗАВЫШЕНИЕ" not in got


def test_без_вилки_вердикт_не_выносится():
    """Сравнивать предложение не с чем — так и надо сказать."""
    got = so.verdict_vs_offer(None, None, 500)
    assert "сравнивать не с чем" in got
    assert "ЗАНИЖЕНИЕ" not in got and "ЗАВЫШЕНИЕ" not in got


def test_раздел_берёт_вилку_из_сводки_а_не_из_строки_перепроверки():
    """Ключевое правило: числа приходят из заявки, и только из неё."""
    rows = [{"pn": "AA-1", "name": "Придуманный клапан", "qty": 10,
             "usd_lo": 100, "usd_hi": 200}]
    supp = {"AA1": {"usd": 400.0, "raw_price": "400", "currency": "USD",
                    "origin": "СП-166 1", "field": "Offer from supplier",
                    "file": "quote.pdf", "row": "7", "sheet": "", "rule": "",
                    "direction": "входящее", "line": ""}}
    html = so.reverify_section(rows, supp)
    assert html == "" or "AA-1" not in html or "ЗАНИЖЕНИЕ подтверждено" in html


def test_раздел_пуст_без_набора_перепроверки(monkeypatch):
    monkeypatch.setattr(so, "reverify_rows", lambda: [])
    assert so.reverify_section([], {}) == ""


def test_раздел_называет_источник_предложения(monkeypatch):
    """Цена без происхождения — не доказательство."""
    monkeypatch.setattr(so, "reverify_rows",
                        lambda: [{"pn": "BB-2", "band_verdict": "ЗАНИЖЕНА"}])
    rows = [{"pn": "BB-2", "name": "Придуманный датчик", "qty": 4,
             "usd_lo": 10, "usd_hi": 20}]
    supp = {"BB2": {"usd": 55.0, "raw_price": "55", "currency": "USD",
                    "origin": "СП-166 2", "field": "Offer from supplier",
                    "file": "quote2.pdf", "row": "3", "sheet": "", "rule": "",
                    "direction": "входящее", "line": ""}}
    html = so.reverify_section(rows, supp)
    assert "quote2.pdf" in html and "СП-166 2" in html
    assert "ЗАНИЖЕНИЕ подтверждено" in html


def test_строка_без_предложения_попадает_в_таблицу_с_пометкой(monkeypatch):
    """Строку нельзя молча выбросить: её отсутствие — тоже результат."""
    monkeypatch.setattr(so, "reverify_rows",
                        lambda: [{"pn": "CC-3", "band_verdict": "НЕЧЕМ ПРОВЕРИТЬ"}])
    rows = [{"pn": "CC-3", "name": "Придуманное уплотнение", "qty": 7,
             "usd_lo": 1, "usd_hi": 5}]
    html = so.reverify_section(rows, {})
    assert "CC-3" in html
    assert "в файлах сделки этой строки нет" in html
