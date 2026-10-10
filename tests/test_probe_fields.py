"""Зонд полей: что считается заполненным и как поле раскладывается по фазам."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from scripts import probe_fields as зонд  # noqa: E402


def test_пустые_значения_портала_не_считаются_заполненными():
    for v in (None, "", [], {}, False, "0", 0, "N", "0.00", "|RUB", "0|EUR", "0.00|USD", [""], {"id": ""}):
        assert not зонд.заполнено(v), repr(v)
    for v in ("Y", "abc", 5, "1500|RUB", ["7"], {"id": "3"}, "2026-10-09T00:00:00+03:00"):
        assert зонд.заполнено(v), repr(v)


def test_фаза_по_трети_рабочих_стадий():
    рабочие = [10, 20, 30, 40, 50, 60]
    assert [зонд.фаза_по_трети(s, рабочие) for s in рабочие] == \
        ["ранние", "ранние", "средние", "средние", "поздние", "поздние"]


def test_таблица_считает_по_фазам_и_не_печатает_значения():
    записи = [{"UF_A": "x", "ph": "р"}, {"UF_A": "", "ph": "р"}, {"UF_A": "y", "ph": "п"}]
    поля = {"UF_A": {"type": "string", "isRequired": True, "formLabel": "Поле А"}}
    [s] = зонд.таблица(записи, ["р", "п"], поля, lambda r: r["ph"])
    assert (s["n"], s["pct"], s["phase"], s["req"], s["label"]) == (2, "67", {"р": "50", "п": "100"}, True, "Поле А")
    assert "x" not in str(s) and "y" not in str(s).replace("type", "")


def test_дубли_находятся_по_подписи():
    assert зонд.норм("KAM (старое)") == зонд.норм("KAM") == "kam"
    assert зонд.норм("Сорсер, копия") == зонд.норм("Сорсер") == "сорсер"
