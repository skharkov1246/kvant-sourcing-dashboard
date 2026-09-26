"""Каскад чтения (indexer.читать_каскадом): свой читатель → починка → LibreOffice → модель.

ЗАЧЕМ ЭТО ПРОВЕРЯТЬ. Замер 23.09.2026: 9 527 файлов из 31 869 (29,9 %) без
единой позиции. Каскад закрывает это только если соблюдены четыре обещания, и
каждое нарушается молча — число позиций выходит правдоподобным:

1. ВЫКЛЮЧАТЕЛЬ РАБОТАЕТ. Каскад выключен, пока его не измерили холостым
   прогоном (CLAUDE.md, правило 3); включённый без замера, он поменял бы
   разбор всех новых файлов разом.
2. ДОРОГОЕ ЗОВЁТСЯ ПОСЛЕДНИМ. LibreOffice — секунды на файл, модель — деньги.
   Зови их поверх удавшегося своего читателя — прогон растянется в разы, а
   результат не изменится.
3. ИЗ МНОГИХ БЕРЁТСЯ ЛУЧШЕЕ, А НЕ ПЕРВОЕ. В архиве «КП и приложения» первым
   лежит письмо или справочник, спецификация — вторым.
4. ПУТЬ ЧТЕНИЯ ЗАПИСАН и не несёт имён файлов (правила 16 и 17).

Корпуса придуманы здесь же (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import ast
import io
import re
import zipfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from library import indexer
from tests.test_read_word import docx, ряд, таблица, яч

ROOT = Path(__file__).resolve().parents[1]
ШАПКА = "Наименование;Кол-во;Цена\n"
СПЕЦИФИКАЦИЯ = ШАПКА + "Насос ЦНС-38;2;1500\nЗадвижка 30с41нж;4;250\nПодшипник 6208;10;35\n"


@pytest.fixture(autouse=True)
def каскад_включён(monkeypatch):
    monkeypatch.setattr(indexer, "КАСКАД", True)
    monkeypatch.delenv("LLM_READ", raising=False)
    # LibreOffice в тестах не зовётся, пока тест не попросит явно: иначе проверка
    # зависит от того, стоит ли он на машине, и идёт секунды.
    monkeypatch.setattr(indexer.convert_office, "найти_soffice", lambda: None)


def читать(b: bytes, rec: dict | None = None):
    return indexer.читать(b, indexer.подвид(b), rec)


def архив(члены: dict[str, bytes | str]) -> bytes:
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w", zipfile.ZIP_DEFLATED) as z:
        for имя, данные in члены.items():
            z.writestr(имя, данные)
    return буфер.getvalue()


def книга(ряды: list[list[str]]) -> bytes:
    """Наименьшая книга xlsx: один лист, строки инлайн-строками."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    пакет = "http://schemas.openxmlformats.org/package/2006/relationships"
    лист = "".join(f'<row r="{н}">' + "".join(f'<c t="inlineStr"><is><t>{з}</t></is></c>' for з in ряд)
                   + "</row>" for н, ряд in enumerate(ряды, 1))
    return архив({
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                               'package/2006/content-types"/>',
        "_rels/.rels": f'<Relationships xmlns="{пакет}"><Relationship Id="rId1" '
                       f'Type="{r}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{ns}" xmlns:r="{r}"><sheets><sheet name="Л" '
                           f'sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{пакет}"><Relationship Id="rId1" '
                                      f'Type="{r}/worksheet" Target="worksheets/sheet1.xml"/>'
                                      f'</Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{ns}"><sheetData>{лист}</sheetData></worksheet>',
    })


def письмо(тело: str, вложения: dict[str, bytes]) -> bytes:
    m = MIMEMultipart()
    m["From"] = "snab@primer-postavshik.test"
    m["To"] = "zakupki@primer-kvant.test"
    m["Subject"] = "Коммерческое предложение"
    m.attach(MIMEText(тело, "plain", "utf-8"))
    for имя, данные in вложения.items():
        часть = MIMEApplication(данные, Name=имя)
        часть["Content-Disposition"] = f'attachment; filename="{имя}"'
        m.attach(часть)
    return m.as_bytes()


# ───────────────────────────── 1. выключатель ─────────────────────────────

