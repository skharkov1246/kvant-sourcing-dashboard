"""Карточка бренда и сводка «код · цена · бренд · поставщик»: чистая сборка снимка.

ЗАЧЕМ. Этап 8.1 плана (docs/suppliers/IMPLEMENTATION_PLAN.md): по бренду видно,
какие машины и узлы под ним, где он стоит, сколько его деталей в каталоге,
сколько кодов его спрашивали и кто давал по ним цену. Владелец дополнил
(24.09.2026): страница за входом, без выгрузок руками, со сводкой «сколько кодов
в базе», облаками брендов и поставщиков и переходами бренд → поставщики и коды →
код → цены по поставщикам → поставщик → его бренды и коды.

КАК УСТРОЕН СНИМОК. Один граф, прочитанный с трёх сторон, поэтому сборка одна,
а ключей KV несколько — предел одного ключа 8 МиБ:

  · brands:v1        — сводка, заполненность, бренды, поставщики;
  · brands:links:v1  — указатель кодов: у какого кода какие бренды и
                       поставщики и в какой корзине его подробности;
  · brands:pairs:v1  — пары «бренд × поставщик»: сколько кодов бренда
                       поставщик прокотировал, своим ли словом назван бренд;
  · brands:codes:NN  — подробности кода: цены по поставщикам, валюте и единице.
                       Корзину страница тянет при открытии кода.

Все ключи пишет один прогон из одних строк базы: разойтись им не с чего.

ЧТО ЗДЕСЬ НЕ СМЕШИВАЕТСЯ (правила карточки товара, library/crossref.py):
  · у каждого поля подписан источник — таблица ПОЛЯ ниже, её же читает страница;
  · бренд кода называют три разных источника (спецификация заказчика, КП
    поставщика, каталог) — у каждого своя графа, в заголовок идёт объединение
    без повторов, а не сумма;
  · «поставщик давал цену по коду бренда» (адрес по детали) и «реестр разведки
    числит компанию за брендом» (родовой адрес) — разные таблицы; в счётчик и в
    заголовок идёт только первое;
  · применимость по машине — «имя машины рядом с деталью», а не подтверждённая
    установка; страница говорит это словами.

Чистые функции: вход — строки базы и файлы, выход — словарь {ключ KV: объект}.
Метка времени передаётся снаружи, иначе одинаковый вход давал бы разный выход.
"""
from __future__ import annotations

import collections
import json
import re
import zlib
from pathlib import Path

from library import codes_sql, oem_kind

ROOT = Path(__file__).resolve().parents[1]

# Файлы, которые сборка читает сама. Пути — строками: по ним каталог данных
# (scripts/build_catalog.py) знает, кто читает эти наборы.
ФАЙЛ_СЛОВАРЯ = "dict/oem.json"
ФАЙЛ_ЦЕПОЧКИ = "dict/chain.json"
ФАЙЛ_АТЛАСА = "zip/data/oem_atlas.json"
ФАЙЛ_КАНАЛОВ = "gt/data/ship_channels.json"

КЛЮЧ = "brands:v1"
КЛЮЧ_СВЯЗЕЙ = "brands:links:v1"
КЛЮЧ_ПАР = "brands:pairs:v1"
# КОРЗИН КОДОВ — ШЕСТНАДЦАТЬ. На синтетике объёма живой базы (40 тыс. строк цены
# КП) запрос сопоставления дал 34 901 строку «код × поставщик × валюта ×
# единица»; шестнадцать корзин держат каждую в сотнях килобайт, то есть с
# запасом на рост в разы, и карточка кода тянет одну маленькую корзину. Число
# корзин — часть закрытого списка ключей публикатора и воркера: менять его
# вместе с ними.
КОРЗИН = 16
КЛЮЧИ_КОРЗИН = tuple(f"brands:codes:{i:02d}" for i in range(КОРЗИН))
ВСЕ_КЛЮЧИ = (КЛЮЧ, КЛЮЧ_СВЯЗЕЙ, КЛЮЧ_ПАР) + КЛЮЧИ_КОРЗИН

# Сколько машин, узлов, компаний и строк атласа показывать в карточке. Больше
# человек не читает; число показанных и общее — рядом, чтобы усечение было видно.
ПОКАЗАТЬ = 25


# ── Ключ бренда ──────────────────────────────────────────────────────────────

def _написания_записи(r) -> set[str]:
    """Ключи написаний записи словаря: имя, ключ и все написания."""
    out = set()
    for н in {r.get("name"), r.get("oem_key")} | {s.get("spelling") for s in r.get("spellings", [])}:
        k = codes_sql.ключ_написания(н or "")
        if len(k) >= 2:
            out.add(k)
    return out


