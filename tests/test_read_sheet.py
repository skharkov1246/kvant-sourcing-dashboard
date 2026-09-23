"""Книга любого вида — в строки таблицы (library/read_sheet.py).

Каждый непрочитанный файл — потерянная цена: в книгах лежат предложения
поставщиков. Прежний читатель (indexer.rows_from_xlsx) терял объединённые
ячейки, формулы без значения, даты по стилю, .xlsb, .ods и книги, которые
openpyxl отвергает. Здесь на каждую потерю — свой корпус.

Книги собираются кодом теста из XML-строк через zipfile: openpyxl в гейте не
ставится (gate.yml), и главный путь обязан работать без него. Корпуса придуманы
(CLAUDE.md, правило 18).
"""
from __future__ import annotations

import importlib.util
import io
import pathlib
import random
import struct
import sys
import zipfile
from xml.sax.saxutils import escape

import pytest

from library import read_sheet as rs

ROOT = pathlib.Path(__file__).resolve().parents[1]
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ТИП_ЛИСТА = NS_R + "/worksheet"


# ------------------------------------------------------------------ сборка xlsx


def c(ref, значение=None, *, s=None, f=None, fattrs="", t=None) -> str:
    """Ячейка <c>. Строка — инлайн-строкой, число — <v>, f — формула."""
    атр = f' r="{ref}"' if ref else ""
    if s is not None:
        атр += f' s="{s}"'
    внутри = ""
    if f is not None:
        внутри = f"<f{fattrs}>{escape(f)}</f>" if f else f"<f{fattrs}/>"
    if t is not None:
        return f'<c{атр} t="{t}">{внутри}<v>{escape(str(значение))}</v></c>'
    if isinstance(значение, str):
        return f'<c{атр} t="inlineStr">{внутри}<is><t>{escape(значение)}</t></is></c>'
    if isinstance(значение, (int, float)):
        return f"<c{атр}>{внутри}<v>{значение}</v></c>"
    return f"<c{атр}>{внутри}</c>"


def лист(ряды, слияния=(), ns=NS) -> str:
    """ряды: список строк (номер = место в списке + 1) или словарь {номер: ячейки}."""
    пары = ряды.items() if isinstance(ряды, dict) else enumerate(ряды, 1)
    части = [f'<worksheet xmlns="{ns}"><sheetData>']
    for r, ячейки in пары:
        части.append(f'<row r="{r}">' + "".join(ячейки) + "</row>")
    части.append("</sheetData>")
    if слияния:
        части.append("<mergeCells>" + "".join(f'<mergeCell ref="{x}"/>' for x in слияния) + "</mergeCells>")
    части.append("</worksheet>")
    return "".join(части)


def стили(*номера, форматы=None) -> str:
    """cellXfs по порядку: номер формата каждого стиля (s="0", s="1", ...)."""
    nf = "".join(f'<numFmt numFmtId="{k}" formatCode="{escape(v, {chr(34): "&quot;"})}"/>'
                 for k, v in (форматы or {}).items())
    return (f'<styleSheet xmlns="{NS}">' + (f"<numFmts>{nf}</numFmts>" if nf else "")
            + "<cellXfs>" + "".join(f'<xf numFmtId="{n}"/>' for n in номера) + "</cellXfs></styleSheet>")


