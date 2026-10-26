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
import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta

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


def _cbr_get(url: str, timeout) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def cbr_codes(timeout: int = 60, fetch=None) -> dict[str, str]:
    """ISO-код валюты → код ряда ЦБ (ParentCode) по справочнику XML_valFull.asp.
    При двух записях с одним ISO-кодом берётся та, у которой ID совпадает с
    ParentCode (действующая), иначе первая. fetch — адрес → тело ответа (тесты
    подставляют свой, в сеть не ходят); ошибка — исключением."""
    return parse_cbr_codes((fetch or (lambda u: _cbr_get(u, timeout)))(CBR_LIB))


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


def cbr_series(series_id: str, d1: date, d2: date, timeout: int = 60, fetch=None) -> list[tuple[date, float]]:
    """Ряд «рублей за ОДНУ единицу валюты» за [d1, d2] одним запросом XML_dynamic.

    Берётся VunitRate (номинал уже учтён: у рупии он менялся 10 → 100); если его
    нет — Value / Nominal. Пустой ответ — пустой список, а не исключение."""
    url = CBR_DYN.format(a=d1.strftime("%d/%m/%Y"), b=d2.strftime("%d/%m/%Y"), sid=series_id)
    return parse_cbr_series((fetch or (lambda u: _cbr_get(u, timeout)))(url))


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

    def __init__(self, series: dict[str, list[tuple[date, float]]],
                 holes: dict[str, set[date]] | None = None):
        """holes — {код: даты}, на которые курса НЕТ, хотя ряд их «покрывает»: даты зеркала со
        сбоем, обрезанные временем или без курса. Ряд зеркала — только прочитанные даты, и
        «последняя запись не позже даты» отдала бы за такую дату курс другой, более ранней
        прочитанной даты (ревью 26.10.2026: курс на месяцы старше без пометки)."""
        self._holes = {str(c).upper(): set(v) for c, v in (holes or {}).items()}
        self._days: dict[str, list[date]] = {}
        self._vals: dict[str, list[float]] = {}
        for code, rows in series.items():
            rows = sorted(rows)
            self._days[code.upper()] = [d for d, _ in rows]
            self._vals[code.upper()] = [v for _, v in rows]

    def codes(self) -> set[str]:
        return set(self._days) | {"RUB"}

    def days(self, code: str) -> list[date]:
        """Даты записей ряда (у зеркала — только прочитанные даты)."""
        return list(self._days.get(str(code or "").upper()) or [])

    def value(self, code: str | None, day: date) -> float | None:
        """Рублей за одну единицу валюты на дату."""
        code = str(code or "").upper()
        if code == "RUB":
            return 1.0
        days = self._days.get(code)
        if not days or day in self._holes.get(code, ()):
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


# ── резерв: зеркало cbr-xml-daily.ru (критика v59, A12) ──────────────────────
#
# Если www.cbr.ru не отвечает адресам раннера GitHub, курсы ЦБ берутся с зеркала.
# Рядов за период у зеркала нет — только архив по дням, один запрос на дату; на
# нерабочий день архив отвечает 404, и берётся шаг назад (до MIRROR_STEP_BACK дней).
# Поэтому запрашиваются только НУЖНЫЕ даты, от новых к старым, с пределом запросов:
# курс на дату тогда тот же, что дал бы ряд, а окно курса между датами строится
# только по прочитанным датам (уже, чем по ряду, — это печатается рядом).

CBR_MIRROR_LATEST = "https://www.cbr-xml-daily.ru/daily_json.js"
CBR_MIRROR_DAY = "https://www.cbr-xml-daily.ru/archive/{y:04d}/{m:02d}/{d:02d}/daily_json.js"
MIRROR_STEP_BACK = 10
MIRROR_RETRY_WAIT = (2.0, 5.0, 15.0)


def parse_cbr_mirror_day(content: bytes) -> tuple[date | None, dict[str, float]]:
    """daily_json.js зеркала → (дата курса, {ISO-код: рублей за ОДНУ единицу})."""
    data = json.loads(content.decode("utf-8", "ignore"))
    try:
        day = date.fromisoformat(str(data.get("Date") or "")[:10])
    except ValueError:
        day = None
    out: dict[str, float] = {}
    for code, v in (data.get("Valute") or {}).items():
        if not isinstance(v, dict):
            continue
        val, nom = _num(v.get("Value")), _num(v.get("Nominal"))
        if val and nom and val > 0:
            out[str(v.get("CharCode") or code).strip().upper()] = val / nom
    return day, out


