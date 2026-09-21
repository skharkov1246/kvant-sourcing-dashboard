"""Сверка с каталогом изготовителя: ключ без разделителей и никаких цен.

Написано 18.09.2026. Магазин изготовителя считали закрытым источником целиком,
потому что цену он отдаёт после входа. Но карта его сайта открыта, и по ней
видно, существует ли номер и какой у карточки адрес. Поймать надо две вещи:
ключ сверки (в каталоге номер записан без разделителей, и именно поэтому поиск
нашим написанием годами возвращал пустоту) и то, что в набор не попадает ни
одной цены — магазин их в карте не публикует, и выдумывать их нельзя.

Корпуса придуманы (правило 18 CLAUDE.md); форма адреса взята с живой карты.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import solar_catalog as SC  # noqa: E402

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?><urlset>
<url><loc>https://shop.solarturbines.com/ShopSolar/product/99001231/01tUm00000AAAAAIAA</loc></url>
<url><loc>https://shop.solarturbines.com/ShopSolar/product/9900124c1/01tUm00000BBBBBIAB</loc></url>
<url><loc>https://shop.solarturbines.com/ShopSolar/category/valves/0ZGUm00000CCCCCIAC</loc></url>
</urlset>"""


def _cat(tmp_path: Path):
    f = tmp_path / "sitemap-product-1.xml"
    f.write_text(SITEMAP, encoding="utf-8")
    return SC.catalog([f])


def test_ключ_сверки_снимает_разделители(tmp_path):
    """«990012-31» в заявке и «99001231» в каталоге — один и тот же номер.

    Это и есть то, из-за чего сверка не работала: разделитель у нас есть, у
    изготовителя его нет.
    """
    cat = _cat(tmp_path)
    assert SC.key("990012-31") in cat
    assert SC.key("990012 31") in cat
    assert cat[SC.key("990012-31")][0] == "99001231", "каталожное написание сохраняется как есть"


def test_адреса_не_товаров_в_каталог_не_идут(tmp_path):
    """Раздел каталога — не карточка детали, и номером его считать нельзя."""
    cat = _cat(tmp_path)
    assert len(cat) == 2, "категория попала в товарные адреса"
    assert not any("valves" in v[0] for v in cat.values())


def test_в_набор_не_попадает_ни_одной_цены(tmp_path):
    """Карта сайта цен не содержит — значит их нет и в наборе.

    Проверка формальная намеренно: стоит кому-нибудь дописать сюда «цену из
    карточки», и набор станет коммерческими данными контрагента в публичном
    репозитории.
    """
    cat = _cat(tmp_path)
    ask = [{"pn": "990012-31", "qty": 3, "usd_lo": 1, "usd_hi": 2, "man": "Solar"},
           {"pn": "ZZ-0001", "qty": 1, "man": "Solar"}]
    m = SC.measure(cat, ask, set())
    text = json.dumps(m, ensure_ascii=False)
    for word in ("price", "цена за", "usd_per", "стоимость"):
        assert word not in text.lower()
    assert not re.search(r'"(price|price_low|price_high|usd)"\s*:', text)


def test_ненайденный_номер_изготовителя_считается_отдельно(tmp_path):
    """«Номера нет в каталоге изготовителя» — измерение, а не пропуск.

    Такая строка закрывается вопросом заказчику: это либо чертёжное
    обозначение, либо сборка, которую изготовитель отдельно не продаёт.
    """
    cat = _cat(tmp_path)
    ask = [{"pn": "990012-31", "qty": 3, "man": "Solar"},
           {"pn": "ZZ-0001", "qty": 1, "man": "Solar"},
           {"pn": "ZZ-0002", "qty": 1, "man": "Cummins"}]
    m = SC.measure(cat, ask, set())
    assert m["matched"] == 1
    assert m["solar_rows"] == 2 and m["solar_matched"] == 1
    assert m["solar_missing"] == 1, "чужой изготовитель в этот счёт попадать не должен"
    assert [r["pn"] for r in m["solar_missing_rows"]] == ["ZZ-0001"]


def test_набор_репозитория_согласован_с_замером():
    """Записанный набор обязан сходиться сам с собой.

    Иначе в отчёт уйдёт число из заголовка, не совпадающее со списком, — ровно
    та ошибка, из-за которой в этом репозитории появилось правило «итог
    пересчитывается, цитата нет».
    """
    p = ROOT / "gt/data/solar_catalog_match.json"
    if not p.exists():
        pytest.skip("набора нет")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["matched"] == len(d["rows"])
    assert d["solar_missing"] == len(d["solar_missing_rows"])
    assert d["solar_matched"] <= d["solar_rows"]
    assert d["spelling_differs"] <= d["matched"]
    assert all(r["url"].startswith("https://shop.solarturbines.com/") for r in d["rows"])
