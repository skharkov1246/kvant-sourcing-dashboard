#!/usr/bin/env python3
"""Страница ГШО «Щековая дробилка Telsmith 3858» → zip/public/telsmith.html.

Разведка по машине лежала в шести файлах zip/data, а на портал попадала только
печатным отчётом /orders/telsmith-sourcing.pdf. Три набора не публиковались вовсе:
вердикты по заводам (telsmith_crossrefs.verdicts), доказательства изготовления
(telsmith_nodrawings.made) и углы разведки (telsmith_crossrefs.angle_findings).
Эта страница выкладывает всё разобранное одним экраном с вкладками.

Вход (zip/data): telsmith_3858, telsmith_prices, telsmith_recon,
                 telsmith_suppliers, telsmith_brands, telsmith_crossrefs,
                 telsmith_nodrawings
Выход: zip/public/telsmith.html  (самодостаточный HTML, данные внутри)
Запуск: python zip/tools/build_telsmith_page.py   — вызывается из zip/build.py
"""
import html
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "public"

e = lambda s: html.escape(str(s if s is not None else ""))
J = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def load(name, default=None):
    p = D / name
    if not p.exists():
        return default if default is not None else {}
    return json.loads(p.read_text(encoding="utf-8"))


def num(v, suf=""):
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v:,.0f}".replace(",", " ") + suf


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--mut:#898781;
--grid:#e1e0d9;--border:rgba(11,11,11,.10);--s1:#2a78d6;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--chip:#eef2f7}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;
--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--mut:#898781;--grid:#2c2c2a;
--border:rgba(255,255,255,.10);--s1:#3987e5;--chip:#262624}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--mut:#898781;--grid:#2c2c2a;--border:rgba(255,255,255,.10);--s1:#3987e5;--chip:#262624}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--s1);text-decoration:none}a:hover{text-decoration:underline}
header{padding:16px 22px 0;max-width:1320px}
h1{font-size:21px;margin:0 0 3px}
.sub{color:var(--ink2);font-size:12.5px}
nav{position:sticky;top:0;z-index:5;background:var(--page);display:flex;gap:4px;flex-wrap:wrap;
padding:10px 22px;border-bottom:1px solid var(--grid);margin-top:12px}
nav button{border:1px solid var(--border);background:var(--surface);color:var(--ink2);padding:6px 12px;
border-radius:18px;font:600 12.5px system-ui;cursor:pointer}
nav button.on{background:var(--s1);border-color:var(--s1);color:#fff}
main{padding:16px 22px 48px;max-width:1320px}
section{display:none}section.on{display:block}
h2{font-size:16px;margin:20px 0 8px}h3{font-size:13.5px;margin:16px 0 6px;color:var(--ink2)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0 4px}
.kpi div{border:1px solid var(--border);background:var(--surface);border-radius:10px;padding:9px 12px}
.kpi b{display:block;font-size:19px;line-height:1.2}
.kpi span{color:var(--mut);font-size:11.5px}
.card{border:1px solid var(--border);background:var(--surface);border-radius:10px;padding:11px 13px;margin:8px 0}
.card>b{display:block;margin-bottom:4px}
.mut{color:var(--mut);font-size:11.5px}
.txt{white-space:pre-wrap;font-size:12.5px;line-height:1.5;color:var(--ink2)}
table{border-collapse:collapse;width:100%;margin:6px 0;font-size:12px}
th,td{border:1px solid var(--grid);padding:4px 7px;text-align:left;vertical-align:top}
th{background:var(--chip);cursor:pointer;white-space:nowrap}
td.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tbody tr:hover{background:var(--chip)}
.bar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:8px 0}
.bar input,.bar select{border:1px solid var(--border);background:var(--surface);color:var(--ink);
padding:5px 9px;border-radius:7px;font:13px system-ui}
.bar input{min-width:240px}
.bar label{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
ul{margin:4px 0 4px 18px;padding:0;font-size:12.5px;color:var(--ink2)}li{margin:2px 0}
.btn{border:1px solid var(--border);background:var(--surface);color:var(--ink2);padding:5px 11px;
border-radius:7px;font:600 12.5px system-ui;cursor:pointer}
.tag{display:inline-block;border-radius:20px;padding:1px 8px;font-size:11px;background:var(--chip);color:var(--ink2)}
.ok{background:#e4f6e4;color:#08610a}.no{background:#fbe6e6;color:#8d1c1c}.wr{background:#fdf3dc;color:#7a5406}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .ok{background:#123d13;color:#8fe08f}
:root:where(:not([data-theme="light"])) .no{background:#4a1414;color:#ff9d9d}
:root:where(:not([data-theme="light"])) .wr{background:#463307;color:#ffd479}}
.wrap{overflow-x:auto}
.src{font-size:11px;color:var(--mut);word-break:break-word}
@media(max-width:700px){header,nav,main{padding-left:12px;padding-right:12px}.bar input{min-width:150px}}
"""


MACH_LABEL = {
    "name": "Модель", "type": "Тип и типоразмер", "oem": "Держатель чертежей",
    "status": "Состояние производства", "series_note": "Принадлежность к серии",
    "search_key": "По какому ключу искать", "site": "Где работает", "channel": "Действующий канал поставки",
}


def facts_table(rows, cols):
    """cols: [(ключ, заголовок, класс)]"""
    th = "".join(f"<th>{e(t)}</th>" for _, t, _ in cols)
    tr = []
    for r in rows:
        td = []
        for k, _, cl in cols:
            v = r.get(k)
            if isinstance(v, list):
                v = "; ".join(str(x) for x in v)
            td.append(f'<td class="{cl}">{e(v) if v else "—"}</td>')
        tr.append("<tr>" + "".join(td) + "</tr>")
    return f'<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(tr)}</tbody></table></div>'


def lst(v):
    """Список пунктов → маркированный список; строка → абзац."""
    if not v:
        return ""
    if isinstance(v, str):
        return f'<div class="txt">{e(v)}</div>'
    return "<ul>" + "".join(f"<li>{e(x)}</li>" for x in v) + "</ul>"


def join(v):
    return ", ".join(str(x) for x in v) if isinstance(v, list) else (v or "—")


def urls(u):
    if not u:
        return ""
    if isinstance(u, str):
        u = [u]
    return '<div class="src">' + " · ".join(f'<a href="{e(x)}" target="_blank" rel="noopener">{e(x)[:70]}</a>' for x in u) + "</div>"


def build():
    d = load("telsmith_3858.json")
    pr = load("telsmith_prices.json", {"basis": [], "estimates": [], "fx_rub_usd": 0})
    rc = load("telsmith_recon.json", {"items": [], "legs": {}, "summary": ""})
    sp = load("telsmith_suppliers.json", {"suppliers": [], "crossrefs": [], "geom": [], "plan": {}, "notes": []})
    br = load("telsmith_brands.json", {"brands": [], "synth": {}})
    cr = load("telsmith_crossrefs.json", {"crossrefs": [], "verdicts": [], "angle_findings": []})
    nd = load("telsmith_nodrawings.json", {"made": []})

    m, st = d.get("machine", {}), d.get("stats", {})
    need, catalog, classes = d.get("need", []), d.get("catalog", []), d.get("classes", [])
    verdicts = cr.get("verdicts", [])
    made = nd.get("made", [])
    angles = cr.get("angle_findings", [])
    crossrefs = cr.get("crossrefs") or sp.get("crossrefs", [])

    made_n = sum(len(s.get("companies", [])) for s in made)
    S = []                                   # секции: (id, подпись, html)

    # ── 1. Машина ───────────────────────────────────────────────────────────
    kpi = f"""<div class="kpi">
<div><b>{num(st.get('need_positions'))}</b><span>позиций потребности</span></div>
<div><b>{(st.get('need_sum_rub') or 0)/1e6:,.1f} млн ₽</b><span>по ценам действующего канала</span></div>
<div><b>{num(st.get('catalog_rows'))}</b><span>строк каталога ЗИП</span></div>
<div><b>{num(st.get('catalog_nodes'))}</b><span>узлов изделия</span></div>
<div><b>{len(verdicts)}</b><span>вердиктов по заводам</span></div>
<div><b>{made_n}</b><span>компаний с доказательством изготовления</span></div>
<div><b>{len(crossrefs)}</b><span>кросс-референсов PN</span></div>
<div><b>{pr.get('fx_rub_usd','—')}</b><span>₽/USD, базис расчёта</span></div>
</div>""".replace(",", " ")
    mach = "".join(f'<div class="card"><b>{e(MACH_LABEL.get(k, k))}</b><div class="txt">{e(v)}</div></div>'
                   for k, v in m.items() if isinstance(v, str))
    cls_rows = "".join(
        f'<tr><td>{e(c.get("title") or c.get("key"))}</td><td class="n">{num(c.get("positions"))}</td>'
        f'<td class="n">{num(c.get("sum_rub"))}</td></tr>' for c in classes)
    S.append(("mach", "Машина", f"""{kpi}
<h2>Что это за машина</h2>{mach}
<h2>Классы номенклатуры</h2>
<div class="wrap"><table><thead><tr><th>Класс</th><th>Позиций</th><th>Сумма, ₽</th></tr></thead>
<tbody>{cls_rows}</tbody></table></div>"""))

    # ── 2. Потребность ──────────────────────────────────────────────────────
    S.append(("need", f"Потребность · {len(need)}", """
<h2>Потребность рудника: что закупается и по какой цене сегодня</h2>
<div class="mut">Цены — действующего канала поставки (прейскурант дилера). Это база сравнения:
столько стоит позиция сейчас, до выхода на независимый рынок.</div>
<div class="bar">
  <input id="qn" placeholder="поиск: номер, наименование, узел…">
  <select id="fnode"></select><select id="fcls"></select>
  <label class="mut"><input type="checkbox" id="fcat"> только те, что есть в каталоге</label>
  <button class="btn" id="csvn">⬇ CSV</button><span class="mut" id="sumn"></span>
</div>
<div class="wrap"><table id="tn"><thead><tr>
<th data-k="no">№</th><th data-k="oem">Номер</th><th data-k="name">Наименование</th>
<th data-k="node">Узел</th><th data-k="class">Класс</th><th data-k="qty">Кол-во</th>
<th data-k="price_rub">Цена, ₽</th><th data-k="sum_rub">Сумма, ₽</th>
<th data-k="lead_days">Срок, дн</th><th data-k="pn_kind">Тип номера</th></tr></thead>
<tbody></tbody></table></div>"""))

    # ── 3. Каталог ──────────────────────────────────────────────────────────
    S.append(("cat", f"Каталог узлов · {len(catalog)}", """
<h2>Состав изделия по каталогу запасных частей</h2>
<div class="mut">Разбор каталога заказчика: позиция, номер Telsmith, наименование, количество на машину, узел, страница.</div>
<div class="bar"><input id="qc" placeholder="поиск: номер, наименование, узел…">
<select id="fnode2"></select><button class="btn" id="csvc">⬇ CSV</button><span class="mut" id="sumc"></span></div>
<div class="wrap"><table id="tc"><thead><tr>
<th data-k="poz">Поз.</th><th data-k="oem">Номер</th><th data-k="name">Наименование</th>
<th data-k="qty">Кол-во</th><th data-k="node">Узел</th><th data-k="page">Стр.</th></tr></thead>
<tbody></tbody></table></div>"""))

    # ── 4. Цены аутмаркета ──────────────────────────────────────────────────
    est = []
    for x in pr.get("estimates", []):
        rows = "".join(
            f'<tr><td>{e(i.get("pn"))}</td><td>{e(i.get("name"))}<div class="mut">{e(i.get("identified_as"))}</div></td>'
            f'<td class="n">{num(i.get("unit_usd_low"))}–{num(i.get("unit_usd_high"))}</td>'
            f'<td class="n">{num(i.get("delivered_usd"))}</td>'
            f'<td><span class="tag {"ok" if i.get("confidence")=="high" else "wr"}">{e(i.get("confidence"))}</span></td>'
            f'<td class="txt">{e(i.get("basis"))}{urls(i.get("urls"))}</td></tr>'
            for i in x.get("items", []))
        est.append(f'<h3>Класс «{e(x.get("cls"))}»</h3><div class="card txt">{e(x.get("method"))}</div>'
                   f'<div class="wrap"><table><thead><tr><th>Номер</th><th>Позиция</th><th>USD/шт</th>'
                   f'<th>С доставкой</th><th>Уверенность</th><th>Основание</th></tr></thead><tbody>{rows}</tbody></table></div>')
    basis = "".join(
        f'<h3>{e(b.get("topic"))}</h3>' + facts_table(b.get("values", []),
                                                      [("item", "Показатель", ""), ("value", "Значение", "n"),
                                                       ("basis", "Основание", "txt"), ("url", "Источник", "src")])
        for b in pr.get("basis", []))
    S.append(("price", f"Цены аутмаркета · {sum(len(x.get('items',[])) for x in pr.get('estimates',[]))}", f"""
<h2>Сколько это стоит на независимом рынке</h2>
<div class="card txt">{e(pr.get('method'))}</div>{''.join(est)}
<h2>Базис расчёта</h2>{basis}"""))

    # ── 5. Сходимость ───────────────────────────────────────────────────────
    it = "".join(
        f'<tr><td>{e(i.get("pn"))}</td><td>{e(i.get("name"))}</td>'
        f'<td class="n">{num(i.get("bottom_up_usd"))}</td><td class="n">{num(i.get("top_down_usd"))}</td>'
        f'<td class="n">{num(i.get("take_usd"))}</td>'
        f'<td><span class="tag {"ok" if i.get("converges") else "no"}">{"сошлось" if i.get("converges") else "не сошлось"}</span></td>'
        f'<td class="txt">{e(i.get("spread"))}</td><td class="txt">{e(i.get("dig"))}</td></tr>'
        for i in rc.get("items", []))
    legs = ""
    for key, title in (("market", "Рыночный трек: котировки аутмаркета"), ("cost", "Расчётный трек: калькуляция по переделам")):
        for blk in (rc.get("legs", {}) or {}).get(key, []):
            rows = "".join(
                f'<tr><td>{e(i.get("pn"))}</td><td class="txt">{e(i.get("analog"))}</td>'
                f'<td class="n">{num(i.get("price_usd_low"))}–{num(i.get("price_usd_high"))}</td></tr>'
                for i in blk.get("items", []))
            legs += (f'<h3>{title} — {e(blk.get("scope"))}</h3>'
                     f'<div class="wrap"><table><thead><tr><th>Номер</th><th>Чем считали</th><th>USD</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>'
                     + (f'<div class="card txt">{e(blk.get("notes"))}</div>' if blk.get("notes") else ""))
    S.append(("recon", f"Сходимость · {len(rc.get('items',[]))}", f"""
<h2>Встречная проверка: калькуляция снизу против рынка сверху</h2>
<div class="card txt">{e(rc.get('summary'))}</div>
<div class="wrap"><table><thead><tr><th>Номер</th><th>Позиция</th><th>Снизу, USD</th><th>Сверху, USD</th>
<th>Принято</th><th>Итог</th><th>Расхождение</th><th>Что копать</th></tr></thead><tbody>{it}</tbody></table></div>
{legs}"""))

    # ── 6. Кросс-референсы ──────────────────────────────────────────────────
    S.append(("cross", f"Кросс-референсы · {len(crossrefs)}", f"""
<h2>Номер Telsmith → кто это делает на самом деле</h2>
<div class="mut">Что за номером внутреннего каталога Telsmith стоит серийное изделие стороннего изготовителя —
и по какому номеру его можно купить без OEM-канала.</div>
{facts_table(crossrefs, [("telsmith_pn","Номер Telsmith",""),("real_maker","Настоящий изготовитель","txt"),
                         ("real_pn","Настоящий номер / исполнение","txt"),("confidence","Уверенность",""),
                         ("evidence_url","Доказательство","src")])}"""))

    # ── 7. Вердикты по заводам (не публиковалось) ───────────────────────────
    vv = []
    for v in verdicts:
        vd = (v.get("verdict") or "").lower()
        cl = "ok" if "подтверждён как изготовитель" in vd and "не подтверждён" not in vd else ("no" if "не подтверждён" in vd or "трейдер" in vd else "wr")
        vv.append(f"""<div class="card"><b>{e(v.get('name'))}</b>
<span class="tag {cl}">{e(v.get('verdict'))}</span>
<h3>Реальное производство</h3><div class="txt">{e(v.get('real_production'))}</div>
<h3>Опыт по Telsmith</h3><div class="txt">{e(v.get('telsmith_experience'))}</div>
<h3>Риски</h3>{lst(v.get('risks'))}
<h3>Что делаем</h3><div class="txt">{e(v.get('recommend'))}</div>
<div class="mut">Классы: {e(join(v.get('covers_classes')))} · контакты: {"найдены" if v.get('contacts_ok') else "не найдены"}</div></div>""")
    S.append(("verd", f"Заводы: вердикты · {len(verdicts)}", f"""
<h2>Проверка заводов: кто изготовитель, а кто витрина</h2>
<div class="mut">Каждая компания проверена отдельно: есть ли собственные переделы, подтверждается ли опыт
по Telsmith, что с контактами и в какую волну запроса её ставить.</div>
<div class="bar"><input id="qv" placeholder="поиск по названию, вердикту, риску…"><span class="mut" id="cntv"></span></div>
<div id="listv">{''.join(vv)}</div>"""))

    # ── 8. Кто уже изготавливал (не публиковалось) ──────────────────────────
    mm = []
    for sc in made:
        rows = "".join(f"""<div class="card"><b>{e(c.get('name'))}</b>
<span class="tag {'ok' if 'подтверждено' in (c.get('proof_level') or '') else 'wr'}">{e(c.get('proof_level'))}</span>
<div class="txt">{e(c.get('proof'))}</div>
<div class="mut">Делал: {e(c.get('models_made'))} · оснастка: {e(c.get('has_tooling'))}</div>
<div class="mut">{e(c.get('email'))} {e(c.get('phone'))} {e(c.get('site'))}</div>{urls(c.get('urls'))}</div>"""
                       for c in sc.get("companies", []))
        mm.append(f'<h3>{e(sc.get("scope"))} — {len(sc.get("companies", []))} компаний</h3>{rows}')
    S.append(("made", f"Кто уже изготавливал · {made_n}", f"""
<h2>Доказательства изготовления: кто физически делал эти детали</h2>
<div class="mut">Работа без чертежей упирается в один вопрос — держал ли завод эту деталь в руках.
Здесь собраны следы: каталожные позиции с прямым указанием модели, отгрузки, оснастка.</div>
{''.join(mm)}"""))

    # ── 9. Углы разведки (не публиковалось) ─────────────────────────────────
    aa = "".join(f'<div class="card"><b>{e(a.get("angle"))}</b><div class="txt">{e(a.get("findings"))}</div></div>'
                 for a in angles)
    S.append(("angle", f"Углы разведки · {len(angles)}", f"""
<h2>С каких сторон заходили и что нашли</h2>
<div class="mut">Разведка шла параллельными углами. Здесь — что дал каждый: где номер подтвердился,
где рынок пуст, какие тупики закрыты и не требуют повторного захода.</div>{aa}"""))

    # ── 10. Работа без чертежей ─────────────────────────────────────────────
    geo = "".join(f'<h3>{e(g.get("topic"))} — {len(g.get("facts", []))} фактов</h3>'
                  + (f'<div class="card txt">{e(g.get("method"))}</div>' if g.get("method") else "")
                  + facts_table(g.get("facts", []), [("item", "Что", ""), ("value", "Значение", "txt"),
                                                     ("source", "Источник", "txt"), ("url", "Ссылка", "src")])
                  for g in sp.get("geom", []))
    plan = "".join(f'<div class="card"><b>{e(c.get("cls"))}</b>'
                   f'<div class="txt"><b>Выполнимо:</b> {e(c.get("feasible"))}</div>'
                   f'<div class="txt"><b>Маршрут:</b> {e(c.get("route"))}</div>'
                   f'<div class="txt"><b>Размеры, которые нельзя упустить:</b></div>{lst(c.get("critical_dims"))}'
                   f'<div class="txt"><b>Размеры, которые прощают:</b></div>{lst(c.get("forgiving_dims"))}'
                   f'<div class="txt"><b>Что спросить у завода:</b></div>{lst(c.get("ask_supplier"))}'
                   f'<div class="txt"><b>Риск:</b> {e(c.get("risk"))}</div></div>'
                   for c in (sp.get("plan", {}) or {}).get("classes", []))
    S.append(("geom", "Без чертежей", f"""
<h2>Как заказывать, когда чертежей нет</h2>
<div class="card txt">{e((sp.get('plan',{}) or {}).get('summary'))}</div>
<h2>План по классам</h2>{plan}
<h2>Восстановление геометрии по фактам</h2>{geo}"""))

    # ── 11. Поставщики ──────────────────────────────────────────────────────
    ss = "".join(f"""<div class="card"><b>{e(s.get('name'))}</b>
<span class="tag">{e(s.get('kind'))}</span> <span class="mut">{e(s.get('city'))}</span>
<div class="txt">{e(s.get('production'))}</div>
<div class="txt"><b>Доказательство:</b> {e(s.get('evidence'))}</div>
<div class="txt"><b>Риски:</b></div>{lst(s.get('risks'))}
<div class="txt"><b>Что делаем:</b> {e(s.get('recommend'))}</div>
<div class="mut">Классы: {e(join(s.get('covers_classes')))}</div>
<div class="mut">{e(s.get('email'))} {e(s.get('phone'))} {e(s.get('whatsapp'))}
{f'<a href="{e(s.get("site"))}" target="_blank" rel="noopener">{e(s.get("site"))}</a>' if s.get('site') else ''}</div></div>"""
                 for s in sp.get("suppliers", []))
    S.append(("sup", f"Поставщики · {len(sp.get('suppliers',[]))}", f"""
<h2>Кого запрашивать</h2>
<div class="bar"><input id="qs" placeholder="поиск: название, класс, город…"><span class="mut" id="cnts"></span></div>
<div id="lists">{ss}</div>"""))

    # ── 12. Имена машины ────────────────────────────────────────────────────
    bb = ""
    for b in br.get("brands", []):
        rows = "".join(f"""<div class="card"><b>{e(i.get('brand'))} — {e(i.get('model'))}</b>
<div class="txt"><b>Связь:</b> {e(i.get('relation'))}</div>
<div class="txt"><b>Доказательство:</b> {e(i.get('evidence'))}</div>
<div class="txt"><b>Система номеров:</b> {e(i.get('parts_system'))}</div>
<div class="txt"><b>Рынок:</b> {e(i.get('market'))}</div>
<div class="mut">Уверенность: {e(i.get('confidence'))}</div>{urls(i.get('urls'))}</div>"""
                       for i in b.get("identities", []))
        bb += f'<h3>{e(b.get("scope"))} — {len(b.get("identities", []))} проверок</h3>{rows}'
    ch = "".join(f'<div class="card"><b>{e(c.get("channel"))}</b><div class="txt">{e(c.get("what_to_do"))}</div></div>'
                 for c in (br.get("synth", {}) or {}).get("channels", []))
    S.append(("brand", "Имена и каналы", f"""
<h2>Под какими ещё именами выпускалась машина</h2>
<div class="card txt">{e((br.get('synth',{}) or {}).get('verdict'))}</div>
<h2>Каналы снабжения</h2>{ch}
<h2>Проверенные версии</h2>{bb}"""))

    tabs = "".join(f'<button data-s="{sid}"{" class=on" if i == 0 else ""}>{e(t)}</button>'
                   for i, (sid, t, _) in enumerate(S))
    secs = "".join(f'<section id="{sid}"{" class=on" if i == 0 else ""}>{h}</section>'
                   for i, (sid, _, h) in enumerate(S))

    js = """
const NEED=__NEED__,CAT=__CAT__;
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=v=>typeof v==="number"?v.toLocaleString("ru-RU",{maximumFractionDigits:0}):(v==null?"—":v);
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id===b.dataset.s));
  window.scrollTo({top:0});});
