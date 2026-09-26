#!/usr/bin/env python3
"""Предложения поставщиков за неделю: что поступило, что мы расшифровали, статистика.

ЗАЧЕМ. Вопрос владельца 26.09.2026: «Дай мне свод о том, какие предложения
поступили в компанию за прошедшую неделю? И расшифровал ты их или нет? И дай мне
статистику». Ответ собирается из базы библиотеки, Битрикс не читается.

ЧТО ТАКОЕ ПРЕДЛОЖЕНИЕ. Файл стороны «поставщик» в lib_files — ровно три места:
  · карточки запросов — вложения карточек СП-166, origin «поле запроса», поля КП
    (scripts/quote_coverage.py ПОЛЯ_КП; наш «Request file» разбор не берёт);
  · письма поставщиков — вложения и тела входящих писем компаний и контактов,
    origin «письмо поставщика» (library/mail_source.py, группа mail-supplier);
  · поля сделок — файлы стороны «поставщик» в полях СДЕЛКИ («Offer from
    supplier(s)», lib_files.side). Цены из них не пишутся по устройству кода
    (indexer.цены_файла: заявка заказчика и предложение там вперемешку).
Единица счёта — файл: один КП — одно предложение, тело письма — тоже документ.

ДВЕ ДАТЫ, И ПУТАТЬ ИХ НЕЛЬЗЯ. «Поступило на неделе» и «обработано нами на
неделе» — разные вопросы, и ответ на второй выдавать за первый нельзя: переразбор
и догонка писем приносят на неделю старые файлы сотнями.

  ПОСТУПИЛО (срез 1) — дата самого предложения, какую база знает. Она пишется
  ТОЛЬКО в строки цены (lib_prices.price_date, источник в price_date_src,
  library/quote_date.py): в lib_files даты документа нет, а файловое поле CRM
  даты загрузки не отдаёт вовсе. Источники, по убыванию точности прихода:
    письмо             — заголовок «Дата:» вложенного .eml/.msg — день прихода;
    письмо в CRM       — у писем поставщиков запасная дата — CREATED дела-письма,
                         то есть день, когда письмо легло в CRM (в базе она лежит
                         с пометкой «карточка: создана», mail_source.ссылки_письма);
    дата в самом КП    — «КП № 15 от 12.03.2025»: день составления, приход — обычно
                         в те же дни, но не раньше;
    карточка создана   — createdTime карточки СП-166: НИЖНЯЯ граница, предложение
                         приходит позже запроса. Берётся и у файлов без цены, если
                         дату карточки записал другой файл той же карточки;
    источник не записан — price_date есть, price_date_src пуст (строки до миграции).
  Следствие, которое печатается прямо: у файла без строк цены даты поступления в
  базе нет. Поэтому срез «поступило» почти целиком состоит из расшифрованных с
  ценой, и доля «с ценой» в нём завышена ПО ПОСТРОЕНИЮ — судить о расшифровке по
  нему нельзя (CLAUDE.md, «Эталон не может быть производным от правила»).

  ОБРАБОТАНО (срез 2) — lib_files.processed_at в окне. Это ПОСЛЕДНЯЯ обработка:
  переразбор, повтор «не скачался» и распознавание скана переписывают отметку
  (indexer.вставка_файлов, reparse.py, ocr.py). Колонки «впервые записан» у
  lib_files нет. Первичность различается по строкам спроса: переразбор старые
  строки lib_demand не удаляет, а помечает (lib_row_junk), поэтому самая ранняя
  lib_demand.created_at файла — день, когда он впервые дал позиции. Раньше окна —
  «повторно», в окне — «впервые»; у файла без строк первичность не установить.
  Именно в этом срезе честно видно, что расшифровано, а что нет.

ЧТО ПЕЧАТАЕТ (журнал публичный — только агрегаты, CLAUDE.md правило 17): по
каждому срезу — таблицы по источнику и по дню (дни UTC): файлов, по статусу
разбора, с позициями, позиций, с ценой, строк цены, строк цены по валютам, с
поставщиком, низкой уверенности, с датой квотации и из них с датой из самого КП,
различных компаний, брендов, карточек; откуда дата и первичность; «расшифровано»
долями; почему не расшифровано (статус и причина — константы кода, цифры в
причине заменены знаком #); где позиции есть, а цены нет (путь и шапка); роли
предложений (library/offer_role.py: прямое / трейдер / не определено). Ни имён
компаний, ни брендов, ни кодов, ни номеров карточек и сделок, ни почт, ни имён
файлов.

ПОДРОБНАЯ ЧАСТЬ — ТОЛЬКО ЗАШИФРОВАННОЙ. Со входом PUBKEY_B64 (открытый ключ RSA,
PEM в base64 одной строкой) скрипт пишет DETAIL_OUT (по умолчанию
/tmp/weekly_offers_detail.json, права 600): по каждому предложению — даты, номер
карточки или сделки, компания, статус, позиции, цены, суммы по валютам, бренды,
до 20 строк цены, роль. Шифрует шаг прогона (.github/workflows/library-stats.yml,
job weekly-offers) и удаляет открытый файл до загрузки; в артефакт идёт только
*.enc. Без ключа подробная часть не пишется вовсе. Закрытый ключ во входе —
отказ: он уже виден в журнале запуска, и пару надо заменить.

ОКНО — дни UTC, обе границы включительно. Входы: DAYS (по умолчанию 7: сегодня и
шесть дней до него), FROM и TO (ГГГГ-ММ-ДД). FROM и TO вместе — ровно они, DAYS
не действует; один FROM — до сегодня; один TO — DAYS дней, кончая им.

Только выборки, соединение только для чтения, statement_timeout — в строке
подключения (CLAUDE.md, правило 9). Колонки спрашиваются у базы
(information_schema), и без необязательной колонки замер не падает, а печатает
оговорку. Подробности файлов читаются частями по ПОРЦИЯ (правило дробления).

    SUPABASE_DB_URL=… python scripts/weekly_offers.py
    SUPABASE_DB_URL=… FROM=2026-09-14 TO=2026-09-20 python scripts/weekly_offers.py
"""
from __future__ import annotations

import base64
import binascii
import collections
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import doc_folder, doc_side, mail_source, price_store, quote_date  # noqa: E402

#: ПОЛЯ КП СП-166 — поля карточки, чья папка «предложение поставщика», с
#: названиями. Это то же множество, что scripts/quote_coverage.py ПОЛЯ_КП (по нему
#: индексатор собирает вложения): карта doc_folder сверена с ним тестом
#: test_doc_folder, а равенство множеств — tests/test_weekly_offers.py. Импорт
#: самого quote_coverage потянул бы за собой bitrix_client и requests, которых
#: прогон сводки не ставит (tests/test_workflow_deps.py): замер Битрикс не читает.
ПОЛЯ_КП = {код: название for код, (папка, название) in doc_folder.ПО_КОДУ[doc_folder.СП166].items()
           if папка == doc_folder.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА}

UTC = timezone.utc

# ── Источники ────────────────────────────────────────────────────────────────
ИСТ_КАРТОЧКИ = "карточки запросов"
ИСТ_ПИСЬМА = "письма поставщиков"
ИСТ_СДЕЛКИ = "поля сделок"
ИСТОЧНИКИ = (ИСТ_КАРТОЧКИ, ИСТ_ПИСЬМА, ИСТ_СДЕЛКИ)
КОРОТКО = {ИСТ_КАРТОЧКИ: "карточки", ИСТ_ПИСЬМА: "письма", ИСТ_СДЕЛКИ: "сделки"}

