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

Нарушение или наследие до правил (split_flags). «До правил» — только то, что
застыло до 19.10.2026 и после правил не могло случиться заново; признак, который
продолжает копиться под правилами, — нарушение:
  noFile      — до правил, если карточка ЗАКРЫТА (Selected, не выбран) и
                закрыта раньше 19.10 (movedTime); на Price at Work файл
                приложить можно и сейчас — нарушение;
  legacy      — до правил, если на «КП получено» перевели раньше 19.10 (как в
                зонде v52, scripts/probe_kp_rules.py);
  pawStale    — часы считаются от max(movedTime, 19.10): нарушение, когда и
                под правилами прошло больше PAW_STALE_DAYS; раньше — до правил.
                Иначе карточка, поставленная на Price at Work 15.10, не стала бы
                нарушением никогда, хотя стоит без итога и под правилами;
  fileNoStage, fileClosed — файл прикладывают без смены стадии, поэтому
                movedTime о времени файла ничего не говорит. До правил — только
                если карточку не меняли с 19.10 вовсе (updatedTime раньше даты:
                файл приложен не позже); нет updatedTime — нарушение.
Наследие нарушением не вменяется, но остаётся списком на доразметку
(count_before).
"""
from __future__ import annotations

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
    """(нарушения, наследие до правил) одной карточки — правила в шапке модуля.

    Без since всё — нарушения, как до разделения."""
    fl = card_flags(r, as_of, stale_days)
    since_d = _date(since) if since else None
    if not fl or not since_d:
        return fl, []
    b = classify_stage(str(r.get("stageId") or ""))
    moved_before = before_rules(r, since_d)
    updated = parse_dt(r.get("updatedTime") or "")
    viol, pre = [], []
    for f in fl:
        if f == "noFile":
            застыло = moved_before and b != "paw"            # закрыта до правил
        elif f == "legacy":
            застыло = moved_before
        elif f == "pawStale":
            # часы под правилами: от max(перевод на Price at Work, дата правил)
            застыло = moved_before and (as_of - since_d).days <= stale_days
        else:                                                # fileNoStage, fileClosed
            застыло = bool(updated and updated < since_d)
        (pre if застыло else viol).append(f)
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
    """Наследие до правил: признаки, застывшие до since. Нарушением не считается —
    это список на доразметку."""
    c: Counter = Counter()
    cards = 0
    for r in items:
        _fl, pre = split_flags(r, as_of, stale_days, since)
        c.update(pre)
        cards += bool(pre)
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
