"""R4 · WP-C «Разбор числа» (У3): «Почему», сверка числа с частями, гистограмма шагов, адрес, текст.

Спецификация R4 (раздел 3.7 и 4: Д-1, Д-4 потребляются, расчёт не меняется):
- каждый заказ считается один раз, по самому тяжёлому виду срока: сумма частей = число плитки = строк;
- «Почему»: у каждого заказа одна причина (данные WP-B `week.orders[].cause`, без них — правило Д-4 по
  кодам очереди и «кого ждём» шага), сумма причин = число списка;
- отборы в адресе — грамматика 2.6, имён людей в адресе нет;
- дни словами (2.7), запрещённые слова (2.7) в разметке списка не встречаются.
Корпус придуман (CLAUDE.md, правило 18): номера, люди и компании — заглушки.
"""
from __future__ import annotations

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

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нет node")

TPL = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
START, END = "// ---- «Реализация» по регламенту ОРК", "function _ctPanel("


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


# придуманные заказы: 8 заказов за W3, у одного два вида срока (сорван и прогноз позже)
ORDERS = [
    {"k": "9001", "no": "801/1", "deal": "5001", "seq": 801, "sup": "Поставщик А", "step": 5, "wd": 14, "norm": 9, "over": True,
     "mgr": "Менеджер 1 (ОРК)", "kam": "КАМ 1 (КАМ)", "dl": "2026-10-01", "q": ["late", "s3Over", "noPlanDates"]},
    {"k": "9002", "no": "802/1", "deal": "5002", "seq": 802, "sup": "Поставщик Б", "step": 7, "waits": "sup", "cd": 40,
     "mgr": "Менеджер 1 (ОРК)", "dl": "2026-10-05", "q": ["late", "prodDev"]},
    {"k": "9003", "no": "803/1", "deal": "5003", "seq": 803, "sup": "Поставщик Б", "step": 9, "waits": "log", "cd": 12,
     "mgr": "не назначен", "dl": "2026-10-10", "fc": "2026-11-02", "q": ["late", "forecast"]},
    {"k": "9004", "no": "804/1", "deal": "5004", "seq": 804, "sup": "Поставщик В", "step": 10, "waits": "kam", "cd": 5,
     "mgr": "Менеджер 2 (ОРК)", "dl": "2026-10-25", "q": ["risk"]},
    {"k": "9005", "no": "805/1", "deal": "5005", "seq": 805, "sup": "Поставщик В", "step": 6, "waits": "oss", "cd": 3,
     "mgr": "Менеджер 2 (ОРК)", "dl": "2026-11-20", "fc": "2026-12-01", "q": ["forecast"]},
    {"k": "9006", "no": "806/1", "deal": "5006", "seq": 806, "sup": "Поставщик Г", "step": None,
     "mgr": "Менеджер 2 (ОРК)", "dl": "2026-11-25", "fc": "2026-12-05", "q": ["forecast", "noPlanDates"]},
    # шаг ОРК в норме, без превышения: остаток правила Д-4 — «на шаге ОРК»
    {"k": "9007", "no": "807/1", "deal": "5007", "seq": 807, "sup": "Поставщик Г", "step": 4, "wd": 1, "norm": 2,
     "mgr": "Менеджер 1 (ОРК)", "dl": "2026-10-28", "q": ["risk"]},
    # причина пришла в данных (WP-B, Д-4) — берётся как есть, вторая — серым
    {"k": "9008", "no": "808/1", "deal": "5008", "seq": 808, "sup": "Поставщик Д", "step": 8, "wd": 1, "norm": 2,
     "mgr": "Менеджер 2 (ОРК)", "dl": "2026-10-12", "q": ["late"], "cause": "log", "cause2": "sup"},
]
# ключи W3: у 9003 два вида — «late» и «forecast»; число плитки — 8 разных заказов
W3_K = ["9001", "9002", "9003", "9008", "9004", "9007", "9003", "9005", "9006"]
W3_T = ["late", "late", "late", "late", "risk", "risk", "forecast", "forecast", "forecast"]
QUEUE = [
    {"order": "9001", "deal": "5001", "code": "late", "sev": 0, "act": "согласовать с клиентом новый срок", "role": "orkMgr",
     "roleName": "менеджер ОРК", "who": "Менеджер 1 (ОРК)", "kam": "КАМ 1 (КАМ)", "days": 18},
    {"order": "9002", "deal": "5002", "code": "prodDev", "sev": 3, "act": "запросить статус производства", "role": "orkMgr",
     "roleName": "менеджер ОРК", "who": "Менеджер 1 (ОРК)", "days": 9},
    {"order": "9004", "deal": "5004", "code": "risk", "sev": 1, "act": "подтвердить отгрузку", "role": "kam", "roleName": "КАМ",
     "who": "КАМ 1 (КАМ)", "days": 6},
]

HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const node = () => ({innerHTML: '', value: '', textContent: '', dataset: {}, classList: {toggle() {}, add() {}, remove() {}, contains() { return false; }},
  addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {}, getAttribute() { return null; }, scrollIntoView() {},
  closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; }, contains() { return false; }, appendChild() {}, insertBefore() {}});
const byId = {};
const document = {getElementById: id => (byId[id] ||= node()), querySelectorAll: () => [], querySelector: () => null,
  createElement: () => node(), addEventListener() {}};
const box = {document, window: {__CONTRACTS__: inp.ct, __DEALCTX__: null, scrollTo() {}, addEventListener() {}},
  location: {hash: '#contracts/week'}, history: {pushState() {}, replaceState() {}}, console, JSON, Math, Object, Array, String, Number,
  Set, Map, RegExp, Date, setTimeout: () => 0, clearTimeout() {}, qmark: () => '', openContract() {}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\nfunction _dcData(){return null;}\n' + inp.code
  + '\nthis._odModel=_odModel; this._odHtml=_odHtml; this._odPrep=_odPrep; this._odTitle=_odTitle; this._odRecon=_odRecon;'
  + ' this._odFParse=_odFParse; this._odFStr=_odFStr; this._odDays=_odDays; this._odAddr=_odAddr; this._odCauseL=_odCauseL;', box);
const X = inp.X, O = X.ork, out = {};
const M = box._odModel(X, O, {id: 'w:W3', title: 'Срок клиента под угрозой · W3'});
box._odPrep(M);
out.items = M.items.map(x => ({k: x.k, kind: x.kind, cause: x.cause, cause2: x.cause2}));
out.title = box._odTitle(M);
out.html = box._odHtml(M, {});
out.recon = box._odRecon(M);
// часть плитки (отбор из адреса): «сорван» и «под угрозой» (до срока + прогноз позже)
const rows = F => (box._odHtml(box._odModel(X, O, {id: 'w:W3', f: F}), box._odFParse(F)).match(/<tr class="drill[^"]*" data-odk=/g) || []).length;
out.part = {late: rows('part.late'), threat: rows('part.risk|forecast'), our: rows('cause.our'), step5: rows('step.5')};
// сумма частей не сходится с плиткой — сказано словами, а не спрятано
const X2 = JSON.parse(JSON.stringify(X)); X2.ork.week.tiles.W3.byOrders = {late: 4, risk: 2, forecast: 1};
out.reconBad = box._odRecon(box._odModel(X2, X2.ork, {id: 'w:W3'}));
// список людей: ключ с именем в адрес не идёт, имя не опускается в строчные
const MP = box._odModel(X, O, {keys: {k: ['9001'], t: null}, pre: 'w', title: 'Менеджер 1 · дольше нормы'});
out.peopleN = box._odModel(X, O, {id: 'w:who/Менеджер 1 (ОРК)/all'}) ? box._odModel(X, O, {id: 'w:who/Менеджер 1 (ОРК)/all'}).n : null;
out.peopleTitle = box._odTitle(Object.assign(MP, {id: 'w:who/x/all'})).txt;
out.colonTitle = box._odTitle(Object.assign(box._odModel(X, O, {keys: {k: ['9001'], t: null}, pre: 'w', title: 'Ждём: КАМ (шаги 2, 3, 10)'}), {})).txt;
out.f = {parse: box._odFParse('part.late|risk,cause.our,step.5,sup.12'), str: box._odFStr({part: ['late'], cause: ['sup'], step: '7'})};
out.addr = box._odAddr('#contracts/week/sep?n=w%3AW3&f=part.late&o=9001&s=5');
out.days = ['late', 'risk', 'forecast', 'prodDev', 's3Over', 'noUtd', 'unpaid', 'noOrder'].map(c => box._odDays(c, 7, {norm: 9}));
out.days0 = box._odDays('risk', 0);
out.labels = ['our', 'sup', 'log', 'kam', 'oss', 'nodata'].map(c => box._odCauseL(c));
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def run():
    ledger = [{"no": n, "name": f"Шаг {n}", "roleName": "менеджер ОРК", "norm": 2} for n in range(1, 14)]
    week = {"orders": ORDERS, "deals": [], "hist": [], "strs": [], "ledger": ledger,
            "drill": {"W3": {"k": W3_K, "t": W3_T}, "who": {"Менеджер 1 (ОРК)": {"over": ["9001"], "wait": []}}},
            "tiles": {"W3": {"state": "show", "value": 8}}}
    X = {"life": {"queue": QUEUE, "labels": {"late": "срок клиента прошёл", "risk": "до срока ≤ 14 дн.",
                                              "forecast": "прогноз позже срока", "prodDev": "производство дольше плана на 2 р.д."},
                  "rules": {"riskDays": 14}, "asOf": "2026-10-19"},
         "ork": {"week": week, "asOf": "2026-10-19"}}
    ct = {"rows": [{"deal": o["deal"], "seq": o["seq"], "customer": f"Заказчик {i}", "orders": [{"id": o["k"], "lbl": o["no"], "live": True}]}
                   for i, o in enumerate(ORDERS)]}
    inp = {"code": TPL[TPL.index(START):TPL.index(END)], "esc": _line("const _esc="), "ctN": _line("const _ctN="), "X": X, "ct": ct}
    p = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False), capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout)


