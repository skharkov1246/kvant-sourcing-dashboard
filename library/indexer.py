"""Сплошной разбор вложений Битрикса → библиотека рынков (Supabase).

Задача владельца: прочитать ВСЕ файлы, привязанные к сделкам, разобрать и
проиндексировать — это фундамент для машины коммерческих предложений. В карточках
Битрикса строк номенклатуры нет (проверено: штатный метод вернул ноль на 2926
сделках), вся номенклатура — в спецификациях-вложениях.

УСТРОЙСТВО. Работа делится на части (SHARDS × SHARD): каждая часть берёт свою
долю файлов по остатку от деления хеша. Разобранное отмечается в lib_files, поэтому
повторный запуск продолжает с места остановки, а не начинает заново. Файлы качаются
и разбираются в несколько потоков — последовательный обход 22 тысяч вложений идёт
часами.

КУДА ПИШЕМ. Только в Supabase, напрямую по SUPABASE_DB_URL. Ни в репозиторий, ни в
артефакты сборки ничего не попадает: репозиторий публичный, а артефакты открытого
репозитория может скачать кто угодно. В журнал прогона выводятся только агрегаты —
ни имён файлов, ни названий сделок, ни содержимого спецификаций.
"""
from __future__ import annotations

import hashlib
import io
import os
import random
import re
import sys
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests

_КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_КОРЕНЬ, "scripts"))
sys.path.insert(0, _КОРЕНЬ)          # bitrix_client лежит в корне
import docfilter  # noqa: E402  (после sys.path)
import offer_terms  # noqa: E402  (базис, оплата, сроки из КП)
import pdftable  # noqa: E402  (таблица из PDF по выравниванию)
import quotes  # noqa: E402  (цена из КП поставщика)
import price_store  # noqa: E402  (запись цены — одна на все разборы)
# Список полей КП держим в одном месте со всеми замерами котировок: два списка
# разошлись бы молча — разбирали бы одно, а считали другое.
from quote_coverage import ПОЛЕ_ЗАПРОСА, ПОЛЯ_КП  # noqa: E402
# Поле «поставщик» карточки запроса — то же, по которому считается отзывчивость.
from supplier_responsiveness import ПОЛЕ_ПОСТАВЩИКА, crm_id  # noqa: E402
from segments import SEGMENTS, classify, name_of  # noqa: E402

# Секреты читаются лениво: без них модуль всё равно импортируется — иначе его
# нельзя ни собрать py_compile, ни импортировать из тестов гейта.
BASE = os.environ.get("BITRIX_WEBHOOK_URL", "").rstrip("/")
DB = os.environ.get("SUPABASE_DB_URL", "")
SHARDS = int(os.environ.get("SHARDS", "1"))
SHARD = int(os.environ.get("SHARD", "0"))
WORKERS = int(os.environ.get("WORKERS", "12"))
DAYS = int(os.environ.get("DAYS", "365"))
LIMIT = int(os.environ.get("LIMIT", "0"))          # 0 — без ограничения
RETRY_FAILED = os.environ.get("RETRY_FAILED", "") not in ("", "0", "false")
# Ворота спецификации можно выключить без выката кода — на случай, если правило
# начнёт отбрасывать нужное. Выключение видно в журнале прогона.
SPECGATE = os.environ.get("SPECGATE", "1") not in ("", "0", "false")
# Откуда брать вложения: сделки (как было) или карточки запросов поставщикам.
# Замер 21.09.2026: в lib_files не было НИ ОДНОГО файла из полей КП — разбор до
# СП-166 никогда не доходил, а там 5 231 файл с ценами, и это единственное место,
# где цена вообще есть: на самой карточке сумма равна нулю у всех 21 865.
SOURCE = os.environ.get("SOURCE", "deals").strip().lower()
SPA_RFQ = 166
# Подпись источника строки. Раньше здесь всегда стояла «спецификация сделки» —
# и строки из КП поставщика ложились под чужим именем: спецификация говорит, что
# заказчик просит, котировка — что поставщик предлагает и почём.
ИСТОЧНИК_СТРОКИ = "котировка поставщика" if SOURCE == "rfq" else "спецификация сделки"
# БРЕНД С КАРТОЧКИ ЗАПРОСА. Цена сравнима только в разрезе «к чему это»: тот же
# подшипник дорог или дёшев в зависимости от машины и изготовителя. Поле на
# карточке есть давно — base/fetch_rfq.py его даже запрашивает, — но в таблицу
# оно не попадало вовсе. Многозначное: у запроса бывает несколько брендов.
ПОЛЕ_БРЕНДОВ = "ufCrm18Brands"
# Поток цен, по которому переразбор снимает свои прежние строки. Имя и вся
# запись живут в library/price_store.py — общем месте для обычного разбора и
# распознавания сканов (CLAUDE.md, правило 14).
FEED_КП = price_store.FEED
# ВЕРСИЯ РАЗБОРЩИКА. По ней library/reparse.py отбирает файлы, разобранные старым
# кодом: coalesce(parser_version, 1) < PARSER_VERSION. Поднимать её ОБЯЗАТЕЛЬНО
# при каждой правке, меняющей результат разбора, иначе правка действует только на
# новые файлы, а уже разобранные остаются с прежним результатом — то есть работа
# над разборщиком не доходит до данных.
#
#   3 — 22.09.2026: PDF читается таблицей с восстановлением колонок
#       (library/pdftable.py), цена берётся из своей колонки; базис, условия
#       оплаты, срок производства и срок поставки читаются и из колонки позиции,
#       и из общих условий КП (library/offer_terms.py); сумма строки хранится.
PARSER_VERSION = 3

