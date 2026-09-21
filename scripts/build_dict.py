#!/usr/bin/env python3
"""Общий словарь баз данных — ПРОЕКЦИЯ, а не новое место хранения.

ЗАЧЕМ. Одни и те же сущности живут в семи подпроектах под разными именами полей:
производитель — это oem, brand, mk, maker или owner; узел — id, sys, seg, cat или
part_class; уровень доверия — conf, tier, confidence или prank, причём под одной
буквенной разметкой скрыты три несводимые шкалы. Пока соответствия нет, любой
сквозной вопрос («кто делает этот узел», «что мы знаем про эту машину») требует
ручного обхода файлов.

ЧЕМ ЭТО НЕ ЯВЛЯЕТСЯ. Не миграцией. Сборщик ТОЛЬКО читает gt/data, gpu/data,
zip/data, pnw/data и пишет исключительно в dict/. Ни один существующий сборщик
не меняется, ни один исходный файл не трогается — поэтому сломать прод он не может.
Когда словарь обрастёт потребителями, источники можно будет приводить к нему
по одному, сверяясь с этим файлом.

ЧТО СОБИРАЕТ.
  dict/oem.json     — производители: канонический ключ и все написания, найденные в базах
  dict/system.json  — классификаторы узлов пяти подпроектов, сведённые в одну таблицу
  dict/chain.json   — рёбра «владелец конструкции → изготовитель узла» с доказательством
  dict/summary.json — что и откуда собрано, для сверки

Запуск:  python scripts/build_dict.py          пересобрать
         python scripts/build_dict.py --check  сверить без перезаписи
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dict"

# ─────────────────────────────────────────────────────────────────────────────
# Нормализация. Сегодня в репозитории пять несовместимых реализаций нормализации
# имени компании и пять — номера. Здесь заводится по одной, и она объявляется
# канонической: приводить источники к ней можно по одному, не ломая остальные.


def nkey(name: str) -> str:
    """Ключ компании. Режет организационно-правовые формы и пунктуацию."""
    s = str(name or "").lower().replace("ё", "е")
    s = re.sub(r"\b(ооо|оао|зао|пао|ао|llc|ltd|inc|gmbh|s\.p\.a|spa|co|corp|company|"
               r"limited|holding|group|a/s|ab|bv|nv|sas|sa|plc|pte|kg|ag)\b", " ", s)
    s = re.sub(r"[^a-z0-9а-я]+", "", s)
    return s[:40]


def norm_pn(pn: str) -> str:
    """Ключ номера. Одна реализация вместо пяти: буквы и цифры, регистр вверх."""
    return re.sub(r"[^A-Z0-9А-Я]", "", str(pn or "").upper())


def clean_name(s: str) -> str:
    """Снимает кавычки, ёлочки и лишние пробелы: в базах одна компания встречается
    и как Grundfos Holding A/S, и как «Grundfos Holding A/S», и как "Grundfos"."""
    return re.sub(r'\s+', " ", str(s or "").strip().strip('"\'\u00ab\u00bb \u2039\u203a')).strip()


def pick_canon(spellings: list[str]) -> str:
    """Каноническое написание: самое информативное из ЧИСТЫХ. Грязное (в кавычках,
    со склеенными словами) берётся только если чистых нет вовсе."""
    clean = [clean_name(x) for x in spellings]
    clean = [x for x in clean if x]
    if not clean:
        return ""
    spaced = [x for x in clean if " " in x] or clean
    return max(spaced, key=lambda x: (len(x.split()), -len(x)))


def load(rel: str, default=None):
    p = ROOT / rel
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# Значение поля mk в gt/data/pn_db.json — не всегда компания. Примерно каждое
# четвёртое значение это указание к закупке: «Закупка по спецификации — OEM не
# нужен», «Любой дистрибьютор стандарта». Выбрасывать их нельзя: для сорсера это
# самый ценный ответ. Поэтому они не смешиваются с изготовителями, а получают
# собственный вид записи.
NOT_A_COMPANY = re.compile(
    r"закупк|любой|не нужен|по типу|по коду|по спецификац|дистрибьютор стандарта|"
    r"оснастк|аренда|сток\b|под заказ|см\.|н/д|неизвест|уточн|реверс|"
    r"→|;\s*или|\bили\b.*\bили\b", re.I)


def edge_kind(maker: str) -> str:
    m = str(maker or "").strip()
    if not m:
        return "empty"
    if NOT_A_COMPANY.search(m) or len(m) > 70:
        return "routing_note"
    return "maker"


def build_oem() -> dict:
    """Производители: канонический ключ и все написания, встреченные в базах."""
    spellings: dict[str, list[dict]] = {}

    def add(name, path, field):
        if not name or not str(name).strip():
            return
        k = nkey(name)
        if not k:
            return
        spellings.setdefault(k, [])
        rec = {"spelling": str(name).strip(), "where": f"{path}:{field}"}
        if rec not in spellings[k]:
            spellings[k].append(rec)

    for r in load("gt/data/pn_db.json", {}).get("rows", []):
        add(r.get("oem"), "gt/data/pn_db.json", "rows[].oem")
        if edge_kind(r.get("mk")) == "maker":
            add(r.get("mk"), "gt/data/pn_db.json", "rows[].mk")
    for f in load("gt/data/models.json", {}).get("families", []):
        add(f.get("title"), "gt/data/models.json", "families[].title")
    g = load("gpu/data/models.json", {})
    for o in g.get("oems", []):
        add(o.get("name"), "gpu/data/models.json", "oems[].name")
    for a in g.get("adjacent", []):
        add(a.get("name"), "gpu/data/models.json", "adjacent[].name")
    for m in load("gpu/data/machines.json", {}).get("machines", []):
        add(m.get("oem"), "gpu/data/machines.json", "machines[].oem")
    for p in load("zip/data/positions.json", []) or []:
        add(p.get("oem"), "zip/data/positions.json", "[].oem")
    for it in load("pnw/data/item_master.json", {}).get("items", []):
        add(it.get("brand"), "pnw/data/item_master.json", "items[].brand")
        add(it.get("maker"), "pnw/data/item_master.json", "items[].maker")

    out = []
    for k, sp in sorted(spellings.items()):
        # каноническое написание — самое длинное: в нём обычно есть группа-владелец
        canon = pick_canon([x["spelling"] for x in sp])
        out.append({"oem_key": k, "name": canon, "spellings": sp, "n_spellings": len(sp)})
    out.sort(key=lambda x: (-x["n_spellings"], x["oem_key"]))
    return {"note": "Производители: ключ nkey(name) и все написания, найденные в базах. "
                    "Разные написания одной компании собраны под одним ключом — это и есть "
                    "то, чего сегодня нет ни в одном подпроекте.",
            "count": len(out), "records": out}


def build_system() -> dict:
    """Классификаторы узлов пяти подпроектов в одной таблице."""
    rows = []

    def add(scope, path, field, value, title=""):
        if not value:
            return
        rows.append({"scope": scope, "source": f"{path}:{field}",
                     "value": str(value), "title": str(title or value)})

    for s in load("gt/data/parts.json", {}).get("systems", []):
        add("gtu", "gt/data/parts.json", "systems[].id", s.get("id"), s.get("title"))
    for s in load("gpu/data/parts.json", {}).get("systems", []):
        add("gpu", "gpu/data/parts.json", "systems[].key", s.get("key"), s.get("name"))
    for s in load("gpu/data/subsuppliers.json", {}).get("systems", []):
        add("gpu", "gpu/data/subsuppliers.json", "systems[].sys", s.get("sys"), s.get("name"))
    dem = load("gpu/data/demand.json", {}).get("systems", {})
    for k, v in (dem.items() if isinstance(dem, dict) else []):
        add("gpu", "gpu/data/demand.json", "systems{}", k, v)
    for c in load("zip/data/telsmith_3858.json", {}).get("classes", []):
        add("gsho", "zip/data/telsmith_3858.json", "classes[].key", c.get("key"), c.get("title"))
    for n in load("zip/data/material_strategy.json", []) or []:
        if isinstance(n, dict):
            add("gsho", "zip/data/material_strategy.json", "[].part_class", n.get("part_class"))

    by_scope: dict[str, dict[str, dict]] = {}
    for r in rows:
        d = by_scope.setdefault(r["scope"], {})
        cur = d.setdefault(r["value"], {"scope": r["scope"], "system_key": r["value"],
                                        "title": r["title"], "sources": []})
        if r["source"] not in cur["sources"]:
            cur["sources"].append(r["source"])
    flat = [v for d in by_scope.values() for v in d.values()]
    flat.sort(key=lambda x: (x["scope"], x["system_key"]))
    return {"note": "Классификаторы узлов живут в пяти местах и не сводятся автоматически: "
                    "ключ уникален только внутри своей области (scope), а не глобально. "
                    "Таблица показывает, какие значения существуют и откуда пришли.",
            "scopes": sorted(by_scope), "count": len(flat), "records": flat}


# ─────────────────────────────────────────────────────────────────────────────
# Машины. Обозначения лежат внутри строковых полей и отдельной сущностью не
# существуют. Поле mach базы PN содержит не только машины: туда попали детали
# («Уплотнение кольцевое»), корзины бренда («Solar (сток)») и машины совсем
# других сегментов (буровые насосы, превенторы). Классификатор разводит их по
# видам явно; вид «не определено» сохраняется как видимый хвост для разбора,
# а не подмешивается к машинам.
MACH_NOTE = re.compile(r"\s*\((сток|общая|sepoc|по документу|унифиц\w*|разные|все)[^)]*\)\s*", re.I)
MACH_BUCKET = re.compile(r"^(solar|ge|siemens|rolls-?royce)(\s+(пакет|compressor|общая|сток))?$", re.I)
# Замыкающего \b в семействах нет намеренно: он не срабатывает внутри
# обозначения — в LM2500 граница после «LM2» не наступает, и правило молча
# переставало ловить самую массовую машину базы.
MACH_GT = re.compile(r"\b(lms\d|lm\d|sgt|taurus|centaur|mars|titan|saturn|avon|olympus|spey|"
                     r"proteus|tyne|coberra|rb\d|frame\s*\d|ms\d{4}|tb\d{4}|gt\d{1,2}|"
                     r"v\d{2}\.|трент|trent)", re.I)
MACH_OTHER = re.compile(r"насос|превентор|вентилятор|компрессор|лебёдк|лебедк|станц|опреснит|"
                        r"агрегат|привод|установк", re.I)
MACH_PART = re.compile(r"кольц|шкаф|клапан|фильтр|прокладк|болт|гайк|датчик|труб|подшипник|"
                       r"уплотн|шланг|кабель|реле|модуль|плат|блок|комплект|втулк|диск|лопат|"
                       r"форсунк|свеч|щуп|масл|смазк|выкл\.|автомат", re.I)


def mach_kind(name: str) -> tuple[str, str]:
    """Вид обозначения и его основа без пометки источника."""
    stem = MACH_NOTE.sub(" ", str(name or "")).strip(" ,")
    if not stem:
        return "empty", ""
    if MACH_BUCKET.match(stem):
        return "bucket", stem
    if MACH_GT.search(stem):
        return "turbine", stem
    if MACH_OTHER.search(stem):
        return "other_machine", stem
    if MACH_PART.search(stem):
        return "part", stem
    return "unknown", stem


def mkey(name: str) -> str:
    return re.sub(r"[^A-Z0-9А-Я]", "", str(name or "").upper())


def build_machine() -> dict:
    """Реестр машин по направлениям: из базы PN (ГТУ) и из реестра ГШО."""
    recs: dict[str, dict] = {}

    def add(name, seg, kind, src, parts=1):
        k = mkey(name)
        if not k or len(k) < 2:
            return
        r = recs.setdefault(k, {"machine_key": k, "name": name, "segment": seg, "kind": kind,
                                "spellings": [], "parts": 0, "sources": []})
        r["parts"] += parts
        if name not in r["spellings"]:
            r["spellings"].append(name)
        if src not in r["sources"]:
            r["sources"].append(src)

    counts: dict[str, int] = {}
    for row in load("gt/data/pn_db.json", {}).get("rows", []):
        kind, stem = mach_kind(row.get("mach"))
        counts[kind] = counts.get(kind, 0) + 1
        if kind in ("turbine", "other_machine"):
            add(stem, "gtu" if kind == "turbine" else "other", kind, "gt/data/pn_db.json")

    for m in load("zip/data/machines.json", {}).get("machines", []):
        add(m["name"], "gsho", "mining_machine", "zip/data/machines.json", m.get("parts", 1))

    out = sorted(recs.values(), key=lambda x: (-x["parts"], x["name"]))
    by_seg: dict[str, int] = {}
    for r in out:
        by_seg[r["segment"]] = by_seg.get(r["segment"], 0) + 1
    return {"note": "Реестр машин: обозначения вынуты из строковых полей баз и разведены по видам. "
                    "Вид unknown в реестр не попадает и остаётся в счётчике видимым хвостом — "
                    "подмешивать неразобранное к машинам значит завышать заполняемость.",
            "count": len(out), "by_segment": by_seg, "pn_db_kinds": counts, "records": out}


def build_chain() -> dict:
    """Рёбра «владелец конструкции → изготовитель узла» с доказательством."""
    edges: dict[tuple, dict] = {}

    def add(frm, to, relation, scope, proof, src, kind):
        if not frm or not to:
            return
        # Ребро компании в саму себя означает не субпоставщика, а собственное
        # изготовление. Смысл противоположный, поэтому и отношение другое.
        if kind == "maker" and nkey(frm) and nkey(frm) == nkey(to):
            relation = "in_house"
        k = (nkey(frm), nkey(to) if kind == "maker" else str(to).strip()[:60], relation)
        e = edges.setdefault(k, {
            "from": str(frm).strip(), "from_key": nkey(frm),
            "to": str(to).strip(), "to_key": nkey(to) if kind == "maker" else "",
            "kind": kind, "relation": relation, "scope": [], "proof": [], "sources": [], "n": 0,
            "from_is_bucket": nkey(frm) in ("прочие", "прочее", "разное"),
        })
        e["n"] += 1
        if scope and scope not in e["scope"] and len(e["scope"]) < 12:
            e["scope"].append(scope)
        if proof and proof not in e["proof"] and len(e["proof"]) < 4:
            e["proof"].append(str(proof)[:220])
        if src not in e["sources"]:
            e["sources"].append(src)

    for r in load("gt/data/pn_db.json", {}).get("rows", []):
        mk = r.get("mk")
        k = edge_kind(mk)
        if k == "empty":
            continue
        add(r.get("oem"), mk, "makes_for", r.get("mach") or r.get("seg"),
            r.get("ev") or r.get("sn"), "gt/data/pn_db.json", k)

    for c in load("zip/data/telsmith_crossrefs.json", {}).get("crossrefs", []):
        add("Telsmith", c.get("real_maker"), "makes_for", c.get("telsmith_pn"),
            c.get("evidence_url"), "zip/data/telsmith_crossrefs.json",
            edge_kind(c.get("real_maker")))

    for s in load("gpu/data/subsuppliers.json", {}).get("systems", []):
        for m in s.get("makers", []):
            add("ГПУ (сегмент)", m.get("name"), "makes_for", s.get("sys"),
                m.get("proof") or m.get("what"), "gpu/data/subsuppliers.json",
                edge_kind(m.get("name")))

    for r in load("gt/data/tfs_subsuppliers.json", {}).get("rows", []):
        add("TFS", r.get("name"), "makes_for", r.get("module"), r.get("what"),
            "gt/data/tfs_subsuppliers.json", edge_kind(r.get("name")))

    recs = sorted(edges.values(), key=lambda x: (-x["n"], x["from_key"], x["to_key"]))
    makers = [e for e in recs if e["kind"] == "maker"]
    notes = [e for e in recs if e["kind"] == "routing_note"]
    return {"note": "Ребро, а не карточка: уровень субпоставщика — свойство пути, а не компании. "
                    "Одна и та же компания бывает первым уровнем для одного владельца конструкции "
                    "и вторым, если между ними стоит пакаджер, поэтому уровень не хранится, "
                    "а считается обходом. Записи вида routing_note — не компании, а указания "
                    "к закупке из поля mk базы PN: «закупка по спецификации», «любой дистрибьютор». "
                    "Для сорсера это ответ не хуже имени завода, поэтому они сохранены отдельным видом.",
            "count": len(recs), "makers": len(makers), "routing_notes": len(notes),
            "records": recs}


def build() -> dict:
    return {"oem": build_oem(), "system": build_system(), "chain": build_chain(),
            "machine": build_machine()}


def main() -> int:
    data = build()
    files = {
        "oem.json": data["oem"],
        "system.json": data["system"],
        "chain.json": data["chain"],
        "machine.json": data["machine"],
        "summary.json": {
            "note": "Словарь — проекция существующих баз. Источники не изменяются; "
                    "пересобирается python scripts/build_dict.py.",
            "oem_keys": data["oem"]["count"],
            "system_keys": data["system"]["count"],
            "system_scopes": data["system"]["scopes"],
            "chain_edges": data["chain"]["count"],
            "chain_makers": data["chain"]["makers"],
            "chain_routing_notes": data["chain"]["routing_notes"],
            "machines": data["machine"]["count"],
            "machines_by_segment": data["machine"]["by_segment"],
        },
    }
    if "--check" in sys.argv:
        for name, payload in files.items():
            p = OUT / name
            if not p.exists():
                print(f"✗ нет dict/{name} — выполните: python scripts/build_dict.py", file=sys.stderr)
                return 1
            if json.loads(p.read_text(encoding="utf-8")) != payload:
                print(f"✗ dict/{name} устарел — выполните: python scripts/build_dict.py", file=sys.stderr)
                return 1
        print(f"✓ словарь актуален: {files['summary.json']['oem_keys']} производителей, "
              f"{files['summary.json']['system_keys']} узлов, "
              f"{files['summary.json']['chain_edges']} рёбер")
        return 0
    OUT.mkdir(exist_ok=True)
    for name, payload in files.items():
        (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    s = files["summary.json"]
    print(f"✓ dict/: производителей {s['oem_keys']}, узлов {s['system_keys']} "
          f"в областях {s['system_scopes']}, рёбер {s['chain_edges']} "
          f"(изготовителей {s['chain_makers']}, указаний к закупке {s['chain_routing_notes']}), "
          f"машин {s['machines']} {s['machines_by_segment']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
