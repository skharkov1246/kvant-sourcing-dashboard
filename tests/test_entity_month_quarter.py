"""«Реализация», «Месяц», «Квартал», «Как считается» и вид направления: сделка и заказ поставщика —
разные единицы (спецификация «сделка и заказ» 30.10.2026, пакеты TP-7 и TP-8; смешения E17, E44–E56).

Функции шаблона исполняются в node:vm на данных smoke-сборки (tests.fixture) и на придуманных
данных. Каждый инвариант проверяется вторым способом — счётом на Python по тем же строкам, а не
чтением того же числа из вёрстки. Корпус придуман (CLAUDE.md, правило 18): компании «Компания N»,
люди — имена без фамилий, суммы условные.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
import shutil
import subprocess
import sys
import types
from collections import defaultdict
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
const node = () => ({innerHTML: '', value: '', textContent: '', dataset: {}, classList: {toggle() {}},
  addEventListener() {}, removeEventListener() {}, setAttribute() {}, scrollIntoView() {},
  closest() { return node(); }, querySelector() { return null; }, contains() { return false; }});
const byId = {};
const document = {getElementById: id => (byId[id] ||= node()), querySelectorAll: () => []};
const box = {document, window: {__ORKMONTH__: inp.orkMonth, __DEALCTX__: null, scrollTo() {}},
  location: {hash: ''}, console, JSON, Math, Object, Array, String, Number, Set, Map, RegExp, Date,
  qmark: () => '', openContract() {}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + inp.unpack + '\nfunction _dcData(){return null;}\n' + inp.code
  + '\nthis.F={_orkWeek,_orkMonth,_orkHow,_orkQuarter,_ctmDueDeals,_ctmCycleDeal,_ctmViolSplit,_ctmDealsOf,_orkJRow};', box);
const out = {views: {}, calls: []};
for (const [k, X] of Object.entries(inp.views || {})) {
  const el = node();
  box.F._orkWeek(X, X.ork, el, '');
  out.views[k] = {week: el.innerHTML, month: box.F._orkMonth(X, X.ork), how: box.F._orkHow(X, X.ork),
                  quarter: box.F._orkQuarter(X, X.ork)};
  for (const id of Object.keys(byId)) delete byId[id];
}
for (const [fn, args] of (inp.calls || [])) {
  const r = box.F[fn](...args);
  out.calls.push(r instanceof Set ? [...r] : r);
}
process.stdout.write(JSON.stringify(out));
"""


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


def _fn(name: str) -> str:
    a = TPL.index(f"function {name}(")
    b = min(i for i in (TPL.find("\nfunction ", a + 1), TPL.find("\n//", a + 1)) if i > 0)
    return TPL[a:b]


def _run(views: dict | None = None, ork_month=None, calls: list | None = None) -> dict:
    inp = {"code": TPL[TPL.index(START):TPL.index(END)], "esc": _line("const _esc="), "ctN": _line("const _ctN="),
           "unpack": _fn("_unpack"), "views": views or {}, "orkMonth": ork_month, "calls": calls or []}
    r = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False, default=str),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def _plain(html: str) -> str:
    """Текст страницы без разметки: кнопки чисел снимаются, число остаётся."""
    t = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", t)


def _views(ct: dict) -> dict:
    """Виды, как их собирает шаблон (_ctRender): основное — строки без направления, направление «Залив» —
    свои строки, свой свод очереди и недели."""
    main = dict(ct, rows=[r for r in ct["rows"] if not r.get("seg")], seg=None, dir="main")
    sep = dict(ct, **ct["sep"])
    sep.update(rows=[r for r in ct["rows"] if r.get("seg")], seg="sep", dir="sep", orphan=None,
               sepHead={"name": "Залив", "cat": "sep"})
    return {"main": main, "sep": sep}


@pytest.fixture(scope="module")
def built():
    r = fixture.build_realization()
    return json.loads(json.dumps(r, ensure_ascii=False, default=str))


@pytest.fixture(scope="module")
def drawn(built):
    return _run(_views(copy.deepcopy(built["contracts"])), built["ork_month"])


# ---- второй способ: сделки со сроком клиента по месяцу срока последнего заказа, на Python ----

def _d(s: str):
    s = str(s or "")
    for f in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s[:10], f).date()
        except ValueError:
            pass
    return None


