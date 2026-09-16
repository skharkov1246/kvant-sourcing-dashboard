#!/usr/bin/env python3
"""Разбор таможенной выгрузки по компании: у кого покупают, чем и в каком темпе.

Принимает то, что реально приходит от баз ВЭД: XLSX (без внешних библиотек —
читаем как zip с XML), CSV и наш JSON от glbs.io. Колонки у каждой базы свои,
поэтому сопоставляем по ключевым словам, а не по позиции.

    python3 zip/customs/analyze_export.py выгрузка.xlsx --inn 7719740695
    python3 zip/customs/analyze_export.py вывоз.csv --name ГАМБИТ --json итог.json

Зачем именно так: ответ на вопрос «у кого покупают» — это не список строк, а
свод по отправителю, стране и номенклатуре. Отдельно считаем долю готовых машин
(8413) против комплектующих (8483, 8484, 7325, 8482): ввоз под перемаркировку и
ввоз под сборку выглядят в данных по-разному.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

# как называется нужное поле у разных баз (ищем вхождение, регистр не важен)
FIELDS = {
    "date": ["дата", "g072", "gd1", "дата декларации"],
    "decl": ["номер декларации", "№ декларации", "g281", "гтд"],
    "recipient": ["получател", "импортер", "импортёр", "g082"],
    "inn": ["инн", "g081"],
    "sender": ["отправител", "экспортер", "экспортёр", "g31_11", "контрагент", "g022"],
    "producer": ["изготовител", "производител", "producer"],
    "brand": ["товарный знак", "марка", "бренд", "g31_12"],
    "country": ["страна происхожд", "g34"],
    "dispatch": ["страна отправл", "g15a"],
    "hs": ["тн вэд", "тнвэд", "код товара", "g33", "hs"],
    "desc": ["описание", "наименование товара", "g31_1"],
    "qty": ["количество", "кол-во", "g0121"],
    "net": ["вес нетто", "нетто", "g38"],
    "value": ["фактурная", "стоимость", "g42", "инвойс"],
}
PARTS_HS = ("8483", "8484", "7325", "8482", "8481")   # комплектующие и литьё
PUMPS_HS = ("8413",)                                   # готовые насосы
DRIVE_HS = ("8406", "8501", "8502")                    # турбины и электродвигатели


def _col(name: str) -> str | None:
    low = str(name or "").strip().lower()
    for key, marks in FIELDS.items():
        if any(m in low for m in marks):
            return key
    return None


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        return [dict(r) for r in csv.DictReader(f, dialect=dialect)]


def read_xlsx(path: Path) -> list[dict]:
    """XLSX — это zip с XML: первый лист плюс таблица общих строк."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        sheets = [n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        rows: list[list[str]] = []
        for row in ET.fromstring(z.read(sorted(sheets)[0])).iter(f"{ns}row"):
            cells: list[str] = []
            for c in row.iter(f"{ns}c"):
                v = c.find(f"{ns}v")
                txt = "" if v is None else (v.text or "")
                if c.get("t") == "s" and txt.isdigit():
                    txt = shared[int(txt)] if int(txt) < len(shared) else ""
                idx = re.match(r"([A-Z]+)", c.get("r") or "")
                pos = 0
                for ch in (idx.group(1) if idx else ""):
                    pos = pos * 26 + (ord(ch) - 64)
                while len(cells) < max(pos - 1, 0):
                    cells.append("")
                cells.append(txt)
            rows.append(cells)
    if not rows:
        return []
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[1:] if any(x for x in r)]


