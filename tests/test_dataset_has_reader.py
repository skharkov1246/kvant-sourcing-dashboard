"""Набор, который читает только тот, кто его пишет, ни на что не влияет.

Найдено 18.09.2026 трижды подряд, и каждый раз стоило денег или доверия:

* gt/data/ship_price_substitutions.json называл три подставные цены, а сводка
  продолжала их содержать и складывать;
* правило о кириллических двойниках было записано, но до сверки с присланными
  предложениями не доходило — шесть строк не находились;
* gt/data/ship_source_control.json дисквалифицировал источник, чьи отрицательные
  ответы стоят за двадцатью тремя строками, но в отчёт не попадал вовсе.

Признак у всех один и проверяется механически: единственный инструмент,
упоминающий набор, — тот, который его и создаёт. Такой набор описывает дефект, а
не исправляет его, и разница видна только когда кто-то складывает числа.

Список исключений закрытый и объяснённый: набор попадает в него, если у него по
устройству не может быть второго читателя.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "gt/data"
CODE = [ROOT / "gt/tools", ROOT / "scripts", ROOT / "library"]

# Наборы, у которых второго читателя быть и не должно, с причиной.
EXEMPT = {
    # сырьё и снимки: их пишут снаружи, читают сборщики по имени каталога
    "bitrix_gt.json": "пишет workflow, читают сборщики страниц",
    "bitrix_history.json": "архив, читается вручную при разборе",
    "bitrix_supplier_sites.json": "справочник, читается вручную",
    "fx_rates.json": "курсы: читаются многими, но через общий хелпер",
    # наборы-выгрузки, чья единственная задача — быть прочитанными человеком
    "ship_blocked.json": "список закрытых каналов, входит в сводку через ship_merge",
    "ship_recheck.json": "вход сводки, читается ship_merge",
    "ship_sweep.json": "вход сводки, читается ship_merge",
    "ship_sellers.json": "вход сводки, читается ship_merge",
}


def readers(name: str) -> set[str]:
    out = set()
    for d in CODE:
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            try:
                if name in p.read_text(encoding="utf-8", errors="ignore"):
                    out.add(p.name)
            except OSError:
                continue
    return out


def producer(name: str) -> str:
    """Имя инструмента, который набор создаёт: ship_foo.json -> ship_foo.py."""
    return name[:-5] + ".py"


def test_у_каждого_набора_есть_читатель_кроме_его_создателя():
    lonely = []
    for p in sorted(DATA.glob("*.json")):
        if p.name in EXEMPT:
            continue
        rs = readers(p.name)
        if not rs:
            continue          # набор вообще никем не упомянут — другая проверка
        if rs <= {producer(p.name)}:
            lonely.append(p.name)
    assert not lonely, (
        "набор читает только тот инструмент, который его пишет, — значит он "
        f"описывает дефект, а не исправляет его: {lonely}. Либо подключите его к "
        "счёту и к отчёту, либо внесите в EXEMPT с объяснением, почему второго "
        "читателя быть не может.")


def test_исключения_объяснены():
    """Исключение без причины через месяц неотличимо от забытого набора."""
    for name, why in EXEMPT.items():
        assert why.strip(), name
        assert re.search(r"[а-яё]", why), f"причина не на русском: {name}"
