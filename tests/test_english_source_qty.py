"""Две меры совпадения количества нельзя путать между собой.

Оплачено ложной цифрой в отчёте владельцу 18.09.2026. В сводке стояло
«количества совпадают у 425 строк из 431», и читалось это как «количество
заявки сходится с её английским первоисточником». Мера же проверяла другое:
ВСТРЕЧАЕТСЯ ли количество первоисточника среди строк заявки по этому номеру.
У сборки заглушки бороскопа ПТ-1 первоисточник несёт 8 шт, заявка — 8 плюс 16
отдельной строкой, итого 24, и мера отвечала «совпало».

Сильная мера — равенство СУММ. По ней сходится 337, а не 425, и на разнице
стоит около полумиллиона долларов экспозиции. Здесь закрыто, чтобы слабая мера
больше не могла выйти в документ под именем сильной.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_english_source.json"
SUMMARY = ROOT / "gt/data/ship_lukoil.json"


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("набора первоисточника нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_обе_меры_названы_своими_именами():
    """Безымянной меры быть не должно: читающий обязан видеть, какая именно."""
    t = doc()["totals"]
    assert "qty_match" not in t, ("ключ qty_match двусмыслен — он означал слабую меру, "
                                  "а читался как сильная; нужны qty_match_line и "
                                  "qty_match_total")
    assert "qty_match_line" in t and "qty_match_total" in t


def test_сильная_мера_это_равенство_сумм():
    for r in doc()["rows"]:
        if not r.get("in_request"):
            continue
        want = abs(float(r["qty_request"] or 0) - float(r["qty_source_pn"] or 0)) < 0.5
        assert r["qty_match_total"] is want, (
            f'{r["pn"]}: сильная мера должна быть равенством сумм '
            f'({r["qty_request"]} против {r["qty_source_pn"]})')


def test_слабая_мера_не_может_быть_строже_сильной_в_заголовке():
    """Если слабая мера больше сильной, документ обязан сказать об этом сам."""
    t = doc()["totals"]
    if t["qty_match_line"] <= t["qty_match_total"]:
        return
    warn = doc().get("qty_warning") or ""
    assert str(t["qty_match_total"]) in warn and str(t["qty_match_line"]) in warn, (
        "слабая мера показывает больше сильной, а оговорки в наборе нет")


def test_деньги_на_неподтверждённом_количестве_считаются_а_не_назначаются():
    g = doc()["totals"].get("qty_gap") or {}
    if not g:
        pytest.skip("замер денег не собран")
    assert abs(g["usd_at_stake"] - (g["usd_by_summary_qty"] - g["usd_by_source_qty"])) < 1.0
    rows = json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]
    by = {re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").upper()): r for r in rows}
    for x in g["top"]:
        r = by[re.sub(r"[^A-Z0-9]", "", x["pn"].upper())]
        mid = (float(r["usd_lo"]) + float(r["usd_hi"])) / 2
        want = mid * (float(x["qty_summary"]) - float(x["qty_source"]))
        assert abs(x["usd_gap"] - want) < 1.0, f'{x["pn"]}: экспозиция не сходится с вилкой'
        assert x["qty_summary"] > x["qty_source"], f'{x["pn"]}: в список попал не тот случай'


def test_расхождение_не_называется_переплатой():
    """Два объяснения, и выбрать между ними может только заказчик.

    Заявка может покрывать больше машин, чем английский лист. Поэтому слово
    «переплата» здесь запрещено: измерено не она, а количество без основания.
    """
    d = doc()
    text = json.dumps({k: v for k, v in d.items() if k != "rows"}, ensure_ascii=False)
    for word in ("переплат", "завышение количества", "приписал"):
        assert word not in text.lower(), f"в замере стоит обвинение «{word}», а измерено не оно"
