"""Вёрстка раздела «ork» вкладки «Реализация» без браузера: функции шаблона (_orkWeek,
_orkMonth, _orkHow) исполняются в node:vm на данных smoke-сборки (fixture.ContractsStub через
настоящий contracts.compute). Проверяется то, что читает руководитель ОРК: неизвестное не
рисуется нулём, «Месяц» без права — «нет доступа». Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import copy
import re
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from tests import fixture  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нет node")

TPL = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
START, END = "// ---- «Реализация» по регламенту ОРК", "function _ctPanel("

# узлы, которые трогает _orkWeek после innerHTML: списки, отборы, обработчики — заглушки
HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const node = () => ({innerHTML: '', value: '', textContent: '', dataset: {}, classList: {toggle() {}},
  addEventListener() {}, removeEventListener() {}, setAttribute() {}, scrollIntoView() {},
  closest() { return node(); }, querySelector() { return null; }, contains() { return false; }});
const byId = {};
const document = {getElementById: id => (byId[id] ||= node()), querySelectorAll: () => []};
const box = {document, window: {__ORKMONTH__: inp.orkMonth, __DEALCTX__: inp.dc || null, scrollTo() {}},
  location: {hash: ''}, console, JSON, Math, Object, Array, String, Number, Set, Map, RegExp,
  qmark: () => '', openContract() {}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + inp.unpack + '\n' + (inp.dcCode || 'function _dcData(){return null;}') + '\n' + inp.code
  + '\nthis._orkWeek=_orkWeek; this._orkMonth=_orkMonth; this._orkHow=_orkHow;'
  + ' this._orkWeekCtx=_orkWeekCtx; this._wkGroups=_wkGroups; this._wkgDetail=_wkgDetail;'
  + ' this._orkJData=_orkJData; this._orkJPick=_orkJPick; this._orkJRow=_orkJRow; this._orkJEmpty=_orkJEmpty;'
  + ' this._orkJNote=_orkJNote;', box);
const out = {};
for (const [k, X] of Object.entries(inp.views)) {
  const el = node();
  box._orkWeek(X, X.ork, el, '');
  // «Что сделать сегодня»: все группы раскрыты целиком — строки всех пунктов (как в панели за числом)
  const C = box._orkWeekCtx(X, X.ork);
  const today = box._wkGroups(C, {role: '', who: ''}).GS.map(g => box._wkgDetail(C, g, true)).join('');
  out[k] = {week: el.innerHTML + Object.values(byId).map(n => n.innerHTML).join(''), today,
            ids: Object.fromEntries(Object.entries(byId).map(([i, n]) => [i, n.innerHTML])),
            month: box._orkMonth(X, X.ork), how: box._orkHow(X, X.ork)};
  for (const id of Object.keys(byId)) delete byId[id];
}
process.stdout.write(JSON.stringify(out));
"""


def _plain(html: str) -> str:
    """Число со списком заказов за ним — кнопка (решение владельца 29.10.2026): для сверки
    подписей кнопка снимается, число остаётся."""
    import re
    return re.sub(r'<button type="button" class="odn[^"]*"[^>]*>(.*?)</button>', r"\1", html, flags=re.S)


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


def _fn(name: str) -> str:
    """Текст функции шаблона верхнего уровня — до следующей функции или комментария."""
    a = TPL.index(f"function {name}(")
    b = min(i for i in (TPL.find("\nfunction ", a + 1), TPL.find("\n//", a + 1)) if i > 0)
    return TPL[a:b]


def _render(views: dict, ork_month, dc: dict | None = None) -> dict:
    code = TPL[TPL.index(START):TPL.index(END)]
    inp = {"code": code, "esc": _line("const _esc="), "ctN": _line("const _ctN="), "unpack": _fn("_unpack"),
           "views": views, "orkMonth": ork_month}
    if dc is not None:
        # закрытая часть (право «договоры и деньги»): настоящие _dcData и списки «главного»
        inp["dc"] = dc
        inp["dcCode"] = "\n".join(_fn(n) for n in ("_dcData", "_dcImpBasis", "_dcImpList"))
    r = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False, default=str),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def built():
    r = fixture.build_realization()
    return json.loads(json.dumps(r, ensure_ascii=False, default=str))


