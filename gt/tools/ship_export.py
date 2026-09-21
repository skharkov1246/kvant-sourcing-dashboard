#!/usr/bin/env python3
"""Машинночитаемый экспорт выкладки по заявке ЛУКОЙЛ — для внешней проверки.

PDF читает человек, но проверять по нему числа неудобно. Здесь то же самое плоской
таблицей плюс описание метода: что как считалось и где проходят границы факта.

  gt/docs/ЗАКУПКА-ЛУКОЙЛ.csv      — 1642 строки, по одной на артикул
  gt/docs/ЗАКУПКА-ЛУКОЙЛ-МЕТОД.md — определения, формулы, сводка и оговорки
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "gt/data/ship_lukoil.json"
REVERIFY = ROOT / "gt/data/ship_reverify.json"
CSV_OUT = ROOT / "gt/docs/ЗАКУПКА-ЛУКОЙЛ.csv"
MD_OUT = ROOT / "gt/docs/ЗАКУПКА-ЛУКОЙЛ-МЕТОД.md"

COLS = [
    "pn", "sheet", "cluster", "man", "model", "name", "cat", "qty", "unit",
    "verdict", "stock_grade", "in_stock", "stock_qty", "lead_time", "covers_qty",
    "seller", "seller_country", "seller_url", "seller_kind",
    "price", "currency", "pack_qty", "price_usd", "unit_price",
    "line_value_full_volume", "in_firm_total",
    "our_usd_lo", "our_usd_hi", "our_conf", "price_gap",
    "real_maker", "real_pn", "substitute", "checked_by", "note",
    # Результат перепроверки — то, ради чего сорсер и открывает эту таблицу:
    # наш вердикт по строке, найденная цена и у кого её брать. Без этих колонок
    # выгрузка показывала только состояние ДО разбора, и работу приходилось
    # переносить глазами из отчёта.
    "rv_verdict", "rv_price_usd", "rv_maker", "rv_channel", "rv_blocker", "rv_next_step",
    "addressee_1", "addressee_2", "addressee_3", "addressee_4",
]


def rv_index() -> dict:
    """Разбор перепроверки по нормализованному номеру.

    Номер в перепроверке может нести пояснение в скобках — в ключ оно не идёт,
    иначе строка не найдётся по своему же номеру.
    """
    if not REVERIFY.exists():
        return {}
    import re as _re
    out = {}
    for r in json.loads(REVERIFY.read_text(encoding="utf-8"))["rows"]:
        k = _re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").split("(")[0].upper())
        if k:
            out.setdefault(k, r)
    return out


def unit_price(r: dict):
    """Цена за штуку В ДОЛЛАРАХ — из unit_price_usd, посчитанного ship_merge.py.

    Сырой price складывать нельзя: он в девяти валютах. Это ломало не только сумму
    закупки, но и price_gap — рублёвая цена 32 566 RUB против вилки 45–165 USD
    давала «завышено в 197 раз», хотя на деле это 386 USD и завышение в 2,3 раза.
    """
    u = r.get("unit_price_usd")
    return None if u in (None, "") else float(u)


def line_value(r: dict) -> float:
    """Стоимость строки, когда продавец подтвердил ВЕСЬ заявленный объём.

    Считается по любому вердикту, в том числе «под заказ со сроком»: объём подтверждён,
    значит цену можно множить на количество.
    """
    if r.get("covers_qty") != "full":
        return 0.0
    u = unit_price(r)
    return 0.0 if u is None else u * float(r.get("qty") or 0)


def firm_value(r: dict) -> float:
    """То же, но только по твёрдому складу — это число стоит в сводке отчёта."""
    return line_value(r) if r.get("stock_grade") == "твёрдый" else 0.0


def price_gap(r: dict) -> str:
    u, lo, hi = unit_price(r), r.get("usd_lo"), r.get("usd_hi")
    if u is None or lo is None or hi is None or u <= 0:
        return ""
    mid = (lo + hi) / 2
    if not mid:
        return ""
    ratio = mid / u if u < mid else u / mid
    if ratio < 2.5:
        return ""
    return f"{'завышено' if u < mid else 'занижено'} в {ratio:.1f}x"


def addressee(sl: dict) -> str:
    """Адресат одной строкой: имя | страна | почта | телефон | основание."""
    return " | ".join([
        sl.get("seller", ""),
        sl.get("country", ""),
        ";".join(sl.get("emails") or []),
        ";".join(sl.get("phones") or []),
        sl.get("basis", ""),
    ])


def row_out(r: dict, rv_by_pn: dict | None = None) -> dict:
    u = unit_price(r)
    key = re.sub(r"[^A-Z0-9]", "", str(r.get("pn") or "").upper())
    rv = (rv_by_pn or {}).get(key)
    # свои адресаты идут первыми, кластерные — добором, основание помечено в самой строке
    addrs = (r.get("sellers") or []) + (r.get("cluster_sellers") or [])
    out = {
        "pn": r["pn"], "sheet": r["sheet"], "cluster": r.get("cluster", ""),
        "man": r["man"], "model": r["model"], "name": r["name"], "cat": r["cat"],
        "qty": r["qty"], "unit": r["unit"], "verdict": r["verdict"],
        "stock_grade": r.get("stock_grade", ""), "in_stock": r["in_stock"],
        "stock_qty": r["stock_qty"], "lead_time": r["lead_time"],
        "covers_qty": r["covers_qty"], "seller": r["seller"],
        "seller_country": r["seller_country"], "seller_url": r["seller_url"],
        "seller_kind": r["kind"], "price": r["price"], "currency": r["currency"],
        "price_usd": r.get("price_usd") or "",
        "pack_qty": r["pack_qty"],
        "unit_price": round(u, 4) if u is not None else "",
        "line_value_full_volume": round(line_value(r), 2) or "",
        "in_firm_total": "да" if firm_value(r) else "нет",
        "our_usd_lo": r.get("usd_lo", ""), "our_usd_hi": r.get("usd_hi", ""),
        "our_conf": r.get("conf", ""), "price_gap": price_gap(r),
        "real_maker": r.get("real_maker", ""), "real_pn": r.get("real_pn", ""),
        "substitute": r.get("substitute", ""), "checked_by": r.get("checked_by", ""),
        "note": r.get("note", ""),
        "rv_verdict": (rv.get("band_verdict") or "") if rv else "",
        "rv_price_usd": rv.get("price_low") if rv and isinstance(
            rv.get("price_low"), (int, float)) else "",
        "rv_maker": (rv.get("maker_short") or "") if rv else "",
        "rv_channel": (rv.get("channel") or "") if rv else "",
        "rv_blocker": (rv.get("blocker") or "") if rv else "",
        "rv_next_step": (rv.get("recommended") or "") if rv else "",
    }
    for i in range(4):
        out[f"addressee_{i + 1}"] = addressee(addrs[i]) if i < len(addrs) else ""
    return out


METHOD = """# Закупка по заявке ЛУКОЙЛ: метод и границы факта