ORIGIN_ЗАПРОСА = "поле запроса"          # indexer.ссылки_карточек
ORIGIN_СДЕЛКИ = "поле сделки"            # indexer.collect_refs
ORIGIN_ПИСЬМА = doc_folder.ПИСЬМО_ПОСТАВЩИКА
FEED_КП = price_store.FEED
FEED_ПИСЬМА = price_store.FEED_ПИСЬМА
ПОТОКИ = [FEED_КП, FEED_ПИСЬМА]
#: Где цена пишется по устройству кода (indexer.цены_файла). У писем — только со
#: входом MAIL_PRICES прогона разбора писем, но по устройству — да.
ЦЕНА_ПИШЕТСЯ = {ИСТ_КАРТОЧКИ: True, ИСТ_ПИСЬМА: True, ИСТ_СДЕЛКИ: False}

#: Файлов на один запрос подробностей (правило дробления).
ПОРЦИЯ = 500
СТРОК_ЦЕНЫ_В_ПОДРОБНОСТЯХ = 20
БРЕНДОВ_В_ПОДРОБНОСТЯХ = 10
ДЛИНА_НАИМЕНОВАНИЯ = 120

# ── Статусы разбора (константы кода — печатать можно) ────────────────────────
СТАТУСЫ = {
    "разобран": "разобран",
    "разобран по скану": "по скану",
    "пусто": "пусто (нет текста)",
    "не скачался": "не скачался",
    "текст без спецификации": "без спецификации",
    "формат не читаем": "формат не читаем",
}
ПРОЧИЙ_СТАТУС = "прочий статус"

# ── Дата поступления ─────────────────────────────────────────────────────────
ДАТА_ПИСЬМА = "письмо (заголовок .eml/.msg)"
ДАТА_CRM = "письмо в CRM (создано)"
ДАТА_КП = "дата в самом КП"
ДАТА_КАРТОЧКИ = "карточка создана (нижняя граница)"
ДАТА_БЕЗ_ИСТОЧНИКА = "дата цены, источник не записан"
НЕТ_ДАТЫ = "дата в базе не записана"
ОТКУДА_ДАТА = (ДАТА_ПИСЬМА, ДАТА_CRM, ДАТА_КП, ДАТА_КАРТОЧКИ, ДАТА_БЕЗ_ИСТОЧНИКА, НЕТ_ДАТЫ)
КОРОТКО_ДАТА = {ДАТА_ПИСЬМА: "дата: письмо (.eml/.msg)", ДАТА_CRM: "дата: письмо в CRM",
                ДАТА_КП: "дата: в самом КП", ДАТА_КАРТОЧКИ: "дата: карточка (нижн. граница)",
                ДАТА_БЕЗ_ИСТОЧНИКА: "дата: источник не записан", НЕТ_ДАТЫ: "дата: не записана"}

В_ОКНЕ, ДО_ОКНА, ПОСЛЕ_ОКНА, НЕ_ЗАПИСАНА = "в окне", "до окна", "после окна", "не записана"

# ── Первичность обработки ────────────────────────────────────────────────────
ВПЕРВЫЕ = "впервые (первые строки спроса файла — в окне)"
ПОВТОРНО = "повторно (строки спроса были до окна)"
НЕ_УСТАНОВИТЬ = "не установить (строк спроса нет)"
ПЕРВИЧНОСТЬ = (ВПЕРВЫЕ, ПОВТОРНО, НЕ_УСТАНОВИТЬ)
#: Короткие подписи строк таблицы (ширина колонки показателя — 34 знака).
КОРОТКО_ПЕРВИЧНОСТЬ = {ВПЕРВЫЕ: "позиции впервые (в окне)", ПОВТОРНО: "повторно (строки были раньше)",
                       НЕ_УСТАНОВИТЬ: "первичность не установить"}

ДНИ_НЕДЕЛИ = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
ПУТЬ_ПОДРОБНОСТЕЙ = "/tmp/weekly_offers_detail.json"


# ── Окно ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Окно:
    """Дни UTC, обе границы включительно."""
    с: date
    по: date

    @property
    def начало(self) -> datetime:
        return datetime(self.с.year, self.с.month, self.с.day, tzinfo=UTC)

    @property
    def конец(self) -> datetime:
        """Полночь ПОСЛЕ последнего дня — граница исключительно."""
        д = self.по + timedelta(days=1)
        return datetime(д.year, д.month, д.day, tzinfo=UTC)

    @property
    def дней(self) -> int:
        return (self.по - self.с).days + 1

    def где(self, д: date | None) -> str:
        if д is None:
            return НЕ_ЗАПИСАНА
        if д < self.с:
            return ДО_ОКНА
        if д > self.по:
            return ПОСЛЕ_ОКНА
        return В_ОКНЕ

    def содержит_момент(self, м: datetime | None) -> bool:
        return м is not None and self.начало <= м < self.конец

    def дни(self) -> list[date]:
        return [self.с + timedelta(days=i) for i in range(self.дней)]


def _дата(имя: str, значение: str) -> date:
    try:
        return date.fromisoformat(значение.strip())
    except ValueError:
        raise ValueError(f"{имя}={значение!r}: нужна дата ГГГГ-ММ-ДД") from None


def окно_из_env(env, сегодня: date | None = None) -> Окно:
    """Окно по входам DAYS, FROM, TO. Ошибка входа — ValueError с понятным текстом."""
    сегодня = сегодня or datetime.now(UTC).date()
    дней_текст = str(env.get("DAYS") or "").strip() or "7"
    try:
        дней = int(дней_текст)
    except ValueError:
        raise ValueError(f"DAYS={дней_текст!r}: нужно целое число дней") from None
    if not 1 <= дней <= 366:
        raise ValueError(f"DAYS={дней}: допустимо от 1 до 366")
    с_текст = str(env.get("FROM") or "").strip()
    по_текст = str(env.get("TO") or "").strip()
    по = _дата("TO", по_текст) if по_текст else сегодня
    с = _дата("FROM", с_текст) if с_текст else по - timedelta(days=дней - 1)
    if с > по:
        raise ValueError(f"FROM {с.isoformat()} позже TO {по.isoformat()}")
    if (по - с).days + 1 > 366:
        raise ValueError("окно длиннее 366 дней — это уже не недельный свод")
    return Окно(с, по)


# ── Ключ подробной части ─────────────────────────────────────────────────────

def проверить_ключ(значение: str | None) -> tuple[bool, str]:
    """(писать ли подробности, что сказать в журнал). Сам ключ не печатается."""
    т = "".join(str(значение or "").split())
    if not т:
        return False, "подробная часть не пишется: вход pubkey пуст (журнал — только агрегаты)"
    try:
        pem = base64.b64decode(т, validate=True).decode("ascii")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False, "::warning::вход pubkey не читается как base64 PEM — подробная часть не пишется"
    if "PRIVATE" in pem.upper():
        return False, ("::error::во входе pubkey ЗАКРЫТЫЙ ключ. Входы запуска видны всем, кто видит "
                       "прогоны: эту пару больше не использовать. Подробная часть не пишется")
    if "-----BEGIN PUBLIC KEY-----" not in pem and "-----BEGIN RSA PUBLIC KEY-----" not in pem:
        return False, "::warning::вход pubkey не похож на открытый ключ PEM — подробная часть не пишется"
    return True, "подробная часть: пишется для шифрования открытым ключом из входа"


# ── Предложение ──────────────────────────────────────────────────────────────