def test_выключатель_выбирает_путь(monkeypatch):
    """Выключенный каскад не трогает прежнюю таблицу стратегий, включённый — идёт сам."""
    звали = []
    monkeypatch.setattr(indexer, "читать_каскадом",
                        lambda b, п, rec=None: звали.append(п) or ([], "текст", ""))
    monkeypatch.setattr(indexer, "КАСКАД", False)
    indexer.читать(СПЕЦИФИКАЦИЯ.encode(), "csv")
    assert звали == [], "каскад позвался при выключенном выключателе"
    monkeypatch.setattr(indexer, "КАСКАД", True)
    indexer.читать(СПЕЦИФИКАЦИЯ.encode(), "csv")
    assert звали == ["csv"]


def test_выключатель_по_умолчанию_выключен(monkeypatch):
    """Правило 3: включают после замера, а не по умолчанию."""
    import importlib
    monkeypatch.delenv("CASCADE", raising=False)
    свежий = importlib.reload(indexer)
    try:
        assert свежий.КАСКАД is False
    finally:
        importlib.reload(indexer)


def test_каждый_подвид_каскада_называет_отказ():
    """Запрет молчать действует и в каскаде: пустота без причины — потерянный файл."""
    for п in [*indexer.ВИД_ПО_ПОДВИДУ, "msg", "eml", "ods", "odt"]:
        строки, текст, отказ = indexer.читать(b"\x00" * 64, п)
        assert строки or текст or отказ, f"подвид {п} промолчал"


# ───────────────────────────── 2. письма ─────────────────────────────

def test_письмо_таблица_из_вложения_и_тело_для_условий():
    """КП часто приходит письмом: цены во вложении, условия — в теле."""
    b = письмо("Добрый день! Условия поставки: DAP Москва, оплата 30/70.",
               {"spec.csv": СПЕЦИФИКАЦИЯ.encode("cp1251")})
    rec: dict = {}
    строки, текст, отказ = читать(b, rec)
    assert rec["subkind"] == "eml"
    assert строки and строки[0] == ["Наименование", "Кол-во", "Цена"], строки
    assert ["Насос ЦНС-38", "2", "1500"] in строки
    assert "Условия поставки" in текст, "тело письма потеряно — условия не найдутся"
    assert "read_mail" in rec["read_chain"]


def test_тело_письма_доходит_до_условий_в_разборе(monkeypatch):
    """handle склеивает текст читателя с таблицей, иначе тело письма пропадёт."""
    b = письмо("Условия поставки: DAP Москва. Оплата 30/70.",
               {"spec.csv": СПЕЦИФИКАЦИЯ.encode("cp1251")})
    видели = []
    monkeypatch.setattr(indexer, "download", lambda fo, rec=None: b)
    monkeypatch.setattr(indexer, "SOURCE", "rfq")
    monkeypatch.setattr(indexer, "применить_условия", lambda items, т: видели.append(т))
    rec, items = indexer.handle({"fo": {"id": 1}, "deal": 1, "origin": "поле запроса",
                                 "field": "UF_X", "field_title": "КП поставщика"})
    assert items and видели and "DAP Москва" in видели[0], видели


# ───────────────────────────── 3. архивы ─────────────────────────────

def test_архив_берёт_таблицу_с_шапкой_а_не_самую_длинную():
    """Справочник без шапки на тридцать строк не должен вытеснить спецификацию.

    Справочник — книгой: у книги ворот шапки нет (строки идут как есть), и
    длиннее оказывается именно она. Справочник-csv ворота отсеяли бы сами, и
    проверка прошла бы при любом правиле выбора — так и вышло в первой редакции.
    """
    b = архив({"a_spravochnik.xlsx": книга([[f"Код {i}", f"Раздел {i}", f"Группа {i}"]
                                             for i in range(30)]),
               "b_spec.csv": СПЕЦИФИКАЦИЯ})
    строки, _, отказ = читать(b)
    assert строки[0] == ["Наименование", "Кол-во", "Цена"], строки[:2]