def xlsx(листы, *, общие=None, стиль=None, d1904=False, ns=NS, описание=True, лишнее=None,
         сжатие=zipfile.ZIP_DEFLATED) -> bytes:
    """листы: [(имя, разметка, state или None)]."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", сжатие) as z:
        if описание:
            z.writestr("[Content_Types].xml",
                       "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
            z.writestr("_rels/.rels",
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" Target="xl/workbook.xml"/>'
                       "</Relationships>")
            опись = "".join(f'<sheet name="{escape(имя)}" sheetId="{i}" r:id="rId{i}"'
                            + (f' state="{st}"' if st else "") + "/>"
                            for i, (имя, _, st) in enumerate(листы, 1))
            z.writestr("xl/workbook.xml",
                       f'<workbook xmlns="{ns}" xmlns:r="{NS_R}">'
                       + ('<workbookPr date1904="1"/>' if d1904 else "")
                       + f"<sheets>{опись}</sheets></workbook>")
            связи = "".join(f'<Relationship Id="rId{i}" Type="{ТИП_ЛИСТА}" Target="worksheets/sheet{i}.xml"/>'
                            for i in range(1, len(листы) + 1))
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       + связи + "</Relationships>")
        if общие is not None:
            z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">' + "".join(общие) + "</sst>")
        if стиль:
            z.writestr("xl/styles.xml", стиль)
        for k, v in (лишнее or {}).items():
            z.writestr(k, v)
        for i, (_, разметка, _) in enumerate(листы, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", разметка)
    return buf.getvalue()


def тело(строки):
    """Строки без меток листов."""
    return [r for r in строки if not rs.это_метка(r)]


# ------------------------------------------------------------------ xlsx: основа


def test_колонки_стоят_по_адресу_а_не_по_порядку():
    """Пустые ячейки в разметке не пишутся. Без адреса r="C2" цена съезжает в
    колонку B, под заголовок количества (rows_from_xlsx_raw теряет колонки так)."""
    b = xlsx([("КП", лист([[c("A1", "Наименование"), c("C1", "Цена")],
                            [c("A2", "Прокладка"), c("C2", 12.5)]]), None)])
    строки, текст, причина = rs.прочитать_книгу(b)
    assert строки == [["№ лист 1 «КП»"], ["Наименование", "", "Цена"], ["Прокладка", "", "12.5"]]
    assert (текст, причина) == ("", "")


def test_ячейки_без_адреса_идут_подряд():
    b = xlsx([("Л", f'<worksheet xmlns="{NS}"><sheetData><row><c t="inlineStr"><is><t>а</t></is></c>'
                    "<c><v>2</v></c></row><row><c><v>3</v></c></row></sheetData></worksheet>", None)])
    assert тело(rs.прочитать_книгу(b)[0]) == [["а", "2"], ["3"]]


def test_общие_строки_прогоны_фонетика_и_управляющие_знаки():
    """Прогоны <r> склеиваются, фонетика <rPh> в текст не идёт, _x000D_ разворачивается."""
    общие = ["<si><t>Насос</t></si>",
             "<si><r><t>ЦНС </t></r><r><rPr><b/></rPr><t>38-176</t></r><rPh><t>ФОНЕТИКА</t></rPh></si>",
             "<si><t>строка_x000D_вторая</t></si>"]
    b = xlsx([("Л", лист([[c("A1", 0, t="s"), c("B1", 1, t="s"), c("C1", 2, t="s"), c("D1", 99, t="s")]]), None)],
             общие=общие)
    # D1 ссылается на строку 99 из трёх: ячейка пустая, но лист не «испорчен».
    assert rs.прочитать_книгу(b) == ([["№ лист 1 «Л»"], ["Насос", "ЦНС 38-176", "строка\rвторая"]], "", "")


def test_числа_булевы_и_ошибки():
    """Дробь из разметки 12.300000000000001 показывается как в Excel — 12.3."""
    b = xlsx([("Л", лист([[c("A1", 12.300000000000001), c("B1", 10),
                           c("C1", "1", t="b"), c("D1", "#N/A", t="e"), c("E1", "1E-3", t="n")]]), None)])
    assert тело(rs.прочитать_книгу(b)[0]) == [["12.3", "10", "TRUE", "#N/A", "0.001"]]


def test_метки_листов_и_разбор_по_листам():
    """У каждого листа своя метка; по_листам раскладывает строки обратно — чтобы
    каждый лист получил свою шапку, а не колонки первого."""
    b = xlsx([("Спецификация", лист([[c("A1", "Наименование")], [c("A2", "Насос")]]), None),
              ("Прайс", лист([[c("A1", "Цена")]]), None)])
    строки = rs.прочитать_книгу(b)[0]
    assert строки[0] == ["№ лист 1 «Спецификация»"]
    assert ["№ лист 2 «Прайс»"] in строки
    листы = rs.по_листам(строки)
    assert [м for м, _ in листы] == ["№ лист 1 «Спецификация»", "№ лист 2 «Прайс»"]
    assert листы[1][1] == [["Цена"]]


def test_метка_не_становится_позицией_у_индексатора():
    """Метка начинается с «№», и indexer.NOISE_ROW отсеивает её как шум. Проверка
    на настоящем разборе позиций: вызывающий может не знать о метках вовсе."""
    pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")
    pytest.importorskip("requests", reason="индексатор импортирует requests")
    sys.path.insert(0, str(ROOT / "library"))
    spec = importlib.util.spec_from_file_location("indexer_rs", ROOT / "library" / "indexer.py")
    ix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ix)
    # Без шапки разбор берёт самую длинную ячейку строки — метка длиннее позиций.
    b = xlsx([("Материалы и запасные части к насосам", лист([[c("A1", "Насос ЦНС 38-176")],
                                                             [c("A2", "Задвижка 30с41нж")]]), None)])
    строки = rs.прочитать_книгу(b)[0]
    assert ix.NOISE_ROW.match(строки[0][0])
    имена = [p["item_name"] for p in ix.items_from_rows(строки)]
    assert имена == ["Насос ЦНС 38-176", "Задвижка 30с41нж"], имена


def test_скрытый_лист_читается_и_идёт_после_видимых():
    """Скрытый лист чаще справочник выпадающих списков: его читают, но в предел
    строк он идёт последним, а метка его называет."""
    b = xlsx([("Справочник", лист([[c("A1", "USD")]]), "hidden"),
              ("КП", лист([[c("A1", "Насос")]]), None),
              ("Тайный", лист([[c("A1", "EUR")]]), "veryHidden")])
    к = rs.разобрать_книгу(b)
    метки = [r[0] for r in к.строки if rs.это_метка(r)]
    assert метки == ["№ лист 2 «КП»", "№ лист 1 «Справочник» (скрытый)", "№ лист 3 «Тайный» (скрытый)"]
    assert к.счёт["скрытых"] == 2
    assert к.причина == ""


def test_книга_без_описания_как_выгрузка_учётной_системы():
    """18 файлов result.xlsx (замер 18.09.2026) без описания книги и связей.
    Листы берутся по именам в естественном порядке: sheet2 раньше sheet10."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet10.xml", лист([[c("A1", "десятый")]]))
        z.writestr("xl/worksheets/sheet2.xml", лист([[c("A1", "второй")]]))
    строки = rs.прочитать_книгу(buf.getvalue())[0]
    assert строки == [["№ лист 1 «sheet2»"], ["второй"], ["№ лист 2 «sheet10»"], ["десятый"]]


def test_строгий_ooxml():
    """Strict OOXML пишет другое пространство имён — разбор идёт по местным именам."""
    строгое = "http://purl.oclc.org/ooxml/spreadsheetml/main"
    b = xlsx([("Л", лист([[c("A1", "Насос"), c("B1", 5)]], ns=строгое), None)], ns=строгое)
    assert тело(rs.прочитать_книгу(b)[0]) == [["Насос", "5"]]


def test_связи_с_абсолютным_путём_и_другим_регистром():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{NS}" xmlns:r="{NS_R}"><sheets>'
                                      '<sheet name="Прайс" sheetId="7" r:id="rId3"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'<Relationship Id="rId3" Type="{ТИП_ЛИСТА}" Target="/xl/worksheets/Data.XML"/></Relationships>')
        z.writestr("xl/worksheets/data.xml", лист([[c("A1", "Кольцо")]]))
    assert rs.прочитать_книгу(buf.getvalue())[0] == [["№ лист 1 «Прайс»"], ["Кольцо"]]


# ------------------------------------------------------------------ объединения


def шапка():
    return [c("A1", "№"), c("B1", "Наименование"), c("C1", "Вариант"), c("D1", "Цена")]


def test_наименование_доходит_до_каждого_варианта_цены():
    """Наименование слито на три строки: оригинал, аналог 1, аналог 2. Без
    размножения две строки из трёх остаются без имени и выпадают из позиций."""
    b = xlsx([("КП", лист([шапка(),
                           [c("A2", 1), c("B2", "Насос ЦНС 38-176"), c("C2", "оригинал"), c("D2", 1500)],
                           [c("C3", "аналог 1"), c("D3", 1200)],
                           [c("C4", "аналог 2"), c("D4", 900)]], слияния=["B2:B4", "A2:A4"]), None)])
    к = rs.разобрать_книгу(b)
    assert тело(к.строки)[1:] == [["1", "Насос ЦНС 38-176", "оригинал", "1500"],
                                  ["", "Насос ЦНС 38-176", "аналог 1", "1200"],
                                  ["", "Насос ЦНС 38-176", "аналог 2", "900"]]
    # Номер п/п — число: он не размножается, как не утраивается цена комплекта.
    assert к.счёт["объединений"] == 2 and к.счёт["размножено"] == 2


