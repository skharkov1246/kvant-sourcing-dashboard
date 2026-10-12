"""Триггеры сборки дашборда: каждый свой модуль, который импортирует main.py, стоит
в списках путей deploy.yml и live-check.yml.

Зачем. Оба списка — разрешающие: правка файла вне списка не пересобирает портал
сразу (ждёт планировщика) и не проходит «Живую сверку метрик», без которой правку
расчёта не вливают (CLAUDE.md, правило 3). Модули presale.py (#500) и premia.py
(#508) вошли в сборку, а в списки не попали — этот тест ловит такое при новом модуле.
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Вёрстка, а не расчёт: живая сверка сравнивает данные вкладок, а не HTML.
НЕ_РАСЧЁТ = {"dashboard.py"}


def _свои_модули_main() -> set[str]:
    дерево = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    имена = set()
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Import):
            имена |= {a.name.split(".")[0] for a in узел.names}
        elif isinstance(узел, ast.ImportFrom) and узел.module and узел.level == 0:
            имена.add(узел.module.split(".")[0])
    return {f"{и}.py" for и in имена if (ROOT / f"{и}.py").is_file()}


def _пути(workflow: str, событие: str) -> set[str]:
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    триггеры = wf.get("on") or wf.get(True)   # PyYAML читает ключ on как True
    return set(триггеры[событие]["paths"])


def test_свои_модули_находятся_разбором_импортов():
    модули = _свои_модули_main()
    assert {"people.py", "premia.py", "presale.py", "dashboard.py"} <= модули
    assert "json.py" not in модули


def test_правка_любого_модуля_сборки_пересобирает_портал():
    нет = sorted(_свои_модули_main() - _пути("deploy.yml", "push"))
    assert not нет, f"deploy.yml, on.push.paths: нет {нет}"


def test_правка_любого_модуля_расчёта_проходит_живую_сверку():
    нет = sorted(_свои_модули_main() - НЕ_РАСЧЁТ - _пути("live-check.yml", "pull_request"))
    assert not нет, f"live-check.yml, on.pull_request.paths: нет {нет}"
