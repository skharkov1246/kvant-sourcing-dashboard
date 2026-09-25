"""Хвост-страна в написании изготовителя и заглушки форм: «у скольких хуже».

Общее правило сведения (codes_sql.свести): написание целиком, затем без хвоста,
который ЦЕЛИКОМ страна. Проверки — что правило не теряет и не портит того, что
сводилось до него, и не трогает имён со словом-страной внутри. Корпус придуман
(CLAUDE.md, правило 18), кроме написаний самого словаря dict/oem.json — это
справочник репозитория, а не данные базы.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library import brand_registry, brands, codes_sql, crossref, model_series, oem_kind
from scripts import brand_models

ROOT = Path(__file__).resolve().parents[1]

# Бренды со словом-страной ВНУТРИ имени: разделителя перед страной нет.
СО_СТРАНОЙ_В_ИМЕНИ = ["Russian Electric Motors", "Deutsche Babcock", "China Yuchai",
                      "American Standard", "Japan Steel Works", "Korea Zinc Pumps",
                      "Italia Valvole", "Swiss Turbo Parts"]


@pytest.fixture(scope="module")
def словарь():
    with open(ROOT / "dict" / "oem.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("написание, бренд", [
    ("Sandvik Tamrock, ФИНЛЯНДИЯ", "Sandvik Tamrock"),
    ("Sandvik Tamrock, FINLAND", "Sandvik Tamrock"),
    ("Caterpillar, UNITED STATES OF AMERICA", "Caterpillar"),
    ("Atlas Copco, ШВЕЦИЯ", "Atlas Copco"),
    ("Epiroc (Швеция)", "Epiroc"),
    ("HUANYOU/CHINA", "HUANYOU"),
    ("Siemens - Germany", "Siemens"),
    ("Siemens — Германия", "Siemens"),
    ("Brand, FINLAND", "Brand"),
    ("Brand, USA", "Brand"),
    ("Brand, U.S.A.", "Brand"),
    ("Brand, P.R.C.", "Brand"),
    ("Brand, КНР", "Brand"),
    ("Brand (Made in Germany)", "Brand"),
    ("Brand - Kolben - Germany", "Brand - Kolben"),   # последний разделитель
    ("Rolls-Royce, UK", "Rolls-Royce"),                # дефис без пробелов — не разделитель
])
def test_хвост_страна_снимается(написание, бренд):
    assert codes_sql.без_страны(написание) == бренд


@pytest.mark.parametrize("написание", [
    "Brand, Ltd", "Brand, Inc.", "Brand, GmbH", "Brand, Normet", "Brand (по профилю)",
    "Brand - Kolben", "Brand, FINLAND Oy", "Brand, Finland Service",
    "Россия, Китай",          # голова тоже страна — бренда нет
    "Germany", "SKF", "Brand-USA",
    *СО_СТРАНОЙ_В_ИМЕНИ,
])
def test_хвост_не_целиком_страна_не_трогается(написание):
    assert codes_sql.без_страны(написание) == ""


def test_имя_со_страной_внутри_сводится_к_себе_и_с_хвостом():
    карта = {codes_sql.ключ_написания(н): codes_sql.ключ_написания(н) for н in СО_СТРАНОЙ_В_ИМЕНИ}
    карта["siemens"] = "siemens"
    for н in СО_СТРАНОЙ_В_ИМЕНИ:
        k = codes_sql.ключ_написания(н)
        assert codes_sql.свести(н, карта) == k, н
        assert codes_sql.свести(н + ", Germany", карта) == k, н
        assert codes_sql.свести(н + " (USA)", карта) == k, н
    assert codes_sql.свести("Siemens, Германия", карта) == "siemens"
    assert codes_sql.свести("Brand, Ltd", карта) is None


def test_полное_написание_сильнее_хвоста():
    карта = {"brandfinland": "brand-finland-record", "brand": "brand"}
    assert codes_sql.свести("Brand, Finland", карта) == "brand-finland-record"
    assert codes_sql.ключи_сведения("Brand, Finland") == ["brandfinland", "brand"]
    assert codes_sql.ключи_сведения("Brand") == ["brand"]


def test_словарь_до_и_после_совпадает(словарь):
    """Каждое написание dict/oem.json, сведённое прежним правилом (ключ целиком),
    сводится общим правилом к ТОМУ ЖЕ бренду: и картой /brands, и картой замера."""
    карта, _, _ = brands.карта_словаря(словарь)
    карта_замера = brand_models.свести_дубли(карта, model_series.справочник())
    написания = {r.get("name") for r in словарь["records"]}
    написания |= {s.get("spelling") for r in словарь["records"] for s in r.get("spellings") or []}
    написания.discard(None)
    хуже = []
    for к in (карта, карта_замера):
        for н in написания:
            до = к.get(codes_sql.ключ_написания(н))
            if до and codes_sql.свести(н, к) != до:
                хуже.append(н)
    assert хуже == []
    # Реестр и карточка товара — тем же правилом, без потерь.
    по_множествам = {k: {v} for k, v in карта.items()}
    р = {"карта": карта, "разложение": brands.разложение_словаря(словарь)}
    for н in написания:
        до = карта.get(codes_sql.ключ_написания(н))
        if до:
            assert brand_registry.разрешить(н, по_множествам) == (brand_registry.РАЗРЕШЕНО, {до}), н
            assert crossref.узнать(н, р) == [до], н


def test_ячейка_каталога_без_ключа_страны():
    карта = {"siemens": "siemens", "skf": "skf", "fag": "fag"}
    assert brands.ключи_ячейки("Siemens, Германия", карта) == ["siemens"]
    assert brands.ключи_ячейки("Siemens - Germany", карта) == ["siemens"]
    assert brands.ключи_ячейки("SKF/FAG, Germany", карта) == ["skf", "fag"]
    # Хвост не страна — как было: две части.
    assert brands.ключи_ячейки("SKF, Ltd", карта) == ["skf"]


def test_реестр_разрешает_хвост_после_тире():
    карта = {"siemens": {"siemens"}}
    assert brand_registry.разрешить("Siemens - Germany", карта) == (brand_registry.РАЗРЕШЕНО, {"siemens"})
    assert brand_registry.разрешить("Siemens - Kolben", карта)[0] == brand_registry.ОЧЕРЕДЬ


def test_sew_и_норд_только_в_поле(словарь):
    """«SEW» и «Норд» — бренд в поле изготовителя (словарь и карта замера), а в
    тексте строки сами бренда не называют: там это context_aliases."""
    карта, _, _ = brands.карта_словаря(словарь)
    assert codes_sql.свести("SEW", карта) == "seweurodrive"
    assert codes_sql.свести("Норд", карта) == "nord"
    assert codes_sql.свести("SEW-EURODRIVE", карта) == "seweurodrive"
    спр = model_series.справочник()
    замер = brand_models.свести_дубли(карта, спр)
    assert codes_sql.свести("SEW", замер) == "seweurodrive"
    assert codes_sql.свести("Норд", замер) == "nord"
    assert "seweurodrive" not in model_series.бренды_в_тексте("Прокладка SEW выдуманная", спр)
    assert "nord" not in model_series.бренды_в_тексте("Прокладка Норд выдуманная", спр)


@pytest.mark.parametrize("написание", [
    "Выбрать менеджера", "(Выпадающий список)", "Выпадающий список", "Выберите значение",
    "Банковские реквизиты", "Наименование банка", "БИК:", "БИК 044000000", "ОГРН",
    "р/счет:", "р/с", "к/счет", "Корр. счет", "Покупатель", "Покупатель:",
    "Грузополучатель", "Грузополучатель: выдуманный",
])
def test_поле_формы_ловится(написание):
    assert oem_kind.поле_формы(написание)
    assert brand_models.род_написания(написание, codes_sql.ключ_написания(написание) or "x") == "форма"
    assert oem_kind.вид_записи(написание, codes_sql.ключ_написания(написание), {})["kind"] == oem_kind.ФОРМА


@pytest.mark.parametrize("написание", [
    "Счетмаш", "Бикон", "Покупательский выбор", "Огранка", "Выборг Насос", "Sandvik",
    "Тип насоса / Pump type",
])
def test_поле_формы_не_обвиняет_имена(написание):
    assert oem_kind.поле_формы(написание) is None


def test_словарь_без_полей_формы(словарь):
    """Ни одна запись словаря вида «бренд» не заглушка формы."""
    assert not [r["name"] for r in словарь["records"]
                if oem_kind.бренд_ли(r) and oem_kind.поле_формы(r["name"])]


def test_замер_сводит_хвост_и_копит_несведённое_без_страны():
    итог = brand_models.Итог()
    карта = {"sandviktamrock": "sandviktamrock"}
    for сделка, н in [("1", "Sandvik Tamrock, ФИНЛЯНДИЯ"), ("2", "Sandvik Tamrock, FINLAND"),
                      ("3", "Выдуманный Бренд, CHINA"), ("4", "Выдуманный Бренд/CHINA")]:
        brand_models.учесть(итог, сделка=сделка, изготовитель=н, текст="", карта=карта)
    assert итог.бренд_строк["sandviktamrock"] == 2
    assert dict(итог.несведённые_строк) == {"выдуманныйбренд": 2}