def карта_словаря(словарь) -> tuple[dict[str, str], dict[str, dict], int]:
    """dict/oem.json → (ключ написания → oem_key, oem_key → запись, спорных).

    Ключ написания считается правилом запроса (codes_sql.ключ_написания), а не
    nkey словаря: запрос сводит написание к своему ключу, и перевести его в ключ
    словаря можно только тем же правилом. Написание, которое два раза попадает в
    разные ключи словаря, в карту не идёт: выбрать между ними нечем, а неверная
    склейка брендов на странице неотличима от верной.

    БРЕНД ДАЁТ ТОЛЬКО ЗАПИСЬ ВИДА «БРЕНД» (library/oem_kind.py). Записи «указание»,
    «номер» и «несколько» в карту и в записи не идут: у первых двух бренда нет,
    у «нескольких» он не один (их бренды — в разложение_словаря, а написание
    считается спорным). Написание «описания» ведёт к его бренду («Bently Nevada
    (по профилю)» → Bently Nevada). Запись без поля kind — бренд, как было.
    """
    записи = {}
    кандидаты = collections.defaultdict(set)
    несколько = set()
    for r in (словарь or {}).get("records", []):
        ключ = r.get("oem_key")
        if not ключ:
            continue
        if oem_kind.бренд_ли(r):
            записи[ключ] = r
            цели = {ключ}
        elif oem_kind.вид(r) == oem_kind.ОПИСАНИЕ:
            цели = set(r.get("brands") or [])
        else:
            if oem_kind.вид(r) == oem_kind.НЕСКОЛЬКО:
                несколько |= _написания_записи(r)
            continue
        for k in _написания_записи(r):
            кандидаты[k] |= цели
    карта = {k: next(iter(v)) for k, v in кандидаты.items() if len(v) == 1}
    return карта, записи, (sum(1 for v in кандидаты.values() if len(v) > 1)
                           + len(несколько - set(карта)))


def разложение_словаря(словарь) -> dict[str, list[str]]:
    """Ключ написания записи-не-бренда → бренды, на которые она раскладывается.

    «Заказ по спецификации» и «330180/330105 (speed probe)» — пустой список:
    бренда у части ячейки нет, и отдельным брендом она не заводится. «Epiroc,
    Normet» — оба бренда. Написание, которое есть и у записи-бренда, сюда не
    идёт: бренд сильнее пометки."""
    брендовые = set()
    for r in (словарь or {}).get("records", []):
        if r.get("oem_key") and oem_kind.бренд_ли(r):
            брендовые |= _написания_записи(r)
    out: dict[str, list[str]] = {}
    for r in (словарь or {}).get("records", []):
        if not r.get("oem_key") or oem_kind.бренд_ли(r):
            continue
        бренды = list(r.get("brands") or []) if oem_kind.вид(r) in oem_kind.С_РАЗЛОЖЕНИЕМ else []
        for k in _написания_записи(r) - брендовые:
            out.setdefault(k, [])
            for b in бренды:
                if b not in out[k]:
                    out[k].append(b)
    return out


def ключи_не_брендов(словарь) -> set[str]:
    """oem_key записей словаря, которые не бренд: реестр базы мог завести их
    брендами до пометок, и сборка снимка их брендом не показывает."""
    return {r["oem_key"] for r in (словарь or {}).get("records", [])
            if r.get("oem_key") and not oem_kind.бренд_ли(r)}


_ДЕЛЕНИЕ = re.compile(r"\s*[,;/()\[\]]\s*|\s+(?:и|или|or)\s+", re.I)


def ключи_ячейки(ячейка, карта, разложение=None) -> list[str]:
    """Ячейка изготовителя из каталожных таблиц → ключи брендов.

    Для lib_models.oem и lib_parts.oem, которые читаются здесь, а не запросом:
    деление то же, что у запроса (запятая, косая, скобки, «и/или»), ключ — тем
    же правилом, затем ключ словаря, если он есть. Отсева пометок незнания
    здесь нет: это выверенные поля каталога, а не слово поставщика. Часть,
    совпавшая с записью словаря вида «указание» или «номер», бренда не даёт,
    «несколько» — даёт свои бренды (разложение_словаря)."""
    разложение = разложение or {}
    out = []
    for часть in _ДЕЛЕНИЕ.split(str(ячейка or "")):
        k = codes_sql.ключ_написания(часть or "")
        if len(k) < 2:
            continue
        if k in карта:
            ключи = [карта[k]]
        elif k in разложение:
            ключи = разложение[k]
        else:
            ключи = [k]
        for x in ключи:
            if x not in out:
                out.append(x)
    return out


# ── Поля карточки и их источники ─────────────────────────────────────────────
# ОДНА ТАБЛИЦА НА СБОРКУ, СЧЁТЧИК И СТРАНИЦУ: подпись источника не должна
# расходиться с тем, откуда значение взято на самом деле.
ПОЛЯ = [
    ("names", "Имя и написания",
     "реестр брендов lib_brands и lib_brand_alias (8.2), без него — словарь dict/oem.json; "
     "без ключа — написание из данных"),
    ("atlas", "Владелец, прежние имена, правило номера", "zip/data/oem_atlas.json"),
    ("models", "Машины", "lib_models.oem"),
    ("use", "Применение", "lib_models.use_case; площадки — lib_fleet (число)"),
    ("units", "Узлы и агрегаты",
     "lib_models → lib_part_models → lib_parts.unit_id → lib_units; детали бренда — lib_parts.unit_id"),
    ("parts", "Детали в каталоге", "lib_parts.oem"),
    ("alts", "Взаимозаменяемость", "lib_part_alt по деталям бренда"),
    ("chain", "Кто делает узлы под брендом", "dict/chain.json (ребро «изготовитель для владельца»)"),
    ("channel", "Каналы закупки", "gt/data/ship_channels.json (одна заявка)"),
    ("demand", "Спрос", "lib_demand_live, файлы стороны «заказчик»: бренд назван спецификацией"),
    ("offers", "Предложения по кодам бренда", "lib_prices, поток «разбор КП»: код бренда с ценой поставщика"),
    ("registry", "Реестр разведки по деталям бренда",
     "lib_part_suppliers → lib_suppliers: родовой адрес, в счётчик не идёт"),
    ("card", "Бренд на карточке запроса",
     "СП-176 «Brands»: ключ бренда — по элементу через реестр (lib_brand_sp176), имя из Битрикса "
     "или реестра; атрибуция уровня карточки, с кодами не складывается"),
]
ИМЕНА_ПОЛЕЙ = [p[0] for p in ПОЛЯ]


