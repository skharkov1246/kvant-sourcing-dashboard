#!/usr/bin/env python3
"""Английский первоисточник заявки «Энергосети»: расшифровка, а НЕ подтверждение.

Два вывода, и второй важнее первого.

1. НЕ ПОДТВЕРЖДЕНИЕ. Страница gas-turbine-parts.com/News/Product_FAQ/151.html
   выглядит как чужой каталог ЗИП SGT-400, и на неё уже ссылались как на
   независимую проверку номера. Замер это опроверг: из 436 её строк 431 (98,9 %)
   стоят в листе «Энергосети» нашей заявки. Это не
   каталог, а англоязычный ПЕРВОИСТОЧНИК самой заявки: русские наименования —
   его построчный машинный перевод («NICKEL PLATED FLAT WASHER - 5/8» →
   «ШАЙБА ПЛОСКАЯ С НИКЕЛЕМ - 5/8»). Ссылаться на неё как на подтверждение
   нельзя: это то же самое правило, что «эталон не может быть производным от
   правила» — источник, оказавшийся первоисточником заявки, заявку не проверяет.

2. КОЛИЧЕСТВО С НИМ НЕ СХОДИТСЯ, и первая мера этого не показывала. «Количества
   совпадают у 425» означало лишь, что количество первоисточника встречается
   среди строк заявки по этому номеру. Равенство СУММ держится только у 337 из
   431, а у 66 номеров заявка просит больше своего же английского листа — это
   489 666 USD экспозиции на количестве, которое первоисточником не подтверждено.
   Обвинением это не является: лист может покрывать меньше машин, чем заявка.
   Обе меры теперь считаются и печатаются раздельно (`qty_match_line` и
   `qty_match_total`), а разница — в `totals.qty_gap`.

3. ЗАТО ЭТО РАСШИФРОВКА. У 431 строки появляется наименование на языке, на
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
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
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
    """Сверка с заявкой: что совпало, где разошлось количество.

    Количество сверяется ДВУМЯ мерами, и путать их нельзя — на этом уже
    получилась ложная цифра в отчёте владельцу.

    `qty_match_line` — слабая мера: количество первоисточника ВСТРЕЧАЕТСЯ среди
    строк заявки по этому номеру. Она была единственной и печаталась словами
    «количество совпадает у 425», хотя означает совсем другое: у MW35024B/01
    первоисточник несёт 8 шт, заявка — 8 плюс 16 отдельной строкой, итого 24, и
    слабая мера говорит «совпало».

    `qty_match_total` — сильная мера: СУММА строк заявки по номеру равна сумме
    строк первоисточника. Это и есть «количество сходится». В заголовок идёт она.
    """
    demand = json.loads(DEMAND.read_text(encoding="utf-8"))["rows"]
    ask: dict[str, list[dict]] = collections.defaultdict(list)
    for r in demand:
        ask[key(r.get("pn"))].append(r)
    # Первоисточник тоже бывает многострочным по одному номеру (прокладки
    # 346321100 идут тремя строками), поэтому сильная мера считается по сумме.
    src_qty: dict[str, float] = collections.defaultdict(float)
    for s in src:
        src_qty[key(s["pn"])] += float(s["qty_en"] or 0)

    rows, hit, qty_line, qty_total = [], 0, 0, 0
    for s in src:
        k = key(s["pn"])
        got = ask.get(k) or []
        row = dict(s)
        row["in_request"] = bool(got)
        row["name_ru"] = (got[0].get("name") or "").strip() if got else ""
        row["sheet"] = got[0].get("sheet") if got else ""
        row["qty_request"] = sum(float(x.get("qty") or 0) for x in got) if got else None
        row["lines_in_request"] = len(got)
        row["qty_source_pn"] = src_qty[k]
        row["qty_match_line"] = bool(got) and any(
            abs(float(x.get("qty") or 0) - s["qty_en"]) < 0.5 for x in got)
        row["qty_match_total"] = bool(got) and abs(
            float(row["qty_request"] or 0) - src_qty[k]) < 0.5
        # Наименование, которое можно ставить в запрос западному продавцу.
        row["ask_as"] = s["name_en"]
        rows.append(row)
        if got:
            hit += 1
            qty_line += int(row["qty_match_line"])
            qty_total += int(row["qty_match_total"])
    totals = {
        "source_rows": len(src),
        "source_pns": len({key(s["pn"]) for s in src}),
        "in_request": hit,
        "share_pct": round(hit / len(src) * 100, 1) if src else 0.0,
        "qty_match_line": qty_line,
        "qty_match_total": qty_total,
        "sheets": sorted({r["sheet"] for r in rows if r["sheet"]}),
    }
    totals["qty_gap"] = gap(src_qty)
    return rows, totals


def gap(src_qty: dict[str, float]) -> dict:
    """Сколько денег стоит расхождение количества со своим же первоисточником.

    Заявка просит БОЛЬШЕ, чем её собственный английский лист, у части номеров.
    Объяснений два, и выбрать между ними может только заказчик: либо лист
    покрывает меньше машин, чем заявка, либо сводка складывает строку-перевод
    первоисточника со строкой русского блока об ТОЙ ЖЕ позиции. Поэтому здесь
    НЕ пишется «переплата»: пишется, сколько экспозиции стоит на количестве,
    которое первоисточником не подтверждено.
    """
    if not SUMMARY.exists():
        return {}
    rows = json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]
    by_pn = {key(r.get("pn")): r for r in rows}
    seen = covered = over = 0
    usd_summary = usd_source = 0.0
    top: list[dict] = []
    for k, q_src in src_qty.items():
        r = by_pn.get(k)
        if r is None:
            continue
        lo, hi, qty = r.get("usd_lo"), r.get("usd_hi"), float(r.get("qty") or 0)
        if lo is None or hi is None:
            continue
        seen += 1
        mid = (float(lo) + float(hi)) / 2
        usd_summary += mid * qty
        usd_source += mid * q_src
        if abs(qty - q_src) < 0.5:
            covered += 1
        elif qty > q_src:
            over += 1
            top.append({"pn": r.get("pn"), "qty_summary": qty, "qty_source": q_src,
                        "usd_gap": round(mid * (qty - q_src), 2)})
    top.sort(key=lambda x: -x["usd_gap"])
    # Список нужен ВЕСЬ: по нему уходит вопрос заказчику, а обрезанный список
    # молча превращает «подтвердите 66 номеров» в «подтвердите 20».
    return {
        "pns_measured": seen,
        "qty_equal": covered,
        "qty_summary_above_source": over,
        "usd_by_summary_qty": round(usd_summary, 2),
        "usd_by_source_qty": round(usd_source, 2),
        "usd_at_stake": round(usd_summary - usd_source, 2),
        "top": top,
    }


def document(rows: list[dict], totals: dict) -> str:
    """Один сборщик файла для --write и --remeasure: расхождений быть не может."""
    g = totals.get("qty_gap") or {}
    return json.dumps({
        "updated": "2026-09-18",
        "source": f"Англоязычный первоисточник листа «Энергосети» заявки ЛУКОЙЛ: {URL}",
        "method": "Строка страницы имеет вид «№ · номер · наименование на английском · "
                  "количество · единица». Сверка с gt/data/rfq_demand.json идёт по "
                  "нормализованному номеру. Количество сверяется двумя мерами: "
                  "qty_match_line — количество первоисточника встречается среди строк "
                  "заявки; qty_match_total — сумма строк заявки равна сумме строк "
                  "первоисточника. Сильная мера — вторая.",
        "not_a_confirmation": "Эта страница НЕ подтверждает заявку: она и есть её "
                              f"англоязычный первоисточник — {totals['in_request']} строк из "
                              f"{totals['source_rows']} стоят в заявке. Ссылаться на неё как на "
                              "независимый каталог нельзя, вывод получится круговым.",
        "what_it_is_for": "Расшифровка. Русское наименование — построчный машинный перевод, "
                          "он не ищется ни в одном каталоге; английский оригинал ищется и "
                          "идёт в запрос цены западному продавцу (поле ask_as).",
        "qty_warning": "Сильная мера расходится со слабой: количество сходится у "
                       f"{totals['qty_match_total']} строк из {totals['in_request']}, а не у "
                       f"{totals['qty_match_line']}, как показывала слабая. У "
                       f"{g.get('qty_summary_above_source', 0)} номеров заявка просит больше "
                       "своего же английского листа — это "
                       f"{g.get('usd_at_stake', 0):.0f} USD экспозиции на количестве, которое "
                       "первоисточником не подтверждено. Объяснений два, и выбрать может только "
                       "заказчик: лист покрывает меньше машин, чем заявка, либо сводка "
                       "складывает строку-перевод первоисточника со строкой русского блока об "
                       "одной и той же позиции.",
        "totals": totals,
        "rows": rows,
    }, ensure_ascii=False, indent=1) + "\n"


def report(totals: dict) -> str:
    g = totals.get("qty_gap") or {}
    return (f"в заявке {totals['in_request']} ({totals['share_pct']} %), "
            f"количество сходится по сумме у {totals['qty_match_total']} "
            f"(слабая мера показывала {totals['qty_match_line']}); "
            f"заявка просит больше первоисточника у {g.get('qty_summary_above_source', 0)} "
            f"номеров на {g.get('usd_at_stake', 0):,.0f} USD".replace(",", " "))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="сохранённая страница первоисточника")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--remeasure", action="store_true",
                    help="пересчитать замеры по уже собранным строкам, без страницы")
    a = ap.parse_args()

    if a.remeasure:
        if not OUT.exists():
            print("набора нет — соберите: --html <файл> --write", file=sys.stderr)
            return 1
        doc = json.loads(OUT.read_text(encoding="utf-8"))
        src = [{k: r[k] for k in ("no", "pn", "name_en", "qty_en", "unit")}
               for r in doc["rows"]]
        rows, totals = match(src)
        OUT.write_text(document(rows, totals), encoding="utf-8")
        print(report(totals))
        return 0

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
        OUT.write_text(document(rows, totals), encoding="utf-8")
        print(f"записано {len(rows)} строк в {OUT.relative_to(ROOT)}; " + report(totals))
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
