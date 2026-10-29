"""Вёрстка раздела «ork» вкладки «Реализация» без браузера: функции шаблона (_orkWeek,
_orkMonth, _orkHow) исполняются в node:vm на данных smoke-сборки (fixture.ContractsStub через
настоящий contracts.compute). Проверяется то, что читает руководитель ОРК: неизвестное не
рисуется нулём, «Месяц» без права — «нет доступа». Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import copy
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
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + (inp.dcCode || 'function _dcData(){return null;}') + '\n' + inp.code
  + '\nthis._orkWeek=_orkWeek; this._orkMonth=_orkMonth; this._orkHow=_orkHow;', box);
const out = {};
for (const [k, X] of Object.entries(inp.views)) {
  const el = node();
  box._orkWeek(X, X.ork, el, '');
  out[k] = {week: el.innerHTML + Object.values(byId).map(n => n.innerHTML).join(''),
            month: box._orkMonth(X, X.ork), how: box._orkHow(X, X.ork)};
  for (const id of Object.keys(byId)) delete byId[id];
}
process.stdout.write(JSON.stringify(out));
"""


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


def _fn(name: str) -> str:
    """Текст функции шаблона верхнего уровня — до следующей функции или комментария."""
    a = TPL.index(f"function {name}(")
    b = min(i for i in (TPL.find("\nfunction ", a + 1), TPL.find("\n//", a + 1)) if i > 0)
    return TPL[a:b]


def _render(views: dict, ork_month, dc: dict | None = None) -> dict:
    code = TPL[TPL.index(START):TPL.index(END)]
    inp = {"code": code, "esc": _line("const _esc="), "ctN": _line("const _ctN="), "views": views,
           "orkMonth": ork_month}
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
    """W5 «Нет данных» (в производстве есть, плановых дат нет) — таблица точек не «· 0» и не
    «точек нет»: заголовок «· —» и подпись плитки. Пустые списки W1/W2/W3 при «Нет данных»
    — подпись плитки, а не «пусто — всё в норме»."""
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
    out = _render({"sep": sep}, built["ork_month"])["sep"]["week"]
    assert "Контрольные точки недели · —" in out and "Контрольные точки недели · 0" not in out
    assert "на этой неделе контрольных точек нет" not in out
    assert "Нет данных: нет плановых дат производства" in out
    for k in ("W2", "W3"):
        assert f"Нет данных: стадия не из таблицы шагов ({k})" in out, k
    assert "никого — все шаги ОРК в норме" not in out and "сроки клиентов не под угрозой" not in out


def test_контрольные_точки_известный_ноль(built):
    """W5 показан и точек нет — «точек нет» (известный ноль); у части заказов нет дат —
    строка «точки известны не у всех»."""
    ct = copy.deepcopy(built["contracts"])
    w = ct["ork"]["week"]
    w["tiles"]["W5"] = {**w["tiles"]["W5"], "state": "show", "value": 0, "noDates": 2, "prod": 5}
    w["checkpoints"] = []
    out = _render({"main": ct}, built["ork_month"])["main"]["week"]
    assert "Контрольные точки недели · 0" in out and "на этой неделе контрольных точек нет" in out
    assert "без плановых дат — 2 из 5" in out


def test_месяц_без_права_нет_доступа(built):
    """Решение владельца 26.10.2026, п. 3: «Месяц» — только руководителям и владельцу. Без права
    (воркер вырезал ORKMONTH) — вся подвкладка «нет доступа», ни плиток отдела, ни S1; в «Как
    считается» у M1–M3 — «под правом»."""
    ct = copy.deepcopy(built["contracts"])
    без = _render({"main": ct}, {"__kvNoData": True})["main"]
    assert "нет доступа: нужно право «Реализация: показатели менеджеров ОРК»" in без["month"]
    for слово in ("M1 · ", "приёмка", "По менеджерам ОРК", "Своевременность шагов по месяцам"):
        assert слово not in без["month"], слово
    assert без["how"].count("под правом") >= 3
    с = _render({"main": ct}, built["ork_month"])["main"]
    assert "нет доступа" not in с["month"] and "M1 · " in с["month"] and "По менеджерам ОРК" in с["month"]
    assert "Своевременность шагов по месяцам" in с["month"]


