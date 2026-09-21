"""Сведение бюджета заказчика с нашей закупкой — gt/tools/budget_join.py.

Корпус придуман целиком (правило 18): номера, наименования и цены здесь
вымышленные, из базы не копируются.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

bj = pytest.importorskip("budget_join")
openpyxl = pytest.importorskip("openpyxl")

RUB = 80.0


def make_book(path: Path, header_row: int = 4) -> None:
    """Файл заказчика: шапка не в первой строке, столбцы в произвольном порядке."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Лист потребности"
    for _ in range(header_row - 1):
        ws.append(["Потребность в запасных частях", None, None, None, None, None])
    ws.append(["№ п/п", "Производитель", "Наименование ТРУ", "Каталожный номер",
               "Количество", "Цена без НДС с транспортными расходами, руб", "СУММА, руб"])
    ws.append([1, "ВымышленМаш", "Клапан придуманный", "ВЫД-100-1", 2, 8000, 16000])
    ws.append([2, "ВымышленМаш", "Привод придуманный", "zx 200/2", 3, 800, 2400])
    ws.append([3, "ВымышленМаш", "Блок придуманный", "ВЫД-300-3", 1, 400000, 400000])
    ws.append([None, None, "ИТОГО", None, None, None, 418400])
    wb.save(path)


def test_header_and_columns_found_by_meaning(tmp_path):
    """Шапка ищется по смыслу: в файле заказчика столбцы переезжают каждую редакцию."""
    p = tmp_path / "b.xlsx"
    make_book(p)
    rows, meta = bj.read_budget(p)
    assert [r["num"] for r in rows] == ["1", "2", "3"]
    assert meta["sheets"][0]["header_row"] == 4
    assert rows[0]["rub"] == 8000 and rows[0]["sum_rub"] == 16000


def test_total_line_without_number_is_not_a_row(tmp_path):
    """Строка «ИТОГО» без № п/п и без номера не должна попасть в бюджет."""
    p = tmp_path / "b.xlsx"
    make_book(p)
    rows, _ = bj.read_budget(p)
    assert sum(r["sum_rub"] for r in rows) == 418400
    assert len(rows) == 3


def test_key_ignores_separators_and_case(tmp_path):
    """«zx 200/2» и «ZX-200-2» — один номер: ключ один на все инструменты."""
    p = tmp_path / "b.xlsx"
    make_book(p)
    rows, _ = bj.read_budget(p)
    from pnkey import key
    assert rows[1]["k"] == key("ZX-200-2") == "ZX2002"


def joined(found=None, lo=None, hi=None, rub_price=8000.0, qty=2.0, covers=None):
    """Одна сведённая строка. Считаем так же, как join(), но без чтения наборов."""
    b = rub_price / RUB
    if found is not None:
        grade = "цена найдена"
        fit = "убыток" if b < found else "сходится"
    elif lo is not None and hi is not None:
        grade = "только вилка"
        fit = "бюджет ниже нашей оценки" if b < lo else "не определено"
    else:
        grade, fit = "ничего", "не определено"
    return {"num": "1", "pn": "ВЫД-100-1", "name": "Клапан придуманный",
            "man": "ВымышленМаш", "sheet": "Лист", "qty": qty, "rub": rub_price,
            "sum_rub": rub_price * qty, "k": "ВЫД1001",
            "budget_usd": b, "budget_sum_usd": rub_price * qty / RUB,
            "our_lo": lo, "our_hi": hi, "found": found, "covers_qty": covers,
            "verdict": "", "grade": grade, "fit": fit, "in_ask": True,
            "qty_ours": qty, "channel": "", "contacts": ""}


def test_shortfall_counts_only_found_prices():
    """Убыток объявляется только против НАЙДЕННОЙ цены: вилка — мнение."""
    j = [joined(found=150.0),                    # бюджет 100 < 150 → убыток
         joined(lo=500.0, hi=900.0),             # бюджет 100 ниже вилки, но это мнение
         joined()]
    c = bj.counters(j, RUB, {}, Path("вымышленный.xlsx"))
    assert c["rows_budget_below_found_price"] == 1
    assert c["shortfall_usd_if_whole_volume"] == pytest.approx((150 - 100) * 2)
    assert c["of_them_seller_confirmed_volume"] == 0


def test_confirmed_volume_counted_separately():
    """Подтверждённое продавцом количество считается отдельным числом."""
    j = [joined(found=150.0, covers="full — остаток подтверждён на весь объём")]
    c = bj.counters(j, RUB, {}, Path("вымышленный.xlsx"))
    assert c["of_them_seller_confirmed_volume"] == 1


def test_readiness_split_sums_to_total():
    """Разряды готовности — разбиение: их сумма обязана давать весь бюджет."""
    j = [joined(found=10.0), joined(lo=1.0, hi=2.0), joined()]
    c = bj.counters(j, RUB, {}, Path("вымышленный.xlsx"))
    parts = c["budget_by_our_readiness"]
    assert sum(v["rows"] for v in parts.values()) == c["rows_with_price"]
    assert sum(v["budget_usd"] for v in parts.values()) == pytest.approx(
        c["budget_total_usd"], abs=0.05)


def test_counters_carry_no_row_level_data():
    """В репозиторий уходят ТОЛЬКО агрегаты: ни номера, ни наименования."""
    j = [joined(found=150.0), joined(lo=1.0, hi=2.0), joined()]
    c = bj.counters(j, RUB, {}, Path("вымышленный.xlsx"))
    text = json.dumps(c, ensure_ascii=False)
    for forbidden in ("ВЫД-100-1", "Клапан придуманный", "ВымышленМаш"):
        assert forbidden not in text
    assert "rows" not in c or not isinstance(c.get("rows"), list)


def test_proposal_never_calls_a_band_a_loss():
    """По вилке нельзя объявлять ни убыток, ни прибыль — только ориентир."""
    p = bj.proposal(joined(lo=500.0, hi=900.0))
    assert "убыт" in p or "НИЖЕ" in p
    assert "запас" not in p
    p2 = bj.proposal(joined(lo=1.0, hi=2.0))
    assert "оценк" in p2 and "раз" in p2


def test_proposal_marks_unconfirmed_volume():
    """Найденная цена без подтверждённого объёма не даёт закладывать строку."""
    assert "НЕ подтверждён" in bj.proposal(joined(found=10.0))
    assert "подтверждён, строку можно" in bj.proposal(
        joined(found=10.0, covers="full — весь объём"))


def test_proposal_for_empty_row_names_the_maker():
    """Там, где нет ничего, предложение — запрос изготовителю, а не «поискать»."""
    p = bj.proposal(joined())
    assert "ВымышленМаш" in p and "не знаем" in p


def test_pdf_inside_repo_is_refused(tmp_path, monkeypatch, capsys):
    """Построчный документ — коммерческий файл заказчика: внутрь git он не пишется."""
    p = tmp_path / "b.xlsx"
    make_book(p)
    monkeypatch.setattr(sys, "argv", ["budget_join.py", "--budget", str(p),
                                      "--pdf", str(ROOT / "gt/docs/нельзя.pdf")])
    monkeypatch.setattr(bj, "join", lambda rows, rub: [joined()])
    assert bj.main() == 2
    assert "ОТКАЗ" in capsys.readouterr().err
    assert not (ROOT / "gt/docs/нельзя.pdf").exists()