# Колонки спецификации. Спецификации у всех заказчиков свои, но заголовки повторяются.
COLS = {
    "item_name": ["наименование", "номенклатура", "описание", "предмет", "позиция", "материал",
                  "запчаст", "description", "item", "наимен"],
    "part_number": ["артикул", "парт", "part", "p/n", "обозначение", "каталожн", "код"],
    "oem": ["производител", "изготовител", "бренд", "марка", "oem", "завод", "manufacturer"],
    "qty": ["кол-во", "количество", "кол.", "qty", "quantity"],
    "unit": ["ед.изм", "ед. изм", "единица", "unit", "ед-ца", "ед."],
}
NOISE_ROW = re.compile(r"^(итого|всего|подпись|примечан|№|n\s*п/п|приложение)", re.I)
# «Unit» опознаёт колонку единицы измерения — и заодно ловит «Unit price», а это
# колонка цены. Тогда в qty_unit уезжает цена, а сама единица теряется. В наших
# данных КП больше всего китайских (CNY — 11 247 строк цены из 22 084), то есть
# шапки чаще английские, и промах не редкий, а типовой. Отсев по слову рядом:
# «unit» значит единицу, если в той же ячейке не сказано «price», «cost», «rate».
ЕДИНИЦА_ЧУЖОЕ = ("price", "cost", "rate", "amount", "цена", "стоимост", "сумма")


# Session pooler Supabase допускает лишь 15 одновременных клиентов (EMAXCONNSESSION).
# Поэтому соединение не удерживается на весь прогон: открываем на время записи и сразу
# закрываем, а при отказе ждём и пробуем снова — потерять двадцать минут разбора из-за
# занятого пула недопустимо.
def connect(attempts: int = 12):
    last = None
    for i in range(attempts):
        try:
            c = psycopg2.connect(DB, connect_timeout=20)
            c.autocommit = False
            return c
        except psycopg2.OperationalError as e:
            last = e
            if "EMAXCONNSESSION" not in str(e) and "too many" not in str(e).lower():
                raise
            time.sleep(min(30, 2 ** min(i, 4)) + random.random() * 3)
    raise last


# ОДИН КЛИЕНТ НА ВЕСЬ ПОРТАЛ. Прежний bx() делал четыре МГНОВЕННЫХ повтора без
# пауз и после них возвращал пустой словарь. Обход принимал пустоту за конец
# данных и завершался как успешный: чтение СП-166 обрывалось то на 7 150, то на
# 7 750, то на 8 250 записях из 21 865 — каждый раз на другом месте, всегда
# «успешно». Четыре повтора подряд без паузы против ограничения частоты
# бесполезны: все четыре укладываются в доли секунды.
#
# bitrix_client.BitrixClient делает это правильно и давно: пауза между запросами,
# экспоненциальная выдержка с джиттером, разбор 429 и 5xx, отдельный список
# повторяемых кодов ошибок Битрикса — и ГРОМКИЙ отказ, когда попытки кончились.
# Держать вторую реализацию того же чтения незачем: сегодня они разошлись молча,
# и это стоило целого расследования.
_КЛИЕНТ = None


def клиент():
    """Ленивая сборка: без секрета модуль всё равно должен импортироваться."""
    global _КЛИЕНТ
    if _КЛИЕНТ is None:
        from bitrix_client import BitrixClient
        # ЧАСТОТА ПОД ЛИМИТ ПОРТАЛА. Умолчание клиента — ~3 запроса в секунду,
        # а Битрикс держит около двух. Одному прогону это сходило с рук за счёт
        # повторов, но разбор идёт частями, и каждая часть шлёт свои запросы:
        # двенадцать частей давали 36 запросов в секунду и гарантированный 429.
        _КЛИЕНТ = BitrixClient(BASE, min_interval=0.5)
    return _КЛИЕНТ


def bx(method: str, params: dict) -> dict:
    """Полный конверт ответа ({result, next, total}). Исчерпав повторы — падает.

    Падение здесь намеренно: неполное чтение, выданное за полное, дороже
    упавшего прогона. Прогон повторяется, потерянные записи — нет.
    """
    return клиент().call_envelope(method, params)


# Размер страницы REST Битрикса. Полное чтение кончается КОРОТКОЙ страницей;
# если последняя страница полна, а «next» не пришёл — чтение оборвалось, и это
# надо кричать, а не молчать.
СТРАНИЦА = 50


def bx_all(method: str, params: dict) -> list:
    """Обход по смещению. Кричит, если похоже, что чтение оборвалось.

    ОБРЫВ БЫЛ И БЫЛ НЕВИДИМ. 21.09.2026: этим способом СП-166 отдавал 7 750
    карточек, а чтение по ключу (>id) — 21 865. Обрыв на смещении происходит без
    ошибки: сервер просто перестаёт присылать «next». Поэтому здесь нет починки
    самого обхода — для больших наборов есть bx_all_by_id, — но есть признак, по
    которому обрыв виден в журнале.
    """
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        items = items or []
        out += items
        if "next" not in j:
            if len(items) >= СТРАНИЦА:
                print(f"::warning::{method}: чтение оборвалось на {len(out)} записях — "
                      f"последняя страница полна ({len(items)}), а продолжения нет. "
                      "Для больших наборов нужен обход по ключу (bx_all_by_id).",
                      flush=True)
            return out
        start = j["next"]


def bx_max_id(method: str, params: dict) -> int:
    """Наибольший идентификатор сущности. Один запрос.

    Нужен, чтобы РАЗДЕЛИТЬ обход между частями. Без него часть не знает границ
    своего диапазона и вынуждена читать всё.
    """
    j = bx(method, {**params, "select": ["id"], "order": {"id": "DESC"},
                    "start": -1})
    res = j.get("result")
    items = (res.get("items") if isinstance(res, dict) and "items" in res else res) or []
    try:
        return int(items[0]["id"]) if items else 0
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


def bx_all_by_id(method: str, params: dict, с_id: int = 0,
                 до_id: int | None = None) -> list:
    """Обход по ключу: filter[>id] = последний прочитанный.

    Тот же способ, которым портал читают scripts/quote_coverage.py и
    scripts/supplier_responsiveness.py (BitrixClient.list_items). На смещении
    чтение СП-166 обрывалось на 7 750 из 21 865 записей — молча, без ошибки.
    start=-1 отключает подсчёт общего числа: он и есть причина медленного и
    ненадёжного обхода по смещению.

    с_id и до_id ОГРАНИЧИВАЮТ ОБХОД ДИАПАЗОНОМ, и ради них всё и затевалось.
    Обход по ключу читает страницы подряд, поэтому поделить его между частями
    иначе нельзя: каждая часть читала бы всё целиком, и двенадцать частей давали
    бы двенадцать полных обходов портала — HTTP 429, на котором 21.09.2026 умерли
    все двенадцать. С диапазонами один полный обход РАСКЛАДЫВАЕТСЯ на части, а не
    повторяется каждой.
    """
    out: list = []
    last = с_id
    исходный = dict(params.get("filter") or {})
    if до_id is not None:
        исходный["<=id"] = до_id
    while True:
        f = dict(исходный)
        f[">id"] = last
        j = bx(method, {**params, "filter": f, "order": {"id": "ASC"}, "start": -1})
        res = j.get("result")
        items = (res.get("items") if isinstance(res, dict) and "items" in res else res) or []
        if not items:
            return out
        out += items
        try:
            last = int(items[-1]["id"])
        except (KeyError, TypeError, ValueError):
            print(f"::warning::{method}: в записи нет числового id — обход по ключу "
                  f"невозможен, прочитано {len(out)}", flush=True)
            return out
        if len(items) < СТРАНИЦА:
            return out


