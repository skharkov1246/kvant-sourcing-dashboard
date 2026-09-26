"""Сравнение цен на /nomenclature — правило ревизии 26.09.2026.

Корпус придуман (CLAUDE.md, правило 18), ответы посчитаны руками.

ЧТО ЗАЩИЩАЕТСЯ:
1. Сравнимость — внутри группы «валюта в верхнем регистре + единица, сведённая
   тем же правилом, что SQL /brands» (codes_sql.единица_группа). Цена за штуку и
   за комплект — не сравнение; «pcs» и «шт» — одна единица; «usd» и «USD» —
   одна валюта.
2. Поставщик — корень сущности реестра (ent), без неё — ключ Битрикса (c). Две
   карточки Битрикса одной сущности — не выбор и не сравнение.
3. «Самые свежие» — по дате цены (price_date), без неё — в конце; дата разбора
   помечена (z).
4. Потери от правила видны числом (правило 0): totals.rule.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

from library import codes_sql, crossref
from tests.test_crossref_snapshot import строка


def позиция(s, ключ):
    return next(p for p in s["positions"] if p["k"] == ключ)


def test_штука_и_комплект_несравнимы():
    s = crossref.собрать([
        строка("ax100", "AX-100", "301", цена=10, валюта="USD", ед="шт"),
        строка("ax100", "AX-100", "302", цена=90, валюта="USD", ед="компл"),
    ])
    p = позиция(s, "ax100")
    assert p["cmp"] is False
    # Выбор при этом есть: две компании, просто цены за разное.
    assert crossref.выбор_позиции(p) == 2
    assert s["totals"]["rule"]["cmp_lost_units"] == 1


def test_единица_не_названа_у_одного_своя_группа_и_своя_причина():
    """Строка без единицы — своя группа, как в /brands; потеря — отдельным числом."""
    s = crossref.собрать([
        строка("jx100", "JX-100", "301", цена=10, валюта="USD", ед="шт"),
        строка("jx100", "JX-100", "302", цена=11, валюта="USD"),
    ])
    assert позиция(s, "jx100")["cmp"] is False
    п = s["totals"]["rule"]
    assert (п["cmp_lost_unit_empty"], п["cmp_lost_units"]) == (1, 0)


def test_pcs_и_шт_одна_единица():
    s = crossref.собрать([
        строка("bx200", "BX-200", "301", цена=10, валюта="USD", ед="pcs"),
        строка("bx200", "BX-200", "302", цена=11, валюта="USD", ед="шт."),
    ])
    assert позиция(s, "bx200")["cmp"] is True


def test_usd_и_USD_одна_валюта():
    s = crossref.собрать([
        строка("cx300", "CX-300", "301", цена=10, валюта="usd"),
        строка("cx300", "CX-300", "302", цена=11, валюта="USD"),
    ])
    assert позиция(s, "cx300")["cmp"] is True
    # Прежнее правило сравнивало написание валюты — позиция приобретена.
    assert s["totals"]["rule"]["cmp_gained"] == 1


def test_две_карточки_одной_сущности_не_выбор():
    s = crossref.собрать([
        строка("dx400", "DX-400", "301", сущность="KV-S-000007-1", цена=10, валюта="USD"),
        строка("dx400", "DX-400", "302", сущность="KV-S-000007-1", цена=11, валюта="USD"),
        # Контроль: разные сущности — выбор и сравнение.
        строка("ex500", "EX-500", "301", сущность="KV-S-000007-1", цена=10, валюта="USD"),
        строка("ex500", "EX-500", "303", сущность="KV-S-000008-1", цена=11, валюта="USD"),
    ])
    p = позиция(s, "dx400")
    assert p["co"] == 2 and p["sup"] == 1
    assert p["cmp"] is False
    assert crossref.выбор_позиции(p) == 1
    q = позиция(s, "ex500")
    assert "sup" not in q and q["cmp"] is True
    t = s["totals"]
    assert t["with_choice"] == 1
    assert t["rule"] == {"cmp_before": 2, "cmp_after": 1, "cmp_lost_units": 0, "cmp_lost_unit_empty": 0,
                         "cmp_lost_one_ent": 1, "cmp_gained": 0,
                         "choice_bitrix": 2, "choice_ent": 1, "choice_lost_one_ent": 1}
    # Позиция с выбором по сущности стоит выше позиции с двумя карточками одной.
    assert [p["k"] for p in s["positions"]] == ["ex500", "dx400"]


def test_сущность_соседнего_предложения_той_же_карточки():
    """ent не доехал до одной строки карточки 301 — она не второй поставщик."""
    s = crossref.собрать([
        строка("fx600", "FX-600", "301", сущность="KV-S-000007-1", цена=10, валюта="USD"),
        строка("fx600", "FX-600", "301", цена=11, валюта="USD", дата=dt.date(2026, 8, 1)),
    ])
    assert позиция(s, "fx600")["cmp"] is False


def test_хоть_одна_группа_с_двумя_поставщиками():
    s = crossref.собрать([
        строка("gx700", "GX-700", "301", цена=10, валюта="USD", ед="шт"),
        строка("gx700", "GX-700", "302", цена=11, валюта="USD", ед="pcs"),
        строка("gx700", "GX-700", "303", цена=900, валюта="RUB", ед="компл"),
    ])
    assert позиция(s, "gx700")["cmp"] is True


def test_самые_свежие_по_дате_цены():
    s = crossref.собрать([
        # Порядок запроса прежний (created_at desc): первой шла строка без даты
        # КП, второй — прошлогодняя, разобранная сегодня.
        строка("hx800", "HX-800", "301", цена=10, валюта="USD", дата=None),
        строка("hx800", "HX-800", "302", цена=11, валюта="USD", дата=dt.date(2025, 3, 1)),
        строка("hx800", "HX-800", "303", цена=12, валюта="USD", дата=dt.date(2026, 8, 1)),
    ])
    список = позиция(s, "hx800")["list"]
    assert [o["c"] for o in список] == ["303", "302", "301"]
    # Дата без price_date — день разбора, и это помечено.
    assert список[2].get("z") == "р" and "z" not in список[0]


def test_дата_карточки_запроса_помечена():
    s = crossref.собрать([
        строка("ix900", "IX-900", "301", цена=10, валюта="USD") + ("файл.pdf", "карточка: создана"),
        строка("ix900", "IX-900", "302", цена=11, валюта="USD") + ("файл2.pdf", "документ"),
    ])
    по_c = {o["c"]: o for o in позиция(s, "ix900")["list"]}
    assert по_c["301"].get("z") == "к" and "z" not in по_c["302"]


# ── Единица: одно правило на Python и SQL ────────────────────────────────────

ЕДИНИЦЫ = ["шт", "шт.", "ШТ", "pcs", "PCS.", "Pc", "ea", "компл", "Компл.", "к-т", "kit", "SET",
           "м", "М.", "mtr", "метров", "т", "кг", "kg", "", "   ", None, "10", "уп/10", "ё-шт"]


def test_единица_сводится_как_в_sql_brands():
    assert codes_sql.единица_группа("pcs") == codes_sql.единица_группа("Шт.") == "шт"
    assert codes_sql.единица_группа("Компл.") == codes_sql.единица_группа("KIT") == "компл"
    assert codes_sql.единица_группа("mtr") == "м"
    assert codes_sql.единица_группа(None) == codes_sql.единица_группа("  ") == codes_sql.NO_UNIT
    assert codes_sql.единица_группа("кг") == "кг"
    # Запрос /brands собран из той же функции, а не из копии правила.
    assert codes_sql.unit_case_sql("u.k") in codes_sql.PR_ALL
    assert codes_sql.unit_key_sql("p.qty_unit") in codes_sql.PR_ALL


@pytest.mark.skipif(not os.environ.get("LIBRARY_SQL_TEST_DSN"),
                    reason="одноразовая база PostgreSQL не настроена")
def test_единица_в_sql_и_в_python_одинакова():
    import psycopg2

    conn = psycopg2.connect(os.environ["LIBRARY_SQL_TEST_DSN"])
    try:
        with conn.cursor() as cur:
            cur.execute("select lower('ШТ') = 'шт'")
            assert cur.fetchone()[0], "локаль базы не складывает кириллицу (CLAUDE.md, 21а)"
            for ед in ЕДИНИЦЫ:
                cur.execute(f"select {codes_sql.unit_case_sql('k')} "
                            f"from (select {codes_sql.unit_key_sql('%s')} as k) x", (ед,))
                assert cur.fetchone()[0] == codes_sql.единица_группа(ед), ед
    finally:
        conn.close()