Файл для внешней проверки. Данные — `ЗАКУПКА-ЛУКОЙЛ.csv`, {n} строк, по одной на
уникальный каталожный номер. Здесь описано, как получено каждое число и где проходит
граница между фактом и допущением.

## Откуда взялся список

Заявка ЛУКОЙЛа, два листа: «Энергосети» и «НВН». Источник — `gt/data/rfq_demand.json`,
{n} уникальных артикулов после агрегации количеств по номеру.

## Как проверялось наличие

Три поколения проверки, поздняя перекрывает раннюю:

| Источник | Строк | Чем отличается |
|---|---|---|
{sources}

Проверка шла по витринам продавцов: агент искал артикул, открывал карточку товара и
снимал наличие, срок и цену. **Ни одна строка не является котировкой** — это публичные
витрины, а не ответы на наш запрос.

Что НЕ считалось доказательством: страница поисковой выдачи, каталог без остатка,
перепечатка нашей же ведомости у китайского реверс-поставщика.

## Определения, по которым надо проверять числа

**verdict** — нашли ли канал:

{verdicts}

**stock_grade** — можно ли отгружать. Вводился потому, что один вердикт «на складе»
смешивал четыре разных состояния:

{grades}

В план отгрузки и в деньги имеет право идти только `твёрдый`.

