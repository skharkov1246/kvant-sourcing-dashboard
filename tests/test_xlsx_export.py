"""Выгрузка таблиц дашборда в Excel (просьба владельца 29.10.2026: «нужна возможность выгрузки
части инфы из дашборда в эксель»).

Два слоя. Писатель .xlsx шаблона (блок xlsxCore) исполняется в node и отдаёт байты; файл
открывается тем же, чем его откроет человек, — openpyxl, — и разбирается zipfile и XML-парсером:
CRC записей, части пакета, числа против текста, кириллица, экранирование, запрещённые в XML
символы, закреплённая шапка, 2000 строк. Затем smoke-страница в headless Chromium (без
Playwright — как проверка гейта, --dump-dom): таблица «Журнал шагов» и очередь недели
снимаются со страницы тем же кодом, что у кнопки «Excel», и сверяются с данными страницы —
в том числе строки, которых на экране нет (первые 40 и «Показать все»). Корпус придуман
(CLAUDE.md, правило 18).
"""
from __future__ import annotations

import base64
import html
import io
import json
import re
import shutil
import subprocess
import sys
import types
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

TPL = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
CORE = TPL[TPL.index("// BEGIN xlsxCore"):TPL.index("// END xlsxCore")]
PARTS = ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
         "xl/styles.xml", "xl/worksheets/sheet1.xml"]

HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const box = {TextEncoder, console};
vm.createContext(box);
vm.runInContext(inp.core + '\nthis._xlBuild=_xlBuild; this._xlVal=_xlVal; this._xlFileName=_xlFileName;'
  + ' this._xlSheetName=_xlSheetName; this._xlCrc32=_xlCrc32;', box);
