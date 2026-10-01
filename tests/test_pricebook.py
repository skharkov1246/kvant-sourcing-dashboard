"""Прайс-бук КП: ключ артикула, правило строгой сравнимости, холостой загрузчик.

Корпус придуман (CLAUDE.md, правило 18): поставщики, номера и цены сочинены.
Каждая проверка держит одну ошибку, найденную 01.10.2026 на настоящем архиве:
валюта «other» засчитывалась как валюта; номер позиции «3.1» — как артикул;
пустой номер поставщика — как поставщик; номер строки в документе повторяется.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import load_pricebook as lp  # noqa: E402
from pricebook import ПРИЧИНЫ, part_key, причина_несравнимости  # noqa: E402

ШАПКА_ОК = {"doc_type": "offer", "incoterm": "EXW", "vat_included": ""}
СТРОКА_ОК = {"unit_price": "120.5", "currency_eff": "USD", "confidence": "0.85",
             "price_in_source": "1.0", "part_number": "VYD-1001"}


def test_ключ_как_lib_pn_key():
    assert part_key("ШАЙБА-12Ё") == "шайба12е"
    assert part_key("3115 3768 00") == "3115376800"
    assert part_key("VYD+12(A)") == "vyd12a"
    assert part_key(None) == ""


def test_сравнимая_строка():
    assert причина_несравнимости(СТРОКА_ОК, ШАПКА_ОК) is None


def test_каждая_причина_и_порядок():
    случаи = [
        ({"unit_price": ""}, {}, "нет цены"),
        ({"currency_eff": "other"}, {}, "нет валюты"),
        ({}, {"doc_type": "pricelist"}, "прайс-лист"),
        ({"confidence": "0.4"}, {}, "низкая уверенность"),
        ({"price_in_source": "0.0"}, {}, "цена не найдена в исходнике"),
        ({"part_number": "3.1"}, {}, "нет артикула"),
        ({"part_number": "6m³"}, {}, "нет артикула"),
        ({}, {"incoterm": "FOB"}, "базис не FCA/EXW"),
        ({"currency_eff": "RUB"}, {}, "НДС не известен"),
    ]
    for правка_строки, правка_шапки, ждём in случаи:
        got = причина_несравнимости({**СТРОКА_ОК, **правка_строки}, {**ШАПКА_ОК, **правка_шапки})
        assert got == ждём, (правка_строки, правка_шапки)
    assert list(dict.fromkeys(с[2] for с in случаи)) == list(ПРИЧИНЫ)
    # первая сработавшая причина побеждает: прайс-лист без валюты — «нет валюты»
    assert причина_несравнимости({**СТРОКА_ОК, "currency_eff": ""},
                                 {**ШАПКА_ОК, "doc_type": "pricelist"}) == "нет валюты"


def test_рубли_с_пометкой_ндс_сравнимы():
    assert причина_несравнимости({**СТРОКА_ОК, "currency_eff": "RUB"},
                                 {**ШАПКА_ОК, "vat_included": "False"}) is None


def _корпус(папка: Path) -> None:
    шапки = [
        {"request_id": "1", "supplier_id": "501.0", "doc_type": "offer", "incoterm": "EXW",
         "vat_included": "", "deal_id": "71.0", "is_selected": "True"},
        {"request_id": "2", "supplier_id": "502.0", "doc_type": "offer", "incoterm": "FCA",
         "vat_included": "", "deal_id": "71.0", "is_selected": "False"},
        {"request_id": "3", "supplier_id": "", "doc_type": "offer", "incoterm": "EXW",
         "vat_included": "", "deal_id": "72.0", "is_selected": "False"},
        {"request_id": "4", "supplier_id": "", "doc_type": "pricelist", "incoterm": "EXW",
         "vat_included": "", "deal_id": "73.0", "is_selected": "False"},
    ]
    строки = [
        # один артикул у двух настоящих поставщиков
        {"request_id": "1", "line_no": "1", "part_number": "VYD-1001", "supplier_id": "501.0",
         "is_selected": "True"},
        {"request_id": "2", "line_no": "1", "part_number": "vyd 1001", "supplier_id": "502.0"},
        # другой артикул: настоящий поставщик и пустой номер — это НЕ два поставщика
        {"request_id": "1", "line_no": "2", "part_number": "VYD-2002", "supplier_id": "501.0"},
        {"request_id": "3", "line_no": "1", "part_number": "VYD-2002", "supplier_id": ""},
        # повтор номера позиции в одном документе (вторая таблица КП)
        {"request_id": "1", "line_no": "2", "part_number": "VYD-3003", "supplier_id": "501.0"},
        # несравнимые
        {"request_id": "4", "line_no": "1", "part_number": "VYD-4004", "supplier_id": ""},
        {"request_id": "2", "line_no": "2", "part_number": "4.5", "supplier_id": "502.0"},
        {"request_id": "2", "line_no": "3", "part_number": "VYD-5005", "supplier_id": "502.0",
         "currency_eff": "other"},
        {"request_id": "2", "line_no": "4", "part_number": "VYD-6006", "supplier_id": "502.0",
         "unit_price": ""},
    ]
    with open(папка / "kp_headers.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for h in шапки for k in h}))
        w.writeheader()
        w.writerows(шапки)
    поля = sorted({*СТРОКА_ОК, *(k for r in строки for k in r), "pn_key"})
    with open(папка / "kp_lines.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=поля)
        w.writeheader()
        for r in строки:
            w.writerow({**СТРОКА_ОК, "pn_key": (r["part_number"] or "").upper().replace("-", ""), **r})


def test_сводка_на_придуманном_корпусе(tmp_path):
    _корпус(tmp_path)
    шапки, строки = lp.прочитать(tmp_path)
    lp.разметить(шапки, строки)
    с = lp.сводка(шапки, строки)
    assert с["строк"] == 9 and с["строк с ценой"] == 8
    assert с["строго сравнимы"] == 5
    assert с["по причинам"]["прайс-лист"] == 1
    assert с["по причинам"]["нет артикула"] == 1
    assert с["по причинам"]["нет валюты"] == 1
    # VYD-1001 — два поставщика; VYD-2002 — один поставщик и пустой номер
    assert с["артикулов у двух и более поставщиков"] == 1
    assert с["строк выбранных поставщиков, сравнимых"] == 1
    assert с["строк с неуникальным номером позиции"] == 2
    assert [r["row_no"] for r in строки] == list(range(9))


def test_холостой_прогон_пишет_только_агрегаты(tmp_path, monkeypatch, capsys):
    _корпус(tmp_path)
    monkeypatch.setenv("PRICEBOOK_DIR", str(tmp_path))
    monkeypatch.delenv("APPLY", raising=False)
    assert lp.main() == 0
    out = capsys.readouterr().out
    assert "холостой прогон" in out
    for запрещено in ("VYD", "vyd", "120.5", "501", "502"):
        assert запрещено not in out, запрещено


def test_типы_колонок():
    assert lp._значение("deal_id", "11790.0") == 11790
    assert lp._значение("price_in_source", "1.0") is True
    assert lp._значение("is_selected", "False") is False
    assert lp._значение("vat_included", "") is None
    assert lp._значение("incoterm", " EXW ") == "EXW"
