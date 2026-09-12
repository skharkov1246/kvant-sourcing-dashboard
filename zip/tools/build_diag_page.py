#!/usr/bin/env python3
"""Страница «Диагностика и дефекты» → zip/public/diag.html.

ЗАЧЕМ. В цепочке портала «машина → узел → признак → дефект → ремонт → запчасть»
звено «признак» было пустым во всех семи направлениях, а «дефект» — почти
пустым. Страница закрывает оба: обратный индекс «наблюдаемый признак → дефект»
из dict/symptom.json и каталог дефектов из zip/data/diagnostics_recon.json.

ГЛАВНОЕ В ВЁРСТКЕ. Вход не через оглавление, а через признак: диагност видит
на машине рост температуры или вторую гармонику — и получает перечень дефектов,
методов проверки и запчастей. Вердикт скептика стоит рядом с каждой строкой;
«скептик не сослался» показан отдельным значением, потому что пустое поле
читалось бы как «проверено, всё хорошо».

Запуск: python zip/tools/build_diag_page.py — вызывается из zip/build.py
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "public"
DICT = ROOT.parent / "dict"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_telsmith_page import CSS  # общая вёрстка страниц-разведок  # noqa: E402

e = lambda s: html.escape(str(s if s is not None else ""))

EXTRA_CSS = """
.sym{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 14px}
.sym button{border:1px solid var(--border);background:var(--surface);color:var(--ink);
 border-radius:20px;padding:4px 11px;font:inherit;font-size:12px;cursor:pointer}
