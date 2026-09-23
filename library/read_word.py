#!/usr/bin/env python3
"""Документ Word и OpenDocument целиком: таблицы, весь текст, картинки.

ЗАЧЕМ. До 23.09.2026 .docx читали три функции indexer.py регулярками по одной
части word/document.xml. Мимо них проходило всё, что лежит в других частях или
в другой разметке, — и это не редкие случаи, а привычки поставщиков:

  • надписи (w:txbxContent) — реквизиты и цены в «плавающем» блоке. Хуже того,
    Word пишет каждую надпись ДВАЖДЫ (mc:Choice и mc:Fallback), и регулярка по
    <w:t> удваивала их текст;
  • колонтитулы, сноски, примечания — отдельные части пакета;
  • объединённые ячейки: gridSpan и gridBefore сдвигают колонки, и цена
    оказывается под заголовком количества; vMerge оставляет строки без
    наименования;
  • вложенные таблицы — регулярка `<w:tr>.*?</w:tr>` рвёт внешнюю строку на
    первой же вложенной и склеивает обе таблицы в кашу;
  • куски одного слова в разных <w:t> (правописание, правки) склеивались через
    пробел: «На сос» вместо «Насос» — такое наименование не находится поиском;
  • удалённый при рецензировании текст (w:delText) читался как живой;
  • картинки — скан спецификации, вставленный в Word, давал «пусто»;
  • .odt, «строгий» OOXML (другое пространство имён), Word 2003 XML, плоский
    пакет, altChunk (HTML внутри docx, так пишут веб-площадки) — не читались.

ЧТО ВОЗВРАЩАЕТ `прочитать_документ(b)` — словарь:

    rows      все таблицы тела построчно, колонки на своих местах;
    text      весь текст по порядку: колонтитулы, тело (строки таблиц — через
              табуляцию), сноски, примечания, SmartArt, текст из метафайлов;
    images    картинки для распознавания [(имя, байты)] — только растр, без
              повторов, в порядке появления в документе;
    reason    пусто, если прочитано хоть что-то; иначе — почему нет;
    embedded  вложенные файлы (книга Excel, вставленная объектом) — их читает
              свой читатель;
    notes     что прочитано не полностью и почему (правило 16: сохраняй,
              почему получилось значение);
    format    какой разметкой прочитан файл.

НИКОГДА НЕ БРОСАЕТ. Любой сбой становится причиной в reason или заметкой в
notes: исключение в читателе — это файл, выпавший из прогона целиком.

ПРАВИЛО 17 (журнал публичный) соблюдается тем, что модуль ничего не печатает.
"""
from __future__ import annotations

import base64
import binascii
import email
import email.policy
import gzip
import hashlib
import io
import posixpath
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from html.parser import HTMLParser
from urllib.parse import unquote

#: Предел строк таблицы на файл. Тот же, что у library/readers.py: спецификация
#: в Word на пять тысяч позиций бывает, и прежний обрыв на 4 000 её резал.
МАКС_СТРОК = 20000

#: Предел знаков текста. Четыре миллиона — это ~2 000 страниц; индексатор берёт
#: из текста 400 000 знаков, запас нужен для определения сегмента по всему файлу.
МАКС_ЗНАКОВ = 4_000_000

#: Предел одной XML-части после распаковки. ElementTree держит дерево в памяти
#: примерно в десять раз больше исходника: 128 МБ — это уже гигабайт с лишним.
МАКС_ЧАСТЬ = 128 * 1024 * 1024

#: Сколько картинок отдавать на распознавание. Секунда с лишним на страницу
#: (замер pdf-analysis — 1,2 с): двести картинок — это уже четыре минуты.
МАКС_КАРТИНОК = 200
МАКС_КАРТИНКА = 64 * 1024 * 1024

#: Вложенные файлы — те же пределы, что у участников архива в readers.py.
МАКС_ВЛОЖЕНИЙ = 40
МАКС_ВЛОЖЕНИЕ = 200 * 1024 * 1024

#: Глубина вложенности документа в документ (altChunk с docx внутри). Два уровня
#: закрывают настоящие случаи; больше — это уже зацикленная или вредная сборка.
МАКС_ГЛУБИНА = 2

#: Потолок объединения колонок. В Word больше 63 колонок в таблице не бывает;
#: без потолка gridSpan="1000000" раздул бы строку на миллион пустых ячеек.
МАКС_ПРОЛЁТ = 64

#: Картинки меньше этого числа точек — узоры заливки из метафайлов, а не текст.
МИН_ТОЧЕК = 32 * 32

#: Шрифт Symbol хранит знаки в частной области (F0xx). Без перевода «±0,05»,
#: «Ø20» и «20 °C» в технических требованиях теряют знак — остаются «0,05» и «20».
СИМВОЛ = {
    0x22: "∀", 0x24: "∃", 0x2D: "−", 0x44: "Δ", 0x46: "Φ", 0x47: "Γ", 0x4C: "Λ",
    0x50: "Π", 0x51: "Θ", 0x53: "Σ", 0x57: "Ω", 0x61: "α", 0x62: "β", 0x63: "χ",
    0x64: "δ", 0x65: "ε", 0x66: "φ", 0x67: "γ", 0x68: "η", 0x6A: "ϕ", 0x6B: "κ",
    0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ",
    0x74: "τ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ", 0xA3: "≤", 0xA5: "∞",
    0xAC: "←", 0xAE: "→", 0xB0: "°", 0xB1: "±", 0xB3: "≥", 0xB4: "×", 0xB7: "•",
    0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈", 0xC6: "Ø", 0xD6: "√", 0xE6: "Ø",
}

#: Элементы WordprocessingML, в которых нет читаемого текста или текст мёртвый.
#: moveFrom — старое место перемещённого при рецензировании текста: там
#: обычный w:t, и без пропуска абзац читался бы дважды. delText, del и
#: instrText (код поля «PAGE», «HYPERLINK …») текстом и так не считаются —
#: текст берётся только из элементов «t», — пропуск лишь не ходит внутрь.
#: tabs — позиции табуляции в свойствах абзаца: локальное имя у них то же «tab».
_ПРОПУСК_W = frozenset({
    "rPr", "tabs", "delText", "delInstrText", "instrText", "del", "moveFrom",
    "fldData", "pPrChange", "rPrChange", "sdtPr", "sdtEndPr", "tblPr", "tblGrid",
    "trPr", "tcPr", "tblPrEx", "footnoteRef", "endnoteRef", "annotationRef",
    "separator", "continuationSeparator", "lastRenderedPageBreak",
})

#: Обёртки, сквозь которые видны строки и ячейки таблицы (элементы управления
#: содержимым, пользовательский XML, вставка при рецензировании).
_ОБЁРТКИ_W = frozenset({"sdt", "sdtContent", "customXml", "ins", "moveTo", "smartTag"})

#: Блочные контейнеры внутри абзаца: надпись, колонтитул и сноска Word 2003
#: (там они лежат прямо в тексте), содержимое которых — свои абзацы.
_БЛОКИ_В_АБЗАЦЕ_W = frozenset({"txbxContent", "hdr", "ftr", "footnote", "endnote"})

_DC = "{http://purl.org/dc/elements/1.1/}"

#: Элементы ODF без читаемого текста. tracked-changes держит УДАЛЁННЫЙ текст
#: рецензирования; note-citation — номер сноски («1»), а не её текст.
_ПРОПУСК_ODF = frozenset({
    "tracked-changes", "sequence-decls", "variable-decls", "user-field-decls",
    "dde-connection-decls", "forms", "note-citation", "table-columns",
    "table-column", "table-column-group", "table-header-columns", "soft-page-break",
    "bookmark", "bookmark-start", "bookmark-end", "reference-mark",
    "reference-mark-start", "reference-mark-end", "title", "desc", "script",
    "event-listeners", "named-expressions", "calculation-settings",
    "database-ranges", "data-pilot-tables", "content-validations", "label-ranges",
    "change", "change-start", "change-end",
})

_ПРОБЕЛЫ = re.compile(r"[   -   　]+")
_НЕВИДИМЫЕ = re.compile(r"[­​‌‍⁠﻿]")
_СЖАТЬ = re.compile(r"[ \t\r\n]+")
_ПЛОХИЕ_БАЙТЫ = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ССЫЛКА_ЗНАКА = re.compile(rb"&#(x[0-9a-fA-F]+|[0-9]+);")
_ОБЪЯВЛЕНИЕ = re.compile(rb"^\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9_.-]+)[\"']")
_DTD = re.compile(rb"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", re.S)
_СУЩНОСТЬ = re.compile(rb"&(?!(?:amp|lt|gt|quot|apos);)[A-Za-z_][\w.-]*;")
_HTML_МЕТКА = re.compile(rb"<(html|body|table|tr|td|p|div)[\s>]|<!doctype html")


def прочитать_документ(b: bytes) -> dict:
    """Документ Word (.docx/.docm/.dotx, строгий OOXML, Word 2003 XML, плоский
    пакет, HTML/MHT под видом .doc, RTF, старый .doc) или OpenDocument (.odt,
    .fodt) — в строки таблиц, текст и картинки. Никогда не бросает."""
    заметки: list[str] = []
    try:
        внутр = _Разбор(0, заметки).документ(b)
    except Exception as e:                              # noqa: BLE001
        внутр = _пусто(f"сбой читателя ({type(e).__name__})")
    return _собрать(внутр, заметки)