@dataclass
class Предложение:
    file_id: str
    источник: str
    владелец: str | None          # карточка, сделка или «C…»/«K…» у письма
    поле: str | None
    статус: str | None
    причина: str | None
    вид: str | None
    позиций: int
    обработан: datetime | None
    путь: str | None = None
    шапка: bool | None = None
    строк_цены: int = 0
    валюты: collections.Counter = field(default_factory=collections.Counter)
    суммы: dict = field(default_factory=dict)       # валюта → {итог, строк_с_итогом, цены}
    с_поставщиком: int = 0
    низкая: int = 0
    с_датой: int = 0
    дата_из_кп: int = 0
    даты: dict = field(default_factory=dict)        # источник даты → дата (свои строки)
    компании: set = field(default_factory=set)      # ключи компаний портала
    бренды_файла: set = field(default_factory=set)  # написания изготовителя из файла
    бренды_карточки: set = field(default_factory=set)  # номера СП-176 с карточки
    первые_строки: datetime | None = None
    дата_поступления: date | None = None
    откуда_дата: str = НЕТ_ДАТЫ
    дата_по_карточке: bool = False
    роль: dict | None = None
    строки: list = field(default_factory=list)

    @property
    def карточка(self) -> str | None:
        return self.владелец if self.источник == ИСТ_КАРТОЧКИ else None

    @property
    def тело_письма(self) -> bool:
        return self.file_id.startswith(mail_source.ПРИСТАВКА_ТЕЛА)

    @property
    def метка_статуса(self) -> str:
        return СТАТУСЫ.get(self.статус or "", ПРОЧИЙ_СТАТУС)

    def первичность(self, окно: Окно) -> str:
        if self.первые_строки is None:
            return НЕ_УСТАНОВИТЬ
        return ВПЕРВЫЕ if self.первые_строки >= окно.начало else ПОВТОРНО


def источник_файла(origin, field_code, side, field_title) -> str | None:
    """Источник предложения или None — файл не со стороны поставщика."""
    if origin == ORIGIN_ЗАПРОСА:
        return ИСТ_КАРТОЧКИ if field_code in ПОЛЯ_КП else None
    if origin == ORIGIN_ПИСЬМА:
        return ИСТ_ПИСЬМА
    if origin == ORIGIN_СДЕЛКИ:
        сторона = side or (doc_side.сторона(field_title) if field_title else None)
        return ИСТ_СДЕЛКИ if сторона == doc_side.ПОСТАВЩИК else None
    return None


def ключ_компании_письма(владелец: str | None) -> str | None:
    """«C123» — письмо висит на компании 123 портала; у контакта («K…») компании нет."""
    в = str(владелец or "")
    return в[1:] if re.fullmatch(r"C\d+", в) else None


def выбрать_дату(п: Предложение, дата_карточки: date | None) -> None:
    """Дата поступления по убыванию точности прихода (см. шапку модуля)."""
    д = п.даты
    письмо_crm = д.get(quote_date.КАРТОЧКА) if п.источник == ИСТ_ПИСЬМА else None
    карточка = д.get(quote_date.КАРТОЧКА) if п.источник == ИСТ_КАРТОЧКИ else None
    for значение, откуда in ((д.get(quote_date.ПИСЬМО), ДАТА_ПИСЬМА), (письмо_crm, ДАТА_CRM),
                             (д.get(quote_date.ДОКУМЕНТ), ДАТА_КП), (карточка, ДАТА_КАРТОЧКИ),
                             (д.get(None), ДАТА_БЕЗ_ИСТОЧНИКА)):
        if значение is not None:
            п.дата_поступления, п.откуда_дата = значение, откуда
            return
    if п.источник == ИСТ_КАРТОЧКИ and дата_карточки is not None:
        п.дата_поступления, п.откуда_дата, п.дата_по_карточке = дата_карточки, ДАТА_КАРТОЧКИ, True
        return
    п.дата_поступления, п.откуда_дата = None, НЕТ_ДАТЫ


# ── Журнал: только константы и числа ─────────────────────────────────────────

def причина_для_журнала(причина) -> str:
    """Причина отказа — константа кода плюс счётчики. Цифры → «#», кавычки, адреса
    и почты снимаются: в журнал не должно попасть ничего из самих данных."""
    т = " ".join(str(причина or "").split())
    if not т:
        return "(без причины)"
    т = re.sub(r"https?://\S+|\S+@\S+", "…", т)
    т = re.sub(r"«[^»]*»|\"[^\"]*\"|'[^']*'", "«…»", т)
    т = re.sub(r"\d+", "#", т)
    return т[:60]


def валюта_для_журнала(валюта) -> str:
    в = str(валюта or "").strip().upper()
    if not в:
        return "(не указана)"
    return в if re.fullmatch(r"[A-Z]{3}", в) else "(прочая)"


def бренд_ключ(написание) -> str:
    return " ".join(str(написание or "").lower().replace("ё", "е").split())


# ── Свод ─────────────────────────────────────────────────────────────────────

class Свод:
    """Агрегаты по группе предложений: только числа и множества ключей для счёта."""

    def __init__(self) -> None:
        self.файлов = 0
        self.статусы: collections.Counter = collections.Counter()
        self.тел_писем = 0
        self.с_позициями = 0
        self.позиций = 0
        self.с_ценой = 0
        self.пишущих = 0             # файлов источников, где цена пишется
        self.пишущих_с_ценой = 0
        self.строк_цены = 0
        self.валюты: collections.Counter = collections.Counter()
        self.с_поставщиком = 0
        self.низкая = 0
        self.с_датой = 0
        self.дата_из_кп = 0
        self.компании: set = set()
        self.сущности: set = set()
        self.бренды: set = set()
        self.бренды_карточек: set = set()
        self.карточки: set = set()
        self.откуда_дата: collections.Counter = collections.Counter()
        self.первичность: collections.Counter = collections.Counter()
        self.почему_нет: collections.Counter = collections.Counter()   # (источник, статус, причина)
        self.немые: collections.Counter = collections.Counter()        # (путь, шапка)
        self.роли: collections.Counter = collections.Counter()
        self.причины_ролей: collections.Counter = collections.Counter()

    def добавить(self, п: Предложение, окно: Окно, компании_реестра: dict) -> None:
        self.файлов += 1
        self.статусы[п.метка_статуса] += 1
        self.тел_писем += п.тело_письма
        if п.позиций > 0:
            self.с_позициями += 1
            self.позиций += п.позиций
        else:
            self.почему_нет[(п.источник, п.метка_статуса, причина_для_журнала(п.причина))] += 1
        if п.строк_цены > 0:
            self.с_ценой += 1
        if ЦЕНА_ПИШЕТСЯ[п.источник]:
            self.пишущих += 1
            self.пишущих_с_ценой += п.строк_цены > 0
            if п.позиций > 0 and п.строк_цены == 0:
                путь = п.путь or "(путь не записан)"
                шапка = ("(нет данных)" if п.шапка is None else "найдена" if п.шапка else "НЕ найдена")
                self.немые[(путь, шапка)] += 1
        self.строк_цены += п.строк_цены
        for в, n in п.валюты.items():
            self.валюты[валюта_для_журнала(в)] += n
        self.с_поставщиком += п.с_поставщиком
        self.низкая += п.низкая
        self.с_датой += п.с_датой
        self.дата_из_кп += п.дата_из_кп
        self.компании |= п.компании
        self.сущности |= {компании_реестра[к][0] for к in п.компании if к in компании_реестра}
        self.бренды |= {бренд_ключ(б) for б in п.бренды_файла if бренд_ключ(б)}
        self.бренды_карточек |= п.бренды_карточки
        if п.карточка:
            self.карточки.add(п.карточка)
        self.откуда_дата[п.откуда_дата] += 1
        self.первичность[п.первичность(окно)] += 1
        if п.роль is not None:
            self.роли[п.роль["role"]] += 1
            self.причины_ролей[(п.роль["role"], п.роль["why"])] += 1


def доля(часть: int, целое: int) -> str:
    return f"{часть} ({100 * часть / целое:.1f} %)" if целое else f"{часть}"


