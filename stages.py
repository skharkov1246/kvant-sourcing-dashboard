"""Классификация стадий смарт-процесса 166 и определение «ТКП выдано+» для сделок.

Корзины запроса — по правилам владельца 19.10.2026 («Как отмечать полученные КП»).
Коды и имена стадий — зонд v52 (19.10.2026), воронка 24:

  new       — New Request                               NEW
  sent      — Request Sent                              PREPARATION
  dialog    — Unread Messages, In Correspondance        UC_61BSRU, UC_GFJ5A8
              (ответ без цены — ещё не КП)
  paw       — Price at Work: КП получено, итога нет     UC_H49RUE
              и устаревшая «КП получено» (:1) — её      :1
              правило отменило, итог ей тоже нужен
  selected  — Selected: поставщика выбрали              SUCCESS
  notsel    — КП есть, выбран другой: Not Selected,     FAIL, :5, :4
              «Не прошли по цене», «Не подошло по технике»
  refused   — Отказ в КП                                :2
  no_answer — Ответ не получен (в срок)                 :3
  other     — стадия, которой нет в таблице (новая в портале)

До 19.10.2026 Price at Work числилась перепиской, а Not Selected и обе стадии
«не подошло» — в «прочем» рядом с отказами: полученное КП, не выбранное по цене,
засчитывалось сорсеру отказом.
"""
from __future__ import annotations

# порядок важен для стопки и легенды
BUCKETS = ["new", "sent", "dialog", "paw", "selected", "notsel", "refused", "no_answer", "other"]
BUCKET_LABELS = {
    "new": "Новый (не отправлен)",
    "sent": "Отправлен поставщику",
    "dialog": "Переписка без цены",
    "paw": "Price at Work — КП без итога",
    "selected": "Selected — выбран",
    "notsel": "КП есть, не выбран",
    "refused": "Отказ в КП",
    "no_answer": "Нет ответа в срок",
    "other": "Прочее",
}
OPEN = {"new", "sent", "dialog", "paw"}        # «в работе»: у Price at Work итога ещё нет
CLOSED = {"selected", "notsel", "refused", "no_answer", "other"}
KP_STAGES = {"paw", "selected", "notsel"}      # стадии, где КП обязано лежать файлом
BEFORE_KP = {"new", "sent", "dialog"}          # стадии до КП

_SPA_STAGE_BUCKET = {
    "DT166_24:NEW": "new",
    "DT166_24:PREPARATION": "sent",
    "DT166_24:UC_61BSRU": "dialog",
    "DT166_24:UC_GFJ5A8": "dialog",
    "DT166_24:UC_H49RUE": "paw",
    "DT166_24:SUCCESS": "selected",
    "DT166_24:FAIL": "notsel",
    "DT166_24:1": "paw",
    "DT166_24:2": "refused",
    "DT166_24:3": "no_answer",
    "DT166_24:4": "notsel",
    "DT166_24:5": "notsel",
}
LEGACY_KP_STAGE = "DT166_24:1"                 # «КП получено» — отменена правилом 19.10.2026


def kp_stage_ids() -> list[str]:
    """Коды стадий воронки СП-166, которые засчитываются как «КП получено» (KP_STAGES и прежняя
    «КП получено»): по первому входу в любую из них считается когорта «КП за N дней» (зонд
    scripts/probe_unpriced.py — те же стадии)."""
    return sorted(s for s, b in _SPA_STAGE_BUCKET.items() if b in KP_STAGES)


def classify_stage(stage_id: str) -> str:
    if stage_id in _SPA_STAGE_BUCKET:
        return _SPA_STAGE_BUCKET[stage_id]
    suf = stage_id.split(":")[-1] if stage_id else ""
    if suf == "NEW":
        return "new"
    if suf == "PREPARATION":
        return "sent"
    if suf == "SUCCESS":
        return "selected"
    if suf == "1":
        return "paw"
    if suf in ("FAIL", "4", "5"):
        return "notsel"
    if suf == "2":
        return "refused"
    if suf == "3":
        return "no_answer"
    if suf.startswith("UC_"):
        return "dialog"
    return "other"


def is_closed(bucket: str) -> bool:
    return bucket in CLOSED


# --- классификатор сделки «ТКП выдано+» (выдано и далее, включая WON) ---
# Внимание: эвристика по имени стадии + семантике. Списки тюнятся при необходимости.
_TKP_POS = (
    "ТКП ОТПРАВЛЕН", "ТКП ГОТОВ", "ТКП ПРИНЯТ", "ТКП ВЫДАН", "ТКП СОГЛАСОВАН",
    "ОТГРУ", "ДОСТАВ", "ПРОИЗВОДСТВО ЗАВЕРШЕНО", "ПОДГОТОВКА К ОТГРУЗКЕ",
)
_TKP_NEG = (
    "ОТКАЗ", "НЕ ВЫДА", "НЕ ОТВЕТ", "НЕ ПРИСЛ", "ЗАКРЫЛ ВЫДАЧУ", "НЕИНТЕРЕСНО", "ПОДГОТОВКА ТКП",
)
_TKP_SUFFIX = {"WON", "EXECUTING"}


def deal_reached_tkp(stage_id: str, semantic_id: str | None, stage_name: str | None) -> bool:
    """Достигла ли сделка стадии «ТКП выдано» или дальше (вкл. победу)."""
    if (semantic_id or "").upper() == "S":  # won
        return True
    suf = stage_id.split(":")[-1] if stage_id else ""
    if suf in _TKP_SUFFIX:
        return True
    if suf == "NEW":  # начальная стадия — не считаем по имени (бывают мисконфиг-имена стадий)
        return False
    name = (stage_name or "").upper()
    if any(n in name for n in _TKP_NEG):
        return False
    if any(p in name for p in _TKP_POS):
        return True
    return False
