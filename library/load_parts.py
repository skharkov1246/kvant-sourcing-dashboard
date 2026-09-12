#!/usr/bin/env python3
"""Запчасти, связка с исполнителями и цены — три звена цепочки портала.

ЗАЧЕМ. В zip/data лежит проработанный каталог: 752 позиции со стопроцентным
заполнением каталожного номера, изготовителя, моделей оборудования, узла,
материала и кода ТН ВЭД; 744 из них с ценовым коридором и указанием, откуда
цена взята; и 4 309 связок «позиция → поставщик». Всё это до сих пор жило
в файлах и в аналитику не попадало: lib_parts не было вовсе, lib_prices пуста,
а ребра «запчасть → исполнитель» в схеме не существовало.

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ lib_demand. Спрос — что у нас спрашивали, миллион строк из
спецификаций, качество разное. Каталог — что мы знаем о самой детали: проверено
руками, с источником цены и применяемостью. Смешивать их нельзя, поэтому таблицы
разные.

ЦЕНА ХРАНИТСЯ КОРИДОРОМ. В каталоге нет одной цены: есть минимум, максимум,
валюта и источник («рынок аналогов», «каталог ODM», «оценка по типу»). Записывать
середину как «цену» — значит потерять и разброс, и происхождение. В lib_prices
идут две строки на позицию, нижняя и верхняя граница, с общим источником.

БЕЗ APPLY=1 идёт вхолостую.

    python library/load_parts.py                        # что получится
    SUPABASE_DB_URL=... APPLY=1 python library/load_parts.py

В журнал идут только агрегаты.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_suppliers import norm as norm_company  # noqa: E402  (после sys.path)
from segments import classify, name_of  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")

POSITIONS = "zip/data/positions.json"
LINKS = "zip/data/odm_suppliers.json"
# Сплошные проверки наличия по заявкам: деталь, продавец, склад, срок и цена
# в одной строке. Это единственный наш источник, где сказано не «кто делает», а
# «у кого сейчас есть» — ради этого сорсер и звонит.
SWEEPS = (("gt/data/ship_sweep.json", "проверка наличия (ЛУКОЙЛ)"),
          ("gt/data/ship_energoseti.json", "проверка наличия (Энергосети)"))
# Цены, собранные разными способами, лежат в четырёх файлах и в аналитику не
# попадали. Поток («feed») помечает происхождение: по нему загрузчик снимает
# свои прежние строки, а в выборке видно, чему верить.
FEED_КАТАЛОГ = "каталог ЗИП"
FEED_ЗАПИСИ = "записи о ценах"
FEED_КОНКУРЕНТЫ = "прайсы конкурентов"
FEED_RFQ = "ценовые коридоры RFQ"
PAREN = re.compile(r"\([^)]*\)")
NOT_KEY = re.compile(r"[^0-9a-zа-яё]+")


def part_key(catalog_no: str, fallback: str) -> str:
    """Ключ детали — каталожный номер без пунктуации и регистра.

    Каталожный номер пишут по-разному: 56017080, 560-170-80, 56017080/A.
    Ключ без пунктуации сводит написания одной детали, но НЕ сводит разные
    детали: у них различаются сами цифры."""
    t = NOT_KEY.sub("", (catalog_no or "").lower().replace("ё", "е"))
    return t or NOT_KEY.sub("", (fallback or "").lower())[:60]


def load(path: str, key: str = "rows"):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return []
    d = json.load(open(full, encoding="utf-8"))
    return d if isinstance(d, list) else (d.get(key) or [])


def число(v):
    """Цена из проверки наличия приходит строкой, пустой строкой и числом."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(" ", "").replace(",", ".")) if v else None
    except ValueError:
        return None


