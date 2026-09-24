"""Дата квотации: из документа, из письма, с карточки — и никогда дата разбора.

Распоряжение владельца 24.09.2026: месяц и год квотации нужны, чтобы
индексировать цену на инфляцию выбранной страны. Корпус придуман (правило 18):
номера, компании и цены вымышлены.
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib
import sys
from datetime import date

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for каталог in ("library", "scripts"):
    sys.path.insert(0, str(ROOT / каталог))

import price_store  # noqa: E402
import quote_date as qd  # noqa: E402

СЕГОДНЯ = date(2026, 9, 24)
КАРТОЧКА = "2025-01-10T10:00:00+03:00"


def дата(текст: str, создана=КАРТОЧКА):
    return qd.дата_квотации(текст, создана, СЕГОДНЯ)


# ───────────────────────────────────────── дата в документе
@pytest.mark.parametrize("текст, ждём", [
    ("Коммерческое предложение № 15 от 12.03.2025\nПодшипник ВЫДУМ-6205", date(2025, 3, 12)),
    ("Исх. № 77 от «5» марта 2025 г.\nНасос ВЫДУМ-1", date(2025, 3, 5)),
    ("Quotation date: 12 March 2025\nValid until 30.04.2025", date(2025, 3, 12)),
    ("Date: March 7, 2025\nItem 1", date(2025, 3, 7)),
    ("Дата: 07.03.25\nПозиция 1", date(2025, 3, 7)),
    ("ТКП № 3-25 от 2025-02-14", date(2025, 2, 14)),
    ("Offer No. 88 dated 14-Feb-2025", date(2025, 2, 14)),
    # «до» из соседней фразы не отменяет подписанную дату своей фразы.
    ("Цены с доставкой до склада.  Дата: 14.02.2025", date(2025, 2, 14)),
])
def test_дата_документа(текст, ждём):
    assert дата(текст) == (ждём, qd.ДОКУМЕНТ)


def test_подпись_сильнее_первой_даты():
    """«от» в шапке — второй приоритет: подписанная дата ниже по тексту важнее."""
    текст = "Письмо № 5 от 01.02.2025\nДата предложения: 20.02.2025"
    assert дата(текст) == (date(2025, 2, 20), qd.ДОКУМЕНТ)


@pytest.mark.parametrize("текст", [
    "Предложение действительно до 30.04.2025",
    "Срок поставки 12.05.2025",
    "Delivery date: 12.05.2025",
    "На ваш запрос № 5 от 01.02.2025 сообщаем",
    "Согласно договору № 12 от 01.02.2024",
    "Подшипник по ГОСТ 8338-75, 12.03.2025",       # дата без подписи — не берём
    "Offer valid until 30.04.2025. Date: 03/04/2025",  # 3 апреля или 4 марта — не угадываем
    "Дата: 12.03.2031",                              # будущее — срок, а не дата КП
    "Дата: 12.03.1998",                              # раньше 2000 — год стандарта
])
def test_чужие_и_сомнительные_даты_не_берутся(текст):
    assert дата(текст) == (date(2025, 1, 10), qd.КАРТОЧКА)


def test_дата_много_старше_карточки_не_берётся():
    """Ссылка на старый документ, а не дата КП: старше карточки больше двух лет."""
    assert дата("КП от 01.02.2020", "2025-01-10T00:00:00+03:00") == \
        (date(2025, 1, 10), qd.КАРТОЧКА)


def test_американская_запись_по_невозможному_дню():
    assert дата("Date: 04/25/2025") == (date(2025, 4, 25), qd.ДОКУМЕНТ)
    assert дата("Date: 25/04/2025") == (date(2025, 4, 25), qd.ДОКУМЕНТ)


# ───────────────────────────────────────── письмо, карточка, нет
def test_дата_письма_от_читателя_писем():
    """Строку «Дата: ГГГГ-ММ-ДД ЧЧ:ММ» пишет library/read_mail.py — это письмо."""
    текст = "Тема: Предложение\nДата: 2025-03-12 10:00\nОт: отправитель\n\nЦена 100"
    assert дата(текст) == (date(2025, 3, 12), qd.ПИСЬМО)


def test_сырой_заголовок_письма():
    текст = "From: a@example.test\nDate: Wed, 12 Mar 2025 10:00:00 +0300\n\nЦена 100"
    assert дата(текст) == (date(2025, 3, 12), qd.ПИСЬМО)
    # «Date:» в теле, но не в формате письма — не письмо и не подписанная дата.
    assert дата("Date: скоро") == (date(2025, 1, 10), qd.КАРТОЧКА)


def test_дата_документа_сильнее_письма():
    текст = "Дата: 2025-03-12 10:00\n\nКП № 4 от 11.03.2025\nПозиция"
    assert дата(текст) == (date(2025, 3, 11), qd.ДОКУМЕНТ)


def test_формат_письма_совпадает_с_читателем():
    """Правило держится за вывод читателя: поменяют формат — тест покраснеет."""
    import read_mail
    сбор = read_mail._Сбор()
    сбор.письмо(0, "Тема", "2025-03-12 10:00", True, True, False, "тело")
    assert qd.дата_письма("\n".join(сбор.части), СЕГОДНЯ) == date(2025, 3, 12)


def test_без_даты_в_тексте_берётся_карточка():
    assert дата("Подшипник ВЫДУМ-6205 4 шт 312,50") == (date(2025, 1, 10), qd.КАРТОЧКА)


def test_нигде_нет_даты():
    assert дата("Подшипник ВЫДУМ-6205", None) == (None, qd.НЕТ)
    assert дата("", "мусор") == (None, qd.НЕТ)


def test_дата_разбора_не_подставляется_никогда():
    """Весь модуль — без now() в значении: сегодня служит только границей."""
    код = inspect.getsource(qd.дата_квотации)
    assert "return сегодня" not in код
    assert дата("нет ничего", None)[0] is None


def test_источники_совпадают_со_схемой():
    схема = (ROOT / "library/supabase/schema.sql").read_text(encoding="utf-8")
    блок = схема[схема.index("lib_prices_price_date_src_chk"):][:400]
    for источник in qd.ИСТОЧНИКИ:
        assert f"'{источник}'" in блок


# ───────────────────────────────────────── запись цены
def test_проставить_и_строка_цены():
    позиции = [{"source_file": "7", "item_name": "Насос ВЫДУМ-1", "deal_id": "77"},
               {"source_file": "7", "item_name": "Насос ВЫДУМ-2", "deal_id": "77"}]
    qd.проставить(позиции, "КП от 12.03.2025", {"card_created": КАРТОЧКА}, СЕГОДНЯ)
    ц = {"price": 1, "currency": "RUB", "confidence": "med"}
    for п in позиции:
        строка = dict(zip(price_store.КОЛОНКИ, price_store.строка(п, ц, lambda s: str(s or ""))))
        assert (строка["price_date"], строка["price_date_src"]) == ("2025-03-12", qd.ДОКУМЕНТ)


def test_старый_вызов_без_даты_оставляет_источник_пустым():
    """Пусто — «не проверено», а не «нет»: разные вещи (правило 16)."""
    строка = dict(zip(price_store.КОЛОНКИ, price_store.строка(
        {"source_file": "7", "item_name": "x"}, {"price": 1, "currency": "RUB",
                                                  "confidence": "med"}, lambda s: str(s or ""))))
    assert строка["price_date"] is None and строка["price_date_src"] is None


class _Курсор:
    """Курсор без базы: отвечает на запрос колонок, остальное записывает."""

    def __init__(self, колонки):
        self.колонки, self.запросы = колонки, []

    def execute(self, q, args=None):
        self.запросы.append(q)

    def fetchall(self):
        return [(к,) for к in self.колонки]


def test_без_миграции_цена_пишется_без_колонки_источника(capsys):
    """Ночной разбор не должен падать, пока схема не применена (риск из плана)."""
    строка = price_store.строка({"source_file": "7", "item_name": "x", "price_date": "2025-03-12",
                                 "price_date_src": qd.ДОКУМЕНТ},
                                {"price": 1, "currency": "RUB", "confidence": "med"},
                                lambda s: str(s or ""))
    без = [к for к in price_store.КОЛОНКИ if к != "price_date_src"]
    записано = {}
    price_store.записать(_Курсор(без), [строка],
                         lambda cur, q, rows, **k: записано.update(q=q, rows=rows))
    assert "price_date_src" not in записано["q"] and "price_date" in записано["q"]
    assert len(записано["rows"][0]) == len(без)
    assert "2025-03-12" in записано["rows"][0]
    assert "нет колонок price_date_src" in capsys.readouterr().out


def test_после_миграции_пишется_всё():
    строка = price_store.строка({"source_file": "7", "item_name": "x"},
                                {"price": 1, "currency": "RUB", "confidence": "med"},
                                lambda s: str(s or ""))
    записано = {}
    price_store.записать(_Курсор(price_store.КОЛОНКИ), [строка],
                         lambda cur, q, rows, **k: записано.update(q=q, rows=rows))
    assert записано["q"] == price_store.ВСТАВКА and записано["rows"] == [строка]


# ───────────────────────────────────────── разбор целиком
psycopg2 = pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")


@pytest.fixture()
def ix(monkeypatch):
    spec = importlib.util.spec_from_file_location("indexer_qd", ROOT / "library" / "indexer.py")
    модуль = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(модуль)
    monkeypatch.setattr(модуль, "SOURCE", "rfq")
    return модуль


ПИСЬМО = ("From: a@example.test\r\nTo: b@example.test\r\nSubject: KP\r\n"
          "Date: Wed, 12 Mar 2025 10:00:00 +0300\r\n"
          "Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
          "Коммерческое предложение\n"
          "1 Подшипник ВЫДУМ-6205 шт 4 312,50 1250,00\n"
          "2 Вал ВЫДУМ-77 шт 1 12400,00 12400,00\n").encode()


@pytest.mark.parametrize("каскад", [False, True])
def test_разбор_письма_ставит_дату_письма(ix, monkeypatch, каскад):
    """Без каскада (ночной и ежедневный разбор) .eml читается простым текстом с
    сырым заголовком «Date:», с каскадом — читателем писем. Дата одна и та же."""
    monkeypatch.setattr(ix, "КАСКАД", каскад)
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: ПИСЬМО)
    rec, items = ix.handle({"deal": "77", "field": "f", "field_title": "КП поставщика",
                            "origin": "поле запроса", "fo": {"id": 5}, "company": "9",
                            "brands": None, "our_company": None, "card_created": КАРТОЧКА})
    assert items, rec
    assert {(it["price_date"], it["price_date_src"]) for it in items} == \
        {("2025-03-12", qd.ПИСЬМО)}


def test_карточка_передаёт_дату_создания_в_ссылку(ix, monkeypatch):
    поле = next(iter(ix.ПОЛЯ_КП))
    monkeypatch.setattr(ix, "bx_all_by_id", lambda *a, **k: [
        {"id": 7, "createdTime": КАРТОЧКА, поле: [{"id": 1, "urlMachine": "https://x.test/1"}]}])
    monkeypatch.setattr(ix, "наша_компания", lambda _v: None)
    refs = ix.collect_refs_rfq(0)
    assert refs[0]["card_created"] == КАРТОЧКА
    assert "createdTime" in ix.ПОЛЯ_КАРТОЧКИ


def test_распознавание_ставит_дату_тем_же_правилом():
    """Правило 14: обе вставки цены (разбор и скан) получают дату одинаково."""
    код = (ROOT / "library/ocr.py").read_text(encoding="utf-8")
    assert код.count("quote_date.проставить(") == 2, "оба пути распознавания — таблица и текст"
    import ocr
    строки = [["№", "Наименование", "Кол-во", "Цена", "Сумма"],
              ["1", "Подшипник ВЫДУМ-6205", "4", "312,50", "1250,00"]]
    ocr.indexer.SOURCE = "rfq"
    try:
        items = ocr.таблица_скана({"file_id": "5"}, {"deal": "77", "card_created": КАРТОЧКА},
                                  строки, "КП № 9 от 03.02.2025\nПодшипник")
    finally:
        ocr.indexer.SOURCE = "deals"
    assert items and {(it["price_date"], it["price_date_src"]) for it in items} == \
        {("2025-02-03", qd.ДОКУМЕНТ)}