def test_контрольные_точки_нет_данных_не_ноль(built):
    """W5 «Нет данных» (в производстве есть, плановых дат нет) — звонки «нет данных», не «0» и не
    «звонков нет»; W2/W3 «Нет данных» — подпись плитки, а не «в норме» и не «под угрозой ничего»."""
    ct = copy.deepcopy(built["contracts"])
    sep = ct["sep"]
    sep["dir"] = "sep"
    w = sep["ork"]["week"]
    w["tiles"]["W5"] = {"state": "nodata", "value": None, "n": 0, "cover": None, "prod": 3, "unknown": 0,
                        "need": "нет плановых дат производства у заказов шагов 6–7 — внести даты размещения (ОСС)"}
    w["checkpoints"] = []
    for k in ("W1", "W2", "W3"):
        w["tiles"][k] = {"state": "nodata", "value": None, "n": 0, "cover": None,
                         "need": f"стадия не из таблицы шагов ({k})"}
    w["overByWho"], w["risk"] = [], []
    sep["life"] = {**(sep.get("life") or {}), "queue": []}
    out = _plain(_render({"sep": sep}, built["ork_month"])["sep"]["week"])
    assert "Звонки поставщикам на неделе: нет данных" in out and "Звонки поставщикам на неделе: 0" not in out
    assert "Звонков поставщикам на этой неделе нет" not in out
    assert "нет плановых дат производства у заказов шагов 6–7" in out
    for k in ("W2", "W3"):
        assert f"стадия не из таблицы шагов ({k})" in out, k
    assert "Все шаги отдела в норме" not in out and "Под угрозой ничего нет" not in out and "Сорванных сроков нет" not in out


def test_контрольные_точки_известный_ноль(built):
    """W5 показан и точек нет — «звонков нет» (известный ноль); у части заказов нет дат —
    строка «без плановых дат производства: 2 из 5»."""
    ct = copy.deepcopy(built["contracts"])
    w = ct["ork"]["week"]
    w["tiles"]["W5"] = {**w["tiles"]["W5"], "state": "show", "value": 0, "noDates": 2, "prod": 5}
    w["checkpoints"] = []
    out = _plain(_render({"main": ct}, built["ork_month"])["main"]["week"])
    assert "Звонков поставщикам на этой неделе нет" in out
    assert "Без плановых дат производства: 2 из 5" in out


def test_месяц_без_права_нет_доступа(built):
    """Решение владельца 26.10.2026, п. 3: «Месяц» — только руководителям и владельцу. Без права
    (воркер вырезал ORKMONTH) — вся подвкладка «нет доступа», ни плиток отдела, ни S1; в «Как
    считается» у M1–M3 — «под правом»."""
    ct = copy.deepcopy(built["contracts"])
    без = _render({"main": ct}, {"__kvNoData": True})["main"]
    assert "нет доступа: нужно право «Реализация: показатели менеджеров ОРК»" in без["month"]
    for слово in ("M1 · шаги 4, 5, 8", "приёмка", "По менеджерам ОРК", "Своевременность шагов по месяцам"):
        assert слово not in без["month"], слово
    assert без["how"].count("под правом") >= 3
    с = _render({"main": ct}, built["ork_month"])["main"]
    assert "нет доступа" not in с["month"] and "M1 · шаги 4, 5, 8" in с["month"] and "По менеджерам ОРК" in с["month"]
    assert "Своевременность шагов по месяцам" in с["month"]


def test_ждём_упд_от_клиента_отдельно_от_сверх_нормы(built):
    """Решение владельца 27.10.2026, Б: шаг 12 больше 10 р.д. без УПД — «ждём УПД» в ведомости
    (вместо «дольше нормы»), своим столбцом в «Кто сколько ведёт» и счётчиком рядом с плитками;
    в W2 шага 12 нет. Числа — те же, что в данных недели (одно правило ork.judged)."""
    ct = copy.deepcopy(built["contracts"])
    w = ct["ork"]["week"]
    n = w["waitUtd"]["n"]
    assert n > 0 and w["ledger"][11]["no"] == 12 and w["ledger"][11]["waitUtd"] == n
    out = _plain(_render({"main": ct}, built["ork_month"])["main"]["week"])
    led = out[out.index('id="orkLed"'):]
    row12 = led[led.index('<span class="wkstn">12</span>'):]
    row12 = row12[:row12.index("</tr>")]
    assert f'УПД</abbr> {n}</span>' in row12, "ведомость, шаг 12"
    ppl = out[out.index('id="wkPeople"'):]
    assert "Ждём <abbr" in ppl[:ppl.index("</thead>")], "столбец «Ждём УПД» в «Кто сколько ведёт»"
    assert f"<td class=\"r\"><b>{n}</b></td></tr></tfoot>" in ppl, "итог столбца «Ждём УПД» = n"
    assert f"<b>{w['tiles']['W2']['value']}</b></td>" in ppl, "итог «дольше нормы» = W2"
    raw = _render({"main": ct}, built["ork_month"])["main"]["week"]
    assert re.search(r'data-od="w:waitUtd"[^>]*>' + str(n) + '</button>', raw[raw.index('id="wkMore"'):]), "счётчик «Ждём УПД»"
    assert "УПД (12):" not in out and "(эскалация УПД)" not in out