# ── Запросы каталожной части карточки ────────────────────────────────────────
# Группировка — по тексту ячейки изготовителя, а ключ бренда ставит сборка:
# одна ячейка «SKF/FAG» даёт два бренда, и делить её надо в одном месте.
МАШИНЫ_SQL = """
select m.oem, m.id, m.name, m.family_title, m.legacy, m.use_case,
       (select count(*) from lib_fleet f where f.model_id = m.id) as площадок
  from lib_models m
 where coalesce(btrim(m.oem), '') <> ''
 order by m.oem, m.name
"""

ДЕТАЛИ_SQL = """
select p.oem,
       count(*)                          as деталей,
       count(p.unit_id)                  as с_узлом,
       count(distinct p.category)        as категорий
  from lib_parts p
 where coalesce(btrim(p.oem), '') <> ''
 group by p.oem
"""

# Узлы двумя путями, и оба подписаны: «узлы машин бренда» (бренд — владелец
# конструкции машины) и «узлы деталей бренда» (бренд — изготовитель детали).
# Строка с пустым узлом — это «узел не определён», и она нужна числом.
УЗЛЫ_SQL = """
select 'машина' as путь, mo.oem, u.id, u.name, u.crit, count(distinct p.id) as деталей
  from lib_models mo
  join lib_part_models pm on pm.model_id = mo.id
  join lib_parts p        on p.id = pm.part_id
  left join lib_units u   on u.id = p.unit_id
 where coalesce(btrim(mo.oem), '') <> ''
 group by 1, 2, 3, 4, 5
union all
select 'деталь', p.oem, u.id, u.name, u.crit, count(*)
  from lib_parts p
  left join lib_units u on u.id = p.unit_id
 where coalesce(btrim(p.oem), '') <> ''
 group by 1, 2, 3, 4, 5
"""

АНАЛОГИ_SQL = """
select p.oem, a.kind, nullif(btrim(a.alt_maker), '') as изготовитель, count(*) as связей
  from lib_parts p
  join lib_part_alt a on a.part_id = p.id
 where coalesce(btrim(p.oem), '') <> ''
 group by 1, 2, 3
"""

РЕЕСТР_SQL = """
select p.oem, s.name, s.kind, s.country, ps.source,
       count(*)                                        as деталей,
       count(*) filter (where ps.verdict is not null)  as с_проверкой
  from lib_parts p
  join lib_part_suppliers ps on ps.part_id = p.id
  join lib_suppliers s       on s.id = ps.supplier_id
 where coalesce(btrim(p.oem), '') <> ''
 group by 1, 2, 3, 4, 5
"""

КАТАЛОГ_ЗАПРОСЫ = {
    "models": МАШИНЫ_SQL,
    "parts": ДЕТАЛИ_SQL,
    "units": УЗЛЫ_SQL,
    "alts": АНАЛОГИ_SQL,
    "registry": РЕЕСТР_SQL,
}


# ── Реестр брендов в базе (этап 8.2) ─────────────────────────────────────────
# Есть реестр — ключ бренда и написания берутся из него, а не из файла: страница
# и прогоны разрешают бренд одним местом. Нет таблиц (миграция не применена) или
# они пусты — сборщик работает по словарю-файлу, как в 8.1.
ЕСТЬ_РЕЕСТР_SQL = "select to_regclass('lib_brand_map') is not null"
РЕЕСТР_БРЕНДЫ_SQL = "select brand_key, name from lib_brands order by brand_key"
РЕЕСТР_НАПИСАНИЯ_SQL = """
select brand_key, spelling, source, seen_at
  from lib_brand_alias
 where status in ('разрешено', 'проверено') and brand_key is not null
 order by brand_key, spelling, source, seen_at
"""
# Элемент справочника марок портала → ключ бренда и название элемента. Название
# нужно странице, когда Битрикс не ответил: оно лежит в реестре.
РЕЕСТР_КАРТОЧКА_SQL = """
select a.sp176_id, m.brand_key, min(a.spelling) as title
  from lib_brand_alias a
  left join lib_brand_sp176 m on m.sp176_id = a.sp176_id
 where a.source = 'СП-176' and a.sp176_id is not null
 group by a.sp176_id, m.brand_key
"""


def словарь_из_реестра(бренды, написания) -> dict:
    """Строки lib_brands и разрешённые lib_brand_alias → словарь той же формы,
    что dict/oem.json: {"records": [{oem_key, name, spellings[{spelling, where}]}]}.

    Форма одна, поэтому сборка карточек не знает, откуда словарь: карта
    написаний считается тем же правилом (карта_словаря), и написание, сведённое
    реестром к двум брендам, отсеивается так же, как спорное в файле."""
    по_ключу = {k: {"oem_key": k, "name": имя, "spellings": []} for k, имя in бренды}
    for k, написание, источник, где in написания:
        запись = по_ключу.get(k)
        if запись is None:
            continue           # бренда нет — гейт засева этого не пускает, но не падать
        запись["spellings"].append({"spelling": написание,
                                    "where": f"{источник}:{где}" if где else источник})
    for запись in по_ключу.values():
        запись["n_spellings"] = len(запись["spellings"])
    return {"records": list(по_ключу.values()), "count": len(по_ключу),
            "from": "реестр базы (lib_brands, lib_brand_alias)"}


