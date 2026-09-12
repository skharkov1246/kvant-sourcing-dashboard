#!/usr/bin/env python3
"""Сводка по базе знаний: сколько чего накоплено. Только агрегаты.

ЗАЧЕМ. Ответ на вопрос «насколько выросла база» до этого собирался разбором
журналов прогонов: цифры приходилось складывать вручную по двенадцати частям
индексатора, а часть показателей (сколько позиций осталось без сегмента, сколько
разных парт-номеров, наполняются ли lib_suppliers и lib_prices) из журналов не
видна вовсе. Этот скрипт спрашивает у базы напрямую.

ЧТО ПЕЧАТАЕТ. Только count, count(distinct) и названия сегментов. Ни одного
наименования позиции, ни одной почты, ни одного куска текста заметки: журнал
прогона в публичном репозитории видит кто угодно, а в базе лежат коммерческие
данные заказчиков. Ровно по этой причине запросы ниже написаны так, что вернуть
содержимое строки они физически не могут.

    SUPABASE_DB_URL=... python scripts/library_stats.py
    Actions → «Библиотека — сводка и переклассификация» → job: stats
"""
from __future__ import annotations

import os
import sys

# Таблицы библиотеки и правок. Порядок — от самых наполненных к служебным.
TABLES = [
    ("lib_demand", "спрос: позиции спецификаций из файлов сделок"),
    ("lib_files", "реестр разобранных вложений Битрикса"),
    ("lib_knowledge", "статьи: устройство, подбор, отказы, аналоги"),
    ("lib_suppliers", "поставщики по сегментам"),
    ("lib_prices", "цены с базисом и источником"),
    ("lib_losses", "разбор проигранных сделок"),
    ("lib_segments", "справочник сегментов"),
    ("lib_row_junk", "пометки «это не номенклатура, а текст документа»"),
    ("lib_mark_runs", "журнал прогонов разметки"),
    ("gt_notes", "правки инженеров в библиотеке ГТУ"),
    ("lib_parts", "каталог деталей: номер, изготовитель, узел"),
    ("lib_part_models", "ребро «деталь → машина»"),
    ("lib_part_suppliers", "ребро «деталь → исполнитель», наличие и срок"),
    ("lib_part_alt", "взаимозаменяемость: чей это номер и чем заменить"),
    ("lib_models", "справочник машин с написаниями"),
    ("lib_units", "узлы деревом: система → компонент"),
    ("lib_symptoms", "признаки: по чему видно неисправность"),
    ("lib_procedures", "инспекции, контроль, ремонт, покрытия"),
    ("lib_defects", "дефекты с последствием и решением"),
    ("lib_symptom_ops", "ребро «признак → чем подтвердить»"),
    ("lib_symptom_defects", "ребро «признак → дефект»"),
    ("lib_defect_ops", "ребро «дефект → чем лечить»"),
    ("lib_bom", "ведомости состава машин"),
    ("lib_fleet", "парк: какая машина где стоит"),
]

# Заполняемость цепочки портала по живой базе. Считается одним запросом на
# звено, и каждое число — ответ на вопрос «чего не хватает», а не «сколько
# всего»: по нему берётся следующая работа (CLAUDE.md, «Куда мы идём»).
CHAIN_SQL = """
select 'машина'             as звено,
       (select count(*) from lib_models)                                       as всего,
       (select count(*) from lib_models where segment_id is not null)           as с_направлением
union all
select 'узел',
       (select count(*) from lib_units),
       (select count(*) from lib_units where crit is not null)
union all
select 'признак',
       (select count(*) from lib_symptoms),
       (select count(*) from lib_symptoms s where {признак_ведёт_дальше})
union all
select 'дефект',
       (select count(*) from lib_defects),
       (select count(*) from lib_defects d where {дефект_ведёт_дальше})
union all
select 'ремонтное решение',
       (select count(*) from lib_procedures),
       (select count(*) from lib_procedures where performer_key is not null)
union all
select 'запчасть',
       (select count(*) from lib_parts),
       (select count(*) from lib_parts where unit_id is not null)
union all
select 'исполнитель',
       (select count(*) from lib_suppliers),
       (select count(*) from lib_suppliers where contact_email is not null)"""

# Чего не хватает деталям каталога — прямой список работ.
GAPS_SQL = """
select (select count(*) from lib_parts where unit_id is null)                   as без_узла,
       (select count(*) from lib_parts p where not exists
          (select 1 from lib_part_models m where m.part_id = p.id))              as без_машины,
       (select count(*) from lib_parts p where not exists
          (select 1 from lib_part_suppliers s where s.part_id = p.id))           as без_исполнителя,
       (select count(*) from lib_parts p where not exists
          (select 1 from lib_prices pr where pr.part_id = p.id))                 as без_цены,
       (select count(*) from lib_knowledge where unit_id is null)                as статей_без_узла,
       (select count(*) from lib_symptoms s where not ({признак_ведёт_дальше}))  as признаков_без_дефекта,
       (select count(*) from lib_defects d where not ({дефект_ведёт_дальше}))     as дефектов_без_решения,
       (select count(*) from lib_procedures where duration is null)              as операций_без_срока"""

