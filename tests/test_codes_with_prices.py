"""«Сколько кодов с ценой и без» — три разных ответа, и подменять их нельзя.

ЗАЧЕМ. Вопрос владельца, 22.09.2026. У него три прочтения, и каждое своё:

  · коды, которые МЫ СПРАШИВАЛИ — их и надо закрывать ценой, это наша работа;
  · коды, по которым ЦЕНА ЕСТЬ — среди них бывают такие, которых мы не спрашивали;
  · коды КАТАЛОГА — наша библиотека деталей, пересекается с первыми лишь частью.

Ответ уходит владельцу как факт, поэтому проверяется то, что даёт НЕВЕРНОЕ ЧИСЛО.

Четыре ловушки, каждая уже срабатывала в этом проекте:

1. КОД — ЭТО КЛЮЧ, А НЕ НАПИСАНИЕ. «AAA-111», «AAA 111» и «aaa111» — один код.
   По написаниям кодов выходит втрое больше, чем есть.
2. СПРОС ЧИТАЕТСЯ ИЗ lib_demand_live. Пункты договоров, попавшие в спрос и уже
   помеченные мусором, иначе станут «кодами без цены» и раздуют знаменатель.
   Ровно этой подменой замер перекрёстной системы однажды посчитал спрос.
3. ЦЕНА БЫВАЕТ НЕ ТОЛЬКО ИЗ ПРЕДЛОЖЕНИЯ. lib_prices держит и прайсы, и таможню.
   «Есть цена вообще» и «поставщик ответил на наш запрос» — разные утверждения.
4. РАЗРЯД, КОТОРЫЙ НЕ МОЖЕТ БЫТЬ НЕПУСТЫМ, — тавтология. Счётчик «без выбора и со
   спросом» однажды равнялся «без выбора» целиком.

Отдельно проверяется ЗАПИСЬ ТОЧКИ ИСТОРИИ (lib_metric_runs): из неё берётся
динамика день ко дню на странице счётчика, и у неё свои две цены ошибки —
разошедшаяся с напечатанным цифра и утёкшее в публичную таблицу наименование.

Корпус придуман (CLAUDE.md, правило 18), ответы посчитаны руками.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")

СХЕМА = "тест_кодов_с_ценами"


def скрипт():
    spec = importlib.util.spec_from_file_location(
        "kvant_codes", ROOT / "scripts" / "codes_with_prices.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# КОРПУС. Считаем руками.
#
# СПРОС (lib_demand_live — строка 4 помечена мусором и не считается):
#   AAA-111 → ключ aaa111   · есть цена предложения          → с ценой КП
#   AAA 111 → ключ aaa111   · ТО ЖЕ САМОЕ, один код, не два
#   BBB222  → ключ bbb222   · цена только из потока «прайс»   → чужой поток
#   CCC333  → ключ ccc333   · ПОМЕЧЕН МУСОРОМ, в счёт не идёт
#   DDD444  → ключ ddd444   · цены нет вовсе                  → без цены
#   EEE555  → ключ eee555   · цены нет вовсе                  → без цены
#   xx      → ключ xx       · короткий ключ, но считается: отсев по длине >= 2
#
# Итого кодов спроса: aaa111, bbb222, ddd444, eee555, xx = ПЯТЬ
#   с ценой КП        — 1 (aaa111)
#   чужой поток       — 1 (bbb222)
#   без цены вовсе    — 3 (ddd444, eee555, xx)
#
# ЦЕНЫ ПРЕДЛОЖЕНИЙ: aaa111 (спрашивали, есть в каталоге), zzz999 (НЕ спрашивали)
# КАТАЛОГ: aaa111 (с ценой, спрашивали), kkk777 (ни цены, ни спроса)
КОРПУС = """
create function lib_pn_key(t text) returns text language sql immutable as $$
  select left(regexp_replace(replace(lower(coalesce(t, '')), 'ё', 'е'),
                             '[^0-9a-zа-я]', '', 'g'), 80)
$$;

create table lib_demand (
  id bigserial primary key, deal_id text, item_name text, part_number text);
create table lib_row_junk (
  demand_id bigint primary key, rule text, run_id text, revoked_at timestamptz);
