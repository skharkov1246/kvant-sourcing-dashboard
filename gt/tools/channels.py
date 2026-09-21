#!/usr/bin/env python3
"""Карта каналов закупки по изготовителям заявки: замер и сверка.

Зачем отдельный инструмент. Числа в `gt/data/ship_channels.json` сначала
набирались по ходу разведки — и сложились неверно: сумма по брендам вышла
9 955 456 USD при всей экспозиции заявки 9 277 410. Причина не в арифметике, а
в двойном счёте: Bently Nevada, Allen-Bradley, Pepperl+Fuchs и Det-Tronics
стоят в заявке под шильдиком Solar и Siemens, поэтому попадали и в свою
строку, и в строку OEM.

Правило замера здесь одно: **одна строка заявки — один бренд**. Сначала
опознаётся компонентный изготовитель (по номеру, наименованию и вскрытому
`real_maker`), и только если его нет — шильдик из поля изготовителя. Поэтому у
Solar и Siemens в карте остаётся их собственная номенклатура, а компоненты под
их шильдиком стоят своими строками: искать их надо у их изготовителя, а не
через OEM.

Экспозиция — середина НАШЕЙ вилки, умноженная на количество. Это не цена
заказчику и не подтверждённая закупка.

    python gt/tools/channels.py                 # показать замер
    python gt/tools/channels.py --write         # записать числа в набор
    python gt/tools/channels.py --check         # сверить набор с замером (гейт)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHIP = ROOT / "gt/data/ship_lukoil.json"
CHAN = ROOT / "gt/data/ship_channels.json"

# Порядок значим: первое совпадение забирает строку. Компонентные изготовители
# стоят выше OEM именно потому, что сидят под его шильдиком.
RULES: list[tuple[str, str]] = [
    ("Bently Nevada (Baker Hughes)",
     r"bently|fieldmonitor|proximitor|\b1701/|\b17018[0-9]-|\b3500/|\b330[0-9]{2}-"
     r"|\b13548[0-9]-|\b177230-"),
    ("Det-Tronics (Emerson)", r"det-?tron|\bEQ[23][0-9]{3}|\bEQP\b"),
    ("Moog", r"\bmoog\b"),
    ("Pepperl+Fuchs", r"pepperl|\bKFD2-|\bKCD2-|\bKFU8"),
    ("Rockwell Automation (Allen-Bradley)",
     r"allen-?bradley|rockwell|\b179[04]-|\b1756-|\b1769-|\b1785-|\b1770-|\b1783-|\b2711"),
    ("Danfoss", r"danfoss|\b180B[0-9]"),
    ("Cummins", r"cummins"),
    ("Fleetguard", r"fleetguard"),
    ("INNIO Jenbacher", r"jenbacher|innio"),
    ("SAACKE", r"saacke"),
    ("Victory Energy", r"victory\s+energy"),
    ("ABB", r"\babb\b|\b3AXD|\b3BSE|\b3BHE"),
    ("Siemens Energy", r"siemens|\bloher\b"),
    ("Solar Turbines", r"\bsolar\b|caterpillar"),
]
COMPILED = [(name, re.compile(pat, re.I)) for name, pat in RULES]
UNMAPPED = "(не в карте)"


def blob(row: dict) -> str:
    """Текст, по которому опознаётся изготовитель.

    Только измеренные поля строки: номер, шильдик, наименование, модель и
    вскрытый настоящий изготовитель. Ни `note`, ни `substitute` — там мои
    рассуждения и названия АЛЬТЕРНАТИВ, по ним строка ушла бы чужому бренду.
    """
    return " ".join(str(row.get(k) or "")
                    for k in ("pn", "man", "name", "model", "real_maker"))


def brand_of(row: dict) -> str:
    """Один бренд на строку: первое совпадение по порядку RULES."""
    text = blob(row)
    for name, rx in COMPILED:
        if rx.search(text):
            return name
    return UNMAPPED


def exposure(row: dict) -> float | None:
    """Середина нашей вилки на количество; None — если вилки нет."""
    lo, hi = row.get("usd_lo"), row.get("usd_hi")
    if lo is None or hi is None:
        return None
    return (float(lo) + float(hi)) / 2 * float(row.get("qty") or 0)


def measure(rows: list[dict]) -> dict:
    """Замер: по бренду строки, экспозиция, сколько строк без оценки."""
    out: dict[str, dict] = {}
    for row in rows:
        b = brand_of(row)
        cell = out.setdefault(b, {"rows": 0, "usd": 0.0, "no_estimate": 0})
        cell["rows"] += 1
        e = exposure(row)
        if e is None:
            cell["no_estimate"] += 1
        else:
            cell["usd"] += e
    for cell in out.values():
        cell["usd"] = int(round(cell["usd"]))
    return out


def oem_shift(rows: list[dict]) -> dict:
    """Сколько экспозиции ушло из кластера OEM к компонентным изготовителям.

    Это главный вывод карты: регистрация у OEM закрывает не весь его кластер.
    """
    shift: dict[str, dict] = {}
    for row in rows:
        man = (row.get("man") or "").strip()
        oem = ("Solar Turbines" if man.lower().startswith("solar")
               else "Siemens Energy" if man.lower().startswith("siemens") else None)
        if not oem:
            continue
        b = brand_of(row)
        if b == oem:
            continue
        cell = shift.setdefault(oem, {"rows": 0, "usd": 0, "brands": {}})
        cell["rows"] += 1
        e = int(round(exposure(row) or 0))
        cell["usd"] += e
        got = cell["brands"].setdefault(b, {"rows": 0, "usd": 0})
        got["rows"] += 1
        got["usd"] += e
    return shift


def tail(rows: list[dict]) -> dict:
    """Остаток вне карты: он должен быть назван, а не спрятан.

    Если не показать остаток числом, карта читается как «это вся заявка».
    """
    out: dict[str, dict] = {}
    for row in rows:
        if brand_of(row) != UNMAPPED:
            continue
        man = (row.get("man") or "—").strip() or "—"
        cell = out.setdefault(man, {"rows": 0, "usd": 0.0, "no_estimate": 0})
        cell["rows"] += 1
        e = exposure(row)
        cell["usd"] += e or 0
        if e is None:
            cell["no_estimate"] += 1
    for cell in out.values():
        cell["usd"] = int(round(cell["usd"]))
    top = sorted(out.items(), key=lambda kv: -kv[1]["usd"])[:10]
    return {
        "rows": sum(c["rows"] for c in out.values()),
        "usd": sum(c["usd"] for c in out.values()),
        "makers": len(out),
        "no_estimate": sum(c["no_estimate"] for c in out.values()),
        "biggest_maker_usd": top[0][1]["usd"] if top else 0,
        "top": [{"man": k, **v} for k, v in top],
    }


def totals(rows: list[dict], m: dict) -> dict:
    tot = int(round(sum(exposure(r) or 0 for r in rows)))
    mapped = sum(v["usd"] for k, v in m.items() if k != UNMAPPED)
    mapped_rows = sum(v["rows"] for k, v in m.items() if k != UNMAPPED)
    return {
        "rows_total": len(rows),
        "exposure_total": tot,
        "rows_mapped": mapped_rows,
        "exposure_mapped": mapped,
        "share_pct": round(mapped / tot * 100, 1) if tot else 0.0,
        "brands_in_request": len({(r.get("man") or "—").strip() for r in rows}),
        "tail": tail(rows),
    }


def report(m: dict, t: dict, shift: dict) -> str:
    lines = [f"строк {t['rows_total']}, экспозиция {t['exposure_total']:,} USD"
             .replace(",", " ")]
    for name, _ in RULES:
        cell = m.get(name)
        if not cell:
            lines.append(f"{name:38} — ни одной строки")
            continue
        lines.append(f"{name:38} {cell['rows']:5} строк {cell['usd']:11,} USD"
                     f"  без оценки {cell['no_estimate']:4}".replace(",", " "))
    cell = m.get(UNMAPPED, {"rows": 0, "usd": 0, "no_estimate": 0})
    lines.append(f"{UNMAPPED:38} {cell['rows']:5} строк {cell['usd']:11,} USD"
                 f"  без оценки {cell['no_estimate']:4}".replace(",", " "))
    lines.append(f"карта покрывает {t['exposure_mapped']:,} из {t['exposure_total']:,} USD "
                 f"= {t['share_pct']} %".replace(",", " "))
    for oem, cell in sorted(shift.items()):
        parts = ", ".join(f"{b} {v['rows']}" for b, v in
                          sorted(cell["brands"].items(), key=lambda kv: -kv[1]["usd"]))
        lines.append(f"из кластера {oem} ушло {cell['rows']} строк на "
                     f"{cell['usd']:,} USD ({parts})".replace(",", " "))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="записать числа в набор")
    ap.add_argument("--check", action="store_true",
                    help="сверить числа набора с замером, вернуть 1 при расхождении")
    a = ap.parse_args()

    rows = json.loads(SHIP.read_text(encoding="utf-8"))["rows"]
    m = measure(rows)
    t = totals(rows, m)
    shift = oem_shift(rows)
    print(report(m, t, shift))

    if not (a.write or a.check):
        return 0

    doc = json.loads(CHAN.read_text(encoding="utf-8"))
    bad: list[str] = []
    for item in doc["brands"]:
        cell = m.get(item["brand"])
        if cell is None:
            bad.append(f"{item['brand']}: в замере такого бренда нет")
            continue
        for key in ("rows", "usd", "no_estimate"):
            if item.get(key) != cell[key]:
                bad.append(f"{item['brand']}.{key}: в наборе {item.get(key)}, "
                           f"замер {cell[key]}")
            if a.write:
                item[key] = cell[key]
        if a.write:
            item["pattern"] = dict(RULES)[item["brand"]]
    named = {i["brand"] for i in doc["brands"]}
    missed = [n for n, _ in RULES if n not in named]
    if missed:
        bad.append("в наборе нет строк по брендам: " + ", ".join(missed))

    if a.write:
        doc["measure"] = {
            "rule": "одна строка заявки — один бренд: сначала компонентный изготовитель "
                    "(номер, наименование, вскрытый настоящий изготовитель), потом шильдик "
                    "из поля изготовителя. Поэтому у Solar и Siemens в карте осталась их "
                    "собственная номенклатура, а компоненты под их шильдиком стоят своими "
                    "строками — искать их надо у их изготовителя, а не через OEM",
            "exposure_is": "середина НАШЕЙ вилки, умноженная на количество. Это не цена "
                           "заказчику и не подтверждённая закупка",
            "tool": "gt/tools/channels.py — он же сверяет эти числа (--check)",
            **t,
            "oem_shift": shift,
        }
        CHAN.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"записано в {CHAN.relative_to(ROOT)}")
        return 0

    if bad:
        print("\nрасхождения набора с замером:", file=sys.stderr)
        for b in bad:
            print("  " + b, file=sys.stderr)
        return 1
    print("✓ числа набора совпадают с замером")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
