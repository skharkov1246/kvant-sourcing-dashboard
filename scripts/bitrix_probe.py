"""CI-зонд v11: кто такой «ОСС» в файле коллег + значения справочника «Схема поставки»."""
from __future__ import annotations

import json
import os
import re

import requests

FULL = json.load(open(os.path.join(os.path.dirname(__file__), "_coll_full.json"), encoding="utf-8"))
ROWS = json.load(open(os.path.join(os.path.dirname(__file__), "_coll_rows.json"), encoding="utf-8"))
SCHEMA_F = "ufCrm20_1724941500"


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
    print("=== 1. Справочник «Схема поставки» ===")
    f172 = bx("crm.item.fields", {"entityTypeId": 172}).get("result", {}).get("fields", {})
    items = (f172.get(SCHEMA_F) or {}).get("items") or []
    print(f"  {SCHEMA_F}: {len(items)} значений")
    for i in items:
        print(f"    {i.get('ID')} = {i.get('VALUE')}")

    print("\n=== 2. Поля-люди в СДЕЛКАХ (кандидаты на «ОСС») ===")
    fd = bx("crm.deal.fields", {}).get("result", {}) or {}
    userf = {k: (v.get("formLabel") or v.get("title") or v.get("listLabel") or "")
             for k, v in fd.items() if v.get("type") in ("employee", "user")}
    for k, t in userf.items():
        print(f"  {k}: «{t}»")

    print("\n=== 3. Поля-люди в ЗАКАЗАХ СП-172 ===")
    userf172 = {k: (v.get("title") or "") for k, v in f172.items()
                if v.get("type") in ("employee", "user")}
    for k, t in userf172.items():
        print(f"  {k}: «{t}»")

    # ---------- 4. проверка: чьё имя совпадает с колонкой «ОСС» ----------
    dealids = sorted({r["deal"] for r in ROWS if r["deal"]})
    OSEL = ["id", "title", "parentId2", "assignedById", "observers", SCHEMA_F] + list(userf172)
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
                                          "select": ["ID"] + list(userf)}):
            deals[str(d["ID"])] = d

    uids = set()
    for o in orders:
        for k in list(userf172) + ["assignedById"]:
            v = o.get(k)
            if v:
                uids |= {str(x) for x in (v if isinstance(v, list) else [v])}
    for d in deals.values():
        for k in userf:
            v = d.get(k)
            if v:
                uids |= {str(x) for x in (v if isinstance(v, list) else [v])}
    uids = {u for u in uids if u.isdigit()}
    users = {}
    for uid in sorted(uids):
        u = (bx("user.get", {"ID": uid}).get("result") or [{}])[0]
        users[uid] = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
    print(f"\n=== 4. Проверено пользователей: {len(users)} ===")

    hits_o = {k: 0 for k in list(userf172) + ["assignedById"]}
    hits_d = {k: 0 for k in userf}
    tested = 0
    for r in FULL:
        o = bykey.get(okey(r.get("order")))
        if not o or not r.get("oss"):
            continue
        tested += 1
        want = r["oss"].strip().lower()
        for k in hits_o:
            v = o.get(k)
            names = [users.get(str(x), "") for x in (v if isinstance(v, list) else [v])] if v else []
            if any(n.lower() == want for n in names if n):
                hits_o[k] += 1
        d = deals.get(str(o.get("parentId2")) or "")
        for k in hits_d:
            v = (d or {}).get(k)
            names = [users.get(str(x), "") for x in (v if isinstance(v, list) else [v])] if v else []
            if any(n.lower() == want for n in names if n):
                hits_d[k] += 1
    print(f"  строк с «ОСС» проверено: {tested}")
    print("  совпадения в полях ЗАКАЗА:", {k: v for k, v in hits_o.items() if v} or "нет")
    print("  совпадения в полях СДЕЛКИ:", {k: v for k, v in hits_d.items() if v} or "нет")

    # схема: сверка значений
    idv = {str(i.get("ID")): i.get("VALUE") for i in items}
    sh = 0
    for r in FULL:
        o = bykey.get(okey(r.get("order")))
        if o and r.get("schema") and idv.get(str(o.get(SCHEMA_F))) == r["schema"]:
            sh += 1
    print(f"\n  «Схема поставки» совпала: {sh} из {sum(1 for r in FULL if r.get('schema'))}")

    print("\n✓ зонд v11 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
