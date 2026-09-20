"""Запись сведения в базу: номер, выданный однажды, не выдаётся второй раз.

Это свойство нельзя проверить без настоящей базы: оно про уникальные индексы,
транзакцию и повторный прогон по уже заполненной таблице. Поэтому тест ищет
PostgreSQL и, не найдя, ЧЕСТНО пропускается — гейт Postgres не поднимает.

Пропуск не оставляет дыры: саму логику опознания, в которой и была ошибка,
закрывают тесты без базы в test_supplier_master_merge.py. Здесь проверяется
связка с базой целиком.

Адрес базы — в SUPPLIER_TEST_DB_URL. Корпус выдуман (CLAUDE.md, правило 18).

    createdb suptest
    psql -d suptest -f library/supabase/suppliers_schema.sql
    SUPPLIER_TEST_DB_URL=postgresql://... python -m pytest tests/test_supplier_write.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for путь in ("scripts", "pnw/tools", "library"):
    sys.path.insert(0, str(ROOT / путь))

SPEC = importlib.util.spec_from_file_location(
    "load_supplier_master", ROOT / "library" / "load_supplier_master.py")
lm = importlib.util.module_from_spec(SPEC)
sys.modules["load_supplier_master"] = lm
SPEC.loader.exec_module(lm)

URL = os.environ.get("SUPPLIER_TEST_DB_URL", "")


def _база():
    if not URL:
        pytest.skip("нет SUPPLIER_TEST_DB_URL — проверка с базой пропущена")
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 не установлен")
    try:
        conn = psycopg2.connect(URL, connect_timeout=5)
    except Exception as e:                                        # noqa: BLE001
        pytest.skip(f"база недоступна: {e}")
    return conn


def _чисто(conn):
    """Пустые таблицы перед каждой проверкой: номера считаются от максимума."""
    with conn, conn.cursor() as cur:
        cur.execute("truncate sup_review, sup_identifier, sup_number_registry, "
                    "sup_entity restart identity cascade")


def _строка(реестр, имя, домен="", ключ="", формы=()):
    return lm.Источник(реестр, имя, домен, ключ, frozenset(формы))


def _свод(*строки):
    наборы = {}
    for r in строки:
        наборы.setdefault(r.реестр, []).append(r)
    return lm.свести(наборы)


def _спросить(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


КОРПУС = (
    ("bitrix/companies", "morskoyuzel", "seaknot.example.com", "bitrix:5", ()),
    ("pnw", "morskoyuzelltd", "seaknot.example.com", "", ()),
    ("bitrix/companies", "tihayagavan", "", "bitrix:9", ()),
    ("а", "romashka", "", "", ("ооо",)),
    ("б", "romashka", "", "", ("ао",)),
)


def test_повторный_прогон_не_выдаёт_новых_номеров():
    """Главное свойство: номер уходит в договоры и раздваиваться не может.

    Второй прогон идёт с ДОПОЛНИВШИМСЯ признаком — у компании появился сайт.
    Ровно этот случай ломал первую версию реестра, где ключ строки складывался
    из признака: новый признак давал новый ключ и новый номер.
    """
    conn = _база()
    _чисто(conn)
    сущности, очередь = _свод(*(_строка(*c) for c in КОРПУС))
    assert lm.записать(URL, сущности, очередь, "test-1") == 0

    номера_до = _спросить(conn, "select sup_id, seq from sup_number_registry order by seq")
    assert len(номера_до) == len(сущности), (
        f"сущностей {len(сущности)}, номеров {len(номера_до)} — юрлица склеились")

    # тот же корпус, но у «тихой гавани» появился сайт
    второй = [c if c[1] != "tihayagavan" else
              ("bitrix/companies", "tihayagavan", "tihaya.example.com", "bitrix:9", ())
              for c in КОРПУС]
    с2, о2 = _свод(*(_строка(*c) for c in второй))
    assert lm.записать(URL, с2, о2, "test-2") == 0

    номера_после = _спросить(conn, "select sup_id, seq from sup_number_registry order by seq")
    assert номера_после == номера_до, "второй прогон переиздал номера"

    новые = _спросить(conn, "select kind, value from sup_identifier where run_id='test-2'")
    assert новые == [("domain", "tihaya.example.com")], (
        f"второй прогон обязан добавить ровно появившийся признак, добавил {новые}")
    conn.close()


def test_у_каждой_строки_есть_ключ_прогона():
    """Без run_id откат невозможен (CLAUDE.md, правило 6)."""
    conn = _база()
    _чисто(conn)
    сущности, очередь = _свод(*(_строка(*c) for c in КОРПУС))
    assert lm.записать(URL, сущности, очередь, "test-3") == 0
    for таблица in ("sup_identifier", "sup_number_registry", "sup_review"):
        пусто = _спросить(conn, f"select count(*) from {таблица} "
                                f"where run_id is null or run_id = ''")[0][0]
        assert пусто == 0, f"{таблица}: {пусто} строк без ключа прогона"
    conn.close()


def test_разные_юрлица_получают_разные_номера():
    """«ООО Ромашка» и «АО Ромашка» — два номера, а не один на двоих."""
    conn = _база()
    _чисто(conn)
    сущности, _ = _свод(_строка("а", "romashka", "", "", ("ооо",)),
                        _строка("б", "romashka", "", "", ("ао",)))
    assert len(сущности) == 2
    assert lm.записать(URL, сущности, [], "test-4") == 0
    assert _спросить(conn, "select count(*) from sup_number_registry")[0][0] == 2
    conn.close()


def test_неудача_откатывает_весь_прогон():
    """Транзакция целиком: половина записи хуже, чем её отсутствие."""
    conn = _база()
    _чисто(conn)
    сущности, _ = _свод(_строка("а", "chestnaya", "chest.example.com"))
    # run_id длиннее любого разумного — упрётся не в длину, а в заведомо битый вид
    плохая = [{"kind": "takogo_vida_net", "reason": "проверка отката"}]
    assert lm.записать(URL, сущности, плохая, "test-5") == 4, "ожидался код отката"
    assert _спросить(conn, "select count(*) from sup_entity")[0][0] == 0, (
        "сущности остались в базе, хотя очередь не записалась — отката не было")
    conn.close()


def test_схема_не_требует_ролей_supabase():
    """Файл схемы обязан применяться на ЧИСТОМ PostgreSQL, без ролей платформы.

    anon, authenticated и service_role заводит Supabase. На раннере их нет, и
    «revoke … from anon» роняет весь файл с «role "anon" does not exist». Прогон
    20.09.2026 20:40 так и упал: локально я роли создал руками и потому ошибки не
    увидел. Проверка статическая — читает сам файл: поднять базу без ролей в
    гейте нельзя, а условие видно и так.
    """
    ddl = (ROOT / "library" / "supabase" / "suppliers_schema.sql").read_text(encoding="utf-8")
    assert "sup_роли_которые_есть" in ddl, (
        "схема снимает права у ролей, не проверив их наличие — упадёт на чистом PostgreSQL")
    # Безусловных упоминаний ролей в самих revoke/grant остаться не должно.
    #
    # Ищем именно ОПЕРАТОР, а не подстроку: первая версия проверки искала «grant»
    # и ловила слово grantee в проверочном select — то есть ругалась на строку,
    # которая ничего не выдаёт и никого не лишает прав.
    оператор = re.compile(r"\b(?:revoke|grant)\s+(?:all|select|insert|update|delete|usage)\b",
                          re.I)
    for строка in ddl.splitlines():
        голая = строка.split("--")[0]
        if not оператор.search(голая):
            continue
        for роль in ("anon", "authenticated", "service_role"):
            assert роль not in голая, (
                f"роль {роль} названа в операторе напрямую, а не через проверку наличия: "
                f"{строка.strip()}")
