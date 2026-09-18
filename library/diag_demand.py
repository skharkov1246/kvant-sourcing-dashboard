#!/usr/bin/env python3
"""Из чего состоят позиции без сегмента. Разведка перед расширением словаря.

ЗАЧЕМ. Первый прогон переклассификации (11.09.2026) разобрал 1 769 позиций из
334 811 — полпроцента. Правило «большинство по файлу» не сработало ни разу, хотя
должно было: значит предположение о том, как устроен остаток, неверно, и городить
следующее правило вслепую бессмысленно. Этот скрипт отвечает на четыре вопроса
фактами:

  • это вообще номенклатура? — сколько строк с пустым или обрубком наименования,
    сколько без парт-номера и без изготовителя;
  • есть ли за что зацепиться через соседей? — сколько файлов и сделок с такими
    строками содержат хоть одну уже классифицированную строку (если ноль, правила
    наследования бесполезны в принципе, и дальше их чинить не надо);
  • чего не хватает словарю? — самые частые русские слова в наименованиях без
    сегмента: это готовый материал для пополнения SEGMENTS;
  • где они лежат? — распределение по форматам исходных файлов.

ЧТО ПЕЧАТАЕТ. Счётчики и частотный список отдельных слов. Слово попадает в
список, только если оно русское, не короче шести букв и встречается не реже
MIN_FREQ раз во всей выборке. Названия компаний, парт-номера, суммы и фамилии
такой фильтр не проходят: они либо не кириллица, либо встречаются единицами.
Ни одного наименования целиком, ни одного номера сделки в выводе нет.

    SUPABASE_DB_URL=... python library/diag_demand.py              # спрос без прозы
    SUPABASE_DB_URL=... SOURCE=junk python library/diag_demand.py  # то, что помечено

ДВА СПИСКА РЯДОМ — самая наглядная проверка разметки без показа содержимого:
слева должно остаться «уплотнение, прокладка, манжета, болт», справа —
«поставки, закупочной, договора, процедуры».
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segments import SEGMENTS  # noqa: E402  (после sys.path)

MIN_LEN = int(os.environ.get("MIN_LEN", "6"))
MIN_FREQ = int(os.environ.get("MIN_FREQ", "0"))     # 0 — считать от размера выборки
SOURCE = os.environ.get("SOURCE", "live")           # live | junk
TOP = int(os.environ.get("TOP", "60"))

# Только кириллица: латиница — это марки и парт-номера, им в частотном списке
# не место (и в публичный журнал они попасть не должны).
WORD = re.compile(r"[а-яё]{%d,}" % MIN_LEN)

SHAPE_SQL = """
select count(*)                                                         as всего,
       count(*) filter (where coalesce(btrim(item_name), '') = '')      as без_наименования,
       count(*) filter (where length(btrim(item_name)) < 6)             as наименование_короче_6,
       count(*) filter (where coalesce(btrim(part_number), '') <> '')   as с_парт_номером,
       count(*) filter (where coalesce(btrim(oem), '') <> '')           as с_изготовителем,
       count(distinct source_file)                                      as файлов,
       count(distinct deal_id)                                          as сделок
from {src} d where d.segment_id is null"""

# Ключевой вопрос: есть ли у безсегментной строки классифицированные соседи.
NEIGHBOURS_SQL = """
with пустые as (select distinct {key} as k from {src} where segment_id is null and {key} is not null),
     полные as (select distinct {key} as k from {src} where segment_id is not null and {key} is not null)
select (select count(*) from пустые),
       (select count(*) from пустые join полные using (k))"""

KINDS_SQL = """
select coalesce(f.kind, '—'), count(*)
from {src} d join lib_files f on f.file_id = d.source_file
where d.segment_id is null group by 1 order by 2 desc"""

NAMES_SQL = "select item_name from lib_demand_src where segment_id is null and item_name is not null"


def source_table(cur) -> tuple[str, str]:
    """Откуда брать выборку. До применения миграции представления нет — разведка
    обязана работать и тогда."""
    cur.execute("select to_regclass('public.lib_demand_live') is not null")
    has_view = bool(cur.fetchone()[0])
    if SOURCE == "junk":
        if not has_view:
            return "lib_demand", "вся база (таблицы пометок ещё нет)"
        return ("(select d.* from lib_demand d join lib_row_junk j on j.demand_id = d.id "
                "and j.revoked_at is null)"), "помеченное как текст документа"
    if has_view:
        return "lib_demand_live", "спрос без помеченного текста документов"
    return "lib_demand", "вся база (разметка ещё не применялась)"


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2

    known = {w for _n, words in SEGMENTS.values() for w in words}
    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=900000")
    conn.autocommit = True
    with conn.cursor() as cur:
        src, what_src = source_table(cur)
        print(f"источник: {what_src}")
        print("\n=== что это за строки ===")
        cur.execute(SHAPE_SQL.format(src=src))
        labels = ("позиций без сегмента", "без наименования", "наименование короче 6 знаков",
                  "с парт-номером", "с изготовителем", "разных файлов", "разных сделок")
        for label, value in zip(labels, cur.fetchone()):
            print(f"  {label:32}{value:>12,}".replace(",", " "))

        print("\n=== есть ли классифицированные соседи ===")
        for key, what in (("source_file", "файлов"), ("deal_id", "сделок")):
            cur.execute(NEIGHBOURS_SQL.format(key=key, src=src))
            всего, общих = cur.fetchone()
            доля = общих / всего * 100 if всего else 0
            print(f"  {what} с безсегментными строками: {всего:,}".replace(",", " "))
            print(f"    из них содержат хоть одну строку с сегментом: {общих:,} ({доля:.1f}%)".replace(",", " "))

        print("\n=== в каких файлах лежат ===")
        cur.execute(KINDS_SQL.format(src=src))
        for kind, n in cur.fetchall():
            print(f"  {kind:24}{n:>12,}".replace(",", " "))

        # Порог частоты — от размера выборки, а не абсолютный: после разметки
        # выборка сожмётся, и «от двухсот раз» перестанет быть защитой — редкое
        # слово может оказаться названием завода или города.
        cur.execute(f"select count(*) from {src} d where d.segment_id is null")
        sample_rows = cur.fetchone()[0] or 0
        floor = MIN_FREQ or max(200, sample_rows // 1000)
        print(f"\n=== частые слова, которых нет в словаре (от {MIN_LEN} букв, от {floor} раз) ===")
        cur.execute(NAMES_SQL.replace("lib_demand_src", src))
        freq: Counter = Counter()
        while True:
            chunk = cur.fetchmany(20000)
            if not chunk:
                break
            for (name,) in chunk:
                freq.update(set(WORD.findall(name.lower().replace("ё", "е"))))
        новые = [(w, n) for w, n in freq.most_common()
                 if n >= floor and not any(k in w or w in k for k in known)]
        print(f"  разных слов всего: {len(freq):,}".replace(",", " "))
        for w, n in новые[:TOP]:
            print(f"    {w:28}{n:>10,}".replace(",", " "))
        if not новые:
            print("    таких слов нет — словарю нечего добавить, остаток не номенклатура")
    conn.close()
    print("\n✓ разведка окончена")
    return 0


if __name__ == "__main__":
    sys.exit(main())
