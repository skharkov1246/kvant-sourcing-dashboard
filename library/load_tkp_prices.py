#!/usr/bin/env python3
"""Цены нашего ТКП — в закрытую базу, мимо публичного репозитория.

ЗАЧЕМ ОТДЕЛЬНЫЙ ЗАГРУЗЧИК. Ведомость состава (gt/data/ms5001_vankor.json) —
техника: номер, наименование, количество. Её место в репозитории. Цены в том же
документе — наша отпускная цена названному заказчику; по CLAUDE.md, правило 5,
в публичный репозиторий она не кладётся. Поэтому загрузчик читает не файл
репозитория, а исходный xlsx по пути из TKP_XLSX: файл остаётся у владельца.

ПОЧЕМУ ОТДЕЛЬНЫЙ ПОТОК (feed). В lib_prices уже лежат цены закупки: прайсы,
таможня, коридоры RFQ. Наша отпускная цена — число другого рода: в ней сидит
наценка, и смешать её с закупочными значит испортить любой коридор. Поэтому
поток называется своим именем, а в note прямо сказано, чья это цена. Запрос
«почём деталь у поставщиков» обязан этот поток исключать.

ЧЕГО ЗДЕСЬ НЕТ. Заказчик-плательщик, контактное лицо и условия сделки не
переносятся: ни одна из этих величин не отвечает на вопрос «почём деталь».

БЕЗ APPLY=1 идёт вхолостую.

    pip install openpyxl psycopg2-binary
    TKP_XLSX=~/ТКП_MS5001PA.xlsx python library/load_tkp_prices.py
    TKP_XLSX=~/ТКП_MS5001PA.xlsx SUPABASE_DB_URL=... APPLY=1 python library/load_tkp_prices.py

В журнал идут только агрегаты: ни наименований позиций, ни цен построчно.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segments import classify  # noqa: E402

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
XLSX = os.environ.get("TKP_XLSX", "")
SHEET = os.environ.get("TKP_SHEET", "ТО-70000")

FEED = "ТКП КВАНТ (отпускная цена)"
NOT_KEY = re.compile(r"[^0-9a-zа-яё]+")
# Реквизиты предложения. Меняются вместе с документом, поэтому лежат рядом,
# а не размазаны по коду.
ПРЕДЛОЖЕНИЕ = {
    "ref": "исх. №99-77 от 08.06.2026, Rev. 1",
    "date": date(2026, 6, 8),
    "basis": "DDP",
    "currency": "USD",
    "machine": "GE MS5001 (исполнение MS5001PA), ТО-70000",
}
NOTE = ("наша отпускная цена в ТКП заказчику, не цена закупки; "
        f"{ПРЕДЛОЖЕНИЕ['machine']}; {ПРЕДЛОЖЕНИЕ['ref']}")


def part_key(pn: str) -> str:
    return NOT_KEY.sub("", (pn or "").lower().replace("ё", "е"))[:80]


def читать(путь: str, лист: str) -> list[dict]:
    """Строки предложения: номер позиции, каталожный номер, количество, цена.

    Отказ выносится по документу, а не по строке (CLAUDE.md, правило 13): если
    шапка не та или колонки не на месте, разбирать нечего. Строка без номера
    части или без цены — это заголовок раздела, а не потеря."""
    import openpyxl
    wb = openpyxl.load_workbook(путь, data_only=True)
    if лист not in wb.sheetnames:
        raise SystemExit(f"в книге нет листа «{лист}»: {wb.sheetnames}")
    ws = wb[лист]
    строки = []
    for r in range(1, ws.max_row + 1):
        поз, наим, pn, кол, цена, сумма = (ws.cell(r, i).value for i in range(1, 7))
        if not isinstance(кол, (int, float)) or not isinstance(цена, (int, float)):
            continue
        if not pn or not наим:
            continue
        строки.append({
            "row": r, "poz": str(поз).strip() if поз is not None else None,
            "name": str(наим).strip(), "pn": str(pn).strip(),
            "qty": float(кол), "price": float(цена),
            "sum": float(сумма) if isinstance(сумма, (int, float)) else None,
        })
    return строки


def проверить(строки: list[dict]) -> list[str]:
    """Что в документе не сходится. Возвращает претензии, а не бросает: решение
    о загрузке за владельцем, а молчать о расхождении нельзя."""
    беды = []
    расход = [s for s in строки
              if s["sum"] is not None and abs(s["qty"] * s["price"] - s["sum"]) > 0.01]
    if расход:
        беды.append(f"строк, где количество × цена ≠ сумма: {len(расход)}")
    номера = Counter(s["pn"] for s in строки)
    повторы = {pn for pn, n in номера.items() if n > 1}
    if повторы:
        разные = sum(1 for pn in повторы
                     if len({s["price"] for s in строки if s["pn"] == pn}) > 1)
        беды.append(f"каталожных номеров в нескольких строках: {len(повторы)}"
                    + (f", из них с разной ценой: {разные}" if разные else ""))
    позиции = [int(s["poz"]) for s in строки if (s["poz"] or "").isdigit()]
    if позиции:
        пропущено = len(set(range(1, max(позиции) + 1)) - set(позиции))
        if пропущено:
            беды.append(f"номеров позиций пропущено: {пропущено} из {max(позиции)}")
    return беды


def main() -> int:
    if not XLSX:
        print("нет переменной TKP_XLSX — путь к исходному файлу предложения",
              file=sys.stderr)
        return 2
    if not os.path.exists(XLSX):
        print("файла по TKP_XLSX нет", file=sys.stderr)
        return 2
    строки = читать(XLSX, SHEET)
    if not строки:
        print("в листе не нашлось ни одной ценовой строки", file=sys.stderr)
        return 2

    print("=== предложение ===")
    print(f"  строк с ценой: {len(строки)} · каталожных номеров: "
          f"{len({s['pn'] for s in строки})}")
    print(f"  валюта: {ПРЕДЛОЖЕНИЕ['currency']} · базис: {ПРЕДЛОЖЕНИЕ['basis']} · "
          f"дата: {ПРЕДЛОЖЕНИЕ['date']}")
    for b in проверить(строки):
        print(f"  ::расхождение:: {b}")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras
    # statement_timeout задаётся строкой подключения, а не SET: SET внутри
    # транзакции откатится вместе с ней (CLAUDE.md, правило 9).
    conn = psycopg2.connect(url, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=5000")
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("select id from lib_parts")
        известные = {r[0] for r in cur.fetchall()}
        # Повторный прогон обязан заменять, а не задваивать: ключа у lib_prices
        # нет, поэтому свой поток снимается целиком перед вставкой.
        cur.execute("delete from lib_prices where feed = %s", (FEED,))
        снято = cur.rowcount
        значения, мимо = [], 0
        for s in строки:
            key = part_key(s["pn"])
            if key not in известные:
                мимо += 1
            значения.append((
                classify(f"{s['name']} газовая турбина"), s["name"][:400], s["pn"][:120],
                s["price"], ПРЕДЛОЖЕНИЕ["currency"], ПРЕДЛОЖЕНИЕ["basis"], s["qty"],
                "шт", ПРЕДЛОЖЕНИЕ["date"], "КП", "high", NOTE,
                key if key in известные else None, FEED))
        psycopg2.extras.execute_values(cur, """
            insert into lib_prices (segment_id, item_name, part_number, price, currency,
                                    basis, qty, qty_unit, price_date, source, confidence,
                                    note, part_id, feed)
            values %s""", значения, page_size=500)
        conn.commit()
        if снято:
            print(f"  прежних цен этого потока снято: {снято}")
        if мимо:
            print(f"  цен без детали в каталоге: {мимо} из {len(значения)} "
                  f"(сначала загрузите ведомость: library/load_crossrefs.py)")
        cur.execute("select count(*) from lib_prices where feed = %s", (FEED,))
        print(f"  записано цен потока: {cur.fetchone()[0]}")
    conn.close()
    print("\n✓ цены предложения загружены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
