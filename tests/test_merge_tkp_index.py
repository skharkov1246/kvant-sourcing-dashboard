"""Слияние описи по охватам: своё влить, чужое не потерять.

Оплачено двумя потерянными прогонами 17.09.2026. Сперва push отбился «fetch
first» и готовая опись осталась только в артефакте. Потом перебазирование
упёрлось в конфликт — раннер и сессия правят один и тот же файл, и текстового
слияния у него быть не может. Правильное слияние смысловое: охват прогона
обновляется, чужие переносятся как есть.

Корпуса придуманные (правило 18 CLAUDE.md).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from merge_tkp_index import LEGACY, merge, scopes_of  # noqa: E402


def doc(scopes: dict) -> dict:
    return {"updated": "2026-09-18", "source": "s", "method": "m", "scopes": scopes}


def sc(files: int) -> dict:
    return {"updated": "2026-09-18", "deals": 1, "files": files,
            "inventory": [{"file_id": str(i)} for i in range(files)]}


def test_свой_охват_обновляется_чужой_остаётся():
    mine = doc({"слово «Энергосети»": sc(93)})
    into = doc({"слово «ЛУКОЙЛ»": sc(1998), "слово «Энергосети»": sc(5)})
    out, updated, kept = merge(mine, into)
    assert updated == ["слово «Энергосети»"]
    assert kept == ["слово «ЛУКОЙЛ»"]
    assert out["scopes"]["слово «Энергосети»"]["files"] == 93
    assert out["scopes"]["слово «ЛУКОЙЛ»"]["files"] == 1998


def test_чужой_охват_не_затирается_даже_если_мой_пустее():
    """Прогон по одной сделке не должен уносить картину по всему заказчику."""
    mine = doc({"сделка 16386": sc(3)})
    into = doc({"слово «ЛУКОЙЛ»": sc(1998)})
    out, _, kept = merge(mine, into)
    assert kept == ["слово «ЛУКОЙЛ»"]
    assert len(out["scopes"]) == 2


def test_старый_формат_переносится_в_отдельный_охват():
    """Иначе прежние 1998 файлов исчезли бы молча при первом же слиянии."""
    legacy = {"updated": "2026-09-17", "deals": 137, "files": 1998,
              "inventory": [{"file_id": "1"}]}
    mine = doc({"слово «НВН»": sc(298)})
    out, _, kept = merge(mine, legacy)
    assert kept == [LEGACY]
    assert out["scopes"][LEGACY]["files"] == 1998
    assert out["scopes"]["слово «НВН»"]["files"] == 298


def test_пустой_приёмник_не_ломает_слияние():
    out, updated, kept = merge(doc({"слово «НВН»": sc(2)}), {})
    assert updated == ["слово «НВН»"] and kept == []
    assert len(out["scopes"]) == 1


def test_старый_формат_без_описи_не_создаёт_пустого_охвата():
    out, _, kept = merge(doc({"a": sc(1)}), {"updated": "x", "inventory": []})
    assert kept == []
    assert LEGACY not in out["scopes"]


def test_scopes_of_не_путает_форматы():
    assert scopes_of({"scopes": {"a": {}}}) == {"a": {}}
    assert scopes_of({}) == {}
    assert scopes_of({"inventory": [{"x": 1}]}).keys() == {LEGACY}


def test_пустая_моя_опись_отказывает_а_не_затирает(tmp_path):
    """Сквозная проверка: без охвата скрипт обязан выйти с ошибкой и НЕ писать."""
    into = tmp_path / "into.json"
    into.write_text(json.dumps(doc({"слово «ЛУКОЙЛ»": sc(1998)}),
                               ensure_ascii=False), encoding="utf-8")
    mine = tmp_path / "mine.json"
    mine.write_text("{}", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/merge_tkp_index.py"),
         "--mine", str(mine), "--into", str(into)],
        capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    kept = json.loads(into.read_text(encoding="utf-8"))
    assert kept["scopes"]["слово «ЛУКОЙЛ»"]["files"] == 1998, "приёмник затёрт"


def test_сквозной_прогон_пишет_итог(tmp_path):
    into = tmp_path / "into.json"
    into.write_text(json.dumps(doc({"слово «ЛУКОЙЛ»": sc(1998)}),
                               ensure_ascii=False), encoding="utf-8")
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps(doc({"слово «Энергосети»": sc(93)}),
                               ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/merge_tkp_index.py"),
         "--mine", str(mine), "--into", str(into)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(into.read_text(encoding="utf-8"))
    assert set(out["scopes"]) == {"слово «ЛУКОЙЛ»", "слово «Энергосети»"}
    assert "охватов 2, файлов 2091" in r.stdout