# Проверка цепочки на конкретных номерах. Оба номера — публичные каталожные
# обозначения изготовителей, они и так опубликованы в репозитории: MW21215M
# (камера сгорания Siemens SGT-400, наша закупка) и 7W-4377 (катушка зажигания
# Caterpillar). Печатаются ТОЛЬКО числа: сколько машин, исполнителей, цен и
# замен нашлось. Это проверка того, что цепочка в живой базе действительно
# собирается, а не только в локальной копии.
ПРОБНЫЕ_НОМЕРА = ("mw21215m", "7w4377")
CHAIN_ONE_SQL = """
select (select count(*) from lib_parts where id = %(k)s)                          as деталь,
       (select count(*) from lib_part_models where part_id = %(k)s)                as машин,
       (select count(*) from lib_part_suppliers where part_id = %(k)s)             as исполнителей,
       (select count(*) from lib_prices where part_id = %(k)s)                     as цен,
       (select count(*) from lib_part_alt where part_id = %(k)s)                   as замен,
       (select count(*) from lib_bom where part_id = %(k)s)                        as в_ведомости"""

# Смычка спроса с каталогом: сколько строк спроса опознано по артикулу.
LINKED_SQL = """
select (select count(*) from lib_demand_catalog)                                 as строк_спроса,
       (select count(distinct part_id) from lib_demand_catalog)                   as деталей,
       (select count(distinct deal_id) from lib_demand_catalog)                   as сделок"""

DEMAND_SQL = """
select count(*)                                              as всего,
       count(segment_id)                                     as с_сегментом,
       count(*) - count(segment_id)                          as без_сегмента,
       count(distinct nullif(btrim(part_number), ''))        as парт_номеров,
       count(distinct nullif(btrim(oem), ''))                as изготовителей,
       count(distinct nullif(btrim(deal_id), ''))            as сделок,
       count(distinct nullif(btrim(source_file), ''))        as файлов_источников
from {src}"""

BY_SEGMENT_SQL = """
select coalesce(d.segment_id, '—') as sid, count(*) as n
from {src} d group by 1 order by 2 desc"""

# Разметка: сколько помечено, сколько снято, какими прогонами.
JUNK_SQL = """
select rule,
       count(*) filter (where revoked_at is null) as действует,
       count(*) filter (where revoked_at is not null) as снято
from lib_row_junk group by 1 order by 2 desc"""
RUNS_SQL = ("select run_id, mode, rows_marked, files_marked, reverted_at is not null "
            "from lib_mark_runs order by started_at desc limit 10")

FILES_SQL = "select status, count(*), coalesce(sum(rows_found), 0) from lib_files group by 1 order by 2 desc"
KINDS_SQL = "select coalesce(kind, '—'), count(*) from lib_files group by 1 order by 2 desc"

# Правки инженеров: сколько их, сколько авторов, по каким страницам.
# Текст заметок и почты авторов не запрашиваются — только счётчики.
NOTES_SQL = """
select count(*)                        as всего,
       count(*) filter (where removed) as снятых,
       count(distinct author)          as авторов,
       count(distinct scope)           as страниц
from gt_notes"""
NOTES_BY_SCOPE_SQL = "select scope, count(*) from gt_notes where not removed group by 1 order by 2 desc"


def table_exists(cur, name: str) -> bool:
    cur.execute("select to_regclass(%s) is not null", (f"public.{name}",))
    return bool(cur.fetchone()[0])


