"""Чаты сделок — от имени владельца (решение 19.10.2026): второй вебхук делит темп
портала с вебхуком сборки, адрес и сессия — свои. Без сети."""
from __future__ import annotations

from bitrix_client import BitrixClient


def test_второй_вебхук_делит_темп_и_снимок_но_не_адрес():
    c = BitrixClient("https://portal.example/rest/1/aaa", min_interval=0.0)
    o = c.with_webhook("https://portal.example/rest/7/bbb/")
    assert c.base == "https://portal.example/rest/1/aaa/" and o.base == "https://portal.example/rest/7/bbb/"
    assert o._lock is c._lock and o._session is not c._session
    o._last_call = 123.0
    c._замедление = 4.0
    assert c._last_call == 123.0 and o._замедление == 4.0
    assert o.snapshot_dir == c.snapshot_dir and o.snapshot_mode == c.snapshot_mode


def test_запрос_второго_вебхука_уходит_по_его_адресу():
    c = BitrixClient("https://portal.example/rest/1/aaa", min_interval=0.0)
    o = c.with_webhook("https://portal.example/rest/7/bbb")
    адреса = []

    class Ответ:
        status_code = 200

        def json(self):
            return {"result": {"messages": []}}

    o._session.post = lambda url, **kw: адреса.append(url) or Ответ()
    c._session.post = lambda url, **kw: адреса.append("НЕ ТОТ") or Ответ()
    assert o.call("im.dialog.messages.get", {"DIALOG_ID": "chat1", "LIMIT": 50}) == {"messages": []}
    assert len(адреса) == 1 and адреса[0].startswith("https://portal.example/rest/7/bbb/im.dialog.messages")
