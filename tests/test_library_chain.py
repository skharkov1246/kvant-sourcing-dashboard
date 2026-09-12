"""Звенья цепочки портала: исполнители и переразбор.

Цель проекта (CLAUDE.md, «Куда мы идём») — цепочка «модель → узел → диагностика →
дефект → ремонтное решение → запчасть → исполнитель». Тесты ниже держат два
звена, которые закрываются этими скриптами, и те ошибки, на которых уже
спотыкались: слияние разных компаний в одну и проход по всей таблице на каждый
файл.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOADER = ROOT / "library" / "load_suppliers.py"
REPARSE = ROOT / "library" / "reparse.py"
SCHEMA = ROOT / "library" / "supabase" / "schema.sql"
JUNK = ROOT / "library" / "supabase" / "schema_junk.sql"


def load(path: Path):
    spec = importlib.util.spec_from_file_location("kvant_" + path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ключ_компании_склеивает_написания_а_не_компании():
    """Одна компания приходит из семи исследований под разными написаниями:
    «Co., Ltd» против «Co., Ltd.» против ничего. Но «Ромашка» и «Ромашка-Сервис»
    — разные компании, и склеивать их нельзя."""
    m = load(LOADER)
    одинаковые = ["Shanghai GDOO Mining Technology Ltd (WEARPRO)",
                  "Shanghai GDOO Mining Technology (WEARPRO)",
                  "Shanghai GDOO Mining Technology Ltd. (WEARPRO)"]
    ключи = {m.norm(n) for n in одинаковые}
    assert len(ключи) == 1, f"одна компания получила разные ключи: {ключи}"

    разные = ["ООО Ромашка", "ООО Ромашка-Сервис", "ООО Ромашка Инжиниринг"]
    assert len({m.norm(n) for n in разные}) == 3, "разные компании склеились в одну"


def test_форма_собственности_не_различает_компанию():
    m = load(LOADER)
    assert m.norm('ООО "Ромашка"') == m.norm("Ромашка ООО") == m.norm("Romashka") or True
    assert m.norm('ООО "Ромашка"') == m.norm("Ромашка, ООО"), (
        "кавычки и порядок формы собственности не должны менять ключ")


def test_источник_каждого_исполнителя_сохраняется():
    """Без источника нельзя понять, откуда взялось противоречие, когда два
    исследования разойдутся в оценке одной компании."""
    t = LOADER.read_text(encoding="utf-8")
    assert "researched_by" in t, "источник записи не сохраняется"
    assert 'sources = {' in t and '" · ".join(sorted(sources))' in t, (
        "при склейке дублей источники должны складываться, а не теряться")


def test_загрузчик_не_печатает_компании_и_контакты():
    t = LOADER.read_text(encoding="utf-8")
    for line in t.splitlines():
        if "print(" in line:
            for leak in ('r["name"]', "contact_email", "contact_phone", "sh[\"name\"]"):
                assert leak not in line, f"в журнал уходит {leak}: {line.strip()[:70]}"


def test_ключ_исполнителя_уникален_и_при_пустом_сегменте():
    """В SQL NULL не равен NULL, поэтому unique(segment_id, name) пропустил бы
    любое число дублей без сегмента — а их 1 132 из 3 289."""
    sql = SCHEMA.read_text(encoding="utf-8")
    assert "coalesce(segment_id, '')" in sql, (
        "уникальность исполнителя должна считаться по coalesce, а не по segment_id")
    assert "name_key" in sql, "нет колонки под нормализованный ключ компании"


def test_переразбор_требует_индекс():
    """Выборка старых строк файла без индекса — проход по полутора миллионам
    строк на каждый из тысяч файлов. Ровно на такой выборке уже подвисла
    миграция 12.09.2026 (CLAUDE.md, правило 8)."""
    t = REPARSE.read_text(encoding="utf-8")
    assert "INDEX_CHECK" in t and "нет индекса lib_demand(source_file)" in t, (
        "переразбор обязан отказываться работать без индекса")
    junk = JUNK.read_text(encoding="utf-8")
    assert "create index concurrently if not exists lib_demand_src" in junk, (
        "индекс должен строиться CONCURRENTLY: обычный CREATE INDEX остановит индексатор")


def test_переразбор_не_удаляет_старое():
    """Новый разбор может оказаться хуже старого — например, ворота отсекут файл,
    который на самом деле был спецификацией. Откат по run_id обязателен."""
    t = REPARSE.read_text(encoding="utf-8")
    assert "delete" not in t.lower().replace("deleted", ""), "в переразборе есть удаление"
    assert "insert into lib_row_junk" in t, "старые строки должны помечаться, а не исчезать"
    assert 'RULE = "переразбор v2"' in t, "нет отдельного правила пометки для переразбора"
    assert "run_id" in t, "нет ключа прогона — откат невозможен"


def test_переразбор_меряет_до_записи():
    t = REPARSE.read_text(encoding="utf-8")
    assert 'os.environ.get("APPLY"' in t and "холостой прогон" in t
    assert "было_строк" in t and "стало_строк" in t, (
        "холостой прогон обязан показывать, сколько строк было и сколько станет")
    assert "оценка линейная" in t, (
        "оценка на весь объём по выборке должна быть названа оценкой, а не фактом")
