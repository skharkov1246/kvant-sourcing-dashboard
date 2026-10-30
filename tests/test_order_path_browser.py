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
    # R4 (спецификация 2.8): фокус при открытии — на заголовке панели, не на ссылке и не на кнопке
    assert pg.evaluate("document.activeElement.id") == "drwName"
    assert pg.locator("#opRoot .op-steps > li.op-s").count() == 13
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
    pg.locator("#drwCrumbs [data-odcr=list]").first.click()
    pg.wait_for_selector("#odTable")
    assert not pg.locator("#opRoot").count()
    assert pg.evaluate("(document.activeElement.closest('tr')||document.activeElement).dataset.odk") == k
    # Enter на строке — путь; Esc — на уровень вверх (к списку, фокус на строке), ещё Esc — закрыть,
    # фокус на число
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#opRoot")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#odTable")
    assert not pg.locator("#opRoot").count()
    assert pg.evaluate("(document.activeElement.closest('tr')||document.activeElement).dataset.odk") == k
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
    back = pg.locator("#drwCrumbs .odcrb[data-odcr=deal]")
    assert back.inner_text().startswith("Сделка №")
    assert pg.evaluate("document.activeElement.id") == "drwName"
    # из сделки — без «‹ ›»: соседние заказы ходят только по списку за числом
    assert not pg.locator("#drwNav:not([hidden]) [data-odnav]").count()
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


def test_карточка_из_таблицы_путь_назад_esc(browser, page_url):
    """Ревью 30.10.2026: карточка сделки из «Сделок и денег» — модальный диалог (Tab не уходит под
    подложку); «Путь заказа →» → «← к сделке» возвращает ту же прокрутку и фокус на ту же кнопку;
    Esc — фокус на строку сделки, а не на панель вкладки; атрибуты дровера после закрытия — прежние."""
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(page_url + "#contracts/main")
    pg.wait_for_selector("#ctBody tr[data-ct]")
    tr = pg.locator("#ctBody tr[data-ct]").first
    ct = tr.get_attribute("data-ct")
    tr.focus()
    pg.keyboard.press("Enter")
    pg.wait_for_selector("#drw.on")
    assert pg.evaluate("[document.getElementById('drw').getAttribute('role'),document.getElementById('drw').getAttribute('aria-modal')]") == ["dialog", "true"]
    for _ in range(60):
        pg.keyboard.press("Tab")
        assert pg.evaluate("document.getElementById('drw').contains(document.activeElement)")
    btn = pg.locator("#drwBody [data-ctop]").first
    if btn.count():
        k = btn.get_attribute("data-ctop")
        pg.evaluate("document.getElementById('drwBody').scrollTop=200")
        btn.focus()
        top = pg.evaluate("document.getElementById('drwBody').scrollTop")
        pg.keyboard.press("Enter")
        pg.wait_for_selector("#opRoot")
        pg.locator("#drwCrumbs [data-odcr=deal]").first.click()
        pg.wait_for_function("!document.getElementById('opRoot')")
        assert pg.evaluate("document.activeElement.dataset.ctop") == k
        assert pg.evaluate("document.getElementById('drwBody').scrollTop") == top
    pg.keyboard.press("Escape")
    assert not pg.evaluate("document.getElementById('drw').classList.contains('on')")
    assert pg.evaluate("document.activeElement.dataset.ct") == ct
    assert pg.evaluate("document.getElementById('drw').getAttribute('role')") is None
    assert not errs, errs
    pg.close()


def test_путь_заказа_стадия_вне_регламента(browser, page_url):
    """Ревью 30.10.2026: заказ со стадией не из таблицы шагов — путь не тянет последний известный
    шаг «→ сейчас» с вердиктом нормы и не пишет «пропущен» у шагов после него; есть строка
    «вне регламента» с днём входа в стадию."""
    pg, errs = _open(browser, page_url)
    k = pg.evaluate("()=>{for(const d of window.__CONTRACTS__.rows) for(const o of d.orders||[]) if(o.live&&o.step==null) return String(o.id);}")
    if not k:
        pytest.skip("в синтетике нет живого заказа со стадией вне регламента")
    pg.evaluate("k=>_opOpen(k,{})", k)
    pg.wait_for_selector("#opRoot")
    txt = pg.locator("#opRoot .op-steps").inner_text()
    assert "→ сейчас" not in txt and "пропущен" not in txt
    assert "нет в регламенте" in pg.locator("#opRoot .op-unk").inner_text()
    assert not errs, errs
    pg.close()


