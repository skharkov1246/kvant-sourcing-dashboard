"""«Реализация», сделка и заказ поставщика (спецификация 30.10.2026, пакет T2 = TP-3 + TP-4): «Неделя»
(плитки, счётчики, ведомость, «Кого ждём», «Кто сколько ведёт») и «Разбор числа» говорят единицами.

Инварианты пакета (спец. §1.8, §3.3, §3.4, §4.3):
- W1 — сделки (с заказом на приёмке или без), n — сделки на шаге 1; «Клиент не оплатил» — сделки,
  заказы в скобках; W5 — «N звонков по M заказам»;
- ведомость в двух частях: шаги сделки (1–3, 13) — сделки, шаги заказа (4–12) — заказы; итоги не
  складывают единицы; поля расчёта (ledger[].unit, W1.unit, adjacent{orders,deals}) берутся как есть;
- «Кого ждём» — двумя числами; «Без менеджера ОРК» — одно значение во всех местах «Недели»;
- «Разбор числа»: единица из D[key].unit, заголовок — разные объекты, список сделок — строка на сделку,
  отрезки и файлы — вложенными строками под заказом, причина пункта сделки — от его хозяина.
Корпус придуман (CLAUDE.md, правило 18): номера, люди и компании — заглушки.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нет node")

TPL = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
START, END = "// ---- «Реализация» по регламенту ОРК", "function _ctPanel("


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const node = () => ({innerHTML: '', value: '', textContent: '', dataset: {}, classList: {toggle() {}, add() {}, remove() {}, contains() { return false; }},
  addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {}, getAttribute() { return null; }, scrollIntoView() {},
  closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; }, contains() { return false; }});
const byId = {};
const document = {getElementById: id => (byId[id] ||= node()), querySelectorAll: () => [], querySelector: () => null, addEventListener() {}};
const box = {document, window: {__CONTRACTS__: null, __DEALCTX__: null, scrollTo() {}, addEventListener() {}},
  location: {hash: ''}, history: {pushState() {}, replaceState() {}}, console, JSON, Math, Object, Array, String, Number,
  Set, Map, RegExp, Date, setTimeout: () => 0, clearTimeout() {}, qmark: () => '', openContract() {}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\nfunction _dcData(){return null;}\n' + inp.code
  + '\nthis.F={_orkWeekCtx,_orkWeekHead,_orkWeekLedger,_orkWeekWait,_orkWeekPeople,_wkU,_odModel,_odHtml,_odTitle,_odSub,_odPrep,_odCnt,_odUnit,_odCause};', box);
const F = box.F, out = {};
const plain = h => String(h).replace(/<button type="button" class="odn[^"]*"[^>]*>([\s\S]*?)<\/button>/g, '$1').replace(/<[^>]+>/g, ' ')
  .replace(/&nbsp;| /g, ' ').replace(/&[a-z]+;/g, ' ').replace(/\s+/g, ' ');
const buttons = h => [...String(h).matchAll(/data-od="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g)].map(m => [m[1], m[2].replace(/<[^>]+>/g, '').trim()]);
const list = (X, spec, Fl) => { const M = F._odModel(X, X.ork, spec); if (!M) return null; F._odPrep(M);
  const h = F._odHtml(M, Object.assign({step: null, sup: '', mgr: '', tag: ''}, Fl || {}));
  return {title: F._odTitle(M).txt, sub: F._odSub(M), cnt: F._odCnt(M), unit: F._odUnit(M), html: h,
          rows: (h.match(/<tr class="drill[^"]*" data-odk=/g) || []).length, pairs: (h.match(/<li class="odpi/g) || []).length,
          keys: [...h.matchAll(/<tr class="drill[^"]*" data-odk="([^"]*)"/g)].map(m => m[1]),
          xl: (/data-xl-name="([^"]*)"/.exec(h) || [])[1] || '', items: M.items.map(x => ({k: x.k, cause: x.cause}))}; };
for (const [name, X] of Object.entries(inp.views)) {
  box.window.__CONTRACTS__ = X;
  const C = F._orkWeekCtx(X, X.ork), U = F._wkU(C);
  const head = F._orkWeekHead(C), led = F._orkWeekLedger(C), wait = F._orkWeekWait(C), ppl = F._orkWeekPeople(C);
  const o = {head: plain(head), headRaw: head, led: plain(led), wait: plain(wait), ppl: plain(ppl), noMgr: C.noMgr,
    btn: {head: buttons(head), led: buttons(led), wait: buttons(wait), ppl: buttons(ppl)},
    U: {w1: U.w1, unp: U.unp, w5: U.w5, dstep: U.dstep, adj: U.adj}, lists: {}};
  for (const id of new Set([].concat(...Object.values(o.btn).map(b => b.map(x => x[0]))))) o.lists[id] = list(X, {id});
  out[name] = o;
}
// «Разбор числа» на списках с единицей из данных (договор 4.3: D[key] = {unit, keys})
const X = inp.views.base;
box.window.__CONTRACTS__ = X;
out.seg = list(X, {keys: {k: ['9002', '9002', '9006'], t: ['дольше нормы · договор · 5 раб. дн.', 'в норме · договор · 1 раб. дн.', 'дольше нормы · заказ и оплата · 12 раб. дн.'], unit: 'segment'}, pre: 'm', title: 'Отрезки шага'});
out.file = list(X, {keys: {k: ['9006', '9006', '9007'], t: ['нет: PO', 'есть: OC', 'нет: Packing list'], unit: 'file'}, pre: 'w', title: 'Обязательные файлы'});
out.dataUnit = list(X, {id: 'w:dk'});
out.close = list(X, {id: 'w:step/13/close'});
out.idle = list(X, {keys: {k: ['d:5004'], t: null, unit: 'deal'}, pre: 'w', title: 'Сделки шага 3'});
process.stdout.write(JSON.stringify(out));
"""