create view lib_demand_live as
  select d.* from lib_demand d
   where not exists (select 1 from lib_row_junk j
                      where j.demand_id = d.id and j.revoked_at is null);
create table lib_prices (
  id bigserial primary key, feed text, part_number text, price numeric);
create table lib_parts (id text primary key, catalog_no text);
create table lib_metric_runs (
  metric text not null, run_key text not null,
  measured_at timestamptz not null default now(),
  nums jsonb not null, note text,
  primary key (metric, run_key));

insert into lib_demand (id, deal_id, part_number) values
  (1, 'D1', 'AAA-111'), (2, 'D2', 'AAA 111'), (3, 'D1', 'BBB222'),
  (4, 'D1', 'CCC333'), (5, 'D2', 'DDD444'), (6, 'D2', 'EEE555'),
  (7, 'D1', 'xx');
insert into lib_row_junk (demand_id, rule, run_id) values (4, 'проза', 'прогон-1');

insert into lib_prices (feed, part_number, price) values
  ('разбор КП', 'AAA-111', 100), ('разбор КП', 'aaa111', 110),
  ('разбор КП', 'ZZZ999', 200),
  ('прайс',     'BBB-222', 300);

insert into lib_parts (id, catalog_no) values
  ('p1', 'AAA111'), ('p2', 'KKK777');
"""


@pytest.fixture()
def cur():
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.execute(f'create schema "{СХЕМА}"')
    c.execute(f'set search_path to "{СХЕМА}"')
    c.execute(КОРПУС)
    yield c
    c.execute(f'drop schema if exists "{СХЕМА}" cascade')
    c.close()
    conn.close()


def подготовь(cur, м):
    cur.execute(м.ПОДГОТОВКА, (м.FEED_КП,))


def test_разные_написания_одного_номера_дают_один_код(cur):
    """Ловушка 1: по написаниям кодов выходит больше, чем есть."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.ОБЪЁМ)
    кс, стрс, ккп, стркп, кц, кт = [int(x) for x in cur.fetchone()]
    # Пять кодов спроса из шести живых строк: AAA-111 и AAA 111 — один код.
    assert кс == 5, "разные написания посчитаны как разные коды"
    assert стрс == 6, "строк спроса шесть: помеченная мусором не считается"
    # Цены предложений: aaa111 (две строки) и zzz999 — два кода, три строки.
    assert (ккп, стркп) == (2, 3)
    # Цены всех потоков: aaa111, zzz999, bbb222 — три кода.
    assert кц == 3
    assert кт == 2


def test_помеченный_мусор_не_становится_кодом_без_цены(cur):
    """Ловушка 2: иначе знаменатель раздут, а доля закрытых занижена."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute("select count(*) from коды_спроса where ключ = 'ccc333'")
    assert cur.fetchone()[0] == 0, "код из помеченной мусором строки попал в счёт"


def test_главный_ответ_разложен_на_три_разряда(cur):
    """Сколько спрошенных кодов с ценой поставщика, сколько без цены вовсе."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.СПРОС_С_ЦЕНОЙ)
    всего, с_кп, чужой, без, стр_с, стр_без = [int(x) for x in cur.fetchone()]
    assert всего == 5
    assert с_кп == 1, "цена предложения только у aaa111"
    assert чужой == 1, "у bbb222 цена есть, но из потока «прайс» — это не ответ нам"
    assert без == 3, "ddd444, eee555, xx — ни одной цены"
    # Разряды взаимоисключающие и покрывают всё.
    assert с_кп + чужой + без == всего
    # Строки спроса: за aaa111 их две (два написания).
    assert стр_с == 2
    assert стр_без == 3