def строки_таблицы(своды: dict[str, Свод], срез: str) -> list[tuple[str, list[int]]]:
    """Показатель → значения по колонкам. Порядок строк один для всех таблиц."""
    с = list(своды.values())
    строки: list[tuple[str, list[int]]] = [("файлов (предложений)", [x.файлов for x in с])]
    for метка in list(СТАТУСЫ.values()) + [ПРОЧИЙ_СТАТУС]:
        if any(x.статусы[метка] for x in с) or метка != ПРОЧИЙ_СТАТУС:
            строки.append((f"  {метка}", [x.статусы[метка] for x in с]))
    строки.append(("  из них тел писем", [x.тел_писем for x in с]))
    строки += [("файлов с позициями", [x.с_позициями for x in с]),
               ("позиций", [x.позиций for x in с]),
               ("файлов с ценой", [x.с_ценой for x in с]),
               ("строк цены", [x.строк_цены for x in с])]
    все_валюты = collections.Counter()
    for x in с:
        все_валюты.update(x.валюты)
    for в, _ in все_валюты.most_common():
        строки.append((f"  в {в}", [x.валюты[в] for x in с]))
    строки += [("строк цены с поставщиком", [x.с_поставщиком for x in с]),
               ("строк цены низкой уверенности", [x.низкая for x in с]),
               ("строк цены с датой квотации", [x.с_датой for x in с]),
               ("  из них дата из самого КП", [x.дата_из_кп for x in с]),
               ("компаний (ключ портала)", [len(x.компании) for x in с]),
               ("  из них сведено с реестром", [len(x.сущности) for x in с]),
               ("брендов в файлах (написаний)", [len(x.бренды) for x in с]),
               ("брендов карточек (СП-176)", [len(x.бренды_карточек) for x in с]),
               ("карточек запросов", [len(x.карточки) for x in с])]
    if срез == "поступило":
        for откуда in ОТКУДА_ДАТА:
            if откуда != НЕТ_ДАТЫ:
                строки.append((КОРОТКО_ДАТА[откуда], [x.откуда_дата[откуда] for x in с]))
    else:
        for п in ПЕРВИЧНОСТЬ:
            строки.append((КОРОТКО_ПЕРВИЧНОСТЬ[п], [x.первичность[п] for x in с]))
    return строки


def печать_таблицы(заголовок: str, своды: dict[str, Свод], срез: str) -> None:
    print(f"\n{заголовок}")
    ширина = max(9, max((len(к) for к in своды), default=9) + 1)
    print(f"    {'показатель':34s}" + "".join(f"{к:>{ширина}s}" for к in своды))
    for метка, значения in строки_таблицы(своды, срез):
        print(f"    {метка[:34]:34s}" + "".join(f"{int(v):>{ширина}d}" for v in значения))


def метка_дня(д: date) -> str:
    return f"{ДНИ_НЕДЕЛИ[д.weekday()]} {д.strftime('%d.%m')}"


def группы_дней(окно: Окно) -> list[tuple[str, date, date]]:
    """До десяти дней — по дню; длиннее — по неделе ISO (иначе таблица не читается)."""
    if окно.дней <= 10:
        return [(метка_дня(д), д, д) for д in окно.дни()]
    группы: dict[str, list[date]] = collections.OrderedDict()
    for д in окно.дни():
        г, н, _ = д.isocalendar()
        группы.setdefault(f"{г}-W{н:02d}", []).append(д)
    return [(k, v[0], v[-1]) for k, v in группы.items()]


# ── Чтение базы ──────────────────────────────────────────────────────────────

КОЛОНКИ_SQL = """
select table_name, column_name from information_schema.columns
 where table_schema = any(current_schemas(false))
   and table_name in ('lib_files', 'lib_prices', 'lib_demand', 'lib_row_junk')
"""


def есть_отношение(cur, имя: str) -> bool:
    cur.execute("select to_regclass(%s) is not null", (имя,))
    return bool(cur.fetchone()[0])


def колонки_базы(cur) -> dict[str, set[str]]:
    cur.execute(КОЛОНКИ_SQL)
    out: dict[str, set[str]] = collections.defaultdict(set)
    for таблица, колонка in cur.fetchall():
        out[таблица].add(колонка)
    return out


def _к(колонки: set[str], имя: str, тип: str, псевдоним: str = "") -> str:
    """Колонка, если она есть в базе, иначе пустое значение того же типа."""
    п = f"{псевдоним}." if псевдоним else ""
    return f"{п}{имя}" if имя in колонки else f"null::{тип}"


def _порции(ключи: list[str]):
    for i in range(0, len(ключи), ПОРЦИЯ):
        yield ключи[i:i + ПОРЦИЯ]


