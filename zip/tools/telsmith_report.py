#!/usr/bin/env python3
"""Единый отчёт по сорсингу ЗИП щековой дробилки Telsmith 3858.

Рабочий документ сорсера: что покупаем, сколько это стоит, где брать цены,
кого запрашивать, что спрашивать и как принимать.

Вход (zip/data):
  telsmith_3858.json      потребность, состав изделия, классы   — telsmith_parse.py
  telsmith_prices.json    базис расчёта и цены аутмаркета
  telsmith_recon.json     сходимость встречных оценок
  telsmith_suppliers.json поставщики, расшифровки кодов, порядок работы без чертежей
  telsmith_brands.json    другие имена машины и каналы снабжения
Выход: zip/orders/ТЕЛСМИТ-СОРСИНГ.html (+ PDF через telsmith_pdf.py)
"""
import html
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "orders"

e = lambda s: html.escape(str(s if s is not None else ""))
rub = lambda v: f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else "—"
mln = lambda v: f"{v/1e6:,.2f}".replace(",", " ") if isinstance(v, (int, float)) else "—"
usd = lambda v: f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else "—"


def load(name, default=None):
    p = D / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else (default if default is not None else {})


CSS = """
@page{size:A4 landscape;margin:11mm 9mm}
body{font:10px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
h1{font-size:19px;margin:0 0 4px}
h2{font-size:13px;margin:14px 0 5px;background:#1b2330;color:#fff;padding:5px 9px;border-radius:3px;page-break-after:avoid}
h3{font-size:11.5px;margin:10px 0 3px;color:#0b3d91;page-break-after:avoid}
.mut{color:#555;font-size:9px}
table{border-collapse:collapse;width:100%;margin:4px 0} tr{page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:9px}
th{background:#eef2f7} b{color:#0b3d91}
.box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;padding:6px 10px;margin:6px 0;page-break-inside:avoid;font-size:9.5px}
.warn{border-left-color:#c62828;background:#fff8f8}
.key{border-left-color:#b8860b;background:#fffdf5}
.num{text-align:right;white-space:nowrap}
.flag{color:#c62828;font-size:8.5px}
.w1{background:#eaf6ec} .w2{background:#fbfbfb}
ul{margin:3px 0 3px 15px;padding:0} li{margin:1.5px 0}
.kpi{display:flex;gap:8px;margin:6px 0}
.kpi div{flex:1;border:1px solid #ccc;border-radius:4px;padding:6px 9px}
.kpi b{display:block;font-size:15px;color:#111}
"""

CLS_TITLE = {
    "распорная": "Распорная плита, седло, зажимы",
    "футеровка": "Футеровки и клинья щёк",
    "вал": "Вал, подшипники, корпуса, шестерни",
    "гидравлика": "Гидравлика: цилиндры, насосы, клапаны, РВД",
    "смазка": "Смазка и фильтрация",
    "рти": "Уплотнения и прокладки",
    "крепёж": "Крепёж",
    "электрика": "Датчики и реле",
    "прочее": "Прочее",
}
ORDER = ["распорная", "футеровка", "вал", "гидравлика", "смазка", "рти", "крепёж", "электрика", "прочее"]
MAKERS = {
    "распорная": "Литейный завод с расчётом на срез: плита работает как предохранитель машины",
    "футеровка": "Литьё марганцовистой стали Mn13Cr2 / Mn18Cr2 с термообработкой",
    "вал": "Тяжёлая поковка и расточка; подшипники серийные, покупаются отдельно",
    "гидравлика": "Серийные изделия под внутренним кодом Telsmith",
    "смазка": "Станции смазки и фильтрация — серийные изделия",
    "рти": "Уплотнения по посадочному размеру, серийная номенклатура",
    "крепёж": "Метизы, класс прочности по документации",
    "электрика": "Реле и датчики — серийные приборы",
    "прочее": "Смешанный состав: пальцы, планки, кожухи, рукава",
}


