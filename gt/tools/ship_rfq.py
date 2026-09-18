#!/usr/bin/env python3
"""Пакет запросов твёрдых предложений по заявке ЛУКОЙЛ.

Выкладка отвечает на вопрос «у кого есть». Отгрузить по ней нельзя: цена с карточки
живёт до следующего обновления витрины, остаток меняется за сутки, а продавец ничем
не связан. Твёрдое предложение даёт то, чего у карточки нет: остаток числом на
сегодня, срок под НАШЕ количество, цену за весь объём, срок действия, Инкотермс.

Инструмент группирует строки со складом по адресату и собирает по письму на каждого:
  gt/data/ship_rfq_letters.json — машинно-читаемый пакет для рассылки
  gt/docs/ЗАПРОСЫ-ЛУКОЙЛ.csv   — то же для слияния почты
  gt/docs/ЗАПРОСЫ-ЛУКОЙЛ.pdf   — читать глазами и править перед отправкой

Заказчик в письмах не назван: имя конечного покупателя — не то, что сообщают
продавцу до сделки, и не то, что кладут в публичный репозиторий.
"""
from __future__ import annotations

import csv
import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "gt/data/ship_lukoil.json"
LETTERS = ROOT / "gt/data/ship_rfq_letters.json"
CSV_OUT = ROOT / "gt/docs/ЗАПРОСЫ-ЛУКОЙЛ.csv"
HTML_OUT = ROOT / "gt/docs/ЗАПРОСЫ-ЛУКОЙЛ.html"
PDF_OUT = ROOT / "gt/docs/ЗАПРОСЫ-ЛУКОЙЛ.pdf"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

# строки, по которым есть что спрашивать: продавец назвал наличие в той или иной форме
ASK_GRADES = ("твёрдый", "частичный", "условный")

# площадка — не адресат: письмо «в eBay» не уходит никому
MARKETPLACE = re.compile(r"^(ebay|amazon|alibaba|aliexpress|avito|taobao|made-in-china)\b", re.I)

# где письмо уместнее по-русски
RU_COUNTRY = re.compile(r"росси|беларус|казахстан|russia|belarus|kazakh|\bRU\b|\bBY\b|\bKZ\b", re.I)


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def rows_of(doc) -> list[dict]:
    return doc if isinstance(doc, list) else doc.get("rows") or doc.get("items") or []


def addressees(r: dict) -> list[dict]:
    """Продавцы этой детали, которым есть куда писать.

    Только `sellers` — те, у кого найдена именно эта позиция. Адресаты кластера
    сюда не идут: «работает по такому классу» и «у него есть эта деталь» — разные
    утверждения, и запрос твёрдой цены по второму отправляют, а по первому нет.
    """
    out = []
    for sl in r.get("sellers") or []:
        if not sl.get("emails"):
            continue
        if not sl.get("is_company", True):
            continue
        if MARKETPLACE.match(str(sl.get("seller") or "")):
            continue
        out.append(sl)
    return out


def price_seen(r: dict) -> str:
    if not r.get("price"):
        return ""
    cur = r.get("currency") or "USD"
    per = f" за {r['pack_qty']} шт" if (r.get("pack_qty") or 1) > 1 else ""
    return f"{r['price']} {cur}{per}"


QUESTIONS_EN = [
    "confirmed quantity on hand today, as a number (not «in stock»)",
    "lead time for the FULL quantity we ask, and for the part of it you can ship now",
    "unit price and total price for the full quantity, net, in your currency",
    "how long the quote stays valid",
    "Incoterms and shipping point",
    "condition: new, factory-sealed, surplus or refurbished; warranty term",
    "country of origin and HS code",
    "return policy if the part does not match the nameplate",
]
QUESTIONS_RU = [
    "подтверждённый остаток на сегодня — числом, а не формулировкой «в наличии»",
    "срок под ВЕСЬ наш объём и отдельно под ту часть, что отгружаете сейчас",
    "цена за штуку и за весь объём, нетто, в вашей валюте",
    "срок действия предложения",
    "Инкотермс и пункт отгрузки",
    "состояние: новое в заводской упаковке, складской остаток или восстановленное; гарантия",
    "страна происхождения и код ТН ВЭД",
    "условия возврата, если деталь не совпала с шильдиком",
]


