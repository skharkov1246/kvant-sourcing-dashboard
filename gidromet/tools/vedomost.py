#!/usr/bin/env python3
"""Печатная управленческая ведомость: три страницы А4 из gidromet/data/razvilki.json.

Тот же файл данных, что и вкладка «Развилки решений» на сайте, поэтому числа
на бумаге и на странице разойтись не могут. Никаких величин в этом файле нет —
он только раскладывает данные по печатной вёрстке.

Запуск:  python3 gidromet/tools/vedomost.py [каталог для вывода]
Дальше:  chromium --headless --print-to-pdf=... --print-to-pdf-no-header vedomost.html
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DANNYE = ROOT / 'data' / 'razvilki.json'

STIL = """
@page { size: A4 landscape; margin: 6mm 8mm; }
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font:7pt/1.24 "DejaVu Sans",Arial,Helvetica,sans-serif;color:#16191c;background:#fff}
.page{page-break-after:always}
.page:last-child{page-break-after:auto}
h1{font-size:12pt;margin:0 0 .8mm;font-weight:700;letter-spacing:-.2pt}
h2{font-size:8.2pt;margin:2.2mm 0 1mm;font-weight:700;text-transform:uppercase;letter-spacing:.5pt;
   color:#1e2429;border-bottom:1.2pt solid #1e2429;padding-bottom:.9mm}
p{margin:0 0 1.3mm}
.sub{font-size:7pt;color:#4a5157;margin:0}
.hdr{border-bottom:1.6pt solid #16191c;padding-bottom:1.5mm;margin-bottom:2mm;
     display:flex;justify-content:space-between;align-items:flex-end;gap:8mm}
.hdr .r{text-align:right;font-size:6.6pt;color:#4a5157;white-space:nowrap;line-height:1.5}
.tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:1.4mm;margin:1.8mm 0 1mm}
.t{border:.5pt solid #b9bfc4;border-top:1.8pt solid #16191c;padding:1.2mm 1.4mm 1.1mm}
.t b{display:block;font-size:9.6pt;line-height:1.05;font-family:"DejaVu Sans Mono",monospace;margin-bottom:.9mm}
.t span{display:block;font-size:6pt;color:#4a5157;line-height:1.3}
.t.neg{border-top-color:#a32617} .t.neg b{color:#a32617}
.t.pos{border-top-color:#1d6b3d} .t.pos b{color:#1d6b3d}
table{border-collapse:collapse;width:100%;font-size:6.3pt}
th,td{border:.4pt solid #b9bfc4;padding:.6mm .9mm;text-align:left;vertical-align:top}
th{background:#eceef0;font-size:6pt;text-transform:uppercase;letter-spacing:.3pt;font-weight:700;line-height:1.22}
td.n{text-align:right;white-space:nowrap;font-family:"DejaVu Sans Mono",monospace}
tr.hl td{background:#fbf0ee}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2.4mm}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:2.4mm}
.razvilki{display:grid;grid-template-columns:1fr 1fr 1fr;gap:2.4mm;align-items:start}
.r-card{break-inside:avoid;border:.4pt solid #b9bfc4;border-left:2.2pt solid #16191c;padding:1.1mm 1.4mm}
.r-card .vv+.vv{margin-top:1.2mm}
.r-h{font-weight:700;font-size:7.6pt;margin-bottom:.8mm}
.r-h .id{font-family:"DejaVu Sans Mono",monospace;color:#fff;background:#2a4a6b;padding:.3mm 1.2mm;margin-right:1.8mm}
.stavka{font-size:6.6pt;color:#4a5157;margin-bottom:1.1mm}
.stavka b{color:#a32617;font-size:7pt}
.vv{border:.4pt solid #c6ccd1;padding:.9mm 1.1mm}
.vv.rek{border-color:#1d6b3d;border-width:.8pt;background:#f4f8f5}
.vv .vn{font-family:"DejaVu Sans Mono",monospace;font-size:8pt;font-weight:700;margin:.4mm 0 .9mm;line-height:1.1}
.vv.rek .vn{color:#1d6b3d}
.vv .vi{font-weight:700;font-size:6.9pt}
.vv .rekm{font-size:5.7pt;font-weight:700;color:#1d6b3d;border:.5pt solid #1d6b3d;padding:.15mm 1mm;margin-left:1.4mm;white-space:nowrap}
.vh{font-size:5.6pt;text-transform:uppercase;letter-spacing:.4pt;color:#6c757c;margin:.9mm 0 0}
ul{margin:.4mm 0 .9mm;padding-left:3.6mm}
li{margin:.45mm 0;font-size:6.3pt}
.itog{margin-top:1.1mm;font-size:6.5pt}
.itog span{display:block;padding:.7mm 1.1mm;border-left:1.8pt solid #7d868d;background:#f2f4f5;margin-top:.7mm}
.itog span.bad{border-left-color:#a32617;background:#fbf0ee}
.itog span.ok{border-left-color:#1d6b3d;background:#eff5f0}
.key{background:#f5f1e6;border:.5pt solid #c9b98a;border-left:2.4pt solid #8a5a10;padding:1.1mm 1.4mm;margin:1.2mm 0;font-size:6.6pt}
.key b{font-size:7.2pt}
.foot{margin-top:1.8mm;padding-top:1.1mm;border-top:.4pt solid #b9bfc4;font-size:6.5pt;color:#4a5157}
"""


def e(s) -> str:
    return html.escape(str(s), quote=False)


def zhirno(s: str) -> str:
    """Разметка **важное** из данных — в <b>, остальное экранируется."""
    out, kusok = [], e(s).split('**')
    for i, k in enumerate(kusok):
        out.append(f'<b>{k}</b>' if i % 2 else k)
    return ''.join(out)


def plitki(tiles) -> str:
    ton = {'risk': 'neg', 'ok': 'pos', 'warn': 'warn'}
    return ('<div class="tiles">' + ''.join(
        f'<div class="t {ton.get(t.get("tone",""), "")}"><b>{e(t["v"])}</b>'
        f'<span>{e(t["c"])}</span></div>' for t in tiles) + '</div>')


def variant(v) -> str:
    def spisok(klass, zagolovok, punkty):
        punkty = (punkty or [])[:4]   # на бумаге — четыре сильнейших довода; на сайте перечни полные
        if not punkty:
            return ''
        return (f'<div class="vh">{zagolovok}</div><ul class="{klass}">'
                + ''.join(f'<li>{zhirno(x)}</li>' for x in punkty) + '</ul>')
    metka = '<span class="rekm">рекомендуется</span>' if v.get('rek') else ''
    return (f'<div class="vv {"rek" if v.get("rek") else ""}">'
            f'<div class="vi">{e(v["imya"])}{metka}</div>'
            f'<div class="vn">{e(v["chislo"])}</div>'
            + spisok('za', 'За', v.get('za')) + spisok('pr', 'Против', v.get('protiv'))
            + '</div>')


def razvilka(r) -> str:
    return (f'<div class="r-card">'
            f'<div class="r-h"><span class="id">{e(r["nomer"])}</span>{e(r["vopros"])}</div>'
            f'<div class="stavka">На кону: <b>{e(r["stavka"])}</b> · решает: '
            f'{e(r["kto"])} · {e(r["kogda"])}</div>'
            f'{"".join(variant(v) for v in r["varianty"])}'
            f'<div class="itog">'
            f'<span class="bad"><b>Цена ошибки.</b> {zhirno(r["cena_oshibki"])}</span>'
            f'<span><b>Чем закрывается.</b> {zhirno(r["chem"])}</span>'
            f'<span class="ok"><b>Рекомендация.</b> {zhirno(r["rekomendaciya"])}</span>'
            f'</div></div>')


def tablica(t) -> str:
    num = t.get('num') or []
    sh = ''.join(f'<th>{e(c)}</th>' for c in t['cols'])
    tela = ''.join(
        '<tr class="' + e((t.get('rowtone') or [''] * len(t['rows']))[i] and 'hl') + '">'
        + ''.join(f'<td class="{"n" if (len(num) > j and num[j]) else ""}">{zhirno(c)}</td>'
                  for j, c in enumerate(row)) + '</tr>'
        for i, row in enumerate(t['rows']))
    return f'<table><thead><tr>{sh}</tr></thead><tbody>{tela}</tbody></table>'


def kartochki(cards) -> str:
    return '<div class="grid3">' + ''.join(
        f'<div class="vv"><div class="vi">{e(c["h"])}</div><ul>'
        + ''.join(f'<li>{zhirno(x)}</li>' for x in c.get('list', [])) + '</ul></div>'
        for c in cards) + '</div>'


def sobrat() -> str:
    d = json.loads(DANNYE.read_text(encoding='utf-8'))
    r = {x['nomer']: x for x in d['razvilki']}
    svod = next(s for s in d['sections'] if s['h'].startswith('Сводная'))
    chto = next(s for s in d['sections'] if s['h'].startswith('Что решается'))
    stavka = next(s for s in d['sections'] if s['h'].startswith('Чем обеспечена'))
    itog = next(s for s in d['sections'] if s['h'].startswith('Итог по ставке'))

    shapka = (
        '<div class="hdr"><div>'
        '<h1>Управленческая ведомость: шесть решений по проекту</h1>'
        '<p class="sub">Месторождение меди, золота и серебра в Республике Карелия. '
        'Независимое инженерное заключение ООО «Квант». Обоснование каждой величины — '
        'на странице <b>kvant-gidromet.pages.dev</b>, вкладка «Развилки решений».</p>'
        '</div><div class="r">Котировки на 01.09.2026<br>Курс 85,85 руб./долл.<br>'
        'Ставка дисконтирования 10 % реальная</div></div>')

    def stranica(soderzhimoe, nomer, hvost):
        return ('<section class="page">' + shapka + soderzhimoe
                + f'<div class="foot">Страница {nomer} из 3. {hvost}</div></section>')

    poryadok = d['lead'].split('\n\n')[1].replace('Порядок важен. ', '')

    s1 = stranica(
        plitki(d['tiles'])
        + f'<div class="key"><b>Порядок важнее содержания.</b> {zhirno(poryadok)}</div>'
        + f'<h2>{e(svod["h"])}</h2>' + tablica(svod['table'])
        + f'<h2>{e(chto["h"])}</h2>' + kartochki(chto['cards'])
        + f'<h2>{e(stavka["h"])}</h2>'
        + '<div class="grid2"><div>' + tablica(stavka['table']) + '</div><div>'
        + f'<div class="key"><b>Итог.</b> {zhirno(itog["boxes"][0]["text"])}</div>'
        + f'<p>{zhirno(d["footnote"])}</p></div></div>',
        1, 'Порядок строк — порядок принятия, а не важности. '
           'Расчёты воспроизводимы: модели в каталоге factory/model.')

    s2 = stranica(
        '<h2>Первые три решения: что закрывается до проектирования</h2>'
        + '<div class="razvilki">'
        + razvilka(r['Р-2']) + razvilka(r['Р-4']) + razvilka(r['Р-3']) + '</div>',
        2, 'Эти три решения принимаются на имеющихся данных и не ждут испытаний.')

    kluchevye = ''.join(
        f'<div class="key"><b>{e(b["title"])}.</b> {zhirno(b["text"])}</div>'
        for b in d['boxes'])

    s3 = stranica(
        '<h2>Оставшиеся три решения: деньги, передел и наша роль</h2>'
        + '<div class="razvilki">'
        + razvilka(r['Р-5']) + razvilka(r['Р-1']) + razvilka(r['Р-6']) + '</div>'
        + kluchevye,
        3, 'Ведомость не заменяет разделы заключения: она называет решение и его цену.')

    return ('<!doctype html>\n<html lang="ru"><head><meta charset="utf-8">'
            '<title>Управленческая ведомость: шесть решений по проекту</title>'
            f'<style>{STIL}</style></head><body>\n{s1}\n{s2}\n{s3}\n</body></html>\n')


def main() -> None:
    kuda = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / 'factory' / 'spravka'
    kuda.mkdir(parents=True, exist_ok=True)
    f = kuda / 'vedomost.html'
    f.write_text(sobrat(), encoding='utf-8')
    print(f'OK -> {f} ({f.stat().st_size // 1024} КБ)')


if __name__ == '__main__':
    main()
