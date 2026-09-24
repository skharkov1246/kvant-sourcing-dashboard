"""Реестр брендов (этап 8.2) на настоящей схеме: засев, повтор, очередь, замер, откат.

Проверяется ровно критерий готовности плана: доля строк цены с разрешённым
брендом считается по каждому из трёх разрезов; каждое неразрешённое написание
стоит в очереди, а не пропадает; повторный засев не создаёт дублей. Плюс то, без
чего этому нельзя верить: ключ написания в SQL тот же, что в Python; запись
отменяется сама при непройденном гейте; откат по run_id возвращает прежнее
состояние; страница /brands берёт ключ бренда из реестра.

Схемы применяются в свою схему одноразовой базы, как прогон миграций; корпус
придуман (CLAUDE.md, правило 18), ответы посчитаны руками. Работает при
поднятой базе PostgreSQL 16 в локали C.UTF-8 (LIBRARY_SQL_TEST_DSN, правило 21а).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from library import brand_registry as br
from library import codes_sql, indexer
from library import load_brands as lb
from tests.test_library_schema_sql import операторы

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

ИМЯ = "brand_registry_sql_test"
ФАЙЛЫ = ("schema.sql", "schema_junk.sql", "suppliers_schema.sql", "brands_schema.sql")

СЛОВАРЬ = {"records": [
    {"oem_key": "kelton", "name": "Kelton GmbH", "spellings": [
        {"spelling": "Kelton", "where": "выдумка:a"}, {"spelling": "Келтон", "where": "выдумка:b"},
        {"spelling": "KELTON GmbH", "where": "выдумка:a"}]},
    {"oem_key": "vortexa", "name": "Vortexa", "spellings": [
        {"spelling": "Vortexa AB", "where": "выдумка:a"}, {"spelling": "Vörtexa", "where": "выдумка:c"}]},
    # Одно написание у двух записей: выбрать нечем — «спорно», в карту не идёт.
    {"oem_key": "alfaone", "name": "Alfa One", "spellings": [{"spelling": "Alfa", "where": "выдумка:a"}]},
    {"oem_key": "alfatwo", "name": "Alfa Two", "spellings": [{"spelling": "ALFA", "where": "выдумка:b"}]},
]}
АТЛАС = {"makers": [
    {"name": "Kelton GmbH & Co", "owner": "Выдуманный холдинг", "country": "Нигдения"},
    {"name": "Brisko Pumpen OG", "owner": "Сам себе", "country": "Нигдения"},
    {"name": "Vortexa Energy Solutions", "country": "Нигдения"},
]}
АЛИАСЫ = {"келтон-м": "Kelton", "zzq": "Нет такого"}
СП176 = [(501, "Kelton"), (502, "Grifon Seals"), (503, "не указан"), (504, "Vortexa Energy"),
         (505, "Brisko Pumpen OG")]

КОРПУС = """
insert into lib_files (file_id, deal_id, origin, field, status, side) values
 ('f1','D1','поле сделки','ufCrm_1633502831','разобран','заказчик'),
 ('f2','D2','поле сделки','ufCrm_1585568303498','разобран','мы');
insert into lib_demand (deal_id, item_name, oem, part_number, source_file) values
 ('D1','Втулка выдуманная','Vortexa AB','VX-1','f1'),
 ('D1','Клапан выдуманный','Акмеро Индастри','AK-2','f1'),
 ('D1','Прокладка выдуманная',null,'PR-3','f1'),
 ('D1','Клапан выдуманный','Келтон','KL-9','f1'),
 ('D2','Клапан выдуманный','Kelton','KL-7','f2');
insert into lib_parts (id, catalog_no, name, oem) values
 ('kl7','KL-7','Клапан выдуманный','Kelton GmbH'),
 ('gs1','GS-1','Кольцо выдуманное','Neverland Works');
insert into lib_suppliers (name, kind) values
 ('Kelton GmbH','изготовитель'), ('Орион Литьё','изготовитель (из каталога)'),
 ('неизвестно','изготовитель'), ('Посредник выдуманный','trader');
