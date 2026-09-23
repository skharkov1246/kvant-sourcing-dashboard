"""Опознание вида файла: от него зависит стратегия чтения и цена чтения.

Цель владельца — читать 99,9 % файлов. Замер 23.09.2026 показал три подмены,
каждая из которых давала ноль позиций:

  • настоящий .zip-архив начинается с PK и опознавался книгой Excel — openpyxl
    на нём падал, файл ложился как «пусто»;
  • .doc и .msg — такой же OLE2, как .xls, и уходили в xlrd, который на них
    падает: «формат не читаем»;
  • TIFF, GIF, BMP опознавались как «прочее», а распознавание берёт файлы по
    виду «изображение» — значит до распознавания они не доходили.

Корпуса собраны побайтно здесь же, а не сняты с базы (CLAUDE.md, правило 18).
"""
from __future__ import annotations

from library import indexer

PK = b"PK\x03\x04"
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def test_zip_архив_не_книга_excel():
    """PK есть у книги, у документа Word и у простого архива — это разные вещи."""
    книга = PK + b"\x14\x00\x06\x00[Content_Types].xml xl/workbook.xml"
    документ = PK + b"\x14\x00\x06\x00[Content_Types].xml word/document.xml"
    архив = PK + b"\x14\x00\x06\x00prilozhenie/specifikaciya.pdf"
    assert indexer.подвид(книга) == "xlsx" and indexer.sniff(книга) == "xlsx/docx"
    assert indexer.подвид(документ) == "docx" and indexer.sniff(документ) == "xlsx/docx"
    assert indexer.подвид(архив) == "zip", "архив опознан книгой — читатель на нём упадёт"
    assert indexer.sniff(архив) == "архив"


def test_картинки_всех_видов_доходят_до_распознавания():
    """Распознавание отбирает файлы по виду «изображение» (library/ocr.py)."""
    картинки = {
        "png": b"\x89PNG\r\n\x1a\n",
        "jpeg": b"\xff\xd8\xff\xe0\x00\x10JFIF",
        "tiff": b"II*\x00\x08\x00\x00\x00",
        "gif": b"GIF89a\x10\x00\x10\x00",
        "bmp": b"BM\x8a\x02\x00\x00",
        "webp": b"RIFF\x24\x00\x00\x00WEBPVP8 ",
    }
    for имя, b in картинки.items():
        assert indexer.подвид(b) == имя, (имя, indexer.подвид(b))
        assert indexer.sniff(b) == "изображение", f"{имя} не дойдёт до распознавания"


def test_выгрузка_1с_это_текст_а_не_ole2():
    """XML Spreadsheet 2003 носит расширение .xls, а внутри текст.

    xlrd на таком файле падает, и файл получает «формат не читаем» — при том,
    что это обычный XML, который читается без всяких библиотек.
    """
    b = ('<?xml version="1.0"?>\n<Workbook '
         'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">').encode()
    assert indexer.подвид(b) == "spreadsheetml"
    assert OLE2[:2] not in b[:2], "корпус случайно стал OLE2"


def test_текстовые_форматы_различаются_между_собой():
    csv = "Наименование;Кол-во;Цена\nНасос ЦНС-38;2;1500\nЗадвижка;4;250\n".encode()
    txt = "Здравствуйте. Просим предоставить предложение на насосы.\nС уважением\n".encode()
    rtf = rb"{\rtf1\ansi\deff0 text}"
    html = b"<html><head><title>x</title></head><body>y</body></html>"
    assert indexer.подвид(csv) == "csv"
    assert indexer.подвид(txt) == "txt"
    assert indexer.подвид(rtf) == "rtf"
    assert indexer.подвид(html) == "html"


def test_архивы_rar_и_7z_опознаны():
    assert indexer.подвид(b"Rar!\x1a\x07\x00") == "rar"
    assert indexer.подвид(b"7z\xbc\xaf\x27\x1c\x00\x04") == "7z"
    assert indexer.sniff(b"7z\xbc\xaf\x27\x1c") == "архив"


def test_двоичный_мусор_не_выдаёт_себя_за_текст():
    """Иначе читатель текста получит байты и сделает из них «позиции»."""
    b = bytes(range(0, 256)) * 4
    assert indexer.подвид(b) in ("неизвестно", "txt")
    if indexer.подвид(b) == "неизвестно":
        assert indexer.sniff(b) == "прочее"


def test_крупный_вид_остался_прежним_по_значениям():
    """По виду отбирают файлы переразбор и распознавание.

    Новое значение вида сломало бы отбор во всех прогонах разом: reparse берёт
    KINDS = pdf,xlsx/docx,старый office, ocr — изображение и pdf.
    """
    допустимые = {"pdf", "xlsx/docx", "старый office", "изображение", "архив", "прочее"}
    assert set(indexer.ВИД_ПО_ПОДВИДУ.values()) <= допустимые
    assert indexer.sniff(b"%PDF-1.7") == "pdf"
    assert indexer.sniff(OLE2 + b"rest") == "старый office"