class Замер:
    """Чтение базы и сбор предложений окна. Ничего не пишет."""

    ОБЯЗАТЕЛЬНЫЕ_ФАЙЛОВ = {"file_id", "deal_id", "origin", "field", "status", "reason",
                           "kind", "rows_found", "processed_at"}
    ОБЯЗАТЕЛЬНЫЕ_ЦЕН = {"source_url", "feed", "currency", "price", "confidence", "price_date"}

    def __init__(self, cur, окно: Окно, подробно: bool = False) -> None:
        self.cur = cur
        self.окно = окно
        self.подробно = подробно
        self.оговорки: list[str] = []
        self.предложения: dict[str, Предложение] = {}
        self.компании_реестра: dict[str, tuple[str, str | None]] = {}
        self.вне_полей_кп = 0
        self.не_поставщика = 0      # поле сделки без стороны, название поля — не поставщика
        self.без_записи_файла = 0   # строка цены есть, записи lib_files нет
        self.порций = 0
        self.роли_посчитаны = False

    # -- колонки ---------------------------------------------------------
    def проверить_схему(self) -> bool:
        for т in ("lib_files", "lib_prices"):
            if not есть_отношение(self.cur, т):
                print(f"нет таблицы {т} — мерить нечего")
                return False
        self.кол = колонки_базы(self.cur)
        нет = (self.ОБЯЗАТЕЛЬНЫЕ_ФАЙЛОВ - self.кол["lib_files"]) | (self.ОБЯЗАТЕЛЬНЫЕ_ЦЕН - self.кол["lib_prices"])
        if нет:
            print("в базе нет колонок " + ", ".join(sorted(нет)) + " — мерить нечем")
            return False
        ф, ц = self.кол["lib_files"], self.кол["lib_prices"]
        if "side" not in ф:
            self.оговорки.append("колонки lib_files.side в базе нет — файлы поставщика в полях сделок "
                                 "не отличить от спроса, источник «поля сделок» не считается")
        if "price_date_src" not in ц:
            self.оговорки.append("колонки lib_prices.price_date_src в базе нет — источник даты "
                                 "квотации неизвестен, дата карточки не отличима от даты КП")
        if not {"parse_path", "header_found"} <= ф:
            self.оговорки.append("колонок parse_path/header_found нет — «позиции есть, цены нет» "
                                 "без разреза по пути разбора")
        if "lib_demand" not in self.кол or not {"source_file", "created_at"} <= self.кол["lib_demand"]:
            self.оговорки.append("lib_demand без source_file/created_at — первичность обработки не установить")
        for к, т in (("rfq_company", "поставщик строки"), ("oem", "изготовитель из файла"),
                     ("rfq_brands", "бренды карточки"), ("rfq_id", "номер карточки"), ("total", "сумма строки")):
            if к not in ц:
                self.оговорки.append(f"колонки lib_prices.{к} нет — {т} не считается")
        return True

    # -- поиск предложений окна -----------------------------------------
    def найти(self) -> tuple[set[str], set[str]]:
        """(файлы, обработанные в окне; файлы с датой предложения в окне — кандидаты)."""
        ф, ц = self.кол["lib_files"], self.кол["lib_prices"]
        условия = ["origin in (%(запрос)s, %(письмо)s)"]
        if "side" in ф:
            if "field_title" in ф:
                условия.append("(origin = %(сделка)s and (side = %(поставщик)s"
                               " or (side is null and field_title is not null)))")
            else:
                условия.append("(origin = %(сделка)s and side = %(поставщик)s)")
        п = {"запрос": ORIGIN_ЗАПРОСА, "письмо": ORIGIN_ПИСЬМА, "сделка": ORIGIN_СДЕЛКИ,
             "поставщик": doc_side.ПОСТАВЩИК, "начало": self.окно.начало, "конец": self.окно.конец,
             "с": self.окно.с, "по": self.окно.по, "потоки": ПОТОКИ, "кп": FEED_КП,
             "карточка": quote_date.КАРТОЧКА}
        self.cur.execute("select file_id from lib_files"
                         " where processed_at >= %(начало)s and processed_at < %(конец)s"
                         "   and (" + " or ".join(условия) + ")", п)
        обработаны = {r[0] for r in self.cur.fetchall()}

        self.cur.execute("select distinct source_url from lib_prices"
                         " where feed = any(%(потоки)s) and source_url is not null"
                         "   and price_date >= %(с)s and price_date <= %(по)s", п)
        датированы = {r[0] for r in self.cur.fetchall()}
        # Дата карточки, записанная ЛЮБЫМ её файлом, — нижняя граница и для
        # файлов этой карточки без строк цены.
        if "price_date_src" in ц and "rfq_id" in ц:
            self.cur.execute("select distinct rfq_id from lib_prices"
                             " where feed = %(кп)s and price_date_src = %(карточка)s"
                             "   and rfq_id is not null"
                             "   and price_date >= %(с)s and price_date <= %(по)s", п)
            карточки = sorted({r[0] for r in self.cur.fetchall()})
            for часть in _порции(карточки):
                self.cur.execute("select file_id from lib_files where origin = %s and deal_id = any(%s)",
                                 (ORIGIN_ЗАПРОСА, часть))
                датированы |= {r[0] for r in self.cur.fetchall()}
        return обработаны, датированы

    # -- подробности по порциям ------------------------------------------
    def прочитать(self, ключи: set[str]) -> None:
        ф, ц = self.кол["lib_files"], self.кол["lib_prices"]
        файлы_sql = ("select file_id, origin, field, deal_id, status, reason, kind, rows_found, processed_at, "
                     + ", ".join((_к(ф, "side", "text"), _к(ф, "field_title", "text"),
                                  _к(ф, "parse_path", "text"), _к(ф, "header_found", "boolean")))
                     + " from lib_files where file_id = any(%s)")
        записаны: set[str] = set()
        for часть in _порции(sorted(ключи)):
            self.порций += 1
            self.cur.execute(файлы_sql, (часть,))
            for (fid, origin, поле, владелец, статус, причина, вид, позиций, обработан,
                 side, заголовок, путь, шапка) in self.cur.fetchall():
                записаны.add(fid)
                ист = источник_файла(origin, поле, side, заголовок)
                if ист is None:
                    if origin == ORIGIN_ЗАПРОСА:
                        self.вне_полей_кп += 1
                    else:
                        self.не_поставщика += 1
                    continue
                self.предложения[fid] = Предложение(
                    file_id=fid, источник=ист, владелец=владелец, поле=поле, статус=статус,
                    причина=причина, вид=вид, позиций=int(позиций or 0), обработан=обработан,
                    путь=путь, шапка=шапка)
        есть = set(self.предложения)
        self.без_записи_файла = len(ключи - записаны)
        свои = sorted(есть)
        src = _к(ц, "price_date_src", "text")
        компания = _к(ц, "rfq_company", "text")
        oem = _к(ц, "oem", "text")
        бренды = _к(ц, "rfq_brands", "text")
        итог = _к(ц, "total", "numeric")
        по_валютам = f"""
select source_url, currency, count(*)::bigint,
       count(*) filter (where nullif(btrim(coalesce({компания}, '')), '') is not null)::bigint,
       count(*) filter (where confidence = 'low')::bigint,
       count(*) filter (where price_date is not null)::bigint,
       count(*) filter (where {src} = %(документ)s)::bigint,
       sum({итог}), count({итог})::bigint, sum(price)
  from lib_prices
 where feed = any(%(потоки)s) and source_url = any(%(файлы)s)
 group by 1, 2"""
        по_файлу = f"""
select source_url,
       min(price_date) filter (where {src} = %(письмо)s),
       min(price_date) filter (where {src} = %(карточка)s),
       min(price_date) filter (where {src} = %(документ)s),
       min(price_date) filter (where {src} is null),
       array_agg(distinct btrim({компания})) filter (where nullif(btrim(coalesce({компания}, '')), '') is not null),
       array_agg(distinct btrim({oem})) filter (where nullif(btrim(coalesce({oem}, '')), '') is not null),
       array_agg(distinct btrim({бренды})) filter (where nullif(btrim(coalesce({бренды}, '')), '') is not null)
  from lib_prices
 where feed = any(%(потоки)s) and source_url = any(%(файлы)s)
 group by 1"""
        for часть in _порции(свои):
            self.порций += 1
            п = {"потоки": ПОТОКИ, "файлы": часть, "документ": quote_date.ДОКУМЕНТ,
                 "письмо": quote_date.ПИСЬМО, "карточка": quote_date.КАРТОЧКА}
            self.cur.execute(по_валютам, п)
            for fid, валюта, n, с_пост, низкая, с_датой, из_кп, сумма, с_суммой, цены in self.cur.fetchall():
                о = self.предложения[fid]
                о.строк_цены += int(n)
                о.валюты[валюта] += int(n)
                о.с_поставщиком += int(с_пост)
                о.низкая += int(низкая)
                о.с_датой += int(с_датой)
                о.дата_из_кп += int(из_кп)
                о.суммы[валюта] = {"строк": int(n), "итог_по_КП": сумма, "строк_с_итогом": int(с_суммой),
                                   "сумма_цен_за_единицу": цены}
            self.cur.execute(по_файлу, п)
            for fid, д_письмо, д_карточка, д_документ, д_без, компании, oem_, бренды_ in self.cur.fetchall():
                о = self.предложения[fid]
                о.даты = {k: v for k, v in ((quote_date.ПИСЬМО, д_письмо), (quote_date.КАРТОЧКА, д_карточка),
                                            (quote_date.ДОКУМЕНТ, д_документ), (None, д_без)) if v is not None}
                о.компании |= set(компании or ())
                о.бренды_файла |= set(oem_ or ())
                for б in бренды_ or ():
                    о.бренды_карточки |= набор_карточки(б)
        self.прочитать_карточки()
        self.прочитать_спрос(свои)
        for о in self.предложения.values():
            к = ключ_компании_письма(о.владелец) if о.источник == ИСТ_ПИСЬМА else None
            if к:
                о.компании.add(к)
        self.прочитать_реестр()
        if self.подробно:
            self.прочитать_строки(свои)

    def прочитать_карточки(self) -> None:
        """Компания, бренды и дата карточки — по строкам цены ЛЮБОГО её файла: у
        файла без цены своих строк нет, а поставщик у карточки один."""
        ц = self.кол["lib_prices"]
        if "rfq_id" not in ц:
            self.даты_карточек: dict = {}
            return
        карточки = sorted({о.владелец for о in self.предложения.values()
                           if о.источник == ИСТ_КАРТОЧКИ and о.владелец})
        src = _к(ц, "price_date_src", "text")
        компания = _к(ц, "rfq_company", "text")
        бренды = _к(ц, "rfq_brands", "text")
        sql = f"""
select rfq_id,
       min(price_date) filter (where {src} = %(карточка)s),
       array_agg(distinct btrim({компания})) filter (where nullif(btrim(coalesce({компания}, '')), '') is not null),
       array_agg(distinct btrim({бренды})) filter (where nullif(btrim(coalesce({бренды}, '')), '') is not null)
  from lib_prices
 where feed = %(кп)s and rfq_id = any(%(карточки)s)
 group by 1"""
        сведения: dict = {}
        for часть in _порции(карточки):
            self.порций += 1
            self.cur.execute(sql, {"карточка": quote_date.КАРТОЧКА, "кп": FEED_КП, "карточки": часть})
            for rfq, д, компании, бренды_ in self.cur.fetchall():
                сведения[rfq] = (д, set(компании or ()), set(бренды_ or ()))
        self.даты_карточек = {k: v[0] for k, v in сведения.items()}
        for о in self.предложения.values():
            if о.источник != ИСТ_КАРТОЧКИ or о.владелец not in сведения:
                continue
            _, компании, бренды_ = сведения[о.владелец]
            о.компании |= компании
            for б in бренды_:
                о.бренды_карточки |= набор_карточки(б)

    def прочитать_спрос(self, свои: list[str]) -> None:
        """Самая ранняя строка спроса файла (первичность) и изготовители позиций."""
        д = self.кол.get("lib_demand", set())
        if not {"source_file", "created_at"} <= д:
            return
        живые = "lib_row_junk" in self.кол and {"demand_id", "revoked_at"} <= self.кол["lib_row_junk"]
        oem = "d.oem" if "oem" in д else "null::text"
        sql = (f"select d.source_file, min(d.created_at),"
               f" array_agg(distinct btrim({oem})) filter (where nullif(btrim(coalesce({oem}, '')), '') is not null"
               + (" and j.demand_id is null" if живые else "") + ")"
               " from lib_demand d"
               + (" left join lib_row_junk j on j.demand_id = d.id and j.revoked_at is null" if живые else "")
               + " where d.source_file = any(%s) group by 1")
        с_позициями = [k for k in свои if self.предложения[k].позиций > 0]
        for часть in _порции(с_позициями):
            self.порций += 1
            self.cur.execute(sql, (часть,))
            for fid, первая, oem_ in self.cur.fetchall():
                о = self.предложения[fid]
                о.первые_строки = первая
                о.бренды_файла |= set(oem_ or ())

    def прочитать_реестр(self) -> None:
        """Ключ компании портала → (сущность реестра, имя). Имя — только в подробности
        и в правило роли; в журнал идёт лишь число сведённых."""
        if not (есть_отношение(self.cur, "sup_identifier") and есть_отношение(self.cur, "sup_entity")):
            self.оговорки.append("реестра компаний (sup_identifier, sup_entity) в базе нет — "
                                 "компании не сведены, роль не определить")
            return
        имена = есть_отношение(self.cur, "sup_name_shown")
        sql = ("select i.value_norm, e.id, " + ("coalesce(nm.name, e.display_name)" if имена else "e.display_name")
               + " from sup_identifier i join sup_entity e on e.id = i.sup_id"
               + (" left join sup_name_shown nm on nm.sup_id = e.id" if имена else "")
               + " where i.kind = 'bitrix' and i.status <> 'rejected' and i.value_norm = any(%s)")
        ключи = sorted({к for о in self.предложения.values() for к in о.компании})
        for часть in _порции(ключи):
            self.порций += 1
            self.cur.execute(sql, (часть,))
            for ключ, сущность, имя in self.cur.fetchall():
                self.компании_реестра[ключ] = (сущность, имя)

    def прочитать_строки(self, свои: list[str]) -> None:
        """До 20 строк цены на файл — только для подробной (шифруемой) части."""
        ц = self.кол["lib_prices"]
        sql = f"""
select source_url, {_к(ц, 'part_number', 'text')}, left({_к(ц, 'item_name', 'text')}, {ДЛИНА_НАИМЕНОВАНИЯ}),
       {_к(ц, 'qty', 'numeric')}, {_к(ц, 'qty_unit', 'text')}, price, currency, confidence
  from (select p.*, row_number() over (partition by p.source_url order by p.id) as н
          from lib_prices p
         where p.feed = any(%(потоки)s) and p.source_url = any(%(файлы)s)) t
 where н <= {СТРОК_ЦЕНЫ_В_ПОДРОБНОСТЯХ}
 order by source_url, н"""
        for часть in _порции([k for k in свои if self.предложения[k].строк_цены > 0]):
            self.порций += 1
            self.cur.execute(sql, {"потоки": ПОТОКИ, "файлы": часть})
            for fid, код, имя, колво, ед, цена, валюта, ув in self.cur.fetchall():
                self.предложения[fid].строки.append(
                    {"код": код, "наименование": имя, "количество": _число(колво), "единица": ед,
                     "цена": _число(цена), "валюта": валюта, "уверенность": ув})

    # -- роль ------------------------------------------------------------
    def посчитать_роли(self) -> str:
        """Роль предложения (library/offer_role.py) по компании и брендам файла и
        карточки. Каталог не привлекается: он сводит бренд по ключу номера, а это
        отдельный проход по lib_parts, для недельного свода лишний."""
        т0 = time.monotonic()
        try:
            from library import offer_role
            доп, откуда = offer_role.карта_базы(self.cur)
            реестр = offer_role.реестр_файлов(tuple(map(tuple, доп)))
            ид = offer_role.идентификаторы(self.cur)
            sp176: dict = {}
            if есть_отношение(self.cur, "lib_brand_sp176"):
                self.cur.execute("select sp176_id::text, brand_key from lib_brand_sp176")
                sp176 = dict(self.cur.fetchall())
        except Exception as e:                                          # noqa: BLE001
            return f"роль не посчитана ({type(e).__name__}) — остальной свод от этого не зависит"
        кэш: dict = {}
        for о in self.предложения.values():
            if о.источник == ИСТ_СДЕЛКИ:
                continue            # поставщик у файла сделки в базе не записан
            сущности = sorted({self.компании_реестра[к][0] for к in о.компании if к in self.компании_реестра})
            if len(сущности) > 1:
                # Две компании у одного файла — сведение спорно, судить роль нельзя.
                о.роль = {"role": offer_role.НЕ_ОПРЕДЕЛЕНО, "why": "несколько компаний у предложения"}
                continue
            сущность = сущности[0] if сущности else None
            имя = next((self.компании_реестра[к][1] for к in sorted(о.компании)
                        if self.компании_реестра.get(к, (None,))[0] == сущность), None) if сущность else None
            домены, инн = ид.get(сущность, ([], [])) if сущность else ([], [])
            бренды = sorted(о.бренды_файла) + sorted({sp176[x] for x in о.бренды_карточки if x in sp176})
            ключ = (имя, tuple(бренды), tuple(домены), tuple(инн))
            if ключ not in кэш:
                кэш[ключ] = offer_role.роль_предложения(имя, бренды, домены=домены, инн=инн, реестр=реестр)
            о.роль = {"role": кэш[ключ]["role"], "why": кэш[ключ]["why"]}
        self.роли_посчитаны = True
        return (f"роль посчитана за {time.monotonic() - т0:.1f} с · карта брендов: {откуда}"
                " · бренд позиции: файл и карточка (каталог не привлекался)")