insert into lib_prices (feed, source, part_number, item_name, price, currency, rfq_id, rfq_brands, oem) values
 ('разбор КП','КП','KL-7','Клапан выдуманный',10,'USD','R1','501','Kelton'),
 ('разбор КП','КП','GS-1','Кольцо выдуманное',20,'USD','R2','502,503','Келтон/Grifon Seals'),
 ('разбор КП','КП','KL-7','Клапан выдуманный',11,'USD','R3',null,'Акмеро Индастри'),
 ('разбор КП','КП','XX-1','Нечто выдуманное',12,'USD','R4','999',null),
 ('разбор КП','КП',null,'Нечто выдуманное',13,'USD','R5',null,'Не указан'),
 ('ТКП КВАНТ (отпускная цена)','КП','KL-7','Клапан',99,'USD','R1',null,'Акмеро Индастри');
"""

# Строки ключа написания: правовые формы, диакритика, двойники, кириллица.
НАПИСАНИЯ = ["Wärtsilä Oyj", "SКF", "Grundfos A/S", "ООО «Ромашка»", "Kelton GmbH & Co",
             "S.p.A. Brisko", "Ёлка-Пром", "3M", "", "  ", "ABB Ltd.", "Çukurova Makina",
             "ОАО Завод-1", "Hölle & Söhne KG"]


def подключить():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN)["dbname"] == "library_sql_test"
    # search_path — в строке подключения: SET в транзакции откатился бы вместе
    # с ней (правило 9), а засев откатывает транзакцию на гейте.
    return psycopg2.connect(DSN, options=f"-c search_path={ИМЯ}")


def читать_сп176(k, n):
    макс = max(i for i, _ in СП176)
    низ, верх = indexer.диапазон_части(макс, k, n)
    return ([(i, t) for i, t in СП176 if i > низ and (верх is None or i <= верх)],
            (низ, верх, макс))


def засеять(conn, run_id, shards=2, apply=True):
    for k in range(shards):
        assert lb.ступень_реестр(conn, k, shards, apply, run_id, читать_сп176) == 0
    for k in range(shards):
        assert lb.ступень_написания(conn, k, shards, apply, run_id) == 0


def счёт(conn):
    with conn.cursor() as c:
        c.execute("select count(*) from lib_brands")
        бренды = c.fetchone()[0]
        c.execute("select count(*) from lib_brand_alias")
        написания = c.fetchone()[0]
        c.execute("select source, status, count(*) from lib_brand_alias group by 1, 2")
        разбивка = {(s, st): n for s, st, n in c.fetchall()}
    conn.rollback()
    return бренды, написания, разбивка


@pytest.fixture(scope="module")
def база():
    import psycopg2

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
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        c.execute(f"create schema {ИМЯ}")
        c.execute(f"set search_path to {ИМЯ}")
        for имя in ФАЙЛЫ:
            for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
                c.execute(оператор)
        c.execute(КОРПУС)
        yield
    finally:
        c.execute(f"drop schema if exists {ИМЯ} cascade")
        for роль in созданные:
            c.execute(f"drop role if exists {роль}")
        conn.close()


@pytest.fixture(scope="module")
def засев(база):
    mp = pytest.MonkeyPatch()
    файлы = {br.ФАЙЛ_СЛОВАРЯ: СЛОВАРЬ, br.ФАЙЛ_АТЛАСА: АТЛАС}
    mp.setattr(lb, "читать_json", lambda путь: файлы.get(путь))
    mp.setattr(br.equipment, "OEM_ALIAS", АЛИАСЫ)
    conn = подключить()
    засеять(conn, "r1")
    yield conn, mp, файлы
    mp.undo()
    conn.close()


def test_ключ_написания_в_sql_тот_же_что_в_python(база):
    conn = подключить()
    with conn.cursor() as c:
        c.execute("select lower('ШАЙБА')")
        assert c.fetchone()[0] == "шайба", "база не в локали UTF-8 (правило 21а)"
        for н in НАПИСАНИЯ:
            c.execute("select lib_brand_key(%s)", (н,))
            assert c.fetchone()[0] == codes_sql.ключ_написания(н), н
    conn.close()


def test_константы_функции_взяты_из_codes_sql():
    текст = (ROOT / "library" / "supabase" / "brands_schema.sql").read_text(encoding="utf-8")
    for константа in (codes_sql.DIACRITICS_FROM, codes_sql.DIACRITICS_TO, codes_sql.HOMO_FROM,
                      codes_sql.HOMO_TO, codes_sql.LEGAL_FORMS_KEY):
        assert "'" + константа + "'" in текст, константа


def test_схема_применяется_повторно(база):
    conn = подключить()
    conn.autocommit = True
    with conn.cursor() as c:
        for оператор in операторы((ROOT / "library" / "supabase" / "brands_schema.sql")
                                  .read_text(encoding="utf-8")):
            c.execute(оператор)
        c.execute("select count(*) from pg_constraint where conname like 'lib_brand_alias_%%'")
        assert c.fetchone()[0] >= 3
    conn.close()


def test_засев_реестра(засев):
    conn, _, _ = засев
    with conn.cursor() as c:
        c.execute("select brand_key, owner, sources from lib_brands order by 1")
        бренды = {k: (o, s) for k, o, s in c.fetchall()}
        c.execute("select spelling_key, brand_key from lib_brand_map")
        карта = dict(c.fetchall())
        c.execute("select sp176_id, brand_key from lib_brand_sp176")
        карточка = dict(c.fetchall())
        c.execute("select source, spelling, status from lib_brand_queue")
        очередь = {(s, sp): st for s, sp, st in c.fetchall()}
    conn.rollback()
    # Словарь заводит свои бренды, атлас подписывает kelton владельцем и заводит
    # brisko; «Vortexa Energy Solutions» похоже на vortexa — спорно, не двойник.
    assert {"kelton", "vortexa", "alfaone", "alfatwo", "briskopumpenog", "grifonseals"} == set(бренды)
    assert бренды["kelton"][0] == "Выдуманный холдинг"
    assert "zip/data/oem_atlas.json" in бренды["kelton"][1]
    assert бренды["grifonseals"][1] == ["СП-176"]
    # Карта: написания сведены, спорное «alfa» в неё не попало.
    assert карта["келтон"] == "kelton" and карта["kelton"] == "kelton" and карта["келтонм"] == "kelton"
    assert карта["vortexa"] == "vortexa" and "alfa" not in карта
    # Элемент СП-176 → бренд: 501 и 505 к существующим, 502 — новый.
    assert карточка == {501: "kelton", 502: "grifonseals", 505: "briskopumpenog"}
    # Очередь: спорные и неразрешённые — все здесь, ни одно не пропало.
    assert очередь[("dict/oem.json", "Alfa")] == "спорно"
    assert очередь[("zip/data/oem_atlas.json", "Vortexa Energy Solutions")] == "спорно"
    assert очередь[("СП-176", "Vortexa Energy")] == "спорно"
    assert очередь[("OEM_ALIAS", "zzq")] == "в очереди"
    assert очередь[("lib_prices.oem", "Акмеро Индастри")] == "в очереди"
    assert очередь[("lib_demand.oem", "Акмеро Индастри")] == "в очереди"
    assert очередь[("lib_parts.oem", "Neverland Works")] == "в очереди"
    assert очередь[("lib_suppliers", "Орион Литьё")] == "в очереди"


def test_не_бренд_и_разрешённые_данные(засев):
    conn, _, _ = засев
    with conn.cursor() as c:
        c.execute("select source, spelling, status, brand_key, n_rows, run_id from lib_brand_alias "
                  "where source in ('lib_prices.oem', 'lib_suppliers', 'СП-176')")
        строки = {(s, sp): (st, b, n, r) for s, sp, st, b, n, r in c.fetchall()}
    conn.rollback()
    assert строки[("lib_prices.oem", "Не указан")][0] == "не бренд"
    assert строки[("lib_prices.oem", "Grifon Seals")][:2] == ("разрешено", "grifonseals")
    assert строки[("lib_prices.oem", "Kelton")][:3] == ("разрешено", "kelton", 1)
    assert строки[("lib_suppliers", "неизвестно")][0] == "не бренд"
    assert строки[("lib_suppliers", "Kelton GmbH")][:2] == ("разрешено", "kelton")
    assert строки[("СП-176", "не указан")][0] == "не бренд"
    assert ("lib_suppliers", "Посредник выдуманный") not in строки
    assert {r for *_, r in строки.values()} == {"r1"}


def test_повторный_засев_не_создаёт_дублей(засев):
    conn, _, _ = засев
    до = счёт(conn)
    засеять(conn, "r2", shards=3)       # и при другом числе частей
    после = счёт(conn)
    assert до == после
    with conn.cursor() as c:
        c.execute("select count(*) from lib_brand_alias where run_id = 'r2'")
        assert c.fetchone()[0] == 0
    conn.rollback()


def test_каждое_неразрешённое_написание_в_очереди(засев):
    conn, _, _ = засев
    with conn.cursor() as c:
        c.execute(br.вне_очереди_sql(br.КАРТА_РЕЕСТРА))
        итог = {s: (n, вне) for s, n, вне in c.fetchall()}
    conn.rollback()
    assert итог == {"lib_demand.oem": (1, 0), "lib_parts.oem": (1, 0), "lib_prices.oem": (1, 0)}


def test_замер_трёх_разрезов_и_спроса(засев):
    conn, _, файлы = засев
    with conn.cursor() as c:
        c.execute(br.замер_sql(True, br.КАРТА_РЕЕСТРА))
        реестр = {r[0]: r[2:] for r in c.fetchall()}
        план = br.план_файлов(файлы[br.ФАЙЛ_СЛОВАРЯ], файлы[br.ФАЙЛ_АТЛАСА], АЛИАСЫ)
        c.execute(br.замер_sql(False, codes_sql.карта_sql(
            [(k, next(iter(v))) for k, v in план.карта.items() if len(v) == 1])))
        файл = {r[0]: r[2:] for r in c.fetchall()}
    conn.rollback()
    # (всего, с текстом, с брендом, разрешено) — посчитано руками по КОРПУСУ.
    assert реестр[1] == (5, 4, 3, 2)      # слово поставщика: Kelton, Келтон/Grifon — да
    assert реестр[2] == (5, 3, 3, 2)      # карточка: 501 и 502 разрешены, 999 — нет
    assert реестр[3] == (5, 3, 3, 2)      # артикул → каталог: KL-7 дважды
    assert реестр[4] == (5, 5, 4, 3)      # хотя бы один разрез
    assert реестр[5] == (5, 4, 4, 3)      # спрос, все живые строки
    assert реестр[6] == (4, 3, 3, 2)      # спрос заказчика
    # Словарь-файл ключей карточки не знает: разрез карточки без реестра — ноль.
    assert файл[2][3] == 0 and файл[1] == (5, 4, 3, 2)


def test_ступень_замера_печатает_только_агрегаты(засев, capsys):
    conn, _, _ = засев
    assert lb.ступень_замер(conn, apply=True, run_id="m1") == 0
    журнал = capsys.readouterr().out
    assert "по реестр базы" in журнал and "по словарь-файл" in журнал
    for секрет in ("Kelton", "Келтон", "Акмеро", "Grifon", "Neverland", "Орион", "KL-7"):
        assert секрет not in журнал, секрет
    with conn.cursor() as c:
        c.execute("select nums from lib_metric_runs where metric = %s and run_key = 'm1'",
                  (lb.МЕТРИКА,))
        числа = c.fetchone()[0]
    conn.rollback()
    assert числа["реестр.1.разрешено"] == 2 and числа["вне_очереди.lib_prices.oem"] == 0


def test_разрешение_из_очереди_и_откат(засев):
    conn, mp, файлы = засев
    было = счёт(conn)
    словарь = {"records": СЛОВАРЬ["records"] + [
        {"oem_key": "akmero", "name": "Акмеро", "spellings": [
            {"spelling": "Акмеро Индастри", "where": "выдумка:d"}]}]}
    mp.setitem(файлы, br.ФАЙЛ_СЛОВАРЯ, словарь)
    try:
        засеять(conn, "r3")
    finally:
        mp.setitem(файлы, br.ФАЙЛ_СЛОВАРЯ, СЛОВАРЬ)
    with conn.cursor() as c:
        c.execute("select status, brand_key, run_id, prev_status, prev_run_id from lib_brand_alias "
                  "where source = 'lib_prices.oem' and spelling = 'Акмеро Индастри'")
        assert c.fetchone() == ("разрешено", "akmero", "r3", "в очереди", "r1")
    conn.rollback()
    lb.откатить(conn, "r3")
    assert счёт(conn) == было
    with conn.cursor() as c:
        c.execute("select status, brand_key, run_id, prev_run_id from lib_brand_alias "
                  "where source = 'lib_prices.oem' and spelling = 'Акмеро Индастри'")
        assert c.fetchone() == ("в очереди", None, "r1", None)
    conn.rollback()


def test_гейт_отменяет_запись(засев):
    conn, _, _ = засев
    было = счёт(conn)
    плохое = br._написание("Выдумка Гейта", "lib_prices.oem", status="в очереди")
    плохое["spelling_key"] = "неправильныйключ"
    with pytest.raises(RuntimeError, match="гейты не пройдены"):
        lb.записать(conn, [], [плохое], "r-bad")
    assert счёт(conn) == было
    # Разрешённое написание без бренда в реестре тоже не проходит.
    сирота = br._написание("Сирота", "lib_prices.oem", status="разрешено", brand_key="nobrand")
    with pytest.raises(RuntimeError, match="бренд есть в реестре"):
        lb.записать(conn, [], [сирота], "r-bad")
    assert счёт(conn) == было


def test_холостой_засев_ничего_не_пишет(засев):
    conn, _, _ = засев
    было = счёт(conn)
    засеять(conn, "r-dry", apply=False)
    assert счёт(conn) == было


def test_страница_берёт_ключ_бренда_из_реестра(засев):
    """Публикатор /brands: реестр есть — карта из lib_brand_map, ключ карточки
    по элементу СП-176, имя элемента из реестра."""
    import importlib.util

    from library import brands

    spec = importlib.util.spec_from_file_location("kvant_publish_brands_t",
                                                  ROOT / "scripts" / "publish_brands.py")
    pb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pb)
    conn, _, _ = засев
    with conn.cursor() as c:
        c.execute(codes_sql.SETTINGS)
        реестр = pb.читать_реестр(c)
        assert реестр and реестр["карточка"]["501"] == "kelton" and реестр["имена"]["502"] == "Grifon Seals"
        c.execute(codes_sql.запросы(из_реестра=True)["brands"])
        колонки = [d[0] for d in c.description]
        строки = [dict(zip(колонки, r)) for r in c.fetchall()]
        c.execute(codes_sql.запросы(из_реестра=True)["card_brands"])
        колонки = [d[0] for d in c.description]
        карточка = [dict(zip(колонки, r)) for r in c.fetchall()]
    conn.rollback()
    ключи = {r["brand_key"] for r in строки}
    # «Келтон» спроса заказчика сведён к kelton картой реестра, а не остался «келтон».
    assert "kelton" in ключи and "келтон" not in ключи
    снимки = brands.собрать({"brands": строки, "card_brands": карточка}, {},
                            словарь=реестр["словарь"], ключи_карточки=реестр["карточка"],
                            имена_брендов=реестр["имена"])
    сводка = снимки[brands.КЛЮЧ]
    assert сводка["dict"]["from"].startswith("реестр базы")
    по_номеру = {c["id"]: c for c in сводка["card_brands"]}
    assert по_номеру["501"]["k"] == "kelton" and по_номеру["502"]["k"] == "grifonseals"
    assert "k" not in по_номеру["999"]


def test_без_реестра_страница_работает_по_старому(база):
    import importlib.util

    spec = importlib.util.spec_from_file_location("kvant_publish_brands_t2",
                                                  ROOT / "scripts" / "publish_brands.py")
    pb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pb)
    import psycopg2

    conn = psycopg2.connect(DSN, options="-c search_path=public")
    with conn.cursor() as c:
        # В public этой базы реестра нет — как на живой до миграции.
        c.execute("select to_regclass('public.lib_brand_map')")
        if c.fetchone()[0] is None:
            assert pb.читать_реестр(c) is None
    conn.close()