const when = new Date(2026, 9, 29, 12, 30, 0);
const out = {books: {}, vals: {}, names: {}, sheets: {}, crc: null};
for (const [k, o] of Object.entries(inp.books)) {
  const b = box._xlBuild(Object.assign({}, o, {when}));
  out.books[k] = Buffer.from(b.buffer, b.byteOffset, b.length).toString('base64');
}
for (const v of inp.vals) out.vals[v] = box._xlVal(v);
for (const [k, a] of Object.entries(inp.names)) out.names[k] = box._xlFileName(a, '2026-10-29');
for (const s of inp.sheets) out.sheets[s] = box._xlSheetName(s);
out.crc = box._xlCrc32(new TextEncoder().encode('123456789'));
process.stdout.write(JSON.stringify(out));
"""

BASIC = {
    "sheet": "Журнал шагов",
    "head": ["Сделка", "Сумма", "Доля", "Код", "Дата", "Текст"],
    "rows": [
        ["№1 Заказчик «Альфа» & сыновья <ООО>", "1 234,5", "45 %", "007", "01.10", "кавычки \"двойные\" и 'одинарные'"],
        ["№2", "−3", "4,5 %", "123456789012", "10.2026", "управляющие\u0001символы\u000bи\ud800 обрывок"],
        ["", "0", "100%", "12345678901", "28.10.2026", "45 % в срок"],
        ["\U0001F6A9 значок", "-2.5", "", "A-17", "3.5", "ж" * 40000],
    ],
    "tail": ["выгружено 4 из 9: остальные строки в страницу не переданы"],
}
# итоги tfoot — под автофильтром (nData — строк данных); {t, txt} — ячейка только текстом (цифры
# собраны из разных элементов страницы); «_x0041_» — подчёркивание экранировано, Excel не прочтёт код знака
FOOT = {"sheet": "Итоги", "head": ["А", "Б"], "nData": 2,
        "rows": [["_x0041_ и a_x00e9_b", "1"], [{"t": "5 100%", "txt": True}, "2"], ["Итого", "3"]]}
BIG = {"sheet": "Большая", "head": ["№", "Количество", "Текст"],
       "rows": [[f"№{i}", f"{i * 1000:,}".replace(",", " "), f"строка {i}"] for i in range(1, 2001)]}
# видимый текст → ожидание: ("n", число) | ("pct", доля, знаков) | ("s",)
VALS = {
    "12": ("n", 12), "1 234": ("n", 1234), "1 234 567,5": ("n", 1234567.5), "1 000": ("n", 1000),
    "−3": ("n", -3), "-2.5": ("n", -2.5), "0": ("n", 0), "0,25": ("n", 0.25), "3.5": ("n", 3.5),
    "12345678901": ("n", 12345678901),
    "45 %": ("pct", 0.45, 0), "4,5 %": ("pct", 0.045, 1), "100%": ("pct", 1, 0), "−5 %": ("pct", -0.05, 0),
    "007": ("s",), "00": ("s",), "123456789012": ("s",), "1 234 567 890 123": ("s",),
    "01.10": ("s",), "28.10": ("s",), "10.2026": ("s",), "28.10.2026": ("s",), "2024–2027": ("s",),
    "45 % в срок": ("s",), "5 дн": ("s",), "€12K": ("s",), "+5": ("s",), "1 23": ("s",), "12:30": ("s",),
    "": ("s",), "—": ("s",), "1,234.5": ("s",), "№933": ("s",),
    # счёт и доля рядом («3» и «<small>100%</small>»): разряды тысяч у процента — текстом, а не 3100 %
    "3 100%": ("s",), "36 100%": ("s",), "1 100 %": ("s",), "5\u00a0100%": ("s",),
}
NAMES = {
    "journal": ["Реализация", "Месяц", "Журнал шагов"],
    "dup": ["Реализация", "Реализация", "Неделя"],
    "bad": ["Сорсинг", 'a/b\\c:d*e?f"g<h>i|j', "конец. "],
    "long": ["Реализация", "Неделя руководителя ОРК", "Что делать — очередь по исполнителю шага " * 6],
}
SHEETS = ["a[b]:c*d?e/f\\g" + "х" * 40, "", "'Кавычки'", "History", "Журнал шагов"]
# доля с дробью — ровно то число, что написано: 13,7 % → 0.137, а не 0.13699999999999998
# (деление на 100 в двоичной дроби давало хвост; ревизия сведения 29.10.2026, «Пульс ОРК»)
PCT_EXACT = {"13,7 %": "0.137", "14,3 %": "0.143", "57,1 %": "0.571", "0,7 %": "0.007", "33.3%": "0.333",
             "−28,6 %": "-0.286", "99,9 %": "0.999", "45 %": "0.45", "100%": "1"}
PCT = {"sheet": "Доли", "head": ["Доля"], "rows": [[t] for t in PCT_EXACT]}


@pytest.fixture(scope="module")
def built():
    if shutil.which("node") is None:
        pytest.skip("нет node")
    inp = {"core": CORE, "books": {"basic": BASIC, "big": BIG, "foot": FOOT, "pct": PCT,
                                   "empty": {"sheet": "", "head": ["А", "Б"], "rows": []}},
           "vals": list(VALS) + list(PCT_EXACT), "names": NAMES, "sheets": SHEETS}
    # ensure_ascii — одиночная половинка суррогатной пары доезжает до node как есть
    r = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp), capture_output=True, text=True,
                       timeout=120, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def _book(built, key):
    return base64.b64decode(built["books"][key])


def _wb(data: bytes):
    openpyxl = pytest.importorskip("openpyxl")
    return openpyxl.load_workbook(io.BytesIO(data))


def test_пакет_zip_и_xml_целы(built):
    """ZIP без сжатия с верными CRC, шесть частей пакета, каждая — корректный XML; CRC32 —
    стандартный (контрольное значение «123456789» → CBF43926)."""
    assert built["crc"] == 0xCBF43926
    for key in ("basic", "big", "empty", "foot"):
        z = zipfile.ZipFile(io.BytesIO(_book(built, key)))
        assert z.testzip() is None, key
        assert z.namelist() == PARTS
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
        for name in PARTS:
            ET.fromstring(z.read(name))
    z = zipfile.ZipFile(io.BytesIO(_book(built, "basic")))
    ct = z.read("[Content_Types].xml").decode()
    assert "spreadsheetml.sheet.main+xml" in ct and "worksheet+xml" in ct and "styles+xml" in ct
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' in sheet
    assert 't="inlineStr"' in sheet and "<sharedStrings" not in sheet
    wb = z.read("xl/workbook.xml").decode("utf-8")
    assert "_xlnm._FilterDatabase" in wb and "&apos;Журнал шагов&apos;!$A$1:$F$5" in wb
    # запрещённые в XML 1.0 символы вырезаны, а не сломали файл
    assert "\x01" not in sheet and "\x0b" not in sheet


def test_ячейки_числа_текст_кириллица_экранирование(built):
    """Числа — числами (русская запись, «−», проценты без слов — с форматом %), остальное —
    текстом как есть: дата, месяц, код с ведущим нулём, номер из 12 цифр, процент со словами.
    Кириллица, «&<>"'» и кавычки-ёлочки — без искажений; шапка жирная и закреплена."""
    wb = _wb(_book(built, "basic"))
    ws = wb.active
    assert ws.title == "Журнал шагов" and ws.freeze_panes == "A2"
    assert [c.value for c in ws[1]] == BASIC["head"] and all(c.font.b for c in ws[1])
    assert ws.auto_filter.ref == "A1:F5"
    assert ws["A2"].value == "№1 Заказчик «Альфа» & сыновья <ООО>"
    assert ws["B2"].value == 1234.5 and ws["C2"].value == 0.45 and ws["C2"].number_format == "0%"
    assert ws["D2"].value == "007" and ws["E2"].value == "01.10"
    assert ws["F2"].value == "кавычки \"двойные\" и 'одинарные'"
    assert ws["B3"].value == -3 and ws["C3"].value == pytest.approx(0.045) and ws["C3"].number_format == "0.0%"
    assert ws["D3"].value == "123456789012" and ws["E3"].value == "10.2026"
    assert ws["F3"].value == "управляющиесимволыи обрывок"
    assert ws["A4"].value is None and ws["B4"].value == 0 and ws["C4"].value == 1
    assert ws["D4"].value == 12345678901 and ws["E4"].value == "28.10.2026" and ws["F4"].value == "45 % в срок"
    assert ws["A5"].value == "\U0001F6A9 значок" and ws["B5"].value == -2.5 and ws["D5"].value == "A-17"
    assert ws["E5"].value == 3.5
    assert len(ws["F5"].value) == 32767 and ws["F5"].value.endswith("…"), "предел ячейки Excel"
    assert ws["A6"].value is None and ws["A7"].value == BASIC["tail"][0]
    widths = [ws.column_dimensions[c].width for c in "ABCDEF"]
    assert all(6 <= w <= 60 for w in widths) and widths[5] == 60


