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


def test_письмо_изготовителю_спрашивает_расшифровку_а_не_только_цену():
    """У изготовителя главный вопрос другой.

    По внутренним обозначениям (SP1xxxxx, CT9xxxx, RM13xxx у Siemens, чертёжные
    позиции Bornemann) в открытом доступе нет ни одной цены — это установлено по
    шести перечням. Пока номер не переведён в коммерческий, цена недостижима ни
    у одного продавца, поэтому письмо изготовителю просит перевод, а не только
    прейскурант.
    """
    q = ql()
    body = q.maker_body("Siemens Energy", [{"pn": "SP106916", "qty": 2, "unit": "шт",
                                            "our_exposure": 100.0}])
    assert "коммерческий номер" in body
    assert "авторизованный канал" in body
    for forbidden in ("USD", "вилк", "ЛУКОЙЛ", "экспозиц"):
        assert forbidden not in body


def test_адрес_изготовителя_без_прочитанной_страницы_не_берётся(tmp_path, monkeypatch):
    """Адрес вида «parts@домен» без названной страницы — догадка.

    То же правило, что и для цен: значение без названного происхождения в дело
    не идёт. Письмо по сочинённому адресу уходит в никуда, а строка при этом
    считается закрытой — то есть ошибка ещё и прячется.
    """
    q = ql()
    f = tmp_path / "mc.json"
    f.write_text(json.dumps({"rows": [
        {"maker": "Хорошая", "email": "spares@example.com", "read_on": "https://example.com/service"},
        {"maker": "Плохая", "email": "parts@example.org", "read_on": ""},
        {"maker": "Безадресная", "email": None, "read_on": "https://example.org/contact"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(q, "MAKER_CONTACTS", f)
    got = q.maker_contacts()
    assert set(got) == {q.maker_key("Хорошая")}, got


def test_ключ_изготовителя_сводит_формы_имени_но_не_склеивает_разных():
    q = ql()
    assert q.maker_key("Drillmec S.p.A.") == q.maker_key("drillmec spa")
    assert q.maker_key("Drillmec") != q.maker_key("Drillmec S.p.A. / Oleobi S.r.l.")
