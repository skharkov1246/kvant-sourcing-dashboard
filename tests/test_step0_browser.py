"""Шаг 0 «Подтверждение передачи в ОРК» и исполнитель заказа, не переданного в ОРК (решения владельца
31.10.2026) — в настоящем браузере (Playwright) на странице из придуманного корпуса
fixture.ПодтверждениеПередачи: путь заказа с открытой задачей начинается с шага 0; строка #wkPre над
столбцами ведомости — те же числа, что в данных, и её кнопки открывают ровно столько строк; группа
s0Over «Что сделать сегодня» открывает ровно свои заказы; у заказа 937/1 (ответственный не из ОРК, в
поле сделки — руководитель ОРК) исполнитель — ответственный заказа с пометкой «не из ОРК — заказ не
передан в ОРК»; scripts/validate_dashboard.py на странице — без ошибок; задачи не прочитаны — «Нет
данных», а не ноль. Нет Playwright или браузера — пропуск. Корпус придуман (CLAUDE.md, правило 18).
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

from tests import fixture  # noqa: E402

ROWS = "#drwBody table.odt tbody tr[data-odk]"
OUT = "не из ОРК — заказ не передан в ОРК"
ASSIGN = "назначает менеджера ОРК: рук. ОРК"


class Глухой(fixture.ПодтверждениеПередачи):
    """Тот же корпус, но портал без права task: tasks.task.list отказывает — задачи не прочитаны."""

    def tasks_list(self, *a, **k):
        raise RuntimeError("ACCESS_DENIED")


def _write(path: Path, stub) -> Path:
    import dashboard
    dashboard.write(fixture.build_metrics(), {"source": "rules", "items": []}, str(path),
                    people=fixture.build_people(), **fixture.build_realization(stub=stub))
    return path


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    return _write(tmp_path_factory.mktemp("step0") / "index.html", fixture.ПодтверждениеПередачи())


@pytest.fixture(scope="module")
def deaf(tmp_path_factory):
    return _write(tmp_path_factory.mktemp("step0deaf") / "index.html", Глухой())


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


def _open(browser, path: Path, h="#contracts/week", width=1280):
    pg = browser.new_page(viewport={"width": width, "height": 900})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(path.resolve().as_uri() + h)
    pg.wait_for_selector("#ctPanel-main")
    pg.wait_for_timeout(400)
    return pg


def _rows(pg, el) -> list[str]:
    el.scroll_into_view_if_needed()
    el.click()
    pg.mouse.move(0, 0)
    pg.wait_for_selector("#drw.on")
    keys = pg.eval_on_selector_all(ROWS, "rs=>rs.map(r=>r.dataset.odk)")
    pg.keyboard.press("Escape")
    return keys


def _week(pg) -> dict:
    return pg.evaluate("window.__CONTRACTS__.ork.week")


def test_путь_заказа_с_открытой_задачей_начинается_с_шага_0(browser, page):
    """941/1: задача открыта 6 р.д. — шаг 0 текущий, «Шаг 0 · до передачи в ОРК», сверх 5 раб. дн. —
    словами «передача в ОРК не подтверждена», а не «дольше нормы» ОРК; путь — 14 шагов."""
    pg = _open(browser, page, "#contracts/week?o=99711")
    pg.wait_for_selector("#drwBody #opRoot")
    cur = pg.eval_on_selector('#drwBody li.op-s[aria-current="step"]', "e=>e.dataset.ops")
    assert cur == "0"
    assert pg.locator("#drwBody li.op-s").count() == 14
    t = pg.inner_text("#drwBody")
    assert "Шаг 0 · до передачи в ОРК" in t and "Шаг 0 из 13" not in t
    assert "передача в ОРК не подтверждена дольше 5 раб. дн." in t
    assert "создана задача подтверждения передачи" in t
    assert pg.locator("#drwBody .op-stuck.is-over").count() == 1
    # заказ с закрытой задачей (942/1): шаг 1, шаг 0 — пройденный, приёмка — от закрытия задачи
    pg.goto(page.resolve().as_uri() + "#contracts/week?o=99721")
    pg.wait_for_timeout(500)
    assert pg.eval_on_selector('#drwBody li.op-s[aria-current="step"]', "e=>e.dataset.ops") == "1"
    assert pg.locator('#drwBody li.op-s.is-done[data-ops="0"]').count() == 1
    assert "закрыта задача подтверждения передачи" in pg.inner_text("#drwBody")
    assert not pg.errors, pg.errors


def test_строка_шага_0_над_столбцами_числа_из_данных(browser, page):
    """#wkPre: заказов на шаге = ledger[0].now, дольше 5 раб. дн. = ledger[0].over (= пунктов s0Over);
    каждая кнопка открывает ровно столько строк; в столбцах и таблице шагов 4–12 шага 0 нет; счётчик
    шапки «Заказов: ждут подтверждения передачи в ОРК» — то же число."""
    pg = _open(browser, page)
    W = _week(pg)
    L0 = next(s for s in W["ledger"] if s["no"] == 0)
    q0 = {str(q["order"]) for q in pg.evaluate("window.__CONTRACTS__.life.queue") if q["code"] == "s0Over"}
    assert L0["now"] >= 5 and L0["over"] == len(q0) >= 1
    pre = pg.locator("#ctPanel-main #wkPre")
    assert pre.count() == 1 and "Подтверждение передачи в ОРК" in pre.inner_text()
    for f, n in (("now", L0["now"]), ("over", L0["over"])):
        b = pre.locator(f'button[data-od="w:step/0/{f}"]')
        assert b.count() == 1 and int(b.inner_text()) == n
        keys = _rows(pg, b)
        assert len(keys) == n, (f, keys)
        if f == "over":
            assert set(keys) == q0
    assert pg.locator('#ctPanel-main :is(#orkLed,.wkhists) [data-od^="w:step/0/"]').count() == 0
    hdr = pg.locator('#ctPanel-main #wkMore li.wkc:has(button[data-od="w:step/0/now"])')
    assert hdr.count() == 1 and "ждут подтверждения передачи в" in hdr.inner_text()
    assert int(hdr.locator('button[data-od="w:step/0/now"]').inner_text()) == L0["now"]
    assert f"дольше {L0['norm']} раб. дн." in hdr.inner_text()
    # «Кто сколько ведёт»: строка «до передачи в ОРК» — заказы шага 0; Σ «дольше нормы» = W2
    P = W["pulse"]["rows"]
    i = next(k for k, r in enumerate(P) if r["kind"] == "pre")
    b = pg.locator(f'#orkPulseB button[data-od="w:pulse/{i}/keys"]')
    assert int(b.inner_text()) == P[i]["n"] == L0["now"]
    assert "До передачи в" in pg.locator("#orkPulseB tr.wkpre-r").inner_text()
    assert not pg.errors, pg.errors


def test_группа_s0Over_открывает_ровно_свои_заказы(browser, page):
    """«Что сделать сегодня», дела по заказам: группа s0Over (как приёмка заказа) — число группы = её
    заказы, список — те же заказы; кто делает — исполнитель задачи подтверждения, в курсе — КАМ."""
    pg = _open(browser, page)
    q0 = [q for q in pg.evaluate("window.__CONTRACTS__.life.queue") if q["code"] == "s0Over"]
    want = {str(q["order"]) for q in q0}
    pg.evaluate("document.querySelectorAll('#orkToday [data-wkmore]').forEach(b=>b.click())")
    pg.wait_for_timeout(200)
    g = pg.locator('#orkToday li.wkg[data-g="exec:s0Over"]')
    assert g.count() == 1
    n = g.locator("button[data-tq]")
    assert int(n.inner_text()) == len(want)
    assert set(_rows(pg, n)) == want
    g.locator("button.wkgt").click()
    pg.wait_for_timeout(200)
    det = pg.inner_text("#orkToday .wkqd").replace("\xa0", " ")
    assert "Исполнитель задачи подтверждения передачи" in det and "в курсе" in det
    assert "раб. дн. при норме 5" in det
    assert not pg.errors, pg.errors


def test_заказ_не_переданный_в_ОРК_делает_ответственный(browser, page):
    """937/1 (как 968/2): ответственный заказа не из ОРК, в поле сделки — руководитель ОРК: делает
    ответственный заказа с пометкой «не из ОРК — заказ не передан в ОРК», менеджера ОРК назначает рук.
    ОРК — в карточке сделки, в пути заказа («Где застрял») и в строке списка заказов."""
    pg = _open(browser, page, "#contracts/main?d=6312")
    pg.wait_for_selector("#drwBody .ctd-card")
    card = pg.inner_text("#drwBody")
    assert f"Сергей (снабжение) ({OUT})" in card and ASSIGN in card
    assert "Ответственный за реализацию" in card
    pg.goto(page.resolve().as_uri() + "#contracts/week?o=99821")
    pg.wait_for_timeout(500)
    stuck = pg.inner_text("#drwBody .op-stuck")
    assert f"Сергей (снабжение) ({OUT})" in stuck and ASSIGN in stuck
    pg.keyboard.press("Escape")
    pg.goto(page.resolve().as_uri() + "#contracts/week")
    pg.wait_for_timeout(400)
    b = pg.locator('#ctPanel-main button[data-od="w:step/4/now"]').first
    b.scroll_into_view_if_needed()
    b.click()
    pg.wait_for_selector("#drw.on")
    row = pg.inner_text(f'{ROWS.replace("[data-odk]", "")}[data-odk="99821"]')
    assert OUT in row and ASSIGN in row
    assert not pg.errors, pg.errors


def test_валидатор_на_странице_без_ошибок(page, deaf):
    """scripts/validate_dashboard.py: проверки шага 0 по данным и в браузере — без ошибок и на
    странице с задачами, и на странице, где задачи не прочитаны."""
    from scripts import validate_dashboard as vd
    for p in (page, deaf):
        html = p.read_text(encoding="utf-8")
        errors: list[str] = []
        blobs = vd.extract_data(html, errors, {})
        vd.check_step0(blobs, errors)
        warns: list[str] = []
        vd.check_browser(p, errors, warns, [])
        if any("Chromium" in w for w in warns):
            pytest.skip("нет браузера для проверки рендера")
        assert not errors, (p.parent.name, errors)


def test_задачи_не_прочитаны_нет_данных(browser, deaf):
    """Отказ без последствий (plan, раздел 5): строка шага 0 — «Нет данных», кнопок шага 0 нет, счётчик
    шапки — «—», ни у одного заказа нет шага 0."""
    pg = _open(browser, deaf)
    W = _week(pg)
    L0 = next(s for s in W["ledger"] if s["no"] == 0)
    assert L0.get("nodata") is True
    assert "Нет данных" in pg.inner_text("#ctPanel-main #wkPre")
    assert pg.locator('#ctPanel-main [data-od^="w:step/0/"]').count() == 0
    assert not [o for r in pg.evaluate("window.__CONTRACTS__.rows") for o in r["orders"] if o["step"] == 0]
    assert not pg.errors, pg.errors
