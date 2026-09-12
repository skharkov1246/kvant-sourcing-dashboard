#!/usr/bin/env python3
"""Поисковый индекс по значениям: data/index.json.

Каталог (data/catalog.json) отвечает на вопрос «какие наборы данных есть».
Индекс отвечает на вопрос «в каком наборе лежит вот это значение»: парт-номер,
производитель, модель, код ТН ВЭД, название организации. Без него поиск по
номеру — это перебор всего дерева (замер: 8,4 с и 40 МБ чтения на один запрос).

Что индексируется — только идентификаторы. Персональные и ценовые поля
(e-mail, телефоны, ИНН, суммы) в индекс НЕ попадают: индекс лежит рядом с
данными и не должен становиться отдельной утечкой. Список исключений
записан в сам файл индекса, чтобы его можно было проверить.

    python scripts/build_index.py            # пересобрать data/index.json
    python scripts/build_index.py --check     # проверить актуальность (для CI)
    python scripts/lookup.py 4380132          # найти значение
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"
MANIFEST = ROOT / ".agent" / "manifest.json"
OUT = ROOT / "data" / "index.json"


def bot_files() -> set[str]:
    """Наборы, которые переписывают боты по расписанию.

    Их содержимое меняется независимо от пул-реквестов, поэтому индекс,
    построенный по ним, устаревал бы сам собой: PR проверяется на слиянии с
    актуальным main, где бот уже успел записать новые данные, и гейт краснел бы
    по причине, к самому PR отношения не имеющей.

    Цена исключения измерена: gt/data/bitrix_gt.json давал 592 вхождения, но
    лишь 36 ключей из 32 619 (0,1 %) встречались только в нём.
    Список берём из .agent/manifest.json, чтобы он был один на репозиторий.
    """
    try:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return {b["path"] for b in m.get("bot_files", []) if b.get("path", "").endswith(".json")}

# Поля, попадающие в индекс: имя поля → группа поиска.
FIELD_GROUPS: dict[str, str] = {
    "pn": "pn", "part": "pn", "part_numbers": "pn", "article": "pn", "sku": "pn", "cross": "pn",
    "oem": "oem", "man": "oem", "brand": "oem", "maker": "oem", "motortech": "oem",
    "model": "model", "engines": "model", "machine": "model", "models": "model",
    "hs10": "hs", "hs": "hs", "hs_code": "hs",
    "name": "org", "supplier": "org", "exporter": "org", "importer": "org", "company": "org",
}

# Поля, которые НЕ индексируются ни при каких условиях: персональные данные,
# контакты, деньги. Индекс не должен позволять выгрузить их перечислением.
EXCLUDED_FIELDS = [
    "email", "e_mail", "mail", "emails", "phone", "tel", "contact_phone", "phones",
    "inn", "ogrn", "kpp", "passport", "address", "addr",
    "price", "prices", "cost", "val", "custval", "statval", "usd_kg", "revenue",
    "margin", "margin_pct", "purchase", "op_profit", "deal_profit", "opportunity",
    "comment", "comments", "note", "notes", "text", "desc", "assessment",
]

MIN_LEN, MAX_LEN = 3, 64
MAX_POSTINGS = 200          # значение в 200+ записях бесполезно как поисковый ключ
_norm_pn = re.compile(r"[^A-Z0-9]")
_ws = re.compile(r"\s+")


def normalize(value: str, group: str) -> str | None:
    """Приводит значение к ключу поиска. Для парт-номеров убираем разделители,
    для остального — регистр и лишние пробелы."""
    s = str(value).strip()
    if not s:
        return None
    if group in ("pn", "hs"):
        s = _norm_pn.sub("", s.upper())
    else:
        s = _ws.sub(" ", s).casefold()
    return s if MIN_LEN <= len(s) <= MAX_LEN else None


def walk_values(obj, out: list[tuple[str, str]], depth: int = 0) -> None:
    """Собирает пары (группа, значение) из записи, не заходя в исключённые поля."""
    if depth > 4:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if key in EXCLUDED_FIELDS:
                continue
            group = FIELD_GROUPS.get(key)
            if group:
                if isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, (str, int, float)):
                            out.append((group, str(x)))
                        elif isinstance(x, dict):
                            walk_values(x, out, depth + 1)
                elif isinstance(v, (str, int, float)):
                    out.append((group, str(v)))
                elif isinstance(v, dict):
                    walk_values(v, out, depth + 1)
            elif isinstance(v, (dict, list)):
                walk_values(v, out, depth + 1)
    elif isinstance(obj, list):
        for x in obj[:200]:
            walk_values(x, out, depth + 1)


def records_of(path: Path, entry: dict) -> tuple[str | None, list]:
    """Записи набора по подсказке каталога: имя главной коллекции уже известно."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, []
    if isinstance(data, list):
        return None, data
    key = (entry.get("main_collection") or {}).get("key")
    if key and isinstance(data.get(key), list):
        return key, data[key]
    for k, v in data.items():                       # запасной путь: самый длинный список
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return k, v
    return None, []


