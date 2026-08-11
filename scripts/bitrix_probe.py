"""CI-зонд v18: ЗИП Cummins QSK60G — что уже запрашивал сорсинг.

Отвечает на два вопроса перед новым заходом по позициям
«Свеча зажигания 18.049-01 (Cummins 4380132/4390446/5544762)» и
«Фильтр масляный 9050 (Fleetguard LF9050 / Cummins 4920071)»:

1. Были ли у нас сделки/запросы СП-166 по Cummins / QSK / QSV / газопоршневым —
   и чем закончились (стадия, сорсер, дата).
2. Заводили ли мы уже компании-кандидаты мирового пула (Hatraco, PeriParts,
   Techie, Ghaddar, TVH, Diesel Parts Direct …) и слали ли им запросы.

Печатает компактную сводку в лог Actions (секреты не выводятся).
Запуск: gh workflow run probe.yml --ref <ветка>  |  Actions → Bitrix probe.
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

# 1. ключевые слова по технике и позициям
KEYWORDS = ["Cummins", "Камминз", "Камминс", "QSK", "QSV", "газопоршн",
            "ГПЭС", "ГПУ", "свеча зажигания", "свечи зажигания",
            "LF9050", "4380132", "4390446", "5544762", "4924504", "5373898",
            "18.049", "4920071"]

# 2. мировой пул кандидатов по этим позициям (имена как их знает рынок)
CANDIDATES = [
    "Hatraco", "PeriParts", "Peri-Parts", "Techie", "Stitt", "Altronic",
    "Champion", "Federal-Mogul", "Federal Mogul", "BERU", "Denso", "NGK",
    "Motortech", "ERS", "RM Walsh", "Walsh",
    "Diesel Parts Direct", "Area Diesel", "Source One", "FinditParts",
    "Reliable Industries", "Everything Truck", "AGA Parts", "Supply Spare",
    "FridayParts", "Friday Parts", "Makano", "Yemparts", "Buymachineryparts",
    "Ghaddar", "Al Khalij", "Alkhalij", "TVH", "Dynatrade", "Radiant",
    "Fleetguard", "Donaldson", "Baldwin", "WIX", "Sakura", "Hifi", "MANN",
    "Cummins", "Filters King", "Hanton", "Sparkplugs", "Sinotruk",
    "Dongfeng", "Chongqing", "Aksa", "Teksan", "Karatay",
]

SELECT = ["id", "title", "stageId", "createdTime", "parentId2", "companyId",
          "assignedById", "ufCrm18Supplier", "ufCrm18SupplContact", "opportunity"]


def main() -> int:
    client = BitrixClient(Settings.load().bitrix_webhook_url)
    users = client.users()
    stages = client.spa_stages(SPA_ENTITY_TYPE_ID, 24)

    def who(uid):
        return users.get(str(uid), f"#{uid}")

    def stage(sid):
        return stages.get(str(sid), str(sid))

    print("=== 1. Сделки по ключевым словам (Cummins / QSK / газопоршневые / свечи) ===")
    deals: dict[str, dict] = {}
    for kw in KEYWORDS:
        for d in client.list_paged("crm.deal.list", {
            "filter": {"%TITLE": kw},
            "select": ["ID", "TITLE", "STAGE_ID", "DATE_CREATE", "ASSIGNED_BY_ID", "OPPORTUNITY"],
        }):
            deals[str(d["ID"])] = d
    print(f"  найдено сделок: {len(deals)}")
    for d in sorted(deals.values(), key=lambda x: str(x.get("DATE_CREATE")), reverse=True)[:40]:
        print(f"  #{d['ID']} {str(d.get('DATE_CREATE'))[:10]} {str(d.get('TITLE'))[:95]!r} "
              f"стадия={d.get('STAGE_ID')} отв={who(d.get('ASSIGNED_BY_ID'))}")

    print("\n=== 2. Запросы СП-166 по тем же словам (в названии) + привязанные к сделкам ===")
    rfqs: dict[str, dict] = {}
    for kw in KEYWORDS:
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"%title": kw}, select=SELECT):
            rfqs[str(r["id"])] = r
    ids = list(deals)
    for i in range(0, len(ids), 50):
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"parentId2": ids[i:i + 50]}, select=SELECT):
            rfqs[str(r["id"])] = r
    print(f"  найдено запросов: {len(rfqs)}")

    comp_ids = {str(r.get("companyId")) for r in rfqs.values() if r.get("companyId")}
    names = client.companies_by_ids(comp_ids) if comp_ids else {}
    by_comp: dict[str, list] = defaultdict(list)
    for r in sorted(rfqs.values(), key=lambda x: str(x.get("createdTime")), reverse=True):
        cname = names.get(str(r.get("companyId"))) or (r.get("ufCrm18Supplier") or "—")
        by_comp[str(cname)].append(r)
        print(f"  #{r['id']} {str(r.get('createdTime'))[:10]} {str(r.get('title'))[:80]!r} "
              f"→ {str(cname)[:45]} | {stage(r.get('stageId'))} | {who(r.get('assignedById'))}")

    print("\n=== 3. Кому из этих поставщиков уже слали (свод) ===")
    for cname, items in sorted(by_comp.items(), key=lambda kv: -len(kv[1])):
        last = str(items[0].get("createdTime"))[:10]
        print(f"  {len(items):>3} запр. | посл. {last} | {cname[:70]}")

    print("\n=== 4. Компании мирового пула: есть ли в базе и слали ли запросы ===")
    found_any = False
    for cand in CANDIDATES:
        comps = client.list_paged("crm.company.list", {
            "filter": {"%TITLE": cand}, "select": ["ID", "TITLE", "WEB", "EMAIL"]})
        if not comps:
            continue
        found_any = True
        for c in comps[:5]:
            cid = str(c["ID"])
            items = client.list_items(SPA_ENTITY_TYPE_ID, filter={"companyId": cid}, select=SELECT)
            items.sort(key=lambda x: str(x.get("createdTime")), reverse=True)
            head = (f"  [{cand}] #{cid} {str(c.get('TITLE'))[:60]!r} — запросов СП-166: {len(items)}")
            print(head)
            for r in items[:5]:
                print(f"        {str(r.get('createdTime'))[:10]} {str(r.get('title'))[:70]!r} "
                      f"| {stage(r.get('stageId'))} | {who(r.get('assignedById'))}")
    if not found_any:
        print("  ни одна компания мирового пула в базе не заведена")

    print("\n=== 5. Машиночитаемый срез (для сайта) ===")
    dump = {
        "deals": [{"id": d["ID"], "title": d.get("TITLE"), "stage": d.get("STAGE_ID"),
                   "created": str(d.get("DATE_CREATE"))[:10]} for d in deals.values()],
        "rfqs": [{"id": r["id"], "title": r.get("title"),
                  "company": names.get(str(r.get("companyId"))) or r.get("ufCrm18Supplier"),
                  "stage": stage(r.get("stageId")), "created": str(r.get("createdTime"))[:10],
                  "sourcer": who(r.get("assignedById"))} for r in rfqs.values()],
    }
    print(json.dumps(dump, ensure_ascii=False)[:60000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
