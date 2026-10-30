"""«Что сделать сегодня» на три части (спецификация «сделка и заказ поставщика», §2; пакет T1 = TP-1 + TP-2).

Функции шаблона (_orkWeekCtx, _orkWeekToday, _wkGroups, _wkgRow, _wkgAct, _wkgDetail, _wkNo, _odQueueR)
исполняются в node:vm на придуманной очереди (CLAUDE.md, правило 18: компании, люди и номера выдуманы).
Проверяется то, что владелец назвал путаницей сделок и заказов: дневной счёт — два числа в своих единицах,
порядок в карточках — вне счёта, счёт группы — разные объекты, клиентский вопрос — один раз в шапке сделки.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.test_ork_template import END, START, TPL, _fn, _line

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нет node")

HARNESS = r"""
const vm = require('node:vm'); const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const node = () => ({innerHTML: '', value: '', textContent: '', dataset: {}, classList: {toggle() {}},
  addEventListener() {}, removeEventListener() {}, setAttribute() {}, scrollIntoView() {},
  closest() { return node(); }, querySelector() { return null; }, querySelectorAll() { return []; }, contains() { return false; }});
const document = {getElementById: () => node(), querySelectorAll: () => [], addEventListener() {}, removeEventListener() {}};
const box = {document, window: {__ORKMONTH__: null, __DEALCTX__: null}, location: {hash: ''}, console, JSON, Math, Object,
  Array, String, Number, Set, Map, RegExp, qmark: () => '', openContract() {}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + inp.unpack + '\nfunction _dcData(){return null;}\n' + inp.code
  + '\nthis.T={_orkWeekCtx,_orkWeekToday,_wkGroups,_wkgRow,_wkgDetail,_wkgAct,_wkNo,_odQueueR,_wkB,_wkIU,_WK_STATE};'
  + '\nthis.setKam=f=>{_rzKam=f;};', box);
