"""Коды по брендам и поставщикам: SQL сводки «код · цена · бренд · поставщик».

ОТКУДА. Запросы написаны и проверены 23.09.2026 как файлы для SQL-редактора
Supabase (генераторы gen_sql.py и gen_match.py, редакция 2, три проверки на
придуманных корпусах и на синтетике объёма живой базы). Владелец выгружать их
руками не будет: снимок собирает прогон в Actions и кладёт в KV за Cloudflare
Access (scripts/publish_brands.py). Поэтому генератор переехал в репозиторий
как код сборщика, а редакторские части сняты: деления на части (у прогона нет
предела редактора в 60 с), «топ-N» (снимку нужны все строки), пояснения «как
выгрузить CSV».

ОПРЕДЕЛЕНИЯ НЕ ИЗОБРЕТАЮТСЯ, А БЕРУТСЯ ИЗ КОДА РЕПОЗИТОРИЯ: сторона файла —
library/doc_side.py и library/doc_folder.py, мусорные написания изготовителя —
library/equipment.OEM_JUNK и base/extract_positions.BRAND_STOP, «не компания» —
scripts/build_dict.NOT_A_COMPANY, предел цены и допуск сверки суммы —
library/quotes.py. Поменяется правило там — поменяется и запрос здесь.

  · код — lib_pn_key(номер) от двух знаков; у строки цены без номера — part_id;
  · спрос — только живые строки (lib_demand_live) и только сторона «заказчик»;
  · источник бренда — по стороне файла: спецификация, КП, каталог идут в вес,
    наши документы и файлы неизвестной стороны считаются отдельно;
  · поставщик — корень цепочки слияния sup_entity, иначе ключ портала;
  · закупочная цена — любой поток, кроме нашей отпускной и розницы конкурента.

КЛЮЧ БРЕНДА — из реестра брендов в базе (этап 8.2, вид lib_brand_map), а пока
реестра нет — из dict/oem.json (этап 8.1). Запрос сводит написание к ключу
своим правилом (порт scripts/build_dict.nkey со свёрткой диакритики и
двойников), а таблица brand_map — вид реестра либо строки, собранные
library/brands.py из словаря, — переводит его в ключ бренда. Написание без ключа словаря не отбрасывается:
оно остаётся своим ключом и считается числом «имя без ключа».

В журнал ничего отсюда не печатается: это текст запросов, а не данные.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path

from library import company_names, doc_folder, doc_side, docfilter, equipment, quotes

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ep = _load("kvant_extract_positions", ROOT / "base" / "extract_positions.py")
bd = _load("kvant_build_dict", ROOT / "scripts" / "build_dict.py")


# Метка на месте строк карты ключей словаря (см. brand_pipeline и запросы()).
МЕТКА_КАРТЫ = "    -- @@карта ключей словаря@@"


def q(s: str) -> str:
    """Строковый литерал SQL."""
    return "'" + s.replace("'", "''") + "'"


def pg_regex(py: str) -> str:
    """Регулярка Python → ARE PostgreSQL. В ARE \\b — это забой, а граница слова — \\y."""
    return py.replace(r"\b", r"\y")


# ── 1. Сторона файла ─────────────────────────────────────────────────────────
def side_by_code() -> str:
    lines = []
    for code, (_folder, title) in doc_folder.ПО_КОДУ[doc_folder.СДЕЛКА].items():
        key = "".join(ch for ch in code.lower() if ch.isascii() and ch.isalnum())
        lines.append(f"                 when {q(key):24} then {q(doc_side.сторона(title))}"
                     f"  -- {title}")
    return "\n".join(lines)


def side_by_title() -> str:
    lines = []
    for title, side in doc_side.ПО_НАЗВАНИЮ.items():
        lines.append(f"                 when {q(title)} then {q(side)}")
    return "\n".join(lines)


def side_by_pattern() -> str:
    lines = []
    for pat, side in doc_side.ПО_ОБРАЗЦУ:
        lines.append(f"                 when y.title ~* {q(pg_regex(pat.pattern))}\n"
                     f"                   then {q(side)}")
    return "\n".join(lines)


# СТОРОНА ПО ПРОИСХОЖДЕНИЮ — там, где его одного хватает. Карточка запроса —
# всегда поставщик. Письма (library/mail_source.py, 24.09.2026) пишут колонку
# side сами, и запасной путь нужен им лишь на случай, если колонки в базе нет:
# письмо лида — заказчик, входящее письмо компании или контакта — поставщик.
# Письма сделки здесь нет: у него два направления, и одно происхождение сторону
# не называет.
СТОРОНА_ПО_ПРОИСХОЖДЕНИЮ = {
    "поле запроса": doc_side.ПОСТАВЩИК,
    doc_folder.ПИСЬМО_ПОСТАВЩИКА: doc_side.ПОСТАВЩИК,
    doc_folder.ПИСЬМО_ЛИДА: doc_side.ЗАКАЗЧИК,
}

# СДЕЛКИ ЗАКАЗЧИКА — файлы, у которых deal_id и есть номер сделки: поле сделки
# и письмо сделки. Письмо лида спрос даёт, но номер у него лида («L123»), и в
# счёт «сделок» он не идёт.
ПРОИСХОЖДЕНИЯ_СДЕЛКИ = ("поле сделки", doc_folder.ПИСЬМО_СДЕЛКИ)


def side_by_origin() -> str:
    return "\n".join(f"                 when {q(o)} then {q(s)}"
                     for o, s in СТОРОНА_ПО_ПРОИСХОЖДЕНИЮ.items())


def origins_sql(origins) -> str:
    return "(" + ", ".join(q(o) for o in origins) + ")"


# НЕ materialized: планировщик сам строит хеш по lib_files (32 тыс. строк), а не
# по спросу (1,2 млн). С «materialized» он хешировал большую сторону: 165 МБ
# временных файлов на одном узле (проверка производительности, 23.09.2026).
# Выражения стороны вычисляются на уровне просмотра lib_files — под внешним
# соединением они становятся PlaceHolderVar, — то есть раз на файл, а не на
# строку спроса. «offset 0» в двух местах — барьеры: to_jsonb(f) и название
# поля считаются ОДИН раз на файл, а не на каждое упоминание в CASE.
FILES_CTE = f"""\
  -- СТОРОНА ФАЙЛА — кто автор документа. Колонка lib_files.side на живой базе
  -- почти пуста (23.09.2026: 650 837 из 650 960 строк с ключом без стороны),
  -- поэтому сторона выводится запасным путём, тем же, что library/doc_side.py:
  -- колонка side → карточка запроса (всегда поставщик) → код поля сделки →
  -- точное название поля → образец названия → «неизвестно».
  -- side и field_title читаются через to_jsonb(f): если колонки в базе нет,
  -- запрос не падает, а получает NULL (инструмент чтения не должен зависеть от
  -- того, применена ли миграция).
  files as not materialized (
    select x.file_id, x.origin,
           coalesce(x.by_col, x.by_origin, x.by_code, x.by_title, x.by_pattern,
                    'неизвестно') as side,
           case when x.by_col     is not null then 'колонка side'
                when x.by_origin  is not null then 'поле карточки запроса'
                when x.by_code    is not null then 'код поля сделки'
                when x.by_title   is not null then 'название поля'
                when x.by_pattern is not null then 'образец названия'
                else 'не определена' end as side_from
      from (
        select y.file_id, y.origin, y.by_col,
               case y.origin
{side_by_origin()}
               end as by_origin,
               case regexp_replace(lower(coalesce(y.field, '')), '[^0-9a-z]', '', 'g')
{side_by_code()}
               end as by_code,
               case y.title
{side_by_title()}
               end as by_title,
               case
{side_by_pattern()}
               end as by_pattern
          from (select f.file_id, f.origin, f.field,
                       nullif(btrim(x0.j ->> 'side'), '') as by_col,
                       btrim(coalesce(x0.j ->> 'field_title', '')) as title
                  from lib_files f
                 cross join lateral (select to_jsonb(f) as j offset 0) x0
                offset 0) y
      ) x
  )"""

# ИСТОЧНИК БРЕНДА ПО СТОРОНЕ ФАЙЛА (проверка определений, 23.09.2026). Раньше
# все строки lib_demand шли источником «спрос», и в облако попадали бренды
# нашего же исходящего ТКП (там со второй строки идут аналоги), внутренних
# файлов и полей неизвестной стороны. Спрос — только заказчик
# (library/doc_side.py, СПРОС); файл поставщика — это КП, чьё бы поле его ни
# держало; «мы» и «внутренний» — наш документ.
def src_case(side: str) -> str:
    return (f"case {side} when 'заказчик'   then 'спецификация'\n"
            f"                 when 'поставщик'  then 'кп'\n"
            f"                 when 'мы'         then 'наш документ'\n"
            f"                 when 'внутренний' then 'наш документ'\n"
            f"                 else 'сторона не определена' end")


# Три источника, чьё слово о бренде идёт в вес облака.
NAMED = "('спецификация', 'кп', 'каталог')"


# ── 2. Разбор ячейки изготовителя ────────────────────────────────────────────
# «total» из base/extract_positions.BRAND_STOP НЕ берём: это настоящий бренд масел
# (TotalEnergies). Правило 7: защищать щедро, обвинять закрытым списком.
PROTECTED = {"total"}
JUNK_WORDS = sorted(
    ({" ".join(w.lower().replace("ё", "е").split()) for w in equipment.OEM_JUNK}
     | {" ".join(w.lower().replace("ё", "е").split()) for w in ep.BRAND_STOP}
     | {
         # Заголовок колонки, повторённый в теле таблицы, и родовые пометки.
         "производитель", "изготовитель", "завод изготовитель", "завод-изготовитель",
         "manufacturer", "brand", "oem", "maker", "vendor", "бренд", "марка",
         "любой", "любая", "любые", "any", "original", "oem original", "genuine",
         "эквивалент", "equivalent", "аналог", "аналоги", "не требуется",
         "по согласованию", "согласно тз", "нет информации", "tbd", "tba",
         "не указано", "не указана", "не известно", "unknown", "various",
         "other", "others", "n/a", "none", "-",
     }) - PROTECTED
)
COUNTRIES = sorted(
    {" ".join(w.lower().replace("ё", "е").split()) for w in ep.COUNTRY_WORDS}
    | {
        "рф", "кнр", "prc", "uk", "беларусь", "белоруссия", "республика беларусь",
        "российская федерация", "китайская народная республика", "казахстан",
        "украина", "узбекистан", "армения", "грузия", "молдова", "азербайджан",
        "киргизия", "кыргызстан", "таджикистан", "туркменистан", "израиль",
        "мексика", "словакия", "словения", "венгрия", "румыния", "болгария",
        "сербия", "хорватия", "португалия", "греция", "ирландия", "шотландия",
        "германия фрг", "фрг", "евросоюз", "ес", "eu", "европа", "europe", "asia",
        "германии", "китая", "россии", "италии", "japan made", "made in china",
        "made in germany", "made in italy", "made in usa", "made in japan",
        "made in russia", "germany made", "cn", "de", "us", "it", "jp", "ru",
        "south korea", "taiwan", "vietnam", "singapore", "canada", "brazil",
        "mexico", "denmark", "norway", "slovakia", "hungary", "romania",
        "portugal", "israel", "belarus", "kazakhstan", "ukraine", "uzbekistan",
        "great britain", "england", "holland", "the netherlands", "czechia",
    }
)


def anchored(py_pat: str) -> str:
    """Порт scripts/build_dict.NOT_A_COMPANY с привязкой к НАЧАЛУ СЛОВА.

    В Python-оригинале подстроки ищутся где угодно: «сток\\b» отсеивает «Восток»,
    «Исток», «Росток» как «указание к закупке» (проверка 23.09.2026). Здесь каждая
    буквенная альтернатива начинается с \\m. Если оригинал обрастёт группами, порт
    не угадывает, а падает: переносить надо глазами.
    """
    assert "(" not in py_pat and ")" not in py_pat, "NOT_A_COMPANY стал сложнее — перенести руками"
    out = []
    for alt in py_pat.split("|"):
        s = re.sub(r"\\b(?=\w)", r"\\m", alt)
        s = re.sub(r"(?<=\w)\\b", r"\\M", s)
        if s[:1].isalpha():
            s = r"\m" + s
        out.append(s)
    return "|".join(out)


NOT_A_COMPANY = (anchored(bd.NOT_A_COMPANY.pattern)
                 + r"|\m(аналог|эквивалент|equivalent|согласн|по чертеж|по образц"
                 + r"|по месту|на выбор|подбор|уточнит)"
                 + r"|\mлюб(ой|ая|ые|ого|ому)\M"
                 + r"|\m(на\s+усмотрени|по\s+тз|по\s+выбору|по\s+желани|в\s+соответствии)")
# Пометка незнания или стоп-слово ПО НАЧАЛУ значения: точное совпадение не
# ловило «Не указан производитель», «Прочие производители», «Unknown manufacturer».
UNKNOWN_RE = (r"^(не\s+(указан|известн|определен|обозначен|имеет)\w*"
              r"|нет\s+(данных|информации|марки|бренда)"
              r"|без\s+(марки|бренда|названия|производителя)"
              r"|прочи[еийх]\w*|разны[ех]|различны[ех]|unknown"
              r"|not\s+(specified|stated|indicated|known|available)"
              r"|no\s*(brand|name)|noname|imported|domestic|chinese"
              r"|импортн\w*|импорт|отечественн\w*)(\s|$)")
COUNTRY_RE = (r"^((производств[оа]|пр-во|made\s+in)(\s|$)"
              r"|(российск|китайск|европейск|германск|японск|итальянск|американск)\w*"
              r"\s+(производств|изготовлен))")
# «Производитель: Siemens», «Изготовитель - SKF», «фирма FAG» — подпись снимается
# в начале ячейки, если за ней что-то есть. Одиночное «Производитель» остаётся и
# отсеивается стоп-словом; «Производительность насосов» не задевается (\M).
PREFIX_RE = (r"^\s*((завод[\s-]*изготовитель|производитель|изготовитель|торговая\s+марка"
             r"|бренд|марка|фирм[аы])\M\s*[:\-–—]?"
             r"|(brand|manufacturer|maker|mfr|make)\M\s*[:\-–—])\s*(?=\S)")
# Деление: запятая, точка с запятой, косая, скобки; союзы «и», «или», «or».
# «SKF (Швеция)» — бренд и страна, «SKF или аналог» — бренд и указание.
SPLIT_RE = r"\s*[,;/()\[\]]\s*|\s+(и|или|or)\s+"
# Короткие имена, которые правило «не короче трёх знаков» и «без цифр» иначе
# выбросило бы. Защищать можно щедро (правило 7): ошибочная защита видна числом.
WHITELIST = ["ge", "3m"]

# ГРАНИЦА СЛОВА ЗАДАНА ЯВНО, а не «\\m…\\M». У PostgreSQL слово по \\m — любая
# буква или цифра ПО ЛОКАЛИ базы: украинская «і», знак ударения, «½» — и Python
# повторить это не может. 24.09.2026 на «іао» ключ в базе и в коде разошёлся, и
# гейт засева брендов (прогон 36038314033) отменил запись части. Явный класс
# одинаков в обоих диалектах и от локали не зависит.
LEGAL_FORMS_KEY = (r"(?<![0-9a-zа-я_])(ооо|оао|зао|пао|ао|llc|ltd|inc|gmbh|s\.p\.a|spa|co|corp|company|"
                   r"limited|holding|group|a/s|ab|bv|nv|sas|sa|plc|pte|kg|ag|oy|oyj|srl|as)(?![0-9a-zа-я_])")
LEGAL_FORMS_SHOW = (r"\m(ооо|оао|зао|пао|ао|ип|llc|ltd|inc|gmbh|s\.p\.a|spa|corp|plc|"
                    r"limited|ag|ab|a/s|bv|nv|sa|sas|co|kg|pte|oy|oyj|srl|as)\M\.?")
DIACRITICS_FROM = "äöüåáàâãéèêëíìîïóòôõúùûñçøšžčřýłæœß"
DIACRITICS_TO = "aouaaaaaeeeeiiiioooouuuncoszcrylaos"
assert len(DIACRITICS_FROM) == len(DIACRITICS_TO)
# Кириллические двойники латиницы: «SКF» с русской К — это SKF. Переводятся только
# в СМЕШАННОМ ключе (есть и латиница, и кириллица); чисто русский ключ не трогается.
HOMO_FROM, HOMO_TO = "аевкмнорстху", "aebkmhopctxy"
assert len(HOMO_FROM) == len(HOMO_TO)


def arr(words: list[str]) -> str:
    body = ",\n".join("        " + ", ".join(q(w) for w in words[i:i + 6])
                      for i in range(0, len(words), 6))
    return "array[\n" + body + "]"


# CTE, превращающие ячейку изготовителя в очищенные бренды. На входе CTE cells:
#   row_id, src, code, cell, name_key, side, origin, deal_id, rfq_company
# Разбор текста идёт ПО РАЗНЫМ ЯЧЕЙКАМ, а не по строкам: написаний в десятки раз
# меньше, чем строк, а регулярки — самая дорогая часть запроса.
# judged_cols — что judged несёт дальше: в запросах 1 и 3 все колонки ячейки, в
# запросе 4 — только нужные итогам (узкая материализация, меньше временных файлов).
def brand_pipeline(judged_cols: str = "c.*, tj.brand_key, tj.brand_name") -> str:
    return f"""\
  -- КАРТА КЛЮЧЕЙ СЛОВАРЯ: ключ написания → oem_key из dict/oem.json. Её
  -- подставляет сборщик (library/brands.py) вместо метки; без словаря карта
  -- пуста, и бренд остаётся своим ключом.
  brand_map (k, oem_key) as (
{МЕТКА_КАРТЫ}
  ),
  cell_texts as (
    select distinct cell from cells where length(code) >= 2
  ),
  -- ДЕЛЕНИЕ ЯЧЕЙКИ. «SKF/FAG» — два бренда, «Getriebebau Nord, ГЕРМАНИЯ» —
  -- бренд и страна (base/extract_positions.load_brands). A/S и S.p.A. снимаются
  -- ДО деления, иначе «/» режет «Grundfos A/S» пополам; подпись «Производитель:»
  -- в начале ячейки — тоже.
  pieces as (
    select ct.cell, lib_pn_key(ct.cell) as cell_key, btrim(p) as piece
      from cell_texts ct
     cross join lateral regexp_split_to_table(
             regexp_replace(
               regexp_replace(ct.cell, '\\m(a/s|s\\.p\\.a)\\M\\.?', ' ', 'gi'),
               {q(PREFIX_RE)}, '', 'i'),
             {q(SPLIT_RE)}) as p
     where btrim(p) <> ''
  ),
  -- СТРАНА В КОНЦЕ ЧАСТИ без разделителя: «SKF Швеция», «Siemens Россия» —
  -- последнее слово снимается, если оно страна, а до него что-то есть.
  -- Одиночная страна («Китай») не трогается и ниже отсеивается как страна.
  pieces_nc as (
    select pc.cell, pc.cell_key,
           case when l.lw = any({arr(COUNTRIES)})
                then btrim(left(pc.piece, length(pc.piece) - length(l.lw)))
                else pc.piece end as piece
      from pieces pc
     cross join lateral (
           select substring(replace(lower(pc.piece), 'ё', 'е') from '\\s(\\S+)$') as lw) l
  ),
  -- КЛЮЧ БРЕНДА — порт scripts/build_dict.nkey плюс свёртка диакритики
  -- (иначе Wärtsilä и Wartsila — два бренда) и кириллических двойников. Написания
  -- одного бренда сводятся ДО group by: сумма кодов по написаниям завысила бы
  -- бренд. Материализован: иначе CASE ниже пересчитывал бы t и ключ до восьми раз
  -- на часть ячейки.
  normed as materialized (
    select pc.cell, pc.cell_key, pc.piece,
           lib_pn_key(pc.piece) as piece_key,
           case when k.k0 ~ '[a-z]' and k.k0 ~ '[а-я]'
                then translate(k.k0, {q(HOMO_FROM)}, {q(HOMO_TO)})
                else k.k0 end as brand_key,
           nm.shown as brand_name,
           btrim(regexp_replace(translate(replace(lower(nm.shown), 'ё', 'е'),
                                          {q(DIACRITICS_FROM)},
                                          {q(DIACRITICS_TO)}),
                                '\\s+', ' ', 'g')) as t
      from pieces_nc pc
     cross join lateral (
           select left(regexp_replace(regexp_replace(
                    translate(replace(lower(pc.piece), 'ё', 'е'),
                              {q(DIACRITICS_FROM)},
                              {q(DIACRITICS_TO)}),
                    {q(LEGAL_FORMS_KEY)}, ' ', 'g'),
                  '[^0-9a-zа-я]', '', 'g'), 40) as k0 offset 0) k
     cross join lateral (
           select btrim(regexp_replace(regexp_replace(
                    regexp_replace(pc.piece, '[«»"“”„'']', '', 'g'),
                    {q(LEGAL_FORMS_SHOW)}, ' ', 'gi'),
                  '\\s+', ' ', 'g'), E' \\t.,;:()[]-_*#') as shown) nm
  ),
  -- ОТСЕВ ПО ТЕКСТУ. Порядок — от самого надёжного признака к самому грубому;
  -- каждая отсеянная часть получает ОДНУ причину, и в итогах (запрос 4) число
  -- по каждой причине видно отдельно: сумма прячет потерю (правило 0).
  -- Ключ бренда на выходе — ключ словаря, если написание в нём есть; отсев
  -- ниже судит по СВОЕМУ ключу написания (raw_key): короткое имя из белого
  -- списка не должно отсеиваться оттого, что у словаря ключ длиннее.
  text_judged as materialized (
    select n.cell, n.cell_key, n.piece, n.piece_key,
           coalesce(bm.oem_key, n.brand_key) as brand_key,
           n.brand_key as raw_key, (bm.oem_key is not null) as in_dict,
           n.brand_name, n.t,
           case
             when n.t = '' or n.brand_key = '' then 'пусто'
             when n.t ~ {q(UNKNOWN_RE)}
               then 'пометка незнания или стоп-слово'
             when n.t = any({arr(JUNK_WORDS)}) then 'пометка незнания или стоп-слово'
             when n.t ~ {q(COUNTRY_RE)}
               then 'страна'
             when n.t = any({arr(COUNTRIES)}) then 'страна'
             when n.t ~* {q(NOT_A_COMPANY)}
               then 'указание к закупке'
             when n.brand_key = any(array[{", ".join(q(w) for w in WHITELIST)}]) then null
             when length(n.brand_key) < 3 then 'короче трёх знаков'
             when n.t !~ '[a-zа-я]{{3}}' then 'нет трёх букв подряд'
             when n.t ~ '[0-9]' then 'есть цифры (модель, марка стали)'
             when array_length(regexp_split_to_array(n.t, ' '), 1) > 6
               then 'больше шести слов'
           end as text_reject
      from normed n
      left join brand_map bm on bm.k = n.brand_key
  ),
  -- ОТСЕВ ПО СТРОКЕ. Одна колонка шапки досталась и номеру, и изготовителю
  -- («Код/Артикул производителя», «OEM P/N», «Тип, марка, обозначение»):
  -- ячейка или её часть совпадает с кодом или наименованием строки. Ячейка из
  -- одних разделителей частей не даёт — она «пусто», а не пропажа (left join).
  judged as (
    select {judged_cols},
           case
             when tj.cell is null or tj.text_reject = 'пусто' then 'пусто'
             when tj.cell_key = c.code or tj.cell_key = c.name_key
               or tj.piece_key = c.code then 'столкновение колонок'
             else tj.text_reject
           end as reject
      from cells c
      left join text_judged tj on tj.cell = c.cell
     where length(c.code) >= 2 and {docfilter.sql_код_годен("c.pn", "c.code")}
  ),
  clean as (
    select * from judged where reject is null
  )"""


# ── 3. Источники ячеек ───────────────────────────────────────────────────────
# ДЕЛЕНИЕ НА ЧАСТИ. В редакторе Supabase (предел 60 с) тяжёлые запросы шли
# частями по ключу КОДА. У прогона в Actions такого предела нет, и сборщик
# ставит n = 1: условие истинно сразу, хеш не считается. Строка частей оставлена
# в тексте, чтобы запрос совпадал с проверенной редакцией число в число.
def part_ok(expr: str) -> str:
    return (f"((select n from parts) = 1 or (hashtext({expr}) & 2147483647)"
            f" % (select n from parts) = (select k from parts))")


# Ключ строки цены — part_number, а при пустом — part_id (scripts/codes_with_prices.py:
# часть потоков пишет цену без номера, но с ключом детали).
#
# ПРАВДОПОДОБНЫЙ КОД (docfilter.sql_код_годен — тело lib_pn_plausible, двойника
# docfilter.код_правдоподобен):
# марка стали, размер с единицей и стандарт кодом не являются — «SS316» склеивал
# в один код все позиции из этой стали (карточка номенклатуры, 24.09.2026). Номер,
# отвергнутый правилом, считается пустым: остаётся part_id, если он есть.
# Правило применяется при чтении, накопленные строки не переписываются.
# Судится НАПИСАНИЕ номера: стандарт с размером («DIN 471 25») — код, а по ключу
# его от голого стандарта не отличить. Поэтому ячейки брендов (cells) несут
# написание pn рядом с кодом, и отсев judged судит его.
def price_code(a: str) -> str:
    return (f"case when length(lib_pn_key({a}.part_number)) >= 2 "
            f"and {docfilter.sql_код_годен(a + '.part_number')} "
            f"then lib_pn_key({a}.part_number) else {a}.part_id end")


def oem_set(col: str) -> str:
    return f"coalesce(btrim({col}), '') <> ''"


# Спрос: только живые строки (lib_demand_live); пустое в lib_demand — это '',
# а не NULL, поэтому фильтр btrim(...) <> ''. Ключ считается ОДИН раз: внутренний
# подзапрос с «offset 0» — барьер, без него планировщик подставил бы выражение
# ключа в условие части и считал бы его дважды.
# with_sup — приписать строке поставщика её файла КП (file_sup), для запроса 3.
def demand_cells_cte(where: str, with_sup: bool = False) -> str:
    sup_join = ("\n              left join file_sup fs on fs.file_id = d.source_file"
                if with_sup else "")
    sup_col = "fs.supplier" if with_sup else "null::text"
    return f"""\
  demand_cells as materialized (
    select 'd:' || x.id as row_id,
           {src_case("x.side")} as src,
           x.code, x.cell,
           lib_pn_key(x.item_name) as name_key, x.side, x.origin, x.deal_id,
           x.rfq_company, x.pn
      from (select d.id,
                   lib_pn_key(d.part_number)     as code,
                   d.part_number                 as pn,
                   d.oem                         as cell,
                   d.item_name,
                   coalesce(f.side, 'без файла') as side,
                   f.origin,
                   d.deal_id,
                   {sup_col} as rfq_company
              from lib_demand_live d
              left join files f on f.file_id = d.source_file{sup_join}
             where {oem_set("d.oem")}
               and coalesce(btrim(d.part_number), '') <> ''
            offset 0) x
     where {where}
  )"""


KP_CELLS = f"""\
    select 'p:' || p.id, 'кп'::text, {price_code("p")}, p.oem,
           lib_pn_key(p.item_name), 'поставщик'::text,
           'поле запроса'::text, p.rfq_id, p.rfq_company, p.part_number
      from lib_prices p
     where p.feed = 'разбор КП' and {oem_set("p.oem")}
       and {part_ok(price_code("p"))}"""

CATALOG_CELLS = f"""\
    -- Каталог: код — ключ catalog_no, а не lib_parts.id (82 детали иначе
    -- выпадают, library/crossref.py СЦЕПКА).
    select 'c:' || pt.id, 'каталог'::text, lib_pn_key(pt.catalog_no), pt.oem,
           lib_pn_key(pt.name), null::text, null::text, null::text, null::text,
           pt.catalog_no
      from lib_parts pt
     where {oem_set("pt.oem")}
       and {part_ok("lib_pn_key(pt.catalog_no)")}"""


# Коды с ценой. Поток «разбор КП» — предложение поставщика в ответ на запрос;
# «закупочная цена» — любой поток, кроме нашей отпускной цены и розницы
# конкурента (scripts/codes_with_prices.py, НЕ_ЗАКУПОЧНЫЕ). У каталожных потоков
# part_number пуст, и ключ лежит в part_id — так же и у «разбор КП».
PRICE_SETS = f"""\
  kp_codes as materialized (
    select distinct z.code
      from (select {price_code("p")} as code
              from lib_prices p
             where p.feed = 'разбор КП') z
     where length(z.code) >= 2 and {part_ok("z.code")}
  ),
  buy_codes as materialized (
    select distinct z.code
      from (select {price_code("p")} as code
              from lib_prices p
             where coalesce(p.feed, '') not in ('ТКП КВАНТ (отпускная цена)',
                                                'прайсы конкурентов')) z
     where length(z.code) >= 2 and {part_ok("z.code")}
  )"""

# Поставщик строки цены. Ключ портала → sup_identifier(kind='bitrix') →
# sup_entity; объединённые сущности — к корню цепочки merged_into (рекурсивно:
# A→B→C даёт C, а не B). Отложенные до ИНН лежат только в очереди проверки.
# Запись entity_uncertain — ОДНА спорная сущность, и ключей портала у неё бывает
# несколько («домен склеил разноимённые карточки»): её имена тогда принадлежат
# разным карточкам, и раздавать весь список каждой — значит подписать карточку
# чужим именем. Поэтому имя берётся, только если ключ портала у записи один;
# review_id показывает, какие номера портала — одна спорная сущность.
# Группировка по sup_id, а не по ключу портала: один поставщик бывает под
# несколькими карточками Битрикса.
SUPPLIER_CTES = f"""\
  canon as (
    with recursive walk (id, root, depth) as (
      select id, id, 0 from sup_entity where merged_into is null
      union all
      select e.id, w.root, w.depth + 1
        from sup_entity e join walk w on e.merged_into = w.id
       where w.depth < 20
    )
    select id, root from walk
  ),
  bx as (
    select i.value_norm as portal_key, coalesce(c.root, i.sup_id) as sup_id
      from sup_identifier i
      left join canon c on c.id = i.sup_id
     where i.kind = 'bitrix' and i.status <> 'rejected'
  ),
  deferred as (
    select distinct on (substr(k, 8)) substr(k, 8) as portal_key,
           case when r.bitrix_keys = 1 then r.payload -> 'names' end as names,
           r.id as review_id
      from (select r0.*,
                   (select count(*) from jsonb_array_elements_text(
                          case when jsonb_typeof(r0.payload -> 'keys') = 'array'
                               then r0.payload -> 'keys' else '[]'::jsonb end) kk
                     where kk like 'bitrix:%') as bitrix_keys
              from sup_review r0
             where r0.kind = 'entity_uncertain' and r0.closed_at is null) r
     cross join lateral jsonb_array_elements_text(
             case when jsonb_typeof(r.payload -> 'keys') = 'array'
                  then r.payload -> 'keys' else '[]'::jsonb end) k
     where k like 'bitrix:%'
     order by substr(k, 8), r.id desc
  ),
  price_rows as materialized (
    select p.id, p.rfq_id, p.source_url, p.currency, p.oem, p.rfq_brands,
           p.rfq_company,
           {price_code("p")} as code,
           p.part_number,
           lib_pn_key(p.item_name) as name_key,
           b.sup_id,
           case when p.rfq_company is null        then 'поставщик не указан'
                when b.sup_id is not null         then 'реестр'
                when dd.portal_key is not null    then 'отложен до ИНН'
                else 'нет в реестре' end          as resolved,
           coalesce(b.sup_id, 'bitrix:' || p.rfq_company) as supplier,
           dd.names                                        as deferred_names,
           dd.review_id
      from lib_prices p
      left join bx b        on b.portal_key  = p.rfq_company
      left join deferred dd on dd.portal_key = p.rfq_company
     where p.feed = 'разбор КП'
  )"""

# Файл КП → поставщик: чей это файл, если у файла есть строки цены с компанией.
# Файл, висящий на нескольких карточках, приписан строке цены с меньшим id.
FILE_SUP = """\
  file_sup as materialized (
    select distinct on (source_url) source_url as file_id, supplier
      from price_rows
     where rfq_company is not null and source_url is not null
     order by source_url, id
  )"""

# Ключи брендов карточки. Пустышки от «11,» и «12,,13» отсекаются; при длине от
# 200 знаков последний ключ может быть обрубком (обрезка в price_store) — снимается.
CARD_KEYS = """unnest(string_to_array(
             case when length({col}) >= 200
                  then regexp_replace({col}, ',[^,]*$', '')
                  else {col} end, ','))"""

# work_mem 16MB, а не больше: при 64MB узлы хеш-агрегации держали память до
# конца запроса, пик процесса — 0,6 ГБ (на вычислителе Micro это 1 ГБ ОЗУ всего),
# а время то же. JIT на таких запросах — 3 с компиляции без выигрыша.

# Настройки сеанса: выполняются отдельным оператором в той же транзакции, что и
# запрос. work_mem 16MB, а не больше: при 64MB узлы хеш-агрегации держали память
# до конца запроса (пик 0,6 ГБ), а время то же. JIT на таких запросах — 3 с
# компиляции без выигрыша. statement_timeout здесь НЕ ставится: он задаётся в
# строке подключения (CLAUDE.md, правило 9), иначе откатывается вместе с
# транзакцией.
SETTINGS = "set local work_mem = '16MB';\nset local jit = off;"


def header_block(n: int, title: str, fname: str, body: str, parts: bool = False) -> str:
    """Пояснение к запросу — комментарием в самом тексте запроса.

    Номер и имя файла остались от редакторской версии: по ним запрос сверяется
    с проверками 23.09.2026. Настройки сеанса и указания по частям сняты."""
    return f"-- ЗАПРОС {n}. {title}\n--\n" + body.rstrip() + "\n"


def topn_tail(inner: str, n_top: int, order: str = "z.rank") -> str:
    """Снимку нужны ВСЕ строки: «топ-N» редактора снят, порядок оставлен."""
    return f"select * from (\n{inner}\n) z\n order by {order};\n"


Q1 = header_block(1, "Бренды: сколько разных кодов у каждого бренда и сколько из них с ценой",
                  "q1_brands.csv", """\