**covers_qty** — хватает ли найденного остатка на заявленное количество:
`full` / `partial` / `no` / `unknown`.

## Формулы

```
price_usd              = price, пересчитанная в доллары по gt/data/fx_rates.json
unit_price             = price_usd / pack_qty   — В ДОЛЛАРАХ, не в валюте продавца
line_value_full_volume = unit_price * qty   ТОЛЬКО если covers_qty == "full", иначе 0
in_firm_total          = да, если при этом stock_grade == "твёрдый"
price_gap              = отношение середины нашей вилки к unit_price, если оно >= 2.5
```

Ключевое: **цена, снятая с одной карточки, действует только на подтверждённый остаток.**
Если продавец не подтвердил весь объём, строка в деньги не идёт вообще. Прежняя версия
этого не делала: сумма была завышена в 6,5 раза — 1 212 346 USD, из них 1 024 659 давали
строки с неподтверждённым объёмом и 241 776 — штучные лоты eBay.

**В файле две суммы, и их нельзя путать:**

| Сумма | Значение | Что входит |
|---|---|---|
| Твёрдый склад | {firm_val} USD | `in_firm_total = да`: наличие подтверждено остатком и объёмом. Это число стоит в PDF |
| Подтверждённый объём, любой вердикт | {full_val} USD | вся колонка `line_value_full_volume`: плюс «под заказ со сроком» и позиции, чьё наличие условное или из августовской проверки |

Разница — {diff} USD — это не склад: объём по ним подтверждён, но отгружать сегодня
нельзя. В план поставки идёт первая сумма, в оценку потенциала сделки — вторая.

## Адресаты

Колонки `addressee_1..4`, формат `имя | страна | почта | телефон | основание`.

Основание различает две разные вещи:
- `по этой детали` — продавца нашли именно по этому артикулу;
- `кластер` — родовой адрес: компания работает по классу «лист · бренд · категория»,
  но конкретно эту деталь у неё не проверяли.

Смешивать их нельзя. Контакт по самой детали есть у {own} строк ({own_pct}%), ещё
{cluster} закрыты родовым адресом, {none} строк без адресата вовсе.

Контакты собраны прямым чтением страниц самих компаний. Адреса по шаблону не
конструировались: если на сайте почта не напечатана, стоит пусто, а не догадка.

## Сводка, которую надо проверить

{summary}

## Известные слабые места

1. **{stale} строк «устаревшего» наличия** взяты из проверки 08.2026 и в сентябре не
   перепроверялись. В том прогоне 95 ссылок из 274 оказались мёртвыми, так что реальная
   цифра твёрдого склада может быть ниже.
2. **Родовой адрес — не адрес по детали.** Половина строк закрыта именно им.
3. **Часть площадок закрыта от автоматического чтения** (Radwell, eBay, Zoro, TME,
   ONERGYS, shop.solarturbines.com): «артикул не опознан» там означает «не подтверждено»,
   а не «на рынке нет».
4. **Подмена предмета задачи.** Исходно требовалось дочековать 451 строку ТКП. Список
   собран заново из заявки, потому что выгрузки прошлой сессии не сохранились, а доступа
   к Битриксу не было. Совпадают ли множества — не проверено.
5. **Цены агентов не перепроверялись вторым источником.** Там, где цена выглядит
   странно (см. колонку `price_gap`), это сигнал к ручной сверке, а не факт.
6. **Качество имён продавцов неоднородно.** Написания сведены автоматически по правилу
   «короткое имя поглощает длинное»; 14 записей помечены как описания класса поставщиков,
   а не компании, и из адресатов исключены.

## Что осмысленно проверить внешней моделью

- Сходится ли `line_value_counted` с формулой и не попали ли в сумму строки без `full`.
- Нет ли строк, где `stock_grade = твёрдый`, но `stock_qty` пуст или `covers_qty != full`.
- Не противоречит ли `note` вердикту: например, «поиском не подтверждено» при `in_stock`.
- Есть ли адресаты с основанием `по этой детали`, но без ссылки на карточку.
- Выглядит ли `unit_price` правдоподобно для изделия из `name` — грубые выбросы
  (свеча за 7 млн ₽, батарея AA за 150 USD) ловятся только так.
