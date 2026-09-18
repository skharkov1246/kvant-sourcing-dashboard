"""Указатель карточек продавцов: совпадает ХВОСТ адреса, а не что попало.

Написано 18.09.2026. Первая редакция инструмента сверяла отрезок пути целиком и
не нашла НИ ОДНОЙ строки заявки при 396 087 проиндексированных адресов: у
продавца адрес выглядит как «/product/solar-turbines-1013121-1/», то есть перед
номером стоит имя изготовителя. После исправления совпало 401 строка, и все 401
проверены на то, что адрес действительно заканчивается нашим номером.

Корпуса придуманы (правило 18 CLAUDE.md), но форма адресов взята с живых карт.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import seller_index as SI  # noqa: E402

MAP = """<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://example-seller.test/product/solar-turbines-9913121-1/</loc></url>
<url><loc>https://example-seller.test/product/unison-994004/</loc></url>
<url><loc>https://example-seller.test/brands/champion-aerospace/p2/</loc></url>
<url><loc>https://example-seller.test/stock/p7/</loc></url>
</urlset>"""


def _idx(tmp_path: Path):
    f = tmp_path / "example-seller.test__map.xml"
    f.write_text(MAP, encoding="utf-8")
    return SI.index({"example-seller.test": [f]})


def test_номер_находится_в_хвосте_адреса(tmp_path):
    """«solar-turbines-9913121-1» обязан находиться по номеру «9913121-1»."""
    idx, seen = _idx(tmp_path)
    assert SI.key("9913121-1") in idx
    assert idx[SI.key("9913121-1")]["example-seller.test"].endswith("solar-turbines-9913121-1/")
    assert SI.key("994004") in idx


def test_короткий_хвост_номером_не_считается(tmp_path):
    """«p2», «p7» и прочие хвосты страниц не должны становиться номерами.

    Иначе указатель «найдёт» карточку половине заявки и тем обесценит себя.
    """
    idx, _seen = _idx(tmp_path)
    for junk in ("p2", "p7", "1", "10"):
        assert SI.key(junk) not in idx
    assert all(len(k) >= SI.MIN_KEY for k in idx)


def test_совпадение_только_по_хвосту_а_не_по_середине():
    """Номер в СЕРЕДИНЕ чужого адреса совпадением не считается.

    «…/1013121-1-bracket-kit/» — это другое изделие, в котором наш номер лишь
    упомянут. Такой адрес выдавать за адрес по детали нельзя.
    """
    tails = SI.tails("solar-turbines-9913121-1")
    assert SI.key("9913121-1") in tails
    middle = SI.tails("9913121-1-bracket-kit")
    assert SI.key("9913121-1") not in middle


def test_карта_карт_в_адреса_не_попадает(tmp_path):
    """Ссылка на другую карту сайта — не карточка товара."""
    f = tmp_path / "example-seller.test__index.xml"
    f.write_text('<sitemapindex><sitemap><loc>https://example-seller.test/sitemap_2.xml'
                 '</loc></sitemap></sitemapindex>', encoding="utf-8")
    idx, seen = SI.index({"example-seller.test": [f]})
    assert not idx and seen.get("example-seller.test", 0) == 0


def test_набор_репозитория_согласован_и_без_цен():
    """Записанный набор сходится сам с собой, и чисел цены в нём нет.

    В указателе адресов цене места нет: он отвечает на вопрос «кому писать», а
    не «сколько стоит». Поэтому у строки разрешён только закрытый список полей,
    а «была ли цена» — признак, а не число.
    """
    p = ROOT / "gt/data/seller_index.json"
    if not p.exists():
        pytest.skip("набора нет")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["ask_rows_matched"] == len(d["rows"])
    assert d["of_them_had_no_address_before"] <= d["ask_rows_matched"]
    assert sum(h["ask_rows_matched"] for h in d["hosts"]) >= d["ask_rows_matched"]
    # Проверяем ПОЛЯ, а не подстроки: первая редакция искала подстроку «"price»
    # во всём наборе и спотыкалась на собственном поле «had_price», а pytest на
    # сравнении строки в 152 КБ строил объяснение так долго, что это выглядело
    # как зависание. Подстрочная проверка по большому тексту — плохая проверка.
    allowed = {"pn", "qty", "sheet", "cards", "had_contacts", "had_price"}
    for r in d["rows"]:
        assert set(r) <= allowed, (r["pn"], set(r) - allowed)
        assert isinstance(r.get("had_price"), bool), "это признак, а не число"
        assert r["cards"], r["pn"]
        for c in r["cards"]:
            assert set(c) == {"host", "url"}
            assert c["url"].startswith("http")


def test_письмо_оператору_не_несёт_нашей_информации():
    """Наружу не уходят ни вилка, ни экспозиция, ни имя заказчика.

    То же правило, что в остальных пакетах писем: продавцу возвращается только
    его собственная номенклатура и наш вопрос.
    """
    p = ROOT / "gt/data/seller_index.json"
    if not p.exists():
        pytest.skip("набора нет")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["letters"], "письмо не собралось — спросить-то надо"
    for L in d["letters"]:
        body = L["body"]
        for bad in ("ЛУКОЙЛ", "лукойл", "Энергосети", "энергосети", "НВН",
                    "вилка", "экспозиц", "наша цена", "USD", "долл"):
            assert bad not in body, (L["to"], bad)
        for q in ("остаток на складе ЧИСЛОМ", "цену за штуку", "срок действия цены",
                  "базис поставки"):
            assert q in body
        assert L["rows"] == len(L["pns"])
        assert "@" in L["to"] and L.get("address_read_on"), "адрес без прочитанной страницы"


def test_письмо_не_спрашивает_то_что_уже_известно():
    """Строка с найденной ценой в письмо не идёт: по ней спрашивать нечего."""
    p = ROOT / "gt/data/seller_index.json"
    if not p.exists():
        pytest.skip("набора нет")
    d = json.loads(p.read_text(encoding="utf-8"))
    priced = {r["pn"] for r in d["rows"] if r["had_price"]}
    asked = {pn for L in d["letters"] for pn in L["pns"]}
    assert not (priced & asked), sorted(priced & asked)[:5]


def test_проверка_образца_записана_и_пройдена():
    """Совпадение адреса — не последний довод: номер обязан быть НА странице.

    И рядом обязателен контроль выдуманным адресом того же вида: если витрина
    отдаёт карточку и на него, её адреса ничего не значат и указатель надо
    выбросить целиком. Замер 18.09.2026: три карточки из начала, середины и
    конца списка — номер на странице у всех; выдуманный адрес карточки не
    отдаёт.
    """
    p = ROOT / "gt/data/seller_index.json"
    if not p.exists():
        pytest.skip("набора нет")
    v = (json.loads(p.read_text(encoding="utf-8")) or {}).get("verification") or {}
    if not v:
        pytest.skip("набор собран без проверки образца")
    assert v["checked_cards"], "образец пуст — проверка ничего не значит"
    for c in v["checked_cards"]:
        assert c["number_on_page"], c
    assert v["invented_address_returns_that_number"] is False
