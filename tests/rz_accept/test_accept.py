"""Приёмка «Реализации» R4 — проверки П-1…П-10 (спецификация R4, раздел 7) на smoke-сборке.

Каркас WP-A: каждая проверка написана целиком; чужие экраны (пакеты B–F) меряются и, пока
config.json не включит enforce, помечаются xfail с замером. Свою часть WP-A проверяет строго:
шапка, строка подвкладок, «Смотреть как», направление (П-2, П-5, П-6 «каркас»), токены цвета
(П-7), градиенты (П-8), адрес и «Назад» (П-9). Нет Playwright или браузера — пропуск.
"""
from __future__ import annotations

import os
import re

import pytest

from tests.rz_accept.conftest import mark

pytest.importorskip("playwright.sync_api")

SUBS = ["week", "month", "quarter", "main", "how"]
FRAME = "#tab-contracts > header.rzhd, #tab-contracts > .ctbar, #tab-contracts > .rzalarm, #tab-contracts > .rzkamnote"

# видимые текстовые узлы под селекторами: размер шрифта, моно, капс с разрядкой
TYPE = r"""(sel)=>{const R=[...document.querySelectorAll(sel)], small=[], mono=[]; let caps=0, n=0;
  R.forEach(r=>{const w=document.createTreeWalker(r,NodeFilter.SHOW_TEXT);
    for(let t=w.nextNode();t;t=w.nextNode()){ if(!t.nodeValue.trim()) continue; const el=t.parentElement;
      if(!el||!el.getClientRects().length||el.closest('.ctvh,.orkvh,svg,script,style,[hidden]')) continue; n++;
      const cs=getComputedStyle(el), fs=parseFloat(cs.fontSize);
      if(fs<12.95) small.push(fs+'px «'+t.nodeValue.trim().slice(0,30)+'» '+el.className);
      if(/mono/i.test(cs.fontFamily.split(',')[0])&&!el.closest('.rzno,.mono,.rzid,code')) mono.push('«'+t.nodeValue.trim().slice(0,20)+'» '+el.className);
      if(cs.textTransform==='uppercase'&&parseFloat(cs.letterSpacing)>0) caps++; }});
  return {n,small:small.length,smallEx:small.slice(0,6),mono:mono.length,monoEx:mono.slice(0,6),caps};}"""


