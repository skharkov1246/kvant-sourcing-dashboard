"""Облако /brands: только марки, одна марка — одно слово (library/brands.облако).

Жалоба владельца 25.09.2026: в облаке стояли «%% массовых», «г.Москва»,
«Заполняется участникком», «Не применимо», «Türkiye», «турбина», а одна марка
шла тремя словами. Проверяется поведение:
  · не марка по закрытому списку помечается (nb) и в облако не идёт, но из
    списка брендов (поиска) не пропадает — пометка, а не удаление;
  · запись словаря обвиняют только узкие списки (поле формы, незнание);
  · написания одной марки справочника рядов (name, aliases, field_aliases) и
    имя с хвостом «Original», «& New», «International», правовой формой или
    страной сводятся в одно слово с суммой весов; context_aliases не сводят;
  · разные записи справочника не сливаются никогда;
  · у скольких хуже: ни одна марка словаря не осталась без места в облаке,
    ни один ключ не пропал из списка, вес облака равен весу марок.

Корпус придуман (CLAUDE.md, правило 18): марки сочинены.
"""
from __future__ import annotations

from library import brands, codes_sql

СЛОВАРЬ = {"records": [
    {"oem_key": "zorvik", "name": "Zorvik", "spellings": [{"spelling": "Zorvik", "where": "выдумка"}]},
    {"oem_key": "прочие", "name": "Прочие", "spellings": [{"spelling": "Прочие", "where": "выдумка"}]},
    # Запись словаря с именем страны: спор со словарём, страница его не решает.
    {"oem_key": "вьетнам", "name": "Вьетнам", "spellings": [{"spelling": "Вьетнам", "where": "выдумка"}]},
]}
РЯДЫ = {"types": ["насос", "компрессор", "ГПУ"], "brands": [
    {"brand_key": "kalvertamrin", "name": "Kalver (Tamrin)", "aliases": ["Kalver", "Tamrin", "Калвер"],
     "field_aliases": ["KLV"], "context_aliases": ["Kal"]},
    {"brand_key": "brennoag", "name": "Brenno AG", "aliases": ["Brenno AG"], "context_aliases": ["Brenno"]},
    {"brand_key": "brennoenergy", "name": "Brenno Energy", "aliases": ["Brenno Energy"],
     "context_aliases": ["Brenno"]},
    {"brand_key": "orlaflow", "name": "Orla Flow", "aliases": ["Orla Flow", "Orlaflow Minerals"],
     "context_aliases": ["Орла"]},
]}
НЕ_МАРКИ = {
    "%% массовых": "единица или процент",
    "% weight": "единица или процент",
    "г.Выдумград": "страна или город",
    "Türkiye": "страна или город",
    "СОЕДИНЕННОЕ КОРОЛЕВСТВО": "страна или город",
    "Германия": "страна или город",
    "Заполняется участникком": "поле формы",
    "Компонентный состав рабочей среды": "поле формы",
    "при большем количестве компонентов добав": "поле формы",
    "Выбрать менеджера": "поле формы",
    "Не применимо": "пометка незнания или стоп-слово",
    "Не указан": "пометка незнания или стоп-слово",
    "OEM p": "пометка незнания или стоп-слово",
    "турбина": "общее слово оборудования",
    "насос": "общее слово оборудования",
    "по чертежу заказчика": "указание к закупке",
    "фторопласт": "материал",
    "Выдуманная компания делает очень много разных вещей": "описание длиннее шести слов",
}
МАРКИ = ["KLV", "Kalver", "Tamrin", "Kalver Original & New", "Brenno AG", "Brenno Energy AG", "Brenno",
         "Orla Flow", "Orlaflow Minerals", "Orla Flow International", "Orla Flow Germany & Co. KG",
         "Zorvik", "Zorvik Original", "Zorvik, Inc", "Орла", "Насосмаш Выдуманный", "Прочие", "Вьетнам"]


def _корпус():
    карта, _, _ = brands.карта_словаря(СЛОВАРЬ)
    строки = {}
    for i, имя in enumerate(list(НЕ_МАРКИ) + МАРКИ):
        k = codes_sql.ключ_написания(имя)
        k = карта.get(k, k)
        r = строки.setdefault(k, {"brand_key": k, "brand": имя, "codes_any": 0})
        r["codes_any"] += 100 + i
    return {"brands": list(строки.values())}


def _сводка(ряды=РЯДЫ):
    return brands.собрать(_корпус(), {}, словарь=СЛОВАРЬ, ряды=ряды,
                          собран="2026-09-25T00:00:00Z")[brands.КЛЮЧ]


def _бренд(с, имя):
    k = codes_sql.ключ_написания(имя)
    return next(b for b in с["brands"] if b["k"] == k)


def _слово(с, k):
    return next(s for s in с["cloud"] if s["k"] == k or k in (s.get("m") or []))


def test_не_марка_помечена_и_не_в_облаке_но_в_списке():
    с = _сводка()
    в_облаке = {k for s in с["cloud"] for k in (s.get("m") or [])} | {s["k"] for s in с["cloud"]}
    for имя, причина in НЕ_МАРКИ.items():
        b = _бренд(с, имя)
        assert b.get("nb") == причина, (имя, b.get("nb"))
        assert b["k"] not in в_облаке, имя
    assert all(p in brands.ПРИЧИНЫ_НЕ_МАРКИ for p in НЕ_МАРКИ.values())


