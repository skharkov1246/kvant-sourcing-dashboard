"""Бренд машины → компания-владелец: library/brand_owner.py и поля владения
в dict/model_series.json и dict/oem.json.

Корпус придуманный (правило 18): марки Kelton, Brisko, Tarvo и холдинги
Grifon, Ostara — их нет ни в одной базе.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from library import brand_owner as bo

ROOT = Path(__file__).resolve().parents[1]
ИСТ = "https://example.invalid/about"

КОРПУС = {
    "brands": [
        {"brand_key": "kelton", "name": "Kelton Turbines", "aliases": ["Kelton"], "role": "бренд",
         "owner": "grifon", "owner_since": 1990, "owner_source": ИСТ,
         "owner_history": [{"owner": "Zamek Holding", "role": "бывший владелец",
                            "owner_since": 1970, "owner_until": 1990, "owner_source": ИСТ}],
         "series": [{"id": "kelton-k", "series": "K"}]},
        {"brand_key": "brisko", "name": "Brisko Pumps", "aliases": ["Brisko"], "role": "бренд",
         "owner": "ostara", "owner_since": 2008, "owner_source": ИСТ,
         "series": [{"id": "brisko-bt", "series": "BT"},
                    {"id": "brisko-old", "series": "OLD", "owner": "kelton", "owner_since": 2015,
                     "owner_source": ИСТ}]},
        {"brand_key": "tarvo", "name": "Tarvo", "aliases": ["Tarvo"], "role": "бренд",
         "owner_history": [{"owner": "grifon", "role": "бывший владелец", "owner_until": 2018,
                            "owner_source": ИСТ}],
         "series": []},
    ],
    "owners": [
        {"brand_key": "grifon", "name": "Grifon Group", "role": "владелец"},
        {"brand_key": "ostara", "name": "Ostara Flow", "role": "владелец",
         "owner": "grifon", "owner_source": ИСТ},
    ],
}


def test_владелец_и_подпись():
    в = bo.владелец("kelton", КОРПУС)
    assert в["key"] == "grifon" and в["name"] == "Grifon Group" and в["since"] == 1990
    assert в["role"] == "владелец" and в["source"] == ИСТ
    assert bo.подпись("kelton", КОРПУС) == "входит в Grifon Group с 1990"
    assert bo.владелец("tarvo", КОРПУС) is None and bo.подпись("tarvo", КОРПУС) is None
    assert bo.владелец("нет-такого", КОРПУС) is None


def test_бренд_не_сливается_с_владельцем():
    """Бренд остаётся своим ключом: запись владельца — отдельная, ссылка — owner."""
    зап = bo.записи(КОРПУС)
    assert зап["kelton"]["name"] == "Kelton Turbines" and зап["grifon"]["name"] == "Grifon Group"
    assert "kelton" not in {к for к, р in зап.items() if р.get("role") == "владелец"}


def test_имя_вместо_ключа():
    данные = copy.deepcopy(КОРПУС)
    данные["brands"][2]["owner"] = "Vidumka Capital LP"
    данные["brands"][2]["owner_source"] = ИСТ
    в = bo.владелец("tarvo", данные)
    assert в["key"] is None and в["name"] == "Vidumka Capital LP"
    assert bo.проверить(данные) == []


def test_цепочка_и_бренды_группы():
    assert [в["key"] for в in bo.цепочка_владельцев("brisko", КОРПУС)] == ["ostara", "grifon"]
    группа = bo.бренды_владельца("grifon", КОРПУС)
    assert [э["key"] for э in группа] == ["brisko", "kelton", "ostara"]
    assert next(э for э in группа if э["key"] == "brisko")["via"] == "ostara"
    assert [э["key"] for э in bo.бренды_владельца("grifon", КОРПУС, вглубь=False)] == ["kelton", "ostara"]
    # бывший владелец не даёт бренд в группу
    assert "tarvo" not in {э["key"] for э in группа}


def test_история_и_ряды():
    h = bo.бывшие_владельцы("tarvo", КОРПУС)
    assert h == [{"key": "grifon", "name": "Grifon Group", "role": "бывший владелец",
                  "until": 2018, "source": ИСТ}]
    h = bo.бывшие_владельцы("kelton", КОРПУС)
    assert h[0]["key"] is None and h[0]["name"] == "Zamek Holding" and h[0]["since"] == 1970
    assert bo.ряды_владельца("kelton", КОРПУС) == [
        {"brand": "brisko", "series_id": "brisko-old", "series": "OLD", "since": 2015}]


def test_проверка_ловит_ошибки():
    assert bo.проверить(КОРПУС) == []

    def с_правкой(f):
        д = copy.deepcopy(КОРПУС)
        f(д)
        return " | ".join(bo.проверить(д))

    assert "без записи" in с_правкой(lambda д: д["brands"][0].update(owner="nikto"))
    assert "нет owner_source" in с_правкой(lambda д: д["brands"][0].pop("owner_source"))
    assert "role" in с_правкой(lambda д: д["brands"][0].update(role="марка"))
    assert "role не «владелец»" in с_правкой(lambda д: д["owners"][0].update(role="бренд"))
    assert "не «бывший владелец»" in с_правкой(
        lambda д: д["brands"][0]["owner_history"][0].update(role="владелец"))
    assert "позже" in с_правкой(lambda д: д["brands"][0]["owner_history"][0].update(owner_since=1999))
    assert "не год" in с_правкой(lambda д: д["brands"][0].update(owner_since="1990"))
    assert "у владельца есть ряды" in с_правкой(lambda д: д["owners"][0].update(series=[{"id": "x"}]))
    assert "нет owner_source" in с_правкой(lambda д: д["brands"][1]["series"][1].pop("owner_source"))
    # цикл: grifon → ostara → grifon; обход цепочки при этом не зацикливается
    цикл = copy.deepcopy(КОРПУС)
    цикл["owners"][0].update(owner="ostara", owner_source=ИСТ)
    assert any("цикл" in о for о in bo.проверить(цикл))
    assert len(bo.цепочка_владельцев("brisko", цикл)) <= 3
    assert "сам собой" in с_правкой(lambda д: д["owners"][0].update(owner="grifon", owner_source=ИСТ))


# ─── настоящий справочник ────────────────────────────────────────────────────

def _файл():
    return json.loads((ROOT / "dict" / "model_series.json").read_text(encoding="utf-8"))


def test_справочник_цел():
    """У каждого owner есть запись (или это имя), цикла нет, role из списка,
    у каждой связи — owner_source."""
    assert bo.проверить(_файл()) == []
    assert _файл()["roles"] == list(bo.РОЛИ)


def test_записи_владельцев_не_двойники_словаря():
    """Новый ключ владельца не повторяет имя другой записи dict/oem.json."""
    oem = json.loads((ROOT / "dict" / "oem.json").read_text(encoding="utf-8"))
    ключи = {r["oem_key"] for r in oem["records"]}
    имена = {r["name"].lower(): r["oem_key"] for r in oem["records"]}
    for р in _файл()["owners"]:
        assert р["brand_key"] in ключи or р["name"].lower() not in имена, р["brand_key"]
        assert not р.get("aliases") and not р.get("series"), р["brand_key"]


def test_известные_связи():
    assert bo.владелец("solarturbines")["key"] == "caterpillar"
    assert bo.подпись("solarturbines") == "входит в Caterpillar с 1981"
    группа = {э["key"] for э in bo.бренды_владельца("caterpillar")}
    assert {"solarturbines", "perkinsfgwilson", "mwmcaterpillarenergy"} <= группа
    assert [в["key"] for в in bo.цепочка_владельцев("wilden")] == ["psg", "dover"]
    assert bo.владелец("epiroc") is None
    assert [h["key"] for h in bo.бывшие_владельцы("epiroc")] == ["atlascopco"]
    assert {r["series_id"] for r in bo.ряды_владельца("siemensenergy")} == {"rr-rb211", "rr-avon"}


def _сборщик():
    spec = importlib.util.spec_from_file_location("kvant_build_dict_owner", ROOT / "scripts" / "build_dict.py")
    м = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(м)
    return м


def test_сборщик_переносит_владение_без_смены_ключей():
    bd = _сборщик()
    поля = bd.поля_владения({
        "brands": [{"brand_key": "keltonturbines", "name": "Kelton Turbines", "role": "бренд",
                    "aliases": ["Kelton Turbines", "Grifon Kelton", "Общее Имя"], "owner": "grifon",
                    "owner_since": 1990, "owner_history": [{"owner": "Zamek"}]},
                   {"brand_key": "brisko", "name": "Brisko", "role": "бренд", "aliases": ["Общее Имя"]},
                   {"brand_key": "safe", "name": "Safe"}],
        "owners": [{"brand_key": "grifon", "name": "Grifon Group", "role": "владелец"}]})
    assert поля["keltonturbines"] == {"role": "бренд", "owner": "grifon", "owner_since": 1990,
                                      "former_owners": ["Zamek"]}
    assert поля["grifonkelton"]["brand_key"] == "keltonturbines"
    assert поля["grifon"] == {"role": "владелец"}
    assert "общееимя" not in поля       # написание двух брендов не сводит ни к одному
    assert "safe" not in поля           # запись без role не размечается


def test_словарь_размечен_только_у_брендов():
    oem = json.loads((ROOT / "dict" / "oem.json").read_text(encoding="utf-8"))
    по_ключу = {r["oem_key"]: r for r in oem["records"]}
    for r in oem["records"]:
        if "role" in r:
            assert r["kind"] == "бренд", r["oem_key"]
            assert r["role"] in bo.РОЛИ
    assert по_ключу["solarturbines"]["owner"] == "caterpillar"
    assert по_ключу["solarturbines"]["name"] != по_ключу["caterpillar"]["name"]