.sym button.on{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.sym button i{font-style:normal;opacity:.6;margin-left:5px}
.grp{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em;
 width:100%;margin:6px 0 -2px}
.dfc{border:1px solid var(--border);background:var(--surface);border-radius:10px;
 padding:11px 13px;margin:8px 0}
.dfc>h4{margin:0 0 4px;font-size:14px}
.dfc dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;margin:6px 0 0;font-size:12.5px}
.dfc dt{color:var(--mut);white-space:nowrap}
.dfc dd{margin:0}
.hd{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.hd .tag{font-size:10.5px}
@media(max-width:700px){.dfc dl{grid-template-columns:1fr}.dfc dt{margin-top:5px}}
"""

VCLASS = {"подтверждено": "ok", "частично": "wr", "опровергнуто": "no",
          "непроверяемо": "wr", "скептик не сослался": "wr", "угол без скептика": "wr"}


def build():
    rec = json.loads((D / "diagnostics_recon.json").read_text(encoding="utf-8"))
    sym = json.loads((DICT / "symptom.json").read_text(encoding="utf-8"))
    st, cov = rec["stats"], sym["coverage"]

    # ── данные для клиента: только то, что рисуется
    rows = [{"n": r["node"], "d": r["defect"], "s": r["symptoms"], "c": r["cause"],
             "m": r["method"], "q": r["consequence"], "r": r["repairable"],
             "p": r["parts"], "src": r["source"], "cf": r["confidence"],
             "v": r["verdict"], "g": r["scope"]}
            for r in sym["defect_rows"]]
    syms = [{"k": r["key"], "t": r["title"], "g": r["group"], "n": r["n"], "d": r["defects"]}
            for r in sym["records"]]
    groups = {g["key"]: g["title"] for g in sym["groups"]}
    finds = [{"t": f["topic"], "f": f["fact"], "n": f.get("figure") or "",
              "s": f.get("source") or "", "u": f.get("url") or "",
              "cf": f.get("confidence") or "", "v": f["verdict"]["verdict"],
              "w": f["verdict"].get("why") or "", "c": f["verdict"].get("correction") or "",
              "a": a["title"]}
             for a in rec["angles"] for f in a["findings"]]

    vb = st["by_verdict"]
    kpi = f"""<div class="kpi">
<div><b>{len(syms)}</b><span>канонических признаков</span></div>
<div><b>{st['defects']}</b><span>дефектов в каталоге</span></div>
<div><b>{cov['pct']} %</b><span>дефектов с распознанным признаком</span></div>
<div><b>{len(sym['by_node'])}</b><span>узлов</span></div>
<div><b>{st['findings']}</b><span>норм и цифр с источником</span></div>
<div><b>{vb.get('опровергнуто', 0)}</b><span>забраковано скептиком</span></div>
</div>"""

    warn = f"""<div class="card"><b>Что тут проверено, а что нет</b>
<div class="txt">{e(rec['caveat'])}</div>
<div class="txt">Из {st['findings']} численных норм скептик подтвердил
{vb.get('подтверждено', 0)}, поправил {vb.get('частично', 0)}, забраковал
{vb.get('опровергнуто', 0)}, признал непроверяемыми {vb.get('непроверяемо', 0)}.
Каталог дефектов проверен точечно: {sum(1 for r in rows if r['v'] != 'скептик не сослался')}
строк из {len(rows)}. Остальные — черновик разведки, который лечится отдельным проходом
проверки, а не молчанием.</div></div>"""

    # ── вкладка 1: признак → дефект
    chips = []
    for g, gt in groups.items():
        part = [s for s in syms if s["g"] == g]
        if not part:
            continue
        chips.append(f'<div class="grp">{e(gt)}</div>')
        for s in part:
            chips.append(f'<button data-k="{e(s["k"])}">{e(s["t"])}<i>{s["n"]}</i></button>')
    tab_sym = f"""<h2>Признак → дефект</h2>
<div class="mut">Выбери наблюдаемый признак — получишь дефекты, методы проверки и запчасти,
которые за ним стоят. Один дефект попадает в несколько признаков: так и в жизни.</div>
<div class="bar"><input id="qs" placeholder="поиск по признаку, дефекту, узлу, запчасти">
<select id="gs"></select><label><input type="checkbox" id="vs"> только проверенное скептиком</label>
<span class="mut" id="cs"></span></div>
<div class="sym" id="chips">{''.join(chips)}</div>
<div id="out"></div>"""

    # ── вкладка 2: каталог по узлам
    nodes = "".join(
        f'<tr><td>{e(k)}</td><td class="n">{v}</td></tr>'
        for k, v in sym["by_node"].items())
    tab_cat = f"""<h2>Каталог дефектов по узлам</h2>
<div class="mut">{st['defects']} строк, {len(sym['by_node'])} узлов. Узлы сведены из
{len(set(r['node_raw'] for r in sym['defect_rows']))} свободных названий разведки —
сведение детерминированное, спор возможен только о том, куда отнести название.</div>
<div class="wrap"><table><thead><tr><th>Узел</th><th>Дефектов</th></tr></thead>
<tbody>{nodes}</tbody></table></div>
<div class="bar"><input id="qc" placeholder="поиск по каталогу">
<select id="nc"></select><select id="sc"></select><span class="mut" id="cc"></span></div>
<div id="outc"></div>"""

    # ── вкладка 3: нормы и цифры
    tab_num = """<h2>Нормы, пороги, формулы</h2>
<div class="mut">Численные значения из стандартов и практики. Вердикт проверки —
рядом с каждым. Поправка скептика показана, когда она есть: цифра без поправки
и цифра с поправкой различаются на глаз.</div>
<div class="bar"><input id="qf" placeholder="поиск по норме, стандарту, цифре">
<select id="vf"></select><select id="af"></select><span class="mut" id="cf"></span></div>
<div id="outf"></div>"""

    # ── вкладка 4: забраковано
    bad = [f for f in finds if f["v"] in ("опровергнуто", "непроверяемо")]
    cards = []
    for f in sorted(bad, key=lambda x: (x["v"] != "опровергнуто", x["a"])):
        cls = VCLASS.get(f["v"], "wr")
        cards.append(
            f'<div class="dfc"><div class="hd"><span class="tag {cls}">{e(f["v"])}</span>'
            f'<span class="mut">{e(f["a"])}</span></div>'
            f'<h4>{e(f["t"])}</h4><div class="txt">{e(f["f"])}</div>'
            f'<dl><dt>Почему</dt><dd>{e(f["w"])}</dd>'
            + (f'<dt>Как правильно</dt><dd>{e(f["c"])}</dd>' if f["c"] else "")
            + f'<dt>Источник</dt><dd class="src">{e(f["s"])}</dd></dl></div>')
    tab_bad = f"""<h2>Забраковано и не поддалось проверке</h2>
<div class="mut">{len(bad)} позиций. Это не брак работы, а её результат: ошибка,
найденная до публикации, стоит прогона, найденная после — доверия. Каждая строка
несёт готовую поправку там, где скептик смог её дать.</div>{''.join(cards)}"""

    # ── вкладка 5: чего нет
    miss, dead = [], []
    for a in rec["angles"]:
        if a["skeptic"]:
            for m in a["skeptic"]["missing"]:
                miss.append((a["title"], m))
        for x in a["dead_ends"]:
            dead.append((a["title"], x))
    tab_gap = f"""<h2>Чего в каталоге нет</h2>
<div class="mut">{len(miss)} пропусков, названных скептиками, и {len(dead)} закрытых
тупиков. Список пропусков — это следующая работа, а не оговорка.</div>
<h3>Пропущенные дефекты и темы</h3>
<div class="wrap"><table><thead><tr><th>Угол</th><th>Чего не хватает</th></tr></thead><tbody>
{''.join(f'<tr><td class="mut">{e(t)}</td><td>{e(m)}</td></tr>' for t, m in miss)}
</tbody></table></div>
<h3>Тупики: куда ходили и не дошли</h3>
<div class="wrap"><table><thead><tr><th>Угол</th><th>Что не получилось</th></tr></thead><tbody>
{''.join(f'<tr><td class="mut">{e(t)}</td><td>{e(x)}</td></tr>' for t, x in dead)}
</tbody></table></div>"""

    S = [("s", "Признак → дефект", tab_sym),
         ("c", "Каталог по узлам", tab_cat),
         ("f", "Нормы и цифры", tab_num),
         ("b", "Забраковано", tab_bad),
         ("g", "Чего нет", tab_gap)]
    tabs = "".join(f'<button data-s="{sid}"{" class=on" if i == 0 else ""}>{e(t)}</button>'
                   for i, (sid, t, _) in enumerate(S))
    secs = "".join(f'<section id="{sid}"{" class=on" if i == 0 else ""}>{h}</section>'
                   for i, (sid, _, h) in enumerate(S))

    payload = json.dumps({"rows": rows, "syms": syms, "groups": groups, "finds": finds,
                          "angles": sorted({f["a"] for f in finds})},
                         ensure_ascii=False, separators=(",", ":"))

    js = """
const DATA=__DIAG__;
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const VC={"подтверждено":"ok","частично":"wr","опровергнуто":"no","непроверяемо":"wr",
 "скептик не сослался":"wr","угол без скептика":"wr"};
const SC={"common":"общее","turbo":"турбомашины","pumps":"насосы","recip":"поршневые"};
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id===b.dataset.s));
  window.scrollTo({top:0});});
