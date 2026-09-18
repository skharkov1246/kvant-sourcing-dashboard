#!/usr/bin/env python3
"""Сток-лист продавца против нашей заявки: сколько строк он закрывает и куда бьёт по вилкам.

ЗАЧЕМ. Открытый сток-лист одного продавца может нести десятки НАШИХ номеров
сразу — это самый дешёвый источник ориентира по цене из всех, что нам
попадались. Замер 18.09.2026 по одной такой странице: из 335 её строк 78
совпали с номерами заявки, и у 43 из них вилки у нас нет вовсе, то есть
оценку можно поставить прямо из этого листа, не тратя разведку.

ЧТО ЗДЕСЬ НЕ ХРАНИТСЯ. Цены. Репозиторий публичный, а сток-лист — коммерческий
документ контрагента: выкладывать его целиком нельзя, даже если страница
открыта. Поэтому набор несёт только СЧЁТ и КЛАСС по каждой строке — ask выше
потолка нашей вилки, внутри, ниже пола или вилки нет. Отдельную цену можно
привести в перепроверке как довод по конкретной строке, с названной страницей;
это довод, а не перепечатка листа.

ЧЕГО ЭТА ЦИФРА НЕ ЗНАЧИТ. Сток-лист — это ask ОДНОГО оператора, а не рынок. У
московского перепродавца внутри ask сидит неизмеренная премия за поставку, и
«ВЫШЕ потолка» по такому листу означает «наша вилка ниже, чем просит этот
продавец», а не «закупка дороже». Второй свидетель обязателен.

    python gt/tools/stocklist_cross.py <файл страницы> --seller <домен> [--write]

Страница берётся ИЗ-ЗА пределов репозитория (скачанная в рабочую папку), чтобы
коммерческий документ в него не попал.
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
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
FX = ROOT / "gt/data/fx_rates.json"
OUT = ROOT / "gt/data/ship_stocklist_cross.json"
GROUPS = ROOT / "gt/data/ship_seller_groups.json"
# Строка листа: «<номер> <описание> <N>pcs <цена>EURO ea». Точка в цене —
# разделитель тысяч: на том же листе рядом стоят «1.200.00EURO» и «20.00EURO».
LINE = re.compile(r"^(\S+)\s+(.*?)\s+(\d+)\s*pcs\s+([\d.,]+)\s*(EURO|EUR|USD|\$)", re.I)


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())


def money(raw: str) -> float:
    """«1.200.00» → 1200.00, «625.00» → 625.00."""
    p = raw.replace(",", ".").split(".")
    return float("".join(p[:-1]) + "." + p[-1]) if len(p) > 1 else float(raw)


def parse(page: str) -> list[dict]:
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    out = []
    for line in text.split("\n"):
        m = LINE.match(line.strip())
        if not m:
            continue
        pn, desc, qty, price, cur = m.groups()
        cur = cur.upper().replace("$", "USD")
        cur = "EUR" if cur == "EURO" else cur        # на листе пишут и EUR, и EURO
        out.append({"pn": pn, "desc": desc.strip(), "qty_listed": int(qty),
                    "price": money(price), "currency": cur})
    return out


def to_usd(v: float, cur: str) -> float:
    if cur == "USD":
        return v
    rates = json.loads(FX.read_text(encoding="utf-8"))
    rates = rates.get("rates") or rates
    r = rates.get("EUR" if cur in ("EUR", "EURO") else cur)  # EURO сведён к EUR при разборе
    return v / float(r) if r else v


def expo(r: dict) -> float:
    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    if lo in (None, "") or hi in (None, ""):
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(r.get("qty") or 0)


def stub_price(rows: list[dict]) -> tuple[float | None, int]:
    """Цена, которая повторяется в листе слишком часто, чтобы быть ценой детали.

    Оплачено замером 18.09.2026: в листе крупнейшего продавца ровно «450.00USD»
    стоит 957 раз из 13 256 строк, а у 1 000 позиций из 1 000 верхний ценовой
    уровень имеет sku = null и цену 0.00 — это шаблон магазина, а не свойство
    изделия. Класс «ask внутри вилки», посчитанный по такой цифре, ничего не
    измеряет. Порог: одна и та же цена у 1 % строк листа и не менее двадцати раз.
    """
    if not rows:
        return None, 0
    c = collections.Counter(round(float(r["price"]), 2) for r in rows)
    price, n = c.most_common(1)[0]
    floor = max(20, len(rows) // 100)
    return (price, n) if n >= floor else (None, 0)


def cross(rows: list[dict], seller: str) -> dict:
    stub, stub_n = stub_price(rows)
    lk = json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]
    band = {key(r.get("pn")): r for r in lk}
    by_pn: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        k = key(r["pn"])
        if k in band:
            by_pn[k].append(r)

    cls = collections.Counter()
    usd = collections.Counter()
    items, note_cross = [], 0
    for k, rs in by_pn.items():
        b = band[k]
        lo = min(to_usd(x["price"], x["currency"]) for x in rs)
        hi = max(to_usd(x["price"], x["currency"]) for x in rs)
        if b.get("usd_lo") in (None, "") or b.get("usd_hi") in (None, ""):
            where = "вилки нет"
        elif lo > float(b["usd_hi"]):
            where = "ask выше потолка"
        elif hi < float(b["usd_lo"]):
            where = "ask ниже пола"
        else:
            where = "ask внутри вилки"
        # У части строк описание НАЧИНАЕТСЯ с другого номера — это кросс
        # продавца, и его нельзя читать как наш номер. Считаем такие отдельно.
        other = bool(re.match(r"^[0-9][0-9A-Z./-]{4,}\s", rs[0]["desc"], re.I))
        note_cross += int(other)
        e = expo(b)
        cls[where] += 1
        usd[where] += e
        on_stub = stub is not None and any(round(float(x["price"]), 2) == stub for x in rs)
        items.append({"pn": b.get("pn"), "where": where, "usd_exposure": round(e, 2),
                      "qty_request": b.get("qty"), "qty_listed": max(x["qty_listed"] for x in rs),
                      "desc_starts_with_other_pn": other,
                      "price_is_list_stub": on_stub})
    items.sort(key=lambda x: -x["usd_exposure"])
    return {
        "seller": seller,
        "listed_rows": len(rows),
        "matched_pns": len(by_pn),
        "matched_exposure": round(sum(usd.values()), 2),
        "by_class": {k: {"pns": cls[k], "usd_exposure": round(usd[k], 2)} for k in cls},
        "desc_starts_with_other_pn": note_cross,
        "stub_price_repeats": stub_n,
        "pns_on_stub_price": sum(1 for x in items if x["price_is_list_stub"]),
        "rows": items,
    }


def witness_of(seller: str) -> str:
    """Кто СВИДЕТЕЛЬ по этому домену: группа владения или общий складской пул.

    Пул отличается от группы: юрлица разные, а склад один (видно по совпадающим
    до единицы остаткам и пометкам secondary/external). Для довода «два
    независимых продавца» это то же самое, что одна группа, поэтому свидетель
    сводится и по группам, и по пулам.
    """
    g = group_of(seller)
    if not GROUPS.exists():
        return g
    d = json.loads(GROUPS.read_text(encoding="utf-8"))
    dom = seller.lower().strip()
    for pl in (d.get("pools") or []):
        for x in (pl.get("domains") or []):
            if dom == str(x).lower() or dom.endswith("." + str(x).lower()):
                return pl.get("pool") or g
    return g


def group_of(seller: str) -> str:
    """К какой группе витрин одного оператора принадлежит домен.

    Без этого два листа одной группы в замере расхождений читались бы как два
    независимых свидетеля — та самая ошибка, из-за которой за ночь развалился
    довод «два независимых продавца» по двадцати одной строке.
    """
    if not GROUPS.exists():
        return ""
    d = json.loads(GROUPS.read_text(encoding="utf-8"))
    dom = seller.lower().strip()
    for g in (d.get("groups") or []):
        for x in (g.get("domains") or []):
            if dom == str(x).lower() or dom.endswith("." + str(x).lower()):
                return g.get("group") or ""
    return ""


def no_band_reach(sellers: dict) -> dict:
    """Номера БЕЗ нашей вилки, у которых появился хоть какой-то ориентир.

    У 785 строк заявки из 1 642 вилки нет вовсе, и в экспозицию они не входят —
    то есть их не видно ни в одной сумме. Открытые листы часть из них называют,
    и это первый ориентир по таким строкам.

    ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Вилка по этим номерам НЕ ставится из этих же листов.
    Поставить её из листа, против которого потом считается класс, — значит
    получить «ask внутри вилки» по построению: эталон стал бы производным от
    правила. Лист годится как повод запросить цену у второго ТИПА свидетеля
    (изготовитель, авторизованный канал), и только его ответ может стать вилкой.
    """
    seen: dict[str, set[str]] = collections.defaultdict(set)
    qty: dict[str, float] = {}
    for name, sl in sellers.items():
        who = sl.get("group") or name          # лист группы — один свидетель
        for r in (sl.get("rows") or []):
            if (r.get("where") or "") != "вилки нет":
                continue
            pn = str(r.get("pn") or "")
            if not pn:
                continue
            seen[pn].add(who)
            qty[pn] = float(r.get("qty_request") or 0)
    two = {pn for pn, w in seen.items() if len(w) > 1}
    return {
        "pns_without_band_on_lists": len(seen),
        "pns_without_band_on_two_independent_lists": len(two),
        "qty_without_band_on_lists": round(sum(qty.values()), 0),
        "why_no_band_set_from_here": "Вилка из этих листов НЕ ставится: иначе класс "
                                     "«ask внутри вилки» получился бы по построению. "
                                     "Лист — повод запросить цену у изготовителя или "
                                     "авторизованного канала, и вилкой может стать только "
                                     "его ответ.",
        "on_two_independent": sorted(two)[:60],
    }


def load_out() -> dict:
    """Прежний набор, приведённый к многопродавцовому виду.

    Инструмент писал ОДНОГО продавца в корень файла, и второй прогон затирал
    первого. С шестью листами это потеряло бы пять замеров — та же ошибка, что
    уже была со счётчиками по охватам, поэтому здесь сразу слияние по продавцу.
    Старая однопродавцовая форма читается и переносится в sellers по имени
    продавца из её же поля source.
    """
    if not OUT.exists():
        return {"sellers": {}}
    d = json.loads(OUT.read_text(encoding="utf-8"))
    if "sellers" in d:
        return d
    old = d.get("totals") or {}
    name = old.get("seller")
    if not name:                       # имя продавца сохранялось только в прозе
        m = re.search(r"сток-листа продавца (\S+)", str(d.get("source") or ""))
        name = m.group(1) if m else "неизвестный продавец"
    return {"sellers": {name: {"totals": old, "rows": d.get("rows") or []}}}


def disagreements(sellers: dict) -> dict:
    """Номера, по которым листы РАСХОДЯТСЯ в классе.

    Это и есть главная цифра набора: пока лист один, «ask выше потолка» читается
    как свойство рынка. Когда листов несколько, видно, что у части номеров класс
    зависит от того, чей лист взять, — и тогда вывод по строке держится не на
    измерении, а на выборе продавца.
    """
    where: dict[str, dict[str, str]] = collections.defaultdict(dict)
    expo_of: dict[str, float] = {}
    for name, s in sellers.items():
        name = s.get("group") or name          # лист группы — один свидетель, не два
        for r in (s.get("rows") or []):
            pn = str(r.get("pn") or "")
            if not pn:
                continue
            where[pn][name] = r.get("where") or ""
            expo_of[pn] = float(r.get("usd_exposure") or 0)
    shared = {pn: w for pn, w in where.items() if len(w) > 1}
    split = {pn: w for pn, w in shared.items() if len(set(w.values())) > 1}
    agree_usd = sum(expo_of[pn] for pn in shared if pn not in split)
    return {
        "pns_on_more_than_one_list": len(shared),
        "pns_with_conflicting_class": len(split),
        "usd_in_conflict": round(sum(expo_of[pn] for pn in split), 2),
        "usd_in_agreement": round(agree_usd, 2),
        "conflicting": sorted(
            ({"pn": pn, "usd_exposure": expo_of[pn], "by_seller": w} for pn, w in split.items()),
            key=lambda x: -x["usd_exposure"])[:40],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("page", help="сохранённая страница сток-листа (ВНЕ репозитория)")
    ap.add_argument("--seller", required=True, help="домен продавца")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    p = Path(a.page)
    if not p.exists():
        print(f"нет файла {p}", file=sys.stderr)
        return 1
    if ROOT in p.resolve().parents:
        print("страница лежит внутри репозитория — это коммерческий документ контрагента, "
              "перенесите её в рабочую папку", file=sys.stderr)
        return 1
    rows = parse(p.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        print("в странице не нашлось ни одной строки вида «номер · описание · N pcs · цена»",
              file=sys.stderr)
        return 1
    m = cross(rows, a.seller)
    print(f"{a.seller}: строк листа {m['listed_rows']}, совпало с заявкой {m['matched_pns']} "
          f"номеров на {m['matched_exposure']:,.0f} USD экспозиции".replace(",", " "))
    for k, v in sorted(m["by_class"].items(), key=lambda x: -x[1]["usd_exposure"]):
        print(f"  {k:<18} номеров {v['pns']:>3} | {v['usd_exposure']:>11,.0f} USD"
              .replace(",", " "))
    print(f"  у {m['desc_starts_with_other_pn']} строк описание начинается с ДРУГОГО номера — "
          f"это кросс продавца, читать его как наш номер нельзя")
    if m["stub_price_repeats"]:
        print(f"  ЦЕНА-ЗАГЛУШКА: одна и та же цифра повторяется в листе "
              f"{m['stub_price_repeats']} раз, и на ней стоит {m['pns_on_stub_price']} наших "
              f"номеров — их класс ничего не измеряет")
    if a.write:
        prev = load_out()
        sellers = prev.get("sellers") or {}
        sellers[a.seller] = {"totals": {k: v for k, v in m.items() if k != "rows"},
                             "group": witness_of(a.seller),
                             "rows": m["rows"]}
        dis = disagreements(sellers)
        nob = no_band_reach(sellers)
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": "Пересечение открытых сток-листов продавцов с номерами заявки "
                      "(gt/data/ship_lukoil.json). Считает gt/tools/stocklist_cross.py, "
                      "по одному листу за прогон, с слиянием по продавцу.",
            "method": "Строка листа имеет вид «номер · описание · N pcs · цена». Точка в цене — "
                      "разделитель тысяч (проверяется на самом листе: рядом стоят «1.200.00» и "
                      "«20.00»). Пересчёт в доллары по gt/data/fx_rates.json. Сверка по "
                      "нормализованному номеру.",
            "no_prices": "ЦЕН ЗДЕСЬ НЕТ И БЫТЬ НЕ МОЖЕТ. Репозиторий публичный, а сток-лист — "
                         "коммерческий документ контрагента: выкладывать его целиком нельзя, "
                         "даже если страница открыта. Набор несёт только счёт и класс: ask выше "
                         "потолка нашей вилки, внутри, ниже пола или вилки нет.",
            "cross_note": "У части строк описание НАЧИНАЕТСЯ с другого номера — это КРОСС "
                          "продавца на его собственный или на соседнее исполнение. Читать такую "
                          "строку как цену НАШЕГО номера нельзя, поэтому она помечена полем "
                          "desc_starts_with_other_pn и считается отдельно.",
            "what_it_is_not": "Сток-лист — это ask ОДНОГО оператора (один продавец), а не рынок. У перепродавца "
                              "внутри ask сидит неизмеренная премия за поставку, поэтому «ask "
                              "выше потолка» означает «наша вилка ниже, чем просит этот "
                              "продавец», а не «закупка дороже». Второй свидетель обязателен.",
            "why_many_sellers": "Замеры живут ПО ПРОДАВЦАМ и не складываются: один "
                                "номер стоит в нескольких листах, и сумма по всем листам "
                                "посчитала бы его столько раз, сколько продавцов его "
                                "держат. Складывать можно только внутри одного листа.",
            "sellers": sellers,
            "disagreements": dis,
            "no_band_reach": nob,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}: продавцов {len(sellers)}, "
              f"номеров больше чем на одном листе {dis['pns_on_more_than_one_list']}, "
              f"из них класс расходится у {dis['pns_with_conflicting_class']} "
              f"на {dis['usd_in_conflict']:,.0f} USD".replace(",", " "))
        print(f"  номеров БЕЗ нашей вилки листы называют {nob['pns_without_band_on_lists']}, "
              f"из них на двух независимых листах "
              f"{nob['pns_without_band_on_two_independent_lists']}; вилку из этих листов "
              f"не ставим — стала бы тавтологией")
    return 0


if __name__ == "__main__":
    sys.exit(main())
