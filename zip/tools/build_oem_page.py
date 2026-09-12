#!/usr/bin/env python3
"""Страница «Атлас производителей» → zip/public/oem.html.

Выкладывает zip/data/oem_atlas.json: 101 изготовитель по семи парам
«направление × регион», их действующие и снятые линейки, лицензии и СП,
и главное — правило чтения номера детали у 93 из них.

ГЛАВНОЕ В ВЁРСТКЕ: неполнота атласа заявлена на первом экране числом
«7 пар из 18» и списком недостающих. Неполный справочник, выдающий себя
за полный, хуже отсутствующего: по нему делают вывод «у нас этого нет».

Запуск: python zip/tools/build_oem_page.py — вызывается из zip/build.py
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "public"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_telsmith_page import CSS  # общая вёрстка страниц-разведок  # noqa: E402

e = lambda s: html.escape(str(s if s is not None else ""))

EXTRA_CSS = """
.mk{border:1px solid var(--border);background:var(--surface);border-radius:10px;
 padding:11px 13px;margin:8px 0}
.mk>h4{margin:0 0 4px;font-size:14px}
.mk dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;margin:6px 0 0;font-size:12.5px}
.mk dt{color:var(--mut);white-space:nowrap}
.mk dd{margin:0}
.hd{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.hd .tag{font-size:10.5px}
@media(max-width:700px){.mk dl{grid-template-columns:1fr}.mk dt{margin-top:5px}}
"""


def build():
    d = json.loads((D / "oem_atlas.json").read_text(encoding="utf-8"))
    st, cm = d["stats"], d["completeness"]

    mk = [{"n": m["name"], "c": m["country"], "cr": m["country_raw"], "o": m["owner"],
           "f": m["former_names"], "l": m["lines"], "sp": m["specs"],
           "a": m["active_lines"], "dc": m["discontinued"], "r": m["rank"],
           "p": m["pn_system"], "w": m["site"], "s": m["source"], "cf": m["confidence"],
           "g": m["segment_title"], "rg": m["region_title"]}
          for m in d["makers"]]

    kpi = f"""<div class="kpi">
<div><b>{st['makers']}</b><span>изготовителей</span></div>
<div><b>{st['with_pn_rule']}</b><span>с правилом чтения номера</span></div>
<div><b>{st['countries']}</b><span>стран</span></div>
<div><b>{st['licensed']}</b><span>лицензий и СП</span></div>
<div><b>{cm['pairs_done']} / {cm['pairs_planned']}</b><span>пар разведки сделано</span></div>
<div><b>{st['dead_ends']}</b><span>закрытых тупиков</span></div>
</div>"""

    gap = f"""<div class="card" style="border-left:4px solid var(--warn,#fab219)">
<b>Атлас неполон — {cm['pairs_done']} пар из {cm['pairs_planned']}</b>
<div class="txt">{e(cm['note'])}</div>
<div class="txt">Не сделано: {e(', '.join(cm['pairs_missing']))}.
Пока этих пар нет, отсутствие изготовителя на странице не означает, что его нет
на рынке, — означает, что до его направления разведка не дошла.</div></div>"""

    tab_mk = """<h2>Изготовители</h2>
<div class="bar"><input id="q" placeholder="поиск по названию, линейке, владельцу, стране">
<select id="fg"></select><select id="fc"></select><select id="fr"></select>
<span class="mut" id="cn"></span></div>
<div id="out"></div>"""

    tab_pn = """<h2>Как читается номер</h2>
<div class="mut">Правило чтения обозначения машины и каталожного номера детали.
Там, где изготовитель систему не публикует, так и написано — это ответ, а не пропуск.</div>
<div class="bar"><input id="qp" placeholder="поиск по правилу или изготовителю">
<select id="pg"></select><span class="mut" id="cp"></span></div>
<div id="outp"></div>"""

    tab_dc = """<h2>Снято с производства</h2>
<div class="mut">Что выведено из каталога и чем заменено. Для сорсинга это первое,
что нужно знать: снятая линейка означает аутмаркет и склады, а не заказ у изготовителя.</div>
<div class="bar"><input id="qd" placeholder="поиск по снятым линейкам">
<span class="mut" id="cd"></span></div>
<div id="outd"></div>"""

    lic = "".join(
        f'<tr><td class="mut">{e(p["segment_title"])} · {e(p["region_title"])}</td><td>{e(x)}</td></tr>'
        for p in d["pairs"] for x in p["licensed"])
    tab_lic = f"""<h2>Лицензии, СП и кто под кем собирает</h2>
<div class="mut">{st['licensed']} связей. Лицензионная сборка — прямой ход к запчасти:
машина под одной маркой, горячая часть под другой.</div>
<div class="wrap"><table><thead><tr><th style="width:180px">Направление</th>
<th>Связь</th></tr></thead><tbody>{lic}</tbody></table></div>"""

    dead = "".join(
        f'<tr><td class="mut">{e(p["segment_title"])} · {e(p["region_title"])}</td><td>{e(x)}</td></tr>'
        for p in d["pairs"] for x in p["dead_ends"])
    summ = "".join(
        f'<div class="card"><b>{e(p["segment_title"])} · {e(p["region_title"])}</b>'
        f'<div class="txt">{e(p["summary"])}</div></div>' for p in d["pairs"])
    tab_gap = f"""<h2>Чего нет и куда не дошли</h2>{gap}
<h3>Сводка по сделанным парам</h3>{summ}
<h3>Тупики: {st['dead_ends']}</h3>
<div class="wrap"><table><thead><tr><th style="width:180px">Направление</th>
<th>Что не получилось</th></tr></thead><tbody>{dead}</tbody></table></div>"""

    S = [("m", "Изготовители", tab_mk), ("p", "Как читается номер", tab_pn),
         ("d", "Снято с производства", tab_dc), ("l", "Лицензии и СП", tab_lic),
         ("g", "Чего нет", tab_gap)]
    tabs = "".join(f'<button data-s="{sid}"{" class=on" if i == 0 else ""}>{e(t)}</button>'
                   for i, (sid, t, _) in enumerate(S))
    secs = "".join(f'<section id="{sid}"{" class=on" if i == 0 else ""}>{h}</section>'
                   for i, (sid, _, h) in enumerate(S))

    js = """
const M=__OEM__;
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id===b.dataset.s));
  window.scrollTo({top:0});});
