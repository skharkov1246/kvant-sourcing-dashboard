"""Перекрёстная система «позиция ↔ поставщик»: сборка снимка для двух карточек.

ТЗ владельца, 22.09.2026, дословно по смыслу:

    Карточка товара — это про номенклатурную позицию. Там должны быть сведения
    в том числе, кто нам на неё выдавал предложение. Так же, как карточка
    поставщика, где будет список позиций и брендов, на которые он выдавал
    предложение. Перекрёстная система: мы понимаем всё по позиции — кто вообще
    что давал, и понимаем по поставщику — чего от кого ожидать. В карточке
    товара также должно быть, кто является ОЕМ, ОДМ, оригинальным, какие могут
    быть аналоги.

Это ОДИН граф, прочитанный с двух сторон, поэтому и снимок один: разойдись
две сборки — и страница позиции покажет поставщика, которого страница
поставщика не знает.

ЧТО ЗДЕСЬ НЕ СМЕШИВАЕТСЯ, И ПОЧЕМУ. Четыре разных утверждения об изготовителе
приходят из четырёх мест, и свести их в одно поле «производитель» значит
потерять то, чем они отличаются:

  · lib_prices.oem      — изготовитель, НАЗВАННЫЙ ПОСТАВЩИКОМ В ФАЙЛЕ. Это его
                          слово, ничем не подтверждённое.
  · lib_prices.rfq_brands — бренды, проставленные НА КАРТОЧКЕ ЗАПРОСА нашим
                          сотрудником. Это наше намерение, а не ответ рынка.
  · lib_parts.oem       — изготовитель ПО КАТАЛОГУ. Ближе всего к «оригиналу».
  · lib_part_alt.alt_maker при kind='номер изготовителя' — ЧЕЙ ЭТО НОМЕР НА
                          САМОМ ДЕЛЕ. Telsmith 14T47 — серийный подшипник
                          SKF/Timken, и без этой строки сорсер ищет
                          несуществующую деталь у несуществующего изготовителя.

РОЛЬ КОМПАНИИ (OEM / ODM / дистрибьютор / сервис / трейдер) лежит в
lib_suppliers.kind — в реестре ИСПОЛНИТЕЛЕЙ, собранном разведкой. Компания,
приславшая КП, живёт в другом реестре (sup_entity, сведение по Битриксу). Эти
два реестра ещё не сведены между собой, и здесь они НЕ склеиваются по имени:
совпадение названий — не доказательство. Карточка показывает обе стороны
раздельно и говорит, что связи между ними пока нет.

АНАЛОГИ РАЗНЫЕ. kind в lib_part_alt — это «номер изготовителя», «замена»,
«аналог», «наш номер». Первое означает «это та же деталь под родным номером»,
третье — «похожая деталь другого производителя». Для закупки разница
решающая, поэтому kind едет в снимок, а не схлопывается в «аналоги».
"""
from __future__ import annotations

import collections

FEED = "разбор КП"

# Сколько предложений показывать в карточке позиции. Больше человек всё равно
# не читает, а снимок растёт линейно. Число показанных и общее — рядом, чтобы
# усечение было видно (молчаливое усечение — та же ложь, что молчаливый отсев).
ПРЕДЛОЖЕНИЙ_НА_ПОЗИЦИЮ = 25
# То же для карточки поставщика.
ПОЗИЦИЙ_НА_ПОСТАВЩИКА = 200

ПРЕДЛОЖЕНИЯ_SQL = """
select lib_pn_key(p.part_number)      as ключ,
       p.part_number                  as написание,
       p.item_name                    as наименование,
       p.rfq_company                  as компания_ключ,
       e.id                           as сущность,
       e.display_name                 as сущность_имя,
       p.oem                          as изготовитель_из_файла,
       p.rfq_brands                   as бренды_с_карточки,
       p.price, p.currency, p.qty, p.qty_unit, p.basis, p.lead_days,
       p.rfq_id                       as карточка,
       p.confidence,
       p.price_date, p.created_at
  from lib_prices p
  left join sup_identifier i
         on i.kind = 'bitrix' and i.status <> 'rejected'
        and i.value_norm = p.rfq_company
  left join sup_entity e on e.id = i.sup_id
 where p.feed = %s
   and coalesce(btrim(p.part_number), '') <> ''
   and lib_pn_key(p.part_number) <> ''
 order by lib_pn_key(p.part_number), p.created_at desc, p.id desc
"""

