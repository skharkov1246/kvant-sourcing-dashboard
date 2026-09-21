"""Проверка самой проверки PDF — scripts/pdf_check.py.

Корпус придуман: числа символов на страницу задаются прямо, PDF не собирается.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts/pdf_check.py").read_text(encoding="utf-8")


def sparse(counts, threshold=400):
    """Тот же расчёт, что в pdf_check: строка берётся из исходника, а не копируется.

    Копия правила в тесте разошлась бы с правилом в коде молча — поэтому здесь
    исполняется именно та строка, которая стоит в scripts/pdf_check.py.
    """
    line = next(x for x in SRC.splitlines() if x.strip().startswith("sparse = ["))
    # Одним словарём: у списочного выражения своя область видимости, и при
    # раздельных globals/locals оно не видит counts.
    ns = {"counts": counts, "a": type("A", (), {"min_chars": threshold})()}
    exec(line.strip(), ns)  # noqa: S102
    return ns["sparse"]


def test_short_last_page_is_not_a_defect():
    """Хвост документа короче порога просто потому, что текст кончился."""
    assert sparse([3000, 3000, 250]) == []


def test_short_middle_page_is_a_defect():
    """Короткая страница, за которой есть содержимое, — кривой разрыв."""
    assert sparse([3000, 250, 3000]) == [2]


def test_several_short_pages_all_reported_except_the_last():
    assert sparse([250, 3000, 250, 200]) == [1, 3]


def test_single_page_document_is_never_sparse():
    assert sparse([10]) == []


def test_empty_document_does_not_crash():
    assert sparse([]) == []


def test_threshold_is_respected():
    assert sparse([399, 3000], 400) == [1]
    assert sparse([400, 3000], 400) == []


def test_rule_is_documented():
    """Правило обязано быть записано: иначе следующий агент «починит» его обратно."""
    doc = (ROOT / "docs/ПРАВИЛА-PDF.md").read_text(encoding="utf-8")
    assert "последнюю" in doc.lower() or "последней" in doc.lower()
    assert re.search(r"counts\[:-1\]", SRC)
