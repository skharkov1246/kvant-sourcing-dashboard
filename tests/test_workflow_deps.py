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
import yaml

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
# Пакеты (каталоги с __init__.py) — тоже наши: «from tests import fixture» брало
# модуль «tests» за сторонний, потому что у __init__.py stem не совпадает с именем.
СВОИ |= {ф.parent.name for ф in ROOT.rglob("__init__.py")
         if not (set(ф.relative_to(ROOT).parts) & ИСКЛЮЧИТЬ)}
# Каталоги с кодом — тоже наши, даже без __init__.py: «from scripts import …»
# работает пространством имён, а сторож считал «scripts» сторонним пакетом.
СВОИ |= {ф.parent.name for ф in ROOT.rglob("*.py")
         if not (set(ф.relative_to(ROOT).parts) & ИСКЛЮЧИТЬ)}

# НЕОБЯЗАТЕЛЬНЫЕ ЗАВИСИМОСТИ — закрытым списком и с причиной на каждую.
# Модуль импортируется внутри функции, которая вызывается только после проверки
# доступности, поэтому его отсутствие — не поломка, а выключенная возможность.
# Список закрытый намеренно: открытое правило «пропускать импорты в функциях»
# погасило бы и настоящие пропуски.
НЕОБЯЗАТЕЛЬНЫЕ = {
    # gt/tools/bitrix_tkp.py: распознавание картинок включается, только если
    # ocr_available() нашёл tesseract; без него разбор просто идёт без OCR.
    "pytesseract",
    "PIL",
}


def файл_модуля(имя: str) -> pathlib.Path | None:
    """Путь к нашему модулю по имени. Скрипты кладут соседние каталоги в sys.path."""
    for кандидат in ROOT.rglob(f"{имя}.py"):
        if not (set(кандидат.relative_to(ROOT).parts) & ИСКЛЮЧИТЬ):
            return кандидат
    return None


def импорты(дерево: ast.AST, только_модульные: bool = False) -> set[str]:
    """Все импорты, КРОМЕ обёрнутых в try. Глубина не важна: ленивый импорт внутри
    функции всё равно выполнится, когда до него дойдёт прогон, — именно так и упал
    прогон 21.09.2026 (requests тянулся через bitrix_client внутри main).

    try/except пропускается: такой импорт объявлен необязательным намеренно.

    только_модульные — для ТЕСТОВ. Там вопрос другой: не «что выполнится, когда
    прогон дойдёт», а «чего не хватит, чтобы набор вообще собрался». Ленивый
    импорт внутри рабочей функции тест уронит сам и громко, а вот импорт в шапке
    ломает сбор всего набора — так гейт 21.09.2026 упал на «No module named yaml»
    и не выполнил ни одного теста. Считать для тестов ленивые импорты значило бы
    требовать от гейта pypdf ради ветки разбора PDF, которую тесты не трогают.
    """
    под_try: set[int] = set()
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Try):
            for внутри in ast.walk(узел):
                под_try.add(id(внутри))
    верхние = set(getattr(дерево, "body", []))
    out: set[str] = set()
    for узел in (верхние if только_модульные else ast.walk(дерево)):
        if id(узел) in под_try:
            continue
        if isinstance(узел, ast.Import):
            out |= {a.name.split(".")[0] for a in узел.names}
        elif isinstance(узел, ast.ImportFrom) and узел.level == 0 and узел.module:
            out.add(узел.module.split(".")[0])
    return out


def сторонние(путь: pathlib.Path, видели: set[pathlib.Path] | None = None,
              только_модульные: bool = False) -> set[str]:
    """Сторонние модули, нужные скрипту, включая тянущиеся через наши модули.

    Обход транзитивный: скрипт импортирует наш bitrix_client, тот — requests,
    и requests нужен прогону, хотя в самом скрипте его нет.
    """
    видели = видели if видели is not None else set()
    if путь in видели:
        return set()
    видели.add(путь)
    out: set[str] = set()
    for модуль in импорты(ast.parse(путь.read_text(encoding="utf-8")), только_модульные):
        if модуль in sys.stdlib_module_names:
            continue
        if модуль in СВОИ:
            свой = файл_модуля(модуль)
            if свой:
                out |= сторонние(свой, видели, только_модульные)
            continue
        out.add(модуль)
    return out


