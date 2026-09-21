#!/usr/bin/env python3
"""Таможенные декларации: кто реально везёт эту номенклатуру и по какой цене.

ЗАЧЕМ. Справочник исполнителей собран по нашим разведкам и перепискам — это те,
кого мы САМИ нашли. Декларации показывают другое: кто фактически поставлял такое
оборудование в страну, из какой страны, под какой маркой, на каких условиях
поставки и по какой цене за килограмм. Это не мнение и не оценка, а совершённые
сделки. 42 314 строк лежали в репозитории и не читались ни одним загрузчиком.

ЧТО ЗДЕСЬ МЕРИТСЯ ДО ЗАГРУЗКИ. Сопоставление с нашей номенклатурой в источнике
помечено само: tag = strong, если декларация нашлась по партномеру, и weak, если
только по коду ТН ВЭД и описанию. Строгих — 80 из 42 314. Поэтому:

  • партномер и связь «деталь → исполнитель» берутся ТОЛЬКО из strong;
  • weak идут в реестр компаний и в ценовой ориентир по товарной группе —
    но ни одна из них не становится ценой детали. Цена за килограмм по группе
    «8207 — буровой и режущий инструмент» отвечает на вопрос «сколько это
    стоит вообще», а не «сколько стоит эта деталь», и названа именно так.

ПОЛЕ «БРЕНД» ЗАПОЛНЯЮТ СЛОВОМ «ОТСУТСТВУЕТ». Это самый частый «бренд» во всей
выборке: 12 130 строк из 42 314. Ещё «НЕ ОБОЗНАЧЕН» и «НЕ ОБОЗНАЧЕНА». Они
отсеиваются списком пометок незнания (equipment.OEM_JUNK), иначе в справочнике
компаний появился бы изготовитель с таким именем — и это уже случалось.

ИМПОРТЁР — ЭТО ПОКУПАТЕЛЬ, А НЕ ИСПОЛНИТЕЛЬ. Он остаётся в реестре деклараций
(там он с ИНН и виден запросом «кто уже возит такое»), но в справочник
исполнителей не идёт: смешать того, кто делает, с тем, кто покупает, значит
сломать единственный вопрос, на который справочник отвечает.

БЕЗ APPLY=1 идёт вхолостую.

    python library/load_customs.py
    SUPABASE_DB_URL=... APPLY=1 python library/load_customs.py

ОТКУДА ДАННЫЕ И ЧТО С НИМИ НЕЛЬЗЯ. Источник — платная подписка glbs.io. Условия
подписки перепубликацию, как правило, запрещают: числа из деклараций грузятся в
закрытую базу и НЕ выносятся на страницы портала (data/catalog_notes.json,
запись zip/customs/out/). Поэтому же в журнал прогона идут только агрегаты:
число строк, число компаний, медианы по товарным группам и названия групп. Ни
одного наименования, ИНН или контрагента — репозиторий публичный.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equipment as eq  # noqa: E402
from load_equipment import part_key  # noqa: E402  (один ключ артикула на всех)
from load_suppliers import norm as norm_company  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
КАТАЛОГ = os.path.join(ROOT, "zip", "customs", "out")
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
FEED = "таможенные декларации"
# Группы ТН ВЭД, которые есть в выборке. Название нужно в журнале: «8207» само
# по себе не говорит ничего, а «буровой и режущий инструмент» говорит.
ГРУППЫ = {
    "8207": "сменный инструмент: буровой, режущий, штамповочный",
    "8431": "части подъёмного, землеройного и горного оборудования",
    "8467": "инструмент ручной пневматический и с двигателем",
    "8412": "двигатели и силовые установки прочие",
    "8466": "части и приспособления к станкам",
    "8481": "клапаны, краны, задвижки",
    "8411": "газовые турбины и турбореактивные двигатели",
    "8414": "насосы воздушные, компрессоры, вентиляторы",
    "8413": "насосы жидкостные",
}


def чисто(v, предел: int = 300) -> str | None:
    t = " ".join(str(v or "").split())
    return t[:предел] or None


def число(v) -> float | None:
    try:
        x = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def ид(*части) -> str:
    return hashlib.sha1("|".join(str(x or "") for x in части).encode()).hexdigest()[:16]


def читать() -> list[dict]:
    """Строки деклараций из всех выгрузок. Имя файла хранится источником:
    без него нельзя ни повторить выборку, ни понять, за какой она период."""
    строки = []
    if not os.path.isdir(КАТАЛОГ):
        return строки
    for имя in sorted(os.listdir(КАТАЛОГ)):
        if not имя.endswith(".json"):
            continue
        d = json.load(open(os.path.join(КАТАЛОГ, имя), encoding="utf-8"))
        for r in (d.get("matched") or []):
            if isinstance(r, dict):
                r = dict(r)
                r["_файл"] = имя
                r["_группа"] = str(d.get("hs") or "")[:4]
                строки.append(r)
    return строки


def разобрать(сырые: list[dict]) -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
    """Декларации, карточки компаний и ценовой ориентир по товарной группе."""
    декларации: dict[str, dict] = {}
    компании: dict[str, dict] = {}
    цены: list[dict] = []

    def компания(имя: str, вид: str, страна: str | None, группа: str) -> None:
        """Карточка заводится только под настоящим именем: пометки незнания
        («ОТСУТСТВУЕТ») отсекаются тем же правилом, что и в каталоге позиций."""
        имя = чисто(имя, 200) or ""
        if not имя or not eq.oem_is_real(имя):
            return
        ключ = norm_company(имя)[:200]
        if not ключ:
            return
        z = компании.setdefault(ключ, {
            "name_key": ключ, "name": имя, "kind": вид, "country": страна,
            "поставок": 0, "группы": set(), "страны": set()})
        z["поставок"] += 1
        if группа:
            z["группы"].add(группа)
        if страна:
            z["страны"].add(страна)

    for r in сырые:
        hs10 = чисто(r.get("hs10"), 12)
        группа = (hs10 or r.get("_группа") or "")[:4]
        строгое = str(r.get("tag") or "") == "strong"
        i = ид(r.get("date"), hs10, r.get("exporter"), r.get("importer"),
               r.get("desc"), r.get("val"), r.get("net_kg"))
        декларации[i] = {
            "id": i, "decl_date": чисто(r.get("date"), 20), "hs10": hs10,
            "hs4": группа or None,
            "part_number": чисто(r.get("pn"), 120) if строгое else None,
            "brand": чисто(r.get("brand"), 200), "exporter": чисто(r.get("exporter"), 300),
            "importer": чисто(r.get("importer"), 300), "importer_inn": чисто(r.get("inn"), 20),
            "origin": чисто(r.get("origin"), 8), "dispatch": чисто(r.get("dispatch"), 8),
            "incoterms": чисто(r.get("incoterms"), 12), "currency": чисто(r.get("cur"), 8),
            "net_kg": число(r.get("net_kg")), "usd_kg": число(r.get("usd_kg")),
            "value_usd": число(r.get("val")) or число(r.get("custval")),
            "descr": чисто(r.get("desc"), 1000), "match": "strong" if строгое else "weak",
            # Узел по описанию товара: «кто возит детали ротора» — вопрос
            # инженера, «8431» — вопрос таможни. Доверие ниже, чем к разметке
            # каталога: описание пишет декларант, и сверить его не с чем.
            "unit_id": eq.unit_of(чисто(r.get("desc"), 1000) or ""),
            "source": f"{FEED}: {r.get('_файл')}",
        }
        компания(r.get("exporter"), "экспортёр", чисто(r.get("dispatch"), 8), группа)
        компания(r.get("brand"), "изготовитель", чисто(r.get("origin"), 8), группа)
        if строгое and число(r.get("usd_kg")):
            цены.append({
                "part_number": чисто(r.get("pn"), 120), "price": число(r.get("usd_kg")),
                "currency": "USD", "basis": чисто(r.get("incoterms"), 12),
                "qty": 1, "qty_unit": "кг", "price_date": чисто(r.get("date"), 20),
                "country": чисто(r.get("origin"), 8),
                "exporter": чисто(r.get("exporter"), 300),
                "note": "цена за килограмм из таможенной декларации, "
                        "а не цена за штуку",
            })
    for z in компании.values():
        z["группы"] = sorted(z["группы"])
        z["страны"] = sorted(z["страны"])
    return декларации, компании, цены


def позиции(декларации: dict[str, dict]) -> dict[str, dict]:
    """Строгие совпадения — это позиции, которых в каталоге нет.

    Проверено: из 38 номеров строгих совпадений в каталоге не нашлось ни одного.
    Это не повод их выбросить — наоборот: номер, по которому есть совершённая
    поставка с ценой и экспортёром, ценнее номера из прайс-листа. Заводим как
    позиции каталога с источником «таможенная декларация», и тогда цена и
    поставщик связываются с деталью, а не висят отдельно."""
    out: dict[str, dict] = {}
    for d in декларации.values():
        if d["match"] != "strong" or not d["part_number"]:
            continue
        ключ = part_key(d["part_number"])
        if len(ключ) < 3:
            continue
        имя = (d["descr"] or d["part_number"])[:300]
        z = out.setdefault(ключ, {
            "id": ключ, "catalog_no": d["part_number"][:120], "name": имя,
            "oem": d["brand"] if d["brand"] and eq.oem_is_real(d["brand"]) else None,
            "hs_code": (d["hs10"] or "")[:12] or None,
            "unit_id": eq.unit_of(имя),
            "source": "таможенная декларация: поставка подтверждена",
            "поставок": 0})
        z["поставок"] += 1
        if not z["oem"] and d["brand"] and eq.oem_is_real(d["brand"]):
            z["oem"] = d["brand"]
    return out


def ориентир(декларации: dict[str, dict]) -> list[dict]:
    """Ценовой ориентир по товарной группе: медиана и квартили цены за килограмм.

    Ориентир, а не цена детали: в одной группе лежат и коронка, и корпус, и
    расходник. Он отвечает «двадцать долларов за килограмм для этой группы —
    это дорого или дёшево», и только на это."""
    по_группе: dict[str, list[float]] = defaultdict(list)
    for d in декларации.values():
        if d["hs4"] and d["usd_kg"]:
            по_группе[d["hs4"]].append(d["usd_kg"])
    out = []
    for группа, значения in sorted(по_группе.items()):
        значения.sort()
        if len(значения) < 20:          # на десятке строк медиана — это шум
            continue
        out.append({
            "hs4": группа, "title": ГРУППЫ.get(группа),
            "rows": len(значения),
            "p25": round(значения[len(значения) // 4], 2),
            "median": round(statistics.median(значения), 2),
            "p75": round(значения[len(значения) * 3 // 4], 2),
        })
    return out


def main() -> int:
    сырые = читать()
    if not сырые:
        print(f"нет выгрузок деклараций в {КАТАЛОГ}", file=sys.stderr)
        return 2
    декларации, компании, цены = разобрать(сырые)
    детали = позиции(декларации)
    bench = ориентир(декларации)

    виды = Counter(d["match"] for d in декларации.values())
    print("=== декларации ===")
    print(f"  строк в выгрузках: {len(сырые)}")
    print(f"  различных деклараций: {len(декларации)} "
          f"(по партномеру {виды['strong']}, по группе и описанию {виды['weak']})")
    print(f"  с ценой за килограмм: {sum(1 for d in декларации.values() if d['usd_kg'])}")
    print(f"  с условиями поставки: {sum(1 for d in декларации.values() if d['incoterms'])}")
    с_узлом = sum(1 for d in декларации.values() if d["unit_id"])
    print(f"  узел выведен из описания: {с_узлом} "
          f"({с_узлом * 100 // max(len(декларации), 1)} %) — подсказка, а не разметка: "
          f"ручной сверки деклараций у нас нет")
    print("\n=== компании ===")
    for вид, n in Counter(z["kind"] for z in компании.values()).most_common():
        print(f"  {вид:16}{n:>6}")
    # Отсев считается строками, а не названиями: «ОТСУТСТВУЕТ» — одно название и
    # двенадцать тысяч строк, и в журнале нужно видеть второе число.
    мусор = Counter()
    for r in сырые:
        имя = чисто(r.get("brand"), 200)
        if имя and not eq.oem_is_real(имя):
            мусор[имя.lower()] += 1
    print(f"  пометок незнания в поле «бренд»: {len(мусор)} названий, "
          f"{sum(мусор.values())} строк")

    print(f"\n=== позиции из подтверждённых поставок ===\n"
          f"  номеров: {len(детали)}; из них с названным изготовителем: "
          f"{sum(1 for z in детали.values() if z['oem'])}; "
          f"с определённым узлом: {sum(1 for z in детали.values() if z['unit_id'])}")

    print("\n=== ценовой ориентир по товарной группе (доллар за килограмм) ===")
    print(f"  {'группа':6}{'строк':>8}{'p25':>9}{'медиана':>10}{'p75':>9}  что в группе")
    for b in bench:
        print(f"  {b['hs4']:6}{b['rows']:>8}{b['p25']:>9.2f}{b['median']:>10.2f}"
              f"{b['p75']:>9.2f}  {b['title'] or '—'}")
    print("\n  ЭТО ОРИЕНТИР ПО ГРУППЕ, А НЕ ЦЕНА ДЕТАЛИ: в одной группе лежат и")
    print("  коронка, и корпус, и расходник. Цена детали берётся только из")
    print(f"  {виды['strong']} строгих совпадений по партномеру.")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, connect_timeout=20,
                            options="-c statement_timeout=900000")
    with conn.cursor() as cur:
        # Узел ставится только существующий: висячая ссылка уронила бы вставку
        # по внешнему ключу на сорока тысячах строк.
        cur.execute("select id from lib_units")
        узлы = {r[0] for r in cur.fetchall()}
        psycopg2.extras.execute_values(cur, """
            insert into lib_customs (id, decl_date, hs10, hs4, part_number, brand,
                                     exporter, importer, importer_inn, origin, dispatch,
                                     incoterms, currency, net_kg, usd_kg, value_usd,
                                     descr, match, unit_id, source)
            values %s
            on conflict (id) do update set
              usd_kg = excluded.usd_kg, value_usd = excluded.value_usd,
              match = excluded.match, unit_id = excluded.unit_id,
              source = excluded.source""",
            [(d["id"], d["decl_date"], d["hs10"], d["hs4"], d["part_number"], d["brand"],
              d["exporter"], d["importer"], d["importer_inn"], d["origin"], d["dispatch"],
              d["incoterms"], d["currency"], d["net_kg"], d["usd_kg"], d["value_usd"],
              d["descr"], d["match"], d["unit_id"] if d["unit_id"] in узлы else None,
              d["source"]) for d in декларации.values()],
            page_size=1000)
        conn.commit()

        # Карточки компаний. Ключ уникальности — нормализованное имя: тот же
        # экспортёр приходит в декларациях в десяти написаниях.
        cur.execute("""select 1 from pg_indexes
                        where tablename = 'lib_suppliers' and indexdef like '%name_key%'""")
        по_ключу = bool(cur.fetchone())
        конфликт = "(name_key) where name_key is not null" if по_ключу else None
        строки = [(z["name"], z["name_key"], z["kind"], z["country"] or None,
                   "low", f"{FEED}: поставок {z['поставок']}, "
                   f"группы {','.join(z['группы'])}")
                  for z in компании.values()]
        if конфликт:
            psycopg2.extras.execute_values(cur, f"""
                insert into lib_suppliers (name, name_key, kind, country, confidence,
                                           strengths)
                values %s
                on conflict {конфликт} do update set
                  country = coalesce(lib_suppliers.country, excluded.country),
                  strengths = coalesce(lib_suppliers.strengths, excluded.strengths)""",
                строки, page_size=500)
        else:
            print("  нет уникального индекса по name_key — карточки не пишутся, "
                  "иначе появятся дубли")
        conn.commit()

        # Позиции каталога из подтверждённых поставок. Пишутся ДО цен: цена
        # ссылается на деталь, и обратный порядок оставил бы её висеть.
        if детали:
            psycopg2.extras.execute_values(cur, """
                insert into lib_parts (id, catalog_no, name, oem, hs_code, unit_id, source)
                values %s
                on conflict (id) do update set
                  hs_code = coalesce(lib_parts.hs_code, excluded.hs_code),
                  oem = coalesce(lib_parts.oem, excluded.oem), updated_at = now()""",
                [(z["id"], z["catalog_no"], z["name"], z["oem"], z["hs_code"],
                  z["unit_id"] if z["unit_id"] in узлы else None, z["source"])
                 for z in детали.values()], page_size=200)
            conn.commit()

        # Цены: снимаются по потоку и пишутся заново — у цены нет естественного
        # ключа, одна деталь законно имеет и минимум, и максимум (правило потока).
        cur.execute("delete from lib_prices where feed = %s", (FEED,))
        if цены:
            psycopg2.extras.execute_values(cur, """
                insert into lib_prices (part_number, price, currency, basis, qty,
                                        qty_unit, price_date, country, exporter,
                                        source, confidence, note, feed)
                values %s""",
                [(c["part_number"], c["price"], c["currency"], c["basis"], c["qty"],
                  c["qty_unit"], c["price_date"], c["country"], c["exporter"],
                  "таможня", "med", c["note"], FEED) for c in цены], page_size=500)
        # Деталь каталога ← декларация: только строгие совпадения и только если
        # деталь в каталоге есть. Висячая ссылка значила бы «поставка неизвестно чего».
        # Ключ артикула живёт во второй миграции (schema_junk.sql). Если её ещё не
        # применили, связывание пропускается с сообщением: цены уже записаны, и
        # ронять из-за этого весь прогон незачем — связать можно следующим.
        cur.execute("select to_regprocedure('lib_pn_key(text)') is not null")
        if cur.fetchone()[0]:
            cur.execute("""
                update lib_prices p set part_id = k.id
                  from lib_parts k
                 where p.feed = %s and p.part_id is null
                   and k.id = lib_pn_key(p.part_number)""", (FEED,))
            связано = cur.rowcount
        else:
            связано = -1
            print("  нет функции lib_pn_key — вторая миграция не применена, "
                  "цены записаны, но с деталью не связаны")
        conn.commit()

        # Ребро «деталь → исполнитель» по совершённой поставке. Это сильнее
        # прайс-листа: прайс говорит «могу», декларация — «вёз».
        #
        # Ключ компании считается ТОЛЬКО нормализацией из load_suppliers и
        # только на Python. Первая версия сводила имя к ключу выражением в SQL
        # (regexp_replace по не-буквам) — и это то самое «одно правило в двух
        # местах»: норма отбрасывает формы собственности (ООО, GmbH, Co.Ltd) и
        # оставляет пробелы между словами, а выражение — нет. Совпало девять
        # рёбер вместо тридцати, и пустой результат ошибкой не выглядел.
        cur.execute("select name_key, id from lib_suppliers where name_key is not null")
        по_ключу = dict(cur.fetchall())
        cur.execute("select id from lib_parts")
        в_каталоге = {r[0] for r in cur.fetchall()}
        рёбра = set()
        for d in декларации.values():
            if d["match"] != "strong" or not d["exporter"] or not d["part_number"]:
                continue
            деталь = part_key(d["part_number"])
            компания = по_ключу.get(norm_company(d["exporter"])[:200])
            if деталь in в_каталоге and компания:
                рёбра.add((деталь, компания))
        if рёбра:
            psycopg2.extras.execute_values(cur, """
                insert into lib_part_suppliers (part_id, supplier_id, source)
                values %s on conflict (part_id, supplier_id) do nothing""",
                [(a, b, "поставка по таможенной декларации") for a, b in sorted(рёбра)],
                page_size=200)
        conn.commit()
        print(f"  рёбер «деталь → экспортёр» по поставкам: {len(рёбра)}")

        for t in ("lib_customs", "lib_suppliers", "lib_prices", "lib_parts"):
            cur.execute(f"select count(*) from {t}")
            print(f"  {t:16}{cur.fetchone()[0]:>8}")
        if связано >= 0:
            print(f"  цен, связанных с деталью каталога: {связано}")
        cur.execute("select count(distinct exporter) from lib_customs where exporter is not null")
        print(f"  экспортёров в реестре деклараций: {cur.fetchone()[0]}")
    conn.close()
    print("\n✓ декларации загружены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