def _ledger(now: dict, over: dict) -> list:
    roles = {1: "orkHead", 2: "kam", 3: "kam", 10: "kam", 11: "log", 13: "oss"}
    waits = {2: "kam", 3: "kam", 6: "oss", 7: "sup", 9: "log", 10: "kam", 11: "log", 13: "oss"}
    norm = {1: 1, 4: 2, 5: 9, 8: 2, 12: 10}
    return [{"no": n, "name": f"Шаг {n}", "role": roles.get(n, "orkMgr"), "roleName": "менеджер ОРК", "waits": waits.get(n, ""),
             "now": now.get(n, 0), "over": over.get(n, 0) if n in norm else None, "norm": norm.get(n),
             "normState": "norm" if n in norm else "none", "normDoc": f"{norm[n]} р.д." if n in norm else ""}
            for n in range(1, 14)]


def _order(i, lbl, step, **kw):
    return dict({"id": str(i), "lbl": lbl, "step": step, "live": True, "dl": "01.12.2026", "delivered": ""}, **kw)


def _base() -> dict:
    """Шесть сделок, девять заказов: у сделки 501 заказ на приёмке и заказ на шаге 4; сделка 502 без
    заказа (на приёмке дольше нормы); 503 — два неоплаченных доставленных заказа (один долг — одна
    сделка); 504 — договор с заказчиком подписывают (шаг 3), заказ не оплачен; 505 — к закрытию;
    506 — заказы на шагах 5 и 9 и две контрольные точки у одного заказа."""
    rows = [
        {"deal": "5001", "seq": 501, "customer": "Заказчик А", "dealStep": 6, "inCat0": True, "saleEur": 40000, "stageName": "Производство",
         "orders": [_order(9001, "501/1", 1), _order(9002, "501/2", 4)]},
        {"deal": "5002", "seq": 502, "customer": "Заказчик Б", "dealStep": 2, "inCat0": True, "orders": []},
        {"deal": "5003", "seq": 503, "customer": "Заказчик В", "dealStep": 2, "inCat0": True, "saleEur": 90000,
         "orders": [_order(9003, "503/1", 12, delivered="01.09.2026", dl="05.09.2026"), _order(9008, "503/2", 12, delivered="02.09.2026", dl="01.09.2026"),
                    _order(9009, "503/3", 9, dl="06.12.2026")]},
        {"deal": "5004", "seq": 504, "customer": "Заказчик Г", "dealStep": 3, "inCat0": True, "orders": [_order(9004, "504/1", 12, delivered="10.09.2026")]},
        {"deal": "5005", "seq": 505, "customer": "Заказчик Д", "dealStep": 13, "inCat0": True, "closed": True,
         "orders": [_order(9005, "505/1", None, live=False, delivered="01.08.2026")]},
        {"deal": "5006", "seq": 506, "customer": "Заказчик Е", "dealStep": 5, "inCat0": True,
         "orders": [_order(9006, "506/1", 5), _order(9007, "506/2", 9)]},
    ]
    q = lambda o, d, code, role, who, **kw: dict({"order": o, "deal": d, "code": code, "role": role, "who": who, "sev": 5, "days": 10,
                                                 "act": f"сделать: {code}", "roleName": role, "seq": int(d) - 4500, "customer": "—"}, **kw)
    queue = [q("9001", "5001", "s1Over", "orkHead", "Рук (рук. ОРК)"), q("", "5002", "noOrder", "orkHead", "Рук (рук. ОРК)"),
             q("9003", "5003", "unpaid", "kam", "Кам 1 (КАМ)"), q("9008", "5003", "unpaid", "kam", "Кам 1 (КАМ)"),
             q("9004", "5004", "unpaid", "kam", "не назначен"), q("", "5005", "dealClose", "oss", "Осс 1 (ОСС)"),
             q("9006", "5006", "s3Over", "orkMgr", "Менеджер 1 (ОРК)")]
    orders = [
        {"k": "9001", "no": "501/1", "deal": "5001", "seq": 501, "step": 1, "mgr": "не назначен", "over": True, "q": ["s1Over"]},
        {"k": "9002", "no": "501/2", "deal": "5001", "seq": 501, "step": 4, "mgr": "Менеджер 1 (ОРК)"},
        {"k": "9003", "no": "503/1", "deal": "5003", "seq": 503, "step": 12, "mgr": "не назначен", "q": ["unpaid", "late"]},
        {"k": "9008", "no": "503/2", "deal": "5003", "seq": 503, "step": 12, "mgr": "ответственный вне ОРК", "q": ["unpaid"]},
        {"k": "9009", "no": "503/3", "deal": "5003", "seq": 503, "step": 9, "mgr": "ответственный вне ОРК", "q": ["late"]},
        {"k": "9004", "no": "504/1", "deal": "5004", "seq": 504, "step": 12, "mgr": "Менеджер 1 (ОРК)", "q": ["unpaid"]},
        {"k": "9006", "no": "506/1", "deal": "5006", "seq": 506, "step": 5, "mgr": "Менеджер 1 (ОРК)", "over": True, "q": ["s3Over"]},
        {"k": "9007", "no": "506/2", "deal": "5006", "seq": 506, "step": 9, "mgr": "Менеджер 1 (ОРК)"},
    ]
    deals = [{"k": "d:5002", "deal": "5002", "seq": 502, "kind": "noOrder", "step": 1, "over": True, "ownR": "orkHead", "q": ["noOrder"]},
             {"k": "d:5005", "deal": "5005", "seq": 505, "kind": "dealClose", "step": 13, "ownR": "oss", "q": ["dealClose"]}]
    now = {1: 1, 4: 1, 5: 1, 9: 2, 12: 3}
    week = {
        "mon": "2026-10-26", "sun": "2026-11-01", "live": 8, "orders": orders, "deals": deals, "hist": [], "strs": [],
        "ledger": _ledger(now, {1: 1, 5: 1}), "adjacent": {"kam": 0, "oss": 0, "log": 2, "sup": 0},
        "tiles": {"W1": {"state": "show", "value": 2, "n": 3}, "W2": {"state": "show", "value": 2, "n": 8, "byStep": {"1": 1, "5": 1}},
                  "W3": {"state": "show", "value": 2, "n": 8, "byOrders": {"late": 2, "risk": 0, "forecast": 0}, "byDeals": {"late": 1}},
                  "W5": {"state": "show", "value": 3, "n": 2}, "unpaid": {"orders": 3, "deals": 2, "oldest": "2026-09-01"}, "late": {}},
        "drill": {"W1": ["9001", "d:5002"], "W2": ["9001", "9006"], "W5": {"k": ["9006", "9007", "9006"], "t": ["P50", "P14", "P14"]},
                  "unpaid": {"k": ["9003", "9008", "9004"], "t": ["оплата до 05.09", "оплата до 01.09", "оплата до 10.09"]},
                  "W3p": {"late": {"k": ["9003", "9009"], "t": ["late", "late"]}},
                  "step": {"1": {"now": ["9001"], "over": ["9001"]}, "4": {"now": ["9002"], "over": []}, "5": {"now": ["9006"], "over": ["9006"]},
                           "9": {"now": ["9007", "9009"]}, "12": {"now": ["9003", "9004", "9008"], "over": []}, "13": {"now": [], "close": ["d:5005"]}},
                  "noOrder": ["d:5002"], "adj": {"log": ["9007", "9009"]}, "who": {"Менеджер 1 (ОРК)": {"over": ["9006"], "wait": []}}, "mapped": ["9001", "9002", "9006", "9007", "9009", "9003", "9004", "9008"],
                  "live": ["9001", "9002", "9006", "9007", "9009", "9003", "9004", "9008"],
                  "dk": {"unit": "deal", "keys": {"k": ["9003", "9008"], "t": ["", ""]}}},
        "pulse": {"rows": [{"who": "Менеджер 1 (ОРК)", "kind": "mgr", "n": 4, "deals": 3, "customers": 3, "over": 1, "wait": 0},
                           {"who": "ответственный вне ОРК", "kind": "out", "n": 2, "deals": 1, "customers": 1, "over": 0, "wait": 0},
                           {"who": "не назначен", "kind": "none", "n": 2, "deals": 2, "customers": 2, "over": 1, "wait": 0}], "live": 8},
        # исполнитель шага: «не назначен» склеивал заказы без менеджера и заказы с ответственным не из ОРК
        "overByWho": [{"who": "Менеджер 1 (ОРК)", "n": 1, "waitUtd": 0}, {"who": "не назначен", "n": 1, "waitUtd": 0}],
        "lateByWho": {"не назначен": 2}, "waitUtd": {"n": 0}, "checkpoints": [], "hygiene": {"n": 0, "by": {}},
    }
    life = {"queue": queue, "labels": {"unpaid": "клиент не оплатил", "dealClose": "оплата получена, сделка не закрыта"}, "rules": {"riskDays": 14},
            "asOf": "2026-10-30"}
    return {"rows": rows, "life": life, "ork": {"asOf": "2026-10-30", "week": week, "how": {"norms": {"1": 1, "12": 10}}}}