def test_ждём_упд_от_клиента_отдельно_от_сверх_нормы(built):
    """Решение владельца 27.10.2026, Б: шаг 12 больше 10 р.д. без УПД — «ждём УПД от клиента»
    в ведомости (вместо числа «сверх нормы») и своим столбцом у «Сверх нормы по менеджерам»;
    в подписи W2 шага 12 нет. Числа — те же, что в данных недели (одно правило ork.judged)."""
    ct = copy.deepcopy(built["contracts"])
    w = ct["ork"]["week"]
    n = w["waitUtd"]["n"]
    assert n > 0 and w["ledger"][11]["no"] == 12 and w["ledger"][11]["waitUtd"] == n
    out = _render({"main": ct}, built["ork_month"])["main"]["week"]
    assert f"ждём УПД от клиента {n}</span>" in out, "ведомость, шаг 12"
    assert "<th class=\"r\">Ждём УПД от клиента</th>" in out, "столбец по менеджерам"
    assert f"ждём УПД от клиента (не в счёте): {n}" in out, "подпись W2"
    assert "УПД (12):" not in out and "(эскалация УПД)" not in out
    # итог столбцов: Σ «сверх нормы» = W2, Σ «ждём УПД» = n
    tot = f'<td class="r mono"><b>{w["tiles"]["W2"]["value"]}</b></td><td></td><td class="r mono"><b>{n}</b></td>'
    assert tot in out


def test_ждём_упд_ноль_при_неизвестном_сроке_прочерк():
    """Все заказы шага 12 вида — срок не посчитать (нет факта доставки): в «Сверх нормы»
    ведомости — «—», а не «ждём УПД от клиента 0» (ноль за неизвестное, правило 15); рядом
    «? N — срок неизвестен», в подписи W2 — «срок УПД неизвестен: N». Корпус —
    fixture.Шаг12Направления через настоящий contracts.compute; затем тот же вывод на
    ручной строке (waitUtd 0, overUnknown == now)."""
    import re
    data = fixture.build_contracts(stub=fixture.Шаг12Направления(wait=False))
    sep = json.loads(json.dumps(data["sep"], ensure_ascii=False, default=str))
    sep["dir"] = "sep"

    def cell(view):
        out = _render({"v": view}, None)["v"]["week"]
        i = out.index('data-step="12"')
        row = out[i:out.index("</tr>", i)]
        return out, re.findall(r'<td class="r mono[^"]*">(.*?)</td>', row)[1]
    out, c = cell(sep)
    assert "ждём УПД от клиента 0" not in c and ">—</span>" in c and "? 1 — срок неизвестен" in c, c
    assert "срок УПД неизвестен: 1" in out
    # ручная строка: у шага 12 2 заказа, оба без срока, один «не в счёте» нет — тоже «—»
    v = copy.deepcopy(sep)
    r12 = next(r for r in v["ork"]["week"]["ledger"] if r["no"] == 12)
    r12.update(now=2, over=0, waitUtd=0, overUnknown=2, notCounted=0)
    _o, c = cell(v)
    assert "ждём УПД от клиента 0" not in c and ">—</span>" in c, c
    # известный ноль (срок есть, ждать нечего) — «ждём УПД от клиента 0», не прочерк
    r12.update(now=2, over=0, waitUtd=0, overUnknown=1, notCounted=0)
    _o, c = cell(v)
    assert "ждём УПД от клиента 0" in c, c


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
    assert week.index("orkQs") < week.index("ctImpS"), "блок — под очередью"
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
