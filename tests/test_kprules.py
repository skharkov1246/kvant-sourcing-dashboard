"""Правила отметки КП (владелец, 19.10.2026): корзины стадий СП-166 и нарушения.

Коды и имена стадий — зонд v52 (19.10.2026); карточки придуманы (правило 18).
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import kprules  # noqa: E402
import stages  # noqa: E402

СТАДИИ = {  # код → корзина по правилам
    "DT166_24:NEW": "new", "DT166_24:PREPARATION": "sent", "DT166_24:UC_61BSRU": "dialog",
    "DT166_24:UC_GFJ5A8": "dialog", "DT166_24:UC_H49RUE": "paw", "DT166_24:SUCCESS": "selected",
    "DT166_24:FAIL": "notsel", "DT166_24:1": "paw", "DT166_24:2": "refused", "DT166_24:3": "no_answer",
    "DT166_24:4": "notsel", "DT166_24:5": "notsel",
}


def test_корзины_стадий_по_правилам():
    for sid, b in СТАДИИ.items():
        assert stages.classify_stage(sid) == b, sid
    assert stages.classify_stage("DT166_24:9") == "other", "новая стадия видна, а не прячется в КП"
    assert "paw" in stages.OPEN and "notsel" in stages.CLOSED
    assert set(stages.BUCKETS) == set(stages.BUCKET_LABELS)


ДЕНЬ = dt.date(2026, 10, 19)


def к(stage, файл=False, moved="2026-10-18T10:00:00+03:00"):
    return {"stageId": stage, "_hasQuote": файл, "movedTime": moved}


def test_нарушения_карточки():
    assert kprules.card_flags(к("DT166_24:UC_H49RUE", True), ДЕНЬ, 14) == []
    assert kprules.card_flags(к("DT166_24:UC_H49RUE", False), ДЕНЬ, 14) == ["noFile"]
    assert kprules.card_flags(к("DT166_24:FAIL", False), ДЕНЬ, 14) == ["noFile"], "Not Selected — это КП"
    assert kprules.card_flags(к("DT166_24:UC_GFJ5A8", True), ДЕНЬ, 14) == ["fileNoStage"]
    assert kprules.card_flags(к("DT166_24:2", True), ДЕНЬ, 14) == ["fileClosed"]
    assert kprules.card_flags(к("DT166_24:UC_H49RUE", True, "2026-09-01T10:00:00+03:00"), ДЕНЬ, 14) == ["pawStale"]
    assert kprules.card_flags(к("DT166_24:1", True), ДЕНЬ, 14) == ["legacy"]
    assert kprules.card_flags(к("DT166_24:PREPARATION"), ДЕНЬ, 14) == []
    c, n = kprules.count_flags([к("DT166_24:UC_H49RUE"), к("DT166_24:SUCCESS", True), к("DT166_24:1")], ДЕНЬ, 14)
    assert (c["noFile"], c["legacy"], n) == (2, 1, 2)


def test_экономика_готова_по_правилу():
    сделки = [{"ID": 1, "TITLE": "А"}, {"ID": 2, "TITLE": "Б"}, {"ID": 3, "TITLE": "В"}, {"ID": 4, "TITLE": "Г"}]
    запросы = [
        {"parentId2": 1, "stageId": "DT166_24:SUCCESS"}, {"parentId2": 1, "stageId": "DT166_24:FAIL"},
        {"parentId2": 2, "stageId": "DT166_24:SUCCESS"}, {"parentId2": 2, "stageId": "DT166_24:UC_H49RUE"},
        {"parentId2": 3, "stageId": "DT166_24:FAIL"},
    ]
    р = kprules.econ_check(сделки, запросы)
    assert (р["yes"], р["ok"], р["bad"]) == (4, 1, 3)
    why = {r["id"]: r["why"] for r in р["rows"]}
    assert why["2"] == ["на Price at Work: 1"]
    assert why["3"] == ["ни одного Selected"]
    assert why["4"] == ["запросов поставщикам нет"]