-- Бренд кода называют три источника, и это РАЗНЫЕ утверждения
-- (docs/suppliers/DATA_SOURCE_MAP.md): спецификация заказчика (строки
-- lib_demand_live из файлов стороны «заказчик»), КП поставщика (lib_prices.oem
-- потока «разбор КП» и строки lib_demand из файлов стороны «поставщик»), каталог
-- (lib_parts.oem). У каждого своя колонка; codes_any — их объединение без
-- повторов, по нему строится облако.
-- НЕ входят в облако: бренды из НАШИХ документов (исходящий ТКП — там со второй
-- строки аналоги, внутренние файлы) и из файлов неизвестной стороны. Они
-- посчитаны отдельно (codes_our_docs, codes_side_unknown), чтобы потеря была
-- видна числом.
-- Цена: codes_customer_kp_price — коды спецификации заказчика с ценой КП, это
-- честная доля закрытия. «Код с брендом из КП и ценой КП» не выводится: у него
-- цена есть по построению (ловушка 5 из scripts/codes_with_prices.py).
-- Код = lib_pn_key(номер) длиной от 2 знаков; «правдоподобный» — есть цифра и
-- длина 4–25 (scripts/codes_with_prices.py). Это оценка, а не отсев.
-- Время: один проход по спросу плюс разбор брендов, около 2 × T запроса 0
-- (часть при n = 4 — около 1 × T).""",
                  parts=True) + f"""\
