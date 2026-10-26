"""Очередь КАМа «что делать сегодня» — раздел «КАМы» вкладки «КАМ и продукт-оунеры».

КАМ отвечает за клиента: подать ТКП, довести тендер, получить решение, согласовать
новый срок, получить дату оплаты и подписанный УПД. Очередь собирает такие пункты
из уже посчитанного — портал не читается, функции чистые:
  • пресейл (presale.compute → openDeals): мяч у КАМа (presale.BALL_RULES) и сделка
    стоит в стадии дольше порога — ballKam;
  • реализация (lifecycle.build → queue): пункты, где КАМ — хозяин или в копии по
    регламенту ОРК (решение владельца 26.10.2026, п. 1: role / copyRole пункта = «kam»):
    срок клиента (late, risk, forecast, prodDev, planAfterDl), долг клиента (unpaid — КАМ
    хозяин, ОСС в копии, п. 4), УПД на 11-й р.д. (noUtd), приёмка и заказ (s1Over, noOrder,
    noDl), оплата без договора (payNoContract), срок оплаты (noPayPlan); пункты одной сделки
    одного вида сводятся в один (заказов несколько — разговор один);
  • сделки без поля «КАМ» — noKam: хозяин пункта руководитель КАМ, действие
    «назначить КАМа». Это открытые сделки реализации и пресейла и любая сделка
    реализации (в том числе закрытая — без УПД) с пунктом для КАМа. Сигналы, которые
    без КАМа некому отдать (срок сорван, неоплата, мяч у КАМа сверх порога), не
    теряются: они — в пункте noKam (also), и важность пункта — по самому важному из них.
Рядом — сроки клиента в ближайшие 14 дней (upcoming14): не отклонение, а то, что
КАМу пора сверить с ОСС и подтвердить клиенту.

ПОРОГИ — утверждены владельцем 19.10.2026 (правило 4: таблицей, правка — одна
строка). Подобраны по смыслу стадии: «Экономика готова | Можно подавать» — подача не
ждёт, 3 дня; тендер и квалификация — неделя; «Ожидаем решение заказчика» — две недели.

ЗОЛОТОЕ ПРАВИЛО ЧАТА (19.10.2026): сделка пресейла, у которой простой по стадии
снят живым чатом (presale: chat.status = active), в очередь не идёт — с ней идёт
работа. Чат не проверен — пункт остаётся с пометкой. Порог мяча у КАМа (3–7 дн.)
меньше простоя пресейла (14 дн.), поэтому чаты таких сделок пресейл читает отдельно —
по правилу chat_wanted (main.py передаёт его в presale.compute, бюджет общий).

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
    "prodDev": "предупредить клиента о риске срока; если срок сдвигается — " + NEW_DATE,
    "planAfterDl": NEW_DATE,
    # долг клиента урегулирует КАМ, ОСС в копии (решение владельца 26.10.2026, п. 4)
    "unpaid": "урегулировать долг: получить у клиента оплату или дату оплаты",
    "noUtd": "получить подписанный УПД",
    "noPayPlan": "сообщить ОСС срок оплаты клиента по договору",
    "s1Over": "ответить руководителю ОРК по приёмке сделки",
    "noOrder": "передать руководителю ОРК всё для приёмки и заказа поставщику",
    "noDl": "дать срок клиента из договора",
    "payNoContract": "довести подписание договора с заказчиком",
}
# код → (важность: меньше — важнее, что случилось)
CODES = {
    "late": (1, lifecycle.EXC["late"][1]),
    "risk": (2, lifecycle.EXC["risk"][1]),
    "forecast": (3, lifecycle.EXC["forecast"][1]),
    "prodDev": (3, lifecycle.EXC["prodDev"][1]),
    "payNoContract": (3, lifecycle.EXC["payNoContract"][1]),
    "ballKam": (4, "мяч у КАМа, сделка стоит сверх порога"),
    "planAfterDl": (5, lifecycle.EXC["planAfterDl"][1]),
    "unpaid": (6, lifecycle.EXC["unpaid"][1]),
    "noUtd": (7, lifecycle.EXC["noUtd"][1]),
    "s1Over": (7, lifecycle.EXC["s1Over"][1]),
    "noOrder": (7, lifecycle.EXC["noOrder"][1]),
    "noPayPlan": (8, lifecycle.EXC["noPayPlan"][1]),
    "noDl": (8, lifecycle.EXC["noDl"][1]),
    "noKam": (9, "в сделке не указан КАМ"),
}


def kam_involved(x: dict) -> bool:
    """Пункт реализации — для КАМа: он хозяин или в копии по регламенту ОРК (ork.item_owner).
    Пункт без ролей (собран не lifecycle) — по таблице REAL_ACT, как до 26.10.2026."""
    if x.get("code") not in REAL_ACT:
        return False
    if "role" in x or "copyRole" in x:
        return "kam" in (x.get("role"), x.get("copyRole"))
    return True


def ball_rule(stage_name: str) -> tuple[int, str]:
    """(порог дней, что сделать) для стадии пресейла с мячом у КАМа."""
    for rx, n, act in _BALL_KAM:
        if rx.search(stage_name or ""):
            return n, act
    return BALL_KAM_DEFAULT


def chat_wanted(r: dict) -> bool:
    """Строка presale.compute, чей чат прочитать для очереди: мяч у КАМа и сделка стоит
    в стадии дольше порога — кандидат в ballKam (с КАМом или без него)."""
    days = r.get("days")
    return r.get("ball") == "kam" and days is not None and days > ball_rule(r.get("stageName") or "")[0]


def _date(s) -> dt.date | None:
    s = str(s or "")
    try:
        return (dt.datetime.strptime(s, "%d.%m.%Y").date() if len(s) == 10 and s[2] == "."
                else dt.date.fromisoformat(s[:10]))
    except ValueError:
        return None


def _chat_note(ch: dict | None, *, gate: bool = False) -> str:
    """Пометка о чате. gate — чат решает, пункт ли это (мяч у КАМа): тогда и не
    прочитанный чат (нет читателя, сделка не попала в чтение) — «чат не проверен»."""
    if not ch:
        return "чат не проверен" if gate else ""
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
    # Сигналы сделок без КАМа: {сделка: {вид: сигнал}}. Пункт с хозяином-КАМом такой
    # сделке не поставить, но и терять сигнал нельзя: сорванный срок без КАМа срочнее,
    # а не тише. Сигналы едут в пункт noKam (also), важность — по самому важному.
    orphan: dict[str, dict[str, dict]] = defaultdict(dict)
    orphan_x: dict[str, dict] = {}            # пункт реализации — подписи сделки, которой нет в rows

    def signal(did, code: str, days, **kw) -> None:
        """Сигнал сделки без КАМа: по виду — один, дней — наибольшее, заказы — счётом."""
        sig = orphan[str(did)]
        o = sig.get(code)
        if o is None:
            o = sig[code] = {"code": code, "what": CODES[code][1], "days": days, "orders": 0}
        o["orders"] += 1
        if (days or 0) >= (o["days"] or 0):
            o.update(days=days, **kw)

    def add(code: str, act: str, *, uid: str, kam: str, gone: bool, deal: str, days, src: str,
            title: str, seq=0, customer: str = "", extra: dict | None = None, sev: int | None = None) -> None:
        sev0, what = CODES[code]
        m, ml = money(deal)
        queue.append({"code": code, "sev": sev0 if sev is None else sev, "what": what, "act": act, "days": days,
                      "kamId": uid if code != "noKam" else "", "kam": kam if code != "noKam" else HEAD,
                      "kamGone": bool(gone) and code != "noKam", "deal": str(deal), "src": src,
                      "customer": customer or "", "title": title or "",
                      "money": m, "moneyLbl": ml, **(extra or {}),
                      # № реализации есть только у сделок реализации
                      **({"seq": seq} if seq else {})})

    no_kam: set[str] = set()

    def add_no_kam(did: str, *, src: str, days, title: str, seq=0, customer: str = "",
                   extra: dict | None = None) -> None:
        """Один пункт «назначить КАМа» на сделку; сигналы сделки — в also, по важности."""
        did = str(did)
        no_kam.add(did)
        also = sorted(orphan.pop(did, {}).values(), key=lambda a: (CODES[a["code"]][0], -(a["days"] or 0)))
        ex = dict(extra or {})
        sev = None
        if also:
            for a in also:
                if a["orders"] <= 1:
                    a.pop("orders")
            ex["also"] = also
            sev = CODES[also[0]["code"]][0]
            days = also[0]["days"]            # место в очереди — по самому важному сигналу
            ex["inWd"] = bool(also[0].get("inWd"))   # его дни — рабочие у сроков регламента ОРК
            if also[0].get("owner"):
                ex["owner"] = also[0]["owner"]
        add("noKam", "назначить КАМа", uid="", kam="", gone=False, deal=did, days=days, src=src,
            title=title, seq=seq, customer=customer, extra=ex, sev=sev)

    # 1. пресейл: мяч у КАМа сверх порога; сделки без КАМа
    ps_none: list[dict] = []
    for d in (presale_data or {}).get("openDeals") or []:
        uid = str(d.get("kamId") or "")
        days = d.get("days")
        over = chat_wanted(d)
        thr, act = ball_rule(d.get("stageName") or "")
        if over and (d.get("chat") or {}).get("status") == "active":
            chat_active += 1                  # золотое правило: в чате идёт работа
            over = False
        if not uid:
            ps_none.append(d)
            if over:
                signal(d["id"], "ballKam", days, thr=thr)
            continue
        if not over:
            continue
        kam, gone = label(uid, d.get("kam"))
        add("ballKam", act, uid=uid, kam=kam, gone=gone, deal=d["id"], days=days, src="presale",
            title=d.get("t") or "",
            extra={"stage": d.get("stageName") or "", "thr": thr, "chat": _chat_note(d.get("chat"), gate=True)})

    # 2. реализация: пункты по срокам и закрытию — один на сделку и вид
    agg: dict[tuple[str, str], dict] = {}
    for x in (life or {}).get("queue") or []:
        code = x.get("code")
        uid = str(x.get("kamId") or "")
        if not kam_involved(x):
            continue
        if not uid:
            # без КАМа разговор с клиентом вести некому: сигнал — в пункт noKam сделки
            did = str(x.get("deal"))
            orphan_x.setdefault(did, x)
            signal(did, code, x.get("days"), dl=x.get("dl") or "",
                   owner=str(x.get("who") or "").replace(" (уволен)", ""), inWd=bool(x.get("inWd")))
            continue
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
                   # хозяин пункта по регламенту (решение 26.10.2026, п. 1) — с кем КАМ сверяет срок и
                   # оплату, и его роль; КАМ хозяин (kamOwner) — пункт ведёт он сам; КАМ в копии
                   # (kamCopy) — пункт ведёт хозяин, КАМ держит разговор с клиентом
                   "owner": str(x.get("who") or "").replace(" (уволен)", ""),
                   "ownerRole": x.get("roleName") or "",
                   "kamOwner": x.get("role") == "kam",
                   "kamCopy": x.get("copyRole") == "kam" and x.get("role") != "kam",
                   # дни рабочие (р.д., календарь РФ, «без пауз») у сроков регламента, иначе календарные
                   "inWd": bool(x.get("inWd")),
                   **({"step": x["step"]} if x.get("step") else {})})

    # 3. сделки без КАМа: пресейл; открытые сделки реализации; сделки реализации с
    #    сигналом для КАМа — и закрытые (УПД не получен), их в открытых нет
    for d in ps_none:
        # мяч у КАМа сверх порога — чат решает, сигнал ли это: непрочитанный помечается
        gate = "ballKam" in orphan.get(str(d["id"]), {})
        add_no_kam(d["id"], src="presale", days=d.get("days"), title=d.get("t") or "",
                   extra={"stage": d.get("stageName") or "", "chat": _chat_note(d.get("chat"), gate=gate)})

    def real_no_kam(did: str, r: dict, x: dict) -> None:
        sd = r.get("stageDays")
        add_no_kam(did, src="real", days=round(sd) if isinstance(sd, (int, float)) else None,
                   title=r.get("title") or x.get("title") or "", seq=r.get("seq") or x.get("seq") or 0,
                   customer=r.get("customer") or x.get("customer") or "",
                   extra={"stage": r.get("stageName") or "",
                          **({"stageDays": round(sd)} if isinstance(sd, (int, float)) else {})})

    for r in rows:
        did = str(r.get("deal"))
        if r.get("kamId") or did in no_kam:
            continue
        real_no_kam(did, r, orphan_x.get(did) or {})
    for did in [d for d in orphan if d not in no_kam]:
        real_no_kam(did, by_deal.get(did) or {}, orphan_x.get(did) or {})

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
                  "utdWd": lifecycle.UTD_WD,
                  "ballKam": [{"stage": rx, "label": lbl, "days": n, "act": act} for rx, lbl, n, act in BALL_KAM]
                             + [{"stage": "", "label": "прочие стадии", "days": BALL_KAM_DEFAULT[0],
                                 "act": BALL_KAM_DEFAULT[1]}],
                  "unpaidBuckets": [30, 90]},
        "labels": {k: v[1] for k, v in CODES.items()},
    }