def _mirror_get(url: str, timeout, session=None) -> tuple[int, bytes]:
    r = (session or requests).get(url, timeout=timeout)
    return r.status_code, (r.content if r.status_code == 200 else b"")


def cbr_mirror_codes(timeout: int = 30, fetch=None) -> set[str]:
    """Проверка зеркала одним запросом: ISO-коды последнего курса (пусто — исключение)."""
    status, body = (fetch or (lambda u: _mirror_get(u, timeout)))(CBR_MIRROR_LATEST)
    if status != 200:
        raise ConnectionError(f"зеркало ответило {status}")
    _d, rates = parse_cbr_mirror_day(body)
    if not rates:
        raise ValueError("в ответе зеркала нет курсов")
    return set(rates) | {"RUB"}


def cbr_mirror_series(days, codes, *, max_requests: int = 900, pause: float = 0.25,
                      timeout: int = 30, fetch=None, on_error: str = "raise",
                      deadline=None, holes: set | None = None) -> tuple[dict[str, list[tuple[date, float]]], dict]:
    """Ряды «рублей за ОДНУ единицу» по зеркалу только на нужные даты.

    days — нужные даты; codes — ISO-коды. Даты идут от новых к старым: при пределе
    запросов без курса остаются самые старые. → (ряды для CbrRates, учёт {запросов,
    дат, без курса, обрезано, ошибок}). Курс записывается под датой самого курса, но не
    позже запрошенной: так «последняя запись не позже даты» в CbrRates даёт ровно
    курс на эту дату.

    on_error — «raise» (зонд v59: сбой зеркала — исключение) или «skip» (сборка: дата со
    сбоем остаётся без курса и считается в «ошибок», прочитанные даты не теряются).
    deadline — () → True, когда время сборки на курсы вышло: оставшиеся даты — «обрезано».
    holes — множество, куда пишутся нужные даты без курса (сбой, обрезано, без курса):
    CbrRates(holes=…) не отдаст за них курс соседней прочитанной даты."""
    get = fetch or (lambda u: _mirror_get(u, timeout))
    want = {str(c).upper() for c in codes} - {"RUB"}
    cache: dict[date, tuple[date, dict[str, float]] | None] = {}
    series: dict[str, dict[date, float]] = {c: {} for c in want}
    stat = {"запросов": 0, "дат": 0, "без курса": 0, "обрезано": 0, "ошибок": 0}
    for day in sorted(set(days), reverse=True):
        stat["дат"] += 1
        found = False
        for k in range(MIRROR_STEP_BACK + 1):
            q = date.fromordinal(day.toordinal() - k)
            if q not in cache:
                if stat["запросов"] >= max_requests or (deadline and deadline()):
                    stat["обрезано"] += 1
                    found = None
                    if holes is not None:
                        holes.add(day)
                    break
                if stat["запросов"] and pause:
                    time.sleep(pause)
                stat["запросов"] += 1
                url = CBR_MIRROR_DAY.format(y=q.year, m=q.month, d=q.day)
                try:
                    status, body = get(url)
                    for wait in MIRROR_RETRY_WAIT:      # 429 и 5xx — подождать, не падать
                        if status != 429 and status < 500:
                            break
                        if deadline and deadline():
                            break
                        time.sleep(wait)
                        stat["запросов"] += 1
                        status, body = get(url)
                    if status not in (200, 404):
                        raise ConnectionError(f"зеркало ответило {status}")
                except Exception:
                    if on_error != "skip":
                        raise
                    stat["ошибок"] += 1                 # дата без курса, прочие даты остаются
                    found = None
                    if holes is not None:
                        holes.add(day)
                    break
                if status == 200:
                    eff, rates = parse_cbr_mirror_day(body)
                    cache[q] = (min(eff, q) if eff else q, rates) if rates else None
                else:
                    cache[q] = None
            hit = cache[q]
            if hit:
                eff, rates = hit
                for c in want & set(rates):
                    series[c][eff] = rates[c]
                found = True
                break
        if found is False:
            stat["без курса"] += 1
            if holes is not None:
                holes.add(day)
    return {c: sorted(v.items()) for c, v in series.items() if v}, stat


