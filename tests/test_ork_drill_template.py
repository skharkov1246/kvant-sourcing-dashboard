"""Список заказов за числом — вёрстка без браузера (node:vm, как test_ork_template): каждое
число «Недели», «Месяца» и «Квартала», отрисованное кнопкой списка ([data-od]), открывает
список ровно из стольких строк, сколько в числе; плитки, «Ждут смежников», «Сверх нормы по
менеджерам» и «Пульс ОРК» — тоже; отбор по шагу и сочетание отборов в списке; пустая очередь
из-за сочетания отборов — «по этому сочетанию отборов пусто», а не «всё по регламенту».
Решение владельца 29.10.2026, «Неделя», п. 4–5. Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import html as htmlmod
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

HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const node = () => ({innerHTML: '', value: '', textContent: '', dataset: {}, classList: {toggle() {}, add() {}, remove() {}},
  addEventListener() {}, removeEventListener() {}, setAttribute() {}, scrollIntoView() {},
  closest() { return node(); }, querySelector() { return null; }, contains() { return false; }});
const byId = {};
const document = {getElementById: id => (byId[id] ||= node()), querySelectorAll: () => [], querySelector: () => null};
const box = {document, window: {__ORKMONTH__: inp.orkMonth, __CONTRACTS__: inp.ct, __DEALCTX__: null, scrollTo() {}},
  location: {hash: ''}, console, JSON, Math, Object, Array, String, Number, Set, Map, RegExp,
  qmark: () => '', openContract() {}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\nfunction _dcData(){return null;}\n' + inp.code
  + '\nthis._orkWeek=_orkWeek; this._orkMonth=_orkMonth; this._orkQuarter=_orkQuarter; this._odModel=_odModel;'
  + ' this._odHtml=_odHtml; this._orkQEmpty=_orkQEmpty; this._orkPulse=_orkPulse; this._odSub=_odSub;', box);
const unesc = s => String(s).replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const attr = (tag, a) => { const m = new RegExp(' ' + a + '="([^"]*)"').exec(tag); return m ? unesc(m[1]) : null; };
const F0 = {step: null, sup: '', mgr: '', tag: ''};
const out = {};
for (const [k, X] of Object.entries(inp.views)) {
  const el = node();
  box._orkWeek(X, X.ork, el, '');
  const pages = {week: el.innerHTML + Object.values(byId).map(n => n.innerHTML).join(''),
                 month: box._orkMonth(X, X.ork), quarter: X.ork.quarter ? box._orkQuarter(X, X.ork) : ''};
  for (const id of Object.keys(byId)) delete byId[id];
  const found = [];
  for (const [sub, h] of Object.entries(pages)) {
    // любой элемент с data-od: кнопка числа (до </button> — кнопки не вкладываются), плитка, строка «Пульса»
    let at = 0;
    while ((at = h.indexOf(' data-od="', at)) >= 0) {
      const a = h.lastIndexOf('<', at), b = h.indexOf('>', at), tag = h.slice(a, b + 1);
      at = b;
      const el = /^<(\w+)/.exec(tag)[1], id = attr(tag, 'data-od'), step = attr(tag, 'data-odstep');
      let text = null;
      if (el === 'button') {
        const inner = h.slice(b + 1, h.indexOf('</button>', b));
        const bold = /<b>([^<]*)<\/b>/.exec(inner);
        text = (bold ? bold[1] : inner.replace(/<[^>]+>/g, '')).trim();
      }
      const M = box._odModel(X, X.ork, {id, F: step != null ? {step} : null});
      const html = M ? box._odHtml(M, Object.assign({}, F0, M.F0 || {})) : '';
      found.push({sub, el, id, step, cls: attr(tag, 'class'), text,
                  items: M ? M.items.length : null, rows: (html.match(/<tr class="drill" data-odct=/g) || []).length});
    }
  }
  const extra = {};
  for (const t of ['W1', 'W2', 'W3', 'W5']) { const M = box._odModel(X, X.ork, {id: 'w:' + t}); extra[t] = M ? M.items.length : null; }
  for (const m of (X.ork.week.overByWho || [])) { const M = box._odModel(X, X.ork, {id: `w:who/${m.who}/all`}); extra['who:' + m.who] = M ? M.items.length : null; }
  // отборы в списке: шаг; сочетание шаг + несуществующий поставщик — «по этому сочетанию отборов пусто»
  const M = box._odModel(X, X.ork, {id: 'w:live'});
  const steps = M.items.map(x => x.r.step == null ? '?' : String(x.r.step));
  const s0 = steps[0];
  extra.filter = {step: s0, want: steps.filter(s => s === s0).length,
    rows: (box._odHtml(M, Object.assign({}, F0, {step: s0})).match(/<tr class="drill" data-odct=/g) || []).length,
    combo: box._odHtml(M, Object.assign({}, F0, {step: s0, sup: 'нет такого поставщика'})),
    one: box._odHtml(M, Object.assign({}, F0, {sup: 'нет такого поставщика'}))};
  // все заказы «Квартала» одним списком: закрытые — своей корзиной, менеджеров ОРК нет
  const QR = X.ork.quarter ? box._odModel(X, X.ork, {keys: {k: (X.ork.quarter.orders || []).map(r => r.k), t: null}, pre: 'q', title: 'Квартал'}) : null;
  extra.qall = QR ? box._odHtml(QR, Object.assign({}, F0)) : '';
  // за числом заказов нет — «За этим числом заказов нет», а не «по этому отбору пусто»
  extra.empty = box._odHtml(box._odModel(X, X.ork, {keys: {k: [], t: null}, pre: 'w', title: 'пусто'}), Object.assign({}, F0));
  const MH = box._odModel(X, X.ork, {id: 'w:hyg/all'});
  extra.hyg = MH ? box._odHtml(MH, Object.assign({}, F0)) : '';
  out[k] = {found, extra};
}
out.qempty = {two: box._orkQEmpty({codeLbl: 'a', stepLbl: 'b'}), one: box._orkQEmpty({codeLbl: 'a'}), none: box._orkQEmpty({})};
const W = JSON.parse(JSON.stringify(inp.views.main.ork.week));
W.pulse.rows[0].gone = true; W.pulse.rows[0].who = W.pulse.rows[0].who + ' (уволен)';
out.pulseGone = box._orkPulse(W);
process.stdout.write(JSON.stringify(out));
"""


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


