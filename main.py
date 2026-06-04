"""Автономный дашборд анализа работы сорсеров (КВАНТ · Bitrix24).

Пайплайн: выгрузка СП-166 «Запросы поставщикам» за период → расчёт метрик →
текстовые инсайты (гибрид Claude/правила) → сборка HTML-дашборда.

Примеры:
  python main.py --period 2026-05 --open
  python main.py --period 2026-05 --dry-run        # только метрики, без LLM и HTML
  python main.py --period 2026-04:2026-05-15 --no-llm
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

import company as company_mod
import config
import dashboard
import insights as insights_mod
import kam as kam_mod
import metrics as metrics_mod
import period as period_mod
from bitrix_client import BitrixClient

RFQ_SELECT = ["id", "assignedById", "stageId", "createdTime", "movedTime", "parentId2", "categoryId",
              "title", "companyId", "ufCrm18Supplier", "ufCrm18SupplContact"]

SUPPLIER_CRM_FIELDS = ("ufCrm18Supplier", "ufCrm18SupplContact")


def _crm_ref_ids(val):
    """Значение crm-поля (напр. ['CO_372','C_45']) → (company_ids, contact_ids)."""
    comps, conts = set(), set()
    for x in (val if isinstance(val, list) else [val] if val else []):
        s = str(x).strip()
        if s.startswith("CO_"):
            comps.add(s[3:])
        elif s.startswith("CT_"):
            conts.add(s[3:])
        elif s.startswith("C_"):
            conts.add(s[2:])
        elif s.isdigit():
            comps.add(s)
    return comps, conts


def _attach_suppliers(client: BitrixClient, rfqs: list[dict]) -> None:
    """Проставляет r['_supplier'] — имя поставщика (компания/контакт) по ссылкам RFQ."""
    comp_ids, cont_ids = set(), set()
    for r in rfqs:
        if r.get("companyId"):
            comp_ids.add(str(r["companyId"]))
        for f in SUPPLIER_CRM_FIELDS:
            c, t = _crm_ref_ids(r.get(f))
            comp_ids |= c
            cont_ids |= t
    comp_names = client.companies_by_ids(comp_ids) if comp_ids else {}
    cont_names = client.contacts_by_ids(cont_ids) if cont_ids else {}

    def name_of(r):
        c, t = _crm_ref_ids(r.get("ufCrm18Supplier"))
        for cid in c:
            if comp_names.get(cid):
                return comp_names[cid]
        for tid in t:
            if cont_names.get(tid):
                return cont_names[tid]
        _, t2 = _crm_ref_ids(r.get("ufCrm18SupplContact"))
        for tid in t2:
            if cont_names.get(tid):
                return cont_names[tid]
        return comp_names.get(str(r.get("companyId") or ""), "—")

    for r in rfqs:
        r["_supplier"] = name_of(r)


def _names_and_since(client: BitrixClient):
    names: dict[str, str] = {}
    since: dict[str, str] = {}
    for u in client.list_paged("user.get", {}):
        uid = str(u["ID"])
        names[uid] = " ".join(x for x in [u.get("LAST_NAME"), u.get("NAME")] if x).strip() or f"user#{uid}"
        raw = u.get("UF_EMPLOYMENT_DATE") or u.get("DATE_REGISTER")
        d = period_mod.parse_dt(raw) if raw else None
        if d:
            since[uid] = f"{period_mod._MON[d.month]}'{str(d.year)[2:]}"
    return names, since


SPA_NEW_STAGE = "DT166_24:NEW"


def _send_stats(client: BitrixClient, rfqs: list[dict], sourcer_rows: list[dict],
                dept_a_ids: set[str], start_iso: str, end_iso: str) -> dict:
    """Реально отправлено vs создано: по факту исходящего CRM-письма на карточке RFQ.
    Возвращает {"rows": [...по сорсерам блока A...], "totals": {...}}."""
    counts: Counter = Counter()  # card_id -> кол-во исходящих писем
    last = 0
    while True:
        ch = client.call("crm.activity.list", {
            "filter": {"OWNER_TYPE_ID": config.SPA_ENTITY_TYPE_ID, "PROVIDER_ID": "CRM_EMAIL",
                       "DIRECTION": 2, ">=CREATED": start_iso, "<=CREATED": end_iso, ">ID": last},
            "select": ["ID", "OWNER_ID"], "order": {"ID": "ASC"}, "start": -1}) or []
        if not ch:
            break
        for a in ch:
            counts[str(a.get("OWNER_ID"))] += 1
        last = int(ch[-1]["ID"])
        if len(ch) < 50:
            break

    by_user: dict[str, list[dict]] = defaultdict(list)
    for r in rfqs:
        u = str(r.get("assignedById"))
        if u in dept_a_ids:
            by_user[u].append(r)

    rows = []
    for s in sourcer_rows:  # уже блок A, отсортирован по объёму
        items = by_user.get(s["id"], [])
        total = len(items)
        sent = sum(1 for r in items if counts.get(str(r["id"])))
        fake = sum(1 for r in items if r.get("stageId") != SPA_NEW_STAGE and not counts.get(str(r["id"])))
        followup = sum(1 for r in items if counts.get(str(r["id"]), 0) >= 2)
        rows.append({
            "id": s["id"], "n": s["n"], "total": total, "sent": sent, "nosend": total - sent,
            "sentPct": round(sent / total * 100) if total else 0,
            "fake": fake, "fuPct": round(followup / sent * 100) if sent else 0,
        })
    tot = {kk: sum(r[kk] for r in rows) for kk in ("total", "sent", "nosend", "fake")}
    tot["sentPct"] = round(tot["sent"] / tot["total"] * 100) if tot["total"] else 0
    return {"rows": rows, "totals": tot}


def run(args) -> int:
    settings = config.Settings.load()
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    p = period_mod.parse_period(args.period, as_of=as_of)
    use_llm = not args.no_llm and not args.dry_run
    if use_llm:
        settings.require_anthropic()

    print(f"▶ Период: {p.label}  ({p.days} дн, {len(p.weeks)} нед)")
    client = BitrixClient(settings.bitrix_webhook_url)

    print("• Справочники: отдел 172, пользователи, воронки, стадии сделок…")
    dept_a_ids = client.dept_member_ids(config.DEPT_SOURCING_ID)
    names, since = _names_and_since(client)
    category_names = client.categories()
    deal_stage_names = client.stages()

    print(f"• Выгрузка RFQ СП-{config.SPA_ENTITY_TYPE_ID} за период…")
    rfqs = client.list_items(
        config.SPA_ENTITY_TYPE_ID,
        filter={
            "categoryId": config.SPA_CATEGORY_ID,
            ">=createdTime": p.start_iso,
            "<=createdTime": p.end_iso,
        },
        select=RFQ_SELECT,
        max_items=args.max_deals,
    )
    print(f"  RFQ: {len(rfqs)}  |  блок A (отдел 172): {sum(1 for r in rfqs if str(r.get('assignedById')) in dept_a_ids)}")

    parent_ids = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
    print(f"• Родительские сделки (parentId2): {len(parent_ids)} → выгрузка стадий…")
    deal_index = client.deals_by_ids(parent_ids)

    print("• Сделки периода (все воронки) для покрытия…")
    period_deals = client.deals_in_period(p.start_iso, p.end_iso)
    print(f"  сделок периода: {len(period_deals)}")

    print("• Поставщики по RFQ (компании/контакты)…")
    _attach_suppliers(client, rfqs)

    print("• Расчёт метрик…")
    m = metrics_mod.build(
        p, rfqs, deal_index, period_deals, dept_a_ids,
        names, since, deal_stage_names, category_names,
    )

    print("• Отправлено vs создано (письма)…")
    m["send"] = _send_stats(client, rfqs, m["sourcersA"], dept_a_ids, p.start_iso, p.end_iso)

    out_dir = Path(args.out)
    slug = (args.period or f"{p.start}_{p.end}").replace(":", "_")
    (out_dir).mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / f"metrics_{slug}.json"
    metrics_path.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ метрики: {metrics_path}")

    if args.dry_run:
        _print_summary(m)
        return 0

    print(f"• Инсайты ({'Claude' if use_llm else 'правила'})…")
    ins = insights_mod.generate(m, settings, use_llm=use_llm)
    print(f"  источник: {ins.get('_source')}")

    company_data = kam_data = eng_data = prod_data = None
    try:
        print("• Пульс + КАМы + инжиниринг + продукт (YTD): общий пул сделок/заказов…")
        ys = "2026-01-01T00:00:00"
        deals_ytd = client.list_deals_fast(filter={">=DATE_CREATE": ys},
            select=["ID", "TITLE", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "ASSIGNED_BY_ID"])
        orders_ytd = client.list_items(172, filter={">=createdTime": ys},
            select=["id", "title", "stageId", "opportunity", "currencyId", "createdTime", "parentId2", "assignedById"])
        # заказы СП-172: исключаем проигранные (…:FAIL) — это не контрактная выручка
        orders_ytd = [o for o in orders_ytd if not str(o.get("stageId", "")).endswith(":FAIL")]
        # полная карта владельцев сделок: 2026 + родители заказов, созданные ДО 2026
        # (иначе ~70% заказов атрибутируются по assignedById карточки заказа, а не по владельцу сделки)
        deal_owner = {str(d["ID"]): str(d.get("ASSIGNED_BY_ID")) for d in deals_ytd}
        parent_ids = {str(o.get("parentId2")) for o in orders_ytd if o.get("parentId2")}
        missing = [pid for pid in parent_ids if pid and pid not in deal_owner]
        if missing:
            extra = client.deals_by_ids(missing, select=["ID", "ASSIGNED_BY_ID"])
            for did, d in extra.items():
                deal_owner[str(did)] = str(d.get("ASSIGNED_BY_ID"))
            print(f"  владельцы родительских сделок заказов: догружено {len(extra)} (из {len(missing)})")
        company_data = company_mod.compute(client, as_of=p.end, created=deals_ytd, orders=orders_ytd)
        kam_data = kam_mod.compute_set(client, kam_mod.CLIENT_GROUPS, as_of=p.end, created=deals_ytd, orders=orders_ytd, with_people=True, deal_owner=deal_owner)
        eng_data = kam_mod.compute_set(client, kam_mod.ENG_GROUPS, as_of=p.end, created=deals_ytd, orders=orders_ytd, with_people=True, deal_owner=deal_owner)
        prod_data = kam_mod.compute_set(client, kam_mod.PRODUCT_GROUPS, as_of=p.end, created=deals_ytd, orders=orders_ytd, with_people=True, deal_owner=deal_owner)
    except Exception as e:  # вкладка «Сорсинг» не должна падать из-за доп. вкладок
        print(f"  ⚠ доп-вкладки пропущены: {type(e).__name__}: {e}")

    html_path = out_dir / f"dashboard_{slug}.html"
    dashboard.write(m, ins, html_path, title=f"Сорсинг · {p.label}",
                    company=company_data, kam=kam_data, eng=eng_data, prod=prod_data)
    print(f"  ✓ дашборд: {html_path}")

    if args.open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


def _print_summary(m: dict) -> None:
    k = m["kpi"]
    print("\n── СВОДКА ─────────────────────────────")
    print(f"Всего RFQ: {k['total']}  (A={k['deptA']} / B={k['outside']}),  ответственных: {k['respCount']}")
    print(f"В работе: {k['openCount']} ({k['inWorkPct']}%)  ·  КП из закрытых (A): {k['kpPctOfClosedA']}%  ·  до ТКП: {k['tkpPct']}%")
    print(f"Цепочка: ранние {m['chain']['early']} / ТКП {m['chain']['tkp']} / проигр. {m['chain']['lost']}")
    cov = m["coverage"]
    print(f"Покрытие сделок: {cov['withReq']}/{cov['deals']} ({cov['covPct']}%), ср. поставщиков {cov['avgSuppliers']}")
    print("\nБлок A по сорсерам:")
    for s in m["sourcersA"]:
        chk = s["closed"]
        agg = s["kp"] + s["refusedCol"] + s["noAnswer"]
        ok = "✓" if chk == agg else f"⚠ closed={chk}≠{agg}"
        print(f"  {s['n']:28} c={s['c']:4} closed={s['closed']:3} КП={s['kp']:3} отказ={s['refusedCol']:3} молч={s['noAnswer']:3} ТКП={s['tkpP']:3}% ср.срок={s['avgDays']} {ok}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Дашборд анализа работы сорсеров (КВАНТ · Bitrix24)")
    ap.add_argument("--period", help="YYYY-MM | YYYY-MM-DD:YYYY-MM-DD | YYYY-MM-DD (по умолч. — с 01.05.2026)")
    ap.add_argument("--out", default=str(config.BASE_DIR / "reports"), help="каталог отчётов")
    ap.add_argument("--no-llm", action="store_true", help="инсайты по правилам, без Claude")
    ap.add_argument("--dry-run", action="store_true", help="только метрики (JSON+сводка), без LLM и HTML")
    ap.add_argument("--open", action="store_true", help="открыть дашборд в браузере")
    ap.add_argument("--max-deals", type=int, default=None, help="ограничить число RFQ (для теста)")
    ap.add_argument("--as-of", help="переопределить «сегодня» (YYYY-MM-DD), для воспроизводимости")
    args = ap.parse_args()
    try:
        return run(args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"✗ Ошибка: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
