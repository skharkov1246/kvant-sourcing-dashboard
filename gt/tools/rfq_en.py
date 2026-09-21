#!/usr/bin/env python3
"""Запрос цен на английском по строкам заявки, у которых нет цены.

Зачем. Русские наименования листа «Энергосети» — построчный машинный перевод с
английского («NICKEL PLATED FLAT WASHER - 5/8» → «ШАЙБА ПЛОСКАЯ С НИКЕЛЕМ -
5/8»), и обратный перевод не ищется ни в одном каталоге. Английский оригинал
восстановлен (gt/data/ship_english_source.json) — значит по этим строкам можно
спрашивать цену у западного продавца на его языке, а не пересказывать перевод.

Замер: у 330 строк, по которым цены продавца нет вовсе, английский оригинал
есть. Из них 144 — крепёж и уплотнения, то есть номенклатура, которую квотируют
по описанию, даже не зная машины.

Документ готов к отправке: первая страница объясняет владельцу, что это и на
чём собрано, дальше идёт сам запрос на английском — вводная часть с перечнем
обязательных полей ответа и таблицы по группам номенклатуры.

Цен в документе нет ни одной: это запрос, а не оферта.

    python gt/tools/rfq_en.py            # собрать PDF
    python gt/tools/rfq_en.py --stats    # только замер, без сборки
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "gt/data"
OUT = ROOT / "gt/docs"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

# Что обязан содержать ответ. Список оплачен разбором выкладки: цена без
# подтверждённого остатка и без срока действия в сумму закупки не идёт.
ASKS = [
    ("Unit price and total price", "for the full quantity stated, not for one piece"),
    ("Confirmed stock as a number", "how many pieces you hold today, not «in stock»"),
    ("Lead time for the full quantity", "counted from order, split by batches if needed"),
    ("Validity of the quotation", "date until which the price holds"),
    ("Incoterms and place of delivery", ""),
    ("Origin of goods and HS code", "country of manufacture, not of shipment"),
    ("Original or equivalent", "state clearly if the item offered is not OEM-original, "
                               "and give its own part number and maker"),
    ("Net weight and dimensions per item", "needed for freight, not for the price"),
    ("Return conditions", "whether the item is returnable and non-cancelable"),
]

CSS = """
@page { size: A4; margin: 13mm 12mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #111; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 2mm; }
h2 { font-size: 12pt; margin: 5mm 0 2.5mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm;
     page-break-after: avoid; }
h3 { font-size: 10pt; margin: 4mm 0 1.5mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.45; }
.sec { page-break-before: always; }
/* Группы номенклатуры идут подряд: принудительный разрыв на каждой группе
   оставлял полупустые страницы (семь на прогоне 18.09.2026). */
.lead { font-size: 9.6pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tr { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.3mm 1.6mm; font-size: 8pt; }
.t td { padding: 1.3mm 1.6mm; word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tbody tr:nth-child(even) td { background: #f6f6f6; }
.t td.n, .t th.n { text-align: right; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.5mm 3mm 1.5mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; width: 78mm; }
.k td.v { width: 30mm; font-size: 11pt; font-weight: bold; white-space: nowrap; }
ol, ul { margin: 0 0 3mm; padding-left: 5.5mm; }
li { margin-bottom: 1.6mm; line-height: 1.45; }
.warn { border-left: 2.4pt solid #111; padding-left: 3.5mm; margin: 0 0 3.5mm; }
"""


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").upper())


def load(name: str):
    p = D / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pick() -> tuple[list[dict], dict]:
    """Строки заявки без цены продавца, у которых есть английский оригинал."""
    en_doc = load("ship_english_source.json") or {}
    en = {key(r["pn"]): r for r in (en_doc.get("rows") or []) if r.get("in_request")}
    rows = (load("ship_lukoil.json") or {}).get("rows") or []
    out = []
    for r in rows:
        if r.get("unit_price_usd") not in (None, ""):
            continue          # цена уже есть — спрашивать нечего
        e = en.get(key(r["pn"]))
        if not e:
            continue
        out.append({**r, "name_en": e["name_en"], "unit_en": e.get("unit") or "pcs"})
    out.sort(key=lambda r: (r.get("cat") or "", str(r.get("pn"))))
    meta = {
        "rows_total": len(rows),
        "no_price": sum(1 for r in rows if r.get("unit_price_usd") in (None, "")),
        "picked": len(out),
        "qty": sum(float(r.get("qty") or 0) for r in out),
        "makers": sorted({(r.get("man") or "—").strip() for r in out}),
    }
    return out, meta


def build(rows: list[dict], meta: dict) -> str:
    h = ["<!doctype html><meta charset='utf-8'><title>Request for quotation</title>"
         f"<style>{CSS}</style>"]
    a = h.append

    a("<h1>Запрос цен на английском: 330 строк, по которым цены нет</h1>")
    a("<p class='lead'>Первая страница — для владельца, дальше идёт сам запрос, "
      "готовый к отправке. Смысл документа в одном: русские наименования этого "
      "листа заявки — машинный перевод с английского, и обратный перевод не "
      "ищется ни в одном каталоге. Английский оригинал восстановлен, поэтому "
      "спрашивать можно на языке продавца.</p>")
    a("<table class='k'>")
    a(f"<tr><td class='l'>строк в запросе</td><td class='v'>{ru(meta['picked'])}</td>"
      f"<td class='dim'>из {ru(meta['no_price'])} строк заявки, где цены продавца нет "
      f"вовсе</td></tr>")
    a(f"<tr><td class='l'>штук за ними</td><td class='v'>{ru(meta['qty'])}</td>"
      f"<td class='dim'>количество взято из заявки, не пересчитывалось</td></tr>")
    by = collections.Counter((r.get("cat") or "—") for r in rows)
    top = " · ".join(f"{E(k)} {v}" for k, v in by.most_common(4))
    a(f"<tr><td class='l'>крупнейшие группы</td><td class='v'>{len(by)}</td>"
      f"<td class='dim'>{top}</td></tr>")
    a(f"<tr><td class='l'>изготовитель</td><td class='v'>{len(meta['makers'])}</td>"
      f"<td class='dim'>{E(', '.join(meta['makers'])[:120])}</td></tr>")
    a("</table>")
    a("<div class='warn'><p><b>Чего в документе нет и почему.</b> Ни одной цены: это "
      "запрос, а не оферта. Ни одной оценки: оценку в запросе печатать нельзя — "
      "продавец подстроится под неё. Английские наименования взяты из восстановленного "
      "первоисточника заявки и не переписывались: если в оригинале «RESISTANCE "
      "TEMPERATURE TRANSMITTER», в запрос идёт именно это, а не обратный перевод "
      "русской строки «сопротивление датчика температуры».</p></div>")
    a("<p class='dim'>Собирает gt/tools/rfq_en.py из gt/data/ship_lukoil.json и "
      "gt/data/ship_english_source.json.</p>")

    a("<div class='sec'><h2>Request for quotation</h2>")
    a("<p class='lead'>Dear Sirs, please quote the items listed below. The part numbers "
      "are the customer's own numbers as they appear in the specification; descriptions "
      "are given as printed in the original English parts list.</p>")
    a("<p><b>Please state for every line:</b></p><ol>")
    for what, why in ASKS:
        a(f"<li><b>{E(what)}</b>" + (f" — {E(why)}" if why else "") + "</li>")
    a("</ol>")
    a("<p><b>Two notes that save a round of e-mails.</b> If an item is not available, "
      "write «no» against that line rather than omitting it: a missing line is read here "
      "as «not answered». If you can offer an equivalent instead of the original, quote "
      "both and mark which is which — an equivalent is acceptable only when its own part "
      "number and maker are stated.</p>")
    a("</div>")

    for cat, items in sorted(collections.Counter(
            (r.get("cat") or "—") for r in rows).most_common()):
        group = [r for r in rows if (r.get("cat") or "—") == cat]
        a(f"<h2>{E(cat)} — {len(group)} lines</h2>")
        a("<table class='t'><colgroup><col style='width:26mm'><col>"
          "<col style='width:16mm'><col style='width:14mm'><col style='width:30mm'>"
          "</colgroup>")
        a("<thead><tr><th>Part number</th><th>Description</th><th class='n'>Qty</th>"
          "<th>Unit</th><th>Machine</th></tr></thead><tbody>")
        for r in group:
            a(f"<tr><td class='pn'>{E(r['pn'])}</td><td>{E(r['name_en'])}</td>"
              f"<td class='n'>{ru(r.get('qty'))}</td><td>{E(r['unit_en'])}</td>"
              f"<td>{E((r.get('model') or '')[:40])}</td></tr>")
        a("</tbody></table>")
    return "".join(h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="только замер")
    a = ap.parse_args()
    rows, meta = pick()
    print(f"строк заявки {meta['rows_total']}, без цены {meta['no_price']}, "
          f"в запрос попало {meta['picked']} ({ru(meta['qty'])} штук)")
    if a.stats:
        return 0
    if not rows:
        print("в запрос не попало ни одной строки", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    hp = OUT / "ЗАПРОС-ЦЕН-АНГЛИЙСКИЙ.html"
    pp = OUT / "ЗАПРОС-ЦЕН-АНГЛИЙСКИЙ.pdf"
    hp.write_text(build(rows, meta), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={pp}", hp.as_uri()],
        check=True, capture_output=True)
    print(f"{pp.name}: {pp.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