def test_вложенность_ограничена_и_не_роняет():
    """Архив в архиве в архиве… — предел, а не бесконечная рекурсия."""
    b = СПЕЦИФИКАЦИЯ.encode()
    for уровень in range(8):
        b = архив({f"u{уровень}.zip" if уровень else "spec.csv": b})
    строки, текст, отказ = читать(b)
    assert строки or текст or отказ


@pytest.mark.parametrize("b", [СПЕЦИФИКАЦИЯ.encode(), b"\x89PNG\r\n\x1a\n", b"\x01\x02 unknown bytes"])
def test_путь_чтения_есть_у_каждого_файла(b):
    """Пустой путь сводка засчитает «прежней таблице» — и замер каскада соврёт."""
    rec: dict = {}
    читать(b, rec)
    assert rec.get("read_chain"), "каскад не записал, кто читал файл"


def test_путь_чтения_без_имён_файлов():
    """Правило 17: путь доезжает до сводок — только читатели и счётчики."""
    b = архив({"Секретный_Клиент_спецификация.csv": СПЕЦИФИКАЦИЯ})
    rec: dict = {}
    читать(b, rec)
    assert rec["read_chain"] and "read_archive" in rec["read_chain"]
    assert "Секретный" not in rec["read_chain"] and ".csv" not in rec["read_chain"]
    assert len(rec["read_chain"]) <= 200


def test_opendocument_не_путается_с_архивом():
    """ODS и ODT — PK-контейнеры, как архив; читатель у них свой."""
    def odf(вид: str) -> bytes:
        буфер = io.BytesIO()
        with zipfile.ZipFile(буфер, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), f"application/vnd.oasis.opendocument.{вид}")
            z.writestr("content.xml", "<x/>")
        return буфер.getvalue()
    assert indexer.уточнить_подвид(odf("spreadsheet"), "zip") == "ods"
    assert indexer.уточнить_подвид(odf("text"), "zip") == "odt"
    assert indexer.уточнить_подвид(архив({"a.csv": "x"}), "zip") == "zip"


# ───────────────────────────── 4. офис и PDF ─────────────────────────────

def test_docx_таблица_через_read_word():
    b = docx(таблица(ряд(яч("Наименование"), яч("Кол-во"), яч("Цена")),
                     ряд(яч("Насос ЦНС-38"), яч("2"), яч("1500")),
                     ряд(яч("Задвижка 30с41нж"), яч("4"), яч("250"))))
    rec: dict = {}
    строки, _, отказ = читать(b, rec)
    assert ["Насос ЦНС-38", "2", "1500"] in строки, строки
    assert rec["read_chain"].startswith("read_word")


СТРАНИЦА_ТАБЛИЦЕЙ = ("Наименование              Кол-во     Цена\n"
                     "Насос ЦНС-38              2          1500\n"
                     "Задвижка 30с41нж          4          250\n"
                     "Подшипник 6208            10         35\n"
                     "Фильтр масляный           3          410\n")


def ответ_read_pdf(текст: str) -> dict:
    return {"text": текст + "\f", "pages": 2, "pages_read": 2, "text_pages": [1],
            "scan_pages": [2], "garbled_pages": [], "blank_pages": [], "failed_pages": [],
            "page_methods": {}, "method": "pdftotext", "reason": "страница 2: скан",
            "missing": []}


def test_pdf_таблица_берётся_прежним_путём_без_второго_чтения(monkeypatch):
    """Таблица собрана по раскладке pypdf — read_pdf не зовётся вовсе."""
    monkeypatch.setattr(indexer, "страницы_pdf", lambda b, layout=False: ([СТРАНИЦА_ТАБЛИЦЕЙ], 1, 0))

    def нельзя(*a, **k):
        raise AssertionError("read_pdf позван поверх собранной таблицы")
    monkeypatch.setattr(indexer.read_pdf, "прочитать_pdf", нельзя)
    rec: dict = {}
    строки, _, _ = indexer.читать(b"%PDF-1.4 x", "pdf", rec)
    assert any("Насос ЦНС-38" in " ".join(r) for r in строки), строки
    assert rec["pdf_pages"] == 1 and rec["read_chain"] == "pypdf:таблица"


