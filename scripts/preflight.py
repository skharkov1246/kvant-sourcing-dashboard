#!/usr/bin/env python3
"""Прогон гейта на своей машине одной командой — до пуша, а не после.

ЗАЧЕМ. Гейт проверяет восемь вещей, и забыть одну из них легко: правки они
требуют разной. 18.09.2026 гейт упал на трёх коммитах подряд по одной и той же
причине — отчёт начал читать два новых набора, у них изменилось поле «кем
используется», а каталог не пересобрали. Тесты при этом были зелёные, ruff
чистый, и локально ничто не намекало на поломку. Цена ошибки — красный PR и
прогон в CI впустую.

ЧТО ЗДЕСЬ ЕСТЬ. Ровно те шаги гейта, которые можно выполнить без сети и без
секретов: ruff, компиляция всех модулей, тесты ядра, актуальность каталога и
поискового указателя, разметка узлов не хуже порога, smoke-сборка дашборда с
проверкой разметки, и сверка бот-файлов с базовой веткой.

ЧЕГО ЗДЕСЬ НЕТ. Шагов, требующих сети или узлов CI: установки зависимостей,
проверки синтаксиса гейтов Cloudflare, поиска секретов в изменённых файлах.
Зелёный прогон здесь — не гарантия зелёного гейта, а отсечение восьми из
одиннадцати способов его уронить.

    python scripts/preflight.py            # всё
    python scripts/preflight.py --fix      # пересобрать каталог и указатель, если устарели
    python scripts/preflight.py --quick    # без тестов и smoke-сборки
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE = """
import sys, types
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
sys.path.insert(0, ".")
import dashboard
from tests import fixture
dashboard.write(fixture.build_metrics(), {"source": "rules", "items": []},
                "smoke/index.html", people=fixture.build_people(),
                reps=fixture.build_reps(), advisor=fixture.build_advisor())
"""
BOT_FILES = ("data/chat_snapshot.json", "data/budget_snapshot.json", "gt/data/bitrix_gt.json")


def run(name: str, cmd: list[str], stdin: str | None = None) -> tuple[str, bool, str, float]:
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=ROOT, input=stdin, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return name, p.returncode == 0, out.strip(), time.monotonic() - t0


def compile_all() -> tuple[str, bool, str, float]:
    t0 = time.monotonic()
    files = [str(f) for f in ROOT.rglob("*.py")
             if ".venv" not in f.parts and "reports" not in f.parts]
    p = subprocess.run([sys.executable, "-m", "py_compile", *files],
                       cwd=ROOT, capture_output=True, text=True)
    return ("компиляция всех модулей", p.returncode == 0,
            (p.stdout + p.stderr).strip() or f"{len(files)} файлов", time.monotonic() - t0)


def bot_files() -> tuple[str, bool, str, float]:
    """Бот-файлы не должны попадать в диффе PR: мерж откатил бы свежие данные."""
    t0 = time.monotonic()
    base = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "origin/main"],
                          cwd=ROOT, capture_output=True, text=True)
    if base.returncode != 0:
        return "бот-файлы не тронуты", True, "origin/main недоступна — шаг пропущен", 0.0
    d = subprocess.run(["git", "diff", "--name-only", "origin/main...HEAD"],
                       cwd=ROOT, capture_output=True, text=True)
    bad = [f for f in d.stdout.split() if f in BOT_FILES]
    msg = ("в диффе изменены файлы, которыми управляют боты: " + ", ".join(bad) +
           "\nверните их состоянием базовой ветки: git checkout origin/main -- " +
           " ".join(bad)) if bad else "не тронуты"
    return "бот-файлы не тронуты", not bad, msg, time.monotonic() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="пересобрать каталог и указатель, если устарели")
    ap.add_argument("--quick", action="store_true", help="без тестов и smoke-сборки")
    a = ap.parse_args()
    py = sys.executable

    # ruff в окружении может стоять отдельной командой, а не модулем python —
    # тогда «python -m ruff» не найдёт его и шаг соврёт про ошибки кода.
    ruff_cmd = ([py, "-m", "ruff", "check", "."]
                if subprocess.run([py, "-c", "import ruff"], cwd=ROOT,
                                  capture_output=True).returncode == 0
                else ["ruff", "check", "."])
    steps = [("ruff — ошибки кода", ruff_cmd, None)]
    if not a.quick:
        steps.append(("тесты ядра", [py, "-m", "pytest", "-q"], None))
        # Тесты прав доступа на node. Гейт их гонял, а этот скрипт — нет, и это
        # ровно та дыра, ради закрытия которой он написан: 20.09.2026 правка
        # access/acl.js прошла зелёный preflight и упала бы на гейте. Если node
        # в окружении нет, шаг честно говорит об этом, а не молчит.
        if shutil.which("node"):
            steps.append(("тесты прав доступа (node)",
                          ["node", "--test", *sorted(
                              str(x.relative_to(ROOT))
                              for x in (ROOT / "access" / "test").glob("*.test.mjs"))], None))
        else:
            print("⚠ node не найден: тесты прав доступа пропущены, гейт их всё равно прогонит")
    steps += [
        ("каталог данных актуален", [py, "scripts/build_catalog.py", "--check"], None),
        ("поисковый указатель актуален", [py, "scripts/build_index.py", "--check"], None),
        ("разметка узлов не хуже порога",
         [py, "scripts/library_units_check.py", "--min-precision", "84",
          "--min-coverage", "78"], None),
    ]
    if not a.quick:
        steps.append(("smoke-сборка дашборда", [py, "-"], SMOKE))

    results = [compile_all()]
    for name, cmd, stdin in steps:
        results.append(run(name, cmd, stdin))
        if name == "smoke-сборка дашборда" and results[-1][1]:
            results.append(run("разметка дашборда",
                               [py, "scripts/validate_dashboard.py", "smoke/index.html"]))
    results.append(bot_files())

    bad = [r for r in results if not r[1]]
    stale = [r for r in bad if "каталог" in r[0] or "указатель" in r[0]]
    if a.fix and stale:
        print("пересобираю каталог и указатель…")
        subprocess.run([py, "scripts/build_catalog.py"], cwd=ROOT)
        subprocess.run([py, "scripts/build_index.py"], cwd=ROOT)
        return main()

    width = max(len(r[0]) for r in results)
    for name, ok, out, sec in results:
        print(f"{'✓' if ok else '✗'} {name:<{width}}  {sec:5.1f} с")
        if not ok:
            for line in out.splitlines()[-12:]:
                print(f"    {line}")
    if bad:
        print(f"\nНЕ ПРОЙДЕНО: {len(bad)} из {len(results)}. Пуш делать рано.")
        if stale:
            print("Каталог или указатель устарели — это чинится так:")
            print("  python scripts/build_catalog.py && python scripts/build_index.py")
            print("  git commit -am 'каталог и индекс'")
            print("либо одной командой: python scripts/preflight.py --fix")
        return 1
    print(f"\nвсё чисто: {len(results)} шагов. Эти же проверки делает гейт, "
          "кроме требующих сети.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
