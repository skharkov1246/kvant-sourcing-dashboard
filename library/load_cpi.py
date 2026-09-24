#!/usr/bin/env python3
"""Индексы потребительских цен по стране и месяцу → lib_cpi.

ЗАЧЕМ. Распоряжение владельца 24.09.2026: «Важно иметь в виду месяц и год
получения квотации, чтобы индексировать их на инфляцию в конкретно выбранной
стране». Дата квотации лежит в lib_prices.price_date (library/quote_date.py);
здесь — второй множитель: во сколько раз выросли цены страны между месяцем
квотации и выбранным месяцем (функция lib_cpi_factor в schema.sql).

ИСТОЧНИК — МВФ, набор CPI, SDMX 2.1 без ключа (проверено 24.09.2026):

    https://api.imf.org/external/sdmx/2.1/data/CPI/{страны}.CPI._T.IX.M?startPeriod=…

  {страны}  — ISO alpha-3 через «+», пусто — все (190 стран, ~25 тыс. точек с
              2015 года, один запрос, около секунды);
  CPI       — индекс потребительских цен (у стран ЕС — HICP Евростата внутри);
  _T        — все товары и услуги;
  IX        — индекс (не темп прироста);
  M         — помесячно.

Что проверено и почему не другие. OECD SDMX по России ответил «NoRecordsFound»
(ряды России остановлены в 2022). ЕЦБ (data-api.ecb.europa.eu) отдаёт HICP без
ключа, но только по Европе. Всемирный банк — только годовые. Росстат с раннера
по TLS не открылся. У МВФ есть и Россия, и Китай, и Турция, и Европа — одним
запросом.

БАЗА РЯДА РАЗНАЯ. У России индекс 2010 = 100, у Китая 2020, у Италии и Турции
2025. Колонка base хранит её у каждой точки, а lib_cpi_factor делит только
внутри одной базы. Сменит источник базу у страны — старые и новые точки не
смешаются, а перезапишутся целиком (точка с новой базой — «пересмотр»).

ЧЕГО ЗДЕСЬ НЕТ. Пересчёта валют: индекс меняет уровень цен внутри страны, а не
курс. Цена в евро, приведённая по инфляции России, — число без смысла; функция
lib_prices_indexed пишет об этом в оговорке. Индекс потребительский, а не цен
производителей: для промышленных деталей он приближение.

ПО УМОЛЧАНИЮ ВХОЛОСТУЮ (правило 3): печатает, сколько точек новых, сколько
пересмотрено, сколько пропало бы (ноль по построению — загрузчик ничего не
удаляет) и по скольким странам. Запись — APPLY=1, с ключом прогона RUN_ID;
откат — ROLLBACK=<ключ>: новые точки удаляются, пересмотренные получают прежнее
значение.

Гейты записи — запись отменяется сама (правило 3):
  · точек меньше MIN_POINTS — ответ источника обрезан;
  · нет ни одной страны из ОБЯЗАТЕЛЬНЫЕ (Россия, Китай, Германия) при загрузке
    всех стран;
  · пересмотрено больше REVISION_SHARE точек базы — источник сменил методику, а
    это решение человека, не загрузчика.

Журнал — только агрегаты (правило 17): числа точек и коды стран.

    SUPABASE_DB_URL=… python library/load_cpi.py                # вхолостую
    SUPABASE_DB_URL=… APPLY=1 python library/load_cpi.py        # запись
    SUPABASE_DB_URL=… ROLLBACK=cpi-123 python library/load_cpi.py
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone

ИСТОЧНИК = "IMF CPI"
АДРЕС = "https://api.imf.org/external/sdmx/2.1/data/CPI/{страны}.CPI._T.IX.M"
НАЧАЛО = "2015-01"
MIN_POINTS = 1000
ОБЯЗАТЕЛЬНЫЕ = ("RUS", "CHN", "DEU")
REVISION_SHARE = 0.2

#: Валюта страны — только для оговорки «валюта цены не совпадает». Закрытый
#: список стран, откуда у нас бывают предложения; остальным — пусто, и оговорка
#: скажет «валюта страны не известна», а не угадает.
ЕВРО = ("AUT", "BEL", "CYP", "DEU", "ESP", "EST", "FIN", "FRA", "GRC", "HRV", "IRL",
        "ITA", "LTU", "LUX", "LVA", "MLT", "NLD", "PRT", "SVK", "SVN")
ВАЛЮТА = {**{к: "EUR" for к in ЕВРО},
          "RUS": "RUB", "CHN": "CNY", "USA": "USD", "GBR": "GBP", "JPN": "JPY",
          "KOR": "KRW", "IND": "INR", "TUR": "TRY", "CHE": "CHF", "SWE": "SEK",
          "NOR": "NOK", "DNK": "DKK", "POL": "PLN", "CZE": "CZK", "HUN": "HUF",
          "ROU": "RON", "BLR": "BYN", "KAZ": "KZT", "UZB": "UZS", "ARM": "AMD",
          "ARE": "AED", "SAU": "SAR", "IRN": "IRR", "CAN": "CAD", "AUS": "AUD",
          "BRA": "BRL", "MEX": "MXN", "ZAF": "ZAR", "SGP": "SGD", "MYS": "MYR",
          "THA": "THB", "VNM": "VND", "IDN": "IDR", "HKG": "HKD", "ISR": "ILS",
          "UKR": "UAH", "SRB": "RSD", "GEO": "GEL", "AZE": "AZN", "KGZ": "KGS"}


def месяц(период: str) -> date | None:
    """«2025-M06» (или «2025-06») → 2025-06-01. Иное — None."""
    с = (период or "").strip().replace("-M", "-")
    try:
        г, м = с.split("-")[:2]
        return date(int(г), int(м), 1)
    except (ValueError, TypeError):
        return None


def разобрать(xml: bytes | str) -> list[dict]:
    """Ответ SDMX structure-specific → точки {country, month, index_value, base, series}.

    Точку без значения, с нулём или с непонятным периодом пропускаем: индекс ноль
    дал бы деление на ноль, а пустой — «рост в бесконечность».
    """
    корень = ET.fromstring(xml)
    out: list[dict] = []
    for ряд in корень.iter():
        if not ряд.tag.endswith("Series"):
            continue
        а = ряд.attrib
        страна = (а.get("COUNTRY") or а.get("REF_AREA") or "").upper()
        if len(страна) != 3 or not страна.isalpha():
            continue
        ключ = ".".join(а.get(k, "") for k in ("COUNTRY", "INDEX_TYPE", "COICOP_1999",
                                                "TYPE_OF_TRANSFORMATION", "FREQUENCY"))
        база_ряда = а.get("COMMON_REFERENCE_PERIOD") or ""
        for т in ряд:
            if not т.tag.endswith("Obs"):
                continue
            о = т.attrib
            м = месяц(о.get("TIME_PERIOD", ""))
            try:
                v = float(о.get("OBS_VALUE", ""))
            except ValueError:
                continue
            if м is None or not v > 0:
                continue
            out.append({"country": страна, "month": м, "index_value": v,
                        "base": о.get("REFERENCE_PERIOD") or база_ряда or "?",
                        "series": ключ, "currency": ВАЛЮТА.get(страна)})
    return out


def скачать(страны: str = "", начало: str = НАЧАЛО, timeout: int = 120) -> bytes:
    import requests
    адрес = АДРЕС.format(страны=страны)
    r = requests.get(адрес, params={"startPeriod": начало}, timeout=timeout,
                     headers={"Accept": "application/xml",
                              "User-Agent": "kvant-library-cpi/1.0"})
    r.raise_for_status()
    return r.content


def сравнить(новые: list[dict], было: dict[tuple, tuple]) -> dict:
    """Раскладка до записи: новые, пересмотренные, без изменений.

    было: (country, month) → (index_value, base). Пересмотр — другая база или
    значение, отличное больше чем на 1e-9 относительно.
    """
    сч = Counter()
    for т in новые:
        к = (т["country"], т["month"])
        if к not in было:
            сч["новых"] += 1
            continue
        v, b = было[к]
        if b != т["base"] or abs(float(v) - т["index_value"]) > 1e-9 * max(1.0, abs(float(v))):
            сч["пересмотрено"] += 1
        else:
            сч["без изменений"] += 1
    ключи = {(т["country"], т["month"]) for т in новые}
    # Пропало бы — точки базы, которых нет в ответе. Загрузчик их НЕ удаляет;
    # число печатается, чтобы обрезанный ответ был виден (правило 0).
    сч["в базе, нет в ответе (не трогаются)"] = sum(1 for к in было if к[0] in
                                                    {т["country"] for т in новые}
                                                    and к not in ключи)
    return dict(сч)


def гейты(точки: list[dict], сч: dict, всего_в_базе: int, все_страны: bool) -> list[str]:
    провал = []
    if len(точки) < MIN_POINTS:
        провал.append(f"точек {len(точки)} меньше {MIN_POINTS} — ответ похож на обрезанный")
    if все_страны:
        есть = {т["country"] for т in точки}
        нет = [к for к in ОБЯЗАТЕЛЬНЫЕ if к not in есть]
        if нет:
            провал.append(f"нет обязательных стран: {', '.join(нет)}")
    if всего_в_базе and сч.get("пересмотрено", 0) > REVISION_SHARE * всего_в_базе:
        провал.append(f"пересмотрено {сч['пересмотрено']} точек из {всего_в_базе} — "
                      "похоже на смену методики, решает человек")
    return провал


ЗАПИСЬ = """
insert into lib_cpi (country, month, index_value, base, source, series, currency,
                     loaded_at, run_id)