"""


def main() -> int:
    if not DATA.exists():
        print(f"нет {DATA} — сначала gt/tools/ship_merge.py", file=sys.stderr)
        return 1
    doc = json.loads(DATA.read_text())
    rows = doc["rows"]

    rvx = rv_index()
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(row_out(r, rvx))
    print(f"CSV: {CSV_OUT.name} {CSV_OUT.stat().st_size / 1e6:.1f} МБ, строк {len(rows)}")

    by_sheet = defaultdict(list)
    for r in rows:
        by_sheet[r["sheet"]].append(r)
    grades = Counter(r.get("stock_grade") for r in rows)
    verd = Counter(r["verdict"] for r in rows)
    src = Counter(r.get("checked_by") for r in rows)

    def has(r, fld):
        return any(sl.get("emails") or sl.get("phones") for sl in r.get(fld) or [])

    own = sum(1 for r in rows if has(r, "sellers"))
    cluster = sum(1 for r in rows if not has(r, "sellers") and has(r, "cluster_sellers"))
    firm = [r for r in rows if r.get("stock_grade") == "твёрдый"]
    firm_val = sum(line_value(r) for r in firm)
    full_val = sum(line_value(r) for r in rows)

    summary = [
        "| Показатель | Значение | Как получено |",
        "|---|---|---|",
        f"| Позиций в заявке | {len(rows)} | уникальные артикулы обоих листов |",
        *(f"| — лист «{s}» | {len(by_sheet[s])} | |" for s in sorted(by_sheet)),
        f"| Твёрдое наличие | {len(firm)} | verdict=in_stock, не conditional, "
        "не из августа, covers_qty=full |",
        f"| Закупка по твёрдым строкам | {firm_val:,.0f} USD | сумма unit_price*qty "
        "по этим строкам; цены шести валют приведены к доллару по справочному курсу "
        "на 13 Sep 2026, см. gt/data/fx_rates.json |".replace(",", " "),
        f"| Подтверждённый объём, любой вердикт | {full_val:,.0f} USD | вся колонка "
        "line_value_full_volume |".replace(",", " "),
        f"| Наличие без покрытия объёма | {grades['частичный']} | деталь есть, "
        "количества нет |",
        f"| Условное наличие | {grades['условный']} | «отгрузим, если есть» |",
        f"| Наличие из августа | {grades['устаревший']} | не перепроверялось |",
        f"| Контакт по самой детали | {own} | адресат с основанием «по этой детали» |",
        f"| Только родовой адрес | {cluster} | адресат с основанием «кластер» |",
        f"| Без адресата | {len(rows) - own - cluster} | |",
        f"| Вскрыт изготовитель узла | {sum(1 for r in rows if r.get('real_maker'))} | "
        "поле real_maker заполнено |",
        f"| Закрывается стандартом | {sum(1 for r in rows if r.get('substitute'))} | "
        "поле substitute заполнено |",
        f"| Цена вне нашей вилки в 2,5+ раза | {sum(1 for r in rows if price_gap(r))} | "
        "колонка price_gap |",
    ]

    MD_OUT.write_text(METHOD.format(
        n=len(rows),
        sources="\n".join(f"| {k or '—'} | {v} | |" for k, v in src.most_common()),
        verdicts="\n".join(f"- `{k}` — {v} строк" for k, v in verd.most_common()),
        grades="\n".join(f"- `{k}` — {v} строк" for k, v in grades.most_common()
                         if k and k != "нет"),
        own=own, own_pct=round(100 * own / len(rows)), cluster=cluster,
        none=len(rows) - own - cluster,
        summary="\n".join(summary),
        firm_val=f"{firm_val:,.0f}".replace(",", " "),
        full_val=f"{full_val:,.0f}".replace(",", " "),
        diff=f"{full_val - firm_val:,.0f}".replace(",", " "),
        stale=grades["устаревший"],
    ), encoding="utf-8")
    print(f"MD:  {MD_OUT.name} {MD_OUT.stat().st_size:,} байт")
    print(f"     обновлено {doc.get('updated')}, сборка {date.today().isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
