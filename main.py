"""Автономный дашборд анализа работы сорсеров (КВАНТ · Bitrix24).

Пайплайн: выгрузка СП-166 «Запросы поставщикам» за период → расчёт метрик →
текстовые инсайты (гибрид Claude/правила) → сборка HTML-дашборда.

Примеры:
  python main.py --period 2026-05 --open
  python main.py --period 2026-05 --dry-run        # только метрики, без LLM и HTML
  python main.py --period 2026-04:2026-05-15 --no-llm
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
import json
import os
import sys
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

import advisor as advisor_mod
import chat_activity
import company as company_mod
import config
import dashboard
import dealctx as dealctx_mod
import insights as insights_mod
import kam as kam_mod
import kaminc as kaminc_mod
import kamqueue as kamqueue_mod
import contracts as contracts_mod
import metrics as metrics_mod
import people as people_mod
import premia as premia_mod
import period as period_mod
import presale as presale_mod
import reps as reps_mod
from bitrix_client import BitrixClient, chat_reader_client, drop_lost, без_вебхука
from stages import classify_stage

RFQ_SELECT = ["id", "assignedById", "createdBy", "stageId", "createdTime", "movedTime", "parentId2",
              # следы живого человека на карточке робота: кто двигал стадию,
              # кто менял, кто последним отметился в таймлайне
              "movedBy", "updatedBy", "lastActivityBy",
              # когда и кто менял карточку последним (updatedBy — строкой выше):
              # «файл при отказе или молчании» — наследие до правил 19.10, если
              # карточку с тех пор не меняли или менял не её сорсер (kprules)
              "updatedTime",
              "categoryId", "title", "companyId", "ufCrm18Supplier", "ufCrm18SupplContact",
              # файлы КП со стороны поставщика: по ним считается «получено КП».
              # Маска "*" файловых полей не возвращает — только поимённо.
              *config.RFQ_QUOTE_FIELDS]

SUPPLIER_CRM_FIELDS = ("ufCrm18Supplier", "ufCrm18SupplContact")


def _deal_field_labels(client: BitrixClient, codes) -> dict[str, str]:
    """Подписи пользовательских полей сделки — чтобы в журнале стояло «Сорсер»,
    а не код поля. Подпись поля — настройка портала, а не персональные данные."""
    try:
        fields = client.call("crm.deal.fields") or {}
    except Exception:                                        # noqa: BLE001
        return {}
    out = {}
    for code in codes:
        meta = fields.get(code) or {}
        out[code] = str(meta.get("formLabel") or meta.get("listLabel") or meta.get("title") or "")
    return out


def _has_quote_file(item: dict) -> bool:
    """Засчитанное КП: файл в поле «Offer from supplier» карточки запроса.

    Правило владельца 19.10.2026: засчитывается только файл в этом поле; КП в
    почте, в сделке или в чате сделки для системы не существует. Поле приходит
    списком, словарём или пустым, а у пустого встречается и строка, и пустой
    список — поэтому проверяется истинность значения, а не его тип. Наш
    исходящий «Request file» сюда не входит: это мы отправили, а не нам.
    """
    v = item.get(config.RFQ_OFFER_FIELD)
    return any(v) if isinstance(v, list) else bool(v)


def _crm_ref_ids(val):
    """Значение crm-поля (напр. ['CO_372','C_45']) → (company_ids, contact_ids)."""
    comps, conts = set(), set()
    for x in (val if isinstance(val, list) else [val] if val else []):
        s = str(x).strip()
        if s.startswith("CO_"):
            comps.add(s[3:])
        elif s.startswith("CT_"):
            conts.add(s[3:])
        elif s.startswith("C_"):
            conts.add(s[2:])
        elif s.isdigit():
            comps.add(s)
    return comps, conts


def _attach_suppliers(client: BitrixClient, rfqs: list[dict]) -> None:
    """Проставляет r['_supplier'] — имя поставщика (компания/контакт) по ссылкам RFQ."""
    comp_ids, cont_ids = set(), set()
    for r in rfqs:
        if r.get("companyId"):
            comp_ids.add(str(r["companyId"]))
        for f in SUPPLIER_CRM_FIELDS:
            c, t = _crm_ref_ids(r.get(f))
            comp_ids |= c
            cont_ids |= t
    comp_names = client.companies_by_ids(comp_ids) if comp_ids else {}
    cont_names = client.contacts_by_ids(cont_ids) if cont_ids else {}

    def name_of(r):
        c, t = _crm_ref_ids(r.get("ufCrm18Supplier"))
        for cid in c:
            if comp_names.get(cid):
                return comp_names[cid]
        for tid in t:
            if cont_names.get(tid):
                return cont_names[tid]
        _, t2 = _crm_ref_ids(r.get("ufCrm18SupplContact"))
        for tid in t2:
            if cont_names.get(tid):
                return cont_names[tid]
        return comp_names.get(str(r.get("companyId") or ""), "—")

    for r in rfqs:
        r["_supplier"] = name_of(r)


def _people(client: BitrixClient) -> dict[str, dict]:
    """Справочник людей портала: действующие И ушедшие.

    Признак «ушёл» и даты (приём, последний вход) есть только здесь. Замер деплоя
    09.10.2026 после #500: выгрузка отдела по UF_DEPARTMENT отдаёт и отключённых, так
    что оба ушедших сорсера были в отделе и раньше — но считались действующими: стояли
    в рейтинге, темп делился на весь период, их 445 открытых карточек и 63 карточки,
    легшие на них после ухода, нигде не выделялись. (Первая редакция этого места
    утверждала, что ушедший «выпадал из отдела»; живая сверка показала: отдел 7 355 →
    7 355, вне отдела 1 613 → 1 613.) Отключённых читаем вызовом с ACTIVE=N — тем же,
    что вкладки ролей (people.roster), поэтому портал второй раз его не считает.

    {uid: {name, active, depts, hired, last}}: hired — дата приёма
    (UF_EMPLOYMENT_DATE, иначе дата регистрации), last — последний вход в портал
    (LAST_LOGIN). Даты увольнения REST не отдаёт; у отключённой записи последний
    вход — ближайшая к ней дата, и она показывается с оговоркой.
    """
    out: dict[str, dict] = {}
    for params, active in (({}, True), ({"ADMIN_MODE": True, "FILTER": {"ACTIVE": "N"}}, False)):
        try:
            rows = client.list_paged("user.get", params)
        except Exception as e:                                # noqa: BLE001
            if active:
                raise
            # без ушедших расчёт остаётся прежним, а не падает
            print(f"  ⚠ ушедшие сотрудники не прочитаны: {type(e).__name__}: {без_вебхука(e)}")
            rows = []
        for u in rows:
            uid = str(u.get("ID") or "")
            if not uid or uid in out:
                continue
            act = u.get("ACTIVE")
            dd = u.get("UF_DEPARTMENT") or []
            out[uid] = {
                "name": " ".join(x for x in [u.get("LAST_NAME"), u.get("NAME")] if x).strip() or f"user#{uid}",
                "active": (str(act).lower() in ("y", "true", "1")) if act is not None else active,
                "depts": [str(x) for x in (dd if isinstance(dd, list) else [dd]) if str(x)],
                "hired": _date_of(u.get("UF_EMPLOYMENT_DATE")) or _date_of(u.get("DATE_REGISTER")),
                "last": _date_of(u.get("LAST_LOGIN")),
            }
    return out


def _date_of(raw):
    """Дата из поля пользователя. Часть полей портал отдаёт пустым объектом {}."""
    return period_mod.parse_dt(raw) if isinstance(raw, str) and raw else None


def _since_label(d) -> str:
    return f"{period_mod._MON[d.month]}'{str(d.year)[2:]}" if d else ""


SPA_NEW_STAGE = "DT166_24:NEW"

_TAG_RE = __import__("re").compile(r"(?is)<(script|style)[^>]*>.*?</\1>|<[^>]+>")
_WS_RE = __import__("re").compile(r"\s+")
_ENT = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&mdash;": "—"}


def _email_preview(desc: str, limit: int = 220) -> str:
    """HTML/текст письма → компактное превью без тегов."""
    s = _TAG_RE.sub(" ", desc or "")
    for a, b in _ENT.items():
        s = s.replace(a, b)
    s = _WS_RE.sub(" ", s).strip()
    return s[:limit].rstrip() + ("…" if len(s) > limit else "")


def _email_to(settings) -> str:
    """Получатель письма из SETTINGS.EMAIL_META.to (если есть)."""
    try:
        meta = settings.get("EMAIL_META") if isinstance(settings, dict) else None
        if isinstance(meta, dict):
            to = meta.get("to") or meta.get("__to") or ""
            return str(to).strip()
    except Exception:
        pass
    return ""


def _has_attachment(a: dict) -> bool:
    for key in ("FILES", "STORAGE_ELEMENT_IDS"):
        v = a.get(key)
        if isinstance(v, (list, dict)) and len(v) > 0:
            return True
        if isinstance(v, str) and v not in ("", "0", "[]", "a:0:{}"):
            return True
    return False


def _mail_activities(client: BitrixClient, start_iso: str, end_iso: str) -> list[dict]:
    """Все CRM-письма карточек СП-166 за окно, одним проходом.

    Раньше исходящие тянулись отдельным запросом. Входящие нужны для счётчика
    полученных КП, и второй такой же проход удвоил бы и время выгрузки, и шанс
    упереться в лимит портала: DIRECTION убран из фильтра, разбор — на нашей
    стороне.

    Тело письма усекается сразу при выгрузке: за окно приходят десятки тысяч
    писем, и держать их сырыми в памяти незачем — в дровер идёт превью, а в
    счётчик КП только дата, карточка и признак вложения.
    """
    out: list[dict] = []
    last = 0
    while True:
        ch = client.call("crm.activity.list", {
            "filter": {"OWNER_TYPE_ID": config.SPA_ENTITY_TYPE_ID, "PROVIDER_ID": "CRM_EMAIL",
                       ">=CREATED": start_iso, "<=CREATED": end_iso, ">ID": last},
            "select": ["ID", "OWNER_ID", "DIRECTION", "SUBJECT", "DESCRIPTION", "SETTINGS",
                       "FILES", "STORAGE_ELEMENT_IDS", "CREATED"],
            "order": {"ID": "ASC"}, "start": -1}) or []
        if not ch:
            break
        for a in ch:
            out.append({
                "cid": str(a.get("OWNER_ID")),
                "dir": str(a.get("DIRECTION") or ""),
                "subj": (a.get("SUBJECT") or "").strip(),
                "to": _email_to(a.get("SETTINGS")),
                "body": _email_preview(a.get("DESCRIPTION")),
                "file": _has_attachment(a),
                "dt": (a.get("CREATED") or "")[:16].replace("T", " "),
                "dtx": a.get("CREATED") or "",
            })
        last = int(ch[-1]["ID"])
        if len(ch) < 50:
            break
    return out


def _inbound_mail(acts: list[dict]) -> list[dict]:
    """Входящие письма поставщиков в виде, который понимает metrics.build."""
    return [{"cid": a["cid"], "dt": a["dtx"], "file": a["file"]}
            for a in acts if a["dir"] == "1"]


def _send_stats(acts: list[dict], rfqs: list[dict], sourcer_rows: list[dict],
                dept_a_ids: set[str]) -> dict:
    """Реально отправлено vs создано: по факту исходящего CRM-письма на карточке RFQ.
    Дополнительно собирает по каждой карточке письма (тема · получатель · превью · вложение)
    для хронологического списка в дровере сорсера.
    Возвращает {"rows": [...], "totals": {...}, "byCard": {card_id: [emails...]}}."""
    counts: Counter = Counter()  # card_id -> кол-во исходящих писем
    by_card: dict[str, list[dict]] = defaultdict(list)
    for a in acts:
        if a["dir"] != "2":
            continue
        counts[a["cid"]] += 1
        by_card[a["cid"]].append({k: a[k] for k in ("subj", "to", "body", "file", "dt", "dtx")})
    # письма каждой карточки — по времени, новые сверху
    for cid in by_card:
        by_card[cid].sort(key=lambda e: e["dtx"], reverse=True)

    by_user: dict[str, list[dict]] = defaultdict(list)
    for r in rfqs:
        # исполнитель уже разрешён в metrics.build (цепочка «ответственный →
        # владелец сделки → автор»), карточки робота засчитаны живому сорсеру
        u = r.get("_owner") or str(r.get("assignedById"))
        if u in dept_a_ids:
            by_user[u].append(r)

    # «Двинута без письма в CRM» (прежде «фейк»): карточка вышла из «Новый», а
    # исходящего CRM-письма на ней нет. Не считаются карточки, где реакция
    # поставщика записана иначе: файл КП приложен, или стадия фиксирует ответ —
    # переписка без цены, Price at Work, Selected, не выбран, отказ. «Ответ не
    # получен» ответа как раз не фиксирует и остаётся в счёте: ни письма в CRM,
    # ни ответа. Письма из личной почты система не видит: входящих на карточках
    # ноль (замер 23.09.2026).
    КОНТАКТ_ДОКАЗАН = {"dialog", "paw", "selected", "notsel", "refused"}

    def _без_письма(r: dict) -> bool:
        return (r.get("stageId") != SPA_NEW_STAGE and not counts.get(str(r["id"]))
                and not r.get("_hasQuote")
                and classify_stage(str(r.get("stageId") or "")) not in КОНТАКТ_ДОКАЗАН)

    rows = []
    for s in sourcer_rows:  # уже блок A: действующие по темпу, затем ушедшие
        items = by_user.get(s["id"], [])
        total = len(items)
        sent = sum(1 for r in items if counts.get(str(r["id"])))
        fake = sum(1 for r in items if _без_письма(r))
        followup = sum(1 for r in items if counts.get(str(r["id"]), 0) >= 2)
        rows.append({
            "id": s["id"], "n": s["n"], "act": s.get("act", True),
            "total": total, "sent": sent, "nosend": total - sent,
            "sentPct": round(sent / total * 100) if total else 0,
            "fake": fake, "fuPct": round(followup / sent * 100) if sent else 0,
        })
    tot = {kk: sum(r[kk] for r in rows) for kk in ("total", "sent", "nosend", "fake")}
    tot["sentPct"] = round(tot["sent"] / tot["total"] * 100) if tot["total"] else 0
    return {"rows": rows, "totals": tot, "byCard": {c: v for c, v in by_card.items()}}


class DataGateError(RuntimeError):
    """Выгрузка не прошла проверку достаточности — собирать дашборд нельзя."""


def _sanity_gates(p, rfqs, period_deals, dept_a_ids, sourcers_a, *, skip: bool = False) -> None:
    """Проверка, что данные из Bitrix пришли, а не «пустой, но валидный» ответ.

    Разбор падений: при частичной выгрузке пайплайн собирал корректный HTML с нулями
    во всех KPI и деплоил его в прод. Здесь прогон останавливается ДО генерации, поэтому
    на сайте остаётся предыдущая рабочая версия.

    Пороги масштабируются длиной периода и переопределяются переменными окружения
    SOURCING_MIN_RFQ / SOURCING_MIN_DEALS / SOURCING_MIN_SOURCERS; отключение — --allow-empty.
    """
    if skip:
        print("  ⚠ проверки достаточности отключены (--allow-empty или --max-deals)")
        return
    scale = min(1.0, max(p.days, 1) / 30.0)          # для коротких периодов пороги ниже
    min_rfq = max(1, int(int(os.getenv("SOURCING_MIN_RFQ") or 20) * scale))
    min_deals = max(1, int(int(os.getenv("SOURCING_MIN_DEALS") or 20) * scale))
    min_sourcers = max(1, int(int(os.getenv("SOURCING_MIN_SOURCERS") or 3) * scale))

    problems = []
    if not dept_a_ids:
        problems.append(f"отдел {config.DEPT_SOURCING_ID} не вернул ни одного сотрудника")
    if len(rfqs) < min_rfq:
        problems.append(f"RFQ {len(rfqs)} < порога {min_rfq}")
    if len(period_deals) < min_deals:
        problems.append(f"сделок периода {len(period_deals)} < порога {min_deals}")
    if len(sourcers_a) < min_sourcers:
        problems.append(f"сорсеров с активностью {len(sourcers_a)} < порога {min_sourcers}")
    if problems:
        raise DataGateError(
            "данные Bitrix не прошли проверку достаточности:\n    - " + "\n    - ".join(problems)
            + "\n  Дашборд НЕ собран, прежняя версия на сайте не тронута."
            + "\n  Если период действительно пустой — запустите с --allow-empty."
        )
    print(f"  ✓ проверка данных пройдена: RFQ {len(rfqs)}, сделок {len(period_deals)}, "
          f"сорсеров {len(sourcers_a)}, отдел {len(dept_a_ids)} чел.")


def run(args) -> int:
    settings = config.Settings.load()
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    p = period_mod.parse_period(args.period, as_of=as_of)
    use_llm = not args.no_llm and not args.dry_run
    if use_llm:
        settings.require_anthropic()

    print(f"▶ Период: {p.label}  ({p.days} дн, {len(p.weeks)} нед)")
    client = BitrixClient(settings.bitrix_webhook_url)

    print("• Справочники: отдел 172, пользователи, воронки, стадии сделок…")
    dept_a_ids = client.dept_member_ids(config.DEPT_SOURCING_ID)
    roster = _people(client)
    names = {u: x["name"] for u, x in roster.items()}
    since = {u: _since_label(x["hired"]) for u, x in roster.items() if x["hired"]}
    # Страховка: если выгрузка отдела перестанет отдавать отключённых, ушедшие не
    # выпадут во «вне отдела» — их вернёт подразделение в карточке записи. На
    # 09.10.2026 счётчик 0: оба ушедших приходят выгрузкой отдела.
    _dept_tree = client.dept_tree_ids(config.DEPT_SOURCING_ID)
    _left_dept = {u for u, x in roster.items()
                  if not x["active"] and set(x["depts"]) & _dept_tree and u not in dept_a_ids}
    dept_a_ids |= _left_dept
    category_names = client.categories()
    deal_stage_names = client.stages()

    print(f"• Выгрузка RFQ СП-{config.SPA_ENTITY_TYPE_ID} за период…")
    rfqs = client.list_items(
        config.SPA_ENTITY_TYPE_ID,
        filter={
            "categoryId": config.SPA_CATEGORY_ID,
            ">=createdTime": p.start_iso,
            "<=createdTime": p.end_iso,
        },
        select=RFQ_SELECT,
        max_items=args.max_deals,
    )
    print(f"  RFQ: {len(rfqs)}  |  блок A по ответственному (отдел 172): "
          f"{sum(1 for r in rfqs if str(r.get('assignedById')) in dept_a_ids)}")

    # Служебные записи: заданные номерами плюс названные служебными по имени.
    # В публичный журнал имя идёт ТОЛЬКО если оно само называет запись
    # служебной. Запись, заданная номером, может носить личное имя сотрудника —
    # с #400 по #402 так в журнал деплоя попадало имя человека (правило 17).
    # Служебные — только среди действующих записей, как и до справочника ушедших:
    # отключённая запись с «служебным» именем карточек больше не заводит, а её
    # прошлые карточки не должны молча сменить исполнителя.
    service_ids = config.service_accounts({u: x["name"] for u, x in roster.items() if x["active"]})
    if service_ids:
        print("  служебные записи: " + ", ".join(
            f"#{u} {names.get(u, '')}" if config.SERVICE_NAME_RE.search(names.get(u, ''))
            else f"#{u} (задана номером)" for u in sorted(service_ids, key=int)))
    else:
        print("  служебные записи: разбор отключён (SERVICE_ACCOUNT_IDS=off)")

    parent_ids = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
    print(f"• Родительские сделки (parentId2): {len(parent_ids)} → выгрузка стадий…")
    # ASSIGNED_BY_ID и поля сорсера нужны цепочке «кому засчитать запрос». До
    # 23.09.2026 сделки выгружались без ответственного, и звено «владелец
    # сделки» в проде не срабатывало ни разу — это не было видно, потому что
    # до служебных записей цепочка до него не доходила.
    deal_index = client.deals_by_ids(parent_ids, select=[
        "ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "ASSIGNED_BY_ID",
        *(f for f, *_ in config.DEAL_SOURCER_FIELDS)])
    _sourcer_labels = _deal_field_labels(client, [f for f, *_ in config.DEAL_SOURCER_FIELDS])
    for _f, *_ in config.DEAL_SOURCER_FIELDS:
        _filled = sum(1 for d in deal_index.values() if d.get(_f) not in (None, "", 0, "0", []))
        print(f"  поле сорсера сделки {_f} «{_sourcer_labels.get(_f, '?')}»: "
              f"заполнено у {_filled} из {len(deal_index)} сделок")

    # Запасной признак ушедшего сорсера: если портал при увольнении снимает
    # подразделение, отключённая запись без подразделения, названная в сделке
    # «Сорсером», — бывший сорсер. Поле ведёт только в отдел (замер 23.09.2026).
    _by_field = set()
    for _f, *_rest in config.DEAL_SOURCER_FIELDS:
        if _rest[1:] and _rest[1] is False:
            continue
        for d in deal_index.values():
            v = d.get(_f)
            for u in (v if isinstance(v, list) else [v]):
                u = str(u or "")
                x = roster.get(u)
                if x and not x["active"] and not x["depts"] and u not in dept_a_ids:
                    _by_field.add(u)
    dept_a_ids |= _by_field
    # Окно работы каждого человека отдела: с приёма, по последний вход у ушедших.
    staff = {u: {"active": roster[u]["active"], "hired": roster[u]["hired"],
                 "left": None if roster[u]["active"] else roster[u]["last"]}
             for u in dept_a_ids if u in roster}
    _gone = sum(1 for x in staff.values() if not x["active"])
    print(f"  состав отдела: действующих {len(staff) - _gone}, ушедших {_gone} "
          f"(по подразделению {len(_left_dept)}, по полю «Сорсер» сделки {len(_by_field)}); "
          f"без даты приёма {sum(1 for x in staff.values() if not x['hired'])}, "
          f"ушедших без даты последнего входа {sum(1 for x in staff.values() if not x['active'] and not x['left'])}")

    # Правило 19.10.2026, п. 10: «Экономика готова» = «Да» — только когда ни один
    # запрос сделки не стоит на Price at Work и хотя бы один стоит на Selected.
    # Сделок с «Да» единицы (зонд v52: 5), их запросы читаются за всё время.
    econ = None
    try:
        _econ_deals = client.list_deals_fast(
            filter={config.ECON_READY_FIELD: config.ECON_READY_YES},
            select=["ID", "TITLE", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "ASSIGNED_BY_ID"])
        _econ_deals = [d for d in _econ_deals if str(d.get("CATEGORY_ID") or "0") not in people_mod.TECH_CATS]
        _econ_ids = sorted({str(d["ID"]) for d in _econ_deals}, key=int)
        _econ_rfqs = client.list_items(
            config.SPA_ENTITY_TYPE_ID,
            filter={"categoryId": config.SPA_CATEGORY_ID, "@parentId2": _econ_ids},
            select=["id", "stageId", "parentId2"]) if _econ_ids else []
        econ = (_econ_deals, _econ_rfqs)
        print(f"  «Экономика готова» = «Да»: сделок {len(_econ_deals)}, их запросов {len(_econ_rfqs)}")
    except Exception as e:                                    # noqa: BLE001
        print(f"  ⚠ проверка «Экономика готова» пропущена: {type(e).__name__}: {без_вебхука(e)}")

    print("• Сделки периода (все воронки) для покрытия…")
    period_deals = client.deals_in_period(p.start_iso, p.end_iso, select=[
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE",
        "ASSIGNED_BY_ID", "COMPANY_ID", "OPPORTUNITY", "CURRENCY_ID"])
    print(f"  сделок периода: {len(period_deals)}")

    print("• Поставщики по RFQ (компании/контакты)…")
    _attach_suppliers(client, rfqs)

    for r in rfqs:
        r["_hasQuote"] = _has_quote_file(r)
    print(f"  карточек с файлом КП поставщика: {sum(1 for r in rfqs if r['_hasQuote'])}")

    print("• Письма карточек СП-166 (исходящие и входящие)…")
    _acts = _mail_activities(client, p.start_iso, p.end_iso)
    _inb = _inbound_mail(_acts)
    print(f"  писем: {len(_acts)}, из них входящих: {len(_inb)}"
          + ("  (ответы поставщиков приходят файлами в карточку, не письмами)"
             if not _inb else ""))

    print("• Расчёт метрик…")
    # Подразделения ушедших: справочник портала знает только действующих, и
    # карточки уволенного шли с подписью «подразделение не указано» — а сам он
    # по этой подписи попадал в кандидаты в служебные записи.
    _dep_names = {str(d.get("ID")): str(d.get("NAME") or "") for d in client.departments()}
    user_depts = dict(client.user_dept_names())
    for u, x in roster.items():
        if not x["active"] and not user_depts.get(u):
            got = [_dep_names[d] for d in x["depts"] if _dep_names.get(d)]
            if got:
                user_depts[u] = got[0]
    m = metrics_mod.build(
        p, rfqs, deal_index, period_deals, dept_a_ids,
        names, since, deal_stage_names, category_names,
        user_depts,
        service_ids,
        _inb,
        config.DEAL_SOURCER_FIELDS,
        staff=staff,
        econ=econ,
    )
    _o = m["origin"]["summary"]
    # Раскладка авторства — в журнал каждым прогоном: по ней видно день ко дню,
    # кто грузит очередь запросов. Только агрегаты, без имён (CLAUDE.md, 17).
    print(f"  авторов карточек: {_o['people']} чел.; отдел поиска поставщиков "
          f"{_o['sourcing']} ({_o['sourcingPct']} %), вне отдела {_o['outside']} "
          f"({_o['outsidePct']} %), без автора {_o['auto']} ({_o['autoPct']} %)")
    if not _o["viaService"] and _o["serviceConfigured"]:
        print(f"  служебных записей задано {_o['serviceConfigured']}, "
              f"но карточек за период они не заводили")
    if _o["viaService"]:
        print(f"  служебные записи: {_o['viaService']} карточек ({_o['viaServicePct']} %) — "
              f"завели {_o['viaServiceMade']}, записаны ответственным по {_o['viaServiceAssigned']}; "
              f"исполнитель восстановлен у {_o['serviceResolved']} ({_o['serviceResolvedPct']} %)")
        # чем именно определён исполнитель — без этой строки ошибка PR #400
        # (карточки легли на руководителя, а не на сорсера) не была бы видна
        print("    чем определён: " + "; ".join(
            f"{x['how']} {x['n']}" for x in m["origin"].get("resolvedHow") or []))
    if _o["candidates"]:
        print(f"  кандидатов в служебные записи: {_o['candidates']} "
              f"(порог {_o['candidateFloor']} карточек) — см. вкладку «Кто заводит запросы»")

    _k = m["kpi"]
    print(f"  сорсеров с запросами: действующих {_k['staffActive']}, ушедших {_k['staffGone']}; "
          f"у ушедших открытых карточек {_k['goneOpen']}, легло после ухода {_k['goneAfter']}")
    _sanity_gates(p, rfqs, period_deals, dept_a_ids, m.get("sourcersA") or [],
                  skip=bool(args.allow_empty or args.max_deals))

    # Воронка пресейла: сделки, которые ведёт сорсинг до ТКП. Стадии — из общего
    # справочника (тот же вызов, что client.stages), запросы — уже выгруженные rfqs;
    # новое чтение портала — сами сделки воронки (их около сотни) и чаты застоявшихся.
    # Сделки читаются здесь, а блок считается после реализации: простой по стадии
    # подтверждает чат сделки (золотое правило 19.10.2026), а бюджет чтения чатов
    # один на сборку — первой его тратит реализация (там деньги), пресейл — остатком.
    # Чаты сделок читаются от имени владельца (решение 19.10.2026): вебхук сборки не
    # участник чатов (сборка 19.10: из 129 чатов с простоем 123 — отказ в доступе).
    # Свой вебхук — секрет BITRIX_CHAT_WEBHOOK_URL, только чтение; темп портала общий.
    # Нет секрета — читает вебхук сборки, как раньше.
    # Второй вебхук — руководителя реализации (решение 22.10.2026): владелец не участник
    # большинства чатов реализации, а добавить в чат может только участник.
    _chat_client, _chat_who = chat_reader_client(client)
    chat_reader = chat_activity.Reader(_chat_client, service_ids)
    print(f"• Чаты сделок читает {_chat_who}")
    _presale_in: dict | None = None
    try:
        _ps = presale_mod.find_category(category_names) or presale_mod.find_category(
            people_mod.deal_categories(client))
        if _ps:
            _pdeals = client.list_deals_fast(filter={"CATEGORY_ID": int(_ps[0])}, select=presale_mod.DEAL_SELECT)
            # подписи «Категории оборудования» — справочник поля (линии продукт-оунеров);
            # не прочитан — только оформление: «значение #id», блок пресейла остаётся
            try:
                _cat_labels = {str(k): str(v) for k, v in
                               ((client.userfields().get(presale_mod.CAT_F) or {}).get("enum") or {}).items()}
            except Exception as e:                       # noqa: BLE001
                print(f"  ⚠ подписи «Категории оборудования» не прочитаны: {type(e).__name__}")
                _cat_labels = {}
            _presale_in = dict(cid=_ps[0], cat_name=_ps[1], deals=_pdeals, stage_meta=client.deal_stage_meta(),
                               rfqs=rfqs, people=roster, names=names, today=p.end, service_ids=service_ids,
                               cat_labels=_cat_labels)
        else:
            print("• Воронка пресейла: воронки с таким именем в портале нет")
    except Exception as e:                                   # noqa: BLE001
        print(f"  ⚠ воронка пресейла пропущена: {type(e).__name__}: {без_вебхука(e)}")

    def _presale() -> None:
        """Блок пресейла с проверкой простоя чатом — общим читателем сборки."""
        if not _presale_in:
            return
        try:
            # чаты читаются и у сделок с мячом у КАМа сверх порога очереди КАМа (3–7 дн.):
            # золотое правило снимает и пункт «мяч у КАМа», а простой пресейла — от 14 дн.
            m["presale"] = presale_mod.compute(**_presale_in, chat_reader=chat_reader,
                                               chat_also=kamqueue_mod.chat_wanted)
            _h = m["presale"]["head"]
            print(f"• Воронка пресейла #{_presale_in['cid']}: сделок {_h['total']}, открыто {_h['open']}, "
                  f"отказ {_h['lost']}, выиграно {_h['won']}; с запросом {_h['withRfqPct']} %, запросов {_h['rfq']}; "
                  f"без сорсера {_h['noSrc']}, без запросов {_h['noRfq']}, ведёт ушедший {_h['gone']}; "
                  f"в стадии > {_h['staleDays']} дн {_h['staleStage']}: стоят (чат подтвердил) {_h['stale']}, "
                  f"в чате работа {_h['staleChat']}, чат не проверен {_h['staleUnverified']}")
            _cs = chat_reader.stats()
            print(f"  чаты сделок за сборку: прочитано {_cs['reads']} из бюджета {_cs['budget']}"
                  f"{'; чтение остановлено лимитом портала' if _cs['stopped'] else ''}")
            if hasattr(_chat_client, "по_вебхуку"):
                print("  чаты ответил вебхук: " + " · ".join(
                    f"{п} {н}" for п, н in zip(_chat_client.подписи, _chat_client.по_вебхуку)))
        except Exception as e:                               # noqa: BLE001
            print(f"  ⚠ воронка пресейла пропущена: {type(e).__name__}: {без_вебхука(e)}")

    print("• Отправлено vs создано (письма)…")
    m["send"] = _send_stats(_acts, rfqs, m["sourcersA"], dept_a_ids)

    # обогащаем детали каждого сорсера письмами (тема · получатель · превью · вложение)
    _by_card = m["send"].pop("byCard", {})
    _mail_cards = 0
    for s in m["sourcersA"]:
        for d in s.get("details", []):
            mails = _by_card.get(str(d.get("id"))) or []
            d["mailN"] = len(mails)
            if mails:
                top = mails[0]                       # последнее письмо карточки
                d["mailSubj"] = top["subj"]
                d["to"] = top["to"]
                d["body"] = top["body"]
                d["file"] = top["file"]
                d["mailDt"] = top["dt"]
                _mail_cards += 1
    print(f"  писем привязано к карточкам: {_mail_cards}")

    # список непокрытых сделок: только там, где запрос ОЖИДАЕТСЯ — живые сделки с суммой
    # (статус ≠ F, не сделка-RFQ сорсинга, не тестовая и не техническая воронка). €0 и
    # отказы — «запрос не требуется». Предикат один на оба места — metrics.req_expected
    # (правило 14: копии правились бы порознь).
    _exp = metrics_mod.req_expected
    try:
        _rfqpar = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
        _unc = [d for d in period_deals if str(d["ID"]) not in _rfqpar and _exp(d)]
        _cur = client.call("crm.currency.list", {}) or []
        _rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in _cur}
        _eur = lambda o, cu: float(o or 0) * _rate.get(cu, 1.0)
        _mfmt = lambda v: (f"€{v/1e6:.1f}M" if abs(v) >= 1e6 else f"€{round(v/1e3)}K" if abs(v) >= 1e3 else f"€{round(v)}")
        _users = client.users()
        _cnames = client.companies_by_ids({str(d.get("COMPANY_ID")) for d in _unc if d.get("COMPANY_ID") and str(d.get("COMPANY_ID")) != "0"})
        _unc_rows = []
        for d in _unc:
            amt = _eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
            sem = (d.get("STAGE_SEMANTIC_ID") or "").upper()
            _unc_rows.append({
                "id": str(d["ID"]), "t": (d.get("TITLE") or f'Сделка #{d["ID"]}')[:90],
                "client": _cnames.get(str(d.get("COMPANY_ID")), "—") or "—",
                "owner": _users.get(str(d.get("ASSIGNED_BY_ID")), "—"),
                "raw": round(amt), "amt": _mfmt(amt),
                "cat": category_names.get(str(d.get("CATEGORY_ID")), ""),
                "date": str(d.get("DATE_CREATE", ""))[:10],
                "sem": "проиграна" if sem == "F" else ("выиграна" if sem == "S" else "в работе"),
            })
        _unc_rows.sort(key=lambda r: -r["raw"])
        m["coverage"]["uncovered"] = _unc_rows
        # недельная динамика непокрытых: по неделе СОЗДАНИЯ сделки (Пн–Вс), свежие сверху
        _MONS = {1: "янв", 2: "фев", 3: "мар", 4: "апр", 5: "май", 6: "июн",
                 7: "июл", 8: "авг", 9: "сен", 10: "окт", 11: "ноя", 12: "дек"}
        def _mon_of(s):
            try:
                d = dt.date.fromisoformat(str(s)[:10]); return d - dt.timedelta(days=d.weekday())
            except Exception:
                return None
        _wk_created = Counter()
        for d in period_deals:
            if not _exp(d):            # знаменатель недели — только сделки, где запрос ожидается
                continue
            mo = _mon_of(d.get("DATE_CREATE"))
            if mo:
                _wk_created[mo] += 1
        _wk_uncov = Counter()
        for r in _unc_rows:
            mo = _mon_of(r["date"])
            if mo:
                _wk_uncov[mo] += 1
                r["wk"] = mo.isoformat()
        _weekly = []
        for mo in sorted(_wk_created, reverse=True):
            created = _wk_created[mo]; uncov = _wk_uncov.get(mo, 0); end = mo + dt.timedelta(days=6)
            label = (f"{mo.day}–{end.day} {_MONS[end.month]}" if mo.month == end.month
                     else f"{mo.day} {_MONS[mo.month]} – {end.day} {_MONS[end.month]}")
            _weekly.append({"key": mo.isoformat(), "label": label, "created": created,
                            "uncov": uncov, "uncovPct": round(uncov / created * 100) if created else 0})
        m["coverage"]["weekly"] = _weekly
        print(f"  непокрытых сделок (без запросов поставщикам): {len(_unc_rows)} | недель: {len(_weekly)}")
    except Exception as e:
        print(f"  ⚠ список непокрытых пропущен: {type(e).__name__}: {e}")

    out_dir = Path(args.out)
    slug = (args.period or f"{p.start}_{p.end}").replace(":", "_")
    (out_dir).mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / f"metrics_{slug}.json"

    def _write_metrics() -> None:
        metrics_path.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ метрики: {metrics_path}")

    if args.dry_run:
        _presale()                 # реализации в этом режиме нет: бюджет чатов — пресейлу
        _write_metrics()
        if not args.no_summary:
            _print_summary(m)
        _print_snapshot(client)
        return 0

    print(f"• Инсайты ({'Claude' if use_llm else 'правила'})…")
    ins = insights_mod.generate(m, settings, use_llm=use_llm)
    print(f"  источник: {ins.get('_source')}")

    company_data = kam_data = eng_data = prod_data = reps_data = people_data = None
    deals_ytd = orders_ytd = orders_all = deal_owner = deal_sale = None
    _rate = {}            # курсы Битрикса к базе €; пусто — если общий пул не собрался
    _pool_companies: dict[str, str] = {}   # названия компаний сделок пула — и для дохода (kaminc)
    # --- общий пул сделок/заказов YTD (нужен Пульсу + отраслевым вкладкам) ---
    try:
        print("• Общий пул сделок/заказов (YTD)…")
        _t = time.time()
        ys = period_mod.year_start(p.end)   # с 01.01 года отчёта (был зашит 2026)
        deals_ytd = client.list_deals_fast(filter={">=DATE_CREATE": ys},
            select=["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID",
                    "DATE_CREATE", "CLOSEDATE", "ASSIGNED_BY_ID", "COMPANY_ID", "MOVED_TIME", "LAST_ACTIVITY_TIME"]
                   + people_mod.DEAL_FIELDS + people_mod.INCOME_FIELDS)
        # заказы СП-172 тянем ОДИН раз с 2025 и со сроком клиенту: тот же список нужен
        # и вкладкам ролей (просрочка), и «Коммерсантам». Раньше каждая вкладка ходила
        # за ними отдельно — три выгрузки одного и того же на каждую сборку.
        orders_all = client.list_items(172, filter={">=createdTime": people_mod.ORDERS_SINCE},
            select=["id", "title", "stageId", "opportunity", "currencyId", "createdTime", "parentId2",
                    "assignedById", people_mod.DL_CUSTOMER, people_mod.DEL_FACT])
        # заказы СП-172: исключаем проигранные — это не контрактная выручка; провал — по
        # семантике стадии (свои стадии провала UC_* по суффиксу «:FAIL» считались живыми)
        orders_ytd = [o for o in drop_lost(client, 172, orders_all)
                      if str(o.get("createdTime", ""))[:4] >= ys[:4]]
        # справочник стадий не прочитан — провал по суффиксу «:FAIL», как до 19.10: видно в журнале
        _без_справочника = sum(1 for v in getattr(client, "_spa_sem", {}).values() if not v)
        if _без_справочника:
            print(f"  ⚠ справочник стадий СП-172 не прочитан для {_без_справочника} воронок — "
                  "провал заказа там по суффиксу «:FAIL»")
        print(f"  заказов СП-172 с {people_mod.ORDERS_SINCE[:4]}: {len(orders_all)}, "
              f"из них непроигранных в {ys[:4]}: {len(orders_ytd)}")
        # курсы → база € (для продаж сделок)
        _cur = client.call("crm.currency.list", {}) or []
        _rate = {x.get("CURRENCY"): (float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)) for x in _cur}
        _eur = lambda o, cu: float(o or 0) * _rate.get(cu, 1.0)
        # полные карты по сделкам: владелец + сумма продажи (€). 2026 + родители заказов старше 2026.
        # (иначе ~70% заказов атрибутируются по assignedById карточки заказа, а не по владельцу сделки;
        #  а продажа = сумма сделки, а НЕ opportunity заказа СП-172, который = закупка у поставщика)
        deal_owner = {str(d["ID"]): str(d.get("ASSIGNED_BY_ID")) for d in deals_ytd}
        deal_sale = {str(d["ID"]): _eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID")) for d in deals_ytd}
        parent_ids = {str(o.get("parentId2")) for o in orders_ytd if o.get("parentId2")}
        missing = [pid for pid in parent_ids if pid and pid not in deal_owner]
        if missing:
            extra = client.deals_by_ids(missing, select=["ID", "ASSIGNED_BY_ID", "OPPORTUNITY", "CURRENCY_ID"])
            for did, d in extra.items():
                deal_owner[str(did)] = str(d.get("ASSIGNED_BY_ID"))
                deal_sale[str(did)] = _eur(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
            print(f"  родительские сделки заказов: догружено {len(extra)} (из {len(missing)})")
        # КАМ — по КЛИЕНТУ (холдингу), не по отделу: компания сделки → направление
        _comp_ids = {str(d.get("COMPANY_ID")) for d in deals_ytd if d.get("COMPANY_ID") and str(d.get("COMPANY_ID")) != "0"}
        _cnames = client.companies_by_ids(_comp_ids)
        _pool_companies = _cnames
        deal_group = {str(d["ID"]): kam_mod.client_dir(_cnames.get(str(d.get("COMPANY_ID")), "")) for d in deals_ytd}
        print(f"  клиентских направлений КАМ: {len(set(deal_group.values()))}")
        print(f"  общий пул собран за {time.time() - _t:.0f} с")
    except Exception as e:
        deal_group = None
        print(f"  ⚠ общий пул не получен — Пульс/КАМ/Инж/Продукт пропущены: {type(e).__name__}: {e}")

    # момент перевода сделок в реализацию (вход в воронку кат.0) — выгружаем ОДИН раз, шарим
    realize_date = None
    try:
        print("• История стадий: момент перевода в реализацию (для Пульса/Когорт/Коммерсантов)…")
        realize_date = client.stage_first_entry(2, 0, "2025-01-01T00:00:00")
        print(f"  вошли в реализацию (с 2025): {len(realize_date)} сделок")
    except Exception as e:
        print(f"  ⚠ история стадий пропущена: {type(e).__name__}: {e}")

    # --- каждая вкладка изолирована: сбой одной не гасит остальные ---
    if deals_ytd is not None:
        try:
            company_data = company_mod.compute(client, as_of=p.end, created=deals_ytd, orders=orders_ytd,
                                               deal_sale=deal_sale, realize_date=realize_date)
            print("  ✓ Пульс компании")
        except Exception as e:
            print(f"  ⚠ Пульс компании пропущен: {type(e).__name__}: {e}")
        try:
            kam_data = kam_mod.compute_set(client, None, as_of=p.end, created=deals_ytd, orders=orders_ytd,
                                           with_people=True, deal_sale=deal_sale, deal_group=deal_group)
            print("  ✓ КАМы (по клиенту)")
        except Exception as e:
            print(f"  ⚠ КАМы пропущены: {type(e).__name__}: {e}")
        for label, groups, key in (("Инжиниринг", kam_mod.ENG_GROUPS, "eng"),
                                    ("Продукт", kam_mod.PRODUCT_GROUPS, "prod")):
            try:
                data = kam_mod.compute_set(client, groups, as_of=p.end, created=deals_ytd, orders=orders_ytd,
                                           with_people=True, deal_owner=deal_owner, deal_sale=deal_sale)
                if key == "kam": kam_data = data
                elif key == "eng": eng_data = data
                else: prod_data = data
                print(f"  ✓ {label}")
            except Exception as e:
                print(f"  ⚠ {label} пропущена: {type(e).__name__}: {e}")

    try:
        print("• Коммерсанты: состав из Bitrix, загрузка КАМов и продукт-оунеров…")
        # открытые сделки ЭТОГО года уже есть в общем пуле — доливаем только те,
        # что заведены раньше и до сих пор открыты (иначе выгружаем 2 700 карточек дважды)
        if deals_ytd is None:                      # общий пул не собрался — тянем всё сами
            deals_open = None
        else:
            open_ytd = [d for d in deals_ytd if (d.get("STAGE_SEMANTIC_ID") or "").upper() == "P"]
            older = client.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "P", "<DATE_CREATE": ys},
                                           select=people_mod.DEAL_SELECT)
            deals_open = open_ytd + older
            print(f"  открытых сделок: {len(deals_open)} (в этом году {len(open_ytd)}, раньше {len(older)})")
        _t = time.time()
        people_data = people_mod.compute(client, as_of=p.end, open_deals=deals_open, created=deals_ytd,
                                         orders=orders_all)
        print(f"  посчитано за {time.time() - _t:.0f} с")
        _st = people_data["staff"]; _rc = people_data["recon"]
        print(f"  ✓ действующих {_st['active']}, уволенных {_st['fired']}; открытых сделок {_rc['openTotal']}: "
              f"за КАМами {_rc['kam']}, за продукт-оунерами {_rc['prod']}, ни за кем {_rc['none']}, "
              f"на уволенных {_rc['orphan']}")
        # гигиена ведения — в журнал прогона: по нему видно динамику день ко дню, не открывая
        # сайт. Только агрегаты и собственные подписи кода (правило публичного репозитория).
        _bad = [h for h in people_data.get("hygiene", []) if not h["ok"]]
        _pc = lambda h: f"{h['val']}%" if h["unit"] == "%" else str(h["val"])
        print(f"  гигиена Bitrix: не в норме {len(_bad)} из {len(people_data.get('hygiene', []))}"
              + ("; " + " · ".join(f"{h['lbl']} — {_pc(h)}" for h in _bad) if _bad else ""))
    except Exception as e:
        print(f"  ⚠ вкладки «КАМы»/«Продукт-оунеры» пропущены: {type(e).__name__}: {e}")

    contracts_data = None
    dealctx_data = None
    # «Месяц» — показатели отдела и менеджеров ОРК поимённо (решение владельца 26.10.2026,
    # п. 3): в страницу — только своим массивом под правом руководителей ОРК, не в данные вкладки
    ork_month_data = None
    # закрытый свод оценок чатов и разбора договоров (решение владельца 23.10.2026):
    # файл из KV realctx:v1, который кладёт сборка (deploy.yml); нет — вкладка как раньше
    real_ctx = dealctx_mod.load()
    if real_ctx is not None:
        print(f"• Свод чатов и договоров: чатов {len(real_ctx['chats'])}, сделок с договорами "
              f"{len(real_ctx['contracts'])}, с финальной экономикой {len(real_ctx['econ'])}, "
              f"отброшено записей не по форме {real_ctx['bad']}")
    try:
        print("• Контракты в реализации (СП-172, все непроигранные)…")
        contracts_data = contracts_mod.compute(client, as_of=p.end, people=roster, service_ids=service_ids,
                                               chat_reader=chat_reader,
                                               ctx_chats=real_ctx["chats"] if real_ctx else None,
                                               ctx_econ=real_ctx["econ"] if real_ctx else None,
                                               ctx_contracts=real_ctx["contracts"] if real_ctx else None,
                                               # справочник отделов уже прочитан (кэш клиента):
                                               # отдел ОРК и его руководитель — без нового запроса
                                               departments=client.departments(),
                                               fx_loader=_cbr_loader(),
                                               stage_meta=(_presale_in or {}).get("stage_meta"))
        ork_month_data = contracts_data.pop("orkMonth", None)
        _ork_head = contracts_data.pop("_orkHead", "") or ""
        # календарь РФ кончается — сроки менеджера следующего года не посчитать: громко заранее
        if (p.end.year + 1) not in period_mod.workcal_years():
            print(f"  ⚠ производственного календаря на {p.end.year + 1} год нет в data/workcal_ru.json: "
                  f"с 1 января сроки регламента ОРК не посчитаются (без подмены на Пн–Пт) — внести календарь")
        print(f"  ✓ контрактов: {len(contracts_data['rows'])}; продажа из сверенного файла экономики у "
              f"{contracts_data['head'].get('nFile', 0)} открытых")
        # регламент ОРК — только счётчики (правило 17): сколько живых заказов легло на шаг,
        # стадий не из таблицы, лет без календаря, сроков без календаря; «сумма прячет потерю»
        _ok = contracts_data.get("ork") or {}
        _tr, _wk = _ok.get("trust") or {}, _ok.get("week") or {}
        _cmp, _lc = _ok.get("slowCmp") or {}, contracts_data.get("life") or {}
        print(f"  регламент ОРК: живых заказов {_wk.get('live', 0)}, на шаге {_tr.get('mapped', 0)}, "
              f"стадия не из таблицы {_tr.get('unknown', 0)}; лет без календаря {len(_tr.get('yearsNoCal') or [])}, "
              f"сроков без календаря {sum((_lc.get('noCalendar') or {}).values())}; сверх нормы (W2) "
              f"{((_wk.get('tiles') or {}).get('W2') or {}).get('value')}, гигиена "
              f"{(_wk.get('hygiene') or {}).get('n', 0)}; руководитель ОРК в справочнике "
              f"{'есть' if _tr.get('orkHead') else 'нет'}; ответственный из ОРК у {_tr.get('respOrk', 0)} "
              f"живых заказов, менеджер ОРК не назначен у "
              f"{((_wk.get('hygiene') or {}).get('by') or {}).get('noMgr', 0)}, старых доставок без УПД "
              f"{_tr.get('oldNoUtd', 0)}; «Замедлено» до чата: было {_cmp.get('old', 0)}, "
              f"стало {_cmp.get('new', 0)} (хуже у {_cmp.get('worse', 0)}, лучше у {_cmp.get('better', 0)}); "
              f"менеджеров ОРК в «Месяце» {len(((ork_month_data or {}).get('main') or {}).get('people') or [])}")
        # приёмка кончается назначением менеджера ОРК (вопрос владельца 29.10.2026) — только счёт
        _t1, _lcc = ((_wk.get("tiles") or {}).get("W1") or {}), (_lc.get("counts") or {})
        print(f"  приёмка: сверх нормы {_lcc.get('s1Over', 0)}, договор с поставщиком сверх нормы "
              f"{_lcc.get('s2Over', 0)}; за 20 р.д. закончена назначением {_t1.get('byAssign', 0)}; «Заказ "
              f"создан» с датой назначения не в счёте: раньше начала приёмки {_t1.get('assignStale', 0)}, "
              f"ответственный не из ОРК {_t1.get('assignNoMgr', 0)}")
        # очередь в своих единицах (решения владельца 30.10.2026) — только счёт, без имён (правило 17)
        _bc, _dcn, _ocn = (_lc.get("bucketCounts") or {}), (_lc.get("dealCounts") or {}), (_lc.get("ordCounts") or {})
        _bx, _bd, _bh = _bc.get("exec") or {}, _bc.get("deal") or {}, _bc.get("hyg") or {}
        print(f"  очередь: по заказам {_bx.get('orders', 0)} заказов в {_bx.get('deals', 0)} сделках; по сделкам "
              f"{_bd.get('deals', 0)} сделок; порядок в карточках {_bh.get('orders', 0)} заказов и "
              f"{_bh.get('deals', 0)} сделок; пунктов до свёртки {sum(_lcc.values())}, после "
              f"{len(_lc.get('queue') or [])}; приёмок сделок {_dcn.get('s1Over', 0)} (заказов на шаге 1 "
              f"{_lcc.get('s1Over', 0)}, из них назначить на новый заказ {_ocn.get('ordAssign', 0)}); "
              f"не оплатил: сделок {_dcn.get('unpaid', 0)} (заказов {_lcc.get('unpaid', 0)}); нет срока "
              f"оплаты: сделок {_dcn.get('noPayPlan', 0)} (заказов {_lcc.get('noPayPlan', 0)})")
        # «Сделки без хозяина» (решение владельца 30.10.2026, п. 3) — только счёт по ролям (правило 17)
        _no = _wk.get("noOwner") or {}
        _nr, _ns = _no.get("byRole") or {}, _no.get("byState") or {}
        print(f"  сделки без хозяина: {_no.get('n', 0)} (нет КАМа или ушёл {_nr.get('kam', 0)}, ОСС сделки "
              f"{_nr.get('ossDeal', 0)}, менеджера ОРК {_nr.get('orkMgr', 0)}, ушёл ответственный сделки "
              f"{_nr.get('dealResp', 0)}; где чего-то нет {_ns.get('none', 0)}, где менеджера ОРК нет, а ответственный "
              f"заказов вне ОРК {_ns.get('out', 0)}, где кто-то ушёл {_ns.get('gone', 0)})")
        # «Пульс ОРК» (решение владельца 29.10.2026) — только счёт по видам строк, без имён (правило 17)
        _pl = (_wk.get("pulse") or {}).get("rows") or []
        _pk = lambda k: sum(x["n"] for x in _pl if x.get("kind") == k)    # noqa: E731
        print(f"  пульс ОРК: менеджеров ОРК с живыми заказами {sum(1 for x in _pl if x.get('kind') == 'mgr')}, "
              f"заказов у них {_pk('mgr')}; ответственный вне ОРК — {_pk('out')}; не назначен — {_pk('none')}; "
              f"списков за числами недели {len((_wk.get('drill') or {}).get('step') or {})} шагов")
        _print_fx(contracts_data.get("fx") or {})
        # «что делать первым» — по общей очереди обоих видов (contracts.life_all); плитки
        # отдельного направления — в его вид (решение владельца 25.10.2026)
        _hc, dealctx_data = dealctx_mod.build(contracts_data["rows"], contracts_mod.life_all(contracts_data),
                                              real_ctx, period_mod.now(dealctx_mod.MSK), today=p.end,
                                              ork_head=_ork_head)
        if _hc is not None:
            _hc_sep = _hc.pop("sep", None)
            contracts_data["head"]["ctx"] = _hc
            if _hc_sep is not None and contracts_data.get("sep"):
                contracts_data["sep"]["head"]["ctx"] = _hc_sep
            _c, _k = _hc["chat"], _hc["contract"]
            # только счётчики (правило 17)
            print(f"  чаты и договоры открытых сделок ({_hc['open']}): оценка чата текущая у {_c['cur']} "
                  f"(есть у {_c['seen']}), ждут нашего ответа {_c['reply']} (дольше суток {_c['replyLate']}), "
                  f"высокий риск {_c['high']}, срок из чата прошёл {_c['dueLate']}; договоры разобраны у "
                  f"{_k['seen']}: с риском сверки {_k['risk']} (флагов {_k['flags']}), без пары {_k['nopair']}, "
                  f"не дочитаны {_k['unread']}; финальная экономика сверена у {_hc['econ']['seen']} "
                  f"(сумма сделки расходится у {_hc['econ']['gap']}, из них «Наше КП» до переторжки у "
                  f"{_hc['econ']['preRebid']}, похожа на сумму с НДС у {_hc['econ']['vat']}, "
                  f"суммы нет у {_hc['econ']['noSum']}, менялась после чтения у {_hc['econ']['changed']}, "
                  f"валюта другая у {_hc['econ']['otherCur']}); "
                  f"пунктов очереди {len(dealctx_data['queue'])}")
        print(f"  «подвисла» по чату решено: " + ", ".join(
            f"{k} {v}" for k, v in sorted((contracts_data["head"].get("chatBy") or {}).items())))
        _ch = (contracts_data.get("head") or {}).get("chat") or {}
        print(f"  чаты сделок с простоем по стадии: активных {_ch.get('active', 0)} (не подвисли), "
              f"тихих {_ch.get('quiet', 0)}, не прочитано {_ch.get('unread', 0)} (нет доступа {_ch.get('denied', 0)}), "
              f"сверх бюджета или стоп по лимиту {_ch.get('skipped', 0)}")
        _g, _sp = contracts_data.get("gone") or {}, contracts_data.get("speed") or {}
        # только счётчики: суммы сделок в публичный журнал не идут (правило 17)
        print(f"  ведёт уволенный: открытых сделок {_g.get('deals', 0)} (ответственный {_g.get('byManager', 0)}, "
              f"ОСС {_g.get('byOss', 0)}, ответственный заказа {_g.get('byResp', 0)}), "
              f"живых заказов {_g.get('orders', 0)}, с просрочкой {_g.get('late', 0)}, "
              f"уволенных {_g.get('people', 0)}; нормы стадий по {_sp.get('benchDays')} дн: "
              f"{_sp.get('benchRecent', 0)} стадий, медиана цикла заказа {_sp.get('medCycle')} дн")
        _sep = (contracts_data.get("head") or {}).get("sep")
        if _sep:
            # только счётчики и номер воронки: имя направления — имя клиента (правило 17)
            print(f"  отдельное направление (воронка {_sep['cat']}): компаний {_sep['companies']}, сделок "
                  f"{_sep['deals']}, открытых {_sep['live']}, живых заказов {_sep['orders']}, срок клиента "
                  f"сорван {_sep['late']}, пунктов очереди {_sep['queue']} — вне основного вида")
    except Exception as e:
        # только класс ошибки: в тексте бывают номера сделок, а журнал публичный
        print(f"  ⚠ вкладка «Контракты» пропущена: {type(e).__name__}")
    _rz_hist(contracts_data, dealctx_data)

    # пресейл — после реализации: чаты сделок читает тот же читатель, остатком бюджета
    _presale()
    _write_metrics()
    _write_digest(contracts_data, dealctx_data, real_ctx, m.get("presale"))

    # Очередь КАМа «что делать сегодня» (kamqueue.py): пресейл с мячом у КАМа, сроки и
    # закрытие реализации, сделки без КАМа. Едет в данных вкладки «КАМ и продукт-оунеры»
    # (people → раздел «КАМы») и вместе с ними — в живую сверку (modules.json, «people»).
    if people_data is not None:
        # линии продукт-оунеров (пресейл по «Категории оборудования») — в данные вкладки
        _pl = (m.get("presale") or {}).get("poLines")
        if _pl:
            people_data["poLines"] = _pl
            print(f"  линии продукт-оунеров: {len(_pl['lines'])} категорий, сделок с категорией "
                  f"{_pl['withCat']} из {_pl['deals']} ({_pl['withCatPct']} %)")
        try:
            # очередь КАМа — по сделкам обоих видов «Реализации»: отдельное направление
            # снято только с ежедневной очереди руководителя реализации (25.10.2026)
            people_data["kamQueue"] = kamqueue_mod.build(
                m.get("presale"), contracts_mod.life_all(contracts_data), (contracts_data or {}).get("rows"),
                p.end, people=roster)
            _kq = people_data["kamQueue"]["head"]
            # только счётчики: имена и суммы в публичный журнал не идут (правило 17)
            print(f"  очередь КАМа: пунктов {_kq['n']} у {_kq['kams']} КАМов — мяч у КАМа сверх порога "
                  f"{_kq['ballKam']}, срок клиента ≤ 14 дн. {_kq['soon']}, не оплачено {_kq['unpaid']}, "
                  f"без КАМа {_kq['noKam']}; снято живым чатом {_kq['chatActive']}")
        except Exception as e:                               # noqa: BLE001
            print(f"  ⚠ очередь КАМа пропущена: {type(e).__name__}")

    try:
        print("• Коммерсанты (персональные дашборды + контрольные точки)…")
        reps_data = reps_mod.compute(client, realize_date=realize_date, as_of=p.end, created=deals_ytd,
                                     orders_src=orders_all)
        print(f"  ✓ коммерсантов: {len(reps_data['reps'])}")
    except Exception as e:
        print(f"  ⚠ вкладка «Коммерсанты» пропущена: {type(e).__name__}: {e}")

    premia_data = None
    try:
        print("• Премии по сделкам (положение ред. 2)…")
        _eur_rub = (1 / _rate["RUB"]) if _rate.get("RUB") else None   # иначе — курс из «Реализации»
        premia_data = premia_mod.compute(people_data, contracts_data, eur_rub=_eur_rub)
        # В журнал — только счётчики (правило 17): журнал публичный, а поправка
        # и суммы — сведения о марже и премиях. Их видно только на вкладке.
        if premia_data:
            _pt = premia_data["totals"]; _ps = _pt["src"]
            print(f"  премии: сделок с прогнозом {_pt['deals']} (реализация {_pt['real']}, "
                  f"проработка {_pt['presale']}); ПЗ: заказы {_ps.get('orders', 0)}, файл {_ps.get('file', 0)}, книга {_ps.get('book', 0)}, "
                  f"экономика {_ps.get('econ', 0)}, оценка {_ps.get('est', 0)}; ниже ×1,17 — {_pt['low']}, "
                  f"убыточных — {_pt['neg']}, выбросов — {_pt['big']}; "
                  f"калибровка по {premia_data['params']['calibN']} сделкам")
        else:
            print("  премии: не посчитаны — нет открытых сделок или курса рубля")
    except Exception as e:
        print(f"  премии: вкладка пропущена — {type(e).__name__}")   # только класс ошибки: журнал публичный

    # Доход КАМов и продуктов (kaminc.py, решения владельца 25.10.2026): после «Реализации»,
    # пресейла, очереди КАМа и премий (поправка результата — из их калибровки). Деньги —
    # отдельным массивом, его воркер оставляет только праву «Деньги КАМов».
    kaminc_data = None
    if contracts_data is not None:
        try:
            print("• Доход КАМов и продуктов…")
            _comp = dict(_pool_companies)
            _need = {str(d.get("comp")) for d in list((people_data or {}).get("deals") or [])
                     + list((people_data or {}).get("inSrc") or [])
                     if d.get("state") != "real" and str(d.get("comp") or "") not in ("", "0")} - set(_comp)
            if _need:
                _comp.update(client.companies_by_ids(_need))
            kaminc_data = kaminc_mod.compute(
                contracts_data, people_data, today=p.end, presale=m.get("presale"),
                kamq=(people_data or {}).get("kamQueue"), calib=(premia_data or {}).get("params"),
                cat_labels=(_presale_in or {}).get("cat_labels"), companies=_comp, roster=roster)
            if kaminc_data:
                _kt, _kh = kaminc_data["head"]["tot"], kaminc_data["head"]
                # только счётчики и доли (правило 17): ни сумм, ни имён, ни номеров сделок
                print(f"  доход: выигранных сделок {_kt['n']} (сверены файлом {_kt['nA']}, с результатом "
                      f"{_kt['nR']}); получено в квартале {_kt['k1']} (платёжкой {_kt['k1po']}), оплачено "
                      f"раньше {_kt['k1old']}; к получению {_kt['due']} (без даты {_kt['dueB']['none']}, срок "
                      f"прошёл {_kt['dueB']['overdue']}); задержано клиентом {_kt['k3c']}, срок под риском "
                      f"{_kt['k3r']}; выигрышей квартала {_kt['k4']}; пайплайн {_kt['pipe']}, тихих потерь "
                      f"{_kt['quiet']}; оплачено по платёжке без стадии {_kt['k1pf']}, закрыто в квартале {_kt['k1cl']}; "
                      f"компаний дочитано {len(_need)}; сверка денег "
                      + ("сошлась" if _kh["money"]["ok"] else "НЕ сошлась — деньги не публикуются: "
                         + ", ".join(_kh["money"]["fail"])))
        except Exception as e:                               # noqa: BLE001
            print(f"  ⚠ доход КАМов пропущен: {type(e).__name__}")   # только класс ошибки: журнал публичный
    # раздел pay и отсрочка оплаты payDays «Реализации» — только вход дохода: в страницу и
    # сверку не идут (условие оплаты клиента — под именным правом, решение Р-4)
    kaminc_mod.strip_inputs(contracts_data)

    advisor_data = None
    if deals_ytd is not None:
        try:
            print("• Советы знатока (прогноз · потери · риски концентрации)…")
            advisor_data = advisor_mod.compute(
                client, as_of=p.end, deals=deals_ytd, orders=orders_ytd,
                deal_sale=deal_sale, realize_date=realize_date,
                deal_stage_names=deal_stage_names, category_names=category_names)
            print(f"  ✓ советы знатока: справка из {len(advisor_data['brief'])} пунктов")
        except Exception as e:
            print(f"  ⚠ вкладка «Советы знатока» пропущена: {type(e).__name__}: {e}")

    if args.data_json:
        # Все вкладки одним файлом — для живой сверки (scripts/live_check.py):
        # сравнить с продом можно только то, что записано. Файл остаётся на
        # раннере, в журнал и в артефакты не попадает (в нём фамилии и суммы).
        Path(args.data_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.data_json).write_text(json.dumps({
            "company": company_data, "kam": kam_data, "eng": eng_data, "prod": prod_data,
            "contracts": contracts_data, "reps": reps_data, "advisor": advisor_data,
            "people": people_data, "premia": premia_data, "dealctx": dealctx_data,
            "kaminc": kaminc_data,
            # «Месяц» «Реализации» (под правом в странице) — живая сверка видит и его; ключ —
            # как имя поля из кода (live_check._ПОЛЕ_КОДА), иначе раздел свернётся в {*}
            "orkMonth": ork_month_data,
            "_portal": client.snapshot_stats(),
        }, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"  ✓ вкладки для сверки: {args.data_json}")
    _print_snapshot(client)

    html_path = out_dir / f"dashboard_{slug}.html"
    dashboard.write(m, ins, html_path, title=f"Сорсинг · {p.label}",
                    company=company_data, kam=kam_data, eng=eng_data, prod=prod_data,
                    contracts=contracts_data, reps=reps_data, advisor=advisor_data, people=people_data,
                    premia=premia_data, dealctx=dealctx_data, kaminc=kaminc_data,
                    # поимённо — своим массивом под правом руководителей (решение 26.10.2026, п. 3)
                    ork_month=ork_month_data)
    print(f"  ✓ дашборд: {html_path}")

    if args.open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


def _cbr_loader():
    """Курсы ЦБ РФ для «Реализации» (решение владельца 25.10.2026): ряды cbr.ru, резерв —
    зеркало; KVANT_FX=bitrix — без сети, по справочнику Битрикса (с плашкой во вкладке)."""
    if os.environ.get("KVANT_FX", "").strip().lower() == "bitrix":
        return None
    from base import fx as fx_mod
    return lambda codes, days, today: fx_mod.load_cbr(codes, days, today=today)


def _print_fx(fx: dict) -> None:
    """Курсы «Реализации» — в журнал только источник, коды, счётчики и даты (правило 17)."""
    fb = fx.get("fallback")
    print(f"  курсы: {fx.get('src')} · запросов курсов {fx.get('requests', 0)} · период {fx.get('lo') or '—'}–"
          f"{fx.get('hi') or '—'}" + (f" · нет у ЦБ: {', '.join(fx['noCode'])}" if fx.get("noCode") else "")
          + (f" · ряд не получен: {', '.join(fx['failed'])}" if fx.get("failed") else "")
          + (f" · ряд с зеркала: {', '.join(fx['patched'])}" if fx.get("patched") else "")
          + f" · без курса на свою дату сделок {fx.get('noRate', 0)}"
          + (" · время на курсы вышло" if fx.get("timeUp") else "")
          + (f" · зеркало: недочитано дат {fx.get('mirrorCut', 0)}, сбоев {fx.get('mirrorErr', 0)}"
             if fx.get("mirrorCut") or fx.get("mirrorErr") else "")
          + (f" · дат-опечаток {fx['badDays']}" if fx.get("badDays") else "")
          + (f" · книг бюджета без курса {fx['nPlanNoRate']}" if fx.get("nPlanNoRate") else ""))
    if fb:
        print(f"::warning::курсы Битрикса ({fb.get('why')}{', ' + fb['err'] if fb.get('err') else ''}): "
              f"сделок по ручным курсам {fb.get('n', 0)}")
    pt = fx.get("partial")
    if pt:
        print(f"::warning::ряд ЦБ не получен: {', '.join(pt.get('codes') or [])} — сделок по курсу Битрикса "
              f"{pt.get('n', 0)}, без суммы {pt.get('lost', 0)}")
    cl = fx.get("calls") or {}
    print("  дата КП: " + ", ".join(f"{k} {v}" for k, v in sorted((fx.get("kpSrc") or {}).items()))
          + f"; «ТКП согласован» в день создания без КП в истории {fx.get('nTkpAtCreate', 0)}; история КП у "
          f"{fx.get('nOffers', 0)} из {fx.get('nNeedOffer', 0)}" + (f" ({fx['kpErr']})" if fx.get("kpErr") else ""))
    print("  НДС: " + ", ".join(f"{k} {v}" for k, v in sorted((fx.get("vat") or {}).items()))
          + "; снят у покупателей: " + ", ".join(f"{k} {v}" for k, v in sorted((fx.get("vatBuyer") or {}).items()))
          + f"; реквизиты прочитаны у {fx.get('nCountry', 0)} из {fx.get('nBuyers', 0)} компаний"
          + (f" (сбой {fx['reqErr']})" if fx.get("reqErr") else "")
          + (f"; отдельные направления без реквизитов — как иностранные {fx['nVatNoreqSep']}" if fx.get("nVatNoreqSep") else ""))
    print(f"  портал на деньги: история КП и кат. 0 — запросов {cl.get('hist', 0)}, реквизиты — {cl.get('req', 0)}")


def _print_snapshot(client) -> None:
    """Счётчики снимка портала — только в прогоне сверки, где снимок включён."""
    st = client.snapshot_stats()
    if st["mode"] == "record":
        print(f"  снимок портала: записано ответов {st['saved']}")
    elif st["mode"] == "replay":
        print(f"  снимок портала: из снимка {st['hits']}, мимо снимка {st['misses']} "
              f"(живое чтение{'' if st['misses'] else ' не понадобилось'})")


def _print_summary(m: dict) -> None:
    k = m["kpi"]
    print("\n── СВОДКА ─────────────────────────────")
    print(f"Всего RFQ: {k['total']}  (A={k['deptA']} / B={k['outside']}),  ответственных: {k['respCount']}")
    print(f"В работе: {k['openCount']} ({k['inWorkPct']}%)  ·  КП засчитано (A): {k['kpCountA']} ({k['kpPctA']}% запросов) · с КП из закрытых: {k['kpPctOfClosedA']}%  ·  до ТКП (на странице скрыто — по текущей стадии сделки): {k['tkpPct']}%")
    print(f"Цепочка: ранние {m['chain']['early']} / ТКП {m['chain']['tkp']} / проигр. {m['chain']['lost']}")
    cov = m["coverage"]
    print(f"Покрытие сделок: {cov['withReq']}/{cov['deals']} ({cov['covPct']}%), ср. запросов на сделку {cov['avgSuppliers']}")
    print("\nБлок A по сорсерам:")
    for s in m["sourcersA"]:
        chk = s["closed"]
        agg = s["kp"] + s["refusedCol"] + s["noAnswer"]
        ok = "✓" if chk == agg else f"⚠ closed={chk}≠{agg}"
        print(f"  {s['n']:28}{'' if s.get('act', True) else ' (ушёл)'} темп={s.get('perDay')}/раб.дн c={s['c']:4} closed={s['closed']:3} КП={s['kp']:3} отказ={s['refusedCol']:3} молч={s['noAnswer']:3} ТКП={s['tkpP']:3}% ср.срок={s['avgDays']} {ok}")


def _rz_hist(contracts_data, dealctx_data) -> None:
    """«±N за неделю» «Реализации» (решение владельца 30.10.2026, п. 1; rzhist.py): история
    счётов из файла RZHIST_PATH (шаг deploy.yml читает закрытый ключ rzhist:v1) + сегодняшняя
    точка → раздел contracts.rzHist страницы; точка — файлом RZHIST_OUT (шаг после деплоя
    кладёт её в ключ). Нет переменных (живая сверка, локальный запуск) — история из одной
    сегодняшней точки, файл не пишется. В журнал — только счёты (правило 17); сбой — классом
    ошибки: вкладка соберётся без «за неделю»."""
    if not isinstance(contracts_data, dict) or contracts_data.get("rows") is None:
        return
    try:
        import rzhist
        day = period_mod.now(dealctx_mod.MSK).date()
        pt = rzhist.point(contracts_data, dealctx_data, day)
        if pt is None:
            return
        hist = rzhist.merge(rzhist.load(os.environ.get("RZHIST_PATH")), pt)
        contracts_data["rzHist"] = rzhist.page(hist, contracts_data, day)
        out = os.environ.get("RZHIST_OUT")
        if out:
            rzhist.write(out, pt)
        print(f"• История счётов «Реализации»: {rzhist.log_line(pt, hist)}")
    except Exception as e:
        print(f"  ⚠ история счётов «Реализации» не собрана: {type(e).__name__}")


def _write_digest(contracts_data, dealctx_data, real_ctx, presale) -> None:
    """Сводка по сделке для модели (решение владельца 25.10.2026, dealdigest.py): файл
    DEALDIGEST_OUT, который шаг деплоя после выкладки кладёт в закрытый ключ dealdigest:v1.
    Нет переменной (живая сверка, локальный запуск) — ничего не пишется. В журнал — только
    счётчики (правило 17); сбой — только класс ошибки: сборка дашборда важнее сводки."""
    out = os.environ.get("DEALDIGEST_OUT")
    if not out:
        return
    if (contracts_data or {}).get("rows") is None:
        # «Реализация» не собралась: сводка из одного пресейла заменила бы в ключе полную —
        # файла нет, и публикация ключ не трогает (dealdigest_kv.publish; ревью 28.10.2026)
        print("  ⚠ сводка по сделкам не записана: сборка без «Реализации»")
        return
    try:
        import dealdigest
        dg = dealdigest.build((contracts_data or {}).get("rows"), contracts_mod.life_all(contracts_data),
                              dealctx_data, presale, period_mod.now(dealctx_mod.MSK), ctx=real_ctx)
        код = dealdigest.check(dg)
        if код:
            print(f"  ⚠ сводка по сделкам не по форме ({код}) — не записана")
            return
        dealdigest.write(out, dg)
        n = dealdigest.counts(dg)
        print(f"• Сводка по сделкам для оценки чатов: сделок {n['deals']} (с заказами {n['orders']}, "
              f"с договором {n['contract']}, с экономикой {n['econ']}, пресейла {n['presale']})")
    except Exception as e:
        print(f"  ⚠ сводка по сделкам не собрана: {type(e).__name__}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Дашборд анализа работы сорсеров (КВАНТ · Bitrix24)")
    ap.add_argument("--period", help="YYYY-MM | YYYY-MM-DD:YYYY-MM-DD | YYYY-MM-DD (по умолч. — с 01.05.2026)")
    ap.add_argument("--out", default=str(config.BASE_DIR / "reports"), help="каталог отчётов")
    ap.add_argument("--no-llm", action="store_true", help="инсайты по правилам, без Claude")
    ap.add_argument("--dry-run", action="store_true", help="только метрики (JSON+сводка), без LLM и HTML")
    ap.add_argument("--no-summary", action="store_true",
                    help="не печатать сводку по сорсерам: в ней фамилии, а журнал Actions публичен")
    ap.add_argument("--open", action="store_true", help="открыть дашборд в браузере")
    ap.add_argument("--max-deals", type=int, default=None, help="ограничить число RFQ (для теста)")
    ap.add_argument("--as-of", help="переопределить «сегодня» (YYYY-MM-DD), для воспроизводимости")
    ap.add_argument("--data-json", help="записать данные всех вкладок в JSON (для живой сверки)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="не останавливаться, если данных из Bitrix мало (период действительно пустой)")
    args = ap.parse_args()
    try:
        return run(args)
    except SystemExit:
        raise
    except DataGateError as e:
        # код 2 = «данные не прошли проверку»: отличается от краха кода (1),
        # чтобы CI мог показать понятную причину и не деплоить.
        print(f"✗ {e}", file=sys.stderr)
        return 2
    except Exception as e:
        # текст любой ошибки — без пути вебхука: журнал Actions публичен
        print(f"✗ Ошибка: {type(e).__name__}: {без_вебхука(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
