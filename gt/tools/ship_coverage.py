#!/usr/bin/env python3
"""Сколько заявки мы понимаем: покрытие по ступеням, а не по числу строк.

ЗАЧЕМ. «Разобрано 423 строки» — плохая мера: строки разной цены и разной
трудности. И она не отвечает на вопрос, который задаёт владелец: что из заявки
мы вообще понимаем. Понимание набирается ступенями, и каждая следующая
опирается на предыдущую:

  1. ОПОЗНАНО — известно, что это за изделие: есть дословное наименование из
     перечня изготовителя или разбор перепроверки. До этой ступени запрос
     поставщику бессмысленен.
  2. ИЗГОТОВИТЕЛЬ — назван тот, кто делает деталь физически, а не тот, чей
     шильдик на ней стоит. Это открывает конкурентный канал.
  3. ЦЕНА — найдено число за штуку с названной страницей.
  4. КАНАЛ — назван тот, у кого брать.
  5. ОСТАТОК ЧИСЛОМ — продавец назвал остаток числом, а не словом «в наличии».
     Единственная ступень, на которой строка становится отгружаемой.

Ступени считаются и в строках, и в ДЕНЬГАХ: одна строка на 700 тысяч важнее
сорока по тысяче. Строки без нашей оценки в денежный счёт не входят и
считаются отдельно — иначе доля будет считаться от неполного знаменателя и
выглядеть лучше, чем есть.

    python gt/tools/ship_coverage.py [--write]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASK = ROOT / "gt/data/ship_lukoil.json"
REVERIFY = ROOT / "gt/data/ship_reverify.json"
LISTS = ROOT / "gt/data/ship_parts_lists.json"
QUESTIONS = ROOT / "gt/data/ship_questions.json"
INSIDE = ROOT / "gt/data/ship_inside_quotes.json"
OUT = ROOT / "gt/data/ship_coverage.json"


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0


# Признаки того, что канал по строке КВОТИРУЕМЫЙ: продавец есть, а цены он не
# публикует в принципе. Список закрытый и намеренно узкий: он обвиняет строку в
# том, что цены нет не по нашей вине, и такое утверждение должно опираться на
# прямые слова разбора, а не на догадку. Лишняя строка здесь приукрашивает
# работу, поэтому пропуск тут дешевле ложного срабатывания.
QUOTE_ONLY = ("только по запросу", "только «под запрос»", "под запрос", "по запросу",
              "квотируемый", "цену не публикует", "цены не публикует", "request a quote",
              "get latest price", "цена по запросу", "без публичной цены", "цены нет вовсе")


def quote_only(r: dict) -> bool:
    text = " ".join(str(r.get(f) or "") for f in ("channel", "price_kind", "blocker")).lower()
    return any(m in text for m in QUOTE_ONLY)


def asked_keys() -> set:
    """Номера, по которым уже стоит вопрос заказчику.

    Нужны, чтобы разделить разрыв лестницы на две ЧЕСТНО РАЗНЫЕ части. Строка,
    ждущая ответа заказчика, — это не наша недоработка: пока не названа фасовка,
    исполнение или настоящий номер, продавец вернёт вопрос, а не цену. Строка,
    по которой вопроса нет, — наша работа, и её надо делать.
    """
    if not QUESTIONS.exists():
        return set()
    out = set()
    for q in json.loads(QUESTIONS.read_text(encoding="utf-8")).get("questions", []):
        for part in re.split(r"[,/\u00b7]| и ", str(q.get("pn") or "")):
            k = key(part)
            if len(k) >= 4:
                out.add(k)
    return out


def answered_keys() -> set:
    """Номера, по которым присланное предложение у нас УЖЕ есть.

    Нужны, чтобы не выдавать за непреодолимое то, что уже преодолено. «Канал
    квотируемый» значит, что продавец не публикует прайс, — но если мы у него
    спросили и он ответил, цена не отсутствует, она лежит во вложении сделки.
    Замер 18.09.2026: так обстоит дело у 48 из 65 квотируемых строк, и это
    85 % денег этого разряда, включая две самые дорогие строки всей заявки.
    """
    if not INSIDE.exists():
        return set()
    return {key(r.get("pn"))
            for r in json.loads(INSIDE.read_text(encoding="utf-8")).get("rows", [])}


def measure() -> dict:
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r.get("pn")): r for r in json.loads(REVERIFY.read_text(encoding="utf-8"))["rows"]}
    listed: dict[str, dict] = {}
    if LISTS.exists():
        for m in json.loads(LISTS.read_text(encoding="utf-8"))["machines"].values():
            for it in m["items"]:
                listed.setdefault(key(it.get("pn")), it)

    # ПРИЗНАКИ независимы: канал бывает назван там, где цены нет, и наоборот.
    asked = asked_keys()
    answered = answered_keys()
    gap_answered = 0
    gap_answered_usd = 0.0
    gap_wait = gap_open = 0
    gap_wait_usd = gap_open_usd = 0.0
    gap_rows = gap_quote = 0
    gap_usd = gap_quote_usd = 0.0
    steps = ["опознано", "изготовитель назван", "цена найдена", "канал назван",
             "остаток числом"]
    got = {s: [0, 0.0] for s in steps}
    # ЛЕСТНИЦА накопительная: каждая ступень требует всех предыдущих.
    ladder = {k: [0, 0.0] for k in
              ("опознана", "есть кому написать", "есть цена и адрес", "отгружаема")}
    total_rows, total_usd, no_band = 0, 0.0, [0, 0]
    for r in ask:
        k = key(r.get("pn"))
        e = expo(r)
        total_rows += 1
        total_usd += e
        if e <= 0:
            no_band[0] += 1
        x = rv.get(k)
        li = listed.get(k)

        named = bool(li and str(li.get("descriptions") or "")) or bool(
            x and str(x.get("what_it_is") or "").strip())
        maker = bool(x and str(x.get("maker_short") or "").strip())
        price = bool(x and num(x.get("price_low")))
        channel = bool(x and str(x.get("channel") or "").strip())
        # остаток ЧИСЛОМ: в поле остатка есть цифра, а не только слова
        stock = bool(x and re.search(r"\d", str(x.get("stock") or "")))

        for name, ok in zip(steps, (named, maker, price, channel, stock)):
            if ok:
                got[name][0] += 1
                got[name][1] += e
                if e <= 0:
                    no_band[1] += 1 if name == "опознано" else 0
        # Лестница считается НАКОПИТЕЛЬНО: каждая ступень требует предыдущую.
        # Без этого «канал назван» выходит больше, чем «цена найдена», и набор
        # читается как лестница, не будучи ею: канал у нас записан и там, где
        # цены нет вовсе.
        if channel and not price:
            gap_rows += 1
            gap_usd += e
            if x and quote_only(x):
                gap_quote += 1
                gap_quote_usd += e
                if k in answered:
                    gap_answered += 1
                    gap_answered_usd += e
            elif k in asked:
                gap_wait += 1
                gap_wait_usd += e
            else:
                gap_open += 1
                gap_open_usd += e
        if named:
            ladder["опознана"][0] += 1
            ladder["опознана"][1] += e
            if channel:
                ladder["есть кому написать"][0] += 1
                ladder["есть кому написать"][1] += e
                if price:
                    ladder["есть цена и адрес"][0] += 1
                    ladder["есть цена и адрес"][1] += e
                    if stock:
                        ladder["отгружаема"][0] += 1
                        ladder["отгружаема"][1] += e
    return {
        "channel_without_price": {
            "rows": gap_rows,
            "usd": round(gap_usd, 2),
            "of_them_quote_only": gap_quote,
            "usd_quote_only": round(gap_quote_usd, 2),
            "of_them_quote_only_already_answered": gap_answered,
            "usd_quote_only_already_answered": round(gap_answered_usd, 2),
            "of_them_waiting_customer": gap_wait,
            "usd_waiting_customer": round(gap_wait_usd, 2),
            "of_them_open_to_search": gap_open,
            "usd_open_to_search": round(gap_open_usd, 2),
            "what_it_means": ("Строки, где канал назван, а цены нет. Это НЕ значит, что мы не "
                              "дошли: у части таких каналов цены нет в принципе — продавец "
                              "работает только по запросу и прейскуранта не публикует. Счёт "
                              "квотируемых ведётся по прямым словам разбора и намеренно занижен: "
                              "лишняя строка здесь приукрасила бы работу."),
        },
        "ladder": {k: {"rows": v[0], "usd": round(v[1], 2),
                       "share_rows": round(100 * v[0] / total_rows, 1),
                       "share_usd": round(100 * v[1] / total_usd, 1) if total_usd else 0.0}
                   for k, v in ladder.items()},
        "rows_total": total_rows,
        "usd_total": round(total_usd, 2),
        "rows_without_band": no_band[0],
        "rows_without_band_identified": no_band[1],
        "steps": {s: {"rows": got[s][0], "usd": round(got[s][1], 2),
                      "share_rows": round(100 * got[s][0] / total_rows, 1),
                      "share_usd": round(100 * got[s][1] / total_usd, 1) if total_usd else 0.0}
                  for s in steps},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = measure()
    print(f"строк заявки {m['rows_total']}, оценка по ним {m['usd_total']:,.0f} USD; "
          f"без оценки {m['rows_without_band']} строк".replace(",", " "))
    print("ЛЕСТНИЦА (каждая ступень требует всех предыдущих):")
    for s, v in m["ladder"].items():
        print(f"  {s:<20} {v['rows']:>5} строк ({v['share_rows']:>4} %) | "
              f"{v['usd']:>11,.0f} USD ({v['share_usd']:>4} % денег)".replace(",", " "))
    print("ПРИЗНАКИ по отдельности (независимы, не складываются в лестницу):")
    for s, v in m["steps"].items():
        print(f"  {s:<20} {v['rows']:>5} строк ({v['share_rows']:>4} %) | "
              f"{v['usd']:>11,.0f} USD ({v['share_usd']:>4} % денег)".replace(",", " "))
    g = m["channel_without_price"]
    print(f"КАНАЛ ЕСТЬ, ЦЕНЫ НЕТ: {g['rows']} строк на {g['usd']:,.0f} USD".replace(",", " "))
    print(f"  {g['of_them_quote_only']:>4} строк | {g['usd_quote_only']:>11,.0f} USD | канал "
          f"квотируемый: прейскуранта он не публикует в принципе"
          .replace(",", " "))
    print(f"      из них {g['of_them_quote_only_already_answered']} строк на "
          f"{g['usd_quote_only_already_answered']:,.0f} USD продавец УЖЕ ОТВЕТИЛ — его "
          f"предложение лежит во вложении сделки, писать заново не нужно"
          .replace(",", " "))
    print(f"  {g['of_them_waiting_customer']:>4} строк | "
          f"{g['usd_waiting_customer']:>11,.0f} USD | ждёт ответа заказчика: пока не назван "
          f"номер или исполнение, продавец вернёт вопрос".replace(",", " "))
    print(f"  {g['of_them_open_to_search']:>4} строк | {g['usd_open_to_search']:>11,.0f} USD | "
          f"НАША работа: цена публикуется, её надо найти — задание печатает "
          f"gt/tools/rv_pricehunt.py".replace(",", " "))
    print(f"  из строк БЕЗ нашей оценки опознано {m['rows_without_band_identified']} — "
          f"они не видны ни в одном денежном счёте")
    if a.write:
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": ("Покрытие заявки ЛУКОЙЛ по ступеням понимания. Считает "
                       "gt/tools/ship_coverage.py по gt/data/ship_lukoil.json, "
                       "gt/data/ship_reverify.json и gt/data/ship_parts_lists.json."),
            "why_steps": ("«Разобрано N строк» — плохая мера: строки разной цены и разной "
                          "трудности. Здесь две разные вещи, и путать их нельзя. ЛЕСТНИЦА "
                          "накопительна: опознана → есть кому написать → есть цена и адрес → "
                          "отгружаема; каждая ступень требует всех предыдущих, и отгружаемой "
                          "строка становится только на последней. ПРИЗНАКИ независимы: канал "
                          "бывает назван там, где цены нет, поэтому по отдельности они в "
                          "лестницу не складываются и «канал» может оказаться больше «цены»."),
            "caveat": ("Доли в деньгах считаются от строк, у которых наша оценка ЕСТЬ. Строки "
                       "без оценки в денежный счёт не входят вовсе, и их число названо "
                       "отдельно: иначе доля считалась бы от неполного знаменателя и выглядела "
                       "бы лучше, чем есть."),
            **m,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
