"""Файлы «Премий» для пересылки и их отправка в Telegram — без сети.

Корпус — синтетика дашборда (tests/fixture.py, правило 18). Проверяется то, ради чего
файлы делаются: ни одна сделка и ни один человек не теряются, суммы считаются при
заданном К, файл не несёт других данных портала, а отправка не уйдёт чужому.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import premia_files  # noqa: E402
import premia_telegram as tg  # noqa: E402

from tests import fixture  # noqa: E402


@pytest.fixture(scope="module")
def data():
    return fixture.build_premia()


# ---------------------------------------------------------------- файлы
def test_html_несёт_вкладку_и_только_массив_премий(data):
    page = premia_files.html_file(data)
    assert "function renderPremia()" in page and "renderPremia();" in page
    assert page.count("window.__PREMIA__ = {") == 1
    for other in ("__DATA__", "__COMPANY__", "__PEOPLE__", "__REPS__", "__CONTRACTS__", "__KAM__"):
        assert f"window.{other}" not in page, other
    assert data["deals"][0]["t"] in page


def test_pdf_ни_одна_сделка_и_ни_один_человек_не_теряются(data):
    doc = premia_files.doc_html(data, 0.6)
    for d in data["deals"]:
        assert f">{d['id']}<" in doc
    for uid in {u for d in data["deals"] for u, _r, _s in d["parts"] if u}:
        assert premia_files._name(data["names"], uid) in doc


def test_pdf_в_стиле_ведомости_без_выделений(data):
    doc = premia_files.doc_html(data, 0.6)
    assert "<b>" not in doc and "<strong>" not in doc
    assert "Times New Roman" in doc and "A4 landscape" in doc
    assert "background: #fff" in doc and doc.count("background") == 1


def test_суммы_людей_сходятся_с_пулом_сделок_при_к(data):
    for k in (0.6, 1.2):
        ppl = premia_files.people(data, k)
        total = sum(b["paid"] + b["real"] + b["pre"] for b in ppl)
        pool = sum(d["p1"] * k for d in data["deals"] if "big" not in d["fl"])
        assert total == pytest.approx(pool, abs=1.0)


def test_к_удваивает_пул_в_pdf(data):
    top = max(data["deals"], key=lambda d: d["p1"])
    assert premia_files._rub(top["p1"] * 0.6) in premia_files.doc_html(data, 0.6)
    assert premia_files._rub(top["p1"] * 1.2) in premia_files.doc_html(data, 1.2)


def test_прогон_пишет_три_файла_и_отказывает_без_данных_и_вне_границ_к(tmp_path, data):
    src = tmp_path / "modules.json"
    src.write_text(json.dumps({"premia": data}, ensure_ascii=False), encoding="utf-8")
    assert premia_files.main(["--data", str(src), "--out", str(tmp_path / "o")]) == 0
    assert sorted(p.name for p in (tmp_path / "o").iterdir()) == ["doc.html", "ids.txt", "premia.html"]
    assert premia_files.main(["--data", str(src), "--out", str(tmp_path / "o2"), "--k", "2"]) == 1
    src.write_text(json.dumps({"premia": None}), encoding="utf-8")
    assert premia_files.main(["--data", str(src), "--out", str(tmp_path / "o3")]) == 1


def test_рубли_разрядами_и_типографским_минусом():
    assert premia_files._rub(1234567.4) == "1 234 567"
    assert premia_files._rub(-1500) == "−1 500"


# ---------------------------------------------------------------- получатель в Telegram
H = tg.name_sha("Owner_Test")


def test_имя_получателя_сверяется_без_собаки_и_регистра():
    assert tg.name_sha("@owner_test") == H == tg.name_sha(" OWNER_TEST ")


@pytest.mark.parametrize("chat, ok", [
    ({"type": "private", "username": "owner_test", "id": 1}, True),
    ({"type": "private", "username": "someone_else", "id": 2}, False),
    ({"type": "group", "username": "owner_test", "id": 3}, False),
    ({"type": "private", "id": 4}, False),
    (None, False),
])
def test_только_личная_переписка_с_нужным_именем(chat, ok):
    assert tg.recipient_ok(chat, H) is ok


def test_сохранённая_переписка_чужая_ищется_в_свежих_сообщениях(monkeypatch):
    calls = []

    def fake(token, method, **kw):
        calls.append(method)
        if method == "getChat":
            return {"type": "private", "username": "someone_else", "id": 7}
        return [{"message": {"chat": {"type": "private", "username": "OWNER_TEST", "id": 42}}}]

    monkeypatch.setattr(tg, "_call", fake)
    assert tg.find_chat("t", "7", H) == "42"
    assert calls == ["getChat", "getUpdates"]


def test_нужного_человека_нет_нигде_отправки_нет(monkeypatch):
    monkeypatch.setattr(tg, "_call", lambda token, method, **kw: (
        {"type": "private", "username": "someone_else"} if method == "getChat" else []))
    assert tg.find_chat("t", "7", H) is None


def test_проверка_до_сборки_находит_получателя_и_ничего_не_шлёт(monkeypatch, capsys):
    class Ready:
        def __init__(self, *a):
            pass

        def namespace(self):
            return "ns"

        def get(self, ns, key):
            return b'{"tgToken": "t", "tgChat": "42"}'

    def fake(token, method, **kw):
        assert method != "sendDocument", "проверка не должна ничего отправлять"
        return {"type": "private", "username": "owner_test", "id": 42}

    monkeypatch.setattr(tg, "NotifyReader", Ready)
    monkeypatch.setattr(tg, "_call", fake)
    assert tg.main(["--check", "--to-sha256", H]) == 0
    assert "получатель найден" in capsys.readouterr().out


def test_без_файлов_и_без_проверки_прогон_не_стартует():
    with pytest.raises(SystemExit):
        tg.main(["--to-sha256", H])


def test_бот_не_настроен_ничего_не_уходит(monkeypatch, tmp_path, capsys):
    class Empty:
        def __init__(self, *a):
            pass

        def namespace(self):
            return "ns"

        def get(self, ns, key):
            return b"{}"

    monkeypatch.setattr(tg, "NotifyReader", Empty)
    monkeypatch.setattr(tg, "_call", lambda *a, **k: pytest.fail("в Telegram не ходим без бота"))
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF")
    assert tg.main(["--to-sha256", H, str(f)]) == 2
    assert "не настроен" in capsys.readouterr().out
