#!/usr/bin/env python3
"""Машины, узлы и связка «запчасть → машина» — начало цепочки портала.

ЗАЧЕМ. Цепочка «модель → узел → диагностика → дефект → ремонтное решение →
запчасть → исполнитель» начиналась с пустоты: справочника машин не было, узлов
не было, а деталь знала машину строкой. Из-за этого нельзя было ответить на
первый же инженерный вопрос — «что ставится на SGT-400 и кто это делает».

ОТКУДА ДАННЫЕ. Машины — gt/data/models.json (27 машин четырьмя семействами,
с наследными именами: SGT-400 = Cyclone) и models из gt/data/ansaldo.json.
Узлы — gt/data/parts.json: 8 систем и 38 компонентов с критичностью и оценкой
доступности помимо OEM. Запчасти — gt/data/pn_db.json: 12 442 партномера с
машиной, изготовителем и описанием; плюс gt/data/pn_catalog.json (187 позиций
из публичных каталогов).

ПОЧЕМУ МАШИНА ИЗ ПАРТНОМЕРОВ ДОБАВЛЯЕТСЯ В СПРАВОЧНИК НЕ ЛЮБАЯ. Поле «машина»
заполняют руками: там встречаются имя изготовителя вместо модели и вовсе не
турбина. Правило отбора — library/equipment.looks_like_machine, порог по
встречаемости — MIN_SEEN. Всё, что не прошло, остаётся без ребра и попадает в
счётчик «не опознано»: это честнее, чем завести машину «Solar (сток)».

ЧТО ПРОИСХОДИТ С УЖЕ ЗАГРУЖЕННЫМ КАТАЛОГОМ. Каталог ЗИП (752 позиции) проверен
руками, партномера — сводка. При совпадении номера побеждает каталог: новые
поля подставляются только в пустые (coalesce), source дописывается.

БЕЗ APPLY=1 идёт вхолостую.

    python library/load_equipment.py
    SUPABASE_DB_URL=... APPLY=1 python library/load_equipment.py

В журнал идут только агрегаты.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equipment as eq  # noqa: E402  (после sys.path)
from segments import classify  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
# Сколько раз имя должно встретиться, чтобы завести машину в справочник по одним
# только партномерам. Ниже порога — опечатка или разовая запись.
MIN_SEEN = int(os.environ.get("MIN_SEEN", "10"))
NOT_KEY = re.compile(r"[^0-9a-zа-яё]+")


def load(path: str, key: str | None = None):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return [] if key else {}
    d = json.load(open(full, encoding="utf-8"))
    return (d.get(key) or []) if key else d


def part_key(pn: str) -> str:
    return NOT_KEY.sub("", (pn or "").lower().replace("ё", "е"))[:80]


def num(v, w=9):
    return f"{v:,}".replace(",", " ").rjust(w)


# ─── машины ──────────────────────────────────────────────────────────────────
def build_models() -> tuple[dict[str, dict], dict[str, str]]:
    """Справочник машин и указатель «написание → ключ машины»."""
    models: dict[str, dict] = {}
    alias: dict[str, str] = {}

    def add(name, **kw):
        key = eq.norm_model(name)
        if not key:
            return None
        rec = models.setdefault(key, {"id": key, "name": name, "aliases": set()})
        for k, v in kw.items():
            if v and not rec.get(k):
                rec[k] = v
        rec["aliases"].add(name)
        alias.setdefault(key, key)
        return key

    for fam in load("gt/data/models.json").get("families", []):
        for m in fam.get("models", []):
            key = add(m["name"], oem=oem_of(fam["id"]), family=fam["id"],
                      family_title=fam["title"], legacy=(m.get("legacy") or "").strip() or None,
                      power=m.get("power"), efficiency=m.get("eff"), shafts=m.get("shafts"),
                      use_case=m.get("use"), note=m.get("zip_note"),
                      source="справочник моделей ГТУ")
            legacy = (m.get("legacy") or "").strip()
            if key and legacy:
                # Наследное имя ключом не сводится («Cyclone» ≠ «SGT-400»), но в
                # каталогах aftermarket машину зовут именно так. Псевдоним — то
                # единственное, что связывает два имени одной машины.
                models[key]["aliases"].add(legacy)
                for part in eq.split_machines(legacy):
                    alias.setdefault(eq.norm_model(part), key)

    for m in load("gt/data/ansaldo.json").get("models", []):
        add(m["name"], oem="Ansaldo Energia", family="ansaldo",
            family_title="Ansaldo Energia / семейство V-машин",
            legacy=(m.get("legacy") or "").strip() or None, power=m.get("power"),
            efficiency=m.get("eff"), shafts=m.get("stages"), note=m.get("combustor"),
            source="разведка Ansaldo")
    return models, alias


def oem_of(family: str) -> str:
    return {"sgt": "Siemens Energy", "finspong": "Siemens Energy",
            "solar": "Solar Turbines", "heavy": "GE / Siemens / Alstom"}.get(family, "")


# ─── узлы ────────────────────────────────────────────────────────────────────
def build_units() -> dict[str, dict]:
    units: dict[str, dict] = {}
    for s in load("gt/data/parts.json").get("systems", []):
        units[s["id"]] = {"id": s["id"], "parent_id": None, "name": s["title"],
                          "name_en": None, "crit": s.get("crit"),
                          "aftermarket": s.get("aftermarket"), "note": None,
                          "source": "номенклатура ЗИП ГТУ"}
        for c in s.get("components", []):
            cid = f"{s['id']}.{eq.slug_en(c.get('en', ''))}"
            if cid.endswith("."):
                continue
            units[cid] = {"id": cid, "parent_id": s["id"], "name": c["ru"],
                          "name_en": c.get("en"), "crit": c.get("crit"),
                          "aftermarket": None, "note": c.get("note"),
                          "source": "номенклатура ЗИП ГТУ"}
    for uid, ru, en, crit in eq.EXTRA_UNITS:
        units.setdefault(uid, {"id": uid, "parent_id": None, "name": ru, "name_en": en,
                               "crit": crit, "aftermarket": None, "note": None,
                               "source": "разметка партномеров"})
    return units


# ─── запчасти из партномеров ─────────────────────────────────────────────────
def build_parts(models, alias, units):
    rows = load("gt/data/pn_db.json").get("rows", [])
    cat = load("gt/data/pn_catalog.json").get("rows", [])
    seen_mach: Counter = Counter()
    for r in rows:
        for m in eq.split_machines(r.get("mach") or ""):
            seen_mach[eq.norm_model(m)] += 1
    # Машины, которых нет в справочнике инженеров, но которые в партномерах
    # встречаются постоянно (LM2500 — 3 664 позиции). Заводим по правилу отбора.
    добавлено = 0
    имена = {}
    for r in rows:
        for m in eq.split_machines(r.get("mach") or ""):
            имена.setdefault(eq.norm_model(m), m)
    oem_of_key: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for m in eq.split_machines(r.get("mach") or ""):
            if r.get("oem"):
                oem_of_key[eq.norm_model(m)][r["oem"]] += 1
    for key, n in seen_mach.most_common():
        if key in alias or n < MIN_SEEN or not eq.looks_like_machine(имена[key], key):
            continue
        oem = oem_of_key[key].most_common(1)
        models[key] = {"id": key, "name": имена[key], "aliases": {имена[key]},
                       "oem": oem[0][0] if oem else None, "family": None,
                       "source": "имена машин из партномеров"}
        alias[key] = key
        добавлено += 1

    parts: dict[str, dict] = {}
    edges: set[tuple[str, str]] = set()
    не_опознано: Counter = Counter()
    unit_stat: Counter = Counter()

    def take(pn, desc, oem, mach, seg, qty, pat, source):
        key = part_key(pn)
        if not key or not (desc or "").strip():
            return
        текст = f"{seg or ''} {desc or ''}"
        # Ярлык инженеров сильнее текстового правила: это их разметка, а не догадка.
        по_ярлыку = eq.unit_of_seg(seg or "")
        unit = по_ярлыку or eq.unit_of(desc or "")
        unit = unit if unit in units else None
        rec = parts.get(key)
        if rec is None:
            rec = parts[key] = {
                "id": key, "catalog_no": str(pn)[:120], "name": str(desc)[:400],
                "oem": (oem or None) and str(oem)[:200], "model": str(mach or "")[:600] or None,
                "category": (seg or None) and str(seg)[:120],
                "segment_id": classify(текст), "unit_id": unit,
                "unit_rule": ("разметка" if по_ярлыку else "описание") if unit else None,
                "pn_pattern": str(pat or "")[:120] or None,
                "qty_demand": qty if isinstance(qty, (int, float)) else None,
                "source": source,
            }
            unit_stat[unit or "—"] += 1
        else:
            if qty and isinstance(qty, (int, float)):
                rec["qty_demand"] = (rec["qty_demand"] or 0) + qty
            if not rec["unit_id"] and unit:
                rec["unit_id"] = unit
                rec["unit_rule"] = "разметка" if по_ярлыку else "описание"
            if source not in rec["source"]:
                rec["source"] += " · " + source
        for m in eq.split_machines(mach or ""):
            k = alias.get(eq.norm_model(m))
            if k:
                edges.add((key, k))
            elif m:
                не_опознано[m] += 1

    for r in rows:
        take(r.get("pn"), r.get("desc"), r.get("oem"), r.get("mach"), r.get("seg"),
             r.get("qty"), r.get("pat"), "партномера потребности")
    for r in cat:
        take(r.get("pn"), r.get("desc"), None, r.get("model"), r.get("system"),
             None, None, "публичные каталоги")
    return parts, edges, не_опознано, unit_stat, добавлено, unit_stat


def build_fleet(alias: dict[str, str]) -> dict[str, dict]:
    """Парк: площадка, владелец, машина. Модель в источнике записана как
    «V64.3A (Siemens)» — завод в скобках, поэтому ключ ищется по очищенному
    имени, а исходная запись сохраняется целиком."""
    import re as _re
    fleet: dict[str, dict] = {}
    for r in load("gt/data/ansaldo.json").get("fleet_ru", []):
        площадка = _re.sub(r"<[^>]+>", " ", str(r.get("plant") or "")).strip()
        if not площадка:
            continue
        сырое = str(r.get("model") or "")
        ключ = None
        for имя in eq.split_machines(sub_tags(сырое)):
            ключ = alias.get(eq.norm_model(имя))
            if ключ:
                break
        ид = "парк." + NOT_KEY.sub("", площадка.lower())[:40]
        fleet[ид] = {"id": ид, "site": площадка[:300], "owner": str(r.get("owner") or "")[:200] or None,
                     "model_id": ключ, "model_raw": сырое[:200] or None,
                     "units": str(r.get("units") or "")[:40] or None,
                     "year": str(r.get("year") or "")[:40] or None,
                     "note": sub_tags(str(r.get("note") or ""))[:1000] or None,
                     "source": "парк V-машин в РФ, разведка Ansaldo"}
    return fleet


def sub_tags(s: str) -> str:
    import re as _re
    return " ".join(_re.sub(r"<[^>]+>", " ", s or "").split())


def main() -> int:
    models, alias = build_models()
    units = build_units()
    parts, edges, не_опознано, unit_stat, добавлено, _ = build_parts(models, alias, units)
    fleet = build_fleet(alias)

    print("=== машины ===")
    fam = Counter(m.get("family") or "из партномеров" for m in models.values())
    for f, n in fam.most_common():
        print(f"  {f:26}{num(n)}")
    print(f"  всего машин:{num(len(models))}   (из них по партномерам: {добавлено})")
    print(f"  наследных имён (SGT-400 = Cyclone): "
          f"{sum(1 for m in models.values() if m.get('legacy'))}")

    print("\n=== узлы ===")
    систем = sum(1 for u in units.values() if u["parent_id"] is None)
    print(f"  систем:{num(систем)}   компонентов:{num(len(units) - систем)}")
    крит = Counter(u.get("crit") or "—" for u in units.values())
    print(f"  по критичности: {dict(крит.most_common())}")

    print("\n=== запчасти из партномеров ===")
    print(f"  различных номеров:{num(len(parts))}")
    ист = Counter(p["source"] for p in parts.values())
    for k, n in ист.most_common():
        print(f"    {k:28}{num(n)}")
    сузлом = sum(1 for p in parts.values() if p["unit_id"])
    print(f"  с определённым узлом:{num(сузлом)}   {сузлом / len(parts) * 100:.1f}%")
    правило = Counter(p["unit_rule"] or "—" for p in parts.values())
    print(f"  чем определён узел: {dict(правило.most_common())}")
    print("  топ узлов:")
    for u, n in unit_stat.most_common(12):
        if u != "—":
            print(f"    {units[u]['name'][:44]:46}{num(n)}")

    print("\n=== связка «запчасть → машина» ===")
    print(f"  рёбер:{num(len(edges))}")
    на_машину = Counter(k for _, k in edges)
    print(f"  машин со связями:{num(len(на_машину))}")
    for k, n in на_машину.most_common(10):
        print(f"    {models[k]['name'][:34]:36}{num(n)}")
    if не_опознано:
        print(f"  имён машин не опознано: {len(не_опознано)} написаний, "
              f"{sum(не_опознано.values())} упоминаний")
        for m, n in не_опознано.most_common(5):
            print(f"    {m[:44]:46}{num(n)}")

    print("\n=== парк ===")
    print(f"  площадок:{num(len(fleet))}   машина опознана у "
          f"{sum(1 for f in fleet.values() if f['model_id'])}")
    владельцы = Counter(f["owner"] or "—" for f in fleet.values())
    print(f"  владельцев: {len(владельцы)}")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    import indexer
    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=900000")
    conn.autocommit = False
    with conn.cursor() as cur:
        indexer.ensure_segments(cur)
        psycopg2.extras.execute_values(cur, """
            insert into lib_models (id, name, oem, family, family_title, legacy, power,
                                    efficiency, shafts, use_case, aliases, note, source)
            values %s
            on conflict (id) do update set
              name = excluded.name, oem = coalesce(lib_models.oem, excluded.oem),
              family = coalesce(lib_models.family, excluded.family),
              family_title = coalesce(lib_models.family_title, excluded.family_title),
              legacy = coalesce(lib_models.legacy, excluded.legacy),
              power = coalesce(lib_models.power, excluded.power),
              efficiency = coalesce(lib_models.efficiency, excluded.efficiency),
              shafts = coalesce(lib_models.shafts, excluded.shafts),
              use_case = coalesce(lib_models.use_case, excluded.use_case),
              aliases = excluded.aliases, note = coalesce(lib_models.note, excluded.note),
              updated_at = now()""",
            [(m["id"], m["name"][:200], (m.get("oem") or None), m.get("family"),
              (m.get("family_title") or None), m.get("legacy"), m.get("power"),
              m.get("efficiency"), m.get("shafts"), m.get("use_case"),
              sorted(a[:120] for a in m["aliases"]), m.get("note"), m.get("source"))
             for m in models.values()], page_size=500)

        # Системы раньше компонентов: parent_id ссылается на ту же таблицу.
        for этап in (None, "дети"):
            строки = [(u["id"], u["parent_id"], u["name"][:300], u["name_en"], u["crit"],
                       u["aftermarket"], u["note"], u["source"])
                      for u in units.values()
                      if (u["parent_id"] is None) == (этап is None)]
            psycopg2.extras.execute_values(cur, """
                insert into lib_units (id, parent_id, name, name_en, crit, aftermarket,
                                       note, source)
                values %s
                on conflict (id) do update set
                  name = excluded.name, name_en = excluded.name_en, crit = excluded.crit,
                  aftermarket = excluded.aftermarket, note = excluded.note,
                  updated_at = now()""", строки, page_size=500)

        psycopg2.extras.execute_values(cur, """
            insert into lib_parts (id, catalog_no, name, oem, model, category, segment_id,
                                   unit_id, unit_rule, pn_pattern, qty_demand, source)
            values %s
            on conflict (id) do update set
              oem        = coalesce(lib_parts.oem, excluded.oem),
              model      = coalesce(lib_parts.model, excluded.model),
              category   = coalesce(lib_parts.category, excluded.category),
              segment_id = coalesce(lib_parts.segment_id, excluded.segment_id),
              unit_id    = coalesce(lib_parts.unit_id, excluded.unit_id),
              unit_rule  = coalesce(lib_parts.unit_rule, excluded.unit_rule),
              pn_pattern = coalesce(lib_parts.pn_pattern, excluded.pn_pattern),
              qty_demand = coalesce(lib_parts.qty_demand, excluded.qty_demand),
              -- position, а не like: в execute_values знак процента служебный,
              -- и даже в комментарии он ломает разбор запроса целиком.
              source     = case when position(excluded.source in lib_parts.source) > 0
                                then lib_parts.source
                                else lib_parts.source || ' · ' || excluded.source end,
              updated_at = now()""",
            [(p["id"], p["catalog_no"], p["name"], p["oem"], p["model"], p["category"],
              p["segment_id"], p["unit_id"], p["unit_rule"], p["pn_pattern"],
              p["qty_demand"], p["source"]) for p in parts.values()], page_size=500)

        psycopg2.extras.execute_values(cur, """
            insert into lib_fleet (id, site, owner, model_id, model_raw, units, year,
                                   note, source)
            values %s
            on conflict (id) do update set
              owner = excluded.owner, model_id = excluded.model_id,
              units = excluded.units, note = excluded.note, updated_at = now()""",
            [(f["id"], f["site"], f["owner"], f["model_id"], f["model_raw"], f["units"],
              f["year"], f["note"], f["source"]) for f in fleet.values()], page_size=200)

        psycopg2.extras.execute_values(cur, """
            insert into lib_part_models (part_id, model_id, source)
            values %s on conflict (part_id, model_id) do nothing""",
            [(a, b, "партномера потребности") for a, b in sorted(edges)], page_size=500)
        conn.commit()

        for t in ("lib_models", "lib_units", "lib_parts", "lib_part_models", "lib_fleet"):
            cur.execute(f"select count(*) from {t}")
            print(f"  {t:18}{num(cur.fetchone()[0])}")
    conn.close()
    print("\n✓ машины, узлы и связки загружены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