def test_высокая_строка_не_удваивает_позицию():
    """Позиция в две строки высотой, где слиты ВСЕ ячейки, — одна позиция. Буквальное
    размножение («вся») делает из неё две и удваивает спрос."""
    ряды = {1: шапка(), 2: [c("A2", 1), c("B2", "Кольцо"), c("C2", "оригинал"), c("D2", 40)],
            4: [c("A4", 2), c("B4", "Шайба"), c("D4", 3)]}
    слияния = ["A2:A3", "B2:B3", "C2:C3", "D2:D3"]
    b = xlsx([("КП", лист(ряды, слияния), None)])
    assert тело(rs.прочитать_книгу(b)[0])[1:] == [["1", "Кольцо", "оригинал", "40"], ["2", "Шайба", "", "3"]]
    вся = тело(rs.прочитать_книгу(b, объединения="вся")[0])
    assert вся[1:3] == [["1", "Кольцо", "оригинал", "40"]] * 2


def test_подпись_раздела_не_растягивается_в_колонку_наименования():
    """«Раздел 1. Насосы» на всю ширину: растянутая, она попадает в колонку
    наименования (B) и становится позицией. Слияние на весь ряд (A2:XFD2 — 16 384
    колонки) и буквально размножается только до края данных, а не на весь ряд."""
    b = xlsx([("КП", лист({1: шапка(), 2: [c("A2", "Раздел 1. Насосы")],
                           3: [c("A3", 1), c("B3", "Насос"), c("D3", 10)]}, ["A2:XFD2"]), None)])
    assert тело(rs.прочитать_книгу(b)[0])[1] == ["Раздел 1. Насосы"]
    assert тело(rs.прочитать_книгу(b, объединения="вся")[0])[1] == ["Раздел 1. Насосы"] * 4


def test_шапка_в_две_строки_только_вширь():
    """«Цена» над «руб.» и «USD» достаётся обеим колонкам; «Наименование», слитое
    по вертикали, во вторую строку шапки не идёт — иначе в теле появится позиция
    «Наименование руб. USD»."""
    b = xlsx([("КП", лист([[c("A1", "Наименование"), c("B1", "Цена")],
                           [c("B2", "руб."), c("C2", "USD")],
                           [c("A3", "Насос"), c("B3", 100), c("C3", 1.25)]], ["A1:A2", "B1:C1"]), None)])
    assert тело(rs.прочитать_книгу(b)[0]) == [["Наименование", "Цена", "Цена"],
                                              ["", "руб.", "USD"],
                                              ["Насос", "100", "1.25"]]


def test_цена_комплекта_не_утраивается():
    """Цена, слитая на три строки комплектующих, — цена комплекта. Размноженная,
    она утроила бы сумму закупки (CLAUDE.md: «не умножай цену одного лота»)."""
    b = xlsx([("КП", лист([шапка(),
                           [c("A2", 1), c("B2", "вал"), c("D2", 5000)],
                           [c("A3", 2), c("B3", "колесо")],
                           [c("A4", 3), c("B4", "корпус")]], ["D2:D4"]), None)])
    строки = тело(rs.прочитать_книгу(b)[0])
    assert [r[3] if len(r) > 3 else "" for r in строки[1:]] == ["5000", "", ""]


def test_без_размножения_как_в_файле():
    b = xlsx([("КП", лист([шапка(), [c("A2", 1), c("B2", "Насос"), c("D2", 1)],
                           [c("A3", 2), c("D3", 2)]], ["B2:B3"]), None)])
    assert тело(rs.прочитать_книгу(b, объединения="нет")[0])[2] == ["2", "", "", "2"]
    assert тело(rs.прочитать_книгу(b)[0])[2] == ["2", "Насос", "", "2"]


def test_объединения_обрезанного_листа_читаются_из_хвоста(monkeypatch):
    """Объединения записаны ПОСЛЕ всех строк. Лист, оборванный на пределе, до них
    не дочитан, и без поиска по хвосту имя позиции снова теряется."""
    monkeypatch.setattr(rs, "МАКС_СТРОК_ЛИСТА", 3)
    ряды = [[c("A1", 1), c("B1", "Насос"), c("C1", 10)], [c("A2", 2), c("C2", 20)]]
    ряды += [[c(f"A{r}", r), c(f"B{r}", f"поз {r}")] for r in range(3, 8)]
    к = rs.разобрать_книгу(xlsx([("Л", лист(ряды, ["B1:B2"]), None)]))
    assert тело(к.строки)[1] == ["2", "Насос", "20"]
    assert len(тело(к.строки)) == 3
    assert "листов обрезано по пределу: 1" in к.причина


# ------------------------------------------------------------------ формулы


def test_формула_без_значения_пересчитывается():
    """openpyxl пишет формулы без значения. data_only отдаёт None — цена пропадала."""
    b = xlsx([("КП", лист([[c("A1", "Кольцо"), c("B1", 3), c("C1", 50), c("D1", f="B1*C1")]]), None)])
    к = rs.разобрать_книгу(b)
    assert тело(к.строки) == [["Кольцо", "3", "50", "150"]]
    assert к.счёт["формул_пересчитано"] == 1 and к.причина == ""


def test_сумма_ссылка_на_другой_лист_и_проценты():
    b = xlsx([("КП", лист([[c("A1", 100)], [c("A2", 200)], [c("A3", "текст")],
                           [c("B1", f="SUM(A1:A3)"), c("C1", f="B1*'Курс ЦБ'!B1"), c("D1", f="A1*(1+20%)")]]), None),
              ("Курс ЦБ", лист([[c("A1", "USD"), c("B1", 92.5)]]), None)])
    строки = rs.прочитать_книгу(b)[0]
    assert строки[1] == ["100", "300", "27750", "120"]


