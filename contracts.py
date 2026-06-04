"""Список победных контрактов в реализации — одним списком (одна строка = один контракт/сделка).
Колонки: заказчик (клиент сделки) · № сделки · сумма сделки (продажа) · закупка (Σ заказов поставщикам СП-172) · маржа.

ВАЖНО про модель данных Bitrix:
  • СП-172 «Заказы» = заказы ПОСТАВЩИКАМ → их opportunity = ЗАКУПКА (в валюте поставщика), companyId = поставщик.
  • Продажа клиенту и сам клиент — на родительской СДЕЛКЕ (parentId2): deal.OPPORTUNITY / deal.COMPANY_ID.
  • Маржа = продажа − Σ закупок. Проигранные заказы (…:FAIL) исключаются.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from bitrix_client import BitrixClient

_SYM = {"EUR": "€", "USD": "$", "CNY": "¥", "RUB": "₽", "INR": "₹", "GBP": "£", "AED": "AED ", "JPY": "¥"}


def _regno(*titles) -> int:
    """Порядковый номер сделки в реализации — число В НАЧАЛЕ названия, за которым идёт точка:
    «871. …» у сделки, «871/1. …» у заказа. Требуем точку, чтобы не путать с клиентскими
    PO-кодами вида «2100007489 …» / «2077921/4 …» (там после цифр пробел/слэш, не точка).
    0 — если номера нет."""
    for t in titles:
        m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(t or ""))
        if m:
            return int(m.group(1))
    return 0


def _money(v: float) -> str:
    v = round(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}€{a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}€{a/1_000:.0f}K"
    return f"{sign}€{a}"


def _orig(v, cur: str) -> str:
    v = round(float(v or 0))
    s = f"{v:,}".replace(",", " ")
    sym = _SYM.get(cur)
    return f"{sym}{s}" if sym else f"{s} {cur or ''}".strip()


def compute(client: BitrixClient, *, as_of: dt.date | None = None) -> dict:
    today = as_of or dt.date.today()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    # 1. все непроигранные заказы поставщикам (закупки)
    orders = client.list_items(172, filter={}, select=["id", "title", "companyId", "parentId2",
                                                        "opportunity", "currencyId", "stageId", "createdTime"])
    orders = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    supl = client.companies_by_ids({str(o["companyId"]) for o in orders if o.get("companyId")})

    # 2. группировка заказов по сделке
    by_deal: dict[str, list] = defaultdict(list)
    orphan = []
    for o in orders:
        did = str(o.get("parentId2") or "")
        (by_deal[did] if did else orphan).append(o)

    # 3. родительские сделки (продажа + клиент)
    deals = client.deals_by_ids([d for d in by_deal if d],
                                select=["ID", "TITLE", "OPPORTUNITY", "CURRENCY_ID", "COMPANY_ID", "STAGE_SEMANTIC_ID"])
    clients = client.companies_by_ids({str(d.get("COMPANY_ID")) for d in deals.values() if d.get("COMPANY_ID")})

    rows = []
    for did, ords in by_deal.items():
        d = deals.get(did, {})
        sale = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        buy = sum(eur(o.get("opportunity"), o.get("currencyId")) for o in ords)
        margin = sale - buy
        suppliers = sorted({supl.get(str(o.get("companyId"))) for o in ords if o.get("companyId")} - {None})
        entered = min((str(o.get("createdTime") or "")[:10] for o in ords if o.get("createdTime")), default="")
        dtitle = d.get("TITLE") or (ords[0].get("title") if ords else "")
        seq = _regno(d.get("TITLE"), ords[0].get("title") if ords else "")
        rows.append({
            "deal": did,
            "seq": seq,
            "entered": entered,
            "customer": clients.get(str(d.get("COMPANY_ID"))) or "—",
            "title": (dtitle or f"Сделка #{did}")[:90],
            "saleEur": round(sale), "saleLbl": _money(sale), "saleOrig": _orig(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")),
            "saleCur": d.get("CURRENCY_ID") or "",
            "buyEur": round(buy), "buyLbl": _money(buy),
            "marginEur": round(margin), "marginLbl": _money(margin),
            "marginPct": round(margin / sale * 100) if sale else None,
            "norders": len(ords),
            "suppliers": ", ".join(suppliers)[:70],
            "done": all(str(o.get("stageId", "")).endswith(":SUCCESS") for o in ords),
            "hasSale": bool(d.get("OPPORTUNITY")),
        })
    # по умолчанию — по номеру реализации убыванием (новые сверху, для отслеживания появления новых)
    rows.sort(key=lambda r: -r["seq"])

    maxseq = max((r["seq"] for r in rows), default=0)
    sale_sum = sum(r["saleEur"] for r in rows)
    buy_sum = sum(r["buyEur"] for r in rows)
    margin_sum = sale_sum - buy_sum
    priced = [r for r in rows if r["hasSale"]]
    orphan_buy = sum(eur(o.get("opportunity"), o.get("currencyId")) for o in orphan)
    kpis = [
        ("Последний №", str(maxseq), "макс. номер в реализации", "ok"),
        ("Контрактов", str(len(rows)), "сделок с заказами поставщикам", ""),
        ("Σ продажи", _money(sale_sum), "сумма сделок (выручка), €", "ok"),
        ("Σ закупки", _money(buy_sum), "заказы поставщикам, €", "amber"),
        ("Σ маржа", _money(margin_sum), "продажа − закупка, €", "ok" if margin_sum >= 0 else "warn"),
        ("Ср. маржа", f"{round(margin_sum/sale_sum*100) if sale_sum else 0}%", "по сумме, валовая", ""),
    ]
    # --- агрегация по ПОСТАВЩИКАМ (оборот = Σ закупок СП-172) ---
    sup_agg: dict[str, dict] = defaultdict(lambda: {"buy": 0.0, "norders": 0, "deals": set(),
                                                    "clients": set(), "last": "", "curs": set()})
    for o in orders:
        sid = str(o.get("companyId") or "")
        if not sid:
            continue
        a = sup_agg[sid]
        a["buy"] += eur(o.get("opportunity"), o.get("currencyId"))
        a["norders"] += 1
        did = str(o.get("parentId2") or "")
        if did:
            a["deals"].add(did)
            cl = clients.get(str(deals.get(did, {}).get("COMPANY_ID")))
            if cl:
                a["clients"].add(cl)
        ct = str(o.get("createdTime") or "")[:10]
        if ct > a["last"]:
            a["last"] = ct
        if o.get("currencyId"):
            a["curs"].add(o.get("currencyId"))
    sup_total = sum(a["buy"] for a in sup_agg.values()) or 1.0
    sup_rows = sorted(({
        "id": sid, "name": supl.get(sid) or f"company #{sid}",
        "buy": round(a["buy"]), "buyLbl": _money(a["buy"]),
        "share": round(a["buy"] / sup_total * 100, 1),
        "norders": a["norders"], "ncontracts": len(a["deals"]), "nclients": len(a["clients"]),
        "clients": ", ".join(sorted(a["clients"]))[:80],
        "avg": _money(a["buy"] / a["norders"] if a["norders"] else 0),
        "last": a["last"], "curs": ", ".join(sorted(a["curs"])),
    } for sid, a in sup_agg.items()), key=lambda r: -r["buy"])
    top10 = sum(r["buy"] for r in sup_rows[:10])
    sup_kpis = [
        ("Поставщиков", str(len(sup_rows)), "с заказами (СП-172)", ""),
        ("Σ оборот", _money(sup_total), "сумма закупок, €", "amber"),
        ("Топ-10 доля", f"{round(top10 / sup_total * 100)}%", "концентрация закупок", "warn" if top10 / sup_total > 0.6 else ""),
        ("Ср. на поставщика", _money(sup_total / len(sup_rows) if sup_rows else 0), "€/поставщик", ""),
        ("Заказов всего", str(sum(r["norders"] for r in sup_rows)), "закупок", ""),
        ("Разовых", str(sum(1 for r in sup_rows if r["norders"] == 1)), "поставщиков с 1 заказом", "amber"),
    ]
    return {
        "label": f"на {today.strftime('%d.%m.%Y')}",
        "rows": rows,
        "kpis": [{"lbl": l, "val": v, "meta": m, "clz": c} for l, v, m, c in kpis],
        "orphan": {"n": len(orphan), "buy": _money(orphan_buy)},
        "suppliers": {
            "label": f"на {today.strftime('%d.%m.%Y')}",
            "rows": sup_rows,
            "kpis": [{"lbl": l, "val": v, "meta": m, "clz": c} for l, v, m, c in sup_kpis],
        },
    }