def test_две_тысячи_строк(built):
    wb = _wb(_book(built, "big"))
    ws = wb.active
    assert ws.max_row == 2001 and ws["A2001"].value == "№2000" and ws["C2"].value == "строка 1"
    vals = [ws.cell(row=i, column=2).value for i in range(2, 2002)]
    assert all(isinstance(v, int) for v in vals) and sum(vals) == 1000 * 2000 * 2001 // 2


def test_итоги_под_фильтром_текст_по_разметке_и_x_коды(built):
    """Строки tfoot («Итого») — ниже автофильтра: сортировка в Excel их с данными не смешает.
    Ячейка, сложенная страницей из разных элементов ({t, txt}), — текстом, даже если похожа на
    число. «_xHHHH_» в тексте — с экранированным подчёркиванием (_x005F_), иначе Excel покажет
    вместо «_x0041_» букву «A»."""
    data = _book(built, "foot")
    ws = _wb(data).active
    assert ws.auto_filter.ref == "A1:B3"
    assert ws["A3"].value == "5 100%" and ws["B3"].value == 2 and ws["A4"].value == "Итого" and ws["B4"].value == 3
    z = zipfile.ZipFile(io.BytesIO(data))
    assert "&apos;Итоги&apos;!$A$1:$B$3" in z.read("xl/workbook.xml").decode("utf-8")
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "_x005F_x0041_ и a_x005F_x00e9_b" in sheet and ">_x0041_" not in sheet


def test_пустая_таблица_только_шапка(built):
    ws = _wb(_book(built, "empty")).active
    assert ws.title == "Таблица" and ws.max_row == 1 and [c.value for c in ws[1]] == ["А", "Б"]


def test_число_или_текст(built):
    for text, exp in VALS.items():
        got = built["vals"][text]
        if exp[0] == "s":
            assert got == {"s": text.strip()}, (text, got)
        elif exp[0] == "n":
            assert "pct" not in got and got["n"] == pytest.approx(exp[1]), (text, got)
        else:
            assert got["n"] == pytest.approx(exp[1]) and got["pct"] == exp[2], (text, got)


def test_доля_точно_без_хвоста_двоичной_дроби(built):
    """Видимая доля «13,7 %» — в файле 0.137 (формат 0.0%), а не 0.13699999999999998: значение
    ячейки, которое видно в строке формул Excel и уходит в формулы, — то, что на странице."""
    for text, want in PCT_EXACT.items():
        got = built["vals"][text]
        assert got["n"] == float(want), (text, got)
    xml = zipfile.ZipFile(io.BytesIO(_book(built, "pct"))).read("xl/worksheets/sheet1.xml").decode()
    vs = re.findall(r"<v>([^<]*)</v>", xml)
    assert vs == list(PCT_EXACT.values()), vs
    ws = _wb(_book(built, "pct")).active
    assert [ws.cell(i + 2, 1).value for i in range(len(PCT_EXACT))] == [float(v) for v in PCT_EXACT.values()]