def test_общая_формула_сдвигается_по_области():
    """Общая формула записана один раз у первой ячейки; D3 и D4 ссылаются на неё
    номером и без сдвига адресов посчитали бы цену первой строки трижды."""
    ряды = [[c("B1", 2), c("C1", 10), c("D1", f="B1*C1+$E$1", fattrs=' t="shared" ref="D1:D3" si="0"'),
             c("E1", 1)],
            [c("B2", 3), c("C2", 10), c("D2", f="", fattrs=' t="shared" si="0"')],
            [c("B3", 4), c("C3", 10), c("D3", f="", fattrs=' t="shared" si="0"')]]
    строки = тело(rs.прочитать_книгу(xlsx([("Л", лист(ряды), None)]))[0])
    assert [r[3] for r in строки] == ["21", "31", "41"]


def test_сохранённое_значение_главнее_пересчёта():
    b = xlsx([("Л", лист([[c("A1", 2), c("B1", 777, f="A1*2")]]), None)])
    assert тело(rs.прочитать_книгу(b)[0]) == [["2", "777"]]


def test_ноль_xlsxwriter_пересчитывается():
    """xlsxwriter пишет <v>0</v> у каждой формулы. Ноль-цена отсеивается как
    отсутствующая, а настоящая цена стоит в формуле."""
    b = xlsx([("Л", лист([[c("A1", 3), c("B1", 50), c("C1", 0, f="A1*B1"), c("D1", 0, f="VLOOKUP(A1,X,2)")]]), None)])
    к = rs.разобрать_книгу(b)
    assert тело(к.строки) == [["3", "50", "150", "0"]]
    assert к.причина == ""                  # у D1 было сохранённое значение — это не потеря


def test_неизвестная_функция_не_выдумывает_цену_и_считается():
    """VLOOKUP не пересчитывается. Ячейка остаётся пустой, а не получает цифры из
    текста формулы («=VLOOKUP(A2,...)» дал бы разбору цены «2»), и потеря названа."""
    b = xlsx([("Л", лист([[c("A1", "Насос"), c("B1", f="VLOOKUP(A1,Прайс!A:B,2,0)"), c("C1", 5)]]), None)])
    к = rs.разобрать_книгу(b)
    assert тело(к.строки) == [["Насос", "", "5"]]
    assert к.причина == "формул без значения 1"


def test_цикл_не_виснет_и_считается():
    b = xlsx([("Л", лист([[c("A1", f="B1+1"), c("B1", f="A1+1"), c("C1", "x")]]), None)])
    к = rs.разобрать_книгу(b)
    assert к.счёт["формул_без_значения"] == 2
    assert тело(к.строки) == [["", "", "x"]]


def test_ошибки_excel_iferror_round_if_и_сцепка():
    """ROUND считает по десятичной записи, как Excel: 2,675 -> 2,68 (round() Питона даёт 2,67)."""
    ряды = [[c("A1", 5), c("B1", 0), c("C1", f="A1/B1"), c("D1", f="IFERROR(A1/B1,-1)"),
             c("E1", f="ROUND(2.675,2)"), c("F1", f='IF(A1>3,"много","мало")'),
             c("G1", f='CONCATENATE("Насос ","ЦНС ",A1)&"-176"'), c("H1", f="-2^2"),
             c("I1", f="ROUNDDOWN(A1/3,1)+MAX(A1,B1)+AVERAGE(A1:B1)")]]
    строки = тело(rs.прочитать_книгу(xlsx([("Л", лист(ряды), None)]))[0])
    assert строки == [["5", "0", "#DIV/0!", "-1", "2.68", "много", "Насос ЦНС 5-176", "4", "9.1"]]


def test_формула_не_считает_сумму_за_обрезкой(monkeypatch):
    """Лист обрезан на пределе: сумма столбца по прочитанной части вышла бы
    правдоподобной и неверной. Такая формула — «без значения»."""
    monkeypatch.setattr(rs, "МАКС_СТРОК_ЛИСТА", 3)
    ряды = [[c("A1", 1), c("B1", f="SUM(A1:A6)"), c("C1", f="A1*2")]] + [[c(f"A{r}", r)] for r in range(2, 7)]
    к = rs.разобрать_книгу(xlsx([("Л", лист(ряды), None)]))
    assert тело(к.строки)[0] == ["1", "", "2"]
    assert "формул без значения 1" in к.причина


# ------------------------------------------------------------------ даты


def test_дата_по_встроенному_и_своему_формату():
    """Без стиля дата — «45123». Стиль 14 — встроенная дата; 164 — свой формат;
    31 — китайская дата (CNY — половина строк цены, а openpyxl 31 датой не считает);
    денежный формат с [$₽-419] датой не становится."""
    ст = стили(0, 14, 164, 31, 165, 20, 166, форматы={164: "dd/mm/yyyy", 165: "# ##0,00 [$₽-419]",
                                                        166: "[$-419]d mmmm yyyy h:mm"})
    b = xlsx([("Л", лист([[c("A1", 45123), c("B1", 45123, s=1), c("C1", 45123, s=2), c("D1", 45123, s=3),
                           c("E1", 45123, s=4), c("F1", 0.5, s=5), c("G1", 45123.75, s=6),
                           c("H1", "2026-09-23T00:00:00", t="d")]]), None)], стиль=ст)
    assert тело(rs.прочитать_книгу(b)[0]) == [["45123", "16.07.2023", "16.07.2023", "16.07.2023", "45123",
                                               "12:00", "16.07.2023 18:00", "23.09.2026"]]


def test_система_дат_1904_и_високосный_1900():
    """Книги с Mac считают дни от 1904 года: тот же день — на 1462 меньше. А Excel
    считает 1900 год високосным, и дни до 1 марта 1900 сдвинуты на единицу."""
    b = xlsx([("Л", лист([[c("A1", 45123 - 1462, s=1)]]), None)], стиль=стили(0, 14), d1904=True)
    assert тело(rs.прочитать_книгу(b)[0]) == [["16.07.2023"]]
    b = xlsx([("Л", лист([[c("A1", 1, s=1), c("B1", 61, s=1)]]), None)], стиль=стили(0, 14))
    assert тело(rs.прочитать_книгу(b)[0]) == [["01.01.1900", "01.03.1900"]]