def sniff(b: bytes) -> str:
    if b[:2] == b"PK":
        return "xlsx/docx"
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "старый office"
    if b[:4] in (b"\x89PNG",) or b[:3] == b"\xff\xd8\xff":
        return "изображение"
    if b[:4] == b"Rar!" or b[:2] == b"\x1f\x8b":
        return "архив"
    return "прочее"


def rows_from_xlsx(b: bytes) -> list[list[str]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    out: list[list[str]] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                out.append(cells)
            if len(out) > 20000:
                return out
    return out


def rows_from_xls(b: bytes) -> list[list[str]]:
    import xlrd
    wb = xlrd.open_workbook(file_contents=b)
    out: list[list[str]] = []
    for ws in wb.sheets():
        for i in range(ws.nrows):
            cells = [str(c.value).strip() for c in ws.row(i)]
            if any(cells):
                out.append(cells)
    return out


def docx_xml(b: bytes) -> str:
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        if "word/document.xml" in z.namelist():
            return z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return ""
    return ""


def text_from_docx(b: bytes) -> str:
    """Весь текст документа. ВНИМАНИЕ: это одна строка без переводов.

    Спецификация в .docx почти всегда лежит таблицей, и её надо читать
    rows_from_docx. Эта функция годится только для определения сегмента по
    тексту файла целиком: склейка всего документа в одну строку означает, что
    text.splitlines() даст ровно один элемент, и построчный разбор потеряет всё.
    Именно так до 12.09.2026 терялись ячейки таблиц во всех .docx."""
    raw = docx_xml(b)
    return " ".join(re.findall(r"<w:t[^>]*>([^<]{1,400})</w:t>", raw)) if raw else ""


def rows_from_docx(b: bytes) -> list[list[str]]:
    """Таблицы документа построчно: <w:tr> — строка, <w:tc> — ячейка.

    Без этого спецификация в .docx не читается вовсе: текстовый путь склеивает
    документ в одну строку. Разметка Word разносит текст ячейки по нескольким
    <w:t> (правки, форматирование, автозамена), поэтому куски ячейки склеиваем."""
    raw = docx_xml(b)
    if not raw:
        return []
    out: list[list[str]] = []
    for tr in re.findall(r"<w:tr[\s>].*?</w:tr>", raw, re.S):
        cells = []
        for tc in re.findall(r"<w:tc[\s>].*?</w:tc>", tr, re.S):
            cells.append(" ".join(re.findall(r"<w:t[^>]*>([^<]{0,400})</w:t>", tc)).strip())
        if any(cells):
            out.append(cells)
        if len(out) >= 4000:
            break
    return out


def text_from_pdf(b: bytes) -> str:
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(b))
        parts = []
        for pg in rd.pages[:60]:
            parts.append(pg.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def rows_from_pdf(b: bytes) -> list[list[str]]:
    """Таблица из PDF: извлечение с сохранением выравнивания, затем колонки.

    ЗАЧЕМ. Прежде PDF шёл единственным путём — построчно, как проза, и цена в нём
    опознавалась арифметикой «кол-во × цена = сумма» (quotes.цена_из_текста).
    Правило честное, но узкое: оно молчит без суммы в строке и не может
    воспользоваться тем, что в самом файле НАПИСАНО, где цена, а где сумма — это
    написано в шапке. extraction_mode="layout" сохраняет отступы, значит колонки
    восстановимы (library/pdftable.py), а дальше работает та же машинерия шапки,
    что у xlsx: цена за единицу против суммы, валюта из заголовка, срок, базис.

    Пустой список — честный ответ «таблицы нет», и вызывающий идёт прежним путём.
    Решает не этот код, а header_map: без опознанной шапки таблица отвергается.
    """
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(b))
        части = []
        for pg in rd.pages[:60]:
            try:
                части.append(pg.extract_text(extraction_mode="layout") or "")
            except Exception:
                # Режим layout есть не во всех версиях pypdf и спотыкается на
                # отдельных страницах. Страница без него — не причина терять файл.
                return []
        # Ступень допуска выбирают ворота шапки: см. pdftable.ДОПУСКИ. Разрез,
        # не давший опознаваемой шапки, ниже отвергается целиком, поэтому
        # перебор ступеней ничем не рискует.
        return pdftable.строки_в_таблицу(
            "\n".join(части), годится=lambda rows: header_map(rows)[0] >= 0)
    except Exception:
        return []


