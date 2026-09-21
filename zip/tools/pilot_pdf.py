#!/usr/bin/env python3
"""Единый PDF для закупок: Пилотный заказ №1 — рекомендации, материалы, реалистичность цен.

Генерирует zip/orders/pilot_report.html (печатная вёрстка);
PDF печатается chromium-скриптом print_pdf.mjs.
"""
import json
import html
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "orders"
CNY = 0.138
e = lambda s: html.escape(str(s if s is not None else ""))

# класс → (материал изготовления, контроль на приёмке)
MATERIAL = {
    "Уплотнение/O-ring NBR": ("Резина NBR 70-90 ShA (OEM для динамики — HNBR/FKM)",
        "твёрдость по Шору, геометрия; для динамических — требовать HNBR"),
    "Кольцо (резин.)": ("NBR 70-90 ShA", "твёрдость по Шору, отсутствие облоя"),
    "Манжета/скребок PU": ("Полиуретан AU 92-95 ShA (OEM: MDI/PPDI-PU)",
        "твёрдость, эластичность; дешёвый TPU быстро «плывёт» в гидравлике"),
    "Скребок-стеклоочиститель": ("PU-профиль", "твёрдость, геометрия кромки"),
    "Диафрагма HNBR армированная": ("HNBR 44-50% ACN, тканевое армирование (OEM: Therban/Zetpol)",
        "ИК/ТГА-проверка что это HNBR а не дешёвый NBR; наличие армирования на срезе"),
    "Комплект уплотнений": ("Смесь: NBR-кольца + PU-манжеты + опорные PTFE",
        "покомпонентная проверка; худший элемент определяет ресурс комплекта"),
    "Метиз/пружина": ("35CrMo/40Cr, класс прочности 10.9-12.9; пружины 60Si2Mn",
        "твёрдость, момент затяжки на образце; сертификат класса прочности"),
    "Кольцо стопорное/распорное (сталь)": ("Пружинная сталь 65Г/60Si2Mn + ТО", "твёрдость, упругость"),
    "Сапун": ("Сталь + фильтроэлемент", "проходимость, резьба"),
    "Клапан/седло (мелкое)": ("Инструментальная/подшипниковая сталь, 58-62 HRC",
        "твёрдость, плоскостность седла — герметичность"),
    "Втулка поршня (цементуемая)": ("Цементуемая Cr-Ni-Mo (18CrNiMo7-6; кит. аналог 20CrNiMo/20CrMnTi)",
        "глубина слоя 1.2-1.8 мм, 58-62 HRC поверхн., сердцевина 30-45 HRC; сертификат плавки"),
    "Втулка направляющая/букса": ("Цементуемая Cr-Ni-Mo, вариант — бронза БрАЖ для пар трения",
        "глубина слоя, твёрдость, посадочные размеры"),
    "Втулка прочая": ("Легированная сталь 40Cr/42CrMo + ТО", "твёрдость, размеры"),
    "Поршень-ударник": ("OEM: 18CrNiMo7-6/Sanbar-класс; кит. предложат 40CrNiMoA/23CrNi3Mo",
        "КРИТИЧНО: сертификат плавки, УЗК на флокены, твёрдость по сечению, шлифовка Ra≤0.4; техкарта — вкладка «Материалы»"),
    "Гильза цилиндра": ("Легированная поковка/труба + ХТО, хонинговка",
        "КРИТИЧНО: геометрия расточки, УЗК, твёрдость зеркала"),
    "Шток": ("40CrNiMoA/42CrMo + ТВЧ/хромирование",
        "прямолинейность, твёрдость покрытия, толщина хрома"),
    "Муфта": ("Сталь 40Cr + зубообработка + ТО", "твёрдость зубьев, биение"),
    "Драйвер (вращатель)": ("Поковка Cr-Ni-Mo + зубофрезеровка + цементация",
        "КРИТИЧНО: глубина слоя на шлицах, сертификат плавки"),
    "Голова промывки": ("Поковка/пруток легир. стали + ТО", "герметичность каналов, резьбы"),
    "Хвостовик": ("OEM: Sanbar 64; замена — 18CrNiMo7-6 поковка, глубокая цементация",
        "КРИТИЧНО: только по нашей техкарте (вкладка «Материалы»); стендовые испытания"),
    "Гидромотор (узел)": ("Покупной узел (корпус HT250/сталь, ротор — подшипниковая сталь)",
        "стендовая проверка давлением/расходом; это НЕ деталь для копирования в пилоте"),
    "Фильтр-элемент": ("Покупной элемент + корпус", "тонкость фильтрации"),
    "Прочее (мелкая механика)": ("Конструкционная сталь", "размеры, твёрдость"),
}


