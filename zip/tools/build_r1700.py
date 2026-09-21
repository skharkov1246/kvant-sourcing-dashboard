#!/usr/bin/env python3
"""Страница ГШО «Caterpillar R1700G» → zip/public/r1700.html.

Досье машины (zip/data/r1700.json) — 12 разделов одним экраном: паспорт,
документация, перечень запчастей с нашими номерами, аналоги вторичного рынка,
официальные дилеры, заводы неоригинала, поставщики и маршруты по таможне,
цены, площадки торгов и эксплуатанты, факты сделок из Битрикса и честный
перечень пробелов.

Данные внутри страницы (самодостаточный HTML, работает офлайн и в печать).
Выход: zip/public/r1700.html
Запуск: python zip/tools/build_r1700.py — вызывается из zip/build.py
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D, OUT = ROOT / "data", ROOT / "public"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_telsmith_page import CSS  # общая вёрстка страниц-разведок  # noqa: E402
# В этом модуле num() уже занят форматированием числа для вёрстки,
# поэтому разбор цены берём под своим именем.
from r1700_dossier import num as price_num  # то же правило разбора, что у базы  # noqa: E402

e = lambda s: html.escape(str(s if s is not None else ""))
J = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def num(v):
    return f"{v:,}".replace(",", " ") if isinstance(v, (int, float)) else "—"


def link(u, text=None):
    u = str(u or "").strip()
    if not u.startswith("http"):
        return e(u)
    return f'<a href="{e(u)}" target="_blank" rel="noopener">{e(text or "источник")}</a>'


def conf_tag(c):
    c = (c or "").lower()
    cls = {"high": "ok", "med": "wr", "low": "no"}.get(c, "wr")
    ru = {"high": "высокое", "med": "среднее", "low": "низкое"}.get(c, c or "—")
    return f'<span class="tag {cls}">{e(ru)}</span>'


def verdict_tag(v):
    v = (v or "").strip()
    if not v:
        return '<span class="mut">не проверялся</span>'
    cls = {"подтверждён": "ok", "исправлен": "wr", "сомнителен": "wr", "снят": "no",
           "наша база": "ok"}.get(v, "wr")
    return f'<span class="tag {cls}">{e(v)}</span>'


def cards(items, fields, title_field, mut_field=None):
    """Карточки одного вида: заголовок, поля «метка: значение», ссылка-источник."""
    out = []
    for it in items:
        body = "".join(
            f'<div class="txt"><b>{e(label)}:</b> {e(it.get(f))}</div>'
            for f, label in fields if str(it.get(f) or "").strip())
        src = it.get("source") or it.get("url") or it.get("site")
        mut = f'<div class="mut">{e(it.get(mut_field))}</div>' if mut_field and it.get(mut_field) else ""
        out.append(f'<div class="card"><b>{e(it.get(title_field))}</b> '
                   f'{conf_tag(it.get("confidence")) if it.get("confidence") else ""} '
                   f'{verdict_tag(it.get("verdict")) if "verdict" in it else ""}'
                   f'{body}{mut}<div class="mut">{link(src)}</div></div>')
    return "".join(out) or '<div class="mut">пусто — звено не закрыто.</div>'


def build():
    d = json.loads((D / "r1700.json").read_text(encoding="utf-8"))
    s = d["stats"]
    m = d["machine"]
    own = d["own"]
    cst = own["customs"]
    btx = own.get("bitrix") or {}
    parts = d["parts"]
    S = []

    # ── 1. Итог ─────────────────────────────────────────────────────────────
    kpi = f"""<div class="kpi">
<div><b>{num(s['parts'])}</b><span>деталей в перечне</span></div>
<div><b>{num(s['parts_with_alt'])}</b><span>с аналогом вторичного рынка</span></div>
<div><b>{num(s['alts'])}</b><span>кроссов по {num(s['alt_brands'])} брендам</span></div>
<div><b>{num(s['docs'])}</b><span>документов по машине</span></div>
<div><b>{num(s['orgs'])}</b><span>организаций-каналов</span></div>
<div><b>{num(s['prices'])}</b><span>ценовых ориентиров</span></div>
<div><b>{num(s['customs_importers'])}</b><span>импортёров Cat по таможне</span></div>
<div><b>{num(s['odm_candidates'])}</b><span>заводов-кандидатов ODM</span></div>
</div>"""
    play = "".join(
        '<div class="card"><b>{}</b><div class="txt">{}</div>{}</div>'.format(
            e(p.get("step")), e(p.get("why")),
            '<div class="mut">{}</div>'.format(e(p.get("how"))) if p.get("how") else "")
        for p in d.get("playbook") or [])
    cov = "".join(f'<tr><td>{e(k)}</td><td class="n">{num(v)}</td></tr>'
                  for k, v in sorted((s.get("parts_by_node") or {}).items()))
    S.append(("sum", "Итог и что делать", f"""{kpi}