def header_map(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Ищем строку заголовков: в ней должно найтись хотя бы два известных названия."""
    for i, row in enumerate(rows[:40]):
        low = [c.lower() for c in row]
        found: dict[str, int] = {}
        for key, words in COLS.items():
            for j, c in enumerate(low):
                if not c or not any(w in c for w in words):
                    continue
                if key == "unit" and any(w in c for w in ЕДИНИЦА_ЧУЖОЕ):
                    continue          # «Unit price» — колонка цены, не единицы
                found.setdefault(key, j)
                break
        if "item_name" in found and len(found) >= 2:
            return i, found
    return -1, {}


def items_from_rows(rows: list[list[str]]) -> list[dict]:
    """Позиции из таблицы. Если заголовков нет — берём самую длинную текстовую
    ячейку строки как наименование: у большинства спецификаций это работает."""
    hi, cols = header_map(rows)
    # Ценовые колонки ищутся в той же строке заголовков. У спецификаций заказчика
    # их там нет, и разбор не меняется; у КП поставщика в них весь смысл файла
    # (library/quotes.py).
    цк = quotes.колонки_цены(rows[hi]) if hi >= 0 else {}
    # Валюта почти всегда написана только в шапке («Цена за ед., EUR»), а в
    # ячейках стоят голые числа.
    вк = quotes.валюта_заголовка(rows[hi], цк) if цк else None
    out: list[dict] = []
    body = rows[hi + 1:] if hi >= 0 else rows
    for row in body:
        if not row:
            continue
        joined = " ".join(row).strip()
        if len(joined) < 6 or NOISE_ROW.match(joined):
            continue
        rec: dict = {}
        if cols:
            def get(key: str) -> str:
                j = cols.get(key, -1)
                return row[j].strip() if 0 <= j < len(row) else ""
            rec["item_name"] = get("item_name")
            rec["part_number"] = get("part_number")
            rec["oem"] = get("oem")
            rec["unit"] = get("unit")
            q = get("qty").replace(",", ".")
            try:
                rec["qty"] = float(re.sub(r"[^\d.]", "", q)) if q else None
            except Exception:
                rec["qty"] = None
        else:
            cand = max(row, key=lambda c: len(c)) if row else ""
            rec["item_name"] = cand
            rec["part_number"] = ""
            rec["oem"] = ""
            rec["unit"] = ""
            rec["qty"] = None
        name = (rec.get("item_name") or "").strip()
        if len(name) < 4 or name.isdigit():
            continue
        if not rec.get("part_number"):
            # Прежнее выражение искало по joined.upper() и принимало за артикул
            # дату 01.09.2026, номер закона 223-ФЗ и любое пятизначное число.
            # docfilter.part_number_of отбрасывает даты, номера пунктов, годы и
            # разряды тысяч. Замена действует только на новые разборы: пересчёт
            # накопленных 113 769 парт-номеров — это UPDATE на полтора миллиона
            # строк с пересчётом fts, отдельная задача.
            rec["part_number"] = docfilter.part_number_of(joined)
        rec["_row"] = joined[:600]
        # Цена берётся только из названной колонки. Сумма строки ценой не
        # становится: из неё цена выводится делением на количество, и такая
        # строка помечена как выведенная (library/quotes.py).
        rec["_цена"] = quotes.цена_строки(row, цк, rec.get("qty"), вк) if цк else None
        # Условия из СВОИХ колонок этой позиции: базис, оплата, срок изготовления,
        # срок поставки. Они главнее общих условий файла — они про эту позицию.
        rec["_из_строки"] = offer_terms.из_строки(row, цк) if цк else None
        out.append(rec)
        if len(out) >= 3000:
            break
    return out


def collect_refs(days: int, shard: int = 0, shards: int = 1) -> list[dict]:
    """Ссылки на все вложения сделок за период.

    Берём их через crm.item.list (entityTypeId=2), а НЕ через crm.deal.list.
    Разница решающая: crm.deal.list отдаёт у файловых полей только ссылки на
    страницы портала (`crm.deal.show/show_file.php`, `crm_show_file.php`), а те
    требуют сессии пользователя и вебхуку возвращают страницу входа с кодом 200 —
    отсюда прежние «не скачался» на всей выборке. Универсальный item-метод отдаёт
    у тех же полей `urlMachine`: REST-ссылку с одноразовым токеном, по которой
    файл приходит как есть. Проверено на портале 08.09.2026: 22 181 вложение
    у 2 928 сделок года, скачивание отдаёт PDF, XLSX и DOCX.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00+03:00")
    fields = ((bx("crm.item.fields", {"entityTypeId": 2}).get("result") or {}).get("fields") or {})
    ffields = [k for k, v in fields.items() if v.get("type") == "file"]
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID"], "order": {"ID": "ASC"}})
    ids = [int(d["ID"]) for d in deals]
    # ЧАСТЬ БЕРЁТ СВОЮ ДОЛЮ СДЕЛОК, А НЕ ВСЕ. Двенадцать частей, каждая из которых
    # перечисляет ВСЕ сделки и все их файловые поля, — это двенадцатикратная
    # нагрузка на портал одним залпом: прогон 21.09.2026 умер во всех двенадцати
    # частях с HTTP 429 ещё до первого файла. Деление по остатку — честное
    # разбиение: файл принадлежит ровно одной сделке, значит попадает ровно в
    # одну часть, и ни один файл не теряется и не читается дважды.
    if shards > 1:
        ids = ids[shard::shards]
    print(f"сделок за {days} дн.: {len(ids)}"
          + (f" (часть {shard + 1} из {shards})" if shards > 1 else "")
          + f" · файловых полей: {len(ffields)}", flush=True)

    refs: list[dict] = []
    for i in range(0, len(ids), 50):
        j = bx("crm.item.list", {"entityTypeId": 2, "filter": {"@id": ids[i:i + 50]},
                                 "select": ["id"] + ffields, "start": 0})
        for x in (j.get("result") or {}).get("items") or []:
            for f in ffields:
                v = x.get(f)
                if not v:
                    continue
                for fo in (v if isinstance(v, list) else [v]):
                    if isinstance(fo, dict) and fo.get("urlMachine"):
                        refs.append({"deal": str(x["id"]), "field": f, "origin": "поле сделки", "fo": fo})
    print(f"вложений в полях сделок: {len(refs)}", flush=True)
    return refs


def ключи_брендов(v) -> str:
    """Ключи брендов карточки списком через запятую.

    Поле многозначное, и первый бренд в нём — не «главный», а просто первый:
    берём все. Имена здесь НЕ разрешаем, как и у поставщика: сопоставление
    ключа со справочником — отдельный проход, а догадка вместо связи хуже
    пустоты.
    """
    значения = v if isinstance(v, list) else ([v] if v else [])
    ключи: list[str] = []
    for z in значения:
        k = crm_id(z)
        if k and str(k) not in ключи:
            ключи.append(str(k))
    return ",".join(ключи)


def без_повторов(refs: list[dict]) -> tuple[list[dict], int]:
    """Ссылки на вложения без повторов по id файла плюс число отброшенных.

    Ключ — id объекта вложения, тот же, что уходит в lib_files.file_id. Именно
    по нему таблица конфликтует, и именно повтор внутри одного пакета роняет
    вставку целиком.
    """
    видели: set[str] = set()
    out: list[dict] = []
    дублей = 0
    for r in refs:
        fid = str((r.get("fo") or {}).get("id") or "")
        if not fid:
            out.append(r)          # без id отсеять нельзя, пусть идёт как есть
            continue
        if fid in видели:
            дублей += 1
            continue
        видели.add(fid)
        out.append(r)
    return out, дублей


def диапазон_части(макс: int, shard: int, shards: int) -> tuple[int, int | None]:
    """Границы идентификаторов для своей части: (после, включительно по).

    Диапазоны СМЕЖНЫЕ, а не по остатку от деления: обход идёт по возрастанию
    ключа, и только смежный кусок можно прочитать, не трогая чужие страницы.

    Плотность идентификаторов неравномерна — где-то пропуски, где-то густо, —
    поэтому части выходят разного размера. Это допустимо: делится нагрузка на
    портал, а она пропорциональна прочитанным страницам, а не ровности долей.
    Сколько досталось каждой части, прогон печатает — ровность видно числом.
    """
    if shards <= 1 or макс <= 0:
        return 0, None
    шаг = макс // shards + 1
    низ = shard * шаг
    верх = макс if shard == shards - 1 else низ + шаг
    return низ, верх


def collect_refs_rfq(days: int, shard: int = 0, shards: int = 1) -> list[dict]:
    """Ссылки на вложения карточек запросов поставщикам (СП-166).

    БЕРЁМ ТОЛЬКО ФАЙЛЫ СО СТОРОНЫ ПОСТАВЩИКА. «Request file» — то, что отправили
    мы; разобрать его как котировку значит объявить прокотированным собственный
    запрос. Список полей — общий с замерами (scripts/quote_coverage.py).

    Механика та же, что у сделок: crm.item.list отдаёт у файловых полей
    urlMachine — REST-ссылку с одноразовым токеном. Разница только в том, какую
    сущность спрашиваем и какие поля берём.
    """
    # ФИЛЬТР ПО ДАТЕ НА СТОРОНЕ ПОРТАЛА СЪЕДАЛ ДВЕ ТРЕТИ КАРТОЧЕК. Замер
    # 21.09.2026: с «>=createdTime» за 7 300 дней (двадцать лет) приходило 7 150
    # карточек, а замеры котировок, читающие ту же сущность БЕЗ фильтра, видят
    # 21 865. Двадцатилетнее окно отсечь ничего не может — значит отсекал сам
    # фильтр: у карточки либо нет createdTime, либо сравнение по нему у СП-166
    # работает не так, как ожидается. Цена ошибки — две трети котировок молча
    # мимо разбора.
    #
    # Поэтому читаем ВСЁ, как это делают scripts/quote_coverage.py и
    # scripts/supplier_responsiveness.py, а окно применяем у себя. Карточку без
    # даты окно НЕ отбрасывает: недоказанное «старая» дешевле потерянной цены.
    поля = list(ПОЛЯ_КП)
    # Обход по ключу, а не по смещению: на смещении приходило 7 750 карточек
    # вместо 21 865, и без единой ошибки (замер 21.09.2026).
    низ, верх = (0, None)
    if shards > 1:
        макс = bx_max_id("crm.item.list", {"entityTypeId": SPA_RFQ})
        низ, верх = диапазон_части(макс, shard, shards)
        print(f"часть {shard + 1} из {shards}: карточки с id от {низ + 1} "
              f"до {верх if верх is not None else 'конца'} (всего до {макс})",
              flush=True)
    карточки = bx_all_by_id("crm.item.list",
                            {"entityTypeId": SPA_RFQ,
                             "select": ["id", "createdTime", ПОЛЕ_ПОСТАВЩИКА,
                                        ПОЛЕ_БРЕНДОВ] + поля},
                            с_id=низ, до_id=верх)
    всего = len(карточки)
    без_даты = 0
    if days and days > 0:
        порог = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        в_окне = []
        for x in карточки:
            д = str(x.get("createdTime") or "")[:10]
            if not д:
                без_даты += 1
                в_окне.append(x)
            elif д >= порог:
                в_окне.append(x)
        карточки = в_окне
    print(f"карточек запросов: всего {всего}, в окне {days} дн. — {len(карточки)}"
          f" (без даты {без_даты}, взяты) · полей КП: {len(поля)}", flush=True)

    refs: list[dict] = []
    свои = 0
    без_поставщика = 0
    без_брендов = 0
    for x in карточки:
        # Компания-поставщик известна ПРЯМО ЗДЕСЬ, и связать цену с ней надо
        # сейчас: отдельный проход позже означал бы второе сплошное чтение
        # портала ради того, что уже держим в руках.
        компания = crm_id(x.get(ПОЛЕ_ПОСТАВЩИКА))
        if not компания:
            без_поставщика += 1
        бренды = ключи_брендов(x.get(ПОЛЕ_БРЕНДОВ))
        if not бренды:
            без_брендов += 1
        for f in поля:
            v = x.get(f)
            if not v:
                continue
            for fo in (v if isinstance(v, list) else [v]):
                if isinstance(fo, dict) and fo.get("urlMachine"):
                    refs.append({"deal": str(x["id"]), "field": f,
                                 "origin": "поле запроса", "fo": fo,
                                 "company": str(компания) if компания else None,
                                 "brands": бренды or None})
        if x.get(ПОЛЕ_ЗАПРОСА):
            свои += 1
    # ОДИН ФАЙЛ — ОДНА ССЫЛКА. Вложение висит на карточке, но одно и то же
    # вложение встречается у нескольких карточек и в нескольких полях сразу.
    # Прогон 22.09.2026 упал на этом: «ON CONFLICT DO UPDATE cannot affect row a
    # second time» — PostgreSQL не даёт обновить одну строку дважды в одном
    # операторе, а lib_files конфликтует по file_id. На 46 файлах дубль не
    # попадался, на 7 523 попался. Помимо падения дубль стоил бы второй закачки
    # того же файла и удвоенных строк цены.
    #
    # Оставляем первую ссылку: поля карточки перебираются в постоянном порядке,
    # поэтому выбор воспроизводим. Число отброшенных печатается — молчаливый
    # отсев скрыл бы, что портал отдаёт файл по нескольку раз.
    refs, дублей = без_повторов(refs)
    print(f"вложений КП от поставщиков: {len(refs)}"
          f" · повторов одного файла отброшено: {дублей}"
          f" · карточек с нашим «Request file» (не берём): {свои}", flush=True)
    print(f"карточек без указанного поставщика: {без_поставщика} из {len(карточки)}"
          " — их цены лягут без привязки к компании", flush=True)
    print(f"карточек без указанного бренда: {без_брендов} из {len(карточки)}"
          " — по ним разрез «чей это» даст только то, что написано в файле",
          flush=True)
    return refs


def is_login_page(b: bytes) -> bool:
    """Портал отдаёт страницу входа с кодом 200 — по коду ответа её не отличить.
    Отличаем по содержимому, иначе HTML формы логина уходит в разбор как файл."""
    head = b.lstrip()[:512].lower()
    return head.startswith(b"<!doctype htm") or head.startswith(b"<html")


def download(fo: dict) -> bytes | None:
    for key in ("urlMachine", "downloadUrl", "url", "URL_MACHINE", "DOWNLOAD_URL"):
        u = fo.get(key)
        if u:
            try:
                r = requests.get(str(u), timeout=90)
                if r.status_code == 200 and len(r.content) > 200 and not is_login_page(r.content):
                    return r.content
            except Exception:
                pass
    fid = fo.get("id") or fo.get("ID")
    if fid:
        try:
            u = (bx("disk.file.get", {"id": fid}).get("result") or {}).get("DOWNLOAD_URL")
            if u:
                r = requests.get(u, timeout=90)
                if r.status_code == 200 and len(r.content) > 200:
                    return r.content
        except Exception:
            pass
    return None


def применить_условия(items: list[dict], весь_текст: str) -> None:
    """Свод коммерческих условий по каждой позиции КП: значение и ОТКУДА оно.

    ОТДЕЛЬНОЙ ФУНКЦИЕЙ, ЧТОБЫ ЕЁ МОЖНО БЫЛО ПРОВЕРИТЬ. Внутри handle() этот шаг
    стоит за скачиванием файла, и тест на него пришлось бы писать через подмену
    сети — то есть проверять копию логики вместо самой логики.

    ОБЩИЕ УСЛОВИЯ РАЗБИРАЮТСЯ ОДИН РАЗ НА ФАЙЛ. Блок условий под таблицей один, а
    позиций в КП бывают сотни.

    Пустое поле без источника означало разом «в КП не указано» (факт о
    предложении — можно спросить поставщика) и «разбор не дошёл» (наш недочёт —
    спрашивать надо разборщик). Это разные выводы и разные действия, поэтому
    источник пишется явно (library/offer_terms.py).
    """
    общие = offer_terms.условия_файла(весь_текст or "")
    был_текст = bool((весь_текст or "").strip())
    for it in items:
        it["_условия"] = offer_terms.свести(it.get("_из_строки") or {},
                                            общие, был_текст)


def handle(ref: dict) -> tuple[dict, list[dict]]:
    fo = ref["fo"]
    fid = str(fo.get("id") or fo.get("ID"))
    rec = {"file_id": fid, "deal_id": ref["deal"], "origin": ref["origin"], "field": ref["field"],
           "kind": None, "size_bytes": None, "status": "не скачался", "reason": None,
           "chars": 0, "rows_found": 0, "segment_id": None, "sha256": None,
           "parse_path": None, "header_found": None, "doc_class": None,
           "class_rule": None, "text_lines": None, "item_lines": None}
    b = download(fo)
    if not b:
        return rec, []
    rec["size_bytes"] = len(b)
    rec["sha256"] = hashlib.sha256(b).hexdigest()
    kind = sniff(b)
    rec["kind"] = kind
    rows: list[list[str]] = []
    text = ""
    try:
        if kind == "xlsx/docx":
            try:
                rows = rows_from_xlsx(b)
            except Exception:
                # Это .docx. Спецификация в нём лежит таблицей; берём таблицу
                # только при найденной шапке, иначе ветка «самая длинная ячейка»
                # превратит таблицу реквизитов и подписей в строки номенклатуры.
                drows = rows_from_docx(b)
                if drows and header_map(drows)[0] >= 0:
                    rows = drows
                else:
                    text = text_from_docx(b)
        elif kind == "старый office":
            rows = rows_from_xls(b)
        elif kind == "pdf":
            # СНАЧАЛА ТАБЛИЦА, ПОТОМ ПРОЗА. Ворота те же, что у .docx: таблица
            # берётся только при опознанной шапке, иначе ветка «самая длинная
            # ячейка» превратит колонтитулы и подписи в номенклатуру. Не прошло —
            # разбираем прежним текстовым путём, то есть хуже, чем было, не будет.
            prows = rows_from_pdf(b)
            if prows and header_map(prows)[0] >= 0:
                rows = prows
            else:
                text = text_from_pdf(b)
    except Exception as e:
        rec["status"] = "формат не читаем"
        rec["reason"] = type(e).__name__
        return rec, []

    items: list[dict] = []
    # ПОЛНЫЙ ТЕКСТ ФАЙЛА — ОТДЕЛЬНО ОТ text. text у табличного пути склеен из
    # строк-позиций, а блок условий («Условия поставки: DAP Москва. Оплата 30/70»)
    # стоит ПОД таблицей и в позиции не попадает. Искать в text общие условия
    # значит не находить их никогда — при том, что в файле они написаны, и это
    # обычная форма КП (владелец: «бывает, что в конце предложения вообще цифра»).
    весь_текст = ""
    if rows:
        items = items_from_rows(rows)
        text = " ".join(r.get("_row", "") for r in items)[:200000]
        весь_текст = "\n".join(" ".join(c for c in r if c) for r in rows)[:400000]
        rec["parse_path"] = "таблица"
        rec["header_found"] = header_map(rows)[0] >= 0
    elif text:
        # ВОРОТА СПЕЦИФИКАЦИИ. До 12.09.2026 здесь любая строка длиннее восьми
        # знаков становилась позицией номенклатуры, и в спрос лёг текст извещений
        # о закупке, проектов договоров и форм КП — 369 171 строка, четверть базы.
        #
        # Отказ выносится ЦЕЛИКОМ ПО ФАЙЛУ, а не по строке. Построчный отказ стоил
        # бы до сорока процентов позиций даже в заведомо хорошей спецификации:
        # продолжение наименования, перенесённое на вторую строку, никаких
        # признаков позиции не несёт. Внутри принятого файла пишутся все строки.
        lines = [ln.strip() for ln in text.splitlines()
                 if len(ln.strip()) > 8 and not NOISE_ROW.match(ln.strip())]
        spec_n = prose_n = 0
        for ln in lines:
            sp, pr = docfilter.row_marks(ln[:300])
            spec_n += bool(sp)
            prose_n += bool(pr and not sp)
        rec["parse_path"], rec["header_found"] = "текст", False
        rec["text_lines"], rec["item_lines"] = len(lines), spec_n
        весь_текст = text[:400000]
        verdict = docfilter.file_verdict(len(lines), spec_n, prose_n)
        if verdict == "документация" and SPECGATE:
            rec["chars"] = len(text)
            rec["status"] = "текст без спецификации"
            rec["reason"] = (f"строк {len(lines)} · с признаками позиции {spec_n} · "
                             f"с признаками текста {prose_n}")      # только агрегаты
            rec["doc_class"], rec["class_rule"] = "документация", docfilter.RULE_VERSION
            return rec, []
        for ln in lines[:2000]:
            # КП приходят PDF-ами, и таблицы в них нет: разбор по заголовкам
            # мимо. Цена здесь опознаётся арифметикой — кол-во × цена = сумма,
            # тройка чисел в самой строке (library/quotes.py). Замер 21.09.2026:
            # без этого пробный разбор дал 139 позиций и НОЛЬ цен.
            ц = quotes.цена_из_текста(ln) if SOURCE == "rfq" else None
            items.append({"item_name": ln[:300], "part_number": docfilter.part_number_of(ln),
                          "oem": "", "unit": "", "qty": ц["qty"] if ц else None,
                          "_row": ln[:600], "_цена": ц})

    rec["chars"] = len(text)
    rec["rows_found"] = len(items)
    rec["segment_id"] = classify(text) if text else None
    if not items:
        # «Пусто» перестало врать: тендерный PDF с текстовым слоем, но без позиций,
        # раньше ложился как «нет текстового слоя», и оценка объёма распознавания
        # сканов по этому статусу была завышена.
        rec["status"] = "пусто" if not rec["chars"] else "текст без спецификации"
        rec["reason"] = ("нет текстового слоя" if not rec["chars"]
                         else "позиции не распознаны")
        return rec, []
    rec["status"] = "разобран"
    # Валюта всего файла — последний довод, когда ни заголовок колонки, ни
    # ячейка её не назвали. Берётся, только если в тексте ровно одна валюта:
    # две («цена в евро, НДС в рублях») угадывать нельзя.
    вф = quotes.валюта_файла(text)
    for it in items:
        # Сегмент СТРОКИ — по самой строке. Наследование от файла помечается
        # отдельно: пока оно молчаливо, segment_id нельзя использовать как эталон
        # (текст строки — подмножество текста файла, поэтому файл либо весь
        # классифицирован, либо весь нет, и любое измерение по нему тавтологично).
        own = classify(it.get("_row", ""))
        it["segment_id"] = own or rec["segment_id"]
        it["segment_rule"] = "строка" if own else ("файл" if rec["segment_id"] else None)
        it["deal_id"] = ref["deal"]
        it["source_file"] = fid
        it["company"] = ref.get("company")
        it["brands"] = ref.get("brands")
        # Валюта файла вместо ненайденной; оговорка и уверенность правятся там же,
        # чтобы в строке не стояли разом «не названа» и «взята по файлу».
        quotes.подставить_валюту(it.get("_цена"), вф)
    if SOURCE == "rfq":
        применить_условия(items, весь_текст)
    return rec, items


NULLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def pg(s) -> str:
    """Строка, пригодная для PostgreSQL.

    В тексте, извлечённом из PDF и старых .xls, попадаются нулевые байты и другие
    управляющие символы. PostgreSQL их в text не принимает — psycopg2 падает с
    «A string literal cannot contain NUL (0x00) characters», и вместе с одной
    строкой теряется весь пакет разобранных файлов."""
    return NULLS.sub(" ", str(s or ""))


def ensure_segments(cur) -> None:
    """Справочник сегментов в базе должен существовать ДО записи номенклатуры.

    lib_demand.segment_id ссылается на lib_segments(id); при пустом справочнике
    вся запись падает с ForeignKeyViolation, а разобранные файлы теряются —
    именно так оборвались все двенадцать частей прогона 08.09.2026.
    Источник истины — словарь SEGMENTS в library/segments.py, поэтому справочник
    наполняем из него, а не поддерживаем вручную в двух местах."""
    psycopg2.extras.execute_values(
        cur,
        "insert into lib_segments (id, name) values %s on conflict (id) do nothing",
        [(sid, name) for sid, (name, _words) in SEGMENTS.items()],
    )


def main() -> int:
    for var in ("BITRIX_WEBHOOK_URL", "SUPABASE_DB_URL"):
        if not os.environ.get(var):
            print(f"нет переменной {var}", file=sys.stderr)
            return 2
    if SHARDS > 1:
        print(f"часть {SHARD + 1} из {SHARDS}", flush=True)
    conn = connect()
    with conn.cursor() as cur:
        ensure_segments(cur)
        conn.commit()
        # Повторная попытка для не скачавшихся: «не скачался» — сетевая осечка,
        # а не свойство файла. «Пусто» и «формат не читаем» повторять незачем:
        # результат будет тот же, а прогон подорожает.
        cur.execute("select file_id from lib_files"
                    + (" where status <> 'не скачался'" if RETRY_FAILED else ""))
        done = {r[0] for r in cur.fetchall()}
    conn.close()
    print(f"уже разобрано ранее: {len(done)}"
          + (" (файлы со статусом «не скачался» пойдут заново)" if RETRY_FAILED else ""), flush=True)

    if SOURCE not in ("deals", "rfq"):
        print(f"неизвестный SOURCE={SOURCE!r}: допустимо deals или rfq", file=sys.stderr)
        return 2
    print(f"источник вложений: {'карточки запросов (СП-166)' if SOURCE == 'rfq' else 'сделки'}",
          flush=True)
    refs = collect_refs_rfq(DAYS) if SOURCE == "rfq" else collect_refs(DAYS)
    mine = [r for r in refs
            if int(hashlib.sha1(str(r["fo"].get("id") or r["fo"].get("ID")).encode()).hexdigest(), 16) % SHARDS == SHARD
            and str(r["fo"].get("id") or r["fo"].get("ID")) not in done]
    if LIMIT:
        mine = mine[:LIMIT]
    print(f"к разбору в этой части: {len(mine)}\n", flush=True)
    if not mine:
        print("нечего делать")
        return 0

    stat: Counter = Counter()
    kinds: Counter = Counter()
    segs: Counter = Counter()
    total_items = 0
    цен = 0
    buf_files: list[tuple] = []
    buf_items: list[tuple] = []
    buf_prices: list[tuple] = []

    def flush() -> None:
        nonlocal buf_files, buf_items, buf_prices
        if not buf_files and not buf_items and not buf_prices:
            return
        conn = connect()
        with conn.cursor() as cur:
            if buf_items:
                try:
                    psycopg2.extras.execute_values(cur, """
                        insert into lib_demand
                          (segment_id, deal_id, item_name, oem, part_number, qty, unit, source,
                           source_file, segment_rule)
                        values %s""", buf_items, page_size=500)
                except (psycopg2.Error, ValueError) as e:
                    # одна испорченная строка не должна стоить всего пакета: двадцать минут
                    # разбора уже потрачены, поэтому досылаем построчно и пропускаем битые
                    conn.rollback()
                    bad = 0
                    for row in buf_items:
                        try:
                            cur.execute("""insert into lib_demand
                                (segment_id, deal_id, item_name, oem, part_number, qty, unit, source,
                                 source_file, segment_rule)
                                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", row)
                        except (psycopg2.Error, ValueError):
                            conn.rollback()
                            bad += 1
                    print(f"  ⚠ пакет номенклатуры не прошёл ({type(e).__name__}); "
                          f"построчно записано {len(buf_items) - bad}, пропущено {bad}", flush=True)
            if buf_prices:
                try:
                    price_store.записать(cur, buf_prices, psycopg2.extras.execute_values)
                except psycopg2.Error as e:
                    # Откатываем ВСЁ, включая учёт файлов: файлы, отмеченные
                    # разобранными, при потерянных ценах — молчаливая потеря, а
                    # упавший прогон повторяется.
                    conn.rollback()
                    conn.close()
                    raise RuntimeError(f"{price_store.ПОДСКАЗКА}. Ошибка: {e}") from e
            if buf_files:
                # ИНВАРИАНТ ПАКЕТА, А НЕ ПОВТОР ОТСЕВА ВЫШЕ. Вставка конфликтует
                # по file_id, и два одинаковых ключа в одном операторе роняют её
                # целиком. Ссылки уже отсеяны в без_повторов(), но буфер сюда
                # наполняет не только этот путь, и требование «ключ в пакете
                # уникален» принадлежит самой вставке. Берём последнюю запись:
                # если файл почему-то разобран дважды, свежий разбор вернее.
                buf_files = list({r[0]: r for r in buf_files}.values())
                psycopg2.extras.execute_values(cur, """
                    insert into lib_files
                      (file_id, deal_id, origin, field, kind, size_bytes, status, reason,
                       chars, rows_found, segment_id, sha256,
                       parse_path, header_found, doc_class, class_rule,
                       text_lines, item_lines, parser_version)
                    values %s
                    on conflict (file_id) do update set
                      status = excluded.status, reason = excluded.reason, chars = excluded.chars,
                      rows_found = excluded.rows_found, segment_id = excluded.segment_id,
                      parse_path = excluded.parse_path, header_found = excluded.header_found,
                      doc_class = excluded.doc_class, class_rule = excluded.class_rule,
                      text_lines = excluded.text_lines, item_lines = excluded.item_lines,
                      parser_version = excluded.parser_version,
                      processed_at = now()""", buf_files, page_size=500)
        conn.commit()
        conn.close()
        buf_files, buf_items, buf_prices = [], [], []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, (rec, items) in enumerate(pool.map(handle, mine), 1):
            stat[rec["status"]] += 1
            if rec["kind"]:
                kinds[rec["kind"]] += 1
            if rec["segment_id"]:
                segs[rec["segment_id"]] += rec["rows_found"]
            total_items += rec["rows_found"]
            buf_files.append((rec["file_id"], rec["deal_id"], rec["origin"], rec["field"], rec["kind"],
                              rec["size_bytes"], rec["status"], pg(rec["reason"]), rec["chars"],
                              rec["rows_found"], rec["segment_id"], rec["sha256"],
                              rec["parse_path"], rec["header_found"], rec["doc_class"],
                              rec["class_rule"], rec["text_lines"], rec["item_lines"],
                              PARSER_VERSION))
            for it in items:
                buf_items.append((it["segment_id"], it["deal_id"], pg(it["item_name"])[:500],
                                  pg(it.get("oem"))[:200], pg(it.get("part_number"))[:120],
                                  it.get("qty"), pg(it.get("unit"))[:40],
                                  ИСТОЧНИК_СТРОКИ, it["source_file"],
                                  it.get("segment_rule")))
                ц = it.get("_цена")
                if ц and SOURCE == "rfq":
                    buf_prices.append(price_store.строка(it, ц, pg))
                    цен += 1
            if len(buf_files) >= 200 or len(buf_items) >= 4000 or len(buf_prices) >= 2000:
                flush()
            if n % 200 == 0:
                print(f"  обработано {n} из {len(mine)} · позиций {total_items}", flush=True)
    flush()

    print("\n=== ИТОГ ЧАСТИ ===")
    print(f"файлов: {sum(stat.values())} · позиций номенклатуры: {total_items}")
    if SOURCE == "rfq":
        print(f"строк с ценой: {цен}"
              + (f" ({цен * 100 // total_items} % позиций)" if total_items else ""))
    print(f"по состоянию: {dict(stat.most_common())}")
    print(f"по формату:   {dict(kinds.most_common())}")
    print("позиции по сегментам:")
    for sid, n in segs.most_common():
        print(f"    {name_of(sid):32s} {n:>8d}")
    print("\n✓ разбор части завершён")
    return 0


if __name__ == "__main__":
    sys.exit(main())