with
  parts as (select 1 as n, 0 as k),
{FILES_CTE},
{demand_cells_cte(part_ok("x.code"))},
  cells as (
    select * from demand_cells
    union all
{KP_CELLS}
    union all
{CATALOG_CELLS}
  ),
{brand_pipeline()},
{PRICE_SETS},
  agg as (
    select c.brand_key,
           coalesce(mode() within group (order by c.brand_name)
                      filter (where c.src in {NAMED}),
                    mode() within group (order by c.brand_name))              as brand,
           count(distinct c.brand_name) filter (where c.src in {NAMED})      as spellings,
           count(distinct c.code) filter (where c.src in {NAMED})            as codes_any,
           count(distinct c.code) filter (where c.src in {NAMED}
                                          and c.code ~ '[0-9]'
                                          and length(c.code) between 4 and 25) as codes_plausible,
           count(distinct c.code) filter (where c.src = 'спецификация')        as codes_customer,
           count(distinct c.code) filter (where c.src = 'кп')                  as codes_kp_file,
           count(distinct c.code) filter (where c.src = 'каталог')             as codes_catalog,
           count(distinct c.code) filter (where c.src = 'наш документ')        as codes_our_docs,
           count(distinct c.code) filter (where c.src = 'сторона не определена')
                                                                               as codes_side_unknown,
           count(distinct c.code) filter (where c.src in ('спецификация', 'каталог')
                                          and kp.code is not null)             as codes_named_not_by_kp_kp_price,
           count(distinct c.code) filter (where c.src = 'спецификация'
                                          and kp.code is not null)             as codes_customer_kp_price,
           count(distinct c.code) filter (where c.src = 'спецификация'
                                          and bc.code is not null)             as codes_customer_buy_price,
           count(distinct c.row_id) filter (where c.src = 'спецификация')      as rows_customer,
           count(distinct c.row_id) filter (where c.row_id like 'p:%')         as rows_kp_price,
           count(distinct c.deal_id) filter (where c.src = 'спецификация'
                                             and c.origin in {origins_sql(ПРОИСХОЖДЕНИЯ_СДЕЛКИ)})
                                                                               as deals_customer,
           count(distinct c.rfq_company) filter (where c.row_id like 'p:%')    as portal_companies_naming
      from clean c
      left join kp_codes  kp on kp.code = c.code
      left join buy_codes bc on bc.code = c.code
     group by c.brand_key
  )