def test_pdf_проза_прежним_путём_pypdf(monkeypatch):
    """Холостой переразбор 23.09.2026: проза через pdftotext разнесла ячейки по
    строкам, и 50 PDF дали 3 цены вместо прежних. Проза снова из pypdf, а
    read_pdf при живом тексте не зовётся вовсе."""
    проза = "Насос ЦНС 38-176 2 шт 150000 300000 — предложение действительно 30 дней"
    monkeypatch.setattr(indexer, "страницы_pdf", lambda b, layout=False: ([проза], 1, 0))

    def нельзя(*a, **k):
        raise AssertionError("read_pdf позван поверх живого текста pypdf")
    monkeypatch.setattr(indexer.read_pdf, "прочитать_pdf", нельзя)
    rec: dict = {}
    строки, текст, _ = indexer.читать(b"%PDF-1.4 x", "pdf", rec)
    assert строки == [] and "150000 300000" in текст
    assert rec["read_chain"] == "pypdf:текст"


def test_pdf_без_текста_pypdf_read_pdf_и_счёт_страниц(monkeypatch):
    """pypdf ничего не дал (шифр, битый файл) — read_pdf, и его постраничный счёт
    обязан попасть в запись: по pdf_mixed распознавание берёт смешанные файлы."""
    monkeypatch.setattr(indexer, "страницы_pdf", lambda b, layout=False: ([], 2, 2))
    monkeypatch.setattr(indexer.read_pdf, "прочитать_pdf",
                        lambda b, layout=True: ответ_read_pdf("Уважаемые коллеги, КП во вложении"))
    rec: dict = {}
    строки, текст, _ = indexer.читать(b"%PDF-1.4 x", "pdf", rec)
    assert строки == [] and "КП во вложении" in текст
    assert rec["pdf_pages"] == 2 and rec["pdf_pages_text"] == 1 and rec["pdf_mixed"] is True
    assert "read_pdf:pdftotext" in rec["read_chain"]


def test_pdf_мусор_pypdf_уступает_read_pdf(monkeypatch):
    """Символьный шрифт: pypdf отдаёт знаки частной области, read_pdf — текст."""
    мусор = "".join(chr(0xF000 + i % 200) for i in range(400))
    monkeypatch.setattr(indexer, "страницы_pdf", lambda b, layout=False: ([мусор], 1, 0))
    monkeypatch.setattr(indexer.read_pdf, "прочитать_pdf",
                        lambda b, layout=True: ответ_read_pdf("Насос ЦНС 38-176, две штуки, цена договорная"))
    rec: dict = {}
    _, текст, _ = indexer.читать(b"%PDF-1.4 x", "pdf", rec)
    assert "Насос ЦНС" in текст and rec["read_chain"].startswith("read_pdf")


def test_таблица_из_раскладки_pdftotext_не_берётся(monkeypatch):
    """Раскладка pdftotext ставит одиночные пробелы между колонками: на ней шапка
    слипается, цена встаёт из колонки «№», количество склеивается в семизначное
    число — и ворота шапки такую таблицу пропускают. Замер по корпусу 23.09.2026.
    Поэтому из текста read_pdf таблица не собирается вовсе."""
    слипшаяся = ("№ Наименование Кол-во Ед. Цена, руб. Сумма, руб.\n"
                 "1 Насос ЦНС 38-176 с рамой 2 шт 150000 300000\n"
                 "2 Задвижка 30с41нж Ду100 4 шт 25000 100000\n")
    monkeypatch.setattr(indexer, "страницы_pdf", lambda b, layout=False: ([], 0, 0))
    monkeypatch.setattr(indexer.read_pdf, "прочитать_pdf",
                        lambda b, layout=True: ответ_read_pdf(слипшаяся))
    строки, текст, _ = indexer.читать(b"%PDF-1.4 x", "pdf", {})
    assert строки == [] and "Насос ЦНС 38-176" in текст


# ───────────────────────────── 5. дорогое — последним ─────────────────────────────