def one(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()


def rows(cur, sql: str):
    cur.execute(sql)
    return cur.fetchall()


def live(cur) -> str:
    """Имя источника спроса. До применения миграции представления ещё нет —
    сводка обязана работать и тогда, иначе её нельзя снять «до»."""
    cur.execute("select to_regclass('public.lib_demand_live') is not null")
    return "lib_demand_live" if cur.fetchone()[0] else "lib_demand"


def block(head: str) -> None:
    print(f"\n=== {head} ===", flush=True)


def num(value, width: int = 12) -> str:
    """Число с неразрывным пробелом в разряде тысяч.

    Форматируем именно число, а не готовую строку: замена запятой во всей строке
    съедала запятые в пояснениях («устройство, подбор, отказы» превращалось в
    «устройство  подбор  отказы»)."""
    return f"{value:,}".replace(",", " ").rjust(width)


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library"))
    from segments import name_of

    conn = psycopg2.connect(url, connect_timeout=20)
    conn.autocommit = True
    with conn.cursor() as cur:
        block("объём базы")
        print(f"{'таблица':16}{'строк':>12}  что внутри")
        present = {}
        for name, note in TABLES:
            if not table_exists(cur, name):
                print(f"{name:16}{'нет таблицы':>12}  {note}")
                continue
            n = one(cur, f"select count(*) from {name}")[0]
            present[name] = n
            print(f"{name:16}{num(n)}  {note}")

        src = live(cur)
        if present.get("lib_demand"):
            block("спрос: что именно у нас спрашивали"
                  + ("" if src == "lib_demand" else " (без помеченного текста документов)"))
            cols = ("всего позиций", "с сегментом", "без сегмента",
                    "разных парт-номеров", "разных изготовителей", "сделок", "файлов-источников")
            for label, value in zip(cols, one(cur, DEMAND_SQL.format(src=src))):
                print(f"  {label:24}{num(value)}")
            total = one(cur, f"select count(*) from {src}")[0]

            block("спрос по сегментам")
            print(f"  {'сегмент':34}{'позиций':>12}{'доля':>9}")
            for sid, n in rows(cur, BY_SEGMENT_SQL.format(src=src)):
                nm = name_of(None if sid == "—" else sid)
                print(f"  {nm:34}{num(n)}{n / total * 100:>8.1f}%")

        if present.get("lib_files"):
            block("вложения Битрикса")
            print(f"  {'состояние':28}{'файлов':>10}{'позиций из них':>16}")
            for status, n, found in rows(cur, FILES_SQL):
                print(f"  {status or '—':28}{num(n, 10)}{num(found, 16)}")
            print(f"  {'':28}{'':>10}")
            print(f"  {'формат':28}{'файлов':>10}")
            for kind, n in rows(cur, KINDS_SQL):
                print(f"  {kind:28}{num(n, 10)}")

        if present.get("lib_row_junk") is not None and "lib_row_junk" in present:
            block("разметка текста документов")
            for rule_name, act, rev in rows(cur, JUNK_SQL):
                print(f"  правило {rule_name:20}действует{num(act, 12)}   снято{num(rev, 10)}")
            for run_id, mode, marked, files_marked, reverted in rows(cur, RUNS_SQL):
                mark = " · ОТКАЧЕН" if reverted else ""
                print(f"  {run_id:26}{mode:12}строк{num(marked or 0, 12)}"
                      f"   файлов{num(files_marked or 0, 8)}{mark}")

        if present.get("lib_models") is not None:
            # Связи середины цепочки появились позже таблиц. Если миграция ещё
            # не применена, уточнение считается по старому признаку (узел у
            # признака, текст решения у дефекта), а сводка отвечает, а не падает.
            связи = {
                "признак_ведёт_дальше":
                    "exists (select 1 from lib_symptom_defects sd where sd.symptom_id = s.id)"
                    if table_exists(cur, "lib_symptom_defects") else "s.unit_id is not null",
                "дефект_ведёт_дальше":
                    "(d.fix is not null or exists "
                    "(select 1 from lib_defect_ops o where o.defect_id = d.id))"
                    if table_exists(cur, "lib_defect_ops") else "d.fix is not null",
            }
            block("заполняемость цепочки портала")
            print(f"  {'звено':22}{'записей':>10}{'из них с уточнением':>22}")
            for звено, всего, уточнено in rows(cur, CHAIN_SQL.format(**связи)):
                print(f"  {звено:22}{num(всего, 10)}{num(уточнено, 22)}")
            block("чего не хватает — это и есть следующая работа")
            подписи = ("деталей без узла", "деталей без машины", "деталей без исполнителя",
                       "деталей без цены", "статей разведки без узла",
                       "признаков без дефекта", "дефектов без решения",
                       "операций без срока")
            for label, value in zip(подписи, one(cur, GAPS_SQL.format(**связи))):
                print(f"  {label:28}{num(value)}")
            block("цепочка по пробному номеру (только числа)")
            print(f"  {'номер':12}{'деталь':>8}{'машин':>8}{'исполн.':>9}{'цен':>6}"
                  f"{'замен':>8}{'ведом.':>8}")
            for k in ПРОБНЫЕ_НОМЕРА:
                try:
                    cur.execute(CHAIN_ONE_SQL, {"k": k})
                    строка = cur.fetchone()
                except Exception:
                    continue
                значения = "".join(num(v, w) for v, w in
                                   zip(строка, (8, 8, 9, 6, 8, 8)))
                print(f"  {k:12}{значения}")
            if table_exists(cur, "lib_demand_catalog"):
                block("спрос, опознанный по каталогу")
                for label, value in zip(("строк спроса с известной деталью", "разных деталей",
                                         "сделок"), one(cur, LINKED_SQL)):
                    print(f"  {label:34}{num(value)}")

        if "gt_notes" in present:
            block("правки инженеров ГТУ")
            for label, value in zip(("всего правок", "снятых", "авторов", "страниц"), one(cur, NOTES_SQL)):
                print(f"  {label:24}{num(value)}")
            for scope, n in rows(cur, NOTES_BY_SCOPE_SQL):
                print(f"    страница {scope:20}{num(n, 8)}")
    conn.close()
    print("\n✓ сводка снята")
    return 0


if __name__ == "__main__":
    sys.exit(main())
