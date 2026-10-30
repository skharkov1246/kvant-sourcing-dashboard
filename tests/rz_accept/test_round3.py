"""Приёмка «Реализации» R4, раунд 3: то, что прежние проверки не ловили (ревью 30.10.2026).

П-2 по таблице 3.10 для каждого экрана (а не только каркас и [data-kpi-rank=1]); П-9 для списков
групп «Что сделать сегодня» и людей «Кто сколько ведёт» (адрес без имён, «Ссылка на этот список»,
«Назад» по уровням), прежний адрес «tag.late»; «Назад» после клика по плитке; Esc со сделки,
открытой из заказа; цели касания KPI и счётчиков на 390; структура списка дел на телефоне;
«Смотреть как» — число в выводе «Недели» = строки его списка. Корпус придуман (правило 18).
"""
from __future__ import annotations

import re

import pytest

from tests.rz_accept.conftest import mark

pytest.importorskip("playwright.sync_api")

# что должно быть целиком (full) или хотя бы начаться (start) до innerHeight — спецификация, 3.10.
# «Неделя», 1280: «Что сделать сегодня» — три части (спецификация «сделка и заказ поставщика», §2): строка
# части A «Дела по заказам» со ссылкой «Порядок в карточках» стоит над группами — в первом экране 3 группы
SUPROW = "#tab-contracts .ctm-sup tbody tr:not(.ctm-gh):has(td:nth-child(3))"
TABLE_3_10 = {
    "week": {"desktop": [("фраза", "#wkLead", 1, "full"), ("3 плитки", "#orkTiles .wkt", 3, "full"),
                         ("5 счётчиков", "#wkMore li.wkc", 5, "full"), ("3 группы действий", "#orkToday li.wkg", 3, "full")],
             "phone": [("фраза", "#wkLead", 1, "full"), ("KPI 1–3 строками", "#orkTiles .wkt", 3, "full"),
                       ("1 группа действий", "#orkToday li.wkg", 1, "full")]},
    "month": {"desktop": [("фраза", "#ctmMonH", 1, "full"), ("буллеты", ".ctmks .ctmk", 2, "full"),
                          ("«Что исправить» (начало)", ".ctm-fix h2", 1, "start")],
              "phone": [("фраза", "#ctmMonH", 1, "full"), ("буллет 1–2", ".ctmks .ctmk", 2, "full")]},
    "quarter": {"desktop": [("фраза", "#ctmQH", 1, "full"), ("буллет Q1", ".ctm-q1g .ctmk", 1, "full"),
                            ("5 поставщиков", SUPROW, 5, "full")],
                "phone": [("фраза", "#ctmQH", 1, "full"), ("буллет Q1", ".ctm-q1g .ctmk", 1, "full")]},
    "main": {"desktop": [("фраза", "#ctdSay", 1, "full"), ("3 плитки", ".ctd-tile", 3, "full"),
                         ("«Требует решения»", "#ctdB", 1, "start")],
             "phone": [("фраза", "#ctdSay", 1, "full"), ("3 KPI строками", ".ctd-tile", 3, "full")]},
    "how": {"desktop": [("оглавление", "#tab-contracts nav.ctm-toc", 1, "full"), ("начало словаря", "#ctmHowDefs", 1, "start")],
            "phone": [("оглавление", "#tab-contracts nav.ctm-toc", 1, "full")]},
}
VIS = r"""(spec)=>{const H=innerHeight, W=innerWidth, bad=[];
  for(const [name,sel,cnt,mode] of spec){const els=[...document.querySelectorAll(sel)].filter(e=>e.getClientRects().length).slice(0,cnt);
    const ok=els.length>=cnt&&els.every(e=>{const r=e.getBoundingClientRect(); return mode==='start'?r.top<H-16&&r.bottom>0:r.bottom<=H+0.5&&r.top>=-1&&r.right<=W+1;});
    if(!ok) bad.push(name+' '+els.map(e=>{const r=e.getBoundingClientRect(); return Math.round(r.top)+'-'+Math.round(r.bottom);}).join(','));}
  return bad;}"""


@pytest.mark.parametrize("vp", ["desktop", "phone"])
@pytest.mark.parametrize("sub", list(TABLE_3_10))
def test_p02_таблица_3_10(open_page, cfg, sub, vp):
    w, h = cfg["viewports"][vp]
    pg = open_page(f"#contracts/{sub}", w, h, "light")
    pg.wait_for_timeout(250)
    bad = pg.evaluate(VIS, [list(x) for x in TABLE_3_10[sub][vp]])
    mark(cfg["P2"]["enforce_kpi"], not bad, f"{sub} {w}×{h}: не в первом экране — {bad}")
    assert not pg.errors, pg.errors


STATE = """(()=>{const d=document.getElementById('drw'),on=d.classList.contains('on');
  const kind=!on?'none':document.querySelector('#drwBody #opRoot')?'order':document.querySelector('#drwBody .odlede,#drwBody #odTable')?'list'
    :document.querySelector('#drwBody .rzc')?'deal':'other';
  const a=document.activeElement;
  return {kind,hash:location.hash,name:on?document.getElementById('drwName').innerText:'',
    focusInHidden:!on&&d.contains(a),focus:(a&&(a.className||a.tagName))+''}})()"""


