"""Читатель писем: .eml, .msg (Outlook) и winmail.dat внутри них.

До library/read_mail.py письмо, приложенное к карточке целиком, не читалось:
.msg падал в читатель книги .xls (тот же контейнер OLE2), .eml уходил в «прочее»
и читался прозой вместе с base64 вложений. Каждое такое письмо — КП в PDF и
книга Excel, которых база не увидела.

Корпуса придуманы здесь же (CLAUDE.md, правило 18): письма .eml собраны пакетом
email, контейнер .msg — собственным писателем составного файла OLE ниже, адреса —
в зарезервированном домене example.test. Живые письма проверяются только по
переменной окружения READ_MAIL_LIVE_DIR и в репозиторий не попадают.
"""
from __future__ import annotations

import base64
import os
import struct
from email.header import Header
from email.message import Message
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from library import read_mail as rm

PDF = b"%PDF-1.4\n" + "КП на насос ЦНС 38-176, цена 150 000 руб.".encode() + b"\n%%EOF"
XLSX = b"PK\x03\x04" + b"[Content_Types].xml xl/workbook.xml" + bytes(range(256)) * 30


# ───────────────────────────── сборка писем .eml ────────────────────────────

def _письмо(тема: str = "Запрос цены", тело: str = "Текст письма",
            кодировка: str = "utf-8", вложения=(), вложенные=()) -> Message:
    """Письмо с адресами участников: читатель обязан их не выдать."""
    if not вложения and not вложенные:
        m: Message = MIMEText(тело, "plain", кодировка)
    else:
        m = MIMEMultipart("mixed")
        m.attach(MIMEText(тело, "plain", кодировка))
        for имя, данные in вложения:
            часть = MIMEApplication(данные, "octet-stream")
            часть.add_header("Content-Disposition", "attachment", filename=("utf-8", "", имя))
            m.attach(часть)
        for вложенное in вложенные:
            m.attach(MIMEMessage(вложенное))
    m["Subject"] = Header(тема, "utf-8")
    m["From"] = "Пётр Поставщиков <sales@supplier.example.test>"
    m["To"] = "buyer@kvant.example.test"
    m["Cc"] = "chief@kvant.example.test"
    m["Date"] = "Tue, 01 Sep 2026 10:15:00 +0300"
    return m


# ─────────────────────── сборка составного файла OLE ────────────────────────

_КОНЕЦ, _СВОБОДЕН, _СЕКТОР_FAT, _СЕКТОР_DIFAT, _НЕТ = (0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD,
                                                      0xFFFFFFFC, 0xFFFFFFFF)


def _запись_каталога(з: dict, мусор: bool = False) -> bytes:
    имя = з["имя"].encode("utf-16-le") + b"\x00\x00"
    размер = з["размер"]
    if мусор and з["тип"] == 2:
        # Версия 3: старшие 4 байта размера не определены, старые программы
        # оставляют там мусор. Читатель обязан их отбросить.
        размер |= 0xDEADBEEF << 32
    return (имя.ljust(64, b"\x00") + struct.pack("<HBB", len(имя), з["тип"], 1)
            + struct.pack("<III", з["лев"], з["прав"], з["ребёнок"])
            + b"\x00" * 36 + struct.pack("<IQ", з["начало"], размер))


def _пустая_запись() -> bytes:
    return b"\x00" * 68 + struct.pack("<III", _НЕТ, _НЕТ, _НЕТ) + b"\x00" * 48