def _due_deals_py(rows: list[dict], as_of: str) -> dict[str, tuple[int, int]]:
    today, out = _d(as_of), defaultdict(lambda: [0, 0])
    for r in rows:
        if r.get("lost"):
            continue
        os_ = [o for o in r.get("orders") or [] if _d(o.get("dl"))]
        if not os_:
            continue
        last = max(_d(o["dl"]) for o in os_)
        if last >= today:
            continue
        late = open_ = skip = 0
        for o in os_:
            f = _d(o.get("delivered"))
            if f:
                late += f > _d(o["dl"])
            elif o.get("live") and not r.get("closed"):
                open_ += 1
            else:
                skip += 1
        if skip:
            continue
        b = out[f"{last:%m.%Y}"]
        b[0] += 1
        b[1] += not late and not open_
    return {k: tuple(v) for k, v in out.items()}


def test_месяц_главное_число_сделки_заказы_рядом(built, drawn):
    """E44: заголовок и первая плитка «Месяца» — сделки к сроку (сделка — в месяце срока последнего
    заказа, к сроку — все её заказы), заказы — рядом своим числом; разные единицы не складываются."""
    ct, om = built["contracts"], built["ork_month"]
    rows = [r for r in ct["rows"] if not r.get("seg")]
    M = om["main"]["month"]
    m0 = M["rows"][M["head"]]
    n, k = _due_deals_py(rows, ct["life"]["asOf"])[m0["m"]]
    OD = m0["otdDue"]
    html = drawn["views"]["main"]["month"]
    hd = re.search(r'<h2 class="ctm-hd" id="ctmMonH">(.*?)</h2>', html, re.S).group(1)
    assert f"сделок к сроку {k} из {n}" in hd and f"заказов — {OD['k']} из {OD['n']}" in hd
    # сумма разных единиц нигде в заголовке не стоит
    assert str(n + OD["n"]) not in re.findall(r"\d+", hd)
    t = _plain(html)
    assert "Сделки доставлены к сроку клиента" in t
    assert f"{k} из {n} {'сделки' if n % 10 == 1 and n % 100 != 11 else 'сделок'}" in t
    # число сделок — кнопка списка сделок (реестр data-ctk), число заказов — список заказов (data-od)
    kpi = html[html.index('Сделки доставлены к сроку клиента'):]
    assert 'data-ctk="main:dd.' + m0["m"] + '"' in html and "список сделок" in kpi[:4000]
    assert f'data-od="{OD["dk"]}"' in html
    # таблица по месяцам — сделки и заказы отдельными столбцами, у столбцов — их совокупность (E53)
    for th in ("Сделки к сроку", "Заказы к сроку", "все сделки вида", "все заказы вида", "заказы с 24.08"):
        assert th in html, th


def test_сделки_месяца_из_данных_важнее_вывода(built):
    """Поле lifecycle (life.otd.months[].dueDeals, пакет PY-6) пришло — шаблон берёт его, а не свой
    вывод по строкам: одно число — один источник."""
    ct = copy.deepcopy(built["contracts"])
    m0 = built["ork_month"]["main"]["month"]
    mk = m0["rows"][m0["head"]]["m"]
    for m in ct["life"]["otd"]["months"]:
        m["dueDeals"] = {"n": 7, "ontime": 3} if m["m"] == mk else {"n": 0, "ontime": 0}
    html = _run({"main": _views(ct)["main"]}, built["ork_month"])["views"]["main"]["month"]
    assert "сделок к сроку 3 из 7" in html
    # ключей у поля нет — число без кнопки списка, а не кнопка с чужим списком
    assert 'data-ctk="main:dd.' + mk + '"' not in html


