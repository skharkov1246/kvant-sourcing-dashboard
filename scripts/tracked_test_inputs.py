#!/usr/bin/env python3
"""Файлы, которые читают тесты, лежат в репозитории, а не только на диске.

ЗАЧЕМ. 22.09.2026 страница public/nomenclature.html прошла зелёный preflight и
уронила гейт: `.gitignore` глушит `public/*.html` целиком, а исключения заведены
поимённо. Локально тест открывал файл с диска и проходил; на раннере файла не
было вовсе — «ENOENT: no such file or directory».

Ошибка целого класса, а не одна. Всё, что тест читает по пути, обязано быть под
версией; иначе зелёный прогон говорит о состоянии рабочей копии, а не о
состоянии репозитория, и это две разные вещи. Проверка дешёвая: пути в тестах
написаны буквально.

    python scripts/tracked_test_inputs.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Где искать ссылки на файлы. Тесты на node открывают их через new URL(...),
# питоновские — через ROOT / "..." либо строкой пути.
ГДЕ = (
    ("access/test", "*.test.mjs"),
    ("tests", "*.py"),
)

# СОЗНАТЕЛЬНО НЕ В РЕПОЗИТОРИИ. Путь, который тест читает ПОД ПРОВЕРКОЙ наличия
# («if page.exists()»), отсутствием не ломается: тест сам решает, что проверять.
# Такие пути перечислены здесь с причиной и печатаются в каждом прогоне — список,
# который не видно, растёт молча и перестаёт быть решением.
СОЗНАТЕЛЬНО_НЕ_В_РЕПОЗИТОРИИ = {
    "zip/public/chain.html":
        "артефакт сборки (scripts/build_chain_page.py); tests/test_dict.py "
        "читает его под «if page.exists()» и без него просто не проверяет страницу",
}

# new URL('../../public/library.html', import.meta.url)
URL_JS = re.compile(r"""new\s+URL\(\s*['"]([^'"]+)['"]\s*,\s*import\.meta\.url""")
# ROOT / "scripts" / "publish_crossref.py"  либо  ROOT / "public/suppliers.html"
ROOT_PY = re.compile(r"""ROOT\s*/\s*((?:['"][^'"]+['"]\s*/\s*)*['"][^'"]+['"])""")
КУСОК = re.compile(r"""['"]([^'"]+)['"]""")


def отслеживаемые() -> set[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         text=True, check=True)
    return {x for x in out.stdout.split("\0") if x}


def ссылки() -> dict[str, list[str]]:
    найдено: dict[str, list[str]] = {}
    for папка, маска in ГДЕ:
        для_поиска = (ROOT / папка)
        if not для_поиска.is_dir():
            continue
        for файл in sorted(для_поиска.rglob(маска)):
            текст = файл.read_text(encoding="utf-8", errors="replace")
            пути = []
            for m in URL_JS.finditer(текст):
                путь = (файл.parent / m.group(1)).resolve()
                пути.append(путь)
            for m in ROOT_PY.finditer(текст):
                части = КУСОК.findall(m.group(1))
                if части:
                    пути.append((ROOT / Path(*части)).resolve())
            for путь in пути:
                try:
                    отн = путь.relative_to(ROOT).as_posix()
                except ValueError:
                    continue            # путь вне репозитория — не наше дело
                найдено.setdefault(отн, []).append(
                    файл.relative_to(ROOT).as_posix())
    return найдено


def main() -> int:
    под_версией = отслеживаемые()
    нарушения = []
    проверено = 0
    for путь, кто in sorted(ссылки().items()):
        # Каталоги и несуществующие пути не проверяем: первое не файл, второе
        # ловится самим тестом, и внятнее, чем здесь.
        if not (ROOT / путь).is_file():
            continue
        проверено += 1
        if путь in СОЗНАТЕЛЬНО_НЕ_В_РЕПОЗИТОРИИ:
            continue
        if путь not in под_версией:
            нарушения.append((путь, sorted(set(кто))))

    if нарушения:
        print("Файлы читаются тестами, но в репозитории их НЕТ. Локально такой "
              "прогон зелёный, на раннере — ENOENT:")
        for путь, кто in нарушения:
            print(f"  {путь}   ← {', '.join(кто)}")
            # Подсказка по самой частой причине: правило .gitignore.
            игнор = subprocess.run(["git", "check-ignore", "-v", путь], cwd=ROOT,
                                   capture_output=True, text=True)
            if игнор.returncode == 0 and игнор.stdout.strip():
                print(f"      глушит: {игнор.stdout.strip()}")
                print("      лечится исключением «!<путь>» рядом с правилом")
        return 1
    print(f"чисто: {проверено} путей, на которые ссылаются тесты, под версией")
    for путь, причина in sorted(СОЗНАТЕЛЬНО_НЕ_В_РЕПОЗИТОРИИ.items()):
        if (ROOT / путь).is_file() and путь not in под_версией:
            print(f"  осознанное исключение: {путь} — {причина}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
