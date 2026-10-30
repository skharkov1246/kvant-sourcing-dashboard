"""R4 · WP-C «Разбор числа» в браузере (Playwright; на гейте без браузера — пропуск).

Приёмка раздела 7 спецификации R4 для панели (У3 и одна ширина на всех уровнях):
- ширина: 390 — весь экран, 768 — 92 %, 1280 — 980 px, 1920 — 1200 px, одна у списка, заказа и сделки;
- ничего не обрезано и нет прокрутки вбок внутри панели на 390 и 768 (замечание ревью R3);
- на 390 первая строка списка — на первом экране; шрифт в панели списка ≥ 13 px;
- крошки «Неделя › … › Заказ …», «‹ N из M ›» и [ ] проходят список по одному действию на заказ;
- Esc — на уровень вверх (заказ → список → закрыто, фокус на исходном числе); «Назад» браузера — так же;
- адрес со списком, отбором и заказом открывает те же уровни в чистом окне.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

pw = pytest.importorskip("playwright.sync_api")

ROWS = "#drwBody #odTable tbody tr"
TILE = "#tab-contracts .actile[data-of='w3']"


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    import dashboard
    from tests import fixture
    out = tmp_path_factory.mktemp("smoke") / "index.html"
    m = fixture.build_metrics()
    dashboard.write(m, {"source": "rules", "items": []}, str(out), people=fixture.build_people(),
                    **fixture.build_realization())
    return out.resolve().as_uri()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:                       # noqa: BLE001 — своя сборка Chromium не скачана
            from scripts.validate_dashboard import find_chrome
            exe, _v = find_chrome()
            if not exe:
                pytest.skip("нет браузера для Playwright")
            b = p.chromium.launch(executable_path=exe)
        yield b
        b.close()


def _open(browser, url, w=1280, h=800, hash_="#contracts/week"):
    pg = browser.new_page(viewport={"width": w, "height": h})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url + hash_)
    pg.wait_for_selector("#ctPanel-main")
    pg.wait_for_timeout(300)
    return pg, errs


W = "Math.round(document.getElementById('drw').getBoundingClientRect().width)"


@pytest.mark.parametrize("vw,want", [(390, 390), (768, 707), (1280, 980), (1920, 1200)])
def test_одна_ширина_на_всех_уровнях_и_без_обрезки(browser, page_url, vw, want):
    pg, errs = _open(browser, page_url, w=vw, h=900)
    pg.locator(TILE).click()
    pg.wait_for_selector("#drw.on")
    assert abs(pg.evaluate(W) - want) <= 1
    # ничего не уходит за край панели, прокрутки вбок нет (ни у панели, ни у обёрток таблицы)
    assert pg.evaluate("""()=>{const d=document.getElementById('drw'), r=d.getBoundingClientRect(), b=document.getElementById('drwBody');
      const out=[...d.querySelectorAll('*')].filter(e=>e.getClientRects().length&&!e.closest('.odvh,[hidden]')&&e.children.length===0&&e.textContent.trim()
        &&(e.getBoundingClientRect().right>r.right+1||e.getBoundingClientRect().left<r.left-1));
      const sc=[...b.querySelectorAll('*')].filter(e=>!e.classList.contains('odvh')&&e.clientWidth>0&&e.scrollWidth>e.clientWidth+2&&getComputedStyle(e).overflowX!=='visible');
      return out.length===0&&sc.length===0&&b.scrollWidth<=b.clientWidth;}"""), "обрезано или прокрутка вбок"
    pg.locator("#odTable [data-odgo]").first.click()
    pg.wait_for_selector("#opRoot")
    assert abs(pg.evaluate(W) - want) <= 1, "панель не прыгает список ↔ заказ"
    assert not errs, errs
    pg.close()


def test_телефон_первая_строка_на_первом_экране_и_шрифт(browser, page_url):
    pg, errs = _open(browser, page_url, w=390, h=844)
    pg.locator(TILE).click()
    pg.wait_for_selector("#drw.on")
    pg.wait_for_timeout(300)
    top = pg.evaluate(f"document.querySelector('{ROWS}').getBoundingClientRect().top")
    assert top < 844 - 40, top
    # П-6: видимый текст панели ≥ 13 px, цели касания на первом экране ≥ 44 px
    small = pg.evaluate("""()=>[...document.querySelectorAll('#drw *')].filter(e=>e.getClientRects().length&&!e.closest('.odvh,[hidden]')
      &&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())&&parseFloat(getComputedStyle(e).fontSize)<13).map(e=>e.className)""")
    assert small == [], small
    tiny = pg.evaluate("""()=>[...document.querySelectorAll('#drw button,#drw a[href],#drw summary')].filter(e=>{const q=e.getBoundingClientRect();
      return e.getClientRects().length&&!e.closest('[hidden]')&&q.bottom>0&&q.top<innerHeight&&q.height<43.5}).map(e=>e.className+':'+e.textContent.trim().slice(0,12))""")
    assert tiny == [], tiny
    assert not errs, errs
    pg.close()


def test_крошки_соседний_заказ_esc_и_назад(browser, page_url):
    pg, errs = _open(browser, page_url)
    tile = pg.locator(TILE)
    tile.click()
    pg.wait_for_selector("#drw.on")
    n = pg.locator(ROWS).count()
    assert pg.evaluate("document.activeElement.id") == "drwName"
    crumbs = pg.locator("#drwCrumbs li").all_inner_texts()
    assert crumbs[0] == "Неделя" and "W3" in crumbs[1]
    # «Почему»: сумма = число в заголовке
    title_n = int(pg.locator("#drwName").inner_text().split()[0])
    assert sum(int(x) for x in pg.locator("#drwBody .odwb b").all_inner_texts()) == title_n == n
    pg.locator("#odTable [data-odgo]").first.click()
    pg.wait_for_selector("#opRoot")
    assert pg.locator("#drwCrumbs li").all_inner_texts()[2].startswith("Заказ ")
    assert pg.locator("#drwNav .odnc").inner_text() == f"1 из {n}"
    # весь список — по одному действию на заказ
    for i in range(2, n + 1):
        pg.keyboard.press("]")
        assert pg.locator("#drwNav .odnc").inner_text() == f"{i} из {n}"
    assert pg.locator("#drwNav [data-odnav='1']").is_disabled()
    pg.locator("#drwNav [data-odnav='-1']").click()
    assert pg.locator("#drwNav .odnc").inner_text() == f"{n - 1} из {n}"
    k = pg.locator("#opRoot").get_attribute("data-opk")
    assert f"o={k}" in pg.evaluate("location.hash")
    # Esc — к списку (строка, из которой ушли, — подсвечена и в фокусе), Esc — закрыть, фокус на числе
    pg.keyboard.press("Escape")
    assert pg.locator("#odTable").count() == 1 and pg.evaluate("document.getElementById('drw').classList.contains('on')")
    assert pg.evaluate(f"document.activeElement.closest('tr').dataset.odk") == k
    assert "o=" not in pg.evaluate("location.hash")
    pg.keyboard.press("Escape")
    assert not pg.evaluate("document.getElementById('drw').classList.contains('on')")
    assert pg.evaluate("el=>document.activeElement===el", tile.element_handle())
    # «Назад» браузера = Esc: заказ → список → закрыто
    tile.click()
    pg.locator("#odTable [data-odgo]").nth(1).click()
    pg.wait_for_selector("#opRoot")
    pg.go_back()
    pg.wait_for_selector("#odTable")
    assert pg.evaluate("document.getElementById('drw').classList.contains('on')")
    pg.go_back()
    pg.wait_for_timeout(200)
    assert not pg.evaluate("document.getElementById('drw').classList.contains('on')")
    assert not errs, errs
    pg.close()


def test_адрес_открывает_те_же_уровни(browser, page_url):
    pg, errs = _open(browser, page_url, hash_="#contracts/week?n=w%3AW3&f=part.late")
    pg.wait_for_selector("#drw.on")
    late = pg.locator(ROWS).count()
    assert late > 0 and pg.locator("#drwBody [data-odf='rm:part']").count() == 1
    k = pg.locator(ROWS).first.get_attribute("data-odk")
    pg.close()
    pg, errs2 = _open(browser, page_url, hash_=f"#contracts/week?n=w%3AW3&f=part.late&o={k}")
    pg.wait_for_selector("#opRoot")
    assert pg.locator("#opRoot").get_attribute("data-opk") == k
    assert pg.locator("#drwNav .odnc").inner_text() == f"1 из {late}"
    pg.close()
    pg, errs3 = _open(browser, page_url, hash_="#contracts/week?n=w%3AW9")
    pg.wait_for_selector("#drw.on")
    assert "нет в данных на" in pg.locator("#drwBody").inner_text()
    assert not errs + errs2 + errs3
    pg.close()