def реестр_без_не_брендов(реестр: dict, не_бренды) -> dict:
    """Реестр базы без брендов, которые словарь-файл пометил не брендом.

    Засев до пометок видов (library/oem_kind.py) завёл брендом каждую запись
    словаря — и «Заказ по спецификации», и «Epiroc, Normet». Строки lib_brands
    остаются (пометка, а не удаление), повторный засев понижает их написания, а
    до него сборка снимка не показывает их брендом и не ведёт к ним карточку
    запроса. не_бренды — ключи_не_брендов(словарь-файл)."""
    if not реестр or not не_бренды:
        return реестр
    с = реестр["словарь"]
    записи = [r for r in с.get("records", []) if r.get("oem_key") not in не_бренды]
    return {**реестр, "словарь": {**с, "records": записи, "count": len(записи)},
            "карточка": {ид: k for ид, k in (реестр.get("карточка") or {}).items() if k not in не_бренды}}


# ── Помощники ────────────────────────────────────────────────────────────────

def _число(v):
    """Decimal из psycopg2 в JSON не сериализуется; целое остаётся целым."""
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() and abs(f) < 2 ** 53 else round(f, 4)


def _целое(v) -> int:
    return int(v or 0)


def _без_пустых(d: dict) -> dict:
    """Пустое выбрасывается; ноль и False — значения, они остаются."""
    return {k: v for k, v in d.items() if v is not None and v != "" and v != [] and v != {}}


def _дата(v):
    return v.isoformat()[:10] if v is not None and hasattr(v, "isoformat") else (v or None)


def _слова(v) -> list[str]:
    return [x for x in str(v or "").split() if x]


def корзина(код: str) -> int:
    """Номер корзины кода: CRC32 байтов UTF-8 по модулю числа корзин.

    Воркер и страница его не считают — номер лежит в указателе кодов, — но
    правило одно и детерминировано: пересборка не перекладывает коды."""
    return zlib.crc32(str(код).encode("utf-8")) % КОРЗИН


def читать_файл(путь: str):
    p = ROOT / путь
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ── Сборка ───────────────────────────────────────────────────────────────────

# Итоги для плиток сводки: метрика запроса 4 → подпись. Тексты метрик — это
# тексты запроса (codes_sql, запрос 4); тест сверяет, что каждая строка там есть,
# иначе плитка молча показала бы «нет данных».
ПЛИТКИ = [
    ("all", "всё", "кодов всего: lib_demand всех сторон, закупочные цены и каталог вместе",
     "кодов в базе всего"),
    ("customer", "спрос заказчика", "кодов спроса заказчика", "в спросе заказчика"),
    ("kp", "цены КП", "кодов с ценой КП", "с ценой из КП"),
    ("buy", "закупочные цены", "кодов с закупочной ценой (любой поток)", "с закупочной ценой"),
    ("brand", "всё", "из них с брендом (спецификация, КП или каталог)", "с брендом"),
    ("supplier", "цены КП", "кодов с ценой КП, хотя бы один поставщик из реестра",
     "с поставщиком из реестра"),
    ("customer_kp", "спрос заказчика", "кодов спроса заказчика с ценой КП",
     "спрос заказчика, закрытый ценой КП"),
    ("customer_buy", "спрос заказчика", "кодов спроса заказчика с закупочной ценой",
     "спрос заказчика с закупочной ценой"),
]


def _итоги(строки):
    по_метрике = {(r["section"], r["metric"]): _число(r["value"]) for r in строки}
    плитки = []
    for ид, раздел, метрика, подпись in ПЛИТКИ:
        плитки.append({"id": ид, "label": подпись, "value": по_метрике.get((раздел, метрика)),
                       "src": f"{раздел} · {метрика}"})
    # Проверки локали идут первыми строками запроса: 0 в них — база в локали C,
    # и ключи кодов на ней считаются иначе (CLAUDE.md, правило 21а).
    локаль = all(v == 1 for (раздел, _), v in по_метрике.items() if раздел == "проверка")
    return {"tiles": плитки, "locale_ok": локаль,
            "rows": [[r["section"], r["metric"], _число(r["value"])] for r in строки]}


def _имя_поставщика(r, имена_портала):
    """Имя группы поставщика и откуда оно.

    display_name реестра — сжатый ключ («alfapumpindustries»), поэтому первым
    идёт название карточки компании в Битриксе по её номеру: его видят люди.
    Спорная запись очереди проверки с несколькими ключами портала имени не
    получает — что это одна компания, не установлено (codes_sql, запрос 6)."""
    ключи = _слова(r.get("portal_keys"))
    for k in ключи:
        if имена_портала.get(k):
            return имена_портала[k], "Битрикс: карточка компании " + k
    имя = r.get("name")
    if имя and not str(имя).startswith("ключ ") and имя != "(имени в базе нет)":
        return имя, r.get("name_from") or "реестр"
    if r.get("supplier") == "(не указан)":
        return "Поставщик на карточке не указан", "на карточке не указан"
    if ключи:
        return "Компания портала " + ", ".join(ключи), "имени нет ни в базе, ни в Битриксе"
    return имя or str(r.get("supplier")), r.get("name_from") or "нет имени"


