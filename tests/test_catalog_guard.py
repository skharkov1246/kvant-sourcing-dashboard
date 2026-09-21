"""Каталог данных не должен молча расходиться с коммитом.

Гейт упал на ветке claude/r1700-ibghje 17.09.2026: в рабочем дереве лежал файл
данных от фоновой разведки, не вошедший в коммит. Сборщик каталога берёт и
неотслеживаемые файлы — иначе --check краснеет на каждом PR, добавляющем файл
кода, — поэтому локально в каталоге оказалось 213 наборов, а в CI 212.
Сообщение при этом было одно: «каталог данных устарел». Чтобы понять, что
именно разошлось, пришлось разворачивать отдельный клон ветки.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("build_catalog", ROOT / "scripts" / "build_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_catalog"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_незакоммиченный_набор_называется_поимённо():
    mod = _load()
    cat = {"datasets": [
        {"path": "data/committed.json"},
        {"path": "data/лежит_в_дереве_но_не_в_git.json"},
    ]}
    mod.sh = lambda *a: "data/committed.json\ndata/other.json"
    assert mod.untracked_datasets(cat) == ["data/лежит_в_дереве_но_не_в_git.json"]
    # всё закоммичено — предупреждать не о чем
    mod.sh = lambda *a: "data/committed.json\ndata/лежит_в_дереве_но_не_в_git.json"
    assert mod.untracked_datasets(cat) == []


def test_проверка_каталога_объясняет_расхождение():
    """Сообщение обязано называть набор, а не только факт расхождения."""
    src = (ROOT / "scripts" / "build_catalog.py").read_text(encoding="utf-8")
    assert "в каталоге нет набора:" in src
    assert "в каталоге есть, а на диске нет:" in src
    assert "изменился набор" in src
    assert "untracked_datasets" in src
    # сравнение должно идти по пути набора, иначе поимённо назвать нечего
    assert 'strip = lambda c: {d["path"]: shape(d) for d in c["datasets"]}' in src


def test_имена_наборов_не_поминаются_в_комментариях_сборщика():
    """consumers() считает потребителем любой файл кода с именем набора внутри.

    Поэтому имя файла данных, названное в комментарии сборщика, добавляет сам
    сборщик в referenced_by этого набора — и каталог начинает врать о том, кто
    его читает. Проверка ловит ровно этот случай на самом сборщике.
    """
    mod = _load()
    me = "scripts/build_catalog.py"
    # Единственный набор, который сборщик читает по-настоящему, — пояснения к
    # каталогу. Всё остальное, где он назван потребителем, — упоминание имени
    # в комментарии.
    REAL = {"data/catalog_notes.json"}
    cat = mod.build()
    wrong = [d["path"] for d in cat["datasets"]
             if me in (d.get("referenced_by") or []) and d["path"] not in REAL]
    assert not wrong, (f"сборщик каталога попал в потребители наборов {wrong} — "
                       "скорее всего, имя файла названо в комментарии")
