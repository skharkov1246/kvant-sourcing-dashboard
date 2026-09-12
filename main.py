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
import os
import sys
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

import advisor as advisor_mod
import company as company_mod
import config
import dashboard
import insights as insights_mod
import kam as kam_mod
import contracts as contracts_mod
import metrics as metrics_mod
import period as period_mod
import reps as reps_mod
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

_TAG_RE = __import__("re").compile(r"(?is)<(script|style)[^>]*>.*?</\1>|<[^>]+>")
_WS_RE = __import__("re").compile(r"\s+")
_ENT = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&mdash;": "—"}


def _email_preview(desc: str, limit: int = 220) -> str:
    """HTML/текст письма → компактное превью без тегов."""
    s = _TAG_RE.sub(" ", desc or "")
    for a, b in _ENT.items():
        s = s.replace(a, b)
    s = _WS_RE.sub(" ", s).strip()
    return s[:limit].rstrip() + ("…" if len(s) > limit else "")


def _email_to(settings) -> str:
    """Получатель письма из SETTINGS.EMAIL_META.to (если есть)."""
    try:
        meta = settings.get("EMAIL_META") if isinstance(settings, dict) else None
        if isinstance(meta, dict):
            to = meta.get("to") or meta.get("__to") or ""
            return str(to).strip()
    except Exception:
        pass
    return ""


def _has_attachment(a: dict) -> bool:
    for key in ("FILES", "STORAGE_ELEMENT_IDS"):
        v = a.get(key)
        if isinstance(v, (list, dict)) and len(v) > 0:
            return True
        if isinstance(v, str) and v not in ("", "0", "[]", "a:0:{}"):
            return True
    return False


def _send_stats(client: BitrixClient, rfqs: list[dict], sourcer_rows: list[dict],
                dept_a_ids: set[str], start_iso: str, end_iso: str) -> dict:
    """Реально отправлено vs создано: по факту исходящего CRM-письма на карточке RFQ.
    Дополнительно собирает по каждой карточке письма (тема · получатель · превью · вложение)
    для хронологического списка в дровере сорсера.
    Возвращает {"rows": [...], "totals": {...}, "byCard": {card_id: [emails...]}}."""
    counts: Counter = Counter()  # card_id -> кол-во исходящих писем
    by_card: dict[str, list[dict]] = defaultdict(list)
    last = 0
    while True:
        ch = client.call("crm.activity.list", {
            "filter": {"OWNER_TYPE_ID": config.SPA_ENTITY_TYPE_ID, "PROVIDER_ID": "CRM_EMAIL",
                       "DIRECTION": 2, ">=CREATED": start_iso, "<=CREATED": end_iso, ">ID": last},
            "select": ["ID", "OWNER_ID", "SUBJECT", "DESCRIPTION", "SETTINGS",
                       "FILES", "STORAGE_ELEMENT_IDS", "CREATED"],
            "order": {"ID": "ASC"}, "start": -1}) or []
        if not ch:
            break
        for a in ch:
            cid = str(a.get("OWNER_ID"))
            counts[cid] += 1
            by_card[cid].append({
                "subj": (a.get("SUBJECT") or "").strip(),
                "to": _email_to(a.get("SETTINGS")),
                "body": _email_preview(a.get("DESCRIPTION")),
                "file": _has_attachment(a),
                "dt": (a.get("CREATED") or "")[:16].replace("T", " "),
                "dtx": a.get("CREATED") or "",
            })
        last = int(ch[-1]["ID"])
        if len(ch) < 50:
            break
    # письма каждой карточки — по времени, новые сверху
    for cid in by_card:
        by_card[cid].sort(key=lambda e: e["dtx"], reverse=True)

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
    return {"rows": rows, "totals": tot, "byCard": {c: v for c, v in by_card.items()}}


class DataGateError(RuntimeError):
    """Выгрузка не прошла проверку достаточности — собирать дашборд нельзя."""


