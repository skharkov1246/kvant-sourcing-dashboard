"""CI-зонд v19: ближайшие закупки со свечами зажигания (ГПУ/ГПЭС) и эффективность запросов.

Отвечает на три вопроса:
1. В каких сделках (в т.ч. открытых, с ближайшими сроками) присутствуют свечи зажигания —
   ищем и по товарным строкам (crm.product.list → crm.item.productrow.list), и по названиям.
2. Кому по этим сделкам уже слали запросы СП-166 и чем закончилось — база для расчёта
   эффективности (доля ответов, КП, выбранных).
3. Какие компании-производители/дистрибьюторы свечей уже заведены в Bitrix.

Печатает сводку + машиночитаемый JSON в лог Actions (секреты не выводятся).
Запуск: Actions → «Bitrix probe» → Run workflow с нужной ветки.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402
from config import SPA_ENTITY_TYPE_ID, Settings  # noqa: E402

PRODUCT_KW = ["свеч", "spark", "зажиган", "ignition", "запальн", "plug"]
DEAL_KW = ["свеч", "spark plug", "зажиган", "газопоршн", "ГПУ", "ГПЭС", "Jenbacher", "MWM",
           "TCG", "Waukesha", "Guascor", "QSV", "QSK", "G3520", "G3516", "GTA", "Cummins"]
RFQ_KW = ["свеч", "spark", "зажиган", "ignition", "запальн"]
BRANDS = ["Denso", "NGK", "Bosch", "Champion", "Beru", "Altronic", "Stitt", "Hatraco",
          "Motortech", "Torch", "Techie", "Jenbacher", "INNIO", "MWM", "Deutz", "Waukesha",
          "Caterpillar", "Guascor", "Clarke Energy", "IGGNITA", "Federal", "Tenneco",
          "Cummins", "Perkins", "ONERGYS", "Kraftgas", "Gas Motoren"]

SELECT = ["id", "title", "stageId", "createdTime", "parentId2", "companyId",
          "assignedById", "ufCrm18Supplier", "opportunity"]
DSEL = ["ID", "TITLE", "STAGE_ID", "STAGE_SEMANTIC_ID", "CATEGORY_ID", "OPPORTUNITY",
        "CURRENCY_ID", "CLOSEDATE", "DATE_CREATE", "ASSIGNED_BY_ID"]


def chunks(seq, n=50):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main() -> int:
    c = BitrixClient(Settings.load().bitrix_webhook_url)
    users = c.users()
    stages = c.stages()
    cats = c.categories()
    spa_stages = c.spa_stages(SPA_ENTITY_TYPE_ID, 24)
    who = lambda u: users.get(str(u), f"#{u}")            # noqa: E731
    dst = lambda s: stages.get(str(s), str(s))            # noqa: E731
    sst = lambda s: spa_stages.get(str(s), str(s))        # noqa: E731

    print("=== 1. Товары «свеча зажигания» в номенклатуре Bitrix ===")
    prods: dict[str, str] = {}
    for kw in PRODUCT_KW:
        try:
            for p in c.list_paged("crm.product.list", {"filter": {"%NAME": kw}, "select": ["ID", "NAME"]}):
                prods[str(p["ID"])] = p.get("NAME") or ""
        except Exception as e:
            print(f"  ! {kw}: {e}")
    print(f"  товарных карточек со словами {PRODUCT_KW}: {len(prods)}")
    for pid, nm in list(prods.items())[:40]:
        print(f"    #{pid} {nm[:110]}")

    print("\n=== 2. В каких сделках эти товары стоят строками ===")
    rows_by_deal: dict[str, list] = defaultdict(list)
    for chunk in chunks(prods, 40):
        start = 0
        while True:
            try:
                res = c.call("crm.item.productrow.list", {
                    "filter": {"=ownerType": "D", "@productId": chunk},
                    "start": start,
                })
            except Exception as e:
                print(f"  ! productrow.list: {e}")
                res = None
            rows = (res or {}).get("productRows") or []
            for r in rows:
                rows_by_deal[str(r.get("ownerId"))].append({
                    "product": r.get("productName") or prods.get(str(r.get("productId")), ""),
                    "qty": r.get("quantity"), "price": r.get("price"),
                })
            if len(rows) < 50:
                break
            start += 50
    print(f"  сделок с товарными строками по свечам: {len(rows_by_deal)}")

    print("\n=== 3. Сделки по ключевым словам (свечи, ГПУ, парки двигателей) ===")
    deals: dict[str, dict] = {}
    for kw in DEAL_KW:
        for d in c.list_paged("crm.deal.list", {"filter": {"%TITLE": kw}, "select": DSEL}):
            deals[str(d["ID"])] = d
    for chunk in chunks(rows_by_deal.keys(), 50):
        for d in c.list_paged("crm.deal.list", {"filter": {"@ID": chunk}, "select": DSEL}):
            deals[str(d["ID"])] = d
    print(f"  всего сделок: {len(deals)} (из них с товарными строками по свечам: {len(rows_by_deal)})")

    live = [d for d in deals.values() if (d.get("STAGE_SEMANTIC_ID") or "") not in ("S", "F")]
    live.sort(key=lambda d: str(d.get("CLOSEDATE") or "9999"))
    print(f"\n=== 4. БЛИЖАЙШИЕ ОТКРЫТЫЕ ЗАКУПКИ (сортировка по дате закрытия) — {len(live)} шт ===")
    for d in live[:45]:
        did = str(d["ID"])
        mark = "🕯" if did in rows_by_deal else "  "
        print(f"  {mark} #{did} закрытие {str(d.get('CLOSEDATE'))[:10]} | {str(d.get('TITLE'))[:88]!r}")
        print(f"      воронка={cats.get(str(d.get('CATEGORY_ID')), d.get('CATEGORY_ID'))} | стадия={dst(d.get('STAGE_ID'))} "
              f"| сумма={d.get('OPPORTUNITY')} {d.get('CURRENCY_ID')} | отв={who(d.get('ASSIGNED_BY_ID'))}")
        for r in rows_by_deal.get(did, [])[:6]:
            print(f"      · {str(r['product'])[:80]} × {r['qty']} по {r['price']}")

    print("\n=== 5. Запросы СП-166 со свечами в названии ===")
    rfqs: dict[str, dict] = {}
    for kw in RFQ_KW:
        for r in c.list_items(SPA_ENTITY_TYPE_ID, filter={"%title": kw}, select=SELECT):
            rfqs[str(r["id"])] = r
    linked: dict[str, dict] = {}
    for chunk in chunks(deals.keys(), 50):
        for r in c.list_items(SPA_ENTITY_TYPE_ID, filter={"parentId2": chunk}, select=SELECT):
            linked[str(r["id"])] = r
    print(f"  по названию: {len(rfqs)} | привязанных к этим сделкам: {len(linked)}")

    allr = {**linked, **rfqs}
    comp_ids = {str(r.get("companyId")) for r in allr.values() if r.get("companyId")}
    names = c.companies_by_ids(comp_ids) if comp_ids else {}
    cname = lambda r: names.get(str(r.get("companyId"))) or (r.get("ufCrm18Supplier") or "—")  # noqa: E731

    for r in sorted(rfqs.values(), key=lambda x: str(x.get("createdTime")), reverse=True)[:60]:
        print(f"  #{r['id']} {str(r.get('createdTime'))[:10]} {str(r.get('title'))[:70]!r} "
              f"→ {str(cname(r))[:45]} | {sst(r.get('stageId'))} | {who(r.get('assignedById'))}")

    print("\n=== 6. Эффективность: во что превращаются наши запросы ===")
    def funnel(items, label):
        cnt = Counter(sst(r.get("stageId")) for r in items)
        tot = sum(cnt.values())
        print(f"  {label}: {tot} запросов")
        for k, v in cnt.most_common():
            print(f"      {v:>4} ({v / tot * 100:>4.0f} %) — {k}")
    if rfqs:
        funnel(list(rfqs.values()), "запросы со свечами в названии")
    if linked:
        funnel(list(linked.values()), "все запросы по сделкам этих парков")

    print("\n=== 7. Топ поставщиков по этим запросам (кто реально доходит до КП) ===")
    by_comp: dict[str, list] = defaultdict(list)
    for r in allr.values():
        by_comp[str(cname(r))].append(r)
    def score(items):
        s = [sst(x.get("stageId")) for x in items]
        return sum(1 for x in s if "Selected" in x or "КП" in x and "Отказ" not in x)
    for nm, items in sorted(by_comp.items(), key=lambda kv: -len(kv[1]))[:45]:
        last = max(str(x.get("createdTime"))[:10] for x in items)
        st = Counter(sst(x.get("stageId")) for x in items)
        print(f"  {len(items):>3} | посл. {last} | {nm[:52]:<52} | {dict(st)}")

    print("\n=== 8. Производители и дистрибьюторы свечей: кто уже заведён в Bitrix ===")
    pool = []
    for b in BRANDS:
        try:
            comps = c.list_paged("crm.company.list", {"filter": {"%TITLE": b}, "select": ["ID", "TITLE", "WEB"]})
        except Exception:
            comps = []
        for co in comps[:6]:
            cid = str(co["ID"])
            items = c.list_items(SPA_ENTITY_TYPE_ID, filter={"companyId": cid}, select=SELECT)
            items.sort(key=lambda x: str(x.get("createdTime")), reverse=True)
            pool.append({"brand": b, "id": cid, "title": co.get("TITLE"), "n": len(items),
                         "last": str(items[0].get("createdTime"))[:10] if items else "",
                         "stage": sst(items[0].get("stageId")) if items else ""})
            print(f"  [{b}] #{cid} {str(co.get('TITLE'))[:58]!r} — запросов: {len(items)}"
                  + (f" | посл. {str(items[0].get('createdTime'))[:10]} — {sst(items[0].get('stageId'))}" if items else ""))

    print("\n=== 9. JSON для сайта ===")
    dump = {
        "live_deals": [{"id": str(d["ID"]), "title": d.get("TITLE"), "close": str(d.get("CLOSEDATE"))[:10],
                        "created": str(d.get("DATE_CREATE"))[:10],
                        "cat": cats.get(str(d.get("CATEGORY_ID")), str(d.get("CATEGORY_ID"))),
                        "stage": dst(d.get("STAGE_ID")), "sum": d.get("OPPORTUNITY"),
                        "cur": d.get("CURRENCY_ID"), "owner": who(d.get("ASSIGNED_BY_ID")),
                        "rows": rows_by_deal.get(str(d["ID"]), [])[:8]} for d in live[:60]],
        "plug_rfqs": [{"id": str(r["id"]), "date": str(r.get("createdTime"))[:10], "title": r.get("title"),
                       "company": cname(r), "stage": sst(r.get("stageId")),
                       "sourcer": who(r.get("assignedById"))} for r in rfqs.values()],
        "funnel_plugs": dict(Counter(sst(r.get("stageId")) for r in rfqs.values())),
        "funnel_parks": dict(Counter(sst(r.get("stageId")) for r in linked.values())),
        "brand_pool": pool,
    }
    print(json.dumps(dump, ensure_ascii=False)[:120000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
