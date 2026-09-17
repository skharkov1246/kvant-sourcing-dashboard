"""Зонд v35: коммерческий состав и нагрузка (для вкладок «КАМы» и «Продукт-оунеры»).

Отвечает на вопросы, без которых управленческая вкладка строится на догадках:
  • кто в портале ЕСТЬ, а кто уволен (user.get ACTIVE), по отделам;
  • какие отделы коммерческие — по числу открытых сделок, а не по названию;
  • какие воронки и стадии живые, сколько в каждой сделок и денег;
  • сколько сделок висит на уволенных;
  • сколько просрочено, сколько без движения, сколько без суммы/клиента/даты;
  • какие поля сделки годятся под «плановую дату» и «вероятность».

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ: идентификаторы и названия отделов/воронок/стадий,
должности, счётчики и суммы. Ни фамилий, ни названий сделок, ни клиентов.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitrix_client import BitrixClient  # noqa: E402

TODAY = dt.date.today()
YEAR_START = "2026-01-01T00:00:00"


def money(v: float) -> str:
    v = round(v)
    return f"{v/1_000_000:.2f}M" if abs(v) >= 1_000_000 else (f"{v/1000:.0f}K" if abs(v) >= 1000 else str(v))


def head(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> int:
    client = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])

    # ------------------------------------------------------------------ 1. отделы
    head("1. ОТДЕЛЫ (department.get)")
    deps = client.list_paged("department.get", {})
    dname = {str(d["ID"]): d.get("NAME", "") for d in deps}
    dparent = {str(d["ID"]): str(d.get("PARENT") or "") for d in deps}
    dhead = {str(d["ID"]): str(d.get("UF_HEAD") or "") for d in deps}
    print(f"всего отделов: {len(deps)}")

    # ------------------------------------------------------------------ 2. люди
    head("2. ЛЮДИ (user.get: ACTIVE / уволенные / отдел / должность)")
    users = client.list_paged("user.get", {"ADMIN_MODE": True})
    act, fired = {}, {}
    udept: dict[str, list[str]] = {}
    upos: dict[str, str] = {}
    for u in users:
        uid = str(u["ID"])
        a = str(u.get("ACTIVE")).lower() in ("y", "true", "1")
        (act if a else fired)[uid] = True
        dd = u.get("UF_DEPARTMENT") or []
        udept[uid] = [str(x) for x in (dd if isinstance(dd, list) else [dd])]
        upos[uid] = (u.get("WORK_POSITION") or "").strip()
    print(f"пользователей всего: {len(users)} · активных: {len(act)} · уволенных/отключённых: {len(fired)}")
    print(f"поля user.get: {sorted(set(k for u in users[:5] for k in u.keys()))}")

    per_dept_act = Counter(); per_dept_fired = Counter()
    for uid in act:
        for d in udept.get(uid) or ["—"]:
            per_dept_act[d] += 1
    for uid in fired:
        for d in udept.get(uid) or ["—"]:
            per_dept_fired[d] += 1

    head("3. ДОЛЖНОСТИ активных (WORK_POSITION → сколько человек)")
    for pos, n in Counter(upos[u] or "(пусто)" for u in act).most_common(60):
        print(f"{n:>4}  {pos}")

    # ------------------------------------------------------------------ 4. воронки
    head("4. ВОРОНКИ СДЕЛОК (crm.category.list entityTypeId=2)")
    cats = {}
    try:
        res = client.call("crm.category.list", {"entityTypeId": 2}) or {}
        for c in (res.get("categories") if isinstance(res, dict) else res) or []:
            cats[str(c.get("id"))] = c.get("name") or f"cat{c.get('id')}"
    except Exception as e:
        print(f"crm.category.list: {type(e).__name__}: {e}")
    if not cats:
        cats = {str(k): v for k, v in (client.categories() or {}).items()}
    for cid, nm in sorted(cats.items(), key=lambda kv: int(kv[0])):
        print(f"cat {cid:>3}  {nm}")

    stage_meta = client.deal_stage_meta()
    print(f"стадий всего (все воронки): {len(stage_meta)}")

    # ------------------------------------------------------------------ 5. сделки
    head("5. СДЕЛКИ: открытые (все годы) + созданные YTD")
    sel = ["ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID",
           "DATE_CREATE", "DATE_MODIFY", "CLOSEDATE", "ASSIGNED_BY_ID", "COMPANY_ID", "CLOSED"]
    op = client.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "P"}, select=sel)
    ytd = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START}, select=sel)
    print(f"открытых сделок (semantic=P): {len(op)}")
    print(f"создано с 01.01.2026: {len(ytd)}")

    cur = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in cur}
    eur = lambda d: float(d.get("OPPORTUNITY") or 0) * rate.get(d.get("CURRENCY_ID"), 1.0)

    head("5.1 Открытые сделки по воронкам")
    byc = defaultdict(lambda: [0, 0.0])
    for d in op:
        b = byc[str(d.get("CATEGORY_ID") or "0")]; b[0] += 1; b[1] += eur(d)
    for cid, (n, s) in sorted(byc.items(), key=lambda kv: -kv[1][0]):
        print(f"cat {cid:>3}  {n:>6} шт  Σ {money(s):>9} €   {cats.get(cid,'?')}")

    head("5.2 Открытые сделки по стадиям (топ-40)")
    bys = Counter(str(d.get("STAGE_ID")) for d in op)
    sums = defaultdict(float)
    for d in op:
        sums[str(d.get("STAGE_ID"))] += eur(d)
    for st, n in bys.most_common(40):
        mt = stage_meta.get(st, {})
        print(f"{n:>6} шт  Σ {money(sums[st]):>9} €  cat{mt.get('cat','?'):>3} {mt.get('sem','?')}  {st:<28} {mt.get('name','?')}")

    # ------------------------------------------------------------------ 6. владельцы
    head("6. ВЛАДЕЛЬЦЫ: активные против уволенных")
    own_open = Counter(); own_open_sum = defaultdict(float)
    for d in op:
        u = str(d.get("ASSIGNED_BY_ID") or "")
        own_open[u] += 1; own_open_sum[u] += eur(d)
    own_ytd = Counter(str(d.get("ASSIGNED_BY_ID") or "") for d in ytd)
    owners = set(own_open) | set(own_ytd)
    fired_owners = [u for u in owners if u in fired]
    ghost = [u for u in owners if u not in act and u not in fired]
    print(f"владельцев всего (открытые+YTD): {len(owners)}")
    print(f"из них активных: {len([u for u in owners if u in act])}")
    print(f"из них УВОЛЕННЫХ: {len(fired_owners)} · на них открытых сделок: "
          f"{sum(own_open[u] for u in fired_owners)} шт  Σ {money(sum(own_open_sum[u] for u in fired_owners))} €")
    print(f"владельцев, которых нет в user.get вовсе: {len(ghost)} · открытых: {sum(own_open[u] for u in ghost)}")

    head("6.1 Отделы по нагрузке (открытые сделки владельцев отдела)")
    dep_open = Counter(); dep_sum = defaultdict(float); dep_people = defaultdict(set)
    for u, n in own_open.items():
        for d in (udept.get(u) or ["—"]):
            dep_open[d] += n; dep_sum[d] += own_open_sum[u]; dep_people[d].add(u)
    for d, n in dep_open.most_common(40):
        heads = "рук.есть" if dhead.get(d) else "рук.нет"
        print(f"dept {d:>4}  {n:>6} откр.  Σ {money(dep_sum[d]):>9} €  людей с сделками {len(dep_people[d]):>3}"
              f"  активных {per_dept_act[d]:>3} уволенных {per_dept_fired[d]:>3}  {heads}  {dname.get(d,'?')}")

    head("6.2 Распределение нагрузки на человека (только активные владельцы)")
    live = sorted(((own_open[u], own_open_sum[u]) for u in own_open if u in act), reverse=True)
    if live:
        ns = [x[0] for x in live]
        print(f"активных владельцев с открытыми сделками: {len(ns)}")
        print(f"сделок на человека: max {ns[0]} · медиана {ns[len(ns)//2]} · min {ns[-1]} · среднее {sum(ns)/len(ns):.1f}")
        print("топ-15 по числу открытых (без имён): " + ", ".join(f"{n}шт/{money(s)}€" for n, s in live[:15]))

    # ------------------------------------------------------------------ 7. качество
    head("7. ПРОСРОЧКА, ЗАСТОЙ, ГИГИЕНА (по открытым сделкам)")
    today = TODAY.isoformat()
    late = [d for d in op if str(d.get("CLOSEDATE") or "")[:10] and str(d["CLOSEDATE"])[:10] < today]
    nodate = [d for d in op if not str(d.get("CLOSEDATE") or "")[:10]]
    noamt = [d for d in op if not float(d.get("OPPORTUNITY") or 0)]
    nocomp = [d for d in op if not d.get("COMPANY_ID") or str(d.get("COMPANY_ID")) == "0"]
    def stale(days):
        cut = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()[:19]
        return [d for d in op if str(d.get("DATE_MODIFY") or "")[:19] < cut]
    print(f"просрочено (CLOSEDATE < сегодня): {len(late)} шт  Σ {money(sum(eur(d) for d in late))} €")
    print(f"без плановой даты закрытия:      {len(nodate)} шт")
    print(f"без суммы:                       {len(noamt)} шт")
    print(f"без компании:                    {len(nocomp)} шт")
    for dd in (30, 60, 90, 180):
        s = stale(dd)
        print(f"без движения > {dd:>3} дн:            {len(s)} шт  Σ {money(sum(eur(x) for x in s))} €")

    # ------------------------------------------------------------------ 8. заказы
    head("8. ЗАКАЗЫ ПОСТАВЩИКАМ (СП-172): владельцы и стадии")
    try:
        orders = client.list_items(172, filter={">=createdTime": "2025-01-01T00:00:00"},
                                   select=["id", "stageId", "opportunity", "currencyId", "createdTime",
                                           "parentId2", "assignedById", "categoryId"])
        print(f"заказов с 2025: {len(orders)}")
        st = Counter(str(o.get("stageId")) for o in orders)
        for s, n in st.most_common(20):
            print(f"{n:>6}  {s}")
        oown = Counter(str(o.get("assignedById")) for o in orders)
        print(f"владельцев заказов: {len(oown)} · из них уволенных: {len([u for u in oown if u in fired])}")
    except Exception as e:
        print(f"СП-172: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------ 9. поля
    head("9. ПОЛЯ СДЕЛКИ — кандидаты под срок/вероятность/ответственных")
    try:
        f = client.call("crm.deal.fields", {}) or {}
        keys = [k for k in f if k.startswith("UF_")]
        print(f"пользовательских полей сделки: {len(keys)}")
        for k in sorted(keys):
            ttl = (f[k].get("formLabel") or f[k].get("title") or "")
            tp = f[k].get("type")
            if tp in ("date", "datetime", "double", "integer", "employee") or "дат" in ttl.lower() or "date" in ttl.lower():
                print(f"{k:<28} {tp:<10} {ttl[:60]}")
        for k in ("PROBABILITY", "CLOSEDATE", "BEGINDATE", "IS_RETURN_CUSTOMER", "SOURCE_ID"):
            if k in f:
                print(f"системное {k:<20} {f[k].get('type')}")
    except Exception as e:
        print(f"crm.deal.fields: {type(e).__name__}: {e}")
    print("\nГОТОВО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
