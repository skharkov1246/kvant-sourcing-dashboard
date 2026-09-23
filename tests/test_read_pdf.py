"""Чтец PDF постранично (library/read_pdf.py): два извлекателя, сканы и мусор по номерам.

PDF — главное узкое место разбора: 11 217 файлов, из них 1 683 без позиций и
3 474 «текстом без шапки» (замер 23.09.2026). Здесь проверяется то, за что модуль
отвечает:

  • кириллица читается, страницы нумеруются с единицы, как у pdftoppm;
  • смешанный PDF отдаёт номера сканов, пустой лист сканом не считается;
  • кириллица через WinAnsi («Íàèìåíîâàíèå») чинится, а шрифт без таблицы
    кодировки называется мусорным и уходит в распознавание;
  • зашифрованный пустым паролем читается, паролем — назван;
  • потолок страниц, мусор перед подписью, битая таблица ссылок, обрезанный
    файл, отсутствие программ — названная причина, а не исключение и не молчание.

PDF собираются побайтно здесь же (правило 18: корпуса придумывают, а не копируют).
Кириллица без встроенного шрифта даётся таблицей ToUnicode: извлекатели берут
букву из неё, а не из шрифта. Если poppler или pypdf в системе нет — проверка
пропускается, а не падает: у гейта их может не быть.
"""
from __future__ import annotations

import io
import random
import re
import shutil

import pytest

from library import read_pdf

ЕСТЬ_POPPLER = bool(shutil.which("pdftotext"))
нужен_poppler = pytest.mark.skipif(not ЕСТЬ_POPPLER, reason="нет pdftotext (poppler-utils)")


def _есть_pypdf() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except Exception:                                                   # noqa: BLE001
        return False


нужен_pypdf = pytest.mark.skipif(not _есть_pypdf(), reason="нет pypdf")


# ── Сборка PDF побайтно ──────────────────────────────────────────────────────

