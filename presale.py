"""Воронка пресейла — сделки воронки «Пресейл», которые ведёт сорсинг.

КАК УСТРОЕНА (зонд v48, 09.10.2026, scripts/probe_presale.py). Воронка сделок №32
«Пресейл»: первая сделка 14.01.2026 (проба), в работе с сентября — 54 сделки в
сентябре, 37 за 9 дней октября. Тринадцать рабочих стадий — «Новый запрос» →
«Назначение сорсера» → «Проработка ТЗ и вопросы заказчику» → «Поиск поставщиков и
сбор КП» → «Анализ КП и фиксация поставщиков» → «Сравнение предложений и расчёт
ЭП» → «Экономика готова | Можно подавать» → тендерные стадии → «Сорсинг завершён /
Ожидаем решение заказчика» → «Согласование финальной спецификации»; успех —
«Сделка выиграна»; двенадцать стадий отказа с причиной («Экономически не
интересно», «Вовремя не подались», «Отмена заказчиком закупки», …).
  • Сделку заводит человек (68 %) или служебная запись (31 %); ведёт — сорсер
    (53 %) или коммерсант (46 %). «Сорсер» заполнен у 66 %, «КАМ» — у 82 %,
    сумма — лишь у 4 % (сумма появляется после сравнения предложений).
  • Под 95 сделками 878 запросов поставщикам (СП-166, воронка «Запросы»): 57 %
    сделок с запросом, медиана 9 запросов на сделку, первый запрос — через 2 дня;
    59 % запросов заводит робот от имени служебной записи.
  • Из 99 сделок, побывавших в воронке, 4 ушли в другие воронки — за полдня:
    это ошибочно заведённые, а не пройденный путь. В реализацию не ушла ни одна.

ЧТО СЧИТАЕТСЯ. Модуль — чистое вычисление из уже выгруженного: сделки воронки,
справочник стадий (client.deal_stage_meta — тот же вызов, что client.stages),
запросы поставщикам отчётного окна (rfqs из main) и справочник людей с ушедшими.
Воронка ищется по имени, а не по номеру: номер в коде устарел бы с первой
перестройкой воронок.

ЧТО ДЕЛАТЬ (то, ради чего блок): сделки без назначенного сорсера, сделки без
единого запроса поставщикам, застрявшие в стадии и те, что ведёт ушедший сорсер.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

import config

PRESALE_RE = re.compile(r"пре\s*-?\s*сейл|presale|pre\s*-?\s*sale|предпродаж", re.I)
SOURCER_F = config.DEAL_SOURCER_FIELDS[0][0]     # «Сорсер» сделки
KAM_F = "UF_CRM_1740390857"                      # «КАМ»
DEAL_SELECT = ["ID", "TITLE", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", "MOVED_TIME",
               "ASSIGNED_BY_ID", "CREATED_BY_ID", "COMPANY_ID", "OPPORTUNITY", SOURCER_F, KAM_F]
# Пороги — по первому месяцу воронки (медиана «Поиска поставщиков» 11 дней, прочих
# рабочих стадий 3–7): две недели без смены стадии — уже не поиск, а простой.
ЗАСТОЙ_ДНЕЙ = 14
# первый запрос поставщику уходит в среднем через 2 дня после сделки
БЕЗ_ЗАПРОСА_ДНЕЙ = 3


def find_category(category_names: dict[str, str]) -> tuple[str, str] | None:
    """(номер, имя) воронки пресейла по имени; None — воронки нет."""
    for cid, name in sorted(category_names.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if PRESALE_RE.search(name or ""):
            return str(cid), name
    return None


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _uid(v) -> str:
    if isinstance(v, (list, tuple)):
        v = next((x for x in v if str(x or "") not in ("", "0")), "")
    u = str(v or "")
    return "" if u in ("", "0", "None") else u


def _median(vals: list) -> float | None:
    s = sorted(v for v in vals if v is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 1)


def _pct(a: int, b: int) -> int:
    return round(a / b * 100) if b else 0


def compute(*, cid: str, cat_name: str, deals: list[dict], stage_meta: dict[str, dict],
            rfqs: list[dict], people: dict[str, dict] | None, names: dict[str, str],
            today: dt.date, service_ids: set[str] | None = None) -> dict:
    people = people or {}
    service = set(service_ids or ())
    stages = sorted(((sid, m) for sid, m in stage_meta.items() if str(m.get("cat")) == str(cid)),
                    key=lambda kv: kv[1].get("sort", 0))
    sem_of = {sid: m.get("sem", "P") for sid, m in stages}

    by_deal: dict[str, list[dict]] = defaultdict(list)
    for r in rfqs:
        p = str(r.get("parentId2") or "")
        if p:
            by_deal[p].append(r)

    def person(uid: str) -> tuple[str, bool]:
        if not uid:
            return "", False
        p = people.get(uid) or {}
        gone = p.get("active") is False
        return (p.get("name") or names.get(uid) or f"user#{uid}"), gone

    rows = []
    for d in deals:
        did = str(d.get("ID"))
        sid = str(d.get("STAGE_ID") or "")
        sem = sem_of.get(sid) or (str(d.get("STAGE_SEMANTIC_ID") or "P").upper())
        created = _date(d.get("DATE_CREATE"))
        moved = _date(d.get("MOVED_TIME")) or created
        mine = by_deal.get(did, [])
        firsts = [t for r in mine if (t := _date(r.get("createdTime")))]
        src_uid = _uid(d.get(SOURCER_F))
        src, src_gone = person(src_uid)
        kam, _ = person(_uid(d.get(KAM_F)))
        open_ = sem == "P"
        age = (today - created).days if created else None
        flags = []
        if open_ and not src_uid:
            flags.append("nosrc")
        if open_ and src_gone:
            flags.append("gone")
        if open_ and not mine and age is not None and age >= БЕЗ_ЗАПРОСА_ДНЕЙ:
            flags.append("norfq")
        days = (today - moved).days if moved else None
        if open_ and days is not None and days > ЗАСТОЙ_ДНЕЙ:
            flags.append("stale")
        rows.append({
            "id": did, "t": str(d.get("TITLE") or f"Сделка #{did}")[:90],
            "stage": sid, "stageName": (stage_meta.get(sid) or {}).get("name") or sid,
            "sem": sem, "open": open_, "days": days, "age": age,
            "created": created.isoformat() if created else "",
            "src": src, "srcId": src_uid, "srcGone": src_gone, "kam": kam,
            "rfq": len(mine), "quotes": sum(1 for r in mine if r.get("_hasQuote")),
            "lag": (min(firsts) - created).days if firsts and created else None,
            "sum": float(d.get("OPPORTUNITY") or 0) > 0,
            "company": str(d.get("COMPANY_ID") or "0") not in ("", "0"),
            "flags": flags,
        })

    open_rows = [r for r in rows if r["open"]]
    # стадии: рабочие по порядку воронки, затем исходы
    st_rows = []
    for sid, m in stages:
        here = [r for r in rows if r["stage"] == sid]
        st_rows.append({"id": sid, "name": m.get("name") or sid, "sem": m.get("sem", "P"), "n": len(here),
                        "med": _median([r["days"] for r in here]),
                        "max": max((r["days"] for r in here if r["days"] is not None), default=None),
                        "stale": sum(1 for r in here if "stale" in r["flags"])})
    known = {sid for sid, _ in stages}
    чужие = sum(1 for r in rows if r["stage"] not in known)

    # заведено по неделям (Пн–Вс), последние 8 недель, с исходом
    monday = today - dt.timedelta(days=today.weekday())
    weeks = []
    for i in range(7, -1, -1):
        a = monday - dt.timedelta(days=7 * i)
        b = a + dt.timedelta(days=6)
        here = [r for r in rows if r["created"] and a.isoformat() <= r["created"] <= b.isoformat()]
        weeks.append({"from": a.isoformat(), "n": len(here),
                      "lost": sum(1 for r in here if r["sem"] == "F"),
                      "won": sum(1 for r in here if r["sem"] == "S")})

    # по сорсеру сделки
    agg: dict[str, dict] = {}
    for r in rows:
        k = r["srcId"] or ""
        a = agg.setdefault(k, {"id": k, "n": r["src"] or "сорсер не назначен", "gone": r["srcGone"],
                               "open": 0, "won": 0, "lost": 0, "rfq": 0, "quotes": 0,
                               "norfq": 0, "stale": 0})
        a["open" if r["open"] else ("won" if r["sem"] == "S" else "lost")] += 1
        a["rfq"] += r["rfq"]
        a["quotes"] += r["quotes"]
        a["norfq"] += "norfq" in r["flags"]
        a["stale"] += "stale" in r["flags"]
    by_src = sorted(agg.values(), key=lambda a: (a["id"] == "", -a["open"], -a["rfq"]))

    with_rfq = [r for r in rows if r["rfq"]]
    act = [r for r in open_rows if r["flags"]]
    order = {"gone": 0, "nosrc": 1, "norfq": 2, "stale": 3}
    act.sort(key=lambda r: (min(order[f] for f in r["flags"]), -(r["days"] or 0)))
    lost = [{"name": s["name"], "n": s["n"]} for s in st_rows if s["sem"] == "F" and s["n"]]
    lost.sort(key=lambda x: -x["n"])
    since28 = (today - dt.timedelta(days=27)).isoformat()
    return {
        "cid": str(cid), "name": cat_name,
        "head": {
            "total": len(rows), "open": len(open_rows),
            "won": sum(1 for r in rows if r["sem"] == "S"),
            "lost": sum(1 for r in rows if r["sem"] == "F"),
            "new28": sum(1 for r in rows if r["created"] >= since28),
            "withRfq": len(with_rfq), "withRfqPct": _pct(len(with_rfq), len(rows)),
            "rfq": sum(r["rfq"] for r in rows),
            "rfqMed": _median([r["rfq"] for r in with_rfq]),
            # запросы, заведённые служебной записью (роботом пресейла)
            "robotRfqPct": _pct(sum(1 for r in rows for x in by_deal.get(r["id"], [])
                                    if str(x.get("createdBy") or "") in service),
                                sum(r["rfq"] for r in rows)),
            "lagMed": _median([r["lag"] for r in rows if r["lag"] is not None]),
            "withQuotes": sum(1 for r in rows if r["quotes"]),
            "noSrc": sum(1 for r in open_rows if "nosrc" in r["flags"]),
            "gone": sum(1 for r in open_rows if "gone" in r["flags"]),
            "noRfq": sum(1 for r in open_rows if "norfq" in r["flags"]),
            "stale": sum(1 for r in open_rows if "stale" in r["flags"]),
            "sumPct": _pct(sum(1 for r in rows if r["sum"]), len(rows)),
            "srcPct": _pct(sum(1 for r in rows if r["srcId"]), len(rows)),
            "otherStage": чужие,
            "staleDays": ЗАСТОЙ_ДНЕЙ, "noRfqDays": БЕЗ_ЗАПРОСА_ДНЕЙ,
        },
        "stages": st_rows,
        "lost": lost,
        "weeks": weeks,
        "bySourcer": by_src,
        "act": act[:60],
    }
