"""Зонд v24: чем на самом деле измеряется нагрузка сорсера.

Отделяем машинную работу от ручной:
  кто создаёт карточки (человек или служебная учётная запись),
  пачками ли они создаются (group_id и всплески по времени),
  сколько сделок и товарных строк приходится на сотрудника,
  какие шаги остаются ручными — это и есть незакрытая автоматизация.
"""
from __future__ import annotations

import os
import statistics as st
from collections import Counter, defaultdict

import requests

ET, CAT = 166, 24
SINCE = "2026-04-01"
F_GROUP = "ufCrm18_1750155997248"     # group_id
F_AI = "ufCrm18_1703712609095"        # AI model
F_TEXT = "ufCrm18_1706636290339"      # Deal_text
F_RESP = "ufCrm18_1709056431446"      # Response received


def bx(method: str, params: dict | None = None) -> dict:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    for _ in range(4):
        try:
            r = requests.post(f"{base}/{method}.json", json=params or {}, timeout=90)
            r.raise_for_status()
            j = r.json()
            if isinstance(j, dict) and j.get("error") in ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"):
                continue
            return j
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
    deps = {str(d["ID"]): str(d.get("NAME") or "") for d in bx_all("department.get", {})}
    uname, udep = {}, {}
    for u in bx_all("user.get", {}):
        uid = str(u["ID"])
        uname[uid] = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip() or uid
        dd = u.get("UF_DEPARTMENT") or []
        udep[uid] = deps.get(str(dd[0]), "—") if dd else "—"

    print(f"=== 1. Запросы с {SINCE} ===")
    rows = bx_all("crm.item.list", {"entityTypeId": ET,
        "filter": {"categoryId": CAT, ">=createdTime": SINCE},
        "select": ["id", "createdTime", "createdBy", "assignedById", "parentId2",
                   F_GROUP, F_AI]})
    print(f"  карточек: {len(rows)}")

    print("\n=== 2. КТО СОЗДАЁТ КАРТОЧКИ ===")
    cb = Counter(str(r.get("createdBy") or "—") for r in rows)
    for uid, n in cb.most_common(12):
        print(f"  {uname.get(uid, uid)[:34]:36s} {udep.get(uid,'—')[:26]:28s} {n:>6}  {n/len(rows)*100:5.1f}%")
    mism = sum(1 for r in rows if str(r.get("createdBy")) != str(r.get("assignedById")))
    print(f"  создатель ≠ ответственный: {mism} из {len(rows)} ({mism/max(len(rows),1)*100:.0f}%)")

    print("\n=== 3. ПАЧКИ: сколько карточек порождает одно действие ===")
    grp = defaultdict(int)
    nogrp = 0
    for r in rows:
        g = r.get(F_GROUP)
        if g in (None, "", 0):
            nogrp += 1
        else:
            grp[str(g)] += 1
    if grp:
        sizes = sorted(grp.values())
        print(f"  групп (group_id): {len(grp)} · карточек в группах: {sum(sizes)} · без группы: {nogrp}")
        print(f"  карточек в группе: медиана {st.median(sizes):.0f} · среднее {sum(sizes)/len(sizes):.1f} · макс {max(sizes)}")
        dist = Counter(min(v, 10) for v in sizes)
        print("  распределение: " + " · ".join(f"{k if k<10 else '10+'}:{v}" for k, v in sorted(dist.items())))
    # всплески: карточки одного создателя в одну минуту
    burst = defaultdict(int)
    for r in rows:
        burst[(str(r.get("createdBy")), str(r.get("createdTime"))[:16])] += 1
    bs = sorted(burst.values())
    big = sum(v for v in bs if v >= 5)
    print(f"  создано в одну минуту одним автором: медиана {st.median(bs):.0f} · макс {max(bs)}")
    print(f"  доля карточек, созданных пачками по 5+ за минуту: {big}/{len(rows)} = {big/max(len(rows),1)*100:.0f}%")

    print("\n=== 4. ПРИЗНАКИ УЧАСТИЯ ИИ ===")
    ai = Counter(str(r.get(F_AI) or "—")[:40] for r in rows)
    for v, n in ai.most_common(8):
        print(f"  «{v}»: {n} ({n/len(rows)*100:.0f}%)")

    print("\n=== 5. НАГРУЗКА В СДЕЛКАХ: сколько сделок на сотрудника в месяц ===")
    dm = defaultdict(lambda: defaultdict(set))     # месяц → сотрудник → сделки
    cm = defaultdict(lambda: defaultdict(int))     # месяц → сотрудник → карточки
    gm = defaultdict(lambda: defaultdict(set))     # месяц → сотрудник → группы
    for r in rows:
        m = str(r.get("createdTime") or "")[:7]
        p = str(r.get("assignedById") or "")
        if not m or not p:
            continue
        cm[m][p] += 1
        if r.get("parentId2"):
            dm[m][p].add(str(r["parentId2"]))
        if r.get(F_GROUP):
            gm[m][p].add(str(r[F_GROUP]))
    print(f"  {'месяц':8s} {'чел':>4} {'сделок':>7} {'сд/чел':>7} {'сд.мед':>7} {'групп':>7} {'гр/чел':>7} {'карт/чел':>9} {'карт/сделку':>12}")
    for m in sorted(cm):
        ppl = sorted(cm[m])
        n = len(ppl)
        dv = [len(dm[m].get(p, ())) for p in ppl]
        gv = [len(gm[m].get(p, ())) for p in ppl]
        cv = [cm[m][p] for p in ppl]
        D, G, C = sum(dv), sum(gv), sum(cv)
        print(f"  {m:8s} {n:>4} {D:>7} {D/n:>7.1f} {st.median(dv):>7.0f} {G:>7} {G/n:>7.1f} {C/n:>9.1f} {C/max(D,1):>12.1f}")

    print("\n  --- то же по Отделу поиска поставщиков ---")
    print(f"  {'месяц':8s} {'чел':>4} {'сделок':>7} {'сд/чел':>7} {'групп':>7} {'гр/чел':>7} {'карт/чел':>9}")
    for m in sorted(cm):
        ppl = [p for p in cm[m] if udep.get(p, "") == "Отдел поиска поставщиков"]
        if not ppl:
            continue
        n = len(ppl)
        D = len(set().union(*[dm[m].get(p, set()) for p in ppl])) if ppl else 0
        G = sum(len(gm[m].get(p, ())) for p in ppl)
        C = sum(cm[m][p] for p in ppl)
        print(f"  {m:8s} {n:>4} {D:>7} {D/n:>7.1f} {G:>7} {G/n:>7.1f} {C/n:>9.1f}")

    print("\n=== 6. ТОВАРНЫЕ СТРОКИ: сколько позиций в сделке (выборка 200) ===")
    dids = sorted({str(r["parentId2"]) for r in rows if r.get("parentId2")})
    print(f"  сделок с запросами с апреля: {len(dids)}")
    sample = dids[-200:]
    cnt, zero = [], 0
    for d in sample:
        j = bx("crm.item.productrow.list", {"filter": {"=ownerType": "D", "=ownerId": int(d)}})
        pr = ((j or {}).get("result") or {}).get("productRows")
        if pr is None:
            continue
        cnt.append(len(pr))
        if not pr:
            zero += 1
    if cnt:
        cs = sorted(cnt)
        print(f"  разобрано сделок: {len(cs)} · без позиций: {zero} ({zero/len(cs)*100:.0f}%)")
        print(f"  позиций в сделке: медиана {st.median(cs):.0f} · среднее {sum(cs)/len(cs):.1f} · "
              f"p75 {cs[3*len(cs)//4]} · макс {max(cs)}")
    else:
        print("  товарные строки через API недоступны")

    print("\n=== 7. ЧТО ОСТАЁТСЯ РУЧНЫМ: длина технического текста запроса (выборка 200) ===")
    ids = [r["id"] for r in rows[-200:]]
    lens, resp = [], 0
    got = 0
    for i in range(0, len(ids), 50):
        for it in bx_all("crm.item.list", {"entityTypeId": ET,
                "filter": {"categoryId": CAT, "@id": ids[i:i+50]},
                "select": ["id", F_TEXT, F_RESP]}):
            got += 1
            t = it.get(F_TEXT)
            if t:
                lens.append(len(str(t)))
            if it.get(F_RESP) not in (None, "", 0, "0"):
                resp += 1
    if lens:
        ls = sorted(lens)
        print(f"  карточек: {got} · с текстом: {len(ls)} · длина: медиана {st.median(ls):.0f} симв. · макс {max(ls)}")
    print(f"  отметка «ответ получен» проставлена: {resp} из {got}")

    print("\n✓ зонд v24 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
