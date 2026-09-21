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

# Как называется нужное поле у разных баз. Для каждого поля: что должно встретиться
# в заголовке и что встречаться не должно. Отрицательные признаки обязательны:
# в выгрузке ГТД рядом стоят «ИНН отправителя» и «ИНН получателя», «Наименование
# получателя» и «Адрес получателя», «Общая таможенная стоимость» и «Фактурная
# стоимость» — без отсева первый же похожий заголовок забирает поле себе.
FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "date": (["дата регистрации", "дата декларац", "g072", "дата"], ["выпуск", "оплат"]),
    "decl": (["номер декларации", "№ декларации", "g281", "гтд"], ["бланк", "листов"]),
    "recipient": (["наименование получател", "получател", "импортер", "импортёр", "g082"],
                  ["инн", "адрес", "код", "огрн", "кпп", "стран"]),
    # голый «ИНН» в простых выгрузках — это ИНН получателя; чужие ИНН отсекаем явно
    "inn": (["инн получател", "g081", "инн"],
            ["отправител", "экспортер", "экспортёр", "декларант", "контрактодержател", "перевозчик"]),
    "sender": (["наименование отправител", "отправител", "экспортер", "экспортёр", "g022", "контрагент"],
               ["инн", "адрес", "код", "огрн", "кпп", "стран", "дата"]),
    "producer": (["изготовител", "производител", "g31_11", "producer"], ["стран"]),
    "brand": (["товарный знак", "марка", "бренд", "g31_12"], []),
    "country": (["страна происхожд", "страны происхожд", "g16"], ["код"]),
    "dispatch": (["страна отправл", "страны отправл", "g15a"], []),
    "hs": (["тн вэд", "тнвэд", "код товара", "g33", "hs"], []),
    "desc": (["описание", "наименование товара", "g31_1"], ["всего", "знак"]),
    "qty": (["количество товара", "кол-во товара", "g31_7"], ["мест", "листов", "наименован", "транспорт"]),
    "net": (["вес нетто", "нетто", "g38"], []),
    "value": (["фактурная", "стоимость", "инвойс", "g42"], ["таможенная", "общая", "статистическ"]),
}
PARTS_HS = ("8483", "8484", "7325", "8482", "8481")   # комплектующие и литьё
PUMPS_HS = ("8413",)                                   # готовые насосы
DRIVE_HS = ("8406", "8501", "8502")                    # турбины и электродвигатели


def _col(name: str) -> str | None:
    low = str(name or "").strip().lower()
    for key, (marks, stop) in FIELDS.items():
        if any(m in low for m in marks) and not any(x in low for x in stop):
            return key
    return None


def _colnum(ref: str) -> int:
    """A1 -> 0, B2 -> 1. Позиция нужна: пустые ячейки в XML просто пропущены."""
    n = 0
    for ch in re.match(r"([A-Z]*)", str(ref or "")).group(1):
        n = n * 26 + (ord(ch) - 64)
    return max(n - 1, 0)


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
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            return []
        rows: list[dict[int, str]] = []
        for row in ET.fromstring(z.read(sheets[0])).iter(f"{ns}row"):
            cells: dict[int, str] = {}
            for c in row.iter(f"{ns}c"):
                if c.get("t") == "inlineStr":
                    txt = "".join(t.text or "" for t in c.iter(f"{ns}t"))
                else:
                    v = c.find(f"{ns}v")
                    txt = "" if v is None else (v.text or "")
                    if c.get("t") == "s" and txt.isdigit():
                        txt = shared[int(txt)] if int(txt) < len(shared) else ""
                cells[_colnum(c.get("r"))] = txt.strip()
            rows.append(cells)
    if not rows:
        return []
    head = rows[0]
    return [{head.get(i, str(i)): v for i, v in r.items()} for r in rows[1:] if any(r.values())]


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


def as_date(s: str) -> tuple[str, str, str] | None:
    """ГГГГ-ММ-ДД или ДД.ММ.ГГГГ -> (год, месяц, день). Границы периода нельзя брать
    сравнением строк: «20.07.2022» лексически больше «03.11.2022»."""
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    if iso:
        return iso.group(1), iso.group(2), iso.group(3)
    rus = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", str(s or ""))
    return (rus.group(3), rus.group(2), rus.group(1)) if rus else None


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
        parsed = as_date(r.get("date", ""))
        if parsed:
            year, month, _ = parsed
            quarters[f"{year}-Q{(int(month) - 1) // 3 + 1}"] += 1

    total = len(rows)
    share = lambda group: round(100 * sum(v for k, v in hs4.items() if k.startswith(group)) / total, 1)
    print(f"строк после фильтра: {total}")
    dated = sorted((r["date"] for r in rows if as_date(r.get("date", ""))), key=as_date)
    print(f"период: {dated[0] if dated else '—'} … {dated[-1] if dated else '—'}")
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
