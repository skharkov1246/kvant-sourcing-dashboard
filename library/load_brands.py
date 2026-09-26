#!/usr/bin/env python3
"""Засев реестра брендов (lib_brands, lib_brand_alias), замер и откат. Этап 8.2.

ЧТО ДЕЛАЕТ. Правила — library/brand_registry.py, схема —
library/supabase/brands_schema.sql. Здесь — чтение источников, запись с
гейтами и печать агрегатов. Ступеней четыре, прогон ставит их по очереди
(.github/workflows/library-brands.yml):

  реестр     — бренды и написания из выверенных источников: dict/oem.json,
               zip/data/oem_atlas.json, OEM_ALIAS и справочник марок портала
               СП-176. Файлы каждая часть строит целиком и пишет свою долю (по
               ключу бренда); справочник портала часть читает только в своём
               диапазоне номеров (indexer.диапазон_части) — полного обхода в
               части нет (CLAUDE.md, «Битрикс не перегружать», п. 4);
  написания  — компании-изготовители lib_suppliers и написания из данных
               (lib_prices.oem, lib_demand.oem, lib_parts.oem): разрешаются по
               реестру базы, неразрешённые встают в очередь. Портал не читается;
  замер      — доля строк цены (три разреза) и спроса с разрешённым брендом,
               по словарю-файлу и по реестру базы рядом: видно, что дал реестр.
               Одна часть: запрос только читает;
  откат      — ROLLBACK=<run_id>.

ПО УМОЛЧАНИЮ ВХОЛОСТУЮ (CLAUDE.md, правило 3). Запись — APPLY=1, с ключом
прогона RUN_ID в каждой строке; запись отменяется сама, если не пройден хоть
один гейт: локаль базы складывает кириллицу (правило 21а), ключ написания в SQL
совпал с ключом в Python у каждой записанной строки, у каждого разрешённого
написания бренд есть в lib_brands.

ПОВТОРНЫЙ ЗАСЕВ ДУБЛЕЙ НЕ СОЗДАЁТ: бренд — первичный ключ, написание —
уникальное (источник, написание, место). Меняется строка в двух случаях —
была в очереди или спорной и разрешилась; либо это строка словаря-файла, и
суждение о ней в файле стало другим (запись стала указанием, «несколькими» или
описанием — library/oem_kind.py). Прежнее состояние сохраняется, и откат его
возвращает.

В ЖУРНАЛ — ТОЛЬКО АГРЕГАТЫ (правило 17): числа по источнику и статусу, доли по
разрезам, число групп расхождения правил. Ни имени бренда, ни написания.

    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… STAGE=реестр SHARDS=10 SHARD=0 \\
      python library/load_brands.py                # вхолостую
    … APPLY=1 RUN_ID=brands-123 python library/load_brands.py
    SUPABASE_DB_URL=… STAGE=замер python library/load_brands.py
    SUPABASE_DB_URL=… ROLLBACK=brands-123 python library/load_brands.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import brand_registry as br  # noqa: E402
from library import codes_sql  # noqa: E402

# Предел одного запроса — в строке подключения (правило 9). Замер соединяет
# 1,45 млн строк спроса с разобранными ячейками; пятнадцать минут — с запасом.
ПРЕДЕЛ_ЗАПРОСА_МС = 15 * 60 * 1000
# Блокировки не ждать дольше минуты: засев не должен вставать в очередь за
# чужим долгим оператором и держать за собой читателей (правило 12).
ПРЕДЕЛ_БЛОКИРОВКИ_МС = 60 * 1000
СП176 = 176
МЕТРИКА = "реестр_брендов"


def читать_json(путь: str):
    p = ROOT / путь
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def подключить(dsn: str):
    import psycopg2

    return psycopg2.connect(dsn, connect_timeout=20, options=(
        f"-c statement_timeout={ПРЕДЕЛ_ЗАПРОСА_МС} -c lock_timeout={ПРЕДЕЛ_БЛОКИРОВКИ_МС}"))


def есть_реестр(cur) -> bool:
    cur.execute(br.ЕСТЬ_РЕЕСТР_SQL)
    return bool(cur.fetchone()[0])


# ── Портал ───────────────────────────────────────────────────────────────────

def элементы_сп176(bx, shard: int, shards: int) -> tuple[list, tuple]:
    """Элементы справочника марок своей части: (номер, название).

    Диапазон номеров — indexer.диапазон_части: один запрос за наибольшим
    номером и страницы по ключу >id с start=-1, без подсчёта total. Всё — через
    BitrixClient: общий бюджет портала, ожидание на лимитах (CLAUDE.md,
    «Битрикс не перегружать»)."""
    from library import indexer

    res = bx.call("crm.item.list", {"entityTypeId": СП176, "order": {"id": "DESC"},
                                    "select": ["id"], "start": -1})
    items = (res or {}).get("items", []) if isinstance(res, dict) else []
    макс = int(items[0]["id"]) if items else 0
    низ, верх = indexer.диапазон_части(макс, shard, shards)
    out, last = [], низ
    while макс:
        f = {">id": last}
        if верх is not None:
            f["<=id"] = верх
        res = bx.call("crm.item.list", {"entityTypeId": СП176, "order": {"id": "ASC"},
                                        "filter": f, "select": ["id", "title"], "start": -1})
        items = (res or {}).get("items", []) if isinstance(res, dict) else []
        if not items:
            break
        out.extend((int(it["id"]), (it.get("title") or "").strip()) for it in items)
        last = int(items[-1]["id"])
        if len(items) < 50:
            break
    return out, (низ, верх, макс)


# ── Запись ───────────────────────────────────────────────────────────────────

ВСТАВКА_БРЕНДОВ = """
insert into lib_brands (brand_key, name, owner, former_names, country, sources, rule, run_id)
values %s
on conflict (brand_key) do nothing
returning brand_key
"""

# Строка меняется из очереди в разрешённое; прежнее — в prev_*, откат вернёт
# его. Решения людей («проверено», «отклонено») засев не трогает.
#
# СТРОКА СЛОВАРЯ-ФАЙЛА ПОВТОРЯЕТ ФАЙЛ. Написание dict/oem.json, чья запись стала
# указанием, «несколькими» или описанием (library/oem_kind.py), было разрешено
# к самой записи как к бренду; повторный засев ставит ему суждение по виду
# («не бренд», «спорно», разрешено к бренду описания), прежнее — в prev_*, и
# откат прогона возвращает его. Пометка, а не удаление (правило 5): строка и
# бренд прежней записи в lib_brands остаются, но lib_brand_map их не видит.
ВСТАВКА_НАПИСАНИЙ = """
insert into lib_brand_alias (spelling, spelling_key, source, seen_at, sp176_id, brand_key,
                             status, candidates, n_rows, note, rule, run_id)
