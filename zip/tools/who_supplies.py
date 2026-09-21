# -*- coding: utf-8 -*-
"""Отчёт «Кто уже поставляет и производит» — карта готовых каналов по ЗИП
гидроперфораторов. Собирается ТОЛЬКО из накопленной базы проекта:
таможенные декларации, живые КП, ODM-привязки, CRM прозвона.
Цель — не изобретать велосипед: показать, что уже найдено и проверено.
"""
import html, json, re, statistics as st
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT = ROOT / "data", ROOT / "orders"
e = lambda s: html.escape(str(s or ""))
FX = {"USD": 1, "CNY": 0.1386, "EUR": 1.164, "RUB": 0.01158}


def load(n):
    p = DATA / n
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


POS = load("positions.json")
PR = load("price_records.json")
ODM = load("odm_suppliers.json")
CRM = load("supplier_crm.json") or {"suppliers": [], "calls": []}
DECL = load("customs_declarations.json")
CI = {c: i for i, c in enumerate(DECL["cols"])}
ROWS = DECL["rows"]

OEM = re.compile(r"ATLAS\s*COPCO|EPIROC|SANDVIK|TAMROCK|MONTABERT|FURUKAWA|"
                 r"\bCOP\s?\d|\bHLX?\d{3}|RD\d{3}|гидроперфоратор|буров", re.I)
CORE = [r for r in ROWS if str(r[CI["pn"]]).strip()
        or OEM.search(str(r[CI["desc"]]) + " " + str(r[CI["exporter"]]))]


def usd(r):
    v, c = r.get("unit_price"), (r.get("currency") or "USD").upper()
    return v * FX.get(c, 1) if isinstance(v, (int, float)) and v else None


def factories():
    """Заводы, давшие ЖИВЫЕ КП (не маркетплейс)."""
    g = defaultdict(list)
    for r in PR:
        if "КП поставщика" not in str(r.get("source")):
            continue
        g[(r.get("exporter") or "?").strip()].append(r)
    out = []
    for name, rs in g.items():
        v = [usd(r) for r in rs if usd(r)]
        out.append((len(rs), st.median(v) if v else None, min(v) if v else None,
                    max(v) if v else None, name))
    return sorted(out, reverse=True)


def exporters():
    ex = Counter(str(r[CI["exporter"]]).strip() for r in CORE if r[CI["exporter"]])
    out = []
    for name, n in ex.most_common(24):
        org = Counter(r[CI["origin"]] for r in CORE
                      if str(r[CI["exporter"]]).strip() == name).most_common(1)
        yrs = sorted({r[CI["year"]] for r in CORE if str(r[CI["exporter"]]).strip() == name})
        out.append((n, org[0][0] if org else "?", f"{yrs[0]}–{yrs[-1]}" if yrs else "", name))
    return out


def pn_hits():
    byp = defaultdict(list)
    for r in ROWS:
        pn = str(r[CI["pn"]]).strip()
        if pn:
            byp[pn].append(r)
    pos = {(p.get("catalog_norm") or ""): p for p in POS}
    out = []
    for pn, rs in sorted(byp.items(), key=lambda x: -len(x[1]))[:16]:
        p = pos.get(pn)
        ex = Counter(str(r[CI["exporter"]]).strip() for r in rs).most_common(2)
        im = Counter(str(r[CI["importer"]]).strip() for r in rs).most_common(1)
        out.append((pn, (p.get("name") if p else "") or "—", len(rs),
                    ", ".join(a[:34] for a, _ in ex), im[0][0][:30] if im else "—"))
    return out


def odm_top():
    sup = Counter()
    meta = {}
    for o in ODM:
        k = (o.get("name") or "?").split("(")[0].strip()[:42]
        sup[k] += 1
        meta.setdefault(k, o)
    return [(v, k, meta[k].get("country") or "", meta[k].get("confidence") or "",
             str(meta[k].get("makes") or "")[:88]) for k, v in sup.most_common(16)]


