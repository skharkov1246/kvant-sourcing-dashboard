"""Взаимозаменяемость: что считать номером, а что — описанием.

Поле «номер изготовителя» заполняют руками, и туда пишут описание: «ШАЙБА
АЛЮМИНИЕВАЯ - 1/4 BSP». Попав в таблицу взаимозаменяемости, такая запись
заставит искать деталь с номером «шайба алюминиевая». Поле «замена», наоборот,
приходит целой фразой, и номер в ней есть — выбрасывать фразу целиком значит
терять подсказку.

Примеры придуманы здесь и не взяты из базы: репозиторий публичный (правило 5).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"kvant_{name}", ROOT / "library" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lc = load("load_crossrefs")


def test_описание_номером_не_считается():
    for s in ("ШАЙБА АЛЮМИНИЕВАЯ - 1/4 BSP", "медная шайба 1/2 BSP",
              "БОЛТ - 5/8 UNC x 2,1 / 2", "подшипник роликовый двухрядный", ""):
        assert not lc.похоже_на_номер(s), s


def test_настоящий_номер_проходит():
    for s in ("6ES7153-2BA10-0XB0", "MW21215M", "64/60030070/1", "14T47", "FF200423B"):
        assert lc.похоже_на_номер(s), s


def test_номер_достаётся_из_фразы_о_замене():
    фраза = "Magelis HMISTO501 / HMIS серия — официальная замена после снятия с выпуска"
    assert "HMISTO501" in lc.номера_из(фраза)


def test_из_фразы_не_достаются_слова_и_голые_числа():
    # «Dwyer» — имя без цифр, «1900/1950» — диапазон моделей без букв: ни то,
    # ни другое номером не считается.
    assert lc.номера_из("Dwyer 1900/1950 низкое давление") == []
    assert lc.номера_из("официальной замены нет, нужен аналог") == []


def test_ключ_детали_сводит_написания_одного_номера():
    assert lc.part_key("560-170-80") == lc.part_key("56017080") == "56017080"
    assert lc.part_key("56017080") != lc.part_key("56017081")


def test_взаимозаменяемость_и_ведомость_собираются_из_данных():
    alts = lc.build_alts()
    assert len(alts) > 100, "источники взаимозаменяемости перестали читаться"
    assert {"номер изготовителя", "замена"} <= {a["kind"] for a in alts}
    for a in alts:
        assert a["alt_pn"] and a["part_id"]
        assert lc.part_key(a["alt_pn"]) != a["part_id"], "связь детали самой с собой"
    строки, детали, машины = lc.build_bom()
    assert строки and детали, "ведомость перестала читаться"
    assert all(s["part_no"] for s in строки)


def test_ключ_запроса_по_номеру_считается_так_же_как_при_загрузке():
    """scripts/library_part.py ищет деталь по тому же ключу, каким она
    загружена. Разойдись нормализация — запрос «560-170-80» не найдёт деталь
    «56017080», и это выглядело бы как «нет данных», а не как ошибка."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "kvant_library_part", ROOT / "scripts" / "library_part.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for номер in ("560-170-80", "56 017 080", "MW21215M", "64/60030070/1", "ШАЙБА-12Ё"):
        assert mod.part_key(номер) == lc.part_key(номер), номер
