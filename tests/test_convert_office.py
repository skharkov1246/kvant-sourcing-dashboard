"""Последний рубеж чтения — LibreOffice headless (library/convert_office.py).

Логика (выбор расширения, строка запуска, таймаут, отсутствие бинарника,
промах приложения, порядок листов) проверяется подменой subprocess.Popen:
LibreOffice для этого не нужен. Настоящая конвертация — только если soffice
есть и открывает файлы: без модулей Writer/Calc он есть, но не читает ничего.

Корпуса придуманы здесь же (CLAUDE.md, правило 18): книги, документы и
презентации пишутся плоским ODF и конвертируются самим LibreOffice.
"""
from __future__ import annotations

import io
import os
import subprocess
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from library import convert_office as co


# ─────────────────────────────── корпуса

def ole2(*имена: str, вне_границы: tuple[str, ...] = ()) -> bytes:
    """Контейнер OLE2 с записями каталога на границах 128 байт. Имена из
    вне_границы кладутся в «текст» документа со сдвигом — как слово в UTF-16."""
    шапка = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    каталог = b""
    for имя in ("Root Entry", *имена):
        запись = имя.encode("utf-16-le") + b"\x00\x00"
        каталог += запись.ljust(128, b"\x00")
    каталог = каталог.ljust(512 * 2, b"\x00")
    текст = b"\x00\x00" + b"".join(и.encode("utf-16-le") + b"\x00\x00" for и in вне_границы)
    return шапка + каталог + текст.ljust(512, b"\x00")


def пакет(части: dict[str, bytes | str], сжатие: int = zipfile.ZIP_DEFLATED) -> bytes:
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w", сжатие) as z:
        for имя, данные in части.items():
            z.writestr(имя, данные, compress_type=zipfile.ZIP_STORED if имя == "mimetype" else сжатие)
    return буфер.getvalue()


ДОКУМЕНТ_XML = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="urn:w"><w:body>'
                + "".join(f"<w:p><w:r><w:t>Абзац выдуманный {i}</w:t></w:r></w:p>" for i in range(50))
                + "</w:body></w:document>")


def docx_части() -> dict[str, bytes | str]:
    return {"_rels/.rels": "<Relationships/>", "word/document.xml": ДОКУМЕНТ_XML,
            "word/styles.xml": "<w:styles>" + "<w:style/>" * 400 + "</w:styles>",
            "[Content_Types].xml": "<Types/>"}


ODF = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
       'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
       'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
       'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"')


def ячейка(т: str) -> str:
    return f"<table:table-cell><text:p>{т}</text:p></table:table-cell>"


def строка(*т: str) -> str:
    return "<table:table-row>" + "".join(ячейка(x) for x in т) + "</table:table-row>"


# Листы нарочно НЕ по алфавиту: порядок книги должен сохраниться.
КНИГА = (f'<?xml version="1.0" encoding="UTF-8"?><office:document {ODF} office:version="1.3" '
         'office:mimetype="application/vnd.oasis.opendocument.spreadsheet"><office:body><office:spreadsheet>'
         '<table:table table:name="Яблоко">' + строка("Поз", "Наименование", "Кол-во")
         + строка("1", 'Втулка выдуманная, 3;5 "мм"', "4") + "</table:table>"
         '<table:table table:name="Альфа">' + строка("второй лист") + "</table:table>"
         '<table:table table:name="Середина">' + строка("третий лист") + "</table:table>"
         "</office:spreadsheet></office:body></office:document>").encode()

ДОКУМЕНТ = (f'<?xml version="1.0" encoding="UTF-8"?><office:document {ODF} office:version="1.3" '
            'office:mimetype="application/vnd.oasis.opendocument.text"><office:body><office:text>'
            "<text:p>Техническое задание выдуманное</text:p>"
            '<table:table table:name="Т1"><table:table-column table:number-columns-repeated="3"/>'
            + строка("№", "Наименование", "Кол-во") + строка("1", "Кольцо вымышленное 40х2", "12")
            + "</table:table><text:p>Конец документа</text:p></office:text></office:body></office:document>").encode()


