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
    APPLY снимает ровно записанное проходом — цены, строки спроса, файлы,
    отметку и строки шага — и не трогает чужие происхождения и отметку пачки;
    запись упавшей попытки находит по строке шага (окно отметки её не видит);
  • откат не последнего прохода — отказ, и база не тронута (иначе письма его
    окна не перечитает никто); упавший проход (отказ Диска, код 3) без отметки
    откатывается по строке шага; снятый — только с ROLLBACK_TO в пределах
    задания, и запись после его конца не снимается.

Работает только при поднятой базе (LIBRARY_SQL_TEST_DSN), как соседние тесты
SQL. Своя схема; за собой тест убирает всё. Корпус придуман (правило 18).
"""
from __future__ import annotations

import json
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone
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


def прогнать(monkeypatch, tmp_path, *, верх: int = 1040, запись: bool = True,
             письма: list[dict] | None = None, закачка: t.Закачка | None = None,
             прогон: str = "7700", попытка: str = "1") -> tuple[int, t.Закачка]:
    """Шаг писем library-daily.yml на настоящей базе: подмена только портала и Диска.

    Начало прохода (INCREMENT_START) — сейчас, как у шага «начало» этой попытки."""
    import psycopg2.extras
    настоящая_вставка = psycopg2.extras.execute_values
    закачка = закачка or t.Закачка()
    ix = t.индексатор(monkeypatch, tmp_path, t.База(),
                      t.Портал(письма if письма is not None else t.корпус(), t.КОНТАКТЫ),
                      закачка, запись=запись, верх=верх)
    for к, v in {"GITHUB_RUN_ID": прогон, "GITHUB_RUN_ATTEMPT": попытка,
                 "INCREMENT_START": str(int(time.time()))}.items():
        monkeypatch.setenv(к, v)
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
    # Попытка 1 записала всё, но до отметки не дошла (упал шаг отметки).
    код, закачка = прогнать(monkeypatch, tmp_path, попытка="1")
    assert код == 0 and закачка.диск
    после_первого = счёт(база)
    assert после_первого["файлов"] and после_первого["спроса"] and после_первого["цен"]
    граница = t.env_шага(tmp_path)["INCREMENT_MAIL_TO"]

    # Повтор того же окна перезапуском (попытка 2, своё начало прохода): ни
    # одной новой строки. Окно отметки попытки 2 начинается ПОСЛЕ записи
    # попытки 1 — запись найдёт только строка шага попытки 1.
    time.sleep(1.1)
    код, повтор = прогнать(monkeypatch, tmp_path, попытка="2")
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

    # Окно одной отметки записи попытки 1 не видит — её видит строка шага.
    conn = подключить()
    with conn.cursor() as cur:
        cur.execute(increment.ОТКАТ_ОТМЕТКА, (increment.ЗАМЕР["mail-supplier"], "7700"))
        nums, конец = cur.fetchone()
        cur.execute("select count(*) from lib_files where origin = 'письмо поставщика'"
                    " and processed_at between to_timestamp(%s) and %s", (nums["начало"], конец))
        assert cur.fetchone()[0] == 0, "корпус не проверяет строку шага: запись в окне отметки"
    conn.close()

    # Откат: холостой только считает.
    monkeypatch.setenv("ROLLBACK", "7700")
    monkeypatch.delenv("APPLY", raising=False)
    monkeypatch.delenv("ROLLBACK_TO", raising=False)
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
        cur.execute("select count(*) from lib_metric_runs where metric = %s", (increment.ШАГ_ПИСЕМ,))
        assert cur.fetchone()[0] == 0, "строки шага не сняты"
    conn.close()
    # Повторный откат того же прогона — отказ, а не молчаливый ноль.
    assert increment.main(["increment.py", "откат", "письма"]) == 2

    # Проход после отката перечитывает то же окно и восстанавливает записанное.
    код, заново = прогнать(monkeypatch, tmp_path, прогон="7701")
    assert код == 0 and sorted(заново.диск) == sorted(закачка.диск)
    assert счёт(база) == после_первого


# ─────────────────────────────────────────────── откат: только последний, упавший, снятый
def индексатор_отката(monkeypatch):
    """increment.py «откат письма» берёт connect у indexer — подменяем только его."""
    monkeypatch.setitem(sys.modules, "indexer", types.SimpleNamespace(connect=подключить))


def строка_замера(c, metric: str, ключ: str, nums: dict, мин_назад: float) -> None:
    c.execute("insert into lib_metric_runs (metric, run_key, nums, measured_at)"
              " values (%s, %s, %s::jsonb, now() - make_interval(secs => %s))",
              (metric, ключ, json.dumps(nums), мин_назад * 60))


def файл_письма(c, ключ: str, мин_назад: float) -> None:
    """Файл письма поставщика с ценой и строкой спроса, обработанный мин_назад минут назад."""
    c.execute("insert into lib_files (file_id, deal_id, origin, status, processed_at) values"
              " (%s, 'C55', 'письмо поставщика', 'разобран', now() - make_interval(secs => %s))",
              (ключ, мин_назад * 60))
    c.execute("insert into lib_demand (deal_id, item_name, part_number, source, source_file)"
              " values ('C55', 'Подшипник выдуманный', 'VYD-1', 'письмо поставщика', %s)", (ключ,))
    c.execute("insert into lib_prices (item_name, part_number, price, currency, source_url,"
              " rfq_id, source, feed) values ('Подшипник выдуманный', 'VYD-1', 10, 'USD', %s,"
              " 'C55', 'письмо поставщика', 'письмо поставщика')", (ключ,))


def файлы_писем(c) -> set[str]:
    c.execute("select file_id from lib_files where origin = 'письмо поставщика'")
    return {r[0] for r in c.fetchall()}


def откат(monkeypatch, ключ: str, применить: bool = True, до: str | None = None) -> int:
    import increment
    monkeypatch.setenv("ROLLBACK", ключ)
    if применить:
        monkeypatch.setenv("APPLY", "1")
    else:
        monkeypatch.delenv("APPLY", raising=False)
    if до is None:
        monkeypatch.delenv("ROLLBACK_TO", raising=False)
    else:
        monkeypatch.setenv("ROLLBACK_TO", до)
    return increment.main(["increment.py", "откат", "письма"])


def test_откат_только_последнего_прохода(база, monkeypatch):
    """Замер ревизии 27.09.2026: отметки R1 (100), R2 (200), R3 (300); откат R2
    снимал его файлы, а последней оставалась R3 — письма 101–200 не перечитал бы
    никто. Теперь откат R2 — отказ, и база не тронута; от последнего — можно.
    F0 — упавший проход до них (только строка шага): и его откат ждёт их."""
    import increment
    индексатор_отката(monkeypatch)
    сейчас = time.time()
    отметка = increment.ЗАМЕР["mail-supplier"]
    строка_замера(база, increment.ШАГ_ПИСЕМ, "F0.1", {"граница": 90, "конец": сейчас - 45 * 60}, 50)
    файл_письма(база, "mail:f0", 47)
    for i, (ключ, после) in enumerate((("R1", 100), ("R2", 200), ("R3", 300))):
        конец = 30 - 10 * i
        строка_замера(база, отметка, ключ, {"начало": int(сейчас - (конец + 5) * 60), "после_id": после},
                      конец)
        файл_письма(база, f"mail:{ключ.lower()}", конец + 2)
    файл_письма(база, "mail:пачка", 1)          # запись ручной пачки после всех проходов
    было = файлы_писем(база)

    assert откат(monkeypatch, "R2") == 2
    assert откат(monkeypatch, "F0") == 2
    assert файлы_писем(база) == было, "отказ отката что-то снял"
    conn = подключить()
    with conn.cursor() as cur:
        assert increment.прочитать(cur, "mail-supplier")["после_id"] == 300
    conn.close()

    for ключ, осталась in (("R3", 200), ("R2", 100), ("R1", None)):
        assert откат(monkeypatch, ключ) == 0, ключ
        conn = подключить()
        with conn.cursor() as cur:
            последняя = increment.прочитать(cur, "mail-supplier")
            assert (последняя or {}).get("после_id") == осталась, ключ
        conn.close()
    assert откат(monkeypatch, "F0") == 0, "после поздних — и упавший"
    assert файлы_писем(база) == {"mail:пачка"}, "снято чужое или не снято своё"
    база.execute("select count(*) from lib_demand where source = 'письмо поставщика'")
    assert база.fetchone()[0] == 1
    база.execute("select count(*) from lib_metric_runs where metric in (%s, %s)",
                 (отметка, increment.ШАГ_ПИСЕМ))
    assert база.fetchone()[0] == 0


def test_упавший_проход_откатывается_по_строке_шага(база, monkeypatch, tmp_path):
    """Отказ Диска: разбор записал «не скачался» по каждому вложению и вышел с
    кодом 3 без отметки. Прежде откат отвечал «откатывать нечего» — теперь он
    находит запись по строке шага и снимает её."""
    import increment
    письма = [t.письмо(n, t.назад(hours=3), [t.диск(n)]) for n in range(2001, 2013)]
    код, _ = прогнать(monkeypatch, tmp_path, письма=письма, закачка=t.Закачка(отказ=True),
                      верх=2012, прогон="7800")
    assert код == 3
    assert len(файлы_писем(база)) == 12
    база.execute("select count(*) from lib_metric_runs where metric = %s",
                 (increment.ЗАМЕР["mail-supplier"],))
    assert база.fetchone()[0] == 0, "упавший проход записал отметку"

    индексатор_отката(monkeypatch)
    assert откат(monkeypatch, "7800", применить=False) == 0
    assert len(файлы_писем(база)) == 12, "холостой откат снял"
    assert откат(monkeypatch, "7800") == 0
    assert файлы_писем(база) == set()
    база.execute("select count(*) from lib_metric_runs where metric = %s", (increment.ШАГ_ПИСЕМ,))
    assert база.fetchone()[0] == 0


def test_снятый_проход_только_с_ROLLBACK_TO_в_пределах_задания(база, monkeypatch):
    """Задание сняли по таймауту — конца у строки шага нет. Откат без ROLLBACK_TO
    — отказ; с ним снимается запись до конца задания, а запись ручной пачки,
    пришедшей после, — нет. Конец дальше длины задания — отказ."""
    import increment
    индексатор_отката(monkeypatch)
    строка_замера(база, increment.ШАГ_ПИСЕМ, "7900.1", {"граница": 500}, 20)
    файл_письма(база, "mail:снятый", 15)
    файл_письма(база, "mail:пачка", 5)
    конец_задания = datetime.now(timezone.utc) - timedelta(minutes=10)

    assert откат(monkeypatch, "7900") == 2
    далеко = datetime.now(timezone.utc) + timedelta(minutes=increment.ЗАДАНИЕ_МИН)
    assert откат(monkeypatch, "7900", до=далеко.isoformat()) == 2
    assert откат(monkeypatch, "7900", до="вчера") == 2
    assert файлы_писем(база) == {"mail:снятый", "mail:пачка"}
    assert откат(monkeypatch, "7900", до=конец_задания.isoformat()) == 0
    assert файлы_писем(база) == {"mail:пачка"}
