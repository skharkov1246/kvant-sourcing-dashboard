"""Путь заказа по шагам — в настоящем браузере (Playwright на smoke-сборке): строка списка заказов
за числом «Недели» открывает путь заказа; фокус — на «← к списку»; «← к списку» возвращает список и
фокус на ту же строку; Esc закрывает и возвращает фокус на число; Tab ходит по кругу внутри
дровера; другие заказы сделки переключают путь; «Карточка сделки» и путь из карточки («← к
сделке»); на 390 px ни страница, ни дровер не уезжают вбок. Нет Playwright или браузера — пропуск
(как tests/test_ork_drill_browser.py). Корпус придуман (CLAUDE.md, правило 18).
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

ORDER_ROW = "#drwBody table.odt tbody tr[data-odk]"


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


def _open(browser, url, w=1280):
    pg = browser.new_page(viewport={"width": w, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url + "#contracts/week")
    pg.wait_for_selector("#tab-contracts [data-od]")
    return pg, errs


def _list_with_orders(pg):
    """Число «Недели», за которым есть заказы (не только пункты сделки): кликнуть, вернуть кнопку."""
    btns = pg.locator("#tab-contracts button.odn[data-od]")
    for i in range(btns.count()):
        b = btns.nth(i)
        if not b.is_visible():
            continue
        b.click()
        pg.wait_for_selector("#drw.on")
        if pg.locator(ORDER_ROW).count():
            return b
        pg.keyboard.press("Escape")
    raise AssertionError("нет числа со списком заказов")


def test_строка_списка_открывает_путь_и_назад(browser, page_url):
    pg, errs = _open(browser, page_url)
    num = _list_with_orders(pg)
    row = pg.locator(ORDER_ROW).first
    k = row.get_attribute("data-odk")
    row.click()
    pg.wait_for_selector("#opRoot")
    assert pg.locator("#opRoot").get_attribute("data-opk") == k
    assert pg.evaluate("document.activeElement.dataset.opgo") == "list"
    assert pg.locator("#opRoot .op-steps > li").count() == 13
    o = pg.evaluate("k=>{for(const d of window.__CONTRACTS__.rows) for(const o of d.orders||[]) if(String(o.id)===k) return o;}", k)
    if o["step"] is not None:
        assert pg.locator("#opRoot li[aria-current=step] .op-dot").inner_text() == str(o["step"])
    href = pg.locator("#opRoot a.op-ext").first.get_attribute("href")
    assert href == f"https://kvantpro.bitrix24.ru/crm/type/172/details/{k}/"
    assert pg.evaluate("document.getElementById('drw').getAttribute('role')") == "dialog"
    # Tab — по кругу внутри дровера
    for _ in range(40):
        pg.keyboard.press("Tab")
        assert pg.evaluate("document.getElementById('drw').contains(document.activeElement)")
    # «← к списку» — тот же список, фокус на той же строке
    pg.locator("[data-opgo=list]").click()
    pg.wait_for_selector("#odTable")
    assert not pg.locator("#opRoot").count()
    assert pg.evaluate("document.activeElement.dataset.odk") == k
    # Enter на строке — путь; Esc — закрыть, фокус на число
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#opRoot")
    pg.keyboard.press("Escape")
    assert not pg.evaluate("document.getElementById('drw').classList.contains('on')")
    assert pg.evaluate("el=>document.activeElement===el", num.element_handle())
    assert not errs, errs
    pg.close()


def test_заказы_сделки_и_карточка(browser, page_url):
    pg, errs = _open(browser, page_url)
    k = pg.evaluate("()=>{for(const d of window.__CONTRACTS__.rows) if((d.orders||[]).length>1) return String(d.orders[0].id);}")
    pg.evaluate("k=>_opOpen(k,{})", k)
    pg.wait_for_selector("#opRoot")
    chips = pg.locator("#opRoot button.op-chip:not(.on)")
    k2 = chips.first.get_attribute("data-opk")
    chips.first.click()
    pg.wait_for_function("k=>document.getElementById('opRoot')&&document.getElementById('opRoot').dataset.opk===k", arg=k2)
    assert pg.evaluate("document.activeElement.classList.contains('op-chip')&&document.activeElement.classList.contains('on')")
    # карточка сделки, затем путь из карточки — «← к сделке»
    pg.locator("[data-opgo=card]").click()
    pg.wait_for_function("!document.getElementById('opRoot')")
    assert pg.evaluate("document.getElementById('drw').classList.contains('on')")
    pg.evaluate("k=>_opOpen(k,{back:'deal'})", k)
    pg.wait_for_selector("#opRoot")
    back = pg.locator("[data-opgo=deal]")
    assert back.inner_text().startswith("← к сделке №")
    assert pg.evaluate("document.activeElement.dataset.opgo") == "deal"
    back.click()
    pg.wait_for_function("!document.getElementById('opRoot')")
    assert pg.evaluate("document.getElementById('drw').contains(document.activeElement)")
    pg.keyboard.press("Escape")
    assert not pg.evaluate("document.getElementById('drw').classList.contains('on')")
    assert not errs, errs
    pg.close()


@pytest.mark.parametrize("w", [390, 768, 1280, 1920])
def test_ширины_без_прокрутки_вбок(browser, page_url, w):
    pg, errs = _open(browser, page_url, w)
    k = pg.evaluate("()=>{for(const d of window.__CONTRACTS__.rows) for(const o of d.orders||[]) if(o.live&&o.stepOver) return String(o.id);}")
    pg.evaluate("k=>_opOpen(k,{})", k)
    pg.wait_for_selector("#opRoot")
    m = pg.evaluate("""()=>{const d=document.getElementById('drw'), b=document.getElementById('drwBody'), r=d.getBoundingClientRect();
      return {page:document.documentElement.scrollWidth>window.innerWidth, body:b.scrollWidth>b.clientWidth,
        out:[...b.querySelectorAll('*')].filter(e=>{const q=e.getBoundingClientRect(); return q.width&&(q.right>r.right+1||q.left<r.left-1);}).length,
        small:[...b.querySelectorAll('#opRoot *')].filter(e=>[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())&&parseFloat(getComputedStyle(e).fontSize)<12).length}}""")
    assert m == {"page": False, "body": False, "out": 0, "small": 0}, m
    assert pg.locator("#opRoot .op-stuck.is-over").count() == 1
    assert not errs, errs
    pg.close()