def _data_mode() -> dict:
    """Поля расчёта пакетов волны 1 (договор 4.3) пришли: шаблон берёт их как есть."""
    X = copy.deepcopy(_base())
    W = X["ork"]["week"]
    W["tiles"]["W1"] = {"state": "show", "value": 1, "n": 2, "unit": "deal"}
    W["tiles"]["W1o"] = {"state": "show", "value": 1, "n": 1, "unit": "order"}
    W["tiles"]["W2"] = {"state": "show", "value": 1, "n": 8, "byStep": {"5": 1}}
    W["drill"]["W2"] = ["9006"]                          # W2 — шаги заказа 4, 5, 8 (приёмка — в W1)
    W["tiles"]["unpaid"] = {"orders": 3, "deals": 2, "rev": 27000, "oldest": "2026-09-01"}
    W["drill"]["W1"] = {"unit": "deal", "keys": ["d:5002"]}
    W["drill"]["W1o"] = {"unit": "order", "keys": ["9001"]}
    for s in W["ledger"]:
        if s["no"] in (1, 2, 3, 13):
            s["unit"], s["now"] = "deal", {1: 2, 2: 2, 3: 0, 13: 1}[s["no"]]
        else:
            s["unit"] = "order"
    W["drill"]["step"]["1"] = {"now": ["d:5001", "d:5002"], "over": ["d:5002"]}
    W["drill"]["step"]["2"] = {"now": ["d:5003", "d:5004"]}
    W["drill"]["step"]["13"] = {"now": ["d:5005"], "close": ["d:5005"]}
    W["adjacent"] = {"kam": {"orders": 0, "deals": 2}, "oss": {"orders": 0, "deals": 1}, "log": {"orders": 2, "deals": 0}, "sup": {"orders": 0, "deals": 0}}
    return X


