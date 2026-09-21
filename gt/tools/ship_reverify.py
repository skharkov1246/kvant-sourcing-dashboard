#!/usr/bin/env python3
"""Перепроверка крупнейших строк заявки ЛУКОЙЛ: настоящий уровень цены и канал.

Зачем. Владелец выставил заказчику ТВЁРДЫЕ цены и будет от них снижаться —
значит на защите нужен не разброс «от и до», а ответ по каждой строке: сколько
позиция стоит на самом деле, у кого её взять и что мешает закрыть строку.
Наша прежняя оценка — рыночная разведка, и она ошибалась в обе стороны: у одной
строки вилка 10–40 USD против настоящих 430, у другой 210 против 150.

Вход:  gt/data/ship_reverify.json — результат перепроверки, строка за строкой
       gt/data/ship_lukoil.json   — заявка: количество, наша вилка, уверенность
Выход: gt/docs/ПЕРЕПРОВЕРКА-ЛУКОЙЛ.pdf

Что документ НЕ делает. Не складывает найденные цены в сумму закупки: цена с
карточки действует на подтверждённый остаток, а не на весь объём (разбор
выкладки 12.09.2026). Не выдаёт «нечем проверить» за «дорого» — это разные
вещи, и они в разных разделах. Не ставит в заголовок цифру, полученную
домножением: рядом с такой всегда стоит оговорка.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "gt/data/ship_reverify.json"
DEMAND = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/docs"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]

CSS = """
@page { size: A4; margin: 12mm 10mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 8.4pt; color: #111; margin: 0; }
h1 { font-size: 18pt; margin: 0 0 2mm; }
h2 { font-size: 12pt; margin: 0 0 2.5mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
h3 { font-size: 10pt; margin: 4.5mm 0 1.5mm; }
p { margin: 0 0 2.5mm; line-height: 1.42; }
.sec { page-break-before: always; }
.sec:first-child { page-break-before: auto; }
.lead { font-size: 9pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tbody.p { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.3mm 1.6mm; font-size: 7.6pt; }
.t td { padding: 1.3mm 1.6mm; word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tbody.p:nth-of-type(even) td { background: #f6f6f6; }
.t tr.n td { color: #333; font-size: 7.7pt; padding-top: 0.4mm; padding-bottom: 1.8mm; line-height: 1.34; }
.t td.n, .t th.n { text-align: right; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.4mm 3mm 1.4mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; width: 62mm; }
.big { font-size: 13pt; font-weight: bold; }
ol, ul { margin: 0 0 3mm; padding-left: 5.5mm; }
li { margin-bottom: 1.6mm; line-height: 1.42; }
.warn { border-left: 2.4pt solid #111; padding-left: 3.5mm; margin-bottom: 3.5mm; }
/* НЕ page-break-inside: avoid. Разбор строки бывает выше страницы, и запрет
   разрыва выбрасывал её на новую, оставляя полупустую — pdf_check это ловит.
   Правило выкладок: таблица и блок текут сами (docs/ПРАВИЛА-PDF.md). */
.row { margin-bottom: 4.5mm; padding-bottom: 2mm; border-bottom: 0.4pt solid #bbb; }
.row h3 { margin-top: 0; page-break-after: avoid; }
.f { margin: 0 0 1.4mm; line-height: 1.38; }
.f b { display: inline; }
.tag { font-size: 7.4pt; border: 0.7pt solid #111; padding: 0.3mm 1.4mm; margin-left: 2mm;
       white-space: nowrap; }
"""

# Вердикт по нашей вилке. Порядок — по цене ошибки: занижение грозит убытком на
# защите, завышение всего лишь съедает запас на снижение.
# «ДУБЛЬ» появился замером: строка 3420742 и строка LF16031 — одна и та же
# позиция заявки (331 шт), посчитанная дважды. Без своего класса такая строка
# уходит в «нечем проверить» и продолжает удваивать сумму молча.
# «НЕ ПОДТВЕРЖДЕНА» появилась проверкой на опровержение: цена по строке есть, но
# наблюдение одно и оно не выдерживает проверки — брокерская витрина, страница за
# антиботом, цифра из сниппета. Это не «дорого» и не «дёшево», а отсутствие
# подтверждения, и смешивать это с вердиктом по цене нельзя.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verdicts import VERDICTS, vkey  # noqa: E402  один список и один способ считать
V_WHAT = {
    "ЗАНИЖЕНА": ("закупка дороже, чем мы считали — запаса на снижение нет, "
                 "а на части строк выставленная цена может оказаться ниже закупки"),
    "ЗАВЫШЕНА": ("закупка дешевле — это и есть запас, от которого можно "
                 "снижаться на торге"),
    "ВЕРНА": "оценка подтвердилась, строку можно защищать как есть",
    "НЕ ПОДТВЕРЖДЕНА": ("цена нашлась, но свидетель один и он не годится: брокерская "
                        "витрина, страница за антиботом или цифра из сниппета. Вилку это "
                        "не опровергает и не подтверждает"),
    "ДУБЛЬ": ("это та же позиция заявки, что другая строка перепроверки: цена у неё "
              "своя не бывает, и в сумму она входит один раз"),
    "НЕЧЕМ ПРОВЕРИТЬ": ("открытой цены нет ни в одном канале: ни заводской, ни "
                        "дистрибьюторской, ни у сток-трейдера. Это НЕ «дорого» "
                        "и НЕ «дёшево» — это отсутствие данных"),
}
FIELDS = [
    ("what_it_is", "что это"),
    ("real_maker", "кто делает на самом деле"),
    ("real_pn", "его номер"),
    ("lifecycle", "жизненный цикл"),
    ("price_low", "минимум"),
    ("price_high", "максимум"),
    ("price_authorized", "авторизованный канал"),
    ("price_source", "у кого найдено"),
    ("price_kind", "природа цены"),
    ("stock", "остаток"),
    ("lead_time", "срок"),
    ("volume_note", "на наш объём"),
    ("recommended", "что закладывать"),
    ("channel", "канал закупки"),
    ("contacts", "контакты"),
    ("blocker", "что мешает закрыть строку"),
    ("note", "чем подтверждено"),
]


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def norm(pn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").upper())


def expo(r: dict) -> float:
    try:
        return float(r.get("expo") or 0)
    except (TypeError, ValueError):
        return 0.0


def holds(r: dict) -> tuple[int, int]:
    """Сколько скептиков подтвердило вывод и сколько их было.

    Проверка на опровержение ставится не для красоты: исследователь уже был
    пойман на выдуманной цитате. Вывод без подтверждения помечается прямо в
    документе, а не тихо приравнивается к подтверждённому.
    """
    sk = r.get("skeptics") or []
    return sum(1 for s in sk if s.get("holds")), len(sk)


def badge(r: dict) -> str:
    ok, total = holds(r)
    if not total:
        return '<span class="tag">проверка на опровержение не прогонялась</span>'
    if ok == total:
        return f'<span class="tag">выдержал {ok} из {total} опровержений</span>'
    return f'<span class="tag">ОСПОРЕН: выдержал {ok} из {total}</span>'


def build(rows: list, meta: dict) -> str:
    by_v = Counter(vkey(r) for r in rows)
    tot = sum(expo(r) for r in rows)
    h = ["<!doctype html><meta charset='utf-8'><title>Перепроверка ЛУКОЙЛ</title>"
         f"<style>{CSS}</style>"]
    a = h.append

    a("<div class='sec'><h1>Перепроверка крупнейших строк заявки</h1>")
    a("<p class='lead'>По каждой строке заново установлено: что это за изделие, "
      "кто его делает на самом деле, сколько оно стоит в открытых каналах и есть "
      "ли рабочий канал закупки. Каждый вывод отдельно проверялся на "
      "опровержение.</p>")
    a("<table class='k'>")
    a(f"<tr><td class='l'>строк перепроверено</td><td class='big'>{ru(len(rows))}</td></tr>")
    a(f"<tr><td class='l'>их экспозиция по середине нашей вилки</td>"
      f"<td class='big'>{ru(tot)} USD</td>"
      f"<td class='dim'>{E(meta.get('share') or '')}</td></tr>")
    for v in VERDICTS:
        if by_v.get(v):
            g = [r for r in rows if vkey(r) == v]
            a(f"<tr><td class='l'>вилка {E(v.lower())}</td>"
              f"<td>{ru(by_v[v])} строк · {ru(sum(expo(x) for x in g))} USD</td>"
              f"<td class='dim'>{E(V_WHAT[v])}</td></tr>")
    nb = sum(1 for r in rows if (r.get("blocker") or "").strip())
    a(f"<tr><td class='l'>строк с замком на заказчике</td><td class='big'>{ru(nb)}</td>"
      f"<td class='dim'>без ответа заказчика цену дать нельзя — нужны шильдик, "
      f"чертёж, ревизия или единица измерения</td></tr>")
    a("</table>")

    a("<div class='warn'><p><b>Что этот документ не утверждает.</b> Найденные "
      "цены не сложены в сумму закупки: цена с карточки действует на "
      "подтверждённый остаток, а не на весь наш объём. «Нечем проверить» — не "
      "«дорого»: это отсутствие данных, и такие строки стоят отдельным "
      "разделом. Цифра, полученная домножением или подстановкой по классу, в "
      "заголовок не идёт и всегда с оговоркой.</p></div>")
    a("</div>")

    for v in VERDICTS:
        g = sorted([r for r in rows if vkey(r) == v], key=lambda r: -expo(r))
        if not g:
            continue
        a(f"<div class='sec'><h2>Вилка {E(v.lower())} — {ru(len(g))} строк, "
          f"{ru(sum(expo(x) for x in g))} USD</h2>")
        a(f"<p>{E(V_WHAT[v])}</p>")
        a("<table class='t'><colgroup><col style='width:30mm'><col style='width:14mm'>"
          "<col style='width:26mm'><col style='width:26mm'><col></colgroup>")
        a("<thead><tr><th>артикул</th><th class='n'>кол-во</th><th>наша вилка</th>"
          "<th>что закладывать</th><th>чем подтверждено</th></tr></thead>")
        for r in g:
            lo, hi = r.get("lo"), r.get("hi")
            band = (f"{ru(lo)} – {ru(hi)} USD" if lo not in (None, "") else "—")
            a("<tbody class='p'>")
            a(f"<tr><td class='pn'>{E(r.get('pn'))}</td>"
              f"<td class='n'>{ru(r.get('qty'))}</td><td>{E(band)}</td>"
              f"<td><b>{E((r.get('recommended') or '—')[:160])}</b></td>"
              f"<td>{E((r.get('note') or '')[:240])}</td></tr>")
            a(f"<tr class='n'><td colspan='5'>{E((r.get('what_it_is') or '')[:400])}"
              f"{badge(r)}</td></tr>")
            a("</tbody>")
        a("</table></div>")

    blocked = [r for r in rows if (r.get("blocker") or "").strip()]
    if blocked:
        a("<div class='sec'><h2>Замки на заказчике</h2>")
        a("<p>Строки, которые нельзя закрыть твёрдой ценой, пока заказчик не "
          "ответит. Это не наша недоработка, а недостающие исходные: без "
          "ревизии, шильдика или единицы измерения цена отличается кратно, и "
          "любая цифра здесь была бы выдумкой.</p>")
        a("<table class='t'><colgroup><col style='width:30mm'><col style='width:16mm'>"
          "<col></colgroup><thead><tr><th>артикул</th><th class='n'>экспозиция</th>"
          "<th>что спросить</th></tr></thead>")
        for r in sorted(blocked, key=lambda r: -expo(r)):
            a("<tbody class='p'><tr>"
              f"<td class='pn'>{E(r.get('pn'))}</td>"
              f"<td class='n'>{ru(expo(r))}</td>"
              f"<td>{E(r.get('blocker'))}</td></tr></tbody>")
        a("</table></div>")

    makers = [r for r in rows if (r.get("real_pn") or "").strip()
              and "неизвест" not in (r.get("real_pn") or "").lower()]
    if makers:
        a("<div class='sec'><h2>Настоящий изготовитель вскрыт</h2>")
        a("<p>Под шильдой OEM часто стоит серийное изделие другого завода. Его "
          "собственный номер — это и есть путь к цене без наценки OEM. Ниже "
          "только то, что подтверждено карточкой или кросс-таблицей.</p>")
        a("<table class='t'><colgroup><col style='width:28mm'><col style='width:44mm'>"
          "<col style='width:38mm'><col></colgroup>")
        a("<thead><tr><th>наш артикул</th><th>кто делает</th><th>его номер</th>"
          "<th>что это даёт</th></tr></thead>")
        for r in sorted(makers, key=lambda r: -expo(r)):
            a("<tbody class='p'><tr>"
              f"<td class='pn'>{E(r.get('pn'))}</td>"
              f"<td>{E((r.get('real_maker') or '')[:200])}</td>"
              f"<td class='pn'>{E((r.get('real_pn') or '')[:120])}</td>"
              f"<td>{E((r.get('recommended') or '')[:200])}</td></tr></tbody>")
        a("</table></div>")

    a("<div class='sec'><h2>Построчно: всё, что нашлось</h2>")
    a("<p class='dim'>Порядок — по экспозиции. Пустые поля не выводятся: там "
      "нечего написать, и придумывать нечего.</p>")
    for r in sorted(rows, key=lambda r: -expo(r)):
        a("<div class='row'>")
        a(f"<h3><span class='pn'>{E(r.get('pn'))}</span> · {E(r.get('man') or '')} · "
          f"{ru(r.get('qty'))} шт · экспозиция {ru(expo(r))} USD · "
          f"вилка {E(vkey(r).lower())}{badge(r)}</h3>")
        a(f"<p class='f dim'>{E(r.get('name') or '')}</p>")
        for key, label in FIELDS:
            val = (r.get(key) or "").strip() if isinstance(r.get(key), str) else r.get(key)
            if not val:
                continue
            a(f"<p class='f'><b>{E(label)}:</b> {E(val)}</p>")
        for s in (r.get("skeptics") or []):
            mark = "подтвердил" if s.get("holds") else "ОСПОРИЛ"
            a(f"<p class='f dim'><b>проверка на опровержение — {mark}:</b> "
              f"{E(s.get('why') or '')}"
              + (f" <b>Поправка:</b> {E(s.get('correction'))}" if s.get("correction") else "")
              + "</p>")
        a("</div>")
    a("</div>")
    return "".join(h)


def enrich(rows: list) -> tuple[list, dict]:
    """Дотягивает количество, вилку и экспозицию из заявки.

    В перепроверку уходил только артикул и контекст, поэтому числа берём из
    единственного места, где они выверены, — из самой заявки.
    """
    if not DEMAND.exists():
        return rows, {}
    doc = json.loads(DEMAND.read_text(encoding="utf-8"))
    src = doc["rows"] if isinstance(doc, dict) else doc
    by = {}
    for x in src:
        by.setdefault(norm(x.get("pn")), x)

    def mid(x):
        lo, hi = x.get("usd_lo"), x.get("usd_hi")
        if lo in (None, "") or hi in (None, ""):
            return None
        return (float(lo) + float(hi)) / 2

    total = sum((mid(x) or 0) * float(x.get("qty") or 0) for x in src)
    out = []
    for r in rows:
        d = by.get(norm(r.get("pn"))) or {}
        m = mid(d)
        r = dict(r)
        r.setdefault("name", d.get("name"))
        r.setdefault("man", d.get("man"))
        # Количество, вилка и экспозиция берутся ТОЛЬКО из сводки заявки, и
        # значение строки перепроверки их не перебивает. Оплачено разбором:
        # три строки несли выдуманные числа — «экспозиция 33 600 USD» при
        # настоящих 4 410, «21 250» при 892, «56 250» при 788. Пока источник
        # один, такое число в документ попасть не может.
        r["qty"] = d.get("qty")
        r["lo"] = d.get("usd_lo")
        r["hi"] = d.get("usd_hi")
        r["conf"] = d.get("conf")
        r["expo"] = 0.0 if m is None else m * float(d.get("qty") or 0)
        out.append(r)
    part = sum(expo(r) for r in out)
    meta = {"share": (f"это {100 * part / total:.0f} % экспозиции всей заявки "
                      f"({ru(total)} USD по {ru(len(src))} строкам)" if total else "")}
    return out, meta


def main() -> int:
    if not SRC.exists():
        print(f"нет {SRC} — перепроверка ещё не сведена", file=sys.stderr)
        return 1
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    rows = doc["rows"] if isinstance(doc, dict) else doc
    if not rows:
        print("в перепроверке нет строк", file=sys.stderr)
        return 1
    rows, meta = enrich(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    hp = OUT / "ПЕРЕПРОВЕРКА-ЛУКОЙЛ.html"
    pp = OUT / "ПЕРЕПРОВЕРКА-ЛУКОЙЛ.pdf"
    hp.write_text(build(rows, meta), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=180000",
         f"--print-to-pdf={pp}", hp.as_uri()],
        check=True, capture_output=True)
    by_v = Counter(vkey(r) for r in rows)
    print(f"{pp.name}: строк {len(rows)}, {pp.stat().st_size / 1e6:.1f} МБ")
    print("  по вердикту: " + " · ".join(f"{k} {v}" for k, v in by_v.most_common()))
    unchecked = sum(1 for r in rows if not (r.get("skeptics") or []))
    if unchecked:
        print(f"  ВНИМАНИЕ: без проверки на опровержение {unchecked} строк — "
              f"в документе они помечены, но это не «подтверждено»")
    return 0


if __name__ == "__main__":
    sys.exit(main())
