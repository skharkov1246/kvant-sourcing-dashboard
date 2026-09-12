#!/usr/bin/env python3
"""Заполняемость цепочки портала по всем направлениям → data/chain_coverage.json.

ЦЕЛЬ (CLAUDE.md, раздел «Куда мы идём»): инженерный портал ремонта и сервиса
динамического оборудования. Пользователь проходит цепочку целиком:

    машина → узел → признак → дефект → ремонтное решение → запчасть → исполнитель

Этот счётчик отвечает на единственный вопрос: где в этой цепочке у нас пусто.
Считает по фактическим файлам репозитория, ничего не оценивает экспертно и ничего
не досчитывает. Ноль в клетке — это честный ноль, а не «данные где-то есть».

Правило приоритета отсюда же: пустое звено важнее улучшения заполненного.

Запуск:  python scripts/build_chain_coverage.py
         python scripts/build_chain_coverage.py --check
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "chain_coverage.json"

# Звенья цепочки в порядке прохождения пользователем.
LINKS = [
    ("machine", "Машина", "модель опознаётся и описана"),
    ("node", "Узел", "состав машины разобран по узлам"),
    ("symptom", "Признак", "по чему видно, что машина нездорова"),
    ("defect", "Дефект", "каталог дефектов: что ломается и почему"),
    ("repair", "Ремонтное решение", "чем восстанавливают, границы применимости"),
    ("part", "Запчасть", "номенклатура с номерами, готовая к запросу"),
    ("maker", "Изготовитель", "кто делает деталь, помимо владельца конструкции"),
    ("contractor", "Исполнитель", "кому отдать работы по ремонту"),
]

SEGMENTS = [
    ("gtu", "ГТУ — газотурбинные"),
    ("gpu", "ГПУ — газопоршневые"),
    ("gsho", "ГШО — горно-шахтное"),
    ("recip", "Поршневые компрессоры"),
    ("pumps", "Насосы"),
    ("instrum", "КИПиА"),
    ("electro", "Электротехника и приводы"),
]


def load(rel, default=None):
    p = ROOT / rel
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def n(x):
    return len(x) if hasattr(x, "__len__") else 0


def nkey(name: str) -> str:
    """Ключ компании — тот же, что в общем словаре (scripts/build_dict.py)."""
    import re
    s = str(name or "").lower().replace("ё", "е")
    s = re.sub(r"\b(ооо|оао|зао|пао|ао|llc|ltd|inc|gmbh|s\.p\.a|spa|co|corp|company|"
               r"limited|holding|group|a/s|ab|bv|nv|sas|sa|plc|pte|kg|ag)\b", " ", s)
    return re.sub(r"[^a-z0-9а-я]+", "", s)[:40]


# Партномера по направлениям. Берутся из ЯВНЫХ полей номера, а не угадываются
# регулярным выражением по тексту: иначе в номера попадают обозначения машин
# (QSV91G) и марки материалов. Считаются уникальные номера, а не строки — по той
# же причине, по которой изготовители считаются компаниями: сумма строк разных
# файлов несравнима между направлениями.
PN_FIELDS = {
    "gtu": [("gt/data/pn_db.json", "rows", ["pn", "mpn"]),
            ("gt/data/pn_catalog.json", "rows", ["pn"])],
    "gpu": [("gpu/data/demand.json", "rows", ["pn"]),
            ("gpu/data/motortech_cross.json", "records", ["part", "motortech", "cross"]),
            ("gpu/data/analogs.json", "families", ["oem_pns"])],
    "gsho": [("zip/data/positions.json", None, ["catalog_no"]),
             ("zip/data/telsmith_3858.json", "need", ["oem"]),
             ("zip/data/telsmith_3858.json", "catalog", ["oem"])],
}
# Сквозной справочник деталей: раздел указан полем section, номер — полями
# ниже. Он идёт в ТО ЖЕ множество номеров, а не отдельной добавкой сверху:
# иначе одна и та же деталь считается дважды — под своим номером и под номером
# бренда из реестра направления.
ITEM_MASTER_SECTION = {"gtu": "ГТУ", "gsho": "ЗИП ГШО"}


def unique_pns(seg: str, load_fn):
    """Множество партномеров направления и файлы, из которых они собраны."""
    keys, srcs = set(), []
    for rel, path, fields in PN_FIELDS.get(seg, []):
        data = load_fn(rel)
        if data is None:
            continue
        if path:
            data = data.get(path) if isinstance(data, dict) else None
        if not data:
            continue
        got = set()
        for row in data:
            if not isinstance(row, dict):
                continue
            for f in fields:
                v = row.get(f)
                for one in (v if isinstance(v, list) else [v]):
                    k = norm_pn(one)
                    if len(k) >= 4:          # короче — это не номер, а индекс строки
                        got.add(k)
        if got:
            keys |= got
            if rel not in srcs:
                srcs.append(rel)

    section = ITEM_MASTER_SECTION.get(seg)
    if section:
        im = load_fn("pnw/data/item_master.json") or {}
        nums = load_fn("pnw/data/numbers.json") or {}
        kv_of_section = {it["kv"] for it in im.get("items", []) if it.get("section") == section}
        got = {norm_pn(r.get("number")) for r in nums.get("rows", [])
               if r.get("kv") in kv_of_section and r.get("kind") != "свой"}
        got = {k for k in got if len(k) >= 4}
        if got:
            keys |= got
            srcs.append("pnw/data/numbers.json")
    return keys, srcs


def norm_pn(pn) -> str:
    import re
    return re.sub(r"[^A-Z0-9А-Я]", "", str(pn or "").upper())


# Реестры компаний по направлениям. Считаются УНИКАЛЬНЫЕ компании, а не строки:
# одна и та же компания лежит в нескольких реестрах и в тысячах связей с позициями.
MAKER_FILES = {
    "gtu": [("gt/data/suppliers.json", None, "name"),
            ("gt/data/heavy_suppliers.json", "rows", "name"),
            ("gt/data/research_suppliers.json", "rows", "name"),
            ("gt/data/tfs_subsuppliers.json", "rows", "name"),
            ("gt/data/sgt400_checklist.json", "rows", "name"),
            ("gt/data/dossiers.json", "dossiers", "__key__"),
            ("gt/data/site_profiles.json", "profiles", "__key__"),
            ("gt/data/bitrix_supplier_sites.json", "confirmed", "__key__")],
    # Вложенный реестр: systems[].makers[].name — путь помечен двоеточием.
    "gpu": [("gpu/data/suppliers.json", "companies", "name"),
            ("gpu/data/subsuppliers.json", "systems:makers", "name")],
    "gsho": [("zip/data/odm_suppliers.json", None, "name"),
             ("zip/data/telsmith_suppliers.json", "suppliers", "name"),
             ("zip/data/supplier_crm.json", "suppliers", "name")],
}


def unique_makers(seg: str, load_fn):
    """Множество компаний направления и файлы, из которых они собраны."""
    keys, srcs = set(), []
    for rel, path, field in MAKER_FILES.get(seg, []):
        data = load_fn(rel)
        if data is None:
            continue
        if path and ":" in path:
            outer, inner = path.split(":", 1)
            rows = data.get(outer, []) if isinstance(data, dict) else []
            data = [m for grp in rows if isinstance(grp, dict) for m in grp.get(inner, [])]
        elif path:
            data = data.get(path) if isinstance(data, dict) else None
        if data is None:
            continue
        names = list(data) if (field == "__key__" and isinstance(data, dict)) else [
            x.get(field) for x in data if isinstance(x, dict)]
        got = {nkey(x) for x in names if x and nkey(x)}
        if got:
            keys |= got
            srcs.append(rel)
    return keys, srcs


def counts() -> dict:
    """Числитель по каждой клетке: что в файлах РЕАЛЬНО есть."""
    c = {s: {k: {"n": 0, "draft": 0, "src": []} for k, _t, _d in LINKS} for s, _ in SEGMENTS}

    def put(seg, link, num, src, draft=False):
        """draft=True — данные есть, но проверку не проходили: отдельный счёт."""
        if not num:
            return
        cell = c[seg][link]
        cell["draft" if draft else "n"] += num
        if src not in cell["src"]:
            cell["src"].append(src)

    # ── ГТУ
    gm = load("gt/data/models.json", {})
    put("gtu", "machine", sum(n(f.get("models")) for f in gm.get("families", [])), "gt/data/models.json")
    mach = load("dict/machine.json", {})
    put("gtu", "machine", sum(1 for m in mach.get("records", []) if m.get("segment") == "gtu"),
        "dict/machine.json")
    gp = load("gt/data/parts.json", {})
    put("gtu", "node", n(gp.get("systems")), "gt/data/parts.json")

    # ── ГПУ
    put("gpu", "machine", n(load("gpu/data/machines.json", {}).get("machines")), "gpu/data/machines.json")
    put("gpu", "node", n(load("gpu/data/parts.json", {}).get("systems")), "gpu/data/parts.json")

    # ── ГШО
    tel = load("zip/data/telsmith_3858.json", {})
    put("gsho", "machine", 1 if tel.get("machine") else 0, "zip/data/telsmith_3858.json")
    put("gsho", "machine", n(load("zip/data/machines.json", {}).get("machines")), "zip/data/machines.json")
    put("gsho", "node", n({r.get("node") for r in tel.get("catalog", []) if r.get("node")}), "zip/data/telsmith_3858.json")
    mat = load("zip/data/material_strategy.json", [])
    put("gsho", "repair", n([x for x in (mat or []) if isinstance(x, dict)]), "zip/data/material_strategy.json")

    # ── Поршневые компрессоры: разведка направления. Считаются только факты
    # с вердиктом проверки — «скептик не сослался» и «не проверялся» в звено
    # не идут: неподтверждённое не заполняет клетку.
    rc = load("zip/data/recip_recon.json", {})
    if rc:
        OK = {"подтверждено", "частично"}
        ang = {a["key"]: a for a in rc.get("angles", [])}
        checked = lambda a: [f for f in ang.get(a, {}).get("findings", []) if f.get("verdict") in OK]
        put("recip", "machine", len(checked("machine")), "zip/data/recip_recon.json")
        put("recip", "node", len(checked("bom")), "zip/data/recip_recon.json")
        put("recip", "part", sum(len(checked(a)) for a in ("valves", "rings", "metal")),
            "zip/data/recip_recon.json")
        put("recip", "repair", len(checked("ru_service")), "zip/data/recip_recon.json")
        comp = {nkey(c.get("name")) for a in rc.get("angles", [])
                for c in a.get("companies", []) if c.get("name")}
        c["recip"]["maker"]["n"] = len(comp)
        c["recip"]["maker"]["src"] = ["zip/data/recip_recon.json"]

    # ── диагностика: признак и дефект. Единственные два звена, пустые везде.
    # Привязка к направлению по области разведки, плюс два узла-исключения:
    # электрическая машина и КИПиА встречаются внутри разведки турбомашин,
    # но принадлежат своим направлениям. Общая часть (вибро- и невибрационные
    # методы) машинно-независима и потому идёт во ВСЕ направления — это не
    # добивка числа, а то, чем эти методы и являются: нормы ISO 20816 и анализ
    # масла одинаковы для турбины, насоса и дробилки.
    sym = load("dict/symptom.json", {})
    if sym.get("defect_rows"):
        # Общая часть идёт только во вращающиеся и возвратно-поступательные машины.
        # КИПиА и электротехника получают ТОЛЬКО свои узлы (правило NODE_SEG ниже):
        # дефекты подшипника качения к датчику давления отношения не имеют, и
        # раздача им общего блока была бы добивкой числа.
        SCOPE_SEG = {"turbo": ["gtu"], "pumps": ["pumps"], "recip": ["recip"],
                     "common": ["gtu", "gpu", "gsho", "recip", "pumps"]}
        NODE_SEG = {"Электрическая машина и питание": "electro",
                    "КИП, САУ, защиты": "instrum"}
        CHECKED = {"подтверждено", "частично"}
        REJECTED = {"опровергнуто"}  # забракованное скептиком не данные, а урок
        rows = sym["defect_rows"]
        # признак → множество индексов дефектов
        sym_of = {}
        for r in sym["records"]:
            for i in r["defects"]:
                sym_of.setdefault(i, set()).add(r["key"])
        d_cnt = {s: {"n": 0, "draft": 0} for s, _ in SEGMENTS}
        s_set = {s: {"n": set(), "draft": set()} for s, _ in SEGMENTS}
        for i, r in enumerate(rows):
            segs = set(SCOPE_SEG.get(r["scope"], []))
            if r["node"] in NODE_SEG:
                segs.add(NODE_SEG[r["node"]])
            if r["verdict"] in REJECTED:
                continue
            bucket = "n" if r["verdict"] in CHECKED else "draft"
            for seg in segs:
                d_cnt[seg][bucket] += 1
                s_set[seg][bucket] |= sym_of.get(i, set())
        for seg, _ in SEGMENTS:
            put(seg, "defect", d_cnt[seg]["n"], "zip/data/diagnostics_recon.json")
            put(seg, "defect", d_cnt[seg]["draft"], "zip/data/diagnostics_recon.json", draft=True)
            # признак считается один раз: проверенный не должен дублироваться в черновике
            ok = s_set[seg]["n"]
            put(seg, "symptom", len(ok), "dict/symptom.json")
            put(seg, "symptom", len(s_set[seg]["draft"] - ok), "dict/symptom.json", draft=True)

    # ── изготовители: уникальные компании по каждому направлению
    for seg in ("gtu", "gpu", "gsho"):
        keys, srcs = unique_makers(seg, lambda rel: load(rel))
        for src in srcs:
            put(seg, "maker", 0, src)
        c[seg]["maker"]["n"] = len(keys)
        c[seg]["maker"]["src"] = srcs

    # ── сквозные наборы: раскладываются по направлениям по полю раздела

    # ── запчасти: уникальные партномера по каждому направлению
    for seg in ("gtu", "gpu", "gsho"):
        keys, srcs = unique_pns(seg, lambda rel: load(rel))
        if keys:
            c[seg]["part"]["n"] = len(keys)
            c[seg]["part"]["src"] = srcs

    # ── словарь: изготовители по рёбрам цепочки (сегмент словарь не знает,
    # поэтому рёбра считаются общим фондом и в клетки направлений не идут)
    ch = load("dict/chain.json", {})
    mach = load("dict/machine.json", {})
    other = [m for m in mach.get("records", []) if m.get("segment") == "other"]
    common = {"chain_edges": n(ch.get("records")), "chain_makers": ch.get("makers", 0),
              "oem_keys": load("dict/oem.json", {}).get("count", 0),
              "machines_total": mach.get("count", 0),
              "machines_foreign_segment": len(other),
              "machines_foreign_note": "Машины, найденные внутри базы ГТУ, но относящиеся к "
                                       "направлению, которого у нас нет: буровое и нефтепромысловое "
                                       "оборудование — насосы, превенторы, цементировочные агрегаты, "
                                       "верхние приводы. Кандидат в новое направление портала."}
    return c, common


def build() -> dict:
    c, common = counts()
    rows = []
    for sid, stitle in SEGMENTS:
        cells = []
        for key, title, what in LINKS:
            cell = c[sid][key]
            state = "есть" if cell["n"] else ("черновик" if cell["draft"] else "пусто")
            cells.append({"link": key, "title": title, "what": what,
                          "n": cell["n"], "draft": cell["draft"], "sources": cell["src"],
                          "state": state})
            # Клетка «есть 2 / черновик 51» не должна читаться как «есть 2».
            # Черновик показывается всегда, счётом заполненных клеток не становится.
        filled = sum(1 for x in cells if x["n"])
        rows.append({"segment": sid, "title": stitle, "cells": cells,
                     "filled": filled, "of": len(LINKS),
                     "pct": round(100 * filled / len(LINKS))})
    rows.sort(key=lambda x: x["filled"])

    # Где пусто во ВСЕХ направлениях — там звено не начато вовсе.
    empty_everywhere = [
        {"link": k, "title": t, "what": w}
        for k, t, w in LINKS
        if all(next(c for c in r["cells"] if c["link"] == k)["state"] == "пусто" for r in rows)
    ]
    from datetime import date
    return {
        "updated": date.today().isoformat(),
        "note": "Заполняемость цепочки портала: машина → узел → признак → дефект → ремонтное "
                "решение → запчасть → изготовитель → исполнитель. Считается "
                "scripts/build_chain_coverage.py по фактическим файлам РЕПОЗИТОРИЯ.",
        "scope": "ЧТО СЧИТАЕТСЯ. Только файлы репозитория: gt/data, gpu/data, zip/data, pnw/data, "
                 "dict/. Инженерная библиотека живёт в закрытой Supabase (таблицы lib_*), и этот "
                 "счётчик её НЕ ВИДИТ — ключа базы в сборке нет. Поэтому ноль в клетке означает "
                 "«нет в файлах репозитория», а не «нет нигде»: часть звеньев закрыта именно "
                 "в библиотеке. Её состояние ведётся отдельно — таблица звеньев в CLAUDE.md и "
                 "scripts/library_report.py, которому нужен SUPABASE_DB_URL. ЧЕРНОВИК: число со "
                 "знаком ~ — строки, которые собраны, но проверку скептиком не проходили. Они не "
                 "засчитываются в заполненные клетки: заполненной клетка становится по "
                 "проверенным данным, а не по собранным.",
        "goal": "Инженерный портал ремонта и сервиса динамического оборудования (CLAUDE.md).",
        "priority_rule": "Пустое звено важнее улучшения заполненного.",
        "links": [{"link": k, "title": t, "what": w} for k, t, w in LINKS],
        "segments": rows,
        "empty_everywhere": empty_everywhere,
        "common": common,
        "summary": {
            "segments": len(rows),
            "links": len(LINKS),
            "cells_filled": sum(r["filled"] for r in rows),
            "cells_draft_only": sum(1 for r in rows for x in r["cells"]
                                    if x["state"] == "черновик"),
            "cells_with_draft": sum(1 for r in rows for x in r["cells"] if x["draft"]),
            "draft_rows": sum(x["draft"] for r in rows for x in r["cells"]),
            "cells_total": len(rows) * len(LINKS),
            "links_not_started": len(empty_everywhere),
        },
    }


def main() -> int:
    fresh = build()
    text = json.dumps(fresh, ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        strip = lambda o: {k: v for k, v in o.items() if k != "updated"}
        old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
        if old is None or strip(old) != strip(fresh):
            print("✗ data/chain_coverage.json устарел — выполните: "
                  "python scripts/build_chain_coverage.py", file=sys.stderr)
            return 1
        s = fresh["summary"]
        print(f"✓ заполняемость цепочки актуальна: {s['cells_filled']}/{s['cells_total']} клеток")
        return 0
    OUT.write_text(text, encoding="utf-8")
    s = fresh["summary"]
    print(f"✓ data/chain_coverage.json: {s['cells_filled']}/{s['cells_total']} клеток заполнено, "
          f"звеньев не начато вовсе: {s['links_not_started']} "
          f"({', '.join(x['title'] for x in fresh['empty_everywhere']) or '—'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
