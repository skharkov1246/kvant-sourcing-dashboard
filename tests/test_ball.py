"""«На ком мяч» (решение владельца 09.10.2026): сделка пресейла — сделка КАМа, но
пока мяч у сорсинга, она не нагрузка КАМа и не его «косяк без суммы».

Стадии — дословные имена воронки «Пресейл» из зонда v48 (09.10.2026).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import people as people_mod  # noqa: E402
import presale  # noqa: E402
from tests import fixture  # noqa: E402

СТАДИИ = {
    "Новый запрос": "head", "Назначение сорсера": "head",
    "Проработка Т3 и вопросы заказчику": "src", "Поиск поставщиков и сбор КП": "src",
    "Анализ КП и фиксация поставщиков": "src", "Сравнение предложений и расчёт ЭП": "src",
    "Доработка по обратной связи": "src",
    "Экономика готова | Можно подавать": "kam", "Техническая часть | Квалификация": "kam",
    "Тендерное предложение выдано | Работаем с клиентом по тендеру": "kam",
    "Переторжка | Торги": "kam", "Сорсинг завершен / Ожидаем решение заказчика": "kam",
    "Согласование финальной спецификации": "kam",
}


def test_все_рабочие_стадии_пресейла_размечены():
    for имя, мяч in СТАДИИ.items():
        assert presale.ball_of(имя) == мяч, имя
    assert presale.ball_of("Сделка выиграна", "S") == "real"
    assert presale.ball_of("Политика", "F") == ""
    assert presale.ball_of("Новая стадия, которой нет в таблице") == "?", "не размеченная — видна, а не на КАМе"


class Портал(fixture.PeopleStub):
    def categories(self):
        return {**fixture.CATS, "32": "Пресейл"}

    def deal_stage_meta(self):
        return {**fixture.STAGE_META,
                "C32:SEARCH": {"name": "Поиск поставщиков и сбор КП", "sem": "P", "sort": 40, "cat": "32"},
                "C32:READY": {"name": "Экономика готова | Можно подавать", "sem": "P", "sort": 70, "cat": "32"}}


ПРЕСЕЙЛ = [
    # мяч у сорсера: суммы ещё нет — это не косяк КАМа и не его нагрузка
    fixture.deal(301, "32", "C32:SEARCH", 0, owner=3, kam=1),
    # мяч у КАМа: экономика готова — сделка в его нагрузке, а сумма обязана быть
    fixture.deal(302, "32", "C32:READY", 0, owner=1, kam=1),
]


def _люди(open_deals):
    return people_mod.compute(Портал(), as_of=fixture.PEOPLE_TODAY, open_deals=open_deals,
                              created=fixture.CREATED, orders=fixture.ORDERS)


def test_мяч_у_сорсинга_не_нагрузка_кама():
    до, после = _люди(fixture.OPEN_DEALS), _люди(fixture.OPEN_DEALS + ПРЕСЕЙЛ)
    кам = lambda P: next(p for p in P["roles"]["kam"]["people"] if p["uid"] == "1")  # noqa: E731
    assert кам(после)["open"] == кам(до)["open"] + 1, "в нагрузку вошла только сделка с мячом у КАМа"
    assert кам(после)["noAmt"] == кам(до)["noAmt"] + 1, "без суммы — только там, где мяч у КАМа"
    assert кам(после)["inSrc"] == 1 and после["roles"]["kam"]["totals"]["inSrc"] == 1
    assert после["recon"]["inSrc"] == 1
    assert [d["id"] for d in после["inSrc"]] == ["301"] and после["inSrc"][0]["state"] == "src"
    assert "301" not in {d["id"] for d in после["deals"]}, "в общий счёт и гигиену не входит"
    assert после["presaleCat"] == "Пресейл"


def test_без_воронки_пресейла_ничего_не_меняется():
    P = fixture.build_people()
    assert P["inSrc"] == [] and P["recon"]["inSrc"] == 0 and P["presaleCat"] == ""
