#!/usr/bin/env python3
"""Диагностика, дефект и ремонтное решение — середина цепочки портала.

ЗАЧЕМ. Портал ремонта отличается от прайс-листа ровно этими тремя звеньями: как
узел проверяют, чем он выходит из строя и что с этим делают. До сих пор их не
было вовсе — были только позиции и поставщики.

ЧЕСТНО О ОБЪЁМЕ. Материала в репозитории мало: разведка Ansaldo (этапы ремонта,
уровни инспекций, ремонтные центры, матрица модернизаций) и проработки отдельных
позиций с описанием риска и рекомендацией. Это десятки строк, а не тысячи. Здесь
заводится структура и переносится то, что есть; основной объём знаний о дефектах
лежит в текстах технических заданий — в тех самых файлах, которые разборщик
относит к «текст без спецификации», — и будет извлекаться отдельно.

ЭТАПЫ РЕМОНТА РАЗБИРАЮТСЯ НА ОПЕРАЦИИ. В разведке этап «2. Assess» записан одной
строкой через точку с запятой: «ЛЮМ; цветная дефектоскопия; вихретоковый; УЗ…».
Это не один метод, а девять, и в справочнике они должны быть отдельными строками,
иначе по «вихретоковому контролю» ничего не найдётся.

ИДЕНТИФИКАТОР — ХЕШ ОТ ВИДА И ИМЕНИ. Порядковый номер сменился бы при любой
правке исходного файла, и повторный прогон завёл бы дубли вместо обновления.

БЕЗ APPLY=1 идёт вхолостую.

    python library/load_repair.py
    SUPABASE_DB_URL=... APPLY=1 python library/load_repair.py

В журнал идут только агрегаты.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equipment as eq  # noqa: E402  (после sys.path)
from load_suppliers import norm as norm_company  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
TAGS = re.compile(r"<[^>]+>")
_ПРОБЕЛ_ПЕРЕД = re.compile(r"\s+([;,.:)])")


def clean(s) -> str:
    """Разметка из вёрстки страниц библиотеки в справочник не идёт.

    Тег заменяется пробелом, а не пустотой: «ремонт<b>TIG</b>» без пробела
    склеился бы в одно слово. Пробел перед знаком препинания после этого
    убирается — иначе в справочник ляжет «Ручной TIG ;»."""
    return _ПРОБЕЛ_ПЕРЕД.sub(r"\1", " ".join(TAGS.sub(" ", str(s or "")).split()))


def ident(kind: str, name: str) -> str:
    return kind[:12] + "." + hashlib.sha1(f"{kind}|{name}".encode()).hexdigest()[:10]


def load(path: str):
    full = os.path.join(ROOT, path)
    return json.load(open(full, encoding="utf-8")) if os.path.exists(full) else {}


def методы(текст: str) -> list[str]:
    """Строка этапа → отдельные операции. Разделитель — точка с запятой.

    Порог — две буквы, а не несколько знаков: названия методов контроля коротки
    (ЛЮМ, УЗ, ВТК, МПД), и любой порог по длине выбросил бы ровно то, ради чего
    строка разбирается."""
    out = []
    for часть in clean(текст).split(";"):
        t = часть.strip(" .")
        if len(t) < 200 and sum(c.isalpha() for c in t) >= 2:
            out.append(t[0].upper() + t[1:])
    return out


def build_procedures() -> dict[str, dict]:
    a = load("gt/data/ansaldo.json")
    proc: dict[str, dict] = {}

    def add(kind, name, **kw):
        name = clean(name)
        if not name:
            return
        rec = {"id": ident(kind, name), "kind": kind, "name": name[:300],
               "unit_id": kw.get("unit_id") or eq.unit_of(name),
               "scope": clean(kw.get("scope"))[:2000] or None,
               "duration": clean(kw.get("duration"))[:120] or None,
               "model_family": kw.get("model_family"),
               "performer": clean(kw.get("performer"))[:200] or None,
               "performer_key": None, "source": kw.get("source")}
        if rec["performer"]:
            rec["performer_key"] = norm_company(rec["performer"])[:200] or None
        if rec["unit_id"] not in UNITS:
            rec["unit_id"] = None
        proc[rec["id"]] = rec

    for st in a.get("repair_stages", []):
        имя = clean(st.get("s"))
        add("ремонт", имя, scope=st.get("w"), model_family="ansaldo",
            source="этапы ремонта горячего тракта, разведка Ansaldo")
        # «Assess» — это контроль, «Coat» — покрытия, остальное — ремонт.
        вид = ("контроль" if "assess" in имя.lower() else
               "покрытие" if "coat" in имя.lower() else
               "ремонт" if "repair" in имя.lower() else None)
        if вид:
            for m in методы(st.get("w")):
                add(вид, m, model_family="ansaldo",
                    source=f"этап «{имя}», разведка Ansaldo")

    for it in a.get("insp_types", []):
        add("инспекция", it.get("t"), scope=it.get("w"), duration=it.get("d"),
            model_family="ansaldo", source="уровни инспекций ГТУ, разведка Ansaldo")
    for it in a.get("gen_inspect", []):
        add("инспекция", it.get("lvl"), scope=it.get("what"), unit_id="generator",
            model_family="ansaldo", source="уровни инспекций генератора, разведка Ansaldo")
    for c in a.get("repair_centres", []):
        add("ремонт", f"Ремонтный центр: {clean(c.get('n'))}", scope=c.get("w"),
            performer=c.get("n"), model_family="ansaldo",
            source="ремонтные центры Ansaldo")
    for c in a.get("gen_repair", []):
        add("ремонт", f"Ремонт генератора: {clean(c.get('n'))}", scope=c.get("what"),
            performer=c.get("n"), unit_id="generator",
            source="исполнители ремонта генераторов, разведка Ansaldo")
    for u in a.get("upg_matrix", []):
        add("модернизация", f"{clean(u.get('p'))} для {clean(u.get('m'))}",
            scope=" · ".join(x for x in (clean(u.get("c")), clean(u.get("gt")),
                                         clean(u.get("cc"))) if x),
            duration=clean(u.get("wh")) or None, model_family="ansaldo",
            source="матрица модернизаций Ansaldo")
    return proc


def build_defects() -> dict[str, dict]:
    """Дефект и решение из проработок отдельных позиций: риск + рекомендация."""
    defects: dict[str, dict] = {}
    for path, key in (("gt/data/hot_parts.json", "positions"),
                      ("gt/data/cummins_qsk60.json", "positions")):
        for p in (load(path).get(key) or []):
            риск = clean(p.get("risks"))
            if len(риск) < 40:
                continue
            имя = clean(p.get("name"))[:200]
            unit = eq.unit_of(имя)
            defects[ident("дефект", имя)] = {
                "id": ident("дефект", имя), "name": f"Риск отказа: {имя}"[:300],
                "unit_id": unit if unit in UNITS else None,
                "part_number": clean(p.get("pn"))[:120] or None,
                "model": clean(p.get("model") or p.get("engine"))[:200] or None,
                "cause": None, "consequence": риск[:4000],
                "fix": clean(p.get("advice"))[:4000] or None,
                "source": f"проработка позиции ({os.path.basename(path)})",
            }
    # Ресурсные ограничения узлов: «1 и 2 ступень — только замена, не ремонт» —
    # это тоже знание о дефекте: оно говорит, чем кончится попытка ремонта.
    for p in (load("gt/data/ansaldo.json").get("parts") or []):
        жизнь = clean(p.get("life"))
        if len(жизнь) < 12 or жизнь == "—":
            continue
        имя = clean(p.get("ru"))[:200]
        unit = eq.unit_of(имя)
        defects[ident("ресурс", имя)] = {
            "id": ident("ресурс", имя), "name": f"Ресурс и ремонтопригодность: {имя}"[:300],
            "unit_id": unit if unit in UNITS else None, "part_number": None,
            "model": "семейство V (Ansaldo / Siemens)", "cause": None,
            "consequence": жизнь[:4000], "fix": clean(p.get("note"))[:4000] or None,
            "source": "номенклатура горячего тракта, разведка Ansaldo",
        }
    return defects


def узлы() -> set[str]:
    d = load("gt/data/parts.json")
    ids = set()
    for s in d.get("systems", []):
        ids.add(s["id"])
        for c in s.get("components", []):
            ids.add(f"{s['id']}.{eq.slug_en(c.get('en', ''))}")
    return ids | {u[0] for u in eq.EXTRA_UNITS}


UNITS = узлы()


def main() -> int:
    proc = build_procedures()
    defects = build_defects()

    print("=== операции ===")
    for k, n in Counter(p["kind"] for p in proc.values()).most_common():
        print(f"  {k:16}{n:>6}")
    print(f"  всего:{len(proc):>16}")
    с_исполнителем = sum(1 for p in proc.values() if p["performer_key"])
    print(f"  с названным исполнителем: {с_исполнителем}")
    print(f"  с определённым узлом:     {sum(1 for p in proc.values() if p['unit_id'])}")

    print("\n=== дефекты и ремонтные решения ===")
    print(f"  записей: {len(defects)}")
    print(f"  с решением: {sum(1 for d in defects.values() if d['fix'])}")
    print(f"  с узлом:    {sum(1 for d in defects.values() if d['unit_id'])}")
    print("\n  ЭТО МАЛО. Основной объём знаний о дефектах лежит в текстах ТЗ —")
    print("  в файлах со статусом «текст без спецификации», их извлечение отдельно.")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=600000")
    conn.autocommit = False
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            insert into lib_procedures (id, kind, name, unit_id, scope, duration,
                                        model_family, performer, performer_key, source)
            values %s
            on conflict (id) do update set
              scope = excluded.scope, duration = excluded.duration,
              unit_id = excluded.unit_id, performer = excluded.performer,
              performer_key = excluded.performer_key, updated_at = now()""",
            [(p["id"], p["kind"], p["name"], p["unit_id"], p["scope"], p["duration"],
              p["model_family"], p["performer"], p["performer_key"], p["source"])
             for p in proc.values()], page_size=500)
        psycopg2.extras.execute_values(cur, """
            insert into lib_defects (id, name, unit_id, part_number, model, cause,
                                     consequence, fix, source)
            values %s
            on conflict (id) do update set
              unit_id = excluded.unit_id, consequence = excluded.consequence,
              fix = excluded.fix, updated_at = now()""",
            [(d["id"], d["name"], d["unit_id"], d["part_number"], d["model"], d["cause"],
              d["consequence"], d["fix"], d["source"]) for d in defects.values()],
            page_size=500)
        conn.commit()
        for t in ("lib_procedures", "lib_defects"):
            cur.execute(f"select count(*) from {t}")
            print(f"  {t:16}{cur.fetchone()[0]:>6}")
        # Сколько исполнителей ремонта нашлись в общей базе компаний — это и есть
        # смычка звеньев «ремонтное решение» и «исполнитель».
        cur.execute("""
            select count(distinct p.performer_key)
              from lib_procedures p join lib_suppliers s on s.name_key = p.performer_key""")
        print(f"  исполнителей ремонта, найденных в базе компаний: {cur.fetchone()[0]}")
    conn.close()
    print("\n✓ операции и дефекты загружены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