def _поток(тело: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(тело) + тело + b"\nendstream"


def _cmap(диапазоны: bytes) -> bytes:
    return (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            b"/CMapName /T def /CMapType 2 def\n"
            b"1 begincodespacerange <00> <FF> endcodespacerange\n" + диапазоны +
            b"endcmap CMapName currentdict /CMap defineresource pop end end\n")


#: F2: байты cp1251 → Юникод. C0–FF у cp1251 — это ровно А–я подряд.
CMAP_КИРИЛЛИЦА = _cmap(b"2 beginbfrange\n<20> <7E> <0020>\n<C0> <FF> <0410>\nendbfrange\n")
#: F3: всё в частную область Юникода — так выглядит символьный шрифт без таблицы.
CMAP_ЧАСТНАЯ = _cmap(b"1 beginbfrange\n<21> <FF> <E021>\nendbfrange\n")

СКАН = b"q 200 0 0 200 100 400 cm /Im1 Do Q"
#: Чертёж: надписи кривыми, текстового слоя нет, поток длинный.
ЧЕРТЁЖ = b"\n".join(b"%d %d m %d %d l S" % (i, i * 2 % 800, i + 30, (i * 7) % 800)
                    for i in range(400))


def собрать_pdf(страницы: list[bytes]) -> bytes:
    """PDF из потоков содержимого. У каждой страницы в ресурсах три шрифта и одна
    картинка: F1 — WinAnsi без таблицы, F2 — с кириллицей, F3 — мусорный. Картинка
    лежит в ресурсах ВСЕХ страниц, а рисуется только там, где есть «/Im1 Do», —
    так проверяется, что сканом считается нарисованное, а не перечисленное."""
    объекты: list[bytes | None] = [None, None]

    def добавить(тело: bytes) -> int:
        объекты.append(тело)
        return len(объекты)

    f1 = добавить(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    т2 = добавить(_поток(CMAP_КИРИЛЛИЦА))
    f2 = добавить(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding "
                  b"/ToUnicode %d 0 R >>" % т2)
    т3 = добавить(_поток(CMAP_ЧАСТНАЯ))
    f3 = добавить(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding "
                  b"/ToUnicode %d 0 R >>" % т3)
    картинка = добавить(b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
                        b"/BitsPerComponent 8 /Length 4 >>\nstream\n\x00\xff\xff\x00\nendstream")
    листы = []
    for тело in страницы:
        поток = добавить(_поток(тело))
        листы.append(добавить(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R "
            b"/F2 %d 0 R /F3 %d 0 R >> /XObject << /Im1 %d 0 R >> >> /Contents %d 0 R >>"
            % (f1, f2, f3, картинка, поток)))
    объекты[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    объекты[1] = (b"<< /Type /Pages /Kids [%s] /Count %d >>"
                  % (b" ".join(b"%d 0 R" % н for н in листы), len(листы)))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    смещения = []
    for н, тело in enumerate(объекты, 1):
        смещения.append(len(out))
        out += b"%d 0 obj\n" % н + (тело or b"") + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(объекты) + 1)
    for с in смещения:
        out += b"%010d 00000 n \n" % с
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(объекты) + 1, xref)
    return bytes(out)


def текст(шрифт: str, строки: str, кодировка: str = "cp1251") -> bytes:
    """Поток, печатающий строки шрифтом. Байты — cp1251: через F2 они читаются
    кириллицей, через F1 — как «Íàèìåíîâàíèå»."""
    out = []
    for i, строка in enumerate(строки.split("\n")):
        б = (строка.encode(кодировка).replace(b"\\", b"\\\\")
             .replace(b"(", b"\\(").replace(b")", b"\\)"))
        out.append(b"BT /%s 10 Tf 40 %d Td (%s) Tj ET" % (шрифт.encode(), 800 - 14 * i, б))
    return b"\n".join(out)


#: Придуманная спецификация: наименования родовые, цены выдуманы.
СПЕЦИФИКАЦИЯ = ("Наименование  Кол-во  Цена\n"
                "Насос ЦНС 38-176  2  15000,00\n"
                "Задвижка 30с41нж  4  2500,00\n"
                "Подшипник 6312  10  870,00")


def зашифровать(b: bytes, пароль: str) -> bytes:
    """RC4-128: pypdf шифрует и расшифровывает его без пакета cryptography,
    поэтому проверка не зависит от лишних зависимостей гейта."""
    from pypdf import PdfReader, PdfWriter
    писатель = PdfWriter(clone_from=PdfReader(io.BytesIO(b)))
    писатель.encrypt(user_password=пароль, owner_password="owner", algorithm="RC4-128")
    выход = io.BytesIO()
    писатель.write(выход)
    return выход.getvalue()


КЛЮЧИ = {"text", "pages", "pages_read", "text_pages", "scan_pages", "garbled_pages",
         "blank_pages", "failed_pages", "page_methods", "method", "reason", "missing"}


# ── Сквозные проверки на настоящих извлекателях ─────────────────────────────

@нужен_poppler
@нужен_pypdf
def test_кириллица_читается_номера_с_единицы():
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)]))
    assert set(р) >= КЛЮЧИ
    assert р["reason"] == "", р["reason"]
    assert р["pages"] == 1 and р["pages_read"] == 1
    assert р["text_pages"] == [1] and not р["scan_pages"] and not р["garbled_pages"]
    assert "Насос ЦНС 38-176" in р["text"] and "15000,00" in р["text"]
    assert р["method"] in ("pdftotext", "pypdf")


@нужен_poppler
@нужен_pypdf
def test_смешанный_pdf_отдаёт_номера_сканов():
    """Прежде одна текстовая страница давала файлу «разобран», и сканы внутри
    не попадали в распознавание никогда. Теперь их номера идут наружу, а слот
    скана в тексте пуст — распознавание вставит его туда же."""
    р = read_pdf.прочитать_pdf(собрать_pdf([
        текст("F2", СПЕЦИФИКАЦИЯ), СКАН, текст("F2", "Продолжение  спецификации\n" + СПЕЦИФИКАЦИЯ), b""]))
    assert р["pages"] == 4
    assert р["text_pages"] == [1, 3]
    assert р["scan_pages"] == [2], "скан не назван номером"
    assert р["blank_pages"] == [4], "пустой лист — не скан: распознавать на нём нечего"
    слоты = р["text"].split("\f")
    assert len(слоты) == 4 and слоты[1] == "" and "Продолжение" in слоты[2]
    assert "смешанный" in р["reason"] and "1 из 4" in р["reason"]


