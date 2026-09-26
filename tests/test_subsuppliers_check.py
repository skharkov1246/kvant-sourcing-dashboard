"""Проверщик подтверждённых субпоставщиков (scripts/subsuppliers_check.py).

Корпус придуман (CLAUDE.md, правило 18): бренд «vydumka», компания «Придуманный
завод», адреса example.test. Последний тест гоняет проверщик по настоящему набору
data/subsuppliers/verified.json — гейт не пропустит запись без доказательства.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("subsuppliers_check", ROOT / "scripts" / "subsuppliers_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["subsuppliers_check"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _mod()
КЛЮЧИ = {"vydumka", "drugaya"}


def _запись(**правки) -> dict:
    r = {"oem_key": "vydumka", "brand": "Выдумка", "company": "Придуманный завод",
         "component": "подшипник ротора", "machines": "насос ВМ-1", "evidence_kind": "а",
         "quote": "Bearing by Pridumanny Zavod fitted as standard to VM-1",
         "sources": ["https://example.test/vm1.pdf"], "checked": "2026-01-01"}
    r.update(правки)
    return r


def _набор(**правки) -> dict:
    d = {"schema": 1, "updated": "2026-01-01", "rule": "только первичный источник",
         "records": [_запись()],
         "rejected": [{"oem_key": "vydumka", "company": "Другой завод", "component": "уплотнение",
                       "reason": "страница перепродавца", "sources": ["https://example.test/shop"]}],
         "gaps": ["каталог бренда закрыт"]}
    d.update(правки)
    return d


def _есть(фрагмент: str, d: dict) -> bool:
    return any(фрагмент in s for s in M.проверить(d, КЛЮЧИ))


def test_чистый_набор_без_нарушений():
    assert M.проверить(_набор(), КЛЮЧИ) == []


def test_схема_верхнего_уровня():
    assert _есть("schema не 1", _набор(schema=2))
    assert _есть("updated — не дата", _набор(updated="26.09.2026"))
    assert _есть("неизвестное поле", _набор(lishnee=1))
    d = _набор()
    del d["records"]
    assert _есть("нет поля records", d)


def test_обязательные_поля_записи():
    for поле in ("company", "component", "quote", "machines"):
        assert _есть(f"поля {поле}", _набор(records=[_запись(**{поле: " "})])), поле
    r = _запись()
    del r["brand"]
    assert _есть("поля brand", _набор(records=[r]))


def test_вид_доказательства_из_списка():
    assert _есть("evidence_kind", _набор(records=[_запись(evidence_kind="г")]))
    assert _есть("evidence_kind", _набор(records=[_запись(evidence_kind="a")]))  # латинская «a»
    for вид in ("а", "б", "в"):
        assert M.проверить(_набор(records=[_запись(evidence_kind=вид)]), КЛЮЧИ) == []


def test_ссылки_только_http():
    assert _есть("sources", _набор(records=[_запись(sources=[])]))
    assert _есть("sources", _набор(records=[_запись(sources=["ftp://example.test/x"])]))
    assert _есть("sources", _набор(records=[_запись(sources="https://example.test/x")]))
    assert _есть("sources", _набор(rejected=[dict(_набор()["rejected"][0], sources=["example.test"])]))


def test_цитата_длина_и_язык_узла():
    assert _есть("длиннее 300", _набор(records=[_запись(quote="x" * 301)]))
    assert M.проверить(_набор(records=[_запись(quote="x" * 300)]), КЛЮЧИ) == []
    assert _есть("не по-русски", _набор(records=[_запись(component="bearing")]))


def test_ключ_бренда_и_повторы():
    assert _есть("oem_key нет", _набор(records=[_запись(oem_key="nevedomo")]))
    assert _есть("повторяется", _набор(records=[_запись(), _запись(company="придуманный ЗАВОД")]))
    assert M.проверить(_набор(records=[_запись(), _запись(oem_key="drugaya")]), КЛЮЧИ) == []


def test_контакты_ловятся_а_номера_деталей_нет():
    assert _есть("e-mail", _набор(records=[_запись(note="пишите ivan.petrov@primer.test")]))
    assert _есть("телефон", _набор(gaps=["звонить +7 999 123-45-67"]))
    assert _есть("телефон", _набор(gaps=["тел. 8 (495) 123-45-67"]))
    чисто = _запись(quote="Volvo TAD1643VE-B, 2 x 200 kW, 96428016",
                    sources=["https://example.test/a+b/9869+0107+01d"])
    assert M.проверить(_набор(records=[чисто]), КЛЮЧИ) == []


def test_main_печатает_только_агрегаты(tmp_path, capsys):
    oem = tmp_path / "oem.json"
    oem.write_text(json.dumps({"records": [{"oem_key": "vydumka"}]}), encoding="utf-8")
    series = tmp_path / "series.json"
    series.write_text(json.dumps({"brands": [{"brand_key": "drugaya"}]}), encoding="utf-8")
    f = tmp_path / "v.json"
    d = _набор(records=[_запись(), _запись(oem_key="drugaya", evidence_kind="б")])
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    args = ["--file", str(f), "--oem", str(oem), "--series", str(series)]
    assert M.main(args) == 0
    out = capsys.readouterr().out
    assert "нарушений нет" in out and "записей: 2" in out

    плохой = copy.deepcopy(d)
    плохой["records"][0]["note"] = "пишите ivan.petrov@primer.test"
    плохой["records"][1]["evidence_kind"] = "г"
    f.write_text(json.dumps(плохой, ensure_ascii=False), encoding="utf-8")
    assert M.main(args) == 1
    out = capsys.readouterr().out
    assert "нарушений: 2" in out
    for значение in ("ivan.petrov", "primer.test", "Придуманный", "Pridumanny"):
        assert значение not in out, значение


def test_набор_в_репозитории_проходит_проверку(capsys):
    assert M.main([]) == 0, capsys.readouterr().out
