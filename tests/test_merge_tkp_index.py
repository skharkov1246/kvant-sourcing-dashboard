"""Опись входящих КП не должна уменьшаться от прогона к прогону.

Оплачено дважды. Сначала — потерей чужих охватов: слияние заменяло файл
целиком, и 17 записей об исходящих запросах вымывались каждым прогоном
(замерено трижды). Потом, 18.09.2026, — усадкой ОДНОГО охвата: прогон по
«НВН» записал 361 файл там, где прежний прогон того же охвата записал 389, и
28 записей исчезли молча, потому что прогон до них не дошёл.

Правило: счётчики прогона берутся свежие (они про этот прогон), а опись файлов
объединяется по файлу. Сколько записей пришло от прежнего прогона — видно
числом, иначе усадка снова будет незаметной.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MERGE = ROOT / "scripts/merge_tkp_index.py"
SRC = ROOT / "gt/data/bitrix_tkp_index.json"


def run(mine: Path, into: Path):
    r = subprocess.run([sys.executable, str(MERGE), "--mine", str(mine), "--into", str(into)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(into.read_text(encoding="utf-8")), r.stdout


def doc(scopes: dict) -> dict:
    return {"updated": "2026-09-18", "source": "s", "method": "m", "scopes": scopes}


def item(fid: str, status: str = "текст без цен") -> dict:
    return {"file_id": fid, "file_name": f"файл-{fid}.pdf", "origin": "сделка 1",
            "direction": "входящее", "status": status}


def test_чужой_охват_не_теряется(tmp_path):
    into = tmp_path / "i.json"
    into.write_text(json.dumps(doc({"чужой": {"inventory": [item("1")]}})), encoding="utf-8")
    mine = tmp_path / "m.json"
    mine.write_text(json.dumps(doc({"мой": {"inventory": [item("2")]}})), encoding="utf-8")
    got, _ = run(mine, into)
    assert set(got["scopes"]) == {"чужой", "мой"}
    assert [x["file_id"] for x in got["scopes"]["чужой"]["inventory"]] == ["1"]


def test_свой_охват_не_усаживается(tmp_path):
    """Главный инвариант: прогон, дошедший не до всех файлов, не стирает опись."""
    into = tmp_path / "i.json"
    into.write_text(json.dumps(doc({"НВН": {
        "state": "разобрано 185 из 298", "downloaded": 185,
        "inventory": [item(str(i)) for i in range(1, 11)]}})), encoding="utf-8")
    mine = tmp_path / "m.json"
    mine.write_text(json.dumps(doc({"НВН": {
        "state": "разобрано 169 из 270", "downloaded": 169,
        "inventory": [item(str(i)) for i in range(1, 8)]}})), encoding="utf-8")
    got, out = run(mine, into)
    sc = got["scopes"]["НВН"]
    assert len(sc["inventory"]) == 10, "опись усохла — прогон стёр то, до чего не дошёл"
    # счётчики — свежего прогона: подменять их суммой нельзя, это выдуманное число
    assert sc["downloaded"] == 169 and "169" in sc["state"]
    assert sc["inventory_kept"] == 3, "усадка должна быть видна числом"
    assert "перенесено записей" in out


def test_свежая_запись_о_файле_побеждает(tmp_path):
    into = tmp_path / "i.json"
    into.write_text(json.dumps(doc({"НВН": {"inventory": [item("5", "пусто")]}})),
                    encoding="utf-8")
    mine = tmp_path / "m.json"
    mine.write_text(json.dumps(doc({"НВН": {"inventory": [item("5", "цены найдены")]}})),
                    encoding="utf-8")
    got, _ = run(mine, into)
    inv = got["scopes"]["НВН"]["inventory"]
    assert len(inv) == 1 and inv[0]["status"] == "цены найдены"


def test_файл_без_id_различается_по_имени_и_сделке(tmp_path):
    into = tmp_path / "i.json"
    a = {"file_name": "спец.pdf", "origin": "сделка 1"}
    b = {"file_name": "спец.pdf", "origin": "сделка 2"}
    into.write_text(json.dumps(doc({"s": {"inventory": [a]}})), encoding="utf-8")
    mine = tmp_path / "m.json"
    mine.write_text(json.dumps(doc({"s": {"inventory": [b]}})), encoding="utf-8")
    got, _ = run(mine, into)
    assert len(got["scopes"]["s"]["inventory"]) == 2, "одноимённые файлы разных сделок склеились"


def test_в_описи_репозитория_нет_цен():
    """Тот же страж, что в прогоне: репозиторий публичный."""
    if not SRC.exists():
        pytest.skip("описи нет")
    text = SRC.read_text(encoding="utf-8")
    assert '"price"' not in text, "в описи появились цены — это данные контрагентов"