# ───────────────────────────── общие мелочи ─────────────────────────────

def _имя(el) -> str:
    т = el.tag
    return т.rsplit("}", 1)[-1] if isinstance(т, str) else ""


def _атр(el, имя: str) -> str | None:
    """Атрибут по локальному имени: w:val, strict-w:val и val без префикса —
    одно и то же, иначе «строгий» OOXML и Word 2003 не читались бы."""
    if el is None:
        return None
    for к, з in el.attrib.items():
        if к == имя or к.endswith("}" + имя):
            return з
    return None


def _ребёнок(el, *имена: str):
    if el is None:
        return None
    for ch in el:
        if _имя(ch) in имена:
            return ch
    return None


def _целое(з: str | None, умолч: int, низ: int, верх: int) -> int:
    try:
        n = int(float(з)) if з is not None else умолч
    except (TypeError, ValueError):
        n = умолч
    return max(низ, min(n, верх))


def _чисто(s: str) -> str:
    """Неразрывные и узкие пробелы — в обычный, невидимые знаки — прочь.

    Мягкий перенос (U+00AD) и пробел нулевой ширины сидят внутри слов: с ними
    «подшипник» не равен «подшипнику» при поиске, хотя на экране их не видно.
    """
    return _ПРОБЕЛЫ.sub(" ", _НЕВИДИМЫЕ.sub("", s)).strip()


def _связь(el) -> str | None:
    """Номер связи картинки или вставки: r:embed, r:id (в том числе строгого
    OOXML — пространство имён другое, локальное имя то же) или o:relid."""
    for к, з in el.attrib.items():
        if "}" not in к:
            continue
        пр, л = к[1:].split("}", 1)
        if л in ("embed", "id", "relid") and ("relationships" in пр or "office:office" in пр):
            return з
    return None


def _естественно(имя: str) -> list:
    """image2 раньше image10: картинки нумеруются по порядку вставки."""
    return [int(ч) if ч.isdigit() else ч.lower() for ч in re.split(r"(\d+)", имя)]


class _Сбор:
    """Что собрано с одного уровня документа.

    `свои` — абзацы этого уровня (из них складывается текст ячейки), `табличные`
    — строки вложенных таблиц: в текст ячейки они НЕ идут, иначе рамка-таблица
    вокруг спецификации превращалась в одну ячейку со всей спецификацией, а
    сама спецификация в таблицу не попадала.
    """
    __slots__ = ("строки", "абзацы", "свои", "табличные", "ссылки", "данные")

    def __init__(self) -> None:
        self.строки: list[list[str]] = []
        self.абзацы: list[str] = []
        self.свои: list[str] = []
        self.табличные: list[str] = []
        self.ссылки: list[str] = []
        self.данные: list[tuple[str, bytes]] = []

    def абзац(self, текст: str) -> None:
        for часть in текст.split("\n"):
            ч = _чисто(часть)
            if ч:
                self.абзацы.append(ч)
                self.свои.append(ч)

    def ряд(self, ячейки: list[str]) -> None:
        self.строки.append(ячейки)
        линия = "\t".join(c for c in ячейки if c)
        if линия:
            self.абзацы.append(линия)
            self.табличные.append(линия)

    def слить(self, д: "_Сбор") -> None:
        self.строки += д.строки
        self.абзацы += д.абзацы
        self.свои += д.свои
        self.табличные += д.табличные
        self.ссылки += д.ссылки
        self.данные += д.данные

    def пуст(self) -> bool:
        return not (self.абзацы or self.строки or self.ссылки or self.данные)


def _пусто(почему: str, формат: str = "") -> dict:
    return {"сб": _Сбор(), "до": [], "после": [], "images": [], "embedded": [],
            "format": формат, "reason": почему}


def _собрать(внутр: dict, заметки: list[str]) -> dict:
    сб: _Сбор = внутр["сб"]
    линии = list(внутр["до"]) + сб.абзацы + list(внутр["после"])
    текст = "\n".join(линии)
    if len(текст) > МАКС_ЗНАКОВ:
        текст = текст[:МАКС_ЗНАКОВ]
        заметки.append(f"текст обрезан на {МАКС_ЗНАКОВ} знаков")
    rows = сб.строки
    картинки = внутр["images"]
    вложения = внутр["embedded"]
    if rows or текст.strip():
        причина = ""
    elif внутр["reason"]:
        причина = внутр["reason"]
    elif картинки:
        причина = f"текста нет, картинок {len(картинки)}: читается распознаванием"
    elif вложения:
        причина = f"текста нет, вложенных файлов {len(вложения)}: читаются своими читателями"
    else:
        причина = "документ без текста, таблиц и картинок"
    return {"rows": rows, "text": текст, "images": картинки, "reason": причина,
            "embedded": вложения, "notes": заметки, "format": внутр["format"]}


# ───────────────────────────── пакет (ZIP) ─────────────────────────────

def _ключ(имя: str) -> str:
    # Сборщики под Windows пишут «word\document.xml», а регистр в ссылках
    # пакета не обязан совпадать с регистром имён: сравнение — без него.
    return имя.replace("\\", "/").lstrip("/").lower()


def _по_заголовкам(b: bytes) -> dict[str, bytes]:
    """Участники ZIP по ЛОКАЛЬНЫМ заголовкам, без центрального каталога.

    Каталог ZIP лежит в хвосте файла. Оборванная загрузка теряет именно хвост,
    и zipfile отказывает целиком — хотя document.xml, записанный в начале, цел.
    Последний, оборванный участник распаковывается сколько есть: разбор XML ниже
    умеет читать документ до обрыва.
    """
    out: dict[str, bytes] = {}
    i = b.find(b"PK\x03\x04")
    while 0 <= i and len(out) < 4000 and i + 30 <= len(b):
        (флаги, метод, _, _, _, сжато, _, длина_имени, длина_доп) = struct.unpack_from(
            "<HHHHIIIHH", b, i + 6)
        имя = b[i + 30:i + 30 + длина_имени].decode("utf-8" if флаги & 0x800 else "cp437", "replace")
        начало = i + 30 + длина_имени + длина_доп
        # Бит 3: размеры записаны ПОСЛЕ данных (так пишут потоковые сборщики,
        # в том числе многие выгрузки учётных систем), в заголовке нули.
        известно = bool(сжато) and not флаги & 8
        дальше = начало + сжато if известно else начало + 1
        данные: bytes | None = None
        if метод == 0 and известно:
            данные = b[начало:начало + сжато]
        elif метод == 8:
            кусок = b[начало:начало + сжато] if известно else b[начало:]
            распак = zlib.decompressobj(-15)
            части, всего, съедено = [], 0, 0
            for k in range(0, len(кусок), 1 << 16):
                try:
                    ч = распак.decompress(кусок[k:k + (1 << 16)])
                except zlib.error:
                    break
                части.append(ч)
                всего += len(ч)
                if распак.eof:
                    съедено = k + min(1 << 16, len(кусок) - k) - len(распак.unused_data)
                    break
                if всего > МАКС_ЧАСТЬ:
                    break
            данные = b"".join(части)
            if съедено:
                дальше = начало + съедено
        if данные is not None and not имя.endswith("/"):
            out.setdefault(имя, данные)
        i = b.find(b"PK\x03\x04", max(дальше, i + 4))
    return out


class _Пакет:
    """Части документа: из ZIP или из словаря (плоский пакет, спасённый ZIP)."""

    def __init__(self, заметки: list[str], zf: zipfile.ZipFile | None = None,
                 части: dict[str, bytes] | None = None, сырой: bytes | None = None) -> None:
        self.заметки = заметки
        self.zf = zf
        self.части = части or {}
        self.сырой = сырой
        self._запас: dict[str, bytes] | None = None
        self.карта: dict[str, str] = {}
        имена = zf.namelist() if zf is not None else list(self.части)
        for n in имена:
            if not n.endswith("/"):
                self.карта.setdefault(_ключ(n), n)

    def список(self) -> list[str]:
        return [n.replace("\\", "/").lstrip("/") for n in self.карта.values()]

    def найти(self, путь: str) -> str | None:
        for п in (путь, unquote(путь)):
            n = self.карта.get(_ключ(п))
            if n is not None:
                return n
        return None

    def есть(self, путь: str) -> bool:
        return self.найти(путь) is not None

    def читать(self, путь: str, предел: int = МАКС_ЧАСТЬ) -> bytes | None:
        n = self.найти(путь)
        if n is None:
            return None
        if self.zf is None:
            д = self.части.get(n)
            return д if д is not None and len(д) <= предел else None
        try:
            with self.zf.open(n) as f:
                # Размер из каталога задаёт сам файл, верить ему нельзя:
                # читаем на байт больше предела и так узнаём бомбу распаковки.
                д = f.read(предел + 1)
            if len(д) > предел:
                self.заметки.append(f"{n}: больше {предел // (1024 * 1024)} МБ, пропущено")
                return None
            return д
        except Exception as e:                          # noqa: BLE001
            # Битый участник (CRC, обрыв): берём, что даёт локальный заголовок.
            if self.сырой is not None:
                if self._запас is None:
                    self._запас = _по_заголовкам(self.сырой)
                д = self._запас.get(n)
                if д:
                    self.заметки.append(f"{n}: участник битый ({type(e).__name__}), прочитан по заголовку")
                    return д[:предел]
            self.заметки.append(f"{n}: не распакован ({type(e).__name__})")
            return None


