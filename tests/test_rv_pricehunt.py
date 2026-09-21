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


def test_строка_с_ответом_в_нашем_вложении_в_разведку_не_идёт():
    """Посылать в интернет за ценой, которая лежит в почте, — терять проход.

    Замер 18.09.2026: из двадцати оставшихся к добору строк двенадцать на
    24 040 USD были именно такими — три пятых остатка. После отсева к веб-разведке
    осталось восемь строк на 13 342 USD, то есть этот фронт практически исчерпан.
    """
    ph, rows, rvx = load()
    house = ph.in_house_keys()
    if not house:
        pytest.skip("замера вложений нет")
    left, blocked, inh = ph.select(ph.candidates(rows, rvx))
    assert inh, "отсев не проверяется: ни одна строка под него не попала"
    for _e, r, _x in left:
        assert ph.key(r.get("pn")) not in house, r.get("pn")
    # части складываются в целое: ни одна строка не посчитана дважды и не выпала
    assert len(left) + len(blocked) + len(inh) == len(ph.candidates(rows, rvx))


def test_вычитается_подтверждённая_цена_а_не_адрес_цены(tmp_path, monkeypatch):
    """Из добора вычитается ship_inside_priced, а не ship_inside_quotes.

    Исправлено 18.09.2026. Наборы отвечают на разные вопросы: quotes — «в каком
    нашем файле встречается этот номер и есть ли в файле хоть одна цена»,
    priced — «цена по этому номеру подтверждена: единица × количество = итог в
    той же строке». На живых данных разница 754 номера против 210, и из-за неё
    48 строк на 78 242 USD не попадали ни в добор цены, ни в письма: отчёт
    считал их отвеченными, а не искал по ним никто. Корпус придуман.
    """
    import rv_pricehunt as ph

    quotes = tmp_path / "quotes.json"
    priced = tmp_path / "priced.json"
    quotes.write_text(json.dumps({"rows": [{"pn": "QQ-7001"}, {"pn": "QQ-7002"}]},
                                 ensure_ascii=False), encoding="utf-8")
    priced.write_text(json.dumps({"parts": [{"pn": "QQ-7002", "key": "QQ7002"}]},
                                 ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ph, "INSIDE", quotes)
    monkeypatch.setattr(ph, "PRICED", priced)

    house = ph.in_house_keys()
    assert house == {"QQ7002"}, "вычитается только подтверждённая цена"
    assert "QQ7001" not in house, (
        "номер, у которого есть лишь адрес цены, из добора вычитать нельзя: "
        "по нему не искал никто")


def test_без_набора_подтверждённых_цен_остаётся_прежний_порядок(tmp_path, monkeypatch):
    """Если подтверждённых цен ещё не собирали — вычитается хотя бы адрес.

    Исправление не должно превращать отсутствие нового набора в отсутствие
    отсева вовсе: тогда разведка пойдёт в открытый доступ по строкам, чья цена
    у нас в почте.
    """
    import rv_pricehunt as ph

    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({"rows": [{"pn": "QQ-7003"}]}, ensure_ascii=False),
                      encoding="utf-8")
    monkeypatch.setattr(ph, "INSIDE", quotes)
    monkeypatch.setattr(ph, "PRICED", tmp_path / "нет-такого-файла.json")
    assert ph.in_house_keys() == {"QQ7003"}