@pytest.fixture(scope="module")
def run():
    r = json.loads(json.dumps(fixture.build_realization(), ensure_ascii=False, default=str))
    ct = r["contracts"]
    sep = dict(ct["sep"], rows=[x for x in ct["rows"] if x.get("seg")], dir="sep", seg="16")
    sep["ork"] = dict(sep["ork"], quarter=ct["ork"]["quarter"])     # как renderContracts: квартал — общий
    main = dict(ct, rows=[x for x in ct["rows"] if not x.get("seg")], dir="main")
    inp = {"code": TPL[TPL.index(START):TPL.index(END)], "esc": _line("const _esc="), "ctN": _line("const _ctN="),
           "views": {"main": main, "sep": sep}, "orkMonth": r["ork_month"], "ct": ct}
    p = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False), capture_output=True,
                       text=True, timeout=180)
    assert p.returncode == 0, p.stderr[-2000:]
    return r, json.loads(p.stdout)


def _n_of(payload, dk: str):
    """n показателя «Месяца» или «Квартала» с этим dk (живых — у строки человека)."""
    hits = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            if o.get("dk") == dk:
                hits.append(o.get("n"))
            if o.get("liveDk") == dk:
                hits.append(o.get("live"))
            if o.get("nDk") == dk:
                hits.append(o.get("n"))
            for k, v in o.items():
                if k not in ("drill", "orders"):
                    walk(v)
    walk(payload)
    return hits


def test_каждое_число_открывает_ровно_свои_заказы(run):
    r, out = run
    payload = {"ct": r["contracts"]["ork"], "month": r["ork_month"]}
    for view in ("main", "sep"):
        found = out[view]["found"]
        assert len([f for f in found if f["sub"] == "week"]) >= 10, view
        pulse = (r["contracts"] if view == "main" else r["contracts"]["sep"])["ork"]["week"]["pulse"]["rows"]
        for f in found:
            assert f["items"] is not None, f
            if f["step"] is None:
                assert f["rows"] == f["items"], f          # список без отбора — все строки числа
            if f["id"].startswith("w:") and f["el"] == "button":
                # кнопка числа «Недели»: строк столько, сколько в числе (с отбором шага — в отборе)
                assert f["rows"] == int(f["text"]), f
            elif f["id"].startswith("w:pulse/") and f["el"] == "tr":
                assert f["items"] == pulse[int(f["id"].split("/")[1])]["n"], f
            elif f["id"][:2] in ("m:", "q:"):
                n = _n_of(payload, f["id"])
                assert n and f["items"] == n[0], f
                if f["el"] == "button" and f["text"].isdigit():
                    assert int(f["text"]) == f["rows"], f
            else:
                raise AssertionError(f"число без проверки: {f}")
    subs = {f["sub"] for f in out["main"]["found"]}
    assert subs == {"week", "month", "quarter"}


def test_плитки_и_строки_сверх_нормы(run):
    r, out = run
    for view, v in (("main", r["contracts"]), ("sep", r["contracts"]["sep"])):
        w, ex = v["ork"]["week"], out[view]["extra"]
        for t in ("W1", "W2", "W3", "W5"):
            if w["tiles"][t].get("state") in ("show", "prelim"):
                assert ex[t] == w["tiles"][t]["value"], (view, t)
        for m in w["overByWho"]:
            assert ex["who:" + m["who"]] == m["n"] + m["waitUtd"], (view, m["who"])


