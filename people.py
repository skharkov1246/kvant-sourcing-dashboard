"""Вкладки «КАМы» и «Продукт-оунеры» — состав, загрузка и качество работы коммерсантов.

Инструмент руководителя: кто есть в компании, что на ком записано ПРЯМО СЕЙЧАС,
сколько это в деньгах, что просрочено и где CRM врёт.

КАК СДЕЛКА ОТНОСИТСЯ К ЧЕЛОВЕКУ (замер зондом v36 по 2 695 открытым сделкам)
  • «Ответственный» (ASSIGNED_BY_ID) заполнен всегда, но это исполнитель: у 1 601 сделки
    из 1 812 он НЕ совпадает с КАМом. Строить по нему нагрузку коммерсанта нельзя.
  • Поле сделки «КАМ» (UF_CRM_1740390857) заполнено у 67% открытых сделок, в нём 18 человек
    и ни одного уволенного. В клиентских воронках (ЛУКОЙЛ, Норникель, Шельф, Basra)
    заполнено у 97–98% — это и есть рабочая атрибуция клиентского менеджера.
  • Поле «Product leader» (UF_CRM_1779187425) заполнено у 10% — продуктовая атрибуция
    в портале только вводится, поэтому для продукт-оунеров основной механизм — владелец
    сделки, а поле лишь уточняет.
  Отсюда каскад: поле роли → владелец, если он сам этой роли → роль не закреплена
  (это не ноль, а отдельный счётчик «сделки без КАМа» — работа для руководителя).

РОЛЬ ЧЕЛОВЕКА определяется из Bitrix, а не списком фамилий в коде: должность
(WORK_POSITION) → название отдела → «вне коммерческого блока». Правило показывается
в строке сотрудника, чтобы атрибуцию можно было оспорить фактом.

СОСТАВ берётся из user.get: 157 активных и 88 отключённых на 17.09.2026. Уволенные не
исчезают: их открытые сделки (374 шт) собираются в блок «бесхозные» — это работа,
которую сегодня не ведёт никто.

ЧТО СЧИТАЕТСЯ ПРОСРОЧКОЙ. Не CLOSEDATE: у всех 2 695 открытых сделок она заполнена,
у 2 624 — в прошлом, в будущем нет ни одной, то есть это не план, а автопростановка.
Просрочка берётся из заказов поставщикам СП-172, поле «Date of deadline to customer»
(149 просроченных из 328 живых заказов с датой) — это обещание клиенту, а не поле CRM.

ЗАСТОЙ — по MOVED_TIME (дата последней смены стадии). Пороги подобраны по факту:
без движения 90 дней — 1 890 сделок, 180 дней — 1 580. Поэтому «застой» = 90 дней,
«брошено» = 180; порог 30 дней бессмысленен — под него попадает 86% портфеля.

КАРТОЧКИ-ВЫБРОСЫ. Базовая валюта портала — EUR (проверено), пересчёт верен, но
пайплайн всё равно раздут: медианная открытая сделка — около €100 тыс., а десяток
карточек несёт сотни миллионов. Поэтому сумма роли всегда показывается вместе с
числом выбросов (сумма ≥ 50 медиан) и их долей: без этого «пайплайн €1,5 млрд»
выглядит фактом, хотя держится на карточках, где сумму нужно просто проверить.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict

from bitrix_client import BitrixClient

YEAR_START = "2026-01-01T00:00:00"
ORDERS_SINCE = "2025-01-01T00:00:00"
REALIZE_CAT = "0"             # воронка «Реализация»
OUTLIER_MULT = 50             # сумма ≥ стольких медиан — карточка-выброс, сумму надо проверить
STALE_DAYS = 90               # без смены стадии дольше — застой
DEAD_DAYS = 180               # ...дольше — брошено
TECH_CATS = {"6", "22", "24", "26", "28"}   # реклама, тестовые, адаптационная — вне управленческого счёта

# поля-люди на карточке сделки (подтверждены зондом v36)
KAM_F = "UF_CRM_1740390857"        # «КАМ» — 67% заполнения, 18 человек, уволенных нет
KAM_OLD = "UF_CRM_1736926032"      # «KAM (старое)» — 6%, используется как запасной
PROD_F = "UF_CRM_1779187425"       # «Product leader» — 10%
PROD_OLD = "UF_CRM_1776169499"     # «Product leader (заменить на множ)»
PROD_HEAD = "UF_CRM_1789481932"    # «Руководитель продуктового направления»
DEAL_FIELDS = [KAM_F, KAM_OLD, PROD_F, PROD_OLD, PROD_HEAD]
DL_CUSTOMER = "ufCrm20_1728900218435"   # дедлайн клиенту на заказе СП-172

DEAL_SELECT = ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY",
               "CURRENCY_ID", "DATE_CREATE", "MOVED_TIME", "LAST_ACTIVITY_TIME",
               "ASSIGNED_BY_ID", "COMPANY_ID"] + DEAL_FIELDS

# --- роль по ДОЛЖНОСТИ (Bitrix WORK_POSITION): проверяется первой, она точнее отдела
POS_PROD = re.compile(r"product (owner|manager|leader|director)|head of (compressor|filtration|"
                      r"technological|the zra|granulation)|granulation project|продукт\w*[ -]?(оунер|менеджер|лидер)|"
                      r"руководител\w* продуктов", re.I)
POS_KAM = re.compile(r"key account|account manager|\bkam\b|ключев\w* клиент|"
                     r"аккаунт[ -]менеджер|менеджер по работе с клиент", re.I)
# --- роль по НАЗВАНИЮ ОТДЕЛА. Продуктовое проверяется раньше клиентского: отдел
#     «Группа по работе с водяными насосами» назван как клиентский, а ведёт оборудование.
DEPT_BACK = re.compile(r"поиска поставщик|тендерн|сопровожден|реализации контракт|логистик|"
                       r"бухгалтер|финанс|юридическ|\bhr\b|персонал|\bit\b|ит[ -]отдел|автоматизаци|"
                       r"чат-бот|администрат|design engineering|конструктор", re.I)
DEPT_PROD = re.compile(r"насос|турбин|компрессор|фильтрац|грануляц|динамическ\w* оборудован|"
                       r"кип\b|скут|\bзра\b|оборудован", re.I)
DEPT_KAM = re.compile(r"группа по работе с|ключев|работе с заказчик|спецпроект|шельф", re.I)


def _regno(title) -> int:
    """Номер реализации в начале названия сделки («871. …»); точка обязательна —
    иначе под правило попадают коды PO и артикулы."""
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(title or ""))
    return int(m.group(1)) if m else 0


def _money(v: float) -> str:
    v = round(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}€{a/1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}€{a/1_000:.0f}K"
    return f"{sign}€{a}"


def _uid(v) -> str:
    """Значение employee-поля Bitrix → id пользователя строкой ('' если пусто)."""
    if isinstance(v, list):
        v = v[0] if v else None
    s = str(v or "").strip()
    return s if s.isdigit() and s != "0" else ""


def _days_since(stamp, today: dt.date) -> int | None:
    s = str(stamp or "")[:10]
    if len(s) != 10:
        return None
    try:
        return (today - dt.date.fromisoformat(s)).days
    except ValueError:
        return None


def roster(client: BitrixClient) -> dict[str, dict]:
    """Состав портала из Bitrix: {uid: {name, pos, depts, active}}.

    `user.get` без фильтра отдаёт только действующих, поэтому уволенных запрашиваем
    вторым вызовом с ACTIVE=N — иначе их сделки выглядят как ничьи.
    """
    people: dict[str, dict] = {}
    for params, default_active in (({"ADMIN_MODE": True}, True),
                                   ({"ADMIN_MODE": True, "FILTER": {"ACTIVE": "N"}}, False)):
        try:
            rows = client.list_paged("user.get", params)
        except Exception:
            continue
        for u in rows:
            uid = str(u.get("ID") or "")
            if not uid or uid in people:
                continue
            dd = u.get("UF_DEPARTMENT") or []
            act = u.get("ACTIVE")
            people[uid] = {
                "name": " ".join(x for x in [u.get("LAST_NAME"), u.get("NAME")] if x).strip() or f"user#{uid}",
                "pos": (u.get("WORK_POSITION") or "").strip(),
                "depts": [str(x) for x in (dd if isinstance(dd, list) else [dd]) if str(x)],
                "active": (str(act).lower() in ("y", "true", "1")) if act is not None else default_active,
            }
    return people


def resolve_role(person: dict, dept_names: dict[str, str]) -> tuple[str, str]:
    """(роль, по чему определена). Каскад: должность → отдел → «вне коммерческого блока»."""
    pos = person.get("pos") or ""
    if POS_PROD.search(pos):
        return "prod", "должность"
    if POS_KAM.search(pos):
        return "kam", "должность"
    for d in person.get("depts") or []:
        nm = dept_names.get(d, "")
        if DEPT_BACK.search(nm):
            continue
        if DEPT_PROD.search(nm):
            return "prod", "отдел"
        if DEPT_KAM.search(nm):
            return "kam", "отдел"
    return "other", "не коммерческая роль"


def deal_categories(client: BitrixClient) -> dict[str, str]:
    """{id воронки: имя}. Берём crm.category.list: он знает настоящее имя воронки 0
    («Реализация»), тогда как crm.dealcategory.list её не возвращает вовсе и в
    справочнике клиента она подписана служебной «Общая»."""
    try:
        res = client.call("crm.category.list", {"entityTypeId": 2}) or {}
        items = (res.get("categories") if isinstance(res, dict) else res) or []
        m = {str(c.get("id")): (c.get("name") or f"воронка #{c.get('id')}") for c in items if c.get("id") is not None}
        if m:
            return m
    except Exception:
        pass
    return {str(k): v for k, v in (client.categories() or {}).items()}


COMMERCIAL_ROLES = ("kam", "prod")


def roles_by_uid(people: dict[str, dict], dept_names: dict[str, str]) -> dict[str, str]:
    """{uid: роль} по всему составу — чтобы каскад «должность → отдел» считался один раз."""
    return {uid: resolve_role(p, dept_names)[0] for uid, p in people.items()}


def responsible(deal: dict, role_of: dict[str, str], people: dict[str, dict]) -> tuple[str, str, str]:
    """Кто ведёт сделку как коммерсант: (uid, роль, источник).

    Тот же каскад, что во вкладках ролей, но для персональных дашбордов: человек
    обязан быть действующим — карточка уволенного не должна попадать ему в KPI.
    Возвращает пустые строки, если коммерсанта у сделки нет.
    """
    for field, role in ((KAM_F, "kam"), (KAM_OLD, "kam"),
                        (PROD_F, "prod"), (PROD_OLD, "prod"), (PROD_HEAD, "prod")):
        u = _uid(deal.get(field))
        if u and people.get(u, {}).get("active"):
            return u, role, "поле"
    owner = str(deal.get("ASSIGNED_BY_ID") or "")
    role = role_of.get(owner, "")
    if owner and role in COMMERCIAL_ROLES and people.get(owner, {}).get("active"):
        return owner, role, "ответственный"
    return "", "", ""


def _blank() -> dict:
    return {"open": 0, "presale": 0, "presaleSum": 0.0, "real": 0, "realSum": 0.0, "buy": 0.0,
            "late": 0, "lateSum": 0.0, "stale": 0, "dead": 0, "noAmt": 0, "noComp": 0,
            "neg": 0, "clean": 0, "created": 0, "won": 0, "wonSum": 0.0, "lost": 0, "lostSum": 0.0,
            "byField": 0, "big": 0, "bigSum": 0.0, "ages": []}


def compute(client: BitrixClient, *, as_of: dt.date | None = None,
            open_deals: list[dict] | None = None, created: list[dict] | None = None,
            orders: list[dict] | None = None) -> dict:
    today = as_of or dt.date.today()
    today_iso = today.isoformat()
    people = roster(client)
    deps = {str(d["ID"]): d.get("NAME", "") for d in client.list_paged("department.get", {})}
    cats = deal_categories(client)
    stage_meta = client.deal_stage_meta()
    catname = lambda c: cats.get(str(c), f"воронка #{c}")

    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)

    if open_deals is None:
        open_deals = client.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "P"}, select=DEAL_SELECT)
    if created is None:
        created = client.list_deals_fast(filter={">=DATE_CREATE": YEAR_START}, select=DEAL_SELECT)
    if orders is None:
        orders = client.list_items(172, filter={">=createdTime": ORDERS_SINCE},
                                   select=["id", "stageId", "opportunity", "currencyId",
                                           "parentId2", "assignedById", DL_CUSTOMER])

    # --- заказы поставщикам: закупка на сделку, живой заказ, просроченное обещание клиенту
    buy_by_deal: dict[str, float] = defaultdict(float)
    order_deals: set[str] = set()     # есть непроигранный заказ (в т.ч. уже исполненный)
    live_deals: set[str] = set()      # заказ ещё в работе
    late_deals: dict[str, int] = {}
    for o in orders:
        did = str(o.get("parentId2") or "")
        stage = str(o.get("stageId") or "")
        if not did or stage.endswith(":FAIL"):
            continue
        buy_by_deal[did] += eur(o.get("opportunity"), o.get("currencyId"))
        order_deals.add(did)
        if stage.endswith(":SUCCESS"):
            continue
        live_deals.add(did)
        dl = str(o.get(DL_CUSTOMER) or "")[:10]
        if dl and dl < today_iso:
            days = (today - dt.date.fromisoformat(dl)).days
            late_deals[did] = max(days, late_deals.get(did, 0))

    # --- роли людей
    role_of, why_of = {}, {}
    for uid, p in people.items():
        role_of[uid], why_of[uid] = resolve_role(p, deps)

    def attribute(d: dict, role: str) -> tuple[str, str]:
        """(uid ответственного за роль, чем определено) — поле сделки, владелец или никто."""
        fields = (KAM_F, KAM_OLD) if role == "kam" else (PROD_F, PROD_OLD, PROD_HEAD)
        for f in fields:
            u = _uid(d.get(f))
            if u:
                return u, "поле"
        owner = str(d.get("ASSIGNED_BY_ID") or "")
        if owner and role_of.get(owner) == role and people.get(owner, {}).get("active"):
            return owner, "владелец"
        return "", ""

    # порог «карточки-выброса» берётся от самих данных: медиана положительных сумм
    # открытых сделок (в €), умноженная на OUTLIER_MULT
    _amts = sorted(eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d in open_deals
                   if str(d.get("CATEGORY_ID") or "0") not in TECH_CATS
                   and eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) > 0)
    med_amt = (_amts[len(_amts) // 2] if _amts else 0.0)
    big_cut = med_amt * OUTLIER_MULT

    agg: dict[tuple[str, str], dict] = defaultdict(_blank)     # (роль, uid) → показатели
    owner_agg: dict[str, dict] = defaultdict(_blank)           # владелец → показатели (для бесхозных)
    funnels: dict[str, Counter] = defaultdict(Counter)
    funnel_sum: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    stages: dict[str, Counter] = defaultdict(Counter)
    details: list[dict] = []
    uncovered = {"kam": [0, 0.0], "prod": [0, 0.0]}            # сделки без закреплённой роли
    tech = 0

    for d in open_deals:
        cat = str(d.get("CATEGORY_ID") or "0")
        if cat in TECH_CATS:
            tech += 1
            continue
        did = str(d["ID"])
        amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        buy = buy_by_deal.get(did, 0.0)
        in_real = (cat == REALIZE_CAT or did in order_deals or buy > 0
                   or _regno(d.get("TITLE")) > 0)
        idle = _days_since(d.get("MOVED_TIME") or d.get("LAST_ACTIVITY_TIME"), today)
        late_days = late_deals.get(did, 0)
        no_amt = not amt
        no_comp = not d.get("COMPANY_ID") or str(d.get("COMPANY_ID")) == "0"
        neg = in_real and buy > 0 and amt > 0 and (amt - buy) < 0
        big = bool(big_cut and amt >= big_cut)
        flawed = bool(late_days or no_amt or no_comp or neg or big
                      or (idle is not None and idle > STALE_DAYS))
        owner = str(d.get("ASSIGNED_BY_ID") or "")
        kam_uid, kam_src = attribute(d, "kam")
        prod_uid, prod_src = attribute(d, "prod")

        def put(bucket: dict, src: str = "") -> None:
            bucket["open"] += 1
            if src == "поле":
                bucket["byField"] += 1
            if idle is not None:
                bucket["ages"].append(idle)
            if in_real:
                bucket["real"] += 1; bucket["realSum"] += amt; bucket["buy"] += buy
            else:
                bucket["presale"] += 1; bucket["presaleSum"] += amt
            if late_days:
                bucket["late"] += 1; bucket["lateSum"] += amt
            if idle is not None and idle > STALE_DAYS:
                bucket["stale"] += 1
                if idle > DEAD_DAYS:
                    bucket["dead"] += 1
            bucket["noAmt"] += no_amt
            bucket["noComp"] += no_comp
            bucket["neg"] += neg
            if big:
                bucket["big"] += 1; bucket["bigSum"] += amt
            bucket["clean"] += (not flawed)

        for role, uid, src in (("kam", kam_uid, kam_src), ("prod", prod_uid, prod_src)):
            if uid:
                put(agg[(role, uid)], src)
                funnels[role][cat] += 1
                funnel_sum[role][cat] += amt
                stages[role][str(d.get("STAGE_ID") or "")] += 1
            else:
                uncovered[role][0] += 1
                uncovered[role][1] += amt
        if owner:
            put(owner_agg[owner])

        details.append({
            "id": did, "t": (d.get("TITLE") or f"Сделка #{did}")[:90],
            "kam": kam_uid, "prod": prod_uid, "own": owner,
            "ownLive": bool(people.get(owner, {}).get("active")),
            "cat": catname(cat), "catId": cat,
            "stage": (stage_meta.get(str(d.get("STAGE_ID") or "")) or {}).get("name", str(d.get("STAGE_ID") or "")),
            "amt": _money(amt), "raw": round(amt), "buyRaw": round(buy),
            "state": "real" if in_real else "presale",
            "idle": idle, "late": late_days, "noAmt": no_amt, "noComp": no_comp, "neg": neg,
            "big": big, "date": str(d.get("DATE_CREATE", ""))[:10],
        })

    # --- результат года: создано / выиграно / проиграно (когорта 2026).
    # ВЫИГРАНА — это НЕ STAGE_SEMANTIC_ID='S': в этом портале победа означает перевод
    # сделки в воронку «Реализация», где она живёт в рабочих стадиях («Оплата получена |
    # Закрытие сделки» и т.п.) с семантикой «в работе». Замер: из 1 662 сделок роли КАМ
    # семантику успеха имеет ОДНА, а в реализацию переведены сотни. Поэтому победа
    # считается так же, как в остальном дашборде: воронка реализации, либо заказ
    # поставщику, либо номер реализации в названии.
    for d in created:
        cat = str(d.get("CATEGORY_ID") or "0")
        if cat in TECH_CATS:
            continue
        did = str(d["ID"])
        amt = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        reached = (cat == REALIZE_CAT or did in order_deals or _regno(d.get("TITLE")) > 0)
        owner = str(d.get("ASSIGNED_BY_ID") or "")
        targets = [(r, u) for r, (u, _s) in
                   (("kam", attribute(d, "kam")), ("prod", attribute(d, "prod"))) if u]
        for role, uid in targets:
            b = agg[(role, uid)]
            b["created"] += 1
            if sem == "F":
                b["lost"] += 1; b["lostSum"] += amt
            elif reached:
                b["won"] += 1; b["wonSum"] += amt
        if owner:
            b = owner_agg[owner]
            b["created"] += 1
            if sem == "F":
                b["lost"] += 1; b["lostSum"] += amt
            elif reached:
                b["won"] += 1; b["wonSum"] += amt

    def row(uid: str, a: dict) -> dict:
        p = people.get(uid, {"name": f"user#{uid}", "pos": "", "depts": [], "active": False})
        margin = a["realSum"] - a["buy"]
        wl = a["won"] + a["lost"]
        ages = sorted(a["ages"])
        return {
            "uid": uid, "name": p["name"], "pos": p["pos"] or "—",
            "dept": ", ".join(deps.get(x, x) for x in (p.get("depts") or [])) or "—",
            "active": bool(p.get("active")), "why": why_of.get(uid, ""),
            "open": a["open"], "byField": a["byField"],
            "presale": a["presale"], "presaleSum": _money(a["presaleSum"]), "presaleRaw": round(a["presaleSum"]),
            "real": a["real"], "realSum": _money(a["realSum"]), "realRaw": round(a["realSum"]),
            "buy": _money(a["buy"]), "buyRaw": round(a["buy"]),
            "margin": _money(margin), "marginRaw": round(margin),
            "marginPct": (round(margin / a["realSum"] * 100) if a["realSum"] else None),
            "created": a["created"], "won": a["won"], "wonSum": _money(a["wonSum"]),
            "lost": a["lost"], "winRate": (round(a["won"] / wl * 100) if wl else None),
            "late": a["late"], "lateSum": _money(a["lateSum"]),
            "stale": a["stale"], "dead": a["dead"],
            "noAmt": a["noAmt"], "noComp": a["noComp"], "neg": a["neg"],
            "big": a["big"], "bigSum": _money(a["bigSum"]),
            "flaws": a["open"] - a["clean"],
            "cleanPct": (round(a["clean"] / a["open"] * 100) if a["open"] else None),
            "medIdle": (ages[len(ages) // 2] if ages else None),
            "loadRaw": round(a["presaleSum"] + a["realSum"]),
        }

    def block(role: str) -> dict:
        rows = [row(u, a) for (r, u), a in agg.items() if r == role and (a["open"] or a["created"])]
        rows.sort(key=lambda r: -(r["realRaw"] + r["presaleRaw"]))
        tot = _blank()
        for (r, _u), a in agg.items():
            if r != role:
                continue
            for k, v in a.items():
                if k == "ages":
                    tot["ages"] += v
                else:
                    tot[k] += v
        margin = tot["realSum"] - tot["buy"]
        wl = tot["won"] + tot["lost"]
        winrate = round(tot["won"] / wl * 100) if wl else 0
        weighted = tot["presaleSum"] * winrate / 100
        n = len([r for r in rows if r["active"]]) or len(rows)
        opens = sorted(r["open"] for r in rows)
        # активные в роли, на кого не записано ни одной сделки
        idle_people = [{"uid": u, "name": p["name"], "pos": p["pos"] or "—"}
                       for u, p in people.items()
                       if p["active"] and role_of.get(u) == role
                       and not agg[(role, u)]["open"] and not agg[(role, u)]["created"]]
        unc_n, unc_sum = uncovered[role]
        return {
            "key": role,
            "people": rows,
            "idlePeople": sorted(idle_people, key=lambda x: x["name"]),
            "funnels": [{"cat": catname(c), "catId": c, "n": k, "sum": _money(funnel_sum[role][c]),
                         "raw": round(funnel_sum[role][c])}
                        for c, k in funnels[role].most_common()],
            "stages": [{"stage": (stage_meta.get(s) or {}).get("name", s), "id": s, "n": k,
                        "cat": catname((stage_meta.get(s) or {}).get("cat", "0"))}
                       for s, k in stages[role].most_common(20)],
            "uncovered": {"n": unc_n, "sum": _money(unc_sum)},
            "totals": {
                "people": n, "peopleAll": len(rows),
                "open": tot["open"], "byField": tot["byField"],
                "presale": tot["presale"], "presaleSum": _money(tot["presaleSum"]),
                "presaleRaw": round(tot["presaleSum"]),
                "real": tot["real"], "realSum": _money(tot["realSum"]), "realRaw": round(tot["realSum"]),
                "buy": _money(tot["buy"]), "margin": _money(margin),
                "marginPct": (round(margin / tot["realSum"] * 100) if tot["realSum"] else 0),
                "created": tot["created"], "won": tot["won"], "wonSum": _money(tot["wonSum"]),
                "lost": tot["lost"], "winRate": winrate,
                "weighted": _money(weighted), "weightedRaw": round(weighted),
                "late": tot["late"], "lateSum": _money(tot["lateSum"]),
                "stale": tot["stale"], "dead": tot["dead"], "noAmt": tot["noAmt"],
                "noComp": tot["noComp"], "neg": tot["neg"],
                "big": tot["big"], "bigSum": _money(tot["bigSum"]),
                "bigShare": (round(tot["bigSum"] / (tot["presaleSum"] + tot["realSum"]) * 100)
                             if (tot["presaleSum"] + tot["realSum"]) else 0),
                "flaws": tot["open"] - tot["clean"],
                "cleanPct": (round(tot["clean"] / tot["open"] * 100) if tot["open"] else None),
                "perPersonDeals": (round(tot["open"] / n, 1) if n else 0),
                "perPersonSum": (_money((tot["presaleSum"] + tot["realSum"]) / n) if n else "—"),
                "medianDeals": (opens[len(opens) // 2] if opens else 0),
                "maxDeals": (opens[-1] if opens else 0),
            },
        }

    kam, prod = block("kam"), block("prod")

    # --- бесхозное: открытые сделки уволенных владельцев
    fired_rows = [row(u, a) for u, a in owner_agg.items()
                  if a["open"] and not people.get(u, {}).get("active", False)]
    fired_rows.sort(key=lambda r: -r["open"])
    orphan = {
        "rows": fired_rows,
        "n": sum(r["open"] for r in fired_rows),
        "sum": _money(sum(r["presaleRaw"] + r["realRaw"] for r in fired_rows)),
        "people": len(fired_rows),
        "real": sum(r["real"] for r in fired_rows),
        "late": sum(r["late"] for r in fired_rows),
    }

    # Сверка. Роли пересекаются (на одной сделке бывает и КАМ, и продукт-оунер), поэтому
    # «всё сходится» проверяется не суммой ролей, а покрытием: закреплено / бесхозно / ничьё.
    live_open = len(details)
    nobody = [d for d in details if not d["kam"] and not d["prod"] and d["ownLive"]]
    recon = {
        "openTotal": live_open, "tech": tech,
        "kam": kam["totals"]["open"], "prod": prod["totals"]["open"],
        "both": sum(1 for d in details if d["kam"] and d["prod"]),
        "none": len(nobody), "noneSum": _money(sum(d["raw"] for d in nobody)),
        "orphan": orphan["n"], "orphanSum": orphan["sum"],
        "orphanCovered": sum(1 for d in details if not d["ownLive"] and (d["kam"] or d["prod"])),
    }
    staff = {
        "total": len(people),
        "active": sum(1 for p in people.values() if p["active"]),
        "fired": sum(1 for p in people.values() if not p["active"]),
        "kam": sum(1 for u, p in people.items() if p["active"] and role_of.get(u) == "kam"),
        "prod": sum(1 for u, p in people.items() if p["active"] and role_of.get(u) == "prod"),
    }
    return {
        "label": f"на {today.strftime('%d.%m.%Y')}",
        "roles": {"kam": kam, "prod": prod},
        "orphan": orphan, "recon": recon, "staff": staff,
        "deals": details,
        "params": {"stale": STALE_DAYS, "dead": DEAD_DAYS, "mult": OUTLIER_MULT,
                   "medAmt": _money(med_amt), "bigCut": _money(big_cut)},
    }
