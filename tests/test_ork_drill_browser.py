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
# все списки, обещанные данными «Месяца» и «Квартала» (dk показанного, liveDk, nDk) — по виду
ALL_DK = """sep=>{const out=[]; const walk=o=>{if(!o||typeof o!=='object')return; if(Array.isArray(o)){o.forEach(walk);return;}
  if(o.dk&&(o.state==='show'||o.state==='prelim')) out.push(o.dk); if(o.liveDk) out.push(o.liveDk); if(o.nDk) out.push(o.nDk);
  for(const k in o) if(k!=='drill'&&k!=='orders') walk(o[k]);};
  walk(window.__CONTRACTS__.ork.quarter); walk(window.__ORKMONTH__&&window.__ORKMONTH__[sep?'sep':'main']); return out;}"""
FIND_N = """id=>{const out=[]; const walk=o=>{if(!o||typeof o!=='object')return; if(Array.isArray(o)){o.forEach(walk);return;}
  if(o.dk===id) out.push(o.n); if(o.liveDk===id) out.push(o.live); if(o.nDk===id) out.push(o.n); for(const k in o) if(k!=='drill'&&k!=='orders') walk(o[k]);};
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
        # плитка открывает диалог списка, а не переключает отбор
        assert tile.get_attribute("aria-haspopup") == "dialog" and tile.get_attribute("aria-pressed") is None
        assert _clicked_rows(pg, tile) == want, t
        if not want:
            assert "За этим числом заказов нет" in pg.locator("#drwBody").inner_text()
        pg.keyboard.press("Escape")
    # «Ждут смежников»: строка — список (а не отбор очереди); «дольше P80» — свой клик
    for r in ("kam", "oss", "log", "sup"):
        tr = panel.locator(f"tr[data-adj='{r}']")
        want = _expect(tr.locator("button[data-od^='w:adj/']")) if tr.locator("button[data-od^='w:adj/']").count() else 0
        if not want:
            continue
        assert _clicked_rows(pg, tr.locator("td").first) == want, r
        assert "Ждут смежников" in pg.locator("#drwName").inner_text()
        assert pg.locator("#drwBody [data-odq]").count() == 1
        pg.keyboard.press("Escape")
        # число «Заказов» — тот же список и тот же переход в очередь, что у строки
        assert _clicked_rows(pg, tr.locator("button[data-od^='w:adj/']")) == want, r
        assert pg.locator("#drwBody [data-odq]").count() == 1, "у числа нет «Пункты очереди этих шагов»"
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
    # из списка W2 — «Пункты в очереди» с клавиатуры: отбор очереди виден снимаемой фишкой, фокус —
    # на ней, а не в закрытом aria-hidden дровере; роль диалога с общего дровера снята
    tile = panel.locator(".actile[data-of='w2']")
    tile.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#drw.on")
    pg.locator("#drwBody [data-odq]").focus()
    pg.keyboard.press("Enter")
    assert pg.locator("#drw.on").count() == 0
    assert "W2 сверх нормы" in pg.locator("#orkFilt").inner_text()
    assert not pg.evaluate("document.getElementById('drw').contains(document.activeElement)"), "фокус остался в дровере"
    assert pg.evaluate("document.activeElement.closest('#orkFilt')!==null")
    assert pg.evaluate("document.getElementById('drw').getAttribute('role')") is None
    pg.keyboard.press("Tab")
    assert not pg.evaluate("document.getElementById('drw').contains(document.activeElement)")
    # отбор плитки заменяет «человек — хозяин, сверх нормы и «ждём УПД»»: человек остаётся простым отбором
    who = panel.locator("#orkOver tr[data-who]").first
    _clicked_rows(pg, who.locator("td").first)
    pg.locator("#drwBody [data-odq]").click()
    assert "хозяин, сверх нормы" in pg.locator("#orkFilt").inner_text()
    _clicked_rows(pg, panel.locator(".actile[data-of='w2']"))
    pg.locator("#drwBody [data-odq]").click()
    filt = pg.locator("#orkFilt").inner_text()
    assert "человек:" in filt and "хозяин, сверх нормы" not in filt, filt
    pg.locator("#orkFilt [data-rm='who']").click()
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
    # точка выгрузки в Excel: функция _xlMark параллельной правки зовётся с телом списка
    pg.evaluate("window.__xl=[]; window._xlMark=r=>window.__xl.push(r&&r.id)")
    # строка — карточка сделки, когда пути заказа нет (_opOpen); путь — test_разбор_числа_…
    pg.evaluate("window._opOpen=undefined; 0")
    row = pg.locator("#orkPulseB tr[data-od]").nth(1)
    row.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#drw.on")
    assert pg.evaluate("document.activeElement.id") == "drwX"
    assert pg.evaluate("document.getElementById('drw').getAttribute('role')") == "dialog"
    assert "drwBody" in pg.evaluate("window.__xl"), "после отрисовки списка не позван _xlMark"
    assert pg.locator("#drwBody table.odt").get_attribute("data-xl-name")
    # дубль «стадия · поставщик» для телефона — не текст ячейки (выгрузка берёт textContent)
    assert pg.evaluate("[...document.querySelectorAll('#drwBody .odm')].every(e=>e.textContent==='' && e.dataset.odm)")
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
    # обратная сверка: у каждого списка в данных есть число на странице, и оно открывает n строк
    have = set(pg.evaluate("[...document.querySelectorAll('#ctPanel-main [data-od]')].map(e=>e.dataset.od)"))
    want = [d for d in pg.evaluate(ALL_DK, False) if d.startswith("m:" if sub == "month" else "q:")]
    assert want and not [d for d in want if d not in have], [d for d in want if d not in have]
    if sub == "quarter":
        # закрытые заказы — корзиной «заказ закрыт»; менеджеров ОРК в списках «Квартала» нет
        el = pg.locator("#ctPanel-main .actile[data-od^='q:']").first
        _clicked_rows(pg, el)
        body = pg.locator("#drwBody").inner_text()
        assert "По менеджерам ОРК" not in body and "стадия не из таблицы" not in body
        pg.keyboard.press("Escape")
        # строка поставщика со скрытой долей: «Готовностей» и сама строка — список без «в срок / позже»
        hid = pg.locator("#ctPanel-main tr[data-od$='n']").first
        n = pg.evaluate(FIND_N, hid.get_attribute("data-od"))
        assert _clicked_rows(pg, hid.locator("td").first) == n[0]
        assert "в срок" not in pg.locator("#drwBody table.odt").inner_text()
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
    # шаги на телефоне — списком выбора (фишки скрыты), отбор им — столько строк, сколько в шаге
    sel = pg.locator("#drwBody select[data-odsel='step']")
    assert sel.is_visible() and not pg.locator("#drwBody .odsteps").is_visible()
    v = sel.locator("option").nth(1).get_attribute("value")
    want = pg.evaluate("v=>[...document.querySelectorAll('#drwBody .odsteps [data-odf]')].find(b=>b.dataset.odf==='step:'+v).querySelector('b').textContent", v)
    sel.select_option(v)
    assert pg.locator(ROWS).count() == int(want)
    assert not errs, errs


@pytest.mark.parametrize("width", [1280, 1920])
def test_разбор_числа_что_делать_отборы_сортировка_путь(browser, page_url, width):
    """«Разбор числа» (отзыв владельца 29.10.2026): «Что делать» — группа отбирает свои строки
    (aria-pressed), повторный клик снимает; поставщик — списком выбора; поиск не теряет фокус;
    сортировка — кнопкой в шапке с aria-sort; «Разрезы» под таблицей и не закрываются при
    отборе; строка заказа — путь заказа (_opOpen, когда он есть), ссылка в Битрикс — своя;
    дровер min(1200px, 100vw), ничего не обрезано справа."""
    pg, errs = _open(browser, page_url, width=width)
    pg.evaluate("window.__op=[]; window._opOpen=k=>window.__op.push(k); 0")   # 0: функцию evaluate вызвал бы сам
    tile = pg.locator("#ctPanel-main .actile[data-of='w3']")
    total = int(tile.locator(".v").inner_text())
    _clicked_rows(pg, tile)
    assert pg.evaluate("Math.round(document.getElementById('drw').getBoundingClientRect().width)") == min(1200, width)
    assert pg.evaluate("(()=>{const b=document.getElementById('drwBody'), r=b.getBoundingClientRect();"
                       "return b.scrollWidth<=b.clientWidth&&[...b.querySelectorAll('#odTable th,#odTable td')]"
                       ".every(c=>c.getBoundingClientRect().right<=r.right+0.5)})()"), "обрезано справа"
    assert pg.locator("#drwBody .oddef").inner_text().startswith("Заказы, у которых срок клиента прошёл")
    # что делать: группы — все строки числа; клик — отбор, повторный — снять
    acts = pg.locator("#drwBody .odact")
    ns = [int(acts.nth(i).locator(".odact-n").inner_text()) for i in range(acts.count())]
    assert sum(ns) == total and pg.locator("#drwBody .odmore").count() == 0
    acts.first.click()
    assert pg.locator(ROWS).count() == ns[0]
    assert pg.locator("#drwBody .odact").first.get_attribute("aria-pressed") == "true"
    assert pg.evaluate("document.activeElement.classList.contains('odact')"), "фокус остался на группе"
    pg.locator("#drwBody .odact").first.click()
    assert pg.locator(ROWS).count() == total
    # «Разрезы» открыты — остаются открытыми после отбора; отбор из разреза — снимаемой фишкой
    pg.locator("#drwBody details.odcuts summary").click()
    row = pg.locator("#drwBody details.odcuts .odrow").first
    n = int(row.locator("b").inner_text())
    row.click()
    assert pg.locator(ROWS).count() == n and pg.locator("#drwBody details.odcuts[open]").count() == 1
    pg.locator("#drwBody [data-odf='reset']").first.click()
    assert pg.locator(ROWS).count() == total
    # поставщик — списком выбора
    sel = pg.locator("#drwBody select[data-odsel='sup']")
    opt = sel.locator("option").nth(1)
    want = int(opt.inner_text().rsplit("—", 1)[1])
    sel.select_option(opt.get_attribute("value"))
    assert pg.locator(ROWS).count() == want and pg.locator("#drwBody [data-odf='rm:sup']").count() == 1
    pg.locator("#drwBody [data-odf='rm:sup']").click()
    assert pg.locator(ROWS).count() == total
    # поиск: по номеру заказа, фокус в поле
    no = pg.locator(ROWS).first.locator(".odno b").inner_text().strip()
    box = pg.locator("#drwBody input[data-odsearch]")
    box.click()
    pg.keyboard.type(no)
    assert pg.evaluate("document.activeElement.matches('input[data-odsearch]')")
    assert 1 <= pg.locator(ROWS).count() < total or total == 1
    assert all(no in pg.locator(ROWS).nth(i).locator(".odno b").inner_text() for i in range(pg.locator(ROWS).count()))
    pg.locator("#drwBody [data-odf='rm:q']").click()
    assert pg.locator(ROWS).count() == total
    # сортировка: кнопка в шапке, aria-sort, повторный клик — обратный порядок
    pg.locator("#drwBody th button[data-odsort='no']").click()
    th = pg.locator("#drwBody th:has(button[data-odsort='no'])")
    assert th.get_attribute("aria-sort") == "ascending"
    first = pg.locator(ROWS).first.get_attribute("data-odk")
    pg.locator("#drwBody th button[data-odsort='no']").click()
    assert th.get_attribute("aria-sort") == "descending" and pg.locator(ROWS).last.get_attribute("data-odk") == first
    # ссылки в Битрикс: новая вкладка; клик по ссылке строку не открывает
    a = pg.locator(ROWS).first.locator("a.odbx").first
    assert a.get_attribute("target") == "_blank" and "noopener" in a.get_attribute("rel")
    assert pg.evaluate("window.__op.length") == 0, "строка открылась до ссылки"
    pg.evaluate("document.querySelector('#drwBody tr[data-odk] a.odbx').addEventListener('click',e=>e.preventDefault(),{once:true})")
    pg.locator("#drwBody tr[data-odk] a.odbx").first.click()
    assert pg.evaluate("window.__op.length") == 0 and pg.locator(ROWS).count() == total
    # строка заказа — путь заказа: _opOpen(ключ заказа)
    r0 = pg.locator(ROWS).first
    k = r0.get_attribute("data-odk")
    r0.focus()
    pg.keyboard.press("Enter")
    assert pg.evaluate("window.__op") == [k]
    pg.keyboard.press("Escape")
    assert not errs, errs