def test_libreoffice_только_когда_своё_пусто(monkeypatch):
    звали = []
    monkeypatch.setattr(indexer.convert_office, "найти_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(indexer.convert_office, "в_csv",
                        lambda b, подсказка="": звали.append(1) or (
                            [["Наименование", "Кол-во", "Цена"], ["Насос ЦНС-38", "2", "1500"]], ""))
    monkeypatch.setattr(indexer.convert_office, "в_текст", lambda b, подсказка="": ("", "пусто"))
    # Свой читатель справился — конвертер не зовётся.
    b = docx(таблица(ряд(яч("Наименование"), яч("Кол-во"), яч("Цена")),
                     ряд(яч("Насос ЦНС-38"), яч("2"), яч("1500"))))
    читать(b)
    assert звали == [], "LibreOffice позван поверх удавшегося чтения"
    # Свой не справился — конвертер и есть последний рубеж.
    rec: dict = {}
    строки, _, отказ = indexer.читать(b"\x01\x02 unknown bytes", "неизвестно", rec)
    assert звали == [1] and ["Насос ЦНС-38", "2", "1500"] in строки and отказ == ""
    assert "libreoffice" in rec["read_chain"]


def test_модель_не_зовётся_без_разрешения_владельца(monkeypatch):
    """Чтение моделью стоит денег и требует секрета — только по LLM_READ=1."""
    звали = []
    monkeypatch.setattr(indexer.read_llm, "прочитать_моделью",
                        lambda b, п: звали.append(п) or ([], "текст моделью", ""))
    indexer.читать(b"\x01\x02 unknown bytes", "неизвестно")
    assert звали == []
    monkeypatch.setenv("LLM_READ", "1")
    строки, текст, _ = indexer.читать(b"\x01\x02 unknown bytes", "неизвестно")
    assert звали == ["неизвестно"] and текст == "текст моделью"
    # И поверх удавшегося чтения не зовётся даже с разрешением.
    читать(СПЕЦИФИКАЦИЯ.encode())
    assert звали == ["неизвестно"]


def test_починка_текста_записывается_в_путь():
    """Кракозябры cp1251, прочитанные как latin-1, — частый случай старых выгрузок."""
    исходный = ("Коммерческое предложение. Насос центробежный ЦНС 38-176 с рамой и "
                "электродвигателем, количество две штуки, срок поставки тридцать дней.")
    испорченный = исходный.encode("cp1251").decode("latin-1").encode("utf-8")
    rec: dict = {}
    _, текст, _ = indexer.читать(испорченный, "txt", rec)
    assert "Насос центробежный" in текст, текст[:80]
    assert "починка" in (rec.get("read_chain") or "")


# ───────────────────────────── 6. папка документа ─────────────────────────────

@pytest.fixture
def наши_из_битрикса(monkeypatch):
    """Список наших компаний без портала: как будто Битрикс его уже отдал."""
    monkeypatch.setattr(indexer, "_НАШИ", {"1": "ООО «КВАНТ»"})
    monkeypatch.setattr(indexer, "_НАШИ_ИМЕНА",
                        indexer.doc_kind.с_транслитом(["ООО «КВАНТ»"]))


КП_СТРОКОЙ = ("ООО «Техснаб-Пример». Кому: ООО «КВАНТ». Коммерческое предложение. "
              "Предлагаем поставку: Насос ЦНС-38, 2 шт, цена 150 000 руб. "
              "Директор ООО «Техснаб-Пример»")


def test_папка_пишется_в_запись(наши_из_битрикса):
    """Папку ставит система; содержимое согласно — это видно в «почему»."""
    rec: dict = {"origin": "поле запроса", "field": "ufCrm18_1731179998",
                 "field_title": "Offer from supplier"}
    indexer.определить_папку(rec, КП_СТРОКОЙ, [])
    assert rec["doc_kind"] == indexer.doc_kind.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА, rec
    assert rec["doc_kind_conf"] == 1.0 and "согласно" in rec["doc_kind_why"], rec
    assert rec["папка_содержимого"] == rec["doc_kind"] and not rec["расхождение"]


def test_сбой_классификатора_не_теряет_файл(monkeypatch):
    def сбой(*a, **k):
        raise ValueError("x")
    monkeypatch.setattr(indexer.doc_kind, "вид_документа", сбой)
    rec: dict = {"origin": "поле сделки", "field": "ufCrm_1633502831"}
    indexer.определить_папку(rec, "текст", [])
    # Папка системы на месте: сверка упала, решение системы — нет.
    assert rec["doc_kind"] == indexer.doc_kind.ЗАПРОС_ЗАКАЗЧИКА
    assert "сбой классификатора" in rec["doc_kind_why"]


