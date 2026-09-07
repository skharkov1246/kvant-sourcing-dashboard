# -*- coding: utf-8 -*-
"""Разбор кросс-номеров ЗИП из свободного текста поля aliases в структуру.

Задача: сорсер должен находить деталь по ЛЮБОМУ номеру — нашему, оригинального
производителя, аналога или поставщика. Сейчас 1772 номера лежат прозой и поиском
не берутся.

Вход:  zip/data/positions.json  (поля catalog_no, catalog_norm, aliases, oem)
Выход: pnw/data/crossrefs.json  — плоская таблица:
       position_id | number | number_norm | brand | kind | source

kind: oem — номер оригинального производителя; analog — номер вторичного рынка;
      variant — то же число в другом написании (с пробелами/дефисами);
      unknown — номер есть, принадлежность не определена.
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "zip" / "data" / "positions.json"
OUT = ROOT / "pnw" / "data" / "crossrefs.json"

# Бренды: OEM техники и вторичный рынок. Порядок важен — длинные имена первыми,
# иначе «Atlas» съест «Atlas Copco».
OEM_BRANDS = ["Atlas Copco", "Sandvik Tamrock", "Sandvik", "Epiroc", "Normet",
              "Caterpillar", "Volvo", "Deutz", "Cummins", "Parker", "Hydac",
              "Bosch Rexroth", "Bosch", "Danfoss", "Eaton", "Rexroth"]
ANALOG_BRANDS = ["Luber-finer", "Luberfiner", "Donaldson", "Fleetguard", "Baldwin",
                 "Coralfly", "Tamfiney", "SF-Filter", "Hifi", "Sakura", "Mann",
                 "Wix", "Napa", "Ryco", "Fil Filter", "Kralinator", "Purolator",
                 "SKF", "FAG", "INA", "Timken", "NSK", "Trelleborg", "Freudenberg"]
ALL_BRANDS = [(b, "oem") for b in OEM_BRANDS] + [(b, "analog") for b in ANALOG_BRANDS]

# Номер: буквенно-цифровой, минимум 5 знаков, обязательно с цифрой.
# Разрешаем внутренние пробелы вида «3222 1881 41» (группы по 2-4 знака).
NUM = re.compile(r"\b(?:[A-ZА-Я]{0,3}[\-\s]?)?(?:\d[\dA-ZА-Я\-\.]{2,}(?:\s\d{2,4}){0,4})\b")
# Мусорные «числа»: годы, проценты, размеры, ссылки на ГОСТ/ISO
JUNK = re.compile(r"^(19|20)\d{2}$|^\d{1,3}$|ГОСТ|ISO|DIN|EN\s|мм$|шт$|кг$", re.I)


def norm(s):
    """Нормализация номера: только буквы и цифры, верхний регистр.
    «3222 1881 41», «3222-1881-41» и «3222188141» дают один ключ."""
    return re.sub(r"[^A-Z0-9А-Я]", "", str(s).upper())


def brand_of(chunk):
    """Бренд, упомянутый в фрагменте, и его тип."""
    low = chunk.lower()
    for b, kind in ALL_BRANDS:
        if b.lower() in low:
            return b, kind
    return "", ""


def main():
    positions = json.loads(SRC.read_text(encoding="utf-8"))
    rows, seen_global = [], set()
    stat = Counter()

    for p in positions:
        pid = p["id"]
        own = norm(p.get("catalog_norm") or p.get("catalog_no"))
        oem_of_pos = (p.get("oem") or "").strip()
        text = str(p.get("aliases") or "")
        if not text.strip():
            continue

        seen_pos = {own}
        # фрагменты: главный разделитель «;», внутри — «/» и «=» как перечисление
        for chunk in re.split(r"[;\n]", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            brand, bkind = brand_of(chunk)
            for raw in NUM.findall(chunk):
                raw = raw.strip(" .-")
                n = norm(raw)
                if len(n) < 5 or not any(c.isdigit() for c in n):
                    continue
                # «N 3115153600» — одиночная буква-приставка перед номером: это
                # обрывок фразы, а не часть артикула. Отрезаем и сверяем заново.
                m2 = re.match(r"^[A-ZА-Я]\s+(.+)$", raw)
                if m2:
                    raw = m2.group(1).strip()
                    n = norm(raw)
                    if n in seen_pos or len(n) < 5:
                        continue
                if JUNK.search(raw.strip()):
                    stat["отброшено как мусор"] += 1
                    continue
                if n in seen_pos:                       # то же число другим написанием
                    stat["варианты написания"] += 1
                    continue
                seen_pos.add(n)
                # тип: бренд явный → по нему; бренд позиции → oem; иначе unknown
                if bkind:
                    kind = bkind
                elif brand == "" and oem_of_pos:
                    kind = "unknown"
                else:
                    kind = "unknown"
                rows.append({
                    "position_id": pid,
                    "catalog_no": p.get("catalog_no"),
                    "number": raw,
                    "number_norm": n,
                    "brand": brand or (oem_of_pos if kind == "oem" else ""),
                    "kind": kind,
                    "source": "aliases",
                })
                stat[kind] += 1
                seen_global.add(n)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updated": "2026-09-01",
        "source": "разбор zip/data/positions.json, поле aliases",
        "note": "kind: oem — номер оригинального производителя; analog — вторичный рынок; "
                "unknown — принадлежность не определена. number_norm — ключ поиска "
                "(только буквы и цифры), схлопывает разные написания одного номера.",
        "rows": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"позиций обработано : {sum(1 for p in positions if str(p.get('aliases') or '').strip())}")
    print(f"кросс-номеров      : {len(rows)}  (уникальных {len(seen_global)})")
    for k, v in stat.most_common():
        print(f"  {k:24} {v}")
    dup = Counter(r["number_norm"] for r in rows)
    multi = [(k, v) for k, v in dup.items() if v > 1]
    print(f"номеров, ведущих на >1 позицию: {len(multi)}  (кандидаты в дубли номенклатуры)")


if __name__ == "__main__":
    main()
