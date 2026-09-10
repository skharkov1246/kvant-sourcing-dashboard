#!/usr/bin/env python3
"""Страница справочника оборудования для сайта: поиск по артикулу и марке.

Самодостаточный HTML: данные вшиты в страницу, поиск работает без сервера и
без запросов наружу. Кладётся рядом с дашбордом за Cloudflare Access, поэтому
коммерческие цены с неё видны только своим.

Строка справочника отвечает на четыре вопроса сразу: что это за артикул и чья
марка, сколько раз его просили и кто, почём его дают поставщики и почём
продаём мы, и чем кончились сделки, где он был. Этого хватает, чтобы считать
предложение, не поднимая переписку.

    python base/kb_catalog.py --db base/kvant.db     # сперва справочник
    python base/kb_page.py --db base/kvant.db --out public/kb.html
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
from datetime import date
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "templates" / "report.css"
TOP = 8000          # столько строк вшивается в страницу: дальше поиск тормозит


def build(db_path: str, top: int) -> str:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=300)
    # к строке справочника подтягивается самый дешёвый поставщик: это первое,
    # что спрашивают, открыв артикул
    rows = con.execute("""
        WITH best AS (
          SELECT pn_key, supplier, price_med, cur,
                 row_number() OVER (PARTITION BY pn_key ORDER BY price_med) rn
          FROM supplier_prices WHERE price_med > 0)
        SELECT c.pn, c.brand, c.name, c.seg, c.mentions, c.deals, c.won, c.lost, c.cur,
               c.price_min, c.price_med, c.price_max, c.sup_med, c.our_med, c.markup,
               b.supplier, c.customers, c.last_seen, b.price_med, b.cur
        FROM catalog_items c
        LEFT JOIN best b ON b.pn_key = c.pn_key AND b.rn = 1
        ORDER BY c.deals DESC, c.mentions DESC LIMIT ?""", (top,)).fetchall()
    total = con.execute("SELECT count(*) FROM catalog_items").fetchone()[0]
    priced = con.execute("SELECT count(*) FROM catalog_items WHERE price_med IS NOT NULL").fetchone()[0]
    con.close()

    data = [[r[0], r[1] or "", (r[2] or "")[:70], r[3] or "", r[4], r[5], r[6], r[7],
             r[8] or "", r[10], r[12], r[13], r[14], (r[15] or "")[:44], r[18], r[19] or ""]
            for r in rows]
    css = CSS.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Справочник оборудования</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@450;600&display=swap">
<style>{css}
#q{{width:100%; padding:12px 14px; font-size:16px; font-family:inherit; background:var(--surface);
   color:var(--ink); border:1px solid var(--line-strong)}}
#q:focus{{outline:2px solid var(--mark); outline-offset:-1px}}
#cnt{{font-size:12.5px; color:var(--muted); margin:8px 0 14px}}
td.pn{{font-family:"IBM Plex Mono",monospace; font-weight:600; white-space:nowrap}}
tr.w td.res{{color:var(--good)}} tr.l td.res{{color:var(--critical)}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">КВАНТ · база знаний · {date.today().isoformat()}</div>
  <h1>Справочник оборудования</h1>
  <p class="standfirst">{html.escape(f"{total:,}".replace(",", " "))} артикулов, собранных из документов портала:
  спецификаций заказчиков и оферт поставщиков. У {html.escape(f"{priced:,}".replace(",", " "))} из них есть цена.
  Поиск идёт по артикулу, марке и наименованию.</p>
</header>
<section>
<input id="q" type="search" placeholder="артикул, марка или наименование — например NU2216 или Grundfos" autocomplete="off">
<div id="cnt"></div>
<div class="tablewrap"><table>
<thead><tr><th>артикул</th><th>марка</th><th>наименование</th><th class="r">просили</th>
<th class="r">сделок</th><th class="r">исход</th><th>вал.</th><th class="r">медиана</th>
<th class="r">поставщик</th><th class="r">мы</th><th class="r">наценка</th>
<th>дешевле всех</th><th class="r">его цена</th></tr></thead>
<tbody id="t"></tbody></table></div>
<p class="note">Медиана — по всем ценам этого артикула в документах; «поставщик» и «мы» —
медианы по офертам поставщиков и нашим предложениям. Наценка считается только там, где
есть обе цены в одной валюте. «Дешевле всех» — поставщик с наименьшей медианной ценой
по его офертам. Показаны {len(data)} артикулов с наибольшим числом сделок из {total}.</p>
</section>
<footer>Собрано base/kb_catalog.py из base/kvant.db. Полная выгрузка — base/export_kb.py.</footer>
</div>
<script>
const D={payload};
const fmt=n=>n==null?'':(Math.round(n*100)/100).toLocaleString('ru-RU');
const t=document.getElementById('t'), q=document.getElementById('q'), cnt=document.getElementById('cnt');
function draw(list){{
  t.innerHTML=list.slice(0,300).map(r=>{{
    const cls=r[6]>r[7]?'w':(r[7]>r[6]?'l':'');
    return `<tr class="${{cls}}"><td class="pn">${{r[0]}}</td><td>${{r[1]}}</td><td>${{r[2]}}</td>`+
      `<td class="r">${{r[4]}}</td><td class="r">${{r[5]}}</td><td class="r res">${{r[6]}}/${{r[7]}}</td>`+
      `<td>${{r[8]}}</td><td class="r">${{fmt(r[9])}}</td><td class="r">${{fmt(r[10])}}</td>`+
      `<td class="r">${{fmt(r[11])}}</td><td class="r">${{r[12]?r[12]+'×':''}}</td>`+
      `<td>${{r[13]}}</td><td class="r">${{fmt(r[14])}} ${{r[15]}}</td></tr>`;
  }}).join('');
  cnt.textContent=`найдено ${{list.length}}` + (list.length>300?', показаны первые 300':'');
}}
function find(){{
  const s=q.value.trim().toUpperCase().replace(/[^0-9A-ZА-Я]/g,'');
  if(!s) return draw(D);
  draw(D.filter(r=>(r[0]+r[1]+r[2]).toUpperCase().replace(/[^0-9A-ZА-Я]/g,'').includes(s)));
}}
q.addEventListener('input',find); draw(D);
</script></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--out", default="kb.html")
    ap.add_argument("--top", type=int, default=TOP)
    a = ap.parse_args()
    Path(a.out).write_text(build(a.db, a.top), encoding="utf-8")
    print(f"готово: {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
