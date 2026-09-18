#!/usr/bin/env python3
"""Сводит ТВЁРДЫЕ цены владельца из вложений сделки с проверкой рынка по заявке.

Зачем. На защите предъявляют выставленную цену, а не разведку. Вопрос, на который
нужен ответ по каждой строке: есть ли запас на снижение и сколько его. Запас —
это разница между выставленной ценой и найденной ценой закупки; там, где рынок
дороже выставленного, запаса нет и строка убыточна.

Вход:
  gt/data/ship_lukoil.json  заявка с проверкой наличия и ценами продавцов
  --tkp <путь>              выгрузка из вложений (gt/tools/bitrix_tkp.py, полная)
  gt/data/fx_rates.json     курсы, чтобы не складывать кроны с рублями

ВЫХОД НЕ КОММИТИТСЯ. И выгрузка, и этот документ содержат цены, выставленные
заказчику, — коммерческие данные (правило 5 CLAUDE.md, SECURITY.md). Репозиторий
публичный, поэтому по умолчанию пишем в каталог вне репозитория, а сам инструмент
проверяет, что путь вывода не внутри дерева git.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "gt/data/ship_lukoil.json"
FX = ROOT / "gt/data/fx_rates.json"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
]
CUR_ALIAS = {"руб": "RUB", "RUR": "RUB", "долл": "USD", "$": "USD",
             "евро": "EUR", "€": "EUR", "₽": "RUB"}


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def ru(n) -> str:
    return f"{int(round(float(n or 0))):,}".replace(",", " ")


def money(n) -> str:
    return f"{float(n or 0):,.2f}".replace(",", " ")


def norm_key(pn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn).upper())


def load_rates() -> dict:
    if not FX.exists():
        return {"USD": 1.0}
    return json.loads(FX.read_text()).get("rates", {"USD": 1.0})


def to_usd(price, cur: str, rates: dict):
    if price in (None, ""):
        return None
    c = CUR_ALIAS.get(str(cur or "").strip(), str(cur or "USD").strip().upper()) or "USD"
    r = rates.get(c)
    if not r:
        return None
    try:
        return float(price) / r
    except (TypeError, ValueError):
        return None


# Направления файлов, из которых берётся цена. Разделение обязательно:
# прогон 17.09.2026 собрал документ «выставленные цены», не глядя на
# направление вовсе, и брал МИНИМУМ по всем файлам. То есть в графу «выставлено
# заказчику» попадала бы цена из КП поставщика — там она заведомо ниже, и запас
# на снижение выходил бы нарисованным или нулевым. Цена ошибки прямая: на
# защите такую цифру опровергает сам поставщик.
DIR_OURS = {"наша цена"}          # Result, ТКП и Economics of the project
DIR_SUPPLIER = {"входящее"}       # КП поставщика, Offer from supplier(s)
DIR_SKIP = {"наш запрос", "заявка"}   # наши исходящие и требования заказчика


def prices_by_direction(tkp_path: Path, rates: dict) -> tuple[dict, dict, dict]:
    """Три словаря «артикул → цена»: наша выставленная, КП поставщика, неопознанное.

    В каждом — НАИМЕНЬШАЯ найденная цена по артикулу, а не первая: один и тот
    же артикул попадается в нескольких файлах (исходное ТКП и правки), и
    осторожная оценка запаса берёт меньшую выставленную цену, иначе запас
    окажется нарисованным.

    «Неопознанное» держится ОТДЕЛЬНО и в графу выставленных цен не идёт: сорсер
    мог положить КП не в тот слот, но выдавать догадку за выставленную цену
    нельзя. Оно показывается своей графой, с пометкой.
    """
    doc = json.loads(tkp_path.read_text())
    ours: dict[str, dict] = {}
    supp: dict[str, dict] = {}
    unk: dict[str, dict] = {}
    for f in doc.get("files", []):
        d = (f.get("direction") or "неизвестно").strip()
        if d in DIR_SKIP:
            continue
        bucket = ours if d in DIR_OURS else supp if d in DIR_SUPPLIER else unk
        for p in f.get("prices", []):
            usd = to_usd(p.get("price"), p.get("currency"), rates)
            if usd is None:
                continue
            k = norm_key(p["pn"])
            prev = bucket.get(k)
            if prev is None or usd < prev["usd"]:
                bucket[k] = {"usd": usd, "raw_price": p["price"],
                             "currency": p.get("currency") or "USD",
                             "direction": d,
                             "field": f.get("field_name", ""),
                             "file": f.get("file_name", ""),
                             "origin": f.get("origin", ""),
                             "sheet": p.get("sheet", ""), "row": p.get("row"),
                             "rule": p.get("class_rule", ""), "line": p.get("raw", "")}
    return ours, supp, unk


def our_prices(tkp_path: Path, rates: dict) -> dict:
    """Только наша выставленная цена. Оставлено для обратной совместимости."""
    return prices_by_direction(tkp_path, rates)[0]


def price_side(ours: dict, unk: dict) -> dict:
    """Левая часть документа: цена из файла сделки, с указанием поля.

    Почему не только «наша цена». Боевой прогон 17.09.2026: у поля «Result,
    ТКП» и «Economics of the project» нашлось шесть файлов и НИ ОДНОЙ цены —
    это картинки и сканы. А 1 843 строки с ценой лежат в поле «Result file» и
    1 431 в «Техническая спецификация», которые по имени в «наша цена» не
    попадают. Если взять только «нашу цену», документ выходит пустым при
    работающей выгрузке — так и случилось.

    Гадать, что «Result file» — это наше ТКП, нельзя: подписать чужую цену
    своей хуже, чем не подписать вовсе. Поэтому берём и то и другое, но КАЖДАЯ
    строка несёт имя поля, из которого цена взята, и признак, установлено ли
    направление. Владелец смотрит на имя поля и говорит, его это файл или нет —
    это вопрос на одну минуту, а документ работает уже сейчас.

    Приоритет у «нашей цены»: если артикул есть и там и там, берём её.
    """
    out = dict(unk)
    out.update(ours)
    return out


CSS = """
@page { size: A4 landscape; margin: 9mm 8mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 7.5pt; color: #111; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 1.5mm; }
h2 { font-size: 11.5pt; margin: 0 0 2mm; border-bottom: 1.4pt solid #111; padding-bottom: 1mm; }
p { margin: 0 0 2mm; line-height: 1.38; }
.sec { page-break-before: always; }
.sec:first-child { page-break-before: auto; }
.lead { font-size: 8.5pt; }
.dim { color: #666; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tbody.p { page-break-inside: avoid; }
.t th { background: #111; color: #fff; text-align: left; padding: 1.2mm 1.4mm; font-size: 7pt; }
.t td { padding: 1.2mm 1.4mm; word-wrap: break-word; overflow-wrap: anywhere; vertical-align: top; }
.t tbody.p:nth-of-type(even) td { background: #f6f6f6; }
.t tr.b td { color: #333; font-size: 6.9pt; padding-top: 0.3mm; padding-bottom: 1.5mm; line-height: 1.3; }
.pn { font-family: "DejaVu Sans Mono", monospace; font-weight: bold; }
.bad { font-weight: bold; }
table.k { border-collapse: collapse; margin: 0 0 3mm; width: 100%; }
.k td { padding: 1.3mm 3mm 1.3mm 0; border-bottom: 0.3pt solid #ddd; vertical-align: top; }
.k td.l { font-weight: bold; }
.big { font-size: 12pt; font-weight: bold; }
ol { margin: 0 0 3mm; padding-left: 5mm; }
li { margin-bottom: 1.4mm; line-height: 1.38; }
.warn { border-left: 2.4pt solid #111; padding-left: 3mm; margin-bottom: 3mm; }
"""


def build(rows: list, ours: dict, fx_day: str, supp: dict | None = None) -> str:
    rates = load_rates()
    supp = supp or {}
    joined = []
    for r in rows:
        o = ours.get(norm_key(r["pn"]))
        sp0 = supp.get(norm_key(r["pn"]))
        # Строку берём, если есть ХОТЬ ОДНА цена из файлов сделки — наша или
        # присланная поставщиком. Прежде требовалась именно наша, и прогон по
        # «НВН» выдал пустой документ при 167 артикулах заявки, покрытых
        # ВХОДЯЩИМИ КП. Терялось самое ценное: письменное предложение
        # контрагента по этой самой заявке.
        if not o and not sp0:
            continue
        if not o:
            o = {"usd": None, "raw_price": "", "currency": "",
                 "direction": "", "field": "", "file": "", "origin": "",
                 "sheet": "", "row": "", "rule": "", "line": ""}
        web = r.get("unit_price_usd")
        web = float(web) if web not in (None, "") else None
        if web is not None and web <= 0:
            web = None         # ноль — заглушка витрины, запас по нему не считается
        # КП поставщика из вложения сделки — свидетельство СИЛЬНЕЕ карточки с
        # витрины: это письменное предложение контрагента по этой самой заявке,
        # а не цена неизвестного продавца неизвестного исполнения. Поэтому за
        # цену закупки берём его, когда оно есть, а витрину держим рядом.
        sp = sp0
        offer = sp["usd"] if sp else None
        mk = offer if offer is not None else web
        src = "КП поставщика" if offer is not None else ("витрина" if web else "")
        qty = int(r.get("qty") or 0)
        # Запас считается только когда есть И выставленная цена, И цена закупки.
        # Нет нашей цены — нет и запаса: вычитать из пустоты нельзя.
        room = (o["usd"] - mk) if (mk is not None and o["usd"] is not None) else None
        joined.append({"r": r, "o": o, "mk": mk, "web": web, "offer": offer,
                       "sp": sp, "src": src, "qty": qty, "room": room,
                       "room_total": (room * qty) if room is not None else None})

    withours = [j for j in joined if j["o"]["usd"] is not None]
    onlyoffer = sorted((j for j in joined if j["o"]["usd"] is None),
                       key=lambda j: -(j["offer"] or 0) * j["qty"])
    checked = [j for j in withours if j["mk"] is not None and j["room"] is not None]
    under = sorted((j for j in checked if j["room"] < 0), key=lambda j: j["room_total"])
    ok = sorted((j for j in checked if j["room"] >= 0), key=lambda j: -j["room_total"])
    nomk = [j for j in withours if j["mk"] is None]
    room_sum = sum(j["room_total"] for j in ok)
    loss_sum = sum(-j["room_total"] for j in under)
    firm = Counter(j["r"].get("stock_grade", "нет") for j in joined)

    parts = [
        f"<h1>Выставленные цены против рынка: {len(joined)} позиций заявки ЛУКОЙЛ</h1>",
        '<p class="lead">Слева — цена, выставленная заказчику, снятая из приложенного к '
        'сделке файла с указанием файла, листа и строки. Справа — цена закупки, найденная '
        'проверкой у продавцов. Разница и есть запас на снижение. Где рынок дороже '
        'выставленного, запаса нет: строка убыточна при текущей цене.</p>',
        f"""<table class="k"><tbody>
<tr><td class="l">Строк с КП поставщика без нашей цены</td>
<td class="big">{len(onlyoffer)}</td>
<td class="dim">по ним есть письменное предложение контрагента, но выставленной
заказчику цены в файлах сделки нет — запас посчитать нечем, а цену закупки знаем</td></tr>
<tr><td class="l">Сошлось с заявкой</td><td class="big">{len(joined)}</td>
<td class="dim">позиций, где есть и цена из файла сделки, и строка заявки. Из них
направление файла установлено как «выставлено нами» у
{sum(1 for j in joined if j["o"].get("direction") in DIR_OURS)};
у остальных поле указано в таблице, и подтвердить его — вопрос одной минуты</td></tr>
<tr><td class="l">Закупка подтверждена</td><td class="big">{len(checked)}</td>
<td class="dim">из них КП поставщика из вложений сделки — {sum(1 for j in checked if j["offer"] is not None)},
остальное карточка продавца с витрины. По {len(nomk)} строкам цены закупки нет вовсе — запас неизвестен</td></tr>
<tr><td class="l">Запас на снижение</td><td class="big">{ru(room_sum)} USD</td>
<td class="dim">сумма по {len(ok)} строкам, где выставлено дороже найденной закупки.
Это предел торга, а не прибыль: расходы, логистика и пошлины сюда не входят</td></tr>
<tr><td class="l">Строк ниже рынка</td><td class="big bad">{len(under)}</td>
<td class="dim">закупка дороже выставленного на {ru(loss_sum)} USD. Снижать по ним
нельзя — надо либо искать канал дешевле, либо пересматривать цену</td></tr>
<tr><td class="l">Твёрдое наличие среди них</td><td class="big">{firm["твёрдый"]}</td>
<td class="dim">частичное {firm["частичный"]}, условное {firm["условный"]},
без наличия {firm["нет"]}</td></tr>
</tbody></table>""",
        '<div class="warn"><h2>Как это читать на защите</h2><ol>'
        '<li><b>Запас считается к найденной цене продавца</b>, а не к нашей разведке. '
        'Разведка — подготовительный материал, её в этом документе нет.</li>'
        f'<li><b>Цены приведены к доллару</b> по справочному курсу на {E(fx_day)}. '
        'Это не курс сделки: банк даст свой, и к оплате он сдвинется.</li>'
        '<li><b>Происхождение каждой выставленной цены указано</b>: файл, лист, номер '
        'строки и правило, которым значение извлечено. Если цифра вызовет вопрос, её '
        'видно откуда проверить.</li>'
        '<li><b>Запас — не прибыль.</b> Из него ещё уйдут доставка, оформление, '
        'пошлины и риск курса.</li></ol></div>',
    ]

    cols = [
        ("Артикул", 9, lambda j: f'<span class="pn">{E(j["r"]["pn"])}</span>'),
        ("Наименование", 18, lambda j: E((j["r"].get("name") or "")[:120])),
        ("Кол-во", 4, lambda j: ru(j["qty"])),
        ("Выставлено, USD/шт", 7,
         lambda j: money(j["o"]["usd"]) if j["o"]["usd"] is not None else "—"),
        ("В валюте ТКП", 7,
         lambda j: (f'{money(j["o"]["raw_price"])} {E(j["o"]["currency"])}'
                    if j["o"]["usd"] is not None else "—")),
        ("КП поставщика, USD/шт", 7,
         lambda j: money(j["offer"]) if j["offer"] is not None else ""),
        ("Витрина, USD/шт", 6, lambda j: money(j["web"]) if j["web"] is not None else ""),
        ("Запас, USD/шт", 6, lambda j: ("" if j["room"] is None else
                                        (f'<span class="bad">{money(j["room"])}</span>'
                                         if j["room"] < 0 else money(j["room"])))),
        ("Запас на объём, USD", 7, lambda j: ("" if j["room_total"] is None else
                                              (f'<span class="bad">{ru(j["room_total"])}</span>'
                                               if j["room_total"] < 0 else ru(j["room_total"])))),
        ("Чем подтверждено", 6, lambda j: E(j["src"])),
        ("Поле, откуда цена", 9,
         lambda j: (f'{E(j["o"].get("field") or "")}'
                    + ("" if j["o"].get("direction") in DIR_OURS
                       else ' <span class="bad">направление не установлено</span>'))),
        ("Наличие", 5, lambda j: E(j["r"].get("stock_grade", ""))),
        ("Продавец", 6, lambda j: E(((j["r"].get("sellers") or [{}])[0].get("seller") or "")[:60])),
    ]

    def table(js: list) -> str:
        if not js:
            return '<p class="dim">Строк нет.</p>'
        th = "".join(f'<th style="width:{w}%">{E(t)}</th>' for t, w, _ in cols)
        bodies = []
        for j in js:
            tds = "".join(f"<td>{fn(j)}</td>" for _, _, fn in cols)
            o = j["o"]
            if o["usd"] is None:
                src = ('<b>Выставленной цены в файлах сделки НЕТ</b> — строка '
                       'попала в документ по входящему КП поставщика')
            else:
                src = (f'<b>Откуда выставленная цена:</b> {E(o["origin"])}, поле '
                       f'«{E(o.get("field") or "")}», файл «{E(o["file"])}», лист '
                       f'{E(o["sheet"])}, строка {E(o["row"])}; {E(o["rule"])}')
            sp = j.get("sp")
            if sp:
                src += (f' <b>· Откуда КП поставщика:</b> {E(sp["origin"])}, поле '
                        f'«{E(sp.get("field") or "")}», файл «{E(sp["file"])}», '
                        f'строка {E(sp["row"])}, {money(sp["raw_price"])} '
                        f'{E(sp["currency"])}')
            bodies.append(f'<tbody class="p"><tr>{tds}</tr>'
                          f'<tr class="b"><td colspan="{len(cols)}">{src}</td></tr></tbody>')
        return f'<table class="t"><thead><tr>{th}</tr></thead>{"".join(bodies)}</table>'

    for n, (title, js, lead) in enumerate([
        ("Закупка ДОРОЖЕ выставленного — запаса нет", under,
         "Самое опасное на защите. Сортировка по размеру убытка на объём."),
        ("Запас на снижение есть", ok,
         "Сортировка по размеру запаса на объём: здесь есть чем торговаться."),
        ("Есть КП поставщика, выставленной цены в файлах нет", onlyoffer,
         "Строки, по которым в приложенных файлах нашлось письменное "
         "предложение контрагента, а нашей выставленной цены нет. Запас "
         "посчитать нечем, но цена закупки подтверждена документом, а не "
         "карточкой с витрины — это готовое основание для торга."),
        ("Цена закупки не найдена — запас неизвестен", nomk,
         "Выставленная цена есть, а подтверждения закупочной нет ни КП "
         "поставщика, ни карточкой продавца. Это не «дорого» и не «дёшево» — "
         "это отсутствие данных, и складывать такие строки с остальными нельзя."),
    ], 1):
        if not js:
            continue
        parts.append(f'<div class="sec"><h2>{n}. {E(title)} — {len(js)} строк</h2>'
                     f'<p class="lead">{E(lead)}</p>' + table(js) + "</div>")

    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
            "<title>Выставленные цены против рынка</title>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def diagnose(rows: list, ours: dict, supp: dict | None = None,
             unk: dict | None = None) -> None:
    """Почему пересечение получилось таким, а не «сошлось 0 — и всё».

    Прогон 17.09.2026 извлёк 2 053 артикула с ценой и НЕ СОШЁЛСЯ с заявкой ни
    одной позицией. Одно это число не отличает «файлы не от той сделки» от
    «сопоставление сломано», а лечатся эти беды по-разному. Поэтому печатаем
    ещё три признака: форму извлечённых номеров, пересечение по началу номера
    и пересечение по цифровой части. Если все три нули — файлы действительно
    про другое; если хоть один даёт совпадения — виновато сопоставление.

    Печатаются ТОЛЬКО артикулы, без цен: журнал прогона публичный (правило 17
    CLAUDE.md), а номенклатурные номера в репозитории и так открыты.
    """
    want = {norm_key(r["pn"]) for r in rows if r.get("pn")}
    got = set(ours)
    print(f"  артикулов в заявке: {len(want)} · извлечено из файлов: {len(got)} "
          f"· пересечение точное: {len(want & got)}")
    # ПОКРЫТИЕ ПО КП ПОСТАВЩИКОВ измеряется отдельно, и это не мелочь: прогон по
    # «НВН» 17.09.2026 отчитался «сошлось 0», хотя в КП поставщиков было 321
    # артикул — их пересечение с заявкой измерителем НЕ считалось вовсе, и
    # отсутствие числа читалось как отсутствие покрытия.
    for name, other in (("в КП поставщиков", supp), ("в неопознанных файлах", unk)):
        if other is None:
            continue
        k = set(other)
        print(f"  {name}: {len(k)} артикулов · пересечение с заявкой: "
              f"{len(want & k)}")
    if not want or not got:
        return
    # по началу номера: заказчик мог приписать суффикс учётной системы
    pref = sum(1 for w in want if any(g.startswith(w[:6]) for g in got) and len(w) >= 6)
    print(f"  совпадений по первым шести знакам: {pref}")
    # по цифровой части: расхождение бывает только в буквенном префиксе
    digits_want = {re.sub(r"[^0-9]", "", w) for w in want}
    digits_got = {re.sub(r"[^0-9]", "", g) for g in got}
    digits_want.discard("")
    digits_got.discard("")
    print(f"  совпадений по цифровой части: {len(digits_want & digits_got)}")
    # форма извлечённых номеров: длина и наличие букв говорят, номера это вообще
    # или мусор разбора
    shapes = Counter()
    for g in sorted(got):
        shapes[f"{len(g)} знаков, "
               f"{'с буквами' if re.search(r'[A-Z]', g) else 'только цифры'}"] += 1
    print("  форма извлечённых номеров (топ-8): "
          + " · ".join(f"{k}: {v}" for k, v in shapes.most_common(8)))
    print("  примеры извлечённых номеров: "
          + ", ".join(sorted(got)[:12]))
    print("  примеры номеров заявки:      "
          + ", ".join(sorted(want)[:12]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tkp", required=True, help="полная выгрузка bitrix_tkp.py")
    ap.add_argument("--out", required=True, help="куда положить PDF (ВНЕ репозитория)")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    # цены заказчику в публичный репозиторий не попадают — проверяем, а не надеемся
    try:
        out.relative_to(ROOT)
    except ValueError:
        pass
    else:
        print(f"ОТКАЗ: {out} внутри репозитория, а документ содержит выставленные цены.\n"
              "Укажи путь вне дерева git.", file=sys.stderr)
        return 2

    tkp = Path(a.tkp)
    if not tkp.exists():
        print(f"нет {tkp}", file=sys.stderr)
        return 1
    rows = json.loads(DATA.read_text())["rows"]
    rates = load_rates()
    ours, supp, unk = prices_by_direction(tkp, rates)
    print(f"артикулов: в файлах с направлением «наша цена» {len(ours)}, "
          f"в КП поставщиков {len(supp)}, в файлах без установленного "
          f"направления {len(unk)}")
    side = price_side(ours, unk)
    print(f"в левую часть документа идёт {len(side)} артикулов "
          f"(с пометкой поля у каждого)")
    ours = side
    fx_day = json.loads(FX.read_text()).get("fetched", "")[5:16] if FX.exists() else ""
    print(f"артикулов с выставленной ценой: {len(ours)}")

    html_path = out.with_suffix(".html")
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(build(rows, ours, fx_day, supp), encoding="utf-8")
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--run-all-compositor-stages-before-draw", "--virtual-time-budget=120000",
         f"--print-to-pdf={out}", html_path.as_uri()],
        check=True, capture_output=True)
    matched = sum(1 for r in rows if norm_key(r["pn"]) in ours)
    print(f"сошлось со заявкой: {matched} позиций")
    diagnose(rows, ours, supp, unk)
    print(f"{out} — {out.stat().st_size / 1e6:.1f} МБ (вне репозитория, не коммитится)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
