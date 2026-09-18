"""Перечень ЗИП даёт опознание, но не цену и не остаток.

Замер написан 18.09.2026, когда снятые целиком открытые перечни закрыли 207
номеров заявки Solar из 497 — за один проход столько, сколько построчная
разведка даёт за неделю. Ценность там ровно одна: дословное наименование по
номеру и адрес источника. Цены в таких перечнях нет вовсе, вместо неё стоит
«запросить».

Здесь закреплено три правила, каждое из которых уже нарушалось в соседних
наборах. Первое: два перечня ОДНОГО держателя — один свидетель, и считать
независимость надо по домену, а не по адресу страницы. Второе: набор обязан
говорить, что цены и подтверждённого остатка он не даёт, иначе «in stock»
прочитают как закрытую потребность. Третье: замеры по разным машинам не
складываются — один номер стоит в перечнях по нескольким машинам сразу.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_parts_lists.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("замера нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_независимость_считается_по_держателю_а_не_по_странице():
    import lists_fold as lf

    assert lf.host("https://www.example.com/parts/?page=2") == "example.com"
    assert lf.host("https://shop.example.com/a") == lf.host("http://example.com/b")
    assert lf.host("") == ""


def test_две_страницы_одного_сайта_не_дают_двух_подтверждений(tmp_path, monkeypatch):
    import lists_fold as lf

    summary = tmp_path / "s.json"
    summary.write_text(json.dumps({"rows": [
        {"pn": "AB-1", "name": "Прокладка", "qty": 2, "usd_lo": 10, "usd_hi": 20,
         "verdict": "pn_not_found"}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(lf, "SUMMARY", summary)
    src = {"matches": [
        {"pn_ask": "AB-1", "desc": "GASKET", "url": "https://one.example/parts/?page=1"},
        {"pn_ask": "AB 1", "desc": "GASKET, METALLIC", "url": "https://one.example/parts/?page=9"},
    ], "lists": [], "not_found": 0}
    m = lf.build(src, "Проверка")
    assert m["pns_matched"] == 1, "нормализация номера не склеила «AB-1» и «AB 1»"
    it = m["items"][0]
    assert it["independent_sources"] == 1, "две страницы одного сайта дали два свидетеля"
    assert len(it["descriptions"]) == 2, "разные наименования из перечней должны сохраняться оба"
    assert m["pns_confirmed_by_two_or_more"] == 0

    src["matches"].append({"pn_ask": "AB-1", "desc": "GASKET", "url": "https://two.example/x"})
    m2 = lf.build(src, "Проверка")
    assert m2["items"][0]["independent_sources"] == 2
    assert m2["pns_confirmed_by_two_or_more"] == 1


def test_набор_говорит_что_цены_и_остатка_он_не_даёт():
    t = (doc().get("what_it_does_not_give") or "").lower()
    assert "цену" in t, "набор обязан сказать, что цены он не даёт"
    assert "остаток" in t or "не остаток" in t, (
        "набор обязан сказать, что признак наличия здесь не подтверждённый остаток")
    assert "не складыва" in t, "набор обязан сказать, что замеры по машинам не складываются"


def test_счёт_по_машине_сходится():
    for name, m in doc()["machines"].items():
        assert m["pns_matched"] == len(m["items"]), f"{name}: счёт номеров не сходится"
        assert m["pns_confirmed_by_two_or_more"] == sum(
            1 for x in m["items"] if x["independent_sources"] >= 2)
        assert m["pns_that_were_not_found"] == sum(
            1 for x in m["items"] if x["verdict_before"] == "pn_not_found")
        s = sum(x["usd_exposure"] for x in m["items"])
        assert abs(s - m["usd_exposure_matched"]) < 1.0, f"{name}: деньги не сходятся"
