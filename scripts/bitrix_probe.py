"""CI-зонд: сделка ЛУКОЙЛ-ЭНЕРГОСЕТИ — какие КП реально получены от поставщиков.

Вопрос владельца (10.09.2026): сорсинг утверждает, что по сделке Энергосети
прокотированы ВСЕ позиции и на всё есть живое КП. Снимок gt/data/bitrix_gt.json
это подтвердить не может: он собирается по ключевым словам ГТУ и сделок
«Энергосети» не содержит вовсе.

Зонд отвечает на три вопроса, читая Bitrix напрямую:
  1. Какие сделки по ЛУКОЙЛ-ЭНЕРГОСЕТИ есть и на какую сумму.
  2. Сколько под ними запросов СП-166 «Запросы поставщикам», по каким поставщикам.
  3. В каких стадиях эти запросы — то есть по скольким КП РЕАЛЬНО получено
     (Selected / Not Selected / Price at Work) против отказов и молчания
     (Отказ в КП / Ответ не получен / Request Sent).

Печатает компактную сводку в лог Actions. Секреты не выводит.
Запуск: Actions → Bitrix probe → Run workflow (ветка с этой версией скрипта).
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402
from config import SPA_ENTITY_TYPE_ID, Settings  # noqa: E402

DEAL_KEYWORDS = ["ЭНЕРГОСЕТИ", "Энергосети", "энергосети", "ЛУКОЙЛ-ЭНЕРГО"]
# стадии СП-166, означающие, что КП от поставщика получено
GOT_QUOTE = {"Selected", "Not Selected", "Price at Work"}
NO_QUOTE = {"Отказ в КП", "Ответ не получен (в срок)", "Не подошло по технике"}


def crm_refs(v) -> list[str]:
    if not v:
        return []
    vals = v if isinstance(v, list) else [v]
    out = []
    for x in vals:
        s = str(x)
        out.append(s.split("_", 1)[1] if s.startswith("CO_") else s)
    return out


def main() -> int:
    client = BitrixClient(Settings.load().bitrix_webhook_url)

    # 1. сделки Энергосети
    deals: dict[str, dict] = {}
    for kw in DEAL_KEYWORDS:
        for d in client.list_paged(
            "crm.deal.list",
            {"filter": {"%TITLE": kw},
             "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID",
                        "COMPANY_ID", "ASSIGNED_BY_ID", "DATE_CREATE"]},
        ):
            deals[str(d["ID"])] = d
    print(f"=== СДЕЛКИ ЭНЕРГОСЕТИ: {len(deals)} ===")
    for did, d in sorted(deals.items(), key=lambda kv: -int(kv[0])):
        opp = d.get("OPPORTUNITY") or 0
        print(f"  {did:>7} | {str(d.get('DATE_CREATE'))[:10]} | {d.get('STAGE_ID'):<24} | "
              f"{float(opp):>16,.0f} {d.get('CURRENCY_ID') or ''} | {(d.get('TITLE') or '')[:80]}")
    if not deals:
        print("  сделок не найдено — проверить ключевые слова")
        return 0

    # 2. запросы СП-166 под этими сделками
    select = ["id", "title", "stageId", "createdTime", "parentId2", "companyId",
              "assignedById", "ufCrm18Supplier", "ufCrm18SupplContact", "opportunity"]
    rfqs: dict[str, dict] = {}
    ids = list(deals)
    for i in range(0, len(ids), 50):
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"parentId2": ids[i:i + 50]}, select=select):
            rfqs[str(r["id"])] = r
    for kw in DEAL_KEYWORDS:
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"%title": kw}, select=select):
            rfqs[str(r["id"])] = r
    print(f"\n=== ЗАПРОСОВ СП-166: {len(rfqs)} ===")

    # 3. справочники
    stages: dict[str, str] = {}
    for cat in (24, 0):
        try:
            stages.update(client.spa_stages(SPA_ENTITY_TYPE_ID, cat))
        except Exception:
            pass
    comp_ids = set()
    for r in rfqs.values():
        if r.get("companyId"):
            comp_ids.add(str(r["companyId"]))
        comp_ids.update(crm_refs(r.get("ufCrm18Supplier")))
    comp_names: dict[str, str] = {}
    if comp_ids:
        for c in client.list_paged("crm.company.list",
                                   {"filter": {"@ID": list(comp_ids)}, "select": ["ID", "TITLE"]}):
            comp_names[str(c["ID"])] = c.get("TITLE") or str(c["ID"])

    # 4. разбор по стадиям — главный ответ
    by_stage = Counter()
    by_deal = defaultdict(Counter)
    suppliers = Counter()
    with_price = 0
    price_sum = 0.0
    for r in rfqs.values():
        st = stages.get(str(r.get("stageId")), str(r.get("stageId")))
        by_stage[st] += 1
        by_deal[str(r.get("parentId2") or "—")][st] += 1
        sup = ", ".join(filter(None, (
            [comp_names.get(str(r.get("companyId") or ""), "")]
            + [comp_names.get(c, c) for c in crm_refs(r.get("ufCrm18Supplier"))]))) or "—"
        suppliers[sup] += 1
        opp = r.get("opportunity")
        if opp:
            with_price += 1
            try:
                price_sum += float(opp)
            except (TypeError, ValueError):
                pass

    print("\n--- СТАДИИ ЗАПРОСОВ (ключ к вопросу «есть ли КП») ---")
    got = ref = other = 0
    for st, n in by_stage.most_common():
        mark = "  КП ЕСТЬ " if st in GOT_QUOTE else ("  нет КП  " if st in NO_QUOTE else "  в работе")
        print(f"  {n:>5} |{mark}| {st}")
        if st in GOT_QUOTE:
            got += n
        elif st in NO_QUOTE:
            ref += n
        else:
            other += n
    tot = sum(by_stage.values()) or 1
    print(f"\n  КП получено      : {got:>5}  ({100 * got / tot:.0f}%)")
    print(f"  отказ / молчание : {ref:>5}  ({100 * ref / tot:.0f}%)")
    print(f"  в работе, без КП : {other:>5}  ({100 * other / tot:.0f}%)")
    print(f"\n  запросов с суммой в карточке: {with_price} из {tot}, итого {price_sum:,.0f}")

    print("\n--- ПО СДЕЛКАМ ---")
    for did, c in sorted(by_deal.items(), key=lambda kv: -sum(kv[1].values())):
        g = sum(n for s, n in c.items() if s in GOT_QUOTE)
        print(f"  сделка {did:>7}: запросов {sum(c.values()):>4}, из них с КП {g:>4} | "
              f"{(deals.get(did, {}).get('TITLE') or '')[:60]}")

    print("\n--- ТОП-25 ПОСТАВЩИКОВ, КОМУ СЛАЛИ ---")
    for s, n in suppliers.most_common(25):
        print(f"  {n:>4}  {s[:88]}")

    print("\n--- ПРИМЕРЫ ЗАПРОСОВ С ПОЛУЧЕННЫМ КП (до 40) ---")
    shown = 0
    for r in sorted(rfqs.values(), key=lambda x: int(x["id"])):
        st = stages.get(str(r.get("stageId")), str(r.get("stageId")))
        if st not in GOT_QUOTE:
            continue
        sup = ", ".join(filter(None, (
            [comp_names.get(str(r.get("companyId") or ""), "")]
            + [comp_names.get(c, c) for c in crm_refs(r.get("ufCrm18Supplier"))]))) or "—"
        opp = r.get("opportunity") or 0
        print(f"  #{r['id']:>7} | {str(r.get('createdTime'))[:10]} | {st:<16} | "
              f"{float(opp or 0):>14,.0f} | {sup[:32]:<32} | {(r.get('title') or '')[:60]}")
        shown += 1
        if shown >= 40:
            break
    print(f"\nвсего запросов с КП: {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
