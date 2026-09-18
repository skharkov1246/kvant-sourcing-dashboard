"""Набор перепроверки не должен противоречить сам себе.

Написано после трёх собственных ошибок за одну ночь 17–18.09.2026, каждую из
которых поймала проверка, а не внимательность: справка утверждала про поля
«Result, ТКП» то, что замер опроверг; измеритель покрытия не считал КП
поставщиков и печатал «сошлось 0»; вывод по LF16031 был построен на цене
ПОХОЖЕГО номера и указывал в противоположную сторону.

Здесь проверяется внутренняя связность того, что уходит владельцу: вердикт по
вилке обязан согласовываться с наличием цены, а «нечем проверить» не может
соседствовать с рекомендованной цифрой.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_reverify.json"
VERDICTS = {"ЗАНИЖЕНА", "ЗАВЫШЕНА", "ВЕРНА", "НЕЧЕМ ПРОВЕРИТЬ"}
PRICED = {"ЗАНИЖЕНА", "ЗАВЫШЕНА", "ВЕРНА"}


def rows() -> list[dict]:
    if not SRC.exists():
        pytest.skip("набора перепроверки нет")
    d = json.loads(SRC.read_text(encoding="utf-8"))
    return d["rows"] if isinstance(d, dict) else d


def verdict(r: dict) -> str:
    v = (r.get("band_verdict") or "").upper()
    return next((k for k in VERDICTS if k in v), "")


def has_number(*vals) -> bool:
    """Есть ли в тексте хоть одно число, похожее на цену."""
    for v in vals:
        if re.search(r"\d[\d\s .,]*\d", str(v or "")):
            return True
    return False


def test_вердикт_из_закрытого_списка():
    bad = [r["pn"] for r in rows() if not verdict(r)]
    assert not bad, f"вердикт не опознан у строк: {bad}"


def test_у_каждой_строки_есть_чем_подтверждено():
    """Без этого поля строка — утверждение без основания."""
    bad = [r["pn"] for r in rows() if not (r.get("note") or "").strip()]
    assert not bad, f"нет поля «чем подтверждено»: {bad}"


def test_вердикт_с_ценой_опирается_на_источник():
    """ЗАНИЖЕНА, ЗАВЫШЕНА и ВЕРНА выносятся ТОЛЬКО по найденной цене.

    Иначе вердикт — догадка, а выглядит как замер.
    """
    bad = []
    for r in rows():
        if verdict(r) not in PRICED:
            continue
        if not has_number(r.get("price_low"), r.get("price_high"),
                          r.get("price_authorized"), r.get("recommended")):
            bad.append(r["pn"])
        elif not (r.get("price_source") or "").strip():
            bad.append(f'{r["pn"]} (нет price_source)')
    assert not bad, f"вердикт по цене без цены или без источника: {bad}"


def test_нечем_проверить_не_даёт_рекомендованной_цифры():
    """Самая опасная связка: «проверить нечем» рядом с конкретной ценой.

    Такая строка читается как подтверждённая, хотя подтверждения нет.
    Рекомендация при этом остаётся полезной, если она говорит, что закладывать
    нечего или чего не хватает, — поэтому запрещено именно ЧИСЛО.
    """
    bad = []
    for r in rows():
        if verdict(r) != "НЕЧЕМ ПРОВЕРИТЬ":
            continue
        rec = str(r.get("recommended") or "")
        # разрешаем числа, которые описывают НАШУ вилку или объём, а не
        # рекомендованный уровень: их вводят словами «наша вилка», «по строке»
        if re.search(r"\d[\d\s .,]*\d", rec) and not re.search(
                r"наша вилка|нечем|не опроверг|не подтвержд|по строке|закладывать нечего"
                r"|проверить нечего|для порядка величин", rec, re.I):
            bad.append(r["pn"])
    assert not bad, ("«нечем проверить» с конкретной рекомендованной ценой "
                     f"без оговорки: {bad}")


def test_скептики_списком_и_с_полем_holds():
    bad = []
    for r in rows():
        sk = r.get("skeptics")
        if sk is None or not isinstance(sk, list):
            bad.append(r["pn"])
            continue
        for s in sk:
            if not isinstance(s, dict) or "holds" not in s:
                bad.append(f'{r["pn"]} (скептик без holds)')
    assert not bad, f"поле проверки на опровержение испорчено: {bad}"


def test_артикулы_не_дублируются():
    """Дубликат удваивает строку в документе и в любой сумме по нему."""
    seen, dup = set(), []
    for r in rows():
        k = re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").split("(")[0].upper())
        if k in seen:
            dup.append(r["pn"])
        seen.add(k)
    assert not dup, f"дубликаты артикулов: {dup}"