<div class="card"><b>{e(m['name'])} — {e(m['kind'])}</b>
<div class="txt">{e(m['why'])}</div>
<div class="mut">Семейство: {e(m['family'])} · собрано {e(d['updated'])} ·
источников {num(s['sources'])} · разведано направлений {num(s['recon_slices'])}</div></div>
<h2>Прикладные шаги</h2>{play or '<div class="mut">шаги не рассчитаны</div>'}
<h2>Чем закрыт перечень по узлам</h2>
<table class="ot" style="max-width:560px"><thead><tr><th>Узел</th><th>Деталей</th></tr></thead>
<tbody>{cov}</tbody></table>
<h2>Чего нет</h2>
{"".join(f'<div class="card"><b>{e(k)}</b><div class="txt">{e(v)}</div></div>' for k, v in (d.get("gaps") or {}).items())
     or '<div class="mut">пробелы не зафиксированы</div>'}"""))

    # ── 2. Паспорт ──────────────────────────────────────────────────────────
    vr = "".join(f"""<div class="card"><b>{e(v.get('model'))}</b> <span class="mut">{e(v.get('years'))}</span>
<div class="txt"><b>Двигатель:</b> {e(v.get('engine'))} · <b>мощность:</b> {e(v.get('power_kw'))} кВт ·
<b>ковш:</b> {e(v.get('bucket_m3'))} м³ · <b>грузоподъёмность:</b> {e(v.get('payload_t'))} т ·
<b>масса:</b> {e(v.get('weight_t'))} т · <b>шины:</b> {e(v.get('tyres'))}</div>
<div class="txt"><b>Серийный префикс:</b> {e(v.get('serial_prefix'))}</div>
{f'<div class="txt">{e(v.get("note"))}</div>' if v.get('note') else ''}
<div class="mut">{link(v.get('source'))}</div></div>""" for v in d.get("variants") or [])
    sp = "".join(f'<tr><td>{e(x.get("param"))}</td><td>{e(x.get("value"))} {e(x.get("unit"))}</td>'
                 f'<td>{e(x.get("variant"))}</td><td>{conf_tag(x.get("confidence"))}</td>'
                 f'<td>{link(x.get("source"), "↗")}</td></tr>' for x in d.get("specs") or [])
    tree = "".join(f'<div class="card"><b>{e(n.get("node"))}</b> '
                   f'<span class="tag {"no" if n.get("crit") == "A" else "wr" if n.get("crit") == "B" else "ok"}">'
                   f'критичность {e(n.get("crit"))}</span>'
                   f'<div class="txt">{e(", ".join(n.get("children") or []))}</div></div>'
                   for n in d.get("node_tree") or [])
    S.append(("spec", "Паспорт", f"""
<h2>Модификации и взаимозаменяемость</h2>{vr or '<div class="mut">нет данных</div>'}
<h2>Параметры машины</h2>
<table class="ot"><thead><tr><th>Параметр</th><th>Значение</th><th>Модификация</th>
<th>Доверие</th><th>Ист.</th></tr></thead><tbody>{sp}</tbody></table>
<h2>Узлы и критичность</h2>{tree or '<div class="mut">дерево узлов не собрано</div>'}"""))

    # ── 3. Документация ─────────────────────────────────────────────────────
    dc = "".join(f"""<tr><td class="pn">{e(x.get('form'))}</td><td>{e(x.get('title_ru'))}</td>
<td>{e(x.get('kind'))}</td><td>{e(x.get('covers'))}</td><td>{e(x.get('lang'))}</td>
<td>{e(x.get('where'))}</td><td class="n">{e(x.get('price'))}</td>
<td>{conf_tag(x.get('confidence'))}</td><td>{verdict_tag(x.get('verdict'))}</td>
<td>{link(x.get('url'), '↗')}</td></tr>""" for x in d.get("docs") or [])
    S.append(("docs", f"Документация · {len(d.get('docs') or [])}", f"""
<h2>Руководства, каталоги и схемы по машине</h2>
<div class="mut">Номер формы — ключ поиска: по нему документ ищется у дилера, в SIS и на вторичном рынке.
Без каталога запчастей (SEBP) ведомость на машину не собрать, без схем (UENR/RENR) не отремонтировать
электрику и гидравлику.</div>
<div class="bar"><input id="qd" placeholder="поиск: форма, название, тип…"><span class="mut" id="cntd"></span></div>
<table class="ot" id="td"><thead><tr><th>Форма</th><th>Название</th><th>Тип</th><th>Покрывает</th>
<th>Язык</th><th>Где взять</th><th>Цена</th><th>Доверие</th><th>Проверка</th><th>Ист.</th></tr></thead>
<tbody>{dc}</tbody></table>"""))

    # ── 4. Запчасти ─────────────────────────────────────────────────────────
    S.append(("parts", f"Запчасти · {len(parts)}", """
<h2>Перечень запчастей машины</h2>
<div class="mut">Строка с вердиктом «наша база» пришла из перечня ЗИП ГШО — применимость к R1700G
подтверждена заявкой, а не страницей в сети. Остальные проверены вторым проходом по источникам.
«Снят» означает: разведка нашла номер, проверка его не подтвердила — держим след, но в торги не берём.</div>
<div class="bar"><input id="qp" placeholder="поиск: номер, наименование, узел, наш KV…">
<select id="fnode"></select><select id="fver"></select><select id="fconf"></select>
<label class="mut"><input type="checkbox" id="falt"> только с аналогом</label>
<label class="mut"><input type="checkbox" id="fkv"> только наши позиции</label>
<button id="csvp" class="exp">⬇ CSV</button><span class="mut" id="cntp"></span></div>
<div style="overflow-x:auto"><table class="ot" id="tp"><thead><tr>
<th data-k="pn">Cat PN</th><th data-k="name_ru">Наименование</th><th data-k="node">Узел</th>
<th data-k="applic">Применимость</th><th data-k="qty">Кол-во</th><th data-k="interval">Интервал</th>
<th data-k="kvs">Наш номер</th><th data-k="nalt">Аналогов</th><th data-k="price">Цена-ориентир</th>
<th data-k="bitrix_status">Битрикс</th><th data-k="confidence">Доверие</th><th data-k="verdict">Проверка</th>
</tr></thead><tbody></tbody></table></div>
<div id="pdet"></div>"""))

    # ── 5. Аналоги ──────────────────────────────────────────────────────────
    S.append(("alts", f"Аналоги · {len(d['alts'])}", """
