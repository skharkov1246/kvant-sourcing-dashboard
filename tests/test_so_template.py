"""«Сорсинг» по принципу «Реализации» (30.10.2026: «переработай дашборд сорсинга так же… по науке
формирования дашбордов»). Smoke-страница в headless Chromium (--dump-dom, как проверка гейта):
первый экран — вывод, три плитки и «Ещё показатели», затем «Сегодня» (не больше 6 дел, у числа одна
единица — запросы или сделки); отметка в карточках — отдельным блоком вне дневного счёта; каждое
число открывает ровно свои записи, у записи ссылка в Битрикс; у списка свой адрес #sourcing/<пункт>,
Esc поднимает на уровень вверх и возвращает фокус на число. Числа списков сверены со сводкой сборки
вторым способом (kpRules, buckets, goneOpen). Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import html
import json
import re
import sys
from urllib.parse import quote
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

PROBE = r"""<script>(function(){
var out={clicks:[]};
function txt(e){return e?(e.textContent||'').replace(/\s+/g,' ').trim():'';}
function rows(){return [].slice.call(document.querySelectorAll('#drwBody table.solst tbody tr')).filter(function(t){return !t.querySelector('td[colspan]');});}
var drw=document.getElementById('drw');
try{
  var T=document.getElementById('tab-sourcing');
  out.titles=[].slice.call(T.querySelectorAll('h2.secttl')).map(function(h){return txt(h).replace(/\s*\?$/,'');});
  out.subs=[].slice.call(T.querySelectorAll('#soTabs [role=tab]')).filter(function(b){return !b.hidden;}).map(function(b){return b.dataset.sub+':'+txt(b);});
  out.firstPanel=[].slice.call(T.querySelectorAll('.sopanel')).filter(function(p){return !p.hidden;}).map(function(p){return p.dataset.sub;});
  out.todayFirst=txt(T.querySelector('#soPanel-today h2.secttl'));
  out.verdict=txt(document.getElementById('soVerdict'));
  out.tiles=[].slice.call(document.querySelectorAll('#kpis .sotile')).map(function(t){return txt(t.querySelector('.t'));});
  out.more=[].slice.call(document.querySelectorAll('#kpisMore .sotile')).map(function(t){return txt(t.querySelector('.t'));});
  out.today=[].slice.call(document.querySelectorAll('#soToday tr[data-sq]')).map(function(tr){
    return {key:tr.dataset.sq, act:txt(tr.querySelector('.soact')), n:parseInt(txt(tr.querySelector('[data-so]')),10), unit:txt(tr.querySelector('.sounit')), more:tr.hidden};});
  out.order=[].slice.call(document.querySelectorAll('#soOrder tr[data-sq]')).map(function(tr){return {key:tr.dataset.sq, n:parseInt(txt(tr.querySelector('[data-so]')),10), unit:txt(tr.querySelector('.sounit'))};});
  out.orderOpen=document.getElementById('soOrderS').open;
  out.blockTag=/БЛОК\s*A|БЛОК\s*B/.test(txt(T));
  // каждое число-кнопка вкладки: открывает ровно столько строк, адрес — #sourcing/<пункт>[/<сорсер>]
  [].slice.call(T.querySelectorAll('[data-so]')).forEach(function(b){
    var n=parseInt(txt(b),10)||parseInt(txt(b.querySelector('.v')),10);
    try{ _soSlot.w=[]; }catch(e){}   // ограничитель записей в историю (40 за 10 с) — для сотни чисел подряд
    b.click();
    var rs=rows();
    out.clicks.push({key:b.dataset.so, uid:b.dataset.souid||'', n:n, rows:rs.length, open:drw.classList.contains('on'), hash:location.hash,
      links:rs.map(function(r){var a=r.querySelector('a.sobx');return a?[a.getAttribute('href'),a.getAttribute('target'),a.getAttribute('rel')]:null;}),
      days:rs.map(function(r){var v=txt(r.querySelector('td:last-child'));return v==='—'?null:parseInt(v,10);}),
      why:txt(document.querySelector('#drwBody .sowhyd'))});
    document.getElementById('drwX').click();
  });
  out.closedHash=location.hash;
  // Esc: список → запросы сорсера → Esc → список → Esc → закрыто, фокус на числе
  var b0=document.querySelector('#soToday button[data-so$="pawStale"]');   // запросы (pawStale или do:pawStale): в списке есть имя сорсера
  if(b0){
    try{ _soSlot.w=[]; }catch(e){}
    b0.focus(); b0.click(); out.lvl1=location.hash;
    var l=document.querySelector('#drwBody .solnk');
    if(l){ l.click(); out.lvl2=location.hash; out.lvl2name=txt(document.getElementById('drwName')); }
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  }
  // «Назад» по истории асинхронен: ждём смены адреса (до 3 с), а не фиксированную паузу —
  // на гейте 60 мс не хватало (прогон 41075308130)
  function until(cond,cb,left){ if(cond()||left<=0){cb();return;} setTimeout(function(){until(cond,cb,left-25);},25); }
  var h2=location.hash;
  until(function(){return location.hash!==h2;},function(){
    try{
      out.back1=location.hash; out.back1open=drw.classList.contains('on'); out.back1name=txt(document.getElementById('drwName'));
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    }catch(e){out.err=String(e&&e.stack||e);}
    var h1=location.hash;
    until(function(){return location.hash!==h1||!drw.classList.contains('on');},function(){
      try{
        out.back2=location.hash; out.back2open=drw.classList.contains('on');
        out.focusBack=!!b0&&document.activeElement===b0;
        // адрес, вставленный в строку браузера, открывает тот же список
        if(b0){ history.replaceState(null,'','#sourcing/people?n='+encodeURIComponent(b0.dataset.so)); window.dispatchEvent(new PopStateEvent('popstate'));
          out.directSub=document.getElementById('tab-sourcing').dataset.sosub;
          out.direct={open:drw.classList.contains('on'), rows:rows().length, n:parseInt(txt(b0),10)};
          document.getElementById('drwX').click(); out.direct.after=location.hash; }
        // сверка вторым способом: сводка сборки против числа строк списка
        var A=window.__DATA__.sourcersA||[], K=window.__DATA__.kpi||{};
        out.sum={violN:A.reduce(function(s,r){return s+(r.violN||0);},0), violPre:A.reduce(function(s,r){return s+(r.violPre||0);},0),
          paw:A.reduce(function(s,r){return s+((r.buckets||{}).paw||0);},0), goneOpen:K.goneOpen||0,
          unc:((window.__DATA__.coverage||{}).uncovered||[]).length, withoutReq:(window.__DATA__.coverage||{}).withoutReq||0,
          econBad:((((window.__DATA__.kpRules||{}).econ)||{}).rows||[]).length};
      }catch(e){out.err=String(e&&e.stack||e);}
      var p=document.createElement('pre'); p.id='so1out'; p.textContent=JSON.stringify(out); document.body.appendChild(p);
    },3000);
  },3000);
}catch(e){out.err=String(e&&e.stack||e); var p=document.createElement('pre'); p.id='so1out'; p.textContent=JSON.stringify(out); document.body.appendChild(p);}
})();</script>"""

UNCOVERED = [  # придуманные сделки без запроса (правило 18)
    {"id": str(9100 + i), "t": f"Сделка-проба {i}", "client": f"Компания {i}", "owner": f"Менеджер {i % 3}",
     "raw": 1000 * (i + 1), "amt": f"€{i + 1}K", "cat": "Сервис", "date": f"2026-07-{10 + i:02d}",
     "sem": "в работе", "wk": "2026-07-06"} for i in range(5)]


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    import validate_dashboard as vd
    chrome, _v = vd.find_chrome()
    if not chrome:
        pytest.skip("нет рабочего Chromium/Chrome — проверка страницы пропущена")
    import dashboard
    from tests import fixture
    m = fixture.build_metrics()
    m["presale"] = fixture.build_presale()
    m["coverage"]["uncovered"] = UNCOVERED
    m["coverage"]["withoutReq"] = len(UNCOVERED)
    d = tmp_path_factory.mktemp("so_tab")
    p = dashboard.write(m, {"source": "rules", "items": []}, d / "index.html", people=fixture.build_people())
    p.write_text(p.read_text(encoding="utf-8").replace("</body>", PROBE + "</body>", 1), encoding="utf-8")
    dom, log = vd._chrome_run(chrome, p, [], 120)
    mm = re.search(r'<pre id="so1out">(.*?)</pre>', dom, re.S)
    assert mm, "страница не отдала результат пробы: " + log[-800:]
    out = json.loads(html.unescape(mm.group(1)))
    assert "err" not in out, out.get("err")
    return out


def test_первый_экран_вывод_три_плитки_сегодня(page):
    assert page["verdict"].startswith("Главное сегодня"), page["verdict"]
    # управление по регламенту первым: сделки дольше нормы стадии; КП без итога — всегда на первом экране
    assert len(page["tiles"]) == 3 and page["tiles"][0] == "Сделки дольше нормы" and "Ждут итога КП" in page["tiles"], page["tiles"]
    assert set(page["tiles"] + page["more"]) >= {"Запросов за период", "Нарушения правил КП", "Сделки без запроса"}
    # подвкладки: управление первым, пульс вторым; «Сделки пресейла» — только при воронке в сборке
    assert [x.split(":")[0] for x in page["subs"]] == ["today", "pulse", "people", "rfq", "presale", "how"], page["subs"]
    assert page["firstPanel"] == ["today"] and page["todayFirst"] == "Сегодня"
    for need in ("Пульс отдела по неделям", "Сорсеры: темп и что исправить", "Куда дошли запросы", "Регламент воронки «Пресейл»"):
        assert need in page["titles"], (need, page["titles"])
    assert not page["blockTag"], "«БЛОК A/B» — жаргон: на вкладке словами «отдел / вне отдела»"


def test_сегодня_не_больше_шести_дел_одна_единица(page):
    today = [r for r in page["today"] if not r.get("more")]
    assert 0 < len(today) <= 6, today
    for r in page["today"]:
        unit = "сдел" if r["key"] in ("ps-src", "ps-rfq", "ps-stale", "unc", "over") else "запрос"
        assert r["unit"].startswith(unit), r
    assert [r["key"] for r in today][:2] == ["ps-src", "gone"], "сначала то, что держит всё остальное"
    # отметка в карточках — вне «Сегодня», свёрнутым блоком
    assert not page["orderOpen"]
    assert {r["key"] for r in page["order"]} & {"noFile", "fileClosed", "econ"}
    assert not {r["key"] for r in page["order"]} & {r["key"] for r in today}


def test_число_открывает_ровно_свои_записи(page):
    cl = page["clicks"]
    assert len(cl) >= 15, len(cl)
    for c in cl:
        assert c["open"] and c["rows"] == c["n"] > 0, c
        want = "?n=" + quote(c["key"], safe="") + ("&u=" + quote(c["uid"], safe="") if c["uid"] else "")
        assert re.fullmatch(r"#sourcing/[a-z]+\?.*", c["hash"]) and c["hash"].endswith(want), c
        # список-факт за числом таблицы («Куда дошли», «Письмо ушло») — без «Что сделать»
        fact = c["key"].startswith(("stg:", "mail:", "st:"))
        assert c["why"].startswith("Почему в списке.") and (fact or "Что сделать." in c["why"]), c["why"]
        for ln in c["links"]:
            assert ln and ln[1] == "_blank" and ln[2] == "noopener", c
            assert re.fullmatch(r"https://kvantpro\.bitrix24\.ru/crm/(type/166|deal)/details/\d+/", ln[0]), ln
        d = [x for x in c["days"] if x is not None]
        assert d == sorted(d, reverse=True), ("дольше ждущие сверху", c["key"], d)
    assert re.fullmatch(r"#sourcing/[a-z]+", page["closedHash"]), page["closedHash"]


def test_числа_сходятся_со_сводкой_сборки(page):
    """Второй способ: число строк списка — против сводки, посчитанной сборкой (metrics.py)."""
    first = {}
    for c in page["clicks"]:
        if not c["uid"]:
            first.setdefault(c["key"], c["rows"])
    s = page["sum"]
    assert first["viol"] == s["violN"] > 0
    assert first["pre"] == s["violPre"] > 0
    assert first["paw"] == s["paw"] > 0
    assert first["gone"] == s["goneOpen"] > 0
    assert first["unc"] == s["unc"] == s["withoutReq"]
    assert first["econ"] == s["econBad"]


def test_адрес_уровня_esc_вверх_фокус_назад(page):
    assert "?n=" in page["lvl1"] and "?p=" in page["lvl2"], (page["lvl1"], page["lvl2"])
    assert page["back1"] == page["lvl1"] and page["back1open"], "Esc из запросов сорсера — назад к списку"
    assert page["back2"] == "#sourcing/today" and not page["back2open"] and page["focusBack"]
    d = page["direct"]
    assert d["open"] and d["rows"] == d["n"] and d["after"] == "#sourcing/people" and page["directSub"] == "people"
