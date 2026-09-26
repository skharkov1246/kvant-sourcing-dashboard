#!/usr/bin/env python3
"""Проверщик набора подтверждённых субпоставщиков: data/subsuppliers/verified.json.

ЗАЧЕМ. Владелец поручил вводить «стопроцентно известных» субпоставщиков
брендов — компании, чей узел или деталь идёт в машину бренда штатно. Стопроцентно
здесь значит одно: у записи есть первичное доказательство (страница бренда,
страница субпоставщика или официальный документ бренда), дословная цитата и
адрес, по которому её можно перечитать. Без механической проверки такой набор
быстро обрастает записями «со слов перепродавца», а публичный репозиторий —
контактами людей (CLAUDE.md, правило 5).

ЧТО ПРОВЕРЯЕТСЯ.
  * схема файла: schema == 1, updated — дата, rule — непустой текст, records,
    rejected и gaps — списки; неизвестное поле верхнего уровня — нарушение;
  * у каждой записи — обязательные непустые поля, evidence_kind из списка
    «а», «б», «в», цитата не пустая и не длиннее 300 знаков, sources — непустой
    список адресов http(s), checked — дата, component — по-русски;
  * oem_key — ключ dict/oem.json или brand_key dict/model_series.json;
  * записи не повторяются (бренд, компания, узел);
  * у отклонённых — ключ, компания, причина и адрес;
  * нигде нет e-mail и телефонов (адреса ссылок из проверки вынимаются).

ПЕЧАТЬ — ТОЛЬКО АГРЕГАТЫ (CLAUDE.md, правило 17): число записей по брендам и
видам доказательства, место нарушения (раздел, номер, правило) — без имён и цитат.

    python scripts/subsuppliers_check.py
    python scripts/subsuppliers_check.py --file <набор> --oem <dict/oem.json> --series <dict/model_series.json>

Код возврата 1 при любом нарушении.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
НАБОР = ROOT / "data" / "subsuppliers" / "verified.json"
СЛОВАРЬ = ROOT / "dict" / "oem.json"
РЯДЫ = ROOT / "dict" / "model_series.json"

ВИДЫ = ("а", "б", "в")
ВЕРХ = {"schema": int, "updated": str, "rule": str, "records": list, "rejected": list, "gaps": list}
ВЕРХ_ОБЯЗАТЕЛЬНЫЕ = ("schema", "updated", "rule", "records", "gaps")
ЗАПИСЬ = ("oem_key", "brand", "company", "component", "machines", "evidence_kind", "quote", "checked")
ЗАПИСЬ_НЕОБЯЗАТЕЛЬНЫЕ = ("note",)
ОТКАЗ = ("oem_key", "company", "component", "reason")
ПРЕДЕЛ_ЦИТАТЫ = 300

ДАТА = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ССЫЛКА = re.compile(r"^https?://[^\s]+$")
ССЫЛКИ_В_ТЕКСТЕ = re.compile(r"https?://\S+")
КИРИЛЛИЦА = re.compile(r"[а-яё]", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Телефон — только по явному признаку: «+» и не меньше девяти цифр, метка
# «тел./phone/fax» или российский «8 (xxx)». Голые группы цифр — обозначения
# деталей и двигателей («TAD1643VE-B», «2 x 200 kW»), их обвинять нельзя.
ТЕЛЕФОН = (
    re.compile(r"(?<![\w+])\+\s?\d[\d\s().\-]{7,}\d"),
    re.compile(r"(?i)\b(?:тел|телефон|phone|tel|fax|факс)\b\.?\s*[:№]?\s*\+?\d[\d\s().\-]{5,}"),
    re.compile(r"(?<!\d)8\s?\(\d{3,5}\)\s?\d{1,3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"),
)


def найти_контакты(text: str) -> list[str]:
    """Какие виды контактов есть в тексте — без самих значений."""
    t = ССЫЛКИ_В_ТЕКСТЕ.sub(" ", str(text or ""))
    found = []
    if EMAIL.search(t):
        found.append("e-mail")
    if any(rx.search(t) for rx in ТЕЛЕФОН):
        found.append("телефон")
    return found


def _строки(obj, путь=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _строки(v, f"{путь}.{k}" if путь else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _строки(v, f"{путь}[{i}]")
    elif isinstance(obj, str):
        yield путь, obj


def _текст(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _ссылки(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(u, str) and ССЫЛКА.match(u) for u in v)


def проверить(d, ключи: set[str]) -> list[str]:
    """Список нарушений набора. Сообщения — без значений полей."""
    if not isinstance(d, dict):
        return ["набор: верхний уровень — не объект"]
    н: list[str] = []
    for поле in ВЕРХ_ОБЯЗАТЕЛЬНЫЕ:
        if поле not in d:
            н.append(f"набор: нет поля {поле}")
    for поле, v in d.items():
        тип = ВЕРХ.get(поле)
        if тип is None:
            н.append(f"набор: неизвестное поле {поле}")
        elif not isinstance(v, тип) or isinstance(v, bool):
            н.append(f"набор: поле {поле} не того типа")
    if d.get("schema") != 1:
        н.append("набор: schema не 1")
    if isinstance(d.get("updated"), str) and not ДАТА.match(d["updated"]):
        н.append("набор: updated — не дата ГГГГ-ММ-ДД")
    if "rule" in d and not _текст(d.get("rule")):
        н.append("набор: rule пустое")

    видено: set[tuple] = set()
    for i, r in enumerate(d.get("records") or []):
        где = f"records[{i}]"
        if not isinstance(r, dict):
            н.append(f"{где}: не объект")
            continue
        for поле in ЗАПИСЬ:
            if not _текст(r.get(поле)):
                н.append(f"{где}: пустое или нет поля {поле}")
        for поле in r:
            if поле not in ЗАПИСЬ and поле not in ЗАПИСЬ_НЕОБЯЗАТЕЛЬНЫЕ and поле != "sources":
                н.append(f"{где}: неизвестное поле {поле}")
        if not _ссылки(r.get("sources")):
            н.append(f"{где}: sources — не список адресов http(s)")
        if _текст(r.get("evidence_kind")) and r["evidence_kind"] not in ВИДЫ:
            н.append(f"{где}: evidence_kind не из списка а/б/в")
        if isinstance(r.get("quote"), str) and len(r["quote"]) > ПРЕДЕЛ_ЦИТАТЫ:
            н.append(f"{где}: цитата длиннее {ПРЕДЕЛ_ЦИТАТЫ} знаков")
        if _текст(r.get("checked")) and not ДАТА.match(r["checked"]):
            н.append(f"{где}: checked — не дата")
        if _текст(r.get("component")) and not КИРИЛЛИЦА.search(r["component"]):
            н.append(f"{где}: component не по-русски")
        if _текст(r.get("oem_key")) and r["oem_key"] not in ключи:
            н.append(f"{где}: oem_key нет в словарях брендов")
        ключ = tuple(str(r.get(k, "")).strip().casefold() for k in ("oem_key", "company", "component"))
        if ключ in видено:
            н.append(f"{где}: запись повторяется")
        видено.add(ключ)

    for i, r in enumerate(d.get("rejected") or []):
        где = f"rejected[{i}]"
        if not isinstance(r, dict):
            н.append(f"{где}: не объект")
            continue
        for поле in ОТКАЗ:
            if not _текст(r.get(поле)):
                н.append(f"{где}: пустое или нет поля {поле}")
        if not _ссылки(r.get("sources")):
            н.append(f"{где}: sources — не список адресов http(s)")

    for i, g in enumerate(d.get("gaps") or []):
        if not _текст(g):
            н.append(f"gaps[{i}]: пустая строка")

    for путь, s in _строки(d):
        for вид in найти_контакты(s):
            н.append(f"{путь}: {вид}")
    return н


def ключи_брендов(oem: Path, series: Path) -> set[str]:
    ключи: set[str] = set()
    if oem.exists():
        ключи |= {r["oem_key"] for r in json.loads(oem.read_text(encoding="utf-8")).get("records", [])
                  if isinstance(r, dict) and r.get("oem_key")}
    if series.exists():
        ключи |= {b["brand_key"] for b in json.loads(series.read_text(encoding="utf-8")).get("brands", [])
                  if isinstance(b, dict) and b.get("brand_key")}
    return ключи


def сводка(d: dict) -> dict:
    записи = [r for r in d.get("records") or [] if isinstance(r, dict)]
    return {
        "записей": len(записи),
        "по брендам": dict(Counter(str(r.get("oem_key")) for r in записи).most_common()),
        "по видам": dict(sorted(Counter(str(r.get("evidence_kind")) for r in записи).items())),
        "отклонено": len(d.get("rejected") or []),
        "пробелов": len(d.get("gaps") or []),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Проверка набора подтверждённых субпоставщиков")
    ap.add_argument("--file", default=str(НАБОР))
    ap.add_argument("--oem", default=str(СЛОВАРЬ))
    ap.add_argument("--series", default=str(РЯДЫ))
    a = ap.parse_args(argv)
    try:
        d = json.loads(Path(a.file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("набор не читается как JSON")
        return 1
    нарушения = проверить(d, ключи_брендов(Path(a.oem), Path(a.series)))
    if isinstance(d, dict):
        с = сводка(d)
        print(f"записей: {с['записей']} · отклонено: {с['отклонено']} · пробелов: {с['пробелов']}")
        print("по брендам: " + ", ".join(f"{k} {v}" for k, v in с["по брендам"].items()))
        print("по видам доказательства: " + ", ".join(f"{k} {v}" for k, v in с["по видам"].items()))
    if нарушения:
        print(f"нарушений: {len(нарушения)}")
        for s in нарушения:
            print("  " + s)
        return 1
    print("нарушений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
