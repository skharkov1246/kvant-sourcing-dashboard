#!/usr/bin/env python3
"""Импорт рабочей Google-таблицы снабжения (котировки поставщиков + карта
производителей) в базу ЗИП.

Источник: zip/tools/sources/kvant_sourcing_sheet.xlsx — снимок Google-таблицы
  https://docs.google.com/spreadsheets/d/1P0N672eTM8n_em3GsWTfWgdc4dDTSKFCWuKCqHFNBEg
Обновить снимок:  make refresh (или curl export?format=xlsx) — см. README.

Выход (идемпотентно):
  - price_records.json += реальные котировки китайских поставщиков из «File A»
    (source=«КП поставщика (таблица снабжения)»), привязка по OEM PN к позиции;
  - zip/data/sheet_meta.json — по позициям: группа, применение (COP),
    кандидаты-производители, примечание, цена оригинала, статус пробников.
"""
import json
import re
import sys
from pathlib import Path
from datetime import date

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
XLSX = ROOT / "tools" / "sources" / "kvant_sourcing_sheet.xlsx"
QUOTE_SRC = "КП поставщика (таблица снабжения)"

norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def fnum(v):
    s = str(v or "").strip().replace(",", ".")
    if not s:
        return None
    m = re.search(r"\d+(\.\d+)?", s)
    if not m:
        return None
    try:
        f = float(m.group())
        return f if f > 0 else None
    except ValueError:
        return None


def pn_map():
    pos = json.loads((DATA / "positions.json").read_text(encoding="utf-8"))
    m = {}
    for p in pos:
        m.setdefault(norm(p.get("catalog_no")), p["id"])
        for t in re.split(r"[\s,;/]+", str(p.get("aliases") or "")):
            n = norm(t)
            if len(n) >= 6:
                m.setdefault(n, p["id"])
    return pos, m


def rows_of(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)
            if any(str(c).strip() for c in r if c is not None)]


def main():
    if not XLSX.exists():
        print(f"нет файла {XLSX}"); sys.exit(1)
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    pos, pmap = pn_map()

    # --- 1) котировки поставщиков из File A ---
    prices = json.loads((DATA / "price_records.json").read_text(encoding="utf-8"))
    prices = [r for r in prices if (r.get("source") or "") != QUOTE_SRC]  # идемпотентно
    max_id = max([r.get("id", 0) for r in prices] + [0])
    A = rows_of(wb["File A (drifter parts)"])
    hdr = [str(c or "") for c in A[0]]
    # ценовые колонки: «Имя, ВАЛЮТА»
    pcols = {}
    for i, h in enumerate(hdr):
        m = re.match(r"(.+?),\s*(USD|CNY|EUR|RUB)", h, re.I)
        if m:
            pcols[i] = (m.group(1).strip(), m.group(2).upper())
    added = 0
    for row in A[1:]:
        if len(row) < 2 or not row[1]:
            continue
        pid = pmap.get(norm(row[1]))
        if pid is None:
            continue
        desc = str(row[3] or "")[:60]
        app = str(row[4] or "")
        for i, (supp, cur) in pcols.items():
            if i >= len(row):
                continue
            price = fnum(row[i])
            if price is None:
                continue
            max_id += 1
            prices.append({
                "id": max_id, "position_id": pid, "year": None,
                "source": QUOTE_SRC, "importer": "", "exporter": supp,
                "country": "Китай", "qty": None, "unit_price": price,
                "currency": cur, "incoterms": "EXW" if "EXW" in hdr[i] else "",
                "url": "", "confidence": "high",
                "note": f"котировка {supp} · {desc}{(' · '+app) if app else ''}"[:200],
            })
            added += 1
    (DATA / "price_records.json").write_text(
        json.dumps(prices, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- 2) карта производителей / применение / пробники → sheet_meta.json ---
    P = rows_of(wb["Производители по позициям"])
    ph = [str(c or "") for c in P[0]]
    def col(name):
        for i, h in enumerate(ph):
            if name.lower() in h.lower():
                return i
        return None
    ci = {k: col(k) for k in ["OEM", "Описание", "Применение", "Группа",
                              "Производитель 1", "Производитель 2", "Производитель 3",
                              "Примечание", "Оригинал", "Пробники"]}
    meta = {}
    for row in P[1:]:
        gi = ci["OEM"]
        if gi is None or gi >= len(row) or not row[gi]:
            continue
        pid = pmap.get(norm(row[gi]))
        if pid is None:
            continue
        g = lambda k: (str(row[ci[k]]).strip() if ci[k] is not None and ci[k] < len(row) and row[ci[k]] is not None else "")
        mans = [g(f"Производитель {n}") for n in (1, 2, 3)]
        mans = [x for x in mans if x]
        meta[pid] = {
            "group": g("Группа"), "application_cop": g("Применение"),
            "manufacturers": mans, "note": g("Примечание"),
            "original": g("Оригинал"), "samples": g("Пробники"),
        }
    (DATA / "sheet_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    npos_q = len({r["position_id"] for r in prices if r.get("source") == QUOTE_SRC})
    nman = sum(1 for v in meta.values() if v["manufacturers"])
    print(f"котировки: +{added} записей по {npos_q} позициям (File A)")
    print(f"sheet_meta: {len(meta)} позиций, из них с производителем {nman}")
    print(f"updated {date.today().isoformat()}")


if __name__ == "__main__":
    main()
