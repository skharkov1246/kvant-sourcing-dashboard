"""CI-зонд v20: состав ближайших закупок со свечами + кому по ним уже слали.

Для десятка ближайших/крупных сделок по паркам ГПУ (Waukesha, Cummins, Jenbacher, MWM)
достаёт товарные строки (crm.deal.productrows.get), связанные запросы СП-166 и суммы —
чтобы понять, где свечи входят прямо в предмет закупки и какой там объём.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402
from config import SPA_ENTITY_TYPE_ID, Settings  # noqa: E402

DEALS = ["22974", "22162", "21618", "22418", "21782", "22552", "22016", "22144",
         "12558", "7942", "9462", "3706", "10704", "20788", "19092", "11356"]
SELECT = ["id", "title", "stageId", "createdTime", "companyId", "assignedById",
          "ufCrm18Supplier", "opportunity"]


def main() -> int:
    c = BitrixClient(Settings.load().bitrix_webhook_url)
    users = c.users()
    spa = c.spa_stages(SPA_ENTITY_TYPE_ID, 24)
    stages = c.stages()
    who = lambda u: users.get(str(u), f"#{u}")     # noqa: E731

    print("=== Состав сделок: товарные строки ===")
    rows_all = {}
    for did in DEALS:
        try:
            rows = c.call("crm.deal.productrows.get", {"id": did}) or []
        except Exception as e:
            print(f"  #{did}: ошибка {e}")
            continue
        rows_all[did] = rows
        d = c.call("crm.deal.get", {"id": did}) or {}
        print(f"\n  #{did} {str(d.get('TITLE'))[:95]!r}")
        print(f"      стадия={stages.get(str(d.get('STAGE_ID')), d.get('STAGE_ID'))} | закрытие={str(d.get('CLOSEDATE'))[:10]} "
              f"| сумма={d.get('OPPORTUNITY')} {d.get('CURRENCY_ID')} | строк={len(rows)}")
        plug = [r for r in rows if any(k in str(r.get("PRODUCT_NAME", "")).lower()
                                       for k in ("свеч", "spark", "зажиган", "plug", "ignition"))]
        for r in (plug or rows)[:25]:
            mark = "🕯" if r in plug else " ·"
            print(f"      {mark} {str(r.get('PRODUCT_NAME'))[:78]:<78} × {r.get('QUANTITY')} по {r.get('PRICE')}")
        if rows and not plug:
            print(f"      (свечей в строках нет; всего строк {len(rows)}, показаны первые)")

    print("\n=== Кому слали запросы по этим сделкам ===")
    by_deal = defaultdict(list)
    for i in range(0, len(DEALS), 50):
        for r in c.list_items(SPA_ENTITY_TYPE_ID, filter={"parentId2": DEALS[i:i + 50]}, select=SELECT):
            by_deal[str(r.get("parentId2"))].append(r)
    comp = {str(r.get("companyId")) for v in by_deal.values() for r in v if r.get("companyId")}
    names = c.companies_by_ids(comp) if comp else {}
    for did in DEALS:
        items = by_deal.get(did) or []
        if not items:
            continue
        print(f"\n  #{did}: запросов {len(items)}")
        for r in sorted(items, key=lambda x: str(x.get("createdTime")), reverse=True)[:25]:
            nm = names.get(str(r.get("companyId"))) or r.get("ufCrm18Supplier") or "—"
            print(f"      {str(r.get('createdTime'))[:10]} {str(nm)[:46]:<46} | {spa.get(str(r.get('stageId')), r.get('stageId'))} "
                  f"| {who(r.get('assignedById'))} | {str(r.get('title'))[:50]}")

    print("\n=== JSON ===")
    print(json.dumps({
        "rows": {k: [{"n": r.get("PRODUCT_NAME"), "q": r.get("QUANTITY"), "p": r.get("PRICE")} for r in v]
                 for k, v in rows_all.items()},
        "rfqs": {k: [{"d": str(r.get("createdTime"))[:10],
                      "c": names.get(str(r.get("companyId"))) or r.get("ufCrm18Supplier"),
                      "s": spa.get(str(r.get("stageId")), r.get("stageId")),
                      "u": who(r.get("assignedById")), "t": r.get("title")} for r in v]
                 for k, v in by_deal.items()},
    }, ensure_ascii=False)[:100000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