@нужен_poppler
@нужен_pypdf
def test_весь_скан_и_чертёж_назван_и_в_распознавание():
    """Картинка с имени в ресурсах не делает лист сканом (пустой лист выше), а
    нарисованная — делает. Чертёж без картинки, но с длинным векторным потоком —
    тоже в распознавание: надписи кривыми текстовый слой не отдаёт."""
    р = read_pdf.прочитать_pdf(собрать_pdf([СКАН, ЧЕРТЁЖ]))
    assert р["scan_pages"] == [1, 2] and р["text_pages"] == []
    assert р["text"].strip("\f") == ""
    assert "без текстового слоя" in р["reason"] and "скан" in р["reason"]


@нужен_poppler
@нужен_pypdf
def test_кириллица_через_winansi_чинится():
    """Старый 1С пишет кириллицу байтами cp1251 в шрифт WinAnsi, и оба
    извлекателя отдают «Íàèìåíîâàíèå». Такая страница проходила порог знаков
    и считалась текстовой, а в позиции ложилась абракадабра."""
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F1", СПЕЦИФИКАЦИЯ)]))
    assert р["text_pages"] == [1], р
    assert "Наименование" in р["text"] and "Задвижка 30с41нж" in р["text"]
    assert "Íàèìåíîâàíèå" not in р["text"]
    assert р["page_methods"][0].endswith("+cp1251")
    assert р["reason"] == ""


@нужен_poppler
@нужен_pypdf
def test_мусорный_шрифт_уходит_в_распознавание_а_цифры_остаются():
    """Шрифт без таблицы кодировки: тысячи знаков, ни одной буквы. Прежде — «текст
    есть», распознавание мимо. Уцелевшее целым шрифтом (цены) при этом остаётся."""
    р = read_pdf.прочитать_pdf(собрать_pdf([
        текст("F3", СПЕЦИФИКАЦИЯ + "\n" + СПЕЦИФИКАЦИЯ) + b"\n" + текст("F2", "15000,00")]))
    assert р["garbled_pages"] == [1], р
    assert р["text_pages"] == [] and р["scan_pages"] == []
    assert "мусор" in р["reason"]
    assert "15000,00" in р["text"], "уцелевшая цифра выброшена вместе с мусором"
    assert not re.search("[\ue000-\uf8ff]", р["text"]), "мусор остался в тексте"


@нужен_poppler
@нужен_pypdf
def test_потолок_страниц_назван(monkeypatch):
    monkeypatch.setenv("PDF_PAGES", "2")
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)] * 3))
    assert р["pages"] == 3 and р["pages_read"] == 2
    assert р["text_pages"] == [1, 2]
    assert len(р["text"].split("\f")) == 2
    assert "2 стр. из 3" in р["reason"] and "PDF_PAGES" in р["reason"]


def test_потолок_из_окружения_терпит_мусор(monkeypatch):
    for значение, ждём in (("", 200), ("abc", 200), ("0", 200), ("-5", 200), ("35", 35)):
        monkeypatch.setenv("PDF_PAGES", значение)
        assert read_pdf.предел_страниц() == ждём, значение


@нужен_poppler
@нужен_pypdf
def test_мусор_перед_подписью_и_битая_таблица_ссылок():
    """Вложение из почты с хвостом заголовков перед «%PDF» и файл с неверным
    startxref: poppler восстанавливает оба, pypdf на первом спотыкался."""
    b = собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)])
    # Пять килобайт заголовков: poppler ищет подпись только в первом килобайте.
    заголовки = b"X-Header: " + b"0" * 70 + b"\r\n"
    for испорченный in (заголовки * 64 + b"Content-Type: application/pdf\r\n\r\n" + b,
                        re.sub(rb"startxref\n\d+", b"startxref\n999999", b)):
        р = read_pdf.прочитать_pdf(испорченный)
        assert р["text_pages"] == [1], р["reason"]
        assert "Насос" in р["text"]


