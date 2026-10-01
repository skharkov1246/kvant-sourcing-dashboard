#!/usr/bin/env python3
"""Прайс-бук КП (разбор моделью) — в закрытую базу, мимо публичного репозитория.

Прайс-бук — коммерческие данные: цены, поставщики, сделки. Загрузчик читает CSV
из папки владельца (PRICEBOOK_DIR) и пишет в таблицы pb_kp_headers и
pb_kp_lines (`library/supabase/pricebook_schema.sql`). Файлы прайс-бука в
репозиторий не попадают, в журнал идут только агрегаты (LESSONS, правило 17).

ЧТО НЕ ПЕРЕНОСИТСЯ. Имя заказчика, название сделки, фамилия сорсера, заметки
разбора и текст условий оплаты: в заметках остаются контакты (замер 01.10.2026 —
маскировка неполная), а остальное есть в Битриксе по deal_id и request_id.

КАЖДАЯ ЗАПИСЬ — С КЛЮЧОМ ПРОГОНА (LESSONS, правило 6). Повторная загрузка той же
версии прайс-бука строк не задваивает; откат — `delete … where run_id = …`.

СТРОКА ИДЕНТИФИЦИРУЕТСЯ ПОРЯДКОМ В ФАЙЛЕ, А НЕ line_no. Номер позиции в
документе повторяется: замер 01.10.2026 — у 417 строк пара (request_id, line_no)
не уникальна (209 из них — повторы) (в КП несколько таблиц, каждая со своей нумерацией). Ключ по
line_no молча выбросил бы эти строки на «on conflict do nothing».

БЕЗ APPLY=1 идёт вхолостую: читает, считает воронку сравнимости, печатает её.

    PRICEBOOK_DIR=~/pricebook python library/load_pricebook.py
    PRICEBOOK_DIR=~/pricebook SUPABASE_DB_URL=... APPLY=1 python library/load_pricebook.py
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pricebook import ПРИЧИНЫ, part_key, причина_несравнимости  # noqa: E402

VERSION = os.environ.get("PRICEBOOK_VERSION", "v1.0")
# Таймаут — в строке подключения, а не через SET: SET откатывается вместе с
# транзакцией (LESSONS, правило 9).
TIMEOUT_MS = int(os.environ.get("PRICEBOOK_TIMEOUT_MS", "600000"))

ШАПКА = ("request_id", "deal_id", "supplier_id", "doc_type", "doc_date", "price_date",
         "currency_main", "incoterm", "vat_included", "payment_class", "lead_weeks_max",
         "valid_until", "is_selected", "confidence", "model", "ocr")
СТРОКА = ("request_id", "line_no", "part_number", "brand_manufacturer",
          "description_original", "qty", "unit", "unit_price", "total_price",
          "currency_eff", "unit_price_rub", "offer_kind", "price_in_source",
          "confidence", "price_date", "is_selected", "supplier_id", "deal_id")


def прочитать(папка: Path) -> tuple[dict[str, dict], list[dict]]:
    csv.field_size_limit(1 << 24)
    with open(папка / "kp_headers.csv", encoding="utf-8", newline="") as f:
        шапки = {r["request_id"]: r for r in csv.DictReader(f)}
    with open(папка / "kp_lines.csv", encoding="utf-8", newline="") as f:
        строки = list(csv.DictReader(f))
    return шапки, строки


def разметить(шапки: dict[str, dict], строки: list[dict]) -> list[dict]:
    """К каждой строке — наш ключ артикула и причина несравнимости (None — сравнима)."""
    for i, r in enumerate(строки):
        r["row_no"] = i
        r["part_key"] = part_key(r.get("part_number"))
        r["reason"] = причина_несравнимости(r, шапки.get(r.get("request_id"), {}))
    return строки


def сводка(шапки: dict[str, dict], строки: list[dict]) -> dict:
    """Только агрегаты: числа, причины, доли. Ни артикулов, ни цен, ни имён."""
    с_ценой = [r for r in строки if r["reason"] != "нет цены"]
    причины = Counter(r["reason"] or "сравнима" for r in с_ценой)
    сравнимые = [r for r in с_ценой if r["reason"] is None]
    поставщики = defaultdict(set)
    for r in сравнимые:
        # пустой номер поставщика — не поставщик: иначе два безымянных КП на один
        # артикул засчитываются «двумя поставщиками» (замер 01.10.2026: 715 против 706)
        if (r.get("supplier_id") or "").strip():
            поставщики[r["part_key"]].add(r["supplier_id"].strip())
    пары = Counter((r.get("request_id"), r.get("line_no")) for r in строки)
    исходный = Counter()
    for r in строки:
        k = (r.get("pn_key") or "").strip()
        if k:
            исходный["ключей"] += 1
            if part_key(k) != k.lower():
                исходный["наш ключ иной"] += 1
    return {
        "документов": len(шапки),
        "строк": len(строки),
        "строк с ценой": len(с_ценой),
        "строго сравнимы": len(сравнимые),
        "по причинам": {п: причины.get(п, 0) for п in (*ПРИЧИНЫ[1:], "сравнима")},
        "артикулов у двух и более поставщиков": sum(1 for v in поставщики.values() if len(v) >= 2),
        "строк выбранных поставщиков, сравнимых": sum(
            1 for r in сравнимые if str(r.get("is_selected")).lower() in ("true", "1")),
        "ключ прайс-бука ≠ lib_pn_key": f"{исходный['наш ключ иной']} из {исходный['ключей']}",
        "строк с неуникальным номером позиции": sum(n for n in пары.values() if n > 1),
    }


ЦЕЛЫЕ = frozenset({"request_id", "deal_id", "supplier_id"})
ПРИЗНАКИ = frozenset({"vat_included", "is_selected", "ocr", "price_in_source"})


def _значение(колонка: str, v):
    """CSV → тип колонки: номера «11790.0» — целые, признаки «1.0»/«True» — boolean."""
    if v is None or str(v).strip() in ("", "nan", "None", "NaN"):
        return None
    v = str(v).strip()
    if колонка in ЦЕЛЫЕ:
        return int(float(v))
    if колонка in ПРИЗНАКИ:
        return v.lower() in ("1", "1.0", "true", "t")
    return v


def записать(dsn: str, шапки: dict[str, dict], строки: list[dict], run_id: str,
             схема: str | None = None) -> tuple[int, int]:
    """схема — только для теста: своя схема одноразовой базы."""
    import psycopg2
    import psycopg2.extras
    опции = f"-c statement_timeout={TIMEOUT_MS} -c lock_timeout=10000"
    if схема:
        опции += f" -c search_path={схема}"
    с = psycopg2.connect(dsn, options=опции)
    try:
        with с, с.cursor() as c:
            ev = psycopg2.extras.execute_values
            ev(c, "insert into pb_kp_headers (pb_version, run_id, " + ", ".join(ШАПКА) + ") values %s "
                  "on conflict (pb_version, request_id) do nothing",
               [(VERSION, run_id, *(_значение(k, h.get(k)) for k in ШАПКА)) for h in шапки.values()],
               page_size=1000)
            ev(c, "insert into pb_kp_lines (pb_version, run_id, row_no, part_key, reason, " +
                  ", ".join(СТРОКА) + ") values %s on conflict (pb_version, row_no) do nothing",
               [(VERSION, run_id, r["row_no"], r["part_key"] or None, r["reason"],
                 *(_значение(k, r.get(k)) for k in СТРОКА)) for r in строки],
               page_size=2000)
            # rowcount у execute_values — счёт последней пачки, а не итог
            # (поймано на полном прайс-буке: «записано 423» из 3 423). Итог —
            # из базы, по ключу прогона.
            c.execute("select (select count(*) from pb_kp_headers where run_id = %s),"
                      "       (select count(*) from pb_kp_lines where run_id = %s)",
                      (run_id, run_id))
            н_шапок, н_строк = c.fetchone()
        return н_шапок, н_строк
    finally:
        с.close()


def main() -> int:
    папка = Path(os.path.expanduser(os.environ.get("PRICEBOOK_DIR", "")))
    if not (папка / "kp_lines.csv").exists():
        print("PRICEBOOK_DIR не указывает на папку прайс-бука (нет kp_lines.csv)", file=sys.stderr)
        return 2
    шапки, строки = прочитать(папка)
    разметить(шапки, строки)
    for k, v in сводка(шапки, строки).items():
        print(f"{k}: {v}")
    if os.environ.get("APPLY", "") in ("", "0", "false"):
        print("холостой прогон: в базу ничего не записано (APPLY=1 — запись)")
        return 0
    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print("APPLY=1 без SUPABASE_DB_URL", file=sys.stderr)
        return 2
    run_id = "pb-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    н_шапок, н_строк = записать(dsn, шапки, строки, run_id)
    print(f"записано: документов {н_шапок}, строк {н_строк}; ключ прогона {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