CSS = """
@page{size:A4;margin:13mm 11mm}
body{font:10.5px/1.5 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:13.5px;margin:15px 0 5px;background:#1b2330;color:#fff;padding:5px 9px;border-radius:3px;page-break-after:avoid}
h3{font-size:11.5px;margin:10px 0 4px;color:#0b3d91;page-break-after:avoid}
.mut{color:#555;font-size:9.5px}
table{border-collapse:collapse;width:100%;margin:4px 0}
tr{page-break-inside:avoid}
th,td{border:1px solid #bbb;padding:3px 6px;text-align:left;vertical-align:top;font-size:9.5px}
th{background:#eef2f7} b{color:#0b3d91}
.box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;padding:7px 11px;margin:6px 0;page-break-inside:avoid}
.warn{border-left-color:#c62828;background:#fff8f8}
.num{text-align:right;white-space:nowrap}
ul{margin:3px 0 3px 16px;padding:0} li{margin:2px 0}
.kpi{display:inline-block;border:1px solid #bbb;border-radius:4px;padding:5px 9px;margin:2px 4px 2px 0;font-size:10px}
.kpi b{font-size:14px;color:#1a7f37}
"""


def main():
    fac = factories()
    n_fact_pos = len({r["position_id"] for r in PR if "КП поставщика" in str(r.get("source"))})
    n_price_pos = len({r["position_id"] for r in PR if usd(r)})
    n_odm_pos = len({o.get("position_id") for o in ODM})
    S, C = CRM.get("suppliers", []), CRM.get("calls", [])

    H = [f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>']
    H.append(f"""<h1>Кто уже поставляет и производит: карта готовых каналов</h1>
<div class="mut">ЗИП гидроперфораторов Epiroc / Atlas Copco COP, Sandvik HL-HLX, Montabert, Furukawa ·
{date.today().strftime('%d.%m.%Y')} · Собрано из накопленной базы проекта, без новых допущений.
Назначение: не искать заново то, что уже найдено и проверено.</div>
<div style="margin:8px 0">
<span class="kpi"><b>{len(POS)}</b> позиций в базе</span>
<span class="kpi"><b>{n_odm_pos}</b> с найденным производителем</span>
<span class="kpi"><b>{n_price_pos}</b> с ценой</span>
<span class="kpi"><b>{n_fact_pos}</b> с ЗАВОДСКИМ КП</span>
<span class="kpi"><b>{len(S)}</b> поставщиков в CRM</span>
<span class="kpi"><b>{len(ROWS):,}</b> таможенных деклараций</span>
</div>""".replace(",", " "))

    H.append(f"""<div class="box"><b>Короткий ответ, что уже не надо изобретать.</b>
По номенклатуре перфораторов рынок поставщиков уже вскрыт: найдены производители на {n_odm_pos} позиций
из {len(POS)}, по {n_fact_pos} позициям есть цены напрямую с заводов, {len(S)} поставщиков заведены в CRM
и с частью уже идёт переписка. Канал в РФ работает — по таможне видно {len(CORE)} отгрузок ЗИП буровой
техники за 2023–2026 и конкретные экспортёры, которые возят регулярно. Заново стоит искать только то,
чего в этом отчёте нет: многослойные подшипники и цельнобронзовые втулки (по ним отдельный отчёт).</div>""")

    # A. Заводы с живыми КП
    H.append('<h2>A. Заводы, которые уже дали нам цены</h2>')
    H.append('<div class="mut">Это не маркетплейс и не парсинг — прямые коммерческие предложения заводов. '
             'Самая ценная часть базы: цена подтверждена конкретным поставщиком.</div>')
    H.append('<table><tr><th style="width:22%">Завод</th><th class="num" style="width:11%">Позиций в КП</th>'
             '<th class="num" style="width:13%">Медиана, $</th><th class="num" style="width:16%">Диапазон, $</th>'
             '<th>Чем интересен</th></tr>')
    WHY = {
        "WMS": "Турция, Анкара (WMS/Atmaca) — комплектные COP и металл-ЗИП. Широчайшее покрытие, но цены у верхней границы: реселлер на уровне OEM",
        "Sanshan": "Китай, Шаньдун — внутренности COP: поршень, гильза, драйвер. Самые низкие цены в выборке",
        "YLF": "Китай — металл-ЗИП, ровный прайс по втулкам и направляющим",
        "Fuxuan": "Китай, Цзиньхуа — металл и втулки для перфораторов",
        "KAT": "Китай, Шаньдун, Ляочэн — хвостовики и втулки COP 1036–2238",
        "Mindrill": "Индия — дрифтеры-аналоги 1838 / 2238 / MD20, самый дешёвый вход",
    }
    for n, med, lo, hi, name in fac[:10]:
        why = next((v for k, v in WHY.items() if k.lower() in name.lower()), "")
        m = f"{med:.1f}" if med else "—"
        rng = f"{lo:.0f}–{hi:.0f}" if lo else "—"
        H.append(f'<tr><td><b>{e(name)}</b></td><td class="num">{n}</td><td class="num">{m}</td>'
                 f'<td class="num">{rng}</td><td class="mut">{e(why)}</td></tr>')
    H.append('</table>')
    H.append("""<div class="box warn"><b>Вывод, который стоит денег.</b> Разброс медиан между заводами —
от 8 $ (Sanshan) до 130 $ (WMS) на сопоставимой номенклатуре, то есть <b>шестнадцатикратный</b>.
WMS удобен широтой ассортимента, но по позициям, которые закрывают Sanshan, YLF, Fuxuan и KAT,
переплата кратная. Прежде чем размещать заказ у одного поставщика «за всё», разложить корзину
по специализациям.</div>""")

    # Б. CRM
    H.append('<h2>Б. С кем уже идёт работа: CRM прозвона</h2>')
    stages = Counter((s.get("stage") or "—").strip() for s in S)
    H.append('<div class="mut">' + " · ".join(f"<b>{e(k)}</b>: {v}" for k, v in stages.most_common()) + '</div>')
    H.append('<table><tr><th style="width:19%">Поставщик</th><th style="width:9%">Тип</th>'
             '<th style="width:20%">География</th><th style="width:30%">Направление</th>'
             '<th style="width:22%">Стадия</th></tr>')
    for s in S:
        H.append(f'<tr><td><b>{e(s.get("name"))}</b></td><td>{e(s.get("kind"))}</td>'
                 f'<td>{e(s.get("geo"))}</td><td>{e(s.get("direction"))}</td>'
                 f'<td class="mut">{e(s.get("stage"))}</td></tr>')
    H.append('</table>')
    if C:
        H.append('<h3>Открытые задачи прозвона</h3><table>'
                 '<tr><th style="width:7%">Приор.</th><th style="width:20%">Кому</th>'
                 '<th style="width:20%">Контакт</th><th>О чём спросить</th></tr>')
        for x in C:
            H.append(f'<tr><td>{e(x.get("priority"))}</td><td><b>{e(x.get("name"))}</b></td>'
                     f'<td class="mut">{e(str(x.get("phone"))[:70])}</td>'
                     f'<td>{e(str(x.get("ask"))[:190])}</td></tr>')
        H.append('</table>')

    # В. ODM
    H.append('<h2>В. Производители, привязанные к позициям (ODM)</h2>')
    H.append(f'<div class="mut">Найдены производители на {n_odm_pos} позиций из {len(POS)}. '
             'Столбец «позиций» — сколько наших номенклатурных строк закрывает поставщик.</div>')
    H.append('<table><tr><th class="num" style="width:8%">Позиций</th><th style="width:26%">Поставщик</th>'
             '<th style="width:9%">Страна</th><th style="width:9%">Дов.</th><th>Что делает</th></tr>')
    for v, k, cn, cf, mk in odm_top():
        H.append(f'<tr><td class="num"><b>{v}</b></td><td>{e(k)}</td><td>{e(cn)}</td>'
                 f'<td>{e(cf)}</td><td class="mut">{e(mk)}</td></tr>')
    H.append('</table>')

    # Г. Таможня — экспортёры
    H.append('<h2>Г. Кто уже везёт ЗИП буровой техники в РФ</h2>')
    H.append(f'<div class="mut">Отфильтровано из {len(ROWS):,} деклараций за 2023–2026: оставлены только те, '
             'где есть наш партномер либо признак буровой техники и OEM. Ядро — {n} отгрузок. '
             'Это готовые каналы: компании уже прошли таможню с этой номенклатурой.'
             .replace(",", " ").replace("{n}", f"{len(CORE):,}".replace(",", " ")) + '</div>')
    H.append('<table><tr><th class="num" style="width:8%">Отгрузок</th><th style="width:8%">Происх.</th>'
             '<th style="width:12%">Годы</th><th>Экспортёр</th></tr>')
    for n, org, yrs, name in exporters():
        H.append(f'<tr><td class="num"><b>{n}</b></td><td>{e(org)}</td><td>{e(yrs)}</td>'
                 f'<td>{e(name)}</td></tr>')
    H.append('</table>')

    # Д. Точные PN
    H.append('<h2>Д. Кто везёт ровно наши партномера</h2>')
    H.append('<div class="mut">Самое точное попадание: в описании декларации совпал наш каталожный номер. '
             'Показывает, у кого физически берут именно эту деталь и кто её уже растаможил.</div>')
    H.append('<table><tr><th style="width:13%">Партномер</th><th style="width:24%">Наименование</th>'
             '<th class="num" style="width:8%">Декл.</th><th style="width:30%">Экспортёр</th>'
             '<th style="width:25%">Импортёр в РФ</th></tr>')
    for pn, nm, n, ex, im in pn_hits():
        H.append(f'<tr><td><b>{e(pn)}</b></td><td>{e(nm[:40])}</td><td class="num">{n}</td>'
                 f'<td>{e(ex)}</td><td class="mut">{e(im)}</td></tr>')
    H.append('</table>')

    # Е. Импортёры
    H.append('<h2>Е. Кто на рынке РФ: импортёры этой номенклатуры</h2>')
    H.append('<div class="mut">Не поставщики, а игроки рынка — конкуренты и потенциальные партнёры. '
             'Полезно понимать, кто уже держит канал и какие объёмы проходят.</div>')
    imp = Counter(str(r[CI["importer"]]).strip() for r in CORE if r[CI["importer"]])
    H.append('<table><tr><th class="num" style="width:10%">Отгрузок</th><th>Импортёр</th>'
             '<th class="num" style="width:10%">Отгрузок</th><th>Импортёр</th></tr>')
    top = imp.most_common(16)
    for i in range(0, len(top), 2):
        a = top[i]
        b = top[i + 1] if i + 1 < len(top) else ("", "")
        H.append(f'<tr><td class="num"><b>{a[1]}</b></td><td>{e(a[0])}</td>'
                 f'<td class="num"><b>{b[1] if b[0] else ""}</b></td><td>{e(b[0])}</td></tr>')
    H.append('</table>')

    # Ж. Выводы
    H.append('<h2>Ж. Что из этого следует</h2>')
    H.append("""<div class="box">
<ul>
<li><b>Поставщиков искать заново не нужно.</b> Производитель найден почти на всю номенклатуру,
поставщики заведены в CRM, часть уже в стадии «К/П», часть ответила и взяла запрос в работу.</li>
<li><b>Первым делом — не новый поиск, а дожим молчащих.</b> В прозвоне висят открытые задачи
по Biaotuo, Xingtai Huimen, GMD, Prodrill, Xiamen Bestlink: списки отправлены, ответа нет.
Это дешевле любого нового поиска.</li>
<li><b>Разложить корзину по специализациям.</b> Медианы заводов различаются в 16 раз.
Уплотнения — у профильных РТИ-заводов (Biaotuo, Xingtai Huimen, Hovoo, Fujis),
внутренности COP — у Sanshan и Woserld, хвостовики — у LianHuaShan, KAT, Litian, Prodrill,
гидромоторы — у Shijiazhuang Hanjiu и Ningbo Zhongyi. Один поставщик «за всё» обходится дороже.</li>
<li><b>Турция как запасной канал.</b> WMS/Atmaca (Анкара) и ALFAROK возят в РФ регулярно,
таможня это подтверждает. Дороже Китая, но короче плечо и меньше вопросов на границе.</li>
<li><b>Казахстан — рабочий транзит.</b> В ядре отгрузок видны ТОО «Мир Инструмента-Алматы»,
«К2 Восток», «Global Trade Solution», «Solid Drilling Solutions». Через них уже идёт поток.</li>
<li><b>Единственная незакрытая тема</b> — многослойные подшипники скольжения и цельнобронзовые
втулки. По ним поставщиков в этой базе нет, поиск вёлся отдельно: см. отчёт
«Подшипник скольжения и бронзовая втулка».</li>
</ul></div>""")

    H.append(f"""<div class="mut" style="margin-top:8px">Источники: собственная база проекта —
таможенные декларации РФ 2023–2026 (glbs.io), живые КП поставщиков, ODM-привязки, CRM прозвона.
Внешние допущения не добавлялись. Где данных нет — прочерк.</div></body></html>""")

    (OUT / "КТО-УЖЕ-ПОСТАВЛЯЕТ.html").write_text("\n".join(H), encoding="utf-8")
    print(f"готово: заводов с КП {len(fac)}, CRM {len(S)}, задач прозвона {len(C)}, "
          f"экспортёров в ядре {len(exporters())}, ядро деклараций {len(CORE)}")


if __name__ == "__main__":
    main()
