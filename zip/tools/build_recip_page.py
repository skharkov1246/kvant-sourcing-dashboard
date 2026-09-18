#!/usr/bin/env python3
"""Страница «Поршневые компрессоры» → zip/public/recip.html.

Направление с подтверждённым спросом и нулевой разведкой: сегмент в
library/segments.py был заведён, а знаний за ним не было ни одного. Страница
выкладывает разведку из zip/data/recip_recon.json — четырнадцать углов,
у десяти собственный скептик.

ГЛАВНОЕ В ВЁРСТКЕ: вердикт скептика стоит рядом с каждым фактом, а не сводкой
внизу. «Скептик не сослался» и «не проверялся» показываются отдельными
значениями: пустое поле читалось бы как «проверено, всё хорошо».

Запуск: python zip/tools/build_recip_page.py — вызывается из zip/build.py
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "public"

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_telsmith_page import CSS  # общая вёрстка страниц-разведок  # noqa: E402

e = lambda s: html.escape(str(s if s is not None else ""))

# Таблица находок широкая: без фиксированной раскладки колонка «Цифра» растягивается
# длинным значением и выдавливает проверку с источником за край экрана.
EXTRA_CSS = """
table.fnd{table-layout:fixed}
table.fnd td{word-break:break-word}
table.fnd td.n{text-align:left;white-space:normal;font-size:11.5px}
"""

# Класс плашки по вердикту. Всё, что не подтверждено прямо, окрашено как
# требующее внимания: молчаливое «серое» состояние здесь опаснее ошибки.
VCLASS = {"подтверждено": "ok", "частично": "wr", "опровергнуто": "no",
          "непроверяемо": "wr", "скептик не сослался": "wr", "не проверялся": "wr"}


def lst(v):
    if not v:
        return ""
    if isinstance(v, str):
        return f'<div class="txt">{e(v)}</div>'
    return "<ul>" + "".join(f"<li>{e(x)}</li>" for x in v) + "</ul>"


def build():
    d = json.loads((D / "recip_recon.json").read_text(encoding="utf-8"))
    st, angles = d["stats"], d["angles"]
    vb = st["by_verdict"]

    kpi = f"""<div class="kpi">
<div><b>{st['angles']}</b><span>углов разведки</span></div>
<div><b>{st['with_skeptic']}</b><span>из них со скептиком</span></div>
<div><b>{st['findings']}</b><span>фактов с источником</span></div>
<div><b>{st['companies']}</b><span>карточек компаний</span></div>
<div><b>{vb.get('подтверждено', 0)}</b><span>подтверждено проверкой</span></div>
<div><b>{vb.get('опровергнуто', 0)}</b><span>опровергнуто</span></div>
<div><b>{st['dead_ends']}</b><span>закрытых тупиков</span></div>
</div>"""

    vrows = "".join(
        f'<tr><td><span class="tag {VCLASS.get(k, "wr")}">{e(k)}</span></td>'
        f'<td class="n">{v}</td><td class="n">{round(100 * v / st["findings"])} %</td></tr>'
        for k, v in sorted(vb.items(), key=lambda x: -x[1]))

    gaps = "".join(
        f'<div class="card"><b>{e(g.get("title"))}</b>'
        f'<div class="txt"><b>Почему важно:</b> {e(g.get("why"))}</div>'
        f'<div class="txt"><b>Что искать:</b> {e(g.get("task"))}</div></div>'
        for g in (d.get("critic") or {}).get("gaps", []))

    S = [("sum", "Итог", f"""{kpi}
