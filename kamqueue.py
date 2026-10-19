"""Очередь КАМа «что делать сегодня» — раздел «КАМы» вкладки «КАМ и продукт-оунеры».

КАМ отвечает за клиента: подать ТКП, довести тендер, получить решение, согласовать
новый срок, получить дату оплаты и подписанный УПД. Очередь собирает такие пункты
из уже посчитанного — портал не читается, функции чистые:
  • пресейл (presale.compute → openDeals): мяч у КАМа (presale.BALL_RULES) и сделка
    стоит в стадии дольше порога — ballKam;
  • реализация (lifecycle.build → queue): пункты по срокам и закрытию, у которых
    разговор с клиентом ведёт КАМ — late, risk, forecast, planAfterDl, unpaid, noUtd;
    пункты одной сделки одного вида сводятся в один (заказов несколько — разговор один);
  • открытые сделки реализации и пресейла без поля «КАМ» — noKam: хозяин пункта
    руководитель КАМ, действие «назначить КАМа».
Рядом — сроки клиента в ближайшие 14 дней (upcoming14): не отклонение, а то, что
КАМу пора сверить с ОСС и подтвердить клиенту.

ПОРОГИ — утверждены владельцем 19.10.2026 (правило 4: таблицей, правка — одна
строка). Подобраны по смыслу стадии: «Экономика готова | Можно подавать» — подача не
ждёт, 3 дня; тендер и квалификация — неделя; «Ожидаем решение заказчика» — две недели.

ЗОЛОТОЕ ПРАВИЛО ЧАТА (19.10.2026): сделка пресейла, у которой простой по стадии
снят живым чатом (presale: chat.status = active), в очередь не идёт — с ней идёт
работа. Чат не проверен — пункт остаётся с пометкой.

КЛЮЧ КАМа — id из поля «КАМ» (contracts.rows.kamId, presale.openDeals.kamId): подписи
реализации («Имя Фамилия (должность)») и пресейла («Фамилия Имя») из разных
справочников, по имени их не свести. Подпись — из справочника людей, если он дан.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

import lifecycle

KAM_NONE = "КАМ не указан"
HEAD = "руководитель КАМ"          # хозяин пунктов «без КАМа»
SOON_DAYS = 14                     # срок клиента в ближайшие дни — КАМу сверить и подтвердить
UNPAID_BUCKETS = ((30, "unpaid30"), (90, "unpaid90"), (None, "unpaid90p"))   # 1–30 / 31–90 / 90+

# Мяч у КАМа: (стадия пресейла — регулярное выражение, подпись, порог дней в стадии,
# что сделать). Утверждено владельцем 19.10.2026. Первое совпадение побеждает.
BALL_KAM = (
    (r"экономика готова|можно подавать", "«Экономика готова | Можно подавать»", 3,
     "подать ТКП заказчику или назвать в сделке дату подачи"),
    (r"техническ\w* част|квалификац", "техническая часть и квалификация", 7,
     "закрыть с заказчиком техническую часть и квалификацию"),
    (r"тендерн|переторжк|торги", "тендер и переторжка", 7,
     "узнать у заказчика ход тендера и следующий шаг, внести в сделку"),
    (r"ожидаем решени|сорсинг заверш", "«Ожидаем решение заказчика»", 14,
     "получить у заказчика решение или дату решения"),
    (r"согласовани\w* финальн", "согласование финальной спецификации", 7,
     "согласовать с заказчиком финальную спецификацию"),
)
BALL_KAM_DEFAULT = (7, "сдвинуть сделку: следующий шаг с заказчиком")
_BALL_KAM = [(re.compile(rx, re.I), n, act) for rx, _lbl, n, act in BALL_KAM]
PROPOSED = False                   # пороги утверждены владельцем 19.10.2026

# пункты реализации, где разговор с клиентом ведёт КАМ: код → что сделать КАМу
NEW_DATE = "письменно согласовать новый срок с клиентом и внести его"
REAL_ACT = {
    "late": NEW_DATE,
    "risk": "предупредить клиента о риске срока; если срок сдвигается — " + NEW_DATE,
    "forecast": NEW_DATE,
    "planAfterDl": NEW_DATE,
    "unpaid": "получить у клиента дату оплаты — ведёт ответственный заказа",
    "noUtd": "получить подписанный УПД",
}
# код → (важность: меньше — важнее, что случилось)
CODES = {
    "late": (1, lifecycle.EXC["late"][1]),
    "risk": (2, lifecycle.EXC["risk"][1]),
    "forecast": (3, lifecycle.EXC["forecast"][1]),
    "ballKam": (4, "мяч у КАМа, сделка стоит сверх порога"),
    "planAfterDl": (5, lifecycle.EXC["planAfterDl"][1]),
    "unpaid": (6, lifecycle.EXC["unpaid"][1]),
    "noUtd": (7, lifecycle.EXC["noUtd"][1]),
    "noKam": (8, "в сделке не указан КАМ"),
}


def ball_rule(stage_name: str) -> tuple[int, str]:
    """(порог дней, что сделать) для стадии пресейла с мячом у КАМа."""
    for rx, n, act in _BALL_KAM:
        if rx.search(stage_name or ""):
            return n, act
    return BALL_KAM_DEFAULT


def _date(s) -> dt.date | None:
    s = str(s or "")
    try:
        return (dt.datetime.strptime(s, "%d.%m.%Y").date() if len(s) == 10 and s[2] == "."
                else dt.date.fromisoformat(s[:10]))
    except ValueError:
        return None


def _chat_note(ch: dict | None) -> str:
    ch = ch or {}
    st = ch.get("status")
    if st == "quiet":
        return f"в чате тихо {ch['days']} дн." if ch.get("days") is not None else "в чате людей не было"
    if st == "active":
        return f"в чате работа: {ch.get('days')} дн. назад"
    if st in ("unread", "skipped"):
        return "чат не проверен"
    return ""


def _bucket(days: int | None) -> str:
    d = days or 0
    for top, key in UNPAID_BUCKETS:
        if top is None or d <= top:
            return key
    return UNPAID_BUCKETS[-1][1]


def build(presale_data: dict | None, life: dict | None, contract_rows: list[dict] | None,
          today: dt.date, *, people: dict[str, dict] | None = None) -> dict:
    """presale_data — presale.compute; life — contracts.compute()["life"]; contract_rows —
    contracts.compute()["rows"]; people — справочник {uid: {name, active}} для подписей.
    → {queue, counts, byKam, upcoming14, rules, labels}."""
    people = people or {}
    rows = [r for r in (contract_rows or []) if not r.get("closed") and not r.get("lost")]
    by_deal = {str(r.get("deal")): r for r in (contract_rows or [])}

    def label(uid: str, fallback: str) -> tuple[str, bool]:
        """(подпись КАМа, ушёл ли) — из справочника людей, иначе подпись строки."""
        if not uid:
            return KAM_NONE, False
        p = people.get(uid)
        if p:
            return p.get("name") or fallback or f"user#{uid}", p.get("active") is False
        fb = str(fallback or "")
        return fb.replace(" (уволен)", "") or f"user#{uid}", fb.endswith("(уволен)")

    def money(did: str) -> tuple[int | None, str]:
        r = by_deal.get(str(did))
        if not r or not r.get("saleEur"):
            return None, ""                   # пресейл и сделка без суммы: денег не знаем
        return int(r["saleEur"]), str(r.get("saleLbl") or "")

    queue: list[dict] = []
    chat_active = 0

    def add(code: str, act: str, *, uid: str, kam: str, gone: bool, deal: str, days, src: str,
            title: str, seq=0, customer: str = "", extra: dict | None = None) -> None:
        sev, what = CODES[code]
        m, ml = money(deal)
        queue.append({"code": code, "sev": sev, "what": what, "act": act, "days": days,
                      "kamId": uid if code != "noKam" else "", "kam": kam if code != "noKam" else HEAD,
                      "kamGone": bool(gone) and code != "noKam", "deal": str(deal), "src": src,
                      "customer": customer or "", "title": title or "",
                      "money": m, "moneyLbl": ml, **(extra or {}),
                      # № реализации есть только у сделок реализации
                      **({"seq": seq} if seq else {})})

    # 1. пресейл: мяч у КАМа сверх порога; сделки без КАМа
    no_kam: set[str] = set()
    for d in (presale_data or {}).get("openDeals") or []:
        uid = str(d.get("kamId") or "")
        kam, gone = label(uid, d.get("kam") if uid else "")
        days = d.get("days")
        if not uid:
            no_kam.add(str(d["id"]))
            add("noKam", "назначить КАМа", uid="", kam="", gone=False, deal=d["id"], days=days, src="presale",
                title=d.get("t") or "", extra={"stage": d.get("stageName") or "", "chat": _chat_note(d.get("chat"))})
            continue
        if d.get("ball") != "kam" or days is None:
            continue
        thr, act = ball_rule(d.get("stageName") or "")
        if days <= thr:
            continue
        if (d.get("chat") or {}).get("status") == "active":
            chat_active += 1                  # золотое правило: в чате идёт работа
            continue
        add("ballKam", act, uid=uid, kam=kam, gone=gone, deal=d["id"], days=days, src="presale",
            title=d.get("t") or "",
            extra={"stage": d.get("stageName") or "", "thr": thr, "chat": _chat_note(d.get("chat"))})

    # 2. реализация: пункты по срокам и закрытию — один на сделку и вид
    agg: dict[tuple[str, str], dict] = {}
    for x in (life or {}).get("queue") or []:
        code = x.get("code")
        uid = str(x.get("kamId") or "")
        if code not in REAL_ACT or not uid:
            continue                          # без КАМа сделка придёт пунктом noKam
        k = (str(x.get("deal")), code)
        a = agg.get(k)
        if a is None:
            agg[k] = a = {"x": x, "orders": 0, "days": x.get("days")}
        a["orders"] += 1
        if (x.get("days") or 0) > (a["days"] or 0):
            a["days"], a["x"] = x.get("days"), x
    for (did, code), a in agg.items():
        x = a["x"]
        uid = str(x.get("kamId") or "")
        kam, gone = label(uid, x.get("kam") or "")
        # пункт — про сделку: название сделки, а не заказа (заказов бывает несколько)
        add(code, REAL_ACT[code], uid=uid, kam=kam, gone=gone, deal=did, days=a["days"], src="real",
            title=(by_deal.get(did) or {}).get("title") or x.get("title") or "",
            seq=x.get("seq") or 0, customer=x.get("customer") or "",
            extra={"orders": a["orders"], "dl": x.get("dl") or "",
                   # хозяин заказа (ответственный, ОСС) — с кем КАМ сверяет срок и оплату
                   "owner": str(x.get("who") or "").replace(" (уволен)", "")})

    # 3. реализация: открытые сделки без КАМа
    for r in rows:
        did = str(r.get("deal"))
        if r.get("kamId") or did in no_kam:
            continue
        no_kam.add(did)
        sd = r.get("stageDays")
        add("noKam", "назначить КАМа", uid="", kam="", gone=False, deal=did,
            days=round(sd) if isinstance(sd, (int, float)) else None,
            src="real", title=r.get("title") or "", seq=r.get("seq") or 0, customer=r.get("customer") or "",
            extra={"stage": r.get("stageName") or ""})

    queue.sort(key=lambda q: (q["sev"], -(q["days"] or 0), -(q["money"] or 0), str(q["deal"])))

    # 4. сроки клиента в ближайшие 14 дней: живые недоставленные заказы
    upcoming: list[dict] = []
    for r in rows:
        uid = str(r.get("kamId") or "")
        kam, gone = label(uid, r.get("kam") or "")
        m, ml = money(r.get("deal"))
        for o in r.get("orders") or []:
            dl = _date(o.get("dl"))
            if not o.get("live") or o.get("delivered") or not dl:
                continue
            left = (dl - today).days
            if 0 <= left <= SOON_DAYS:
                upcoming.append({"kamId": uid, "kam": kam, "kamGone": gone, "deal": str(r.get("deal")),
                                 "seq": r.get("seq") or 0, "customer": r.get("customer") or "",
                                 "title": o.get("title") or r.get("title") or "", "order": o.get("id"),
                                 "sup": o.get("sup") or "", "dl": o.get("dl"), "left": left,
                                 "money": m, "moneyLbl": ml})
    upcoming.sort(key=lambda u: (u["left"], -(u["money"] or 0), str(u["deal"])))

    # 5. по КАМам: пункты по видам, неоплата по давности, сроки ≤ 14 дн. Нулевые поля
    #    не пишутся: столбец из одних нулей — шум и в странице, и в живой сверке
    per: dict[str, dict] = {}
    deals_money: dict[str, dict[str, int]] = defaultdict(dict)

    def slot(uid: str, name: str, gone: bool) -> dict:
        return per.setdefault(uid, {"id": uid, "name": name, "gone": gone, "c": Counter()})

    for q in queue:
        s = slot(q["kamId"], q["kam"], q["kamGone"])
        s["c"]["n"] += 1
        s["c"][q["code"]] += 1
        if q["code"] == "unpaid":
            s["c"][_bucket(q["days"])] += 1
        if q["money"]:
            deals_money[q["kamId"]][q["deal"]] = q["money"]
    for u in upcoming:
        slot(u["kamId"], u["kam"], u["kamGone"])["c"]["soon"] += 1
    by_kam = []
    for uid, s in per.items():
        row = {"id": uid, "name": s["name"] if uid else (HEAD if s["c"].get("noKam") else KAM_NONE),
               "gone": s["gone"]}
        row.update({k: v for k, v in s["c"].items() if v})
        if sum(deals_money[uid].values()):
            row["money"] = sum(deals_money[uid].values())
        by_kam.append(row)
    by_kam.sort(key=lambda r: (r["id"] == "", -r.get("n", 0), -r.get("soon", 0), r["name"]))

    counts = Counter(q["code"] for q in queue)
    return {
        "asOf": today.strftime("%d.%m.%Y"),
        "queue": queue,
        "counts": dict(counts),
        "head": {"ballKam": counts.get("ballKam", 0), "soon": len(upcoming),
                 "unpaid": counts.get("unpaid", 0), "noKam": counts.get("noKam", 0),
                 "n": len(queue), "kams": sum(1 for r in by_kam if r["id"]),
                 "chatActive": chat_active},
        "byKam": by_kam,
        "upcoming14": upcoming,
        "rules": {"proposed": PROPOSED, "soonDays": SOON_DAYS, "riskDays": lifecycle.RISK_DAYS,
                  "utdGrace": lifecycle.UTD_GRACE,
                  "ballKam": [{"stage": rx, "label": lbl, "days": n, "act": act} for rx, lbl, n, act in BALL_KAM]
                             + [{"stage": "", "label": "прочие стадии", "days": BALL_KAM_DEFAULT[0],
                                 "act": BALL_KAM_DEFAULT[1]}],
                  "unpaidBuckets": [30, 90]},
        "labels": {k: v[1] for k, v in CODES.items()},
    }