function opts(sel,vals,label){sel.innerHTML='<option value="">'+label+'</option>'+
  [...new Set(vals.filter(Boolean))].sort().map(v=>`<option>${esc(v)}</option>`).join("");}
// потребность
let sortN={k:"sum_rub",d:-1};
function rowsN(){const q=$("qn").value.toLowerCase(),nd=$("fnode").value,cl=$("fcls").value,oc=$("fcat").checked;
  let r=NEED.filter(x=>(!nd||x.node===nd)&&(!cl||x.class===cl)&&(!oc||x.in_catalog)&&
    (!q||[x.oem,x.name,x.node,x.eid,x.ens].join(" ").toLowerCase().includes(q)));
  r.sort((a,b)=>{const A=a[sortN.k],B=b[sortN.k];return (A>B?1:A<B?-1:0)*sortN.d;});return r;}
function drawN(){const r=rowsN();
  $("tn").tBodies[0].innerHTML=r.map(x=>`<tr><td class="n">${x.no}</td><td>${esc(x.oem)}</td>
   <td>${esc(x.name)}${x.in_catalog?' <span class="tag ok">в каталоге</span>':''}</td>
   <td>${esc(x.node)}</td><td>${esc(x.class)}</td><td class="n">${fmt(x.qty)}</td>
   <td class="n">${fmt(x.price_rub)}</td><td class="n">${fmt(x.sum_rub)}</td>
   <td class="n">${fmt(x.lead_days)}</td><td>${esc(x.pn_kind)}</td></tr>`).join("");
  $("sumn").textContent=`${r.length} позиций · ${(r.reduce((s,x)=>s+(x.sum_rub||0),0)/1e6).toFixed(1)} млн ₽`;}
