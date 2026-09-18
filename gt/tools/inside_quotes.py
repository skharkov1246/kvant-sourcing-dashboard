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
import random
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


# Кириллические буквы, неотличимые на вид от латинских. Свод нужен потому, что
# в заявке 26 номеров написаны со смешанным алфавитом, и по такому написанию
# сверка не находит ничего: «180В4131» с кириллической «В» и «180B4131» с
# латинской — для машины два разных номера.
HOMOGLYPHS = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
}
CYR = re.compile("[А-Яа-яЁё]")


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def key_folded(x) -> str:
    """Тот же ключ, но с приведением кириллических двойников к латинице.

    Приведение делается ТОЛЬКО если ВСЯ кириллица в обозначении — двойники.
    Иначе русское обозначение калечится в правдоподобный набор знаков и даёт
    ложное совпадение: «ПЦ-102 10х38 6А» превращается в «10210X386A» и находит
    в корпусе случайную строку. Проверено 18.09.2026 — именно этот номер и
    всплыл единственным ложным при попытке приводить буквы без оговорки.
    """
    s = str(x or "")
    cyr = [c for c in s if CYR.match(c)]
    if not cyr or not all(c in HOMOGLYPHS for c in cyr):
        return ""
    folded = "".join(HOMOGLYPHS.get(c, c) for c in s)
    out = key(folded)
    return out if out != key(s) else ""


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


def control(real: list[str], corpus: set[str]) -> dict:
    """Отрицательный контроль: сколько совпадений даст ВЫДУМАННЫЙ номер.

    Сверка по номеру выглядит убедительно сама по себе, и это её слабое место:
    если корпус велик, а номера коротки, совпадать будет что угодно. Проверяется
    это единственным способом — подстановкой заведомо несуществующих номеров ТОЙ
    ЖЕ ФОРМЫ. Здесь они получаются перестановкой цифр внутри настоящего номера:
    длина, набор знаков и расположение букв сохраняются, номер становится
    другим.

    Замер 18.09.2026: настоящие номера совпали в 66,0 % случаев, выдуманные — в
    0,0 % на 1 483 попытках. Значит совпадение здесь несёт сведение, а не шум.
    Зерно случайности задано числом, чтобы замер повторялся.
    """
    rnd = random.Random(20260918)
    fake = []
    for k in real:
        ch = list(k)
        digits = [i for i, c in enumerate(ch) if c.isdigit()]
        if len(digits) < 2:
            continue
        for _ in range(6):
            i, j = rnd.sample(digits, 2)
            ch[i], ch[j] = ch[j], ch[i]
        got = "".join(ch)
        if got != k:
            fake.append(got)
    hit_real = sum(1 for k in real if k in corpus)
    hit_fake = sum(1 for k in fake if k in corpus)
    return {
        "real_checked": len(real),
        "real_matched": hit_real,
        "real_matched_pct": round(hit_real / len(real) * 100, 1) if real else 0.0,
        "fake_checked": len(fake),
        "fake_matched": hit_fake,
        "fake_matched_pct": round(hit_fake / len(fake) * 100, 1) if fake else 0.0,
        "how": ("Выдуманный номер получен перестановкой цифр внутри настоящего: длина, "
                "набор знаков и расположение букв те же, номер другой. Зерно случайности "
                "задано числом, замер повторяется."),
        "what_it_means": ("Если выдуманные номера совпадают почти так же часто, как "
                          "настоящие, сверка не несёт сведения — совпадает что угодно. "
                          "Расхождение долей и есть доказательство."),
    }


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
            for k in (key(pn), key_folded(pn)):
                if k and usable(k):
                    where.setdefault(k, []).append(f)

    hits, hits_nopric = [], []
    folded_hits = 0
    for r in ask:
        k = key(r.get("pn"))
        got = where.get(k)
        if not got:
            kf = key_folded(r.get("pn"))
            got = where.get(kf) if kf else None
            if got:
                folded_hits += 1
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

    corpus = set(where)
    real_keys = [key(r.get("pn")) for r in ask if usable(key(r.get("pn")))]
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
        "found_only_after_folding": folded_hits,
        "folding_note": (
            "Совпадения, найденные только после приведения кириллических букв-двойников к "
            "латинице. В заявке номера со смешанным алфавитом, и по такому написанию "
            "сверка не находит ничего. Приведение делается ТОЛЬКО когда вся кириллица в "
            "обозначении — двойники: иначе русское обозначение калечится в правдоподобный "
            "набор знаков и даёт ложное совпадение."),
        "negative_control": control(real_keys, corpus),
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
    c = m["negative_control"]
    print(f"отрицательный контроль: настоящие номера совпали в {c['real_matched_pct']} % "
          f"({c['real_matched']} из {c['real_checked']}), выдуманные той же формы — в "
          f"{c['fake_matched_pct']} % ({c['fake_matched']} из {c['fake_checked']})")
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
