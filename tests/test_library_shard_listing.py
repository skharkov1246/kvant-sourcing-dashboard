"""Часть прогона берёт свою долю сделок, а не весь портал.

Прогон переразбора 21.09.2026 умер во ВСЕХ двенадцати частях с HTTP 429 ещё до
первого файла: каждая часть перечисляла все сделки и все их файловые поля, то
есть портал получил двенадцатикратный обход одним залпом. Деление по остатку
чинит это и обязано оставаться честным разбиением — иначе файлы либо потеряются,
либо разберутся дважды.

Список идентификаторов придуман здесь (правило 18).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def доля(ids, shard, shards):
    """Та же строка деления, что в library/indexer.py, взятая из исходника:
    копия правила в тесте разошлась бы с правилом в коде молча."""
    src = (ROOT / "library/indexer.py").read_text(encoding="utf-8")
    line = next(x.strip() for x in src.splitlines()
                if x.strip().startswith("ids = ids[shard::shards]"))
    ns = {"ids": list(ids), "shard": shard, "shards": shards}
    exec(line, ns)  # noqa: S102
    return ns["ids"]


def test_части_вместе_дают_весь_список_и_не_пересекаются():
    ids = list(range(1, 101))
    части = [доля(ids, s, 12) for s in range(12)]
    собрано = [i for ч in части for i in ч]
    assert sorted(собрано) == ids, "часть списка потерялась или задвоилась"
    assert len(собрано) == len(set(собрано))


def test_одна_часть_берёт_весь_список():
    ids = [5, 6, 7]
    assert доля(ids, 0, 1) == ids


def test_части_примерно_равны():
    """Перекос сделал бы одну часть в разы длиннее остальных, и прогон упёрся бы
    в неё одну."""
    длины = [len(доля(range(1000), s, 12)) for s in range(12)]
    assert max(длины) - min(длины) <= 1


def test_переразбор_просит_свою_долю_у_обхода():
    """Само деление бесполезно, если переразбор его не запрашивает."""
    src = (ROOT / "library/reparse.py").read_text(encoding="utf-8")
    assert "collect_refs(DAYS, SHARD, SHARDS)" in src, (
        "переразбор снова перечисляет весь портал в каждой части")
