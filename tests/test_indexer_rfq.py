"""Разбор вложений карточек запросов: берём чужое, не берём своё.

Корпус придуман (правило 18). Портал не опрашивается: bx_all подменяется.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for каталог in ("library", "scripts"):
    sys.path.insert(0, str(ROOT / каталог))

psycopg2 = pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")
_spec = importlib.util.spec_from_file_location("indexer", ROOT / "library" / "indexer.py")
ix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ix)

ОФФЕР = "ufCrm18_1731179998"          # Offer from supplier
КП = "ufCrm18_1700698211875"          # КП поставщика


def карточки(monkeypatch, items):
    monkeypatch.setattr(ix, "bx_all", lambda *a, **k: items)


def test_берутся_только_файлы_поставщика(monkeypatch):
    """Наш «Request file» лежит в той же карточке и попасть в разбор не должен:
    разобрать его как котировку значит объявить прокотированным свой же запрос."""
    карточки(monkeypatch, [{
        "id": 101,
        ОФФЕР: [{"id": 1, "urlMachine": "https://portal.example.test/f/1"}],
        ix.ПОЛЕ_ЗАПРОСА: [{"id": 2, "urlMachine": "https://portal.example.test/f/2"}],
    }])
    refs = ix.collect_refs_rfq(30)
    assert len(refs) == 1
    assert refs[0]["field"] == ОФФЕР
    assert all(r["field"] != ix.ПОЛЕ_ЗАПРОСА for r in refs)


def test_происхождение_отличает_запрос_от_сделки(monkeypatch):
    """lib_files.deal_id хранит и сделки, и карточки запросов — различает origin."""
    карточки(monkeypatch, [{"id": 77, КП: [{"id": 9, "urlMachine": "https://x.test/9"}]}])
    refs = ix.collect_refs_rfq(30)
    assert refs[0]["origin"] == "поле запроса"
    assert refs[0]["deal"] == "77"


def test_файл_без_ссылки_пропускается(monkeypatch):
    """Без urlMachine скачать нечего: такую запись брать в работу бессмысленно."""
    карточки(monkeypatch, [{"id": 5, ОФФЕР: [{"id": 3}, {"id": 4, "urlMachine": "https://x.test/4"}]}])
    refs = ix.collect_refs_rfq(30)
    assert [r["fo"]["id"] for r in refs] == [4]


def test_одиночное_поле_и_список_читаются_одинаково(monkeypatch):
    """Bitrix отдаёт файловое поле то словарём, то списком словарей."""
    карточки(monkeypatch, [{"id": 1, ОФФЕР: {"id": 8, "urlMachine": "https://x.test/8"}},
                           {"id": 2, ОФФЕР: [{"id": 9, "urlMachine": "https://x.test/9"}]}])
    assert len(ix.collect_refs_rfq(30)) == 2


def test_источник_по_умолчанию_прежний():
    """Прогон без SOURCE обязан вести себя как раньше: сделки, а не запросы."""
    assert ix.SOURCE == "deals"


def test_список_полей_общий_с_замерами():
    """Два списка разошлись бы молча: разбирали бы одно, а считали другое."""
    import quote_coverage as qc
    assert ix.ПОЛЯ_КП is qc.ПОЛЯ_КП and ix.ПОЛЕ_ЗАПРОСА == qc.ПОЛЕ_ЗАПРОСА