@pytest.fixture(scope="module")
def run():
    inp = {"code": TPL[TPL.index(START):TPL.index(END)], "esc": _line("const _esc="), "ctN": _line("const _ctN="),
           "views": {"base": _base(), "data": _data_mode()}}
    p = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False), capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads(p.stdout)


def test_W1_сделки_n_сделки_на_шаге_1(run):
    """W1 — разные сделки (501 с заказом на приёмке и 502 без заказа), n — сделки шага 1, а не
    «заказы + сделки» (было 2 и 3 в смешанной единице); список за числом — строка на сделку."""
    o = run["base"]
    assert o["U"]["w1"] == {"v": 2, "n": 2}
    assert "Сделок: приёмка дольше 1 раб. дн. W1 2" in o["head"]
    assert 'data-od="w:W1"' in o["headRaw"] and 'data-ods="из 2 сделок на шаге 1"' in o["headRaw"]   # шапка списка за числом
    L = o["lists"]["w:W1"]
    assert L["unit"] == "deal" and L["rows"] == 2 and L["keys"] == ["d:5001", "d:5002"] or sorted(L["keys"]) == ["d:5001", "d:5002"]
    assert L["title"].startswith("2 сделки (1 заказ):"), L["title"]
    # поля расчёта пришли — W1 и W1′ берутся как есть, у каждого своя единица
    d = run["data"]
    assert d["U"]["w1"] == {"v": 1, "n": 2} and "Сделок: приёмка дольше 1 раб. дн. W1" in d["head"]
    # W1′ (заказы) — подстрокой W1 (сделки): у каждого своё число и единица (ревью сведения, П-2)
    assert re.search(r"новые заказы ждут менеджера ОРК ?: 1", d["head"]), d["head"][d["head"].find("Ещё"):][:300]
    assert 'data-od="w:W1o"' in d["headRaw"]


