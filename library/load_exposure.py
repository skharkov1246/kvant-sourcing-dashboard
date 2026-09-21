#!/usr/bin/env python3
"""Деньги на кону: где у нас нет цены, а сумма по позиции большая.

ЗАЧЕМ. Список работ сорсера до сих пор шёл по порядку строк заявки, а не по
деньгам. gt/data/ship_inside_quotes.json сводит опись вложений сделок Битрикса с
номерами заявки и отвечает на два вопроса сразу: сколько долларов стоит за
позицией и в каком файле какой сделки цена уже лежит. 757 позиций на 2 168 584
доллара — и ни по одной из них нашей цены в базе нет. Файл не читался ни одним
загрузчиком библиотеки.

ЧТО ЗДЕСЬ ГЛАВНОЕ — АДРЕС, А НЕ ЦЕНА. Самих цен в источнике нет и быть не может:
репозиторий публичный. Есть адрес — сделка, имя файла, сколько в нём строк и
сколько с ценой. По этому адресу цену достаёт library/quotes.py; здесь заводится
очередь работ, отсортированная по сумме.

ИСТОЧНИК СЧИТАН С ОТРИЦАТЕЛЬНЫМ КОНТРОЛЕМ, и это переносится в базу вместе с
числами: выдуманные номера (перестановка цифр внутри настоящего) дали 0,0 %
совпадений против 60,4 % у настоящих. Сверка идёт по нормализованному номеру от
шести знаков, чисто числовые ряды короче семи знаков отброшены: ложное
совпадение здесь дороже пропуска, оно отправляет сорсера искать не то.

БЕЗ APPLY=1 идёт вхолостую.

    python library/load_exposure.py
    SUPABASE_DB_URL=... APPLY=1 python library/load_exposure.py

В журнал прогона идут только агрегаты: суммы, количества, доли. Ни номеров
сделок, ни имён файлов, ни наименований — репозиторий публичный.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_equipment import part_key  # noqa: E402  (один ключ артикула на всех)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ФАЙЛ = "gt/data/ship_inside_quotes.json"
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")


def число(v) -> float | None:
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def читать() -> dict:
    полный = os.path.join(ROOT, ФАЙЛ)
    return json.load(open(полный, encoding="utf-8")) if os.path.exists(полный) else {}


def разобрать(d: dict) -> list[dict]:
    """Строка очереди работ. Адресов может быть несколько — берутся все, но в
    первую очередь тот файл, где доля строк с ценой выше: в описи есть и
    двухтысячестрочные прайсы поставщика, и наш запрос из тридцати строк."""
    out: dict[str, dict] = {}
    for r in (d.get("rows") or []):
        номер = " ".join(str(r.get("pn") or "").split())
        if not номер:
            continue
        адреса = [a for a in (r.get("found_in") or []) if isinstance(a, dict)]
        адреса.sort(key=lambda a: (a.get("rows_with_price") or 0), reverse=True)
        лучший = адреса[0] if адреса else {}
        ключ = part_key(номер) or номер[:80]
        запись = out.get(ключ)
        if запись is None:
            out[ключ] = {
                "id": ключ,
                "part_number": номер[:120],
                "name": " ".join(str(r.get("name") or "").split())[:400] or None,
                "qty": число(r.get("qty")),
                "usd_exposure": число(r.get("usd_exposure")),
                "have_price": str(r.get("we_already_have_price")).lower() == "true",
                "deal": str(лучший.get("deal") or "")[:120] or None,
                "file": str(лучший.get("file") or "")[:300] or None,
                "file_rows": лучший.get("rows"),
                "file_rows_priced": лучший.get("rows_with_price"),
                "addresses": len(адреса),
                "написаний": 1,
                "source": "опись вложений сделок против номеров заявки "
                          "(сверка от шести знаков, отрицательный контроль 0,0 % "
                          "на выдуманных номерах против 60,4 % на настоящих)",
            }
            continue
        # ОДНА ДЕТАЛЬ В ДВУХ НАПИСАНИЯХ — ОДНА СТРОКА ОЧЕРЕДИ. Номер приходит и с
        # дефисами, и без; после нормализации ключ один, и две строки в одной
        # вставке роняют её целиком («cannot affect row a second time»). Деньги
        # и количество складываются: за деталью стоит вся сумма, а не большая из
        # двух, — иначе очередь недосчитается работы.
        запись["qty"] = (запись["qty"] or 0) + (число(r.get("qty")) or 0)
        запись["usd_exposure"] = ((запись["usd_exposure"] or 0)
                                  + (число(r.get("usd_exposure")) or 0))
        запись["addresses"] += len(адреса)
        запись["написаний"] += 1
        if (лучший.get("rows_with_price") or 0) > (запись["file_rows_priced"] or 0):
            запись.update({"deal": str(лучший.get("deal") or "")[:120] or None,
                           "file": str(лучший.get("file") or "")[:300] or None,
                           "file_rows": лучший.get("rows"),
                           "file_rows_priced": лучший.get("rows_with_price")})
    return list(out.values())


def main() -> int:
    d = читать()
    строки = разобрать(d)
    if not строки:
        print(f"нет данных в {ФАЙЛ}", file=sys.stderr)
        return 2

    без_цены = [s for s in строки if not s["have_price"]]
    сумма = sum(s["usd_exposure"] or 0 for s in строки)
    сумма_без = sum(s["usd_exposure"] or 0 for s in без_цены)
    с_адресом = sum(1 for s in строки if s["deal"])

    слитых = sum(s["написаний"] - 1 for s in строки)
    print("=== деньги на кону ===")
    print(f"  позиций: {len(строки)}   из них без нашей цены: {len(без_цены)}")
    if слитых:
        print(f"  свёрнуто написаний одного номера: {слитых} — "
              f"деньги и количество сложены, а не взято большее")
    print(f"  сумма всего:      {сумма:>14,.0f} $".replace(",", " "))
    print(f"  сумма без цены:   {сумма_без:>14,.0f} $".replace(",", " "))
    print(f"  с адресом, где цена лежит: {с_адресом} "
          f"({с_адресом * 100 // len(строки)} %)")
    крупные = sorted(без_цены, key=lambda s: s["usd_exposure"] or 0, reverse=True)[:10]
    print("\n  десять крупнейших позиций без цены (только суммы и количества):")
    for s in крупные:
        print(f"    {s['usd_exposure'] or 0:>12,.0f} $   кол-во {s['qty'] or 0:g}"
              .replace(",", " "))
    доля = sum(s["usd_exposure"] or 0 for s in крупные) / сумма_без * 100 if сумма_без else 0
    print(f"  на эти десять приходится {доля:.0f} % всей суммы без цены — "
          f"очередь работ начинается с них, а не с первой строки заявки")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=300000")
    with conn.cursor() as cur:
        # Сколько позиций очереди уже закрыто ценой из КП. Считается ДО записи:
        # это ответ на вопрос «сколько работы сделано», и он обязан считаться по
        # базе, а не по флагу источника, который с 18.09 не менялся.
        cur.execute("""
            select count(*) from lib_exposure e
             where exists (select 1 from lib_prices p
                            where p.part_id = e.part_id and p.feed = 'разбор КП'
                              and p.price is not null)""")
        закрыто = cur.fetchone()[0]
        if закрыто:
            print(f"  уже закрыто ценой из КП: {закрыто} позиций прошлой очереди")
        cur.execute("select id from lib_parts")
        каталог = {r[0] for r in cur.fetchall()}
        psycopg2.extras.execute_values(cur, """
            insert into lib_exposure (id, part_id, part_number, name, qty, usd_exposure,
                                      have_price, deal, file, file_rows, file_rows_priced,
                                      addresses, source)
            values %s
            on conflict (id) do update set
              qty = excluded.qty, usd_exposure = excluded.usd_exposure,
              have_price = excluded.have_price, deal = excluded.deal,
              file = excluded.file, file_rows = excluded.file_rows,
              file_rows_priced = excluded.file_rows_priced,
              addresses = excluded.addresses, updated_at = now()""",
            [(s["id"], s["id"] if s["id"] in каталог else None, s["part_number"],
              s["name"], s["qty"], s["usd_exposure"], s["have_price"], s["deal"],
              s["file"], s["file_rows"], s["file_rows_priced"], s["addresses"],
              s["source"]) for s in строки], page_size=300)
        conn.commit()
        cur.execute("select count(*), count(part_id) from lib_exposure")
        всего, с_деталью = cur.fetchone()
        print(f"  lib_exposure {всего:>6}   из них узнаны каталогом: {с_деталью}")
    conn.close()
    print("\n✓ очередь работ по деньгам загружена")
    return 0


if __name__ == "__main__":
    sys.exit(main())