function opts(sel,vals,label){sel.innerHTML='<option value="">'+label+'</option>'+
  vals.map(v=>'<option>'+esc(v)+'</option>').join("");}
function row(m,fields){
  let dl="";
  fields.forEach(([k,t])=>{if(m[k])dl+='<dt>'+t+'</dt><dd>'+esc(m[k])+'</dd>';});
  return '<div class="mk"><div class="hd"><span class="tag">'+esc(m.g)+'</span>'+
   '<span class="tag">'+esc(m.c)+'</span><span class="tag">'+esc(m.rg)+'</span>'+
   '<span class="mut">достоверность: '+esc(m.cf)+'</span></div>'+
   '<h4>'+esc(m.n)+'</h4><dl>'+dl+
   (m.w?'<dt>Сайт</dt><dd class="src"><a href="'+esc(m.w)+'" target="_blank" rel="noopener">'+esc(m.w)+'</a></dd>':"")+
   (m.s?'<dt>Источник</dt><dd class="src">'+esc(m.s)+'</dd>':"")+'</dl></div>';}
const FULL=[["r","Место на рынке"],["o","Владелец"],["cr","Где сделано"],
 ["f","Прежние названия"],["l","Линейки"],["sp","Характеристики"],
 ["a","Что выпускается сейчас"],["dc","Снято с производства"],["p","Чтение номера"]];
// ── изготовители
const q=document.getElementById("q"),fg=document.getElementById("fg"),
      fc=document.getElementById("fc"),fr=document.getElementById("fr"),
      cn=document.getElementById("cn"),out=document.getElementById("out");
