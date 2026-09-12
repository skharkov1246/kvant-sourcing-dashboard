#!/usr/bin/env python3
"""Три страницы портала из разведок 12.09.2026.

  zip/data/dirs_recon.json       → zip/public/dirs.html    насосы, КИПиА, электротехника
  zip/data/repair_recon.json     → zip/public/repair.html  ремонт и исполнители
  zip/data/subsupplier_recon.json→ zip/public/subs.html    субпоставщики и чтение номеров

ОБЩЕЕ ПРАВИЛО ВЁРСТКИ. Вердикт проверки стоит плашкой у каждой записи, а не
сводкой внизу. «Угол без скептика» окрашен как требующее внимания: молчаливое
серое состояние здесь опаснее ошибки — по непроверенному справочнику заказывают
детали.

Запуск: python zip/tools/build_recon_pages.py — вызывается из zip/build.py
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "public"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_telsmith_page import CSS  # noqa: E402

e = lambda s: html.escape(str(s if s is not None else ""))

EXTRA = """
.rc{border:1px solid var(--border);background:var(--surface);border-radius:10px;
 padding:11px 13px;margin:8px 0}
.rc>h4{margin:0 0 4px;font-size:14px}
.rc dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;margin:6px 0 0;font-size:12.5px}
.rc dt{color:var(--mut);white-space:nowrap}
.rc dd{margin:0}
.hd{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.hd .tag{font-size:10.5px}
@media(max-width:700px){.rc dl{grid-template-columns:1fr}.rc dt{margin-top:5px}}
"""

VC = {"подтверждено": "ok", "частично": "wr", "опровергнуто": "no", "непроверяемо": "wr",
      "скептик не сослался": "wr", "угол без скептика": "wr"}

JS_BASE = """
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const VC=%s;
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id===b.dataset.s));
  window.scrollTo({top:0});});
function opts(sel,vals,label){sel.innerHTML='<option value="">'+label+'</option>'+
  vals.map(v=>'<option>'+esc(v)+'</option>').join("");}
function card(o,fields,tags){
  let dl="";fields.forEach(([k,t])=>{if(o[k])dl+='<dt>'+t+'</dt><dd>'+esc(o[k])+'</dd>';});
  const tg=(tags||[]).filter(Boolean).map(t=>'<span class="tag">'+esc(t)+'</span>').join("");
  const v=o.v||o.verdict;
  return '<div class="rc"><div class="hd">'+tg+
   (v?'<span class="tag '+(VC[v]||"wr")+'">'+esc(v)+'</span>':"")+
   (o.cf?'<span class="mut">достоверность: '+esc(o.cf)+'</span>':"")+'</div>'+
   '<h4>'+esc(o.t)+'</h4>'+(o.d?'<div class="txt">'+esc(o.d)+'</div>':"")+
   '<dl>'+dl+(o.u?'<dt>Ссылка</dt><dd class="src"><a href="'+esc(o.u)+
     '" target="_blank" rel="noopener">'+esc(o.u)+'</a></dd>':"")+'</dl></div>';}
function filt(rows,q,keys){const t=q.trim().toLowerCase();if(!t)return rows;
  return rows.filter(r=>keys.map(k=>r[k]||"").join(" ").toLowerCase().includes(t));}
""" % json.dumps(VC, ensure_ascii=False)


def page(fname, title, sub, crumbs, kpi, warn, sections, payload, js):
    tabs = "".join(f'<button data-s="{sid}"{" class=on" if i == 0 else ""}>{e(t)}</button>'
                   for i, (sid, t, _) in enumerate(sections))
    secs = "".join(f'<section id="{sid}"{" class=on" if i == 0 else ""}>{h}</section>'
                   for i, (sid, _, h) in enumerate(sections))
    body = (JS_BASE + "\nconst DATA=" +
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n" + js)
    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><style>{CSS}{EXTRA}</style></head><body>
<header><div class="mut">{crumbs}</div>
<h1>{e(title)}</h1><div class="sub">{sub}</div></header>
<nav>{tabs}</nav><main>{kpi}{warn}{secs}</main>
<script>{body}</script></body></html>"""
    OUT.mkdir(exist_ok=True)
    p = OUT / fname
    p.write_text(doc, encoding="utf-8")
    print(f"zip/public/{fname}: {p.stat().st_size:,} байт")
    return p


CRUMBS = ('<a href="./">← ГШО · рабочая база</a> · <a href="./chain.html">цепочка портала</a>'
          ' · <a href="./diag.html">диагностика</a> · <a href="./oem.html">производители</a>')


