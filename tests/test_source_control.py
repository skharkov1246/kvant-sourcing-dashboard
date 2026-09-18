"""Отрицательное утверждение источника — сведение только при контроле.

Написано 18.09.2026, когда выяснилось: продавец, на которого ссылаются 27 строк
нашей перепроверки, отдаёт полноценную страницу на ЛЮБУЮ строку в адресе.
Проверено выдуманным номером: 47 КБ, заголовок из этого самого номера, надпись
«Out of Stock». Значит его «нет в наличии» — ответ по умолчанию.

Здесь закреплены три правила, каждое оплачено разбором.
Первое: один контроль без второго бессмыслен — страница поиска отдаёт 200 и на
существующий номер, и на выдуманный, и по одному коду ответа судить нельзя;
так контроль едва не обвинил честного продавца.
Второе: «доступ закрыт» (403, 429, обрыв) контролем не является — это разница в
защите сайта, а не в том, различает ли он номера.
Третье: провал контроля обесценивает ОТРИЦАТЕЛЬНЫЕ утверждения источника, но не
положительные: «In Stock» по умолчанию не пишется.

Корпус придуман, сеть не трогается.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_source_control.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def page(http=200, size=1000, says=(), pn="x"):
    return {"pn": pn, "http": http, "bytes": size, "echoes_number": True,
            "says_about_stock": list(says), "money_on_page": []}


def verdict(fake, real):
    import source_control as sc

    calls = iter([fake, real])
    orig = sc.one
    sc.one = lambda tpl, pn: next(calls)
    try:
        return sc.probe("example.com", "https://example.com/{pn}", "REAL-1")
    finally:
        sc.one = orig


def test_одинаковый_ответ_на_выдуманный_и_настоящий_номер_проваливает_контроль():
    v = verdict(page(200, 47421, ["out of stock"]), page(200, 46859, ["in stock"]))
    assert v["passes"] is False
    assert "по умолчанию" in v["what_it_means"]


def test_разный_код_ответа_проходит_контроль():
    v = verdict(page(404, 2562197, ["not found"]), page(200, 2902340, ["in stock"]))
    assert v["passes"] is True


def test_прямое_ничего_не_найдено_проходит_контроль():
    """Даже при одинаковом коде: сайт сам сказал, что не нашёл."""
    v = verdict(page(404, 100, ["no results"]), page(404, 100, ["no results"]))
    assert v["passes"] is True


def test_закрытый_доступ_контролем_не_является():
    for code in (0, 403, 429, 503):
        v = verdict(page(200, 1000), page(code, 50))
        assert v["passes"] is None, code
        assert "не состоялся" in v["what_it_means"]


def test_замер_записан_и_называет_провалившихся():
    if not SRC.exists():
        pytest.skip("замера нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    assert d["fake_number"]
    assert d["sellers"]
    for s in d["sellers"]:
        assert s["passes"] in (True, False, None)
        assert s["what_it_means"]
        # У каждого продавца должны стоять ОБА контроля: без второго вывод неверен.
        assert s["control_fake"]["pn"] in (d["fake_number"], d["fake_number_numeric"])
        assert s["control_real"]["pn"] != d["fake_number"]
