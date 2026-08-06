"""CI-зонд v15: сущность «Бюджет сделки» — есть ли там полная себестоимость.

В воронке реализации есть стадия «Бюджет сделки согласован | Оплата поставщикам»,
а в заказах СП-172 — даты «…, budget». Значит бюджет ведётся отдельно.
Ищем его смарт-процесс, разбираем поля затрат и меряем заполняемость.
"""
from __future__ import annotations

import os
import re
from collections import Counter

import requests

KEYS = ("логист", "доставк", "таможн", "пошлин", "ндс", "vat", "сбор", "агент", "комисс",
        "страхов", "сертиф", "себестоим", "cost", "margin", "маржа", "наценк", "транспорт",
        "фрахт", "брокер", "склад", "затрат", "расход", "бюджет", "budget", "прибыл", "profit",
        "выручк", "revenue", "закуп", "продаж")


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
    print("=== 1. Все смарт-процессы портала ===")
    types = (bx("crm.type.list", {}).get("result") or {}).get("types") or []
    budget_ids = []
    for t in types:
        tid = t.get("entityTypeId")
        title = str(t.get("title") or "")
        print(f"  entityTypeId={tid} · «{title}» (код {t.get('name')})")
        if any(w in title.lower() for w in ("бюджет", "budget", "эконом")):
            budget_ids.append((tid, title))
    print(f"\n  похоже на бюджет/экономику: {budget_ids or 'не найдено'}")

    for tid, title in budget_ids or []:
        print(f"\n=== 2. Поля «{title}» (entityTypeId={tid}) ===")
        f = bx("crm.item.fields", {"entityTypeId": tid}).get("result", {}).get("fields", {})
        print(f"  всего полей: {len(f)}")
        money = {k: v for k, v in f.items()
                 if v.get("type") in ("money", "double", "integer")
                 or any(w in (v.get("title") or "").lower() for w in KEYS)}
        for k, v in money.items():
            print(f"    {k} [{v.get('type')}]: «{str(v.get('title'))[:60]}»")

        print(f"\n=== 3. Записи «{title}»: сколько и как связаны со сделками ===")
        sel = ["id", "title", "stageId", "createdTime", "opportunity", "currencyId",
               "parentId2"] + list(money)
        try:
            items = bx_all("crm.item.list", {"entityTypeId": tid, "select": sel})
        except Exception as e:
            print(f"    ошибка выгрузки: {type(e).__name__}")
            continue
        print(f"    записей: {len(items)}")
        withparent = sum(1 for i in items if i.get("parentId2"))
        print(f"    привязано к сделке (parentId2): {withparent}")
        if items:
            ex = items[0]
            print(f"    пример записи #{ex.get('id')}: «{str(ex.get('title'))[:50]}» "
                  f"стадия={ex.get('stageId')} сделка={ex.get('parentId2')}")
            print("    --- заполняемость полей затрат ---")
            for k, v in money.items():
                n = sum(1 for i in items if i.get(k) not in (None, "", 0, "0", "0.00"))
                if n:
                    vals = [str(i.get(k))[:22] for i in items
                            if i.get(k) not in (None, "", 0, "0", "0.00")][:3]
                    print(f"      {k} «{str(v.get('title'))[:38]}»: {n} из {len(items)} · {vals}")
            print("    --- все непустые поля первой записи (что реально ведут) ---")
            full = bx("crm.item.get", {"entityTypeId": tid, "id": ex.get("id")}).get("result", {})
            it = (full or {}).get("item") or {}
            for k, v in it.items():
                if v not in (None, "", 0, "0", [], {}) and not k.startswith("ufCrm"):
                    print(f"      {k} = {str(v)[:44]}")
            for k, v in it.items():
                if v not in (None, "", 0, "0", [], {}) and k.startswith("ufCrm"):
                    ttl = (f.get(k) or {}).get("title") or ""
                    print(f"      {k} «{str(ttl)[:34]}» = {str(v)[:34]}")

    print("\n=== 4. Сколько сделок кат.0 дошли до стадии «Бюджет согласован» и дальше ===")
    deals = bx_all("crm.deal.list", {"filter": {"CATEGORY_ID": 0},
                                     "select": ["ID", "STAGE_ID", "STAGE_SEMANTIC_ID"]})
    print(f"  сделок кат.0: {len(deals)} · по стадиям: {dict(Counter(d.get('STAGE_ID') for d in deals))}")

    print("\n✓ зонд v15 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
