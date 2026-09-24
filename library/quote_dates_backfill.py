#!/usr/bin/env python3
"""Досчёт даты квотации у уже записанных цен КП — без переразбора файлов.

ЗАЧЕМ. До 24.09.2026 разбор не писал lib_prices.price_date у потока «разбор КП»
вовсе: около 34 тыс. строк цены лежат без даты, и индексировать их на инфляцию
нечем (распоряжение владельца 24.09.2026). Переразбор всех КП ради даты —
тысячи закачек с портала; дата документа придёт с общим переразбором части 8.5
(docs/suppliers/IMPLEMENTATION_PLAN.md). Здесь — то, что можно взять дёшево:
дату создания карточки запроса (createdTime СП-166) по lib_prices.rfq_id.
Это нижняя граница даты квотации, источник «карточка: создана»
(library/quote_date.py).

ЧТО ДЕЛАЕТ.
  1. Из базы — номера карточек, у строк которых нет даты (feed «разбор КП» и
     выведенные из потока — их откат возвращает, пусть вернутся с датой).
  2. Делит номера на SHARDS частей смежными кусками (правило дробления) и по
     каждой части читает карточки crm.item.list с фильтром «@id» по 50 номеров,
     start=-1 (без подсчёта total): карточек тысячи — запросов десятки.
  3. Пишет дату ТОЛЬКО в пустую price_date (дату документа не перетирает никогда),
     с источником и ключом прогона price_date_run. Каждая часть — своя транзакция:
     упавшая часть уносит только себя, повтор берёт то, что осталось пустым.

ВХОЛОСТУЮ ПО УМОЛЧАНИЮ (правило 3). Печатает: строк без даты, карточек, сколько
карточек портал отдал и сколько из них с датой, раскладку по годам (только
агрегаты, правило 17), сколько строк получит дату и сколько останется пустыми.
«Стало хуже» здесь ноль по построению — пишется только в пустое, — и это число
печатается явно (правило 0).

ГЕЙТЫ — запись части отменяется сама:
  · портал отдал меньше MIN_FOUND карточек из запрошенных — похоже на сбой чтения,
    а не на удалённые карточки;
  · дата карточки вне [2000-01-01, сегодня] — такую не пишем вовсе.

БЮДЖЕТ ПОРТАЛА. Клиент — общий indexer.bx (bitrix_client, BITRIX_RPS и
BITRIX_PARALLEL; CLAUDE.md, «Битрикс не перегружать»). Части идут ПОДРЯД в
одном процессе, поэтому BITRIX_PARALLEL=1. Прикидка: 5 тыс. карточек = 100
запросов = около полутора минут при 1,2 запроса в секунду.

    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… SHARDS=10 python library/quote_dates_backfill.py
    … APPLY=1 python library/quote_dates_backfill.py
    SUPABASE_DB_URL=… ROLLBACK=qd-123 python library/quote_dates_backfill.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import price_store  # noqa: E402
import quote_date  # noqa: E402

SPA_RFQ = 166
ПАЧКА = 50
#: Доля запрошенных карточек, которую портал обязан отдать. Карточки удаляют, но
#: не половину разом: меньше — сбой чтения, и писать по нему нельзя.
MIN_FOUND = 0.5
ПОТОКИ = (price_store.FEED, price_store.FEED_ВЫВЕДЕНО)

БЕЗ_ДАТЫ = """
select rfq_id, count(*)::bigint
  from lib_prices
 where feed = any(%s) and price_date is null
   and rfq_id is not null and rfq_id ~ '^[0-9]+$'
 group by rfq_id"""

СВОДКА = """
select count(*)::bigint,
       count(*) filter (where price_date is null)::bigint,
       count(*) filter (where price_date is null and (rfq_id is null or rfq_id !~ '^[0-9]+$'))::bigint
  from lib_prices where feed = any(%s)"""

ЗАПИСЬ = """
update lib_prices p
   set price_date = v.d::date, price_date_src = %s, price_date_run = %s
  from (values %s) as v(rfq, d)
 where p.feed = any(%s) and p.price_date is null and p.rfq_id = v.rfq"""

ОТКАТ = """
update lib_prices set price_date = null, price_date_src = null, price_date_run = null
 where price_date_run = %s"""


def части(номера: list[int], shards: int) -> list[list[int]]:
    """Смежные куски отсортированных номеров; пустых частей не бывает."""
    номера = sorted(set(номера))
    if shards <= 1 or len(номера) <= 1:
        return [номера] if номера else []
    шаг = -(-len(номера) // shards)
    return [номера[i:i + шаг] for i in range(0, len(номера), шаг)]


def прочитать_карточки(номера: list[int], bx) -> dict[int, str | None]:
    """Номер карточки → createdTime. Карточки, которых портал не отдал, — не в ответе."""
    out: dict[int, str | None] = {}
    for i in range(0, len(номера), ПАЧКА):
        j = bx("crm.item.list", {"entityTypeId": SPA_RFQ,
                                 "filter": {"@id": номера[i:i + ПАЧКА]},
                                 "select": ["id", "createdTime"], "start": -1})
        res = j.get("result")
        items = (res.get("items") if isinstance(res, dict) else res) or []
        for x in items:
            try:
                out[int(x["id"])] = x.get("createdTime")
            except (KeyError, TypeError, ValueError):
                continue
    return out


def даты(карточки: dict[int, str | None], сегодня: date) -> dict[int, date]:
    """Номер → дата карточки, только правдоподобные (quote_date.НИЖНЯЯ … сегодня)."""
    out = {}
    for н, v in карточки.items():
        д = quote_date.дата_карточки(v)
        if д and quote_date.НИЖНЯЯ <= д <= сегодня:
            out[н] = д
    return out


def разобрать_часть(номера: list[int], строк: dict[int, int], карточки: dict[int, str | None],
                    сегодня: date) -> tuple[dict[int, date], Counter, list[str]]:
    """Что часть запишет и почему — без базы и портала (для проверки и печати)."""
    д = даты(карточки, сегодня)
    сч = Counter()
    сч["карточек запрошено"] = len(номера)
    сч["карточек отдал портал"] = len(карточки)
    сч["карточек с датой"] = len(д)
    сч["строк получат дату"] = sum(строк.get(н, 0) for н in д)
    сч["строк останутся без даты"] = sum(строк.get(н, 0) for н in номера if н not in д)
    сч["строк станет хуже"] = 0            # пишется только в пустую дату
    провал = []
    if номера and len(карточки) < MIN_FOUND * len(номера):
        провал.append(f"портал отдал {len(карточки)} карточек из {len(номера)} — "
                      "похоже на сбой чтения")
    return д, сч, провал


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("::error::нет SUPABASE_DB_URL")
        return 2
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    try:
        with conn.cursor() as cur:
            откат = os.environ.get("ROLLBACK", "").strip()
            if откат:
                cur.execute(ОТКАТ, (откат,))
                n = cur.rowcount
                conn.commit()
                print(f"✓ откат {откат}: дата снята у строк: {n}")
                return 0
            if not os.environ.get("BITRIX_WEBHOOK_URL", "").strip():
                print("::error::нет BITRIX_WEBHOOK_URL")
                return 2
            cur.execute(СВОДКА, (list(ПОТОКИ),))
            всего, без_даты, без_карточки = (int(x) for x in cur.fetchone())
            print(f"строк цены КП: {всего} · без даты: {без_даты}"
                  f" · из них без номера карточки: {без_карточки}")
            cur.execute(БЕЗ_ДАТЫ, (list(ПОТОКИ),))
            строк = {int(r[0]): int(r[1]) for r in cur.fetchall()}
        conn.commit()

        shards = max(1, int(os.environ.get("SHARDS", "10") or 10))
        писать = bool(os.environ.get("APPLY", "").strip())
        run_id = os.environ.get("RUN_ID", "").strip() or \
            f"qd-{os.environ.get('GITHUB_RUN_ID') or int(datetime.now().timestamp())}"
        куски = части(list(строк), shards)
        print(f"карточек с недатированными строками: {len(строк)} · частей: {len(куски)}"
              f" · запросов к порталу около {sum(-(-len(к) // ПАЧКА) for к in куски)}")

        import indexer
        сегодня = datetime.now(timezone.utc).date()
        итог: Counter = Counter()
        по_годам: Counter = Counter()
        упало = 0
        for i, кусок in enumerate(куски, 1):
            карточки = прочитать_карточки(кусок, indexer.bx)
            д, сч, провал = разобрать_часть(кусок, строк, карточки, сегодня)
            итог.update(сч)
            for н, дд in д.items():
                по_годам[дд.year] += строк.get(н, 0)
            print(f"часть {i} из {len(куски)}: " + " · ".join(f"{к} {v}" for к, v in сч.items()),
                  flush=True)
            for п in провал:
                print(f"::error::часть {i}: гейт не пройден: {п} — запись части отменена")
            if провал:
                упало += 1
                continue
            if писать and д:
                with conn.cursor() as cur:
                    итог["строк записано"] += записать(cur, д, run_id,
                                                       psycopg2.extras.execute_values)
                conn.commit()

        print("\nИТОГ: " + " · ".join(f"{к} {v}" for к, v in итог.items()))
        print("строк с датой карточки по годам: "
              + ", ".join(f"{г}: {n}" for г, n in sorted(по_годам.items())))
        if упало:
            print(f"::warning::частей с непройденным гейтом: {упало} — повтор возьмёт их строки")
        if not писать:
            print("вхолостую: в базе ничего не изменено. Для записи APPLY=1")
        else:
            print(f"ключ прогона {run_id} · откат: ROLLBACK={run_id}")
        return 1 if упало else 0
    finally:
        conn.close()


def записать(cur, д: dict[int, date], run_id: str, execute_values) -> int:
    """Одна часть — одним UPDATE … FROM (VALUES …). Возвращает число строк.

    execute_values принимает ровно одно место под пачку значений, поэтому
    источник, ключ прогона и потоки вписываются заранее через mogrify. Пачка —
    одна на часть (page_size больше любой части): иначе rowcount вернул бы число
    строк последней страницы, а не всей части.
    """
    к = ЗАПИСЬ.split("%s")               # 5 кусков вокруг 4 мест
    assert len(к) == 5, "в ЗАПИСЬ ровно четыре места %s"
    запрос = (cur.mogrify(к[0] + "%s" + к[1] + "%s", (quote_date.КАРТОЧКА, run_id)).decode()
              + к[2] + "%s"
              + cur.mogrify(к[3] + "%s" + к[4], (list(ПОТОКИ),)).decode())
    execute_values(cur, запрос, [(str(н), дд.isoformat()) for н, дд in sorted(д.items())],
                   page_size=max(1, len(д)))
    return cur.rowcount


if __name__ == "__main__":
    sys.exit(main())
