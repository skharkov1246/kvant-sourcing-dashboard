"""Распаковка архивов до документов (library/read_archive.py).

Замер 23.09.2026: 169 архивов в базе, все 169 без позиций. Каждый тест здесь —
один из путей, которым документ из архива терялся: RAR и 7z, tar.gz, имена в
cp866, участник без расширения, архив в архиве, зашифрованный сосед, бомба.

Все архивы собираются здесь же из придуманных корпусов (CLAUDE.md, правило 18);
7z и RAR — настоящими программами, если они есть, иначе тест пропускается.
Внешний путь (временный каталог, таймаут, разбор сообщений) проверяется ещё и
поддельными распаковщиками — он должен быть проверен и там, где 7z не стоит.
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import random
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import zipfile
import zlib

import pytest

from library import read_archive as ra

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer\n%%EOF\n"
CSV = "Наименование;Кол-во\nНасос ЦНС 38-176;2\nЗадвижка 30с41нж;4\n".encode("cp1251")
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(200)
XML_1C = '<?xml version="1.0"?>\n<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">'.encode()
МУСОР = bytes(range(256)) * 8                  # нули и управляющие: не документ


def _zip(файлы, метод=zipfile.ZIP_DEFLATED) -> bytes:
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w", метод) as z:
        for имя, данные in файлы:
            z.writestr(имя, данные)
    return буфер.getvalue()


def _xlsx() -> bytes:
    return _zip([("[Content_Types].xml", "<Types/>"), ("xl/workbook.xml", "<workbook/>")])


def _zip_сырые_имена(файлы) -> bytes:
    """ZIP с именами, записанными готовыми байтами и БЕЗ флага UTF-8 — как у
    архиватора русской Windows. zipfile сам так не пишет (не-ASCII имя он
    кодирует в UTF-8 и ставит флаг), поэтому пишем заглушку той же длины и
    меняем её байты в локальном заголовке и в центральном каталоге."""
    заглушки = []
    for n, (сырое, _) in enumerate(файлы):
        заглушка = f"@{n:03d}".encode().ljust(len(сырое), b"_")
        assert len(заглушка) == len(сырое)
        заглушки.append(заглушка)
    b = _zip([(з.decode(), д) for з, (_, д) in zip(заглушки, файлы)])
    for заглушка, (сырое, _) in zip(заглушки, файлы):
        assert b.count(заглушка) == 2
        b = b.replace(заглушка, сырое)
    return b


def _зашифровать(b: bytes, имя: str) -> bytes:
    """Ставит бит «зашифровано» участнику в обоих заголовках — так zipfile
    видит его ровно как настоящий зашифрованный (zipfile писать шифр не умеет)."""
    b = bytearray(b)
    сырое = имя.encode()
    for подпись, смещение_флага, смещение_имени in ((b"PK\x03\x04", 6, 30), (b"PK\x01\x02", 8, 46)):
        поз = 0
        while (j := b.find(подпись, поз)) >= 0:
            if b[j + смещение_имени:j + смещение_имени + len(сырое)] == сырое:
                флаги = struct.unpack_from("<H", b, j + смещение_флага)[0] | 1
                struct.pack_into("<H", b, j + смещение_флага, флаги)
            поз = j + 4
    return bytes(b)


def _tar(файлы, режим="w", **kw) -> bytes:
    буфер = io.BytesIO()
    with tarfile.open(fileobj=буфер, mode=режим, **kw) as t:
        for имя, данные in файлы:
            info = tarfile.TarInfo(имя)
            info.size = len(данные)
            t.addfile(info, io.BytesIO(данные))
    return буфер.getvalue()


def _имена(участники) -> list[str]:
    return [имя for имя, _ in участники]


# ─────────────────────────────── имена в ZIP ────────────────────────────────

def test_имя_cp866_без_флага_utf8_читается_по_русски():
    """Главный случай писем с Windows: без исправления zipfile отдаёт имя в
    cp437 — «æÑÑ¿ÑÇ¿¬áµ¿∩», и по нему участника не найти ни глазами, ни отбором."""
    b = _zip_сырые_имена([("Спецификация.xlsx".encode("cp866"), _xlsx()),
                          ("Письмо.pdf".encode("cp866"), PDF)])
    участники, причина = ra.распаковать(b)
    assert _имена(участники) == ["Спецификация.xlsx", "Письмо.pdf"], _имена(участники)
    assert причина == ""


def test_имя_utf8_без_флага_как_у_macos_не_портится_в_cp866():
    b = _zip_сырые_имена([("Коммерческое предложение.pdf".encode("utf-8"), PDF)])
    участники, _ = ra.распаковать(b)
    assert _имена(участники) == ["Коммерческое предложение.pdf"]


def test_имя_cp1251_без_флага_узнаётся_по_буквам():
    b = _zip_сырые_имена([("Счёт на оплату.pdf".encode("cp1251"), PDF)])
    участники, _ = ra.распаковать(b)
    assert _имена(участники) == ["Счёт на оплату.pdf"]


def test_имя_с_флагом_utf8_остаётся_как_есть():
    """Флаг 0x800 — имя уже верно; перекодирование испортило бы его."""
    участники, _ = ra.распаковать(_zip([("Ведомость ЗИП.pdf", PDF)]))
    assert _имена(участники) == ["Ведомость ЗИП.pdf"]


def test_имя_с_флагом_utf8_и_умляутами_не_перекодируется():
    """Немецкий поставщик: «Ölpumpe» кодируется и в cp437, и без проверки флага
    имя ушло бы в перекодировку cp866 и стало бы «Щlpumpe»."""
    участники, _ = ra.распаковать(_zip([("Ölpumpe Müller.pdf", PDF)]))
    assert _имена(участники) == ["Ölpumpe Müller.pdf"]


def test_имя_из_поля_unicode_path_побеждает_основное():
    """WinRAR пишет рядом с cp866-именем поле 0x7075 с UTF-8 — оно точнее."""
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w") as z:
        info = zipfile.ZipInfo("spec.pdf")
        верное = "Спецификация насоса.pdf".encode()
        тело = b"\x01" + struct.pack("<I", zlib.crc32(b"spec.pdf")) + верное
        info.extra = struct.pack("<HH", 0x7075, len(тело)) + тело
        z.writestr(info, PDF)
    участники, _ = ra.распаковать(буфер.getvalue())
    assert _имена(участники) == ["Спецификация насоса.pdf"]


# ───────────────────────────── отбор по байтам ──────────────────────────────

def test_участник_отбирается_по_байтам_а_не_по_расширению():
    b = _zip([
        ("СПЕЦИФИКАЦИЯ", _xlsx()),             # без расширения — книга
        ("скан", JPEG),                        # без расширения — картинка
        ("data.bin", PDF),                     # чужое расширение — PDF
        ("мусор.dat", МУСОР),                  # не документ
        ("выгрузка.xls", XML_1C),              # XML 1С под .xls — имя не трогаем
        ("перечень", CSV),                     # текст cp1251
    ])
    участники, причина = ra.распаковать(b)
    assert _имена(участники) == ["СПЕЦИФИКАЦИЯ.xlsx", "скан.jpg", "data.bin.pdf",
                                 "выгрузка.xls", "перечень.txt"], _имена(участники)
    assert dict(участники)["data.bin.pdf"] == PDF
    assert причина == "", причина            # не-документ — не потеря


def test_служебные_файлы_архиваторов_не_отдаются():
    """Thumbs.db — тот же OLE2, что .xls: без отбора ушёл бы в читатель книг."""
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(600)
    b = _zip([("__MACOSX/._spec.pdf", PDF), ("Thumbs.db", ole), ("spec.pdf", PDF)])
    участники, _ = ra.распаковать(b)
    assert _имена(участники) == ["spec.pdf"]
    пусто, причина = ra.распаковать(_zip([("Thumbs.db", ole)]))
    assert пусто == [] and причина


def test_вид_по_байтам():
    assert ra.вид(PDF) == "pdf"
    assert ra.вид(b"garbage\r\n" + PDF) == "pdf"          # мусор перед заголовком
    assert ra.вид(_xlsx()) == "xlsx"
    assert ra.вид(CSV) == "текст"
    assert ra.вид(МУСОР) == ""
    assert ra.вид(bytes(range(1, 32)) * 100) == ""        # управляющие без нулей
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(100)
    assert ra.вид(ole + "Workbook".encode("utf-16-le")) == "xls"
    assert ra.вид(ole + "WordDocument".encode("utf-16-le")) == "doc"
    assert ra.вид(ole + "__substg1.0_0037001F".encode("utf-16-le")) == "msg"
    # tar с PDF первым участником: подпись PDF на 512-м байте — это всё равно tar
    assert ra.вид(_tar([("a.pdf", PDF)])) == "tar"


def test_книга_и_документ_не_архив():
    """Прежний отбор путал PK-архив с книгой: 169 архивов легли «пусто»."""
    assert ra.это_архив(_zip([("a.pdf", PDF)]))
    assert not ra.это_архив(_xlsx())
    assert not ra.это_архив(PDF)
    assert ra.распаковать(_xlsx()) == ([], "не архив")
    assert ra.распаковать(PDF) == ([], "не архив")


# ─────────────────────────────── вложенность ────────────────────────────────

def test_архив_в_архиве_читается():
    """Обычный случай: «КП.zip» внутри «Письмо.zip»."""
    кп = _zip([("Спецификация.pdf", PDF)])
    письмо = _zip([("Сопроводительное.txt", CSV), ("КП.zip", кп)])
    участники, причина = ra.распаковать(письмо)
    assert _имена(участники) == ["Сопроводительное.txt", "КП.zip/Спецификация.pdf"]
    assert участники[1][1] == PDF
    assert причина == ""


def test_глубина_ограничена_и_названа():
    третий = _zip([("глубоко.pdf", PDF)])
    второй = _zip([("третий.zip", третий)])
    первый = _zip([("второй.zip", второй), ("верх.pdf", PDF)])
    участники, причина = ra.распаковать(первый, глубина=2)
    assert _имена(участники) == ["верх.pdf"]
    assert "предел глубины" in причина
    участники, причина = ra.распаковать(первый, глубина=3)
    assert "второй.zip/третий.zip/глубоко.pdf" in _имена(участники)
    assert причина == ""
    участники, причина = ra.распаковать(первый, глубина=1)
    assert _имена(участники) == ["верх.pdf"] and "предел глубины" in причина


def test_глубина_не_больше_потолка():
    b = _zip([("дно.pdf", PDF)])
    for n in range(8):
        b = _zip([(f"уровень{n}.zip", b)])
    участники, причина = ra.распаковать(b, глубина=100)
    assert участники == [] and "предел глубины" in причина


# ─────────────────────────── tar, gz, bz2, xz ───────────────────────────────

@pytest.mark.parametrize("режим", ["w:gz", "w:bz2", "w:xz", "w"])
def test_tar_со_сжатием_читается(режим):
    b = _tar([("docs/КП.pdf", PDF), ("docs/перечень.csv", CSV), ("docs/фото.dat", МУСОР)], режим)
    участники, причина = ra.распаковать(b)
    assert _имена(участники) == ["docs/КП.pdf", "docs/перечень.csv"]
    assert причина == ""


def test_tar_gz_внутри_zip_это_один_уровень():
    """tar.gz — один архив: иначе при глубине 2 «Письмо.zip/выгрузка.tar.gz»
    не открылся бы, хотя это всего лишь архив в архиве."""
    b = _zip([("выгрузка.tar.gz", _tar([("КП.pdf", PDF)], "w:gz"))])
    участники, причина = ra.распаковать(b, глубина=2)
    assert _имена(участники) == ["выгрузка.tar.gz/КП.pdf"], (участники, причина)


def test_имя_в_tar_cp866_читается():
    b = _tar([("Спецификация.pdf", PDF)], "w", format=tarfile.GNU_FORMAT, encoding="cp866")
    участники, _ = ra.распаковать(b)
    assert _имена(участники) == ["Спецификация.pdf"]


def _gz_с_именем(имя: bytes, данные: bytes) -> bytes:
    """gzip с полем FNAME. Модуль gzip пишет имя только в latin-1, а русское
    имя из архиватора Windows приходит байтами — собираем заголовок руками."""
    сж = zlib.compressobj(9, zlib.DEFLATED, -15)
    тело = сж.compress(данные) + сж.flush()
    return (b"\x1f\x8b\x08\x08" + bytes(4) + b"\x00\x03" + имя + b"\x00" + тело
            + struct.pack("<II", zlib.crc32(данные), len(данные)))


def test_одиночный_gz_берёт_имя_из_заголовка():
    участники, причина = ra.распаковать(_gz_с_именем("Спецификация.pdf".encode(), PDF))
    assert участники == [("Спецификация.pdf", PDF)], участники
    assert причина == ""


def test_одиночные_bz2_и_xz_без_имени():
    assert ra.распаковать(bz2.compress(PDF)) == ([("содержимое.pdf", PDF)], "")
    assert ra.распаковать(lzma.compress(CSV)) == ([("содержимое.txt", CSV)], "")
    assert ra.распаковать(gzip.compress(PDF, mtime=0)) == ([("содержимое.pdf", PDF)], "")


def test_gz_внутри_zip_получает_имя_без_суффикса():
    b = _zip([("КП.pdf.gz", gzip.compress(PDF, mtime=0))])
    участники, _ = ra.распаковать(b)
    assert участники == [("КП.pdf.gz/КП.pdf", PDF)]


# ─────────────────────────── шифр, бомбы, пределы ───────────────────────────

def test_зашифрованный_участник_не_мешает_остальным():
    b = _зашифровать(_zip([("secret.pdf", PDF), ("open.pdf", PDF)]), "secret.pdf")
    assert zipfile.ZipFile(io.BytesIO(b)).getinfo("secret.pdf").flag_bits & 1
    участники, причина = ra.распаковать(b)
    assert _имена(участники) == ["open.pdf"]
    assert "зашифровано" in причина, причина
    пусто, причина = ra.распаковать(_зашифровать(_zip([("secret.pdf", PDF)]), "secret.pdf"))
    assert пусто == [] and "зашифровано" in причина


def test_бомба_упирается_в_предел_размера(monkeypatch):
    """Предел общий на всю распаковку: 3 МБ текста при пределе 1 МБ не читаются,
    а мелкий сосед — читается."""
    monkeypatch.setenv("ARCHIVE_MAX_MB", "1")
    большой = ("Позиция;1\n" * 300_000).encode()
    участники, причина = ra.распаковать(_zip([("большой.txt", большой), ("КП.pdf", PDF)]))
    assert _имена(участники) == ["КП.pdf"]
    assert "предел размера" in причина
    участники, причина = ra.распаковать(gzip.compress(большой))
    assert участники == [] and "предел размера" in причина
    участники, причина = ra.распаковать(_tar([("большой.txt", большой)], "w:xz"))
    assert участники == [] and "предел размера" in причина


def test_бомба_не_разворачивается_в_память(monkeypatch):
    """Предел должен держать ЧТЕНИЕ, а не только ответ: проверка «больше
    предела» после gzip.decompress() целиком уже развернула бы 30 МБ нулей из
    30 КБ архива (у 42.zip — петабайты). Считаем, сколько байт отдали разжиматели."""
    monkeypatch.setenv("ARCHIVE_MAX_MB", "1")
    счёт = {"gz": 0, "zip": 0}

    def считать(ключ, исходный):
        def read(self, n=-1):
            d = исходный(self, n)
            счёт[ключ] += len(d)
            return d
        return read
    monkeypatch.setattr(gzip.GzipFile, "read", считать("gz", gzip.GzipFile.read))
    monkeypatch.setattr(zipfile.ZipExtFile, "read", считать("zip", zipfile.ZipExtFile.read))
    бомба = b"A" * (30 * ra.МБ)
    assert ra.распаковать(gzip.compress(бомба, 1))[1] == "предел размера"
    assert счёт["gz"] <= 2 * ra.МБ, счёт
    # участник, заявивший 30 МБ при пределе 1 МБ, не читается дальше головы
    assert ra.распаковать(_zip([("бомба.txt", бомба)]))[1] == "предел размера"
    assert счёт["zip"] <= 64 * 1024, счёт


def test_заголовок_соврал_о_размере_чтение_всё_равно_ограничено():
    """Источник заявил 10 байт, а отдаёт сто тысяч — повреждённый заголовок или
    нарочно собранная бомба. Читается не больше остатка бюджета плюс байт
    (голова 8 КБ читается всегда, поэтому бюджет здесь больше головы)."""
    прочитано = []

    class Врун(io.BytesIO):
        def read(self, n=-1):
            d = super().read(n)
            прочитано.append(len(d))
            return d
    сбор = ra._Сбор(предел=10_000, таймаут=1)
    сбор.взять("врун.txt", 10, lambda: Врун(b"A" * 100_000), 0)
    assert сбор.итог() == ([], "предел размера")
    assert sum(прочитано) <= 10_001, прочитано


def test_бюджет_общий_на_вложенные_архивы(monkeypatch):
    """Каждый вложенный архив по отдельности в предел влезает, вместе — нет."""
    monkeypatch.setenv("ARCHIVE_MAX_MB", "1")
    кусок = ("Позиция;1\n" * 40_000).encode()             # 400 КБ
    b = _zip([(f"часть{n}.zip", _zip([(f"часть{n}.txt", кусок)])) for n in range(4)])
    участники, причина = ra.распаковать(b)
    assert 0 < len(участники) < 4
    assert "предел размера" in причина


def test_предел_числа_участников():
    b = _zip([(f"строка{n}.txt", f"Позиция {n};1\n".encode()) for n in range(250)])
    участники, причина = ra.распаковать(b)
    assert len(участники) == ra.МАКС_УЧАСТНИКОВ == 200
    assert "предел числа участников" in причина


def test_zip_без_центрального_каталога_читается_по_локальным_заголовкам():
    """Обрезанное вложение: zipfile без каталога в хвосте не видит ничего."""
    b = _zip([("КП.pdf", PDF * 20), ("перечень.csv", CSV * 20)])
    обрезок = b[:b.find(b"PK\x01\x02")]
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(обрезок))
    участники, причина = ra.распаковать(обрезок)
    assert участники == [("КП.pdf", PDF * 20), ("перечень.csv", CSV * 20)], причина
    # оборван посреди второго участника — первый всё равно читается
    участники, _ = ra.распаковать(обрезок[:-40])
    assert участники[0] == ("КП.pdf", PDF * 20)


@pytest.mark.parametrize("n", range(14))
def test_мусор_не_роняет_и_называет_причину(n):
    случай = random.Random(n)
    входы = [
        b"", None, "строка вместо байтов", b"PK\x03\x04", b"PK\x03\x04" + случай.randbytes(500),
        b"Rar!\x1a\x07\x00" + случай.randbytes(300), b"7z\xbc\xaf\x27\x1c\x00\x04" + случай.randbytes(300),
        b"\x1f\x8b\x08\x00" + случай.randbytes(300), b"BZh91AY&SY" + случай.randbytes(300),
        b"\xfd7zXZ\x00" + случай.randbytes(300), случай.randbytes(5000),
        _zip([("a.pdf", PDF)])[:25], b"MSCF\x00\x00\x00\x00" + случай.randbytes(100),
        _tar([("a.pdf", PDF)])[:530],               # оборван посреди участника
    ]
    участники, причина = ra.распаковать(входы[n])
    assert isinstance(участники, list) and isinstance(причина, str)
    assert участники == [] and причина, (n, причина)


# ────────────────────── внешний путь: поддельные программы ──────────────────

def _подделка(каталог, имя: str, тело: str):
    """Поддельный распаковщик на Python: внешний путь проверяется и там, где
    ни 7z, ни unrar не стоят (гейт их не ставит)."""
    путь = каталог / имя
    путь.write_text(f"#!{sys.executable}\nimport sys, os, time\nargs = sys.argv[1:]\n{тело}\n")
    путь.chmod(0o755)
    return путь


СЕМЁРКА = b"7z\xbc\xaf\x27\x1c\x00\x04" + bytes(40)


def test_внешний_распаковщик_отдаёт_дерево_и_убирает_каталог(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _подделка(bin_, "7z", """
if args[0] == "l":
    sys.exit(2)                                   # оглавления нет — читаем без него
