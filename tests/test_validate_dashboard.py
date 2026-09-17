"""Тесты проверки собранного дашборда — без браузера и без сети.

Написаны после боевого прогона 17.09.2026: страница выросла до 10,7 МБ, Chromium не
уложился в отведённые 75 секунд, и проверка рендера МОЛЧА не выполнилась — в журнале
осталось предупреждение, а деплой ушёл непроверенным. Здесь закреплено поведение:
бюджет времени считается от размера страницы, а если с открытыми вкладками браузер
не успел — базовая проверка рендера всё равно должна произойти по чистой странице.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_dashboard as vd  # noqa: E402

PAGE = ("<html><body><div id=\"tab-sourcing\">данные</div><div id=\"tab-company\"></div>"
        + "<span>наполнитель</span>" * 4000 + "</body></html>")


def _page(tmp_path: Path, size_mb: int = 0) -> Path:
    p = tmp_path / "index.html"
    filler = "<!--" + ("x" * 1024 * 1024 * size_mb) + "-->" if size_mb else ""
    p.write_text(PAGE.replace("</body>", filler + "</body>"), encoding="utf-8")
    return p


def _fake_runs(monkeypatch, answers):
    """answers: функция (path) → (dom, log). Подменяет запуск браузера."""
    monkeypatch.setattr(vd, "find_chrome", lambda: "/bin/true")
    calls = []

    def run(chrome, path, extra, timeout, headless="--headless=new"):
        calls.append((Path(path).name, timeout))
        return answers(Path(path))

    monkeypatch.setattr(vd, "_chrome_run", run)
    return calls


def test_бюджет_времени_растёт_с_размером_страницы(tmp_path, monkeypatch):
    calls = _fake_runs(monkeypatch, lambda p: (PAGE * 40, ""))
    errors, warns = [], []
    vd.check_browser(_page(tmp_path, size_mb=8), errors, warns)
    assert not errors
    assert calls[0][1] >= 8 * 25          # 25 секунд на мегабайт, не фиксированные 75


def test_если_с_вкладками_не_успели_базовая_проверка_всё_равно_выполняется(tmp_path, monkeypatch):
    # страница с открытыми вкладками не отвечает, чистая — отвечает
    calls = _fake_runs(monkeypatch, lambda p: ("", "timeout") if p.name.endswith(".tabs.html")
                       else (PAGE * 40, ""))
    errors, warns = [], []
    vd.check_browser(_page(tmp_path, size_mb=1), errors, warns)
    assert not errors                                    # деплой не блокируется
    assert any("вкладки не проверены" in w for w in warns)
    assert any(name.endswith(".tabs.html") for name, _ in calls)
    assert any(not name.endswith(".tabs.html") for name, _ in calls)


def test_сломанная_вкладка_ловится_и_называется(tmp_path, monkeypatch):
    broken = ('<html><body><div id="tab-sourcing">ок</div><div id="tab-company"></div>'
              '<div id="tab-kam"><section><div class="secttl">' + vd._TAB_FAIL + '</div></section></div>'
              + "<span>наполнитель</span>" * 4000 + "</body></html>")
    _fake_runs(monkeypatch, lambda p: (broken, ""))
    errors, warns = [], []
    vd.check_browser(_page(tmp_path), errors, warns)
    assert any("kam" in e for e in errors)


def test_временный_файл_с_вкладками_не_остаётся_на_диске(tmp_path, monkeypatch):
    _fake_runs(monkeypatch, lambda p: (PAGE * 40, ""))
    page = _page(tmp_path)
    vd.check_browser(page, [], [])
    assert not (tmp_path / "index.tabs.html").exists()
