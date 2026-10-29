"""«Журнал шагов» «Месяца» (orkview._month → ORKMONTH.journal): срок каждого заказа на отрезках
с нормой регламента — S1 приёмка (1 р.д.), S2 (2), S3 (≤ 9), S6 (2). Просьба владельца
29.10.2026: «выгрузи списком в Эксель таблицу, где приёмка дольше 1 р.д.».

Проверяется: поля строки, окно и совокупность — те же, что у M1 (заказы, созданные с
ork.ORK_SINCE, отрезок закончился в месяцах «Месяца» по Москве); отметки словами (пачкой,
возврат, пропуск, порядок меток, нет календаря); приёмка сделки вне воронки — строкой «не в
счёте M1»; хозяин по регламенту и ворота людей «Месяца» (имя скрыто с той же причиной);
сходимость с M1 — чистые строки в счёте и сверх нормы дают ровно n и n − k ячейки; журнал — только
в массиве ORKMONTH, и резка воркера и гейта убирает его у всех без права «ork_heads».
Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import contracts as contracts_mod  # noqa: E402
import ork  # noqa: E402
import orkview  # noqa: E402
from tests import fixture  # noqa: E402

ROW_KEYS = {"deal", "seq", "order", "title", "sup", "seg", "step", "stepName", "start", "end", "wd", "norm",
            "over", "inNorm", "flags", "measurable", "counted", "note", "owner", "ownerRole", "ownerHidden",
            "ownerWhy", "m"}
_d = lambda s: dt.datetime.strptime(s, "%d.%m.%Y").date()           # noqa: E731


def _build(stub=None):
    """contracts.compute на корпусе fixture и заказы, которые он отдал orkview основного вида."""
    got: dict[str, list] = {}
    orig = orkview.build

    def spy(orders, *a, **k):
        got.setdefault("o", orders)
        return orig(orders, *a, **k)
    old = os.environ.get("KVANT_NOW")
    os.environ["KVANT_NOW"] = fixture.CT_NOW
    orkview.build = spy
    try:
        stub = stub or fixture.ContractsStub()
        data = contracts_mod.compute(stub, as_of=fixture.CT_TODAY, people=fixture.CT_ROSTER,
                                     departments=stub.departments())
    finally:
        orkview.build = orig
        if old is None:
            os.environ.pop("KVANT_NOW", None)
        else:
            os.environ["KVANT_NOW"] = old
    return data, got["o"]


@pytest.fixture(scope="module")
def built():
    return _build()


def _clean(r):
    return r["measurable"] and r["counted"]


def test_поля_окно_совокупность(built):
    data, orders = built
    om = data["orkMonth"]["main"]
    J, rows = om["journal"], om["journal"]["rows"]
    assert rows and all(set(r) == ROW_KEYS for r in rows)
    assert J["months"] == [r["m"] for r in om["month"]["rows"]] and J["since"] == ork.ORK_SINCE.isoformat()
    assert [(s["seg"], s["step"], s["norm"]) for s in J["segs"]] == [("S1", 1, 1), ("S2", 4, 2), ("S3", 5, 9),
                                                                    ("S6", 8, 2)]
    assert {s["seg"]: s["name"] for s in J["segs"]}["S1"] == "Приёмка сделки"
    # окно: конец отрезка (по Москве) — в месяце строки, месяц — из окна «Месяца»
    for r in rows:
        e = _d(r["end"])
        assert r["m"] == e.strftime("%m.%Y") and r["m"] in J["months"], r
    # совокупность M1: заказы ОРК-эпохи, каждый законченный в окне отрезок — ровно одна строка
    lo = {o["id"]: o for o in orders}
    era = {o["id"] for o in orders if o.get("created") and o["created"] >= ork.ORK_SINCE}
    assert {r["order"] for r in rows} <= era
    m0 = dt.datetime.strptime(J["months"][-1], "%m.%Y").date()
    want = {(o["id"], c) for o in orders if o["id"] in era for c in orkview.JOURNAL_SEGS
            if (s := (o.get("_segs") or {}).get("segs", {}).get(c)) and orkview._end(s) and orkview._end(s) >= m0}
    assert {(r["order"], r["seg"]) for r in rows} == want
    for r in rows:
        o, s = lo[r["order"]], lo[r["order"]]["_segs"]["segs"][r["seg"]]
        assert (r["deal"], r["seq"], r["title"], r["sup"]) == (o["deal"], o["seq"], o["title"], o["sup"])
        assert (r["wd"], r["inNorm"], r["norm"]) == (s["wd"], s["inNorm"], ork.SEG_NORM[r["seg"]])
        assert r["over"] == (s["wd"] - r["norm"] if s["wd"] is not None and s["wd"] > r["norm"] else None)
        assert r["start"] == ork._d(s["start"]).strftime("%d.%m.%Y")


def test_хозяин_по_регламенту_и_ворота_людей(built):
    """S1 — руководитель ОРК (справочник отделов), S2/S3/S6 — менеджер ОРК заказа или «не
    назначен». Ячейка «Месяца» скрыта воротами людей («раскрыл бы человека») — в её строках
    имени нет, причина та же."""
    data, orders = built
    om = data["orkMonth"]["main"]
    lo = {o["id"]: o for o in orders}
    cells = {r["m"]: r["M1"] for r in om["month"]["rows"]}
    head = fixture.CT_ROSTER["30"]["name"]
    hidden = 0
    for r in om["journal"]["rows"]:
        if r["seg"] == "S1":
            assert (r["owner"], r["ownerRole"], r["ownerHidden"]) == (head, "руководитель ОРК", False)
            continue
        assert r["ownerRole"] == "менеджер ОРК"
        cell = cells[r["m"]][r["seg"]]
        if cell.get("peopleGate"):
            hidden += 1
            assert r["ownerHidden"] and r["owner"] == "" and r["ownerWhy"] == cell["why"]
            assert "раскрыл бы человека" in r["ownerWhy"]
        else:
            assert not r["ownerHidden"] and r["owner"] == (lo[r["order"]].get("orkMgr") or "не назначен")
    assert hidden, "в корпусе за S2/S3/S6 один ответственный — имя скрыто"


def _consistent(om: dict, *, all_cells: bool) -> int:
    """Чистые строки в счёте (без отметок) — n ячейки M1, из них сверх нормы — n − k."""
    by = {}
    for r in om["journal"]["rows"]:
        if _clean(r):
            by.setdefault((r["m"], r["seg"]), []).append(r)
    checked = 0
    for mr in om["month"]["rows"]:
        for c in orkview.JOURNAL_SEGS:
            cell, R = mr["M1"][c], by.get((mr["m"], c), [])
            if cell["state"] not in ("show", "prelim"):
                assert not all_cells or not R, (mr["m"], c, cell)
                continue
            assert len(R) == cell["n"], (mr["m"], c, len(R), cell)
            assert sum(1 for r in R if (r["over"] or 0) > 0) == cell["n"] - cell["k"], (mr["m"], c)
            assert all((r["over"] or 0) > 0 for r in R if not r["inNorm"])
            checked += 1
    return checked


def test_сходимость_с_m1(built, monkeypatch):
    """Каждая ячейка M1 — из тех же строк: показанные ячейки сходятся как есть; с воротами,
    снятыми на время проверки (люди, «пачки», покрытие), сходится каждая ячейка с отрезками."""
    data, _o = built
    assert _consistent(data["orkMonth"]["main"], all_cells=False) >= 1
    for k, v in (("n_managers_min", 0), ("burst_max", 1.0), ("cover_prelim", 0.0), ("cover_show", 0.0)):
        monkeypatch.setitem(ork.GATES, k, v)
    open_data, _o2 = _build()
    om = open_data["orkMonth"]["main"]
    n = _consistent(om, all_cells=True)
    segs = {(r["m"], r["seg"]) for r in om["journal"]["rows"] if _clean(r)}
    assert n == len(segs) >= 4, (n, segs)
    total = sum(1 for r in om["journal"]["rows"] if _clean(r))
    assert total == sum(mr["M1"][c]["n"] for mr in om["month"]["rows"] for c in orkview.JOURNAL_SEGS
                        if mr["M1"][c]["state"] in ("show", "prelim"))


# ── придуманные заказы: отметки, приёмка вне воронки, ворота людей, порядок ───────────────
T = dt.date(2026, 10, 26)
NAMES = {"NEW": "Заказ создан", "SENT": "Заказ направлен", "PO": "Сформирован РО/Подготовка описаний",
         "PAY": "Оплата поставщику / агенту", "PROD": "Производство заказа", "READY": "Заказ готов",
         "SHIP": "Подготовка к отгрузке"}


def _ts(day: str, hm: str = "10:00") -> str:
    return f"2026-{day}T{hm}:00+03:00"


def _o(oid: str, tr: list, *, deal="D1", who="Анна", created="2026-09-01", t0=None, wdf=None, seq=900):
    kw = {"t0": t0 or tr[0][1]}
    if wdf:
        kw["workdays_fn"] = wdf
    return {"id": oid, "deal": deal, "seq": seq, "title": f"{seq}/{oid}. Узел", "sup": "Поставщик П",
            "created": dt.date.fromisoformat(created), "resp": who, "orkMgr": who, "respOrk": True,
            "art": {}, "_reach": ork.reach_times(tr, NAMES), "_segs": ork.segments(tr, NAMES, **kw)}


def _corpus():
    return [
        _o("1", [("NEW", _ts("09-07")), ("SENT", _ts("09-10"))]),                      # приёмка 3 р.д.
        _o("2", [("NEW", _ts("09-14")), ("SENT", _ts("09-15"))], seq=901),             # в норме
        _o("3", [("NEW", _ts("09-01", "10:00")), ("SENT", _ts("09-01", "10:01")),      # «пачкой»
                 ("PO", _ts("09-01", "10:02")), ("PAY", _ts("09-01", "10:03"))], seq=902),
        _o("4", [("NEW", _ts("09-02")), ("SENT", _ts("09-03")), ("PO", _ts("09-08")),  # возврат внутри S3
                 ("SENT", _ts("09-09")), ("PO", _ts("09-10")), ("PAY", _ts("09-21"))], seq=903, who="Борис"),
        _o("5", [("NEW", _ts("09-02")), ("PAY", _ts("09-04"))], seq=904),              # пропуск шагов 4–5
        _o("6", [("NEW", _ts("09-01")), ("SENT", _ts("09-02"))], t0=_ts("09-05"), seq=905),   # t0 позже
        _o("7", [("NEW", _ts("09-16")), ("SENT", _ts("09-18"))], wdf=lambda a, b: None, seq=906),  # календаря нет
        _o("8", [("NEW", _ts("09-21")), ("SENT", _ts("09-25"))], deal="D9", seq=907),  # сделка вне воронки
        _o("9", [("READY", _ts("09-15")), ("SHIP", _ts("09-21"))], seq=908),           # S6 сверх нормы
        _o("10", [("NEW", _ts("09-21")), ("SENT", _ts("09-28"))], created="2026-08-01", seq=909),  # до эпохи ОРК
        _o("11", [("NEW", _ts("10-05")), ("SENT", _ts("10-06")), ("PO", _ts("10-12"))], seq=910, who="Вера"),
    ]


def test_отметки_вне_воронки_ворота_и_порядок(monkeypatch):
    month, _mp, J = orkview._month(_corpus(), [], T, None, {}, deal_ok_all={"D1": True, "D9": False},
                                   ork_head="Роза")
    rows = J["rows"]
    by = {(r["order"], r["seg"]): r for r in rows}
    r = by[("1", "S1")]
    assert (r["start"], r["end"], r["wd"], r["over"], r["flags"], r["measurable"], r["counted"]) == (
        "07.09.2026", "10.09.2026", 3, 2, "", True, True)
    assert (r["owner"], r["ownerRole"], r["m"], r["step"], r["stepName"]) == (
        "Роза", "руководитель ОРК", "09.2026", 1, "Приёмка сделки")
    assert by[("2", "S1")]["inNorm"] is True and by[("2", "S1")]["over"] is None
    assert all("пачкой — срок не мерится" in by[("3", c)]["flags"] and not by[("3", c)]["measurable"]
               for c in ("S1", "S2", "S3"))
    assert by[("4", "S3")]["flags"] == "возврат на шаг раньше" and by[("4", "S2")]["flags"] == ""
    assert by[("5", "S2")]["flags"] == "шаг пропущен" and by[("5", "S3")]["flags"] == "шаг пропущен"
    assert by[("6", "S1")]["flags"] == "порядок меток нарушен" and by[("6", "S1")]["wd"] < 0
    assert by[("6", "S1")]["over"] is None
    r7 = by[("7", "S1")]
    assert (r7["flags"], r7["wd"], r7["over"], r7["measurable"]) == ("нет календаря", None, None, False)
    r8 = by[("8", "S1")]
    assert (r8["counted"], r8["note"], r8["measurable"], r8["over"]) == (False, orkview.J_OUT, True, 3)
    assert by[("9", "S6")]["over"] == 2 and by[("9", "S6")]["ownerRole"] == "менеджер ОРК"
    assert not [k for k in by if k[0] == "10"], "заказ до эпохи ОРК — не в журнале, как у M1"
    assert by[("11", "S2")]["m"] == "10.2026"
    # сентябрь: за S2 — Анна, Борис (двое < 3) — ячейка скрыта, имени в её строках нет
    sep = next(m for m in month["rows"] if m["m"] == "09.2026")
    assert sep["M1"]["S2"]["state"] == "hidden" and sep["M1"]["S2"].get("peopleGate")
    for k, r in by.items():
        if r["m"] == "09.2026" and r["seg"] == "S2":
            assert r["ownerHidden"] and r["owner"] == "" and r["ownerWhy"] == sep["M1"]["S2"]["why"], k
    assert by[("1", "S1")]["ownerHidden"] is False, "приёмка — без ворот людей"
    # S1 сентября в счёте M1 — измеримые строки сделок воронки: 1, 2, 4, 5, 9 (3 — пачкой, 6 —
    # порядок меток, 7 — нет календаря, 8 — сделка вне воронки); сверх нормы — 1 и 5
    clean = sorted(r["order"] for r in rows if r["m"] == "09.2026" and r["seg"] == "S1" and _clean(r))
    assert clean == ["1", "2", "4", "5", "9"]
    # «пачек» в ячейке больше порога — ячейка скрыта (значения нет), строки журнала остаются
    assert sep["M1"]["S1"]["state"] == "hidden" and "k" not in sep["M1"]["S1"]
    for k, v in (("burst_max", 1.0), ("cover_prelim", 0.0), ("cover_show", 0.0)):
        monkeypatch.setitem(ork.GATES, k, v)
    m2, _mp2, _j2 = orkview._month(_corpus(), [], T, None, {}, deal_ok_all={"D1": True, "D9": False},
                                   ork_head="Роза")
    s1 = next(m for m in m2["rows"] if m["m"] == "09.2026")["M1"]["S1"]
    assert s1["n"] == 5 and s1["n"] - s1["k"] == 2 == sum(1 for r in rows if r["m"] == "09.2026"
                                                          and r["seg"] == "S1" and _clean(r) and r["over"])
    # порядок: превышение по убыванию, затем дата конца
    overs = [r["over"] for r in rows if r["over"]]
    assert overs == sorted(overs, reverse=True) and rows[0]["over"] == max(overs)
    assert [r["over"] for r in rows[:len(overs)]] == overs, "сверх нормы — первыми"
    tail = [_d(r["end"]) for r in rows if not r["over"]]
    assert tail == sorted(tail)


def test_пустое_окно():
    month, _mp, J = orkview._month([], [], T, None, {})
    assert J["rows"] == [] and len(J["months"]) == ork.MONTHS_BACK and len(J["segs"]) == 4


# ── только в массиве ORKMONTH; резка воркера и гейта ───────────────────────────────────────
@pytest.fixture(scope="module")
def page():
    import dashboard
    r = fixture.build_realization()
    html = dashboard.render({}, {}, contracts=r["contracts"], dealctx=r["dealctx"], ork_month=r["ork_month"])
    return html, r


def test_в_странице_только_под_правом(page):
    html, r = page
    lines = {ln.split(" = ", 1)[0]: ln for ln in html.splitlines() if ln.startswith("window.__")}
    om = json.loads(lines["window.__ORKMONTH__"].split(" = ", 1)[1].rstrip(";"))
    assert om["main"]["journal"]["rows"], "журнал — в массиве ORKMONTH"
    for name, ln in lines.items():
        if name != "window.__ORKMONTH__":
            assert '"stepName"' not in ln and '"journal"' not in ln and '"ownerWhy"' not in ln, name


def test_упаковка_строк_журнала():
    """Строк от 20 — упакованным списком (как сорсеры); шаблон разворачивает их тем же _unpack."""
    import dashboard
    row = {k: None for k in ROW_KEYS} | {"seg": "S1", "deal": "D", "wd": 2}
    om = {"main": {"month": {}, "people": [], "how": [], "journal": {"rows": [dict(row, seq=i) for i in range(25)]}}}
    p = dashboard._pack_ork_month(om)["main"]["journal"]["rows"]
    assert p[dashboard.PACK_MARK] == 1 and len(p["r"]) == 25 and "seq" in p["f"]
    few = {"main": {"journal": {"rows": [row]}}}
    assert dashboard._pack_ork_month(few)["main"]["journal"]["rows"] == [row]


CUT = r"""
const fs = require('node:fs');
const html = fs.readFileSync(0, 'utf8');
(async () => {
  const acl = await import(process.argv[1]);
  const gate = await import(process.argv[2]);
  const out = {
    noRight: acl.cutDashboard(html, ['contracts'], {admin: false, rights: ['suppliers', 'suppliers_fin', 'kam_money']}),
    heads: acl.cutDashboard(html, ['contracts'], {admin: false, rights: ['ork_heads']}),
    admin: acl.cutDashboard(html, ['contracts'], {admin: true, rights: []}),
    gateUser: gate.applyAcl(html, ['contracts'], {admin: false, email: 'u@example.com'}),
    gateAdmin: gate.applyAcl(html, ['contracts'], {admin: true, email: 'a@example.com'}),
  };
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="нет node")
def test_резка_воркера_и_гейта_убирает_журнал_без_права(page):
    """Журнал — внутри ORKMONTH: строка массива режется целиком (public/_worker.js, access/acl.js
    — право ork_heads; access/gate.js — только админ). Без права в странице нет ни ключей журнала,
    ни названий заказов из него."""
    html, r = page
    rows = r["ork_month"]["main"]["journal"]["rows"]
    only_journal = {x["title"] for x in rows} - {o["title"] for d in r["contracts"]["rows"] for o in d["orders"]}
    p = subprocess.run(["node", "-e", CUT, (ROOT / "access" / "acl.js").as_uri(), (ROOT / "access" / "gate.js").as_uri()],
                       input=html, capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert p.returncode == 0, p.stderr[-2000:]
    out = json.loads(p.stdout)
    for k in ("noRight", "gateUser"):
        assert '"stepName"' not in out[k] and '"ownerWhy"' not in out[k] and '"journal"' not in out[k], k
        assert "window.__ORKMONTH__ = __kvNoData();" in out[k], k
        assert not [t for t in only_journal if t and t in out[k]], k
    for k in ("heads", "admin", "gateAdmin"):
        assert '"stepName"' in out[k] and '"journal"' in out[k], k