<h2>Кроссы вторичного рынка</h2>
<div class="mut">Таблица работает в обе стороны: ищи по номеру Caterpillar, чтобы увидеть, чем заменить,
и по номеру аналога, чтобы понять, какой оригинал за ним стоит. На торгах кросс — это доказательство
применимости: без него заявку с неоригиналом отклоняют.</div>
<div class="bar"><input id="qa" placeholder="поиск по любому номеру или бренду…">
<select id="fbrand"></select><button id="csva" class="exp">⬇ CSV</button><span class="mut" id="cnta"></span></div>
<div style="overflow-x:auto"><table class="ot" id="ta"><thead><tr><th>Cat PN</th><th>Бренд</th>
<th>Номер аналога</th><th>Вид</th><th>Примечание</th></tr></thead><tbody></tbody></table></div>"""))

    # ── 6. Оригинал: дилеры ─────────────────────────────────────────────────
    POLICY = d.get("policy") or {}
    pol_note = (f'<div class="card" style="border-left:3px solid var(--warn)"><b>Лист запроса: '
                f'{e(POLICY.get("ask_scope"))}</b><div class="txt">{e(POLICY.get("why"))}</div>'
                f'<div class="mut">Решение от {e(POLICY.get("decided"))}</div></div>') if POLICY else ""

    def split_ask(rows):
        """Кого запрашиваем и кто остаётся картиной рынка — по решению владельца."""
        return [o for o in rows if o.get("ask")], [o for o in rows if not o.get("ask")]

    dealers_all = [o for o in d["orgs"] if o["slice"] == "dealers"]
    dealers, dealers_ru = split_ask(dealers_all)
    # Раньше здесь стоял один заголовок «по решению владельца». Теперь канал
    # выпадает из листа запроса по четырём разным причинам — решение владельца,
    # снят проверкой, дубль по домену, справочный каталог, — и каждая пишется у
    # своей строки. Один общий заголовок на четыре причины врал бы о трёх из них.
    ru_block = lambda rows, title: (
        f'<details style="margin-top:10px"><summary class="mut">{title} · {len(rows)} — '
        f'в лист запроса не выводятся, из данных не удалены</summary>'
        + cards(rows, [("ask_off", "Почему не запрашиваем"), ("kind", "Тип"), ("city", "Город"),
                       ("role", "Роль"), ("note", "Чем полезен")],
                "org", "site") + "</details>") if rows else ""

    # Поля адреса — одни и те же во всех трёх направлениях.
    CONTACT = [("email", "Почта"), ("contact_form", "Форма запроса"), ("contact_lang", "Язык письма"),
               ("contact_src", "Оговорка по адресу")]

    S.append(("deal", f"Оригинал · {len(dealers)}", f"""
<h2>Официальные каналы Caterpillar</h2>{pol_note}
<div class="mut">Первый вопрос дилеру — не цена, а готовность отгружать в РФ. От ответа зависит
весь маршрут. Второй — подтверждение применимости по серийному номеру машины.</div>
<div class="bar"><input id="qdl" placeholder="поиск: организация, страна, роль…"><span class="mut" id="cntdl"></span></div>
<div id="listdl">{cards(dealers, [("role", "Роль"), ("country", "Страна"), ("city", "Город"),
                                  ("brands", "Бренды"), ("stock", "Наличие и срок"),
                                  *CONTACT, ("phone", "Телефон"), ("note", "Чем полезен")],
                        "org", "site")}</div>
{ru_block(dealers_ru, "Российские компании этого направления")}"""))

    # ── 7. Неоригинал ───────────────────────────────────────────────────────
    after_all = [o for o in d["orgs"] if o["slice"] == "aftermarket"]
    after, after_ru = split_ask(after_all)
    odm = own["odm"]
    odm_cards = "".join(f"""<div class="card"><b>{e(o.get('org'))}</b> {conf_tag(o.get('confidence'))}
<div class="txt">{e(o.get('makes'))}</div>
<div class="mut">{e(o.get('region'))}, {e(o.get('country'))} · {link(o.get('site'))} ·
из нашего справочника ODM</div></div>""" for o in odm[:400])
    S.append(("after", f"Неоригинал · {len(after)}+{len(odm)}", f"""
