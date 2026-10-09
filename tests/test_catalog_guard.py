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


def test_в_мелком_клоне_подпись_набора_не_переписывается():
    """Обрезанная история не должна подменять подпись «кто менял набор».

    09.10.2026 в каталоге на main у всех 317 наборов стоял ОДИН коммит
    709cb3e с автором github-actions[bot]: ночной прогон собирает каталог в
    клоне глубины 1, и `git log -1 -- <файл>` отдаёт ему границу обрезки.
    Проверка --check это терпела (поля подписи исключены из сравнения), и
    ошибка жила молча. Теперь в мелком клоне подпись берётся из прежнего
    каталога, а при его отсутствии честно говорит, что не определена."""
    mod = _load()
    прежнее = {"last_change": "2026-09-27", "last_author": "skharkov1246",
               "last_commit": "2a3312bd", "updated_by": "человек/агент"}
    mod.МЕЛКИЙ[0] = True
    try:
        assert mod.git_meta("data/какой-нибудь.json", прежнее) == прежнее
        пусто = mod.git_meta("data/какой-нибудь.json", None)
        assert пусто["last_commit"] is None
        assert "обрезан" in пусто["updated_by"]
        assert пусто["last_author"] is None
    finally:
        mod.МЕЛКИЙ[0] = False


def test_в_полном_клоне_подпись_считается_по_git():
    """В полной истории подпись берётся из git, а не из прежнего каталога.

    Иначе починка мелкого клона заморозила бы подпись навсегда."""
    mod = _load()
    mod.МЕЛКИЙ[0] = False
    взято = mod.git_meta("CLAUDE.md", {"last_commit": "ffffffff",
                                       "last_author": "никто",
                                       "last_change": "1999-01-01",
                                       "updated_by": "человек/агент"})
    assert взято["last_commit"] != "ffffffff", "подпись взята из прежнего каталога, а не из git"
    assert взято["last_change"] and взято["last_change"] > "2026-01-01"
