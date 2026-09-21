"""Утверждение «цена лежит в нашем вложении» — адрес или цена.

Набор ship_inside_quotes сводит строку заявки с файлом, в котором есть хоть
одна цена. Это адрес. Цена по строке — другое утверждение, и разница измерима:
по охвату «Энергосети» адрес был у 265 строк, цена сошлась у 104.

Корпуса здесь придуманы (правило 18 CLAUDE.md): номера взяты заведомо
несуществующие, чтобы тест не зависел от данных заявки.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import ship_offer  # noqa: E402


def _corpus(tmp: Path) -> tuple[Path, Path]:
    """Выгрузка и адреса: по одной строке на каждый из четырёх случаев."""
    dump = {"files": [
        {"file_name": "kp-vendor.xlsx", "direction": "входящее", "prices": [
            # цена по колонке — эта строка обязана сойтись
            {"pn": "ZZ-1000", "price": 12.5, "currency": "USD",
             "class_rule": "колонка «цена» по заголовку", "is_price": True},
            # догадка: значение есть, но ценой не является
            {"pn": "ZZ-2000", "price": 7, "currency": "USD",
             "class_rule": "последнее число строки (заголовок не опознан)",
             "is_price": False},
        ]},
    ]}
    inside = {"rows": [
        {"pn": "ZZ-1000", "found_in": [{"file": "kp-vendor.xlsx"}]},
        {"pn": "ZZ-2000", "found_in": [{"file": "kp-vendor.xlsx"}]},
        {"pn": "ZZ-3000", "found_in": [{"file": "kp-vendor.xlsx"}]},
        {"pn": "ZZ-4000", "found_in": [{"file": "kp-drugogo-progona.xlsx"}]},
    ]}
    d = tmp / "dump.json"
    i = tmp / "inside.json"
    d.write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")
    i.write_text(json.dumps(inside, ensure_ascii=False), encoding="utf-8")
    return d, i


def _split(tmp: Path, monkeypatch) -> dict:
    d, i = _corpus(tmp)
    monkeypatch.setattr(ship_offer, "INSIDE", i)
    rows = [{"pn": f"ZZ-{n}000"} for n in (1, 2, 3, 4)]
    _, supp, _ = ship_offer.prices_by_direction(d, {"USD": 1.0})
    return ship_offer.inside_claim_split(rows, d, supp)


def test_четыре_случая_разделены(tmp_path, monkeypatch):
    s = _split(tmp_path, monkeypatch)
    assert s["rows_with_address"] == 4
    assert s["price_joined"] == 1
    assert s["value_is_guess"] == 1
    assert s["row_absent_in_parsed_file"] == 1
    assert s["address_outside_this_dump"] == 1


def test_в_деньги_идёт_только_сошедшаяся_цена(tmp_path, monkeypatch):
    """Ключевое свойство: адрес не равен цене, и это видно числом.

    Без разделения все четыре строки читались бы как «цена у нас есть», и
    отчёт владельцу называл бы деньгами то, что деньгами не подтверждено.
    """
    s = _split(tmp_path, monkeypatch)
    assert s["price_joined"] < s["rows_with_address"]
    assert (s["price_joined"] + s["value_is_guess"]
            + s["row_absent_in_parsed_file"] + s["address_outside_this_dump"]
            == s["rows_with_address"])


def test_строка_без_адреса_в_счёт_не_попадает(tmp_path, monkeypatch):
    """Строка заявки, которой набор адресов не касался, здесь не считается."""
    d, i = _corpus(tmp_path)
    monkeypatch.setattr(ship_offer, "INSIDE", i)
    _, supp, _ = ship_offer.prices_by_direction(d, {"USD": 1.0})
    s = ship_offer.inside_claim_split([{"pn": "QQ-9999"}], d, supp)
    assert s["rows_with_address"] == 0
