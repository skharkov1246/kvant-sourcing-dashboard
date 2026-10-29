"""Заказы за числами «Реализации» (решение владельца 29.10.2026, «Неделя», п. 4–5): строки
живых заказов недели, ключи каждого числа, «Пульс ОРК» по людям, заказы за числами «Квартала»
и «Месяца» (orkview._week_drill, _quarter_drill, _month_drill).

Главное свойство: у каждого числа список ровно из тех заказов, что за ним стоят, — сколько
ключей, столько и в числе; ключ находится среди строк своей части данных. Поставщик — только там,
где он уже есть у вида (CONTRACTS), без права на вкладку его в странице нет. Корпус придуман
(CLAUDE.md, правило 18): fixture.ContractsStub через настоящий contracts.compute.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import ork  # noqa: E402
import orkview  # noqa: E402
from tests import fixture  # noqa: E402

# коды закрытой части (dealctx, право «договоры и деньги») — в строки CONTRACTS не идут
DEALCTX_CODES = {"chatReply", "chatRisk", "chatDue", "contractRisk", "contractMissing", "econGap",
                 "contractUnreadable"}
MONEY_KEYS = {"buy", "buyEur", "sale", "saleEur", "eur", "opportunity", "margin", "revEur", "buyRaw"}


@pytest.fixture(scope="module")
def built():
    r = fixture.build_realization()
    return json.loads(json.dumps(r, ensure_ascii=False, default=str))


def _views(ct: dict) -> list[tuple[str, dict]]:
    return [("main", ct), ("sep", ct["sep"])]


def _n(v) -> int:
    return len(v["k"]) if isinstance(v, dict) else len(v)


def _keys(v) -> list[str]:
    return v["k"] if isinstance(v, dict) else v


def _rows(part: dict, key: str = "orders") -> list[dict]:
    """Строки части данных с подписями (стадия, поставщик, люди — номером в словаре strs)."""
    return orkview.unpack(part.get(key) or [], part.get("strs") or [])


# ── строки «Недели» ───────────────────────────────────────────────────────────────────────
def test_строки_заказов_та_же_совокупность_что_live(built):
    """week.orders — живые заказы ведомости (U + C12): столько же, сколько week.live, ключи
    уникальны; номер — как портал называет заказ («950/1»); шаг — тот же, что в ведомости."""
    for name, v in _views(built["contracts"]):
        w = v["ork"]["week"]
        rows = _rows(w)
        assert len(rows) == w["live"] == len({r["k"] for r in rows}), name
        # номер — как у пункта очереди (ork.order_label): «950/1» или «id <id>» без номера
        assert all(re.fullmatch(r"\d{1,4}/\d{1,3}|id \d+", r["no"]) for r in rows), name
        steps = Counter(r["step"] for r in rows)
        for s in w["ledger"]:
            assert steps.get(s["no"], 0) == s["now"], (name, s["no"])
        assert steps.get(None, 0) == w["unknown"]
        # строка — то, что нужно списку: сделка, стадия, шаг, хозяин, менеджер ОРК
        for r in rows:
            assert {"k", "no", "deal", "seq", "stage", "step", "mgr"} <= set(r), r
            assert isinstance(r["stage"], str) and isinstance(r["mgr"], str) and r["stage"] and r["mgr"]
            assert not (set(r) & MONEY_KEYS), "денег в строках нет"
            assert "mk" not in r, "вид менеджера — у строки «Пульса», не в каждой строке"
        # подписи — номером в словаре: строка в payload без повторяющихся названий
        assert len(w["strs"]) == len(set(w["strs"])) and w["strs"][0] == ""
        assert all(isinstance(r[f], int) and r[f] >= 1 for r in w["orders"] for f in orkview.PACKED if f in r), name
    main = _rows(built["contracts"]["ork"]["week"])
    assert {"950/1", "930/2"} <= {r["no"] for r in main}, "номер заказа — из названия «950/1. …»"
    assert any(r.get("c12") for r in main) and any(r.get("p80") for r in main)
    assert any(r.get("over") for r in main) and any(r.get("wu") for r in main)


def test_коды_очереди_и_прогноз_у_строки_как_в_очереди(built):
    """Пункты очереди на заказе — ровно коды life.queue этого заказа; прогноз — тот же, что у
    пункта очереди (lifecycle.forecast, одно мерило); кодов закрытой части в строках нет."""
    ct = built["contracts"]
    for name, v in _views(ct):
        rows = {r["k"]: r for r in _rows(v["ork"]["week"])}
        q = v["life"]["queue"]
        by: dict[str, list] = {}
        for x in q:
            if x.get("order"):
                by.setdefault(str(x["order"]), []).append(x["code"])
        for k, r in rows.items():
            assert r.get("q", []) == by.get(k, []), (name, k)
            assert not (set(r.get("q", [])) & DEALCTX_CODES)
        checked = 0
        for x in q:
            r = rows.get(str(x.get("order") or ""))
            if r and x.get("fc"):
                assert dt.date.fromisoformat(r["fc"]).strftime("%d.%m.%Y") == x["fc"], (name, x["order"])
                checked += 1
        assert checked


def test_день_отсчёта_у_строки_как_у_пункта_очереди(built):
    """Строка списка говорит, от какого дня считаны р.д. шага, — «с 14.08 (вход в шаг)», «с 13.09
    (доставка)», — тем же текстом, что пункт очереди о сроке шага (s1Over…s6Over, noUtd: ref,
    lifecycle.order_ref): ревизия сведения 29.10.2026 — в списке за числом «Р.д. на шаге / норма»
    были без основания, которое очередь выпуска 1 показывает. У строки без посчитанных р.д.
    основания нет."""
    term = set(ork.TERM_CODE.values())
    checked = 0
    for name, v in _views(built["contracts"]):
        rows = {r["k"]: r for r in _rows(v["ork"]["week"])}
        for r in rows.values():
            if r.get("wd") is not None:
                assert re.fullmatch(r"с \d\d\.\d\d(\.\d{4})? \((вход в шаг|назначение|доставка|создан заказ|"
                                    r"вход в реализацию)\)", r.get("since", "")), (name, r["k"], r.get("since"))
            else:
                assert "since" not in r, (name, r["k"])
        for q in v["life"]["queue"]:
            r = rows.get(str(q.get("order") or ""))
            if r and q["code"] in term:
                assert r["since"] == q["ref"], (name, q["code"], q["order"])
                checked += 1
    assert checked >= 5, "корпус: пункты о сроке шага на живых заказах недели"


def test_каждое_число_недели_и_его_заказы(built):
    """Число ↔ ключи: плитки W1–W3, W5, ведомость (сейчас, сверх нормы, ждём УПД, срок
    неизвестен, не в счёте, нет артефактов, закрытые сделки, дольше P80, к закрытию), ждут
    смежников, дольше P80, сделки без заказа, «ждём УПД», сверх нормы по менеджерам, гигиена,
    строка доверия. Ключ заказа — среди week.orders, ключ сделки — среди week.deals."""
    for name, v in _views(built["contracts"]):
        w = v["ork"]["week"]
        D, T = w["drill"], w["tiles"]
        okeys = {r["k"] for r in w["orders"]} | {r["k"] for r in w["hist"]}
        dkeys = {r["k"] for r in w["deals"]}

        def known(lst):
            for k in _keys(lst):
                assert k in (dkeys if k.startswith("d:") else okeys), (name, k)
        for code in ("W1", "W2", "W3", "W5"):
            if T[code].get("state") in ("show", "prelim"):
                assert _n(D[code]) == T[code]["value"], (name, code)
            known(D[code])
        assert _n(D["W1"]) == T["W1"]["orders"] + T["W1"]["noOrder"]
        assert Counter(D["W3"]["t"]) == Counter({c: n for c, n in T["W3"]["by"].items() if n})
        for s in w["ledger"]:
            e = D["step"][str(s["no"])]
            assert _n(e["now"]) == s["now"], (name, s["no"])
            for key, field in (("over", "over"), ("wait", "waitUtd"), ("unk", "overUnknown"),
                               ("nc", "notCounted"), ("art", "artMiss")):
                if s.get(field) is not None:
                    assert _n(e[key]) == s[field], (name, s["no"], key)
            if s["no"] == 7 and s.get("longP80") is not None:
                assert _n(e["p80"]) == s["longP80"]
            for lst in e.values():
                known(lst)
        assert _n(D["step"]["12"]["closed"]) == w["closed12"]
        assert _n(D["step"]["13"]["close"]) == w["dealClose"]
        # сделка без заказа (не закрыта) и «к закрытию» («Оплата получена» ⇒ закрыта) — разные
        # сделки: строка сделки одна на вид, «к закрытию» не показывается «сделкой без заказа»
        assert not set(D["noOrder"] + D["noOrderOver"]) & set(D["step"]["13"]["close"]), name
        kinds = {r["k"]: r["kind"] for r in w["deals"]}
        assert {kinds[k] for k in D["step"]["13"]["close"]} <= {"dealClose"}
        assert {kinds[k] for k in D["noOrder"]} <= {"noOrder"}
        # «Моя приёмка»: «в срок за 20 р.д.: k из n» — n ключей, «в норме» у k из них
        rec = T["W1"]["recent"]
        if rec.get("state") in ("show", "prelim"):
            assert _n(D["W1recent"]) == rec["n"], name
            assert sum(t.startswith("в норме") for t in D["W1recent"]["t"]) == rec["k"]
            known(D["W1recent"])
        else:
            assert "W1recent" not in D, "скрытое «в срок за 20 р.д.» — без списка"
        # строки законченных заказов приёмки — без менеджера ОРК (поимённо — только «Пульс»)
        assert not any("mgr" in r for r in w["hist"])
        assert {r: _n(D["adj"][r]) for r in ork.ADJACENT} == w["adjacent"], name
        lp = w["longP80"]
        assert _n(D["p80"]) == (lp["k"] if lp["state"] in ("show", "prelim") else 0)
        assert _n(D["noOrder"]) == w["dealsNoOrder"] and _n(D["noOrderOver"]) == w["dealsNoOrderOver"]
        assert _n(D["waitUtd"]) == w["waitUtd"]["n"]
        assert _n(D["live"]) == w["live"] and _n(D["unk"]) == w["unknown"]
        assert _n(D["mapped"]) == v["ork"]["trust"]["mapped"]
        for m in w["overByWho"]:
            assert _n(D["who"][m["who"]]["over"]) == m["n"] and _n(D["who"][m["who"]]["wait"]) == m["waitUtd"]
            known(D["who"][m["who"]]["over"] + D["who"][m["who"]]["wait"])
        # итоги «Сверх нормы по менеджерам» открывают W2 и «ждём УПД»: суммы строк — те же числа
        assert sum(m["n"] for m in w["overByWho"]) == (T["W2"]["value"] or 0) == _n(D["W2"]), name
        assert sum(m["waitUtd"] for m in w["overByWho"]) == w["waitUtd"]["n"] == _n(D["waitUtd"]), name
        H, hy = D["hyg"], w["hygiene"]
        assert _n(H["all"]) == hy["n"] and _n(H["excl"]) == hy["excluded"]
        assert _n(H["noMgr"]) == hy["by"].get("noMgr", 0)
        for k in ("behind", "abandoned", "old", "unknown"):
            assert _n(H["x" + k]) == hy["byExcl"].get(k, 0), (name, k)
        known(H["all"])
    # пример корпуса: у «Ждут смежников» логистики 10 заказов — все на шагах 9 и 11
    w = built["contracts"]["ork"]["week"]
    rows = {r["k"]: r for r in _rows(w)}
    assert w["adjacent"]["log"] > 0
    assert {rows[k]["step"] for k in w["drill"]["adj"]["log"]} <= {9, 11}
    assert all(rows[k].get("waits") == "log" for k in w["drill"]["adj"]["log"])


def test_пульс_орк_инварианты(built):
    """«Пульс ОРК»: Σ строк = живые заказы; по шагам — ведомость; Σ «сверх нормы» = W2;
    Σ «ждём УПД» = week.waitUtd.n; доля — от всех живых; сделки и заказчики — по строкам
    человека; «ответственный вне ОРК» и «не назначен» — последними, люди — по алфавиту."""
    for name, v in _views(built["contracts"]):
        w = v["ork"]["week"]
        P = w["pulse"]
        rows = _rows(w)
        assert sum(p["n"] for p in P["rows"]) == w["live"] == P["live"], name
        by = Counter()
        for p in P["rows"]:
            by.update(p["byStep"])
            # заказы строки — предикат страницы (_odResolve): строки недели с mgr == who; сверх
            # нормы — и over, ждём УПД — и wu; ключей в данных за строкой нет
            assert not ({"keys", "overKeys", "waitKeys"} & set(p))
            mine = [r for r in rows if r["mgr"] == p["who"]]
            assert len(mine) == p["n"], (name, p["who"])
            assert sum(1 for r in mine if r.get("over")) == p["over"]
            assert sum(1 for r in mine if r.get("wu")) == p["wait"]
            assert Counter(str(r["step"]) if r["step"] is not None else "?" for r in mine) == Counter(p["byStep"])
            assert p["pct"] == round(p["n"] * 100 / w["live"], 1)
            assert p["deals"] == len({r["deal"] for r in mine})
        for s in w["ledger"]:
            assert by.get(str(s["no"]), 0) == s["now"], (name, s["no"])
        assert by.get("?", 0) == w["unknown"]
        assert P["over"] == sum(p["over"] for p in P["rows"]) == (w["tiles"]["W2"]["value"] or 0)
        assert P["wait"] == w["waitUtd"]["n"]
        kinds = [p["kind"] for p in P["rows"]]
        assert kinds == sorted(kinds, key=orkview.PULSE_ORDER.get), name
        named = [p["who"] for p in P["rows"] if p["kind"] == "mgr"]
        assert named == sorted(named, key=str.casefold)
    P = built["contracts"]["ork"]["week"]["pulse"]
    who = {p["who"]: p for p in P["rows"]}
    # корпус: менеджеры ОРК, «ответственный вне ОРК» (ответственный — снабжение) и «не назначен»
    assert {"Олег (ОРК)", "Пётр (ОРК)", orkview.PULSE_OUT, orkview.PULSE_NONE} <= set(who)
    assert who[orkview.PULSE_OUT]["kind"] == "out" and who[orkview.PULSE_NONE]["kind"] == "none"
    assert not any(p["gone"] for p in P["rows"])


def test_пульс_ушедший_менеджер_помечен():
    """Ушедший сотрудник с живыми заказами — своей строкой с пометкой (gone), а не «не назначен»."""
    roster = copy.deepcopy(fixture.CT_ROSTER)
    roster["31"]["active"] = False
    ct = json.loads(json.dumps(fixture.build_contracts(people=roster), ensure_ascii=False, default=str))
    P = ct["ork"]["week"]["pulse"]
    gone = [p for p in P["rows"] if p["gone"]]
    assert gone and gone[0]["who"].startswith("Олег (ОРК)") and gone[0]["who"].endswith("(уволен)")
    assert gone[0]["n"] > 0 and gone[0]["kind"] == "mgr"
    assert sum(p["n"] for p in P["rows"]) == ct["ork"]["week"]["live"]


def test_pulse_who_правило_хозяина():
    """Менеджер ОРК заказа — ответственный из ОРК, иначе поле сделки; иначе — «вне ОРК» или
    «не назначен»."""
    assert orkview.pulse_who({"orkMgr": "А"}, {"orkResp": "Б"}) == ("А", "mgr")
    assert orkview.pulse_who({"orkMgr": "", "resp": "В"}, {"orkResp": "Б"}) == ("Б", "mgr")
    assert orkview.pulse_who({"orkMgr": "", "resp": "В"}, {"orkResp": ""}) == (orkview.PULSE_OUT, "out")
    assert orkview.pulse_who({"orkMgr": "", "resp": "—"}, None) == (orkview.PULSE_NONE, "none")
    assert orkview.order_no({"id": 5, "title": "486/1. Узел"}) == "486/1"
    assert orkview.order_no({"id": 5, "title": "Узел без номера"}) == "id 5"
    # тот же номер, что у пункта очереди: дата в начале названия — не номер заказа
    assert orkview.order_no({"id": 7, "title": "15/10.2025 Узел"}) == "id 7"
    assert orkview.order_no({"id": 8, "title": "959/1 Узел", "lbl": "959/1"}) == "959/1"


# ── «Квартал» и «Месяц» ──────────────────────────────────────────────────────────────────
def test_квартал_заказы_за_q1_и_q3(built):
    """Q1 по кварталу и поставщику, Q3: ключей — n показателя (в срок — k); у скрытой доли
    «в срок» (строка поставщика «не показываем») списка с подписями нет, но видимое число
    готовностей открывает n ключей без подписей «в срок / позже» (nDk); ключ — среди
    quarter.orders."""
    q = built["contracts"]["ork"]["quarter"]
    D, okeys = q["drill"], {r["k"] for r in q["orders"]}
    seen = hidden_n = 0
    for i, Q in enumerate(q["quarters"]):
        for st in [Q["Q1"]] + Q["suppliers"]:
            if st.get("state") in ("show", "prelim"):
                d = D[st["dk"].split(":", 1)[1]]
                assert len(d["k"]) == st["n"], st.get("sup")
                assert sum(t.startswith("в срок") for t in d["t"]) == st["k"]
                assert set(d["k"]) <= okeys
                assert "nDk" not in st
                seen += 1
            else:
                assert "dk" not in st, "скрытая строка — без списка с подписями"
                if st in Q["suppliers"] and st.get("n"):
                    d = D[st["nDk"].split(":", 1)[1]]
                    assert len(d["k"]) == st["n"] and d["t"] is None, st.get("sup")
                    assert set(d["k"]) <= okeys
                    hidden_n += 1
                else:
                    assert "nDk" not in st
    assert seen > 2
    assert hidden_n, "корпус: строка поставщика со скрытой долей и видимым числом готовностей"
    Q3 = q["Q3"]
    assert len(D["Q3"]["k"]) == Q3["n"] and set(D["Q3"]["k"]) <= okeys
    assert sum(t == "первичная оплата 100 %" for t in D["Q3"]["t"]) == Q3["k"]
    assert not any(set(r) & MONEY_KEYS for r in q["orders"])


def test_квартал_без_менеджеров_орк(built):
    """«Квартал» видят все с вкладкой (CONTRACTS), а поимённо — только «Месяц» (право ork_heads)
    и «Пульс ОРК» живых заказов: в строках «Квартала» менеджера нет, и ни одно имя сотрудника ОРК
    корпуса в «Квартал» не попадает — в том числе у закрытого заказа ОРК-эпохи с ответственным из
    ОРК (иначе список Q1 дал бы поимённый счёт «в срок / позже»)."""
    names = [p["name"] for p in fixture.CT_ROSTER.values()]
    for name, v in _views(built["contracts"]):
        q = v["ork"]["quarter"]
        if q is None:                       # вид направления берёт «Квартал» основного (quarterFrom)
            continue
        assert not any("mgr" in r for r in _rows(q)), name
        blob = json.dumps(q, ensure_ascii=False)
        assert not [n for n in names if n in blob], name
    # корпус: в списках Q1 есть закрытые заказы — и у них менеджера нет
    q = built["contracts"]["ork"]["quarter"]
    assert any(r.get("closed") for r in q["orders"])
    # в «Месяце» (под правом) менеджер у строк есть
    assert any(r.get("mgr") for r in _rows(built["ork_month"]["main"]["month"]))


def test_строки_частей_словарём_и_обратно():
    """_pack / unpack: подписи — номером в словаре, развёртка даёт исходные строки."""
    rows = [{"k": "1", "stage": "Заказ создан", "mgr": "А (ОРК)", "own": "А (ОРК)", "step": 1},
            {"k": "d:2", "stage": "Оплата получена", "mgr": "не назначен", "kind": "noOrder", "step": 13}]
    src = copy.deepcopy(rows)
    strs = orkview._pack(rows)
    assert strs == ["", "Заказ создан", "А (ОРК)", "Оплата получена", "не назначен"]
    assert rows[0]["own"] == rows[0]["mgr"] == 2 and rows[1]["stage"] == 3
    assert all(r[f] >= 1 for r in rows for f in orkview.PACKED if f in r), "номер 0 — не подпись"
    assert orkview.unpack(rows, strs) == src


def test_оплата_получена_значит_закрыта():
    """Списки «сделок без заказа» и «к закрытию» не пересекаются потому, что «Оплата получена»
    — закрытая сделка (contracts: closed = успех, провал или paid), а сделка без заказа — открытая:
    это допущение строки сделки (одна на вид) держится на корпусе с оплатой без заказов."""
    stub = fixture.ContractsStub()
    stub.orders = [o for o in stub.orders if str(o.get("parentId2")) != "6030"]    # 6030 — «Оплата получена»
    ct = json.loads(json.dumps(fixture.build_contracts(stub=stub), ensure_ascii=False, default=str))
    assert all(r.get("closed") for r in ct["rows"] if r.get("paidStage"))
    D = ct["ork"]["week"]["drill"]
    assert "d:6030" in D["step"]["13"]["close"] and "d:6030" not in D["noOrder"]


# ── поставщик — только там, где он уже есть ───────────────────────────────────────────────
def _cut(html: str, tabs: list[str], fine: dict) -> str:
    js = ("import {cutDashboard} from './access/acl.js';import fs from 'node:fs';"
          "const a=JSON.parse(process.argv[1]);"
          "process.stdout.write(cutDashboard(fs.readFileSync(0,'utf8'),a.tabs,a.fine));")
    r = subprocess.run(["node", "--input-type=module", "-e", js, json.dumps({"tabs": tabs, "fine": fine})],
                       input=html, capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    return r.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="нет node")
def test_поставщик_без_права_на_вкладку_не_доходит(tmp_path):
    """Названия поставщиков в строках заказов — внутри CONTRACTS (как rows[].orders[].sup):
    без права на «Реализацию» и «Поставщики» массив режется целиком — ни одного названия
    поставщика корпуса («Компания 40N») в странице; с правом — есть. Строки «Месяца» — только
    праву ork_heads."""
    import dashboard
    m = fixture.build_metrics()
    out = tmp_path / "index.html"
    dashboard.write(m, {"source": "rules", "items": []}, str(out), people=fixture.build_people(),
                    **fixture.build_realization())
    html = out.read_text(encoding="utf-8")
    sup = re.compile(r"Компания 40\d")
    assert sup.search(html) and '"orders":[{"k":' in html, "в сборке строки заказов с поставщиком есть"
    без = _cut(html, ["sourcing", "company"], {"admin": False, "rights": ["suppliers"]})
    assert not sup.search(без), "поставщик дошёл до страницы без права на вкладку"
    assert '"pulse":' not in без and '"drill":' not in без
    с = _cut(html, ["sourcing", "contracts"], {"admin": False, "rights": ["suppliers"]})
    assert sup.search(с) and '"pulse":' in с
    # «Месяц» с его строками — только праву ork_heads
    assert re.search(r"^window\.__ORKMONTH__ = __kvNoData\(\);$", с, re.M)
    с_правом = _cut(html, ["sourcing", "contracts"], {"admin": False, "rights": ["ork_heads"]})
    assert not re.search(r"^window\.__ORKMONTH__ = __kvNoData\(\);$", с_правом, re.M)
