"""«Реализация» R4, пакет WP-A «Каркас, словарь, адрес» (спецификация R4, 2.2–2.9, Д-11а).

Данные (Д-11а): у пункта очереди — whoId и escalateToId, id Битрикса тех же людей, что who и
escalateTo («Смотреть как» отбирает по id: имя в адрес не пишется). Две проверки разными
способами (CLAUDE.md, 26.09.2026): (1) id пункта через справочник заглушки даёт ровно то имя,
что в who; (2) одно имя — один id по всей очереди, подписи ролей и «не назначен» — без id.
Счёт пунктов и их хозяева не меняются (ork.owner — та же таблица полей ROLE_KEYS).

Вёрстка (node:vm, без браузера): адрес _rzRoute (разбор, запись, снятие нижних уровней), род
дней _rzDays, словарь _rzGloss/_rzAbbr, число-кнопка _rzNum, свежесть _rzStale, отбор _rzKamOk.
Линтер scripts/rz_lint.py — чистые функции. Корпус придуман (CLAUDE.md, правило 18).
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

import ork  # noqa: E402
from scripts import rz_lint  # noqa: E402

TPL = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- Д-11а: whoId, escalateToId
@pytest.fixture(scope="module")
def ct():
    from tests import fixture
    return fixture.build_contracts()


@pytest.fixture(scope="module")
def names():
    from tests import fixture
    st = fixture.ContractsStub()
    return lambda u: st.user_name(u)


def _queues(ct):
    return [("main", ct["life"]["queue"])] + ([("sep", ct["sep"]["life"]["queue"])] if ct.get("sep") else [])


def test_у_каждого_пункта_есть_whoId_и_escalateToId(ct):
    for _v, Q in _queues(ct):
        assert Q
        for q in Q:
            assert "whoId" in q and "escalateToId" in q, q["code"]
            assert isinstance(q["whoId"], str) and isinstance(q["escalateToId"], str)


def test_id_через_справочник_даёт_то_же_имя(ct, names):
    """Первый способ: id пункта → имя по справочнику людей заглушки = who / escalateTo."""
    n_who = n_esc = 0
    for _v, Q in _queues(ct):
        for q in Q:
            if q["whoId"]:
                n_who += 1
                assert names(q["whoId"]) == re.sub(r" \(уволен\)$", "", q["who"]), (q["code"], q["who"], q["whoId"])
            if q["escalateToId"]:
                n_esc += 1
                assert names(q["escalateToId"]) == re.sub(r" \(уволен\)$", "", q["escalateTo"]), q["code"]
    assert n_who > 20 and n_esc > 20          # корпус покрывает менеджеров ОРК, КАМ, ОСС, руководителя


def test_одно_имя_один_id_и_у_подписей_роли_id_нет(ct):
    """Второй способ: по всей очереди имя ↔ id однозначно; «не назначен», «логистика»,
    «руководитель ОРК» (подпись роли) — без id; человек с именем — всегда с id."""
    by_name: dict = {}
    labels = {"не назначен", *ork.ROLES.values()}
    for _v, Q in _queues(ct):
        for q in Q:
            for nm, i in ((q["who"], q["whoId"]), (q["escalateTo"], q["escalateToId"])):
                if not nm:
                    assert not i
                    continue
                if nm in labels:
                    assert i == "", (q["code"], nm)
                else:
                    assert i, (q["code"], nm)
                    assert by_name.setdefault(nm, i) == i, nm
    roles = {q["role"] for _v, Q in _queues(ct) for q in Q if q["whoId"]}
    assert {"orkMgr", "kam", "oss", "orkHead"} <= roles


def test_id_людей_сделки_не_уходят_в_страницу(ct):
    assert not any("_pid" in r for r in ct["rows"])


def test_role_id_берёт_поле_того_же_человека_что_и_owner():
    ppl = {"orkMgr": "", "dealOrk": "Ольга (ОРК)", "orkHead": "Рита", "kam": "Анна", "ossOrder": "", "ossDeal": "Глеб"}
    ids = {"orkMgr": "901", "dealOrk": "902", "orkHead": "903", "kam": "904", "ossOrder": "905", "ossDeal": "906"}
    # менеджер ОРК заказа не из отдела (имени нет) — берётся поле сделки, и id — тоже его
    assert ork.owner(5, ppl)["who"] == "Ольга (ОРК)" and ork.role_id("orkMgr", ppl, ids) == "902"
    assert ork.role_id("oss", ppl, ids) == "906"          # ОСС заказа пуст — ОСС сделки
    assert ork.role_id("kam", ppl, ids) == "904"
    assert ork.role_id("log", ppl, ids) == "" and ork.role_id(None, ppl, ids) == ""
    assert ork.role_id("orkHead", {**ppl, "orkHead": ""}, ids) == ""   # подпись роли — без id
    assert ork.role_id("kam", ppl, {**ids, "kam": "0"}) == ""


def test_owner_прежний_выбор_людей():
    """ROLE_KEYS — та же таблица, что была в owner: хозяин и копия не меняются."""
    p = {"orkMgr": "М", "dealOrk": "Д", "orkHead": "", "kam": "К", "ossOrder": "", "ossDeal": "О"}
    assert (ork.owner(5, p)["role"], ork.owner(5, p)["who"]) == ("orkMgr", "М")
    assert ork.owner(1, p)["who"] == "руководитель ОРК"
    assert ork.owner(None, p, debt=True) == {"role": "kam", "who": "К", "copy": "О", "copyRole": "oss"}
    assert ork.owner(5, {**p, "orkMgr": ""})["who"] == "Д"
    assert ork.owner(5, {**p, "orkMgr": "", "dealOrk": ""})["who"] == "не назначен"


# ---------------------------------------------------------------- вёрстка: помощники WP-A
A, B = "// ==== R4:WP-A — каркас «Реализации»", "// ==== /R4:WP-A (помощники) ===="
HARNESS = r"""
const vm = require('node:vm'); const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const hist = []; let H = '#contracts/week';
const location = {get hash(){return H;}};
const history = {state: null, pushState(s, t, h){hist.push(['push', h]); H = h; this.state = s;},
  replaceState(s, t, h){hist.push(['replace', h]); H = h; this.state = s;}};