""" + topn_tail("""\
select row_number() over (order by a.codes_any desc, a.brand_key) as rank,
       count(*) over ()                                          as brands_total,
       count(*) filter (where a.codes_any >= 2) over ()          as brands_2plus,
       (select n from parts) as parts_n, (select k from parts) as part_k,
       a.*
  from agg a
 where a.codes_any > 0 or (select n from parts) > 1""", 300)

Q2 = header_block(2, "Поставщики: сколько разных кодов каждый прокотировал (цена из его КП)",
                  "q2_suppliers.csv", """\
-- Поставщик строки цены (поток «разбор КП») — компания Битрикса с карточки
-- запроса (rfq_company); имя — через реестр sup_identifier/sup_entity.
-- display_name реестра — сжатый ключ («alfapumpindustries»), поэтому рядом
-- домены и ключи портала: по ним сборщик PDF подставит название из выгрузки
-- Битрикса «Компании» (ID, Название). Без неё облако поставщиков строится
-- только из имён реестра, а «компания портала N» в облако не ставится.
-- Разные сущности реестра с одинаковым именем различаются в name_unique.
-- Бренды «в его КП» — из строк цены И из строк спроса его файла КП (старые КП
-- писали изготовителя только в спрос).
-- Строки без поставщика на карточке в облако не идут — их число в запросе 4.
-- Время: lib_prices (~35 тыс. строк), реестр и строки спроса файлов КП —
-- около 1 × T запроса 0 (проход по спросу за строками файлов КП).""",
                  parts=False) + f"""\
with
{SUPPLIER_CTES},
{FILE_SUP},
  cells as (
    select 'p:' || pr.id as row_id, 'кп'::text as src, pr.code, pr.oem as cell,
           pr.name_key, 'поставщик'::text as side, 'поле запроса'::text as origin,
           pr.rfq_id as deal_id, pr.supplier as rfq_company, pr.part_number as pn
      from price_rows pr
     where {oem_set("pr.oem")} and pr.rfq_company is not null
    union all
    select 'd:' || d.id, 'кп'::text, lib_pn_key(d.part_number), d.oem,
           lib_pn_key(d.item_name), 'поставщик'::text, 'поле запроса'::text,
           d.deal_id, fs.supplier, d.part_number
      from lib_demand_live d
      join file_sup fs on fs.file_id = d.source_file
     where {oem_set("d.oem")} and coalesce(btrim(d.part_number), '') <> ''
  ),
{brand_pipeline()},
  -- Бренды из файла КП у поставщика, по числу кодов — подпись «что он
  -- котирует». rfq_company в cells здесь несёт группу поставщика.
  sup_brand as (
    select rfq_company as supplier, brand_key,
           mode() within group (order by brand_name) as brand,
           count(distinct code) as codes
      from clean group by 1, 2
  ),
  sup_brand_top as (
    select supplier, count(*) as brands_file,
           string_agg(brand || ' (' || codes || ')', '; ' order by codes desc, brand_key)
             filter (where rn <= 5) as brands_file_top
      from (select sb.*, row_number() over (partition by supplier
                                            order by codes desc, brand_key) as rn
              from sup_brand sb) z
     group by supplier
  ),
  -- Ключи брендов с карточки запроса (СП-176 «Brands»): имён в базе нет.
  card_brands as (
    select pr.supplier, btrim(b) as brand_id, count(distinct pr.code) as codes
      from price_rows pr
     cross join lateral {CARD_KEYS.format(col="pr.rfq_brands")} b
     where pr.rfq_company is not null and btrim(b) ~ '^[0-9]+$'
     group by 1, 2
  ),
  card_brands_top as (
    select supplier, count(*) as brands_card,
           string_agg(brand_id || ' (' || codes || ')', '; ' order by codes desc, brand_id)
             filter (where rn <= 5) as brands_card_top
      from (select cb.*, row_number() over (partition by supplier
                                            order by codes desc, brand_id) as rn
              from card_brands cb) z
     group by supplier
  ),
  agg as (
    select pr.supplier,
           max(pr.sup_id)                                                   as sup_id,
           max(pr.resolved)                                                 as resolved,
           max(pr.deferred_names::text)                                     as deferred_names,
           max(pr.review_id)                                                as review_id,
           count(distinct pr.code) filter (where length(pr.code) >= 2)      as codes,
           count(distinct pr.code) filter (where pr.code ~ '[0-9]'
                                           and length(pr.code) between 4 and 25) as codes_plausible,
           count(*)                                                         as price_rows,
           count(distinct pr.rfq_id)                                        as cards,
           count(distinct pr.source_url)                                    as files,
           count(distinct pr.rfq_company)                                   as portal_keys_n,
           string_agg(distinct pr.rfq_company, ' ')                         as portal_keys,
           string_agg(distinct pr.currency, ' ')                            as currencies
      from price_rows pr
     where pr.rfq_company is not null
     group by pr.supplier
  ),
  ident as (
    select sup_id,
           string_agg(value, ' ' order by value) filter (where kind = 'domain') as domains,
           count(*) filter (where kind = 'domain')                             as domains_n,
           string_agg(value, ' ' order by value) filter (where kind = 'legal')  as legal_forms
      from sup_identifier
     where status <> 'rejected' and kind in ('domain', 'legal')
     group by sup_id
  ),
  -- Одинаковый display_name у РАЗНЫХ сущностей: «ООО Ромашка» и «АО Ромашка»
  -- разведены сведением намеренно; в облаке их нельзя слить по имени.
  same_name as (
    select display_name, count(*) as n
      from sup_entity where resolution <> 'merged' group by 1
  )
select row_number() over (order by a.codes desc, a.price_rows desc, a.supplier) as rank,
       count(*) over ()                                                    as suppliers_total,
       a.supplier,
       coalesce(nm.name, e.display_name, a.deferred_names, '(имени в базе нет)') as name,
       case when coalesce(sn.n, 1) > 1
            then e.display_name || ' · ' || coalesce(i.legal_forms, a.supplier)
            else e.display_name end                                        as name_unique,
       a.resolved                                                          as name_from,
       a.review_id,
       i.domains, coalesce(i.domains_n, 0)                                 as domains_n,
       i.legal_forms,
       coalesce(sn.n, 1)                                                   as same_name_entities,
       e.status, e.resolution,
       a.codes, a.codes_plausible, a.price_rows, a.cards, a.files,
       coalesce(sbt.brands_file, 0)  as brands_file,  sbt.brands_file_top,
       coalesce(cbt.brands_card, 0)  as brands_card,  cbt.brands_card_top,
       a.portal_keys_n, a.portal_keys, a.currencies
  from agg a
  left join sup_entity e        on e.id = a.sup_id
  left join sup_name_shown nm   on nm.sup_id = e.id
  left join ident i             on i.sup_id = a.sup_id
  left join same_name sn        on sn.display_name = e.display_name
  left join sup_brand_top sbt   on sbt.supplier = a.supplier
  left join card_brands_top cbt on cbt.supplier = a.supplier
 order by rank
;
"""

# Метрики итогов: (порядок, раздел, метрика, значение, способ сложения частей).
# «сумма» — коды и строки, поделённые по ключу кода, складываются; «одинаково» —
# не зависит от части (файлы, строки цен); «не менее» — число разных брендов по
# частям не складывается, берётся наибольшее.
Q4 = header_block(4, "Итоги для подписи отчёта: всего кодов, из них с брендом и поставщиком, что отсеяно",
                  "q4_totals.csv", """\