# ── курсы ЦБ для сборки дашборда (решение владельца 25.10.2026: «Реализация» в евро
# по ЦБ на дату КП) ────────────────────────────────────────────────────────────
#
# Порядок: ряды cbr.ru (справочник + один запрос на валюту за весь период) → зеркало
# на нужные даты (запрос на дату, предел BUILD_MIRROR_MAX) → None: тогда вызывающий
# берёт курсы Битрикса и говорит об этом плашкой, 1:1 не подставляется. Валюта, ряд
# которой cbr.ru не отдал (при годном евро), добирается с зеркала на нужные даты; не
# добралась — остаётся в failed (вызывающий говорит о ней плашкой). Кеш на прогон — по
# адресу запроса: вторая вкладка или повторный расчёт в сеть не ходят.
#
# Время сборки ограничено (deploy.yml: 35 мин на всё): на курсы — BUILD_FX_BUDGET_S. Вышло —
# недочитанные даты остаются без курса (счёт «обрезано»), прочитанное не теряется; первый
# обрыв связи с cbr.ru (тайм-аут, отказ соединения) — сразу на зеркало, без ожидания по
# каждой валюте.

#: запас ряда до самой ранней нужной даты: курс на дату — последняя запись не позже неё
#: (выходные, праздники); тот же запас, что у зонда v59 (CBR_LEAD_DAYS)
BUILD_LEAD_DAYS = 30
#: предел запросов к зеркалу в сборке; без курса остаются самые старые даты (зеркало идёт
#: от новых к старым). Время держит не он, а BUILD_FX_BUDGET_S
BUILD_MIRROR_MAX = 600
#: время на курсы в одной сборке, с: cbr.ru (6–8 запросов) укладывается в секунды; зеркало
#: — запрос на дату, при 0,25 с паузы и ответе за 0,5 с около 320 дат
BUILD_FX_BUDGET_S = 240.0
#: тайм-ауты (соединение, чтение), с: cbr.ru отдаёт ряд за секунды, зеркало — день за доли
CBR_TIMEOUT = (5, 20)
MIRROR_TIMEOUT = (5, 15)
#: даты вне [первый курс ЦБ к рублю 1992 г.; сегодня + год] — опечатка поля, а не дата курса:
#: год «24» дал бы в запросе «dd/mm/24», год 1 — OverflowError и падение на курсы Битрикса
DAY_MIN = date(1992, 7, 1)
DAY_MAX_AHEAD = 366
_RUN_CACHE: dict = {}


def day_ok(d: date | None, today: date) -> bool:
    """Дата годится для курса ЦБ: не раньше DAY_MIN и не дальше года вперёд."""
    return bool(d) and DAY_MIN <= d <= today + timedelta(days=DAY_MAX_AHEAD)


def _cbr_net(url: str) -> bytes:
    """cbr.ru по сети — одна функция на прогон: по ней ключ кеша прогона."""
    return _cbr_get(url, CBR_TIMEOUT)


def _cached(fetch, url: str):
    key = (fetch, url)
    if key not in _RUN_CACHE:
        _RUN_CACHE[key] = fetch(url)
    return _RUN_CACHE[key]


def _merge(series: dict, extra: dict) -> dict:
    out = {c: list(v) for c, v in series.items()}
    for c, v in extra.items():
        out[c] = sorted(set(out.get(c, [])) | set(v))
    return out


