#!/usr/bin/env python3
"""Снимок сделок Bitrix за период → JSON для base/build_db.py.

Что забирается: карточки сделок, справочники (воронки, стадии, пользователи,
курсы валют), компании этих сделок и полная история переходов по стадиям.
История нужна отдельно: победа в портале — это ПЕРЕЕЗД карточки в воронку
«Реализация», а не стадия WON, и увидеть её можно только по истории.

Постранично по возрастанию ID: по смещению портал отдаёт лишь первые тысячи
записей. Временную блокировку метода («operation time limit») пережидаем — это
защита портала, а не ошибка запроса.

    python base/export_bitrix.py --since 2023-01-01 --out export2023.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

from parse_archives import webhook

SELECT = ["ID", "TITLE", "TYPE_ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID",
          "DATE_CREATE", "DATE_MODIFY", "CLOSEDATE", "BEGINDATE", "CLOSED", "OPPORTUNITY",
          "CURRENCY_ID", "OPPORTUNITY_ACCOUNT", "ASSIGNED_BY_ID", "CREATED_BY_ID",
          "COMPANY_ID", "CONTACT_ID", "SOURCE_ID", "SOURCE_DESCRIPTION", "COMMENTS",
          "UTM_SOURCE", "IS_RETURN_CUSTOMER", "PROBABILITY", "LEAD_ID", "ADDITIONAL_INFO"]
RETRY_ERRORS = {"QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT",
                "INTERNAL_SERVER_ERROR", "OVERLOAD_LIMIT"}


def bx(sess: requests.Session, base: str, method: str, payload: dict | None = None,
       tries: int = 6) -> dict | None:
    for i in range(tries):
        try:
            r = sess.post(base + method + ".json", json=payload or {}, timeout=180)
            d = r.json()
        except Exception:
            time.sleep(1.5 * (i + 1))
            continue
        if isinstance(d, dict) and d.get("error"):
            if d["error"] in RETRY_ERRORS or "operation time limit" in (d.get("error_description") or ""):
                time.sleep(min(15 * (i + 1), 180))
                continue
            print(f"ОШИБКА {method}: {d['error']} {str(d.get('error_description'))[:120]}",
                  file=sys.stderr)
            return None
        time.sleep(0.32)
        return d
    return None


def run(since: str, out_path: str) -> dict:
    sess = requests.Session()
    base = webhook().rstrip("/") + "/"

    print(f"1/5 сделки с {since}…", flush=True)
    deals, last = [], 0
    while True:
        d = bx(sess, base, "crm.deal.list", {"order": {"ID": "ASC"}, "select": SELECT,
                                             "filter": {">ID": last, ">=DATE_CREATE": since},
                                             "start": -1})
        chunk = (d or {}).get("result") or []
        if not chunk:
            break
        deals += chunk
        last = int(chunk[-1]["ID"])
        if len(deals) % 1000 < len(chunk):
            print(f"  {len(deals)}", flush=True)
    print(f"  сделок: {len(deals)}", flush=True)

    print("2/5 справочники…", flush=True)
    cats = {"0": "Общая"}
    for c in ((bx(sess, base, "crm.dealcategory.list",
                  {"select": ["ID", "NAME"], "start": -1}) or {}).get("result") or []):
        cats[str(c["ID"])] = c["NAME"]
    stages = {}
    for cid in cats:
        ent = "DEAL_STAGE" if cid == "0" else f"DEAL_STAGE_{cid}"
        for s in ((bx(sess, base, "crm.status.list",
                      {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"},
                       "start": -1}) or {}).get("result") or []):
            stages[s["STATUS_ID"]] = {"name": s.get("NAME"), "sem": s.get("SEMANTICS") or "P",
                                      "sort": int(s.get("SORT") or 0), "cat": cid}
    users, start = {}, 0
    while True:
        d = bx(sess, base, "user.get", {"start": start})
        for u in ((d or {}).get("result") or []):
            users[str(u["ID"])] = " ".join(
                x for x in [u.get("LAST_NAME"), u.get("NAME")] if x).strip() or f"user#{u['ID']}"
        if not (d or {}).get("next"):
            break
        start = d["next"]
    currency = {}
    for c in ((bx(sess, base, "crm.currency.list") or {}).get("result") or []):
        currency[c["CURRENCY"]] = float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1)
    print(f"  воронок {len(cats)}, стадий {len(stages)}, сотрудников {len(users)}, "
          f"валют {len(currency)}", flush=True)

    print("3/5 компании…", flush=True)
    cids = sorted({int(d["COMPANY_ID"]) for d in deals
                   if d.get("COMPANY_ID") and str(d["COMPANY_ID"]) != "0"})
    companies = {}
    for i in range(0, len(cids), 50):
        for c in ((bx(sess, base, "crm.company.list",
                      {"filter": {"@ID": cids[i:i + 50]},
                       "select": ["ID", "TITLE", "INDUSTRY"], "start": -1}) or {}).get("result") or []):
            companies[str(c["ID"])] = {"title": c.get("TITLE"), "industry": c.get("INDUSTRY")}
    print(f"  компаний: {len(companies)}", flush=True)

    print("4/5 история стадий…", flush=True)
    history, start, pages = defaultdict(list), 0, 0
    while True:
        d = bx(sess, base, "crm.stagehistory.list",
               {"entityTypeId": 2, "filter": {">=CREATED_TIME": since + "T00:00:00"},
                "select": ["OWNER_ID", "CATEGORY_ID", "STAGE_ID", "CREATED_TIME", "TYPE_ID"],
                "order": {"CREATED_TIME": "ASC"}, "start": start})
        res = (d or {}).get("result") or {}
        items = (res.get("items") if isinstance(res, dict) else res) or []
        for x in items:
            history[str(x.get("OWNER_ID"))].append(
                [str(x.get("CATEGORY_ID")), str(x.get("STAGE_ID")), str(x.get("CREATED_TIME"))[:19]])
        pages += 1
        if pages % 20 == 0:
            print(f"  страниц {pages}, сделок в истории {len(history)}", flush=True)
        if not (d or {}).get("next") or not items:
            break
        start = d["next"]
    print(f"  история: {len(history)} сделок, {pages} страниц", flush=True)

    print("5/5 запись…", flush=True)
    Path(out_path).write_text(json.dumps(
        {"since": since, "deals": deals, "cats": cats, "stages": stages, "users": users,
         "currency": currency, "companies": companies, "products": {}, "history": history},
        ensure_ascii=False), encoding="utf-8")
    print(f"готово → {out_path}", flush=True)
    return {"deals": len(deals), "history": len(history), "companies": len(companies)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-09-01", help="дата создания сделки, от которой брать")
    ap.add_argument("--out", default="export.json")
    a = ap.parse_args()
    run(a.since, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