def test_имя_файла_и_листа(built):
    """КВАНТ_<вкладка>_<раздел>_<таблица>_<дата>.xlsx: без знаков, запрещённых Windows, ≤ 120;
    лист ≤ 31 знака без []:*?/\\."""
    n = built["names"]
    assert n["journal"] == "КВАНТ_Реализация_Месяц_Журнал шагов_2026-10-29.xlsx"
    assert n["dup"] == "КВАНТ_Реализация_Неделя_2026-10-29.xlsx"
    assert not re.search(r'[\\/:*?"<>|]', n["bad"]) and n["bad"].endswith("_конец_2026-10-29.xlsx")
    assert len(n["long"]) <= 120 and n["long"].endswith("_2026-10-29.xlsx") and n["long"].startswith("КВАНТ_")
    s = built["sheets"]
    first = s[SHEETS[0]]
    assert len(first) <= 31 and not re.search(r"[\[\]:*?/\\]", first)
    assert s[""] == "Таблица" and s["History"] == "Таблица" and s["'Кавычки'"] == "Кавычки"
    assert s["Журнал шагов"] == "Журнал шагов"


# ── страница целиком: тот же код, что у кнопки, в headless Chromium ──────────────────────
PROBE = r"""<script>(function(){
var out={};
function b64(u){var s='';for(var i=0;i<u.length;i+=8192)s+=String.fromCharCode.apply(null,u.subarray(i,i+8192));return btoa(s);}
function snap(t,key){var S=_xlSnap(t),cap=_xlCaption(t),tab=_xlTabName(t),sub=_xlSub(t);
  out[key]={head:S.head,rows:S.rows.length,shown:S.shown,full:S.full,tail:S.tail,nData:S.nData,
    file:_xlFileName([tab,sub,cap],'2026-10-29'),
    b64:b64(_xlBuild({sheet:cap||sub||tab,head:S.head,rows:S.rows,tail:S.tail,nData:S.nData}))};}
try{
  snap(document.getElementById('tbConv').closest('table'),'conv');
  var syn=document.createElement('div');
  syn.innerHTML='<h2>Проба склейки</h2><table><thead><tr><th>A</th><th>B</th><th>C</th><th>D</th><th>E</th><th>F</th><th>G</th></tr></thead><tbody>'
    +'<tr><td class="num r"><span class="q">5</span> <small class="dimv">100%</small></td><td>1<br>234</td><td>3<div>100</div></td>'
    +'<td><b>1 234</b></td><td>Имя<span class="gone">ушёл 10.07</span></td><td><b>№1</b><div class="dep">Компания 9</div></td>'
    +'<td>Марка® ™ © \u{1F6A9}</td></tr></tbody><tfoot><tr><td>Итого</td><td>7</td></tr></tfoot></table>'
    +'<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td colspan="2" class="dimv">Нет данных: проба пустого отбора</td></tr></tbody></table>';
  document.getElementById('tab-sourcing').appendChild(syn);
  var st=syn.querySelectorAll('table');
  snap(st[0],'mixed'); snap(st[1],'emptyState');
  var CT=[].slice.call(document.querySelectorAll('.tabs .tab')).filter(function(x){return x.dataset.tab==='contracts'})[0];
  CT.click();
  document.querySelector('#tab-contracts [role="tab"][data-sub="month"]').click();
  var tj=document.querySelector('#orkJ table');
  out.jbar=!!(tj&&tj.dataset.xlOn&&document.querySelector('#orkJ .xlbar .xlbtn'));
  snap(tj,'journal');
  var sj=document.getElementById('orkJStep');sj.value='';sj.dispatchEvent(new Event('change'));
  var ov=document.getElementById('orkJOver');ov.checked=false;ov.dispatchEvent(new Event('change'));
  var al=document.getElementById('orkJAll');al.checked=true;al.dispatchEvent(new Event('change'));
  snap(tj,'journalAll');
  // «Как считается» и «Квартал»: ячейки «доля + k из n» и «число + мало данных» (data-xlv, раунд 3)
  document.querySelector('#tab-contracts [role="tab"][data-sub="how"]').click();
  snap(document.getElementById('ctmHowMet').nextElementSibling.querySelector('table'),'howMet');
  snap(document.getElementById('ctmHowArt').nextElementSibling.querySelector('table'),'howArt');
  document.querySelector('#tab-contracts [role="tab"][data-sub="quarter"]').click();
  snap(document.querySelector('#tab-contracts table.ctm-rg'),'qRange');
  document.querySelector('#tab-contracts [role="tab"][data-sub="week"]').click();
  snap(document.getElementById('orkPulseB').closest('table'),'people');
  snap(document.getElementById('orkLed').closest('table'),'ledger');
  snap(document.getElementById('orkLedD').closest('table'),'ledgerD');
  var l0=document.getElementById('orkLed0'); if(l0) snap(l0.closest('table'),'ledger0');
  // список за числом (дровер): в имени файла — подвкладка и направление числа, как у таблиц вкладки
  // «N заказов в работе» — в шапке вкладки (R4, WP-A), по выбранному направлению; в панели — если есть
  var od=function(sel,key){var b=document.querySelector(sel+' button[data-od="w:live"]')||document.querySelector('#ctLive button[data-od="w:live"]'); b.click();
    var t=document.getElementById('odTable'); snap(t,key); out[key].trs=t.tBodies[0].rows.length; out[key].n=+b.textContent;
    _odClose(false);};
  od('#ctPanel-main','odMain');
  var sd=document.getElementById('ctDir-sep'); if(sd){sd.click(); od('#ctPanel-sep','odSep'); document.getElementById('ctDir-main').click();}
  document.querySelector('#tab-contracts [role="tab"][data-sub="main"]').click();
  snap(document.getElementById('ctBody').closest('table'),'deals');
}catch(e){out.err=String(e&&e.stack||e);}
var p=document.createElement('pre');p.id='xlout';p.textContent=JSON.stringify(out);document.body.appendChild(p);
})();</script>"""


