#!/usr/bin/env python3
"""Страница «Аудит КП №1763» → zip/public/audit-kp1763.html.

Разбор коммерческого предложения ООО «НПФ ТЕХНОЛОГИЯ» на 16,76 млн ₽ лежал
в zip/data/kp1763_audit.json и в консольном выводе zip/tools/kp1763_audit.py —
на портал не попадал ни в каком виде. Страница собирает оба источника: выводы
и арифметику из модуля, 185 фактов разведки из JSON.

Выход: zip/public/audit-kp1763.html (самодостаточный HTML)
Запуск: python zip/tools/build_audit_page.py — вызывается из zip/build.py
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "public"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kp1763_audit import FINDINGS, FX_ASSUMED, ITEMS, build_mass_table  # noqa: E402
from build_telsmith_page import CSS  # общая вёрстка страниц-разведок  # noqa: E402

e = lambda s: html.escape(str(s if s is not None else ""))
rub = lambda v: f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else "—"


def build():
    d = json.loads((D / "kp1763_audit.json").read_text(encoding="utf-8"))
    blocks = d.get("blocks", [])
    n_facts = sum(len(b.get("findings", [])) for b in blocks)
    mass = {row[0]: row[5] for row in build_mass_table()}

    S = []

    # ── Итог ────────────────────────────────────────────────────────────────
    kpi = f"""<div class="kpi">
<div><b>{rub(d.get('amount_net'))} ₽</b><span>сумма КП без НДС</span></div>
<div><b>{rub(d.get('amount_gross'))} ₽</b><span>к оплате, НДС {int((d.get('vat_rate') or 0)*100)} %</span></div>
<div><b>{len(ITEMS)}</b><span>позиций в спецификации</span></div>
<div><b>{len(blocks)}</b><span>направлений проверки</span></div>
<div><b>{n_facts}</b><span>проверенных фактов</span></div>
</div>"""
    fnd = "".join(
        f'<div class="card"><span class="tag {"no" if f["weight"]=="ключевой" else "wr"}">{e(f["weight"])}</span>'
        f'<b>{e(f["title"])}</b><div class="txt">{e(f["text"])}</div></div>' for f in FINDINGS)
    S.append(("sum", "Итог", f"""{kpi}
<div class="card"><b>Предмет</b><div class="txt">{e(d.get('subject'))}</div>
<div class="mut">Как проверяли: {e(d.get('source'))}</div></div>
<h2>Выводы</h2>{fnd}"""))

    # ── Спецификация ────────────────────────────────────────────────────────
    rows = "".join(
        f'<tr><td class="n">{e(n)}</td><td>{e(pn)}</td><td>{e(kind)}</td><td class="txt">{e(mat)}</td>'
        f'<td class="n">{rub(price)}</td><td class="n">{qty}</td><td class="n">{rub(price*qty)}</td>'
        f'<td class="n">{mass.get(n) or ">200"} кг</td></tr>'
        for n, pn, kind, mat, price, qty in ITEMS)
    S.append(("spec", f"Спецификация · {len(ITEMS)}", f"""
<h2>Что выставлено и какой массе детали это соответствовало бы</h2>
<div class="mut">Последний столбец — обратный счёт: сколько должна весить бронзовая деталь, чтобы
заявленная цена окупалась материалом и переделом (партия 10 шт, курс {FX_ASSUMED:.0f} ₽/USD).
Втулка гидроперфоратора столько не весит — это и есть мера завышения.</div>
<div class="wrap"><table><thead><tr><th>№</th><th>Партномер</th><th>Тип</th><th>Материал</th>
<th>Цена, ₽/шт</th><th>Кол-во</th><th>Сумма, ₽</th><th>Оправданная масса</th></tr></thead>
<tbody>{rows}</tbody></table></div>"""))

    # ── Блоки разведки ──────────────────────────────────────────────────────
    for i, b in enumerate(blocks):
        f_rows = "".join(
            f'<tr><td>{e(f.get("topic"))}</td><td class="txt">{e(f.get("fact"))}</td>'
            f'<td class="n">{e(f.get("figure"))}</td>'
            f'<td><span class="tag {"ok" if f.get("confidence")=="high" else "wr"}">{e(f.get("confidence"))}</span></td>'
            f'<td class="src">{e(f.get("source"))}</td><td class="mut">{e(f.get("date"))}</td></tr>'
            for f in b.get("findings", []))
        S.append((f"b{i}", f'{b.get("label")} · {len(b.get("findings", []))}', f"""
<h2>{e(b.get('label'))}</h2>
<div class="card txt">{e(b.get('summary'))}</div>
<div class="wrap"><table><thead><tr><th>Тема</th><th>Факт</th><th>Цифра</th><th>Уверенность</th>
<th>Источник</th><th>Дата</th></tr></thead><tbody>{f_rows}</tbody></table></div>"""))

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
<title>Аудит КП №1763 — бронзовые втулки и гильзы</title>
<style>{CSS}</style></head><body>
<header>
<div class="mut"><a href="./">← ГШО · рабочая база</a> · <a href="./search.html">поиск детали по номеру</a></div>
<h1>Аудит КП №1763 — бронзовые гильзы и втулки на {rub(d.get('amount_gross'))} ₽</h1>
<div class="sub">{e(d.get('subject'))}. Проверка на {n_facts} фактах по {len(blocks)} направлениям:
кто поставщик, цены на бронзу в РФ, ставки механообработки, альтернативы, аналоги марок,
цены Китая. Составлено {e(d.get('generated'))}.</div>
</header>
<nav>{tabs}</nav><main>{secs}</main>
<script>{js}</script></body></html>"""

    OUT.mkdir(exist_ok=True)
    p = OUT / "audit-kp1763.html"
    p.write_text(doc, encoding="utf-8")
    print(f"zip/public/audit-kp1763.html: {p.stat().st_size:,} байт | блоков {len(blocks)}, фактов {n_facts}")
    return p


if __name__ == "__main__":
    build()