<h2>Кто делает неоригинал</h2>{pol_note}
<div class="mut">Разделяй изготовителя и торговца: на торгах письмо изготовителя о применимости
весит больше прайса перекупщика.</div>
<div class="bar"><input id="qaf" placeholder="поиск: завод, бренд, что делает…"><span class="mut" id="cntaf"></span></div>
<div id="listaf">{cards(after, [("kind", "Тип"), ("country", "Страна"), ("city", "Город"),
                                ("brands", "Бренды и узлы"), ("stock", "Партия и срок"),
                                *CONTACT, ("note", "Что покрывает")], "org", "site")}</div>
{ru_block(after_ru, "Российские заводы")}
<h2>Заводы из нашего справочника ODM с упоминанием Caterpillar · {len(odm)}</h2>
<div class="mut">Показаны первые {min(400, len(odm))} по уровню доверия. Полный перечень —
в zip/data/odm_suppliers.json и на вкладке «Позиции».</div>
<div class="bar"><input id="qodm" placeholder="поиск по заводам ODM…"><span class="mut" id="cntodm"></span></div>
<div id="listodm">{odm_cards}</div>"""))

    # ── 8. Поставщики и маршруты ────────────────────────────────────────────
    traders_all = [o for o in d["orgs"] if o["slice"] == "traders"]
    traders, traders_ru = split_ask(traders_all)
    imp = "".join(f'<tr><td>{e(x["org"])}</td><td class="n">{num(x["shipments"])}</td></tr>'
                  for x in cst["importers"][:60])
    exp = "".join(f'<tr><td>{e(x["org"])}</td><td class="n">{num(x["shipments"])}</td></tr>'
                  for x in cst["exporters"][:60])
    lanes = "".join(f'<tr><td>{e(x["origin"])}</td><td>{e(x["incoterms"])}</td><td>{e(x["place"])}</td>'
                    f'<td class="n">{num(x["shipments"])}</td></tr>' for x in cst["lanes"][:40])
    r17 = "".join(f'<div class="card"><b>{e(x.get("date"))} · {e(x.get("importer"))}</b>'
                  f'<div class="txt">{e(x.get("desc"))}</div>'
                  f'<div class="mut">отправитель {e(x.get("exporter"))} · происхождение {e(x.get("origin"))} ·'
                  f' {e(x.get("incoterms"))} {e(x.get("place"))} · ТН ВЭД {e(x.get("hs10"))} · {e(x.get("src"))}</div></div>'
                  for x in cst["r1700_rows"])
    S.append(("sup", f"Поставщики · {len(traders)}", f"""
<h2>Кого запрашивать</h2>{pol_note}
<div class="bar"><input id="qtr" placeholder="поиск: компания, город, что держат…"><span class="mut" id="cnttr"></span></div>
<div id="listtr">{cards(traders, [("kind", "Тип"), ("country", "Страна"), ("city", "Город"),
                                  ("brands", "Бренды"), ("stock", "Склад и срок"),
                                  *CONTACT, ("phone", "Телефон"), ("note", "Чем полезен")],
                        "org", "site")}</div>
{ru_block(traders_ru, "Российские торговцы и исполнители")}
<h2>Ввоз Caterpillar по нашей таможенной выгрузке</h2>
<div class="mut">{e(cst['note'])} Строк с признаком Caterpillar — {num(len(cst['rows']))}.
Это не мнение, а факт отгрузки: у этих компаний канал уже работает.</div>
<h3>Прямо по R1700G</h3>{r17 or '<div class="mut">строк именно по R1700G в выгрузке нет</div>'}
<div class="grid2">
<div><h3>Импортёры (получатели в РФ)</h3><table class="ot"><thead><tr><th>Организация</th>
<th>Отгрузок</th></tr></thead><tbody>{imp}</tbody></table></div>
<div><h3>Отправители (кто грузит)</h3><table class="ot"><thead><tr><th>Организация</th>
<th>Отгрузок</th></tr></thead><tbody>{exp}</tbody></table></div></div>
<h3>Маршруты и условия</h3><table class="ot" style="max-width:620px"><thead><tr><th>Происхождение</th>
<th>Инкотермс</th><th>Место</th><th>Отгрузок</th></tr></thead><tbody>{lanes}</tbody></table>"""))

    # ── 9. Цены ─────────────────────────────────────────────────────────────
    pr = "".join(f"""<tr><td class="pn">{e(x.get('pn'))}</td><td>{e(x.get('name_ru'))}</td>
<td>{e(x.get('tier'))}</td><td>{e(x.get('brand'))}</td><td class="n">{e(x.get('price'))}</td>
<td>{e(x.get('currency'))}</td><td>{e(x.get('seller'))}</td><td>{e(x.get('region'))}</td>
<td>{e(x.get('date'))}</td><td>{conf_tag(x.get('confidence'))}</td><td>{link(x.get('url'), '↗')}</td></tr>"""
                 for x in d.get("prices") or [])
    S.append(("price", f"Цены · {len(d.get('prices') or [])}", f"""
<h2>Ценовые ориентиры</h2>
<div class="mut">Три уровня на одну позицию — оригинал, качественный аналог, эконом. Без вилки
нельзя ни назвать цену на торгах, ни понять, что предложил конкурент.</div>
<div class="bar"><input id="qpr" placeholder="поиск: номер, наименование, продавец…"><span class="mut" id="cntpr"></span></div>
<table class="ot" id="tpr"><thead><tr><th>PN</th><th>Наименование</th><th>Уровень</th><th>Бренд</th>
<th>Цена</th><th>Вал.</th><th>Продавец</th><th>Регион</th><th>Дата</th><th>Доверие</th><th>Ист.</th>
</tr></thead><tbody>{pr}</tbody></table>"""))

    # ── 9а. Признаки и дефекты ──────────────────────────────────────────────
    flt = d.get("faults") or []
    # Номер детали кликабелен: со строки дефекта сорсер попадает прямо в карточку
    # позиции, а не ищет её руками в перечне из 561 строки.
    def pnlist(pns):
        return " · ".join(f'<a href="#pn={e(x)}" class="pn">{e(x)}</a>' for x in pns) or \
            '<span class="mut">номеров под это решение в ведомости нет</span>'

    fr = "".join(f"""<tr data-node="{e(x.get('node'))}"><td><b>{e(x.get('symptom'))}</b>
