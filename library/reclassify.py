#!/usr/bin/env python3
"""Переклассификация уже разобранной номенклатуры: позиции без сегмента.

ЗАЧЕМ. При разборе вложений сегмент позиции определялся по тексту её строки, а
если он не определился — наследовался от файла целиком. Когда ни строка, ни файл
не дали совпадения, позиция легла в lib_demand с пустым segment_id. Такие позиции
в базе есть, но ни в один разрез по оборудованию не попадают: в аналитике их
как будто нет.

Скрипт разбирает этот остаток тремя правилами, от надёжного к слабому:

  1. СЛОВАРЬ. Классифицируем заново по трём колонкам сразу — наименование,
     изготовитель, парт-номер. При разборе смотрели только на строку таблицы:
     если марка стояла в отдельной колонке «Производитель», она в текст не
     попадала. Это самое надёжное правило, работает на самой позиции.
  0. ПРОЗА НЕ УЧАСТВУЕТ. Строки, помеченные разметкой (library/mark_prose.py) как
     текст тендерного документа, исключены из правил наследования: 98,8% сделок
     имеют классифицированного соседа, и наследование залило бы сегменты
     оборудования текстом договоров. Но правило словаря их всё-таки смотрит —
     иначе пометка стала бы необратимой: когда словарь дорастёт до крепежа,
     ошибочно помеченная строка уже не была бы подобрана. Сработал словарь —
     пометка снимается (revoked_at), а не удаляется.
  2. ПО ФАЙЛУ. Позиция берёт сегмент большинства своих соседей по тому же файлу.
     Спецификация — документ на одну поставку: если 80 строк из 100 распознались
     как насосы, оставшиеся 20 — это крепёж и уплотнения к тем же насосам, а не
     другой сегмент. Правило применяется, только если большинство уверенное
     (доля лидера не ниже MIN_SHARE).
  3. ПО СДЕЛКЕ. То же самое на уровне сделки — для файлов, где не распозналось
     вообще ничего. Правило слабее: в одной сделке бывает разнородная закупка,
     поэтому порог доли выше и правило включается отдельно (RULE_DEAL=1).

ПОВТОРНЫЙ ПРОГОН. Каждый следующий прогон видит уже проставленные сегменты, и
правило большинства начинает срабатывать там, где в прошлый раз распознанных
соседей не хватало. Это нормально, но означает, что наследование расширяется
шагами: прогоняйте сначала вхолостую и смотрите, сколько и каким правилом
добавится, прежде чем писать.

БЕЗОПАСНОСТЬ. Скрипт только проставляет сегмент там, где его нет: он не меняет
уже заполненный segment_id, ничего не удаляет и не трогает другие колонки.
Без APPLY=1 запускается вхолостую — считает и печатает, но не пишет.
В журнал идут только агрегаты: ни наименований, ни номеров сделок.

    SUPABASE_DB_URL=... python library/reclassify.py            # вхолостую
    SUPABASE_DB_URL=... APPLY=1 python library/reclassify.py    # с записью
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segments import SEGMENTS, classify, name_of  # noqa: E402  (после sys.path)

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
RULE_DEAL = os.environ.get("RULE_DEAL", "") not in ("", "0", "false")
BATCH = int(os.environ.get("BATCH", "5000"))

# Доля сегмента-лидера среди распознанных соседей, ниже которой наследование
# не применяется: смешанная спецификация лучше останется без сегмента, чем
# уедет в чужой.
MIN_SHARE_FILE = float(os.environ.get("MIN_SHARE_FILE", "0.6"))
MIN_SHARE_DEAL = float(os.environ.get("MIN_SHARE_DEAL", "0.75"))

# Помеченная проза приходит с признаком junk: правило словаря её смотрит,
# правила наследования — нет.
UNSEGMENTED = ("select d.id, d.item_name, d.oem, d.part_number, d.source_file, d.deal_id, "
               "(j.demand_id is not null) as junk "
               "from lib_demand d "
               "left join lib_row_junk j on j.demand_id = d.id and j.revoked_at is null "
               "where d.segment_id is null")
UNSEGMENTED_PLAIN = ("select d.id, d.item_name, d.oem, d.part_number, d.source_file, d.deal_id, "
                     "false as junk from lib_demand d where d.segment_id is null")

# Снятие ошибочной пометки: строку узнал словарь, значит это номенклатура.
UNMARK_SQL = ("update lib_row_junk set revoked_at = now(), revoked_by = 'словарь' "
              "where demand_id = any(%s) and revoked_at is null")

# Большинство по файлу и по сделке считаем одним запросом на группу, а не по
# строке: строк — сотни тысяч, запрос на каждую превратил бы прогон в часы.
MAJORITY_SQL = """
select d.{key}, d.segment_id, count(*)
from lib_demand d
left join lib_row_junk j on j.demand_id = d.id and j.revoked_at is null
where d.segment_id is not null and d.{key} is not null and btrim(d.{key}) <> ''
  and j.demand_id is null
