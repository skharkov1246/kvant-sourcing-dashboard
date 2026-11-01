"""Сторож шага 0 «Подтверждение передачи в ОРК» (решение владельца 31.10.2026): шаг 0 — первый в
ork.STEPS, а 0 в Python и JS ложно. Проверка шага на истинность («есть шаг?») молча теряет шаг 0:
заказ без хозяина, без ячейки шага, путь «с 1» (plan шага 0, раздел 1, риск номер один). Шаг
проверяют только на None (null) — сторож запрещает известные шаблоны истинности в модулях
«Реализации» и в шаблоне вкладки.

Шаблоны — таблицей (правило 4); новый найденный шаблон — строкой сюда.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PY_FILES = ("ork.py", "contracts.py", "orkview.py", "lifecycle.py", "dealctx.py", "dealdigest.py", "kamqueue.py")
TEMPLATE = "templates/dashboard_core.html"
# шаблон → почему нельзя
FORBIDDEN = (
    (r"\bif step else\b", "шаг 0 — ложь: «x if step else y» теряет шаг 0"),
    (r"\bif ostep\b(?!\s+is\b)", "шаг заказа проверяют на None: «if ostep is not None»"),
    (r"\[\"step\"\]\s+and\b", "x[\"step\"] and … — шаг 0 ложен"),
    (r"\.get\(\"step\"\)\s+(?:or|and)\b", "x.get(\"step\") or/and … — шаг 0 ложен"),
    (r"\bif\s+\w+(?:\.get\(\"step\"\)|\[\"step\"\])\s+else\b", "… if x[\"step\"] else … — шаг 0 ложен"),
    (r"\b(?:and|if|not)\s+\w+(?:\[\"step\"\]|\.get\(\"step\"\))\s*(?:\]|\)|:|$)",
     "… and x[\"step\"]] / if x[\"step\"]: — шаг 0 ложен"),
    (r"\b(?:o?step|st)\s+or\s+\d", "st or 1 — шаг 0 подменяется"),
    (r"\bo\.step\s*\|\|", "o.step||… — шаг 0 подменяется (JS)"),
    (r"!o\.step\b", "!o.step — шаг 0 «нет шага» (JS)"),
    # «last[0]===o.step?…» — сравнение, а не проверка на истинность: перед o.step — не знак сравнения
    (r"(?<![=<>!])\bo\.step\?(?![.?])", "o.step?…:… — шаг 0 идёт в ветку «нет шага» (JS): o.step!=null?…"),
)


def _hits(path: Path) -> list[str]:
    out = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0] if path.suffix == ".py" else line
        for rx, why in FORBIDDEN:
            if re.search(rx, code):
                out.append(f"{path.name}:{no}: {why}: {line.strip()[:120]}")
    return out


@pytest.mark.parametrize("name", PY_FILES)
def test_нет_проверки_шага_на_истинность_python(name):
    hits = _hits(ROOT / name)
    assert not hits, "\n".join(hits)


def test_нет_проверки_шага_на_истинность_шаблон():
    hits = _hits(ROOT / TEMPLATE)
    assert not hits, "\n".join(hits)


def test_сторож_ловит_известные_ошибки():
    """Шаблоны сторожа ловят то, что было в коде до шага 0 (корпус придуман: строки — образцы)."""
    bad = ['"role": EXECUTOR.get(step) if step else None',
           "lo['own'] = ork.owner(ostep, people) if ostep else {}",
           '_live_st = [x for x in ords_det if x["live"] and x["step"]]',
           'step = c.get("step") or r.get("dealStep")',
           '**({"step": x["step"]} if x.get("step") else {})',
           "st = max(st or 1, max(fl))",
           "n<(o.step||14)", "if(!o.step) return", "s:o.step?String(o.step):''"]
    good = ['"role": EXECUTOR.get(step) if step is not None else None',
            "if ostep is not None else {}", 'x["step"] is not None', "o.step!=null?String(o.step):''",
            "const S=D.step||{}", "o.step??14", "o.step?.x", "last[0]===o.step?last[2]:null",
            'if x["step"] is not None:', 'x["live"] and x["step"] is not None]']
    for s in bad:
        assert any(re.search(rx, s) for rx, _w in FORBIDDEN), s
    for s in good:
        assert not any(re.search(rx, s) for rx, _w in FORBIDDEN), s
