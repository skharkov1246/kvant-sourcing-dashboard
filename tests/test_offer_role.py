"""Роль предложения (library/offer_role.py) на выдуманном корпусе.

Бренды и компании придуманы (CLAUDE.md, правило 18): Zentrix и Polarmax — марки
холдинга Borealis, Kvarcton — сама по себе, Service Pumps — бренд, в чьём имени
стоит признак трейдера. Мутации — внизу: каждое звено правила снимается, и
хотя бы один случай корпуса обязан поменять ответ.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import offer_role as orl  # noqa: E402

СЛОВАРЬ = {"records": [
    {"oem_key": "zentrix", "name": "Zentrix", "kind": "бренд",
     "spellings": [{"spelling": "Zentrix GmbH"}, {"spelling": "ZENTRIX"}]},
    {"oem_key": "kvarcton", "name": "Kvarcton", "kind": "бренд", "spellings": [{"spelling": "Kvarcton AB"}]},
    {"oem_key": "polarmax", "name": "Polarmax", "kind": "бренд", "spellings": []},
    {"oem_key": "servicepumps", "name": "Service Pumps", "kind": "бренд", "spellings": []},
]}
РЯДЫ = {
    "brands": [
        {"brand_key": "zentrix", "name": "Zentrix", "role": "бренд", "aliases": ["Zentrix"],
         "context_aliases": ["ZX"], "owner": "borealis", "owner_source": "https://example.org/z"},
        {"brand_key": "polarmax", "name": "Polarmax", "role": "бренд", "aliases": ["Polarmax"],
         "owner": "borealis", "owner_source": "https://example.org/p"},
        {"brand_key": "kvarcton", "name": "Kvarcton", "role": "бренд", "aliases": ["Kvarcton"]},
    ],
    "owners": [{"brand_key": "borealis", "name": "Borealis Holding", "role": "владелец"}],
}
РАЗВЕДКА = [{"oem_key": "zentrix", "dealers": [
    {"company": "Ромашка Технолоджи", "role": "официальный дилер: продажи и сервис"},
    {"company": "Zentrix Nordic AB", "role": "дочерняя компания изготовителя"},
    {"company": "Лютик Инжиниринг", "role": "изготовитель неоригинальных аналогов"},
], "sub_suppliers": [{"company": "Kvarcton", "component": "подшипник"},
                     {"company": "Василёк Литьё", "component": "корпус"}]}]


@pytest.fixture(scope="module")
def р():
    return orl.собрать(СЛОВАРЬ, РЯДЫ, РАЗВЕДКА)


def роль(р, компания, бренд, **кв):
    return orl.роль_предложения(компания, бренд, реестр=р, **кв)


# (компания, бренд позиции, доп. поля, роль, причина)
КОРПУС = [
    # Сам бренд и его дочки — прямое.
    ("Zentrix GmbH", "Zentrix", {}, orl.ПРЯМОЕ, "имя: тот же бренд"),
    ("ООО «Зентрикс»", "Zentrix", {}, orl.ПРЯМОЕ, "транслит: тот же бренд"),
    ("Zentrix Rus LLC", "ZENTRIX", {}, orl.ПРЯМОЕ, "имя: тот же бренд"),
    ("ООО Зентрикс Россия", "Zentrix", {}, orl.ПРЯМОЕ, "транслит: тот же бренд"),
    ("Zentrix (Russia)", "Zentrix", {}, orl.ПРЯМОЕ, "имя: тот же бренд"),
    ("Кварктон", "Kvarcton", {}, orl.ПРЯМОЕ, "транслит: тот же бренд"),
    ("Zentrix / Зентрикс", "Zentrix", {}, orl.ПРЯМОЕ, "транслит: тот же бренд"),
    ("Borealis Holding AG", "Zentrix", {}, orl.ПРЯМОЕ, "имя: владелец бренда"),
    ("Zentrix GmbH", "Borealis", {}, orl.ПРЯМОЕ, "имя: дочерняя бренда"),
    ("Zentrix Nordic AB", "Zentrix", {}, orl.ПРЯМОЕ, "разведка: тот же бренд"),
    ("ООО Альфа", "Zentrix", {"домены": ["https://www.zentrix.ru/"]}, orl.ПРЯМОЕ, "домен: тот же бренд"),
    ("Zentrix Service", "Zentrix", {"домены": ["sales@zentrix.co.uk"]}, orl.ПРЯМОЕ, "домен: тот же бренд"),
    ("ООО Альфа", "Zentrix", {"инн": ["7700000001"], "карта_инн": {"7700000001": "zentrix"}},
     orl.ПРЯМОЕ, "инн: тот же бренд"),
    ("Service Pumps Ltd", "Service Pumps", {}, orl.ПРЯМОЕ, "имя: тот же бренд"),
    ("ООО Ромашкамаш", "Ромашкамаш", {}, orl.ПРЯМОЕ, "имя: тот же бренд"),
    # Третьи лица с признаком трейдера в имени — трейдер.
    ("Zentrix Service", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("ООО «Зентрикс-Сервис»", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("Zentrix Trading FZE", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("Zentrix-Trade", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("Zentrix Parts Ltd", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("ТД Зентрикс", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("ООО Торговый дом «Зентрикс»", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("Зентрикс Снаб", "Zentrix", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("Service Pumps Trading", "Service Pumps", {}, orl.ТРЕЙДЕР, "признак трейдера в имени"),
    ("ООО Альфа", "Zentrix", {"домены": ["zentrix-parts.ru"]}, orl.ТРЕЙДЕР, "имя не бренд"),
    ("ООО Альфа", "Zentrix", {"домены": ["zentrix@gmail.com"]}, orl.ТРЕЙДЕР, "имя не бренд"),
    # Бренд компании и бренд позиции разные — трейдер.
    ("Kvarcton AB", "Zentrix", {}, orl.ТРЕЙДЕР, "субпоставщик бренда по разведке"),
    ("Kvarcton AB", "Polarmax", {}, orl.ТРЕЙДЕР, "компания — другой бренд"),
    ("Polarmax", "Zentrix", {}, orl.ТРЕЙДЕР, "другой бренд той же группы"),
    ("Ромашка Технолоджи", "Zentrix", {}, orl.ТРЕЙДЕР, "дилер бренда по разведке"),
    ("Лютик Инжиниринг", "Zentrix", {}, orl.ТРЕЙДЕР, "дилер бренда по разведке"),
    ("Василёк Литьё", "Zentrix", {}, orl.ТРЕЙДЕР, "субпоставщик бренда по разведке"),
    ("ООО Ромашка", "Zentrix", {}, orl.ТРЕЙДЕР, "имя не бренд"),
    ("ООО Ромашка (Zentrix)", "Zentrix", {}, orl.ТРЕЙДЕР, "имя не бренд"),
    ("ZentrixMaster", "Zentrix", {}, orl.ТРЕЙДЕР, "имя не бренд"),
    # Бренда позиции нет или нет компании — не определено.
    ("Zentrix GmbH", None, {}, orl.НЕ_ОПРЕДЕЛЕНО, "нет бренда позиции"),
    ("Zentrix GmbH", "", {}, orl.НЕ_ОПРЕДЕЛЕНО, "нет бренда позиции"),
    ("Zentrix GmbH", "не указан", {}, orl.НЕ_ОПРЕДЕЛЕНО, "нет бренда позиции"),
    ("Zentrix GmbH", ["нет", "прочие"], {}, orl.НЕ_ОПРЕДЕЛЕНО, "нет бренда позиции"),
    ("", "Zentrix", {}, orl.НЕ_ОПРЕДЕЛЕНО, "нет компании"),
    (None, "Zentrix", {}, orl.НЕ_ОПРЕДЕЛЕНО, "нет компании"),
]


@pytest.mark.parametrize("компания,бренд,доп,ожидается,почему", КОРПУС)
def test_корпус(р, компания, бренд, доп, ожидается, почему):
    ответ = роль(р, компания, бренд, **доп)
    assert (ответ["role"], ответ["why"]) == (ожидается, почему), ответ


def test_несколько_написаний_бренда(р):
    """Бренд позиции — список: прямое, если компания — любой из брендов."""
    assert роль(р, "Kvarcton AB", ["Zentrix", "Kvarcton"])["role"] == orl.ПРЯМОЕ
    assert роль(р, "Kvarcton AB", ["не указан", "Kvarcton"])["role"] == orl.ПРЯМОЕ


def test_контекстное_написание_только_для_позиции(р):
    """«ZX» в поле изготовителя — Zentrix; компания «ZX» бренда не называет."""
    assert orl.бренды_позиции(["ZX"], р) == {"zentrix"}
    assert orl.бренды_имени("ZX", р) == (set(), "")


def test_доказательство_без_имени_компании(р):
    """evidence не несёт имени компании: в журнал замера идут только ключи брендов."""
    for компания, бренд, доп, _, _ in КОРПУС:
        ответ = роль(р, компания, бренд, **доп)
        assert set(ответ) == {"role", "why", "evidence"}
        if компания:
            assert компания not in repr(ответ["evidence"])


def test_каждая_причина_достижима(р):
    """Корпус проходит все причины трейдера, все пути прямого и все причины «не
    определено», кроме спорного имени (его — test_спорное_имя)."""
    бывшие = {(о["role"], о["why"]) for о in (роль(р, к, б, **д) for к, б, д, _, _ in КОРПУС)}
    assert {(orl.ТРЕЙДЕР, п) for п in orl.ПРИЧИНЫ[orl.ТРЕЙДЕР]} <= бывшие
    assert {(orl.НЕ_ОПРЕДЕЛЕНО, п) for п in orl.ПРИЧИНЫ[orl.НЕ_ОПРЕДЕЛЕНО]
            if п != "имя спорно между брендами"} <= бывшие
    for путь in ("инн", "домен", "разведка", "имя", "транслит"):
        assert any(р_ == orl.ПРЯМОЕ and п.startswith(путь + ":") for р_, п in бывшие), путь
    for связь in (orl.ТОТ_ЖЕ, orl.ВЛАДЕЛЕЦ, orl.ДОЧЕРНЯЯ):
        assert any(р_ == orl.ПРЯМОЕ and п.endswith(связь) for р_, п in бывшие), связь


def test_спорное_имя(р):
    """Имя, которое реестр отдаёт двум брендам, одному из которых позиция: не решаем."""
    спорный = orl.собрать(СЛОВАРЬ, РЯДЫ, РАЗВЕДКА, доп_карта=[("zentrixpolarmax", "zentrix"),
                                                            ("zentrixpolarmax", "polarmax")])
    ответ = роль(спорный, "Zentrix Polarmax", "Zentrix")
    assert (ответ["role"], ответ["why"]) == (orl.НЕ_ОПРЕДЕЛЕНО, "имя спорно между брендами")


def test_признаки_трейдера():
    assert orl.признаки_трейдера("ООО «Зентрикс-Сервис»") == ["сервис"]
    assert orl.признаки_трейдера("Zentrix Trading & Parts") == ["trading", "parts"]
    assert orl.признаки_трейдера("ТД Альфа") == ["тд"]
    # Слова, похожие на признак, но не он: «Торгмаш» — да (основа), «Партнёр» — нет.
    assert orl.признаки_трейдера("Партнёр Инжиниринг") == []
    assert orl.признаки_трейдера("Zentrix Tradition") == []
    assert orl.признаки_трейдера("") == []


def test_своя_площадка():
    assert orl.своя_площадка("дочерняя компания изготовителя: продажи и сервис")
    assert orl.своя_площадка("100-процентная дочка изготовителя")
    assert orl.своя_площадка("изготовитель, головная площадка")
    assert orl.своя_площадка("официальное подразделение: продажи")
    assert not orl.своя_площадка("изготовитель неоригинальных уплотнений")
    assert not orl.своя_площадка("изготовитель и продавец аналогов фильтров")
    assert not orl.своя_площадка("официальный дилер")
    assert not orl.своя_площадка("перепродавец: поставки датчиков")


def test_метка_домена():
    assert orl.метка_домена("https://www.zentrix.ru/contacts") == "zentrix"
    assert orl.метка_домена("zentrix.co.uk") == "zentrix"
    assert orl.метка_домена("sales@zentrix.com") == "zentrix"
    assert orl.метка_домена("x@gmail.com") == ""
    assert orl.метка_домена("") == ""


def test_остов():
    assert orl.остов("сименс") == orl.остов("siemens")
    assert orl.остов("катерпиллар") == orl.остов("caterpillar")
    assert orl.транслит("скф") == "skf"


def test_реестр_файлов_на_деле():
    """Реестр из файлов репозитория: случаи ревизии, ради которых модуль заведён."""
    assert orl.роль_предложения("ООО «СКФ»", "SKF")["role"] == orl.ПРЯМОЕ
    assert orl.роль_предложения("ООО Сименс", "Siemens AG")["role"] == orl.ПРЯМОЕ
    assert orl.роль_предложения("СКФ Сервис", "SKF")["why"] == "признак трейдера в имени"
    assert orl.роль_предложения("Caterpillar Inc.", "Solar Turbines")["why"].endswith("владелец бренда")


# ── Мутации: снятое звено меняет ответ хотя бы у одного случая ───────────────

def _ответы(р):
    return [(роль(р, к, б, **д)["role"], роль(р, к, б, **д)["why"]) for к, б, д, _, _ in КОРПУС]


def _ожидаемые():
    return [(ож, п) for _, _, _, ож, п in КОРПУС]


_связать = orl.Реестр._связать


def _без_признаков_бренда(self, *a, **k):
    _связать(self, *a, **k)
    self.признаки_бренда.clear()


МУТАЦИИ = {
    "без признаков трейдера": lambda mp: (mp.setattr(orl, "ПРИЗНАКИ_ЛАТ", frozenset()),
                                          mp.setattr(orl, "ОСНОВЫ_РУС", ()),
                                          mp.setattr(orl, "СЛОВА_РУС", frozenset())),
    "без транслита": lambda mp: mp.setattr(orl, "_ТРАНСЛИТ", {}),
    "без остова": lambda mp: mp.setattr(orl, "ОСТОВ_ОТ", 10 ** 6),
    "без хвоста страны": lambda mp: mp.setattr(orl, "ХВОСТЫ", frozenset()),
    "без цепочки владения": lambda mp: mp.setattr(orl.brand_owner, "цепочка_владельцев", lambda *a, **k: []),
    "без домена": lambda mp: mp.setattr(orl, "метка_домена", lambda d: ""),
    "без разведки своих": lambda mp: mp.setattr(orl, "своя_площадка", lambda r: False),
    "признак бренда не защищает": lambda mp: mp.setattr(orl.Реестр, "_связать", _без_признаков_бренда),
    "части имени без проверки каждой": lambda mp: mp.setattr(orl, "варианты_имени", lambda имя: [
        k for k in orl.brands.ключи_имени(имя)] + [orl.codes_sql.ключ_написания(имя or "")]),
    "метка домена без второго уровня": lambda mp: mp.setattr(orl, "_ВТОРЫЕ", frozenset()),
}


@pytest.mark.parametrize("имя", sorted(МУТАЦИИ))
def test_мутация_ловится(имя, monkeypatch):
    МУТАЦИИ[имя](monkeypatch)
    р = orl.собрать(СЛОВАРЬ, РЯДЫ, РАЗВЕДКА)
    assert _ответы(р) != _ожидаемые(), f"мутация «{имя}» выжила"
