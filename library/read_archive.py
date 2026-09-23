"""Распаковка вложений-архивов до документов: ZIP, RAR, 7z, tar, gz, bz2, xz, cab.

Замер 23.09.2026: в базе 169 архивов, и все 169 без позиций. Прежний читатель
(`readers.участники_архива`) берёт только ZIP, один уровень, до 40 участников и
отбирает их по списку расширений. Мимо него уходит:

* RAR и 7z — по разведке `base/parse_archives.py` это 149 rar и 63 семёрки на
  1,6 ГБ вложений;
* tar.gz и одиночные .gz/.bz2/.xz — выгрузки с серверов;
* имена в ZIP с Windows: архиватор пишет их в cp866 без флага UTF-8, zipfile
  читает их как cp437, и «Спецификация.xlsx» превращается в «С»-мусор;
* участники без расширения или с чужим расширением — поэтому участник здесь
  отбирается по БАЙТАМ, а не по имени;
* архив в архиве — обычный случай: «КП.zip» внутри «Письмо.zip»;
* зашифрованный участник, из-за которого не читались и остальные.

Контракт: `распаковать(b)` никогда не бросает исключений и всегда убирает
временные файлы; вторым значением называет, что потеряно и почему. В причину
идут только постоянные слова этого модуля и счётчики — ни имён участников, ни
их текста (CLAUDE.md, правило 17: причина пишется в журнал прогона).
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import shutil
import signal
import stat
import struct
import subprocess
import tarfile
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

try:                                    # есть только на Unix; без него живём без
    import resource                     # предела размера файла у распаковщика
except ImportError:                     # pragma: no cover
    resource = None

#: Сколько документов отдать из одного архива со всеми вложенными. Прежний
#: предел 40 обрывал архив с фотоотчётом на сороковом снимке, и спецификация,
#: лежащая после снимков, терялась; выгрузку каталога производителя на тысячи
#: файлов 200 по-прежнему не пускают.
МАКС_УЧАСТНИКОВ = 200

#: Сколько записей одного архива осмотреть. Осмотр стоит разжатия первых 8 КБ
#: каждой записи; архив на десятки тысяч записей — бомба «много мелких», а не
#: письмо, и дальше 2000 записей не смотрим.
МАКС_ЗАПИСЕЙ = 2000

#: Жёсткий потолок вложенности, какую бы глубину ни попросили: 42.zip — это
#: пять уровней по 16 архивов, и каждый уровень множит работу в 16 раз.
ГЛУБИНА_ПРЕДЕЛ = 5

#: Сколько потоковых сжатий подряд снимать без траты уровня (tar.gz — это ОДИН
#: архив, а не два). Три слоя честному файлу хватает с запасом; gz в gz в gz
#: дальше — только бомба.
СЛОЁВ_СЖАТИЯ = 3

#: Сколько байт участника смотреть, чтобы решить, документ ли он. 8 КБ хватает
#: всем подписям (PDF ищется в первом килобайте, tar — на 257-м байте) и замеру
#: доли управляющих знаков у текста.
ГОЛОВА = 8192

МБ = 1024 * 1024

#: Пароль-пустышка для внешних распаковщиков. Без ключа -p 7z и unrar на
#: зашифрованном участнике СПРАШИВАЮТ пароль и ждут до таймаута; с неверным —
#: сразу пропускают участника и читают остальные.
ПАРОЛЬ = "kvant-bez-parolya"

АРХИВЫ = frozenset({"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "cab"})
ПОТОКИ = frozenset({"gz", "bz2", "xz"})
КАРТИНКИ = frozenset({"jpeg", "png", "gif", "tiff", "bmp", "webp", "heic"})
ДОКУМЕНТЫ = frozenset({
    "pdf", "xlsx", "docx", "pptx", "ooxml", "odt", "ods", "odp",
    "xls", "doc", "ppt", "msg", "ole", "rtf", "текст", "djvu",
}) | КАРТИНКИ

# Заметки, которые не потеря, а описание: если документы нашлись, в причину
# они не идут, иначе причина врала бы о полностью прочитанном архиве.
_НЕ_ПОТЕРИ = ("не документ", "пустой участник", "пустой архив")

# Подписи, опознаваемые по началу файла. Порядок важен только для OLE2: его
# подвид (xls, doc, msg) уточняется отдельно.
_ПОДПИСИ = (
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),
    (b"{\\rtf", "rtf"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"AT&TFORM", "djvu"),
    (b"Rar!\x1a\x07", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"\x1f\x8b\x08", "gz"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"MSCF\x00\x00\x00\x00", "cab"),
)

# Расширение, которое дописывается участнику БЕЗ расширения: разбор в
# base/parse_archives.py и сторонние читатели выбирают ветку по имени.
_КАНОН = {
    "pdf": ".pdf", "xlsx": ".xlsx", "docx": ".docx", "pptx": ".pptx",
    "odt": ".odt", "ods": ".ods", "odp": ".odp",
    "xls": ".xls", "doc": ".doc", "ppt": ".ppt", "msg": ".msg",
    "rtf": ".rtf", "текст": ".txt", "djvu": ".djvu",
    "jpeg": ".jpg", "png": ".png", "gif": ".gif", "tiff": ".tif",
    "bmp": ".bmp", "webp": ".webp", "heic": ".heic",
}

# Имя с таким расширением не трогаем, даже если байты говорят другое: «.xls»,
# который на деле XML-выгрузка 1С, читатель узнаёт сам, а дописанное «.txt»
# увело бы его в чужую ветку.
_РАСШИРЕНИЯ = frozenset({
    ".pdf", ".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm", ".xls", ".xlt",
    ".docx", ".docm", ".dotx", ".dotm", ".doc", ".dot",
    ".pptx", ".pptm", ".ppsx", ".ppt", ".pps", ".odt", ".ods", ".odp",
    ".rtf", ".txt", ".csv", ".tsv", ".xml", ".html", ".htm", ".json", ".eml",
    ".msg", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".webp",
    ".heic", ".djvu", ".djv",
})

# Сжатия, которые zipfile разжимает сам: хранение, deflate, bzip2, lzma.
# Deflate64 (9) пишет Проводник Windows на больших архивах, PPMd (98) — WinZip и
# 7-Zip; их отдаём внешнему 7z.
_СЖАТИЯ_ZIP = frozenset({0, 8, 12, 14})

# Каким распаковщиком брать формат, по порядку. RAR первым отдаётся unrar:
# 7z из Ubuntu 24.04 (пакет 7zip, сборка dfsg) формат RAR видит, но кодека
# разжатия в нём нет — «Unsupported Method» на всём, кроме хранения.
_ИНСТРУМЕНТЫ = {
    "rar": ("unrar", "7z", "bsdtar"),
    "7z": ("7z", "bsdtar"),
    "cab": ("7z", "bsdtar"),
    "zip": ("7z",),
}

_СЛУЖЕБНЫЕ = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})


# ─────────────────────────────── вид по байтам ───────────────────────────────

def вид(b: bytes) -> str:
    """Что лежит в байтах: «pdf», «xlsx», «ole», «текст», «zip», «rar» … или «».

    Смотрит только байты. Имя участника врёт слишком часто: у писем с Windows
    оно в cp866, у выгрузок его нет вовсе, у 1С «.xls» — это XML.
    """
    try:
        return _вид(b)
    except Exception:                                   # noqa: BLE001
        return ""


def это_архив(b: bytes) -> bool:
    """Архив ли это. Книга xlsx и документ docx — НЕ архив, хотя тоже PK.

    Именно на этом споткнулся прежний отбор: настоящий архив опознавался
    книгой, падал в openpyxl и ложился как «пусто» (169 файлов из 169).
    """
    return вид(b) in АРХИВЫ


def _вид(b: bytes) -> str:
    if not b:
        return ""
    if b[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return _вид_zip(b)
    if b.startswith(b"%PDF-"):
        return "pdf"
    for подпись, имя in _ПОДПИСИ:
        if b.startswith(подпись):
            return _вид_ole(b) if имя == "ole" else имя
    if b[:3] == b"BZh" and b[3:4] in b"123456789" and b[4:10] in (b"1AY&SY", b"\x17rE8P\x90"):
        return "bz2"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[4:8] == b"ftyp" and b[8:12] in (b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1"):
        return "heic"
    if _похоже_на_bmp(b):
        return "bmp"
    # tar проверяется ДО свободного поиска «%PDF-»: у tar с PDF первым
    # участником подпись PDF стоит на 512-м байте, и архив опознался бы файлом.
    if _похоже_на_tar(b):
        return "tar"
    # PDF по стандарту может начинаться не с первого байта: почтовые шлюзы и
    # сканеры кладут мусор перед заголовком, читатели ищут его в первом КБ.
    if b"%PDF-" in b[:1024]:
        return "pdf"
    if _похоже_на_текст(b[:ГОЛОВА]):
        return "текст"
    return ""


def _вид_zip(b: bytes) -> str:
    """PK-контейнер: книга, документ, ODF или просто архив."""
    голова = b[:256]
    # ODF обязан класть несжатый mimetype первым участником, поэтому он виден в
    # голове и без разбора каталога.
    if b"mimetype" in голова and b"application/vnd.oasis.opendocument." in голова:
        for хвост, к in ((b"text", "odt"), (b"spreadsheet", "ods"), (b"presentation", "odp")):
            if b"opendocument." + хвост in голова:
                return к
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            имена = z.namelist()
    except Exception:                                   # noqa: BLE001
        # Обрезанный или только голова: имена берём из локальных заголовков.
        имена = [сырое.decode("latin-1") for сырое in _имена_локальные(b[:65536])]
    if any(n.startswith("xl/") for n in имена):
        return "xlsx"
    if any(n.startswith("word/") for n in имена):
        return "docx"
    if any(n.startswith("ppt/") for n in имена):
        return "pptx"
    if "[Content_Types].xml" in имена:
        return "ooxml"
    return "zip"


def _имена_локальные(b: bytes) -> list[bytes]:
    out: list[bytes] = []
    поз = 0
    while len(out) < 50:
        j = b.find(b"PK\x03\x04", поз)
        if j < 0 or j + 30 > len(b):
            break
        дл = struct.unpack_from("<H", b, j + 26)[0]
        out.append(b[j + 30:j + 30 + дл])
        поз = j + 30
    return out


def _вид_ole(b: bytes) -> str:
    """xls, doc, ppt или msg: у всех одна подпись OLE2, различаются потоками.

    Имена потоков лежат в каталоге UTF-16LE. Главный поток создаётся первым и
    стоит в каталоге раньше вложенных объектов (Excel-таблица внутри Word живёт
    в ObjectPool дальше), поэтому берём самое раннее вхождение.
    """
    метки = (
        ("__substg1.0_", "msg"),
        ("Workbook", "xls"),
        ("Book\x00", "xls"),
        ("WordDocument", "doc"),
        ("PowerPoint Document", "ppt"),
    )
    лучшее, к = len(b) + 1, "ole"
    for метка, имя in метки:
        j = b.find(метка.encode("utf-16-le"))
        if 0 <= j < лучшее:
            лучшее, к = j, имя
    return к


def _похоже_на_bmp(b: bytes) -> bool:
    # «BM» — всего два байта, их много в тексте; проверяем ещё нулевой резерв и
    # размер заголовка DIB, который бывает ровно восьми видов.
    return (len(b) >= 26 and b[:2] == b"BM" and b[6:10] == b"\x00\x00\x00\x00"
            and int.from_bytes(b[14:18], "little") in (12, 16, 40, 52, 56, 64, 108, 124))


def _похоже_на_tar(b: bytes) -> bool:
    """Заголовок tar по контрольной сумме: подпись «ustar» есть не у всех.

    Старые tar (v7) подписи не имеют, а по одной «ustar» на 257-м байте текст
    случайно сошёлся бы; контрольная сумма заголовка отсекает и то и другое.
    """
    if len(b) < 512:
        return False
    h = b[:512]
    поле = h[148:156].replace(b"\x00", b" ").strip()
    if not поле:
        return False
    try:
        записано = int(поле, 8)
    except ValueError:
        return False
    сумма = sum(h[:148]) + 32 * 8 + sum(h[156:512])
    return записано == сумма and (h[257:262] == b"ustar" or h[0] != 0)


_УПРАВЛЯЮЩИЕ = bytes(x for x in range(32) if x not in (9, 10, 12, 13, 26, 27))


def _похоже_на_текст(г: bytes) -> bool:
    """Текст — это csv, txt, html, xml и выгрузки 1С в любой кодировке.

    Кириллица в cp1251 и cp866 занимает верхнюю половину байтов, поэтому
    проверяем не «печатаемость», а отсутствие нулей и управляющих: у сжатых и
    двоичных данных управляющий знак — почти каждый десятый байт (26 из 256), у
    текста их нет. Порог 1 % оставляет место случайному \\x0b в выгрузке.
    """
    if not г:
        return False
    if г.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in г:
        return False
    плохих = len(г) - len(г.translate(None, _УПРАВЛЯЮЩИЕ))
    return плохих * 100 <= len(г)


# ───────────────────────────────── имена ─────────────────────────────────────

def _оценка(s: str) -> int:
    """Насколько строка похожа на имя файла: кириллица и латиница в плюс,
    псевдографика и знаки чужих алфавитов в минус."""
    очки = 0
    for ch in s:
        if "а" <= ch <= "я" or "А" <= ch <= "Я" or ch in "ёЁ№":
            очки += 2
        elif ch.isascii() and (ch.isalnum() or ch in " ._-()[]{},+&'!#@=~/\\%$;"):
            очки += 1
        else:
            очки -= 2
    return очки


def _лучшее_имя(сырое: bytes) -> str:
    """Имя из байтов неизвестной кодировки.

    Без флага UTF-8 архиваторы Windows пишут имя в кодировке OEM — у русской
    Windows это cp866; macOS пишет UTF-8, но флаг не ставит; редкие программы
    пишут cp1251. Пробуем все три и берём самое похожее на имя; при равенстве
    побеждает UTF-8, затем cp866 — он и есть случай по умолчанию.
    """
    кандидаты = []
    try:
        кандидаты.append(сырое.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    кандидаты.append(сырое.decode("cp866", "replace"))
    кандидаты.append(сырое.decode("cp1251", "replace"))
    return max(кандидаты, key=_оценка)


def _юникод_из_доп(доп: bytes, сырое: bytes) -> str:
    """Имя из поля Info-ZIP Unicode Path (0x7075): его пишут WinRAR и 7-Zip
    рядом с именем в cp866. Поле верно, только если его CRC сходится с
    основным именем, — иначе имя правили программой, не знающей о поле."""
    i = 0
    while i + 4 <= len(доп):
        тег, размер = struct.unpack_from("<HH", доп, i)
        тело = доп[i + 4:i + 4 + размер]
        if тег == 0x7075 and len(тело) >= 5 and тело[0] == 1:
            if struct.unpack_from("<I", тело, 1)[0] == zlib.crc32(сырое):
                try:
                    return тело[5:].decode("utf-8")
                except UnicodeDecodeError:
                    return ""
        i += 4 + размер
    return ""


def _имя_zip(info: zipfile.ZipInfo) -> str:
    # Флаг 0x800 — имя в UTF-8, zipfile его уже раскодировал верно. Иначе он
    # раскодировал его как cp437, а это обратимо: cp437 покрывает все 256
    # байтов, и исходные байты возвращаются без потерь.
    if info.flag_bits & 0x800:
        return info.filename
    исходное = getattr(info, "orig_filename", info.filename)
    try:
        сырое = исходное.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    return _юникод_из_доп(info.extra, сырое) or _лучшее_имя(сырое)


def _имя_из_фс(отн: str) -> str:
    # Распаковщик кладёт имя на диск теми байтами, что были в архиве; не-UTF-8
    # байты Python отдаёт суррогатами, возвращаем байты и выбираем кодировку.
    return _лучшее_имя(os.fsencode(отн))


def _имя_tar(имя: str) -> str:
    if any("\udc80" <= ch <= "\udcff" for ch in имя):
        return _лучшее_имя(имя.encode("utf-8", "surrogateescape"))
    return имя


def _служебное(имя: str) -> bool:
    """Мусор архиваторов: копии ресурсов macOS и эскизы Проводника.

    Thumbs.db — тот же OLE2, что и .xls, и без этого отбора уходил бы в
    читатель книг."""
    части = имя.replace("\\", "/").split("/")
    база = части[-1].lower()
    return "__MACOSX" in части or база.startswith("._") or база in _СЛУЖЕБНЫЕ


def _путь(префикс: str, имя: str) -> str:
    имя = имя.replace("\\", "/").lstrip("/")
    while имя.startswith("./"):
        имя = имя[2:]
    return f"{префикс}/{имя}" if префикс else имя


def _с_расширением(имя: str, к: str) -> str:
    if PurePosixPath(имя).suffix.lower() in _РАСШИРЕНИЯ:
        return имя
    канон = _КАНОН.get(к)
    return имя + канон if канон else имя


def _имя_gz(b: bytes) -> bytes:
    """Исходное имя из заголовка gzip (поле FNAME), если упаковщик его сохранил."""
    if len(b) < 10 or not b[3] & 0x08:
        return b""
    i = 10
    if b[3] & 0x04:
        i = 12 + struct.unpack_from("<H", b, 10)[0]
    конец = b.find(b"\x00", i)
    return b[i:конец] if конец > i else b""


_СУФФИКСЫ_СЖАТИЯ = (".gz", ".gzip", ".bz2", ".xz", ".tgz", ".tbz2", ".txz")


def _имя_потока(b: bytes, к: str, префикс: str) -> str:
    база = ""
    if к == "gz":
        сырое = _имя_gz(b)
        if сырое:
            база = _лучшее_имя(сырое).replace("\\", "/").rsplit("/", 1)[-1]
    if not база and префикс:
        база = префикс.rsplit("/", 1)[-1]
        for суффикс in _СУФФИКСЫ_СЖАТИЯ:
            if база.lower().endswith(суффикс) and len(база) > len(суффикс):
                база = база[:-len(суффикс)]
                break
    return _путь(префикс, база or "содержимое")


# ─────────────────────────────── окружение ───────────────────────────────────

def _предел_байт() -> int:
    """Сколько всего можно распаковать из одного архива (ARCHIVE_MAX_MB, 500).

    500 МБ — в сорок раз больше среднего архива разведки (901 zip на 11,1 ГБ,
    по 12 МБ) и в миллионы раз меньше того, во что разворачивается 42.zip
    (4,5 ПБ из 42 КБ)."""
    try:
        мб = float(os.environ.get("ARCHIVE_MAX_MB") or 500)
    except ValueError:
        мб = 500.0
    return max(0, int(мб * МБ))


def _таймаут() -> float:
    """Сколько ждать внешний распаковщик (ARCHIVE_TIMEOUT_S, 120 с): 7z
    разжимает сотню мегабайт за секунды, минуты — это бомба или зависание на
    запросе следующего тома."""
    try:
        return max(1.0, float(os.environ.get("ARCHIVE_TIMEOUT_S") or 120))
    except ValueError:
        return 120.0


def _бинарь(имя: str) -> str | None:
    if имя == "7z":
        for вариант in ("7z", "7zz", "7za"):
            путь = shutil.which(вариант)
            if путь:
                return путь
        return None
    return shutil.which(имя)


def _команда(инструмент: str, путь: str, вход: Path, выход: Path, список: Path | None) -> list[str]:
    if инструмент == "7z":
        к = [путь, "x", "-y", "-bd", "-bb0", "-sccUTF-8", "-scsUTF-8", f"-p{ПАРОЛЬ}", f"-o{выход}", str(вход)]
        if список is not None:
            к.append(f"@{список}")
        return к
    if инструмент == "unrar":
        return [путь, "x", "-y", "-idq", "-o+", f"-p{ПАРОЛЬ}", str(вход), str(выход) + os.sep]
    return [путь, "-x", "-f", str(вход), "-C", str(выход), "--passphrase", ПАРОЛЬ]


def _файлы(каталог: Path):
    """Обычные файлы распакованного дерева по порядку. Ссылки не читаются:
    символическая ссылка из архива может указывать на /etc/passwd."""
    for корень, папки, имена in os.walk(каталог, followlinks=False):
        папки.sort()
        for имя in sorted(имена):
            путь = Path(корень) / имя
            try:
                сведения = os.lstat(путь)
            except OSError:
                continue
            if not stat.S_ISREG(сведения.st_mode):
                continue
            if not сведения.st_mode & stat.S_IRUSR:
                try:                                    # 7z восстанавливает права 000
                    os.chmod(путь, 0o600)
                except OSError:
                    continue
            yield путь, os.path.relpath(путь, каталог), сведения.st_size


def _о_шифровании(текст: str) -> bool:
    низ = текст.lower()
    return "password" in низ or "encrypt" in низ


# ─────────────────────────────── сборщик ─────────────────────────────────────

class _Сбор:
    """Состояние одного вызова: найденное, заметки о потерях, остаток бюджета.

    Бюджет общий на все уровни вложенности: архив внутри архива тратит его
    дважды — своими байтами и байтами участников, — иначе 42.zip прошёл бы.
    """

    def __init__(self, предел: int, таймаут: float):
        self.участники: list[tuple[str, bytes]] = []
        self.заметки: dict[str, int] = {}
        self.остаток = предел
        self.таймаут = таймаут

    def отметить(self, что: str, сколько: int = 1) -> None:
        self.заметки[что] = self.заметки.get(что, 0) + сколько

    def полно(self) -> bool:
        return len(self.участники) >= МАКС_УЧАСТНИКОВ

    def итог(self) -> tuple[list[tuple[str, bytes]], str]:
        заметки = dict(self.заметки)
        if self.участники:
            for к in _НЕ_ПОТЕРИ:
                заметки.pop(к, None)
        причина = "; ".join(к if n == 1 else f"{к}: {n}" for к, n in заметки.items())
        if not self.участники and not причина:
            причина = "нет документов"
        return list(self.участники), причина

    # Участник читается в два шага: голова 8 КБ, и только если она похожа на
    # документ или архив — остальное. Фото на 30 МБ и чертёж .dwg тогда не
    # тратят бюджет распаковки.
    def взять(self, имя: str, размер: int, открыть, осталось: int) -> None:
        try:
            with открыть() as f:
                голова = f.read(ГОЛОВА)
                if not голова:
                    self.отметить("пустой участник")
                    return
                if not вид(голова):
                    self.отметить("не документ")
                    return
                if размер > self.остаток or len(голова) > self.остаток:
                    self.отметить("предел размера")
                    return
                хвост = f.read(self.остаток - len(голова) + 1)
        except Exception:                               # noqa: BLE001
            self.отметить("битый участник")
            return
        данные = голова + хвост
        if len(данные) > self.остаток:                  # заголовок соврал о размере
            self.отметить("предел размера")
            return
        self.остаток -= len(данные)
        self.разобрать(имя, данные, осталось)

    def разобрать(self, имя: str, данные: bytes, осталось: int) -> None:
        к = вид(данные)
        if к in АРХИВЫ:
            if осталось <= 0:
                self.отметить("предел глубины")
                return
            try:
                self.раскрыть(данные, к, имя, осталось - 1)
            except Exception:                           # noqa: BLE001
                self.отметить("битый архив")
        elif к in ДОКУМЕНТЫ:
            if self.полно():
                self.отметить("предел числа участников")
                return
            self.участники.append((_с_расширением(имя, к), данные))
        else:
            self.отметить("не документ")

    def раскрыть(self, b: bytes, к: str, префикс: str, осталось: int, слой: int = 0) -> None:
        if к == "zip":
            self.zip(b, префикс, осталось)
        elif к == "tar":
            self.tar(b, префикс, осталось)
        elif к in ПОТОКИ:
            self.поток(b, к, префикс, осталось, слой)
        else:
            self.внешний(b, к, префикс, осталось)

    # ── ZIP ──
    def zip(self, b: bytes, префикс: str, осталось: int) -> None:
        try:
            z = zipfile.ZipFile(io.BytesIO(b))
        except Exception:                               # noqa: BLE001
            self.zip_по_заголовкам(b, префикс, осталось)
            return
        with z:
            записи = [i for i in z.infolist() if not i.is_dir()]
            if not записи:
                self.отметить("пустой архив")
                return
            неподдержанные: list[tuple[zipfile.ZipInfo, str]] = []
            for n, info in enumerate(записи):
                if n >= МАКС_ЗАПИСЕЙ:
                    self.отметить("предел записей")
                    break
                if self.полно():
                    self.отметить("предел числа участников")
                    break
                имя = _имя_zip(info)
                if _служебное(имя):
                    continue
                if info.flag_bits & 0x1:
                    self.отметить("зашифровано")
                    continue
                if info.compress_type not in _СЖАТИЯ_ZIP:
                    неподдержанные.append((info, имя))
                    continue
                self.взять(_путь(префикс, имя), info.file_size, lambda i=info: z.open(i), осталось)
        if неподдержанные:
            self.zip_внешне(b, неподдержанные, префикс, осталось)

    def zip_внешне(self, b: bytes, неподдержанные, префикс: str, осталось: int) -> None:
        """Участники в Deflate64 и PPMd: их разжимает 7z, а совпадение
        распакованного файла с записью ищется по CRC и размеру — имена 7z
        раскодирует по-своему, а CRC у них общий."""
        if _бинарь("7z") is None:
            self.отметить("сжатие не поддержано", len(неподдержанные))
            return
        отбор = {(info.CRC, info.file_size): _путь(префикс, имя) for info, имя in неподдержанные}
        найдено = self.внешний(b, "zip", префикс, осталось, отбор=отбор)
        if найдено < len(отбор):
            self.отметить("сжатие не поддержано", len(отбор) - найдено)

    def zip_по_заголовкам(self, b: bytes, префикс: str, осталось: int) -> None:
        """ZIP без каталога в хвосте: обрезанное вложение или склейка.

        zipfile читает только через центральный каталог в конце файла, и
        обрезанный на последних килобайтах архив для него пуст целиком. Но
        перед каждым участником стоит свой локальный заголовок — идём по ним.
        """
        поз, найдено = 0, 0
        while найдено < МАКС_ЗАПИСЕЙ:
            j = b.find(b"PK\x03\x04", поз)
            if j < 0 or j + 30 > len(b):
                break
            (_, _, флаги, метод, _, _, _, сжато, _, дл_имени, дл_доп) = struct.unpack_from("<IHHHHHIIIHH", b, j)
            начало = j + 30 + дл_имени + дл_доп
            if начало > len(b):
                break
            сырое = b[j + 30:j + 30 + дл_имени]
            доп = b[j + 30 + дл_имени:начало]
            поз = начало
            if флаги & 0x800:
                имя = сырое.decode("utf-8", "replace")
            else:
                имя = _юникод_из_доп(доп, сырое) or _лучшее_имя(сырое)
            if имя.endswith(("/", "\\")):
                continue
            найдено += 1
            if self.полно():
                self.отметить("предел числа участников")
                break
            if _служебное(имя):
                continue
            известен = not флаги & 0x08 and сжато != 0xFFFFFFFF
            if флаги & 0x1:
                self.отметить("зашифровано")
                if известен:
                    поз = начало + сжато
                continue
            if метод == 0:
                if not известен:
                    self.отметить("битый участник")
                    continue
                данные = b[начало:начало + сжато]
                обрезан = len(данные) < сжато
                поз = начало + len(данные)
            elif метод == 8:
                вход = b[начало:начало + сжато] if известен and сжато else b[начало:]
                d = zlib.decompressobj(-15)
                try:
                    данные = d.decompress(вход, self.остаток + 1)
                except zlib.error:
                    self.отметить("битый участник")
                    continue
                if len(данные) > self.остаток:
                    self.отметить("предел размера")
                    continue
                поз = начало + len(вход) - len(d.unconsumed_tail) - len(d.unused_data)
                обрезан = not d.eof
            else:
                self.отметить("сжатие не поддержано")
                continue
            if обрезан:
                self.отметить("обрезанный участник")
            if not данные:
                self.отметить("пустой участник")
                continue
            self.взять(_путь(префикс, имя), len(данные), lambda d=данные: io.BytesIO(d), осталось)
        if найдено == 0:
            self.отметить("битый архив")

    # ── tar ──
    def tar(self, b: bytes, префикс: str, осталось: int) -> None:
        try:
            архив = tarfile.open(fileobj=io.BytesIO(b), mode="r:", encoding="utf-8", errors="surrogateescape")
        except Exception:                               # noqa: BLE001
            self.отметить("битый архив")
            return
        with архив:
            записей = 0
            итер = iter(архив)
            while True:
                try:
                    m = next(итер)
                except StopIteration:
                    break
                except Exception:                       # noqa: BLE001
                    self.отметить("битый архив")        # обрезан посреди заголовка
                    break
                if not m.isfile():                      # ссылки и устройства — мимо
                    continue
                if записей >= МАКС_ЗАПИСЕЙ:
                    self.отметить("предел записей")
                    break
                if self.полно():
                    self.отметить("предел числа участников")
                    break
                записей += 1
                имя = _имя_tar(m.name)
                if _служебное(имя):
                    continue
                self.взять(_путь(префикс, имя), m.size, lambda m=m: архив.extractfile(m), осталось)
            if записей == 0:
                self.отметить("пустой архив")

    # ── gz, bz2, xz ──
    def поток(self, b: bytes, к: str, префикс: str, осталось: int, слой: int) -> None:
        """Потоковое сжатие одного файла. Внутри обычно tar (tar.gz — один
        архив, поэтому уровень вложенности не тратится) или сам документ."""
        if слой >= СЛОЁВ_СЖАТИЯ:
            self.отметить("предел глубины")
            return
        if к == "gz":
            открыть = lambda: gzip.GzipFile(fileobj=io.BytesIO(b))  # noqa: E731
        elif к == "bz2":
            открыть = lambda: bz2.BZ2File(io.BytesIO(b))  # noqa: E731
        else:
            открыть = lambda: lzma.LZMAFile(io.BytesIO(b))  # noqa: E731
        куски: list[bytes] = []
        всего, обрезан = 0, False
        try:
            with открыть() as f:
                # Читаем кусками по 1 МБ и не дальше остатка бюджета: у gzip
                # степень сжатия нулей — 1000:1, и gzip.decompress() целиком
                # развернул бы 10-мегабайтный файл в 10 ГБ памяти.
                while всего <= self.остаток:
                    кусок = f.read(min(МБ, self.остаток - всего + 1))
                    if not кусок:
                        break
                    куски.append(кусок)
                    всего += len(кусок)
        except Exception:                               # noqa: BLE001
            if not куски:
                self.отметить("битый архив")
                return
            обрезан = True                              # оборван посреди потока
        if всего > self.остаток:
            self.отметить("предел размера")
            return
        данные = b"".join(куски)
        if обрезан:
            self.отметить("обрезанный архив")
        if not данные:
            self.отметить("пустой архив")
            return
        self.остаток -= len(данные)
        внутри = вид(данные)
        if внутри in ПОТОКИ:
            self.поток(данные, внутри, префикс, осталось, слой + 1)
        elif внутри in АРХИВЫ:
            self.раскрыть(данные, внутри, префикс, осталось, слой + 1)
        else:
            self.разобрать(_имя_потока(b, к, префикс), данные, осталось)

    # ── RAR, 7z, cab и нечитаемые stdlib участники ZIP ──
    def внешний(self, b: bytes, к: str, префикс: str, осталось: int, отбор: dict | None = None) -> int:
        """Распаковка внешней программой во временный каталог.

        Возвращает, сколько файлов из `отбор` найдено (для ZIP-участников в
        Deflate64); без отбора — ноль. Каталог удаляется при любом исходе,
        включая исключение и таймаут.
        """
        инструменты = [(и, п) for и in _ИНСТРУМЕНТЫ[к] if (п := _бинарь(и))]
        if not инструменты:
            self.отметить(f"нет распаковщика {к}")
            return 0
        if self.остаток <= 0:
            self.отметить("предел размера")
            return 0
        try:
            with tempfile.TemporaryDirectory(prefix="kvant-archive-", ignore_cleanup_errors=True) as каталог:
                return self._внешний_в(Path(каталог), b, к, префикс, осталось, отбор, инструменты)
        except Exception:                               # noqa: BLE001
            self.отметить("ошибка распаковщика")
            return 0

    def _внешний_в(self, каталог: Path, b: bytes, к: str, префикс: str, осталось: int,
                   отбор: dict | None, инструменты: list[tuple[str, str]]) -> int:
        вход = каталог / f"архив.{к}"
        вход.write_bytes(b)
        список: Path | None = None
        ожидается: int | None = None
        зашифрованные: set[str] = set()

        # Оглавление снимает 7z — он читает заголовки RAR даже без кодека
        # разжатия. По нему видно зашифрованных участников и размеры ДО
        # распаковки: бомба отсекается, не коснувшись диска.
        p7 = _бинарь("7z")
        оглавление = self._оглавление(p7, вход) if p7 else None
        if оглавление == "зашифрован":
            self.отметить("зашифровано")
            return 0
        if isinstance(оглавление, list):
            файлы = [з for з in оглавление if not з["каталог"]]
            if not файлы:
                self.отметить("пустой архив")
                return 0
            зашифрованные = {з["путь"] for з in файлы if з["зашифрован"]}
            if зашифрованные and отбор is None:
                self.отметить("зашифровано", len(зашифрованные))
            кандидаты = [з for з in файлы if not з["зашифрован"] and not _служебное(з["путь"])]
            if отбор is not None:
                кандидаты = [з for з in кандидаты if (з["crc"], з["размер"]) in отбор]
            выбор: list[dict] = []
            всего = 0
            for з in кандидаты:
                if len(выбор) >= МАКС_ЗАПИСЕЙ:
                    self.отметить("предел записей")
                    break
                if всего + з["размер"] > self.остаток:
                    self.отметить("предел размера")
                    continue
                выбор.append(з)
                всего += з["размер"]
            if not выбор:
                return 0
            if отбор is not None or len(выбор) < len(кандидаты):
                список = каталог / "список.txt"
                список.write_text("\n".join(з["путь"] for з in выбор) + "\n", encoding="utf-8")
            ожидается = len(выбор)

        лучший: tuple[Path, int] | None = None
        таймаут = успех = без_кодека = False
        for n, (инструмент, путь) in enumerate(инструменты):
            выход = каталог / f"out{n}"
            выход.mkdir()
            код, ошибки = self._запустить(
                _команда(инструмент, путь, вход, выход, список if инструмент == "7z" else None))
            if код is None:
                # Бомба, которая не уложилась в минуты у 7z, не уложится и у
                # bsdtar: второй таймаут подряд — просто вдвое дольше.
                таймаут = True
                self.отметить("таймаут распаковщика")
                break
            if код == -signal.SIGXFSZ:
                self.отметить("предел размера")
            if _о_шифровании(ошибки):
                # bsdtar оставляет на месте зашифрованного участника файл с
                # шифротекстом правильной длины — его имя берём из сообщения.
                for строка in ошибки.splitlines():
                    if ": " in строка and _о_шифровании(строка):
                        зашифрованные.add(строка.split(": ", 1)[0].strip())
            # «Unsupported Method» — 7z без кодека RAR (сборка dfsg в Ubuntu):
            # архив цел, не хватает пакета, и причина должна это сказать.
            без_кодека = без_кодека or "unsupported method" in ошибки.lower()
            число = sum(1 for _ in _файлы(выход))
            if лучший is None or число > лучший[1]:
                лучший = (выход, число)
            if код == 0 or (ожидается is not None and число >= ожидается):
                успех = True
                break

        if лучший is None or лучший[1] == 0:
            if таймаут:
                pass
            elif без_кодека:
                self.отметить(f"нет кодека {к}")
            elif зашифрованные:
                if оглавление is None:
                    self.отметить("зашифровано")
            else:
                self.отметить("битый архив" if отбор is None else "ошибка распаковщика")
            return 0
        if оглавление is None and зашифрованные:
            self.отметить("зашифровано", len(зашифрованные))
        if not успех and not таймаут and not зашифрованные:
            self.отметить("распаковано не полностью")

        найдено = 0
        for путь, отн, размер in _файлы(лучший[0]):
            if отн in зашифрованные:
                continue
            if self.полно():
                self.отметить("предел числа участников")
                break
            if отбор is not None:
                try:
                    данные = путь.read_bytes()
                except OSError:
                    continue
                имя = отбор.get((zlib.crc32(данные), len(данные)))
                if имя is None:
                    continue
                найдено += 1
                self.взять(имя, len(данные), lambda d=данные: io.BytesIO(d), осталось)
                continue
            имя = _имя_из_фс(отн)
            if _служебное(имя):
                continue
            self.взять(_путь(префикс, имя), размер, lambda p=путь: open(p, "rb"), осталось)
        return найдено

    def _оглавление(self, p7: str, вход: Path):
        """Список записей по `7z l -slt`: путь, размер, CRC, каталог, шифр.

        None — оглавления нет (7z не справился), «зашифрован» — зашифрованы
        сами заголовки (7z -mhe, rar -hp): без пароля не видно даже имён.
        """
        код, вывод, ошибки = self._запустить(
            [p7, "l", "-slt", "-sccUTF-8", f"-p{ПАРОЛЬ}", str(вход)], с_выводом=True)
        if код is None:
            return None
        текст = вывод.decode("utf-8", "replace").replace("\r\n", "\n")
        if код != 0:
            return "зашифрован" if _о_шифровании(текст + ошибки) else None
        _, разделитель, хвост = текст.partition("\n----------\n")
        if not разделитель:
            return []
        записи = []
        for блок in хвост.split("\n\n"):
            поля = dict(с.split(" = ", 1) for с in блок.splitlines() if " = " in с)
            if "Path" not in поля:
                continue
            try:
                размер = int(поля.get("Size") or 0)
            except ValueError:
                размер = 0
            try:
                crc = int(поля["CRC"], 16) if поля.get("CRC") else None
            except ValueError:
                crc = None
            записи.append({
                "путь": поля["Path"], "размер": размер, "crc": crc,
                "каталог": поля.get("Folder") == "+" or поля.get("Attributes", "").startswith("D"),
                "зашифрован": поля.get("Encrypted") == "+",
            })
        return записи

    def _запустить(self, команда: list[str], с_выводом: bool = False):
        """Запуск с таймаутом и пределом размера файла; (код, вывод?, ошибки).

        Код None — таймаут. Предел размера одного файла (RLIMIT_FSIZE) равен
        остатку бюджета: распаковщик, пишущий больше, получает SIGXFSZ, а не
        заполняет диск прогона. Ставится после запуска через prlimit, а не в
        preexec_fn: индексатор работает потоками, а preexec_fn при потоках
        может подвесить дочерний процесс до exec.
        """
        окружение = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
        try:
            proc = subprocess.Popen(команда, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=окружение, start_new_session=True)
        except OSError:
            return (-1, b"", "") if с_выводом else (-1, "")
        if resource is not None and hasattr(resource, "prlimit"):
            предел = max(1, self.остаток + 1)
            try:
                resource.prlimit(proc.pid, resource.RLIMIT_FSIZE, (предел, предел))
            except (OSError, ValueError):
                pass
        try:
            вывод, ошибки = proc.communicate(timeout=self.таймаут)
        except subprocess.TimeoutExpired:
            # Группа процессов целиком: /usr/bin/7z в Ubuntu — обёртка-скрипт,
            # и убийство одной обёртки оставило бы сам 7z работать дальше.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.communicate()
            return (None, b"", "") if с_выводом else (None, "")
        текст_ошибок = ошибки.decode("utf-8", "replace")[:20000]
        if с_выводом:
            return proc.returncode, вывод, текст_ошибок
        return proc.returncode, текст_ошибок


# ─────────────────────────────── вход ────────────────────────────────────────

def распаковать(b: bytes, глубина: int = 2) -> tuple[list[tuple[str, bytes]], str]:
    """Все документы архива, со вложенными архивами до `глубина` уровней.

    Возвращает ([(имя, байты)], причина). Имя — путь внутри архива, у
    вложенных через «/»: «КП.zip/Спецификация.xlsx»; участнику без расширения
    дописывается расширение по байтам. Причина пуста, если прочитано всё, и
    называет потерю («зашифровано: 2», «нет распаковщика rar», «предел
    размера»), если что-то не прочитано, — даже когда документы нашлись.
    Если документов нет, причина не пуста никогда.

    Документ отбирается по байтам: PDF, книги и документы OOXML/ODF, OLE2 (xls,
    doc, msg), RTF, картинки (для распознавания), DjVu и текст. Глубина 2 —
    это архив и архивы внутри него; tar.gz считается одним уровнем.
    Пределы: ARCHIVE_MAX_MB (500) на всю распаковку, 200 документов, 5 уровней.
    """
    сбор = _Сбор(_предел_байт(), _таймаут())
    try:
        глубина = max(1, min(int(глубина), ГЛУБИНА_ПРЕДЕЛ))
        данные = bytes(b) if b else b""
        if not данные:
            return [], "пустой файл"
        к = вид(данные)
        if к not in АРХИВЫ:
            return [], "не архив"
        сбор.раскрыть(данные, к, "", глубина - 1)
    except Exception:                                   # noqa: BLE001
        сбор.отметить("ошибка разбора")
    return сбор.итог()