@нужен_pypdf
def test_срез_перед_подписью_возвращает_верные_смещения(caplog, monkeypatch):
    """Смещения таблицы ссылок отсчитаны от «%PDF». Без среза оба извлекателя
    идут в восстановление таблицы — на простом файле оно удаётся, на файле с
    потоками объектов и дописанными правками бывает неполным. Со срезом pypdf
    открывает файл без восстановления, то есть без предупреждения о startxref."""
    monkeypatch.setattr(read_pdf, "_недостающие", lambda: ["pdftotext", "pdfinfo", "qpdf"])
    b = b"Content-Type: application/pdf\r\n\r\n" + собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)])
    with caplog.at_level("WARNING"):
        р = read_pdf.прочитать_pdf(b)
    assert р["text_pages"] == [1]
    assert not [з for з in caplog.records if "startxref" in з.getMessage()], "таблица ссылок восстанавливалась"


@нужен_poppler
def test_обрезанный_и_чужой_файл_названы_без_исключения():
    b = собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)])
    р = read_pdf.прочитать_pdf(b[:len(b) // 3])
    assert р["text"] == "" and р["reason"].startswith("PDF не открылся"), р["reason"]
    assert read_pdf.прочитать_pdf(b"")["reason"] == "пустой файл"
    assert read_pdf.прочитать_pdf(None)["reason"] == "пустой файл"         # type: ignore[arg-type]
    assert read_pdf.прочитать_pdf(b"PK\x03\x04" + b"0" * 100)["reason"].startswith("не PDF")


@нужен_poppler
def test_испорченные_байты_никогда_не_бросают():
    """Обещание модуля: никакого исключения на любых байтах. Сорок испорченных
    копий одного файла — порча в случайных местах, но с постоянным зерном."""
    b = собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ), СКАН])
    сл = random.Random(20260923)
    for _ in range(40):
        м = bytearray(b)
        for _ in range(сл.randint(1, 30)):
            м[сл.randrange(len(м))] = сл.randrange(256)
        р = read_pdf.прочитать_pdf(bytes(м))
        assert set(р) >= КЛЮЧИ
        assert р["text"] or р["reason"], "пустота без причины"


def test_последний_рубеж(monkeypatch):
    def сломан(*_a, **_k):
        raise RuntimeError("не должно выйти наружу")
    monkeypatch.setattr(read_pdf, "_прочитать", сломан)
    р = read_pdf.прочитать_pdf(b"%PDF-1.4")
    assert р["reason"] == "сбой чтения PDF (RuntimeError)" and set(р) >= КЛЮЧИ


# ── Шифрование ───────────────────────────────────────────────────────────────

@нужен_pypdf
def test_зашифрованный_пустым_паролем_читается():
    """Запрет копирования при пустом пароле пользователя — частая «защита» КП.
    Текст при этом доступен, и терять такой файл нельзя."""
    р = read_pdf.прочитать_pdf(зашифровать(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)]), ""))
    assert р["text_pages"] == [1], р["reason"]
    assert "Насос ЦНС 38-176" in р["text"]


@нужен_pypdf
def test_pypdf_сам_снимает_пустой_пароль(monkeypatch):
    """Без poppler и qpdf пустой пароль снимает pypdf — иначе такой файл на
    раннере без poppler-utils терялся бы целиком."""
    monkeypatch.setattr(read_pdf, "_недостающие", lambda: ["pdftotext", "pdfinfo", "qpdf"])
    р = read_pdf.прочитать_pdf(зашифровать(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)]), ""))
    assert р["text_pages"] == [1] and р["method"] == "pypdf", р["reason"]


@нужен_pypdf
def test_зашифрованный_паролем_назван():
    р = read_pdf.прочитать_pdf(зашифровать(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)]), "секрет"))
    assert р["text"] == ""
    assert "зашифрован" in р["reason"] and "пароль" in р["reason"], р["reason"]


