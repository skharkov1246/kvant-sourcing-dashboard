#!/usr/bin/env python3
"""Дозаполнить сторону документа у файлов, разобранных до 22.09.2026.

ЗАЧЕМ. Сторона документа (library/doc_side.py) определяется по НАЗВАНИЮ поля
карточки, а в lib_files до сих пор хранился только КОД (ufCrm_…). Индексатор с
22.09.2026 пишет название и сторону сам, но у 30 с лишним тысяч уже разобранных
файлов эти колонки пусты — а именно на них стоят все замеры спроса.

Без дозаполнения отбор «заявки заказчика» дал бы почти пустой результат, и это
выглядело бы как «заявок мало», а не как «сторона не проставлена».

ЧТО ДЕЛАЕТ. Берёт названия файловых полей одним вызовом crm.item.fields на тип
сущности (сделка и карточка запроса), считает сторону правилом и обновляет
lib_files. Одним UPDATE на поле, а не построчно: полей десятки, файлов десятки
тысяч.

ПО УМОЛЧАНИЮ ВХОЛОСТУЮ (CLAUDE.md, правило 3): печатает раскладку и ничего не
пишет. Запись — APPLY=1.

КОД ПОЛЯ СКЛАДЫВАЕТСЯ ПО РЕГИСТРУ. Замер 22.09.2026: один и тот же ufCrm_1633502831
лежит в базе и как «ufCrm_1633502831», и как «UF_CRM_1633502831» — API отдаёт разный
регистр в разных контекстах. Сверка без складывания регистра оставила бы половину
файлов без стороны.

В журнал — только агрегаты (правило 17). Названия полей — настройки нашего же
Битрикса.

    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… python scripts/backfill_doc_side.py
    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… APPLY=1 python scripts/backfill_doc_side.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from library import doc_side  # noqa: E402

# Сверка идёт по ключу без регистра и подчёркиваний — см. пояснение в шапке.
СВОДКА = """
select lower(replace(coalesce(field, ''), '_', '')) as ключ,
       min(field)                                   as пример,
       count(*)::bigint                             as файлов,
       count(*) filter (where side is null)::bigint  as без_стороны
  from lib_files
 group by 1
 order by 3 desc
"""

ОБНОВИТЬ = """
update lib_files
   set field_title = %s, side = %s
 where lower(replace(coalesce(field, ''), '_', '')) = %s
   and (side is null or side <> %s or field_title is distinct from %s)
"""


def названия() -> dict[str, str]:
    """Код поля без регистра → название. Один вызов на тип сущности."""
    from library import indexer
    from quote_coverage import ПОЛЯ_КП

    карта: dict[str, str] = {}
    # Названия полей КП известны из константы, и они точнее вызова: константа —
    # закрытый список, по которому разбор и работает.
    for код, имя in ПОЛЯ_КП.items():
        карта[ключ(код)] = имя
    for тип in (2, indexer.SPA_RFQ):
        поля = ((indexer.bx("crm.item.fields", {"entityTypeId": тип}).get("result")
                 or {}).get("fields") or {})
        for код, свойства in поля.items():
            имя = (свойства or {}).get("title") or ""
            if имя:
                карта.setdefault(ключ(код), имя)
    return карта


def ключ(код: str) -> str:
    return (код or "").lower().replace("_", "")


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("::error::нет SUPABASE_DB_URL")
        return 2
    if not os.environ.get("BITRIX_WEBHOOK_URL", "").strip():
        print("::error::нет BITRIX_WEBHOOK_URL — названий полей не взять, а без них"
              " сторону не определить")
        return 2
    писать = bool(os.environ.get("APPLY", "").strip())
    import psycopg2

    карта = названия()
    print(f"названий полей получено: {len(карта)}")

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    try:
        with conn.cursor() as cur:
            cur.execute(СВОДКА)
            строки = cur.fetchall()
            всего = sum(int(r[2]) for r in строки)
            по_стороне: dict[str, int] = {}
            без_названия = 0
            обновлено = 0
            print(f"\nфайлов в базе: {всего}\n")
            print(f"{'поле':<28s} {'файлов':>7s} {'без стороны':>11s}  сторона · название")
            for _к, пример, файлов, без_стороны in строки:
                имя = карта.get(ключ(пример))
                if not имя:
                    без_названия += int(файлов)
                    print(f"{(пример or '(нет поля)'):<28s} {int(файлов):>7d}"
                          f" {int(без_стороны):>11d}  — названия нет")
                    continue
                ст = doc_side.сторона(имя)
                по_стороне[ст] = по_стороне.get(ст, 0) + int(файлов)
                print(f"{пример:<28s} {int(файлов):>7d} {int(без_стороны):>11d}"
                      f"  {ст:<12s} {имя[:44]}")
                if писать:
                    cur.execute(ОБНОВИТЬ, (имя, ст, ключ(пример), ст, имя))
                    обновлено += cur.rowcount

            print("\nПО СТОРОНАМ:")
            for ст in doc_side.СТОРОНЫ:
                if ст in по_стороне:
                    спрос = "ЗАЯВКА ЗАКАЗЧИКА — спрос" if ст in doc_side.СПРОС else "не спрос"
                    print(f"    {ст:<12s} файлов {по_стороне[ст]:>7d}  — {спрос}")
            if без_названия:
                print(f"    {'нет названия':<12s} файлов {без_названия:>7d}"
                      "  — сторона не проставится")

            if not писать:
                print("\nвхолостую: в базе ничего не изменено. Для записи APPLY=1")
                return 0
            conn.commit()
            print(f"\n✓ обновлено строк lib_files: {обновлено}")
            cur.execute("select side, count(*)::bigint from lib_files group by 1 order by 2 desc")
            print("проверка после записи:")
            for ст, n in cur.fetchall():
                print(f"    {str(ст or '(пусто)'):<14s} {int(n):>7d}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
