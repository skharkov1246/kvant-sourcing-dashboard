# -*- coding: utf-8 -*-
"""Единый вход: поиск детали по любому номеру. Один самодостаточный HTML.

Точка входа для сотрудника: одна строка. Вводится любой номер — наш KV,
номер бренда, реального изготовителя, аналога или поставщика — и открывается
карточка детали со всеми известными номерами и ссылкой на раздел.

Выход: pnw/public/search.html
"""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "pnw" / "data"
OUT = ROOT / "pnw" / "public" / "search.html"

SECTION_URL = {
    "ЗИП ГШО": "https://kvant-zip.pages.dev",
    "ГТУ": "https://kvant-zip.pages.dev/gt/",
}


def main():
    items = json.loads((DATA / "item_master.json").read_text(encoding="utf-8"))["items"]
    nums = json.loads((DATA / "numbers.json").read_text(encoding="utf-8"))["rows"]

    kv2i = {it["kv"]: i for i, it in enumerate(items)}
    # компактная проекция: экономим вес страницы
    I = [[it["kv"], (it.get("name") or "")[:90], it.get("brand") or "",
          it.get("maker") or "", (it.get("node") or "")[:40],
          (it.get("machine") or "")[:40], it["section"],
          (it.get("equipment") or it.get("client") or "")[:40],
          ((it.get("material") or "") + " " + (it.get("note") or ""))[:110]] for it in items]
    KIND = {"свой": 0, "бренд": 1, "изготовитель": 2, "поставщик": 3, "аналог": 4, "заказчика": 5}
    N = []
    for r in nums:
        i = kv2i.get(r["kv"])
        if i is None:
            continue
        N.append([r["number_norm"], r["number"], KIND.get(r["kind"], 4), (r.get("owner") or "")[:26], i])

    css = """
*{box-sizing:border-box}
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;color:#111;background:#f5f6f8}
header{background:#1b2330;color:#fff;padding:14px 18px}
h1{font-size:17px;margin:0 0 3px;font-weight:600}
.sub{font-size:12px;opacity:.75}
.wrap{max-width:1080px;margin:0 auto;padding:16px 18px 40px}
.searchbox{background:#fff;border:1px solid #d5d8dd;border-radius:6px;padding:14px;margin:-28px 0 14px;
 box-shadow:0 2px 10px rgba(0,0,0,.08)}
#q{width:100%;font-size:17px;padding:11px 13px;border:2px solid #1b2330;border-radius:5px;outline:none}
#q:focus{border-color:#0b5fd0}
.hint{font-size:12px;color:#666;margin-top:7px}
.stat{font-size:12px;color:#555;margin:9px 2px}
.card{background:#fff;border:1px solid #d5d8dd;border-radius:6px;padding:12px 14px;margin:9px 0}
.kv{font-size:17px;font-weight:700;color:#0b3d91;letter-spacing:.4px;font-family:ui-monospace,Menlo,Consolas,monospace}
.nm{font-size:14px;margin:3px 0 8px}
.attrs{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.a{background:#eef2f7;border:1px solid #dbe1ea;border-radius:3px;padding:2px 7px;font-size:12px}
.a b{color:#555;font-weight:500}
table{border-collapse:collapse;width:100%;margin-top:5px}
th,td{border:1px solid #dfe2e6;padding:4px 7px;text-align:left;font-size:12.5px}
th{background:#f2f4f7;font-weight:600}
.num{font-family:ui-monospace,Menlo,Consolas,monospace}
.k1{color:#0b3d91}.k2{color:#1a7f37;font-weight:600}.k3{color:#a15c00}.k4{color:#666}
.hit{background:#fff3cd;font-weight:700}
.sec{float:right;font-size:12px}
.sec a{color:#0b5fd0;text-decoration:none}
.empty{padding:26px;text-align:center;color:#777}
mark{background:#ffe066;padding:0 1px}
"""
    js = """
const KINDS=['свой','бренд','изготовитель','поставщик','аналог','заказчика'];
const KC=['k2','k1','k2','k3','k4','k4'];
const norm=s=>String(s||'').toUpperCase().replace(/[^A-ZА-Я0-9]/g,'');
const byItem={};
for(const n of N){(byItem[n[4]]=byItem[n[4]]||[]).push(n)}
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');

function search(q){
  const nq=norm(q); if(nq.length<3) return [];
  const exact=[],partial=[],byname=[];
  const seen=new Set();
  for(const n of N){
    if(n[0]===nq){ if(!seen.has(n[4])){seen.add(n[4]);exact.push([n[4],n]);} }
    else if(n[0].includes(nq)){ if(!seen.has(n[4])){seen.add(n[4]);partial.push([n[4],n]);} }
  }
  if(exact.length+partial.length<40){
    // по наименованию: все слова запроса должны встретиться, порядок не важен
    const words=q.toUpperCase().split(/\s+/).filter(w=>w.length>2);
    if(words.length){
      for(let i=0;i<I.length;i++){
        if(seen.has(i))continue;
        const hay=(I[i][1]+' '+I[i][2]+' '+I[i][3]+' '+I[i][4]+' '+I[i][5]+' '+(I[i][8]||'')).toUpperCase();
        if(words.every(w=>hay.includes(w))){seen.add(i);byname.push([i,null]);}
        if(byname.length>40)break;
      }
    }
  }
  return exact.concat(partial,byname).slice(0,60);
}

function card(idx,hit,q){
  const it=I[idx], ns=(byItem[idx]||[]);
  const nq=norm(q);
  const attrs=[['Бренд',it[2]],['Изготовитель',it[3]],['Узел',it[4]],['Машина',it[5]],['Применение',it[7]]]
    .filter(a=>a[1]).map(a=>`<span class="a"><b>${a[0]}:</b> ${esc(a[1])}</span>`).join('');
  const rows=ns.map(n=>{
    const h=(n[0]===nq)?' class="hit"':'';
    return `<tr${h}><td class="num">${esc(n[1])}</td><td class="${KC[n[2]]}">${KINDS[n[2]]}</td><td>${esc(n[3])}</td></tr>`;
  }).join('');
  const url=SEC[it[6]]||'';
  const reason=hit?`<div class="stat">Найдено по номеру <b>${esc(hit[1])}</b> — ${KINDS[hit[2]]}${hit[3]?' ('+esc(hit[3])+')':''}</div>`:
    `<div class="stat">Найдено по наименованию</div>`;
  return `<div class="card">
    <span class="sec">${url?`<a href="${url}" target="_blank">${esc(it[6])} ↗</a>`:esc(it[6])}</span>
    <div class="kv">${esc(it[0])}</div>
    <div class="nm">${esc(it[1])}</div>
    <div class="attrs">${attrs}</div>
    ${reason}
    <table><tr><th style="width:32%">Номер</th><th style="width:22%">Тип</th><th>Чей</th></tr>${rows}</table>
  </div>`;
}

let t=null;
function run(){
  const q=document.getElementById('q').value.trim();
  const out=document.getElementById('out');
  if(q.length<3){out.innerHTML='<div class="empty">Введите не менее трёх знаков номера или слова из наименования.</div>';return}
  const res=search(q);
  if(!res.length){out.innerHTML=`<div class="empty">По запросу «${esc(q)}» ничего не найдено.<br>
    Проверьте номер: пробелы и дефисы значения не имеют, «3222 1881 41» и «3222188141» — одно и то же.</div>`;return}
  out.innerHTML=`<div class="stat">Найдено деталей: <b>${res.length}</b></div>`+
    res.map(r=>card(r[0],r[1],q)).join('');
}
document.getElementById('q').addEventListener('input',()=>{clearTimeout(t);t=setTimeout(run,120)});
run();
"""
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>КВАНТ · Поиск детали по номеру</title><style>{css}</style></head><body>
<header>
  <h1>КВАНТ · Поиск детали по номеру</h1>
  <div class="sub">Единый вход во все разделы. Работает по любому номеру: нашему, бренда,
  реального изготовителя, аналога или поставщика. Деталей в справочнике: {len(I)} · номеров: {len(N)} · обновлено {date.today().strftime('%d.%m.%Y')}</div>
</header>
<div class="wrap">
  <div class="searchbox">
    <input id="q" type="text" placeholder="Введите номер детали или слово из наименования"
           autocomplete="off" spellcheck="false">
    <div class="hint">Пробелы и дефисы не важны: «3222 1881 41» и «3222188141» — один и тот же номер.
    Примеры: <b>MW21215M</b> · <b>56045189</b> · <b>3222188141</b> · <b>KV-000455-6</b></div>
  </div>
  <div id="out"></div>
</div>
<script>
const I={json.dumps(I, ensure_ascii=False, separators=(',', ':'))};
const N={json.dumps(N, ensure_ascii=False, separators=(',', ':'))};
const SEC={json.dumps(SECTION_URL, ensure_ascii=False)};
{js}
</script></body></html>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT}: {OUT.stat().st_size:,} байт | деталей {len(I)}, номеров {len(N)}".replace(",", " "))


if __name__ == "__main__":
    main()