def test_ждём_упд_ноль_при_неизвестном_сроке_прочерк():
    """Все заказы шага 12 вида — срок не посчитать (нет факта доставки): в ведомости — «—», а не
    «ждём УПД 0» (ноль за неизвестное, правило 15); рядом «срок не посчитать: N», у счётчика
    «Ждём УПД» — тоже. Корпус — fixture.Шаг12Направления через настоящий contracts.compute; затем
    тот же вывод на ручной строке (waitUtd 0, overUnknown == now)."""
    data = fixture.build_contracts(stub=fixture.Шаг12Направления(wait=False))
    sep = json.loads(json.dumps(data["sep"], ensure_ascii=False, default=str))
    sep["dir"] = "sep"

    def cell(view):
        out = _plain(_render({"v": view}, None)["v"]["week"])
        i = out.index('<span class="wkstn">12</span>')
        row = out[i:out.index("</tr>", i)]
        return out, re.findall(r'<td class="r">(.*?)</td>', row)[1]
    out, c = cell(sep)
    assert "УПД</abbr> <span" not in c and ">—</span>" in c and "срок не посчитать: 1" in c, c
    assert "срок не посчитать: 1" in out[out.index('id="wkMore"'):]
    v = copy.deepcopy(sep)
    r12 = next(r for r in v["ork"]["week"]["ledger"] if r["no"] == 12)
    r12.update(now=2, over=0, waitUtd=0, overUnknown=2, notCounted=0)
    _o, c = cell(v)
    assert ">—</span>" in c and 'rzfinal">0<' not in c, c
    # известный ноль (срок есть, ждать нечего) — «ждём УПД 0», не прочерк
    r12.update(now=2, over=0, waitUtd=0, overUnknown=1, notCounted=0)
    _o, c = cell(v)
    assert 'УПД</abbr> <span class="rzfinal">0</span>' in c, c


def test_что_важно_сегодня_в_неделе_под_очередью(built):
    """Решение владельца 25.10.2026: «Что важно сегодня» — руководителю, закрытой частью. С
    26.10.2026 очередь «что делать» — в «Неделе»: блок — сразу под ней, строка ведёт в
    карточку (data-ct), хозяин — по регламенту ОРК; у направления — свой список. Без права
    («договоры и деньги») блока нет совсем."""
    ct, dc = built["contracts"], built["dealctx"]
    assert dc["important"] and dc["sepImportant"]
    sep = copy.deepcopy(ct["sep"])
    sep["dir"], sep["seg"] = "sep", "16"          # как renderContracts: вид направления помечен seg
    out = _render({"main": ct, "sep": sep}, built["ork_month"], dc=dc)
    week = out["main"]["week"]
    assert week.index("orkTodayS") < week.index("ctImpS"), "блок — под «Что сделать сегодня»"
    assert week.count('<tbody id="ctImp">') == 1
    for x in dc["important"]:
        assert f'data-ct="{x["deal"]}"' in week[week.index("ctImpS"):] and x["text"] in week
        assert x["role"] in week[week.index("ctImpS"):]
    assert not any(x["text"] in week for x in dc["sepImportant"]), "пункты направления — не в основном виде"
    assert all(x["text"] in out["sep"]["week"] for x in dc["sepImportant"])
    # «в копии» — как в очереди недели (ревью 28.10.2026): у срыва срока направления КАМ в копии
    с_копией = [x for x in dc["sepImportant"] if x.get("copy")]
    assert с_копией and all(f"в копии: {x['copy']}" in out["sep"]["week"] for x in с_копией)
    assert "(КАМ)" in out["sep"]["week"][out["sep"]["week"].index("ctImpS"):]
    # без закрытой части — ни заголовка, ни строк
    bare = _render({"main": ct}, built["ork_month"])["main"]["week"]
    assert "ctImpS" not in bare and "Что важно сегодня" not in bare
    # закрытая часть есть, а оценок с «главным» нет — «Нет данных», не пустая таблица
    empty = _render({"main": ct}, built["ork_month"], dc={**dc, "important": [], "sepImportant": []})["main"]["week"]
    assert "ctImpS" in empty and "Нет данных" in empty[empty.index("ctImpS"):] and 'id="ctImp"' not in empty