def набор_карточки(поле) -> set[str]:
    """«101,205» — номера элементов СП-176; обрубок в конце длинного поля снимается
    (как в scripts/offer_role_measure.карточка_в_ключи)."""
    т = str(поле or "")
    if len(т) >= 200:
        т = re.sub(r",[^,]*$", "", т)
    return {x for x in re.sub(r"\s", "", т).split(",") if x}


def _число(x):
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    return x


# ── Итог и печать ────────────────────────────────────────────────────────────

@dataclass
class Итог:
    окно: Окно
    поступило: list[Предложение]
    обработано: list[Предложение]
    вне_окна_дат: collections.Counter          # (откуда дата, где относительно окна) → файлов
    до_окна_обработаны: collections.Counter    # откуда дата → файлов (дата до окна, обработан в окне)
    замер: Замер
    сообщение_роли: str = ""


def собрать(cur, окно: Окно, подробно: bool = False) -> Итог | None:
    з = Замер(cur, окно, подробно)
    if not з.проверить_схему():
        return None
    обработаны, датированы = з.найти()
    з.прочитать(обработаны | датированы)
    даты_карточек = getattr(з, "даты_карточек", {})
    for о in з.предложения.values():
        выбрать_дату(о, даты_карточек.get(о.владелец) if о.источник == ИСТ_КАРТОЧКИ else None)
    сообщение = з.посчитать_роли()
    поступило, обработано = [], []
    вне = collections.Counter()
    до_окна_обработаны = collections.Counter()
    for о in з.предложения.values():
        где = окно.где(о.дата_поступления)
        if где == В_ОКНЕ:
            поступило.append(о)
        else:
            вне[(о.откуда_дата, где)] += 1
        if окно.содержит_момент(о.обработан):
            обработано.append(о)
            if где == ДО_ОКНА:
                до_окна_обработаны[о.откуда_дата] += 1
    return Итог(окно, поступило, обработано, вне, до_окна_обработаны, з, сообщение)


