"""CI-зонд v17: бюджеты сделок — сами ссылки и их доступность.

UF_CRM_1577100780 «Transaction budget + product conditions» заполнен у 833 сделок кат.0
и ведёт на Google Sheets. Достаём образцы ссылок и проверяем, открываются ли они
без авторизации (export?format=csv). Заодно — покрытие по стадиям.
"""
from __future__ import annotations

import os
import re
from collections import Counter

import requests

BUD = "UF_CRM_1577100780"


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


def regno(t) -> int:
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(t or ""))
    return int(m.group(1)) if m else 0


def main() -> int:
    deals = bx_all("crm.deal.list", {"filter": {"CATEGORY_ID": 0},
                                     "select": ["ID", "TITLE", "STAGE_ID", "STAGE_SEMANTIC_ID",
                                                "OPPORTUNITY", "CURRENCY_ID", BUD]})
    have = [d for d in deals if str(d.get(BUD) or "").startswith("http")]
    print(f"=== 1. Покрытие: {len(have)} из {len(deals)} сделок кат.0 имеют ссылку на бюджет ===")
    live = [d for d in deals if (d.get("STAGE_SEMANTIC_ID") or "") not in ("S", "F")]
    live_have = [d for d in live if str(d.get(BUD) or "").startswith("http")]
    print(f"  среди открытых: {len(live_have)} из {len(live)}")

    print("\n=== 2. Куда ведут ссылки (домены) ===")
    dom = Counter(re.sub(r"^https?://([^/]+)/.*$", r"\1", str(d.get(BUD))) for d in have)
    for k, v in dom.most_common():
        print(f"  {v:>4} — {k}")

    print("\n=== 3. Образцы: свежие сделки с бюджетом ===")
    have.sort(key=lambda d: -regno(d.get("TITLE")))
    samples = have[:12]
    for d in samples:
        print(f"  №{regno(d.get('TITLE')):>3} · сделка #{d['ID']} · {str(d.get('TITLE'))[:40]}")
        print(f"      {str(d.get(BUD))[:150]}")

    print("\n=== 4. Открываются ли без авторизации? ===")
    for d in samples[:6]:
        url = str(d.get(BUD))
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
        if not m:
            print(f"  #{d['ID']}: не Google Sheets → {url[:70]}")
            continue
        sid = m.group(1)
        exp = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"
        try:
            r = requests.get(exp, timeout=30, allow_redirects=True)
            ct = r.headers.get("content-type", "")[:40]
            body = r.text[:120].replace("\n", " ") if "text" in ct or "csv" in ct else ""
            print(f"  #{d['ID']}: {r.status_code} · {ct} · {len(r.content)}Б · {body}")
        except Exception as e:
            print(f"  #{d['ID']}: EXC {type(e).__name__}")

    print("\n✓ зонд v17 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
