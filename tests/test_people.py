"""Тесты вкладок «КАМы» и «Продукт-оунеры» (people.py) — без сети и без Bitrix.

Корпус придуман (правило репозитория: тестовые данные не копируются из базы), но
форма записей повторяет ответы Bitrix. Проверяется то, ради чего модуль и писался:
кто считается сотрудником, чья сделка, что такое просрочка и что не приписывается никому.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import people as people_mod   # noqa: E402
from tests import fixture     # noqa: E402

USERS, FIRED, OPEN_DEALS = fixture.USERS, fixture.FIRED, fixture.OPEN_DEALS


def build():
    return fixture.build_people()


# ------------------------------------------------------------------ состав
def test_уволенный_не_попадает_в_роль_а_его_сделка_в_бесхозные():
    r = build()
    kam_ids = {p["uid"] for p in r["roles"]["kam"]["people"]}
    assert "5" not in kam_ids                      # уволенный КАМ не занимает строку в роли
    assert r["orphan"]["people"] == 1 and r["orphan"]["n"] == 1
    assert r["staff"]["fired"] == 1 and r["staff"]["active"] == len(USERS)


def test_роль_определяется_должностью_или_отделом_а_сорсер_не_коммерсант():
    r = build()
    kam = {p["uid"]: p for p in r["roles"]["kam"]["people"]}
    prod = {p["uid"]: p for p in r["roles"]["prod"]["people"]}
    assert kam["1"]["why"] == "должность"          # Key Account Manager
    assert kam["4"]["why"] == "отдел"              # Project Manager в клиентской группе
    assert prod["2"]["why"] == "должность"         # Head of compressor department
    assert "3" not in kam and "3" not in prod      # сорсер не коммерсант ни в одной роли


def test_активный_в_роли_без_сделок_показан_отдельно():
    r = build()
    assert [p["uid"] for p in r["roles"]["prod"]["idlePeople"]] == ["6"]


# ------------------------------------------------------------------ атрибуция
def test_поле_кам_важнее_ответственного():
    r = build()
    kam = {p["uid"]: p for p in r["roles"]["kam"]["people"]}
    assert "3" not in kam                          # ответственный-сорсер не получает сделку 101
    assert kam["1"]["open"] == 3                   # 101, 102, 108 (техническая 107 исключена)
    assert kam["1"]["byField"] == 3


def test_сделка_без_роли_не_приписывается_никому_а_считается_отдельно():
    r = build()
    assert r["roles"]["kam"]["uncovered"]["n"] >= 1
    assert r["recon"]["none"] == 1                 # сделка 104 — ничья (у 106 владелец уволен)
    assert r["recon"]["orphan"] == 1 and r["recon"]["orphanCovered"] == 0
    assert r["recon"]["openTotal"] == len(OPEN_DEALS) - 1   # техническая воронка вычтена
    assert r["recon"]["tech"] == 1


# ------------------------------------------------------------------ состояния и деньги
def test_реализация_отличается_от_проработки():
    r = build()
    kam = {p["uid"]: p for p in r["roles"]["kam"]["people"]}
    assert kam["1"]["real"] == 1 and kam["1"]["realRaw"] == 500_000     # сделка 102
    assert kam["1"]["presale"] == 2                                     # 101 и 108
    assert kam["1"]["marginRaw"] == 100_000                             # 500K продажа − 400K закупка


def test_проигранный_заказ_не_делает_сделку_реализацией():
    r = build()
    d104 = next(x for x in r["deals"] if x["id"] == "104")
    assert d104["state"] == "presale" and d104["buyRaw"] == 0


# ------------------------------------------------------------------ просрочка и косяки
def test_просрочка_берётся_из_дедлайна_заказа_а_не_из_даты_сделки():
    r = build()
    kam = {p["uid"]: p for p in r["roles"]["kam"]["people"]}
    assert kam["1"]["late"] == 1
    d102 = next(x for x in r["deals"] if x["id"] == "102")
    assert d102["late"] == 47


def test_застой_считается_по_смене_стадии():
    r = build()
    d106 = next(x for x in r["deals"] if x["id"] == "106")
    assert d106["idle"] > people_mod.DEAD_DAYS
    assert r["orphan"]["rows"][0]["dead"] == 1


def test_гигиена_ловит_сделку_без_суммы_и_без_клиента():
    r = build()
    kam = {p["uid"]: p for p in r["roles"]["kam"]["people"]}
    assert kam["1"]["noAmt"] == 1 and kam["1"]["noComp"] == 1
    assert kam["1"]["flaws"] >= 2 and kam["1"]["cleanPct"] is not None


# ------------------------------------------------------------------ итоги роли
def test_победа_это_перевод_в_реализацию_а_не_семантика_стадии():
    t = build()["roles"]["kam"]["totals"]
    # 102 — воронка реализации, 203 — номер реализации в названии: обе победы.
    # 201 помечена семантикой «успех», но в реализацию не попала — не победа.
    assert t["won"] == 2 and t["lost"] == 1


def test_итоги_роли_считают_конверсию_и_взвешенный_пайплайн_отдельно():
    t = build()["roles"]["kam"]["totals"]
    assert t["winRate"] == round(t["won"] / (t["won"] + t["lost"]) * 100)
    # взвешенный пайплайн — оценка: проработка, умноженная на конверсию роли
    assert t["weightedRaw"] == round(t["presaleRaw"] * t["winRate"] / 100)
    assert t["realRaw"] != t["weightedRaw"]        # факт и оценка — разные числа