def claims_table(groups):
    """Утверждения, которые скептик проверял сам, а не привязываясь к карточке."""
    rows = ""
    for gt, cl in groups:
        for v in cl:
            rows += (f'<tr><td class="mut">{e(gt)}</td>'
                     f'<td><span class="tag {VC.get(v["verdict"], "wr")}">{e(v["verdict"])}</span></td>'
                     f'<td>{e(v["claim"])}</td><td>{e(v["why"])}</td>'
                     f'<td>{e(v.get("correction") or "")}</td></tr>')
    return (f'<div class="wrap"><table><thead><tr><th style="width:11%">Угол</th>'
            f'<th style="width:9%">Вердикт</th><th style="width:24%">Утверждение</th>'
            f'<th style="width:28%">Проверка</th><th style="width:28%">Поправка</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>')


# ───────────────────────────────────── насосы, КИПиА, электротехника
def build_dirs():
    d = json.loads((D / "dirs_recon.json").read_text(encoding="utf-8"))
    st = d["stats"]
    F = [{"t": f["topic"], "d": f["fact"], "n": f.get("figure") or "", "s": f.get("source") or "",
          "u": f.get("url") or "", "cf": f.get("confidence") or "", "v": f["verdict"]["verdict"],
          "g": a["segment_title"], "a": a["title"]}
         for a in d["angles"] for f in a["findings"]]
    C = [{"t": c["name"], "d": c.get("makes") or "", "k": c.get("kind") or "",
          "c": c.get("country") or "", "ev": c.get("evidence") or "", "u": c.get("site") or "",
          "cf": c.get("confidence") or "", "v": c["verdict"]["verdict"],
          "g": a["segment_title"], "a": a["title"]}
         for a in d["angles"] for c in a["companies"]]

    kpi = f"""<div class="kpi">
<div><b>{st['findings']}</b><span>фактов с источником</span></div>
<div><b>{st['companies']}</b><span>карточек компаний</span></div>
<div><b>{st['angles']}</b><span>углов разведки</span></div>
<div><b>0</b><span>из них со скептиком</span></div>
<div><b>{st['gaps']}</b><span>названных пробелов</span></div>
<div><b>{st['dead_ends']}</b><span>закрытых тупиков</span></div></div>"""
    warn = f"""<div class="card" style="border-left:4px solid var(--warn,#fab219)">
<b>Ни одна запись этой страницы проверку не проходила</b>
<div class="txt">{e(d['method'])}</div>
<div class="txt">Поэтому у всех {st['findings'] + st['companies']} записей вердикт
«угол без скептика». Пользоваться как отправной точкой можно, ссылаться в
коммерческом предложении — нет.</div></div>"""

    summ = "".join(f'<div class="card"><b>{e(a["segment_title"])} · {e(a["title"])}</b>'
                   f'<div class="txt">{e(a["summary"])}</div></div>' for a in d["angles"])
    gaps = "".join(f'<tr><td class="mut">{e(a["segment_title"])} · {e(a["title"])}</td>'
                   f'<td>{e(x)}</td></tr>' for a in d["angles"] for x in a["gaps"])
    dead = "".join(f'<tr><td class="mut">{e(a["segment_title"])} · {e(a["title"])}</td>'
                   f'<td>{e(x)}</td></tr>' for a in d["angles"] for x in a["dead_ends"])

    S = [("f", "Факты и нормы", """<h2>Факты, нормы, цифры</h2>
<div class="bar"><input id="q1" placeholder="поиск по факту, стандарту, цифре">
<select id="g1"></select><select id="a1"></select><span class="mut" id="c1"></span></div>
<div id="o1"></div>"""),
         ("c", "Компании", """<h2>Компании: кто что делает</h2>
<div class="bar"><input id="q2" placeholder="поиск по компании, изделию, стране">
<select id="g2"></select><select id="k2"></select><span class="mut" id="c2"></span></div>
<div id="o2"></div>"""),
         ("s", "Сводки углов", f"<h2>Сводка по каждому углу</h2>{summ}"),
         ("g", "Чего нет", f"""<h2>Чего нет и куда не дошли</h2>
<h3>Пробелы, названные самой разведкой: {st['gaps']}</h3>
<div class="wrap"><table><thead><tr><th style="width:210px">Угол</th><th>Чего не хватает</th>
</tr></thead><tbody>{gaps}</tbody></table></div>
<h3>Тупики: {st['dead_ends']}</h3>
<div class="wrap"><table><thead><tr><th style="width:210px">Угол</th><th>Что не получилось</th>
</tr></thead><tbody>{dead}</tbody></table></div>""")]

    js = """
const FF=[["n","Цифра"],["s","Источник"]],CF=[["k","Кто это"],["c","Страна"],["ev","Чем подтверждено"]];
const q1=document.getElementById("q1"),g1=document.getElementById("g1"),a1=document.getElementById("a1"),
      c1=document.getElementById("c1"),o1=document.getElementById("o1");
opts(g1,[...new Set(DATA.F.map(x=>x.g))].sort(),"все направления");
opts(a1,[...new Set(DATA.F.map(x=>x.a))].sort(),"все углы");
function d1(){let r=DATA.F;if(g1.value)r=r.filter(x=>x.g===g1.value);
 if(a1.value)r=r.filter(x=>x.a===a1.value);r=filt(r,q1.value,["t","d","n","s"]);
 c1.textContent="фактов: "+r.length;
 o1.innerHTML=r.length?r.map(x=>card(x,FF,[x.g,x.a])).join(""):'<div class="card">Ничего не нашлось.</div>';}
[q1,g1,a1].forEach(x=>x.oninput=d1);d1();
const q2=document.getElementById("q2"),g2=document.getElementById("g2"),k2=document.getElementById("k2"),
      c2=document.getElementById("c2"),o2=document.getElementById("o2");
opts(g2,[...new Set(DATA.C.map(x=>x.g))].sort(),"все направления");
opts(k2,[...new Set(DATA.C.map(x=>x.k))].sort(),"любая роль");
function d2(){let r=DATA.C;if(g2.value)r=r.filter(x=>x.g===g2.value);
 if(k2.value)r=r.filter(x=>x.k===k2.value);r=filt(r,q2.value,["t","d","k","c","ev"]);
 c2.textContent="компаний: "+r.length;
 o2.innerHTML=r.length?r.map(x=>card(x,CF,[x.g,x.a])).join(""):'<div class="card">Ничего не нашлось.</div>';}
[q2,g2,k2].forEach(x=>x.oninput=d2);d2();
"""
    sub = (f"{e(d['subject'])}. {st['findings']} фактов с источником, {st['companies']} компаний "
           f"по {st['angles']} углам. Собрано {e(d['generated'])}. "
           f"<b>Проверку скептиком не проходила ни одна запись.</b>")
    return page("dirs.html", "Насосы, КИПиА, электротехника", sub, CRUMBS, kpi, warn, S,
                {"F": F, "C": C}, js)


