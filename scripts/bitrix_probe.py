"""CI-зонд v16: найти «Бюджет сделки» — с показом ошибок API, а не молчком.

1. crm.type.list с выводом сырого ответа (вдруг отказ в доступе).
2. Все PARENT_ID_* на сделке — это связи с другими смарт-процессами.
3. Поля со словом «бюджет/budget» в сделках и заказах + их заполняемость.
"""
from __future__ import annotations

import os
import re

import requests

BKEYS = ("бюджет", "budget")


def bx(method: str, params: dict | None = None, *, loud: bool = False) -> dict:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    last = None
    for _ in range(3):
        try:
            r = requests.post(f"{base}/{method}.json", json=params or {}, timeout=60)
            j = r.json() if r.content else {}
            if r.status_code >= 400 or (isinstance(j, dict) and j.get("error")):
                last = f"HTTP {r.status_code} · {str(j)[:200]}"
                if loud:
                    print(f"    ! {method}: {last}")
                return j if isinstance(j, dict) else {}
            return j
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    if loud and last:
        print(f"    ! {method}: {last}")
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


def main() -> int:
    print("=== 1. crm.type.list — сырой ответ ===")
    j = bx("crm.type.list", {}, loud=True)
    res = j.get("result")
    print(f"  ключи ответа: {list(j.keys())}")
    types = (res or {}).get("types") if isinstance(res, dict) else None
    if types:
        for t in types:
            print(f"  entityTypeId={t.get('entityTypeId')} · «{t.get('title')}»")
    else:
        print(f"  result: {str(res)[:300]}")

    print("\n=== 2. Связи сделки с другими смарт-процессами (PARENT_ID_*) ===")
    fd = bx("crm.deal.fields", {}).get("result", {}) or {}
    parents = {k: (fd[k].get("formLabel") or fd[k].get("title") or k)
               for k in fd if k.startswith("PARENT_ID_")}
    for k, t in parents.items():
        tid = k.replace("PARENT_ID_", "")
        print(f"  {k} → entityTypeId {tid}: «{t}»")
        info = bx("crm.type.get", {"id": tid})
        ti = (info.get("result") or {}).get("type") or {}
        if ti:
            print(f"      подтверждение: «{ti.get('title')}» (код {ti.get('name')})")

    print("\n=== 3. Поля со словом «бюджет/budget» в СДЕЛКАХ ===")
    dbud = {k: str(fd[k].get("formLabel") or fd[k].get("title") or k) for k in fd
            if any(w in str(fd[k].get("formLabel") or fd[k].get("title") or "").lower() for w in BKEYS)}
    for k, t in dbud.items():
        print(f"  {k} [{fd[k].get('type')}]: «{t[:70]}»")
    if not dbud:
        print("  нет")

    print("\n=== 4. Поля со словом «бюджет/budget» в ЗАКАЗАХ СП-172 ===")
    f172 = bx("crm.item.fields", {"entityTypeId": 172}).get("result", {}).get("fields", {})
    obud = {k: str(v.get("title") or k) for k, v in f172.items()
            if any(w in str(v.get("title") or "").lower() for w in BKEYS)}
    for k, t in obud.items():
        print(f"  {k} [{f172[k].get('type')}]: «{t[:70]}»")
    if not obud:
        print("  нет")

    print("\n=== 5. Заполняемость бюджетных полей ===")
    if dbud:
        deals = bx_all("crm.deal.list", {"filter": {"CATEGORY_ID": 0},
                                         "select": ["ID", "TITLE", "STAGE_ID"] + list(dbud)})
        print(f"  сделок кат.0: {len(deals)}")
        for k, t in dbud.items():
            vals = [d.get(k) for d in deals if d.get(k) not in (None, "", 0, "0", "[]")]
            if vals:
                print(f"    {k} «{t[:44]}»: заполнено {len(vals)} · примеры: {[str(v)[:26] for v in vals[:3]]}")
    if obud:
        orders = bx_all("crm.item.list", {"entityTypeId": 172,
                                          "select": ["id", "stageId"] + list(obud)})
        live = [o for o in orders if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
        print(f"  заказов: {len(orders)}, живых: {len(live)}")
        for k, t in obud.items():
            n = sum(1 for o in orders if o.get(k) not in (None, "", 0, "0", "[]"))
            nl = sum(1 for o in live if o.get(k) not in (None, "", 0, "0", "[]"))
            if n:
                ex = [str(o.get(k))[:26] for o in orders if o.get(k) not in (None, "", 0, "0", "[]")][:3]
                print(f"    {k} «{t[:40]}»: всего {n}, живых {nl} · {ex}")

    print("\n=== 6. Все смарт-процессы перебором entityTypeId (128…200) ===")
    for tid in range(128, 201):
        info = bx("crm.type.get", {"id": tid})
        ti = (info.get("result") or {}).get("type") or {}
        if ti and ti.get("title"):
            print(f"  {tid}: «{ti.get('title')}»")

    print("\n✓ зонд v16 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
