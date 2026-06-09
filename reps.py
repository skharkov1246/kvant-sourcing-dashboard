"""Вкладка «Коммерсанты» — персональные дашборды менеджеров с контрольными точками.

Считает по 4 коммерческим сотрудникам (можно расширить) измеримые KPI:
  ОБЪЁМ:    активных сделок + Σ пайплайн (€); создано (портфель с 2025).
  РЕЗУЛЬТАТ: в реализации (шт) + конверсия %; продажи (€); маржа (€); средний чек.
  КАЧЕСТВО: проиграно (шт); win-rate = выиграно ÷ (выиграно+проиграно).
  СКОРОСТЬ: ср. срок создание→реализация (дней, из истории стадий).
  РИСК:     просрочено (открытые, срок прошёл) шт+€; застряло (без движения >30 дн) шт.
КОНТРОЛЬНЫЕ ТОЧКИ (воронка по сотруднику): Создано → ТКП выдано → В реализации →
  Договор подписан → Отгружено → Завершено (+ Проиграно), с конверсиями между точками.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

from bitrix_client import BitrixClient

COHORT_START = "2025-01-01T00:00:00"

# кого показываем (имя для вкладки → паттерн поиска в имени пользователя Bitrix)
REPS = [
    ("Полупанов", r"polupanov"),
    ("Щуренков", r"shchurenkov|schurenkov|shurenkov"),
    ("Ситдиков", r"sitdikov"),
    ("Зорин", r"\bzorin\b"),
    ("Володин", r"volodin"),
    ("Черкасов", r"cherkas"),
]

# контрольные точки (монотонная шкала уровней 0..5)
CHECKPOINTS = ["Создано", "ТКП выдано", "В реализации", "Договор подписан", "Отгружено", "Завершено"]
# стадии базовой воронки реализации (категория 0) → уровень
CAT0_LEVEL = {"NEW": 2, "FINAL_INVOICE": 3, "UC_YHGPBY": 3, "UC_Q3DI3H": 3, "UC_MT3AJ9": 4, "WON": 5}
MLBL = {1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
        7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек"}
_QUOTE_RE = re.compile(r"ТКП\s*(выда|отправ|соглас)|тендерн\w*\s+предложен\w*\s+выда|quotation\s+issued|EXECUTING", re.I)
_FAIL_RE = re.compile(r"не\s*состоял|отказ|не\s*прошл|не\s*подош|не\s*ответил|закрыл выдачу|fail|lost|потерян", re.I)


def _money(v: float) -> str:
    v = round(v); s = "-" if v < 0 else ""; a = abs(v)
    if a >= 1_000_000:
        return f"{s}€{a/1_000_000:.1f}M"
    if a >= 1_000:
        return f"{s}€{a/1_000:.0f}K"
    return f"{s}€{a}"


def _regno(title) -> int:
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(title or ""))
    return int(m.group(1)) if m else 0


def _pctile(sv: list, p: float):
    if not sv:
        return None
    k = (len(sv) - 1) * p / 100.0; f = int(k); c = min(f + 1, len(sv) - 1)
    return round(sv[f] + (sv[c] - sv[f]) * (k - f))


def compute(client: BitrixClient, *, realize_date: dict | None = None, as_of: dt.date | None = None) -> dict:
    today = as_of or dt.date.today()
    today_iso = today.isoformat()
    unames = client.users()

    # сопоставляем имена → id
    rep_ids = {}
    for label, pat in REPS:
        rx = re.compile(pat, re.I)
        uid = next((u for u, n in unames.items() if rx.search(n or "")), None)
        if uid:
            rep_ids[label] = str(uid)
    ids = list(rep_ids.values())
    if not ids:
        return {"reps": [], "labels": [l for l, _ in REPS], "window": "—"}

    # курсы → €
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    eur = lambda o, cu: float(o or 0) * rate.get(cu, 1.0)

    # сделки этих менеджеров с 2025 (портфель целиком)
    deals = client.list_deals_fast(
        filter={">=DATE_CREATE": COHORT_START, "ASSIGNED_BY_ID": ids},
        select=["ID", "TITLE", "ASSIGNED_BY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "CATEGORY_ID",
                "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "CLOSEDATE", "MOVED_TIME", "COMPANY_ID"])

    # имена стадий всех затронутых воронок
    cats = {str(d.get("CATEGORY_ID")) for d in deals}
    stage_name = {}
    for cat in cats:
        ent = "DEAL_STAGE" if cat in ("0", "None", "") else f"DEAL_STAGE_{cat}"
        for s in (client.call("crm.status.list", {"filter": {"ENTITY_ID": ent}, "select": ["STATUS_ID", "NAME"]}) or []):
            stage_name[str(s.get("STATUS_ID"))] = s.get("NAME") or ""

    # заказы поставщикам (закупка/контрактность) с 2025
    orders = client.list_items(172, filter={">=createdTime": COHORT_START},
                               select=["id", "stageId", "opportunity", "currencyId", "parentId2"])
    orders = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    order_parents = {str(o.get("parentId2")) for o in orders if o.get("parentId2")}
    buy_by_deal = defaultdict(float)
    for o in orders:
        did = str(o.get("parentId2") or "")
        if did:
            buy_by_deal[did] += eur(o.get("opportunity"), o.get("currencyId"))

    realize_date = realize_date or {}

    def classify(d):
        """→ (level:int|None, lost:bool, realized:bool). level=уровень контрольной точки."""
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        sid = str(d.get("STAGE_ID") or ""); cat = str(d.get("CATEGORY_ID"))
        nm = stage_name.get(sid, "")
        did = str(d["ID"]); regno = _regno(d.get("TITLE"))
        realized = (cat == "0") or (did in order_parents) or regno > 0 or sem == "S"
        if sem == "F" or (not realized and _FAIL_RE.search(nm)):
            return (None, True, False)
        if cat == "0":
            lvl = CAT0_LEVEL.get(sid.split(":")[-1], 2)   # cat0 ⇒ уже в реализации (≥2)
        elif realized:
            lvl = 2
        elif _QUOTE_RE.search(nm) or sid.endswith(":EXECUTING"):
            lvl = 1
        else:
            lvl = 0
        return (lvl, False, realized)   # уровень≥2 ⟺ realized (согласовано с KPI)

    reps_out = []
    for label, uid in rep_ids.items():
        mine = [d for d in deals if str(d.get("ASSIGNED_BY_ID")) == uid]
        created = len(mine)
        lvlcnt = Counter()      # сколько на каждом уровне (текущем)
        passed = Counter()      # сколько ДОСТИГЛО уровня k (level>=k)
        lost = 0
        open_sum = sales = buy = 0.0
        real_n = open_n = 0
        overdue_n = 0; overdue_sum = 0.0; stuck_n = 0
        ttw = []
        detail = []
        for d in mine:
            did = str(d["ID"]); amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
            lvl, is_lost, realized = classify(d)
            cd = str(d.get("CLOSEDATE", ""))[:10]; mv = str(d.get("MOVED_TIME", ""))[:10]
            sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
            is_open = (not is_lost) and (not realized) and sem != "S"
            cls = "lost" if is_lost else ("real" if realized else "early")
            ovd = is_open and bool(cd) and cd < today_iso
            stuck = False
            if is_open and mv:
                try:
                    stuck = (today - dt.date.fromisoformat(mv)).days > 30
                except Exception:
                    pass
            if is_lost:
                lost += 1
            else:
                lvlcnt[lvl] += 1
                for k in range(0, lvl + 1):
                    passed[k] += 1
            if realized:
                real_n += 1; sales += amt; buy += buy_by_deal.get(did, 0.0)
                rd = realize_date.get(did); dc = str(d.get("DATE_CREATE", ""))[:10]
                if rd and dc:
                    try:
                        days = (dt.date.fromisoformat(rd) - dt.date.fromisoformat(dc)).days
                        if days >= 0:
                            ttw.append(days)
                    except Exception:
                        pass
            elif is_open:
                open_n += 1; open_sum += amt
                if ovd:
                    overdue_n += 1; overdue_sum += amt
                if stuck:
                    stuck_n += 1
            detail.append({
                "id": did, "t": (d.get("TITLE") or f"Сделка #{did}")[:90],
                "raw": round(amt), "amt": _money(amt), "date": str(d.get("DATE_CREATE", ""))[:10],
                "stage": stage_name.get(str(d.get("STAGE_ID")), str(d.get("STAGE_ID"))),
                "lvl": (None if is_lost else lvl), "cls": cls, "seq": _regno(d.get("TITLE")),
                "ovd": ovd, "stuck": stuck, "sem": sem or "P",
                "c": "", "o": label,
            })
        closed_won_lost = real_n + lost
        margin = sales - buy
        # воронка контрольных точек: «Создано» = весь приток; дальше — достигло уровня k (живые)
        base = created or 1
        funnel = [{"k": 0, "name": CHECKPOINTS[0], "n": created, "pct": 100}]
        for k in range(1, len(CHECKPOINTS)):
            n = passed.get(k, 0)
            funnel.append({"k": k, "name": CHECKPOINTS[k], "n": n, "pct": round(n / base * 100)})
        # помесячно: создано / выиграно (по дате создания)
        bym_c = Counter(); bym_r = Counter()
        for d in mine:
            mm = str(d.get("DATE_CREATE", ""))[:7]
            if len(mm) == 7:
                bym_c[mm] += 1
                _, il, rz = classify(d)
                if rz:
                    bym_r[mm] += 1
        months = sorted(bym_c)
        bymon = [{"m": MLBL[int(m[5:7])] + " " + m[:4], "mk": m, "c": bym_c[m], "r": bym_r[m]} for m in months]
        ttw.sort()
        reps_out.append({
            "label": label, "uid": uid, "name": unames.get(uid, label),
            "kpis": {
                "created": created, "open": open_n, "openSum": _money(open_sum), "openSumRaw": round(open_sum),
                "real": real_n, "conv": round(real_n / created * 100) if created else 0,
                "sales": _money(sales), "salesRaw": round(sales),
                "margin": _money(margin), "marginRaw": round(margin),
                "marginPct": round(margin / sales * 100) if sales else 0,
                "avgCheck": _money(sales / real_n if real_n else 0),
                "lost": lost, "winRate": round(real_n / closed_won_lost * 100) if closed_won_lost else 0,
                "ttwMed": _pctile(ttw, 50), "ttwP90": _pctile(ttw, 90), "ttwN": len(ttw),
                "overdue": overdue_n, "overdueSum": _money(overdue_sum), "overdueRaw": round(overdue_sum),
                "stuck": stuck_n,
            },
            "funnel": funnel,
            "bymon": bymon,
            "deals": sorted(detail, key=lambda r: -r["raw"]),
        })

    # порядок как в REPS
    order = {l: i for i, (l, _) in enumerate(REPS)}
    reps_out.sort(key=lambda r: order.get(r["label"], 99))
    return {
        "reps": reps_out,
        "labels": [r["label"] for r in reps_out],
        "window": f"01.01.2025 – {today.strftime('%d.%m.%Y')}",
    }