<div class="mut">{e(x.get('node'))}</div></td>
<td class="mut">{e(x.get('measure'))}</td>
<td>{e(x.get('defect'))}{f'<div class="mut">коды: {e(x.get("codes"))}</div>' if x.get('codes') else ''}</td>
<td class="mut">{e(x.get('confirm'))}</td>
<td>{e(x.get('repair'))}<div>{pnlist(x.get('parts') or [])}</div></td>
<td>{conf_tag(x.get('confidence'))}<div>{verdict_tag(x.get('verdict'))}</div></td>
<td>{link(x.get('url'), '↗')}</td></tr>"""
                  for x in flt)
    fst = (d.get("stats") or {})
    fv = fst.get("faults_verdicts") or {}
    S.append(("faults", f"Признаки и дефекты · {len(flt)}", f"""
<h2>От признака к ремонтному решению и номерам</h2>
<div class="mut">Звено «признак → дефект» до сих пор было пустым по всем машинам портала.
Строк {num(len(flt))} по {num(len(fst.get('faults_by_node') or {}))} узлам, с каталожными
номерами {num(fst.get('faults_with_parts'))}, с кодами диагностики {num(fst.get('faults_with_codes'))}.
Подтверждено документом {num(fv.get('подтверждён', 0))}, общая практика без нашего документа —
{num(fv.get('не проверялся', 0))}, сомнительно {num(fv.get('сомнителен', 0))}.</div>
<div class="mut"><b>Чего здесь нет:</b> порогов замера по этой машине. Руководства Cat по R1700G
(SEBU8211, KENR6256/6262/6264/6266, SENR9888) закрыты: catpublications отдаёт обложку, зеркала SIS —
403. Строка говорит, что мерить и чем подтверждать; норму берите из SIS под серийный номер.</div>
<div class="bar"><input id="qf" placeholder="поиск: признак, дефект, узел, номер, код…"><span class="mut" id="cntf"></span></div>
<table class="ot" id="tf"><thead><tr><th>Признак</th><th>Что меряют</th><th>Вероятный дефект</th>
<th>Чем подтвердить</th><th>Ремонтное решение и номера</th><th>Доверие</th><th>Ист.</th>
</tr></thead><tbody>{fr}</tbody></table>"""))

    # ── 10. Торги ───────────────────────────────────────────────────────────
    t = d["tenders"]
    pl = "".join(f"""<div class="card"><b>{e(x.get('name'))}</b> {conf_tag(x.get('confidence'))}
<div class="txt"><b>Закон:</b> {e(x.get('law'))} · <b>что там:</b> {e(x.get('what'))}</div>
<div class="txt"><b>Как искать:</b> {e(x.get('how'))}</div>
<div class="mut">{link(x.get('site'), x.get('site'))} · {link(x.get('source'))}</div></div>"""
                 for x in t.get("platforms") or [])
    ow = "".join(f"""<tr><td>{e(x.get('org'))}</td><td>{e(x.get('region'))}</td>
<td>{e(x.get('machines'))}</td><td>{e(x.get('note'))}</td><td>{link(x.get('source'), '↗')}</td></tr>"""
                 for x in t.get("owners") or [])
    ch = "".join(f'<div class="card"><b>{e(x.get("step"))}</b><div class="txt">{e(x.get("why"))}</div></div>'
                 for x in t.get("checklist") or [])
    S.append(("tend", "Торги", f"""
<h2>Где объявляют закупки</h2>{pl or '<div class="mut">площадки не собраны</div>'}
<h2>Кто эксплуатирует машины</h2>
<table class="ot"><thead><tr><th>Организация</th><th>Регион</th><th>Машины</th><th>Примечание</th>
<th>Ист.</th></tr></thead><tbody>{ow}</tbody></table>
<h2>Чтобы заявку не отклонили</h2>{ch or '<div class="mut">чек-лист не собран</div>'}"""))

    # ── 11. Битрикс ─────────────────────────────────────────────────────────
    bs = btx.get("stats") or {}
    def deal_rows(rows, kind):
        out = ""
        for r in rows:
            dl = "".join(f'<div class="mut">{e(x.get("date"))} · {e(x.get("deal"))} · '
                         f'{e(x.get("client"))} · {e(x.get("status"))}</div>'
                         for x in r.get("deals") or [])
            out += (f'<div class="card"><b>{e(r.get("pn"))} · {e(r.get("name"))}</b> '
                    f'<span class="tag {"ok" if kind == "продавали" else "wr"}">{e(kind)}</span>'
                    f'<div class="txt">{e(r.get("node"))} · применимость {e(r.get("machine"))} · '
                    f'вилка {e(r.get("price_min"))}–{e(r.get("price_max"))} {e(r.get("price_cur"))} '
                    f'({e(r.get("price_src"))})</div>{dl}</div>')
        return out
    S.append(("btx", "Битрикс", f"""
