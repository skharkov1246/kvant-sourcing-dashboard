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
    assert снимок["totals"] == {"entities": 3, "numbered": 2, "review_open": 7,
                                "with_inn": 1, "with_rfq": 0, "rfq_measurable": 0}
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


# ── ОТЗЫВЧИВОСТЬ В СНИМКЕ ────────────────────────────────────────────────────


def test_отзывчивость_попадает_в_снимок_числителем_и_знаменателем():
    """Доля не передаётся: «50 %» из двух и из сорока — разные утверждения."""
    стат = {"sent": 12, "answered": 6, "quoted": 2, "silent": 3, "no_outcome": 3, "cards": 15}
    снимок = собрать(КОРПУС, ПРИЗНАКИ, 0, [("KV-S-000001-8", стат)])
    первая = снимок["entities"][0]
    assert первая["rfq"] == стат
    assert not any(isinstance(v, float) for v in первая["rfq"].values())
    assert снимок["totals"]["with_rfq"] == 1
    assert снимок["totals"]["rfq_measurable"] == 1


def test_меньше_трёх_запросов_историю_даёт_но_измеримой_не_считается():
    """У 72,5 % компаний один запрос: там доля — один случай, а не свойство."""
    стат = {"sent": 2, "answered": 1, "quoted": 0, "silent": 1, "no_outcome": 0, "cards": 2}
    снимок = собрать(КОРПУС, ПРИЗНАКИ, 0, [("KV-S-000001-8", стат)])
    assert снимок["totals"]["with_rfq"] == 1
    assert снимок["totals"]["rfq_measurable"] == 0


def test_пустая_история_в_снимок_не_кладётся():
    """Ноль отправленных — это «не писали», а не «не отвечает»."""
    стат = {"sent": 0, "answered": 0, "quoted": 0, "silent": 0, "no_outcome": 0, "cards": 1}
    снимок = собрать(КОРПУС, ПРИЗНАКИ, 0, [("KV-S-000001-8", стат)])
    assert "rfq" not in снимок["entities"][0]
    assert снимок["totals"]["with_rfq"] == 0


# ── привязка KV ──────────────────────────────────────────────────────────────
def test_привязка_kv_ищется_как_у_воркера():
    """Публикатор обязан искать привязку в том же порядке, что воркер портала.

    Прогон 21.09.2026 08:53 собрал снимок целиком и упал на KV_BINDING_MISSING:
    искал только ACL, а у проекта заведена VISITS. Ошибка в другую сторону тише
    и хуже — снимок лёг бы туда, откуда страница не читает.
    """
    import pathlib
    import re

    from scripts import publish_suppliers as ps

    корень = pathlib.Path(__file__).resolve().parents[1]
    js = (корень / "public" / "_worker.js").read_text(encoding="utf-8")
    # Читаем код, а не комментарии: пояснение выше по файлу называет обе привязки.
    без_комментариев = re.sub(r"(?m)^\s*//.*$", "", js)
    m = re.search(r"function aclStore\(env\)\s*\{[^}]*?env\.(\w+)\s*\|\|\s*env\.(\w+)",
                  без_комментариев)
    assert m, "в воркере не нашлась aclStore с порядком привязок"
    assert ps.ПРИВЯЗКИ == (m.group(1), m.group(2))


def test_привязка_берётся_первая_найденная():
    from scripts import publish_suppliers as ps

    class Заглушка(ps.Cloudflare):
        def __init__(self, namespaces):
            self._ns = namespaces

        def envelope(self, method, path, body=None):
            return {"result": {"deployment_configs": {"production":
                    {"kv_namespaces": self._ns}}}}

    acl = {"ACL": {"namespace_id": "a" * 32}, "VISITS": {"namespace_id": "b" * 32}}
    assert Заглушка(acl).namespace() == "a" * 32
    только_visits = {"VISITS": {"namespace_id": "b" * 32}}
    assert Заглушка(только_visits).namespace() == "b" * 32


def test_без_обеих_привязок_отказ():
    import pytest

    from scripts import publish_suppliers as ps

    class Заглушка(ps.Cloudflare):
        def __init__(self):
            pass

        def envelope(self, method, path, body=None):
            return {"result": {"deployment_configs": {"production":
                    {"kv_namespaces": {"OTHER": {"namespace_id": "c" * 32}}}}}}

    with pytest.raises(ps.PublishError) as e:
        Заглушка().namespace()
    assert "KV_BINDING_MISSING" in str(e.value)
