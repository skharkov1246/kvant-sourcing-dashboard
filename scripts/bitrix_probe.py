"""CI-зонд v2: словарь UF-полей, money-поля сделок, товарные позиции заказов, ссылки.

Запускается ТОЛЬКО вручную (workflow_dispatch, .github/workflows/probe.yml).
Читает Bitrix через секрет CI и печатает диагностику в лог — сам вебхук не выводит.

Вопросы v2:
  H. Названия (label) всех UF-полей СДЕЛКИ — что такое UF_CRM_1704981063967 ("362044|CNY"),
     UF_CRM_1710446284794 ("4754113.13|RUB"), поля-ссылки и т.д.
  I. Названия ufCrm20_* полей ЗАКАЗА СП-172 (числа-кандидаты на суммы, плановые даты).
  J. Money-поля по 114 реализованным сделкам: заполненность; закрывают ли они 29 дыр buy=0.
  K. Товарные позиции заказов с opportunity=0 — лежит ли сумма в товарах.
  L. Куда ведут короткие ссылки (clck.ru, ~xxx) — HEAD-редирект без авторизации.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from bitrix_client import BitrixClient
from company import _regno


def hdr(t):
    print(f"\n{'='*12} {t} {'='*12}")


def j(x, cap=8000):
    s = json.dumps(x, ensure_ascii=False, indent=1, default=str)
    print(s[:cap] + ("\n…[обрезано]" if len(s) > cap else ""))


MONEY_A = "UF_CRM_1704981063967"
MONEY_B = "UF_CRM_1710446284794"
LINKS = ["UF_CRM_65F30719CA2A8", "UF_CRM_1733957245242", "UF_CRM_1740133235324"]


def main() -> int:
    s = config.Settings.load()
    c = BitrixClient(s.bitrix_webhook_url)

    hdr("H. Словарь UF-полей сделки (crm.deal.fields)")
    flds = c.call("crm.deal.fields", {}) or {}
    uf = {k: {"type": v.get("type"), "label": (v.get("formLabel") or v.get("listLabel") or v.get("title") or "")}
          for k, v in flds.items() if k.startswith("UF_")}
    interesting = {k: v for k, v in uf.items()
                   if v["type"] in ("money", "url", "string", "double", "integer", "file", "crm")
                   or k in LINKS + [MONEY_A, MONEY_B]}
    j(interesting, 9000)

    hdr("I. Словарь полей заказа СП-172 (crm.item.fields)")
    f172 = c.call("crm.item.fields", {"entityTypeId": 172}) or {}
    f172 = f172.get("fields") if isinstance(f172, dict) and "fields" in f172 else f172
    uf172 = {k: {"type": v.get("type"), "label": (v.get("title") or "")}
             for k, v in (f172 or {}).items() if k.startswith("ufCrm")}
    j(uf172, 9000)

    hdr("J. Money-поля по реализованным сделкам — закрывают ли дыры buy=0")
    orders = c.list_items(172, filter={">=createdTime": "2026-01-01T00:00:00"},
                          select=["id", "opportunity", "currencyId", "parentId2", "stageId"])
    live = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    buy = defaultdict(float)
    for o in live:
        if o.get("parentId2"):
            buy[str(o["parentId2"])] += float(o.get("opportunity") or 0)
    deals = c.list_deals_fast(filter={">=DATE_CREATE": "2026-01-01"},
                              select=["ID", "TITLE", "OPPORTUNITY", "CURRENCY_ID", MONEY_A, MONEY_B])
    parents = set(buy.keys()) | {str(o.get("parentId2")) for o in live if o.get("parentId2")}
    realized = [d for d in deals if str(d["ID"]) in parents or _regno(d.get("TITLE")) > 0]
    def money_val(d, f):
        v = str(d.get(f) or "")
        return v if v and v not in ("0", "0|", "|") else ""
    stats = {"всего реализованных": len(realized),
             f"{MONEY_A} заполнено": sum(1 for d in realized if money_val(d, MONEY_A)),
             f"{MONEY_B} заполнено": sum(1 for d in realized if money_val(d, MONEY_B))}
    zero = [d for d in realized if buy.get(str(d["ID"]), 0.0) == 0]
    stats["buy=0 всего"] = len(zero)
    stats[f"buy=0 и есть {MONEY_A}"] = sum(1 for d in zero if money_val(d, MONEY_A))
    stats[f"buy=0 и есть {MONEY_B}"] = sum(1 for d in zero if money_val(d, MONEY_B))
    j(stats)
    print("примеры buy=0 с money-полями (id · A · B):")
    for d in zero[:10]:
        print(f"  {d['ID']} · A={money_val(d, MONEY_A) or '—'} · B={money_val(d, MONEY_B) or '—'}")

    hdr("K. Товарные позиции заказов с opportunity=0")
    zero_opp = [o for o in live if float(o.get("opportunity") or 0) == 0][:3]
    for o in zero_opp:
        oid = o["id"]
        for owner_type in ("Tac", "DYNAMIC_172"):
            try:
                pr = c.call("crm.item.productrow.list",
                            {"filter": {"=ownerId": oid, "=ownerType": owner_type}}) or {}
                rows = pr.get("productRows") if isinstance(pr, dict) else pr
                print(f"заказ {oid} ownerType={owner_type}: строк={len(rows or [])}")
                if rows:
                    j([{k: r.get(k) for k in ("productName", "price", "quantity", "sum")} for r in rows[:4]], 1200)
                break
            except Exception as e:
                print(f"заказ {oid} ownerType={owner_type}: ошибка {e}")

    hdr("L. Куда ведут короткие ссылки (HEAD, без авторизации)")
    for url in ["https://clck.ru/3RETQu", "https://clck.ru/3RcC4F",
                "https://kvantpro.bitrix24.ru/~F2LMd", "https://kvantpro.bitrix24.ru/~zZ6JR"]:
        try:
            r = requests.head(url, allow_redirects=False, timeout=10)
            print(f"{url} → {r.status_code} Location: {r.headers.get('Location', '—')[:160]}")
        except Exception as e:
            print(f"{url} → ошибка {type(e).__name__}")

    print("\n✓ зонд v2 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