@pytest.fixture(scope="module")
def page_run(tmp_path_factory):
    import validate_dashboard as vd
    chrome, _v = vd.find_chrome()
    if not chrome:
        pytest.skip("нет рабочего Chromium/Chrome — проверка страницы пропущена")
    import dashboard
    from tests import fixture
    r = fixture.build_realization()
    m = fixture.build_metrics()
    # у первого сорсера КП по каждому запросу: в ячейке «КП засчитано» — «56» и «<small>100%</small>»
    m["sourcersA"][0]["quotes"] = m["sourcersA"][0]["c"]
    d = tmp_path_factory.mktemp("xlsx_page")
    page = dashboard.write(m, {"source": "rules", "items": []}, d / "index.html", **r)
    page.write_text(page.read_text(encoding="utf-8").replace("</body>", PROBE + "</body>", 1), encoding="utf-8")
    dom, log = vd._chrome_run(chrome, page, [], 120)
    mm = re.search(r'<pre id="xlout">(.*?)</pre>', dom, re.S)
    assert mm, "страница не отдала результат пробы: " + log[-500:]
    out = json.loads(html.unescape(mm.group(1)))
    assert "err" not in out, out.get("err")
    return out, r, m


def _rows(b64: str) -> list[list]:
    ws = _wb(base64.b64decode(b64)).active
    return [[c.value for c in row] for row in ws.iter_rows()]


def test_журнал_шагов_в_excel_как_на_странице(page_run):
    """Случай владельца: шаг S1, «только сверх нормы» (по умолчанию) — в файле ровно заказы, где
    приёмка дольше 1 р.д., столько же, сколько в данных журнала; шапка — 11 столбцов таблицы;
    под таблицей — какой отбор выгружен. Все шаги с неизмеримыми — все строки журнала."""
    out, r, _m = page_run
    J = r["ork_month"]["main"]["journal"]["rows"]
    s1_over = [x for x in J if x["seg"] == "S1" and x["measurable"] and (x["over"] or 0) > 0]
    j = out["journal"]
    assert out["jbar"] and j["file"] == "КВАНТ_Реализация_Месяц_Журнал шагов_2026-10-29.xlsx"
    # E52 (спецификация «сделка и заказ»): строка — шаг заказа, первым столбцом — заказ, сделка — вторым
    assert j["head"] == ["Заказ", "Сделка", "Шаг", "Начало", "Конец", "Шёл, раб. дн.", "Норма, раб. дн.",
                         "Дольше нормы, раб. дн.", "Почему не считаем", "Кто делал", "Месяц"]
    rows = _rows(j["b64"])
    data = rows[1:1 + j["rows"]]
    assert j["rows"] == len(s1_over) > 0
    assert all(row[2].startswith("Приёмка сделки") and "S1" in row[2] and isinstance(row[7], int) and row[7] >= 1
               for row in data)
    assert [row[7] for row in data] == sorted((x["over"] for x in s1_over), reverse=True)
    assert all(isinstance(row[5], int) and row[6] == 1 for row in data), "р.д. и норма — числами"
    assert all(re.fullmatch(r"\d\d\.\d\d\.\d{4}", row[4]) and re.fullmatch(r"\d\d\.\d{4}", row[10]) for row in data)
    assert rows[-1][0].startswith("Отбор: Приёмка сделки — S1 (шаг 1)") and "только дольше нормы" in rows[-1][0]
    a = out["journalAll"]
    assert a["rows"] == len(J) and len(_rows(a["b64"])) == 1 + len(J) + 2