def test_клиент_не_оплатил_сделки_заказы_в_скобках(run):
    """«Клиент не оплатил в срок» — сделки (2), заказы — рядом (3): один долг по сделке 503 с двумя
    заказами — одна сделка. То же значение — в ведомости (шаг 13) и в списке за числом."""
    o = run["base"]
    assert o["U"]["unp"]["deals"] == 2 and o["U"]["unp"]["orders"] == 3
    assert re.search(r"Сделок: клиент не оплатил в срок 3 заказа · с 01\.09 · КАМ 2", o["head"]), o["head"]
    assert o["lists"]["w:_fb/unpaidO"]["rows"] == 3 and o["lists"]["w:_fb/unpaidO"]["unit"] == "order"
    assert "клиент не оплатил 2 сделки 3 заказа в них" in o["led"]
    L = o["lists"]["w:unpaid"]
    assert L["unit"] == "deal" and L["rows"] == 2 and L["title"].startswith("2 сделки (3 заказа):"), L["title"]
    # без tiles.unpaid.deals — тот же счёт из ключей списка (разные сделки)
    assert run["data"]["U"]["unp"]["deals"] == 2 and "≈€27 тыс." in run["data"]["head"] and "доля этих заказов в сумме сделок — оценка" in run["data"]["headRaw"]


def test_W5_звонки_по_заказам(run):
    o = run["base"]
    assert o["U"]["w5"] == {"pts": 3, "orders": 2}
    assert "Звонков по 2 заказам W5 3" in o["head"] and o["lists"]["w:_fb/w5orders"]["rows"] == 2
    assert run["base"]["lists"]["w:W5"]["title"].startswith("3 звонка по 2 заказам")


def test_ведомость_в_двух_частях(run):
    """Шаги сделки — сделками: 1 = {501, 502}, 2 = {503} (502 — уже на шаге 1), 3 = {504},
    13 = {505}; шаги заказа — заказами; итоги частей не складывают единицы."""
    o = run["base"]
    ds = {k: v["n"] for k, v in o["U"]["dstep"].items()}
    assert ds == {"1": 2, "2": 1, "3": 1, "13": 1}, ds
    led = o["led"]
    assert "Шаг заказа Кто делает Заказов на шаге" in led and "Шаг сделки Кто делает Сделок на шаге" in led
    assert "Всего заказов на шагах 4–12 7" in led and "Всего сделок на шагах 1–3 и 13 5" in led
    # кнопка числа сделок шага открывает список сделок, строк — столько же
    for no, n in ds.items():
        lid = o["U"]["dstep"][no]["id"]
        if n:
            L = o["lists"][lid]
            assert L["unit"] == "deal" and L["rows"] == n, (no, L["title"])
    # поля расчёта: ledger[].unit и now — как есть
    dd = {k: v["n"] for k, v in run["data"]["U"]["dstep"].items()}
    assert dd == {"1": 2, "2": 2, "3": 0, "13": 1} and run["data"]["U"]["dstep"]["2"]["id"] == "w:step/2/now"


def test_кого_ждём_двумя_числами(run):
    o = run["base"]
    assert o["U"]["adj"]["kam"]["deals"] == 2 and o["U"]["adj"]["kam"]["orders"] == 0
    assert o["U"]["adj"]["oss"]["deals"] == 1 and o["U"]["adj"]["log"]["orders"] == 2
    assert "по сделкам: договор с заказчиком, подписанный договор с заказчиком (шаги 2, 3)" in o["wait"]
    assert "Заказов Сделок" in o["wait"]
    d = run["data"]["U"]["adj"]
    assert d["kam"]["deals"] == 2 and d["oss"]["deals"] == 1 and d["log"]["orders"] == 2


