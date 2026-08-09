"""CI-зонд v18: материал для проектирования оргструктуры коммерческого блока.

Собираем факты, а не мнения: дерево отделов, люди, деньги по отделам и воронкам,
пересечения отделов на одних клиентах, заполняемость ролевых полей.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import Counter, defaultdict

import requests

YTD = "2026-01-01"
ROLES = {
    "UF_CRM_1736926032": "KAM", "UF_CRM_1740390857": "КАМ(2)",
    "UF_CRM_1779187335": "Сорсер", "UF_CRM_1779187425": "Product leader",
    "UF_CRM_1776169420": "Head of sourcing", "UF_CRM_1781863844": "Тендерный специалист",
    "UF_CRM_1781863815": "Рук. тендерного", "UF_CRM_1781858627": "Ответственный за реализацию",
    "UF_CRM_1715604558": "Сопровождение сделки (ОСС)",
}


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
    print("=== 1. ДЕРЕВО ОТДЕЛОВ ===")
    deps = bx_all("department.get", {})
    byid = {str(d["ID"]): d for d in deps}
    kids = defaultdict(list)
    for d in deps:
        kids[str(d.get("PARENT") or "")].append(str(d["ID"]))
    users = bx_all("user.get", {"FILTER": {"ACTIVE": "Y"}})
    upd = defaultdict(list)
    uname, udept = {}, {}
    for u in users:
        uid = str(u["ID"])
        uname[uid] = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
        for d in (u.get("UF_DEPARTMENT") or []):
            upd[str(d)].append(uid)
            udept[uid] = str(d)

    def walk(did, depth=0):
        d = byid.get(did)
        if not d:
            return
        n = len(upd.get(did, []))
        head = uname.get(str(d.get("UF_HEAD") or ""), "")
        print(f"  {'  '*depth}{did:>5} · {str(d.get('NAME'))[:44]:46s} люди:{n:>3}"
              + (f" · рук. {head}" if head else ""))
        for k in sorted(kids.get(did, []), key=lambda x: int(x)):
            walk(k, depth + 1)

    roots = [str(d["ID"]) for d in deps if not d.get("PARENT")]
    for r in roots:
        walk(r)
    print(f"  всего отделов: {len(deps)} · активных сотрудников: {len(users)}")

    print("\n=== 2. ВОРОНКИ СДЕЛОК ===")
    cats = bx("crm.category.list", {"entityTypeId": 2}).get("result", {}).get("categories", []) or []
    for c in cats:
        print(f"  cat {c.get('id')}: «{c.get('name')}»")

    print(f"\n=== 3. СДЕЛКИ С {YTD}: деньги по отделам ===")
    deals = bx_all("crm.deal.list", {
        "filter": {">=DATE_CREATE": YTD},
        "select": ["ID", "TITLE", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY",
                   "CURRENCY_ID", "ASSIGNED_BY_ID", "COMPANY_ID"] + list(ROLES)})
    cur = bx("crm.currency.list", {}).get("result") or []
    rate = {c.get("CURRENCY"): float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1) for c in cur}
    def eur(v, c): return float(v or 0) * rate.get(c, 1.0)
    print(f"  сделок с {YTD}: {len(deals)}")
    print(f"  по воронкам: {dict(Counter(str(d.get('CATEGORY_ID')) for d in deals).most_common())}")

    agg = defaultdict(lambda: {"n": 0, "sum": 0.0, "won": 0, "wonsum": 0.0, "people": set()})
    for d in deals:
        uid = str(d.get("ASSIGNED_BY_ID") or "")
        did = udept.get(uid) or "—"
        a = agg[did]
        a["n"] += 1
        a["sum"] += eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        a["people"].add(uid)
        if d.get("STAGE_SEMANTIC_ID") == "S":
            a["won"] += 1
            a["wonsum"] += eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
    print(f"\n  {'отдел':>6} {'название':44s} {'чел':>4} {'сделок':>7} {'Σ €':>12} {'выиграно':>9} {'Σ выигр €':>12}")
    for did, a in sorted(agg.items(), key=lambda kv: -kv[1]["sum"])[:25]:
        nm = str(byid.get(did, {}).get("NAME") or ("без отдела" if did == "—" else did))[:44]
        print(f"  {did:>6} {nm:44s} {len(a['people']):>4} {a['n']:>7} {a['sum']:>12,.0f} "
              f"{a['won']:>9} {a['wonsum']:>12,.0f}")

    print("\n=== 4. ПЕРЕСЕЧЕНИЯ: сколько отделов ведут одного клиента ===")
    comp = bx_all("crm.company.list", {"select": ["ID", "TITLE"]})
    cname = {str(c["ID"]): c.get("TITLE") or "" for c in comp}
    HOLD = [(r"лукойл", "Лукойл"), (r"норильск|кольская|быстринск", "Норникель"),
            (r"таиф", "ТАИФ"), (r"сибур|ставролен|запсиб|казаньоргсинтез|нижнекамскнефтехим", "Сибур"),
            (r"\bрн-|роснефт|башнефт", "Роснефть"), (r"газпром", "Газпром"),
            (r"новат[эе]к|сахалинск|ямал\s*спг|арктик\s*спг", "Новатэк/Сахалин")]
    def hold(cid):
        t = (cname.get(str(cid)) or "").lower()
        for rx, nm in HOLD:
            if re.search(rx, t):
                return nm
        return None
    hdep = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for d in deals:
        h = hold(d.get("COMPANY_ID"))
        if not h:
            continue
        did = udept.get(str(d.get("ASSIGNED_BY_ID") or "")) or "—"
        cell = hdep[h][did]
        cell[0] += 1
        cell[1] += eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
    for h, dd in sorted(hdep.items(), key=lambda kv: -sum(v[1] for v in kv[1].values())):
        tot = sum(v[1] for v in dd.values())
        print(f"\n  {h}: Σ {tot:,.0f} € · отделов-участников: {len(dd)}")
        for did, v in sorted(dd.items(), key=lambda kv: -kv[1][1])[:6]:
            nm = str(byid.get(did, {}).get("NAME") or ("без отдела" if did == "—" else did))[:40]
            print(f"      {did:>5} {nm:42s} {v[0]:>4} сделок · {v[1]:>12,.0f} €")

    print("\n=== 5. РОЛЕВЫЕ ПОЛЯ: заполняемость на сделках этого года ===")
    for f, nm in ROLES.items():
        n = sum(1 for d in deals if d.get(f) not in (None, "", 0, "0", []))
        print(f"  {nm:32s} {n:>5} из {len(deals)}")

    print("\n✓ зонд v18 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