def test_отборы_в_списке(run):
    _r, out = run
    f = out["main"]["extra"]["filter"]
    assert f["rows"] == f["want"] > 0, f
    assert "По этому сочетанию отборов пусто." in f["combo"] and "Сбросить" in f["combo"]
    assert "По этому отбору пусто." in f["one"]
    assert 'data-odf="rm:step"' in f["combo"] and 'data-odf="rm:sup"' in f["combo"], "отборы снимаются по одному"


def test_очередь_пуста_из_за_сочетания(run):
    _r, out = run
    q = out["qempty"]
    assert "по этому сочетанию отборов пусто" in q["two"] and "регламент" not in q["two"] and "data-rmall" in q["two"]
    assert "всё по регламенту" in q["one"] and "всё по регламенту" in q["none"]


def test_пульс_отрисован_и_ушедший_помечен(run):
    r, out = run
    gone = out["pulseGone"]
    assert "Пульс ОРК: заказы в работе по людям" in gone and '<span class="gone">ушёл</span>' in gone
    assert "(уволен)" not in htmlmod.unescape(gone.split("</thead>")[1].split("data-ods")[0]), "подпись без «(уволен)»"
    P = r["contracts"]["ork"]["week"]["pulse"]
    assert gone.count('data-od="w:pulse/') >= len(P["rows"])
    week = [f for f in out["main"]["found"] if f["sub"] == "week" and f["id"].startswith("w:pulse/") and f["el"] == "tr"]
    assert len(week) == len(P["rows"])
    # шапка списка человека — «ФИО · N заказов в работе · X % от всех живых · сделок M · заказчиков C»
    assert all(f["items"] == p["n"] for f, p in zip(week, P["rows"]))


def _dks(o, out: list) -> list:
    """Все списки, которые данные «Месяца» и «Квартала» обещают числам: dk у показанного,
    liveDk, nDk."""
    if isinstance(o, list):
        for x in o:
            _dks(x, out)
    elif isinstance(o, dict):
        if o.get("dk") and o.get("state") in ("show", "prelim"):
            out.append(o["dk"])
        for f in ("liveDk", "nDk"):
            if o.get(f):
                out.append(o[f])
        for k, v in o.items():
            if k not in ("drill", "orders"):
                _dks(v, out)
    return out


def test_у_каждого_списка_в_данных_есть_число_на_странице(run):
    """Обратная сверка: каждый список, собранный orkview для «Месяца» и «Квартала» (dk, liveDk,
    nDk), отрисован числом со списком — ни одного числа текстом при собранном списке (полнота
    текущего месяца у менеджера, «Готовностей» строки поставщика со скрытой долей)."""
    r, out = run
    om = r["ork_month"]
    for view in ("main", "sep"):
        ids = {f["id"] for f in out[view]["found"]}
        q = r["contracts"]["ork"]["quarter"]                  # квартал — общий для видов
        want = _dks(q, []) + _dks((om.get(view) or {}), [])
        assert not [d for d in want if d not in ids], view
    main = _dks(r["contracts"]["ork"]["quarter"], []) + _dks(om["main"], [])
    assert any(d.endswith("n") and d.startswith("q:") for d in main), "корпус: строка поставщика со скрытой долей"
    assert any(".0.M2" in d and d.startswith("m:p") for d in main), "корпус: полнота текущего месяца у менеджера"


def test_закрытые_пусто_гигиена_и_дубль_для_телефона(run):
    """Список: закрытый заказ — корзиной «заказ закрыт», не «стадия не из таблицы»; в «Квартале» —
    без «По менеджерам ОРК»; за числом без заказов — «За этим числом заказов нет»; у гигиены —
    вид и почему (hw); дубль «стадия · поставщик» для узкого экрана — атрибутом, не текстом ячейки."""
    import re
    r, out = run
    ex = out["main"]["extra"]
    assert 'data-odf="step:x"' in ex["qall"] and "заказ закрыт" in ex["qall"]
    assert "По менеджерам ОРК" not in ex["qall"] and "По поставщикам" in ex["qall"]
    assert "За этим числом заказов нет." in ex["empty"] and "отбору" not in ex["empty"]
    w = r["contracts"]["ork"]["week"]
    import orkview
    hw = [x["hw"] for x in orkview.unpack(w["orders"], w["strs"]) if x.get("hw") and x.get("hyg")]
    assert hw and htmlmod.escape(hw[0], quote=False) in ex["hyg"].replace("&quot;", '"')
    assert re.search(r'class="dep odm" data-odm="[^"]+"></div>', ex["hyg"])
    assert "По менеджерам ОРК" in ex["hyg"], "«Неделя»: менеджеры ОРК в списке есть"