def test_обратная_сторона_коды_с_ценой(cur):
    """Поставщик присылает прайс шире запроса — это отдельный разряд."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.ЦЕНЫ_ПРОТИВ_СПРОСА)
    кодов, спраш, не_спраш, в_кат = [int(x) for x in cur.fetchone()]
    assert кодов == 2
    assert спраш == 1, "aaa111 спрашивали"
    assert не_спраш == 1, "zzz999 не спрашивали — прайс шире запроса"
    assert спраш + не_спраш == кодов
    assert в_кат == 1, "в каталоге есть только aaa111"


def test_каталог_считается_отдельной_вселенной(cur):
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.КАТАЛОГ)
    кат, с_ценой, спраш = [int(x) for x in cur.fetchone()]
    assert кат == 2
    assert с_ценой == 1
    assert спраш == 1
    # kkk777 — ни цены, ни спроса: каталог шире того, что мы спрашиваем.
    assert кат - спраш == 1


def test_ни_один_разряд_не_тавтологичен(cur):
    """Ловушка 4: разряд, который не может быть непустым, ничего не измеряет.

    Корпус нарочно даёт непустое значение КАЖДОМУ разряду. Если разряд окажется
    нулём здесь, значит он не может быть непустым вообще, и его надо убирать или
    переписывать, а не оставлять украшением.
    """
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.СПРОС_С_ЦЕНОЙ)
    всего, с_кп, чужой, без, _, _ = [int(x) for x in cur.fetchone()]
    for имя, v in (("всего", всего), ("с ценой КП", с_кп),
                   ("чужой поток", чужой), ("без цены", без)):
        assert v > 0, f"разряд «{имя}» пуст — он не может быть непустым?"
    cur.execute(м.ЦЕНЫ_ПРОТИВ_СПРОСА)
    for имя, v in zip(("кодов", "спрашивали", "не спрашивали", "в каталоге"),
                      [int(x) for x in cur.fetchone()]):
        assert v > 0, f"разряд «{имя}» пуст"


def test_оценка_правдоподобия_не_отбрасывает_строк(cur):
    """Это ОЦЕНКА, а не факт: счётчик описывает, но ничего не выкидывает."""
    м = скрипт()
    подготовь(cur, м)
    cur.execute(м.ПРАВДОПОДОБНОСТЬ)
    в, прав, без_ц, кор, длин = [int(x) for x in cur.fetchone()]
    # Всего — столько же кодов, сколько в главном ответе: оценка ничего не режет.
    assert в == 5
    # Правдоподобны четыре: aaa111, bbb222, ddd444, eee555. «xx» — нет.
    assert прав == 4
    assert кор == 1, "«xx» короче четырёх знаков"
    assert без_ц == 1, "«xx» без единой цифры"
    assert длин == 0


# ── ТОЧКА ИСТОРИИ ──────────────────────────────────────────────────────────
# Из этих строк рисуется динамика на странице счётчика, поэтому проверяется не
# только «записалось», но и «записалось то же, что напечатано» и «ничего кроме
# чисел» (CLAUDE.md, правило 17: таблицу можно показывать целиком).

def точки(cur):
    cur.execute("select run_key, nums, note from lib_metric_runs "
                "where metric = 'коды_и_цены' order by run_key")
    return cur.fetchall()


# ПОЧЕМУ КОРПУС ЭТИХ ТЕСТОВ ЛЕЖИТ В public, А НЕ В ОТДЕЛЬНОЙ СХЕМЕ. Скрипты
# проекта подключаются с options="-c statement_timeout=…", а этот параметр
# ЗАМЕЩАЕТ options из строки подключения целиком — значит search_path через DSN
# до скрипта не доходит ни в одном из них. Соглашение общее и правильное
# (правило 9: таймаут задаётся подключением), поэтому подстраивается тест: он
# поднимает корпус в public одноразовой базы CI и убирает за собой.
ТАБЛИЦЫ_КОРПУСА = ("lib_row_junk", "lib_demand_live", "lib_demand", "lib_prices",
                   "lib_parts", "lib_metric_runs")


@pytest.fixture()
def корпус_в_public():
    import psycopg2
    from psycopg2.extensions import parse_dsn

    # Тот же предохранитель, что у теста паритета ключа: корпус создаётся в
    # public, и ошибиться базой здесь стоило бы данных.
    config = parse_dsn(DSN)
    assert config["host"] in ("127.0.0.1", "localhost")
    assert config["dbname"] == "library_sql_test"

    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()

    def снести():
        c.execute("drop view if exists lib_demand_live cascade")
        for имя in ТАБЛИЦЫ_КОРПУСА:
            c.execute(f"drop table if exists {имя} cascade")
        c.execute("drop function if exists lib_pn_key(text) cascade")

    снести()
    c.execute(КОРПУС)
    yield c
    снести()
    c.close()
    conn.close()


def прогон(м, **среда):
    """Гоняет скрипт целиком — от подключения до записи точки."""
    import contextlib
    import io
    старое = dict(os.environ)
    os.environ.update(SUPABASE_DB_URL=DSN, **среда)
    os.environ.pop("GITHUB_RUN_ID", None)
    try:
        буфер = io.StringIO()
        with contextlib.redirect_stdout(буфер):
            код = м.main()
        return код, буфер.getvalue()
    finally:
        os.environ.clear()
        os.environ.update(старое)


def test_точка_истории_не_пишется_без_разрешения(корпус_в_public):
    """Замер по умолчанию ничего не меняет: WRITE — осознанное включение."""
    м = скрипт()
    код, вывод = прогон(м)
    assert код == 0
    assert точки(корпус_в_public) == [], "замер записал точку, хотя WRITE не задан"
    assert "НЕ записана" in вывод


def test_точка_истории_повторяет_напечатанные_числа(корпус_в_public):
    """Цифра в истории обязана равняться напечатанной.

    Соблазн посчитать историю вторым проходом запросов велик, а цена —
    расхождение: между проходами идёт разбор, и точка разойдётся с выводом.
    """
    м = скрипт()
    код, вывод = прогон(м, WRITE="1", RUN_KEY="прогон-теста")
    assert код == 0
    (ключ, числа, оговорка), = точки(корпус_в_public)
    assert ключ == "прогон-теста"
    assert оговорка is None
    # Ровно те пять чисел, что напечатаны главным ответом.
    assert числа["asked"] == 5
    assert числа["with_kp"] == 1
    assert числа["other_feed"] == 1
    assert числа["no_price"] == 3
    assert числа["plausible"] == 4
    # И те, что напечатаны обратной стороной и каталогом.
    assert числа["price_codes"] == 2
    assert числа["price_asked"] == 1
    assert числа["price_not_asked"] == 1
    assert числа["catalog"] == 2
    assert числа["catalog_priced"] == 1
    # Напечатанное и записанное — одно и то же: ищем те же числа в выводе.
    assert "         5" in вывод and "         3" in вывод


def test_повторный_прогон_тем_же_ключом_не_плодит_точки(корпус_в_public):
    """Иначе одна перезапущенная задача Actions рисует на графике ступеньку."""
    м = скрипт()
    прогон(м, WRITE="1", RUN_KEY="прогон-теста")
    прогон(м, WRITE="1", RUN_KEY="прогон-теста", NOTE="со второго раза")
    строки = точки(корпус_в_public)
    assert len(строки) == 1, "перезапуск прогона добавил вторую точку"
    assert строки[0][2] == "со второго раза", "перезапись не обновила оговорку"


def test_в_истории_только_числа(корпус_в_public):
    """Таблица публичная по назначению: наименованию позиции в ней места нет.

    Проверяется не отсутствие конкретного слова, а ТИП каждого значения: строка
    в nums — это уже утечка, чем бы она ни была.
    """
    м = скрипт()
    прогон(м, WRITE="1", RUN_KEY="прогон-теста")
    (_, числа, _), = точки(корпус_в_public)
    assert числа, "точка пуста"
    for имя, значение in числа.items():
        assert isinstance(значение, int), f"{имя} = {значение!r} — не число"
        assert имя.replace("_", "").isalnum() and имя.isascii(), имя


def test_ключ_прогона_берётся_из_actions_а_иначе_из_времени():
    """Номер прогона нужен, чтобы точку можно было найти в логах."""
    м = скрипт()
    старое = dict(os.environ)
    try:
        os.environ.pop("RUN_KEY", None)
        os.environ["GITHUB_RUN_ID"] = "123456"
        assert м.ключ_прогона() == "123456"
        os.environ["RUN_KEY"] = "явный"
        assert м.ключ_прогона() == "явный", "RUN_KEY должен перебивать прогон Actions"
        os.environ.pop("RUN_KEY")
        os.environ.pop("GITHUB_RUN_ID")
        ключ = м.ключ_прогона()
        assert ключ.startswith("вручную-") and ключ.endswith("Z"), ключ
    finally:
        os.environ.clear()
        os.environ.update(старое)
