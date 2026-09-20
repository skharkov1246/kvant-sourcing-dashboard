"""Сборка снимка поставщиков. Корпус придуман, не скопирован из базы (правило 18)."""
import importlib.util
import pathlib

СКРИПТ = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "publish_suppliers.py"
_spec = importlib.util.spec_from_file_location("publish_suppliers", СКРИПТ)
publish_suppliers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(publish_suppliers)
собрать = publish_suppliers.собрать


def сущность(sid, имя, причина="domain", номер_выдан=True, страна="RU"):
    # Порядок колонок — как в СУЩНОСТИ_SQL: id, имя, страна, note, status,
    # resolution, есть ли номер.
    return (sid, имя, страна, причина, "active", "resolved", номер_выдан)


КОРПУС = [
    сущность("KV-S-000001-8", "Учебный завод"),
    сущность("KV-S-000002-6", "Учебная мастерская", причина="name"),
    сущность("KV-S-000003-4", "Учебное бюро", номер_выдан=False),
]
ПРИЗНАКИ = [
    ("KV-S-000001-8", "domain", "example.test", "synthetic-a"),
    ("KV-S-000001-8", "inn", "0000000000", "synthetic-a"),
    ("KV-S-000001-8", "bitrix", "1", "synthetic-portal"),
    ("KV-S-000002-6", "domain", "first.invalid", "synthetic-b"),
    ("KV-S-000002-6", "domain", "second.invalid", "synthetic-b"),
]


def test_счётчики_считаются_а_не_пишутся_руками():
    снимок = собрать(КОРПУС, ПРИЗНАКИ, 7)
    assert снимок["totals"] == {"entities": 3, "numbered": 2, "review_open": 7, "with_inn": 1}
    assert снимок["version"] == 1
    assert снимок["published_at"].endswith("Z")


def test_сущность_без_номера_отдаётся_без_номера():
    снимок = собрать(КОРПУС, ПРИЗНАКИ, 0)
    без_номера = [e for e in снимок["entities"] if e["name"] == "Учебное бюро"]
    assert len(без_номера) == 1
    assert без_номера[0]["number"] is None


def test_источники_собираются_без_повторов_и_упорядочены():
    снимок = собрать(КОРПУС, ПРИЗНАКИ, 0)
    первая = снимок["entities"][0]
    assert первая["sources"] == ["synthetic-a", "synthetic-portal"]
    assert первая["merged_by"] == "domain"


def test_два_домена_у_одной_записи_дают_оговорку():
    # Два домена — признак склеенных компаний, а не одной с двумя сайтами.
    # Оговорка обязана считаться из данных: иначе она устареет молча.
    снимок = собрать(КОРПУС, ПРИЗНАКИ, 0)
    assert "caveat" in снимок
    assert снимок["caveat"].startswith("1 запис")
    вторая = [e for e in снимок["entities"] if e["number"] == "KV-S-000002-6"][0]
    assert вторая["domain"] == "first.invalid, second.invalid"


def test_без_склеек_оговорки_нет():
    снимок = собрать(КОРПУС[:1], ПРИЗНАКИ[:3], 0)
    assert "caveat" not in снимок


def test_несколько_инн_показываются_оба_а_не_первый():
    # Два ИНН у одной сущности — противоречие. Показать первый значит спрятать его.
    признаки = ПРИЗНАКИ + [("KV-S-000001-8", "inn", "1111111111", "synthetic-c")]
    снимок = собрать(КОРПУС, признаки, 0)
    первая = снимок["entities"][0]
    assert первая["inn"] == "0000000000, 1111111111"


def test_пустой_реестр_даёт_пустой_снимок_а_не_падение():
    снимок = собрать([], [], 0)
    assert снимок["entities"] == []
    assert снимок["totals"]["entities"] == 0
    assert "caveat" not in снимок