def _hex(c: str):
    c = c.strip()
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", c)
    if m:
        return tuple(int(x) for x in m.groups())
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(_hex(a)), _lum(_hex(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ---------------------------------------------------------------- П-1 тест 5 секунд (люди)
def test_p01_снимки_для_теста_5_секунд(open_page, cfg):
    """Тест 5 секунд проводят люди (2 из 2). Здесь — только снимки всех экранов в RZ_SHOTS."""
    out = os.environ.get("RZ_SHOTS")
    if not out:
        pytest.skip("П-1 — проверка людьми; RZ_SHOTS=<папка> снимет экраны для неё")
    os.makedirs(out, exist_ok=True)
    for sub in SUBS:
        for (w, h) in cfg["viewports"].values():
            for th in ("light", "dark"):
                pg = open_page(f"#contracts/{sub}", w, h, th)
                pg.screenshot(path=f"{out}/p1_{sub}_{w}_{th}.png")


# ---------------------------------------------------------------- П-2 первый экран
@pytest.mark.parametrize("vp", ["desktop", "phone"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_p02_первый_экран(open_page, cfg, vp, theme):
    w, h = cfg["viewports"][vp]
    pg = open_page("#contracts/week", w, h, theme)
    need = {"h1": "#tab-contracts header.rzhd h1", "дата данных": "#tab-contracts .rzasof",
            "заказы в работе": "#ctLive button.rzn", "подвкладки": "#ctTabs [role=tab][aria-selected=true]",
            "смотреть как": "#rzKam", "направление": "#ctDirs [role=tab][aria-selected=true]"}
    miss = pg.evaluate("""(N)=>Object.entries(N).filter(([k,s])=>{const e=document.querySelector(s); if(!e) return true;
      const r=e.getBoundingClientRect(); return !(r.width>0&&r.height>0&&r.bottom<=innerHeight&&r.right<=innerWidth+1);}).map(x=>x[0])""", need)
    mark(cfg["P2"]["enforce_frame"], not miss, f"{vp}/{theme}: не на первом экране — {miss}")
    hscroll = pg.evaluate("document.documentElement.scrollWidth>innerWidth+1")
    mark(cfg["P2"]["enforce_frame"], not hscroll, f"{vp}: горизонтальная прокрутка страницы")
    kpi = pg.evaluate("""(()=>{const e=document.querySelector('#tab-contracts [role=tabpanel]:not([hidden]) [data-kpi-rank="1"]');
      if(!e) return 'нет'; const r=e.getBoundingClientRect(); return r.bottom<=innerHeight?'да':'ниже экрана'})()""")
    mark(cfg["P2"]["enforce_kpi"], kpi == "да", f"{vp}/{theme}: главный показатель [data-kpi-rank=1] — {kpi}")


# ---------------------------------------------------------------- П-3 до Битрикса ≤ 3 действий
@pytest.mark.parametrize("sub", ["week", "main"])
def test_p03_от_числа_до_битрикса(open_page, cfg, sub):
    pg = open_page(f"#contracts/{sub}")
    sel = "#tab-contracts [role=tabpanel]:not([hidden]) :is(button.rzn[data-of],button[data-od],[data-od][role=button])"
    n = min(pg.evaluate(f"document.querySelectorAll('{sel}').length"), cfg["P3"]["per_screen_cap"])
    bad = []
    for i in range(n):
        pg.evaluate(f"(()=>{{const b=document.querySelectorAll('{sel}')[{i}]; b.scrollIntoView({{block:'center'}}); b.click();}})()")
        pg.wait_for_timeout(80)
        steps = 2 if pg.query_selector('#drw.on #drwBody a[href*="bitrix24"]') else None
        if steps is None:
            row = pg.query_selector("#drw.on #drwBody tr[data-odk], #drw.on #drwBody [data-odct]")
            if row:
                row.click()
                pg.wait_for_timeout(80)
                steps = 3 if pg.query_selector('#drw.on #drwBody a[href*="bitrix24"]') else None
        if steps is None or steps > cfg["P3"]["max_actions"]:
            bad.append(i)
        pg.evaluate("(()=>{const d=document.getElementById('drw'); if(d&&d.classList.contains('on')) document.getElementById('drwX').click();})()")
        pg.wait_for_timeout(40)
    mark(cfg["P3"]["enforce"], not bad, f"{sub}: чисел без пути до Битрикса за ≤3 действия — {len(bad)} из {n}")


# ---------------------------------------------------------------- П-4 тупики
# Число — счёт множества («19 заказов», «3 сделки», «4 пункта») или голая мера в ячейке («12», «25 %»,
# «3 из 12»): за ним стоит список (спец. 2.1, П-4). Не числа-тупики: даты, номера, годы, пороги и нормы
# («≤ 14 дн.», «1 раб. день»), имена из корпуса («Компания 302»), ноль (за нулём списка нет — спец. 2.4,
# «ноль — не кнопка»), подписи-рисунки (aria-hidden), строки таблиц, которые сами открываются, итог
# таблицы (tfoot — сумма её строк), заголовок-вывод и строка-расшифровка над списком, который открывается.
DEAD = r"""(()=>{const root=document.querySelector('#tab-contracts'); const out=[];
  const CNT=/(^|[^\d\/.,])\d[\d\u00a0\u202f ]*\s*(заказ|сдел|пункт|заказчик|человек|файл|строк|позици|отрез|поставщик|случа|карточ)/i;
  const BARE=/^\s*[≤≥~≈]?\s*\d[\d\u00a0\u202f ]*(,\d+)?\s*(%|из\s+\d+)?\s*$/;
  const DRILL='button.rzn,button.odn,[data-od],[data-of],tr[data-odk],tr[data-ct],tr.drill,[data-ctk],[data-m]';
  const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  for(let t=w.nextNode();t;t=w.nextNode()){ const s=t.nodeValue; const el=t.parentElement;
    if(!el||!el.getClientRects().length||el.closest('script,style,select,option,.ctvh,.orkvh,[hidden],[aria-hidden="true"]')) continue;
    {const d=el.closest('details'); if(d&&!d.open&&!el.closest('summary')) continue;}
    const x=s.replace(/\b\d{1,2}\.\d{1,2}(\.\d{4})?\b/g,'').replace(/\b(19|20)\d\d\b/g,'').replace(/№\s?\d+(\/\d+)?/g,'').replace(/\b\d+\/\d+\b/g,'')
      .replace(/(Компания|Поставщик|Менеджер|КАМ|Клиент)\s+\d+/g,'');
    if(!CNT.test(x)&&!BARE.test(x)) continue;
    if(/^\s*0\s*$/.test(x)) continue;
    if(el.closest('button,a[href],[data-od],[data-of],.rzfinal,.rzcode,abbr,.rzn0,.rznd,th,thead,tfoot,caption,.rzgl-l,summary,label')) continue;
    const tr=el.closest('tr'); if(tr&&(tr.matches('[data-odk],[data-ct],[data-od],.drill,[data-adj]')||tr.querySelector('button,a[href]'))) continue;
    const hd=el.closest('h1,h2,h3,h4')||(()=>{const p=el.closest('p,div'); const pr=p&&p.previousElementSibling; return pr&&/^H[1-4]$/.test(pr.tagName)?p:null;})();
    if(hd){const box=hd.closest('section,.card,details,.wksec,.ctd-d,.ctd-a')||hd.parentElement; if(box&&box.querySelector(DRILL)) continue;}
    out.push(s.trim().slice(0,40)); }
  return out;})()"""


@pytest.mark.parametrize("sub", SUBS)
def test_p04_тупиков_нет(open_page, cfg, sub):
    pg = open_page(f"#contracts/{sub}")
    dead = pg.evaluate(DEAD)
    mark(cfg["P4"]["enforce"], len(dead) <= cfg["P4"]["dead_max"], f"{sub}: чисел-тупиков {len(dead)}: {dead[:6]}")


# ---------------------------------------------------------------- П-5 неясные подписи (rz_lint)
def test_p05_словарь_каркас_и_вкладка(open_page, cfg):
    from scripts import rz_lint
    pg = open_page("#contracts/week")
    base = pg.url.split("#")[0]
    tot_frame = tot_tab = 0
    worst = []
    for d in ("", "/sep"):
        for s in SUBS:
            pg.goto("about:blank")
            pg.goto(f"{base}#contracts/{s}{d}")
            pg.wait_for_timeout(200)
            fr = rz_lint.lint_segments(pg.evaluate(rz_lint.COLLECT, "frame"))
            tb = rz_lint.lint_segments(pg.evaluate(rz_lint.COLLECT, "all"))
            tot_frame += fr["total"]
            tot_tab += tb["total"]
            worst += [f"{s}{d}: {x['rule']} «{x['match']}»" for x in fr["forbidden"][:2]]
    mark(cfg["P5"]["enforce_frame"], tot_frame == 0, f"каркас (шапка, подвкладки): нарушений {tot_frame} {worst[:4]}")
    mark(cfg["P5"]["enforce_tab"], tot_tab <= cfg["P5"]["tab_max"], f"вся вкладка: нарушений словаря {tot_tab}")


# ---------------------------------------------------------------- П-6 шрифт и цели касания
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_p06_шрифт_и_касание(open_page, cfg, theme):
    pg = open_page("#contracts/week", 1280, 800, theme)
    fr = pg.evaluate(TYPE, FRAME)
    ok = fr["small"] == 0 and fr["mono"] == 0 and fr["caps"] == 0
    mark(cfg["P6"]["enforce_frame"], ok, f"каркас: мельче 13 px {fr['smallEx']}, моно {fr['monoEx']}, капс {fr['caps']}")
    tab = pg.evaluate(TYPE, "#tab-contracts")
    ph = open_page("#contracts/week", 390, 844, theme)
    tap = ph.evaluate("""(t)=>[...document.querySelectorAll('#rzGlossB,#ctTabs [role=tab],#rzKam,#ctDirs [role=tab]')]
      .filter(e=>e.getClientRects().length).map(e=>{const r=e.getBoundingClientRect(); return [e.id||e.textContent.trim().slice(0,12),Math.round(r.width),Math.round(r.height)]})
      .filter(x=>x[1]<t||x[2]<t)""", cfg["P6"]["tap_px"])
    mark(cfg["P6"]["enforce_frame"], not tap, f"390: цели касания меньше 44×44 — {tap}")
    mark(cfg["P6"]["enforce_tab"], tab["small"] == 0 and tab["caps"] == 0,
         f"вся вкладка: мельче 13 px {tab['small']} из {tab['n']} {tab['smallEx'][:3]}, капс {tab['caps']}, моно не у номеров {tab['mono']}")


# ---------------------------------------------------------------- П-7 цвет
TOK = ["--rz-data", "--rz-mut", "--rz-sel", "--rz-alarm", "--rz-warn", "--rz-warnbg", "--rz-link", "--rz-focus", "--bg", "--panel"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_p07_контраст_токенов(open_page, cfg, theme):
    pg = open_page("#contracts/week", 1280, 800, theme)
    t = pg.evaluate("(K)=>{const cs=getComputedStyle(document.getElementById('tab-contracts')); return Object.fromEntries(K.map(k=>[k,cs.getPropertyValue(k).trim()]))}", TOK)
    assert all(t.values()), t
    bad = []
    for fg in ("--rz-mut", "--rz-sel", "--rz-alarm", "--rz-warn", "--rz-link"):
        for bg in ("--bg", "--panel"):
            c = contrast(t[fg], t[bg])
            if c < cfg["P7"]["text"]:
                bad.append(f"{fg} на {bg} {c:.2f}")
    if contrast(t["--rz-warn"], t["--rz-warnbg"]) < cfg["P7"]["text"]:
        bad.append("--rz-warn на --rz-warnbg")
    for fg in ("--rz-data", "--rz-focus"):
        if contrast(t[fg], t["--bg"]) < cfg["P7"]["mark"]:
            bad.append(f"{fg} (метка) на --bg")
    mark(cfg["P7"]["enforce"], not bad, f"{theme}: контраст ниже порога — {bad}")


def test_p07_красная_площадь(open_page, cfg, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    pg = open_page("#contracts/week", 1280, 800)
    alarm = _hex(pg.evaluate("getComputedStyle(document.getElementById('tab-contracts')).getPropertyValue('--rz-alarm')"))
    box = pg.evaluate("(()=>{const r=document.getElementById('tab-contracts').getBoundingClientRect(); return [r.left,Math.max(0,r.top),r.width,Math.min(innerHeight,r.bottom)-Math.max(0,r.top)]})()")
    f = tmp_path / "red.png"
    pg.screenshot(path=str(f), clip={"x": box[0], "y": box[1], "width": box[2], "height": max(1, box[3])})
    im = Image.open(f).convert("RGB")
    px = list(im.get_flattened_data()) if hasattr(im, "get_flattened_data") else list(im.getdata())
    red = sum(1 for p in px if sum(abs(a - b) for a, b in zip(p, alarm)) < 60)
    share = red / max(1, len(px))
    mark(cfg["P7"]["enforce_red"], share <= cfg["P7"]["red_max"], f"красная площадь первого экрана {share:.1%}")


# ---------------------------------------------------------------- П-8 целостность
def test_p08_градиентов_во_вкладке_нет(open_page, cfg):
    pg = open_page("#contracts/week")
    g = pg.evaluate("""(()=>{const out=[]; for(const s of document.styleSheets){let R; try{R=s.cssRules}catch(e){continue}
      const walk=L=>{for(const r of L){ if(r.cssRules&&!r.selectorText) walk(r.cssRules);
        else if(r.selectorText&&/#tab-contracts|\\.drw\\.rz|\\.rz[a-z]/.test(r.selectorText)&&/gradient\\(/.test(r.cssText)) out.push(r.selectorText.slice(0,80)); }};
      walk(R);} document.querySelectorAll('#tab-contracts [style*="gradient"]').forEach(e=>out.push('style: '+e.className)); return out;})()""")
    mark(cfg["P8"]["enforce"], not g, f"градиенты в правилах вкладки: {g[:5]}")


# ---------------------------------------------------------------- П-9 адрес
STATE = """(()=>{const d=document.getElementById('drw'),on=d.classList.contains('on');
  const kind=!on?'none':document.querySelector('#drwBody #opRoot')?'order':document.querySelector('#drwBody .rzst-notfound')?'notfound'
    :document.querySelector('#drwBody [data-odf]')?'list':document.querySelector('#drwBody .rzc')?'deal':'other';
  const t=document.querySelector('#ctTabs [aria-selected=true]'), r=document.querySelector('#ctDirs [aria-selected=true]');
  return {kind,sub:t&&t.dataset.sub,dir:r?r.dataset.dir:'main',hash:location.hash}})()"""


def test_p09_адрес_открывает_уровни(open_page, cfg, browser, smoke):
    pg = open_page("#contracts/week")
    ids = pg.evaluate("""(()=>{const C=window.__CONTRACTS__;const r=C.rows.find(r=>!r.seg&&(r.orders||[]).some(o=>o.live));
      const o=r.orders.find(o=>o.live);const s=C.rows.find(r=>r.seg)||{};const so=(s.orders||[])[0]||{};
      return {d:String(r.deal),o:String(o.id),sd:String(s.deal||''),so:String(so.id||'')};})()""")
    cases = [("#contracts/week?n=w%3AW3", "list", "week", "main"),
             ("#contracts/week?n=w%3AW3&f=tag.late", "list", "week", "main"),
             (f"#contracts/week?n=w%3AW3&o={ids['o']}", "order", "week", "main"),
             (f"#contracts/week?n=w%3AW3&o={ids['o']}&s=5", "order", "week", "main"),
             (f"#contracts/main?d={ids['d']}", "deal", "main", "main"),
             (f"#contracts/main/sep?d={ids['sd']}", "deal", "main", "sep"),
             (f"#contracts/main/sep?d={ids['sd']}&o={ids['so']}", "order", "main", "sep"),
             # приёмка — одна на сделку (30.10.2026): в текущем месяце корпуса первой приёмки нет — список прошлого
             ("#contracts/month?n=m%3A1.S1", "list", "month", "main"),
             ("#contracts/week?o=99999999", "notfound", "week", "main"),
             ("#contracts/sep", "none", "main", "sep")]
    bad = []
    for h, kind, sub, d in cases:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        q = ctx.new_page()
        q.goto(smoke + h)
        q.wait_for_timeout(400)
        st = q.evaluate(STATE)
        if (st["kind"], st["sub"], st["dir"]) != (kind, sub, d):
            bad.append(f"{h} → {st}")
        if re.search(r"[А-Яа-яЁё]", st["hash"]):
            bad.append(f"{h}: кириллица в адресе {st['hash']}")
        ctx.close()
    mark(cfg["P9"]["enforce"], not bad, f"адреса не открыли свой уровень: {bad}")


def test_p09_назад_шагает_по_уровням(open_page, cfg):
    pg = open_page("#contracts/week")
    pg.click("#ctLive button.rzn")
    pg.wait_for_timeout(200)
    h1 = pg.evaluate("location.hash")
    row = pg.query_selector("#drwBody tr[data-odk]")
    assert row, "в списке нет строк заказов"
    row.click()
    pg.wait_for_timeout(200)
    s2 = pg.evaluate(STATE)
    pg.go_back()
    pg.wait_for_timeout(300)
    s3 = pg.evaluate(STATE)
    pg.go_back()
    pg.wait_for_timeout(300)
    s4 = pg.evaluate(STATE)
    ok = ("n=w%3Alive" in h1 and s2["kind"] == "order" and "&o=" in s2["hash"] and s3["kind"] == "list"
          and "&o=" not in s3["hash"] and s4["kind"] == "none" and s4["hash"] == "#contracts/week")
    mark(cfg["P9"]["enforce"], ok, f"«Назад»: {h1} → {s2} → {s3} → {s4}")
    # × закрывает всё: уровни уходят из адреса
    pg.click("#ctLive button.rzn")
    pg.wait_for_timeout(200)
    pg.click("#drwX")
    pg.wait_for_timeout(300)
    mark(cfg["P9"]["enforce"], pg.evaluate("location.hash") == "#contracts/week", "× не снял уровни из адреса")
    assert not pg.errors, pg.errors


# ---------------------------------------------------------------- П-10 высота «Недели»
def test_p10_высота_недели(open_page, cfg):
    hs = {}
    for w, h in (cfg["viewports"]["desktop"], cfg["viewports"]["phone"]):
        pg = open_page("#contracts/week", w, h)
        pg.evaluate("document.querySelectorAll('#tab-contracts details[open]').forEach(d=>d.open=false)")
        hs[w] = pg.evaluate("Math.round(document.getElementById('tab-contracts').getBoundingClientRect().height)")
    ok = hs[1280] <= cfg["P10"]["max_1280"] and hs[390] <= cfg["P10"]["max_390"]
    mark(cfg["P10"]["enforce"], ok, f"высота «Недели»: 1280 — {hs[1280]} px (≤ {cfg['P10']['max_1280']}), 390 — {hs[390]} px (≤ {cfg['P10']['max_390']})")
