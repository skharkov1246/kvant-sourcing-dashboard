#!/usr/bin/env python3
"""Досчёт истории связей: карточка СП-166 → сделка, сделка → заказчик.

ЗАЧЕМ. Недельный свод (scripts/weekly_offers.py) идёт по цепочке «КП поставщика
→ запрос → сделка → заказчик → наше КП заказчику», а два её звена в библиотеке
не хранились (library/deal_links.py). Разбор теперь пишет их из того же обхода,
что читает файлы (indexer.записать_связи), но только у карточек и сделок, до
которых обход дошёл. Здесь — вся история: все карточки СП-166 и все сделки.

ЧТО ЧИТАЕТ (CLAUDE.md, «Битрикс не перегружать»):
  карточки — crm.item.list entityTypeId 166, select id, parentId2, title,
             createdTime, по ключу «>id», start=-1 (без подсчёта total);
  сделки   — crm.deal.list select ID, TITLE, COMPANY_ID, STAGE_ID, DATE_CREATE,
             по ключу «>ID», start=-1.
Наибольший номер — один запрос на сущность; дальше части берут свой смежный
диапазон номеров (indexer.диапазон_части): один обход раскладывается на части,
а не повторяется каждой. Запросов на сущность ≈ записей/50 + частей + 1. Части
идут ПОДРЯД в одном процессе, поэтому BITRIX_PARALLEL=1; клиент — общий
indexer.bx с бюджетом портала. План с оценкой запросов и времени печатается ДО
обращения к порталу; PLAN=1 — только план.

ИМЯ ЗАКАЗЧИКА — БЕЗ ОБХОДА КОМПАНИЙ. Пишется номер компании сделки, а имя
даёт вид lib_deal_customer из реестра компаний (sup_identifier → sup_name_shown).
Реестр собран по поставщикам: сколько заказчиков он называет, печатается числом.

ПРОВЕРКИ (правило 3 и «Качество прежде скорости») — по части, запись части
отменяется сама:
  · портал не отдал поле связи (parentId2 у карточки, COMPANY_ID у сделки) ни в
    одной записи части — select не сработал, «связи нет» писать нельзя;
  · СВЕРКА PYTHON ↔ SQL: число строк и число связанных считает Python и
    считает база по тем же VALUES — расхождение значит порчу по дороге;
  · после вставки: что вернула вставка, то и лежит в таблице с ключом прогона
    (база снова считает сама) — иначе часть откатывается.
Сверх того печатается, сколько строк уже записано и сколько РАСХОДИТСЯ с
записанным (досчёт их не трогает: пишет только новые).

ВХОЛОСТУЮ ПО УМОЛЧАНИЮ: соединение только для чтения — записать нельзя даже по
ошибке. APPLY=1 — вставка ТОЛЬКО отсутствующих строк с ключом прогона run_id
(правило 6); «стало хуже» — ноль по построению. ROLLBACK=<ключ> снимает ровно
вставленные этим прогоном строки: они — копия портала, а не наша работа, и
досчёт их вернёт. Строку, которую потом подтвердил и изменил разбор, откат не
снимает — у неё уже ключ разбора.

В ЖУРНАЛ — ТОЛЬКО АГРЕГАТЫ (правило 17): ни названий, ни номеров.

    SUPABASE_DB_URL=… PLAN=1 python library/backfill_deal_links.py
    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… LIMIT=500 python library/backfill_deal_links.py
    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… APPLY=1 python library/backfill_deal_links.py
    SUPABASE_DB_URL=… ROLLBACK=lk-123 python library/backfill_deal_links.py

Входы: ENTITIES («cards,deals»), SHARDS (10), LIMIT (0 — все; иначе не больше
стольких записей на сущность, от свежих номеров), APPLY, PLAN, ROLLBACK, RUN_ID.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deal_links  # noqa: E402
import indexer  # noqa: E402  (клиент портала с бюджетом, обход по ключу, диапазоны частей)

КАРТОЧКИ, СДЕЛКИ = "cards", "deals"
СУЩНОСТИ = (КАРТОЧКИ, СДЕЛКИ)


@dataclass(frozen=True)
class Сущность:
    имя: str
    метод: str
    параметры: dict
    ключ: str                  # поле номера: «id» у crm.item.list, «ID» у crm.deal.list
    поле_связи: str            # без него в ответе — select не сработал
    строка: object             # deal_links.строка_*
    связь: int                 # индекс колонки связи в строке
    сверка: str
    новые: str
    записано: str
    таблица: str
    что_связь: str
    origin: str                # lib_files.origin файлов этой сущности — для плана


ОПИСАНИЕ = {
    КАРТОЧКИ: Сущность(КАРТОЧКИ, "crm.item.list",
                       {"entityTypeId": indexer.SPA_RFQ, "select": list(deal_links.ПОЛЯ_КАРТОЧКИ)},
                       "id", deal_links.ПОЛЕ_СДЕЛКИ_КАРТОЧКИ, deal_links.строка_карточки, 1,
                       deal_links.СВЕРКА_КАРТОЧЕК, deal_links.НОВЫЕ_КАРТОЧКИ,
                       deal_links.ЗАПИСАНО_КАРТОЧЕК, "lib_rfq_cards", "со сделкой",
                       "поле запроса"),
    СДЕЛКИ: Сущность(СДЕЛКИ, "crm.deal.list", {"select": list(deal_links.ПОЛЯ_СДЕЛКИ)},
                     "ID", "COMPANY_ID", deal_links.строка_сделки, 2,
                     deal_links.СВЕРКА_СДЕЛОК, deal_links.НОВЫЕ_СДЕЛКИ,
                     deal_links.ЗАПИСАНО_СДЕЛОК, "lib_deals", "с компанией",
                     "поле сделки"),
}


def включено(имя: str) -> bool:
    return os.environ.get(имя, "").strip().lower() in ("1", "true", "yes")


def число(имя: str, умолчание: int) -> int:
    т = os.environ.get(имя, "").strip()
    try:
        return int(т) if т else умолчание
    except ValueError:
        raise SystemExit(f"::error::{имя}={т!r}: нужно целое число") from None


def сущности_входа(значение: str | None) -> tuple[str, ...]:
    т = [x.strip() for x in (значение or "").split(",") if x.strip()] or list(СУЩНОСТИ)
    чужие = [x for x in т if x not in СУЩНОСТИ]
    if чужие:
        raise SystemExit(f"::error::ENTITIES: неизвестные {чужие}; допустимо "
                         + ", ".join(СУЩНОСТИ))
    return tuple(x for x in СУЩНОСТИ if x in т)


# ── План ─────────────────────────────────────────────────────────────────────

ПЛАН_SQL = """
select count(distinct f.deal_id)::bigint,
       coalesce(max(f.deal_id::bigint), 0)::bigint
  from lib_files f
 where f.origin = %s and f.deal_id ~ '^[0-9]{1,15}$'"""


def запросов_сверху(наибольший: int, частей: int) -> int:
    """Оценка СВЕРХУ: номера не сплошные, записей не больше наибольшего номера.
    Страница — 50 записей; каждая часть кончается короткой (или пустой)
    страницей; плюс один запрос наибольшего номера."""
    return -(-наибольший // indexer.СТРАНИЦА) + частей + 1


# ── Портал ───────────────────────────────────────────────────────────────────

def наибольший_номер(с: Сущность) -> int:
    """Один запрос: запись с наибольшим номером."""
    j = indexer.bx(с.метод, {**{к: v for к, v in с.параметры.items() if к != "select"},
                             "select": [с.ключ], "order": {с.ключ: "DESC"}, "start": -1})
    res = (j or {}).get("result")
    items = (res.get("items") if isinstance(res, dict) and "items" in res else res) or []
    try:
        return int(items[0][с.ключ]) if items else 0
    except (KeyError, TypeError, ValueError, IndexError):
        return 0


@dataclass
class Итог_части:
    записей: int = 0
    строк: list = field(default_factory=list)
    отброшено: int = 0          # запись без номера или без поля связи
    провал: list = field(default_factory=list)


def прочитать_часть(с: Сущность, низ: int, верх: int | None) -> Итог_части:
    """Записи диапазона (низ; верх] и строки таблицы из них. База не трогается."""
    записи = indexer.bx_all_by_id(с.метод, dict(с.параметры), с_id=низ, до_id=верх, ключ=с.ключ)
    и = Итог_части(записей=len(записи))
    с_полем = 0
    for x in записи:
        с_полем += isinstance(x, dict) and с.поле_связи in x
        r = с.строка(x)
        if r:
            и.строк.append(r)
        else:
            и.отброшено += 1
    if записи and not с_полем:
        и.провал.append(f"портал не отдал поле {с.поле_связи} ни в одной из {len(записи)} "
                        "записей — select не сработал, «связи нет» писать нельзя")
    и.строк = deal_links.без_повторов(и.строк)
    return и


# ── Прогон ───────────────────────────────────────────────────────────────────

ИТОГ_SQL = {
    "карточек в lib_rfq_cards": "select count(*)::bigint from lib_rfq_cards",
    "из них со сделкой": "select count(deal_id)::bigint from lib_rfq_cards",
    "карточек, чья сделка не в lib_deals": (
        "select count(*)::bigint from lib_rfq_cards c where c.deal_id is not null"
        " and c.deal_id not in (select deal_id from lib_deals)"),
    "сделок в lib_deals": "select count(*)::bigint from lib_deals",
    "из них с компанией": "select count(company_id)::bigint from lib_deals",
    "из них с именем заказчика из реестра": (
        "select count(company_title)::bigint from lib_deal_customer"),
    # Ради чего всё: сколько карточек с ценой из КП поставщика теперь выходят
    # на сделку (lib_prices.rfq_id — номер карточки).
    "карточек с ценой КП": (
        "select count(distinct rfq_id)::bigint from lib_prices where feed = 'разбор КП'"),
    "из них выходят на сделку": (
        "select count(distinct p.rfq_id)::bigint from lib_prices p"
        " join lib_rfq_cards c on c.card_id = p.rfq_id"
        " where p.feed = 'разбор КП' and c.deal_id is not null"),
}


def итог_базы(cur) -> list[str]:
    out = []
    for имя, sql in ИТОГ_SQL.items():
        cur.execute(sql)
        out.append(f"{имя} {cur.fetchone()[0]}")
    return out


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("::error::нет SUPABASE_DB_URL")
        return 2
    import psycopg2
    import psycopg2.extras
    сырой_откат = os.environ.get("ROLLBACK", "")
    откат = сырой_откат.strip()
    if сырой_откат and not откат:
        # Прогон с непустым входом rollback стоит вне очереди портала: из одних
        # пробелов он читал бы портал мимо неё.
        print("::error::ROLLBACK из одних пробелов: нужен ключ lk-… или пустое значение")
        return 2
    писать = включено("APPLY")
    # statement_timeout и lock_timeout — в строке подключения (правило 9).
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    try:
        if откат:
            return откатить(conn, откат)
        conn.set_session(readonly=not писать)
        return выполнить(conn, писать, psycopg2.extras.execute_values)
    finally:
        conn.close()


def откатить(conn, ключ: str) -> int:
    n = []
    with conn.cursor() as cur:
        for sql in deal_links.ОТКАТ:
            cur.execute(sql, (ключ,))
            n.append(cur.rowcount)
    conn.commit()
    print(f"✓ откат {ключ}: снято карточек {n[0]}, сделок {n[1]}")
    return 0


def выполнить(conn, писать: bool, execute_values) -> int:
    """Весь досчёт на открытом соединении. Портал — через indexer.bx (тест подменяет)."""
    сущности = сущности_входа(os.environ.get("ENTITIES"))
    shards = max(1, число("SHARDS", 10))
    предел = число("LIMIT", 0)
    run_id = (os.environ.get("RUN_ID", "").strip()
              or f"lk-{os.environ.get('GITHUB_RUN_ID') or 'local'}")
    with conn.cursor() as cur:
        cur.execute(deal_links.ЕСТЬ_ТАБЛИЦЫ)
        if not cur.fetchone()[0]:
            print("::error::нет таблиц lib_rfq_cards и lib_deals — примените "
                  "library/supabase/deal_links_schema.sql прогоном «ZIP base — apply DB "
                  "migrations» и повторите. Портал не читался.")
            return 2
        план = {}
        for имя in сущности:
            cur.execute(ПЛАН_SQL, (ОПИСАНИЕ[имя].origin,))
            известно, наибольший = cur.fetchone()
            план[имя] = (int(известно), int(наибольший))
        до = итог_базы(cur)
    conn.commit()

    import bitrix_client
    rps, процессов = bitrix_client.бюджет_портала()
    интервал = bitrix_client.интервал_портала()
    print("режим: " + ("ЗАПИСЬ (только новые строки)" if писать
                       else "холостой (соединение только для чтения)")
          + f" · сущности: {', '.join(сущности)} · частей: {shards}"
          + (f" · LIMIT={предел} записей на сущность" if предел else ""))
    всего_запросов = 0
    for имя in сущности:
        известно, наибольший = план[имя]
        з = запросов_сверху(наибольший, shards)
        всего_запросов += з
        print(f"план {имя}: номеров с файлами в базе {известно}, наибольший {наибольший}"
              f" · запросов не больше ~{з} (оценка сверху: номера не сплошные)")
    print(f"запросов к порталу не больше ~{всего_запросов} · около "
          f"{всего_запросов * интервал / 60:.0f} мин: бюджет {rps:g}/с на портал, "
          f"процессов {процессов} (части идут подряд)")
    print("в базе до прогона: " + " · ".join(до), flush=True)
    if включено("PLAN"):
        print("PLAN=1: только план, портал не читался, база не менялась")
        return 0
    if not os.environ.get("BITRIX_WEBHOOK_URL", "").strip():
        print("::error::нет BITRIX_WEBHOOK_URL")
        return 2

    итог: Counter = Counter()
    упало = 0
    for имя in сущности:
        с = ОПИСАНИЕ[имя]
        наибольший = наибольший_номер(с)
        if not наибольший:
            print(f"::error::{имя}: портал не назвал наибольший номер — без него части "
                  "читали бы весь портал; сущность пропущена")
            упало += 1
            continue
        print(f"\n{имя}: наибольший номер на портале {наибольший}", flush=True)
        взято = 0
        # ОТ СВЕЖИХ НОМЕРОВ К СТАРЫМ: выборка LIMIT берёт то, что нужно своду
        # в первую очередь. Разбиение одно и смежное — порядок частей его не меняет.
        for i in reversed(range(shards)):
            if предел and взято >= предел:
                break
            низ, верх = indexer.диапазон_части(наибольший, i, shards)
            ч = прочитать_часть(с, низ, верх)
            строки = ч.строк
            if предел:
                строки = sorted(строки, key=lambda r: int(r[0]), reverse=True)[:предел - взято]
            взято += len(строки)
            п_всего, п_связь = deal_links.счёт_python(строки, с.связь)
            провал = list(ч.провал)
            with conn.cursor() as cur:
                б_всего, б_связь, записано, расходится = deal_links.сверить(
                    cur, с.сверка, строки, execute_values)
                if (б_всего, б_связь) != (п_всего, п_связь):
                    провал.append(f"сверка Python ↔ SQL: строк {п_всего} ↔ {б_всего}, "
                                  f"{с.что_связь} {п_связь} ↔ {б_связь}")
                вставлено = вставлено_связь = 0
                if писать and not провал and строки:
                    ret = deal_links.вставить_новые(cur, с.новые, строки, run_id, execute_values)
                    вставлено = len(ret)
                    вставлено_связь = sum(1 for _к, св in ret if св is not None)
                    cur.execute(с.записано, (run_id, [r[0] for r in ret]))
                    в_базе = tuple(int(x) for x in cur.fetchone())
                    if в_базе != (вставлено, вставлено_связь):
                        провал.append(f"после вставки: вернула {вставлено} ({с.что_связь} "
                                      f"{вставлено_связь}), в таблице с ключом прогона {в_базе[0]} "
                                      f"({в_базе[1]})")
            if провал:
                conn.rollback()
                упало += 1
                for п in провал:
                    print(f"::error::{имя}, часть {i + 1} из {shards}: гейт не пройден: {п} — "
                          "часть не записана")
                вставлено = вставлено_связь = 0
            else:
                conn.commit()
            for к, v in ((f"{имя}: записей портала", ч.записей),
                         (f"{имя}: строк", п_всего), (f"{имя}: {с.что_связь}", п_связь),
                         (f"{имя}: отброшено", ч.отброшено),
                         (f"{имя}: уже записано", записано),
                         (f"{имя}: расходится с записанным", расходится),
                         (f"{имя}: вставлено", вставлено),
                         (f"{имя}: вставлено {с.что_связь}", вставлено_связь)):
                итог[к] += v
            print(f"часть {i + 1} из {shards} ({низ + 1}…{верх if верх is not None else 'конец'}):"
                  f" записей {ч.записей} · строк {п_всего} · {с.что_связь} {п_связь}"
                  f" · SQL {б_всего}/{б_связь} · уже записано {записано}"
                  f" · расходится {расходится} · вставлено {вставлено}", flush=True)

    print("\nИТОГ: " + " · ".join(f"{к} {v}" for к, v in итог.items()))
    print("станет хуже: 0 — досчёт пишет только отсутствующие строки")
    with conn.cursor() as cur:
        print("в базе после прогона: " + " · ".join(итог_базы(cur)))
    conn.commit()
    print(bitrix_client.сводка_нагрузки())
    if упало:
        print(f"::warning::частей с непройденным гейтом: {упало} — не записаны; "
              "повтор возьмёт то, чего нет в таблицах")
    if not писать:
        print("вхолостую: в базе ничего не изменено. Для записи APPLY=1")
    else:
        print(f"ключ прогона {run_id} · откат: ROLLBACK={run_id}")
    return 1 if упало else 0


if __name__ == "__main__":
    sys.exit(main())