# ── номера заказов (владелец 29.10.2026: «сделка одна — заказы разные 1/2/3») ──────────────
@pytest.fixture(scope="module")
def три():
    """fixture.ТриЗаказа через настоящий contracts.compute: сделка 990 с тремя заказами у разных
    поставщиков, 933/3 с назначением в день создания, 991/1 с датой записи раньше создания."""
    ct = fixture.build_contracts(stub=fixture.ТриЗаказа())
    ct = json.loads(json.dumps(ct, ensure_ascii=False, default=str))
    return ct, ct.pop("orkMonth", None)


def _rows(html: str) -> list[str]:
    return [r for r in _nos(html).split("</tr>") if "<tr" in r]


def _nos(html: str) -> str:
    """«№» номера заказа — обычным шрифтом (span.rzn, ревью 30.10.2026): для сравнения строк — как текст."""
    return html.replace('<span class="rzn">№</span>', "№")


_KQ_JS = r"""
const vm = require('node:vm'); const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = {};
const mk = () => ({innerHTML: '', textContent: '', value: '', dataset: {}, classList: {toggle() {}},
  addEventListener() {}, querySelectorAll() { return []; }, closest() { return null; }});
const el = {querySelector: s => (nodes[s] ||= mk()), querySelectorAll: () => []};
const box = {window: {}, JSON, String, Math, Object, Array, Number, RegExp, el, K: inp.K};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.code + '\nfunction _kqHasCt(){return false;} function _incMoney(){return null;}'
  + ' function _fmtE(v){return String(v);}\n_kqWire(el, K, "kq");', box);
process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(nodes).map(([k, n]) => [k, n.innerHTML]))));
"""


def _kq_body(K: dict) -> str:
    """Очередь КАМа (_kqWire) в node:vm на данных K → innerHTML тела таблицы (#kqb)."""
    inp = {"esc": _line("const _esc="), "code": _line("const _ordNo=") + "\n" + _fn("_kqWire"), "K": K}
    r = subprocess.run(["node", "-e", _KQ_JS], input=json.dumps(inp, ensure_ascii=False, default=str),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)["#kqb"]



def test_неделя_строки_с_номером_заказа_поставщиком_и_сроком(три):
    """«Что сделать сегодня»: у каждой строки заказа — «990/1» (как в Битриксе), поставщик этого
    заказа и срок, по которому строка считана; у шага — «с дд.мм (…)». Три заказа одной сделки —
    три разные строки. Поставщика нет в данных страницы — строки без него."""
    ct, om = три
    v = copy.deepcopy(ct)
    v["life"]["queue"] = [x for x in v["life"]["queue"] if x["seq"] in (990, 933, 991)]
    qb = _rows(_render({"main": v}, om)["main"]["today"])
    for lbl, sup, ref in (("990/1", "Компания 401", "срок 14.10.2025"), ("990/2", "Компания 404", "срок 29.09.2026"),
                          ("990/3", "Компания 407", "срок 09.10.2026")):
        r = [x for x in qb if f">{lbl} ›<" in x and "просрочен на" in x]
        assert len(r) == 1 and sup in r[0] and ref in r[0], lbl
    r = [x for x in qb if ">933/3 ›<" in x and "с 14.10 (назначение)" in x]
    assert len(r) == 1 and "Менеджер ОРК" in r[0]
    r = [x for x in qb if ">991/1 ›<" in x and "с 15.10 (создан заказ)" in x]
    assert len(r) == 1 and "Руководитель ОРК" in r[0]
    # поставщика в данных нет — строка без него, номер и срок остаются
    for x in v["life"]["queue"]:
        x["sup"] = ""
    today = _render({"main": v}, om)["main"]["today"]
    assert "Компания 40" not in today
    assert ">990/2 ›<" in today and "срок 29.09.2026" in today


