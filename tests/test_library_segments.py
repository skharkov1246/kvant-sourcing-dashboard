"""Словарь сегментов, переклассификация и сводка по базе.

История. Разбор 22 526 вложений Битрикса дал 548 622 позиции номенклатуры, но
сегмент определялся по тексту строки, а при неудаче наследовался от файла целиком.
Что не распозналось ни там, ни там, легло в lib_demand с пустым segment_id: позиции
в базе есть, а ни в один разрез по оборудованию не попадают. Пока словарь жил внутри
indexer.py, переклассифицировать это было нечем — модуль требовал двух секретов уже
на импорте. Тесты ниже закрывают дыры, через которые словарь снова разъедется, а
журнал прогона снова начнёт печатать коммерческие данные.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEGMENTS_PY = ROOT / "library" / "segments.py"
INDEXER = ROOT / "library" / "indexer.py"
RECLASSIFY = ROOT / "library" / "reclassify.py"
STATS = ROOT / "scripts" / "library_stats.py"


def load():
    """Словарь грузится по пути: psycopg2 и requests в гейте не установлены."""
    spec = importlib.util.spec_from_file_location("kvant_segments", SEGMENTS_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_словарь_грузится_без_зависимостей():
    """Ради этого словарь и вынесли: у indexer.py на импорте requests и psycopg2."""
    seg = load()
    assert len(seg.SEGMENTS) >= 16, "сегментов стало меньше — часть спроса перестанет считаться"
    text = SEGMENTS_PY.read_text(encoding="utf-8")
    for heavy in ("import psycopg2", "import requests", "os.environ["):
        assert heavy not in text, f"в общий словарь просочилось {heavy}: тесты гейта его не импортируют"


def test_словарь_в_одном_месте():
    """Две копии разойдутся на первой правке, и одна позиция попадёт в разные сегменты."""
    ind = INDEXER.read_text(encoding="utf-8")
    assert "SEGMENTS: dict[str, tuple[str, list[str]]] = {" not in ind, (
        "словарь сегментов снова объявлен в indexer.py — источник истины должен быть один")
    assert "from segments import" in ind, "indexer.py не берёт словарь из общего модуля"
    assert "def classify(" not in ind, "classify снова продублирован в indexer.py"


def test_каждый_сегмент_описан():
    seg = load()
    for sid, (name, words) in seg.SEGMENTS.items():
        assert re.fullmatch(r"[a-z0-9_-]+", sid), f"код сегмента {sid} не годится в ключ базы"
        assert name.strip(), f"у сегмента {sid} нет названия"
        assert words, f"у сегмента {sid} нет ни одного слова — он никогда не сработает"
        for w in words:
            assert w == w.lower(), f"слово «{w}» в {sid} не в нижнем регистре: сравнение идёт по lower()"
            assert w.strip() == w and w.strip(), f"слово «{w}» в {sid} с лишними пробелами"


def test_отнесение_работает_на_живых_примерах():
    seg = load()
    for text, expect in [
        ("Насос центробежный ЦНС 300-360", "pumps"),
        ("Подшипник роликовый SKF 22315", "bearings"),
        ("Ротор турбины SGT-400, газотурбинная установка", "gtu"),
        ("Свеча зажигания газопоршневого двигателя Jenbacher J420", "gpu"),
        ("Задвижка клиновая Ду150 Ру16", "valves"),
    ]:
        assert seg.classify(text) == expect, f"«{text}» отнесено к {seg.classify(text)}, ожидался {expect}"
    assert seg.classify("болт М12 оцинкованный") is None, (
        "крепёж не должен уезжать в случайный сегмент: лучше пустой сегмент, чем чужой")
    assert seg.name_of(None) and "не определён" in seg.name_of(None)


def test_переклассификация_только_дополняет():
    """Скрипт не имеет права трогать уже проставленный сегмент и удалять строки."""
    t = RECLASSIFY.read_text(encoding="utf-8")
    assert "delete" not in t.lower().replace("delete_", ""), "в переклассификации есть удаление"
    assert "where d.id = v.id::bigint and d.segment_id is null" in t, (
        "update без условия segment_id is null: перезапишет уже определённые сегменты")
    assert "set segment_id" in t and t.lower().count("update lib_demand") == 1, (
        "переклассификация должна менять ровно одну колонку одной таблицы")
    assert 'os.environ.get("APPLY"' in t, "нет холостого режима: прогон сразу пишет в базу"


def test_переклассификация_не_печатает_коммерческие_данные():
    """Журнал публичного репозитория читает кто угодно."""
    t = RECLASSIFY.read_text(encoding="utf-8")
    for line in t.splitlines():
        if "print(" not in line:
            continue
        for leak in ("item_name", "part_number", "deal_id", "oem", "author", "body", "title"):
            assert leak not in line, f"в журнал уходит {leak}: {line.strip()[:70]}"


def test_сводка_печатает_только_агрегаты():
    t = STATS.read_text(encoding="utf-8")
    assert "select *" not in t.lower(), "сводка тянет строки целиком"
    for line in t.splitlines():
        if "print(" not in line:
            continue
        for leak in ("item_name", "body", "title", "author", "text"):
            assert leak not in line, f"в журнал уходит {leak}: {line.strip()[:70]}"
    assert "count(distinct author)" in t and "author," not in t, (
        "почты авторов правок можно только считать, но не печатать")


def test_добор_повторяет_только_несостоявшиеся_загрузки():
    """«Пусто» и «формат не читаем» — свойство файла, повтор даст тот же результат
    и удвоит стоимость прогона. «Не скачался» — сетевая осечка, её и повторяем."""
    ind = INDEXER.read_text(encoding="utf-8")
    assert "RETRY_FAILED" in ind, "нет режима добора не скачавшихся файлов"
    assert "where status <> 'не скачался'" in ind, (
        "добор должен исключать из «уже сделано» только не скачавшиеся файлы")
    wf = (ROOT / ".github" / "workflows" / "library-index.yml").read_text(encoding="utf-8")
    assert "RETRY_FAILED" in wf, "флаг добора не проброшен в workflow"


def test_секреты_индексатора_читаются_лениво():
    """os.environ[...] на импорте — это KeyError в py_compile и в тестах гейта."""
    ind = INDEXER.read_text(encoding="utf-8")
    assert 'os.environ["BITRIX_WEBHOOK_URL"]' not in ind, "секрет требуется уже на импорте модуля"
    assert 'os.environ["SUPABASE_DB_URL"]' not in ind, "секрет требуется уже на импорте модуля"
    assert 'if not os.environ.get(var)' in ind, "отсутствие секрета должно проверяться при запуске"


def test_workflow_сводки_не_просит_лишних_секретов():
    """Ни сводке, ни переклассификации Битрикс не нужен — они работают по базе."""
    wf = (ROOT / ".github" / "workflows" / "library-stats.yml").read_text(encoding="utf-8")
    assert "BITRIX_WEBHOOK_URL" not in wf, "лишний секрет в прогоне, которому он не нужен"
    assert "SUPABASE_DB_URL" in wf
    assert "reclassify-apply" in wf and "APPLY" in wf, "нет режима записи"


def load_reclassify():
    """Модуль импортируем целиком: psycopg2 в нём подключается уже внутри main(),
    поэтому правила наследования проверяются без базы и без зависимостей."""
    spec = importlib.util.spec_from_file_location("kvant_reclassify", RECLASSIFY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_наследование_по_большинству():
    """Спецификация — документ на одну поставку: строка без сегмента среди
    девяноста насосных берёт их сегмент. Смешанный файл остаётся без сегмента —
    чужой сегмент хуже пустого."""
    r = load_reclassify()
    уверенный = [("файл-1", "pumps", 90), ("файл-1", "valves", 10)]
    смешанный = [("файл-2", "pumps", 50), ("файл-2", "gtu", 50)]
    assert r.majority(уверенный, 0.6) == {"файл-1": "pumps"}
    assert r.majority(смешанный, 0.6) == {}
    assert r.majority(уверенный + смешанный, 0.6) == {"файл-1": "pumps"}
    assert r.majority(уверенный, 0.95) == {}, "порог не соблюдается"


def test_пороги_наследования_консервативны():
    """По сделке правило слабее, чем по файлу: в одной сделке бывает разнородная
    закупка. Порог сделки обязан быть строже."""
    r = load_reclassify()
    assert 0.5 < r.MIN_SHARE_FILE <= r.MIN_SHARE_DEAL <= 1.0, (
        "порог наследования по сделке должен быть не ниже, чем по файлу, и оба выше половины")


def test_разведка_не_печатает_наименования():
    """Частотный список — это отдельные слова, а не строки номенклатуры.
    Фильтр «только кириллица от шести букв и от MIN_FREQ повторов» не пропускает
    ни марку, ни парт-номер, ни фамилию: они либо латиницей, либо единичны."""
    diag = (ROOT / "library" / "diag_demand.py").read_text(encoding="utf-8")
    assert "[а-яё]{%d,}" in diag, "частотный список собирается не по кириллическим словам"
    assert "MIN_FREQ" in diag and "MIN_LEN" in diag, "нет порогов длины и частоты"
    for line in diag.splitlines():
        if "print(" in line:
            for leak in ("item_name", "deal_id", "part_number", "oem)"):
                assert leak not in line, f"в журнал уходит {leak}: {line.strip()[:70]}"
    assert "select item_name from lib_demand" in diag, "наименования нужны только для частот"
    wf = (ROOT / ".github" / "workflows" / "library-stats.yml").read_text(encoding="utf-8")
    assert "diag" in wf and "diag_demand.py" in wf, "разведка не подключена к workflow"