<h2>Что мы по Caterpillar уже отдавали в сделки</h2>
<div class="kpi">
<div><b>{num(bs.get('sold'))}</b><span>позиций продавали</span></div>
<div><b>{num(bs.get('quoted'))}</b><span>позиций квотировали</span></div>
<div><b>{num(bs.get('sold_r1700', 0) + bs.get('quoted_r1700', 0))}</b><span>из них по R1700G</span></div>
<div><b>{num(bs.get('prices'))}</b><span>ценовых фактов в базе</span></div>
</div>
<div class="card"><b>Режим сбора</b><div class="txt">{'живой — портал отвечал' if btx.get('live') else 'офлайн — по снимкам в репозитории; живой пас делается там, где задан BITRIX_WEBHOOK_URL'}</div>
<div class="mut">{e(btx.get('note'))}</div></div>
<h3>Продавали</h3>{deal_rows(btx.get('sold') or [], 'продавали') or '<div class="mut">нет</div>'}
<h3>Квотировали</h3>{deal_rows(btx.get('quoted') or [], 'квотировали') or '<div class="mut">нет</div>'}
<h3>Исходы сделок</h3>
<table class="ot" style="max-width:480px"><thead><tr><th>Исход</th><th>Сделок</th></tr></thead><tbody>
{"".join(f'<tr><td>{e(k)}</td><td class="n">{num(v)}</td></tr>' for k, v in (bs.get('deal_outcomes') or {}).items())}
</tbody></table>"""))

    # ── сборка ──────────────────────────────────────────────────────────────
    tabs = "".join(f'<button data-s="{sid}"{" class=on" if i == 0 else ""}>{e(t)}</button>'
                   for i, (sid, t, _) in enumerate(S))
    secs = "".join(f'<section id="{sid}"{" class=on" if i == 0 else ""}>{h}</section>'
                   for i, (sid, _, h) in enumerate(S))

    # данные для таблиц с фильтрами — плоско, чтобы JS не разбирал вложенность
    pjs = [{
        "pn": p["pn"], "name_ru": p.get("name_ru"), "node": p.get("node"), "applic": p.get("applic"),
        "qty": p.get("qty"), "interval": p.get("interval"), "kvs": ", ".join(p.get("kv") or []),
        "nalt": len(p.get("alts") or []), "price": p.get("price_usd") or (
            f"{p.get('price_eur_min')}–{p.get('price_eur_max')} EUR" if p.get("price_eur_min") else ""),
        # Спутник для сортировки: в колонке цены лежит строка, а строки
        # сравниваются посимвольно — «9787» оказывалось выше «1 240.88».
        # Пусто там, где строка не является ценой целиком («99.80 OEM / 62.00
        # аналог», «цена по запросу»): такие позиции уходят в конец обоих
        # направлений, а не притворяются дешёвыми.
        "price_n": price_num(p.get("price_usd")) if price_num(p.get("price_usd")) is not None
        else (p.get("price_eur_min") or None),
        # Валюта нужна самой сортировке: 0.09 EUR и 0.41 USD — не одна шкала.
        # Сравниваем только внутри валюты, доллар идёт первой группой (320 строк
        # против 40 евровых), евро второй, строки без числа — последними.
        "price_cur": "USD" if price_num(p.get("price_usd")) is not None
        else ("EUR" if p.get("price_eur_min") else None),
        "qty_n": price_num(p.get("qty")),
        "bitrix_status": p.get("bitrix_status") or "", "confidence": p.get("confidence"),
        "verdict": p.get("verdict"), "sources": p.get("sources") or [],
        "alts": p.get("alts") or [], "note": p.get("note") or "",
        "pp": p.get("pp"), "opendb": p.get("opendb_signal") or "",
        "deals": p.get("bitrix_deals") or [], "facts": p.get("price_facts") or [],
    } for p in parts]

    js = """
const P=__P__,A=__A__;
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));b.classList.add("on");
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on",s.id===b.dataset.s));
  window.scrollTo({top:0});});
function opts(sel,vals,label){sel.innerHTML='<option value="">'+label+'</option>'+
  [...new Set(vals.filter(Boolean))].sort().map(v=>`<option>${esc(v)}</option>`).join("");}
const tagc=c=>({high:"ok",med:"wr",low:"no"}[c]||"wr");
const tagru=c=>({high:"высокое",med:"среднее",low:"низкое"}[c]||c||"—");
const vtag=v=>!v?'<span class="mut">—</span>':
  `<span class="tag ${{"подтверждён":"ok","наша база":"ok","исправлен":"wr","сомнителен":"wr","снят":"no"}[v]||"wr"}">${esc(v)}</span>`;
// ── запчасти
let sp={k:"pn",d:1};
function rowsP(){const q=$("qp").value.toLowerCase(),nd=$("fnode").value,vr=$("fver").value,cf=$("fconf").value,
  al=$("falt").checked,kv=$("fkv").checked;
  let r=P.filter(x=>(!nd||x.node===nd)&&(!vr||x.verdict===vr)&&(!cf||x.confidence===cf)&&
    (!al||x.nalt>0)&&(!kv||x.kvs)&&
    (!q||[x.pn,x.name_ru,x.node,x.applic,x.kvs,x.note,(x.alts||[]).map(a=>a.brand+" "+a.pn).join(" ")]
      .join(" ").toLowerCase().includes(q)));
  // Колонки с числовым спутником сортируем числом, остальные — строкой.
  // Строки без числа (оговорка, «цена по запросу») уходят вниз при любом
  // направлении: иначе по убыванию они заняли бы место самых дорогих.
  const NUM={price:"price_n",qty:"qty_n",nalt:"nalt"};
  const CUR={USD:0,EUR:1};
  r.sort((a,b)=>{const nk=NUM[sp.k];
    if(nk){const A=a[nk],B=b[nk];
      if(A==null&&B==null)return 0; if(A==null)return 1; if(B==null)return -1;
      if(nk==="price_n"){const ca=CUR[a.price_cur]??9,cb=CUR[b.price_cur]??9;
        if(ca!==cb)return ca-cb;}   // разные валюты не сравниваем, а разводим
      return (A-B)*sp.d;}
    const A=a[sp.k]??"",B=b[sp.k]??"";return (A>B?1:A<B?-1:0)*sp.d;});return r;}
