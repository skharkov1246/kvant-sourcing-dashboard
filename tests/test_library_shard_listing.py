"""Часть прогона читает свой диапазон сделок, а не весь портал.

Переразбор 21.09.2026 умер во ВСЕХ двенадцати частях с HTTP 429: каждая часть
перечисляла все сделки и все их файловые поля. Деление по остатку после полного
перечисления спасло чтение полей, но не сам список: 24.09.2026 холостой
переразбор сделок за 3 650 дней умер в 44 частях из 50 на crm.deal.list.
Теперь часть фильтрует список сделок своим диапазоном номеров на стороне портала,
как карточки запросов. Диапазоны обязаны быть честным разбиением.

Список идентификаторов придуман здесь (правило 18).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))


def _части(макс, shards):
    import indexer
    return [indexer.диапазон_части(макс, s, shards) for s in range(shards)]


def _в_части(i, низ, верх):
    return i > низ and (верх is None or i <= верх)


def test_диапазоны_вместе_дают_весь_список_и_не_пересекаются():
    ids = list(range(1, 1001, 7))
    for shards in (10, 12, 25, 50):
        части = _части(max(ids), shards)
        for i in ids:
            assert sum(_в_части(i, н, в) for н, в in части) == 1, (i, shards)


def test_одна_часть_берёт_весь_список():
    import indexer
    assert indexer.диапазон_части(500, 0, 1) == (0, None)


def test_список_сделок_фильтруется_на_портале_диапазоном():
    """Само деление бесполезно, если crm.deal.list по-прежнему зовётся без него."""
    src = (ROOT / "library/indexer.py").read_text(encoding="utf-8")
    тело = src.split("def collect_refs(", 1)[1].split("\ndef ", 1)[0]
    assert "диапазон_части(" in тело
    assert 'фильтр[">ID"]' in тело and 'фильтр["<=ID"]' in тело
    assert '"filter": фильтр' in тело
    assert "ids[shard::shards]" not in тело, "второе разбиение по остатку выбросит файлы (правило дробления)"


def test_переразбор_просит_свою_долю_у_обхода():
    src = (ROOT / "library/reparse.py").read_text(encoding="utf-8")
    assert "collect_refs(DAYS, SHARD, SHARDS)" in src, (
        "переразбор снова перечисляет весь портал в каждой части")