<div class="card txt">{e(d['method'])}</div>
<h2>Чем кончилась проверка</h2>
<div class="wrap"><table><thead><tr><th>Вердикт скептика</th><th>Фактов</th><th>Доля</th></tr></thead>
<tbody>{vrows}</tbody></table></div>
<div class="card"><b>Как читать</b><div class="txt">Вердикт стоит рядом с каждым фактом, а не сводкой
внизу. «Скептик не сослался» означает, что угол проверялся, но именно этот факт скептик не разобрал —
считать его подтверждённым нельзя. «Не проверялся» означает угол добора, запущенный без скептика.
Ни одно из этих состояний не равно «всё хорошо».</div></div>
{f'<h2>Что критик полноты назвал недостающим</h2>{gaps}' if gaps else ''}""")]

    for a in angles:
        rows = "".join(
            f'<tr><td>{e(x.get("topic"))}</td><td class="txt">{e(x.get("fact"))}'
            + (f'<div class="mut"><b>Поправка скептика:</b> {e(x["correction"])}</div>' if x.get("correction") else "")
            + f'</td><td class="n">{e(x.get("figure"))}</td>'
              f'<td><span class="tag {VCLASS.get(x.get("verdict"), "wr")}">{e(x.get("verdict"))}</span>'
            + (f'<div class="mut">{e(x["verdict_why"])[:400]}</div>' if x.get("verdict_why") else "")
            + f'</td><td class="src">{e(x.get("source"))}'
            + (f'<div><a href="{e(x["url"])}" target="_blank" rel="noopener">{e(x["url"])[:60]}</a></div>' if x.get("url") else "")
            + (f'<div class="mut">лучше: {e(x["better_source"])[:120]}</div>' if x.get("better_source") else "")
            + "</td></tr>"
            for x in a["findings"])
        comps = "".join(
            f'<div class="card"><b>{e(c.get("name"))}</b> '
            f'<span class="tag">{e(c.get("kind"))}</span> <span class="mut">{e(c.get("country"))}</span>'
            f'<div class="txt">{e(c.get("makes"))}</div>'
            f'<div class="txt"><b>Доказательство:</b> {e(c.get("evidence"))}</div>'
            f'<div class="mut">уверенность: {e(c.get("confidence"))} '
            + (f'· <a href="{e(c["site"])}" target="_blank" rel="noopener">{e(c["site"])}</a>' if c.get("site") else "")
            + "</div></div>"
            for c in a.get("companies", []))
        skept = ('<span class="tag ok">скептик был</span>' if a["skeptic"]
                 else '<span class="tag wr">угол добора, скептика не было</span>')
        S.append((a["key"], f'{a["title"]} · {len(a["findings"])}', f"""
<h2>{e(a['title'])}</h2>{skept}
<div class="card txt">{e(a['summary'])}</div>
{f'<div class="card"><b>Оценка скептика по углу</b><div class="txt">{e(a["overall"])}</div></div>' if a.get('overall') else ''}
<div class="wrap"><table class="fnd"><colgroup><col style="width:13%"><col style="width:42%">
<col style="width:9%"><col style="width:20%"><col style="width:16%"></colgroup>
<thead><tr><th>Тема</th><th>Факт</th><th>Цифра</th>
<th>Проверка</th><th>Источник</th></tr></thead><tbody>{rows}</tbody></table></div>
{f'<h3>Компании — {len(a["companies"])}</h3>{comps}' if comps else ''}
{f'<h3>Закрытые тупики — искали и не нашли</h3>{lst(a["dead_ends"])}' if a.get('dead_ends') else ''}
{f'<h3>Что осталось неясным</h3>{lst(a["gaps"])}' if a.get('gaps') else ''}
{f'<h3>Чего скептик недосчитался в угле</h3>{lst(a["missing"])}' if a.get('missing') else ''}"""))

    tabs = "".join(f'<button data-s="{sid}"{" class=on" if i == 0 else ""}>{e(t)}</button>'
                   for i, (sid, t, _) in enumerate(S))
    secs = "".join(f'<section id="{sid}"{" class=on" if i == 0 else ""}>{h}</section>'
                   for i, (sid, _, h) in enumerate(S))
    js = """document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id===b.dataset.s));
  window.scrollTo({top:0});});"""

    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Поршневые компрессоры — разведка направления</title>
<style>{CSS}{EXTRA_CSS}</style></head><body>
<header>
<div class="mut"><a href="./">← ГШО · рабочая база</a> · <a href="./search.html">поиск детали по номеру</a></div>
<h1>Поршневые компрессоры — разведка направления</h1>
<div class="sub">{e(d['subject'])}. {st['angles']} углов, {st['findings']} фактов с источником,
{st['companies']} компаний, {st['dead_ends']} закрытых тупиков. Собрано {e(d['generated'])}.
Вердикт проверки стоит рядом с каждым фактом.</div>
</header>
<nav>{tabs}</nav><main>{secs}</main>
<script>{js}</script></body></html>"""
    OUT.mkdir(exist_ok=True)
    p = OUT / "recip.html"
    p.write_text(doc, encoding="utf-8")
    print(f"zip/public/recip.html: {p.stat().st_size:,} байт | углов {st['angles']}, "
          f"фактов {st['findings']}, компаний {st['companies']}")
    return p


if __name__ == "__main__":
    build()
