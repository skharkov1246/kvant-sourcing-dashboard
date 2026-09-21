"""Адрес в письме наружу: приёмник обязан отсекать догадку.

Неверный адрес хуже отсутствия адреса: письмо уходит в никуда, а строка при этом
считается закрытой — ошибка прячется. Поэтому приёмник адресов написан по тем же
правилам, что приёмник цен, и каждая его проверка отвечает случившемуся:

  * почта без названной страницы — это «parts@домен», сочинённое по образцу;
  * почта, прочитанная на посторонней странице, — «родовой адрес вместо адреса
    по детали»: разведка нашла автомобильный послепродажный канал DENSO для
    свечи промышленного газового двигателя;
  * запись без почты не отсекается: форма и телефон — тоже точка входа, а
    «почты нет вовсе» — измеренный результат.

Корпус придуман, а не взят из выдачи разведки.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def mc():
    import mc_merge
    return mc_merge


def good() -> dict:
    return {"maker": "Придуманный завод", "legal_name": "Pridumanny Zavod GmbH",
            "email": "spares@pridumanny.example", "email_kind": "служба запчастей",
            "form_url": "", "phone": "+49 000 000", "via_distributor": None,
            "distributor_quote": "", "read_on": "https://pridumanny.example/service/spares",
            "note": "прочитано на странице службы", "confidence": "высокая"}


def test_хорошая_запись_проходит():
    assert mc().check(good()) == ""


def test_почта_без_названной_страницы_отсекается():
    r = dict(good(), read_on="")
    why = mc().check(r)
    assert "read_on" in why, why


def test_метка_домена_берётся_слева_от_зоны():
    """Сверка «по части после первой точки» отключала проверку во всей зоне .com.

    ОПЛАЧЕНО ТЕСТОМ. «atmus.com» и «fleetguard.com» обе кончаются на «com» —
    значит «совпадают», и любая почта в .com проходила бы на любой странице
    в .com. Отказ по DENSO прошёл тогда случайно: там различались зоны.
    """
    m = mc()
    assert m.label("atmus.com") == "atmus"
    assert m.label("eu.denso.com") == "denso"
    assert m.label("kempstoncontrols.co.uk") == "kempstoncontrols"
    assert m.label("atmus.com") != m.label("fleetguard.com")


def test_марка_и_её_владелец_принимаются_когда_связь_названа():
    """Fleetguard — марка Atmus Filtration Technologies, и почта службы там на
    atmus.com. Адрес настоящий, но связь обязана быть записана, иначе это
    неотличимо от почты постороннего домена."""
    m = mc()
    r = dict(good(), maker="Fleetguard", email="filtration.support.intl@atmus.example",
             legal_name="Atmus Filtration Technologies",
             read_on="https://fleetguard.example/contactsupport")
    assert m.check(r) == ""
    assert "связь не названа" in m.check(dict(r, legal_name="", note=""))
    # объяснение годится и в разборе: разведка пишет связь туда, с цитатой
    assert m.check(dict(r, legal_name="",
                        note="почта перешла на владельца марки: Atmus Filtration")) == ""


def test_почта_с_посторонней_страницы_отсекается():
    r = dict(good(), email="reception@eu.other.example",
             read_on="https://pridumanny.example/service/spares")
    why = mc().check(r)
    assert "связь не названа" in why, why


def test_почта_с_страницы_дистрибьютора_проходит():
    """Изготовитель может продавать только через дистрибьютора — тогда адрес
    берётся у него, и это названо прямо, а не подставлено молча."""
    r = dict(good(), email="sales@dealer.example",
             read_on="https://dealer.example/contacts",
             via_distributor="Dealer Example Ltd",
             distributor_quote="we offer our products through authorized partners")
    assert mc().check(r) == ""


def test_запись_без_почты_но_с_формой_принимается():
    r = dict(good(), email=None, read_on="", form_url="https://pridumanny.example/contact")
    assert mc().check(r) == ""


def test_пустая_запись_не_принимается():
    r = dict(good(), email=None, form_url="", phone="", read_on="")
    assert "записывать нечего" in mc().check(r)


def test_круговой_источник_и_выдача_поиска_отсекаются():
    m = mc()
    assert "круговой" in m.check(dict(good(), note="взято из kvant-sourcing-dashboard"))
    assert "поисковой машины" in m.check(
        dict(good(), read_on="https://www.google.com/search?q=spares"))


def test_один_изготовитель_дважды_берётся_с_почтой(tmp_path):
    """Две разведки могли наткнуться на одного изготовителя: запись с почтой
    важнее записи с одной формой, иначе адрес теряется из-за порядка файлов."""
    m = mc()
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"rows": [dict(good(), email=None, read_on="",
                                           form_url="https://pridumanny.example/contact")]},
                            ensure_ascii=False), encoding="utf-8")
    b.write_text(json.dumps({"rows": [good()]}, ensure_ascii=False), encoding="utf-8")
    r = m.merge([a, b], dry=True)
    assert len(r["took"]) == 1
    assert r["took"][0]["email"] == "spares@pridumanny.example"