# Наименование в письме НЕ обрезается. Обрезка на 90 знаках уносила как раз
# конец строки, где стоят размер, исполнение и давление, — то есть ровно то, по
# чему продавец подбирает деталь. Правила репозитория обрезку в выгрузках
# запрещают, и письмо здесь не исключение.
def body_en(name: str, lines: list[tuple[dict, dict]]) -> str:
    tbl = []
    for i, (r, sl) in enumerate(lines, 1):
        bits = [f"{i}. P/N {r['pn']} — {r.get('qty') or '?'} pcs"]
        if r.get("man"):
            bits.append(f"brand {r['man']}")
        if r.get("name"):
            bits.append(str(r["name"]))
        s = "; ".join(bits)
        if sl.get("url"):
            s += f"\n   your listing: {sl['url']}"
        seen = price_seen(r)
        if seen:
            s += f"\n   price we see on the listing: {seen}"
        if str(r.get("stock_qty") or "").strip():
            s += f"\n   stock shown: {r['stock_qty']}"
        tbl.append(s)
    q = "\n".join(f"  {i}) {t}" for i, t in enumerate(QUESTIONS_EN, 1))
    return (
        f"Dear {name} team,\n\n"
        "We are a procurement company sourcing spare parts for gas turbine and gas engine "
        "power generation equipment. We found the positions below on your listings and ask "
        "you for a firm quotation.\n\n"
        f"{chr(10).join(tbl)}\n\n"
        "For each position, please confirm:\n"
        f"{q}\n\n"
        "If a position is not available in the full quantity, please say so plainly and "
        "quote what you can ship — partial coverage is useful to us, an unconfirmed "
        "«in stock» is not. If you can offer a direct equivalent from the original "
        "component manufacturer, quote it as an alternative and name the part number.\n\n"
        "Best regards,\n")


def body_ru(name: str, lines: list[tuple[dict, dict]]) -> str:
    tbl = []
    for i, (r, sl) in enumerate(lines, 1):
        bits = [f"{i}. {r['pn']} — {r.get('qty') or '?'} шт"]
        if r.get("man"):
            bits.append(f"бренд {r['man']}")
        if r.get("name"):
            bits.append(str(r["name"]))
        s = "; ".join(bits)
        if sl.get("url"):
            s += f"\n   ваша карточка: {sl['url']}"
        seen = price_seen(r)
        if seen:
            s += f"\n   цена на карточке: {seen}"
        if str(r.get("stock_qty") or "").strip():
            s += f"\n   показанный остаток: {r['stock_qty']}"
        tbl.append(s)
    q = "\n".join(f"  {i}) {t}" for i, t in enumerate(QUESTIONS_RU, 1))
    return (
        f"Здравствуйте, {name}!\n\n"
        "Мы закупаем запасные части для газотурбинного и газопоршневого генерирующего "
        "оборудования. Позиции ниже нашли у вас и просим твёрдое предложение.\n\n"
        f"{chr(10).join(tbl)}\n\n"
        "По каждой позиции просим подтвердить:\n"
        f"{q}\n\n"
        "Если позиции нет в полном объёме — напишите прямо и дайте цену на то, что "
        "отгружаете: частичное покрытие нам полезно, а неподтверждённое «в наличии» — нет. "
        "Если можете предложить прямой аналог изготовителя узла — дайте его отдельной "
        "строкой с указанием номера.\n\n"
        "С уважением,\n")


