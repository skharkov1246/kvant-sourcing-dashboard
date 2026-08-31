"""CI-зонд v22: аудит процесса «Запросы поставщикам» (СП-166, cat 24).

Меряем то, что нужно для разговора об UX и конверсии в КП:
  1) точные названия стадий и воронка (всего / 2026)
  2) карточка запроса: сколько полей вообще и сколько реально заполняют
  3) время: создан → отправлен → ответ; сколько висит в текущей стадии
  4) ответная конверсия: новый поставщик против повторного
  5) есть ли вообще куда писать — email/телефон у компаний-получателей
  6) связь со сделкой, товарные позиции, число поставщиков на один запрос
"""
from __future__ import annotations

import datetime as dt
import os
import statistics as st
from collections import Counter, defaultdict

import requests

ET = 166
CAT = 24
Y = "2026-01-01"


def bx(method: str, params: dict | None = None) -> dict:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    for _ in range(3):
        try:
            r = requests.post(f"{base}/{method}.json", json=params or {}, timeout=90)
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


def dtp(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    print("=== 1. СТАДИИ ВОРОНКИ ЗАПРОСОВ ===")
    stg = bx("crm.status.list", {"filter": {"ENTITY_ID": f"DYNAMIC_{ET}_STAGE_{CAT}"}}).get("result") or []
    names = {}
    for s in sorted(stg, key=lambda x: int(x.get("SORT") or 0)):
        names[s.get("STATUS_ID")] = s.get("NAME")
        print(f"  {s.get('SORT'):>4} {s.get('STATUS_ID'):24s} «{s.get('NAME')}»")

    print("\n=== 2. КАРТОЧКА ЗАПРОСА: сколько полей просит система ===")
    f = (bx("crm.item.fields", {"entityTypeId": ET}).get("result") or {}).get("fields", {})
    uf = {k: v for k, v in f.items() if k.startswith("ufCrm")}
    req = [k for k, v in f.items() if v.get("isRequired")]
    print(f"  всего полей: {len(f)} · пользовательских: {len(uf)} · обязательных: {len(req)}")
    print(f"  обязательные: {req}")

    print("\n=== 3. ЗАПРОСЫ ===")
    sel = ["id", "title", "stageId", "createdTime", "updatedTime", "movedTime",
           "companyId", "contactId", "assignedById", "parentId2", "opportunity", "currencyId"]
    allr = bx_all("crm.item.list", {"entityTypeId": ET, "select": sel, "filter": {"categoryId": CAT}})
    y26 = [r for r in allr if str(r.get("createdTime") or "").startswith("2026")]
    print(f"  всего запросов: {len(allr)} · в 2026: {len(y26)}")

    def funnel(rows, label):
        c = Counter(r.get("stageId") for r in rows)
        n = len(rows) or 1
        print(f"\n  --- воронка: {label} ({len(rows)}) ---")
        for sid, _ in sorted(c.items(), key=lambda kv: -kv[1]):
            print(f"    {names.get(sid, sid)[:44]:46s} {c[sid]:>6}  {c[sid]/n*100:5.1f}%")
    funnel(allr, "за всё время")
    funnel(y26, "2026")

    print("\n=== 4. ЗАПОЛНЯЕМОСТЬ КАРТОЧКИ (выборка 400 запросов 2026) ===")
    sample = y26[-400:] if len(y26) > 400 else y26
    ids = [r["id"] for r in sample]
    full = []
    for i in range(0, len(ids), 50):
        chunk = bx_all("crm.item.list", {"entityTypeId": ET,
            "filter": {"categoryId": CAT, "@id": ids[i:i + 50]}})
        full += chunk
    print(f"  разобрано карточек: {len(full)}")
    fillc = Counter()
    for it in full:
        for k, v in it.items():
            if v not in (None, "", 0, "0", [], {}, "0.00"):
                fillc[k] += 1
    n = len(full) or 1
    print("  --- пользовательские поля: как часто заполнены ---")
    rows = [(k, fillc.get(k, 0), (uf.get(k) or {}).get("title") or "") for k in uf]
    for k, c, t in sorted(rows, key=lambda x: -x[1]):
        if c:
            print(f"    {c/n*100:5.1f}%  {c:>4}/{n}  {k:26s} «{str(t)[:44]}»")
    dead = [k for k, c, t in rows if not c]
    print(f"  ПУСТЫЕ ВСЕГДА ({len(dead)} полей): {dead[:24]}")

    print("\n=== 5. ВРЕМЯ ===")
    now = dt.datetime.now(dt.timezone.utc)
    open_st = [s for s in names if s not in ("SUCCESS", "FAIL")]
    ages = defaultdict(list)
    for r in y26:
        mv, cr = dtp(r.get("movedTime")), dtp(r.get("createdTime"))
        if mv:
            ages[r.get("stageId")].append((now - mv).total_seconds() / 86400)
    print("  сколько дней запросы уже сидят в своей текущей стадии (медиана / макс):")
    for sid, v in sorted(ages.items(), key=lambda kv: -len(kv[1])):
        if v:
            print(f"    {names.get(sid, sid)[:40]:42s} n={len(v):>5}  мед {st.median(v):6.1f} дн  макс {max(v):6.0f} дн")
    closed = [r for r in y26 if r.get("stageId") in ("SUCCESS", "FAIL")]
    lags = [( (dtp(r.get("updatedTime")) - dtp(r.get("createdTime"))).total_seconds()/86400 )
            for r in closed if dtp(r.get("updatedTime")) and dtp(r.get("createdTime"))]
    if lags:
        lags.sort()
        print(f"  цикл запроса до закрытия (2026, n={len(lags)}): "
              f"мед {st.median(lags):.1f} дн · p25 {lags[len(lags)//4]:.1f} · p75 {lags[3*len(lags)//4]:.1f}")

    print("\n=== 6. ОТВЕТИЛИ ЛИ: новый поставщик против повторного ===")
    first_seen = {}
    for r in sorted(allr, key=lambda r: str(r.get("createdTime"))):
        cid = str(r.get("companyId") or "")
        if cid and cid not in first_seen:
            first_seen[cid] = r["id"]
    ANSW = {"SUCCESS", "1", "2"}   # уточняется по названиям стадий ниже
    def answered(r): return r.get("stageId") in ("SUCCESS",) or str(r.get("stageId")).endswith(":1")
    n_new = a_new = n_rep = a_rep = 0
    for r in y26:
        cid = str(r.get("companyId") or "")
        if not cid:
            continue
        isnew = first_seen.get(cid) == r["id"]
        ok = r.get("stageId") == "SUCCESS"
        if isnew:
            n_new += 1; a_new += ok
        else:
            n_rep += 1; a_rep += ok
    print(f"  первый запрос этой компании : {n_new:>6} · дошли до «КП получено» {a_new:>5} = {a_new/max(n_new,1)*100:4.1f}%")
    print(f"  повторный запрос            : {n_rep:>6} · дошли до «КП получено» {a_rep:>5} = {a_rep/max(n_rep,1)*100:4.1f}%")
    per = Counter(str(r.get("companyId")) for r in allr if r.get("companyId"))
    dist = Counter(min(v, 6) for v in per.values())
    print(f"  поставщиков всего в запросах: {len(per)} · запросов на поставщика: "
          + " · ".join(f"{k if k<6 else '6+'}:{v}" for k, v in sorted(dist.items())))

    print("\n=== 7. ЕСТЬ ЛИ КУДА ПИСАТЬ: контакты компаний-получателей ===")
    cids = sorted({str(r.get("companyId")) for r in y26 if r.get("companyId")})
    have_mail = have_phone = seen = 0
    for i in range(0, len(cids), 100):
        for c in bx_all("crm.company.list", {"filter": {"ID": cids[i:i + 100]},
                                             "select": ["ID", "HAS_EMAIL", "HAS_PHONE"]}):
            seen += 1
            have_mail += c.get("HAS_EMAIL") == "Y"
            have_phone += c.get("HAS_PHONE") == "Y"
    print(f"  компаний в запросах 2026: {seen}")
    print(f"    с email:   {have_mail:>5} ({have_mail/max(seen,1)*100:.0f}%)")
    print(f"    с телефоном:{have_phone:>5} ({have_phone/max(seen,1)*100:.0f}%)")

    print("\n=== 8. СВЯЗНОСТЬ ===")
    withdeal = sum(1 for r in y26 if r.get("parentId2"))
    withcont = sum(1 for r in y26 if r.get("contactId"))
    withsum = sum(1 for r in y26 if float(r.get("opportunity") or 0) > 0)
    print(f"  привязан к сделке: {withdeal}/{len(y26)} ({withdeal/max(len(y26),1)*100:.0f}%)")
    print(f"  указано контактное лицо: {withcont}/{len(y26)} ({withcont/max(len(y26),1)*100:.0f}%)")
    print(f"  заполнена сумма: {withsum}/{len(y26)} ({withsum/max(len(y26),1)*100:.0f}%)")
    perdeal = Counter(str(r.get("parentId2")) for r in y26 if r.get("parentId2"))
    if perdeal:
        vals = sorted(perdeal.values())
        print(f"  поставщиков опрашивают на одну сделку: мед {st.median(vals):.0f} · "
              f"p75 {vals[3*len(vals)//4]} · макс {max(vals)} · сделок с запросами {len(perdeal)}")
        d1 = sum(1 for v in perdeal.values() if v == 1)
        print(f"  сделок, где опросили только ОДНОГО поставщика: {d1} ({d1/len(perdeal)*100:.0f}%)")

    print("\n✓ зонд v22 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