def _разрешить(каталог: str, цель: str) -> str:
    цель = цель.replace("\\", "/")
    if цель.startswith("/"):
        return цель.lstrip("/")
    return posixpath.normpath(posixpath.join(каталог, цель)).lstrip("/")


# ───────────────────────────── разбор XML ─────────────────────────────

def _разобрать_xml(данные: bytes | None, имя: str, заметки: list[str]) -> ET.Element | None:
    try:
        return _разобрать_xml_(данные, имя, заметки)
    except Exception as e:                              # noqa: BLE001
        заметки.append(f"{имя}: XML не разобран ({type(e).__name__})")
        return None


def _разобрать_xml_(данные: bytes | None, имя: str, заметки: list[str]) -> ET.Element | None:
    """XML-часть в дерево — даже битая.

    Три ступени, от точной к спасательной:
      1. как есть;
      2. без управляющих знаков: сторонние генераторы (выгрузки учётных систем)
         пишут \\x0b и &#1; — XML 1.0 их запрещает, expat отказывает целиком,
         хотя Word такой файл открывает;
      3. до места обрыва: у оборванной загрузки дерево до обрыва цело.
    DTD вырезается заранее: Word его не пишет никогда, а объявление сущностей в
    части документа — это либо чужой генератор, либо «миллиард смехов».
    """
    if not данные:
        return None
    if b"<!DOCTYPE" in данные[:4096] or b"<!ENTITY" in данные[:4096]:
        # Вместе с DTD уходят и ссылки на её сущности: неопределённая ссылка
        # обрывает разбор, и всё после неё терялось бы.
        данные = _СУЩНОСТЬ.sub(b"", _DTD.sub(b"", данные, count=1))
        заметки.append(f"{имя}: объявление DTD вырезано")
    try:
        return ET.fromstring(данные)
    except ET.ParseError:
        pass
    except Exception:                                   # noqa: BLE001, S110
        pass
    чистые = _очистить(данные)
    try:
        корень = ET.fromstring(чистые)
        заметки.append(f"{имя}: недопустимые знаки XML вырезаны")
        return корень
    except Exception:                                   # noqa: BLE001, S110
        pass
    корень = _до_обрыва(чистые)
    if корень is not None:
        заметки.append(f"{имя}: XML битый, прочитано до места обрыва")
    return корень