# ───────────────────────────────────── ремонт и исполнители
def build_repair():
    d = json.loads((D / "repair_recon.json").read_text(encoding="utf-8"))
    st = d["stats"]
    T = [{"t": t["name"], "d": t.get("how") or "", "ap": t.get("applies_to") or "",
          "li": t.get("limits") or "", "ri": t.get("risks") or "", "eq": t.get("equipment") or "",
          "ac": t.get("acceptance") or "", "ec": t.get("economics") or "",
          "sd": t.get("standard") or "", "s": t.get("source") or "",
          "cf": t.get("confidence") or "", "v": t["verdict"]["verdict"],
          "vw": t["verdict"].get("why") or "", "vc": t["verdict"].get("correction") or "",
          "a": a["title"]}
         for a in d["tech_angles"] for t in a["technologies"]]
    K = [{"t": c["name"], "d": c.get("does") or "", "k": c.get("kind") or "",
          "r": c.get("region") or "", "ci": c.get("city") or "", "eq": c.get("equipment") or "",
          "te": c.get("technologies") or "", "op": c.get("own_production") or "",
          "ev": c.get("evidence") or "", "u": c.get("site") or "",
          "cf": c.get("confidence") or "", "v": c["verdict"]["verdict"], "a": a["title"]}
         for a in d["contractor_angles"] for c in a["contractors"]]

    kpi = f"""<div class="kpi">
<div><b>{st['technologies']}</b><span>технологий восстановления</span></div>
<div><b>{st['contractors']}</b><span>исполнителей</span></div>
<div><b>{st['contractors_own_production']}</b><span>с собственным переделом</span></div>
<div><b>{len(st['by_region'])}</b><span>городов</span></div>
<div><b>{st['checked_claims']}</b><span>утверждений проверено</span></div>
<div><b>{st['by_claim_verdict'].get('опровергнуто', 0)}</b><span>забраковано</span></div></div>"""
    warn = f"""<div class="card" style="border-left:4px solid var(--warn,#fab219)">
<b>Что проверено, а что нет</b><div class="txt">{e(d['method'])}</div>
<div class="txt">Скептики проверили {st['checked_claims']} утверждений по технологиям:
подтверждено {st['by_claim_verdict'].get('подтверждено', 0)},
поправлено {st['by_claim_verdict'].get('частично', 0)},
забраковано {st['by_claim_verdict'].get('опровергнуто', 0)},
непроверяемо {st['by_claim_verdict'].get('непроверяемо', 0)}.
Все {st['contractors']} карточек исполнителей — без проверки.</div></div>"""

    claims = claims_table([(a["title"], a["skeptic"]["checked_claims"])
                           for a in d["tech_angles"] if a["skeptic"]])
    miss = "".join(f'<tr><td class="mut">{e(a["title"])}</td><td>{e(m)}</td></tr>'
                   for a in d["tech_angles"] if a["skeptic"]
                   for m in a["skeptic"]["missing"])
    dead = "".join(f'<tr><td class="mut">{e(a["title"])}</td><td>{e(x)}</td></tr>'
                   for a in d["tech_angles"] + d["contractor_angles"] for x in a["dead_ends"])

    S = [("t", "Технологии ремонта", """<h2>Чем восстанавливают</h2>
<div class="mut">Границы применимости и риски — рядом с каждой технологией: они и решают,
восстанавливать деталь или покупать новую.</div>
<div class="bar"><input id="q1" placeholder="поиск по технологии, детали, стандарту">
<select id="a1"></select><select id="v1"></select><span class="mut" id="c1"></span></div>
<div id="o1"></div>"""),
         ("k", "Исполнители", """<h2>Кто это делает</h2>
<div class="mut">Последнее звено цепочки портала. Собственный передел отмечен отдельно:
посредник и завод — разные ответы на вопрос «кому отдать работу».</div>
<div class="bar"><input id="q2" placeholder="поиск по исполнителю, оборудованию, технологии">
<select id="k2"></select><select id="r2"></select><span class="mut" id="c2"></span></div>
<div id="o2"></div>"""),
         ("v", "Проверка скептиков", f"""<h2>Что проверено и что забраковано</h2>
<div class="mut">{st['checked_claims']} утверждений по технологиям. Поправка стоит там,
где скептик смог её дать.</div>{claims}"""),
         ("g", "Чего нет", f"""<h2>Чего нет и куда не дошли</h2>
<h3>Пропущено, по мнению скептиков: {st['missing']}</h3>
<div class="wrap"><table><thead><tr><th style="width:210px">Угол</th><th>Чего не хватает</th>
</tr></thead><tbody>{miss}</tbody></table></div>
<h3>Тупики: {st['dead_ends']}</h3>
<div class="wrap"><table><thead><tr><th style="width:210px">Угол</th><th>Что не получилось</th>
</tr></thead><tbody>{dead}</tbody></table></div>""")]

    js = """
const TF=[["ap","К чему применяется"],["li","Границы применимости"],["ri","Риски"],
 ["eq","Оборудование"],["ac","Приёмка"],["ec","Экономика"],["sd","Стандарт"],
 ["vw","Проверка"],["vc","Поправка"],["s","Источник"]];
const KF=[["k","Кто это"],["r","Где"],["eq","Оборудование"],["te","Технологии"],
 ["op","Собственный передел"],["ev","Чем подтверждено"]];
const q1=document.getElementById("q1"),a1=document.getElementById("a1"),v1=document.getElementById("v1"),
      c1=document.getElementById("c1"),o1=document.getElementById("o1");
opts(a1,[...new Set(DATA.T.map(x=>x.a))].sort(),"все углы");
opts(v1,[...new Set(DATA.T.map(x=>x.v))],"любой вердикт");
function d1(){let r=DATA.T;if(a1.value)r=r.filter(x=>x.a===a1.value);
 if(v1.value)r=r.filter(x=>x.v===v1.value);r=filt(r,q1.value,["t","d","ap","li","sd"]);
 c1.textContent="технологий: "+r.length;
 o1.innerHTML=r.length?r.map(x=>card(x,TF,[x.a])).join(""):'<div class="card">Ничего не нашлось.</div>';}
[q1,a1,v1].forEach(x=>x.oninput=d1);d1();
const q2=document.getElementById("q2"),k2=document.getElementById("k2"),r2=document.getElementById("r2"),
      c2=document.getElementById("c2"),o2=document.getElementById("o2");
opts(k2,[...new Set(DATA.K.map(x=>x.k))].sort(),"любая роль");
opts(r2,[...new Set(DATA.K.map(x=>x.ci))].sort(),"все города");
function d2(){let r=DATA.K;if(k2.value)r=r.filter(x=>x.k===k2.value);
 if(r2.value)r=r.filter(x=>x.ci===r2.value);r=filt(r,q2.value,["t","d","eq","te","op","r"]);
 c2.textContent="исполнителей: "+r.length;
 o2.innerHTML=r.length?r.map(x=>card(x,KF,[x.ci,x.a])).join(""):'<div class="card">Ничего не нашлось.</div>';}
[q2,k2,r2].forEach(x=>x.oninput=d2);d2();
"""
    sub = (f"{e(d['subject'])}. {st['technologies']} технологий, {st['contractors']} исполнителей "
           f"в {len(st['by_region'])} городах, {st['checked_claims']} проверенных утверждений. "
           f"Собрано {e(d['generated'])}.")
    return page("repair.html", "Ремонтные решения и исполнители", sub, CRUMBS, kpi, warn, S,
                {"T": T, "K": K}, js)