-- Таблица «раздел · показатель · значение». Нужна, чтобы PDF писал «показано N
-- из M», а не только топ, и чтобы потеря была видна числом (правило 0).
-- Облако в сумме БОЛЬШЕ итога: один код бывает у нескольких брендов и
-- поставщиков, поэтому итог берётся отсюда, а не суммой весов облака.
-- Первые две строки — проверки локали базы: lower() должен складывать кириллицу
-- (CLAUDE.md, правило 21а); если там 0, сборщик PDF откажется строить отчёт.
-- Время: полный проход по спросу с номером плюс разбор брендов, около 4 × T
-- запроса 0 (часть при n = 4 — около 2,3 × T); это самый тяжёлый запрос.""", parts=True) + f"""\
with
  parts as (select 1 as n, 0 as k),
{FILES_CTE},
  -- Один проход по спросу: ключ считается один раз (барьер offset 0); номер
  -- строки, ячейка изготовителя и ключ наименования несутся только там, где
  -- изготовитель назван; номер файла — только у файлов карточки запроса.
  demand as materialized (
    select x.* from (
      select case when {oem_set("d.oem")} then d.id end               as row_id,
             lib_pn_key(d.part_number)                                as code,
             coalesce(f.side, 'без файла')                            as side,
             coalesce(f.side_from = 'колонка side', false)            as side_by_col,
             f.origin,
             case when f.origin = 'поле запроса' then d.source_file end as kp_file,
             case when {oem_set("d.oem")} then d.oem end              as cell,
             case when {oem_set("d.oem")} then lib_pn_key(d.item_name) end as name_key,
             d.part_number                                            as pn
        from lib_demand_live d
        left join files f on f.file_id = d.source_file
       where coalesce(btrim(d.part_number), '') <> ''
      offset 0) x
     where length(x.code) >= 2 and {docfilter.sql_код_годен("x.pn", "x.code")} and {part_ok("x.code")}
  ),
  demand_codes as materialized (
    select code, side, origin, side_by_col, count(*) as rows_n
      from demand group by 1, 2, 3, 4
  ),
  live_kp as materialized (
    select distinct kp_file, code from demand where kp_file is not null
  ),
  cells as (
    select 'd:' || row_id as row_id, {src_case("side")} as src,
           code, cell, name_key, side, origin, null::text as deal_id,
           null::text as rfq_company, pn
      from demand where cell is not null
    union all
{KP_CELLS}
    union all
{CATALOG_CELLS}
  ),
{brand_pipeline("c.src, c.code, tj.brand_key")},
{PRICE_SETS},
{SUPPLIER_CTES},
  catalog_codes as materialized (
    select distinct z.code
      from (select lib_pn_key(catalog_no) as code from lib_parts) z
     where length(z.code) >= 2 and {docfilter.sql_код_годен("z.code")} and {part_ok("z.code")}
  ),
  -- Коды, чей бренд назван одним из трёх источников облака.
  brand_codes as materialized (
    select distinct code from clean where src in {NAMED}
  ),
  spec_brand_codes as materialized (
    select distinct code from clean where src = 'спецификация'
  ),
  kp_brand_codes as materialized (
    select distinct code from clean where src = 'кп'
  ),
  customer_codes as materialized (
    select distinct code from demand_codes where side = 'заказчик'
  ),
  deal_codes as materialized (
    select distinct code from demand_codes where origin = 'поле сделки'
  ),
  all_codes as materialized (
    select code from demand_codes
    union select code from buy_codes
    union select code from catalog_codes
  ),
  kp_rows_part as materialized (
    select * from price_rows where length(code) >= 2 and {part_ok("code")}
  ),
  res as (
    select 0 as ord, 'проверка'::text as section,
           'lower(''ШАЙБА'') = ''шайба'' — локаль складывает кириллицу' as metric,
           (lower('ШАЙБА') = 'шайба')::int::bigint as value, 'одинаково'::text as merge
    union all
    select 0, 'проверка', 'lib_pn_key(''ВЫДУМ-101'') = ''выдум101''',
           (lib_pn_key('ВЫДУМ-101') = 'выдум101')::int, 'одинаково'
    union all
    select 0, 'проверка', 'правдоподобный код: марка SS316 не код, ВЫДУМ-101 — код',
           (not {docfilter.sql_код_годен("'SS316'")}
            and {docfilter.sql_код_годен("'ВЫДУМ-101'")})::int,
           'одинаково'
    union all
    select 0, 'проверка', 'стандарт с размером: DIN 471 25 — код, DIN 933 — не код',
           ({docfilter.sql_код_годен("'DIN 471 25'")}
            and not {docfilter.sql_код_годен("'DIN 933'")})::int,
           'одинаково'
    union all
    select 1, 'строки lib_demand (все стороны)',
           'строк lib_demand с кодом, сторона документа: ' || side,
           sum(rows_n)::bigint, 'сумма'
      from demand_codes group by side
    union all
    select 2, 'строки lib_demand (все стороны)',
           'кодов lib_demand, сторона документа: ' || side, count(distinct code), 'сумма'
      from demand_codes group by side
    union all
    select 3, 'строки lib_demand (все стороны)', 'кодов lib_demand, все стороны документов',
           count(distinct code), 'сумма'
      from demand_codes
    union all
    select 3, 'строки lib_demand (все стороны)',
           'кодов в файлах полей сделки, все стороны документов', count(*), 'сумма'
      from deal_codes
    union all
    select 3, 'строки lib_demand (все стороны)',
           'кодов в файлах полей сделки с брендом (спецификация, КП или каталог)', count(*),
           'сумма'
      from deal_codes c join brand_codes b using (code)
    union all
    select 4, 'спрос заказчика', 'кодов спроса заказчика', count(*), 'сумма'
      from customer_codes
    union all
    select 4, 'спрос заказчика',
           'кодов спроса заказчика только по колонке lib_files.side (определение codes_with_prices.py)',
           count(distinct code), 'сумма'
      from demand_codes where side = 'заказчик' and side_by_col
    union all
    select 5, 'спрос заказчика', 'кодов спроса заказчика с брендом из самой спецификации',
           count(*), 'сумма' from spec_brand_codes
    union all
    select 6, 'спрос заказчика',
           'кодов спроса заказчика, бренд которых назван не заказчиком (подстановка из КП или каталога)',
           count(*), 'сумма'
      from customer_codes c
      join brand_codes b using (code)
      left join spec_brand_codes s using (code)
     where s.code is null
    union all
    select 7, 'спрос заказчика', 'кодов спроса заказчика с ценой КП', count(*), 'сумма'
      from customer_codes c join kp_codes k using (code)
    union all
    select 8, 'спрос заказчика', 'кодов спроса заказчика с закупочной ценой', count(*), 'сумма'
      from customer_codes c join buy_codes k using (code)
    union all
    -- Откуда взята сторона файла (правило 16: сохраняй, почему получилось)
    select 11, 'сторона файла',
           'файлов: ' || coalesce(origin, '(без происхождения)') || ' · ' || side
             || ' · ' || side_from,
           count(*), 'одинаково'
      from files group by origin, side, side_from
    union all
    select 12, 'бренд', 'строк с непустым изготовителем, источник: ' || src,
           count(distinct row_id), 'сумма' from cells group by src
    union all
    select 13, 'бренд', 'частей ячейки, источник: ' || src || ' · ' || coalesce(reject, 'годно'),
           count(*), 'сумма' from judged group by src, reject
    union all
    select 14, 'бренд', 'кодов, потерявших бренд целиком из-за отсева, источник: ' || src,
           count(*), 'сумма'
      from (select src, code from judged group by src, code
             having bool_and(reject is not null)) z
     group by src
    union all
    select 15, 'бренд', 'брендов (ключей) всего: спецификация, КП, каталог',
           count(distinct brand_key), 'не менее'
      from clean where src in {NAMED}
    union all
    select 16, 'бренд',
           'кодов с брендом ТОЛЬКО из наших документов или файлов неизвестной стороны (в облако не входят)',
           count(*), 'сумма'
      from (select distinct code from clean where src not in {NAMED}) z
      left join brand_codes b using (code)
     where b.code is null
    union all
    select 17, 'бренд', 'брендов по источнику: ' || src, count(distinct brand_key), 'не менее'
      from clean group by src
    union all
    select 20, 'цены КП', 'строк цены «разбор КП»', count(*), 'одинаково' from price_rows
    union all
    select 21, 'цены КП', 'строк цены, поставщик: ' || resolved, count(*), 'одинаково'
      from price_rows group by resolved
    union all
    select 22, 'цены КП', 'кодов с ценой КП', count(*), 'сумма' from kp_codes
    union all
    select 23, 'цены КП', 'кодов с ценой КП, поставщик: ' || resolved, count(distinct code),
           'сумма'
      from kp_rows_part group by resolved
    union all
    select 24, 'цены КП', 'кодов с ценой КП, хотя бы один поставщик из реестра',
           count(distinct code), 'сумма' from kp_rows_part where resolved = 'реестр'
    union all
    select 25, 'цены КП', 'поставщиков (групп), поставщик: ' || resolved,
           count(distinct supplier), 'одинаково'
      from price_rows where rfq_company is not null group by resolved
    union all
    select 26, 'цены КП', 'ключей портала (компаний Битрикса) с ценой',
           count(distinct rfq_company), 'одинаково' from price_rows
    union all
    select 27, 'цены КП', 'кодов с ценой КП с брендом из файла КП (строка цены или спроса)',
           count(*), 'сумма'
      from kp_codes c join kp_brand_codes b using (code)
    union all
    select 28, 'цены КП', 'кодов с ценой КП с брендом (спецификация, КП или каталог)',
           count(*), 'сумма'
      from kp_codes c join brand_codes b using (code)
    union all
    select 29, 'цены КП', 'кодов с ценой КП с ключом бренда на карточке',
           count(distinct code), 'сумма'
      from kp_rows_part where coalesce(rfq_brands, '') ~ '[0-9]'
    union all
    select 30, 'цены КП', 'строк цены с непустым изготовителем из файла', count(*),
           'одинаково' from price_rows where {oem_set("oem")}
    union all
    select 31, 'цены КП', 'строк цены с ключами брендов карточки', count(*), 'одинаково'
      from price_rows where coalesce(rfq_brands, '') ~ '[0-9]'
    union all
    select 32, 'цены КП', 'строк цены с rfq_brands длиной от 200 (последний ключ снят)',
           count(*), 'одинаково' from price_rows where length(rfq_brands) >= 200
    union all
    select 33, 'цены КП', 'ключей брендов карточки (СП-176) всего', count(distinct btrim(b)),
           'одинаково'
      from price_rows
     cross join lateral {CARD_KEYS.format(col="rfq_brands")} b
     where btrim(b) ~ '^[0-9]+$'
    union all
    -- Живые строки соблюдены для спроса, а у цен связи с lib_row_junk нет.
    -- Сначала измерить (правило 3): строка цены, чья строка спроса в том же файле
    -- помечена мусором, продолжает давать «код с ценой КП».
    select 34, 'цены КП', 'строк цены КП без живой строки спроса того же файла и кода',
           count(*), 'сумма'
      from kp_rows_part pr
      left join live_kp l on l.kp_file = pr.source_url and l.code = pr.code
     where l.code is null
    union all
    select 40, 'закупочные цены', 'кодов с закупочной ценой (любой поток)', count(*), 'сумма'
      from buy_codes
    union all
    select 41, 'закупочные цены',
           'кодов с закупочной ценой с брендом (спецификация, КП или каталог)',
           count(*), 'сумма'
      from buy_codes c join brand_codes b using (code)
    union all
    select 42, 'каталог', 'кодов каталога', count(*), 'сумма' from catalog_codes
    union all
    select 43, 'каталог', 'кодов каталога с брендом каталога', count(distinct code), 'сумма'
      from clean where src = 'каталог'
    union all
    select 50, 'всё', 'кодов всего: lib_demand всех сторон, закупочные цены и каталог вместе',
           count(*), 'сумма'
      from all_codes
    union all
    select 51, 'всё', 'из них с брендом (спецификация, КП или каталог)', count(*), 'сумма'
      from all_codes c join brand_codes b using (code)
    union all
    select 52, 'всё',
           'кодов с брендом (спецификация, КП или каталог), правдоподобных (цифра, 4–25 знаков)',
           count(*), 'сумма'
      from brand_codes where code ~ '[0-9]' and length(code) between 4 and 25
  )
select section, metric, value, merge,
       count(*) over () as metrics_total,
       (select n from parts) as parts_n, (select k from parts) as part_k
  from res
 order by ord, metric
;
"""

Q5 = header_block(5, "(по желанию) Бренды С КАРТОЧКИ ЗАПРОСА — ключи смарт-процесса 176 «Brands»",
                  "q5_card_brands.csv", """\
-- Самый полный источник бренда у цен КП (заполнен у ~94 % строк), но в базе
-- только номера, имён нет. Облако по нему строится, если приложить выгрузку
-- Битрикса: смарт-процесс «Brands» → экспорт (ID, Название). Без неё сборщик
-- покажет только таблицу номеров. Это атрибуция уровня КАРТОЧКИ: все строки
-- КП получают все бренды карточки, поэтому с брендами запроса 1 не
-- складывается. У распознанных сканов ключей нет (ocr.py их не ставит).
-- Лёгкий: только lib_prices и реестр, секунды.""", parts=False) + f"""\
with
{SUPPLIER_CTES},
  keys as (
    select pr.*, btrim(b) as brand_id
      from price_rows pr
     cross join lateral {CARD_KEYS.format(col="pr.rfq_brands")} b
     where btrim(b) ~ '^[0-9]+$'
  ),
  agg as (
    select brand_id,
           count(distinct code) filter (where length(code) >= 2)               as codes,
           count(distinct code) filter (where code ~ '[0-9]'
                                        and length(code) between 4 and 25)    as codes_plausible,
           count(distinct id)                                                  as price_rows,
           count(distinct supplier) filter (where rfq_company is not null)     as suppliers,
           count(distinct supplier) filter (where resolved = 'реестр')         as suppliers_named,
           count(distinct rfq_id)                                              as cards
      from keys
     group by brand_id
  )
select row_number() over (order by codes desc, brand_id) as rank,
       count(*) over () as keys_total,
       a.*
  from agg a
 order by rank
