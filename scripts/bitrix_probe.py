"""CI-зонд v13: точные названия стадий, которые попадают в блок «Где стоит процесс».

Печатаем: стадии воронки реализации (кат.0), стадии заказов СП-172 по всем воронкам,
и сколько живых заказов сейчас в каждой — чтобы понять, какая подпись что означает.
"""
from __future__ import annotations

import os
from collections import Counter

import requests


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


def main() -> int:
    print("=== 1. Стадии ВОРОНКИ РЕАЛИЗАЦИИ (сделки, кат.0) — блок «Воронка реализации» ===")
    for st in bx("crm.status.list", {"filter": {"ENTITY_ID": "DEAL_STAGE"},
                                     "order": {"SORT": "ASC"}}).get("result", []) or []:
        print(f"  {st.get('STATUS_ID')}: «{st.get('NAME')}»")

    print("\n=== 2. Стадии ЗАКАЗОВ СП-172 — блок «Узкие места закупки» ===")
    names = {}
    for cid in (26, 0):
        try:
            res = bx("crm.status.list", {"filter": {"ENTITY_ID": f"DYNAMIC_172_STAGE_{cid}"},
                                         "order": {"SORT": "ASC"}}).get("result", []) or []
            for st in res:
                names[st.get("STATUS_ID")] = st.get("NAME")
                print(f"  [cat {cid}] {st.get('STATUS_ID')}: «{st.get('NAME')}»")
        except Exception as e:
            print(f"  cat {cid}: {type(e).__name__}")

    print("\n=== 3. Сколько живых заказов сейчас в каждой стадии ===")
    orders = bx_all("crm.item.list", {"entityTypeId": 172, "select": ["id", "stageId"]})
    live = [o for o in orders if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    c = Counter(o.get("stageId") for o in live)
    print(f"  всего заказов {len(orders)}, живых {len(live)}")
    for sid, n in c.most_common():
        print(f"    {n:>4} — {sid} «{names.get(sid, '?')}»")

    print("\n✓ зонд v13 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
