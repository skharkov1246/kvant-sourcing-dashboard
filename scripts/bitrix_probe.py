"""CI-зонд: диагностика данных Bitrix для методологии вкладки «Реализация».

Запускается ТОЛЬКО вручную (workflow_dispatch, .github/workflows/probe.yml).
Читает Bitrix через секрет CI и печатает диагностику в лог — сам вебхук не выводит.

Вопросы, на которые отвечает:
  A. Какие стадии у воронки реализации (кат. 0) — id, имена, порядок.
  B. Какие воронки/стадии у Заказов поставщикам (СП-172).
  C. Отдаёт ли crm.stagehistory.list ВСЕ переходы по стадиям (структура записи) для сделок кат.0.
  D. Работает ли crm.stagehistory.list для СП-172 (entityTypeId=172).
  E. Почему закупка нулевая: сколько реализованных сделок без заказов / с заказами на 0.
  F. Все непустые поля сделки-примера с нулевой закупкой (ищем «экономику проекта»: ссылки, UF-поля).
  G. Все непустые поля одного заказа СП-172 (нет ли суммы в другом поле).
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from bitrix_client import BitrixClient
from company import _regno


def hdr(t):
    print(f"\n{'='*12} {t} {'='*12}")


def j(x, cap=7000):
    s = json.dumps(x, ensure_ascii=False, indent=1, default=str)
    print(s[:cap] + ("\n…[обрезано]" if len(s) > cap else ""))


def main() -> int:
    s = config.Settings.load()
    c = BitrixClient(s.bitrix_webhook_url)

    hdr("A. Стадии воронки реализации (кат.0, сделки)")
    st = c.stages()
    c0 = {k: v for k, v in st.items() if k.startswith("C0:") or ":" not in k}
    j(c0)

    hdr("B. Воронки и стадии СП-172")
    try:
        cats172 = c.call("crm.category.list", {"entityTypeId": 172}) or {}
        j(cats172, 1500)
        cat_items = (cats172.get("categories") if isinstance(cats172, dict) else cats172) or []
        for cat in cat_items[:3]:
            cid = cat.get("id")
            print(f"-- стадии категории {cid} ({cat.get('name')}):")
            j(c.spa_stages(172, int(cid)), 1500)
    except Exception as e:
        print(f"ошибка: {type(e).__name__}: {e}")

    hdr("C. stagehistory сделок кат.0 — структура записей (последние 5)")
    try:
        d = c.call("crm.stagehistory.list", {
            "entityTypeId": 2, "filter": {"CATEGORY_ID": 0},
            "order": {"ID": "DESC"}, "start": 0}) or {}
        items = (d.get("items") if isinstance(d, dict) else d) or []
        j(items[:5])
        print(f"полей в записи: {sorted(items[0].keys()) if items else '—'}")
    except Exception as e:
        print(f"ошибка: {type(e).__name__}: {e}")

    hdr("D. stagehistory для СП-172 — работает ли")
    try:
        d = c.call("crm.stagehistory.list", {
            "entityTypeId": 172, "order": {"ID": "DESC"}, "start": 0}) or {}
        items = (d.get("items") if isinstance(d, dict) else d) or []
        print(f"записей на первой странице: {len(items)}")
        j(items[:3])
    except Exception as e:
        print(f"ошибка: {type(e).__name__}: {e}")

    hdr("E. Диагностика нулевой закупки (YTD)")
    orders = c.list_items(172, filter={">=createdTime": "2026-01-01T00:00:00"},
                          select=["id", "title", "stageId", "opportunity", "currencyId", "parentId2"])
    live = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    no_parent = [o for o in live if not o.get("parentId2")]
    zero_opp = [o for o in live if float(o.get("opportunity") or 0) == 0]
    print(f"заказов YTD (без FAIL): {len(live)} | без parentId2: {len(no_parent)} | с opportunity=0: {len(zero_opp)}")
    from collections import defaultdict
    buy = defaultdict(float)
    for o in live:
        if o.get("parentId2"):
            buy[str(o["parentId2"])] += float(o.get("opportunity") or 0)
    deals = c.list_deals_fast(filter={">=DATE_CREATE": "2026-01-01"},
                              select=["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID",
                                      "OPPORTUNITY", "CURRENCY_ID"])
    order_parents = set(buy.keys()) | {str(o.get("parentId2")) for o in live if o.get("parentId2")}
    realized = [dd for dd in deals if str(dd["ID"]) in order_parents or _regno(dd.get("TITLE")) > 0]
    zero_buy = [dd for dd in realized if buy.get(str(dd["ID"]), 0.0) == 0]
    print(f"реализованных сделок YTD: {len(realized)} | из них с закупкой 0: {len(zero_buy)}")
    print("примеры сделок с закупкой 0 (id · номер · название · сумма сделки · заказов):")
    for dd in zero_buy[:8]:
        did = str(dd["ID"])
        n_ord = sum(1 for o in live if str(o.get("parentId2")) == did)
        print(f"  {did} · №{_regno(dd.get('TITLE'))} · {str(dd.get('TITLE'))[:48]} · opp={dd.get('OPPORTUNITY')} {dd.get('CURRENCY_ID')} · заказов={n_ord}")
    print("примеры заказов с opportunity=0 (id · название · стадия):")
    for o in zero_opp[:6]:
        print(f"  {o.get('id')} · {str(o.get('title'))[:50]} · {o.get('stageId')}")

    hdr("F. Все непустые поля сделки с нулевой закупкой (ищем экономику проекта)")
    sample = zero_buy[:2] if zero_buy else realized[:2]
    for dd in sample:
        did = str(dd["ID"])
        full = c.call("crm.deal.get", {"id": did}) or {}
        nonempty = {k: v for k, v in full.items()
                    if v not in (None, "", "0", 0, [], {}, "N", "0.00") and not k.startswith("~")}
        print(f"--- сделка {did} ({str(full.get('TITLE'))[:40]}) — непустые поля:")
        j(nonempty, 5000)
        links = {k: v for k, v in full.items() if isinstance(v, str) and re.search(r"https?://", v)}
        print(f"поля со ссылками: {list(links.keys()) or '—'}")
        if links:
            j(links, 1500)

    hdr("G. Все непустые поля одного заказа СП-172")
    if live:
        oid = live[0]["id"]
        full = c.call("crm.item.get", {"entityTypeId": 172, "id": oid}) or {}
        item = full.get("item") if isinstance(full, dict) else full
        nonempty = {k: v for k, v in (item or {}).items() if v not in (None, "", 0, [], {}, "N")}
        j(nonempty, 5000)

    print("\n✓ зонд завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