;
"""


# ── СОПОСТАВЛЕНИЕ (запросы 6 и 7) ─
# «Сосед» в пояснениях ниже — запросы 1–5 выше: сопоставление писалось вторым
# генератором поверх первого и берёт его куски, а не копирует их.────────────────────────────────────────────
def sub(text: str, old: str, new: str, label: str, count: int = 1) -> str:
    """Точная замена с проверкой числа вхождений: правка не ложится мимо молча."""
    n = text.count(old)
    if n != count:
        raise RuntimeError(f"{label}: найдено {n} вхождений, ожидалось {count} — сверить")
    return text.replace(old, new)


# ── ЦЕНА ─────────────────────────────────────────────────────────────────────
# Контракт колонки (library/quotes.py): lib_prices.total — numeric(18,4), число
# от 10¹⁴ в неё не влезает. Это НЕ проверка правдоподобия: разборщик такие строки
# не пишет вовсе, и отсев здесь — страховка от чужих потоков, а не суждение о цене.
PRICE_LIMIT = int(quotes.ПРЕДЕЛ_ЦЕНЫ)
# Допуск сверки «цена × количество = сумма» — тот же, что у разборщика.
TOL_MIN, TOL_UNIT = quotes.ДОПУСК_МИН, quotes.ДОПУСК_НА_ЕДИНИЦУ
# Предел количества — тот же, что у разборщика (indexer.количество_ячейки).
QTY_MAX = int(quotes.МАКС_КОЛИЧЕСТВО)
# Оговорки разборщика, по которым видно, откуда цена. Если формулировка в
# quotes.py изменится, сборка упадёт здесь, а не выдаст запрос, считающий ноль.
_QSRC = inspect.getsource(quotes)
NOTE_FROM_TOTAL = "делением суммы"
NOTE_CUR_FILE = "валюта взята по файлу"
NOTE_TEXT = "опознана в тексте"
NOTE_QTY_BAD = quotes.КОЛ_НЕ_СОШЛОСЬ
for _p in (NOTE_FROM_TOTAL, NOTE_CUR_FILE, NOTE_TEXT, NOTE_QTY_BAD):
    if _p not in _QSRC:
        raise RuntimeError(f"в library/quotes.py нет оговорки «{_p}» — сверить признаки строки цены")

# ЕДИНИЦА — часть ключа строки, как валюта: цена за метр и за бухту сводятся не
# больше, чем рубли с евро. Написания одной единицы сводятся; пустая — отдельно.
UNIT_SHT = ("шт", "штук", "штука", "штуки", "pcs", "pc", "pce", "ea", "each", "piece",
            "pieces", "ед", "единица", "единиц", "nos")
UNIT_SET = ("компл", "комплект", "комплекта", "кт", "set", "sets", "kit")
UNIT_M = ("м", "метр", "метра", "метров", "m", "mtr")
NO_UNIT = "(не указана)"


def _in(words: tuple[str, ...]) -> str:
    return ", ".join(q(w) for w in words)


BRAND_PIPELINE = brand_pipeline()

# Частей одна: предела редактора у прогона нет (см. part_ok).
Q6_PARTS = 1

# Сторона файла — запасным путём gen_sql.FILES_CTE. Запросу 6 нужны только файлы
# строк спроса с кодами этой части: подставляем отбор вместо таблицы. Запрос 7
# берёт FILES_CTE как есть — как запросы 1 и 4 соседа.
FILES_ASKED = sub(FILES_CTE, "from lib_files f\n",
                  "from (select * from lib_files\n"
                  "                         where file_id in (select source_file from ask_rows)) f\n",
                  "gen_sql.FILES_CTE (запрос 6)")

# Строка цены с тем, что нужно сопоставлению. Общая для запросов 6 и 7.
# Группа поставщика — price_rows.supplier соседа (sup_id корня слияния, иначе
# ключ портала); строка без поставщика на карточке — отдельной группой
# «(не указан)», а не выброшена: цена есть, и её потеря была бы не видна
# (правило 0). Группа «lib:N» — старый справочник по supplier_id; поток
# «разбор КП» supplier_id не пишет (library/price_store.py), и на живой базе она
# не срабатывает. Запрос 3 соседа строк без компании не берёт вовсе.
PR_ALL = f"""\
  pr_all as materialized (
    select r.id, r.code, r.rfq_id, r.rfq_company, r.sup_id, r.resolved, r.oem,
           r.rfq_brands, r.source_url, r.review_id,
           coalesce(r.supplier, 'lib:' || p.supplier_id, '(не указан)')     as sup_group,
           coalesce(nullif(upper(btrim(r.currency)), ''), '(не названа)')  as cur,
           case when u.k is null then {q(NO_UNIT)}
                when u.k in ({_in(UNIT_SHT)}) then 'шт'
                when u.k in ({_in(UNIT_SET)}) then 'компл'
                when u.k in ({_in(UNIT_M)}) then 'м'
                else u.k end                                               as unit,
           p.part_number, p.item_name, p.price,
           -- КОЛИЧЕСТВО, КОТОРОЕ НЕ ЧИТАЕТСЯ, В МОДУ/МИН/МАКС НЕ ИДЁТ: больше
           -- quotes.МАКС_КОЛИЧЕСТВО или «количество × цена ≠ сумма» (склейка
           -- ячеек до 24.09.2026 — «3 163 518 182,316»). Строка не выбрасывается:
           -- цена её остаётся, сверка суммы ниже считается по записанному числу.
           case when p.qty > 0 and p.qty <= {QTY_MAX}
                     and not coalesce(p.price > 0 and u.total > 0
                                      and abs(p.price * p.qty - u.total)
                                          > greatest({TOL_MIN}, p.qty * {TOL_UNIT}), false)
                then p.qty end                                             as qty,
           p.qty_unit, p.basis,
           -- У потока «разбор КП» price_date не пишется, а created_at ставится
           -- заново при каждом переразборе (price_store снимает строки файла и
           -- пишет снова): это день записи в базу, а не дата КП.
           coalesce(p.price_date, p.created_at::date)                      as d,
           (p.price_date is not null)                                      as d_is_price_date,
           p.supplier_id,
           coalesce(p.price > 0 and p.price < {PRICE_LIMIT}, false)          as price_ok,
           -- ПРИЗНАКИ СТРОКИ ЦЕНЫ — что о ней знает разборщик (library/quotes.py).
           (p.confidence = 'low')                                          as conf_low,
           coalesce(p.note like '%{NOTE_FROM_TOTAL}%', false)                as from_total,
           coalesce(p.note like '%{NOTE_CUR_FILE}%', false)          as cur_by_file,
           coalesce(p.note like '%{NOTE_TEXT}%', false)              as text_arith,
           -- Сверка «цена × количество = сумма» с допуском разборщика. Цена,
           -- выведенная из суммы делением, сходится с ней по построению — её
           -- сверка ничего не доказывает, и в счёт она не идёт (правило 1).
           -- total читается через to_jsonb: без миграции колонки запрос не падает.
           -- Количество, снятое разбором за несходство (quotes.КОЛ_НЕ_СОШЛОСЬ),
           -- — та же несошедшаяся сумма, только записанная без количества.
           case when coalesce(p.note like '%{NOTE_QTY_BAD}%', false) then false
                when p.price > 0 and p.qty > 0 and u.total > 0
                     and not coalesce(p.note like '%{NOTE_FROM_TOTAL}%', false)
                then abs(p.price * p.qty - u.total)
                     <= greatest({TOL_MIN}, p.qty * {TOL_UNIT}) end               as total_ok,
           case when jsonb_typeof(r.deferred_names) = 'array' then
             (select string_agg(x, ' / ')
                from jsonb_array_elements_text(r.deferred_names) x) end    as deferred_txt
      from price_rows r
      join lib_prices p on p.id = r.id
     cross join lateral (
           select nullif(regexp_replace(lower(btrim(p.qty_unit)), '[^a-zа-я0-9]', '', 'g'),
                         '') as k,
                  (to_jsonb(p) ->> 'total')::numeric as total) u
     where length(r.code) >= 2
  )"""

# Сведения о поставщике — по всем его строкам цены, а не по строке выгрузки:
# иначе одна и та же компания получала бы в разных строках разные имена и ключи.
SUP_INFO = """\
  sup_info as (
    select sup_group, max(sup_id) as sup_id, max(supplier_id) as supplier_id,
           max(deferred_txt) as deferred_txt, max(review_id) as review_id,
           string_agg(distinct rfq_company, ' ' order by rfq_company) as portal_keys
      from pr_all group by 1
  )"""

# Ключи портала спорной записи очереди проверки: сосед не сводит их в одну
# группу (запись entity_uncertain — сомнение, а не доказательство одной
# компании) и имени не выдаёт, если ключей больше одного. Здесь они видны рядом:
# «эти номера портала — одна спорная запись».
REVIEW_KEYS = """\
  review_keys as (
    select r.id as review_id,
           string_agg(distinct substr(k, 8), ' ' order by substr(k, 8)) as keys,
           count(distinct k)                                              as keys_n
      from sup_review r
     cross join lateral jsonb_array_elements_text(
             case when jsonb_typeof(r.payload -> 'keys') = 'array'
                  then r.payload -> 'keys' else '[]'::jsonb end) k
     where r.id in (select review_id from sup_info) and k like 'bitrix:%'
     group by r.id
  )"""

# Имя поставщика группы: реестр → очередь проверки (отложен до ИНН, имя — только
# при одном ключе портала, как у соседа) → старый справочник lib_suppliers по
# supplier_id → ключ портала с явной пометкой. У сущности без имён
# load_supplier_master.показать() пишет в display_name ключ («bitrix:2002»): это
# не имя, и за имя из реестра он не выдаётся.
# Первым идёт общий выбор имени sup_name_shown (suppliers_schema.sql, блок 8а):
# карточка Битрикса, реквизиты, написание, домен. Вида нет — на его месте пустая
# выборка (запросы(имена=False)), и всё работает по-старому.
KEYLIKE = r"e.display_name ~ '^[a-z_]+:\S+$'"
SUP_NAME = f"""\
coalesce(nm.name, case when not {KEYLIKE} then e.display_name end,
                {{a}}.deferred_txt, ls.name,
                case when {{a}}.sup_group = '(не указан)' then '(поставщик на карточке не указан)'
                     else 'ключ ' || coalesce({{a}}.portal_keys, '?') || ' (имени в базе нет)' end)"""
SUP_FROM = f"""\
case when nm.name is not null then 'реестр: ' || nm.name_source
            when not {KEYLIKE} then 'реестр'
            when {{a}}.deferred_txt is not null then 'отложен до ИНН: имя из очереди проверки'
            when {{a}}.review_id is not null and {{a}}.sup_id is null
              then 'отложен до ИНН: у спорной записи несколько ключей портала, имя не выдаётся'
            when ls.name is not null then 'справочник lib_suppliers'
            when {{a}}.sup_group = '(не указан)' then 'на карточке не указан'
            when e.id is not null then 'реестр без имени: показан ключ портала'
            else 'нет в реестре: показан ключ портала' end"""

# Ячейки строк цены. «Чей это КП» (rfq_company ячейки) — группа поставщика,
# если компания на карточке есть; у строки без компании — пусто, как в запросе 3
# соседа (там такая строка — «КП без поставщика», чужое слово для всех).
KP_ROW_CELLS = """\
    select 'p:' || pr.id as row_id, 'кп'::text as src, pr.code, pr.oem as cell,
           lib_pn_key(pr.item_name) as name_key, 'поставщик'::text as side,
           'поле запроса'::text as origin, pr.rfq_id as deal_id,
           case when pr.rfq_company is not null then pr.sup_group end as rfq_company,
           pr.part_number as pn
      from pr
     where {oem}"""


Q6 = header_block(6, "Сопоставление по коду и поставщику: код → цена → бренд → поставщик",
                  f"q6_match_p0.csv … q6_match_p{Q6_PARTS - 1}.csv", f"""\
