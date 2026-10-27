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
const box = {document, window: {__ORKMONTH__: inp.orkMonth, __DEALCTX__: null, scrollTo() {}},
  location: {hash: ''}, console, JSON, Math, Object, Array, String, Number, Set, Map, RegExp,
  qmark: () => '', openContract() {}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + 'function _dcData(){return null;}\n' + inp.code
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


def _render(views: dict, ork_month) -> dict:
    code = TPL[TPL.index(START):TPL.index(END)]
    inp = {"code": code, "esc": _line("const _esc="), "ctN": _line("const _ctN="), "views": views,
           "orkMonth": ork_month}
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