def собрать(коды: dict, каталог: dict | None = None, *, словарь=None, цепочка=None,
            атлас=None, каналы=None, имена_портала=None, имена_брендов=None,
            ключи_карточки=None, разложение=None, собран: str | None = None) -> dict:
    """Строки базы и файлы → {ключ KV: снимок}.

    коды     — {имя запроса codes_sql.ЗАПРОСЫ: [строки-словари]};
    каталог  — {имя из КАТАЛОГ_ЗАПРОСЫ: [кортежи]};
    имена_портала — {ключ компании Битрикса: название}; имена_брендов — {номер
    элемента СП-176: название}. Нет Битрикса — пустые словари, и страница
    покажет номер с пометкой, а не выдуманное имя.
    ключи_карточки — {номер элемента СП-176: ключ бренда} из реестра (8.2);
    без реестра ключ ищется по имени элемента, как в 8.1.
    разложение — записи-не-бренды словаря (разложение_словаря); без него — из
    переданного словаря. Словарь из реестра видов не знает, поэтому публикатор
    передаёт разложение словаря-файла.
    """
    каталог = каталог or {}
    имена_портала = имена_портала or {}
    имена_брендов = имена_брендов or {}
    ключи_карточки = ключи_карточки or {}
    карта, записи, спорных = карта_словаря(словарь)
    if разложение is None:
        разложение = разложение_словаря(словарь)

    # ── бренды из запроса 1 ─────────────────────────────────────────────────
    бренды: dict[str, dict] = {}

    def бренд(ключ, имя=None):
        b = бренды.get(ключ)
        if b is None:
            запись = записи.get(ключ)
            b = бренды[ключ] = {
                "k": ключ,
                "name": (запись or {}).get("name") or имя or ключ,
                "dict": запись is not None,
            }
            if запись is not None:
                b["spellings"] = sorted({s.get("spelling") for s in запись.get("spellings", [])
                                         if s.get("spelling")})[:ПОКАЗАТЬ]
                b["spellings_n"] = len({s.get("spelling") for s in запись.get("spellings", [])})
        elif имя and not b["dict"] and b["name"] == ключ:
            b["name"] = имя
        return b

    for r in коды.get("brands", []):
        b = бренд(r["brand_key"], r.get("brand"))
        b["codes"] = _без_пустых({
            "any": _целое(r.get("codes_any")),
            "plausible": _целое(r.get("codes_plausible")),
            "customer": _целое(r.get("codes_customer")),
            "kp_file": _целое(r.get("codes_kp_file")),
            "catalog": _целое(r.get("codes_catalog")),
            "our_docs": _целое(r.get("codes_our_docs")),
            "side_unknown": _целое(r.get("codes_side_unknown")),
            "customer_kp": _целое(r.get("codes_customer_kp_price")),
            "customer_buy": _целое(r.get("codes_customer_buy_price")),
            "rows_customer": _целое(r.get("rows_customer")),
            "rows_kp": _целое(r.get("rows_kp_price")),
            "deals": _целое(r.get("deals_customer")),
            "companies_naming": _целое(r.get("portal_companies_naming")),
        })
        b["spelled"] = _целое(r.get("spellings"))

    # ── каталожная часть карточки (8.1) ─────────────────────────────────────
    for oem, ид, имя, семья, прежнее, применение, площадок in каталог.get("models", []):
        for k in ключи_ячейки(oem, карта, разложение):
            b = бренд(k, oem)
            b.setdefault("models", []).append(_без_пустых({
                "id": ид, "name": имя, "family": семья, "legacy": прежнее,
                "use": применение, "fleet": _целое(площадок)}))
    for oem, деталей, с_узлом, категорий in каталог.get("parts", []):
        for k in ключи_ячейки(oem, карта, разложение):
            b = бренд(k, oem)
            п = b.setdefault("parts", {"n": 0, "unit": 0, "categories": 0})
            п["n"] += _целое(деталей)
            п["unit"] += _целое(с_узлом)
            п["categories"] = max(п["categories"], _целое(категорий))
    узлы = collections.defaultdict(lambda: collections.defaultdict(lambda: [None, None, 0]))
    for путь, oem, uid, uname, crit, деталей in каталог.get("units", []):
        for k in ключи_ячейки(oem, карта, разложение):
            бренд(k, oem)
            у = узлы[(k, путь)][uid]
            у[0], у[1] = uname, crit
            у[2] += _целое(деталей)
    for (k, путь), по_узлу in узлы.items():
        b = бренды[k]
        без_узла = по_узлу.get(None, [None, None, 0])[2]
        всего = sum(v[2] for v in по_узлу.values())
        список = sorted(([uid, v[0], v[1], v[2]] for uid, v in по_узлу.items() if uid),
                        key=lambda x: (-x[3], x[0]))
        b.setdefault("units", {})["machine" if путь == "машина" else "part"] = _без_пустых({
            "list": [_без_пустых({"id": u, "name": n, "crit": c, "parts": q})
                     for u, n, c, q in список[:ПОКАЗАТЬ]],
            "units": len(список),
            "parts": всего,
            # ДОЛЯ «НЕ ОПРЕДЕЛЕНО» — ЧИСЛОМ, А НЕ ПУСТОТОЙ: у 21 % позиций каталога
            # узел не проставлен, и без этого числа пустой раздел читался бы
            # как «узлов у бренда нет».
            "undefined": без_узла,
        })
    for oem, вид, изготовитель, связей in каталог.get("alts", []):
        for k in ключи_ячейки(oem, карта, разложение):
            b = бренд(k, oem)
            а = b.setdefault("alts", {"kinds": {}, "makers": {}})
            а["kinds"][вид] = а["kinds"].get(вид, 0) + _целое(связей)
            if изготовитель and вид == "номер изготовителя":
                а["makers"][изготовитель] = а["makers"].get(изготовитель, 0) + _целое(связей)
    for b in бренды.values():
        if "alts" in b:
            м = b["alts"]["makers"]
            b["alts"]["makers"] = [[n, q] for n, q in sorted(м.items(), key=lambda x: (-x[1], x[0]))][:ПОКАЗАТЬ]
            b["alts"]["makers_n"] = len(м)
    реестр = collections.defaultdict(dict)
    for oem, имя, вид, страна, источник, деталей, проверено in каталог.get("registry", []):
        for k in ключи_ячейки(oem, карта, разложение):
            бренд(k, oem)
            р = реестр[k].setdefault(имя, {"name": имя, "role": вид, "country": страна,
                                           "src": set(), "parts": 0, "checked": 0})
            р["src"].add(источник or "—")
            р["parts"] += _целое(деталей)
            р["checked"] += _целое(проверено)
    for k, по_имени in реестр.items():
        список = sorted(по_имени.values(), key=lambda x: (-x["parts"], x["name"]))
        бренды[k]["registry"] = {
            "n": len(список),
            "list": [_без_пустых({**р, "src": sorted(р["src"])}) for р in список[:ПОКАЗАТЬ]],
        }

    # ── файлы: атлас, цепочка, каналы ───────────────────────────────────────
    for m in (атлас or {}).get("makers", []):
        for k in ключи_ячейки(m.get("name"), карта, разложение)[:1]:
            if k not in бренды:
                continue           # атлас не заводит бренд: он подпись к существующему
            бренды[k]["atlas"] = _без_пустых({
                "name": m.get("name"), "country": m.get("country"), "owner": m.get("owner"),
                "former": m.get("former_names"), "active": m.get("active_lines"),
                "discontinued": m.get("discontinued"), "pn": m.get("pn_system"),
                "site": m.get("site"), "segment": m.get("segment_title"),
                "confidence": m.get("confidence")})
    по_владельцу = collections.defaultdict(list)
    for r in (цепочка or {}).get("records", []):
        if r.get("from_is_bucket"):
            continue               # «Прочие» — корзина, а не владелец конструкции
        for k in ключи_ячейки(r.get("from"), карта, разложение)[:1]:
            по_владельцу[k].append(r)
    for k, рёбра in по_владельцу.items():
        if k not in бренды:
            continue
        рёбра.sort(key=lambda r: (-(r.get("n") or 0), str(r.get("to"))))
        бренды[k]["chain"] = {
            "n": len(рёбра),
            "makers": sum(1 for r in рёбра if r.get("kind") == "maker"),
            "notes": sum(1 for r in рёбра if r.get("kind") == "routing_note"),
            "proven": sum(1 for r in рёбра if r.get("proof")),
            "list": [_без_пустых({"to": r.get("to"), "kind": r.get("kind"),
                                  "scope": (r.get("scope") or [])[:5],
                                  "proof": (r.get("proof") or [])[:3]})
                     for r in рёбра[:ПОКАЗАТЬ]],
        }
    for c in (каналы or {}).get("brands", []):
        for k in ключи_ячейки(c.get("brand"), карта, разложение)[:1]:
            if k in бренды:
                бренды[k]["channel"] = _без_пустых({
                    "state": c.get("state"), "channel": c.get("channel"),
                    "checked": c.get("checked"), "action": c.get("action")})

    # ── поставщики (запрос 2 и группы из сопоставления) ─────────────────────
    поставщики: dict[str, dict] = {}
    for r in коды.get("suppliers", []):
        имя, откуда = _имя_поставщика(r, имена_портала)
        поставщики[r["supplier"]] = _без_пустых({
            "k": r["supplier"], "name": имя, "from": откуда,
            "reg": r.get("name") if r.get("name_from") == "реестр" else None,
            "keys": _слова(r.get("portal_keys")),
            "domains": _слова(r.get("domains")),
            "codes": _целое(r.get("codes")), "rows": _целое(r.get("price_rows")),
            "cards": _целое(r.get("cards")), "files": _целое(r.get("files")),
            "cur": _слова(r.get("currencies")),
            "brands_file": _целое(r.get("brands_file")),
            "brands_card": _целое(r.get("brands_card")),
            "same_name": _целое(r.get("same_name_entities")) if _целое(r.get("same_name_entities")) > 1 else None,
        })

    def поставщик(ключ, строка):
        if ключ not in поставщики:
            имя, откуда = _имя_поставщика({"supplier": ключ, "name": строка.get("поставщик")
                                           or строка.get("supplier_name"),
                                           "name_from": строка.get("имя_откуда") or строка.get("name_from"),
                                           "portal_keys": строка.get("ключи_портала")
                                           or строка.get("portal_keys")}, имена_портала)
            поставщики[ключ] = _без_пустых({"k": ключ, "name": имя, "from": откуда,
                                            "keys": _слова(строка.get("ключи_портала")
                                                           or строка.get("portal_keys"))})
        return поставщики[ключ]

    # ── пары бренд × поставщик (запрос 7) ───────────────────────────────────
    пары = []
    по_бренду_пар = collections.Counter()
    for r in коды.get("pairs", []):
        if r.get("row_kind") != "пара":
            continue
        k, s = r["brand_key"], r["supplier"]
        бренд(k, r.get("brand"))
        поставщик(s, r)
        по_бренду_пар[k] += 1
        пары.append([k, s, _целое(r.get("codes")), _целое(r.get("codes_own_row")),
                     _целое(r.get("codes_by_customer")), _целое(r.get("codes_by_other_kp")),
                     _целое(r.get("codes_by_catalog")), _целое(r.get("codes_priced")),
                     _целое(r.get("price_rows")), r.get("codes_by_currency") or "",
                     _целое(r.get("asked_closed"))])
        b = бренды[k]
        b["priced"] = _целое(r.get("brand_codes_priced"))
        b["asked"] = _целое(r.get("brand_codes_asked"))
    for k, n in по_бренду_пар.items():
        бренды[k]["sups"] = n

    # ── коды (запрос 6): указатель и корзины ────────────────────────────────
    указатель: dict[str, dict] = {}
    корзины = [dict() for _ in range(КОРЗИН)]
    for r in коды.get("match", []):
        код = r["код_ключ"]
        s = r["поставщик_ключ"]
        поставщик(s, r)
        п = указатель.get(код)
        if п is None:
            п = указатель[код] = {"n": r.get("код_как_написан") or код, "b": set(), "s": set(),
                                  "asked": bool(r.get("спрошен_нами")), "p": корзина(код)}
            корзины[п["p"]][код] = _без_пустых({
                "n": r.get("код_как_написан") or код,
                "name": r.get("наименование"),
                "bs": r.get("бренд_из_спроса"), "bsk": _слова(r.get("бренд_из_спроса_ключ")),
                "bc": r.get("бренд_из_каталога"), "bck": _слова(r.get("бренд_из_каталога_ключ")),
                "asked": bool(r.get("спрошен_нами")),
                "deals": _целое(r.get("сделок_спроса")),
                "sides": r.get("стороны_спроса"),
                "sups": _целое(r.get("поставщиков_с_ценой_по_коду")),
            }) | {"offers": []}
        for bk in (_слова(r.get("бренд_из_строки_ключ")) + _слова(r.get("бренд_из_спроса_ключ"))
                   + _слова(r.get("бренд_из_каталога_ключ"))):
            п["b"].add(bk)
        п["s"].add(s)
        корзины[п["p"]][код]["offers"].append(_без_пустых({
            "s": s, "cur": r.get("валюта"), "unit": r.get("единица"),
            "unit_w": r.get("ед_изм_как_написано") if r.get("ед_изм_как_написано") != r.get("единица") else None,
            "min": _число(r.get("цена_мин")), "max": _число(r.get("цена_макс")),
            "med": _число(r.get("цена_медиана")),
            "rows": _целое(r.get("строк_цены")), "drop": _целое(r.get("строк_отсеяно")) or None,
            "low": _целое(r.get("строк_низкой_уверенности")) or None,
            "from_total": _целое(r.get("строк_цена_из_суммы")) or None,
            "cur_file": _целое(r.get("строк_валюта_по_файлу")) or None,
            "text": _целое(r.get("строк_из_текста_по_арифметике")) or None,
            "tot_ok": _целое(r.get("строк_сумма_сошлась")) or None,
            "tot_bad": _целое(r.get("строк_сумма_не_сошлась")) or None,
            "qty": _число(r.get("количество_в_КП")),
            "basis": r.get("базис"),
            "d1": _дата(r.get("дата_первая")), "d2": _дата(r.get("дата_последняя")),
            "dsrc": r.get("дата_откуда"),
            "cards": _целое(r.get("карточек_запроса")), "files": _целое(r.get("файлов_кп")),
            "br": r.get("бренд_из_строки"), "brk": _слова(r.get("бренд_из_строки_ключ")),
            "card": _слова(r.get("бренд_с_карточки")),
        }))
    for bk in {bk for п in указатель.values() for bk in п["b"]}:
        бренд(bk)

    # ── бренды с карточки запроса (запрос 5) ────────────────────────────────
    с_карточки = []
    for r in коды.get("card_brands", []):
        ид = str(r.get("brand_id"))
        имя = имена_брендов.get(ид)
        k = ключи_карточки.get(ид)
        if not k and имя:
            ключи = ключи_ячейки(имя, карта, разложение)
            k = ключи[0] if len(ключи) == 1 else None
        с_карточки.append(_без_пустых({
            "id": ид, "name": имя, "k": k,
            "codes": _целое(r.get("codes")), "rows": _целое(r.get("price_rows")),
            "sups": _целое(r.get("suppliers")), "cards": _целое(r.get("cards"))}))
        if k and k in бренды:
            бренды[k].setdefault("card", []).append({"id": ид, "codes": _целое(r.get("codes"))})

    # ── итог ────────────────────────────────────────────────────────────────
    список_брендов = sorted(бренды.values(), key=lambda b: (
        -(b.get("codes", {}).get("any") or 0), -(b.get("parts", {}).get("n") or 0),
        -len(b.get("models", [])), b["k"]))
    for b in список_брендов:
        if "models" in b:
            b["models_n"] = len(b["models"])
            b["fleet"] = sum(m.get("fleet", 0) for m in b["models"])
            b["models"] = b["models"][:ПОКАЗАТЬ]
    список_поставщиков = sorted(поставщики.values(),
                                key=lambda s: (-(s.get("codes") or 0), -(s.get("rows") or 0), s["k"]))
    пары.sort(key=lambda p: (-p[7], -p[2], p[0], p[1]))

    сводка = {
        "version": 1,
        **({"published_at": собран} if собран else {}),
        "fields": [{"id": i, "label": л, "src": и} for i, л, и in ПОЛЯ],
        "totals": _итоги(коды.get("totals", [])),
        "brands": список_брендов,
        "suppliers": список_поставщиков,
        "card_brands": sorted(с_карточки, key=lambda c: (-c.get("codes", 0), c["id"])),
        "dict": {"records": len(записи), "spellings_mapped": len(карта), "ambiguous": спорных,
                 "from": (словарь or {}).get("from") or ФАЙЛ_СЛОВАРЯ,
                 "card_keys": len(ключи_карточки)},
        "parts": КОРЗИН,
    }
    сводка["coverage"] = заполненность(сводка)
    # ССЫЛКИ НОМЕРАМИ, А НЕ СТРОКАМИ. Пар десятки тысяч, и ключ бренда с ключом
    # поставщика, повторённые в каждой, весили больше самих чисел: на корпусе
    # объёма синтетики живой базы связи одним ключом с именами занимали 81 %
    # предела. Номера — места в списках brands и suppliers ТОГО ЖЕ ключа KV, а не
    # сводки: ключи пишутся порознь, и ссылка в чужой список разошлась бы с ним
    # при сбое между записями.
    def номера(ключи):
        список = sorted(set(ключи))
        return список, {k: i for i, k in enumerate(список)}

    бренды_пар, нб = номера(p[0] for p in пары)
    поставщики_пар, нп = номера(p[1] for p in пары)
    пары_снимок = {
        "version": 1,
        **({"published_at": собран} if собран else {}),
        "brands": бренды_пар, "suppliers": поставщики_пар,
        # Пара — массивом, а не словарём: имён полей в каждой из десятков тысяч
        # пар было бы больше, чем чисел. Порядок полей — здесь, одним местом.
        "pair_fields": ["brand", "supplier", "codes", "own_row", "by_customer", "by_other_kp",
                        "by_catalog", "priced", "price_rows", "by_currency", "asked_closed"],
        "pairs": [[нб[p[0]], нп[p[1]], *p[2:]] for p in пары],
    }
    бренды_кодов, нбк = номера(b for п in указатель.values() for b in п["b"])
    поставщики_кодов, нпк = номера(s for п in указатель.values() for s in п["s"])
    связи = {
        "version": 1,
        **({"published_at": собран} if собран else {}),
        "brands": бренды_кодов, "suppliers": поставщики_кодов,
        "code_fields": ["code", "written", "part", "brands", "suppliers", "asked"],
        "codes": [[к, п["n"] if п["n"] != к else None, п["p"], sorted(нбк[b] for b in п["b"]),
                   sorted(нпк[s] for s in п["s"]), 1 if п["asked"] else 0]
                  for к, п in sorted(указатель.items())],
    }
    снимки = {КЛЮЧ: сводка, КЛЮЧ_СВЯЗЕЙ: связи, КЛЮЧ_ПАР: пары_снимок}
    for i, ключ in enumerate(КЛЮЧИ_КОРЗИН):
        снимки[ключ] = {"version": 1, **({"published_at": собран} if собран else {}),
                        "part": i, "codes": корзины[i]}
    return снимки