out = next(a[2:] for a in args if a.startswith("-o"))
os.makedirs(os.path.join(out, "sub"))
open(os.path.join(out, "spec.pdf"), "wb").write(b"%PDF-1.4 fake")
open(os.path.join(out, "sub", "перечень.csv"), "wb").write("а;б\\n1;2\\n".encode("cp1251"))
os.symlink("/etc/passwd", os.path.join(out, "ссылка.txt"))
""")
    временный = tmp_path / "tmp"
    временный.mkdir()
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.setattr(ra.tempfile, "tempdir", str(временный))
    участники, причина = ra.распаковать(СЕМЁРКА)
    assert _имена(участники) == ["spec.pdf", "sub/перечень.csv"], (участники, причина)
    assert причина == ""
    assert list(временный.iterdir()) == []


def test_ошибка_посреди_распаковки_не_оставляет_файлов(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _подделка(bin_, "7z", """
if args[0] == "l":
    sys.exit(2)
out = next(a[2:] for a in args if a.startswith("-o"))
open(os.path.join(out, "spec.pdf"), "wb").write(b"%PDF-1.4 fake")
""")
    временный = tmp_path / "tmp"
    временный.mkdir()
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.setattr(ra.tempfile, "tempdir", str(временный))

    def падает(*_a, **_k):
        raise RuntimeError("сломано")
    monkeypatch.setattr(ra, "_файлы", падает)
    участники, причина = ra.распаковать(СЕМЁРКА)
    assert участники == [] and "ошибка распаковщика" in причина
    assert list(временный.iterdir()) == []


def test_таймаут_распаковщика_назван(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _подделка(bin_, "7z", "time.sleep(15)")
    временный = tmp_path / "tmp"
    временный.mkdir()
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.setenv("ARCHIVE_TIMEOUT_S", "1")
    monkeypatch.setattr(ra.tempfile, "tempdir", str(временный))
    начало = time.monotonic()
    участники, причина = ra.распаковать(СЕМЁРКА)
    assert участники == [] and "таймаут распаковщика" in причина
    assert time.monotonic() - начало < 10
    assert list(временный.iterdir()) == []


def test_шифротекст_от_bsdtar_не_отдаётся_документом(tmp_path, monkeypatch):
    """bsdtar на зашифрованном участнике оставляет файл правильной длины с
    шифротекстом и пишет об этом в stderr — такой файл документом не считаем."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _подделка(bin_, "bsdtar", """
