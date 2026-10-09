"""Зонд v48: как устроена воронка пресейла.

ВОПРОС. Владелец (09.10.2026): «теперь появилась воронка пресейла, посмотри как она
устроена». В репозитории о пресейле известно только одно — робот, который от имени
служебных записей заводит карточки «Запросов поставщикам» (СП-166). Самой воронки
ни один зонд не мерил: где она живёт (воронка сделок или категория СП-166), из
каких стадий состоит, кто в ней заводит и ведёт сделки, как она связана с
запросами поставщикам и куда сделки из неё уходят.

ЧТО МЕРИТ.
  1. Воронки сделок и категории СП-166: номер, число записей всего и за 60 дней,
     дата первой записи. Названия печатаются только у воронки пресейла, у
     «Реализации» и у новых воронок (первая запись — не раньше 120 дней назад):
     в названиях прочих воронок стоят имена клиентов, а журнал Actions публичен.
  2. Пользовательские поля сделки и СП-166 с «пресейлом» в подписи.
  3. По каждой найденной воронке пресейла: стадии с числом сделок и медианой
     дней в стадии; месяц создания; кто завёл и кто ведёт (служебная запись,
     отдел поиска поставщиков, прочие); заполненность полей (сумма, компания,
     «Сорсер», «Head of sourcing», «КАМ», «Product leader»); запросы
     поставщикам под сделками (доля сделок с запросом, запросов на сделку, лаг
     от сделки до первого запроса, кто заводит запросы); куда сделки уходят из
     воронки (по истории стадий) и за сколько дней.

ТОЛЬКО АГРЕГАТЫ (CLAUDE.md, правило 17): ни имён, ни названий сделок и компаний,
ни номеров людей, кроме номеров служебных записей.

    python scripts/probe_presale.py        # нужен BITRIX_WEBHOOK_URL
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

PRESALE_RE = re.compile(r"пре\s*-?\s*сейл|presale|pre\s*-?\s*sale|предпродаж", re.I)
SAFE_RE = re.compile(r"^(общая|реализация)$", re.I)
НОВАЯ_ДНЕЙ = 120          # воронка, у которой первая запись моложе, считается новой
СВЕЖИЕ_ДНЕЙ = 60          # окно «за последние дни» в сводке воронок
ИСТОРИЯ_ДНЕЙ = 180        # глубина истории стадий для «куда уходят»
МАКС_СДЕЛОК = 3000        # потолок выгрузки сделок одной воронки
KAM_F = "UF_CRM_1740390857"
PROD_F = "UF_CRM_1779187425"
SOURCER_F, HEAD_F = config.DEAL_SOURCER_FIELDS[0][0], config.DEAL_SOURCER_FIELDS[1][0]


# ------------------------------------------------------------------ чистые помощники
def пресейл(name: str) -> bool:
    return bool(PRESALE_RE.search(name or ""))


def подпись(cid: str, name: str, first: dt.date | None, today: dt.date) -> str:
    """Название воронки — только если оно не может нести имя клиента."""
    новая = bool(first and (today - first).days <= НОВАЯ_ДНЕЙ)
    if пресейл(name) or SAFE_RE.match((name or "").strip()) or str(cid) == "0" or новая:
        return f"#{cid} «{name}»" + (" (новая)" if новая else "")
    return f"#{cid}"


def медиана(vals: list[float]) -> float | None:
    s = sorted(v for v in vals if v is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def доля(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else f"{part}/0"


def заполнено(v) -> bool:
    if isinstance(v, list):
        return any(заполнено(x) for x in v)
    return v not in (None, "", 0, "0", False)


def роль(uid, service: set[str], dept: set[str]) -> str:
    u = str((uid[0] if isinstance(uid, list) and uid else uid) or "")
    if u in ("", "0", "None"):
        return "пусто"
    if u in service:
        return "служебная запись"
    if u in dept:
        return "отдел поиска поставщиков"
    return "прочие"


def дата(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


# ------------------------------------------------------------------ зонд
def _categories(client, entity: int) -> list[dict]:
    out, start = [], 0
    while True:
        data = client.call_envelope("crm.category.list", {"entityTypeId": entity, "start": start})
        res = (data or {}).get("result") or {}
        chunk = res.get("categories") if isinstance(res, dict) else res
        out.extend(chunk or [])
        nxt = (data or {}).get("next")
        if not nxt or not chunk:
            return out
        start = nxt


def _total(client, method: str, params: dict) -> int:
    data = client.call_envelope(method, dict(params, start=0))
    return int((data or {}).get("total") or 0)


def _first_date(client, cid) -> dt.date | None:
    res = client.call("crm.deal.list", {"order": {"ID": "ASC"}, "filter": {"CATEGORY_ID": cid},
                                        "select": ["ID", "DATE_CREATE"], "start": -1}) or []
    return дата(res[0].get("DATE_CREATE")) if res else None


def обзор_воронок(client, today: dt.date) -> list[tuple[str, str]]:
    since = (today - dt.timedelta(days=СВЕЖИЕ_ДНЕЙ)).isoformat() + "T00:00:00"
    print("\n== Воронки сделок ==")
    found = []
    for c in sorted(_categories(client, 2), key=lambda x: int(x.get("id") or 0)):
        cid, name = str(c.get("id")), str(c.get("name") or "")
        всего = _total(client, "crm.deal.list", {"filter": {"CATEGORY_ID": cid}, "select": ["ID"]})
        свежих = _total(client, "crm.deal.list", {"filter": {"CATEGORY_ID": cid, ">=DATE_CREATE": since},
                                                  "select": ["ID"]})
        first = _first_date(client, cid)
        print(f"  {подпись(cid, name, first, today)}: сделок {всего}, за {СВЕЖИЕ_ДНЕЙ} дн {свежих}, "
              f"первая {first or '—'}" + ("  ← ПРЕСЕЙЛ" if пресейл(name) else ""))
        if пресейл(name):
            found.append(("deal", cid))
    print("\n== Категории СП-166 «Запросы поставщикам» ==")
    for c in sorted(_categories(client, config.SPA_ENTITY_TYPE_ID), key=lambda x: int(x.get("id") or 0)):
        cid, name = str(c.get("id")), str(c.get("name") or "")
        всего = _total(client, "crm.item.list", {"entityTypeId": config.SPA_ENTITY_TYPE_ID,
                                                 "filter": {"categoryId": cid}, "select": ["id"]})
        свежих = _total(client, "crm.item.list", {"entityTypeId": config.SPA_ENTITY_TYPE_ID,
                                                  "filter": {"categoryId": cid, ">=createdTime": since},
                                                  "select": ["id"]})
        # категории СП-166 — названия процессов, а не клиентов: печатаются все
        print(f"  #{cid} «{name}»: карточек {всего}, за {СВЕЖИЕ_ДНЕЙ} дн {свежих}"
              + ("  ← ПРЕСЕЙЛ" if пресейл(name) else ""))
        if пресейл(name):
            found.append(("rfq", cid))
    return found


def поля_пресейла(client) -> None:
    print("\n== Поля с «пресейлом» в подписи ==")
    n = 0
    for где, method, params in (("сделка", "crm.deal.fields", {}),
                                ("СП-166", "crm.item.fields", {"entityTypeId": config.SPA_ENTITY_TYPE_ID})):
        try:
            res = client.call(method, params) or {}
        except Exception as e:                                   # noqa: BLE001
            print(f"  {где}: не прочитано ({type(e).__name__})")
            continue
        res = res.get("fields", res) if isinstance(res, dict) else {}
        for code, meta in res.items():
            label = " / ".join(str(meta.get(k) or "") for k in ("formLabel", "listLabel", "title") if meta.get(k))
            if пресейл(label) or пресейл(code):
                n += 1
                print(f"  {где}: {code} «{label}» тип {meta.get('type')}")
    if not n:
        print("  нет")


def разбор_сделок(client, cid: str, service: set[str], dept: set[str], today: dt.date) -> None:
    print(f"\n== Воронка пресейла: сделки, категория #{cid} ==")
    ent = "DEAL_STAGE" if cid == "0" else f"DEAL_STAGE_{cid}"
    stages = {s["STATUS_ID"]: (s.get("NAME") or s["STATUS_ID"], (s.get("EXTRA") or {}).get("SEMANTICS") or "")
              for s in client.list_paged("crm.status.list", {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"}})}
    sel = ["ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", "MOVED_TIME", "CREATED_BY_ID",
           "ASSIGNED_BY_ID", "OPPORTUNITY", "COMPANY_ID", "SOURCE_ID", SOURCER_F, HEAD_F, KAM_F, PROD_F]
    deals = client.list_deals_fast(filter={"CATEGORY_ID": cid}, select=sel, max_items=МАКС_СДЕЛОК)
    n = len(deals)
    print(f"  сделок выгружено {n}" + (f" (потолок {МАКС_СДЕЛОК})" if n >= МАКС_СДЕЛОК else ""))
    by_stage = Counter(d.get("STAGE_ID") for d in deals)
    age: dict[str, list] = {}
    for d in deals:
        t = дата(d.get("MOVED_TIME")) or дата(d.get("DATE_CREATE"))
        if t:
            age.setdefault(d.get("STAGE_ID"), []).append((today - t).days)
    print("  стадии (по порядку): сделок · медиана дней в стадии")
    for sid, (nm, sem) in stages.items():
        md = медиана(age.get(sid, []))
        print(f"    {nm} [{sem or 'P'}]: {by_stage.get(sid, 0)} · {md if md is not None else '—'}")
    чужие = sum(v for k, v in by_stage.items() if k not in stages)
    if чужие:
        print(f"    стадия вне справочника: {чужие}")
    print("  итог: " + ", ".join(f"{k or 'P'} {v}" for k, v in
                                  Counter((d.get("STAGE_SEMANTIC_ID") or "P") for d in deals).most_common()))
    print("  создано по месяцам: " + ", ".join(
        f"{m} {v}" for m, v in sorted(Counter(str(d.get("DATE_CREATE") or "")[:7] for d in deals).items())))
    for поле, подп in (("CREATED_BY_ID", "кто завёл"), ("ASSIGNED_BY_ID", "кто ведёт")):
        c = Counter(роль(d.get(поле), service, dept) for d in deals)
        print(f"  {подп}: " + ", ".join(f"{k} {доля(v, n)}" for k, v in c.most_common()))
    svc = Counter(str(d.get("CREATED_BY_ID")) for d in deals if str(d.get("CREATED_BY_ID")) in service)
    if svc:
        print("    служебные записи-авторы: " + ", ".join(f"#{u} {v}" for u, v in svc.most_common()))
    for поле, подп in (("OPPORTUNITY", "сумма > 0"), ("COMPANY_ID", "компания"), (SOURCER_F, "«Сорсер»"),
                       (HEAD_F, "«Head of sourcing»"), (KAM_F, "«КАМ»"), (PROD_F, "«Product leader»")):
        ok = sum(1 for d in deals if (float(d.get(поле) or 0) > 0 if поле == "OPPORTUNITY" else заполнено(d.get(поле))))
        print(f"  заполнено {подп}: {доля(ok, n)}")
    src = Counter(str(d.get("SOURCE_ID") or "—") for d in deals)
    print("  источник (SOURCE_ID): " + ", ".join(f"{k} {v}" for k, v in src.most_common(8)))

    # запросы поставщикам под сделками пресейла
    ids = [str(d["ID"]) for d in deals]
    rfqs = []
    for i in range(0, len(ids), 50):
        rfqs += client.list_items(config.SPA_ENTITY_TYPE_ID, filter={"@parentId2": [int(x) for x in ids[i:i + 50]]},
                                  select=["id", "parentId2", "categoryId", "createdBy", "assignedById",
                                          "createdTime"])
    per = Counter(str(r.get("parentId2")) for r in rfqs)
    print(f"  запросов поставщикам под сделками: {len(rfqs)}; сделок с запросом {доля(len(per), n)}; "
          f"запросов на сделку (медиана) {медиана(list(per.values())) or 0}")
    if rfqs:
        print("    категории СП-166: " + ", ".join(f"#{k} {v}" for k, v in
                                                  Counter(str(r.get("categoryId")) for r in rfqs).most_common()))
        print("    кто завёл запрос: " + ", ".join(f"{k} {доля(v, len(rfqs))}" for k, v in
                                                  Counter(роль(r.get("createdBy"), service, dept) for r in rfqs).most_common()))
        print("    ответственный запроса: " + ", ".join(f"{k} {доля(v, len(rfqs))}" for k, v in
                                                       Counter(роль(r.get("assignedById"), service, dept) for r in rfqs).most_common()))
        created = {str(d["ID"]): дата(d.get("DATE_CREATE")) for d in deals}
        first: dict[str, dt.date] = {}
        for r in rfqs:
            t, p = дата(r.get("createdTime")), str(r.get("parentId2"))
            if t and (p not in first or t < first[p]):
                first[p] = t
        lags = [(first[p] - created[p]).days for p in first if created.get(p)]
        print(f"    лаг сделка → первый запрос, дней: медиана {медиана(lags)}, "
              f"в тот же день {sum(1 for x in lags if x <= 0)} из {len(lags)}")

    # куда сделки уходят из пресейла: история стадий по категории
    since = (today - dt.timedelta(days=ИСТОРИЯ_ДНЕЙ)).isoformat() + "T00:00:00"
    hist = client.stage_history(2, category_id=int(cid), since=since)
    ушли = [o for o in hist if o not in set(ids)]
    print(f"  по истории за {ИСТОРИЯ_ДНЕЙ} дн в воронке побывало сделок {len(hist)}, сейчас вне неё {len(ушли)}")
    if ушли:
        now = client.deals_by_ids(ушли, select=["ID", "CATEGORY_ID", "STAGE_SEMANTIC_ID"])
        print("    где сейчас: " + ", ".join(f"воронка #{k} {v}" for k, v in
                                          Counter(str(d.get("CATEGORY_ID")) for d in now.values()).most_common()))
        print(f"    не найдены (удалены) {len(ушли) - len(now)}")
        стаж = []
        for o in ушли:
            e = hist.get(o) or []
            if e:
                a, b = дата(e[0][1]), дата(e[-1][1])
                if a and b:
                    стаж.append((b - a).days)
        print(f"    дней в пресейле до ухода (первый → последний вход в его стадии): медиана {медиана(стаж)}")


def разбор_карточек(client, cid: str, service: set[str], dept: set[str]) -> None:
    print(f"\n== Воронка пресейла: категория СП-166 #{cid} ==")
    stages = client.spa_stages(config.SPA_ENTITY_TYPE_ID, int(cid))
    items = client.list_items(config.SPA_ENTITY_TYPE_ID, filter={"categoryId": cid}, max_items=МАКС_СДЕЛОК,
                              select=["id", "stageId", "createdBy", "assignedById", "createdTime", "parentId2"])
    n = len(items)
    by = Counter(r.get("stageId") for r in items)
    print(f"  карточек {n}; стадии: " + ", ".join(f"{nm} {by.get(sid, 0)}" for sid, nm in stages.items()))
    for поле, подп in (("createdBy", "кто завёл"), ("assignedById", "кто ведёт")):
        c = Counter(роль(r.get(поле), service, dept) for r in items)
        print(f"  {подп}: " + ", ".join(f"{k} {доля(v, n)}" for k, v in c.most_common()))
    print(f"  с родительской сделкой: {доля(sum(1 for r in items if r.get('parentId2')), n)}")


def main() -> int:
    from bitrix_client import BitrixClient
    settings = config.Settings.load()
    client = BitrixClient(settings.bitrix_webhook_url)
    today = dt.date.today()
    service = config.service_accounts(client.users())
    dept = client.dept_member_ids(config.DEPT_SOURCING_ID)
    print(f"зонд v48 · {today} · служебных записей {len(service)}: "
          + ", ".join(f"#{u}" for u in sorted(service, key=int)) + f" · отдел поиска поставщиков {len(dept)} чел.")
    found = обзор_воронок(client, today)
    поля_пресейла(client)
    if not found:
        print("\nВоронка с «пресейлом» в названии не найдена: см. новые воронки в обзоре выше.")
    for kind, cid in found:
        try:
            (разбор_сделок(client, cid, service, dept, today) if kind == "deal"
             else разбор_карточек(client, cid, service, dept))
        except Exception as e:                                   # noqa: BLE001
            print(f"  разбор #{cid} оборвался: {type(e).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
