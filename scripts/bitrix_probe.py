"""Зонд v25: перечень активных сотрудников с корпоративной почтой — для именных доступов."""
from __future__ import annotations
import os
from collections import Counter
import requests


def bx(method, params=None):
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    for _ in range(4):
        try:
            r = requests.post(f"{base}/{method}.json", json=params or {}, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return {}


def bx_all(method, params):
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        out += (res.get("items") if isinstance(res, dict) and "items" in res else res) or []
        if "next" not in j:
            return out
        start = j["next"]


def main():
    deps = {str(d["ID"]): str(d.get("NAME") or "") for d in bx_all("department.get", {})}
    users = bx_all("user.get", {"FILTER": {"ACTIVE": "Y"}})
    rows = []
    for u in users:
        name = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
        email = (u.get("EMAIL") or "").strip().lower()
        dd = u.get("UF_DEPARTMENT") or []
        dep = deps.get(str(dd[0]), "") if dd else ""
        kind = "служебная" if "служеб" in name.lower() or "аккаунт" in name.lower() else "сотрудник"
        rows.append((email, name, dep, str(u.get("ID")), kind))
    rows.sort(key=lambda r: (r[4], r[2], r[1]))
    dom = Counter(e.split("@")[-1] for e, *_ in rows if "@" in e)
    print(f"активных учётных записей: {len(rows)} · с почтой: {sum(1 for r in rows if '@' in r[0])}")
    print(f"домены почты: {dict(dom.most_common(8))}")
    print("\n=== СПИСОК (email | имя | подразделение | id | тип) ===")
    for e, n, d, i, k in rows:
        print(f"{e or '—'} | {n} | {d} | {i} | {k}")
    print("\n✓ зонд v25 завершён")


if __name__ == "__main__":
    main()
