"""CI-зонд v14: чем закрыть разрыв между валовой и плановой маржой.

Ищем в Битриксе всё, что похоже на затраты между закупкой и продажей:
логистика, таможня, пошлины, НДС, агентские, страховка, сертификация.
Меряем не только наличие полей, но и ЗАПОЛНЯЕМОСТЬ — модель на пустых полях бесполезна.
Плюс проверяем тождество «Margin = сумма сделки − Cost (rub)» на живых данных.
"""
from __future__ import annotations

import os
import re
from collections import Counter

import requests

KEYS = ("доставк", "логист", "таможн", "пошлин", "ндс", "vat", "сбор", "агент", "комисс",
        "страхов", "сертиф", "себестоим", "cost", "margin", "маржа", "наценк", "коэф",
        "транспорт", "фрахт", "брокер", "склад", "услуг", "затрат", "расход", "price", "цена")
DEAL_COST = "UF_CRM_1710446284794"    # Cost (rub)
DEAL_OFFER = "UF_CRM_1704981063967"   # Offer sum and currency of selected supplier
DEAL_MARGIN = "UF_CRM_1704981084196"  # Margin


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


def num(v):
    """Из строкового поля вытащить число: '1 234,56 EUR' → 1234.56."""
    s = str(v or "").replace("\xa0", " ").strip()
    if not s:
        return None
    s = s.split("|")[0]
    m = re.search(r"-?\d[\d  ]*(?:[.,]\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def main() -> int:
    print("=== 1. Поля-кандидаты в ЗАКАЗАХ СП-172 ===")
    f172 = bx("crm.item.fields", {"entityTypeId": 172}).get("result", {}).get("fields", {})
    cand172 = {k: (v.get("title") or "") for k, v in f172.items()
               if any(w in (v.get("title") or "").lower() for w in KEYS)}
    for k, t in cand172.items():
        print(f"  {k} [{f172[k].get('type')}]: «{t[:66]}»")

    print("\n=== 2. Поля-кандидаты в СДЕЛКАХ ===")
    fd = bx("crm.deal.fields", {}).get("result", {}) or {}
    candd = {k: (fd[k].get("formLabel") or fd[k].get("title") or k) for k in fd
             if any(w in str(fd[k].get("formLabel") or fd[k].get("title") or "").lower() for w in KEYS)}
    for k, t in candd.items():
        print(f"  {k} [{fd[k].get('type')}]: «{str(t)[:66]}»")

    print("\n=== 3. ЗАПОЛНЯЕМОСТЬ полей заказов (живые заказы) ===")
    sel = ["id", "title", "stageId", "opportunity", "currencyId", "parentId2"] + list(cand172)
    orders = bx_all("crm.item.list", {"entityTypeId": 172, "select": sel})
    live = [o for o in orders if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    print(f"  всего заказов {len(orders)}, живых {len(live)}")
    for k, t in cand172.items():
        n_all = sum(1 for o in orders if o.get(k) not in (None, "", 0, "0"))
        n_live = sum(1 for o in live if o.get(k) not in (None, "", 0, "0"))
        if n_all:
            ex = [str(o.get(k))[:26] for o in orders if o.get(k) not in (None, "", 0, "0")][:3]
            print(f"  {k} «{t[:40]}»: всего {n_all}, живых {n_live} · примеры: {ex}")

    print("\n=== 4. ЗАПОЛНЯЕМОСТЬ полей сделок (воронка реализации, кат.0) ===")
    dsel = ["ID", "TITLE", "OPPORTUNITY", "CURRENCY_ID", "STAGE_SEMANTIC_ID",
            DEAL_COST, DEAL_OFFER, DEAL_MARGIN] + list(candd)
    dsel = list(dict.fromkeys(dsel))
    deals = bx_all("crm.deal.list", {"filter": {"CATEGORY_ID": 0}, "select": dsel})
    print(f"  сделок кат.0: {len(deals)}")
    for k in candd:
        n = sum(1 for d in deals if d.get(k) not in (None, "", 0, "0", "0|RUB", "0.00|RUB"))
        if n:
            ex = [str(d.get(k))[:24] for d in deals if d.get(k) not in (None, "", 0, "0", "0|RUB", "0.00|RUB")][:3]
            print(f"  {k} «{str(candd[k])[:40]}»: заполнено {n} · примеры: {ex}")

    print("\n=== 5. Сходится ли «Margin = сумма сделки − Cost (rub)»? ===")
    cur = bx("crm.currency.list", {}).get("result") or []
    rate = {c.get("CURRENCY"): float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1) for c in cur}
    def eur(v, c): return float(v or 0) * rate.get(c, 1.0)
    def money(s):
        s = str(s or "")
        if "|" not in s:
            return None, None
        a, c = s.split("|", 1)
        try:
            return float(a or 0), c
        except ValueError:
            return None, None
    ok = bad = nodata = 0
    shown = 0
    for d in deals:
        cost, ccur = money(d.get(DEAL_COST))
        marg, mcur = money(d.get(DEAL_MARGIN))
        if not cost or marg is None:
            nodata += 1
            continue
        sale = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        c_e, m_e = eur(cost, ccur), eur(marg, mcur)
        if sale and abs((sale - c_e) - m_e) / max(abs(sale), 1) < 0.02:
            ok += 1
        else:
            bad += 1
            if shown < 8:
                shown += 1
                print(f"    #{d['ID']} продажа {sale:,.0f}€ − cost {c_e:,.0f}€ = {sale-c_e:,.0f}€, "
                      f"а в поле Margin {m_e:,.0f}€ · {str(d.get('TITLE'))[:34]}")
    print(f"  тождество выполняется: {ok} · не сходится: {bad} · нет данных: {nodata}")

    print("\n=== 6. Cost (rub) против Σ заказов поставщикам — это одно и то же? ===")
    by_deal = {}
    for o in orders:
        did = str(o.get("parentId2") or "")
        if did:
            by_deal.setdefault(did, []).append(o)
    same = higher = lower = 0
    ratios = []
    shown = 0
    for d in deals:
        did = str(d["ID"])
        cost, ccur = money(d.get(DEAL_COST))
        ords = by_deal.get(did) or []
        if not cost or not ords:
            continue
        so = sum(eur(o.get("opportunity"), o.get("currencyId")) for o in ords)
        c_e = eur(cost, ccur)
        if not so or not c_e:
            continue
        r = c_e / so
        ratios.append(r)
        if abs(r - 1) < 0.03:
            same += 1
        elif r > 1:
            higher += 1
            if shown < 8:
                shown += 1
                print(f"    #{d['ID']} Cost {c_e:,.0f}€ ВЫШЕ Σ заказов {so:,.0f}€ (×{r:.2f}) · {str(d.get('TITLE'))[:32]}")
        else:
            lower += 1
    ratios.sort()
    med = ratios[len(ratios) // 2] if ratios else 0
    print(f"  сопоставимых сделок: {len(ratios)} · совпадает ±3%: {same} · "
          f"Cost выше: {higher} · Cost ниже: {lower} · медиана Cost/Σзаказов: {med:.3f}")

    print("\n=== 7. Товарные позиции в сделках (crm.item.productrow) — есть ли строки затрат? ===")
    sample = [str(d["ID"]) for d in deals[:6]]
    for did in sample:
        rows = bx("crm.item.productrow.list", {"filter": {"=ownerType": "D", "=ownerId": did}}).get("result", {})
        items = (rows or {}).get("productRows") or []
        if items:
            print(f"    сделка #{did}: {len(items)} строк · " +
                  ", ".join(f"{str(i.get('productName'))[:24]}={i.get('price')}" for i in items[:3]))
    print("\n✓ зонд v14 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