out = args[args.index("-C") + 1]
open(os.path.join(out, "secret.pdf"), "wb").write(b"%PDF-garbage")
open(os.path.join(out, "open.pdf"), "wb").write(b"%PDF-1.4 ok")
sys.stderr.write("secret.pdf: The file content is encrypted, but currently not supported\\n")
sys.exit(1)
""")
    monkeypatch.setenv("PATH", str(bin_))
    участники, причина = ra.распаковать(СЕМЁРКА)
    assert _имена(участники) == ["open.pdf"], (участники, причина)
    assert "зашифровано" in причина


def test_нет_кодека_rar_названо_а_не_битый_архив(tmp_path, monkeypatch):
    """7z из Ubuntu 24.04 видит RAR, но разжать не может: «Unsupported Method».
    Архив цел — не хватает пакета, и причина обязана сказать именно это."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _подделка(bin_, "7z", """
if args[0] == "l":
    sys.exit(2)
sys.stderr.write("ERROR: Unsupported Method : spec.pdf\\n")
sys.exit(2)
""")
    monkeypatch.setenv("PATH", str(bin_))
    assert ra.распаковать(b"Rar!\x1a\x07\x00" + bytes(40)) == ([], "нет кодека rar")


def test_оглавление_7z_отсекает_бомбу_до_распаковки(tmp_path, monkeypatch):
    """По оглавлению видно размеры ДО распаковки: участник больше бюджета не
    просится у 7z вовсе и не ложится на диск прогона."""
    monkeypatch.setenv("ARCHIVE_MAX_MB", "1")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    журнал = tmp_path / "журнал.txt"
    _подделка(bin_, "7z", f"""