def слайд(т: str) -> str:
    return ('<draw:page draw:name="s"><draw:frame svg:x="1cm" svg:y="1cm" svg:width="20cm" svg:height="3cm">'
            f"<draw:text-box><text:p>{т}</text:p></draw:text-box></draw:frame></draw:page>")


ПРЕЗЕНТАЦИЯ = (f'<?xml version="1.0" encoding="UTF-8"?><office:document {ODF} office:version="1.3" '
               'office:mimetype="application/vnd.oasis.opendocument.presentation"><office:body><office:presentation>'
               + слайд("Слайд один: насос выдуманный НВ-1") + слайд("Слайд два: ротор вымышленный")
               + "</office:presentation></office:body></office:document>").encode()

SPREADSHEETML = ('<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
                 'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Лист1"><Table>'
                 '<Row><Cell><Data ss:Type="String">Заглушка выдуманная</Data></Cell>'
                 '<Cell><Data ss:Type="Number">7</Data></Cell></Row></Table></Worksheet></Workbook>').encode()


# ─────────────────────────────── подмена subprocess.Popen

class _Процесс:
    """Один «запуск soffice»: сценарий решает, что он выведет и что запишет."""

    def __init__(self, хозяин: "_Запуски", cmd: list[str], kw: dict) -> None:
        self.хозяин, self.cmd, self.kw = хозяин, cmd, kw
        self.pid = 40000 + len(хозяин.вызовы)
        self.returncode: int | None = None
        self.timeout: float | None = None
        self._раз = 0
        хозяин.вызовы.append(self)

    @property
    def цель(self) -> str:
        return self.cmd[self.cmd.index("--convert-to") + 1]

    @property
    def выход(self) -> Path:
        return Path(self.cmd[self.cmd.index("--outdir") + 1])

    @property
    def вход(self) -> Path:
        return Path(self.cmd[-1])

    @property
    def профиль(self) -> str:
        return next(a for a in self.cmd if a.startswith("-env:UserInstallation=")).split("=", 1)[1]

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        self._раз += 1
        if self._раз > 1:                               # дочитывание после убийства
            self.returncode = -9
            return b"", b""
        self.timeout = timeout
        self.входной_файл_был = self.вход.exists()
        итог = self.хозяин.сценарий(self)
        if итог == "висит":
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        self.returncode = 0                              # soffice отвечает 0 и при неудаче
        return итог[0].encode(), итог[1].encode()


class _Запуски:
    def __init__(self, сценарий) -> None:
        self.сценарий = сценарий
        self.вызовы: list[_Процесс] = []
        self.убитые: list[tuple[int, int]] = []

    def __call__(self, cmd: list[str], **kw) -> _Процесс:
        return _Процесс(self, cmd, kw)


@pytest.fixture
def подмена(monkeypatch):
    monkeypatch.delenv("CONVERT_TIMEOUT", raising=False)

    def поставить(сценарий) -> _Запуски:
        запуски = _Запуски(сценарий)
        monkeypatch.setattr(co.subprocess, "Popen", запуски)
        monkeypatch.setattr(co, "найти_soffice", lambda: "/opt/lo/program/soffice")
        monkeypatch.setattr(co.os, "killpg", lambda pid, sig: запуски.убитые.append((pid, sig)))
        return запуски
    return поставить


def пишет(имя: str, данные: bytes = b"%PDF-1.4 x", вывод: str = ""):
    """Сценарий: успешная конвертация, файл имя в --outdir."""
    def сценарий(п: _Процесс):
        (п.выход / имя).write_bytes(данные)
        return вывод or f"convert {п.вход} as a Writer document -> {п.выход / имя}", ""
    return сценарий


def не_открыл(п: _Процесс):
    # Так отвечает настоящий soffice: без строки «convert … as a …», код 0.
    return "", "Error: source file could not be loaded"


def test_неоткрывшийся_файл_не_пробуют_чужим_фильтром(подмена):
    """Даже если в выводе названо приложение, а файл не открылся, второй
    вызов другим фильтром вывода бесполезен: 5 запусков вместо 3."""
    запуски = подмена(lambda п: (f"convert {п.вход} as a Writer document",
                                  "Error: source file could not be loaded"))
    co.в_текст(ole2("НеизвестныйПоток"))
    assert len(запуски.вызовы) == 3


