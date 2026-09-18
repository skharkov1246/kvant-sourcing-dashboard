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


def test_четыре_разряда_решения_складываются_в_разобранные_строки():
    """Строка не может быть одновременно в двух разрядах и не может выпасть.

    Разряд «открыть своё вложение» добавлен 18.09.2026 по найденному
    противоречию: 114 строк на 3 446 303 USD стояли в «не забираем» с пометкой
    «цены нет ни у одного проверенного продавца», тогда как цена по ним лежала в
    предложении, которое поставщик нам уже прислал. Отчёт предлагал руководству
    отказаться от трёх с половиной миллионов долларов по неверному основанию.

    Если разряды перестанут складываться, значит признак начал срабатывать
    дважды либо строка выпала, и таблица решений снова начнёт лгать.
    """
    import sys

    sys.path.insert(0, str(ROOT / "gt/tools"))
    import ship_final as sf

    rv = ROOT / "gt/data/ship_reverify.json"
    inq = ROOT / "gt/data/ship_inside_quotes.json"
    if not rv.exists():
        pytest.skip("набора нет")
    rows = json.loads(rv.read_text(encoding="utf-8"))["rows"]
    house = set()
    if inq.exists():
        house = {sf.key(r.get("pn"))
                 for r in json.loads(inq.read_text(encoding="utf-8")).get("rows", [])}
    got = [sf.group_of(r, house) for r in rows]
    assert len(got) == len(rows)
    assert set(got) <= {"берём", "ждём", "не берём", "открыть своё вложение"}, set(got)


def test_строка_с_ценой_в_нашем_вложении_не_попадает_в_отказ():
    import sys

    sys.path.insert(0, str(ROOT / "gt/tools"))
    import ship_final as sf

    rv = ROOT / "gt/data/ship_reverify.json"
    inq = ROOT / "gt/data/ship_inside_quotes.json"
    if not rv.exists() or not inq.exists():
        pytest.skip("наборов нет")
    house = {sf.key(r.get("pn"))
             for r in json.loads(inq.read_text(encoding="utf-8")).get("rows", [])}
    bad = [r["pn"] for r in json.loads(rv.read_text(encoding="utf-8"))["rows"]
           if sf.group_of(r, house) == "не берём" and sf.key(r.get("pn")) in house]
    assert not bad, f"отказ по строкам, цена которых лежит в нашем же вложении: {bad[:5]}"


def test_число_со_словом_согласовано():
    """«21 писем» в оглавлении отчёта читается как неверная цифра.

    Документ читает владелец: если не сошлось согласование, он справедливо
    сомневается и в счёте. Правило русского счёта: 1, 21, 101 — «письмо»;
    2–4, 22–24 — «письма»; 5–20, 11–14 в любой сотне — «писем».
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("sf", ROOT / "gt/tools/ship_final.py")
    sf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sf)
    cases = {1: "1 письмо", 2: "2 письма", 4: "4 письма", 5: "5 писем",
             11: "11 писем", 12: "12 писем", 14: "14 писем", 21: "21 письмо",
             22: "22 письма", 25: "25 писем", 100: "100 писем", 101: "101 письмо",
             111: "111 писем", 112: "112 писем", 0: "0 писем"}
    for n, want in cases.items():
        got = sf.plural(n, "письмо", "письма", "писем")
        assert got == want, f"{n}: получили «{got}», ждали «{want}»"


def d(name: str):
    p = ROOT / "gt/data" / name
    if not p.exists():
        pytest.skip(f"нет набора {name}")
    return json.loads(p.read_text(encoding="utf-8"))


def test_пакеты_писем_не_превышают_ступень_есть_кому_написать():
    """Письмо нельзя написать по строке, у которой канал не назван.

    Три пакета писем считаются независимо друг от друга и от лестницы
    понимания. Если сумма охваченных ими строк превысит ступень «есть кому
    написать», значит один из пакетов берёт строки, которых на этой ступени
    нет, — то есть письмо уйдёт в пустоту. За сутки учёт писем ломался дважды
    именно на добавлении пути, поэтому проверка сверяет их с лестницей, а не
    сами с собой.
    """
    cov = d("ship_coverage.json")
    ql, vl = d("ship_quote_letters.json"), d("ship_volume_letters.json")
    channel = cov["ladder"]["есть кому написать"]["rows"]
    # quote_letters берёт строки без письма в других пакетах, volume_letters —
    # строки с ценой без остатка числом: пересечения между ними нет по
    # построению, и это закрыто отдельным тестом в test_quote_letters.py
    covered = ql["rows_with_address"] + ql["rows_to_maker"] + ql["rows_to_form"] \
        + vl["rows_with_address"]
    assert covered <= channel, (
        f"письма охватывают {covered} строк при {channel} на ступени «есть кому написать»: "
        f"часть писем уйдёт по строкам без канала")


def test_ступени_лестницы_не_растут_вверх():
    """Каждая ступень требует всех предыдущих — значит она не может быть шире.

    Проверка тривиальная и именно поэтому нужная: ступени считаются одним
    проходом, и любая правка условия одной из них способна незаметно нарушить
    вложенность, а лестница читается владельцем как накопительная.
    """
    lad = d("ship_coverage.json")["ladder"]
    order = ["опознана", "есть кому написать", "есть цена и адрес", "отгружаема"]
    prev = None
    for name in order:
        cur = lad[name]
        if prev is not None:
            assert cur["rows"] <= prev["rows"], f"{name} шире предыдущей ступени"
            assert cur["usd"] <= prev["usd"] + 1, f"{name} дороже предыдущей ступени"
        prev = cur


def test_заниженные_строки_подмножество_ступени_с_ценой():
    """«Продаём ниже закупки» требует найденной цены — значит не может быть
    шире ступени «есть цена и адрес»."""
    und, cov = d("ship_underpriced.json"), d("ship_coverage.json")
    assert len(und["rows"]) <= cov["ladder"]["есть цена и адрес"]["rows"]


def test_счётная_доля_закупки_не_больше_строк_с_ценой():
    """Закупку можно сложить только там, где цена найдена и проверена."""
    mrg, cov = d("ship_margin.json"), d("ship_coverage.json")
    assert mrg["countable"]["rows"] <= cov["ladder"]["есть цена и адрес"]["rows"]
    by = mrg["by_grade"]
    assert sum(v["rows"] for v in by.values()) == mrg["sales_rows"]