def test_производство_дольше_обычного_не_отклонений_нет(browser, page_url):
    """Ревью 30.10.2026: заказ из списка «Производство дольше обычного» — в строке списка дни в
    производстве и порог, в пути заказа не «Отклонений нет», а «запросить у поставщика статус»."""
    pg, errs = _open(browser, page_url)
    b = pg.locator("#ctPanel-main button[data-od='w:p80']").first
    if not b.count():
        pytest.skip("в синтетике нет числа «Производство дольше обычного»")
    b.click()
    pg.wait_for_selector("#drw.on")
    rows = pg.locator(ORDER_ROW)
    n = rows.count()
    assert n == int(b.inner_text())
    for i in range(n):
        r = pg.locator(ORDER_ROW).nth(i)
        assert "дольше, чем у 8 из 10 заказов" in r.inner_text()
        r.click()
        pg.wait_for_selector("#opRoot")
        t = pg.locator("#opRoot").inner_text()
        assert "Отклонений нет" not in t and "статус и новую дату" in t
        pg.locator("#drwCrumbs [data-odcr=list]").first.click()
        pg.wait_for_selector("#odTable")
    assert not errs, errs
    pg.close()


@pytest.mark.parametrize("sub", ["week", "month", "quarter", "how", "week/sep", "quarter/sep"])
def test_телефон_подпись_столбца_не_на_значении(browser, page_url, sub):
    """Ревью R4 (30.10.2026): на 390 px таблица «карточками» (table.rzflow) — подпись столбца
    (::before, 40 % ширины) слева, значение — справа от неё: отступ ячейки с подписью не меньше
    35 % её ширины (правила самих таблиц, например .ctm table.ctm-t td, специфичнее и клали
    подпись поверх значения)."""
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(page_url + "#contracts/" + sub)
    pg.wait_for_function("window.__RENDER_OK__===1")
    pg.evaluate("document.querySelectorAll('#tab-contracts details').forEach(d=>d.open=true)")
    m = pg.evaluate("""()=>{let n=0; const bad=[];
      document.querySelectorAll('#tab-contracts table.rzflow :is(td,th)[data-l]').forEach(c=>{const w=c.getBoundingClientRect().width; if(!w) return; n++;
        // R4 · WP-F: подпись — своей колонкой сетки (::before в потоке), а не поверх отступа — тоже не наложение
        // или подписи нет / она в строке перед значением — строка-карточка поставщика «Квартала»
        const bf=getComputedStyle(c,'::before'), grid=bf.position==='static'||bf.content==='none';
        if(!grid&&parseFloat(getComputedStyle(c).paddingLeft)<0.35*w) bad.push(c.dataset.l);}); return {n, bad:bad.slice(0,5)}}""")
    assert not m["bad"], m
    assert pg.evaluate("document.documentElement.scrollWidth<=innerWidth")
    pg.close()


@pytest.mark.parametrize("w", [1280, 1920])
def test_ведомость_13_шагов_без_прокрутки(browser, page_url, w):
    """Ревью R4: ведомость «Где сейчас заказы: 13 шагов» умещается в свою обёртку — подзаголовок
    столбца «Нет файлов шага» переносится, а не растягивает таблицу."""
    pg, errs = _open(browser, page_url, w)
    r = pg.evaluate("()=>{const t=document.querySelector('#tab-contracts table.wkled'), x=t.closest('.twrap'); return [x.scrollWidth, x.clientWidth]}")
    assert r[0] <= r[1] + 1, r
    assert not errs, errs
    pg.close()


def test_телефон_плитки_недели_без_наложений(browser, page_url):
    """Ревью R4: на 390 px плитки W1–W5 по две в ряд — слова не наезжают на код (W1…), пометка
    «предварительно» не выходит за плитку."""
    pg, errs = _open(browser, page_url, 390)
    out = pg.evaluate("""()=>{const out=[]; document.querySelectorAll('#tab-contracts .orktw').forEach(w=>{const card=w.getBoundingClientRect();
      const L=[...w.querySelectorAll('*')].filter(e=>e.children.length===0&&e.textContent.trim()&&e.getBoundingClientRect().width>0);
      L.forEach(e=>{const r=e.getBoundingClientRect(); if(r.right>card.right+0.5||r.left<card.left-0.5) out.push('вне плитки: '+e.textContent.trim());});
      for(let i=0;i<L.length;i++) for(let j=i+1;j<L.length;j++){const a=L[i].getBoundingClientRect(), b=L[j].getBoundingClientRect();
        if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>2&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2) out.push(L[i].textContent.trim()+' × '+L[j].textContent.trim());}});
      return out}""")
    assert out == [], out
    assert not errs, errs
    pg.close()