@нужен_pypdf
@pytest.mark.skipif(not shutil.which("qpdf"), reason="нет qpdf")
def test_qpdf_снимает_шифр_который_не_снял_pypdf(monkeypatch):
    """pypdf без пакета cryptography не снимает AES и бросает DependencyError;
    poppler в этом прогоне нет. Тогда шифр снимает qpdf, и файл читается."""
    исходный = read_pdf._открыть_pypdf

    def без_aes(данные: bytes) -> dict:
        if b"/Encrypt" in данные:
            return {"читатель": None, "страниц": 0, "шифр": True, "пароль": False,
                    "почему": "pypdf: не расшифровал (DependencyError)"}
        return исходный(данные)

    monkeypatch.setattr(read_pdf, "_открыть_pypdf", без_aes)
    monkeypatch.setattr(read_pdf, "_недостающие", lambda: ["pdftotext", "pdfinfo"])
    р = read_pdf.прочитать_pdf(зашифровать(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)]), ""))
    assert р["text_pages"] == [1], р["reason"]
    assert р["method"] == "pypdf"


# ── Отсутствие программ ─────────────────────────────────────────────────────

def test_нет_ни_одного_извлекателя_названо(monkeypatch):
    monkeypatch.setattr(read_pdf, "_недостающие", lambda: ["pdftotext", "pdfinfo", "qpdf", "pypdf"])
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)]))
    assert р["reason"] == "нет ни pdftotext (poppler-utils), ни pypdf"
    assert "pdftotext" in р["missing"]


@нужен_pypdf
def test_без_poppler_читает_pypdf(monkeypatch):
    monkeypatch.setattr(read_pdf, "_недостающие", lambda: ["pdftotext", "pdfinfo", "qpdf"])
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ), СКАН]))
    assert р["method"] == "pypdf" and р["text_pages"] == [1] and р["scan_pages"] == [2]
    assert "нет pdftotext" in р["reason"], "недостающая программа не названа"


@нужен_poppler
def test_без_pypdf_читает_poppler_и_число_страниц_по_выводу(monkeypatch):
    """Без pypdf и pdfinfo число страниц узнаётся по выводу pdftotext."""
    monkeypatch.setattr(read_pdf, "_недостающие", lambda: ["pypdf", "pdfinfo"])
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)] * 3))
    assert р["method"] == "pdftotext" and р["pages"] == 3 and р["text_pages"] == [1, 2, 3]


@нужен_poppler
def test_pypdf_не_открыл_число_страниц_даёт_pdfinfo(monkeypatch):
    """pypdf упал на открытии: о том, что документ оборван потолком, узнаём от
    pdfinfo — иначе обрыв был бы молчаливым."""
    monkeypatch.setattr(read_pdf, "_открыть_pypdf", lambda _d: {
        "читатель": None, "страниц": 0, "шифр": False, "пароль": False,
        "почему": "pypdf: не открыл (PdfReadError)"})
    monkeypatch.setenv("PDF_PAGES", "1")
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)] * 2))
    if not shutil.which("pdfinfo"):
        pytest.skip("нет pdfinfo")
    assert р["pages"] == 2 and р["pages_read"] == 1 and "1 стр. из 2" in р["reason"]


@нужен_poppler
@нужен_pypdf
def test_pypdf_недосчитал_страниц_берётся_счёт_pdfinfo(monkeypatch):
    """На битом дереве страниц pypdf находит часть листов и молчит об остальных.
    Число страниц — большее из мнений pypdf и pdfinfo, иначе хвост теряется молча."""
    if not shutil.which("pdfinfo"):
        pytest.skip("нет pdfinfo")
    исходный = read_pdf._открыть_pypdf

    def недосчитал(данные: bytes) -> dict:
        итог = исходный(данные)
        итог["страниц"] = 1
        return итог

    monkeypatch.setattr(read_pdf, "_открыть_pypdf", недосчитал)
    р = read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)] * 3))
    assert р["pages"] == 3 and р["text_pages"] == [1, 2, 3], р["reason"]


