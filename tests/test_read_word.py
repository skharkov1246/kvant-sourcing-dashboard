"""Документ Word и OpenDocument целиком: library/read_word.py.

Каждый тест — случай, который прежнее чтение .docx (регулярки по одной части
word/document.xml) теряло или искажало: сдвиг колонок при объединённых
ячейках, надписи, колонтитулы, сноски, картинки, .odt, «строгий» OOXML, битый
XML и оборванный ZIP.

Корпуса собраны здесь же из XML-строк, а не сняты с базы (CLAUDE.md,
правило 18); имена, цены и реквизиты выдуманы.
"""
from __future__ import annotations

import base64
import io
import struct
import zipfile
import zlib

from library import read_word
from library.read_word import прочитать_документ

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W_STRICT = "http://purl.oclc.org/ooxml/wordprocessingml/main"
R_STRICT = "http://purl.oclc.org/ooxml/officeDocument/relationships"
ПАКЕТ = "http://schemas.openxmlformats.org/package/2006/relationships"
ПРОСТРАНСТВА = (
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office"'
)
КЛЮЧИ = {"rows", "text", "images", "reason", "embedded", "notes", "format"}


# ───────────── сборка корпусов ─────────────

def п(текст: str) -> str:
    return f"<w:p><w:r><w:t xml:space=\"preserve\">{текст}</w:t></w:r></w:p>"


def яч(текст: str, свойства: str = "") -> str:
    return f"<w:tc><w:tcPr>{свойства}</w:tcPr>{п(текст) if текст else '<w:p/>'}</w:tc>"


def ряд(*ячейки: str, свойства: str = "") -> str:
    return f"<w:tr><w:trPr>{свойства}</w:trPr>{''.join(ячейки)}</w:tr>"


def таблица(*ряды: str) -> str:
    return f"<w:tbl><w:tblPr/><w:tblGrid/>{''.join(ряды)}</w:tbl>"


def связь(ид: str, тип: str, цель: str, пр: str = R, внешняя: bool = False) -> str:
    режим = ' TargetMode="External"' if внешняя else ""
    return f'<Relationship Id="{ид}" Type="{пр}/{тип}" Target="{цель}"{режим}/>'


def связи(*записи: str) -> str:
    return f'<?xml version="1.0"?><Relationships xmlns="{ПАКЕТ}">{"".join(записи)}</Relationships>'


def документ(тело: str, пр_w: str = W, пр_r: str = R) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{пр_w}" xmlns:r="{пр_r}" {ПРОСТРАНСТВА}>'
            f"<w:body>{тело}<w:sectPr/></w:body></w:document>")


