"""Воронка пресейла (presale.compute) на придуманных сделках.

Формы записей — как в crm.deal.list и crm.status.list; стадии и пороги — по
замеру воронки «Пресейл» (зонд v48, 09.10.2026).
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import presale  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 9)
SRC = presale.SOURCER_F


def _d(days_ago: int) -> str:
    return (СЕГОДНЯ - dt.timedelta(days=days_ago)).isoformat() + "T10:00:00+03:00"


def стадии() -> dict:
    m = {}
    for i, (sid, name, sem) in enumerate([
            ("C32:NEW", "Новый запрос", "P"), ("C32:PREPARATION", "Назначение сорсера", "P"),
            ("C32:UC_1", "Поиск поставщиков и сбор КП", "P"), ("C32:WON", "Сделка выиграна", "S"),
            ("C32:LOSE", "Экономически не интересно", "F"), ("C32:UC_9", "Вовремя не подались", "F")]):
        m[sid] = {"name": name, "sem": sem, "sort": 10 * i, "cat": "32"}
    m["C0:NEW"] = {"name": "Чужая", "sem": "P", "sort": 0, "cat": "0"}
    return m


def сделки() -> list[dict]:
    return [
        # без сорсера, свежая
        {"ID": "1", "TITLE": "Насос", "STAGE_ID": "C32:NEW", "DATE_CREATE": _d(1), "MOVED_TIME": _d(1)},
        # сорсер есть, 5 дней без единого запроса
        {"ID": "2", "TITLE": "Клапан", "STAGE_ID": "C32:UC_1", "DATE_CREATE": _d(5), "MOVED_TIME": _d(4), SRC: "76"},
        # сорсер ушёл, стоит 20 дней, запросы есть
        {"ID": "3", "TITLE": "Компрессор", "STAGE_ID": "C32:UC_1", "DATE_CREATE": _d(30), "MOVED_TIME": _d(20),
         SRC: ["77"], "OPPORTUNITY": "1000"},
        # отказ
        {"ID": "4", "TITLE": "Турбина", "STAGE_ID": "C32:LOSE", "DATE_CREATE": _d(40), "MOVED_TIME": _d(30), SRC: "76"},
    ]


def запросы() -> list[dict]:
    return ([{"id": 10 + i, "parentId2": 3, "createdTime": _d(28 - i), "createdBy": "900", "_hasQuote": i == 0}
             for i in range(3)]
            + [{"id": 20, "parentId2": 4, "createdTime": _d(39), "createdBy": "76"},
               {"id": 30, "parentId2": 999, "createdTime": _d(3), "createdBy": "76"}])     # чужая сделка


def _ps() -> dict:
    return presale.compute(cid="32", cat_name="Пресейл", deals=сделки(), stage_meta=стадии(), rfqs=запросы(),
                           people={"76": {"name": "Иванов И.", "active": True},
                                   "77": {"name": "Петрова А.", "active": False}},
                           names={}, today=СЕГОДНЯ, service_ids={"900"})


def test_воронка_находится_по_имени_а_не_по_номеру():
    assert presale.find_category({"0": "Общая", "32": "Пресейл", "14": "Клиент"}) == ("32", "Пресейл")
    assert presale.find_category({"40": "Presale 2027"}) == ("40", "Presale 2027")
    assert presale.find_category({"0": "Общая"}) is None


def test_флаги_того_что_делать():
    ps = _ps()
    act = {r["id"]: r["flags"] for r in ps["act"]}
    assert act["1"] == ["nosrc"], "свежая сделка без сорсера: назначить; запросов ещё не ждём"
    assert act["2"] == ["norfq"]
    assert set(act["3"]) == {"gone", "stale"}
    assert "4" not in act, "закрытая отказом сделка действий не требует"
    assert [r["id"] for r in ps["act"]] == ["3", "1", "2"], "сначала ушедший сорсер, потом без сорсера"


def test_сводка_и_стадии():
    ps = _ps()
    h = ps["head"]
    assert (h["total"], h["open"], h["lost"], h["won"]) == (4, 3, 1, 0)
    assert (h["withRfq"], h["rfq"], h["withQuotes"]) == (2, 4, 1)
    assert h["robotRfqPct"] == 75, "3 из 4 запросов под сделками воронки завёл робот"
    assert h["lagMed"] == 1.5, "сделка 3: запрос через 2 дня, сделка 4: через 1"
    st = {s["name"]: s for s in ps["stages"]}
    assert "Чужая" not in st, "стадии другой воронки не берутся"
    assert st["Поиск поставщиков и сбор КП"]["n"] == 2 and st["Поиск поставщиков и сбор КП"]["stale"] == 1
    assert ps["lost"] == [{"name": "Экономически не интересно", "n": 1}]


def test_по_сорсеру_и_ушедший():
    by = {a["n"]: a for a in _ps()["bySourcer"]}
    assert by["Петрова А."]["gone"] is True and by["Петрова А."]["open"] == 1 and by["Петрова А."]["rfq"] == 3
    assert by["Иванов И."]["open"] == 1 and by["Иванов И."]["lost"] == 1 and by["Иванов И."]["norfq"] == 1
    assert by["сорсер не назначен"]["open"] == 1
    assert _ps()["bySourcer"][-1]["id"] == "", "строка «не назначен» — последней"
