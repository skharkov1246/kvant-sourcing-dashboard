#!/usr/bin/env python3
"""Вопросы к заказчику по заявке ЛУКОЙЛ: строки, по которым запрос уйдёт впустую.

Часть позиций нельзя закупать не потому, что не нашли продавца, а потому, что
неизвестно, ЧТО покупать. Просить цену по такой строке — тратить и своё время,
и время продавца: он ответит вопросом или, хуже, посчитает не то.

Документ собирает такие строки в один список с формулировкой вопроса, ценой
ошибки и тем, что мы уже установили сами. Он не для продавцов, а для разговора
с заказчиком: пока эти строки не расшифрованы, они не идут ни в запрос цен,
ни в сумму закупки.

Источник — gt/data/ship_questions.json, где каждая находка записана руками по
разбору агентских выдач: это не производная от данных, а вывод инженера.
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "gt/data/ship_questions.json"
DEMAND = ROOT / "gt/data/ship_lukoil.json"
EN = ROOT / "gt/data/ship_english_source.json"
HTML_OUT = ROOT / "gt/docs/ВОПРОСЫ-ЗАКАЗЧИКУ-ЛУКОЙЛ.html"
PDF_OUT = ROOT / "gt/docs/ВОПРОСЫ-ЗАКАЗЧИКУ-ЛУКОЙЛ.pdf"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

KIND_RU = {
    "фасовка": "Неизвестна фасовка",
    "единица": "Комплекты или штуки",
    "исполнение": "Не определено исполнение",
    "наименование": "Наименование не совпадает с каталогом",
    "номер": "Номер не существует или снят",
    "тип": "Не определён тип машины",
}
ORDER = ["фасовка", "единица", "номер", "тип", "исполнение", "наименование"]


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n))):,}".replace(",", " ")


CSS = """
@page { size: A4 portrait; margin: 13mm 12mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.6pt; color: #111; margin: 0; }
h1 { font-size: 18pt; margin: 0 0 2mm; }
h2 { font-size: 11.5pt; margin: 0 0 2.5mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
p { margin: 0 0 2.5mm; line-height: 1.42; }
.lead { font-size: 9pt; }
.dim { color: #666; }
.q { page-break-inside: avoid; border-top: 0.4pt solid #bbb; padding-top: 3mm; margin-bottom: 4.5mm; }
.q h3 { font-size: 10pt; margin: 0 0 1.5mm; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
.ask { border-left: 2.4pt solid #111; padding: 0.5mm 0 0.5mm 3mm; margin: 2mm 0; font-size: 9pt; }
.cost { background: #f4f4f4; padding: 2mm 2.5mm; margin: 2mm 0 0; font-size: 8.2pt; }
table.k { border-collapse: collapse; margin: 0 0 3.5mm; width: 100%; }
.k td { padding: 1.4mm 3mm 1.4mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; }
.big { font-size: 13pt; font-weight: bold; }
ol { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.6mm; line-height: 1.4; }
.sec { page-break-before: always; }
/* Таблица течёт сама: заголовок повторяется на каждой странице, строка не рвётся. */
table.t { border-collapse: collapse; width: 100%; margin: 2mm 0 0; font-size: 8pt; }
table.t thead { display: table-header-group; }
.t th { text-align: left; border-bottom: 1pt solid #111; padding: 1.2mm 3mm 1.2mm 0;
        font-size: 7.6pt; text-transform: uppercase; letter-spacing: 0.02em; }
.t td { padding: 1.1mm 3mm 1.1mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top;
        overflow-wrap: anywhere; }
.t tr { page-break-inside: avoid; }
.t td:nth-child(2), .t td:nth-child(3), .t th:nth-child(2), .t th:nth-child(3)
  { text-align: right; white-space: nowrap; }
"""


def qty_of(rows: list[dict], pn: str):
    for r in rows:
        if str(r.get("pn", "")).strip() == pn:
            return r.get("qty"), r.get("name") or ""
    return None, ""


def qty_section(rows: list[dict]) -> str:
    """Количество, которое не подтверждается английским листом самой заявки.

    Раздел СЧИТАЕТСЯ из gt/data/ship_english_source.json, а не пишется руками:
    список уходит заказчику, и обрезанный или устаревший список молча
    превращает «подтвердите 66 номеров» в «подтвердите двадцать».
    """
    if not EN.exists():
        return ""
    g = ((json.loads(EN.read_text(encoding="utf-8")).get("totals") or {}).get("qty_gap") or {})
    top = g.get("top") or []
    if not top:
        return ""
    name = {str(r.get("pn") or ""): (r.get("name") or "") for r in rows}
    out = ['<div class="sec"><h2>Количество, которое не подтверждается вашим же английским '
           f'листом — {ru(len(top))} номеров</h2>']
    out.append(
        '<p class="ask"><b>Спросить:</b> подтвердите количество по номерам ниже и скажите, '
        'на сколько машин составлена заявка. Ответ одной цифрой по каждой строке.</p>')
    out.append(
        f'<p><b>Что уже установлено:</b> к заявке есть англоязычный лист, с которого сделан '
        f'построчный перевод русских наименований. По {ru(len(top))} номерам заявка просит '
        f'больше, чем стоит в этом листе. Возможных объяснений два, и выбрать можете только вы: '
        f'лист покрывает меньше машин, чем заявка, либо в заявку попали две записи об одной и той '
        f'же позиции — строка-перевод и строка русского блока. Для кратности 2 и 4 первое '
        f'объяснение работает; там, где разница десятки раз, — нет.</p>')
    out.append(
        f'<p class="cost"><b>Цена ошибки.</b> {ru(g.get("usd_at_stake"))} USD нашей оценки '
        f'стоит на количестве, которое листом не подтверждено. Считано так: из '
        f'{ru(g.get("pns_measured"))} номеров, которые есть и в заявке, и в английском листе, '
        f'по количеству заявки выходит {ru(g.get("usd_by_summary_qty"))} USD, а по количеству '
        f'листа — {ru(g.get("usd_by_source_qty"))} USD; разница и есть эта сумма. Ровно у '
        f'{ru(g.get("qty_equal"))} номеров количество совпадает точно. Это не претензия к вам: '
        f'пока количество не подтверждено, мы не можем ни запрашивать по этим строкам цену, ни '
        f'считать сумму.</p>')
    out.append('<table class="t"><thead><tr><th>артикул</th><th>в заявке</th>'
               '<th>в английском листе</th><th>наименование</th></tr></thead><tbody>')
    for x in top:
        out.append(f'<tr><td class="pn">{E(x["pn"])}</td>'
                   f'<td>{ru(x["qty_summary"])}</td><td>{ru(x["qty_source"])}</td>'
                   f'<td>{E(name.get(str(x["pn"]), "")[:58])}</td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def build_html(doc: dict, rows: list[dict]) -> str:
    qs = doc["questions"]
    tot = sum(int(q.get("qty") or 0) for q in qs)
    by_kind = {k: [q for q in qs if q["kind"] == k] for k in ORDER}

    parts = [
        "<h1>Вопросы к заказчику по заявке ЛУКОЙЛ</h1>",
        '<p class="lead">Эти строки нельзя закупать не потому, что не нашли продавца, '
        'а потому, что неизвестно, <b>что</b> покупать. Просить по ним цену — тратить '
        'время: продавец ответит встречным вопросом или посчитает не то. Пока строки '
        'не расшифрованы, они не идут ни в запрос цен, ни в сумму закупки.</p>',
        f"""<table class="k"><tbody>
<tr><td class="l">Строк требуют уточнения</td><td class="big">{len(qs)}</td>
<td class="dim">по каждой ниже сказано, что именно спросить</td></tr>
<tr><td class="l">Штук за ними</td><td class="big">{ru(tot)}</td>
<td class="dim">объём, который сейчас не посчитать</td></tr>
</tbody></table>""",
        "<h2>Как этим пользоваться</h2><ol>"
        "<li>Вопрос сформулирован так, чтобы на него отвечали одним словом или "
        "номером — не пересказом.</li>"
        "<li>Под каждым вопросом сказано, <b>что мы уже установили сами</b>: "
        "заказчику остаётся подтвердить или поправить, а не искать с нуля.</li>"
        "<li>Цена ошибки показана числом там, где её можно оценить. Это довод "
        "в разговоре: строка не «придирка снабженца», а деньги или неподходящая "
        "деталь на площадке.</li></ol>",
    ]

    first = True
    for kind in ORDER:
        group = by_kind.get(kind) or []
        if not group:
            continue
        cls = "sec" if not first else ""
        parts.append(f'<div class="{cls}"><h2>{E(KIND_RU[kind])} — {len(group)}</h2>')
        first = False
        for q in sorted(group, key=lambda x: -(int(x.get("qty") or 0))):
            qty, name = qty_of(rows, q["pn"])
            head = (f'<h3><span class="pn">{E(q["pn"])}</span> — '
                    f'{ru(q.get("qty") or qty or 0)} {E(q.get("unit") or "шт")}</h3>')
            body = [head]
            if name:
                body.append(f'<p class="dim">{E(name)}</p>')
            body.append(f'<p class="ask"><b>Спросить:</b> {E(q["ask"])}</p>')
            if q.get("known"):
                body.append(f'<p><b>Что уже установлено:</b> {E(q["known"])}</p>')
            if q.get("cost"):
                body.append(f'<p class="cost"><b>Цена ошибки.</b> {E(q["cost"])}</p>')
            parts.append(f'<div class="q">{"".join(body)}</div>')
        parts.append("</div>")

    parts.append(qty_section(rows))

    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<title>Вопросы заказчику ЛУКОЙЛ</title>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def main() -> int:
    if not SRC.exists():
        print(f"нет {SRC}", file=sys.stderr)
        return 1
    doc = json.loads(SRC.read_text())
    rows = []
    if DEMAND.exists():
        d = json.loads(DEMAND.read_text())
        rows = d["rows"] if isinstance(d, dict) else d

    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(build_html(doc, rows), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=60000",
         f"--print-to-pdf={PDF_OUT}", HTML_OUT.as_uri()],
        check=True, capture_output=True)
    qs = doc["questions"]
    print(f"вопросов: {len(qs)}, штук за ними: "
          f"{sum(int(q.get('qty') or 0) for q in qs):,}".replace(",", " "))
    print(f"{PDF_OUT.name}: {PDF_OUT.stat().st_size / 1e6:.2f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