def test_формула_со_стилем_даты_даёт_дату():
    b = xlsx([("Л", лист([[c("A1", 45123, s=1), c("B1", f="A1+30", s=1)]]), None)], стиль=стили(0, 14))
    assert тело(rs.прочитать_книгу(b)[0]) == [["16.07.2023", "15.08.2023"]]


# ------------------------------------------------------------------ повреждённые книги


def большая_книга(строк=400) -> bytes:
    ряды = [[c(f"A{r}", f"Позиция номер {r}"), c(f"B{r}", r)] for r in range(1, строк + 1)]
    return xlsx([("Л", лист(ряды), None)])


def test_оборванный_архив_читается_по_локальным_заголовкам():
    """Скачивание оборвалось: оглавления архива в конце файла нет. Участники
    собираются по заголовкам с начала файла, а оборванный лист отдаёт строки до обрыва."""
    b = большая_книга()
    к = rs.разобрать_книгу(b[:int(len(b) * 0.7)])
    строки = тело(к.строки)
    assert 20 < len(строки) < 400
    assert строки[0] == ["Позиция номер 1", "1"]
    assert "архив книги повреждён" in к.причина
    assert "листов с испорченной разметкой: 1" in к.причина


def test_испорченная_разметка_листа_отдаёт_строки_до_порчи():
    """Лист, разметка которого оборвана посреди ячейки: первая строка цела."""
    испорчено = лист([[c("A1", "Насос"), c("B1", 1)]]).replace("</sheetData></worksheet>", "") + '<row r="2"><c r="A2"'
    к = rs.разобрать_книгу(xlsx([("Л", испорчено, None)]))
    assert тело(к.строки) == [["Насос", "1"]]
    assert к.причина == "листов с испорченной разметкой: 1"


def test_картинка_вместо_ячеек_названа():
    """Скан, вклеенный в лист картинкой, — дело распознавания; причина это говорит."""
    b = xlsx([("Л", лист([]), None)], лишнее={"xl/media/image1.png": b"\x89PNG\r\n\x1a\n"})
    строки, текст, причина = rs.прочитать_книгу(b)
    assert (строки, текст) == ([], "")
    assert причина == "ячеек нет, картинок 1: нужен распознаватель"


def test_надпись_в_фигуре_идёт_в_текст():
    """Условия поставки часто пишут в текстовом поле поверх листа."""
    рисунок = ('<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
               'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><xdr:sp><xdr:txBody>'
               "<a:p><a:r><a:t>Условия поставки: </a:t></a:r><a:r><a:t>DAP Москва</a:t></a:r></a:p>"
               "<a:p><a:r><a:t>Оплата 30/70</a:t></a:r></a:p></xdr:txBody></xdr:sp></xdr:wsDr>")
    b = xlsx([("Л", лист([[c("A1", "Насос")]]), None)], лишнее={"xl/drawings/drawing1.xml": рисунок})
    assert rs.прочитать_книгу(b)[1] == "Условия поставки: DAP Москва\nОплата 30/70"


def test_пределы_строк_на_лист_и_на_книгу(monkeypatch):
    """Прежний предел — 20 000 строк на всю книгу: лист-справочник первым съедал
    его целиком. Теперь предел на лист и отдельный на книгу; потери названы."""
    monkeypatch.setattr(rs, "МАКС_СТРОК_ЛИСТА", 5)
    monkeypatch.setattr(rs, "МАКС_СТРОК", 8)
    ряды = [[c(f"A{r}", r)] for r in range(1, 7)]
    к = rs.разобрать_книгу(xlsx([("А", лист(ряды), None), ("Б", лист(ряды), None), ("В", лист(ряды), None)]))
    assert [len(т) for _, т in rs.по_листам(к.строки)] == [5, 3]
    assert к.причина == "листов обрезано по пределу: 2 · листов не прочитано сверх предела книги: 1"


# ------------------------------------------------------------------ ods, fods


ODS_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
          'xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2"')

ODS_ТЕЛО = """<office:automatic-styles>
 <style:style style:name="ta1" style:family="table"><style:table-properties table:display="true"/></style:style>
 <style:style style:name="ta2" style:family="table"><style:table-properties table:display="false"/></style:style>
</office:automatic-styles>
<office:body><office:spreadsheet>
 <table:table table:name="Справочник" table:style-name="ta2">
  <table:table-row><table:table-cell office:value-type="string"><text:p>USD</text:p></table:table-cell></table:table-row>
 </table:table>
 <table:table table:name="КП" table:style-name="ta1">
  <table:table-row>
   <table:table-cell office:value-type="string"><text:p>Наименование</text:p></table:table-cell>
   <table:table-cell office:value-type="string" table:number-columns-repeated="2"><text:p>ед.</text:p></table:table-cell>
   <table:table-cell office:value-type="string"><text:p>Цена</text:p></table:table-cell>
   <table:table-cell table:number-columns-repeated="16380"/>
  </table:table-row>
  <table:table-row table:number-rows-repeated="3"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>
  <table:table-row>
   <table:table-cell office:value-type="string" table:number-rows-spanned="2"><text:p>Насос<text:s text:c="2"/>ЦНС</text:p></table:table-cell>
   <table:table-cell office:value-type="float" office:value="1"><text:p>1</text:p></table:table-cell>
   <table:table-cell office:value-type="date" office:date-value="2026-09-23"><text:p>23 сент.</text:p></table:table-cell>
   <table:table-cell office:value-type="currency" office:value="1234.5"><text:p>1 234,50 руб.</text:p></table:table-cell>
  </table:table-row>
  <table:table-row>
   <table:covered-table-cell/>
   <table:table-cell office:value-type="float" office:value="2"><text:p>2</text:p></table:table-cell>
   <table:table-cell/>
   <table:table-cell table:formula="of:=[.D5]*2"/>
  </table:table-row>
  <table:table-row table:number-rows-repeated="1048000"><table:table-cell/></table:table-row>
 </table:table>
</office:spreadsheet></office:body>"""


def ods(тело=ODS_ТЕЛО, mimetype="application/vnd.oasis.opendocument.spreadsheet") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", mimetype)
        z.writestr("content.xml", f'<office:document-content {ODS_NS}>{тело}</office:document-content>')
    return buf.getvalue()


