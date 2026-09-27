"""Ежедневный проход писем на настоящей базе: повтор, две отметки, откат.

То же, что tests/test_mail_daily.py проверяет на базе в памяти, здесь — на
PostgreSQL со схемами library/supabase: вставка lib_files с конфликтом по
file_id, lib_demand без ключа (повтор разбора задвоил бы строки), цены своего
потока, lib_metric_runs с «последней отметкой» по measured_at. Проверяется:
  • второй проход того же окна не добавляет ни одной строки ни в одну таблицу;
  • отметка ручной пачки («почта:mail-supplier») и отметка прохода
    («инкремент_письма_поставщиков») читаются каждая своя и друг друга не
    сдвигают;
  • откат прохода (increment.py «откат письма») холостым только считает, с
    APPLY снимает ровно записанное проходом — цены, строки спроса, файлы и
    отметку — и не трогает чужие происхождения и отметку пачки.

Работает только при поднятой базе (LIBRARY_SQL_TEST_DSN), как соседние тесты
SQL. Своя схема; за собой тест убирает всё. Корпус придуман (правило 18).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests import test_mail_daily as t
from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "mail_daily_sql_test"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql")


@pytest.fixture
def база():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    созданные: list[str] = []
    try:
        for роль in ("anon", "authenticated", "service_role"):
            c.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not c.fetchone():
                c.execute(f"create role {роль} nologin")
                созданные.append(роль)
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        for имя in ФАЙЛЫ:
            for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                c.execute(оператор)
        yield c
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def подключить(*_a, **_k):
    import psycopg2
    conn = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    conn.autocommit = False
    return conn


def прогнать(monkeypatch, tmp_path, *, верх: int = 1040, запись: bool = True) -> tuple[int, t.Закачка]:
    """Шаг писем library-daily.yml на настоящей базе: подмена только портала и Диска."""
    import psycopg2.extras
    настоящая_вставка = psycopg2.extras.execute_values
    закачка = t.Закачка()
    ix = t.индексатор(monkeypatch, tmp_path, t.База(), t.Портал(t.корпус(), t.КОНТАКТЫ),
                      закачка, запись=запись, верх=верх)
    monkeypatch.setattr(psycopg2.extras, "execute_values", настоящая_вставка)
    monkeypatch.setattr(ix, "connect", подключить)
    return ix.main(), закачка


def счёт(c) -> dict[str, int]:
    out = {}
    for имя, запрос in {
        "файлов": "select count(*) from lib_files where origin = 'письмо поставщика'",
        "спроса": "select count(*) from lib_demand where source = 'письмо поставщика'",
        "цен": "select count(*) from lib_prices where feed = 'письмо поставщика'",
        "чужих файлов": "select count(*) from lib_files where origin <> 'письмо поставщика'",
        "чужого спроса": "select count(*) from lib_demand where source <> 'письмо поставщика'",
        "чужих цен": "select count(*) from lib_prices where feed <> 'письмо поставщика'",
    }.items():
        c.execute(запрос)
        out[имя] = c.fetchone()[0]
    return out


ЧУЖОЕ = """
insert into lib_segments (id, name) values ('bearings_seals', 'Подшипники и уплотнения')
  on conflict (id) do nothing;
insert into lib_files (file_id, deal_id, origin, field, status) values
  ('77001', '31', 'поле запроса', 'ufCrm18_1', 'разобран');
insert into lib_demand (deal_id, item_name, part_number, source, source_file) values
  ('31', 'Клапан выдуманный', 'KL-7', 'котировка поставщика', '77001');
insert into lib_prices (item_name, part_number, price, currency, source_url, rfq_id, source, feed)
  values ('Клапан выдуманный', 'KL-7', 10, 'USD', '77001', '31', 'разбор КП', 'разбор КП');
"""


def test_повтор_не_дублирует_отметки_не_мешают_откат_снимает_своё(база, monkeypatch, tmp_path):
    import increment
    import mail_source as ms

    база.execute(ЧУЖОЕ)
    monkeypatch.setenv("INCREMENT_START", str(int(time.time()) - 5))
    код, закачка = прогнать(monkeypatch, tmp_path)
    assert код == 0 and закачка.диск
    после_первого = счёт(база)
    assert после_первого["файлов"] and после_первого["спроса"] and после_первого["цен"]
    граница = t.env_шага(tmp_path)["INCREMENT_MAIL_TO"]

    # Повтор того же окна: отметку не записали — завтра то же окно.
    код, повтор = прогнать(monkeypatch, tmp_path)
    assert код == 0 and повтор.диск == []
    assert счёт(база) == после_первого, "повтор окна задвоил строки"

    # Две отметки: пачки и прохода. Каждая читается своя.
    conn = подключить()
    with conn.cursor() as cur:
        monkeypatch.setenv("MAIL_FILES", "1")
        ms.записать_отметку(cur, "mail-supplier", 0, 1036, 200, "пачка-1")
    conn.commit()
    conn.close()
    monkeypatch.setenv("INCREMENT_MAIL_TO", граница)
    monkeypatch.setenv("GITHUB_RUN_ID", "7700")
    time.sleep(0.01)
    assert increment.main(["increment.py", "отметка", "письма"]) == 0
    conn = подключить()
    with conn.cursor() as cur:
        assert ms.прочитать_отметку(cur, "mail-supplier") == 1036
        assert increment.прочитать(cur, "mail-supplier")["после_id"] == int(граница)
    conn.close()

    # Откат: холостой только считает.
    monkeypatch.setenv("ROLLBACK", "7700")
    monkeypatch.delenv("APPLY", raising=False)
    assert increment.main(["increment.py", "откат", "письма"]) == 0
    assert счёт(база) == после_первого
    # С APPLY — снимает своё, чужое и отметку пачки не трогает.
    monkeypatch.setenv("APPLY", "1")
    assert increment.main(["increment.py", "откат", "письма"]) == 0
    после_отката = счёт(база)
    assert после_отката == {"файлов": 0, "спроса": 0, "цен": 0, "чужих файлов": 1,
                            "чужого спроса": 1, "чужих цен": 1}
    conn = подключить()
    with conn.cursor() as cur:
        assert increment.прочитать(cur, "mail-supplier") is None, "отметка прохода не снята"
        assert ms.прочитать_отметку(cur, "mail-supplier") == 1036
    conn.close()
    # Повторный откат того же прогона — отказ, а не молчаливый ноль.
    assert increment.main(["increment.py", "откат", "письма"]) == 2

    # Проход после отката перечитывает то же окно и восстанавливает записанное.
    код, заново = прогнать(monkeypatch, tmp_path)
    assert код == 0 and sorted(заново.диск) == sorted(закачка.диск)
    assert счёт(база) == после_первого