# ─────────────────────────────── нет LibreOffice

def test_нет_libreoffice_названная_причина_а_не_исключение(monkeypatch):
    """Прогон на машине без LibreOffice не должен падать: файл получает
    причину «нет LibreOffice», и по ней видно, что чинить — установку."""
    monkeypatch.delenv("SOFFICE", raising=False)
    monkeypatch.setattr(co.shutil, "which", lambda имя: None)

    def запрет(*a, **k):
        raise AssertionError("без soffice запускать нечего")
    monkeypatch.setattr(co.subprocess, "Popen", запрет)
    doc = ole2("WordDocument")
    assert co.в_pdf(doc) == (None, "нет LibreOffice")
    assert co.в_текст(doc) == ("", "нет LibreOffice")
    assert co.в_csv(doc) == ([], "нет LibreOffice")


def test_soffice_из_окружения_и_битый_путь(monkeypatch, tmp_path):
    бинарник = tmp_path / "soffice"
    бинарник.write_text("#!/bin/sh\n")
    monkeypatch.setenv("SOFFICE", str(бинарник))
    assert co.найти_soffice() == str(бинарник)
    monkeypatch.setenv("SOFFICE", str(tmp_path / "нет-такого"))
    assert co.найти_soffice() is None


# ─────────────────────────────── строка запуска

def test_каждый_вызов_свой_профиль_своя_группа_utf8(подмена):
    """Общий профиль — причина «таймаутов» LibreOffice в этом репозитории:
    второй soffice отдаёт файл первому (или его сироте) и ждёт. Поэтому
    профиль у каждого вызова свой, процесс — в своей группе, локаль — UTF-8."""
    запуски = подмена(пишет("in.pdf"))
    doc = ole2("WordDocument")
    assert co.в_pdf(doc) == (b"%PDF-1.4 x", "")
    assert co.в_pdf(doc) == (b"%PDF-1.4 x", "")
    assert len(запуски.вызовы) == 2
    первый, второй = запуски.вызовы
    assert первый.профиль != второй.профиль
    for п in запуски.вызовы:
        каталог = Path(п.kw["env"]["TMPDIR"])
        assert п.cmd[0] == "/opt/lo/program/soffice"
        assert п.профиль.startswith("file:///") and п.профиль == (каталог / "profile").as_uri()
        assert {"--headless", "--norestore"} <= set(п.cmd)
        assert п.цель == "pdf" and п.вход == каталог / "in.doc" and п.выход.parent == каталог
        assert п.входной_файл_был
        assert п.kw["start_new_session"] is True
        assert п.kw["stdin"] is subprocess.DEVNULL
        assert п.kw["env"]["LC_ALL"] == "C.UTF-8"
        assert not каталог.exists(), "временный каталог вызова обязан исчезнуть"


def test_код_ноль_без_файла_не_успех(подмена):
    """soffice отвечает 0 и при «source file could not be loaded»: успех
    определяется по файлу, а не по коду."""
    подмена(не_открыл)
    pdf, почему = co.в_pdf(ole2("WordDocument"))
    assert pdf is None
    assert "не открыл" in почему


def test_пустой_pdf_не_результат(подмена):
    подмена(пишет("in.pdf", b""))
    assert co.в_pdf(ole2("WordDocument"))[0] is None


# ─────────────────────────────── таймаут

