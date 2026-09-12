#!/usr/bin/env python3
"""Один номер — вся цепочка: что это, чьё, на какие машины, кто даст и за сколько.

ЗАЧЕМ. Это главный запрос портала и главный вопрос сорсера. Данные для ответа
уже сведены в базе, но собрать их можно было только руками из семи таблиц.

    SUPABASE_DB_URL=... python scripts/library_part.py MW21215M
    SUPABASE_DB_URL=... python scripts/library_part.py "64/60030070/1" --limit 8
    PGDSN=... python scripts/library_part.py 7W-4377

Номер нормализуется так же, как при загрузке (без регистра и пунктуации),
поэтому «560-170-80» и «56017080» находят одну и ту же деталь. Если точного
совпадения нет, ищется по началу номера и по связям взаимозаменяемости: часто
на руках номер сборщика, а в базе — номер изготовителя, и наоборот.

В журнал идут данные одной запрошенной позиции, поэтому запускать это на
публичном раннере нельзя: скрипт для локального запуска и для страницы портала.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

DSN = os.environ.get("PGDSN") or os.environ.get("SUPABASE_DB_URL", "")
NOT_KEY = re.compile(r"[^0-9a-zа-яё]+")


def part_key(pn: str) -> str:
    return NOT_KEY.sub("", (pn or "").lower().replace("ё", "е"))[:80]


def строка(label: str, value, w: int = 20) -> None:
    if value not in (None, "", []):
        print(f"  {label:{w}}{value}")


def найди(cur, номер: str) -> list[str]:
    """Ключи деталей, к которым может относиться номер: точный, по началу и
    через взаимозаменяемость."""
    key = part_key(номер)
    if not key:
        return []
    cur.execute("select id from lib_parts where id = %s", (key,))
    точные = [r[0] for r in cur.fetchall()]
    if точные:
        return точные
    # Номер изготовителя или замена: ищем через таблицу взаимозаменяемости.
    cur.execute("""
        select distinct part_id from lib_part_alt
         where lower(regexp_replace(alt_pn, '[^0-9a-zA-Zа-яА-Я]', '', 'g')) = %s""", (key,))
    через_замену = [r[0] for r in cur.fetchall()]
    if через_замену:
        return через_замену
    cur.execute("select id from lib_parts where id like %s limit 10", (key + "%",))
    return [r[0] for r in cur.fetchall()]


def покажи(cur, part_id: str, limit: int) -> None:
    cur.execute("""
        select p.catalog_no, p.name, p.oem, p.model, p.category, p.hs_code, p.material,
               u.name, u.crit, p.pn_pattern, p.source, s.name
          from lib_parts p
          left join lib_units u on u.id = p.unit_id
          left join lib_segments s on s.id = p.segment_id
         where p.id = %s""", (part_id,))
    r = cur.fetchone()
    if not r:
        return
    print(f"\n=== {r[0]} ===")
    строка("наименование", r[1])
    строка("изготовитель", r[2])
    строка("машины (текст)", r[3])
    строка("узел", f"{r[7]} (критичность {r[8]})" if r[7] else None)
    строка("сегмент", r[11])
    строка("категория", r[4])
    строка("ТН ВЭД", r[5])
    строка("материал", r[6])
    строка("шифровка номера", r[9])
    строка("источник записи", r[10])

    cur.execute("""
        select m.name, coalesce(m.oem, ''), coalesce(m.legacy, '')
          from lib_part_models pm join lib_models m on m.id = pm.model_id
         where pm.part_id = %s order by m.name limit %s""", (part_id, limit))
    машины = cur.fetchall()
    if машины:
        print("\n  машины из справочника:")
        for name, oem, legacy in машины:
            хвост = " · ".join(x for x in (oem, f"он же {legacy}" if legacy else "") if x)
            print(f"    {name}{' — ' + хвост if хвост else ''}")

    cur.execute("""
        select a.kind, a.alt_pn, coalesce(a.alt_maker, ''), coalesce(a.confidence, '')
          from lib_part_alt a where a.part_id = %s order by a.kind, a.alt_pn limit %s""",
        (part_id, limit))
    замены = cur.fetchall()
    if замены:
        print("\n  другие номера этой же детали:")
        for kind, pn, maker, conf in замены:
            print(f"    {kind:22}{pn:26}{maker}{'  (' + conf + ')' if conf else ''}")

    cur.execute("""
        select s.name, coalesce(s.country, ''), coalesce(ps.source, ''),
               coalesce(ps.verdict, ''), coalesce(ps.in_stock, ''),
               coalesce(ps.lead_time, ''), ps.price, coalesce(ps.currency, '')
          from lib_part_suppliers ps join lib_suppliers s on s.id = ps.supplier_id
         where ps.part_id = %s
         order by (ps.verdict = 'in_stock') desc nulls last, s.name limit %s""",
        (part_id, limit))
    кто = cur.fetchall()
    if кто:
        print("\n  кто даёт:")
        for name, country, src, verdict, stock, lead, price, cur_ in кто:
            хвост = " · ".join(x for x in (
                country, verdict, stock, lead,
                f"{price:g} {cur_}" if price else "") if x)
            print(f"    {name[:44]:46}{src[:26]:28}{хвост}")

    cur.execute("""
        select price, coalesce(currency, ''), coalesce(source, ''), coalesce(feed, ''),
               coalesce(source_url, '')
          from lib_prices where part_id = %s order by price limit %s""", (part_id, limit))
    цены = cur.fetchall()
    if цены:
        print("\n  цены:")
        for price, cur_, src, feed, url in цены:
            print(f"    {price:>12,.2f} {cur_:4}{feed[:22]:24}{src[:40]}"
                  f"{'  ' + url if url else ''}".replace(",", " "))

    cur.execute("""
        select machine, coalesce(own_no, ''), coalesce(qty, '')
          from lib_bom where part_id = %s limit %s""", (part_id, limit))
    ведомость = cur.fetchall()
    if ведомость:
        print("\n  в ведомости состава:")
        for machine, own, qty in ведомость:
            print(f"    {machine[:34]:36}наш номер {own or '—':14}{qty}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Вся цепочка по одному номеру")
    ap.add_argument("номер")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()
    if not DSN:
        print("нет PGDSN / SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    conn = psycopg2.connect(DSN, connect_timeout=20)
    with conn.cursor() as cur:
        ключи = найди(cur, args.номер)
        if not ключи:
            print(f"«{args.номер}» в каталоге не найден ни точно, ни по началу, "
                  "ни через взаимозаменяемость")
            conn.close()
            return 1
        if len(ключи) > 1:
            print(f"подходит записей: {len(ключи)}")
        for k in ключи[:args.limit]:
            покажи(cur, k, args.limit)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
