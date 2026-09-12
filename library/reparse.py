#!/usr/bin/env python3
"""Переразбор вложений новым разборщиком: возврат потерянного спроса.

ЗАЧЕМ. Правки разборщика (#233) действуют только на новые файлы. Между тем
накопленные 9 920 файлов xlsx/docx разбирались кодом, в котором .docx склеивался
в ОДНУ строку: `text_from_docx` соединяет все <w:t> пробелом, `splitlines()` даёт
один элемент, и вся таблица спецификации превращалась в одну строку lib_demand.
Пока файлы не разобраны заново, возвращённые таблицы .docx не дают ничего.

ЧТО ДЕЛАЕТ. Разбирает файл заново текущим разборщиком и сравнивает с тем, что
лежит в базе. В холостом режиме только считает: сколько строк было, сколько
станет, у скольких файлов результат изменился. Запись — по файлу в одной
транзакции: старые строки помечаются в lib_row_junk как «переразбор», новые
вставляются. Пометка обратима, старые строки не удаляются.

ПОЧЕМУ ПОМЕТКА, А НЕ УДАЛЕНИЕ. Новый разбор может оказаться хуже старого —
например, ворота спецификации отсекут файл, который на самом деле был
спецификацией. Откат по run_id возвращает прежнее состояние целиком.

ПОРЯДОК. Сначала холостой прогон с LIMIT: он даёт измеренную оценку прироста на
выборке, а не догадку. Только потом полный прогон с записью.

    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... LIMIT=200 python library/reparse.py
    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... APPLY=1 python library/reparse.py

В журнал идут только агрегаты.
"""
from __future__ import annotations

import hashlib
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indexer  # noqa: E402

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
SHARDS = int(os.environ.get("SHARDS", "1"))
SHARD = int(os.environ.get("SHARD", "0"))
WORKERS = int(os.environ.get("WORKERS", "8"))
LIMIT = int(os.environ.get("LIMIT", "0"))
DAYS = int(os.environ.get("DAYS", "400"))
# Какие форматы переразбирать. По умолчанию только то, где правка даёт эффект:
# .docx терял таблицы целиком. Расширять — осознанно и с новым измерением.
KINDS = tuple(k.strip() for k in os.environ.get("KINDS", "xlsx/docx").split(",") if k.strip())
RULE = "переразбор v2"

CANDIDATES = """
select f.file_id, f.kind, coalesce(f.rows_found, 0)
  from lib_files f
 where f.status = 'разобран'
   and coalesce(f.parser_version, 1) < %s
   and f.kind = any(%s)"""

# Индекс по source_file обязателен: без него выборка старых строк файла — это
# последовательный проход по полутора миллионам строк на каждый из тысяч файлов
# (CLAUDE.md, правило 8). Скрипт отказывается работать без него.
INDEX_CHECK = """
select count(*) from pg_indexes
 where tablename = 'lib_demand' and indexdef like '%%source_file%%'"""

OLD_ROWS = "select id from lib_demand where source_file = %s"
MARK = ("insert into lib_row_junk (demand_id, rule, run_id, marks) "
        "select unnest(%s::bigint[]), %s, %s, 'переразбор' "
        "on conflict (demand_id) do nothing")


def num(v, w=10):
    return f"{v:,}".replace(",", " ").rjust(w)


