#!/usr/bin/env python3
"""Помесячные индексы потребительских цен по валютам → data/inflation/cpi_monthly.json.

ЗАЧЕМ. Решение владельца 26.09.2026: «Цена считается актуальной на дату выдачи,
потом индексируется на инфляцию». Файл — второй множитель library/inflation.py:
во сколько раз выросли цены в стране валюты между месяцем цены и месяцем, на
который цену приводят.

ПОЧЕМУ ИПЦ, А НЕ ИЦП (цены производителей). Для промышленных запчастей ИЦП ближе
по смыслу, но:
  · ИЦП России и Китая — индексы всей промышленности, в них больше половины веса
    у сырья и энергии; за 2021–2023 они ходили на ±20 % в год вслед за нефтью и
    металлом, чего цена насоса или подшипника не делала. Индексация по такому
    ряду сдвигала бы цену сильнее, чем она менялась на деле;
  · у Китая НБС публикует и ИПЦ, и ИЦП только как темпы («тот же месяц прошлого
    года = 100»), уровня ряда нет ни у того, ни у другого;
  · ИПЦ есть у всех пяти валют одним определением (COICOP, все товары и
    услуги), публикуется раньше и почти не пересматривается — у ИЦП пересмотры
    обычны;
  · «индексируется на инфляцию» в договорах и у владельца — это ИПЦ; им же уже
    считает база (library/load_cpi.py, lib_cpi — МВФ, CPI).
Для промышленных деталей ИПЦ — приближение; эта оговорка стоит в файле.

ИСТОЧНИКИ (проверено из песочницы 26.09.2026):
  RUB  Росстат недоступен: rosstat.gov.ru и gks.ru не проходят проверку TLS
       (сертификат выдан российским корневым центром, которого нет в цепочке
       доверия прокси), fedstat.ru отвечает 403. Взято зеркало — МВФ, набор CPI
       (ряд МВФ по России собирается из данных Росстата).
  USD  BLS (bls.gov, download.bls.gov) отвечает 403, публичный API без ключа
       исчерпал суточный лимит. Взято зеркало — FRED, ряд CPIAUCNS: это ровно
       ряд BLS CUUR0000SA0 (CPI-U, все города, без сезонной поправки,
       1982–84 = 100).
  EUR  Евростат напрямую: prc_hicp_minr (HICP, ECOICOP ver.2, 2025 = 100),
       еврозона EA — меняющийся состав (EA21 с 2026). Прежний набор
       prc_hicp_midx остановлен в феврале 2026.
  CNY  НБС (data.stats.gov.cn) не отвечает (сброс соединения), и уровня ряда
       НБС не публикует вовсе — только темпы. Взято зеркало — МВФ, CPI Китая
       (2020 = 100), собранный МВФ из темпов НБС.
  GBP  ONS напрямую: ряд D7BT (CPI INDEX 00: ALL ITEMS, 2015 = 100), набор MM23.

Сверка зеркал: USD и GBP сверяются с тем же рядом у МВФ по месячным темпам;
EUR — с HICP еврозоны у ЕЦБ (ICP.M.U2.N.000000.4.INX). Итог сверки лежит в
файле в разделе «сверка» у каждой валюты. Расхождение темпов у
ЗЕРКАЛА больше ПОРОГ_СВЕРКИ — прогон отказывается писать; у официального ряда
сверка справочная (МВФ по Великобритании расходится с D7BT до 0,33 п.п. в месяц
— другая редакция ряда у МВФ; первоисточник ONS первичен).

Пропуски — как у источника. У США нет октября 2025: BLS его не опубликовал
(приостановка работы правительства США осенью 2025), FRED его не содержит.

Ничего не досчитывается и не интерполируется: в файл идут ровно числа
источника. Месяца нет у источника — нет и в файле.

    python scripts/fetch_inflation.py            # скачать, сверить, записать
    python scripts/fetch_inflation.py --dry-run  # скачать и сверить, не писать
"""
from __future__ import annotations

import argparse
import csv
import http.client
import io
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "inflation" / "cpi_monthly.json"
НАЧАЛО = "2018-01"
ПОРОГ_СВЕРКИ = 0.002  # 0,2 п.п. месячного темпа

IMF = ("https://api.imf.org/external/sdmx/2.1/data/CPI/{c}.CPI._T.IX.M"
       "?startPeriod=" + НАЧАЛО)
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS"
ONS = ("https://www.ons.gov.uk/generator?format=csv"
       "&uri=/economy/inflationandpriceindices/timeseries/d7bt/mm23")