def test_длинный_текст_сохраняет_шапку_и_подпись(monkeypatch):
    """Классификатор видит голову и хвост: бланк и подпись — там."""
    видел = []
    monkeypatch.setattr(indexer.doc_kind, "вид_документа",
                        lambda т, с, сторона, **k: видел.append(т) or ("x", 0.5, "y"))
    т = "БЛАНК-НАЧАЛО " + "середина " * 50000 + " ПОДПИСЬ-КОНЕЦ"
    indexer.определить_папку({}, т, [])
    assert видел[0].startswith("БЛАНК-НАЧАЛО") and видел[0].endswith("ПОДПИСЬ-КОНЕЦ")
    assert len(видел[0]) <= indexer.ПАПКА_ГОЛОВА + indexer.ПАПКА_ХВОСТ + 1


# ───────────────────────────── 7. запись и прогон ─────────────────────────────

def _запись_файла(monkeypatch) -> dict:
    """Запись такой, какой её отдаёт handle() (файл не скачался): все ключи на месте."""
    monkeypatch.setattr(indexer, "download", lambda fo, rec=None: None)
    rec, _ = indexer.handle({"fo": {"id": "5"}, "deal": "11", "origin": "поле запроса",
                             "field": "ufCrm18_1731179998", "field_title": "Offer from supplier"})
    rec["read_chain"] = "xlsx → таблица"
    return rec


def test_вставка_файла_колонок_столько_же_сколько_значений(monkeypatch):
    """Новая колонка в списке без значения в кортеже роняет запись всего буфера.

    Проверяется поведением: сгенерированный запрос, его шаблон строки и кортеж
    значений — по одному списку колонок, и путь чтения в нём есть."""
    rec = _запись_файла(monkeypatch)
    колонки = indexer.КОЛОНКИ_ВСТАВКИ
    запрос, шаблон = indexer.вставка_файлов(колонки)
    строка = indexer.кортеж_файла(rec, колонки)
    assert шаблон.count("%s") == len(строка) == len(колонки)
    assert "read_chain = excluded.read_chain" in запрос
    assert строка[колонки.index("read_chain")] == "xlsx → таблица"


def test_переразбор_пишет_путь_чтения_и_подстановки_сходятся(monkeypatch):
    import library.reparse as r
    assert "read_chain" in r.КОЛОНКИ_ЗАПИСИ, "колонка не проверяется до записи"
    rec = _запись_файла(monkeypatch)
    sql = r.правка_файла(r.КОЛОНКИ_ЗАПИСИ)
    значения = r.значения_правки(rec, r.КОЛОНКИ_ЗАПИСИ)
    assert "read_chain = %s" in sql
    assert sql.count("%s") == len(значения)
    assert значения[r.КОЛОНКИ_ЗАПИСИ.index("read_chain")] == "xlsx → таблица"


def test_схема_добавляет_колонку_пути_чтения():
    схема = (ROOT / "library" / "supabase" / "schema_junk.sql").read_text(encoding="utf-8")
    код = re.sub(r"(?m)--.*$", "", схема)
    assert re.search(r"alter table lib_files add column if not exists read_chain\s+text", код)


def test_прогон_ставит_читателей_каскада():
    """Код зовёт pdftotext, qpdf и soffice — прогон обязан их поставить.

    Пакеты ставит одна точка — .github/actions/parse-env; что её зовут все
    прогоны разбора, сверяет tests/test_parse_env.py."""
    сырой = (ROOT / ".github/actions/parse-env/action.yml").read_text(encoding="utf-8")
    yml = re.sub(r"(?m)^\s*#[^\n]*$", "", сырой)
    шаг = yml[yml.index("Системные пакеты разбора"):]
    шаг = шаг[:шаг.index("- name:", 10)]
    for пакет in ("poppler-utils", "qpdf", "antiword", "catdoc", "unrar"):
        assert пакет in шаг, f"{пакет} не ставится"
    # LibreOffice — только под каскад.
    ветка = шаг[шаг.index('"$CASCADE_IN" = "1"'):]
    ветка = re.split(r"\n\s*fi\n", ветка)[0]
    assert "libreoffice-calc-nogui" in ветка and "libreoffice-writer-nogui" in ветка
    wf = (ROOT / ".github/workflows/library-index.yml").read_text(encoding="utf-8")
    assert re.search(r"cascade:\s*\$\{\{\s*inputs\.cascade\s*&&\s*'1'\s*\|\|\s*'0'\s*\}\}", wf)