const box = {location, history, window: {__CONTRACTS__: inp.ct}, document: {}, console, JSON, Math, Object, Array,
  String, Number, Set, Map, RegExp, Date, encodeURIComponent, decodeURIComponent, CustomEvent: function(){}};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + inp.subs + '\n' + inp.code
  + '\nthis.R=_rzRoute; this.D=_rzDays; this.G=_rzGloss; this.Ab=_rzAbbr; this.N=_rzNum; this.S=_rzStale;'
  + ' this.K=_rzKam; this.KO=_rzKamOk; this.GL=CT_GLOSS; this.RG=_RZ_GLOSS; this.ST=_rzState; this.C=_rzCount;'
  + ' this.T=_rzTile; this.BU=_rzBullet; this.BA=_rzBars; this.CB=_rzCellBar; this.GH=_rzGlossHtml;', box);
const out = {};
const R = box.R;
out.parse = inp.parse.map(h => R.parse(h));
out.href = inp.href.map(s => R.href(s));
// уровни: список → заказ → шаг; смена списка снимает заказ и шаг; подвкладка — всё
H = '#contracts/week'; hist.length = 0;
const s1 = R.set({n: 'w:W3'}, {push: true});
const s2 = R.set({o: '7001', s: '5'}, {push: true});
const s3 = R.set({f: 'tag.late'});
const s4 = R.set({n: 'w:W2'}, {push: true});
const s5 = R.set({k: '31'});
const s6 = R.set({sub: 'main', sep: true}, {push: true});
out.sets = [s1, s2, s3, s4, s5, s6].map(s => R.href(s)); out.hist = hist.slice(); out.depth = history.state.rzDepth;
out.days = inp.days.map(([c, n, o]) => box.D(c, n, o));
out.daysOld = box.D('s3Over', 12, true);
box.G.reset();
out.gloss = [box.G('ОРК'), box.G('ОРК'), box.G('КАМ', {first: true}), box.G('ОРК', {first: true, gen: true}), box.Ab('ОСС'), box.Ab('нет-такого')];
out.num = [box.N(0, {of: 'w:x'}), box.N(5, {of: 'w:x', title: 'Т', rank: '1'}), box.N(null, {of: 'w:x'}), box.N(1234, {of: 'w:x'}), box.N(3, {final: true})];
out.stale = [box.S('2026-10-19', '2026-10-30T09:00:00'), box.S('2026-10-29', '2026-10-30T09:00:00'),
  box.S('23.10.2026', '2026-10-26T09:00:00'), box.S('2026-10-23', '2026-10-27T09:00:00'), box.S('', '2026-10-27')];