# Каталожная часть карточки. Соединяется по тому же ключу, что и цена: позиция,
# не нашедшаяся в каталоге, покажет предложения и не покажет ни аналогов, ни
# машины — и скажет об этом словами, а не пустотой.
КАТАЛОГ_SQL = """
with ключи as (
  select distinct lib_pn_key(part_number) as ключ
    from lib_prices
   where feed = %s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
)
select p.id, p.catalog_no, p.name, p.oem, p.category, p.target_equipment, p.kv_no
  from ключи к join lib_parts p on p.id = к.ключ
"""

АНАЛОГИ_SQL = """
with ключи as (
  select distinct lib_pn_key(part_number) as ключ
    from lib_prices
   where feed = %s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
)
select a.part_id, a.alt_pn, a.kind, a.alt_maker, a.confidence
  from ключи к join lib_part_alt a on a.part_id = к.ключ
 order by a.part_id, a.kind, a.alt_pn
"""

МАШИНЫ_SQL = """
with ключи as (
  select distinct lib_pn_key(part_number) as ключ
    from lib_prices
   where feed = %s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
)
select m.part_id, coalesce(mo.name, m.model_id) as машина
  from ключи к
  join lib_part_models m on m.part_id = к.ключ
  left join lib_models mo on mo.id = m.model_id
 order by m.part_id, 2
"""

# КТО ЭТО ДЕЛАЕТ И В КАКОЙ РОЛИ. Ровно то, о чём ТЗ говорит «кто является ОЕМ,
# ОДМ». Роль — слово реестра исполнителей, а не наша оценка.
ИЗГОТОВИТЕЛИ_SQL = """
with ключи as (
  select distinct lib_pn_key(part_number) as ключ
    from lib_prices
   where feed = %s and coalesce(btrim(part_number), '') <> ''
     and lib_pn_key(part_number) <> ''
)
select ps.part_id, s.name, s.kind, s.country, ps.makes, ps.verdict, ps.confidence
  from ключи к
  join lib_part_suppliers ps on ps.part_id = к.ключ
  join lib_suppliers s on s.id = ps.supplier_id
 order by ps.part_id, s.name
"""


def _бренды(строка) -> list[str]:
    """Список брендов из поля-сцепки. Пустые элементы — не бренды.

    «FAG,» с запятой в хвосте и значение из одного пробела дают элемент-пустышку;
    без отсева у компании без брендов бренд оказывается один.
    """
    if not строка:
        return []
    return sorted({ч.strip() for ч in str(строка).split(",") if ч.strip()})


def _дата(v) -> str | None:
    return v.isoformat()[:10] if v is not None and hasattr(v, "isoformat") else None


def _число(v):
    """Numeric из psycopg2 — Decimal; в JSON он не сериализуется."""
    return float(v) if v is not None else None


