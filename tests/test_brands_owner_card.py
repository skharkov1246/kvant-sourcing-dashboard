"""Карточка /brands: бренд машины → компания-владелец (library/brands.владение).

Проверяется поведение снимка, по которому страница пишет строки «Входит в …
с …», «Прежде: … до …» и «Бренды группы: …»:
  · бренд с владельцем получает own.o с годом, источником и ссылкой на карточку
    владельца — только если такая карточка есть в снимке;
  · владелец получает список брендов группы (и через промежуточного владельца);
  · бывший владелец — в own.was с годом «до»;
  · бренд без владения поля own не получает (строки на странице нет);
  · ключ словаря, отличный от ключа справочника (brand_key), сводится к записи
    справочника, а ключи и сведение брендов не меняются.

Корпус придуман (CLAUDE.md, правило 18): марки и холдинги сочинены.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from library import brand_owner, brands

ИСТ = "https://example.invalid/deal"

РЯДЫ = {"types": ["насос"], "brands": [
    {"brand_key": "velmora", "name": "Velmora Turbines", "aliases": ["Velmora"], "role": "бренд",
     "owner": "kordex", "owner_since": 1994, "owner_source": ИСТ,
     "owner_history": [{"owner": "Stavin Holding", "role": "бывший владелец", "owner_since": 1971,
                        "owner_until": 1994, "owner_source": ИСТ}]},
    {"brand_key": "pranto", "name": "Pranto Pumps", "aliases": ["Pranto"], "role": "бренд",
     "owner": "lumeq", "owner_since": 2011, "owner_source": ИСТ,
     "series": [{"id": "pranto-x", "series": "X"}]},
    {"brand_key": "kordex", "name": "Kordex", "aliases": ["Kordex"], "role": "бренд",
     "series": [{"id": "kordex-r", "series": "R"}]},
    {"brand_key": "tirsa", "name": "Tirsa", "aliases": ["Tirsa"], "role": "бренд",
     "owner_history": [{"owner": "kordex", "role": "бывший владелец", "owner_until": 2019,
                        "owner_source": ИСТ}]},
    {"brand_key": "ombra", "name": "Ombra", "aliases": ["Ombra"], "role": "бренд"},
], "owners": [
    {"brand_key": "lumeq", "name": "Lumeq Group", "role": "владелец",
     "owner": "kordex", "owner_since": 2016, "owner_source": ИСТ},
]}

# Ключ словаря «velmoraenergy» сведён со справочником полем brand_key.
СЛОВАРЬ = {"records": [
    {"oem_key": "velmoraenergy", "name": "Velmora Energy", "brand_key": "velmora",
     "spellings": [{"spelling": "Velmora Energy", "where": "выдумка"}]},
    {"oem_key": "pranto", "name": "Pranto", "spellings": [{"spelling": "Pranto", "where": "выдумка"}]},
    {"oem_key": "kordex", "name": "Kordex", "spellings": [{"spelling": "Kordex", "where": "выдумка"}]},
    {"oem_key": "tirsa", "name": "Tirsa", "spellings": [{"spelling": "Tirsa", "where": "выдумка"}]},
    {"oem_key": "ombra", "name": "Ombra", "spellings": [{"spelling": "Ombra", "where": "выдумка"}]},
    {"oem_key": "nulvo", "name": "Nulvo", "spellings": [{"spelling": "Nulvo", "where": "выдумка"}]},
]}


def _коды(ключи=("velmoraenergy", "pranto", "kordex", "tirsa", "ombra", "nulvo")):
    return {"brands": [{"brand_key": k, "brand": k, "codes_any": 10 + i} for i, k in enumerate(ключи)]}


def _сводка(коды=None, ряды=РЯДЫ, **kw):
    return brands.собрать(коды or _коды(), {}, словарь=СЛОВАРЬ, ряды=ряды,
                          собран="2026-09-25T00:00:00Z", **kw)[brands.КЛЮЧ]


def _бренд(с, k):
    return next(b for b in с["brands"] if b["k"] == k)


def test_бренд_с_владельцем():
    """«Входит в Kordex с 1994»: ссылка на карточку владельца и источник связи.
    Ключ словаря velmoraenergy сведён со справочником полем brand_key."""
    o = _бренд(_сводка(), "velmoraenergy")["own"]["o"]
    assert o == {"name": "Kordex", "c": "kordex", "since": 1994, "src": ИСТ}


def test_бывший_владелец():
    """«Прежде: Stavin Holding с 1971 до 1994» — владелец назван именем, ссылки нет."""
    с = _сводка()
    assert _бренд(с, "velmoraenergy")["own"]["was"] == [
        {"name": "Stavin Holding", "since": 1971, "until": 1994, "src": ИСТ}]
    # бывший владелец с карточкой в снимке — ссылкой
    tirsa = _бренд(с, "tirsa")["own"]
    assert tirsa["was"] == [{"name": "Kordex", "c": "kordex", "until": 2019, "src": ИСТ}]
    assert "o" not in tirsa


def test_владелец_с_группой():
    """У владельца — бренды группы, и через промежуточного владельца (Lumeq)."""
    группа = _бренд(_сводка(), "kordex")["own"]["group"]
    по_имени = {x["name"]: x for x in группа}
    assert по_имени["Velmora Turbines"] == {"name": "Velmora Turbines", "c": "velmoraenergy", "since": 1994}
    assert по_имени["Pranto Pumps"] == {"name": "Pranto Pumps", "c": "pranto", "since": 2011,
                                        "via": "Lumeq Group"}
    # Lumeq — холдинг без карточки в снимке: в группе именем, без ссылки
    assert "c" not in по_имени["Lumeq Group"]
    # бывший бренд (Tirsa) в группу не входит
    assert "Tirsa" not in по_имени


def test_цепочка_вверх():
    """Pranto → Lumeq Group → Kordex: нынешний владелец и выше по цепочке."""
    own = _бренд(_сводка(), "pranto")["own"]
    assert own["o"]["name"] == "Lumeq Group" and "c" not in own["o"]
    assert own["up"] == [{"name": "Kordex", "c": "kordex"}]


def test_бренд_без_владельца_без_строки():
    """Нет владения — нет поля own: страница строку не пишет."""
    с = _сводка()
    assert "own" not in _бренд(с, "ombra")       # запись справочника без владельца
    assert "own" not in _бренд(с, "nulvo")       # бренда нет в справочнике
    assert brands.итоги_владения(с)["cards"] == 4


def test_ссылка_только_на_карточку_снимка():
    """Карточки владельца нет в снимке — владелец именем, без ссылки."""
    с = _сводка(_коды(("velmoraenergy", "pranto")))
    o = _бренд(с, "velmoraenergy")["own"]["o"]
    assert o["name"] == "Kordex" and "c" not in o
    ключи = {b["k"] for b in с["brands"]}
    for b in с["brands"]:
        own = b.get("own") or {}
        for x in ([own["o"]] if "o" in own else []) + own.get("up", []) + own.get("was", []) + own.get("group", []):
            assert x.get("c") is None or x["c"] in ключи


def test_ключи_и_сведение_не_меняются():
    """Владение — только новое поле own: ключи, имена и облако те же, что без него."""
    с = _сводка()
    без = _сводка(ряды={"types": [], "brands": []})
    assert [b["k"] for b in с["brands"]] == [b["k"] for b in без["brands"]]
    assert [b["name"] for b in с["brands"]] == [b["name"] for b in без["brands"]]
    for b in без["brands"]:
        assert "own" not in b


def test_реестр_без_brand_key_и_карта_из_файла():
    """Словарь реестра базы не знает brand_key: публикатор передаёт карту словаря-файла."""
    реестровый = copy.deepcopy(СЛОВАРЬ)
    for r in реестровый["records"]:
        r.pop("brand_key", None)
    с = brands.собрать(_коды(), {}, словарь=реестровый, ряды=РЯДЫ,
                       ключи_рядов=brands.карта_рядов_словаря(СЛОВАРЬ))[brands.КЛЮЧ]
    assert _бренд(с, "velmoraenergy")["own"]["o"]["name"] == "Kordex"
    без_карты = brands.собрать(_коды(), {}, словарь=реестровый, ряды=РЯДЫ)[brands.КЛЮЧ]
    assert "own" not in _бренд(без_карты, "velmoraenergy")


def test_владелец_своим_ключом_сильнее_сведённого():
    """Две карточки на одну запись справочника: ссылка — на ту, чей ключ совпадает."""
    словарь = copy.deepcopy(СЛОВАРЬ)
    словарь["records"].append({"oem_key": "akordexgroup", "name": "Kordex Group", "brand_key": "kordex",
                               "spellings": [{"spelling": "Kordex Group", "where": "выдумка"}]})
    с = brands.собрать(_коды(("akordexgroup", "velmoraenergy", "kordex")), {}, словарь=словарь,
                       ряды=РЯДЫ)[brands.КЛЮЧ]
    assert _бренд(с, "velmoraenergy")["own"]["o"]["c"] == "kordex"


# ── Ключ brands:owners:v1 для карточки бренда на /p ──────────────────────────

def test_ключ_владения_тот_же_own_по_ключу_карточки():
    """{ключ бренда: own} — ровно поле own карточки снимка, тем же ключом k
    (им же /p адресует бренд), и только бренды с владением."""
    снимки = brands.собрать(_коды(), {}, словарь=СЛОВАРЬ, ряды=РЯДЫ, собран="2026-09-25T00:00:00Z")
    сводка, ключ = снимки[brands.КЛЮЧ], снимки[brands.КЛЮЧ_ВЛАДЕНИЯ]
    assert ключ["version"] == 1 and ключ["published_at"] == "2026-09-25T00:00:00Z"
    assert ключ["brands"] == {b["k"]: b["own"] for b in сводка["brands"] if b.get("own")}
    assert set(ключ["brands"]) == {"velmoraenergy", "pranto", "kordex", "tirsa"}
    # Ссылки ключа — на ключи карточек снимка, то есть на /p#brand=<ключ>.
    ключи = {b["k"] for b in сводка["brands"]}
    for own in ключ["brands"].values():
        for x in ([own["o"]] if "o" in own else []) + own.get("was", []) + own.get("group", []):
            assert x.get("c") is None or x["c"] in ключи


def test_ключ_владения_без_справочника_пустой():
    """Нет справочника рядов — ключ есть, но пустой; одна пометка role — не владение."""
    снимки = brands.собрать(_коды(), {}, словарь=СЛОВАРЬ, ряды={"types": [], "brands": []})
    assert снимки[brands.КЛЮЧ_ВЛАДЕНИЯ] == {"version": 1, "brands": {}}
    assert not brands.есть_владение({"role": "владелец"})
    assert brands.есть_владение({"group": [{"name": "X"}]})


def test_ключ_владения_в_закрытом_списке_и_у_воркера():
    """Ключ — в списке публикатора и тем же именем и пределом в воркере."""
    assert brands.КЛЮЧ_ВЛАДЕНИЯ == "brands:owners:v1" and brands.КЛЮЧ_ВЛАДЕНИЯ in brands.ВСЕ_КЛЮЧИ
    текст = (Path(__file__).resolve().parents[1] / "public" / "_worker.js").read_text(encoding="utf-8")
    assert f'const BRANDS_OWNERS_KEY = "{brands.КЛЮЧ_ВЛАДЕНИЯ}"' in текст
    assert f"const BRANDS_OWNERS_MAX_BYTES = {brands.ПРЕДЕЛ_ВЛАДЕНИЯ // 1024} * 1024;" in текст


def test_ключ_владения_на_настоящем_справочнике_в_пределе():
    """Верхняя граница размера: карточка на КАЖДУЮ запись справочника рядов и
    владельцев (в живом снимке их не больше) — ключ всё равно в пределе."""
    ряды = brands.читать_файл(brands.ФАЙЛ_РЯДОВ)
    ключи = sorted(brand_owner.записи(ряды))
    снимки = brands.собрать(
        {"brands": [{"brand_key": k, "brand": k, "codes_any": 1} for k in ключи]}, {},
        словарь={"records": [{"oem_key": k, "name": k, "spellings": [{"spelling": k, "where": "проверка"}]}
                             for k in ключи]},
        ряды=ряды, собран="2026-09-25T00:00:00Z")
    сырой = json.dumps(снимки[brands.КЛЮЧ_ВЛАДЕНИЯ], ensure_ascii=False, separators=(",", ":")).encode()
    assert снимки[brands.КЛЮЧ_ВЛАДЕНИЯ]["brands"], "в справочнике рядов нет ни одного владения"
    assert len(сырой) <= brands.ПРЕДЕЛ_ВЛАДЕНИЯ, len(сырой)
