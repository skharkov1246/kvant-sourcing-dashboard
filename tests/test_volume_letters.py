"""Письмо уходит НАРУЖУ — значит в нём не должно быть ничего нашего.

Два случившихся дефекта закрыты здесь.

Первый: имя изготовителя брали из поля разбора real_maker, а там лежит проза с
оговорками. В письмо поставщику пошла бы строка «ДОГАДКА ПО КЛАССУ ОПРОВЕРГНУТА:
в нашей» — наш внутренний вывод, отправленный контрагенту.

Второй: адрес вырезался выражением, которое захватывало точку предложения, и
первый же адресат вышел как «export@aftermarket.express.» — письмо ушло бы в
никуда.

И одно правило по существу: в письме нет ни нашей вилки, ни суммы, ни имени
заказчика. Назвав объём закупки, мы отдаём переговорную позицию.

Корпус придуман, а не взят из набора.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def vl():
    import volume_letters
    return volume_letters


def test_имя_изготовителя_не_берётся_из_прозы_разбора():
    v = vl()
    proza = ("ДОГАДКА ПО КЛАССУ ОПРОВЕРГНУТА: в нашей разметке стояло «Pall/Donaldson» "
             "с основанием «сегмент фильтров»")
    assert v.short_maker(proza, "Jenbacher/INNIO") == "Jenbacher/INNIO"
    assert v.short_maker("Cummins", "Cummins Inc") == "Cummins"
    assert v.short_maker("", "") == ""


def test_адрес_без_хвостовой_точки():
    v = vl()
    got = v.MAIL.findall("Пишите: export@aftermarket.express. Телефон отдельно.")
    assert got == ["export@aftermarket.express"], got
    assert v.MAIL.findall("sales@kempstoncontrols.co.uk,") == ["sales@kempstoncontrols.co.uk"]


def test_в_письме_нет_ни_вилки_ни_суммы_ни_заказчика():
    v = vl()
    body = v.body([{"pn": "AB-1", "qty": 4, "unit": "шт", "maker": "Acme",
                    "our_exposure": 12345.0, "what_it_is": "", "seller_hint": "", "mail": ""}])
    for forbidden in ("12345", "USD", "вилк", "ЛУКОЙЛ", "экспозиц"):
        assert forbidden not in body, f"в письме наружу оказалось «{forbidden}»"
    # четыре вопроса — и ровно они
    for q in ("остаток", "срок поставки", "цена за весь объём", "срок действия цены"):
        assert q in body, q


def test_все_четыре_вопроса_пронумерованы():
    """Ненумерованный список вопросов отвечают выборочно — проверено перепиской."""
    v = vl()
    body = v.body([{"pn": "AB-1", "qty": 1, "unit": "шт", "maker": "", "our_exposure": 0,
                    "what_it_is": "", "seller_hint": "", "mail": ""}])
    for n in ("1)", "2)", "3)", "4)"):
        assert n in body, n


def test_итог_набора_делится_на_адресные_и_безадресные_нацело():
    v = vl()
    d = v.build()
    assert d["rows_with_address"] + d["rows_without_address"] == d["rows_total"]
    assert abs(d["usd_with_address"] + d["usd_without_address"] - d["usd_total"]) < 1.0
    assert len(d["rows_no_address_list"]) == d["rows_without_address"]


def test_адресат_опубликовавший_нашу_заявку_помечен():
    """Писать ему можно, но это решение, а не рассылка: он и так знает наш объём."""
    v = vl()
    d = v.build()
    marked = [L for L in d["letters"] if L.get("warning")]
    for L in marked:
        assert "ОПУБЛИКОВАЛ НАШУ ЗАЯВКУ" in L["warning"]
    doms = v.leak_domains()
    if doms:
        for L in d["letters"]:
            dom = L["to"].split("@")[-1].lower()
            if any(dom == k or dom.endswith("." + k) for k in doms):
                assert L.get("warning"), f"{L['to']} без пометки об утечке"
