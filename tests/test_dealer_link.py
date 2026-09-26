"""Дилеры разведки брендов → реестр компаний (library/dealer_link.py), без базы.

Что держится:
  · сводят только сильные ключи: домен записи дилера (поле domain) и ИНН из
    текста записи с верной контрольной суммой; общий домен не сводит;
  · имя — только кандидат, пояснение в скобках ключу имени не мешает;
  · связь ведёт в корень цепочки слияний, правило одно с supplier_link;
  · вид записи (своя площадка, официальный дилер, дилер) — offer_role.вид_дилера;
  · роль предложения читает те же ключи, что замер (Реестр.дилеры_ключ);
  · печать — только агрегаты.

Корпус придуман (CLAUDE.md, правило 18): домены — в зонах .example и .test,
ИНН — синтетические с верной контрольной суммой.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import dealer_link as dl  # noqa: E402
from library import offer_role as orl  # noqa: E402
from library import supplier_link as sl  # noqa: E402

ИНН_А = "0000000018"
ИНН_Б = "1111111117"

E = [("KV-S-000001-8", None, None, "Альфа Насосы"),
     ("KV-S-000002-6", None, "Нигдения", "Бета Уплотнения"),
     ("KV-S-000003-4", None, None, "Выдумка Нигдения"),
     ("KV-S-000004-2", "KV-S-000005-9", None, "deltaold"),    # слита в 5
     ("KV-S-000005-9", None, None, "Дельта Групп"),
     ("KV-S-000006-7", None, None, "ООО Ромашка")]
I = [("KV-S-000001-8", "domain", "alpha-pumps.example"),
     ("KV-S-000001-8", "inn", ИНН_А),
     ("KV-S-000002-6", "alias", "бетауплотнения"),
     ("KV-S-000003-4", "domain", "vydumka-nig.example"),
     ("KV-S-000004-2", "domain", "delta.example"),
     ("KV-S-000006-7", "inn", ИНН_Б),
     ("KV-S-000006-7", "legal", "ооо")]

РАЗВЕДКА = [{"oem_key": "vydumka", "dealers": [
    {"company": "Альфа Насосы (Нигдения)", "country": "Нигдения", "role": "официальный дилер",
     "domain": "alpha-pumps.example", "domain_source": "https://shop.alpha-pumps.example/"},
    {"company": "ООО «Ромашка» (ИНН 1111111117)", "country": "Россия", "role": "независимый продавец"},
    {"company": "Бета Уплотнения (дистрибьютор)", "country": "Нигдения", "role": "официальный дистрибьютор"},
    {"company": "Gamma Trading", "country": "Нигдения", "role": "дилер", "domain": "gmail.com"},
    {"company": "Дельта", "country": "Нигдения", "role": "местный дилер (по указателю изготовителя)",
     "domain": "delta.example"},
    {"company": "Эпсилон", "country": "Нигдения", "role": "официальный дилер", "domain": "eps.example"},
    {"company": "Выдумка Нигдения", "country": "Нигдения", "role": "дочерняя компания изготовителя",
     "domain": "vydumka-nig.example"},
    {"company": "Зета (ИНН 1111111118)", "country": "Нигдения", "role": "дилер"},     # сумма неверна
]}, {"oem_key": "drugaya", "dealers": [
    {"company": "Альфа Насосы", "country": "Нигдения", "role": "дистрибьютор",
     "domain": "alpha-pumps.example"},
]}, {"name": "без ключа бренда — пропускается", "dealers": [{"company": "Ноль", "role": "дилер"}]}]


def _счёт():
    список = dl.дилеры(РАЗВЕДКА)
    итог = dl.сопоставить(список, sl.собрать_реестр(E, I))
    return список, итог, dl.счёт(список, итог)


def test_ключи_дилера():
    assert dl.ключи_дилера({"domain": "alpha-pumps.example"}) == (frozenset({"alpha-pumps.example"}), frozenset())
    assert dl.ключи_дилера({"domain": "https://www.Alpha-Pumps.example/ru"})[0] == {"alpha-pumps.example"}
    assert dl.ключи_дилера({"domain": "gmail.com"}) == (frozenset(), frozenset())
    assert dl.ключи_дилера({"domain": "2.5"}) == (frozenset(), frozenset())
    assert dl.ключи_дилера({"company": "Ромашка (ИНН 1111111117)"})[1] == {ИНН_Б}
    assert dl.ключи_дилера({"role": "по реестру ИНН 0000000018"})[1] == {ИНН_А}
    assert dl.ключи_дилера({"company": "Зета (ИНН 1111111118)"})[1] == frozenset()   # сумма
    assert dl.ключи_дилера({"inn": ИНН_Б})[1] == {ИНН_Б}
    # Адрес в sources доменом не считается — только поле domain.
    assert dl.ключи_дилера({"sources": ["https://alpha-pumps.example/"]}) == (frozenset(), frozenset())


def test_без_пояснений():
    assert dl.без_пояснений("Atlas (дочернее общество, Дубай) Group") == "Atlas Group"
    assert dl.без_пояснений("Бета") == "Бета" and dl.без_пояснений(None) == ""


def test_сведение_только_сильными_ключами():
    список, итог, с = _счёт()
    по = {(x.oem_key, x.номер): x.разведка.id for x in список}
    assert итог.связь[по[("vydumka", 0)]] == "KV-S-000001-8"                 # домен
    assert итог.связь[по[("vydumka", 1)]] == "KV-S-000006-7"                 # ИНН из текста
    assert итог.связь[по[("vydumka", 4)]] == "KV-S-000005-9"                 # корень слияния
    assert итог.связь[по[("vydumka", 6)]] == "KV-S-000003-4"
    assert итог.связь[по[("drugaya", 0)]] == "KV-S-000001-8"
    # Имя — только кандидат, и пояснение в скобках ключу не мешает.
    assert по[("vydumka", 2)] not in итог.связь
    assert итог.кандидаты[по[("vydumka", 2)]] == {"KV-S-000002-6": "имя+страна"}
    # Общий домен не сводит; ключа нет — нет и связи; неверный ИНН — не ключ.
    for n in (3, 5, 7):
        assert по[("vydumka", n)] not in итог.связь, n
    assert len(список) == 9


def test_правило_одно_с_supplier_link():
    список, итог, _ = _счёт()
    прямо = sl.сопоставить([x.разведка for x in список], sl.собрать_реестр(E, I))
    assert прямо.связь == итог.связь and прямо.кандидаты == итог.кандидаты
    assert прямо.строки == итог.строки


def test_счёт():
    _, _, с = _счёт()
    assert с["записей"] == 9 and с["брендов"] == 2
    assert с["по_виду"] == {orl.ОФИЦИАЛЬНЫЙ: 7, orl.ДИЛЕР: 1, orl.СВОЯ: 1}
    assert с["с_доменом"] == 5 and с["домен_отсеян"] == 1
    assert с["с_инн"] == 1 and с["с_ключом"] == 6
    assert с["сведено"] == 5
    assert с["сведено_по"] == {"домен сайта": 4, "инн": 1}
    assert с["сведено_по_виду"] == {orl.ОФИЦИАЛЬНЫЙ: 3, orl.ДИЛЕР: 1, orl.СВОЯ: 1}
    assert с["сущностей"] == 4
    assert с["брендов_с_офиц"] == 2
    assert с["с_ключом_не_сведено"] == 1                                        # Эпсилон
    assert с["кандидатов"] == 1 and с["кандидатов_одн"] == 1 and с["кандидатов_стр"] == 1
    assert с["кандидатов_по_виду"] == {orl.ОФИЦИАЛЬНЫЙ: 1}


def test_роль_предложения_видит_те_же_ключи():
    """Реестр роли предложения собирает ключи записей тем же dl.ключи_дилера."""
    р = orl.собрать({"records": []}, {}, РАЗВЕДКА)
    ожидается = {}
    for d in РАЗВЕДКА:
        if not d.get("oem_key"):
            continue
        for x in d["dealers"]:
            домены, инн = dl.ключи_дилера(x)
            for ключ in [("домен", h) for h in домены] + [("инн", n) for n in инн]:
                ожидается.setdefault(ключ, set()).add((d["oem_key"], orl.вид_дилера(x["role"])))
    assert dict(р.дилеры_ключ) == ожидается
    assert р.дилеры_ключ[("домен", "alpha-pumps.example")] == {("vydumka", orl.ОФИЦИАЛЬНЫЙ),
                                                               ("drugaya", orl.ОФИЦИАЛЬНЫЙ)}


def test_печать_только_агрегаты(capsys):
    _, итог, с = _счёт()
    dl.печать(с, len(E), sl.собрать_реестр(E, I))
    out = capsys.readouterr().out
    assert "ВСЕГО записей дилеров сведено:         5 из 9" in out
    for значение in ("Альфа", "Ромашка", "Бета", "alpha-pumps", "delta.example", ИНН_Б, "KV-S-"):
        assert значение not in out, значение


def test_без_базы_код_2(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert dl.main() == 2


def test_набор_репозитория_читается():
    """Настоящие файлы: записи читаются, у каждой записи с полем domain ключ есть."""
    разведка = dl.читать_файлы()
    список = dl.дилеры(разведка)
    с_полем = sum(1 for d in разведка for x in d.get("dealers") or [] if x.get("domain"))
    assert список and с_полем == sum(1 for x in список if x.разведка.сайты)
