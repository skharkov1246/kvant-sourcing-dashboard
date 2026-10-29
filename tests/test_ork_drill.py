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


# ── строки «Недели» ───────────────────────────────────────────────────────────────────────
def test_строки_заказов_та_же_совокупность_что_live(built):
    """week.orders — живые заказы ведомости (U + C12): столько же, сколько week.live, ключи
    уникальны; номер — как портал называет заказ («950/1»); шаг — тот же, что в ведомости."""
    for name, v in _views(built["contracts"]):
        w = v["ork"]["week"]
        rows = w["orders"]
        assert len(rows) == w["live"] == len({r["k"] for r in rows}), name
        assert all(re.fullmatch(r"\d{1,5}(/\d+)?|#\d+", r["no"]) for r in rows), name
        steps = Counter(r["step"] for r in rows)
        for s in w["ledger"]:
            assert steps.get(s["no"], 0) == s["now"], (name, s["no"])
        assert steps.get(None, 0) == w["unknown"]
        # строка — то, что нужно списку: сделка, стадия, шаг, хозяин, менеджер ОРК
        for r in rows:
            assert {"k", "no", "deal", "seq", "stage", "step", "mgr", "mk"} <= set(r), r
            assert not (set(r) & MONEY_KEYS), "денег в строках нет"
    main = built["contracts"]["ork"]["week"]["orders"]
    assert {"950/1", "930/2"} <= {r["no"] for r in main}, "номер заказа — из названия «950/1. …»"
    assert any(r.get("c12") for r in main) and any(r.get("p80") for r in main)
    assert any(r.get("over") for r in main) and any(r.get("wu") for r in main)


def test_коды_очереди_и_прогноз_у_строки_как_в_очереди(built):
    """Пункты очереди на заказе — ровно коды life.queue этого заказа; прогноз — тот же, что у
    пункта очереди (lifecycle.forecast, одно мерило); кодов закрытой части в строках нет."""
    ct = built["contracts"]
    for name, v in _views(ct):
        rows = {r["k"]: r for r in v["ork"]["week"]["orders"]}
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


def test_каждое_число_недели_и_его_заказы(built):
    """Число ↔ ключи: плитки W1–W3, W5, ведомость (сейчас, сверх нормы, ждём УПД, срок
    неизвестен, не в счёте, нет артефактов, закрытые сделки, дольше P80, к закрытию), ждут
    смежников, дольше P80, сделки без заказа, «ждём УПД», сверх нормы по менеджерам, гигиена,
    строка доверия. Ключ заказа — среди week.orders, ключ сделки — среди week.deals."""
    for name, v in _views(built["contracts"]):
        w = v["ork"]["week"]
        D, T = w["drill"], w["tiles"]
        okeys = {r["k"] for r in w["orders"]}
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
        H, hy = D["hyg"], w["hygiene"]
        assert _n(H["all"]) == hy["n"] and _n(H["excl"]) == hy["excluded"]
        assert _n(H["noMgr"]) == hy["by"].get("noMgr", 0)
        for k in ("behind", "abandoned", "old", "unknown"):
            assert _n(H["x" + k]) == hy["byExcl"].get(k, 0), (name, k)
        known(H["all"])
    # пример корпуса: у «Ждут смежников» логистики 10 заказов — все на шагах 9 и 11
    w = built["contracts"]["ork"]["week"]
    rows = {r["k"]: r for r in w["orders"]}
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
        rows = {r["k"]: r for r in w["orders"]}
        assert sum(p["n"] for p in P["rows"]) == w["live"] == P["live"], name
        by = Counter()
        for p in P["rows"]:
            by.update(p["byStep"])
            assert len(p["keys"]) == p["n"] and {rows[k]["mgr"] for k in p["keys"]} == {p["who"]}
            assert len(p["overKeys"]) == p["over"] and len(p["waitKeys"]) == p["wait"]
            assert set(p["overKeys"]) | set(p["waitKeys"]) <= set(p["keys"])
            assert p["pct"] == round(p["n"] * 100 / w["live"], 1)
            assert p["deals"] == len({rows[k]["deal"] for k in p["keys"]})
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
    assert orkview.order_no({"id": 5, "title": "Узел без номера"}) == "#5"


# ── «Квартал» и «Месяц» ──────────────────────────────────────────────────────────────────
def test_квартал_заказы_за_q1_и_q3(built):
    """Q1 по кварталу и поставщику, Q3: ключей — n показателя (в срок — k); скрытая строка
    поставщика ключей не получает; ключ — среди quarter.orders."""
    q = built["contracts"]["ork"]["quarter"]
    D, okeys = q["drill"], {r["k"] for r in q["orders"]}
    seen = 0
    for i, Q in enumerate(q["quarters"]):
        for st in [Q["Q1"]] + Q["suppliers"]:
            if st.get("state") in ("show", "prelim"):
                d = D[st["dk"].split(":", 1)[1]]
                assert len(d["k"]) == st["n"], st.get("sup")
                assert sum(t.startswith("в срок") for t in d["t"]) == st["k"]
                assert set(d["k"]) <= okeys
                seen += 1
            else:
                assert "dk" not in st, "скрытая строка — без списка"
    assert seen > 2
    Q3 = q["Q3"]
    assert len(D["Q3"]["k"]) == Q3["n"] and set(D["Q3"]["k"]) <= okeys
    assert sum(t == "первичная оплата 100 %" for t in D["Q3"]["t"]) == Q3["k"]
    assert not any(set(r) & MONEY_KEYS for r in q["orders"])


def test_месяц_заказы_за_числами_под_правом(built):
    """«Месяц» (массив ORKMONTH, под правом ork_heads): у показанной ячейки M1, M2, доставки в
    срок и поимённо — ключей ровно n; у «не показываем» — ни dk, ни списка; в данных вкладки
    для всех (CONTRACTS) «Месяца» и его списков нет."""
    om = built["ork_month"]
    assert "month" not in built["contracts"]["ork"] and "drill" not in json.dumps(built["contracts"]["ork"]["how"])
    for vk in ("main", "sep"):
        V = om.get(vk)
        if not V:
            continue
        D, okeys = V["month"]["drill"], {r["k"] for r in V["month"]["orders"]}
        cells = []
        for row in V["month"]["rows"]:
            cells += list(row["M1"].values()) + [row["M2"], row["otd"]]
        for p in V["people"]:
            cells += list(p["M1"].values()) + [p["M2"]] + list((p["cur"].get("M1") or {}).values()) + [p["cur"]["M2"]]
            if p.get("liveDk"):
                assert len(D[p["liveDk"].split(":", 1)[1]]["k"]) == p["live"]
        shown = 0
        for st in cells:
            if st.get("state") in ("show", "prelim"):
                d = D[st["dk"].split(":", 1)[1]]
                assert len(d["k"]) == st["n"] and set(d["k"]) <= okeys, st
                shown += 1
            else:
                assert "dk" not in st
        if vk == "main":
            assert shown >= 3
            # в корпусе S2/S3/S6 отдела скрыты воротами людей — их отрезков в списках нет
            hidden = [r["M1"]["S2"] for r in V["month"]["rows"] if r["M1"]["S2"]["state"] == "hidden"]
            assert hidden and not any(k.endswith(".S2") and not k.startswith("p") for k in D)


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