def test_сделка_к_сроку_правило_на_придуманных_строках():
    """Правило вывода сделок месяца (запасной путь до поля PY-6) на придуманных строках: сделка — в
    месяце срока ПОСЛЕДНЕГО заказа; к сроку — все заказы доставлены не позже своего срока;
    живой недоставленный — не к сроку; заказ закрыт без доставки — сделку не судим; срок впереди и
    проигранная сделка — не в счёте. Второй способ — _due_deals_py."""
    o = lambda i, dl, f="", live=True: {"id": i, "dl": dl, "delivered": f, "live": live}  # noqa: E731
    rows = [
        {"deal": "1", "orders": [o("11", "05.09.2026", "01.09.2026"), o("12", "20.09.2026", "20.09.2026")]},
        {"deal": "2", "orders": [o("21", "10.09.2026", "15.09.2026")]},                         # позже срока
        {"deal": "3", "orders": [o("31", "02.08.2026", "01.08.2026"), o("32", "25.09.2026")]},  # открыт, сентябрь
        {"deal": "4", "orders": [o("41", "10.09.2026", "09.09.2026"), o("42", "10.11.2026")]},  # срок впереди
        {"deal": "5", "orders": [o("51", "12.09.2026", "", live=False)]},                       # закрыт без доставки
        {"deal": "6", "lost": True, "orders": [o("61", "12.09.2026", "11.09.2026")]},
    ]
    X = {"rows": rows, "life": {"asOf": "19.10.2026", "otd": {"months": [{"m": "08.2026"}, {"m": "09.2026"}, {"m": "10.2026"}]}}}
    got = _run(calls=[["_ctmDueDeals", [X]]])["calls"][0]
    py = _due_deals_py(rows, "19.10.2026")
    assert py == {"09.2026": (3, 1)}
    assert {m: (v["n"], v["ontime"]) for m, v in got.items()} == py
    assert got["09.2026"]["keys"]["k"] == ["d:1", "d:2", "d:3"]
    assert got["09.2026"]["keys"]["t"][0].startswith("к сроку") and got["09.2026"]["keys"]["t"][2].startswith("не к сроку")


def test_приёмка_и_файлы_в_своей_единице(built):
    """E17, E45: приёмка (S1) подписана своей единицей — сборка по заказам так и названа, сборка по
    сделкам (решение владельца 30.10.2026, п. 1) — «приёмка сделки … из N сделок»; M2 — файлы и у
    скольких заказов, а не «карточки заполнены»."""
    ct = copy.deepcopy(built["contracts"])
    om = copy.deepcopy(built["ork_month"])
    M = om["main"]["month"]
    hi = M["head"]
    s1 = M["rows"][hi]["M1"]["S1"]
    t0 = _plain(_run({"main": _views(ct)["main"]}, om)["views"]["main"]["month"])
    assert "Приёмка за 1 рабочий день — по заказам" in t0 and f"{s1['k']} из {s1['n']} заказ" in t0
    s1.update(unit="deal", n=3, k=1, value=33)
    M["rows"][hi]["M2"] = {"state": "prelim", "value": 57, "have": 16, "n": 28, "ordersN": 4, "target": 95}
    t1 = _plain(_run({"main": _views(ct)["main"]}, om)["views"]["main"]["month"])
    assert "Приёмка сделки за 1 рабочий день" in t1 and "1 из 3 сделок" in t1
    assert "Обязательные файлы на месте" in t1 and "16 из 28 файлов" in t1 and "у 4 заказов" in t1
    assert "Карточки заказов заполнены" not in t1


def test_что_исправить_счёт_разных_объектов(built, drawn):
    """E17: «Что исправить» считает разные объекты своей единицы: приёмка — сделки (заказы в скобках),
    шаги заказа — заказы; «N раз» (строки журнала) больше не счёт."""
    om = built["ork_month"]["main"]
    m0 = om["month"]["rows"][om["month"]["head"]]["m"]
    rows = [r for r in om["journal"]["rows"] if r["seg"] == "S1" and r["m"] == m0 and r["measurable"] and (r["over"] or 0) > 0]
    nd, no = len({r["deal"] for r in rows}), len({r["order"] for r in rows})
    assert nd < no      # фикстура: у сделки 933 несколько заказов — прежний счёт «раз» завышал приёмку
    t = _plain(drawn["views"]["main"]["month"])
    assert f"приёмка дольше нормы у {nd} сделок ({no} заказа)" in t or f"приёмка дольше нормы у {nd} сделки ({no} заказа)" in t
    assert "раза дольше нормы" not in t and "раз дольше нормы" not in t


def test_журнал_первая_колонка_заказ(drawn):
    """E52: строка журнала — шаг заказа: первым столбцом «Заказ», сделка — вторым (строки — в
    test_ork_template: data-jrow — путь заказа, data-jct — карточка сделки)."""
    html = drawn["views"]["main"]["month"]
    head = re.search(r'<table class="lft orkj[^>]*>.*?</thead>', html, re.S).group(0)
    assert head.index('scope="col">Заказ</th>') < head.index('scope="col">Сделка</th>')
    assert "Строка и номер заказа открывают путь заказа по шагам, номер сделки — карточку сделки" in html


