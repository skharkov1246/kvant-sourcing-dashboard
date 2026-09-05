"""Зонд v23: динамика с апреля — отправленные запросы и полученные КП, в среднем на человека.

Считаем ПОТОК СОБЫТИЙ по истории стадий СП-166 (не текущие стадии):
  отправлено   = первый вход запроса в стадию «Request Sent» в этом месяце
  получено КП  = первый вход в любую стадию, означающую наличие цены
  ответ получен = шире: любая реакция поставщика, включая отказ котировать
Делим на число сотрудников, у которых в этом месяце была хотя бы одна отправка.
Приводим среднее и медиану — среднее искажают несколько человек с большим объёмом.
"""
from __future__ import annotations

import os
import statistics as st
from collections import Counter, defaultdict

import requests

ET, CAT = 166, 24
SINCE = "2026-03-01"

SENT = "DT166_24:PREPARATION"
QUOTE = {"DT166_24:UC_H49RUE", "DT166_24:SUCCESS", "DT166_24:1",
         "DT166_24:4", "DT166_24:5", "DT166_24:FAIL"}
RESP = QUOTE | {"DT166_24:UC_61BSRU", "DT166_24:UC_GFJ5A8", "DT166_24:2"}


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
    print("=== 1. Кто есть кто ===")
    deps = {str(d["ID"]): str(d.get("NAME") or "") for d in bx_all("department.get", {})}
    uname, udep = {}, {}
    for u in bx_all("user.get", {}):
        uid = str(u["ID"])
        uname[uid] = f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip() or uid
        dd = u.get("UF_DEPARTMENT") or []
        udep[uid] = deps.get(str(dd[0]), "—") if dd else "—"
    print(f"  сотрудников: {len(uname)} · подразделений: {len(deps)}")

    print("\n=== 2. Запросы: кто ответственный ===")
    items = bx_all("crm.item.list", {"entityTypeId": ET, "filter": {"categoryId": CAT},
                                     "select": ["id", "assignedById", "createdTime"]})
    owner = {str(i["id"]): str(i.get("assignedById") or "") for i in items}
    born = {str(i["id"]): str(i.get("createdTime") or "")[:7] for i in items}
    print(f"  запросов всего: {len(items)}")

    print(f"\n=== 3. История стадий с {SINCE} ===")
    params = {"entityTypeId": ET, "filter": {"CATEGORY_ID": CAT, ">=CREATED_TIME": SINCE},
              "select": ["OWNER_ID", "CREATED_TIME", "STAGE_ID"], "order": {"CREATED_TIME": "ASC"}}
    hist = bx_all("crm.stagehistory.list", params)
    print(f"  записей истории: {len(hist)}")
    print(f"  встреченные стадии: {dict(Counter(h.get('STAGE_ID') for h in hist).most_common(14))}")

    # первый вход каждого запроса в каждую стадию
    first: dict[tuple[str, str], str] = {}
    for h in hist:
        k = (str(h.get("OWNER_ID")), str(h.get("STAGE_ID")))
        t = str(h.get("CREATED_TIME") or "")
        if k not in first or t < first[k]:
            first[k] = t

    sent_ev = defaultdict(lambda: defaultdict(int))    # месяц → сотрудник → шт
    quote_ev = defaultdict(lambda: defaultdict(int))
    resp_ev = defaultdict(lambda: defaultdict(int))
    seen_q, seen_r = set(), set()
    for (oid, sid), t in sorted(first.items(), key=lambda kv: kv[1]):
        m, who = t[:7], owner.get(oid, "")
        if not who or not m:
            continue
        if sid == SENT:
            sent_ev[m][who] += 1
        if sid in QUOTE and oid not in seen_q:
            seen_q.add(oid); quote_ev[m][who] += 1
        if sid in RESP and oid not in seen_r:
            seen_r.add(oid); resp_ev[m][who] += 1

    months = [m for m in sorted(set(sent_ev) | set(quote_ev)) if m >= "2026-04"]

    print("\n=== 4. ДИНАМИКА ПО МЕСЯЦАМ: все, кто шлёт запросы ===")
    print(f"  {'месяц':8s} {'чел':>4} {'отправл':>8} {'на чел':>7} {'медиана':>8} "
          f"{'КП':>6} {'КП/чел':>7} {'КП мед':>7} {'КП/отпр':>8} {'ответ':>7} {'отв%':>6}")
    for m in months:
        people = sorted(set(sent_ev[m]))
        n = len(people) or 1
        s_v = [sent_ev[m][p] for p in people]
        q_v = [quote_ev[m].get(p, 0) for p in people]
        S, Q, R = sum(s_v), sum(quote_ev[m].values()), sum(resp_ev[m].values())
        print(f"  {m:8s} {len(people):>4} {S:>8} {S/n:>7.1f} {st.median(s_v):>8.0f} "
              f"{Q:>6} {Q/n:>7.1f} {st.median(q_v):>7.0f} {Q/max(S,1)*100:>7.0f}% "
              f"{R:>7} {R/max(S,1)*100:>5.0f}%")

    print("\n=== 5. ТО ЖЕ, ПО ПОДРАЗДЕЛЕНИЯМ (топ-6 по объёму) ===")
    dvol = Counter()
    for m in months:
        for p, v in sent_ev[m].items():
            dvol[udep.get(p, "—")] += v
    for dep, _ in dvol.most_common(6):
        print(f"\n  --- {dep} ---")
        print(f"  {'месяц':8s} {'чел':>4} {'отправл':>8} {'на чел':>7} {'КП':>6} {'КП/чел':>7} {'КП/отпр':>8}")
        for m in months:
            people = [p for p in sent_ev[m] if udep.get(p, "—") == dep]
            if not people:
                continue
            n = len(people)
            S = sum(sent_ev[m][p] for p in people)
            Q = sum(quote_ev[m].get(p, 0) for p in people)
            print(f"  {m:8s} {n:>4} {S:>8} {S/n:>7.1f} {Q:>6} {Q/n:>7.1f} {Q/max(S,1)*100:>7.0f}%")

    print("\n=== 6. КОГОРТЫ: из запросов, СОЗДАННЫХ в месяце, сколько дошло до КП ===")
    print("  (последние месяцы занижены: цикл ответа не завершён)")
    coh = defaultdict(lambda: [0, 0])
    for oid, m in born.items():
        if m < "2026-04":
            continue
        coh[m][0] += 1
        if oid in seen_q:
            coh[m][1] += 1
    for m in sorted(coh):
        n, q = coh[m]
        print(f"  {m}: создано {n:>5} · дошли до КП {q:>5} = {q/max(n,1)*100:4.1f}%")

    print("\n=== 7. КТО ФОРМИРУЕТ ОБЪЁМ: топ-12 по отправкам за период ===")
    tot_s, tot_q = Counter(), Counter()
    for m in months:
        for p, v in sent_ev[m].items():
            tot_s[p] += v
        for p, v in quote_ev[m].items():
            tot_q[p] += v
    print(f"  {'сотрудник':28s} {'подразделение':30s} {'отпр':>6} {'КП':>5} {'КП/отпр':>8}")
    for p, v in tot_s.most_common(12):
        q = tot_q.get(p, 0)
        print(f"  {uname.get(p, p)[:28]:28s} {udep.get(p, '—')[:30]:30s} {v:>6} {q:>5} {q/max(v,1)*100:>7.0f}%")

    print("\n✓ зонд v23 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