def main():
    d = load("telsmith_3858.json")
    pr = load("telsmith_prices.json", {"estimates": [], "basis": [], "fx_rub_usd": 86.47})
    rc = load("telsmith_recon.json", {"items": [], "summary": ""})
    sp = load("telsmith_suppliers.json", {"suppliers": [], "crossrefs": [], "plan": {}, "geom": []})
    br = load("telsmith_brands.json", {"synth": {}})

    m, st, need = d["machine"], d["stats"], d["need"]
    classes = {c["key"]: c for c in d["classes"]}
    fx = pr.get("fx_rub_usd") or 86.47
    est = {i["pn"]: i for x in pr.get("estimates", []) for i in x.get("items", []) if i.get("pn")}
    recon = {i["pn"]: i for i in rc.get("items", [])}
    sy = br.get("synth") or {}
    plan = sp.get("plan") or {}

    # экономика: что охвачено оценкой
    rows, d_sum, a_sum = [], 0, 0
    for n in need:
        it = est.get(n["oem"]) if n.get("oem") else None
        if not it or not isinstance(n["sum_rub"], (int, float)) or not isinstance(n["price_rub"], (int, float)) or not n["qty"]:
            continue
        a = it["delivered_usd"] * fx
        d_sum += n["sum_rub"]
        a_sum += a * n["qty"]
        rows.append((n, it, a))

    H = [f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>']

    # ── шапка и резюме ────────────────────────────────────────────────────
    H.append(f"""<h1>Щековая дробилка Telsmith 3858: рабочий документ сорсера</h1>
<div class="mut">Реестр исследований ГШО · {date.today().strftime('%d.%m.%Y')} · {e(m['type'])} · {e(m['site'])}<br>
Действующий канал — {e(m['channel'])}. Цены прейскуранта — рубли без НДС, склад Норильск.
Закупка ведётся по аутмаркету: независимые изготовители. Оригинал через дистрибьюторов и обходные каналы не рассматриваются.</div>""")

    H.append(f"""<div class="kpi">
<div><span class="mut">Потребность</span><b>{st['need_positions']} поз. · {st['need_sum_rub']/1e6:.0f} млн ₽</b><span class="mut">по прейскуранту дилера</span></div>
<div><span class="mut">Охвачено оценкой</span><b>{len(rows)} поз. · {d_sum/1e6:.0f} млн ₽</b><span class="mut">{d_sum/st['need_sum_rub']*100:.0f}% суммы потребности</span></div>
<div><span class="mut">Аутмаркет с доставкой</span><b>{a_sum/1e6:.1f} млн ₽</b><span class="mut">по курсу {fx:.2f} ₽/$</span></div>
<div><span class="mut">Запас</span><b>{(d_sum-a_sum)/1e6:.0f} млн ₽ · {d_sum/a_sum:.0f}×</b><span class="mut">до получения предложений — рамка для переговоров</span></div>
</div>""")

    H.append('<div class="box key"><b>Пять вещей, которые нужно знать до первого письма.</b><ol style="margin:3px 0 0 16px;padding:0">'
             f'<li><b>В списке две разные машины.</b> {st.get("vibro_positions", 0)} позиций на '
             f'{st.get("vibro_sum_rub", 0)/1e6:.1f} млн ₽ относятся не к дробилке, а к вибровозбудителю питателя — '
             'группы номеров 277-6xx и 18-xxxx. В каталоге дистрибьютора B1-18-1179 описана как DRIVE GEAR 280H '
             'VIBRATING UNIT. Это другие поставщики и другой запрос.</li>'
             f'<li><b>Серия.</b> {e(m.get("series_note", ""))}</li>'
             f'<li><b>Машина снята с производства.</b> {e(m.get("status", ""))}</li>'
             f'<li><b>Ключ поиска.</b> {e(m.get("search_key", ""))}</li>'
             '<li><b>Признак настоящего изготовителя — масса.</b> Заводские массы опубликованы: распорная плита '
             'B1-273-1723 — 342 кг. Кто называет вес изделия, тот его делал; кто уходит от ответа, перепродаёт.</li>'
             '</ol></div>')

    # ── 1. Потребность ────────────────────────────────────────────────────
    H.append('<h2>1. Что покупаем</h2>')
    H.append('<table><tr><th style="width:26%">Класс деталей</th><th class="num">Позиций</th>'
             '<th class="num">Сумма, млн ₽</th><th class="num">Доля</th><th>Кто это изготавливает</th></tr>')
    for k in ORDER:
        c = classes.get(k)
        if not c:
            continue
        H.append(f'<tr><td><b>{e(c["title"])}</b></td><td class="num">{c["positions"]}</td>'
                 f'<td class="num">{mln(c["sum_rub"])}</td>'
                 f'<td class="num">{c["sum_rub"]/st["need_sum_rub"]*100:.1f}%</td>'
                 f'<td>{e(MAKERS.get(k, ""))}</td></tr>')
    H.append('</table>')
    H.append(f'<div class="mut">Состав изделия по каталогу запасных частей — {st["catalog_rows"]} строк '
             f'в {st["catalog_nodes"]} узлах, с потребностью сопоставлена {st["matched"]} позиция.</div>')

    # ── 2. Как считалась цена ─────────────────────────────────────────────
    H.append('<h2>2. Как считалась цена и где брать цифры самому</h2>')
    H.append('<div class="box">Цена считалась двумя независимыми путями навстречу друг другу. '
             '<b>Снизу вверх</b> — от материала и переделов: масса, марка стали, литьё или ковка, механическая '
             'и термическая обработка, контроль, упаковка, прибыль завода. <b>Сверху вниз</b> — от рынка: '
             'опубликованные цены аутмаркета на такую же или ближайшую деталь. Совпадение двух путей в пределах '
             'двух раз означает, что цифру можно нести в переговоры; расхождение больше означает, что мы чего-то '
             'не знаем о самой позиции.</div>')
    for b in pr.get("basis", []):
        H.append(f'<h3>Базис расчёта: {e(b["topic"])}</h3>')
        H.append('<table><tr><th style="width:30%">Величина</th><th style="width:17%">Значение</th>'
                 '<th>Основание и источник</th></tr>')
        for v in b.get("values", [])[:14]:
            src = e(v.get("basis", ""))[:340]
            if v.get("url"):
                src += f'<br><span class="mut">{e(v["url"])[:110]}</span>'
            H.append(f'<tr><td>{e(v["item"])[:130]}</td><td><b>{e(v["value"])[:70]}</b></td><td>{src}</td></tr>')
        H.append('</table>')
        if b.get("notes"):
            H.append(f'<div class="mut">Неопределённость: {e(b["notes"])[:500]}</div>')

    # ── 3. Цена и запас ───────────────────────────────────────────────────
    H.append('<h2>3. Цена и запас по позициям</h2>')
    H.append('<table><tr><th>Номер</th><th style="width:23%">Наименование</th><th class="num">Кол-во</th>'
             '<th class="num">Дилер, ₽/шт</th><th class="num">Аутмаркет FOB, $/шт</th>'
             '<th class="num">С доставкой, ₽/шт</th><th class="num">Крат.</th><th class="num">Запас, млн ₽</th>'
             '<th style="width:21%">Что это на самом деле</th><th>Надёжность</th></tr>')
    for n, it, a in sorted(rows, key=lambda r: -(r[0]["sum_rub"] - r[2] * r[0]["qty"])):
        gap = n["sum_rub"] - a * n["qty"]
        fob = f'{it["unit_usd_low"]:,.0f}–{it["unit_usd_high"]:,.0f}'.replace(",", " ")
        H.append(f'<tr><td><b>{e(n["oem"])}</b></td><td>{e(n["name"])[:44]}</td>'
                 f'<td class="num">{e(n["qty"])}</td><td class="num">{rub(n["price_rub"])}</td>'
                 f'<td class="num">{fob}</td><td class="num">{rub(a)}</td>'
                 f'<td class="num">{n["price_rub"]/a:.0f}×</td><td class="num">{gap/1e6:.2f}</td>'
                 f'<td>{e(it["identified_as"])[:88]}</td><td>{e(it["confidence"])[:30]}</td></tr>')
    H.append('</table>')

    # ── 4. Сходимость ─────────────────────────────────────────────────────
    if recon:
        conv = [i for i in recon.values() if i.get("converges")]
        H.append('<h2>4. Сходимость встречных оценок: чему верить и где копать</h2>')
        H.append(f'<div class="box">Сошлось по {len(conv)} позициям из {len(recon)}. '
                 'Где оценки разошлись больше чем вдвое — в последнем столбце сказано, что именно проверить '
                 'у поставщика или на машине, чтобы расхождение снять.</div>')
        if rc.get("summary"):
            H.append(f'<div class="box">{e(rc["summary"])}</div>')
        H.append('<table><tr><th>Номер</th><th style="width:19%">Наименование</th>'
                 '<th class="num">Снизу вверх, $</th><th class="num">Сверху вниз, $</th>'
                 '<th class="num">Расхождение</th><th class="num">В работу, $</th>'
                 '<th class="num">Дилер, ₽/шт</th><th style="width:33%">Куда копать</th></tr>')
        by_pn = {n["oem"]: n for n in need if n.get("oem")}
        for i in sorted(recon.values(), key=lambda x: (x.get("converges", False), x.get("pn", ""))):
            n = by_pn.get(i["pn"], {})
            cl = "" if i.get("converges") else ' class="flag"'
            H.append(f'<tr><td><b>{e(i["pn"])}</b></td><td>{e(i.get("name", ""))[:38]}</td>'
                     f'<td class="num">{usd(i.get("bottom_up_usd")) if i.get("bottom_up_usd") else "—"}</td>'
                     f'<td class="num">{usd(i.get("top_down_usd")) if i.get("top_down_usd") else "—"}</td>'
                     f'<td{cl}>{e(i.get("spread", ""))[:24]}</td>'
                     f'<td class="num"><b>{usd(i.get("take_usd")) if i.get("take_usd") else "—"}</b></td>'
                     f'<td class="num">{rub(n.get("price_rub"))}</td>'
                     f'<td>{e(i.get("dig", ""))[:230]}</td></tr>')
        H.append('</table>')

    # ── 5. Расшифровка номеров ────────────────────────────────────────────
    if sp.get("crossrefs"):
        H.append('<h2>5. Расшифровка номеров: как превратить код Telsmith в каталожное изделие</h2>')
        H.append('<div class="box">Открытый рабочий источник — поисковая база дистрибьютора Telsmith '
                 '(texasbearing.com): по номеру выдаётся описание изделия и привязка к модели. Оттуда взяты все '
                 'расшифровки ниже. Второй приём: в карточках ремкомплектов публикуется типоразмер цилиндра — '
                 'например, позиция 66H19 читается как «Kit, Seal, Cylinder, Hydraulic, 6.00×1.00», то есть '
                 'диаметр цилиндра и штока восстанавливаются без каталога Telsmith. Третий: цена литья у китайских '
                 'заводов публикуется в долларах за килограмм, поэтому зная массу, цену считают сами.</div>')
        H.append('<table><tr><th style="width:9%">Код Telsmith</th><th style="width:32%">Что это на самом деле</th>'
                 '<th style="width:14%">Изготовитель</th><th class="num">Цена дилера, ₽/шт</th>'
                 '<th>Надёжность расшифровки</th></tr>')
        by_pn = {n["oem"]: n for n in need if n.get("oem")}
        for c in sp["crossrefs"]:
            n = by_pn.get(c["telsmith_pn"], {})
            H.append(f'<tr><td><b>{e(c["telsmith_pn"])[:22]}</b></td><td>{e(c["real_pn"])[:330]}</td>'
                     f'<td>{e(c["real_maker"])[:70]}</td><td class="num">{rub(n.get("price_rub"))}</td>'
                     f'<td>{e(c["confidence"])[:150]}</td></tr>')
        H.append('</table>')

    # ── 6. Каналы ─────────────────────────────────────────────────────────
    if sy.get("channels"):
        H.append('<h2>6. Каналы снабжения: где ещё существует эта машина</h2>')
        if sy.get("verdict"):
            H.append(f'<div class="box">{e(sy["verdict"])}</div>')
        H.append('<table><tr><th style="width:20%">Канал</th><th style="width:36%">Что делать</th>'
                 '<th style="width:22%">Что даёт</th><th>Риск</th></tr>')
        for c in sy["channels"]:
            H.append(f'<tr><td><b>{e(c["channel"])[:140]}</b></td><td>{e(c["what_to_do"])[:460]}</td>'
                     f'<td>{e(c["value"])[:260]}</td><td>{e(c["risk"])[:260]}</td></tr>')
        H.append('</table>')

    # ── 7. Кого запрашивать ───────────────────────────────────────────────
    sup = sp.get("suppliers", [])
    if sup:
        H.append('<h2>7. Кого запрашивать</h2>')
        H.append('<div class="box">Порядок отбора: доказанное изготовление важнее упоминания бренда. '
                 'Первая волна — компании, у которых в каталоге есть позиции с привязкой к типоразмеру 38×58 '
                 'либо к модели 3858. Вторая — подтверждённый опыт по другим моделям Telsmith. '
                 'Справочно — компании, у которых связь с брендом сводится к поисковой накрутке; их держим как '
                 'резерв по номенклатуре, но образцы у них не заказываем. Компании канала OEM исключены.</div>')
        H.append('<table><tr><th style="width:19%">Компания</th><th style="width:15%">Уровень доказательства</th>'
                 '<th style="width:29%">Чем подтверждён опыт</th><th style="width:14%">Контакт</th>'
                 '<th>Очередь запроса</th></tr>')
        for s in sup[:26]:
            ct = "<br>".join(x for x in (e(s.get("email")), e(s.get("phone")), e(s.get("whatsapp"))) if x) \
                 or '<span class="mut">контакт не подтверждён</span>'
            cls = ' class="w1"' if s.get("recommend", "").startswith("ПЕРВАЯ") else ""
            site = f'<br><span class="mut">{e(s["site"])[:44]}</span>' if s.get("site") else ""
            H.append(f'<tr{cls}><td><b>{e(s["name"])[:52]}</b>{site}</td><td>{e(s.get("kind", ""))[:90]}</td>'
                     f'<td>{e(s.get("evidence", ""))[:330]}</td><td>{ct}</td>'
                     f'<td>{e(s.get("recommend", ""))[:70]}</td></tr>')
        H.append('</table>')
        risky = [s for s in sup if s.get("risks")][:6]
        for s in risky:
            H.append(f'<div class="box warn"><b>{e(s["name"])[:60]} — на что смотреть:</b><ul>'
                     + "".join(f"<li>{e(r)[:300]}</li>" for r in s["risks"][:4]) + '</ul></div>')

    # ── 8. Что запрашивать и как принимать ────────────────────────────────
    if plan.get("classes"):
        H.append('<h2>8. Что запрашивать и как принимать — по классам</h2>')
        if plan.get("summary"):
            H.append(f'<div class="box">{e(plan["summary"])}</div>')
        for c in plan["classes"]:
            bad = (c.get("feasible") or "").lower().startswith("нет")
            H.append(f'<div class="box{" warn" if bad else ""}"><b>{e(c["cls"])[:120]}</b><br>'
                     f'<b>Реально ли без чертежей:</b> {e(c["feasible"])[:150]}<br>'
                     f'<b>Как закрывать:</b> {e(c["route"])[:900]}')
            if c.get("ask_supplier"):
                H.append('<br><b>Что спросить у поставщика:</b><ul>'
                         + "".join(f"<li>{e(x)[:280]}</li>" for x in c["ask_supplier"][:6]) + '</ul>')
            if c.get("critical_dims"):
                H.append('<b>Критичные размеры — ошибка недопустима:</b><ul>'
                         + "".join(f"<li>{e(x)[:280]}</li>" for x in c["critical_dims"][:5]) + '</ul>')
            if c.get("forgiving_dims"):
                H.append(f'<b>Допускают отклонение:</b> {e("; ".join(c["forgiving_dims"]))[:400]}<br>')
            H.append(f'<b>Главный риск:</b> {e(c.get("risk", ""))[:420]}</div>')

    # ── 9. Позиции первого заказа ─────────────────────────────────────────
    H.append('<h2>9. Позиции первого заказа</h2>')
    first = sorted([n for n in need if isinstance(n["sum_rub"], (int, float))], key=lambda x: -x["sum_rub"])[:26]
    H.append('<table><tr><th>Element ID</th><th>Номер Telsmith</th><th style="width:28%">Наименование</th>'
             '<th>Узел</th><th class="num">Кол-во</th><th class="num">Цена дилера, ₽/шт</th>'
             '<th class="num">Сумма, млн ₽</th><th>Вид номера</th><th>Машина</th></tr>')
    for n in first:
        vib = (n.get("unit") or "").startswith("вибро")
        H.append(f'<tr><td>{e(n["eid"])}</td><td><b>{e(n["oem"])}</b></td><td>{e(n["name"])[:52]}</td>'
                 f'<td>{e(n["node"])[:24]}</td><td class="num">{e(n["qty"])}</td>'
                 f'<td class="num">{rub(n["price_rub"])}</td><td class="num">{mln(n["sum_rub"])}</td>'
                 f'<td>{e(n["pn_kind"])}</td><td{" class=flag" if vib else ""}>'
                 f'{"питатель" if vib else "дробилка"}</td></tr>')
    H.append('</table>')

    H.append('<div class="mut" style="margin-top:9px">Источники: прейскурант дилера и каталог запасных частей '
             'Telsmith 3858 (документы заказчика); заводская документация Telsmith по массам и параметрам; '
             'поисковая база дистрибьютора; опубликованные прайсы китайских литейных заводов; курс Банка России '
             'и тарифы перевозчиков на дату отчёта. Контакты приведены только опубликованные на сайтах компаний. '
             'Оценка цен ориентировочная: до получения предложений заводов её следует считать рамкой для '
             'переговоров, а не ценой закупки.</div></body></html>')

    OUT.mkdir(exist_ok=True)
    (OUT / "ТЕЛСМИТ-СОРСИНГ.html").write_text("\n".join(H), encoding="utf-8")
    print(f"HTML: {st['need_positions']} позиций | оценено {len(rows)} | сверок {len(recon)} | "
          f"поставщиков {len(sup)} | расшифровок {len(sp.get('crossrefs', []))} | "
          f"классов в порядке работы {len(plan.get('classes', []))}")


if __name__ == "__main__":
    main()
