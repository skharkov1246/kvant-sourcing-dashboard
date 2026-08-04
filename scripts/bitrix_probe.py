"""CI-зонд v10: определить поля «ОСС», «схема поставки», город и дату курса.

Сверяем гипотезы против файла коллег: у них по каждому заказу известны oss / manager /
schema / delivery_city — ищем, какие поля Битрикса дают ровно эти значения.
"""
from __future__ import annotations

import json
import os
import re

import requests

ROWS = json.load(open(os.path.join(os.path.dirname(__file__), "_coll_rows.json"), encoding="utf-8"))
FULL = json.load(open(os.path.join(os.path.dirname(__file__), "_coll_full.json"), encoding="utf-8"))


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
    print("=== 1. Все пользовательские поля СП-172 (кандидаты: схема, город, ОСС) ===")
    f = bx("crm.item.fields", {"entityTypeId": 172}).get("result", {}).get("fields", {})
    interesting = {}
    for k, v in f.items():
        t = (v.get("title") or "").lower()
        if any(w in t for w in ("схем", "scheme", "город", "city", "адрес", "достав", "delivery",
                                "ответствен", "осс", "сопровожд", "менеджер")):
            interesting[k] = v.get("title")
            print(f"  {k}: «{v.get('title')}» type={v.get('type')} items={len(v.get('items') or [])}")
    print(f"  (всего полей в СП-172: {len(f)})")

    print("\n=== 2. Списочные поля СП-172 со значениями (ищем схему поставки) ===")
    for k, v in f.items():
        its = v.get("items") or []
        if its and 2 <= len(its) <= 15:
            vals = [str(i.get("VALUE"))[:34] for i in its][:9]
            print(f"  {k} «{str(v.get('title'))[:40]}»: {vals}")

    # ---------- 3. проверка гипотез по данным ----------
    dealids = sorted({r["deal"] for r in ROWS if r["deal"]})
    OSEL = ["id", "title", "parentId2", "assignedById", "companyId", "stageId"] + list(interesting)
    orders = []
    for i in range(0, len(dealids), 50):
        orders += bx_all("crm.item.list", {"entityTypeId": 172,
                                           "filter": {"parentId2": dealids[i:i + 50]}, "select": OSEL})
    bykey = {}
    for o in orders:
        k = okey(o.get("title"))
        if k:
            bykey.setdefault(k, o)
    deals = {}
    for i in range(0, len(dealids), 50):
        for d in bx_all("crm.deal.list", {"filter": {"ID": dealids[i:i + 50]},
                                          "select": ["ID", "ASSIGNED_BY_ID", "TITLE"]}):
            deals[str(d["ID"])] = d

    uids = {str(o.get("assignedById")) for o in orders if o.get("assignedById")}
    uids |= {str(d.get("ASSIGNED_BY_ID")) for d in deals.values() if d.get("ASSIGNED_BY_ID")}
    users = {}
    for u in bx_all("user.get", {"FILTER": {"ID": sorted(uids)}}) or []:
        users[str(u.get("ID"))] = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
    if not users:                       # user.get не принимает список — добираем поштучно
        for uid in sorted(uids):
            u = (bx("user.get", {"ID": uid}).get("result") or [{}])[0]
            users[uid] = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()

    print(f"\n=== 3. Сверка с файлом коллег ({len(FULL)} строк) ===")
    hit_oss_order = hit_mgr_order = hit_oss_deal = hit_mgr_deal = tested = 0
    schema_hits = {}
    city_hits = {}
    for r in FULL:
        o = bykey.get(okey(r.get("order")))
        if not o:
            continue
        tested += 1
        resp_o = users.get(str(o.get("assignedById")), "")
        d = deals.get(str(o.get("parentId2")) or "")
        resp_d = users.get(str((d or {}).get("ASSIGNED_BY_ID")), "")
        if r.get("oss") and resp_o and r["oss"].lower() == resp_o.lower():
            hit_oss_order += 1
        if r.get("manager") and resp_o and r["manager"].lower() == resp_o.lower():
            hit_mgr_order += 1
        if r.get("oss") and resp_d and r["oss"].lower() == resp_d.lower():
            hit_oss_deal += 1
        if r.get("manager") and resp_d and r["manager"].lower() == resp_d.lower():
            hit_mgr_deal += 1
        for k in interesting:
            v = o.get(k)
            if v and r.get("schema") and str(v).strip().lower() == str(r["schema"]).strip().lower():
                schema_hits[k] = schema_hits.get(k, 0) + 1
            if v and r.get("delivery_city") and str(v).strip().lower() == str(r["delivery_city"]).strip().lower():
                city_hits[k] = city_hits.get(k, 0) + 1
    print(f"  сопоставлено заказов: {tested}")
    print(f"  «ОСС»      = ответственный ЗАКАЗА: {hit_oss_order} | ответственный СДЕЛКИ: {hit_oss_deal}")
    print(f"  «Менеджер» = ответственный ЗАКАЗА: {hit_mgr_order} | ответственный СДЕЛКИ: {hit_mgr_deal}")
    print(f"  «Схема» совпала с полями: {schema_hits or '— не найдено среди отобранных'}")
    print(f"  «Город» совпал с полями: {city_hits or '— не найдено среди отобранных'}")

    # если схема не нашлась — перебираем ВСЕ строковые/списочные поля одного заказа
    if not schema_hits:
        print("\n  --- поиск схемы по всем полям (значения первых 3 заказов) ---")
        for o in orders[:3]:
            print(f"   заказ {str(o.get('title'))[:34]}:")
            for k, v in o.items():
                if isinstance(v, str) and v and len(v) < 60 and k.startswith("ufCrm"):
                    print(f"     {k} = {v[:52]}")

    print("\n✓ зонд v10 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