ESTAT = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
         "prc_hicp_minr?geo=EA&coicop18=TOTAL&unit=I25&format=JSON"
         "&sinceTimePeriod=" + НАЧАЛО)
ECB = ("https://data-api.ecb.europa.eu/service/data/ICP/M.U2.N.000000.4.INX"
       "?format=csvdata&startPeriod=" + НАЧАЛО)
MONTHS = dict(JAN=1, FEB=2, MAR=3, APR=4, MAY=5, JUN=6, JUL=7, AUG=8, SEP=9,
              OCT=10, NOV=11, DEC=12)


def get(url: str, попыток: int = 3) -> str:
    # FRED рвёт соединение на нестандартный User-Agent — представляемся curl.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0", "Accept": "*/*"})
    for n in range(попыток):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8")
        except (OSError, http.client.HTTPException):
            if n == попыток - 1:
                raise
            time.sleep(5 * (n + 1))
    raise RuntimeError("недостижимо")


def parse_imf(text: str) -> tuple[dict[str, float], str]:
    """SDMX 2.1 structure-specific → {месяц: значение}, базовый период."""
    for s in ET.fromstring(text).iter():
        if s.tag.endswith("Series"):
            pts = {o.attrib["TIME_PERIOD"].replace("-M", "-"): float(o.attrib["OBS_VALUE"])
                   for o in s if o.tag.endswith("Obs") and o.attrib.get("OBS_VALUE")}
            return pts, s.attrib.get("COMMON_REFERENCE_PERIOD", "")
    return {}, ""


def parse_fred(text: str) -> dict[str, float]:
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        v = r.get("CPIAUCNS", "")
        if r["observation_date"][:7] >= НАЧАЛО and v not in ("", "."):
            out[r["observation_date"][:7]] = float(v)
    return out


def parse_ons(text: str) -> tuple[dict[str, float], str]:
    out, release = {}, ""
    for r in csv.reader(io.StringIO(text)):
        if not r:
            continue
        if r[0] == "Release date" and len(r) > 1:
            release = r[1]
        m = re.fullmatch(r"(\d{4}) ([A-Z]{3})", r[0])
        if m and len(r) > 1 and r[1]:
            k = f"{m[1]}-{MONTHS[m[2]]:02d}"
            if k >= НАЧАЛО:
                out[k] = float(r[1])
    return out, release


def parse_estat(text: str) -> tuple[dict[str, float], str]:
    d = json.loads(text)
    t = d["dimension"]["time"]["category"]["index"]
    return ({k: float(d["value"][str(i)]) for k, i in t.items() if str(i) in d["value"]},
            d.get("updated", ""))


def parse_ecb(text: str) -> dict[str, float]:
    return {r["TIME_PERIOD"]: float(r["OBS_VALUE"])
            for r in csv.DictReader(io.StringIO(text)) if r.get("OBS_VALUE")}


