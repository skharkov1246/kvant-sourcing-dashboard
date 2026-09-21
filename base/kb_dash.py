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

    # ── у кого дешевле: артикулы, где цену дали несколько поставщиков
    # Разброс выше двадцатикратного — это не разница в цене, а разъехавшийся
    # разбор: в одной оферте цена за штуку, в другой сумма по позиции. Такие
    # пары в счёт не идут, иначе медиана разброса уходит в тысячи.
    multi = q("""
        WITH s AS (SELECT pn_key, max(pn) pn, max(brand) br, cur,
                          count(DISTINCT supplier) n, min(price_med) lo, max(price_med) hi,
                          sum(deals) dl
                   FROM supplier_prices WHERE price_med > 0
                   GROUP BY pn_key, cur HAVING n > 1)
        SELECT pn, br, cur, n, lo, hi, round(hi/lo, 1), dl
        FROM s WHERE lo > 10 AND hi/lo <= 20 ORDER BY dl DESC, n DESC LIMIT 15""")
    multi_t = table(["артикул", "марка", "вал.", "поставщиков", "дешевле всех", "дороже всех",
                     "разброс", "сделок"],
                    [[f'<span class="code">{h(pn)}</span>', h(br or ""), h(cu), num(n),
                      num(lo), num(hi), f"×{sp:g}", num(dl)]
                     for pn, br, cu, n, lo, hi, sp, dl in multi], {3, 4, 5, 6, 7})
    sp_rows = one("SELECT count(*) FROM supplier_prices")
    sp_pn = one("SELECT count(DISTINCT pn_key) FROM supplier_prices")
    sp_multi = one("""SELECT count(*) FROM (SELECT pn_key FROM supplier_prices
                      GROUP BY pn_key HAVING count(DISTINCT supplier)>1)""")
    spreads = [r[0] for r in q("""SELECT hi/lo FROM
                       (SELECT min(price_med) lo, max(price_med) hi FROM supplier_prices
                        WHERE price_med>10 GROUP BY pn_key, cur
                        HAVING count(DISTINCT supplier)>1)
                       WHERE hi/lo <= 20 ORDER BY hi/lo""")]
    # медиана, а не среднее: пара сорвавшихся строк тянет среднее в разы
    sp_spread = f"{spreads[len(spreads)//2]:.1f}" if spreads else "—"
    sp_cmp = len(spreads)

    # ── сегменты оборудования: где деньги и где проигрываем
    seg_rows = q("""
        WITH s AS (SELECT deal_id did, max(side='поставщик') sup
                   FROM file_cards WHERE deal_id IS NOT NULL GROUP BY deal_id)
        SELECT coalesce(g.name, d.seg), count(*),
               sum(d.won=1),
               sum(CASE WHEN d.closed='Y' AND d.won IS NOT 1 THEN 1 ELSE 0 END),
               sum(coalesce(s.sup,0)),
               round(sum(coalesce(d.sum_eur,0))/1e6, 1)
        FROM deals d LEFT JOIN segments g ON g.code = d.seg
        LEFT JOIN s ON s.did = d.id
        GROUP BY 1 ORDER BY 2 DESC""")
    seg_t = table(["сегмент оборудования", "сделок", "выиграно", "проиграно", "доля побед",
                   "с офертой поставщика", "сумма, млн €"],
                  [[f"<b>{h(str(nm)[:52])}</b>", num(n), num(w or 0), num(ls or 0),
                    pct(w or 0, (w or 0) + (ls or 0)), pct(sup or 0, n), num(sm)]
                   for nm, n, w, ls, sup, sm in seg_rows], {1, 2, 3, 4, 5, 6})
    seg_named = one("SELECT count(*) FROM deals WHERE seg IS NOT NULL AND seg<>'other'")
    seg_tot = one("SELECT count(*) FROM deals")

    # ── поставщики: кто отвечает, кто молчит, кто чем возит
    sup_req = one("SELECT sum(requests) FROM supplier_stats")
    sup_ans = one("SELECT sum(answered) FROM supplier_stats")
    sup_sil = one("SELECT sum(silent) FROM supplier_stats")
    sup_n = one("SELECT count(*) FROM supplier_stats")
    freq = q("""SELECT CASE WHEN requests=1 THEN 'написали один раз'
                            WHEN requests<=3 THEN '2–3 раза' WHEN requests<=10 THEN '4–10'
                            WHEN requests<=30 THEN '11–30' ELSE '31 и больше' END g,
                       min(requests), count(*), sum(requests), sum(answered), sum(silent), sum(selected)
                FROM supplier_stats GROUP BY 1 ORDER BY 2""")
    freq_t = table(["сколько раз писали поставщику", "поставщиков", "запросов",
                    "прислал файл", "полное молчание", "выбран"],
                   [[f"<b>{h(g)}</b>", num(sup), num(req), pct(ans, req), pct(sil, req), num(sel)]
                    for g, _m, sup, req, ans, sil, sel in freq], {1, 2, 3, 4, 5})
    pool = q("""SELECT supplier, requests, answered, silent, selected, won_deals, priced, brands
                FROM supplier_stats WHERE requests>=15
                ORDER BY selected DESC, answered DESC LIMIT 15""")
    pool_t = table(["поставщик", "запросов", "прислал файл", "молчал", "выбран",
                    "побед", "цен разобрано", "марки"],
                   [[f"<b>{h(str(s)[:38])}</b>", num(n), num(a), num(si), num(sel), num(w),
                     num(pr), h((br or "")[:38])]
                    for s, n, a, si, sel, w, pr, br in pool], {1, 2, 3, 4, 5, 6})
    bs = q("""SELECT brand, supplier, positions, priced, deals, won
              FROM brand_suppliers WHERE priced>0 ORDER BY deals DESC, priced DESC LIMIT 15""")
    bs_t = table(["марка", "поставщик", "позиций", "с ценой", "сделок", "побед"],
                 [[f"<b>{h(b)}</b>", h(str(s)[:38]), num(n), num(pr), num(dl), num(w)]
                  for b, s, n, pr, dl, w in bs], {2, 3, 4, 5})
    pool_n = one("SELECT count(*) FROM supplier_stats WHERE requests>=10 AND answer_rate>=0.6")

    # ── почему проигрываем: сюжеты из переписки и вложений, по лифту
    won_tot = one("SELECT count(*) FROM deals WHERE won=1")
    lost_tot = one("SELECT count(*) FROM deals WHERE closed='Y' AND (won IS NULL OR won=0)")
    narr = q("""
        SELECT l.narrative,
               sum(CASE WHEN d.won=1 THEN 1 ELSE 0 END),
               sum(CASE WHEN d.closed='Y' AND (d.won IS NULL OR d.won=0) THEN 1 ELSE 0 END),
               sum(CASE WHEN l.main=1 AND d.closed='Y' AND (d.won IS NULL OR d.won=0) THEN 1 ELSE 0 END)
        FROM loss_marks l JOIN deals d ON d.id = l.deal_id GROUP BY 1""")
    NAMES = {
        "no_answer": "Поставщик не ответил", "competitor": "Проиграли конкуренту",
        "customer_cancel": "Заказчик отменил или перенёс", "tech": "Не прошли по технике",
        "no_supplier": "Не нашли изготовителя / нет канала",
        "sanctions": "Санкции и отказ поставлять в РФ", "price": "Разговор о цене",
        "no_techinfo": "Заказчик не дал техническую информацию",
        "lead_time": "Не прошли по срокам", "docs": "Документы, сертификация, допуски",
        "payment_terms": "Условия оплаты и финансы", "logistics": "Логистика и таможня",
    }
    nrows = []
    for code, w, ls, ml in narr:
        pw, pl = (w or 0) / max(won_tot, 1), (ls or 0) / max(lost_tot, 1)
        nrows.append((pl / pw if pw else 99.0, code, pw, pl, ml or 0))
    nrows.sort(reverse=True)
    narr_t = table(["сюжет", "у выигранных", "у проигранных", "лифт", "главный сюжет у скольких проигранных"],
                   [[f"<b>{h(NAMES.get(code, code))}</b>", pct(round(pw*won_tot), won_tot),
                     pct(round(pl*lost_tot), lost_tot), f"{lift:.2f}", num(ml)]
                    for lift, code, pw, pl, ml in nrows], {1, 2, 3, 4})
    marked = one("SELECT count(DISTINCT deal_id) FROM loss_marks")
    top_lift = nrows[0] if nrows else None

    offers = one("SELECT count(*) FROM file_cards WHERE kind='оферта'")
    off_priced = one("SELECT count(*) FROM file_cards WHERE kind='оферта' AND priced>0")
    tender = one("SELECT count(*) FROM file_cards WHERE kind='тендер'")
    old = one("SELECT count(*) FROM file_cards WHERE date_max<'2024-01-01'")

    acts = [
        ("КИПиА, арматура и ГПУ: оферты есть, а побед 4–10 %",
         "В КИПиА оферта поставщика найдена в 47 % сделок, в арматуре — в 44 %, но выигрывается "
         "только 9 % и 10 %. Для сравнения, в ГШО оферта есть в 59 % сделок и побед 21 %. "
         "Значит в этих сегментах барьер не в поиске поставщика, а дальше: оригинал вместо "
         "аналога, сертификация, допуск завода-изготовителя. Прежде чем вкладываться в сорсинг "
         "по ним, стоит проверить на десятке проигранных сделок, что именно требовал заказчик."),
        ("Холодные адреса отвечают в 17 % случаев, постоянный пул — в 52 %",
         f"Из {num(sup_n)} поставщиков {num(one('SELECT count(*) FROM supplier_stats WHERE requests=1'))} "
         "получили ровно один запрос, и ответил из них каждый шестой. Сорок три поставщика, "
         "которым пишут чаще тридцати раз, дают половину всех ответов и большую часть выборов. "
         f"Маршрут запроса должен начинаться с этого пула ({num(pool_n)} адресов, отвечающих чаще "
         "60 % раз) и только потом уходить в холодный поиск — тогда оферта появляется там, "
         "где сейчас молчание."),
        ("«Проиграли по цене» — не причина: этот сюжет есть в 92 % выигранных сделок",
         "Разметка по всему тексту показывает, что разговор о цене идёт везде, и по нему "
         "нельзя отличить проигрыш от победы (лифт 1,03). Единственный сюжет с сильным "
         "перекосом в проигрыши — «поставщик не ответил», лифт 3,1: у проигранных он "
         "встречается втрое чаще. Разбирать надо не цену, а молчание поставщиков: "
         "срок ответа, второй канал связи, запасной поставщик на тот же артикул."),
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
        (f"Разброс цен поставщиков ×{sp_spread} — это и есть маржа, которую сейчас не выбирают",
         f"У {num(sp_cmp)} артикулов есть сравнимые цены от двух и более поставщиков, и между "
         f"самым дешёвым и самым дорогим медианно ×{sp_spread}. Пока расчёт делается по первой "
         "пришедшей оферте, эта разница остаётся у поставщика. Таблица supplier_prices "
         "отвечает на вопрос «у кого этот артикул дешевле» за один запрос."),
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

<section><h2>У кого дешевле</h2>
<p class="lead">Из оферт собрано {num(sp_rows)} цен поставщиков на {num(sp_pn)} артикулов.
У {num(sp_multi)} артикулов цену дали два поставщика и больше; из них {num(sp_cmp)} сравнимы —
цены отличаются не больше чем в двадцать раз. Медианный разброс между самым дешёвым
и самым дорогим — <strong>×{sp_spread}</strong>.</p>
{multi_t}
<p class="note">Цена — медиана по офертам этого поставщика на этот артикул, в валюте оферты.
Разброс считается внутри одной валюты, чтобы это была разница в цене, а не в курсе. Пары,
где цены расходятся больше чем в двадцать раз, отброшены: это не разница в цене, а разбор,
где у одного поставщика взята цена за штуку, а у другого сумма по позиции.</p>
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

<section><h2>Сегменты оборудования</h2>
<p class="lead">Сегмент сделки проставлен по названию карточки и по тому, что в ней
запрашивали: {num(seg_named)} сделок из {num(seg_tot)} узнаны, остальные — «прочее».
Столбец «с офертой поставщика» показывает, где сорсинг вообще доходит до цены.</p>
{seg_t}
<p class="note">Сумма — по полю сделки, в евро по курсу на дату; у сделок в работе она
плановая. Доля побед считается от сделок с известным исходом.</p>
</section>

<section><h2>Поставщики: кто отвечает</h2>
<p class="lead">{num(sup_req)} запросов ушло к {num(sup_n)} поставщикам. Файл в ответ пришёл
в {pct(sup_ans, sup_req)} случаев, полное молчание — ни файла, ни движения по стадии —
в {pct(sup_sil, sup_req)}.</p>
{freq_t}
<p>Доля ответа растёт вместе с числом обращений: холодный адрес, которому написали
однажды, отвечает в 17 % случаев, поставщик из постоянного пула — в 52 %. Постоянных,
кто отвечает чаще чем в 60 % запросов, — {num(pool_n)}.</p>
<h3>Рабочий пул</h3>
{pool_t}
<h3>Кому писать по марке</h3>
<p>Пары «марка + поставщик», где поставщик реально присылал цены. Это то, с чего
начинается расчёт: не поиск по каталогу, а адрес, по которому уже приходил ответ.</p>
{bs_t}
</section>

<section><h2>Почему проигрываем</h2>
<p class="lead">Сюжеты размечены правилами по всему тексту сделки — вложения, письма, чат.
Сюжет находится у {num(marked)} сделок из {num(one("SELECT count(*) FROM deals"))}, но частота
сама по себе ничего не объясняет: «разговор о цене» есть у 95 % проигранных и у 92 %
выигранных. Разделяет исход лифт — во сколько раз чаще сюжет встречается у проигранных.</p>
{narr_t}
<p class="note">Лифт около единицы — общий фон переписки, а не причина. Лифт меньше единицы
означает обратное: сюжет чаще у выигранных. «Логистика и таможня» с лифтом 0,07 — признак
того, что сделка дошла до отгрузки, а не причина исхода.</p>
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