-- Одна строка на четвёрку (код, поставщик, валюта, единица). Цена — только
-- закупочная, из предложения поставщика (lib_prices, поток «разбор КП»), за
-- единицу, как лежит в lib_prices.price. Цены разных валют и разных единиц не
-- сводятся: у каждой своя строка (цена за метр и за бухту сравнимы не больше,
-- чем рубли с евро). Единица — qty_unit строки, написания одной единицы сведены
-- (шт = pcs = ea …); пустая — «(не указана)», отдельной строкой.
--
-- ЦЕНА «ЗАПИСАННАЯ», А НЕ «ПРАВДОПОДОБНАЯ». В мин/макс/медиану идёт цена
-- 0 < цена < 10¹⁴ — это контракт колонки (library/quotes.py), и разборщик иного
-- не пишет, так что строк_отсеяно на живой базе около нуля. Правдоподобие цены
-- этот отсев НЕ проверяет. Что о строке известно, показано числом строк:
-- строк_низкой_уверенности (confidence 'low' у разборщика), строк_цена_из_суммы
-- (цена выведена делением суммы на количество), строк_валюта_по_файлу (валюта не
-- из строки, а одна на весь файл), строк_из_текста_по_арифметике (строка PDF без
-- таблицы, принятая по равенству кол-во × цена = сумма), строк_сумма_сошлась /
-- _не_сошлась (цена × количество против суммы строки с допуском разборщика; цена
-- из суммы в эту сверку не идёт — она сходится по построению).
--
-- КОД — ключ строки цены, как в запросах 2–3 (lib_pn_key номера от 2 знаков,
-- при пустом номере — part_id); код_как_написан — самое частое написание в КП.
-- БРЕНД — четыре разных утверждения, поэтому четыре графы, источник — по
-- стороне файла, как в запросе 1: бренд_из_строки — назван в КП ЭТОГО
-- поставщика (строка цены или строка спроса его файла КП — «своё слово», как
-- codes_own_row запроса 3); бренд_из_спроса — в спецификации ЗАКАЗЧИКА (файл
-- стороны «заказчик»; КП поставщика и наше ТКП, приложенные к сделке, сюда не
-- идут); бренд_из_каталога — lib_parts; бренд_с_карточки — номера из поля
-- «Brands» карточки запроса (имён в базе нет; сборщик PDF подпишет их по
-- bitrix_brands.csv). У граф *_ключ — ключ бренда запроса 1; подписи и ключи
-- идут в одном порядке — по ключу.
-- ПОСТАВЩИК — группа, как в запросах 2–3; имя_откуда говорит, откуда имя;
-- «ключ N (имени в базе нет)» — компания портала без имени в базе. Спорная
-- запись очереди проверки с несколькими ключами портала (запись_очереди,
-- ключи_записи_очереди) — отдельные группы без имени, как у соседа: что это одна
-- компания, не установлено.
-- СПРОШЕН_НАМИ — код есть в живом спросе ЗАКАЗЧИКА: строка файла стороны
-- «заказчик» (scripts/codes_with_prices.py: «заявка заказчика — это и есть наш
-- спрос»; codes_customer запроса 1). сделок_спроса — сделок с этим кодом в
-- заявках заказчика. стороны_спроса — стороны файлов полей сделки и файлов
-- заказчика, где код есть: код, который поставщик дописал в своё КП к сделке,
-- или наш аналог в нашем ТКП, спросом заказчика не считается.
-- ДАТА — у потока «разбор КП» дата цены не пишется: это день записи разбора в
-- базу, и переразбор ставит новую. дата_откуда: «запись разбора …», «дата цены»
-- или «смешано».
-- Время: цены (~35 тыс. строк) плюс проход по спросу только по кодам с ценой.""") + f"""\
with
  parts as (select {Q6_PARTS} as n, 0 as k),
{SUPPLIER_CTES},
{FILE_SUP},
{PR_ALL},
  pr as materialized (
    select * from pr_all where {part_ok("code")}
  ),
  part_codes as materialized (select distinct code from pr),
  code_text as (
    select code,
           mode() within group (order by btrim(part_number))              as code_written,
           left(mode() within group (order by btrim(item_name)), 120)     as item
      from pr group by code
  ),
  -- Живые строки спроса по кодам части, из файлов любой стороны: сторона
  -- решается ниже. Ключ считается один раз (барьер offset 0), отбор кодов —
  -- независимым подзапросом (правило 8).
  ask_rows as materialized (
    select x.* from (
      select d.id, lib_pn_key(d.part_number) as code, d.deal_id, d.source_file,
             d.oem, d.item_name, d.part_number
        from lib_demand_live d
       where coalesce(btrim(d.part_number), '') <> ''
      offset 0) x
     where x.code in (select code from part_codes)
  ),
{FILES_ASKED},
  ask_sided as materialized (
    select a.*, coalesce(f.side, 'без файла') as side, f.origin, fs.supplier as file_sup
      from ask_rows a
      left join files f     on f.file_id = a.source_file
      left join file_sup fs on fs.file_id = a.source_file
  ),
  -- СПРОС ЗАКАЗЧИКА — сторона «заказчик». Прочие стороны — только перечнем, и
  -- только у файлов полей сделки: КП поставщика на карточке запроса есть почти у
  -- каждого кода с ценой, и перечень утонул бы в нём.
  asked as (
    select code,
           count(distinct deal_id) filter (where side = 'заказчик')      as deals,
           coalesce(bool_or(side = 'заказчик'), false)                   as by_customer,
           string_agg(distinct side, ' ' order by side)
             filter (where side = 'заказчик' or origin = 'поле сделки')  as sides
      from ask_sided
     group by code
  ),
  cells as (
{KP_ROW_CELLS.format(oem=oem_set("pr.oem"))}
    union all
    select 'd:' || a.id, {src_case("a.side")}, a.code, a.oem, lib_pn_key(a.item_name),
           a.side, a.origin, a.deal_id, a.file_sup, a.part_number
      from ask_sided a
     where {oem_set("a.oem")}
    union all
    select 'c:' || pt.id, 'каталог'::text, lib_pn_key(pt.catalog_no), pt.oem,
           lib_pn_key(pt.name), null::text, null::text, null::text, null::text,
           pt.catalog_no
      from lib_parts pt
     where {oem_set("pt.oem")}
       and lib_pn_key(pt.catalog_no) in (select code from part_codes)
  ),
{BRAND_PIPELINE},
  brand_names as (
    select brand_key,
           coalesce(mode() within group (order by brand_name) filter (where src in {NAMED}),
                    mode() within group (order by brand_name))            as brand
      from clean group by brand_key
  ),
  -- Подписи и ключи — в ОДНОМ порядке (по ключу): иначе графы бренд_* и
  -- бренд_*_ключ не совпадают по местам, и подпись достаётся чужому ключу.
  -- «Своё слово» поставщика — по коду и поставщику: строки цены его группы и
  -- строки спроса его файла КП (file_sup), как codes_own_row запроса 3; у строки
  -- цены без компании своё слово — слово этой строки.
  row_brand as (
    select code, sup,
           string_agg(brand, '; ' order by brand_key)                   as brands,
           string_agg(brand_key, ' ' order by brand_key)                as brand_keys
      from (select distinct c.code, coalesce(c.rfq_company, pr.sup_group) as sup,
                   c.brand_key, bn.brand
              from clean c
              join brand_names bn on bn.brand_key = c.brand_key
              left join pr on c.row_id = 'p:' || pr.id
             where c.src = 'кп' and (c.rfq_company is not null or pr.id is not null)) z
     group by 1, 2
  ),
  -- Бренд «из спроса» — только из спецификации заказчика: слово поставщика в
  -- его КП к сделке и наше ТКП — другие утверждения.
  code_brand_src as (
    select code, src,
           string_agg(brand, '; ' order by brand_key)                   as brands,
           string_agg(brand_key, ' ' order by brand_key)                as brand_keys
      from (select distinct c.code, c.src, c.brand_key, bn.brand
              from clean c
              join brand_names bn on bn.brand_key = c.brand_key
             where c.src in ('спецификация', 'каталог')) z
     group by 1, 2
  ),
  card_ids as (
    select pr.code, pr.sup_group, pr.cur, pr.unit,
           string_agg(distinct btrim(b), ' ' order by btrim(b)) as ids
      from pr
     cross join lateral {CARD_KEYS.format(col="pr.rfq_brands")} b
     where btrim(b) ~ '^[0-9]+$'
     group by 1, 2, 3, 4
  ),
  grp as (
    select code, sup_group, cur, unit,
           min(price) filter (where price_ok)                            as price_min,
           max(price) filter (where price_ok)                            as price_max,
           percentile_cont(0.5) within group (order by price)
             filter (where price_ok)                                     as price_med,
           count(*) filter (where price_ok)                              as price_rows,
           count(*) filter (where not price_ok)                          as rows_dropped,
           count(*) filter (where price_ok and conf_low)                 as rows_low,
           count(*) filter (where price_ok and from_total)               as rows_from_total,
           count(*) filter (where price_ok and cur_by_file)              as rows_cur_by_file,
           count(*) filter (where price_ok and text_arith)               as rows_text_arith,
           count(*) filter (where price_ok and total_ok)                 as rows_total_ok,
           count(*) filter (where price_ok and not total_ok)             as rows_total_bad,
           mode() within group (order by qty)
             filter (where price_ok and qty > 0)                         as qty_mode,
           min(qty) filter (where price_ok and qty > 0)                  as qty_min,
           max(qty) filter (where price_ok and qty > 0)                  as qty_max,
           mode() within group (order by nullif(btrim(qty_unit), ''))    as unit_written,
           string_agg(distinct nullif(upper(btrim(basis)), ''), ' '
                      order by nullif(upper(btrim(basis)), ''))          as basis,
           min(d)                                                        as d_first,
           max(d)                                                        as d_last,
           count(*) filter (where d_is_price_date)                       as d_price_n,
           count(*)                                                      as rows_all,
           count(distinct rfq_id)                                        as cards,
           count(distinct source_url)                                    as files
      from pr
     group by 1, 2, 3, 4
  ),
{SUP_INFO},
{REVIEW_KEYS},
  code_sup as (
    select code,
           count(distinct sup_group) filter (where price_rows > 0
                                               and sup_group <> '(не указан)') as n
      from grp group by 1
  )
select (select n from parts)                                             as parts_n,
       (select k from parts)                                             as part_k,
       (select count(*) from (select distinct code, sup_group, cur, unit
                                from pr_all) z)                          as пар_всего,
       count(*) over ()                                                  as пар_в_части,
       g.code                                                            as код_ключ,
       ct.code_written                                                   as код_как_написан,
       ct.item                                                           as наименование,
       rb.brands                                                         as бренд_из_строки,
       rb.brand_keys                                                     as бренд_из_строки_ключ,
       ci.ids                                                            as бренд_с_карточки,
       sb.brands                                                         as бренд_из_спроса,
       sb.brand_keys                                                     as бренд_из_спроса_ключ,
       kb.brands                                                         as бренд_из_каталога,
       kb.brand_keys                                                     as бренд_из_каталога_ключ,
       {SUP_NAME.format(a="si")} as поставщик,
       {SUP_FROM.format(a="si")} as имя_откуда,
       g.sup_group                                                       as поставщик_ключ,
       si.portal_keys                                                    as ключи_портала,
       si.review_id                                                      as запись_очереди,
       rk.keys                                                           as ключи_записи_очереди,
       g.cur                                                             as валюта,
       g.unit                                                            as единица,
       g.unit_written                                                    as ед_изм_как_написано,
       round(g.price_min, 4)                                             as цена_мин,
       round(g.price_max, 4)                                             as цена_макс,
       round(g.price_med::numeric, 4)                                    as цена_медиана,
       g.price_rows                                                      as строк_цены,
       g.rows_dropped                                                    as строк_отсеяно,
       g.rows_low                                                        as строк_низкой_уверенности,
       g.rows_from_total                                                 as строк_цена_из_суммы,
       g.rows_cur_by_file                                                as строк_валюта_по_файлу,
       g.rows_text_arith                                                 as строк_из_текста_по_арифметике,
       g.rows_total_ok                                                   as строк_сумма_сошлась,
       g.rows_total_bad                                                  as строк_сумма_не_сошлась,
       g.qty_mode                                                        as "количество_в_КП",
       g.qty_min                                                         as кол_во_мин,
       g.qty_max                                                         as кол_во_макс,
       g.basis                                                           as базис,
       g.d_first                                                         as дата_первая,
       g.d_last                                                          as дата_последняя,
       case when g.d_price_n = 0 then 'запись разбора (переразбор ставит новую)'
            when g.d_price_n = g.rows_all then 'дата цены'
            else 'смешано' end                                           as дата_откуда,
       g.cards                                                           as карточек_запроса,
       g.files                                                           as файлов_кп,
       coalesce(cs.n, 0)                                                 as поставщиков_с_ценой_по_коду,
       coalesce(a.by_customer, false)                                    as спрошен_нами,
       coalesce(a.deals, 0)                                              as сделок_спроса,
       a.sides                                                           as стороны_спроса,
       (select count(*) from price_rows)                                 as строк_кп_всего,
       (select count(*) from price_rows where length(code) < 2
                                           or code is null)              as строк_кп_без_кода
  from grp g
  join code_text ct            on ct.code = g.code
  left join row_brand rb       on rb.code = g.code and rb.sup = g.sup_group
  left join card_ids ci        on ci.code = g.code and ci.sup_group = g.sup_group
                              and ci.cur = g.cur and ci.unit = g.unit
  left join code_brand_src sb  on sb.code = g.code and sb.src = 'спецификация'
  left join code_brand_src kb  on kb.code = g.code and kb.src = 'каталог'
  left join code_sup cs        on cs.code = g.code
  left join asked a            on a.code = g.code
  join sup_info si             on si.sup_group = g.sup_group
  left join review_keys rk     on rk.review_id = si.review_id and rk.keys_n > 1
  left join sup_entity e       on e.id = si.sup_id
  left join sup_name_shown nm  on nm.sup_id = e.id
  left join lib_suppliers ls   on ls.id = si.supplier_id
 order by g.code, g.cur, g.unit, g.price_min nulls last, g.sup_group
;
"""


Q7 = header_block(7, "Облако-сопоставление в свёртке: бренд × поставщик с ценой и валютой",
                  "q7_brand_supplier_price.csv", """\