def test_журнал_номер_заказа_в_каждой_строке(page_run):
    """Ревью раунда 3: номер заказа — кнопка, её выгрузка пропускала, и строки одной сделки в файле
    не различались. Столбец «Заказ» (первый) — «933/1 · название · поставщик», «Сделка» — «сделка №933 · заказчик»."""
    out, _r, _m = page_run
    rows = _rows(out["journalAll"]["b64"])[1:1 + out["journalAll"]["rows"]]
    assert rows and all(re.match(r"(\d+/\d+|id \d+)( · |$)", str(row[0] or "")) for row in rows), rows[:3]
    assert all(str(row[1] or "").startswith("сделка №") for row in rows), rows[:3]


def test_доли_и_оговорки_одной_ячейкой_не_склеиваются(page_run):
    """Ревью раунда 3: «75 %3 из 4», «10меньше 80 %», «28мало данных» — две величины в одной ячейке
    уходили склейкой. Теперь — data-xlv: «75 (3 из 4)», число «10», «28 (мало данных)»."""
    out, _r, _m = page_run
    met = _rows(out["howMet"]["b64"])
    i = next(j for j, h in enumerate(met[0]) if str(h).startswith("С датами"))
    vals = [row[i] for row in met[1:1 + out["howMet"]["rows"]] if row[i] not in (None, "—")]
    assert vals and all(re.fullmatch(r"\d+ \(\d+ из \d+\)", str(v)) for v in vals), vals
    art = _rows(out["howArt"]["b64"])
    j = next(k for k, h in enumerate(art[0]) if "%" in str(h))
    pv = [row[j] for row in art[1:1 + out["howArt"]["rows"]] if row[j] not in (None, "—")]
    assert pv and all(isinstance(v, int) for v in pv), pv
    q = _rows(out["qRange"]["b64"])
    # E49: «В расчёте» — с единицей словами (у звена «вся сделка» — сделки, у прочих — заказы)
    k = next(x for x, h in enumerate(q[0]) if str(h).startswith("В расчёте"))
    qv = [row[k] for row in q[1:1 + out["qRange"]["rows"]] if row[k] is not None]
    assert qv and all(re.fullmatch(r"\d+ (заказ|заказа|заказов|сделка|сделки|сделок)( \(мало данных\))?", str(v)) for v in qv), qv
    assert not any(re.search(r"\d(мало|меньше)", str(v)) for v in qv + pv + vals)


def test_кто_сколько_ведёт_все_люди_и_итог(page_run):
    """«Кто сколько ведёт» (R4: одна таблица вместо «Пульса» и «Сверх нормы»): в файле — строка на
    человека и итог; «Заказов в работе» — числом, сумма строк = итог = заказы в работе вида."""
    out, r, _m = page_run
    p = out["people"]
    assert p["file"].startswith("КВАНТ_Реализация_Неделя руководителя ОРК_Кто сколько ведёт")
    rows = _rows(p["b64"])
    i = rows[0].index("Заказов в работе")
    body = rows[1:1 + p["rows"]]
    assert body[-1][0] == "Итого"
    live = r["contracts"]["ork"]["week"]["live"]
    assert body[-1][i] == live and sum(x[i] for x in body[:-1]) == live
    assert all(isinstance(x[i], int) for x in body)