def docx(тело: str = "", части: dict | None = None, связи_документа: str = "",
         главная: str = "word/document.xml", пр_w: str = W, пр_r: str = R,
         сырой_документ: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"/>')
        z.writestr("_rels/.rels", связи(связь("rId1", "officeDocument", главная, пр_r)))
        z.writestr(главная, сырой_документ if сырой_документ is not None
                   else документ(тело, пр_w, пр_r))
        if связи_документа:
            каталог, имя = главная.rsplit("/", 1)
            z.writestr(f"{каталог}/_rels/{имя}.rels", связи_документа)
        for имя, данные in (части or {}).items():
            z.writestr(имя, данные)
    return buf.getvalue()


def png(ш: int = 40, в: int = 30, тон: int = 0) -> bytes:
    """Настоящий PNG: распознаванию отдаётся файл, который оно откроет."""
    сырое = b"".join(b"\x00" + bytes([(x + y + тон) % 256 for x in range(ш)]) for y in range(в))

    def кусок(т: bytes, д: bytes) -> bytes:
        return struct.pack(">I", len(д)) + т + д + struct.pack(">I", zlib.crc32(т + д) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + кусок(b"IHDR", struct.pack(">IIBBBBB", ш, в, 8, 0, 0, 0, 0))
            + кусок(b"IDAT", zlib.compress(сырое)) + кусок(b"IEND", b""))


def картинка(ид: str) -> str:
    """Встроенный рисунок DrawingML, как его пишет Word."""
    return (f'<w:p><w:r><w:drawing><wp:inline><a:graphic><a:graphicData><pic:pic>'
            f'<pic:blipFill><a:blip r:embed="{ид}"/></pic:blipFill>'
            f'</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


def emf_текст(x: int, y: int, текст: str, шаг: int = 10) -> bytes:
    """Запись EMR_EXTTEXTOUTW: так Excel пишет каждую ячейку таблицы, вставленной
    в Word рисунком. Ширина знака — шаг в массиве dx."""
    строка = текст.encode("utf-16-le")
    строка += b"\x00" * (-len(строка) % 4)
    dx = struct.pack(f"<{len(текст)}i", *([шаг] * len(текст)))
    размер = 76 + len(строка) + len(dx)
    return (struct.pack("<II", 84, размер) + b"\x00" * 16 + struct.pack("<Iff", 1, 1.0, 1.0)
            + struct.pack("<iiIII", x, y, len(текст), 76, 0) + b"\x00" * 16
            + struct.pack("<I", 76 + len(строка)) + строка + dx)


def emf(*записи: bytes) -> bytes:
    заголовок = struct.pack("<II", 1, 88) + b"\x00" * 32 + b" EMF" + b"\x00" * 44
    return заголовок + b"".join(записи) + struct.pack("<II", 14, 20) + b"\x00" * 12


# ───────────── таблицы: колонки на своих местах ─────────────

def test_gridspan_не_сдвигает_колонки():
    """Объединённая шапка «Наименование» на две колонки: у прежнего чтения в
    шапке 4 ячейки, в данных 5, и цена оказывалась под заголовком «Кол-во»."""
    b = docx(таблица(
        ряд(яч("№"), яч("Наименование", '<w:gridSpan w:val="2"/>'), яч("Кол-во"), яч("Цена")),
        ряд(яч("1"), яч("Насос"), яч("ЦНС 38-176"), яч("2"), яч("1500")),
    ))
    rows = прочитать_документ(b)["rows"]
    assert rows[0] == ["№", "Наименование", "", "Кол-во", "Цена"], rows[0]
    assert rows[1][rows[0].index("Цена")] == "1500"
    assert rows[1][rows[0].index("Кол-во")] == "2"


def test_vmerge_переносит_наименование_вниз():
    """Объединение по вертикали: Word хранит в продолжении пустую ячейку, и
    вторая позиция оставалась без наименования."""
    b = docx(таблица(
        ряд(яч("Наименование"), яч("Размер"), яч("Цена")),
        ряд(яч("Подшипник", '<w:vMerge w:val="restart"/>'), яч("6205-2RS"), яч("350")),
        ряд(яч("", "<w:vMerge/>"), яч("6206-2RS"), яч("410")),
    ))
    rows = прочитать_документ(b)["rows"]
    assert rows[2] == ["Подшипник", "6206-2RS", "410"], rows[2]


def test_строка_из_одних_переносов_не_выдаётся():
    """Строка без своего текста — только перенесённое объединение — это не
    позиция: иначе объединение рождало бы фантомные строки спроса."""
    b = docx(таблица(
        ряд(яч("Раздел", '<w:vMerge w:val="restart"/>'), яч("Муфта")),
        ряд(яч("", "<w:vMerge/>"), яч("")),
    ))
    assert прочитать_документ(b)["rows"] == [["Раздел", "Муфта"]]


def test_gridbefore_ставит_пустые_ячейки_в_начало():
    b = docx(таблица(
        ряд(яч("Наименование"), яч("Кол-во"), яч("Цена")),
        ряд(яч("5"), яч("900"), свойства='<w:gridBefore w:val="1"/>'),
    ))
    rows = прочитать_документ(b)["rows"]
    assert rows[1] == ["", "5", "900"], rows[1]


def test_вложенная_таблица_идёт_своими_строками():
    """Рамка-таблица вокруг спецификации: прежняя регулярка рвала внешнюю строку
    на первой вложенной. Вложенные строки — сразу после внешней, и в текст
    внешней ячейки они не попадают."""
    вложенная = таблица(ряд(яч("Наименование"), яч("Цена")), ряд(яч("Муфта МУВП-3"), яч("7 800")))
    b = docx(таблица(ряд(f"<w:tc>{п('Спецификация к письму')}{вложенная}<w:p/></w:tc>")))
    rows = прочитать_документ(b)["rows"]
    assert rows == [["Спецификация к письму"], ["Наименование", "Цена"], ["Муфта МУВП-3", "7 800"]], rows


def test_таблица_внутри_элемента_управления_читается():
    """Повторяющийся раздел (w:sdt) оборачивает строки таблицы."""
    b = docx(таблица(ряд(яч("Наименование"), яч("Цена")),
                     f"<w:sdt><w:sdtPr/><w:sdtContent>{ряд(яч('Втулка'), яч('120'))}</w:sdtContent></w:sdt>"))
    assert прочитать_документ(b)["rows"][1] == ["Втулка", "120"]


def test_предел_строк_не_теряет_текст(monkeypatch):
    monkeypatch.setattr(read_word, "МАКС_СТРОК", 2)
    b = docx(таблица(*(ряд(яч(f"Позиция {k}"), яч(str(k))) for k in range(1, 5))))
    д = прочитать_документ(b)
    assert len(д["rows"]) == 2
    assert "Позиция 4" in д["text"], "сверх предела строки обязаны остаться в тексте"
    assert any("больше 2" in з for з in д["notes"])


# ───────────── текст ─────────────

def test_куски_слова_склеиваются_без_пробела():
    """Word рвёт слово на несколько w:t (правописание, правки). Прежняя склейка
    через пробел давала «На сос» — такое наименование не находится поиском."""
    абзац = "<w:p><w:r><w:t>На</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>сос ЦНС</w:t></w:r></w:p>"
    b = docx(абзац + таблица(ряд(f"<w:tc>{абзац}</w:tc>")))
    д = прочитать_документ(b)
    assert д["text"].splitlines()[0] == "Насос ЦНС"
    assert д["rows"] == [["Насос ЦНС"]]


def test_перевод_строки_и_табуляция():
    """w:br — новая строка текста, а внутри ячейки — пробел; позиции табуляции
    в свойствах абзаца (w:tabs/w:tab) знаков табуляции не дают."""
    абзац = ('<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
             "<w:r><w:t>Болт М12</w:t><w:br/><w:t>ГОСТ 7798</w:t><w:tab/><w:t>10 шт</w:t></w:r></w:p>")
    b = docx(абзац + таблица(ряд(f"<w:tc>{абзац}</w:tc>")))
    д = прочитать_документ(b)
    assert д["text"].splitlines()[:2] == ["Болт М12", "ГОСТ 7798\t10 шт"]
    assert д["rows"] == [["Болт М12 ГОСТ 7798\t10 шт"]]


def test_надпись_читается_один_раз():
    """Word пишет надпись дважды: DrawingML в mc:Choice и VML в mc:Fallback.
    Регулярка по w:t удваивала реквизиты и цены поставщика."""
    содержимое = f"<w:txbxContent>{п('Реквизиты: р/с 40702810000000000001')}</w:txbxContent>"
    b = docx(
        '<w:p><w:r><w:t>Письмо</w:t></w:r><w:r><mc:AlternateContent>'
        f'<mc:Choice Requires="wps"><w:drawing><wp:anchor><a:graphic><a:graphicData>'
        f'<wps:wsp><wps:txbx>{содержимое}</wps:txbx></wps:wsp></a:graphicData></a:graphic>'
        f'</wp:anchor></w:drawing></mc:Choice>'
        f'<mc:Fallback><w:pict><v:shape><v:textbox>{содержимое}</v:textbox></v:shape></w:pict></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p>')
    текст = прочитать_документ(b)["text"]
    assert текст.count("Реквизиты") == 1, текст
    assert текст.splitlines() == ["Письмо", "Реквизиты: р/с 40702810000000000001"]


def test_пустой_choice_уступает_fallback():
    b = docx('<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing/></mc:Choice>'
             f'<mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>{п("Цена 4 500")}'
             '</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>')
    assert прочитать_документ(b)["text"] == "Цена 4 500"


def test_удалённое_при_рецензировании_и_коды_полей_не_читаются():
    b = docx('<w:p><w:r><w:t>Цена </w:t></w:r>'
             '<w:del w:id="1"><w:r><w:delText>900</w:delText></w:r></w:del>'
             '<w:ins w:id="2"><w:r><w:t>950</w:t></w:r></w:ins>'
             '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t> руб.</w:t></w:r>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
    assert прочитать_документ(b)["text"] == "Цена 950 руб."


def test_перемещённое_при_рецензировании_читается_один_раз():
    """w:moveFrom держит обычный w:t (не delText): без пропуска перенесённый
    абзац читался бы дважды — на старом месте и на новом."""
    b = docx('<w:p><w:moveFrom w:id="1"><w:r><w:t>Муфта МУВП-3</w:t></w:r></w:moveFrom></w:p>'
             + п("Середина")
             + '<w:p><w:moveTo w:id="2"><w:r><w:t>Муфта МУВП-3</w:t></w:r></w:moveTo></w:p>')
    assert прочитать_документ(b)["text"].splitlines() == ["Середина", "Муфта МУВП-3"]


def test_колонтитулы_сноски_примечания_по_порядку():
    """Колонтитулы — перед телом, сноски и нижний колонтитул — после.
    Одинаковые колонтитулы первой и прочих страниц идут один раз."""
    шапка = f'<w:hdr xmlns:w="{W}">{п("ООО «Выдумка» ИНН 7700000000")}</w:hdr>'
    b = docx(п("Тело письма"), части={
        "word/header1.xml": шапка,
        "word/header2.xml": шапка,
        "word/footer1.xml": f'<w:ftr xmlns:w="{W}">{п("Цены действительны 30 дней")}</w:ftr>',
        "word/footnotes.xml": (f'<w:footnotes xmlns:w="{W}"><w:footnote w:type="separator" w:id="-1">'
                               f'<w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                               f'<w:footnote w:id="1">{п("С рамой и муфтой")}</w:footnote></w:footnotes>'),
        "word/comments.xml": f'<w:comments xmlns:w="{W}"><w:comment w:id="0">{п("Уточнить срок")}</w:comment></w:comments>',
    }, связи_документа=связи(
        связь("rId1", "header", "header1.xml"), связь("rId2", "header", "header2.xml"),
        связь("rId3", "footer", "footer1.xml"), связь("rId4", "footnotes", "footnotes.xml"),
        связь("rId5", "comments", "comments.xml")))
    текст = прочитать_документ(b)["text"]
    assert текст.splitlines() == ["ООО «Выдумка» ИНН 7700000000", "Тело письма", "С рамой и муфтой",
                                  "Цены действительны 30 дней", "Уточнить срок"], текст


def test_колонтитулы_находятся_и_без_связей():
    """Связей документа нет (битый пакет) — части ищутся по привычным именам."""
    b = docx(п("Тело"), части={"word/footer1.xml": f'<w:ftr xmlns:w="{W}">{п("Подвал")}</w:ftr>'})
    assert прочитать_документ(b)["text"].splitlines() == ["Тело", "Подвал"]


def test_smartart_читается():
    """Текст SmartArt лежит не в document.xml, а в diagrams/data1.xml."""
    данные = ('<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
              'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><dgm:ptLst>'
              '<dgm:pt modelId="1"><dgm:t><a:bodyPr/><a:p><a:r><a:t>Этап 1: дефектовка</a:t></a:r></a:p>'
              '</dgm:t></dgm:pt></dgm:ptLst></dgm:dataModel>')
    b = docx(п("Схема работ"), части={"word/diagrams/data1.xml": данные},
             связи_документа=связи(связь("rId7", "diagramData", "diagrams/data1.xml")))
    assert прочитать_документ(b)["text"].splitlines() == ["Схема работ", "Этап 1: дефектовка"]


def test_символы_шрифта_symbol():
    b = docx('<w:p><w:r><w:t>Допуск </w:t><w:sym w:font="Symbol" w:char="F0B1"/><w:t>0,05; </w:t>'
             '<w:sym w:font="Symbol" w:char="F0C6"/><w:t>20; 90</w:t><w:sym w:font="Symbol" w:char="F0B0"/>'
             '<w:sym w:font="Wingdings" w:char="F0FE"/></w:r></w:p>')
    assert прочитать_документ(b)["text"] == "Допуск ±0,05; Ø20; 90°"


# ───────────── картинки и метафайлы ─────────────

def test_картинки_по_порядку_без_повторов_и_без_метафайлов():
    """Растр — на распознавание в порядке появления в документе; одинаковый
    снимок — один раз; EMF распознавание не откроет, он в картинки не идёт."""
    первая, вторая = png(тон=1), png(тон=2)
    b = docx(картинка("rIdB") + картинка("rIdA"), части={
        "word/media/image1.png": первая,
        "word/media/image2.png": вторая,
        "word/media/image3.png": первая,                # тот же снимок ещё раз
        "word/media/image4.emf": emf(),
        "word/media/image10.png": png(тон=3),           # ни на что не ссылается
    }, связи_документа=связи(связь("rIdA", "image", "media/image1.png"),
                             связь("rIdB", "image", "media/image2.png")))
    д = прочитать_документ(b)
    assert [имя for имя, _ in д["images"]] == ["word/media/image2.png", "word/media/image1.png",
                                               "word/media/image10.png"]
    assert д["images"][0][1] == вторая
    assert д["reason"].endswith("читается распознаванием"), д["reason"]


def test_скан_без_текста_называет_причину():
    """Скан спецификации, вставленный в Word: текста нет, и это должно быть
    названо — иначе файл получает «пусто» и выпадает из распознавания."""
    b = docx(картинка("rId9"), части={"word/media/image1.jpeg": b"\xff\xd8\xff\xe0" + b"\x00" * 400},
             связи_документа=связи(связь("rId9", "image", "media/image1.jpeg")))
    д = прочитать_документ(b)
    assert д["text"] == "" and д["rows"] == []
    assert len(д["images"]) == 1
    assert "распознаванием" in д["reason"]


def test_текст_из_emf_собирается_строками():
    """Таблица Excel, вставленная в Word рисунком EMF: каждая ячейка — своя
    команда вывода текста. Строки собираются по высоте, ячейки — через
    табуляцию, куски одного слова (зазор 0) — вплотную."""
    рисунок = emf(
        emf_текст(500, 100, "Цена"), emf_текст(0, 101, "Наименование"),
        emf_текст(0, 300, "Под"), emf_текст(30, 300, "шипник"), emf_текст(500, 299, "350,00"),
    )
    b = docx(п("Приложение 1"), части={"word/media/image1.emf": рисунок})
    д = прочитать_документ(b)
    assert д["text"].splitlines() == ["Приложение 1", "Наименование\tЦена", "Подшипник\t350,00"], д["text"]
    assert д["images"] == []


def test_растр_внутри_emf_уходит_на_распознавание():
    """Скан, вставленный через буфер обмена, лежит EMF с растром внутри."""
    bmi = struct.pack("<IiiHHIIiiII", 40, 40, 40, 1, 24, 0, 0, 0, 0, 0, 0)
    точки = bytes(range(256)) * 18 + bytes(192)          # 40 × 40 × 3 = 4800 байт
    запись = (struct.pack("<II", 81, 80 + len(bmi) + len(точки)) + b"\x00" * 40
              + struct.pack("<IIII", 80, len(bmi), 80 + len(bmi), len(точки)) + b"\x00" * 16 + bmi + точки)
    b = docx(п("Скан"), части={"word/media/image1.emf": emf(запись)})
    картинки = прочитать_документ(b)["images"]
    assert len(картинки) == 1
    имя, bmp = картинки[0]
    assert bmp[:2] == b"BM" and bmp.endswith(точки)
    assert struct.unpack_from("<I", bmp, 10)[0] == 14 + 40, "смещение точек в BMP"


def test_вложенная_книга_отдаётся_своему_читателю():
    книга = b"PK\x03\x04" + "книга".encode()
    b = docx(п("См. таблицу"), части={"word/embeddings/Microsoft_Excel_Worksheet.xlsx": книга})
    assert прочитать_документ(b)["embedded"] == [("word/embeddings/Microsoft_Excel_Worksheet.xlsx", книга)]


# ───────────── разновидности OOXML ─────────────

def test_строгий_ooxml():
    """ISO 29500 Strict: другое пространство имён и у разметки, и у связей."""
    b = docx(таблица(ряд(яч("Наименование"), яч("Цена")), ряд(яч("Втулка"), яч("120"))) + картинка("rId3"),
             пр_w=W_STRICT, пр_r=R_STRICT, части={"word/media/image1.png": png()},
             связи_документа=связи(связь("rId3", "image", "media/image1.png", R_STRICT)))
    д = прочитать_документ(b)
    assert д["format"] == "docx-strict"
    assert д["rows"] == [["Наименование", "Цена"], ["Втулка", "120"]]
    assert [и for и, _ in д["images"]] == ["word/media/image1.png"]


def test_главная_часть_ищется_по_связям_а_не_по_имени():
    b = docx(п("Сборщик назвал часть иначе"), главная="content/main.xml")
    д = прочитать_документ(b)
    assert д["text"] == "Сборщик назвал часть иначе" and д["reason"] == ""


def test_altchunk_html_с_объединениями():
    """Веб-площадки собирают docx с altChunk: всё содержание — в HTML-части.
    В HTML ячейка с rowspan в следующей строке ОТСУТСТВУЕТ — без вставки её
    значения колонки правее съезжают влево."""
    html = ("<html><body><p>Приложение</p><table>"
            "<tr><th>№</th><th colspan=2>Наименование</th><th>Цена</th></tr>"
            "<tr><td>1</td><td rowspan=2>Подшипник</td><td>6205</td><td>350</td></tr>"
            "<tr><td>2</td><td>6206</td><td>410</td></tr></table></body></html>")
    b = docx(п("Шапка") + '<w:altChunk r:id="rIdA"/>', части={"word/afchunk.htm": html.encode()},
             связи_документа=связи(связь("rIdA", "aFChunk", "afchunk.htm")))
    д = прочитать_документ(b)
    assert д["rows"] == [["№", "Наименование", "", "Цена"], ["1", "Подшипник", "6205", "350"],
                         ["2", "Подшипник", "6206", "410"]], д["rows"]
    assert д["text"].splitlines()[:2] == ["Шапка", "Приложение"]


def test_word_2003_xml():
    """Word 2003 XML: vmerge строчными, колонтитул внутри sectPr, картинка
    base64 в w:binData."""
    пр = "http://schemas.microsoft.com/office/word/2003/wordml"
    картинка_b64 = base64.b64encode(png()).decode()

    def я(т: str, св: str = "") -> str:
        return f"<w:tc><w:tcPr>{св}</w:tcPr><w:p><w:r><w:t>{т}</w:t></w:r></w:p></w:tc>"
    начало, продолжение = '<w:vmerge w:val="restart"/>', "<w:vmerge/>"
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Word.Document"?>'
           f'<w:wordDocument xmlns:w="{пр}" xmlns:v="urn:schemas-microsoft-com:vml"><w:body>'
           f'<w:p><w:r><w:pict><w:binData w:name="wordml://1.png">{картинка_b64}</w:binData>'
           f'<v:shape><v:imagedata src="wordml://1.png"/></v:shape></w:pict></w:r></w:p>'
           f"<w:tbl><w:tr>{я('Наименование')}{я('Цена')}</w:tr>"
           f"<w:tr>{я('Шпонка', начало)}{я('40')}</w:tr>"
           f"<w:tr>{я('', продолжение)}{я('45')}</w:tr></w:tbl>"
           f'<w:sectPr><w:hdr w:type="odd"><w:p><w:r><w:t>Бланк поставщика</w:t></w:r></w:p></w:hdr>'
           f"</w:sectPr></w:body></w:wordDocument>")
    д = прочитать_документ(xml.encode())
    assert д["format"] == "wordml2003"
    assert д["rows"][2] == ["Шпонка", "45"]
    assert "Бланк поставщика" in д["text"]
    assert [д_ for _, д_ in д["images"]] == [png()]


def test_плоский_пакет():
    """«XML-документ Word» (pkg:package): все части в одном XML."""
    пр = "http://schemas.microsoft.com/office/2006/xmlPackage"
    xml = (f'<?xml version="1.0"?><pkg:package xmlns:pkg="{пр}">'
           f'<pkg:part pkg:name="/_rels/.rels"><pkg:xmlData>'
           f'{связи(связь("rId1", "officeDocument", "word/document.xml")).split("?>", 1)[1]}'
           f'</pkg:xmlData></pkg:part>'
           f'<pkg:part pkg:name="/word/document.xml"><pkg:xmlData>'
           f'{документ(п("Плоский пакет: цена 700")).split("?>", 1)[1]}</pkg:xmlData></pkg:part>'
           f'<pkg:part pkg:name="/word/media/image1.png"><pkg:binaryData>{base64.b64encode(png()).decode()}'
           f'</pkg:binaryData></pkg:part></pkg:package>')
    д = прочитать_документ(xml.encode())
    assert д["format"] == "flat-opc"
    assert д["text"] == "Плоский пакет: цена 700"
    assert len(д["images"]) == 1


def test_mht_под_видом_doc():
    """Веб-архив Word, переименованный в .doc: HTML и картинки частями MIME."""
    html = ("<html><head><title>x</title><style>p{}</style></head><body>"
            "<table><tr><td>Наименование</td><td>Цена</td></tr>"
            "<tr><td>Сальник</td><td>95</td></tr></table></body></html>")
    картинка_b64 = base64.b64encode(png()).decode()
    mht = ("MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=\"ГРАНИЦА\"\r\n\r\n"
           "--ГРАНИЦА\r\nContent-Type: text/html; charset=\"utf-8\"\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
           f"{html}\r\n--ГРАНИЦА\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n"
           f"Content-Location: file:///C:/x_files/image001.png\r\n\r\n{картинка_b64}\r\n--ГРАНИЦА--\r\n")
    д = прочитать_документ(mht.replace("ГРАНИЦА", "b0undary").encode("utf-8"))
    assert д["format"] == "mht"
    assert д["rows"] == [["Наименование", "Цена"], ["Сальник", "95"]]
    assert [x for _, x in д["images"]] == [png()]


# ───────────── OpenDocument ─────────────

ODF_ПРОСТРАНСТВА = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:dc="http://purl.org/dc/elements/1.1/"'
)


def odt(тело: str, шапка: str = "", части: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", f'<?xml version="1.0" encoding="UTF-8"?><office:document-content '
                                  f"{ODF_ПРОСТРАНСТВА}><office:body><office:text>{тело}"
                                  f"</office:text></office:body></office:document-content>")
        z.writestr("styles.xml", f'<?xml version="1.0"?><office:document-styles {ODF_ПРОСТРАНСТВА}>'
                                 f'<office:master-styles><style:master-page style:name="Standard">'
                                 f"<style:header>{шапка}</style:header></style:master-page>"
                                 f"</office:master-styles></office:document-styles>")
        for имя, данные in (части or {}).items():
            z.writestr(имя, данные)
    return buf.getvalue()


def test_odt_текст_хвосты_сноски_надписи_колонтитул():
    """ODF — смешанное содержимое: текст лежит и в хвостах элементов. Без них
    «Насос <span>ЦНС</span> 38-176» терял бы «38-176». Удалённое при
    рецензировании (text:tracked-changes) и номер сноски в текст не идут."""
    b = odt(
        "<text:tracked-changes><text:changed-region><text:deletion><text:p>УДАЛЁННОЕ</text:p>"
        "</text:deletion></text:changed-region></text:tracked-changes>"
        "<text:p>Насос <text:span>ЦНС</text:span> 38-176<text:s text:c=\"2\"/>2 шт"
        "<text:note><text:note-citation>1</text:note-citation><text:note-body>"
        "<text:p>с рамой</text:p></text:note-body></text:note>.</text:p>"
        "<text:p><draw:frame><draw:text-box><text:p>Цена в надписи 125 000</text:p></draw:text-box>"
        '</draw:frame><draw:frame><draw:image xlink:href="Pictures/a.png"/></draw:frame></text:p>'
        "<text:p>Примечание<office:annotation><dc:creator>Иванов</dc:creator>"
        "<text:p>проверить срок</text:p></office:annotation></text:p>",
        шапка="<text:p>АО «Небывалое»</text:p>", части={"Pictures/a.png": png()})
    д = прочитать_документ(b)
    assert д["format"] == "odt"
    assert д["text"].splitlines() == ["АО «Небывалое»", "Насос ЦНС 38-176 2 шт.", "с рамой",
                                      "Цена в надписи 125 000", "Примечание", "проверить срок"], д["text"]
    assert [и for и, _ in д["images"]] == ["Pictures/a.png"]


def test_odt_таблица_с_объединениями_и_повторами():
    """covered-table-cell держит место объединённой ячейки; значение
    объединения вниз переносится; number-columns-repeated раскрывается."""
    def я(т: str, атр: str = "") -> str:
        return f"<table:table-cell {атр}><text:p>{т}</text:p></table:table-cell>"
    вширь, вниз, повтор = ('table:number-columns-spanned="2"', 'table:number-rows-spanned="2"',
                           'table:number-columns-repeated="2"')
    b = odt('<table:table><table:table-column table:number-columns-repeated="3"/>'
            f"<table:table-header-rows><table:table-row>{я('Наименование', вширь)}"
            f"<table:covered-table-cell/>{я('Цена')}</table:table-row></table:table-header-rows>"
            f"<table:table-row>{я('Подшипник', вниз)}{я('6205')}{я('350')}</table:table-row>"
            f"<table:table-row><table:covered-table-cell/>{я('6206')}{я('410')}</table:table-row>"
            f"<table:table-row>{я('—', повтор)}{я('0')}"
            '<table:table-cell table:number-columns-repeated="1000"/></table:table-row>'
            "</table:table>")
    assert прочитать_документ(b)["rows"] == [
        ["Наименование", "", "Цена"], ["Подшипник", "6205", "350"],
        ["Подшипник", "6206", "410"], ["—", "—", "0"]]


def test_odt_встроенная_таблица_calc():
    """Таблица Calc, вставленная объектом в .odt, — свой content.xml в папке
    объекта; читается той же разметкой table:table."""
    объект = (f'<?xml version="1.0"?><office:document-content {ODF_ПРОСТРАНСТВА}><office:body>'
              "<office:spreadsheet><table:table><table:table-row><table:table-cell><text:p>Клапан</text:p>"
              "</table:table-cell><table:table-cell><text:p>3</text:p></table:table-cell></table:table-row>"
              "</table:table></office:spreadsheet></office:body></office:document-content>")
    b = odt('<text:p><draw:frame><draw:object xlink:href="./Object 1"/></draw:frame></text:p>',
            части={"Object 1/content.xml": объект})
    assert прочитать_документ(b)["rows"] == [["Клапан", "3"]]


def test_odt_зашифрованный_называет_причину():
    b = odt("<text:p>x</text:p>", части={
        "META-INF/manifest.xml": "<manifest:manifest><manifest:encryption-data/></manifest:manifest>"})
    assert "паролем" in прочитать_документ(b)["reason"]


# ───────────── битые файлы ─────────────

def test_недопустимый_знак_xml_не_роняет_документ():
    """Сторонние генераторы пишут \\x0b и &#1; — XML 1.0 их запрещает, expat
    отказывает целиком, хотя Word такой файл открывает."""
    сырой = документ(п("Начало") + п("Вертикальная\x0bтабуляция") + п("Конец &#1;текста")).encode()
    д = прочитать_документ(docx(сырой_документ=сырой))
    assert д["text"].splitlines() == ["Начало", "Вертикальнаятабуляция", "Конец текста"]
    assert any("недопустимые знаки" in з for з in д["notes"])


def test_оборванный_document_xml_читается_до_обрыва():
    сырой = документ(п("Первая позиция") + п("Вторая позиция") + п("Третья позиция")).encode()
    обрыв = сырой[:сырой.index("Третья".encode())]
    д = прочитать_документ(docx(сырой_документ=обрыв))
    assert д["text"].splitlines()[:2] == ["Первая позиция", "Вторая позиция"]
    assert any("до места обрыва" in з for з in д["notes"])


def test_оборванный_zip_читается_по_локальным_заголовкам():
    """Оборванная загрузка теряет хвост ZIP — а с ним центральный каталог.
    zipfile отказывает целиком, хотя document.xml в начале файла цел."""
    b = docx(таблица(ряд(яч("Наименование"), яч("Цена")), ряд(яч("Гайка"), яч("12"))),
             части={"word/media/image1.png": png()})
    обрыв = b[:b.rindex(b"PK\x01\x02") - 10]              # каталог и хвост последнего участника
    д = прочитать_документ(обрыв)
    assert д["rows"] == [["Наименование", "Цена"], ["Гайка", "12"]]
    assert any("ZIP без каталога" in з for з in д["notes"])


def test_cp1251_под_объявлением_utf8():
    """Генератор склеил XML строками: объявлен UTF-8, байты — cp1251."""
    сырой = документ(п("Задвижка 30с41нж Ду100 — 4 шт.")).encode("cp1251")
    assert прочитать_документ(docx(сырой_документ=сырой))["text"] == "Задвижка 30с41нж Ду100 — 4 шт."


def test_dtd_с_сущностями_вырезается():
    """Word не пишет DTD. Объявление сущностей в части документа — «миллиард
    смехов»; читатель обязан не раздуть его и не упасть."""
    бомба = ('<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "ААААААААААААААААААААААААААААААААААААААААААА">'
             + "".join(f'<!ENTITY {x} "&{y};&{y};&{y};&{y};&{y};&{y};&{y};&{y};&{y};&{y};">'
                       for x, y in zip("bcdefghi", "abcdefgh"))
             + f']><w:document xmlns:w="{W}"><w:body>{п("Настоящий текст")}<w:p><w:r><w:t>&i;</w:t></w:r></w:p>'
             f'{п("Хвост после бомбы &amp; ещё")}</w:body></w:document>')
    д = прочитать_документ(docx(сырой_документ=бомба.encode()))
    assert д["text"].splitlines() == ["Настоящий текст", "Хвост после бомбы & ещё"], д["text"][:200]
    assert any("DTD" in з for з in д["notes"])


def test_зашифрованный_docx_называет_причину():
    """Docx с паролем — это не ZIP, а OLE2 с потоком EncryptedPackage."""
    b = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 100
    д = прочитать_документ(b)
    assert "паролем" in д["reason"] and д["text"] == ""


def test_книга_excel_не_выдаётся_за_документ():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_rels/.rels", связи(связь("rId1", "officeDocument", "xl/workbook.xml")))
        z.writestr("xl/workbook.xml", "<workbook/>")
    assert "книга Excel" in прочитать_документ(buf.getvalue())["reason"]


def test_никогда_не_бросает():
    """Исключение в читателе — файл, выпавший из прогона целиком. Любой вход
    даёт словарь с причиной, а не падение."""
    глубокий = документ("<w:sdt><w:sdtContent>" * 3000 + п("дно") + "</w:sdtContent></w:sdt>" * 3000)
    плохие = [
        b"", None, "строка вместо байтов", b"PK\x03\x04" + b"\xff" * 64, bytes(range(256)) * 4,
        docx(сырой_документ=b"\x00\x01\x02 not xml"),
        docx(таблица(ряд(яч("x", '<w:gridSpan w:val="99999999"/>'))) + таблица(ряд(яч("y", '<w:gridSpan w:val="abc"/>')))),
        docx(сырой_документ=глубокий.encode()),
        docx(п("x"), связи_документа="<не xml"),
        docx('<w:altChunk r:id="нет"/>'),
        b"<?xml version='1.0'?><w:wordDocument xmlns:w='x'><w:body><w:p><w:r><w:binData>!!!</w:binData>",
        emf(b"\x54\x00\x00\x00\xff\xff\xff\x7f"), b"\xd7\xcd\xc6\x9a" + b"\x00" * 40,
    ]
    for b in плохие:
        д = прочитать_документ(b)
        assert set(д) == КЛЮЧИ, (b[:20] if isinstance(b, bytes) else b, д)
        assert д["reason"] or д["rows"] or д["text"], "пустота без причины — потерянный файл"
    широкая = прочитать_документ(плохие[6])["rows"]
    assert len(широкая[0]) == read_word.МАКС_ПРОЛЁТ and широкая[1] == ["y"]


def test_причина_пуста_только_когда_прочитано():
    assert прочитать_документ(docx(п("Текст")))["reason"] == ""
    assert прочитать_документ(docx(""))["reason"] == "документ без текста, таблиц и картинок"
