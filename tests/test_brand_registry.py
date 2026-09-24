"""Правила засева реестра брендов (этап 8.2) без базы.

Корпус придуман (CLAUDE.md, правило 18). SQL-сторона — в
tests/test_brand_registry_sql.py: там те же правила проходят через настоящую
схему, гейты и откат.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from library import brand_registry as br
from library import load_brands as lb

ROOT = Path(__file__).resolve().parents[1]

СЛОВАРЬ = {"records": [
    {"oem_key": "kelton", "name": "Kelton GmbH", "spellings": [
        {"spelling": "Kelton", "where": "x"}, {"spelling": "Келтон", "where": "y"}]},
    {"oem_key": "alfaone", "name": "Alfa One", "spellings": [{"spelling": "Alfa", "where": "x"}]},
    {"oem_key": "alfatwo", "name": "Alfa Two", "spellings": [{"spelling": "ALFA", "where": "y"}]},
]}
АТЛАС = {"makers": [
    {"name": "Kelton GmbH & Co", "owner": "Выдуманный холдинг"},
    {"name": "Brisko Pumpen OG", "country": "Нигдения"},
    {"name": "Kelton Energy Solutions"},
]}


def test_словарь_атлас_и_спорное():
    п = br.план_файлов(СЛОВАРЬ, АТЛАС, {"келтон-м": "Kelton", "zzq": "Нет такого"})
    assert set(п.бренды) == {"kelton", "alfaone", "alfatwo", "briskopumpenog"}
    assert п.бренды["kelton"]["owner"] == "Выдуманный холдинг"
    статусы = {(н["source"], н["spelling"]): (н["status"], н.get("brand_key")) for н in п.написания}
    assert статусы[("dict/oem.json", "Alfa")] == ("спорно", None)
    assert статусы[("dict/oem.json", "Келтон")] == ("разрешено", "kelton")
    # Имя с вхождением чужого ключа не заводит двойника и не приклеивается.
    assert статусы[("zip/data/oem_atlas.json", "Kelton Energy Solutions")] == ("спорно", None)
    assert статусы[("OEM_ALIAS", "келтон-м")] == ("разрешено", "kelton")
    assert статусы[("OEM_ALIAS", "zzq")] == ("в очереди", None)
    for н in п.написания:
        assert н["spelling_key"] == br.ключ(н["spelling"])


def test_справочник_заводит_бренд_а_компании_нет():
    п = br.план_файлов(СЛОВАРЬ, {}, {})
    бренды, написания = br.план_справочника(п, [(1, "Kelton"), (2, "Grifon Seals"),
                                                (3, "Не указан"), (4, "Kelton Pumps Group")])
    по_номеру = {н["sp176_id"]: н for н in написания}
    assert по_номеру[1]["brand_key"] == "kelton" and "kelton" in бренды
    assert по_номеру[2]["brand_key"] == br.новый_ключ("Grifon Seals") in бренды
    assert по_номеру[3]["status"] == "не бренд"
    assert по_номеру[4]["status"] == "спорно" and по_номеру[4]["candidates"] == ["kelton"]
    компании = br.написания_компаний([(10, "Kelton GmbH"), (11, "Орион Литьё"), (12, "неизвестно")],
                                     п.карта)
    assert [(н["status"], н.get("brand_key")) for н in компании] == [
        ("разрешено", "kelton"), ("в очереди", None), ("не бренд", None)]


def test_части_ячейки_разрешают_только_однозначно():
    карта = {"kelton": {"kelton"}, "fag": {"fag"}}
    assert br.разрешить("Kelton (Германия)", карта) == ("разрешено", {"kelton"})
    assert br.разрешить("Kelton/FAG", карта) == ("спорно", {"kelton", "fag"})
    assert br.разрешить("Выдумка", карта) == ("в очереди", set())


def test_написания_данных_по_суждению_запроса():
    строки = [("Kelton", "kelton", "kelton", True, None, 5),
              ("Не указан", "неуказан", "неуказан", False, "пометка незнания или стоп-слово", 3),
              ("Акмеро", "акмеро", "акмеро", False, None, 2)]
    out = br.написания_данных("lib_prices.oem", строки)
    assert [(н["status"], н.get("brand_key"), н["n_rows"]) for н in out] == [
        ("разрешено", "kelton", 5), ("не бренд", None, 3), ("в очереди", None, 2)]


def test_без_повторов_складывает_строки_данных():
    a = br._написание("Kelton", "lib_prices.oem", status="разрешено", brand_key="kelton", n_rows=2)
    b = br._написание("Kelton ", "lib_prices.oem", status="разрешено", brand_key="kelton", n_rows=3)
    out = lb.без_повторов([a, b])
    assert len(out) == 1 and out[0]["n_rows"] == 5


def test_каждая_запись_файлов_ровно_в_одной_части():
    п = br.план_файлов(json.loads((ROOT / "dict/oem.json").read_text(encoding="utf-8")),
                       json.loads((ROOT / "zip/data/oem_atlas.json").read_text(encoding="utf-8")))
    for частей in (10, 12, 25, 50):
        счёт = [0] * частей
        for н in п.написания:
            счёт[br.часть_ключа(н.get("brand_key") or н["spelling_key"] or н["spelling"], частей)] += 1
        assert sum(счёт) == len(п.написания)
    # На живом словаре план не пуст, ключи брендов словаря сохранены как есть.
    assert {r["oem_key"] for r in json.loads((ROOT / "dict/oem.json").read_text(encoding="utf-8"))["records"]} \
        <= set(п.бренды)


def test_сверка_правил_считает_расхождения():
    правила = {"грубое": lambda s: s.lower()[:3], "точное": br.ключ}
    итог = {r["rule"]: r for r in br.сверка_правил(["Kelton", "KELTON", "Kelvin", "Kelton GmbH"],
                                                   правила)}
    assert итог["точное"]["merges"] == 0 and итог["точное"]["splits"] == 0
    assert итог["грубое"]["merges"] == 1      # «kel» склеивает kelton и kelvin
    assert итог["грубое"]["splits"] == 0


def test_прогон_частями_и_вхолостую():
    wf = yaml.safe_load((ROOT / ".github/workflows/library-brands.yml").read_text(encoding="utf-8"))
    входы = wf[True]["workflow_dispatch"]["inputs"]
    assert входы["apply"]["default"] is False
    assert min(int(x) for x in входы["shards"]["options"]) >= 10
    assert wf["concurrency"]["group"] == "bitrix-portal"
    текст = (ROOT / ".github/workflows/library-brands.yml").read_text(encoding="utf-8")
    assert "BITRIX_PARALLEL" in текст and "BITRIX_RPS" in текст
    assert "library/supabase/brands_schema.sql" in (
        ROOT / ".github/workflows/zip-db.yml").read_text(encoding="utf-8")
