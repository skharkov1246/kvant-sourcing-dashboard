"""Заведение дилеров разведки брендов в реестр — на настоящей схеме PostgreSQL.

Схема — те же файлы library/supabase/*.sql, что в рабочей базе
(suppliers_schema.sql и research_dealers_schema.sql); реестр и разведка
выдуманы (правило 18). Держится:
  · вхолостую ничего не пишет (соединение только для чтения), в журнале —
    только агрегаты;
  · запись заводит сущность с вечным номером после наибольшего, признаки с
    источником «разведка брендов» и доказательством, строки происхождения с
    брендом, видом, страной и ключом прогона;
  · ПОСЛЕ ЗАПИСИ ЗАМЕР dealer_link сводит заведённых — «сведено» растёт ровно
    на заведённые записи; второй прогон не заводит никого;
  · страница /suppliers (публикатор) видит источник «разведка брендов» и note;
  · откат пометкой: ничего не удалено, признаки rejected, сущность inactive,
    замер снова их не сводит; повторная запись возвращает ТЕ ЖЕ номера;
  · непройденный гейт до записи и непройденная проверка после записи —
    откат своей транзакции, в базе ничего;
  · файл схемы применяется повторно, после него — схема поставщиков, и без
    ролей Supabase; права — только у сервисной роли.
База — одноразовая, в локали C.UTF-8 (LIBRARY_SQL_TEST_DSN, правило 21а).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from library import dealer_link as dl  # noqa: E402
from library import load_research_dealers as ld  # noqa: E402
from library import supplier_link as sl  # noqa: E402
from tests.test_library_schema_sql import операторы  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "research_dealers_sql_test"
БЕЗ_РОЛЕЙ = "research_dealers_sql_noroles"
ФАЙЛ = "research_dealers_schema.sql"
ФАЙЛЫ = ("suppliers_schema.sql", ФАЙЛ)
А, Б = "KV-S-000041-1", "KV-S-000042-2"
ИНН_А, ИНН_Б = "0000000018", "1111111117"

КОРПУС = f"""
insert into sup_entity (id, kind, display_name, resolution) values
 ('{А}', 'legal', 'Альфа Насосы', 'resolved'),
 ('{Б}', 'legal', 'Спорная Выдумка', 'resolved');
insert into sup_number_registry (sup_id, seq, run_id) values ('{А}', 41, 'm1'), ('{Б}', 42, 'm1');
insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) values
 ('{А}', 'domain', 'alpha-pumps.example', 'ALPHAPUMPSEXAMPLE', 'сведение реестров', 'verified', 'm1'),
 ('{Б}', 'inn', '{ИНН_Б}', '{ИНН_Б}', 'реквизиты портала', 'verified', 'm1');
