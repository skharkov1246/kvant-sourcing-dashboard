"""Правила отметки КП (решение владельца 19.10.2026, «Как отмечать полученные КП»).

Чистые функции: нарушения по карточкам СП-166 и проверка сделок, где поставлено
«Экономика готова» = «Да». Замер перед внедрением — зонд v52 (19.10.2026, 60 дней,
5 104 карточки): Price at Work без итога дольше 14 дней — 389, стадия КП без файла —
298, файл есть, а стадия до КП — 40; «Экономика готова» = «Да» у 5 сделок, по
правилу чисто — 0.

Нарушения карточки (код → смысл):
  noFile      — стадия КП (Price at Work, Selected, не выбран), а файла в
                «Offer from supplier» нет: КП не засчитается (п. 1, 3, 4);
  fileNoStage — файл КП приложен, а стадия до КП (новый, отправлен, переписка):
                не переведён на Price at Work (п. 2);
  fileClosed  — файл КП приложен, а стадия «Отказ в КП» или «Ответ не получен»;
  pawStale    — Price at Work дольше PAW_STALE_DAYS без итога (п. 9);
  legacy      — стадия «КП получено», которую правило отменило (п. 9).

Нарушение или «до правил» (split_flags). «До правил» — два разных случая:
застывшее наследие и льготный срок.
  Наследие — то, что сложилось до 19.10.2026 и под правилами заново не случится:
  noFile      — карточка ЗАКРЫТА (Selected, не выбран) раньше 19.10 (movedTime);
                на Price at Work файл приложить можно и сейчас — нарушение;
  legacy      — на «КП получено» перевели раньше 19.10 (как в зонде v52,
                scripts/probe_kp_rules.py);
  fileClosed  — файл при отказе или молчании: карточку не меняли с 19.10
                (updatedTime раньше даты) или последним после 19.10 её менял не
                её сорсер (updatedBy ≠ исполнитель) — переназначение, о котором
                просит сам дашборд, не делает чужое наследие нарушением нового
                исполнителя.
  Льготный срок — то, что исправляется сейчас, одним действием сорсера: по
  правилам у него на это PAW_STALE_DAYS дней с 19.10 (grace_until):
  pawStale    — часы считаются от max(movedTime, 19.10): нарушение, когда и
                под правилами прошло больше PAW_STALE_DAYS;
  fileNoStage — карточка открыта, перевести стадию можно сейчас; когда приложен
                файл, по выдаче не установить, поэтому до конца льготного срока
                — «до правил», после — нарушение (updatedTime не используется:
                любую правку карточки, хоть переназначение, он считал бы файлом).
«До правил» нарушением не вменяется, но остаётся списком на доразметку
(count_before). Карточка считается «до правил», только если нарушений у неё
нет: иначе одна и та же карточка стояла бы и в нарушениях, и «вне нарушений».
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from period import parse_dt
from stages import BEFORE_KP, KP_STAGES, LEGACY_KP_STAGE, classify_stage

FLAGS = ("noFile", "fileNoStage", "fileClosed", "pawStale", "legacy")
FLAG_LABELS = {
    "noFile": "стадия КП без файла",
    "fileNoStage": "файл есть, стадия до КП",
    "fileClosed": "файл есть, стадия отказа или молчания",
    "pawStale": "Price at Work без итога",
    "legacy": "стадия «КП получено»",
}


def card_flags(r: dict, as_of, stale_days: int) -> list[str]:
    """Нарушения одной карточки СП-166 (порядок — как FLAGS)."""
    sid = str(r.get("stageId") or "")
    b = classify_stage(sid)
    f = bool(r.get("_hasQuote"))
    out = []
    if b in KP_STAGES and not f:
        out.append("noFile")
    if f and b in BEFORE_KP:
        out.append("fileNoStage")
    if f and b in ("refused", "no_answer"):
        out.append("fileClosed")
    if b == "paw":
        moved = parse_dt(r.get("movedTime") or "")
        if moved and (as_of - moved).days > stale_days:
            out.append("pawStale")
    if sid == LEGACY_KP_STAGE:
        out.append("legacy")
    return out


def _date(since):
    return parse_dt(since) if isinstance(since, str) else since


def grace_until(since, stale_days: int):
    """Последний день льготного срока: since + stale_days. С следующего дня
    Price at Work без итога и файл без перевода стадии — нарушения."""
    since_d = _date(since) if since else None
    return since_d + dt.timedelta(days=stale_days) if since_d else None


def before_rules(r: dict, since) -> bool:
    """Стадию карточки в последний раз меняли до даты правил (since — date или ISO).

    Дата — movedTime, без неё — createdTime. Нет ни той ни другой — карточка
    считается по правилам, как было до этого разделения."""
    since_d = _date(since) if since else None
    if not since_d:
        return False
    moved = parse_dt(r.get("movedTime") or "") or parse_dt(r.get("createdTime") or "")
    return bool(moved and moved < since_d)


def split_flags(r: dict, as_of, stale_days: int, since=None) -> tuple[list[str], list[str]]:
    """(нарушения, «до правил») одной карточки — правила в шапке модуля.

    Без since всё — нарушения, как до разделения."""
    fl = card_flags(r, as_of, stale_days)
    since_d = _date(since) if since else None
    if not fl or not since_d:
        return fl, []
    b = classify_stage(str(r.get("stageId") or ""))
    moved_before = before_rules(r, since_d)
    льгота = (as_of - since_d).days <= stale_days          # льготный срок ещё идёт
    updated = parse_dt(r.get("updatedTime") or "")
    кто = str(r.get("updatedBy") or "")
    чей = str(r.get("_owner") or "")
    viol, pre = [], []
    for f in fl:
        if f == "noFile":
            до = moved_before and b != "paw"                # закрыта до правил
        elif f == "legacy":
            до = moved_before
        elif f == "pawStale":
            # часы под правилами: от max(перевод на Price at Work, дата правил)
            до = moved_before and льгота
        elif f == "fileNoStage":
            до = льгота
        else:                                               # fileClosed
            до = bool(updated and updated < since_d) or bool(кто and чей and кто != чей)
        (pre if до else viol).append(f)
    return viol, pre


def count_flags(items: list[dict], as_of, stale_days: int, since=None) -> tuple[Counter, int]:
    """(нарушения по коду, карточек хотя бы с одним нарушением).

    С since — без наследия до правил: его считает count_before."""
    c: Counter = Counter()
    cards = 0
    for r in items:
        fl, _pre = split_flags(r, as_of, stale_days, since)
        c.update(fl)
        cards += bool(fl)
    return c, cards


def count_before(items: list[dict], as_of, stale_days: int, since) -> tuple[Counter, int]:
    """«До правил» (наследие и льготный срок): признаки по коду — все, карточки —
    только без нарушений. Нарушением не считается — это список на доразметку."""
    c: Counter = Counter()
    cards = 0
    for r in items:
        fl, pre = split_flags(r, as_of, stale_days, since)
        c.update(pre)
        cards += bool(pre) and not fl
    return c, cards


def econ_check(deals: list[dict], rfqs: list[dict]) -> dict:
    """Сделки с «Экономика готова» = «Да» против правила п. 10: ни одного запроса
    на Price at Work и хотя бы один Selected. → {yes, ok, bad, rows} (rows — только
    нарушители, с причинами)."""
    по = {str(d.get("ID")): Counter() for d in deals}
    for r in rfqs:
        d = str(r.get("parentId2") or "")
        if d in по:
            по[d][classify_stage(str(r.get("stageId") or ""))] += 1
    rows = []
    for d in deals:
        did = str(d.get("ID"))
        c = по[did]
        why = []
        if not sum(c.values()):
            why.append("запросов поставщикам нет")
        if c["paw"]:
            why.append(f"на Price at Work: {c['paw']}")
        if sum(c.values()) and not c["selected"]:
            why.append("ни одного Selected")
        if why:
            rows.append({"id": did, "t": str(d.get("TITLE") or f"Сделка #{did}")[:90],
                         "own": str(d.get("ASSIGNED_BY_ID") or ""),
                         "sem": str(d.get("STAGE_SEMANTIC_ID") or "P").upper()[:1] or "P",
                         "n": sum(c.values()), "paw": c["paw"], "sel": c["selected"], "why": why})
    return {"yes": len(deals), "ok": len(deals) - len(rows), "bad": len(rows), "rows": rows}