def test_без_менеджера_одно_значение_и_люди_по_своим_заказам(run):
    """«Без менеджера ОРК» — заказы, где менеджер не назначен (строка «Пульса»): одно число в выводе,
    счётчике и таблице. «Дольше нормы», «сорван» у строк «не назначен» и «не из ОРК» — по их заказам
    (было: «исполнитель шага не назначен» склеивал обе строки); итог — прежний."""
    o = run["base"]
    assert o["noMgr"] == 2
    assert "Без менеджера ОРК — 2 заказа." in o["head"] and "Заказов без менеджера ОРК 2" in o["head"]
    assert "без менеджера ОРК — 2 заказа; ответственный не из ОРК — 2 заказа" in o["ppl"]
    rows = {m.group(1): m.group(2) for m in re.finditer(r"(Менеджер ОРК не назначен|Ответственный не из ОРК) [^0-9]*?(\d+ \d+ \d+ \d+)", o["ppl"])}
    assert rows["Менеджер ОРК не назначен"] == "2 1 1 0", rows       # заказы, дольше нормы, сорван, ждём УПД
    assert rows["Ответственный не из ОРК"] == "2 0 1 0", rows
    assert re.search(r"Итого 8 2 2 0", o["ppl"]), o["ppl"]
    late = [i for i, _t in o["btn"]["ppl"] if i.startswith("w:_fb/pulseLate")]
    assert late and all(o["lists"][i]["rows"] == 1 for i in late)


def test_W3_действие_у_заказа_и_у_сделки(run):
    h = run["base"]["head"]
    assert "Менеджер ОРК → ускорить поставку · КАМ → новый срок с клиентом" in h
    assert "Менеджер ОРК → новый срок · в курсе КАМ" not in h


def test_каждое_число_недели_открывает_список_своей_единицы(run):
    """Число — кнопка; строк в списке — столько, сколько в числе: сделок у списка сделок, заказов
    (или строк-звонков W5) у списка заказов."""
    for name in ("base", "data"):
        o = run[name]
        for part, bs in o["btn"].items():
            for lid, txt in bs:
                m = re.match(r"\d+", txt)
                if not m:
                    continue
                L = o["lists"][lid]
                assert L is not None, (name, lid)
                assert L["rows"] == int(m.group()), (name, part, lid, txt, L["title"])


def test_разбор_числа_единица_из_данных_и_пары_под_заказом(run):
    """D[key] = {unit, keys}: единица из данных; пары «заказ × отрезок | файл» — строка заказа и
    вложенные строки, заголовок считает пары у разных заказов; имя листа Excel — с единицей."""
    du = run["dataUnit"]
    assert du["unit"] == "deal" and du["rows"] == 1 and du["title"].startswith("1 сделка (2 заказа)")
    assert "Причина — одна на сделку" in du["html"] and 'class="odhist"' not in du["html"]
    assert du["xl"].startswith("Сделки")
    seg = run["seg"]
    assert seg["unit"] == "segment" and seg["rows"] == 2 and seg["pairs"] == 3
    assert seg["title"].startswith("3 отрезка у 2 заказов"), seg["title"]
    assert "Где эти заказы сейчас" in seg["html"] and "Что делать сейчас" in seg["html"]
    fl = run["file"]
    assert fl["unit"] == "file" and fl["rows"] == 2 and fl["pairs"] == 3
    assert fl["title"].startswith("3 обязательных файла у 2 заказов, на месте 1"), fl["title"]
    assert "файл" in fl["xl"].lower()                     # единица — в имени листа (подпись её уже несёт)


def test_причина_сделки_от_хозяина(run):
    """«Закрыть сделку» делает ОСС: причина — «у ОСС», а не «у клиента и КАМа» шага 13 (E29)."""
    c = run["close"]
    assert c["unit"] == "deal" and c["items"][0]["cause"] == "oss"
    assert c["title"].startswith("1 сделка:")
    assert "Строка — карточка сделки" in c["html"]


def test_сделка_без_дел_не_у_менеджера_орк(run):
    """Сделка без пункта очереди: «дел по сделке нет», а не «Разобрать сделку · менеджер ОРК»;
    причина — от шага её стадии (шаг 3 — договор с заказчиком, ждём КАМа)."""
    i = run["idle"]
    assert i["unit"] == "deal" and i["rows"] == 1
    assert "Дел по сделке в очереди нет" in i["html"] and "Разобрать сделку" not in i["html"]
    assert i["items"][0]["cause"] == "kam"