def _click(pg, js):
    pg.evaluate(f"(()=>{{const b={js}; b.scrollIntoView({{block:'center'}}); b.click();}})()")
    pg.wait_for_timeout(250)


@pytest.mark.parametrize("start,js,want", [
    ("группа дел", "document.querySelector('#orkToday li.wkg button[data-of=queue]')", r"n=queue&f=code\.\w+"),
    ("человек", "[...document.querySelectorAll('#orkPulseB button.odn')].find(b=>/w:who\\//.test(b.dataset.of))",
     r"n=w%3Awho%2F(\d+|none)%2F(over|late|wait)"),
])
def test_p09_списки_групп_и_людей_с_адресом(open_page, browser, smoke, cfg, start, js, want):
    pg = open_page("#contracts/week")
    _click(pg, js)
    s1 = pg.evaluate(STATE)
    rows1 = pg.evaluate("document.querySelectorAll('#drwBody #odTable tr[data-odk]').length")
    ok = s1["kind"] == "list" and re.search(want, s1["hash"]) and not re.search(r"[А-Яа-яЁё]", s1["hash"])
    ok = ok and pg.query_selector("#drwBody .odcopy") is not None
    mark(cfg["P9"]["enforce"], bool(ok), f"{start}: адрес списка {s1}, «Ссылка на этот список» — {bool(pg.query_selector('#drwBody .odcopy'))}")
    # заказ → «Назад» — список того же числа; ещё «Назад» — панель закрыта, адрес вкладки, фокус не в панели
    pg.click("#drwBody #odTable tr[data-odk]")
    pg.wait_for_timeout(250)
    s2 = pg.evaluate(STATE)
    pg.go_back()
    pg.wait_for_timeout(300)
    s3 = pg.evaluate(STATE)
    pg.go_back()
    pg.wait_for_timeout(300)
    s4 = pg.evaluate(STATE)
    seq = s2["kind"] == "order" and "&o=" in s2["hash"] and s3["kind"] == "list" and s3["hash"] == s1["hash"] \
        and s4["kind"] == "none" and s4["hash"] == "#contracts/week" and not s4["focusInHidden"]
    mark(cfg["P9"]["enforce"], seq, f"{start}: «Назад» {s2} → {s3} → {s4}")
    # тот же адрес в чистом окне — тот же список, столько же строк
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    q = ctx.new_page()
    q.goto(smoke + s1["hash"])
    q.wait_for_timeout(400)
    rows2 = q.evaluate("document.querySelectorAll('#drwBody #odTable tr[data-odk]').length")
    ctx.close()
    mark(cfg["P9"]["enforce"], rows1 == rows2 > 0, f"{start}: из адреса {rows2} строк, кликом {rows1}")
    assert not pg.errors, pg.errors


def test_p09_прежний_адрес_tag_late(open_page, cfg):
    """ia_copy 1.4: «#contracts/week?n=w%3AW3&f=tag.late» — та же часть W3 (part.late), без сырого кода."""
    pg = open_page("#contracts/week?n=w%3AW3&f=tag.late")
    pg.wait_for_timeout(300)
    rows = pg.evaluate("document.querySelectorAll('#drwBody #odTable tr[data-odk]').length")
    late = pg.evaluate("window.__CONTRACTS__.ork.week.tiles.W3.byOrders.late")
    chips = pg.evaluate("[...document.querySelectorAll('#drwBody .odactive .stchip')].map(b=>b.textContent)")
    raw = [c for c in chips if re.search(r"\b(late|risk|forecast)\b", c)]
    mark(cfg["P9"]["enforce"], rows == late and not raw and "part.late" in pg.evaluate("location.hash"),
         f"tag.late: строк {rows} (сорвано {late}), фишки {chips}, адрес {pg.evaluate('location.hash')}")


def test_p09_назад_после_плитки_закрывает(open_page, cfg):
    pg = open_page("#contracts/week")
    pg.click('#orkTiles [data-kpi-rank="1"]')
    pg.wait_for_timeout(250)
    s1 = pg.evaluate(STATE)
    pg.go_back()
    pg.wait_for_timeout(300)
    s2 = pg.evaluate(STATE)
    mark(cfg["P9"]["enforce"], s1["kind"] == "list" and s2["kind"] == "none" and not s2["focusInHidden"],
         f"плитка → «Назад»: {s1} → {s2}")


