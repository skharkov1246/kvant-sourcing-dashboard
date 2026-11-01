"""Зонд v64 «Вложения писем запросов» (scripts/probe_mail_attach.py): разбор без портала —
вложения письма, сигнатура, код ошибки; полный проход на заглушке клиента. Корпус придуман (правило 18)."""
from __future__ import annotations

import importlib.util
import io
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gt" / "tools"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

spec = importlib.util.spec_from_file_location("probe_mail_attach", ROOT / "scripts" / "probe_mail_attach.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_вложения_и_сигнатура_и_код():
    assert P.вложения({"FILES": [{"id": 5, "url": "x"}, {"url": "y"}, "z"]}) == ["5"]
    assert P.вложения({}) == []
    assert P.сигнатура(b"%PDF-1.4") == "pdf" and P.сигнатура(b"<html><form action=auth>") == "страница входа"
    assert P.код(RuntimeError("ACCESS_DENIED: no rights")) == "ACCESS_DENIED"


def test_проход_на_заглушке(monkeypatch, capsys):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["№", "Позиция", "Кол-во"])
    for i in range(1, 6):
        ws.append([i, f"Позиция {i}", 2])
    buf = io.BytesIO()
    wb.save(buf)
    XL = buf.getvalue()

    class Bx:
        def __init__(self, *a, **k):
            pass

        def call(self, m, p=None, retries=None):
            if m == "crm.activity.list":
                if p["filter"][">ID"]:
                    return []
                return [{"ID": str(i), "OWNER_ID": str(i), "FILES": [{"id": 100 + i}], "CREATED": "2026-10-01"}
                        for i in range(1, 11)]
            if m == "crm.item.list":
                return {"items": [{"id": x} for x in p["filter"]["@id"]]}
            if m == "disk.file.get":
                return {"DOWNLOAD_URL": "https://x/d"}
            return {}

        def before_request(self, kind="файл"):
            pass

    import bitrix_client
    import config
    import requests
    monkeypatch.setattr(bitrix_client, "BitrixClient", Bx)
    monkeypatch.setattr(config.Settings, "load", classmethod(lambda cls: types.SimpleNamespace(bitrix_webhook_url="x")))
    monkeypatch.setattr(requests, "get", lambda url, timeout=60: types.SimpleNamespace(
        status_code=200, content=XL, headers={"Content-Disposition": 'attachment; filename="spec.xlsx"'}))
    monkeypatch.delenv("BITRIX_FILES_WEBHOOK_URL", raising=False)
    assert P.main() == 0
    out = capsys.readouterr().out
    assert "карточек с разобранным вложением 10 из 10" in out and "строк по вложениям 50" in out