values %s
on conflict (country, month, source) do update set
  prev_value  = lib_cpi.index_value,
  prev_base   = lib_cpi.base,
  prev_run    = lib_cpi.run_id,
  index_value = excluded.index_value,
  base        = excluded.base,
  series      = excluded.series,
  currency    = coalesce(excluded.currency, lib_cpi.currency),
  loaded_at   = excluded.loaded_at,
  run_id      = excluded.run_id
where lib_cpi.index_value is distinct from excluded.index_value
   or lib_cpi.base is distinct from excluded.base"""

# Откат: новые точки прогона удаляются, пересмотренные получают прежнее.
ОТКАТ_НОВЫХ = "delete from lib_cpi where run_id = %s and prev_run is null and prev_value is null"
ОТКАТ_ПЕРЕСМОТРА = """
update lib_cpi set index_value = prev_value, base = prev_base, run_id = prev_run,
                   prev_value = null, prev_base = null, prev_run = null
 where run_id = %s and prev_value is not null"""


def записать(cur, точки: list[dict], run_id: str, execute_values) -> int:
    сейчас = datetime.now(timezone.utc)
    строки = [(т["country"], т["month"], т["index_value"], т["base"], ИСТОЧНИК,
               т["series"], т["currency"], сейчас, run_id) for т in точки]
    execute_values(cur, ЗАПИСЬ, строки, page_size=1000)
    cur.execute("select count(*) from lib_cpi where run_id = %s", (run_id,))
    return int(cur.fetchone()[0])


def откатить(cur, run_id: str) -> tuple[int, int]:
    cur.execute(ОТКАТ_ПЕРЕСМОТРА, (run_id,))
    пересмотр = cur.rowcount
    cur.execute(ОТКАТ_НОВЫХ, (run_id,))
    return cur.rowcount, пересмотр


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("::error::нет SUPABASE_DB_URL")
        return 2
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    try:
        with conn.cursor() as cur:
            откат = os.environ.get("ROLLBACK", "").strip()
            if откат:
                удалено, возвращено = откатить(cur, откат)
                conn.commit()
                print(f"✓ откат {откат}: новых точек удалено {удалено}, "
                      f"пересмотренных возвращено {возвращено}")
                return 0

            страны = os.environ.get("COUNTRIES", "").strip().upper().replace(",", "+")
            начало = os.environ.get("START", "").strip() or НАЧАЛО
            точки = разобрать(скачать(страны, начало))
            по_стране = Counter(т["country"] for т in точки)
            месяцы = sorted({т["month"] for т in точки})
            print(f"источник: {ИСТОЧНИК} · стран {len(по_стране)} · точек {len(точки)}"
                  + (f" · месяцы {месяцы[0]:%Y-%m} … {месяцы[-1]:%Y-%m}" if месяцы else ""))
            for к in ОБЯЗАТЕЛЬНЫЕ:
                if к in по_стране:
                    последний = max(т["month"] for т in точки if т["country"] == к)
                    print(f"    {к}: точек {по_стране[к]}, последний месяц {последний:%Y-%m}")
            базы = Counter((т["country"], т["base"]) for т in точки)
            print(f"рядов «страна × база»: {len(базы)} · стран с двумя базами: "
                  f"{sum(1 for n in Counter(к for к, _ in базы).values() if n > 1)}")

            cur.execute("select country, month, index_value, base from lib_cpi "
                        "where source = %s", (ИСТОЧНИК,))
            было = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}
            сч = сравнить(точки, было)
            print("раскладка: " + " · ".join(f"{к} {v}" for к, v in сч.items()))
            провал = гейты(точки, сч, len(было), not страны)
            for п in провал:
                print(f"::error::гейт не пройден: {п}")
            if not os.environ.get("APPLY", "").strip():
                print("вхолостую: в базе ничего не изменено. Для записи APPLY=1")
                return 1 if провал else 0
            if провал:
                print("запись отменена гейтами")
                return 1
            run_id = os.environ.get("RUN_ID", "").strip() or \
                f"cpi-{os.environ.get('GITHUB_RUN_ID') or int(datetime.now().timestamp())}"
            n = записать(cur, точки, run_id, psycopg2.extras.execute_values)
            conn.commit()
            print(f"✓ записано точек (новых и пересмотренных): {n} · ключ прогона {run_id}"
                  f" · откат: ROLLBACK={run_id}")
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
