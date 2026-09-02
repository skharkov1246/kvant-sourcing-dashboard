#!/usr/bin/env python3
"""Справка к переговорам для генерального директора: печатный А4 из gidromet/data/gd.json.

Читатель — не инженер. Документ говорит, что произносить, чем подтверждать
и чего не говорить. Величин в этом файле нет: они в данных, данные — из моделей.

Запуск:  python3 gidromet/tools/spravka_gd.py [каталог для вывода]
Дальше:  chromium --headless --print-to-pdf=... --print-to-pdf-no-header spravka_gd.html
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DANNYE = ROOT / 'data' / 'gd.json'

STIL = """
@page { size: A4 portrait; margin: 10mm 12mm 10mm; }
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font:8.3pt/1.3 "DejaVu Sans",Arial,Helvetica,sans-serif;color:#16191c;background:#fff}
h1{font-size:16pt;margin:0 0 1.5mm;font-weight:700;letter-spacing:-.2pt}
h2{font-size:9.8pt;margin:3.6mm 0 1.6mm;font-weight:700;text-transform:uppercase;letter-spacing:.5pt;
   color:#1e2429;border-bottom:1.3pt solid #1e2429;padding-bottom:1.2mm;break-after:avoid}
h3{font-size:9.6pt;margin:3mm 0 1mm;font-weight:700;break-after:avoid}
p{margin:0 0 2mm}
.sub{font-size:9pt;color:#4a5157;margin:0 0 1mm}
.hdr{border-bottom:1.8pt solid #16191c;padding-bottom:2.5mm;margin-bottom:3mm}
.sut{background:#f2f4f5;border-left:2.6pt solid #2a4a6b;padding:2.2mm 3mm;margin:0 0 2mm;font-size:9.4pt}
.rech{background:#fbf9f2;border:.5pt solid #d9cfae;border-left:3pt solid #8a5a10;padding:2.2mm 3mm;margin:1.2mm 0 1.6mm;font-size:9.2pt;line-height:1.38}
.rech p{margin:0 0 2mm} .rech p:last-child{margin:0}
.nah{break-inside:avoid;border:.5pt solid #b9bfc4;border-left:2.6pt solid #16191c;padding:1.6mm 2.6mm;margin:0 0 1.6mm}
.nah .n{display:inline-block;font-family:"DejaVu Sans Mono",monospace;font-weight:700;color:#fff;background:#2a4a6b;padding:.3mm 1.8mm;margin-right:2mm;font-size:8.6pt}
.nah .t{font-weight:700;font-size:9.8pt}
.nah .l{font-size:7pt;text-transform:uppercase;letter-spacing:.4pt;color:#6c757c;margin:1.1mm 0 .2mm}
.nah .f{font-style:italic;background:#f2f4f5;padding:1mm 2.2mm;margin-top:1mm;border-left:1.6pt solid #7d868d;font-size:8pt}
.poz{border:.5pt solid #c6ccd1;padding:2mm 3mm;margin:0 0 2mm}
.poz h3{break-after:avoid}
.poz.glav{border-color:#1d6b3d;border-width:.9pt;background:#f4f8f5}
table{border-collapse:collapse;width:100%;font-size:7.9pt;margin:.8mm 0 1.6mm}
th,td{border:.4pt solid #b9bfc4;padding:1.2mm 2mm;text-align:left;vertical-align:top}
th{background:#eceef0;font-size:7.8pt;text-transform:uppercase;letter-spacing:.3pt;font-weight:700}
td.v{width:26%;font-weight:700;background:#fbf0ee}
td.o{background:#fff}
.chisla{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:1.6mm;margin:1mm 0 2mm}
.ch{border:.5pt solid #b9bfc4;border-top:2pt solid #16191c;padding:1.6mm 2.4mm}
.ch b{display:block;font-family:"DejaVu Sans Mono",monospace;font-size:10pt;line-height:1.1;margin-bottom:.8mm}
.ch span{font-size:8.4pt;color:#4a5157}
ul{margin:.5mm 0 2mm;padding-left:4.6mm}
li{margin:.8mm 0}
ul.neg li::marker{color:#a32617;content:"✕  "}
.shagi{display:grid;grid-template-columns:repeat(5,1fr);gap:1.6mm;margin:1mm 0 2mm}
.sh{border:.5pt solid #b9bfc4;border-top:2pt solid #2a4a6b;padding:1.5mm 2mm;font-size:8.3pt}
.sh b{display:block;font-size:9pt;margin-bottom:.8mm}
.foot{margin-top:4mm;padding-top:1.5mm;border-top:.4pt solid #b9bfc4;font-size:7.8pt;color:#4a5157}
.pb{break-before:page}
.nah2{display:grid;grid-template-columns:1fr 1fr;gap:3mm}
.slovar{display:grid;grid-template-columns:repeat(4,1fr);gap:1.6mm;margin:.8mm 0 1.6mm}
.sw{border:.5pt solid #b9bfc4;border-top:2pt solid #2a4a6b;padding:1.4mm 2mm;font-size:8pt;line-height:1.3}
.sw b{display:block;font-size:8.8pt;margin-bottom:.7mm}
ol.zakr{margin:1.2mm 0 1mm;padding-left:5mm} ol.zakr li{margin:.7mm 0}
.blok{break-inside:avoid}
"""


def e(s) -> str:
    return html.escape(str(s), quote=False)


def zh(s: str) -> str:
    out, kusok = [], e(s).split('**')
    for i, k in enumerate(kusok):
        out.append(f'<b>{k}</b>' if i % 2 else k)
    return ''.join(out)


def abz(s: str, klass='') -> str:
    return ''.join(f'<p class="{klass}">{zh(x)}</p>' for x in s.split('\n\n') if x.strip())


def sobrat() -> str:
    d = json.loads(DANNYE.read_text(encoding='utf-8'))

    shapka = (f'<div class="hdr"><h1>{e(d["zagolovok"])}</h1>'
              f'<p class="sub">{e(d["podzagolovok"])}</p></div>'
              f'<div class="sut">{zh(d["sut"])}</div>')

    sl = d.get('slovar')
    slovar = ('' if not sl else
              f'<div class="blok"><h2>{e(sl["h"])}</h2><div class="slovar">'
              + ''.join(f'<div class="sw"><b>{e(x["t"])}</b>{zh(x["d"])}</div>' for x in sl['spisok'])
              + '</div></div>')

    dm = d['dve_minuty']
    rech = f'<h2>{e(dm["h"])}</h2><div class="rech">{abz(dm["text"])}</div>'

    por = d['poryadok']
    shagi = (f'<div class="blok"><h2>{e(por["h"])}</h2><div class="shagi">'
             + ''.join(f'<div class="sh"><b>{e(s["t"])}</b>{e(s["d"])}</div>' for s in por['shagi'])
             + '</div>'
             + (f'<ol class="zakr">' + ''.join(f'<li>{zh(x)}</li>' for x in por['zakrytie']) + '</ol>'
                if por.get('zakrytie') else '')
             + '</div>')

    nh = d['nahodki']
    nahodki = (f'<h2>{e(nh["h"])}</h2><p>{zh(nh["text"])}</p>'
               + ''.join(
                   f'<div class="nah"><span class="n">{e(x["n"])}</span><span class="t">{e(x["chto"])}</span>'
                   f'<div class="nah2"><div><div class="l">Простыми словами</div><div>{zh(x["prosto"])}</div></div>'
                   f'<div><div class="l">В деньгах</div><div>{zh(x["dengi"])}</div></div></div>'
                   f'<div class="f">{zh(x["fraza"])}</div></div>'
                   for x in nh['spisok']))

    st = d['stavka']
    stavka = (f'<h2>{e(st["h"])}</h2><p>{zh(st["text"])}</p>'
              + ''.join(
                  f'<div class="poz {"glav" if i == 0 else ""}"><h3>{e(p["h"])}</h3>{abz(p["text"])}</div>'
                  for i, p in enumerate(st['pozicii'])))

    vz = d['vozrazheniya']
    vozr = (f'<h2>{e(vz["h"])}</h2><table><thead><tr><th>Заказчик скажет</th><th>Ответ</th></tr></thead><tbody>'
            + ''.join(f'<tr><td class="v">{e(x["v"])}</td><td class="o">{zh(x["o"])}</td></tr>'
                      for x in vz['spisok'])
            + '</tbody></table>')

    ch = d['chisla']
    # заголовок и сетка чисел не разрываются между страницами
    chisla = (f'<div class="blok"><h2>{e(ch["h"])}</h2><p>{zh(ch["text"])}</p><div class="chisla">'
              + ''.join(f'<div class="ch"><b>{e(x["ch"])}</b><span>{e(x["chto"])}</span></div>'
                        for x in ch['spisok'])
              + '</div></div>')

    ng = d['ne_govorit']
    ne_gov = (f'<h2>{e(ng["h"])}</h2><ul class="neg">'
              + ''.join(f'<li>{zh(x)}</li>' for x in ng['spisok']) + '</ul>')

    es = d['esli_sprosyat']
    esli = (f'<h2>{e(es["h"])}</h2><table><thead><tr><th>Вопрос</th><th>Ответ</th></tr></thead><tbody>'
            + ''.join(f'<tr><td class="v">{e(x["v"])}</td><td class="o">{zh(x["o"])}</td></tr>'
                      for x in es['spisok'])
            + '</tbody></table>')

    podval = f'<div class="foot">{zh(d["podval"])}</div>'

    return ('<!doctype html>\n<html lang="ru"><head><meta charset="utf-8">'
            f'<title>{e(d["zagolovok"])}</title><style>{STIL}</style></head><body>\n'
            + shapka + rech + slovar + nahodki + stavka + vozr + chisla + ne_gov + esli + shagi + podval
            + '\n</body></html>\n')


def main() -> None:
    kuda = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / 'factory' / 'spravka'
    kuda.mkdir(parents=True, exist_ok=True)
    f = kuda / 'spravka_gd.html'
    f.write_text(sobrat(), encoding='utf-8')
    print(f'OK -> {f} ({f.stat().st_size // 1024} КБ)')


if __name__ == '__main__':
    main()