values %s
on conflict (source, spelling, seen_at) do update set
  prev_status = lib_brand_alias.status,
  prev_brand_key = lib_brand_alias.brand_key,
  prev_run_id = lib_brand_alias.run_id,
  status = excluded.status, brand_key = excluded.brand_key,
  candidates = excluded.candidates, note = excluded.note,
  spelling_key = excluded.spelling_key, rule = excluded.rule,
  run_id = excluded.run_id, updated_at = now()
where (lib_brand_alias.status in ('в очереди', 'спорно') and excluded.status = 'разрешено')
   or (lib_brand_alias.source = 'dict/oem.json'
       and lib_brand_alias.status in ('разрешено', 'спорно', 'в очереди', 'не бренд')
       and (lib_brand_alias.status, lib_brand_alias.brand_key, lib_brand_alias.candidates)
           is distinct from (excluded.status, excluded.brand_key, excluded.candidates))
returning (xmax = 0) as inserted
"""

ГЕЙТ_КЛЮЧА = "ключ написания в SQL совпал с ключом в Python"
ГЕЙТЫ = [
    ("локаль базы складывает кириллицу (правило 21а)",
     "select case when lower('ШАЙБА') = 'шайба' then 0 else 1 end"),
    (ГЕЙТ_КЛЮЧА,
     "select count(*) from lib_brand_alias where run_id = %(run)s "
     "and spelling_key <> lib_brand_key(spelling)"),
    ("у разрешённого написания бренд есть в реестре",
     "select count(*) from lib_brand_alias where run_id = %(run)s and brand_key is not null "
     "and brand_key not in (select brand_key from lib_brands)"),
]


def печать_расхождения(cur, run_id: str) -> None:
    """Класс расхождения ключа — агрегатом, без написаний (правило 17)."""
    cur.execute(br.СРЕДА_SQL)
    версия, поставщик, ctype, icu = cur.fetchone()
    print(f"    среда базы: версия {версия}, свёртка {поставщик or '?'}, "
          f"ctype {ctype or '?'}" + (f", icu {icu}" if icu else ""))
    cur.execute(br.РАСХОЖДЕНИЯ_SQL, {"run": run_id})
    for класс, n in sorted(br.классы_расхождения(cur.fetchall()).items()):
        print(f"    расхождение ключа: {класс} ×{n}")


def без_повторов(написания: list[dict]) -> list[dict]:
    """Одна строка на (источник, написание, место): иначе ON CONFLICT DO UPDATE
    в одном операторе упрётся в свою же строку. Строки данных складываются."""
    по_ключу: dict[tuple, dict] = {}
    for н in написания:
        k = (н["source"], н["spelling"], н["seen_at"])
        if k in по_ключу:
            if н.get("n_rows"):
                по_ключу[k]["n_rows"] = (по_ключу[k].get("n_rows") or 0) + н["n_rows"]
            continue
        по_ключу[k] = dict(н)
    return list(по_ключу.values())


def записать(conn, бренды: list[dict], написания: list[dict], run_id: str) -> dict:
    """Одна транзакция на часть. Гейт не пройден — откат, запись не состоялась."""
    from psycopg2.extras import execute_values

    итог = {"брендов_новых": 0, "брендов_было": 0, "написаний_новых": 0,
            "написаний_разрешилось": 0, "написаний_без_изменений": 0}
    with conn.cursor() as cur:
        if бренды:
            новые = execute_values(cur, ВСТАВКА_БРЕНДОВ, [
                (b["brand_key"], b["name"], b.get("owner"), b.get("former_names"), b.get("country"),
                 b["sources"], br.ПРАВИЛО, run_id) for b in бренды], fetch=True, page_size=500)
            итог["брендов_новых"] = len(новые)
            итог["брендов_было"] = len(бренды) - len(новые)
        if написания:
            ответ = execute_values(cur, ВСТАВКА_НАПИСАНИЙ, [
                (н["spelling"], н["spelling_key"], н["source"], н["seen_at"], н.get("sp176_id"),
                 н.get("brand_key"), н["status"], н.get("candidates"), н.get("n_rows"),
                 н.get("note"), br.ПРАВИЛО, run_id) for н in написания], fetch=True, page_size=500)
            итог["написаний_новых"] = sum(1 for (вставлена,) in ответ if вставлена)
            итог["написаний_разрешилось"] = sum(1 for (вставлена,) in ответ if not вставлена)
            итог["написаний_без_изменений"] = len(написания) - len(ответ)
        провалы = []
        for имя, sql in ГЕЙТЫ:
            cur.execute(sql, {"run": run_id})
            n = cur.fetchone()[0]
            print(f"  гейт: {имя} — {'пройден' if n == 0 else f'НЕ ПРОЙДЕН ({n})'}")
            if n:
                провалы.append(имя)
                if имя == ГЕЙТ_КЛЮЧА:
                    печать_расхождения(cur, run_id)
    if провалы:
        conn.rollback()
        raise RuntimeError("гейты не пройдены, запись отменена: " + "; ".join(провалы))
    conn.commit()
    return итог


ОТКАТ = [
    ("написаний удалено",
     "delete from lib_brand_alias where run_id = %(run)s and prev_run_id is null"),
    ("написаний возвращено в прежнее состояние",
     "update lib_brand_alias set status = prev_status, brand_key = prev_brand_key, "
     "run_id = prev_run_id, prev_status = null, prev_brand_key = null, prev_run_id = null, "
     "updated_at = now() where run_id = %(run)s and prev_run_id is not null"),
    # Бренд прогона остаётся, если на него уже ссылается написание другого
    # прогона: удалить его значит оставить чужое написание без бренда. Условие —
    # «not in» по подзапросу без ссылки на внешнюю строку: считается один раз
    # (правило 8).
    ("брендов удалено",
     "delete from lib_brands where run_id = %(run)s and brand_key not in "
     "(select brand_key from lib_brand_alias where brand_key is not null)"),
    ("брендов прогона оставлено (на них ссылаются другие прогоны)",
     "select count(*) from lib_brands where run_id = %(run)s"),
]


def откатить(conn, run_id: str) -> None:
    with conn.cursor() as cur:
        for имя, sql in ОТКАТ:
            cur.execute(sql, {"run": run_id})
            n = cur.fetchone()[0] if sql.startswith("select") else cur.rowcount
            print(f"  {имя}: {n}")
    conn.commit()


# ── Печать ───────────────────────────────────────────────────────────────────

def печать_итогов(написания, заголовок: str) -> None:
    print(заголовок)
    for (источник, статус), n in sorted(br.итоги(написания).items()):
        print(f"  {источник:<26} {статус:<10} {n:>7}")


def печать_сверки(словарь, атлас) -> None:
    """Прежние правила против единого на корпусе написаний файлов (план 8.2)."""
    print("сверка прежних правил нормализации с единым (" + br.ПРАВИЛО + "), групп:")
    for r in br.сверка_правил(br.корпус_файлов(словарь, атлас)):
        if not r["loaded"]:
            print(f"  {r['rule']:<48} не загрузилось")
            continue
        print(f"  {r['rule']:<48} корпус {r['corpus']:>5} · склеивает лишнее {r['merges']:>4}"
              f" · расщепляет {r['splits']:>4}")


# ── Ступени ──────────────────────────────────────────────────────────────────

def ступень_реестр(conn, shard, shards, apply, run_id, читать_сп176=None) -> int:
    """читать_сп176(shard, shards) → (элементы, (низ, верх, макс)); None — портала нет."""
    словарь = читать_json(br.ФАЙЛ_СЛОВАРЯ)
    атлас = читать_json(br.ФАЙЛ_АТЛАСА)
    план = br.план_файлов(словарь, атлас)
    if shard == 0:
        печать_сверки(словарь, атлас)
    # Доля файлов — по ключу бренда у разрешённых, по ключу написания у прочих:
    # бренд и его написания пишет одна часть.
    бренды = {k: b for k, b in план.бренды.items() if br.часть_ключа(k, shards) == shard}
    написания = [н for н in план.написания
                 if br.часть_ключа(н.get("brand_key") or н["spelling_key"] or н["spelling"],
                                   shards) == shard]
    print(f"файлы: брендов в плане {len(план.бренды)}, из них в части {len(бренды)}; "
          f"написаний в плане {len(план.написания)}, в части {len(написания)}")

    if читать_сп176 is not None:
        t = time.monotonic()
        элементы, (низ, верх, макс) = читать_сп176(shard, shards)
        бренды_сп, написания_сп = br.план_справочника(план, элементы)
        print(f"справочник марок СП-176: номера {низ + 1}…{верх if верх is not None else макс} "
              f"из {макс}, элементов в части {len(элементы)}, "
              f"брендов к записи {len(бренды_сп)}, {time.monotonic() - t:.0f} с")
        for k, b in бренды_сп.items():
            бренды.setdefault(k, b)
        написания += написания_сп
    else:
        print("::warning::нет BITRIX_WEBHOOK_URL — справочник марок СП-176 не прочитан, "
              "ключи rfq_brands останутся неразрешёнными")
    написания = без_повторов(написания)
    печать_итогов(написания, "написаний в части по источнику и статусу:")
    return _записать_или_нет(conn, list(бренды.values()), написания, apply, run_id)


def карта_для_данных(cur) -> tuple[dict, str, str]:
    """(карта с множествами, текст карты для SQL, откуда). Реестр базы, если
    он есть и не пуст; иначе словарь-файл — как у страницы /brands до 8.2."""
    if есть_реестр(cur):
        cur.execute(br.КАРТА_С_СПОРНЫМИ_SQL)
        строки = cur.fetchall()
        if строки:
            return br.карта_из_строк(строки), br.КАРТА_РЕЕСТРА, "реестр базы"
    план = br.план_файлов(читать_json(br.ФАЙЛ_СЛОВАРЯ), читать_json(br.ФАЙЛ_АТЛАСА))
    пары = [(k, next(iter(v))) for k, v in план.карта.items() if len(v) == 1]
    return план.карта, codes_sql.карта_sql(пары), "словарь-файл (реестра в базе нет)"


def ступень_написания(conn, shard, shards, apply, run_id) -> int:
    with conn.cursor() as cur:
        карта, карта_sql, откуда = карта_для_данных(cur)
        print(f"разрешение по: {откуда}; ключей написаний в карте {len(карта)}, "
              f"спорных {sum(1 for v in карта.values() if len(v) > 1)}")
        # Записи словаря-файла, которые не бренд (library/oem_kind.py): компания с
        # таким именем получает суждение по виду записи, а не очередь.
        не_бренды = br.план_файлов(читать_json(br.ФАЙЛ_СЛОВАРЯ), читать_json(br.ФАЙЛ_АТЛАСА)).не_бренды
        cur.execute(br.КОМПАНИИ_SQL, {"n": shards, "k": shard})
        написания = br.написания_компаний(cur.fetchall(), карта, не_бренды)
        for источник in br.ИСТ_ДАННЫЕ:
            t = time.monotonic()
            cur.execute(br.данные_sql(источник, карта_sql), {"n": shards, "k": shard})
            строки = cur.fetchall()
            написания += br.написания_данных(источник, строки)
            print(f"  {источник}: частей ячеек в части {len(строки)}, "
                  f"{time.monotonic() - t:.1f} с")
    conn.rollback()
    написания = без_повторов(написания)
    печать_итогов(написания, "написаний в части по источнику и статусу:")
    return _записать_или_нет(conn, [], написания, apply, run_id)


def _записать_или_нет(conn, бренды, написания, apply, run_id) -> int:
    if not apply:
        print("вхолостую: в базе ничего не изменено. Для записи APPLY=1")
        return 0
    try:
        итог = записать(conn, бренды, написания, run_id)
    except RuntimeError as e:
        print(f"::error::{e}")
        return 1
    print("записано: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in итог.items()))
    print(f"откат: ROLLBACK={run_id}")
    return 0


def ступень_замер(conn, apply, run_id) -> int:
    числа = {}
    with conn.cursor() as cur:
        cur.execute(codes_sql.SETTINGS)
        реестр = есть_реестр(cur)
        план = br.план_файлов(читать_json(br.ФАЙЛ_СЛОВАРЯ), читать_json(br.ФАЙЛ_АТЛАСА))
        словарь_sql = codes_sql.карта_sql(
            [(k, next(iter(v))) for k, v in план.карта.items() if len(v) == 1])
        варианты = [("словарь-файл", False, словарь_sql)]
        if реестр:
            варианты.append(("реестр базы", True, br.КАРТА_РЕЕСТРА))
        else:
            print("::warning::реестра в базе нет (lib_brand_map): замер только по словарю-файлу")
        for имя, из_реестра, карта_sql in варианты:
            t = time.monotonic()
            cur.execute(br.замер_sql(из_реестра, карта_sql))
            print(f"доля строк с разрешённым брендом — по {имя} ({time.monotonic() - t:.0f} с):")
            print(f"  {'разрез':<52} {'строк':>9} {'с текстом':>10} {'с брендом':>10} "
                  f"{'разрешено':>10} {'доля':>7}")
            for порядок, разрез, всего, с_текстом, с_брендом, разрешено in cur.fetchall():
                доля = 100 * разрешено / с_текстом if с_текстом else 0.0
                print(f"  {разрез:<52} {всего:>9} {с_текстом:>10} {с_брендом:>10} "
                      f"{разрешено:>10} {доля:>6.1f}%")
                метка = "реестр" if из_реестра else "файл"
                числа[f"{метка}.{порядок}.всего"] = int(всего)
                числа[f"{метка}.{порядок}.с_текстом"] = int(с_текстом)
                числа[f"{метка}.{порядок}.разрешено"] = int(разрешено)
        if реестр:
            cur.execute(br.вне_очереди_sql(br.КАРТА_РЕЕСТРА))
            print("неразрешённые написания данных и очередь:")
            for источник, неразрешённых, вне in cur.fetchall():
                print(f"  {источник:<16} неразрешённых {неразрешённых:>7} · вне реестра {вне:>7}"
                      + ("" if вне == 0 else "  ← не стоят в очереди"))
                числа[f"вне_очереди.{источник}"] = int(вне)
            cur.execute(br.РЕЕСТР_ИТОГИ_SQL)
            print("реестр: написаний по источнику и статусу (строк данных за ними):")
            for источник, статус, n, строк in cur.fetchall():
                print(f"  {источник:<26} {статус:<10} {n:>7} ({строк})")
                числа[f"реестр.{источник}.{статус}"] = int(n)
            cur.execute("select count(*) from lib_brands")
            числа["брендов"] = int(cur.fetchone()[0])
            print(f"брендов в реестре: {числа['брендов']}")
    if apply:
        with conn.cursor() as cur:
            cur.execute("insert into lib_metric_runs (metric, run_key, nums, note) "
                        "values (%s, %s, %s, %s) on conflict (metric, run_key) do update "
                        "set nums = excluded.nums, measured_at = now(), note = excluded.note",
                        (МЕТРИКА, run_id, json.dumps(числа, ensure_ascii=False),
                         "доля строк с разрешённым брендом, план 8.2"))
        conn.commit()
        print(f"точка замера записана: {МЕТРИКА} · {run_id}")
    else:
        conn.rollback()
    return 0


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("::error::нет SUPABASE_DB_URL")
        return 1
    stage = (os.environ.get("STAGE") or "реестр").strip()
    shards = max(1, int(os.environ.get("SHARDS") or 1))
    shard = int(os.environ.get("SHARD") or 0)
    apply = bool(os.environ.get("APPLY", "").strip())
    откат = os.environ.get("ROLLBACK", "").strip()
    run_id = os.environ.get("RUN_ID", "").strip() or f"brands-{int(time.time())}"
    if not 0 <= shard < shards:
        print(f"::error::часть {shard} вне 0…{shards - 1}")
        return 1
    conn = подключить(dsn)
    try:
        if откат:
            print(f"откат прогона {откат}:")
            откатить(conn, откат)
            return 0
        print(f"ступень «{stage}», часть {shard} из {shards}, "
              f"{'запись, ключ ' + run_id if apply else 'вхолостую'}")
        if stage == "реестр":
            url = os.environ.get("BITRIX_WEBHOOK_URL", "").strip()
            читать = None
            if url:
                from bitrix_client import BitrixClient

                bx = BitrixClient(url)
                читать = lambda k, n: элементы_сп176(bx, k, n)  # noqa: E731
            return ступень_реестр(conn, shard, shards, apply, run_id, читать)
        if stage == "написания":
            return ступень_написания(conn, shard, shards, apply, run_id)
        if stage == "замер":
            return ступень_замер(conn, apply, run_id)
        print(f"::error::неизвестная ступень {stage!r}: реестр, написания, замер")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
