"""Приёмка «Реализации»: сделка и заказ поставщика — ремонт по ревью сведения (30.10.2026), браузер.

Что ловим на собранной странице (корпус придуман, правило 18):
  * за «Клиент не оплатил в срок» (сделки) и W1 — действие и хозяин пункта сделки, а не «Дел по
    сделке в очереди нет»;
  * плитка W2 — шаги 4, 5, 8 в каждом виде «Смотреть как»: части складываются в W2;
  * «Смотреть как» видит пункты закрытой части своего человека (whoId);
  * две кнопки Excel не лежат одна на другой;
  * П-9: список → × → сделка со страницы → Esc ⇒ панель закрыта, адрес без списка;
  * «не назначен» в строках очереди — подмножество списка «Заказов без менеджера ОРК»;
  * карточка сделки с разговором с клиентом не говорит «По сделке целиком дел нет».
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")


def _drawer(pg):
    return pg.evaluate("(()=>{const d=document.getElementById('drw'); return d.classList.contains('on')?d.innerText:''})()")


@pytest.mark.parametrize("n,act", [("w:unpaidDeals", "Урегулировать долг"), ("w:W1", "Принять сделку")])
def test_список_сделок_даёт_действие_пункта_сделки(open_page, n, act):
    pg = open_page(f"#contracts/week?n={n.replace(':', '%3A')}")
    pg.wait_for_timeout(400)
    t = _drawer(pg)
    assert act in t, t[:400]
    assert "Дел по сделке в очереди нет" not in t
    assert "Не определить" not in t
    assert not pg.errors, pg.errors


def test_w2_части_равны_числу_в_каждом_виде(open_page):
    pg = open_page("#contracts/week")
    js = """()=>{const t=document.getElementById('wkTW2'); const v=t.querySelector('.wktv');
      const ps=[...t.querySelectorAll('.wkw2l > span')].filter(s=>!s.getAttribute('aria-hidden'));
      return {v:v?+v.innerText:0, parts:ps.map(s=>+((s.innerText.match(/(\\d+)\\s*$/)||[])[1]||0)),
              lbl:ps.map(s=>s.innerText), bars:[...t.querySelectorAll('.wkmini [data-od]')].map(b=>b.dataset.od)}}"""
    opts = pg.evaluate("[...document.querySelectorAll('#rzKam option')].map(o=>o.value)")
    assert len(opts) > 3
    for v in opts:
        pg.select_option("#rzKam", v)
        pg.wait_for_timeout(250)
        r = pg.evaluate(js)
        assert sum(r["parts"]) == r["v"], (v, r)
        assert not any("приёмка" in x for x in r["lbl"]), (v, r)
        assert not any("/step/1/" in x for x in r["bars"]), (v, r)
    assert not pg.errors, pg.errors


def test_смотреть_как_видит_пункты_закрытой_части(open_page):
    pg = open_page("#contracts/week")
    opts = pg.evaluate("[...document.querySelectorAll('#rzKam option')].map(o=>[o.value,o.textContent])")
    seen = 0
    for v, name in opts:
        if not v:
            continue
        nm = name.split(" (")[0]
        bad = pg.evaluate("""([k,nm])=>((window.__DEALCTX__||{}).queue||[]).filter(q=>String(q.who||'').split(' (')[0]===nm
            &&String(q.whoId||'')!==k).map(q=>q.code+' '+q.deal)""", [v, nm])
        assert not bad, (name, bad)
        seen += pg.evaluate("k=>((window.__DEALCTX__||{}).queue||[]).filter(q=>String(q.whoId||'')===k).length", v)
    assert seen >= 3
    # Роза (руководитель ОРК): её пункт закрытой части в «Порядке в карточках» — виден в её виде
    pg.select_option("#rzKam", "30")
    pg.wait_for_timeout(300)
    assert "дел нет" not in pg.evaluate("document.querySelector('#wkHygQ summary').innerText")


def test_кнопки_excel_не_лежат_одна_на_другой(open_page):
    for w, h in ((1280, 800), (390, 844)):
        pg = open_page("#contracts/week", w, h)
        pg.evaluate("document.querySelectorAll('#tab-contracts details').forEach(d=>d.open=true)")
        pg.wait_for_timeout(300)
        r = pg.evaluate("""()=>{const B=[...document.querySelectorAll('#tab-contracts .xlbtn')].filter(e=>e.getClientRects().length);
          const k=B.map(e=>{const r=e.getBoundingClientRect(); return [Math.round(r.x),Math.round(r.y+scrollY),Math.round(r.width),Math.round(r.height)].join(',')});
          const L=B.filter(e=>e.closest('#wkLedD')).map(e=>e.getAttribute('aria-label'));
          return {n:B.length, uniq:new Set(k).size, L}}""")
        assert r["n"] == r["uniq"], (w, r)
        assert len(r["L"]) == 2 and len(set(r["L"])) == 2, (w, r)


def test_p09_закрытый_список_не_возвращается(open_page):
    pg = open_page("#contracts/week")
    pg.locator("#tab-contracts button.rzn[data-of='w:W2']").first.click()
    pg.wait_for_timeout(300)
    assert _drawer(pg)
    pg.locator("#drwX").click()
    pg.wait_for_timeout(300)
    b = pg.locator("#orkToday button.wkdeal").first
    b.click()
    pg.wait_for_timeout(300)
    assert "?d=" in pg.evaluate("location.hash")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    s = pg.evaluate("({on:document.getElementById('drw').classList.contains('on'),hash:location.hash,"
                    "cls:document.activeElement&&document.activeElement.className})")
    assert not s["on"] and s["hash"] == "#contracts/week" and "wkdeal" in str(s["cls"]), s


def test_не_назначен_в_очереди_подмножество_счётчика(open_page):
    pg = open_page("#contracts/week")
    r = pg.evaluate("""()=>{const C=window.__CONTRACTS__, Q=(C.life||{}).queue||[], W=C.ork.week, S=W.strs||[];
      const mg=o=>typeof o.mgr==='number'?S[o.mgr]:o.mgr;
      const none=new Set(W.orders.filter(o=>mg(o)==='не назначен').map(o=>String(o.k)));
      const un=[...new Set(Q.filter(q=>q.order&&q.role==='orkMgr'&&!q.whoOut&&(!q.who||q.who==='не назначен')).map(q=>String(q.order)))];
      return {bad:un.filter(k=>!none.has(k)), n:none.size}}""")
    assert not r["bad"], r
    # строки группы: «не назначен: N» не больше счётчика «Заказов без менеджера ОРК»
    import re
    subs = pg.evaluate("[...document.querySelectorAll('#orkToday li.wkg .wkgs')].map(e=>e.innerText)")
    for s in subs:
        m = re.search(r"не назначен: (\d+)", s)
        if m:
            assert int(m.group(1)) <= r["n"], (s, r["n"])


def test_карточка_сделки_говорит_о_разговоре_с_клиентом(open_page):
    pg = open_page("#contracts/week")
    did = pg.evaluate("""()=>{const Q=(window.__CONTRACTS__.life||{}).queue||[]; const q=Q.find(q=>q.order&&q.actClient&&q.code==='late'); return q?String(q.deal):''}""")
    assert did
    pg.evaluate(f"location.hash='#contracts/week?d={did}'")
    pg.wait_for_timeout(500)
    t = _drawer(pg)
    assert "По сделке целиком дел нет" not in t
    assert "согласовать с клиентом новый срок" in t, t[:600]
