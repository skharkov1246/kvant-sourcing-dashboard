"""Приёмка «Реализации» R4 (спецификация R4, раздел 7): общая smoke-сборка, браузер, пороги.

Сборка — как в gate.yml (dashboard.write с данными tests.fixture) во временную папку, рядом —
шрифты public/fonts (без них замер шрифта врёт). Нет Playwright или браузера — пропуск.
Пороги и включение проверок — config.json; mark(...) роняет тест только при enforce, иначе
помечает xfail с замером (проверка готова, пакет-владелец ещё не сдан). Корпус придуман.
"""
from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

CFG = json.loads((Path(__file__).with_name("config.json")).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def cfg():
    return CFG


@pytest.fixture(scope="session")
def smoke(tmp_path_factory):
    import dashboard
    from tests import fixture
    out = tmp_path_factory.mktemp("rz_accept") / "index.html"
    m = fixture.build_metrics()
    m["presale"] = fixture.build_presale()
    dashboard.write(m, {"source": "rules", "items": []}, str(out), people=fixture.build_people(),
                    reps=fixture.build_reps(), advisor=fixture.build_advisor(), premia=fixture.build_premia(),
                    kaminc=fixture.build_kaminc(), **fixture.build_realization())
    shutil.copytree(ROOT / "public" / "fonts", out.parent / "fonts", dirs_exist_ok=True)
    return out.resolve().as_uri()


@pytest.fixture(scope="session")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:                               # noqa: BLE001 — своя сборка Chromium не скачана
            from scripts.validate_dashboard import find_chrome
            exe, _v = find_chrome()
            if not exe:
                pytest.skip("нет браузера для Playwright")
            b = p.chromium.launch(executable_path=exe)
        yield b
        b.close()


@pytest.fixture
def open_page(browser, smoke):
    """open_page(hash, w, h, theme) → страница с открытой «Реализацией»."""
    ctxs = []

    def _open(h="#contracts/week", w=1280, hh=800, theme="light"):
        ctx = browser.new_context(viewport={"width": w, "height": hh})
        ctx.add_init_script(f"try{{localStorage.setItem('kvant-theme','{theme}')}}catch(e){{}}")
        ctxs.append(ctx)
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(smoke + h)
        pg.wait_for_timeout(350)
        return pg
    yield _open
    for c in ctxs:
        c.close()


def mark(enforce: bool, ok: bool, msg: str):
    """Проверка пакета, который ещё не сдан: замер — в xfail, а не красный тест."""
    if ok:
        return
    if enforce:
        pytest.fail(msg)
    pytest.xfail("замер (enforce=false): " + msg)
