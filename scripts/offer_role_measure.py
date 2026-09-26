#!/usr/bin/env python3
"""Замер вхолостую: сколько предложений прямые, сколько от трейдеров.

ЗАЧЕМ. Решение владельца 26.09.2026: «Прямым предложением является офер от
непосредственно бренда или производителя, все остальные — от трейдеров». Правило —
library/offer_role.py. Прежде чем показывать роль на карточках, её меряют на живой
базе (CLAUDE.md, правило 3): сколько прямых, каким путём они доказаны, и у
скольких предложений прежнее правило ревизии (строка имени компании против строки
изготовителя, scripts/portal_audit.py до 26.09.2026) говорило «прямое», а новое —
нет (правило 0: у скольких стало хуже).

ЧТО ЧИТАЕТ. Предложения lib_prices потока «разбор КП» — та же связь с реестром
компаний, что у crossref.ПРЕДЛОЖЕНИЯ_SQL: компания карточки запроса → sup_identifier
(bitrix) → sup_entity, имя — sup_name_shown (без вида — display_name). У компании —
её домены и ИНН (sup_identifier). Бренд позиции — три источника, и они считаются
порознь, а потом вместе:
  · файл      — изготовитель, названный поставщиком в КП (lib_prices.oem);
  · карточка  — бренды карточки запроса (lib_prices.rfq_brands → вид lib_brand_sp176);
  · каталог   — изготовитель каталога по ключу номера (lib_parts.oem);
  · итог      — все три написания вместе.
Карта написаний — файлы репозитория плюс lib_brand_map базы, если он есть.

ЧТО ПЕЧАТАЕТ (только агрегаты, правило 17): по каждому источнику — прямых /
трейдеров / не определено, раскладка по причинам, различных компаний каждой роли
(числом); топ ключей брендов по числу прямых (ключи брендов — константы
справочников, не данные заказчиков); сверка с прежним правилом. Ни имён компаний,
ни доменов, ни номеров, ни наименований.

ДЕЛЕНИЕ. Строки читаются частями по остатку id (PARTS, по умолчанию 10, правило
дробления) и сводятся в базе группировкой: одинаковые сочетания «компания, бренды»
судятся один раз. Только выборки — работает при базе в режиме только чтения.

    SUPABASE_DB_URL=… python scripts/offer_role_measure.py
"""
from __future__ import annotations

import collections
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import offer_role  # noqa: E402
from library.oem_kind import без_формы  # noqa: E402

FEED = "разбор КП"
ИСТОЧНИКИ = ("файл", "карточка", "каталог", "итог")
ТОП_БРЕНДОВ = 30

# Домены и ИНН компании — тот же запрос, что читает публикатор снимка
# номенклатуры (offer_role.ИДЕНТИФИКАТОРЫ_SQL): роль в снимке и в замере одна.
ЧАСТЬ_SQL = """
with ид as (""" + offer_role.ИДЕНТИФИКАТОРЫ_SQL.replace("%", "%%") + """)
select e.id                                   as сущность,
       coalesce(nm.name, e.display_name)      as имя,
       nullif(btrim(p.oem), '')               as файл,
       nullif(btrim(p.rfq_brands), '')        as карточка,
       {каталог}                              as каталог,
       ид.домены, ид.инн,
       count(*)                               as строк
  from lib_prices p
  left join sup_identifier i
         on i.kind = 'bitrix' and i.status <> 'rejected' and i.value_norm = p.rfq_company
  left join sup_entity e on e.id = i.sup_id
  left join {имена} nm on nm.sup_id = e.id
  left join ид on ид.sup_id = e.id
  {каталог_join}
 where p.feed = %(feed)s and (p.id %% %(n)s) = %(k)s
 group by 1, 2, 3, 4, 5, 6, 7
"""

КАТАЛОГ_JOIN = """left join (select lib_pn_key(catalog_no) as k, min(oem) as oem
               from lib_parts where btrim(coalesce(oem, '')) <> '' group by 1) c
         on c.k = lib_pn_key(p.part_number)"""

ЗАГЛУШКА_ИМЁН = "(select null::text as sup_id, null::text as name where false)"


def есть(cur, имя: str) -> bool:
    cur.execute("select to_regclass(%s) is not null", (имя,))
    return bool(cur.fetchone()[0])


def карточка_в_ключи(поле, sp176: dict) -> list[str]:
    """«101,205» (номера элементов СП-176) → ключи брендов; обрубок в конце снимается."""
    t = str(поле or "")
    if len(t) >= 200:
        t = re.sub(r",[^,]*$", "", t)
    return [sp176[x] for x in re.sub(r"\s", "", t).split(",") if x in sp176]


def прежнее_прямое(имя, изготовитель) -> bool:
    """Правило ревизии до 26.09.2026 — строка без формы против строки."""
    н = lambda s: " ".join(str(s or "").lower().replace("ё", "е").split())  # noqa: E731
    return bool(имя) and bool(изготовитель) and н(без_формы(имя)) == н(без_формы(изготовитель))


class Итог:
    def __init__(self):
        self.роли = {и: collections.Counter() for и in ИСТОЧНИКИ}
        self.причины = {и: collections.Counter() for и in ИСТОЧНИКИ}
        self.компании = {и: collections.defaultdict(set) for и in ИСТОЧНИКИ}
        self.бренды_прямых = collections.Counter()
        self.прежнее = collections.Counter()      # (прежнее прямое?, новая роль) → строк
        self.строк = 0
        self.без_сущности = 0


