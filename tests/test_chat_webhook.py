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


def test_счётчики_снимка_общие_у_двух_вебхуков():
    """Промах снимка у вебхука владельца виден в сводке сборки: иначе живая сверка
    сочла бы себя точной, хотя чаты читались мимо снимка (ревью 19.10.2026)."""
    c = BitrixClient("https://portal.example/rest/1/aaa", min_interval=0.0)
    o = c.with_webhook("https://portal.example/rest/7/bbb")
    o.snapshot_misses += 3
    o.snapshot_hits += 1
    assert c.snapshot_stats()["misses"] == 3 and c.snapshot_stats()["hits"] == 1



def test_второй_вебхук_читает_где_первому_отказ(monkeypatch):
    """Решение 22.10.2026: владелец не участник большинства чатов реализации — где ему
    отказ в доступе, читает вебхук руководителя реализации; темп портала общий."""
    from bitrix_client import ЧтениеЧатов, chat_reader_client
    monkeypatch.setenv("BITRIX_CHAT_WEBHOOK_URL", "https://portal.example/rest/7/bbb")
    monkeypatch.setenv("BITRIX_CHAT_WEBHOOK_URL_2", "https://portal.example/rest/9/ccc")
    c = BitrixClient("https://portal.example/rest/1/aaa", min_interval=0.0)
    чтение, кто = chat_reader_client(c)
    assert isinstance(чтение, ЧтениеЧатов) and кто == "вебхуки владельца → руководителя реализации"
    владелец, руководитель = чтение.клиенты
    assert владелец._lock is c._lock and руководитель._lock is c._lock

    def отказ(*_a, **_k):
        raise RuntimeError("im.dialog.messages.get: ACCESS_ERROR You do not have access")
    владелец.call = отказ
    руководитель.call = lambda method, params=None, **kw: {"messages": [{"id": 1}]}
    assert чтение.call("im.dialog.messages.get", {"DIALOG_ID": "chat5"}) == {"messages": [{"id": 1}]}
    assert чтение.по_вебхуку == [0, 1]
    # отказ последнего — наружу: chat_activity считает его «нет доступа»
    руководитель.call = отказ
    try:
        чтение.call("im.dialog.messages.get", {"DIALOG_ID": "chat6"})
        raise AssertionError("отказ последнего вебхука проглочен")
    except RuntimeError as e:
        assert "ACCESS" in str(e)
    # не отказ в доступе (лимит, сеть) — следующий вебхук не зовётся
    владелец.call = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("QUERY_LIMIT_EXCEEDED"))
    руководитель.call = lambda *a, **k: (_ for _ in ()).throw(AssertionError("второй позван зря"))
    try:
        чтение.call("im.dialog.messages.get", {"DIALOG_ID": "chat7"})
    except RuntimeError as e:
        assert "QUERY_LIMIT" in str(e)
    чтение.limit_wait_budget = 60
    assert владелец.limit_wait_budget == 60 and руководитель.limit_wait_budget == 60


def test_один_или_ни_одного_вебхука_чатов(monkeypatch):
    from bitrix_client import chat_reader_client
    c = BitrixClient("https://portal.example/rest/1/aaa", min_interval=0.0)
    monkeypatch.delenv("BITRIX_CHAT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("BITRIX_CHAT_WEBHOOK_URL_2", raising=False)
    assert chat_reader_client(c) == (c, "вебхук сборки")
    monkeypatch.setenv("BITRIX_CHAT_WEBHOOK_URL_2", "https://portal.example/rest/9/ccc")
    клиент, кто = chat_reader_client(c)
    assert кто == "вебхук руководителя реализации" and клиент.base == "https://portal.example/rest/9/ccc/"
    monkeypatch.setenv("BITRIX_CHAT_WEBHOOK_URL_2", "не-вебхук")
    assert chat_reader_client(c)[1] == "вебхук сборки"


def test_ответивший_вебхук_чата_зовётся_первым_на_следующих_страницах():
    """Ревью 28.10.2026: история — до шести страниц чата; владельцу отказ на первой — со второй
    сразу вебхук руководителя, без лишнего запроса к порталу. Другой чат — снова по очереди."""
    from bitrix_client import ЧтениеЧатов
    вызовы = []

    class Вебхук:
        def __init__(self, имя, доступ):
            self.имя, self.доступ = имя, доступ

        def call(self, method, params=None, **kw):
            вызовы.append((self.имя, params["DIALOG_ID"]))
            if params["DIALOG_ID"] not in self.доступ:
                raise RuntimeError("ACCESS_ERROR")
            return {"messages": []}

    чтение = ЧтениеЧатов([Вебхук("владелец", {"chat2"}), Вебхук("руководитель", {"chat1", "chat2"})],
                          ["владельца", "руководителя"])
    for last in (None, 900, 850):
        чтение.call("im.dialog.messages.get", {"DIALOG_ID": "chat1", "LAST_ID": last})
    assert вызовы == [("владелец", "chat1"), ("руководитель", "chat1"), ("руководитель", "chat1"),
                      ("руководитель", "chat1")]
    вызовы.clear()
    чтение.call("im.dialog.messages.get", {"DIALOG_ID": "chat2"})
    assert вызовы == [("владелец", "chat2")] and чтение.по_вебхуку == [1, 3]
    # запомненный потерял доступ — следующий по очереди, и запоминается он
    чтение.клиенты[1].доступ = {"chat2"}
    чтение.клиенты[0].доступ = {"chat1", "chat2"}
    вызовы.clear()
    чтение.call("im.dialog.messages.get", {"DIALOG_ID": "chat1"})
    чтение.call("im.dialog.messages.get", {"DIALOG_ID": "chat1"})
    assert вызовы == [("руководитель", "chat1"), ("владелец", "chat1"), ("владелец", "chat1")]
