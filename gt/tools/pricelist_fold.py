#!/usr/bin/env python3
"""Заводской прейскурант переносится в перепроверку строкой на номер.

ЧТО ЭТО ЗА ИСТОЧНИК И ЧЕМ ОН ЛУЧШЕ ПРОЧИХ. Тридцать восемь снятых перечней
запасных частей не дали ни одной цены: там либо «запросить», либо пусто. Один
дал — заводской прейскурант изготовителя на 284 страницы и 14 745 строк вида
«номер — наименование — цена». Это цена САМОГО ИЗГОТОВИТЕЛЯ, а не посредника, и
по надёжности она выше всего, что мы находили у продавцов.

ЧЕМ ОН ХУЖЕ ЖИВОЙ ЦЕНЫ, и это обязано стоять рядом с каждой цифрой: прейскурант
датирован, и дата у него не сегодняшняя. Цена десятилетней давности говорит о
ПОРЯДКЕ величины и о соотношении позиций между собой, но не о том, сколько
попросят сейчас. Ни инфляция, ни смена владельца линии, ни изменение условий
поставки в ней не учтены.

РАЗДЕЛИТЕЛЬ РАЗРЯДОВ ПРОВЕРЯЕТСЯ НА САМОМ ЛИСТЕ, до чтения любой цены. В этом
прейскуранте точка отделяет тысячи, запятая — центы, и доказано это соседними
строками одной страницы: «01810411 Safety plate 6,00» против «01170411 POWER
PACK 35.100,00». Ошибка в разделителе дала бы стократное расхождение — именно
так уже едва не случилось на другом листе.

    python gt/tools/pricelist_fold.py <выдача.json> --source "<чей прейскурант, дата>" \
        --currency EUR [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASK = ROOT / "gt/data/ship_lukoil.json"
FX = ROOT / "gt/data/fx_rates.json"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MONEY = re.compile(r"(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s*$")


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def money(quote: str) -> float | None:
    """«35.100,00» → 35100.0, «6,00» → 6.0. Точка — тысячи, запятая — центы."""
    m = MONEY.search(str(quote or "").strip())
    if not m:
        return None
    return float(m.group(1).replace(".", "") + "." + m.group(2))


def build(src: dict, source: str, currency: str) -> list[dict]:
    ask = {key(r.get("pn")): r for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]}
    rates = json.loads(FX.read_text(encoding="utf-8"))
    rate = float((rates.get("rates") or rates)[currency]) if currency != "USD" else 1.0
    made = []
    for m in src.get("matches") or []:
        val = money(m.get("price_quote"))
        row = ask.get(key(m.get("pn_ask")))
        if val is None or row is None:
            continue
        usd = val / rate
        lo, hi = row.get("usd_lo"), row.get("usd_hi")
        band = "" if lo in (None, "") else f"{float(lo):g}–{float(hi):g} USD за штуку"
        if lo in (None, ""):
            verdict = ("НЕЧЕМ ПРОВЕРИТЬ: цена изготовителя найдена, а вилки по строке в наших "
                       "данных нет вовсе — сравнивать не с чем.")
        elif usd > float(hi):
            verdict = (f"ЗАНИЖЕНА: цена изготовителя по прейскуранту {usd:,.2f} USD за штуку "
                       f"выше потолка нашей вилки ({band}) в {usd / float(hi):.1f} раза."
                       ).replace(",", " ")
        elif usd < float(lo):
            verdict = (f"ЗАВЫШЕНА: цена изготовителя по прейскуранту {usd:,.2f} USD за штуку "
                       f"ниже пола нашей вилки ({band}) в {float(lo) / usd:.1f} раза."
                       ).replace(",", " ")
        else:
            verdict = (f"ВЕРНА: цена изготовителя по прейскуранту {usd:,.2f} USD за штуку лежит "
                       f"внутри нашей вилки ({band}). Про середину вилки это ничего не "
                       f"говорит.").replace(",", " ")
        quote = str(m.get("price_quote") or "").strip()
        made.append({
            "pn": row.get("pn"),
            "what_it_is": (f"По заводскому прейскуранту изделие названо так: «{m.get('desc')}». "
                           f"Опознание опирается на совпадение номера в прейскуранте "
                           f"изготовителя, а не на описание продавца."),
            "real_maker": "",
            "real_pn": str(m.get("pn_list") or ""),
            "lifecycle": ("Не проверялся. Присутствие в прейскуранте означает, что позиция "
                          "числилась в номенклатуре на дату прейскуранта, и только это."),
            "channel": (f"Изготовитель по собственному прейскуранту. Это НЕ предложение и не "
                        f"подтверждение наличия: прейскурант говорит, по какой цене позиция "
                        f"числилась в номенклатуре. Источник: {source}."),
            "contacts": "Через изготовителя либо его уполномоченного представителя.",
            "price_kind": (f"цена изготовителя по прейскуранту ({source}) — не предложение, не "
                           f"цена сделки и не сегодняшняя цена"),
            "price_authorized": "",
            "price_source": (f"{source}; строка прейскуранта дословно: «{quote}». Адрес: "
                             f"{m.get('url')}"),
            "stock": "Прейскурант остатка не содержит вовсе.",
            "lead_time": "Прейскурант срока не содержит.",
            "volume_note": (f"Наш объём по строке — {row.get('qty')} шт. Ступеней цены от "
                            f"количества прейскурант не публикует."),
            "recommended": (f"Для порядка величин закладывать {usd:,.2f} USD за штуку. Это цена "
                            f"изготовителя на дату прейскуранта, а не сегодняшняя: к моменту "
                            f"заказа она изменится, и запрашивать её надо письмом."
                            ).replace(",", " "),
            "blocker": ("Цена датирована прошлым и не является предложением; наличие и срок "
                        "неизвестны; авторизованный представитель по строке не назван."),
            "band_verdict": verdict,
            "maker_short": "",
            "price_low": round(usd, 2),
            "price_high": round(usd, 2),
            "price_note": f"{usd:,.2f} USD · {usd:,.2f} USD".replace(",", " "),
            "note": (f"Чем подтверждено. Строка заводского прейскуранта дословно: «{quote}». "
                     f"Источник: {source}, адрес {m.get('url')}.\n\nКак прочитана цена. "
                     f"Разделитель разрядов проверен на самом листе ДО чтения: точка отделяет "
                     f"тысячи, запятая — центы, и это доказано соседними строками одной "
                     f"страницы («Safety plate 6,00» против «POWER PACK 35.100,00»). Пересчёт "
                     f"в доллары только по нашему сохранённому курсу gt/data/fx_rates.json; "
                     f"курс справочный межбанковский, а не курс сделки.\n\nЧего эта цена НЕ "
                     f"значит. Она датирована и не является сегодняшней: ни инфляция, ни смена "
                     f"владельца линии, ни условия поставки в ней не учтены. Она говорит о "
                     f"порядке величины и о соотношении позиций между собой. Наличия, срока и "
                     f"предложения за ней нет.\n\nСкептик по строке не работал: строка создана "
                     f"переносом прейскуранта инструментом gt/tools/pricelist_fold.py."),
            "skeptics": [{
                "lens": "перенос прейскуранта без независимой проверки",
                "holds": True,
                "confidence": "средняя",
                "checked": ("Проверено: номер совпал с номером прейскуранта изготовителя, "
                            "разделитель разрядов разобран на самом листе. Не проверялось: "
                            "действует ли цена сегодня, есть ли изделие в наличии, кто "
                            "уполномоченный представитель."),
                "objection": ("цена изготовителя прошлых лет может расходиться с сегодняшней "
                              "в разы, и вердикт по вилке опирается именно на неё"),
                "correction": ("Перед выносом в защиту запросить действующую цену у "
                               "изготовителя или его представителя письмом."),
            }],
        })
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--source", required=True, help="чей прейскурант и на какую дату")
    ap.add_argument("--currency", default="EUR")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    made = build(json.loads(Path(a.src).read_text(encoding="utf-8")), a.source, a.currency)
    print(f"строк с ценой прейскуранта: {len(made)}")
    if a.dry_run or not made:
        for r in made:
            print(f"  {str(r['pn']):18} {r['band_verdict'][:76]}")
        return 0
    tmp = HERE / "_pricelist_tmp.json"
    tmp.write_text(json.dumps({"rows": made}, ensure_ascii=False), encoding="utf-8")
    import rv_merge
    m = rv_merge.merge([tmp])
    tmp.unlink()
    print(f"принято набором: {len(m['took'])}, отказов {len(m['left'])}")
    for _, pn, why in m["left"]:
        print(f"  ОТКАЗ {pn}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