def main() -> int:
    for var in ("BITRIX_WEBHOOK_URL", "SUPABASE_DB_URL"):
        if not os.environ.get(var):
            print(f"нет переменной {var}", file=sys.stderr)
            return 2
    import psycopg2
    import psycopg2.extras

    run_id = "reparse-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if SHARDS > 1:
        print(f"часть {SHARD + 1} из {SHARDS}", flush=True)
    print(f"режим: {'ЗАПИСЬ В БАЗУ' if APPLY else 'холостой, без записи'} · прогон {run_id}")
    print(f"форматы к переразбору: {', '.join(KINDS)}", flush=True)

    conn = indexer.connect()
    with conn.cursor() as cur:
        cur.execute(INDEX_CHECK)
        if not cur.fetchone()[0]:
            print("нет индекса lib_demand(source_file): выборка старых строк файла превратится "
                  "в проход по всей таблице. Примените миграцию перед переразбором.",
                  file=sys.stderr)
            conn.close()
            return 2
        cur.execute(CANDIDATES, (indexer.PARSER_VERSION, list(KINDS)))
        было = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    conn.close()
    print(f"кандидатов на переразбор: {len(было)}", flush=True)
    if not было:
        print("нечего переразбирать")
        return 0

    корзины = Counter()
    for _kind, n in было.values():
        корзины["0–1 строка" if n <= 1 else "2–9" if n < 10 else "10–99" if n < 100 else "100+"] += 1
    print("сколько позиций эти файлы дали прежним разбором:")
    for k in ("0–1 строка", "2–9", "10–99", "100+"):
        if корзины[k]:
            print(f"    {k:12}{num(корзины[k])} файлов")

    refs = indexer.collect_refs(DAYS)
    mine = [r for r in refs
            if str(r["fo"].get("id") or r["fo"].get("ID")) in было
            and int(hashlib.sha1(str(r["fo"].get("id") or r["fo"].get("ID")).encode()).hexdigest(), 16)
            % SHARDS == SHARD]
    if LIMIT:
        mine = mine[:LIMIT]
    print(f"\nк переразбору в этой части: {len(mine)}\n", flush=True)
    if not mine:
        print("нечего делать")
        return 0

    стат: Counter = Counter()
    было_строк = стало_строк = 0
    лучше = хуже = так_же = 0
    записано_файлов = 0

    def запиши(rec, items):
        """Один файл — одна транзакция: пометить старое, вставить новое."""
        c = indexer.connect()
        try:
            with c.cursor() as cur:
                cur.execute(OLD_ROWS, (rec["file_id"],))
                старые = [r[0] for r in cur.fetchall()]
                if старые:
                    cur.execute(MARK, (старые, RULE, run_id))
                if items:
                    psycopg2.extras.execute_values(cur, """
                        insert into lib_demand
                          (segment_id, deal_id, item_name, oem, part_number, qty, unit, source,
                           source_file, segment_rule)
                        values %s""",
                        [(it["segment_id"], it["deal_id"], indexer.pg(it["item_name"])[:500],
                          indexer.pg(it.get("oem"))[:200], indexer.pg(it.get("part_number"))[:120],
                          it.get("qty"), indexer.pg(it.get("unit"))[:40],
                          "спецификация сделки", it["source_file"], it.get("segment_rule"))
                         for it in items], page_size=500)
                cur.execute("""
                    update lib_files set status = %s, rows_found = %s, chars = %s,
                           segment_id = %s, parse_path = %s, header_found = %s,
                           doc_class = %s, class_rule = %s, text_lines = %s, item_lines = %s,
                           parser_version = %s, reason = %s, processed_at = now()
                     where file_id = %s""",
                    (rec["status"], rec["rows_found"], rec["chars"], rec["segment_id"],
                     rec["parse_path"], rec["header_found"], rec["doc_class"], rec["class_rule"],
                     rec["text_lines"], rec["item_lines"], indexer.PARSER_VERSION,
                     indexer.pg(rec["reason"]), rec["file_id"]))
            c.commit()
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, (rec, items) in enumerate(pool.map(indexer.handle, mine), 1):
            fid = rec["file_id"]
            старое = было.get(fid, (None, 0))[1]
            новое = len(items)
            стат[rec["status"]] += 1
            было_строк += старое
            стало_строк += новое
            if новое > старое:
                лучше += 1
            elif новое < старое:
                хуже += 1
            else:
                так_же += 1
            if APPLY:
                запиши(rec, items)
                записано_файлов += 1
            if n % 100 == 0:
                print(f"  обработано {n} из {len(mine)} · было {было_строк} · стало {стало_строк}",
                      flush=True)

    print("\n=== ИТОГ ===")
    print(f"файлов переразобрано:{num(len(mine))}")
    print(f"позиций было:        {num(было_строк)}")
    print(f"позиций стало:       {num(стало_строк)}")
    дельта = стало_строк - было_строк
    print(f"разница:             {num(дельта)}   "
          f"{'прирост' if дельта > 0 else 'убыль' if дельта < 0 else 'без изменений'}")
    print(f"\nфайлов стало лучше: {лучше} · хуже: {хуже} · без изменений: {так_же}")
    print(f"по состоянию: {dict(стат.most_common())}")
    if LIMIT and len(было) > len(mine):
        на_файл = дельта / len(mine) if mine else 0
        print(f"\nоценка на весь объём ({len(было)} файлов): "
              f"{int(на_файл * len(было)):+,} позиций".replace(",", " "))
        print("оценка линейная — выборка не случайна, а идёт в порядке обхода Битрикса")
    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
    else:
        print(f"\n✓ записано файлов: {записано_файлов} · откат: REVERT={run_id} у mark_prose.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
