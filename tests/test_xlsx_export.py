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
}
NAMES = {
    "journal": ["Реализация", "Месяц", "Журнал шагов"],
    "dup": ["Реализация", "Реализация", "Неделя"],
    "bad": ["Сорсинг", 'a/b\\c:d*e?f"g<h>i|j', "конец. "],
    "long": ["Реализация", "Неделя руководителя ОРК", "Что делать — очередь по исполнителю шага " * 6],
}
SHEETS = ["a[b]:c*d?e/f\\g" + "х" * 40, "", "'Кавычки'", "History", "Журнал шагов"]


@pytest.fixture(scope="module")
def built():
    if shutil.which("node") is None:
        pytest.skip("нет node")
    inp = {"core": CORE, "books": {"basic": BASIC, "big": BIG, "empty": {"sheet": "", "head": ["А", "Б"], "rows": []}},
           "vals": list(VALS), "names": NAMES, "sheets": SHEETS}
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
    for key in ("basic", "big", "empty"):
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
  out[key]={head:S.head,rows:S.rows.length,shown:S.shown,full:S.full,tail:S.tail,
    file:_xlFileName([tab,sub,cap],'2026-10-29'),
    b64:b64(_xlBuild({sheet:cap||sub||tab,head:S.head,rows:S.rows,tail:S.tail}))};}
try{
  var CT=[].slice.call(document.querySelectorAll('.tabs .tab')).filter(function(x){return x.dataset.tab==='contracts'})[0];
  CT.click();
  document.querySelector('#tab-contracts [role="tab"][data-sub="month"]').click();
  var tj=document.querySelector('#orkJ table');
  out.jbar=!!(tj&&tj.dataset.xlOn&&document.querySelector('#orkJ .xlbar .xlbtn'));
  snap(tj,'journal');
  var st=document.getElementById('orkJStep');st.value='';st.dispatchEvent(new Event('change'));
  var ov=document.getElementById('orkJOver');ov.checked=false;ov.dispatchEvent(new Event('change'));
  var al=document.getElementById('orkJAll');al.checked=true;al.dispatchEvent(new Event('change'));
  snap(tj,'journalAll');
  document.querySelector('#tab-contracts [role="tab"][data-sub="week"]').click();
  snap(document.getElementById('orkQb').closest('table'),'queue');
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
    d = tmp_path_factory.mktemp("xlsx_page")
    page = dashboard.write(m, {"source": "rules", "items": []}, d / "index.html", **r)
    page.write_text(page.read_text(encoding="utf-8").replace("</body>", PROBE + "</body>", 1), encoding="utf-8")
    dom, log = vd._chrome_run(chrome, page, [], 120)
    mm = re.search(r'<pre id="xlout">(.*?)</pre>', dom, re.S)
    assert mm, "страница не отдала результат пробы: " + log[-500:]
    out = json.loads(html.unescape(mm.group(1)))
    assert "err" not in out, out.get("err")
    return out, r


def _rows(b64: str) -> list[list]:
    ws = _wb(base64.b64decode(b64)).active
    return [[c.value for c in row] for row in ws.iter_rows()]


def test_журнал_шагов_в_excel_как_на_странице(page_run):
    """Случай владельца: шаг S1, «только сверх нормы» (по умолчанию) — в файле ровно заказы, где
    приёмка дольше 1 р.д., столько же, сколько в данных журнала; шапка — 11 столбцов таблицы;
    под таблицей — какой отбор выгружен. Все шаги с неизмеримыми — все строки журнала."""
    out, r = page_run
    J = r["ork_month"]["main"]["journal"]["rows"]
    s1_over = [x for x in J if x["seg"] == "S1" and x["measurable"] and (x["over"] or 0) > 0]
    j = out["journal"]
    assert out["jbar"] and j["file"] == "КВАНТ_Реализация_Месяц_Журнал шагов_2026-10-29.xlsx"
    assert j["head"] == ["Сделка", "Заказ", "Шаг", "Начало", "Конец", "Р.д.", "Норма", "Сверх нормы на",
                         "Отметки", "Хозяин", "Месяц"]
    rows = _rows(j["b64"])
    data = rows[1:1 + j["rows"]]
    assert j["rows"] == len(s1_over) > 0
    assert all(row[2].startswith("S1 ") and isinstance(row[7], int) and row[7] >= 1 for row in data)
    assert [row[7] for row in data] == sorted((x["over"] for x in s1_over), reverse=True)
    assert all(isinstance(row[5], int) and row[6] == 1 for row in data), "р.д. и норма — числами"
    assert all(re.fullmatch(r"\d\d\.\d\d\.\d{4}", row[4]) and re.fullmatch(r"\d\d\.\d{4}", row[10]) for row in data)
    assert rows[-1][0].startswith("Отбор: шаг S1 Приёмка сделки") and "только сверх нормы" in rows[-1][0]
    a = out["journalAll"]
    assert a["rows"] == len(J) and len(_rows(a["b64"])) == 1 + len(J) + 2


def test_очередь_недели_все_строки_а_не_первые_40(page_run):
    """Очередь «Что делать» показывает первые 40 и «Показать все»; кнопка выгружает весь отбор —
    в файле столько строк, сколько пунктов в очереди вида."""
    out, r = page_run
    q = out["queue"]
    assert q["shown"] == 40 and q["full"] and q["rows"] > 40
    assert q["file"].startswith("КВАНТ_Реализация_Неделя руководителя ОРК_Что делать") and "(" not in q["file"]
    assert len(_rows(q["b64"])) == 1 + q["rows"]


def test_таблица_сделок_как_на_странице(page_run):
    out, r = page_run
    d = out["deals"]
    assert d["rows"] == d["shown"] > 0 and not d["full"]
    assert d["head"][0] == "№" and "Сумма сделки" in d["head"]
    first = _rows(d["b64"])[1]
    assert isinstance(first[0], int), "№ сделки — числом"
