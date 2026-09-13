#!/usr/bin/env python3
"""Досье машины → SQL для базы: zip/data/r1700.json → zip/supabase/seed_r1700.sql.

Данные портала живут в Supabase, а в git лежит их источник. Этот скрипт переводит
досье в идемпотентный SQL: повторный прогон не двоит строки, а обновляет их
(insert … on conflict do update). Файл применяется тем же workflow, что и схема:
Actions → «ZIP base — apply DB migrations» (psql -f).

Таблицы (создаются в zip/supabase/migrations.sql, раздел 7): mach_machines,
mach_docs, mach_parts, mach_part_alts, mach_channels, mach_prices, mach_specs,
mach_tenders, mach_customs.

Запуск: python zip/tools/r1700_sql.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SRC = ROOT / "data" / "r1700.json"
OUT = ROOT / "supabase" / "seed_r1700.sql"


def q(v) -> str:
    """Литерал SQL. None и пустая строка — null, чтобы не плодить пустые значения."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).strip()
    if not s:
        return "null"
    return "'" + s.replace("'", "''") + "'"


def ins(table: str, cols: list[str], rows: list[list], conflict: str | None = None) -> str:
    """Один insert на таблицу пачками по 200 строк — большие VALUES тяжелее плана."""
    if not rows:
        return f"-- {table}: нет строк\n"
    out = []
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        vals = ",\n  ".join("(" + ", ".join(q(v) for v in r) + ")" for r in chunk)
        stmt = f"insert into {table} ({', '.join(cols)}) values\n  {vals}\n"
        if conflict:
            upd = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in conflict.split(", "))
            stmt += f"on conflict ({conflict}) do update set {upd};\n" if upd else \
                    f"on conflict ({conflict}) do nothing;\n"
        else:
            stmt += ";\n"
        out.append(stmt)
    return "\n".join(out)


