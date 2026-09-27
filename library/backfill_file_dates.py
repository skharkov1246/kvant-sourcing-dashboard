#!/usr/bin/env python3
"""Досчёт даты прихода файлов стороны поставщика — lib_files.source_created_at.

ЗАЧЕМ. Недельный свод предложений (scripts/weekly_offers.py) отвечает на вопрос
«какие КП пришли за неделю», а у lib_files даты прихода не было: processed_at
переписывает каждая обработка. Новые файлы получают first_seen_at (когда мы их
увидели) и, если это даром, source_created_at (когда они появились у
источника) — library/supabase/file_dates_schema.sql, indexer.дата_у_источника.
Здесь — то же для уже записанных файлов последних DAYS дней.

ЧТО БЕРЁТ. Файлы, которые недельный свод считает предложениями поставщика
(weekly_offers.источник_файла — одно определение на свод и досчёт): карточки
запросов СП-166 (поля КП), письма поставщиков (тела и вложения), поля сделок с
папкой «предложение поставщика». Только с пустой датой у источника и только
обработанные за DAYS дней (processed_at, ocr_at или first_seen_at в окне):
обработка не бывает раньше прихода, а processed_at только растёт, поэтому
файл, пришедший за DAYS дней, в выборку попадает наверняка. Выборка шире
нужного (переразобранные старые файлы в неё тоже входят), но не уже.

ОТКУДА ДАТА.
  письма — CREATED дела-письма: crm.activity.list по номерам писем пачкой по
    50, start=-1 (номер — в поле «письмо <ID>», mail_source.ссылки_письма).
    Точная; источник «письмо: создано в CRM». Одна на все файлы письма.
  карточки и сделки — заголовок Last-Modified ответа закачки файла. Файловое
    поле CRM даты загрузки не отдаёт, а ответ закачки — единственное место, где
    она может быть. Карточка или сделка читается crm.item.list пачкой по 50
    («@id», start=-1) ради urlMachine, файл — одним запросом без тела
    (indexer.заголовки_адреса). Что заголовок значит у портала, НЕ ПРОВЕРЕНО —
    поэтому досчёт сначала мерит, и мерит с перекрёстными проверками.

ПЕРЕКРЁСТНЫЕ ПРОВЕРКИ ЗАГОЛОВКА (журнал — только счётчики, правило 17):
  · indexer.дата_заголовка — нет заголовка, равен времени ответа (сервер ставит
    «сейчас»), раньше 2000 года, позже ответа, раньше создания карточки или
    сделки (дата другой копии того же содержимого);
  · позже первой обработки файла нами (processed_at, ocr_at, first_seen_at,
    самая ранняя строка спроса) больше чем на ДОПУСК — прийти позже, чем мы его
    разобрали, файл не мог; такой заголовок говорит не о загрузке;
  · Content-Length ответа не равен size_bytes записи — ответ не о том файле.
  Сверх того — замер без записи: у файлов полей CRM номер вложения растёт со
  временем загрузки (один ряд номеров на портал). Если заголовок значит
  «загружен», то по возрастанию номера даты не идут назад; пар соседних по
  номеру, где дата идёт назад больше чем на час, печатается число. Много таких
  пар — заголовок значит «переложен в хранилище», и писать его нельзя.

ГЕЙТЫ — запись части отменяется сама (правило 3):
  · портал отдал меньше MIN_FOUND владельцев из запрошенных — сбой чтения, а не
    удалённые карточки;
  · противоречий (позже первой обработки, размер не совпал) больше
    ПОРОГ_ПРОТИВОРЕЧИЙ от файлов, у которых заголовок был, — заголовок части не
    значит «загружен», и её даты не пишутся вовсе.

ВХОЛОСТУЮ ПО УМОЛЧАНИЮ (правило 3). Холостой прогон читает портал и печатает
замер; соединение с базой у него ТОЛЬКО ДЛЯ ЧТЕНИЯ — записать он не может даже
по ошибке. Запись — APPLY=1, и ТОЛЬКО в пустую дату: записанное не
переписывается, «стало хуже» — ноль по построению, и это печатается (правило 0).
Каждая запись — с ключом прогона source_date_run (правило 6): откат снимает
ровно свои три колонки, и файл возвращается к прежнему виду.

БЮДЖЕТ ПОРТАЛА (CLAUDE.md, «Битрикс не перегружать»). Клиент — общий
indexer.bx и очередь indexer.клиент() (BITRIX_RPS, BITRIX_PARALLEL). Части идут
ПОДРЯД в одном процессе, поэтому BITRIX_PARALLEL=1. Запросов: по одному на 50
писем, на 50 карточек или сделок и по одному на файл карточки или сделки. План
с числом запросов и временем печатается ДО обращения к порталу; PLAN=1 — только
план, портал не читается вовсе. Прикидка: 2 000 файлов карточек — около 2 040
запросов, около 28 минут при 1,2 запроса в секунду.

    SUPABASE_DB_URL=… PLAN=1 python library/backfill_file_dates.py
    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… LIMIT=200 python library/backfill_file_dates.py
    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… APPLY=1 python library/backfill_file_dates.py
    SUPABASE_DB_URL=… ROLLBACK=fd-123 python library/backfill_file_dates.py

Входы: DAYS (60), SHARDS (10), SOURCES («mail,rfq,deals»), LIMIT (0 — все),
APPLY, PLAN, ROLLBACK, RUN_ID.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import indexer  # noqa: E402  (клиент портала, суд о заголовке, заголовки без тела)
import weekly_offers  # noqa: E402  (scripts/ на sys.path у indexer: что такое предложение)

ПАЧКА = 50
#: Доля владельцев (писем, карточек, сделок), которую портал обязан отдать.
MIN_FOUND = 0.5
#: Доля противоречий среди файлов с заголовком, выше которой даты части не пишутся.
ПОРОГ_ПРОТИВОРЕЧИЙ = 0.05
#: Допуск сверки с первой обработкой: часы сервера портала и базы разные.
ДОПУСК = timedelta(hours=1)
#: Назад по номеру больше этого — пара считается нарушающей монотонность.
НАЗАД = timedelta(hours=1)

ПИСЬМА, КАРТОЧКИ, СДЕЛКИ = "mail", "rfq", "deals"
ИСТОЧНИКИ = (ПИСЬМА, КАРТОЧКИ, СДЕЛКИ)
#: Источник недельного свода → источник досчёта.
ПО_СВОДУ = {weekly_offers.ИСТ_ПИСЬМА: ПИСЬМА, weekly_offers.ИСТ_КАРТОЧКИ: КАРТОЧКИ,
            weekly_offers.ИСТ_СДЕЛКИ: СДЕЛКИ}
СУЩНОСТЬ = {КАРТОЧКИ: indexer.SPA_RFQ, СДЕЛКИ: 2}
ПОЛЕ_ПИСЬМА = re.compile(r"^письмо (\d+)$")
КОЛОНКИ_МИГРАЦИИ = ("source_created_at", "source_date_src", "source_date_run")

# Причины, по которым дата не взята, — константы кода (правило 17).
НЕ_ПРЕДЛОЖЕНИЕ = "не предложение поставщика (weekly_offers)"
НЕТ_НОМЕРА_ПИСЬМА = "у письма нет номера в поле"
НЕТ_НОМЕРА_ВЛАДЕЛЬЦА = "у файла нет номера карточки или сделки"
НЕ_СКАЧИВАЛСЯ = "файл не скачался при разборе — заголовок не спрашивался"
ВЛАДЕЛЬЦА_НЕТ = "портал не отдал письмо, карточку или сделку"
ФАЙЛА_НЕТ = "файла в поле больше нет"
ССЫЛКИ_НЕТ = "у вложения нет ссылки"
ДАТЫ_ПИСЬМА_НЕТ = "у письма нет правдоподобной даты"
ПОЗЖЕ_ОБРАБОТКИ = "позже первой обработки нами"
РАЗМЕР_НЕ_ТОТ = "Content-Length не равен size_bytes"
ПРОТИВОРЕЧИЯ = (ПОЗЖЕ_ОБРАБОТКИ, РАЗМЕР_НЕ_ТОТ)

ЗАПИСЬ = """
update lib_files f
   set source_created_at = v.d::timestamptz, source_date_src = v.src, source_date_run = %s
  from (values %s) as v(fid, d, src)
 where f.file_id = v.fid and f.source_created_at is null"""

ОТКАТ = """
update lib_files set source_created_at = null, source_date_src = null, source_date_run = null
 where source_date_run = %s"""

КОЛОНКИ_SQL = """
select table_name, column_name from information_schema.columns
 where table_schema = any(current_schemas(false))
   and table_name in ('lib_files', 'lib_demand')"""


@dataclass
class Файл:
    file_id: str
    источник: str            # ПИСЬМА | КАРТОЧКИ | СДЕЛКИ
    владелец: str            # номер письма, карточки или сделки
    поле: str | None
    статус: str | None
    размер: int | None
    первая_обработка: datetime | None = None


def включено(имя: str) -> bool:
    return os.environ.get(имя, "").strip().lower() in ("1", "true", "yes")


def число(имя: str, умолчание: int) -> int:
    т = os.environ.get(имя, "").strip()
    try:
        return int(т) if т else умолчание
    except ValueError:
        raise SystemExit(f"::error::{имя}={т!r}: нужно целое число") from None


def источники_входа(значение: str | None) -> tuple[str, ...]:
    """SOURCES → закрытый список источников; неизвестное — отказ словами."""
    т = [x.strip() for x in (значение or "").split(",") if x.strip()] or list(ИСТОЧНИКИ)
    чужие = [x for x in т if x not in ИСТОЧНИКИ]
    if чужие:
        raise SystemExit(f"::error::SOURCES: неизвестные источники {чужие}; допустимо "
                         + ", ".join(ИСТОЧНИКИ))
    return tuple(x for x in ИСТОЧНИКИ if x in т)


# ── База ─────────────────────────────────────────────────────────────────────

def колонки(cur) -> dict[str, set[str]]:
    cur.execute(КОЛОНКИ_SQL)
    out: dict[str, set[str]] = defaultdict(set)
    for т, к in cur.fetchall():
        out[т].add(к)
    return out


def _к(есть: set[str], имя: str, тип: str) -> str:
    return f"f.{имя}" if имя in есть else f"null::{тип}"


def кандидаты(cur, есть: dict[str, set[str]], дней: int, источники: tuple[str, ...],
              сейчас: datetime) -> tuple[list[Файл], Counter]:
    """Файлы без даты у источника, обработанные за `дней`, и отсев с причинами."""
    ф = есть["lib_files"]
    когда = ["f.processed_at >= %(с)s"]
    if "ocr_at" in ф:
        когда.append("f.ocr_at >= %(с)s")
    if "first_seen_at" in ф:
        когда.append("f.first_seen_at >= %(с)s")
    cur.execute(
        f"select f.file_id, f.origin, f.field, f.deal_id, {_к(ф, 'side', 'text')},"
        f" {_к(ф, 'field_title', 'text')}, f.status, f.size_bytes, f.processed_at,"
        f" {_к(ф, 'ocr_at', 'timestamptz')}, {_к(ф, 'first_seen_at', 'timestamptz')}"
        "  from lib_files f"
        " where f.source_created_at is null"
        "   and f.origin in (%(запрос)s, %(письмо)s, %(сделка)s)"
        f"   and ({' or '.join(когда)})",
        {"с": сейчас - timedelta(days=дней), "запрос": weekly_offers.ORIGIN_ЗАПРОСА,
         "письмо": weekly_offers.ORIGIN_ПИСЬМА, "сделка": weekly_offers.ORIGIN_СДЕЛКИ})
    файлы: list[Файл] = []
    отсев: Counter = Counter()
    for fid, origin, поле, владелец, side, заголовок, статус, размер, обр, ocr, впервые in cur.fetchall():
        ист = ПО_СВОДУ.get(weekly_offers.источник_файла(origin, поле, side, заголовок))
        if ист is None:
            отсев[НЕ_ПРЕДЛОЖЕНИЕ] += 1
            continue
        if ист not in источники:
            continue
        if ист == ПИСЬМА:
            м = ПОЛЕ_ПИСЬМА.match(str(поле or ""))
            if not м:
                отсев[НЕТ_НОМЕРА_ПИСЬМА] += 1
                continue
            владелец = м.group(1)
        elif not str(владелец or "").isdigit():
            отсев[НЕТ_НОМЕРА_ВЛАДЕЛЬЦА] += 1
            continue
        elif статус == "не скачался":
            # Заголовок не дастся тому, кто не дался разбору: запрос впустую.
            отсев[НЕ_СКАЧИВАЛСЯ] += 1
            continue
        моменты = [x for x in (обр, ocr, впервые) if x is not None]
        файлы.append(Файл(str(fid), ист, str(владелец or ""), поле, статус,
                          int(размер) if размер is not None else None,
                          min(моменты) if моменты else None))
    return файлы, отсев


def дополнить_спросом(cur, есть: dict[str, set[str]], файлы: list[Файл]) -> None:
    """Первая обработка — ещё и самая ранняя строка спроса файла, если она раньше."""
    if not {"source_file", "created_at"} <= есть.get("lib_demand", set()) or not файлы:
        return
    по_id = {f.file_id: f for f in файлы}
    ключи = sorted(по_id)
    for i in range(0, len(ключи), 500):
        cur.execute("select source_file, min(created_at) from lib_demand"
                    " where source_file = any(%s) group by 1", (ключи[i:i + 500],))
        for fid, первая in cur.fetchall():
            f = по_id[fid]
            if первая is not None and (f.первая_обработка is None or первая < f.первая_обработка):
                f.первая_обработка = первая


def выборка(файлы: list[Файл], предел: int) -> list[Файл]:
    """Не больше `предел` файлов — вразброс по хешу номера, а не первые подряд:
    замер монотонности и лагов нужен по всему окну, а не по его краю."""
    if предел <= 0 or len(файлы) <= предел:
        return файлы
    return sorted(файлы, key=lambda f: hashlib.sha256(f.file_id.encode()).hexdigest())[:предел]


def части(файлы: list[Файл], shards: int) -> list[list[Файл]]:
    """Смежные куски по (источник, владелец): владелец — ровно в одной части, и
    его письмо, карточка или сделка читается один раз. Пустых частей нет."""
    по_владельцу: dict[tuple[str, str], list[Файл]] = defaultdict(list)
    for f in файлы:
        по_владельцу[(f.источник, f.владелец)].append(f)
    ключи = sorted(по_владельцу, key=lambda к: (ИСТОЧНИКИ.index(к[0]),
                                               int(к[1]) if к[1].isdigit() else 0, к[1]))
    if not ключи:
        return []
    shards = max(1, min(shards, len(ключи)))
    шаг = -(-len(ключи) // shards)
    return [[f for к in ключи[i:i + шаг] for f in по_владельцу[к]]
            for i in range(0, len(ключи), шаг)]


def запросов(часть: list[Файл]) -> int:
    """Запросов к порталу на часть: по одному на пачку владельцев и на файл CRM."""
    n = 0
    for ист in ИСТОЧНИКИ:
        владельцы = {f.владелец for f in часть if f.источник == ист}
        n += -(-len(владельцы) // ПАЧКА)
        if ист != ПИСЬМА:
            n += sum(1 for f in часть if f.источник == ист)
    return n


# ── Портал ───────────────────────────────────────────────────────────────────

def _элементы(j: dict) -> list:
    res = (j or {}).get("result")
    return ((res.get("items") if isinstance(res, dict) else res) or [])


def прочитать_письма(номера: list[str], bx) -> dict[str, str | None]:
    """Номер письма → CREATED. Не отданные порталом — не в ответе."""
    out: dict[str, str | None] = {}
    for i in range(0, len(номера), ПАЧКА):
        j = bx("crm.activity.list", {"filter": {"ID": [int(x) for x in номера[i:i + ПАЧКА]]},
                                     "select": ["ID", "CREATED"], "start": -1})
        for x in _элементы(j):
            out[str(x.get("ID"))] = x.get("CREATED")
    return out


def прочитать_владельцев(ист: str, номера: list[str], поля: list[str], bx
                         ) -> tuple[set[str], dict[str, tuple[dict, str | None]]]:
    """(отданные порталом номера, file_id → (объект вложения, createdTime владельца))."""
    отдано: set[str] = set()
    вложения: dict[str, tuple[dict, str | None]] = {}
    for i in range(0, len(номера), ПАЧКА):
        j = bx("crm.item.list", {"entityTypeId": СУЩНОСТЬ[ист],
                                 "filter": {"@id": [int(x) for x in номера[i:i + ПАЧКА]]},
                                 "select": ["id", "createdTime", *поля], "start": -1})
        for x in _элементы(j):
            отдано.add(str(x.get("id")))
            for поле in поля:
                v = x.get(поле)
                for fo in (v if isinstance(v, list) else [v] if v else []):
                    if isinstance(fo, dict) and (fo.get("id") or fo.get("ID")):
                        вложения.setdefault(str(fo.get("id") or fo.get("ID")),
                                            (fo, x.get("createdTime")))
    return отдано, вложения


def ссылка(fo: dict) -> str | None:
    for ключ in ("urlMachine", "downloadUrl", "url", "URL_MACHINE", "DOWNLOAD_URL"):
        if fo.get(ключ):
            return str(fo[ключ])
    return None


# ── Суд ──────────────────────────────────────────────────────────────────────

@dataclass
class Итог_части:
    даты: dict                   # file_id → (момент, источник даты)
    счёт: Counter
    причины: Counter             # почему даты нет — константы кода
    провал: list
    заголовочные: list           # (номер файла CRM, момент) — для монотонности
    лаг_владельца_ч: list        # часы от создания карточки/сделки до заголовка
    лаг_обработки_ч: list        # часы от заголовка или письма до первой обработки


def позже_обработки(м: datetime, f: Файл) -> bool:
    return f.первая_обработка is not None and м > f.первая_обработка + ДОПУСК


def часы(а: datetime, б: datetime) -> float:
    return (б - а).total_seconds() / 3600


def разобрать_часть(часть: list[Файл], bx, заголовки_адреса) -> Итог_части:
    """Дата каждого файла части и почему её нет — портал через bx и
    заголовки_адреса (подставляются тестом), база не трогается."""
    и = Итог_части({}, Counter(), Counter(), [], [], [], [])
    # с_датой — файлы, у которых дата-кандидат есть (письмо с правдоподобной
    # датой, заголовок, прошедший indexer.дата_заголовка): от них считается
    # доля противоречий.
    с_датой = противоречий = 0
    запрошено = отдано_всего = 0
    for ист in ИСТОЧНИКИ:
        свои = [f for f in часть if f.источник == ист]
        if not свои:
            continue
        номера = sorted({f.владелец for f in свои}, key=lambda x: (len(x), x))
        запрошено += len(номера)
        и.счёт[f"{ист}: файлов"] += len(свои)
        if ист == ПИСЬМА:
            письма = прочитать_письма(номера, bx)
            отдано_всего += len(письма)
            for f in свои:
                if f.владелец not in письма:
                    и.причины[(ист, ВЛАДЕЛЬЦА_НЕТ)] += 1
                    continue
                м = indexer.дата_письма(письма[f.владелец])
                if м is None:
                    и.причины[(ист, ДАТЫ_ПИСЬМА_НЕТ)] += 1
                    continue
                с_датой += 1
                if позже_обработки(м, f):
                    и.причины[(ист, ПОЗЖЕ_ОБРАБОТКИ)] += 1
                    противоречий += 1
                    continue
                if f.первая_обработка is not None:
                    и.лаг_обработки_ч.append(часы(м, f.первая_обработка))
                и.даты[f.file_id] = (м, indexer.ИСТ_ДАТЫ_ПИСЬМО)
            continue
        поля = sorted({f.поле for f in свои if f.поле})
        отдано, вложения = прочитать_владельцев(ист, номера, поля, bx)
        отдано_всего += len(отдано)
        for f in свои:
            if f.владелец not in отдано:
                и.причины[(ист, ВЛАДЕЛЬЦА_НЕТ)] += 1
                continue
            if f.file_id not in вложения:
                и.причины[(ист, ФАЙЛА_НЕТ)] += 1
                continue
            fo, создан = вложения[f.file_id]
            адрес = ссылка(fo)
            if not адрес:
                и.причины[(ист, ССЫЛКИ_НЕТ)] += 1
                continue
            заголовки, почему = заголовки_адреса(адрес)
            if заголовки is None:
                # Причина — код ответа или имя класса исключения
                # (indexer.заголовки_адреса), адреса в ней нет.
                и.причины[(ист, "заголовки не получены: " + почему[:50])] += 1
                continue
            м, суд = indexer.дата_заголовка(заголовки, создан)
            if м is None:
                и.причины[(ист, "заголовок: " + суд)] += 1
                continue
            с_датой += 1
            длина = {str(к).lower(): v for к, v in заголовки.items()}.get("content-length")
            if длина and f.размер is not None and str(длина).isdigit() and int(длина) != f.размер:
                и.причины[(ист, РАЗМЕР_НЕ_ТОТ)] += 1
                противоречий += 1
                continue
            if позже_обработки(м, f):
                и.причины[(ист, ПОЗЖЕ_ОБРАБОТКИ)] += 1
                противоречий += 1
                continue
            владелец = indexer.момент(создан)
            if владелец is not None:
                и.лаг_владельца_ч.append(часы(владелец, м))
            if f.первая_обработка is not None:
                и.лаг_обработки_ч.append(часы(м, f.первая_обработка))
            if f.file_id.isdigit():
                и.заголовочные.append((int(f.file_id), м))
            и.даты[f.file_id] = (м, indexer.ИСТ_ДАТЫ_ЗАКАЧКА)
    и.счёт["владельцев запрошено"] = запрошено
    и.счёт["владельцев отдал портал"] = отдано_всего
    и.счёт["с датой-кандидатом"] = с_датой
    и.счёт["противоречий"] = противоречий
    и.счёт["дат к записи"] = len(и.даты)
    и.счёт["станет хуже"] = 0                 # пишется только в пустую дату
    if запрошено and отдано_всего < MIN_FOUND * запрошено:
        и.провал.append(f"портал отдал {отдано_всего} владельцев из {запрошено} — похоже на сбой чтения")
    if с_датой and противоречий > ПОРОГ_ПРОТИВОРЕЧИЙ * с_датой:
        и.провал.append(f"противоречий {противоречий} из {с_датой} (порог "
                        f"{ПОРОГ_ПРОТИВОРЕЧИЙ:.0%}) — дата части не значит «пришёл»")
    return и


def монотонность(пары: list[tuple[int, datetime]]) -> tuple[int, int]:
    """(соседних по номеру пар, из них дата назад больше НАЗАД)."""
    по_номеру = sorted(пары)
    назад = sum(1 for (_, а), (_, б) in zip(по_номеру, по_номеру[1:]) if б < а - НАЗАД)
    return max(0, len(по_номеру) - 1), назад


def квантили(значения: list[float]) -> str:
    """Медиана и крайние десятые доли — числа без привязки к файлам (правило 17)."""
    if not значения:
        return "нет"
    с = sorted(значения)
    д = lambda q: с[min(len(с) - 1, int(q * (len(с) - 1)))]            # noqa: E731
    return f"n={len(с)} · 10 % {д(0.1):.1f} · медиана {median(с):.1f} · 90 % {д(0.9):.1f}"


def записать(cur, даты: dict, run_id: str, execute_values) -> int:
    """Одна часть — одним UPDATE … FROM (VALUES …), только в пустую дату."""
    к = ЗАПИСЬ.split("%s")
    assert len(к) == 3, "в ЗАПИСЬ ровно два места %s"
    запрос = cur.mogrify(к[0] + "%s", (run_id,)).decode() + к[1] + "%s" + к[2]
    строки = [(fid, м.isoformat(), src) for fid, (м, src) in sorted(даты.items())]
    execute_values(cur, запрос, строки, page_size=max(1, len(строки)))
    return cur.rowcount


# ── Прогон ───────────────────────────────────────────────────────────────────

def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("::error::нет SUPABASE_DB_URL")
        return 2
    import psycopg2
    import psycopg2.extras
    откат = os.environ.get("ROLLBACK", "").strip()
    писать = включено("APPLY")
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    try:
        if откат:
            with conn.cursor() as cur:
                cur.execute(ОТКАТ, (откат,))
                n = cur.rowcount
            conn.commit()
            print(f"✓ откат {откат}: дата у источника снята у файлов: {n}")
            return 0
        # ХОЛОСТОЙ — ТОЛЬКО ЧТЕНИЕ. Не дисциплина, а свойство соединения: любая
        # запись упадёт в базе, а не пройдёт молча.
        conn.set_session(readonly=not писать)
        return выполнить(conn, писать, psycopg2.extras.execute_values)
    finally:
        conn.close()


def выполнить(conn, писать: bool, execute_values, bx=None, заголовки_адреса=None,
              сейчас: datetime | None = None) -> int:
    """Весь досчёт на открытом соединении. bx и заголовки_адреса подставляет тест."""
    дней = число("DAYS", 60)
    shards = max(1, число("SHARDS", 10))
    предел = число("LIMIT", 0)
    источники = источники_входа(os.environ.get("SOURCES"))
    сейчас = сейчас or datetime.now(timezone.utc)
    run_id = os.environ.get("RUN_ID", "").strip() or \
        f"fd-{os.environ.get('GITHUB_RUN_ID') or int(сейчас.timestamp())}"
    with conn.cursor() as cur:
        есть = колонки(cur)
        нет = [к for к in КОЛОНКИ_МИГРАЦИИ if к not in есть["lib_files"]]
        if нет:
            print(f"::error::в lib_files нет колонок {', '.join(нет)} — примените "
                  "library/supabase/file_dates_schema.sql прогоном «ZIP base — apply DB "
                  "migrations» и повторите. Портал не читался.")
            return 2
        файлы, отсев = кандидаты(cur, есть, дней, источники, сейчас)
        всего = len(файлы)
        файлы = выборка(файлы, предел)
        дополнить_спросом(cur, есть, файлы)
    conn.commit()

    куски = части(файлы, shards)
    import bitrix_client
    rps, процессов = bitrix_client.бюджет_портала()
    интервал = bitrix_client.интервал_портала()
    план = sum(запросов(к) for к in куски)
    print(f"окно: {дней} дн. · источники: {', '.join(источники)} · режим: "
          + ("ЗАПИСЬ (только в пустую дату)" if писать else "холостой (соединение только для чтения)"))
    print(f"файлов без даты у источника: {всего}"
          + (f" · в выборке LIMIT={предел}: {len(файлы)}" if len(файлы) < всего else "")
          + " · " + " · ".join(f"{ист} {sum(1 for f in файлы if f.источник == ист)}" for ист in источники))
    if отсев:
        print("не берутся: " + " · ".join(f"{к} {v}" for к, v in sorted(отсев.items())))
    print(f"частей: {len(куски)} · запросов к порталу около {план}"
          f" · около {план * интервал / 60:.0f} мин: бюджет {rps:g}/с на портал,"
          f" процессов {процессов} (части идут подряд)", flush=True)
    if включено("PLAN"):
        print("PLAN=1: только план, портал не читался, база не менялась")
        return 0
    if not куски:
        print("нечего досчитывать")
        return 0
    if not os.environ.get("BITRIX_WEBHOOK_URL", "").strip() and bx is None:
        print("::error::нет BITRIX_WEBHOOK_URL")
        return 2
    bx = bx or indexer.bx
    заголовки_адреса = заголовки_адреса or indexer.заголовки_адреса

    итог: Counter = Counter()
    причины: Counter = Counter()
    по_источнику_даты: Counter = Counter()
    заголовочные: list = []
    лаг_владельца: list = []
    лаг_обработки: list = []
    упало = 0
    for i, кусок in enumerate(куски, 1):
        ч = разобрать_часть(кусок, bx, заголовки_адреса)
        итог.update(ч.счёт)
        причины.update(ч.причины)
        заголовочные += ч.заголовочные
        лаг_владельца += ч.лаг_владельца_ч
        лаг_обработки += ч.лаг_обработки_ч
        for _, src in ч.даты.values():
            по_источнику_даты[src] += 1
        print(f"часть {i} из {len(куски)}: " + " · ".join(f"{к} {v}" for к, v in ч.счёт.items()),
              flush=True)
        for п in ч.провал:
            print(f"::error::часть {i}: гейт не пройден: {п} — запись части отменена")
        if ч.провал:
            упало += 1
            continue
        if писать and ч.даты:
            with conn.cursor() as cur:
                итог["записано"] += записать(cur, ч.даты, run_id, execute_values)
            conn.commit()

    print("\nИТОГ: " + " · ".join(f"{к} {v}" for к, v in итог.items()))
    print("даты по источнику: " + (" · ".join(f"{к} {v}" for к, v in по_источнику_даты.most_common())
                                   or "нет"))
    if причины:
        print("почему даты нет (источник · причина · файлов):")
        for (ист, п), n in sorted(причины.items(), key=lambda x: (-x[1], x[0])):
            print(f"    {ист:6s} {п[:70]:70s} {n:>6d}")
    пар, назад = монотонность(заголовочные)
    print(f"монотонность заголовка по номеру файла: пар {пар}, дата назад больше часа — {назад}"
          + (f" ({100 * назад / пар:.1f} %)" if пар else "")
          + " · много — заголовок значит «переложен», а не «загружен»")
    print(f"часы от создания карточки или сделки до заголовка: {квантили(лаг_владельца)}")
    print(f"часы от даты у источника до первой обработки нами: {квантили(лаг_обработки)}")
    if упало:
        print(f"::warning::частей с непройденным гейтом: {упало} — их даты не записаны; "
              "повтор возьмёт то, что осталось пустым")
    if not писать:
        print("вхолостую: в базе ничего не изменено. Для записи APPLY=1")
    else:
        print(f"ключ прогона {run_id} · откат: ROLLBACK={run_id}")
    return 1 if упало else 0


if __name__ == "__main__":
    sys.exit(main())
