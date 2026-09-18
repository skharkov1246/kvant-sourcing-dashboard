#!/usr/bin/env python3
"""Письма держателям перечней: восемь адресов на пятьсот восемьдесят шесть номеров.

ОТКУДА ЭТО БЕРЁТСЯ. Снятые перечни запасных частей опознали 1 051 строку заявки,
но канал закупки назван только у 465. Разница — 586 строк на 233 224 доллара:
изделие известно, а спросить цену не у кого. При этом адрес есть у каждой из
них, и он один и тот же на сотни строк — держатель перечня, в котором наш номер
напечатан. Восемь писем закрывают то, на что построчная разведка тратит недели.

ЧЕМ ЭТОТ АДРЕС ЯВЛЯЕТСЯ И ЧЕМ НЕ ЯВЛЯЕТСЯ. Это адрес ПО ДЕТАЛИ, а не родовой:
наш номер напечатан у них в перечне, дословно. Но что за ним стоит —
оригинальная деталь, копия по чертежу или только готовность взяться — перечень
не говорит, и один из держателей прямо пишет о себе, что не является ни
изготовителем, ни дистрибьютором и работает по чертежу либо образцу. Поэтому
письмо спрашивает об этом первым пунктом, а не последним, и ни одна такая
строка не считается закрытой каналом, пока не пришёл ответ.

    python gt/tools/lists_rfq.py [--min-rows 5]
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
ASK = ROOT / "gt/data/ship_lukoil.json"
REVERIFY = ROOT / "gt/data/ship_reverify.json"
LISTS = ROOT / "gt/data/ship_parts_lists.json"
INSIDE = ROOT / "gt/data/ship_inside_quotes.json"
OUT = ROOT / "gt/docs"
PACK = ROOT / "gt/data/ship_lists_rfq.json"
CHROME = ["/opt/pw-browsers/chromium/chrome-linux/chrome",
          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]

CSS = """
@page { size: A4 portrait; margin: 14mm 12mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.6pt; color: #111; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
h2 { font-size: 10.5pt; margin: 6mm 0 2mm; border-bottom: 1.2pt solid #111;
     padding-bottom: 0.8mm; page-break-after: avoid; }
p { margin: 0 0 2.5mm; line-height: 1.5; }
.dim { color: #555; }
pre { white-space: pre-wrap; font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8pt;
      margin: 0; line-height: 1.45; }
.letter { border: 0.8pt solid #111; padding: 3mm; margin-bottom: 4mm; }
/* Письмо начинается с новой страницы: иначе хвост предыдущего оставался на
   странице один, и проверка PDF считала её полупустой. Заодно письмо удобнее
   печатать и отправлять по одному. */
h2 { page-break-before: always; }
.tail { page-break-inside: avoid; margin-top: 3mm; }
h2:first-of-type { page-break-before: auto; }
"""
# Хранилища документов держателями номенклатуры не являются: перечень там лежит,
# а продаёт его не архив. Письмо такому адресату уходит впустую, поэтому они
# исключаются явно и с названной причиной, а не молча.
NOT_SELLERS = {
    "archive.org": "Интернет-архив: хранилище копий страниц, а не продавец",
    "scribd.com": "площадка публикации документов, а не продавец",
    "web.archive.org": "Интернет-архив: хранилище копий страниц, а не продавец",
}
QUESTIONS = [
    "что именно вы поставляете по этому номеру: оригинальную деталь изготовителя, "
    "изготовленную по чертежу или образцу, либо восстановленную",
    "цена за штуку и за весь объём, нетто, в вашей валюте",
    "подтверждённый остаток на сегодня — числом, а не формулировкой «в наличии»",
    "срок под весь объём и отдельно под ту часть, что отгружаете сейчас",
    "срок действия предложения",
    "условия поставки и пункт отгрузки",
    "страна происхождения и код товарной номенклатуры",
    "условия возврата, если деталь не совпала с заводской табличкой",
]


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    try:
        return f"{float(n):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return E(n)


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def already_quoted() -> set:
    """Номера, по которым предложение от КАКОГО-ТО поставщика уже получено.

    Письмо по такому номеру не бесполезно — второе предложение есть конкуренция,
    — но оно и не разведка: цена по строке у нас уже будет. Замер 18.09.2026:
    749 позиций из 1 007 в этих письмах именно такие, то есть три четверти
    пакета уходит за ВТОРЫМ предложением, а не за первым. Без этого счёта
    исполнитель разошлёт все семнадцать писем с одинаковым ожиданием и удивится
    ответам.
    """
    if not INSIDE.exists():
        return set()
    return {key(r.get("pn"))
            for r in json.loads(INSIDE.read_text(encoding="utf-8")).get("rows", [])}


def build(min_rows: int) -> dict:
    quoted = already_quoted()
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    by_pn = {key(r.get("pn")): r for r in ask}
    done = {key(r.get("pn")) for r in json.loads(REVERIFY.read_text(encoding="utf-8"))["rows"]}
    listed: dict[str, dict] = {}
    for m in json.loads(LISTS.read_text(encoding="utf-8"))["machines"].values():
        for it in m["items"]:
            listed.setdefault(key(it.get("pn")), it)

    by_host: dict[str, list] = collections.defaultdict(list)
    for k, it in listed.items():
        if k in done or k not in by_pn:
            continue
        row = by_pn[k]
        for h in it.get("sources") or []:
            by_host[h].append((row, it))
    letters, skipped = [], []
    for host, items in sorted(by_host.items(), key=lambda x: -len(x[1])):
        if host in NOT_SELLERS:
            skipped.append({"host": host, "rows": len(items), "why": NOT_SELLERS[host]})
            continue
        if len(items) < min_rows:
            skipped.append({"host": host, "rows": len(items),
                            "why": f"позиций меньше порога в {min_rows}"})
            continue
        items.sort(key=lambda x: str(x[0].get("pn")))
        lines = []
        for i, (row, it) in enumerate(items, 1):
            desc = "; ".join(it.get("descriptions") or [])
            lines.append(f"{i}. {row.get('pn')} — {row.get('qty')} {row.get('unit') or 'шт'}"
                         + (f"\n   у вас в перечне: {desc}" if desc else ""))
        head = (
            f"Здравствуйте!\n\n"
            f"Мы закупаем запасные части для газотурбинного, газопоршневого и бурового "
            f"оборудования. В вашем открытом перечне запасных частей напечатаны номера, "
            f"которые нам нужны, — всего {len(items)}. Просим предложение по ним.\n\n"
            + "\n".join(lines))
        # Хвост письма держится одним куском: вопросы и подпись, оторванные от
        # текста, оставались на странице одни, и проверка PDF считала её
        # полупустой. Это и по существу верно — вопросы читают целиком.
        tail = ("По каждой позиции просим указать:\n"
                + "\n".join(f"  {i}) {q}" for i, q in enumerate(QUESTIONS, 1)) +
                "\n\nПервый пункт для нас главный: мы не предполагаем, что наличие номера в "
                "перечне означает наличие детали, и хотим понимать, о чём идёт речь, до "
                "обсуждения цены. Если часть позиций вы не поставляете — напишите прямо, это "
                "полезнее общего ответа.\n\nС уважением,\n______________________\n")
        pns = [str(r.get("pn")) for r, _ in items]
        n_quoted = sum(1 for x in pns if key(x) in quoted)
        letters.append({"host": host, "rows": len(items), "subject":
                        f"Запрос предложения: {len(items)} позиций из вашего перечня",
                        "pns": pns,
                        "already_quoted": n_quoted,
                        "is_discovery": n_quoted * 2 < len(pns),
                        "body": head + "\n\n" + tail, "body_head": head, "body_tail": tail})
    return {"letters": letters, "skipped": skipped,
            "rows_covered": sum(x["rows"] for x in letters),
            "rows_already_quoted": sum(x["already_quoted"] for x in letters),
            "what_already_quoted_means": (
                "Позиции, по которым предложение от какого-то поставщика уже получено и "
                "лежит во вложении сделки. Письмо по ним не бесполезно — второе "
                "предложение есть конкуренция, — но это запрос за ВТОРЫМ предложением, а "
                "не разведка. Письма, где таких позиций меньше половины, помечены как "
                "разведочные: с них отдача выше, и рассылать надо с них.")}


def render(pack: dict) -> str:
    out = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
           "<title>Запросы держателям перечней</title><style>" + CSS + "</style></head><body>"]
    a = out.append
    a("<h1>Запросы держателям открытых перечней запасных частей</h1>")
    a(f"<p class='dim'>Писем {len(pack['letters'])}, позиций в них "
      f"{pack['rows_covered']}. Это строки заявки, по которым изделие опознано перечнем, а "
      f"канал закупки не назван: адрес есть, и он один и тот же на сотни строк.</p>")
    a("<p class='dim'>Адрес здесь — ПО ДЕТАЛИ, а не родовой: наш номер напечатан у них в "
      "перечне дословно. Но что за ним стоит — оригинал, изготовление по чертежу или только "
      "готовность взяться — перечень не говорит, и один из держателей прямо пишет о себе, "
      "что не является ни изготовителем, ни дистрибьютором. Поэтому письмо спрашивает об "
      "этом первым пунктом. Ни одна строка не считается закрытой каналом, пока не пришёл "
      "ответ.</p>")
    a("<p class='dim'>Имени заказчика в письмах нет: конечного покупателя продавцу до сделки "
      "не называют.</p>")
    if pack.get("rows_already_quoted") is not None:
        disc = [x for x in pack["letters"] if x.get("is_discovery")]
        a(f"<p><b>С каких писем начинать.</b> По "
          f"{ru(pack['rows_already_quoted'])} позициям из {ru(pack['rows_covered'])} "
          f"предложение от какого-то поставщика У НАС УЖЕ ЕСТЬ — оно лежит во вложении "
          f"сделки (см. «ГДЕ-ЦЕНА-УЖЕ-ЕСТЬ-ЛУКОЙЛ.pdf»). Такое письмо не бесполезно: второе "
          f"предложение есть конкуренция. Но это запрос за ВТОРЫМ предложением, а не "
          f"разведка, и ждать от него надо другого.</p>")
        a(f"<p>Разведочными — там, где отвеченных позиций меньше половины, — оказались "
          f"{ru(len(disc))} писем из {ru(len(pack['letters']))}: "
          + ", ".join(f"<b>{E(x['host'])}</b> ({ru(x['rows'])} позиций, отвечено "
                      f"{ru(x['already_quoted'])})" for x in
                      sorted(disc, key=lambda x: -x["rows"]))
          + ". С них отдача выше, и рассылать надо с них.</p>")
        a(f"<p class='dim'>{E(pack['what_already_quoted_means'])}</p>")
    big = max((x["rows"] for x in pack["letters"]), default=0)
    a(f"<p><b>Решение владельца до отправки.</b> Эти письма отличаются от обычного запроса "
      f"поставщику не по форме, а по объёму: в самом крупном перечислено {ru(big)} наших "
      f"номеров подряд. Для продавца со складом это обычный запрос. Но часть держателей — "
      f"мастерские, которые прямо пишут о себе, что изготавливают по чертежу или образцу; для "
      f"такого адресата перечень из сотен номеров одной машины — это, по сути, её программа "
      f"запасных частей. Отправлять ли им полный список, дробить ли его или ограничиться "
      f"продавцами со складом — решает владелец, а не исполнитель. Пока решения нет, письма "
      f"лежат здесь готовыми и наружу не уходят.</p>")
    if pack.get("skipped"):
        a("<p class='dim'>Кому НЕ пишем и почему: "
          + "; ".join(f"{E(s['host'])} ({E(s['rows'])} позиций) — {E(s['why'])}"
                      for s in pack["skipped"]) + ".</p>")
    for x in pack["letters"]:
        a(f"<h2>{E(x['host'])} — {x['rows']} позиций</h2>")
        a(f"<div class='letter'><pre>Тема: {E(x['subject'])}\n\n{E(x['body_head'])}</pre>"
          f"<div class='tail'><pre>{E(x['body_tail'])}</pre></div></div>")
    a("</body></html>")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rows", type=int, default=5)
    a = ap.parse_args()
    pack = build(a.min_rows)
    if not pack["letters"]:
        print("держателей перечней с нашими номерами не нашлось", file=sys.stderr)
        return 1
    PACK.write_text(json.dumps({
        "updated": "2026-09-18",
        "source": ("Письма держателям открытых перечней запасных частей по строкам заявки "
                   "ЛУКОЙЛ, где изделие опознано перечнем, а канал закупки не назван. Собирает "
                   "gt/tools/lists_rfq.py."),
        "what_this_address_is": ("Адрес ПО ДЕТАЛИ: наш номер напечатан у держателя в перечне "
                                 "дословно. Это НЕ означает, что деталь у него есть и что она "
                                 "оригинальная: один из держателей прямо пишет о себе, что не "
                                 "является ни изготовителем, ни дистрибьютором и работает по "
                                 "чертежу либо образцу. Поэтому письмо спрашивает об этом "
                                 "первым пунктом, и строка не считается закрытой каналом до "
                                 "ответа."),
        **pack,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    hp = OUT / "ЗАПРОСЫ-ПО-ПЕРЕЧНЯМ-ЛУКОЙЛ.html"
    pp = OUT / "ЗАПРОСЫ-ПО-ПЕРЕЧНЯМ-ЛУКОЙЛ.pdf"
    hp.write_text(render(pack), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — документ не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=180000",
         f"--print-to-pdf={pp}", hp.as_uri()], check=True, capture_output=True)
    print(f"писем {len(pack['letters'])}, позиций {pack['rows_covered']}; "
          f"{pp.name}: {pp.stat().st_size / 1e6:.1f} МБ")
    for x in pack["letters"]:
        print(f"  {x['rows']:>4} позиций | {x['host']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