def build() -> str:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    mk = d["machine"]["key"]
    m = d["machine"]
    L = [
        f"-- Досье машины {mk} для базы ЗИП. Сгенерировано zip/tools/r1700_sql.py",
        f"-- из zip/data/r1700.json (сборка {d['updated']}). Руками не править: правь досье и пересобери.",
        "-- Идемпотентно: повторный прогон обновляет строки, а не двоит их.",
        "-- Применение: Actions → «ZIP base — apply DB migrations», либо",
        "--   psql \"$SUPABASE_DB_URL\" -v ON_ERROR_STOP=1 -f zip/supabase/seed_r1700.sql",
        "",
        "begin;",
        "",
    ]

    L.append(ins("mach_machines",
                 ["machine_key", "name", "brand", "kind", "family", "note", "updated"],
                 [[mk, m["name"], m["brand"], m["kind"], m["family"], m["why"], d["updated"]]],
                 conflict="machine_key"))

    # документация
    L.append(ins("mach_docs",
                 ["machine_key", "form", "title", "kind", "lang", "covers", "media",
                  "where_get", "url", "price", "confidence", "verdict"],
                 [[mk, x.get("form"), x.get("title_ru"), x.get("kind"), x.get("lang"), x.get("covers"),
                   x.get("media"), x.get("where"), x.get("url"), x.get("price"),
                   x.get("confidence"), x.get("verdict")] for x in d.get("docs") or []
                  if str(x.get("form") or "").strip()],
                 conflict="machine_key, form"))

    # запчасти: перед вставкой снимаем дубли по нормализованному номеру
    seen, prows = set(), []
    for p in d["parts"]:
        if p["pn_norm"] in seen:
            continue
        seen.add(p["pn_norm"])
        prows.append([mk, p["pn"], p["pn_norm"], p.get("name_ru"), p.get("name_en"), p.get("node"),
                      p.get("applic"), p.get("qty"), p.get("interval"), p.get("price_usd"),
                      p.get("price_eur_min"), p.get("price_eur_max"),
                      ", ".join(p.get("kv") or []) or None, p.get("position_id"),
                      p.get("bitrix_status"), p.get("confidence"), p.get("verdict"),
                      " | ".join(p.get("sources") or []) or None, p.get("note")])
    L.append(ins("mach_parts",
                 ["machine_key", "pn", "pn_norm", "name_ru", "name_en", "node", "applic", "qty",
                  "interval_h", "price_usd", "price_eur_min", "price_eur_max", "kv", "position_id",
                  "bitrix_status", "confidence", "verdict", "sources", "note"],
                 prows, conflict="machine_key, pn_norm"))

    # аналоги
    seen, arows = set(), []
    for a in d["alts"]:
        key = (a["pn_norm"], a["brand"], a["alt_pn_norm"])
        if key in seen or not a["alt_pn_norm"]:
            continue
        seen.add(key)
        arows.append([mk, a["pn_norm"], a["brand"], a["alt_pn"], a["alt_pn_norm"],
                      a.get("kind"), a.get("note")])
    L.append(ins("mach_part_alts",
                 ["machine_key", "pn_norm", "brand", "alt_pn", "alt_pn_norm", "kind", "note"],
                 arows, conflict="machine_key, pn_norm, brand, alt_pn_norm"))

    # каналы: дилеры, заводы неоригинала, торговцы
    seen, crows = set(), []
    for o in d["orgs"]:
        key = (o["org"], o["slice"])
        if key in seen:
            continue
        seen.add(key)
        crows.append([mk, o["org"], o["slice"], o.get("kind"), o.get("country"), o.get("city"),
                      o.get("role"), o.get("brands"), o.get("site"), o.get("email"), o.get("phone"),
                      o.get("stock"), o.get("note"), o.get("source"), o.get("confidence"), o.get("verdict")])
    L.append(ins("mach_channels",
                 ["machine_key", "org", "lane", "kind", "country", "city", "role", "brands",
                  "site", "email", "phone", "stock", "note", "source", "confidence", "verdict"],
                 crows, conflict="machine_key, org, lane"))

    # цены, параметры, торги, таможня — наборы без естественного ключа: перезаливаем целиком
    L.append(f"delete from mach_prices  where machine_key = {q(mk)};")
    L.append(ins("mach_prices",
                 ["machine_key", "pn", "name", "tier", "brand", "price", "currency", "seller",
                  "region", "dt", "url", "confidence", "verdict"],
                 [[mk, x.get("pn"), x.get("name_ru") or x.get("name"), x.get("tier"), x.get("brand"),
                   x.get("price"), x.get("currency"), x.get("seller"), x.get("region"),
                   x.get("date"), x.get("url"), x.get("confidence"), x.get("verdict")]
                  for x in d.get("prices") or []]))

    L.append(f"delete from mach_specs   where machine_key = {q(mk)};")
    L.append(ins("mach_specs",
                 ["machine_key", "param", "value", "unit", "variant", "source", "confidence"],
                 [[mk, x.get("param"), x.get("value"), x.get("unit"), x.get("variant"),
                   x.get("source"), x.get("confidence")] for x in d.get("specs") or []
                  if str(x.get("param") or "").strip()]))

    t = d["tenders"]
    trows = [[mk, "площадка", x.get("name"), x.get("what"), x.get("how"), x.get("site") or x.get("source"),
              x.get("confidence")] for x in t.get("platforms") or []]
    trows += [[mk, "эксплуатант", x.get("org"), x.get("region"), x.get("machines"), x.get("source"),
               x.get("confidence") or "med"] for x in t.get("owners") or []]
    trows += [[mk, "требование", x.get("step"), x.get("why"), None, x.get("source"), "med"]
              for x in t.get("checklist") or []]
    L.append(f"delete from mach_tenders where machine_key = {q(mk)};")
    L.append(ins("mach_tenders", ["machine_key", "kind", "name", "detail", "extra", "source", "confidence"],
                 trows))

    L.append(f"delete from mach_customs where machine_key = {q(mk)};")
    L.append(ins("mach_customs",
                 ["machine_key", "dt", "importer", "inn", "exporter", "origin", "incoterms",
                  "place", "hs10", "pn", "descr", "usd_kg", "src"],
                 [[mk, x.get("date"), x.get("importer"), x.get("inn"), x.get("exporter"), x.get("origin"),
                   x.get("incoterms"), x.get("place"), x.get("hs10"), x.get("pn"), x.get("desc"),
                   x.get("usd_kg"), x.get("src")] for x in d["own"]["customs"]["rows"]]))

    L += ["", "commit;", ""]
    counts = {
        "деталей": len(prows), "аналогов": len(arows), "каналов": len(crows),
        "документов": len(d.get("docs") or []), "цен": len(d.get("prices") or []),
        "параметров": len(d.get("specs") or []), "торгов": len(trows),
        "таможня": len(d["own"]["customs"]["rows"]),
    }
    L.append("-- " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(L) + "\n", counts


def main() -> int:
    sql, counts = build()
    OUT.write_text(sql, encoding="utf-8")
    print(f"{OUT.relative_to(REPO)}: {OUT.stat().st_size:,} байт")
    print("  " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print("  залить: Actions → «ZIP base — apply DB migrations» (сначала схема, потом этот файл)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
