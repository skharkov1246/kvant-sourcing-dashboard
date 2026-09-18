"""В отчёте не должно быть чисел, набранных руками.

Проверка 18.09.2026 нашла в оглавлении отчёта строку «13 писем на 820 позиций»,
тогда как в наборе их было 17 на 1 007. Набранное руками число живёт ровно до
следующего прогона, а читается владельцем как сегодняшнее — и никакой признак
не показывает, что оно устарело.

Здесь проверяется не весь текст, а самое опасное место: оглавление документов,
где числа описывают ДРУГИЕ файлы и потому расходятся незаметно.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "gt/docs/ОТЧЁТ-ЗАЯВКА-ЛУКОЙЛ.html"


def text() -> str:
    if not DOC.exists():
        pytest.skip("отчёта нет")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", DOC.read_text(encoding="utf-8"),
               flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())


def num(s: str) -> int:
    return int(s.replace(" ", "").replace(" ", ""))


def test_число_писем_в_оглавлении_совпадает_с_набором():
    p = ROOT / "gt/data/ship_lists_rfq.json"
    if not p.exists():
        pytest.skip("набора писем нет")
    d = json.loads(p.read_text(encoding="utf-8"))
    m = re.search(r"([\d  ]+) писем на ([\d  ]+) позиций", text())
    assert m, "строка про письма исчезла из оглавления"
    assert num(m.group(1)) == len(d.get("letters") or []), m.group(0)
    assert num(m.group(2)) == d.get("rows_covered"), m.group(0)


def test_число_строк_рабочего_листа_совпадает_с_набором():
    p = ROOT / "gt/data/ship_inside_quotes.json"
    if not p.exists():
        pytest.skip("замера нет")
    d = json.loads(p.read_text(encoding="utf-8"))
    m = re.search(r"([\d  ]+) строк заявки на ([\d  ]+) долларов, цена по которым", text())
    assert m, "строка про рабочий лист исчезла из оглавления"
    assert num(m.group(1)) == d["rows_without_our_price"], m.group(0)
    assert num(m.group(2)) == round(d["usd_without_our_price"]), m.group(0)


def test_в_оглавлении_нет_номера_сделки_набранного_руками():
    """Номер сделки в прозе устаревает так же, как и счёт, и проверить его
    глазами нельзя: он выглядит одинаково правильным при любом значении."""
    src = (ROOT / "gt/tools/ship_final.py").read_text(encoding="utf-8")
    assert "сделки 18016" not in src, "номер сделки снова набран в тексте отчёта"