group by 1, 2"""
MAJORITY_SQL_PLAIN = """
select d.{key}, d.segment_id, count(*)
from lib_demand d
where d.segment_id is not null and d.{key} is not null and btrim(d.{key}) <> ''
group by 1, 2"""


def majority(pairs, min_share: float) -> dict[str, str]:
    """Сегмент-лидер по каждой группе, если его доля не ниже порога."""
    per: dict[str, Counter] = {}
    for key, sid, n in pairs:
        per.setdefault(key, Counter())[sid] += n
    out = {}
    for key, c in per.items():
        sid, n = c.most_common(1)[0]
        if n / sum(c.values()) >= min_share:
            out[key] = sid
    return out


def write(cur, updates: list[tuple[str, str, str, int]]) -> None:
    """Проставить сегмент пачкой. Условие segment_id is null — страховка от
    гонки: если параллельный прогон уже проставил сегмент, мы его не затираем.
    Колонки segment_rule и segment_run делают наследование обратимым: откат —
    по значению segment_run, команда приведена в шапке library/mark_prose.py."""
    import psycopg2.extras
    psycopg2.extras.execute_values(
        cur,
        "update lib_demand d set segment_id = v.sid::text, segment_rule = v.rule::text, "
        "segment_run = v.run::text from (values %s) as v(sid, rule, run, id) "
        "where d.id = v.id::bigint and d.segment_id is null",
        updates, page_size=1000)


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2

    # statement_timeout — в строке подключения: SET внутри транзакции откатится
    # вместе с ней, а дефолт пула Supabase в две минуты уже ронял прогон миграций.
    conn = psycopg2.connect(url, connect_timeout=20,
                            options="-c statement_timeout=900000")
    conn.autocommit = False
    run_id = "reclass-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    print(f"режим: {'запись в базу' if APPLY else 'вхолостую, без записи'}", flush=True)

    with conn.cursor() as cur:
        cur.execute("select count(*) from lib_demand")
        total = cur.fetchone()[0]
        cur.execute("select count(*) from lib_demand where segment_id is null")
        blank = cur.fetchone()[0]
        print(f"позиций всего: {total} · без сегмента: {blank} "
              f"({blank / total * 100:.1f}%)" if total else "база пуста", flush=True)
        if not blank:
            print("нечего переклассифицировать")
            return 0

        # До применения миграции таблицы пометок ещё нет — работаем по-старому.
        cur.execute("select to_regclass('public.lib_row_junk') is not null")
        junk_ready = bool(cur.fetchone()[0])
        maj_sql = MAJORITY_SQL if junk_ready else MAJORITY_SQL_PLAIN
        if not junk_ready:
            print("таблицы пометок ещё нет — проза не исключается", flush=True)

        cur.execute(maj_sql.format(key="source_file"))
        by_file = majority(cur.fetchall(), MIN_SHARE_FILE)
        by_deal = {}
        if RULE_DEAL:
            cur.execute(maj_sql.format(key="deal_id"))
            by_deal = majority(cur.fetchall(), MIN_SHARE_DEAL)
        print(f"файлов с уверенным большинством: {len(by_file)}"
              + (f" · сделок: {len(by_deal)}" if RULE_DEAL else ""), flush=True)

        cur.execute(UNSEGMENTED if junk_ready else UNSEGMENTED_PLAIN)
        rule: Counter = Counter()
        segs: Counter = Counter()
        batch: list[tuple[str, str, str, int]] = []
        unmark: list[int] = []
        unmarked = 0
        skipped_junk = 0
        seen = 0
        while True:
            chunk = cur.fetchmany(BATCH)
            if not chunk:
                break
            for rid, name, oem, pn, src, deal, junk in chunk:
                seen += 1
                sid = classify(" ".join(x for x in (name, oem, pn) if x))
                fired = "словарь"
                if sid:
                    rule["1 · словарь по наименованию, изготовителю и номеру"] += 1
                    if junk:
                        # Словарь узнал помеченную строку — значит пометка ошибочна.
                        unmark.append(rid)
                        unmarked += 1
                elif junk:
                    # Наследование по прозе не применяется ни при каких условиях.
                    skipped_junk += 1
                    rule["не разобрано"] += 1
                    continue
                elif src and src in by_file:
                    sid = by_file[src]
                    fired = "файл"
                    rule["2 · большинство по файлу"] += 1
                elif deal and deal in by_deal:
                    sid = by_deal[deal]
                    fired = "сделка"
                    rule["3 · большинство по сделке"] += 1
                else:
                    rule["не разобрано"] += 1
                    continue
                segs[sid] += 1
                batch.append((sid, fired, run_id, rid))
            if APPLY and batch:
                with conn.cursor() as w:
                    write(w, batch)
                    if unmark:
                        w.execute(UNMARK_SQL, (unmark,))
                conn.commit()
                unmark = []
            batch = []
            print(f"  просмотрено {seen} из {blank}", flush=True)

    resolved = sum(v for k, v in rule.items() if k != "не разобрано")
    print("\n=== ИТОГ ===")
    print(f"просмотрено позиций без сегмента: {seen}")
    print(f"сегмент определён: {resolved} ({resolved / seen * 100:.1f}%)" if seen else "")
    for k, v in rule.most_common():
        print(f"    {k:52s} {v:>9d}")
    print(f"исключено из наследования как текст документа: {skipped_junk}")
    print(f"пометок, ошибочность которых показал словарь: {unmarked}")
    print("распределение по сегментам:")
    for sid, n in segs.most_common():
        print(f"    {name_of(sid):32s} {n:>9d}")
    if not APPLY:
        print("\nэто был прогон вхолостую — в базе ничего не изменилось. "
              "Для записи: APPLY=1")
    conn.close()
    return 0


if __name__ == "__main__":
    assert SEGMENTS, "словарь сегментов пуст"
    sys.exit(main())
