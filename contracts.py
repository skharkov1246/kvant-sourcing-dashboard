"""Вкладка «Реализация» — сделки ВОРОНКИ РЕАЛИЗАЦИИ (категория 0) + их заказы поставщикам.

Вселенная: все сделки в воронке реализации (кат. 0, номера «N. …» до текущего максимума),
плюс сделки с заказами СП-172, ещё числящиеся в других воронках (помечаются «вне воронки»).

Модель данных Bitrix:
  • СП-172 «Заказы» = заказы ПОСТАВЩИКАМ → их opportunity = ЗАКУПКА, companyId = поставщик.
  • Продажа клиенту и сам клиент — на родительской СДЕЛКЕ (parentId2).
  • ЗАКУПКА — каскад из 3 источников (см. BUY_*): Σ заказов СП-172 → money-поля сделки
    из экономики проекта («Cost (rub)» / «Offer sum … of selected supplier») → «нет данных».
    Маржа считается ТОЛЬКО при наличии источника (никаких фиктивных 100%).
  • СКОРОСТЬ — по crm.stagehistory (первый вход в каждую стадию, полное время):
    тайминг стадий сделки в кат.0, лаг заведения первого заказа, стадии заказов СП-172.
  • ЗАМЕДЛЕННОСТЬ: возраст текущей стадии > max(2×медиана по этой стадии, SLOW_MIN_DAYS).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from bitrix_client import BitrixClient

_SYM = {"EUR": "€", "USD": "$", "CNY": "¥", "RUB": "₽", "INR": "₹", "GBP": "£", "AED": "AED ", "JPY": "¥"}

# money-поля сделки, продублированные из «экономики проекта» (см. зонд crm.deal.fields)
BUY_ECON_RUB = "UF_CRM_1710446284794"   # «Cost (rub)» — себестоимость в рублях
BUY_ECON_VAL = "UF_CRM_1704981063967"   # «Offer sum and currency of selected supplier»
ECON_MARGIN = "UF_CRM_1704981084196"    # «Margin»
ECON_PAID = "UF_CRM_1713874110281"      # «Оплачено»
ECON_REST = "UF_CRM_1713874579940"      # «Остаток к оплате»
ECON_LINK = "UF_CRM_1740133235324"      # ссылка (clck.ru) на файл «Экономика проекта» в Я.Диске

# плановые даты заказа СП-172 (см. зонд v9: поле дедлайна подтверждено на 72/72 строк
# выгрузки «Контроль дедлайнов» — коллеги контролируют именно его)
DL_CUSTOMER = "ufCrm20_1728900218435"   # «Date of deadline to customer» — обещано клиенту
PROD_END = "ufCrm20_1724941935"         # «Production end date, budget» — плановый конец производства
INBOUND_PLAN = "ufCrm20_1709294315471"  # «Inbound Delivery Date, planned» — плановое поступление
SCHEMA_F = "ufCrm20_1724941500"         # «Схема поставки» (справочник, 39 значений)
CITY_F = "ufCrm20_1742552190053"        # «Город доставки заказчику по бюджету»
OSS_F = "ufCrm20_1723236618"            # «Deal support specialist» — ОСС заказа (совпал 73/74)
OSS_DEAL = "UF_CRM_1715604558"          # «Сопровождение сделки» — тот же человек на сделке (74/74)

CAP_RATE = 0.20          # стоимость денег, годовых: по чему считаем упущенную доходность
                         # от поздней отгрузки (решение владельца)

SLOW_MIN_DAYS = 7        # порог замедленности не бывает ниже недели
SLOW_MULT = 2.0          # … и не ниже 2× медианы по стадии
HIST_SINCE = "2024-06-01T00:00:00"  # глубина истории стадий (бенчмарки + таймлайны)


def _regno(*titles) -> int:
    """Порядковый номер сделки в реализации — число В НАЧАЛЕ названия, за которым идёт точка:
    «871. …» у сделки, «871/1. …» у заказа. 0 — если номера нет."""
    for t in titles:
        m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(t or ""))
        if m:
            return int(m.group(1))
    return 0


def _money(v: float) -> str:
    v = round(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{sign}€{a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}€{a/1_000:.0f}K"
    return f"{sign}€{a}"


def _orig(v, cur: str) -> str:
    v = round(float(v or 0))
    s = f"{v:,}".replace(",", " ")
    sym = _SYM.get(cur)
    return f"{sym}{s}" if sym else f"{s} {cur or ''}".strip()


_MSK = dt.timezone(dt.timedelta(hours=3))


def _parse_dt(s: str) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return d.replace(tzinfo=_MSK) if d.tzinfo is None else d   # метки Bitrix — портал (МСК)


def _days_between(a: str, b: str) -> float | None:
    da, db = _parse_dt(a), _parse_dt(b)
    if not da or not db:
        return None
    return max((db - da).total_seconds() / 86400.0, 0.0)


def _pdate(v) -> dt.date | None:
    """Плановая дата из поля СП-172 ('2026-03-31T03:00:00+03:00' или '2026-03-31') → date."""
    s = str(v or "")[:10]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def _median(vals: list) -> float | None:
    sv = sorted(v for v in vals if v is not None)
    if not sv:
        return None
    n = len(sv)
    return sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2.0


def _parse_econ_money(raw, rate) -> float:
    """Bitrix money-поле '1234.56|RUB' → €. 0 если пусто/мусор."""
    s = str(raw or "")
    if "|" not in s:
        return 0.0
    amt, cur = s.split("|", 1)
    try:
        return float(amt or 0) * rate.get(cur, 1.0)
    except ValueError:
        return 0.0


def compute(client: BitrixClient, *, as_of: dt.date | None = None) -> dict:
    today = as_of or dt.date.today()
    now_iso = dt.datetime.now(_MSK).isoformat()
    curlist = client.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in curlist}
    def eur(o, cu): return float(o or 0) * rate.get(cu, 1.0)
    # курсы в Битриксе заданы вручную и не тянутся из внешнего источника — показываем дату,
    # чтобы расхождение с любой другой отчётностью было объяснимо, а не спорно
    rate_dates = [str(x.get("DATE_UPDATE") or "")[:10] for x in curlist
                  if x.get("CURRENCY") in ("RUB", "USD", "EUR") and x.get("DATE_UPDATE")]
    _asof = min(rate_dates) if rate_dates else ""
    _d = _pdate(_asof)
    rate_asof = _d.strftime("%d.%m.%Y") if _d else _asof
    base_cur = next((x.get("CURRENCY") for x in curlist if x.get("BASE") == "Y"), "EUR")

    # 1. все непроигранные заказы поставщикам (закупки)
    orders = client.list_items(172, filter={}, select=["id", "title", "companyId", "parentId2",
                                                        "opportunity", "currencyId", "stageId",
                                                        "createdTime", "movedTime",
                                                        "assignedById",
                                                        DL_CUSTOMER, PROD_END, INBOUND_PLAN,
                                                        SCHEMA_F, CITY_F, OSS_F])
    orders = [o for o in orders if not str(o.get("stageId", "")).endswith(":FAIL")]
    supl = client.companies_by_ids({str(o["companyId"]) for o in orders if o.get("companyId")})
    # справочник «Схема поставки»: значения приходят ID-шками
    try:
        f172 = (client.call("crm.item.fields", {"entityTypeId": 172}) or {}).get("fields", {})
        schema_enum = {str(i.get("ID")): i.get("VALUE")
                       for i in ((f172.get(SCHEMA_F) or {}).get("items") or [])}
    except Exception:
        schema_enum = {}

    # 2. группировка заказов по сделке
    by_deal: dict[str, list] = defaultdict(list)
    orphan = []
    for o in orders:
        did = str(o.get("parentId2") or "")
        (by_deal[did] if did else orphan).append(o)

    # 3. вселенная: сделки ВОРОНКИ РЕАЛИЗАЦИИ (кат.0) + родители заказов вне её
    DSEL = ["ID", "TITLE", "OPPORTUNITY", "CURRENCY_ID", "COMPANY_ID", "STAGE_ID",
            "STAGE_SEMANTIC_ID", "CATEGORY_ID", "DATE_CREATE", "ASSIGNED_BY_ID", OSS_DEAL,
            BUY_ECON_RUB, BUY_ECON_VAL, ECON_MARGIN, ECON_PAID, ECON_REST, ECON_LINK]
    cat0 = client.list_deals_fast(filter={"CATEGORY_ID": 0}, select=DSEL)
    deals: dict[str, dict] = {str(d["ID"]): d for d in cat0}
    extra_ids = [d for d in by_deal if d and d not in deals]
    if extra_ids:
        deals.update(client.deals_by_ids(extra_ids, select=DSEL))
    clients = client.companies_by_ids({str(d.get("COMPANY_ID")) for d in deals.values() if d.get("COMPANY_ID")})

    # 4. справочники стадий + история стадий (полное время первого входа)
    dstages = client.deal_stages_cat(0)                    # упорядоченные стадии воронки реализации
    dorder = {sid: i for i, sid in enumerate(dstages)}
    ostages: dict[str, str] = {}                           # стадии всех воронок СП-172
    ocat_ids = sorted({int(o.get("categoryId") or 0) for o in orders if o.get("categoryId")} | {26})
    for cid in ocat_ids:
        try:
            ostages.update(client.spa_stages(172, cid))
        except Exception:
            pass
    dhist = client.stage_history(2, category_id=0, since=HIST_SINCE)
    ohist = client.stage_history(172, since=HIST_SINCE)

    def _stage_name(sid: str, cat=None) -> str:
        if sid in dstages:
            return dstages[sid]
        return ostages.get(sid) or sid

    # 5. бенчмарки: медианная длительность каждой стадии ЗАКАЗОВ (по истории переходов)
    odur: dict[str, list[float]] = defaultdict(list)
    for oid, entries in ohist.items():
        for (s1, t1), (_s2, t2) in zip(entries, entries[1:]):
            d = _days_between(t1, t2)
            if d is not None:
                odur[s1].append(d)
    omed = {s: round(_median(v) or 0, 1) for s, v in odur.items() if v}
    # …и стадий СДЕЛОК в кат.0
    ddur: dict[str, list[float]] = defaultdict(list)
    for did_h, entries in dhist.items():
        for (s1, t1), (_s2, t2) in zip(entries, entries[1:]):
            d = _days_between(t1, t2)
            if d is not None:
                ddur[s1].append(d)
    dmed = {s: round(_median(v) or 0, 1) for s, v in ddur.items() if v}

    def _slow_threshold(stage_id: str, med: dict) -> float:
        return max(SLOW_MULT * med.get(stage_id, 0.0), float(SLOW_MIN_DAYS))

    # 6. строки: одна строка = одна сделка воронки реализации (или с заказами)
    rows = []
    for did, d in deals.items():
        ords = by_deal.get(did, [])
        in_cat0 = str(d.get("CATEGORY_ID") or "0") == "0"
        sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
        seq = _regno(d.get("TITLE"), ords[0].get("title") if ords else "")
        if not in_cat0 and not ords:
            continue                      # чужая воронка без заказов — не наша вселенная
        if in_cat0 and not ords and seq == 0 and not d.get("OPPORTUNITY"):
            continue                      # пустышки без номера/сумм/заказов

        sale = eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        # --- каскад закупки: заказы → экономика (money-поля) → нет данных
        buy_orders = sum(eur(o.get("opportunity"), o.get("currencyId")) for o in ords)
        buy_econ = _parse_econ_money(d.get(BUY_ECON_RUB), rate) or _parse_econ_money(d.get(BUY_ECON_VAL), rate)
        if buy_orders > 0:
            buy, buy_src = buy_orders, "orders"
        elif buy_econ > 0:
            buy, buy_src = buy_econ, "econ"
        else:
            buy, buy_src = 0.0, "none"
        discrep = (buy_orders > 0 and buy_econ > 0
                   and abs(buy_orders - buy_econ) / max(buy_orders, buy_econ) > 0.2)
        margin = (sale - buy) if (sale and buy_src != "none") else None
        # красный флаг: экономики проекта нет ни ссылкой на файл, ни money-полями
        econ_link = str(d.get(ECON_LINK) or "").strip()
        no_econ = not econ_link and buy_econ <= 0

        # --- тайминг стадий сделки в кат.0
        tl = dhist.get(did, [])
        timeline = []
        for i, (sid, ts) in enumerate(tl):
            nxt = tl[i + 1][1] if i + 1 < len(tl) else now_iso
            timeline.append({"stage": sid, "name": _stage_name(sid), "at": str(ts)[:16].replace("T", " "),
                             "days": round(_days_between(ts, nxt) or 0, 1)})
        entered_real = tl[0][1] if tl else ""
        cur_stage = str(d.get("STAGE_ID") or "")
        stage_days = None
        if tl:
            # возраст ТЕКУЩЕЙ стадии — от последнего входа
            stage_days = round(_days_between(tl[-1][1], now_iso) or 0, 1)
        done = sem == "S" or (bool(ords) and all(str(o.get("stageId", "")).endswith(":SUCCESS") for o in ords))
        closed = sem in ("S", "F")

        # --- заказы: стадия, возраст стадии, дедлайн клиенту, замедленность
        ords_det = []
        slow_orders = 0
        late_orders = 0            # заказов с просроченным обещанием клиенту
        worst_late = None          # самая глубокая просрочка по сделке, дн
        late_buy = 0.0             # закупка под просроченными заказами, €
        late_money_days = 0.0      # €×дни закупки: сколько закуплено и как долго висит
        late_nosum = 0             # просроченных заказов без суммы (в деньги не попадают)
        late_rev = 0.0             # стоимость отгрузки, зависшей из-за просрочки, €
        late_rev_days = 0.0        # €×дни выручки — база расчёта упущенной доходности
        no_dl = 0                  # живых заказов без плановой даты клиенту
        first_order_ts = min((str(o.get("createdTime") or "") for o in ords), default="")
        n_live_ords = sum(1 for o in ords if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL")))
        for o in ords:
            osid = str(o.get("stageId") or "")
            olive = not (osid.endswith(":SUCCESS") or osid.endswith(":FAIL"))
            otl = ohist.get(str(o.get("id")), [])
            oin = otl[-1][1] if otl else str(o.get("movedTime") or o.get("createdTime") or "")
            odays = round(_days_between(oin, now_iso) or 0, 1) if oin else None
            othr = _slow_threshold(osid, omed)
            oslow = bool(olive and odays is not None and odays > othr)
            if oslow:
                slow_orders += 1
            # обещание клиенту: та же дата, по которой снабжение ведёт «Контроль дедлайнов»
            dl = _pdate(o.get(DL_CUSTOMER))
            dl_days = (today - dl).days if dl else None       # >0 — просрочено, <0 — есть запас
            olate = bool(olive and dl_days is not None and dl_days > 0)
            obuy = eur(o.get("opportunity"), o.get("currencyId"))
            # стоимость отгрузки этого заказа: доля сделки пропорционально закупке
            # (как в экономике проекта); если сумм закупки нет — поровну между живыми
            if buy_orders > 0 and obuy > 0:
                o_rev = sale * (obuy / buy_orders)
            elif n_live_ords:
                o_rev = sale / n_live_ords
            else:
                o_rev = 0.0
            if olate:
                late_orders += 1
                late_buy += obuy
                late_money_days += obuy * dl_days
                late_rev += o_rev
                late_rev_days += o_rev * dl_days
                if obuy <= 0:
                    late_nosum += 1
                worst_late = dl_days if worst_late is None else max(worst_late, dl_days)
            if olive and not dl:
                no_dl += 1
            pend = _pdate(o.get(PROD_END))
            ords_det.append({
                "id": str(o.get("id")), "title": str(o.get("title") or "")[:70],
                "sup": supl.get(str(o.get("companyId"))) or "—",
                "stage": osid, "stageName": _stage_name(osid),
                "days": odays, "thr": round(othr, 1), "slow": oslow, "live": olive,
                "buyLbl": _money(obuy), "buyRaw": round(obuy),
                "created": str(o.get("createdTime") or "")[:10],
                "dl": dl.strftime("%d.%m.%Y") if dl else "",
                "dlDays": dl_days, "late": olate,
                "revEur": round(o_rev), "revLbl": _money(o_rev),
                "lossEur": round(o_rev * dl_days / 365 * CAP_RATE) if olate else 0,
                "burnDay": round(o_rev * CAP_RATE / 365, 1) if olive else 0,
                "schema": schema_enum.get(str(o.get(SCHEMA_F)), "") if o.get(SCHEMA_F) else "",
                "city": str(o.get(CITY_F) or ""),
                "oss": client.user_name(o.get(OSS_F)) if o.get(OSS_F) else "",
                "prodEnd": pend.strftime("%d.%m.%Y") if pend else "",
                # план производства заканчивается позже обещания клиенту — план сам себе противоречит
                "prodAfterDl": bool(pend and dl and pend > dl),
                "inb": (_pdate(o.get(INBOUND_PLAN)).strftime("%d.%m.%Y")
                        if _pdate(o.get(INBOUND_PLAN)) else ""),
            })
        ords_det.sort(key=lambda x: (not x["live"], -(x["dlDays"] if x["dlDays"] is not None else -9999),
                                     -(x["days"] or 0)))

        # --- лаг заведения первого заказа (вход в кат.0 → первый заказ)
        first_lag = None
        if entered_real and first_order_ts:
            first_lag = round(_days_between(entered_real, first_order_ts) or 0, 1)
        elif entered_real and not ords and not closed:
            first_lag = round(_days_between(entered_real, now_iso) or 0, 1)  # заказа ещё нет — лаг растёт

        # --- замедленность сделки
        dthr = _slow_threshold(cur_stage, dmed)
        slow_deal = bool(not closed and in_cat0 and stage_days is not None and stage_days > dthr)
        no_orders_slow = bool(not closed and in_cat0 and not ords
                              and first_lag is not None and first_lag > SLOW_MIN_DAYS)
        late = bool(not closed and late_orders)
        slow = slow_deal or slow_orders > 0 or no_orders_slow or late
        why = []
        if late:
            why.append(f"просрочено обещание клиенту: {late_orders} заказ(ов), макс. {worst_late} дн")
        if slow_deal:
            why.append(f"стадия «{_stage_name(cur_stage)}» {stage_days} дн (порог {round(dthr, 1)})")
        if slow_orders:
            why.append(f"замедленных заказов: {slow_orders}")
        if no_orders_slow:
            why.append(f"нет ни одного заказа {first_lag} дн после входа в реализацию")

        suppliers = sorted({supl.get(str(o.get("companyId"))) for o in ords if o.get("companyId")} - {None})
        entered = (str(entered_real)[:10] if entered_real
                   else min((str(o.get("createdTime") or "")[:10] for o in ords if o.get("createdTime")), default=""))
        dtitle = d.get("TITLE") or (ords[0].get("title") if ords else "")
        rows.append({
            "deal": did, "seq": seq, "entered": entered,
            "customer": clients.get(str(d.get("COMPANY_ID"))) or "—",
            "title": (dtitle or f"Сделка #{did}")[:90],
            "saleEur": round(sale), "saleLbl": _money(sale),
            "saleOrig": _orig(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")), "saleCur": d.get("CURRENCY_ID") or "",
            "buyEur": round(buy), "buyLbl": _money(buy) if buy_src != "none" else "—",
            "buySrc": buy_src, "buyOrdersEur": round(buy_orders), "buyEconEur": round(buy_econ),
            "discrep": discrep, "econLink": bool(econ_link), "noEcon": no_econ,
            "marginEur": round(margin) if margin is not None else None,
            "marginLbl": _money(margin) if margin is not None else "—",
            "marginPct": round(margin / sale * 100) if (margin is not None and sale) else None,
            "econPaid": _money(_parse_econ_money(d.get(ECON_PAID), rate)) if d.get(ECON_PAID) else "",
            "econRest": _money(_parse_econ_money(d.get(ECON_REST), rate)) if d.get(ECON_REST) else "",
            "norders": len(ords), "suppliers": ", ".join(suppliers)[:70],
            # разрезы, которые ведёт снабжение: ответственный сделки, схема поставки, город
            "manager": client.user_name(d.get("ASSIGNED_BY_ID")) or "—",
            "oss": (", ".join(sorted({o["oss"] for o in ords_det if o["oss"]}))[:60]
                    or client.user_name(d.get(OSS_DEAL)) or "—"),
            "schemas": ", ".join(sorted({o["schema"] for o in ords_det if o["schema"]}))[:60],
            "cities": ", ".join(sorted({o["city"] for o in ords_det if o["city"]}))[:50],
            "done": done, "closed": closed, "lost": sem == "F",
            "inCat0": in_cat0,
            "stage": cur_stage,
            "stageName": _stage_name(cur_stage) if in_cat0 else "вне воронки",
            "stageDays": stage_days, "stageThr": round(dthr, 1),
            "slow": slow, "why": " · ".join(why),
            # дедлайн клиенту (поле «Date of deadline to customer» заказов СП-172)
            "late": late, "lateN": late_orders, "lateDays": worst_late,
            "lateBuy": round(late_buy), "lateBuyLbl": _money(late_buy),
            "lateMoneyDays": round(late_money_days), "lateNoSum": late_nosum,
            "lateRev": round(late_rev), "lateRevLbl": _money(late_rev),
            "lateRevDays": round(late_rev_days),
            "lossEur": round(late_rev_days / 365 * CAP_RATE),
            "lossLbl": _money(late_rev_days / 365 * CAP_RATE),
            "burnDay": round(late_rev * CAP_RATE / 365),
            "noDl": no_dl,
            "dlNext": min((o["dl"] for o in ords_det if o["live"] and o["dl"]),
                          key=lambda s: s[6:] + s[3:5] + s[:2], default=""),
            "firstLag": first_lag,
            "timeline": timeline, "orders": ords_det,
            "hasSale": bool(d.get("OPPORTUNITY")),
        })
    rows.sort(key=lambda r: -r["seq"])

    live_rows = [r for r in rows if not r["closed"]]
    slow_rows = [r for r in live_rows if r["slow"]]

    # 7. скорость: сводные метрики
    cycle_days = []                     # полный цикл заказа: создание → SUCCESS
    for oid, entries in ohist.items():
        succ = next((t for s, t in entries if s.endswith(":SUCCESS")), None)
        if succ and entries:
            d0 = _days_between(entries[0][1], succ)
            if d0 is not None:
                cycle_days.append(d0)
    lag_vals = [r["firstLag"] for r in rows if r["firstLag"] is not None and r["norders"] > 0]
    med_cycle = _median(cycle_days)
    med_lag = _median(lag_vals)

    # текущая просрочка по стадиям: сколько живых сидит в стадии, сколько из них за порогом
    # и сколько дней в среднем теряется сверх порога
    o_now: dict[str, list] = defaultdict(lambda: [0, 0, 0.0])   # stage → [в стадии, просрочено, Σ дней сверх порога]
    d_now: dict[str, list] = defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        if not r["closed"] and r["inCat0"] and r["stageDays"] is not None:
            a = d_now[r["stage"]]
            a[0] += 1
            if r["stageDays"] > r["stageThr"]:
                a[1] += 1
                a[2] += r["stageDays"] - r["stageThr"]
        for o in r["orders"]:
            if o["live"] and o["days"] is not None:
                a = o_now[o["stage"]]
                a[0] += 1
                if o["days"] > o["thr"]:
                    a[1] += 1
                    a[2] += o["days"] - o["thr"]

    def _now_stats(s: str, now: dict) -> dict:
        live_n, over_n, lost = now.get(s, [0, 0, 0.0])
        return {"live": live_n, "over": over_n,
                "lostAvg": round(lost / over_n, 1) if over_n else 0}

    # бенчмарки стадий заказов для графика «где копится время» (только осмысленные стадии)
    bench = [{"stage": s, "name": _stage_name(s), "med": m, "n": len(odur[s]),
              **_now_stats(s, o_now)}
             for s, m in sorted(omed.items(), key=lambda kv: -kv[1])
             if len(odur[s]) >= 5 and not (s.endswith(":FAIL"))][:14]
    dbench = [{"stage": s, "name": _stage_name(s), "med": m, "n": len(ddur[s]),
               **_now_stats(s, d_now)}
              for s, m in dmed.items() if len(ddur[s]) >= 5]
    dbench.sort(key=lambda x: dorder.get(x["stage"], 99))

    maxseq = max((r["seq"] for r in rows), default=0)
    sale_sum = sum(r["saleEur"] for r in rows)
    buy_sum = sum(r["buyEur"] for r in rows)
    margin_sum = sum(r["marginEur"] for r in rows if r["marginEur"] is not None)
    sale_m = sum(r["saleEur"] for r in rows if r["marginEur"] is not None)
    n_econ = sum(1 for r in rows if r["buySrc"] == "econ")
    n_nobuy = sum(1 for r in rows if r["buySrc"] == "none")
    orphan_buy = sum(eur(o.get("opportunity"), o.get("currencyId")) for o in orphan)
    n_contracts = sum(1 for r in rows if r["norders"] > 0)

    # --- дедлайны клиенту: сводка по живым заказам (то же поле, что в «Контроле дедлайнов») ---
    late_rows = [r for r in live_rows if r["late"]]
    late_buy_sum = sum(r["lateBuy"] for r in late_rows)
    late_orders_n = sum(r["lateN"] for r in late_rows)
    nodl_orders_n = sum(r["noDl"] for r in live_rows)
    live_orders_n = sum(1 for r in live_rows for o in r["orders"] if o["live"])
    depth = {"<30": 0, "30-90": 0, "90-180": 0, ">180": 0}
    for r in live_rows:
        for o in r["orders"]:
            if o["late"]:
                dd = o["dlDays"]
                depth["<30" if dd < 30 else "30-90" if dd < 90 else "90-180" if dd < 180 else ">180"] += 1
    prod_after = sum(1 for r in live_rows for o in r["orders"] if o["live"] and o["prodAfterDl"])

    # --- ЦЕНА ПРОСРОЧКИ В ДЕНЬГАХ ---
    # 1) евро-дни: сколько денег закупки и сколько дней сверх обещания клиенту они висят.
    #    Это база: умножив на годовую ставку/365, получаем стоимость замороженного капитала.
    # 2) выручка и маржа сделок, где есть хоть один просроченный заказ — деньги, которые
    #    должны были закрыться, но ещё не закрылись (риск, а не потеря).
    money_days = sum(r["lateMoneyDays"] for r in late_rows)
    # ПОТЕРИ НА ИНВЕСТИЦИЯХ: стоимость отгрузки, которую клиент оплатил бы раньше,
    # умноженная на дни задержки и на ставку — упущенная доходность (методика владельца)
    rev_days = sum(r["lateRevDays"] for r in late_rows)
    loss_total = rev_days / 365 * CAP_RATE
    late_rev_total = sum(r["lateRev"] for r in late_rows)
    burn_day = late_rev_total * CAP_RATE / 365          # сгорает за каждый следующий день простоя
    # что отгружать первым: заказы с наибольшей ценой одного дня задержки
    ship_first = []
    for r in live_rows:
        for o in r["orders"]:
            if o["live"] and o["burnDay"] > 0:
                ship_first.append({
                    "seq": r["seq"], "deal": r["deal"], "customer": r["customer"],
                    "title": o["title"], "sup": o["sup"], "stageName": o["stageName"],
                    "dl": o["dl"], "dlDays": o["dlDays"], "late": o["late"],
                    "revLbl": o["revLbl"], "burnDay": o["burnDay"],
                    "lossLbl": _money(o["lossEur"]), "lossEur": o["lossEur"]})
    ship_first.sort(key=lambda x: -x["burnDay"])
    ship_first = ship_first[:12]
    late_nosum_n = sum(r["lateNoSum"] for r in late_rows)
    rev_at_risk = sum(r["saleEur"] for r in late_rows)
    margin_at_risk = sum(r["marginEur"] for r in late_rows if r["marginEur"] is not None)
    avg_late = round(money_days / late_buy_sum, 1) if late_buy_sum else 0   # средневзвеш. просрочка, дн
    # топ сделок по евро-дням — где деньги стоят дольше и больше всего
    top_cost = sorted(late_rows, key=lambda r: -r["lateMoneyDays"])[:10]
    cost_top = [{"deal": r["deal"], "seq": r["seq"], "customer": r["customer"],
                 "days": r["lateDays"], "buyLbl": r["lateBuyLbl"], "buy": r["lateBuy"],
                 "moneyDays": r["lateMoneyDays"], "saleLbl": r["saleLbl"],
                 "manager": r["manager"], "oss": r["oss"]} for r in top_cost]

    # разрезы просрочки: по ответственному сделки и по схеме поставки
    def _cut(keyfn) -> list:
        agg: dict[str, list] = defaultdict(lambda: [0, 0, 0.0, 0])   # [просроч. заказов, сделок, € , всего живых заказов]
        for r in live_rows:
            for o in r["orders"]:
                if not o["live"]:
                    continue
                k = keyfn(r, o) or "—"
                agg[k][3] += 1
                if o["late"]:
                    agg[k][0] += 1
                    agg[k][2] += o["buyRaw"]
            if r["late"]:
                agg[keyfn(r, r["orders"][0]) if r["orders"] else "—"][1] += 1
        out = [{"name": k, "lateOrders": v[0], "lateDeals": v[1], "buy": round(v[2]),
                "buyLbl": _money(v[2]), "liveOrders": v[3],
                "pct": round(v[0] / v[3] * 100) if v[3] else 0}
               for k, v in agg.items() if v[0]]
        out.sort(key=lambda x: -x["lateOrders"])
        return out[:12]

    deadlines = {
        "lateDeals": len(late_rows), "lateOrders": late_orders_n,
        "lateBuy": _money(late_buy_sum), "lateBuyNum": round(late_buy_sum), "depth": depth,
        "noDl": nodl_orders_n, "liveOrders": live_orders_n,
        "prodAfterDl": prod_after,
        "field": "Date of deadline to customer",
        "moneyDays": round(money_days), "avgLate": avg_late,
        "rate": round(CAP_RATE * 100),
        "revDays": round(rev_days), "lateRev": _money(late_rev_total),
        "loss": _money(loss_total), "lossNum": round(loss_total),
        "burnDay": _money(burn_day), "burnDayNum": round(burn_day),
        "shipFirst": ship_first,
        "revAtRisk": _money(rev_at_risk), "marginAtRisk": _money(margin_at_risk),
        "lateNoSum": late_nosum_n, "costTop": cost_top,
        "byManager": _cut(lambda r, o: r["manager"]),
        "byOss": _cut(lambda r, o: o.get("oss") or r["oss"]),
        "bySchema": _cut(lambda r, o: o.get("schema")),
    }

    # аддитивно: все прежние KPI сохранены, новые добавлены следом
    kpis = [
        ("Последний №", str(maxseq), "макс. номер в реализации", "ok"),
        ("Контрактов", str(n_contracts), f"сделок с заказами · всего в воронке {len(rows)}", ""),
        ("Σ продажи", _money(sale_sum), "сумма сделок (выручка), €", "ok"),
        ("Σ закупки", _money(buy_sum),
         f"заказы {len(rows)-n_econ-n_nobuy} · из экономики {n_econ} · нет данных {n_nobuy}", "amber"),
        ("Σ маржа", _money(margin_sum), "продажа − закупка (где есть данные), €", "ok" if margin_sum >= 0 else "warn"),
        ("Ср. маржа", f"{round(margin_sum/sale_m*100) if sale_m else 0}%", "по сумме, валовая", ""),
        ("В работе", f"{len(live_rows)}", "открытых сделок в воронке", ""),
        ("⚠ Замедлено", str(len(slow_rows)),
         f"{round(len(slow_rows)/len(live_rows)*100) if live_rows else 0}% открытых", "warn" if slow_rows else "ok"),
        ("Медиана цикла заказа", f"{round(med_cycle) if med_cycle else '—'} дн", "создание → закрытие заказа", ""),
        ("🚩 Просрочен дедлайн", str(len(late_rows)),
         f"сделок · заказов {late_orders_n} · закупка {_money(late_buy_sum)}", "warn" if late_rows else "ok"),
        ("💸 Потери от просрочки", _money(loss_total),
         f"упущенная доходность при {round(CAP_RATE*100)}% годовых · сгорает {_money(burn_day)}/день",
         "warn" if loss_total else "ok"),
    ]

    # --- агрегация по ПОСТАВЩИКАМ (без изменений) ---
    sup_agg: dict[str, dict] = defaultdict(lambda: {"buy": 0.0, "norders": 0, "deals": set(),
                                                    "clients": set(), "last": "", "curs": set()})
    for o in orders:
        sid = str(o.get("companyId") or "")
        if not sid:
            continue
        a = sup_agg[sid]
        a["buy"] += eur(o.get("opportunity"), o.get("currencyId"))
        a["norders"] += 1
        did = str(o.get("parentId2") or "")
        if did:
            a["deals"].add(did)
            cl = clients.get(str(deals.get(did, {}).get("COMPANY_ID")))
            if cl:
                a["clients"].add(cl)
        ct = str(o.get("createdTime") or "")[:10]
        if ct > a["last"]:
            a["last"] = ct
        if o.get("currencyId"):
            a["curs"].add(o.get("currencyId"))
    sup_total = sum(a["buy"] for a in sup_agg.values()) or 1.0
    sup_rows = sorted(({
        "id": sid, "name": supl.get(sid) or f"company #{sid}",
        "buy": round(a["buy"]), "buyLbl": _money(a["buy"]),
        "share": round(a["buy"] / sup_total * 100, 1),
        "norders": a["norders"], "ncontracts": len(a["deals"]), "nclients": len(a["clients"]),
        "clients": ", ".join(sorted(a["clients"]))[:80],
        "avg": _money(a["buy"] / a["norders"] if a["norders"] else 0),
        "last": a["last"], "curs": ", ".join(sorted(a["curs"])),
    } for sid, a in sup_agg.items()), key=lambda r: -r["buy"])
    top10 = sum(r["buy"] for r in sup_rows[:10])
    sup_kpis = [
        ("Поставщиков", str(len(sup_rows)), "с заказами (СП-172)", ""),
        ("Σ оборот", _money(sup_total), "сумма закупок, €", "amber"),
        ("Топ-10 доля", f"{round(top10 / sup_total * 100)}%", "концентрация закупок", "warn" if top10 / sup_total > 0.6 else ""),
        ("Ср. на поставщика", _money(sup_total / len(sup_rows) if sup_rows else 0), "€/поставщик", ""),
        ("Заказов всего", str(sum(r["norders"] for r in sup_rows)), "закупок", ""),
        ("Разовых", str(sum(1 for r in sup_rows if r["norders"] == 1)), "поставщиков с 1 заказом", "amber"),
    ]
    return {
        "label": f"на {today.strftime('%d.%m.%Y')}",
        "rows": rows,
        "kpis": [{"lbl": l, "val": v, "meta": m, "clz": c} for l, v, m, c in kpis],
        "deadlines": deadlines,
        # курсы Битрикса заданы вручную; показываем дату, чтобы расхождение с другой
        # отчётностью было объяснимо (см. сверку с «Контролем дедлайнов»)
        "fx": {"asOf": rate_asof, "base": base_cur,
               "usdRub": round(rate.get("USD", 0) / rate["RUB"], 2) if rate.get("RUB") else None,
               "eurUsd": round(rate.get("EUR", 0) / rate["USD"], 4) if rate.get("USD") else None},
        "speed": {
            "medCycle": round(med_cycle, 1) if med_cycle else None,
            "medLag": round(med_lag, 1) if med_lag is not None else None,
            "orderBench": bench,          # медианы стадий заказов («где копится время»)
            "dealBench": dbench,          # медианы стадий сделки в кат.0
            "slowMin": SLOW_MIN_DAYS, "slowMult": SLOW_MULT,
        },
        "orphan": {"n": len(orphan), "buy": _money(orphan_buy)},
        "suppliers": {
            "label": f"на {today.strftime('%d.%m.%Y')}",
            "rows": sup_rows,
            "kpis": [{"lbl": l, "val": v, "meta": m, "clz": c} for l, v, m, c in sup_kpis],
        },
    }