def test_esc_со_сделки_из_заказа_на_уровень_вверх(open_page, cfg):
    """Список → заказ → «Сделка №… →» → Esc: список, фокус на строке; ещё Esc — закрыто, фокус на числе."""
    pg = open_page("#contracts/week")
    pg.click('#orkTiles [data-kpi-rank="1"]')
    pg.wait_for_timeout(250)
    pg.click("#drwBody #odTable tr[data-odk]")
    pg.wait_for_timeout(250)
    pg.click("#drwBody [data-opgo='card']")
    pg.wait_for_timeout(300)
    s0 = pg.evaluate(STATE)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    s1 = pg.evaluate(STATE)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    s2 = pg.evaluate(STATE)
    ok = s0["kind"] == "deal" and s1["kind"] == "list" and "odgo" in s1["focus"] and s2["kind"] == "none" and not s2["focusInHidden"]
    mark(cfg["P9"]["enforce"], ok, f"Esc: {s0} → {s1} → {s2}")


def _tap(pg, sel):
    """Нажатие пальцем по подписи (не по числу): точка в середине подписи — попадает в растянутую кнопку."""
    r = pg.evaluate(f"(()=>{{const e=document.querySelector('{sel}'); e.scrollIntoView({{block:'center'}}); const r=e.getBoundingClientRect(); return [r.left+r.width/2,r.top+r.height/2]}})()")
    pg.mouse.click(r[0], r[1])
    pg.wait_for_timeout(250)


def test_p06_строки_kpi_и_счётчиков_на_390(open_page, cfg):
    """На 390 вся строка KPI и счётчика — цель касания ≥ 44 px; нажатие на подпись открывает список."""
    pg = open_page("#contracts/week", 390, 844)
    small = pg.evaluate("""[...document.querySelectorAll('#orkTiles .wkt')].map(e=>Math.round(e.getBoundingClientRect().height)).filter(h=>h<44)""")
    _tap(pg, "#wkTLate .wkth")
    s1 = pg.evaluate(STATE)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    pg.click("#wkMore > summary")
    pg.wait_for_timeout(150)
    cnt = pg.evaluate("""[...document.querySelectorAll('#wkMore li.wkc')].filter(li=>li.querySelector(':scope>button.odn')).map(li=>{
      const r=li.getBoundingClientRect(), b=li.querySelector(':scope>button.odn'); const rb=b.getBoundingClientRect();
      const s=getComputedStyle(b,'::before'); return [Math.round(r.height), s.position, s.content]})""")
    _tap(pg, "#wkMore li.wkc .wkcl")
    s2 = pg.evaluate(STATE)
    ok = not small and s1["kind"] == "list" and all(h >= 44 and p == "absolute" for h, p, _c in cnt) and s2["kind"] == "list"
    mark(cfg["P6"]["enforce_tab"], ok, f"390: KPI ниже 44 — {small}; подпись KPI → {s1['kind']}; счётчики {cnt}; подпись счётчика → {s2['kind']}")


def test_список_дел_на_телефоне_только_li(open_page, cfg):
    """WCAG 1.3.1: раскрытая группа на телефоне — внутри своего <li>, у <ul> дети — только li."""
    pg = open_page("#contracts/week", 390, 844)
    bad = pg.evaluate("[...document.querySelectorAll('#orkToday ul.wkgl > *')].filter(e=>e.tagName!=='LI').map(e=>e.tagName+'#'+e.id)")
    inside = pg.evaluate("!!document.querySelector('#orkToday ul.wkgl > li > #wkqD')")
    mark(cfg["P6"]["enforce_tab"], not bad and inside, f"дети списка дел не li: {bad}; раскрытая группа в li — {inside}")


def test_смотреть_как_число_вывода_равно_списку(open_page, cfg):
    """«Смотреть как» КАМа: первое число вывода «Недели» — его заказы, столько же строк в списке."""
    pg = open_page("#contracts/week")
    k = pg.evaluate("(_rzPeople().find(p=>p.role==='kam')||{}).id||''")
    assert k
    pg.select_option("#rzKam", k)
    pg.wait_for_timeout(300)
    b = pg.query_selector("#wkLead button.rzn")
    n = int(b.inner_text())
    b.click()
    pg.wait_for_timeout(300)
    rows = pg.evaluate("document.querySelectorAll('#drwBody #odTable tr[data-odk]').length")
    allv = pg.evaluate("window.__CONTRACTS__.ork.week.tiles.W3.byOrders.late")
    lead = pg.inner_text("#wkLead") if pg.query_selector("#wkLead") else ""
    mark(cfg["P9"]["enforce"], n == rows and "По отделу" in lead,
         f"«Смотреть как»: число {n}, строк {rows}, по отделу {allv}; вывод «{lead[:120]}»")


def test_журнал_шагов_на_390_номер_заказа_виден(open_page, cfg):
    pg = open_page("#contracts/month", 390, 844)
    w = pg.evaluate("""(()=>{const b=document.querySelector('#orkJb tr td:nth-child(2) button'); return b?Math.round(b.getBoundingClientRect().width):-1})()""")
    hid = pg.evaluate("""[2,3,4,5,6,7,11].filter(i=>{const td=document.querySelector('#orkJb tr td:nth-child('+i+')'); return td&&getComputedStyle(td).display==='none'})""")
    mark(cfg["P6"]["enforce_tab"], w > 0 and not hid, f"журнал на 390: ширина номера {w}, скрытые столбцы {hid}")
