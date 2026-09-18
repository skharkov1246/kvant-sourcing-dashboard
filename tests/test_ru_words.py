"""Англицизм заменяется, цитата — нет.

Правило оплачено дважды. Первый раз — тем, что владелец просил русский без
англицизмов, а слово «прайс» возвращалось в набор с каждой новой пачкой
разведки: разовая чистка данных держится до следующего слияния, поэтому замена
стоит на входе. Второй раз — тем, что механическая замена без защиты цитат
испортила одиннадцать составных слов, и их правили по смыслу вручную.

Корпус здесь придуман, а не скопирован из базы.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def test_обычное_слово_заменяется():
    import ru_words as rw

    assert rw.clean("Заводской прайс открыт") == "Заводской прейскурант открыт"
    assert rw.clean("нет прайса ни у кого") == "нет прейскуранта ни у кого"
    assert rw.clean("Прайс дистрибьютора") == "Прейскурант дистрибьютора"


def test_составное_слово_не_ломается():
    import ru_words as rw

    assert rw.clean("заводской прайс-лист") == "заводской прейскурант"
    assert rw.clean("одна цена из прайс-файла группы") == (
        "одна цена из прейскурантного файла группы")
    assert "прейскурант-" not in rw.clean("цена в прайс-буке завода")


def test_цитата_остаётся_дословной():
    """Цитата со страницы продавца есть доказательство: править её нельзя."""
    import ru_words as rw

    t = 'На странице написано «Download our price list» и «прайс-лист 2026»'
    assert rw.clean(t) == t


def test_адрес_не_трогается():
    import ru_words as rw

    t = "Прайс лежит по адресу https://example.com/прайс-лист.pdf"
    out = rw.clean(t)
    assert out.startswith("Прейскурант лежит")
    assert "https://example.com/прайс-лист.pdf" in out


def test_текст_без_слова_возвращается_как_есть():
    import ru_words as rw

    t = "Цена найдена на карточке продавца, 12,50 USD за штуку"
    assert rw.clean(t) is t


def test_чистится_вся_запись():
    import ru_words as rw

    row = {"pn": "X-1", "note": "прайс завода", "price_low": 10.0,
           "price_note": "по прайсу дистрибьютора"}
    n = rw.clean_row(row)
    assert n == 2
    assert row["note"] == "прейскурант завода"
    assert row["price_note"] == "по прейскуранту дистрибьютора"
    assert row["price_low"] == 10.0