def read_glbs_json(path: Path) -> list[dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(d, dict) and "matched" in d:
        return d["matched"]
    if isinstance(d, dict) and isinstance(d.get("search"), dict):
        return d["search"].get("result", [])
    if isinstance(d, list):
        return d
    raise SystemExit("не понял формат JSON: ждал matched или search.result")


def normalize(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rec: dict[str, str] = {}
        for k, v in r.items():
            key = _col(k)
            if key and not rec.get(key):
                rec[key] = str(v or "").strip()
        if any(rec.values()):
            out.append(rec)
    return out


def num(s: str) -> float:
    try:
        return float(re.sub(r"[^\d.,-]", "", str(s)).replace(",", ".") or 0)
    except ValueError:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Свод таможенной выгрузки по компании")
    ap.add_argument("file", help="XLSX, CSV или JSON от glbs.io")
    ap.add_argument("--inn", help="оставить строки с этим ИНН получателя")
    ap.add_argument("--name", help="оставить строки, где в получателе есть эта подстрока")
    ap.add_argument("--top", type=int, default=15, help="сколько отправителей показать")
    ap.add_argument("--json", help="куда записать свод машиночитаемо")
    a = ap.parse_args()

    path = Path(a.file)
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        return 1
    raw = {".csv": read_csv, ".xlsx": read_xlsx, ".json": read_glbs_json}.get(path.suffix.lower())
    if not raw:
        print("поддерживаются .xlsx, .csv, .json", file=sys.stderr)
        return 1
    rows = normalize(raw(path))
    if not rows:
        print("в файле нет строк", file=sys.stderr)
        return 1

    if a.inn:
        rows = [r for r in rows if re.sub(r"\D", "", r.get("inn", "")) == re.sub(r"\D", "", a.inn)]
    if a.name:
        rows = [r for r in rows if a.name.upper() in r.get("recipient", "").upper()]
    if not rows:
        print("после фильтра не осталось строк — проверь ИНН или имя", file=sys.stderr)
        return 1

    senders: Counter[str] = Counter()
    s_weight: defaultdict[str, float] = defaultdict(float)
    s_value: defaultdict[str, float] = defaultdict(float)
    countries: Counter[str] = Counter()
    hs4: Counter[str] = Counter()
    quarters: Counter[str] = Counter()
    for r in rows:
        snd = r.get("sender") or r.get("producer") or "—"
        senders[snd] += 1
        s_weight[snd] += num(r.get("net", ""))
        s_value[snd] += num(r.get("value", ""))
        countries[(r.get("country") or r.get("dispatch") or "—")] += 1
        code = re.sub(r"\D", "", r.get("hs", ""))[:4]
        if code:
            hs4[code] += 1
        m = re.search(r"(\d{4})-(\d{2})", r.get("date", ""))
        if m:
            quarters[f"{m.group(1)}-Q{(int(m.group(2)) - 1) // 3 + 1}"] += 1

    total = len(rows)
    share = lambda group: round(100 * sum(v for k, v in hs4.items() if k.startswith(group)) / total, 1)
    print(f"строк после фильтра: {total}")
    print(f"период: {min((r.get('date','') for r in rows if r.get('date')), default='—')}"
          f" … {max((r.get('date','') for r in rows if r.get('date')), default='—')}")
    print(f"\nотправители (топ {a.top}):")
    for snd, cnt in senders.most_common(a.top):
        print(f"  {cnt:4d} · нетто {s_weight[snd]:12,.0f} кг · сумма {s_value[snd]:14,.0f} · {snd[:70]}")
    print("\nстраны:", ", ".join(f"{k} — {v}" for k, v in countries.most_common(10)))
    print("ТН ВЭД (4 знака):", ", ".join(f"{k} — {v}" for k, v in hs4.most_common(12)))
    print("по кварталам:", ", ".join(f"{k} — {v}" for k, v in sorted(quarters.items())))
    print(f"\nготовые насосы 8413: {share(PUMPS_HS[0])} % строк"
          f" · приводы 8406/85: {sum(share(g) for g in DRIVE_HS)} %"
          f" · комплектующие {'/'.join(PARTS_HS)}: {sum(share(g) for g in PARTS_HS)} %")

    if a.json:
        Path(a.json).write_text(json.dumps({
            "rows": total,
            "senders": [{"sender": s, "count": c, "net_kg": round(s_weight[s], 1),
                         "value": round(s_value[s], 2)} for s, c in senders.most_common()],
            "countries": countries.most_common(),
            "hs4": hs4.most_common(),
            "quarters": sorted(quarters.items()),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nсвод записан: {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
