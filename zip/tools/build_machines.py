#!/usr/bin/env python3
"""Реестр машин ГШО → zip/data/machines.json.

ЗАЧЕМ. Счётчик цепочки портала (scripts/build_chain_coverage.py) показал по ГШО
одну машину при 1918 позициях номенклатуры: детали есть, машин нет, и цепочка
«машина → узел → … → запчасть» рвётся на первом же шаге. При этом обозначения
машин в данных ЕСТЬ — они лежат внутри строкового поля machine справочника
деталей, через запятую, в разных написаниях. Отдельной сущности из них никто
не собрал.

ЧТО ДЕЛАЕТ. Разбирает поле machine, сводит написания одной машины под один ключ
и считает, сколько позиций номенклатуры к каждой относится. Ничего не выдумывает:
машина попадает в реестр, только если встретилась в данных.

ЧЕГО НЕ ДЕЛАЕТ. Не определяет паспортные данные машин — их в наших файлах нет.
Реестр отвечает на вопрос «какие машины мы обслуживаем и сколько по ним ЗИПа»,
а не «что это за машина». Второе — предмет разведки.

Запуск:  python zip/tools/build_machines.py
         python zip/tools/build_machines.py --check
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "zip" / "data" / "machines.json"

# Разделители перечня машин внутри одного поля. Точку не берём: она встречается
# внутри обозначений (Boomer S1 D-DH).
SPLIT = re.compile(r"[,;/]| и ")

# Мусор, который в поле machine попадает вместо обозначения машины.
NOT_A_MACHINE = re.compile(
    r"^(нет|н/д|разн|прочее|уточн|см\.|-|—|\?+)$|^\d{1,2}$|^[а-яё\s]+$", re.I)


def mkey(name: str) -> str:
    """Ключ машины: буквы и цифры, регистр вверх. Склеивает ST14 и ST-14,
    Boomer M2C и BOOMER M2C — в данных это одна и та же машина."""
    return re.sub(r"[^A-Z0-9А-Я]", "", str(name or "").upper())


def bkey(name: str) -> str:
    """Ключ бренда. Epiroc и EPIROC, Normet и Normet Group — один изготовитель."""
    s = str(name or "").lower().replace("ё", "е")
    s = re.sub(r"\b(group|ооо|оао|зао|пао|ао|llc|ltd|inc|gmbh|co|corp|ab|oy)\b", " ", s)
    return re.sub(r"[^a-z0-9а-я]+", "", s)[:32]


def load(rel, default=None):
    p = ROOT / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def build() -> dict:
    items = (load("pnw/data/item_master.json", {}) or {}).get("items", [])
    gsho = [x for x in items if x.get("section") == "ЗИП ГШО"]

    machines: dict[str, dict] = {}
    brand_names: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for it in gsho:
        # Поле brand тоже бывает перечнем: «Epiroc, Normet, Paus» — это не изготовитель,
        # а список тех, чью технику деталь закрывает. Берём первый: он соответствует
        # основной применимости, а остальные всё равно попадут через свои же позиции.
        brands_raw = [b.strip() for b in SPLIT.split(str(it.get("brand") or "")) if b.strip()]
        brand = brands_raw[0] if brands_raw else ""
        bk = bkey(brand)
        if bk and brand:
            brand_names[bk][brand] += 1
        for raw in SPLIT.split(str(it.get("machine") or "")):
            name = raw.strip().strip("«»\"'()")
            if not name or len(name) < 2 or NOT_A_MACHINE.match(name):
                continue
            k = mkey(name)
            if not k or len(k) < 2:
                continue
            m = machines.setdefault(k, {
                "machine_key": k, "name": name, "spellings": {},
                "brands": {}, "parts": 0, "nodes": {},
            })
            m["spellings"][name] = m["spellings"].get(name, 0) + 1
            if bk:
                m["brands"][bk] = m["brands"].get(bk, 0) + 1
            m["parts"] += 1
            node = (it.get("node") or "").strip()
            if node:
                m["nodes"][node] = m["nodes"].get(node, 0) + 1

    # Каноническое имя машины — самое частое написание; бренд — самый частый.
    brand_canon = {bk: max(names.items(), key=lambda x: x[1])[0] for bk, names in brand_names.items()}
    recs = []
    for m in machines.values():
        canon = max(m["spellings"].items(), key=lambda x: (x[1], len(x[0])))[0]
        bk = max(m["brands"].items(), key=lambda x: x[1])[0] if m["brands"] else ""
        recs.append({
            "machine_key": m["machine_key"],
            "name": canon,
            "brand": brand_canon.get(bk, ""),
            "brand_key": bk,
            "spellings": sorted(m["spellings"], key=lambda s: -m["spellings"][s]),
            "parts": m["parts"],
            "nodes": sorted(m["nodes"], key=lambda s: -m["nodes"][s])[:12],
            "n_nodes": len(m["nodes"]),
        })
    recs.sort(key=lambda x: (-x["parts"], x["name"]))

    by_brand: dict[str, dict] = {}
    for r in recs:
        b = by_brand.setdefault(r["brand"] or "не определён",
                                {"brand": r["brand"] or "не определён", "machines": 0, "parts": 0})
        b["machines"] += 1
        b["parts"] += r["parts"]
    brands = sorted(by_brand.values(), key=lambda x: -x["parts"])

    merged = [r for r in recs if len(r["spellings"]) > 1]
    return {
        "note": "Реестр машин ГШО, выведенный из справочника деталей: обозначения лежали внутри "
                "строкового поля machine через запятую и отдельной сущностью не существовали. "
                "Собирается zip/tools/build_machines.py; руками не заполнять. Паспортных данных "
                "не содержит — их в наших файлах нет, это предмет разведки.",
        "source": "pnw/data/item_master.json, раздел «ЗИП ГШО»",
        "stats": {
            "positions": len(gsho),
            "machines": len(recs),
            "brands": len(brands),
            "merged_spellings": len(merged),
            "parts_linked": sum(r["parts"] for r in recs),
        },
        "brands": brands,
        "machines": recs,
    }


def main() -> int:
    fresh = build()
    if "--check" in sys.argv:
        if not OUT.exists() or json.loads(OUT.read_text(encoding="utf-8")) != fresh:
            print("✗ zip/data/machines.json устарел — выполните: python zip/tools/build_machines.py",
                  file=sys.stderr)
            return 1
        print(f"✓ реестр машин ГШО актуален: {fresh['stats']['machines']} машин")
        return 0
    OUT.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    s = fresh["stats"]
    print(f"✓ zip/data/machines.json: {s['machines']} машин, {s['brands']} брендов, "
          f"{s['parts_linked']} связей с номенклатурой, "
          f"склеено написаний у {s['merged_spellings']} машин")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
