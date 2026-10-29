"""«Key-аккаунты и продукты» → «Люди» → «Гигиена Bitrix»: клик по строке гигиены, за которой
стоит список сделок, открывает этот список. До 29.10.2026 отбор строк (HYGF) жил внутри
отрисовки «Людей», а обработчик клика — снаружи: клик падал «HYGF is not defined» (нашла
ревизия светлой темы). Проверка — та же страница smoke-сборки в headless Chromium. Корпус
придуман (CLAUDE.md, правило 18): fixture.build_people через настоящий people.compute.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    import dashboard
    from tests import fixture
    out = tmp_path_factory.mktemp("reps_hyg") / "index.html"
    # как smoke-сборка preflight: «Люди» есть, только когда есть люди коммерческого блока (reps)
    dashboard.write(fixture.build_metrics(), {"source": "rules", "items": []}, str(out),
                    people=fixture.build_people(), reps=fixture.build_reps(), **fixture.build_realization())
    return out.resolve().as_uri()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:                       # noqa: BLE001 — своя сборка Chromium не скачана
            from validate_dashboard import find_chrome
            exe, _v = find_chrome()
            if not exe:
                pytest.skip("нет браузера для Playwright")
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        yield b
        b.close()


def test_строка_гигиены_открывает_список_сделок(browser, page_url):
    from tests import fixture
    P = fixture.build_people()
    clickable = [h for h in P["hygiene"] if h["k"] in ("noRole", "onFired", "withAmt", "alive", "future")]
    assert clickable, "в корпусе нет строк гигиены со списком сделок — тест ничего не проверил бы"
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(page_url + "#reps/all")
    pg.wait_for_selector("#repsPanel")
    rows = pg.locator("#repsPanel tr[data-hyg]")
    assert rows.count() == len(clickable), "строка гигиены со списком сделок — кнопкой"
    for i in range(rows.count()):
        k = rows.nth(i).get_attribute("data-hyg")
        rows.nth(i).click()
        pg.wait_for_selector("#drw.on", timeout=3000)
        sub = pg.locator("#drwSub").inner_text()
        assert sub.startswith("Гигиена Bitrix · что чинить · сделок "), (k, sub)
        pg.locator("#drwX").click()
        pg.wait_for_function("!document.querySelector('#drw.on')", timeout=3000)
    assert not errs, errs
    pg.close()