opts(fg,[...new Set(M.map(m=>m.g))].sort(),"все направления");
opts(fc,[...new Set(M.map(m=>m.c))].sort(),"все страны");
opts(fr,[...new Set(M.map(m=>m.rg))].sort(),"все регионы");
function draw(){
  const t=q.value.trim().toLowerCase();
  let r=M;
  if(fg.value)r=r.filter(m=>m.g===fg.value);
  if(fc.value)r=r.filter(m=>m.c===fc.value);
  if(fr.value)r=r.filter(m=>m.rg===fr.value);
  if(t)r=r.filter(m=>(m.n+" "+m.l+" "+m.o+" "+m.c+" "+m.cr+" "+m.f+" "+m.a+" "+m.p).toLowerCase().includes(t));
  cn.textContent="изготовителей: "+r.length;
  out.innerHTML=r.length?r.map(m=>row(m,FULL)).join(""):'<div class="card">Ничего не нашлось.</div>';}
[q,fg,fc,fr].forEach(x=>x.oninput=draw);draw();
// ── чтение номера
const qp=document.getElementById("qp"),pg=document.getElementById("pg"),
      cp=document.getElementById("cp"),outp=document.getElementById("outp");
opts(pg,[...new Set(M.map(m=>m.g))].sort(),"все направления");
function drawP(){
  const t=qp.value.trim().toLowerCase();
  let r=M.filter(m=>m.p&&m.p.length>40);
  if(pg.value)r=r.filter(m=>m.g===pg.value);
  if(t)r=r.filter(m=>(m.n+" "+m.p).toLowerCase().includes(t));
  cp.textContent="правил: "+r.length;
  outp.innerHTML=r.length?r.map(m=>row(m,[["p","Чтение номера"],["l","Линейки"]])).join(""):
   '<div class="card">Ничего не нашлось.</div>';}
[qp,pg].forEach(x=>x.oninput=drawP);drawP();
// ── снятое
const qd=document.getElementById("qd"),cd=document.getElementById("cd"),
      outd=document.getElementById("outd");
function drawD(){
  const t=qd.value.trim().toLowerCase();
  let r=M.filter(m=>m.dc&&m.dc.length>20);
  if(t)r=r.filter(m=>(m.n+" "+m.dc).toLowerCase().includes(t));
  cd.textContent="изготовителей: "+r.length;
  outd.innerHTML=r.length?r.map(m=>row(m,[["dc","Снято с производства"],["a","Что выпускается сейчас"]])).join(""):
   '<div class="card">Ничего не нашлось.</div>';}
qd.oninput=drawD;drawD();
"""
    js = js.replace("__OEM__", json.dumps(mk, ensure_ascii=False, separators=(",", ":")))

    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Атлас производителей — линейки, снятое, чтение номера</title>
<style>{CSS}{EXTRA_CSS}</style></head><body>
<header>
<div class="mut"><a href="./">← ГШО · рабочая база</a> · <a href="./chain.html">цепочка портала</a>
 · <a href="./diag.html">диагностика и дефекты</a> · <a href="./search.html">поиск детали по номеру</a></div>
<h1>Атлас производителей</h1>
<div class="sub">{e(d['subject'])}. {st['makers']} изготовителей из {st['countries']} стран,
{st['with_pn_rule']} правил чтения номера, {st['licensed']} лицензий и СП.
Собрано {e(d['generated'])}. Атлас неполон: {cm['pairs_done']} пар из {cm['pairs_planned']}.</div>
</header>
<nav>{tabs}</nav><main>{kpi}{gap}{secs}</main>
<script>{js}</script></body></html>"""
    OUT.mkdir(exist_ok=True)
    p = OUT / "oem.html"
    p.write_text(doc, encoding="utf-8")
    print(f"zip/public/oem.html: {p.stat().st_size:,} байт | изготовителей {st['makers']}, "
          f"правил номера {st['with_pn_rule']}, лицензий {st['licensed']}")
    return p


if __name__ == "__main__":
    build()