def build(rows: list[dict]) -> list[dict]:
    """Группировка строк по адресату. Ключ — первая почта: одна компания под двумя
    торговыми именами («The Modern Shop» и «Modern Group Shop») пишет с одного ящика
    и должна получить одно письмо, а не два."""
    groups: dict[str, dict] = {}
    for r in rows:
        if r.get("stock_grade") not in ASK_GRADES:
            continue
        for sl in addressees(r):
            box = str(sl["emails"][0]).strip().lower()
            g = groups.setdefault(box, {"to": box, "cc": [], "names": set(),
                                        "country": "", "phones": [], "lines": []})
            g["names"].add(str(sl.get("seller") or "").strip())
            for em in sl["emails"][1:]:
                em = str(em).strip().lower()
                if em not in g["cc"]:
                    g["cc"].append(em)
            for ph in sl.get("phones") or []:
                if ph not in g["phones"]:
                    g["phones"].append(str(ph))
            g["country"] = g["country"] or str(sl.get("country") or "")
            if r["pn"] not in {x[0]["pn"] for x in g["lines"]}:
                g["lines"].append((r, sl))

    out = []
    for g in groups.values():
        name = sorted(g["names"], key=len)[0] or g["to"].split("@")[0]
        lines = sorted(g["lines"], key=lambda x: -(float(x[0].get("qty") or 0)))
        ru = bool(RU_COUNTRY.search(g["country"]))
        firm = sum(1 for r, _ in lines if r.get("stock_grade") == "твёрдый")
        subj = (f"Запрос твёрдого предложения: {len(lines)} позиций"
                if ru else
                f"Request for firm quotation — {len(lines)} line item"
                f"{'s' if len(lines) > 1 else ''}")
        out.append({
            "to": g["to"], "cc": g["cc"], "seller": name, "country": g["country"],
            "phones": g["phones"], "lang": "ru" if ru else "en", "subject": subj,
            "body": body_ru(name, lines) if ru else body_en(name, lines),
            "lines": len(lines), "firm_lines": firm,
            "qty_total": sum(int(r.get("qty") or 0) for r, _ in lines),
            "pns": [{"pn": r["pn"], "qty": r.get("qty"), "grade": r.get("stock_grade"),
                     "sheet": r.get("sheet"), "url": sl.get("url") or "",
                     "price": r.get("price"), "currency": r.get("currency")}
                    for r, sl in lines],
        })
    out.sort(key=lambda x: (-x["firm_lines"], -x["lines"], x["seller"]))
    return out


CSS = """
@page { size: A4 portrait; margin: 12mm 11mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.2pt; color: #111; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 2mm; }
h2 { font-size: 11pt; margin: 0 0 2mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
p { margin: 0 0 2.5mm; line-height: 1.4; }
.lead { font-size: 8.6pt; }
.dim { color: #666; }
.letter { page-break-inside: avoid; border-top: 0.4pt solid #bbb; padding-top: 2.5mm;
          margin-bottom: 4mm; }
.letter h3 { font-size: 9.4pt; margin: 0 0 1.2mm; }
pre { font-family: "DejaVu Sans Mono", monospace; font-size: 7.1pt; white-space: pre-wrap;
      word-wrap: break-word; overflow-wrap: anywhere; margin: 0; line-height: 1.32;
      background: #f7f7f7; padding: 2mm 2.5mm; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.3mm 3mm 1.3mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; }
.big { font-size: 12pt; font-weight: bold; }
ol { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.35; }
.mono { font-family: "DejaVu Sans Mono", monospace; }
.sec { page-break-before: always; }
"""


