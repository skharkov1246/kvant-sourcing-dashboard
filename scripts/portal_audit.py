#!/usr/bin/env python3
"""Ревизия вкладок портала: насколько адекватно и насколько полезно то, что видит сорсер.

ЗАЧЕМ. Владелец 24.09.2026: «на ещё одну пятую ресурса постоянно просто
проходиться по всем вкладкам и смотреть, насколько адекватна информация там и
насколько она полезна». Глазами это делалось по случаю — и по случаю находилось:
код «SS316» с «73 сделками», бренды номерами СП-176, имя компании «supremevalves».
Здесь то же самое сделано замером: каждая вкладка читается ровно тем снимком KV,
который показывает страница, и по каждому полю считается, у скольких значений
дефект, а по вкладке — сколько в ней пользы для сорсера.

ЧТО ЧИТАЕТСЯ. Те же ключи, что читает воркер (public/_worker.js), без правки:
  · /suppliers    — suppliers:v1 и котировки crossref:v1 + crossref:list:00..07;
  · /nomenclature — crossref:v1, части списка, корзины crossref:offers:00..31;
  · /brands       — brands:v1, brands:links:v1, brands:pairs:v1, brands:codes:00..15;
  · /counters     — counters:v1;
  · /library      — library:v2:current и блобы ревизии (обход деревьев catalog и
                    directory, как у scripts/publish_library_v2.py);
  · словарь брендов dict/oem.json — файл репозитория, эталон разрешения бренда.
Клиент KV — класс Cloudflare из scripts/publish_suppliers.py (и его наследник
из publish_library_v2.py для библиотеки) с ЗАКРЫТЫМ списком ключей и запретом
записи: ревизия не может переписать ни один снимок.

ЧТО ПЕЧАТАЕТСЯ. Только агрегаты (CLAUDE.md, правило 17): код проверки, подпись,
сколько значений проверено, сколько дефектных и доля. Ни имени компании, ни
кода, ни ИНН, ни домена. Там, где форма дефекта помогает найти причину, — ОБРАЗЕЦ:
цифры заменены на «9», латинские буквы на «a», кириллица на «я» («aa999»,
«99aa»). Образец не называет ни одного значения.

ЗАПИСЬ. Итог (только агрегаты) кладётся в KV-ключ audit:v1 с датой — чтобы
вкладка «Счётчики» могла его показать, — и только при AUDIT_APPLY=1. Писатель —
отдельный наследник с единственным ключом в закрытом списке. Неполный итог не
пишется (отказ_записи): упавшая вкладка, обход библиотеки на пределе, битый
ключ, --only, --prev-dir, --from-dir, нет правила кода. Прошлый audit:v1
читается перед прогоном: рядом с каждой проверкой печатается, сколько дефектных
было в прошлый раз, — у скольких стало хуже, видно сразу (правило 0).

ПРАВИЛО «У СКОЛЬКИХ ХУЖЕ» ПОИМЁННО. Прошлый снимок номенклатуры можно подать
папкой (--prev-dir): тогда по каждому ключу позиции считается, сколько позиций
потеряли бренд, цену, компанию или выбор. Итог — числом, без ключей.

    CLOUDFLARE_ACCOUNT_ID=… CLOUDFLARE_API_TOKEN=… python scripts/portal_audit.py
    python scripts/portal_audit.py --from-dir DIR            # снимки файлами
    python scripts/portal_audit.py --from-dir DIR --prev-dir ПРОШЛЫЙ
    AUDIT_APPLY=1 python scripts/portal_audit.py             # и записать audit:v1

Файлы папки называются как у публикаторов с --out: ключ, где «:» заменено на
«_», плюс «.json» (crossref_list_03.json, brands_codes_11.json,
library_v2_blob_<sha256>.json).
"""
from __future__ import annotations

import argparse
import collections
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pnw" / "tools"))

from library import (brand_registry, brands, codes_sql, company_names, crossref, doc_side,  # noqa: E402
                     docfilter, oem_kind, offer_terms, quotes)
# Правовая форма, описание вместо имени и указание к закупке — правила словаря
# брендов (library/oem_kind.py): по ним сборщик ставит вид записи dict/oem.json,
# а ревизия ищет дефекты. Одно правило в одном месте.
from library.oem_kind import без_формы, похоже_на_описание  # noqa: E402
# Контрольная сумма ИНН — в company_names: её же зовёт связь реестров
# (library/supplier_link.py). Одно правило в одном месте.
from library.company_names import инн_верен  # noqa: E402
from kv_number import luhn  # noqa: E402

КЛЮЧ_РЕВИЗИИ = "audit:v1"
КЛЮЧ_ПОСТАВЩИКОВ = "suppliers:v1"
КЛЮЧ_СЧЁТЧИКОВ = "counters:v1"
КЛЮЧ_БИБЛИОТЕКИ = "library:v2:current"
ПРЕФИКС_БЛОБА = "library:v2:blob:"
# Что ревизия читает. Список закрытый, как у публикаторов: ключа вне его клиент
# не спросит. Запись — только КЛЮЧ_РЕВИЗИИ, и только отдельным писателем.
КЛЮЧИ_ЧТЕНИЯ = ((КЛЮЧ_ПОСТАВЩИКОВ,) + crossref.ВСЕ_КЛЮЧИ + brands.ВСЕ_КЛЮЧИ
                + (КЛЮЧ_СЧЁТЧИКОВ, КЛЮЧ_РЕВИЗИИ))
ПРЕДЕЛ_РЕВИЗИИ = 1 << 20
# Предел API Cloudflare — 1200 запросов за 5 минут на токен, и токен у ревизии
# тот же, что у публикаторов. Пауза 0,5 с держит ревизию на 600 — половине
# предела: публикатор, пошедший рядом, не получит 429 на своём PUT (а у PUT
# повтора нет — он кончается CLOUDFLARE_WRITE_OUTCOME_UNCONFIRMED). Прогон
# Actions к тому же не стартует, пока идёт публикатор KV (portal-audit.yml).
ПАУЗА_KV = 0.5
# Сколько блобов библиотеки читать за прогон. Обход — N/50 листьев каталога и
# столько же справочника; дальше ревизия останавливается и говорит об этом.
# 3000 × 0,5 с = 25 минут — внутри timeout-minutes прогона (40).
БЛОБОВ_БИБЛИОТЕКИ = int(os.environ.get("AUDIT_LIBRARY_MAX_BLOBS") or 3000)

# Пороги свежести. Реестр поставщиков публикуется только руками
# (suppliers-publish.yml без расписания), номенклатура и бренды — ночью.
ВОЗРАСТ_РЕЕСТРА_Д = 7
ВОЗРАСТ_НОМЕНКЛАТУРЫ_Ч = 36
ВОЗРАСТ_БРЕНДОВ_Ч = 48
ВОЗРАСТ_СЧЁТЧИКОВ_Ч = 30
ВОЗРАСТ_ИНКРЕМЕНТА_Ч = 36
РАЗНИЦА_СНИМКОВ_Ч = 24
РАННЯЯ_ДАТА = date(2015, 1, 1)

ISO_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
ДАТА_RX = re.compile(r"\d{4}-\d{2}-\d{2}")
НОМЕР_KV = re.compile(r"KV-([SG])-(\d{6})-(\d)")
ХОСТ = re.compile(r"[a-zа-я0-9-]+(\.[a-zа-я0-9-]+)+")
ДОМЕН_ИМЕНИ = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
ДОМЕН_КОМПАНИИ = re.compile(r"[\w.-]+\.(ru|com|cn|de|net|org)", re.I)
ВАЛЮТА_RX = re.compile(r"[A-Z]{3}")
# Действующие коды ISO 4217 (без металлов, расчётных и тестовых X-кодов). Не
# входящая сюда валюта — дефект написания: «РУБ», «usd», «RUR» страница сравнит
# с RUB и USD как чужую валюту, и «сравнимы» не поставится.
ISO_ВАЛЮТЫ = frozenset((
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP "
    "BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP "
    "GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR "
    "KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK "
    "MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR "
    "SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD "
    "TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWG").split())
# Потолок цены за единицу: около 300 млн долларов — ГТУ или ГПА целиком — в
# пересчёте по ПОРЯДКУ курса (точный курс не нужен: выше потолка — склейка ячеек,
# а не цена). Один порог на все валюты судил бы тенге как доллары. Валюта не из
# списка получает щедрый множитель: правило 7, обвинять только уверенно.
ПОТОЛОК_ЦЕНЫ_USD = 3e8
ПОРЯДОК_КУРСА = {"USD": 1, "EUR": 1, "GBP": 1, "CHF": 1, "AED": 4, "BYN": 4, "PLN": 4, "SGD": 2,
                 "CNY": 8, "SEK": 11, "NOK": 11, "DKK": 7, "TRY": 40, "UAH": 45, "RUB": 100,
                 "INR": 90, "JPY": 160, "KZT": 550, "UZS": 13000, "KRW": 1500, "IDR": 17000,
                 "VND": 26000, "IRR": 50000}


def потолок_цены(валюта) -> float:
    return ПОТОЛОК_ЦЕНЫ_USD * ПОРЯДОК_КУРСА.get(str(валюта or "").upper(), 50000)


# Базисы — все, что пишут разборщики (offer_terms: ещё DDU, DES, DEQ; quotes).
ИНКОТЕРМС = frozenset(offer_terms.БАЗИСЫ) | frozenset(quotes.БАЗИСЫ)


def базис_известен(s) -> bool:
    """Первое латинское слово базиса — Incoterms: «DAP,», «FCA-Шанхай» годны."""
    m = re.match(r"[A-Za-z]+", str(s or "").strip())
    return bool(m) and m[0].upper() in ИНКОТЕРМС
ПОЧТОВЫЕ = frozenset(("gmail.com", "mail.ru", "yandex.ru", "ya.ru", "bk.ru", "list.ru",
                      "inbox.ru", "rambler.ru", "outlook.com", "hotmail.com", "qq.com", "163.com"))
# Правовая форма как слово целиком. «co» и «as» здесь только для проверки
# «имя состоит из одной формы»: в составе имени они не обвиняют.
ПРАВОВЫЕ = frozenset(("ооо", "оао", "зао", "пао", "ао", "ип", "llc", "ltd", "inc", "gmbh",
                      "corp", "plc", "ag", "ab", "bv", "nv", "sa", "sas", "srl", "co", "kg",
                      "oy", "oyj", "pte", "limited", "as"))
# Изготовитель со словом поставщика: форма из этого списка значит, что в ячейку
# записали себя или контрагента, а не марку (опись вкладки, 24.09.2026).
ПРАВОВЫЕ_ОЕМ = frozenset(("ооо", "оао", "зао", "пао", "ао", "ип", "llc", "gmbh", "ltd"))
ШТУЧНЫЕ = frozenset(("шт", "шт.", "pcs", "pc", "ea", "компл", "компл.", "комплект"))
# Синонимы единиц: «шт», «шт.», «pcs» и «ea» — одна единица, а не расхождение.
ЕДИНИЦЫ = {"шт": "шт", "штука": "шт", "штук": "шт", "штуки": "шт", "pcs": "шт", "pc": "шт",
           "ea": "шт", "each": "шт", "piece": "шт", "pieces": "шт", "ед": "шт",
           "компл": "компл", "комплект": "компл", "к-т": "компл", "кт": "компл", "set": "компл",
           "sets": "компл", "м": "м", "m": "м", "метр": "м", "кг": "кг", "kg": "кг", "л": "л",
           "l": "л", "т": "т", "t": "т"}


def единица(s) -> str:
    t = норм(s).strip().rstrip(".")
    return ЕДИНИЦЫ.get(t, t)


# Хвост «склейки ячеек»: единицы и валюты, которые стоят между количеством,
# ценой и суммой, когда строку таблицы склеили в наименование.
_ХВОСТОВЫЕ = frozenset(("шт", "шт.", "pcs", "pcs.", "pc", "ea", "компл", "компл.", "кг", "kg", "м",
                        "м.", "л", "т", "ед", "ед.", "уп", "уп.", "руб", "руб.", "rub", "usd", "eur",
                        "cny", "₽", "$", "€", "-", "–"))
_ЧИСЛО_ХВОСТА = re.compile(r"\(?(\d+(?:[.,]\d+)?)\)?")


def склейка_ячеек(имя) -> bool:
    """Наименование со склеенными ячейками «количество цена сумма» в хвосте.

    Одно правило на три вкладки (номенклатура, поставщики, бренды). Хвост —
    конечные токены-числа и единицы/валюты между ними. Склейка — тройка чисел
    подряд, где a × b = c с допуском разборщика (quotes.ДОПУСК_*): «8 3200 25600»,
    «8 3200.0 шт 25600.0»; либо три числа и больше, из них два дробных. Размеры
    («Кольцо 12.42 x 1.78 NBR», «Кабель 3х2,5 0,66 кВ»), мощность и напор («ЦНС
    300 180», «ТМ 1 000») не обвиняются: там нет тройки с произведением."""
    токены = str(имя or "").split()
    числа = []
    for t in reversed(токены):
        m = _ЧИСЛО_ХВОСТА.fullmatch(t)
        if m:
            числа.append(m[1])
            continue
        if t.lower() in _ХВОСТОВЫЕ:
            continue
        break
    числа.reverse()
    if len(числа) < 3:
        return False
    if sum(1 for x in числа if re.search(r"[.,]", x)) >= 2:
        return True
    зн = [float(x.replace(",", ".")) for x in числа]
    for a, b, c in zip(зн, зн[1:], зн[2:]):
        if a > 0 and b > 0 and c > max(a, b) and abs(a * b - c) <= max(
                quotes.ДОПУСК_МИН, min(a, b) * quotes.ДОПУСК_НА_ЕДИНИЦУ):
            return True
    return False
# Служебные слова и страны там, где ждут бренд. Закрытый список: словарь
# запроса брендов (codes_sql) плюс то, что названо в описи вкладок.
СЛУЖЕБНЫЕ = (frozenset(codes_sql.JUNK_WORDS) | frozenset(codes_sql.COUNTRIES)
             | frozenset(("китай", "россия", "оригинал", "аналог", "нет", "n/a", "производитель",
                          "oem", "unknown", "не указан", "не указано", "неизвестно")))
СЛОВА_НЕ_БРЕНДА = re.compile(r"(?<![а-яёa-z])(аналог|оригинал|не указан|китай)", re.I)
# Указание к закупке вместо марки — выражение запроса брендов (codes_sql), где
# слово привязано к началу: Python-оригинал из build_dict ищет подстроку, и
# «Восток» у него — «сток». Порт в Python — oem_kind.не_компания(): им же сборщик
# словаря ставит вид «указание».
НЕ_КОМПАНИЯ = oem_kind.не_компания()

ОТКУДА_ИМЯ = frozenset(("bitrix:title", "bitrix:requisite", "написание", "реестр", "домен",
                        "ключ реестра"))
ИМЯ_НАСТОЯЩЕЕ = frozenset(("bitrix:title", "bitrix:requisite", "написание", "реестр"))
МЕТКИ_ОТКУДА = frozenset(("bitrix", "реквизиты портала", "реквизиты Битрикса",
                          "сведение реестров"))
ПРИЧИНЫ_СВЕДЕНИЯ = frozenset(("один источник, сливать не с чем",
                              "домен совпал у двух и более источников",
                              "имя совпало, но домен есть не у всех — проверить нечем",
                              "имя совпало, домена нет ни у одного источника"))
СХЛОПНУТЫ = re.compile(r"схлопнуты \d+ карточки портала под одним именем — "
                       r"задвоение Bitrix, подтвердить человеком")
ПРИЧИНЫ_ОЧЕРЕДИ = frozenset(("несколько правовых форм",
                             "домен склеил разноимённые карточки портала"))
ВИДЫ_АНАЛОГОВ = frozenset(("номер изготовителя", "замена", "аналог", "наш номер"))
РОЛИ_ИЗГОТОВИТЕЛЕЙ = frozenset(("OEM", "ODM", "дистрибьютор", "сервис", "трейдер",
                                "изготовитель", "изготовитель (из каталога)"))
# Стороны файла — словарь library/doc_side (ЗАКАЗЧИК, МЫ, ПОСТАВЩИК, ВНУТРЕННИЙ,
# НЕИЗВЕСТНО) плюс две метки замера кодов для строк без файла и без разметки.
СТОРОНЫ = frozenset((doc_side.ЗАКАЗЧИК, doc_side.МЫ, doc_side.ПОСТАВЩИК, doc_side.ВНУТРЕННИЙ,
                     doc_side.НЕИЗВЕСТНО, "без файла", "сторона не проставлена"))

# Библиотека: словари страницы public/library.html.
УРОВНИ_ПРОВЕРКИ = frozenset(("verified", "high", "reviewed", "confirmed",
                             "verified_observations_with_explicit_open_questions", "partial",
                             "medium", "med", "low", "hypothesis", "unverified", "draft"))
ПРОВЕРЕНО = frozenset(("verified", "high", "reviewed", "confirmed",
                       "verified_observations_with_explicit_open_questions"))
ВИДНО_КАК_ТРЕБУЕТ = frozenset(("low", "unverified", "draft"))
НЕ_ВИДНО_КАК_ТРЕБУЕТ = frozenset(("hypothesis", "partial", "medium", "med"))
РОЛИ_БИБЛИОТЕКИ = frozenset(("maker", "trader", "analog", "service", "unknown"))
ТИПЫ_ЦЕНЫ = frozenset(("Расценка из сделки", "Наше КП заказчику", "Предложение поставщика",
                       "Открытая цена из источника"))
НАПРАВЛЕНИЯ = frozenset(("input_estimate", "outgoing_offer"))
ЦЕНЫ_ПОСТАВЩИКА = frozenset(("Предложение поставщика", "Открытая цена из источника"))
ЗАГЛУШКИ_ПОСТАВЩИКА = frozenset(("Не указан", "Автор не подтверждён"))
ВАЛЮТЫ_БИБЛИОТЕКИ = frozenset(("RUB", "USD", "EUR", "CNY", "AED", "GBP", "INR"))
ЛОКАТОР = frozenset(("page", "pages", "sheet", "cells", "cell", "row", "rows", "column",
                     "json_pointer", "section", "table", "line", "lines", "paragraph"))

# Счётчики: поля последней точки «коды_и_цены», которые читает страница.
ПОЛЯ_СЧЁТЧИКОВ = ("asked", "rows_asked", "with_kp", "price_rows", "no_price", "rows_without",
                  "other_feed", "price_codes", "price_asked", "catalog", "catalog_priced",
                  "catalog_asked", "price_in_catalog", "plausible", "no_digit",
                  "shorter_than_four", "longer_than_25")
ЗАМЕР_КОДОВ = "коды_и_цены"
ПРОГОН_RX = re.compile(r"\d{6,}|вручную-\d{8}T\d{6}Z")

КЛАСС_НЕ_КОДА = getattr(docfilter, "класс_не_кода", None)
# Без правила кода (ветка без docfilter.класс_не_кода) проверки «марка, размер,
# стандарт» НЕ вызываются вовсе: иначе каждое значение шло бы в счёт как
# проверенное и чистое, таблица показала бы «0 дефектных», а доли
# правдоподобных кодов завысились бы. Так они уходят в «не к чему применить»,
# а запись итога в audit:v1 отказывает (main).


def есть_правило_кода() -> bool:
    return КЛАСС_НЕ_КОДА is not None


# ── Общие правила значений ───────────────────────────────────────────────────

def норм(s) -> str:
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def образец(s) -> str:
    """Форма значения без самого значения: цифры → 9, буквы → a / я."""
    out = []
    for ch in str(s)[:24]:
        if ch.isdigit():
            out.append("9")
        elif "a" <= ch.lower() <= "z":
            out.append("a")
        elif ch.isalpha():
            out.append("я")
        else:
            out.append(ch)
    return "".join(out)