ODS_ОЖИДАНИЕ = [["№ лист 2 «КП»"], ["Наименование", "ед.", "ед.", "Цена"],
                ["Насос  ЦНС", "1", "23.09.2026", "1234.5"],
                ["Насос  ЦНС", "2", "", "2469"],
                ["№ лист 1 «Справочник» (скрытый)"], ["USD"]]


def test_ods_повторы_объединения_значения_и_скрытый_лист():
    """Повтор «пустая ячейка × 16 380» и «пустая строка × 1 048 000» не
    разворачивается; непустой повтор разворачивается; число берётся из
    office:value (1234.5), а не из показа «1 234,50 руб.»; слитое по вертикали
    наименование доходит до второй строки; скрытый лист задан стилем."""
    к = rs.разобрать_книгу(ods())
    assert к.строки == ODS_ОЖИДАНИЕ
    assert к.путь == "ods" and к.счёт["формул_пересчитано"] == 1


def test_ods_миллион_ячеек_из_одного_элемента_не_проходит(monkeypatch):
    """Непустая ячейка «× 1000 колонок» в строке «× 1000 повторов» — миллион ячеек
    из одного элемента разметки. Предел строк этого не держит, держит предел ячеек
    (здесь он снижен до 20 000, чтобы тест шёл миллисекунды, а не секунды)."""
    monkeypatch.setattr(rs, "МАКС_ЯЧЕЕК", 20000)
    тело_ = ('<office:body><office:spreadsheet><table:table table:name="Л">'
             '<table:table-row table:number-rows-repeated="1000">'
             '<table:table-cell office:value-type="float" office:value="1" table:number-columns-repeated="1000"/>'
             "</table:table-row></table:table></office:spreadsheet></office:body>")
    к = rs.разобрать_книгу(ods(тело_))
    assert к.счёт["строк"] * 1000 <= rs.МАКС_ЯЧЕЕК + 1000
    assert к.причина == "листов обрезано по пределу: 1"


def test_предел_ячеек_делится_между_листами(monkeypatch):
    monkeypatch.setattr(rs, "МАКС_ЯЧЕЕК", 10)
    ряды = [[c(f"A{r}", r), c(f"B{r}", r)] for r in range(1, 5)]
    к = rs.разобрать_книгу(xlsx([("А", лист(ряды), None), ("Б", лист(ряды), None), ("В", лист(ряды), None)]))
    assert [len(т) for _, т in rs.по_листам(к.строки)] == [4, 1]
    assert к.причина == "листов обрезано по пределу: 1 · листов не прочитано сверх предела книги: 1"


def test_fods_плоский_xml():
    b = (f'<?xml version="1.0"?><office:document {ODS_NS} '
         f'office:mimetype="application/vnd.oasis.opendocument.spreadsheet">{ODS_ТЕЛО}</office:document>').encode()
    к = rs.разобрать_книгу(b)
    assert к.строки == ODS_ОЖИДАНИЕ and к.путь == "fods"


def test_odt_не_таблица_названа():
    строки, _, причина = rs.прочитать_книгу(ods(mimetype="application/vnd.oasis.opendocument.text"))
    assert строки == [] and причина == "документ ODF, но не таблица (text)"


# ------------------------------------------------------------------ xlsb


def запись(тип: int, тело: bytes = b"") -> bytes:
    """Запись BIFF12: тип и размер по 7 бит с битом продолжения."""
    out = bytearray([тип & 0x7F | (0x80 if тип > 0x7F else 0)])
    if тип > 0x7F:
        out.append(тип >> 7)
    n = len(тело)
    while True:
        out.append(n & 0x7F | (0x80 if n > 0x7F else 0))
        n >>= 7
        if not n:
            break
    return bytes(out) + тело


def wstr(s: str) -> bytes:
    return struct.pack("<I", len(s)) + s.encode("utf-16-le")


def яч_bin(кол: int, стиль: int = 0) -> bytes:
    return struct.pack("<I", кол) + стиль.to_bytes(3, "little") + b"\x00"


def xlsb() -> bytes:
    книга = (запись(153, struct.pack("<I", 0) + b"\x00" * 8)
             + запись(156, struct.pack("<II", 0, 1) + wstr("rId1") + wstr("КП"))
             + запись(156, struct.pack("<II", 1, 2) + wstr("rId2") + wstr("Справочник")))
    строки = запись(159) + запись(19, b"\x00" + wstr("Насос ЦНС 38-176")) + запись(19, b"\x00" + wstr("Цена")) + запись(160)
    стили_bin = (запись(615) + запись(44, struct.pack("<H", 164) + wstr("dd/mm/yyyy")) + запись(616)
                 + запись(617) + запись(47, struct.pack("<HH", 0, 0) + b"\x00" * 12)
                 + запись(47, struct.pack("<HH", 0, 164) + b"\x00" * 12) + запись(618))
    rk_целое = (1500 << 2) | 2                      # целое 1500
    rk_сотые = (12345 << 2) | 3                     # 123,45: целое и «делить на 100»
    лист1 = (запись(145)
             + запись(0, struct.pack("<I", 0) + b"\x00" * 13)
             + запись(7, яч_bin(0) + struct.pack("<I", 1))
             + запись(6, яч_bin(2) + wstr("Срок"))
             + запись(0, struct.pack("<I", 1) + b"\x00" * 13)
             + запись(7, яч_bin(0) + struct.pack("<I", 0))
             + запись(2, яч_bin(1) + struct.pack("<I", rk_целое))
             + запись(5, яч_bin(2, стиль=1) + struct.pack("<d", 45123.0))
             + запись(0, struct.pack("<I", 2) + b"\x00" * 13)
             + запись(2, яч_bin(1) + struct.pack("<I", rk_сотые))
             + запись(4, яч_bin(3) + b"\x01")
             + запись(3, яч_bin(4) + b"\x2a")
             + запись(146)
             + запись(177) + запись(176, struct.pack("<IIII", 1, 2, 0, 0)) + запись(178))
    лист2 = запись(145) + запись(0, struct.pack("<I", 0) + b"\x00" * 13) + запись(6, яч_bin(0) + wstr("USD")) + запись(146)
    связи = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             f'<Relationship Id="rId1" Type="{ТИП_ЛИСТА}" Target="worksheets/sheet1.bin"/>'
             f'<Relationship Id="rId2" Type="{ТИП_ЛИСТА}" Target="worksheets/sheet2.bin"/>'
             f'<Relationship Id="rId3" Type="{NS_R}/sharedStrings" Target="sharedStrings.bin"/>'
             f'<Relationship Id="rId4" Type="{NS_R}/styles" Target="styles.bin"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.bin", книга)
        z.writestr("xl/_rels/workbook.bin.rels", связи)
        z.writestr("xl/sharedStrings.bin", строки)
        z.writestr("xl/styles.bin", стили_bin)
        z.writestr("xl/worksheets/sheet1.bin", лист1)
        z.writestr("xl/worksheets/sheet2.bin", лист2)
    return buf.getvalue()


