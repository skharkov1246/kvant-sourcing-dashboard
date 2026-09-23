"""Причина «шапки нет» доезжает до базы ОБЕИМИ вставками.

Правило 14 (CLAUDE.md): две записи в одну таблицу правятся вместе. У lib_files
таких записей две — пакетная в library/indexer.py и построчный UPDATE в
library/reparse.py. Колонка, добавленная в одну, роняет вторую на каждом файле,
а файл к тому моменту уже отмечен разобранным.

Проверка читает исходники, а не базу: обе записи должны называть header_miss,
и колонка должна быть заведена в схеме. Разбор исходника читает КОД, а не
комментарии, поэтому строки с `--` и `#` снимаются (CLAUDE.md, стиль работы).
"""
from __future__ import annotations

import re
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent


def код(путь: str) -> str:
    текст = (КОРЕНЬ / путь).read_text(encoding="utf-8")
    без_sql = re.sub(r"--[^\n]*", "", текст)
    return re.sub(r"(?<!\w)#[^\n]*", "", без_sql)


def запрос(текст: str, начало: str, конец: str) -> str:
    """Сам запрос, а не весь файл.

    Первая редакция этой проверки считала вхождения «header_miss» по файлу
    целиком — и мутация «снять колонку из UPDATE» её ПЕРЕЖИЛА: счёт добирался
    строками разбора причин, которые к записи отношения не имеют. Проверять надо
    тот отрезок кода, который исполняется при записи.
    """
    i = текст.index(начало)
    return текст[i:текст.index(конец, i) + len(конец)]


def test_пакетная_вставка_indexer_несёт_причину():
    вставка = запрос(код("library/indexer.py"), "insert into lib_files", "processed_at = now()")
    assert "header_miss" in вставка.split("values")[0], "нет в списке колонок"
    assert "header_miss = excluded.header_miss" in вставка, "нет в on conflict do update"


def test_значение_причины_подставляется_в_кортеж_вставки():
    строки = код("library/indexer.py")
    кортеж = запрос(строки, "buf_files.append((", "PARSER_VERSION))")
    assert 'rec.get("header_miss")' in кортеж, "колонка есть, а значения в кортеже нет"


def test_построчный_update_переразбора_несёт_причину():
    правка = запрос(код("library/reparse.py"), "update lib_files set", "where file_id = %s")
    assert "header_miss = %s" in правка, "нет в set"
    хвост = код("library/reparse.py")
    i = хвост.index("where file_id = %s")
    assert 'rec.get("header_miss")' in хвост[i:i + 600], "нет значения в кортеже UPDATE"


def test_колонка_заведена_в_схеме_добавлением_а_не_созданием():
    """`create table if not exists` существующую таблицу НЕ меняет (правило 21)."""
    схема = код("library/supabase/schema_junk.sql")
    m = re.search(r"alter table lib_files add column if not exists\s+header_miss", схема)
    assert m, "колонки header_miss нет в схеме или она заведена не через alter"