# ---- «Квартал» ----

def test_обещайте_клиенту_по_сделке(built, drawn):
    """E47: «Обещайте клиенту» — по сделке (вход в «Реализацию» → доставка последнего заказа), один
    заказ — рядом. Сделок, доставленных целиком, меньше порога ячейки — числа по сделке нет, а
    прежнее число заказа (P80 цикла) стоит рядом и не меняется."""
    cyc = built["contracts"]["ork"]["quarter"]["Q2"]["cycle"]
    t = _plain(drawn["views"]["main"]["quarter"])
    assert f"один заказ: от создания до доставки — 8 из 10 укладываются в {round(cyc['p80'])} дн." in t
    assert "Сколько обещать по сделке — пока не посчитать" in t and "нужно от 8" in t
    ct = copy.deepcopy(built["contracts"])
    ct["ork"]["quarter"]["Q2"]["cycleDeal"] = {"state": "prelim", "value": 95, "n": 12, "med": 80, "p80": 95}
    t2 = _plain(_run({"main": _views(ct)["main"]}, built["ork_month"])["views"]["main"]["quarter"])
    assert "Обещайте клиенту не меньше 95 дн. от входа сделки в «Реализацию» до доставки последнего заказа — так укладываются 8 из 10 сделок" in t2
    assert "Вся сделка: от входа в «Реализацию» до доставки последнего заказа" in t2 and "12 сделок" in t2


def test_цикл_сделки_правило_на_придуманных_строках():
    """Вывод цикла сделки (запасной путь до поля PY-6): только сделки, доставленные целиком за окно;
    P80 = s[⌈0,8n⌉−1] — как у заказов. Второй способ — тот же счёт на Python."""
    rows, days = [], [30, 45, 60, 61, 90, 120, 150, 200, 210, 400]
    base = dt.date(2026, 10, 1)
    for i, d in enumerate(days):
        last = base - dt.timedelta(days=i)
        c0 = last - dt.timedelta(days=d)
        rows.append({"deal": str(100 + i), "cat0At": c0.isoformat() + "T12:00:00+03:00",
                     "orders": [{"id": f"{i}1", "delivered": last.strftime("%d.%m.%Y")},
                                {"id": f"{i}2", "delivered": (last - dt.timedelta(days=5)).strftime("%d.%m.%Y")}]})
    rows.append({"deal": "900", "cat0At": "2026-01-01", "orders": [{"id": "91", "delivered": "01.09.2026"}, {"id": "92", "delivered": ""}]})
    rows.append({"deal": "901", "cat0At": "2024-01-01", "orders": [{"id": "93", "delivered": "01.09.2024"}]})   # вне окна
    X = {"rows": rows, "life": {"asOf": "19.10.2026"}}
    got = _run(calls=[["_ctmCycleDeal", [X, 365, 30]]])["calls"][0]
    s = sorted(days)
    assert got["n"] == len(days) and got["p80"] == s[math.ceil(0.8 * len(s)) - 1] == 200
    assert got["state"] == "prelim" and len(got["keys"]["k"]) == got["n"] and "d:900" not in got["keys"]["k"]


def test_поставщики_по_воротам(built):
    """E51: в счёт «не держат срок» — только поставщики с готовыми заказами от порога квартала; меньше —
    «мало готовых заказов: N поставщиков», без вывода."""
    ct = copy.deepcopy(built["contracts"])
    q = ct["ork"]["quarter"]["quarters"][1]
    q["suppliers"] = [
        {"sup": "Компания 900", "state": "show", "value": 40, "n": 6, "k": 2},
        {"sup": "Компания 901", "state": "show", "value": 95, "n": 7, "k": 7},
        {"sup": "Компания 902", "state": "prelim", "value": 0, "n": 2, "k": 0},
        {"sup": "Компания 903", "state": "prelim", "value": 0, "n": 3, "k": 0},
    ]
    ct["ork"]["quarter"]["quarters"][0]["suppliers"] = []
    t = _plain(_run({"main": _views(ct)["main"]}, built["ork_month"])["views"]["main"]["quarter"])
    assert "Не держат плановый срок производства: 1 поставщик · мало готовых заказов: 2 поставщика" in t
    # у фикстуры все поставщики ниже порога — прежний заголовок «7 поставщиков» был выводом из 1–3 заказов
    t0 = _plain(_run({"main": _views(copy.deepcopy(built["contracts"]))["main"]}, built["ork_month"])["views"]["main"]["quarter"])
    assert "Держат ли поставщики плановый срок производства — судить рано" in t0 and "Не держат" not in t0


