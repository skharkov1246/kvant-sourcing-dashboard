"""Запись lib_files на настоящей базе: до миграции и после неё.

ЗАЧЕМ. Схема живёт в файле, а применяется отдельным ручным прогоном («ZIP base —
apply DB migrations»), тогда как ночной разбор котировок идёт по расписанию.
Разбор, который останавливался на любой недостающей колонке, от мержа до ручной
миграции краснел бы каждую ночь (разбор 23.09.2026). Теперь недостающее СВЕДЕНИЕ
пропускается, и это надо проверить там, где ошибка и случилась бы, — в самой
базе: запрос, собранный по колонкам, которые в ней есть, проходит; пустая наша
компания записанную не стирает ни вставкой разбора, ни UPDATE переразбора.

Работает только при поднятой базе (LIBRARY_SQL_TEST_DSN), как и соседние тесты
SQL. Своя схема, а не public: в той же базе живут другие тесты; за собой тест
убирает всё. Корпус придуман (правило 18).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from library import indexer as ix

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "lib_files_write_test"
ЧУЖАЯ = "lib_files_write_test_other"

# lib_files такой, какой её писал разбор до 23.09.2026: ровно обязательные колонки.
ДО_МИГРАЦИИ = """
create table lib_files (
  file_id text primary key, deal_id text, origin text, field text, kind text,
  size_bytes bigint, status text not null, reason text, chars int, rows_found int,
  segment_id text, sha256 text, processed_at timestamptz default now(),
  field_title text, side text, parse_path text, header_found boolean, doc_class text,
  class_rule text, text_lines int, item_lines int, parser_version smallint)"""


def миграция_сведений() -> list[str]:
    """Операторы schema_junk.sql, добавляющие колонки-сведения, — из самого файла:
    если какой-то колонки там нет, тест это покажет, а не подменит своей."""
    схема = re.sub(r"(?m)--.*$", "", (ROOT / "library/supabase/schema_junk.sql")
                   .read_text(encoding="utf-8"))
    операторы = {m.group(1): m.group(0) for m in re.finditer(
        r"alter table lib_files add column if not exists\s+(\w+)\s+[^;]+;", схема)}
    нет = [к for к in ix.КОЛОНКИ_СВЕДЕНИЙ if к not in операторы]
    assert not нет, f"schema_junk.sql не добавляет колонки: {нет}"
    return [операторы[к] for к in ix.КОЛОНКИ_СВЕДЕНИЙ]


@pytest.fixture
def cur():
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    for схема in (СХЕМА, ЧУЖАЯ):
        c.execute(f"drop schema if exists {схема} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    c.execute(ДО_МИГРАЦИИ)
    try:
        yield c
    finally:
        for схема in (СХЕМА, ЧУЖАЯ):
            c.execute(f"drop schema if exists {схема} cascade")
        conn.close()


@pytest.fixture
def запись(monkeypatch):
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: None)

    def сделать(**ещё):
        ссылка = {"fo": {"id": "5"}, "deal": "11", "origin": "поле запроса",
                  "field": "ufCrm18_1731179998", "field_title": "Offer from supplier"}
        rec, _ = ix.handle({**ссылка, "our_company": ещё.pop("our_company", None)})
        rec.update(ещё)
        return rec
    return сделать


def записать(cur, колонки, rec) -> None:
    import psycopg2.extras
    запрос, шаблон = ix.вставка_файлов(колонки)
    psycopg2.extras.execute_values(cur, запрос, [ix.кортеж_файла(rec, колонки)],
                                   template=шаблон)


def строка(cur, *колонки):
    cur.execute(f"select {', '.join(колонки)} from lib_files where file_id = '5'")
    return cur.fetchone()


def test_до_миграции_разбор_пишет_обязательное(cur, запись, capsys):
    колонки = ix.проверить_колонки(cur)
    assert колонки == ix.КОЛОНКИ_ОБЯЗАТЕЛЬНЫЕ
    out = capsys.readouterr().out
    assert out.count("::warning::") == 1 and "our_company" in out
    записать(cur, колонки, запись(our_company="ООО «Кордален»"))
    assert строка(cur, "status", "parser_version") == ("не скачался", ix.PARSER_VERSION)
    # Повтор того же файла — обновление, а не падение.
    записать(cur, колонки, запись(status="пусто"))
    assert строка(cur, "status") == ("пусто",)


def test_после_миграции_пустая_наша_компания_записанную_не_стирает(cur, запись, capsys,
                                                                     monkeypatch):
    for оператор in миграция_сведений():
        cur.execute(оператор)
    колонки = ix.проверить_колонки(cur)
    assert колонки == ix.КОЛОНКИ_ВСТАВКИ and "::warning::" not in capsys.readouterr().out
    записать(cur, колонки, запись(our_company="ООО «Кордален»", doc_kind="x"))
    # Сбой чтения наших компаний: our_company пусто у каждой карточки прогона.
    monkeypatch.setattr(ix, "_НАШИ_СБОЙ", True)
    записать(cur, колонки, запись(status="пусто", doc_kind="y"))
    assert строка(cur, "our_company", "status", "doc_kind") == ("ООО «Кордален»", "пусто", "y")
    # Известная другая компания — заменяет.
    записать(cur, колонки, запись(our_company="Mirvelta Trading LLC"))
    assert строка(cur, "our_company") == ("Mirvelta Trading LLC",)
    # Без сбоя пустое значение — факт: нашу компанию в карточке сняли.
    monkeypatch.setattr(ix, "_НАШИ_СБОЙ", False)
    записать(cur, колонки, запись(status="разобран"))
    assert строка(cur, "our_company") == (None,)


def test_переразбор_на_базе_до_и_после_миграции(cur, запись, monkeypatch):
    import library.reparse as r
    monkeypatch.setattr(r.indexer, "_НАШИ_СБОЙ", True)
    записать(cur, ix.КОЛОНКИ_ОБЯЗАТЕЛЬНЫЕ, запись())
    # До миграции: UPDATE по тем колонкам, что есть, проходит.
    колонки, нет_обяз, нет_свед = ix.колонки_записи(
        ix.колонки_базы(cur, r.КОЛОНКИ_ЗАПИСИ), r.ОБЯЗАТЕЛЬНЫЕ_ЗАПИСИ, r.СВЕДЕНИЯ_ЗАПИСИ)
    assert нет_обяз == [] and нет_свед == list(ix.КОЛОНКИ_СВЕДЕНИЙ)
    cur.execute(r.правка_файла(колонки), r.значения_правки(запись(status="разобран"), колонки))
    assert строка(cur, "status") == ("разобран",)
    # После миграции: пустая наша компания записанную не стирает, новая — заменяет.
    for оператор in миграция_сведений():
        cur.execute(оператор)
    колонки = r.КОЛОНКИ_ЗАПИСИ
    cur.execute(r.правка_файла(колонки), r.значения_правки(запись(our_company="ООО «Кордален»"),
                                                            колонки))
    cur.execute(r.правка_файла(колонки), r.значения_правки(запись(status="пусто"), колонки))
    assert строка(cur, "our_company", "status") == ("ООО «Кордален»", "пусто")


def test_одноимённая_таблица_другой_схемы_не_в_счёт(cur, capsys):
    """Колонки ищутся в той lib_files, куда пойдёт запись (схемы пути поиска).
    Копия таблицы в другой схеме — уже с миграцией — иначе сошла бы за свою, и
    вставка упала бы на первом сбросе буфера."""
    cur.execute(f"create schema {ЧУЖАЯ}")
    cur.execute(f"create table {ЧУЖАЯ}.lib_files (file_id text primary key, "
                + ", ".join(f"{к} text" for к in ix.КОЛОНКИ_СВЕДЕНИЙ) + ")")
    assert ix.проверить_колонки(cur) == ix.КОЛОНКИ_ОБЯЗАТЕЛЬНЫЕ
    assert "our_company" in capsys.readouterr().out