if args[0] == "l":
    print("Path = a.7z\\nType = 7z\\n\\n----------")
    print("Path = big.txt\\nSize = 3000000\\nCRC = 00000001\\nEncrypted = -\\n")
    print("Path = kp.pdf\\nSize = 13\\nCRC = 00000002\\nEncrypted = -\\n")
    sys.exit(0)
out = next(a[2:] for a in args if a.startswith("-o"))
список = [a[1:] for a in args if a.startswith("@")]
нужно = open(список[0], encoding="utf-8").read().split() if список else ["big.txt", "kp.pdf"]
open({str(журнал)!r}, "w").write(" ".join(нужно))
if "big.txt" in нужно:
    open(os.path.join(out, "big.txt"), "wb").write(b"A" * 3000000)
if "kp.pdf" in нужно:
    open(os.path.join(out, "kp.pdf"), "wb").write(b"%PDF-1.4 fake")
""")
    monkeypatch.setenv("PATH", str(bin_))
    участники, причина = ra.распаковать(СЕМЁРКА)
    assert _имена(участники) == ["kp.pdf"], (участники, причина)
    assert "предел размера" in причина
    assert журнал.read_text() == "kp.pdf"


def test_нет_распаковщика_названо(monkeypatch):
    monkeypatch.setattr(ra.shutil, "which", lambda *_a, **_k: None)
    assert ra.распаковать(СЕМЁРКА) == ([], "нет распаковщика 7z")
    assert ra.распаковать(b"Rar!\x1a\x07\x00" + bytes(40)) == ([], "нет распаковщика rar")


# ─────────────────────── настоящие 7z и RAR, если есть ──────────────────────

СЕМЬ_ЗЭ = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
нужен_7z = pytest.mark.skipif(not СЕМЬ_ЗЭ, reason="нет 7z (apt install p7zip-full)")


def _семёрка(tmp_path, файлы, *ключи) -> bytes:
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for имя, данные in файлы:
        (src / имя).write_bytes(данные)
    архив = tmp_path / "a.7z"
    if архив.exists():
        архив.unlink()
    subprocess.run([СЕМЬ_ЗЭ, "a", "-bd", *ключи, str(архив), *[имя for имя, _ in файлы]],
                   cwd=src, check=True, capture_output=True)
    return архив.read_bytes()


@нужен_7z
def test_7z_с_вложенным_zip(tmp_path):
    b = _семёрка(tmp_path, [("Спецификация.pdf", PDF), ("КП.zip", _zip([("КП.pdf", PDF)]))])
    участники, причина = ra.распаковать(b)
    assert sorted(_имена(участники)) == sorted(["Спецификация.pdf", "КП.zip/КП.pdf"]), участники
    assert причина == ""


@нужен_7z
def test_7z_зашифрованный_участник_и_зашифрованные_заголовки(tmp_path):
    # Один архив, два участника: первый добавлен с паролем, второй — без.
    _семёрка(tmp_path, [("secret.pdf", PDF)], "-pSecret")
    (tmp_path / "src" / "open.pdf").write_bytes(PDF)
    subprocess.run([СЕМЬ_ЗЭ, "a", "-bd", str(tmp_path / "a.7z"), "open.pdf"],
                   cwd=tmp_path / "src", check=True, capture_output=True)
    участники, причина = ra.распаковать((tmp_path / "a.7z").read_bytes())
    assert _имена(участники) == ["open.pdf"], (участники, причина)
    assert "зашифровано" in причина
    b = _семёрка(tmp_path, [("secret.pdf", PDF)], "-pSecret", "-mhe=on")
    assert ra.распаковать(b) == ([], "зашифровано")


@нужен_7z
def test_zip_в_deflate64_дочитывается_через_7z(tmp_path):
    текст = ("Позиция ЗИП;1;шт\n" * 3000).encode()
    src = tmp_path / "src"
    src.mkdir()
    (src / "perechen.txt").write_bytes(текст)
    (src / "kp.pdf").write_bytes(PDF)
    архив = tmp_path / "d64.zip"
    subprocess.run([СЕМЬ_ЗЭ, "a", "-bd", "-tzip", "-mm=Deflate64", str(архив), "perechen.txt", "kp.pdf"],
                   cwd=src, check=True, capture_output=True)
    b = архив.read_bytes()
    if 9 not in {i.compress_type for i in zipfile.ZipFile(io.BytesIO(b)).infolist()}:
        pytest.skip("7z не выбрал Deflate64")
    участники, причина = ra.распаковать(b)
    assert dict(участники).get("perechen.txt") == текст, (участники, причина)
    assert "сжатие не поддержано" not in причина


def _rar_хранение(файлы) -> bytes:
    """RAR 4 с участниками без сжатия (метод 0x30), имена в cp866 — как у
    WinRAR на русской Windows. Программы rar в Ubuntu нет, собираем руками."""
    def блок(тип, флаги, тело):
        остальное = struct.pack("<BHH", тип, флаги, 7 + len(тело)) + тело
        return struct.pack("<H", zlib.crc32(остальное) & 0xFFFF) + остальное
    b = b"Rar!\x1a\x07\x00" + блок(0x73, 0, bytes(6))
    for имя, данные in файлы:
        сырое = имя.encode("cp866")
        тело = struct.pack("<IIBIIBBHI", len(данные), len(данные), 0, zlib.crc32(данные),
                           0x5A000000, 20, 0x30, len(сырое), 0x20) + сырое
        b += блок(0x74, 0x8000, тело) + данные
    return b + bytes.fromhex("C43D7B00400700")


@pytest.mark.skipif(not any(shutil.which(и) for и in ("unrar", "7z", "bsdtar")),
                    reason="нет распаковщика RAR (apt install unrar libarchive-tools)")
def test_rar_читается_и_имя_cp866_раскодировано():
    участники, причина = ra.распаковать(_rar_хранение([("Спец.pdf", PDF), ("перечень.csv", CSV)]))
    assert sorted(_имена(участники)) == sorted(["Спец.pdf", "перечень.csv"]), (участники, причина)
    assert dict(участники)["Спец.pdf"] == PDF


def test_прогон_ставит_распаковщики_которые_зовёт_модуль():
    """Код зовёт внешнюю программу — прогон обязан её поставить.

    В Ubuntu 24.04 p7zip-full — пустой переходник на 7zip без кодека RAR: сжатый
    RAR на нём даёт «Unsupported Method». Без unrar или bsdtar в прогоне модуль
    честно скажет «нет кодека rar» — и архив всё равно потеряется.

    Разбор файла прогона читает КОМАНДУ установки, а не комментарии: слово в
    пояснении проверку обмануло бы (CLAUDE.md, стиль работы).
    """
    import re
    from pathlib import Path
    from library import read_archive

    # Пакеты ставит одна точка всех прогонов разбора — .github/actions/parse-env.
    сырой = (Path(__file__).resolve().parent.parent
             / ".github/actions/parse-env/action.yml").read_text(encoding="utf-8")
    yml = re.sub(r"(?m)^\s*#[^\n]*$", "", сырой)
    шаг = yml[yml.index("Системные пакеты разбора"):]
    шаг = шаг[:шаг.index("- name:", 10)]
    команда = re.search(r'if \[ "\$PARSE" = "true" \]; then\n\s*pkgs\+=\(([^)]*)\)', шаг).group(1)
    пакет_программы = {"unrar": "unrar", "bsdtar": "libarchive-tools", "7z": "p7zip-full"}
    for программы in read_archive._ИНСТРУМЕНТЫ.values():
        for программа in программы:
            пакет = пакет_программы.get(программа)
            assert пакет and пакет in команда, \
                f"модуль зовёт {программа}, а прогон не ставит {пакет}"