def test_число_со_списком_заказов_идёт_в_файл(page_run):
    """Число «Реализации» со списком заказов за ним — кнопка (.odn): в файле оно есть, как на
    странице. Ведомость в двух частях (спец. «сделка и заказ», E21): шаги заказа 4–12 — «Заказов на
    шаге» каждой строки — число живых заказов на шаге (кнопка списка), в файле — то же число; шаги
    сделки 1–3 и 13 — своей таблицей «Сделок на шаге», итог — сумма строк, единицы не смешаны."""
    out, r, _m = page_run
    led = out["ledger"]
    assert led["head"][:3] == ["Шаг заказа", "Кто делает", "Заказов на шаге"]
    # шаг 0 «Подтверждение передачи в ОРК» (решение владельца 31.10.2026) — своей таблицей «Заказы — шаг 0»
    L = [s for s in r["contracts"]["ork"]["week"]["ledger"] if s["no"] not in (0, 1, 2, 3, 13)]
    rows = _rows(led["b64"])[1:1 + led["rows"]]
    assert led["rows"] == len(L) + 1 and sum(1 for s in L if s["now"]) > 0     # + строка «Всего заказов на шагах 4–12»
    for row, s in zip(rows, L):
        assert str(row[2]).split(" · ")[0] == str(s["now"] or 0), (s["no"], row[2])
    assert rows[-1][0] == "Всего заказов на шагах 4–12" and rows[-1][2] == sum(s["now"] or 0 for s in L)
    ld = out["ledgerD"]
    assert ld["head"][:3] == ["Шаг сделки", "Кто делает", "Сделок на шаге"]
    rd = _rows(ld["b64"])[1:1 + ld["rows"]]
    assert ld["rows"] == 4 + 1 and [str(x[0]).split(" ")[0] for x in rd[:-1]] == ["1", "2", "3", "13"]
    assert rd[-1][0] == "Всего сделок на шагах 1–3 и 13" and rd[-1][2] == sum(int(x[2]) for x in rd[:-1]) > 0
    # шаг 0: одна строка, «Заказов на шаге» и «Дольше 5 раб. дн.» — числами, как в ведомости
    l0 = out["ledger0"]
    s0 = next(s for s in r["contracts"]["ork"]["week"]["ledger"] if s["no"] == 0)
    assert l0["file"].endswith(".xlsx") and "Заказы — шаг 0" in l0["file"]
    assert l0["head"][:4] == ["Шаг 0 — до передачи в ОРК", "Кто делает", "Заказов на шаге", "Дольше 5 раб. дн., заказов"]
    r0 = _rows(l0["b64"])[1:1 + l0["rows"]]
    assert l0["rows"] == 1 and r0[0][2] == s0["now"] > 0 and r0[0][3] == s0["over"]


def test_список_за_числом_имя_файла_с_подвкладкой_и_направлением(page_run):
    """Ревизия сведения 29.10.2026: список за числом в дровере выгружался как
    «КВАНТ_Реализация_Живые заказы вида_…» и из «Основного», и из второго направления — два разных
    списка под одним именем. Теперь раздел — подвкладка и направление числа, с которого открыт
    список; строк в файле — столько, сколько в списке и в числе."""
    out, r, _m = page_run
    a, b = out["odMain"], out["odSep"]
    sep = r["contracts"]["sep"]
    assert a["file"] == "КВАНТ_Реализация_Неделя руководителя ОРК_Заказы в работе_2026-10-29.xlsx", a["file"]
    name = r["contracts"]["head"]["sep"]["name"]                # название второго направления вкладки
    assert b["file"] == f"КВАНТ_Реализация_Неделя руководителя ОРК {name}_Заказы в работе_2026-10-29.xlsx", b["file"]
    for x, v in ((a, r["contracts"]), (b, sep)):
        assert x["rows"] == x["trs"] == x["n"] == v["ork"]["week"]["live"] > 0
        rows = _rows(x["b64"])
        assert len(rows) == 1 + x["rows"]
        # шапка — столбцы списка без значков сортировки; в ячейках — данные, без ссылок «в Битриксе»
        # и без подписей для телефона (поставщик под заказом — только столбцом)
        # ревью 30.10.2026: по столбцу на поле, числа — числами; R4 (WP-C): поставщик — столбцом за
        # заказчиком, «Почему» — столбцом, единица «раб. дн.», «Кто делает»
        H = ["№ заказа", "Сделка", "Заказчик", "Поставщик", "Шаг №", "Шаг", "Стадия в Битриксе", "Раб. дн. на шаге",
             "Норма, раб. дн.", "Дней после срока клиента (минус — до срока)", "Срок клиента", "Прогноз доставки",
             "Почему", "Вторая причина", "Что сделать", "Кто делает", "Менеджер ОРК", "Ещё пункты очереди"]
        assert rows[0][:len(H)] == H, rows[0]
        assert not any("Битрикс" in str(c) or "поставщик:" in str(c) for row in rows[1:] for c in row)
        col = {h: i for i, h in enumerate(rows[0])}
        for h in ("Шаг №", "Раб. дн. на шаге", "Норма, раб. дн.", "Дней после срока клиента (минус — до срока)"):
            vals = [row[col[h]] for row in rows[1:] if row[col[h]] not in (None, "")]
            assert all(isinstance(v, int) for v in vals), (h, vals[:5])
            assert vals or x is b, h                    # у второго направления заказов мало — типы, не наличие
        for h in ("Срок клиента", "Прогноз доставки"):
            assert all(re.fullmatch(r"\d\d\.\d\d\.\d{4}", row[col[h]]) for row in rows[1:] if row[col[h]]), h
        assert all(isinstance(row[col["Что сделать"]], str) and row[col["Что сделать"]] for row in rows[1:])