# ── Выбор извлекателя и оценка качества — без внешних программ ─────────────

def _проход(поп: dict[int, str], пай: dict[int, str], распознавать: dict | None = None) -> dict:
    до = max([*поп, *пай, 0])
    return {"откр": {}, "всего": до, "до": до, "предел": 200,
            "поп": {"тексты": поп, "сбой": set(), "срок": False},
            "пай": {"тексты": пай, "сбой": set(), "распознавать": распознавать or {}, "срок": False}}


ХОРОШИЙ = СПЕЦИФИКАЦИЯ + "\nУсловия поставки: самовывоз со склада, срок 30 дней"


def test_берётся_лучший_извлекатель_на_странице():
    """pdftotext отдал номера глифов, pypdf — текст: берётся pypdf, и страница
    текстовая, а не мусорная."""
    р = read_pdf._собрать(_проход({1: "(cid:12)(cid:40) " * 80}, {1: ХОРОШИЙ}), [])
    assert р["page_methods"] == ["pypdf"] and р["text_pages"] == [1]
    assert "Насос" in р["text"]


def test_страница_берёт_своего_победителя_вопреки_документу():
    """По документу лучше pdftotext (он взял больше текста), но на второй странице
    он отдал номера глифов: там берётся pypdf, а не предпочтённый по документу."""
    р = read_pdf._собрать(_проход({1: ХОРОШИЙ * 3, 2: "(cid:7) " * 90}, {1: ХОРОШИЙ, 2: ХОРОШИЙ}), [])
    assert р["page_methods"] == ["pdftotext", "pypdf"] and р["text_pages"] == [1, 2]
    assert р["method"] in ("pdftotext,pypdf", "pypdf,pdftotext")


def test_ничья_достаётся_предпочтённому_по_документу():
    """На ничьей берётся тот, кто лучше по документу, — чтобы колонки таблицы,
    разорванной по страницам, были выровнены одним способом."""
    р = read_pdf._собрать(_проход({1: ХОРОШИЙ, 2: ХОРОШИЙ}, {1: ХОРОШИЙ, 2: ХОРОШИЙ}), [])
    assert р["page_methods"] == ["pdftotext", "pdftotext"] and р["method"] == "pdftotext"
    # pypdf лучше на первой странице с большим отрывом — он и предпочтён на второй.
    р = read_pdf._собрать(_проход({1: "", 2: ХОРОШИЙ}, {1: ХОРОШИЙ * 3, 2: ХОРОШИЙ}), [])
    assert р["page_methods"] == ["pypdf", "pypdf"]


def test_буквы_вразбивку_проигрывают():
    вразбивку = " ".join(ХОРОШИЙ.replace(" ", ""))
    р = read_pdf._собрать(_проход({1: вразбивку}, {1: ХОРОШИЙ}), [])
    assert р["page_methods"] == ["pypdf"]


def test_страница_без_текста_у_обоих_и_сбой_у_обоих():
    р = read_pdf._собрать(_проход({1: "", 2: ХОРОШИЙ}, {1: " ", 2: ХОРОШИЙ}, {1: False}), [])
    assert р["blank_pages"] == [1] and р["text_pages"] == [2]
    п = _проход({2: ХОРОШИЙ}, {2: ХОРОШИЙ})
    п["до"] = п["всего"] = 2
    р = read_pdf._собрать(п, [])
    assert р["failed_pages"] == [1] and р["scan_pages"] == [1]
    assert "ни одним способом" in р["reason"]


def test_мусор_меряется_закрытым_списком():
    """Обвиняют только признаки, которых в настоящем тексте не бывает. Отточия,
    прочерки и артикулы без гласных — не мусор (правило 7)."""
    assert read_pdf.оценить("(cid:12)" * 50)["доля"] == 1.0
    assert read_pdf.оценить("\ue041\ue042\ue043 " * 30)["доля"] == 1.0
    assert read_pdf.оценить("Ð\x9dÐ°Ñ\x81Ð¾Ñ\x81 " * 10)["доля"] == 1.0
    assert read_pdf.оценить("Íàèìåíîâàíèå Êîë-âî")["доля"] > 0.9
    for чистый in (ХОРОШИЙ, "RSTP-12 ........ 15 шт ------ BFGH 7", "Größe été préfixe Straße"):
        assert read_pdf.оценить(чистый)["доля"] == 0.0, чистый


