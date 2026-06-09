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

# скорость реакции на @упоминания в чатах сделок
REACT_SLA_MIN = 120          # целевой срок реакции (2 часа) — для «в срок»
REACT_DAYS = 21              # окно: активные чаты за N дней
REACT_CAP = 500              # потолок числа сканируемых чатов
NEGLECT_H = 24               # «брошено»: ждут дольше суток = зашквар (для алерта/красного)
WAIT_MAX_H = 30 * 24         # ожидания старше 30 дн — это уже «архив», не «висит сейчас»
SAMPLE_PER_REP = 60          # сколько реплик сотрудника собрать для AI-оценки
_MENTION_RE = re.compile(r"\[USER=(\d+)\]")


def _add_wait(wd, cid, deal, title, age_h, kind):
    cur = wd.get(cid)
    if not cur or age_h > cur["ageH"]:
        wd[cid] = {"chat": int(cid), "deal": deal, "t": title, "ageH": age_h, "kind": kind}


def _react_stats(client, rep_ids, *, as_of=None, deal_owner=None, deal_stage=None, real_users=None,
                 collect_samples=False):
    """Результат-ориентированная аналитика чатов сделок:
       • реакция на @упоминания (когда отвечает САМ) — медиана/в срок;
       • исход упоминаний: сам / закрыл коллега / повисло (никто);
       • «ВИСИТ НА НЁМ» — тег без ответа ИЛИ в его сделке последнее сообщение чужое, с возрастом в днях;
       • вовлечённость — в скольких чатах активен и когда писал последний раз.
       Раздельно для пресейла и реализации (по его собственным сделкам)."""
    deal_owner = deal_owner or {}; deal_stage = deal_stage or {}
    real = set(str(u) for u in (real_users or []))
    def pt(s):
        try:
            return dt.datetime.fromisoformat(str(s)[:19])
        except Exception:
            return None
    now = dt.datetime.now(); cut = now - dt.timedelta(days=REACT_DAYS)
    try:
        rec = client.call("im.recent.get", {}) or []
    except Exception:
        return {}
    items = (rec.get("items") if isinstance(rec, dict) else rec) or []
    chats = []
    for it in items:
        ch = it.get("chat") or {}
        if ch.get("type") == "crm" and str(ch.get("entity_id", "")).startswith("DEAL|"):
            la = pt(it.get("date_last_activity"))
            if la and la >= cut:
                chats.append((it.get("chat_id") or ch.get("id"),
                              (it.get("title") or ch.get("name") or "")[:70],
                              str(ch.get("entity_id")).split("|")[-1]))
    chats = chats[:REACT_CAP]
    repset = set(str(u) for u in rep_ids)

    def blank():
        return {"ment": 0, "self": 0, "coll": 0, "none": 0, "rt": []}
    acc = {u: {"all": blank(), "presale": blank(), "real": blank(),
               "wait": {}, "posted": set(), "lastPost": None, "samples": []} for u in repset}
    clean = lambda s: re.sub(r"\[/?[A-Z][^\]]*\]", "", str(s or "")).strip()[:400]

    for cid, title, deal in chats:
        try:
            r = client.call("im.dialog.messages.get", {"DIALOG_ID": "chat" + str(cid), "LIMIT": 50})
            ms = (r.get("messages") if isinstance(r, dict) else r) or []
        except Exception:
            continue
        ms = [m for m in ms if pt(m.get("date"))]
        ms.sort(key=lambda m: pt(m.get("date")))
        owner = deal_owner.get(deal)
        sb = deal_stage.get(deal) if deal_stage.get(deal) in ("presale", "real") else None
        # вовлечённость + последнее РЕАЛЬНОЕ (не бот) сообщение в чате + выборка текста для AI-оценки
        last_real = None
        for mi, m in enumerate(ms):
            au = str(m.get("author_id"))
            if au in repset:
                a = acc[au]; a["posted"].add(cid); t = pt(m.get("date"))
                if t and (a["lastPost"] is None or t > a["lastPost"]):
                    a["lastPost"] = t
                if collect_samples and len(a["samples"]) < SAMPLE_PER_REP:
                    txt = clean(m.get("text"))
                    if len(txt) >= 3:
                        ctx = clean(ms[mi - 1].get("text")) if mi > 0 and str(ms[mi - 1].get("author_id")) != au else ""
                        a["samples"].append({"deal": deal, "ctx": ctx[:240], "msg": txt})
            if au in real:
                last_real = m
        # упоминания: исход сам/коллега/никто
        for i, m in enumerate(ms):
            au = str(m.get("author_id")); t0 = pt(m.get("date"))
            for rid in set(_MENTION_RE.findall(m.get("text") or "")) & repset:
                if au == rid:
                    continue
                a = acc[rid]
                for b in (a["all"], a[sb]) if sb else (a["all"],):
                    b["ment"] += 1
                later = ms[i + 1:]
                self_t = next((pt(x.get("date")) for x in later
                               if str(x.get("author_id")) == rid and pt(x.get("date")) >= t0), None)
                if self_t:
                    rt = (self_t - t0).total_seconds() / 60.0
                    for b in (a["all"], a[sb]) if sb else (a["all"],):
                        b["self"] += 1; b["rt"].append(rt)
                else:
                    coll = any(str(x.get("author_id")) in real and str(x.get("author_id")) not in (au, rid)
                               and pt(x.get("date")) >= t0 for x in later)
                    key = "coll" if coll else "none"
                    for b in (a["all"], a[sb]) if sb else (a["all"],):
                        b[key] += 1
                    if not coll:
                        age_h = (now - t0).total_seconds() / 3600.0
                        if REACT_SLA_MIN / 60.0 < age_h <= WAIT_MAX_H:
                            _add_wait(a["wait"], cid, deal, title, age_h, "тег без ответа")
        # «его сделка ждёт его»: в его сделке последнее реальное сообщение — чужое
        if owner in acc and last_real is not None and str(last_real.get("author_id")) != owner:
            age_h = (now - pt(last_real.get("date"))).total_seconds() / 3600.0
            if REACT_SLA_MIN / 60.0 < age_h <= WAIT_MAX_H:
                _add_wait(acc[owner]["wait"], cid, deal, title, age_h, "его сделка ждёт")

    out = {}
    for u, a in acc.items():
        def fin(b):
            x = sorted(b["rt"]); n = len(x)
            pct = lambda lim: round(sum(1 for v in x if v <= lim) / n * 100) if n else 0
            return {"mentions": b["ment"], "self": b["self"], "coll": b["coll"], "none": b["none"],
                    "medMin": (round(x[n // 2]) if n else None), "withinSla": pct(REACT_SLA_MIN),
                    "w30": pct(30), "w120": pct(120), "w1440": pct(1440), "answeredSelf": n,
                    "handledPct": round((b["self"] + b["coll"]) / b["ment"] * 100) if b["ment"] else 0}
        waits = sorted(a["wait"].values(), key=lambda w: -w["ageH"])
        o = fin(a["all"])
        o.update({
            "presale": fin(a["presale"]), "real": fin(a["real"]),
            "waiting": [{"chat": w["chat"], "deal": w["deal"], "t": w["t"],
                         "ageH": round(w["ageH"], 1), "ageDays": round(w["ageH"] / 24, 1), "kind": w["kind"]}
                        for w in waits[:60]],
            "waitN": len(waits),
            "waitGt1d": sum(1 for w in waits if w["ageH"] > 24),
            "waitGt3d": sum(1 for w in waits if w["ageH"] > 72),
            "waitMaxDays": round(waits[0]["ageH"] / 24, 1) if waits else 0,
            "activeChats": len(a["posted"]),
            "lastPostDays": (max(0.0, round((now - a["lastPost"]).total_seconds() / 86400, 1)) if a["lastPost"] else None),
        })
        if collect_samples:
            o["samples"] = a["samples"]
        out[u] = o
    return out


_SNAP_PATH = __import__("os").path.join(__import__("os").path.dirname(__file__), "data", "chat_snapshot.json")
_REACT_NUM = ["mentions", "self", "coll", "none", "medMin", "withinSla", "w30", "w120", "w1440",
              "handledPct", "waitN", "waitGt1d", "waitGt3d", "waitMaxDays", "activeChats", "lastPostDays"]


def scan_chat_reaction(client, *, as_of=None, collect_samples=False):
    """Суточный скан чатов сделок → реакция по сотрудникам (+ выборка текста для AI-оценки).
    Возвращает (react_dict, rep_ids, unames). Стадия сделки — упрощённо: кат.0/№ → 'real', иначе 'presale'."""
    import re as _re
    today = as_of or dt.date.today()
    unames = client.users()
    rep_ids = {}
    for label, pat in REPS:
        rx = _re.compile(pat, _re.I)
        uid = next((u for u, n in unames.items() if rx.search(n or "")), None)
        if uid:
            rep_ids[label] = str(uid)
    ids = list(rep_ids.values())
    deals = client.list_deals_fast(filter={">=DATE_CREATE": COHORT_START, "ASSIGNED_BY_ID": ids},
                                   select=["ID", "TITLE", "ASSIGNED_BY_ID", "CATEGORY_ID"])
    downer = {}; dstage = {}
    idset = set(ids)
    for d in deals:
        o = str(d.get("ASSIGNED_BY_ID") or "")
        if o in idset:
            did = str(d["ID"]); downer[did] = o
            is_real = str(d.get("CATEGORY_ID")) == "0" or _regno(d.get("TITLE")) > 0
            dstage[did] = "real" if is_real else "presale"
    react = _react_stats(client, ids, as_of=today, deal_owner=downer, deal_stage=dstage,
                         real_users=set(unames.keys()), collect_samples=collect_samples)
    return react, rep_ids, unames


def _load_react_snapshot(uids) -> dict:
    """Читает data/chat_snapshot.json: усредняет реакцию за последнюю неделю, «висит» — из последнего дня,
    плюс AI-оценка. Дашборд использует это вместо тяжёлого скана при каждой пересборке."""
    import json
    try:
        snap = json.loads(open(_SNAP_PATH, encoding="utf-8").read())
    except Exception:
        return {}
    days = snap.get("days", {}); assess = snap.get("assessment", {})
    daykeys = sorted(days)[-7:]
    out = {}
    for uid in (str(u) for u in uids):
        recs = [days[d][uid] for d in daykeys if isinstance(days.get(d), dict) and days[d].get(uid)]
        if not recs:
            out[uid] = {"assessment": assess.get(uid), "days": 0}
            continue
        avg = {}
        for k in _REACT_NUM:
            vals = [r[k] for r in recs if r.get(k) is not None]
            avg[k] = (sum(vals) / len(vals)) if vals else None
        for k in _REACT_NUM:  # счётчики/проценты — целыми, кроме дробных дней
            if avg.get(k) is not None and k not in ("waitMaxDays", "lastPostDays"):
                avg[k] = round(avg[k])
        for k in ("waitMaxDays", "lastPostDays"):
            if avg.get(k) is not None:
                avg[k] = round(avg[k], 1)
        latest = recs[-1]
        avg["waiting"] = latest.get("waiting", [])
        avg["presale"] = latest.get("presale", {})
        avg["real"] = latest.get("real", {})
        avg["days"] = len(recs)
        avg["assessment"] = assess.get(uid)
        out[uid] = avg
    return out


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

    # реакция в чатах сделок — НЕ сканируем при каждой пересборке: читаем суточный снимок
    # (data/chat_snapshot.json, обновляется раз в сутки отдельным джобом). Среднее за неделю.
    react = _load_react_snapshot(rep_ids.values())

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
            "react": react.get(uid, {"mentions": 0, "self": 0, "coll": 0, "none": 0, "medMin": None,
                                     "withinSla": 0, "w30": 0, "w120": 0, "w1440": 0, "handledPct": 0,
                                     "waiting": [], "waitN": 0, "waitGt1d": 0, "waitGt3d": 0, "waitMaxDays": 0,
                                     "activeChats": 0, "lastPostDays": None,
                                     "presale": {}, "real": {}}),
            "deals": sorted(detail, key=lambda r: -r["raw"]),
        })

    # порядок как в REPS
    order = {l: i for i, (l, _) in enumerate(REPS)}
    reps_out.sort(key=lambda r: order.get(r["label"], 99))
    return {
        "reps": reps_out,
        "labels": [r["label"] for r in reps_out],
        "window": f"01.01.2025 – {today.strftime('%d.%m.%Y')}",
        "slaMin": REACT_SLA_MIN,
        "reactDays": REACT_DAYS,
    }