function drawP(){const r=rowsP();
  $("tp").tBodies[0].innerHTML=r.map((x,i)=>`<tr data-i="${P.indexOf(x)}">
   <td class="pn">${esc(x.pn)}</td><td>${esc(x.name_ru)}</td><td>${esc(x.node)}</td>
   <td class="mut">${esc((x.applic||"").slice(0,70))}</td><td class="n">${esc(x.qty)}</td>
   <td class="n">${esc(x.interval)}</td><td class="pn">${esc(x.kvs)}</td>
   <td class="n">${x.nalt||""}</td><td class="n">${esc(x.price)}</td>
   <td>${x.bitrix_status?`<span class="tag ${x.bitrix_status==="продавали"?"ok":"wr"}">${esc(x.bitrix_status)}</span>`:""}</td>
   <td><span class="tag ${tagc(x.confidence)}">${esc(tagru(x.confidence))}</span></td>
   <td>${vtag(x.verdict)}</td></tr>`).join("");
  $("cntp").textContent=`${r.length} из ${P.length} · с аналогом ${r.filter(x=>x.nalt).length} · наших ${r.filter(x=>x.kvs).length}`;}
$("tp").addEventListener("click",ev=>{const tr=ev.target.closest("tr[data-i]");if(!tr)return;
  const x=P[+tr.dataset.i];
  $("pdet").innerHTML=`<div class="card"><b>${esc(x.pn)} — ${esc(x.name_ru)}</b>
   <div class="txt">${esc(x.applic)}</div>${x.note?`<div class="txt">${esc(x.note)}</div>`:""}
   ${x.opendb?`<div class="txt"><b>Кросс в открытых БД:</b> ${esc(x.opendb)}</div>`:""}
   ${x.alts.length?`<div class="txt"><b>Аналоги:</b> ${x.alts.map(a=>`${esc(a.brand)} ${esc(a.pn)}<span class="mut"> (${esc(a.kind)})</span>`).join(" · ")}</div>`:""}
   ${x.facts.length?`<div class="txt"><b>Цены в нашей базе:</b> ${x.facts.map(f=>`${esc(f.price)} ${esc(f.cur)} — ${esc(f.seller)}`).join(" · ")}</div>`:""}
   ${x.deals.length?`<div class="txt"><b>Сделки:</b> ${x.deals.map(dd=>`${esc(dd.date)} ${esc(dd.client)} — ${esc(dd.status)}`).join(" · ")}</div>`:""}
   <div class="mut">${x.sources.map(s=>s.startsWith("http")?`<a href="${esc(s)}" target="_blank" rel="noopener">источник</a>`:esc(s)).join(" · ")}</div></div>`;
  $("pdet").scrollIntoView({block:"nearest"});});
// ── аналоги
function rowsA(){const q=$("qa").value.toLowerCase(),b=$("fbrand").value;
  return A.filter(x=>(!b||x.brand===b)&&(!q||[x.pn,x.alt_pn,x.brand,x.kind,x.note].join(" ").toLowerCase().includes(q)));}
function drawA(){const r=rowsA();
  $("ta").tBodies[0].innerHTML=r.slice(0,4000).map(x=>`<tr><td class="pn">${esc(x.pn)}</td>
   <td>${esc(x.brand)}</td><td class="pn">${esc(x.alt_pn)}</td><td>${esc(x.kind)}</td>
   <td class="mut">${esc(x.note)}</td></tr>`).join("");
  $("cnta").textContent=`${r.length} кроссов`+(r.length>4000?" (показаны 4000)":"");}
