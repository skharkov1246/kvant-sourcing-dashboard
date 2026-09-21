"""Выгрузка СП-166 обязана пройти ВСЕ страницы, а не первую.

История. Цикл был написан как `while not items`, а не `while True`: условие
проверяется перед каждым проходом, и после первой же страницы items перестаёт
быть пустым — цикл выходил, забрав пятьдесят самых старых карточек вместо
двадцати одной тысячи (число из шапки base/fetch_rfq.py). На таблице rfq стоят
supplier_stats, brand_suppliers и все метрики поставщиков, поэтому ошибка не
портила цифру, а обесценивала её: поставщик, которому писали в этом году,
в статистику не попадал вовсе.

Портал здесь не нужен: вызовы подменяются, страницы придумываются (правило 18
CLAUDE.md — корпуса в тестах придумывают, а не копируют из базы).
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "base"))

SPEC = importlib.util.spec_from_file_location("fetch_rfq", ROOT / "base" / "fetch_rfq.py")
fetch_rfq = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_rfq)

СТРАНИЦА = 50
ВСЕГО = 127          # две полные страницы и хвост: ловит и обрыв, и лишний проход


def карточки(n: int) -> list[dict]:
    """Придуманные карточки запроса: только те поля, которые читает run()."""
    return [{
        "id": i,
        "title": f"Запрос {i}",
        "stageId": "DT166_16:NEW",
        "previousStageId": "",
        "createdTime": "2026-01-01T10:00:00+03:00",
        "updatedTime": "2026-01-02T10:00:00+03:00",
        "movedTime": "2026-01-02T10:00:00+03:00",
        "begindate": "", "closedate": "",
        "assignedById": 1, "companyId": 0, "currencyId": "EUR", "opportunity": 0,
        "parentId2": f"D_{1000 + i}",
        "ufCrm18Supplier": f"CO_{9000 + i % 7}",
        "ufCrm18SupplContact": "", "ufCrm18Brands": "",
    } for i in range(1, n + 1)]


def база(tmp_path) -> str:
    """Пустая база с таблицами ядра: run() читает companies, её создаёт схема."""
    путь = tmp_path / "kvant.db"
    con = sqlite3.connect(путь)
    con.executescript((ROOT / "base" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return str(путь)


def подменить(monkeypatch, корпус: list[dict], счётчик: list[int]):
    """Подменяет обращения к порталу: стадии, компании и постраничный список."""
    по_id = {c["id"]: c for c in корпус}

    def call(sess, base, method, payload):
        if method == "crm.status.list":
            return {"result": [{"STATUS_ID": "DT166_16:NEW", "NAME": "Request Sent"}]}
        if method == "crm.status.entity.types":
            return {"result": []}
        if method == "crm.company.list":
            нужны = payload["filter"]["@ID"]
            return {"result": [{"ID": str(i), "TITLE": f"Поставщик {i}"} for i in нужны]}
        if method == "crm.item.list":
            счётчик[0] += 1
            после = int(payload["filter"][">id"])
            дальше = sorted(i for i in по_id if i > после)[:СТРАНИЦА]
            return {"result": {"items": [по_id[i] for i in дальше]}}
        raise AssertionError(f"неожиданный метод: {method}")

    monkeypatch.setattr(fetch_rfq, "call", call)
    monkeypatch.setattr(fetch_rfq, "webhook", lambda: "https://portal.invalid/rest/1/token/")
    monkeypatch.setattr(fetch_rfq.time, "sleep", lambda *_: None)


def test_выгрузка_проходит_все_страницы(tmp_path, monkeypatch):
    счётчик = [0]
    подменить(monkeypatch, карточки(ВСЕГО), счётчик)
    итог = fetch_rfq.run(база(tmp_path), str(tmp_path / "att.json"))

    assert итог["rfq"] == ВСЕГО, (
        f"забрано {итог['rfq']} карточек из {ВСЕГО}: цикл вышел раньше времени")
    # 127 карточек по 50 — три страницы с данными плюс одна пустая на выход
    assert счётчик[0] == 4, f"страниц запрошено {счётчик[0]}, ожидалось 4"


def test_одна_страница_не_считается_полной_выгрузкой(tmp_path, monkeypatch):
    """Сторож против возврата прежней ошибки: ровно одна страница — это отказ."""
    счётчик = [0]
    подменить(monkeypatch, карточки(ВСЕГО), счётчик)
    fetch_rfq.run(база(tmp_path), str(tmp_path / "att.json"))
    assert счётчик[0] > 1, "выгрузка ограничилась первой страницей"


def test_сохранённая_выгрузка_не_ходит_в_портал(tmp_path, monkeypatch):
    """--from-raw читает .raw.json и НЕ делает ни одного crm.item.list."""
    счётчик = [0]
    корпус = карточки(ВСЕГО)
    подменить(monkeypatch, корпус, счётчик)
    out = tmp_path / "att.json"
    out.with_suffix(".raw.json").write_text(json.dumps(корпус, ensure_ascii=False),
                                           encoding="utf-8")
    итог = fetch_rfq.run(база(tmp_path), str(out), from_raw=True)
    assert итог["rfq"] == ВСЕГО
    assert счётчик[0] == 0, "при --from-raw карточки перевыгружались из портала"
