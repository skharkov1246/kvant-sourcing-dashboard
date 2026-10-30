"""«Сделки и деньги» и карточка сделки вкладки «Реализация» (R3, 29.10.2026: «как мне понимать,
что дальше… нельзя провалиться в процессы, где зависло»). Smoke-страница в headless Chromium
(--dump-dom, как проверка гейта): таблица сделок — худшие сверху, у строки — что сделать и кто и
ссылка в Битрикс; строка открывает карточку с клавиатуры, Esc закрывает, фокус возвращается на
строку; карточка — шапка → «Что делать» → «Заказы сделки» (у каждого заказа ссылка в Битрикс,
«Путь заказа →» — только когда на странице есть _opOpen) → чат и договор → экономика → путь по
стадиям. Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import html
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

PROBE = r"""<script>(function(){
var out={};
function txt(e){return e?(e.textContent||'').replace(/\s+/g,' ').trim():'';}
try{
  var CT=[].slice.call(document.querySelectorAll('.tabs .tab')).filter(function(x){return x.dataset.tab==='contracts'})[0];
  CT.click();
  document.querySelector('#tab-contracts [role="tab"][data-sub="main"]').click();
  var X=window.__CONTRACTS__, by={};
  X.rows.forEach(function(r){by[String(r.deal)]=r;});
  var Q=((X.life||{}).queue||[]), D=window.__DEALCTX__&&Array.isArray(window.__DEALCTX__.queue)?window.__DEALCTX__.queue:[];
  var trs=[].slice.call(document.querySelectorAll('#ctPanel-main #ctBody tr[data-ct]'));
  out.head=[].slice.call(document.querySelectorAll('#ctPanel-main #ctHead th')).map(txt);
  out.rows=trs.map(function(tr){var r=by[tr.dataset.ct]||{}, os=(r.orders||[]).filter(function(o){return o.live&&!r.closed;});
    var a=tr.querySelector('td.c-no a');
    return {deal:tr.dataset.ct, late:!!r.late&&!r.closed, lateDays:r.lateDays||0, closed:!!r.closed,
      over:!r.closed&&(os.some(function(o){return o.stepOver;})||!!(r.stepCell&&r.stepCell.over)||!!r.stuck),
      href:a?a.getAttribute('href'):'', target:a?a.getAttribute('target'):'', rel:a?a.getAttribute('rel'):'',
      tab:tr.getAttribute('tabindex'), act:txt(tr.querySelector('td.c-do')), ord:txt(tr.querySelector('td.c-ord')),
      acts:Q.concat(D).filter(function(q){return String(q.deal)===tr.dataset.ct;}).map(function(q){return q.act;}),
      norders:r.norders||0, overN:os.filter(function(o){return o.stepOver;}).length};});
  out.chips=[].slice.call(document.querySelectorAll('#ctPanel-main #ctChips button.stchip')).map(function(b){return {m:b.dataset.m,p:b.getAttribute('aria-pressed')};});
  out.xl=_xlSnap(document.querySelector('#ctPanel-main #ctBody').closest('table')).head;
  // карточка с клавиатуры: Enter на строке
  var tr0=trs[0]; tr0.focus();
  tr0.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  var drw=document.getElementById('drw'), body=document.getElementById('drwBody');
  out.open=drw.classList.contains('on'); out.focusX=document.activeElement===document.getElementById('drwX');
  out.card={deal:tr0.dataset.ct, secs:[].slice.call(body.querySelectorAll('.rzc-h')).map(function(h){return txt(h).replace(/\s*\d+$/,'');}),
    dealLink:(body.querySelector('a.rzc-bx')||{}).href||'', ords:body.querySelectorAll('.rzc-ord').length,
    ordLinks:[].slice.call(body.querySelectorAll('.rzc-ord a.rzc-lnk')).map(function(a){return a.getAttribute('href');}),
    paths:body.querySelectorAll('.rzc-ord [data-ctop]').length, strip:body.querySelectorAll('.rzc-strip [data-ctop]').length, todo:txt(body.querySelector('.rzc-todo')), text:txt(body)};
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  out.closed=!drw.classList.contains('on');
  setTimeout(function(){
    try{
      out.focusBack=document.activeElement===tr0;
      // путь заказа: _opOpen есть — у каждого заказа кнопка, вызов с back:'deal' и сделкой
      var calls=[]; window._opOpen=function(k,o){calls.push([k,o&&o.back,o&&o.deal&&String(o.deal.deal)]);};
      var tr1=trs.filter(function(t){return (by[t.dataset.ct].orders||[]).length>1;})[0]||tr0;
      tr1.querySelector('td.c-deal').click();
      var bs=[].slice.call(body.querySelectorAll('.rzc-ord [data-ctop]'));
      out.path={deal:tr1.dataset.ct, n:bs.length, ords:(by[tr1.dataset.ct].orders||[]).map(function(o){return String(o.id);}),
        ids:bs.map(function(b){return b.dataset.ctop;})};
      if(bs[0]) bs[0].click();
      out.path.calls=calls;
      // ссылка «Битрикс» в строке не открывает карточку
      document.getElementById('drwX').click();
      var a=trs[0].querySelector('td.c-no a'); a.addEventListener('click',function(e){e.preventDefault();});
      a.click(); out.linkNoCard=!drw.classList.contains('on');
    }catch(e){out.err=String(e&&e.stack||e);}
    var p=document.createElement('pre'); p.id='c1out'; p.textContent=JSON.stringify(out); document.body.appendChild(p);
  },0);
}catch(e){out.err=String(e&&e.stack||e); var p=document.createElement('pre'); p.id='c1out'; p.textContent=JSON.stringify(out); document.body.appendChild(p);}
})();</script>"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    import validate_dashboard as vd
    chrome, _v = vd.find_chrome()
    if not chrome:
        pytest.skip("нет рабочего Chromium/Chrome — проверка страницы пропущена")
    import dashboard
    from tests import fixture
    r = fixture.build_realization()
    d = tmp_path_factory.mktemp("ct_deals")
    p = dashboard.write(fixture.build_metrics(), {"source": "rules", "items": []}, d / "index.html", **r)
    p.write_text(p.read_text(encoding="utf-8").replace("</body>", PROBE + "</body>", 1), encoding="utf-8")
    dom, log = vd._chrome_run(chrome, p, [], 120)
    m = re.search(r'<pre id="c1out">(.*?)</pre>', dom, re.S)
    assert m, "страница не отдала результат пробы: " + log[-800:]
    out = json.loads(html.unescape(m.group(1)))
    assert "err" not in out, out.get("err")
    return out