def test_починка_перекодировок():
    assert read_pdf.починить("Ð\x9dÐ°Ñ\x81Ð¾Ñ\x81 ЦНС-38") == ("Насос ЦНС-38", "utf-8")
    битое = "Íàèìåíîâàíèå Êîë-âî Öåíà 30ñ41íæ 15000,00"
    починено, чем = read_pdf.починить(битое)
    assert чем == "cp1251", "KOI8 поверх cp1251 должен проигрывать по частотности букв"
    assert починено == "Наименование Кол-во Цена 30с41нж 15000,00"
    assert read_pdf.починить("Größe été préfixe") == ("Größe été préfixe", "")
    assert read_pdf.починить(ХОРОШИЙ) == (ХОРОШИЙ, "")
    # KOI8, прочитанный как cp1252: cp1251 тоже «чинит» все слова, но в чужие
    # буквы («оБЙНЕОПЧБОЙЕ»), и проигрывает по частотности.
    koi8 = "Наименование Количество Цена Поставка".encode("koi8_r").decode("cp1252")
    assert read_pdf.починить(koi8) == ("Наименование Количество Цена Поставка", "koi8_r")
    # UTF-8, прочитанный как cp1251: генератор писал UTF-8 в шрифт с таблицей cp1251.
    assert read_pdf.починить("РќР°СЃРѕСЃ ЦНС-38") == ("Насос ЦНС-38", "utf-8")


def test_настоящие_пары_после_р_и_с_не_обвиняются():
    """Признак «UTF-8 через cp1251» — пара «Р»/«С» и знак верхней половины. Из
    списка выброшены знаки, которые стоят после этих букв в настоящем тексте:
    обвинение стоит спроса, а не полноты (правило 7)."""
    for настоящий in ("ГОСТ Р\xa052857-2007", "Рёбра, Рёв", "Сірий", "класс С–D", "категория “С”",
                      "80 С°", "ООО «С»", "Р№ 12"):
        assert read_pdf.оценить(настоящий)["доля"] == 0.0, настоящий
        assert read_pdf.починить(настоящий) == (настоящий, ""), настоящий


# ── pdftotext кусками: упавший кусок повторяется постранично ────────────────

def test_упавший_кусок_дочитывается_по_странице(monkeypatch):
    """Кусок из пяти страниц упал на третьей (как poppler на битом потоке):
    две готовые берутся, дальше — по странице, и теряется одна третья, а не
    весь кусок."""
    вызовы: list[tuple[int, int]] = []

    def поддельный(_путь, с, по, _таймаут, _layout):
        вызовы.append((с, по))
        if с != по:
            return -11, [f"страница {н}" for н in range(с, min(по, 2) + 1)], b""
        if с == 3:
            return None, [], b""                    # зависла: таймаут
        return 0, [f"страница {с}"], b""

    monkeypatch.setattr(read_pdf, "_pdftotext", поддельный)
    итог = read_pdf._poppler("x.pdf", 5, True)
    assert sorted(итог["тексты"]) == [1, 2, 4, 5] and итог["сбой"] == {3}
    assert вызовы == [(1, 5), (3, 3), (4, 4), (5, 5)]


def test_зависший_кусок_целиком_дочитывается_по_странице(monkeypatch):
    вызовы = []

    def поддельный(_путь, с, по, _таймаут, _layout):
        вызовы.append((с, по))
        return (None, [], b"") if с != по else (0, [f"страница {с}"], b"")

    monkeypatch.setattr(read_pdf, "_pdftotext", поддельный)
    итог = read_pdf._poppler("x.pdf", 3, True)
    assert sorted(итог["тексты"]) == [1, 2, 3] and not итог["сбой"]


