"""CI-зонд v12: кто такая Яна Мокшина и почему она попадает в отчёт.

Ищем пользователя по фамилии, смотрим его статус/отдел, и во всех источниках дашборда
считаем, где он фигурирует: RFQ СП-166, сделки, заказы СП-172, поля сорсера/КАМ/ОСС.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from collections import Counter

import requests

NEEDLE = ("мокшин", "mokshin")


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
    print("=== 1. Ищем пользователя ===")
    users = bx_all("user.get", {})
    hits = [u for u in users
            if any(n in f"{u.get('NAME','')} {u.get('LAST_NAME','')} {u.get('EMAIL','')}".lower()
                   for n in NEEDLE)]
    if not hits:
        print("  пользователь не найден")
        return 0
    uids = []
    for u in hits:
        uid = str(u.get("ID"))
        uids.append(uid)
        print(f"  ID={uid} · {u.get('NAME')} {u.get('LAST_NAME')} · {u.get('EMAIL')}")
        print(f"    активен: {u.get('ACTIVE')} · должность: {u.get('WORK_POSITION')} "
              f"· отделы: {u.get('UF_DEPARTMENT')} · уволен: {u.get('UF_USR_DISMISSED') or '—'}")
        print(f"    последний вход: {str(u.get('LAST_LOGIN'))[:19]} · создан: {str(u.get('DATE_REGISTER'))[:10]}")

    # отдел сорсинга (172) — по нему строится «блок A» в отчёте
    dept_ids = set()
    for d in bx_all("department.get", {}):
        pid, did = str(d.get("PARENT") or ""), str(d.get("ID"))
        if did == "172" or pid == "172":
            dept_ids.add(did)
            print(f"  отдел сорсинга: {did} «{d.get('NAME')}» (родитель {pid})")
    a_ids = set()
    for did in dept_ids or {"172"}:
        for u in bx_all("user.get", {"FILTER": {"UF_DEPARTMENT": int(did)}}):
            a_ids.add(str(u.get("ID")))
    print(f"  всего в отделе сорсинга: {len(a_ids)} чел · наш герой в нём: "
          f"{'ДА' if set(uids) & a_ids else 'нет'}")

    U = set(uids)
    today = dt.date.today().isoformat()

    print("\n=== 2. RFQ СП-166 «Запросы поставщикам» (основа вкладки «Сорсинг») ===")
    rfq = bx_all("crm.item.list", {"entityTypeId": 166,
                                   "select": ["id", "title", "assignedById", "createdTime", "stageId"]})
    mine = [r for r in rfq if str(r.get("assignedById")) in U]
    print(f"  всего RFQ: {len(rfq)} · её: {len(mine)}")
    if mine:
        ct = sorted(str(r.get("createdTime") or "")[:10] for r in mine)
        print(f"  период её RFQ: {ct[0]} … {ct[-1]}")
        print(f"  за последние 90 дней: "
              f"{sum(1 for c in ct if c >= (dt.date.today()-dt.timedelta(days=90)).isoformat())}")
        by_year = Counter(c[:4] for c in ct)
        print(f"  по годам: {dict(sorted(by_year.items()))}")
        for r in mine[-5:]:
            print(f"    #{r.get('id')} {str(r.get('title'))[:48]} · {str(r.get('createdTime'))[:10]}")

    print("\n=== 3. Сделки (ответственный) ===")
    deals = bx_all("crm.deal.list", {"filter": {"ASSIGNED_BY_ID": sorted(U)},
                                     "select": ["ID", "TITLE", "CATEGORY_ID", "STAGE_SEMANTIC_ID",
                                                "DATE_CREATE", "OPPORTUNITY", "CURRENCY_ID"]})
    print(f"  сделок, где она ответственная: {len(deals)}")
    print(f"    по воронкам: {dict(Counter(str(d.get('CATEGORY_ID')) for d in deals))}")
    print(f"    открытых (P): {sum(1 for d in deals if d.get('STAGE_SEMANTIC_ID') == 'P')}")
    for d in sorted(deals, key=lambda x: str(x.get("DATE_CREATE")))[-5:]:
        print(f"    #{d['ID']} cat={d.get('CATEGORY_ID')} sem={d.get('STAGE_SEMANTIC_ID')} "
              f"{str(d.get('TITLE'))[:44]} · {str(d.get('DATE_CREATE'))[:10]}")

    print("\n=== 4. Заказы СП-172 (ответственный / ОСС) ===")
    orders = bx_all("crm.item.list", {"entityTypeId": 172,
                                      "select": ["id", "title", "assignedById", "stageId",
                                                 "createdTime", "ufCrm20_1723236618"]})
    o_resp = [o for o in orders if str(o.get("assignedById")) in U]
    o_oss = [o for o in orders if str(o.get("ufCrm20_1723236618")) in U]
    print(f"  всего заказов: {len(orders)} · ответственная: {len(o_resp)} · ОСС: {len(o_oss)}")
    live_r = [o for o in o_resp if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    live_o = [o for o in o_oss if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
    print(f"  из них ЖИВЫХ: ответственная {len(live_r)} · ОСС {len(live_o)}")
    for o in (live_r + live_o)[:8]:
        print(f"    #{o.get('id')} {str(o.get('title'))[:44]} · {o.get('stageId')} · создан {str(o.get('createdTime'))[:10]}")

    print("\n=== 5. Поля сделок, где она может быть записана ===")
    fd = bx("crm.deal.fields", {}).get("result", {}) or {}
    userf = [k for k, v in fd.items() if v.get("type") in ("employee", "user")
             and k.startswith("UF_CRM_")]
    for k in userf:
        try:
            res = bx_all("crm.deal.list", {"filter": {k: sorted(U)}, "select": ["ID", "CATEGORY_ID"]})
        except Exception:
            continue
        if res:
            lbl = (fd.get(k, {}).get("formLabel") or fd.get(k, {}).get("title") or k)
            print(f"  {k} «{lbl}»: {len(res)} сделок "
                  f"(воронки: {dict(Counter(str(x.get('CATEGORY_ID')) for x in res))})")

    print("\n✓ зонд v12 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