def собрать(предложения, каталог=(), аналоги=(), машины=(), изготовители=()):
    """Строки базы → снимок для страниц. Чистая функция: тестируется без базы."""
    по_позиции = collections.defaultdict(list)
    for r in предложения:
        по_позиции[r[0]].append(r)

    кат = {r[0]: r for r in каталог}
    альт = collections.defaultdict(list)
    for part_id, alt_pn, kind, maker, conf in аналоги:
        альт[part_id].append({"pn": alt_pn, "kind": kind, "maker": maker,
                              "conf": conf})
    маш = collections.defaultdict(list)
    for part_id, имя in машины:
        маш[part_id].append(имя)
    изг = collections.defaultdict(list)
    for part_id, имя, роль, страна, делает, вердикт, conf in изготовители:
        изг[part_id].append({"name": имя, "role": роль, "country": страна,
                             "makes": делает, "verdict": вердикт, "conf": conf})

    позиции = []
    по_компании = collections.defaultdict(lambda: {"позиции": {}, "бренды": set(),
                                                   "строк": 0, "имя": None,
                                                   "сущность": None})
    с_выбором = сравнимых = в_каталоге = 0

    for ключ, строки in по_позиции.items():
        к = кат.get(ключ)
        # НОМЕР ДЛЯ ЧЕЛОВЕКА — КАТАЛОЖНЫЙ, ЕСЛИ ОН ЕСТЬ. Иначе самое частое
        # написание из котировок: выбирать первое попавшееся значит показывать
        # разный номер при каждой пересборке.
        написания = collections.Counter(r[1] for r in строки if r[1])
        номер = (к[1] if к else None) or (написания.most_common(1)[0][0]
                                          if написания else ключ)
        наименования = collections.Counter(r[2] for r in строки if r[2])
        имя = (к[2] if к else None) or (наименования.most_common(1)[0][0]
                                        if наименования else None)

        предл = []
        компании = set()
        валюты = set()
        с_ценой = 0
        изг_из_файлов = collections.Counter()
        бренды_позиции = set()
        for (_, написание, _наим, комп, сущ, сущ_имя, оем, бренды, цена, вал,
             qty, ед, базис, срок, карточка, conf, дата, создано) in строки:
            if комп:
                компании.add(комп)
            if оем:
                изг_из_файлов[оем] += 1
            бренды_позиции.update(_бренды(бренды))
            if цена is not None and вал:
                с_ценой += 1
                валюты.add(вал)
            предл.append({
                "co": комп, "ent": сущ, "ent_name": сущ_имя,
                "oem": оем, "brands": _бренды(бренды),
                "price": _число(цена), "cur": вал,
                "qty": _число(qty), "unit": ед, "basis": базис,
                "lead": срок, "rfq": карточка, "conf": conf,
                "date": _дата(дата) or _дата(создано),
                "pn": написание,
            })
            if комп:
                с = по_компании[комп]
                с["строк"] += 1
                с["сущность"] = с["сущность"] or сущ
                с["имя"] = с["имя"] or сущ_имя
                с["бренды"].update(_бренды(бренды))
                п = с["позиции"].setdefault(ключ, {"n": номер, "name": имя,
                                                   "cnt": 0, "price": None,
                                                   "cur": None, "date": None})
                п["cnt"] += 1
                if п["price"] is None and цена is not None:
                    п["price"], п["cur"] = _число(цена), вал
                п["date"] = п["date"] or _дата(дата) or _дата(создано)

        if len(компании) >= 2:
            с_выбором += 1
        # Сравнимость — только внутри одной валюты. Пересчёт по курсу здесь не
        # делается намеренно: курс на дату котировки мы не храним, а курс на
        # сегодня превратил бы прошлогоднее КП в сегодняшнее предложение.
        сравнима = с_ценой >= 2 and len(валюты) == 1
        if сравнима:
            сравнимых += 1
        if к:
            в_каталоге += 1

        позиции.append({
            "k": ключ,
            "n": номер,
            "name": имя,
            "co": len(компании),
            "offers": len(строки),
            "shown": min(len(предл), ПРЕДЛОЖЕНИЙ_НА_ПОЗИЦИЮ),
            "cmp": сравнима,
            # Четыре утверждения об изготовителе — четырьмя полями. Слияние их
            # в одно потеряло бы, кто это сказал.
            "oem_file": [и for и, _ in изг_из_файлов.most_common(5)],
            "oem_cat": (к[3] if к else None),
            "brands": sorted(бренды_позиции),
            "makers": изг.get(ключ, []),
            "alts": альт.get(ключ, []),
            "models": маш.get(ключ, []),
            "cat": bool(к),
            "cat_name": (к[2] if к else None),
            "category": (к[4] if к else None),
            "unit": (к[5] if к else None),
            "kv": (к[6] if к else None),
            "list": предл[:ПРЕДЛОЖЕНИЙ_НА_ПОЗИЦИЮ],
        })

    # Позиции — по числу предложивших компаний: сверху то, где есть выбор.
    позиции.sort(key=lambda p: (-p["co"], -p["offers"], p["n"] or ""))

    компании = []
    for ключ, с in по_компании.items():
        поз = sorted(с["позиции"].items(),
                     key=lambda kv: (-kv[1]["cnt"], kv[1]["n"] or ""))
        компании.append({
            "co": ключ,
            "ent": с["сущность"],
            "name": с["имя"],
            "rows": с["строк"],
            "parts": len(поз),
            "brands": sorted(с["бренды"]),
            "list": [dict(v, k=k) for k, v in поз[:ПОЗИЦИЙ_НА_ПОСТАВЩИКА]],
        })
    компании.sort(key=lambda c: (-c["parts"], -c["rows"], c["co"]))

    сведённых = sum(1 for c in компании if c["ent"])
    return {
        "version": 1,
        "positions": позиции,
        "companies": компании,
        "totals": {
            "positions": len(позиции),
            "with_choice": с_выбором,
            "comparable": сравнимых,
            "in_catalog": в_каталоге,
            "companies": len(компании),
            "companies_resolved": сведённых,
            "offers": sum(p["offers"] for p in позиции),
        },
    }