# ───────────────────────────────────── субпоставщики и чтение номеров
def build_subs():
    d = json.loads((D / "subsupplier_recon.json").read_text(encoding="utf-8"))
    st = d["stats"]
    CH = [{"t": f'{c["oem"]} · {c["node"]}', "d": c.get("tier1") or "",
           "oem": c.get("oem") or "", "nd": c.get("node") or "",
           "t1": c.get("tier1") or "", "t1c": c.get("tier1_country") or "",
           "t1e": c.get("tier1_evidence") or "", "t2": c.get("tier2") or "",
           "t2e": c.get("tier2_evidence") or "", "bd": c.get("buyable_direct") or "",
           "cf": c.get("confidence") or "", "v": c["verdict"]["verdict"], "g": s["title"]}
          for s in d["segments"] for c in s["chains"]]
    RU = [{"t": r.get("oem") or "", "d": r.get("format") or "",
           "mh": r.get("maker_hint") or "", "cr": r.get("crossref_route") or "",
           "pc": r.get("public_catalogs") or "", "ex": r.get("examples") or "",
           "br": r.get("breaks") or "", "s": r.get("source") or "",
           "cf": r.get("confidence") or "", "v": r["verdict"]["verdict"],
           "vw": r["verdict"].get("why") or "", "vc": r["verdict"].get("correction") or "",
           "g": s["title"]}
          for s in d["segments"] for r in s["rules"]]

    kpi = f"""<div class="kpi">
<div><b>{st['chains']}</b><span>цепочек OEM → узел → кто делает</span></div>
<div><b>{st['rules']}</b><span>правил чтения номера</span></div>
<div><b>{st['buyable_direct']}</b><span>покупается напрямую, минуя OEM</span></div>
<div><b>{st['ours']}</b><span>совпадений с нашей базой</span></div>
<div><b>{st['with_skeptic']} / {st['segments']}</b><span>направлений со скептиком</span></div>
<div><b>{st['checked_claims']}</b><span>утверждений проверено</span></div></div>"""
    warn = f"""<div class="card" style="border-left:4px solid var(--warn,#fab219)">
<b>Что проверено, а что нет</b><div class="txt">{e(d['method'])}</div>
<div class="txt">Скептик отработал по ГТУ и ГПУ: из {st['checked_claims']} утверждений
забраковано {st['by_claim_verdict'].get('опровергнуто', 0)} — в основном шифровки,
выведенные по нашей же выборке и на ней же проверенные. По остальным пяти
направлениям проверки не было вовсе.</div></div>"""

    claims = claims_table([(s["title"], s["skeptic"]["checked_claims"])
                           for s in d["segments"] if s["skeptic"]])
    ours = "".join(f'<tr><td class="mut">{e(s["title"])}</td><td>{e(x)}</td></tr>'
                   for s in d["segments"] for x in s["ours"])
    dead = "".join(f'<tr><td class="mut">{e(s["title"])}</td><td>{e(x)}</td></tr>'
                   for s in d["segments"] for x in s["dead_ends"])

    S = [("c", "Цепочки субпоставщиков", """<h2>Кто на самом деле делает узел</h2>
<div class="mut">Два уровня вглубь от марки на шильдике. Колонка «напрямую» отвечает
на главный вопрос закупки: можно ли купить у изготовителя, минуя OEM.</div>
<div class="bar"><input id="q1" placeholder="поиск по OEM, узлу, изготовителю">
<select id="g1"></select><select id="b1"></select><span class="mut" id="c1"></span></div>
<div id="o1"></div>"""),
         ("r", "Чтение номера", """<h2>Как читается каталожный номер</h2>
<div class="mut">Формат, что в номере указывает на изготовителя, куда идти за
перекрёстной ссылкой и — обязательно — где правило ломается.</div>
<div class="bar"><input id="q2" placeholder="поиск по изготовителю, формату, примеру">
<select id="g2"></select><select id="v2"></select><span class="mut" id="c2"></span></div>
<div id="o2"></div>"""),
         ("v", "Проверка скептиков", f"""<h2>Что проверено и что забраковано</h2>
<div class="mut">{st['checked_claims']} утверждений по ГТУ и ГПУ.</div>{claims}"""),
         ("o", "Совпадения и тупики", f"""<h2>Что из этого уже есть у нас</h2>
<div class="mut">{st['ours']} совпадений разведки с нашей базой поставщиков и номенклатуры.</div>
<div class="wrap"><table><thead><tr><th style="width:210px">Направление</th>
<th>Совпадение</th></tr></thead><tbody>{ours}</tbody></table></div>
<h3>Тупики: {st['dead_ends']}</h3>
<div class="wrap"><table><thead><tr><th style="width:210px">Направление</th>
<th>Что не получилось</th></tr></thead><tbody>{dead}</tbody></table></div>""")]

    js = """
const CF2=[["oem","OEM на шильдике"],["nd","Узел"],["t1","Кто делает (1-й уровень)"],
 ["t1c","Страна"],["t1e","Чем подтверждено"],["t2","Кто делает ему (2-й уровень)"],
 ["t2e","Чем подтверждено"],["bd","Покупается напрямую"]];
const RF=[["mh","Что указывает на изготовителя"],["cr","Куда идти за перекрёстной ссылкой"],
 ["pc","Открытые каталоги"],["ex","Примеры"],["br","Где правило ломается"],
 ["vw","Проверка"],["vc","Поправка"],["s","Источник"]];
const q1=document.getElementById("q1"),g1=document.getElementById("g1"),b1=document.getElementById("b1"),
      c1=document.getElementById("c1"),o1=document.getElementById("o1");
opts(g1,[...new Set(DATA.CH.map(x=>x.g))].sort(),"все направления");
opts(b1,["покупается напрямую","только через OEM"],"любой способ закупки");
function d1(){let r=DATA.CH;if(g1.value)r=r.filter(x=>x.g===g1.value);
 if(b1.value)r=r.filter(x=>/^\\s*да/i.test(x.bd)===(b1.value==="покупается напрямую"));
 r=filt(r,q1.value,["oem","nd","t1","t2","t1c"]);
 c1.textContent="цепочек: "+r.length;
 o1.innerHTML=r.length?r.map(x=>card(x,CF2,[x.g])).join(""):'<div class="card">Ничего не нашлось.</div>';}
[q1,g1,b1].forEach(x=>x.oninput=d1);d1();
const q2=document.getElementById("q2"),g2=document.getElementById("g2"),v2=document.getElementById("v2"),
      c2=document.getElementById("c2"),o2=document.getElementById("o2");
opts(g2,[...new Set(DATA.RU.map(x=>x.g))].sort(),"все направления");
opts(v2,[...new Set(DATA.RU.map(x=>x.v))],"любой вердикт");
function d2(){let r=DATA.RU;if(g2.value)r=r.filter(x=>x.g===g2.value);
 if(v2.value)r=r.filter(x=>x.v===v2.value);r=filt(r,q2.value,["t","d","ex","mh","br"]);
 c2.textContent="правил: "+r.length;
 o2.innerHTML=r.length?r.map(x=>card(x,RF,[x.g])).join(""):'<div class="card">Ничего не нашлось.</div>';}
[q2,g2,v2].forEach(x=>x.oninput=d2);d2();
"""
    sub = (f"{e(d['subject'])}. {st['chains']} цепочек по {st['segments']} направлениям, "
           f"{st['rules']} правил чтения номера, {st['buyable_direct']} узлов покупаются "
           f"напрямую. Собрано {e(d['generated'])}.")
    return page("subs.html", "Субпоставщики и чтение номеров", sub, CRUMBS, kpi, warn, S,
                {"CH": CH, "RU": RU}, js)


if __name__ == "__main__":
    build_dirs()
    build_repair()
    build_subs()