def своды_по_источнику(предложения, окно, реестр) -> dict[str, Свод]:
    своды = {КОРОТКО[и]: Свод() for и in ИСТОЧНИКИ}
    своды["всего"] = Свод()
    for о in предложения:
        своды[КОРОТКО[о.источник]].добавить(о, окно, реестр)
        своды["всего"].добавить(о, окно, реестр)
    return своды


def своды_по_дням(предложения, окно, реестр, день_предложения) -> dict[str, Свод]:
    группы = группы_дней(окно)
    своды = {метка: Свод() for метка, _, _ in группы}
    своды["всего"] = Свод()
    for о in предложения:
        д = день_предложения(о)
        for метка, с, по in группы:
            if д is not None and с <= д <= по:
                своды[метка].добавить(о, окно, реестр)
                break
        своды["всего"].добавить(о, окно, реестр)
    return своды


def печать_расшифровки(своды: dict[str, Свод], замер: Замер) -> None:
    print("\n  РАСШИФРОВАНО (файл дал хотя бы одну позицию; цена — там, где она пишется по устройству кода):")
    print(f"    {'источник':12s} {'файлов':>7s} {'с позициями':>18s} {'с ценой':>24s}")
    for ист in ИСТОЧНИКИ:
        с = своды[КОРОТКО[ист]]
        if ЦЕНА_ПИШЕТСЯ[ист]:
            цена = (f"{с.пишущих_с_ценой} из {с.пишущих}"
                    + (f" ({100 * с.пишущих_с_ценой / с.пишущих:.1f} %)" if с.пишущих else ""))
        else:
            цена = "— не пишется"
        print(f"    {КОРОТКО[ист]:12s} {с.файлов:>7d} {доля(с.с_позициями, с.файлов):>18s} {цена:>24s}")
    if своды[КОРОТКО[ИСТ_ПИСЬМА]].файлов and not своды[КОРОТКО[ИСТ_ПИСЬМА]].строк_цены:
        print("    письма: цены писем пишутся только со входом MAIL_PRICES прогона разбора писем"
              " (library-mail.yml), своим потоком «письмо поставщика»")
    print("    сделки: цены из полей сделки не пишутся по устройству кода (indexer.цены_файла)")
    всего = своды["всего"]
    if всего.почему_нет:
        print("\n  ПОЧЕМУ НЕ РАСШИФРОВАНО — ни одной позиции (источник · статус · причина · файлов):")
        for (ист, статус, причина), n in sorted(всего.почему_нет.items(), key=lambda x: (-x[1], x[0]))[:30]:
            print(f"    {КОРОТКО[ист]:9s} {статус[:18]:18s} {причина[:60]:60s} {n:>6d}")
    if всего.немые:
        print("\n  ПОЗИЦИИ ЕСТЬ, ЦЕНЫ НЕТ (карточки и письма; путь разбора · шапка · файлов):")
        for (путь, шапка), n in sorted(всего.немые.items(), key=lambda x: (-x[1], x[0])):
            print(f"    {путь[:18]:18s} шапка {шапка:12s} {n:>6d}")
        print("    Цена ищется в строке заголовков таблицы: путь «текст» и таблица без шапки"
              " цену почти не дают (scripts/quote_parse_funnel.py).")


def печать_ролей(своды: dict[str, Свод], сообщение: str, посчитаны: bool) -> None:
    print(f"\n  РОЛИ ПРЕДЛОЖЕНИЙ (library/offer_role.py) — {сообщение}")
    if not посчитаны:
        return
    from library import offer_role
    print(f"    {'источник':12s}" + "".join(f"{р:>16s}" for р in offer_role.РОЛИ))
    for ист in (ИСТ_КАРТОЧКИ, ИСТ_ПИСЬМА):
        с = своды[КОРОТКО[ист]]
        print(f"    {КОРОТКО[ист]:12s}" + "".join(f"{с.роли[р]:>16d}" for р in offer_role.РОЛИ))
    причины = своды["всего"].причины_ролей
    if причины:
        print("    причины (роль · почему · предложений):")
        for (роль, почему), n in sorted(причины.items(), key=lambda x: (-x[1], x[0]))[:15]:
            print(f"      {роль:14s} {str(почему)[:44]:44s} {n:>6d}")


