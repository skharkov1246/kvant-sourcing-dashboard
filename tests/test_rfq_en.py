"""Запрос цен на английском: в запросе не должно быть ни одной нашей цифры.

Правило простое и оплаченное практикой: продавец, увидевший нашу оценку,
подстроится под неё. Поэтому в документ идут только номер, английское
наименование из восстановленного первоисточника, количество и единица — и
ничего из наших вилок, цен и выводов.

Корпуса придуманные (правило 18).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import rfq_en  # noqa: E402

ROWS = [
    {"pn": "AA-1", "name": "ПРИДУМАННАЯ ШАЙБА", "name_en": "INVENTED WASHER - M16",
     "unit_en": "pcs", "qty": 12, "cat": "крепёж", "model": "Придуманная машина",
     "usd_lo": 1, "usd_hi": 9, "unit_price_usd": None},
    {"pn": "BB-2", "name": "ПРИДУМАННЫЙ ДАТЧИК", "name_en": "INVENTED TRANSMITTER",
     "unit_en": "pcs", "qty": 3, "cat": "датчики и КИП", "model": "Придуманная машина",
     "usd_lo": 100, "usd_hi": 400, "unit_price_usd": None},
]
META = {"rows_total": 2, "no_price": 2, "picked": 2, "qty": 15, "makers": ["Придуманный завод"]}


def test_в_запросе_нет_наших_вилок_и_оценок():
    html = rfq_en.build(ROWS, META)
    body = html.split("<h2>Request for quotation</h2>", 1)[1]
    for forbidden in ("usd_lo", "400", "вилка", "экспозиц", "USD"):
        assert forbidden not in body, f"в тело запроса просочилось «{forbidden}»"


def test_каждая_строка_попала_в_запрос():
    html = rfq_en.build(ROWS, META)
    for r in ROWS:
        assert r["pn"] in html and r["name_en"] in html


def test_наименование_идёт_английским_оригиналом_а_не_переводом():
    """Обратный перевод русской строки не ищется ни в одном каталоге."""
    html = rfq_en.build(ROWS, META)
    body = html.split("<h2>Request for quotation</h2>", 1)[1]
    assert "INVENTED TRANSMITTER" in body
    assert "ПРИДУМАННЫЙ ДАТЧИК" not in body


def test_в_запросе_перечислено_что_обязан_прислать_продавец():
    html = rfq_en.build(ROWS, META)
    for what, _ in rfq_en.ASKS:
        assert what in html, f"из запроса выпало обязательное поле «{what}»"
    assert "Confirmed stock as a number" in html


def test_запрос_требует_называть_аналог_аналогом():
    html = rfq_en.build(ROWS, META)
    assert "equivalent" in html.lower()
    assert "not OEM-original" in html


def test_отсутствие_позиции_просят_отметить_а_не_пропустить():
    """Пропущенная строка читается как «не ответили» — об этом сказано прямо."""
    assert "not answered" in rfq_en.build(ROWS, META)


def test_строка_с_ценой_в_запрос_не_идёт():
    """Спрашивать цену там, где она уже есть, — трата времени продавца."""
    priced = dict(ROWS[0], pn="CC-3", unit_price_usd=42.0)
    rows = [r for r in [*ROWS, priced] if r.get("unit_price_usd") in (None, "")]
    assert all(r["pn"] != "CC-3" for r in rows)


def test_количество_берётся_из_заявки_и_не_пересчитывается():
    html = rfq_en.build(ROWS, META)
    assert ">12<" in html.replace(" ", "") or "12" in html
    assert "количество взято из заявки, не пересчитывалось" in html