def test_карточка_заказа_номер_и_начало_счёта(три):
    """Карточка сделки (openContract → _ctOrdHtml): три заказа — три блока с номерами 990/1, 990/2,
    990/3, поставщиком и сроком клиента; у заказа на шаге с нормой — «шаг N · W р.д. при норме M»
    и «с дд.мм (назначение)»; у каждого — ссылка на заказ в Битриксе (смарт-процесс 172), а
    «Путь заказа →» — только когда на странице есть _opOpen (R3, 29.10.2026)."""
    ct, _om = три
    js = (r"""
const vm = require('node:vm'); const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8')); const out = {};
const box = {JSON, String, Math, out, inp, encodeURIComponent, window: {__CONTRACTS__: {ork: inp.ork}}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.helpers + '\nconst _stShort=n=>String(n||"").split(" | ")[0];\n' + inp.fn
  + '\nfor (const x of inp.rows) out[x.deal] = (x.orders||[]).map(o=>_ctOrdHtml(o,x));'
  + '\nthis._opOpen = function(){};\nout.withPath = _ctOrdHtml(inp.rows[0].orders[0], inp.rows[0]);', box);
process.stdout.write(JSON.stringify(out));
""")
    helpers = "\n".join(_line(p) for p in ("const _orkD=", "const _orkDm=", "const _orkDy=", "const _ctBxOrd=",
                                           "const _CT_ROLE=", "const _ctdNm=", "const _ctdT=", "const _ctdL="))
    rows = [r for r in ct["rows"] if r["deal"] == "6090" or r["seq"] == 933]
    inp = {"esc": _line("const _esc="), "helpers": helpers + "\n" + _fn("_ctStepNm"), "fn": _fn("_ctOrdHtml"),
           "rows": rows, "ork": ct["ork"]}
    r = subprocess.run(["node", "-e", js], input=json.dumps(inp, ensure_ascii=False), capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout)
    card = out["6090"]
    assert len(card) == 3
    for lbl, sup, dl in (("990/1", "Компания 401", "14.10.2025"), ("990/2", "Компания 404", "29.09.2026"),
                         ("990/3", "Компания 407", "09.10.2026")):
        hit = [h for h in card if f'<span class="no">{lbl}</span>. Узел' in h]
        assert len(hit) == 1 and sup in hit[0] and f"срок клиента {dl}" in hit[0], lbl
        assert "https://kvantpro.bitrix24.ru/crm/type/172/details/" in hit[0] and "в Битриксе ↗" in hit[0]
        assert 'target="_blank" rel="noopener"' in hit[0]
        assert "data-ctop=" not in hit[0], "без _opOpen кнопки пути нет"
    assert "просрочен на" in next(h for h in card if ">990/1<" in h), "990/1 — срок клиента прошёл"
    o933 = [h for h in out[next(r["deal"] for r in rows if r["seq"] == 933)] if ">933/3<" in h]
    # номер шага — всегда со словом: имя шага из ведомости «Недели» (ork.STEPS)
    nm4 = next(s["name"] for s in ct["ork"]["week"]["ledger"] if s["no"] == 4)
    # R4: «раб. дн.» вместо «р.д.» (спецификация 2.7), «Делает:» вместо «исполнитель шага:»
    assert len(o933) == 1 and f"шаг 4 «{nm4}» · 3 раб. дн. на шаге при норме 2" in o933[0] and "с 14.10 (назначение)" in o933[0]
    assert "Делает: " in o933[0] and "р.д." not in o933[0]
    assert 'data-ctop="' in out["withPath"] and "Путь заказа →" in out["withPath"]


def test_очередь_кама_заказы_строками(три):
    """Очередь КАМа: пункт сделки 990 «срок клиента прошёл» один (разговор с клиентом один), а
    заказы — три строки «↳ №990/1 · поставщик · срок · дни» по номеру."""
    import contracts
    import kamqueue
    ct, _om = три
    K = kamqueue.build(fixture.build_presale(), contracts.life_all(ct), ct["rows"], fixture.CT_TODAY,
                       people=fixture.CT_PEOPLE)
    K = json.loads(json.dumps(K, ensure_ascii=False, default=str))
    K["queue"] = [x for x in K["queue"] if x.get("seq") == 990]
    body = _kq_body(K)
    late = [x for x in _rows(body) if "срок клиента прошёл" in x]
    assert len(late) == 1, "пункт сделки один"
    assert "хозяин Олег" not in late[0], "хозяин заказа тот же, что у пункта, — не повторяется"
    lines = [ln for ln in late[0].split('<div class="dep">↳ ') if ln.startswith("<b")]
    assert len(lines) == 3
    for ln, (lbl, sup, ref, days) in zip(lines, (("990/1", "Компания 401", "срок 14.10.2025", 370),
                                                  ("990/2", "Компания 404", "срок 29.09.2026", 20),
                                                  ("990/3", "Компания 407", "срок 09.10.2026", 10))):
        assert f"№{lbl}</b>" in ln and sup in ln and ref in ln and f"{days} дн" in ln, ln


