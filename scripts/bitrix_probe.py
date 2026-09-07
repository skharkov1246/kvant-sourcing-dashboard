"""Зонд v26: покрывает ли правило «вся почта @kvantpro.com» всех сотрудников.

ПЕРСОНАЛЬНЫЕ ДАННЫЕ НЕ ПЕЧАТАЮТСЯ: репозиторий публичный, журналы сборок открыты.
Выводится только сводка — сколько активных сотрудников, как распределены домены
их почты, у скольких почты нет. Сами адреса не выводятся ни в каком виде.
"""
from __future__ import annotations

import os
from collections import Counter

import requests


def bx_all(method: str, params: dict) -> list:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    out, start = [], 0
    while True:
        j = {}
        for _ in range(4):
            try:
                r = requests.post(f"{base}/{method}.json", json={**params, "start": start}, timeout=90)
                r.raise_for_status()
                j = r.json()
                break
            except Exception:
                continue
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j:
            return out
        start = j["next"]


def main() -> int:
    users = bx_all("user.get", {"FILTER": {"ACTIVE": "Y"}})
    print(f"=== Активных сотрудников в портале: {len(users)} ===\n")

    doms, no_mail, bots = Counter(), 0, 0
    for u in users:
        if str(u.get("USER_TYPE") or "") not in ("employee", ""):
            bots += 1
            continue
        e = str(u.get("EMAIL") or "").strip().lower()
        if "@" not in e:
            no_mail += 1
            continue
        doms[e.rsplit("@", 1)[1]] += 1

    total = sum(doms.values())
    print(f"  с почтой: {total} · без почты: {no_mail} · служебных/внешних учёток: {bots}\n")
    print("  распределение по доменам (адреса не выводятся):")
    for d, n in doms.most_common():
        share = n / max(total, 1) * 100
        print(f"    {d:34s} {n:>4}  {share:5.1f}%")

    main_dom = doms.most_common(1)[0][0] if doms else None
    if main_dom:
        covered = doms[main_dom]
        print(f"\n  правило «Emails ending in @{main_dom}» покроет {covered} из {len(users)} сотрудников")
        rest = total - covered
        print(f"  останется завести вручную: {rest} (почта на прочих доменах) + {no_mail} без почты в портале")

    print("\n✓ зонд v26 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