def _sanity_gates(p, rfqs, period_deals, dept_a_ids, sourcers_a, *, skip: bool = False) -> None:
    """Проверка, что данные из Bitrix пришли, а не «пустой, но валидный» ответ.

    Разбор падений: при частичной выгрузке пайплайн собирал корректный HTML с нулями
    во всех KPI и деплоил его в прод. Здесь прогон останавливается ДО генерации, поэтому
    на сайте остаётся предыдущая рабочая версия.

    Пороги масштабируются длиной периода и переопределяются переменными окружения
    SOURCING_MIN_RFQ / SOURCING_MIN_DEALS / SOURCING_MIN_SOURCERS; отключение — --allow-empty.
    """
    if skip:
        print("  ⚠ проверки достаточности отключены (--allow-empty или --max-deals)")
        return
    scale = min(1.0, max(p.days, 1) / 30.0)          # для коротких периодов пороги ниже
    min_rfq = max(1, int(int(os.getenv("SOURCING_MIN_RFQ") or 20) * scale))
    min_deals = max(1, int(int(os.getenv("SOURCING_MIN_DEALS") or 20) * scale))
    min_sourcers = max(1, int(int(os.getenv("SOURCING_MIN_SOURCERS") or 3) * scale))

    problems = []
    if not dept_a_ids:
        problems.append(f"отдел {config.DEPT_SOURCING_ID} не вернул ни одного сотрудника")
    if len(rfqs) < min_rfq:
        problems.append(f"RFQ {len(rfqs)} < порога {min_rfq}")
    if len(period_deals) < min_deals:
        problems.append(f"сделок периода {len(period_deals)} < порога {min_deals}")
    if len(sourcers_a) < min_sourcers:
        problems.append(f"сорсеров с активностью {len(sourcers_a)} < порога {min_sourcers}")
    if problems:
        raise DataGateError(
            "данные Bitrix не прошли проверку достаточности:\n    - " + "\n    - ".join(problems)
            + "\n  Дашборд НЕ собран, прежняя версия на сайте не тронута."
            + "\n  Если период действительно пустой — запустите с --allow-empty."
        )
    print(f"  ✓ проверка данных пройдена: RFQ {len(rfqs)}, сделок {len(period_deals)}, "
          f"сорсеров {len(sourcers_a)}, отдел {len(dept_a_ids)} чел.")


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
    period_deals = client.deals_in_period(p.start_iso, p.end_iso, select=[
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE",
        "ASSIGNED_BY_ID", "COMPANY_ID", "OPPORTUNITY", "CURRENCY_ID"])
    print(f"  сделок периода: {len(period_deals)}")

    print("• Поставщики по RFQ (компании/контакты)…")
    _attach_suppliers(client, rfqs)

    print("• Расчёт метрик…")
    m = metrics_mod.build(
        p, rfqs, deal_index, period_deals, dept_a_ids,
        names, since, deal_stage_names, category_names,
    )

    _sanity_gates(p, rfqs, period_deals, dept_a_ids, m.get("sourcersA") or [],
                  skip=bool(args.allow_empty or args.max_deals))

    print("• Отправлено vs создано (письма)…")
    m["send"] = _send_stats(client, rfqs, m["sourcersA"], dept_a_ids, p.start_iso, p.end_iso)

    # обогащаем детали каждого сорсера письмами (тема · получатель · превью · вложение)
    _by_card = m["send"].pop("byCard", {})
    _mail_cards = 0
    for s in m["sourcersA"]:
        for d in s.get("details", []):
            mails = _by_card.get(str(d.get("id"))) or []
            d["mailN"] = len(mails)
            if mails:
                top = mails[0]                       # последнее письмо карточки
                d["mailSubj"] = top["subj"]
                d["to"] = top["to"]
                d["body"] = top["body"]
                d["file"] = top["file"]
                d["mailDt"] = top["dt"]
                _mail_cards += 1
    print(f"  писем привязано к карточкам: {_mail_cards}")

    # список непокрытых сделок: только там, где запрос ОЖИДАЕТСЯ — живые оценённые сделки
    # (есть сумма, статус ≠ F, не сделка-RFQ сорсинга). €0/БО/отказы/отмены — «запрос не требуется».
    # (предикат должен совпадать с metrics._req_expected)
    _exp = lambda d: (float(d.get("OPPORTUNITY") or 0) > 0
                      and (d.get("STAGE_SEMANTIC_ID") or "").upper() != "F"
                      and not str(d.get("TITLE") or "").strip().upper().startswith("RFQ")
                      and "test" not in str(d.get("TITLE") or "").lower()
                      and "тест" not in str(d.get("TITLE") or "").lower())
    try:
        _rfqpar = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
        _unc = [d for d in period_deals if str(d["ID"]) not in _rfqpar and _exp(d)]
        _cur = client.call("crm.currency.list", {}) or []
        _rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in _cur}
        _eur = lambda o, cu: float(o or 0) * _rate.get(cu, 1.0)
        _mfmt = lambda v: (f"€{v/1e6:.1f}M" if abs(v) >= 1e6 else f"€{round(v/1e3)}K" if abs(v) >= 1e3 else f"€{round(v)}")
        _users = client.users()
        _cnames = client.companies_by_ids({str(d.get("COMPANY_ID")) for d in _unc if d.get("COMPANY_ID") and str(d.get("COMPANY_ID")) != "0"})
        _unc_rows = []
        for d in _unc:
            amt = _eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
            sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
            _unc_rows.append({
                "id": str(d["ID"]), "t": (d.get("TITLE") or f'Сделка #{d["ID"]}')[:90],
                "client": _cnames.get(str(d.get("COMPANY_ID")), "—") or "—",
                "owner": _users.get(str(d.get("ASSIGNED_BY_ID")), "—"),
                "raw": round(amt), "amt": _mfmt(amt),
                "cat": category_names.get(str(d.get("CATEGORY_ID")), ""),
                "date": str(d.get("DATE_CREATE", ""))[:10],
                "sem": "проиграна" if sem == "F" else ("выиграна" if sem == "S" else "в работе"),
            })
        _unc_rows.sort(key=lambda r: -r["raw"])
        m["coverage"]["uncovered"] = _unc_rows
        # недельная динамика непокрытых: по неделе СОЗДАНИЯ сделки (Пн–Вс), свежие сверху
        _MONS = {1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
                 7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек"}
        def _mon_of(s):
            try:
                d = dt.date.fromisoformat(str(s)[:10]); return d - dt.timedelta(days=d.weekday())
            except Exception:
                return None
        _wk_created = Counter()
        for d in period_deals:
            if not _exp(d):            # знаменатель недели — только сделки, где запрос ожидается
                continue
            mo = _mon_of(d.get("DATE_CREATE"))
            if mo:
                _wk_created[mo] += 1
        _wk_uncov = Counter()
        for r in _unc_rows:
            mo = _mon_of(r["date"])
            if mo:
                _wk_uncov[mo] += 1
                r["wk"] = mo.isoformat()
        _weekly = []
        for mo in sorted(_wk_created, reverse=True):
            created = _wk_created[mo]; uncov = _wk_uncov.get(mo, 0); end = mo + dt.timedelta(days=6)
            label = (f"{mo.day}–{end.day} {_MONS[end.month]}" if mo.month == end.month
                     else f"{mo.day} {_MONS[mo.month]} – {end.day} {_MONS[end.month]}")
            _weekly.append({"key": mo.isoformat(), "label": label, "created": created,
                            "uncov": uncov, "uncovPct": round(uncov / created * 100) if created else 0})
        m["coverage"]["weekly"] = _weekly
        print(f"  непокрытых сделок (без запросов поставщикам): {len(_unc_rows)} | недель: {len(_weekly)}")
    except Exception as e:
        print(f"  ⚠ список непокрытых пропущен: {type(e).__name__}: {e}")

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

    company_data = kam_data = eng_data = prod_data = reps_data = None
    deals_ytd = orders_ytd = deal_owner = deal_sale = None
    # --- общий пул сделок/заказов YTD (нужен Пульсу + отраслевым вкладкам) ---
    try:
        print("• Общий пул сделок/заказов (YTD)…")
        ys = "2026-01-01T00:00:00"
        deals_ytd = client.list_deals_fast(filter={">=DATE_CREATE": ys},
            select=["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "CLOSEDATE", "ASSIGNED_BY_ID", "COMPANY_ID"])
        orders_ytd = client.list_items(172, filter={">=createdTime": ys},
            select=["id", "title", "stageId", "opportunity", "currencyId", "createdTime", "parentId2", "assignedById"])
        # заказы СП-172: исключаем проигранные (…:FAIL) — это не контрактная выручка
        orders_ytd = [o for o in orders_ytd if not str(o.get("stageId", "")).endswith(":FAIL")]
        # курсы → база € (для продаж сделок)
        _cur = client.call("crm.currency.list", {}) or []
        _rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in _cur}
        _eur = lambda o, cu: float(o or 0) * _rate.get(cu, 1.0)
        # полные карты по сделкам: владелец + сумма продажи (€). 2026 + родители заказов старше 2026.
        # (иначе ~70% заказов атрибутируются по assignedById карточки заказа, а не по владельцу сделки;
        #  а продажа = сумма сделки, а НЕ opportunity заказа СП-172, который = закупка у поставщика)
        deal_owner = {str(d["ID"]): str(d.get("ASSIGNED_BY_ID")) for d in deals_ytd}
        deal_sale = {str(d["ID"]): _eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d in deals_ytd}
        parent_ids = {str(o.get("parentId2")) for o in orders_ytd if o.get("parentId2")}
        missing = [pid for pid in parent_ids if pid and pid not in deal_owner]
        if missing:
            extra = client.deals_by_ids(missing, select=["ID", "ASSIGNED_BY_ID", "OPPORTUNITY", "CURRENCY_ID"])
            for did, d in extra.items():
                deal_owner[str(did)] = str(d.get("ASSIGNED_BY_ID"))
                deal_sale[str(did)] = _eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
            print(f"  родительские сделки заказов: догружено {len(extra)} (из {len(missing)})")
        # КАМ — по КЛИЕНТУ (холдингу), не по отделу: компания сделки → направление
        _comp_ids = {str(d.get("COMPANY_ID")) for d in deals_ytd if d.get("COMPANY_ID") and str(d.get("COMPANY_ID")) != "0"}
        _cnames = client.companies_by_ids(_comp_ids)
        deal_group = {str(d["ID"]): kam_mod.client_dir(_cnames.get(str(d.get("COMPANY_ID")), "")) for d in deals_ytd}
        print(f"  клиентских направлений КАМ: {len(set(deal_group.values()))}")
    except Exception as e:
        deal_group = None
        print(f"  ⚠ общий пул не получен — Пульс/КАМ/Инж/Продукт пропущены: {type(e).__name__}: {e}")

    # момент перевода сделок в реализацию (вход в воронку кат.0) — выгружаем ОДИН раз, шарим
    realize_date = None
    try:
        print("• История стадий: момент перевода в реализацию (для Пульса/Когорт/Коммерсантов)…")
        realize_date = client.stage_first_entry(2, 0, "2025-01-01T00:00:00")
        print(f"  вошли в реализацию (с 2025): {len(realize_date)} сделок")
    except Exception as e:
        print(f"  ⚠ история стадий пропущена: {type(e).__name__}: {e}")

    # --- каждая вкладка изолирована: сбой одной не гасит остальные ---
    if deals_ytd is not None:
        try:
            company_data = company_mod.compute(client, as_of=p.end, created=deals_ytd, orders=orders_ytd,
                                               deal_sale=deal_sale, realize_date=realize_date)
            print("  ✓ Пульс компании")
        except Exception as e:
            print(f"  ⚠ Пульс компании пропущен: {type(e).__name__}: {e}")
        try:
            kam_data = kam_mod.compute_set(client, None, as_of=p.end, created=deals_ytd, orders=orders_ytd,
                                           with_people=True, deal_sale=deal_sale, deal_group=deal_group)
            print("  ✓ КАМы (по клиенту)")
        except Exception as e:
            print(f"  ⚠ КАМы пропущены: {type(e).__name__}: {e}")
        for label, groups, key in (("Инжиниринг", kam_mod.ENG_GROUPS, "eng"),
                                    ("Продукт", kam_mod.PRODUCT_GROUPS, "prod")):
            try:
                data = kam_mod.compute_set(client, groups, as_of=p.end, created=deals_ytd, orders=orders_ytd,
                                           with_people=True, deal_owner=deal_owner, deal_sale=deal_sale)
                if key == "kam": kam_data = data
                elif key == "eng": eng_data = data
                else: prod_data = data
                print(f"  ✓ {label}")
            except Exception as e:
                print(f"  ⚠ {label} пропущена: {type(e).__name__}: {e}")

    contracts_data = None
    try:
        print("• Контракты в реализации (СП-172, все непроигранные)…")
        contracts_data = contracts_mod.compute(client, as_of=p.end)
        print(f"  ✓ контрактов: {len(contracts_data['rows'])}")
    except Exception as e:
        print(f"  ⚠ вкладка «Контракты» пропущена: {type(e).__name__}: {e}")

    try:
        print("• Коммерсанты (персональные дашборды + контрольные точки)…")
        reps_data = reps_mod.compute(client, realize_date=realize_date, as_of=p.end)
        print(f"  ✓ коммерсантов: {len(reps_data['reps'])}")
    except Exception as e:
        print(f"  ⚠ вкладка «Коммерсанты» пропущена: {type(e).__name__}: {e}")

    advisor_data = None
    if deals_ytd is not None:
        try:
            print("• Советы знатока (прогноз · потери · риски концентрации)…")
            advisor_data = advisor_mod.compute(
                client, as_of=p.end, deals=deals_ytd, orders=orders_ytd,
                deal_sale=deal_sale, realize_date=realize_date,
                deal_stage_names=deal_stage_names, category_names=category_names)
            print(f"  ✓ советы знатока: справка из {len(advisor_data['brief'])} пунктов")
        except Exception as e:
            print(f"  ⚠ вкладка «Советы знатока» пропущена: {type(e).__name__}: {e}")

    html_path = out_dir / f"dashboard_{slug}.html"
    dashboard.write(m, ins, html_path, title=f"Сорсинг · {p.label}",
                    company=company_data, kam=kam_data, eng=eng_data, prod=prod_data,
                    contracts=contracts_data, reps=reps_data, advisor=advisor_data)
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
    ap.add_argument("--allow-empty", action="store_true",
                    help="не останавливаться, если данных из Bitrix мало (период действительно пустой)")
    args = ap.parse_args()
    try:
        return run(args)
    except SystemExit:
        raise
    except DataGateError as e:
        # код 2 = «данные не прошли проверку»: отличается от краха кода (1),
        # чтобы CI мог показать понятную причину и не деплоить.
        print(f"✗ {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"✗ Ошибка: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