def test_запрет_копирования_и_короткий_документ_не_перебираются(monkeypatch):
    """Код 3 (запрет копирования в части сборок poppler) и «нет таких страниц»
    одинаковы для всех страниц: перебор был бы сотнями одинаковых отказов."""
    вызовы = []

    def запрет(_путь, с, по, _таймаут, _layout):
        вызовы.append((с, по))
        return 3, [], b"Copying of text from this document is not allowed."

    monkeypatch.setattr(read_pdf, "_pdftotext", запрет)
    итог = read_pdf._poppler("x.pdf", 60, True)
    assert итог["запрет"] and len(вызовы) == 1

    вызовы.clear()

    def короткий(_путь, с, по, _таймаут, _layout):
        вызовы.append((с, по))
        if с > 25:
            return 99, [], b"Command Line Error: Wrong page range given"
        return 0, [f"стр {н}" for н in range(с, по + 1)], b""

    monkeypatch.setattr(read_pdf, "_pdftotext", короткий)
    итог = read_pdf._poppler("x.pdf", 60, True)
    assert len(итог["тексты"]) == 25 and not итог["сбой"] and вызовы == [(1, 25), (26, 50)]


def test_таймаут_внешней_программы_не_бросает():
    if not shutil.which("sleep"):
        pytest.skip("нет sleep")
    код, _вывод, _ = read_pdf._запуск(["sleep", "5"], 1)
    assert код is None
    assert read_pdf._запуск(["программы-такой-нет"], 5)[0] == -999


def test_неоткрытый_файл_не_перебирается_постранично(monkeypatch):
    вызовы = []

    def поддельный(_путь, с, по, _таймаут, _layout):
        вызовы.append((с, по))
        return 1, [], b"Command Line Error: Incorrect password\n"

    monkeypatch.setattr(read_pdf, "_pdftotext", поддельный)
    итог = read_pdf._poppler("x.pdf", 60, True)
    assert итог["не_открылся"] and итог["пароль"] and len(вызовы) == 1


def test_длинный_документ_читается_кусками(monkeypatch):
    вызовы = []

    def поддельный(_путь, с, по, _таймаут, _layout):
        вызовы.append((с, по))
        return 0, [f"стр {н}" for н in range(с, по + 1)], b""

    monkeypatch.setattr(read_pdf, "_pdftotext", поддельный)
    итог = read_pdf._poppler("x.pdf", 60, True)
    assert len(итог["тексты"]) == 60 and вызовы == [(1, 25), (26, 50), (51, 60)]


# ── Журнал и запись ──────────────────────────────────────────────────────────

@нужен_poppler
@нужен_pypdf
def test_ничего_не_печатает(capsys):
    """Журнал прогона публичный (правило 17): ни слова документа наружу."""
    read_pdf.прочитать_pdf(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ), СКАН, текст("F1", СПЕЦИФИКАЦИЯ)]))
    read_pdf.прочитать_pdf(зашифровать(собрать_pdf([текст("F2", СПЕЦИФИКАЦИЯ)]), "секрет"))
    вывод = capsys.readouterr()
    assert вывод.out == ""
    for слово in ("Насос", "Задвижка", "Наименование", "15000"):
        assert слово not in вывод.err


def test_в_запись_ставит_смешанный_только_когда_есть_что_распознавать():
    rec: dict = {}
    р = read_pdf._собрать(_проход({1: ХОРОШИЙ, 2: ""}, {1: ХОРОШИЙ, 2: ""}, {2: True}), [])
    read_pdf.в_запись(р, rec)
    assert rec == {"pdf_pages": 2, "pdf_pages_text": 1, "pdf_pages_lost": 0, "pdf_mixed": True}
    rec = {}
    read_pdf.в_запись(read_pdf._собрать(_проход({1: ХОРОШИЙ}, {1: ХОРОШИЙ}), []), rec)
    assert "pdf_mixed" not in rec and rec["pdf_pages_text"] == 1
    read_pdf.в_запись(р, None)                                          # не падает