def load_rows():
    import csv as _csv
    rows = list(_csv.DictReader(open(OUT / "pilot_order_rfq.csv", encoding="utf-8-sig"), delimiter=";"))
    bom = list(_csv.DictReader(open(OUT / "pilot_order_bom_rkd.csv", encoding="utf-8-sig"), delimiter=";"))
    return rows, bom


def factory_quotes():
    """по позициям — лучшее ФАБРИЧНОЕ КП (не WMS-перекуп)."""
    prices = json.loads((DATA / "price_records.json").read_text(encoding="utf-8"))
    fq = {}
    for r in prices:
        if not str(r.get("source", "")).startswith("КП поставщика") or not r.get("unit_price"):
            continue
        if (r.get("exporter") or "") == "WMS":
            continue
        usd = r["unit_price"] * (CNY if r.get("currency") == "CNY" else 1.0)
        pid = r["position_id"]
        if pid not in fq or usd < fq[pid][0]:
            fq[pid] = (usd, r.get("exporter", ""))
    return fq


def main():
    rows, bom = load_rows()
    pos = {p["id"]: p for p in json.loads((DATA / "positions.json").read_text(encoding="utf-8"))}
    pp2id = {str(p.get("pp")): pid for pid, p in pos.items()}
    fq = factory_quotes()

    # ── вердикт реалистичности по классам (данные: фабричные КП vs цель) ──
    cls_stat = {}
    for r in rows:
        c = r["cls"]
        st = cls_stat.setdefault(c, {"n": 0, "tgt": float(r["target"]), "fmin": None, "fsup": "", "est": r["est_cost"]})
        st["n"] += 1
        pid = pp2id.get(str(r["pp"]))
        if pid in fq:
            u, s = fq[pid]
            if st["fmin"] is None or u < st["fmin"]:
                st["fmin"], st["fsup"] = round(u, 2), s
    for c, st in cls_stat.items():
        if st["fmin"] is None:
            st["verdict"], st["vcls"] = "фабрику не котировали — цель проверить RFQ", "warn"
        elif st["fmin"] <= st["tgt"] * 1.6:
            st["verdict"], st["vcls"] = "цена ПОДТВЕРЖДЕНА фабричным КП", "ok"
        else:
            st["verdict"], st["vcls"] = f"фабрика дала ${st['fmin']} — модель занижает, цель скорректировать", "bad"
    # ручные поправки экспертизы (узлы, не детали)
    if "Гидромотор (узел)" in cls_stat:
        cls_stat["Гидромотор (узел)"]["verdict"], cls_stat["Гидромотор (узел)"]["vcls"] = \
            "покупной узел: реальная цена $400-900 — НЕ копировать в пилоте, покупать готовый", "bad"

    take = [r for r in rows if r["verdict"] == "брать по КП"]
    push = [r for r in rows if r["verdict"] != "брать по КП"]
    t_quote = sum(float(r["sum_quote"]) for r in rows)
    t_target = sum(min(float(r["sum_quote"]), float(r["sum_target"])) for r in rows)
    wms_n = sum(1 for r in rows if r["quote_sup"] == "WMS")
    t_bom = sum(float(r["sum_target"]) for r in bom)

    css = """
    @page{size:A4;margin:16mm 14mm}
    body{font:11px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
    h1{font-size:20px;margin:0 0 2px} h2{font-size:14px;margin:18px 0 6px;border-bottom:2px solid #1b2330;padding-bottom:3px;page-break-after:avoid}
    h3{font-size:12px;margin:10px 0 4px;page-break-after:avoid}
    .mut{color:#555} .small{font-size:9.5px}
    table{border-collapse:collapse;width:100%;margin:6px 0;page-break-inside:auto}
    tr{page-break-inside:avoid} th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top}
    th{background:#eef2f7;font-size:10px} td{font-size:10px}
    .cn{text-align:center;white-space:nowrap} .rt{text-align:right;white-space:nowrap}
    .ok{background:#e7f6ec} .warn{background:#fff7dd} .bad{background:#fde8e8}
    .kpi{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
    .kpi div{border:1px solid #ccc;border-radius:6px;padding:6px 12px;text-align:center}
    .kpi b{display:block;font-size:16px}
    .box{border:1px solid #ccc;border-left:4px solid #1b2330;border-radius:4px;padding:7px 10px;margin:6px 0;page-break-inside:avoid}
    .red{border-left-color:#c62828} .grn{border-left-color:#1a7f37}
    ol li,ul li{margin:2px 0}
    .pb{page-break-before:always}
    """
    H = [f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<h1>Пилотный заказ №1 — ЗИП гидроперфораторов</h1>
<div class="mut">Рекомендации для закупок · {date.today().strftime('%d.%m.%Y')} · КВАНТ, проект локализации ·
основа: 76 позиций с живыми КП поставщиков + 10 деталей с готовым РКД (COP 3060MUX) · целевые цены = расчётная себестоимость производства +20%</div>

<div class="kpi">
<div><b>76</b>позиций к заказу</div><div><b>21</b>брать по КП</div><div><b>55</b>торговаться до цели</div>
<div><b>${t_quote:,.0f}</b>сумма по текущим КП</div><div><b>${t_target:,.0f}</b>сумма по целевым</div>
<div><b>10</b>деталей по РКД (${t_bom:,.0f})</div>
</div>

<h2>1. Общие рекомендации</h2>
<div class="box red"><b>Главное: {wms_n} из 76 лучших КП — перекупщик WMS с ценами уровня OEM</b> (шпилька $110 при себестоимости $0.8;
кольцо $308 при фабричной цене $0.3 у Sanshan). Эти цены — не рынок производства, а маржа посредника.
<b>Не принимать.</b> По всем WMS-позициям слать RFQ фабрикам с нашей целевой ценой.</div>
<ol>
<li><b>Целевая цена в переговорах</b> — «наша расчётная себестоимость +20%» (колонка «Цель» в таблицах). Фабричные КП Sanshan подтверждают порядок цифр.</li>
<li><b>Пул поставщиков:</b> Sanshan (фабрика, металл+РТИ, 29 фабричных КП — базовый кандидат) · YLF, Fuxuan (РТИ — для конкуренции) · KAT (втулки). На каждый лот — минимум 2 КП.</li>
<li><b>Разбить заказ на 2 риск-корзины:</b> (А) РТИ/метизы/мелочь — заказывать сразу, брак некритичен; (Б) ответственные детали (поршень, гильза, шток, втулки, драйвер) — сначала образцы 1-2 шт + входной контроль материала, потом партия.</li>
<li><b>Материал:</b> все текущие фабричные КП — на <u>китайском материале</u>. Для корзины (Б) параллельно запросить цену на импортном (18CrNiMo7-6 / 40CrNiMoA ESR) — ожидаемо +40-60%.</li>
<li><b>Цены EXW:</b> до склада РФ добавить логистику/пошлину/НДС ≈ +25-40%. Даже с этим маржа против OEM-рынка кратная.</li>
<li><b>Оплата:</b> 30% аванс / 70% по фото готовой продукции и отчёту инспекции; новых поставщиков — через Trade Assurance/аккредитив.</li>
<li><b>Гидромотор — не копировать:</b> это покупной узел ($400-900 реально), в пилот не включать как «изготовление».</li>
</ol>

<h2>2. Анализ материалов и входной контроль</h2>
<div class="mut small">Из чего реально изготавливаются классы деталей заказа, что предложат китайские фабрики, и что проверять на приёмке. Полные техкарты и протоколы испытаний — вкладка «Материалы» на kvant-zip.pages.dev.</div>
<table><tr><th>Класс детали</th><th>Материал (OEM → китайское предложение)</th><th>Входной контроль</th></tr>"""]
    seen = set()
    for r in rows + bom:
        c = r["cls"]
        if c in seen or c not in MATERIAL:
            continue
        seen.add(c)
        m, chk = MATERIAL[c]
        H.append(f"<tr><td><b>{e(c)}</b></td><td>{e(m)}</td><td>{e(chk)}</td></tr>")
    H.append(f"""</table>
<div class="box"><b>Три правила материала для корзины (Б):</b> 1) сертификат плавки (mill certificate) обязателен в контракте;
2) выборочный спектрометр/искровая проба на приёмке — подмена стали это типовой риск;
3) для РТИ — дата вулканизации не старше 12 мес, для мембран требовать подтверждение HNBR (ИК-спектр), не NBR.</div>