def test_таймаут_убивает_группу_чистит_канал_и_не_множится(подмена, monkeypatch, tmp_path):
    """Зависший soffice убивается ГРУППОЙ (oosplash и soffice.bin вместе), его
    канал в /tmp удаляется, остальные расширения после таймаута не пробуются:
    три таймаута подряд на одном файле — шесть минут простоя прогона."""
    monkeypatch.setenv("CONVERT_TIMEOUT", "7")
    monkeypatch.setattr(co, "КАТАЛОГИ_КАНАЛОВ", (str(tmp_path),))
    соседский = tmp_path / "OSL_PIPE_0_SingleOfficeIPC_соседа"
    соседский.write_bytes(b"")

    def висит(п: _Процесс):
        (tmp_path / co.имя_канала(п.профиль)).write_bytes(b"")
        return "висит"
    запуски = подмена(висит)
    текст, почему = co.в_текст(ole2("НеизвестныйПоток"))
    assert (текст, почему) == ("", "LibreOffice: таймаут 7 с")
    assert len(запуски.вызовы) == 1
    п = запуски.вызовы[0]
    assert 0 < п.timeout <= 7
    assert (п.pid, co.signal.SIGKILL) in запуски.убитые
    assert not (tmp_path / co.имя_канала(п.профиль)).exists()
    assert соседский.exists(), "чужой канал трогать нельзя"
    assert not Path(п.kw["env"]["TMPDIR"]).exists()


@pytest.mark.parametrize("значение, ждём", [("", 120.0), ("abc", 120.0), ("0", 120.0),
                                             ("-5", 120.0), ("45", 45.0), ("2.5", 2.5)])
def test_таймаут_из_окружения(monkeypatch, значение, ждём):
    monkeypatch.setenv("CONVERT_TIMEOUT", значение)
    assert co.таймаут() == ждём


def test_имя_канала_по_формуле_libreoffice():
    """Наблюдено 23.09.2026: soffice с профилем file:///tmp/kvant-probe-profile
    создал именно этот канал (hex без ведущих нулей — 29 знаков, а не 32)."""
    assert (co.имя_канала("file:///tmp/kvant-probe-profile").split("_", 3)[3]
            == "SingleOfficeIPC_7da2848ccef94891643631e81cab6")


# ─────────────────────────────── выбор расширения

def test_ole2_узнаётся_по_потокам():
    assert co.расширения(ole2("WordDocument"))[0][0] == ".doc"
    assert co.расширения(ole2("Workbook"))[0][0] == ".xls"
    assert co.расширения(ole2("Book"))[0][0] == ".xls"
    assert co.расширения(ole2("PowerPoint Document"))[0][0] == ".ppt"
    assert co.расширения(ole2("MatOST"))[0][0] == ".wps"


def test_ole2_основной_поток_раньше_вложенного():
    """Документ Word со вставленной книгой несёт и WordDocument, и Workbook;
    основной поток записан в каталоге раньше."""
    assert co.расширения(ole2("WordDocument", "ObjectPool", "Workbook"))[0][:2] == [".doc", ".xls"]
    assert co.расширения(ole2("Workbook", "MBD0001", "WordDocument"))[0][:2] == [".xls", ".doc"]


def test_слово_в_тексте_не_поток():
    """Русский .doc хранит текст в UTF-16: «Workbook» в тексте — не поток."""
    кандидаты, _ = co.расширения(ole2("НеизвестныйПоток", вне_границы=("Workbook",)))
    assert кандидаты == [".doc", ".xls", ".ppt"]


def test_неизвестный_ole2_пробует_doc_xls_ppt(подмена):
    запуски = подмена(не_открыл)
    текст, почему = co.в_текст(ole2("НеизвестныйПоток"))
    assert текст == ""
    assert [п.вход.suffix for п in запуски.вызовы] == [".doc", ".xls", ".ppt"]
    assert почему == "LibreOffice не открыл файл ни как .doc, ни как .xls, ни как .ppt"


def test_зашифрованный_и_письмо_отказ_без_запуска(подмена):
    запуски = подмена(пишет("in.pdf"))
    assert co.в_pdf(ole2("EncryptedPackage", "EncryptionInfo"))[1].startswith("зашифрован паролем")
    assert "Outlook" in co.в_текст(ole2("__substg1.0_0037001F"))[1]
    assert запуски.вызовы == []


