#!/usr/bin/env python3
"""Расход и стоимость обслуживания ГПУ: что меняют, как часто и почём.

ЗАЧЕМ. В цепочке пусто было ровно здесь: у ремонтного решения нет ни срока, ни
стоимости — 81 операция из 89 без интервала. А спрашивают именно это: «сколько
стоит содержать эту машину в год и что в этой сумме главное». Файл
gpu/data/consumption.json отвечает на оба вопроса по десяти газопоршневым
машинам — 180 строк с узлом, годовым расходом, ценой за единицу и интервалом
замены в моточасах — и не читался ни одним загрузчиком библиотеки.

ЭТО РАСЧЁТ, А НЕ ФАКТ, И ТАК ПОМЕЧЕНО. Источник собран gpu/tools/build_consumption.py
по типовым интервалам ТО изготовителей и нашим ценовым вилкам при 8 000 часов
работы в год. Это оценка, а не наши счета: уверенность «low», источник назван
расчётом, допущение по часам хранится рядом с числом. Выдать её за факт значит
через месяц не отличить одно от другого.

ЦЕНА ЗА ЕДИНИЦУ И ЦЕНА ЗА ГОД — РАЗНЫЕ КОЛОНКИ, и путать их нельзя: годовая
получается умножением на расход (у головки блока 8 штук в год), и подстановка
годовой суммы вместо цены завысила бы позицию в восемь раз. Обе хранятся
отдельно, как и в источнике.

БЕЗ APPLY=1 идёт вхолостую.

    python library/load_consumption.py
    SUPABASE_DB_URL=... APPLY=1 python library/load_consumption.py

В журнал идут только агрегаты и названия машин — они и так опубликованы.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equipment as eq  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ФАЙЛ = "gpu/data/consumption.json"
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
ИСТОЧНИК = ("расчёт расхода ГПУ (типовые интервалы ТО изготовителя и наши "
            "ценовые вилки, 8 000 часов в год) — оценка, не наши счета")


def число(v) -> float | None:
    try:
        x = float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    return x if x >= 0 else None


def ид(*ч) -> str:
    return hashlib.sha1("|".join(str(x or "") for x in ч).encode()).hexdigest()[:16]


def читать() -> dict:
    полный = os.path.join(ROOT, ФАЙЛ)
    return json.load(open(полный, encoding="utf-8")) if os.path.exists(полный) else {}


def разобрать(d: dict) -> tuple[list[dict], list[str]]:
    """Строки расхода. Узел берётся ключом системы ГПУ, а не угадывается по
    названию: в источнике он уже проставлен инженером направления."""
    часов = число((d.get("assumptions") or {}).get("hours_per_year", {}).get("base")) \
        or число(d.get("hours_per_year"))
    строки, без_узла = [], []
    for e in d.get("engines", []):
        машина = " ".join(str(e.get("model") or "").split())
        if not машина:
            continue
        ключ_машины = eq.norm_model(машина)
        for i in (e.get("items") or []):
            имя = " ".join(str(i.get("name") or "").split())
            if not имя:
                continue
            узел = "gpu." + str(i.get("sys") or "").strip()
            if узел == "gpu.":
                узел = None
                без_узла.append(имя)
            строки.append({
                "id": ид(машина, имя),
                "model_key": ключ_машины, "model_raw": машина[:200],
                "unit_id": узел, "name": имя[:300],
                "qty_year": число(i.get("qty_year")),
                "usd_unit": число(i.get("usd_unit")),
                "usd_year": число(i.get("usd_year")),
                "interval_h": число(i.get("interval")),
                "hours_year": часов,
                "note": " ".join(str(i.get("note") or "").split())[:600] or None,
                "source": ИСТОЧНИК,
            })
    return строки, без_узла


def main() -> int:
    d = читать()
    строки, без_узла = разобрать(d)
    if not строки:
        print(f"нет данных расхода в {ФАЙЛ}", file=sys.stderr)
        return 2

    машины = {s["model_key"] for s in строки}
    print("=== расход и стоимость обслуживания ===")
    print(f"  машин: {len(машины)}   строк расхода: {len(строки)}")
    print(f"  с узлом: {sum(1 for s in строки if s['unit_id'])}"
          + (f"   БЕЗ УЗЛА: {len(без_узла)}" if без_узла else ""))
    print(f"  с интервалом замены: {sum(1 for s in строки if s['interval_h'])}")
    print(f"  с ценой за единицу:  {sum(1 for s in строки if s['usd_unit'])}")

    по_узлу = Counter()
    for s in строки:
        if s["unit_id"] and s["usd_year"]:
            по_узлу[s["unit_id"]] += s["usd_year"]
    print("\n  годовая стоимость по узлам, сумма по всем машинам (долларов):")
    for u, v in по_узлу.most_common(8):
        print(f"    {u:18}{v:>12,.0f}".replace(",", " "))
    год = sum(s["usd_year"] or 0 for s in строки)
    print(f"\n  ВСЕГО по десяти машинам: {год:,.0f} $/год".replace(",", " "))
    print("  ЭТО ОЦЕНКА ПО ТИПОВЫМ ИНТЕРВАЛАМ, А НЕ НАШИ СЧЕТА: уверенность низкая,")
    print("  допущение — 8 000 часов работы в год, оно хранится рядом с числом.")

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
        # Машина и узел ставятся только существующие: справочники грузит другой
        # загрузчик, и висячая ссылка уронила бы вставку по внешнему ключу.
        cur.execute("select id from lib_models")
        модели = {r[0] for r in cur.fetchall()}
        cur.execute("select id from lib_units")
        узлы = {r[0] for r in cur.fetchall()}
        # Имя машины в расчёте и в справочнике различаются приставкой завода:
        # «Jenbacher J320 (Type 3)» против «INNIO Jenbacher J320 (Type 3)».
        # Поэтому после точного ключа пробуется ЕДИНСТВЕННОЕ вхождение: ключ
        # расчёта содержится в ключе справочника и ни в каком другом. Два
        # кандидата — оставляем без машины: связь наугад хуже её отсутствия.
        неоднозначных = 0
        for z in строки:
            if z["model_key"] in модели or len(z["model_key"]) < 8:
                continue
            похожие = [m for m in модели if z["model_key"] in m]
            if len(похожие) == 1:
                z["model_key"] = похожие[0]
            elif похожие:
                неоднозначных += 1
        нашлось = sum(1 for z in строки if z["model_key"] in модели)
        if неоднозначных:
            print(f"  имён машин с несколькими кандидатами: {неоднозначных} — "
                  f"оставлены без связи")
        psycopg2.extras.execute_values(cur, """
            insert into lib_consumption (id, model_id, model_raw, unit_id, name,
                                         qty_year, usd_unit, usd_year, interval_h,
                                         hours_year, note, source)
            values %s
            on conflict (id) do update set
              qty_year = excluded.qty_year, usd_unit = excluded.usd_unit,
              usd_year = excluded.usd_year, interval_h = excluded.interval_h,
              unit_id = excluded.unit_id, note = excluded.note""",
            [(s["id"], s["model_key"] if s["model_key"] in модели else None,
              s["model_raw"], s["unit_id"] if s["unit_id"] in узлы else None,
              s["name"], s["qty_year"], s["usd_unit"], s["usd_year"],
              s["interval_h"], s["hours_year"], s["note"], s["source"])
             for s in строки], page_size=200)
        conn.commit()
        cur.execute("select count(*) from lib_consumption")
        print(f"  lib_consumption {cur.fetchone()[0]:>6}")
        print(f"  строк, связанных со справочником машин: {нашлось} из {len(строки)}")
    conn.close()
    print("\n✓ расход и стоимость обслуживания загружены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
