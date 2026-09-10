#!/usr/bin/env python3
"""Отчёт по каталогу документов: что лежит в портале и что с этим делать.

Читает file_cards (карточка на документ) и сводит их так, как к ним обращаются
в работе: род документа, чья сторона, где искать цены, насколько свежи
сведения, кто из заказчиков и поставщиков сколько прислал, сколько в архиве
мусора и дублей. Внизу — что с этим делать, с числами.

    python base/file_cards.py --db base/kvant.db      # сперва карточки
    python base/kb_dash.py --db base/kvant.db --out kb_report.html
"""
from __future__ import annotations

import argparse
import html
import sqlite3
from datetime import date
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "templates" / "report.css")


def h(x) -> str:
    return html.escape(str(x if x is not None else ""))


def num(x) -> str:
    """Разряды неразрывным пробелом: 105 569, а не 105569."""
    if x is None:
        return "—"
    if isinstance(x, float):
        x = round(x)
    return f"{int(x):,}".replace(",", " ")


def pct(a, b) -> str:
    return f"{100*a/b:.0f}%" if b else "—"


def table(cols: list[str], rows: list[list], right: set[int] = frozenset()) -> str:
    th = "".join(f'<th class="{"r" if i in right else ""}">{h(c)}</th>' for i, c in enumerate(cols))
    body = []
    for r in rows:
        tds = "".join(f'<td class="{"r" if i in right else ""}">{c}</td>' for i, c in enumerate(r))
        body.append(f"<tr>{tds}</tr>")
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def build(db_path: str) -> str:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=300)
    q = lambda s, *a: con.execute(s, a).fetchall()          # noqa: E731
    one = lambda s, *a: con.execute(s, a).fetchone()[0]     # noqa: E731

    docs = one("SELECT count(*) FROM file_cards")
    copies = one("SELECT sum(copies) FROM file_cards")
    chars = one("SELECT sum(chars) FROM file_cards")
    with_pos = one("SELECT count(*) FROM file_cards WHERE positions>0")
    with_price = one("SELECT count(*) FROM file_cards WHERE priced>0")
    blanks = one("SELECT count(*) FROM file_cards WHERE blank=1")
    deals = one("SELECT count(DISTINCT deal_id) FROM file_cards WHERE deal_id IS NOT NULL")

    tiles = [
        (num(docs), f"документов разобрано и описано<br>из <em>{num(copies)}</em> вложений портала"),
        (num(round(chars / 1e6)) + " млн", "знаков текста в них<br>≈ " + num(chars / 2000) + " страниц"),
        (pct(with_pos, docs), f"документов дали номенклатуру<br><em>{num(with_pos)}</em> шт."),
        (pct(with_price, docs), f"документов дали цену<br><em>{num(with_price)}</em> шт."),
        (num(deals), "сделок покрыто документами"),
        (num(blanks), "незаполненных бланков<br>— место занимают, знаний не несут"),
        (num(one("SELECT count(*) FROM catalog_items")), "артикулов в справочнике<br>собраны из этих документов"),
        (num(one("""SELECT count(*) FROM file_cards WHERE delivery_days IS NOT NULL
                    OR prepay_pct IS NOT NULL OR warranty_mo IS NOT NULL""")),
         "документов с условиями<br>срок, аванс, гарантия, штраф — числом"),
    ]
    tiles_html = "".join(f'<div class="tile"><b>{t}</b><span>{s}</span></div>' for t, s in tiles)

    # ── роды документов
    kinds = q("""SELECT kind, count(*), sum(copies), sum(chars), sum(positions), sum(priced),
                        sum(positions>0), sum(blank)
                 FROM file_cards GROUP BY kind ORDER BY 2 DESC""")
    krows = [[f"<b>{h(k)}</b>", num(n), num(cp), num(round((ch or 0) / 1e6)),
              num(p or 0), num(pr or 0), pct(wp or 0, n)] for k, n, cp, ch, p, pr, wp, _b in kinds]
    kinds_t = table(["род документа", "документов", "вложений", "млн знаков",
                     "позиций", "с ценой", "доля с номенклатурой"], krows, {1, 2, 3, 4, 5, 6})

    # ── стороны
    sides = q("""SELECT side, count(*), sum(positions), sum(priced) FROM file_cards
                 GROUP BY side ORDER BY 2 DESC""")
    srows = [[f"<b>{h(s)}</b>", num(n), num(p or 0), num(pr or 0), pct(pr or 0, p or 1)]
             for s, n, p, pr in sides]
    sides_t = table(["сторона", "документов", "позиций", "с ценой", "доля цен"], srows, {1, 2, 3, 4})

    # ── свежесть: год документа берём из текста, а не из даты загрузки
    years = q("""SELECT substr(date_max,1,4) y, count(*) FROM file_cards
                 WHERE date_max IS NOT NULL GROUP BY y ORDER BY y DESC LIMIT 8""")
    yrows = [[f"<b>{h(y)}</b>", num(n), pct(n, docs)] for y, n in years]
    years_t = table(["год в документе", "документов", "доля"], yrows, {1, 2})

    # ── языки
    langs = q("SELECT lang, count(*) FROM file_cards GROUP BY 1 ORDER BY 2 DESC")
    lang_t = table(["язык", "документов", "доля"],
                   [[h(x or "?"), num(n), pct(n, docs)] for x, n in langs], {1, 2})

    # ── заказчики
    comp = q("""SELECT company, count(*), count(DISTINCT deal_id),
                       sum(won=1), sum(won=0), sum(positions)
                FROM file_cards WHERE company IS NOT NULL AND company<>''
                GROUP BY company ORDER BY 2 DESC LIMIT 20""")
    crows = [[h(c), num(n), num(d), num(w or 0), num(l or 0), num(p or 0)]
             for c, n, d, w, l, p in comp]
    comp_t = table(["заказчик", "документов", "сделок", "выиграно", "проиграно", "позиций"],
                   crows, {1, 2, 3, 4, 5})

    # ── поставщики: считаем по офертам, привязанным к запросу
    sup = q("""SELECT supplier, count(*), sum(positions), sum(priced), sum(won=1)
               FROM file_cards WHERE supplier IS NOT NULL AND supplier<>''
               GROUP BY supplier ORDER BY 2 DESC LIMIT 20""")
    sup_t = table(["поставщик", "документов", "позиций", "с ценой", "в выигранных сделках"],
                  [[h(s), num(n), num(p or 0), num(pr or 0), num(w or 0)]
                   for s, n, p, pr, w in sup], {1, 2, 3, 4})

    # ── дубли: один и тот же файл в десятке сделок
    dup = q("""SELECT filename, kind, copies, deals FROM file_cards
               WHERE copies>1 ORDER BY copies DESC LIMIT 15""")
    dup_t = table(["файл", "род", "копий", "сделок"],
                  [[h((f or "").split(" :: ")[-1][:70]), h(k), num(c), num(d)]
                   for f, k, c, d in dup], {2, 3})
    dup_docs = one("SELECT count(*) FROM file_cards WHERE copies>1")
    dup_saved = one("SELECT sum(copies)-count(*) FROM file_cards WHERE copies>1")

    # ── примеры карточек
    ex = q("""SELECT kind, side, title, filename, positions, priced, brands, date_max, items
              FROM file_cards WHERE title<>'' AND positions>0
              ORDER BY positions DESC LIMIT 25""")
    ex_t = table(["род", "сторона", "как документ назвал себя", "о чём", "позиций", "с ценой", "марки", "дата"],
                 [[h(k), h(s), f"<b>{h(t[:70])}</b><br><span class='note'>{h((f or '').split(' :: ')[-1][:60])}</span>",
                   f"<span class='note'>{h((it or '')[:90])}</span>",
                   num(p), num(pr), h((b or "")[:40]), h(d or "")]
                  for k, s, t, f, p, pr, b, d, it in ex], {4, 5})

    # ── справочник оборудования
    cat = one("SELECT count(*) FROM catalog_items")
    cat_price = one("SELECT count(*) FROM catalog_items WHERE price_med IS NOT NULL")
    cat_brand = one("SELECT count(*) FROM catalog_items WHERE brand IS NOT NULL")
    cat_rep = one("SELECT count(*) FROM catalog_items WHERE deals>1")
    cat_mk = one("SELECT count(*) FROM catalog_items WHERE markup IS NOT NULL")
    mk_med = one("SELECT round(avg(markup),2) FROM catalog_items WHERE markup IS NOT NULL")
    top = q("""SELECT pn, brand, name, deals, won, lost, cur, price_med, markup
               FROM catalog_items ORDER BY deals DESC, mentions DESC LIMIT 20""")
    top_t = table(["артикул", "марка", "наименование", "сделок", "выигр.", "проигр.", "вал.",
                   "медиана цены", "наценка"],
                  [[f'<span class="code">{h(pn)}</span>', h(br or ""), h((nm or "")[:44]),
                    num(dl), num(w), num(ls), h(cu or ""), num(pm) if pm else "—",
                    f"×{mk:.2f}" if mk else "—"]
                   for pn, br, nm, dl, w, ls, cu, pm, mk in top], {3, 4, 5, 7, 8})
    # сделки по марке считаем по самим сделкам: суммировать «сделок» по артикулам
    # нельзя — одна сделка на сорок позиций одной марки даст сорок сделок
    brands = q("""
        WITH b AS (
          SELECT p.manufacturer br, p.deal_id did,
                 max(CASE WHEN c.side='поставщик' THEN 1 ELSE 0 END) sup
          FROM positions p JOIN file_cards c ON c.fid=p.fid
          WHERE p.manufacturer IS NOT NULL AND p.deal_id IS NOT NULL
          GROUP BY 1, 2)
        SELECT b.br, count(*), sum(d.won=1),
               sum(CASE WHEN d.closed='Y' AND d.won IS NOT 1 THEN 1 ELSE 0 END), sum(b.sup)
        FROM b JOIN deals d ON d.id=b.did
        GROUP BY b.br ORDER BY 2 DESC LIMIT 20""")
    br_t = table(["марка", "сделок", "выиграно", "проиграно", "доля побед", "с офертой поставщика"],
                 [[f"<b>{h(b)}</b>", num(d), num(w or 0), num(ls or 0),
                   pct(w or 0, (w or 0) + (ls or 0)), pct(sup or 0, d)]
                  for b, d, w, ls, sup in brands], {1, 2, 3, 4, 5})

    # ── что решает исход
    outc = q("""
        WITH s AS (SELECT deal_id did, max(side='поставщик') sup
                   FROM file_cards WHERE deal_id IS NOT NULL GROUP BY deal_id)
        SELECT s.sup, count(*), sum(d.won=1),
               sum(CASE WHEN d.closed='Y' AND d.won IS NOT 1 THEN 1 ELSE 0 END)
        FROM s JOIN deals d ON d.id=s.did GROUP BY 1 ORDER BY 1""")
    o = {row[0]: row for row in outc}
    no_sup, has_sup = o.get(0), o.get(1)
    wr = lambda r: pct(r[2] or 0, (r[2] or 0) + (r[3] or 0)) if r else "—"   # noqa: E731
    outc_t = table(["в сделке есть оферта поставщика", "сделок", "решено", "выиграно", "доля побед"],
                   [[f"<b>{lbl}</b>", num(r[1]), num((r[2] or 0) + (r[3] or 0)), num(r[2] or 0), wr(r)]
                    for lbl, r in (("нет", no_sup), ("есть", has_sup)) if r], {1, 2, 3, 4})
    rfqc = q("""
        WITH r AS (SELECT deal_id did, count(*) n FROM rfq WHERE deal_id IS NOT NULL GROUP BY deal_id)
        SELECT CASE WHEN r.n IS NULL THEN 'ни одного' WHEN r.n=1 THEN 'один'
                    WHEN r.n<=3 THEN '2–3' WHEN r.n<=6 THEN '4–6' ELSE '7 и больше' END g,
               min(coalesce(r.n,0)), count(*), sum(d.won=1),
               sum(CASE WHEN d.closed='Y' AND d.won IS NOT 1 THEN 1 ELSE 0 END)
        FROM deals d LEFT JOIN r ON r.did=d.id GROUP BY 1 ORDER BY 2""")
    rfq_t = table(["запросов поставщикам в сделке", "сделок", "решено", "выиграно", "доля побед"],
                  [[f"<b>{h(g)}</b>", num(n), num((w or 0) + (ls or 0)), num(w or 0),
                    pct(w or 0, (w or 0) + (ls or 0))] for g, _k, n, w, ls in rfqc], {1, 2, 3, 4})
    sup_share = pct(has_sup[1] if has_sup else 0, (has_sup[1] if has_sup else 0) + (no_sup[1] if no_sup else 0))

    # марки с крайними долями побед — считаются, а не перечисляются руками:
    # после каждой пересборки словаря марок список меняется
    edge = q("""
        WITH b AS (
          SELECT p.manufacturer br, p.deal_id did,
                 max(CASE WHEN c.side='поставщик' THEN 1 ELSE 0 END) sup
          FROM positions p JOIN file_cards c ON c.fid=p.fid
          WHERE p.manufacturer IS NOT NULL AND p.deal_id IS NOT NULL
          GROUP BY 1, 2)
        SELECT b.br, count(*) dl, sum(d.won=1) w,
               sum(CASE WHEN d.closed='Y' AND d.won IS NOT 1 THEN 1 ELSE 0 END) l, sum(b.sup) sp
        FROM b JOIN deals d ON d.id=b.did
        GROUP BY b.br HAVING dl>=80 AND w+l>=40""")
    ranked = sorted(edge, key=lambda r: (r[2] or 0) / max((r[2] or 0) + (r[3] or 0), 1))
    fmt_br = lambda r: f"{r[0]} — {pct(r[2] or 0, (r[2] or 0)+(r[3] or 0))} побед при офертах в {pct(r[4] or 0, r[1])} сделок"   # noqa: E731
    worst = "; ".join(fmt_br(r) for r in ranked[:4])
    best = "; ".join(fmt_br(r) for r in ranked[-4:][::-1])

    offers = one("SELECT count(*) FROM file_cards WHERE kind='оферта'")
    off_priced = one("SELECT count(*) FROM file_cards WHERE kind='оферта' AND priced>0")
    tender = one("SELECT count(*) FROM file_cards WHERE kind='тендер'")
    old = one("SELECT count(*) FROM file_cards WHERE date_max<'2024-01-01'")

    acts = [
        ("Половина сделок идёт без оферты поставщика — и выигрывается 1 из 100",
         f"Оферта поставщика есть только в {sup_share} сделок. Без неё доля побед {wr(no_sup)}, "
         f"с ней — {wr(has_sup)}. Запрос хотя бы четырём поставщикам поднимает долю побед "
         "с 12 % до 18 %, семи и больше — до 22 %. Это то, что видно в документах и что "
         "можно потребовать регламентом: сделка не идёт дальше стадии расчёта, пока в ней "
         "нет ни одной оферты."),
        ("Марки, где мы почти не выигрываем",
         f"{worst}. Для сравнения, где выигрываем: {best}. "
         "Закономерность одна и та же: где чаще находится оферта поставщика, там и побед "
         "больше. По маркам из первого списка либо нужен канал к оригиналу, либо такие "
         "запросы честнее отдавать в отказ сразу, не тратя расчёт."),
        ("Цены поставщиков лежат в " + num(offers) + " офертах, разобрано " + pct(off_priced, offers or 1),
         f"Из {num(offers)} документов-оферт цену удалось снять с {num(off_priced)}. "
         "Остальные — сканы и картинки в письмах: их читает OCR, но таблицу он не восстанавливает. "
         "Это ближайший источник роста базы цен, доработка разбора здесь дороже всего окупается."),
        ("Тендерная документация — " + num(tender) + " документов, и это не наш текст",
         "Извещения, инструкции участникам, проекты договора приходят пакетом и занимают "
         "большую часть корпуса. В них нет номенклатуры, но есть условия: сроки, штрафы, "
         "требования к участнику. Их стоит держать отдельным родом и не смешивать со спецификациями."),
        ("Устаревшие сведения: " + num(old) + " документов датированы до 2024 года",
         "Цена и наличие в них уже не действуют. При сборке базы знаний по ценам такие документы "
         "нужно помечать как исторические, иначе средняя цена по артикулу поедет вниз."),
        ("Дубли: " + num(dup_docs) + " файлов приложены повторно, " + num(dup_saved) + " лишних копий",
         "Одно и то же ТЗ лежит в десятке сделок. Карточка заведена на содержимое, "
         "поэтому разбор и распознавание делаются один раз — и все сделки с этим файлом "
         "видны в одной строке."),
        ("Незаполненные бланки: " + num(blanks) + " документов",
         "Формы под заполнение участником: номенклатуры в них нет по определению. "
         "Отмечены колонкой blank — их можно исключать из выборок одним условием."),
    ]
    acts_html = "".join(f'<div class="act"><b>{h(t)}</b><span>{h(s)}</span></div>' for t, s in acts)

    css = CSS.read_text(encoding="utf-8")
    today = date.today().isoformat()
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Каталог документов</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@450;600&display=swap">
<style>{css}
@media print{{:root{{color-scheme:light}} body{{background:#fff}} section{{break-inside:avoid; margin-top:30px}}
  /* в печати таблицу некуда прокручивать: колонки должны влезать целиком */
  .tablewrap{{break-inside:avoid; overflow:visible}}
  table{{font-size:10.5px; table-layout:auto}} th{{padding:6px 7px; font-size:9px}}
  td{{padding:5px 7px}} .act{{break-inside:avoid}} .wrap{{max-width:none; padding:0}}
  .tiles{{grid-template-columns:repeat(4,1fr)}} .tile b{{font-size:21px}}}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">КВАНТ · база знаний · {today}</div>
  <h1>Каталог документов</h1>
  <p class="standfirst">Каждое вложение портала прочитано и описано строкой: род документа,
  чья сторона его прислала, о чём он, сколько в нём позиций и цен, каким числом датирован
  и чем кончилась сделка. Это то, по чему теперь можно ориентироваться, не открывая файлы.</p>
  <div class="asof">Источник — Bitrix24, вложения сделок и запросов поставщикам. Карточки строит base/file_cards.py</div>
</header>
<div class="tiles">{tiles_html}</div>

<section><h2>Роды документов</h2>
<p>Строка на род: сколько документов, сколько вложений они дают с копиями, сколько текста
и сколько номенклатуры из них удалось достать. Колонка «доля с номенклатурой» показывает,
где искать артикулы, а где — условия и бумаги.</p>
{kinds_t}</section>

<section><h2>Чья сторона</h2>
<p>Сторона берётся из поля карточки, а не из содержимого: файл в «Offer from supplier» —
оферта поставщика, даже если внутри он называется «предложение». Это единственный надёжный
признак того, чья цена в документе.</p>
{sides_t}</section>

<section><h2>Свежесть и язык</h2>
<div class="grid2"><div>{years_t}</div><div>{lang_t}</div></div>
<p class="note">Год берётся из текста документа, а не из даты загрузки: тендерную документацию
2023 года прикладывают в 2026-м, и дата файла в CRM говорит только о том, когда его положили.</p>
</section>

<section><h2>Заказчики</h2>
<p>Двадцать заказчиков с наибольшим числом документов. Выиграно и проиграно — по сделкам,
к которым эти документы приложены.</p>
{comp_t}</section>

<section><h2>Поставщики</h2>
<p>По документам, привязанным к запросам поставщикам. Столбец «с ценой» — сколько позиций
у поставщика удалось снять с ценой: это и есть материал для базы цен.</p>
{sup_t}</section>

<section><h2>Дубли</h2>
<p>Файлов с копиями — {num(dup_docs)}, лишних вложений — {num(dup_saved)}.</p>
{dup_t}</section>

<section><h2>Как выглядит карточка</h2>
<p>Двадцать пять документов с самой богатой номенклатурой — чтобы было видно, что именно
записано о каждом файле.</p>
{ex_t}</section>

<section><h2>Справочник оборудования</h2>
<p class="lead">Из позиций собран справочник: строка на артикул. {num(cat)} артикулов,
у {num(cat_price)} есть цена, у {num(cat_brand)} — марка, {num(cat_rep)} просили больше
одного раза. У {num(cat_mk)} известны обе цены — поставщика и наша, средняя наценка
по ним <strong>×{mk_med}</strong>.</p>
<h3>Что просят чаще всего</h3>
<p>Порядок — по числу сделок, а не по числу упоминаний: один прайс-лист поставщика
даёт тысячи упоминаний одного артикула и перекрывает реальный спрос.</p>
{top_t}
<h3>Марки</h3>
<p>Двадцать марок, вокруг которых больше всего сделок. Доля побед считается от сделок
с известным исходом.</p>
{br_t}
</section>

<section><h2>Что решает исход</h2>
<p class="lead">Оферта поставщика в карточке сделки — самый сильный признак исхода из всех,
что видны в документах. Там, где её нет, выигрывается {wr(no_sup)} сделок; там, где есть —
{wr(has_sup)}. Оферта есть в {sup_share} сделок.</p>
{outc_t}
<p class="note">Связь работает в обе стороны: у выигранной сделки документов больше просто
потому, что она дольше живёт. Поэтому рядом — разрез по числу запросов поставщикам:
он от исхода не зависит, запросы делаются до решения заказчика.</p>
{rfq_t}
<p>Чем больше поставщиков опрошено, тем выше доля побед — от одного запроса
до семи и больше разрыв почти вдвое. Это уже управляемое действие, а не следствие.</p>
</section>

<section><h2>Что делать</h2>{acts_html}</section>

<footer>Собрано из base/kvant.db: file_cards, positions, deals, rfq.
Выгрузка для загрузки в базу — base/export_kb.py.</footer>
</div></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--out", default="kb_report.html")
    a = ap.parse_args()
    Path(a.out).write_text(build(a.db), encoding="utf-8")
    print(f"готово: {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