def load_cbr(codes, days, *, today: date, fetch=None, mirror_fetch=None,
             mirror_max: int = BUILD_MIRROR_MAX, mirror_pause: float = 0.25,
             budget_s: float = BUILD_FX_BUDGET_S, clock=time.monotonic) -> tuple[CbrRates | None, dict]:
    """Курсы ЦБ на нужные даты для сборки → (CbrRates или None, учёт).

    codes — ISO-коды валют сумм (RUB и EUR добавляются сами), days — даты, на которые
    нужен курс (даты вне day_ok отбрасываются и считаются в badDays). fetch — адрес → тело
    ответа cbr.ru (ошибка — исключением); mirror_fetch — адрес → (код ответа, тело) зеркала;
    по умолчанию — сеть. budget_s и clock — время на курсы (тесты подставляют свои часы).
    Учёт: src («cbr», «mirror», «» — ЦБ недоступен), requests (запросов в сеть, без попаданий
    в кеш прогона), noCode (валют нет в справочнике ЦБ), failed (ряд не получен ни с cbr.ru,
    ни с зеркала), patched (ряд cbr.ru не получен, взят с зеркала), why (тип ошибки), lo/hi —
    период рядов, timeUp — время вышло. В учёте только коды валют и счётчики — журнал
    публичный."""
    real = fetch or _cbr_net
    calls = {"n": 0}
    t0 = clock()

    def time_up() -> bool:
        return clock() - t0 >= budget_s

    def get(url):
        if (real, url) not in _RUN_CACHE:
            calls["n"] += 1
        return _cached(real, url)

    want = sorted(({str(c).upper() for c in codes if c} | {"EUR"}) - {"RUB"})
    ds_all = {d for d in days if d}
    ds = sorted(d for d in ds_all if day_ok(d, today))
    info: dict = {"src": "", "requests": 0, "noCode": [], "failed": [], "patched": [], "why": "", "lo": None,
                  "hi": today, "mirror": None, "badDays": len(ds_all) - len(ds), "timeUp": False}
    if not ds:
        ds = [today]
    d1 = min(ds) - timedelta(days=BUILD_LEAD_DAYS)
    info["lo"] = d1
    series: dict[str, list[tuple[date, float]]] = {}
    try:
        sids = cbr_codes(fetch=get)
        for n, c in enumerate(want):
            sid = sids.get(c)
            if not sid:
                info["noCode"].append(c)
                continue
            if time_up():
                info["timeUp"] = True
                info["failed"] += [x for x in want[n:] if sids.get(x)]
                break
            try:
                rows = cbr_series(sid, d1, today, fetch=get)
            except (requests.Timeout, requests.ConnectionError, ConnectionError) as e:
                # связь с cbr.ru оборвалась — прочие валюты не ждут по тайм-ауту каждая
                info["why"] = info["why"] or type(e).__name__
                info["failed"] += [x for x in want[n:] if sids.get(x)]
                break
            except Exception as e:  # noqa: BLE001 — одна валюта без ряда, прочие считаются
                info["failed"].append(c)
                info["why"] = info["why"] or type(e).__name__
                continue
            if rows:
                series[c] = rows
            else:
                info["failed"].append(c)
    except Exception as e:  # noqa: BLE001 — справочник не получен: идём на зеркало
        info["why"] = info["why"] or type(e).__name__
    info["requests"] = calls["n"]
    sess = requests.Session() if mirror_fetch is None else None
    mget = mirror_fetch or (lambda u: _mirror_get(u, MIRROR_TIMEOUT, sess))
    try:
        if series.get("EUR"):
            info["src"] = "cbr"
            miss = [c for c in info["failed"] if c != "EUR"]
            if miss and not time_up():
                # ряд одной валюты не получен — её курс на нужные даты с зеркала, остальное с cbr.ru
                try:
                    mholes: set = set()
                    mser, stat = cbr_mirror_series(set(ds), set(miss), max_requests=mirror_max,
                                                   pause=mirror_pause, fetch=mget, on_error="skip",
                                                   deadline=time_up, holes=mholes)
                    info["requests"] += stat["запросов"]
                    info["mirror"] = stat
                    info["patched"] = sorted(c for c in miss if mser.get(c))
                    series = _merge(series, {c: mser[c] for c in info["patched"]})
                    info["failed"] = sorted(set(info["failed"]) - set(info["patched"]))
                except Exception as e:  # noqa: BLE001 — зеркало не помогло: валюта остаётся в failed
                    info["mirrorWhy"] = type(e).__name__
            info["timeUp"] = info["timeUp"] or time_up()
            # даты зеркала без курса — дыры только у валют, взятых с зеркала
            return CbrRates(series, holes={c: mholes for c in info["patched"]} if info["patched"] else None), info
        # cbr.ru не дал ряда к евро — зеркало на нужные даты и сегодня
        if time_up():
            info["timeUp"] = True
            info["why"] = info["why"] or "время вышло"
            return None, info
        try:
            cbr_mirror_codes(fetch=mget)
            mholes = set()
            mser, stat = cbr_mirror_series(set(ds) | {today}, set(want), max_requests=mirror_max,
                                           pause=mirror_pause, fetch=mget, on_error="skip", deadline=time_up,
                                           holes=mholes)
            info["requests"] += 1 + stat["запросов"]
            info["mirror"] = stat
        except Exception as e:  # noqa: BLE001 — зеркало тоже недоступно: курсов ЦБ нет
            info["requests"] += 1
            info["why"] = info["why"] or type(e).__name__
            info["mirrorWhy"] = type(e).__name__
            return None, info
        info["timeUp"] = info["timeUp"] or time_up()
        if not mser.get("EUR"):
            return None, info
        info["src"] = "mirror"
        info["noCode"] = sorted(c for c in want if c not in mser)
        info["failed"] = []
        return CbrRates(mser, holes={c: mholes for c in want}), info
    finally:
        if sess is not None:
            sess.close()
