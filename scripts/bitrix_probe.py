"""CI-зонд v9: чей курс валют верен, какое поле коллеги считают дедлайном, и масштаб пропусков.

1. Курсы валют Битрикса (значение + дата обновления) — источник наших € и их $.
2. Идентификация поля-дедлайна: сверяем даты из файла коллег со всеми date-полями СП-172.
3. Масштаб: сколько живых заказов реально просрочено по плановым полям, сколько из них
   в файле коллег, и сколько из них НАШ движок не пометил бы (свежая стадия < 7 дн).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import requests

ROWS = json.load(open(os.path.join(os.path.dirname(__file__), "_coll_rows.json"), encoding="utf-8"))
TODAY = dt.date(2026, 8, 4)
DEADLINE_FLDS = {
    "ufCrm20_1728900218435": "Date of deadline to customer",
    "ufCrm20_1723236261": "Customer delivery date, planned",
    "ufCrm20_1709294315471": "Inbound Delivery Date, planned",
    "ufCrm20_1723235828": "Supplier Shipment Date, planned",
    "ufCrm20_1724941935": "Production end date, budget",
    "ufCrm20_1724942005": "Customer delivery date, budget",
    "ufCrm20_1723385006": "Customer Shipment Date, planned",
    "ufCrm20_1723400865529": "Дата окончания производства",
}


def bx(method: str, params: dict | None = None) -> dict:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    for _ in range(3):
        try:
            r = requests.post(f"{base}/{method}.json", json=params or {}, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return {}


def bx_all(method: str, params: dict) -> list:
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j:
            return out
        start = j["next"]


def okey(t) -> str:
    m = re.match(r"\s*(\d{1,4})/(\d+)", str(t or ""))
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def main() -> int:
    print("=== 1. КУРСЫ ВАЛЮТ в Битриксе (наш и их источник денег) ===")
    cur = bx("crm.currency.list", {}).get("result") or []
    base_cur = None
    for c in cur:
        amt, cnt = float(c.get("AMOUNT") or 1), float(c.get("AMOUNT_CNT") or 1)
        if c.get("BASE") == "Y":
            base_cur = c.get("CURRENCY")
        print(f"  {c.get('CURRENCY')}: AMOUNT={c.get('AMOUNT')} CNT={c.get('AMOUNT_CNT')} "
              f"→ 1 {c.get('CURRENCY')} = {amt/cnt:.4f} базовой  base={c.get('BASE')} "
              f"обновлено={str(c.get('DATE_UPDATE'))[:10]}")
    rate = {c.get("CURRENCY"): float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1) for c in cur}
    print(f"  базовая валюта: {base_cur}")
    if rate.get("USD") and rate.get("RUB"):
        print(f"  → по Битриксу: 1 USD = {rate['USD']/rate['RUB']:.2f} RUB, "
              f"1 EUR = {rate.get('EUR',0)/rate['RUB']:.2f} RUB, "
              f"1 EUR = {rate.get('EUR',0)/rate['USD']:.4f} USD")
    print("  → у коллег (из расхождений): 1 USD ≈ 73.4 RUB, 1 EUR ≈ 1.146 USD")

    # ---------- 2. какое поле = «дедлайн» в их файле ----------
    dealids = sorted({r["deal"] for r in ROWS if r["deal"]})
    OSEL = ["id", "title", "parentId2", "opportunity", "currencyId", "stageId", "movedTime",
            "createdTime"] + list(DEADLINE_FLDS)
    orders = []
    for i in range(0, len(dealids), 50):
        orders += bx_all("crm.item.list", {"entityTypeId": 172,
                                           "filter": {"parentId2": dealids[i:i + 50]}, "select": OSEL})
    bykey = {}
    for o in orders:
        k = okey(o.get("title"))
        if k:
            bykey.setdefault(k, o)

    print("\n=== 2. КАКОЕ ПОЛЕ коллеги показывают как «дедлайн» ===")
    hits = {f: 0 for f in DEADLINE_FLDS}
    checked = 0
    for r in ROWS:
        o = bykey.get(okey(r["order"]))
        if not o or not r.get("dl"):
            continue
        checked += 1
        their = "-".join(r["dl"].split(".")[::-1])          # ДД.ММ.ГГГГ → ГГГГ-ММ-ДД
        for f in DEADLINE_FLDS:
            if str(o.get(f) or "")[:10] == their:
                hits[f] += 1
    for f, n in sorted(hits.items(), key=lambda kv: -kv[1]):
        print(f"  {DEADLINE_FLDS[f][:38]:40s} совпало у {n} из {checked} строк")

    # то же для колонки prod_end
    hits2 = {f: 0 for f in DEADLINE_FLDS}
    for r in ROWS:
        o = bykey.get(okey(r["order"]))
        pe = r.get("prod_end") if isinstance(r.get("prod_end"), str) else None
        if not o or not pe:
            continue
        their = "-".join(pe.split(".")[::-1])
        for f in DEADLINE_FLDS:
            if str(o.get(f) or "")[:10] == their:
                hits2[f] += 1
    if any(hits2.values()):
        print("  (колонка «конец пр-ва»):", {DEADLINE_FLDS[f][:26]: n for f, n in hits2.items() if n})

    # ---------- 3. масштаб просрочки по всему СП-172 ----------
    print("\n=== 3. МАСШТАБ: просрочка по всем живым заказам СП-172 ===")
    allo = bx_all("crm.item.list", {"entityTypeId": 172, "select": OSEL})
    live = [o for o in allo if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    theirkeys = {okey(r["order"]) for r in ROWS}
    theirov = {okey(r["order"]) for r in ROWS if r.get("sec") == "overdue"}
    print(f"  живых заказов всего: {len(live)}; строк у коллег: {len(theirkeys)} (просрочено {len(theirov)})")

    MAIN = "ufCrm20_1728900218435"
    od = []
    for o in live:
        v = str(o.get(MAIN) or "")[:10]
        if v and v < TODAY.isoformat():
            days = (TODAY - dt.date.fromisoformat(v)).days
            od.append((days, o))
    od.sort(key=lambda x: -x[0])
    inn = sum(1 for _, o in od if okey(o.get("title")) in theirkeys)
    print(f"\n  просрочено по «{DEADLINE_FLDS[MAIN]}»: {len(od)} живых заказов")
    print(f"    из них есть в файле коллег: {inn} ({100*inn//max(len(od),1)}%) → НЕ ПОКРЫТО: {len(od)-inn}")
    b = {"<30 дн": 0, "30-90": 0, "90-180": 0, ">180": 0}
    for d, _ in od:
        b["<30 дн" if d < 30 else "30-90" if d < 90 else "90-180" if d < 180 else ">180"] += 1
    print(f"    глубина просрочки: {b}")

    # сколько из просроченных наш движок НЕ пометил бы (стадия сменилась недавно)
    fresh = []
    for d, o in od:
        mv = str(o.get("movedTime") or "")[:10]
        if mv:
            age = (TODAY - dt.date.fromisoformat(mv)).days
            if age < 7:                       # ниже нашего минимального порога SLOW_MIN_DAYS
                fresh.append((d, age, o))
    print(f"    из них НАШ движок точно не пометит (возраст стадии <7 дн): {len(fresh)}")
    print("\n  топ-10 самых просроченных (глубина, дн стадии, заказ):")
    for d, o in od[:10]:
        mv = str(o.get("movedTime") or "")[:10]
        age = (TODAY - dt.date.fromisoformat(mv)).days if mv else None
        mark = "✓в файле" if okey(o.get("title")) in theirkeys else "✗НЕТ в файле"
        print(f"    -{d:>4} дн | стадия {str(age):>4} дн | {str(o.get('title'))[:44]:46s} {mark}")

    # разрез по «сколько денег» в непокрытой просрочке
    rate_usd = rate.get("USD") or 1
    lost = sum(float(o.get("opportunity") or 0) * rate.get(o.get("currencyId"), 1) / rate_usd
               for _, o in od if okey(o.get("title")) not in theirkeys)
    print(f"\n  Σ закупки по НЕПОКРЫТОЙ просрочке: ${lost:,.0f}")

    print("\n✓ зонд v9 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