def test_гарантия_заказы_и_сделки(built, drawn):
    """E54: гарантия — заказы, сделки — в скобках, счёт разных сделок за заказами на гарантии."""
    ct = built["contracts"]
    ks = ct["life"]["warranty"]["keys"]["k"]
    by = {o["id"]: r["deal"] for r in ct["rows"] if not r.get("seg") for o in r["orders"]}
    nd = len({by[k] for k in ks})
    t = _plain(drawn["views"]["main"]["quarter"])
    w = "сделка" if nd % 10 == 1 and nd % 100 != 11 else "сделки" if nd % 10 in (2, 3, 4) and nd % 100 not in (12, 13, 14) else "сделок"
    assert f"Гарантия: {len(ks)} заказов ({nd} {w}) на гарантии" in t


def test_одна_подпись_q1(drawn):
    """E50: конец производства Q1 — одной подписью на «Квартале» и в «Как считается»."""
    q, h = _plain(drawn["views"]["main"]["quarter"]), _plain(drawn["views"]["main"]["how"])
    lab = "планового конца производства в заказе поставщику (бюджет)"
    assert lab in q and lab in h
    for old in ("названного поставщиком", "по бюджету сделки", "Готово к обещанному сроку"):
        assert old not in q and old not in h, old


# ---- «Как считается» ----

def test_чей_шаг_по_регламенту(drawn):
    """E48: у 13 шагов колонка «Чей шаг»: 1–3 — сделка, 4–12 — заказ, 13 — сделка и заказы; «где меряем»
    — отдельно (шаг 1 сборки по заказам — «по заказу»)."""
    html = drawn["views"]["main"]["how"]
    body = re.search(r'<table class="lft ctm-t ctm-steps[^"]*">.*?</table>', html, re.S).group(0)
    assert "Чей шаг" in body
    rows = re.findall(r'<tr><td class="r ctm-no">(\d+)</td><th[^>]*>.*?</th><td data-xlv="([^"]*)"', body, re.S)
    own = {int(n): v.split(" · ")[0] for n, v in rows}
    assert len(own) == 13
    assert all(own[n] == "сделка" for n in (1, 2, 3)) and all(own[n] == "заказ" for n in range(4, 13))
    assert own[13] == "сделка и заказы"
    assert "по заказу: «Заказ создан» до назначения менеджера ОРК" in body


def test_нарушения_два_итога(built, drawn):
    """E46: «Стадии двигали неправильно» — два итога: разные заказы и разные сделки (по ключам списков),
    а не сумма 38 «случаев» из заказов и сделок."""
    H = built["contracts"]["ork"]["how"]
    V, D = H["violations"], H["drill"]
    codes = ("returns", "bursts", "negative", "payNoContract", "dealBehindOrders", "behindFacts", "unknownStage")
    keys = [k for c in codes for k in D[c]["k"]]
    orders, deals = {k for k in keys if not k.startswith("d:")}, {k for k in keys if k.startswith("d:")}
    s_ord = sum(1 for k in keys if not k.startswith("d:"))
    assert sum(V[c] for c in codes) == len(keys) == 38     # прежний итог
    html = drawn["views"]["main"]["how"]
    hd = re.search(r'id="ctmHowViol">(.*?)</h2>', html, re.S).group(1)
    assert f"у {len(orders)} заказов (нарушений {s_ord})" in hd and f"у {len(deals)} сделок" in hd
    assert "38" not in hd
    # столбец «Сколько» — с единицей
    t = _plain(html)
    assert f"Стадия сделки отстала от заказов сделка {V['dealBehindOrders']} сделок" in t
    assert f"Даты стадий в обратном порядке заказ {V['negative']} заказов" in t


