"""Письмо продавцу не несёт нашей информации и не путает чужую цифру с его.

Корпуса придуманы (правило 18 CLAUDE.md), но каждая форма взята с живой строки:
письмо, унёсшее нашу вилку, и число, принадлежащее соседнему исполнению.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import stock_letters as SL  # noqa: E402


def _rows(tmp: Path, stock: str, contacts: str = "sales@example.com") -> tuple[Path, Path]:
    ask = {"rows": [{"pn": "ZZ-9001", "qty": 4, "usd_lo": 10, "usd_hi": 20,
                     "name": "Придуманная позиция"}]}
    rv = {"rows": [{"pn": "ZZ-9001", "stock": stock, "contacts": contacts,
                    "band_verdict": "ВЕРНА", "note": "корпус"}]}
    a, r = tmp / "ask.json", tmp / "rv.json"
    a.write_text(json.dumps(ask, ensure_ascii=False), encoding="utf-8")
    r.write_text(json.dumps(rv, ensure_ascii=False), encoding="utf-8")
    return a, r


def _build(tmp: Path, monkeypatch, stock: str, contacts: str = "sales@example.com") -> dict:
    a, r = _rows(tmp, stock, contacts)
    monkeypatch.setattr(SL, "ASK", a)
    monkeypatch.setattr(SL, "RV", r)
    return SL.build()


# Корпус, попадающий в пакет: первая фраза называет число продавца, а дальше
# идёт наша оговорка — значит подтверждения нет и спросить его надо.
WITH_NUMBER = "5 шт заявлено в листе. Даты у листа нет, числом остаток не подтверждён."


def test_наша_проза_в_письмо_не_уходит(tmp_path, monkeypatch):
    """Поле остатка — наша внутренняя запись, и в письмо она не копируется.

    В нём встречаются и наша вилка, и имя заказчика, и слово «выставлено».
    Продавцу возвращается ровно одно: его собственное число.
    """
    d = _build(tmp_path, monkeypatch,
               "5 шт заявлено в листе. Наша вилка 10–20 USD, числом не подтверждено.")
    assert d["letters"], "письмо должно собраться — спросить-то надо"
    for letter in d["letters"]:
        assert not SL.FORBIDDEN.search(letter["body"])
        assert "5 шт" in letter["body"]


def test_имя_заказчика_в_письмо_не_уходит(tmp_path, monkeypatch):
    d = _build(tmp_path, monkeypatch,
               "10 шт в листе. По перечню ЛУКОЙЛа, лист Энергосети, не подтверждено.")
    for letter in d["letters"]:
        assert not re.search(r"ЛУКОЙЛ|Энергосети", letter["body"], re.I)


def test_цифра_соседнего_исполнения_в_письмо_не_попадает(tmp_path, monkeypatch):
    """«По -10 — ничего. По соседнему -200 напечатано 3 pcs» — это не его цифра.

    Иначе продавец получит «в ваших данных указано 3 pcs» по позиции, о которой
    он ничего не заявлял, и ответит не о том.
    """
    d = _build(tmp_path, monkeypatch, "По -10 — ничего. По соседнему -200 напечатано 3 pcs")
    assert d["letters"], "письмо должно собраться, просто без чужой цифры"
    assert "3 pcs" not in d["letters"][0]["body"]
    assert "в ваших данных" not in d["letters"][0]["body"]


def test_своя_цифра_продавца_в_письме_остаётся(tmp_path, monkeypatch):
    """Возвращать продавцу его же число — смысл письма: его и надо поправить."""
    d = _build(tmp_path, monkeypatch,
               "12 шт заявлено в листе. Даты нет, числом не подтверждено.")
    assert d["letters"] and "12 шт" in d["letters"][0]["body"]


def test_строка_с_подтверждённым_остатком_в_пакет_не_идёт(tmp_path, monkeypatch):
    """Спрашивать нечего: продавец уже назвал остаток числом и без оговорок."""
    d = _build(tmp_path, monkeypatch, "на складе 40 шт, отгрузка из Москвы")
    assert not d["letters"] and d["rows_total"] == 0


def test_строка_без_числа_в_пакет_не_идёт(tmp_path, monkeypatch):
    """Там нечего подтверждать — это работа писем о цене, а не об остатке."""
    d = _build(tmp_path, monkeypatch, "«In Stock» словом, числа нет ни у кого")
    assert not d["letters"]


def test_без_почты_строка_идёт_в_счёт_безадресных(tmp_path, monkeypatch):
    """Домен не додумывается: письмо без адреса не собирается."""
    d = _build(tmp_path, monkeypatch, WITH_NUMBER, contacts="телефон +7 000")
    assert not d["letters"]
    assert d["rows_without_address"] == 1


def test_четыре_вопроса_и_ни_одного_лишнего(tmp_path, monkeypatch):
    d = _build(tmp_path, monkeypatch, WITH_NUMBER)
    body = d["letters"][0]["body"]
    for q in ("остаток на складе ЧИСЛОМ", "закрывает ли он", "на какую дату",
              "какая из них действующая"):
        assert q in body
    assert "цена за штуку" not in body, "это письмо не про цену"
