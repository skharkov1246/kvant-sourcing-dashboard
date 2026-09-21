"""Письма по строкам без цены и без вилки — gt/tools/gap_letters.py.

Корпус придуман целиком (правило 18).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

gl = pytest.importorskip("gap_letters")

BRANDS = [
    {"brand": "Вымышленные Турбины", "pattern": r"вымтурб|\bVT[0-9]{3}-"},
    {"brand": "Придуманная Автоматика", "pattern": r"придавт|\bPA[0-9]{4}\b"},
]
CONTACTS = [
    {"maker": "Вымышленные", "email": "parts@vymturb.example", "form_url": "None",
     "phone": "", "read_on": "https://vymturb.example/contact", "confidence": "высокая"},
    {"maker": "Вымышленные Турбины", "email": "spares@vymturb.example", "form_url": "None",
     "phone": "+1 000 000 0000", "read_on": "https://vymturb.example/parts",
     "confidence": "средняя"},
    {"maker": "Придуманная Автоматика", "email": "guess@pridavt.example", "form_url": "None",
     "phone": "", "read_on": "догадка по домену", "confidence": "низкая"},
]


def test_brand_taken_from_our_pattern_not_customer_column():
    """Столбец заказчика врёт: бренд определяется нашим шаблоном номера."""
    row = {"pn": "VT101-7", "name": "Клапан придуманный", "man": "ПридАвт"}
    # Столбец заказчика говорит «ПридАвт», а номер — шаблона турбин.
    assert gl.brand_of(row, BRANDS) == "Вымышленные Турбины"


def test_brand_unknown_when_no_pattern_matches():
    assert gl.brand_of({"pn": "ZZ-1", "name": "нечто"}, BRANDS) == ""


def test_address_takes_longest_matching_maker():
    """«Вымышленные» ⊂ «Вымышленные Турбины» — берётся более точное имя."""
    a = gl.address_for("Вымышленные Турбины", CONTACTS)
    assert a["email"] == "spares@vymturb.example"
    assert a["maker"] == "Вымышленные Турбины"


def test_low_confidence_address_gives_no_letter():
    """Догадка вида «parts@домен» письма не порождает."""
    assert gl.address_for("Придуманная Автоматика", CONTACTS) == {}


def test_unrelated_maker_never_matches_brand():
    """«Придуманная Автоматика» не должна ловиться на бренд турбин."""
    a = gl.address_for("Вымышленные Турбины", CONTACTS)
    assert "pridavt" not in json.dumps(a, ensure_ascii=False)


def items():
    return [{"pn": "VT101-7", "qty": 3, "unit": "шт", "name": "Клапан придуманный"},
            {"pn": "VT102-1", "qty": 1, "unit": "шт", "name": "Привод придуманный"}]


def test_letter_carries_numbers_and_quantities():
    t = gl.letter_text("Вымышленные Турбины", {}, items())
    assert "VT101-7 — 3 шт" in t and "VT102-1 — 1 шт" in t
    assert "Позиций: 2" in t


def test_letter_never_carries_price_budget_or_customer():
    """Ни цены, ни бюджета, ни имени конечного заказчика в письме быть не может."""
    t = gl.letter_text("Вымышленные Турбины", {}, items()).lower()
    for forbidden in ("usd", "руб", "бюджет", "цена за", "лукойл", "энергосети"):
        assert forbidden not in t


def test_letter_asks_to_decode_internal_designation():
    """Главный вопрос изготовителю — расшифровка внутреннего обозначения."""
    t = gl.letter_text("Вымышленные Турбины", {}, items())
    assert "внутреннее" in t and "коммерческий номер" in t


def test_measure_sums_to_the_whole(monkeypatch):
    """Разряды — разбиение: письма + без адреса + без бренда + уже в письме = всё."""
    rows = [{"pn": "VT101-7", "qty": 3, "unit": "шт", "name": "к", "man": ""},
            {"pn": "VT101-7", "qty": 2, "unit": "шт", "name": "к", "man": ""},
            {"pn": "PA1234", "qty": 1, "unit": "шт", "name": "а", "man": ""},
            {"pn": "ZZ-9", "qty": 1, "unit": "шт", "name": "н", "man": ""}]
    monkeypatch.setattr(gl, "gap_rows", lambda: (rows, 10))
    monkeypatch.setattr(gl, "brands", lambda: BRANDS)
    monkeypatch.setattr(gl, "contacts", lambda: CONTACTS)
    monkeypatch.setattr(gl, "already_written", lambda: set())
    d = gl.measure()
    assert d["gap_rows"] == 4
    assert (d["rows_in_letters"] + d["rows_without_address"] + d["unbranded_rows"]
            + d["of_them_already_in_a_letter"]) == d["gap_rows"]


def test_same_number_asked_once(monkeypatch):
    """Одна деталь на две машины — в письме одна позиция с суммой количеств."""
    rows = [{"pn": "VT101-7", "qty": 3, "unit": "шт", "name": "к", "man": ""},
            {"pn": "VT101-7", "qty": 2, "unit": "шт", "name": "к", "man": ""}]
    monkeypatch.setattr(gl, "gap_rows", lambda: (rows, 10))
    monkeypatch.setattr(gl, "brands", lambda: BRANDS)
    monkeypatch.setattr(gl, "contacts", lambda: CONTACTS)
    monkeypatch.setattr(gl, "already_written", lambda: set())
    L = gl.measure()["letters"][0]
    assert L["positions"] == 1 and L["rows"] == 2
    assert L["qty_total"] == 5
    assert L["body"].count("VT101-7") == 1


def test_number_already_in_another_package_is_not_asked_twice(monkeypatch):
    rows = [{"pn": "VT101-7", "qty": 3, "unit": "шт", "name": "к", "man": ""}]
    monkeypatch.setattr(gl, "gap_rows", lambda: (rows, 10))
    monkeypatch.setattr(gl, "brands", lambda: BRANDS)
    monkeypatch.setattr(gl, "contacts", lambda: CONTACTS)
    monkeypatch.setattr(gl, "already_written", lambda: {gl.key("VT101-7")})
    d = gl.measure()
    assert d["letters"] == [] and d["of_them_already_in_a_letter"] == 1


def test_live_dataset_letters_have_a_read_address():
    """В живом наборе у каждого письма адрес прочитан, а не додуман."""
    p = ROOT / "gt/data/gap_letters.json"
    if not p.exists():
        pytest.skip("набор ещё не собран")
    d = json.loads(p.read_text(encoding="utf-8"))
    for L in d["letters"]:
        to = L.get("to") or {}
        assert to.get("email") or to.get("form_url")
        assert to.get("confidence") != "низкая"
        assert to.get("read_on")


# --- второй путь атрибуции: столбец заказчика, сведённый с книгой адресов ---

CONTACTS2 = CONTACTS + [
    {"maker": "Придумбур", "email": "sales@pridumbur.example", "form_url": "None",
     "phone": "", "read_on": "https://pridumbur.example/contacts", "confidence": "средняя"},
    {"maker": "ПридумбурХД", "email": "other@pridumburhd.example", "form_url": "None",
     "phone": "", "read_on": "https://pridumburhd.example/c", "confidence": "средняя"},
]


def test_maker_path_matches_longest_name_inside_customer_spelling():
    """«Придумбур» ⊂ «Придумбур С.п.А. / Вымысел» — письмо идёт Придумбуру."""
    row = {"pn": "ZZ-1", "man": "Придумбур С.п.А. / Вымысел ООО"}
    assert gl.maker_of(row, CONTACTS2) == "Придумбур"


def test_maker_path_does_not_match_in_reverse():
    """Заказчик написал «Придумбур», а в книге есть «ПридумбурХД» — это не он."""
    row = {"pn": "ZZ-1", "man": "Придумбур"}
    assert gl.maker_of(row, CONTACTS2) == "Придумбур"


def test_maker_path_refuses_low_confidence():
    row = {"pn": "ZZ-1", "man": "Придуманная Автоматика"}
    assert gl.maker_of(row, CONTACTS2) == ""


def test_maker_path_needs_a_named_maker():
    assert gl.maker_of({"pn": "ZZ-1", "man": ""}, CONTACTS2) == ""


def test_pattern_path_wins_over_customer_column(monkeypatch):
    """Номер нашего шаблона — наше измерение; столбец заказчика ему не перебивает."""
    rows = [{"pn": "VT101-7", "qty": 1, "unit": "шт", "name": "к", "man": "Придумбур"}]
    monkeypatch.setattr(gl, "gap_rows", lambda: (rows, 10))
    monkeypatch.setattr(gl, "brands", lambda: BRANDS)
    monkeypatch.setattr(gl, "contacts", lambda: CONTACTS2)
    monkeypatch.setattr(gl, "already_written", lambda: set())
    L = gl.measure()["letters"][0]
    assert L["brand"] == "Вымышленные Турбины"
    assert L["brand_source"] == "наш шаблон номера"


def test_customer_column_used_only_when_pattern_is_silent(monkeypatch):
    rows = [{"pn": "ZZ-1", "qty": 2, "unit": "шт", "name": "н", "man": "Придумбур С.п.А."}]
    monkeypatch.setattr(gl, "gap_rows", lambda: (rows, 10))
    monkeypatch.setattr(gl, "brands", lambda: BRANDS)
    monkeypatch.setattr(gl, "contacts", lambda: CONTACTS2)
    monkeypatch.setattr(gl, "already_written", lambda: set())
    d = gl.measure()
    assert d["unbranded_rows"] == 0
    L = d["letters"][0]
    assert L["brand"] == "Придумбур"
    assert L["brand_source"] == "столбец «Производитель» заказчика"


def test_attribution_paths_sum_to_rows_in_letters(monkeypatch):
    rows = [{"pn": "VT101-7", "qty": 1, "unit": "шт", "name": "к", "man": ""},
            {"pn": "ZZ-1", "qty": 1, "unit": "шт", "name": "н", "man": "Придумбур"}]
    monkeypatch.setattr(gl, "gap_rows", lambda: (rows, 10))
    monkeypatch.setattr(gl, "brands", lambda: BRANDS)
    monkeypatch.setattr(gl, "contacts", lambda: CONTACTS2)
    monkeypatch.setattr(gl, "already_written", lambda: set())
    d = gl.measure()
    assert sum(d["rows_by_attribution_path"].values()) == d["rows_in_letters"] == 2


def test_unbranded_rows_keep_the_maker_the_customer_named(monkeypatch):
    """Строка без адреса обязана сохранить, кого заказчик назвал: это следующая работа."""
    rows = [{"pn": "ZZ-9", "qty": 1, "unit": "шт", "name": "н", "man": "НетТакогоВКниге"}]
    monkeypatch.setattr(gl, "gap_rows", lambda: (rows, 10))
    monkeypatch.setattr(gl, "brands", lambda: BRANDS)
    monkeypatch.setattr(gl, "contacts", lambda: CONTACTS2)
    monkeypatch.setattr(gl, "already_written", lambda: set())
    d = gl.measure()
    assert d["unbranded_rows"] == 1
    assert d["unbranded_makers_named_by_customer"] == ["НетТакогоВКниге"]


def test_live_letters_name_their_attribution_path():
    p = ROOT / "gt/data/gap_letters.json"
    if not p.exists():
        pytest.skip("набор ещё не собран")
    d = json.loads(p.read_text(encoding="utf-8"))
    ok = {"наш шаблон номера", "столбец «Производитель» заказчика"}
    for L in d["letters"]:
        assert L.get("brand_source") in ok


# --- третий путь: почта из наших же наборов, с закрытым правилом домена ---

@pytest.mark.parametrize("maker,domain", [
    # Измеренные ложные совпадения проверки «имя входит в домен». Каждое из них
    # отправило бы письмо ЧУЖОЙ компании.
    ("Argo Hytos", "cargocaresolutions.com"),
    ("Versa", "universal-thermosensors.co.uk"),
    ("NATIONAL OILWELL VARCO", "platinum-international.store"),
    ("General Monitors", "general-gauges.com"),
    ("Johnson Controls", "johnsonturbine.com"),
    ("Power-Genex", "powergaskets.com"),
    ("ROTA", "rotatingmachinery.com"),
    ("Extreme Pro", "extreme-bolt.com"),
    # Дистрибьютор — не изготовитель: письмо изготовителю адресуется изготовителю.
    ("Drilltech", "drilltechuae.com"),
])
def test_domain_does_not_belong_to_maker(maker, domain):
    assert gl.owns_domain(maker, domain) is False


@pytest.mark.parametrize("maker,domain", [
    ("HARTING", "harting.com"),
    ("Phoenix Contact", "phoenixcontact.com"),
    ("MURR Elektronik", "murrelektronik.de"),
    ("EuroSwitch", "euroswitch.it"),
    ("BEKA", "beka.co.uk"),
    ("Wandfluh", "wandfluh.com"),
    ("SCANCON", "scancon.dk"),
    ("Industrie technik", "industrietechnik.it"),
])
def test_domain_belongs_to_maker(maker, domain):
    assert gl.owns_domain(maker, domain) is True


def test_domain_rule_survives_broken_input():
    for bad in ("", None, ".", "co.uk", "localhost"):
        assert gl.owns_domain("BEKA", bad) is False


def test_own_address_says_it_was_not_re_read():
    """Адрес из нашей записи — слабее прочитанного сейчас, и это должно быть видно."""
    a = gl.own_address("HARTING")
    if not a:
        pytest.skip("в наборах нет записи по этому изготовителю")
    assert a["confidence"] != "высокая"
    assert "не перечитывалась" in a["read_on"] and a["read_on"].startswith("gt/data/")


def test_own_address_prefers_a_shared_mailbox():
    """Общий ящик переживёт увольнение сотрудника, личный — нет."""
    a = gl.own_address("HARTING")
    if not a:
        pytest.skip("в наборах нет записи по этому изготовителю")
    assert a["email"].split("@")[0].lower() in (
        "info", "sales", "support", "contact", "enquiries", "webenquiries", "order",
        "northamericainquiry")


def test_own_address_is_empty_for_a_made_up_maker():
    assert gl.own_address("ВымышленМаш Нетакого") == {}
