"""Имена компаний из карточек Битрикса: пачки, выбор, гейты, прогон, папка «Ждут ИНН».

Без базы и без портала: портал подменён записывающим клиентом, строки базы —
кортежами. Корпус придуман (правило 18). Проверки держатся за поведение — что
ушло в портал и что получилось, — а не за спелинг кода.
"""
from __future__ import annotations

import collections
import importlib.util
import pathlib

import yaml

from library import company_names as cn

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "publish_suppliers_names", ROOT / "scripts" / "publish_suppliers.py")
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)


# ── Похоже на ключ ──────────────────────────────────────────────────────────

КЛЮЧИ = ["supremevalves", "ethosenergybloomfieldwgpwindustrialturbineservices",
         "bitrix:2002", "ромашка", "abc123", "", "   ", None]
ИМЕНА = ["Supreme Valves", "ООО «Ромашка»", "Учебный завод", "ACME", "acme pumps",
         "Acme-Pumps", "3M", "Бета Сервис"]


def test_ключи_узнаются_а_имена_нет():
    assert all(cn.как_ключ(k) for k in КЛЮЧИ)
    assert not any(cn.как_ключ(n) for n in ИМЕНА)


# ── Портал: пачки по ключу, без смещения ─────────────────────────────────────

class Портал:
    """Записывает вызовы; отвечает из придуманных карточек и реквизитов."""

    def __init__(self, компании=None, реквизиты=None):
        self.вызовы: list[tuple[str, dict]] = []
        self.компании = компании or {}
        self.реквизиты = реквизиты or []

    def call(self, method, params):
        self.вызовы.append((method, params))
        f = params["filter"]
        if method == "crm.company.list":
            return [{"ID": str(i), "TITLE": self.компании[i]} for i in f["@ID"]
                    if i in self.компании][:50]
        if method == "crm.requisite.list":
            после = f.get(">ID", 0)
            строки = [r for r in self.реквизиты
                      if int(r["ENTITY_ID"]) in f["@ENTITY_ID"] and int(r["ID"]) > после]
            return sorted(строки, key=lambda r: int(r["ID"]))[:50]
        raise AssertionError(method)


def test_пачки_не_больше_пятидесяти_без_повторов_и_нулей():
    п = cn.пачки(list(range(0, 130)) + [5, "7", "x", None, " 9 "])
    assert all(len(x) <= 50 for x in п)
    плоско = [i for x in п for i in x]
    assert плоско == sorted(set(плоско)) and 0 not in плоско
    assert len(плоско) == 129


def test_названия_читаются_пачками_по_ID_без_смещения():
    портал = Портал({i: f"Компания {i}" for i in range(1, 121)})
    титулы = cn.названия(портал, range(1, 121))
    assert len(титулы) == 120
    assert len(портал.вызовы) == 3                      # 50 + 50 + 20
    for method, params in портал.вызовы:
        assert method == "crm.company.list"
        assert len(params["filter"]["@ID"]) <= 50
        assert params["start"] == -1                    # без подсчёта total
        assert set(params["select"]) == {"ID", "TITLE"}


def test_реквизиты_дочитываются_по_ключу_а_не_смещением():
    # У компании 1 — 60 строк реквизитов: страница из 50 полна, нужна дочитка.
    строки = [{"ID": str(n), "ENTITY_ID": "1", "RQ_INN": ""} for n in range(1, 61)]
    строки.append({"ID": "100", "ENTITY_ID": "2", "RQ_INN": "0000000002"})
    портал = Портал(реквизиты=строки)
    out = cn.реквизиты(портал, [1, 2])
    assert len(out[1]) == 60 and len(out[2]) == 1
    assert [p["filter"][">ID"] for _, p in портал.вызовы] == [0, 50]
    for method, params in портал.вызовы:
        assert method == "crm.requisite.list"
        assert params["filter"]["ENTITY_TYPE_ID"] == 4
        assert len(params["filter"]["@ENTITY_ID"]) <= 50
        assert params["start"] == -1
        assert "RQ_INN" in params["select"] and "RQ_COMPANY_NAME" in params["select"]


# ── Выбор и запись ───────────────────────────────────────────────────────────

def test_название_похожее_на_ключ_не_берётся():
    assert cn.имя_из_названий(["supremevalves", "Supreme Valves Ltd", "SV"]) == "Supreme Valves Ltd"
    assert cn.имя_из_названий(["supremevalves", " "]) == ""


def test_реквизиты_краткое_имя_первым_разные_инн_не_берутся():
    r = cn.из_реквизитов([{"RQ_COMPANY_NAME": "ООО «Ромашка»",
                           "RQ_COMPANY_FULL_NAME": "Общество с ограниченной ответственностью «Ромашка»",
                           "RQ_INN": "0000000001"}])
    assert r["name"] == "ООО «Ромашка»" and r["inn"] == "0000000001"
    спор = cn.из_реквизитов([{"RQ_INN": "0000000001"}, {"RQ_INN": "0000000002"}])
    assert спор["inn"] == "" and спор["inn_conflict"]


