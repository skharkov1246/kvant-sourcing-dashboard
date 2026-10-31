"""Зонд v63 «Строки запроса» (scripts/probe_request_lines.py): разбор без портала — корзины,
вердикт, файловое поле, вид файла, счёт строк. Корпус придуман (CLAUDE.md, правило 18)."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gt" / "tools"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

spec = importlib.util.spec_from_file_location("probe_request_lines", ROOT / "scripts" / "probe_request_lines.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_корзины_строк():
    assert [P.корзина(n) for n in (1, 2, 5, 6, 20, 21, 100, 101, 1000, 1001)] == \
        ["1", "2–5", "2–5", "6–20", "6–20", "21–100", "21–100", "101–1000", "101–1000", "> 1000"]


def test_вердикт_ворота():
    assert P.вердикт(30, 0.8) == "показать"
    assert P.вердикт(30, 0.79) == "предварительно"
    assert P.вердикт(10, 1.0) == "предварительно"
    assert P.вердикт(9, 1.0) == "скрыть"


def test_файловое_поле_объект_или_список():
    assert P.файлы_поля(None) == []
    assert len(P.файлы_поля({"id": 1, "urlMachine": "u"})) == 1
    assert len(P.файлы_поля([{"id": 1, "urlMachine": "u"}, {"id": 2}, "x"])) == 1


def test_вид_по_сигнатуре():
    assert P.вид(b"%PDF-1.7 ...") == "pdf"
    assert P.вид(b"PK\x03\x04....") == "xlsx/zip"
    assert P.вид(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1....") == "xls/doc"
    assert P.вид(b"hello") == "прочее"


def test_строки_непустые_и_с_артикулом():
    rows = [("Лист1", 1, ["№", "Наименование", "Кол-во"]),
            ("Лист1", 2, [1, "Уплотнение 6RS-2041-11", 4]),
            ("Лист1", 3, [None, "", None]),
            ("Лист1", 4, [2, "Фильтр масляный", 1])]
    непуст, с_арт = P.строки(rows)
    assert непуст == 3
    assert 0 <= с_арт <= непуст


def test_части_смежными_срезами():
    ch = P.части(list(range(1, 101)), 10)
    assert len(ch) == 10 and ch[0][0] == 1 and ch[-1][-1] == 100
    assert sum(len(c) for c in ch) == 100


def test_строки_текста_письма():
    html = "<p>Добрый день!</p><table><tr><td>1</td><td>6RS-2041-11</td></tr><tr><td>2</td><td>Фильтр</td></tr></table>Спасибо<br>Иванов"
    непуст, tr, с_арт = P.текст_строк(html)
    assert tr == 2 and непуст >= 4 and 0 <= с_арт <= непуст
    assert P.текст_строк("") == (0, 0, 0)