def test_xlsb_читается_своим_разбором():
    """pyxlsb в прогоне нет, и без своего разбора .xlsb терялся бы всегда.
    RK-числа (целое и «сотые»), общие строки, дата по стилю, объединение, скрытый лист."""
    к = rs.разобрать_книгу(xlsb())
    assert к.путь == "xlsb", к.причина
    assert к.строки == [["№ лист 1 «КП»"], ["Цена", "", "Срок"],
                        ["Насос ЦНС 38-176", "1500", "16.07.2023"],
                        ["Насос ЦНС 38-176", "123.45", "", "TRUE", "#N/A"],
                        ["№ лист 2 «Справочник» (скрытый)"], ["USD"]]


def test_xlsb_без_листов_названа_причина():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.bin", b"\xff\xff\xff")
    строки, _, причина = rs.прочитать_книгу(buf.getvalue())
    assert строки == [] and причина.startswith("xlsb: свой разбор не нашёл листов")


# ------------------------------------------------------------------ xls, OLE2, SpreadsheetML


def test_ole2_не_книга_названа_причина():
    """xlrd в гейте нет; в прогоне есть. В обоих случаях отказ назван."""
    b = rs._OLE2 + b"\x00" * 600
    строки, _, причина = rs.прочитать_книгу(b)
    assert строки == []
    assert причина in ("xls: нет пакета xlrd",) or причина.startswith("OLE2 не открылся книгой")


def test_xls_объединения_даты_и_скрытый_лист():
    """Старый .xls через xlrd: объединения видны только с formatting_info=True."""
    xlwt = pytest.importorskip("xlwt")
    pytest.importorskip("xlrd")
    wb = xlwt.Workbook()
    справочник = wb.add_sheet("Справочник")
    справочник.write(0, 0, "USD")
    справочник.visibility = 1                      # 1 — скрытый
    кп = wb.add_sheet("КП")
    кп.write(0, 0, "№")
    кп.write(0, 1, "Наименование")
    кп.write(0, 2, "Цена")
    кп.write(0, 3, "Срок")
    кп.write(1, 0, 1)
    кп.write_merge(1, 2, 1, 1, "Насос ЦНС 38-176")
    кп.write(1, 2, 1500)
    кп.write(1, 3, 45123, xlwt.easyxf(num_format_str="DD.MM.YYYY"))
    кп.write(2, 0, 2)
    кп.write(2, 2, 1200)
    buf = io.BytesIO()
    wb.save(buf)
    к = rs.разобрать_книгу(buf.getvalue())
    assert к.путь == "xls"
    assert к.строки == [["№ лист 2 «КП»"], ["№", "Наименование", "Цена", "Срок"],
                        ["1", "Насос ЦНС 38-176", "1500", "16.07.2023"], ["2", "Насос ЦНС 38-176", "1200"],
                        ["№ лист 1 «Справочник» (скрытый)"], ["USD"]]
    assert rs.прочитать_книгу(buf.getvalue()[:len(buf.getvalue()) - 100])[2]


def test_xlrd_пишет_не_в_stdout(monkeypatch):
    """xlrd по умолчанию описывает порчу в stdout — с именем листа («MERGEDCELLS
    bad range» на листе «КП поставщика»), а журнал прогона публичный (правило 17).
    Его logfile связан с тем stdout, что был при импорте, поэтому перехват вывода
    в тесте его не видит; проверяется сам вызов. Поддельный xlrd — чтобы тест шёл
    и в гейте, где настоящего нет."""
    куда = []

    def открыть(**k):
        куда.append(k.get("logfile"))
        raise ValueError("не книга")

    monkeypatch.setitem(sys.modules, "xlrd", type(sys)("xlrd"))
    monkeypatch.setattr(sys.modules["xlrd"], "open_workbook", открыть, raising=False)
    assert rs.прочитать_книгу(rs._OLE2 + bytes(600))[2].startswith("OLE2 не открылся книгой (ValueError)")
    assert куда and all(л is not None and л not in (sys.stdout, sys.__stdout__, sys.stderr) for л in куда)


def ole2(мини_цепь) -> bytes:
    """Минимальный контейнер OLE2: заголовок, FAT, каталог (корень и поток
    «Workbook» на 200 байт — малый, живёт в мини-потоке), мини-таблица, мини-поток."""
    КОНЕЦ, СВОБОДЕН, FATSECT = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD
    заголовок = bytearray(512)
    заголовок[0:8] = rs._OLE2
    struct.pack_into("<HHHHH", заголовок, 24, 0x3E, 3, 0xFFFE, 9, 6)   # сектор 512, мини 64
    struct.pack_into("<II", заголовок, 44, 1, 1)             # секторов FAT; первый сектор каталога
    struct.pack_into("<IIIII", заголовок, 56, 4096, 2, 1, КОНЕЦ, 0)
    struct.pack_into("<109I", заголовок, 76, 0, *([СВОБОДЕН] * 108))
    fat = struct.pack("<128I", FATSECT, КОНЕЦ, КОНЕЦ, КОНЕЦ, *([СВОБОДЕН] * 124))

    def запись(имя, тип, начало, размер, ребёнок=0xFFFFFFFF):
        e = bytearray(128)
        n = (имя + "\0").encode("utf-16-le")
        e[:len(n)] = n
        struct.pack_into("<HBB", e, 64, len(n), тип, 1)
        struct.pack_into("<III", e, 68, 0xFFFFFFFF, 0xFFFFFFFF, ребёнок)
        struct.pack_into("<II", e, 116, начало, размер)
        return bytes(e)

    каталог = запись("Root Entry", 5, 3, 512, ребёнок=1) + запись("Workbook", 2, 0, 200) + bytes(256)
    мини = struct.pack("<128I", *мини_цепь, *([СВОБОДЕН] * (128 - len(мини_цепь))))
    return bytes(заголовок) + fat + каталог + мини + bytes(512)


