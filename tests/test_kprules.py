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


ПРАВИЛА = "2026-10-19"


def _раздел(карточка, день=ДЕНЬ):
    return kprules.split_flags(карточка, день, 14, ПРАВИЛА)


def test_дата_последнего_движения_карточки():
    до = к("DT166_24:UC_H49RUE", False, "2026-10-10T10:00:00+03:00")
    assert kprules.before_rules(до, ПРАВИЛА) is True
    assert kprules.before_rules(к("DT166_24:UC_H49RUE", False, "2026-10-19T09:00:00+03:00"), ПРАВИЛА) is False, (
        "в день правил — уже по правилам")
    assert kprules.before_rules({"stageId": "DT166_24:1"}, ПРАВИЛА) is False, "без даты — как прежде, по правилам"
    assert kprules.before_rules({"createdTime": "2026-09-01T10:00:00+03:00"}, dt.date(2026, 10, 19)) is True
    assert kprules.before_rules(до, None) is False


def test_до_правил_только_застывшее():
    """«До правил» — только то, что застыло до 19.10 и не могло случиться заново под
    правилами; признак, который копится и после 19.10, — нарушение."""
    # стадия КП без файла: закрытая до правил — наследие, открытая (Price at Work) — нарушение
    assert _раздел(к("DT166_24:SUCCESS", False, "2026-10-10T10:00:00+03:00")) == ([], ["noFile"])
    assert _раздел(к("DT166_24:UC_H49RUE", False, "2026-10-10T10:00:00+03:00")) == (["noFile"], []), (
        "на Price at Work файл можно приложить и сейчас")
    # «КП получено» — по дате перевода, как в зонде v52
    assert _раздел(к("DT166_24:1", True, "2026-10-10T10:00:00+03:00"), dt.date(2026, 10, 23))[1] == ["legacy"]
    assert _раздел(к("DT166_24:1", True, "2026-10-20T10:00:00+03:00"), dt.date(2026, 10, 23))[0] == ["legacy"]


def test_price_at_work_без_итога_часы_идут_и_под_правилами():
    """Часы считаются от max(перевод на Price at Work, 19.10): карточка с 15.10
    становится нарушением, когда и под правилами прошло больше 14 дней."""
    с_15_10 = к("DT166_24:UC_H49RUE", True, "2026-10-15T10:00:00+03:00")
    assert _раздел(с_15_10, dt.date(2026, 11, 20)) == (["pawStale"], []), "36 дней, из них 32 под правилами"
    с_01_10 = к("DT166_24:UC_H49RUE", True, "2026-10-01T10:00:00+03:00")
    assert _раздел(с_01_10, dt.date(2026, 10, 23)) == ([], ["pawStale"]), "под правилами только 4 дня"
    assert _раздел(с_01_10, dt.date(2026, 11, 3)) == (["pawStale"], []), "с 02.11 — нарушение"


def test_файл_без_перевода_стадии_льготный_срок():
    """Открытая карточка с файлом: перевести стадию сорсер может сейчас. Когда
    приложен файл, по выдаче не узнать, поэтому до конца льготного срока (19.10 +
    14 дн.) — «до правил», после — нарушение. updatedTime не решает: любую правку,
    хоть переназначение, он принял бы за файл."""
    отправлен = к("DT166_24:PREPARATION", True, "2026-10-12T10:00:00+03:00")
    правка = dict(отправлен, updatedTime="2026-10-21T10:00:00+03:00")
    assert _раздел(правка, dt.date(2026, 10, 23)) == ([], ["fileNoStage"])
    assert _раздел(отправлен, dt.date(2026, 11, 2)) == ([], ["fileNoStage"]), "02.11 — последний день срока"
    assert _раздел(dict(отправлен, updatedTime="2026-10-15T10:00:00+03:00"), dt.date(2026, 11, 3)) == (
        ["fileNoStage"], []), "с 03.11 — нарушение, даже если карточку не трогали"
    assert kprules.grace_until(ПРАВИЛА, 14) == dt.date(2026, 11, 2)


def test_файл_при_отказе_по_дате_и_автору_правки():
    """Закрытая карточка с файлом при отказе — наследие, если с 19.10 её не меняли
    или последним менял не её сорсер: переназначение не вменяет чужое наследие."""
    отказ = dict(к("DT166_24:2", True, "2026-10-05T10:00:00+03:00"), _owner="76")
    день = dt.date(2026, 10, 23)
    assert _раздел(dict(отказ, updatedTime="2026-10-06T10:00:00+03:00", updatedBy="76"), день) == ([], ["fileClosed"])
    assert _раздел(dict(отказ, updatedTime="2026-10-21T10:00:00+03:00", updatedBy="90"), день) == (
        [], ["fileClosed"]), "правил другой (переназначил руководитель) — наследие"
    assert _раздел(dict(отказ, updatedTime="2026-10-21T10:00:00+03:00", updatedBy="76"), день) == (
        ["fileClosed"], []), "сам сорсер правил карточку после 19.10 и стадию не поправил — нарушение"
    assert _раздел(отказ, день) == (["fileClosed"], []), "ни даты, ни автора правки — нарушение"


def test_счёт_нарушений_и_наследия():
    карточки = [к("DT166_24:SUCCESS", False, "2026-10-10T10:00:00+03:00"),       # наследие
                к("DT166_24:UC_H49RUE", False, "2026-10-10T10:00:00+03:00"),     # нарушение
                {"stageId": "DT166_24:1", "_hasQuote": True},                     # без даты — нарушение
                # нарушение (Price at Work без файла) и льготный простой разом
                к("DT166_24:UC_H49RUE", False, "2026-09-20T10:00:00+03:00")]
    c, n = kprules.count_flags(карточки, dt.date(2026, 10, 23), 14, ПРАВИЛА)
    assert (dict(c), n) == ({"noFile": 2, "legacy": 1}, 3)
    c, n = kprules.count_before(карточки, dt.date(2026, 10, 23), 14, ПРАВИЛА)
    assert dict(c) == {"noFile": 1, "pawStale": 1}, "по кодам — все признаки «до правил»"
    assert n == 1, "карточка с нарушением в счёт «до правил» не входит — иначе стояла бы в обоих"
    c, n = kprules.count_flags(карточки, ДЕНЬ, 14)
    assert n == 4, "без даты правил — все карточки, как было"


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