def test_кто_сколько_ведёт_числа_числами(page_run):
    """«Кто сколько ведёт»: «Дольше нормы на его шагах», «Срок клиента сорван», «Ждём УПД» — числами,
    итоги = W2, сорванные заказы (Д-1, по заказам) и «ждём УПД» вида."""
    out, r, _m = page_run
    rows = _rows(out["people"]["b64"])
    h = rows[0]
    body = rows[1:1 + out["people"]["rows"]]
    w = r["contracts"]["ork"]["week"]
    for name, want in (("Дольше нормы на его шагах", w["tiles"]["W2"]["value"]),
                       ("Срок клиента сорван", w["tiles"]["W3"]["byOrders"]["late"]),
                       ("Ждём УПД", w["waitUtd"]["n"])):
        i = h.index(name)
        assert all(isinstance(x[i], int) for x in body), name
        assert body[-1][i] == want == sum(x[i] for x in body[:-1]), name


def test_таблица_сделок_как_на_странице(page_run):
    out, r, _m = page_run
    d = out["deals"]
    # R4: итог по видимым строкам — в tfoot (под автофильтром): строк файла = видимые + строка итога
    assert d["nData"] == d["shown"] > 0 and d["rows"] == d["shown"] + 1 and not d["full"]
    assert d["head"][0] == "№" and "Продажа, € тыс." in d["head"]
    first = _rows(d["b64"])[1]
    assert isinstance(first[0], int), "№ сделки — числом"


def test_счёт_и_доля_в_одной_ячейке_не_склеиваются(page_run):
    """Находка ревизии: «КП засчитано» — счёт и доля в одной ячейке; при доле 100 % текст «56 100%»
    читался как 56 100 % (561 с форматом %). Ячейка строки и итог отдела — текстом «N (P%)»
    (data-xlv), и любая ячейка, где цифры стоят в разных элементах, — текстом, а не числом."""
    out, _r, m = page_run
    A = m["sourcersA"]
    rows = _rows(out["conv"]["b64"])
    assert rows[0][2] == "КП" or "КП" in rows[0][2]
    body = rows[1:1 + len(A)]
    for row, a in zip(body, A):
        q, c = a["quotes"], a["c"]
        assert row[2] == f"{q} ({round(q / c * 100)}%)", (row[2], q, c)
    assert body[0][2] == f"{A[0]['c']} (100%)"
    tq, tc = sum(a["quotes"] for a in A), sum(a["c"] for a in A)
    total = rows[1 + len(A)]
    assert total[0].startswith("Отдел, итого") and total[2] == f"{tq} ({round(tq / tc * 100)}%)"
    assert out["conv"]["nData"] == len(A)
    ws = _wb(base64.b64decode(out["conv"]["b64"])).active
    assert ws.auto_filter.ref == f"A1:J{1 + len(A)}", "итог отдела — под автофильтром"
    # разметка: «5 <small>100%</small>», «1<br>234», «3<div>100</div>» — текст; одно число в одном
    # элементе — число; плашка «ушёл» — отдельным словом; блоки — через « · »; ©®™ — остаются
    mx = _rows(out["mixed"]["b64"])
    assert mx[1] == ["5 100%", "1 · 234", "3 · 100", 1234, "Имя ушёл 10.07", "№1 · Компания 9", "Марка® ™ ©"]
    assert mx[2][:2] == ["Итого", 7]
    assert _wb(base64.b64decode(out["mixed"]["b64"])).active.auto_filter.ref == "A1:G2"


def test_пустое_состояние_под_таблицей_а_не_строкой_данных(page_run):
    """Одна строка на всю ширину («Нет данных: …») — не строка данных: в файле 0 строк и пояснение
    под таблицей."""
    out, _r, _m = page_run
    e = out["emptyState"]
    assert e["rows"] == 0 and e["tail"] == ["Нет данных: проба пустого отбора"]
    rows = _rows(e["b64"])
    assert rows[0] == ["A", "B"] and rows[-1][0] == "Нет данных: проба пустого отбора" and len(rows) == 3
