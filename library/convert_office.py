#!/usr/bin/env python3
"""Последний рубеж чтения: LibreOffice headless для форматов без своего читателя.

ЗАЧЕМ. Замер 23.09.2026: «старый office» — 1 754 файла, 423 без позиций;
«прочее» — 410, все без позиций. Для .doc, .xls с экзотикой, .ppt/.pptx,
.odt/.ods/.odp, .xlsb, .wps, .wpd, .pages и повреждённых .docx/.xlsx своего
читателя на Python нет и не будет, а LibreOffice открывает почти всё. Модуль
превращает такой файл в то, что читатели уже умеют: PDF, текст или строки
таблицы (все листы книги, таблицы документа Word).

ЧТО ВЫЯСНЕНО 23.09.2026 И ПОЧЕМУ КОД УСТРОЕН ТАК.

1. «LibreOffice тут таймаутит» (CLAUDE.md, docs/ПРАВИЛА-PDF.md: 119, 499 и
   899 с подряд) — это не медлительность. HTML-таблица в 1 568 строк → PDF за
   5,2 с (93 страницы), книга с оформлением на все 1 024 столбца → PDF за 1,2 с.
   Причина в цепочке запуска: /usr/bin/soffice — shell-скрипт, он делает exec
   в oosplash, а тот порождает soffice.bin. subprocess.run(timeout=…) убивает
   только oosplash, soffice.bin остаётся сиротой и держит профиль и канал
   /tmp/OSL_PIPE_…; каждый следующий вызов с ТЕМ ЖЕ профилем (ove/tools/
   build_rfq_pdf.py профиль не задаёт вовсе) отдаёт работу сироте и ждёт её.
   Воспроизведено: после таймаута сирота сам дописал PDF, второй вызов ждал его.
   Отсюда: своя группа процессов и killpg, свой профиль на каждый вызов.
2. Код возврата 0 бывает и при «source file could not be loaded». Успех —
   только наличие выходного файла.
3. В локали POSIX выгрузка всех листов в CSV при кириллических именах листов
   НЕ ПИШЕТ НИ ОДНОГО файла (имя листа уходит в «??????»), код возврата 0.
   Процессу ставится LC_ALL=C.UTF-8.
4. Без модулей Writer, Calc и Impress (стоит один libreoffice-core) любой файл
   даёт «source file could not be loaded» за секунду. В этой среде так и было
   до установки libreoffice-writer/-calc/-impress: soffice есть, прочесть им
   нечего. Поэтому «нет LibreOffice» и «LibreOffice не открыл» — разные причины.
5. Формат LibreOffice определяет по содержимому: .xls с расширением .doc
   открывается в Calc. Но фильтр ВЫВОДА обязан совпасть с приложением, которое
   открыло файл: текст из Calc фильтром Writer не выгрузить. Приложение
   читается из строки «convert … as a Calc document», и при промахе делается
   второй вызов фильтром этого приложения.

ПРАВИЛО 17 (журнал публичный): модуль ничего не печатает, только возвращает.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import Path

#: Таймаут по умолчанию, секунды. Действует на ВЕСЬ вызов, а не на попытку:
#: неизвестный OLE2 пробуется до трёх раз, и три таймаута подряд на одном файле
#: остановили бы прогон на шесть минут.
ТАЙМАУТ = 120.0

#: Предел строк — тот же, что у читателей readers.py и indexer.py.
МАКС_СТРОК = 20000

#: Сколько расширений пробовать. Больше не нужно: формат LibreOffice узнаёт по
#: содержимому, расширение лишь подсказывает, с какого детектора начать.
МАКС_ПОПЫТОК = 4

#: Предел распакованного при починке ZIP: настоящая спецификация влезает, бомба —
#: нет. Тот же порядок, что у участников архива в readers.py.
МАКС_РАСПАКОВКА = 256 * 1024 * 1024

#: Где LibreOffice создаёт канал единственного экземпляра. Путь зашит в
#: sal (osl/unx/pipe.cxx) и от TMPDIR не зависит.
КАТАЛОГИ_КАНАЛОВ = ("/tmp", "/var/tmp")

WRITER, CALC, IMPRESS, DRAW = "writer", "calc", "impress", "draw"

_СЕМЕЙСТВА = {
    WRITER: (".doc", ".docx", ".docm", ".dot", ".dotx", ".dotm", ".odt", ".ott", ".fodt",
             ".rtf", ".wps", ".wpd", ".wp", ".wp5", ".wp6", ".pages", ".txt", ".html",
             ".htm", ".xhtml", ".abw", ".lwp", ".sxw", ".wri", ".hwp", ".odm"),
    CALC: (".xls", ".xlsx", ".xlsm", ".xlsb", ".xlt", ".xltx", ".xltm", ".xlw", ".ods",
           ".ots", ".fods", ".csv", ".numbers", ".sxc", ".dbf", ".slk", ".dif", ".xlr",
           ".wk1", ".wks"),
    IMPRESS: (".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".pot", ".potx", ".odp", ".otp",
              ".fodp", ".key", ".sxi"),
    DRAW: (".odg", ".otg", ".fodg", ".vsd", ".vsdx", ".vdx", ".pub", ".cdr", ".sxd"),
}
#: Расширение → приложение LibreOffice. «.xml» намеренно без приложения: под ним
#: и Word 2003 XML, и SpreadsheetML (выгрузка 1С), решает содержимое.
СЕМЕЙСТВО = {расш: сем for сем, все in _СЕМЕЙСТВА.items() for расш in все}
СЕМЕЙСТВО[".xml"] = ""

#: Подвиды indexer.подвид, у которых имя не совпадает с расширением.
_ПОДВИДЫ = {"spreadsheetml": ".xml", "ole2_разобрать": "", "zip": "", "неизвестно": ""}

#: Порядок проб для OLE2, чьих потоков не узнали: книга и документ встречаются
#: чаще презентации (замер «старого office»).
OLE2_ПОРЯДОК = (".doc", ".xls", ".ppt")

#: Потоки OLE2 → расширение или отказ. Имя ищется в UTF-16LE на границе записи
#: каталога (128 байт): русский .doc хранит текст в UTF-16, и слово «Workbook»
#: в тексте без проверки границы дало бы ложную книгу.
ПОТОКИ_OLE2: tuple[tuple[str, str, str], ...] = (
    ("EncryptedPackage", "", "зашифрован паролем (OOXML в контейнере OLE2)"),
    ("__substg1.0_", "", "письмо Outlook (.msg): LibreOffice его не открывает"),
    ("WordDocument", ".doc", ""),
    ("Workbook", ".xls", ""),
    ("Book", ".xls", ""),                               # BIFF5, Excel 5/95
    ("PowerPoint Document", ".ppt", ""),
    ("MatOST", ".wps", ""),                             # Microsoft Works
    ("VisioDocument", ".vsd", ""),
    ("Quill", ".pub", ""),                              # Microsoft Publisher
    ("WksSSWorkBook", ".xlr", ""),                      # таблица Works
)
_ПРЕФИКСНЫЕ = {"__substg1.0_"}                          # за именем идёт номер свойства

#: Подписи по первым байтам вне OLE2 и ZIP.
ПОДПИСИ: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", ".pdf"),
    (b"{\\rtf", ".rtf"),
    (b"\xffWPC", ".wpd"),                               # WordPerfect
    (b"\xdb\xa5", ".doc"),                              # Word 2.0
    (b"\x09\x00\x04\x00", ".xls"), (b"\x09\x02\x06\x00", ".xls"),
    (b"\x09\x04\x06\x00", ".xls"),                      # BIFF2-4 без OLE2
)

_ODF_MIME = {"text": ".odt", "text-template": ".ott", "text-master": ".odm",
             "spreadsheet": ".ods", "presentation": ".odp", "graphics": ".odg"}
_ODF_ПЛОСКИЙ = {".odt": ".fodt", ".ods": ".fods", ".odp": ".fodp", ".odg": ".fodg"}
_SUN_MIME = {b"writer": ".sxw", b"calc": ".sxc", b"impress": ".sxi", b"draw": ".sxd"}

#: Все листы книги в CSV (признак 12 = -1, каждый лист — свой файл in-<лист>.csv):
#: запятая, кавычка, UTF-8 (76), значения «как показаны», без формул.
ЦЕЛЬ_CSV = "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true,false,false,-1"
#: Ключи целей: приложение; «*» — любое приложение; «» — приложение неизвестно
#: (тогда пробуется эта цель, а при промахе — цель того, кто открыл файл).
ЦЕЛЬ_PDF = {"*": "pdf"}
#: Текст: Writer пишет его сам; из книги — через CSV; у Impress и Draw текстового
#: фильтра нет, поэтому плоский ODF, откуда текст берётся разбором XML.
ЦЕЛЬ_ТЕКСТ = {"": "txt:Text (encoded):UTF8", WRITER: "txt:Text (encoded):UTF8",
              CALC: ЦЕЛЬ_CSV, IMPRESS: "fodp", DRAW: "fodg"}
#: Строки: из книги — CSV, из документа — HTML, где таблицы остаются таблицами
#: (текстовая выгрузка Writer разворачивает таблицу по ячейке на строку).
ЦЕЛЬ_СТРОКИ = {"": ЦЕЛЬ_CSV, CALC: ЦЕЛЬ_CSV, WRITER: "html"}

НЕТ_ЦЕЛИ = {IMPRESS: "презентация: таблиц не извлекаем, текст — в_текст",
            DRAW: "рисунок: таблиц не извлекаем, текст — в_текст"}

_ПРИЛОЖЕНИЕ = re.compile(r"\bas an? ([A-Za-z/ ]+?) document\b")
_ЛИСТ = re.compile(r"^Writing sheet .* -> (.+)$", re.MULTILINE)


# ─────────────────────────────── выбор расширения

def из_подсказки(подсказка: str) -> str:
    """Расширение из подсказки: имени файла, «.xlsb», «xlsb» или подвида indexer."""
    п = (подсказка or "").strip().lower()
    if not п:
        return ""
    if п in _ПОДВИДЫ:
        return _ПОДВИДЫ[п]
    расш = Path(п).suffix if "." in п else "." + п
    return расш if расш in СЕМЕЙСТВО or расш == ".pdf" else ""


def потоки_ole2(b: bytes) -> list[tuple[str, str]]:
    """Узнанные потоки OLE2 как (расширение, отказ), по порядку записей каталога.

    Первым идёт тот, чья запись раньше: вложенный объект (книга внутри
    документа Word) лежит в каталоге после основного потока, поэтому по одному
    лишь наличию имени .doc с таблицей внутри опознался бы книгой.
    """
    найдено: list[tuple[int, str, str]] = []
    for имя, расш, отказ in ПОТОКИ_OLE2:
        образ = имя.encode("utf-16-le")
        if имя not in _ПРЕФИКСНЫЕ:
            образ += b"\x00\x00"
        начало = b.find(образ, 512)
        while начало != -1:
            if начало % 128 == 0:
                найдено.append((начало, расш, отказ))
                break
            начало = b.find(образ, начало + 1)
    return [(р, о) for _, р, о in sorted(найдено)]


def _из_zip(b: bytes) -> str:
    """Какой офисный файл внутри PK-контейнера; «» — просто архив.

    Голова смотрится ВСЕГДА, а каталог ZIP — если открылся: у обрезанного
    .docx каталога в хвосте нет, но локальные заголовки первых участников на
    месте, а это ровно тот повреждённый файл, ради которого нужен LibreOffice.
    """
    голова = b[:65536]
    if голова[30:38] == b"mimetype":
        тип = голова[38:120]
        for вид, расш in sorted(_ODF_MIME.items(), key=lambda x: -len(x[0])):
            if тип.startswith(b"application/vnd.oasis.opendocument." + вид.encode()):
                return расш
        for вид, расш in _SUN_MIME.items():
            if тип.startswith(b"application/vnd.sun.xml." + вид):
                return расш
    имена = голова
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            имена += "\n".join(z.namelist()).encode("utf-8", "replace")
    except Exception:                                   # noqa: BLE001, S110 — битый ZIP смотрим по голове
        pass
    if b"word/" in имена:
        return ".docx"
    if b"xl/workbook.bin" in имена:
        return ".xlsb"
    if b"xl/" in имена:
        return ".xlsx"
    if b"ppt/" in имена:
        return ".pptx"
    if b"visio/" in имена:
        return ".vsdx"
    if b"Index/Document.iwa" in имена:
        return ".pages"
    return ""


def _из_текста(b: bytes) -> str:
    """Текстовые форматы. «» — не текст."""
    голова = b[:4096]
    # Управляющие байты — признак двоичного файла, даже если он «декодируется»:
    # LibreOffice открыл бы его как текст и вернул «####…» (замер 23.09.2026).
    if b"\x00" in голова or sum(x < 32 and x not in (9, 10, 12, 13) for x in голова) > len(голова) // 100:
        return ""
    for кодировка in ("utf-8", "cp1251"):
        try:
            текст = голова.decode(кодировка)
            break
        except UnicodeDecodeError:
            continue
    else:
        return ""
    низ = текст.lstrip("﻿ \r\n\t").lower()[:2000]
    if not низ.startswith("<"):
        return ".txt"
    if "office:document" in низ:
        for вид, расш in sorted(_ODF_MIME.items(), key=lambda x: -len(x[0])):
            if f"opendocument.{вид}\"" in низ:
                return _ODF_ПЛОСКИЙ.get(расш, ".xml")
    if "<html" in низ or "<!doctype html" in низ:
        return ".html"
    return ".xml"


def опознать(b: bytes) -> tuple[list[str], bool, str]:
    """Что говорит само содержимое: (расширения, уверенно ли, отказ).

    Пустой список — формат не опознан: не OLE2, не ZIP, не подпись и не текст.
    """
    if b.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        потоки = потоки_ole2(b)
        if потоки and потоки[0][1]:
            return [], True, потоки[0][1]
        свои = [р for р, _ in потоки if р]
        # Узнанный поток — и хватит: формат LibreOffice определяет по содержимому,
        # и .doc, который не открылся как .doc, как .xls не откроется тоже.
        return (свои or list(OLE2_ПОРЯДОК)), bool(свои), ""
    if b[:2] == b"PK":
        расш = _из_zip(b)
        return ([расш] if расш else []), bool(расш), "" if расш else "zip: архив, а не офисный файл"
    подпись = next((р for п, р in ПОДПИСИ if b.startswith(п)), "")
    if подпись:
        return [подпись], True, ""
    расш = _из_текста(b)
    return ([расш] if расш else []), расш not in ("", ".txt"), ""


def расширения(b: bytes, подсказка: str = "") -> tuple[list[str], str]:
    """Расширения входного файла по порядку проб и отказ, если пробовать нечего.

    Решает СОДЕРЖИМОЕ, подсказка — запасной ход: имени у вложения портала часто
    нет или оно врёт (выгрузка 1С зовётся .xls, а внутри XML). Когда содержимое
    не говорит ничего определённого, первой идёт подсказка.
    """
    подск = из_подсказки(подсказка)
    свои, уверенно, отказ = опознать(b)
    if отказ and (свои or not подск or b[:2] != b"PK"):
        return [], отказ
    порядок = свои + [подск] if уверенно else [подск] + свои
    итог: list[str] = []
    for р in порядок:
        if р and р not in итог:
            итог.append(р)
    # Ни содержимое, ни подсказка не сказали ничего: пусть LibreOffice решает сам,
    # файл без расширения он опознаёт по содержимому.
    return (итог or [""]), ""


def починить_zip(b: bytes) -> bytes | None:
    """Пересобрать PK-контейнер из того, что в нём читается; None — нечего.

    LibreOffice строже zipfile: замер 23.09.2026 — .docx с мусором в хвосте
    zipfile открывает, а LibreOffice нет; без центрального каталога (обрыв
    загрузки) не открывает никто. Пересборка даёт чистый каталог: если zipfile
    открыл контейнер — из его участников, если нет — по локальным заголовкам,
    которые у обрезанного файла целы.
    """
    участники: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            всего = 0
            for св in z.infolist():
                всего += св.file_size
                if св.is_dir() or всего > МАКС_РАСПАКОВКА:
                    continue
                try:
                    участники.append((св.filename, z.read(св)))
                except Exception:                       # noqa: BLE001, S112 — битый участник, не весь файл
                    continue
    except Exception:                                   # noqa: BLE001 — каталога нет: идём по заголовкам
        участники = _по_заголовкам(b)
    if not участники:
        return None
    участники += _служебные(участники)
    выход = io.BytesIO()
    with zipfile.ZipFile(выход, "w") as z:
        for имя, данные in участники:
            # ODF требует mimetype первым и несжатым — по нему его и узнают.
            z.writestr(имя, данные, compress_type=zipfile.ZIP_STORED if имя == "mimetype"
                       else zipfile.ZIP_DEFLATED)
    return выход.getvalue()


_OOXML = "application/vnd.openxmlformats-officedocument."
#: Типы частей OOXML для восстановленного [Content_Types].xml.
_ТИПЫ_ЧАСТЕЙ: tuple[tuple[str, str], ...] = (
    (r"word/document\.xml", "wordprocessingml.document.main+xml"),
    (r"word/styles\.xml", "wordprocessingml.styles+xml"),
    (r"word/settings\.xml", "wordprocessingml.settings+xml"),
    (r"word/fontTable\.xml", "wordprocessingml.fontTable+xml"),
    (r"word/numbering\.xml", "wordprocessingml.numbering+xml"),
    (r"xl/workbook\.xml", "spreadsheetml.sheet.main+xml"),
    (r"xl/worksheets/sheet\d+\.xml", "spreadsheetml.worksheet+xml"),
    (r"xl/sharedStrings\.xml", "spreadsheetml.sharedStrings+xml"),
    (r"xl/styles\.xml", "spreadsheetml.styles+xml"),
    (r"ppt/presentation\.xml", "presentationml.presentation.main+xml"),
    (r"ppt/slides/slide\d+\.xml", "presentationml.slide+xml"),
    (r"ppt/slideLayouts/slideLayout\d+\.xml", "presentationml.slideLayout+xml"),
    (r"ppt/slideMasters/slideMaster\d+\.xml", "presentationml.slideMaster+xml"),
    (r".*theme/theme\d+\.xml", "theme+xml"),
)
_ГЛАВНЫЕ = ("word/document.xml", "xl/workbook.xml", "ppt/presentation.xml")
#: Части с содержимым: их берём и оборванными — лучше половина текста, чем ничего.
_СОДЕРЖИМОЕ = re.compile(r"word/document\.xml|xl/worksheets/sheet\d+\.xml|xl/sharedStrings\.xml"
                         r"|ppt/slides/slide\d+\.xml|content\.xml")


def _служебные(участники: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Недостающие служебные части по уцелевшим: [Content_Types].xml и
    _rels/.rels у OOXML, META-INF/manifest.xml у ODF.

    LibreOffice пишет их ПОСЛЕДНИМИ участниками (манифест ODF — ещё и после
    миниатюры), и при обрыве загрузки они теряются первыми, а без них пакет не
    опознаётся, хотя document.xml или content.xml цел (замер 23.09.2026).
    """
    имена = [имя for имя, _ in участники]
    данные = dict(участники)
    if "mimetype" in данные and "content.xml" in данные and "META-INF/manifest.xml" not in данные:
        тип = данные["mimetype"].decode("ascii", "replace").strip()
        # Перечислить надо ВСЕХ участников: неупомянутый в манифесте LibreOffice
        # считает повреждением пакета и файл не открывает.
        виды = {".xml": "text/xml", ".rdf": "application/rdf+xml", ".png": "image/png"}
        части = "".join(f'<manifest:file-entry manifest:full-path="{и}" '
                        f'manifest:media-type="{виды.get(Path(и).suffix, "")}"/>'
                        for и in имена if и != "mimetype")
        return [("META-INF/manifest.xml", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
            'manifest:version="1.3"><manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
            f'manifest:media-type="{тип}"/>{части}</manifest:manifest>').encode())]
    главная = next((г for г in _ГЛАВНЫЕ if г in имена), "")
    if not главная:
        return []
    out: list[tuple[str, bytes]] = []
    if "[Content_Types].xml" not in имена:
        замены = "".join(f'<Override PartName="/{и}" ContentType="{_OOXML}{т}"/>'
                         for и in имена for шаблон, т in _ТИПЫ_ЧАСТЕЙ if re.fullmatch(шаблон, и))
        out.append(("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f"{замены}</Types>").encode()))
    if "_rels/.rels" not in имена:
        out.append(("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'relationships/officeDocument" Target="{главная}"/></Relationships>').encode()))
    return out


_ТЕГ = re.compile(rb"<(/?)([A-Za-z_][\w.:-]*)[^<>]*?(/?)>|<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>", re.S)


def дописать_xml(данные: bytes) -> bytes:
    """Закрыть оборванный XML: отрезать по последнему целому тегу и закрыть
    незакрытые элементы в обратном порядке.

    LibreOffice недописанный document.xml не открывает вовсе (замер
    23.09.2026: .docx, оборванный внутри document.xml, — ни строки), а
    дописанный читает до места обрыва.
    """
    хвост = данные.rfind(b">")
    if хвост == -1:
        return данные
    данные = данные[:хвост + 1]
    стек: list[bytes] = []
    for м in _ТЕГ.finditer(данные):
        закрыв, имя, сам = м.group(1), м.group(2), м.group(3)
        if not имя or сам:
            continue
        if not закрыв:
            стек.append(имя)
        elif имя in стек:
            while стек and стек.pop() != имя:
                pass
    return данные + b"".join(b"</" + и + b">" for и in reversed(стек))


def _по_заголовкам(b: bytes) -> list[tuple[str, bytes]]:
    """Участники по локальным заголовкам PK\\x03\\x04, без центрального каталога.

    Размер сжатых данных в заголовке бывает нулевым (флаг 8: размер в хвосте
    участника), поэтому deflate читается до конца своего потока — он
    самоограничен. Оборванный участник берётся, только если в нём содержимое
    (_СОДЕРЖИМОЕ): недописанный служебный fontTable.xml или styles.xml роняет
    открытие всего документа, а без него LibreOffice обходится (замер
    23.09.2026: .docx, обрезанный на 50 и 60 %, открылся только без него).
    """
    out: list[tuple[str, bytes]] = []
    всего = 0
    поз = b.find(b"PK\x03\x04")
    while поз != -1 and len(b) - поз >= 30:
        _, _, флаги, метод, _, _, _, сжато, _, дл_имени, дл_доп = struct.unpack("<IHHHHHIIIHH", b[поз:поз + 30])
        начало = поз + 30 + дл_имени + дл_доп
        имя = b[поз + 30:поз + 30 + дл_имени].decode("utf-8" if флаги & 0x800 else "cp437", "replace")
        данные, конец, оборван = None, поз + 4, False
        if метод == 8:
            кусок = b[начало:начало + сжато] if сжато and not флаги & 8 else b[начало:]
            распаковка = zlib.decompressobj(-15)
            try:
                данные = распаковка.decompress(кусок, МАКС_РАСПАКОВКА - всего + 1)
                конец = начало + len(кусок) - len(распаковка.unused_data)
                оборван = not распаковка.eof
            except zlib.error:
                данные = None
        elif метод == 0 and not флаги & 8:
            данные, конец = b[начало:начало + сжато], начало + сжато
            оборван = len(данные) < сжато
        if оборван:
            данные = дописать_xml(данные) if _СОДЕРЖИМОЕ.fullmatch(имя) and данные else None
        if данные is not None and имя and not имя.endswith("/"):
            всего += len(данные)
            if всего > МАКС_РАСПАКОВКА:
                break
            out.append((имя, данные))
        поз = b.find(b"PK\x03\x04", max(конец, поз + 4))
    return out


# ─────────────────────────────── запуск LibreOffice

def найти_soffice() -> str | None:
    """Путь к soffice. SOFFICE в окружении — для установки не в PATH (/opt/…)."""
    свой = os.environ.get("SOFFICE", "").strip()
    if свой:
        return свой if Path(свой).exists() else None
    return shutil.which("soffice") or shutil.which("libreoffice")


def таймаут() -> float:
    """CONVERT_TIMEOUT из окружения; мусор и неположительное — умолчание."""
    try:
        значение = float(os.environ.get("CONVERT_TIMEOUT", "") or ТАЙМАУТ)
    except ValueError:
        return ТАЙМАУТ
    return значение if значение > 0 else ТАЙМАУТ


def команда(soffice: str, профиль: str, цель: str, выход: Path, вход: Path) -> list[str]:
    """Строка запуска. Профиль — СВОЙ у каждого вызова: второй soffice с общим
    профилем не работает сам, а передаёт файл первому и ждёт его (п. 1 шапки)."""
    return [soffice, f"-env:UserInstallation={профиль}", "--headless", "--norestore",
            "--nolockcheck", "--convert-to", цель, "--outdir", str(выход), str(вход)]


def окружение(каталог: Path) -> dict[str, str]:
    """Окружение процесса. LC_ALL — иначе листы с кириллицей теряются (п. 3
    шапки); TMPDIR — чтобы lu*.tmp убитого процесса ушли вместе с каталогом
    вызова, а не копились в /tmp."""
    env = dict(os.environ)
    env["LC_ALL"] = env["LANG"] = "C.UTF-8"
    env["TMPDIR"] = str(каталог)
    return env


def имя_канала(профиль: str) -> str:
    """Имя канала единственного экземпляра для профиля. Формула LibreOffice
    (desktop/source/app/officeipcthread.cxx): MD5 от строки профиля в UTF-16,
    байты в hex БЕЗ ведущих нулей. Сверено с каналами, оставшимися в /tmp."""
    хеш = "".join(format(x, "x") for x in hashlib.md5(профиль.encode("utf-16-le")).digest())  # noqa: S324
    return f"OSL_PIPE_{os.getuid()}_SingleOfficeIPC_{хеш}"


def _убрать_канал(профиль: str) -> None:
    """Канал убитого процесса остаётся в /tmp навсегда: закрыть его было
    некому. Имя вычисляется, поэтому удаляется ровно свой, а не соседский."""
    for каталог in КАТАЛОГИ_КАНАЛОВ:
        try:
            (Path(каталог) / имя_канала(профиль)).unlink()
        except OSError:
            continue


def запустить(cmd: list[str], env: dict[str, str], срок: float) -> tuple[int | None, str, str]:
    """Запуск до срока (time.monotonic). Код None — таймаут.

    Своя сессия, а значит своя группа процессов: oosplash и soffice.bin в ней
    вместе, и killpg снимает обоих. preexec_fn не годится — indexer читает
    файлы потоками, а preexec_fn в многопоточном процессе может зависнуть.
    """
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env, start_new_session=True)
    try:
        вывод, ошибки = p.communicate(timeout=max(срок - time.monotonic(), 0.01))
    except subprocess.TimeoutExpired:
        _убить(p)
        p.communicate()
        return None, "", ""
    _убить(p)                          # отставшие потомки не должны держать каталог
    return p.returncode, вывод.decode("utf-8", "replace"), ошибки.decode("utf-8", "replace")


def _убить(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def приложение(вывод: str) -> str:
    """Каким приложением LibreOffice открыл файл, по строке «as a Calc document»."""
    м = _ПРИЛОЖЕНИЕ.search(вывод)
    if not м:
        return ""
    имя = м.group(1).lower()
    for сем in (WRITER, CALC, IMPRESS, DRAW):
        if имя.startswith(сем):
            return сем
    return ""


def _собрать(выход: Path, цель: str, вывод: str) -> list[tuple[str, bytes]]:
    """Выходные файлы по порядку. Листы книги — в порядке, в каком LibreOffice
    их писал (строки «Writing sheet … -> путь»), а не по алфавиту имён."""
    суффикс = "." + цель.split(":", 1)[0]
    файлы = [ф for ф in выход.iterdir() if ф.is_file() and ф.suffix.lower() == суффикс]
    if суффикс == ".pdf":
        файлы = [ф for ф in файлы if ф.stat().st_size > 0]
    порядок = [Path(с.strip()).name for с in _ЛИСТ.findall(вывод)]

    def ключ(ф: Path) -> tuple[int, int, str]:
        место = порядок.index(ф.name) if ф.name in порядок else len(порядок)
        return место, ф.stat().st_mtime_ns, ф.name
    return [(ф.name, ф.read_bytes()) for ф in sorted(файлы, key=ключ)]


def _причина(вывод: str, ошибки: str, код: int, расш: str) -> str:
    весь = вывод + "\n" + ошибки
    if "source file could not be loaded" in весь:
        return f"LibreOffice не открыл файл как {расш or 'файл без расширения'}"
    if "no export filter" in весь.lower():
        return "LibreOffice: нет фильтра вывода"
    if "impl_store" in весь or "verify input parameters" in весь:
        return "LibreOffice открыл файл, но не выгрузил результат"
    return f"LibreOffice не дал вывода (код {код})"


def цель_для(цели: dict[str, str], сем: str) -> str | None:
    """Цель --convert-to для приложения; None — такому приложению выгружать нечего
    (строки таблицы из презентации)."""
    if сем in цели:
        return цели[сем]
    if "*" in цели:
        return цели["*"]
    return None if сем else цели.get("")


def конвертировать(b: bytes, подсказка: str = "", цели: dict[str, str] | None = None,
                   ) -> tuple[list[tuple[str, bytes]], str, str]:
    """Конвертация в формат по приложению: цели = {приложение: цель --convert-to}
    (ключи — см. ЦЕЛЬ_PDF). Возвращает (выходные файлы по порядку, приложение,
    отказ). Не бросает: любая неудача — названный отказ.

    Повреждённый PK-контейнер, который LibreOffice не открыл, пересобирается и
    пробуется ещё раз в пределах того же срока.
    """
    цели = цели or ЦЕЛЬ_PDF
    if not b:
        return [], "", "пустой файл"
    if not найти_soffice():
        return [], "", "нет LibreOffice"
    срок = time.monotonic() + таймаут()
    начало_zip = b.find(b"PK\x03\x04", 0, 65536)
    if начало_zip > 0 and not опознать(b)[0] and _из_zip(b[начало_zip:]):
        # Мусор ПЕРЕД контейнером: LibreOffice открыл бы такое как текст в UTF-16.
        b = починить_zip(b) or b
    файлы, сем, отказ = _конвертировать(b, подсказка, цели, срок)
    # Только для самого PK-контейнера: .doc со встроенной книгой тоже несёт
    # PK\x03\x04, и «починка» подменила бы документ его вложением.
    if not файлы and "не открыл" in отказ and b[:2] == b"PK":
        починенный = починить_zip(b)
        if починенный and починенный != b:
            файлы, сем, ещё = _конвертировать(починенный, подсказка, цели, срок)
            if not файлы:
                отказ = f"{отказ}; после пересборки ZIP: {ещё}"
    return файлы, сем, "" if файлы else отказ


def _конвертировать(b: bytes, подсказка: str, цели: dict[str, str], срок: float,
                    ) -> tuple[list[tuple[str, bytes]], str, str]:
    кандидаты, отказ = расширения(b, подсказка)
    if отказ:
        return [], "", отказ
    soffice = найти_soffice()
    if not soffice:
        return [], "", "нет LibreOffice"
    if срок - time.monotonic() <= 0:
        return [], "", f"LibreOffice: таймаут {таймаут():g} с"
    предел = таймаут()
    отказы: list[str] = []
    with tempfile.TemporaryDirectory(prefix="convert_office_", ignore_cleanup_errors=True) as к:
        каталог = Path(к)
        профиль = (каталог / "profile").as_uri()
        env = окружение(каталог)

        def один(вход: Path, цель: str) -> tuple[int | None, list[tuple[str, bytes]], str, str]:
            выход = каталог / "out"
            shutil.rmtree(выход, ignore_errors=True)
            выход.mkdir()
            try:
                код, вывод, ошибки = запустить(команда(soffice, профиль, цель, выход, вход), env, срок)
            except OSError as e:
                return -1, [], "", f"сбой запуска LibreOffice ({type(e).__name__})"
            if код is None:
                _убрать_канал(профиль)
                return None, [], "", ""
            return код, _собрать(выход, цель, вывод), вывод, ошибки

        for расш in кандидаты[:МАКС_ПОПЫТОК]:
            сем = СЕМЕЙСТВО.get(расш, "")
            if расш == ".pdf":
                отказы.append("PDF: читается своей веткой, не через LibreOffice")
                continue
            цель = цель_для(цели, сем)
            if цель is None:
                отказы.append(НЕТ_ЦЕЛИ.get(сем, f"{сем}: нет цели"))
                continue
            вход = каталог / f"in{расш}"
            вход.write_bytes(b)
            try:
                код, файлы, вывод, ошибки = один(вход, цель)
                if код is None:
                    return [], сем, f"LibreOffice: таймаут {предел:g} с"
                факт = приложение(вывод) or сем
                другая = цель_для(цели, факт)
                # Второй вызов — только если файл ОТКРЫЛСЯ, но не тем приложением:
                # неоткрывшийся файл другим фильтром вывода не откроется.
                открылся = "could not be loaded" not in вывод + ошибки
                if not файлы and открылся and факт != сем and другая and другая != цель:
                    код, файлы, вывод, ошибки = один(вход, другая)
                    if код is None:
                        return [], факт, f"LibreOffice: таймаут {предел:g} с"
            finally:
                вход.unlink(missing_ok=True)
            if файлы:
                return файлы, факт, ""
            if факт and цель_для(цели, факт) is None:
                return [], факт, НЕТ_ЦЕЛИ.get(факт, f"{факт}: нет цели")
            отказы.append(ошибки if код == -1 else _причина(вывод, ошибки, код, расш))
    if отказы and all(о.startswith("LibreOffice не открыл файл как ") for о in отказы) and len(отказы) > 1:
        return [], "", ("LibreOffice не открыл файл ни как "
                        + ", ни как ".join(о.rsplit(" ", 1)[1] for о in отказы))
    return [], "", "; ".join(dict.fromkeys(отказы)) or "LibreOffice не дал вывода"


# ─────────────────────────────── разбор того, что выгрузил LibreOffice

def строки_csv(b: bytes) -> list[list[str]]:
    """Лист, выгруженный в CSV: строки без пустых, ячейки без краевых пробелов."""
    out: list[list[str]] = []
    for строка in csv.reader(io.StringIO(b.decode("utf-8-sig", "replace"))):
        ячейки = [я.strip() for я in строка]
        if any(ячейки):
            out.append(ячейки)
    return out


class _Таблицы(HTMLParser):
    """Строки всех таблиц HTML. Вложенная таблица даёт свои строки, объединённая
    по горизонтали ячейка — пустые справа, чтобы столбцы не съезжали."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.строки: list[list[str]] = []
        self._стек: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table":
            self._стек.append({"строка": None, "ячейка": None, "шир": 1})
            return
        if not self._стек:
            return
        т = self._стек[-1]
        if tag == "tr":
            self._закрыть_строку(т)
            т["строка"] = []
        elif tag in ("td", "th"):
            self._закрыть_ячейку(т)
            if т["строка"] is None:
                т["строка"] = []
            т["ячейка"] = []
            try:
                т["шир"] = max(1, min(int(dict(attrs).get("colspan") or 1), 256))
            except ValueError:
                т["шир"] = 1
        elif tag in ("br", "p", "div", "li") and т["ячейка"] is not None:
            т["ячейка"].append(" ")

    def handle_endtag(self, tag: str) -> None:
        if not self._стек:
            return
        т = self._стек[-1]
        if tag in ("td", "th"):
            self._закрыть_ячейку(т)
        elif tag == "tr":
            self._закрыть_строку(т)
        elif tag == "table":
            self._закрыть_строку(т)
            self._стек.pop()

    def handle_data(self, data: str) -> None:
        if self._стек and self._стек[-1]["ячейка"] is not None:
            self._стек[-1]["ячейка"].append(data)

    def _закрыть_ячейку(self, т: dict) -> None:
        if т["ячейка"] is None:
            return
        т["строка"].append(" ".join("".join(т["ячейка"]).split()))
        т["строка"].extend([""] * (т["шир"] - 1))
        т["ячейка"], т["шир"] = None, 1

    def _закрыть_строку(self, т: dict) -> None:
        self._закрыть_ячейку(т)
        if т["строка"] and any(т["строка"]) and len(self.строки) < МАКС_СТРОК:
            self.строки.append(т["строка"])
        т["строка"] = None


def строки_html(b: bytes) -> list[list[str]]:
    """Строки таблиц из HTML, который выгрузил Writer."""
    м = re.search(rb"charset=[\"']?([A-Za-z0-9_-]+)", b[:4096])
    кодировка = м.group(1).decode() if м else "utf-8"
    try:
        текст = b.decode(кодировка, "replace")
    except LookupError:
        текст = b.decode("utf-8", "replace")
    разбор = _Таблицы()
    разбор.feed(текст)
    разбор.close()
    return разбор.строки


_ODF_ТЕКСТ = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODF_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_АБЗАЦ = {f"{{{_ODF_ТЕКСТ}}}p", f"{{{_ODF_ТЕКСТ}}}h"}


def текст_odf(b: bytes) -> str:
    """Текст плоского ODF (fodp, fodg) — только из office:body.

    Мастер-страницы (office:master-styles) не берутся: в них заполнители
    «<number>», «<date/time>», которые повторились бы на каждом слайде.
    """
    try:
        корень = ET.fromstring(b)
    except ET.ParseError:
        return ""
    тело = корень.find(f"{{{_ODF_OFFICE}}}body")
    if тело is None:
        return ""

    def абзац(эл: ET.Element) -> str:
        части = [эл.text or ""]
        for реб in эл:
            тег = реб.tag.rsplit("}", 1)[-1]
            if тег == "s":
                try:
                    части.append(" " * max(1, min(int(реб.get(f"{{{_ODF_ТЕКСТ}}}c") or 1), 100)))
                except ValueError:
                    части.append(" ")
            elif тег == "tab":
                части.append("\t")
            elif тег == "line-break":
                части.append("\n")
            elif реб.tag not in _АБЗАЦ:             # вложенный абзац обойдёт общий цикл
                части.append(абзац(реб))
            части.append(реб.tail or "")
        return "".join(части)

    строки = [абзац(эл).strip() for эл in тело.iter() if эл.tag in _АБЗАЦ]
    return "\n".join(с for с in строки if с)


_СВОИ_ЗНАКИ = re.compile(r"[0-9A-Za-zА-Яа-яЁё]")


def похоже_на_мусор(текст: str) -> bool:
    """Двоичный файл, который LibreOffice открыл как текст: «####…» или
    иероглифы из байтов, прочитанных как UTF-16 (замер 23.09.2026). Меряется
    доля кириллицы, латиницы и цифр: у документа их большинство."""
    значимые = [з for з in текст[:20000] if not з.isspace()]
    if not значимые:
        return False
    return sum(1 for з in значимые if _СВОИ_ЗНАКИ.match(з)) / len(значимые) < 0.5


МУСОР = "LibreOffice открыл неопознанный файл как текст: вместо текста мусор"


def _листы(файлы: list[tuple[str, bytes]]) -> list[list[list[str]]]:
    листы, всего = [], 0
    for _, данные in файлы:
        строки = строки_csv(данные)[: МАКС_СТРОК - всего]
        всего += len(строки)
        листы.append(строки)
    return листы


# ─────────────────────────────── вход для читателей

def в_pdf(b: bytes, подсказка: str = "") -> tuple[bytes | None, str]:
    """Любой офисный файл → PDF. Возвращает (байты PDF, причина неудачи).

    PDF отдаётся как есть: гонять его через Draw незачем.
    """
    if b.startswith(b"%PDF"):
        return b, ""
    файлы, _, отказ = конвертировать(b, подсказка, ЦЕЛЬ_PDF)
    return (файлы[0][1], "") if файлы else (None, отказ)


def в_текст(b: bytes, подсказка: str = "") -> tuple[str, str]:
    """Любой офисный файл → текст. Книга — все листы (ячейки через табуляцию,
    листы через пустую строку), презентация — все слайды."""
    файлы, сем, отказ = конвертировать(b, подсказка, ЦЕЛЬ_ТЕКСТ)
    if not файлы:
        return "", отказ
    if сем == CALC or файлы[0][0].endswith(".csv"):
        блоки = ["\n".join("\t".join(с).rstrip("\t") for с in лист) for лист in _листы(файлы)]
        текст = "\n\n".join(б for б in блоки if б)
        пусто = "LibreOffice: листы пусты"
    elif файлы[0][0].endswith((".fodp", ".fodg")):
        текст, пусто = текст_odf(файлы[0][1]), "LibreOffice: в файле нет текста"
    else:
        текст = файлы[0][1].decode("utf-8-sig", "replace").strip()
        пусто = "LibreOffice: в файле нет текста"
    if not текст:
        return "", пусто
    # Проверка — только для неопознанного входа: китайский каталог в .docx
    # опознан по содержимому и браковаться не должен.
    if not опознать(b)[0] and похоже_на_мусор(текст):
        return "", МУСОР
    return текст, ""


def в_csv(b: bytes, подсказка: str = "") -> tuple[list[list[str]], str]:
    """Таблицы файла → строки. Книга — ВСЕ листы подряд в их порядке (как
    rows_from_xlsx), документ Word — строки всех его таблиц."""
    файлы, сем, отказ = конвертировать(b, подсказка, ЦЕЛЬ_СТРОКИ)
    if not файлы:
        return [], отказ
    if файлы[0][0].endswith(".html"):
        строки = строки_html(файлы[0][1])
        return (строки, "") if строки else ([], "LibreOffice: в документе нет таблиц")
    строки = [с for лист in _листы(файлы) for с in лист]
    if not строки:
        return [], "LibreOffice: листы пусты"
    if not опознать(b)[0] and похоже_на_мусор(" ".join(" ".join(с) for с in строки[:500])):
        return [], МУСОР
    return строки, ""
