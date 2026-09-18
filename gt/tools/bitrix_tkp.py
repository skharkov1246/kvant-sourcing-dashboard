#!/usr/bin/env python3
"""Входящие КП по сделке из Bitrix24: адресный обход, файл за файлом.

Распоряжение владельца 17.09.2026: «Никаких массовых выгрузок не нужно делать.
Они бесполезны и только тратят ресурс. Ты смотришь, читаешь сделку, читаешь
запросы поставщикам, а потом уже видишь файлы и потом их загружаешь. Причём
файлы должны быть не наши исходящие запросы поставщикам, а те, которые появляются
от сорсеров или от контрагентов». Этот инструмент переписан под такой порядок.

ЧЕМ ОН ОТЛИЧАЕТСЯ ОТ ПЕРВОЙ ПОПЫТКИ, которая повисла на 28 минутах и не дала ни
одного файла. Разбор 17.09.2026 вскрыл четыре ошибки, и все четыре учтены здесь:

1. crm.deal.list для файлов НЕ ГОДИТСЯ. Его файловые поля отдают ссылки вида
   crm_show_file.php, которым нужна сессия портала, — вебхук получает СТРАНИЦУ
   ВХОДА с кодом 200 и разбирает её как спецификацию. Рабочий путь один:
   crm.item.list с entityTypeId=2 и ссылка urlMachine. Это измерено в
   base/collect_attachments.py и base/fetch_files.py, здесь оно повторено.
2. Маска select=["*"] файловых полей НЕ ВОЗВРАЩАЕТ. Поля берутся из
   crm.item.fields по type == "file" — одним вызовом за прогон.
3. Ссылка urlMachine уже лежит в объекте, и спрашивать её у disk.* незачем.
   Прежний порядок платил два дросселированных вызова на каждый файл, а при
   ошибке уходил в шесть повторов с паузой до 3,5 минут.
4. Результат писался только в конце, поэтому таймаут унёс всё. Здесь запись
   идёт по ходу, после каждого файла.

НАПРАВЛЕНИЕ ФАЙЛА определяется ИМЕНЕМ ПОЛЯ, а не догадкой по названию файла.
У смарт-процесса 166 «Запросы поставщикам» восемь файловых полей (их состав
записан в base/fetch_rfq.py), и они прямо делятся на наши и чужие: «КП
поставщика» и «Offer from supplier» — входящие, «Request file» — наш исходящий
запрос. Это и есть та очевидность из контекста, которую требовал владелец.

Где ещё искать, кроме полей карточки: комментарии таймлайна, дела (у дела есть
поле DIRECTION — готовый признак входящего письма) и чат сделки, включая
открытые линии с внешним контрагентом.

ДВА ВЫХОДА, потому что репозиторий публичный, а цены заказчику коммерческие
(правило 5 CLAUDE.md, SECURITY.md):
  --out-full   полная выгрузка с ценами. Артефактом, в репозиторий не идёт.
  --out-index  опись БЕЗ цен: что за файл, откуда, чем разобран, сколько строк.

Холостой прогон обязателен перед скачиванием (правило 3 CLAUDE.md):
  python gt/tools/bitrix_tkp.py --keyword ЛУКОЙЛ --dry-run
показывает, сколько файлов нашлось и каких, не скачивая ни одного.

В журнал идут только агрегаты и имена полей (правило 17): ни наименований
позиций, ни цен, ни имён файлов заказчика.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402

DEAL_ENTITY = 2
SPA_RFQ = 166

# Восемь файловых полей СП-166 — состав из base/fetch_rfq.py, там он выверен на
# живом портале. Направление проставлено по смыслу названия: это и есть признак,
# который владелец назвал «очевидным из контекста».
RFQ_FILE_FIELDS = {
    "ufCrm18_1700698211875": ("КП поставщика", "входящее"),
    "ufCrm18_1731179998": ("Offer from supplier", "входящее"),
    "ufCrm18_1703711961310": ("Offer, old", "входящее"),
    "ufCrm18_1703712059311": ("Processed offer", "входящее"),
    "ufCrm18_1703712074559": ("Processed offer with descriptions / archive", "входящее"),
    "ufCrm18_1727423346": ("Request file", "наш запрос"),
    # сорсер мог положить КП не в тот слот, поэтому это «неизвестно», а не отказ
    "ufCrm18_1730999038678": ("Мануал, чертеж, шильд", "неизвестно"),
    "ufCrm18_1730999106096": ("Bank Details", "неизвестно"),
}
RFQ_SELECT = ["id", "title", "stageId", "parentId2", "ufCrm18Supplier", "companyId",
              "createdTime", "assignedById"]

# НАПРАВЛЕНИЕ ПО ИМЕНИ ПОЛЯ СДЕЛКИ. Измерено холостым прогоном 17.09.2026: у
# сделки 25 файловых полей и 1998 файлов, из них по коду поля СП-166 опознавалось
# лишь 9 — остальные 1509 уходили в «неизвестно» и разбирались бы вслепую. Имена
# полей сделки говорят прямо, и делятся они на ЧЕТЫРЕ группы, а не на две:
#   «Offer from us», «Request file» — наши исходящие, цен заказчику там нет;
#   «Result, ТКП», «Economics of the project» — НАШИ ВЫСТАВЛЕННЫЕ ЦЕНЫ, ровно
#     то, что владелец просил оставить: «строки, на которые я дал цены»;
#   «Offer from supplier(s)», «КП поставщика» — входящие от контрагентов;
#   «Техническая спецификация», «Technical data from customer» — заявка
#     заказчика, цен в ней нет, и 396 таких файлов не должны съесть предел.
# Порядок правил значим: «наш запрос» проверяется первым, иначе «Processed file
# for supplier» попадёт во входящие.
FIELD_DIR = [
    (re.compile(r"offer\s+from\s+us|наше?\s+кп|request\s+file|запрос\s+цен"
                r"|запрос\s+оферт|processed\s+file\s+for\s+supplier", re.I), "наш запрос"),
    (re.compile(r"result\W*ткп|economics\s+of\s+the\s+project", re.I), "наша цена"),
    (re.compile(r"offer\s+from\s+supplier|кп\s+поставщика|offer\W*old"
                r"|processed\s+offer|order\s+confirmation", re.I), "входящее"),
    (re.compile(r"техническ\w*\s+специф|technical\s+data\s+from\s+customer"
                r"|customer\s+request", re.I), "заявка"),
]

# Порядок разбора при пределе --max-files. Предел есть всегда, значит вопрос не
# «разберём ли всё», а «что останется неразобранным». Остаться должны картинки
# канбана, а не выставленные цены.
DIR_PRIORITY = {"наша цена": 0, "входящее": 1, "неизвестно": 2,
                "заявка": 3, "наш запрос": 4}   # порядок разбора, не фильтр
# внутри «неизвестно»: вложение письма ценнее канбан-картинки
FIELD_PRIORITY = [
    (re.compile(r"вложение\s+(дела|комментария)|result\s+file|other\s*\(important\)"
                r"|мануал|delivery\s+agreement", re.I), 0),
    (re.compile(r"tender\s+platform|картинка|kanban|purchasing\s+method"
                r"|documents,\s*bot|bank\s+details", re.I), 2),
]


def dir_from_field(name: str) -> str:
    """Направление по имени поля. При сомнении «неизвестно», а не «наш запрос»:
    отброшенное КП мы не увидим никогда, а наш же запрос виден сразу по
    отсутствию цен (правило 7 CLAUDE.md — признак либо защищает, либо обвиняет).
    """
    for rx, d in FIELD_DIR:
        if rx.search(name or ""):
            return d
    return "неизвестно"


def rank(c: dict) -> tuple:
    fp = 1
    for rx, p in FIELD_PRIORITY:
        if rx.search(c.get("field_name") or ""):
            fp = p
            break
    return (DIR_PRIORITY.get(c.get("direction"), 2), fp)


# Что берём в разбор. «Заявка» здесь ТОЖЕ, и это исправление моего же правила
# по замеру. Я считал, что в требованиях заказчика цен нет, и исключал их —
# боевой прогон 17.09.2026 показал обратное: в файлах поля «Техническая
# спецификация» 1 431 строка с парой «артикул — цена» и 394 артикула заявки.
# Похоже, ответ заказчику возвращают в том же шаблоне, в котором пришёл запрос,
# и цены оказываются в файле с этим именем. Правило, исключавшее их, теряло
# треть покрытия.
#
# «Наш запрос» остаётся исключённым по умолчанию: там цен тоже хватает (1 345
# строк в одном файле), но это наши ориентиры поставщику, а не выставленное
# заказчику и не присланное контрагентом. Кто хочет и их — ставит --all-files.
WANTED = {"входящее", "наша цена", "заявка"}

PN_RES = [
    r"\b\d{6,7}-\d{1,4}(?:-\d{1,4})?\b", r"\b\d{6}C\d\b", r"\b64/\d{8}/\d{1,4}\b",
    r"\b[MR][WTU]\d{4,5}[A-Z]?(?:/\d+)?\b", r"\bCT\d{3,5}[A-Z]?/\d+\b",
    r"\bSP0\d{5}\b", r"\bESP0\d{5}\b", r"\bE\d{6}-\d{3}\b",
    r"\b\d{3,4}[A-Z]{1,2}\d{4}[PG]\d{3,4}\b", r"\b\d{8}P\d{3}\b",
    r"\b\d{4}M\d{2}P\d{2}\b", r"\b\d{5,6}/\d{2,3}\b",
    r"\b6ES7[\dA-Z-]{8,}\b", r"\b1794-[A-Z0-9]{2,8}\b", r"\b1756-[A-Z0-9]{2,8}\b",
    r"\b[A-Z0-9]{2,}[-/][A-Z0-9]{2,}(?:[-/][A-Z0-9]{1,})?\b",
]
NBSP = "\u00a0"
CYR = str.maketrans({"А": "A", "В": "B", "С": "C", "Е": "E", "К": "K", "М": "M",
                     "Н": "H", "О": "O", "Р": "P", "Т": "T", "Х": "X"})
NUM_RE = re.compile(r"(?<![\dA-Za-z./-])(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d{1,2})?"
                    r"|\d+[.,]\d{1,2}|\d{2,9})(?![\dA-Za-z/-])")
CUR_RE = re.compile(r"\b(USD|EUR|RUB|RUR|GBP|CNY|руб|долл|евро|\$|€|₽)\b", re.I)
H_PRICE = re.compile(r"цена|стоим|price|amount|сумма|тариф|ставка", re.I)
H_QTY = re.compile(r"кол-?в|количест|qty|quantity|шт\b|ед\b", re.I)
H_PN = re.compile(r"артик|парт|номер|part|p/?n|каталож|обознач", re.I)
H_NAME = re.compile(r"наимен|назван|описан|name|descr|номенкл", re.I)


def norm_pn(s: str) -> str:
    return re.sub(r"\s+", "", str(s).upper().translate(CYR))


def find_pns(text: str) -> list[str]:
    """Артикулы из свободного текста.

    Ищем в двух видах строки сразу. В виде с пробелами работают границы слова:
    «прокладка 304649-100» отдаёт номер, а в склеенном «ПРОКЛАДКА304649-100»
    граница \b перед цифрами пропадает и номер теряется. Склеенный вид нужен
    для pdf, где номер рвётся переносом строки.
    """
    spaced = str(text).upper().translate(CYR)
    glued = re.sub(r"\s+", "", spaced)
    out: list[str] = []
    seen = set()
    for t in (spaced, glued):
        for rx in PN_RES:
            for m in re.findall(rx, t):
                if len(m) >= 5 and m not in seen:
                    seen.add(m)
                    out.append(m)
    return out


def pn_from_cell(v) -> str:
    """Артикул из ячейки, на которую указал заголовок.

    Здесь нельзя опираться на паттерны: у Cummins и Solar номера чисто цифровые
    — 3420932, 1017891, 199515. В свободном тексте такие паттерны хватали бы
    количества и цены, поэтому там их нет намеренно. Но если колонка названа
    «Артикул», доверяем заголовку, а не догадке.
    """
    if v in (None, ""):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = norm_pn(v)
    if not t or len(t) < 5 or len(t) > 40:
        return ""
    if not re.search(r"\d", t):        # без цифры это слово, а не артикул
        return ""
    return t


def to_num(s):
    """Число из ячейки.

    «3 299,51 USD» и «86,89 руб/шт» — тоже числа: в ТКП валюту и единицу пишут
    в той же ячейке, и без отсечения цена теряется целиком.
    """
    if s in (None, "") or isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s) if s > 0 else None
    t = str(s).replace(NBSP, " ").strip()
    t = CUR_RE.sub(" ", t)                                   # USD, руб, $, € — вон
    t = re.sub(r"(?i)\b(шт|ед|компл|за|вкл|без|ндс|vat|pcs?|each)\b", " ", t)
    t = re.sub(r"[^\d., -]", " ", t).strip(" -/")
    t = re.sub(r"[ ](?=\d{3}(?:\D|$))", "", t).strip()       # 1 234 → 1234
    if not t:
        return None
    t = t.split()[0]                    # осталось несколько чисел — берём первое
    t = t.replace(",", ".") if t.count(",") == 1 and t.count(".") == 0 else t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


# ------------------------------------------------------------------ разбор файлов

def rows_from_xlsx(content: bytes) -> list[tuple[str, int, list]]:
    import openpyxl
    out = []
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    for ws in wb.worksheets:
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            if row and any(c not in (None, "") for c in row):
                out.append((ws.title, i, list(row)))
    return out


def rows_from_xlsx_raw(content: bytes) -> list[tuple[str, int, list]]:
    """Тот же xlsx, но читаемый напрямую как архив с разметкой.

    ЗАЧЕМ. Замер 18.09.2026 по описи вложений: продажная сторона заявки —
    восемнадцать файлов «result.xlsx» примерно по 5 КБ, приложенных к полю
    «Result, ТКП». Они скачались без единой ошибки и НЕ ОТКРЫЛИСЬ обычным
    путём. Пока они не читаются, сопоставить продажу с закупкой построчно
    нельзя, а значит и марж по заявке не существует.

    Обычный читатель книг придирчив к устройству файла: ему нужны и описание
    книги, и связи листов, и общий словарь строк на своих местах. Выгрузка из
    учётной системы кладёт их иначе, и читатель отказывается ещё до данных.
    Здесь данные берутся прямо: словарь строк из sharedStrings, значения ячеек
    из каждого листа, номер строки — из её собственного признака.

    Это ЗАПАСНОЙ путь, а не замена: он не считает формулы и не знает форматов.
    Поэтому он включается только после отказа основного.
    """
    import zipfile
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(content))
    names = z.namelist()

    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.iter(f"{ns}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))

    out: list[tuple[str, int, list]] = []
    sheets = sorted(n for n in names
                    if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    for sheet in sheets:
        title = sheet.rsplit("/", 1)[-1][:-4]
        root = ET.fromstring(z.read(sheet))
        for row in root.iter(f"{ns}row"):
            i = int(row.get("r") or (len(out) + 1))
            cells = []
            for c in row.iter(f"{ns}c"):
                v = c.find(f"{ns}v")
                text = v.text if v is not None else None
                if c.get("t") == "s" and text is not None:
                    idx = int(text)
                    text = shared[idx] if 0 <= idx < len(shared) else ""
                elif c.get("t") == "inlineStr":
                    isx = c.find(f"{ns}is")
                    text = ("".join(t.text or "" for t in isx.iter(f"{ns}t"))
                            if isx is not None else None)
                cells.append(text)
            if any(x not in (None, "") for x in cells):
                out.append((title, i, cells))
    return out


def rows_from_xls(content: bytes) -> list[tuple[str, int, list]]:
    import xlrd
    out = []
    bk = xlrd.open_workbook(file_contents=content)
    for sh in bk.sheets():
        for i in range(sh.nrows):
            vals = sh.row_values(i)
            if any(v not in (None, "") for v in vals):
                out.append((sh.name, i + 1, vals))
    return out


def rows_from_text(content: bytes) -> list[tuple[str, int, list]]:
    txt = content.decode("utf-8", errors="ignore")
    out = []
    for i, line in enumerate(txt.splitlines(), 1):
        if line.strip():
            cells = re.split(r"[\t;]|(?:\s{2,})", line.strip())
            out.append(("text", i, [c for c in cells if c != ""]))
    return out


def rows_from_pdf(content: bytes) -> list[tuple[str, int, list]]:
    from pypdf import PdfReader
    out = []
    rd = PdfReader(io.BytesIO(content))
    for pno, page in enumerate(rd.pages, 1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            # в pdf нет колонок: строка приходит текстом. Отдать её одной
            # ячейкой нельзя — тогда из «964587C1 109 2,93» ценой оказывался
            # сам артикул. Режем по двум пробелам, а если их нет — по одному.
            cells = re.split(r"\s{2,}", line)
            if len(cells) < 2:
                cells = line.split()
            out.append((f"стр.{pno}", i, cells))
    return out


# Распознавание. Боевой прогон по «Энергосети» 17.09.2026: из 93 файлов 24
# оказались картинками и 3 сканами без текстового слоя — 29 % файлов, и цены в
# них есть, просто не в виде текста. Способ взят из base/reparse.py, где он уже
# работает на этом же корпусе: pytesseract с языками rus+eng, мелкие картинки
# увеличиваются вдвое (иначе распознаются плохо).
#
# Отсутствие tesseract — НЕ ошибка разбора: инструмент должен честно сказать
# «распознавание недоступно», а не «не разобрался». Иначе оценка объёма работы
# по сканам занижается, как это уже было (правило 15 CLAUDE.md).
OCR_LANG = "rus+eng"
OCR_MAX_PAGES = 12
OCR_MIN_CHARS = 40          # меньше — шум распознавания, а не текст


def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_image_text(content: bytes) -> str:
    import pytesseract
    from PIL import Image
    img = Image.open(io.BytesIO(content))
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    if max(img.size) < 900:
        img = img.resize((img.width * 2, img.height * 2))
    return pytesseract.image_to_string(img, lang=OCR_LANG)


def ocr_pdf_text(content: bytes) -> str:
    try:
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(content, dpi=200, first_page=1,
                                   last_page=OCR_MAX_PAGES)
    except Exception:
        return ""
    import pytesseract
    out = []
    for pg in pages:
        try:
            out.append(pytesseract.image_to_string(pg, lang=OCR_LANG))
        except Exception:
            continue
    return "\n".join(out)


def rows_from_ocr(name: str, content: bytes) -> tuple[list, str]:
    """Строки из картинки или скана. Возвращает (строки, способ)."""
    if not ocr_available():
        return [], "распознавание недоступно (нет tesseract)"
    low = (name or "").lower()
    try:
        txt = (ocr_pdf_text(content) if low.endswith(".pdf")
               else ocr_image_text(content))
    except Exception as e:
        return [], f"распознавание не удалось ({type(e).__name__})"
    if len(txt.strip()) < OCR_MIN_CHARS:
        return [], "распознано, но текста нет"
    return rows_from_text(txt.encode("utf-8")), "распознавание"


PARSERS = [
    ((".xlsx", ".xlsm"), rows_from_xlsx, "xlsx"),
    ((".xls",), rows_from_xls, "xls"),
    ((".csv", ".txt", ".tsv"), rows_from_text, "текст"),
    ((".pdf",), rows_from_pdf, "pdf"),
]


def sniff(content: bytes) -> str:
    """Расширение по магическим байтам.

    Обязательно, а не «на всякий случай»: холостой прогон 17.09.2026 показал,
    что ИМЕНИ НЕТ у 1986 файлов из 1998 — crm.item.list в файловом объекте
    отдаёт только id и ссылку. Разбор выбирается по расширению, поэтому без
    этого 99 % файлов получили бы «формат не поддержан», и прогон отчитался бы
    нулём при полностью рабочей выгрузке.
    """
    if content[:4] == b"%PDF":
        return ".pdf"
    if content[:2] == b"PK":
        head = content[:4000]
        if b"xl/" in head or b"workbook.xml" in head:
            return ".xlsx"
        return ".zip"
    if content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return ".xls"          # старый формат Office; xlrd разберёт
    if content[:5] == b"{\\rtf" or content[:4] == b"\x7fELF":
        return ".bin"
    if content[:3] in (b"\xff\xd8\xff",) or content[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    head = content[:4000]
    # НОЛЬ-БАЙТ РЕШАЕТ РАНЬШЕ КОДИРОВКИ. b"\x00\x01binary" — валидный utf-8, и
    # проверка одной декодировкой объявляла двоичный чертёж текстом. Текстовый
    # файл ноль-байтов не содержит.
    if b"\x00" in head:
        return ".bin"
    for enc in ("utf-8", "cp1251"):
        try:
            head.decode(enc)
            return ".txt"
        except UnicodeDecodeError:
            continue
    return ".bin"


def rows_from_zip(content: bytes) -> tuple[list, str]:
    """Строки из архива: КП часто присылают запакованным.

    Замер боевого прогона по «НВН» 17.09.2026: десять файлов получили «формат не
    поддержан», а base/parse_archives.py в этом же репозитории существует именно
    потому, что предложения приходят архивами. Внутрь заходим на ОДИН уровень:
    архив в архиве бывает, но редко, а бесконечная вложенность — способ
    подвесить прогон.

    Имя участника архива есть, поэтому разбор выбирается по нему, а не по
    магическим байтам, и это надёжнее.
    """
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except Exception as e:
        return [], f"архив не открылся ({type(e).__name__})"
    rows, opened, skipped = [], 0, 0
    for name in z.namelist():
        low = name.lower()
        if low.endswith("/") or not low.endswith(
                (".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".tsv", ".pdf")):
            skipped += 1
            continue
        try:
            body = z.read(name)
        except Exception:
            skipped += 1
            continue
        got, _ = parse(name, body)
        if got:
            # лист помечаем именем файла внутри архива, иначе непонятно, откуда
            # взялась строка
            rows += [(f"{name}:{sheet}", r, cells) for sheet, r, cells in got]
            opened += 1
        else:
            skipped += 1
    if not rows:
        return [], f"архив: разбираемого внутри нет (пропущено {skipped})"
    return rows, f"архив ({opened} файлов внутри, пропущено {skipped})"


def parse(name: str, content: bytes):
    """Возвращает (строки, способ). Отказ выносится ПО ФАЙЛУ, а не по строке —
    правило 13 CLAUDE.md: построчный отказ теряет до 40 % позиций."""
    low = (name or "").lower()
    if not low.endswith(tuple(e for exts, _, _ in PARSERS for e in exts)):
        low = (name or "") + sniff(content)
        low = low.lower()
    for exts, fn, tag in PARSERS:
        if low.endswith(exts):
            try:
                rows = fn(content)
            except Exception as e:
                # ОШИБКА чтения pdf — это тоже случай скана: извлекатель текста
                # спотыкается ровно там, где текстового слоя нет. Отдавать такой
                # файл как «не разобрался» значит терять его цены совсем.
                if low.endswith(".pdf"):
                    got, how = rows_from_ocr(low, content)
                    if got:
                        return got, how
                    return [], f"{tag}: не разобрался ({type(e).__name__}); {how}"
                # Книга, которую придирчивый читатель не открыл, ещё не потеряна:
                # пробуем прочитать её напрямую как архив с разметкой. Этот путь
                # добавлен 18.09.2026 из-за восемнадцати файлов продажной стороны
                # заявки, которые скачались без ошибок и не открылись.
                if low.endswith((".xlsx", ".xlsm")):
                    try:
                        rows = rows_from_xlsx_raw(content)
                    except Exception as e2:                      # noqa: BLE001
                        return [], (f"{tag}: не разобрался ({type(e).__name__}), "
                                    f"прямое чтение тоже ({type(e2).__name__})")
                    if rows:
                        return rows, f"{tag} прямым чтением архива"
                    return [], f"{tag}: не разобрался ({type(e).__name__}), листы пусты"
                return [], f"{tag}: не разобрался ({type(e).__name__})"
            # pdf без текстового слоя — это скан, и он идёт в распознавание, а
            # не объявляется пустым (правило 15 CLAUDE.md: статус не должен врать)
            if not rows and low.endswith(".pdf"):
                return rows_from_ocr(low, content)
            return rows, tag
    if low.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
        return rows_from_ocr(low, content)
    if low.endswith((".zip", ".xlsx", ".docx")) or content[:2] == b"PK":
        return rows_from_zip(content)
    return [], "формат не поддержан"


def header_map(rows: list) -> dict:
    """Ищем строку заголовков в первых 25 строках и запоминаем, какая колонка чем."""
    best, best_hits = {}, 0
    for _sheet, _i, cells in rows[:25]:
        m, hits = {}, 0
        for j, c in enumerate(cells):
            s = str(c or "")
            if H_PRICE.search(s) and "price" not in m:
                m["price"] = j; hits += 1
            elif H_QTY.search(s) and "qty" not in m:
                m["qty"] = j; hits += 1
            elif H_PN.search(s) and "pn" not in m:
                m["pn"] = j; hits += 1
            elif H_NAME.search(s) and "name" not in m:
                m["name"] = j; hits += 1
        if hits > best_hits:
            best, best_hits = m, hits
    return best if best_hits >= 2 else {}


def price_rows(rows: list, hdr: dict) -> list[dict]:
    """Пары «артикул — цена». Если заголовки нашлись, берём колонки по ним; иначе
    ищем в строке артикул и число. Исходная строка сохраняется целиком: без неё
    следующая ошибка снова будет неизмеримой (правило 16 CLAUDE.md)."""
    out = []
    for sheet, i, cells in rows:
        joined = " | ".join(str(c) for c in cells if c not in (None, ""))
        if not joined:
            continue
        pn = price = None
        rule = ""
        if hdr.get("pn") is not None and hdr["pn"] < len(cells):
            pn = pn_from_cell(cells[hdr["pn"]]) or None
        if pn is None:
            cand = find_pns(joined)
            pn = cand[0] if cand else None
        if pn is None and hdr.get("pn") is None:
            # в ТКП артикул почти всегда в первых двух колонках; смотрим только
            # их, чтобы не принять за номер количество из середины строки
            for c in cells[:2]:
                cand2 = pn_from_cell(c)
                if cand2 and (to_num(c) is None or re.fullmatch(r"\d{6,9}", cand2)):
                    pn = cand2
                    break
        if hdr.get("price") is not None and hdr["price"] < len(cells):
            price = to_num(cells[hdr["price"]])
            rule = "колонка «цена» по заголовку"
        if price is None:
            # без заголовка: берём НАИБОЛЬШЕЕ число строки, но не то, что стоит в
            # колонке количества — иначе цена и количество путаются местами
            # из кандидатов вон и колонка количества, и та ячейка, откуда взят
            # артикул: без этого цифровой номер 3420932 объявляется ценой
            skip = {hdr.get("qty"), hdr.get("pn")}
            if pn:
                skip |= {j for j, c in enumerate(cells)
                         if c not in (None, "") and pn_from_cell(c) == pn}
            nums = [to_num(c) for j, c in enumerate(cells) if j not in skip]
            nums = [n for n in nums if n is not None]
            if nums:
                price = nums[-1]
                rule = "последнее число строки (заголовок не опознан)"
        if pn and price:
            # ГРАДУС ЦЕНЫ. Замер 18.09.2026 по выгрузке «Энергосети»: из 6 619
            # значений только 317 взяты из колонки «цена» по заголовку, а 6 302 —
            # правилом «последнее число строки». И это правило берёт не цену: в
            # Quotation p76057.pdf оно вытащило НОМЕРА ПОЗИЦИЙ (94, 101, 104,
            # 105, 107 — подряд, 187 пар из 474 идут с шагом ровно 1), а в
            # файлах-заявках — количество. Счётчики по вилкам, построенные на
            # этом, дали 222 «заниженные» строки вместо 31.
            #
            # Значение НЕ выбрасывается: иногда оно и есть цена, а решает
            # открытый файл. Но ценой оно не называется, и всякий, кто считает
            # деньги, обязан взять только градус «цена по колонке».
            graded = rule.startswith("колонка")
            out.append({
                "pn": pn, "price": price, "sheet": sheet, "row": i,
                "currency": (CUR_RE.search(joined) or [""])[0] if CUR_RE.search(joined) else "",
                "raw": joined[:400], "class_rule": rule,
                "is_price": graded,
                "price_grade": ("цена по колонке" if graded
                                else "догадка: последнее число строки, ценой не является"),
            })
    return out



# ------------------------------------------------------------ сбор кандидатов
# Порядок ровно такой, какой задал владелец: сначала читаем сделку, потом
# запросы поставщикам, потом видим файлы — и лишь затем качаем.


def say(msg: str) -> None:
    """Прогресс в журнал. flush обязателен: stdout в Actions буферизован
    блоками, и без него прогон выглядит повисшим, а не идущим."""
    print(msg, flush=True)


def file_fields(bx: BitrixClient, entity: int) -> dict:
    """Файловые поля сущности: код → название. Один вызов за прогон.

    Маска select=["*"] файловые поля НЕ возвращает — это и было причиной, по
    которой первая версия не получила ни одного файла.
    """
    r = bx.call("crm.item.fields", {"entityTypeId": entity}) or {}
    fields = (r.get("fields") if isinstance(r, dict) else None) or {}
    return {k: (v.get("title") or k) for k, v in fields.items()
            if isinstance(v, dict) and v.get("type") == "file"}


def objs(v) -> list:
    """Файловые объекты из значения поля. Три разные формы, и все встречаются.

    Проверка документации 17.09.2026 поймала форму, на которой прежний код
    терял файлы молча: FILES комментария таймлайна приходит СЛОВАРЁМ, где ключ
    совпадает с id файла — {"10": {id, name, urlDownload, authorId}}. Прежний
    objs получал такой словарь, считал его ОДНИМ объектом, не находил в нём
    ссылки и возвращал пустоту. Файл при этом есть.

    Требуем ссылку или id: у файла CRM это urlMachine, у файла Диска — числовой
    ID, по которому ссылку ещё надо спросить у disk.file.get.
    """
    if not v:
        return []
    items: list = []
    if isinstance(v, list):
        items = v
    elif isinstance(v, dict):
        # словарь по id: все значения — словари. Иначе это сам объект.
        vals = [x for x in v.values() if isinstance(x, dict)]
        items = vals if vals and len(vals) == len(v) else [v]
    else:
        return []
    out = []
    for o in items:
        if not isinstance(o, dict):
            continue
        if o.get("urlMachine") or o.get("downloadUrl") or o.get("ID") or o.get("id"):
            out.append(o)
    return out


def cand(origin: str, field: str, field_name: str, direction: str, o: dict,
         extra: str = "", via: str = "") -> dict:
    """Кандидат на скачивание.

    Поле via говорит, КАК брать байты, и это не косметика. Документация:
    у файлового поля CRM рабочая ссылка — urlMachine с одноразовым токеном; а
    вот urlDownload из FILES комментария таймлайна токена НЕ содержит, и
    серверный клиент получит по нему html-страницу вместо файла. Там нужен
    disk.file.get по id. Перепутать эти два пути — значит разобрать страницу
    входа как спецификацию.
    """
    fid = str(o.get("id") or o.get("ID") or "")
    url = o.get("urlMachine") or ""
    if not via:
        via = "ссылка" if url else "диск"
    return {
        "origin": origin, "field": field, "field_name": field_name,
        "direction": direction, "via": via,
        "file_id": fid,
        "file_name": o.get("name") or o.get("fileName") or o.get("NAME") or "",
        "url": url,
        "author": str(o.get("authorName") or o.get("authorId") or ""),
        "context": extra,
    }


def from_deal(bx: BitrixClient, did: int, ffields: dict) -> list:
    """Шаг 1: файловые поля самой карточки сделки.

    Через crm.item.list с entityTypeId=2, а НЕ crm.deal.list: у последнего
    ссылки ведут на crm_show_file.php, где нужна сессия портала, и вебхук
    получает страницу входа с кодом 200.
    """
    if not ffields:
        return []
    r = bx.call("crm.item.list", {"entityTypeId": DEAL_ENTITY, "filter": {"@id": [did]},
                                  "select": ["id", "title"] + list(ffields)}) or {}
    items = (r.get("items") if isinstance(r, dict) else None) or []
    out = []
    for it in items:
        for code, title in ffields.items():
            for o in objs(it.get(code)):
                # направление читается по ИМЕНИ поля сделки: «Offer from us» —
                # наше, «Offer from supplier(s)» — входящее, «Result, ТКП» и
                # «Economics of the project» — наши выставленные цены. До этой
                # правки все 1509 файлов сделок шли в «неизвестно».
                out.append(cand(f"сделка {did}", code, title,
                                dir_from_field(title), o))
    return out


def from_rfq(bx: BitrixClient, did: int, rfields: dict | None = None) -> tuple[list, int]:
    """Шаг 2: привязанные записи СП-166 «Запросы поставщикам».

    Здесь направление читается прямо: имя файлового поля говорит, наш это
    запрос или присланное поставщиком КП. Список полей берётся у портала
    (`rfields`), а не только из зашитых восьми: на живом портале их больше, и
    зашитый список молча терял всё, чего в нём нет. Восемь известных остаются
    как оговорка поверх — там направление выверено вручную.
    """
    fields = dict(rfields or {}) or {c: t for c, (t, _) in RFQ_FILE_FIELDS.items()}
    # Выборку держим короткой: поля, которые мы всё равно не скачиваем (наш
    # исходящий запрос, заявка заказчика), спрашивать незачем. Замеров, что
    # длинная выборка замедляет обход, У МЕНЯ НЕТ: и с восемью зашитыми полями,
    # и со всеми полями портала обход 137 сделок занял 4 мин 52 с. Это
    # предосторожность, а не исправление измеренной беды.
    fields = {c: t for c, t in fields.items()
              if c in RFQ_FILE_FIELDS
              or dir_from_field(t) not in ("наш запрос", "заявка")}
    try:
        items = bx.list_items(SPA_RFQ, filter={"parentId2": did},
                              select=RFQ_SELECT + list(fields))
    except Exception as e:
        say(f"    СП-166: не прочитались ({type(e).__name__})")
        return [], 0
    out = []
    for it in items:
        rid = it.get("id")
        sup = str(it.get("ufCrm18Supplier") or "")
        for code, title in fields.items():
            known = RFQ_FILE_FIELDS.get(code)
            direction = known[1] if known else dir_from_field(title)
            for o in objs(it.get(code)):
                out.append(cand(f"СП-166 {rid}", code, known[0] if known else title,
                                direction, o,
                                extra=f"поставщик {sup}" if sup else ""))
    return out, len(items)


def from_timeline(bx: BitrixClient, did: int) -> list:
    """Шаг 3: комментарии таймлайна. Сюда сорсер кладёт полученное КП.

    Две поправки по документации, каждая стоила бы потерянных файлов.
    Первая: FILES приходит словарём по id, и objs это теперь умеет.
    Вторая: urlDownload здесь БЕЗ токена — байты берутся через disk.file.get,
    поэтому via="диск".

    Автор берётся у САМОГО ФАЙЛА (authorId/authorName внутри записи FILES), а
    не у комментария: комментарий мог написать один человек, а файл приложить
    другой, и для направления важен второй.
    """
    out = []
    try:
        r = bx.call("crm.timeline.comment.list", {
            "filter": {"ENTITY_ID": did, "ENTITY_TYPE": "deal"},
            "select": ["ID", "COMMENT", "FILES", "AUTHOR_ID", "CREATED"]}) or {}
        rows = (r.get("result") if isinstance(r, dict) else None) or (
            r if isinstance(r, list) else [])
    except Exception as e:
        say(f"    таймлайн: не прочитался ({type(e).__name__})")
        return out
    for c in rows:
        txt = str(c.get("COMMENT") or "")[:200]
        for o in objs(c.get("FILES")):
            out.append(cand(f"комментарий {c.get('ID')}", "FILES",
                            "вложение комментария", direction_from_text(txt), o,
                            extra=txt, via="диск"))
    return out


INCOMING_WORDS = re.compile(
    r"получ|присл|прише?л|во влож|предлож|оффер|offer|quotat|proposal|"
    r"ответ|от поставщик|цены|прайс", re.I)
OUTGOING_WORDS = re.compile(
    r"отправ|направ|запрос|высла|разосла|наше тз|наш запрос|rfq|request", re.I)


def direction_from_text(txt: str) -> str:
    """Направление по тексту рядом с файлом. Слабый признак, но лучше, чем ничего.

    Асимметрия цены ошибки: ошибочно отброшенное КП мы не увидим никогда, а
    ошибочно взятый наш же запрос виден сразу по отсутствию цен. Поэтому при
    любом сомнении — «неизвестно», а не «наш запрос».
    """
    if not txt:
        return "неизвестно"
    inc, out = bool(INCOMING_WORDS.search(txt)), bool(OUTGOING_WORDS.search(txt))
    if inc and not out:
        return "входящее"
    if out and not inc:
        return "наш запрос"
    return "неизвестно"


def from_activities(bx: BitrixClient, did: int) -> tuple[list, list]:
    """Шаг 4: дела сделки — письма и звонки. Возвращает (файлы, тела писем).

    Три поправки по документации:
    1. Тело письма приходит прямо в DESCRIPTION — скачивать нечего, и цена
       вполне может лежать в самом письме, а не во вложении. Раньше я этого не
       брал вовсе и терял такие КП целиком.
    2. DIRECTION есть только у писем (TYPE_ID=4). У задач, звонков и
       уведомлений он пустой, и считать пустоту «исходящим» нельзя.
    3. FILES у дела — тип diskfile, и его ID это НАСТОЯЩИЙ идентификатор Диска,
       в отличие от id файлового поля CRM. Значит via="диск".

    И главное ограничение, которое надо помнить: КП, которое сорсер скачал с
    личной почты и приложил руками, дела не создаёт вовсе — DIRECTION по нему
    не появится никогда. Поэтому признак отбрасывает наше надёжно, а вот
    подтверждает чужое далеко не всегда.
    """
    out, bodies = [], []
    try:
        rows = bx.list_paged("crm.activity.list", {
            "filter": {"OWNER_ID": did, "OWNER_TYPE_ID": DEAL_ENTITY},
            "select": ["ID", "SUBJECT", "DESCRIPTION", "DIRECTION", "FILES",
                       "STORAGE_ELEMENT_IDS", "PROVIDER_TYPE_ID", "TYPE_ID",
                       "AUTHOR_ID"]})
    except Exception as e:
        say(f"    дела: не прочитались ({type(e).__name__})")
        return out, bodies
    for a in rows:
        # DIRECTION: 1 входящее, 2 исходящее — но только у письма
        d = str(a.get("DIRECTION") or "")
        is_mail = str(a.get("TYPE_ID") or "") == "4"
        if is_mail and d == "1":
            direction = "входящее"
        elif is_mail and d == "2":
            direction = "наш запрос"
        else:
            direction = "неизвестно"
        subj = str(a.get("SUBJECT") or "")[:200]
        if direction == "неизвестно":
            direction = direction_from_text(subj)
        body = str(a.get("DESCRIPTION") or "")
        if body and direction != "наш запрос":
            # тело письма разбирается как текст: цена могла прийти прямо в нём
            bodies.append({"origin": f"письмо {a.get('ID')}", "direction": direction,
                           "subject": subj, "text": body})
        for o in objs(a.get("FILES")):
            out.append(cand(f"дело {a.get('ID')}", "FILES", "вложение дела",
                            direction, o, extra=subj, via="диск"))
    return out, bodies


def from_chat(bx: BitrixClient, did: int) -> list:
    """Шаг 5: чат сделки, включая открытые линии с внешним контрагентом.

    Владелец прямо сказал, что КП кидают в переписку. Скоуп im может быть не
    выдан — тогда фиксируем причину, а не молчим.
    """
    out = []
    try:
        cid = bx.deal_chat_id(did)
    except Exception as e:
        say(f"    чат: id не получен ({type(e).__name__})")
        return out
    if not cid:
        return out
    try:
        msgs = bx.chat_messages(cid, limit=200)
    except Exception as e:
        say(f"    чат {cid}: сообщения не прочитались ({type(e).__name__})")
        return out
    for m in msgs or []:
        txt = str(m.get("text") or m.get("TEXT") or "")[:200]
        direction = direction_from_text(txt)
        for key in ("files", "FILES", "attach", "ATTACH"):
            for o in objs(m.get(key)):
                out.append(cand(f"чат {cid}", key, "файл чата", direction, o, extra=txt))
    return out


# ---------------------------------------------------------------- скачивание

def fetch(url: str, timeout: int = 45):
    """Байты по машинной ссылке. Прямой GET, без вызовов disk.*.

    urlMachine уже лежит в объекте и содержит одноразовый токен. Прежняя версия
    спрашивала ссылку у disk.file.get и disk.attachedObject.get ПЕРЕД тем как
    посмотреть в объект — два дросселированных вызова на каждый файл впустую, а
    при ошибке шесть повторов с паузой до 3,5 минут. Отсюда и 28 минут.
    """
    import requests
    try:
        rr = requests.get(url, timeout=timeout)
    except Exception as e:
        return None, f"сеть: {type(e).__name__}", ""
    if rr.status_code != 200:
        return None, f"HTTP {rr.status_code}", ""
    body = rr.content
    if len(body) < 200:
        return None, "пусто", ""
    # вебхук без прав получает страницу входа с кодом 200 — это измерено в
    # base/collect_attachments.py. Такую «спецификацию» разбирать нельзя.
    head = body[:600].lower()
    if b"<html" in head and (b"login" in head or b"auth" in head or b"bitrix" in head):
        return None, "страница входа вместо файла (нет прав на ссылку)", ""
    return body, "ок", name_from(rr.headers.get("Content-Disposition", ""))


def name_from(cd: str) -> str:
    """Имя файла из заголовка отдачи — единственное место, где оно есть.

    В файловом объекте crm.item.list имени нет (измерено: 1986 из 1998 без
    имени), а `Content-Disposition` его несёт. Способ взят из
    base/fetch_files.py, где он уже проверен на живом портале. Возможны две
    формы: RFC 5987 с процентным кодированием и простая в кавычках.
    """
    from urllib.parse import unquote
    m = re.search(r"filename\*\s*=\s*utf-8''([^;]+)", cd or "", re.I)
    if m:
        return unquote(m.group(1)).strip().strip('"')
    m = re.search(r'filename\s*=\s*"?([^";]+)', cd or "", re.I)
    return m.group(1).strip() if m else ""


def disk_url(bx: BitrixClient, fid: str) -> tuple[str, str]:
    """Ссылка на файл Диска по его id и ПРИЧИНА, если ссылки нет.

    Нужна там, где прямой ссылки нет или она без токена: вложения комментариев
    таймлайна и дел. retries=1 намеренно — нехватка прав и «нет такого файла»
    неустранимы, и повторять их шесть раз с растущей паузой значит подвесить
    прогон, как это и случилось в первый раз.
    """
    seen: list[str] = []
    for method, key in (("disk.file.get", "DOWNLOAD_URL"),
                        ("disk.attachedObject.get", "DOWNLOAD_URL")):
        try:
            r = bx.call(method, {"id": fid}, retries=1) or {}
        except Exception as e:                                   # noqa: BLE001
            # Причину НЕ глотаем. Прежняя версия писала по всем неудачам одно и
            # то же — «нет скоупа disk или файла нет», две гипотезы в одной
            # строке. Зонд 18.09.2026 показал, что обе неверны: право disk
            # выдано, Диск отвечает, а по нашим файлам приходит ACCESS_DENIED —
            # то есть у сотрудника, чьим вебхуком мы ходим, нет прав на эти
            # вложения. Неизмеренная причина стоила названного не тем действия
            # владельца в отчёте.
            m = re.search(r":\s*([A-Z_]{3,40})", str(e))
            seen.append(f"{method.split('.')[1]}: {m.group(1) if m else type(e).__name__}")
            continue
        if isinstance(r, dict):
            u = r.get(key) or (r.get("result") or {}).get(key) if isinstance(
                r.get("result"), dict) else r.get(key)
            if u:
                return u, ""
            seen.append(f"{method.split('.')[1]}: ответил без ссылки")
    return "", "; ".join(seen)


def write_index(path: str, scope: str, payload: dict) -> None:
    """Опись НАКОПИТЕЛЬНАЯ по охватам, а не перезаписываемая.

    Прогон 17.09.2026 по слову «Энергосети» затёр картину по слову «ЛУКОЙЛ»:
    1998 файлов и 137 сделок пропали из файла, а документ «что есть в системе»
    на них и опирался. Каждый охват (ключевое слово или id сделки) — своя
    запись; прогон обновляет только свою и не касается чужих.

    Верхних полей `inventory` / `deals` в файле больше НЕТ: они создавали
    иллюзию, будто опись описывает всё, тогда как описывали они последний
    прогон. Читатели обязаны выбрать охват или сложить их сами.
    """
    pth = Path(path)
    pth.parent.mkdir(parents=True, exist_ok=True)
    doc = {}
    if pth.exists():
        try:
            doc = json.loads(pth.read_text(encoding="utf-8"))
        except ValueError:
            doc = {}
    scopes = doc.get("scopes")
    if not isinstance(scopes, dict):
        # старый однопрогонный формат: сохраняем его как охват «(прежний прогон)»,
        # чтобы прежние цифры не исчезли молча
        scopes = {}
        if doc.get("inventory"):
            scopes["(прежний прогон)"] = {
                k: doc[k] for k in
                ("updated", "state", "deals", "rfq_items", "files", "downloaded",
                 "inventory") if k in doc}
    scopes[scope] = payload
    Path(path).write_text(json.dumps(
        {"updated": date.today().isoformat(),
         "source": "Bitrix24: опись входящих КП, адресный обход по сделкам, БЕЗ цен",
         "method": "цены исключены намеренно: репозиторий публичный. Здесь "
                   "происхождение файла, направление, способ разбора, число строк "
                   "и артикулы с ценой — этого хватает, чтобы свести с заявкой. "
                   "Опись накопительная: ключ верхнего уровня scopes — охват "
                   "прогона (ключевое слово в названии сделки либо её id).",
         "scopes": scopes}, ensure_ascii=False, indent=1), encoding="utf-8")


def take(bx: BitrixClient, c: dict):
    """Байты кандидата: по ссылке или через Диск, смотря что за источник.

    Возвращает (байты, как прошло, имя из заголовка отдачи).
    """
    if c.get("via") == "ссылка" and c.get("url"):
        return fetch(c["url"])
    if c.get("file_id"):
        u, why = disk_url(bx, c["file_id"])
        if u:
            return fetch(u)
        return None, f"Диск ссылку не отдал ({why or 'причина не записана'})", ""
    if c.get("url"):
        return fetch(c["url"])
    return None, "ни ссылки, ни id", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deal", type=int, help="одна сделка по id")
    g.add_argument("--keyword", help="подстрока в названии сделки")
    ap.add_argument("--dry-run", action="store_true",
                    help="холостой прогон: показать найденные файлы, ничего не качая")
    ap.add_argument("--all-files", action="store_true",
                    help="качать и наши запросы тоже, не только входящие")
    ap.add_argument("--max-files", type=int, default=400, help="предел числа файлов")
    ap.add_argument("--out-full", default="tkp_full.json")
    ap.add_argument("--out-index", default="gt/data/bitrix_tkp_index.json")
    a = ap.parse_args()

    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        say("нет BITRIX_WEBHOOK_URL")
        return 1
    bx = BitrixClient(wh)

    if a.deal:
        ids = [a.deal]
    else:
        deals = bx.list_paged("crm.deal.list", {"filter": {"%TITLE": a.keyword},
                                                "select": ["ID", "TITLE"]})
        ids = sorted(int(d["ID"]) for d in deals)
    # охват прогона — ключ, под которым его опись ляжет в накопительный файл
    scope = f"сделка {a.deal}" if a.deal else f"слово «{a.keyword}»"
    say(f"сделок к обходу: {len(ids)} · охват описи: {scope}")

    ffields = file_fields(bx, DEAL_ENTITY)
    say(f"файловых полей у сделки: {len(ffields)}")
    try:
        rfields = file_fields(bx, SPA_RFQ)
    except Exception as e:
        rfields = {}
        say(f"файловые поля СП-166 не прочитались ({type(e).__name__}) — "
            f"беру зашитые восемь")
    say(f"файловых полей у СП-166: {len(rfields) or len(RFQ_FILE_FIELDS)}")
    by_dir_fields = {}
    for t in list(ffields.values()) + list(rfields.values()):
        d = dir_from_field(t)
        by_dir_fields[d] = by_dir_fields.get(d, 0) + 1
    say(f"поля по направлению: {by_dir_fields}")

    # --- сбор кандидатов: сделка за сделкой, с прогрессом ---
    cands, seen, rfq_total, bodies = [], set(), 0, []

    def dump_inventory(done: int) -> None:
        """Опись по ходу обхода, а не только в конце.

        Правило, оплаченное первым прогоном: результат, записанный только в
        конце, таймаут уносит целиком. Холостой путь этому правилу не подчинялся
        — восемнадцатиминутный обход при 45-минутном пределе шага означал, что
        всё держится на том, чтобы уложиться. Теперь частичная опись есть всегда.
        """
        write_index(a.out_index, scope, {
            "updated": "holostoy" if a.dry_run else date.today().isoformat(),
            "state": f"обход: {done} из {len(ids)} сделок",
            "deals": len(ids), "rfq_items": rfq_total,
            "files": len(cands), "downloaded": 0,
            "inventory": [{k: v for k, v in c.items() if k != "url"} for c in cands]})

    for n, did in enumerate(ids, 1):
        say(f"[{n}/{len(ids)}] сделка {did}")
        got = []
        got += from_deal(bx, did, ffields)
        rq, cnt = from_rfq(bx, did, rfields)
        rfq_total += cnt
        got += rq
        got += from_timeline(bx, did)
        acts, mail_bodies = from_activities(bx, did)
        got += acts
        bodies += mail_bodies
        got += from_chat(bx, did)
        fresh = 0
        for c in got:
            key = c["file_id"] or c["url"][:120]
            if key in seen:
                continue
            seen.add(key)
            cands.append(c)
            fresh += 1
        by_dir = {}
        for c in got:
            by_dir[c["direction"]] = by_dir.get(c["direction"], 0) + 1
        say(f"    запросов СП-166: {cnt} · файлов найдено {len(got)}, новых {fresh}"
            + (f" · по направлению: {by_dir}" if by_dir else ""))
        if n % 20 == 0 or n == len(ids):
            dump_inventory(n)

    say(f"итого кандидатов: {len(cands)} (дублей снято {len(seen) - len(cands) if len(seen) > len(cands) else 0})")
    by_field = {}
    for c in cands:
        k = f'{c["field_name"]} [{c["direction"]}]'
        by_field[k] = by_field.get(k, 0) + 1
    for k in sorted(by_field, key=lambda x: -by_field[x]):
        say(f"    {by_field[k]:>4}  {k}")

    todo = [c for c in cands if a.all_files or c["direction"] in WANTED
            or c["direction"] == "неизвестно"]
    # Порядок, а не просто фильтр: предел --max-files отрежет хвост, и отрезать
    # он должен канбан-картинки, а не выставленные цены. Сортировка устойчивая,
    # поэтому внутри группы сохраняется порядок обхода — сделка за сделкой.
    todo.sort(key=rank)
    say(f"к разбору: {len(todo)} (наши цены, входящие и неопознанные; "
        f"заявка заказчика и наши запросы "
        f"{'включены' if a.all_files else 'исключены'})")
    ordered = {}
    for c in todo[:a.max_files]:
        ordered[c["direction"]] = ordered.get(c["direction"], 0) + 1
    say(f"в пределе {a.max_files} по направлению: {ordered}")

    if a.dry_run:
        write_index(a.out_index, scope, {
            "updated": "holostoy", "state": "холостой прогон, обход завершён",
            "deals": len(ids), "rfq_items": rfq_total,
            "files": len(cands), "downloaded": 0,
            "inventory": [{k: v for k, v in c.items() if k != "url"} for c in cands]})
        say("холостой прогон: ничего не скачано, опись записана")
        return 0

    # --- скачивание и разбор: по одному, с записью ПОСЛЕ КАЖДОГО файла ---
    full, index = [], []
    n_dl = n_rows = n_price = 0
    t0 = time.time()

    def dump():
        Path(a.out_full).write_text(json.dumps(
            {"updated": date.today().isoformat(),
             "source": "Bitrix24: входящие КП по сделке, адресный обход",
             "warning": "СОДЕРЖИТ КОММЕРЧЕСКИЕ ЦЕНЫ. В публичный репозиторий не коммитить.",
             "deals": ids, "files": full}, ensure_ascii=False, indent=1), encoding="utf-8")
        write_index(a.out_index, scope, {
            "updated": date.today().isoformat(),
            "state": f"разобрано {n_dl} файлов из {min(len(todo), a.max_files)}",
            "deals": len(ids), "rfq_items": rfq_total, "files": len(cands),
            "downloaded": n_dl, "inventory": index})

    for n, c in enumerate(todo[:a.max_files], 1):
        body, how, got_name = take(bx, c)
        rec = {k: v for k, v in c.items() if k != "url"}
        # имя приходит ТОЛЬКО с загрузкой: в файловом объекте его нет у 1986
        # файлов из 1998. Без него разбор выбирался бы по пустому расширению.
        if got_name and not rec.get("file_name"):
            rec["file_name"] = got_name
            c["file_name"] = got_name
        rec["download"] = how
        rec["size"] = len(body) if body else 0
        if not body:
            index.append(dict(rec, rows=0, priced=0, price_guesses=0, parse_path="",
                              status="не скачан"))
            if n % 10 == 0 or n == len(todo):
                say(f"  [{n}/{min(len(todo), a.max_files)}] скачано {n_dl}, "
                    f"строк {n_rows}, с ценой {n_price}, {(time.time()-t0)/60:.1f} мин")
                dump()
            continue
        n_dl += 1
        rows, how_parsed = parse(c["file_name"], body)
        # какой формат узнан по байтам — иначе «формат не поддержан» остаётся
        # загадкой, и следующая ошибка снова будет неизмеримой (правило 16)
        rec["sniffed"] = sniff(body)
        hdr = header_map(rows) if rows else {}
        pr = price_rows(rows, hdr) if rows else []
        n_rows += len(rows)
        n_price += len(pr)
        # статус не должен врать (правило 15 CLAUDE.md): pdf без текстового
        # слоя — это скан под распознавание, а не «пусто» и не «не КП»
        low = (c["file_name"] or "").lower()
        # Статус читает СПОСОБ разбора, а не только расширение: после появления
        # распознавания «картинка» может оказаться и разобранной, и нераспознанной
        # по разным причинам, и склеивать эти случаи значит врать о объёме работы
        # (правило 15 CLAUDE.md).
        if pr:
            status = "распознан" if how_parsed == "распознавание" else "разобран"
        elif rows:
            status = ("распознан, цен нет" if how_parsed == "распознавание"
                      else "текст без цен")
        elif "распознавание недоступно" in how_parsed:
            status = "скан или картинка, распознавание недоступно"
        elif how_parsed.startswith("распознано") or how_parsed.startswith("распознавание"):
            status = f"скан или картинка: {how_parsed}"
        elif low.endswith(".pdf"):
            status = "скан, требуется распознавание"
        elif low.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff")):
            status = "картинка, требуется распознавание"
        else:
            status = "не разобрался"
        full.append(dict(rec, parse_path=how_parsed, header=hdr, status=status, prices=pr))
        index.append(dict(rec, parse_path=how_parsed, status=status, rows=len(rows),
                          priced=sum(1 for p in pr if p.get("is_price")),
                          price_guesses=sum(1 for p in pr if not p.get("is_price")),
                          pns=sorted({p["pn"] for p in pr})[:400]))
        if n % 10 == 0 or n == min(len(todo), a.max_files):
            say(f"  [{n}/{min(len(todo), a.max_files)}] скачано {n_dl}, "
                f"строк {n_rows}, с ценой {n_price}, {(time.time()-t0)/60:.1f} мин")
            dump()

    # тела писем: КП могло прийти текстом, без вложения. Скачивать нечего —
    # DESCRIPTION дела уже у нас, и он идёт тем же разбором, что и файл.
    n_mail = 0
    for b in bodies:
        rows = rows_from_text(b["text"].encode("utf-8", "ignore"))
        hdr = header_map(rows) if rows else {}
        pr = price_rows(rows, hdr) if rows else []
        if not pr:
            continue
        n_mail += 1
        rec = {"origin": b["origin"], "field": "DESCRIPTION", "field_name": "тело письма",
               "direction": b["direction"], "via": "текст письма", "file_id": "",
               "file_name": f'письмо: {b["subject"][:80]}', "author": "", "context": "",
               "download": "не требуется", "size": len(b["text"])}
        full.append(dict(rec, parse_path="текст", header=hdr, status="разобран", prices=pr))
        index.append(dict(rec, parse_path="текст", status="разобран", rows=len(rows),
                          priced=sum(1 for x in pr if x.get("is_price")),
                          price_guesses=sum(1 for x in pr if not x.get("is_price")),
                          pns=sorted({x["pn"] for x in pr})[:400]))
        n_price += len(pr)
    if bodies:
        say(f"тел писем просмотрено: {len(bodies)}, с ценами: {n_mail}")

    dump()
    say(f"скачано файлов: {n_dl} из {min(len(todo), a.max_files)}")
    say(f"строк разобрано: {n_rows}")
    say(f"строк с парой «артикул — цена»: {n_price}")
    say(f"уникальных артикулов с ценой: {len({p['pn'] for f in full for p in f['prices']})}")
    st = {}
    for r in index:
        st[r["status"]] = st.get(r["status"], 0) + 1
    say(f"по статусу файлов: {st}")
    if len(todo) > a.max_files:
        say(f"ВНИМАНИЕ: предел --max-files {a.max_files}, не разобрано "
            f"{len(todo) - a.max_files} файлов — это не «всё покрыто»")
    return 0


if __name__ == "__main__":
    sys.exit(main())
