"""«Сделки без хозяина» вкладки «Реализация» (решение владельца 30.10.2026, п. 3): открытая сделка
воронки реализации, где нет КАМа, ОСС сделки или менеджера ОРК (ни на сделке, ни у её живых заказов)
или хозяин ушёл. Одно множество у счётчика «Недели» (week.noOwner), списка за ним (drill.noOwner) и
фишки «Сделок и денег». Корпус придуман (CLAUDE.md, правило 18): сделки, люди и номера — вымышленные.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import kamqueue  # noqa: E402
import ork  # noqa: E402
import orkview  # noqa: E402
from tests import fixture  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 19)


def _deal(did: int, **kw) -> dict:
    """Строка сделки, как её отдаёт contracts.compute, — с полным набором хозяев."""
    d = {"deal": str(did), "seq": did - 1000, "closed": False, "lost": False, "inCat0": True,
         "kam": "Вымышленный КАМ", "ossDeal": "Вымышленный ОСС", "orkResp": "Вымышленный менеджер",
         "managerGone": False, "cat0At": "2026-09-19T10:00:00+03:00",
         "orders": [{"id": did * 10 + 1, "live": True, "orkMgr": "Вымышленный менеджер"}]}
    d.update(kw)
    return d


def _roles(gaps: list[dict]) -> list[tuple[str, str]]:
    return [(g["role"], g["state"]) for g in gaps]


# ── правило сделки ───────────────────────────────────────────────────────────────────────
def test_полная_сделка_не_в_списке():
    assert orkview.owner_gaps(_deal(9001)) == []
    out = orkview.no_owner([_deal(9001)], СЕГОДНЯ)
    assert out["n"] == 0 and out["rows"] == []


def test_нет_только_КАМа():
    g = orkview.owner_gaps(_deal(9002, kam=""))
    assert _roles(g) == [("kam", "none")]
    assert g[0]["by"] == "руководитель КАМ"


def test_нет_ОСС_и_менеджера_ОРК():
    # менеджера ОРК нет ни на сделке, ни у живого заказа; у закрытого заказа он не в счёт
    d = _deal(9003, ossDeal="", orkResp="",
              orders=[{"id": 1, "live": True, "orkMgr": ""}, {"id": 2, "live": False, "orkMgr": "Вымышленный менеджер"}])
    g = orkview.owner_gaps(d)
    assert _roles(g) == [("ossDeal", "none"), ("orkMgr", "none")]
    assert {x["by"] for x in g} == {ork.ROLES["orkHead"]}


def test_менеджер_ОРК_у_заказа_закрывает_роль():
    # поля сделки нет, но у живого заказа менеджер ОРК есть — хозяин есть
    d = _deal(9004, orkResp="", orders=[{"id": 1, "live": True, "orkMgr": "Вымышленный менеджер"}])
    assert orkview.owner_gaps(d) == []


def test_хозяин_ушёл():
    d = _deal(9005, kam="Вымышленный КАМ (уволен)", managerGone=True)
    assert _roles(orkview.owner_gaps(d)) == [("kam", "gone"), ("dealResp", "gone")]
    # менеджер ОРК «ушёл» — только когда ушли все менеджеры сделки и её живых заказов
    d = _deal(9006, orkResp="Вымышленный менеджер (уволен)",
              orders=[{"id": 1, "live": True, "orkMgr": "Вымышленный менеджер (уволен)"}])
    assert _roles(orkview.owner_gaps(d)) == [("orkMgr", "gone")]
    d = _deal(9007, orkResp="Вымышленный менеджер (уволен)",
              orders=[{"id": 1, "live": True, "orkMgr": "Другой вымышленный менеджер"}])
    assert orkview.owner_gaps(d) == []


def test_ответственный_заказа_вне_ОРК_своё_состояние():
    # единственный живой заказ: ответственный есть, но не из ОРК; «Ответственного за реализацию» нет.
    # Сделка — в «Сделках без хозяина», а заказ — в строке «ответственный вне ОРК» «Пульса», не в
    # «не назначен» (= «Заказов без менеджера ОРК»): подпись говорит об этом прямо
    o = {"id": 90091, "deal": "9009", "live": True, "orkMgr": "", "resp": "Вымышленный сотрудник продаж",
         "respOrk": False}
    d = _deal(9009, orkResp="", orders=[{"id": "90091", "live": True, "orkMgr": ""}])
    assert orkview.pulse_who(o, d) == (orkview.PULSE_OUT, "out")
    kind_of = {"90091": orkview.pulse_who(o, d)[1]}
    g = orkview.owner_gaps(d, kind_of)
    assert _roles(g) == [("orkMgr", "out")] and (g[0]["ordNone"], g[0]["ordOut"]) == (0, 1)
    assert orkview.owner_gap_text(g) == "менеджер ОРК не назначен: ответственный заказа вне ОРК"
    out = orkview.no_owner([d], СЕГОДНЯ, kind_of)
    assert out["n"] == 1 and out["byState"] == {"none": 0, "out": 1, "gone": 0}
    assert out["rows"][0]["days"] == 30, "дни — от входа в «Реализацию», как у «нет»"
    # тот же заказ без ответственного совсем — «нет», и он в «не назначен» «Пульса»
    o2 = {**o, "resp": ""}
    assert orkview.pulse_who(o2, d) == (orkview.PULSE_NONE, "none")
    g2 = orkview.owner_gaps(d, {"90091": "none"})
    assert _roles(g2) == [("orkMgr", "none")] and (g2[0]["ordNone"], g2[0]["ordOut"]) == (1, 0)
    # смешанная сделка: один заказ без ответственного, другой — с ответственным вне ОРК — «нет», счёт обоих видов
    d3 = _deal(9010, orkResp="", orders=[{"id": "1", "live": True, "orkMgr": ""}, {"id": "2", "live": True, "orkMgr": ""}])
    g3 = orkview.owner_gaps(d3, {"1": "none", "2": "out"})
    assert _roles(g3) == [("orkMgr", "none")] and (g3[0]["ordNone"], g3[0]["ordOut"]) == (1, 1)
    assert "Вымышленный" not in json.dumps(out, ensure_ascii=False)


def test_не_открытая_сделка_реализации_не_в_списке():
    for kw in ({"closed": True}, {"lost": True}, {"inCat0": False}):
        assert orkview.owner_gaps(_deal(9008, kam="", ossDeal="", **kw)) == [], kw


def test_кто_назначает_КАМа_тот_же_что_в_очереди_КАМа():
    assert orkview.OWNER_ASSIGN["kam"] == kamqueue.HEAD


# ── список: число = строки = ключи ─────────────────────────────────────────────────────────
def test_список_сделок_и_дни():
    deals = [_deal(9010, kam=""),                                             # нет КАМа
             _deal(9011, ossDeal="", orkResp="", orders=[{"id": 5, "live": True, "orkMgr": ""}]),
             _deal(9012, kam="Вымышленный КАМ (уволен)"),                      # ушёл
             _deal(9013),                                                      # полная — нет
             _deal(9014, kam="", closed=True)]                                 # закрыта — нет
    out = orkview.no_owner(deals, СЕГОДНЯ)
    assert out["n"] == len(out["rows"]) == 3
    assert {r["deal"] for r in out["rows"]} == {"9010", "9011", "9012"}
    assert all(r["k"] == "d:" + r["deal"] for r in out["rows"])
    by = {r["deal"]: r for r in out["rows"]}
    # «нет» — дни от входа в «Реализацию» (19.09 → 19.10 — 30 дн.); «ушёл» — дата ухода не хранится
    assert by["9010"]["days"] == 30 and by["9010"]["miss"][0]["days"] == 30
    assert by["9012"]["days"] is None and by["9012"]["miss"][0]["days"] is None
    assert by["9011"]["orders"] == 1
    assert out["byRole"] == {"kam": 2, "ossDeal": 1, "orkMgr": 1, "dealResp": 0}
    assert out["byState"] == {"none": 2, "out": 0, "gone": 1}
    assert out["unit"] == "deal"
    assert orkview.owner_gap_text(by["9011"]["miss"]) == "нет: ОСС сделки, менеджер ОРК"
    assert orkview.owner_gap_text(by["9012"]["miss"]) == "ушёл: КАМ"
    # имён в данных страницы нет — только роли (правило 5)
    assert "Вымышленный" not in json.dumps(out, ensure_ascii=False)


# ── сборка целиком ───────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def ct():
    return fixture.build_contracts()


def test_сборка_число_равно_ключам_и_строкам_сделок(ct):
    # строки сделок — одним массивом ct["rows"]: направление помечено seg (вид направления — его строки)
    for view, rows in ((ct, [r for r in ct["rows"] if not r.get("seg")]),
                       (ct["sep"], [r for r in ct["rows"] if r.get("seg")])):
        w = view["ork"]["week"]
        own, dk = w["noOwner"], w["drill"]["noOwner"]
        assert dk["unit"] == "deal" and all(k.startswith("d:") for k in dk["k"])
        assert own["n"] == len(own["rows"]) == len(dk["k"]) == len(set(dk["k"]))
        assert [r["k"] for r in own["rows"]] == dk["k"]
        # то же множество, что правило по строкам сделок вида (фишка «Сделок и денег» читает его же)
        want = {str(r["deal"]) for r in rows if orkview.owner_gaps(r)}
        assert {r["deal"] for r in own["rows"]} == want
        # сделка — открытая сделка воронки реализации
        by = {str(r["deal"]): r for r in rows}
        assert all(not by[r["deal"]]["closed"] and by[r["deal"]]["inCat0"] for r in own["rows"])
    assert ct["ork"]["week"]["noOwner"]["n"] > 0          # синтетика показывает блок


def test_сборка_заказы_сделок_без_менеджера_ОРК_по_видам_Пульса(ct):
    # «нет менеджера ОРК» у сделки и «Заказов без менеджера ОРК» — разные множества: у роли orkMgr
    # счёт живых заказов делится ровно как строки «не назначен» и «ответственный вне ОРК» «Пульса»
    w = ct["ork"]["week"]
    mk = {}
    for r in w["orders"]:
        # вид строки «Пульса» у строки заказа — подписью менеджера (mk снят: он у строки «Пульса»)
        m = r.get("mgr")
        m = w["strs"][m] if isinstance(m, int) else m          # строки заказов — со справочником strs
        k = {orkview.PULSE_OUT: "out", orkview.PULSE_NONE: "none"}.get(m, "mgr")
        mk.setdefault(r["deal"], []).append(k)
    seen_out = False
    for r in w["noOwner"]["rows"]:
        for g in r["miss"]:
            if g["role"] != "orkMgr" or g["state"] == "gone":
                continue
            ks = mk.get(r["deal"], [])
            assert (g["ordNone"], g["ordOut"]) == (ks.count("none"), ks.count("out")), r["deal"]
            assert "mgr" not in ks, r["deal"]
            assert g["state"] == ("out" if ks and ks.count("none") == 0 else "none"), r["deal"]
            seen_out = seen_out or g["ordOut"] > 0
    pulse = {p["kind"]: p["n"] for p in w["pulse"]["rows"] if p["kind"] in ("none", "out")}
    none_ord = sum(g["ordNone"] for r in w["noOwner"]["rows"] for g in r["miss"] if "ordNone" in g)
    out_ord = sum(g["ordOut"] for r in w["noOwner"]["rows"] for g in r["miss"] if "ordOut" in g)
    assert none_ord <= pulse.get("none", 0) and out_ord <= pulse.get("out", 0)
    assert seen_out, "синтетика показывает сделку с заказом, где ответственный вне ОРК"
