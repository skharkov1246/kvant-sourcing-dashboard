"""Три пакета писем не должны пересекаться и не должны оставлять дыру.

Замер 18.09.2026 вскрыл, что самый большой класс строк не попадал ни в один
пакет: у ship_rfq условие «есть найденный склад», у volume_letters — «есть цена
без остатка числом», а строки, где известен только АДРЕС, не подходили ни туда,
ни туда. Их оказалось 522 на 8 629 058 USD — больше, чем в двух других пакетах
вместе. Дыра между двумя правильными условиями опаснее ошибки в одном из них:
каждое по отдельности верно, и поэтому её никто не искал.

Здесь закрыто три свойства: строка не получает два письма из разных пакетов;
письмо наружу не несёт ни нашей вилки, ни суммы, ни имени заказчика; итог
делится на адресные и безадресные нацело.

Корпус для проверок формата придуман; проверки полноты читают наборы целиком.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def ql():
    import quote_letters
    return quote_letters


def pns_of(name: str) -> set:
    q = ql()
    p = ROOT / "gt/data" / name
    if not p.exists():
        return set()
    out = set()
    for L in json.loads(p.read_text(encoding="utf-8")).get("letters") or []:
        for pn in L.get("pns") or []:
            out.add(q.key(pn))
    return out


def test_один_номер_не_получает_двух_писем_из_разных_пакетов():
    mine = pns_of("ship_quote_letters.json")
    for other in ("ship_rfq_letters.json", "ship_volume_letters.json", "ship_lists_rfq.json"):
        dup = mine & pns_of(other)
        assert not dup, f"номера в двух пакетах сразу ({other}): {sorted(dup)[:5]}"


def test_в_письме_нет_ни_вилки_ни_суммы_ни_заказчика():
    q = ql()
    body = q.body([{"pn": "AB-1", "qty": 4, "unit": "шт", "maker": "Acme",
                    "our_exposure": 98765.0, "mail": ""}])
    for forbidden in ("98765", "USD", "вилк", "ЛУКОЙЛ", "экспозиц"):
        assert forbidden not in body, f"в письме наружу оказалось «{forbidden}»"


def test_пять_вопросов_пронумерованы_и_отказ_назван_ответом():
    q = ql()
    body = q.body([{"pn": "AB-1", "qty": 1, "unit": "шт", "maker": "", "our_exposure": 0,
                    "mail": ""}])
    for n in ("1)", "2)", "3)", "4)", "5)"):
        assert n in body, n
    assert "«нет» — это тоже ответ" in body, "отказ обязан быть назван полноценным ответом"


def test_итог_делится_на_адресные_и_безадресные_нацело():
    q = ql()
    d = q.build()
    assert d["rows_with_address"] + d["rows_without_address"] == d["rows_total"]
    assert abs(d["usd_with_address"] + d["usd_without_address"] - d["usd_total"]) < 1.0
    assert len(d["rows_no_address_list"]) == d["rows_without_address"]
    assert sum(L["rows"] for L in d["letters"]) == d["rows_with_address"]


def test_имя_изготовителя_не_берётся_из_прозы_разбора():
    q = ql()
    proza = "ДОГАДКА ПО КЛАССУ ОПРОВЕРГНУТА: в нашей разметке стояло «Pall/Donaldson»"
    assert q.short_maker(proza, "Jenbacher/INNIO") == "Jenbacher/INNIO"


def test_адресат_опубликовавший_заявку_помечен():
    q = ql()
    d = q.build()
    doms = q.leak_domains()
    if not doms:
        return
    for L in d["letters"]:
        dom = L["to"].split("@")[-1].lower()
        if any(dom == k or dom.endswith("." + k) for k in doms):
            assert L.get("warning"), f"{L['to']} без пометки об утечке"
