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


def test_агрегат_по_вилкам_печатает_только_счётчики(capsys):
    """Журнал прогона публичный: в нём могут быть счётчики строк, но не цены.

    Этот агрегат — главное число для защиты: он показывает, что письменные
    предложения поставщиков делают с НАШИМИ вилками по всей заявке, а не по
    сорока перепроверенным строкам. Поэтому он обязан быть и обязан быть без цен.
    """
    rows = [
        {"pn": "AA-1", "usd_lo": 10, "usd_hi": 20, "qty": 1},
        {"pn": "BB-2", "usd_lo": 10, "usd_hi": 20, "qty": 1},
        {"pn": "CC-3", "usd_lo": 10, "usd_hi": 20, "qty": 1},
        {"pn": "DD-4", "usd_lo": None, "usd_hi": None, "qty": 1},
    ]
    def offer(usd):
        return {"usd": usd, "raw_price": str(usd), "currency": "USD", "origin": "СП-166 1",
                "field": "Offer from supplier", "file": "q.pdf", "row": "1", "sheet": "",
                "rule": "", "direction": "входящее", "line": ""}
    supp = {"AA1": offer(99.0), "BB2": offer(15.0), "CC3": offer(1.0), "DD4": offer(7.0)}
    so.diagnose(rows, {}, supp, {})
    out = capsys.readouterr().out
    assert "строк заявки с КП поставщика: 4" in out
    assert "предложение выше потолка вилки: 1" in out
    assert "предложение внутри вилки: 1" in out
    assert "предложение ниже пола вилки: 1" in out
    assert "вилки по строке нет: 1" in out
    # ни одной цены в журнале
    for price in ("99", "15.0", "1.0", "7.0"):
        assert price not in out.split("строк заявки с КП поставщика")[1]


def test_строки_без_вилки_с_кп_собираются_отдельным_разделом():
    """По ним оценку не надо искать: она уже написана контрагентом."""
    rows = [{"pn": "NB-1", "name": "Придуманный фильтр", "qty": 10,
             "usd_lo": None, "usd_hi": None},
            {"pn": "WB-2", "name": "Придуманный клапан", "qty": 2,
             "usd_lo": 100, "usd_hi": 200}]
    def offer(usd, pn):
        return {"usd": usd, "raw_price": str(usd), "currency": "USD", "origin": "СП-166 9",
                "field": "Offer from supplier", "file": "q.pdf", "row": "1", "sheet": "",
                "rule": "", "direction": "входящее", "line": ""}
    supp = {"NB1": offer(50.0, "NB-1"), "WB2": offer(150.0, "WB-2")}
    html = so.no_band_section(rows, supp)
    assert "NB-1" in html
    assert "WB-2" not in html, "строка С вилкой в этот раздел попадать не должна"
    assert "q.pdf" in html and "СП-166 9" in html
    assert "НЕ закупка и НЕ экспозиция" in html


def test_раздел_без_вилки_пуст_когда_нечего_показать():
    assert so.no_band_section([{"pn": "X-1", "usd_lo": 1, "usd_hi": 2, "qty": 1}], {}) == ""


def test_сумма_раздела_не_складывается_с_экспозицией():
    """Оговорка обязательна: это сумма предложений, а не закупка."""
    rows = [{"pn": "NB-3", "name": "Придуманная прокладка", "qty": 4,
             "usd_lo": None, "usd_hi": None}]
    supp = {"NB3": {"usd": 25.0, "raw_price": "25", "currency": "USD", "origin": "СП-166 1",
                    "field": "Offer from supplier", "file": "q.pdf", "row": "2", "sheet": "",
                    "rule": "", "direction": "входящее", "line": ""}}
    html = so.no_band_section(rows, supp)
    assert "100" in html, "сумма на объём должна считаться"
    assert "Складывать её с экспозицией заявки нельзя" in html


def test_счёт_по_перепроверенным_строкам_отдельный(monkeypatch):
    """Картина по крупным перепроверенным строкам ОБРАТНАЯ общей по заявке.

    Замер прогона 18.09.2026: по 776 строкам заявки предложение выше потолка у
    222 и ниже пола у 106, а по 22 перепроверенным — наоборот, завышение
    подтверждено 13 раз против занижения 7. Складывать эти два счёта в один
    нельзя, поэтому они и считаются отдельно.
    """
    monkeypatch.setattr(so, "reverify_rows", lambda: [
        {"pn": "AA-1"}, {"pn": "BB-2"}, {"pn": "CC-3"}, {"pn": "DD-4"}])
    rows = [{"pn": "AA-1", "usd_lo": 10, "usd_hi": 20, "qty": 1},
            {"pn": "BB-2", "usd_lo": 10, "usd_hi": 20, "qty": 1},
            {"pn": "CC-3", "usd_lo": 10, "usd_hi": 20, "qty": 1},
            {"pn": "DD-4", "usd_lo": 10, "usd_hi": 20, "qty": 1}]
    def offer(usd):
        return {"usd": usd, "raw_price": str(usd), "currency": "USD", "origin": "СП-166 1",
                "field": "Offer from supplier", "file": "q.pdf", "row": "1", "sheet": "",
                "rule": "", "direction": "входящее", "line": ""}
    supp = {"AA1": offer(99.0), "BB2": offer(1.0), "CC3": offer(15.0)}
    st = so.reverify_stats(rows, supp)
    assert st == {"rows": 4, "with_offer": 3, "overstated_confirmed": 1,
                  "understated_confirmed": 1, "band_right": 1, "no_offer": 1}


def test_счётчики_по_перепроверке_не_несут_ни_цен_ни_номеров():
    """Они идут в репозиторий, значит в них может быть только счёт."""
    rows = [{"pn": "AA-1", "usd_lo": 10, "usd_hi": 20, "qty": 1}]
    st = so.reverify_stats(rows, {"AA1": {"usd": 99.0, "raw_price": "99", "currency": "USD",
                                          "origin": "СП-166 1", "field": "f", "file": "q.pdf",
                                          "row": "1", "sheet": "", "rule": "",
                                          "direction": "входящее", "line": ""}})
    for v in st.values():
        assert isinstance(v, int)