box.K.v = '';
out.ko = [box.KO({whoId: '1'})];
box.K.v = '31';
out.ko.push(box.KO({whoId: '31'}), box.KO({kamId: '31'}), box.KO({escalateToId: '31'}), box.KO({whoId: '2', kamId: '3'}), box.KO(null));
out.glossKeys = Object.keys(box.GL); out.rg = box.RG;
out.states = ['empty-filter', 'empty-ok', 'nodata', 'prelim', 'hidden', 'stale', 'noright', 'notfound', 'error', 'loading']
  .map(k => box.ST(k, {filters: 'шаг 5', what: 'срок шага 6', field: 'Дата подтверждения', k: 9, n: 9, who: 'ОСС', min: 30, why: 'один человек',
    date: '19.10.2026', days: 11, block: 'Чат и договор', bx: 'https://x/', back: 1}));
out.comp = [box.C({label: 'Клиент не оплатил в срок', v: 6, of: 'w:unpaid', tone: 'alarm'}), box.C({label: 'Ждём УПД', v: 0, of: 'w:x'}),
  box.T({title: 'Срок клиента сорван', code: 'W3', v: 19, of: 'w:W3', rank: '1', tone: 'alarm', sub: '16 сделок'}),
  box.BU({k: 7, n: 9, target: 0.95, of: 'm:x'}), box.BU({k: 1, n: 2, nMin: 5}), box.BA([{label: 'А', v: 2}, {label: 'Б', v: 5, of: 'q:1'}]),
  box.CB(8, {max: 10, red: 8}), box.GH()];