<h2>3. Где цена реалистична, где нет</h2>
<div class="mut small">Вердикт по каждому классу: сверка нашей цели (себестоимость+20%) с лучшим ФАБРИЧНЫМ КП (перекуп WMS исключён из сверки).</div>
<table><tr><th>Класс</th><th class="cn">Позиций</th><th class="rt">Себест. (модель)</th><th class="rt">Цель</th><th class="rt">Мин. фабричное КП</th><th>Вердикт</th></tr>""")
    order = {"ok": 0, "warn": 1, "bad": 2}
    for c, st in sorted(cls_stat.items(), key=lambda kv: (order[kv[1]["vcls"]], kv[0])):
        f = f"${st['fmin']} ({e(st['fsup'])})" if st["fmin"] is not None else "—"
        H.append(f"<tr class='{st['vcls']}'><td><b>{e(c)}</b></td><td class='cn'>{st['n']}</td><td class='rt'>${st['est']}</td><td class='rt'><b>${st['tgt']}</b></td><td class='rt'>{f}</td><td>{e(st['verdict'])}</td></tr>")
    H.append("""</table>
<div class="box grn"><b>Зелёное</b> — фабричное КП уже на уровне цели: цена реалистична, заказывать/дожимать чуть-чуть.</div>
<div class="box"><b>Жёлтое</b> — по классу котировал только перекуп: цель основана на модели, проверяется первым фабричным RFQ (риск умеренный — модель откалибрована по соседним классам).</div>
<div class="box red"><b>Красное</b> — модель или постановка занижает (сложные узлы): цель пересмотреть, в пилоте — только образцы или покупной узел.</div>

