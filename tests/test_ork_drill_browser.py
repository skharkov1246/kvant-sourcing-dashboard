"""Список заказов за числом — в настоящем браузере (Playwright на smoke-сборке): клик по каждому
числу «Недели» (плитки, ведомость, «Ждут смежников», «дольше P80», «Сверх нормы по менеджерам»,
гигиена, «Пульс ОРК», строка доверия) открывает список, строк в нём столько же, сколько в
числе; Esc закрывает и возвращает фокус на число; Tab ходит по кругу внутри списка; строка —
карточка сделки и «← к списку»; очередь говорит «по этому сочетанию отборов пусто»; на 390 px
страница не уезжает вбок. «Месяц» и «Квартал» — строк столько, сколько n показателя.
Решение владельца 29.10.2026, «Неделя», п. 4–5. Нет Playwright или браузера — пропуск (на
гейте те же числа проверяет scripts/validate_dashboard.py). Корпус придуман (правило 18).
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

ROWS = "#drwBody table.odt tbody tr"
FIND_N = """id=>{const out=[]; const walk=o=>{if(!o||typeof o!=='object')return; if(Array.isArray(o)){o.forEach(walk);return;}
  if(o.dk===id) out.push(o.n); if(o.liveDk===id) out.push(o.live); for(const k in o) if(k!=='drill'&&k!=='orders') walk(o[k]);};
  walk(window.__CONTRACTS__&&window.__CONTRACTS__.ork); walk(window.__ORKMONTH__); return out;}"""


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


def _open(browser, url, sub="week", width=1280, sep=False):
    pg = browser.new_page(viewport={"width": width, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(f"{url}#contracts/{sub}{'/sep' if sep else ''}")
    pg.wait_for_selector("#ctPanel-sep" if sep else "#ctPanel-main")
    pg.wait_for_timeout(300)
    return pg, errs


def _expect(el) -> int:
    """Число на элементе: кнопка — её текст (у фишки — <b>)."""
    b = el.locator("b")
    return int((b.first.inner_text() if b.count() else el.inner_text()).strip())


def _clicked_rows(pg, el) -> int:
    el.scroll_into_view_if_needed()
    el.click()
    pg.wait_for_selector("#drw.on")
    return pg.locator(ROWS).count()


def _close_and_focus(pg, el) -> None:
    pg.keyboard.press("Escape")
    assert pg.locator("#drw.on").count() == 0, "Esc не закрыл список"
    assert el.evaluate("e=>document.activeElement===e"), "фокус не вернулся на число"


@pytest.mark.parametrize("sep", [False, True])
def test_каждое_число_недели_открывает_свои_заказы(browser, page_url, sep):
    pg, errs = _open(browser, page_url, sep=sep)
    panel = pg.locator("#ctPanel-sep" if sep else "#ctPanel-main")
    pg.evaluate("document.querySelectorAll('#orkHyg').forEach(d=>d.open=true)")
    btns = panel.locator("button[data-od]")
    n = btns.count()
    assert n >= (8 if sep else 40), n
    for i in range(n):
        el = btns.nth(i)
        if not el.is_visible():
            continue
        want = _expect(el)
        got = _clicked_rows(pg, el)
        assert got == want, (el.get_attribute("data-od"), want, got)
        _close_and_focus(pg, el)
    # строки «Пульса» — список человека: строк — его заказов в работе; шапка — по образцу владельца
    P = pg.evaluate(f"window.__CONTRACTS__{'.sep' if sep else ''}.ork.week.pulse.rows")
    rows = panel.locator("#orkPulseB tr[data-od]")
    assert rows.count() == len(P)
    for i in range(rows.count()):
        got = _clicked_rows(pg, rows.nth(i).locator("td").first)
        assert got == P[i]["n"]
        head = pg.locator("#drwSub").inner_text()
        assert f"{P[i]['n']} заказ" in head and "от всех живых" in head and f"сделок {P[i]['deals']}" in head
        pg.keyboard.press("Escape")
    assert not errs, errs


def test_плитки_смежники_менеджеры_и_очередь(browser, page_url):
    pg, errs = _open(browser, page_url)
    panel = pg.locator("#ctPanel-main")
    for t in ("w1", "w2", "w3", "w5"):
        tile = panel.locator(f".actile[data-of='{t}']")
        want = int(tile.locator(".v").inner_text())
        assert _clicked_rows(pg, tile) == want, t
        pg.keyboard.press("Escape")
    # «Ждут смежников»: строка — список (а не отбор очереди); «дольше P80» — свой клик
    for r in ("kam", "oss", "log", "sup"):
        tr = panel.locator(f"tr[data-adj='{r}']")
        want = _expect(tr.locator("button[data-od^='w:adj/']")) if tr.locator("button[data-od^='w:adj/']").count() else 0
        if not want:
            continue
        assert _clicked_rows(pg, tr.locator("td").first) == want, r
        assert "Ждут смежников" in pg.locator("#drwName").inner_text()
        pg.keyboard.press("Escape")
    p80 = panel.locator("tr[data-adj='sup'] button[data-od='w:p80']")
    assert _clicked_rows(pg, p80) == _expect(p80)
    pg.keyboard.press("Escape")
    # «Сверх нормы по менеджерам»: строка — его заказы сверх нормы и «ждём УПД»
    trs = panel.locator("#orkOver tr[data-who]")
    for i in range(trs.count()):
        c = trs.nth(i).locator("td")
        want = int(c.nth(1).inner_text()) + int(c.nth(3).inner_text())
        assert _clicked_rows(pg, c.first) == want
        pg.keyboard.press("Escape")
    # из списка W2 — «Пункты в очереди»: отбор очереди виден снимаемой фишкой
    _clicked_rows(pg, panel.locator(".actile[data-of='w2']"))
    pg.locator("#drwBody [data-odq]").click()
    assert pg.locator("#drw.on").count() == 0
    assert "W2 сверх нормы" in pg.locator("#orkFilt").inner_text()
    # сочетание отборов, которое ничего не оставляет, — так и сказано, со «Сбросить»
    pg.locator("#orkFilt [data-rm='code']").click()
    pg.locator("#orkChips .stchip[data-c='dealClose']").click()
    pg.locator("#orkStep").select_option("9")
    body = pg.locator("#orkQb").inner_text()
    assert "по этому сочетанию отборов пусто" in body and "по регламенту" not in body
    assert pg.locator("#orkFilt [data-rm]").count() == 2
    pg.locator("#orkFilt [data-rm='step']").click()
    assert pg.locator("#orkQb tr[data-ct]").count() >= 1, "снят один отбор — пункты вернулись"
    # «Списком заказов»: текущий отбор очереди — список, строк — сколько пунктов
    n = int(pg.locator("#orkQn").inner_text().strip("()").split()[0])
    assert _clicked_rows(pg, pg.locator("#orkQList")) == n
    pg.keyboard.press("Escape")
    assert not errs, errs


def test_клавиатура_карточка_и_отборы_в_списке(browser, page_url):
    pg, errs = _open(browser, page_url)
    row = pg.locator("#orkPulseB tr[data-od]").nth(1)
    row.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#drw.on")
    assert pg.evaluate("document.activeElement.id") == "drwX"
    assert pg.evaluate("document.getElementById('drw').getAttribute('role')") == "dialog"
    total = pg.locator(ROWS).count()
    for _ in range(total * 2 + 40):                 # Tab по кругу — фокус не уходит со списка
        pg.keyboard.press("Tab")
        assert pg.evaluate("document.getElementById('drw').contains(document.activeElement)")
    pg.locator("#drwX").focus()
    pg.keyboard.press("Shift+Tab")
    assert pg.evaluate("document.getElementById('drw').contains(document.activeElement)")
    # отбор по шагу: строк — сколько на фишке; снять — все
    chip = pg.locator("#drwBody .statleg [data-odf^='step:']").nth(1)
    want = int(chip.locator("b").inner_text())
    chip.click()
    assert pg.locator(ROWS).count() == want
    pg.locator("#drwBody [data-odf='rm:step']").click()
    assert pg.locator(ROWS).count() == total
    # строка — карточка сделки; «← к списку» — тот же список
    pg.locator(ROWS).first.focus()
    pg.keyboard.press("Enter")
    assert pg.locator("#drwBody [data-odback]").count() == 1 and pg.locator(ROWS).count() == 0
    pg.locator("#drwBody [data-odback]").click()
    assert pg.locator(ROWS).count() == total
    pg.keyboard.press("Escape")
    assert pg.evaluate("document.activeElement===document.querySelectorAll('#orkPulseB tr[data-od]')[1]")
    assert not errs, errs


@pytest.mark.parametrize("sub", ["month", "quarter"])
def test_месяц_и_квартал(browser, page_url, sub):
    pg, errs = _open(browser, page_url, sub=sub)
    els = pg.locator("#ctPanel-main [data-od^='m:'], #ctPanel-main [data-od^='q:']")
    assert els.count() >= 3
    for i in range(els.count()):
        el = els.nth(i)
        n = pg.evaluate(FIND_N, el.get_attribute("data-od"))
        assert n and _clicked_rows(pg, el) == n[0], el.get_attribute("data-od")
        pg.keyboard.press("Escape")
    assert not errs, errs


def test_телефон_390_без_сдвига_вбок(browser, page_url):
    pg, errs = _open(browser, page_url, width=390)
    assert pg.evaluate("document.documentElement.scrollWidth<=innerWidth")
    pg.locator("tr[data-adj='log'] td").first.click()
    pg.wait_for_selector("#drw.on")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.documentElement.scrollWidth<=innerWidth")
    assert pg.evaluate("Math.round(document.getElementById('drw').getBoundingClientRect().width)") == 390
    # строки — карточками: таблица не шире дровера
    assert pg.evaluate("document.querySelector('#odTable').scrollWidth<=document.getElementById('drwBody').clientWidth")
    assert not errs, errs
