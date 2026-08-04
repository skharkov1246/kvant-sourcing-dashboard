"""CI-зонд v8: сверка выгрузки коллег «Контроль дедлайнов» с живым Битриксом и с нашим движком.

Вход: scripts/_coll_rows.json — срез их выгрузки (75 строк: заказ, deal_id, суммы, статусы, дедлайн).
Проверяем по каждой стороне:
  A. сделки из их файла — есть ли в воронке реализации (кат.0), совпадает ли сумма;
  B. заказы СП-172 — суммы, стадии, живые/закрытые; какие их строки уже неактуальны;
  C. наш пробел: сколько живых заказов СП-172 имеют просроченный дедлайн, но в их файле их нет;
  D. их пробел: какие заказы сделок из их файла отсутствуют в выгрузке.
Секреты не печатаются.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import requests

ROWS = json.load(open(os.path.join(os.path.dirname(__file__), "_coll_rows.json"), encoding="utf-8"))
TODAY = dt.date(2026, 8, 4)


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


def regno(t) -> int:
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(t or ""))
    return int(m.group(1)) if m else 0


def main() -> int:
    cur = bx("crm.currency.list", {}).get("result") or []
    rate = {c.get("CURRENCY"): float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1) for c in cur}
    usd = rate.get("USD") or 1.0            # 1 USD в базовой валюте-эквиваленте
    def to_usd(v, c):                        # сумма в валюте c → USD
        return float(v or 0) * rate.get(c, 1.0) / usd

    dealids = sorted({r["deal"] for r in ROWS if r["deal"]})
    print(f"=== вход: {len(ROWS)} строк коллег, {len(dealids)} сделок ===")

    # ---------- A. сделки ----------
    DSEL = ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID"]
    deals = {}
    for i in range(0, len(dealids), 50):
        chunk = dealids[i:i + 50]
        for d in bx_all("crm.deal.list", {"filter": {"ID": chunk}, "select": DSEL}):
            deals[str(d["ID"])] = d
    print(f"\n=== A. сделки: найдено в Битриксе {len(deals)} из {len(dealids)} ===")
    missing = [d for d in dealids if d not in deals]
    if missing:
        print(f"  НЕ НАЙДЕНЫ (удалены/нет прав): {missing[:10]}")
    cats = {}
    for d in deals.values():
        cats.setdefault(str(d.get("CATEGORY_ID")), []).append(d)
    print("  распределение по воронкам:", {k: len(v) for k, v in sorted(cats.items())})
    notc0 = [d for d in deals.values() if str(d.get("CATEGORY_ID")) != "0"]
    for d in notc0[:8]:
        print(f"    вне кат.0: #{d['ID']} cat={d.get('CATEGORY_ID')} {str(d.get('TITLE'))[:50]}")
    sem = {}
    for d in deals.values():
        sem[d.get("STAGE_SEMANTIC_ID")] = sem.get(d.get("STAGE_SEMANTIC_ID"), 0) + 1
    print("  семантика стадий сделок:", sem)
    closed = [d for d in deals.values() if (d.get("STAGE_SEMANTIC_ID") or "") in ("S", "F")]
    print(f"  ЗАКРЫТЫХ сделок в их «активной» выгрузке: {len(closed)}")
    for d in closed[:10]:
        print(f"    #{d['ID']} sem={d.get('STAGE_SEMANTIC_ID')} {str(d.get('TITLE'))[:55]}")

    # суммы сделок
    print("\n  --- сумма сделки: их deal_sum vs Bitrix OPPORTUNITY (→USD) ---")
    seen, diff = set(), 0
    for r in ROWS:
        d = deals.get(r["deal"])
        if not d or r["deal"] in seen:
            continue
        seen.add(r["deal"])
        bxs = to_usd(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        their = float(r.get("dsum") or 0)
        if their and bxs and abs(their - bxs) / max(their, bxs) > 0.02:
            diff += 1
            if diff <= 10:
                print(f"    #{d['ID']} их={their:>12,.0f}  Bitrix={bxs:>12,.0f} ({d.get('OPPORTUNITY')} {d.get('CURRENCY_ID')})"
                      f"  откл {100*(their-bxs)/max(bxs,1):+.0f}%  {str(d.get('TITLE'))[:38]}")
        elif not bxs and their:
            diff += 1
            if diff <= 10:
                print(f"    #{d['ID']} их={their:,.0f}  Bitrix=0 (сумма пустая)  {str(d.get('TITLE'))[:40]}")
    print(f"    сделок с расхождением суммы >2%: {diff} из {len(seen)}")

    # ---------- B. заказы ----------
    OSEL = ["id", "title", "parentId2", "opportunity", "currencyId", "stageId", "categoryId",
            "createdTime", "movedTime"]
    orders = []
    for i in range(0, len(dealids), 50):
        orders += bx_all("crm.item.list", {"entityTypeId": 172,
                                           "filter": {"parentId2": dealids[i:i + 50]}, "select": OSEL})
    print(f"\n=== B. заказы СП-172 по этим сделкам: {len(orders)} в Битриксе, {len(ROWS)} строк у коллег ===")
    st = {}
    for o in orders:
        st[o.get("stageId")] = st.get(o.get("stageId"), 0) + 1
    print("  стадии:", dict(sorted(st.items(), key=lambda kv: -kv[1])[:12]))
    dead = [o for o in orders if str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    print(f"  закрытых (SUCCESS/FAIL) среди них: {len(dead)}")

    # сопоставление их строк с заказами по номеру «NNN/M»
    def key(t):
        m = re.match(r"\s*(\d{1,4})/(\d+)", str(t or ""))
        return f"{m.group(1)}/{m.group(2)}" if m else ""
    bykey = {}
    for o in orders:
        k = key(o.get("title"))
        if k:
            bykey.setdefault(k, []).append(o)
    nomatch, sumdiff, closed_rows = [], 0, []
    for r in ROWS:
        k = key(r["order"])
        cand = bykey.get(k) or []
        if not cand:
            nomatch.append(r["order"][:50])
            continue
        o = cand[0]
        sid = str(o.get("stageId") or "")
        if sid.endswith((":SUCCESS", ":FAIL")):
            closed_rows.append((r["order"][:45], sid, r["so"]))
        bo = to_usd(o.get("opportunity"), o.get("currencyId"))
        their = float(r.get("osum") or 0)
        if their and bo and abs(their - bo) / max(their, bo) > 0.02:
            sumdiff += 1
            if sumdiff <= 10:
                print(f"    сумма заказа {k}: их={their:>10,.0f} Bitrix={bo:>10,.0f}"
                      f" ({o.get('opportunity')} {o.get('currencyId')}) откл {100*(their-bo)/max(bo,1):+.0f}%")
        elif their and not bo:
            sumdiff += 1
            if sumdiff <= 10:
                print(f"    сумма заказа {k}: их={their:,.0f} Bitrix=0 (пустая opportunity)")
    print(f"  строк коллег без заказа в Битриксе: {len(nomatch)} → {nomatch[:8]}")
    print(f"  строк с расхождением суммы заказа >2%: {sumdiff}")
    print(f"  строк коллег про УЖЕ ЗАКРЫТЫЕ заказы: {len(closed_rows)}")
    for x in closed_rows[:10]:
        print(f"    {x[0]:47s} стадия={x[1]} (в файле: «{x[2]}»)")

    # D. их пробел: живые заказы этих сделок, которых нет в их файле
    theirkeys = {key(r["order"]) for r in ROWS}
    absent = [o for o in orders if key(o.get("title")) and key(o.get("title")) not in theirkeys
              and not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    print(f"\n=== D. живые заказы этих же сделок, ОТСУТСТВУЮЩИЕ в выгрузке коллег: {len(absent)} ===")
    for o in absent[:12]:
        print(f"    {str(o.get('title'))[:52]:54s} стадия={o.get('stageId')}")

    # ---------- C. поля дат в СП-172 + масштаб пропуска ----------
    print("\n=== C. поля дат/дедлайнов в СП-172 ===")
    f = bx("crm.item.fields", {"entityTypeId": 172}).get("result", {}).get("fields", {})
    datef = {k: (v.get("title") or "") for k, v in f.items()
             if v.get("type") in ("date", "datetime") and k.lower() not in
             ("createdtime", "updatedtime", "movedtime", "begindate", "closedate")}
    for k, t in list(datef.items())[:25]:
        print(f"    {k}: {t}")

    # сколько ВСЕГО живых заказов и сколько из них с просроченной датой
    allo = bx_all("crm.item.list", {"entityTypeId": 172, "select": OSEL + list(datef.keys())})
    live = [o for o in allo if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    print(f"\n  всего заказов СП-172: {len(allo)}, живых: {len(live)}")
    for fld, title in datef.items():
        vals = [o for o in live if o.get(fld)]
        od = [o for o in vals if str(o.get(fld))[:10] < TODAY.isoformat()]
        if vals:
            print(f"    {fld} «{title[:34]}»: заполнено у {len(vals)} живых, ПРОСРОЧЕНО {len(od)}")
    print(f"\n  у коллег в разделе «просрочено»: {sum(1 for r in ROWS if r['sec']=='overdue')}")

    print("\n✓ зонд v8 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
