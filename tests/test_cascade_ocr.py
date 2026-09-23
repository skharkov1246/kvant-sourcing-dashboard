"""Сканы внутри документа, архива и письма доходят до распознавания.

ЗАЧЕМ. Отбор распознавания брал файлы по виду: «изображение», «pdf». Документ
Word из фотографий страниц, архив сканов и письмо со снимком КП по виду не скан —
разбор давал им «пусто», распознавание не брало никогда. Цена в таком файле была
потеряна навсегда, и ни один отчёт этого не показывал.

Три звена, каждое рвётся молча:
1. каскад разбора ПОМЕЧАЕТ файл причиной «картинки внутри:»;
2. отбор распознавания БЕРЁТ файлы с такой причиной;
3. распознавание ДОСТАЁТ сканы изнутри и читает таблицу по координатам, так что
   цена встаёт под свой заголовок, а не выводится арифметикой.

Корпуса придуманы здесь же (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from tests.test_cascade import архив, письмо
from tests.test_read_word import картинка, png
from tests.test_read_word import docx as собрать_docx
from tests.test_read_word import связи, связь

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))
ocr = importlib.import_module("ocr")
indexer = ocr.indexer          # тот самый модуль, которым пользуется распознавание

ТАБЛИЦА = [["Наименование", "Кол-во", "Цена"], ["Насос ЦНС-38", "2", "1500"],
           ["Задвижка 30с41нж", "4", "250"]]


def docx_из_снимка() -> bytes:
    return собрать_docx(картинка("rIdA"), части={"word/media/image1.png": png()},
                        связи_документа=связи(связь("rIdA", "image", "media/image1.png")))


@pytest.fixture(autouse=True)
def каскад(monkeypatch):
    monkeypatch.setattr(indexer, "КАСКАД", True)
    monkeypatch.setattr(indexer.convert_office, "найти_soffice", lambda: None)


# ───────────────────────────── 1. пометка ─────────────────────────────

def test_документ_из_снимков_помечен_для_распознавания():
    rec: dict = {}
    строки, текст, отказ = indexer.читать(docx_из_снимка(), "docx", rec)
    assert not строки and not текст.strip()
    assert отказ.startswith(indexer.КАРТИНКИ_ВНУТРИ), отказ
    assert "сканов 1" in rec["read_chain"]


def test_документ_с_текстом_не_помечается():
    """Логотип в бланке КП с таблицей — не повод гнать файл в распознавание."""
    from tests.test_read_word import п
    b = собрать_docx(п("Коммерческое предложение на поставку насоса ЦНС-38") + картинка("rIdA"),
                     части={"word/media/image1.png": png()},
                     связи_документа=связи(связь("rIdA", "image", "media/image1.png")))
    _, текст, отказ = indexer.читать(b, "docx", {})
    assert текст and not отказ.startswith(indexer.КАРТИНКИ_ВНУТРИ)


# ───────────────────────────── 2. отбор ─────────────────────────────

def test_отбор_берёт_помеченные_файлы_любого_вида():
    код = "\n".join(ln.split("--")[0] for ln in ocr.CANDIDATES.splitlines())
    # «%%»: отбор выполняется с параметрами, и psycopg2 читает одиночный «%» как
    # место под значение (tests/test_ocr_mixed.py гоняет отбор на базе).
    метка = f"reason like '{indexer.КАРТИНКИ_ВНУТРИ}%%'"
    # Дважды: в условии «что брать» и в условии «какой вид» — иначе второе
    # условие отсечёт docx и архив, которые первое пропустило.
    assert код.count(метка) == 2, код


# ───────────────────────────── 3. извлечение ─────────────────────────────

def test_сканы_достаются_из_документа_архива_и_письма():
    снимок = png(тон=7)
    assert indexer.картинки_файла(docx_из_снимка()) == [("картинка", png())]
    assert indexer.картинки_файла(архив({"skan1.png": снимок, "pismo.txt": "Здравствуйте"})) \
        == [("картинка", снимок)]
    assert indexer.картинки_файла(письмо("КП во вложении", {"kp.png": снимок})) \
        == [("картинка", снимок)]


def test_pdf_берётся_из_архива_но_не_сам_по_себе():
    """Свой PDF файла распознавание читает целиком и так; PDF-вложение — нет."""
    pdf = b"%PDF-1.4\n%%EOF\n"
    assert indexer.картинки_файла(архив({"skan.pdf": pdf})) == [("pdf", pdf)]
    assert indexer.картинки_файла(pdf) == []


def test_сканов_не_больше_предела():
    b = архив({f"s{i}.png": png(тон=i) for i in range(indexer.МАКС_СКАНОВ_ФАЙЛА + 5)})
    assert len(indexer.картинки_файла(b)) == indexer.МАКС_СКАНОВ_ФАЙЛА


# ───────────────────────────── 4. чтение ─────────────────────────────

def test_распознавание_документа_из_снимков_даёт_цену_из_колонки(monkeypatch):
    monkeypatch.setattr(indexer, "download", lambda fo, rec=None: docx_из_снимка())
    monkeypatch.setattr(indexer, "SOURCE", "rfq")
    monkeypatch.setattr(ocr.ocr_table, "распознать_таблицу",
                        lambda путь, **k: (ТАБЛИЦА, "Насос ЦНС-38 2 1500", "уверенность 91"))
    rec, items = ocr.recognise({"fo": {"id": 7}, "deal": 1})
    assert rec["status"] == "разобран по скану", rec
    насос = next(it for it in items if "Насос" in it["item_name"])
    assert насос["_цена"] and float(насос["_цена"]["price"]) == 1500, насос["_цена"]
    assert насос["source_file"] == "7" and насос["segment_rule"] in ("строка", "файл", None)


def test_таблица_продолжается_на_следующем_скане_без_шапки(monkeypatch, tmp_path):
    """Шапка только на первой странице — ворота ставятся по всем страницам сразу."""
    ответы = iter([(ТАБЛИЦА, "стр 1", ""),
                   ([["Подшипник 6208", "10", "35"]], "стр 2", "")])
    monkeypatch.setattr(ocr.ocr_table, "распознать_таблицу", lambda путь, **k: next(ответы))
    строки, текст, _ = ocr.распознать_сканы([("картинка", png()), ("картинка", png(тон=1))],
                                          str(tmp_path))
    assert ["Подшипник 6208", "10", "35"] in строки and indexer.header_map(строки)[0] >= 0


def test_без_каскада_прежний_сплошной_текст(monkeypatch):
    """Выключенный каскад не меняет распознавание: таблица по координатам не зовётся."""
    monkeypatch.setattr(indexer, "КАСКАД", False)

    def нельзя(*a, **k):
        raise AssertionError("таблица по координатам позвана без каскада")
    monkeypatch.setattr(ocr.ocr_table, "распознать_таблицу", нельзя)
    monkeypatch.setattr(ocr, "ocr_image", lambda путь, psm=None: ("Насос ЦНС-38 2 шт", ""))
    строки, текст, причина = ocr.распознать_картинку("/нет/такого")
    assert строки == [] and текст == "Насос ЦНС-38 2 шт" and причина == ""


def test_пустая_таблица_уходит_в_сплошной_текст(monkeypatch):
    """Разрез по координатам ничего не дал — остаётся прежний путь, а не пустота."""
    monkeypatch.setattr(ocr.ocr_table, "распознать_таблицу", lambda путь, **k: ([], "", "сбой"))
    monkeypatch.setattr(ocr, "ocr_image", lambda путь, psm=None: ("Насос ЦНС-38 2 шт", ""))
    assert ocr.распознать_картинку("/нет/такого")[1] == "Насос ЦНС-38 2 шт"
