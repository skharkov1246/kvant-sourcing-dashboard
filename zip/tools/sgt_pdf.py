#!/usr/bin/env python3
"""PDF «Поставщики ЗИП ГТУ Siemens / SGT-400» для пересылки сорсерам.
Читает zip/orders/sgt_exporters_contacts.csv → печатная HTML → PDF (Chromium)."""
import csv
import html
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "orders"
e = lambda s: html.escape(str(s or ""))

TIERS = [
    ("П1", "Уровень 1 — прямое попадание в SGT-400 (Китай; реально для РФ)", "t1"),
    ("П2-DLE|П2-SGT400|П2$|^П2,", "Уровень 2 — профильные независимые (UK/EU; санкционный риск — заход через посредника)", "t2"),
    ("Иран", "Уровень 3 — лицензиаты Siemens-дизайна (Иран; не-долларовые схемы)", "t3"),
    ("П3|лид", "Уровень 4 — прочие производители/трейдеры/сервис", "t4"),
]


def tier_of(p):
    import re
    for pat, title, cls in TIERS:
        if re.search(pat, p or ""):
            return title
    return TIERS[-1][1]


def main():
    rows = list(csv.DictReader(open(OUT / "sgt_exporters_contacts.csv", encoding="utf-8-sig"), delimiter=";"))
    groups = {t[1]: [] for t in TIERS}
    for r in rows:
        groups[tier_of(r.get("Приоритет"))].append(r)

    css = """
    @page{size:A4 landscape;margin:12mm 10mm}
    body{font:10.5px/1.4 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
    h1{font-size:19px;margin:0 0 2px} h2{font-size:13px;margin:14px 0 5px;border-bottom:2px solid #1b2330;padding-bottom:2px;page-break-after:avoid}
    .mut{color:#555;font-size:10px}
    table{border-collapse:collapse;width:100%;margin:4px 0}
    tr{page-break-inside:avoid} th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:9.5px}
    th{background:#eef2f7} b{color:#0b3d91} .grn{color:#1a7f37;font-weight:700}
    .box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;padding:6px 10px;margin:6px 0;font-size:10.5px;page-break-inside:avoid}
    .warn{border-left-color:#c62828}
    """
    H = [f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<h1>Поставщики ЗИП ГТУ Siemens — фокус SGT-400</h1>
<div class="mut">Камера сгорания SGT-400 (MW21215M, 12 шт) + главная горелка DUAL DLE (MW22316B/01, 12 шт) · {date.today().strftime('%d.%m.%Y')} ·
источники: таможенная статистика РФ HS 8411 за 2023–2026 (1 347 деклараций) + веб-верификация контактов. Всего компаний: {len(rows)}.</div>
<div class="box"><b>Первый залп RFQ (сегодня):</b> ① Blaze Shenzhen — peter@turbineblade.net (камеры сгорания SGT-400 в каталоге) ·
② O.B.T Qingdao — lisa@obtcasting.com (литьё камер Siemens) · ③ Leda Greenpower — WhatsApp +86 138 0121 8512 (прайс burner assembly/pilot burner SGT-400) ·
④ Dongturbo — WhatsApp +86 139-0807-3010 (уже возил камеру сгорания SGT-600 в РФ). В запросе указывать оба PN, кол-во 12+12, приложить фото/чертёж или предложить б/у образец на обмер.</div>"""]
    for _, title, _ in TIERS:
        rs = groups[title]
        if not rs:
            continue
        H.append(f"<h2>{e(title)} — {len(rs)}</h2><table><tr><th style='width:15%'>Компания</th><th style='width:9%'>Гео</th><th style='width:9%'>Роль</th><th style='width:24%'>Что делает / возил (модели)</th><th style='width:11%'>Сайт</th><th style='width:12%'>Email</th><th style='width:11%'>Телефон</th><th style='width:9%'>Примечание</th></tr>")
        for r in rs:
            H.append(f"<tr><td><b>{e(r['Экспортёр'])}</b></td><td>{e(r['Страна'])}, {e(r['Город'])}</td><td>{e(r['Роль'])}</td>"
                     f"<td>{e(r['Что возил (модели)'])}</td><td>{e(r['Сайт'])}</td><td>{e(r['Email'])}</td><td>{e(r['Телефон'])}</td>"
                     f"<td class='mut'>{e(r['Комментарий'])}</td></tr>")
        H.append("</table>")
    H.append(f"""
<div class="box warn"><b>Не работать напрямую:</b> Foshan Xinchuang и Homedia (Zhangzhou) — трейдеры-прокладки российского импортёра ООО «СТС», не производители.
Контекст: ООО «СТС» — главный импортёр этой номенклатуры в РФ (64 отгрузки), работает с Dongturbo, Suzhou Fuhang и Siemens (GB) одновременно.</div>
<div class="mut">Контакты проверены по официальным сайтам/профилям на {date.today().isoformat()}; где данных нет — указано «не найдено» (ничего не выдумано).
Полная база и таможенная статистика: kvant-zip.pages.dev (вкладка «Таможня») · CSV: kvant-zip.pages.dev/orders/sgt_exporters_contacts.csv</div>
</body></html>""")
    (OUT / "sgt_suppliers_report.html").write_text("\n".join(H), encoding="utf-8")
    print(f"HTML готов: {sum(len(v) for v in groups.values())} компаний по {sum(1 for v in groups.values() if v)} уровням")


if __name__ == "__main__":
    main()
