"""Цена, подтверждённая арифметикой строки: что принимается и что отбивается.

Все корпуса придуманы (правило 18 CLAUDE.md), но КАЖДЫЙ повторяет форму, на
которой инструмент ошибался на живых данных, — иначе тест охраняет не то место.
Формы взяты из разбора трёх предложений по заявке 18.09.2026.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import tkp_tables as T  # noqa: E402


# --- арифметика -------------------------------------------------------------

def test_цена_принимается_когда_строка_сама_её_подтверждает():
    assert T.unit_by_arithmetic("1 | AB-1701 | 35 | $2,273 | $79,555 | 4-6weeks") == (
        2273.0, 79555.0, 35)


def test_итог_не_принимается_за_цену():
    """Форма CambiaTech: заголовок указывал на колонку ИТОГА.

    Проверка заголовком взяла бы 79 555 за цену штуки при цене 2 273 — в
    тридцать пять раз выше. Арифметика такого не допускает: цена та, от которой
    итог получается умножением.
    """
    unit, total, qty = T.unit_by_arithmetic("1 | AB-1701 | 35 | $2,273 | $79,555")
    assert unit == 2273.0 and total == 79555.0 and qty == 35
    assert unit != total


def test_номер_позиции_ценой_не_становится():
    """Форма Quotation p76057: поставщик вернул перечень с нулями.

    Прежнее правило «последнее число строки» брало здесь номер позиции. Здесь
    строка не даёт ни количества, ни итога — значит цены нет, и это отказ, а не
    ноль и не догадка.
    """
    assert T.unit_by_arithmetic("7 | ZZ-1019431 | $0.00 | /EA | $0.00") is None


def test_количество_слитое_с_ценой_разбирается_верно():
    """Форма ¥-предложения: PDF теряет пробел, «pcs 2 545.20» — это 2 и 545,20.

    Пробел, принятый за разделитель разрядов, читал это как одно число 2 545,20
    и ломал самопроверку у 56 строк формы.
    """
    assert T.unit_by_arithmetic("8 ZZ-14013 pcs 2 545.20¥ | ¥ | 1,090.40") == (
        545.2, 1090.4, 2)


def test_ложная_тройка_отбивается_узким_допуском():
    """22 × 768 = 16 896 при итоге 16 902,60 — расхождение 6,6.

    При допуске 0,1 % такая тройка проходила и перебивала верную (768,30 × 22),
    потому что 768 больше 22. Допуск на округление копеек её не пропускает.
    """
    assert T.unit_by_arithmetic("49 ZZ-21024 pcs 22 768.30¥ | ¥ | 16,902.60") == (
        768.3, 16902.6, 22)


def test_две_согласованные_цены_в_строке_дают_отказ():
    """Выбрать между ними нечем, а ошибка уйдёт в деньги отчёта."""
    # 10 × 5 = 50 и 4 × 25 = 100: обе тройки согласованы, цены разные.
    assert T.unit_by_arithmetic("ZZ-1 | 10 | 5.00 | 50.00 | 4 | 25.00 | 100.00") is None


def test_количество_один_ценой_не_подтверждается():
    """При количестве 1 единица равна итогу, и проверка вырождается."""
    assert T.unit_by_arithmetic("ZZ-9 | 1 | 500.00 | 500.00") is None


# --- разряды и дробь --------------------------------------------------------

def test_русская_и_английская_записи_читаются_каждая_своим_правилом():
    assert T.money_values("итог 1 234,56") == [1234.56]
    assert T.money_values("итог 1,234.56") == [1234.56]


def test_пробел_без_копеек_разрядом_не_считается():
    """«12 413,700.00» — это количество 12 и цена 413 700,00, а не 12 413."""
    assert 413700.0 in T.money_values("pcs 12 413,700.00¥")
    assert 12413.0 not in T.money_values("pcs 12 413,700.00¥")


# --- номер ------------------------------------------------------------------

def test_итог_строки_номером_не_становится():
    """У двух самых дорогих строк заявки в поле номера стоял итог «¥4,964,400.00».

    Вместе эти две строки несут 1,38 млн USD экспозиции, и обе терялись.
    """
    assert not T.looks_like_pn("¥4,964,400.00")
    assert not T.looks_like_pn("$0.00")
    assert not T.looks_like_pn("08/13/2026")
    assert not T.looks_like_pn("pcs")


def test_чисто_цифровой_номер_остаётся_номером():
    """Отказ по цифровым номерам обрушил разбор трёх файлов — это не деньги."""
    assert T.looks_like_pn("3420932")
    assert T.looks_like_pn("012633000")
    assert T.looks_like_pn("1019431-1600")


def test_номер_берётся_из_строки_когда_поле_занято_итогом():
    assert T.pn_from_row("42 ZZ-21215M pcs 12 413,700.00¥ | ¥ 4,964,400.00") == (
        "ZZ-21215M")


# --- валюта -----------------------------------------------------------------

def test_знак_иены_неоднозначен_и_молча_не_пересчитывается():
    cur, why = T.currency_of("8 ZZ-1 pcs 2 545.20¥")
    assert cur == T.YEN_MARK and "кода валюты" in why
    usd, msg = T.to_usd(545.2, cur, {"CNY": 6.730863, "JPY": 153.797848})
    assert usd is None and "22,8" in msg


def test_решение_по_иене_пересчитывает_и_называет_основание():
    usd, msg = T.to_usd(545.2, T.YEN_MARK, {"CNY": 6.730863}, yen="cny")
    assert usd is not None and abs(usd - 545.2 / 6.730863) < 1e-9
    assert "CNY" in msg


def test_код_валюты_рядом_со_знаком_снимает_неоднозначность():
    assert T.currency_of("2 545.20¥ CNY")[0] == "CNY"
    assert T.currency_of("2 545.20¥ JPY")[0] == "JPY"


def test_две_валюты_в_строке_дают_отказ_а_не_выбор():
    cur, why = T.currency_of("500 USD / 42000 RUB")
    assert cur == "" and "несколько валют" in why


def test_пустая_валюта_долларом_не_считается():
    usd, msg = T.to_usd(100.0, "", {"USD": 1.0})
    assert usd is None and "не установлена" in msg


# --- сквозной проход --------------------------------------------------------

def _dump(tmp: Path) -> Path:
    doc = {"files": [
        {"file_name": "kp-vendor.pdf", "direction": "входящее", "origin": "сделка 1",
         "prices": [
             {"pn": "ZZ-1701", "raw": "1 | ZZ-1701 | 35 | $2,273 | $79,555", "sheet": "стр.1",
              "row": 4},
             {"pn": "¥4,964,400.00",
              "raw": "42 ZZ-21215M pcs 12 413,700.00¥ | ¥ 4,964,400.00", "row": 42},
             {"pn": "ZZ-NOPRICE", "raw": "7 | ZZ-NOPRICE | $0.00 | /EA | $0.00", "row": 7},
         ]},
        {"file_name": "nash-zapros.xlsx", "direction": "наш запрос",
         "prices": [{"pn": "ZZ-1701", "raw": "1 | ZZ-1701 | 35 | $1.00 | $35.00"}]},
    ]}
    p = tmp / "dump.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_сквозной_проход_берёт_обе_формы_и_чинит_номер(tmp_path):
    doc = json.loads(_dump(tmp_path).read_text(encoding="utf-8"))
    rows, why = T.extract(doc, yen="cny")
    keys = {r["key"] for r in rows}
    assert "ZZ1701" in keys and "ZZ21215M" in keys
    assert "ZZNOPRICE" not in keys
    assert any(r["usd"] == 2273.0 for r in rows)
    assert why


def test_наш_исходящий_запрос_в_цену_закупки_не_идёт(tmp_path):
    """Иначе нашу же цифру мы предъявим как предложение контрагента."""
    doc = json.loads(_dump(tmp_path).read_text(encoding="utf-8"))
    rows, _ = T.extract(doc, yen="cny")
    assert all(r["file"] != "nash-zapros.xlsx" for r in rows)
    assert all(r["unit"] != 1.0 for r in rows)


def test_счётчики_не_несут_ни_цен_ни_номеров(tmp_path):
    """Счётчики уходят в публичный репозиторий (правило 17 CLAUDE.md)."""
    doc = json.loads(_dump(tmp_path).read_text(encoding="utf-8"))
    rows, why = T.extract(doc, yen="cny")
    c = T.counters(rows, why, doc)
    text = json.dumps(c, ensure_ascii=False)
    assert "2273" not in text and "ZZ-1701" not in text and "413700" not in text
    assert c["rows_confirmed_by_arithmetic"] >= 2
