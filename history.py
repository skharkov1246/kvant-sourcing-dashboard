"""Аналитика истории сделок: год закупочных процедур — где именно проигрываем.

Считает по живому порталу и возвращает готовый разбор (словарь) для отчётов.
В дашборд не подключён: это аналитический модуль, а не вкладка.

Вопрос владельца: какие у нас слабые зоны в проработке конкурсов и в сделках,
которые мы проигрываем.

МОДЕЛЬ ИСХОДА. В этом портале победа — это ПЕРЕЕЗД карточки в воронку
реализации (кат. 0): сделка сохраняет ID, история стадий продолжается, а
стадия-победа («Tender won», SEMANTICS='S') почти не ставится. Поэтому:
  * воронка сделки берётся не текущая, а ТА, ГДЕ ОНА ЗАВЕДЕНА — первая
    в истории стадий (иначе выигранные тендеры пропадают из своей воронки
    и конверсия по ней выходит нулевой);
  * победой считается факт появления кат. 0 в истории стадий.
Проверено на портале 08.09.2026: пути карточек 4→0, 8→0, 30→0.

ПРИЧИНА ПРОИГРЫША. Отдельного поля в портале нет — причина закодирована
именем F-стадии, на которой сделка остановилась («Не прошли по цене»,
«Пост-щик не ответил/не прислал ТКП», «Отмена Заказчиком закупки»).
Считаем по ним, честно называя это стадией остановки, а не «причиной».

ТИХИЕ ПОТЕРИ. Медиана жизни проигранной сделки — 27 дней, выигранной — 149.
Сделка, простоявшая в работе дольше порога и не доехавшая до реализации,
практически потеряна, хотя формально числится живой. Считаем отдельно.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from bitrix_client import BitrixClient

YEAR_DAYS = 365
STUCK_DAYS = 120        # порог «тихой потери»: 90-й перцентиль жизни проигранных
MIN_DEALS_FUNNEL = 10   # воронки мельче в таблицу не выводим — статистики нет
MIN_DEALS_OWNER = 25
MIN_DEALS_CLIENT = 15
TOP_ROWS = 15
OUTLIER_EUR = 20_000_000  # выше этого сумма в карточке почти всегда ошибка ввода:
                          # в годовой выборке такие единичны, но перетягивают итог

DSEL = ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY",
        "CURRENCY_ID", "DATE_CREATE", "CLOSEDATE", "ASSIGNED_BY_ID", "COMPANY_ID"]


def _date(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _cat_of_stage(stage_id: str) -> str:
    """Воронка, закодированная в идентификаторе стадии: 'C2:NEW' → '2', 'NEW' → '0'."""
    s = str(stage_id or "")
    if s.startswith("C") and ":" in s:
        head = s[1:s.index(":")]
        return head if head.isdigit() else "0"
    return "0"


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return v[len(v) // 2]


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def compute(client: BitrixClient, *, as_of: dt.date | None = None, days: int = YEAR_DAYS) -> dict:
    as_of = as_of or dt.date.today()
    start = as_of - dt.timedelta(days=days)
    since_iso = f"{start.isoformat()}T00:00:00"

    deals = client.list_deals_fast(filter={">=DATE_CREATE": since_iso}, select=DSEL)
    if not deals:
        return {"empty": True, "period": {"from": start.isoformat(), "to": as_of.isoformat()}}

    stages = client.deal_stage_meta()
    cats = client.categories()
    users = client.users()
    hist = client.stage_history(2, since=since_iso)

    comp_ids = {str(d.get("COMPANY_ID")) for d in deals if d.get("COMPANY_ID") and str(d["COMPANY_ID"]) != "0"}
    comps = client.companies_by_ids(comp_ids) if comp_ids else {}
    curlist = client.call("crm.currency.list", {}) or []
    rates = {x.get("CURRENCY"): float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)
             for x in curlist}

    def eur(d: dict) -> float:
        """Сумма в базовой валюте портала — это ЕВРО (BASE=Y стоит у EUR в crm.currency.list).
        Курс AMOUNT/AMOUNT_CNT переводит валюту сделки в базовую."""
        v = float(d.get("OPPORTUNITY") or 0)
        return v * float(rates.get(d.get("CURRENCY_ID") or "EUR", 1))

    # --- исход и воронка происхождения по истории стадий
    origin: dict[str, str] = {}
    won_at: dict[str, str] = {}
    for did, rows in hist.items():
        rows = sorted(rows, key=lambda r: r[1])
        if rows:
            origin[did] = _cat_of_stage(rows[0][0])
        for sid, at in rows:
            if _cat_of_stage(sid) == "0" and did not in won_at:
                won_at[did] = str(at)[:10]

    for d in deals:
        did = str(d["ID"])
        origin.setdefault(did, str(d.get("CATEGORY_ID")))

    by_origin: dict[str, list[dict]] = defaultdict(list)
    for d in deals:
        by_origin[origin[str(d["ID"])]].append(d)

    # --- 1. исход по воронке, где сделка заведена
    funnels = []
    for cid, ds in by_origin.items():
        won = [d for d in ds if str(d["ID"]) in won_at]
        lags = []
        for d in won:
            a, b = _date(d["DATE_CREATE"]), _date(won_at[str(d["ID"])])
            if a and b and b >= a:
                lags.append((b - a).days)
        funnels.append({
            "cat": cats.get(cid, cid), "cat_id": cid, "n": len(ds), "won": len(won),
            "pct": _pct(len(won), len(ds)), "median_days": _median(lags),
            "sum": round(sum(eur(d) for d in ds)), "sum_won": round(sum(eur(d) for d in won)),
        })
    funnels = [f for f in funnels if f["n"] >= MIN_DEALS_FUNNEL]
    funnels.sort(key=lambda f: -f["n"])

    # --- 2. воронка конкурсов: сколько дошло до каждой стадии
    tender_cat = next((cid for cid, name in cats.items() if "тендер" in str(name).lower()), "2")
    tenders = by_origin.get(tender_cat, [])
    tender_stages = sorted(
        [(sid, m) for sid, m in stages.items() if m["cat"] == tender_cat and m["sem"] != "F"],
        key=lambda x: x[1]["sort"])
    reached = Counter()
    for d in tenders:
        seen = {sid for sid, _at in hist.get(str(d["ID"]), [])}
        for sid, _m in tender_stages:
            if sid in seen:
                reached[sid] += 1
    conv = [{"stage": m["name"], "n": reached[sid], "pct": _pct(reached[sid], len(tenders))}
            for sid, m in tender_stages]

    # --- 3. на каких стадиях останавливаются конкурсы
    stops = Counter()
    stop_sum: dict[str, float] = defaultdict(float)
    lost_tenders = [d for d in tenders if str(d["ID"]) not in won_at]
    for d in lost_tenders:
        sid = str(d.get("STAGE_ID"))
        stops[sid] += 1
        stop_sum[sid] += eur(d)
    tender_stops = [{"stage": (stages.get(sid) or {}).get("name", sid),
                     "sem": (stages.get(sid) or {}).get("sem", "P"),
                     "n": n, "pct": _pct(n, len(lost_tenders)), "sum": round(stop_sum[sid])}
                    for sid, n in stops.most_common(TOP_ROWS)]

    # --- 4. где теряются деньги: закрытые проигрышем, все воронки
    money = defaultdict(lambda: {"n": 0, "sum": 0.0})
    for d in deals:
        sid = str(d.get("STAGE_ID"))
        if (stages.get(sid) or {}).get("sem") != "F" or str(d["ID"]) in won_at:
            continue
        key = (origin[str(d["ID"])], sid)
        money[key]["n"] += 1
        money[key]["sum"] += eur(d)
    money_lost = sorted(
        [{"cat": cats.get(k[0], k[0]), "stage": (stages.get(k[1]) or {}).get("name", k[1]),
          "n": v["n"], "sum": round(v["sum"])} for k, v in money.items()],
        key=lambda r: -r["sum"])[:TOP_ROWS]

    # --- 5. ответственные и клиенты
    def group(key_fn, names, min_n):
        acc = defaultdict(lambda: {"n": 0, "won": 0, "sum": 0.0})
        for d in deals:
            k = key_fn(d)
            if not k:
                continue
            acc[k]["n"] += 1
            acc[k]["won"] += 1 if str(d["ID"]) in won_at else 0
            acc[k]["sum"] += eur(d)
        out = [{"name": names.get(k, k), "n": v["n"], "won": v["won"],
                "pct": _pct(v["won"], v["n"]), "sum": round(v["sum"])}
               for k, v in acc.items() if v["n"] >= min_n]
        return sorted(out, key=lambda r: -r["n"])[:TOP_ROWS]

    owners = group(lambda d: str(d.get("ASSIGNED_BY_ID") or ""), users, MIN_DEALS_OWNER)
    clients = group(lambda d: str(d.get("COMPANY_ID") or ""), comps, MIN_DEALS_CLIENT)
    clients.sort(key=lambda r: -r["sum"])

    # --- 6. скорость и тихие потери
    def last_move(did: str) -> dt.date | None:
        rows = hist.get(did) or []
        return _date(max((r[1] for r in rows), default=None))

    speed = []
    for label, sel in (
        ("дошли до реализации", lambda d: str(d["ID"]) in won_at),
        ("проиграны", lambda d: (stages.get(str(d.get("STAGE_ID"))) or {}).get("sem") == "F"
         and str(d["ID"]) not in won_at),
        ("в работе", lambda d: (stages.get(str(d.get("STAGE_ID"))) or {}).get("sem") == "P"
         and str(d["ID"]) not in won_at),
    ):
        lives = []
        for d in deals:
            if not sel(d):
                continue
            a = _date(d["DATE_CREATE"])
            if a:
                lives.append(((last_move(str(d["ID"])) or as_of) - a).days)
        speed.append({"kind": label, "n": len(lives), "median_days": _median(lives)})

    stuck = []
    for cid, ds in by_origin.items():
        rows = [d for d in ds
                if (stages.get(str(d.get("STAGE_ID"))) or {}).get("sem") == "P"
                and str(d["ID"]) not in won_at
                and (_date(d["DATE_CREATE"]) and (as_of - _date(d["DATE_CREATE"])).days > STUCK_DAYS)]
        if rows:
            stuck.append({"cat": cats.get(cid, cid), "n": len(rows), "of": len(ds),
                          "pct": _pct(len(rows), len(ds)), "sum": round(sum(eur(d) for d in rows))})
    stuck.sort(key=lambda r: -r["sum"])

    # --- выбросы: суммы в карточках вводятся руками и иногда завышены на порядки.
    # Одна сделка с ошибкой ввода перетягивает годовой итог, поэтому кроме суммы
    # всегда показываем медиану и отдельно перечисляем подозрительные карточки.
    won_total = sum(1 for d in deals if str(d["ID"]) in won_at)
    amounts = sorted((eur(d) for d in deals if eur(d) > 0), reverse=True)
    med_amount = _median(amounts) or 0
    outliers = [{"title": str(d.get("TITLE"))[:80], "sum": round(eur(d)),
                 "orig": round(float(d.get("OPPORTUNITY") or 0)), "cur": d.get("CURRENCY_ID"),
                 "cat": cats.get(origin[str(d["ID"])], "")}
                for d in deals if eur(d) > OUTLIER_EUR]
    outliers.sort(key=lambda r: -r["sum"])
    sum_all = sum(eur(d) for d in deals)
    sum_clean = sum_all - sum(o["sum"] for o in outliers)
    return {
        "empty": False,
        "period": {"from": start.isoformat(), "to": as_of.isoformat(), "days": days},
        "totals": {"deals": len(deals), "sum": round(sum_all), "sum_clean": round(sum_clean),
                   "median": round(med_amount), "won": won_total, "pct": _pct(won_total, len(deals)),
                   "with_history": len(hist), "currency": "EUR"},
        "outliers": {"threshold": OUTLIER_EUR, "rows": outliers[:TOP_ROWS]},
        "funnels": funnels,
        "tender": {"cat": cats.get(tender_cat, tender_cat), "n": len(tenders),
                   "won": len(tenders) - len(lost_tenders), "conv": conv, "stops": tender_stops},
        "money_lost": money_lost,
        "owners": owners,
        "clients": clients,
        "speed": speed,
        "stuck": {"threshold_days": STUCK_DAYS, "rows": stuck[:TOP_ROWS]},
    }