def test_дни_на_шаге_в_списке_как_в_пути(browser, page_url):
    """Ревью R4: «Где застрял» строки списка за W3 и «Заказы в работе» — то же число р.д., та же
    единица и та же дата «с …», что в «Пути заказа» этого заказа (шаг без нормы — тоже)."""
    import re
    pg, errs = _open(browser, page_url)
    seen, checked = set(), 0
    for sel in ("#tab-contracts button.rzn[data-kpi-rank='1']", "#ctPanel-main button.odn[data-od='w:live']"):
        b = pg.locator(sel).first
        if not b.count():
            continue
        b.click()
        pg.wait_for_selector("#drw.on")
        rows = pg.evaluate("""()=>[...document.querySelectorAll('#drwBody tr[data-odk]')].map(tr=>{const d=tr.querySelector('.odc2 .oddays'),
          m=/с (\\d\\d\\.\\d\\d\\.\\d{4})/.exec((tr.querySelector('.odc2')||{}).textContent||''); return [tr.dataset.odk, d?d.textContent.trim():'', m?m[1]:'']})""")
        for k, days, since in rows:
            if k in seen or k.startswith("d:") or not re.match(r"\d+ ", days):
                continue
            seen.add(k)
            pg.locator(f'#drwBody tr[data-odk="{k}"]').first.click()
            pg.wait_for_selector("#opRoot")
            hero = pg.evaluate("""()=>{const R=document.getElementById('opRoot'), b=R.querySelector('.op-num'), t=R.querySelector('.op-meta'),
              m=/^с (\\d\\d\\.\\d\\d\\.\\d{4})/.exec(t?t.textContent.trim():''); return [b?b.textContent.trim():'', m?m[1]:'']}""")
            if hero[0]:
                checked += 1
                assert re.match(r"\d+", days).group(0) == hero[0], (k, days, hero)
                assert "раб. дн." in days, (k, days)
                if since and hero[1]:
                    assert since == hero[1], (k, since, hero)
            pg.locator("#drwCrumbs [data-odcr=list]").first.click()
            pg.wait_for_selector("#odTable")
        pg.keyboard.press("Escape")
    assert checked >= 5, checked
    assert not errs, errs
    pg.close()


def test_шаг_раскрывается_esc_и_соседний_заказ(browser, page_url):
    """R4, WP-D (спецификация 2.8, 3.8): строка пути раскрывает «Шаг» (aria-expanded, «Кончается,
    когда», файлы шага); Esc сворачивает шаг и возвращает фокус на его строку, следующий Esc — к
    списку; «]» и «›» — соседний заказ того же списка за одно действие, счётчик «N из M»."""
    pg, errs = _open(browser, page_url)
    _list_with_orders(pg)
    n = pg.locator(ORDER_ROW).count()
    pg.locator(ORDER_ROW).first.click()
    pg.wait_for_selector("#opRoot")
    sh = pg.locator("#opRoot li.op-s:not(.is-next) button.op-sh").first
    st = sh.get_attribute("data-opstep")
    sh.click()
    pg.wait_for_selector(f"#opStep{st}")
    assert pg.locator(f'#opRoot button[data-opstep="{st}"]').get_attribute("aria-expanded") == "true"
    assert "Кончается, когда" in pg.locator(f"#opStep{st}").inner_text()
    pg.keyboard.press("Escape")
    assert not pg.locator(f"#opStep{st}").count()
    assert pg.evaluate("document.activeElement.dataset.opstep") == st
    if n > 1:
        first = pg.locator("#opRoot").get_attribute("data-opk")
        assert pg.locator("#drwNav .odnc").inner_text().startswith("1 из ")
        pg.keyboard.press("]")
        pg.wait_for_function("k=>document.getElementById('opRoot').dataset.opk!==k", arg=first)
        assert pg.locator("#drwNav .odnc").inner_text().startswith("2 из ")
        pg.locator("#drwNav [data-odnav='-1']").click()
        pg.wait_for_function("k=>document.getElementById('opRoot').dataset.opk===k", arg=first)
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#odTable")
    assert not errs, errs
    pg.close()


@pytest.mark.parametrize("w,h", [(1280, 800), (390, 844)])
def test_первый_экран_где_застрял_и_что_сделать(browser, page_url, w, h):
    """Спецификация 3.10 и задание WP-D: «Где застрял» целиком и начало «Что сделать» — до
    innerHeight на 1280×800 и 390×844; текст панели не мельче 13 px."""
    pg = browser.new_page(viewport={"width": w, "height": h})
    pg.goto(page_url + "#contracts/week")
    pg.wait_for_selector("#tab-contracts [data-od]")
    k = pg.evaluate("()=>{for(const d of window.__CONTRACTS__.rows) for(const o of d.orders||[]) if(o.live&&o.stepOver) return String(o.id);}")
    pg.evaluate("k=>_opOpen(k,{})", k)
    pg.wait_for_selector("#opRoot")
    m = pg.evaluate("""()=>{const b=s=>{const e=document.querySelector(s); return e?e.getBoundingClientRect().bottom:1e9;};
      return {stuck:b('#opRoot .op-stuck'), todo:b('#opTodoH'), H:innerHeight,
        small:[...document.querySelectorAll('#opRoot *')].filter(e=>e.getClientRects().length&&[...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())&&parseFloat(getComputedStyle(e).fontSize)<13).length}}""")
    assert m["stuck"] <= m["H"] and m["todo"] <= m["H"], m
    assert m["small"] == 0, m
    pg.close()