function opts(sel,vals,label){sel.innerHTML='<option value="">'+label+'</option>'+
  vals.map(v=>'<option>'+esc(v)+'</option>').join("");}
function defCard(r){
  const cls=VC[r.v]||"wr";
  return '<div class="dfc"><div class="hd"><span class="tag">'+esc(r.n)+'</span>'+
   '<span class="tag">'+esc(SC[r.g]||r.g)+'</span><span class="tag '+cls+'">'+esc(r.v)+'</span>'+
   '<span class="mut">достоверность: '+esc(r.cf)+'</span></div>'+
   '<h4>'+esc(r.d)+'</h4><dl>'+
   '<dt>Признак</dt><dd>'+esc(r.s)+'</dd>'+
   '<dt>Причина</dt><dd>'+esc(r.c)+'</dd>'+
   '<dt>Чем проверить</dt><dd>'+esc(r.m)+'</dd>'+
   '<dt>Чем кончится</dt><dd>'+esc(r.q)+'</dd>'+
   '<dt>Ремонтопригодность</dt><dd>'+esc(r.r)+'</dd>'+
   '<dt>Запчасти</dt><dd>'+esc(r.p)+'</dd>'+
   '<dt>Источник</dt><dd class="src">'+esc(r.src)+'</dd></dl></div>';}
// ── вкладка 1
let pick=null;
const qs=document.getElementById("qs"),gs=document.getElementById("gs"),
      vs=document.getElementById("vs"),cs=document.getElementById("cs"),out=document.getElementById("out");
opts(gs,Object.values(DATA.groups),"все группы признаков");
document.querySelectorAll("#chips button").forEach(b=>b.onclick=()=>{
  pick=(pick===b.dataset.k)?null:b.dataset.k;
  document.querySelectorAll("#chips button").forEach(x=>x.classList.toggle("on",x.dataset.k===pick));
  drawS();});
function drawS(){
  const q=qs.value.trim().toLowerCase(),g=gs.value,onlyv=vs.checked;
  let list=DATA.syms;
  if(pick)list=list.filter(s=>s.k===pick);
  if(g)list=list.filter(s=>DATA.groups[s.g]===g);
  let html="",total=0;
  list.forEach(s=>{
    let rs=s.d.map(i=>DATA.rows[i]);
    if(onlyv)rs=rs.filter(r=>r.v!=="скептик не сослался");
    if(q)rs=rs.filter(r=>(r.d+" "+r.n+" "+r.s+" "+r.c+" "+r.p+" "+r.m).toLowerCase().includes(q));
    if(!rs.length)return;
    total+=rs.length;
    html+='<h3>'+esc(s.t)+' <span class="mut">'+rs.length+'</span></h3>'+rs.map(defCard).join("");});
  cs.textContent=total?("показано связей: "+total):"ничего не нашлось";
  out.innerHTML=html||'<div class="card">Ничего не нашлось. Сбрось фильтры.</div>';}
