"""Цены нашего КП и содержимое файла: поле говорит «наше КП», содержимое — другое.

Холостой переразбор 27.09.2026 (2 512 файлов поля «Offer from us»): 159 файлов
классификатор уверенно прочитал как КП поставщика нам (751 строка цены), 101 —
как договор или счёт (384). Такая цена — не наша цена заказчику. Проверяется:
  · закрытый список (indexer.НЕ_НАШЕ_КП) — папки классификатора, без «наше
    предложение» и «не определено» (правило 7: обвиняет закрытый список);
  · строки_цен у сделки отдаёт пустой список для каждой папки списка и строки
    для остальных; сбой классификатора (папки нет) цену не теряет;
  · карточек запросов и писем отбор не касается;
  · отложенный файл не пополняет счёт ложных строк цены;
  · полный разбор (handle) с подменённым классификатором;
  · профиль крупного файла — только доли и константы кода (правило 17).

Базы не нужно. Корпус придуман (правило 18).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")

import doc_folder  # noqa: E402
import doc_kind  # noqa: E402
import indexer as ix  # noqa: E402
import price_store  # noqa: E402
import reparse  # noqa: E402
from tests.test_read_sheet import c as ячейка, xlsx, лист as разметка_листа  # noqa: E402

НАШЕ = "ufCrm_1585568303498"            # Offer from us
ЦЕНА = 4242.42                          # в корпусе больше нигде не встречается


def _позиции() -> list[dict]:
    общие = {"oem": "", "source_file": "Ф-наше", "deal_id": "41", "company": None}
    return [
        dict(общие, item_name="Клапан выдуманный", part_number="KL-7", unit="шт", qty=2.0,
             _цена={"price": ЦЕНА, "currency": "USD", "confidence": "med", "note": None,
                    "total": 2 * ЦЕНА}),
        dict(общие, item_name="Седло выдуманное", part_number="ZC-2002", unit="шт", qty=3.0,
             _цена={"price": 100.0, "currency": "USD", "confidence": "med", "note": None,
                    "total": 300.0}),
    ]


def _запись(папка: str | None) -> dict:
    rec = {"origin": doc_folder.ПОЛЕ_СДЕЛКИ, "field": НАШЕ, "file_id": "Ф-наше"}
    if папка is not None:
        rec["папка_содержимого"] = папка
    return rec


@pytest.fixture
def сделки(monkeypatch):
    monkeypatch.setattr(ix, "SOURCE", "deals")
    monkeypatch.setattr(ix, "НАШЕ_КП", True)
    monkeypatch.setattr(ix, "ОТЛОЖЕНО_НАШЕ_КП", {})
    monkeypatch.setattr(price_store, "ОТКАЗАНО", price_store.Counter())


def test_закрытый_список_из_папок_классификатора():
    assert set(ix.НЕ_НАШЕ_КП) <= set(doc_kind.ПАПКИ)
    assert len(set(ix.НЕ_НАШЕ_КП)) == len(ix.НЕ_НАШЕ_КП) == 5
    assert doc_kind.НАШЕ_ПРЕДЛОЖЕНИЕ not in ix.НЕ_НАШЕ_КП
    assert doc_kind.НЕ_ОПРЕДЕЛЕНО not in ix.НЕ_НАШЕ_КП


@pytest.mark.parametrize("папка", [*doc_kind.ПАПКИ, None])
def test_строки_цен_сделки_по_содержимому(сделки, папка):
    строки = ix.строки_цен(_запись(папка), _позиции())
    if папка in ix.НЕ_НАШЕ_КП:
        assert строки == []
        assert ix.против_поля(_запись(папка)) == папка
        assert ix.ОТЛОЖЕНО_НАШЕ_КП == {папка: [1, 2]}
    else:
        поля = [dict(zip(price_store.КОЛОНКИ, r)) for r in строки]
        assert sorted(p["price"] for p in поля) == [100.0, ЦЕНА]
        assert {p["feed"] for p in поля} == {price_store.FEED_НАШЕ_КП}
        assert ix.против_поля(_запись(папка)) is None
        assert ix.ОТЛОЖЕНО_НАШЕ_КП == {}


def test_файл_без_цены_не_считается_отложенным(сделки):
    без_цены = [dict(it, _цена=None) for it in _позиции()]
    assert ix.строки_цен(_запись(doc_kind.ДОГОВОР), без_цены) == []
    assert ix.ОТЛОЖЕНО_НАШЕ_КП == {}


def test_карточек_запросов_отбор_не_касается(сделки, monkeypatch):
    monkeypatch.setattr(ix, "SOURCE", "rfq")
    for папка in ix.НЕ_НАШЕ_КП:
        assert ix.против_поля(_запись(папка)) is None
        assert len(ix.строки_цен(_запись(папка), _позиции())) == 2
    assert ix.ОТЛОЖЕНО_НАШЕ_КП == {}


def test_отложенный_файл_не_пополняет_счёт_ложных(сделки, monkeypatch):
    monkeypatch.setattr(ix.quotes, "ОТБОР_ЛОЖНЫХ", True)
    ложная = dict(_позиции()[0], item_name="Page 1 of 1", part_number="", unit="", qty=1.0,
                  _цена={"price": 1.0, "currency": "USD", "confidence": "low",
                         "note": "цена выведена делением суммы строки на количество",
                         "total": 1.0})
    # Та же строка у нашего КП без противоречия — ложная и снимается отбором:
    # значит, корпус отбор действительно задевает.
    assert len(ix.строки_цен(_запись(doc_kind.НАШЕ_ПРЕДЛОЖЕНИЕ), [ложная, *_позиции()])) == 2
    assert sum(price_store.ОТКАЗАНО.values()) == 1
    assert ix.строки_цен(_запись(doc_kind.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА), [ложная, *_позиции()]) == []
    assert sum(price_store.ОТКАЗАНО.values()) == 1
    assert ix.ОТЛОЖЕНО_НАШЕ_КП == {doc_kind.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА: [1, 3]}


def test_строка_журнала(сделки):
    ix.строки_цен(_запись(doc_kind.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА), _позиции())
    ix.строки_цен(_запись(doc_kind.ДОГОВОР), _позиции()[:1])
    ix.строки_цен(_запись(doc_kind.ДОГОВОР), _позиции()[:1])
    строка = ix.строка_против_поля()
    assert "файлов 3, строк цены 4" in строка
    assert f"{doc_kind.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА} 1/2" in строка and f"{doc_kind.ДОГОВОР} 2/2" in строка
    assert "выдуман" not in строка and "KL-7" not in строка


# ── полный разбор файла ──────────────────────────────────────────────────────

def _кп_xlsx() -> bytes:
    ряды = []
    for r, строка in enumerate(
            (["Коммерческое предложение выдуманное"],
             ["№", "Наименование", "Артикул", "Кол-во", "Ед. изм.", "Цена, USD", "Сумма, USD"],
             [1, "Клапан выдуманный", "KL-7", 2, "шт", ЦЕНА, 2 * ЦЕНА],
             [2, "Седло выдуманное", "ZC-2002", 3, "шт", 100, 300]), 1):
        ряды.append([ячейка(f"{chr(ord('A') + k)}{r}", v) for k, v in enumerate(строка)])
    return xlsx([("КП", разметка_листа(ряды), None)])


def _ссылка() -> dict:
    return {"deal": "41", "field": НАШЕ, "field_title": None, "origin": doc_folder.ПОЛЕ_СДЕЛКИ,
            "fo": {"id": "f-our", "urlMachine": "https://portal.example.test/f"},
            "our_company": None, "owner_created": "2026-09-01T10:00:00+03:00"}


@pytest.mark.parametrize("папка, строк", [(doc_kind.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА, 0),
                                          (doc_kind.ДОГОВОР, 0),
                                          (doc_kind.НАШЕ_ПРЕДЛОЖЕНИЕ, 2),
                                          (doc_kind.НЕ_ОПРЕДЕЛЕНО, 2)])
def test_полный_разбор_с_классификатором(сделки, monkeypatch, папка, строк):
    monkeypatch.setattr(ix, "КАСКАД", True)
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: _кп_xlsx())
    monkeypatch.setattr(ix, "наши_имена", lambda: ())
    ув = 0.9 if папка != doc_kind.НЕ_ОПРЕДЕЛЕНО else 0.3
    monkeypatch.setattr(ix.doc_kind, "вид_документа", lambda *a, **k: (папка, ув, "выдумано"))
    rec, items = ix.handle(_ссылка())
    assert rec["status"] == "разобран" and rec["папка_содержимого"] == папка
    # Папка файла остаётся системной: отбор снимает цену, а не переклассифицирует.
    assert rec["doc_kind"] == doc_folder.НАШЕ_ПРЕДЛОЖЕНИЕ
    assert len(ix.строки_цен(rec, items)) == строк


def test_сбой_классификатора_цену_не_теряет(сделки, monkeypatch):
    monkeypatch.setattr(ix, "КАСКАД", True)
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: _кп_xlsx())
    monkeypatch.setattr(ix, "наши_имена", lambda: ())

    def сбой(*a, **k):
        raise ValueError("выдуманный сбой")
    monkeypatch.setattr(ix.doc_kind, "вид_документа", сбой)
    rec, items = ix.handle(_ссылка())
    assert "папка_содержимого" not in rec
    assert len(ix.строки_цен(rec, items)) == 2


# ── профиль крупного файла ───────────────────────────────────────────────────

def _строки(n: int, **поле) -> list[tuple]:
    out = []
    for i in range(n):
        it = {"item_name": f"Деталь выдуманная {i}", "part_number": f"QX-{i:04d}",
              "qty": 2.0, "unit": "шт", "source_file": "Ф-крупный", "deal_id": "41",
              "company": None, "oem": ""}
        ц = {"price": 10.0 + i, "currency": "USD", "confidence": "med", "note": None,
             "total": 2 * (10.0 + i)}
        for к, v in поле.items():
            (ц if к in ц else it)[к] = v(i) if callable(v) else v
        out.append(price_store.строка(it, ц, ix.pg, price_store.ИСТОЧНИК_НАШЕ_КП,
                                      price_store.FEED_НАШЕ_КП))
    return out


def test_профиль_нашего_кп():
    п = reparse.профиль_цен({"папка_содержимого": doc_kind.НАШЕ_ПРЕДЛОЖЕНИЕ, "subkind": "xlsx",
                             "parse_path": "таблица"}, [{}] * 120, _строки(120))
    assert (п["строк"], п["позиций"]) == (120, 120)
    assert (п["код"], п["кол"], п["ед"], п["сумма"], п["разные"], п["низкая"]) == (
        100.0, 100.0, 100.0, 100.0, 100.0, 0.0)
    assert п["валюты"] == "USD 100%" and п["признаки"] == "—"


def test_профиль_прайс_листа():
    строки = _строки(200, qty=None, total=None, part_number="", price=lambda i: 5.0 + i % 3,
                     currency=lambda i: "USD" if i % 2 else "EUR", confidence="low")
    п = reparse.профиль_цен({}, [{}] * 250, строки)
    assert (п["код"], п["кол"], п["сумма"], п["низкая"]) == (0.0, 0.0, 0.0, 100.0)
    assert п["разные"] == 1.5 and п["папка"] == "(не сверялась)"
    assert п["признаки"] == "мало кол-ва, мало кода, повторы цен, смесь валют, низкая ув."


@pytest.mark.parametrize("доля, признак", [(0.4, True), (0.6, False)])
def test_порог_признака_количества(доля, признак):
    строки = _строки(100, qty=lambda i: 2.0 if i < доля * 100 else None)
    assert ("мало кол-ва" in reparse.профиль_цен({}, [{}] * 100, строки)["признаки"]) is признак


def test_печать_крупных_без_содержимого(capsys):
    крупные = [reparse.профиль_цен({"папка_содержимого": doc_kind.НЕ_ОПРЕДЕЛЕНО}, [{}] * n,
                                   _строки(n)) for n in (150, 300)]
    reparse.печать_крупных(крупные, 900)
    out = capsys.readouterr().out
    assert "файлов 2, строк 450 (50 % строк потока)" in out
    assert "выдуман" not in out and "QX-" not in out and "Ф-крупный" not in out and "41" not in out