<h2>4. На что обратить внимание (красные флаги)</h2>
<ul>
<li><b>Худшие цены WMS для отклонения:</b> шток 3115 6001 66 — $1763 (цель $72) · муфта 3115 2031 00 — $1322 (цель $24) · комплект 3115 9362 00 — $749 (цель $14.4) · кольцо 3115 6010 36 — $308 (цель $0.24) · шпильки — $88-110 (цель $0.96).</li>
<li><b>Sanshan котирует на китайском материале</b> (их формулировка в переписке) — для корзины (Б) это стартовая цена, не финальная спецификация.</li>
<li><b>Комплекты уплотнений:</b> цена комплекта скрывает качество компонентов — согласовать состав покомпонентно (какие манжеты PU, какие кольца NBR).</li>
<li><b>Позиции с одним КП</b> — конкуренции нет: не подписывать до второго КП (YLF/Fuxuan закрывают РТИ быстро).</li>
<li><b>Таможенный бенчмарк</b> (22 тыс. деклараций РФ): медиана перфораторного импорта 7.2 USD/кг — грубая проверка «цена/вес» при приёмке КП.</li>
<li><b>Критичные детали (поршень/гильза/хвостовик):</b> ресурс решает термообработка, не только сталь — приёмка по нашей техкарте (цементация: глубина слоя, HRC поверхн./сердцевина, УЗК). Без этого дешёвая деталь = дорогой простой.</li>
</ul>

