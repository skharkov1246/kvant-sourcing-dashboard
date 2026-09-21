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
    monkeypatch.setattr(vd, "find_chrome", lambda: ("/bin/true", "Chrome 141 (тест)"))
    calls = []

    def run(chrome, path, extra, timeout, headless="--headless=new"):
        calls.append((Path(path).name, timeout))
        return answers(Path(path))

    monkeypatch.setattr(vd, "_chrome_run", run)
    return calls


def test_бюджет_времени_растёт_с_размером_но_остаётся_небольшим(tmp_path, monkeypatch):
    # замер 17.09.2026: 10,4 МБ со всеми вкладками отдают DOM за ~3 с, 14 МБ — за 4,3 с.
    # Бюджет обязан быть кратным запасом к этому, а не сотнями секунд: длинный бюджет
    # не приближает результат, он только удлиняет зависание.
    calls = _fake_runs(monkeypatch, lambda p: (PAGE * 40, ""))
    errors, warns = [], []
    vd.check_browser(_page(tmp_path, size_mb=8), errors, warns)
    assert not errors
    assert 45 <= calls[0][1] <= 120


def test_старый_headless_не_используется(tmp_path, monkeypatch):
    # --headless=old удалён из Chrome ≥132: rc=1 и пустой stdout за 0,03 с. Пока он
    # стоял последней «запасной» попыткой, проверка рендера боевой страницы не
    # выполнялась вовсе — при этом деплой считал её пройденной.
    modes = []
    monkeypatch.setattr(vd, "find_chrome", lambda: ("/bin/true", "Chrome 141 (тест)"))
    monkeypatch.setattr(vd, "_chrome_run",
                        lambda chrome, path, extra, timeout, headless="--headless=new":
                        (modes.append(headless), ("", "пусто"))[1])
    errors, warns = [], []
    vd.check_browser(_page(tmp_path), errors, warns)
    assert modes and all(m == "--headless=new" for m in modes)


def test_dom_напечатанный_зависшим_браузером_не_выбрасывается(tmp_path, monkeypatch):
    # Chrome печатает DOM и иногда не завершается (фоновые процессы профиля).
    # Раньше в этом случае вывод выбрасывался вместе с TimeoutExpired, и проверка
    # молча превращалась в «браузер ничего не отдал» — так она и не работала с 14.09.
    import subprocess
    monkeypatch.setattr(vd, "find_chrome", lambda: ("/bin/true", "Chrome 141 (тест)"))

    def run(cmd, capture_output=True, text=True, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout, output=PAGE * 40, stderr="висит")

    monkeypatch.setattr(subprocess, "run", run)
    errors, warns = [], []
    vd.check_browser(_page(tmp_path), errors, warns)
    assert not errors                                  # DOM получен, рендер проверен
    assert any("не завершился" in w for w in warns)     # но про зависание сказано вслух


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
              '<div id="tab-reps"><section><div class="secttl">' + vd._TAB_FAIL + '</div></section></div>'
              + "<span>наполнитель</span>" * 4000 + "</body></html>")
    _fake_runs(monkeypatch, lambda p: (broken, ""))
    errors, warns = [], []
    vd.check_browser(_page(tmp_path), errors, warns)
    assert any("reps" in e for e in errors)


def test_временный_файл_с_вкладками_не_остаётся_на_диске(tmp_path, monkeypatch):
    _fake_runs(monkeypatch, lambda p: (PAGE * 40, ""))
    page = _page(tmp_path)
    vd.check_browser(page, [], [])
    assert not (tmp_path / "index.tabs.html").exists()


def test_браузер_выбирается_по_ответу_на_версию_а_не_по_наличию_файла(monkeypatch):
    # snap-обёртка chromium есть в PATH и при запуске ВИСИТ, ничего не печатая.
    # Из-за неё проверка рендера боевой страницы не работала: валидатор брал первый
    # попавшийся файл и потом молча ждал его до таймаута.
    import subprocess
    monkeypatch.setattr(vd, "CHROME_CANDIDATES", ["", "зависающий", "рабочий"])
    monkeypatch.setattr(vd.shutil, "which", lambda c: "/usr/bin/" + c)

    def run(cmd, capture_output=True, text=True, timeout=None):
        if cmd[0].endswith("зависающий"):
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, 0, "Google Chrome 141.0.0.0\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    path, version = vd.find_chrome()
    assert path == "/usr/bin/рабочий"
    assert "141" in version


def test_если_ни_один_браузер_не_отвечает_проверка_пропускается_с_понятным_текстом(tmp_path, monkeypatch):
    monkeypatch.setattr(vd, "find_chrome", lambda: (None, None))
    errors, warns = [], []
    vd.check_browser(_page(tmp_path), errors, warns)
    assert not errors
    assert any("не отвечают" in w for w in warns)


def test_в_журнал_попадает_какой_браузер_и_какая_версия(tmp_path, monkeypatch):
    # без этой строки диагностика «почему не отдал DOM» стоит отдельного расследования
    _fake_runs(monkeypatch, lambda p: (PAGE * 40, ""))
    notes = []
    vd.check_browser(_page(tmp_path), [], [], notes)
    assert any("Chrome 141" in n for n in notes)


def test_разбивка_веса_по_полям_считает_и_список_и_словарь():
    """Ужимать страницу без замера нельзя: сначала надо знать, какое поле её держит."""
    rows = [{"id": i, "title": "наименование позиции " * 8, "flag": False} for i in range(50)]
    top = {nm: sz for sz, nm in vd._field_weights(rows)}
    assert set(top) == {"id", "title", "flag"}
    assert top["title"] > top["id"] > 0            # длинный текст тяжелее числа
    bykey = {nm: sz for sz, nm in vd._field_weights({"a": rows, "b": 1})}
    assert bykey["a"] > bykey["b"]
    assert vd._field_weights([1, 2, 3]) == []      # не однотипные записи — разбивки нет