def main() -> int:
    positions = load(POSITIONS)
    links = load(LINKS)
    if not positions:
        print("каталог позиций не найден", file=sys.stderr)
        return 2

    parts: dict[str, dict] = {}
    столкновения = 0
    for r in positions:
        key = part_key(r.get("catalog_norm") or r.get("catalog_no"), r.get("name", ""))
        if not key:
            continue
        текст = " ".join(str(r.get(f) or "") for f in
                         ("name", "category", "target_equipment", "model", "oem", "applications"))
        новая = {
            "id": key,
            "pos_id": r.get("id"),
            "catalog_no": str(r.get("catalog_no") or "")[:120],
            "name": str(r.get("name") or "")[:400],
            "oem": str(r.get("oem") or "")[:200] or None,
            "model": str(r.get("model") or "")[:600] or None,
            "category": str(r.get("category") or "")[:120] or None,
            "segment_id": classify(текст),
            "hs_code": str(r.get("hs_code") or "")[:120] or None,
            "material": str(r.get("material_type") or "")[:120] or None,
            "applications": str(r.get("applications") or "")[:1000] or None,
            "target_equipment": str(r.get("target_equipment") or "")[:200] or None,
            "aliases": [str(a)[:120] for a in (r.get("aliases") or []) if a][:20],
            "qty_quarter": r.get("qty_quarter"),
            "status": str(r.get("bitrix_status") or "")[:60] or None,
            "price_min": r.get("price_min"),
            "price_max": r.get("price_max"),
            "price_cur": str(r.get("price_cur") or "EUR")[:10],
            "price_src": str(r.get("price_src") or "")[:200] or None,
        }
        if key in parts:
            столкновения += 1
            continue
        parts[key] = новая

    # Проверки наличия: деталь + продавец + склад + срок + цена одной строкой.
    наличие: dict[tuple, dict] = {}
    новых_деталей = 0
    for путь, откуда in SWEEPS:
        for r in load(путь):
            pn = str(r.get("pn") or "").strip()
            key = part_key(pn, r.get("name", ""))
            if not key:
                continue
            if key not in parts:
                новых_деталей += 1
                parts[key] = {
                    "id": key, "pos_id": None, "catalog_no": pn[:120],
                    "name": str(r.get("name") or pn)[:400],
                    "oem": str(r.get("man") or r.get("real_maker") or "")[:200] or None,
                    "model": str(r.get("model") or "")[:600] or None,
                    "category": str(r.get("cat") or r.get("kind") or "")[:120] or None,
                    "segment_id": classify(f"{r.get('name') or ''} {r.get('cat') or ''}"),
                    "hs_code": None, "material": None, "applications": None,
                    "target_equipment": str(r.get("model") or "")[:200] or None,
                    "aliases": [], "qty_quarter": None, "status": None,
                    "price_min": число(r.get("usd_lo")), "price_max": число(r.get("usd_hi")),
                    "price_cur": str(r.get("currency") or "USD")[:10],
                    "price_src": откуда,
                }
            продавец = PAREN.sub(" ", str(r.get("seller") or ""))
            ключ_продавца = norm_company(продавец)
            if not ключ_продавца:
                continue
            наличие[(key, ключ_продавца)] = {
                "part_id": key, "supplier_key": ключ_продавца,
                "makes": str(r.get("note") or r.get("real_maker") or "")[:1000] or None,
                "catalog_url": str(r.get("seller_url") or "")[:400] or None,
                "confidence": "med", "source": откуда,
                "verdict": str(r.get("verdict") or "")[:40] or None,
                "in_stock": str(r.get("in_stock") or "")[:40] or None,
                "stock_qty": str(r.get("stock_qty") or "")[:40] or None,
                "lead_time": str(r.get("lead_time") or "")[:120] or None,
                "price": число(r.get("price")),
                "currency": str(r.get("currency") or "USD")[:10],
            }

    по_позиции = {p["pos_id"]: p["id"] for p in parts.values() if p["pos_id"] is not None}
    рёбра: dict[tuple, dict] = {}
    без_позиции = 0
    for r in links:
        pid = по_позиции.get(r.get("position_id"))
        if not pid:
            без_позиции += 1
            continue
        ключ_компании = norm_company(r.get("name") or "")
        if not ключ_компании:
            continue
        рёбра[(pid, ключ_компании)] = {
            "part_id": pid,
            "supplier_key": ключ_компании,
            "makes": str(r.get("makes") or "")[:1000] or None,
            "catalog_url": str(r.get("catalog_url") or "")[:400] or None,
            "confidence": str(r.get("confidence") or "med")[:10],
            "source": "каталог ЗИП",
        }

    # ─── цены из отдельных файлов ────────────────────────────────────────────
    # Ключ детали: где есть position_id — по нему, где нет — по номеру.
    по_id = {r.get("id"): part_key(r.get("catalog_norm") or r.get("catalog_no"),
                                   r.get("name", "")) for r in positions}
    цены_извне: list[dict] = []

    def цена(key, price, currency, источник, feed, **kw):
        if not key or price in (None, "", 0):
            return
        try:
            p = float(str(price).replace(" ", "").replace(",", "."))
        except ValueError:
            return
        if p <= 0:
            return
        цены_извне.append({"part_id": key, "price": p, "currency": (currency or "USD")[:10],
                           "source": (источник or feed)[:200], "feed": feed,
                           "source_url": (kw.get("url") or None) and str(kw["url"])[:400],
                           "country": (kw.get("country") or None) and str(kw["country"])[:80],
                           "year": kw.get("year") if isinstance(kw.get("year"), int) else None,
                           "exporter": (kw.get("exporter") or None) and str(kw["exporter"])[:200],
                           "confidence": (kw.get("conf") or "med")[:10]})

    for r in load("zip/data/price_records.json"):
        цена(по_id.get(r.get("position_id")), r.get("unit_price"), r.get("currency"),
             f"{r.get('source') or ''} · {r.get('exporter') or ''}".strip(" ·"),
             FEED_ЗАПИСИ, url=r.get("url"), country=r.get("country"), year=r.get("year"),
             exporter=r.get("exporter"), conf=r.get("confidence"))
    for r in load("zip/data/competitor_prices.json"):
        цена(part_key(r.get("catalog_no"), r.get("name", "")), r.get("price_rub"), "RUB",
             r.get("source"), FEED_КОНКУРЕНТЫ, conf="med")
    for r in load("gt/data/rfq_prices.json", "prices"):
        for граница, значение in (("минимум", r.get("usd_lo")), ("максимум", r.get("usd_hi"))):
            цена(part_key(r.get("pn"), ""), значение, "USD",
                 f"{r.get('basis') or 'RFQ'} · {граница}", FEED_RFQ,
                 url=r.get("url"), conf=r.get("conf"))
    for r in load("zip/data/cat_stock_2026-09.json", "positions"):
        цена(part_key(r.get("pn_norm") or r.get("pn"), r.get("name_ru", "")),
             r.get("price_exw_usd") or r.get("line_exw_usd"), "USD",
             "склад поставщика, EXW", FEED_ЗАПИСИ, conf="med")

    print("=== запчасти ===")
    print(f"  позиций в каталоге:{len(positions):>8}")
    print(f"  различных деталей: {len(parts):>8}" +
          (f"   (столкновений ключа: {столкновения})" if столкновения else ""))
    заполнено = Counter()
    for p in parts.values():
        for f in ("oem", "model", "category", "hs_code", "material", "target_equipment",
                  "applications", "aliases", "qty_quarter", "status"):
            заполнено[f] += bool(p.get(f))
    for f, n in заполнено.most_common():
        print(f"    {f:18}{n:>6}   {n / len(parts) * 100:>5.1f}%")

    seg = Counter(p["segment_id"] or "—" for p in parts.values())
    print("\n  по сегментам:")
    for sid, n in seg.most_common(8):
        print(f"    {name_of(None if sid == '—' else sid):32}{n:>6}")

    print("\n=== проверка наличия у продавцов ===")
    print(f"  строк проверки:{len(наличие):>10}   (деталей добавлено: {новых_деталей})")
    вердикты = Counter(e["verdict"] or "—" for e in наличие.values())
    for v, n in вердикты.most_common(6):
        print(f"    {v:28}{n:>6}")
    с_ценой = sum(1 for e in наличие.values() if e["price"])
    print(f"  из них с ценой продавца: {с_ценой}")

    print("\n=== связка «запчасть → исполнитель» ===")
    print(f"  связок в файле:{len(links):>10}")
    print(f"  различных рёбер:{len(рёбра):>9}" +
          (f"   (без позиции: {без_позиции})" if без_позиции else ""))
    на_деталь = Counter(e["part_id"] for e in рёбра.values())
    if на_деталь:
        print(f"  деталей с исполнителями:{len(на_деталь):>6}"
              f"   в среднем {sum(на_деталь.values()) / len(на_деталь):.1f} на деталь")
        print(f"  максимум исполнителей на деталь: {max(на_деталь.values())}")

    цены = [p for p in parts.values() if p["price_min"] is not None or p["price_max"] is not None]
    print("\n=== цены ===")
    print(f"  деталей с ценовым коридором:{len(цены):>6}   {len(цены) / len(parts) * 100:.0f}%")
    ист = Counter(p["price_src"] or "не указан" for p in цены)
    for k, n in ист.most_common(6):
        print(f"    {k[:44]:46}{n:>6}")
    print(f"\n  цен из отдельных файлов: {len(цены_извне)}")
    for k, n in Counter(c["feed"] for c in цены_извне).most_common():
        print(f"    {k:28}{n:>6}")
    print(f"  из них с ссылкой на источник: "
          f"{sum(1 for c in цены_извне if c['source_url'])}")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    import indexer
    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=900000")
    conn.autocommit = False
    with conn.cursor() as cur:
        indexer.ensure_segments(cur)
        psycopg2.extras.execute_values(cur, """
            insert into lib_parts (id, catalog_no, name, oem, model, category, segment_id,
                                   hs_code, material, applications, target_equipment, aliases,
                                   qty_quarter, status, source)
            values %s
            on conflict (id) do update set
              name = excluded.name, oem = excluded.oem, model = excluded.model,
              category = excluded.category, segment_id = excluded.segment_id,
              hs_code = excluded.hs_code, material = excluded.material,
              applications = excluded.applications, target_equipment = excluded.target_equipment,
              aliases = excluded.aliases, qty_quarter = excluded.qty_quarter,
              status = excluded.status, updated_at = now()""",
            [(p["id"], p["catalog_no"], p["name"], p["oem"], p["model"], p["category"],
              p["segment_id"], p["hs_code"], p["material"], p["applications"],
              p["target_equipment"], p["aliases"], p["qty_quarter"], p["status"], "каталог ЗИП")
             for p in parts.values()], page_size=500)

        cur.execute("select id from lib_parts")
        известные_детали = {r[0] for r in cur.fetchall()}

        # Исполнителя ищем по тому же нормализованному ключу, каким он загружен.
        cur.execute("select name_key, id from lib_suppliers where name_key is not null")
        по_ключу = dict(cur.fetchall())
        готовые = [(e["part_id"], по_ключу[e["supplier_key"]], e["makes"], e["catalog_url"],
                    e["confidence"], e["source"])
                   for e in рёбра.values() if e["supplier_key"] in по_ключу]
        не_нашлись = len(рёбра) - len(готовые)
        наличие_готовое = [
            (e["part_id"], по_ключу[e["supplier_key"]], e["makes"], e["catalog_url"],
             e["confidence"], e["source"], e["verdict"], e["in_stock"], e["stock_qty"],
             e["lead_time"], e["price"], e["currency"])
            for e in наличие.values() if e["supplier_key"] in по_ключу]
        psycopg2.extras.execute_values(cur, """
            insert into lib_part_suppliers (part_id, supplier_id, makes, catalog_url,
                                            confidence, source)
            values %s on conflict (part_id, supplier_id) do nothing""",
            готовые, page_size=500)
        # Наличие пишется вторым и обновляет ребро: «кто делает» и «у кого есть»
        # — про одну и ту же пару, и разносить их по двум строкам незачем.
        psycopg2.extras.execute_values(cur, """
            insert into lib_part_suppliers (part_id, supplier_id, makes, catalog_url,
                                            confidence, source, verdict, in_stock,
                                            stock_qty, lead_time, price, currency)
            values %s
            on conflict (part_id, supplier_id) do update set
              verdict = excluded.verdict, in_stock = excluded.in_stock,
              stock_qty = excluded.stock_qty, lead_time = excluded.lead_time,
              price = coalesce(excluded.price, lib_part_suppliers.price),
              currency = excluded.currency,
              catalog_url = coalesce(lib_part_suppliers.catalog_url, excluded.catalog_url)""",
            наличие_готовое, page_size=500)

        # Цена коридором: две строки на позицию, нижняя и верхняя граница.
        ценовые = []
        for p in цены:
            for граница, значение in (("минимум", p["price_min"]), ("максимум", p["price_max"])):
                if значение is None:
                    continue
                ценовые.append((p["segment_id"], p["name"][:400], p["catalog_no"], значение,
                                p["price_cur"], f"{p['price_src'] or 'каталог'} · {граница}",
                                None, "med", p["id"], FEED_КАТАЛОГ,
                                None, None, None))
        # Сначала снимаем прежние цены этого источника, потом вставляем заново.
        # У lib_prices нет ключа, по которому цену можно опознать — одна деталь
        # даёт две границы, и обе законны, — поэтому on conflict тут не работает,
        # а без удаления повторный прогон задваивает цены. Проверено сравнением:
        # два прогона подряд дали 3 986 строк вместо 2 498.
        потоки = [FEED_КАТАЛОГ, FEED_ЗАПИСИ, FEED_КОНКУРЕНТЫ, FEED_RFQ]
        cur.execute("delete from lib_prices where feed = any(%s) or source_url = %s",
                    (потоки, FEED_КАТАЛОГ))
        снято = cur.rowcount
        # Цена ставится только на деталь из каталога: part_id — внешний ключ.
        # Прайсы конкурентов почти целиком отваливаются здесь, и это правильно:
        # «МХ 5101.00» — номер холдинга-конкурента, а не наша позиция.
        мимо = sum(1 for c in цены_извне if c["part_id"] not in известные_детали)
        for c in цены_извне:
            if c["part_id"] in известные_детали:
                ценовые.append((None, None, None, c["price"], c["currency"], c["source"],
                                c["source_url"], c["confidence"], c["part_id"], c["feed"],
                                c["country"], c["year"], c["exporter"]))
        if мимо:
            print(f"  цен мимо каталога (детали нет у нас): {мимо} из {len(цены_извне)}")
        psycopg2.extras.execute_values(cur, """
            insert into lib_prices (segment_id, item_name, part_number, price, currency,
                                    source, source_url, confidence, part_id, feed,
                                    country, year, exporter)
            values %s""", ценовые, page_size=500)
        if снято:
            print(f"  прежних цен этого источника снято: {снято}")
        conn.commit()

        for t in ("lib_parts", "lib_part_suppliers", "lib_prices"):
            cur.execute(f"select count(*) from {t}")
            print(f"  {t:20}{cur.fetchone()[0]:>8}")
        if не_нашлись:
            print(f"  рёбер без загруженного исполнителя: {не_нашлись} "
                  "(сначала прогоните load_suppliers.py)")
    conn.close()
    print("\n✓ каталог загружен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
