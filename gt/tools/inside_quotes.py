#!/usr/bin/env python3
"""Цены, которые уже лежат у нас: номера заявки во ВХОДЯЩИХ предложениях.

ЗАЧЕМ. Разведка ищет цену в открытом доступе — это долго и часто безрезультатно.
Но опись вложений сделок помнит, какие артикулы стоят в каждом присланном
предложении поставщика и сколько там строк с ценой. Значит по части строк заявки
цена у нас УЖЕ ЕСТЬ: не в интернете, а в собственном почтовом ящике.

Замечено 18.09.2026: разведка по двенадцати строкам вернула одну цену из
открытых источников, и тут же сообщила, что по четырём другим номерам цена
стоит во входящем предложении, которое опись видела и разобрала.

ЧТО ЭТОТ ЗАМЕР ДАЁТ. По каждой непокрытой строке заявки — адрес: номер сделки,
имя файла, сколько в нём строк и сколько с ценой. Этого хватает, чтобы открыть
файл и взять цифру, не обращаясь никуда.

ЧЕГО НЕ ДАЁТ. Самой цены: опись цен не хранит намеренно — репозиторий публичный,
а это коммерческие данные контрагентов. Здесь только указание, где смотреть.

СЧЁТ ВЕДЁТСЯ ПО НОРМАЛИЗОВАННОМУ НОМЕРУ и только по тем, у кого есть и буква, и
достаточная длина: короткий числовой ряд вроде «10-12» совпадает с чем угодно, и
такое совпадение дороже, чем его отсутствие.

    python gt/tools/inside_quotes.py [--write]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "gt/data/bitrix_tkp_index.json"
ASK = ROOT / "gt/data/ship_lukoil.json"
REVERIFY = ROOT / "gt/data/ship_reverify.json"
OUT = ROOT / "gt/data/ship_inside_quotes.json"

# Направления, где может стоять цена поставщика. «Наш запрос» исключён: там
# наши ориентиры, а не предложение. «Заявка» включена по замеру 17.09.2026 —
# ответ заказчику возвращают в том же шаблоне, и цены оказываются там.
PRICED_DIRECTIONS = ("входящее", "заявка", "неизвестно")
MIN_LEN = 6


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def usable(k: str) -> bool:
    """Номер годится для сверки, если он длинный и не чисто числовой.

    Чисто числовой короткий ряд совпадает со случайной цифрой из таблицы —
    датой, количеством, суммой. Ложное совпадение здесь обвиняет строку в том,
    что цена у нас есть, и отправляет исполнителя искать её впустую.
    """
    return len(k) >= MIN_LEN and not k.isdigit() or (k.isdigit() and len(k) >= 7)


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def measure() -> dict:
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r.get("pn")): r
          for r in json.loads(REVERIFY.read_text(encoding="utf-8"))["rows"]}

    files = [i for s in idx["scopes"].values() for i in (s.get("inventory") or [])
             if i.get("pns") and (i.get("priced") or 0) > 0
             and i.get("direction") in PRICED_DIRECTIONS]

    where: dict[str, list] = {}
    for f in files:
        for pn in f["pns"]:
            k = key(pn)
            if usable(k):
                where.setdefault(k, []).append(f)

    hits, hits_nopric = [], []
    for r in ask:
        k = key(r.get("pn"))
        got = where.get(k)
        if not got:
            continue
        x = rv.get(k) or {}
        has_price = isinstance(x.get("price_low"), (int, float))
        item = {
            "pn": r.get("pn"),
            "name": str(r.get("name") or "")[:120],
            "qty": r.get("qty"),
            "usd_exposure": round(expo(r), 2),
            "we_already_have_price": has_price,
            "found_in": [{"deal": f.get("origin"), "file": f.get("file_name"),
                          "rows": f.get("rows"), "rows_with_price": f.get("priced"),
                          "direction": f.get("direction")}
                         for f in sorted(got, key=lambda f: -(f.get("priced") or 0))[:4]],
        }
        hits.append(item)
        if not has_price:
            hits_nopric.append(item)

    hits.sort(key=lambda x: -x["usd_exposure"])
    hits_nopric.sort(key=lambda x: -x["usd_exposure"])
    return {
        "updated": idx.get("updated"),
        "source": ("Опись вложений сделок Bitrix (gt/data/bitrix_tkp_index.json) против "
                   "номеров заявки (gt/data/ship_lukoil.json). Считает "
                   "gt/tools/inside_quotes.py."),
        "what_it_gives": ("Адрес, где цена уже лежит: номер сделки, имя файла, сколько в нём "
                          "строк и сколько с ценой. Самих цен здесь нет и не будет — "
                          "репозиторий публичный."),
        "quote_files_scanned": len(files),
        "rows_of_request_found": len(hits),
        "usd_found": round(sum(h["usd_exposure"] for h in hits), 2),
        "rows_without_our_price": len(hits_nopric),
        "usd_without_our_price": round(sum(h["usd_exposure"] for h in hits_nopric), 2),
        "min_key_length": MIN_LEN,
        "caveat": ("Сверка по нормализованному номеру длиной от шести знаков; чисто "
                   "числовые ряды короче семи знаков отброшены — они совпадают со "
                   "случайной цифрой таблицы, а ложное совпадение здесь дороже пропуска: "
                   "оно отправляет исполнителя искать цену, которой нет."),
        "rows": hits_nopric,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    m = measure()
    print(f"просмотрено присланных предложений с ценами: {m['quote_files_scanned']}")
    print(f"номеров заявки нашлось в них: {m['rows_of_request_found']} на "
          f"{m['usd_found']:,.0f} USD".replace(",", " "))
    print(f"из них БЕЗ нашей цены: {m['rows_without_our_price']} на "
          f"{m['usd_without_our_price']:,.0f} USD — по ним цена уже есть у нас, "
          f"искать не нужно".replace(",", " "))
    for h in m["rows"][:a.top]:
        f = h["found_in"][0]
        print(f"  {h['usd_exposure']:>10,.0f} | {str(h['pn'])[:26]:26} | {f['deal']} | "
              f"{str(f['file'])[:46]} (строк {f['rows']}, с ценой {f['rows_with_price']})"
              .replace(",", " "))
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
