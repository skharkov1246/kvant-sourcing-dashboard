"""Прогон ставит то, что импортируют его скрипты.

ЗАЧЕМ. Дважды подряд прогон падал на ModuleNotFoundError, и оба раза причина
одна: в job добавили шаг, который запускает скрипт с другим набором импортов, а
строку `pip install` не тронули. 20.09.2026 — «No module named pytest» в сведении,
21.09.2026 — «No module named requests» в публикации снимка. Оба раза это
обнаруживалось прогоном на живых данных, то есть дорого.

ЧТО ПРОВЕРЯЕТСЯ. Для каждого job: какие скрипты он запускает, какие сторонние
модули эти скрипты импортируют на верхнем уровне, и есть ли они в pip install
того же job. Импорты внутри функций не в счёт — они ленивые по замыслу
(так, например, psycopg2 в замерах), и требовать их установки нельзя.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# Имя модуля → имя пакета, когда они различаются.
ПАКЕТ = {"psycopg2": "psycopg2-binary", "yaml": "pyyaml", "dotenv": "python-dotenv",
         "PIL": "pillow", "fitz": "pymupdf", "bs4": "beautifulsoup4"}
# Стандартная библиотека и наши собственные модули ставить не нужно. Свои ищем по
# всему дереву: скрипты добавляют в sys.path соседние каталоги (pnw/tools, scripts),
# и модуль оттуда импортируется по имени, как сторонний.
ИСКЛЮЧИТЬ = {".venv", "node_modules", ".git", "smoke"}
СВОИ = {ф.stem for ф in ROOT.rglob("*.py")
        if not (set(ф.relative_to(ROOT).parts) & ИСКЛЮЧИТЬ)}


def файл_модуля(имя: str) -> pathlib.Path | None:
    """Путь к нашему модулю по имени. Скрипты кладут соседние каталоги в sys.path."""
    for кандидат in ROOT.rglob(f"{имя}.py"):
        if not (set(кандидат.relative_to(ROOT).parts) & ИСКЛЮЧИТЬ):
            return кандидат
    return None


def импорты(дерево: ast.AST) -> set[str]:
    """Все импорты, КРОМЕ обёрнутых в try. Глубина не важна: ленивый импорт внутри
    функции всё равно выполнится, когда до него дойдёт прогон, — именно так и упал
    прогон 21.09.2026 (requests тянулся через bitrix_client внутри main).

    try/except пропускается: такой импорт объявлен необязательным намеренно.
    """
    под_try: set[int] = set()
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Try):
            for внутри in ast.walk(узел):
                под_try.add(id(внутри))
    out: set[str] = set()
    for узел in ast.walk(дерево):
        if id(узел) in под_try:
            continue
        if isinstance(узел, ast.Import):
            out |= {a.name.split(".")[0] for a in узел.names}
        elif isinstance(узел, ast.ImportFrom) and узел.level == 0 and узел.module:
            out.add(узел.module.split(".")[0])
    return out


def сторонние(путь: pathlib.Path, видели: set[pathlib.Path] | None = None) -> set[str]:
    """Сторонние модули, нужные скрипту, включая тянущиеся через наши модули.

    Обход транзитивный: скрипт импортирует наш bitrix_client, тот — requests,
    и requests нужен прогону, хотя в самом скрипте его нет.
    """
    видели = видели if видели is not None else set()
    if путь in видели:
        return set()
    видели.add(путь)
    out: set[str] = set()
    for модуль in импорты(ast.parse(путь.read_text(encoding="utf-8"))):
        if модуль in sys.stdlib_module_names:
            continue
        if модуль in СВОИ:
            свой = файл_модуля(модуль)
            if свой:
                out |= сторонние(свой, видели)
            continue
        out.add(модуль)
    return out


def джобы():
    """(файл прогона, имя job, текст всех его команд) — по одному на job."""
    for файл in sorted(WORKFLOWS.glob("*.yml")):
        текст = файл.read_text(encoding="utf-8")
        # Грубое, но достаточное деление: у наших прогонов по одному job в файле.
        # Если появится второй, тест увидит объединённый текст и станет мягче,
        # а не соврёт в другую сторону.
        yield файл.name, текст


@pytest.mark.parametrize("имя,текст", list(джобы()))
def test_прогон_ставит_то_что_импортирует(имя, текст):
    ставит = " ".join(re.findall(r"pip install[^\n]*", текст))
    # «pip install -r requirements.txt» ставит всё, что в файле перечислено:
    # подклеиваем его содержимое, иначе тест соврёт на прогонах, которые так и
    # устроены.
    for требования in re.findall(r"pip install[^\n]*-r\s+(\S+)", текст):
        файл = ROOT / требования
        if файл.exists():
            ставит += " " + файл.read_text(encoding="utf-8")
    for относительный in set(re.findall(r"python\s+((?:scripts|library|gt/tools)/[\w/]+\.py)", текст)):
        путь = ROOT / относительный
        if not путь.exists():
            continue
        for модуль in сторонние(путь):
            пакет = ПАКЕТ.get(модуль, модуль)
            assert пакет in ставит or модуль in ставит, (
                f"{имя}: запускает {относительный}, которому нужен «{пакет}», "
                f"а pip install его не ставит")