def судить(итог: Итог, строки, sp176: dict, реестр) -> None:
    кэш: dict = {}
    for сущ, имя, файл, карточка, каталог, домены, инн, n in строки:
        итог.строк += n
        if сущ is None:
            итог.без_сущности += n
        бренды = {"файл": [файл] if файл else [],
                  "карточка": карточка_в_ключи(карточка, sp176),
                  "каталог": [каталог] if каталог else []}
        бренды["итог"] = бренды["файл"] + бренды["карточка"] + бренды["каталог"]
        for и in ИСТОЧНИКИ:
            ключ = (имя, tuple(бренды[и]), tuple(домены or ()), tuple(инн or ()))
            о = кэш.get(ключ)
            if о is None:
                о = кэш[ключ] = offer_role.роль_предложения(имя, бренды[и], домены=домены or (),
                                                            инн=инн or (), реестр=реестр)
            итог.роли[и][о["role"]] += n
            итог.причины[и][(о["role"], о["why"])] += n
            if сущ is not None:
                итог.компании[и][о["role"]].add(сущ)
            if и == "итог" and о["role"] == offer_role.ПРЯМОЕ:
                for b in о["evidence"].get("position") or []:
                    # Ключ вне реестра — написание из данных, а не константа
                    # справочника: в журнал идёт только его число.
                    итог.бренды_прямых["(вне реестра)" if b.startswith("~") else b] += n
            if и == "файл" and файл:
                итог.прежнее[(прежнее_прямое(имя, файл), о["role"])] += n


def доля(a: int, b: int) -> str:
    return f"{a:,} из {b:,} ({100 * a / b:.1f} %)" if b else f"{a:,} из 0"


def печать(итог: Итог, откуда_карты: str, частей: int) -> None:
    print(f"\nпредложений «{FEED}»: {итог.строк:,}; без сущности реестра компаний: "
          f"{доля(итог.без_сущности, итог.строк)}; карта: {откуда_карты}; частей: {частей}")
    for и in ИСТОЧНИКИ:
        р = итог.роли[и]
        print(f"\n── бренд позиции: {и} ──")
        for роль in offer_role.РОЛИ:
            print(f"  {роль:<14} {доля(р[роль], итог.строк):<32} компаний {len(итог.компании[и][роль]):,}")
        for (роль, почему), n in sorted(итог.причины[и].items(), key=lambda x: (-x[1], x[0])):
            print(f"    {роль:<14} {почему:<40} {n:>9,}")
    print(f"\n── прямые по брендам позиции (итог), первые {ТОП_БРЕНДОВ} ──")
    for b, n in итог.бренды_прямых.most_common(ТОП_БРЕНДОВ):
        print(f"  {b:<40} {n:>9,}")
    print("\n── сверка с прежним правилом ревизии (бренд позиции — файл) ──")
    for (было, стало), n in sorted(итог.прежнее.items(), key=lambda x: (not x[0][0], x[0][1])):
        print(f"  прежнее {'прямое' if было else 'нет':<7} → {стало:<14} {n:>9,}")
    хуже = sum(n for (было, стало), n in итог.прежнее.items() if было and стало != offer_role.ПРЯМОЕ)
    лучше = sum(n for (было, стало), n in итог.прежнее.items() if not было and стало == offer_role.ПРЯМОЕ)
    print(f"  стало хуже (было прямое, стало нет): {хуже:,}; стало прямым: {лучше:,}")


def замер(cur, частей: int) -> tuple[Итог, str] | None:
    """Прогон по открытому курсору: (итог, откуда карта) или None, если мерить нечего."""
    for т in ("lib_prices", "sup_identifier", "sup_entity"):
        if not есть(cur, т):
            print(f"нет таблицы {т} — мерить нечего")
            return None
    доп, откуда = offer_role.карта_базы(cur)
    sp176 = {}
    if есть(cur, "lib_brand_sp176"):
        cur.execute("select sp176_id::text, brand_key from lib_brand_sp176")
        sp176 = dict(cur.fetchall())
    print(f"элементов СП-176 с брендом: {len(sp176):,}")
    реестр = offer_role.реестр_файлов(tuple(map(tuple, доп)))
    cur.execute("select to_regprocedure('lib_pn_key(text)') is not null")
    каталог = bool(cur.fetchone()[0]) and есть(cur, "lib_parts")
    sql = ЧАСТЬ_SQL.format(
        каталог="c.oem" if каталог else "null::text",
        каталог_join=КАТАЛОГ_JOIN if каталог else "",
        имена="sup_name_shown" if есть(cur, "sup_name_shown") else ЗАГЛУШКА_ИМЁН)
    итог = Итог()
    for k in range(частей):
        t = time.time()
        cur.execute(sql, {"feed": FEED, "n": частей, "k": k})
        строки = cur.fetchall()
        судить(итог, строки, sp176, реестр)
        print(f"часть {k + 1}/{частей}: сочетаний {len(строки):,}, {time.time() - t:.1f} с")
    return итог, откуда


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — мерить нечего")
        return 2
    import psycopg2

    частей = max(1, int(os.environ.get("PARTS", "10") or 10))
    t0 = time.time()
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=900000 -c work_mem=64MB")
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("select lower('ШАЙБА') = 'шайба'")
            if not cur.fetchone()[0]:
                print("база не складывает регистр кириллицы (локаль C) — ключи считаются"
                      " иначе, чем на живой (CLAUDE.md, правило 21а)")
                return 3
            вышло = замер(cur, частей)
            if вышло is None:
                return 3
            печать(вышло[0], вышло[1], частей)
    finally:
        conn.close()
    print(f"\nвремя: {time.time() - t0:.0f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