def test_собрать_пишет_только_изменившееся_и_помнит_прежнее():
    сущности = {"KV-S-000001-8": [11], "KV-S-000002-6": [22], "KV-S-000003-4": [33]}
    титулы = {11: "Учебный завод", 22: "Учебная мастерская", 33: "uchebnoebyuro"}
    реквиз = {33: [{"RQ_COMPANY_NAME": "АО «Учебное бюро»", "RQ_INN": "0000000003"}]}
    текущие = {
        # Не изменилось — строки не будет.
        ("KV-S-000001-8", "bitrix:title"): {"name": "Учебный завод", "card_id": "11"},
        # Переименовали в портале — строка с прежним именем.
        ("KV-S-000002-6", "bitrix:title"): {"name": "Мастерская (старое)", "card_id": "22"},
    }
    строки, сч = cn.собрать(сущности, титулы, реквиз, текущие)
    по = {(s["sup_id"], s["source"]): s for s in строки}
    assert ("KV-S-000001-8", "bitrix:title") not in по
    assert по[("KV-S-000002-6", "bitrix:title")]["previous_name"] == "Мастерская (старое)"
    assert по[("KV-S-000002-6", "bitrix:title")]["name"] == "Учебная мастерская"
    # Название-ключ отвергнуто, имя пришло из реквизитов вместе с ИНН.
    assert ("KV-S-000003-4", "bitrix:title") not in по
    assert по[("KV-S-000003-4", "bitrix:requisite")]["inn"] == "0000000003"
    assert сч["название похоже на ключ — не взято"] == 1
    assert сч["bitrix:title: без изменений"] == 1
    assert not cn.гейты(сч, строки)


def test_гейт_отменяет_запись_когда_портал_молчит():
    сущности = {f"KV-S-00000{i}-0": [i] for i in range(1, 5)}
    строки, сч = cn.собрать(сущности, {1: "Учебный завод"}, {}, {})
    провалы = cn.гейты(сч, строки)
    assert провалы and "портал ответил" in провалы[0]


def test_гейт_ловит_имя_ключ_если_оно_дошло_до_записи():
    сч = collections.Counter({"сущностей": 1, "карточек найдено у сущностей": 1})
    строки = [{"name": "supremevalves", "inn": None}]
    assert cn.гейты(сч, строки)


def test_разбиение_одно_и_полное():
    ключи = [f"KV-S-{i:06d}-0" for i in range(500)]
    for частей in (10, 12, 25, 50):
        где = [[n for n in range(частей) if cn.часть(k, частей, n)] for k in ключи]
        assert all(len(g) == 1 for g in где)      # каждая сущность ровно в одной части


# ── Читатели не падают без вида ──────────────────────────────────────────────

def test_без_вида_подставляется_пустая_выборка():
    sql = "select 1 from sup_entity e\n  left join sup_name_shown nm on nm.sup_id = e.id"
    assert cn.имена_sql(sql, True) == sql
    без = cn.имена_sql(sql, False)
    assert "sup_name_shown" not in без and "where false" in без


# ── Прогон: одна пятая портала ───────────────────────────────────────────────

def test_прогон_держит_пятую_часть_портала():
    wf = yaml.safe_load((ROOT / ".github/workflows/suppliers-names.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert set(on) == {"workflow_dispatch"}                 # расписание решает владелец
    assert on["workflow_dispatch"]["inputs"]["apply"]["default"] is False
    assert on["workflow_dispatch"]["inputs"]["shards"]["options"] == ["10", "12", "25", "50"]
    assert "rollback_run_id" in on["workflow_dispatch"]["inputs"]
    assert wf["concurrency"]["group"] == "bitrix-fifth"
    job = wf["jobs"]["names"]
    assert job["strategy"]["max-parallel"] == 1
    шаг = next(s for s in job["steps"] if "company_names.py" in (s.get("run") or ""))
    rps, parallel = float(шаг["env"]["BITRIX_RPS"]), int(шаг["env"]["BITRIX_PARALLEL"])
    assert rps == 0.3 and parallel == 1
    # Пятая часть умолчания клиента, и ни одна часть не берёт больше.
    import bitrix_client
    assert rps / parallel <= bitrix_client.RPS_ПО_УМОЛЧАНИЮ / 5 + 1e-9


# ── Страница: папка «Ждут ИНН» и имя ─────────────────────────────────────────

def test_ключ_очереди_с_префиксом_портала_даёт_номер_карточки():
    assert ps.номер_карточки("bitrix:101") == "101"
    assert ps.номер_карточки("101") == "101"
    assert ps.номер_карточки("bitrix:0") == "" and ps.номер_карточки(None) == ""
    снимок = ps.собрать([], [], 0, (), [{"reason": "r", "names": ["Учебный"],
                                          "keys": ["bitrix:101", "bitrix:102"]}])
    assert снимок["inn_queue"][0]["cards"] == ["101", "102"]


def test_без_номера_уходит_в_папку_с_номером_остаётся():
    строки = [
        ("KV-S-000001-8", "Учебный завод", "RU", "один источник, сливать не с чем",
         "active", "candidate", True, "bitrix:title"),
        ("KV-S-000002-6", "Учебное бюро", "RU", "x", "active", "candidate", False, None),
    ]
    снимок = ps.собрать(строки, [], 0)
    по = {e["name"]: e for e in снимок["entities"]}
    # Одиночка с номером — в основной таблице: номер выдан, ИНН ей не нужен.
    assert not по["Учебный завод"].get("wait_inn")
    assert по["Учебный завод"]["name_from"] == "bitrix:title"
    assert по["Учебное бюро"]["wait_inn"] is True
    assert снимок["totals"]["wait_inn"] == 1
