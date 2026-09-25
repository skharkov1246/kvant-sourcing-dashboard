"""Справочник материалов: материал в тексте, ячейка-материал, марка вместо кода,
материал вместо бренда.

24.09.2026 марка стали «SS316» показывалась брендом и кодом детали. Справочник
dict/material.json учит систему отличать материал (марку, полимер, эластомер,
торговое название материала) от кода и от бренда. Корпус придуман (правило 18).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library import brands, codes_sql, docfilter, materials, oem_kind

ROOT = Path(__file__).resolve().parents[1]


def ключи(строка):
    return [x["key"] for x in materials.материал_в_тексте(строка)]


# ── Файл справочника ─────────────────────────────────────────────────────────

def test_справочник_цел():
    сп = json.loads((ROOT / "dict/material.json").read_text(encoding="utf-8"))
    группы = {g["key"] for g in сп["groups"]}
    ключи_записей = [r["key"] for r in сп["records"]]
    assert len(ключи_записей) == len(set(ключи_записей)), "ключи материалов повторяются"
    виды = {"обозначение", "название", "аббревиатура", "торговое название материала", "номер EN"}
    for r in сп["records"]:
        assert r["group"] in группы, r["key"]
        assert r["name"] and r["use"], r["key"]
        assert r["spellings"], r["key"]
        for s in r["spellings"]:
            assert s["kind"] in виды, (r["key"], s)
            assert s["text"].strip() == s["text"] and s["text"], (r["key"], s)
    # Счётчики в файле — те же, что считает модуль: файл ведётся руками, и
    # забытый пересчёт виден здесь, а не в отчёте.
    св = materials.сводка()
    assert сп["count"] == св["count"] and сп["spellings_count"] == св["spellings_count"]
    assert сп["by_kind"] == св["by_kind"] and сп["groups"] == св["groups"]
    for x in сп["not_a_code"]:
        assert set(x["records"]) <= set(ключи_записей), x["what"]


def test_все_группы_задачи_есть():
    группы = {r["group"] for r in materials.справочник()["records"]}
    for g in ("сталь_углеродистая", "сталь_легированная", "сталь_нержавеющая", "чугун", "медный_сплав",
              "алюминиевый_сплав", "титановый_сплав", "никелевый_сплав", "твёрдый_сплав", "полимер",
              "эластомер", "уплотнительный", "керамика", "смазка"):
        assert g in группы, g


def test_каждое_написание_находит_свою_запись():
    """Написание, которое справочник не находит сам в себе, — мёртвое."""
    for r in materials.справочник()["records"]:
        for s in r["spellings"]:
            t = s["text"]
            if s.get("context"):
                t = "материал " + t
            assert r["key"] in ключи(t), (r["key"], s["text"], ключи(t))


def test_ключ_написания_у_одной_записи():
    """Одно написание — один материал: иначе находка зависит от порядка записей."""
    чьи: dict[str, set] = {}
    for r in materials.справочник()["records"]:
        for s in r["spellings"]:
            чьи.setdefault(materials.ключ(s["text"]), set()).add(r["key"])
    assert not {k: v for k, v in чьи.items() if len(v) > 1}


# ── Материал в тексте ────────────────────────────────────────────────────────

@pytest.mark.parametrize("строка,ключ,написание", [
    ("Уплотнение PTFE 50х70", "ptfe", "PTFE"),
    ("Кольцо NBR 70 Shore", "nbr", "NBR"),
    ("Вал 40Х", "40х", "40Х"),
    ("Корпус 12Х18Н10Т", "12х18н10т", "12Х18Н10Т"),
    ("Корпус 12X18H10T латиницей", "12х18н10т", "12X18H10T"),
    ("SS316 болт М12", "aisi316", "SS316"),
    ("Болт М12 SS 316L", "aisi316l", "SS 316L"),
    ("Втулка БрАЖ9-4 ф40", "браж9-4", "БрАЖ9-4"),
    ("Корпус СЧ20", "сч", "СЧ20"),
    ("Кольцо Viton 20х3", "fkm", "Viton"),
    ("Кольцо уплотнительное из фторопласта", "ptfe", "фторопласта"),
    ("Прокладка паронит ПОН-Б 2 мм", "паронит", "паронит"),
    ("Пара торцевая SiC/SiC", "sic", "SiC"),
    ("Лопатка Inconel 718", "inconel718", "Inconel 718"),
    ("Шпилька A193 B7", "b7", "A193 B7"),
    ("Втулка из PA", "pa", "PA"),
    ("Кольцо CR 70 шор", "cr", "CR"),
    ("Сальник NBR/CR", "cr", "CR"),
])
def test_материал_найден(строка, ключ, написание):
    найдено = materials.материал_в_тексте(строка)
    assert any(x["key"] == ключ and x["text"] == написание for x in найдено), найдено
    for x in найдено:
        assert строка[x["start"]:x["end"]] == x["text"]


@pytest.mark.parametrize("строка", [
    "PA-100", "Втулка PA-100", "ПСЧ20", "KV-PTFE-1", "Подшипник 6316", "Подшипник NU 316 ECP",
    "1.4301", "Клапан PP", "Шайба ф4", "Кольцо 316", "Позиция 7075",
])
def test_материала_нет(строка):
    assert materials.материал_в_тексте(строка) == [], materials.материал_в_тексте(строка)


def test_короткое_написание_только_с_контекстом():
    assert ключи("Корпус насоса, материал PP") == ["pp"]
    assert ключи("нерж. сталь 316") == ["aisi316"]
    assert ключи("Werkstoff 1.4301") == ["aisi304"]
    assert ключи("Корпус PP") == []


# ── Ячейка целиком и код ─────────────────────────────────────────────────────

@pytest.mark.parametrize("ячейка", ["Viton", "PTFE", "SS316", "AISI 316L", "Сталь 40Х", "Материал: PTFE",
                                    "12Х18Н10Т", "Kalrez", "PP", "резина NBR", "нерж. сталь 316"])
def test_ячейка_материал(ячейка):
    assert materials.ячейка_материал(ячейка), ячейка


@pytest.mark.parametrize("ячейка", ["SKF", "Parker", "PA-100", "316", "1.4301", "Кольцо NBR", "Stellite",
                                    "W. L. Gore (Gore-Tex)", "", "Корпус 12Х18Н10Т"])
def test_ячейка_не_материал(ячейка):
    assert materials.ячейка_материал(ячейка) is None, ячейка


@pytest.mark.parametrize("строка,код", [
    ("Уплотнение PTFE 50х70", ""),
    ("Кольцо NBR 70 Shore", ""),
    ("Вал 40Х", ""),
    ("Корпус 12Х18Н10Т", ""),
    ("SS316 болт М12", ""),
    ("Пруток ВТ1-0 ф20", ""),
    ("Втулка БрАЖ9-4 KV-4417-B", "KV-4417-B"),
    ("Клапан 12Х18Н10Т DN50 KV-4417-B", "KV-4417-B"),
    ("Втулка PA-100", "PA-100"),
    ("Кольцо PA6 PA-100", "PA-100"),
    ("Подшипник SKF 22315 EK", "22315"),
])
def test_материал_не_код(строка, код):
    assert docfilter.part_number_of(строка) == код


@pytest.mark.parametrize("токен,марка", [
    ("ВТ1-0", True), ("БрАЖ9-4", True), ("PA6", True), ("42CrMo4", True), ("316L", True), ("СЧ20", True),
    ("PA-100", False), ("KV-4417-SS316", False), ("6316", False), ("316", False), ("22315", False),
])
def test_марка_ли(токен, марка):
    assert materials.марка_ли(токен) is марка


def test_марка_правила_кода_из_справочника_без_изменений():
    """Класс «марка» правила правдоподобия кода собирается из справочника и
    обязан совпасть буква в букву с тем, что вписано в lib_pn_plausible."""
    assert dict(docfilter._КЛАССЫ_НЕ_КОДА)["марка"] == materials.выражение_не_кода()
    текст = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    assert materials.выражение_не_кода() in текст


# ── Материал — не бренд ──────────────────────────────────────────────────────

def test_viton_не_бренд_позиции():
    assert brands.ключи_ячейки("Viton", {}) == []
    assert brands.ключи_ячейки("Viton / Parker", {}) == ["parker"]
    # Бренд словаря сильнее пометки.
    assert brands.ключи_ячейки("Viton", {"viton": "chemours"}) == ["chemours"]


def test_вид_записи_материал():
    вид = oem_kind.вид_записи("Viton", "viton", {})
    assert вид["kind"] == oem_kind.МАТЕРИАЛ and oem_kind.МАТЕРИАЛ in oem_kind.БЕЗ_БРЕНДА
    assert oem_kind.вид_записи("SS316", "ss316", {})["kind"] == oem_kind.МАТЕРИАЛ
    assert oem_kind.вид_записи("Parker", "parker", {})["kind"] == oem_kind.БРЕНД
    assert oem_kind.вид_записи("Stellite", "stellite", {})["kind"] == oem_kind.БРЕНД
    # Короткое написание (TC — карбид вольфрама) бренда не обвиняет: так же
    # пишут сокращённое имя компании.
    assert oem_kind.вид_записи("TC", "tc", {})["kind"] == oem_kind.БРЕНД
    assert materials.ячейка_материал("TC") and not materials.ячейка_материал("TC", короткие=False)
    assert not oem_kind.бренд_ли({"kind": oem_kind.МАТЕРИАЛ})


def test_ключи_запроса_brands_совпадают_с_ключом_написания():
    """Ключи материалов вписаны в запрос /brands; считаются своим правилом, потому
    что codes_sql грузит справочник раньше, чем объявляет ключ_написания."""
    for r in materials.справочник()["records"]:
        for s in r["spellings"]:
            k = materials._ключ_бренда(s["text"])
            if k in codes_sql.MATERIAL_KEYS:
                assert k == codes_sql.ключ_написания(s["text"]), s["text"]
    assert "viton" in codes_sql.MATERIAL_KEYS and "ptfe" in codes_sql.MATERIAL_KEYS
    assert "stellite" not in codes_sql.MATERIAL_KEYS
    assert "'материал, а не бренд'" in codes_sql.BRAND_PIPELINE


def test_словарь_брендов_помечен_а_не_почищен():
    """На dict/oem.json материалов-записей нет (замер 25.09.2026): вид есть в
    счётчике, записи не удаляются."""
    сп = json.loads((ROOT / "dict/oem.json").read_text(encoding="utf-8"))
    assert "материал" in сп["by_kind"]
    assert сп["by_kind"]["материал"] == sum(1 for r in сп["records"] if r["kind"] == "материал")