def test_зацикленная_цепочка_ole2_не_доходит_до_xlrd():
    """xlrd идёт по мини-цепочке без учёта пройденного: порча нескольких байт в
    .xls на 7 КБ выбрала 1,8 ГБ за 22 секунды. Цикл ловится до xlrd."""
    КОНЕЦ = 0xFFFFFFFE
    assert rs._ole2_цепи_целы(ole2([1, 2, 3, КОНЕЦ]))
    assert not rs._ole2_цепи_целы(ole2([1, 2, 3, 1]))
    assert rs.прочитать_книгу(ole2([1, 2, 3, 1])) == ([], "", "OLE2 испорчен: цепочка секторов книги зациклена")


def test_зашифрованная_книга_названа():
    b = rs._OLE2 + b"\x00" * 64 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 64
    assert rs.прочитать_книгу(b)[2] == "книга зашифрована паролем: без пароля не читается"


def test_spreadsheetml_с_объединениями_и_пропусками():
    """Выгрузка 1С «.xls», внутри XML: ss:Index пропускает колонки, ss:MergeDown
    сливает наименование на две строки, скрытый лист — в WorksheetOptions."""
    b = '''<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:x="urn:schemas-microsoft-com:office:excel">
 <Worksheet ss:Name="Служебный"><Table><Row><Cell><Data ss:Type="String">RUB</Data></Cell></Row></Table>
  <WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel"><Visible>SheetHidden</Visible></WorksheetOptions>
 </Worksheet>
 <Worksheet ss:Name="TDSheet"><Table>
  <Row><Cell><Data ss:Type="Number">1</Data></Cell><Cell ss:MergeDown="1"><Data ss:Type="String">Насос</Data></Cell>
       <Cell ss:Index="4"><Data ss:Type="Number">1500</Data></Cell></Row>
  <Row><Cell><Data ss:Type="Number">2</Data></Cell><Cell ss:Index="4"><Data ss:Type="Number">1200</Data></Cell>
       <Cell><Data ss:Type="DateTime">2026-09-23T00:00:00.000</Data></Cell></Row>
 </Table></Worksheet>
</Workbook>'''.encode()
    к = rs.разобрать_книгу(b)
    assert к.строки == [["№ лист 2 «TDSheet»"], ["1", "Насос", "", "1500"], ["2", "Насос", "", "1200", "23.09.2026"],
                        ["№ лист 1 «Служебный» (скрытый)"], ["RUB"]]


# ------------------------------------------------------------------ устойчивость


def test_не_книги_названы():
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    случаи = {b"": "пустой файл",
              b"%PDF-1.4 nothing": "не книга: ни zip, ни OLE2, ни XML таблицы",
              docx.getvalue(): "архив без листов книги: не xlsx, не xlsb, не ods",
              b"PK\x05\x06" + b"\x00" * 18: "архив не читается: ни одного участника"}
    for b, причина in случаи.items():
        assert rs.прочитать_книгу(b) == ([], "", причина)


def test_никогда_не_бросает_и_ничего_не_печатает(capsys):
    """Порча в любом байте и обрыв в любом месте — ответ с причиной, а не исключение."""
    образцы = [большая_книга(30), ods(), xlsb()]
    случайно = random.Random(20260923)
    for образец in образцы:
        for доля in range(1, 20):
            ответ = rs.прочитать_книгу(образец[:len(образец) * доля // 20])
            assert isinstance(ответ, tuple) and len(ответ) == 3
            assert ответ[0] or ответ[1] or ответ[2]
        for _ in range(60):
            порча = bytearray(образец)
            for _ in range(случайно.randint(1, 8)):
                порча[случайно.randrange(len(порча))] = случайно.randrange(256)
            ответ = rs.прочитать_книгу(bytes(порча))
            assert ответ[0] or ответ[1] or ответ[2]
    assert rs.прочитать_книгу(None) == ([], "", "пустой файл")
    assert rs.прочитать_книгу("не байты")[2].startswith("книга: сбой разбора")
    вывод = capsys.readouterr()
    assert (вывод.out, вывод.err) == ("", "")


def test_неизвестный_вид_объединений_не_ломает():
    b = xlsx([("Л", лист([[c("A1", "x")]]), None)])
    assert rs.прочитать_книгу(b, объединения="как-нибудь")[0] == [["№ лист 1 «Л»"], ["x"]]


# ------------------------------------------------------------------ openpyxl (если стоит)


def test_книга_от_openpyxl_формулы_и_объединения():
    """Настоящий писатель: openpyxl сохраняет формулы БЕЗ значений. Прежний
    читатель (data_only) отдавал по ним None; свой разбор их пересчитывает."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "КП"
    ws.append(["№", "Наименование", "Кол-во", "Цена", "Сумма"])
    ws.append([1, "Насос ЦНС 38-176", 2, 1500, "=C2*D2"])
    ws.append([2, None, 1, 1200, "=C3*D3"])
    ws.merge_cells("B2:B3")
    buf = io.BytesIO()
    wb.save(buf)
    строки = тело(rs.прочитать_книгу(buf.getvalue())[0])
    assert строки[1:] == [["1", "Насос ЦНС 38-176", "2", "1500", "3000"],
                          ["2", "Насос ЦНС 38-176", "1", "1200", "1200"]]


def test_запасной_путь_openpyxl(monkeypatch):
    """Свой разбор не нашёл ничего на испорченной разметке — пробуется openpyxl."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["Кольцо", 7])
    buf = io.BytesIO()
    wb.save(buf)

    def сломан(поток, лист, *a, **k):
        лист.испорчен = True

    monkeypatch.setattr(rs, "_лист_разметки", сломан)
    к = rs.разобрать_книгу(buf.getvalue())
    assert к.путь == "openpyxl"
    assert тело(к.строки) == [["Кольцо", "7"]]
    assert "прочитана запасным openpyxl" in к.причина
