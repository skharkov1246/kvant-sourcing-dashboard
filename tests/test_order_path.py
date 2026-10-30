"""Путь живого заказа по шагам регламента ОРК (ork.path, contracts: orders[].path) — «провалиться в
процесс, где зависло» (владелец 29.10.2026). Запись пути — [шаг, день входа ISO (МСК), р.д. на
шаге]; последняя запись — текущий шаг заказа, день счёта его срока и р.д. — те же, что step,
stepFrom и stepWd строки заказа. Корпус придуман (CLAUDE.md, правило 18): стадии — имена воронки
заказов, даты и номера — выдуманы.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import ork  # noqa: E402

TODAY = dt.date(2026, 10, 19)
# стадии заказа: STAGE_ID → имя (как ostages у contracts)
NAMES = {"S:NEW": "Заказ создан", "S:SENT": "Заказ направлен", "S:PO": "Сформирован РО/Подготовка описаний",
         "S:SPEC": "Подготовка спецификации и инвойса", "S:PAY": "Оплата поставщику / агенту",
         "S:PROD": "Производство заказа", "S:READY": "Заказ готов", "S:SHIP": "Отгрузка",
         "S:TRUCK": "Подготовка груза/Поиск транспорта", "S:GOT": "Заказ получен клиентом",
         "S:WAIT": "Ожидание оплаты", "S:OLD": "Архив (старая стадия)"}


def cal(a, b):
    """Календарные дни вместо р.д. — чтобы ожидания читались глазами (функция подставляется)."""
    return (b - a).days


def t(day: str, hh: int = 10) -> str:
    return f"{day}T{hh:02d}:00:00+03:00"


def order(stage: str, created: str, tr: list, **kw) -> dict:
    """Заказ, как его видит ork: стадия, вход в шаг (ork.step_since), создание, первые выходы."""
    tr_t = [(s, t(d)) for s, d in tr]
    o = {"stage": NAMES[stage], "created": dt.date.fromisoformat(created), "t0": t(created, 9),
         "stageSince": tr_t[-1][1] if tr_t else t(created, 9),
         "stepSince": ork.step_since(tr_t, NAMES)[1] if tr_t else t(created, 9),
         "_reach": ork.reach_times(tr_t, NAMES)}
    o.update(kw)
    return o, tr_t


def check_tail(o: dict, p: list, fn=cal) -> None:
    """Последняя запись = шаг сейчас; у шага с нормой — день счёта срока (clock) и р.д. до
    сегодня; у шага без нормы — вход по истории, р.д. от него."""
    st = ork.step_now(o)
    assert p[-1][0] == st
    assert p[-1][2] == fn(dt.date.fromisoformat(p[-1][1]), TODAY)
    if st is None:                                 # стадия вне регламента: ни нормы, ни выдуманного шага
        assert all(e[0] is not None for e in p[:-1])
        return
    if st in ork.NORM_WD:
        assert p[-1][1] == ork.clock(o, st)[0].isoformat()
    ov = ork.overrun(o, TODAY, fn)
    if ov["wd"] is not None:                       # шаг с нормой: та же пара, что stepFrom/stepWd
        assert (p[-1][1], p[-1][2]) == (ov["since"], ov["wd"])


def test_normal_path():
    o, tr = order("S:PROD", "2026-09-01", [("S:NEW", "2026-09-01"), ("S:SENT", "2026-09-03"),
                                           ("S:PO", "2026-09-07"), ("S:SPEC", "2026-09-10"),
                                           ("S:PAY", "2026-09-20"), ("S:PROD", "2026-09-25")])
    p = ork.path(o, tr, NAMES, TODAY, cal)
    # шаг 5 — две стадии подряд: одна запись; р.д. — до входа в следующую
    assert p == [[1, "2026-09-01", 2], [4, "2026-09-03", 4], [5, "2026-09-07", 13],
                 [6, "2026-09-20", 5], [7, "2026-09-25", 24]]
    check_tail(o, p)


def test_back_and_forth():
    o, tr = order("S:PO", "2026-09-01", [("S:NEW", "2026-09-01"), ("S:SENT", "2026-09-02"),
                                         ("S:PO", "2026-09-05"), ("S:SENT", "2026-09-08"),
                                         ("S:SPEC", "2026-09-15"), ("S:SENT", "2026-09-20"),
                                         ("S:PO", "2026-10-01")])
    p = ork.path(o, tr, NAMES, TODAY, cal)
    assert [x[0] for x in p] == [1, 4, 5, 4, 5, 4, 5]            # возвраты — отдельными записями
    assert p[-1][:2] == [5, "2026-10-01"]                         # срок шага 5 — от повторного входа
    check_tail(o, p)


def test_skipped_step():
    o, tr = order("S:PROD", "2026-08-01", [("S:NEW", "2026-08-01"), ("S:PROD", "2026-08-04")])
    p = ork.path(o, tr, NAMES, TODAY, cal)
    assert p == [[1, "2026-08-01", 3], [7, "2026-08-04", 76]]     # шаги 2–6 пропущены
    check_tail(o, p)


def test_assignment_ends_step1():
    o, tr = order("S:NEW", "2026-10-01", [("S:NEW", "2026-10-01")],
                  assignDate=dt.date(2026, 10, 3), orkMgr="Менеджер А")
    p = ork.path(o, tr, NAMES, TODAY, cal)
    # стадия так и стоит «Заказ создан», но с назначения — шаг 4 (ASSIGN_FLOOR), срок — от него
    assert p == [[1, "2026-10-01", 2], [4, "2026-10-03", 16]]
    assert ork.clock(o, 4) == (dt.date(2026, 10, 3), "назначение")
    check_tail(o, p)
    # назначение раньше начала приёмки — не в счёт: шаг 1
    o2, tr2 = order("S:NEW", "2026-10-01", [("S:NEW", "2026-10-01")],
                    assignDate=dt.date(2026, 9, 20), orkMgr="Менеджер А")
    assert [x[0] for x in ork.path(o2, tr2, NAMES, TODAY, cal)] == [1]


def test_no_history_one_entry_from_created():
    o, tr = order("S:NEW", "2026-10-07", [])
    p = ork.path(o, tr, NAMES, TODAY, cal)
    assert p == [[1, "2026-10-07", 12]]
    check_tail(o, p)


def test_facts_floor_and_delivered_without_utd():
    # стадия отстала: «Подготовка груза» (шаг 10), а доставлено клиенту 05.10 и УПД нет — шаг 12
    # от доставки (как step_now и clock), а не 10
    o, tr = order("S:TRUCK", "2026-07-01", [("S:NEW", "2026-07-01"), ("S:SENT", "2026-07-02"),
                                            ("S:PROD", "2026-07-10"), ("S:TRUCK", "2026-09-01")],
                  delFact=dt.date(2026, 10, 5), utd=False)
    p = ork.path(o, tr, NAMES, TODAY, cal)
    assert [x[0] for x in p] == [1, 4, 7, 10, 12]
    assert p[-1] == [12, "2026-10-05", 14]
    check_tail(o, p)
    # отгрузка поставщика 20.08 (шаг 9) раньше стадии: шаг поднят фактом с его дня
    o2, tr2 = order("S:PROD", "2026-07-01", [("S:NEW", "2026-07-01"), ("S:PROD", "2026-07-10")],
                    shipFact=dt.date(2026, 8, 20))
    p2 = ork.path(o2, tr2, NAMES, TODAY, cal)
    assert [x[:2] for x in p2] == [[1, "2026-07-01"], [7, "2026-07-10"], [9, "2026-08-20"]]
    check_tail(o2, p2)


def test_unknown_stage_keeps_step():
    o, tr = order("S:PROD", "2026-09-01", [("S:NEW", "2026-09-01"), ("S:OLD", "2026-09-05"),
                                           ("S:PROD", "2026-09-15")])
    p = ork.path(o, tr, NAMES, TODAY, cal)
    assert [x[0] for x in p] == [1, 7]                           # стадия не из таблицы шаг не меняет


def test_unknown_current_stage_closes_last_step():
    """Стадия сейчас не из таблицы (ревью 30.10.2026): последний известный шаг кончается в день входа
    в неё, последняя запись — [None, день, р.д.]; шаг 5 не тянется «→ сейчас» и не судится нормой."""
    o, tr = order("S:OLD", "2026-09-01", [("S:NEW", "2026-09-01"), ("S:SPEC", "2026-09-03"),
                                          ("S:OLD", "2026-09-20")])
    assert ork.step_now(o) is None
    p = ork.path(o, tr, NAMES, TODAY, cal)
    assert p == [[1, "2026-09-01", 2], [5, "2026-09-03", 17], [None, "2026-09-20", 29]]
    check_tail(o, p)
    # только переход в неизвестную стадию — одна запись «вне регламента»
    o2, tr2 = order("S:OLD", "2026-08-10", [("S:OLD", "2026-08-10")])
    p2 = ork.path(o2, tr2, NAMES, TODAY, cal)
    assert p2 == [[None, "2026-08-10", 70]]
    check_tail(o2, p2)
    # истории нет — с входа в стадию (stageSince), без шага
    o3, tr3 = order("S:OLD", "2026-08-10", [])
    p3 = ork.path(o3, tr3, NAMES, TODAY, cal)
    assert [x[0] for x in p3] == [None]
    check_tail(o3, p3)


def test_contracts_rows_tail_invariant():
    """Каждый живой заказ синтетики: последняя запись пути = step, stepFrom, stepWd; закрытые —
    без пути; в пути ни имён, ни денег — только шаг, ISO-дата и число."""
    from tests import fixture
    ct = fixture.build_contracts()
    live = 0
    for r in ct["rows"]:
        for o in r.get("orders") or []:
            if not o.get("live"):
                assert "path" not in o
                continue
            live += 1
            p = o["path"]
            assert p, o["id"]
            for i, (st, day, wd) in enumerate(p):
                if st is None:                     # «стадия вне регламента» — только последней записью
                    assert i == len(p) - 1 and o["step"] is None, o["id"]
                    continue
                assert isinstance(st, int) and 1 <= st <= 13
                dt.date.fromisoformat(day)
                assert wd is None or isinstance(wd, int)
            assert [x[1] for x in p] == sorted(x[1] for x in p)          # по времени
            assert p[-1][0] == o["step"], o["id"]
            if o["stepFrom"]:
                assert p[-1][1] == o["stepFrom"] and p[-1][2] == o["stepWd"], o["id"]
    assert live > 10