def сверка(ряд: dict[str, float], эталон: dict[str, float], имя: str) -> dict:
    """Сравнение месячных темпов: база рядов разная, темпы — одни."""
    общие = sorted(set(ряд) & set(эталон))
    худшее, где = 0.0, None
    for a, b in zip(общие, общие[1:]):
        diff = abs(ряд[b] / ряд[a] - эталон[b] / эталон[a])
        if diff > худшее:
            худшее, где = diff, b
    return {"с_чем": имя, "общих_месяцев": len(общие),
            "наибольшее_расхождение_месячного_темпа": round(худшее, 5),
            "месяц": где}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    сегодня = date.today().isoformat()

    imf = {c: parse_imf(get(IMF.format(c=c))) for c in ("RUS", "CHN", "USA", "GBR")}
    fred = parse_fred(get(FRED))
    ons, ons_release = parse_ons(get(ONS))
    estat, estat_upd = parse_estat(get(ESTAT))
    ecb = parse_ecb(get(ECB))

    ряды = {
        "RUB": dict(
            страна="Россия", индекс="ИПЦ, все товары и услуги (CPI, COICOP _T)",
            база=imf["RUS"][1] + " = 100", точки=imf["RUS"][0],
            url=IMF.format(c="RUS"), первоисточник="Росстат",
            официальный=False,
            зеркало="МВФ, набор CPI (IMF.STA:CPI) — Росстат недоступен из песочницы: "
                    "rosstat.gov.ru/gks.ru не проходят проверку TLS, fedstat.ru — 403"),
        "USD": dict(
            страна="США", индекс="CPI-U, U.S. city average, all items, "
                                  "без сезонной поправки (BLS CUUR0000SA0)",
            база="1982-84 = 100", точки=fred, url=FRED, первоисточник="U.S. BLS",
            официальный=False,
            зеркало="FRED (ФРБ Сент-Луиса), ряд CPIAUCNS = BLS CUUR0000SA0 — "
                    "bls.gov отвечает 403, API BLS без ключа исчерпал суточный лимит",
            сверка=сверка(fred, imf["USA"][0], "МВФ CPI USA")),
        "EUR": dict(
            страна="Еврозона (EA, меняющийся состав)",
            индекс="HICP, все товары и услуги, ECOICOP ver.2 (Eurostat prc_hicp_minr, TOTAL)",
            база="2025 = 100", точки=estat, url=ESTAT, первоисточник="Eurostat",
            официальный=True, обновлено_источником=estat_upd,
            сверка=сверка(estat, ecb, "ЕЦБ ICP.M.U2.N.000000.4.INX")),
        "CNY": dict(
            страна="Китай", индекс="ИПЦ, все товары и услуги (CPI, COICOP _T)",
            база=imf["CHN"][1] + " = 100", точки=imf["CHN"][0],
            url=IMF.format(c="CHN"), первоисточник="НБС КНР",
            официальный=False,
            зеркало="МВФ, набор CPI — data.stats.gov.cn не отвечает из песочницы; "
                    "НБС публикует только темпы, уровень ряда собран МВФ"),
        "GBP": dict(
            страна="Великобритания", индекс="CPI INDEX 00: ALL ITEMS (ONS D7BT, набор MM23)",
            база="2015 = 100", точки=ons, url=ONS, первоисточник="ONS",
            официальный=True, обновлено_источником=ons_release,
            сверка=сверка(ons, imf["GBR"][0], "МВФ CPI GBR")),
    }

    плохо = []
    out_ряды = {}
    for вал, р in ряды.items():
        pts = р.pop("точки")
        if not pts:
            плохо.append(f"{вал}: источник не дал ни одной точки")
            continue
        с = р.get("сверка")
        # Гейт — только для зеркал: официальный ряд первичен, сверка при нём
        # справочная (ряд МВФ по Великобритании расходится с D7BT до 0,3 п.п.
        # в месяц — у МВФ другая редакция ряда; верим ONS).
        if с and not р["официальный"] and с["наибольшее_расхождение_месячного_темпа"] > ПОРОГ_СВЕРКИ:
            плохо.append(f"{вал}: расхождение с {с['с_чем']} {с}")
        url = р["url"]
        р["первый_месяц"], р["последний_месяц"] = min(pts), max(pts)
        р["точек"] = len(pts)
        р["точки"] = [{"месяц": k, "значение": pts[k], "источник": url, "выгружено": сегодня}
                      for k in sorted(pts)]
        out_ряды[вал] = р
        print(f"{вал}: {len(pts)} точек, {min(pts)} … {max(pts)}, "
              f"{'официальный' if р['официальный'] else 'зеркало'}"
              + (f", сверка {с['наибольшее_расхождение_месячного_темпа']}" if с else ""))

    if плохо:
        print("ОТКАЗ — не записано:\n  " + "\n  ".join(плохо), file=sys.stderr)
        return 1

    doc = {
        "описание": "Помесячные индексы потребительских цен по валютам цен: множитель "
                    "индексации цены на инфляцию (library/inflation.py). Числа — ровно "
                    "как у источника, без досчёта; месяца нет у источника — нет и здесь.",
        "выбор_индекса": "ИПЦ (потребительские цены), а не ИЦП: ИЦП России и Китая — "
                         "индексы всей промышленности с весом сырья и энергии, ходили "
                         "на ±20 % в год вслед за нефтью и металлом; у Китая уровень "
                         "ряда НБС не публикует ни для ИПЦ, ни для ИЦП; ИПЦ есть у всех "
                         "пяти валют одним определением и почти не пересматривается. "
                         "Для промышленных запчастей ИПЦ — приближение.",
        "выгружено": сегодня,
        "база_разная": "У каждого ряда своя база; коэффициент — отношение двух точек "
                       "ОДНОГО ряда, поэтому база не важна.",
        "ряды": out_ряды,
    }
    if a.dry_run:
        print("--dry-run: не записано")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"записано: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