# ── ревью 29.10.2026: номер чужой сделки, W1, сделка без КАМа, право на поставщика ────────────
def test_номер_чужой_сделки_и_без_номера(три):
    """Номер в названии карточки — от другой сделки (карточку скопировали): строка под №990
    показывает «990 · 905/2», а не «905/2»; без номера — «990 · id …»."""
    ct, om = три
    v = copy.deepcopy(ct)
    q = [x for x in v["life"]["queue"] if x["seq"] == 990 and x["code"] == "late"]
    q[1]["ordLbl"], q[2]["ordLbl"] = "905/2", "id 99502"
    v["life"]["queue"] = q
    qb = _render({"main": v}, om)["main"]["today"]
    assert ">990/1 ›<" in qb and ">990 · 905/2 ›<" in qb and ">990 · id 99502 ›<" in qb
    assert ">905/2 ›<" not in qb


def test_w1_закончено_назначением_из_n(три):
    """Приёмка (W1): «закончено назначением» — из того же n, что «в срок за 20 р.д.» (не путается
    с k); «Заказ создан» с датой назначения не в счёте — по причинам (ключи Д-9)."""
    ct, om = три
    v = copy.deepcopy(ct)
    T1 = v["ork"]["week"]["tiles"]["W1"]
    T1["recent"] = {**(T1.get("recent") or {}), "state": "show", "k": 1, "n": 5}
    T1.update(byAssign=2, assignStale=1, assignNoMgr=3)
    out = _plain(_render({"main": v}, om)["main"]["week"])
    assert "за 20 раб. дн. принято в срок 1 из 5" in out
    assert "из них приёмку закрыло назначение менеджера <abbr" in out and "</abbr> — 2." in out
    assert "Назначение не засчитано («Заказ создан»): дата раньше начала приёмки — 1, назначен не сотрудник <abbr" in out
    T1.update(byAssign=0, assignStale=0, assignNoMgr=0)
    out = _plain(_render({"main": v}, om)["main"]["week"])
    assert "приёмку закрыло назначение" not in out and "Назначение не засчитано" not in out


def test_очередь_кама_без_кама_заказы_строками():
    """Сделка без КАМа: сигнал «срок клиента прошёл» в пункте «назначить КАМа» — с заказами
    строками «↳ №902/1 · поставщик · срок · дни», как у пункта с КАМом; хозяин заказа — если он
    у заказа другой."""
    import kamqueue
    today = fixture.CT_TODAY

    def q(n, sup, ref, days, who):
        return {"code": "late", "deal": "6902", "days": days, "kamId": "", "kam": "", "who": who, "seq": 902,
                "customer": "Заказчик 1", "title": f"902/{n}. Узел", "dl": "", "ordLbl": f"902/{n}", "ordNo": n,
                "sup": sup, "ref": ref}
    life = {"queue": [q(2, "Поставщик Б", "срок 05.09.2026", 44, "Пётр (ОРК)"),
                      q(1, "Поставщик А", "срок 01.09.2026", 48, "Олег (ОРК)")]}
    rows = [{"deal": "6902", "seq": 902, "customer": "Заказчик 1", "title": "Сделка 1", "kam": "", "kamId": "",
             "closed": False, "lost": False, "stageName": "Производство", "stageDays": 3, "orders": []}]
    K = json.loads(json.dumps(kamqueue.build(None, life, rows, today), ensure_ascii=False, default=str))
    body = _kq_body(K)
    r = [x for x in _rows(body) if "назначить КАМа" in x]
    assert len(r) == 1
    lines = [ln for ln in r[0].split('<div class="dep">↳ ') if ln.startswith("<b")]
    assert len(lines) == 2, r[0]
    assert "№902/1</b> · поставщик Поставщик А · срок 01.09.2026 · 48 дн" in lines[0]
    assert "хозяин" not in lines[0], "хозяин тот же, что у пункта"
    assert "№902/2</b> · поставщик Поставщик Б · срок 05.09.2026 · 44 дн · хозяин Пётр (ОРК)" in lines[1]