-- Надстройка запроса 3: пары и их графы codes (= codes_any_source), codes_own_row,
-- codes_by_customer, codes_by_other_kp, codes_by_catalog считаются ТЕМИ ЖЕ
-- определениями (бренд кода — из спецификации, КП или каталога, источник — по
-- стороне файла; «своё слово» — строка цены или строка спроса файла КП этого
-- поставщика) и для поставщиков с карточки совпадают с запросом 3 число в число.
-- Здесь есть и то, чего в запросе 3 нет: группа «(не указан)» (строки цены без
-- компании на карточке) и «lib:N»; в облако сборщик их не берёт.
-- Добавлено:
--   codes_priced       — из них кодов, где у поставщика есть записанная цена
--                        (0 < цена < 10¹⁴ — контракт колонки; правдоподобие не
--                        проверялось, признаки строк — в запросе 6);
--   price_rows         — строк записанной цены, rows_dropped — отсеянных;
--   currencies_n, codes_by_currency — валюты пары и кодов по каждой; цены
--                        разных валют не сводятся;
--   brand_codes_priced — кодов бренда с записанной ценой КП от кого угодно;
--                        share_of_brand_priced = codes_priced / он;
--   brand_codes_asked  — кодов бренда в СПРОСЕ ЗАКАЗЧИКА: код из живой строки
--                        файла стороны «заказчик» (codes_customer запроса 1),
--                        бренд назван в спецификации заказчика или в каталоге.
--                        НЕ в КП и НЕ в нашем ТКП: знаменатель не должен
--                        зависеть от ответов поставщиков (правило 1);
--                        asked_closed — из них закрыл записанной ценой этот
--                        поставщик; share_of_asked = asked_closed / brand_codes_asked.
-- Время: как у запроса 1 (полный проход по живому спросу со стороной файла).""") + f"""\
with
  parts as (select 1 as n, 0 as k),
{SUPPLIER_CTES},
{FILE_SUP},
{PR_ALL},
  pr as materialized (
    select * from pr_all where {part_ok("code")}
  ),
  kp_codes as materialized (select distinct code from pr),
{FILES_CTE},
  -- Один проход по живому спросу части (как запрос 1): ключ считается один раз.
  -- Берутся строки файлов заказчика (знаменатель спроса) и любые строки по
  -- кодам с ценой (бренд кода — как запрос 3).
  demand as materialized (
    select x.* from (
      select d.id                          as id,
             lib_pn_key(d.part_number)     as code,
             f.origin,
             coalesce(f.side, 'без файла') as side,
             d.deal_id,
             fs.supplier                   as file_sup,
             case when {oem_set("d.oem")} then d.oem end       as cell,
             case when {oem_set("d.oem")} then d.item_name end as item_name,
             case when {oem_set("d.oem")} then d.part_number end as pn
        from lib_demand_live d
        left join files f     on f.file_id = d.source_file
        left join file_sup fs on fs.file_id = d.source_file
       where coalesce(btrim(d.part_number), '') <> ''
      offset 0) x
     where length(x.code) >= 2 and {part_ok("x.code")}
       and (x.side = 'заказчик' or x.code in (select code from kp_codes))
  ),
  -- Спрос заказчика: строка файла стороны «заказчик».
  asked_codes as materialized (
    select distinct code from demand where side = 'заказчик'
  ),
  cells as (
    select 'd:' || id as row_id, {src_case("side")} as src, code, cell,
           lib_pn_key(item_name) as name_key, side, origin, deal_id,
           file_sup as rfq_company, pn
      from demand where cell is not null
    union all
{KP_ROW_CELLS.format(oem=oem_set("pr.oem"))}
    union all
    select 'c:' || pt.id, 'каталог'::text, lib_pn_key(pt.catalog_no), pt.oem,
           lib_pn_key(pt.name), null::text, null::text, null::text, null::text,
           pt.catalog_no
      from lib_parts pt
     where {oem_set("pt.oem")}
       and (lib_pn_key(pt.catalog_no) in (select code from kp_codes)
            or lib_pn_key(pt.catalog_no) in (select code from asked_codes))
  ),
{BRAND_PIPELINE},
  -- Бренды кода с ценой — как code_brand запроса 3: три источника облака.
  code_brand as (
    select distinct code, brand_key, brand_name, src,
           case when src = 'кп' then rfq_company end as named_by
      from clean
     where src in {NAMED} and code in (select code from kp_codes)
  ),
  cb as (select distinct code, brand_key from code_brand),
  quoted as (
    select sup_group as supplier, code,
           bool_or(price_ok)                        as priced,
           count(*) filter (where price_ok)         as price_rows,
           count(*) filter (where not price_ok)     as rows_dropped
      from pr group by 1, 2
  ),
  quoted_cur as (
    select distinct sup_group as supplier, code, cur from pr where price_ok
  ),
  brand_priced as (
    select brand_key, count(*) as n
      from cb where code in (select code from pr where price_ok)
     group by 1
  ),
  -- Знаменатель спроса бренда: код из спроса заказчика, бренд — из
  -- спецификации заказчика или каталога.
  ask_brand as materialized (
    select distinct brand_key, code
      from clean
     -- Не «code in (…)» верхним условием: его планировщик превращает в
     -- полусоединение, и при оценке asked_codes в одну строку выходит вложенный
     -- цикл по CTE (91 с на синтетике у проверяющего). Внутри coalesce
     -- подзапрос остаётся хешированным SubPlan и считается один раз (правило 8).
     where coalesce(code in (select code from asked_codes), false)
       and src in ('спецификация', 'каталог')
  ),
  brand_asked as (select brand_key, count(*) as n from ask_brand group by 1),
  pairs as (
    select x.brand_key, q.supplier,
           mode() within group (order by x.brand_name)                        as brand,
           count(distinct q.code)                                             as codes,
           count(distinct q.code) filter (where x.src = 'кп'
                                          and x.named_by = q.supplier)        as codes_own_row,
           count(distinct q.code) filter (where x.src = 'спецификация')       as codes_by_customer,
           count(distinct q.code) filter (where x.src = 'кп'
                                          and x.named_by is distinct from q.supplier)
                                                                              as codes_by_other_kp,
           count(distinct q.code) filter (where x.src = 'каталог')            as codes_by_catalog,
           count(distinct q.code) filter (where q.priced)                     as codes_priced
      from quoted q
      join code_brand x on x.code = q.code
     group by 1, 2
  ),
  pair_rows as (
    select cb.brand_key, q.supplier,
           sum(q.price_rows) as price_rows, sum(q.rows_dropped) as rows_dropped
      from quoted q join cb on cb.code = q.code
     group by 1, 2
  ),
  pair_cur as (
    select cb.brand_key, qc.supplier, qc.cur, count(*) as codes
      from quoted_cur qc join cb on cb.code = qc.code
     group by 1, 2, 3
  ),
  pair_cur_s as (
    select brand_key, supplier, count(*) as currencies_n,
           string_agg(cur || ' ' || codes, '; ' order by codes desc, cur) as by_currency
      from pair_cur group by 1, 2
  ),
  pair_ask as (
    select ab.brand_key, q.supplier, count(*) as n
      from ask_brand ab
      join quoted q on q.code = ab.code and q.priced
     group by 1, 2
  ),
{SUP_INFO},
  pair_ranked as (
    select row_number() over (order by p.codes_priced desc, p.codes desc,
                                       p.brand_key, p.supplier)              as rank,
           'пара'::text                                                     as row_kind,
           count(*) over ()                                                 as pairs_total,
           row_number() over (partition by p.brand_key
                              order by p.codes_priced desc, p.codes desc,
                                       p.supplier)                          as rank_in_brand,
           dense_rank() over (order by coalesce(bp.n, 0) desc, p.brand_key) as brand_rank,
           count(*) over (partition by p.brand_key)                         as brand_suppliers_n,
           sum(p.codes_priced) over (partition by p.brand_key)              as brand_pairs_priced,
           p.brand_key, p.brand, p.supplier,
           {SUP_NAME.format(a="si")} as supplier_name,
           {SUP_FROM.format(a="si")} as name_from,
           si.portal_keys, si.review_id,
           p.codes, p.codes_own_row, p.codes_by_customer, p.codes_by_other_kp,
           p.codes_by_catalog, p.codes_priced,
           coalesce(r.price_rows, 0)                                         as price_rows,
           coalesce(r.rows_dropped, 0)                                       as rows_dropped,
           coalesce(c.currencies_n, 0)                                       as currencies_n,
           c.by_currency                                                     as codes_by_currency,
           coalesce(bp.n, 0)                                                 as brand_codes_priced,
           round(p.codes_priced::numeric / nullif(bp.n, 0), 4)               as share_of_brand_priced,
           coalesce(ba.n, 0)                                                 as brand_codes_asked,
           coalesce(pa.n, 0)                                                 as asked_closed,
           round(coalesce(pa.n, 0)::numeric / nullif(ba.n, 0), 4)            as share_of_asked
      from pairs p
      join sup_info si            on si.sup_group = p.supplier
      left join pair_rows r       on r.brand_key = p.brand_key and r.supplier = p.supplier
      left join pair_cur_s c      on c.brand_key = p.brand_key and c.supplier = p.supplier
      left join pair_ask pa       on pa.brand_key = p.brand_key and pa.supplier = p.supplier
      left join brand_priced bp   on bp.brand_key = p.brand_key
      left join brand_asked ba    on ba.brand_key = p.brand_key
      left join sup_entity e      on e.id = si.sup_id
      left join sup_name_shown nm on nm.sup_id = e.id
      left join lib_suppliers ls  on ls.id = si.supplier_id
  ),
  -- ОТБОРА НЕТ: снимку нужны все пары. В редакторской версии здесь стояли
  -- 5 000 самых весомых и по 15 поставщиков у 60 крупнейших брендов — это было
  -- ограничение выгрузки CSV, а не смысла.
  pair_out as (
    select * from pair_ranked
  ),
  -- ИТОГ БРЕНДА В ЧАСТИ — только в режиме частей. Знаменатель спроса бренда
  -- складывается по частям лишь тогда, когда каждая часть отдаёт его и для
  -- брендов, у которых в этой части нет ни одной пары: код бренда без цены
  -- лежит в одной части, а коды с ценой — в другой. Без этих строк доля
  -- закрытия спроса в частях завышалась бы.
  brand_out as (
    select b.brand_key, bn.brand,
           coalesce(bp.n, 0) as brand_codes_priced,
           coalesce(ba.n, 0) as brand_codes_asked
      from (select brand_key from brand_priced
            union select brand_key from brand_asked) b
      join (select brand_key,
                   coalesce(mode() within group (order by brand_name)
                              filter (where src in {NAMED}),
                            mode() within group (order by brand_name)) as brand
              from clean group by 1) bn on bn.brand_key = b.brand_key
      left join brand_priced bp on bp.brand_key = b.brand_key
      left join brand_asked ba  on ba.brand_key = b.brand_key
     where (select n from parts) > 1
  )
select o.rank, o.row_kind, o.pairs_total, o.rank_in_brand, o.brand_suppliers_n,
       o.brand_pairs_priced,
       (select n from parts) as parts_n, (select k from parts) as part_k,
       o.brand_key, o.brand, o.supplier, o.supplier_name, o.name_from, o.portal_keys,
       o.review_id,
       o.codes, o.codes_own_row, o.codes_by_customer, o.codes_by_other_kp, o.codes_by_catalog,
       o.codes_priced, o.price_rows, o.rows_dropped, o.currencies_n, o.codes_by_currency,
       o.brand_codes_priced, o.share_of_brand_priced, o.brand_codes_asked, o.asked_closed,
       o.share_of_asked
  from pair_out o
union all
select null, 'итог бренда в части', null, null, null, null,
       (select n from parts), (select k from parts),
       b.brand_key, b.brand, '(все поставщики)', null, null, null, null,
       null, null, null, null, null, null, null, null, null, null,
       b.brand_codes_priced, null, b.brand_codes_asked, null, null
  from brand_out b
 order by row_kind desc, rank, brand_key
;
"""


# ── Для сборщика ─────────────────────────────────────────────────────────────
# Запрос 3 не берётся: запрос 7 — его надстройка теми же определениями и с
# ценой, и проверками 23.09.2026 установлено, что по графам запроса 3 они
# совпадают число в число. Запрос 0 — замер для редактора, прогону он не нужен.
ЗАПРОСЫ = {
    "brands": Q1,        # бренд: коды по трём источникам, доли с ценой
    "suppliers": Q2,     # поставщик: коды с ценой КП, бренды его КП
    "totals": Q4,        # итоги: коды всего, с брендом, с ценой, что отсеяно
    "card_brands": Q5,   # ключи брендов с карточки запроса (СП-176)
    "match": Q6,         # код × поставщик × валюта × единица: мин, макс, медиана
    "pairs": Q7,         # бренд × поставщик: коды, с ценой, доля спроса
}


def карта_sql(карта) -> str:
    """Пары (ключ написания, oem_key) → строки values для brand_map.

    Пустая карта — пустой набор той же формы: запрос обязан работать и без
    словаря, тогда бренды остаются своими ключами."""
    пары = sorted({(k, v) for k, v in карта if k and v})
    if not пары:
        return "    select null::text, null::text where false"
    return "    values\n" + ",\n".join(f"      ({q(k)}, {q(v)})" for k, v in пары)


# Карта из реестра брендов в базе (этап 8.2, library/supabase/brands_schema.sql):
# вид lib_brand_map отдаёт «ключ написания → ключ бренда» только однозначных
# написаний. Ключ написания в нём — lib_brand_key, порт ключ_написания ниже.
КАРТА_РЕЕСТРА = "    select spelling_key, brand_key from lib_brand_map"


def запросы(карта=(), из_реестра: bool = False, имена: bool = False) -> dict[str, str]:
    """Тексты запросов с подставленной картой ключей.

    из_реестра — карта берётся из базы (вид lib_brand_map), иначе — строками
    values из словаря-файла. Реестра нет — сборщик зовёт без него, и запросы
    работают по-старому.

    имена — вид sup_name_shown в базе есть (company_names.вид_имён_есть); нет —
    на его месте пустая выборка, и имя поставщика берётся по-старому."""
    вставка = КАРТА_РЕЕСТРА if из_реестра else карта_sql(карта)
    return {имя: company_names.имена_sql(sql.replace(МЕТКА_КАРТЫ, вставка), имена)
            for имя, sql in ЗАПРОСЫ.items()}


# Ключ написания бренда в Python — ТОТ ЖЕ, что считает brand_pipeline (normed):
# нижний регистр, ё → е, свёртка диакритики, снятие правовых форм по границе
# слова, только буквы и цифры, 40 знаков, латинские двойники в смешанном ключе.
# Нужен сборщику, чтобы перевести написания словаря в ключи запроса; расхождение
# с SQL ловит tests/test_brands_sql.py на одних и тех же написаниях.
# LEGAL_FORMS_KEY пишется одинаково в диалектах PostgreSQL и Python: граница
# слова — явный класс, а не \\m…\\M по локали.
_ФОРМЫ = re.compile(LEGAL_FORMS_KEY)
_ДИАКРИТИКА = str.maketrans(DIACRITICS_FROM, DIACRITICS_TO)
_ДВОЙНИКИ = str.maketrans(HOMO_FROM, HOMO_TO)


def ключ_написания(s: str) -> str:
    # «İ» (U+0130) Python складывает в «i» с отдельной точкой сверху, PostgreSQL —
    # в «i»: это единственное расхождение lower() на всём Юникоде (замер
    # 24.09.2026, 139 тыс. знаков, база C.UTF-8).
    t = str(s or "").replace("İ", "I").lower().replace("ё", "е").translate(_ДИАКРИТИКА)
    t = _ФОРМЫ.sub(" ", t)
    k = re.sub(r"[^0-9a-zа-я]", "", t)[:40]
    if re.search(r"[a-z]", k) and re.search(r"[а-я]", k):
        k = k.translate(_ДВОЙНИКИ)
    return k