def _очистить(д: bytes) -> bytes:
    if д[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return д                                        # UTF-16: байтовая чистка сломала бы его
    д = _ПЛОХИЕ_БАЙТЫ.sub(b"", д)

    def ссылка(m: re.Match) -> bytes:
        v = m.group(1)
        try:
            n = int(v[1:], 16) if v[:1] == b"x" else int(v)
        except ValueError:
            return b""
        годно = (n in (9, 10, 13) or 0x20 <= n <= 0xD7FF or 0xE000 <= n <= 0xFFFD
                 or 0x10000 <= n <= 0x10FFFF)
        return m.group(0) if годно else b""
    д = _ССЫЛКА_ЗНАКА.sub(ссылка, д)
    # Объявлен UTF-8 (или ничего), а байты — cp1251: так пишут генераторы,
    # склеивающие XML строками. expat видит «invalid token» на первой букве.
    объявлено = _ОБЪЯВЛЕНИЕ.match(д)
    if объявлено is None or объявлено.group(1).lower().replace(b"-", b"") == b"utf8":
        try:
            д.decode("utf-8")
        except UnicodeDecodeError:
            замены = д.decode("utf-8", "replace").count("�")
            if замены > 10:
                д = д.decode("cp1251", "replace").encode("utf-8")
            else:
                д = д.decode("utf-8", "replace").encode("utf-8")
    return д


def _до_обрыва(д: bytes) -> ET.Element | None:
    разборщик = ET.XMLPullParser(events=("start",))
    корень = None
    try:
        for k in range(0, len(д), 1 << 20):
            разборщик.feed(д[k:k + (1 << 20)])
            for _, el in разборщик.read_events():
                if корень is None:
                    корень = el
        разборщик.close()
    except Exception:                                   # noqa: BLE001
        pass
    try:
        for _, el in разборщик.read_events():
            if корень is None:
                корень = el
    except Exception:                                   # noqa: BLE001, S110
        pass
    return корень


# ───────────────────────────── метафайлы и картинки ─────────────────────────────

def _вид_картинки(д: bytes) -> str:
    if (д[:8] == b"\x89PNG\r\n\x1a\n" or д[:3] == b"\xff\xd8\xff" or д[:6] in (b"GIF87a", b"GIF89a")
            or д[:4] in (b"II*\x00", b"MM\x00*") or (д[:4] == b"RIFF" and д[8:12] == b"WEBP")
            or д[:12] == b"\x00\x00\x00\x0cjP  \r\n\x87\n" or (д[:2] == b"BM" and len(д) > 54)):
        return "растр"
    if len(д) >= 44 and д[:4] == b"\x01\x00\x00\x00" and д[40:44] == b" EMF":
        return "emf"
    if д[:4] == b"\xd7\xcd\xc6\x9a" or (д[:2] in (b"\x01\x00", b"\x02\x00") and д[2:4] == b"\x09\x00"):
        return "wmf"
    if д[:2] == b"\x1f\x8b":
        return "gzip"                                   # .emz / .wmz из Word-HTML
    return ""


def _dib_в_bmp(bmi: bytes, биты: bytes) -> bytes | None:
    """Растр из метафайла (заголовок DIB и точки) в файл BMP для распознавания.

    Скан, вставленный в Word через буфер обмена из просмотрщика, часто лежит не
    PNG, а EMF с растром внутри — распознавание EMF не открывает, BMP открывает.
    """
    if len(bmi) < 12 or not биты:
        return None
    размер_заголовка = struct.unpack_from("<I", bmi)[0]
    if размер_заголовка == 12:
        ш, в = struct.unpack_from("<HH", bmi, 4)
        сжатие = 0
    elif размер_заголовка >= 40 and len(bmi) >= 40:
        ш, в = struct.unpack_from("<ii", bmi, 4)
        сжатие = struct.unpack_from("<I", bmi, 16)[0]
    else:
        return None
    if сжатие in (4, 5):                                # BI_JPEG, BI_PNG: внутри готовый файл
        return биты if _вид_картинки(биты) == "растр" else None
    if abs(ш) * abs(в) < МИН_ТОЧЕК:
        return None
    return (b"BM" + struct.pack("<IHHI", 14 + len(bmi) + len(биты), 0, 0, 14 + len(bmi))
            + bmi + биты)


def _размер_bmi(dib: bytes) -> int:
    if len(dib) < 16:
        return 0
    hs = struct.unpack_from("<I", dib)[0]
    if hs == 12:
        бит = struct.unpack_from("<H", dib, 10)[0]
        return 12 + 3 * ((1 << бит) if бит <= 8 else 0)
    if hs < 40 or len(dib) < 40:
        return 0
    бит, сжатие = struct.unpack_from("<HI", dib, 14)
    цветов = struct.unpack_from("<I", dib, 32)[0] or ((1 << бит) if бит <= 8 else 0)
    маски = 12 if сжатие == 3 and hs == 40 else 0
    return hs + маски + 4 * min(цветов, 256)


def _линии_метафайла(куски: list[tuple[int, int, float | None, str]]) -> list[str]:
    """Надписи метафайла в строки: одна высота — одна строка, слева направо.

    Таблица Excel, вставленная в Word «как рисунок», хранит каждую ячейку
    отдельной командой вывода текста с координатами. Строки собираются по
    координате y с допуском 0,8 ширины знака (из массива dx): высота строки —
    около двух ширин знака, поэтому сдвиг разных шрифтов одной строки на 1–2
    единицы склеивается, а соседние строки — нет. Допуск по медиане шагов между
    строками не годится: пар с дрожанием бывает больше, чем самих строк, и
    медиана выходит равной дрожанию. Без dx строки берутся по точному y.
    """
    куски = [к for к in куски if к[3].strip()]
    if not куски:
        return []
    ширины = sorted(abs(к[2]) / len(к[3]) for к in куски if к[2])
    допуск = 0.8 * ширины[len(ширины) // 2] if ширины else 0
    куски.sort(key=lambda к: (к[0], к[1]))
    группы: list[list[tuple[int, int, float | None, str]]] = []
    for к in куски:
        if группы and abs(к[0] - группы[-1][0][0]) <= допуск:
            группы[-1].append(к)
        else:
            группы.append([к])
    линии = []
    for г in группы:
        г.sort(key=lambda к: к[1])
        части: list[str] = []
        конец: float | None = None
        знак = 0.0
        for _, x, w, t in г:
            if части:
                if конец is None or not знак:
                    части.append("\t")
                else:
                    зазор = x - конец
                    части.append("" if зазор <= 0.3 * знак else (" " if зазор <= 1.5 * знак else "\t"))
            части.append(t)
            if w:
                конец, знак = x + w, abs(w) / max(1, len(t))
            else:
                конец = None
        линия = _чисто("".join(части))
        if линия:
            линии.append(линия)
    return линии


def _emf(д: bytes) -> tuple[list[str], list[bytes]]:
    куски: list[tuple[int, int, float | None, str]] = []
    растры: list[bytes] = []
    i, n, записей = 0, len(д), 0
    while i + 8 <= n and записей < 2_000_000:
        тип, размер = struct.unpack_from("<II", д, i)
        if размер < 8 or i + размер > n:
            break
        записей += 1
        конец = i + размер
        if тип in (83, 84) and размер >= 76:            # EMR_EXTTEXTOUTA / W
            x, y, знаков, смещение, опции = struct.unpack_from("<iiIII", д, i + 36)
            # ETO_GLYPH_INDEX: вместо знаков номера глифов шрифта, прочесть нельзя.
            if знаков and not опции & 0x10:
                ширина_байт = 2 if тип == 84 else 1
                сырое = д[i + смещение:i + смещение + знаков * ширина_байт]
                if i + смещение + знаков * ширина_байт <= конец:
                    t = сырое.decode("utf-16-le" if тип == 84 else "cp1251", "replace")
                    ширина = None
                    сдвиги = struct.unpack_from("<I", д, i + 72)[0]
                    шаг = 2 if опции & 0x2000 else 1     # ETO_PDY: пары x, y
                    if сдвиги and i + сдвиги + 4 * знаков * шаг <= конец:
                        dx = struct.unpack_from(f"<{знаков * шаг}i", д, i + сдвиги)
                        ширина = float(sum(dx[::шаг]))
                    куски.append((y, x, ширина, t.replace("\x00", "")))
        elif тип in (80, 81, 76, 77):                   # SETDIBITSTODEVICE, STRETCHDIBITS, BITBLT, STRETCHBLT
            место = 48 if тип in (80, 81) else 84
            if размер >= место + 16:
                об, рб, оп, рп = struct.unpack_from("<IIII", д, i + место)
                if об and оп and i + об + рб <= конец and i + оп + рп <= конец:
                    bmp = _dib_в_bmp(д[i + об:i + об + рб], д[i + оп:i + оп + рп])
                    if bmp:
                        растры.append(bmp)
        elif тип == 14:                                 # EMR_EOF
            break
        i = конец
    return _линии_метафайла(куски), растры


def _wmf(д: bytes) -> tuple[list[str], list[bytes]]:
    куски: list[tuple[int, int, float | None, str]] = []
    растры: list[bytes] = []
    i = 22 if д[:4] == b"\xd7\xcd\xc6\x9a" else 0
    if i + 18 > len(д):
        return [], []
    i += 2 * max(9, min(struct.unpack_from("<H", д, i + 2)[0], 64))
    n, записей = len(д), 0
    while i + 6 <= n and записей < 2_000_000:
        слов, функция = struct.unpack_from("<IH", д, i)
        размер = слов * 2
        if размер < 6 or i + размер > n or функция == 0:
            break
        записей += 1
        p, конец = i + 6, i + размер
        try:
            if функция == 0x0521:                       # META_TEXTOUT
                длина = struct.unpack_from("<h", д, p)[0]
                if длина > 0:
                    t = д[p + 2:p + 2 + длина]
                    y, x = struct.unpack_from("<hh", д, p + 2 + длина + (длина & 1))
                    куски.append((y, x, None, t.decode("cp1251", "replace")))
            elif функция == 0x0A32:                     # META_EXTTEXTOUT
                y, x, длина, опции = struct.unpack_from("<hhhH", д, p)
                q = p + 8 + (8 if опции & 0x0006 else 0)
                if длина > 0 and q + длина <= конец:
                    t = д[q:q + длина].decode("cp1251", "replace")
                    ширина = None
                    q_dx = q + длина + (длина & 1)
                    if q_dx + 2 * длина <= конец:
                        ширина = float(sum(struct.unpack_from(f"<{длина}h", д, q_dx)))
                    куски.append((y, x, ширина, t))
            elif функция in (0x0F43, 0x0B41, 0x0940):   # STRETCHDIB, DIBSTRETCHBLT, DIBBITBLT
                # Вариант без растра узнаётся по длине записи (MS-WMF 2.3.1.3).
                if функция == 0x0F43 or слов != (функция >> 8) + 3:
                    пропуск = {0x0F43: 22, 0x0B41: 20, 0x0940: 16}[функция]
                    dib = д[p + пропуск:конец]
                    рб = _размер_bmi(dib)
                    if рб:
                        bmp = _dib_в_bmp(dib[:рб], dib[рб:])
                        if bmp:
                            растры.append(bmp)
        except struct.error:
            pass
        i = конец
    return _линии_метафайла(куски), растры


def _метафайл(д: bytes) -> tuple[list[str], list[bytes]]:
    вид = _вид_картинки(д)
    try:
        if вид == "emf":
            return _emf(д)
        if вид == "wmf":
            return _wmf(д)
    except Exception:                                   # noqa: BLE001, S110
        pass
    return [], []


class _Картинки:
    """Картинки документа: растр — на распознавание, метафайл — в текст.

    Одинаковые снимки (логотип в каждом колонтитуле) отдаются один раз — по
    хешу содержимого, как в base/reparse.py.
    """

    def __init__(self, заметки: list[str]) -> None:
        self.заметки = заметки
        self.итог: list[tuple[str, bytes]] = []
        self.линии: list[str] = []
        self.виденные: set[bytes] = set()
        self.сказано = False

    def добавить(self, имя: str, д: bytes | None, глубина: int = 0) -> None:
        if not д:
            return
        h = hashlib.sha1(д).digest()
        if h in self.виденные:
            return
        self.виденные.add(h)
        вид = _вид_картинки(д)
        if вид == "растр":
            if len(self.итог) >= МАКС_КАРТИНОК:
                if not self.сказано:
                    self.заметки.append(f"картинок больше {МАКС_КАРТИНОК}: остальные не отданы")
                    self.сказано = True
                return
            self.итог.append((имя, д))
        elif вид in ("emf", "wmf"):
            строки, растры = _метафайл(д)
            self.линии += строки
            for k, r in enumerate(растры, 1):
                self.добавить(f"{имя}#{k}.bmp", r, глубина)
        elif вид == "gzip" and глубина == 0:
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(д)) as f:
                    self.добавить(имя, f.read(МАКС_КАРТИНКА), 1)
            except Exception:                           # noqa: BLE001, S110
                pass

    def готово(self) -> list[tuple[str, bytes]]:
        return list(self.итог)


# ───────────────────────────── HTML (altChunk, Word-HTML, MHT) ─────────────────────────────

class _ТабХ:
    __slots__ = ("ряд", "слияния", "ожидание", "своё", "пролёт")

    def __init__(self) -> None:
        self.ряд: list[str] | None = None
        self.слияния: dict[int, list] = {}
        self.ожидание: list[list[str]] = []
        self.своё = False
        self.пролёт = (1, 1)


class _ХТМЛ(HTMLParser):
    """HTML в таблицы и текст: colspan и rowspan ставят ячейки на свои колонки.

    В HTML ячейка с rowspan в следующих строках ОТСУТСТВУЕТ (в Word она есть),
    и без вставки её значения все ячейки правее съезжают на колонку влево.
    """
    _БЛОЧНЫЕ = frozenset({
        "p", "div", "br", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4",
        "h5", "h6", "blockquote", "pre", "hr", "section", "article", "header",
        "footer", "caption", "center", "address", "form", "fieldset", "nav", "aside",
        "main", "figure", "figcaption",
    })
    _ПРОПУСК = frozenset({"script", "style", "head", "xml", "noscript", "template", "title", "svg"})

    def __init__(self, сб: _Сбор, ряд) -> None:
        super().__init__(convert_charrefs=True)
        self.сб = сб
        self.выдать_ряд = ряд
        self.пропуск: list[str] = []
        self.уровни: list[list] = [[[], []]]            # [линии, текущий кусок]
        self.таблицы: list[_ТабХ] = []
        self.пре = 0

    def _перевод(self) -> None:
        линии, кусок = self.уровни[-1]
        if кусок:
            текст = "".join(кусок)
            кусок.clear()
            if len(self.уровни) == 1:
                self.сб.абзац(текст)
            else:
                линии.extend(текст.split("\n"))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "body" and "head" in self.пропуск:
            self.пропуск.clear()                        # </head> забыт — тело всё равно читаем
            return
        if tag in self._ПРОПУСК:
            self.пропуск.append(tag)
            return
        if self.пропуск:
            return
        a = {k.lower(): v for k, v in attrs if k}
        if tag == "table":
            self._перевод()
            self.таблицы.append(_ТабХ())
        elif tag == "tr" and self.таблицы:
            self._конец_ряда(self.таблицы[-1])
            self.таблицы[-1].ряд = []
        elif tag in ("td", "th") and self.таблицы:
            т = self.таблицы[-1]
            if len(self.уровни) > len(self.таблицы):
                self._конец_ячейки(т)                    # </td> забыт
            if т.ряд is None:
                т.ряд = []
            т.пролёт = (_целое(a.get("colspan"), 1, 1, МАКС_ПРОЛЁТ),
                        _целое(a.get("rowspan"), 1, 1, 10000))
            self.уровни.append([[], []])
        elif tag == "img" or tag.endswith("imagedata"):
            src = a.get("src") or ""
            if src:
                self.сб.ссылки.append(src)
        elif tag == "pre":
            self.пре += 1
            self._перевод()
        elif tag in self._БЛОЧНЫЕ:
            self._перевод()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.пропуск:
            if tag in self.пропуск:
                while self.пропуск and self.пропуск.pop() != tag:
                    pass
            return
        if tag in ("td", "th") and self.таблицы and len(self.уровни) > len(self.таблицы):
            self._конец_ячейки(self.таблицы[-1])
        elif tag == "tr" and self.таблицы:
            if len(self.уровни) > len(self.таблицы):
                self._конец_ячейки(self.таблицы[-1])
            self._конец_ряда(self.таблицы[-1])
        elif tag == "table" and self.таблицы:
            self._конец_таблицы()
        elif tag == "pre":
            self.пре = max(0, self.пре - 1)
            self._перевод()
        elif tag in self._БЛОЧНЫЕ:
            self._перевод()

    def handle_data(self, data):
        if not self.пропуск:
            self.уровни[-1][1].append(data if self.пре else _СЖАТЬ.sub(" ", data))

    @staticmethod
    def _перенос(т: _ТабХ) -> None:
        while т.ряд is not None and len(т.ряд) in т.слияния:
            кол = len(т.ряд)
            запись = т.слияния[кол]
            т.ряд.append(запись[0])
            т.ряд.extend([""] * (запись[2] - 1))
            запись[1] -= 1
            if запись[1] <= 0:
                del т.слияния[кол]

    def _конец_ячейки(self, т: _ТабХ) -> None:
        self._перевод()
        линии, _ = self.уровни.pop()
        текст = " ".join(ч for ч in (_чисто(л) for л in линии) if ч)
        if т.ряд is None:
            т.ряд = []
        self._перенос(т)
        кол = len(т.ряд)
        ширина, высота = т.пролёт
        т.ряд.append(текст)
        т.ряд.extend([""] * (ширина - 1))
        if высота > 1:
            т.слияния[кол] = [текст, высота - 1, ширина]
        т.своё = т.своё or bool(текст)
        т.пролёт = (1, 1)

    def _конец_ряда(self, т: _ТабХ) -> None:
        if т.ряд is None:
            return
        self._перенос(т)
        ряды = ([т.ряд] if т.своё else []) + т.ожидание
        т.ряд, т.своё, т.ожидание = None, False, []
        k = self.таблицы.index(т)
        if k > 0:
            self.таблицы[k - 1].ожидание.extend(ряды)
        else:
            for р in ряды:
                self.выдать_ряд(self.сб, р)

    def _конец_таблицы(self) -> None:
        т = self.таблицы[-1]
        if len(self.уровни) > len(self.таблицы):
            self._конец_ячейки(т)
        self._конец_ряда(т)
        self.таблицы.pop()

    def закончить(self) -> None:
        try:
            self.close()
        except Exception:                               # noqa: BLE001, S110
            pass
        while self.таблицы:
            self._конец_таблицы()
        while len(self.уровни) > 1:
            self._перевод()
            линии, _ = self.уровни.pop()
            for л in линии:
                self.сб.абзац(л)
        self._перевод()


def _декод_html(b: bytes) -> str:
    m = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_-]+)", b[:8192], re.I)
    if m:
        try:
            return b.decode(m.group(1).decode("ascii"), "replace")
        except LookupError:
            pass
    for кодировка in ("utf-8-sig", "cp1251"):
        try:
            return b.decode(кодировка)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _mht(b: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    """Веб-архив Word (.mht, часто под именем .doc): HTML плюс картинки частями."""
    сообщение = email.message_from_bytes(b, policy=email.policy.default)
    html, картинки = "", []
    for k, часть in enumerate(сообщение.walk()):
        if часть.is_multipart():
            continue
        тип = часть.get_content_type()
        if тип == "text/html" and not html:
            try:
                html = часть.get_content()
            except Exception:                           # noqa: BLE001
                сырое = часть.get_payload(decode=True) or b""
                html = _декод_html(сырое)
        elif тип.startswith("image/") or тип == "application/octet-stream":
            данные = часть.get_payload(decode=True)
            if данные:
                картинки.append((str(часть.get("Content-Location") or f"часть{k}"), данные))
    return html, картинки


def _текст_похож(b: bytes) -> str:
    for кодировка in ("utf-8-sig", "cp1251"):
        try:
            т = b.decode(кодировка)
        except UnicodeDecodeError:
            continue
        печатных = sum(1 for c in т[:20000] if c.isprintable() or c in "\r\n\t")
        return т if печатных >= 0.95 * min(len(т), 20000) else ""
    return ""


# ───────────────────────────── основной разбор ─────────────────────────────

class _Разбор:
    """Один документ. Вложенный документ (altChunk) читается отдельным разбором:
    у него свои связи и свой пакет, и общими остаются только заметки."""

    def __init__(self, глубина: int, заметки: list[str]) -> None:
        self.глубина = глубина
        self.заметки = заметки
        self.пакет: _Пакет | None = None
        self.связи: dict[str, tuple[str, str, bool]] = {}
        self.строк = 0
        self.сказано_о_пределе = False
        self.доп_картинки: list[tuple[str, bytes]] = []
        self.доп_вложения: list[tuple[str, bytes]] = []
        self.объекты: set[str] = set()
        self.тег_корня = ""

    # ─── выбор разметки ───

    def документ(self, b) -> dict:
        if not isinstance(b, (bytes, bytearray, memoryview)):
            return _пусто(f"на входе не байты, а {type(b).__name__}")
        b = bytes(b)
        if not b.strip():
            return _пусто("пустой файл")
        if b[:2] == b"PK":
            return self._zip(b)
        if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            return self._ole(b)
        if b.lstrip()[:5] == b"{\\rtf":
            return self._rtf(b)
        return self._текстовый(b)

    def _zip(self, b: bytes) -> dict:
        try:
            пакет = _Пакет(self.заметки, zf=zipfile.ZipFile(io.BytesIO(b)), сырой=b)
        except Exception:                               # noqa: BLE001
            части = _по_заголовкам(b)
            if not части:
                return _пусто("ZIP битый: ни каталога, ни целых участников")
            self.заметки.append(f"ZIP без каталога (оборван?): спасено участников {len(части)}")
            пакет = _Пакет(self.заметки, части=части)
        self.пакет = пакет
        главная = self._главная()
        if главная:
            return self._ooxml(главная)
        if пакет.есть("content.xml"):
            return self._odf()
        if пакет.есть("xl/workbook.xml"):
            return _пусто("это книга Excel, а не документ Word", "xlsx")
        if пакет.есть("ppt/presentation.xml"):
            return _пусто("это презентация PowerPoint, а не документ Word", "pptx")
        return _пусто("ZIP-архив, а не документ: участников читает readers.участники_архива", "zip")

    def _ole(self, b: bytes) -> dict:
        if "EncryptedPackage".encode("utf-16-le") in b:
            return _пусто("документ зашифрован паролем: без пароля не читается", "docx-зашифрован")
        try:
            from library import readers                 # вызов из тестов и скриптов корня
        except ImportError:
            try:
                import readers                          # вызов из indexer.py (library в sys.path)
            except ImportError:
                return _пусто("старый .doc: нет модуля readers для внешнего читателя", "doc")
        текст, почему = readers.text_from_doc(b)
        внутр = _пусто(почему, "doc")
        внутр["сб"].абзац(текст)
        return внутр

    def _rtf(self, b: bytes) -> dict:
        try:
            from library import readers
        except ImportError:
            try:
                import readers
            except ImportError:
                return _пусто("RTF: нет модуля readers", "rtf")
        внутр = _пусто("", "rtf")
        внутр["сб"].абзац(readers.text_from_rtf(b))
        return внутр

    def _текстовый(self, b: bytes) -> dict:
        голова = b[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if голова.startswith(b"mime-version") or (b"mime-version:" in голова[:2048]
                                                  and b"content-type:" in голова):
            try:
                html, части = _mht(b)
            except Exception as e:                      # noqa: BLE001
                return _пусто(f"MHT не разобран ({type(e).__name__})", "mht")
            return self._html(html, части, "mht")
        # Граница имени тега обязательна: «<table:table» в плоском .fodt — это
        # не HTML, а подстрока «<table» в нём есть.
        похоже_html = bool(_HTML_МЕТКА.search(голова))
        if (голова.startswith(b"<") or голова[:2] in (b"\xff\xfe", b"\xfe\xff")) and not похоже_html:
            корень = _разобрать_xml(b, "XML", [])
            if корень is not None:
                и = _имя(корень)
                if и == "wordDocument":
                    return self._wordml2003(корень)
                if и == "package":
                    return self._плоский_пакет(корень)
                if и in ("document", "document-content"):
                    return self._odf_корень(корень, "fodt")
                return _пусто(f"XML, но не документ Word (корень «{и[:40]}»)", "xml")
        if похоже_html:
            return self._html(_декод_html(b), [], "html")
        текст = _текст_похож(b)
        if текст:
            внутр = _пусто("", "txt")
            внутр["сб"].абзац(текст)
            return внутр
        return _пусто("не документ Word: ни ZIP, ни XML, ни HTML, ни RTF")

    # ─── OOXML ───

    def _связи_части(self, часть: str) -> dict[str, tuple[str, str, bool]]:
        каталог, имя = posixpath.split(часть)
        путь = posixpath.join(каталог, "_rels", имя + ".rels")
        корень = _разобрать_xml(self.пакет.читать(путь), путь, self.заметки) if self.пакет else None
        out: dict[str, tuple[str, str, bool]] = {}
        if корень is None:
            return out
        for el in корень.iter():
            if _имя(el) != "Relationship":
                continue
            ид, цель = el.get("Id") or "", el.get("Target") or ""
            внешняя = (el.get("TargetMode") or "").lower() == "external"
            тип = (el.get("Type") or "").rstrip("/").rsplit("/", 1)[-1]
            out[ид] = (тип, цель if внешняя else _разрешить(каталог, цель), внешняя)
        return out

    def _главная(self) -> str | None:
        """Главная часть документа — по связям пакета, а не по имени.

        «word/document.xml» — только привычка Word. Сторонние сборщики пишут
        document2.xml или кладут часть в другую папку, и ссылка на неё есть
        только в _rels/.rels (в строгом OOXML — с другим адресом типа, но с тем
        же хвостом «officeDocument»).
        """
        пакет = self.пакет
        for тип, путь, внешняя in self._связи_части("").values():
            if тип == "officeDocument" and not внешняя and пакет.есть(путь):
                if путь.lower().startswith(("xl/", "ppt/")):
                    return None
                return путь
        типы = _разобрать_xml(пакет.читать("[Content_Types].xml"), "[Content_Types].xml", [])
        if типы is not None:
            for el in типы.iter():
                ct = (el.get("ContentType") or "").lower()
                if "wordprocessingml" in ct and ct.endswith("main+xml"):
                    путь = (el.get("PartName") or "").lstrip("/")
                    if пакет.есть(путь):
                        return путь
        for имя in sorted(пакет.список(), key=_естественно):
            if re.search(r"(^|/)document\d*\.xml$", имя.lower()) and "glossary" not in имя.lower():
                return имя
        return None

    def _часть(self, путь: str) -> tuple[_Сбор | None, list[str]]:
        корень = _разобрать_xml(self.пакет.читать(путь), путь, self.заметки)
        if корень is None:
            return None, []
        self.тег_корня = корень.tag if isinstance(корень.tag, str) else ""
        прежние = self.связи
        self.связи = self._связи_части(путь)
        сб = _Сбор()
        try:
            self.блок(корень, сб)
        except RecursionError:
            self.заметки.append(f"{путь}: вложенность глубже предела, хвост не прочитан")
        except Exception as e:                          # noqa: BLE001
            # Сбой одной части (колонтитула, сноски) не должен уносить весь
            # документ: собранное до сбоя остаётся, причина — в заметках.
            self.заметки.append(f"{путь}: обход прерван ({type(e).__name__})")
        пути = [self.связи[и][1] for и in сб.ссылки
                if и in self.связи and not self.связи[и][2]]
        self.связи = прежние
        return сб, пути

    def _ooxml(self, главная: str) -> dict:
        пакет = self.пакет
        сб, пути_тела = self._часть(главная)
        if сб is None:
            внутр = _пусто(f"{главная}: XML не разобран", "docx")
            сб = _Сбор()
        else:
            внутр = _пусто("", "docx")
            внутр["сб"] = сб
            # «Строгий» OOXML (ISO 29500 Strict) узнаётся по пространству имён
            # корня: локальные имена те же, и обход их не различает.
            if "purl.oclc.org/ooxml" in self.тег_корня:
                внутр["format"] = "docx-strict"

        связи = self._связи_части(главная)
        по_типу: dict[str, list[str]] = {}
        for тип, путь, внешняя in связи.values():
            if not внешняя:
                по_типу.setdefault(тип, []).append(путь)
        if not связи:
            # Связей нет (битый пакет) — части ищутся по привычным именам Word.
            for имя in пакет.список():
                м = re.match(r"word/(header|footer|footnotes|endnotes|comments)\d*\.xml$", имя, re.I)
                if м:
                    по_типу.setdefault(м.group(1).lower(), []).append(имя)

        def части(тип: str) -> list[tuple[_Сбор, list[str]]]:
            out = []
            for путь in sorted(set(по_типу.get(тип, [])), key=_естественно):
                с, п = self._часть(путь)
                if с is not None:
                    out.append((с, п))
            return out

        def без_повторов(собранное: list[tuple[_Сбор, list[str]]]) -> list[str]:
            # Колонтитулы первой, чётной и прочих страниц часто одинаковы:
            # один и тот же текст трижды — это шум, а не содержание.
            линии, виденные = [], set()
            for с, _ in собранное:
                ключ = tuple(с.абзацы)
                if ключ and ключ not in виденные:
                    виденные.add(ключ)
                    линии += с.абзацы
            return линии

        шапки, низы = части("header"), части("footer")
        сноски = части("footnotes") + части("endnotes")
        примечания = части("comments")
        smartart = части("diagramData")
        внутр["до"] = без_повторов(шапки)
        после = [л for с, _ in сноски for л in с.абзацы]
        после += без_повторов(низы)
        после += [л for с, _ in примечания for л in с.абзацы]
        после += без_повторов(smartart)

        картинки = _Картинки(self.заметки)
        пути = list(пути_тела)
        for собранное in (шапки, низы, сноски, примечания):
            for _, п in собранное:
                пути += п
        for д in [сб.данные] + [с.данные for с, _ in шапки + низы + сноски]:
            for имя, данные in д:
                картинки.добавить(имя, данные)
        медиа = [и for и in пакет.список() if "/media/" in "/" + и.lower()]
        виденные_пути: set[str] = set()
        for путь in пути + sorted(медиа, key=_естественно):
            ключ = _ключ(путь)
            if ключ in виденные_пути:
                continue
            виденные_пути.add(ключ)
            картинки.добавить(путь, пакет.читать(путь, МАКС_КАРТИНКА))
        for имя, данные in self.доп_картинки:
            картинки.добавить(имя, данные)
        после += картинки.линии
        внутр["после"] = после
        внутр["images"] = картинки.готово()
        внутр["embedded"] = self._вложения([и for и in пакет.список()
                                            if "/embeddings/" in "/" + и.lower()]) + self.доп_вложения
        return внутр

    def _вложения(self, имена: list[str]) -> list[tuple[str, bytes]]:
        out = []
        for имя in sorted(имена, key=_естественно):
            if len(out) >= МАКС_ВЛОЖЕНИЙ:
                self.заметки.append(f"вложенных файлов больше {МАКС_ВЛОЖЕНИЙ}: остальные не отданы")
                break
            д = self.пакет.читать(имя, МАКС_ВЛОЖЕНИЕ)
            if д:
                out.append((имя, д))
        return out

    def _ряд(self, сб: _Сбор, ячейки: list[str]) -> None:
        if self.строк >= МАКС_СТРОК:
            if not self.сказано_о_пределе:
                self.заметки.append(f"строк таблиц больше {МАКС_СТРОК}: остальные только в тексте")
                self.сказано_о_пределе = True
            линия = "\t".join(c for c in ячейки if c)
            if линия:
                сб.абзацы.append(линия)
                сб.табличные.append(линия)
            return
        self.строк += 1
        сб.ряд(ячейки)

    def блок(self, el, сб: _Сбор) -> None:
        """Блочный уровень WordprocessingML: абзацы, таблицы, их обёртки."""
        for ch in el:
            и = _имя(ch)
            if и == "p":
                self.абзац(ch, сб)
            elif и == "tbl":
                self.таблица(ch, сб)
            elif и in _ПРОПУСК_W:
                continue
            elif и == "AlternateContent":
                self.альтернатива(ch, None, сб)
            elif и == "altChunk":
                self.вставка(ch, сб)
            elif и == "binData":
                self.двоичное(ch, сб)
            elif и in ("r", "hyperlink", "fldSimple") or (и == "t" and not len(ch)):
                # Бег без абзаца — нарушение схемы, но сторонние сборщики так
                # пишут, и Word такой текст показывает: читаем как абзац.
                куски: list[str] = []
                вл = _Сбор()
                if и == "t":
                    куски.append(ch.text or "")
                else:
                    self.строка(ch, куски, вл)
                сб.абзац("".join(куски))
                сб.слить(вл)
            else:
                self.блок(ch, сб)

    def абзац(self, p, сб: _Сбор) -> None:
        куски: list[str] = []
        вл = _Сбор()
        self.строка(p, куски, вл)
        сб.абзац("".join(куски))
        # Надпись и сноска Word 2003 — после текста абзаца, к которому привязаны.
        сб.слить(вл)

    def строка(self, el, куски: list[str], вл: _Сбор) -> None:
        """Текст внутри абзаца. Куски склеиваются БЕЗ разделителя: Word рвёт
        слово на несколько <w:t> по правописанию и правкам, и прежняя склейка
        через пробел давала «На сос» вместо «Насос»."""
        for ch in el:
            и = _имя(ch)
            if и == "t":
                if len(ch):
                    self.строка(ch, куски, вл)
                else:
                    куски.append(ch.text or "")
            elif и in ("tab", "ptab"):
                куски.append("\t")
            elif и in ("br", "cr"):
                куски.append("\n")
            elif и == "noBreakHyphen":
                куски.append("-")
            elif и == "sym":
                куски.append(_символ(ch))
            elif и in _ПРОПУСК_W:
                continue
            elif и in _БЛОКИ_В_АБЗАЦЕ_W:
                self.блок(ch, вл)
            elif и == "p":
                self.абзац(ch, вл)
            elif и == "tbl":
                self.таблица(ch, вл)
            elif и == "AlternateContent":
                self.альтернатива(ch, куски, вл)
            elif и in ("blip", "imagedata"):
                ид = _связь(ch)
                if ид:
                    вл.ссылки.append(ид)
                self.строка(ch, куски, вл)
            elif и == "binData":
                self.двоичное(ch, вл)
            else:
                self.строка(ch, куски, вл)

    def альтернатива(self, el, куски: list[str] | None, сб: _Сбор) -> None:
        """mc:AlternateContent: Word пишет одну и ту же надпись дважды — в Choice
        (DrawingML) и в Fallback (VML). Берём Choice, Fallback — только если
        Choice пуст; иначе текст каждой надписи удваивался."""
        варианты = [ch for ch in el if _имя(ch) in ("Choice", "Fallback")]
        for вариант in варианты:
            к: list[str] = []
            вл = _Сбор()
            if куски is None:
                self.блок(вариант, вл)
            else:
                self.строка(вариант, к, вл)
            if "".join(к).strip() or not вл.пуст():
                if куски is not None:
                    куски.extend(к)
                сб.слить(вл)
                return

    def двоичное(self, el, сб: _Сбор) -> None:
        """w:binData Word 2003 XML: картинка base64 прямо в тексте документа."""
        try:
            д = base64.b64decode("".join((el.text or "").split()), validate=False)
        except (binascii.Error, ValueError):
            return
        if д:
            сб.данные.append((_атр(el, "name") or "binData", д))

    def таблица(self, tbl, сб: _Сбор) -> None:
        """Таблица Word: колонки на своих местах при любых объединениях.

        gridBefore — строка начинается не с первой колонки сетки: без пустых
        ячеек в начале вся строка съезжает влево. gridSpan — ячейка шириной в
        несколько колонок: занимает их все, текст в первой, остальные пустые.
        vMerge — ячейка, объединённая по вертикали: в продолжении Word хранит
        пустую ячейку, и строка оставалась без наименования; значение
        переносится вниз. Строка, где своего текста нет (только перенесённое),
        не выдаётся — иначе объединение рождало бы фантомные позиции.
        """
        слияния: dict[int, str] = {}
        for tr in _дети(tbl, "tr"):
            свойства = _ребёнок(tr, "trPr")
            до = _целое(_атр(_ребёнок(свойства, "gridBefore"), "val"), 0, 0, МАКС_ПРОЛЁТ)
            после = _целое(_атр(_ребёнок(свойства, "gridAfter"), "val"), 0, 0, МАКС_ПРОЛЁТ)
            ряд = [""] * до
            своё = False
            ожидание = _Сбор()
            for tc in _дети(tr, "tc"):
                tcpr = _ребёнок(tc, "tcPr")
                пролёт = _целое(_атр(_ребёнок(tcpr, "gridSpan"), "val"), 1, 1, МАКС_ПРОЛЁТ)
                верт = гор = None
                for x in (tcpr if tcpr is not None else ()):
                    л = _имя(x).lower()
                    if л == "vmerge":
                        верт = (_атр(x, "val") or "continue").lower()
                    elif л == "hmerge":
                        гор = (_атр(x, "val") or "continue").lower()
                ся = _Сбор()
                self.блок(tc, ся)
                текст = " ".join(ся.свои)
                кол = len(ряд)
                своя = bool(текст)
                if верт == "restart":
                    слияния[кол] = текст
                elif верт is not None:
                    if not текст:
                        текст = слияния.get(кол, "")
                else:
                    for k in range(кол, кол + пролёт):
                        слияния.pop(k, None)
                if гор is not None and гор != "restart":
                    текст, своя = "", False               # продолжение старого hMerge
                своё = своё or своя
                ряд.append(текст)
                ряд.extend([""] * (пролёт - 1))
                ожидание.строки += ся.строки
                ожидание.табличные += ся.табличные
                ожидание.ссылки += ся.ссылки
                ожидание.данные += ся.данные
            ряд.extend([""] * после)
            if своё:
                self._ряд(сб, ряд)
            # Вложенные таблицы — сразу после строки, в которой лежат.
            сб.строки += ожидание.строки
            сб.абзацы += ожидание.табличные
            сб.табличные += ожидание.табличные
            сб.ссылки += ожидание.ссылки
            сб.данные += ожидание.данные

    def вставка(self, el, сб: _Сбор) -> None:
        """w:altChunk — кусок другого формата (HTML, MHT, RTF, docx) внутри docx.

        Так собирают документы веб-площадки и генераторы отчётов: document.xml
        почти пуст, всё содержание — в afchunk.htm, и python-docx отдаёт ноль.
        """
        запись = self.связи.get(_связь(el) or "")
        if not запись or запись[2] or self.пакет is None:
            return
        if self.глубина >= МАКС_ГЛУБИНА:
            self.заметки.append(f"{запись[1]}: вставка глубже {МАКС_ГЛУБИНА} уровней не читается")
            return
        д = self.пакет.читать(запись[1])
        if not д:
            return
        внутр = _Разбор(self.глубина + 1, self.заметки).документ(д)
        вложенный: _Сбор = внутр["сб"]
        вложенный.ссылки = []
        for л in внутр["до"]:
            сб.абзац(л)
        сб.слить(вложенный)
        for л in внутр["после"]:
            сб.абзац(л)
        self.доп_картинки += внутр["images"]
        self.доп_вложения += внутр["embedded"]

    # ─── Word 2003 XML и плоский пакет ───

    def _wordml2003(self, корень) -> dict:
        """Word 2003 XML (w:wordDocument): та же разметка абзацев и таблиц, но
        колонтитулы лежат внутри sectPr, сноски — внутри текста, картинки —
        base64 в w:binData. Обход без привязки к пространству имён берёт всё."""
        внутр = _пусто("", "wordml2003")
        тело = next((el for el in корень.iter() if _имя(el) == "body"), корень)
        сб = внутр["сб"]
        try:
            self.блок(тело, сб)
        except RecursionError:
            self.заметки.append("Word 2003 XML: вложенность глубже предела")
        картинки = _Картинки(self.заметки)
        for имя, д in сб.данные:
            картинки.добавить(имя, д)
        внутр["после"] = картинки.линии
        внутр["images"] = картинки.готово()
        return внутр

    def _плоский_пакет(self, корень) -> dict:
        """Плоский пакет (pkg:package — «XML-документ Word»): все части в одном
        XML, двоичные — base64. Собирается в словарь частей и читается как docx."""
        части: dict[str, bytes] = {}
        for часть in корень:
            if _имя(часть) != "part":
                continue
            имя = (_атр(часть, "name") or "").lstrip("/")
            for содержимое in часть:
                л = _имя(содержимое)
                if л == "xmlData" and len(содержимое):
                    части[имя] = ET.tostring(содержимое[0], encoding="utf-8")
                elif л == "binaryData":
                    try:
                        части[имя] = base64.b64decode("".join((содержимое.text or "").split()))
                    except (binascii.Error, ValueError):
                        continue
        if not части:
            return _пусто("плоский пакет без частей", "flat-opc")
        self.пакет = _Пакет(self.заметки, части=части)
        главная = self._главная()
        if not главная:
            return _пусто("плоский пакет без документа Word", "flat-opc")
        внутр = self._ooxml(главная)
        внутр["format"] = "flat-opc"
        return внутр

    # ─── HTML ───

    def _html(self, html: str, части: list[tuple[str, bytes]], формат: str) -> dict:
        внутр = _пусто("", формат)
        сб = внутр["сб"]
        разборщик = _ХТМЛ(сб, self._ряд)
        try:
            разборщик.feed(html)
        except Exception as e:                          # noqa: BLE001
            self.заметки.append(f"HTML разобран не до конца ({type(e).__name__})")
        разборщик.закончить()
        картинки = _Картинки(self.заметки)
        for src in сб.ссылки:
            if src.startswith("data:image") and "," in src:
                try:
                    картинки.добавить("data-uri", base64.b64decode(src.split(",", 1)[1]))
                except (binascii.Error, ValueError):
                    pass
        сб.ссылки = []
        for имя, д in части:
            картинки.добавить(имя, д)
        внутр["после"] = картинки.линии
        внутр["images"] = картинки.готово()
        return внутр

    # ─── OpenDocument ───

    def _odf(self) -> dict:
        пакет = self.пакет
        манифест = пакет.читать("META-INF/manifest.xml") or b""
        if b"encryption-data" in манифест:
            return _пусто("документ OpenDocument зашифрован паролем: без пароля не читается", "odt")
        вид = (пакет.читать("mimetype", 200) or b"").decode("ascii", "replace")
        формат = {"text": "odt", "spreadsheet": "ods", "presentation": "odp"}.get(
            вид.rsplit(".", 1)[-1].split("-")[0], "odf") if вид else "odt"
        корень = _разобрать_xml(пакет.читать("content.xml"), "content.xml", self.заметки)
        if корень is None:
            return _пусто("content.xml не разобран", формат)
        стили = _разобрать_xml(пакет.читать("styles.xml"), "styles.xml", self.заметки)
        return self._odf_корень(корень, формат, стили)

    def _odf_корень(self, корень, формат: str, стили=None) -> dict:
        """Тело OpenDocument плюс колонтитулы (в .odt они в styles.xml, в плоском
        .fodt — в том же файле, в office:master-styles)."""
        внутр = _пусто("", формат)
        сб = внутр["сб"]
        тело = next((el for el in корень.iter() if _имя(el) == "body"), None)
        try:
            if тело is not None:
                self.odf_блок(тело, сб)
        except RecursionError:
            self.заметки.append("ODF: вложенность глубже предела, хвост не прочитан")
        except Exception as e:                          # noqa: BLE001
            self.заметки.append(f"ODF: обход прерван ({type(e).__name__})")
        шапки: list[_Сбор] = []
        низы: list[_Сбор] = []
        for источник in (стили, корень):
            if источник is None:
                continue
            for страница in источник.iter():
                if _имя(страница) != "master-page":
                    continue
                for ч in страница:
                    л = _имя(ч)
                    if л.startswith(("header", "footer")) and not л.endswith("style"):
                        с = _Сбор()
                        self.odf_блок(ч, с)
                        (шапки if л.startswith("header") else низы).append(с)

        def без_повторов(список: list[_Сбор]) -> list[str]:
            линии, виденные = [], set()
            for с in список:
                ключ = tuple(с.абзацы)
                if ключ and ключ not in виденные:
                    виденные.add(ключ)
                    линии += с.абзацы
            return линии

        внутр["до"] = без_повторов(шапки)
        после = без_повторов(низы)
        картинки = _Картинки(self.заметки)
        for имя, д in сб.данные + [д for с in шапки + низы for д in с.данные]:
            картинки.добавить(имя, д)
        if self.пакет is not None and формат != "fodt":
            пути = []
            for href in сб.ссылки + [h for с in шапки + низы for h in с.ссылки]:
                if "://" in href or href.startswith("../"):
                    continue
                пути.append(re.sub(r"^(\./)+", "", href))
            пути += sorted((и for и in self.пакет.список() if и.lower().startswith("pictures/")),
                           key=_естественно)
            for путь in пути:
                картинки.добавить(путь, self.пакет.читать(путь, МАКС_КАРТИНКА))
            внутр["embedded"] = self._вложения([
                и for и in self.пакет.список()
                if re.fullmatch(r"Object \d+", и) and _ключ(и) not in self.объекты])
        после += картинки.линии
        внутр["после"] = после
        внутр["images"] = картинки.готово()
        return внутр

    def odf_блок(self, el, сб: _Сбор) -> None:
        for ch in el:
            и = _имя(ch)
            if и in ("p", "h"):
                self.odf_абзац(ch, сб)
            elif и == "table":
                self.odf_таблица(ch, сб)
            elif и in _ПРОПУСК_ODF or ch.tag.startswith(_DC):
                continue
            elif и == "image":
                self.odf_картинка(ch, сб)
            elif и == "object":
                self.odf_объект(ch, сб)
            elif и == "binary-data":
                self.odf_двоичное(ch, сб)
            else:
                self.odf_блок(ch, сб)

    def odf_абзац(self, p, сб: _Сбор) -> None:
        куски: list[str] = []
        вл = _Сбор()
        self.odf_строка(p, куски, вл)
        сб.абзац("".join(куски))
        сб.слить(вл)

    def odf_строка(self, el, куски: list[str], вл: _Сбор) -> None:
        """Текст абзаца ODF: смешанное содержимое, текст лежит и в хвостах
        элементов (el.tail) — без них «Насос <span>ЦНС</span> 38-176» терял бы
        «38-176». Пробелы в тексте сжимаются по правилу ODF, явные — text:s."""
        if el.text:
            куски.append(_СЖАТЬ.sub(" ", el.text))
        for ch in el:
            и = _имя(ch)
            if и == "s":
                куски.append(" " * _целое(_атр(ch, "c"), 1, 1, 100))
            elif и == "tab":
                куски.append("\t")
            elif и == "line-break":
                куски.append("\n")
            elif и in _ПРОПУСК_ODF or ch.tag.startswith(_DC):
                pass
            elif и == "note":
                тело = _ребёнок(ch, "note-body")
                if тело is not None:
                    self.odf_блок(тело, вл)
            elif и in ("annotation", "text-box"):
                self.odf_блок(ch, вл)
            elif и in ("p", "h"):
                self.odf_абзац(ch, вл)
            elif и == "table":
                self.odf_таблица(ch, вл)
            elif и == "image":
                self.odf_картинка(ch, вл)
            elif и == "object":
                self.odf_объект(ch, вл)
            elif и == "binary-data":
                self.odf_двоичное(ch, вл)
            else:
                self.odf_строка(ch, куски, вл)
            if ch.tail:
                куски.append(_СЖАТЬ.sub(" ", ch.tail))

    def odf_картинка(self, el, сб: _Сбор) -> None:
        href = _атр(el, "href")
        if href:
            сб.ссылки.append(href)
        for ch in el:
            if _имя(ch) == "binary-data":
                self.odf_двоичное(ch, сб)

    def odf_двоичное(self, el, сб: _Сбор) -> None:
        try:
            д = base64.b64decode("".join((el.text or "").split()))
        except (binascii.Error, ValueError):
            return
        if д:
            сб.данные.append(("binary-data", д))

    def odf_объект(self, el, сб: _Сбор) -> None:
        """Встроенный объект .odt — чаще всего таблица Calc: у неё свой
        content.xml с той же разметкой table:table, и он читается здесь же."""
        href = re.sub(r"^(\./)+", "", _атр(el, "href") or "").rstrip("/")
        if not href or self.пакет is None or "://" in href or _ключ(href) in self.объекты:
            return
        self.объекты.add(_ключ(href))
        корень = _разобрать_xml(self.пакет.читать(href + "/content.xml"), href, self.заметки)
        тело = next((x for x in корень.iter() if _имя(x) == "body"), None) if корень is not None else None
        if тело is not None:
            self.odf_блок(тело, сб)

    def odf_таблица(self, tbl, сб: _Сбор) -> None:
        """Таблица ODF. Объединённые ячейки здесь ЕСТЬ в разметке — как
        table:covered-table-cell, поэтому колонки не съезжают сами; значение
        объединения по вертикали переносится вниз, как и в Word. Повторы
        (number-columns-repeated) раскрываются: Calc сжимает ими одинаковые
        соседние ячейки, и нераскрытый повтор сдвигал бы колонки."""
        верт: dict[int, tuple[str, int, int]] = {}
        номер = 0
        for tr in _odf_ряды(tbl):
            повтор = _целое(_атр(tr, "number-rows-repeated"), 1, 1, 1_000_000)
            ряд: list[str] = []
            своё = False
            ожидание = _Сбор()
            for tc in tr:
                и = _имя(tc)
                if и not in ("table-cell", "covered-table-cell"):
                    continue
                n = _целое(_атр(tc, "number-columns-repeated"), 1, 1, 1024)
                if и == "covered-table-cell":
                    for _ in range(n):
                        запись = верт.get(len(ряд))
                        ряд.append(запись[0] if запись and запись[2] < номер <= запись[1] else "")
                    continue
                ся = _Сбор()
                self.odf_блок(tc, ся)
                текст = " ".join(ся.свои)
                вниз = _целое(_атр(tc, "number-rows-spanned"), 1, 1, 1_000_000)
                for _ in range(n):
                    кол = len(ряд)
                    ряд.append(текст)
                    if вниз > 1:
                        верт[кол] = (текст, номер + вниз - 1, номер)
                    else:
                        верт.pop(кол, None)
                своё = своё or bool(текст)
                ожидание.строки += ся.строки
                ожидание.табличные += ся.табличные
                ожидание.ссылки += ся.ссылки
                ожидание.данные += ся.данные
            while ряд and not ряд[-1]:
                ряд.pop()                               # хвост пустых повторов Calc
            if своё:
                for _ in range(min(повтор, 100)):
                    self._ряд(сб, list(ряд))
            сб.строки += ожидание.строки
            сб.абзацы += ожидание.табличные
            сб.табличные += ожидание.табличные
            сб.ссылки += ожидание.ссылки
            сб.данные += ожидание.данные
            номер += повтор


def _дети(el, имя: str):
    """Строки и ячейки таблицы Word сквозь обёртки (sdt, customXml, ins), но
    не внутрь вложенных таблиц. Удалённые при рецензировании (del) пропускаются."""
    for ch in el:
        и = _имя(ch)
        if и == имя:
            yield ch
        elif и in _ОБЁРТКИ_W:
            yield from _дети(ch, имя)


def _odf_ряды(tbl):
    for ch in tbl:
        и = _имя(ch)
        if и == "table-row":
            yield ch
        elif и in ("table-header-rows", "table-rows", "table-row-group"):
            yield from _odf_ряды(ch)


def _символ(el) -> str:
    """w:sym — знак из шрифта-символа: «±», «°», «Ø» в технических требованиях."""
    код = _атр(el, "char") or ""
    try:
        n = int(код, 16)
    except ValueError:
        return ""
    if n >= 0xF000:
        n -= 0xF000
    шрифт = (_атр(el, "font") or "").lower()
    if "symbol" in шрифт:
        return СИМВОЛ.get(n, chr(n) if 0x20 <= n < 0x7F else "")
    if "wingdings" in шрифт or "webdings" in шрифт:
        return ""                                       # флажки и значки, не текст
    return chr(n) if n >= 0x20 else ""
