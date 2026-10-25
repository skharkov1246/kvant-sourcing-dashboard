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

import bisect
import csv
import io
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
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


# ── ряды ЦБ за период: один запрос на валюту (зонд v59 и пересчёт по дате КП) ──
#
# Дневной XML_daily — один запрос на дату: за три года это около 750 запросов.
# XML_dynamic отдаёт весь ряд одной валюты за период одним запросом. Код ряда —
# не ISO, а внутренний код ЦБ: берётся из справочника XML_valFull.asp по
# ISO_Char_Code, а не зашивается списком — новая валюта иначе молча осталась бы
# без курса (критика v59, A12). Ряд запрашивается по ParentCode: под ним ЦБ
# отдаёт и записи наследника (сербский динар R01804 → R01805F).
#
# Кеш — в памяти процесса: ряды публичные, а зонд — один прогон. Функции выше
# (rates, to_eur, sqlite-кеш fx_rates) этим не затронуты.

CBR_LIB = "https://www.cbr.ru/scripts/XML_valFull.asp"
CBR_DYN = ("https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1={a}&date_req2={b}"
           "&VAL_NM_RQ={sid}")
ECB_HIST = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
#: Дирхам ОАЭ привязан к доллару: 3,6725 AED за 1 USD (у ЕЦБ дирхама нет).
AED_USD = 3.6725


def _num(s) -> float | None:
    try:
        return float(str(s).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def cbr_codes(timeout: int = 60) -> dict[str, str]:
    """ISO-код валюты → код ряда ЦБ (ParentCode) по справочнику XML_valFull.asp.
    При двух записях с одним ISO-кодом берётся та, у которой ID совпадает с
    ParentCode (действующая), иначе первая."""
    r = requests.get(CBR_LIB, timeout=timeout)
    r.raise_for_status()
    return parse_cbr_codes(r.content)


def parse_cbr_codes(content: bytes) -> dict[str, str]:
    root = ET.fromstring(content)
    out: dict[str, str] = {}
    for it in root.findall("Item"):
        iso = (it.findtext("ISO_Char_Code") or "").strip().upper()
        if not iso:
            continue
        own = (it.get("ID") or "").strip()
        parent = (it.findtext("ParentCode") or "").strip() or own
        if iso not in out or own == parent:
            out[iso] = parent
    return out


def cbr_series(series_id: str, d1: date, d2: date, timeout: int = 60) -> list[tuple[date, float]]:
    """Ряд «рублей за ОДНУ единицу валюты» за [d1, d2] одним запросом XML_dynamic.

    Берётся VunitRate (номинал уже учтён: у рупии он менялся 10 → 100); если его
    нет — Value / Nominal. Пустой ответ — пустой список, а не исключение."""
    url = CBR_DYN.format(a=d1.strftime("%d/%m/%Y"), b=d2.strftime("%d/%m/%Y"), sid=series_id)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return parse_cbr_series(r.content)


def parse_cbr_series(content: bytes) -> list[tuple[date, float]]:
    root = ET.fromstring(content)
    out: list[tuple[date, float]] = []
    for rec in root.findall("Record"):
        try:
            d = datetime.strptime(rec.get("Date") or "", "%d.%m.%Y").date()
        except ValueError:
            continue
        v = _num(rec.findtext("VunitRate"))
        if v is None:
            val, nom = _num(rec.findtext("Value")), _num(rec.findtext("Nominal"))
            v = val / nom if (val is not None and nom) else None
        if v and v > 0:
            out.append((d, v))
    out.sort()
    return out


class CbrRates:
    """Курсы ЦБ по рядам: курс на дату — последняя запись не позже этой даты.

    Дата раньше первой записи ряда — None, а не ближайший курс: запрос ряда
    начинается за 30 дней до самой ранней нужной даты (критика v59, A11), и
    пробел в начале значит, что курса правда нет. RUB — 1 рубль за рубль."""

    def __init__(self, series: dict[str, list[tuple[date, float]]]):
        self._days: dict[str, list[date]] = {}
        self._vals: dict[str, list[float]] = {}
        for code, rows in series.items():
            rows = sorted(rows)
            self._days[code.upper()] = [d for d, _ in rows]
            self._vals[code.upper()] = [v for _, v in rows]

    def codes(self) -> set[str]:
        return set(self._days) | {"RUB"}

    def value(self, code: str | None, day: date) -> float | None:
        """Рублей за одну единицу валюты на дату."""
        code = str(code or "").upper()
        if code == "RUB":
            return 1.0
        days = self._days.get(code)
        if not days:
            return None
        i = bisect.bisect_right(days, day) - 1
        return self._vals[code][i] if i >= 0 else None

    def cross(self, a: str, b: str, day: date) -> float | None:
        """Единиц валюты a за одну единицу b на дату."""
        va, vb = self.value(a, day), self.value(b, day)
        return vb / va if (va and vb) else None

    def to_eur(self, amount, code: str | None, day: date) -> float | None:
        if amount is None or not code:
            return None
        k = self.cross(code, "EUR", day)
        return float(amount) / k if k else None

    def cross_range(self, a: str, b: str, lo: date, hi: date) -> tuple[float, float] | None:
        """(наименьший, наибольший) курс «a за 1 b» на отрезке дат [lo, hi]."""
        if hi < lo:
            lo, hi = hi, lo
        days = {lo}
        for code in (a.upper(), b.upper()):
            ds = self._days.get(code) or []
            i, j = bisect.bisect_left(ds, lo), bisect.bisect_right(ds, hi)
            days.update(ds[i:j])
        vals = [x for x in (self.cross(a, b, d) for d in days) if x]
        return (min(vals), max(vals)) if vals else None


def ecb_history(timeout: int = 60) -> dict[str, list[tuple[date, float]]]:
    """Курсы ЕЦБ за всю историю одним запросом: {код: [(дата, единиц за 1 EUR)]}.
    Второй способ проверить ряды ЦБ (критика v59, A12); рубля у ЕЦБ с 2022 года нет."""
    r = requests.get(ECB_HIST, timeout=timeout)
    r.raise_for_status()
    return parse_ecb_history(r.content)


def parse_ecb_history(content: bytes) -> dict[str, list[tuple[date, float]]]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("utf-8", "ignore")
    out: dict[str, list[tuple[date, float]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            d = date.fromisoformat(str(row.get("Date") or "").strip())
        except ValueError:
            continue
        for k, v in row.items():
            code = str(k or "").strip().upper()
            x = _num(v)
            if code and code != "DATE" and x:
                out.setdefault(code, []).append((d, x))
    for v in out.values():
        v.sort()
    return out
