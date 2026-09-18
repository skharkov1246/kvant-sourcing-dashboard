#!/usr/bin/env python3
"""Английский первоисточник заявки «Энергосети»: расшифровка, а НЕ подтверждение.

Два вывода, и второй важнее первого.

1. НЕ ПОДТВЕРЖДЕНИЕ. Страница gas-turbine-parts.com/News/Product_FAQ/151.html
   выглядит как чужой каталог ЗИП SGT-400, и на неё уже ссылались как на
   независимую проверку номера. Замер это опроверг: из 436 её строк 431 (98,9 %)
   стоят в листе «Энергосети» нашей заявки, количества совпадают у 425. Это не
   каталог, а англоязычный ПЕРВОИСТОЧНИК самой заявки: русские наименования —
   его построчный машинный перевод («NICKEL PLATED FLAT WASHER - 5/8» →
   «ШАЙБА ПЛОСКАЯ С НИКЕЛЕМ - 5/8»). Ссылаться на неё как на подтверждение
   нельзя: это то же самое правило, что «эталон не может быть производным от
   правила» — источник, оказавшийся первоисточником заявки, заявку не проверяет.

2. ЗАТО ЭТО РАСШИФРОВКА. У 431 строки появляется наименование на языке, на
   котором её поймёт западный продавец. Русский машинный перевод не ищется ни
   в одном каталоге, английский оригинал ищется. В запрос цены идёт он.

    python gt/tools/english_source.py --html <файл> --write   # собрать набор
    python gt/tools/english_source.py --check                 # сверить с заявкой
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "gt/data/ship_english_source.json"
DEMAND = ROOT / "gt/data/rfq_demand.json"
URL = "http://www.gas-turbine-parts.com/News/Product_FAQ/151.html"
LINE = re.compile(r"^\s*(\d{1,4})\t([^\t]+)\t([^\t]+)\t(\d+)\t(\w+)\s*$")


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").upper())


def parse(raw_html: str) -> list[dict]:
    """Строки первоисточника: номер, английское наименование, количество."""
    text = html.unescape(re.sub(r"<[^>]+>", "\n", raw_html))
    out = []
    for line in text.split("\n"):
        m = LINE.match(line)
        if m:
            out.append({"no": int(m.group(1)), "pn": m.group(2).strip(),
                        "name_en": m.group(3).strip(), "qty_en": int(m.group(4)),
                        "unit": m.group(5)})
    return out


def match(src: list[dict]) -> tuple[list[dict], dict]:
    """Сверка с заявкой: что совпало, где разошлось количество."""
    demand = json.loads(DEMAND.read_text(encoding="utf-8"))["rows"]
    ask: dict[str, list[dict]] = collections.defaultdict(list)
    for r in demand:
        ask[key(r.get("pn"))].append(r)

    rows, hit, qty_ok = [], 0, 0
    for s in src:
        got = ask.get(key(s["pn"])) or []
        row = dict(s)
        row["in_request"] = bool(got)
        row["name_ru"] = (got[0].get("name") or "").strip() if got else ""
        row["sheet"] = got[0].get("sheet") if got else ""
        row["qty_request"] = sum(float(x.get("qty") or 0) for x in got) if got else None
        row["lines_in_request"] = len(got)
        row["qty_match"] = bool(got) and any(
            abs(float(x.get("qty") or 0) - s["qty_en"]) < 0.5 for x in got)
        # Наименование, которое можно ставить в запрос западному продавцу.
        row["ask_as"] = s["name_en"]
        rows.append(row)
        if got:
            hit += 1
            if row["qty_match"]:
                qty_ok += 1
    totals = {
        "source_rows": len(src),
        "source_pns": len({key(s["pn"]) for s in src}),
        "in_request": hit,
        "share_pct": round(hit / len(src) * 100, 1) if src else 0.0,
        "qty_match": qty_ok,
        "sheets": sorted({r["sheet"] for r in rows if r["sheet"]}),
    }
    return rows, totals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="сохранённая страница первоисточника")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if a.write:
        if not a.html:
            print("нужен --html: страница первоисточника", file=sys.stderr)
            return 1
        src = parse(Path(a.html).read_text(encoding="utf-8", errors="replace"))
        if not src:
            print("в странице не нашлось ни одной строки вида «№ номер наименование "
                  "кол-во ед.»", file=sys.stderr)
            return 1
        rows, totals = match(src)
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": f"Англоязычный первоисточник листа «Энергосети» заявки ЛУКОЙЛ: {URL}",
            "method": "Строка страницы имеет вид «№ · номер · наименование на английском · "
                      "количество · единица». Сверка с gt/data/rfq_demand.json идёт по "
                      "нормализованному номеру.",
            "not_a_confirmation": "Эта страница НЕ подтверждает заявку: она и есть её "
                                  "англоязычный первоисточник — 431 строка из 436 стоит в "
                                  "заявке, количества совпадают у 425. Ссылаться на неё как на "
                                  "независимый каталог нельзя, вывод получится круговым.",
            "what_it_is_for": "Расшифровка. Русское наименование — построчный машинный перевод, "
                              "он не ищется ни в одном каталоге; английский оригинал ищется и "
                              "идёт в запрос цены западному продавцу (поле ask_as).",
            "totals": totals,
            "rows": rows,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"записано {len(rows)} строк в {OUT.relative_to(ROOT)}; "
              f"в заявке {totals['in_request']} ({totals['share_pct']} %), "
              f"количество совпадает у {totals['qty_match']}")
        return 0

    if not OUT.exists():
        print("набора нет — соберите: --html <файл> --write", file=sys.stderr)
        return 1
    doc = json.loads(OUT.read_text(encoding="utf-8"))
    src = [{k: r[k] for k in ("no", "pn", "name_en", "qty_en", "unit")} for r in doc["rows"]]
    _, totals = match(src)
    if totals != doc["totals"]:
        print(f"набор расходится с заявкой: в наборе {doc['totals']}, замер {totals}",
              file=sys.stderr)
        return 1
    print(f"✓ набор сходится с заявкой: {totals['in_request']} из {totals['source_rows']} "
          f"строк первоисточника стоят в ней")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
