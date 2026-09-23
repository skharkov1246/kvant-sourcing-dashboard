#!/usr/bin/env python3
"""Читатели форматов, у которых своей ветки разбора не было.

ЗАЧЕМ. Замер 23.09.2026 по всей базе (31 869 файлов): 9 527 файлов, то есть
29,9 %, не дали НИ ОДНОЙ позиции. Разрез по виду назвал два разряда, потерянных
целиком, потому что ветки чтения у них нет вовсе:

    прочее    410 файлов · 410 без позиций · 100 %
    архив     169 файлов · 169 без позиций · 100 %

и ещё один, где читатель применялся не тот:

    старый office  1 754 файла · 423 без позиций · из них 375 — падение xlrd
                   (XLRDError 362, AssertionError 11, CompDocError 2)

Падение xlrd означает, что в читатель книги .xls попал не .xls: .doc и .msg —
такой же контейнер OLE2, а выгрузка 1С носит расширение .xls, но внутри неё XML.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Ни одной новой зависимости: csv, xml, zip и регулярные
выражения — из стандартной поставки. Старый .doc читается внешними antiword или
catdoc, и если их в системе нет, читатель возвращает пусто с НАЗВАННОЙ причиной,
а не молча: невидимая потеря хуже видимой.

ПРАВИЛО 17 (журнал публичный) соблюдается тем, что этот модуль ничего не печатает
вовсе — он только возвращает строки вызывающему.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

#: Сколько байт файла нюхать, чтобы определить кодировку и разделитель.
ГОЛОВА = 65536

#: Предел строк на файл. Тот же порядок, что у прочих читателей indexer.py:
#: книга обрывается на 20 000 строк, документ Word на 4 000.
МАКС_СТРОК = 20000

#: Участники архива, которые имеет смысл разбирать. Вложенные архивы НЕ берутся:
#: архив в архиве это либо ошибка отправителя, либо бомба, и один уровень вглубь
#: закрывает настоящие случаи — «предложение и приложения одним файлом».
УЧАСТНИКИ = (".xlsx", ".xlsm", ".xls", ".docx", ".doc", ".pdf",
             ".csv", ".txt", ".tsv", ".rtf", ".xml")

#: Сколько участников архива читать. Архив с сотней файлов — это каталог
#: производителя, а не предложение; читать его целиком дорого и незачем.
МАКС_УЧАСТНИКОВ = 40


def декод(b: bytes) -> str:
    """Байты в текст. Пробуются utf-8 и cp1251 — две кодировки, в которых
    приходит всё; последняя попытка с заменой, чтобы читатель не падал вовсе."""
    for кодировка in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return b.decode(кодировка)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def разделитель(текст: str) -> str:
    """Чем разделены колонки. Считаем по первым строкам, а не гадаем.

    csv.Sniffer на русских файлах ошибается: он видит запятую в «1,5 мм» и
    объявляет её разделителем. Поэтому берём тот знак, который встречается в
    строках ОДИНАКОВОЕ число раз и чаще прочих: у настоящей таблицы разделителей
    поровну в каждой строке, у прозы — как придётся.
    """
    строки = [s for s in текст.splitlines()[:20] if s.strip()][:10]
    if len(строки) < 2:
        return ";"
    лучший, лучший_счёт = ";", 0
    for р in (";", "\t", "|", ","):
        счёт = [s.count(р) for s in строки]
        if счёт[0] and len(set(счёт)) == 1 and счёт[0] > лучший_счёт:
            лучший, лучший_счёт = р, счёт[0]
    return лучший


def rows_from_text(b: bytes) -> list[list[str]]:
    """CSV, TSV и простой текст с колонками.

    Разбор идёт стандартным csv, а не split: в ячейке бывает разделитель внутри
    кавычек («Насос ЦНС 38-176; с рамой»), и split разорвал бы позицию надвое.
    """
    текст = декод(b[:ГОЛОВА * 16])
    if not текст.strip():
        return []
    р = разделитель(текст)
    out: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(текст), delimiter=р):
            if any(c.strip() for c in row):
                out.append([c.strip() for c in row])
            if len(out) >= МАКС_СТРОК:
                break
    except csv.Error:
        # Файл не разбирается как csv (битые кавычки) — берём строками целиком:
        # одна колонка это хуже, чем пять, но лучше, чем ноль.
        return [[s.strip()] for s in текст.splitlines()[:МАКС_СТРОК] if s.strip()]
    return out


def вырезать_группы(текст: str, имена: tuple[str, ...]) -> str:
    """Вырезать группы RTF вида {\имя ...} и {\*\имя ...} с любой вложенностью.

    Регулярка вложенность не считает: группа шрифтов содержит по группе на
    шрифт, а группа стилей — ещё глубже. Поэтому скобки считаются вручную.
    Экранированные \{ и \} скобками не считаются.
    """
    out: list[str] = []
    i, n = 0, len(текст)
    while i < n:
        if текст[i] == "{":
            j = i + 1
            if текст.startswith("\\*", j):
                j += 2
            if текст.startswith("\\", j):
                m = re.match(r"[a-zA-Z]+", текст[j + 1:])
                if m and m.group(0) in имена:
                    уровень, k = 0, i
                    while k < n:
                        ch = текст[k]
                        if ch == "\\":
                            k += 2
                            continue
                        if ch == "{":
                            уровень += 1
                        elif ch == "}":
                            уровень -= 1
                            if уровень == 0:
                                break
                        k += 1
                    out.append(" ")
                    i = k + 1
                    continue
        out.append(текст[i])
        i += 1
    return "".join(out)


def text_from_rtf(b: bytes) -> str:
    """RTF в простой текст: разворот кодов, снятие управляющих слов.

    Своего разбора RTF тут ровно столько, сколько нужно для номенклатуры:
    \\'hh — знак в текущей кодовой странице, \\uNNNN — Юникод, \\par — перевод
    строки. Группы со шрифтами и цветами выбрасываются целиком.
    """
    текст = b.decode("cp1251", errors="replace")
    # СЛУЖЕБНЫЕ ГРУППЫ ВЫРЕЗАЮТСЯ ЦЕЛИКОМ, с любой вложенностью. Прежняя регулярка
    # требовала ДВА обратных слэша перед именем группы — `{\\fonttbl` — и на
    # настоящем `{\fonttbl` не срабатывала: в текст утекали «Times New Roman;
    # Symbol; Arial; Normal;». Нашёл агент Word 23.09.2026 на RTF из LibreOffice.
    текст = вырезать_группы(текст, ("fonttbl", "colortbl", "stylesheet", "info",
                                    "pict", "listtable", "listoverridetable",
                                    "rsidtbl", "generator", "themedata",
                                    "colorschememapping", "latentstyles",
                                    "datastore", "xmlnstbl", "mmathPr"))
    текст = re.sub(r"\\u(-?\d+)\s?\??", lambda m: chr(int(m.group(1)) % 65536), текст)
    текст = re.sub(r"\\'([0-9a-fA-F]{2})",
                   lambda m: bytes([int(m.group(1), 16)]).decode("cp1251", "replace"), текст)
    текст = re.sub(r"\\(?:par|line|row|cell)\b", "\n", текст)
    текст = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", текст)
    текст = текст.replace("{", " ").replace("}", " ")
    строки = [re.sub(r"[ \t]+", " ", s).strip() for s in текст.splitlines()]
    return "\n".join(s for s in строки if s)


def text_from_html(b: bytes) -> str:
    """HTML в текст. Скрипты и стили снимаются вместе с содержимым."""
    текст = декод(b)
    текст = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", текст)
    текст = re.sub(r"(?i)</(tr|p|div|br|li|h[1-6])\s*>", "\n", текст)
    текст = re.sub(r"(?i)</t[dh]\s*>", "\t", текст)
    текст = re.sub(r"(?s)<[^>]+>", " ", текст)
    текст = (текст.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    строки = [re.sub(r"[ \t]+", " ", s).strip() for s in текст.splitlines()]
    return "\n".join(s for s in строки if s)


def rows_from_spreadsheetml(b: bytes) -> list[list[str]]:
    """XML Spreadsheet 2003 — выгрузка 1С с расширением .xls.

    ЭТО НЕ КНИГА OLE2, А ТЕКСТ. xlrd на таком файле падает с XLRDError, и до
    23.09.2026 такие выгрузки получали «формат не читаем»: 362 файла.

    Пустые ячейки восстанавливаются по ss:Index — без этого колонки съезжают
    влево, и цена оказывается под заголовком количества.
    """
    try:
        корень = ET.fromstring(b.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return []
    out: list[list[str]] = []
    for строка in корень.iter():
        if not строка.tag.endswith("}Row") and строка.tag != "Row":
            continue
        ячейки: list[str] = []
        for клетка in строка:
            if not (клетка.tag.endswith("}Cell") or клетка.tag == "Cell"):
                continue
            место = next((v for k, v in клетка.attrib.items() if k.endswith("Index")), None)
            if место:
                try:
                    while len(ячейки) < int(место) - 1:
                        ячейки.append("")
                except ValueError:
                    pass
            значение = ""
            for данные in клетка:
                if данные.tag.endswith("}Data") or данные.tag == "Data":
                    значение = "".join(данные.itertext()).strip()
                    break
            ячейки.append(значение)
        if any(c for c in ячейки):
            out.append(ячейки)
        if len(out) >= МАКС_СТРОК:
            break
    return out


#: Внешние читатели старого .doc, по порядку предпочтения. antiword держит
#: разметку абзацев, catdoc проще и есть чаще.
ДОК_ЧИТАТЕЛИ = (("antiword", ("-w", "0")), ("catdoc", ("-w",)))


def text_from_doc(b: bytes) -> tuple[str, str]:
    """Старый .doc (Word 97-2003). Возвращает (текст, причина неудачи).

    ПРИЧИНА ВОЗВРАЩАЕТСЯ, А НЕ ГЛОТАЕТСЯ. Читатель внешний, и его может не быть в
    системе: тогда файл теряется, и потеря обязана быть названа. Молчаливое
    «пусто» на 375 файлах — ровно то, из-за чего .doc не читались полгода.
    """
    for имя, ключи in ДОК_ЧИТАТЕЛИ:
        if not shutil.which(имя):
            continue
        with tempfile.TemporaryDirectory() as каталог:
            путь = Path(каталог) / "in.doc"
            путь.write_bytes(b)
            try:
                готово = subprocess.run([имя, *ключи, str(путь)], capture_output=True,
                                        timeout=60, check=False)
            except subprocess.TimeoutExpired:
                return "", f"{имя}: таймаут 60 с"
            except Exception as e:                      # noqa: BLE001
                return "", f"{имя}: сбой запуска ({type(e).__name__})"
            if готово.returncode == 0 and готово.stdout.strip():
                return готово.stdout.decode("utf-8", errors="replace"), ""
    есть = [и for и, _ in ДОК_ЧИТАТЕЛИ if shutil.which(и)]
    if not есть:
        return "", "нет ни antiword, ни catdoc"
    return "", f"{'/'.join(есть)}: текста не дали"


def участники_архива(b: bytes) -> list[tuple[str, bytes]]:
    """Файлы внутри ZIP, на один уровень вглубь.

    Настоящий архив до 23.09.2026 опознавался книгой Excel (и у книги, и у архива
    первые байты PK), падал в openpyxl и ложился как «пусто»: 169 файлов, все до
    единого без позиций.

    Возвращаются пары (имя, байты). Имя нужно вызывающему только чтобы отличить
    участников друг от друга — в журнал оно не идёт (правило 17).
    """
    out: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as архив:
            for сведения in архив.infolist():
                if сведения.is_dir() or len(out) >= МАКС_УЧАСТНИКОВ:
                    continue
                имя = сведения.filename
                if not имя.lower().endswith(УЧАСТНИКИ):
                    continue
                # Бомба распаковки: участник, раздувающийся во много раз, не
                # читается. Предел щедрый — настоящая спецификация в него влезает.
                if сведения.file_size > 200 * 1024 * 1024:
                    continue
                try:
                    out.append((имя, архив.read(сведения)))
                except Exception:                       # noqa: BLE001, S112
                    continue                            # битый участник — не вся почта
    except (zipfile.BadZipFile, OSError):
        return []
    return out
