#!/usr/bin/env python3
"""Какие бренды и какие модели машин встречаются в нашем спросе и в предложениях.

ЗАЧЕМ. Распоряжение владельца 25.09.2026: наполнять бренды модельными рядами,
«особенно если они попадались у нас в запросах и предложениях». Порядок работы
задаёт не очередь разведки (dict/oem.json по числу написаний), а то, что реально
спрашивают и предлагают. Этот замер даёт такой порядок: по каждому бренду —
строки спроса (lib_demand_live), сделки, строки цены (lib_prices), карточки
запросов, и какие МОДЕЛИ машин названы рядом.

ОТКУДА БРЕНД У СТРОКИ — три пути, и они считаются порознь:
  · поле изготовителя (lib_demand.oem, lib_prices.oem) — ключом написания
    (codes_sql.ключ_написания) через реестр lib_brand_map, если он есть в базе,
    иначе через словарь dict/oem.json;
  · имя бренда в тексте строки — по aliases справочника dict/model_series.json;
  · модель ряда в тексте (library/model_series.py) — модель называет бренд сама.

ОТКУДА МОДЕЛЬ — два словаря:
  · шаблоны рядов dict/model_series.json (основной);
  · точные имена реестра машин lib_models (name и aliases) с цифрой в имени —
    отдельной таблицей, для сверки: шаблоны должны покрывать реестр, а не
    расходиться с ним.

ЧТО ПЕЧАТАЕТСЯ (только агрегаты, CLAUDE.md, правило 17). Ключи и имена брендов —
константы справочников. Модели — канонические обозначения, вырезанные шаблоном
справочника, и имена lib_models; печатаются лишь при MIN_DEALS+ сделках или
MIN_ROWS+ строках цены, чтобы редкое обозначение не выдавало конкретную заявку.
Ни наименований позиций, ни номеров сделок и карточек, ни написаний изготовителя
из данных. Неразрешённые написания изготовителя — только числом.

ДЕЛЕНИЕ. Спрос и цены читаются диапазонами идентификаторов (PARTS, по умолчанию
16, правило дробления): часть — свой диапазон id, строки в памяти не копятся,
счёт сделок — множествами на бренд и модель.

Только выборки: работает при базе в режиме только чтения. Ничего не пишет в
базу. OUT=<путь> — те же агрегаты файлом JSON (артефакт прогона).

    SUPABASE_DB_URL=… python scripts/brand_models.py
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import brands, codes_sql, model_series  # noqa: E402

MIN_DEALS = int(os.environ.get("MIN_DEALS", "2") or 2)
MIN_ROWS = int(os.environ.get("MIN_ROWS", "3") or 3)
МОДЕЛЕЙ_НА_БРЕНД = 15
БРЕНДОВ_ВНЕ = 40
ПАЧКА = 5000

ПУТИ = ("поле", "имя", "модель")


class Итог:
    """Счётчики одного источника (спрос или цены)."""

    def __init__(self):
        self.строк = 0
        self.с_брендом = 0
        self.с_моделью = 0
        self.неразрешённых_написаний = 0
        self.бренд_строк = collections.Counter()
        self.бренд_путь = collections.Counter()          # (бренд, путь) → строк
        self.бренд_сделки = collections.defaultdict(set)  # бренд → {сделка/карточка}
        self.бренд_с_ценой = collections.Counter()
        self.модель_строк = collections.Counter()         # (бренд, ряд, модель) → строк
        self.модель_сделки = collections.defaultdict(set)
        self.реестр_строк = collections.Counter()         # имя lib_models → строк
        self.реестр_сделки = collections.defaultdict(set)


def учесть(итог: Итог, *, сделка, изготовитель, текст, карта: dict,
           реестр: re.Pattern | None = None, реестр_имена: dict | None = None,
           с_ценой: bool = False, спр=None) -> None:
    """Одна строка данных → счётчики. Ничего не возвращает и не печатает."""
    итог.строк += 1
    пути = collections.defaultdict(set)
    ключ_поля = ""
    if изготовитель and str(изготовитель).strip():
        ключ_поля = карта.get(codes_sql.ключ_написания(изготовитель), "")
        if ключ_поля:
            пути[ключ_поля].add("поле")
        else:
            итог.неразрешённых_написаний += 1
    полный = " | ".join(str(x) for x in (текст, изготовитель) if x)
    for к in model_series.бренды_в_тексте(полный, спр):
        пути[к].add("имя")
    модели = model_series.модель_в_тексте(полный, {ключ_поля} if ключ_поля else (), спр)
    for бренд, ряд, модель in модели:
        пути[бренд].add("модель")
    if пути:
        итог.с_брендом += 1
    if модели:
        итог.с_моделью += 1
    for бренд, п in пути.items():
        итог.бренд_строк[бренд] += 1
        for x in п:
            итог.бренд_путь[(бренд, x)] += 1
        if сделка:
            итог.бренд_сделки[бренд].add(сделка)
        if с_ценой:
            итог.бренд_с_ценой[бренд] += 1
    for м in set(модели):
        итог.модель_строк[м] += 1
        if сделка:
            итог.модель_сделки[м].add(сделка)
    if реестр is not None and текст:
        for имя in {реестр_имена.get(m.group(0).upper(), m.group(0).upper())
                    for m in реестр.finditer(model_series.нормализовать(текст))}:
            итог.реестр_строк[имя] += 1
            if сделка:
                итог.реестр_сделки[имя].add(сделка)


def карта_брендов(cur=None) -> tuple[dict, str]:
    """Ключ написания → ключ бренда. Реестр базы важнее словаря файла."""
    with open(ROOT / "dict" / "oem.json", encoding="utf-8") as f:
        карта, _, _ = brands.карта_словаря(json.load(f))
    откуда = "dict/oem.json"
    if cur is not None:
        cur.execute("select to_regclass('lib_brand_map') is not null")
        if cur.fetchone()[0]:
            cur.execute("select spelling_key, brand_key from lib_brand_map")
            реестр = dict(cur.fetchall())
            карта = {**карта, **реестр}
            откуда = "lib_brand_map + dict/oem.json"
    # Ключи справочника рядов, которых нет в словаре, сводятся по их aliases.
    for к, б in model_series.справочник().бренды.items():
        for a in [б.get("name", "")] + list(б.get("aliases", [])):
            kk = codes_sql.ключ_написания(a)
            if len(kk) >= 3:
                карта.setdefault(kk, к)
    return карта, откуда


def словарь_реестра(строки) -> tuple[re.Pattern | None, dict]:
    """lib_models (name, aliases) → выражение точных имён с цифрой."""
    имена = {}
    for name, aliases in строки:
        for a in [name] + list(aliases or []):
            a = str(a or "").strip()
            if len(a) >= 3 and re.search(r"\d", a) and re.search(r"[A-Za-zА-Яа-я]", a) and len(a) <= 40:
                имена.setdefault(model_series.нормализовать(a).upper(), name)
    if not имена:
        return None, {}
    части = sorted(имена, key=len, reverse=True)
    rx = re.compile(model_series.Л + "(?:" + "|".join(re.escape(ч) for ч in части) + ")"
                    + model_series.П, re.I)
    return rx, имена


def _колонки(cur, таблица: str) -> set:
    cur.execute("select column_name from information_schema.columns where table_name = %s",
                (таблица,))
    return {r[0] for r in cur.fetchall()}


def _части(cur, таблица: str, частей: int):
    cur.execute(f"select min(id), max(id) from {таблица}")
    lo, hi = cur.fetchone()
    if lo is None:
        return []
    шаг = max(1, (hi - lo + 1 + частей - 1) // частей)
    return [(lo + i * шаг, lo + (i + 1) * шаг) for i in range(частей) if lo + i * шаг <= hi]


def обход(conn, таблица: str, поля: str, частей: int, на_строку) -> int:
    всего = 0
    with conn.cursor() as cur:
        части = _части(cur, таблица, частей)
    for i, (a, b) in enumerate(части, 1):
        t0 = time.monotonic()
        n = 0
        with conn.cursor(name=f"bm_{таблица}_{i}") as cur:
            cur.itersize = ПАЧКА
            cur.execute(f"select {поля} from {таблица} where id >= %s and id < %s", (a, b))
            for r in cur:
                на_строку(r)
                n += 1
        всего += n
        print(f"  {таблица}: часть {i}/{len(части)} — строк {n:,} за {time.monotonic() - t0:.0f} с",
              flush=True)
    return всего


def свод(итог: Итог, спр, имена_брендов: dict) -> dict:
    бренды_ = []
    for к, n in итог.бренд_строк.most_common():
        б = спр.бренды.get(к)
        модели = [
            {"модель": м, "ряд": р, "строк": итог.модель_строк[(к, р, м)],
             "сделок": len(итог.модель_сделки.get((к, р, м), ()))}
            for (бк, р, м) in итог.модель_строк if бк == к]
        модели.sort(key=lambda x: (-x["сделок"], -x["строк"], x["модель"]))
        бренды_.append({
            "бренд": к, "имя": (б or {}).get("name") or имена_брендов.get(к, к),
            "в_справочнике_рядов": б is not None, "origin": (б or {}).get("origin", ""),
            "строк": n, "сделок": len(итог.бренд_сделки.get(к, ())),
            "с_ценой": итог.бренд_с_ценой.get(к, 0),
            **{f"через_{п}": итог.бренд_путь.get((к, п), 0) for п in ПУТИ},
            "моделей": len(модели),
            "модели": [м for м in модели
                       if м["сделок"] >= MIN_DEALS or м["строк"] >= MIN_ROWS][:МОДЕЛЕЙ_НА_БРЕНД],
        })
    реестр = [{"машина": имя, "строк": n, "сделок": len(итог.реестр_сделки.get(имя, ()))}
              for имя, n in итог.реестр_строк.most_common()]
    реестр = [r for r in реестр if r["сделок"] >= MIN_DEALS or r["строк"] >= MIN_ROWS]
    return {"строк": итог.строк, "с_брендом": итог.с_брендом, "с_моделью": итог.с_моделью,
            "неразрешённых_написаний": итог.неразрешённых_написаний,
            "бренды": бренды_, "реестр_lib_models": реестр[:30]}


def _доля(a, b) -> str:
    return f"{100 * a / b:.1f} %" if b else "—"


def печать(заголовок: str, с: dict, единица: str) -> None:
    print(f"\n══ {заголовок} ══")
    print(f"строк {с['строк']:,}; с брендом {с['с_брендом']:,} ({_доля(с['с_брендом'], с['строк'])}); "
          f"с моделью ряда {с['с_моделью']:,} ({_доля(с['с_моделью'], с['строк'])}); "
          f"написаний изготовителя, не сведённых к бренду: {с['неразрешённых_написаний']:,} строк")
    в = [б for б in с["бренды"] if б["в_справочнике_рядов"]]
    вне = [б for б in с["бренды"] if not б["в_справочнике_рядов"]]
    в.sort(key=lambda б: (-б["сделок"], -б["строк"]))
    вне.sort(key=lambda б: (-б["сделок"], -б["строк"]))
    print(f"\nБренды со справочником рядов ({len(в)}), по {единица}:")
    print(f"  {'бренд':<34} {'род':<9} {'строк':>8} {единица:>8} {'с ценой':>8} "
          f"{'поле':>7} {'имя':>7} {'модель':>7} {'моделей':>7}")
    for б in в:
        print(f"  {б['имя'][:34]:<34} {б['origin'][:9]:<9} {б['строк']:>8,} {б['сделок']:>8,} "
              f"{б['с_ценой']:>8,} {б['через_поле']:>7,} {б['через_имя']:>7,} "
              f"{б['через_модель']:>7,} {б['моделей']:>7,}")
    print(f"\nМодели по брендам (от {MIN_DEALS} {единица} или {MIN_ROWS} строк, до {МОДЕЛЕЙ_НА_БРЕНД} на бренд):")
    for б in в:
        if б["модели"]:
            print(f"  {б['имя']}: " + "; ".join(
                f"{м['модель']} — {м['сделок']}/{м['строк']}" for м in б["модели"]))
    print(f"\nБренды БЕЗ справочника рядов — кандидаты в следующую работу (первые {БРЕНДОВ_ВНЕ}):")
    for б in вне[:БРЕНДОВ_ВНЕ]:
        print(f"  {б['имя'][:40]:<40} {б['бренд'][:30]:<30} строк {б['строк']:>8,}  {единица} {б['сделок']:>6,}")
    if с["реестр_lib_models"]:
        print("\nИмена реестра lib_models в тексте (сверка со шаблонами):")
        for r in с["реестр_lib_models"]:
            print(f"  {r['машина'][:40]:<40} строк {r['строк']:>7,}  {единица} {r['сделок']:>6,}")


def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — мерить нечего")
        return 2
    import psycopg2

    частей = max(1, int(os.environ.get("PARTS", "16") or 16))
    спр = model_series.справочник()
    print(f"справочник рядов: брендов {len(спр.бренды)}, шаблонов {len(спр.шаблоны)}; частей {частей}")
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=1500000 -c work_mem=64MB")
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("select lower('ШАЙБА') = 'шайба'")
            if not cur.fetchone()[0]:
                print("база не складывает регистр кириллицы (локаль C) — ключи считаются"
                      " иначе, чем на живой (CLAUDE.md, правило 21а)")
                return 3
            карта, откуда = карта_брендов(cur)
            print(f"карта написаний изготовителя: {len(карта):,} ключей ({откуда})")
            имена_брендов = {}
            cur.execute("select to_regclass('lib_brands') is not null")
            if cur.fetchone()[0]:
                cur.execute("select brand_key, name from lib_brands")
                имена_брендов = dict(cur.fetchall())
            реестр, реестр_имена = None, {}
            cur.execute("select to_regclass('lib_models') is not null")
            if cur.fetchone()[0]:
                cur.execute("select name, aliases from lib_models")
                реестр, реестр_имена = словарь_реестра(cur.fetchall())
            print(f"имён реестра машин с цифрой: {len(реестр_имена):,}")
            cur.execute("select to_regclass('lib_demand_live') is not null")
            спрос_табл = "lib_demand_live" if cur.fetchone()[0] else "lib_demand"
            колонки_цен = _колонки(cur, "lib_prices")

        итоги = {}
        спрос = Итог()
        print(f"\nспрос: {спрос_табл}")
        обход(conn, спрос_табл, "deal_id, oem, model, item_name", частей,
              lambda r: учесть(спрос, сделка=r[0], изготовитель=r[1],
                               текст=" | ".join(x for x in (r[3], r[2]) if x), карта=карта,
                               реестр=реестр, реестр_имена=реестр_имена, спр=спр))
        итоги["спрос"] = свод(спрос, спр, имена_брендов)

        цены = Итог()
        карточка = "rfq_id" if "rfq_id" in колонки_цен else "null"
        изг = "oem" if "oem" in колонки_цен else "null"
        бренды_запроса = "rfq_brands" if "rfq_brands" in колонки_цен else "null"
        print("\nцены: lib_prices")
        обход(conn, "lib_prices",
              f"{карточка}, {изг}, {бренды_запроса}, item_name, (price is not null and price > 0)",
              частей,
              lambda r: учесть(цены, сделка=r[0], изготовитель=r[1],
                               текст=" | ".join(x for x in (r[3], r[2]) if x), карта=карта,
                               реестр=реестр, реестр_имена=реестр_имена, с_ценой=bool(r[4]),
                               спр=спр))
        итоги["цены"] = свод(цены, спр, имена_брендов)
    finally:
        conn.close()

    печать("СПРОС (lib_demand_live)", итоги["спрос"], "сделок")
    печать("ПРЕДЛОЖЕНИЯ (lib_prices)", итоги["цены"], "карточек")
    out = os.environ.get("OUT", "").strip()
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(итоги, f, ensure_ascii=False, indent=1)
        print(f"\nагрегаты записаны: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