# ── Счётчик заполненности ────────────────────────────────────────────────────

def статус(доля: float) -> str:
    """Пороги этапа 8 (план, «Части этапа»): закрыто от 80 %, частично от 40 %."""
    return "закрыто" if доля >= 80 else ("частично" if доля >= 40 else "дыра")


def _заполнено(b: dict, поле: str) -> bool:
    if поле == "names":
        return bool(b.get("dict"))
    if поле == "use":
        return any(m.get("use") for m in b.get("models", [])) or bool(b.get("fleet"))
    if поле == "units":
        return any((v.get("units") or 0) > 0 for v in (b.get("units") or {}).values())
    if поле == "parts":
        return bool((b.get("parts") or {}).get("n"))
    if поле == "demand":
        return bool((b.get("codes") or {}).get("customer"))
    if поле == "offers":
        return bool(b.get("sups"))
    return bool(b.get(поле))


def заполненность(сводка: dict) -> dict:
    """По каждому полю карточки: у скольких брендов заполнено, доля и статус.

    Две вселенные, и обе печатаются. «Бренды с деталями в каталоге» — мерило
    готовности 8.1 (план: «по каждому бренду, у которого в каталоге есть
    детали»). «Все бренды» — вместе со словом поставщика и заказчика; там дыр
    больше по построению, и смешивать эти числа нельзя.
    """
    бренды = сводка.get("brands", [])
    вселенные = {
        "catalog": [b for b in бренды if (b.get("parts") or {}).get("n")],
        "all": бренды,
    }
    out = {"thresholds": {"closed": 80, "partial": 40}, "universes": {}}
    for имя, набор in вселенные.items():
        всего = len(набор)
        поля = []
        for ид, подпись, источник in ПОЛЯ:
            n = sum(1 for b in набор if _заполнено(b, ид))
            доля = round(100 * n / всего, 1) if всего else 0.0
            поля.append({"id": ид, "label": подпись, "filled": n, "total": всего,
                         "pct": доля, "status": статус(доля)})
        out["universes"][имя] = {"total": всего, "fields": поля}
    # «НЕ ОПРЕДЕЛЕНО» ЧИСЛОМ. Имя без ключа словаря, деталь без узла, поставщик
    # без имени — каждое видно отдельно, а не растворено в долях.
    деталей = sum((b.get("parts") or {}).get("n", 0) for b in бренды)
    с_узлом = sum((b.get("parts") or {}).get("unit", 0) for b in бренды)
    поставщики = сводка.get("suppliers", [])
    out["undefined"] = {
        "brands_without_dict_key": sum(1 for b in бренды if not b.get("dict")),
        "brands": len(бренды),
        "parts_without_unit": деталей - с_узлом,
        "parts": деталей,
        "suppliers_without_name": sum(1 for s in поставщики
                                      if str(s.get("from", "")).startswith("имени нет")),
        "suppliers": len(поставщики),
        "card_brands_without_name": sum(1 for c in сводка.get("card_brands", []) if not c.get("name")),
        "card_brands": len(сводка.get("card_brands", [])),
    }
    return out