def test_заказ_считается_один_раз_по_самому_тяжёлому_виду(run):
    """Д-1 (потребляется): 9003 — и «сорван», и «прогноз позже»: одна строка, вид — «late»;
    сумма частей = число плитки = строк списка; сверка печатает сложение."""
    it = {x["k"]: x for x in run["items"]}
    assert len(run["items"]) == len(it) == 8, "строка на заказ, а не на пару «заказ × вид»"
    assert it["9003"]["kind"] == "late"
    kinds = [x["kind"] for x in run["items"]]
    assert (kinds.count("late"), kinds.count("risk"), kinds.count("forecast")) == (4, 2, 2)
    assert run["recon"].startswith('<p class="odrec">8 = сорван 4 + до срока ≤ 14 дн. 2 + прогноз позже 2')
    assert "не сходится" not in run["recon"]
    assert run["html"].count('<tr class="drill') == 8
    assert "не сходится: сумма частей 7, строк в списке 8" in run["reconBad"]


def test_почему_одна_причина_у_каждого_и_сумма_равна_числу(run):
    """Д-4 (потребляется): правило без данных WP-B и причина из данных — у каждого заказа ровно одна;
    сумма «Почему» = число в заголовке."""
    it = {x["k"]: x for x in run["items"]}
    want = {"9001": "our", "9002": "sup", "9003": "log", "9004": "kam", "9005": "oss", "9006": "nodata", "9007": "our", "9008": "log"}
    assert {k: v["cause"] for k, v in it.items()} == want
    assert it["9008"]["cause2"] == "sup", "вторая причина — из данных"
    assert it["9001"]["cause2"] == "nodata", "+ не заполнены плановые даты"
    nums = [int(n) for n in re.findall(r'class="odwb[^"]*"[^>]*>.*?<b>(\d+)</b></button>', run["html"])]
    assert sum(nums) == 8 == int(re.match(r"(\d+)", run["title"]["txt"]).group(1))
    # заказ на шаге ОРК в норме — не «дольше нормы»: подпись честная
    assert "на шаге ОРК" in run["html"]


def test_отборы_части_причины_шага(run):
    p = run["part"]
    assert p == {"late": 4, "threat": 4, "our": 2, "step5": 1}, p


def test_адрес_и_имена(run):
    assert run["f"]["parse"] == {"part": ["late", "risk"], "cause": ["our"], "step": "5"}, "sup.<имя> в адрес не идёт"
    assert run["f"]["str"] == "part.late,cause.sup,step.7"
    a = run["addr"]
    assert (a["sub"], a["sep"], a["n"], a["f"], a["o"], a["s"]) == ("week", True, "w:W3", "part.late", "9001", "5")
    assert run["peopleN"] == "", "ключ с именем человека не пишется в адрес"


def test_заголовок_вывод(run):
    t = run["title"]
    assert t["txt"] == "8 заказов: срок клиента под угрозой" and t["code"] == "W3"
    assert run["peopleTitle"].startswith("1 заказ: Менеджер 1"), "имя не опускается в строчные"
    assert run["colonTitle"] == "Ждём: КАМ (шаги 2, 3, 10) — 1 заказ"


def test_дни_словами_и_запрещённые_слова(run):
    assert run["days"] == ["просрочен на 7 дн.", "до срока 7 дн.", "прогноз позже срока на 7 дн.", "7 дн. после плана производства",
                           "7 раб. дн. при норме 9", "7 раб. дн. от доставки", "не оплачено 7 дн. после срока", "7 раб. дн. без заказа"]
    assert run["days0"] == "до срока 0 дн. — сегодня"
    h = re.sub(r'<td hidden data-xlv="[^"]*"></td>|data-xlv="[^"]*"', "", run["html"])
    text = re.sub(r"<[^>]+>", " ", h)
    for bad in ("р.д.", "хозяин", "в копии", "заказ(ов)", "сдел.", "Σ"):
        assert bad not in text, bad
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text), "ISO-дата на экране"
    assert not re.search(r"\((КАМ|ОРК|рук\. ОРК)\)\s*\(", text), "роль дважды"
    assert not re.search(r"\b[sS]\dOver\b|noUtd|noPlanDates", text), "имя кода на экране"
    assert "<abbr" in h, "сокращения — с расшифровкой"
    assert "ещё: срок клиента прошёл" in text, "второй пункт заказа — словами"
