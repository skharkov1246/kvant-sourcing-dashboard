#!/usr/bin/env python3
"""Карта цепочки портала как страница → zip/public/chain.html.

ЗАЧЕМ. Счётчик заполняемости (scripts/build_chain_coverage.py) считает, где в
портале пусто, но результат лежит только в data/chain_coverage.json — его никто
не видит. Цель из CLAUDE.md («пустое звено важнее улучшения заполненного»)
работает только тогда, когда карта пустых звеньев перед глазами.

ЧТО ПОКАЗЫВАЕТ. Матрицу «направление × звено цепочки»: где сколько записей, из
каких файлов они взяты и где ноль. Ноль здесь — не оформительский прочерк, а
утверждение: данных нет. Клетка без источника подсвечивается отдельно, потому
что непроверяемое число ничем не лучше выдуманного.

Запуск: python scripts/build_chain_page.py — вызывается из zip/build.py
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "zip" / "public" / "chain.html"
sys.path.insert(0, str(ROOT / "zip" / "tools"))
from build_telsmith_page import CSS  # общая вёрстка страниц портала  # noqa: E402

e = lambda s: html.escape(str(s if s is not None else ""))

EXTRA = """
table.chain{table-layout:fixed;font-size:12px}
table.chain th{white-space:normal;vertical-align:bottom}
table.chain td.c{text-align:center;font-variant-numeric:tabular-nums}
td.zero{background:rgba(208,59,59,.10);color:var(--crit,#d03b3b);font-weight:600}
td.has{background:rgba(12,163,12,.08)}
td.draft{background:rgba(250,178,25,.14);color:var(--warn,#a87b06);font-weight:600}
td.c a{color:inherit;text-decoration:underline;text-underline-offset:2px}
td .dr{font-size:10px;font-weight:400;color:var(--warn,#a87b06);white-space:nowrap}
.seg{font-weight:600;text-align:left}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0;font-size:12px;color:var(--ink2)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:5px}
.bar{height:7px;border-radius:4px;background:var(--chip);overflow:hidden;margin-top:4px}
.bar>i{display:block;height:100%;background:var(--s1)}
"""


# Из какой страницы портала пришло число клетки. Без этого карта говорит
# «признак ~20» и не даёт на них посмотреть: цифра есть, дороги к ней нет.
PAGE_OF = {
    "zip/data/diagnostics_recon.json": ("./diag.html", "диагностика и дефекты"),
    "dict/symptom.json": ("./diag.html", "признак → дефект"),
    "zip/data/oem_atlas.json": ("./oem.html", "атлас производителей"),
    "zip/data/repair_recon.json": ("./repair.html", "ремонт и исполнители"),
    "zip/data/subsupplier_recon.json": ("./subs.html", "субпоставщики"),
    "zip/data/dirs_recon.json": ("./dirs.html", "насосы, КИПиА, электротехника"),
    "zip/data/recip_recon.json": ("./recip.html", "поршневые компрессоры"),
    "zip/data/telsmith_3858.json": ("./telsmith.html", "Telsmith 3858"),
}


def cell_link(sources):
    """Первая страница, на которой это число можно посмотреть глазами."""
    for src in sources:
        if src in PAGE_OF:
            return PAGE_OF[src]
    return None


def build():
    d = json.loads((ROOT / "data" / "chain_coverage.json").read_text(encoding="utf-8"))
    links, segs, s = d["links"], d["segments"], d["summary"]

    head = "".join(f'<th>{e(l["title"])}<div class="mut" style="font-weight:400">{e(l["what"])}</div></th>'
                   for l in links)
    rows = ""
    for r in segs:
        cells = ""
        for c in r["cells"]:
            # Три состояния, не два: «есть», «черновик» (собрано, но не проверено)
            # и «пусто». Черновик показан всегда, даже когда рядом есть проверенное:
            # клетка «2» при 147 непроверенных строках читалась бы как «почти пусто».
            draft = c.get("draft", 0)
            cls = "has" if c["n"] else ("draft" if draft else "zero")
            title = e("; ".join(c["sources"])) if c["sources"] else "источников нет"
            if draft:
                title += f" · черновик: {draft} строк без проверки скептиком"
            link = cell_link(c["sources"])
            num = f'{c["n"] if c["n"] else "—"}'
            if link:
                num = f'<a href="{link[0]}" title="{e(link[1])}">{num}</a>'
            cells += (f'<td class="c {cls}" title="{title}">{num}'
                      + (f'<div class="dr">+{draft} черн.</div>' if draft else "")
                      + (f'<div class="mut" style="font-size:10px">{len(c["sources"])} ф.</div>'
                         if c["sources"] and not draft else "")
                      + "</td>")
        rows += (f'<tr><td class="seg">{e(r["title"])}'
                 f'<div class="bar"><i style="width:{r["pct"]}%"></i></div></td>{cells}'
                 f'<td class="c"><b>{r["filled"]}/{r["of"]}</b></td></tr>')

    empty = "".join(f'<div class="card"><b>{e(x["title"])}</b>'
                    f'<div class="txt">{e(x["what"])}</div></div>'
                    for x in d["empty_everywhere"])
    com = d.get("common", {})
    foreign = ""
    if com.get("machines_foreign_segment"):
        foreign = (f'<div class="card"><b>Машины чужого сегмента: '
                   f'{com["machines_foreign_segment"]}</b>'
                   f'<div class="txt">{e(com.get("machines_foreign_note"))}</div></div>')

    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Заполняемость портала — карта цепочки</title>
<style>{CSS}{EXTRA}</style></head><body>
<header>
<div class="mut"><a href="./">← ГШО · рабочая база</a> · <a href="./diag.html">диагностика</a>
 · <a href="./oem.html">производители</a> · <a href="./repair.html">ремонт и исполнители</a>
 · <a href="./subs.html">субпоставщики</a> · <a href="./dirs.html">насосы и КИП</a>
 · <a href="./recip.html">поршневые</a></div>
<h1>Заполняемость портала: где пусто</h1>
<div class="sub">Цель — инженерный портал ремонта и сервиса динамического оборудования.
Пользователь проходит цепочку целиком: машина → узел → признак → дефект → ремонтное решение →
запчасть → изготовитель → исполнитель. Карта показывает, какие звенья по каким направлениям
уже есть, а какие пусты. Обновлено {e(d.get('updated', ''))}.</div>
<div class="kpi">
<div><b>{s['cells_filled']} / {s['cells_total']}</b><span>клеток заполнено</span></div>
<div><b>{s.get('draft_rows', 0)}</b><span>строк черновика ждут проверки</span></div>
<div><b>{s['links_not_started']}</b><span>звеньев не начато нигде</span></div>
<div><b>{s['segments']}</b><span>направлений</span></div>
<div><b>{com.get('machines_total', 0)}</b><span>машин в реестре</span></div>
<div><b>{com.get('oem_keys', 0)}</b><span>производителей в словаре</span></div>
<div><b>{com.get('chain_edges', 0)}</b><span>рёбер «кто кому делает»</span></div>
</div>
</header>
<main>
<div class="card txt">{e(d['note'])}</div>
<div class="card" style="border-left:4px solid var(--warn,#fab219)"><b>Что этот счётчик не видит</b>
<div class="txt">{e(d.get('scope', ''))}</div></div>
<div class="legend">
  <span><i style="background:rgba(12,163,12,.35)"></i>есть данные, под числом — сколько файлов-источников</span>
  <span><i style="background:rgba(250,178,25,.35)"></i>черновик: собрано, но скептиком не проверено — в заполненные не идёт</span>
  <span><i style="background:rgba(208,59,59,.35)"></i>пусто в файлах репозитория — в библиотеке Supabase может быть</span>
</div>
<div class="wrap"><table class="chain"><thead><tr><th style="width:190px">Направление</th>{head}
<th style="width:62px">Итог</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="mut">Наведите на клетку — покажет файлы, из которых взято число;
подчёркнутое число открывает страницу, где эти данные можно посмотреть.
Правило приоритета: {e(d['priority_rule'])}</div>
<h2>Звенья, не начатые ни по одному направлению — в файлах репозитория</h2>{empty or '<div class="card">нет — все звенья где-то начаты</div>'}
{f'<h2>Находки</h2>{foreign}' if foreign else ''}
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc, encoding="utf-8")
    print(f"zip/public/chain.html: {OUT.stat().st_size:,} байт | "
          f"{s['cells_filled']}/{s['cells_total']} клеток, "
          f"звеньев не начато {s['links_not_started']}")
    return OUT


if __name__ == "__main__":
    build()
