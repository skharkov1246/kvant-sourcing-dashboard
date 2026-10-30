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
  walk(window.__CONTRACTS__&&window.__CONTRACTS__.ork); walk(window.__ORKMONTH__);
  // R4 (Д-2, Д-10): списки без показателя-владельца («не доставлено», «не считаем», столбцы по месяцам,
  // хвост P80) — длина списка в drill; число на кнопке — та же длина (tests/test_rz_wpf.py)
  if(!out.length){const D=id[0]==='m'?((((window.__ORKMONTH__||{}).main||{}).month||{}).drill||{}):(((window.__CONTRACTS__||{}).ork||{}).quarter||{}).drill||{};
    const L=D[id.slice(2)]; if(L&&Array.isArray(L.k)) out.push(L.k.length);}
  return out;}"""


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
    import re
    b = el.locator("b")
    return int(re.match(r"\d+", (b.first.inner_text() if b.count() else el.inner_text()).strip()).group())


def _clicked_rows(pg, el) -> int:
    el.scroll_into_view_if_needed()
    el.click()
    # указатель уходит с панели: под ним бывает сокращение <abbr> с подсказкой, а Esc сначала закрывает
    # подсказку (WCAG 1.4.13), и только следующий Esc — список
    pg.mouse.move(0, 0)
    pg.wait_for_selector("#drw.on")
    # спец. «сделка и заказ» (E27): пары «заказ × отрезок | файл» — вложенными строками под заказом;
    # строка списка — объект его единицы (заказ или сделка)
    pairs = pg.locator("#drwBody table.odt tbody li.odpi").count()
    return pairs or pg.locator(ROWS + ".drill").count()


def _close_and_focus(pg, el) -> None:
    pg.keyboard.press("Escape")
    assert pg.locator("#drw.on").count() == 0, "Esc не закрыл список"
    assert el.evaluate("e=>document.activeElement===e"), "фокус не вернулся на число"


@pytest.mark.parametrize("sep", [False, True])
def test_каждое_число_недели_открывает_свои_заказы(browser, page_url, sep):
    pg, errs = _open(browser, page_url, sep=sep)
    panel = pg.locator("#ctPanel-sep" if sep else "#ctPanel-main")
    pg.evaluate("document.querySelectorAll('#ctPanel-main details').forEach(d=>d.open=true)")
    btns = panel.locator("button[data-od]")
    n = btns.count()
    assert n >= (2 if sep else 40), n
    for i in range(n):
        el = btns.nth(i)
        if not el.is_visible():
            continue
        want = _expect(el)
        got = _clicked_rows(pg, el)
        assert got == want, (el.get_attribute("data-od"), want, got)
        _close_and_focus(pg, el)
    if not sep:
        # «Кто сколько ведёт»: число заказов человека — его список; шапка — по образцу владельца
        P = pg.evaluate("window.__CONTRACTS__.ork.week.pulse.rows")
        bs = panel.locator("#orkPulseB button[data-od^='w:pulse/'][data-od$='/keys']")
        assert bs.count() == sum(1 for p in P if p["n"])
        for i in range(bs.count()):
            b = bs.nth(i)
            p = P[int(b.get_attribute("data-od").split("/")[1])]
            assert _clicked_rows(pg, b) == p["n"]
            head = pg.locator("#drwSub").inner_text()
            assert f"{p['n']} заказ" in head and "от всех в работе" in head and f"сделок с его заказами {p['deals']}" in head
            pg.keyboard.press("Escape")
    assert not errs, errs


def test_плитки_смежники_менеджеры_и_очередь(browser, page_url):
    """«Неделя» R4: плитки KPI 1–3 открывают свои заказы; «сорван» + «под угрозой» = W3 (каждый
    заказ — один раз); строка «Кого ждём» — список; «Что сделать сегодня» — группы: число группы —
    её пункты, из списка — назад к группе (фокус на ней, дровер закрыт, роль диалога снята);
    выбор группы, «ещё N», отбор по человеку и «Снять отбор»."""
    pg, errs = _open(browser, page_url)
    panel = pg.locator("#ctPanel-main")
    W3 = pg.evaluate("window.__CONTRACTS__.ork.week.tiles.W3")
    ks = {}
    for r in ("1", "2", "3"):
        tile = panel.locator(f"[data-kpi-rank='{r}']")
        ks[r] = _expect(tile)
        assert _clicked_rows(pg, tile) == ks[r], r
        pg.keyboard.press("Escape")
    assert ks["1"] + ks["3"] == W3["value"] and ks["1"] == W3["byOrders"]["late"]
    for r in ("kam", "oss", "log", "sup"):
        tr = panel.locator(f"tr[data-adj='{r}']")
        b = tr.locator("button[data-od^='w:adj/']")
        if not b.count():
            continue
        assert _clicked_rows(pg, tr.locator("th").first) == _expect(b), r
        assert "Ждём:" in pg.locator("#drwName").inner_text()
        pg.keyboard.press("Escape")
    p80 = panel.locator("#wkWait button[data-od='w:p80']")
    assert _clicked_rows(pg, p80) == _expect(p80)
    pg.keyboard.press("Escape")
    # группа дел: число — пункты группы; «Показать в «Что сделать сегодня»» — назад к группе
    gb = panel.locator("#orkToday button[data-tq]").first
    key = gb.get_attribute("data-tq")
    assert _clicked_rows(pg, gb) == _expect(gb)
    pg.locator("#drwBody [data-odq]").focus()
    pg.keyboard.press("Enter")
    assert pg.locator("#drw.on").count() == 0
    assert not pg.evaluate("document.getElementById('drw').contains(document.activeElement)"), "фокус остался в дровере"
    assert pg.evaluate("document.activeElement.closest('.wkg.on')!==null")
    assert pg.evaluate("document.activeElement.closest('.wkg').dataset.g") == key
    assert pg.evaluate("document.getElementById('drw').getAttribute('role')") is None
    # выбор другой группы — её строки справа; ↓ — к следующей группе
    g2 = panel.locator(".wkg .wkgt").nth(1)
    g2.click()
    assert panel.locator(".wkg").nth(1).get_attribute("class").split() == ["wkg", "on"]
    assert panel.locator(".wkgt[aria-expanded='true']").count() == 1
    pg.keyboard.press("ArrowDown")
    assert pg.evaluate("document.activeElement===document.querySelectorAll('.wkgt')[2]")
    # «ещё N ›» — все строки открытой группы
    panel.locator(".wkg .wkgt").first.click()
    more = panel.locator("#wkqD [data-wkall]")
    if more.count():
        n = int(panel.locator(".wkg.on button[data-tq]").inner_text())
        more.click()
        # строки — заказы группы (число группы — разные заказы); шапки сделок «клиентских» кодов —
        # отдельные строки tr.wkdhr, по одной на сделку, в счёт не входят
        assert panel.locator("#wkqD tbody tr:not(.wkdhr):not(.wksubr)").count() == n
        hd = panel.locator("#wkqD tbody tr.wkdhr button.wkdeal")
        ds = [hd.nth(i).get_attribute("data-ct") for i in range(hd.count())]
        assert len(ds) == len(set(ds))
    # отбор по человеку: группы — только его пункты; под отбор пусто — «Снять отбор»
    who = pg.locator("#wkWho option").nth(1).get_attribute("value")
    pg.locator("#wkWho").select_option(who)
    assert pg.locator("#wkWho").input_value() == who and panel.locator(".wkg").count() >= 1
    pg.evaluate("()=>{const s=document.getElementById('wkRole'); const o=document.createElement('option'); o.value='нет'; o.textContent='нет'; s.appendChild(o); s.value='нет'; s.dispatchEvent(new Event('change',{bubbles:true}));}")
    assert "заказов нет" in panel.locator("#orkTodayS").inner_text()
    panel.locator("[data-wkreset]").click()
    assert panel.locator(".wkg").count() >= 4 and pg.locator("#wkWho").input_value() == ""
    assert not errs, errs


def test_клавиатура_карточка_и_отборы_в_списке(browser, page_url):
    pg, errs = _open(browser, page_url)
    # точка выгрузки в Excel: функция _xlMark параллельной правки зовётся с телом списка
    pg.evaluate("window.__xl=[]; window._xlMark=r=>window.__xl.push(r&&r.id)")
    row = pg.locator("#orkPulseB button[data-od]").nth(1)
    row.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#drw.on")
    # фокус — на заголовке панели (R4, 2.8), не на «×» и не на внешней ссылке
    assert pg.evaluate("document.activeElement.id") == "drwName"
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
    # отбор по шагу: столбец гистограммы «На каких шагах» — строк столько, сколько над столбцом; снять — все
    chip = pg.locator("#drwBody .odcol:not([aria-disabled])").nth(0)
    want = int(chip.locator(".odcol-n").inner_text())
    chip.click()
    assert pg.locator(ROWS).count() == want
    pg.locator("#drwBody [data-odf='rm:step']").click()
    assert pg.locator(ROWS).count() == total
    # строка заказа — путь заказа по шагам (_opOpen), строка уровня сделки — карточка сделки;
    # назад к тому же списку — крошкой (у пути заказа — и своей кнопкой)
    first = pg.locator(ROWS).first
    first.locator("[data-odgo]").focus()
    pg.keyboard.press("Enter")
    back = "#drwCrumbs .odcrb[data-odcr=list]"
    assert pg.locator(back).count() == 1 and pg.locator(ROWS).count() == 0
    pg.locator(back).click()
    assert pg.locator(ROWS).count() == total
    pg.keyboard.press("Escape")
    assert pg.evaluate("document.activeElement===document.querySelectorAll('#orkPulseB button[data-od]')[1]")
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
        el = pg.locator("#ctPanel-main .ctmk-n[data-od^='q:'], #ctPanel-main .actile[data-od^='q:']").first
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
    pg.locator("tr[data-adj='log'] th").first.click()
    pg.wait_for_selector("#drw.on")
    pg.wait_for_timeout(300)
    assert pg.evaluate("document.documentElement.scrollWidth<=innerWidth")
    assert pg.evaluate("Math.round(document.getElementById('drw').getBoundingClientRect().width)") == 390
    # строки — карточками: таблица не шире дровера
    assert pg.evaluate("document.querySelector('#odTable').scrollWidth<=document.getElementById('drwBody').clientWidth")
    # телефон: отбор и шаги свёрнуты (первый экран — «Почему», «Что делать» и первые заказы); шаги —
    # строками «шаг · число», отбор ими — столько строк, сколько в шаге
    srch = pg.locator("#drwBody input[data-odsearch]")
    assert not srch.is_visible() and pg.locator("#drwBody details.odtd > summary").is_visible()
    pg.locator("#drwBody details.odtd > summary").click()
    assert srch.is_visible()
    pg.locator("#drwBody details.odstp > summary").click()
    row = pg.locator("#drwBody .odsr").first
    want = int(row.locator("b").inner_text())
    row.click()
    assert pg.locator(ROWS).count() == want
    assert not errs, errs


@pytest.mark.parametrize("width", [1280, 1920])
def test_разбор_числа_что_делать_отборы_сортировка_путь(browser, page_url, width):
    """«Разбор числа» (отзыв владельца 29.10.2026): «Что делать» — группа отбирает свои строки
    (aria-pressed), повторный клик снимает; поставщик — списком выбора; поиск не теряет фокус;
    сортировка — кнопкой в шапке с aria-sort; «Разрезы» под таблицей и не закрываются при
    отборе; строка заказа — путь заказа (_opOpen, когда он есть), ссылка в Битрикс — своя;
    панель одной ширины (R4, 2.1: 1280 — 980 px, 1920 — 1200 px), ничего не обрезано справа."""
    pg, errs = _open(browser, page_url, width=width)
    pg.evaluate("window.__op=[]; window._opOpen=(k,o)=>window.__op.push([k,(o||{}).back]); 0")   # 0: функцию evaluate вызвал бы сам
    tile = pg.locator("#ctPanel-main [data-kpi-rank='1']")
    total = _expect(tile)
    _clicked_rows(pg, tile)
    assert pg.evaluate("Math.round(document.getElementById('drw').getBoundingClientRect().width)") == {1280: 980, 1920: 1200}[width]
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
    # «Почему»: сумма причин = число списка; причина отбирает свои строки
    why = [int(x) for x in pg.locator("#drwBody .odwb b").all_inner_texts()]
    assert sum(why) == total
    pg.locator("#drwBody .odwb").first.click()
    assert pg.locator(ROWS).count() == why[0] and pg.locator("#drwBody [data-odf='rm:cause']").count() == 1
    pg.locator("#drwBody [data-odf='reset']").first.click()
    assert pg.locator(ROWS).count() == total
    # поставщик — строкой «имя · число» (число сразу за именем, не обрезано краем)
    chip = pg.locator("#drwBody .odsupb").first
    want = int(chip.locator("b").inner_text())
    assert pg.evaluate("(()=>{const r=document.getElementById('drw').getBoundingClientRect();"
                       "return [...document.querySelectorAll('#drwBody .odsupb b')].every(b=>b.getBoundingClientRect().right<=r.right)})()")
    chip.click()
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
    r0.locator("[data-odgo]").focus()
    pg.keyboard.press("Enter")
    assert pg.evaluate("window.__op") == [[k, "list"]]
    pg.keyboard.press("Escape")
    assert not errs, errs
