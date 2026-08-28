"""CI-зонд v21: результативность сорсера Егора Семитко в сравнении со всей когортой.

Меряем одной линейкой всех, кто заводил RFQ в СП-166, и смотрим, где он в ряду:
  • объём: сколько RFQ завёл, за какой срок, темп в неделю;
  • входящие КП: сколько запросов дошло до «КП получено/выбран» и конверсия от закрытых;
  • приведённые поставщики: компании, к которым ОН первым в компании отправил RFQ;
  • ЭФФЕКТИВНЫЕ поставщики: из приведённых — те, у кого потом реально появились
    заказы в СП-172 (то есть его находка превратилась в закупку), и на какую сумму.
Плюс контекст: стаж (первый RFQ), доля свежих запросов, медиана и квартили когорты.
"""
from __future__ import annotations

import os
import statistics as st
from collections import Counter, defaultdict

import requests

TARGET = ("семитко", "semitko")
SEL_STAGES = {"DT166_24:SUCCESS", "DT166_24:1"}          # «КП получено / выбран»
CLOSED_SUF = {"SUCCESS", "1", "2", "3", "FAIL", "4", "5"}


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


def suf(sid): return str(sid or "").split(":")[-1]
def is_closed(sid): return suf(sid) in CLOSED_SUF
def is_sel(sid): return str(sid) in SEL_STAGES or suf(sid) in ("SUCCESS", "1")
def d10(s): return str(s or "")[:10]


