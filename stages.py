"""Классификация стадий смарт-процесса 166 и определение «ТКП выдано+» для сделок.

Бакеты RFQ (как в легенде дашборда):
  new      — Новый (не отправлен)        DT166_24:NEW
  sent     — Отправлен поставщику        DT166_24:PREPARATION
  dialog   — Переписка/цена              UC_61BSRU / UC_GFJ5A8 / UC_H49RUE
  selected — КП получено / выбран        SUCCESS, :1
  refused  — Отказ в КП                  :2
  no_answer— Нет ответа в срок           :3
  other    — Прочее                      FAIL, :4, :5
"""
from __future__ import annotations

# порядок важен для стопки и легенды
BUCKETS = ["new", "sent", "dialog", "selected", "refused", "no_answer", "other"]
BUCKET_LABELS = {
    "new": "Новый (не отправлен)",
    "sent": "Отправлен поставщику",
    "dialog": "Переписка/цена",
    "selected": "КП получено/выбран",
    "refused": "Отказ в КП",
    "no_answer": "Нет ответа в срок",
    "other": "Прочее",
}
OPEN = {"new", "sent", "dialog"}        # «в работе»
CLOSED = {"selected", "refused", "no_answer", "other"}

_SPA_STAGE_BUCKET = {
    "DT166_24:NEW": "new",
    "DT166_24:PREPARATION": "sent",
    "DT166_24:UC_61BSRU": "dialog",
    "DT166_24:UC_GFJ5A8": "dialog",
    "DT166_24:UC_H49RUE": "dialog",
    "DT166_24:SUCCESS": "selected",
    "DT166_24:1": "selected",
    "DT166_24:2": "refused",
    "DT166_24:3": "no_answer",
    "DT166_24:FAIL": "other",
    "DT166_24:4": "other",
    "DT166_24:5": "other",
}


def classify_stage(stage_id: str) -> str:
    if stage_id in _SPA_STAGE_BUCKET:
        return _SPA_STAGE_BUCKET[stage_id]
    suf = stage_id.split(":")[-1] if stage_id else ""
    if suf == "NEW":
        return "new"
    if suf == "PREPARATION":
        return "sent"
    if suf in ("SUCCESS", "1"):
        return "selected"
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
    name = (stage_name or "").upper()
    if any(n in name for n in _TKP_NEG):
        return False
    if any(p in name for p in _TKP_POS):
        return True
    return False