def test_словарь_обвиняют_только_узкие_списки():
    с = _сводка()
    assert _бренд(с, "Прочие").get("nb") == "пометка незнания или стоп-слово"
    вьетнам = _бренд(с, "Вьетнам")
    assert вьетнам["dict"] and not вьетнам.get("nb")
    assert _слово(с, "вьетнам")["k"] == "вьетнам"


def test_марки_не_обвиняются():
    с = _сводка()
    for имя in МАРКИ:
        if имя == "Прочие":
            continue
        assert not _бренд(с, имя).get("nb"), имя
    assert brands.не_марка("Насосмаш Выдуманный", "насосмашвыдуманный") is None
    assert brands.не_марка("Турбоатом Выдумка", "турбоатомвыдумка") is None


def test_дубли_одной_марки_одним_словом_с_суммой():
    с = _сводка()
    по_ключу = {b["k"]: b for b in с["brands"]}
    kalver = _слово(с, "klv")
    assert set(kalver["m"]) == {codes_sql.ключ_написания(x)
                                for x in ("KLV", "Kalver", "Tamrin", "Kalver Original & New")}
    assert kalver["name"] == "Kalver (Tamrin)"
    # Марка справочника рядов — словарное слово, хотя ни одного ключа словаря брендов у неё нет.
    assert kalver.get("d") == 1 and _слово(с, "zorvik").get("d") == 1
    assert not _слово(с, "насосмашвыдуманный").get("d")
    assert kalver["w"] == sum(по_ключу[k]["codes"]["any"] for k in kalver["m"])
    orla = _слово(с, "orlaflow")
    assert set(orla["m"]) == {"orlaflow", "orlaflowminerals", "orlaflowinternational", "orlaflowgermany"}
    # Слияние по карте словаря: хвост «Original» и правовая форма.
    zorvik = _слово(с, "zorvik")
    assert zorvik["k"] == "zorvik" and set(zorvik["m"]) == {"zorvik", "zorvikoriginal"}
    for k in kalver["m"]:
        if k != kalver["k"]:
            assert по_ключу[k]["cg"] == kalver["k"]


def test_разные_записи_и_контекст_не_сливаются():
    с = _сводка()
    ag, energy = _слово(с, "brenno"), _слово(с, "brennoenergy")
    assert ag is not energy
    # «Brenno» — context_alias двух записей, «Орла» — контекст одной: не сводят.
    орла = _бренд(с, "Орла")
    assert not орла.get("cg") and _слово(с, орла["k"])["k"] == орла["k"]
    имена = [s["name"].casefold() for s in с["cloud"]]
    assert len(имена) == len(set(имена))


def test_без_справочника_рядов_сводит_только_словарь():
    с = _сводка(ряды=None)
    assert not _бренд(с, "KLV").get("cg")
    assert set(_слово(с, "zorvik")["m"]) == {"zorvik", "zorvikoriginal"}


def test_у_скольких_хуже():
    с = _сводка()
    и = brands.итоги_облака(с)
    assert и["dict_outside"] == 0 and и["dict_not_brand"] == 1
    assert и["keys"] == len(_корпус()["brands"])           # из списка не пропал ни один ключ
    в_облаке = {k for s in с["cloud"] for k in (s.get("m") or [])} | {s["k"] for s in с["cloud"]}
    for b in с["brands"]:
        assert bool(b.get("nb")) != (b["k"] in в_облаке), b["k"]
    assert sum(s["w"] for s in с["cloud"]) == sum(brands._вес(b) for b in с["brands"] if not b.get("nb"))
    assert и["not_brand"] == len(НЕ_МАРКИ) + 1
    assert и["top_not_brand_before"] > 0 and и["top_dup_before"] > 0


def test_хвост_имени_закрытым_списком():
    assert brands.без_хвоста("Kalver Original & New") == "Kalver"
    assert brands.без_хвоста("Orla Flow Germany & Co. KG") == "Orla Flow"
    assert brands.без_хвоста("Orla Flow International") == "Orla Flow"
    assert brands.без_хвоста("Kalver Power Systems") == ""
    assert brands.без_хвоста("Original") == ""


def test_написание_двух_записей_справочника_не_сводит():
    ряды = {"brands": [
        {"brand_key": "alfa", "name": "Alfa Kolt", "aliases": ["Alfa Kolt", "Kolt Twin"]},
        {"brand_key": "beta", "name": "Beta Kolt", "aliases": ["Beta Kolt", "Kolt Twin"]}]}
    assert "kolttwin" not in brands.карта_рядов(ряды)
    бренды = [{"k": "kolttwin", "name": "Kolt Twin", "dict": False, "codes": {"any": 5}},
              {"k": "alfakolt", "name": "Alfa Kolt", "dict": False, "codes": {"any": 3}}]
    слова = brands.облако(бренды, {}, {}, ряды)
    assert {s["k"] for s in слова} == {"kolttwin", "alfakolt"}
    assert not бренды[0].get("cg")