def main() -> int:
    users = {str(u["ID"]): f"{u.get('NAME') or ''} {u.get('LAST_NAME') or ''}".strip()
             for u in bx_all("user.get", {})}
    target_ids = [uid for uid, nm in users.items() if any(t in nm.lower() for t in TARGET)]
    print("=== 1. Кого ищем ===")
    for uid in target_ids:
        print(f"  user #{uid}: «{users[uid]}»")
    if not target_ids:
        print("  ! пользователь с фамилией Семитко не найден в портале")

    print("\n=== 2. Выгрузка RFQ (СП-166) ===")
    rfqs = bx_all("crm.item.list", {"entityTypeId": 166,
        "select": ["id", "title", "companyId", "parentId2", "stageId",
                   "createdTime", "movedTime", "assignedById", "createdBy"]})
    print(f"  всего записей СП-166: {len(rfqs)}")
    if rfqs:
        print(f"  диапазон дат: {min(d10(r.get('createdTime')) for r in rfqs)} → "
              f"{max(d10(r.get('createdTime')) for r in rfqs)}")

    # --- кто первым отправил RFQ в компанию-поставщика = «привёл поставщика»
    first_by_sup = {}
    for r in sorted(rfqs, key=lambda r: str(r.get("createdTime") or "")):
        cid = str(r.get("companyId") or "")
        if cid and cid not in first_by_sup:
            first_by_sup[cid] = (str(r.get("assignedById") or ""), d10(r.get("createdTime")))

    print("\n=== 3. Заказы СП-172: у каких поставщиков реально закупались ===")
    orders = bx_all("crm.item.list", {"entityTypeId": 172,
        "select": ["id", "companyId", "opportunity", "currencyId", "stageId", "createdTime"]})
    cur = bx("crm.currency.list", {}).get("result") or []
    rate = {c.get("CURRENCY"): float(c.get("AMOUNT") or 1) / float(c.get("AMOUNT_CNT") or 1) for c in cur}
    ord_by_sup = defaultdict(lambda: [0, 0.0])
    for o in orders:
        cid = str(o.get("companyId") or "")
        if not cid:
            continue
        ord_by_sup[cid][0] += 1
        ord_by_sup[cid][1] += float(o.get("opportunity") or 0) * rate.get(o.get("currencyId"), 1.0)
    print(f"  заказов всего: {len(orders)} · поставщиков с заказами: {len(ord_by_sup)}")

    # --- метрики по каждому сорсеру
    by_user = defaultdict(list)
    for r in rfqs:
        by_user[str(r.get("assignedById") or "")].append(r)

    rows = []
    for uid, items in by_user.items():
        if len(items) < 5:                      # случайные заводители — не когорта
            continue
        dates = sorted(d10(r.get("createdTime")) for r in items if r.get("createdTime"))
        closed = [r for r in items if is_closed(r.get("stageId"))]
        sel = [r for r in items if is_sel(r.get("stageId"))]
        y26 = [r for r in items if d10(r.get("createdTime")) >= "2026-01-01"]
        sel26 = [r for r in y26 if is_sel(r.get("stageId"))]
        brought = [cid for cid, (owner, _) in first_by_sup.items() if owner == uid]
        eff = [cid for cid in brought if cid in ord_by_sup]
        eff_money = sum(ord_by_sup[cid][1] for cid in eff)
        eff_orders = sum(ord_by_sup[cid][0] for cid in eff)
        months = max(1, (len(set(d[:7] for d in dates)) or 1))
        rows.append({
            "uid": uid, "name": users.get(uid, f"#{uid}"),
            "n": len(items), "n26": len(y26),
            "first": dates[0] if dates else "—", "last": dates[-1] if dates else "—",
            "months": months,
            "closed": len(closed), "sel": len(sel), "sel26": len(sel26),
            "conv": round(len(sel) / len(closed) * 100) if closed else 0,
            "sup": len({str(r.get("companyId")) for r in items if r.get("companyId")}),
            "brought": len(brought), "eff": len(eff),
            "effRate": round(len(eff) / len(brought) * 100) if brought else 0,
            "effMoney": eff_money, "effOrders": eff_orders,
        })

    def rank(key, uid, rev=True):
        order = sorted(rows, key=lambda r: r[key], reverse=rev)
        return next((i + 1 for i, r in enumerate(order) if r["uid"] == uid), None)

    print(f"\n=== 4. КОГОРТА СОРСЕРОВ ({len(rows)} чел., ≥5 RFQ) ===")
    hdr = (f"  {'сорсер':26s} {'RFQ':>5} {'2026':>5} {'закр':>5} {'КП':>4} {'конв':>5} "
           f"{'пост':>5} {'привёл':>7} {'эфф':>4} {'%эф':>4} {'Σ€ через них':>13} {'первый RFQ':>11}")
    print(hdr)
    for r in sorted(rows, key=lambda r: -r["n"]):
        mark = "  ←" if r["uid"] in target_ids else ""
        print(f"  {r['name'][:26]:26s} {r['n']:>5} {r['n26']:>5} {r['closed']:>5} {r['sel']:>4} "
              f"{r['conv']:>4}% {r['sup']:>5} {r['brought']:>7} {r['eff']:>4} {r['effRate']:>3}% "
              f"{r['effMoney']:>13,.0f} {r['first']:>11}{mark}")

    if rows:
        print("\n  --- медианы когорты ---")
        for k, lbl in [("n", "RFQ всего"), ("n26", "RFQ в 2026"), ("sel", "КП получено"),
                       ("conv", "конверсия, %"), ("brought", "привёл поставщиков"),
                       ("eff", "эффективных"), ("effRate", "доля эффективных, %")]:
            vals = sorted(r[k] for r in rows)
            print(f"    {lbl:24s} медиана {st.median(vals):>8.1f} · "
                  f"нижний квартиль {vals[len(vals)//4]:>6} · верхний {vals[3*len(vals)//4]:>6}")

    for uid in target_ids:
        me = next((r for r in rows if r["uid"] == uid), None)
        print(f"\n=== 5. РАЗБОР: {users[uid]} ===")
        if not me:
            n = len(by_user.get(uid, []))
            print(f"  в когорте нет: всего {n} RFQ (порог 5). Похоже, RFQ он практически не заводит.")
            continue
        print(f"  RFQ всего: {me['n']} (место {rank('n', uid)} из {len(rows)}) · "
              f"в 2026: {me['n26']} (место {rank('n26', uid)})")
        print(f"  период работы: {me['first']} → {me['last']} · активных месяцев: {me['months']} · "
              f"темп ≈ {me['n']/me['months']:.1f} RFQ/мес")
        print(f"  входящие КП («КП получено/выбран»): {me['sel']} всего, {me['sel26']} в 2026 "
              f"(место {rank('sel', uid)})")
        print(f"  конверсия закрытых в КП: {me['conv']}% (место {rank('conv', uid)}) · "
              f"закрыто {me['closed']} из {me['n']}")
        print(f"  уникальных поставщиков в запросах: {me['sup']}")
        print(f"  ПРИВЁЛ поставщиков (первым отправил им RFQ): {me['brought']} (место {rank('brought', uid)})")
        print(f"  из них ЭФФЕКТИВНЫХ (появились заказы): {me['eff']} ({me['effRate']}%) · "
              f"место {rank('eff', uid)} · заказов через них {me['effOrders']} на ≈{me['effMoney']:,.0f} €")

        mine = by_user[uid]
        print("  распределение по стадиям:")
        for s, n in Counter(str(r.get("stageId")) for r in mine).most_common():
            print(f"    {s:28s} {n:>4}")
        print("  по месяцам:")
        mon = Counter(d10(r.get("createdTime"))[:7] for r in mine)
        print("    " + " · ".join(f"{m}:{n}" for m, n in sorted(mon.items())))
        eff_ids = [cid for cid in first_by_sup if first_by_sup[cid][0] == uid and cid in ord_by_sup]
        if eff_ids:
            cn = {}
            for c in bx_all("crm.company.list", {"filter": {"ID": sorted(eff_ids)[:50]},
                                                 "select": ["ID", "TITLE"]}):
                cn[str(c["ID"])] = c.get("TITLE") or ""
            print("  его эффективные поставщики (заказы / сумма):")
            for cid in sorted(eff_ids, key=lambda c: -ord_by_sup[c][1])[:20]:
                n, e = ord_by_sup[cid]
                print(f"    {cn.get(cid, '#'+cid)[:46]:48s} {n:>3} зак. · {e:>12,.0f} €")

    print("\n✓ зонд v21 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