const T = box.T, out = {};
for (const [name, X] of Object.entries(inp.cases)) {
  for (const k of Object.keys(T._WK_STATE)) delete T._WK_STATE[k];
  const C = T._orkWeekCtx(X, X.ork), F = X.F || {role: '', who: ''};
  const P = T._wkGroups(C, F);
  out[name] = {
    today: T._orkWeekToday(C, F), hyg: T._orkWeekToday(C, F, 'hyg'),
    parts: P.parts.map(p => ({b: p.b, orders: p.orders, inDeals: p.inDeals, deals: p.deals, dela: p.dela, people: p.people,
      groups: p.GS.map(g => ({key: g.key, code: g.code, unit: g.unit, n: g.n, deals: g.deals.size, items: g.items.length,
        row: T._wkgRow(C, g, false), detail: T._wkgDetail(C, g, true)}))})),
    list: Object.fromEntries(P.GS.map(g => [g.key, (() => {const f = {}; (g.key.startsWith('c:') ? [] : [['code', [...g.codes].sort().join('|')]]).concat([['b', g.b]]).forEach(([k, v]) => f[k] = v);
      const R = T._odQueueR(X, f); return R ? R.k : null;})()])),
  };
}
process.stdout.write(JSON.stringify(out));
"""

# придуманный корпус: три сделки, у 501 — три заказа; 502 — без заказа; 503 — оплата получена, не закрыта
ROWS = [
    {"deal": 501, "seq": 501, "customer": "Компания 1", "kam": "Анна (КАМ)", "stageName": "Производство | Ожидание", "dealStep": 6,
     "orders": [{"id": 9011, "lbl": "501/1", "live": True, "step": 9}, {"id": 9012, "lbl": "501/2", "live": True, "step": 6},
                {"id": 9013, "lbl": "501/3", "live": True, "step": 4}]},
    {"deal": 502, "seq": 502, "customer": "Компания 2", "kam": "Борис (КАМ)", "stageName": "ТКП согласован", "dealStep": 2, "orders": []},
    {"deal": 503, "seq": 503, "customer": "Компания 3", "kam": "Анна (КАМ)", "stageName": "Оплата получена | Закрытие сделки",
     "dealStep": 13, "orders": [{"id": 9031, "lbl": "503/1", "live": True, "step": 12}]},
]


def _o(code, order, lbl, no, deal, days, who="Пётр (ОРК)", **kw):
    """Пункт заказа — как lifecycle.build до свёртки (step — шаг кода по ork.ITEM, не сегодняшний шаг)."""
    x = {"code": code, "sev": 1, "what": f"что: {code}", "act": f"сделать: {code}", "days": days, "step": 13,
         "role": "orkMgr", "roleName": "менеджер ОРК", "who": who, "escalateTo": "Анна (КАМ)", "copyRole": "kam",
         "kam": "Анна (КАМ)", "deal": deal, "seq": deal, "customer": f"Компания {deal - 500}", "order": order,
         "ordLbl": lbl, "ordNo": no, "ref": f"срок {no:02d}.09.2026", "sup": f"Поставщик {no}"}
    x.update(kw)
    return x


def _d(code, deal, days=None, who="Роза (рук. ОРК)", role="orkHead", **kw):
    x = {"code": code, "sev": 3, "what": f"что: {code}", "act": f"сделать: {code}", "days": days, "step": 5,
         "role": role, "roleName": "руководитель ОРК", "who": who, "escalateTo": "", "copyRole": "",
         "kam": "", "deal": deal, "seq": deal, "customer": f"Компания {deal - 500}", "order": "", "ordLbl": "",
         "ordNo": None, "ref": "", "sup": ""}
    x.update(kw)
    return x


QUEUE = [
    # A: срок сорван у двух заказов одной сделки и у третьего — другой код
    _o("late", 9011, "501/1", 1, 501, 30), _o("late", 9012, "501/2", 2, 501, 12, who="не назначен"),
    _o("s2Over", 9013, "501/3", 3, 501, 5),
    # B: долг клиента по двум заказам одной сделки (до свёртки — два пункта заказа) — одна сделка
    _o("unpaid", 9011, "501/1", 1, 501, 40, role="kam", roleName="КАМ", who="Анна (КАМ)", escalateTo="", copyRole=""),
    _o("unpaid", 9012, "501/2", 2, 501, 10, role="kam", roleName="КАМ", who="Анна (КАМ)", escalateTo="", copyRole=""),
    # B: приёмка — заказ 501/3 на шаге 1 (s1Over) и сделка 502 без заказа (noOrder) — одна группа в сделках
    _o("s1Over", 9013, "501/3", 3, 501, 2, role="orkHead", roleName="руководитель ОРК", who="Роза (рук. ОРК)"),
    _d("noOrder", 502, days=4),
    _d("payNoContract", 501),
    # C: оплата получена, не закрыта (оговорка в what), нет срока оплаты по двум заказам, нет дат
    _d("dealClose", 503, days=7, who="Дина (ОСС)", role="oss",
       what="оплата получена, сделка не закрыта; доставлено без УПД: 1 (503/1) — закрыть после исполнения"),
    _o("noPayPlan", 9011, "501/1", 1, 501, 3), _o("noPayPlan", 9012, "501/2", 2, 501, 2),
    _o("noPlanDates", 9013, "501/3", 3, 501, None),
]


def _case(queue=None, F=None):
    X = {"life": {"queue": json.loads(json.dumps(queue if queue is not None else QUEUE)), "labels": {}}, "rows": ROWS,
         "ork": {"week": {"tiles": {}, "ledger": [{"no": n, "name": f"шаг {n}"} for n in range(1, 14)], "pulse": {"rows": []}},
                 "how": {"norms": {"1": 1}}}}
    if F:
        X["F"] = F
    return X


@pytest.fixture(scope="module")
def out():
    code = TPL[TPL.index(START):TPL.index(END)]
    cases = {"main": _case(),
             "who": _case(F={"role": "", "who": "Пётр (ОРК)"}),
             "contract": _case([{**_o("late", 9011, "501/1", 1, 501, 3), "bucket": "hyg", "unit": "order"},
                                {**_d("dealClose", 503, days=1), "note": "оговорка из поля note", "unit": "deal", "bucket": "hyg",
                                 "ords": [{"id": 9031, "lbl": "503/1", "sup": "Поставщик 9", "dl": "01.10.2026", "days": 1}]}])}
    inp = {"code": code, "esc": _line("const _esc="), "ctN": _line("const _ctN="), "unpack": _fn("_unpack"), "cases": cases}
    r = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout)


def _part(o, b):
    return next(p for p in o["parts"] if p["b"] == b)


def _grp(p, code):
    return next(g for g in p["groups"] if g["code"] == code)


def _txt(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html).replace("\xa0", " "))


def test_дневной_счёт_два_числа_в_своих_единицах(out):
    """Шапка: «по заказам: N заказов · по сделкам: M сделок» — разные заказы и разные сделки, без «пунктов»
    и без общей суммы; порядок в карточках — отдельной ссылкой, вне дневного счёта."""
    o = out["main"]
    A, B, H = _part(o, "exec"), _part(o, "deal"), _part(o, "hyg")
    assert (A["orders"], A["inDeals"]) == (3, 1)          # 501/1, 501/2, 501/3 — одна сделка
    assert B["deals"] == 2                                  # 501 (долг, приёмка, оплата без договора) и 502 (без заказа)
    assert (H["orders"], H["deals"]) == (1, 2)             # 501/3 без дат; 503 закрыть, 501 нет срока оплаты
    h2 = _txt(re.search(r'<h2 class="wkh2" id="wkTodayH">(.*?)</h2>', o["today"]).group(1))
    assert "по заказам: 3 заказа" in h2 and "по сделкам: 2 сделки" in h2
    assert "пункт" not in h2 and "5" not in h2, "сумма заказов и сделок в шапке"
    assert "Порядок в карточках: 1 заказ · 2 сделки" in _txt(o["today"])
    assert "Порядок в карточках Битрикса: 1 заказ · 2 сделки" in _txt(o["hyg"]) and "не входит" in o["hyg"]
    # у каждой части — свои люди: A — Пётр (не назначен не считается), B — Анна и Роза
    assert A["people"] == 1 and B["people"] == 2


def test_счёт_группы_разные_объекты_единицы(out):
    """Группа «долг клиента» из двух пунктов заказа одной сделки — 1 сделка; «срок сорван» — 2 заказа в 1 сделке;
    приёмка (s1Over у заказа и noOrder у сделки) — одна группа «приёмка» в сделках; число группы = строки списка."""
    o = out["main"]
    B = _part(o, "deal")
    up = _grp(B, "unpaid")
    assert (up["unit"], up["n"], up["items"]) == ("deal", 1, 2)
    assert re.search(r'data-tq="deal:unpaid"[^>]*>1</button> <span class="wkmut wkgnu">сделка</span>', up["row"])
    acc = _grp(B, "accept")
    assert (acc["unit"], acc["n"]) == ("deal", 2) and not any(g["code"] in ("s1Over", "noOrder") for g in B["groups"])
    late = _grp(_part(o, "exec"), "late")
    assert (late["unit"], late["n"], late["deals"]) == ("order", 2, 1)
    assert "заказа" in late["row"] and "в 1 сделке" in late["row"]
    # список за числом группы — объекты её единицы, каждый один раз: столько строк, сколько в числе
    for p in o["parts"]:
        for g in p["groups"]:
            k = o["list"][g["key"]]
            assert len(k) == len(set(k)) == g["n"], g["key"]
            assert all(x.startswith("d:") for x in k) == (g["unit"] == "deal"), g["key"]


def test_своя_шкала_важности_у_части(out):
    """B сортирует свои группы своей шкалой: оплата поставщику без договора — выше приёмки и долга (по EXC
    у них другая важность), группы A и C в список B не попадают."""
    B = _part(out["main"], "deal")
    assert [g["code"] for g in B["groups"]] == ["payNoContract", "accept", "unpaid"]
    assert [g["code"] for g in _part(out["main"], "exec")["groups"]] == ["late", "s2Over"]
    assert [g["code"] for g in _part(out["main"], "hyg")["groups"]] == ["noPayPlan", "dealClose", "noPlanDates"]


def test_клиентский_вопрос_один_раз_в_шапке_сделки(out):
    """«Срок сорван»: заказы под шапкой сделки; «согласовать с клиентом новый срок» — один раз, у КАМа; у строки
    заказа — действие исполнения и менеджер ОРК, «в курсе: КАМ» не повторяется."""
    d = _grp(_part(out["main"], "exec"), "late")["detail"]
    assert d.count('class="wkdhr"') == 1 and d.count("согласовать с клиентом новый срок") == 1
    hdr = _txt(d[d.index('class="wkdhr"'):d.index("</tr>", d.index('class="wkdhr"'))])
    assert "Сделка №501" in hdr and "2 заказа" in hdr and "Анна" in hdr
    body = d[d.index("</tr>", d.index('class="wkdhr"')):]
    assert body.count('<tr>') == 2 and "в курсе" not in body
    assert ">501/1 ›<" in body and ">501/2 ›<" in body
    assert "Ускорить поставку" in _grp(_part(out["main"], "exec"), "late")["row"]


def test_шаг_строки_заказа_сегодняшний(out):
    """Строка заказа — шаг заказа сегодня (step_now из таблицы сделок), а не шаг кода по ork.ITEM (13)."""
    d = _grp(_part(out["main"], "exec"), "s2Over")["detail"]
    assert "<th scope=\"col\">Шаг заказа</th>" in d
    assert re.search(r"<td>4 · шаг 4", d) and "<td>13 · " not in d


def test_таблица_сделок_заказы_и_оговорка_под_строкой(out):
    """Часть B: «Сделка · Стадия сделки · Заказы · Дней · Кто»; у долга — заказы списком в строке, дни — по
    худшему; «закрыть сделку» в C — оговорка отдельной строкой под сделкой, стадия словами, шаг сделки 13."""
    d = _grp(_part(out["main"], "deal"), "unpaid")["detail"]
    heads = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', d)
    assert heads == ["Сделка", "Стадия сделки", "Заказы", "Дней", "Кто делает · кто в курсе"]
    assert d.count('class="wkno wkdeal wkdn"') == 1 and "сделка №501 ›" in d
    assert ">501/1 ›<" in d and ">501/2 ›<" in d and "2 заказа" in _txt(d) and "не оплачено 40 дн." in _txt(d)
    assert "Производство" in d and "Ожидание" not in _txt(d) and "заказы на шагах 4–9" in _txt(d)
    c = _grp(_part(out["main"], "hyg"), "dealClose")["detail"]
    assert 'class="wksubr"' in c and "Оговорка:" in c and "доставлено без УПД: 1 (503/1)" in c
    assert "шаг 13 · " in c


def test_отбор_по_человеку_в_каждой_части(out):
    """«Смотреть как»/отбор по человеку режет каждую часть отдельно: у Петра дела по заказам есть, по сделкам —
    «По сделкам под отбор … дел нет»."""
    o = out["who"]
    assert _part(o, "exec")["orders"] == 2 and _part(o, "deal")["deals"] == 0
    assert "По сделкам под отбор «человек: Пётр (ОРК)» дел нет." in _txt(o["today"])


def test_поля_договора_bucket_unit_ords_note(out):
    """Поля договора §4.3 главнее запасных таблиц: пункт с bucket=hyg уходит в порядок в карточках, у пункта
    сделки — ords из данных и note отдельным полем."""
    o = out["contract"]
    assert _part(o, "exec")["orders"] == 0 and _part(o, "hyg")["orders"] == 1
    c = _grp(_part(o, "hyg"), "dealClose")["detail"]
    assert "оговорка из поля note" in c and ">503/1 ›<" in c and "Поставщик 9" in c
