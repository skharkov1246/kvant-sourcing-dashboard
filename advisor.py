"""Вкладка «Советы знатока» — директорская аналитика поверх данных Bitrix.

Отвечает на три вопроса коммерческого директора:
  1. Сколько денег будет?   — взвешенный прогноз с открытого пайплайна + покрытие факта.
  2. Где теряем?            — причины проигрышей (по стадиям отказа) + velocity менеджеров.
  3. Где риски концентрации? — топ-клиенты, повторные vs новые, замолчавшие клиенты.

Модель реализации та же, что на «Пульсе» (company.py): сделка «в реализации», если у неё
есть заказ поставщику СП-172 или номер реализации в названии. Вероятности прогноза:
базовая — эмпирическая конверсия зрелой когорты-2025; для сделок, дошедших до ТКП,
вероятность повышается (эвристика ×3, потолок 60%) — честно помечено в методике.

Экзекьютив-справка генерируется по правилам (без LLM) — работает в CI с --no-llm.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from company import _money, _regno, _pctile
from stages import deal_reached_tkp

YEAR_START = "2026-01-01"
PREV_START = "2025-01-01"
TKP_PROB_CAP = 60          # потолок вероятности для «ТКП выдано+», %
TKP_PROB_MULT = 3.0        # множитель базовой конверсии для ТКП+
COVERAGE_NORM = 3.0        # здоровое покрытие: пайплайн ≥ 3 мес среднего факта
SILENT_MIN_EUR = 10_000    # «замолчавший клиент» — если выручка-2025 была ощутимой


def _median_days(pairs: list[int]) -> int | None:
    return _pctile(sorted(pairs), 50) if pairs else None


def compute(client, *, as_of: dt.date | None = None,
            deals: list[dict] | None = None, orders: list[dict] | None = None,
            deal_sale: dict | None = None, realize_date: dict | None = None,
            deal_stage_names: dict | None = None, category_names: dict | None = None) -> dict:
    today = as_of or dt.date.today()
    realize_date = realize_date or {}
    deal_stage_names = deal_stage_names or {}
    category_names = category_names or {}

    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    if deals is None:
        deals = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START},
            select=["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY",
                    "CURRENCY_ID", "DATE_CREATE", "CLOSEDATE", "ASSIGNED_BY_ID", "COMPANY_ID"])
    if orders is None:
        orders = client.list_items(172, filter={">=createdTime": YEAR_START + "T00:00:00"},
            select=["id", "stageId", "opportunity", "currencyId", "parentId2"])
        orders = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    if deal_sale is None:
        deal_sale = {str(d["ID"]): eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d in deals}
    order_parents = {str(o.get("parentId2")) for o in orders if o.get("parentId2")}

    def cls_of(d) -> str:
        did = str(d["ID"])
        if (d.get("STAGE_SEMANTIC_ID") or "").upper() == "F":
            return "lost"
        if did in order_parents or _regno(d.get("TITLE")) > 0:
            return "real"
        return "early"

    # ---- прошлый год: базовая конверсия зрелой когорты + клиентская история
    prev = client.list_deals_fast(
        filter={">=DATE_CREATE": PREV_START, "<DATE_CREATE": YEAR_START},
        select=["ID", "TITLE", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID", "COMPANY_ID"])
    prev_real = [d for d in prev
                 if str(d["ID"]) in realize_date or _regno(d.get("TITLE")) > 0]
    p_base = round(len(prev_real) / len(prev) * 100) if prev else 15
    p_base = max(3, min(p_base, 50))
    p_tkp = min(TKP_PROB_CAP, round(p_base * TKP_PROB_MULT))

    # ---- 1 · прогноз: взвешенный открытый пайплайн по месяцам планового закрытия
    open_deals = [d for d in deals if cls_of(d) == "early"]
    by_close: dict[str, dict] = defaultdict(lambda: {"raw": 0.0, "wgt": 0.0, "n": 0})
    no_date = {"raw": 0.0, "wgt": 0.0, "n": 0}
    raw_sum = wgt_sum = 0.0
    for d in open_deals:
        amt = deal_sale.get(str(d["ID"]), 0.0)
        tkp = deal_reached_tkp(d.get("STAGE_ID", ""), (d.get("STAGE_SEMANTIC_ID") or "").upper(),
                               deal_stage_names.get(d.get("STAGE_ID", "")))
        prob = (p_tkp if tkp else p_base) / 100.0
        raw_sum += amt; wgt_sum += amt * prob
        cd = str(d.get("CLOSEDATE") or "")[:7]
        bucket = by_close[cd] if cd and cd >= today.strftime("%Y-%m") else no_date
        bucket["raw"] += amt; bucket["wgt"] += amt * prob; bucket["n"] += 1
    MLBL = {1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
            7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек"}
    def _mlbl(ym):
        y, m = ym.split("-"); return f"{MLBL[int(m)]} '{y[2:]}"
    fc_months = [{"m": k, "label": _mlbl(k), "n": v["n"], "raw": round(v["raw"]),
                  "rawLbl": _money(v["raw"]), "wgt": round(v["wgt"]), "wgtLbl": _money(v["wgt"])}
                 for k, v in sorted(by_close.items())][:8]

    # средний месячный факт реализации-2026 (по месяцу перевода в реализацию)
    real_by_mon = Counter()
    for d in deals:
        did = str(d["ID"])
        if cls_of(d) != "real":
            continue
        rm = str(realize_date.get(did, ""))[:7]
        if rm.startswith("2026"):
            real_by_mon[rm] += deal_sale.get(did, 0.0)
    full_months = [m for m in real_by_mon if m < today.strftime("%Y-%m")]
    avg_real = (sum(real_by_mon[m] for m in full_months) / len(full_months)) if full_months else 0.0
    coverage = round(wgt_sum / avg_real, 1) if avg_real else None

    # ---- 2а · причины проигрышей: по стадиям отказа
    lost = [d for d in deals if cls_of(d) == "lost"]
    lost_sum = sum(deal_sale.get(str(d["ID"]), 0.0) for d in lost)
    reason_cnt: Counter = Counter(); reason_sum: dict[str, float] = defaultdict(float)
    for d in lost:
        nm = deal_stage_names.get(d.get("STAGE_ID", "")) or "стадия не определена"
        reason_cnt[nm] += 1
        reason_sum[nm] += deal_sale.get(str(d["ID"]), 0.0)
    reasons = [{"name": nm, "n": n, "pct": round(n / len(lost) * 100) if lost else 0,
                "sum": round(reason_sum[nm]), "sumLbl": _money(reason_sum[nm])}
               for nm, n in reason_cnt.most_common(8)]
    one_stage_pct = reasons[0]["pct"] if reasons else 0

    # ---- 2б · sales velocity по менеджерам
    unames = client.users()
    own: dict[str, dict] = defaultdict(lambda: {"created": 0, "open": 0, "pipe": 0.0,
                                                "real": 0, "sales": 0.0, "lost": 0, "cyc": []})
    for d in deals:
        uid = str(d.get("ASSIGNED_BY_ID"))
        o = own[uid]; o["created"] += 1
        c = cls_of(d); amt = deal_sale.get(str(d["ID"]), 0.0)
        if c == "early":
            o["open"] += 1; o["pipe"] += amt
        elif c == "lost":
            o["lost"] += 1
        else:
            o["real"] += 1; o["sales"] += amt
            rd, cd = str(realize_date.get(str(d["ID"]), ""))[:10], str(d.get("DATE_CREATE", ""))[:10]
            if rd and cd:
                try:
                    o["cyc"].append(max((dt.date.fromisoformat(rd) - dt.date.fromisoformat(cd)).days, 0))
                except ValueError:
                    pass
    velocity = []
    for uid, o in own.items():
        if o["created"] < 5:
            continue
        closed = o["real"] + o["lost"]
        wr = round(o["real"] / closed * 100) if closed else 0
        cyc = _median_days(o["cyc"])
        avg_check = o["sales"] / o["real"] if o["real"] else 0.0
        vel = (o["open"] * avg_check * (wr / 100.0) / max(cyc, 1) * 30) if (cyc is not None and o["real"]) else 0.0
        velocity.append({"uid": uid, "n": unames.get(uid, f"user#{uid}"), "created": o["created"],
                         "open": o["open"], "pipe": _money(o["pipe"]), "real": o["real"],
                         "sales": _money(o["sales"]), "wr": wr, "cyc": cyc,
                         "check": _money(avg_check) if o["real"] else "—",
                         "vel": round(vel), "velLbl": _money(vel) + "/мес" if vel else "—"})
    velocity.sort(key=lambda r: -r["vel"])
    velocity = velocity[:14]

    # ---- 3 · клиенты: концентрация, повторные vs новые, замолчавшие
    comp_prev_ids = {str(d.get("COMPANY_ID")) for d in prev if d.get("COMPANY_ID") and str(d.get("COMPANY_ID")) != "0"}
    prev_rev: dict[str, float] = defaultdict(float)
    for d in prev_real:
        cid = str(d.get("COMPANY_ID") or "")
        if cid and cid != "0":
            prev_rev[cid] += eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
    cur_rev: dict[str, float] = defaultdict(float)
    cur_comp_ids: set[str] = set()
    repeat_rev = new_rev = 0.0
    for d in deals:
        cid = str(d.get("COMPANY_ID") or "")
        if cid and cid != "0":
            cur_comp_ids.add(cid)
        if cls_of(d) != "real":
            continue
        amt = deal_sale.get(str(d["ID"]), 0.0)
        if cid and cid != "0":
            cur_rev[cid] += amt
        if cid in comp_prev_ids:
            repeat_rev += amt
        else:
            new_rev += amt
    total_rev = sum(cur_rev.values())
    top_ids = sorted(cur_rev, key=lambda c: -cur_rev[c])[:10]
    silent_ids = sorted((c for c in prev_rev
                         if prev_rev[c] >= SILENT_MIN_EUR and c not in cur_comp_ids),
                        key=lambda c: -prev_rev[c])[:8]
    cnames = client.companies_by_ids(set(top_ids) | set(silent_ids)) if (top_ids or silent_ids) else {}
    top_clients = [{"name": cnames.get(c, f"компания #{c}"), "rev": round(cur_rev[c]),
                    "revLbl": _money(cur_rev[c]),
                    "share": round(cur_rev[c] / total_rev * 100) if total_rev else 0,
                    "repeat": c in comp_prev_ids} for c in top_ids]
    top5_share = round(sum(cur_rev[c] for c in top_ids[:5]) / total_rev * 100) if total_rev else 0
    repeat_share = round(repeat_rev / (repeat_rev + new_rev) * 100) if (repeat_rev + new_rev) else 0
    silent = [{"name": cnames.get(c, f"компания #{c}"), "revLbl": _money(prev_rev[c])} for c in silent_ids]

    # ---- 4 · гигиена данных (что мешает верить цифрам)
    open_no_date = [d for d in open_deals if not str(d.get("CLOSEDATE") or "")[:10]]
    open_overdue = [d for d in open_deals
                    if str(d.get("CLOSEDATE") or "")[:10] and str(d["CLOSEDATE"])[:10] < today.isoformat()]
    open_zero = [d for d in open_deals if deal_sale.get(str(d["ID"]), 0.0) <= 0]
    def _hyg(label, items, cls):
        return {"lbl": label, "n": len(items),
                "sumLbl": _money(sum(deal_sale.get(str(d["ID"]), 0.0) for d in items)), "cls": cls}
    hygiene = [
        _hyg("Открытые сделки без плановой даты закрытия — прогноз по ним слепой", open_no_date, "warn"),
        _hyg("Открытые с прошедшим сроком — либо двигать, либо закрывать", open_overdue, "bad"),
        _hyg("Открытые с суммой €0 — не участвуют в пайплайне", open_zero, "warn"),
    ]

    # ---- экзекьютив-справка (правила, без LLM)
    brief: list[dict] = []
    def say(cls, html): brief.append({"cls": cls, "html": html})
    cov_txt = (f"покрывает <b>{coverage} мес</b> среднего факта (норма ≥ {COVERAGE_NORM:g})"
               if coverage is not None else "средний факт ещё не накоплен")
    say("ok" if (coverage or 0) >= COVERAGE_NORM else "warn",
        f"<b>Прогноз.</b> Открытый пайплайн {_money(raw_sum)} ({len(open_deals)} сделок), "
        f"взвешенно — <b>{_money(wgt_sum)}</b> (база {p_base}%, ТКП+ {p_tkp}%). Это {cov_txt}.")
    if velocity:
        lead, tail = velocity[0], velocity[-1]
        say("ok", f"<b>Velocity.</b> Лидер — <b>{lead['n']}</b> ({lead['velLbl']}); "
                  f"замыкает {tail['n']} ({tail['velLbl']}). Формула: открытые × ср. чек × win-rate ÷ цикл.")
    if reasons:
        r0 = reasons[0]
        say("warn" if one_stage_pct >= 80 else "ok",
            f"<b>Проигрыши.</b> {len(lost)} сделок на {_money(lost_sum)}. Топ-стадия отказа — "
            f"«{r0['name']}» ({r0['pct']}%)." + (" Причины почти не детализируются — "
            "ввести обязательное поле «причина проигрыша», иначе управлять нечем." if one_stage_pct >= 80 else ""))
    if total_rev:
        say("warn" if top5_share > 50 else "ok",
            f"<b>Концентрация.</b> Топ-5 клиентов дают <b>{top5_share}%</b> выручки"
            + (" — высокая зависимость, диверсифицировать." if top5_share > 50 else " — приемлемо.")
            + f" Повторные клиенты — {repeat_share}% выручки.")
    if silent:
        say("bad", f"<b>Замолчали.</b> {len(silent)} клиентов с выручкой-2025 без единой сделки в 2026 "
                   f"(крупнейший — {silent[0]['name']}, {silent[0]['revLbl']}). Передать КАМам на реактивацию.")
    nd = len(open_no_date)
    if nd:
        say("warn", f"<b>Гигиена.</b> {nd} открытых сделок без плановой даты закрытия — "
                    f"прогноз по месяцам для них невозможен ({_money(sum(deal_sale.get(str(d['ID']), 0.0) for d in open_no_date))}).")

    return {
        "label": today.strftime("%d.%m.%Y"),
        "brief": brief,
        "kpis": [
            {"lbl": "Взвешенный прогноз", "val": _money(wgt_sum), "meta": f"сырой пайплайн {_money(raw_sum)}", "clz": "amber"},
            {"lbl": "Покрытие факта", "val": (f"{coverage}×" if coverage is not None else "—"),
             "meta": f"мес. факт ~{_money(avg_real)}" if avg_real else "факт не накоплен",
             "clz": "ok" if (coverage or 0) >= COVERAGE_NORM else "warn"},
            {"lbl": "Топ-5 клиентов", "val": f"{top5_share}%", "meta": "доля выручки",
             "clz": "warn" if top5_share > 50 else "ok"},
            {"lbl": "Повторная выручка", "val": f"{repeat_share}%", "meta": f"новые клиенты {100 - repeat_share}%", "clz": "teal"},
            {"lbl": "Проиграно", "val": _money(lost_sum), "meta": f"{len(lost)} сделок", "clz": "warn"},
            {"lbl": "Замолчавших клиентов", "val": str(len(silent)), "meta": f"выручка-2025 ≥ {_money(SILENT_MIN_EUR)}",
             "clz": "warn" if silent else "ok"},
        ],
        "forecast": {"pBase": p_base, "pTkp": p_tkp, "openN": len(open_deals),
                     "rawLbl": _money(raw_sum), "wgtLbl": _money(wgt_sum),
                     "byMonth": fc_months,
                     "noDate": {"n": no_date["n"], "rawLbl": _money(no_date["raw"]), "wgtLbl": _money(no_date["wgt"])},
                     "avgRealLbl": _money(avg_real) if avg_real else None, "coverage": coverage},
        "velocity": velocity,
        "lost": {"n": len(lost), "sumLbl": _money(lost_sum), "reasons": reasons, "oneStagePct": one_stage_pct},
        "clients": {"top": top_clients, "top5Share": top5_share, "repeatShare": repeat_share,
                    "repeatLbl": _money(repeat_rev), "newLbl": _money(new_rev), "silent": silent},
        "hygiene": hygiene,
    }