"""

РАЗВЕДКА = [{"oem_key": "vydumka", "dealers": [
    {"company": "Альфа Насосы", "country": "Нигдения", "role": "официальный дилер",
     "domain": "alpha-pumps.example", "domain_source": "https://alpha-pumps.example/dealers"},
    {"company": "Бета Уплотнения (дистрибьютор)", "country": "Нигдения, склад в столице",
     "role": "официальный дилер", "domain": "beta-seal.example",
     "domain_source": "https://beta-seal.example/about"},
    {"company": "Гамма", "country": "Нигдения", "role": "дилер", "sources": ["https://x.example/"]},
    {"company": f"ООО Дельта Выдуманная (ИНН {ИНН_А})", "country": "Россия",
     "role": "дочерняя компания изготовителя", "sources": ["https://delta.example/rekvizity"]},
    {"company": f"Тета (ИНН {ИНН_Б})", "country": "Нигдения", "role": "дилер",
     "domain": "alpha-pumps.example", "domain_source": "https://alpha-pumps.example/"},
]}, {"oem_key": "drugaya", "dealers": [
    {"company": "Бета Уплотнения", "country": "Нигдения", "role": "независимый продавец",
     "domain": "beta-seal.example", "domain_source": "https://beta-seal.example/drugaya"},
]}]
ТАЙНЫ = ("beta-seal", "Бета", "Дельта", ИНН_А, "KV-S-", "https://", "alpha")


def _применить(c, файлы) -> None:
    for файл in файлы:
        for оператор in операторы((ROOT / "library" / "supabase" / файл).read_text(encoding="utf-8")):
            c.execute(оператор)


@pytest.fixture()
def база():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    созданные: list[str] = []
    c = conn.cursor()
    try:
        for роль in ("anon", "authenticated", "service_role"):
            c.execute("select 1 from pg_roles where rolname = %s", (роль,))
            if not c.fetchone():
                c.execute(f"create role {роль} nologin")
                созданные.append(роль)
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        c.execute(f"create schema {СХЕМА}")
        c.execute(f"set search_path to {СХЕМА}")
        _применить(c, ФАЙЛЫ)
        c.execute(КОРПУС)
        yield conn
    finally:
        c.execute("reset search_path")
        for сх in (СХЕМА, БЕЗ_РОЛЕЙ):
            c.execute(f"drop schema if exists {сх} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


def соединение(только_чтение: bool = False):
    import psycopg2
    conn = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    if только_чтение:
        conn.set_session(readonly=True)
    return conn


def прогнать(**kw) -> int:
    conn = соединение(только_чтение=not (kw.get("apply") or kw.get("rollback")))
    try:
        return ld.прогон(conn, РАЗВЕДКА, **kw)
    finally:
        conn.close()


def выбрать(conn, sql: str, *args):
    c = conn.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute(sql, args)
    return c.fetchall()


def связь() -> dict:
    """Замер dealer_link по реестру из базы: {запись: корень}."""
    conn = соединение(только_чтение=True)
    try:
        with conn.cursor() as cur:
            р, _ = sl.читать_реестр(cur)
    finally:
        conn.close()
    return dl.сопоставить(dl.дилеры(РАЗВЕДКА), р).связь


def снимок(conn) -> tuple:
    return tuple(выбрать(conn, f"select count(*) from {т}")[0][0]
                 for т in ("sup_entity", "sup_number_registry", "sup_identifier", "sup_research_dealer"))


def test_вхолостую_ничего_не_пишет_и_только_агрегаты(база, capsys):
    до = снимок(база)
    assert прогнать() == 0
    out = capsys.readouterr().out
    assert снимок(база) == до
    assert "К ЗАВЕДЕНИЮ: сущностей                  2  (записей разведки 3)" in out
    assert "после заведения сведено станет (в памяти): 4 из 6" in out
    assert "Прогон ВХОЛОСТУЮ" in out
    for тайна in ТАЙНЫ:
        assert тайна not in out, тайна


def test_запись_и_после_неё_замер_сводит_заведённых(база, capsys):
    assert связь() == {0: А}
    assert прогнать(apply=True, run_id="dealers-t1") == 0
    out = capsys.readouterr().out
    assert re.search(r"выдано новых номеров\s+2\n", out)
    assert "сведено после записи (из базы):      4 из 6" in out
    for тайна in ТАЙНЫ:
        assert тайна not in out, тайна

    новые = dict(выбрать(база, "select display_name, id from sup_entity where id not in (%s, %s)", А, Б))
    assert set(новые) == {"Бета Уплотнения", "ООО Дельта Выдуманная"}
    бета, дельта = новые["Бета Уплотнения"], новые["ООО Дельта Выдуманная"]
    assert sorted(выбрать(база, "select seq from sup_number_registry where run_id = 'dealers-t1'")) == [(43,), (44,)]
    # Точка 4 задачи: замер dealer_link из базы сводит заведённых.
    assert связь() == {0: А, 1: бета, 3: дельта, 5: бета}

    [(страна, resolution, note, status)] = выбрать(
        база, "select country, resolution, note, status from sup_entity where id = %s", бета)
    assert (страна, resolution, status) == ("Нигдения", "candidate", "active")
    assert note == "разведка брендов: официальный дилер, дилер (drugaya, vydumka)"
    признаки = выбрать(база, "select sup_id, kind, value, source, evidence, status, run_id "
                              "from sup_identifier where source = 'разведка брендов' order by 1, 2, 3")
    assert (бета, "domain", "beta-seal.example", "разведка брендов",
            "https://beta-seal.example/about", "stated", "dealers-t1") in признаки
    assert (дельта, "inn", ИНН_А, "разведка брендов", None, "stated", "dealers-t1") in признаки
    assert (дельта, "legal", "ооо", "разведка брендов", None, "stated", "dealers-t1") in признаки
    происхождение = выбрать(база, "select sup_id, oem_key, dealer_no, kind, country, key_kind, evidence "
                                  "from sup_research_dealer where run_id = 'dealers-t1' order by 2, 3")
    assert происхождение == [
        (бета, "drugaya", 0, "дилер", "Нигдения", "domain", "https://beta-seal.example/drugaya"),
        (бета, "vydumka", 1, "официальный дилер", "Нигдения", "domain", "https://beta-seal.example/about"),
        (дельта, "vydumka", 3, "своя площадка", "Россия", "inn", "https://delta.example/rekvizity")]

    # Второй прогон не заводит никого: все с ключом теперь в реестре.
    до = снимок(база)
    assert прогнать(apply=True, run_id="dealers-t2") == 0
    assert "Заводить некого" in capsys.readouterr().out
    assert снимок(база) == до


def test_страница_видит_источник(база):
    import publish_suppliers as ps
    from library import company_names

    assert прогнать(apply=True, run_id="dealers-t1") == 0
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute(company_names.имена_sql(ps.СУЩНОСТИ_SQL, True))
    строки = c.fetchall()
    c.execute(ps.ПРИЗНАКИ_SQL, (list(ps.ПОКАЗЫВАЕМ),))
    снимок_ = ps.собрать(строки, c.fetchall(), 0)
    по_имени = {e["name"]: e for e in снимок_["entities"]}
    бета = по_имени["Бета Уплотнения"]
    assert бета["sources"] == ["разведка брендов"] and бета["domain"] == "beta-seal.example"
    assert бета["merged_by"].startswith("разведка брендов: ")
    assert бета["number"] and бета["number"].startswith("KV-S-")


def test_откат_пометкой_и_повторная_запись_возвращает_номера(база, capsys):
    assert прогнать(apply=True, run_id="dealers-t1") == 0
    номера = sorted(выбрать(база, "select sup_id from sup_research_dealer where run_id = 'dealers-t1'"))
    до = снимок(база)
    assert прогнать(rollback="dealers-t1") == 0
    out = capsys.readouterr().out
    assert re.search(r"сущностей выведено из строя\s+2\n", out)
    assert снимок(база) == до, "откат — пометкой: ни одна строка не удалена"
    assert связь() == {0: А}
    assert выбрать(база, "select count(*) from sup_identifier where source = 'разведка брендов' "
                         "and status <> 'rejected'") == [(0,)]
    assert выбрать(база, "select distinct status from sup_entity where id in "
                         "(select sup_id from sup_research_dealer)") == [("inactive",)]
    assert выбрать(база, "select count(*) from sup_research_dealer where rolled_back_at is null") == [(0,)]
    # Повторный откат того же ключа — нечего откатывать.
    assert прогнать(rollback="dealers-t1") == 3

    assert прогнать(apply=True, run_id="dealers-t3") == 0
    out = capsys.readouterr().out
    assert re.search(r"номер возвращён после отката\s+2\n", out) and "выдано новых номеров" not in out
    assert sorted(выбрать(база, "select sup_id from sup_research_dealer where run_id = 'dealers-t3'")) == номера
    assert выбрать(база, "select max(seq) from sup_number_registry") == [(44,)]
    assert выбрать(база, "select distinct status from sup_entity where id in "
                         "(select sup_id from sup_research_dealer)") == [("active",)]
    assert set(связь()) == {0, 1, 3, 5}
    assert выбрать(база, "select count(*) from sup_identifier where source = 'разведка брендов' "
                         "and status = 'stated' and run_id = 'dealers-t3'")[0][0] == 5


def test_откат_не_гасит_сущность_с_чужими_признаками(база):
    """Сведение потом дописало компании карточку портала — откат снимает наши
    признаки, но сущность остаётся действующей."""
    assert прогнать(apply=True, run_id="dealers-t1") == 0
    [(бета,)] = выбрать(база, "select id from sup_entity where display_name = 'Бета Уплотнения'")
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute("insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) "
              "values (%s, 'bitrix', '777', '777', 'bitrix', 'verified', 'm2')", (бета,))
    assert прогнать(rollback="dealers-t1") == 0
    assert выбрать(база, "select status from sup_entity where id = %s", бета) == [("active",)]


def test_гейт_до_записи_отменяет_запись(база, monkeypatch, capsys):
    monkeypatch.setitem(ld.ГЕЙТЫ, "макс_к_заведению", 1)
    до = снимок(база)
    assert прогнать(apply=True, run_id="dealers-гейт") == 1
    assert "ГЕЙТЫ НЕ СОШЛИСЬ" in capsys.readouterr().out
    assert снимок(база) == до


def test_проверка_после_записи_откатывает_транзакцию(база, monkeypatch, capsys):
    """Мутация: запись назначает записи чужую сущность — проверка из базы это
    видит, транзакция откатывается, в базе ничего."""
    настоящая = ld.записать

    def испорченная(cur, отбор, сырые, run_id):
        назначено, сч = настоящая(cur, отбор, сырые, run_id)
        return {i: А for i in назначено}, сч

    monkeypatch.setattr(ld, "записать", испорченная)
    до = снимок(база)
    assert прогнать(apply=True, run_id="dealers-после") == 1
    assert "ПРОВЕРКА ПОСЛЕ ЗАПИСИ НЕ СОШЛАСЬ" in capsys.readouterr().out
    assert снимок(база) == до


def test_ключ_занят_к_моменту_записи_не_пишется(база, monkeypatch, capsys):
    """Реестр изменился между замером и записью: домен уже у другой сущности —
    группа пропускается, признак не крадётся, остальные пишутся."""
    настоящая = ld.записать

    def гонка(cur, отбор, сырые, run_id):
        cur.execute("insert into sup_identifier (sup_id, kind, value, value_norm, source, status, run_id) "
                    "values (%s, 'domain', 'beta-seal.example', 'BETASEALEXAMPLE', 'сведение', 'stated', 'm3')",
                    (Б,))
        return настоящая(cur, отбор, сырые, run_id)

    monkeypatch.setattr(ld, "записать", гонка)
    assert прогнать(apply=True, run_id="dealers-гонка") == 0
    assert re.search(r"пропущено: ключ занят к моменту записи\s+1\n", capsys.readouterr().out)
    assert выбрать(база, "select count(*) from sup_identifier where value_norm = 'BETASEALEXAMPLE'") == [(1,)]
    assert выбрать(база, "select display_name from sup_entity e join sup_research_dealer r on r.sup_id = e.id") \
        == [("ООО Дельта Выдуманная",)]


def test_без_таблицы_происхождения_запись_не_идёт(база, capsys):
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute("drop table sup_research_dealer")
    assert прогнать(apply=True, run_id="dealers-x") == 2
    assert прогнать(rollback="dealers-x") == 3
    assert прогнать() == 0          # замер вхолостую таблицы не требует


def test_схема_повторно_и_схема_поставщиков_после_права_только_сервису(база):
    c = база.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    _применить(c, (ФАЙЛ, "suppliers_schema.sql", ФАЙЛ))
    for роль, можно in (("anon", False), ("authenticated", False), ("service_role", True)):
        [(есть,)] = выбрать(база, "select has_table_privilege(%s, %s, 'select')",
                            роль, f"{СХЕМА}.sup_research_dealer")
        assert есть is можно, роль
    [(сила, форс)] = выбрать(база, "select relrowsecurity, relforcerowsecurity from pg_class c "
                                   "join pg_namespace n on n.oid = c.relnamespace "
                                   "where n.nspname = %s and c.relname = 'sup_research_dealer'", СХЕМА)
    assert сила and форс
    # Вид вне списка не пройдёт: список — отдельным alter (правило 21).
    with pytest.raises(Exception, match="sup_research_dealer_kind_check"):
        c.execute(f"insert into sup_research_dealer (sup_id, oem_key, dealer_no, company, kind, key_kind, "
                  f"key_value, run_id) values ('{А}', 'x', 0, 'x', 'посредник', 'domain', 'x', 'r')")


def test_схема_применяется_без_ролей_supabase(база):
    """Правило 20: pg_roles подменён пустым видом, как на чистом PostgreSQL."""
    c = база.cursor()
    c.execute(f"drop schema if exists {БЕЗ_РОЛЕЙ} cascade")
    c.execute(f"create schema {БЕЗ_РОЛЕЙ}")
    c.execute(f"create view {БЕЗ_РОЛЕЙ}.pg_roles as select rolname from pg_catalog.pg_roles where false")
    c.execute(f"set search_path to {БЕЗ_РОЛЕЙ}, pg_catalog")
    c.execute("select count(*) from pg_roles where rolname in ('anon', 'authenticated', 'service_role')")
    assert c.fetchone()[0] == 0, "подмена pg_roles не действует — проверка ничего бы не доказала"
    _применить(c, ФАЙЛЫ)
    c.execute("select has_table_privilege('service_role', %s, 'select')", (f"{БЕЗ_РОЛЕЙ}.sup_research_dealer",))
    assert c.fetchone()[0] is False
    c.execute("reset search_path")
