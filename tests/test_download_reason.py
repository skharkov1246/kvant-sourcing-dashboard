"""Закачка файла: повторы и НАЗВАННАЯ причина неудачи.

Замер 23.09.2026 по всей базе: 999 файлов со статусом «не скачался» и НИ ОДНОЙ
причины — ошибка глоталась целиком (`except Exception: pass`), а попытка была
одна. По такому статусу чинить нечего: не видно, протух ли токен, отказал ли
портал, пришла ли вместо файла страница входа.

Сеть здесь подставная: настоящих запросов проверки не делают (правило 18).
"""
from __future__ import annotations

import pytest

from library import indexer


class Ответ:
    def __init__(self, код: int, тело: bytes = b"x" * 500):
        self.status_code, self.content = код, тело


@pytest.fixture(autouse=True)
def без_пауз(monkeypatch):
    """Выдержку между попытками не ждём: проверка не должна идти секунды."""
    monkeypatch.setattr(indexer.time, "sleep", lambda _с: None)


def test_сетевая_осечка_лечится_повтором(monkeypatch):
    """Одна попытка означала: любая осечка — «не скачался» навсегда."""
    попытки = {"n": 0}

    def get(*_a, **_k):
        попытки["n"] += 1
        if попытки["n"] < 3:
            raise ConnectionError("оборвалось")
        return Ответ(200)

    monkeypatch.setattr(indexer.requests, "get", get)
    monkeypatch.setattr(indexer, "is_login_page", lambda _b: False)
    тело, почему = indexer.скачать_адрес("https://пример/1")
    assert тело and почему == "" and попытки["n"] == 3


def test_код_404_не_повторяется(monkeypatch):
    """404 не станет двухсотым, сколько его ни проси: повтор — трата прогона."""
    попытки = {"n": 0}

    def get(*_a, **_k):
        попытки["n"] += 1
        return Ответ(404, b"")

    monkeypatch.setattr(indexer.requests, "get", get)
    тело, почему = indexer.скачать_адрес("https://пример/1")
    assert тело is None and почему == "код 404" and попытки["n"] == 1


def test_код_429_повторяется_и_причина_названа(monkeypatch):
    """429 у закачки — блокировка метода по времени: ждать минуты, а не 1–2 с.

    Терпение кончается бюджетом ожидания клиента, и причина это называет."""
    попытки = {"n": 0}

    def get(*_a, **_k):
        попытки["n"] += 1
        return Ответ(429, b"")

    monkeypatch.setattr(indexer.requests, "get", get)
    тело, почему = indexer.скачать_адрес("https://пример/1")
    assert тело is None and почему.startswith("код 429")
    assert "бюджета ожидания" in почему
    assert попытки["n"] > indexer.ПОПЫТОК_ЗАКАЧКИ, "лимит пережидали числом попыток, а не временем"


def test_код_503_переждан_и_файл_получен(monkeypatch):
    """Частота (503) проходит сама, если подождать: файл не теряется."""
    ответы = [Ответ(503, b"")] * 5 + [Ответ(200)]
    monkeypatch.setattr(indexer.requests, "get", lambda *_a, **_k: ответы.pop(0))
    monkeypatch.setattr(indexer, "is_login_page", lambda _b: False)
    тело, почему = indexer.скачать_адрес("https://пример/1")
    assert тело and почему == ""


def test_страница_входа_повтором_не_лечится(monkeypatch):
    """Токен протух: тот же адрес отдаст ту же страницу входа."""
    попытки = {"n": 0}

    def get(*_a, **_k):
        попытки["n"] += 1
        return Ответ(200, b"<html>login</html>" * 20)

    monkeypatch.setattr(indexer.requests, "get", get)
    monkeypatch.setattr(indexer, "is_login_page", lambda _b: True)
    тело, почему = indexer.скачать_адрес("https://пример/1")
    assert тело is None and почему == "вместо файла страница входа"
    assert попытки["n"] == 1, "страницу входа пробовали повторно — трата прогона"


def test_причина_доезжает_до_записи_файла(monkeypatch):
    """Без этого статус «не скачался» стоит у 999 файлов и молчит."""
    monkeypatch.setattr(indexer.requests, "get", lambda *_a, **_k: Ответ(403, b""))
    rec: dict = {}
    assert indexer.download({"urlMachine": "https://пример/1"}, rec) is None
    assert "403" in rec["reason"], rec["reason"]
    assert "urlMachine" in rec["reason"], "не видно, какая из ссылок отказала"


def test_вложение_без_единой_ссылки_названо_отдельно():
    rec: dict = {}
    assert indexer.download({}, rec) is None
    assert rec["reason"] == "ссылок на файл нет вовсе"


def test_в_причину_не_попадает_адрес_файла(monkeypatch):
    """Правило 17: журнал публичный. Адрес несёт одноразовый токен портала."""
    monkeypatch.setattr(indexer.requests, "get", lambda *_a, **_k: Ответ(500, b""))
    rec: dict = {}
    indexer.download({"urlMachine": "https://portal.example/rest/1/SEKRET/file.xlsx"}, rec)
    assert "SEKRET" not in rec["reason"] and "http" not in rec["reason"], rec["reason"]


def test_код_отказа_портала_без_описания():
    e = RuntimeError("disk.file.get: ERROR_NOT_FOUND Файл 123456 не найден")
    assert indexer.код_ошибки_портала(e) == "ERROR_NOT_FOUND"
    assert indexer.код_ошибки_портала(ValueError("сеть")) == "ValueError"
