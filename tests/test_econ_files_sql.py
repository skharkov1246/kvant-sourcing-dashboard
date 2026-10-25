"""Финальная экономика на настоящей базе PostgreSQL (library/supabase/econ_files_schema.sql).

  · схема применяется дважды, в свою схему одноразовой базы, без ролей платформы;
  · RLS на обеих таблицах включена и принудительна;
  · предел суток держит база: 30 попыток по Москве, больший вход его не поднимает,
    два прогона разом — тоже; вчерашние попытки не в счёт;
  · запись прогона scripts/econ_files.База: строка с ключом прогона, повтор не задваивает,
    итог — запросом по ключу, откат — пометкой; удалить и править нельзя;
  · сверенная строка без чисел, статус не из списка, путь вместо ключа файла, текст в
    курсах — не встают;
  · вид последнего чтения: помеченные, сбои доставки и разбора не в счёт; только прогоны,
    прошедшие гейт (econ_file_runs) — упавший до гейта прогон вкладке не виден;
  · у несверенной строки сумм нет (ревизия 25.10.2026: отрицательная «продажа» роняла запись);
  · решение гейта только дописывается; очередь (прочитано) считает чтением только строки
    прошедших гейт прогонов — упавший прогон сделку не «прочитал» (вторая ревизия);
  · продажа «Нашего КП» — только у сверенной переторжки; колонка добавляется и в таблицу,
    созданную до неё (правило 21);
  · каждый статус Python база принимает.

Работает при поднятой базе (LIBRARY_SQL_TEST_DSN). Корпус придуман (правило 18).
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "econ_files_test"


def _применить(c) -> None:
    from tests.test_library_schema_sql import операторы
    for оператор in операторы((ROOT / "library" / "supabase" / "econ_files_schema.sql").read_text(encoding="utf-8")):
        c.execute(оператор)


@pytest.fixture
def conn():
    import psycopg2
    с = psycopg2.connect(DSN)
    с.autocommit = True
    c = с.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    for _ in range(2):
        _применить(c)
    try:
        yield с
    finally:
        с.cursor().execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()


def _одно(conn, sql, *p):
    with conn.cursor() as c:
        c.execute(sql, p)
        return c.fetchone()


def _база():
    import econ_files as ef
    return ef.База(DSN, схема=СХЕМА)


def _строка(прогон: str, deal: int, status: str = "сверено", **kw) -> dict:
    import econ_files as ef
    r = {"run_id": прогон, "method_ver": ef.METHOD_VER, "deal_id": deal, "file_key": "e" * 64, "status": status,
         "sub_cur": "RUB", "sale_sub": 2_600_000.0, "sale_eur": 26_000.0, "purchase_eur": 20_000.0, "markup": 0.3,
         "rebid": False, "items": 3, "coef_median": 1.5, "totals_dev": 0.0, "bad_lines": 0,
         "fx": {"EUR": 100.0, "RUB": 1.0}, "deal_cur": "EUR", "bitrix_ratio": 1.0, "bitrix_note": None,
         "kp_sale_sub": None, "why": None}
    if status != "сверено":
        r.update(sale_sub=None, sale_eur=None, purchase_eur=None, markup=None, rebid=None, fx=None,
                 bitrix_ratio=None, coef_median=None, totals_dev=None)
    r.update(kw)
    return r


def test_схема_rls_и_таблицы(conn):
    for таблица in ("econ_file_reads", "econ_file_attempts", "econ_file_runs"):
        assert _одно(conn, "select relrowsecurity, relforcerowsecurity from pg_class where oid = %s::regclass",
                     f"{СХЕМА}.{таблица}") == (True, True), таблица
    база = _база()
    try:
        assert база.нет_таблиц() == []
    finally:
        база.закрыть()


def test_предел_суток_держит_база(conn):
    база = _база()
    try:
        взято = [база.взять("ef-s1", 8000 + i, 99) for i in range(32)]
        assert sum(взято) == 30 and взято[:30] == [True] * 30, "больший вход предел владельца не поднимает"
        assert база.взято_сегодня() == 30
        assert not база.взять("ef-s2", 9000, 30), "второй прогон в те же сутки — ни одной попытки"
    finally:
        база.закрыть()
    # попытки вчерашних суток по Москве — не в счёт (в обход триггера: только вставка)
    with conn.cursor() as c:
        c.execute(f"set search_path to {СХЕМА}")
        c.execute("alter table econ_file_attempts disable trigger econ_file_attempts_append_only")
        c.execute("update econ_file_attempts set at = at - interval '1 day'")
        c.execute("alter table econ_file_attempts enable trigger econ_file_attempts_append_only")
    база = _база()
    try:
        assert база.взято_сегодня() == 0 and база.взять("ef-s3", 9001, 30)
        assert not база.взять("ef-s3", 9002, 1), "предел входа меньше — меньше"
    finally:
        база.закрыть()


def test_два_прогона_разом_предел_не_удваивают(conn):
    итог: list[int] = []

    def прогон(run: str):
        база = _база()
        try:
            итог.append(sum(база.взять(run, 7000 + i, 30) for i in range(25)))
        finally:
            база.закрыть()
    потоки = [threading.Thread(target=прогон, args=(f"ef-p{k}",)) for k in range(2)]
    for п in потоки:
        п.start()
    for п in потоки:
        п.join()
    assert sum(итог) == 30
    assert _одно(conn, f"select count(*) from {СХЕМА}.econ_file_attempts")[0] == 30


def test_запись_повтор_итог_и_откат(conn):
    import psycopg2
    база = _база()
    try:
        for _ in range(2):                                   # повтор того же прогона не задваивает
            база.записать(_строка("ef-w1", 101))
            база.записать(_строка("ef-w1", 102, "шаблон другой"))
            база.записать(_строка("ef-w1", 103, "не скачался", file_key=None))
        assert база.итог("ef-w1") == {"сверено": 1, "шаблон другой": 1, "не скачался": 1}
        assert база.прочитано() == {}, "прогон до гейта сделки не «прочитал»"
        база.отметить_прогон("ef-w1")
        assert set(база.прочитано()) == {101, 102, 103}
        assert _одно(conn, f"select fx->>'EUR', bitrix_ratio from {СХЕМА}.econ_file_reads where deal_id = 101") \
            == ("100.0", 1)
        assert база.пометить_прогон("ef-w1") == 3
        assert база.итог("ef-w1") == {} and база.прочитано() == {}, "помеченное — не чтение"
        assert база.пометить_прогон("ef-w1") == 0
    finally:
        база.закрыть()
    with conn.cursor() as c:
        c.execute(f"set search_path to {СХЕМА}")
        with pytest.raises(psycopg2.Error):
            c.execute("delete from econ_file_reads where run_id = 'ef-w1'")
        with pytest.raises(psycopg2.Error):
            c.execute("update econ_file_reads set sale_eur = 1 where deal_id = 101")
        with pytest.raises(psycopg2.Error):
            c.execute("update econ_file_reads set voided_at = now() where deal_id = 101")   # дважды нельзя
        c.execute("select econ_file_take_slot('ef-w1', 101, 30)")
        with pytest.raises(psycopg2.Error):
            c.execute("delete from econ_file_attempts")
        with pytest.raises(psycopg2.Error):
            c.execute("update econ_file_attempts set deal_id = 102")
        with pytest.raises(psycopg2.Error):
            c.execute("truncate econ_file_reads")


@pytest.mark.parametrize("правка", [
    {"sale_eur": None},                                # сверено без числа
    {"sale_eur": 19_000.0, "markup": -0.05},           # продажа ниже закупки
    {"markup": 0.5},                                   # наценка не из своих чисел
    {"items": 0},
    {"bad_lines": 1},
    {"totals_dev": 0.01},
    {"coef_median": 7},
    {"file_key": "Проекты ВЫДУМКА/Экономика.xlsx"},    # путь вместо ключа
    {"fx": {"EUR": "сто"}},                            # текст в курсах
    {"fx": {"евро": 100}},
    {"status": "почти сверено"},
    {"bitrix_note": "что-то"},
    {"bitrix_note": "валюта сделки другая"},           # и отношение, и «не сверить» разом
    {"run_id": "ef w1"},
    {"sub_cur": "руб"},
    {"why": "Проекты ВЫДУМКА"},                         # текст вместо адреса ячейки
    # несверенная строка с суммами (_строка сносит их у несверенной — правка возвращает)
    {"status": "неправдоподобно", "sale_sub": 2_600_000.0, "sale_eur": 26_000.0, "purchase_eur": 20_000.0,
     "markup": 0.3},
    {"status": "неправдоподобно", "sale_sub": -3_000_000.0, "sale_eur": -30_000.0, "markup": -2.5},
    {"status": "итоги не сходятся", "purchase_eur": 20_000.0},          # одной суммы хватает
    {"kp_sale_sub": 3_000_000.0},                                        # продажа КП без переторжки
    {"rebid": True, "kp_sale_sub": -1.0},
    {"status": "неправдоподобно", "kp_sale_sub": 3_000_000.0},           # у несверенной сумм нет
])
def test_ограничения_строки(conn, правка):
    import psycopg2
    база = _база()
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            база.записать(_строка("ef-c1", 201, **правка))
    finally:
        база.закрыть()


def test_каждый_статус_python_база_принимает(conn):
    import econ_files as ef
    база = _база()
    try:
        for i, статус in enumerate(ef.СТАТУСЫ):
            база.записать(_строка("ef-st", 300 + i, статус))
        for i, где in enumerate(("G12", "AK11", "D4", "R1", "AK:AL", "позиции", "проверка 1", "проверка 2")):
            база.записать(_строка("ef-why", 400 + i, "шаблон другой", why=где))
        assert sum(база.итог("ef-st").values()) == len(ef.СТАТУСЫ)
    finally:
        база.закрыть()


def test_вид_последнего_чтения(conn):
    база = _база()
    try:
        база.записать(_строка("ef-v1", 401))
        база.записать(_строка("ef-v1", 402))
        база.записать(_строка("ef-v1", 403))
        база.записать(_строка("ef-v1", 404))
        assert база.отметить_прогон("ef-v1") == (4, 4)
        # позже: у 401 сеть подвела — прежнее чтение остаётся; у 402 шаблон сменился — скрыто;
        # у 404 упал разбор — прежнее чтение остаётся
        база.записать(_строка("ef-v2", 401, "не скачался", file_key=None))
        база.записать(_строка("ef-v2", 402, "шаблон другой"))
        база.записать(_строка("ef-v2", 404, "сбой чтения"))
        база.отметить_прогон("ef-v2")
        база.записать(_строка("ef-v3", 403, sale_eur=27_000.0, markup=0.35))
        база.отметить_прогон("ef-v3")
        база.пометить_прогон("ef-v3")                         # откат последнего — видно прежнее
        # прогон упал до гейта (не в econ_file_runs): его строки, хоть и не помечены, не видны
        база.записать(_строка("ef-v4", 401, sale_eur=28_000.0, markup=0.4))
        база.записать(_строка("ef-v4", 405, sale_eur=28_000.0, markup=0.4))
    finally:
        база.закрыть()
    with conn.cursor() as c:
        c.execute(f"select deal_id, status, run_id, sale_eur from {СХЕМА}.econ_file_latest order by deal_id")
        assert c.fetchall() == [(401, "сверено", "ef-v1", 26_000), (402, "шаблон другой", "ef-v2", None),
                                (403, "сверено", "ef-v1", 26_000), (404, "сверено", "ef-v1", 26_000)]


def test_очередь_не_считает_чтением_прогон_до_гейта(conn):
    """Ревизии sql и privacy, 1: прогон упал до гейта (не в econ_file_runs) — его строк нет во
    вкладке, и сделку они не «прочитали»: иначе она ждала бы ПОВТОР_ДНЕЙ без экономики.
    Строка «сбой чтения» прошедшего гейт прогона — чтение (сделка уходит в конец очереди)."""
    база = _база()
    try:
        база.записать(_строка("ef-q1", 701))
        база.записать(_строка("ef-q1", 702, "сбой чтения", file_key=None))
        база.отметить_прогон("ef-q1")
        база.записать(_строка("ef-q2", 703))                   # упал до гейта
        база.записать(_строка("ef-q2", 701, sale_eur=27_000.0, markup=0.35))
        прочитано = база.прочитано()
        assert set(прочитано) == {701, 702}
        с = база.сверка_на_чтение()
        assert set(с) == {701}, "сверка для очереди — из вида: только сверенное и прошедшее гейт"
        assert с[701]["sale"] == 2_600_000.0 and с[701]["saleEur"] == 26_000.0 and с[701]["cur"] == "RUB"
        assert с[701]["fx"] == {"EUR": 100.0, "RUB": 1.0} and с[701]["ratio"] == 1.0 and с[701]["note"] is None
        assert с[701]["at"] <= прочитано[701]
    finally:
        база.закрыть()


def test_продажа_кп_переторжки_и_колонка_в_старой_таблице(conn):
    """Колонку kp_sale_sub добавляет add column if not exists и в таблицу, созданную схемой
    до неё (create table if not exists таблицу не меняет — правило 21)."""
    with conn.cursor() as c:
        c.execute(f"set search_path to {СХЕМА}")
        c.execute("alter table econ_file_reads drop column kp_sale_sub cascade")     # таблица «до ревизии»
        assert _одно(conn, f"select to_regclass('{СХЕМА}.econ_file_latest')")[0] is None
        _применить(c)
    база = _база()
    try:
        база.записать(_строка("ef-k1", 801, rebid=True, kp_sale_sub=3_000_000.0))
        база.отметить_прогон("ef-k1")
    finally:
        база.закрыть()
    assert _одно(conn, f"select kp_sale_sub from {СХЕМА}.econ_file_latest where deal_id = 801")[0] == 3_000_000


def test_решение_гейта_только_дописывается(conn):
    import psycopg2
    база = _база()
    try:
        база.записать(_строка("ef-g1", 601))
        база.записать(_строка("ef-g1", 602, "шаблон другой"))
        assert база.отметить_прогон("ef-g1") == (2, 1)
        база.записать(_строка("ef-g1", 603))                  # после гейта: счёт не переписывается
        assert база.отметить_прогон("ef-g1") == (2, 1), "повтор не задваивает и не правит решение"
        assert база.нет_таблиц() == []
    finally:
        база.закрыть()
    with conn.cursor() as c:
        c.execute(f"set search_path to {СХЕМА}")
        for sql in ("update econ_file_runs set verified_n = 2", "delete from econ_file_runs",
                    "truncate econ_file_runs",
                    "insert into econ_file_runs (run_id, method_ver, rows_n, verified_n) values ('ef g', 'v', 1, 0)",
                    "insert into econ_file_runs (run_id, method_ver, rows_n, verified_n) values ('ef-g2', 'v', 1, 2)"):
            with pytest.raises(psycopg2.Error):
                c.execute(sql)


def test_свод_для_вкладки_на_настоящей_базе(conn):
    """realization_ctx.База.экономика — только сверенные последние чтения."""
    import realization_ctx as rc
    база = _база()
    try:
        база.записать(_строка("ef-r1", 501, rebid=True))
        база.записать(_строка("ef-r1", 502, "итоги не сходятся"))
        база.отметить_прогон("ef-r1")
    finally:
        база.закрыть()
    чтение = rc.База(DSN, схема=СХЕМА)
    try:
        rows = чтение.экономика()
    finally:
        чтение.закрыть()
    assert [r["deal"] for r in rows] == [501]
    свод, n = rc.свод_экономики(rows)
    assert свод["501"]["rebid"] is True and свод["501"]["cur"] == "RUB" and свод["501"]["fx"]["EUR"] == 100.0
    assert свод["501"]["ratio"] == 1.0 and свод["501"]["note"] is None
    assert n["экономика: не прошла сверку"] == 0
