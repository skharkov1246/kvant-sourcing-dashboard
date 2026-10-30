"""Приёмка «Сорсинга» — проверки П-1…П-10 (второй круг, 30.10.2026) на smoke-сборке, по образцу
приёмки «Реализации» R4 (tests/rz_accept): первый экран, путь от числа до Битрикса, числа-тупики,
шрифт и цели касания, красная площадь, градиенты, адреса уровней, высота подвкладок. Нет Playwright
или браузера — пропуск. Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import os
import re

import pytest

from tests.so_accept.conftest import mark

pytest.importorskip("playwright.sync_api")

PANEL = "#tab-sourcing .sopanel:not([hidden])"
TYPE = r"""(sel)=>{const R=[...document.querySelectorAll(sel)], small=[], mono=[]; let caps=0, n=0;
  R.forEach(r=>{const w=document.createTreeWalker(r,NodeFilter.SHOW_TEXT);
    for(let t=w.nextNode();t;t=w.nextNode()){ if(!t.nodeValue.trim()) continue; const el=t.parentElement;
      if(!el||!el.getClientRects().length||el.closest('.sovh,svg,script,style,[hidden]')) continue;
      {const d=el.closest('details'); if(d&&!d.open&&!el.closest('summary')) continue;} n++;
      const cs=getComputedStyle(el), fs=parseFloat(cs.fontSize);
      if(fs<12.95) small.push(fs+'px «'+t.nodeValue.trim().slice(0,30)+'» '+el.className);
      if(/mono/i.test(cs.fontFamily.split(',')[0])&&!el.closest('.mono,code')) mono.push('«'+t.nodeValue.trim().slice(0,20)+'» '+el.className);
      if(cs.textTransform==='uppercase') caps++; }});
  return {n,small:small.length,smallEx:small.slice(0,6),mono:mono.length,monoEx:mono.slice(0,6),caps};}"""


# ---------------------------------------------------------------- П-1 тест 5 секунд (люди)
def test_p01_снимки_для_теста_5_секунд(open_page, cfg):
    out = os.environ.get("SO_SHOTS")
    if not out:
        pytest.skip("П-1 — проверка людьми; SO_SHOTS=<папка> снимет экраны для неё")
    os.makedirs(out, exist_ok=True)
    for sub in cfg["subs"]:
        for (w, h) in cfg["viewports"].values():
            pg = open_page(f"#sourcing/{sub}", w, h)
            pg.screenshot(path=f"{out}/p1_{sub}_{w}.png")


# ---------------------------------------------------------------- П-2 первый экран
@pytest.mark.parametrize("vp", ["desktop", "phone"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_p02_первый_экран(open_page, cfg, vp, theme):
    w, h = cfg["viewports"][vp]
    pg = open_page("#sourcing/today", w, h, theme)
    need = {"заголовок": "#tab-sourcing header h1", "строка доверия": "#subline", "подвкладка": "#soTabs [role=tab][aria-selected=true]",
            "вывод": "#soVerdict", "первое дело «Сегодня»": "#soToday tr[data-sq]"}
    if vp == "desktop":                 # на телефоне «Сегодня» — сразу под выводом, плитки ниже (как у «Реализации»)
        need["плитка"] = "#kpis .sotile"
    miss = pg.evaluate("""(N)=>Object.entries(N).filter(([k,s])=>{const e=document.querySelector(s); if(!e) return true;
      const r=e.getBoundingClientRect(); return !(r.width>0&&r.height>0&&r.bottom<=innerHeight&&r.right<=innerWidth+1);}).map(x=>x[0])""", need)
    mark(cfg["P2"]["enforce"], not miss, f"{vp}/{theme}: не на первом экране — {miss}")
    mark(cfg["P2"]["enforce"], not pg.evaluate("document.documentElement.scrollWidth>innerWidth+1"), f"{vp}: прокрутка страницы вбок")
    assert not pg.errors, pg.errors


# ---------------------------------------------------------------- П-3 до Битрикса ≤ 3 действий
@pytest.mark.parametrize("sub", ["today", "pulse", "people", "rfq"])
def test_p03_от_числа_до_битрикса(open_page, cfg, sub):
    pg = open_page(f"#sourcing/{sub}")
    sel = f"{PANEL} :is(button[data-so],[role=button][data-so])"
    n = min(pg.evaluate(f"document.querySelectorAll('{sel}').length"), cfg["P3"]["per_screen_cap"])
    bad = []
    for i in range(n):
        pg.evaluate(f"(()=>{{const b=document.querySelectorAll('{sel}')[{i}]; b.scrollIntoView({{block:'center'}}); b.click();}})()")
        pg.wait_for_timeout(60)
        ok = pg.query_selector('#drw.on #drwBody a[href*="bitrix24"]') is not None
        if not ok:
            bad.append(pg.evaluate(f"document.querySelectorAll('{sel}')[{i}].dataset.so"))
        pg.evaluate("(()=>{const d=document.getElementById('drw'); if(d&&d.classList.contains('on')) document.getElementById('drwX').click();})()")
        pg.wait_for_timeout(30)
    mark(cfg["P3"]["enforce"], not bad, f"{sub}: чисел без пути до Битрикса за 2 действия — {len(bad)} из {n}: {bad[:6]}")


# ---------------------------------------------------------------- П-4 тупики
# Число — счёт множества («12 запросов», «3 сделки») или голая мера в ячейке («12», «25 %»): за ним
# стоит список. Не тупики: даты, номера недель и годы, нормы («2 раб. дня»), имена корпуса, ноль,
# шапки и итоги таблиц, строки, которые открываются сами (data-uid, data-sq), заголовки над списком.
DEAD = r"""(()=>{const root=document.querySelector('#tab-sourcing .sopanel:not([hidden])'); const out=[];
  const CNT=/(^|[^\d\/.,])\d[\d   ]*\s*(запрос|сдел|карточ|поставщик|КП\b|человек|пункт)/i;
  const BARE=/^\s*[≤≥~≈+−-]?\s*\d[\d   ]*(,\d+)?\s*(%|из\s+\d+)?\s*$/;
  const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  for(let t=w.nextNode();t;t=w.nextNode()){ const s=t.nodeValue; const el=t.parentElement;
    if(!el||!el.getClientRects().length||el.closest('script,style,select,option,.sovh,[hidden],[aria-hidden="true"],svg')) continue;
    {const d=el.closest('details'); if(d&&!d.open&&!el.closest('summary')) continue;}
    const x=s.replace(/\b\d{1,2}\.\d{1,2}(\.\d{4})?\b/g,'').replace(/\b\d{1,2}[–-]\d{1,2}\.\d{2}\b/g,'').replace(/\b(19|20)\d\d\b/g,'')
      .replace(/№\s?\d+/g,'').replace(/\bW\d+\b/g,'').replace(/\d+\s*\/\s*5/g,'').replace(/(Компания|Поставщик|Менеджер)[-\s]+\d+/g,'')
      .replace(/\d+[–-]\d+\s*раб/g,'').replace(/\d+\s*(раб\.?\s*(дн|день|дня|дней)|дн\.?|дней|дня|день|ч\b|мин)/g,'');
    if(!CNT.test(x)&&!BARE.test(x)) continue;
    if(/^\s*0\s*$/.test(x)) continue;
    if(el.closest('button,a[href],[data-so],[data-uid],[data-wk],th,thead,tfoot,caption,summary,label,.sozero,.soreg,.sogl,.sowhy,.sonote,.dep,.sosub,.legendrow,.sohowb,.sodelta,.soratio,h1,h2,h3,h4')) continue;
    const tr=el.closest('tr'); if(tr&&(tr.matches('[data-sq],[data-uid],.clickrow,.drill')||tr.querySelector('button[data-so],a[href]'))) continue;
    out.push(s.trim().slice(0,40)+' · '+(el.closest('section[aria-labelledby]')||{id:''}).getAttribute?.('aria-labelledby')); }
  return out;})()"""


@pytest.mark.parametrize("sub", ["today", "pulse", "people", "rfq"])
def test_p04_тупиков_нет(open_page, cfg, sub):
    pg = open_page(f"#sourcing/{sub}")
    dead = pg.evaluate(DEAD)
    mark(cfg["P4"]["enforce"], len(dead) <= cfg["P4"]["dead_max"], f"{sub}: чисел-тупиков {len(dead)}: {dead[:8]}")


# ---------------------------------------------------------------- П-6 шрифт и цели касания
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_p06_шрифт_и_касание(open_page, cfg, theme):
    bad = []
    for sub in cfg["subs"]:
        pg = open_page(f"#sourcing/{sub}", 1280, 800, theme)
        t = pg.evaluate(TYPE, "#tab-sourcing")
        if t["small"] or t["caps"] or t["mono"]:
            bad.append(f"{sub}: мельче 13 px {t['small']} {t['smallEx'][:2]}, капс {t['caps']}, моно {t['monoEx'][:2]}")
    ph = open_page("#sourcing/today", 390, 844, theme)
    tap = ph.evaluate("""(t)=>[...document.querySelectorAll('#soTabs [role=tab],#soAs,#soToday button[data-so]')]
      .filter(e=>e.getClientRects().length).map(e=>{const r=e.getBoundingClientRect(); return [e.id||e.textContent.trim().slice(0,12),Math.round(r.width),Math.round(r.height)]})
      .filter(x=>x[2]<t)""", cfg["P6"]["tap_px"])
    if tap:
        bad.append(f"390: цели касания ниже 44 px — {tap[:4]}")
    mark(cfg["P6"]["enforce"], not bad, "; ".join(bad))


# ---------------------------------------------------------------- П-7 красная площадь
def test_p07_красная_площадь(open_page, cfg, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    pg = open_page("#sourcing/today", 1280, 800)
    nr = pg.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--nr').trim()")
    rgb = tuple(int(nr.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
    f = tmp_path / "red.png"
    pg.screenshot(path=str(f))
    im = Image.open(f).convert("RGB")
    px = list(im.getdata())
    share = sum(1 for p in px if sum(abs(a - b) for a, b in zip(p, rgb)) < 60) / max(1, len(px))
    mark(cfg["P7"]["enforce"], share <= cfg["P7"]["red_max"], f"красная площадь первого экрана {share:.2%}")


# ---------------------------------------------------------------- П-8 градиенты
def test_p08_градиентов_нет(open_page, cfg):
    pg = open_page("#sourcing/today")
    g = pg.evaluate("""(()=>{const out=[]; for(const s of document.styleSheets){let R; try{R=s.cssRules}catch(e){continue}
      const walk=L=>{for(const r of L){ if(r.cssRules&&!r.selectorText) walk(r.cssRules);
        else if(r.selectorText&&/#tab-sourcing/.test(r.selectorText)&&/gradient\\(/.test(r.cssText)) out.push(r.selectorText.slice(0,80)); }};
      walk(R);}
      document.querySelectorAll('#tab-sourcing [style*="gradient"]').forEach(e=>out.push('style: '+e.className));
      const vis=[...document.querySelectorAll('#tab-sourcing *')].filter(e=>e.getClientRects().length&&/gradient\\(/.test(getComputedStyle(e).backgroundImage)).map(e=>'вид: '+e.className);
      return out.concat(vis.slice(0,5));})()""")
    mark(cfg["P8"]["enforce"], not g, f"градиенты во вкладке: {g[:5]}")


# ---------------------------------------------------------------- П-9 адрес
STATE = """(()=>{const d=document.getElementById('drw'),on=d.classList.contains('on');
  const kind=!on?'none':document.querySelector('#drwBody #soDrwF')?'sourcer':/^Списка по ссылке нет/.test(document.getElementById('drwName').textContent)?'missing':document.querySelector('#drwBody .sowhyd')?'list':'other';
  const t=document.querySelector('#soTabs [aria-selected=true]'); return {kind,sub:t&&t.dataset.sub,hash:location.hash}})()"""


def test_p09_адрес_открывает_уровни(open_page, cfg, browser, smoke):
    pg = open_page("#sourcing/today")
    uid = pg.evaluate("String((window.__DATA__.sourcersA||[]).find(r=>r.act!==false).id)")
    cases = [("#sourcing/pulse", "none", "pulse"), ("#sourcing/today?n=pawStale", "list", "today"),
             (f"#sourcing/people?n=pawStale&u={uid}", "list", "people"), (f"#sourcing/people?p={uid}", "sourcer", "people"),
             ("#sourcing/rfq?n=nosuchkey", "missing", "rfq"), ("#sourcing/how", "none", "how"), ("#sourcing", "none", "today")]
    bad = []
    for h, kind, sub in cases:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        q = ctx.new_page()
        q.goto(smoke + h)
        q.wait_for_timeout(400)
        st = q.evaluate(STATE)
        if (st["kind"], st["sub"]) != (kind, sub):
            bad.append(f"{h} → {st}")
        if re.search(r"[А-Яа-яЁё]", st["hash"]):
            bad.append(f"{h}: кириллица в адресе {st['hash']}")
        ctx.close()
    mark(cfg["P9"]["enforce"], not bad, f"адреса не открыли свой уровень: {bad}")


def test_p09_назад_шагает_по_уровням(open_page, cfg):
    pg = open_page("#sourcing/today")
    pg.click('#soToday button[data-so$="pawStale"]')
    pg.wait_for_timeout(200)
    s1 = pg.evaluate(STATE)
    pg.click("#drwBody .solnk")
    pg.wait_for_timeout(200)
    s2 = pg.evaluate(STATE)
    pg.go_back()
    pg.wait_for_timeout(300)
    s3 = pg.evaluate(STATE)
    pg.go_back()
    pg.wait_for_timeout(300)
    s4 = pg.evaluate(STATE)
    ok = (s1["kind"] == "list" and s2["kind"] == "sourcer" and "?p=" in s2["hash"] and s3["kind"] == "list"
          and s4["kind"] == "none" and s4["hash"] == "#sourcing/today")
    mark(cfg["P9"]["enforce"], ok, f"«Назад»: {s1} → {s2} → {s3} → {s4}")
    pg.click('#soToday button[data-so$="pawStale"]')
    pg.wait_for_timeout(200)
    pg.click("#drwX")
    pg.wait_for_timeout(200)
    mark(cfg["P9"]["enforce"], pg.evaluate("location.hash") == "#sourcing/today", "× не снял уровни из адреса")
    assert not pg.errors, pg.errors


# ---------------------------------------------------------------- П-10 высота подвкладок
def test_p10_высота_подвкладок(open_page, cfg):
    bad = []
    for sub in cfg["subs"]:
        for w, h in (cfg["viewports"]["desktop"], cfg["viewports"]["phone"]):
            pg = open_page(f"#sourcing/{sub}", w, h)
            pg.evaluate("document.querySelectorAll('#tab-sourcing details[open]').forEach(d=>d.open=false)")
            hh = pg.evaluate("Math.round(document.getElementById('tab-sourcing').getBoundingClientRect().height)")
            lim = cfg["P10"]["max_1280"] if w == 1280 else cfg["P10"]["max_390"]
            if hh > lim:
                bad.append(f"{sub} {w}: {hh} px (≤ {lim})")
    mark(cfg["P10"]["enforce"], not bad, f"высота: {bad}")


# ---------------------------------------------------------------- аудит 30.10.2026: то, что было сломано
def test_аудит_подвкладка_на_телефоне_одна(open_page, cfg):
    """`display:flex` у «Сегодня» на телефоне перебивал `hidden`: «Сегодня» стояло на всех подвкладках."""
    bad = []
    for sub in cfg["subs"]:
        if sub == "today":
            continue
        pg = open_page(f"#sourcing/{sub}", *cfg["viewports"]["phone"])
        if pg.evaluate("getComputedStyle(document.getElementById('soPanel-today')).display") != "none":
            bad.append(sub)
        sw = pg.evaluate("[...document.querySelectorAll('#tab-sourcing .sopanel:not([hidden]) .twrap')]"
                         ".filter(t=>t.getClientRects().length&&t.scrollWidth>t.clientWidth+2).map(t=>t.querySelector('table')?.className)")
        if sub in ("pulse", "today") and sw:
            bad.append(f"{sub}: прокрутка вбок {sw}")
    assert not bad, bad


def test_аудит_панель_диалог_и_tab_внутри(open_page):
    pg = open_page("#sourcing/today")
    pg.focus("#soToday button[data-so]")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(150)
    aria = pg.evaluate("['role','aria-modal','aria-labelledby'].map(a=>document.getElementById('drw').getAttribute(a))")
    assert aria == ["dialog", "true", "drwName"], aria
    out = 0
    for _ in range(25):
        pg.keyboard.press("Tab")
        out += not pg.evaluate("document.getElementById('drw').contains(document.activeElement)")
    assert out == 0, f"Tab ушёл из панели {out} раз из 25"
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    assert pg.evaluate("document.activeElement.matches('#soToday button[data-so]')"), "фокус не вернулся на число"
    assert pg.evaluate("document.getElementById('drw').getAttribute('role')") is None


def test_аудит_знак_вопроса_кнопка(open_page):
    pg = open_page("#sourcing/today")
    bad = pg.evaluate("[...document.querySelectorAll('#tab-sourcing .qq')].filter(q=>q.getAttribute('role')!=='button'||q.tabIndex!==0||!/^Как считается/.test(q.getAttribute('aria-label')||'')).length")
    assert bad == 0, bad
    pg.evaluate("[...document.querySelectorAll('#tab-sourcing .qq')].find(z=>z.getClientRects().length).id='qqT'")
    pg.focus("#qqT")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(150)
    assert pg.evaluate("document.getElementById('drw').classList.contains('on')")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    assert pg.evaluate("document.activeElement.id") == "qqT"


def test_аудит_сегодня_дело_один_раз(open_page):
    """Сделка без сорсера — только в «Назначить сорсера»; запросы ушедшего — только в «Переназначить»."""
    pg = open_page("#sourcing/today")
    r = pg.evaluate("""()=>{const by=SOQ.by, ids=k=>(by[k]||{rows:[]}).rows.map(x=>x.kind+':'+x.id);
      const blk=new Set(ids('ps-src')), gone=new Set(ids('gone')), dup=[];
      SOQ.items.concat(SOQ.cards).forEach(it=>{const b=it.base||it.key; if(b==='ps-src'||b==='gone') return;
        it.rows.forEach(x=>{const k=x.kind+':'+x.id; if(blk.has(k)||(x.kind==='card'&&gone.has(k))) dup.push(b+' '+k);});});
      const who=SOQ.items.filter(it=>/сорсер/.test(it.who)&&it.rows.some(x=>x.who==='руководитель сорсинга')).map(it=>it.key);
      return {dup:dup.slice(0,5),n:dup.length,who};}""")
    assert r["n"] == 0 and not r["who"], r