def test_худшие_сверху(page):
    """Порядок по умолчанию: сорванный срок клиента (глубже — выше), затем сверх нормы или простой,
    закрытые — внизу. Строк столько же, сколько сделок основного вида."""
    rows = page["rows"]
    assert len(rows) > 5
    tier = [3 if r["late"] else 2 if r["over"] else 0 if r["closed"] else 1 for r in rows]
    assert all(a >= b for a, b in zip(tier, tier[1:])), tier
    late = [r["lateDays"] for r in rows if r["late"]]
    assert late and late == sorted(late, reverse=True), late
    first_not_late = next(i for i, r in enumerate(rows) if not r["late"])
    assert all(not r["late"] for r in rows[first_not_late:]), "просрочка — только в начале"
    if any(r["closed"] for r in rows):
        i = next(i for i, r in enumerate(rows) if r["closed"])
        assert all(r["closed"] for r in rows[i:]), "закрытые — в конце"


def test_строка_что_сделать_и_битрикс(page):
    """У каждой строки — ссылка на сделку в Битриксе (новая вкладка) и фокус с клавиатуры; у сделки
    с пунктами очереди — действие одного из них; число заказов — «N заказ(а/ов)», сверх нормы — счётом."""
    for r in page["rows"]:
        assert r["href"] == f"https://kvantpro.bitrix24.ru/crm/deal/details/{r['deal']}/", r
        assert r["target"] == "_blank" and r["rel"] == "noopener" and r["tab"] == "0"
        if r["acts"]:
            assert any(a[:20].lower() in r["act"].lower() for a in r["acts"]), (r["deal"], r["act"])
        assert re.search(r"\d+ заказ(а|ов)?", r["ord"]), r["ord"]
        if r["overN"]:
            assert f"{r['overN']} дольше нормы" in r["ord"], r["ord"]
    assert page["head"][0].startswith("№") and any(h.startswith("Что сделать") for h in page["head"])
    assert page["xl"][0] == "№" and "Продажа, €" in page["xl"], page["xl"]
    assert {c["m"] for c in page["chips"]} >= {"all", "live", "late", "slow"}
    assert [c["p"] for c in page["chips"] if c["m"] == "all"] == ["true"]


def test_карточка_сделки_по_порядку(page):
    """Enter на строке — карточка, фокус на «Закрыть»; Esc закрывает, фокус — снова на строке.
    Разделы — сверху вниз: что делать, заказы, (чат и договор), экономика, путь по стадиям."""
    assert page["open"] and page["focusX"] and page["closed"] and page["focusBack"]
    c = page["card"]
    secs = c["secs"]
    assert secs[:2] == ["Что делать", "Заказы сделки"], secs
    order = [s for s in ("Что делать", "Заказы сделки", "Чат и договор", "Экономика сделки", "Путь по стадиям воронки")
             if any(x.startswith(s) for x in secs)]
    pos = [next(i for i, x in enumerate(secs) if x.startswith(s)) for s in order]
    assert pos == sorted(pos), secs
    assert c["dealLink"] == f"https://kvantpro.bitrix24.ru/crm/deal/details/{c['deal']}/"
    assert c["ords"] == len(c["ordLinks"]) > 0
    assert all(re.fullmatch(r"https://kvantpro\.bitrix24\.ru/crm/type/172/details/\d+/", h) for h in c["ordLinks"])
    assert c["paths"] == c["ords"], "_opOpen есть — у каждого заказа «Путь заказа →»"
    # ревью 30.10.2026: живые заказы — строкой под шапкой (первый экран карточки), кнопка — путь заказа
    assert 0 < c["strip"] <= c["ords"]
    assert "кто:" in c["todo"] or "Пунктов в очереди нет" in c["text"]
    assert "Сделка в Битриксе ↗" in c["text"] and "Экономика сделки" in c["text"]


def test_путь_заказа_из_карточки(page):
    """Есть _opOpen — у каждого заказа «Путь заказа →»; клик зовёт _opOpen(id заказа,
    {back: 'deal', deal}) с этой сделкой. Ссылка «Битрикс» в строке карточку не открывает."""
    p = page["path"]
    assert p["n"] == len(p["ords"]) > 1 and p["ids"] == p["ords"]
    assert p["calls"] == [[p["ords"][0], "deal", p["deal"]]]
    assert page["linkNoCard"]
