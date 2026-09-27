"""Дата прихода файла на настоящей базе: first_seen_at и дата у источника.

ЗАЧЕМ. У lib_files не было даты появления: processed_at переписывает каждая
обработка — вставка разбора при конфликте, UPDATE переразбора, распознавание
целиком. Недельный свод поэтому не мог сказать, какие КП пришли за неделю.
Миграция library/supabase/file_dates_schema.sql заводит first_seen_at (первая
вставка, не переписывается НИКЕМ) и дату у источника (пишется только в пустое).
Проверяется там, где ошибка и случилась бы, — в самой базе, всеми записями
lib_files, какие есть в коде: вставка разбора (первая и повтор), UPDATE
переразбора, вставка и постраничная отметка распознавания, отметка извлечения
дефектов, прямой UPDATE колонки.

Строки «до миграции» кладутся ДО применения файла — как на живой базе: у них
first_seen_at пусто и пустым остаётся.

Работает только при поднятой базе (LIBRARY_SQL_TEST_DSN). Своя схема; за собой
тест убирает всё. Корпус придуман (правило 18).
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import indexer as ix  # noqa: E402
import ocr  # noqa: E402
import reparse  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "file_dates_test"
МИГРАЦИЯ = ROOT / "library" / "supabase" / "file_dates_schema.sql"
МСК = timezone(timedelta(hours=3))


def операторы(путь: Path) -> list[str]:
    from tests.test_library_schema_sql import операторы as резать
    return резать(путь.read_text(encoding="utf-8"))


@pytest.fixture
def cur():
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    for имя in ("schema.sql", "schema_junk.sql"):
        for оператор in операторы(ROOT / "library" / "supabase" / имя):
            c.execute(оператор)
    # Строка, записанная ДО миграции дат: так лежат все файлы живой базы.
    c.execute("insert into lib_files (file_id, deal_id, origin, field, status, processed_at)"
              " values ('старый', '41', 'поле запроса', 'ufCrm18_1700698211875', 'разобран',"
              " '2026-08-01 10:00+00')")
    # Дважды: файл обязан быть повторяемым (правило 21).
    for _ in range(2):
        for оператор in операторы(МИГРАЦИЯ):
            c.execute(оператор)
    try:
        yield c
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        conn.close()


@pytest.fixture
def запись(monkeypatch):
    """Запись файла такой, какой её отдаёт handle() (файл не скачался)."""
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: None)

    def сделать(file_id="77", origin="поле запроса", **ещё):
        ссылка = {"fo": {"id": file_id}, "deal": "11", "origin": origin,
                  "field": ("письмо 9001" if origin == "письмо поставщика" else "ufCrm18_1731179998"),
                  "field_title": "Offer from supplier", "our_company": None,
                  "card_created": ещё.pop("card_created", "2026-09-15T12:00:00+03:00")}
        rec, _ = ix.handle(ссылка)
        rec.update(ещё)
        return rec
    return сделать


def вставить(cur, rec) -> None:
    import psycopg2.extras
    колонки = ix.проверить_колонки(cur)
    запрос, шаблон = ix.вставка_файлов(колонки)
    psycopg2.extras.execute_values(cur, запрос, [ix.кортеж_файла(rec, колонки)], template=шаблон)


def переразобрать(cur, rec) -> None:
    колонки, нет_обяз, _ = ix.колонки_записи(
        ix.колонки_базы(cur, reparse.КОЛОНКИ_ЗАПИСИ), reparse.ОБЯЗАТЕЛЬНЫЕ_ЗАПИСИ,
        reparse.СВЕДЕНИЯ_ЗАПИСИ)
    assert нет_обяз == []
    cur.execute(reparse.правка_файла(колонки), reparse.значения_правки(rec, колонки))


def поля(cur, fid, *колонки):
    cur.execute(f"select {', '.join(колонки)} from lib_files where file_id = %s", (fid,))
    return cur.fetchone()


def test_впервые_ставится_первой_вставкой_и_не_меняется_ничем(cur, запись):
    import psycopg2.extras
    assert ix.проверить_колонки(cur) == ix.КОЛОНКИ_ВСТАВКИ
    вставить(cur, запись())
    впервые, обработан = поля(cur, "77", "first_seen_at", "processed_at")
    assert впервые is not None and впервые == обработан

    # Повтор «не скачался» → разобран: конфликт по file_id, processed_at новый.
    вставить(cur, запись(status="разобран", kind="xlsx", sha256="a" * 64))
    # Переразбор: UPDATE по колонкам записи.
    переразобрать(cur, запись(status="пусто", reason="нет текстового слоя"))
    # Распознавание целиком: своя вставка при конфликте.
    psycopg2.extras.execute_values(cur, ocr.ФАЙЛ_ЦЕЛИКОМ, [(
        "77", "11", "разобран", "pdf", 10, 1, None, None,
        datetime(2026, 9, 20, tzinfo=timezone.utc), 10, ix.PARSER_VERSION)])
    # Распознавание постранично, извлечение дефектов — отметки UPDATE.
    cur.execute(ocr.ФАЙЛ_ПОСТРАНИЧНО, (datetime(2026, 9, 21, tzinfo=timezone.utc), 5, "77"))
    cur.execute("update lib_files set defects_at = now() where file_id = any(%s)", (["77"],))
    # Даже прямой UPDATE колонки её не меняет — ни записанную, ни пустую.
    cur.execute("update lib_files set first_seen_at = now() + interval '1 day'")
    cur.execute("update lib_files set first_seen_at = null where file_id = '77'")

    сейчас, обработан_после = поля(cur, "77", "first_seen_at", "processed_at")
    assert сейчас == впервые, "first_seen_at переписана"
    assert обработан_после > обработан, "processed_at и должна была сдвинуться"
    assert поля(cur, "77", "status", "ocr_chars") == ("разобран", 5)
    # Строка до миграции: пусто и пустым остаётся — выдумывать дату нельзя.
    assert поля(cur, "старый", "first_seen_at") == (None,)
    вставить(cur, запись(file_id="старый", status="разобран"))
    переразобрать(cur, запись(file_id="старый", status="разобран"))
    assert поля(cur, "старый", "first_seen_at") == (None,)


def test_распознавание_впервые_записанного_файла_ставит_дату(cur):
    import psycopg2.extras
    psycopg2.extras.execute_values(cur, ocr.ФАЙЛ_ЦЕЛИКОМ, [(
        "88", "12", "разобран", "pdf", 10, 1, None, None, None, 0, ix.PARSER_VERSION)])
    assert поля(cur, "88", "first_seen_at")[0] is not None
    # Вставка с явным null не оставляет строку без даты.
    cur.execute("insert into lib_files (file_id, status, first_seen_at) values ('89', 'пусто', null)")
    assert поля(cur, "89", "first_seen_at")[0] is not None


def test_дата_у_источника_только_в_пустое(cur, запись, monkeypatch):
    # Письмо: CREATED пишется разбором всегда — первая дата остаётся навсегда.
    вставить(cur, запись(file_id="mail:5", origin="письмо поставщика",
                         card_created="2026-09-16T09:30:00+03:00"))
    assert поля(cur, "mail:5", "source_created_at", "source_date_src", "source_date_run") == (
        datetime(2026, 9, 16, 6, 30, tzinfo=timezone.utc), ix.ИСТ_ДАТЫ_ПИСЬМО, None)
    вставить(cur, запись(file_id="mail:5", origin="письмо поставщика",
                         card_created="2026-09-19T09:30:00+03:00", status="разобран"))
    assert поля(cur, "mail:5", "source_created_at")[0] == datetime(2026, 9, 16, 6, 30,
                                                                   tzinfo=timezone.utc)
    # Поле карточки: заголовок закачки пишется только со входом FILE_DATE_HEADER.
    вставить(cur, запись())
    assert поля(cur, "77", "source_created_at", "source_date_src") == (None, None)
    загружен = datetime(2026, 9, 17, 8, tzinfo=timezone.utc)
    переразобрать(cur, запись(source_created_at=загружен, source_date_src=ix.ИСТ_ДАТЫ_ЗАКАЧКА))
    assert поля(cur, "77", "source_created_at", "source_date_src") == (загружен, ix.ИСТ_ДАТЫ_ЗАКАЧКА)
    # Записанную не переписывает ни переразбор, ни повтор вставки, ни пустое значение.
    позже = загружен + timedelta(days=3)
    переразобрать(cur, запись(source_created_at=позже, source_date_src=ix.ИСТ_ДАТЫ_ЗАКАЧКА))
    вставить(cur, запись(source_created_at=позже, source_date_src=ix.ИСТ_ДАТЫ_ЗАКАЧКА))
    переразобрать(cur, запись())
    assert поля(cur, "77", "source_created_at", "source_date_src") == (загружен, ix.ИСТ_ДАТЫ_ЗАКАЧКА)


def test_схема_держит_пару_и_закрытый_список(cur):
    import psycopg2
    for sql in ("update lib_files set source_created_at = now() where file_id = 'старый'",
                "update lib_files set source_date_src = 'закачка: Last-Modified' where file_id = 'старый'",
                "update lib_files set source_created_at = now(), source_date_src = 'угадано'"
                " where file_id = 'старый'"):
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(sql)
    cur.execute("update lib_files set source_created_at = now(), source_date_src = %s"
                " where file_id = 'старый'", (ix.ИСТ_ДАТЫ_ПИСЬМО,))


def test_список_видов_в_схеме_тот_же_что_в_коде():
    """Вид, который пишет код, но не держит схема, уронил бы вставку на живой базе."""
    import importlib.util
    текст = re.sub(r"(?m)--.*$", "", МИГРАЦИЯ.read_text(encoding="utf-8"))
    блок = re.search(r"lib_files_source_date_src_chk\s+check\s*\((.*?)\);", текст, re.S).group(1)
    виды = set(re.findall(r"'([^']+)'", блок))
    spec = importlib.util.spec_from_file_location("weekly_offers_dates", ROOT / "scripts" / "weekly_offers.py")
    wo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wo              # dataclasses ищут модуль по имени
    spec.loader.exec_module(wo)
    assert виды == {ix.ИСТ_ДАТЫ_ПИСЬМО, ix.ИСТ_ДАТЫ_ЗАКАЧКА} == set(wo.ДАТА_У_ИСТОЧНИКА)
    assert (wo.ИСТ_ДАТЫ_ПИСЬМО, wo.ИСТ_ДАТЫ_ЗАКАЧКА) == (ix.ИСТ_ДАТЫ_ПИСЬМО, ix.ИСТ_ДАТЫ_ЗАКАЧКА)


def test_миграция_в_двух_схемах_и_без_ролей_платформы():
    """Файл применяется и во вторую схему той же базы (ограничение и триггер ищутся
    по самой таблице), а на чистом PostgreSQL без anon — не падает (правило 20)."""
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    схемы = ("file_dates_two_a", "file_dates_two_b")
    try:
        for сх in схемы:
            c.execute(f"drop schema if exists {сх} cascade")
            c.execute(f"create schema {сх}")
            c.execute(f"set search_path to {сх}")
            c.execute("create table lib_files (file_id text primary key, status text not null,"
                      " processed_at timestamptz default now())")
            for оператор in операторы(МИГРАЦИЯ):
                c.execute(оператор)
        for сх in схемы:
            c.execute("""select count(*) from pg_constraint k join pg_class t on t.oid = k.conrelid
                          join pg_namespace n on n.oid = t.relnamespace
                         where n.nspname = %s and k.conname = 'lib_files_source_date_src_chk'""", (сх,))
            assert c.fetchone()[0] == 1, сх
            c.execute("""select count(*) from pg_trigger g join pg_class t on t.oid = g.tgrelid
                          join pg_namespace n on n.oid = t.relnamespace
                         where n.nspname = %s and g.tgname = 'lib_files_first_seen'""", (сх,))
            assert c.fetchone()[0] == 1, сх
    finally:
        for сх in схемы:
            c.execute(f"drop schema if exists {сх} cascade")
        conn.close()


def test_схема_не_называет_роли_без_проверки():
    текст = re.sub(r"(?m)--.*$", "", МИГРАЦИЯ.read_text(encoding="utf-8"))
    assert not re.search(r"(?:from|to)\s+(?:anon|authenticated|service_role)\b", текст)
    assert "lock_timeout" in текст
    # Значение по умолчанию — отдельным оператором: иначе все прежние строки
    # получили бы время миграции и стали бы «новыми».
    assert not re.search(r"add column if not exists first_seen_at[^;]*default", текст)