def build_html(pack: list[dict], rows: list[dict]) -> str:
    ask = [r for r in rows if r.get("stock_grade") in ASK_GRADES]
    covered = {p["pn"] for L in pack for p in L["pns"]}
    orphan = [r for r in ask if r["pn"] not in covered]
    firm_boxes = sum(1 for L in pack if L["firm_lines"])
    parts = [
        "<h1>Запросы твёрдых предложений по заявке ЛУКОЙЛ</h1>",
        '<p class="lead">Выкладка отвечает, у кого есть. Отгрузить по ней нельзя: цена '
        'с карточки живёт до следующего обновления витрины, остаток меняется за сутки, '
        'продавец ничем не связан. Ниже — готовые письма: по одному на адресата, со '
        'списком его позиций и восемью вопросами, ответы на которые превращают карточку '
        'в предложение.</p>',
        f"""<table class="k"><tbody>
<tr><td class="l">Писем к отправке</td><td class="big">{len(pack)}</td>
<td class="dim">из них {firm_boxes} содержат хотя бы одну твёрдую строку</td></tr>
<tr><td class="l">Позиций в запросах</td><td class="big">{len(covered)}</td>
<td class="dim">строки со складом в любой из трёх градаций; всего таких {len(ask)}</td></tr>
<tr><td class="l">Позиций без адресата</td><td class="big">{len(orphan)}</td>
<td class="dim">склад назван, но почты продавца нет — им нужен телефон или форма сайта</td></tr>
</tbody></table>""",
        "<h2>Как этим пользоваться</h2><ol>"
        "<li>Письма не подписаны: подставьте своё имя, компанию и реквизиты перед отправкой.</li>"
        "<li>Имя заказчика в письмах не названо намеренно. До сделки продавцу оно не нужно, "
        "а репозиторий публичный.</li>"
        "<li>Порядок — по числу твёрдых строк у адресата. Верхние двадцать писем закрывают "
        "большую часть объёма; начинать с них.</li>"
        "<li>Ответ считается предложением, только если в нём есть остаток числом и срок под "
        "наш объём. «Есть в наличии, пишите» — это не ответ, а повторение витрины.</li>"
        "<li>Для рассылки берите <span class=\"mono\">gt/docs/ЗАПРОСЫ-ЛУКОЙЛ.csv</span> "
        "или <span class=\"mono\">gt/data/ship_rfq_letters.json</span> — там те же письма "
        "полями «кому», «тема», «текст».</li></ol>",
    ]
    if orphan:
        parts.append("<h2>Позиции со складом, но без адреса продавца</h2>"
                     '<p class="lead">Склад назван, писать некуда: почты у продавца не '
                     "нашлось. Здесь нужен телефон, форма обратной связи или другой продавец.</p>"
                     "<p>" + " · ".join(
                         f'<span class="mono">{E(r["pn"])}</span> ({r.get("qty") or "?"} шт)'
                         for r in sorted(orphan, key=lambda r: -(float(r.get("qty") or 0)))
                     ) + "</p>")

    # заголовок раздела живёт внутри первого письма: отдельным блоком он занимал
    # целую страницу, потому что письмо под ним не разрывается и уезжало на следующую
    parts.append('<div class="sec">')
    for i, L in enumerate(pack, 1):
        head = ("<h2>Письма</h2>" if i == 1 else "")
        head += (f'<h3>{i}. {E(L["seller"])}'
                 + (f' · {E(L["country"])}' if L["country"] else "") + "</h3>")
        meta = [f'<span class="mono">{E(L["to"])}</span>']
        if L["cc"]:
            meta.append("копия: " + ", ".join(f'<span class="mono">{E(c)}</span>'
                                              for c in L["cc"][:3]))
        if L["phones"]:
            meta.append(E(L["phones"][0]))
        meta.append(f'позиций — {L["lines"]}, из них твёрдых — {L["firm_lines"]}, '
                    f'всего {L["qty_total"]} шт')
        parts.append(f'<div class="letter">{head}'
                     f'<p class="dim">{" · ".join(meta)}</p>'
                     f'<p><b>Тема:</b> {E(L["subject"])}</p>'
                     f"<pre>{E(L['body'])}</pre></div>")
    parts.append("</div>")
    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<title>Запросы ЛУКОЙЛ</title>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def main() -> int:
    if not DATA.exists():
        print(f"нет {DATA} — сначала gt/tools/ship_merge.py", file=sys.stderr)
        return 1
    rows = rows_of(json.loads(DATA.read_text()))
    pack = build(rows)

    LETTERS.write_text(json.dumps(
        {"source": "gt/data/ship_lukoil.json", "letters": pack},
        ensure_ascii=False, indent=1), encoding="utf-8")

    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["to", "cc", "seller", "country", "phone", "lang", "subject", "body",
                    "lines", "firm_lines", "qty_total", "pns"])
        for L in pack:
            w.writerow([L["to"], "; ".join(L["cc"]), L["seller"], L["country"],
                        "; ".join(L["phones"][:2]), L["lang"], L["subject"], L["body"],
                        L["lines"], L["firm_lines"], L["qty_total"],
                        "; ".join(p["pn"] for p in L["pns"])])

    HTML_OUT.write_text(build_html(pack, rows), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={PDF_OUT}", HTML_OUT.as_uri()],
        check=True, capture_output=True)

    firm = sum(1 for L in pack if L["firm_lines"])
    print(f"писем: {len(pack)} (с твёрдыми строками — {firm})")
    print(f"позиций в запросах: {sum(L['lines'] for L in pack)}")
    print(f"{PDF_OUT.name}: {PDF_OUT.stat().st_size / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
