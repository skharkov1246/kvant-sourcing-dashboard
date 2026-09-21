"""Проверка проверяльщика связи сайта ЗИП с базой.

Скрипт scripts/zip_db_check.py существует потому, что сайт закрыт Cloudflare
Access и снаружи не читается. Значит, ошибиться в нём некому: неверный адрес
базы или пропущенная ветка разбора дадут «всё хорошо» на сломанном сайте.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from scripts import zip_db_check as проверка

КОРЕНЬ = pathlib.Path(__file__).resolve().parents[1]


def test_адрес_базы_совпадает_с_воркером():
    """SUPA_ORIGIN продублирован в питоне и в JS — сверяем, что одинаково."""
    js = (КОРЕНЬ / "zip" / "site" / "_worker.js").read_text(encoding="utf-8")
    # Читаем код, а не комментарии (CLAUDE.md, стиль работы).
    без_комментариев = re.sub(r"(?m)^\s*//.*$", "", js)
    m = re.search(r'SUPA_ORIGIN\s*=\s*"([^"]+)"', без_комментариев)
    assert m, "в воркере сайта не нашёлся SUPA_ORIGIN"
    assert m.group(1) == проверка.SUPA_ORIGIN


def test_имя_переменной_совпадает_с_воркером():
    js = (КОРЕНЬ / "zip" / "site" / "_worker.js").read_text(encoding="utf-8")
    без_комментариев = re.sub(r"(?m)^\s*//.*$", "", js)
    assert f"env.{проверка.ПЕРЕМЕННАЯ}" in без_комментариев


@pytest.mark.parametrize(
    "запись,ждём",
    [
        ({"value": "sb_secret_живой"}, "sb_secret_живой"),
        ({"value": "*****", "type": "secret_text"}, None),   # секрет скрыт звёздами
        ({"type": "secret_text"}, None),                      # значения нет вовсе
        ({"value": ""}, None),
        ({}, None),
    ],
)
def test_значение_секрета_не_принимается_за_ключ(запись, ждём):
    assert проверка.значение(запись) == ждём


def test_переменные_терпят_пустоту():
    assert проверка.переменные({}) == {}
    assert проверка.переменные({"env_vars": None}) == {}
    assert проверка.переменные({"env_vars": {"A": None}}) == {"A": {}}


def test_новый_ключ_идёт_без_bearer(monkeypatch):
    """sb_secret_… — ключ нового вида: воркер шлёт только apikey."""
    поймано = {}

    def подмена(url, заголовки):
        поймано["url"] = url
        поймано["заголовки"] = заголовки
        return 200, b"[]"

    monkeypatch.setattr(проверка, "_запрос", подмена)
    код, _ = проверка.проверить_ключ("sb_secret_x")
    assert код == 200
    assert поймано["заголовки"]["apikey"] == "sb_secret_x"
    assert "Authorization" not in поймано["заголовки"]
    assert поймано["url"].startswith(проверка.SUPA_ORIGIN)


def test_старый_ключ_идёт_с_bearer(monkeypatch):
    поймано = {}
    monkeypatch.setattr(
        проверка, "_запрос",
        lambda url, заголовки: (поймано.update(заголовки=заголовки), (200, b"[]"))[1],
    )
    проверка.проверить_ключ("eyJhbGciOi")
    assert поймано["заголовки"]["Authorization"] == "Bearer eyJhbGciOi"
