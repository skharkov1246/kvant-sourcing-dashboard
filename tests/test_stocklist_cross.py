"""Сток-лист продавца измеряется, но не перепечатывается.

Репозиторий публичный, а сток-лист — коммерческий документ контрагента. Даже
когда страница открыта, выкладывать её целиком нельзя: набор несёт только счёт
и класс (ask выше потолка нашей вилки, внутри, ниже пола, вилки нет).

Второе, что здесь закреплено, — разбор строки листа. Цена «1.200.00EURO» это
1 200 евро, а не 120 000: точка на этом листе разделяет тысячи, и проверяется
это на самом листе, где рядом стоят «20.00EURO» и «625.00EURO». Ошибка в
разделителе дала бы стократное завышение и вердикт «занижена» на пустом месте.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_stocklist_cross.json"
sys.path.insert(0, str(ROOT / "gt/tools"))
CLASSES = {"ask выше потолка", "ask внутри вилки", "ask ниже пола", "вилки нет"}


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("замера нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_в_наборе_нет_ни_одной_цены():
    text = SRC.read_text(encoding="utf-8") if SRC.exists() else ""
    if not text:
        pytest.skip("замера нет")
    for bad in ('"price"', '"eur"', '"usd_price"', "EURO", "EUR "):
        assert bad not in text, f"в набор попала цена контрагента ({bad})"
    for r in doc()["rows"]:
        assert set(r) <= {"pn", "where", "usd_exposure", "qty_request", "qty_listed",
                          "desc_starts_with_other_pn"}, f'{r["pn"]}: лишнее поле в строке'


def test_разделитель_тысяч_читается_как_на_листе():
    from stocklist_cross import money
    assert money("625.00") == 625.0
    assert money("1.200.00") == 1200.0
    assert money("20.00") == 20.0
    assert money("11.500.00") == 11500.0


def test_строка_листа_разбирается_целиком():
    from stocklist_cross import parse
    got = parse("<p>1060817-10 Element, Fltr, Gas, Pleated, Cyl 150pcs 650.00EURO ea</p>")
    assert len(got) == 1
    r = got[0]
    assert r["pn"] == "1060817-10" and r["qty_listed"] == 150
    assert r["price"] == 650.0 and r["currency"] == "EUR"


def test_класс_из_закрытого_списка_и_счёт_сходится():
    d = doc()
    t = d["totals"]
    assert sum(v["pns"] for v in t["by_class"].values()) == t["matched_pns"] == len(d["rows"])
    for r in d["rows"]:
        assert r["where"] in CLASSES, f'{r["pn"]}: класс «{r["where"]}» не из списка'
    assert abs(sum(v["usd_exposure"] for v in t["by_class"].values())
               - t["matched_exposure"]) < 1.0
    # у строк без вилки экспозиции быть не может: она считается по вилке
    for r in d["rows"]:
        if r["where"] == "вилки нет":
            assert r["usd_exposure"] == 0


def test_кросс_продавца_помечен_а_не_выдан_за_наш_номер():
    d = doc()
    flagged = [r for r in d["rows"] if r["desc_starts_with_other_pn"]]
    assert d["totals"]["desc_starts_with_other_pn"] == len(flagged)
    if flagged:
        assert "кросс" in json.dumps(d, ensure_ascii=False).lower(), (
            "в наборе должно быть сказано, что описание с чужого номера — это кросс продавца")


def test_набор_говорит_чем_он_не_является():
    d = doc()
    txt = (d.get("what_it_is_not") or "").lower()
    assert "одн" in txt and "рынок" in txt, (
        "набор обязан говорить, что ask одного оператора — не рынок: иначе цифру прочитают "
        "как подтверждённую закупку")
