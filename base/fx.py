#!/usr/bin/env python3
"""Курсы валют по дате — чтобы сравнивать цену поставщика с нашей.

Зачем. Оферта поставщика приходит в долларах или юанях, наше ТКП уходит в евро
или рублях: из 269 сделок, где обе стороны с ценой, общая валюта нашлась лишь у
69. Без пересчёта остальные 200 сделок для сравнения цен потеряны, а отношение
цен в разных валютах — это курс, а не наценка.

Источник — дневные курсы ЦБ РФ (XML_daily.asp). Берётся VunitRate, курс за одну
единицу валюты: у юаня и иены номинал в котировке 10 и 100, и без этого цена
уехала бы на порядок. На выходные и праздники ЦБ отдаёт курс последнего
рабочего дня — это нормально, храним его под запрошенной датой.

Курсы кешируются в самой базе: за год набирается около 250 дат, повторные
прогоны в сеть уже не ходят.
"""
from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

CBR = "https://www.cbr.ru/scripts/XML_daily.asp?date_req={d}"
DDL = """
CREATE TABLE IF NOT EXISTS fx_rates (
  day TEXT, code TEXT, rub REAL,          -- рублей за ОДНУ единицу валюты
  PRIMARY KEY (day, code)
);
"""
_MEM: dict[str, dict[str, float]] = {}


def ensure(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


def _fetch(day: str) -> dict[str, float]:
    d = datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m/%Y")
    r = requests.get(CBR.format(d=d), timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content.decode("windows-1251", "ignore"))
    out: dict[str, float] = {"RUB": 1.0}
    for v in root.findall("Valute"):
        code = (v.findtext("CharCode") or "").strip().upper()
        unit = (v.findtext("VunitRate") or v.findtext("Value") or "").replace(",", ".")
        try:
            out[code] = float(unit)
        except ValueError:
            continue
    return out


def rates(con: sqlite3.Connection, day: str) -> dict[str, float]:
    """Курсы на дату: сначала кеш в базе, потом ЦБ."""
    if day in _MEM:
        return _MEM[day]
    have = {c: v for c, v in con.execute("SELECT code, rub FROM fx_rates WHERE day=?", (day,))}
    if not have:
        try:
            have = _fetch(day)
        except Exception:
            have = {}
        if have:
            con.executemany("INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)",
                            [(day, c, v) for c, v in have.items()])
            con.commit()
    _MEM[day] = have
    return have


def to_eur(con: sqlite3.Connection, amount: float, code: str | None, day: str) -> float | None:
    """Сумма в евро на дату. None — если валюта неизвестна или курса нет."""
    if amount is None or not code:
        return None
    code = code.upper()
    r = rates(con, day)
    eur = r.get("EUR")
    if not eur:
        return None
    if code == "EUR":
        return float(amount)
    per = r.get(code)
    if not per:
        return None
    return float(amount) * per / eur


def day_of(value: str | None, default: str | None = None) -> str:
    """Дата в виде ГГГГ-ММ-ДД из значения поля Битрикса."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
    return m.group(1) if m else (default or date.today().isoformat())