def печать(итог: Итог, сообщение_ключа: str) -> None:
    о, з = итог.окно, итог.замер
    реестр = з.компании_реестра
    print(f"=== ПРЕДЛОЖЕНИЯ ПОСТАВЩИКОВ: окно {о.с.isoformat()} … {о.по.isoformat()}"
          f" (дни UTC, включительно, {о.дней} дн.) ===")
    print("Предложение — файл стороны поставщика: вложение карточки запроса (поля КП),"
          " письмо поставщика (вложение или тело), файл «поставщик» в поле сделки.")
    for т in з.оговорки:
        print(f"    оговорка: {т}")
    print(f"предложений найдено: {len(з.предложения)} · частей чтения по {ПОРЦИЯ} файлов: {з.порций}"
          + (f" · файлов «поле запроса» вне полей КП (не считаются): {з.вне_полей_кп}" if з.вне_полей_кп else "")
          + (f" · полей сделки без стороны, чьё название — не поставщика: {з.не_поставщика}"
             if з.не_поставщика else "")
          + (f" · файлов со строкой цены, но без записи lib_files: {з.без_записи_файла}"
             if з.без_записи_файла else ""))

    # СРЕЗ 1
    print("\n" + "─" * 100)
    print(f"СРЕЗ 1. ПОСТУПИЛО В ОКНЕ — дата предложения в окне: {len(итог.поступило)} файлов")
    print("  Дата поступления пишется ТОЛЬКО в строки цены (lib_prices.price_date). У файла без цены даты")
    print("  в базе нет (кроме даты карточки по другому её файлу), поэтому срез почти целиком из")
    print("  расшифрованных, и доля «с ценой» в нём завышена ПО ПОСТРОЕНИЮ. О расшифровке — срез 2.")
    print("  «Дата в самом КП» — день составления, «карточка создана» — нижняя граница: пришло не раньше.")
    if итог.вне_окна_дат:
        print("  вне среза (откуда дата · где относительно окна · файлов):")
        for (откуда, где), n in sorted(итог.вне_окна_дат.items(), key=lambda x: (ОТКУДА_ДАТА.index(x[0][0]), x[0][1])):
            print(f"    {откуда[:34]:34s} {где:12s} {n:>7d}")
    if итог.до_окна_обработаны:
        print("  из них дата ДО окна, а обработаны нами в окне — могли и прийти в окне, если дата — КП или")
        print("  карточки (это не дата прихода):")
        for откуда, n in sorted(итог.до_окна_обработаны.items(), key=lambda x: ОТКУДА_ДАТА.index(x[0])):
            print(f"    {откуда[:34]:34s} {n:>7d}")
    своды_1 = своды_по_источнику(итог.поступило, о, реестр)
    печать_таблицы("  ПО ИСТОЧНИКУ:", своды_1, "поступило")
    печать_таблицы("  ПО ДНЮ ПОСТУПЛЕНИЯ (UTC):",
                   своды_по_дням(итог.поступило, о, реестр, lambda x: x.дата_поступления), "поступило")
    печать_ролей(своды_1, итог.сообщение_роли, з.роли_посчитаны)

    # СРЕЗ 2
    print("\n" + "─" * 100)
    print(f"СРЕЗ 2. ОБРАБОТАНО НАМИ В ОКНЕ — lib_files.processed_at в окне: {len(итог.обработано)} файлов")
    print("  processed_at — ПОСЛЕДНЯЯ обработка: переразбор, повтор «не скачался» и распознавание скана")
    print("  её переписывают. «Впервые» и «повторно» — по самой ранней строке спроса файла (lib_demand")
    print("  created_at; переразбор старые строки не удаляет, а помечает). У файла без строк не установить.")
    своды_2 = своды_по_источнику(итог.обработано, о, реестр)
    печать_таблицы("  ПО ИСТОЧНИКУ:", своды_2, "обработано")
    печать_таблицы("  ПО ДНЮ ОБРАБОТКИ (UTC):",
                   своды_по_дням(итог.обработано, о, реестр,
                                 lambda x: x.обработан.astimezone(UTC).date() if x.обработан else None),
                   "обработано")
    печать_расшифровки(своды_2, з)
    печать_ролей(своды_2, итог.сообщение_роли, з.роли_посчитаны)
    print("\n" + "─" * 100)
    print(сообщение_ключа)


# ── Подробная часть ──────────────────────────────────────────────────────────

def подробности(итог: Итог) -> dict:
    """Всё, что журналу нельзя: номера, компании, бренды, строки цены. Только для
    шифрования. Суммы — без пересчёта валют и без домножения цены на количество
    (CLAUDE.md, «Числа в выгрузках владельцу»)."""
    о, з = итог.окно, итог.замер
    поступило = {x.file_id for x in итог.поступило}
    обработано = {x.file_id for x in итог.обработано}
    записи = []
    for п in sorted(з.предложения.values(), key=lambda x: (x.источник, str(x.владелец), x.file_id)):
        if п.file_id not in поступило and п.file_id not in обработано:
            continue
        компании = [{"ключ_портала": к,
                     "реестр": з.компании_реестра.get(к, (None, None))[0],
                     "имя": з.компании_реестра.get(к, (None, None))[1]} for к in sorted(п.компании)]
        записи.append({
            "file_id": п.file_id,
            "источник": п.источник,
            "карточка": п.владелец if п.источник == ИСТ_КАРТОЧКИ else None,
            "сделка": п.владелец if п.источник == ИСТ_СДЕЛКИ else None,
            "владелец_письма": п.владелец if п.источник == ИСТ_ПИСЬМА else None,
            "тело_письма": п.тело_письма,
            "поле": ПОЛЯ_КП.get(п.поле or "", п.поле),
            "срезы": [s for s, есть in (("поступило в окне", п.file_id in поступило),
                                        ("обработано в окне", п.file_id in обработано)) if есть],
            "поступило": {"дата": п.дата_поступления.isoformat() if п.дата_поступления else None,
                          "откуда": п.откуда_дата, "по_другому_файлу_карточки": п.дата_по_карточке,
                          "относительно_окна": о.где(п.дата_поступления)},
            "обработано": {"последний_раз": п.обработан.isoformat() if п.обработан else None,
                           "первая_строка_спроса": п.первые_строки.isoformat() if п.первые_строки else None,
                           "первичность": п.первичность(о)},
            "компании": компании,
            "статус": п.статус,
            "причина": п.причина,
            "вид": п.вид,
            "позиций": п.позиций,
            "строк_цены": п.строк_цены,
            "валюты": dict(п.валюты),
            "суммы": {str(в): {"строк": s["строк"],
                               "итог_по_КП": _число(s["итог_по_КП"]),
                               "строк_с_итогом": s["строк_с_итогом"],
                               "сумма_цен_за_единицу": _число(s["сумма_цен_за_единицу"])}
                      for в, s in п.суммы.items()},
            "бренды": sorted(п.бренды_файла, key=бренд_ключ)[:БРЕНДОВ_В_ПОДРОБНОСТЯХ],
            "бренды_карточки_СП176": sorted(п.бренды_карточки)[:БРЕНДОВ_В_ПОДРОБНОСТЯХ],
            "роль": п.роль,
            "строки_цены": п.строки[:СТРОК_ЦЕНЫ_В_ПОДРОБНОСТЯХ],
        })
    return {
        "окно": {"с": о.с.isoformat(), "по": о.по.isoformat(), "дней": о.дней, "пояс": "UTC",
                 "границы": "включительно"},
        "собрано": datetime.now(UTC).isoformat(timespec="seconds"),
        "оговорки": [
            "поступило — дата предложения, какую база знает: она пишется только в строки цены;"
            " «карточка создана» — нижняя граница, «дата в самом КП» — день составления",
            "обработано — последняя обработка файла (переразбор и распознавание её переписывают)",
            "итог_по_КП — сумма колонки «сумма» строк, где она названа в КП; цена на количество не"
            " домножалась, валюты не пересчитывались; сумма_цен_за_единицу — простая сумма цен,"
            " не стоимость закупки",
            *з.оговорки,
        ],
        "предложений": len(записи),
        "предложения": записи,
    }


def записать_подробности(данные: dict, путь: str) -> None:
    """Файл с правами 600 — до шифрования его не должен читать никто, кроме шага."""
    fd = os.open(путь, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(данные, f, ensure_ascii=False, indent=1, default=str)


def выполнить(cur, окно: Окно, env) -> int:
    """Замер на открытом курсоре: печать агрегатов и, со входом ключа, подробный файл."""
    писать, сообщение_ключа = проверить_ключ(env.get("PUBKEY_B64"))
    итог = собрать(cur, окно, подробно=писать)
    if итог is None:
        return 3
    if писать:
        путь = str(env.get("DETAIL_OUT") or ПУТЬ_ПОДРОБНОСТЕЙ)
        данные = подробности(итог)
        записать_подробности(данные, путь)
        сообщение_ключа += f" — предложений в файле: {данные['предложений']}"
    печать(итог, сообщение_ключа)
    return 0


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — мерить нечего")
        return 2
    try:
        окно = окно_из_env(os.environ)
    except ValueError as e:
        print(f"::error::{e}")
        return 2
    import psycopg2

    т0 = time.monotonic()
    # Таймаут — в строке подключения, а не SET (CLAUDE.md, правило 9).
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c work_mem=64MB")
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            код = выполнить(cur, окно, os.environ)
    finally:
        conn.close()
    print(f"\nвремя: {time.monotonic() - т0:.0f} с")
    return код


if __name__ == "__main__":
    raise SystemExit(main())