// каталог
let sortC={k:"poz",d:1};
function rowsC(){const q=$("qc").value.toLowerCase(),nd=$("fnode2").value;
  let r=CAT.filter(x=>(!nd||x.node===nd)&&(!q||[x.oem,x.name,x.node,x.eid].join(" ").toLowerCase().includes(q)));
  r.sort((a,b)=>{const A=a[sortC.k],B=b[sortC.k];return (A>B?1:A<B?-1:0)*sortC.d;});return r;}
function drawC(){const r=rowsC();
  $("tc").tBodies[0].innerHTML=r.map(x=>`<tr><td class="n">${esc(x.poz)}</td><td>${esc(x.oem)}</td>
   <td>${esc(x.name)}</td><td class="n">${fmt(x.qty)}</td><td>${esc(x.node)}</td>
   <td class="n">${esc(x.page)}</td></tr>`).join("");
  $("sumc").textContent=`${r.length} строк`;}
function csv(name,rows,cols){const q=v=>'"'+String(v==null?"":v).replace(/"/g,'""')+'"';
  const b=new Blob(["\\ufeff"+[cols.join(";"),...rows.map(r=>cols.map(c=>q(r[c])).join(";"))].join("\\n")],
    {type:"text/csv;charset=utf-8"});
  const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=name;a.click();}
opts($("fnode"),NEED.map(x=>x.node),"Узел: все");
opts($("fcls"),NEED.map(x=>x.class),"Класс: все");
opts($("fnode2"),CAT.map(x=>x.node),"Узел: все");
["qn","fnode","fcls","fcat"].forEach(id=>$(id).oninput=drawN);
["qc","fnode2"].forEach(id=>$(id).oninput=drawC);
$("tn").querySelectorAll("th[data-k]").forEach(t=>t.onclick=()=>{
  const k=t.dataset.k;sortN={k,d:sortN.k===k?-sortN.d:-1};drawN();});
$("tc").querySelectorAll("th[data-k]").forEach(t=>t.onclick=()=>{
  const k=t.dataset.k;sortC={k,d:sortC.k===k?-sortC.d:1};drawC();});
$("csvn").onclick=()=>csv("telsmith_need.csv",rowsN(),
  ["no","eid","ens","oem","name","node","class","qty","price_rub","sum_rub","lead_days","pn_kind","in_catalog"]);
$("csvc").onclick=()=>csv("telsmith_catalog.csv",rowsC(),["poz","eid","oem","name","qty","node","page"]);
drawN();drawC();
// живой поиск по карточкам
function cards(inp,list,cnt){const f=()=>{const q=$(inp).value.toLowerCase();let n=0;
  $(list).querySelectorAll(":scope > .card").forEach(c=>{
    const hit=!q||c.textContent.toLowerCase().includes(q);c.style.display=hit?"":"none";if(hit)n++;});
  $(cnt).textContent=n+" из "+$(list).querySelectorAll(":scope > .card").length;};$(inp).oninput=f;f();}
cards("qv","listv","cntv");cards("qs","lists","cnts");
"""
    js = js.replace("__NEED__", J(need)).replace("__CAT__", J(catalog))

    html_doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telsmith 3858 — щековая дробилка: потребность, цены, заводы</title>
<style>{CSS}</style></head><body>
<header>
<div class="mut"><a href="./">← ГШО · рабочая база</a> · <a href="./orders/telsmith-sourcing.pdf">печатный отчёт (PDF)</a>
 · <a href="./search.html">поиск детали по номеру</a></div>
<h1>Щековая дробилка Telsmith 3858 — реестр разведки</h1>
<div class="sub">Потребность рудника, состав изделия, цены независимого рынка, проверка заводов и порядок работы
без чертежей. Собрано {e(date.today().isoformat())} из {len(need)} позиций потребности, {len(catalog)} строк каталога,
{len(verdicts)} вердиктов по заводам и {made_n} доказательств изготовления.</div>
</header>
<nav>{tabs}</nav><main>{secs}</main>
<script>{js}</script></body></html>"""

    OUT.mkdir(exist_ok=True)
    p = OUT / "telsmith.html"
    p.write_text(html_doc, encoding="utf-8")
    print(f"zip/public/telsmith.html: {p.stat().st_size:,} байт | потребность {len(need)}, каталог {len(catalog)}, "
          f"вердиктов {len(verdicts)}, изготовителей {made_n}, углов {len(angles)}")
    return p


if __name__ == "__main__":
    build()