[qs,gs].forEach(x=>x.oninput=drawS);vs.onchange=drawS;drawS();
// ── вкладка 2
const qc=document.getElementById("qc"),nc=document.getElementById("nc"),
      sc=document.getElementById("sc"),cc=document.getElementById("cc"),outc=document.getElementById("outc");
opts(nc,[...new Set(DATA.rows.map(r=>r.n))].sort(),"все узлы");
opts(sc,[...new Set(DATA.rows.map(r=>SC[r.g]||r.g))].sort(),"все направления");
function drawC(){
  const q=qc.value.trim().toLowerCase();
  let rs=DATA.rows;
  if(nc.value)rs=rs.filter(r=>r.n===nc.value);
  if(sc.value)rs=rs.filter(r=>(SC[r.g]||r.g)===sc.value);
  if(q)rs=rs.filter(r=>(r.d+" "+r.n+" "+r.s+" "+r.c+" "+r.p+" "+r.m+" "+r.src).toLowerCase().includes(q));
  cc.textContent="дефектов: "+rs.length;
  outc.innerHTML=rs.length?rs.map(defCard).join(""):'<div class="card">Ничего не нашлось.</div>';}
[qc,nc,sc].forEach(x=>x.oninput=drawC);drawC();
// ── вкладка 3
const qf=document.getElementById("qf"),vf=document.getElementById("vf"),
      af=document.getElementById("af"),cf=document.getElementById("cf"),outf=document.getElementById("outf");
opts(vf,[...new Set(DATA.finds.map(f=>f.v))],"любой вердикт");
opts(af,DATA.angles,"все углы");
function drawF(){
  const q=qf.value.trim().toLowerCase();
  let fs=DATA.finds;
  if(vf.value)fs=fs.filter(f=>f.v===vf.value);
  if(af.value)fs=fs.filter(f=>f.a===af.value);
  if(q)fs=fs.filter(f=>(f.t+" "+f.f+" "+f.n+" "+f.s).toLowerCase().includes(q));
  cf.textContent="норм: "+fs.length;
  outf.innerHTML=fs.length?fs.map(f=>'<div class="dfc"><div class="hd">'+
   '<span class="tag '+(VC[f.v]||"wr")+'">'+esc(f.v)+'</span><span class="mut">'+esc(f.a)+'</span>'+
   (f.cf?'<span class="mut">достоверность: '+esc(f.cf)+'</span>':"")+'</div>'+
   '<h4>'+esc(f.t)+'</h4><div class="txt">'+esc(f.f)+'</div><dl>'+
   (f.n?'<dt>Цифра</dt><dd>'+esc(f.n)+'</dd>':"")+
   (f.w?'<dt>Проверка</dt><dd>'+esc(f.w)+'</dd>':"")+
   (f.c?'<dt>Поправка</dt><dd>'+esc(f.c)+'</dd>':"")+
   '<dt>Источник</dt><dd class="src">'+esc(f.s)+
   (f.u?' · <a href="'+esc(f.u)+'" target="_blank" rel="noopener">ссылка</a>':"")+
   '</dd></dl></div>').join(""):'<div class="card">Ничего не нашлось.</div>';}
[qf,vf,af].forEach(x=>x.oninput=drawF);drawF();
"""
    js = js.replace("__DIAG__", payload)

    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Диагностика и дефекты — признак, причина, ремонт, запчасть</title>
<style>{CSS}{EXTRA_CSS}</style></head><body>
<header>
<div class="mut"><a href="./">← ГШО · рабочая база</a> · <a href="./chain.html">цепочка портала</a>
 · <a href="./search.html">поиск детали по номеру</a></div>
<h1>Диагностика и дефекты</h1>
<div class="sub">{e(rec['subject'])}. {len(syms)} канонических признаков,
{st['defects']} дефектов по {len(sym['by_node'])} узлам, {st['findings']} норм с источником.
Собрано {e(rec['generated'])}. Вердикт проверки стоит рядом с каждой строкой.</div>
</header>
<nav>{tabs}</nav><main>{kpi}{warn}{secs}</main>
<script>{js}</script></body></html>"""
    OUT.mkdir(exist_ok=True)
    p = OUT / "diag.html"
    p.write_text(doc, encoding="utf-8")
    print(f"zip/public/diag.html: {p.stat().st_size:,} байт | признаков {len(syms)}, "
          f"дефектов {st['defects']}, узлов {len(sym['by_node'])}, норм {st['findings']}")
    return p


if __name__ == "__main__":
    build()