def test_поставщик_только_зрителю_с_правом(три):
    """Право на поставщика — право на массив (access/acl.js, cutDashboard): имена поставщиков
    очереди («Неделя», CONTRACTS) и строк заказов очереди КАМа (PEOPLE) доходят только до того,
    кому массив выдан. PEOPLE и раньше нёс поставщика (сроки ≤ 14 дн., upcoming14): строки заказов
    круг зрителей не расширяют. Зритель без «Реализации», «Поставщиков» и «КАМов» — ни одного имени."""
    import contracts
    import dashboard
    import kamqueue
    ct, _om = три
    K = kamqueue.build(fixture.build_presale(), contracts.life_all(ct), ct["rows"], fixture.CT_TODAY,
                       people=fixture.CT_PEOPLE)
    K = json.loads(json.dumps(K, ensure_ascii=False, default=str))
    assert any(u.get("sup") for u in K.get("upcoming14") or []), "PEOPLE и раньше нёс поставщика"
    assert any(o.get("sup") for x in K["queue"] for o in x.get("ords") or [])
    html = dashboard.render({}, {}, contracts=ct, people={"kamQueue": K})
    js = r"""
import fs from 'node:fs';
const {cutDashboard} = await import(process.argv[1]);
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {};
for (const [k, tabs] of Object.entries(inp.views)) {
  const page = cutDashboard(inp.html, tabs, {admin: false, rights: ['suppliers']});
  const line = p => (page.split('\n').find(l => l.startsWith(`window.__${p}__`)) || '');
  out[k] = {any: page.includes('Компания 40'), people: line('PEOPLE').includes('Компания 40'),
            contracts: line('CONTRACTS').includes('Компания 40')};
}
process.stdout.write(JSON.stringify(out));
"""
    views = {"нет": ["sourcing", "company"], "кам": ["reps"], "реал": ["contracts"]}
    r = subprocess.run(["node", "--input-type=module", "-e", js, (ROOT / "access" / "acl.js").as_uri()],
                       input=json.dumps({"html": html, "views": views}, ensure_ascii=False),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout)
    assert out["нет"] == {"any": False, "people": False, "contracts": False}
    assert out["кам"] == {"any": True, "people": True, "contracts": False}
    assert out["реал"] == {"any": True, "people": False, "contracts": True}


# ── «Журнал шагов» «Месяца» (просьба владельца 29.10.2026) ─────────────────────────────────
JHARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const box = {window: {__ORKMONTH__: inp.orkMonth}, console, JSON, Math, Object, Array, String, Number, Set,
  Map, RegExp, qmark: () => '', openContract() {}, document: {getElementById: () => null}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + inp.unpack + '\nfunction _dcData(){return null;}\n' + inp.code, box);
