"""Счётчики КП живут по охватам, и прогон одного листа не стирает другой.

Оплачено трижды описью входящих КП: прогон длится десятки минут, запись своего
файла поверх уносит чужой охват целиком, и 17 файлов исчезали с каждым
прогоном. У счётчиков цена ошибки выше: цифры по «Энергосети» стоят в листе
решений владельцу, и прогон по «НВН» стёр бы их молча — а сложить два охвата
нельзя, это разные заявки.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_offer_stats.json"
MERGE = ROOT / "scripts/merge_offer_stats.py"
COUNTS = ("rows_with_offer", "above_ceiling", "inside_band", "below_floor", "no_band")


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("счётчиков нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_счётчики_разложены_по_охватам():
    d = doc()
    assert isinstance(d.get("scopes"), dict) and d["scopes"], (
        "счётчики должны лежать под ключом охвата, иначе прогон другого листа их затрёт")
    for name, body in d["scopes"].items():
        assert name.strip(), "охват без имени"
        assert body.get("rows_with_offer") is not None, f"охват {name} без счёта строк"


def test_в_счётчиках_нет_ни_цен_ни_артикулов():
    """Тот же страж, что в прогоне: репозиторий публичный."""
    text = json.dumps(doc(), ensure_ascii=False)
    for key in ('"price"', '"usd"', '"pn"', '"unit_price'):
        assert key not in text, f"в счётчиках стоит {key} — это коммерческие данные контрагента"


def test_слияние_сохраняет_чужой_охват(tmp_path):
    """Главный инвариант: мой охват заменяется, чужой остаётся."""
    into = tmp_path / "stats.json"
    into.write_text(json.dumps({
        "updated": "2026-01-01", "source": "s", "method": "m",
        "scopes": {"чужой лист": {"rows_with_offer": 5, "above_ceiling": 1}},
    }, ensure_ascii=False), encoding="utf-8")
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps({
        "updated": "2026-09-18", "source": "s2", "method": "m2",
        "scopes": {"мой лист": {"rows_with_offer": 7, "above_ceiling": 2}},
    }, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(MERGE), "--mine", str(mine), "--into", str(into)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = json.loads(into.read_text(encoding="utf-8"))
    assert set(got["scopes"]) == {"чужой лист", "мой лист"}
    assert got["scopes"]["чужой лист"]["rows_with_offer"] == 5
    assert got["scopes"]["мой лист"]["rows_with_offer"] == 7
    assert got["updated"] == "2026-09-18"


def test_слияние_читает_старую_одноохватную_форму(tmp_path):
    """Иначе первый прогон после правки оставил бы документ пустым."""
    into = tmp_path / "stats.json"
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps({
        "scope": "Энергосети", "rows_with_offer": 776, "above_ceiling": 222,
        "source": "s", "method": "m",
    }, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(MERGE), "--mine", str(mine), "--into", str(into)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = json.loads(into.read_text(encoding="utf-8"))
    assert got["scopes"]["Энергосети"]["rows_with_offer"] == 776
    assert "scope" not in got["scopes"]["Энергосети"]


def test_охваты_не_складываются_и_документ_об_этом_говорит():
    d = doc()
    if len(d.get("scopes") or {}) < 2:
        assert "не складываются" in (d.get("method") or ""), (
            "в методе должно стоять, что охваты не складываются: это разные заявки")
        return
    assert "не складываются" in (d.get("method") or "")


def test_прогон_сливает_а_не_переписывает():
    """Страж на сам прогон: буквальное копирование файла запрещено."""
    wf = (ROOT / ".github/workflows/bitrix-tkp.yml").read_text(encoding="utf-8")
    assert "merge_offer_stats.py" in wf, "прогон обязан сливать счётчики, а не писать поверх"
    assert 'cp "$RUNNER_TEMP/stats.json" gt/data/ship_offer_stats.json' not in wf
