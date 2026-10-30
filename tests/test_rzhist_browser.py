"""«±N за неделю» на «Неделе» в браузере (Playwright; на гейте без браузера — пропуск).

Три сборки одного корпуса: с историей (30 придуманных дней, fixture.rz_hist_points), с одной
сегодняшней точкой и без раздела rzHist (прежняя сборка). Проверяется:
  · у плиток, частей «Что сделать сегодня», «Порядка в карточках» и шага 13 — дельта словами с
    единицей и направлением; «хуже» — цветом тревоги (число и слово), «лучше» — нет; строки-счётчики
    справа — без дельты (первый экран 1280 не растёт);
  · числа рядом с дельтой — те же, что в точке истории (два способа: страница и rzhist.point);
  · ряд 12 недель — только при двух точках и больше; короче — «история с дд.мм»;
  · без раздела — прежний вид (у «сорван» — восстановление Д-12), без «Смотреть как» дельт нет.
Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import copy
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

pw = pytest.importorskip("playwright.sync_api")

ALARM = "rgb(192, 31, 43)"


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    import dashboard
    import rzhist
    from tests import fixture
    base = fixture.build_realization(hist=False)
    m = fixture.build_metrics()
    pt = rzhist.point(base["contracts"], base["dealctx"], fixture.CT_TODAY)
    out = {}
    for name in ("hist", "one", "none"):
        r = copy.deepcopy(base)
        if name == "hist":
            r["contracts"]["rzHist"] = rzhist.page(rzhist.merge(fixture.rz_hist_points(pt), pt), r["contracts"], fixture.CT_TODAY)
        elif name == "one":
            r["contracts"]["rzHist"] = rzhist.page(rzhist.merge(None, pt), r["contracts"], fixture.CT_TODAY)
        f = tmp_path_factory.mktemp("rzh_" + name) / "index.html"
        dashboard.write(m, {"source": "rules", "items": []}, str(f), people=fixture.build_people(), **r)
        out[name] = f.resolve().as_uri()
    out["pt"] = pt
    return out


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


def _open(browser, url, w=1280, h=800):
    pg = browser.new_page(viewport={"width": w, "height": h})
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(url + "#contracts/week")
    pg.wait_for_selector("#orkWeekS", timeout=20000)
    return pg, errs


def _txt(pg, sel):
    return [re.sub(r"\s+", " ", t).strip() for t in pg.eval_on_selector_all(sel, "els=>els.map(e=>e.textContent)")]


def test_с_историей_дельты_словами_и_те_же_числа(pages, browser):
    pg, errs = _open(browser, pages["hist"])
    pt = pages["pt"]["main"]
    late = _txt(pg, "#wkTLate .rzwk")
    assert late[0] == "+3 заказа за неделю, хуже" and late[1] == "+1 сделка за неделю, хуже"
    assert _txt(pg, "#wkTW2 .rzwk") == ["+2 заказа за неделю, хуже"]
    assert _txt(pg, "#wkTThr .rzwk") == ["−2 заказа за неделю, лучше"]
    # строки-счётчики справа — без дельты: строка не растёт, первый экран 1280 (таблица 3.10) держится
    assert not _txt(pg, "#wkMore .rzwk")
    # «Что сделать сегодня»: дельта — у счёта своей части; части с закрытой частью (DEALCTX в
    # странице) — счёт *Full; шапка дня — без дельт (одна строка)
    head = _txt(pg, "#wkTodayH")[0]
    assert f"по заказам: {pt['execOrdersFull']} заказ" in head and f"по сделкам: {pt['dealDealsFull']} сдел" in head
    assert "за неделю" not in head
    ex, dl = _txt(pg, "#wkPH-exec")[0], _txt(pg, "#wkPH-deal")[0]
    # дельта — в скобках сразу за числом заказов, до «в N сделках»: не читается как дельта сделок
    assert f"{pt['execOrdersFull']} заказов (+2 заказа за неделю, хуже) в " in ex
    assert f"{pt['dealDealsFull']} сделок" in dl and "−3 сделки за неделю, лучше" in dl
    hyg = _txt(pg, "#wkPH-hyg")[0]
    assert f"{pt['hygOrdersFull']} заказ" in hyg and "−5 заказов за неделю, лучше" in hyg and "без изменений" in hyg
    # числа плиток — те же, что в точке истории
    assert _txt(pg, "#wkTLate .wktv")[0] == str(pt["lateOrders"])
    assert _txt(pg, "#wkTW2 .wktv")[0] == str(pt["overOrders"])
    assert _txt(pg, "#wkTThr .wktv")[0] == str(pt["threatOrders"])
    # цвет: тревога — только у «хуже» (число и слово), «лучше» и «без изменений» — не тревога
    col = pg.eval_on_selector_all("#tab-contracts .rzwk", """els=>els.map(e=>({bad:e.classList.contains('rzwk-bad'),
        n:e.querySelector('.rzwk-n')?getComputedStyle(e.querySelector('.rzwk-n')).color:'', w:e.querySelector('.rzwk-w')?getComputedStyle(e.querySelector('.rzwk-w')).color:'',
        t:getComputedStyle(e).color, txt:e.textContent}))""")
    assert col and all((c["n"] == ALARM and c["w"] == ALARM) == c["bad"] for c in col if c["n"])
    assert all(("хуже" in c["txt"]) == c["bad"] for c in col)
    assert all(c["t"] != ALARM for c in col)
    # ряды 12 недель: у «сорван» и «под угрозой» рядом с числом (у W2 — мини-столбцы, спец. 2.4а); на экране
    # мин. и макс., цель — в подписи для диктора
    sp = _txt(pg, "#orkTiles .rzspark-m")
    assert len(sp) == 2 and all(re.fullmatch(r"мин\. \d+ · макс\. \d+", s) for s in sp)
    assert all("цель 0" in a for a in pg.eval_on_selector_all("#orkTiles .rzspark svg", "els=>els.map(e=>e.getAttribute('aria-label'))"))
    assert pg.eval_on_selector_all("#wkTW2 .rzspark", "els=>els.length") == 0
    # шаг 13 «к закрытию» — дельта в ведомости
    assert any("сделк" in t and "за неделю" in t for t in _txt(pg, "#orkLedD .rzwk"))
    assert not errs


def test_одна_точка_история_с_даты(pages, browser):
    pg, errs = _open(browser, pages["one"])
    assert _txt(pg, "#wkTW2 .rzwk") == ["история с 19.10"] and _txt(pg, "#wkTThr .rzwk") == ["история с 19.10"]
    # «срок сорван» — по восстановлению Д-12, с пометкой в подсказке
    t = pg.eval_on_selector("#wkTLate .rzwk", "e=>e.getAttribute('title')")
    assert "восстановлено по нынешнему сроку" in t and "за неделю" in _txt(pg, "#wkTLate .rzwk")[0]
    assert pg.eval_on_selector_all("#wkTThr .rzspark", "els=>els.length") == 0, "ряд — от двух точек"
    assert pg.eval_on_selector_all("#wkTLate .rzspark", "els=>els.length") == 1, "у «сорван» — Д-12 и сегодня"
    assert not _txt(pg, "#orkTodayS .rzwk") and not _txt(pg, "#wkMore .rzwk"), "без дельты части молчат"
    assert not errs


def test_без_раздела_прежний_вид(pages, browser):
    pg, errs = _open(browser, pages["none"])
    assert not _txt(pg, "#tab-contracts .rzwk")
    assert any("за неделю" in t for t in _txt(pg, "#wkTLate .wkspd")), "Д-12 — как раньше"
    assert pg.eval_on_selector_all("#wkTLate .wkspark svg", "els=>els.length") == 1
    assert not errs


def test_телефон_дельта_строкой_без_прокрутки(pages, browser):
    pg, errs = _open(browser, pages["hist"], 390, 844)
    vis = pg.eval_on_selector_all("#orkTiles .wkwk .rzwk", "els=>els.filter(e=>e.offsetParent).map(e=>e.textContent)")
    assert len(vis) == 3, "у каждой плитки-строки — дельта"
    assert pg.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    fs = pg.eval_on_selector_all("#tab-contracts .rzwk", "els=>els.filter(e=>e.offsetParent).map(e=>parseFloat(getComputedStyle(e).fontSize))")
    assert fs and min(fs) >= 12
    assert not errs