vm.runInContext(`
const J=_orkJData(window.__ORKMONTH__.main), cust=d=>'Заказчик '+d, out={};
const F0={seg:'S1',m:'',over:true,all:false};
const P=_orkJPick(J.rows,F0);
out.def={n:P.R.length,k:P.k,segs:[...new Set(P.R.map(r=>r.seg))],overs:P.R.map(r=>r.over),
  html:P.R.map(r=>_orkJRow(r,cust)).join(''),note:_orkJNote(J,F0)};
const FA={seg:'',m:'',over:false,all:true}, PA=_orkJPick(J.rows,FA);
out.all={n:PA.R.length,base:PA.base.length,note:_orkJNote(J,FA)};
const FM={seg:'S1',m:J.months[1],over:true,all:false}, PM=_orkJPick(J.rows,FM);
out.mon={n:PM.R.length,m:J.months[1]};
const E={rows:[],segs:J.segs,months:J.months,since:J.since,label:J.label};
out.empty=_orkJEmpty(E,F0,_orkJPick([],F0));
const F6={seg:'S6',m:'08.2026',over:true,all:false};
out.noSeg=_orkJEmpty(J,F6,_orkJPick(J.rows,F6));
const ok=J.rows.filter(r=>r.measurable&&!(r.over>0)).slice(0,1);
out.allOk=_orkJEmpty(J,F0,{base:ok,meas:ok,R:[]});
const hid=J.rows.find(r=>r.ownerHidden); out.hidRow=hid?_orkJRow(hid,cust):'';
const s1=J.rows.find(r=>r.seg==='S1');
out.noHead=_orkJRow(Object.assign({},s1,{owner:''}),cust,()=>true);
out.noCard=_orkJRow(s1,cust,()=>false); out.card=_orkJRow(s1,cust,d=>d===s1.deal);
this.out=out;`, box);
process.stdout.write(JSON.stringify(box.out));
"""


def _jrun(ork_month) -> dict:
    code = TPL[TPL.index(START):TPL.index(END)]
    inp = {"code": code, "esc": _line("const _esc="), "ctN": _line("const _ctN="), "unpack": _fn("_unpack"),
           "orkMonth": ork_month}
    r = subprocess.run(["node", "-e", JHARNESS], input=json.dumps(inp, ensure_ascii=False, default=str),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def test_журнал_шагов_в_месяце_под_правом(built):
    """«Месяц» с правом — блок «Журнал шагов» с отбором (шаг S1 — по умолчанию, месяц, «только
    сверх нормы» включено, «показывать неизмеримые» выключено), таблицей из 11 столбцов и
    пояснением, откуда срок; без права — ни блока, ни строк (весь «Месяц» — «нет доступа»)."""
    ct = copy.deepcopy(built["contracts"])
    с = _render({"main": ct}, built["ork_month"])["main"]["month"]
    assert "Журнал шагов: срок каждого заказа" in с and 'data-xl-name="Журнал шагов"' in с
    assert '<option value="S1" selected>Приёмка сделки — S1 (шаг 1), норма 1 раб. дн.</option>' in с
    assert '<input type="checkbox" id="orkJOver" checked>' in с and '<input type="checkbox" id="orkJAll">' in с
    for m in built["ork_month"]["main"]["journal"]["months"]:
        mm, yy = m.split(".")
        word = ("январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь".split())[int(mm) - 1]
        assert f'<option value="{m}">{word} {yy}</option>' in с, m
    for th in ("Сделка", "Заказ", "Шаг", "Начало", "Конец", "Шёл, раб. дн.", "Норма, раб. дн.", "Дольше нормы, раб. дн.",
               "Почему не считаем", "Кто делал", "Месяц"):
        assert f'scope="col">{th}</th>' in с, th
    assert "по датам смены стадий заказа в Битриксе" in с and "производственному календарю РФ" in с
    assert "«пачкой»" in с and "срок не дают" in с
    assert с.index("Своевременность шагов по месяцам") < с.index("Журнал шагов") < с.index('id="orkMgrH">По менеджерам ОРК')
    без = _render({"main": ct}, {"__kvNoData": True})["main"]["month"]
    assert "Журнал шагов" not in без and "orkJb" not in без
    # массив «Месяца» без журнала (сборка старше) — «Нет данных» и откуда придёт
    om = copy.deepcopy(built["ork_month"])
    om["main"].pop("journal")
    нет = _render({"main": ct}, om)["main"]["month"]
    assert "Нет данных: журнала шагов в сборке нет" in нет and "orkJb" not in нет


def test_журнал_отбор_строки_и_пусто(built):
    """Отбор по умолчанию — случай владельца: приёмка (S1) дольше нормы, только измеримые, по
    убыванию превышения; строка ведёт в карточку сделки (data-ct). Пустой отбор — «Нет данных» и
    что не нашлось; все в норме — известный ноль, не «нет данных»."""
    om = built["ork_month"]
    rows = om["main"]["journal"]["rows"]
    s1 = [r for r in rows if r["seg"] == "S1" and r["measurable"] and (r["over"] or 0) > 0]
    out = _jrun(om)
    d = out["def"]
    assert d["n"] == d["k"] == len(s1) > 0 and d["segs"] == ["S1"]
    assert d["overs"] == sorted(d["overs"], reverse=True)
    for r in s1:
        assert f'data-ct="{r["deal"]}"' in d["html"] and f'>{r["over"]}<span class="ctm-s">дольше нормы</span></td>' in d["html"]
        # R4: номер заказа — кнопка пути заказа (У4), «в Битриксе ↗» — рядом
        assert f'data-jop="{r["order"]}"' in d["html"]
    assert d["html"].count("<tr ") == len(s1) and "р.д." not in d["html"]
    assert d["note"].startswith("Отбор: Приёмка сделки — S1 (шаг 1) · все месяцы окна · только дольше нормы · без шагов без честного срока")
    assert out["all"]["n"] == out["all"]["base"] == len(rows) and "все шаги" in out["all"]["note"]
    assert out["mon"]["n"] == sum(1 for r in s1 if r["m"] == out["mon"]["m"]) > 0
    assert out["empty"].startswith("Нет данных: в окне «Месяца» нет законченных шагов")
    assert out["noSeg"].startswith("Нет данных: шагов «Подготовка к отгрузке от поставщика», закончившихся в августе 2026, нет")
    assert out["allOk"].startswith("Дольше нормы — никого: все 1 шаг отбора в норме")
    # имя скрыто воротами людей — «скрыто» и причина словами (это была бы оценка одного человека), а не имя
    assert "скрыто" in out["hidRow"] and "оценка одного сотрудника" in out["hidRow"]
    # имени руководителя ОРК в справочнике нет — роль один раз, а не «руководитель ОРК руководитель ОРК»
    nh = out["noHead"]
    assert nh.count("руководитель ОРК") == 1 and "имени в справочнике нет" in nh
    # сделки нет в виде — строка не «кликабельна»: ни drill, ни tabindex, ни data-ct
    assert 'class="drill"' in out["card"] and 'tabindex="0"' in out["card"]
    assert "drill" not in out["noCard"] and "tabindex" not in out["noCard"] and "data-ct" not in out["noCard"]