process.stdout.write(JSON.stringify(out));
"""


def _line(prefix):
    i = TPL.index(prefix)
    return TPL[i:TPL.index("\n", i)]


@pytest.fixture(scope="module")
def js(ct):
    if shutil.which("node") is None:
        pytest.skip("нет node")
    code = TPL[TPL.index(A):TPL.index(B)]
    inp = {"code": code, "esc": _line("const _esc="), "ctN": _line("const _ctN="), "subs": _line("const CT_SUBS="),
           "ct": {"life": ct["life"], "sep": {"life": (ct.get("sep") or {}).get("life") or {}}},
           "parse": ["#contracts/week", "#contracts/sep", "#contracts/main/sep?d=950&o=7120",
                     "#contracts/week?n=w%3AW3&o=7001&s=5", "#contracts/month?n=m%3A9.S1&o=7003&s=1",
                     "#contracts/week?n=w%3AW3&f=tag.late&k=31", "#contracts/week?o=Иван&d=950x&s=14&k=a1",
                     "#contracts/nope", "#reps/kam", "#contracts/how/"],
           "href": [{"sub": "week", "n": "w:W3", "f": "part.late,cause.our", "o": "7001", "s": "5"},
                    {"sub": "main", "sep": True, "d": "973"}, {"sub": "quarter"}],
           "days": [["late", 35, {}], ["risk", 0, {}], ["risk", 4, {}], ["forecast", 20, {}], ["planAfterDl", 3, {}],
                    ["prodDev", 109, {}], ["s3Over", 46, {}], ["s1Over", 2, {}], ["s2Over", 5, {"norm": 2}],
                    ["noUtd", 72, {}], ["unpaid", 30, {}], ["noOrder", 3, {}], ["late", 1234, {"html": True}],
                    ["late", None, {}]]}
    p = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False), capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout)


def test_адрес_разбор(js):
    P = js["parse"]
    assert P[0] == {"sub": "week", "sep": False, "n": "", "f": "", "k": "", "d": "", "o": "", "s": ""}
    assert (P[1]["sub"], P[1]["sep"]) == ("main", True)              # прежний адрес направления
    assert (P[2]["sub"], P[2]["sep"], P[2]["d"], P[2]["o"]) == ("main", True, "950", "7120")
    assert (P[3]["n"], P[3]["o"], P[3]["s"]) == ("w:W3", "7001", "5")
    assert (P[4]["sub"], P[4]["n"], P[4]["s"]) == ("month", "m:9.S1", "1")
    assert (P[5]["f"], P[5]["k"]) == ("tag.late", "31")
    # имена и мусор в адресе не проходят: d, o, k — только цифры, s — 1–13
    assert (P[6]["o"], P[6]["d"], P[6]["s"], P[6]["k"]) == ("", "", "", "")
    assert P[7]["sub"] == "week" and P[8]["sub"] == "week" and P[9]["sub"] == "how"


def test_адрес_запись(js):
    H = js["href"]
    assert H[0] == "#contracts/week?n=w%3AW3&f=part.late,cause.our&o=7001&s=5"
    assert H[1] == "#contracts/main/sep?d=973"
    assert H[2] == "#contracts/quarter"
    assert not any(re.search(r"[А-Яа-яЁё]", h) for h in H)          # имён в адресе нет


def test_адрес_уровни_и_история(js):
    S = js["sets"]
    assert S[0] == "#contracts/week?n=w%3AW3"
    assert S[1] == "#contracts/week?n=w%3AW3&o=7001&s=5"
    assert S[2] == "#contracts/week?n=w%3AW3&f=tag.late&o=7001&s=5"   # отбор — без смены уровня
    assert S[3] == "#contracts/week?n=w%3AW2"                           # другой список: заказ и шаг сняты
    assert S[4] == "#contracts/week?n=w%3AW2&k=31"
    assert S[5] == "#contracts/main/sep?k=31"                           # подвкладка: уровни сняты, «как» остаётся
    kinds = [k for k, _ in js["hist"]]
    assert kinds == ["push", "push", "replace", "push", "replace", "push"]
    assert js["depth"] == 4                                               # «Назад» × 4 — к началу


def test_род_дней(js):
    D = js["days"]
    nb = " "
    assert D[0] == f"просрочен на 35{nb}дн."
    assert D[1] == f"до срока 0{nb}дн. — сегодня" and D[2] == f"до срока 4{nb}дн."
    assert D[3] == D[4].replace("3", "20") == f"прогноз позже срока на 20{nb}дн."
    assert D[5] == f"109{nb}дн. после плана производства"
    assert D[6] == f"46{nb}раб.{nb}дн. при норме 9" and D[7] == f"2{nb}раб.{nb}дн. при норме 1"
    assert D[8] == f"5{nb}раб.{nb}дн. при норме 2"
    assert D[9] == f"72{nb}раб.{nb}дн. от доставки"
    assert D[10] == f"не оплачено 30{nb}дн. после срока"
    assert D[11] == f"3{nb}раб.{nb}дн. без заказа"
    assert D[12] == f"просрочен на <b>1 234</b>{nb}дн."
    assert D[13] == ""
    assert "р.д." not in js["daysOld"] and "раб." in js["daysOld"]       # прежний вызов — без «р.д.»


def test_словарь_первое_появление_словами_дальше_abbr(js):
    G = js["gloss"]
    assert G[0].startswith("отдел реализации контрактов (<abbr") and 'data-tip="' in G[0]
    assert G[1].startswith("<abbr class=\"rzabbr\"") and ">ОРК</abbr>" in G[1]
    assert G[2].startswith("КАМ (менеджер клиента)".replace("КАМ", "<abbr")) or "менеджер клиента" in G[2]
    assert G[3].startswith("отдела реализации контрактов (<abbr")
    assert 'tabindex="0"' in G[4] and "сопровождение сделки" in G[4]
    assert G[5] == "нет-такого"
    # один словарь: все сокращения линтера — в CT_GLOSS; прежняя строка сокращений — без «р.д.»
    assert set(rz_lint.ABBR) <= set(js["glossKeys"])
    assert "р.д." not in js["rg"] and "раб. дн." in js["rg"]
    assert {"cause.our", "cause.sup", "cause.log", "cause.kam", "cause.oss", "cause.nodata"} <= set(js["glossKeys"])


def test_число_кнопка(js):
    z, five, none, big, fin = js["num"]
    assert "<button" not in z                                   # за нулём списка нет — не кнопка
    assert five.startswith('<button type="button" class="rzn"') and 'data-of="w:x"' in five and 'data-od="w:x"' in five
    assert 'data-kpi-rank="1"' in five and ">5</button>" in five
    assert "нет данных" in none and "<button" not in none
    assert "1 234" in big
    assert 'class="rzfinal"' in fin


def test_свежесть_по_рабочим_дням(js):
    s = js["stale"]
    assert s[0]["stale"] is True and s[0]["days"] == 11 and s[0]["date"] == "19.10.2026"
    assert s[1]["stale"] is False                               # вчера — свежие
    assert s[2]["stale"] is False and s[2]["wd"] == 1           # пятница → понедельник: 1 раб. день
    assert s[3]["stale"] is True and s[3]["wd"] == 2
    assert s[4]["stale"] is False and s[4]["date"] == ""


def test_смотреть_как(js):
    assert js["ko"] == [True, True, True, True, False, False]


def test_состояния_и_компоненты(js):
    st = dict(zip(['empty-filter', 'empty-ok', 'nodata', 'prelim', 'hidden', 'stale', 'noright', 'notfound', 'error',
                   'loading'], js["states"]))
    assert "Под отбор «шаг 5» заказов нет." in st["empty-filter"] and "Снять отбор" in st["empty-filter"]
    assert "Нет данных: срок шага 6. Почему: «Дата подтверждения» пустое у 9 из 9. Вносит: ОСС." in st["nodata"]
    assert "Мало данных: 9 заказов в расчёте, уверенный вывод — от 30." in st["prelim"]
    assert "Данные на 19.10.2026 — 11 дн. назад. Сборка не обновлялась." in st["stale"] and "Прогоны ↗" in st["stale"]
    assert "Чат и договор видят руководители ОРК и владелец." in st["noright"]
    assert "нет в данных на 19.10.2026: закрыт, удалён или в другом направлении." in st["notfound"]
    assert "Найти в Битриксе ↗" in st["notfound"] and "К списку" in st["notfound"]
    assert "Остальное — по открытой сборке." in st["error"] and "Повторить" in st["error"]
    cnt, cnt0, tile, bul, bulfew, bars, cb, gh = js["comp"]
    assert cnt.startswith('<button type="button" class="rzn rzcount rz-alarm"') and "›" in cnt
    assert "<button" not in cnt0
    assert 'class="rzn rztile-n"' in tile and 'data-kpi-rank="1"' in tile and "rzcode" in tile and "W3" in tile
    assert "78 % · " in bul and " из 9" in bul and 'left:95%' in bul
    assert "rzbul-few" in bulfew and "мало данных: 2 заказа в расчёте" in bulfew
    assert bars.index(">Б<") < bars.index(">А<")                # худший (больший) сверху
    assert "width:64px" in cb and "width:100%" in cb            # 8 из 10 — 64 из 80 px, всё дольше нормы
    assert "Сокращения" in gh and "Коды показателей" in gh and "Термины" in gh
    allhtml = "".join(js["states"] + js["comp"])
    assert "linear-gradient" not in allhtml


# ---------------------------------------------------------------- линтер
def test_линтер_запрещённые_слова():
    t = ("Хозяин пункта · в копии КАМ · живых заказов вида 51 · гигиена карточек · нет артефактов · вторая опора · "
         "ворота доверия · покрытие · зонд v60 · Σ продажи · 46 / 9 р.д. · 2 заказ(ов) · 3 сдел. · Ан. §2.1 · Рег. 9.2 · "
         "2026-04-02 · s3Over · noUtd · S3 без шага · 13 из 2 раб. дн. · Пункты очереди")
    rules = {x["rule"] for x in rz_lint.forbidden(t)}
    assert rules == {n for n, _r, _h in rz_lint.FORBIDDEN}
    ok = ("Кто делает · в курсе: КАМ · 51 заказ в работе · ошибки в карточках · S3 (шаг 5) · покрытие 75 % · "
          "02.04.2026 · раб. дн. · видам · опорный · 13 раб. дн. при норме 2 · 3 из 12 заказов")
    assert rz_lint.forbidden(ok) == []


def test_линтер_сокращения_и_коды():
    seg = [{"t": "51 заказ в работе у отдела реализации контрактов (", "abbr": False},
           {"t": "ОРК", "abbr": True}, {"t": ")", "abbr": False},
           {"t": "Шаги ОРК дольше нормы · ", "abbr": False}, {"t": "W2", "abbr": False, "code": True},
           {"t": "в курсе: КАМ Анна", "abbr": False}, {"t": "КАМ", "abbr": True}, {"t": "Q1 без хвоста", "abbr": False}]
    a = rz_lint.abbr_check(seg)
    assert [x["abbr"] for x in a["first"]] == ["КАМ"]          # КАМ впервые — без «менеджер клиента»
    assert [x["abbr"] for x in a["later"]] == ["ОРК"]          # второе «ОРК» — не в <abbr>
    assert [x["code"] for x in rz_lint.code_check(seg)] == ["Q1"]
    r = rz_lint.lint_segments(seg)
    assert r["total"] == 3
