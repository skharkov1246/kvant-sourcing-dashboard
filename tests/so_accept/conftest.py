"""Приёмка «Сорсинга» (второй круг, 30.10.2026): smoke-сборка с придуманными пресейлом, сделками без
запроса и днями дожима, браузер, пороги из config.json. Как tests/rz_accept: сборка — во временную
папку, рядом — шрифты public/fonts; нет Playwright или браузера — пропуск. Корпус придуман (правило 18).
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

UNCOVERED = [{"id": str(9100 + i), "t": f"Сделка-проба {i}", "client": f"Компания {i}", "owner": f"Менеджер {i % 3}",
              "raw": 1000 * (i + 1), "amt": f"€{i + 1}K", "cat": "Сервис", "date": f"2026-07-{10 + i:02d}",
              "sem": "в работе", "wk": "2026-07-06"} for i in range(5)]


@pytest.fixture(scope="session")
def cfg():
    return CFG


@pytest.fixture(scope="session")
def smoke(tmp_path_factory):
    import dashboard
    from tests import fixture
    out = tmp_path_factory.mktemp("so_accept") / "index.html"
    m = fixture.kp_cohorts(fixture.build_metrics())   # КП по когортам недель
    m["presale"] = fixture.build_presale(tkp=True)   # КП → ТКП по истории стадий (зонд v62)
    m["coverage"]["uncovered"] = UNCOVERED
    m["coverage"]["withoutReq"] = len(UNCOVERED)
    i = 0
    for s in m["sourcersA"]:            # дни дожима (main.py кладёт wdf) — придуманные
        for d in s["details"]:
            i += 1
            d["wdf"] = (i * 7) % 15
            d["mailN"] = 1 if i % 3 else 2
    dashboard.write(m, {"source": "rules", "items": []}, str(out), people=fixture.build_people(),
                    reps=fixture.build_reps())
    shutil.copytree(ROOT / "public" / "fonts", out.parent / "fonts", dirs_exist_ok=True)
    return out.resolve().as_uri()


@pytest.fixture(scope="module")
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
    """open_page(hash, w, h, theme) → страница с открытым «Сорсингом»."""
    ctxs = []

    def _open(h="#sourcing/today", w=1280, hh=800, theme="light"):
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
    if ok:
        return
    if enforce:
        pytest.fail(msg)
    pytest.xfail("замер (enforce=false): " + msg)
