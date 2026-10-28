"""bitrix_client.stage_history(…, transitions=…): все переходы с повторными входами — из тех
же запросов. Конверт ответа придуманный (правило 18), сети нет."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from bitrix_client import BitrixClient       # noqa: E402

# две страницы crm.stagehistory.list: заказ 7 возвращается в стадию A, заказ 9 — без возврата
СТРАНИЦЫ = {
    0: {"result": {"items": [
        {"OWNER_ID": 7, "STAGE_ID": "A", "CREATED_TIME": "2026-09-01T10:00:00+03:00"},
        {"OWNER_ID": 7, "STAGE_ID": "B", "CREATED_TIME": "2026-09-02T10:00:00+03:00"},
        {"OWNER_ID": 9, "STAGE_ID": "A", "CREATED_TIME": "2026-09-02T11:00:00+03:00"},
    ]}, "next": 3},
    3: {"result": {"items": [
        {"OWNER_ID": 7, "STAGE_ID": "A", "CREATED_TIME": "2026-09-03T10:00:00+03:00"},
        {"OWNER_ID": 7, "STAGE_ID": "", "CREATED_TIME": "2026-09-03T11:00:00+03:00"},
        {"OWNER_ID": 7, "STAGE_ID": "C", "CREATED_TIME": "2026-09-04T10:00:00+03:00"},
    ]}},
}


def _клиент():
    c = BitrixClient("https://portal.example/rest/1/выдумка", min_interval=0.0)
    вызовы = []

    def конверт(method, params=None, **kw):
        вызовы.append((method, params["start"]))
        return СТРАНИЦЫ[params["start"]]
    c.call_envelope = конверт
    return c, вызовы


def test_все_переходы_с_возвратами_и_те_же_запросы():
    c, вызовы = _клиент()
    last, tr = {}, {}
    hist = c.stage_history(172, since="2026-01-01", last=last, transitions=tr)
    assert вызовы == [("crm.stagehistory.list", 0), ("crm.stagehistory.list", 3)]
    # прежний ответ — первые входы — не изменился
    assert hist == {"7": [("A", "2026-09-01T10:00:00+03:00"), ("B", "2026-09-02T10:00:00+03:00"),
                          ("C", "2026-09-04T10:00:00+03:00")],
                    "9": [("A", "2026-09-02T11:00:00+03:00")]}
    assert last == {"7": ("C", "2026-09-04T10:00:00+03:00"), "9": ("A", "2026-09-02T11:00:00+03:00")}
    # все переходы: повторный вход в A сохранён, пустая стадия пропущена
    assert [s for s, _ in tr["7"]] == ["A", "B", "A", "C"]
    assert tr["9"] == [("A", "2026-09-02T11:00:00+03:00")]


def test_без_transitions_всё_как_прежде():
    c, вызовы = _клиент()
    c2, _ = _клиент()
    assert c.stage_history(172) == c2.stage_history(172, transitions={})
    assert len(вызовы) == 2