def _cfb(дерево: dict, сдвиг: int = 9, мусор_в_размере: bool = False) -> bytes:
    """Составной файл OLE (MS-CFB) из дерева {имя: байты | {…}}.

    Соседи в каталоге — сбалансированное дерево, а не цепочка через правого
    соседа: так читатель обязан обходить и левые ветви. Потоки короче 4096 байт
    идут в мини-поток, длиннее — в обычные секторы: проверяются оба пути. Если
    секторов таблицы размещения больше 109, остальные перечисляются цепочкой
    DIFAT — так устроен любой .msg тяжелее семи мегабайт.
    """
    S = 1 << сдвиг
    записи = [{"имя": "Root Entry", "тип": 5, "лев": _НЕТ, "прав": _НЕТ,
               "ребёнок": _НЕТ, "данные": b""}]

    def соседи(номера: list[int]) -> int:
        if not номера:
            return _НЕТ
        середина = len(номера) // 2
        корень = номера[середина]
        записи[корень]["лев"] = соседи(номера[:середина])
        записи[корень]["прав"] = соседи(номера[середина + 1:])
        return корень

    def строить(i: int, узел: dict) -> None:
        дети = []
        for имя, под in узел.items():
            хранилище = isinstance(под, dict)
            записи.append({"имя": имя, "тип": 1 if хранилище else 2, "лев": _НЕТ,
                           "прав": _НЕТ, "ребёнок": _НЕТ,
                           "данные": b"" if хранилище else под})
            дети.append(len(записи) - 1)
            if хранилище:
                строить(len(записи) - 1, под)
        дети.sort(key=lambda j: (len(записи[j]["имя"]), записи[j]["имя"].upper()))
        записи[i]["ребёнок"] = соседи(дети)

    строить(0, дерево)
    мини, minifat = bytearray(), []
    for з in записи:
        з["начало"], з["размер"] = (0 if з["тип"] == 1 else _КОНЕЦ), len(з["данные"])
        if з["тип"] == 2 and 0 < len(з["данные"]) < 4096:
            n = (len(з["данные"]) + 63) // 64
            з["начало"] = len(minifat)
            minifat.extend(range(len(minifat) + 1, len(minifat) + n))
            minifat.append(_КОНЕЦ)
            мини += з["данные"].ljust(n * 64, b"\x00")
    секторы: list[bytes] = []
    fat: list[int] = []

    def разместить(данные: bytes) -> int:
        if not данные:
            return _КОНЕЦ
        n = (len(данные) + S - 1) // S
        первый = len(секторы)
        for k in range(n):
            секторы.append(данные[k * S:(k + 1) * S].ljust(S, b"\x00"))
            fat.append(первый + k + 1 if k < n - 1 else _КОНЕЦ)
        return первый

    for з in записи:
        if з["тип"] == 2 and len(з["данные"]) >= 4096:
            з["начало"] = разместить(з["данные"])
    записи[0]["начало"], записи[0]["размер"] = разместить(bytes(мини)), len(мини)
    minifat_байты = struct.pack(f"<{len(minifat)}I", *minifat)
    первый_minifat = разместить(minifat_байты)
    число_minifat = (len(minifat_байты) + S - 1) // S
    каталог = b"".join(_запись_каталога(з, мусор_в_размере) for з in записи)
    каталог += _пустая_запись() * (-len(записи) % (S // 128))
    первый_каталог = разместить(каталог)
    на_сектор = S // 4
    число_fat = число_difat = 1

    def difat_нужно(n_fat: int) -> int:
        return max(0, -(-(n_fat - 109) // (на_сектор - 1)))

    while True:
        число_difat = difat_нужно(число_fat)
        if len(секторы) + число_fat + число_difat <= число_fat * на_сектор:
            break
        число_fat += 1
    первый_fat = len(секторы)
    первый_difat = первый_fat + число_fat if число_difat else _КОНЕЦ
    fat.extend([_СЕКТОР_FAT] * число_fat)
    fat.extend([_СЕКТОР_DIFAT] * число_difat)
    fat.extend([_СВОБОДЕН] * (число_fat * на_сектор - len(fat)))
    fat_байты = struct.pack(f"<{len(fat)}I", *fat)
    секторы.extend(fat_байты[k * S:(k + 1) * S] for k in range(число_fat))
    номера_fat = list(range(первый_fat, первый_fat + число_fat))
    хвост = номера_fat[109:]
    for k in range(число_difat):
        кусок = хвост[k * (на_сектор - 1):(k + 1) * (на_сектор - 1)]
        кусок += [_СВОБОДЕН] * (на_сектор - 1 - len(кусок))
        следующий = первый_difat + k + 1 if k < число_difat - 1 else _КОНЕЦ
        секторы.append(struct.pack(f"<{на_сектор}I", *кусок, следующий))
    difat = номера_fat[:109] + [_СВОБОДЕН] * (109 - len(номера_fat[:109]))
    шапка = (rm.OLE_ПОДПИСЬ + b"\x00" * 16
             + struct.pack("<HHHHH", 0x3E, 3 if сдвиг == 9 else 4, 0xFFFE, сдвиг, 6)
             + b"\x00" * 6
             + struct.pack("<9I", len(каталог) // S if сдвиг == 12 else 0, число_fat,
                           первый_каталог, 0, 4096, первый_minifat, число_minifat,
                           первый_difat, число_difat)
             + struct.pack("<109I", *difat))
    return шапка.ljust(S, b"\x00") + b"".join(секторы)


# ───────────────────────────── сборка писем .msg ────────────────────────────

def _поток_свойств(шапка: int, свойства=()) -> bytes:
    """Поток `__properties_version1.0`: шапка и по 16 байт на свойство."""
    out = b"\x00" * шапка
    for ид, тип, значение in свойства:
        out += struct.pack("<II", (ид << 16) | тип, 6) + значение.ljust(8, b"\x00")
    return out


def _строка(ид: int, текст: str) -> tuple[str, bytes]:
    return f"__substg1.0_{ид:04X}001F", текст.encode("utf-16-le")


def _filetime(год: int, месяц: int, день: int, час: int, минута: int) -> bytes:
    import datetime as dt
    мкс = (dt.datetime(год, месяц, день, час, минута) - dt.datetime(1601, 1, 1)) // dt.timedelta(
        microseconds=1)
    return struct.pack("<q", мкс * 10)


def _msg_узел(тема: str = "Запрос цены", тело: str | None = "Цена насоса 150 000 руб.",
              вложения=(), шапка: int = 32, фикс=(), доп: dict | None = None) -> dict:
    """Хранилище письма Outlook: тема, тело, отправитель, получатель, вложения.

    Вложение — (имя, байты), («письмо», узел) для письма во вложении (способ 5)
    или («ссылка», имя) для вложения ссылкой без байтов (способ 2).
    """
    узел: dict = dict([
        _строка(0x001A, "IPM.Note"),
        _строка(0x0037, тема),
        _строка(0x0C1A, "Пётр Поставщиков"),
        _строка(0x0C1F, "sales@supplier.example.test"),
        _строка(0x0E04, "Закупщик"),
    ])
    if тело is not None:
        узел.update([_строка(0x1000, тело)])
    узел["__properties_version1.0"] = _поток_свойств(шапка, [(0x3FFD, 0x0003,
                                                              struct.pack("<i", 1251)), *фикс])
    узел["__recip_version1.0_#00000000"] = dict([
        ("__properties_version1.0", _поток_свойств(8, [(0x0C15, 0x0003, struct.pack("<i", 1))])),
        _строка(0x3001, "Закупщик"), _строка(0x3003, "buyer@kvant.example.test")])
    for н, (вид, данные) in enumerate(вложения):
        if вид == "письмо":
            хранилище = {"__properties_version1.0": _поток_свойств(8, [(0x3705, 0x0003,
                                                                   struct.pack("<i", 5))]),
                         "__substg1.0_3701000D": данные}
        elif вид == "ссылка":
            хранилище = dict([("__properties_version1.0", _поток_свойств(8, [
                (0x3705, 0x0003, struct.pack("<i", 2))])), _строка(0x3707, данные)])
        else:
            хранилище = dict([("__properties_version1.0", _поток_свойств(8, [
                (0x3705, 0x0003, struct.pack("<i", 1))])), _строка(0x3707, вид),
                ("__substg1.0_37010102", данные)])
        узел[f"__attach_version1.0_#{н:08X}"] = хранилище
    узел["__nameid_version1.0"] = {}
    if доп:
        узел.update(доп)
    return узел


def _msg(*args, сдвиг: int = 9, **kwargs) -> bytes:
    return _cfb(_msg_узел(*args, **kwargs), сдвиг)


# ───────────────────────────── сборка winmail.dat ───────────────────────────

def _tnef_атрибут(уровень: int, ид: int, данные: bytes) -> bytes:
    return (bytes([уровень]) + struct.pack("<II", ид, len(данные)) + данные
            + struct.pack("<H", sum(данные) & 0xFFFF))


def _mapi(свойства) -> bytes:
    """Свойства MAPI в TNEF. Перед нужными — именованное свойство со строковым
    именем: его длину читатель обязан пройти точно, иначе всё следующее съедет."""
    out = struct.pack("<I", len(свойства) + 1)
    имя = "Примечание".encode("utf-16-le") + b"\x00\x00"
    значение = "служебное".encode("utf-16-le") + b"\x00\x00"
    out += (struct.pack("<HH", 0x001F, 0x8005) + b"\x11" * 16 + struct.pack("<II", 1, len(имя))
            + имя + b"\x00" * (-len(имя) % 4)
            + struct.pack("<II", 1, len(значение)) + значение + b"\x00" * (-len(значение) % 4))
    for тип, ид, знач in свойства:
        out += struct.pack("<HH", тип, ид)
        if тип == 0x001F:
            сырое = знач.encode("utf-16-le") + b"\x00\x00"
        elif тип == 0x001E:
            сырое = знач.encode("cp1251") + b"\x00"
        else:
            сырое = знач
        if тип in (0x001E, 0x001F, 0x0102, 0x000D):
            out += struct.pack("<II", 1, len(сырое)) + сырое + b"\x00" * (-len(сырое) % 4)
        else:
            out += сырое
    return out


def _tnef(тема: str = "Коммерческое предложение", тело: str = "Тело из winmail",
          вложения=(("KP~1.PDF", "КП от завода.pdf", PDF),)) -> bytes:
    out = rm.TNEF_ПОДПИСЬ + struct.pack("<H", 0x0101)
    out += _tnef_атрибут(1, 0x00069007, struct.pack("<II", 1251, 0))
    out += _tnef_атрибут(1, 0x00018004, тема.encode("cp1251") + b"\x00")
    out += _tnef_атрибут(1, 0x00069003, _mapi([(0x0003, 0x0E07, struct.pack("<i", 1)),
                                               (0x001F, 0x1000, тело)]))
    for короткое, длинное, данные in вложения:
        out += _tnef_атрибут(2, 0x00069002, b"\x01\x00\x00\x00\xff\xff\xff\xff" + b"\x00" * 6)
        out += _tnef_атрибут(2, 0x00018010, короткое.encode("cp1251") + b"\x00")
        out += _tnef_атрибут(2, 0x0006800F, данные)
        out += _tnef_атрибут(2, 0x00069005, _mapi([(0x001F, 0x3707, длинное)]))
    return out


# ═══════════════════════════════════ .eml ═══════════════════════════════════

def test_тема_koi8_и_тело_cp1251_читаются_верно():
    """Две разные кодировки в одном письме — обычное дело для старых почтовых
    программ: тема KOI8-R словами RFC 2047, тело в windows-1251."""
    m = MIMEText("Цена насоса ЦНС 38-176 — 150 000 руб.", "plain", "windows-1251")
    m["Subject"] = Header("Коммерческое предложение", "koi8-r")
    m["From"] = "sales@supplier.example.test"
    raw = m.as_bytes()
    assert b"=?koi8-r?" in raw.lower() and b"charset=\"windows-1251\"" in raw
    итог = rm.прочитать_письмо(raw)
    assert "Тема: Коммерческое предложение" in итог["text"], итог["text"]
    assert "Цена насоса ЦНС 38-176 — 150 000 руб." in итог["text"], итог["text"]
    assert итог["reason"] == "" and итог["формат"] == "eml" and итог["писем"] == 1


def test_тема_windows1251_словом_q():
    m = _письмо()
    del m["Subject"]
    import quopri
    слово = quopri.encodestring("Цена задвижки".encode("cp1251"), header=True).decode()
    m["Subject"] = f"=?windows-1251?Q?{слово}?="
    итог = rm.прочитать_письмо(m.as_bytes())
    assert "Тема: Цена задвижки" in итог["text"], итог["text"]


def test_тема_сырыми_байтами_cp1251_без_кодирования():
    """Старые программы пишут тему байтами cp1251 без RFC 2047. Стандартный
    разбор заменяет их на «�» — такая тема теряется вместе с номенклатурой."""
    m = _письмо()
    del m["Subject"]
    m["Subject"] = "ZAGLUSHKA"
    raw = m.as_bytes().replace(b"ZAGLUSHKA", "Насос НБ-125 цена".encode("cp1251"))
    assert "Насос".encode("cp1251") in raw
    итог = rm.прочитать_письмо(raw)
    assert "Тема: Насос НБ-125 цена" in итог["text"], итог["text"]


def test_буква_utf8_разрезанная_между_словами_rfc2047_собирается():
    """Почтовые программы режут длинную тему посреди многобайтной буквы. Каждая
    половина по отдельности даёт «�»; байты соседних слов склеиваются до
    декодирования."""
    байты = "Цена".encode()
    тема = (f"=?utf-8?B?{base64.b64encode(байты[:3]).decode()}?=\r\n "
            f"=?utf-8?B?{base64.b64encode(байты[3:]).decode()}?= насоса")
    assert rm._заголовок(тема) == "Цена насоса"


def test_тело_koi8_без_объявленной_кодировки_угадывается():
    """Тело 8-битное, кодировка не объявлена. cp1251 дал бы «уЕОБ» — KOI8-R,
    прочитанная как cp1251, почти вся из заглавных; выбор идёт по строчным."""
    raw = (b"From: sales@supplier.example.test\r\nSubject: test\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: text/plain\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
           + "Цена насоса составляет сто пятьдесят тысяч рублей".encode("koi8_r") + b"\r\n")
    итог = rm.прочитать_письмо(raw)
    assert "Цена насоса составляет сто пятьдесят тысяч рублей" in итог["text"], итог["text"]


def test_адреса_и_имена_участников_не_попадают_в_текст():
    """Правило 17: адресов в тексте нет — ни в заголовках, ни в подписи. Участники
    названы ролями; имя человека из заголовка «От» тоже не берётся."""
    тело = "Цена 150 000 руб.\n--\nПётр, отдел продаж, пишите на orders@supplier.example.test"
    итог = rm.прочитать_письмо(_письмо(тело=тело).as_bytes())
    assert "@" not in итог["text"], итог["text"]
    assert "example.test" not in итог["text"]
    assert "Поставщиков" not in итог["text"]
    assert "От: отправитель" in итог["text"] and "Кому: получатель" in итог["text"]
    assert "Копия: получатель" in итог["text"]
    assert "[почта]" in итог["text"]
    assert "Дата: 2026-09-01 10:15" in итог["text"]


def test_html_без_тегов_br_даёт_строки_ячейки_табуляцию():
    """Outlook разбивает строки тегом <br> без пары, и прежний разбор сливал две
    позиции с ценами в одну строку; сущности &#1062; оставались цифрами."""
    html = ("<html><head><style>.x{color:red}</style><title>t</title></head><body>"
            "<p>Позиция 1 &mdash; 100&nbsp;руб<br>Позиция 2 &mdash; 200 руб</p>"
            "<table><tr><td>Насос</td><td>1 500</td></tr></table>"
            "<p>&#1062;ена &laquo;франко-склад&raquo;</p><!-- скрыто --></body></html>")
    m = MIMEText(html, "html", "utf-8")
    m["Subject"] = "html"
    m["From"] = "sales@supplier.example.test"
    текст = rm.прочитать_письмо(m.as_bytes())["text"]
    строки = текст.splitlines()
    assert "Позиция 1 — 100 руб" in строки and "Позиция 2 — 200 руб" in строки, строки
    assert "Насос\t1 500" in строки, строки
    assert "Цена «франко-склад»" in строки, строки
    assert "color" not in текст and "<" not in текст and "скрыто" not in текст


def test_из_альтернатив_берётся_простой_текст_один_раз():
    """Одно тело в двух видах: взятые оба, цены задвоились бы в базе."""
    m = MIMEMultipart("alternative")
    m.attach(MIMEText("Цена 100 руб за штуку, срок поставки четыре недели", "plain", "utf-8"))
    m.attach(MIMEText("<p>Цена 100 руб за штуку, срок поставки четыре недели</p><p>HTMLВИД</p>",
                      "html", "utf-8"))
    m["Subject"] = "alt"
    m["From"] = "sales@supplier.example.test"
    текст = rm.прочитать_письмо(m.as_bytes())["text"]
    assert текст.count("Цена 100 руб") == 1, текст
    assert "HTMLВИД" not in текст


def test_заглушка_в_простом_виде_уступает_html():
    """«Включите HTML» в простой части, таблица цен только в HTML: взять простую
    часть значит потерять цены."""
    m = MIMEMultipart("alternative")
    m.attach(MIMEText("Включите HTML", "plain", "utf-8"))
    строки = "".join(f"<tr><td>Позиция {i}</td><td>{i}00 руб</td></tr>" for i in range(1, 30))
    m.attach(MIMEText(f"<table>{строки}</table>", "html", "utf-8"))
    m["Subject"] = "alt"
    m["From"] = "sales@supplier.example.test"
    текст = rm.прочитать_письмо(m.as_bytes())["text"]
    assert "Позиция 17\t1700 руб" in текст, текст


def test_вложения_отдаются_байтами_с_именами():
    итог = rm.прочитать_письмо(_письмо(вложения=[("КП завода.pdf", PDF),
                                                 ("Спецификация.xlsx", XLSX)]).as_bytes())
    assert итог["attachments"] == [("КП завода.pdf", PDF), ("Спецификация.xlsx", XLSX)]
    assert "Вложения: КП завода.pdf; Спецификация.xlsx" in итог["text"], итог["text"]
    assert итог["reason"] == ""


def test_имя_вложения_словом_rfc2047_и_сырыми_байтами():
    """Outlook кладёт слово RFC 2047 прямо в кавычки имени, старые программы —
    байты cp1251. get_filename() во втором случае отдаёт «�»."""
    m = MIMEMultipart("mixed")
    m["Subject"] = "имена"
    m["From"] = "sales@supplier.example.test"
    m.attach(MIMEText("тело", "plain", "utf-8"))
    for имя in ("=?UTF-8?B?" + base64.b64encode("Счёт.pdf".encode()).decode() + "?=",
                "ZAGLUSHKA.pdf"):
        часть = MIMEBase("application", "pdf")
        часть.set_payload(PDF + имя.encode())
        часть["Content-Disposition"] = f'attachment; filename="{имя}"'
        m.attach(часть)
    raw = m.as_bytes().replace(b"ZAGLUSHKA", "Прайс".encode("cp1251"))
    assert "Прайс.pdf".encode("cp1251") in raw
    имена = [и for и, _ in rm.прочитать_письмо(raw)["attachments"]]
    assert имена == ["Счёт.pdf", "Прайс.pdf"], имена


def test_пересланное_письмо_раскрывается_с_вложением():
    внутреннее = _письмо(тема="Исходный запрос", тело="Нужен насос ЦНС 38-176, 2 шт.",
                         вложения=[("ТЗ.pdf", PDF)])
    итог = rm.прочитать_письмо(_письмо(тема="Fwd: запрос", тело="Пересылаю",
                                       вложенные=[внутреннее]).as_bytes())
    текст = итог["text"]
    assert "Тема: Fwd: запрос" in текст and "Пересылаю" in текст
    assert "вложенное письмо, уровень 1" in текст and "Тема: Исходный запрос" in текст
    assert "Нужен насос ЦНС 38-176, 2 шт." in текст
    assert итог["attachments"] == [("ТЗ.pdf", PDF)]
    assert итог["писем"] == 2 and итог["reason"] == ""


def test_пересланное_письмо_в_base64_тоже_раскрывается():
    внутреннее = _письмо(тема="Вложено base64", тело="Цена 42 руб")
    часть = MIMEBase("message", "rfc822")
    часть.set_payload(base64.b64encode(внутреннее.as_bytes()).decode())
    часть["Content-Transfer-Encoding"] = "base64"
    m = MIMEMultipart("mixed")
    m["Subject"] = "внешнее"
    m["From"] = "sales@supplier.example.test"
    m.attach(MIMEText("см. ниже", "plain", "utf-8"))
    m.attach(часть)
    текст = rm.прочитать_письмо(m.as_bytes())["text"]
    assert "Тема: Вложено base64" in текст and "Цена 42 руб" in текст, текст


def test_глубина_вложенных_писем_ограничена_тремя_и_потеря_названа():
    письмо = _письмо(тема="уровень 4", тело="самое глубокое")
    for уровень in (3, 2, 1, 0):
        письмо = _письмо(тема=f"уровень {уровень}", тело=f"тело {уровень}", вложенные=[письмо])
    итог = rm.прочитать_письмо(письмо.as_bytes())
    for уровень in range(4):
        assert f"Тема: уровень {уровень}" in итог["text"]
    assert "уровень 4" not in итог["text"] and "самое глубокое" not in итог["text"]
    assert "глубже 3" in итог["reason"], итог["reason"]
    assert итог["писем"] == 4


def test_одинаковое_вложение_на_двух_уровнях_берётся_один_раз():
    """Ответ с КП пересылают вместе с исходным письмом: тот же PDF на двух
    уровнях. Прочитанный дважды, он дал бы вдвое больше строк спроса."""
    внутреннее = _письмо(тема="КП", вложения=[("КП.pdf", PDF)])
    итог = rm.прочитать_письмо(_письмо(вложения=[("КП.pdf", PDF)],
                                       вложенные=[внутреннее]).as_bytes())
    assert итог["attachments"] == [("КП.pdf", PDF)]


def test_разные_вложения_с_одним_именем_различаются():
    итог = rm.прочитать_письмо(_письмо(вложения=[("image001.png", b"\x89PNG-1"),
                                                 ("image001.png", b"\x89PNG-2")]).as_bytes())
    assert [и for и, _ in итог["attachments"]] == ["image001.png", "image001-2.png"]


def test_подпись_эцп_не_становится_вложением():
    m = MIMEMultipart("signed", protocol="application/pkcs7-signature")
    m.attach(MIMEText("Цена 10 руб", "plain", "utf-8"))
    подпись = MIMEApplication(b"\x30\x82\x01\x00", "pkcs7-signature", name="smime.p7s")
    m.attach(подпись)
    m["Subject"] = "подписано"
    m["From"] = "sales@supplier.example.test"
    итог = rm.прочитать_письмо(m.as_bytes())
    assert итог["attachments"] == [] and "Цена 10 руб" in итог["text"]


def test_winmail_dat_раскрывается_и_тело_не_задваивается():
    """Outlook в формате RTF кладёт вложения внутрь winmail.dat: снаружи виден
    один «.dat», а КП внутри. Тело в TNEF повторяет простую часть — второй раз
    не берётся. Длинное имя вложения из свойств MAPI важнее короткого 8.3."""
    m = MIMEMultipart("mixed")
    m["Subject"] = "RTF-письмо"
    m["From"] = "sales@supplier.example.test"
    m.attach(MIMEText("Внешнее тело", "plain", "utf-8"))
    часть = MIMEApplication(_tnef(), "ms-tnef")
    часть.add_header("Content-Disposition", "attachment", filename="winmail.dat")
    m.attach(часть)
    итог = rm.прочитать_письмо(m.as_bytes())
    assert итог["attachments"] == [("КП от завода.pdf", PDF)], итог["attachments"]
    assert "Внешнее тело" in итог["text"] and "Тело из winmail" not in итог["text"]
    assert "Вложения: КП от завода.pdf" in итог["text"], итог["text"]
    assert итог["reason"] == ""


def test_winmail_dat_отдельным_файлом_даёт_тему_и_тело():
    итог = rm.прочитать_письмо(_tnef())
    assert итог["формат"] == "tnef"
    assert "Тема: Коммерческое предложение" in итог["text"], итог["text"]
    assert "Тело из winmail" in итог["text"]
    assert итог["attachments"] == [("КП от завода.pdf", PDF)]


def test_письмо_msg_во_вложении_eml_раскрывается_здесь_же():
    """Письмо во вложении раскрывается внутри, а не отдаётся наружу: глубина
    считается одна на всю цепочку, иначе предел ничего бы не ограничивал."""
    итог = rm.прочитать_письмо(_письмо(вложения=[("ответ.msg", _msg(тема="Ответ завода"))])
                               .as_bytes())
    assert "Тема: Ответ завода" in итог["text"] and "уровень 1" in итог["text"]
    assert итог["attachments"] == []


def test_это_письмо_eml():
    assert rm.это_письмо_eml(_письмо().as_bytes())
    assert rm.это_письмо_eml(b"From MAILER-DAEMON Tue Sep  1 10:00:00 2026\n" + _письмо().as_bytes())
    # тело сразу после заголовков, без пустой строки — против RFC, но бывает
    assert rm.это_письмо_eml("From: a@example.test\nSubject: s\nЦена 100 руб\n".encode())
    for не_письмо in ("Наименование;Кол-во;Цена\nНасос;1;100\n".encode(),
                      "Кому: закупщику\nТема: цена\n\nтекст".encode(),
                      b"Date: 2026-09-01\nprice list follows\n",
                      b"<html><body>From: x</body></html>", PDF, _msg(), b""):
        assert not rm.это_письмо_eml(не_письмо), не_письмо[:40]


def test_не_письмо_называется_а_не_читается_прозой():
    итог = rm.прочитать_письмо("Наименование;Кол-во\nНасос;1\n".encode())
    assert итог["text"] == "" and "не письмо" in итог["reason"]


# ═══════════════════════════════════ .msg ═══════════════════════════════════

@pytest.mark.parametrize("сдвиг", [9, 12], ids=["версия3-512", "версия4-4096"])
def test_msg_тема_тело_роли_и_вложения_из_мини_и_обычных_секторов(сдвиг):
    """Короткое вложение лежит в мини-потоке, длинное (от 4096 байт) — в обычных
    секторах: это два разных пути чтения, и оба обязаны отдать байты точно."""
    длинное = XLSX * 3
    assert len(PDF) < 4096 <= len(длинное)
    b = _msg(вложения=[("КП завода.pdf", PDF), ("Спецификация.xlsx", длинное)], сдвиг=сдвиг,
             фикс=[(0x0039, 0x0040, _filetime(2026, 9, 1, 7, 15))])
    assert rm.это_письмо_outlook(b)
    итог = rm.прочитать_письмо(b)
    assert итог["attachments"] == [("КП завода.pdf", PDF), ("Спецификация.xlsx", длинное)]
    текст = итог["text"]
    assert "Тема: Запрос цены" in текст and "Цена насоса 150 000 руб." in текст
    assert "Дата: 2026-09-01 07:15" in текст
    assert "От: отправитель" in текст and "Кому: получатель" in текст
    assert "@" not in текст and "Поставщиков" not in текст
    assert "Вложения: КП завода.pdf; Спецификация.xlsx" in текст
    assert итог["формат"] == "msg" and итог["reason"] == ""


def test_msg_тяжелее_семи_мегабайт_читается_через_difat():
    """Шапка перечисляет только 109 секторов таблицы размещения — это 7 МБ при
    секторе 512 байт. Письмо с тяжёлым PDF длиннее, и остальные секторы таблицы
    лежат цепочкой DIFAT: не прочитай её — вложение обрывается на седьмом
    мегабайте."""
    тяжёлое = PDF + bytes(range(256)) * (8 * 1024 * 4)
    b = _msg(вложения=[("Чертежи.pdf", тяжёлое)])
    assert struct.unpack_from("<I", b, 72)[0] >= 1, "в корпусе нет цепочки DIFAT"
    итог = rm.прочитать_письмо(b)
    assert итог["attachments"] == [("Чертежи.pdf", тяжёлое)]


def test_мусор_в_старших_байтах_размера_версии_3_отбрасывается():
    b = _cfb(_msg_узел(вложения=[("КП.pdf", PDF), ("x.xlsx", XLSX * 3)]), мусор_в_размере=True)
    итог = rm.прочитать_письмо(b)
    assert итог["attachments"] == [("КП.pdf", PDF), ("x.xlsx", XLSX * 3)]
    assert "Цена насоса 150 000 руб." in итог["text"]


def test_msg_отличается_от_книги_и_документа():
    """.xls, .doc и .msg — один и тот же OLE2. Отличаются потоками в корне."""
    книга = _cfb({"Workbook": b"\x09\x08" + b"\x00" * 5000, "\x05SummaryInformation": b"x" * 100})
    документ = _cfb({"WordDocument": b"\xec\xa5" + b"\x00" * 600, "1Table": b"\x00" * 300})
    assert not rm.это_письмо_outlook(книга) and not rm.это_письмо_outlook(документ)
    assert rm.это_письмо_outlook(_msg())
    assert not rm.это_письмо_outlook(PDF) and not rm.это_письмо_outlook(b"")
    итог = rm.прочитать_письмо(книга)
    assert итог["text"] == "" and "не письмо" in итог["reason"]


def test_msg_документ_со_вложенным_письмом_не_письмо():
    """Документ Word со вставленным письмом содержит потоки Outlook, но глубже
    корня. Поиск по байтам принял бы его за письмо и увёл из читателя .doc."""
    документ = _cfb({"WordDocument": b"\xec\xa5" + b"\x00" * 600,
                     "ObjectPool": {"_1234": _msg_узел()}})
    assert "__substg1.0_".encode("utf-16-le") in документ
    assert not rm.это_письмо_outlook(документ)


def test_msg_тело_из_html_когда_простого_нет():
    html = "<p>Цена задвижки<br>30с41нж — 12&nbsp;500 руб</p>".encode("cp1251")
    b = _msg(тело=None, доп={"__substg1.0_10130102": html},
             фикс=[(0x3FDE, 0x0003, struct.pack("<i", 1251))])
    текст = rm.прочитать_письмо(b)["text"]
    assert "Цена задвижки\n30с41нж — 12 500 руб" in текст, текст


def test_msg_тело_из_сжатого_rtf_когда_нет_ни_простого_ни_html():
    """Письмо в формате RTF несёт тело только в сжатом RTF. Пример сжатия — из
    MS-OXRTFCP 3.1.1: независимая проверка распаковщика."""
    сжатый = bytes.fromhex("2d0000002b0000004c5a4675f1c5c7a703000a007263706731323542320af3"
                           "2068656c090020627705b06c647d0a800fa0")
    текст = rm.прочитать_письмо(_msg(тело=None, доп={"__substg1.0_10090102": сжатый}))["text"]
    assert текст.endswith("hello world"), текст


def test_rtf_распаковка_по_примерам_спецификации():
    второй = bytes.fromhex("1a0000001c0000004c5a4675e2d44b51410004205758595a0d6e7d010eb0")
    assert rm._rtf_распаковать(второй) == b"{\\rtf1 WXYZWXYZWXYZWXYZWXYZ}"
    несжатый = struct.pack("<III", 12 + 5, 5, 0x414C454D) + b"\x00" * 4 + b"{abc}"
    assert rm._rtf_распаковать(несжатый) == b"{abc}"


def test_rtf_с_html_внутри_берёт_html_и_не_берёт_оформление_rtf():
    """HTML-письмо в RTF (\\fromhtml1): исходный HTML — в {\\*\\htmltag}, а то, что
    между \\htmlrtf и \\htmlrtf0, в письме не было и попасть в текст не должно."""
    rtf = (r"{\rtf1\ansi\ansicpg1251\fromhtml1 {\fonttbl{\f0 Arial;}}"
           r"{\*\htmltag19 <body>}{\*\htmltag64 <p>}\htmlrtf {\htmlrtf0 "
           r"\'d6\'e5\'ed\'e0 100 \u8364\'3f"
           r"\htmlrtf\par ТОЛЬКО_RTF}\htmlrtf0 {\*\htmltag72 </p>}"
           r"{\*\htmltag64 <p>}Срок{\*\htmltag84 &nbsp;}4 нед{\*\htmltag72 </p>}}").encode("cp1251")
    текст = rm._rtf_в_текст(rtf)
    assert текст.splitlines() == ["Цена 100 €", "Срок 4 нед"], текст
    assert "Arial" not in текст


def test_rtf_читается_в_кодовой_странице_из_ansicpg():
    """Кодовая страница — из \\ansicpg документа, а не всегда cp1251: письмо
    иностранного завода в cp1252 иначе превращает «é» в «й»."""
    assert rm._rtf_в_текст(rb"{\rtf1\ansi\ansicpg1252 Pompe centrifuge, caf\'e9}") == \
        "Pompe centrifuge, café"


def test_msg_строка_не_юникод_читается_в_кодовой_странице_письма():
    """Строки 001E — в кодовой странице из свойств письма, а не всегда cp1251."""
    узел = _msg_узел(тема="")
    узел.pop("__substg1.0_0037001F")
    узел["__substg1.0_0037001E"] = "Цена клапана".encode("koi8_r") + b"\x00"
    узел["__properties_version1.0"] = _поток_свойств(32, [(0x3FFD, 0x0003,
                                                           struct.pack("<i", 20866))])
    assert "Тема: Цена клапана" in rm.прочитать_письмо(_cfb(узел))["text"]


def test_msg_письмо_во_вложении_раскрывается():
    """Способ 5: письмо во вложении — хранилище внутри хранилища, у его потока
    свойств шапка 24 байта, а не 32. Ошибка в шапке сдвигает дату."""
    вложенное = _msg_узел(тема="Исходный запрос", тело="Нужно 2 шт.", шапка=24,
                          вложения=[("ТЗ.pdf", PDF)],
                          фикс=[(0x0039, 0x0040, _filetime(2026, 8, 20, 9, 30))])
    итог = rm.прочитать_письмо(_msg(тема="Fwd", вложения=[("письмо", вложенное)]))
    текст = итог["text"]
    assert "вложенное письмо, уровень 1" in текст and "Тема: Исходный запрос" in текст
    assert "Нужно 2 шт." in текст and "Дата: 2026-08-20 09:30" in текст, текст
    assert итог["attachments"] == [("ТЗ.pdf", PDF)] and итог["писем"] == 2


def test_msg_вложение_ссылкой_потеря_названа():
    итог = rm.прочитать_письмо(_msg(вложения=[("ссылка", "КП.pdf")]))
    assert итог["attachments"] == [] and "ссылкой" in итог["reason"]


def test_msg_вставленный_объект_ole_отдаёт_файл():
    """Способ 6: объект, вставленный в тело RTF-письма, — ещё одно хранилище.
    PDF вставляется потоком CONTENTS, упакованный файл — потоком Ole10Native."""
    нативный = (struct.pack("<IH", 0, 2) + "Прайс.xlsx".encode("cp1251") + b"\x00"
                + b"C:\\tmp\\p.xlsx\x00" + struct.pack("<I", 0x00030000)
                + struct.pack("<I", 12) + b"C:\\t\\p.xlsx\x00" + struct.pack("<I", len(XLSX)) + XLSX)
    узел = _msg_узел()
    for н, объект in enumerate(({"CONTENTS": PDF, "\x01CompObj": b"x" * 40},
                                {"\x01Ole10Native": нативный})):
        узел[f"__attach_version1.0_#{н:08X}"] = dict([
            ("__properties_version1.0", _поток_свойств(8, [(0x3705, 0x0003,
                                                            struct.pack("<i", 6))])),
            _строка(0x3707, "объект.bin"), ("__substg1.0_3701000D", объект)])
    итог = rm.прочитать_письмо(_cfb(узел))
    assert итог["attachments"] == [("объект.bin", PDF), ("Прайс.xlsx", XLSX)], итог["attachments"]


# ═════════════════════════════ никогда не бросает ═══════════════════════════

def _битые() -> list:
    целое = _msg(вложения=[("КП.pdf", PDF), ("x.xlsx", XLSX * 3)])
    петля = bytearray(целое)
    первый_fat = struct.unpack_from("<I", петля, 76)[0]
    начало = (первый_fat + 1) * 512
    for k in range(128):                            # каждый сектор ссылается на себя
        struct.pack_into("<I", петля, начало + 4 * k, k)
    return [b"", b"   ", None, "строка", 12345, целое[:700], целое[:len(целое) // 2],
            rm.OLE_ПОДПИСЬ + b"\x00" * 600, rm.OLE_ПОДПИСЬ + b"\xff" * 5000, bytes(петля),
            rm.TNEF_ПОДПИСЬ + b"\x00\x00\x02\xff\xff\xff\xff\xff\xff\xff\xff" * 3,
            _tnef()[:40], PDF, XLSX,
            _письмо(вложения=[("КП.pdf", PDF)]).as_bytes()[:-60],
            b"Content-Type: multipart/mixed; boundary=x\r\nSubject: s\r\n\r\n" * 3,
            b"From: a\r\nSubject: s\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n"
            + b"--b\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n" * 300]


def test_петля_в_таблице_секторов_обрывается():
    """Цепочка, замкнутая на себя, в битом файле обязана кончиться на первом же
    повторе, а не крутиться, пока не кончится память: тогда прогон падает целиком,
    а не теряет одно письмо. Счётчик вызовов не даёт тесту самому зависнуть."""
    вызовы: list[int] = []

    def читать(n: int) -> bytes:
        вызовы.append(n)
        assert len(вызовы) < 50, "цепочка секторов зациклилась"
        return b"x" * 512

    assert rm._Ole._цепь(0, (1, 2, 0), читать) == b"x" * 512 * 3
    assert вызовы == [0, 1, 2]
    итог = rm.прочитать_письмо(_битые()[9])
    assert итог["reason"], "битая таблица секторов прошла без названной причины"


@pytest.mark.parametrize("вход", _битые(), ids=lambda x: repr(x)[:30])
def test_никогда_не_бросает(вход):
    итог = rm.прочитать_письмо(вход)
    assert set(итог) >= {"text", "attachments", "reason"}
    assert isinstance(итог["text"], str) and isinstance(итог["attachments"], list)
    assert итог["text"] or итог["attachments"] or итог["reason"], "потеря без названной причины"
    rm.это_письмо_outlook(вход)
    rm.это_письмо_eml(вход)


def test_длинная_строка_без_собаки_не_вешает_разбор():
    """Base64, вставленный в тело текстом, — сплошные буквы без «@». Регулярка
    адреса без пределов повторов перебирала такую строку квадратично: 200 000
    букв — 134 с (замер 23.09.2026). С пределами — доли секунды."""
    import time
    m = MIMEText("Цена 100 руб\n" + "A" * 60_000, "plain", "utf-8")
    m["Subject"] = "длинно"
    m["From"] = "sales@supplier.example.test"
    начало = time.monotonic()
    итог = rm.прочитать_письмо(m.as_bytes())
    assert time.monotonic() - начало < 3, "перебор адреса стал квадратичным"
    assert "Цена 100 руб" in итог["text"]


def test_незакрытые_комментарии_html_не_вешают_разбор():
    """«<!--» без закрытия: ленивая регулярка от каждого открытия шла до конца
    строки — 10 000 открытий на 200 КБ давали 1,9 с, 20 000 на 300 КБ — втрое
    дольше. Проход один, и текст после незакрытого блока не теряется."""
    import time
    html = "<p>Цена 100 руб</p>" + "у" * 300_000 + "<!--" * 20_000 + "<p>хвост</p>"
    начало = time.monotonic()
    текст = rm._html_в_текст(html)
    assert time.monotonic() - начало < 1, "вырезание комментариев стало квадратичным"
    assert текст.startswith("Цена 100 руб") and текст.endswith("хвост")
    assert rm._html_в_текст("до<!-- скрыто -->после<STYLE a=1>.x{}</style >!") == "допосле!"


def test_внутренняя_ошибка_становится_причиной(monkeypatch):
    def сломано(_b):
        raise RuntimeError("проверка")
    monkeypatch.setattr(rm, "_прочитать", сломано)
    итог = rm.прочитать_письмо(b"x")
    assert итог["text"] == "" and "RuntimeError" in итог["reason"]


def test_модуль_ничего_не_печатает(capsys):
    """Правило 17: журнал прогона публичный, модуль только возвращает."""
    for вход in [_письмо(вложения=[("КП.pdf", PDF)]).as_bytes(), _msg(), _tnef(), *_битые()]:
        rm.прочитать_письмо(вход)
    assert capsys.readouterr() == ("", "")


# ═════════════════════════════════ живые письма ═════════════════════════════

def test_живые_письма_из_каталога():
    """Живые .msg и .eml — только локально: READ_MAIL_LIVE_DIR=<каталог>.
    В репозиторий настоящие письма не попадают (правила 5 и 18)."""
    каталог = os.environ.get("READ_MAIL_LIVE_DIR")
    if not каталог:
        pytest.skip("READ_MAIL_LIVE_DIR не задан: живые письма проверяются локально")
    файлы = [п for п in Path(каталог).rglob("*") if п.suffix.lower() in (".msg", ".eml")]
    if not файлы:
        pytest.skip("в каталоге нет .msg и .eml")
    for п in файлы:
        итог = rm.прочитать_письмо(п.read_bytes())
        assert итог["text"] or итог["attachments"], (п.name, итог["reason"])
        assert "@" not in итог["text"], п.name