def test_в_расчёте_с_единицей(built, drawn):
    """E49: «В расчёте» — с единицей словами; W1 сборки старше — заказы и сделки двумя числами, не суммой."""
    W = built["contracts"]["ork"]["week"]
    n1, d1 = W["tiles"]["W1"]["n"], W["dealsNoOrder"]
    t = _plain(drawn["views"]["main"]["how"])
    o1 = n1 - d1
    assert f"{o1} заказ" in t and f"+ {d1} сделк" in t
    met = {m["code"]: m for m in built["contracts"]["ork"]["how"]["metrics"]}
    assert f"{met['W2']['n']} заказов" in t and f"{met['W3']['n']} заказ" in t


def test_viol_split_из_данных_важнее_ключей():
    """how.viol {orders, deals} (PY-6) пришло — счёт по единицам берётся из него."""
    V = {"returns": 2, "payNoContract": 3}
    VV = {"orders": {"returns": 2, "payNoContract": 1}, "deals": {"payNoContract": 2}}
    got = _run(calls=[["_ctmViolSplit", [V, {}, ["returns", "payNoContract"], VV]]])["calls"][0]
    assert got["rows"]["payNoContract"] == {"order": 1, "deal": 2}
    assert got["tot"]["order"]["sum"] == 3 and got["tot"]["deal"]["sum"] == 2


# ---- вид направления («Залив») ----

def test_залив_сорван_заказы_и_сделки(built, drawn):
    """E56, E20: вывод направления — «сорван: N заказов (M сделок)», «клиент не оплатил» — сделками."""
    t = _plain(drawn["views"]["sep"]["week"])
    q = built["contracts"]["sep"]["life"]["queue"]
    late = [x for x in q if x["code"] == "late"]
    assert f"срок клиента сорван: {len(late)} заказ ({len({x['deal'] for x in late})} сделка)" in t
    ct = copy.deepcopy(built["contracts"])
    ct["sep"]["ork"]["week"]["tiles"]["unpaid"] = {"orders": 3, "deals": 2}
    t2 = _plain(_run({"sep": _views(ct)["sep"]}, built["ork_month"])["views"]["sep"]["week"])
    assert "клиент не оплатил в срок: 2 сделки (3 заказа)" in t2 and "оплата клиента" not in t2.split("Без отклонений:")[-1]


def test_залив_пункт_сделки_на_карточке_сделки(built):
    """E55: пункт сделки (unit = deal) — строкой «по сделке» на карточке своей сделки, до заказов, и не
    попадает в строку заказа."""
    ct = copy.deepcopy(built["contracts"])
    sep_rows = [r for r in ct["rows"] if r.get("seg")]
    d = sep_rows[0]
    ct["sep"]["life"]["queue"].append({"code": "payNoContract", "unit": "deal", "bucket": "deal", "deal": d["deal"],
                                       "seq": d["seq"], "order": "", "act": "Подписать договор с клиентом",
                                       "who": "Анна (КАМ)", "role": "kam", "roleName": "КАМ", "sev": 5, "days": 3})
    html = _run({"sep": _views(ct)["sep"]}, built["ork_month"])["views"]["sep"]["week"]
    card = html[html.index(f'data-ct="{d["deal"]}"'):]
    card = card[:card.index("</article>")]
    assert "по сделке:" in card and "Подписать договор с клиентом" in card
    assert card.index("по сделке:") < card.index('class="wkdos"')
    other = html.replace(card, "")
    assert "Подписать договор с клиентом" not in other


def test_залив_срок_сделки_по_недоставленным(built):
    """§1.6 п. 3: срок сделки на карточке направления — ближайший срок НЕДОСТАВЛЕННОГО заказа с номером;
    прошедший срок доставленного заказа сроком сделки не показывается."""
    ct = copy.deepcopy(built["contracts"])
    d = [r for r in ct["rows"] if r.get("seg") and len(r["orders"]) > 1][0]
    first, second = sorted(d["orders"], key=lambda o: _d(o["dl"]))[:2]
    first["delivered"] = first["dl"]
    html = _run({"sep": _views(ct)["sep"]}, built["ork_month"])["views"]["sep"]["week"]
    t = _plain(html)
    assert f"ближайший срок заказа {second['dl'][:5]} · {second['lbl']} (из 2 заказов, доставлено 1, к сроку 1)" in t