<h2 class="pb">5. Приложение А — заказ по позициям с КП (76)</h2>
<div class="mut small">Кол-во — пилотная партия. «Цель» — цена для переговоров. Вердикт: ✓ = брать по КП, → = торговаться до цели.</div>
<table><tr><th>П/П</th><th>Артикул</th><th>Деталь</th><th>Класс</th><th class="cn">Кол-во</th><th class="rt">Цель $</th><th class="rt">КП $</th><th>Пост.</th><th class="cn">Верд.</th></tr>""")
    for r in sorted(rows, key=lambda r: (r["verdict"] != "брать по КП", -float(r["sum_quote"]))):
        v = "✓" if r["verdict"] == "брать по КП" else "→"
        cl = "ok" if v == "✓" else ""
        H.append(f"<tr class='{cl}'><td class='cn'>{e(r['pp'])}</td><td class='cn'>{e(r['catalog_no'])}</td><td>{e(r['name'][:44])}</td><td>{e(r['cls'][:22])}</td><td class='cn'>{e(r['qty'])}</td><td class='rt'><b>{e(r['target'])}</b></td><td class='rt'>{e(r['quote_usd'])}</td><td>{e(r['quote_sup'])}</td><td class='cn'>{v}</td></tr>")
    H.append("""</table>
<h2>6. Приложение Б — заказ по готовому РКД (BOM COP 3060MUX, 10)</h2>
<table><tr><th>Артикул КВАНТ</th><th>Epiroc PN</th><th>Деталь</th><th>Класс</th><th class="cn">Кол-во</th><th class="rt">Цель $</th><th class="rt">Сумма $</th></tr>""")
    for r in bom:
        H.append(f"<tr><td class='cn'>{e(r['kv_pn'])}</td><td class='cn'>{e(r['epiroc_pn'])}</td><td>{e(r['desc'][:40])}</td><td>{e(r['cls'][:24])}</td><td class='cn'>{e(r['qty'])}</td><td class='rt'><b>{e(r['target'])}</b></td><td class='rt'>{e(r['sum_target'])}</td></tr>")
    H.append(f"""</table>
<div class="mut small" style="margin-top:14px">Методика: параметрическая оценка себестоимости по классу детали (материал+передел, Китай EXW, китайский материал,
партия = пилот; точность ±40%) × 1.20 = целевая цена. Данные: рабочая таблица снабжения (КП WMS/Sanshan/YLF/Fuxuan/KAT/Mindrill),
инженерный BOM COP 3060MUX, таможенная статистика glbs.io (23 тыс. деклараций), стратегия материалов «крепче OEM».
Живая база: kvant-zip.pages.dev · Сгенерировано {date.today().isoformat()}</div>
</body></html>""")
    (OUT / "pilot_report.html").write_text("\n".join(H), encoding="utf-8")
    print(f"HTML: {OUT/'pilot_report.html'} | классов с вердиктом: {len(cls_stat)}")
    for c, st in sorted(cls_stat.items(), key=lambda kv: order[kv[1]['vcls']]):
        print(f"  [{st['vcls']:4s}] {c}: цель ${st['tgt']} | фабрика {st['fmin']} | {st['verdict'][:60]}")


if __name__ == "__main__":
    main()