function csv(name,rows,cols){const q=v=>'"'+String(v==null?"":v).replace(/"/g,'""')+'"';
  const b=new Blob(["\\ufeff"+[cols.join(";"),...rows.map(r=>cols.map(c=>q(r[c])).join(";"))].join("\\n")],
    {type:"text/csv;charset=utf-8"});
  const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=name;a.click();}
opts($("fnode"),P.map(x=>x.node),"Узел: все");
opts($("fver"),P.map(x=>x.verdict),"Проверка: любая");
opts($("fconf"),P.map(x=>x.confidence),"Доверие: любое");
opts($("fbrand"),A.map(x=>x.brand),"Бренд: все");
["qp","fnode","fver","fconf","falt","fkv"].forEach(id=>$(id).oninput=drawP);
["qa","fbrand"].forEach(id=>$(id).oninput=drawA);
$("tp").querySelectorAll("th[data-k]").forEach(t=>t.onclick=()=>{
  const k=t.dataset.k;sp={k,d:sp.k===k?-sp.d:1};drawP();});
$("csvp").onclick=()=>csv("r1700_parts.csv",rowsP(),
  ["pn","name_ru","node","applic","qty","interval","kvs","nalt","price","bitrix_status","confidence","verdict"]);
$("csva").onclick=()=>csv("r1700_crossrefs.csv",rowsA(),["pn","brand","alt_pn","kind","note"]);
drawP();drawA();
// глубокая ссылка из карточки позиции базы ЗИП: r1700.html#pn=1R-1808 — открыть на этой детали
function openByHash(){const h=new URLSearchParams((location.hash||"").replace(/^#/,""));
  const pn=h.get("pn");if(!pn)return;
  document.querySelector('nav button[data-s="parts"]').click();
  $("qp").value=pn;drawP();
  // поиск по подстроке ловит и примечания соседних строк, поэтому карточку
  // открываем по точному совпадению номера, а первую строку берём только если
  // точного нет: иначе ссылка на 1R-1808 открывала карточку 174-2032.
  const rs=[...$("tp").tBodies[0].querySelectorAll("tr[data-i]")];
  const want=pn.trim().toUpperCase();
  const tr=rs.find(r=>(r.cells[0]||{}).textContent.trim().toUpperCase()===want)||rs[0];
  if(tr){tr.click();tr.scrollIntoView({block:"center"});}}
// вторая ссылка вида #pn=… с этой же страницы меняет только хеш, документ не
// перезагружается — без hashchange карточка осталась бы от первой ссылки.
openByHash();addEventListener("hashchange",openByHash);
// ── живой поиск по карточкам и простым таблицам
function cards(inp,list,cnt){const el=$(inp);if(!el)return;const f=()=>{const q=el.value.toLowerCase();let n=0;
  $(list).querySelectorAll(":scope > .card").forEach(c=>{
    const hit=!q||c.textContent.toLowerCase().includes(q);c.style.display=hit?"":"none";if(hit)n++;});
  $(cnt).textContent=n+" из "+$(list).querySelectorAll(":scope > .card").length;};el.oninput=f;f();}
["qdl,listdl,cntdl","qaf,listaf,cntaf","qodm,listodm,cntodm","qtr,listtr,cnttr"]
  .forEach(s=>{const [a,b,c]=s.split(",");cards(a,b,c);});
function trows(inp,tbl,cnt){const el=$(inp);if(!el)return;const f=()=>{const q=el.value.toLowerCase();let n=0;
  $(tbl).tBodies[0].querySelectorAll("tr").forEach(r=>{
    const hit=!q||r.textContent.toLowerCase().includes(q);r.style.display=hit?"":"none";if(hit)n++;});
  $(cnt).textContent=n+" строк";};el.oninput=f;f();}
trows("qd","td","cntd");trows("qpr","tpr","cntpr");trows("qf","tf","cntf");
"""
    js = js.replace("__P__", J(pjs)).replace("__A__", J(d["alts"]))

    extra = """
.bar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:8px 0}
.bar input,.bar select{border:1px solid var(--border);background:var(--surface);color:var(--ink);
border-radius:8px;padding:5px 9px;font:13px system-ui}.bar input{min-width:230px}
.exp{border:1px solid var(--border);background:var(--surface);color:var(--ink2);border-radius:8px;
padding:5px 9px;font:600 12px system-ui;cursor:pointer}
table.ot{border-collapse:collapse;width:100%;font-size:12px}
table.ot th{text-align:left;color:var(--mut);font-weight:600;border-bottom:1px solid var(--grid);
padding:4px 7px;white-space:nowrap;cursor:pointer}
table.ot td{border-bottom:1px solid var(--grid);padding:3px 7px;vertical-align:top}
table.ot tbody tr:hover{background:var(--chip)}
td.pn{font-family:ui-monospace,"DejaVu Sans Mono",monospace;white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.tag{display:inline-block;border-radius:6px;padding:0 6px;font-size:11px;font-weight:600;
border:1px solid var(--border)}
.tag.ok{background:rgba(12,163,12,.12);color:var(--good)}
.tag.wr{background:rgba(250,178,25,.14);color:#8a6200}
.tag.no{background:rgba(208,59,59,.12);color:var(--crit)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
@media print{nav{display:none}section{display:block!important}}
"""

    html_doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caterpillar R1700G — запчасти, документация, каналы закупки</title>
<style>{CSS}{extra}</style></head><body>
<header>
<div class="mut"><a href="./">← ГШО · рабочая база</a> · <a href="./orders/r1700-sourcing.pdf">печатный отчёт (PDF)</a>
 · <a href="./search.html">поиск детали по номеру</a></div>
<h1>Caterpillar R1700G — досье машины для торгов</h1>
<div class="sub">Погрузочно-доставочная машина подземных горных работ. Собрано {e(d['updated'])}:
{num(s['parts'])} деталей, {num(s['alts'])} кроссов по {num(s['alt_brands'])} брендам,
{num(s['docs'])} документов, {num(s['orgs'])} организаций-каналов, {num(s['prices'])} ценовых ориентиров,
{num(s['customs_rows'])} строк таможни и {num(s['odm_candidates'])} заводов-кандидатов.
Источников — {num(s['sources'])}.</div>
</header>
<nav>{tabs}</nav><main>{secs}</main>
<script>{js}</script></body></html>"""

    OUT.mkdir(exist_ok=True)
    p = OUT / "r1700.html"
    p.write_text(html_doc, encoding="utf-8")
    print(f"zip/public/r1700.html: {p.stat().st_size:,} байт | деталей {len(parts)}, кроссов {len(d['alts'])}, "
          f"документов {len(d.get('docs') or [])}, организаций {len(d['orgs'])}, разделов {len(S)}")
    return p


if __name__ == "__main__":
    build()