def test_прежний_читатель_xls_защищён_от_зацикливания_и_утечки(monkeypatch):
    """xlrd зацикливается на битой цепочке OLE2 и печатает имя листа в журнал."""
    monkeypatch.setattr(indexer.read_sheet, "ole2_цепи_целы", lambda b: False)
    with pytest.raises(ValueError, match="OLE2"):
        indexer.rows_from_xls(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500)
    дерево = ast.parse((ROOT / "library" / "indexer.py").read_text(encoding="utf-8"))
    вызовы = [у for у in ast.walk(дерево) if isinstance(у, ast.Call)
              and isinstance(у.func, ast.Attribute) and у.func.attr == "open_workbook"]
    assert вызовы and all("logfile" in {к.arg for к in у.keywords} for у in вызовы), \
        "xlrd без logfile печатает имена листов в публичный журнал"


def test_зашифрованная_книга_не_идёт_в_word(monkeypatch):
    monkeypatch.setattr(indexer.read_sheet, "прочитать_книгу",
                        lambda b: ([], "", "книга зашифрована паролем: без пароля не читается"))

    def нельзя(b):
        raise AssertionError("зашифрованная книга ушла в читатель Word")
    monkeypatch.setattr(indexer.read_word, "прочитать_документ", нельзя)
    строки, текст, отказ = indexer.читать(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole2_разобрать")
    assert отказ.startswith("книга зашифрована")


def test_каждый_лист_книги_разбирается_своей_шапкой():
    """Письмо на первом листе, прайс на втором — шапка прайса глубже сорока строк
    от начала книги, и общий разбор её не находит."""
    письмо = [["№ лист 1 «Письмо»"]] + [[f"Строка сопроводительного письма номер {i}"] for i in range(45)]
    прайс = [["№ лист 2 «Прайс»"], ["Наименование", "Кол-во", "Цена"],
             ["Насос ЦНС-38", "2", "1500"], ["Задвижка 30с41нж", "4", "250"]]
    items = indexer.позиции_по_листам(письмо + прайс)
    насос = [it for it in items if "Насос" in it["item_name"]]
    assert насос and насос[0]["_цена"] and float(насос[0]["_цена"]["price"]) == 1500, items[-3:]


def test_без_каскада_книга_разбирается_как_прежде(monkeypatch):
    monkeypatch.setattr(indexer, "КАСКАД", False)
    строки = [["№ лист 1 «Л»"], ["Наименование", "Кол-во", "Цена"], ["Насос ЦНС-38", "2", "1500"]]
    assert indexer.листы_книги(строки) == [строки]


def test_каскад_пишет_свою_версию_разборщика(monkeypatch):
    """Иначе переразбор с каскадом не возьмёт файлы, уже записанные версией 3,
    и проверка «у скольких стало хуже» пройдёт мимо всех прочитанных файлов."""
    import importlib
    try:
        monkeypatch.setenv("CASCADE", "1")
        с_каскадом = importlib.reload(indexer).PARSER_VERSION
        monkeypatch.setenv("CASCADE", "")
        без = importlib.reload(indexer).PARSER_VERSION
    finally:
        monkeypatch.delenv("CASCADE", raising=False)
        importlib.reload(indexer)
    assert без == 3 and с_каскадом > без


def test_переразбор_сравнивает_цены_по_читателю():
    """Итог «цен стало меньше» не говорит, какой читатель их теряет: первая часть
    холостого прогона каскада дала −652 строки цены, и виновника (проза PDF через
    pdftotext) пришлось вычислять косвенно."""
    import tests.test_reparse_wiring as w
    код = w.без_комментариев((ROOT / "library" / "reparse.py").read_text(encoding="utf-8"))
    assert "цены_читателя[путь[0]]" in код and "ЦЕНЫ ПО ЧИТАТЕЛЮ" in код