def test_подсказка_имя_расширение_подвид():
    двоичный = b"\x00\x01\x02" + "неопознанное".encode()
    assert co.расширения(двоичный, "Спецификация.XLSB")[0] == [".xlsb"]
    assert co.расширения(двоичный, "wpd")[0] == [".wpd"]
    assert co.расширения(двоичный, "spreadsheetml")[0] == [".xml"]
    assert co.расширения(двоичный, "ole2_разобрать")[0] == [""]
    assert co.расширения(двоичный, "картинка.jpg")[0] == [""]
    # содержимое уверенно — оно первым, подсказка запасной
    assert co.расширения(ole2("WordDocument"), "x.xls")[0] == [".doc", ".xls"]
    # содержимое неуверенно — первой подсказка
    assert co.расширения(ole2("Прочее"), "старый.wps")[0] == [".wps", ".doc", ".xls", ".ppt"]


def test_pk_контейнеры():
    assert co.расширения(пакет(docx_части()))[0] == [".docx"]
    assert co.расширения(пакет({"xl/workbook.xml": "<w/>"}))[0] == [".xlsx"]
    assert co.расширения(пакет({"xl/workbook.bin": b"\x00"}))[0] == [".xlsb"]
    assert co.расширения(пакет({"ppt/presentation.xml": "<p/>"}))[0] == [".pptx"]
    for вид, расш in (("text", ".odt"), ("spreadsheet", ".ods"), ("presentation", ".odp")):
        odf = пакет({"mimetype": f"application/vnd.oasis.opendocument.{вид}", "content.xml": "<c/>"})
        assert co.расширения(odf)[0] == [расш]
    # обрезанный: каталога ZIP нет, опознаётся по локальным заголовкам
    целый = пакет(docx_части())
    assert co.расширения(целый[: len(целый) // 2])[0] == [".docx"]


def test_архив_не_офис():
    архив = пакет({"письмо.txt": "привет", "фото.jpg": b"\xff\xd8"})
    assert co.расширения(архив) == ([], "zip: архив, а не офисный файл")
    assert co.расширения(архив, "это.docx") == ([".docx"], "")


def test_текстовые_и_подписи():
    assert co.расширения(b"{\\rtf1\\ansi x}")[0] == [".rtf"]
    assert co.расширения(b"\xffWPC\x10\x00\x00\x00")[0] == [".wpd"]
    assert co.расширения(КНИГА)[0] == [".fods"]
    assert co.расширения(ПРЕЗЕНТАЦИЯ)[0] == [".fodp"]
    assert co.расширения(b"<!DOCTYPE html><html><body>x</body></html>")[0] == [".html"]
    assert co.расширения(SPREADSHEETML)[0] == [".xml"]
    assert co.расширения("просто текст\n".encode())[0] == [".txt"]
    assert co.расширения(b"\x01\x02\x03" * 50)[0] == [""]


# ─────────────────────────────── приложения и цели

def test_промах_приложения_второй_вызов_фильтром_открывшего(подмена):
    """Файл открыт Calc, а просили текст фильтром Writer: второй вызов — CSV."""
    def сценарий(п: _Процесс):
        if п.цель.startswith("txt"):
            return (f"convert {п.вход} as a Calc document -> x.txt",
                    "Error: Please verify input parameters... (SfxBaseModel::impl_store failed")
        (п.выход / "in-Лист1.csv").write_bytes("Заглушка выдуманная,7\n".encode())
        return f"Writing sheet Лист1 -> {п.выход / 'in-Лист1.csv'}", ""
    запуски = подмена(сценарий)
    assert co.в_текст(SPREADSHEETML) == ("Заглушка выдуманная\t7", "")
    assert [п.цель for п in запуски.вызовы] == [co.ЦЕЛЬ_ТЕКСТ[""], co.ЦЕЛЬ_CSV]


def test_листы_в_порядке_книги_а_не_по_алфавиту(подмена):
    def сценарий(п: _Процесс):
        for имя in ("Альфа", "Середина", "Яблоко"):     # на диск — по алфавиту
            (п.выход / f"in-{имя}.csv").write_bytes(f"{имя},1\n".encode())
        return "\n".join(f"Writing sheet {и} -> {п.выход / f'in-{и}.csv'}"
                         for и in ("Яблоко", "Альфа", "Середина")), ""
    подмена(сценарий)
    строки, почему = co.в_csv(ole2("Workbook"))
    assert почему == ""
    assert строки == [["Яблоко", "1"], ["Альфа", "1"], ["Середина", "1"]]


def test_презентация_в_строки_честный_отказ_без_запуска(подмена):
    запуски = подмена(пишет("in.csv"))
    строки, почему = co.в_csv(пакет({"ppt/presentation.xml": "<p/>"}))
    assert строки == [] and почему.startswith("презентация")
    assert запуски.вызовы == []


def test_документ_в_строки_через_html(подмена):
    html = ("<html><head><meta charset=utf-8></head><body><p>вне таблицы</p><table>"
            "<tr><td>№</td><td>Наименование</td></tr><tr><td>1</td><td>Кольцо</td></tr>"
            "</table></body></html>").encode()
    запуски = подмена(пишет("in.html", html))
    assert co.в_csv(ole2("WordDocument")) == ([["№", "Наименование"], ["1", "Кольцо"]], "")
    assert запуски.вызовы[0].цель == "html"


def test_pdf_как_есть_и_пустой_вход(подмена):
    запуски = подмена(пишет("in.pdf"))
    pdf = b"%PDF-1.7\n..."
    assert co.в_pdf(pdf) == (pdf, "")
    assert co.в_текст(b"") == ("", "пустой файл")
    assert запуски.вызовы == []


def test_мусор_вместо_текста_назван(подмена):
    """Двоичный файл без подписи LibreOffice открывает как текст и отдаёт
    «####…» или иероглифы из байтов, прочитанных как UTF-16."""
    подмена(пишет("in.txt", b"#" * 300))
    assert co.в_текст(b"\x01\x02\x03" * 100) == ("", co.МУСОР)
    assert co.похоже_на_мусор("塘塘塘塘偋̄᐀ࠈ######")
    assert not co.похоже_на_мусор("Техническое задание: насос НВ-1, 2 шт.")
    assert not co.похоже_на_мусор("")


# ─────────────────────────────── разбор выгрузок

def test_строки_html_объединения_и_вложенные():
    html = ('<table><tr><th colspan="2">Шапка&nbsp;общая</th><th>Кол</th></tr>'
            "<tr><td>1</td><td>Втулка<br>выдуманная</td><td>4</td></tr>"
            "<tr><td></td><td></td><td></td></tr>"
            "<tr><td>2</td><td><table><tr><td>вложенная</td></tr></table></td><td>1</td></tr>"
            "</table>").encode()
    assert co.строки_html(html) == [["Шапка общая", "", "Кол"], ["1", "Втулка выдуманная", "4"],
                                    ["вложенная"], ["2", "", "1"]]


def test_текст_презентации_без_мастер_страниц():
    fodp = (f'<?xml version="1.0"?><office:document {ODF}><office:master-styles><style:master-page>'
            "<draw:frame><draw:text-box><text:p>&lt;number&gt;</text:p></draw:text-box></draw:frame>"
            "</style:master-page></office:master-styles><office:body><office:presentation>"
            '<draw:page><draw:frame><draw:text-box><text:p>Насос<text:s text:c="2"/>НВ-1<text:tab/>2 шт'
            "<text:span> выдуманный</text:span></text:p><text:h>Ротор</text:h></draw:text-box></draw:frame>"
            "</draw:page></office:presentation></office:body></office:document>").encode()
    assert co.текст_odf(fodp) == "Насос  НВ-1\t2 шт выдуманный\nРотор"


def test_csv_лист_без_пустых_строк():
    b = '﻿Поз,Наименование\n,\n1,"Втулка, 3;5 ""мм"""\n'.encode()
    assert co.строки_csv(b) == [["Поз", "Наименование"], ["1", 'Втулка, 3;5 "мм"']]


# ─────────────────────────────── починка ZIP

def test_мусор_в_хвосте_пересобирается():
    """zipfile такой файл открывает, LibreOffice — нет (замер 23.09.2026)."""
    b = пакет(docx_части()) + b"\x00" * 3000
    z = zipfile.ZipFile(io.BytesIO(co.починить_zip(b)))
    assert z.read("word/document.xml").decode() == ДОКУМЕНТ_XML


def test_без_центрального_каталога_по_локальным_заголовкам():
    целый = пакет(docx_части())
    b = целый[: целый.find(b"PK\x01\x02")]
    z = zipfile.ZipFile(io.BytesIO(co.починить_zip(b)))
    assert set(z.namelist()) == set(docx_части())
    assert z.read("word/document.xml").decode() == ДОКУМЕНТ_XML


def test_обрыв_служебные_восстановлены_содержимое_дописано():
    """Как пишет LibreOffice: [Content_Types].xml последним. Обрыв внутри
    styles.xml — служебный участник выброшен, недостающие служебные части
    восстановлены; обрыв внутри document.xml — он дописан до целого XML."""
    части = {"word/document.xml": ДОКУМЕНТ_XML, "word/styles.xml": "<w:styles>" + "<w:style/>" * 4000 + "</w:styles>",
             "[Content_Types].xml": "<Types/>"}
    целый = пакет(части, zipfile.ZIP_STORED)
    обрыв_в_стилях = целый[: целый.find(b"<w:styles>") + 500]
    z = zipfile.ZipFile(io.BytesIO(co.починить_zip(обрыв_в_стилях)))
    assert "word/styles.xml" not in z.namelist()
    assert z.read("word/document.xml").decode() == ДОКУМЕНТ_XML
    типы = z.read("[Content_Types].xml").decode()
    assert 'PartName="/word/document.xml"' in типы and "wordprocessingml.document.main+xml" in типы
    assert 'Target="word/document.xml"' in z.read("_rels/.rels").decode()
    обрыв_в_документе = целый[: целый.find("Абзац выдуманный 20".encode())]
    документ = zipfile.ZipFile(io.BytesIO(co.починить_zip(обрыв_в_документе))).read("word/document.xml")
    assert документ.endswith(b"</w:body></w:document>")
    assert "Абзац выдуманный 19".encode() in документ


def test_манифест_odf_перечисляет_всех():
    """Неупомянутого в манифесте участника LibreOffice считает повреждением."""
    части = {"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": "<c/>",
             "manifest.rdf": "<r/>", "Thumbnails/thumbnail.png": b"\x89PNG" + b"\x00" * 20000,
             "META-INF/manifest.xml": "<m/>"}
    целый = пакет(части, zipfile.ZIP_STORED)
    z = zipfile.ZipFile(io.BytesIO(co.починить_zip(целый[: целый.find(b"\x89PNG") + 100])))
    assert z.infolist()[0].filename == "mimetype" and z.infolist()[0].compress_type == zipfile.ZIP_STORED
    манифест = z.read("META-INF/manifest.xml").decode()
    for имя in ("content.xml", "manifest.rdf"):
        assert f'full-path="{имя}"' in манифест
    assert "opendocument.text" in манифест


def test_мусор_перед_контейнером_срезается_до_запуска(подмена):
    """Без этого LibreOffice открывает такой файл как текст в UTF-16 и отдаёт
    иероглифы (замер 23.09.2026)."""
    запуски = подмена(пишет("in.txt", "Абзац выдуманный".encode()))
    assert co.в_текст(b"X" * 300 + пакет(docx_части())) == ("Абзац выдуманный", "")
    п = запуски.вызовы[0]
    assert п.вход.suffix == ".docx"


def test_дописать_xml():
    оборван = '<?xml version="1.0"?><a x="1"><b><c/><d>текст</d><e>недо'.encode()
    assert co.дописать_xml(оборван) == '<?xml version="1.0"?><a x="1"><b><c/><d>текст</d><e></e></b></a>'.encode()


# ─────────────────────────────── настоящий LibreOffice

@pytest.fixture(scope="module")
def lo():
    """LibreOffice, который открывает файлы. Без модулей Writer и Calc soffice
    есть, но не читает ничего — такой прогон пропускается, а не краснеет."""
    if not co.найти_soffice():
        pytest.skip("нет LibreOffice")
    pdf, почему = co.в_pdf(b"{\\rtf1\\ansi probe}")
    if not pdf:
        pytest.skip(f"LibreOffice не конвертирует: {почему}")
    # Корпуса собираются параллельно: у каждого вызова свой профиль.
    задания = {"xls": КНИГА, "xlsx": КНИГА, "doc": ДОКУМЕНТ, "docx": ДОКУМЕНТ, "ppt": ПРЕЗЕНТАЦИЯ}
    with ThreadPoolExecutor(4) as пул:
        итоги = dict(zip(задания, пул.map(lambda вид: co.конвертировать(задания[вид], "", {"*": вид}), задания)))
    корпуса = {}
    for вид, (файлы, _, почему) in итоги.items():
        assert файлы, f"корпус {вид} не собрался: {почему}"
        корпуса[вид] = файлы[0][1]
    return корпуса


def test_настоящая_книга_все_листы_в_порядке_при_локали_posix(lo, monkeypatch):
    """В локали POSIX LibreOffice молча не пишет листы с кириллическим именем:
    окружение процесса обязано ставить UTF-8 само, а не надеяться на прогон."""
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    for вид in ("xls", "xlsx"):
        строки, почему = co.в_csv(lo[вид])
        assert почему == "", вид
        assert строки == [["Поз", "Наименование", "Кол-во"], ["1", 'Втулка выдуманная, 3;5 "мм"', "4"],
                          ["второй лист"], ["третий лист"]], вид


def test_настоящий_документ_таблица_и_текст(lo):
    for вид in ("doc", "docx"):
        строки, почему = co.в_csv(lo[вид])
        assert (строки, почему) == ([["№", "Наименование", "Кол-во"], ["1", "Кольцо вымышленное 40х2", "12"]], ""), вид
        текст, почему = co.в_текст(lo[вид])
        assert почему == "" and "Техническое задание выдуманное" in текст and "Конец документа" in текст, вид


def test_настоящая_презентация_текст_и_pdf(lo):
    текст, почему = co.в_текст(lo["ppt"])
    assert (текст, почему) == ("Слайд один: насос выдуманный НВ-1\nСлайд два: ротор вымышленный", "")
    pdf, почему = co.в_pdf(lo["ppt"])
    assert pdf and pdf.startswith(b"%PDF") and почему == ""


def test_настоящий_промах_расширения(lo):
    """Книга под именем .doc и SpreadsheetML без приложения: решает то, чем
    LibreOffice файл открыл."""
    assert "Втулка выдуманная" in co.в_текст(lo["xls"], "заявка.doc")[0]
    assert co.в_текст(SPREADSHEETML) == ("Заглушка выдуманная\t7", "")


def test_настоящий_повреждённый_docx(lo):
    целый = lo["docx"]
    for повреждённый in (целый + b"\x00" * 5000, целый[: целый.find(b"PK\x01\x02")]):
        текст, почему = co.в_текст(повреждённый)
        assert почему == "" and "Кольцо вымышленное 40х2" in текст


def test_настоящие_параллельные_вызовы(lo):
    """Свой профиль у каждого вызова: четыре одновременных не ждут друг друга."""
    итоги: list = [None] * 4

    def работа(i: int) -> None:
        итоги[i] = co.в_csv(lo["xls"])
    нити = [threading.Thread(target=работа, args=(i,)) for i in range(4)]
    for н in нити:
        н.start()
    for н in нити:
        н.join()
    assert all(и == итоги[0] and и[1] == "" and len(и[0]) == 4 for и in итоги)


def test_настоящий_таймаут_не_оставляет_ни_процессов_ни_файлов(lo, monkeypatch, tmp_path):
    monkeypatch.setenv("CONVERT_TIMEOUT", "0.3")
    monkeypatch.setattr(co.tempfile, "tempdir", str(tmp_path))
    pdf, почему = co.в_pdf(lo["doc"])
    assert pdf is None and почему == "LibreOffice: таймаут 0.3 с"
    time.sleep(0.5)
    живые = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if str(tmp_path).encode() in Path(f"/proc/{pid}/cmdline").read_bytes():
                живые.append(pid)
        except OSError:
            continue
    assert живые == [], "после таймаута не должно остаться ни oosplash, ни soffice.bin"
    assert list(tmp_path.iterdir()) == []