def целое(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def число(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def время(s):
    if not isinstance(s, str) or not ISO_Z.fullmatch(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def время_с_зоной(s):
    """ISO-8601 с зоной (Z или ±ЧЧ:ММ) → datetime; без зоны — None."""
    if not isinstance(s, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})", s):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def день(s):
    if not isinstance(s, str) or not ДАТА_RX.fullmatch(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def день_свободно(s):
    """Дата из начала строки: «2026-09-01», «2026-09-01T…», «01.09.2026»."""
    if not isinstance(s, str):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s.strip())
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def только_цифры(s) -> bool:
    t = str(s if s is not None else "")
    return bool(re.fullmatch(r"[\d\s,.;]+", t) and re.search(r"\d", t))


def служебное(s) -> bool:
    """Пометка незнания, страна или служебное слово там, где ждут марку."""
    t = норм(s)
    if not t.strip(" .,;:-–—/"):
        return bool(str(s or "").strip())
    t = t.strip(" .,;:")
    return (t in СЛУЖЕБНЫЕ or bool(re.match(codes_sql.UNKNOWN_RE, t))
            or bool(re.match(codes_sql.COUNTRY_RE, t)))


def класс_не_кода(s):
    return КЛАСС_НЕ_КОДА(s) if КЛАСС_НЕ_КОДА else None


def класс_написания(s):
    """Класс «не код» по НАПИСАНИЮ: то же правило docfilter, но обозначение
    «стандарт плюс размер» — «ГОСТ 9833-73 020-025-30», «DIN 471 25», «GB 276
    6205» — стандартом не считается. Ключ такого обозначения целиком совпадает
    с классом «стандарт» (docfilter судит ключ, где пробелов нет), а это номер
    детали по стандарту: второй числовой токен после номера стандарта — размер.
    Голый стандарт («ГОСТ 8752-79», «DIN 933») обвиняется, как и прежде."""
    кл = класс_не_кода(s)
    if кл == "стандарт" and sum(1 for t in str(s or "").split() if re.search(r"\d", t)) >= 2:
        return None
    return кл


def ключ_кода(s) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", str(s or "").lower().replace("ё", "е"))[:80]


def слов_в_коде(s) -> int:
    """Слов из четырёх букв и больше: «NU 316 ECP», «NJ 2312 ECML C3» — номер
    (одно слово или ни одного), «подшипник NU 316 роликовый» — наименование."""
    return sum(1 for t in str(s or "").split() if re.fullmatch(r"[A-Za-zА-Яа-яЁё]{4,}", t))


def код_правдоподобен(s) -> bool:
    """Ключ 4–25 знаков с цифрой и не марка, не размер, не стандарт."""
    k = ключ_кода(s)
    return 4 <= len(k) <= 25 and bool(re.search(r"\d", k)) and класс_не_кода(s) is None


# Марки и материалы, которых нет в правиле docfilter (правило 1: проверка не
# должна мерить публикатора его же правилом — на свежем снимке класс_не_кода
# равен нулю по построению). Ключ целиком: «316ss», «inox316», «a480» (A4-80),
# «nbr70», «inconel625»; и склейка двух свойств подряд — «ss31619mm» (марка +
# размер), «dn50pn16». Номер материала EN «1.4401» судится по написанию: ключ
# «14401» от артикула не отличить.
_СТАЛИ = "(201|202|301|302|303|304|309|310|316|317|321|347|410|416|420|430|431|440|904)(l|ln|h|ti|lh)?"
_МАТЕРИАЛ = ("{с}ss|inox({с})?|a[24](50|70|80)|(nbr|hnbr|fkm|fpm|epdm|vmq|ffkm|ptfe)[0-9]{{0,3}}"
             "|inconel[0-9]{{3}}|incoloy[0-9]{{3}}|monel[0-9]{{3}}|hastelloy[a-z]?[0-9]{{0,3}}"
             "|duplex[0-9]{{0,4}}").format(с=_СТАЛИ)
_СВОЙСТВА = "|".join([выр for _, выр in getattr(docfilter, "_КЛАССЫ_НЕ_КОДА", ())] + [_МАТЕРИАЛ])
МАТЕРИАЛ_RX = re.compile("(" + _МАТЕРИАЛ + ")")
СКЛЕЙКА_СВОЙСТВ_RX = re.compile("(" + _СВОЙСТВА + ")(" + _СВОЙСТВА + ")")


def класс_материала(s, написание=None):
    """«материал» — марка или материал вне правила кода; «склейка» — два свойства
    подряд в одном ключе; None — не то и не другое."""
    k = ключ_кода(s)
    if not k:
        return None
    if МАТЕРИАЛ_RX.fullmatch(k) or re.fullmatch(r"\s*1\.4\d{3}\s*", str(написание or "")):
        return "материал"
    if СКЛЕЙКА_СВОЙСТВ_RX.fullmatch(k):
        return "склейка"
    return None


def мусор_бренда(s) -> bool:
    t = str(s or "")
    return (похоже_на_описание(t) or служебное(t)
            or bool(СЛОВА_НЕ_БРЕНДА.search(t)) or класс_не_кода(t) == "марка")


def изготовитель_как_ключ(x, известные=frozenset()) -> bool:
    """Имя изготовителя похоже на ключ. Бренды, которые сами пишутся строчными
    (igus, ifm, skf как ключ), не обвиняются: короткое слово латиницей до 12
    знаков и имя, известное /brands, — имя, а не сжатый ключ."""
    t = str(x or "")
    if not t.strip():
        return True
    if норм(t) in известные or re.fullmatch(r"[a-z0-9]{1,12}", t):
        return False
    return company_names.как_ключ(t)


def правовая_форма_в(s, формы=ПРАВОВЫЕ_ОЕМ) -> bool:
    return any(w in формы for w in re.findall(r"[a-zа-я]+", норм(s)))


def только_форма(s) -> bool:
    return норм(s).strip(" .,«»\"'") in ПРАВОВЫЕ


def имя_ключ_компании(s) -> str:
    """Нормализованное имя компании для поиска дублей: правовая форма ОСТАЁТСЯ.

    «ООО Ромашка» и «АО Ромашка» сведение разводит намеренно (разные юрлица),
    а «ООО Ромашка» и «ООО «Ромашка»» — одно имя."""
    return re.sub(r"[^0-9a-zа-я]", "", норм(s))


def номер_верен(s) -> bool:
    m = НОМЕР_KV.fullmatch(s or "")
    return bool(m) and luhn(m[2]) == int(m[3])


def бренд_найден(имя, бренды_снимка) -> bool:
    return норм(имя) in бренды_снимка


def бренды_брендов(bv) -> set[str]:
    """Нижний регистр имён, ключей и написаний brands:v1 — как ищет /brands#n=."""
    out = set()
    for b in (bv or {}).get("brands") or []:
        if not isinstance(b, dict):
            continue
        for v in [b.get("name"), b.get("k"), *(b.get("spellings") or [])]:
            if v:
                out.add(норм(v))
    return out


def карта_брендов(словарь, bv) -> dict:
    """Ключ написания → ключи брендов: словарь dict/oem.json плюс имена, ключи и
    написания brands:v1 — всё, что /brands находит по имени."""
    карта, _ = карта_словаря_брендов(словарь)
    for b in (bv or {}).get("brands") or []:
        if isinstance(b, dict):
            for v in [b.get("name"), b.get("k"), *(b.get("spellings") or [])]:
                kн = codes_sql.ключ_написания(str(v or ""))
                if len(kн) >= 2:
                    карта[kн].add(b.get("k"))
    return карта


def форма_у_неизвестного(x, карта) -> bool:
    """Правовая форма в ячейке изготовителя — дефект, только если имя без формы
    не находится ни в словаре, ни в /brands. «Bosch Rexroth GmbH», «АО Силовые
    машины» — изготовители со своей формой; «ООО Ромашка» — поставщик вписал
    себя или контрагента."""
    if not правовая_форма_в(x):
        return False
    остаток = без_формы(x)
    if not ключ_кода(остаток):
        return True
    return brand_registry.разрешить(остаток, карта)[0] == brand_registry.ОЧЕРЕДЬ


def доля_текстом(числ, знам) -> str:
    return f"{100 * числ / знам:5.1f} %" if знам else "    — "


# ── Счёт ─────────────────────────────────────────────────────────────────────

class Вкладка:
    """Проверки и польза одной вкладки. Коды проверок — закрытый список:
    неизвестный код — ошибка программы, а не новая строка отчёта."""

    def __init__(self, ид, имя, проверки: dict, польза: dict):
        self.ид, self.имя = ид, имя
        self.подписи, self.подписи_пользы = проверки, польза
        self.счета = {к: [0, 0] for к in проверки}
        self.формы: dict[str, collections.Counter] = {}
        self.доли: dict[str, tuple] = {}
        self.снимки: dict[str, dict] = {}
        self.заметки: list[str] = []
        self.упала: str | None = None
        # Вкладка посчитана не целиком (обход библиотеки встал на пределе):
        # такой итог не должен затирать полный в audit:v1.
        self.неполная = False

    def счёт(self, код, дефект, вес=1, форма=None) -> bool:
        с = self.счета[код]
        с[0] += вес
        if дефект:
            с[1] += вес
            if форма is not None:
                self.формы.setdefault(код, collections.Counter())[образец(форма)] += 1
        return bool(дефект)

    def доля(self, код, числ, знам):
        assert код in self.подписи_пользы, код
        self.доли[код] = (числ, знам)

    def заметка(self, текст):
        self.заметки.append(текст)

    def снимок(self, ключ, снимки: "Снимки", сейчас, published=None):
        размер = снимки.размер.get(ключ)
        t = время(published)
        self.снимки[ключ] = {"present": размер is not None, "bytes": размер,
                             "age_h": round(возраст_ч(t, сейчас), 1) if t else None}

    def средняя_доля_дефектов(self) -> float | None:
        доли = [д / п for п, д in self.счета.values() if п]
        return sum(доли) / len(доли) if доли else None

    def дефектных_штук(self) -> int:
        """Сколько дефектных значений всего — в штуках, а не в долях: дефект в
        тысячах видимых строк не должен тонуть в средней по сотне проверок."""
        return sum(д for _, д in self.счета.values())

    def средняя_польза(self) -> float | None:
        доли = [ч / з for к, (ч, з) in self.доли.items()
                if з and not self.подписи_пользы[к].startswith("(справочно)")]
        return sum(доли) / len(доли) if доли else None

    def итог(self) -> dict:
        проверки = []
        for к, (п, д) in self.счета.items():
            строка = {"id": к, "label": self.подписи[к], "checked": п, "bad": д}
            if к in self.формы:
                строка["forms"] = self.формы[к].most_common(5)
            проверки.append(строка)
        польза = [{"id": к, "label": self.подписи_пользы[к], "num": ч, "den": з}
                  for к, (ч, з) in self.доли.items()]
        сд, сп = self.средняя_доля_дефектов(), self.средняя_польза()
        return {"id": self.ид, "name": self.имя, "snapshots": self.снимки,
                "checks": проверки, "useful": польза, "notes": self.заметки,
                **({"failed": self.упала} if self.упала else {}),
                **({"partial": True} if self.неполная else {}),
                "score": {"checks": sum(1 for п, _ in self.счета.values() if п),
                          "bad_total": self.дефектных_штук(),
                          "checks_with_bad": sum(1 for п, д in self.счета.values() if п and д),
                          "mean_bad_share": round(сд, 4) if сд is not None else None,
                          "mean_useful": round(сп, 4) if сп is not None else None}}


def возраст_ч(t, сейчас) -> float:
    return (сейчас - t).total_seconds() / 3600


# ── Источники снимков ────────────────────────────────────────────────────────

def имя_файла(ключ: str) -> str:
    return ключ.replace(":", "_") + ".json"


class Папка:
    """Снимки файлами — как их кладут публикаторы с --out."""

    вид = "dir"

    def __init__(self, путь):
        self.путь = Path(путь)

    def get(self, ключ):
        p = self.путь / имя_файла(ключ)
        return p.read_bytes() if p.is_file() else None


class ИсточникKV:
    """Два клиента на один namespace: снимки страниц и блобы библиотеки."""

    вид = "kv"

    def __init__(self, снимки, библиотека, namespace, sleep=time.sleep):
        self.снимки, self.библиотека, self.ns = снимки, библиотека, namespace
        self.sleep = sleep
        self.запросов = 0

    def get(self, ключ):
        if self.запросов:
            self.sleep(ПАУЗА_KV)
        self.запросов += 1
        клиент = self.библиотека if ключ.startswith("library:") else self.снимки
        return клиент.get(self.ns, ключ)


class _ДляХранилища:
    """Адаптер для publish_library_v2.Store: тот зовёт cf.get(namespace, key)."""

    def __init__(self, источник):
        self.источник = источник
        self.прочитано = 0

    def get(self, _namespace, ключ):
        self.прочитано += 1
        if self.прочитано > БЛОБОВ_БИБЛИОТЕКИ:
            raise ПределОбхода()
        return self.источник.get(ключ)


class ПределОбхода(Exception):
    pass


class Снимки:
    """Разобранные снимки с кешем; размер и ошибки чтения — отдельно."""

    def __init__(self, источник):
        self.источник = источник
        self.кеш: dict = {}
        self.размер: dict[str, int] = {}
        self.битые: set[str] = set()

    def json(self, ключ):
        if ключ in self.кеш:
            return self.кеш[ключ]
        raw = self.источник.get(ключ)
        значение = None
        if raw is not None:
            self.размер[ключ] = len(raw)
            try:
                значение = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.битые.add(ключ)
        self.кеш[ключ] = значение
        return значение


def _модуль(имя, файл):
    """Модуль из scripts/ как библиотека: scripts/ — не пакет."""
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(имя, ROOT / "scripts" / файл)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def источник_kv(opener=None, sleep=time.sleep):
    """Читатель KV для прогона. opener — подмена транспорта в тестах."""
    ps = _модуль("kvant_publish_suppliers", "publish_suppliers.py")
    lib2 = _модуль("kvant_publish_library_v2", "publish_library_v2.py")

    # ЗАПРЕТ ЗАПИСИ — НА УРОВНЕ ТРАНСПОРТА, А НЕ ИМЕНИ МЕТОДА. put и preserve
    # перекрыты для ясного отказа, но PUT можно послать и через envelope() или
    # call(): поэтому call() пропускает только GET.
    class ЧтениеСнимков(ps.Cloudflare):
        КЛЮЧИ = КЛЮЧИ_ЧТЕНИЯ

        def call(self, method, path, body=None, missing=False):
            if method != "GET" or body is not None:
                raise ps.PublishError("AUDIT_READ_ONLY")
            return super().call(method, path, body, missing)

        def put(self, *_a, **_k):
            raise ps.PublishError("AUDIT_READ_ONLY")

    class ЧтениеБиблиотеки(lib2.Cloudflare):
        # Закрытый список и у библиотеки: указатель и блобы по sha256. Черновики
        # (library:draft:*), история и library:v1 родительскому классу доступны,
        # ревизии — нет.
        def value_path(self, namespace, key):
            хвост = key[len(ПРЕФИКС_БЛОБА):] if key.startswith(ПРЕФИКС_БЛОБА) else None
            if key != КЛЮЧ_БИБЛИОТЕКИ and not (хвост is not None and re.fullmatch(r"[0-9a-f]{64}", хвост)):
                raise lib2.v1.PublishError("INVALID_KV_KEY")
            return super().value_path(namespace, key)

        def call(self, method, path, body=None, missing=False):
            if method != "GET" or body is not None:
                raise lib2.v1.PublishError("AUDIT_READ_ONLY")
            return super().call(method, path, body, missing)

        def put(self, *_a, **_k):
            raise lib2.v1.PublishError("AUDIT_READ_ONLY")

        preserve = put

    счёт = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    токен = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    снимки = ЧтениеСнимков(счёт, токен, opener=opener, sleep=sleep)
    ns = снимки.namespace()
    библиотека = ЧтениеБиблиотеки(счёт, токен, opener=opener, sleep=sleep)
    return ИсточникKV(снимки, библиотека, ns, sleep=sleep), ps


def записать_ревизию(raw: bytes, ps=None, opener=None):
    ps = ps or _модуль("kvant_publish_suppliers", "publish_suppliers.py")

    class ЗаписьРевизии(ps.Cloudflare):
        КЛЮЧИ = (КЛЮЧ_РЕВИЗИИ,)

    ps.require(len(raw) <= ПРЕДЕЛ_РЕВИЗИИ, "AUDIT_TOO_LARGE")
    cf = ЗаписьРевизии(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
                       os.environ.get("CLOUDFLARE_API_TOKEN", ""), opener=opener)
    cf.put(cf.namespace(), КЛЮЧ_РЕВИЗИИ, raw)


# ── /suppliers ───────────────────────────────────────────────────────────────

ПРОВЕРКИ_ПОСТАВЩИКОВ = {
    "s.present": "снимка suppliers:v1 нет или он не читается",
    "s.version": "version ≠ 1 — воркер отвечает 503",
    "s.published": "published_at не ISO-8601 с Z",
    "s.age": f"снимок старше {ВОЗРАСТ_РЕЕСТРА_Д} дней (публикуется только руками)",
    "s.cross_age": "реестр и котировки собраны с разницей больше суток",
    "s.totals": "счётчик totals.* не сходится с пересчётом по записям",
    "s.review": "review_open меньше очереди ИНН",
    "s.review_tail": "review_open > 0 при пустой очереди ИНН — хвост прежних прогонов",
    "s.caveat": "оговорки о склейке нет или её число не сходится с записями",
    "s.number_fmt": "номер не KV-S/G-NNNNNN-C или не сходится контрольная цифра",
    "s.number_dup": "номер повторяется",
    "s.number_wait": "номер пуст ⇔ «ждёт ИНН» нарушено",
    "s.number_group": "номер группы KV-G-: из /brands карточку не открыть",
    "s.name_empty": "имя пусто",
    "s.name_key": "имя похоже на ключ (как_ключ: «bitrix:NNN», сжатый norm_name)",
    "s.name_latin": "имя — строчная латиница без пробелов длиннее 12 или вида «x:123»",
    "s.name_domain": "имя похоже на домен, а источник имени не «домен»",
    "s.name_from_contra": "источник имени — карточка, реквизиты, написание или реестр, а имя — ключ",
    "s.name_junk": "в имени @, http, перевод строки, одна правовая форма или > 150 знаков",
    "s.name_dup": "одно имя у двух и более записей, не разведённых ИНН или доменом (несведённый дубль)",
    "s.name_from_vocab": "источник имени вне словаря страницы",
    "s.name_from_absent": "источника имени нет ни у одной записи (вид имён не применён)",
    "s.inn_sum": "ИНН (страна пуста или RU) не 10/12 цифр или не сходится контрольная сумма",
    "s.inn_stub": "ИНН-заглушка: одна повторённая цифра",
    "s.inn_letters": "буквы в ИНН (иностранный номер в виде inn)",
    "s.inn_multi": "больше одного ИНН у записи (страница не помечает)",
    "s.inn_dup": "один ИНН у двух и более записей",
    "s.inn_many": "ИНН у четырёх и более записей (вероятно, наш собственный)",
    "s.domain_host": "домен — не имя хоста (схема, путь, @, пробел, заглавные)",
    "s.domain_mail": "почтовый сервис вместо сайта компании",
    "s.domain_dup": "один домен у двух и более записей, не разведённых ИНН (группа компаний — не дубль)",
    "s.rfq_type": "история запросов: не целые или отрицательные числа",
    "s.rfq_sum": "история запросов: ответил + промолчал + без исхода ≠ отправлено",
    "s.rfq_order": "история запросов: КП > ответов или ответов > отправлено",
    "s.rfq_cards": "история запросов: карточек меньше, чем отправлено",
    "s.rfq_zero": "история запросов есть при нуле отправленных",
    "s.sources_vocab": "метка «Откуда» вне закрытого набора",
    "s.sources_empty": "«Откуда» пусто — сирота без карточки, ИНН и домена",
    "s.sources_flat": "столбец «Откуда» почти не различает записи (≤ 4 сочетаний на 50+ записей)",
    "s.merged_vocab": "«Чем слито» вне списка причин сведения",
    "s.merged_empty": "«Чем слито» пусто",
    "s.country_dead": "«Страна» пуста у всех записей (мёртвое поле)",
    "s.country_iso": "страна не ISO-3166 из двух букв",
    "s.country_ru": "российский ИНН, а страна не RU",
    "s.dead_labels": "подпись карточки без данных у всех записей (Правовая форма, Позиций в каталоге)",
    "s.status_hidden": "статус не active, а страница его не показывает",
    "q.names_key": "очередь ИНН: имя похоже на ключ",
    "q.reason": "очередь ИНН: причина вне списка сведения",
    "q.card_fmt": "очередь ИНН: номер карточки не число > 0",
    "q.card_dup": "очередь ИНН: одна карточка в двух и более строках",
    "q.names_empty": "очередь ИНН: строка без имени",
    "q.stale": "очередь ИНН: имя совпадает с записью, у которой номер уже есть (тёзки с разными ИНН не в счёт)",
    "x.present": "котировки: нет заголовка или части списка",
    "x.ent_missing": "строки котировок компании, чей ent не из номеров реестра",
    "x.ent_null": "строки котировок без ent — не видны ни в одной карточке",
    "x.brand_digits": "бренды или oem компании — номера СП-176",
    "x.brand_junk": "бренды или oem компании — описание, служебное слово или марка",
    "x.link_n": "ссылка /brands#n=<oem> не находит бренд",
    "x.link_s": "ссылка /brands#s=<ent> не находит поставщика",
    "x.pn_class": "номер позиции — марка, размер или стандарт",
    "x.pk_short": "ключ позиции короче 3 знаков или только цифры до трёх знаков",
    "x.pname": "наименование позиции пусто, равно коду или склейка ячеек «количество цена сумма»",
    "x.edge_price": "цена ребра ≤ 0 или ≥ 1e14",
    "x.edge_cur": "цена без валюты или валюта не из трёх заглавных латинских",
    "x.edge_date": "дата ребра не разбирается, позже завтрашнего дня или раньше 2015",
    "x.false_alarm": "ложная тревога «снимок отдаёт не все предложения» (2+ ключа портала)",
    "x.part_date": "дата части списка ≠ дате заголовка (страница пишет «не прочитался»)",
    "x.edge_index": "индекс компании в ребре вне списка компаний",
    "x.edge_rows": "сумма строк по рёбрам компании ≠ её rows",
}

ПОЛЬЗА_ПОСТАВЩИКОВ = {
    "u.real_name": "настоящее имя (карточка, реквизиты, написание или реестр, не ключ)",
    "u.name_domain": "(справочно) имя взято из домена",
    "u.name_key": "(справочно) имя — ключ реестра",
    "u.inn": "один ИНН с верной контрольной суммой",
    "u.name_inn": "настоящее имя и верный ИНН — можно слать запрос и договор",
    "u.site": "один домен, и это не почтовый сервис",
    "u.rfq": "есть история запросов",
    "u.rfq_meas": "история измерима (3+ запроса)",
    "u.answered": "ответов от отправленного (Σ)",
    "u.no_outcome": "(справочно) исход не зафиксирован (Σ от отправленного)",
    "u.offers": "раздел «На что давал предложения» непуст",
    "u.offers_oem": "из них с брендами из собственных КП",
    "u.pos_price": "позиции поставщиков с ценой и валютой",
    "u.pos_date": "позиции поставщиков с датой",
    "u.pos_price_date": "позиции поставщиков с ценой, валютой и датой",
    "u.pos_choice": "позиции, где есть с кем сравнить (2+ компании)",
    "u.pos_brand": "позиции с известным брендом",
    "u.pos_brand_col": "код с брендом в соседнем столбце (столбца нет — 0 по построению)",
    "u.pos_plausible": "правдоподобные коды позиций",
    "u.bitrix_link": "переход в Битрикс из основной таблицы (у записей с меткой bitrix)",
    "u.direct": "запись открывается прямой ссылкой #e= (номер есть)",
    "u.link_s": "ссылки /brands#s= находят поставщика",
    "u.link_n": "ссылки /brands#n= находят бренд",
    "u.verified": "сведение подтверждено доменом",
    "u.single": "(справочно) один источник, сливать не с чем",
    "u.collapsed": "(справочно) схлопнуты карточки портала — ждут человека",
    "u.live_labels": "подписи карточки с данными (Страна, Правовая форма, Позиций в каталоге)",
    "u.visible": "не спрятано в папку «Ждут ИНН»",
    "u.queue_readable": "строки очереди ИНН подписаны читаемым именем",
}


def _позиции_списка(части) -> list[dict]:
    позиции = []
    for ч in части:
        if isinstance(ч, dict):
            позиции.extend(p for p in ч.get("positions") or [] if isinstance(p, dict))
    return позиции


def _сущности(sup) -> list[dict]:
    return [e for e in (sup or {}).get("entities") or [] if isinstance(e, dict)]


def _инн_части(e) -> list[str]:
    return [x.strip() for x in str(e.get("inn") or "").split(",") if x.strip()]


def _домены(e) -> list[str]:
    return [x.strip() for x in str(e.get("domain") or "").split(", ") if x.strip()]


def _верные_инн(e) -> set[str]:
    return {re.sub(r"\s", "", x) for x in _инн_части(e) if инн_верен(x)}


def разные_юрлица(a, b, по_домену=True) -> bool:
    """Сведение оставляет раздельными две записи нарочно (load_supplier_master):
    «имя совпало, налоговые номера разные — разные юрлица» (тёзки вроде «ООО
    Техснаб») и «домен совпал, налоговые номера разные» (группа компаний). Такая
    пара — не дубль. Для имени разводит и разный домен у обеих."""
    return _разведены((_верные_инн(a), set(_домены(a))), (_верные_инн(b), set(_домены(b))), по_домену)


def _разведены(a, b, по_домену) -> bool:
    (ia, da), (ib, db) = a, b
    if ia and ib and not ia & ib:
        return True
    return по_домену and bool(da) and bool(db) and not da & db


def дубли_без_разводки(группы: dict, по_домену=True) -> set[int]:
    """Номера записей (id() объекта), у которых в своей группе есть запись, не
    разведённая с ними ИНН (и доменом): такие — несведённые дубли."""
    out = set()
    for записи in группы.values():
        if len(записи) < 2:
            continue
        признаки = [(_верные_инн(e), set(_домены(e))) for e in записи]
        for i, a in enumerate(записи):
            if any(not _разведены(признаки[i], признаки[j], по_домену)
                   for j in range(len(записи)) if j != i):
                out.add(id(a))
    return out


def ревизия_поставщиков(с: Снимки, сейчас) -> Вкладка:
    т = Вкладка("suppliers", "Поставщики (/suppliers)", ПРОВЕРКИ_ПОСТАВЩИКОВ,
                ПОЛЬЗА_ПОСТАВЩИКОВ)
    sup = с.json(КЛЮЧ_ПОСТАВЩИКОВ)
    т.снимок(КЛЮЧ_ПОСТАВЩИКОВ, с, сейчас, (sup or {}).get("published_at"))
    if т.счёт("s.present", not isinstance(sup, dict)):
        return т
    hdr = с.json(crossref.КЛЮЧ)
    т.снимок(crossref.КЛЮЧ, с, сейчас, (hdr or {}).get("published_at"))
    части = [с.json(k) for k in crossref.КЛЮЧИ_СПИСКА]
    bv = с.json(brands.КЛЮЧ)

    т.счёт("s.version", sup.get("version") != 1)
    ents = _сущности(sup)
    опубликован = время(sup.get("published_at"))
    т.счёт("s.published", опубликован is None and (sup.get("published_at") is not None or ents))
    if опубликован:
        т.счёт("s.age", возраст_ч(опубликован, сейчас) > 24 * ВОЗРАСТ_РЕЕСТРА_Д)
    котировки = время((hdr or {}).get("published_at"))
    if опубликован and котировки:
        т.счёт("s.cross_age", abs((опубликован - котировки).total_seconds()) > 3600 * РАЗНИЦА_СНИМКОВ_Ч)

    # Счётчики шапки.
    tot = sup.get("totals") or {}
    очередь = [q for q in sup.get("inn_queue") or [] if isinstance(q, dict)]
    карточки_очереди = {str(c) for q in очередь for c in q.get("cards") or []}
    пересчёт = {
        "entities": len(ents),
        "numbered": sum(1 for e in ents if e.get("number")),
        "with_inn": sum(1 for e in ents if e.get("inn")),
        "wait_inn": sum(1 for e in ents if e.get("wait_inn")),
        "with_rfq": sum(1 for e in ents if ((e.get("rfq") or {}).get("sent") or 0) > 0),
        "rfq_measurable": sum(1 for e in ents if ((e.get("rfq") or {}).get("sent") or 0) >= 3),
    }
    if очередь or "inn_entities" in tot:
        пересчёт["inn_entities"] = len(очередь)
        пересчёт["inn_cards"] = len(карточки_очереди)
    for имя, значение in пересчёт.items():
        if т.счёт("s.totals", tot.get(имя) != значение):
            т.заметка(f"расходится totals.{имя}")
    if т.счёт("s.totals", bool(очередь) != ("inn_entities" in tot)):
        т.заметка("inn_queue и totals.inn_entities есть не вместе")
    ro = tot.get("review_open")
    if целое(ro):
        т.счёт("s.review", ro < (tot.get("inn_entities") or 0))
        т.счёт("s.review_tail", ro > 0 and not очередь)

    многодоменных = sum(1 for e in ents if len(_домены(e)) > 1)
    caveat = sup.get("caveat")
    if многодоменных or caveat:
        m = re.match(r"\d+", str(caveat or ""))
        т.счёт("s.caveat", not caveat or not m or int(m[0]) != многодоменных)

    # Номера.
    номера = collections.Counter(e.get("number") for e in ents if e.get("number"))
    for e in ents:
        n = e.get("number")
        if n:
            т.счёт("s.number_fmt", not номер_верен(str(n)))
            т.счёт("s.number_dup", номера[n] > 1)
            т.счёт("s.number_group", str(n).startswith("KV-G-"))
        т.счёт("s.number_wait", bool(n) == bool(e.get("wait_inn")))

    # Имена.
    основные = [e for e in ents if not e.get("wait_inn")]
    по_имени = collections.defaultdict(list)
    for e in ents:
        if имя_ключ_компании(e.get("name")):
            по_имени[имя_ключ_компании(e.get("name"))].append(e)
    дубли_имени = дубли_без_разводки(по_имени)
    есть_источник = any("name_from" in e for e in ents)
    for e in ents:
        имя = e.get("name")
        откуда = e.get("name_from")
        if т.счёт("s.name_empty", not (isinstance(имя, str) and имя.strip())):
            continue
        ключ = company_names.как_ключ(имя)
        т.счёт("s.name_key", ключ)
        т.счёт("s.name_latin", bool(re.fullmatch(r"[a-z]{13,}|[a-z_]+:\d+", имя)))
        т.счёт("s.name_domain", bool(ДОМЕН_ИМЕНИ.fullmatch(имя)) and откуда != "домен")
        if откуда in ИМЯ_НАСТОЯЩЕЕ:
            т.счёт("s.name_from_contra", ключ)
        т.счёт("s.name_junk", ("@" in имя or "http" in имя.lower() or "\n" in имя
                               or только_форма(имя) or len(имя) > 150
                               or not re.search(r"\w", имя.strip("\"'«» "))))
        т.счёт("s.name_dup", id(e) in дубли_имени)
        if есть_источник and откуда is not None:
            т.счёт("s.name_from_vocab", откуда not in ОТКУДА_ИМЯ)
    if ents:
        т.счёт("s.name_from_absent", not есть_источник)

    # ИНН.
    по_инн = collections.Counter(x for e in ents for x in set(_инн_части(e)))
    for e in ents:
        части_инн = _инн_части(e)
        if not части_инн:
            continue
        т.счёт("s.inn_multi", len(set(части_инн)) > 1)
        # Иностранный налоговый номер (БИН Казахстана — тоже 12 цифр) по
        # контрольной сумме ИНН не сходится и сходиться не обязан.
        российский = not e.get("country") or e.get("country") == "RU"
        for x in части_инн:
            т.счёт("s.inn_letters", bool(re.search(r"[^\d\s]", x)))
            т.счёт("s.inn_stub", bool(re.fullmatch(r"(\d)\1+", x)))
            if российский:
                т.счёт("s.inn_sum", not инн_верен(x), форма=x)
            т.счёт("s.inn_dup", по_инн[x] > 1)
    for n in по_инн.values():
        т.счёт("s.inn_many", n > 3)

    # Домены.
    по_домену = collections.defaultdict(list)
    for e in ents:
        for d in set(_домены(e)):
            по_домену[d].append(e)
    дубли_домена = {d: дубли_без_разводки({d: записи}, по_домену=False) for d, записи in по_домену.items()}
    for e in ents:
        for d in _домены(e):
            т.счёт("s.domain_host", not ХОСТ.fullmatch(d), форма=d)
            т.счёт("s.domain_mail", d.lower() in ПОЧТОВЫЕ)
            т.счёт("s.domain_dup", id(e) in дубли_домена[d])

    # История запросов.
    for e in ents:
        r = e.get("rfq")
        if r is None:
            continue
        поля = ("sent", "answered", "quoted", "silent", "no_outcome", "cards")
        if т.счёт("s.rfq_type", not isinstance(r, dict)
                  or any(not целое(r.get(p, 0)) or r.get(p, 0) < 0 for p in поля)):
            continue
        sent, ans = r.get("sent", 0), r.get("answered", 0)
        т.счёт("s.rfq_sum", ans + r.get("silent", 0) + r.get("no_outcome", 0) != sent)
        т.счёт("s.rfq_order", r.get("quoted", 0) > ans or ans > sent)
        т.счёт("s.rfq_cards", r.get("cards", 0) < sent)
        т.счёт("s.rfq_zero", sent == 0)

    # Откуда, чем слито, страна, статус.
    сочетания = collections.Counter()
    for e in ents:
        src = e.get("sources") or []
        for метка in src:
            т.счёт("s.sources_vocab", метка not in МЕТКИ_ОТКУДА)
        т.счёт("s.sources_empty", not src)
        сочетания[tuple(sorted(src))] += 1
        m = e.get("merged_by")
        if not т.счёт("s.merged_empty", not (isinstance(m, str) and m.strip())):
            т.счёт("s.merged_vocab", m not in ПРИЧИНЫ_СВЕДЕНИЯ and not СХЛОПНУТЫ.fullmatch(m))
        страна = e.get("country")
        if страна:
            т.счёт("s.country_iso", not re.fullmatch(r"[A-Z]{2}", str(страна)))
        if страна and any(инн_верен(x) for x in _инн_части(e)):
            т.счёт("s.country_ru", страна != "RU")
        if "status" in e:
            т.счёт("s.status_hidden", e.get("status") != "active")
    if len(основные) >= 50:
        т.счёт("s.sources_flat", len(сочетания) <= 4)
    if ents:
        т.счёт("s.country_dead", not any(e.get("country") for e in ents))
        for поле in ("legal_form", "parts"):
            т.счёт("s.dead_labels", not any(e.get(поле) not in (None, "", [], {}) for e in ents))

    # Очередь «Ждут ИНН».
    с_номером_по_имени = collections.defaultdict(list)
    for e in ents:
        if e.get("number"):
            с_номером_по_имени[имя_ключ_компании(e.get("name"))].append(e)

    def устарела(имя) -> bool:
        # Имя, у которого уже две записи с номером и разными ИНН, — имя тёзок:
        # совпадение с ним ничего не говорит о строке очереди.
        записи = с_номером_по_имени.get(имя_ключ_компании(имя)) or []
        тёзки = any(разные_юрлица(a, b, по_домену=False)
                    for i, a in enumerate(записи) for b in записи[i + 1:])
        return bool(записи) and not тёзки

    сколько_карточек = collections.Counter(str(c) for q in очередь for c in set(q.get("cards") or []))
    for q in очередь:
        names = [n for n in q.get("names") or [] if isinstance(n, str)]
        т.счёт("q.names_empty", not names)
        for n in names:
            т.счёт("q.names_key", company_names.как_ключ(n))
            т.счёт("q.stale", устарела(n))
        т.счёт("q.reason", q.get("reason") not in ПРИЧИНЫ_ОЧЕРЕДИ)
        for c in q.get("cards") or []:
            т.счёт("q.card_fmt", not re.fullmatch(r"[1-9]\d*", str(c)))
            т.счёт("q.card_dup", сколько_карточек[str(c)] > 1)

    # Котировки в карточке поставщика.
    for k, v in zip((crossref.КЛЮЧ,) + crossref.КЛЮЧИ_СПИСКА, [hdr] + части):
        т.счёт("x.present", not isinstance(v, dict))
    номера_реестра = {e.get("number") for e in ents if e.get("number")}
    companies = [c for c in (hdr or {}).get("companies") or [] if isinstance(c, dict)]
    позиции = _позиции_списка(части)
    нбр = бренды_брендов(bv)
    поставщики_брендов = {s.get("k") for s in (bv or {}).get("suppliers") or [] if isinstance(s, dict)}
    for c in companies:
        rows = c.get("rows") if целое(c.get("rows")) else 0
        if c.get("ent"):
            т.счёт("x.ent_missing", c["ent"] not in номера_реестра, вес=max(rows, 1))
            if bv is not None and c["ent"] in номера_реестра:
                т.счёт("x.link_s", c["ent"] not in поставщики_брендов)
        т.счёт("x.ent_null", not c.get("ent"), вес=max(rows, 1))
        for b in list(c.get("brands") or []) + list(c.get("oem") or []):
            т.счёт("x.brand_digits", только_цифры(b))
            т.счёт("x.brand_junk", not только_цифры(b) and мусор_бренда(b))
        if bv is not None:
            for o in c.get("oem") or []:
                т.счёт("x.link_n", not бренд_найден(o, нбр))
    дата_заголовка = (hdr or {}).get("published_at")
    for ч in части:
        if isinstance(ч, dict) and isinstance(hdr, dict):
            т.счёт("x.part_date", ч.get("published_at") != дата_заголовка)
    строк_по_компании = collections.Counter()
    ключи_по_компании = collections.defaultdict(set)
    # Сутки запаса: сборка в 18:07 UTC — уже завтра в Китае, и КП «от сегодня»
    # там датировано завтрашним днём по UTC.
    будущее = сейчас.date() + timedelta(days=1)
    for p in позиции:
        рёбра = [r for r in p.get("e") or [] if isinstance(r, list) and r]
        if not рёбра:
            continue
        n, k = p.get("n") or p.get("k"), str(p.get("k") or "")
        if есть_правило_кода():
            т.счёт("x.pn_class", класс_написания(n) is not None, форма=n)
        т.счёт("x.pk_short", len(k) < 3 or bool(re.fullmatch(r"\d{1,3}", k)))
        имя = p.get("name")
        т.счёт("x.pname", not имя or ключ_кода(имя) == k or склейка_ячеек(имя))
        for r in рёбра:
            ci = r[0]
            if т.счёт("x.edge_index", not целое(ci) or not 0 <= ci < len(companies)):
                continue
            строк_по_компании[ci] += r[1] if len(r) > 1 and целое(r[1]) else 0
            ключи_по_компании[ci].add(crossref.ид_позиции(p))
            цена = r[2] if len(r) > 2 else None
            if цена is not None:
                т.счёт("x.edge_price", not число(цена) or цена <= 0 or цена >= 1e14)
                вал = r[3] if len(r) > 3 else None
                т.счёт("x.edge_cur", not (isinstance(вал, str) and ВАЛЮТА_RX.fullmatch(вал)))
            if len(r) > 4 and r[4] is not None:
                d = день(r[4])
                т.счёт("x.edge_date", d is None or d > будущее or d < РАННЯЯ_ДАТА)
    for ci, c in enumerate(companies):
        if целое(c.get("rows")) and (ci in строк_по_компании):
            т.счёт("x.edge_rows", строк_по_компании[ci] != c["rows"])
    по_сущности = collections.defaultdict(list)
    for ci, c in enumerate(companies):
        if c.get("ent") in номера_реестра:
            по_сущности[c["ent"]].append(ci)
    for ent, индексы in по_сущности.items():
        if len(индексы) < 2:
            continue
        частей = sum(companies[i].get("parts") or 0 for i in индексы)
        различных = len(set().union(*(ключи_по_компании[i] for i in индексы)))
        т.счёт("x.false_alarm", частей > различных)

    # Польза.
    настоящих = [e for e in основные if e.get("name_from") in ИМЯ_НАСТОЯЩЕЕ
                 and not company_names.как_ключ(e.get("name"))]
    т.доля("u.real_name", len(настоящих), len(основные))
    т.доля("u.name_domain", sum(1 for e in основные if e.get("name_from") == "домен"), len(основные))
    т.доля("u.name_key", sum(1 for e in основные if e.get("name_from") == "ключ реестра"),
           len(основные))
    верный_инн = [e for e in основные if len(_инн_части(e)) == 1 and инн_верен(_инн_части(e)[0])]
    т.доля("u.inn", len(верный_инн), len(основные))
    т.доля("u.name_inn", sum(1 for e in верный_инн if e in настоящих), len(основные))
    т.доля("u.site", sum(1 for e in основные if len(_домены(e)) == 1
                         and _домены(e)[0].lower() not in ПОЧТОВЫЕ), len(основные))
    rfq = [e.get("rfq") for e in основные if isinstance(e.get("rfq"), dict)]
    т.доля("u.rfq", sum(1 for r in rfq if (r.get("sent") or 0) > 0), len(основные))
    т.доля("u.rfq_meas", sum(1 for r in rfq if (r.get("sent") or 0) >= 3), len(основные))
    sent = sum(r.get("sent") or 0 for r in rfq if целое(r.get("sent")))
    т.доля("u.answered", sum(r.get("answered") or 0 for r in rfq if целое(r.get("answered"))), sent)
    т.доля("u.no_outcome", sum(r.get("no_outcome") or 0 for r in rfq if целое(r.get("no_outcome"))),
           sent)
    if isinstance(hdr, dict):
        с_котировками = {c.get("ent") for c in companies if c.get("ent")}
        с_ое = {c.get("ent") for c in companies if c.get("ent")
                and any(not только_цифры(o) for o in c.get("oem") or [])}
        с_разделом = [e for e in основные if e.get("number") in с_котировками]
        т.доля("u.offers", len(с_разделом), len(основные))
        т.доля("u.offers_oem", sum(1 for e in с_разделом if e.get("number") in с_ое), len(с_разделом))
    рёберные = [p for p in позиции if p.get("e")]
    if рёберные:
        def с_ценой(p):
            return any(len(r) > 3 and r[2] is not None and r[3] for r in p["e"])

        def с_датой(p):
            return any(len(r) > 4 and r[4] for r in p["e"])

        n = len(рёберные)
        т.доля("u.pos_price", sum(1 for p in рёберные if с_ценой(p)), n)
        т.доля("u.pos_date", sum(1 for p in рёберные if с_датой(p)), n)
        т.доля("u.pos_price_date", sum(1 for p in рёберные if с_ценой(p) and с_датой(p)), n)
        т.доля("u.pos_choice", sum(1 for p in рёберные if (p.get("co") or 0) >= 2), n)
        т.доля("u.pos_brand", sum(1 for p in рёберные if p.get("brands") or p.get("oem_file")
                                  or p.get("oem_cat")), n)
        т.доля("u.pos_brand_col", 0, n)
        if есть_правило_кода():
            т.доля("u.pos_plausible", sum(1 for p in рёберные if код_правдоподобен(p.get("n") or p.get("k"))), n)
    с_меткой = [e for e in основные if "bitrix" in (e.get("sources") or [])]
    т.доля("u.bitrix_link", sum(1 for e in с_меткой if e.get("bitrix") or e.get("cards")), len(с_меткой))
    т.доля("u.direct", sum(1 for e in ents if e.get("number")), len(ents))
    if bv is not None and isinstance(hdr, dict):
        ссылки_s = [c["ent"] for c in companies if c.get("ent") in номера_реестра]
        т.доля("u.link_s", sum(1 for x in ссылки_s if x in поставщики_брендов), len(ссылки_s))
        ссылки_n = [o for c in companies for o in c.get("oem") or []]
        т.доля("u.link_n", sum(1 for o in ссылки_n if бренд_найден(o, нбр)), len(ссылки_n))
    причины = collections.Counter(e.get("merged_by") for e in основные)
    т.доля("u.verified", причины["домен совпал у двух и более источников"], len(основные))
    т.доля("u.single", причины["один источник, сливать не с чем"], len(основные))
    т.доля("u.collapsed", sum(n for m, n in причины.items() if m and СХЛОПНУТЫ.fullmatch(str(m))),
           len(основные))
    т.доля("u.live_labels", sum(1 for поле in ("country", "legal_form", "parts")
                                if any(e.get(поле) not in (None, "", [], {}) for e in ents)), 3)
    спрятано = (tot.get("wait_inn") or 0) + (tot.get("inn_entities") or 0)
    всего = len(ents) + (tot.get("inn_entities") or 0)
    т.доля("u.visible", всего - спрятано, всего)
    имена_очереди = [n for q in очередь for n in q.get("names") or []]
    т.доля("u.queue_readable", sum(1 for n in имена_очереди if not company_names.как_ключ(n)),
           len(имена_очереди))
    return т


# ── /nomenclature ────────────────────────────────────────────────────────────

ПРОВЕРКИ_НОМЕНКЛАТУРЫ = {
    "n.keys": "ключа снимка нет в KV (заголовок, 8 частей, 32 корзины)",
    "n.published": "published_at заголовка не ISO-8601 с Z",
    "n.age": f"заголовок старше {ВОЗРАСТ_НОМЕНКЛАТУРЫ_Ч} ч (ночная сборка пропущена)",
    "n.part_date": "дата части или корзины ≠ дате заголовка (смесь сборок)",
    "n.version": "version ≠ 1, lists ≠ 8, parts ≠ 32 или positions в заголовке (старый формат)",
    "n.totals": "итог totals.* не сходится с пересчётом по частям",
    "n.empty": "позиций ноль — страница покажет «Пока пусто»",
    "n.co_fmt": "компания: co не число > 0 (ссылка в Битрикс в никуда)",
    "n.co_dup": "компания: co повторяется",
    "n.co_orphan": "компания без единого ребра",
    "n.co_name_key": "компания: имя похоже на ключ",
    "n.co_name_latin": "компания: имя — строчная латиница без пробелов длиннее 12",
    "n.co_name_noent": "компания: имя без ent",
    "n.co_name_domain": "компания: имя — цифры или домен",
    "n.co_ent_noname": "компания без имени — страница пишет «карточка N в Битриксе»",
    "n.co_ent_missing": "компания: ent не открывается на /suppliers#e=",
    "n.co_name_dup": "компания: одно имя у двух разных ent",
    "n.co_counts": "компания: parts или rows не сходятся с рёбрами",
    "n.co_brand_digits": "компания: бренды или oem — номера СП-176",
    "n.co_brand_case": "компания: регистровые дубли брендов",
    "n.co_oem_len": "компания: oem длиннее 10",
    "n.k_fmt": "ключ позиции не ^[0-9a-zа-я]{1,80}$",
    "n.k_class": "ключ позиции — марка, размер или стандарт (регрессия правила кода)",
    "n.k_material": "ключ позиции — марка или материал вне правила кода (316ss, inox, A4-80, NBR70) или склейка двух свойств",
    "n.k_desc": "ключ-описание: кириллица длиннее 30 или ровно 80 знаков",
    "n.k_nodigit": "ключ без цифр (слово, а не артикул)",
    "n.k_short_num": "ключ — только цифры короче 4 (номер позиции списка)",
    "n.k_dup": "пара «бренд + код» повторяется в частях списка",
    "n.k_bucket": "номер корзины не crc32(k) % 32 или пары нет в корзине",
    "n.brand_src": "пара с брендом: имени нет или источник не из списка (каталог, строка, карточка, маска)",
    "n.brand_why": "пара без бренда: причина не «нет» и не «спорно», «спорно» без двух кандидатов или бренд вместе с причиной",
    "n.pair_split": "предложение со своим брендом лежит в паре другого бренда (один код у двух брендов в одной позиции)",
    "n.brand_mask": "бренд по маске кода, а код не подходит под маску этого бренда (crossref.МАСКИ)",
    "n.brand_undet_dup": "у кода две пары «Бренд не определён»",
    "n.n_empty": "номер пуст (показывается сжатый ключ)",
    "n.n_class": "номер — марка, размер, стандарт, дата, пункт или год",
    "n.n_words": "номер из двух и больше слов от 4 букв — описание, а не номер",
    "n.n_key": "ключ номера ≠ ключу позиции вне каталога (номер от чужой строки)",
    "n.name_empty": "наименование пусто",
    "n.name_glued": "наименование — склейка ячеек «количество цена сумма»",
    "n.name_is_code": "наименование — сам код",
    "n.name_prose": "наименование — тендерная проза",
    "n.name_long": "наименование длиннее 250 знаков",
    "n.dem_qty": "спрос: количество > 1e6 или ≤ 0",
    "n.dem_units": "спрос: количество или единица при нескольких единицах",
    "n.dem_rows": "спрос: строк меньше сделок",
    "n.dem_glued": "слипшийся спрос: 30+ сделок у ключа без цифр, короче 4, марки или материала",
    "n.dem_unit": "спрос: единица — цифры или длиннее 15",
    "n.off": "предложения: offers < 1, co > offers или co ≠ числу рёбер",
    "n.e_index": "ребро: индекс вне списка компаний или повтор компании",
    "n.e_cnt": "ребро: строк < 1 или сумма строк > offers",
    "n.e_cur": "ребро: цена без валюты",
    "n.e_date": "ребро: дата не ГГГГ-ММ-ДД или позже следующего за сборкой дня",
    "n.co_zero": "предложения есть, а компаний ноль («Не назван»)",
    "n.cmp_co": "«сравнимы» при одной компании",
    "n.cmp_basket": "«сравнимы», а в корзине меньше двух цен одной валюты",
    "n.cat_empty": "в каталоге, но ни изготовителя, ни аналогов, ни машин, ни имени",
    "n.cat_contra": "не в каталоге, а аналоги, машины или изготовитель по каталогу есть",
    "n.oem_cat": "изготовитель по каталогу: цифры, ключ, страна или служебное слово",
    "n.oem_file": "изготовитель со слов поставщика: цифры, служебное слово или страна",
    "n.oem_file_legal": "изготовитель со слов поставщика: > 60 знаков или правовая форма у имени, неизвестного словарю и /brands",
    "n.oem_file_case": "изготовитель со слов поставщика: регистровые дубли",
    "n.brands_digits": "бренд карточки запроса — номер СП-176",
    "n.brands_case": "бренды карточки запроса: регистровые дубли",
    "n.brands_unknown": "бренд позиции не найден в brands:v1 (ссылку #n= не построить)",
    "n.alt_kind": "аналог: вид вне списка",
    "n.alt_self": "аналог: номер пуст или ссылается на саму позицию",
    "n.alt_dup": "аналог: повтор пары (номер, вид)",
    "n.alt_maker": "аналог: изготовитель — цифры или ключ",
    "n.alt_class": "аналог: номер — марка или голый стандарт (стандарт с размером — номер)",
    "n.models_id": "машина показана идентификатором",
    "n.models_dup": "машина повторяется у позиции",
    "n.models_many": "больше 20 машин у детали (родовая связь)",
    "n.b_shown": "корзина: shown ≠ длине списка или ≠ min(offers, 25)",
    "n.o_c": "предложение: компании нет в списке или co не число > 0",
    "n.o_e": "предложение: ent расходится с компанией",
    "n.o_price": "предложение: цена ≤ 0 или выше потолка валюты (≈ 300 млн долларов)",
    "n.o_cur": "предложение: валюта не из ISO 4217",
    "n.o_pu": "предложение: цена без валюты или валюта без цены",
    "n.o_qty": "предложение: количество > 1e6 или ≤ 0",
    "n.o_triple": "предложение: количество × цена ≠ сумма",
    "n.o_unit": "предложение: единица — цифры или длиннее 15",
    "n.o_frac": "предложение: дробное количество в штуках",
    "n.o_total": "предложение: сумма ≤ 0 или меньше цены при количестве от 1",
    "n.o_t_noq": "предложение в штуках: сумма без количества, и сумма/цена не целое",
    "n.o_r": "предложение: пометки условий не из четырёх букв «сфнм?» или противоречат значению",
    "n.o_basis": "предложение: базис не начинается с Incoterms",
    "n.o_days": "предложение: срок < 0, > 730 дней или не целый",
    "n.o_adv": "предложение: аванс вне 0..100 или без условий оплаты",
    "n.o_pay_long": "предложение: условия оплаты длиннее 200 знаков",
    "n.o_b_digits": "предложение: бренд — номер СП-176",
    "n.o_m": "предложение: изготовитель по файлу — цифры, служебное, > 60 или правовая форма у неизвестного имени",
    "n.o_date": "предложение: дата не ГГГГ-ММ-ДД, позже следующего за сборкой дня или раньше 2015",
    "n.o_dup": "предложение: точный дубль (компания, цена, валюта, количество, дата)",
    "n.o_repeat": "одна компания с одной ценой больше трёх раз (повторный разбор)",
    "n.o_f": "предложение: карточка запроса не число",
    "n.o_card_label": "предложение: у компании нет имени — страница пишет «карточка N в Битриксе»",
    "n.mk": "изготовитель по каталогу: имя пусто или ключ, роль вне списка, повтор имени",
    "n.mk_raw": "изготовитель по каталогу: машинный код вместо слов в «Роль», «Страна», «Что делает», «Проверка»",
    "n.mk_note": "изготовитель по каталогу: «Что именно делает» — заметка разведки (> 200 знаков или 2+ предложения)",
    "n.mk_nocat": "изготовители есть, а позиции нет в каталоге",
    "n.col_dead": "столбец списка «Машины» или «Аналоги» заполнен меньше чем у 5 % позиций («Нет связи», «Нет»)",
    "n.link_k": "ссылка /nomenclature#k= со страницы брендов не находит позицию",
    "n.brands_age": "снимки брендов и номенклатуры собраны с разницей больше суток",
    "n.worse_lost": "позиция прошлого снимка пропала (--prev-dir)",
    "n.worse_brand": "позиция потеряла бренд (--prev-dir)",
    "n.worse_price": "позиция потеряла цену (--prev-dir)",
    "n.worse_co": "у позиции стало меньше компаний (--prev-dir)",
    "n.worse_qty": "у позиции стало меньше предложений с количеством (--prev-dir)",
}

ПОЛЬЗА_НОМЕНКЛАТУРЫ = {
    "u.brand": "пары с определённым брендом (позиция — «бренд + код», П1)",
    "u.brand_guess": "(справочно) бренд по маске кода — предположение",
    "u.brand_any": "бренд определён хоть одним источником",
    "u.oem_cat": "изготовитель по каталогу",
    "u.oem_file_only": "(справочно) только со слов поставщика",
    "u.brands_only": "(справочно) только бренд карточки запроса",
    "u.brand_col": "бренд в соседнем столбце «Изготовитель» и он правдоподобен",
    "u.brand_col_nochoice": "то же среди позиций без выбора (есть по чему искать второго)",
    "u.brand_pairs": "у бренда позиции без выбора есть другой поставщик в brands:pairs:v1",
    "u.choice": "выбор: две и больше компании",
    "u.one_co_two": "(справочно) два КП одной компании — не выбор",
    "u.cmp": "сравнимы по цене в одной валюте",
    "u.edge_full": "позиции с ценой, валютой и датой хотя бы в одном ребре",
    "u.offer_full": "предложения с ценой, валютой и датой",
    "u.fresh": "свежая цена не старше 180 дней",
    "u.lead": "предложения со сроком поставки или изготовления",
    "u.basis": "предложения с базисом",
    "u.pay": "предложения с условиями оплаты",
    "u.gap_ours": "(справочно) пустые условия, где разбор не дошёл («?» от всех пустот)",
    "u.co_name": "компании с настоящим именем",
    "u.co_resolved": "компании, сведённые с реестром",
    "u.co_inn": "компании с ИНН в реестре",
    "u.offer_co": "предложения с компанией",
    "u.offer_qty": "предложения с количеством (без него страница пишет «— шт»)",
    "u.o_m_self": "(справочно) изготовитель по файлу — сама компания КП (прямая поставка)",
    "u.pos_co": "позиции, у которых компания названа",
    "u.cat": "позиции в каталоге",
    "u.cat_alts": "из них с аналогами",
    "u.cat_mfr": "из них с «номером изготовителя»",
    "u.cat_models": "из них с машинами",
    "u.cat_makers": "из них с изготовителями и ролями",
    "u.col_models": "столбец «Машины» списка: позиции с машиной (из всех)",
    "u.col_alts": "столбец «Аналоги» списка: позиции с аналогами (из всех)",
    "u.n_key_cat": "(справочно) каталожный номер с ключом не как у позиции (сцепка по catalog_no)",
    "u.nochoice_demand3": "(справочно) без выбора, но спрашивали в 3+ сделках — приоритет рассылки",
    "u.qty_unit": "спрос: количество читается с единицей",
    "u.k_plausible": "ключи правдоподобны",
    "u.link_k": "коды со страницы брендов открываются здесь",
    "u.ent_open": "компании открываются на /suppliers#e=",
    "u.brand_found": "бренды позиций найдены в brands:v1",
}


def _корзины(с: Снимки) -> list:
    return [с.json(k) for k in crossref.КЛЮЧИ_КОРЗИН]


def _предложения(корзины) -> dict[str, dict]:
    out = {}
    for b in корзины:
        if isinstance(b, dict):
            for k, v in (b.get("positions") or {}).items():
                if isinstance(v, dict):
                    out[k] = v
    return out


def ревизия_номенклатуры(с: Снимки, сейчас, прошлые: Снимки | None = None, словарь=None) -> Вкладка:
    т = Вкладка("nomenclature", "Номенклатура (/nomenclature)", ПРОВЕРКИ_НОМЕНКЛАТУРЫ,
                ПОЛЬЗА_НОМЕНКЛАТУРЫ)
    hdr = с.json(crossref.КЛЮЧ)
    т.снимок(crossref.КЛЮЧ, с, сейчас, (hdr or {}).get("published_at"))
    части = [с.json(k) for k in crossref.КЛЮЧИ_СПИСКА]
    корзины = _корзины(с)
    for k, v in zip(crossref.ВСЕ_КЛЮЧИ, [hdr] + части + корзины):
        т.счёт("n.keys", not isinstance(v, dict))
    if not isinstance(hdr, dict):
        return т
    sup = с.json(КЛЮЧ_ПОСТАВЩИКОВ)
    bv = с.json(brands.КЛЮЧ)
    links = с.json(brands.КЛЮЧ_СВЯЗЕЙ)
    pairs = с.json(brands.КЛЮЧ_ПАР)

    собран = время(hdr.get("published_at"))
    т.счёт("n.published", собран is None)
    if собран:
        т.счёт("n.age", возраст_ч(собран, сейчас) > ВОЗРАСТ_НОМЕНКЛАТУРЫ_Ч)
    for v in части + корзины:
        if isinstance(v, dict):
            т.счёт("n.part_date", v.get("published_at") != hdr.get("published_at"))
    т.счёт("n.version", hdr.get("version") != 1 or hdr.get("lists") != crossref.СПИСОК_ЧАСТЕЙ
           or hdr.get("parts") != crossref.КОРЗИН or "positions" in hdr)

    позиции = _позиции_списка(части)
    подробно = _предложения(корзины)
    companies = [c if isinstance(c, dict) else {} for c in hdr.get("companies") or []]
    tot = hdr.get("totals") or {}
    пересчёт = {
        "positions": len(позиции),
        "with_choice": sum(1 for p in позиции if (p.get("co") or 0) >= 2),
        "comparable": sum(1 for p in позиции if p.get("cmp")),
        "in_catalog": sum(1 for p in позиции if p.get("cat")),
        "companies": len(companies),
        "companies_resolved": sum(1 for c in companies if c.get("ent")),
        "offers": sum(p.get("offers") or 0 for p in позиции),
        "no_demand": sum(1 for p in позиции if not (p.get("demand") or {}).get("deals")),
    }
    for имя, значение in пересчёт.items():
        if т.счёт("n.totals", tot.get(имя) != значение):
            т.заметка(f"расходится totals.{имя}")
    # Бренд позиции (П1, П2): итоги сверяются, только если снимок их несёт —
    # снимок, собранный до правила, их не знает, и это не дефект.
    if isinstance(tot.get("brand"), dict):
        б = tot["brand"]
        по_источнику = collections.Counter(p.get("bs") for p in позиции if p.get("bk"))
        for имя, значение in (("determined", sum(1 for p in позиции if p.get("bk"))),
                              ("none", sum(1 for p in позиции if p.get("bw") == "нет")),
                              ("disputed", sum(1 for p in позиции if p.get("bw") == "спорно")),
                              ("codes", len({p.get("k") for p in позиции})),
                              ("offers_coded", пересчёт["offers"])):
            if т.счёт("n.totals", б.get(имя) != значение):
                т.заметка(f"расходится totals.brand.{имя}")
        if т.счёт("n.totals", (б.get("by") or {}) != {и: по_источнику[и] for и in crossref.ИСТОЧНИКИ_БРЕНДА}):
            т.заметка("расходится totals.brand.by")
    т.счёт("n.empty", not позиции)

    # Компании.
    номера = {e.get("number") for e in _сущности(sup) if e.get("number")}
    инн_по_номеру = {e.get("number"): e.get("inn") for e in _сущности(sup) if e.get("number")}
    сколько_co = collections.Counter(str(c.get("co")) for c in companies)
    рёбра_компании = collections.defaultdict(lambda: [0, 0])     # позиций, строк
    for p in позиции:
        for r in p.get("e") or []:
            if isinstance(r, list) and r and целое(r[0]) and 0 <= r[0] < len(companies):
                рёбра_компании[r[0]][0] += 1
                рёбра_компании[r[0]][1] += r[1] if len(r) > 1 and целое(r[1]) else 0
    ent_по_имени = collections.defaultdict(set)
    for c in companies:
        if c.get("name") and c.get("ent"):
            ent_по_имени[норм(c["name"])].add(c["ent"])
    # Одно имя у двух ent — дубль, только если реестр их не развёл: тёзки с
    # разными ИНН (или доменами) — разные юрлица по замыслу сведения.
    запись_по_номеру = {e.get("number"): e for e in _сущности(sup) if e.get("number")}
    дубль_ent = set()
    for группа in ent_по_имени.values():
        записи = {x: запись_по_номеру.get(x) or {} for x in группа}
        for x in группа:
            if any(not разные_юрлица(записи[x], записи[y]) for y in группа if y != x):
                дубль_ent.add(x)
    for ci, c in enumerate(companies):
        co = str(c.get("co"))
        т.счёт("n.co_fmt", not re.fullmatch(r"[1-9]\d*", co))
        т.счёт("n.co_dup", сколько_co[co] > 1)
        т.счёт("n.co_orphan", ci not in рёбра_компании)
        имя, ent = c.get("name"), c.get("ent")
        if имя:
            т.счёт("n.co_name_key", company_names.как_ключ(имя))
            т.счёт("n.co_name_latin", bool(re.fullmatch(r"[a-z]{13,}", имя)))
            т.счёт("n.co_name_noent", not ent)
            т.счёт("n.co_name_domain", bool(re.fullmatch(r"\d+", имя) or ДОМЕН_КОМПАНИИ.fullmatch(имя)))
            if ent:
                т.счёт("n.co_name_dup", ent in дубль_ent)
        # Без имени страница пишет «карточка N в Битриксе» — есть ent или нет:
        # имя приходит только через ent (ПРЕДЛОЖЕНИЯ_SQL), и чаще его нет как раз
        # потому, что нет ent.
        т.счёт("n.co_ent_noname", not имя)
        if ent and sup is not None:
            т.счёт("n.co_ent_missing", ent not in номера)
        if ci in рёбра_компании:
            т.счёт("n.co_counts", c.get("parts") != рёбра_компании[ci][0]
                   or c.get("rows") != рёбра_компании[ci][1])
        бренды_компании = list(c.get("brands") or []) + list(c.get("oem") or [])
        for b in бренды_компании:
            т.счёт("n.co_brand_digits", только_цифры(b))
        for набор in (c.get("brands") or [], c.get("oem") or []):
            т.счёт("n.co_brand_case", len({норм(b) for b in набор}) < len(set(набор)))
        if "oem" in c:
            т.счёт("n.co_oem_len", len(c["oem"]) > crossref.ОЕМ_НА_КОМПАНИЮ)

    # Позиции списка.
    # ПОЗИЦИЯ — ПАРА «БРЕНД + КОД» (crossref.ид_позиции): у кода, разбитого по
    # брендам, строк списка несколько, и повтором считается повтор пары.
    сколько_k = collections.Counter(crossref.ид_позиции(p) for p in позиции)
    без_бренда_у_кода = collections.Counter(p.get("k") for p in позиции if not p.get("bk"))
    нбр = бренды_брендов(bv)
    карта = карта_брендов(словарь, bv)
    # Сутки запаса к дате сборки: КП из Китая «от сегодня» датировано завтра по UTC.
    будущее = (собран.date() if собран else сейчас.date()) + timedelta(days=1)
    каталожных_с_другим_ключом = 0
    for p in позиции:
        k = str(p.get("k") or "")
        т.счёт("n.k_fmt", not re.fullmatch(r"[0-9a-zа-я]{1,80}", k))
        if есть_правило_кода():
            т.счёт("n.k_class", класс_не_кода(k) is not None, форма=k)
        т.счёт("n.k_material", класс_материала(k, p.get("n")) is not None, форма=k)
        т.счёт("n.k_desc", (len(k) > 30 and bool(re.search(r"[а-я]", k))) or len(k) == 80)
        т.счёт("n.k_nodigit", not re.search(r"\d", k))
        т.счёт("n.k_short_num", bool(re.fullmatch(r"\d{1,3}", k)))
        ид = crossref.ид_позиции(p)
        т.счёт("n.k_dup", сколько_k[ид] > 1)
        т.счёт("n.k_bucket", p.get("b") != crossref.корзина(k)
               or ((p.get("offers") or 0) >= 1 and ид not in подробно))
        # Бренд пары: источник из закрытого списка, у пары без бренда — причина.
        if p.get("bk"):
            т.счёт("n.brand_src", not p.get("bn") or p.get("bs") not in crossref.ИСТОЧНИКИ_БРЕНДА
                   or "bw" in p)
            if p.get("bs") == "маска":
                т.счёт("n.brand_mask", p["bk"] not in {м[0] for м in crossref.маска_кода(k)}
                       and not any(норм(м[1]) == норм(p.get("bn")) for м in crossref.маска_кода(k)))
        elif "bs" in p or "bw" in p or "bc" in p:
            кандидаты = [c for c in p.get("bc") or [] if isinstance(c, list) and c]
            т.счёт("n.brand_why", p.get("bw") not in crossref.ПРИЧИНЫ_БЕЗ_БРЕНДА or "bs" in p
                   or (p.get("bw") == "спорно" and len(кандидаты) < 2))
            т.счёт("n.brand_undet_dup", без_бренда_у_кода[k] > 1)
        n = p.get("n")
        if not т.счёт("n.n_empty", not n):
            if есть_правило_кода():
                т.счёт("n.n_class", класс_написания(n) is not None or docfilter._rubbish(str(n)), форма=n)
            т.счёт("n.n_words", слов_в_коде(n) >= 2)
            # У позиции каталога номер — catalog_no, а сцепка идёт по id детали
            # (catalog_norm): у 82 деталей из 13 501 их ключи расходятся, и это
            # точная сцепка, а не чужая деталь (crossref.СЦЕПКА).
            if p.get("cat"):
                каталожных_с_другим_ключом += ключ_кода(n) != k
            else:
                т.счёт("n.n_key", ключ_кода(n) != k)
        имя = p.get("name")
        if not т.счёт("n.name_empty", not имя):
            имя = str(имя)
            т.счёт("n.name_glued", склейка_ячеек(имя))
            т.счёт("n.name_is_code", ключ_кода(имя) == k)
            т.счёт("n.name_prose", docfilter.row_is_prose(*docfilter.row_marks(имя)))
            т.счёт("n.name_long", len(имя) > 250)
        d = p.get("demand")
        if isinstance(d, dict):
            q = d.get("qty")
            if q is not None:
                т.счёт("n.dem_qty", not число(q) or q > 1e6 or q <= 0)
            т.счёт("n.dem_units", (d.get("units") or 0) > 1 and (q is not None or bool(d.get("unit_name"))))
            т.счёт("n.dem_rows", (d.get("rows") or 0) < (d.get("deals") or 0))
            # Ходовые подшипники 6205, 22220 спрашивают в десятках сделок по
            # праву; слипшийся ключ — без цифр, короче 4, марка или материал.
            т.счёт("n.dem_glued", (d.get("deals") or 0) >= 30
                   and (len(k) <= 3 or not re.search(r"\d", k) or класс_не_кода(k) is not None
                        or класс_материала(k) is not None))
            if d.get("unit_name"):
                ед = str(d["unit_name"])
                т.счёт("n.dem_unit", ед.isdigit() or len(ед) > 15)
        offers, co = p.get("offers") or 0, p.get("co") or 0
        рёбра = [r for r in p.get("e") or [] if isinstance(r, list) and r]
        т.счёт("n.off", offers < 1 or co > offers or co != len(рёбра))
        т.счёт("n.co_zero", offers > 0 and co == 0)
        индексы = [r[0] for r in рёбра]
        т.счёт("n.e_index", any(not целое(i) or not 0 <= i < len(companies) for i in индексы)
               or len(set(индексы)) < len(индексы))
        if рёбра:
            cnts = [r[1] if len(r) > 1 else 0 for r in рёбра]
            т.счёт("n.e_cnt", any(not целое(x) or x < 1 for x in cnts)
                   or sum(x for x in cnts if целое(x)) > offers)
        for r in рёбра:
            if len(r) > 2 and r[2] is not None:
                т.счёт("n.e_cur", len(r) < 4 or not r[3])
            if len(r) > 4 and r[4] is not None:
                dd = день(r[4])
                т.счёт("n.e_date", dd is None or dd > будущее)
        det = подробно.get(ид) or {}
        список = [o for o in det.get("list") or [] if isinstance(o, dict)]
        # Свой бренд предложения (поле o, «с:ключ» или «к:ключ») — всегда бренд
        # его пары: иначе в одной позиции лежат два бренда одного кода.
        for o in список:
            if isinstance(o.get("o"), str) and ":" in o["o"]:
                т.счёт("n.pair_split", o["o"].split(":", 1)[1] != (p.get("bk") or ""))
        if p.get("cmp"):
            т.счёт("n.cmp_co", co < 2)
            if offers <= crossref.ПРЕДЛОЖЕНИЙ_НА_ПОЗИЦИЮ and det:
                с_ценой = [o for o in список if o.get("p") is not None and o.get("u")]
                т.счёт("n.cmp_basket", len(с_ценой) < 2 or len({o["u"] for o in с_ценой}) > 1)
        if p.get("cat"):
            т.счёт("n.cat_empty", not (p.get("oem_cat") or p.get("alts") or p.get("models")
                                       or det.get("cat_name")))
        else:
            т.счёт("n.cat_contra", bool(p.get("alts") or p.get("models") or p.get("oem_cat")))
        if p.get("oem_cat"):
            oc = p["oem_cat"]
            т.счёт("n.oem_cat", только_цифры(oc) or изготовитель_как_ключ(oc, нбр) or служебное(oc))
        of = [x for x in p.get("oem_file") or [] if isinstance(x, str)]
        for x in of:
            т.счёт("n.oem_file", только_цифры(x) or служебное(x))
            т.счёт("n.oem_file_legal", len(x) > 60 or форма_у_неизвестного(x, карта))
        if of:
            т.счёт("n.oem_file_case", len({норм(x) for x in of}) < len(of))
        # Номер элемента СП-176 приходит и строкой, и числом JSON: str() без
        # отсева по типу, иначе [1138, 340] проходит мимо.
        br = [str(x) for x in p.get("brands") or [] if x is not None and not isinstance(x, (dict, list))]
        for x in br:
            т.счёт("n.brands_digits", только_цифры(x))
            if bv is not None:
                т.счёт("n.brands_unknown", not бренд_найден(x, нбр))
        if br:
            т.счёт("n.brands_case", len({норм(x) for x in br}) < len(br))
        пары_ан = collections.Counter()
        for a in p.get("alts") or []:
            if not isinstance(a, dict):
                continue
            pn = a.get("pn")
            т.счёт("n.alt_kind", a.get("kind") not in ВИДЫ_АНАЛОГОВ)
            т.счёт("n.alt_self", not pn or ключ_кода(pn) == k)
            пары_ан[(ключ_кода(pn), a.get("kind"))] += 1
            if a.get("maker"):
                т.счёт("n.alt_maker", только_цифры(a["maker"]) or изготовитель_как_ключ(a["maker"], нбр))
            if pn and есть_правило_кода():
                т.счёт("n.alt_class", класс_написания(pn) in ("марка", "стандарт"))
        for n_ in пары_ан.values():
            т.счёт("n.alt_dup", n_ > 1)
        машины = [m for m in p.get("models") or [] if isinstance(m, str)]
        for m in машины:
            т.счёт("n.models_id", bool(re.fullmatch(r"[a-z0-9_\-]+", m)) and bool(re.search(r"[_\-]", m)))
        if машины:
            т.счёт("n.models_dup", len({норм(m) for m in машины}) < len(машины))
            т.счёт("n.models_many", len(машины) > 20)

    # Корзины.
    по_co = {str(c.get("co")): c for c in companies}
    изготовитель_сам = 0
    for k, det in подробно.items():
        список = [o for o in det.get("list") or [] if isinstance(o, dict)]
        т.счёт("n.b_shown", det.get("shown") != len(список))
        кортежи = collections.Counter()
        цены_компаний = collections.Counter()
        for o in список:
            c = o.get("c")
            if c is not None:
                т.счёт("n.o_c", str(c) not in по_co or not re.fullmatch(r"[1-9]\d*", str(c)))
                if o.get("e"):
                    т.счёт("n.o_e", (по_co.get(str(c)) or {}).get("ent") != o["e"])
                т.счёт("n.o_card_label", not (по_co.get(str(c)) or {}).get("name"))
            p_, u = o.get("p"), o.get("u")
            if p_ is not None:
                т.счёт("n.o_price", not число(p_) or p_ <= 0 or p_ > потолок_цены(u))
            if u is not None:
                т.счёт("n.o_cur", u not in ISO_ВАЛЮТЫ)
            т.счёт("n.o_pu", (p_ is None) != (u is None))
            q, t_ = o.get("q"), o.get("t")
            if q is not None:
                т.счёт("n.o_qty", not число(q) or q > 1e6 or q <= 0)
            if число(q) and число(p_) and число(t_):
                т.счёт("n.o_triple", abs(p_ * q - t_) > max(0.5, q * 0.005))
            ед = o.get("n")
            if ед is not None:
                т.счёт("n.o_unit", str(ед).isdigit() or len(str(ед)) > 15)
                if число(q) and норм(ед) in ШТУЧНЫЕ:
                    т.счёт("n.o_frac", q != int(q))
            if t_ is not None:
                # Сумма меньше цены законна при количестве меньше единицы (0,25 т).
                т.счёт("n.o_total", not число(t_) or t_ <= 0
                       or (число(p_) and t_ < p_ and not (число(q) and q < 1)))
                if (q is None and число(t_) and число(p_) and p_ > 0
                        and ед is not None and норм(ед) in ШТУЧНЫЕ):
                    т.счёт("n.o_t_noq", abs(t_ / p_ - round(t_ / p_)) > 0.01)
            r = o.get("r")
            if r is not None:
                плохо = not re.fullmatch(r"[сфнм?]{4}", str(r))
                if not плохо:
                    for буква, поле in zip(str(r), (("s",), ("y", "a"), ("l",), ("k",))):
                        есть = any(o.get(x) is not None for x in поле)
                        if (есть and буква == "н") or (not есть and буква in "сф"):
                            плохо = True
                т.счёт("n.o_r", плохо)
            if o.get("s") is not None:
                т.счёт("n.o_basis", not базис_известен(o["s"]))
            for поле in ("l", "k"):
                if o.get(поле) is not None:
                    v = o[поле]
                    т.счёт("n.o_days", not целое(v) or v < 0 or v > 730)
            if o.get("a") is not None:
                a = o["a"]
                т.счёт("n.o_adv", not число(a) or not 0 <= a <= 100 or not o.get("y"))
            if o.get("y") is not None:
                т.счёт("n.o_pay_long", len(str(o["y"])) > 200)
            for b in o.get("b") or []:
                т.счёт("n.o_b_digits", только_цифры(b))
            if o.get("m") is not None:
                m = str(o["m"])
                имя_c = (по_co.get(str(c)) or {}).get("name")
                т.счёт("n.o_m", только_цифры(m) or служебное(m) or len(m) > 60
                       or форма_у_неизвестного(m, карта))
                # Изготовитель, сам приславший КП, — прямая поставка, а не дефект.
                изготовитель_сам += bool(имя_c) and норм(без_формы(имя_c)) == норм(без_формы(m))
            if o.get("d") is not None:
                dd = день(o["d"])
                т.счёт("n.o_date", dd is None or dd > будущее or dd < РАННЯЯ_ДАТА)
            if o.get("f") is not None:
                т.счёт("n.o_f", not re.fullmatch(r"\d+", str(o["f"])))
            # Правило дубля одно на сборку и ревизию (crossref.ключ_дубля):
            # сборка схлопывает ровно то, что здесь считалось бы дублем.
            кортежи[crossref.ключ_дубля(o)] += 1
            if o.get("c") is not None and o.get("p") is not None:
                цены_компаний[(o.get("c"), o.get("p"), o.get("u"))] += 1
        for n_ in кортежи.values():
            т.счёт("n.o_dup", n_ > 1, вес=n_)
        for n_ in цены_компаний.values():
            т.счёт("n.o_repeat", n_ > 3)
        makers = [m for m in det.get("makers") or [] if isinstance(m, dict)]
        имена_изг = collections.Counter(норм(m.get("name")) for m in makers)
        for m in makers:
            т.счёт("n.mk", not m.get("name") or изготовитель_как_ключ(m.get("name"), нбр)
                   or (m.get("role") is not None and m.get("role") not in РОЛИ_ИЗГОТОВИТЕЛЕЙ)
                   or имена_изг[норм(m.get("name"))] > 1)
            # Таблица изготовителей выводит роль, страну, «что делает» и вердикт
            # как есть: pn_not_found, oem_only, in_stock — коды разведки
            # (gt/data/ship_*.json через load_parts), а не слова для сорсера.
            т.счёт("n.mk_raw", any(re.fullmatch(r"[a-z]+(_[a-z0-9]+)+", str(m.get(поле) or ""))
                                   for поле in ("role", "country", "makes", "verdict")))
            if m.get("makes"):
                делает = str(m["makes"])
                т.счёт("n.mk_note", len(делает) > 200 or bool(re.search(r"[.!?]\s+[А-ЯЁA-Z]", делает)))
    строка_по_k = {crossref.ид_позиции(p): p for p in позиции}
    for k, det in подробно.items():
        p = строка_по_k.get(k)
        if p is None:
            continue
        список = det.get("list") or []
        т.счёт("n.b_shown", det.get("shown") != min(p.get("offers") or 0,
                                                     crossref.ПРЕДЛОЖЕНИЙ_НА_ПОЗИЦИЮ)
               and bool(список))
        if det.get("makers"):
            т.счёт("n.mk_nocat", not p.get("cat"))

    # Связи со страницей брендов: страница ведёт по ключу КОДА (#k=), и адрес
    # кода, разбитого на пары, открывает все его пары.
    ключи_позиций = {p.get("k") for p in позиции}
    if isinstance(links, dict):
        for код in links.get("codes") or []:
            if isinstance(код, list) and код:
                т.счёт("n.link_k", код[0] not in ключи_позиций)
    if isinstance(bv, dict):
        tb = время(bv.get("published_at"))
        if tb and собран:
            т.счёт("n.brands_age", abs((tb - собран).total_seconds()) > 3600 * РАЗНИЦА_СНИМКОВ_Ч)

    # Мёртвый столбец: «Машины» и «Аналоги» рисуются в каждой строке списка,
    # а заполнены только у позиций каталога — у остальных «Нет связи» и «Нет».
    if позиции:
        for поле in ("models", "alts"):
            т.счёт("n.col_dead", sum(1 for p in позиции if p.get(поле)) < 0.05 * len(позиции))

    # Прошлый снимок — «у скольких стало хуже» поимённо, итог числом.
    # ПО КОДУ, А НЕ ПО ПАРЕ: снимок до правила «бренд + код» знает код, а не
    # пару, и сравнение по ключу пары объявило бы пропавшей каждую позицию с
    # брендом. Пары кода складываются: код пропал — пропал весь; бренд, цена и
    # количество — у кода в любой из пар; компании — у самой большой пары
    # (разбиение, отнявшее выбор из двух компаний, — это «стало хуже»).
    if прошлые is not None:
        def по_коду(позиции_, корзины_) -> dict:
            det_ = _предложения(корзины_)
            out = {}
            for p in позиции_:
                к_ = p.get("k")
                с = out.setdefault(к_, {"бренд": False, "цена": False, "co": 0, "q": 0})
                с["бренд"] |= bool(p.get("bk") or p.get("brands") or p.get("oem_file") or p.get("oem_cat"))
                с["цена"] |= any(isinstance(r, list) and len(r) > 2 and r[2] is not None for r in p.get("e") or [])
                с["co"] = max(с["co"], p.get("co") or 0)
                d_ = det_.get(crossref.ид_позиции(p)) or det_.get(к_) or {}
                с["q"] += sum(1 for o in d_.get("list") or [] if isinstance(o, dict) and o.get("q") is not None)
            return out

        было = по_коду(_позиции_списка([прошлые.json(k) for k in crossref.КЛЮЧИ_СПИСКА]), _корзины(прошлые))
        стало = по_коду(позиции, корзины)
        for k, старая in было.items():
            новая = стало.get(k)
            if т.счёт("n.worse_lost", новая is None):
                continue
            т.счёт("n.worse_brand", старая["бренд"] and not новая["бренд"])
            т.счёт("n.worse_price", старая["цена"] and not новая["цена"])
            т.счёт("n.worse_co", новая["co"] < старая["co"])
            т.счёт("n.worse_qty", новая["q"] < старая["q"])

    # Польза.
    n = len(позиции)
    if n:
        def правдоподобный_бренд(x):
            return bool(x) and not только_цифры(x) and not служебное(x) and not изготовитель_как_ключ(x, нбр)

        def столбец(p):
            return правдоподобный_бренд(p.get("oem_cat")) or правдоподобный_бренд((p.get("oem_file") or [None])[0])

        т.доля("u.brand", sum(1 for p in позиции if p.get("bk")), n)
        т.доля("u.brand_guess", sum(1 for p in позиции if p.get("bk") and p.get("bs") == "маска"),
               sum(1 for p in позиции if p.get("bk")))
        т.доля("u.brand_any", sum(1 for p in позиции if p.get("oem_cat") or p.get("oem_file")
                                  or p.get("brands")), n)
        т.доля("u.oem_cat", sum(1 for p in позиции if правдоподобный_бренд(p.get("oem_cat"))), n)
        т.доля("u.oem_file_only", sum(1 for p in позиции if not p.get("oem_cat") and p.get("oem_file")), n)
        т.доля("u.brands_only", sum(1 for p in позиции if p.get("brands") and not p.get("oem_cat")
                                    and not p.get("oem_file")), n)
        т.доля("u.brand_col", sum(1 for p in позиции if столбец(p)), n)
        без_выбора = [p for p in позиции if (p.get("co") or 0) <= 1]
        т.доля("u.brand_col_nochoice", sum(1 for p in без_выбора if столбец(p)), len(без_выбора))
        if isinstance(pairs, dict) and isinstance(bv, dict):
            поставщиков_бренда = collections.Counter()
            for пара in pairs.get("pairs") or []:
                if isinstance(пара, list) and пара and целое(пара[0]) and пара[0] < len(pairs.get("brands") or []):
                    поставщиков_бренда[pairs["brands"][пара[0]]] += 1
            ключ_по_имени = {}
            for b in bv.get("brands") or []:
                for v in [b.get("name"), b.get("k"), *(b.get("spellings") or [])]:
                    if v:
                        ключ_по_имени.setdefault(норм(v), b.get("k"))
            с_брендом = [p for p in без_выбора if столбец(p)]

            def адресат(p):
                имя = p.get("oem_cat") if правдоподобный_бренд(p.get("oem_cat")) else (p.get("oem_file") or [None])[0]
                return поставщиков_бренда[ключ_по_имени.get(норм(имя))] >= 1

            т.доля("u.brand_pairs", sum(1 for p in с_брендом if адресат(p)), len(с_брендом))
        т.доля("u.choice", sum(1 for p in позиции if (p.get("co") or 0) >= 2), n)
        т.доля("u.one_co_two", sum(1 for p in позиции if p.get("co") == 1 and (p.get("offers") or 0) >= 2), n)
        т.доля("u.cmp", sum(1 for p in позиции if p.get("cmp")), n)
        т.доля("u.edge_full", sum(1 for p in позиции if any(
            len(r) > 4 and r[2] is not None and r[3] and r[4] for r in p.get("e") or [])), n)
        т.доля("u.pos_co", sum(1 for p in позиции if (p.get("co") or 0) > 0), n)
        кат = [p for p in позиции if p.get("cat")]
        т.доля("u.cat", len(кат), n)
        т.доля("u.cat_alts", sum(1 for p in кат if p.get("alts")), len(кат))
        т.доля("u.cat_mfr", sum(1 for p in кат if any(isinstance(a, dict) and a.get("kind") == "номер изготовителя"
                                                    for a in p.get("alts") or [])), len(кат))
        т.доля("u.cat_models", sum(1 for p in кат if p.get("models")), len(кат))
        т.доля("u.cat_makers", sum(1 for p in кат if (подробно.get(crossref.ид_позиции(p)) or {}).get("makers")),
               len(кат))
        т.доля("u.col_models", sum(1 for p in позиции if p.get("models")), n)
        т.доля("u.col_alts", sum(1 for p in позиции if p.get("alts")), n)
        т.доля("u.n_key_cat", каталожных_с_другим_ключом, len(кат))
        т.доля("u.nochoice_demand3", sum(1 for p in без_выбора
                                         if ((p.get("demand") or {}).get("deals") or 0) >= 3), n)
        с_количеством = [p for p in позиции if (p.get("demand") or {}).get("qty") is not None]
        т.доля("u.qty_unit", sum(1 for p in с_количеством if p["demand"].get("unit_name")), len(с_количеством))
        if есть_правило_кода():
            т.доля("u.k_plausible", sum(1 for p in позиции if код_правдоподобен(p.get("k"))), n)
        # Где искать слипшийся ключ: формы ключей с наибольшим спросом (правило
        # 17 — образец, а не ключ). «aa999 ×73» — это «SS316» с 73 сделками.
        верх = sorted(((p.get("demand") or {}).get("deals") or 0, str(p.get("k") or "")) for p in позиции)[-10:]
        if верх and верх[-1][0]:
            т.заметка("ключи с наибольшим спросом (форма × сделок): "
                      + ", ".join(f"{образец(k)} ×{d}" for d, k in reversed(верх) if d))
        if bv is not None:
            бренды_поз = [x for p in позиции for x in p.get("brands") or []]
            т.доля("u.brand_found", sum(1 for x in бренды_поз if бренд_найден(x, нбр)), len(бренды_поз))
    предложения = [o for det in подробно.values() for o in det.get("list") or [] if isinstance(o, dict)]
    if предложения:
        m = len(предложения)
        т.доля("u.offer_full", sum(1 for o in предложения if o.get("p") is not None and o.get("u")
                                   and o.get("d")), m)
        if собран:
            граница = собран.date() - timedelta(days=180)
            т.доля("u.fresh", sum(1 for o in предложения if день(o.get("d")) and день(o["d"]) >= граница), m)
        т.доля("u.lead", sum(1 for o in предложения if o.get("l") is not None or o.get("k") is not None), m)
        т.доля("u.basis", sum(1 for o in предложения if o.get("s")), m)
        т.доля("u.pay", sum(1 for o in предложения if o.get("y")), m)
        пустоты = [ч for o in предложения for ч in str(o.get("r") or "????") if ч in "н?"]
        т.доля("u.gap_ours", sum(1 for ч in пустоты if ч == "?"), len(пустоты))
        т.доля("u.offer_co", sum(1 for o in предложения if o.get("c") is not None), m)
        т.доля("u.offer_qty", sum(1 for o in предложения if число(o.get("q"))), m)
        т.доля("u.o_m_self", изготовитель_сам, sum(1 for o in предложения if o.get("m") is not None))
    if companies:
        т.доля("u.co_name", sum(1 for c in companies if c.get("name")
                                and not company_names.как_ключ(c["name"])), len(companies))
        т.доля("u.co_resolved", sum(1 for c in companies if c.get("ent")), len(companies))
        if sup is not None:
            т.доля("u.co_inn", sum(1 for c in companies if инн_по_номеру.get(c.get("ent"))), len(companies))
            с_ent = [c for c in companies if c.get("ent")]
            т.доля("u.ent_open", sum(1 for c in с_ent if c["ent"] in номера), len(с_ent))
    if isinstance(links, dict):
        коды = [к[0] for к in links.get("codes") or [] if isinstance(к, list) and к]
        т.доля("u.link_k", sum(1 for к in коды if к in ключи_позиций), len(коды))
    return т


# ── /brands ──────────────────────────────────────────────────────────────────

ПРОВЕРКИ_БРЕНДОВ = {
    "b.keys": "ключа снимка нет в KV (сводка, связи, пары, 16 корзин)",
    "b.published": "published_at сводки не ISO-8601 с Z",
    "b.age": f"сводка старше {ВОЗРАСТ_БРЕНДОВ_Ч} ч (ночной шаг не отработал)",
    "b.part_date": "дата ключа ≠ дате сводки (запись оборвалась на середине)",
    "b.tiles": "плитки: пусто, отрицательное или дробное значение",
    "b.tile_order": "плитки: нарушено неравенство (brand ≤ all, customer_kp ≤ customer…)",
    "b.tile_zero": "плитка «всё» или «цены КП» равна нулю",
    "b.tile_kp_links": "плитка «цены КП» расходится с числом кодов указателя больше 5 %",
    "b.locale": "локаль базы не прошла проверку (правило 21а)",
    "b.rows_kp": "строка итогов «кодов с ценой КП» ≠ плитке",
    "b.rows_noname": "строк цены КП без поставщика больше 10 %",
    "b.cov_recount": "coverage.undefined не сходится с пересчётом",
    "b.cov_card_all": "все бренды карточек без имени (Битрикс не ответил)",
    "b.cov_nokey": "брендов без ключа словаря больше половины",
    "b.cov_noname": "поставщиков без имени больше 20 %",
    "b.name_key": "бренд: имя — сжатый ключ (строчная латиница длиннее 12)",
    "b.k_desc": "бренд: имя — описание (пояснение в скобках, тире, «по/для/или», 6+ слов без правовой формы)",
    "b.name_digits": "бренд: номер вместо имени",
    "b.name_has_digit": "бренд: в имени две цифры и больше (модель или марка; 3M, 4B — имена)",
    "b.name_cell": "бренд: имя — целая ячейка из нескольких брендов",
    "b.name_junk": "бренд: имя — пометка незнания, страна или указание к закупке",
    "b.name_empty": "бренд: имя пусто",
    "b.name_dup": "бренд: одно имя у разных ключей",
    "b.unmerged": "бренд: несведённый дубль словарного бренда",
    "b.k_dup": "бренд: ключ повторяется",
    "b.codes": "бренд: счётчики кодов противоречат друг другу",
    "b.plausible_low": "бренд: правдоподобных кодов меньше половины",
    "b.sups_pairs": "бренд: «поставщиков с ценой» ≠ числу пар",
    "b.asked": "бренд: priced > кодов или asked расходится со спросом > 5 %",
    "b.spell": "бренд: словарный без написаний, написание-мусор или spellings_n мал",
    "b.spell_shared": "бренд: одно написание у двух брендов",
    "b.atlas": "бренд: атлас подписал чужой бренд или раздел из одних «Не знаем»",
    "b.models": "бренд: models_n мал, пустое имя или повтор машины",
    "b.models_shared": "машина у двух несвязанных брендов (ячейка «A/B»; владелец и марка по атласу не в счёт)",
    "b.units": "бренд: узлы противоречат (undefined > parts, пустой список, критичность не A/B/C)",
    "b.parts_unit": "бренд: деталей с узлом больше деталей",
    "b.alts_trunc": "бренд: список изготовителей усечён до 25, страница не пишет",
    "b.alts_maker": "бренд: изготовитель — ключ или цифры",
    "b.chain": "бренд: цепочка — пустое «кто» или вид вне двух известных",
    "b.chain_proven": "бренд: доказано меньше половины рёбер цепочки",
    "b.channel": "бренд: канал пуст при состоянии или проверен больше 180 дней назад",
    "b.registry_trunc": "бренд: реестр разведки усечён до 25, страница не пишет",
    "b.registry": "бренд: реестр — checked > parts или имя-ключ",
    "b.card": "бренд: элемент карточки не из списка элементов или не число",
    "b.cloud_nomark": "облако: слово — не марка (пометка nb, служебное слово, страна) или ведёт не на бренд",
    "b.cloud_dup": "облако: ключ в двух словах, одно имя у двух слов или сведённый ключ стоит своим словом",
    "b.cloud_lost": "облако: бренд словаря без пометки не попал ни в одно слово",
    "b.s_name_key": "поставщик: имя похоже на ключ",
    "b.s_name_json": "поставщик: имя — JSON-массив («[…]»), а не название с кавычками",
    "b.s_name_number": "поставщик: номер вместо имени («Компания портала N»)",
    "b.s_k_fmt": "поставщик: ключ вне известных форматов",
    "b.s_dup": "поставщик: одно имя у разных ключей без пометки same_name",
    "b.s_reg": "поставщик: имя из реестра, а оно ключ",
    "b.s_keys": "поставщик: номер портала не число, у двух поставщиков или не сходится с ключом",
    "b.s_counts": "поставщик: кодов, карточек или файлов больше строк цены",
    "b.s_cur_case": "поставщик: валюты с регистровыми дублями («usd» и «USD»)",
    "b.s_cur_iso": "поставщик: валюта не из трёх заглавных латинских",
    "b.s_domain": "поставщик: домен без точки, с пробелом или @",
    "b.s_codes_zero": "поставщик: кодов с ценой ноль",
    "b.cb_all_noname": "бренды карточек: имени нет ни у одного",
    "b.cb": "бренд карточки: номер вместо имени, повтор элемента или имени",
    "b.cb_link": "бренд карточки: ссылка #b= в пустоту или без текста",
    "b.cov_table": "заполненность: доля, статус или «заполнено из» противоречат",
    "b.fields_src": "поле карточки без подписи источника",
    "b.dict": "словарь не прочитан или корзин не 16",
    "b.c_class": "код (ключ) — марка, размер или стандарт",
    "b.c_rubbish": "написание кода — дата, пункт, закон или год",
    "b.c_short": "код без цифры или короче 4 знаков",
    "b.c_fmt": "код не ключ lib_pn_key (ключ детали part_id) — номенклатура его не знает",
    "b.c_words": "в написании кода два слова от 4 букв и больше (наименование вместо номера)",
    "b.c_brandkey": "код равен ключу бренда (столкновение колонок)",
    "b.c_dup": "код повторяется в указателе",
    "b.c_written": "ключ написания ≠ коду — страница показывает чужое написание (и отвергнутую марку при откате на part_id)",
    "b.c_part": "корзина кода ≠ crc32 % 16 или кода нет в корзине",
    "b.c_orphan": "в корзине код, которого нет в указателе",
    "b.c_brand_idx": "код: номер бренда вне списка, повтор или ключа нет в сводке",
    "b.c_sup_idx": "код: номер поставщика вне списка или ключа нет в сводке",
    "b.c_sups": "код: поставщиков в указателе ≠ плитке корзины",
    "b.c_asked": "код: «спрашивал заказчик» в указателе ≠ корзине",
    "b.l_lists": "указатель: списки брендов и поставщиков не упорядочены, с дублями или лишними",
    "b.p_fields": "пары: порядок полей разошёлся со страницей",
    "b.p_idx": "пара: номер вне списка или ключа нет в сводке",
    "b.p_dup": "пара повторяется",
    "b.p_counts": "пара: своё слово, заказчик или с ценой больше кодов; строк меньше «с ценой»",
    "b.p_asked": "пара: закрыл спрос больше спроса бренда",
    "b.p_cur": "пара: «по валютам» не разбирается или не сходится",
    "b.p_nosup": "пара с поставщиком «(не указан)» или lib:N",
    "b.k_name": "корзина: наименования нет или оно похоже на номер",
    "b.k_glued": "корзина: наименование — склейка ячеек «количество цена сумма»",
    "b.k_n": "корзина: написание ≠ указателю",
    "b.k_asked": "корзина: спрашивал при нуле сделок или сторона вне списка",
    "b.k_bs": "корзина: подписи и ключи брендов разной длины, ключа нет или номер",
    "b.o_s": "цена: поставщика нет в сводке или повтор (поставщик, валюта, единица)",
    "b.o_single_noname": "единственная цена по коду — без поставщика",
    "b.o_cur": "цена: валюта не ISO и не «(не названа)»",
    "b.o_unit": "цена: единица не указана, либо написание единицы — число или > 20",
    "b.o_minmax": "цена: не 0 < мин ≤ медиана ≤ макс < 1e14",
    "b.o_spread": "цена: макс/мин > 100 у одного поставщика в одной валюте и единице",
    "b.o_suspect": "цена из текста = количеству, год даты КП вместо цены или выше потолка валюты за штуку",
    "b.o_null": "цены нет ни в одном предложении кода",
    "b.o_tot_bad": "цена: «количество × цена ≠ сумма» в строках",
    "b.o_rows": "цена: строк меньше пометок",
    "b.o_marks": "пометок «низкая уверенность / из суммы / из текста» больше 30 %",
    "b.o_qty": "количество > 1e6, ≤ 0 или дробное в штуках",
    "b.o_basis": "базис не начинается с Incoterms или длиннее 60",
    "b.o_dates": "даты: первая позже последней, позже следующего за сборкой дня или раньше 2015",
    "b.o_dsrc": "дата — день записи разбора, а не КП (долей)",
    "b.o_br": "бренд в его КП: подписи и ключи разной длины или ключа нет",
    "b.o_card": "бренд карточки у цены: элемент не число или не из списка",
    "b.o_card_num": "бренд карточки показан номером «элемент N»",
    "b.x_e": "ссылка /suppliers#e= не находит сущность",
    "b.x_k": "ссылка /nomenclature#k= не находит позицию",
}

ПОЛЬЗА_БРЕНДОВ = {
    "u.code_brand": "коды с ценой, у которых известен бренд",
    "u.code_brand_kp": "коды, где бренд назван в КП самого поставщика",
    "u.choice": "выбор: два и больше поставщика по коду",
    "u.cmp": "сравнимый выбор: две и больше цены в одной валюте и единице",
    "u.real_date": "предложения с ценой и настоящей датой КП",
    "u.record_date": "(справочно) дата — день записи разбора",
    "u.fresh90": "предложения с датой не старше 90 дней",
    "u.fresh365": "предложения с датой не старше года",
    "u.clean_price": "цена пригодна к сравнению (валюта, единица, без пометок)",
    "u.named_code": "коды с наименованием",
    "u.customer_kp": "спрос заказчика, закрытый ценой КП",
    "u.asked_codes": "коды с ценой, которые спрашивал заказчик",
    "u.s_name": "поставщики с настоящим именем",
    "u.s_name_rows": "то же с весом по строкам цены",
    "u.s_registry": "поставщики из реестра (KV-S-)",
    "u.s_inn": "поставщики с ИНН в реестре",
    "u.s_domain": "поставщики с доменом",
    "u.rows_sup": "строки цены с поставщиком",
    "u.dict": "бренды со словарным ключом",
    "u.dict_weight": "то же с весом по кодам",
    "u.cloud_clean": "облако: без курсивных и сжатых ключей среди первых 120",
    "u.own_pairs": "пары, где поставщик назвал бренд своим словом",
    "u.own_brands": "бренды с хотя бы одной такой парой",
    "u.coverage_closed": "поля карточки бренда в статусе «закрыто» (каталожные бренды)",
    "u.link_k": "коды открываются на /nomenclature#k=",
    "u.link_e": "поставщики KV-S- открываются на /suppliers#e=",
    "u.link_b": "ссылки #b= и #s= из указателя и пар находят цель",
    "u.qty": "предложения с количеством",
    "u.units_defined": "детали брендов с определённым узлом (Σ по брендам)",
    "u.c_long": "(справочно) коды длиннее 25 знаков — конфигуратор или склейка",
    "u.basis": "предложения с базисом",
}


def _развернуть(список, номера) -> list:
    return [список[i] if целое(i) and 0 <= i < len(список) else None for i in номера]


def ревизия_брендов(с: Снимки, сейчас) -> Вкладка:
    т = Вкладка("brands", "Бренды и коды (/brands)", ПРОВЕРКИ_БРЕНДОВ, ПОЛЬЗА_БРЕНДОВ)
    bv = с.json(brands.КЛЮЧ)
    т.снимок(brands.КЛЮЧ, с, сейчас, (bv or {}).get("published_at"))
    links = с.json(brands.КЛЮЧ_СВЯЗЕЙ)
    pairs = с.json(brands.КЛЮЧ_ПАР)
    корзины = [с.json(k) for k in brands.КЛЮЧИ_КОРЗИН]
    for v in [bv, links, pairs] + корзины:
        т.счёт("b.keys", not isinstance(v, dict))
    if not isinstance(bv, dict):
        return т
    sup = с.json(КЛЮЧ_ПОСТАВЩИКОВ)
    позиции_номенклатуры = {p.get("k") for p in _позиции_списка(
        [с.json(k) for k in crossref.КЛЮЧИ_СПИСКА])}
    есть_номенклатура = any(isinstance(с.json(k), dict) for k in crossref.КЛЮЧИ_СПИСКА)

    собран = время(bv.get("published_at"))
    т.счёт("b.published", собран is None)
    if собран:
        т.счёт("b.age", возраст_ч(собран, сейчас) >= ВОЗРАСТ_БРЕНДОВ_Ч)
    for v in [links, pairs] + корзины:
        if isinstance(v, dict):
            т.счёт("b.part_date", v.get("published_at") != bv.get("published_at"))

    # Плитки и итоги.
    tot = bv.get("totals") or {}
    плитки = {p.get("id"): p.get("value") for p in tot.get("tiles") or [] if isinstance(p, dict)}
    for ид, v in плитки.items():
        т.счёт("b.tiles", v is None or not число(v) or v < 0 or v != int(v))
    п = {k: v for k, v in плитки.items() if число(v)}
    for меньше, больше in (("brand", "all"), ("customer", "all"), ("customer_kp", "customer"),
                           ("customer_kp", "kp"), ("customer_buy", "customer"),
                           ("supplier", "kp"), ("kp", "buy")):
        if меньше in п and больше in п:
            т.счёт("b.tile_order", п[меньше] > п[больше])
    for ид in ("all", "kp"):
        if ид in п:
            т.счёт("b.tile_zero", п[ид] == 0)
    коды_указателя = [к for к in (links or {}).get("codes") or [] if isinstance(к, list) and к]
    if "kp" in п and isinstance(links, dict) and п["kp"]:
        т.счёт("b.tile_kp_links", abs(п["kp"] - len(коды_указателя)) > 0.05 * п["kp"])
    if "locale_ok" in tot:
        т.счёт("b.locale", tot.get("locale_ok") is not True)
    строки = {(r[0], r[1]): r[2] for r in tot.get("rows") or [] if isinstance(r, list) and len(r) >= 3}
    kp_rows = строки.get(("цены КП", "кодов с ценой КП"))
    if kp_rows is not None and "kp" in п:
        т.счёт("b.rows_kp", kp_rows != п["kp"])
    без = строки.get(("цены КП", "строк цены, поставщик: поставщик не указан"))
    всего_кп = next((v for (раздел, м), v in строки.items() if м == "строк цены «разбор КП»"), None)
    if число(без) and число(всего_кп) and всего_кп:
        т.счёт("b.rows_noname", без / всего_кп > 0.10)

    бренды_ = [b for b in bv.get("brands") or [] if isinstance(b, dict)]
    поставщики_ = [s for s in bv.get("suppliers") or [] if isinstance(s, dict)]
    карточки_ = [c for c in bv.get("card_brands") or [] if isinstance(c, dict)]
    und = (bv.get("coverage") or {}).get("undefined") or {}
    if und:
        пересчёт = {"brands_without_dict_key": sum(1 for b in бренды_ if not b.get("dict")),
                    "brands": len(бренды_),
                    "suppliers_without_name": sum(1 for s in поставщики_
                                                  if str(s.get("from", "")).startswith("имени нет")),
                    "suppliers": len(поставщики_),
                    "card_brands_without_name": sum(1 for c in карточки_ if not c.get("name")),
                    "card_brands": len(карточки_)}
        for имя, v in пересчёт.items():
            if т.счёт("b.cov_recount", und.get(имя) != v):
                т.заметка(f"расходится coverage.undefined.{имя}")
        if карточки_:
            т.счёт("b.cov_card_all", und.get("card_brands_without_name") == len(карточки_))
        if бренды_:
            т.счёт("b.cov_nokey", пересчёт["brands_without_dict_key"] / len(бренды_) > 0.5)
        if поставщики_:
            т.счёт("b.cov_noname", пересчёт["suppliers_without_name"] / len(поставщики_) > 0.2)

    # Бренды.
    ключи_брендов = {b.get("k") for b in бренды_}
    сколько_k = collections.Counter(b.get("k") for b in бренды_)
    имена = collections.defaultdict(set)
    for b in бренды_:
        if b.get("name"):
            имена[str(b["name"]).casefold()].add(b.get("k"))
    написание_у = collections.defaultdict(set)
    словарные = {}
    for b in бренды_:
        for н in b.get("spellings") or []:
            kн = codes_sql.ключ_написания(н)
            if kн:
                написание_у[kн].add(b.get("k"))
                if b.get("dict"):
                    словарные[kн] = b.get("k")
    пар_бренда = collections.Counter()
    if isinstance(pairs, dict):
        for пара in pairs.get("pairs") or []:
            if isinstance(пара, list) and пара and целое(пара[0]) and 0 <= пара[0] < len(pairs.get("brands") or []):
                пар_бренда[pairs["brands"][пара[0]]] += 1
    машины_брендов = collections.defaultdict(set)
    узлов_деталей = [0, 0]
    известные_бренды = бренды_брендов(bv)
    карточные_ид = {str(c.get("id")) for c in карточки_}
    for b in бренды_:
        k, имя = b.get("k"), b.get("name")
        if т.счёт("b.name_empty", not имя):
            continue
        имя = str(имя)
        т.счёт("b.name_key", имя == k and bool(re.fullmatch(r"[a-z0-9]{13,}", имя)))
        т.счёт("b.k_desc", похоже_на_описание(имя))
        т.счёт("b.name_digits", bool(re.fullmatch(r"[\d\s,.;]+", имя)) or bool(re.fullmatch(r"\d+", str(k))))
        # «3M», «4B» — имена: одна цифра не обвиняет, белый список запроса брендов тоже.
        т.счёт("b.name_has_digit", len(re.findall(r"\d", имя)) >= 2 and not только_цифры(имя)
               and codes_sql.ключ_написания(имя) not in codes_sql.WHITELIST)
        т.счёт("b.name_cell", bool(re.search(r"[,;/()]|\s(и|или|or)\s", имя))
               and codes_sql.ключ_написания(имя) != k)
        т.счёт("b.name_junk", служебное(имя) or bool(НЕ_КОМПАНИЯ.search(имя)))
        т.счёт("b.name_dup", len(имена[имя.casefold()]) > 1)
        if not b.get("dict"):
            т.счёт("b.unmerged", k in словарные and словарные[k] != k)
        т.счёт("b.k_dup", сколько_k[k] > 1)
        cd = b.get("codes") or {}
        if cd:
            any_ = cd.get("any") or 0
            т.счёт("b.codes", (cd.get("customer_kp") or 0) > (cd.get("customer") or 0)
                   or (cd.get("customer_buy") or 0) > (cd.get("customer") or 0)
                   or any_ < max(cd.get("customer") or 0, cd.get("kp_file") or 0, cd.get("catalog") or 0)
                   or (cd.get("plausible") or 0) > any_
                   or (cd.get("deals") or 0) > (cd.get("rows_customer") or 0)
                   or (any_ == 0 and (b.get("sups") or 0) > 0))
            if any_:
                т.счёт("b.plausible_low", (cd.get("plausible") or 0) / any_ < 0.5)
        elif b.get("sups"):
            т.счёт("b.codes", True)
        if isinstance(pairs, dict) and (b.get("sups") or пар_бренда[k]):
            т.счёт("b.sups_pairs", (b.get("sups") or 0) != пар_бренда[k])
        if "priced" in b or "asked" in b:
            спрос = cd.get("customer") or 0
            т.счёт("b.asked", (b.get("priced") or 0) > (cd.get("any") or 0)
                   or abs((b.get("asked") or 0) - спрос) > 0.05 * max(спрос, b.get("asked") or 0, 1))
        sp = b.get("spellings") or []
        т.счёт("b.spell", (b.get("dict") and not sp) or any(служебное(x) for x in sp)
               or (b.get("spellings_n") or 0) < len(sp))
        for x in sp:
            т.счёт("b.spell_shared", len(написание_у[codes_sql.ключ_написания(x)]) > 1)
        at = b.get("atlas")
        if isinstance(at, dict):
            т.счёт("b.atlas", codes_sql.ключ_написания(at.get("name") or "") != k
                   or not any(at.get(x) for x in ("country", "owner", "former", "pn", "active", "discontinued")))
        mods = [m for m in b.get("models") or [] if isinstance(m, dict)]
        if mods:
            имена_маш = [норм(m.get("name")) for m in mods]
            т.счёт("b.models", (b.get("models_n") or 0) < len(mods) or any(not x for x in имена_маш)
                   or len(set(имена_маш)) < len(имена_маш))
            for m in mods:
                if m.get("id"):
                    машины_брендов[m["id"]].add(k)
        for часть in (b.get("units") or {}).values():
            if not isinstance(часть, dict):
                continue
            список = [u for u in часть.get("list") or [] if isinstance(u, dict)]
            т.счёт("b.units", (часть.get("undefined") or 0) > (часть.get("parts") or 0)
                   or ((часть.get("units") or 0) > 0 and not список)
                   or any(u.get("crit") not in (None, "A", "B", "C") or not u.get("name") for u in список))
            if число(часть.get("parts")) and часть["parts"] > 0:
                узлов_деталей[0] += часть["parts"] - min(часть.get("undefined") or 0, часть["parts"])
                узлов_деталей[1] += часть["parts"]
        parts = b.get("parts")
        if isinstance(parts, dict):
            т.счёт("b.parts_unit", (parts.get("unit") or 0) > (parts.get("n") or 0))
        al = b.get("alts")
        if isinstance(al, dict):
            makers = [m for m in al.get("makers") or [] if isinstance(m, list) and m]
            т.счёт("b.alts_trunc", (al.get("makers_n") or 0) > len(makers))
            for m in makers:
                т.счёт("b.alts_maker", изготовитель_как_ключ(m[0], известные_бренды) or только_цифры(m[0]))
        ch = b.get("chain")
        if isinstance(ch, dict):
            т.счёт("b.chain", (ch.get("makers") or 0) + (ch.get("notes") or 0) != (ch.get("n") or 0)
                   or any(isinstance(r, dict) and not r.get("to") for r in ch.get("list") or []))
            if ch.get("n"):
                т.счёт("b.chain_proven", (ch.get("proven") or 0) / ch["n"] < 0.5)
        kn = b.get("channel")
        if isinstance(kn, dict):
            проверен = день_свободно(kn.get("checked"))
            т.счёт("b.channel", (bool(kn.get("state")) and not kn.get("channel"))
                   or (проверен is not None and (сейчас.date() - проверен).days > 180))
        рг = b.get("registry")
        if isinstance(рг, dict):
            список = [x for x in рг.get("list") or [] if isinstance(x, dict)]
            т.счёт("b.registry_trunc", (рг.get("n") or 0) > len(список))
            for x in список:
                т.счёт("b.registry", (x.get("checked") or 0) > (x.get("parts") or 0)
                       or company_names.как_ключ(x.get("name")) or только_цифры(x.get("name")))
        for эл in b.get("card") or []:
            if isinstance(эл, dict):
                т.счёт("b.card", not re.fullmatch(r"\d+", str(эл.get("id")))
                       or str(эл.get("id")) not in карточные_ид)
    # Облако (library/brands.облако): в нём только марки, одна марка — одно слово.
    слова_облака = [s for s in ((bv or {}).get("cloud") or []) if isinstance(s, dict)]
    if слова_облака:
        по_ключу_б = {b.get("k"): b for b in бренды_}
        в_словах = collections.Counter()
        имена_слов = collections.Counter(str(s.get("name") or "").casefold() for s in слова_облака)
        for s in слова_облака:
            for k in set(s.get("m") or []) | {s.get("k")}:
                в_словах[k] += 1
        for s in слова_облака:
            b = по_ключу_б.get(s.get("k"))
            т.счёт("b.cloud_nomark", b is None or bool(b.get("nb")) or служебное(s.get("name"))
                   or bool(re.fullmatch(r"\s*%.*", str(s.get("name") or ""))))
            т.счёт("b.cloud_dup", any(в_словах[k] > 1 for k in set(s.get("m") or []) | {s.get("k")})
                   or имена_слов[str(s.get("name") or "").casefold()] > 1
                   or (b is not None and bool(b.get("cg"))))
        for b in бренды_:
            if b.get("dict") and not b.get("nb"):
                т.счёт("b.cloud_lost", в_словах[b.get("k")] == 0)
    # Машина у двух брендов законна, когда бренды связаны: владелец и марка
    # (Solar Turbines — Caterpillar), пакетировщик и изготовитель, «Siemens» и
    # «Siemens Energy». Связь — атлас (owner, former) или ключ одного —
    # начало ключа другого.
    связи_бренда = collections.defaultdict(set)
    for b in бренды_:
        at = b.get("atlas") if isinstance(b.get("atlas"), dict) else {}
        for поле in ("owner", "former"):
            for часть_ in brand_registry.части_имени(без_формы(at.get(поле) or "")):
                связи_бренда[b.get("k")].add(часть_)

    def связаны(a, b_) -> bool:
        a, b_ = str(a or ""), str(b_ or "")
        префикс = min(len(a), len(b_)) >= 4 and (a.startswith(b_) or b_.startswith(a))
        return bool(a) and bool(b_) and (префикс or b_ in связи_бренда[a] or a in связи_бренда[b_])

    for ид, у_брендов in машины_брендов.items():
        список_ = sorted(у_брендов, key=str)
        т.счёт("b.models_shared", any(not связаны(a, b_) for i, a in enumerate(список_) for b_ in список_[i + 1:]))

    # Поставщики.
    ключи_поставщиков = {s.get("k") for s in поставщики_}
    по_имени = collections.defaultdict(set)
    for s in поставщики_:
        if s.get("name"):
            по_имени[имя_ключ_компании(re.sub(r"(?i)\b(ооо|оао|зао|пао|ао|ип|llc|ltd|inc|gmbh)\b\.?", "",
                                               str(s["name"])))].add(s.get("k"))
    портал = collections.Counter(x for s in поставщики_ for x in set(s.get("keys") or []))
    for s in поставщики_:
        имя, k = str(s.get("name") or ""), str(s.get("k") or "")
        т.счёт("b.s_name_key", company_names.как_ключ(имя))
        # Кавычки в названии из Битрикса — «ООО "Ромашка"» — это имя; JSON — только массив.
        т.счёт("b.s_name_json", bool(re.fullmatch(r"\s*\[.*\]\s*", имя, re.S)))
        т.счёт("b.s_name_number", имя.startswith("Компания портала ") or bool(re.match(r"ключ \d", имя)))
        т.счёт("b.s_k_fmt", not (re.fullmatch(r"KV-S-\d{6}-\w", k) or k.startswith("KV-G-")
                                 or re.fullmatch(r"bitrix:\d+", k) or re.fullmatch(r"lib:\S+", k)
                                 or k == "(не указан)"))
        if имя and k != "(не указан)":
            т.счёт("b.s_dup", len(по_имени[имя_ключ_компании(re.sub(
                r"(?i)\b(ооо|оао|зао|пао|ао|ип|llc|ltd|inc|gmbh)\b\.?", "", имя))]) > 1
                and not s.get("same_name"))
        if s.get("reg") or s.get("from") == "реестр":
            т.счёт("b.s_reg", company_names.как_ключ(s.get("reg") or имя))
        keys = [str(x) for x in s.get("keys") or []]
        if keys or k.startswith("bitrix:"):
            т.счёт("b.s_keys", any(not x.isdigit() for x in keys) or any(портал[x] > 1 for x in keys)
                   or (k.startswith("bitrix:") and keys != [k[len("bitrix:"):]]))
        rows = s.get("rows") or 0
        if "rows" in s or "codes" in s:
            т.счёт("b.s_counts", (s.get("codes") or 0) > rows or (s.get("cards") or 0) > rows
                   or (s.get("files") or 0) > rows)
            т.счёт("b.s_codes_zero", not s.get("codes"))
        cur = [str(x) for x in s.get("cur") or []]
        if cur:
            т.счёт("b.s_cur_case", len({x.strip().upper() for x in cur}) < len(cur))
            for x in cur:
                т.счёт("b.s_cur_iso", not ВАЛЮТА_RX.fullmatch(x))
        for d in s.get("domains") or []:
            т.счёт("b.s_domain", "." not in d or " " in d or "@" in d)

    # Бренды с карточки запроса.
    if карточки_:
        т.счёт("b.cb_all_noname", not any(c.get("name") for c in карточки_))
        ид_ = collections.Counter(str(c.get("id")) for c in карточки_)
        имена_к = collections.defaultdict(set)
        for c in карточки_:
            if c.get("name"):
                имена_к[str(c["name"]).casefold()].add(c.get("k"))
        for c in карточки_:
            имя = c.get("name")
            т.счёт("b.cb", (bool(имя) and bool(re.fullmatch(r"[\d\s,;]+", str(имя)))) or ид_[str(c.get("id"))] > 1
                   or (bool(имя) and len(имена_к[str(имя).casefold()]) > 1))
            if c.get("k"):
                т.счёт("b.cb_link", c["k"] not in ключи_брендов or not имя)

    # Заполненность.
    cov = bv.get("coverage") or {}
    пороги = cov.get("thresholds") or {"closed": 80, "partial": 40}
    for имя_вс, вс in (cov.get("universes") or {}).items():
        for f in (вс or {}).get("fields") or []:
            filled, total, pct = f.get("filled") or 0, f.get("total") or 0, f.get("pct")
            ждём = round(100 * filled / total, 1) if total else 0.0
            статус = ("закрыто" if (pct or 0) >= пороги.get("closed", 80)
                      else "частично" if (pct or 0) >= пороги.get("partial", 40) else "дыра")
            т.счёт("b.cov_table", pct != ждём or f.get("status") != статус or filled > total)
        if имя_вс == "catalog":
            т.счёт("b.cov_table", (вс or {}).get("total") != sum(
                1 for b in бренды_ if (b.get("parts") or {}).get("n")))
    источники = {f.get("id") for f in bv.get("fields") or [] if isinstance(f, dict) and f.get("src")}
    for ид in brands.ИМЕНА_ПОЛЕЙ:
        т.счёт("b.fields_src", ид not in источники)
    dct = bv.get("dict") or {}
    т.счёт("b.dict", dct.get("records") == 0 or bv.get("parts") != brands.КОРЗИН)

    # Указатель кодов.
    список_б = (links or {}).get("brands") or []
    список_с = (links or {}).get("suppliers") or []
    подробно = {}
    корзина_кода = collections.Counter()
    for i, b in enumerate(корзины):
        if isinstance(b, dict):
            for код, v in (b.get("codes") or {}).items():
                подробно[код] = (i, v)
                корзина_кода[код] += 1
    сколько_кодов = collections.Counter(к[0] for к in коды_указателя)
    исп_б, исп_с = set(), set()
    for к in коды_указателя:
        код = str(к[0])
        написано = к[1] if len(к) > 1 else None
        # Класс судится по КОДУ: номер, отвергнутый правилом, откатывается на
        # part_id (codes_sql.price_code), и это верный откат. Написание при этом
        # может остаться отвергнутым («SS316») — это ловит b.c_written: страница
        # показывает написание вместо кода.
        if есть_правило_кода():
            т.счёт("b.c_class", класс_не_кода(код) is not None, форма=код)
        if написано is not None:
            w = str(написано)
            т.счёт("b.c_rubbish", docfilter._rubbish(w) or bool(docfilter.SIZE.fullmatch(w))
                   or bool(docfilter.DUPN.fullmatch(w)))
            т.счёт("b.c_words", слов_в_коде(w) >= 2)
            т.счёт("b.c_written", ключ_кода(w) != код)
        т.счёт("b.c_short", not re.search(r"\d", код) or len(код) < 4)
        т.счёт("b.c_fmt", not re.fullmatch(r"[0-9a-zа-я]{2,80}", код))
        т.счёт("b.c_brandkey", код in ключи_брендов)
        т.счёт("b.c_dup", сколько_кодов[код] > 1)
        часть = к[2] if len(к) > 2 else None
        т.счёт("b.c_part", часть != brands.корзина(код) or код not in подробно
               or корзина_кода[код] > 1 or (код in подробно and подробно[код][0] != часть))
        номера_б = к[3] if len(к) > 3 and isinstance(к[3], list) else []
        номера_с = к[4] if len(к) > 4 and isinstance(к[4], list) else []
        исп_б.update(номера_б)
        исп_с.update(номера_с)
        бк = _развернуть(список_б, номера_б)
        т.счёт("b.c_brand_idx", None in бк or len(set(номера_б)) < len(номера_б)
               or any(x not in ключи_брендов for x in бк if x is not None))
        ск = _развернуть(список_с, номера_с)
        т.счёт("b.c_sup_idx", None in ск or any(x not in ключи_поставщиков for x in ск if x is not None))
        det = (подробно.get(код) or (None, {}))[1]
        if det:
            т.счёт("b.c_sups", len([x for x in ск if x and x != "(не указан)"]) != (det.get("sups") or 0))
            т.счёт("b.c_asked", bool(к[5] if len(к) > 5 else 0) != bool(det.get("asked")))
            т.счёт("b.k_n", (написано or код) != det.get("n", код))
    for код in подробно:
        т.счёт("b.c_orphan", код not in сколько_кодов)
    if isinstance(links, dict):
        for список, исп in ((список_б, исп_б), (список_с, исп_с)):
            т.счёт("b.l_lists", список != sorted(set(список)) or len(исп) < len(список))

    # Пары.
    if isinstance(pairs, dict):
        т.счёт("b.p_fields", (pairs.get("pair_fields") or [])[:11] != [
            "brand", "supplier", "codes", "own_row", "by_customer", "by_other_kp", "by_catalog",
            "priced", "price_rows", "by_currency", "asked_closed"])
        пб, пс = pairs.get("brands") or [], pairs.get("suppliers") or []
        спрос_бренда = {b.get("k"): b.get("asked") for b in бренды_}
        виденные = collections.Counter()
        for пара in pairs.get("pairs") or []:
            if not isinstance(пара, list) or len(пара) < 11:
                т.счёт("b.p_idx", True)
                continue
            bk, sk = _развернуть(пб, [пара[0]])[0], _развернуть(пс, [пара[1]])[0]
            т.счёт("b.p_idx", bk is None or sk is None or bk not in ключи_брендов
                   or sk not in ключи_поставщиков)
            виденные[(пара[0], пара[1])] += 1
            codes, own, by_cust, priced, prow = (x if число(x) else 0 for x in
                                                 (пара[2], пара[3], пара[4], пара[7], пара[8]))
            т.счёт("b.p_counts", own > codes or by_cust > codes or priced > codes or prow < priced)
            if спрос_бренда.get(bk) is not None:
                т.счёт("b.p_asked", (пара[10] if число(пара[10]) else 0) > (спрос_бренда.get(bk) or 0))
            if пара[9]:
                части_в = [x.strip() for x in str(пара[9]).split(";") if x.strip()]
                разбор = [re.fullmatch(r"([A-Z]{3}|\(не названа\)) (\d+)", x) for x in части_в]
                т.счёт("b.p_cur", any(m is None for m in разбор)
                       or sum(int(m[2]) for m in разбор if m) < priced)
            т.счёт("b.p_nosup", sk == "(не указан)" or bool(re.fullmatch(r"lib:\S+", str(sk or ""))))
        for n_ in виденные.values():
            т.счёт("b.p_dup", n_ > 1)

    # Корзины: код и цены.
    карточка_ид = {str(c.get("id")): c for c in карточки_}
    предложения = []
    будущее = (собран.date() if собран else сейчас.date()) + timedelta(days=1)
    for код, (_, det) in подробно.items():
        имя = det.get("name")
        т.счёт("b.k_name", not имя or класс_не_кода(имя) is not None
               or not re.search(r"[A-Za-zА-Яа-яЁё]", str(имя)))
        if имя:
            т.счёт("b.k_glued", склейка_ячеек(имя))
        т.счёт("b.k_asked", (bool(det.get("asked")) and not det.get("deals"))
               or any(x not in СТОРОНЫ for x in re.split(r"\s*[,;]\s*", str(det.get("sides") or "")) if x))
        for подп, ключи in (("bs", "bsk"), ("bc", "bck")):
            if det.get(подп) or det.get(ключи):
                подписи = [x for x in str(det.get(подп) or "").split("; ") if x]
                т.счёт("b.k_bs", len(подписи) != len(det.get(ключи) or [])
                       or any(x not in ключи_брендов for x in det.get(ключи) or [])
                       or any(re.fullmatch(r"[\d\s,;]+", x) for x in подписи))
        offers = [o for o in det.get("offers") or [] if isinstance(o, dict)]
        тройки = collections.Counter((o.get("s"), o.get("cur"), o.get("unit")) for o in offers)
        с_ценой = {o.get("s") for o in offers if число(o.get("min")) and o.get("s") != "(не указан)"}
        if offers:
            т.счёт("b.o_null", all(o.get("min") is None for o in offers))
            if len(offers) == 1:
                т.счёт("b.o_single_noname", offers[0].get("s") == "(не указан)")
            т.счёт("b.c_sups", (det.get("sups") or 0) != len(с_ценой))
        for o in offers:
            предложения.append(o)
            т.счёт("b.o_s", o.get("s") not in ключи_поставщиков
                   or тройки[(o.get("s"), o.get("cur"), o.get("unit"))] > 1)
            cur = o.get("cur")
            if cur is not None:
                т.счёт("b.o_cur", not (ВАЛЮТА_RX.fullmatch(str(cur)) or cur == "(не названа)"))
            if o.get("unit") is not None or o.get("unit_w") is not None:
                uw = o.get("unit_w")
                т.счёт("b.o_unit", o.get("unit") == "(не указана)"
                       or (uw is not None and (str(uw).replace(".", "").isdigit() or len(str(uw)) > 20)))
            mn, md, mx = o.get("min"), o.get("med"), o.get("max")
            if mn is not None or mx is not None:
                т.счёт("b.o_minmax", not (число(mn) and число(md) and число(mx)
                                          and 0 < mn <= md <= mx < 1e14))
                if число(mn) and число(mx) and mn > 0:
                    т.счёт("b.o_spread", mx / mn > 100)
                # Цена, равная количеству, и цена-год — улики, только когда цена
                # взята из текста (пометка text) или год совпал с датой КП: 10 шт
                # по 10 USD и цена 2000 ₽ — обычные цены.
                штучно = норм(o.get("unit")) in ШТУЧНЫЕ
                годы = {d.year for d in (день(o.get("d1")), день(o.get("d2"))) if d}
                т.счёт("b.o_suspect", (bool(o.get("text")) and число(o.get("qty")) and o["qty"] > 1
                                       and mn == o["qty"])
                       or (o.get("rows") == 1 and число(mn) and mn == int(mn) and 1990 <= mn <= 2035
                           and (int(mn) in годы or bool(o.get("text"))))
                       or (штучно and число(md) and md > потолок_цены(cur)))
            rows = o.get("rows") or 0
            if o.get("tot_bad") is not None or o.get("tot_ok") is not None:
                т.счёт("b.o_tot_bad", (o.get("tot_bad") or 0) > 0)
            т.счёт("b.o_rows", any((o.get(x) or 0) > rows for x in ("low", "from_total", "cur_file", "text"))
                   or (o.get("tot_ok") or 0) + (o.get("tot_bad") or 0) > rows)
            q = o.get("qty")
            if q is not None:
                т.счёт("b.o_qty", not число(q) or q > 1e6 or q <= 0
                       or (норм(o.get("unit")) in ШТУЧНЫЕ and q != int(q)))
            if o.get("basis") is not None:
                б = str(o["basis"])
                т.счёт("b.o_basis", len(б) > 60 or not базис_известен(б))
            d1, d2 = день(o.get("d1")), день(o.get("d2"))
            if o.get("d1") or o.get("d2"):
                т.счёт("b.o_dates", (o.get("d1") and d1 is None) or (o.get("d2") and d2 is None)
                       or (d1 and d2 and d1 > d2) or (d2 is not None and d2 > будущее)
                       or (d1 is not None and d1 < РАННЯЯ_ДАТА))
            if o.get("dsrc") is not None:
                т.счёт("b.o_dsrc", str(o["dsrc"]).startswith("запись разбора"))
            if o.get("br") or o.get("brk"):
                подписи = [x for x in str(o.get("br") or "").split("; ") if x]
                т.счёт("b.o_br", len(подписи) != len(o.get("brk") or [])
                       or any(x not in ключи_брендов for x in o.get("brk") or []))
            for эл in o.get("card") or []:
                т.счёт("b.o_card", not re.fullmatch(r"\d+", str(эл)) or str(эл) not in карточка_ид)
                т.счёт("b.o_card_num", not (карточка_ид.get(str(эл)) or {}).get("name"))
    if предложения:
        помеченных = sum(1 for o in предложения if o.get("low") or o.get("from_total") or o.get("text"))
        т.счёт("b.o_marks", помеченных / len(предложения) > 0.30)

    # Внешние ссылки.
    if isinstance(sup, dict):
        номера = {e.get("number") for e in _сущности(sup) if e.get("number")}
        for s in поставщики_:
            if re.match(r"KV-S-", str(s.get("k") or "")):
                т.счёт("b.x_e", s["k"] not in номера)
    if есть_номенклатура:
        for к in коды_указателя:
            т.счёт("b.x_k", к[0] not in позиции_номенклатуры)

    # Польза.
    n = len(коды_указателя)
    if n:
        т.доля("u.code_brand", sum(1 for к in коды_указателя if len(к) > 3 and к[3]), n)
        т.доля("u.code_brand_kp", sum(1 for к in коды_указателя if any(
            o.get("brk") for o in (подробно.get(к[0]) or (0, {}))[1].get("offers") or [])), n)
        т.доля("u.choice", sum(1 for к in коды_указателя if len([x for x in _развернуть(список_с, к[4] if len(к) > 4 else [])
                                                                if x and x != "(не указан)"]) >= 2), n)

        def сравнимо(код):
            группы = collections.defaultdict(set)
            for o in (подробно.get(код) or (0, {}))[1].get("offers") or []:
                if число(o.get("min")) and o.get("s") != "(не указан)":
                    группы[(o.get("cur"), o.get("unit"))].add(o.get("s"))
            return any(len(v) >= 2 for v in группы.values())

        т.доля("u.cmp", sum(1 for к in коды_указателя if сравнимо(к[0])), n)
        т.доля("u.named_code", sum(1 for к in коды_указателя if (подробно.get(к[0]) or (0, {}))[1].get("name")), n)
        т.доля("u.asked_codes", sum(1 for к in коды_указателя if len(к) > 5 and к[5]), n)
        if есть_номенклатура:
            т.доля("u.link_k", sum(1 for к in коды_указателя if к[0] in позиции_номенклатуры), n)
    if предложения:
        m = len(предложения)
        т.доля("u.real_date", sum(1 for o in предложения if число(o.get("min")) and o.get("dsrc") == "дата цены"), m)
        т.доля("u.record_date", sum(1 for o in предложения if str(o.get("dsrc") or "").startswith("запись разбора")), m)
        for код_, дней in (("u.fresh90", 90), ("u.fresh365", 365)):
            т.доля(код_, sum(1 for o in предложения if день(o.get("d2"))
                             and (сейчас.date() - день(o["d2"])).days <= дней), m)
        т.доля("u.clean_price", sum(1 for o in предложения if число(o.get("min"))
                                    and o.get("cur") not in (None, "(не названа)")
                                    and o.get("unit") not in (None, "(не указана)")
                                    and not (o.get("low") or o.get("from_total") or o.get("text") or o.get("tot_bad"))), m)
        т.доля("u.qty", sum(1 for o in предложения if число(o.get("qty"))), m)
    if узлов_деталей[1]:
        т.доля("u.units_defined", узлов_деталей[0], узлов_деталей[1])
    if коды_указателя:
        т.доля("u.c_long", sum(1 for к in коды_указателя if len(str(к[0])) > 25), len(коды_указателя))
        т.доля("u.basis", sum(1 for o in предложения if o.get("basis")), m)
    if "customer" in п and п["customer"]:
        т.доля("u.customer_kp", п.get("customer_kp") or 0, п["customer"])
    if поставщики_:
        def настоящее(s):
            имя = str(s.get("name") or "")
            откуда = str(s.get("from") or "")
            return ((откуда.startswith("Битрикс: карточка компании") or откуда == "реестр")
                    and not company_names.как_ключ(имя) and not имя.startswith("Компания портала")
                    and not имя.startswith("["))

        т.доля("u.s_name", sum(1 for s in поставщики_ if настоящее(s)), len(поставщики_))
        т.доля("u.s_name_rows", sum(s.get("rows") or 0 for s in поставщики_ if настоящее(s)),
               sum(s.get("rows") or 0 for s in поставщики_))
        т.доля("u.s_registry", sum(1 for s in поставщики_ if str(s.get("k") or "").startswith("KV-S-")),
               len(поставщики_))
        т.доля("u.s_domain", sum(1 for s in поставщики_ if s.get("domains")), len(поставщики_))
        строк = sum(s.get("rows") or 0 for s in поставщики_)
        т.доля("u.rows_sup", строк - sum(s.get("rows") or 0 for s in поставщики_ if s.get("k") == "(не указан)"),
               строк)
        if isinstance(sup, dict):
            инн = {e.get("number") for e in _сущности(sup) if e.get("number") and e.get("inn")}
            т.доля("u.s_inn", sum(1 for s in поставщики_ if s.get("k") in инн), len(поставщики_))
            kv = [s for s in поставщики_ if str(s.get("k") or "").startswith("KV-S-")]
            номера = {e.get("number") for e in _сущности(sup) if e.get("number")}
            т.доля("u.link_e", sum(1 for s in kv if s["k"] in номера), len(kv))
    if бренды_:
        т.доля("u.dict", sum(1 for b in бренды_ if b.get("dict")), len(бренды_))
        т.доля("u.dict_weight", sum((b.get("codes") or {}).get("any") or 0 for b in бренды_ if b.get("dict")),
               sum((b.get("codes") or {}).get("any") or 0 for b in бренды_))
        # Облако — слова сводки (library/brands.облако): слово ведёт на бренд,
        # его и судим. Снимок до слов облака — первые 120 брендов, как было.
        по_ключу_о = {b.get("k"): b for b in бренды_}
        слова_о = [s for s in (bv.get("cloud") or []) if isinstance(s, dict)]
        if слова_о:
            # Слово — марка словаря, если сборка так его пометила (d: словарь
            # брендов или справочник рядов), даже когда ведёт на написание без ключа.
            облако = [{**(по_ключу_о.get(s.get("k")) or {}), **({"dict": True} if s.get("d") else {})}
                      for s in sorted(слова_о, key=lambda s: -(s.get("w") or 0))[:120]]
        else:
            облако = sorted(бренды_, key=lambda b: -(((b.get("codes") or {}).get("any"))
                                                     or (b.get("parts") or {}).get("n") or 0))[:120]
        т.доля("u.cloud_clean", sum(1 for b in облако if b.get("dict") and not b.get("nb")
                                    and not (b.get("name") == b.get("k") and re.fullmatch(r"[a-z0-9]{13,}", str(b.get("k"))))),
               len(облако))
    if isinstance(pairs, dict) and pairs.get("pairs"):
        пары_ = [x for x in pairs["pairs"] if isinstance(x, list) and len(x) > 3]
        т.доля("u.own_pairs", sum(1 for x in пары_ if (x[3] or 0) > 0), len(пары_))
        пб = pairs.get("brands") or []
        свои = {пб[x[0]] for x in пары_ if (x[3] or 0) > 0 and целое(x[0]) and x[0] < len(пб)}
        все = {пб[x[0]] for x in пары_ if целое(x[0]) and x[0] < len(пб)}
        т.доля("u.own_brands", len(свои), len(все))
        ссылки = [(_развернуть(пб, [x[0]])[0], _развернуть(pairs.get("suppliers") or [], [x[1]])[0]) for x in пары_]
        ссылки_ок = sum(1 for b_, s_ in ссылки if b_ in ключи_брендов and s_ in ключи_поставщиков)
        ссылки_указателя = [(x, y) for к in коды_указателя
                            for x in _развернуть(список_б, к[3] if len(к) > 3 else [])
                            for y in [None]] + [(None, y) for к in коды_указателя
                                                for y in _развернуть(список_с, к[4] if len(к) > 4 else [])]
        ок_указателя = sum(1 for x, y in ссылки_указателя if (x in ключи_брендов) or (y in ключи_поставщиков))
        т.доля("u.link_b", ссылки_ок + ок_указателя, len(ссылки) + len(ссылки_указателя))
    кат = ((bv.get("coverage") or {}).get("universes") or {}).get("catalog") or {}
    поля_кат = кат.get("fields") or []
    if поля_кат:
        т.доля("u.coverage_closed", sum(1 for f in поля_кат if f.get("status") == "закрыто"), len(поля_кат))
    return т


# ── /counters ────────────────────────────────────────────────────────────────

ПРОВЕРКИ_СЧЁТЧИКОВ = {
    "c.present": "снимка counters:v1 нет или он не читается",
    "c.version": "version ≠ 1 — страница «Не открылось»",
    "c.dropped": "публикатор отбросил нечисловые значения (dropped > 0)",
    "c.size": "размер снимка больше 100 КиБ (ошибка публикатора)",
    "c.metric": "замера «коды_и_цены» нет или в нём нет точки с asked",
    "c.cut": "история обрезана ровно до 400 точек (оговорка, не дефект)",
    "c.dup": "повтор run или at внутри замера",
    "c.order": "точки не упорядочены по at (запрос публикатора изменился)",
    "c.at": "at не ISO-8601 с Z или в будущем",
    "c.fresh": f"последней точке «коды_и_цены» больше {ВОЗРАСТ_СЧЁТЧИКОВ_Ч} ч (пропущена ночь)",
    "c.same_day": "несколько точек в один день — одинаковые подписи оси (справочно)",
    "c.run": "«коды_и_цены»: прогон не GITHUB_RUN_ID и не «вручную-…» — точку записали в обход скрипта",
    "c.note": "оговорка длиннее 500 знаков или с почтой, ссылкой, номером сделки",
    "c.note_needed": "asked или with_kp сдвинулся больше чем на 30 % без оговорки",
    "c.fields": "в последней точке нет поля, которое читает страница",
    "c.int": "значение не целое или отрицательное",
    "c.inv_asked": "asked ≠ with_kp + other_feed + no_price",
    "c.asked_zero": "asked = 0",
    "c.rows": "строки спроса: rows_asked < asked, ≠ rows_customer или rows_with + rows_without > rows_asked",
    "c.with_kp": "with_kp ≠ price_asked или больше min(asked, price_codes)",
    "c.with_kp_drop": "with_kp упал к прошлой точке без оговорки",
    "c.no_price": "no_price ≠ asked − with_kp − other_feed, или rows_without не сходится",
    "c.other_feed": "with_kp + other_feed > price_codes_any",
    "c.price": "цены: price_codes ≠ asked + not_asked, строк меньше кодов или больше «всех потоков»",
    "c.catalog": "каталог: catalog_priced ≠ price_in_catalog или больше каталога",
    "c.catalog_scale": "каталог не того порядка, что lib_parts (1e3…1e5)",
    "c.plausible": "правдоподобность: plausible > asked или разряды не сходятся",
    "c.inc_fresh": f"инкремент: последней отметке больше {ВОЗРАСТ_ИНКРЕМЕНТА_Ч} ч",
    "c.inc_start": "инкремент: «начало» в будущем",
    "c.after_id": "инкремент или почта: «после_id» убывает от точки к точке",
    "c.mail": "почта: писем отрицательно",
    "c.brand_reg": "реестр брендов: разрешено ≤ с текстом ≤ всего нарушено",
    "c.brand_reg_drop": "реестр брендов: доля разрешённых упала больше 5 п. п. без оговорки",
}

ПОЛЬЗА_СЧЁТЧИКОВ = {
    "u.with_kp": "коды спроса с ценой поставщика",
    "u.no_price": "(справочно) коды спроса без цены вовсе — очередь рассылки",
    "u.rows_without": "(справочно) строки спроса за кодами без цены",
    "u.plausible": "годность знаменателя: правдоподобные коды спроса",
    "u.price_catalog": "цены, которые находятся по машине (есть в каталоге)",
    "u.catalog_asked": "каталог, который спрашивал рынок",
    "u.brand_resolved": "строки с разрешённым брендом (замер «реестр_брендов», на странице не показан)",
    "u.fresh": "последняя точка «коды_и_цены» свежая",
}


def _новая_оговорка(точка, прежняя) -> bool:
    """Сдвиг объяснён, если у точки есть оговорка и она не повтор прежней:
    писатель, ставящий одну и ту же подпись на каждую точку, ничего не объясняет."""
    note = точка.get("note")
    return bool(note) and note != прежняя


def _разрезы_реестра(nums: dict) -> dict[str, dict]:
    """«<метка>.<порядок>.всего / с_текстом / разрешено» → разрез с тремя числами.
    Счётчики написаний «реестр.<источник>.<статус>» сюда не попадают: у них нет
    тройки, и складывать написания со строками нельзя."""
    разрезы = collections.defaultdict(dict)
    for имя, v in nums.items():
        части = имя.rsplit(".", 1)
        if len(части) == 2 and части[1] in ("всего", "с_текстом", "разрешено") and число(v):
            разрезы[части[0]][части[1]] = v
    return {k: x for k, x in разрезы.items() if len(x) == 3}


def _точки(snap, замер) -> list[dict]:
    return [t for t in ((snap or {}).get("metrics") or {}).get(замер) or [] if isinstance(t, dict)]


def ревизия_счётчиков(с: Снимки, сейчас) -> Вкладка:
    т = Вкладка("counters", "Счётчики (/counters)", ПРОВЕРКИ_СЧЁТЧИКОВ, ПОЛЬЗА_СЧЁТЧИКОВ)
    snap = с.json(КЛЮЧ_СЧЁТЧИКОВ)
    точки_кодов = _точки(snap, ЗАМЕР_КОДОВ)
    т.снимок(КЛЮЧ_СЧЁТЧИКОВ, с, сейчас, (точки_кодов[-1].get("at") if точки_кодов else None))
    if т.счёт("c.present", not isinstance(snap, dict)):
        return т
    т.счёт("c.version", snap.get("version") != 1)
    т.счёт("c.dropped", bool(snap.get("dropped")))
    т.счёт("c.size", (с.размер.get(КЛЮЧ_СЧЁТЧИКОВ) or 0) > 100 * 1024)
    с_asked = [t for t in точки_кодов if (t.get("nums") or {}).get("asked") is not None]
    т.счёт("c.metric", not с_asked)

    for замер, точки in (snap.get("metrics") or {}).items():
        точки = [t for t in точки or [] if isinstance(t, dict)]
        т.счёт("c.cut", len(точки) == 400)
        runs = collections.Counter(t.get("run") for t in точки)
        ats = collections.Counter(t.get("at") for t in точки)
        for t in точки:
            т.счёт("c.dup", runs[t.get("run")] > 1 or ats[t.get("at")] > 1)
            at = время(t.get("at"))
            т.счёт("c.at", at is None or at > сейчас + timedelta(minutes=5))
            if замер == ЗАМЕР_КОДОВ:
                т.счёт("c.run", not ПРОГОН_RX.fullmatch(str(t.get("run") or "")))
            note = t.get("note")
            if note is not None:
                т.счёт("c.note", len(str(note)) > 500 or "@" in str(note) or "bitrix24" in str(note)
                       # «сделки 2026 года» и «№ 2026-09» — год, а не номер сделки.
                       or bool(re.search(r"(сделк\w*|№)\s*(?!(?:19|20)\d{2}\b)\d{4,6}\b", str(note), re.I)))
            for v in (t.get("nums") or {}).values():
                т.счёт("c.int", not целое(v) or v < 0)
        at_список = [t.get("at") for t in точки]
        т.счёт("c.order", at_список != sorted(at_список, key=lambda x: str(x)))
        дни = collections.Counter(str(t.get("at"))[:10] for t in точки)
        if замер == ЗАМЕР_КОДОВ:
            for d, n in дни.items():
                т.счёт("c.same_day", n > 1)
        # Прочие замеры: никто их не видит, но и за ними следим.
        if замер.startswith("инкремент_") and точки:
            последняя = время(точки[-1].get("at"))
            if последняя:
                т.счёт("c.inc_fresh", возраст_ч(последняя, сейчас) > ВОЗРАСТ_ИНКРЕМЕНТА_Ч)
            for t in точки:
                начало = (t.get("nums") or {}).get("начало")
                if число(начало):
                    т.счёт("c.inc_start", начало > сейчас.timestamp() + 300)
        if замер.startswith("инкремент_") or замер.startswith("почта:"):
            прежний = None
            for t in точки:
                после = (t.get("nums") or {}).get("после_id")
                if число(после):
                    if прежний is not None:
                        т.счёт("c.after_id", после < прежний)
                    прежний = после
            if замер.startswith("почта:"):
                for t in точки:
                    for имя, v in (t.get("nums") or {}).items():
                        if "писем" in имя:
                            т.счёт("c.mail", not число(v) or v < 0)
        if замер == "реестр_брендов":
            прежние = {}
            for t in точки:
                for разрез, x in _разрезы_реестра(t.get("nums") or {}).items():
                    т.счёт("c.brand_reg", not x["разрешено"] <= x["с_текстом"] <= x["всего"])
                    доля = x["разрешено"] / x["с_текстом"] if x["с_текстом"] else None
                    if доля is not None and разрез in прежние:
                        было, прежняя_оговорка = прежние[разрез]
                        т.счёт("c.brand_reg_drop", доля < было - 0.05 and not _новая_оговорка(t, прежняя_оговорка))
                    if доля is not None:
                        прежние[разрез] = (доля, t.get("note"))

    if not с_asked:
        return т
    последняя = с_asked[-1]
    nums = последняя.get("nums") or {}
    at = время(последняя.get("at"))
    if at:
        т.счёт("c.fresh", возраст_ч(at, сейчас) > ВОЗРАСТ_СЧЁТЧИКОВ_Ч)
    for поле in ПОЛЯ_СЧЁТЧИКОВ:
        т.счёт("c.fields", поле not in nums)

    def g(имя, точка=None):
        v = ((точка or последняя).get("nums") or {}).get(имя)
        return v if число(v) else None

    for t in с_asked:
        n_ = t.get("nums") or {}
        a, w, o, np = (n_.get(x) for x in ("asked", "with_kp", "other_feed", "no_price"))
        if all(число(x) for x in (a, w, o, np)):
            т.счёт("c.inv_asked", a != w + o + np)
    a = g("asked")
    if a is not None:
        т.счёт("c.asked_zero", a == 0)
    ra, rc, rw, rwo = g("rows_asked"), g("rows_customer"), g("rows_with"), g("rows_without")
    if ra is not None and a is not None:
        т.счёт("c.rows", ra < a or (rc is not None and ra != rc)
               or (rw is not None and rwo is not None and rw + rwo > ra))
    w, pa, pc = g("with_kp"), g("price_asked"), g("price_codes")
    if w is not None and pa is not None:
        т.счёт("c.with_kp", w != pa or (a is not None and pc is not None and w > min(a, pc)))
    o, np = g("other_feed"), g("no_price")
    if all(x is not None for x in (np, a, w, o)):
        т.счёт("c.no_price", np != a - w - o or np < 0
               or (rwo is not None and (rwo < np or (ra is not None and rw is not None and rwo > ra - rw))))
    pany = g("price_codes_any")
    if w is not None and o is not None and pany is not None:
        т.счёт("c.other_feed", w + o > pany)
    pna, prow, psa, pic = g("price_not_asked"), g("price_rows"), g("price_supplier_added"), g("price_in_catalog")
    if pc is not None:
        т.счёт("c.price", (pa is not None and pna is not None and pc != pa + pna)
               or (prow is not None and prow < pc) or (pany is not None and pc > pany)
               or (psa is not None and pna is not None and psa > pna)
               or (pic is not None and pic > pc))
    cat, cp, ca = g("catalog"), g("catalog_priced"), g("catalog_asked")
    if cat is not None:
        т.счёт("c.catalog", (cp is not None and pic is not None and cp != pic)
               or (cp is not None and cp > cat) or (ca is not None and a is not None and ca > min(cat, a)))
        т.счёт("c.catalog_scale", not 1e3 <= cat <= 1e5)
    pl, nd, sh, lo = g("plausible"), g("no_digit"), g("shorter_than_four"), g("longer_than_25")
    if pl is not None and a is not None:
        разряды = [x for x in (nd, sh, lo) if x is not None]
        т.счёт("c.plausible", pl > a or (len(разряды) == 3 and not (
            max(разряды) <= a - pl <= sum(разряды))))
    if len(с_asked) >= 2:
        прошлая = с_asked[-2]
        объяснено = _новая_оговорка(последняя, прошлая.get("note"))
        for имя in ("asked", "with_kp"):
            было, стало = g(имя, прошлая), g(имя)
            if было and стало is not None:
                т.счёт("c.note_needed", abs(стало - было) / было > 0.30 and not объяснено)
        было_w = g("with_kp", прошлая)
        if было_w is not None and w is not None:
            т.счёт("c.with_kp_drop", w < было_w and not объяснено)

    # Польза.
    if a:
        т.доля("u.with_kp", w or 0, a)
        т.доля("u.no_price", np or 0, a)
        т.доля("u.plausible", pl or 0, a)
    if ra:
        т.доля("u.rows_without", rwo or 0, ra)
    if pc:
        т.доля("u.price_catalog", pic or 0, pc)
    if cat:
        т.доля("u.catalog_asked", ca or 0, cat)
    реестр = _точки(snap, "реестр_брендов")
    if реестр:
        разрезы = _разрезы_реестра(реестр[-1].get("nums") or {})
        т.доля("u.brand_resolved", sum(x["разрешено"] for x in разрезы.values()),
               sum(x["с_текстом"] for x in разрезы.values()))
    if at:
        т.доля("u.fresh", 1 if возраст_ч(at, сейчас) <= ВОЗРАСТ_СЧЁТЧИКОВ_Ч else 0, 1)
    return т


# ── /library ─────────────────────────────────────────────────────────────────

ПРОВЕРКИ_БИБЛИОТЕКИ = {
    "l.present": "указателя library:v2:current нет или он не читается",
    "l.pointer": "указатель: не version 2, ревизия не по правилу ID или нет ссылки на манифест",
    "l.manifest": "манифест не читается или не сходится по sha256 и ревизии",
    "l.published": "дата публикации не разбирается или в будущем",
    "l.blob": "блоб дерева не читается или не сходится (обход прерван)",
    "l.seg_name": "сегмент: имя пусто, равно id или похоже на ключ",
    "l.seg_dup": "сегмент: одно имя у двух сегментов",
    "l.seg_count": "сегментов больше 100",
    "l.seg_sum": "сегмент: Σ по видам ≠ article_count, или Σ сегментов ≠ итогу",
    "l.index_count": "сегмент: число записей дерева ≠ счётчику вида",
    "l.seg_deadend": "сегмент: компоненты есть, а поставщиков и цен нет (тупик)",
    "l.seg_empty": "сегмент без статей",
    "l.conf_keys": "уровень проверки вне списка страницы («Уровень проверки не указан»)",
    "l.conf_hidden": "«Требуют проверки» не видит hypothesis, partial, medium, med",
    "l.id_dup": "id статьи повторяется между деревьями",
    "l.dir_match": "каталог и справочник расходятся (id есть в одном, нет в другом)",
    "l.seg_kind": "статья: сегмент вне манифеста или вид ≠ виду дерева",
    "l.managed": "справочник: managed ≠ true при importer_id = id",
    "l.updated": "обновлено: не ISO с зоной, в будущем, раньше 2000 или позже публикации",
    "l.title": "заголовок пуст или похож на ключ",
    "l.title_pn": "у компонента заголовок — сам артикул (описания нет)",
    "l.desc": "компонент без описания («Описание требует проработки»)",
    "l.topic": "тема — сырое перечисление snake_case или длиннее 200",
    "l.body": "отрывок пуст или равен заголовку",
    "l.body_junk": "в отрывке значение поля undefined/None/NaN/null, сырой JSON или HTML-теги",
    "l.body_table": "таблица Markdown: у строк другое число ячеек, чем в шапке",
    "l.body_links": "ссылка в тексте со схемой не http(s) или с логином (страница её не откроет)",
    "l.conf": "уровень проверки ни строкой, ни объектом {level}",
    "l.conf_norefs": "«проверено», а источников нет",
    "l.pn_class": "артикул — марка, размер или голый стандарт (стандарт с размером — номер)",
    "l.pn_bad": "артикул неправдоподобен: короче 4, без цифры, дата, пункт, список",
    "l.pn_oem": "артикул равен бренду",
    "l.pn_words": "в артикуле больше двух кириллических слов (наименование)",
    "l.pn_dup": "компонент повторяется (сегмент, бренд, артикул)",
    "l.oem_digits": "бренд — номера",
    "l.oem_key": "бренд — сжатый ключ",
    "l.oem_unknown": "бренд — пометка незнания",
    "l.oem_multi": "бренд — несколько брендов (спорно по словарю)",
    "l.oem_instruction": "бренд разрешился в указание закупки или номер детали, а не в марку",
    "l.family": "группа — сырое перечисление, в карточке показано без перевода",
    "l.qty": "количество: не число, > 1e6, ≤ 0, дробное в штуках, объект или без единицы",
    "l.priced": "расценка: не bool или противоречит ценам сегмента",
    "l.aliases": "другие обозначения: не список строк или содержат сам артикул",
    "l.s_name": "поставщик: имя-ключ, цифры, контакт вместо имени или бренд без роли изготовителя",
    "l.s_dup": "поставщик повторяется по ключу написания внутри сегмента",
    "l.s_role": "поставщик: роль вне списка",
    "l.s_brands": "поставщик: бренд оборудования — цифры, ключ или пометка незнания",
    "l.p_amount": "цена: не число по форме, ≤ 0 или выше потолка валюты (≈ 300 млн долларов)",
    "l.p_cur": "цена: валюта не ISO или цена без валюты",
    "l.p_date": "цена: дата не разбирается, позже завтрашнего дня, раньше 2000 или «до» раньше даты",
    "l.p_type": "цена: тип или назначение вне списка",
    "l.p_supplier": "цена: источник — заглушка или бренд, которого нет среди изготовителей сегмента",
    "l.p_pn": "цена: артикул неправдоподобен или без компонента в сегменте",
    "l.p_unit": "цена: единица расходится с компонентом (шт = pcs = ea)",
    "l.r_meta": "кандидаты: не та версия или производитель — блок скрыт",
    "l.r_dangling": "кандидат: article_id не найден, не поставщик или сама статья",
    "l.r_fields": "кандидат: тип связи, позиция, указатель или артикул неверны",
    "l.r_url": "кандидат: исходная запись не http(s) или с логином",
    "l.r_dup": "кандидат повторяется у компонента или их больше 512",
    "l.ref_label": "источник без названия и адреса («Источник N»)",
    "l.ref_url": "источник: адрес не http(s) или с логином; sha256 не 64 hex",
    "l.ref_dup": "источник повторяется в статье",
    "l.ref_locator": "источник: указатель места с вложенными объектами или неизвестными ключами",
    "l.crm_url": "ссылка CRM небезопасна (молча пропадает)",
    "l.open_q": "открытые вопросы у статьи «проверено»",
    "l.desc_hint": "подсказка «Описание доступно в полном материале», а описания нет и там",
}

ПОЛЬЗА_БИБЛИОТЕКИ = {
    "u.brand": "компоненты с определённым брендом (разрешён по словарю или /brands в марку)",
    "u.brand_disputed": "(справочно) бренд спорный — несколько марок",
    "u.oem_queue": "(справочно) бренда нет ни в словаре dict/oem.json, ни в /brands — очередь словаря",
    "u.pn_long": "(справочно) артикулы длиннее 25 знаков — конфигуратор или склейка",
    "u.code_brand": "правдоподобный артикул и определённый бренд",
    "u.brand_column": "(справочно) столбец бренда в таблице компонентов (его нет)",
    "u.two_offers": "компоненты с двумя и больше предложениями поставщиков",
    "u.two_candidates": "компоненты с двумя и больше кандидатами",
    "u.no_candidates": "(справочно) компоненты без кандидатов — тупик",
    "u.price_buyable": "цены, пригодные для закупки",
    "u.price_ours": "(справочно) «Наше КП заказчику» среди цен",
    "u.sup_real": "поставщики с настоящим именем",
    "u.sup_role": "поставщики с ролью",
    "u.sup_inn": "поставщики с ИНН (в контракте поля нет)",
    "u.desc": "компоненты с описанием",
    "u.purpose": "компоненты с назначением в узле",
    "u.confirmed": "статьи с подтверждением (источник и уровень «проверено»)",
    "u.seg_full": "сегменты, где есть компоненты, поставщики и цены",
    "u.chain": "артикулы компонента с ценой и кандидатом",
    "u.orphan_prices": "(справочно) цены без компонента в сегменте",
    "u.truncated": "(справочно) краткая карточка урезана — поля проверены по полной записи",
}


def _безопасный_url(u) -> bool:
    """Зеркало safeUrl из public/library.html: new URL(value, location.origin) и
    только http(s) без логина. Ссылка без схемы — «#раздел», «x.html», «/library»
    — относительная и открывается; пробел и управляющие знаки — нет."""
    if not isinstance(u, str) or re.search(r"[\x00-\x20\x7f]", u):
        return False
    схема = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", u)
    if схема and схема[1].lower() not in ("http", "https"):
        return False
    хост = re.match(r"(?:[A-Za-z][A-Za-z0-9+.-]*:)?[/\\]{2}([^/\\?#]*)", u)
    return not (хост and "@" in хост[1])


# HTML — только закрытым списком тегов и формой тега: «0,1<s<0,3 мм при t>80 °C»
# и «p<p_max, t>60» — формулы, а не разметка. Пустое значение — только как
# значение поля («Срок: undefined»), а не слово текста («None of the seals…»).
HTML_ТЕГ = re.compile(r"</?(p|br|div|span|table|tr|td|th|b|i|a|ul|ol|li|h[1-6]|strong|em|img|sup|sub)"
                      r"(\s+[a-z-]+=(\"[^\"]*\"|'[^']*'|[^\s>]+))*\s*/?>", re.I)
ПУСТОЕ_ПОЛЕ = re.compile(r"(?:^|[:=|]\s*)(undefined|None|NaN|null)\s*(?:$|[|,;.])", re.M)


def _список_значений(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, (str, int, float)) and str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v]
    return []


def описание_компонента(s, lib2) -> str:
    """Зеркало componentDescription из public/library.html."""
    src = s.get("sources") if isinstance(s.get("sources"), dict) else {}
    f = src.get("component_fields") if isinstance(src.get("component_fields"), dict) else {}

    def сжать(v):
        return re.sub(r"[\W_]", "", норм(v))

    pn, oem = сжать(f.get("part_number")), сжать(f.get("oem"))

    def значимое(v):
        простое = str(v).strip() if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""
        k = сжать(простое)
        return простое if k and k not in {"unknown", "неуказано", "неизвестно", pn, oem, oem + pn, pn + oem} else ""

    return (значимое(f.get("position_type")) or значимое(f.get("name")) or значимое(f.get("description"))
            or (lib2.component_type_label(src, s.get("segment_id")) if lib2 else "")
            or значимое(s.get("title")))


def вид_записи_словаря(запись) -> str:
    """Вид записи dict/oem.json. У записи без поля kind (словарь прежней сборки,
    выдуманный корпус) вид — по признакам, которые не требуют доказательства:
    указание к закупке и номер; иначе бренд — как читатель вёл себя до пометок."""
    r = запись or {}
    if r.get("kind"):
        return r["kind"]
    if oem_kind.указание(r.get("name")):
        return oem_kind.УКАЗАНИЕ
    if oem_kind.номер(r.get("name"), r.get("oem_key")):
        return oem_kind.НОМЕР
    return oem_kind.БРЕНД


def карта_словаря_брендов(словарь) -> tuple[dict, dict]:
    """dict/oem.json → (ключ написания → {ключи брендов}, ключ → запись).

    Как у читателей словаря: написание «описания» ведёт к его бренду (brands) —
    «Bently Nevada (по профилю)» → Bently Nevada; указание, номер и «несколько»
    ведут к своей записи, и оценка видит, что бренд разрешился в не-бренд или
    в несколько брендов сразу."""
    карта = collections.defaultdict(set)
    записи = {}
    for r in (словарь or {}).get("records") or []:
        k = r.get("oem_key")
        if not k:
            continue
        записи[k] = r
        цели = ({b for b in r.get("brands") or [] if b} if вид_записи_словаря(r) == oem_kind.ОПИСАНИЕ
                else {k})
        if not цели:
            continue
        for н in {r.get("name"), k} | {x.get("spelling") for x in r.get("spellings") or []}:
            kн = codes_sql.ключ_написания(н or "")
            if len(kн) >= 2:
                карта[kн] |= цели
    return карта, записи


def без_бренда(ключ, записи) -> bool:
    """Ключ ведёт к записи словаря вида «указание» или «номер»: бренда у неё нет."""
    return вид_записи_словаря(записи.get(ключ)) in oem_kind.БЕЗ_БРЕНДА


def ревизия_библиотеки(с: Снимки, сейчас, словарь, lib2=None) -> Вкладка:
    т = Вкладка("library", "Библиотека (/library)", ПРОВЕРКИ_БИБЛИОТЕКИ, ПОЛЬЗА_БИБЛИОТЕКИ)
    указатель = с.json(КЛЮЧ_БИБЛИОТЕКИ)
    if т.счёт("l.present", not isinstance(указатель, dict)):
        return т
    lib2 = lib2 or _модуль("kvant_publish_library_v2", "publish_library_v2.py")
    ref = указатель.get("manifest")
    т.счёт("l.pointer", указатель.get("version") != 2
           or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,159}", str(указатель.get("revision") or ""))
           or not (isinstance(ref, dict) and re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256") or ""))
                   and целое(ref.get("bytes"))))
    адаптер = _ДляХранилища(с.источник)
    store = lib2.Store(адаптер, "0" * 32)
    try:
        манифест = store.get(ref)
    except (lib2.v1.PublishError, ПределОбхода):
        манифест = None
    if т.счёт("l.manifest", not isinstance(манифест, dict) or манифест.get("revision") != указатель.get("revision")):
        return т
    опубликовано = время_с_зоной(манифест.get("published_at")) or время(манифест.get("published_at"))
    т.счёт("l.published", опубликовано is None or опубликовано > сейчас + timedelta(minutes=5))
    т.снимки[КЛЮЧ_БИБЛИОТЕКИ] = {"present": True, "bytes": с.размер.get(КЛЮЧ_БИБЛИОТЕКИ),
                                 "age_h": round(возраст_ч(опубликовано, сейчас), 1) if опубликовано else None,
                                 "articles": манифест.get("article_count")}

    сегменты = [x for x in манифест.get("segments") or [] if isinstance(x, dict)]
    т.счёт("l.seg_count", len(сегменты) > lib2.MAX_SEGMENTS)
    имена_сег = collections.Counter(норм(x.get("name")) for x in сегменты)
    всего = 0
    for x in сегменты:
        имя = x.get("name")
        т.счёт("l.seg_name", not имя or имя == x.get("id") or company_names.как_ключ(имя)
               or bool(re.fullmatch(r"[a-z0-9_-]+", str(имя))))
        т.счёт("l.seg_dup", имена_сег[норм(имя)] > 1)
        виды = x.get("counts_by_kind") or {}
        т.счёт("l.seg_sum", sum(v for v in виды.values() if целое(v)) != x.get("article_count"))
        всего += x.get("article_count") or 0
        т.счёт("l.seg_empty", not x.get("article_count"))
        if (виды.get("component") or 0) > 0:
            т.счёт("l.seg_deadend", not виды.get("supplier") and not виды.get("price"))
        for уровень, n_ in (x.get("counts_by_confidence") or {}).items():
            т.счёт("l.conf_keys", уровень not in УРОВНИ_ПРОВЕРКИ, вес=max(n_, 1))
            т.счёт("l.conf_hidden", уровень in НЕ_ВИДНО_КАК_ТРЕБУЕТ, вес=max(n_, 1))
    т.счёт("l.seg_sum", всего != манифест.get("article_count"))

    # Обход каталога: краткие карточки всех видов.
    статьи: list[dict] = []
    виденные = collections.Counter()
    ид_сегментов = {x.get("id") for x in сегменты}
    прервано = False
    for x in сегменты:
        for вид in lib2.KINDS:
            дерево = ((x.get("indexes") or {}).get(вид) or {}).get("catalog")
            n_ = 0
            try:
                for item in lib2.entries(store, дерево, "catalog"):
                    n_ += 1
                    статьи.append(item)
                    виденные[item.get("id")] += 1
                    т.счёт("l.seg_kind", item.get("segment_id") != x.get("id")
                           or item.get("segment_id") not in ид_сегментов
                           or (item.get("sources") or {}).get("kind") != вид)
            except ПределОбхода:
                прервано = True
                break
            except lib2.v1.PublishError:
                т.счёт("l.blob", True)
                continue
            т.счёт("l.index_count", n_ != ((x.get("counts_by_kind") or {}).get(вид) or 0)
                   or (n_ == 0 and дерево is not None))
        if прервано:
            break
    справочник = {}
    if not прервано:
        try:
            for e in lib2.entries(store, манифест.get("directory"), "directory"):
                справочник[e.get("id")] = e
        except ПределОбхода:
            прервано = True
        except lib2.v1.PublishError:
            т.счёт("l.blob", True)
    if прервано:
        т.счёт("l.blob", True)
        т.неполная = True
        т.заметка(f"обход библиотеки остановлен на пределе {БЛОБОВ_БИБЛИОТЕКИ} блобов")
    for ид, n_ in виденные.items():
        т.счёт("l.id_dup", n_ > 1)
    if справочник and not прервано:
        for ид in set(виденные) | set(справочник):
            т.счёт("l.dir_match", ид not in виденные or ид not in справочник)
    # managed проверяется по importer_id краткой карточки.
    for s in статьи:
        src = s.get("sources") or {}
        if src.get("importer_id") == s.get("id") and s.get("id") in справочник:
            т.счёт("l.managed", справочник[s["id"]].get("managed") is not True)

    # ПОЛНЫЕ ЗАПИСИ — ТОЛЬКО УРЕЗАННЫМ КРАТКОЙ ВЫДАЧЕЙ, И В ПОРЯДКЕ СПРАВОЧНИКА.
    # Урезанной summary() считает любую карточку, у которой в sources есть ключ
    # вне своего списка, — а publication_approved есть у каждой управляемой, так
    # что урезаны почти все. Блоки тел лежат подряд по id: читаем их по порядку
    # справочника, и кеш stored_article держит один блок на полсотни статей.
    урезанные = {s.get("id") for s in статьи if s.get("sources_truncated")}
    кеш = {}
    полные = {}
    for ид, e in справочник.items():
        if ид in урезанные and not прервано:
            try:
                полные[ид] = lib2.stored_article(store, e, кеш)
            except ПределОбхода:
                прервано = True
                т.неполная = True
                т.счёт("l.blob", True)
            except lib2.v1.PublishError:
                т.счёт("l.blob", True)
    if статьи:
        т.доля("u.truncated", len(урезанные), len(статьи))

    карта, записи = карта_словаря_брендов(словарь)
    # Бренд, которого нет в словаре, но который знает /brands, — определён: словарь
    # покрывает не всё, и «в очереди словаря» — покрытие словаря, а не дефект
    # компонента (FAG, NSK, Timken на настоящем dict/oem.json).
    карта_всех = карта_брендов(словарь, с.json(brands.КЛЮЧ))
    по_сегменту = collections.defaultdict(lambda: {"component": [], "supplier": [], "price": [], "knowledge": []})
    for s in статьи:
        полная = полные.get(s.get("id"))
        if полная is not None:
            s = {**s, "sources": полная.get("sources") if isinstance(полная.get("sources"), dict) else s.get("sources")}
        src = s.get("sources") if isinstance(s.get("sources"), dict) else {}
        по_сегменту[s.get("segment_id")][src.get("kind") or "knowledge"].append(s)
    ид_всех = {s.get("id"): s for s in статьи}
    # Дубль поставщика — внутри сегмента: одна компания в двух сегментах — это
    # две статьи по замыслу, а не дубль.
    имена_поставщиков_seen = collections.Counter()
    for seg, группы in по_сегменту.items():
        for s in группы["supplier"]:
            имя = ((s.get("sources") or {}).get("supplier_fields") or {}).get("name")
            if имя:
                имена_поставщиков_seen[(seg, codes_sql.ключ_написания(имя))] += 1

    компоненты_ключи = collections.Counter()
    pn_компонентов = collections.defaultdict(dict)
    for seg, группы in по_сегменту.items():
        for s in группы["component"]:
            f = (s.get("sources") or {}).get("component_fields") or {}
            if f.get("part_number"):
                pn_компонентов[seg][ключ_кода(f["part_number"])] = f
                компоненты_ключи[(seg, codes_sql.ключ_написания(f.get("oem") or ""), ключ_кода(f["part_number"]))] += 1
    цены_по_pn = collections.defaultdict(collections.Counter)
    все_цены_по_pn = collections.defaultdict(collections.Counter)
    for seg, группы in по_сегменту.items():
        for s in группы["price"]:
            pf = (s.get("sources") or {}).get("price_fields") or {}
            if pf.get("part_number"):
                все_цены_по_pn[seg][ключ_кода(pf["part_number"])] += 1
                if pf.get("price_type") in ЦЕНЫ_ПОСТАВЩИКА:
                    цены_по_pn[seg][ключ_кода(pf["part_number"])] += 1

    def оценка_бренда(oem):
        """(дефект-код или None, состояние разрешения)."""
        o = str(oem)
        if только_цифры(o):
            return "l.oem_digits", None
        if company_names.как_ключ(o) and not re.fullmatch(r"[a-z0-9]{1,12}", o) or re.fullmatch(r"[a-z]{13,}|bitrix:\d+", o):
            return "l.oem_key", None
        if brand_registry.пометка_незнания(o) or норм(o) == "unknown":
            return "l.oem_unknown", None
        состояние, ключи = brand_registry.разрешить(o, карта)
        if состояние == brand_registry.СПОРНО:
            return "l.oem_multi", состояние
        if состояние == brand_registry.ОЧЕРЕДЬ:
            return None, brand_registry.разрешить(o, карта_всех)[0]
        if any(без_бренда(k, записи) for k in ключи):
            return "l.oem_instruction", состояние
        if any(вид_записи_словаря(записи.get(k)) == oem_kind.НЕСКОЛЬКО for k in ключи):
            return "l.oem_multi", brand_registry.СПОРНО
        return None, состояние

    # Изготовители сегмента: бренд в роли поставщика у них — прямая поставка.
    изготовители_сегмента = collections.defaultdict(set)
    for seg, группы in по_сегменту.items():
        for s in группы["supplier"]:
            f = (s.get("sources") or {}).get("supplier_fields") or {}
            if f.get("name") and f.get("role") in ("maker", "service"):
                изготовители_сегмента[seg].add(codes_sql.ключ_написания(f["name"]))

    # Польза: копим по ходу.
    с_брендом = спорных = код_и_бренд = с_описанием = с_назначением = в_очереди = длинных_pn = 0
    два_предложения = два_кандидата = без_кандидатов = цепочка = 0
    компонентов = 0
    for s in статьи:
        урезана = bool(s.get("sources_truncated"))
        полная = полные.get(s.get("id"))
        if полная is not None and isinstance(полная.get("sources"), dict):
            s = {**s, "sources": полная["sources"], "excerpt": str(полная.get("body") or "")}
        src = s.get("sources") if isinstance(s.get("sources"), dict) else {}
        вид = src.get("kind") or "knowledge"
        title = s.get("title")
        т.счёт("l.title", not title or company_names.как_ключ(title)
               or bool(re.fullmatch(r"[a-z]{13,}|bitrix:\d+", str(title))))
        topic = s.get("topic")
        if topic:
            т.счёт("l.topic", bool(re.fullmatch(r"[a-z]+(_[a-z]+)+", str(topic))) or len(str(topic)) > 200)
        текст = str(s.get("excerpt") or "")
        т.счёт("l.body", not текст.strip() or текст.strip() == str(title or "").strip())
        т.счёт("l.body_junk", bool(ПУСТОЕ_ПОЛЕ.search(текст) or re.search(r'^\s*\{"', текст, re.M)
                                   or HTML_ТЕГ.search(текст)))
        # Таблица — подряд идущие строки с «|»; у каждой своя шапка. Хвост отрывка
        # (краткая карточка режет тело) не судится: строку там могли обрезать.
        for таблица in re.findall(r"(?:^[ \t]*\|.*(?:\n|$))+", текст, re.M):
            строки_таблицы = [x for x in таблица.splitlines() if x.strip()]
            if len(строки_таблицы) >= 2 and (полная is not None or not текст.rstrip().endswith(таблица.rstrip())):
                ячеек = [x.strip().strip("|").count("|") for x in строки_таблицы]
                т.счёт("l.body_table", len(set(ячеек)) > 1)
        for ссылка in re.findall(r"\]\(([^)\s]+)", текст):
            т.счёт("l.body_links", not _безопасный_url(ссылка))
        conf = s.get("confidence")
        уровень = conf.get("level") if isinstance(conf, dict) else conf
        т.счёт("l.conf", not isinstance(уровень, str) or уровень not in УРОВНИ_ПРОВЕРКИ)
        refs = src.get("references") if isinstance(src.get("references"), list) else []
        if уровень in ПРОВЕРЕНО:
            т.счёт("l.conf_norefs", not refs)
            if "open_questions" in src:
                т.счёт("l.open_q", bool(src.get("open_questions")))
        upd = время_с_зоной(s.get("updated_at"))
        if s.get("updated_at") is not None:
            т.счёт("l.updated", upd is None or upd > сейчас + timedelta(minutes=5) or upd.year < 2000
                   or (опубликовано is not None and upd > опубликовано + timedelta(minutes=5)))
        виденные_ref = collections.Counter()
        for r in refs:
            if not isinstance(r, dict):
                т.счёт("l.ref_label", True)
                continue
            т.счёт("l.ref_label", not (r.get("title") or r.get("name") or r.get("url")))
            if r.get("url") is not None or r.get("sha256") is not None:
                т.счёт("l.ref_url", (r.get("url") is not None and not re.match(r"https?://", str(r["url"])))
                       or (r.get("url") is not None and not _безопасный_url(r["url"]))
                       or (r.get("sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(r["sha256"]))))
            loc = r.get("locator")
            if isinstance(loc, dict):
                т.счёт("l.ref_locator", any(isinstance(v, (dict, list)) and k != "cells" for k, v in loc.items())
                       or any(k not in ЛОКАТОР for k in loc))
            виденные_ref[(r.get("sha256") or r.get("url") or r.get("title"))] += 1
        for ключ_, n_ in виденные_ref.items():
            if ключ_:
                т.счёт("l.ref_dup", n_ > 1)
        for cl in src.get("crm_links") or []:
            if isinstance(cl, dict) and cl.get("url") is not None:
                т.счёт("l.crm_url", not _безопасный_url(cl["url"]) or not str(cl["url"]).startswith("http"))

        if вид == "component":
            компонентов += 1
            f = src.get("component_fields") if isinstance(src.get("component_fields"), dict) else {}
            pn, oem = f.get("part_number"), f.get("oem")
            if title and pn:
                т.счёт("l.title_pn", ключ_кода(title) in (ключ_кода(pn), ключ_кода(f"{oem or ''} {pn}")))
            описание = описание_компонента(s, lib2)
            т.счёт("l.desc", not описание)
            if урезана:
                т.счёт("l.desc_hint", not описание)
            if описание:
                с_описанием += 1
            if f.get("purpose"):
                с_назначением += 1
            if pn:
                pn_s = str(pn)
                if есть_правило_кода():
                    т.счёт("l.pn_class", класс_написания(pn_s) is not None, форма=ключ_кода(pn_s))
                k = ключ_кода(pn_s)
                # Длиннее 25 знаков — и склейка, и код конфигуратора (Rexroth,
                # Endress+Hauser): справочно, а не дефектом.
                длинных_pn += len(k) > 25
                т.счёт("l.pn_bad", len(k) < 4 or not re.search(r"\d", k)
                       or docfilter._is_date(pn_s) or bool(re.fullmatch(r"\d{1,2}(\.\d{1,3})+", pn_s))
                       or bool(re.fullmatch(r"[\d ,;]+", pn_s) and re.search(r"[,;]", pn_s)))
                if oem:
                    т.счёт("l.pn_oem", ключ_кода(pn_s) == ключ_кода(oem))
                т.счёт("l.pn_words", len(re.findall(r"[А-Яа-яЁё]{2,}", pn_s)) > 2)
                т.счёт("l.pn_dup", компоненты_ключи[(s.get("segment_id"), codes_sql.ключ_написания(oem or ""), k)] > 1)
            определён = False
            if oem:
                дефект, состояние = оценка_бренда(oem)
                for код_ in ("l.oem_digits", "l.oem_key", "l.oem_unknown", "l.oem_multi", "l.oem_instruction"):
                    т.счёт(код_, дефект == код_)
                определён = дефект is None and состояние == brand_registry.РАЗРЕШЕНО
                спорных += состояние == brand_registry.СПОРНО
                в_очереди += дефект is None and состояние == brand_registry.ОЧЕРЕДЬ
            с_брендом += определён
            if определён and pn and код_правдоподобен(pn):
                код_и_бренд += 1
            fam = f.get("family")
            if isinstance(fam, str) and re.fullmatch(r"[a-z]+(_[a-z]+)+", fam):
                т.счёт("l.family", fam not in lib2.COMPONENT_FAMILIES)
            q = f.get("quantity")
            if q is not None:
                try:
                    qn = float(q) if not isinstance(q, (dict, list, bool)) else None
                except (TypeError, ValueError):
                    qn = None
                т.счёт("l.qty", qn is None or not math.isfinite(qn) or qn > 1e6 or qn <= 0
                       or (норм(f.get("unit")) in ШТУЧНЫЕ | {"ea"} and qn != int(qn))
                       or not f.get("unit"))
            if "priced" in f:
                цен = все_цены_по_pn[s.get("segment_id")][ключ_кода(pn)] if pn else 0
                т.счёт("l.priced", not isinstance(f["priced"], bool)
                       or (bool(pn) and f["priced"] is True and цен == 0)
                       or (bool(pn) and f["priced"] is False and цен > 0))
            if "aliases" in f:
                al = f["aliases"]
                т.счёт("l.aliases", not isinstance(al, list) or any(not isinstance(x, str) for x in al)
                       or (bool(pn) and ключ_кода(pn) in {ключ_кода(x) for x in al if isinstance(x, str)}))
            rel = src.get("library_relations")
            кандидаты = 0
            if isinstance(rel, dict):
                т.счёт("l.r_meta", rel.get("version") != 1 or rel.get("producer") != "publisher-v2")
                cs = [c for c in rel.get("candidate_suppliers") or [] if isinstance(c, dict)]
                кандидаты = len(cs)
                сколько = collections.Counter(c.get("article_id") for c in cs)
                т.счёт("l.r_dup", any(n_ > 1 for n_ in сколько.values()) or len(cs) > lib2.MAX_COMPONENT_RELATIONS)
                for c in cs:
                    цель = ид_всех.get(c.get("article_id"))
                    т.счёт("l.r_dangling", цель is None or c.get("article_id") == s.get("id")
                           or ((цель.get("sources") or {}).get("kind") != "supplier"))
                    т.счёт("l.r_fields", c.get("relation_type") != "historical_supplier_candidate"
                           or not (целое(c.get("position_id")) and c["position_id"] > 0)
                           or not str(c.get("json_pointer") or "").startswith("/")
                           or (c.get("part_number") is not None and pn is not None and c.get("part_number") != pn))
                    if c.get("source_url") is not None:
                        т.счёт("l.r_url", not re.match(r"https?://", str(c["source_url"]))
                               or not _безопасный_url(c["source_url"]))
            if кандидаты >= 2:
                два_кандидата += 1
            if кандидаты == 0:
                без_кандидатов += 1
            if pn and цены_по_pn[s.get("segment_id")][ключ_кода(pn)] >= 2:
                два_предложения += 1
            if pn and кандидаты and цены_по_pn[s.get("segment_id")][ключ_кода(pn)]:
                цепочка += 1
        elif вид == "supplier":
            f = src.get("supplier_fields") if isinstance(src.get("supplier_fields"), dict) else {}
            имя = f.get("name")
            if имя:
                состояние, ключи = brand_registry.разрешить(имя, карта)
                # Бренд в роли поставщика законен у изготовителя и сервиса:
                # прямая поставка от изготовителя.
                бренд_вместо = (состояние == brand_registry.РАЗРЕШЕНО and codes_sql.ключ_написания(имя) in ключи
                                and f.get("role") not in ("maker", "service"))
                т.счёт("l.s_name", company_names.как_ключ(имя)
                       or bool(re.fullmatch(r"[a-z]{13,}|bitrix:\d+|[\d ,]+", str(имя)))
                       or bool(re.search(r"@|https?://|\+?\d[\d\s()-]{8,}\d", str(имя)))
                       or бренд_вместо)
                т.счёт("l.s_dup", имена_поставщиков_seen[(s.get("segment_id"), codes_sql.ключ_написания(имя))] > 1)
            if "role" in f:
                т.счёт("l.s_role", f.get("role") not in РОЛИ_БИБЛИОТЕКИ)
            for b in _список_значений(f.get("oem_brands")):
                т.счёт("l.s_brands", только_цифры(b) or (company_names.как_ключ(b) and len(b) > 12)
                       or brand_registry.пометка_незнания(b))
        elif вид == "price":
            f = src.get("price_fields") if isinstance(src.get("price_fields"), dict) else {}
            amount = f.get("amount")
            if amount is not None:
                ок = bool(re.fullmatch(r"\d+(\.\d{1,8})?", str(amount)))
                т.счёт("l.p_amount", not ок or not 0 < float(amount) < потолок_цены(f.get("currency")))
            cur = f.get("currency")
            if amount is not None or cur is not None:
                т.счёт("l.p_cur", not cur or not re.fullmatch(r"[A-Z]{3}", str(cur)))
            for поле in ("price_date", "valid_until", "quote_valid_until"):
                if f.get(поле):
                    d = день_свободно(f[поле])
                    т.счёт("l.p_date", d is None or d.year < 2000
                           or (поле == "price_date" and d > сейчас.date() + timedelta(days=1))
                           or (поле != "price_date" and день_свободно(f.get("price_date")) is not None
                               and d < день_свободно(f.get("price_date"))))
            if "price_type" in f or "direction" in f:
                т.счёт("l.p_type", ("price_type" in f and f["price_type"] not in ТИПЫ_ЦЕНЫ)
                       or ("direction" in f and f["direction"] not in НАПРАВЛЕНИЯ))
            if f.get("supplier"):
                sup_ = str(f["supplier"])
                состояние, ключи = brand_registry.разрешить(sup_, карта)
                т.счёт("l.p_supplier", sup_ in ЗАГЛУШКИ_ПОСТАВЩИКА
                       or (состояние == brand_registry.РАЗРЕШЕНО and codes_sql.ключ_написания(sup_) in ключи
                           and codes_sql.ключ_написания(sup_) not in изготовители_сегмента[s.get("segment_id")]))
            if f.get("part_number"):
                k = ключ_кода(f["part_number"])
                т.счёт("l.p_pn", not код_правдоподобен(f["part_number"]) or k not in pn_компонентов[s.get("segment_id")])
                comp = pn_компонентов[s.get("segment_id")].get(k)
                if comp and comp.get("unit") and f.get("unit"):
                    т.счёт("l.p_unit", единица(comp["unit"]) != единица(f["unit"]))

    # Польза.
    if компонентов:
        т.доля("u.brand", с_брендом, компонентов)
        т.доля("u.brand_disputed", спорных, компонентов)
        т.доля("u.oem_queue", в_очереди, компонентов)
        т.доля("u.pn_long", длинных_pn, компонентов)
        т.доля("u.code_brand", код_и_бренд, компонентов)
        т.доля("u.brand_column", 0, компонентов)
        т.доля("u.two_offers", два_предложения, компонентов)
        т.доля("u.two_candidates", два_кандидата, компонентов)
        т.доля("u.no_candidates", без_кандидатов, компонентов)
        т.доля("u.desc", с_описанием, компонентов)
        т.доля("u.purpose", с_назначением, компонентов)
        т.доля("u.chain", цепочка, компонентов)
    цены = [s for g in по_сегменту.values() for s in g["price"]]
    if цены:
        def годна(s):
            f = (s.get("sources") or {}).get("price_fields") or {}
            d = день_свободно(f.get("price_date"))
            return (bool(re.fullmatch(r"\d+(\.\d{1,8})?", str(f.get("amount") or ""))) and bool(f.get("currency"))
                    and d is not None and (сейчас.date() - d).days <= 365
                    and f.get("direction") != "outgoing_offer")

        т.доля("u.price_buyable", sum(1 for s in цены if годна(s)), len(цены))
        т.доля("u.price_ours", sum(1 for s in цены if ((s.get("sources") or {}).get("price_fields") or {}).get("price_type")
                                   == "Наше КП заказчику"), len(цены))
        т.доля("u.orphan_prices", sum(1 for s in цены if ключ_кода(((s.get("sources") or {}).get("price_fields") or {}).get("part_number"))
                                      not in pn_компонентов[s.get("segment_id")]), len(цены))
    поставщики = [s for g in по_сегменту.values() for s in g["supplier"]]
    if поставщики:
        def f_(s):
            return (s.get("sources") or {}).get("supplier_fields") or {}

        т.доля("u.sup_real", sum(1 for s in поставщики if f_(s).get("name") and not company_names.как_ключ(f_(s)["name"])),
               len(поставщики))
        т.доля("u.sup_role", sum(1 for s in поставщики if f_(s).get("role") not in (None, "unknown")), len(поставщики))
        т.доля("u.sup_inn", sum(1 for s in поставщики if re.fullmatch(r"\d{10}|\d{12}", str(f_(s).get("inn") or ""))),
               len(поставщики))
    if статьи:
        т.доля("u.confirmed", sum(1 for s in статьи if (((s.get("confidence") or {}).get("level")
                                                           if isinstance(s.get("confidence"), dict) else s.get("confidence"))
                                                          in ПРОВЕРЕНО)
                                  and any(isinstance(r, dict) and (r.get("url") or r.get("sha256"))
                                          for r in (s.get("sources") or {}).get("references") or [])), len(статьи))
    if сегменты:
        т.доля("u.seg_full", sum(1 for x in сегменты if all(((x.get("counts_by_kind") or {}).get(v) or 0) > 0
                                                           for v in ("component", "supplier", "price"))), len(сегменты))
    return т


# ── dict/oem.json ────────────────────────────────────────────────────────────

ПРОВЕРКИ_СЛОВАРЯ = {
    "d.count": "count ≠ числу записей",
    "d.key": "ключ пуст, повторяется или не ^[0-9a-zа-я]+$",
    "d.kind": "вид записи пуст или не из закрытого списка (бренд, указание, несколько, описание, номер)",
    "d.kind_ref": "brands записи — не бренды словаря, у «описания» их нет, у «бренда» они есть",
    # Ниже — записи вида «бренд», то есть то, что читатель словаря примет за марку.
    "d.desc": "вид «бренд», а имя — описание: пояснение в скобках, тире, двоеточие, «по/для/или», 6+ слов (без правовой формы)",
    "d.key_40": "вид «бренд», а ключ ровно 40 знаков — обрезка nkey, риск склейки",
    "d.key_glue": "вид «бренд», а написания под ключом разные до обрезки nkey — склейка обрезкой",
    "d.key_cyr": "вид «бренд», а в ключе кириллица при латинском имени — пояснение по-русски",
    "d.instruction": "вид «бренд», а имя — указание закупки (отсев страницы /brands и «типовые»: «по спецификации», «по типу»…)",
    "d.multi": "вид «бренд», а в имени несколько брендов («/», «или», «,» — не считая «, Inc», «Co., Ltd.» и скобок)",
    "d.key_digits": "вид «бренд», а в ключе пять и больше цифр — номер детали вместо бренда",
    "d.spell_quotes": "написание в кавычках или начинается не с буквы или цифры",
    "d.spell_shared": "одно нормализованное написание в двух записях",
    "d.where": "источник написания указывает на несуществующий файл",
}

ПОЛЬЗА_СЛОВАРЯ = {
    "u.real": "записи — настоящие бренды (не указание, не несколько, не номер, не описание)",
    "u.brand_real": "записи вида «бренд» — настоящие бренды: что читатель примет за марку, то и марка",
}


def ревизия_словаря(словарь, корень: Path = ROOT) -> Вкладка:
    """Словарь брендов. Признаки не-бренда — library/oem_kind.py, те же, по которым
    сборщик ставит вид записи. Дефект — запись вида «бренд» с признаком: её
    читатель примет за марку. Запись другого вида с тем же признаком — честная
    пометка, а не дефект (правило 5: пометка, а не удаление)."""
    т = Вкладка("dict", "Словарь брендов (dict/oem.json)", ПРОВЕРКИ_СЛОВАРЯ, ПОЛЬЗА_СЛОВАРЯ)
    записи = [r for r in (словарь or {}).get("records") or [] if isinstance(r, dict)]
    т.счёт("d.count", (словарь or {}).get("count") != len(записи))
    ключи = collections.Counter(r.get("oem_key") for r in записи)
    виды = {r.get("oem_key"): r.get("kind") or oem_kind.БРЕНД for r in записи}
    написания = collections.defaultdict(set)
    for r in записи:
        for x in r.get("spellings") or []:
            if isinstance(x, dict) and x.get("spelling"):
                написания[codes_sql.ключ_написания(x["spelling"])].add(r.get("oem_key"))
    файлы = {}
    настоящих = брендов = настоящих_брендов = 0
    for r in записи:
        k, имя = str(r.get("oem_key") or ""), str(r.get("name") or "")
        т.счёт("d.key", not k or ключи[k] > 1 or not re.fullmatch(r"[0-9a-zа-я]+", k))
        вид = r.get("kind")
        т.счёт("d.kind", вид not in oem_kind.ВИДЫ)
        вид = вид or oem_kind.БРЕНД
        бренды = r.get("brands")
        if бренды is not None or вид != oem_kind.БРЕНД:
            т.счёт("d.kind_ref", (вид == oem_kind.БРЕНД and bool(бренды))
                   or (вид == oem_kind.ОПИСАНИЕ and not бренды)
                   or not isinstance(бренды or [], list)
                   or any(виды.get(b) != oem_kind.БРЕНД for b in бренды or []))
        п = oem_kind.признаки(имя, k)
        # Та же четвёрка признаков, что u.real: запись без них — настоящий бренд.
        чистая = not (п["описание"] or п["указание"] or п["несколько"] or п["номер"])
        настоящих += чистая
        if вид == oem_kind.БРЕНД:
            брендов += 1
            настоящих_брендов += чистая
            т.счёт("d.desc", п["описание"])
            т.счёт("d.key_40", п["ключ_40"])
            полные = {codes_sql.bd.nkey_full(x.get("spelling")) for x in r.get("spellings") or []
                      if isinstance(x, dict) and x.get("spelling")}
            т.счёт("d.key_glue", len(k) == oem_kind.ДЛИНА_КЛЮЧА and len(полные) > 1)
            т.счёт("d.key_cyr", п["кириллица"])
            т.счёт("d.instruction", п["указание"])
            т.счёт("d.multi", п["несколько"])
            т.счёт("d.key_digits", п["номер"])
        for x in r.get("spellings") or []:
            if not isinstance(x, dict):
                continue
            sp = str(x.get("spelling") or "")
            т.счёт("d.spell_quotes", bool(re.match(r"\s*[\"«“„']", sp)) or not re.match(r"[0-9A-Za-zА-Яа-яЁё]", sp))
            if sp:
                т.счёт("d.spell_shared", len(написания[codes_sql.ключ_написания(sp)]) > 1)
            путь = str(x.get("where") or "").split(":", 1)[0]
            if путь:
                if путь not in файлы:
                    файлы[путь] = (корень / путь).is_file()
                т.счёт("d.where", not файлы[путь])
    if записи:
        т.доля("u.real", настоящих, len(записи))
    if брендов:
        т.доля("u.brand_real", настоящих_брендов, брендов)
    return т


# ── Прогон ───────────────────────────────────────────────────────────────────

def читать_словарь(путь: Path = ROOT / brands.ФАЙЛ_СЛОВАРЯ):
    return json.loads(путь.read_text(encoding="utf-8")) if путь.is_file() else None


def ревизия(с: Снимки, сейчас, словарь=None, прошлые: Снимки | None = None, lib2=None,
            только=None) -> list[Вкладка]:
    """Все вкладки по очереди. Упавшая вкладка не роняет остальные: вместо неё
    в итоге — пустая вкладка с пометкой «упала» и именем исключения (без данных).
    Ошибку чтения KV это не глотает: её тип — PublishError публикатора, и она
    поднимается дальше, потому что без снимков мерить нечего."""
    шаги = [("suppliers", "Поставщики (/suppliers)", lambda: ревизия_поставщиков(с, сейчас)),
            ("nomenclature", "Номенклатура (/nomenclature)", lambda: ревизия_номенклатуры(с, сейчас, прошлые, словарь)),
            ("brands", "Бренды и коды (/brands)", lambda: ревизия_брендов(с, сейчас)),
            ("counters", "Счётчики (/counters)", lambda: ревизия_счётчиков(с, сейчас)),
            ("library", "Библиотека (/library)", lambda: ревизия_библиотеки(с, сейчас, словарь, lib2)),
            ("dict", "Словарь брендов (dict/oem.json)", lambda: ревизия_словаря(словарь))]
    out = []
    for ид, имя, f in шаги:
        if только and ид not in только:
            continue
        try:
            out.append(f())
        except (TypeError, ValueError, KeyError, AttributeError, IndexError, ZeroDivisionError) as ошибка:
            т = Вкладка(ид, имя, {}, {})
            т.упала = type(ошибка).__name__
            т.заметка(f"ревизия вкладки упала: {т.упала}")
            out.append(т)
    return out


def сводка(вкладки: list[Вкладка], сейчас, вид_источника: str) -> dict:
    return {"version": 1, "at": сейчас.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": вид_источника,
            "rule": getattr(docfilter, "RULE_VERSION", None),
            "code_rule": КЛАСС_НЕ_КОДА is not None,
            "tabs": [т.итог() for т in вкладки]}


def прежние_дефекты(прошлая) -> dict[tuple[str, str], int]:
    out = {}
    for tab in (прошлая or {}).get("tabs") or []:
        for ch in tab.get("checks") or []:
            out[(tab.get("id"), ch.get("id"))] = ch.get("bad")
    return out


def печать(вкладки: list[Вкладка], прошлая=None, поток=None):
    поток = поток or sys.stdout
    было = прежние_дефекты(прошлая)

    def p(s=""):
        print(s, file=поток)

    for т in вкладки:
        p(f"\n═══ {т.имя} ═══")
        for ключ, мета in т.снимки.items():
            if not мета.get("present"):
                p(f"  снимок {ключ}: нет")
                continue
            размер = мета.get("bytes") or 0
            возраст = мета.get("age_h")
            p(f"  снимок {ключ}: {размер / 1024:,.0f} КиБ".replace(",", " ")
              + (f", возраст {возраст:.1f} ч" if возраст is not None else "")
              + (f", статей {мета['articles']}" if мета.get("articles") is not None else ""))
        p(f"  {'проверка':<18} {'подпись':<78} {'проверено':>10} {'дефектных':>10} {'доля':>8}"
          + ("  было" if было else ""))
        for к, (проверено, дефектных) in т.счета.items():
            if not проверено:
                continue
            хвост = ""
            if было and (т.ид, к) in было:
                # Число «было» — из KV: печатается только целым, иначе «?» (перевод
                # строки в значении дал бы в журнале строку-команду «::…»).
                прежнее = было[(т.ид, к)]
                хвост = f"  {прежнее}" if целое(прежнее) else "  ?"
            p(f"  {к:<18} {т.подписи[к]:<78} {проверено:>10} {дефектных:>10} "
              f"{доля_текстом(дефектных, проверено):>8}{хвост}")
            if к in т.формы:
                p("  " + " " * 19 + "образцы: " + ", ".join(f"{ф} ×{n}" for ф, n in т.формы[к].most_common(5)))
        не_проверено = [к for к, (п, _) in т.счета.items() if not п]
        if не_проверено:
            p(f"  не к чему применить: {len(не_проверено)} проверок ({', '.join(не_проверено)})")
        for з in т.заметки:
            p(f"  · {з}")
        if т.доли:
            p(f"  {'польза':<18} {'подпись':<78} {'числитель':>10} {'из':>10} {'доля':>8}")
            for к, (ч, з) in т.доли.items():
                p(f"  {к:<18} {т.подписи_пользы[к]:<78} {ч:>10} {з:>10} {доля_текстом(ч, з):>8}")
    p("\n═══ СВОДКА: где работа ═══")
    p(f"  {'вкладка':<14} {'проверок':>9} {'с дефектом':>11} {'дефектных штук':>15} "
      f"{'ср. доля дефектов':>18} {'ср. польза':>11}")
    # Порядок — по дефектным штукам: сорсер видит строки, а не доли. Средняя доля
    # по сотне проверок с равным весом размывает массовый дефект (тысячи строк
    # «карточка N в Битриксе» сдвигают её меньше чем на процент) — она вторым ключом.
    порядок = sorted(вкладки, key=lambda т: (-т.дефектных_штук(), -(т.средняя_доля_дефектов() or 0),
                                             т.средняя_польза() or 1))
    for т in порядок:
        сд, сп = т.средняя_доля_дефектов(), т.средняя_польза()
        проверок = sum(1 for п, _ in т.счета.values() if п)
        с_деф = sum(1 for п, д in т.счета.values() if п and д)
        p(f"  {т.ид:<14} {проверок:>9} {с_деф:>11} {т.дефектных_штук():>15} "
          f"{(f'{100 * сд:.1f} %' if сд is not None else '—'):>18} "
          f"{(f'{100 * сп:.1f} %' if сп is not None else '—'):>11}")
    if порядок:
        p(f"  следующая вкладка для правки: {порядок[0].ид} (больше всего дефектных штук)")
        по_доле = max(вкладки, key=lambda т: т.средняя_доля_дефектов() or 0)
        if по_доле is not порядок[0]:
            p(f"  по средней доле дефектов первая: {по_доле.ид}")


def отказ_записи(источник, только, prev_dir, вкладки: list[Вкладка], с: Снимки) -> str | None:
    """Почему итог нельзя класть в audit:v1; None — можно. Каждая причина —
    итог, который затёр бы полный или соврал бы о нём: следующий прогон берёт
    из audit:v1 столбец «было», а вкладка «Счётчики» покажет его как всю ревизию."""
    if not isinstance(источник, ИсточникKV):
        return "снимки из папки (--from-dir): итог по файлам в живую KV"
    if только:
        return "частичная ревизия (--only): одна вкладка вместо всей ревизии"
    if prev_dir:
        return "«у скольких хуже» посчитано по локальному прошлому снимку (--prev-dir)"
    if not есть_правило_кода():
        return "нет правила кода (docfilter.класс_не_кода): проверки «марка, размер, стандарт» не посчитаны"
    упавшие = [т.ид for т in вкладки if т.упала]
    if упавшие:
        return f"упали вкладки: {', '.join(упавшие)}"
    неполные = [т.ид for т in вкладки if т.неполная]
    if неполные:
        return f"посчитаны не целиком: {', '.join(неполные)}"
    if с.битые:
        return f"не разобрались как JSON ключей: {len(с.битые)}"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ревизия вкладок портала: дефекты и польза")
    ap.add_argument("--from-dir", help="читать снимки из папки файлов (ключ с «_» вместо «:»)")
    ap.add_argument("--prev-dir", help="прошлый снимок номенклатуры — считать «у скольких хуже»")
    ap.add_argument("--only", help="вкладки через запятую: suppliers,nomenclature,brands,counters,library,dict")
    ap.add_argument("--out", help="сохранить итог (только агрегаты) в файл JSON")
    a = ap.parse_args(argv)
    сейчас = datetime.now(timezone.utc)
    ps = None
    try:
        if a.from_dir:
            источник = Папка(a.from_dir)
        else:
            источник, ps = источник_kv()
        с = Снимки(источник)
        прошлая = с.json(КЛЮЧ_РЕВИЗИИ)
        прошлые = Снимки(Папка(a.prev_dir)) if a.prev_dir else None
        только = set(a.only.split(",")) if a.only else None
        вкладки = ревизия(с, сейчас, читать_словарь(), прошлые, только=только)
    except Exception as ошибка:  # noqa: BLE001 — наружу только код, без данных
        код = str(ошибка) if re.fullmatch(r"[A-Z_]+", str(ошибка)) else type(ошибка).__name__
        print(f"::error::ревизия не состоялась: {код}")
        return 1
    if not есть_правило_кода():
        print("::warning::docfilter.класс_не_кода недоступен: проверки «марка, размер, стандарт» "
              "не применены (строка «не к чему применить»), запись итога запрещена")
    печать(вкладки, прошлая)
    упавшие = [т.ид for т in вкладки if т.упала]
    if упавшие:
        print(f"::error::ревизия упала на вкладках: {', '.join(упавшие)} — остальные посчитаны")
    if с.битые:
        print(f"\n::warning::не разобрались как JSON ключей: {len(с.битые)}")
    if isinstance(источник, ИсточникKV):
        print(f"\nзапросов к KV: {источник.запросов}")
    итог = сводка(вкладки, сейчас, источник.вид)
    raw = json.dumps(итог, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    print(f"итог ревизии: {len(raw)} Б")
    if a.out:
        Path(a.out).write_bytes(raw)
        print(f"итог сохранён в {a.out}")
    if os.environ.get("AUDIT_APPLY") == "1":
        отказ = отказ_записи(источник, только, a.prev_dir, вкладки, с)
        if отказ:
            print(f"::error::AUDIT_APPLY=1: {отказ} — итог в {КЛЮЧ_РЕВИЗИИ} не пишется")
            return 1
        try:
            записать_ревизию(raw, ps)
        except Exception as ошибка:  # noqa: BLE001
            код = str(ошибка) if re.fullmatch(r"[A-Z_]+", str(ошибка)) else type(ошибка).__name__
            print(f"::error::запись {КЛЮЧ_РЕВИЗИИ} не состоялась: {код}")
            return 1
        print(f"итог записан в KV, ключ {КЛЮЧ_РЕВИЗИИ}")
    else:
        print(f"вхолостую: {КЛЮЧ_РЕВИЗИИ} не записан (AUDIT_APPLY=1 включает запись)")
    return 1 if упавшие else 0


if __name__ == "__main__":
    raise SystemExit(main())