def build() -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    skip = bot_files()
    files: list[dict] = []
    index: dict[str, dict[str, list]] = {}

    for entry in catalog["datasets"]:
        if entry["path"].endswith(".js") or entry.get("format") != "json":
            continue
        if entry["path"] in skip:                    # см. bot_files(): волатильные наборы
            continue
        path = ROOT / entry["path"]
        if not path.exists():
            continue
        coll, rows = records_of(path, entry)
        if not rows:
            continue
        fi = len(files)
        hits = 0
        for ri, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            pairs: list[tuple[str, str]] = []
            walk_values(row, pairs)
            for group, raw in pairs:
                key = normalize(raw, group)
                if not key:
                    continue
                bucket = index.setdefault(group, {}).setdefault(key, [])
                if len(bucket) < MAX_POSTINGS and [fi, ri] not in bucket:
                    bucket.append([fi, ri])
                    hits += 1
        files.append({
            "path": entry["path"],
            "collection": coll,
            "records": len(rows),
            "subproject": entry.get("subproject", ""),
            "sensitivity": (entry.get("sensitivity") or {}).get("level", ""),
        })
        if not hits:                                 # набор без индексируемых полей
            files[fi]["indexed"] = 0

    for group in index:
        index[group] = dict(sorted(index[group].items()))

    postings = sum(len(v) for g in index.values() for v in g.values())
    return {
        "schema": "kvant.value-index/1",
        "built_from": "data/catalog.json",
        "generated_by": "scripts/build_index.py",
        "how_to_use": "python scripts/lookup.py <значение>",
        "indexed_fields": FIELD_GROUPS,
        "excluded_fields": EXCLUDED_FIELDS,
        "excluded_datasets": sorted(bot_files()),
        "excluded_datasets_reason": "наборы, переписываемые ботами по расписанию: индекс по ним устаревал бы сам собой",
        "excluded_reason": "персональные данные, контакты и суммы в индекс не попадают — иначе индекс сам станет выгрузкой",
        "summary": {
            "files": len(files),
            "values": sum(len(v) for v in index.values()),
            "postings": postings,
            "by_group": {g: len(v) for g, v in sorted(index.items())},
        },
        "files": files,
        "index": index,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка поискового индекса по значениям")
    ap.add_argument("--check", action="store_true", help="только проверить актуальность")
    a = ap.parse_args()

    fresh = build()
    if a.check:
        if not OUT.exists():
            print(f"✗ нет {OUT.relative_to(ROOT)} — выполните: python scripts/build_index.py", file=sys.stderr)
            return 1
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if old.get("index") != fresh["index"]:
            print("✗ индекс устарел — выполните: python scripts/build_index.py", file=sys.stderr)
            return 1
        print(f"✓ индекс актуален: {fresh['summary']['values']} значений")
        return 0

    OUT.write_text(json.dumps(fresh, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    s = fresh["summary"]
    print(f"✓ {OUT.relative_to(ROOT)}: {s['values']} значений, {s['postings']} вхождений, "
          f"{s['files']} наборов, {OUT.stat().st_size / 1048576:.1f} МБ")
    print("  по группам:", s["by_group"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