def _команды(узел) -> str:
    """Весь текст `run:` внутри job, включая шаги внутри вложенных структур."""
    куски: list[str] = []
    if isinstance(узел, dict):
        for ключ, значение in узел.items():
            if ключ == "run" and isinstance(значение, str):
                куски.append(значение)
            else:
                куски.append(_команды(значение))
    elif isinstance(узел, list):
        куски.extend(_команды(x) for x in узел)
    return "\n".join(k for k in куски if k)


def джобы():
    """(файл: job, текст его команд) — ПО КАЖДОЙ работе отдельно.

    Раньше текст брался файлом целиком, пока в каждом прогоне была одна работа.
    Вторая работа в suppliers-quotes.yml (замер цен) это сломала бы молча:
    объединённый текст видит «pip install requests …» соседней работы и считает
    зависимость поставленной, хотя у самой работы её нет. Ровно этот класс ошибки
    тест и создавался ловить, поэтому делим по работам, а не по файлам.
    """
    for файл in sorted(WORKFLOWS.glob("*.yml")):
        данные = yaml.safe_load(файл.read_text(encoding="utf-8")) or {}
        работы = данные.get("jobs") or {}
        if not работы:                       # не наш формат — не молчим, а смотрим целиком
            yield файл.name, файл.read_text(encoding="utf-8")
            continue
        for имя, тело in работы.items():
            yield f"{файл.name}: {имя}", _команды(тело)


def _запускает(текст: str) -> set[str]:
    """Файлы, которые работа исполняет: свои скрипты и тесты под pytest.

    ТЕСТЫ ЗДЕСЬ НЕ ЛИШНИЕ. 21.09.2026 гейт упал на «No module named yaml»:
    его уронил новый тест, а сторож смотрел только на скрипты прогонов и этой
    зависимости не видел. Тест — такой же исполняемый файл с импортами, и его
    зависимость ставится тем же pip install.
    """
    пути = set(re.findall(r"python\s+((?:scripts|library|gt/tools)/[\w/]+\.py)", текст))
    # ТОЛЬКО НАСТОЯЩИЙ ВЫЗОВ. Необязательное «python -m» ловило слово pytest внутри
    # «pip install … pytest psycopg2-binary», путей там нет — и проверка считала,
    # что работа гоняет ВЕСЬ набор тестов. Три ложные тревоги подряд.
    for вызов in re.findall(r"(?:python\s+-m\s+pytest|(?m:^\s*pytest))([^\n]*)", текст):
        явные = re.findall(r"(tests/[\w/]+\.py)", вызов)
        # «pytest -q» без путей — это ВЕСЬ набор тестов.
        пути |= set(явные) if явные else {
            str(ф.relative_to(ROOT)) for ф in (ROOT / "tests").glob("*.py")}
    return пути


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
    for относительный in sorted(_запускает(текст)):
        путь = ROOT / относительный
        if not путь.exists():
            continue
        # Для тестов считаем только импорты в шапке: см. пояснение в импорты().
        for модуль in сторонние(путь, только_модульные=относительный.startswith("tests/")):
            if модуль in НЕОБЯЗАТЕЛЬНЫЕ:
                continue
            пакет = ПАКЕТ.get(модуль, модуль)
            assert пакет in ставит or модуль in ставит, (
                f"{имя}: запускает {относительный}, которому нужен «{пакет}», "
                f"а pip install его не ставит")


def test_имена_работ_только_латиницей():
    """GitHub принимает в id работы только [A-Za-z_][A-Za-z0-9_-]*.

    Кириллическое имя не даёт понятной ошибки — прогон просто не запускается.
    То же правило, что для имён переменных в bash (CLAUDE.md, стиль работы):
    комментарии и названия по-русски, идентификаторы латиницей.
    """
    плохие = []
    for путь in sorted(ROOT.glob(".github/workflows/*.yml")):
        данные = yaml.safe_load(путь.read_text(encoding="utf-8")) or {}
        for job in (данные or {}).get("jobs", {}):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", str(job)):
                плохие.append(f"{путь.name}: {job}")
    assert not плохие, "нелатинские id работ: " + ", ".join(плохие)
