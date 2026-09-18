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



def tmp_out(sc, payload):
    """Подменяет файл замера на время проверки и возвращает функцию восстановления."""
    import json
    real = sc.OUT
    keep = real.read_text(encoding="utf-8") if real.exists() else None
    real.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def restore():
        if keep is None:
            real.unlink(missing_ok=True)
        else:
            real.write_text(keep, encoding="utf-8")
    return restore


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("замера нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def sellers() -> dict:
    """Замеры по продавцам. Старая однопродавцовая форма тоже читается."""
    d = doc()
    if "sellers" in d:
        return d["sellers"]
    return {(d.get("totals") or {}).get("seller") or "один продавец":
            {"totals": d.get("totals") or {}, "rows": d.get("rows") or []}}


def all_rows() -> list[dict]:
    return [r for s in sellers().values() for r in (s.get("rows") or [])]


def test_в_наборе_нет_ни_одной_цены():
    text = SRC.read_text(encoding="utf-8") if SRC.exists() else ""
    if not text:
        pytest.skip("замера нет")
    for bad in ('"price"', '"eur"', '"usd_price"', "EURO", "EUR "):
        assert bad not in text, f"в набор попала цена контрагента ({bad})"
    for r in all_rows():
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
    """Счёт сходится ВНУТРИ каждого продавца, а между продавцами не складывается.

    Один номер стоит в нескольких листах, поэтому сумма по всем листам посчитала
    бы его столько раз, сколько продавцов его держат.
    """
    for name, sl in sellers().items():
        t, rows = sl["totals"], sl.get("rows") or []
        assert sum(v["pns"] for v in t["by_class"].values()) == t["matched_pns"] == len(rows), (
            f"{name}: счёт по классам не сходится с числом строк")
        for r in rows:
            assert r["where"] in CLASSES, f'{name} / {r["pn"]}: класс «{r["where"]}» не из списка'
        assert abs(sum(v["usd_exposure"] for v in t["by_class"].values())
                   - t["matched_exposure"]) < 1.0, f"{name}: экспозиция по классам не сходится"
        # у строк без вилки экспозиции быть не может: она считается по вилке
        for r in rows:
            if r["where"] == "вилки нет":
                assert r["usd_exposure"] == 0, f'{name} / {r["pn"]}: вилки нет, а экспозиция есть'


def test_кросс_продавца_помечен_а_не_выдан_за_наш_номер():
    d = doc()
    flagged = []
    for name, sl in sellers().items():
        f = [r for r in (sl.get("rows") or []) if r["desc_starts_with_other_pn"]]
        assert sl["totals"]["desc_starts_with_other_pn"] == len(f), (
            f"{name}: счёт кроссов не сходится со числом помеченных строк")
        flagged += f
    if flagged:
        assert "кросс" in json.dumps(d, ensure_ascii=False).lower(), (
            "в наборе должно быть сказано, что описание с чужого номера — это кросс продавца")


def test_набор_говорит_чем_он_не_является():
    d = doc()
    txt = (d.get("what_it_is_not") or "").lower()
    assert "одн" in txt and "рынок" in txt, (
        "набор обязан говорить, что ask одного оператора — не рынок: иначе цифру прочитают "
        "как подтверждённую закупку")


def test_прогон_по_второму_листу_не_затирает_первый():
    """Замеры живут ПО ПРОДАВЦАМ, и второй прогон не имеет права стереть первый.

    Инструмент писал одного продавца в корень файла, и второй прогон затирал
    его целиком. С шестью листами это потеряло бы пять замеров — ровно та же
    ошибка, что уже была со счётчиками по охватам выгрузки. Здесь закреплено:
    старая однопродавцовая форма переносится в sellers, а новый продавец
    добавляется рядом, не касаясь чужих.
    """
    import sys
    sys.path.insert(0, str(ROOT / "gt/tools"))
    import stocklist_cross as sc

    old = {"updated": "2026-09-18", "source": "Пересечение открытого сток-листа продавца "
           "первый.example с номерами заявки", "totals": {"matched_pns": 7}, "rows": [
               {"pn": "AAA-1", "where": "ask внутри вилки", "usd_exposure": 100.0}]}
    out = tmp_out(sc, old)
    try:
        got = sc.load_out()
        assert "первый.example" in got["sellers"], "старая форма должна перенестись по имени"
        assert got["sellers"]["первый.example"]["totals"]["matched_pns"] == 7
    finally:
        out()


def test_лист_группы_не_считается_вторым_свидетелем():
    """Два листа ОДНОГО оператора не образуют расхождения и не подтверждают друг друга."""
    import sys
    sys.path.insert(0, str(ROOT / "gt/tools"))
    import stocklist_cross as sc

    g = "Одна группа"
    sellers = {
        "vitrina-a.example": {"group": g, "rows": [
            {"pn": "X-1", "where": "ask внутри вилки", "usd_exposure": 50.0}]},
        "vitrina-b.example": {"group": g, "rows": [
            {"pn": "X-1", "where": "ask выше потолка", "usd_exposure": 50.0}]},
    }
    d = sc.disagreements(sellers)
    assert d["pns_on_more_than_one_list"] == 0, (
        "две витрины одной группы — один свидетель, номер не может стоять «более чем на "
        "одном листе»")
    assert d["pns_with_conflicting_class"] == 0

    # а два РАЗНЫХ оператора расхождение дают
    sellers["vitrina-b.example"]["group"] = "Другая группа"
    d2 = sc.disagreements(sellers)
    assert d2["pns_on_more_than_one_list"] == 1
    assert d2["pns_with_conflicting_class"] == 1


def test_раздел_справки_не_исчезает_молча():
    """Если набор есть, раздел о сток-листах обязан быть в справке.

    Оплачено 18.09.2026: инструмент перешёл на слияние по продавцу, поле totals
    в корне файла исчезло, а справка читала именно его — и целый раздел пропал
    из документа БЕЗ ЕДИНОЙ ОШИБКИ. Это тот же класс ошибки, что описан в
    правилах про две вставки в одну таблицу: правится одна сторона, ломается
    другая, и ничто не падает.
    """
    brief = ROOT / "gt/docs/СПРАВКА-НА-ЗАЩИТУ-ЛУКОЙЛ.html"
    if not SRC.exists() or not brief.exists():
        pytest.skip("набора или справки нет")
    if not sellers():
        pytest.skip("в наборе нет ни одного листа")
    t = brief.read_text(encoding="utf-8")
    assert "сток-лист" in t.lower(), "раздел о сток-листах исчез из справки"
    # и он обязан показывать НЕСКОЛЬКО листов, а не один
    for name in list(sellers())[:3]:
        assert name in t, f"лист {name} есть в наборе, но не показан в справке"


def test_в_наборе_сказано_что_замеры_не_складываются():
    d = doc()
    if "sellers" not in d:
        pytest.skip("старая однопродавцовая форма")
    txt = json.dumps(d, ensure_ascii=False).lower()
    assert "не складыва" in txt, (
        "один номер стоит в нескольких листах, и набор обязан сказать, что суммы по "
        "листам не складываются — иначе его сложат")
