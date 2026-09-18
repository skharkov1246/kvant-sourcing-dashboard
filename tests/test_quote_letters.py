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


def test_итог_делится_на_пять_частей_нацело():
    """Пять путей: продавцу, изготовителю, через форму, каналом продавца, никак.

    Учёт ломался дважды подряд, и оба раза на добавлении пути: сначала письма
    изготовителям, потом обращения через форму. Каждый раз строки уходили из
    «без адреса», а сумма частей перестала сходиться с целым — то есть одна из
    категорий молча теряла строки. Форма при этом не «адреса нет»: там ABB
    (39 строк) и Rockwell (342 600 USD).
    """
    q = ql()
    d = q.build()
    assert (d["rows_with_address"] + d["rows_to_maker"] + d["rows_to_form"]
            + d["rows_to_seller_channel"] + d["rows_without_address"]) == d["rows_total"]
    assert len(d["seller_tasks"]) == d["rows_to_seller_channel"]
    assert sum(t["rows"] for t in d["form_tasks"]) == d["rows_to_form"]
    assert len(d["rows_no_address_list"]) == d["rows_without_address"]
    assert sum(L["rows"] for L in d["letters"]) == d["rows_with_address"] + d["rows_to_maker"]
    by_kind = {}
    for L in d["letters"]:
        by_kind[L["kind"]] = by_kind.get(L["kind"], 0) + L["rows"]
    assert by_kind.get("продавцу", 0) == d["rows_with_address"]
    assert by_kind.get("изготовителю", 0) == d["rows_to_maker"]


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


def test_пояснение_в_скобках_не_делает_изготовителя_другим():
    """«Fleetguard (Cummins Filtration)» и «Fleetguard» — один изготовитель.

    Без этого три строки Fleetguard на 282 561 USD не нашли своего адреса, хотя
    он был найден и лежал в наборе. А «A / B» остаётся раздельным: «Drillmec
    S.p.A. / Oleobi S.r.l.» — ДВА изготовителя с разными адресами, и склейка
    отправила бы письмо не туда.
    """
    q = ql()
    assert q.maker_key("Fleetguard (Cummins Filtration)") == q.maker_key("Fleetguard")
    assert q.maker_key("Siemens Energy (чертёж завода Линкольн)") == q.maker_key("Siemens Energy")
    assert q.maker_key("Drillmec") != q.maker_key("Drillmec S.p.A. / Oleobi S.r.l.")


def test_продавец_без_почты_это_не_отсутствие_адреса(tmp_path, monkeypatch):
    """Прочитанная страница или телефон — канал, а не пустая графа.

    Добавлено 18.09.2026. Замер: у 48 строк почты продавца нет, но у 25 разбор
    прочитал страницу и ещё у 6 — телефон, всего на 122 494 USD. Пока они
    считались безадресными, документ называл работой «найти адрес» — при том
    что адрес был найден и записан. Корпус придуман; обе формы записи взяты с
    живых строк: телефон дистрибьютора и карточка товара у торговца.
    """
    q = ql()
    ask = {"rows": [{"pn": "ZZ-4100", "qty": 3, "usd_lo": 10, "usd_hi": 20, "unit": "шт"},
                    {"pn": "ZZ-4200", "qty": 5, "usd_lo": 10, "usd_hi": 20, "unit": "шт"},
                    {"pn": "ZZ-4300", "qty": 7, "usd_lo": 10, "usd_hi": 20, "unit": "шт"}]}
    rv = {"rows": [
        {"pn": "ZZ-4100", "channel": "торговец класса",
         "contacts": "Придуманный поставщик: +1-000-555-0100, склада не называет"},
        {"pn": "ZZ-4200", "channel": "витрина",
         "contacts": "карточка по номеру https://example-shop.test/p/ZZ-4200"},
        {"pn": "ZZ-4300", "channel": "класс изделий",
         "contacts": "адреса нет ни по детали, ни по классу"}]}
    a, r = tmp_path / "ask.json", tmp_path / "rv.json"
    a.write_text(json.dumps(ask, ensure_ascii=False), encoding="utf-8")
    r.write_text(json.dumps(rv, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(q, "ASK", a)
    monkeypatch.setattr(q, "RV", r)
    monkeypatch.setattr(q, "OTHER_SETS", ())
    with_mail, without = q.candidates()
    assert not with_mail, "почты в корпусе нет ни у кого"
    by = {x["pn"]: x for x in without}
    assert by["ZZ-4100"]["seller_tel"] == "+1-000-555-0100"
    assert by["ZZ-4200"]["seller_url"] == "https://example-shop.test/p/ZZ-4200"
    assert not by["ZZ-4300"]["seller_url"] and not by["ZZ-4300"]["seller_tel"], (
        "по этой строке спрашивать действительно некуда — она обязана остаться "
        "в безадресном остатке")


def test_почта_по_домену_не_додумывается(tmp_path, monkeypatch):
    """Из страницы продавца адрес почты не выводится.

    Иначе письмо уйдёт на sales@ придуманного домена, и продавец либо не
    получит его, либо получит чужой. Правило то же, что в пакете писем об
    остатке: домен не додумывается.
    """
    q = ql()
    ask = {"rows": [{"pn": "ZZ-4400", "qty": 2, "usd_lo": 10, "usd_hi": 20, "unit": "шт"}]}
    rv = {"rows": [{"pn": "ZZ-4400", "channel": "витрина",
                    "contacts": "https://example-shop.test/p/ZZ-4400"}]}
    a, r = tmp_path / "ask.json", tmp_path / "rv.json"
    a.write_text(json.dumps(ask, ensure_ascii=False), encoding="utf-8")
    r.write_text(json.dumps(rv, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(q, "ASK", a)
    monkeypatch.setattr(q, "RV", r)
    monkeypatch.setattr(q, "OTHER_SETS", ())
    with_mail, without = q.candidates()
    assert not with_mail
    assert without[0]["mail"] == ""
