"""Названия сделок не лежат в открытых данных ГПУ и ЗИП (решение владельца 22.10.2026).

Репозиторий публичный (CLAUDE.md, правило 5): название сделки — это предмет и заказчик.
Сделка в закоммиченных данных — только номером в Битриксе; название видно в карточке,
за входом в портал. Проверяется:
1. ЗИП: каждое поле deal в positions.json, r1700_bitrix.json и r1700.json — номер, без текста;
   ключа deal_titles нет.
2. ГПУ: закоммиченный сид bitrix_gpu.json — не живая выгрузка и без названий. Живую
   выгрузку gpu/tools/bitrix_gpu.py делает прогон деплоя, на сайт за входом; в git ей не место.
3. Генератор r1700_bitrix.py названий не пишет ни офлайн (из позиций), ни живым режимом.

Корпус для генератора придуман (правило 18).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP = ROOT / "zip" / "data"
NUM = re.compile(r"\d+\Z")


def r1700_bitrix():
    spec = importlib.util.spec_from_file_location("_r1700_bitrix_titles", ROOT / "zip" / "tools" / "r1700_bitrix.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def deal_values(bitrix: dict) -> list:
    return [d.get("deal") for sect in ("sold", "quoted") for r in bitrix.get(sect) or [] for d in r.get("deals") or []]


# 1 ─ ЗИП ─────────────────────────────────────────────────────────────────────

def test_зип_сделки_только_номером():
    vals = [h.get("deal") for p in load(ZIP / "positions.json") for h in p.get("bitrix_price_history") or []]
    btx = load(ZIP / "r1700_bitrix.json")
    dossier = load(ZIP / "r1700.json")
    vals += deal_values(btx) + deal_values(dossier["own"]["bitrix"])
    vals += [d.get("deal") for p in dossier.get("parts") or [] for d in p.get("bitrix_deals") or []]
    assert vals, "в данных ЗИП не нашлось ни одной сделки — проверка ничего не проверяет"
    bad = [v for v in vals if not NUM.match(str(v or ""))]
    assert not bad, f"{len(bad)} значений deal не номер сделки — в открытых данных только номер (deal_ref)"
    assert "deal_titles" not in btx and "deal_titles" not in dossier["own"]["bitrix"]


# 2 ─ ГПУ ─────────────────────────────────────────────────────────────────────

def test_сид_гпу_без_названий_сделок():
    seed = load(ROOT / "gpu" / "data" / "bitrix_gpu.json")
    hint = ("закоммичена живая выгрузка bitrix_gpu.py — верните сид: "
            "git checkout origin/main -- gpu/data/bitrix_gpu.json")
    assert seed.get("live") is False, hint
    for d in (seed.get("deals") or []) + (seed.get("adjacent") or []):
        assert not d.get("title"), hint
        assert all(not r.get("title") for r in d.get("rfqs") or []), hint


# 3 ─ генератор r1700_bitrix.py ────────────────────────────────────────────────

def test_номер_сделки_из_строки_с_названием():
    m = r1700_bitrix()
    assert m.deal_ref("8502 419. Насос-образец для стенда (270)") == "8502"
    assert m.deal_ref("9318 443. (312) Придуманная поставка") == "9318"
    assert m.deal_ref(" 77 ") == "77"
    assert m.deal_ref("Сделка: Придуманное название") == ""
    assert m.deal_ref("12ab") == "" and m.deal_ref(None) == ""


def test_офлайн_режим_не_переносит_названия(tmp_path, monkeypatch):
    m = r1700_bitrix()
    (tmp_path / "positions.json").write_text(json.dumps([{
        "id": 1, "name": "Фильтр-образец Caterpillar", "model": "R1700", "bitrix_status": "продавали",
        "bitrix_price_history": [{"date": "2031-01-01", "deal": "8502 419. Насос-образец для стенда (270)",
                                  "client": "Заказчик-образец", "status": "контракт"}],
    }], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "DATA", tmp_path)
    sold, quoted = m.from_positions()
    assert [d["deal"] for d in sold[0]["deals"]] == ["8502"] and not quoted
    assert "Насос-образец" not in json.dumps(sold, ensure_ascii=False)


def test_живой_режим_не_пишет_названия_сделок(monkeypatch):
    m = r1700_bitrix()

    class Клиент:
        def __init__(self, url):
            pass

        def list_items(self, entity, **kw):
            return [{"id": 5, "title": "Запрос R1700 фильтр", "stageId": "NEW", "createdTime": "2031-01-01"}]

        def deal_stages(self):
            return {"C1:WON": {"name": "Выиграна", "sem": "S"}}

        def list_paged(self, method, params):
            if method == "crm.deal.list":
                return [{"ID": "77", "TITLE": "Сделка-образец Caterpillar для стенда", "STAGE_ID": "C1:WON",
                         "OPPORTUNITY": "1", "CURRENCY_ID": "RUB", "DATE_CREATE": "2031-01-02T00:00:00"}]
            return []

    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example/rest/1/придуманный/")
    monkeypatch.setitem(sys.modules, "bitrix_client", types.SimpleNamespace(BitrixClient=Клиент))
    res = m.live()
    assert [d["id"] for d in res["deals"]] == ["77"] and res["deals"][0]["stage"] == "Выиграна"
    assert "Сделка-образец" not in json.dumps(res, ensure_ascii=False)
