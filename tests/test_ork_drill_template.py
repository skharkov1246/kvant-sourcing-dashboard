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
import re
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
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + inp.unpack + '\nfunction _dcData(){return null;}\n' + inp.code
  + '\nthis._orkWeek=_orkWeek; this._orkMonth=_orkMonth; this._orkQuarter=_orkQuarter; this._odModel=_odModel;'
  + ' this._odHtml=_odHtml; this._orkWeekCtx=_orkWeekCtx; this._orkWeekToday=_orkWeekToday; this._orkWeekPeople=_orkWeekPeople; this._odSub=_odSub;'
  + ' this._odPrep=_odPrep; this._odDef=_odDef;', box);
const unesc = s => String(s).replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const attr = (tag, a) => { const m = new RegExp(' ' + a + '="([^"]*)"').exec(tag); return m ? unesc(m[1]) : null; };
const F0 = {step: null, sup: '', mgr: '', tag: ''};
const out = {};
// запасная строка (_odBase: заказ не из строк части — закрытый заказ пункта «оплата»): номер — lbl
// из данных (ork.order_label), а не разбор названия: дата в начале названия — не номер
const R0 = box.window.__CONTRACTS__.rows[0];
R0.orders = (R0.orders || []).concat([{id: '79001', title: '29.10 доп. поставка', lbl: 'id 79001', live: false, sup: 'Компания 9'},
  {id: '79002', title: '905 / 2 Поставка', lbl: '905/2', live: false, sup: 'Компания 9'}]);
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
      const el = /^<(\w+)/.exec(tag)[1], id = attr(tag, 'data-od'), step = attr(tag, 'data-odstep'), otag = attr(tag, 'data-odtag');
      let text = null;
      if (el === 'button') {
        const inner = h.slice(b + 1, h.indexOf('</button>', b));
        const bold = /<b>([^<]*)<\/b>/.exec(inner);
        text = (bold ? bold[1] : inner.replace(/<[^>]+>/g, '')).trim();
      }
      // начальный отбор — как _odBind: шаг, иначе подпись (часть числа: «сорван 19» у W3)
      const M = box._odModel(X, X.ork, {id, F: step != null ? {step} : otag != null ? {tag: otag} : null});
      const html = M ? box._odHtml(M, Object.assign({}, F0, M.F0 || {})) : '';
      found.push({sub, el, id, step, tag: otag, cls: attr(tag, 'class'), text,
                  items: M ? M.items.length : null, rows: (html.match(/<tr class="drill" data-odk="[^"]*" data-odct=/g) || []).length});
    }
  }
  const extra = {};
  for (const t of ['W1', 'W2', 'W3', 'W5']) { const M = box._odModel(X, X.ork, {id: 'w:' + t}); extra[t] = M ? M.items.length : null; }
  for (const m of (X.ork.week.overByWho || [])) { const M = box._odModel(X, X.ork, {id: `w:who/${m.who}/all`}); extra['who:' + m.who] = M ? M.items.length : null; }
  // отборы в списке: шаг; сочетание шаг + несуществующий поставщик — «по этому сочетанию отборов пусто»
  const M = box._odModel(X, X.ork, {id: 'w:live'});
  const steps = M.items.map(x => x.r.step == null ? '?' : String(x.r.step));
  const s0 = steps[0];
  extra.live = box._odHtml(M, Object.assign({}, F0));
  extra.filter = {step: s0, want: steps.filter(s => s === s0).length,
    rows: (box._odHtml(M, Object.assign({}, F0, {step: s0})).match(/<tr class="drill" data-odk="[^"]*" data-odct=/g) || []).length,
    combo: box._odHtml(M, Object.assign({}, F0, {step: s0, sup: 'нет такого поставщика'})),
    one: box._odHtml(M, Object.assign({}, F0, {sup: 'нет такого поставщика'}))};
  // все заказы «Квартала» одним списком: закрытые — своей корзиной, менеджеров ОРК нет
  const QR = X.ork.quarter ? box._odModel(X, X.ork, {keys: {k: (X.ork.quarter.orders || []).map(r => r.k), t: null}, pre: 'q', title: 'Квартал'}) : null;
  extra.qall = QR ? box._odHtml(QR, Object.assign({}, F0)) : '';
  // за числом заказов нет — «За этим числом заказов нет», а не «по этому отбору пусто»
  extra.empty = box._odHtml(box._odModel(X, X.ork, {keys: {k: [], t: null}, pre: 'w', title: 'пусто'}), Object.assign({}, F0));
  const MH = box._odModel(X, X.ork, {id: 'w:hyg/all'});
  extra.hyg = MH ? box._odHtml(MH, Object.assign({}, F0)) : '';
  // «Разбор числа» W3: «Что делать» (группы по следующему действию), худшие сверху, отбор по
  // действию, поиск, сортировка по номеру, ссылки в Битрикс, шапка для выгрузки
  const M3 = box._odModel(X, X.ork, {id: 'w:W3'});
  if (M3 && M3.items.length) {
    const P3 = box._odPrep(M3), keys = h => [...h.matchAll(/<tr class="drill" data-odk="([^"]*)"/g)].map(m => unesc(m[1]));
    const h3 = box._odHtml(M3, Object.assign({}, F0)), g0 = P3.groups[0], q0 = String(M3.items[0].r.no);
    extra.w3 = {n: M3.items.length, html: h3, order: keys(h3), def: box._odDef(M3),
      groups: P3.groups.map(g => ({key: g.key, n: g.n, txt: g.txt, role: g.role})),
      items: M3.items.map(x => ({k: x.k, no: x.r.no || '', late: x.late, deal: x.r.deal || '', isD: x.k.startsWith('d:'),
        missing: !!x.r.missing, act: x.a.key})),
      g0: keys(box._odHtml(M3, Object.assign({}, F0, {act: g0.key}))),
      q: q0, qrows: keys(box._odHtml(M3, Object.assign({}, F0, {q: q0}))),
      sortNo: keys(box._odHtml(M3, Object.assign({}, F0, {sort: 'no', dir: 1})))};
  }
  out[k] = {found, extra};
}
out.fallback = box._odHtml(box._odModel(inp.views.main, inp.views.main.ork,
  {keys: {k: ['79001', '79002', '79999'], t: null}, pre: 'w', title: 'запасные'}), Object.assign({}, F0));
const CM = box._orkWeekCtx(inp.views.main, inp.views.main.ork);
out.qempty = {role: box._orkWeekToday(CM, {role: 'нет такой роли', who: ''}), all: box._orkWeekToday(CM, {role: '', who: ''})};
const W = JSON.parse(JSON.stringify(inp.views.main.ork.week));
W.pulse.rows[0].gone = true; W.pulse.rows[0].who = W.pulse.rows[0].who + ' (уволен)';
out.pulseGone = box._orkWeekPeople(box._orkWeekCtx(inp.views.main, Object.assign({}, inp.views.main.ork, {week: W})));
process.stdout.write(JSON.stringify(out));
"""


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


def _fn(name: str) -> str:
    """Текст функции шаблона верхнего уровня — до следующей функции или комментария (как
    test_ork_template): «Журнал шагов» «Месяца» разворачивает упакованный список _unpack."""
    a = TPL.index(f"function {name}(")
    b = min(i for i in (TPL.find("\nfunction ", a + 1), TPL.find("\n//", a + 1)) if i > 0)
    return TPL[a:b]


@pytest.fixture(scope="module")
def run():
    r = json.loads(json.dumps(fixture.build_realization(), ensure_ascii=False, default=str))
    ct = r["contracts"]
    sep = dict(ct["sep"], rows=[x for x in ct["rows"] if x.get("seg")], dir="sep", seg="16")
    sep["ork"] = dict(sep["ork"], quarter=ct["ork"]["quarter"])     # как renderContracts: квартал — общий
    main = dict(ct, rows=[x for x in ct["rows"] if not x.get("seg")], dir="main")
    inp = {"code": TPL[TPL.index(START):TPL.index(END)], "esc": _line("const _esc="), "ctN": _line("const _ctN="),
           "unpack": _fn("_unpack"), "views": {"main": main, "sep": sep}, "orkMonth": r["ork_month"], "ct": ct}
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
    for view in ("main", "sep"):
        # массив «Месяца» — свой у вида (ключи «m:1.due» есть у обоих видов, числа разные)
        payload = {"ct": r["contracts"]["ork"], "month": r["ork_month"][view]}
        found = out[view]["found"]
        # у направления «Неделя» — карточки сделок (3.6): чисел меньше, чем в основном виде
        assert len([f for f in found if f["sub"] == "week"]) >= (10 if view == "main" else 2), view
        for f in found:
            assert f["items"] is not None, f
            if f["step"] is None and f.get("tag") is None:
                assert f["rows"] == f["items"], f          # список без отбора — все строки числа
            if f["id"].startswith("w:") and f["el"] == "button":
                # кнопка числа «Недели»: строк столько, сколько в числе (с отбором шага или подписи — в отборе)
                assert f["rows"] == int(re.match(r"\d+", f["text"]).group()), f
            elif f["id"][:2] in ("m:", "q:"):
                n = _n_of(payload, f["id"])
                if not n:
                    # R4 (Д-2, Д-10): списки без показателя-владельца — «не доставлено», «не считаем»,
                    # столбцы «Доставки к сроку», хвост P80: длина списка = число на кнопке
                    src = (r["ork_month"][view]["month"] if f["id"][0] == "m"
                           else r["contracts"]["ork"]["quarter"])
                    n = [len((src.get("drill") or {}).get(f["id"][2:], {}).get("k") or [])]
                assert n and n[0] and f["items"] == n[0], f
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
    """«Что сделать сегодня»: отбор, под который пунктов нет, — «Под отбор «…» заказов нет.» и
    «Снять отбор», а не «всё по регламенту»; без отбора — группы и открытая первая."""
    _r, out = run
    q = out["qempty"]
    assert "Под отбор «роль: нет такой роли» заказов нет." in q["role"] and "data-wkreset" in q["role"]
    assert "регламент" not in q["role"]
    assert 'class="wkg on"' in q["all"] and q["all"].count('class="wkg') >= 2


def test_пульс_отрисован_и_ушедший_помечен(run):
    """«Кто сколько ведёт» (одна таблица вместо «Пульса» и «Сверх нормы», В4): ушедший помечен
    «уволен — переназначить», подпись без «(уволен)»; у каждого человека с заказами — число
    списка его заказов (w:pulse/i/keys), строк в списке столько же."""
    r, out = run
    gone = out["pulseGone"]
    assert "Кто сколько ведёт" in gone and "уволен — переназначить (рук. ОРК)" in gone
    body = htmlmod.unescape(gone.split("</thead>")[1])
    assert "(уволен)</th>" not in body and "(уволен) <span" not in body.split("data-od")[0]
    P = r["contracts"]["ork"]["week"]["pulse"]
    assert gone.count('data-od="w:pulse/') == sum(1 for p in P["rows"] if p["n"])
    # строка «не назначен» открывает и «Без менеджера ОРК» в выводе и счётчике (одно множество, раунд 3)
    week = [f for f in out["main"]["found"] if f["sub"] == "week" and f["id"].startswith("w:pulse/")]
    assert len({f["id"] for f in week}) == sum(1 for p in P["rows"] if p["n"])
    assert all(f["items"] == P["rows"][int(f["id"].split("/")[1])]["n"] for f in week)


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
    без отбора «Менеджер ОРК»; за числом без заказов — «За этим числом заказов нет»; у гигиены —
    вид и почему (hw); поставщик — строкой в ячейке заказа и отбором «Поставщик» (R4, 3.7)."""
    import re
    r, out = run
    ex = out["main"]["extra"]
    assert 'data-odf="step:x"' in ex["qall"] and "заказ закрыт" in ex["qall"]
    assert 'data-odsel="mgr"' not in ex["qall"] and 'class="odsupf"' in ex["qall"]
    assert "За этим числом заказов нет." in ex["empty"] and "отбору" not in ex["empty"]
    w = r["contracts"]["ork"]["week"]
    import orkview
    hw = [x["hw"] for x in orkview.unpack(w["orders"], w["strs"]) if x.get("hw") and x.get("hyg")]
    assert hw and htmlmod.escape(hw[0], quote=False) in ex["hyg"].replace("&quot;", '"')
    assert re.search(r'<div class="dep odsup">поставщик: [^<]+</div>', ex["hyg"])
    assert 'data-odsel="mgr"' in ex["hyg"], "«Неделя»: менеджеры ОРК в списке есть"


def test_строка_списка_номер_заказа_и_день_отсчёта(run):
    """Ревизия сведения 29.10.2026. Номер в запасной строке (заказа нет в строках части — закрытый
    заказ пункта «оплата») — подпись из данных (lbl, ork.order_label), как у очереди: «id 79001», а
    не «29» из даты в начале названия и не «#79001»; заказа нет и в таблице сделок — «id <ключ>».
    У строки с посчитанными р.д. — от какого дня они считаны («с 14.08 (вход в шаг)»), как у
    пункта очереди о сроке шага."""
    import orkview
    r, out = run
    fb = out["fallback"]
    for no in ("id 79001", "905/2", "id 79999"):
        assert f'<b class="mono">{no}</b>' in fb, no
    assert '<b class="mono">29</b>' not in fb and "#79" not in fb
    assert "заказа нет в данных вкладки" in fb, "ключ без строки — помечен"
    w = r["contracts"]["ork"]["week"]
    rows = [x for x in orkview.unpack(w["orders"], w["strs"]) if x.get("wd") is not None]
    assert rows and all(x.get("since") for x in rows)
    live = out["main"]["extra"]["live"]
    for x in rows:
        assert f'<div class="dep">{htmlmod.escape(x["since"], quote=True)}</div>' in live, (x["k"], x["since"])


def test_разбор_числа_что_делать_худшие_сверху_битрикс(run):
    """«Разбор числа» (отзыв владельца 29.10.2026: «как понимать, что дальше», «провалиться, где
    зависло»): что считает число — фразой; «Что делать» — группы по следующему действию, сумма
    групп — все строки числа, отбор по группе даёт ровно её строки; строки — сначала худшие
    (просрочка срока клиента по убыванию); поиск по номеру находит заказ; сортировка по номеру;
    у каждой строки — ссылки в Битрикс на заказ и сделку, в выгрузку они не идут (data-xlv="");
    шапка таблицы — data-xlv (кнопки сортировки в файл не попадают); разрезы — под таблицей."""
    import re
    _r, out = run
    w = out["main"]["extra"]["w3"]
    assert w["def"].startswith("Заказы, у которых срок клиента прошёл")
    g = w["groups"]
    assert sum(x["n"] for x in g) == w["n"] == len(w["order"])
    assert g[0]["key"] == "c:late", "первым — самое важное действие (пункт «late»)"
    assert all(x["txt"] and x["role"] for x in g if x["key"].startswith("c:")), "у действия — хозяин"
    assert sorted(w["g0"]) == sorted(x["k"] for x in w["items"] if x["act"] == g[0]["key"])
    it = {x["k"]: x for x in w["items"]}
    late = [it[k]["late"] or 0 for k in w["order"]]
    assert late == sorted(late, reverse=True) and late[0] > 0, "сначала худшие"
    assert w["q"] and w["qrows"] and all(w["q"].lower() in it[k]["no"].lower() for k in w["qrows"])
    nat = lambda s: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)]  # noqa: E731
    nos = [it[k]["no"] or k for k in w["sortNo"]]
    assert nos == sorted(nos, key=nat) and sorted(w["sortNo"]) == sorted(w["order"])
    h = w["html"]
    for x in w["items"]:
        if not x["isD"] and not x["missing"]:
            assert f'crm/type/172/details/{x["k"]}/"' in h, x["k"]
        assert f'crm/deal/details/{x["deal"]}/"' in h, x["deal"]
    links = re.findall(r'<a class="odbx"[^>]*>', h)
    assert links and all('target="_blank"' in a and 'rel="noopener"' in a and 'data-xlv=""' in a for a in links)
    # шапка выгрузки — по столбцу на поле (ревью 30.10.2026): видимые столбцы и скрытые рядом
    for t in ("№ заказа", "Сделка", "Шаг №", "Раб. дн. на шаге", "Норма, раб. дн.", "Срок клиента", "Почему", "Что сделать", "Кто делает", "Поставщик"):
        assert f'data-xlv="{t}"' in h, t
    assert "клик — фильтр" not in h and "Что делать" in h
    # порядок экрана (R4, 3.7): сверка → «Почему» → «Что делать» → шаги → отборы → таблица
    assert h.index('class="odrec"') < h.index('id="odWhyH"') < h.index('id="odActH"') < h.index('id="odStpH"') < h.index('class="odsupf"') < h.index("<table")
    assert "просрочен на " in h
