-- КАРТОЧКИ ПОРТАЛА: КОД, БРЕНД, ПОСТАВЩИК — шаг 2 плана «одна стартовая
-- страница» (25.09.2026).
--
-- ЗАЧЕМ. Владелец: «с стартовой страницы переходить от кода к бренду, от бренда
-- к другому коду, от кода к поставщику этого кода — одна большая
-- информационная база». Шаг 1 (portal_schema.sql) нашёл слово; здесь три
-- функции отвечают карточкой на то, что нашлось: portal_code(ключ кода),
-- portal_brand(ключ бренда), portal_supplier(номер KV-S). Каждая карточка
-- несёт ключи переходов (ключ кода, ключ бренда, номер поставщика, машина), и
-- страница /p ставит на каждый код, бренд и компанию ссылку на его карточку.
-- Ни одна страница, функция и таблица при этом не меняются: файл только
-- ДОБАВЛЯЕТ функции.
--
-- ПРАВИЛА НОМЕНКЛАТУРЫ (PDF владельцу 24.09.2026), как они здесь исполнены:
--   · позиция — пара «бренд + код»: у кода всегда стоит бренд, у бренда — коды;
--   · бренд с ИСТОЧНИКОМ: каталог (lib_parts.oem), каталог аналогов
--     (lib_part_alt.alt_maker, когда код — чей-то аналог), строка спецификации
--     (lib_demand.oem), карточка запроса (lib_prices.rfq_brands → СП-176 →
--     реестр), маска кода (lib_pn_patterns: шифровка с «#»), слово КП
--     (lib_prices.oem). Выбирается бренд реестра, а не слово: сначала по
--     старшинству источника (каталог → каталог аналогов → спецификация →
--     карточка → маска → КП), внутри источника — по числу строк; тот же
--     порядок, что у бренда в выдаче поиска (portal_search). Слово без бренда
--     реестра идёт, только если реестрового нет вовсе, и показывается словом;
--   · «спорно» — если источники, называющие бренд ПОЗИЦИИ (все, кроме КП),
--     разрешаются в разные бренды реестра.
--     Слово поставщика в КП — утверждение о его ПРЕДЛОЖЕНИИ, а не о позиции:
--     оно делает строку аналогом, а позицию спорной не делает. Кроме случая,
--     когда кроме КП бренд не назван никем: тогда спорно, если КП расходятся.
--     Нет реестра — спор не судится вовсе (null), а не «не спорно»;
--   · аналоги отдельно от оригинала: строка КП — аналог, если её бренд реестра
--     не тот, что у позиции, или поставщик пишет «аналог / эквивалент /
--     equivalent / replacement». Взаимозаменяемость из каталога (lib_part_alt)
--     — отдельным списком;
--   · только человеческие имена: компания — вид sup_name_shown (без него —
--     display_name, если он не похож на ключ), бренд — lib_brands.name, машина
--     и узел — их имена. Номеров справочника (СП-176, companyId портала) и
--     сжатых ключей в ответе нет: номер элемента СП-176 без бренда реестра
--     показывается написанием самого справочника, а без него не показывается;
--   · количество — только правдоподобное: portal_qty — правило
--     crossref.КОЛ_ЧИТАЕТСЯ буква в букву (сверяет тест), иначе null и
--     «не знаем» на странице, а сама строка не прячется;
--   · код, отвергнутый правилом правдоподобия (lib_pn_plausible — двойник
--     docfilter.sql_код_годен, каталог защищает), карточки не получает: ответ
--     {"rejected": true}, а в списках такой код идёт без ссылки или не идёт.
--
-- ОПОРЫ, КОТОРЫХ МОЖЕТ НЕ БЫТЬ: реестр брендов (brands_schema.sql), вид имён
-- sup_name_shown, реестр номеров sup_number_registry, проверка
-- lib_pn_plausible, факты sup_fact. Функции — на plpgsql (как portal_search):
-- запрос разбирается при исполнении, и ветка без опоры просто не исполняется;
-- наличие проверяется при ВЫЗОВЕ, а не при применении.
--
-- ВРЕМЯ. Предел времени у функции не действует (проверено в portal_schema.sql),
-- поэтому каждое чтение ограничено числом строк, а между разделами карточки
-- стоит бюджет (portal_entity.budget_ms, по умолчанию 5 с): исчерпан — раздел
-- не считается, и его имя идёт в "partial". Молчаливого усечения нет.
--
-- ЧТЕНИЕ БОЛЬШИХ ТАБЛИЦ — ТОЛЬКО ПО СТОЯЩИМ ИНДЕКСАМ. Спрос — lib_demand_pnkey
-- (код) и lib_demand_oem (бренд), КП — lib_prices_pn_key (код),
-- lib_prices_oem (бренд), lib_prices_rfqco (поставщик). Новых индексов файл не
-- строит. Бренд по спросу и КП ищется по ДОСЛОВНЫМ написаниям реестра
-- (lib_brand_alias.spelling): ключ написания lib_brand_key стоит ~60 мкс на
-- ячейку, и разрешать им все ячейки таблицы на каждый вызов — секунды (замер
-- 25.09.2026: 8 000 ячеек — 0,46 с, с делением ячейки на части — 1 с). Цена —
-- ячейка «SKF, FAG» бренду SKF не засчитывается; поэтому счёт по бренду
-- подписан на странице «не меньше».
--
-- ИДЕМПОТЕНТНО: применяется повторно без ошибок. Роли Supabase — только через
-- проверку наличия (правило 20). Применение — Actions → «ZIP base — apply DB
-- migrations» (zip-db.yml), после portal_schema.sql.

set statement_timeout = '10min';
set lock_timeout      = '10s';

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Количество, которое читается. Тело — crossref.КОЛ_ЧИТАЕТСЯ дословно, без
--    псевдонима «p.» (tests/test_portal_entity_sql.py сверяет строку): больше
--    миллиона или «количество × цена ≠ сумма» с допуском разборщика —
--    количества нет. ОДНО ВЫРАЖЕНИЕ БЕЗ FROM — И ЭТО ЗАМЕР: такую функцию
--    планировщик встраивает в запрос, а с подзапросом «from (select …) p» она
--    исполнялась отдельным вызовом на каждую строку, и карточка поставщика с
--    5 000 строками КП тратила на это больше половины времени (25.09.2026).
create or replace function portal_qty(qty numeric, price numeric, total numeric) returns numeric
  language sql immutable parallel safe as $$
    select case when qty > 0 and qty <= 1000000 and not coalesce(price > 0 and total > 0 and abs(price * qty - total) > greatest(0.5, qty * 0.005), false)
                then qty end
  $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Маска кода → регулярное выражение по КЛЮЧУ кода. Маской считается только
--    шифровка с «#» из букв, цифр и разделителей («MW#####X[/NN]»): «#» и «N» —
--    цифра, «X» — буква, [..] — необязательная часть, прочие буквы и цифры —
--    сами собой, разделители выпадают (в ключе их нет). Словесные описания
--    («числовой PN + суффикс») масками не считаются: угадывать их нельзя.
create or replace function portal_mask_re(pattern text) returns text
  language plpgsql immutable parallel safe as $fn$
declare
  выход   text := '';
  знак    text;
  глубина int := 0;
begin
  if pattern is null or strpos(pattern, '#') = 0 or pattern !~ '^[A-Za-z0-9#/\[\] .-]+$' then
    return null;
  end if;
  for i in 1 .. char_length(pattern) loop
    знак := substr(pattern, i, 1);
    if знак = '[' then
      выход := выход || '(?:';
      глубина := глубина + 1;
    elsif знак = ']' then
      if глубина = 0 then return null; end if;
      выход := выход || ')?';
      глубина := глубина - 1;
    elsif знак in ('#', 'N') then
      выход := выход || '[0-9]';
    elsif знак = 'X' then
      выход := выход || '[a-z]';
    elsif знак ~ '[A-Za-z0-9]' then
      выход := выход || lower(знак);
    end if;
  end loop;
  if глубина <> 0 or выход !~ '\[0-9\]|\[a-z\]' then
    return null;
  end if;
  return '^' || выход || '$';
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Какие из ключей — коды. lib_pn_plausible — при вызове (её может не быть);
--    ключ курируемого каталога — код всегда (docfilter.sql_код_годен: «каталог
--    защищает»). Проверка каталога — только для отвергнутых, по индексам.
create or replace function portal_codes_ok(keys text[]) returns text[]
  language plpgsql stable as $fn$
declare
  годные text[];
begin
  if to_regprocedure('lib_pn_plausible(text)') is null then
    return array(select distinct x from unnest(keys) x where coalesce(x, '') <> '');
  end if;
  execute $q$
    select coalesce(array_agg(distinct x), '{}') from unnest($1) x
     where coalesce(x, '') <> ''
       and case when lib_pn_plausible(x) then true
                else exists (select 1 from lib_parts p where p.id = x)
                  or exists (select 1 from lib_parts p where lib_pn_key(p.catalog_no) = x) end
  $q$ into годные using keys;
  return годные;
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Ячейка изготовителя → бренды реестра: {ячейка: [[ключ, имя], …]}. Сначала
--    ячейка целиком; не разрешилась — по частям тем же делением, что у
--    страницы /brands (codes_sql.SPLIT_RE): «SKF (Швеция)», «Kelton / FAG».
--    Неразрешённой ячейки в ответе нет — она остаётся словом у вызывающего.
--    Карта берётся из lib_brand_map одним чтением по списку ключей: условие на
--    ключ группировки уходит внутрь вида, к индексу lib_brand_alias_key.
create or replace function portal_brands_of(cells text[]) returns jsonb
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  ключи text[];
  карта jsonb;
  выход jsonb;
begin
  if to_regclass('lib_brand_map') is null or to_regclass('lib_brands') is null
     or to_regprocedure('lib_brand_key(text)') is null then
    return '{}';
  end if;
  -- Части — в памяти, через jsonb: временной таблицы в stable-функции и в
  -- read-only транзакции PostgREST быть не может.
  select coalesce(jsonb_agg(jsonb_build_array(ч.cell, ч.part, ч.k)), '[]')
    into карта
    from (
      select c.cell, 0 as part, lib_brand_key(c.cell) as k
        from (select distinct left(btrim(x), 200) as cell from unnest(cells) x
               where coalesce(btrim(x), '') <> '') c
      union all
      select c.cell, 1, lib_brand_key(btrim(s))
        from (select distinct left(btrim(x), 200) as cell from unnest(cells) x
               where coalesce(btrim(x), '') <> '') c
        cross join lateral regexp_split_to_table(c.cell, '\s*[,;/()\[\]]\s*|\s+(?:и|или|or)\s+') s
       where btrim(s) <> '' and btrim(s) <> c.cell
    ) ч
   where ч.k <> '';
  ключи := array(select distinct x ->> 2 from jsonb_array_elements(карта) x);
  select coalesce(jsonb_agg(jsonb_build_array(m.spelling_key, b.brand_key, b.name)), '[]')
    into выход
    from lib_brand_map m join lib_brands b on b.brand_key = m.brand_key
   where m.spelling_key = any(ключи);
  with части as (
    select x ->> 0 as cell, (x ->> 1)::int as part, x ->> 2 as k from jsonb_array_elements(карта) x
  ), бренды as (
    select y ->> 0 as k, y ->> 1 as brand_key, y ->> 2 as name from jsonb_array_elements(выход) y
  ), найдено as (
    select ч.cell, ч.part, б.brand_key, б.name from части ч join бренды б on б.k = ч.k
  ), итог as (
    -- Целиком разрешилась — части не смотрятся: «Kelton GmbH» не станет двумя.
    select н.cell, н.brand_key, н.name from найдено н where н.part = 0
    union
    select н.cell, н.brand_key, н.name from найдено н
     where н.part = 1 and н.cell not in (select cell from найдено where part = 0)
  )
  select coalesce(jsonb_object_agg(z.cell, z.brands), '{}') into выход
    from (select и.cell, jsonb_agg(jsonb_build_array(и.brand_key, и.name) order by и.name, и.brand_key) as brands
            from итог и group by и.cell) z;
  return выход;
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Имена компаний: {sup_id: {id, name, src, number}}. Имя — из общего выбора
--    sup_name_shown (suppliers_schema.sql, 8а); без вида — display_name, если он
--    не похож на ключ (то же правило sup_имя_как_ключ), иначе имени нет, и
--    страница пишет «имя не известно», а не сжатую строку. number — вечный
--    номер KV-S, только если он выдан (sup_number_registry).
create or replace function portal_sup_names(ids text[]) returns jsonb
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  выход jsonb;
  выданы text[] := '{}';
begin
  if to_regclass('sup_entity') is null or coalesce(array_length(ids, 1), 0) = 0 then
    return '{}';
  end if;
  if to_regclass('sup_number_registry') is not null then
    execute 'select coalesce(array_agg(sup_id), ''{}'') from sup_number_registry where sup_id = any($1)'
      into выданы using ids;
  end if;
  if to_regclass('sup_name_shown') is not null then
    execute $q$
      select coalesce(jsonb_object_agg(e.id, jsonb_build_object(
               'id', e.id, 'name', n.name,
               'src', case when n.name is not null then n.name_source end,
               'number', case when e.id = any($2) then e.id end)), '{}')
        from sup_entity e left join sup_name_shown n on n.sup_id = e.id
       where e.id = any($1)
    $q$ into выход using ids, выданы;
  else
    select coalesce(jsonb_object_agg(e.id, jsonb_build_object(
             'id', e.id,
             'name', case when not (e.display_name is null or btrim(e.display_name) = ''
                                    or e.display_name ~ '^[a-z_]+:\S+$'
                                    or e.display_name ~ '^[a-zа-яё0-9]+$') then e.display_name end,
             'src', case when not (e.display_name is null or btrim(e.display_name) = ''
                                   or e.display_name ~ '^[a-z_]+:\S+$'
                                   or e.display_name ~ '^[a-zа-яё0-9]+$') then 'реестр' end,
             'number', case when e.id = any(выданы) then e.id end)), '{}')
      into выход
      from sup_entity e where e.id = any(ids);
  end if;
  return выход;
end $fn$;

-- Ключ компании портала (lib_prices.rfq_company) → компания реестра. Слитая
-- ведёт на ту, в которую слита. Ключ без сущности в ответ не идёт: номер
-- компании портала человеку ничего не говорит, страница пишет «не сведена».
create or replace function portal_companies(keys text[]) returns jsonb
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  карта jsonb;
  имена jsonb;
begin
  if to_regclass('sup_entity') is null or to_regclass('sup_identifier') is null
     or coalesce(array_length(keys, 1), 0) = 0 then
    return '{}';
  end if;
  select coalesce(jsonb_object_agg(x.value_norm, x.sup_id), '{}') into карта
    from (select distinct on (i.value_norm) i.value_norm,
                 coalesce(case when e.resolution = 'merged' then e.merged_into end, e.id) as sup_id
            from sup_identifier i join sup_entity e on e.id = i.sup_id
           where i.kind = 'bitrix' and i.status <> 'rejected' and i.value_norm = any(keys)
           order by i.value_norm, (e.resolution = 'merged'), e.id) x;
  имена := portal_sup_names(array(select distinct v from jsonb_each_text(карта) t(k, v)));
  return (select coalesce(jsonb_object_agg(t.k, имена -> t.v), '{}')
            from jsonb_each_text(карта) t(k, v) where имена ? t.v);
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Строки КП по бренду: слово поставщика (дословное написание реестра, индекс
--    lib_prices_oem), карточка запроса (элемент СП-176 бренда в rfq_brands) и
--    каталог (детали бренда по lib_parts.oem → ключ кода, индекс
--    lib_prices_pn_key). Строка, найденная двумя путями, считается один раз
--    (первым путём). Путь карточки читает строки КП целиком (у rfq_brands
--    индекса нет): десятки тысяч строк, десятки миллисекунд.
create or replace function portal_brand_rows(b text, lim int default 20000)
returns table (id bigint, code text, written text, rfq_company text, month text, via text)
  language plpgsql stable set plan_cache_mode = force_custom_plan set jit = off as $fn$
#variable_conflict use_column
declare
  предел    int := least(greatest(coalesce(lim, 20000), 1), 50000);
  написания text[];
  элементы  text[] := '{}';
  коды      text[];
begin
  if to_regclass('lib_brands') is null or to_regclass('lib_brand_alias') is null
     or coalesce(b, '') = '' then
    return;
  end if;
  написания := array(
    select distinct s from (
      select a.spelling as s from lib_brand_alias a
       where a.brand_key = b and a.status in ('разрешено', 'проверено')
      union all
      select x.name from lib_brands x where x.brand_key = b) y
     where coalesce(btrim(s), '') <> '');
  if to_regclass('lib_brand_sp176') is not null then
    элементы := array(select s.sp176_id::text from lib_brand_sp176 s where s.brand_key = b);
  end if;
  коды := array(select distinct x.id from lib_parts x where x.oem = any(написания));
  return query
    select distinct on (z.id) z.id, lib_pn_key(z.part_number), z.part_number, z.rfq_company,
           to_char(z.price_date, 'YYYY-MM'), z.via
      from (
        select p.id, p.part_number, p.rfq_company, p.price_date, 'слово КП'::text as via, 0 as ord
          from lib_prices p
         where p.feed = 'разбор КП' and p.oem = any(написания)
        union all
        select p.id, p.part_number, p.rfq_company, p.price_date, 'карточка запроса', 1
          from lib_prices p
         where p.feed = 'разбор КП' and cardinality(элементы) > 0 and p.rfq_brands is not null
           and string_to_array(regexp_replace(p.rfq_brands, '\s', '', 'g'), ',') && элементы
        union all
        select p.id, p.part_number, p.rfq_company, p.price_date, 'каталог', 2
          from lib_prices p
         where p.feed = 'разбор КП' and cardinality(коды) > 0
           and lib_pn_key(p.part_number) = any(коды)
      ) z
     where coalesce(btrim(z.part_number), '') <> ''
     order by z.id, z.ord
     limit предел;
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. КАРТОЧКА КОДА.
--
--    Разделы и их источники:
--      код и бренд — написание (каталог, иначе самое частое в спросе и КП),
--                    бренд с источником и признаком спорности (шапка файла);
--      спрос       — lib_demand_live по ключу (не больше 5 000 строк): сделок,
--                    строк, единиц (сумма правдоподобных количеств — только в
--                    одной единице, как в crossref.СПРОС_SQL), последний месяц
--                    ЗАНЕСЕНИЯ строки (даты сделки в базе нет), заказчиков —
--                    null: связи «сделка → заказчик» в базе нет, и страница
--                    говорит «не знаем», а не ноль;
--      предложения — lib_prices «разбор КП» по ключу (не больше 2 000, новые
--                    первыми), оригинал и аналоги двумя списками по 100;
--      аналоги     — lib_part_alt детали и обратная связь «этот код — аналог к»;
--      машины, узлы — lib_part_models → lib_models, lib_parts.unit_id → lib_units;
--      кому писать — поставщики, дававшие цену по бренду позиции на ДРУГИЕ коды
--                    и не дававшие на этот (portal_brand_rows).
begin;
drop function if exists portal_code(text);
create function portal_code(key text) returns jsonb
  language plpgsql stable
  set plan_cache_mode = force_custom_plan
  set jit = off
as $fn$
#variable_conflict use_column
declare
  запрос   text := btrim(coalesce(portal_code.key, ''));
  k        text;
  бюджет   interval := coalesce(nullif(current_setting('portal_entity.budget_ms', true), '')::int,
                                5000) * interval '1 millisecond';
  реестр   boolean := to_regclass('lib_brands') is not null
                      and to_regclass('lib_brand_alias') is not null
                      and to_regclass('lib_brand_map') is not null
                      and to_regprocedure('lib_brand_key(text)') is not null;
  деталь   jsonb;
  спрос    jsonb := '{}';
  спрос_я  jsonb := '{}';    -- ячейка изготовителя спроса → строк
  строки   jsonb := '[]';    -- строки КП по коду
  кп_я     jsonb := '{}';    -- ячейка изготовителя КП → строк
  карточки jsonb := '{}';    -- элемент СП-176 → {n, key, name}
  маски    jsonb := '[]';
  альт_изг jsonb := '{}';    -- изготовитель по каталогу аналогов → строк
  есть_альт boolean;
  ячейки   jsonb := '{}';    -- ячейка → бренды реестра
  претензии jsonb := '[]';
  бренд    jsonb;
  б_ключ   text;
  б_имя    text;
  спорно   boolean;
  компании jsonb := '{}';
  кп       jsonb;
  аналоги  jsonb := '[]';
  аналог_к jsonb := '[]';
  машины   jsonb := '[]';
  узлы     jsonb := '[]';
  кому     jsonb := '[]';
  годные   text[];
  ещё      jsonb;
  усечено  text[] := '{}';
begin
  if char_length(запрос) = 0 or char_length(запрос) > 120 then
    return null;
  end if;
  k := lib_pn_key(запрос);
  if char_length(k) < 2 then
    return null;
  end if;

  -- ── каталог: точно по id, иначе однозначно по ключу каталожного номера ──
  -- (та же ступенька, что crossref.СЦЕПКА: два номера с одним ключом деталь
  -- не дают — пустой раздел честнее чужой детали).
  select to_jsonb(x) into деталь
    from (select p.id, p.catalog_no, p.name, p.oem, p.kv_no, p.unit_id
            from lib_parts p where p.id = k) x;
  if деталь is null then
    select to_jsonb(x) into деталь
      from (select min(p.id) as id, min(p.catalog_no) as catalog_no, min(p.name) as name,
                   min(p.oem) as oem, min(p.kv_no) as kv_no, min(p.unit_id) as unit_id
              from lib_parts p where lib_pn_key(p.catalog_no) = k
            having count(*) = 1) x;
  end if;

  -- ── правдоподобие: отвергнутый код карточки не получает ──
  if not (k = any(portal_codes_ok(array[k]))) then
    return jsonb_build_object('key', k, 'rejected', true);
  end if;

  -- ── этот код в каталоге аналогов (lib_part_alt.alt_pn, индекс lib_part_alt_key) ──
  есть_альт := exists (select 1 from lib_part_alt a where lib_pn_key(a.alt_pn) = k);
  if есть_альт then
    select coalesce(jsonb_object_agg(z.c, z.n), '{}') into альт_изг
      from (select btrim(a.alt_maker) as c, count(*)::int as n from lib_part_alt a
             where lib_pn_key(a.alt_pn) = k and coalesce(btrim(a.alt_maker), '') <> ''
             group by 1 order by 2 desc, 1 limit 10) z;
  end if;

  -- ── спрос ──
  with d as materialized (
    select d.deal_id, d.part_number, d.item_name, d.oem, d.qty, d.unit, d.created_at
      from lib_demand_live d where lib_pn_key(d.part_number) = k limit 5000)
  select jsonb_build_object(
           'rows', count(*), 'deals', count(distinct d.deal_id),
           'units', count(distinct nullif(btrim(d.unit), '')),
           'qty', case when count(distinct nullif(btrim(d.unit), '')) <= 1
                       then sum(d.qty) filter (where d.qty > 0 and d.qty <= 1000000) end,
           'unit', case when count(distinct nullif(btrim(d.unit), '')) = 1
                        then min(nullif(btrim(d.unit), '')) end,
           'qty_hidden', count(*) filter (where d.qty is not null and not (d.qty > 0 and d.qty <= 1000000)),
           'last_month', to_char(max(d.created_at), 'YYYY-MM'),
           'customers', null,
           'capped', count(*) >= 5000,
           'written', mode() within group (order by d.part_number),
           'name', left(mode() within group (order by d.item_name), 200)),
         (select coalesce(jsonb_object_agg(z.c, z.n), '{}')
            from (select btrim(x.oem) as c, count(*)::int as n from d x
                   where coalesce(btrim(x.oem), '') <> '' group by 1 order by 2 desc, 1 limit 20) z)
    into спрос, спрос_я
    from d;

  -- ── строки КП по коду ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'предложения'::text;
  else
    select coalesce(jsonb_agg(to_jsonb(x) order by x.ord), '[]') into строки
      from (
        select row_number() over (order by p.price_date desc nulls last, p.created_at desc, p.id desc)::int as ord,
               p.part_number as written, p.item_name, p.oem, p.rfq_brands, p.rfq_company, p.rfq_id,
               p.price, p.currency, portal_qty(p.qty, p.price, p.total) as qty,
               (p.qty is not null and portal_qty(p.qty, p.price, p.total) is null) as qty_hidden,
               nullif(btrim(p.qty_unit), '') as unit,
               case when p.total > 0 then p.total end as total,
               nullif(btrim(p.basis), '') as basis, p.lead_days,
               to_char(p.price_date, 'YYYY-MM') as month, p.price_date_src as month_src,
               lower(concat_ws(' ', p.item_name, p.note, p.oem))
                 ~ '(аналог|эквивалент|equivalent|replacement)' as says_analog
          from lib_prices p
         where p.feed = 'разбор КП' and lib_pn_key(p.part_number) = k
         order by p.price_date desc nulls last, p.created_at desc, p.id desc
         limit 2000) x;
    select coalesce(jsonb_object_agg(z.c, z.n), '{}') into кп_я
      from (select btrim(r ->> 'oem') as c, count(*)::int as n from jsonb_array_elements(строки) r
             where coalesce(btrim(r ->> 'oem'), '') <> '' group by 1 order by 2 desc, 1 limit 30) z;
  end if;

  -- Кода нет нигде — ни в каталоге, ни в спросе, ни в КП, ни среди аналогов:
  -- карточки нет (null), а не пустая карточка. Не посчитанные за бюджет КП
  -- этого не решают: «не успели» — не «нет».
  if деталь is null and not есть_альт and coalesce((спрос ->> 'rows')::int, 0) = 0
     and строки = '[]' and 'предложения' <> all(усечено) then
    return null;
  end if;

  -- ── карточка запроса: элементы СП-176 → бренд реестра (или написание
  --    самого справочника; номер элемента наружу не идёт) ──
  if реестр and to_regclass('lib_brand_sp176') is not null and строки <> '[]' then
    select coalesce(jsonb_object_agg(z.id, jsonb_build_object('n', z.n, 'key', z.brand_key, 'name', z.name)), '{}')
      into карточки
      from (
        select ids.id, ids.n, b.brand_key, coalesce(b.name, w.spelling) as name
          from (select btrim(x) as id, count(*)::int as n
                  from jsonb_array_elements(строки) r
                 cross join lateral unnest(string_to_array(
                         case when length(r ->> 'rfq_brands') >= 200
                              then regexp_replace(r ->> 'rfq_brands', ',[^,]*$', '')
                              else r ->> 'rfq_brands' end, ',')) x
                 where btrim(x) ~ '^[0-9]{1,18}$'
                 group by 1) ids
          left join lib_brand_sp176 s on s.sp176_id = ids.id::bigint
          left join lib_brands b on b.brand_key = s.brand_key
          left join lateral (select min(btrim(a.spelling)) as spelling from lib_brand_alias a
                              where a.sp176_id = ids.id::bigint and a.source = 'СП-176') w on true) z
     where z.name is not null;
  end if;

  -- ── маска кода ──
  select coalesce(jsonb_agg(distinct btrim(t.oem)), '[]') into маски
    from lib_pn_patterns t
   where portal_mask_re(t.pattern) is not null and k ~ portal_mask_re(t.pattern)
     and coalesce(btrim(t.oem), '') <> '';

  -- ── бренды реестра по всем ячейкам разом ──
  if реестр then
    ячейки := portal_brands_of(array(
      select деталь ->> 'oem'
      union all select jsonb_object_keys(спрос_я)
      union all select jsonb_object_keys(кп_я)
      union all select jsonb_object_keys(альт_изг)
      union all select jsonb_array_elements_text(маски)));
  end if;

  -- ── претензии на бренд позиции ──
  with ячейки_и as (
    select 'каталог'::text as src, 0 as ord, left(btrim(деталь ->> 'oem'), 200) as cell, 1 as n
     where coalesce(btrim(деталь ->> 'oem'), '') <> ''
    union all select 'каталог аналогов', 1, left(btrim(e.key), 200), e.value::int from jsonb_each(альт_изг) e
    union all select 'спецификация', 2, left(btrim(e.key), 200), e.value::int from jsonb_each(спрос_я) e
    union all select 'маска кода', 4, left(btrim(m), 200), 1 from jsonb_array_elements_text(маски) m
    union all select 'КП', 5, left(btrim(e.key), 200), e.value::int from jsonb_each(кп_я) e
  ), претензия as (
    select и.ord, b ->> 0 as key, b ->> 1 as name, и.n
      from ячейки_и и cross join lateral jsonb_array_elements(ячейки -> и.cell) b
     where ячейки ? и.cell
    union all
    select и.ord, null, и.cell, и.n from ячейки_и и where not (ячейки ? и.cell)
    union all
    select 3, e.value ->> 'key', e.value ->> 'name', (e.value ->> 'n')::int from jsonb_each(карточки) e
  ), по_источнику as (
    select coalesce(п.key, 'слово:' || lower(п.name)) as id, п.ord, min(п.key) as key,
           min(п.name) as name, sum(п.n)::int as n
      from претензия п group by 1, 2
  ), по_бренду as (
    select и.id, min(и.key) as key, (array_agg(и.name order by и.ord))[1] as name,
           min(и.ord) as best, (array_agg(и.n order by и.ord))[1] as n_best,
           sum(и.n)::int as rows, array_agg(и.ord order by и.ord) as ords
      from по_источнику и group by и.id
  )
  select coalesce(jsonb_agg(jsonb_build_object(
           'key', г.key, 'name', г.name, 'rows', г.rows, 'best', г.best,
           'sources', array(select case o when 0 then 'каталог' when 1 then 'каталог аналогов'
                                           when 2 then 'спецификация' when 3 then 'карточка запроса'
                                           when 4 then 'маска кода' else 'КП' end
                              from unnest(г.ords) o order by o))
           order by г.key is null, г.best, г.n_best desc, г.rows desc, г.name), '[]')
    into претензии
    from по_бренду г;

  if претензии <> '[]' then
    бренд := претензии -> 0;
    б_ключ := бренд ->> 'key';
    б_имя := бренд ->> 'name';
  end if;
  if реестр then
    -- Спор — между брендами реестра источников позиции; нет их — между КП;
    -- бренда реестра не назвал никто — спор не судится (null).
    select case when count(*) filter (where (x ->> 'best')::int < 5) > 0
                then count(*) filter (where (x ->> 'best')::int < 5) > 1
                when count(*) > 0 then count(*) > 1 end
      into спорно
      from jsonb_array_elements(претензии) x where x ->> 'key' is not null;
  end if;

  -- ── предложения: оригинал и аналоги ──
  if строки <> '[]' then
    компании := portal_companies(array(select distinct r ->> 'rfq_company' from jsonb_array_elements(строки) r
                                        where r ->> 'rfq_company' is not null));
    with r as (
      select x.*, ячейки -> left(btrim(x.oem), 200) as row_brands
        from jsonb_to_recordset(строки) as x(ord int, written text, oem text, rfq_company text, rfq_id text,
                                             price numeric, currency text, qty numeric, qty_hidden boolean,
                                             unit text, total numeric, basis text, lead_days int,
                                             month text, month_src text, says_analog boolean)
    ), o as (
      select r.*,
             case when б_ключ is not null and r.row_brands is not null
                       and not exists (select 1 from jsonb_array_elements(r.row_brands) y where y ->> 0 = б_ключ)
                  then 'бренд строки — ' || (r.row_brands -> 0 ->> 1) || ', у позиции — ' || б_имя
                  when r.says_analog then 'поставщик пишет «аналог»' end as why,
             jsonb_build_object(
               'company', компании -> r.rfq_company,
               'brand', case when r.row_brands is not null
                             then jsonb_build_object('key', r.row_brands -> 0 ->> 0, 'name', r.row_brands -> 0 ->> 1)
                             when coalesce(btrim(r.oem), '') <> ''
                             then jsonb_build_object('key', null, 'name', left(btrim(r.oem), 120)) end,
               'written', r.written, 'price', r.price, 'currency', r.currency, 'qty', r.qty,
               'qty_hidden', r.qty_hidden, 'unit', r.unit, 'total', r.total, 'basis', r.basis,
               'lead_days', r.lead_days, 'month', r.month, 'month_src', r.month_src) as j
        from r
    )
    select jsonb_build_object(
             'rows', count(*), 'suppliers', count(distinct o.rfq_company), 'cards', count(distinct o.rfq_id),
             'capped', count(*) >= 2000, 'brand_judged', б_ключ is not null,
             'original', coalesce((select jsonb_agg(x.j order by x.ord) from (select * from o where o.why is null order by o.ord limit 100) x), '[]'),
             'analog', coalesce((select jsonb_agg(x.j || jsonb_build_object('why', x.why) order by x.ord)
                                   from (select * from o where o.why is not null order by o.ord limit 100) x), '[]'),
             'original_n', count(*) filter (where o.why is null),
             'analog_n', count(*) filter (where o.why is not null))
      into кп
      from o;
  else
    кп := jsonb_build_object('rows', 0, 'suppliers', 0, 'cards', 0, 'capped', false,
                             'brand_judged', б_ключ is not null, 'original', '[]'::jsonb, 'analog', '[]'::jsonb,
                             'original_n', 0, 'analog_n', 0);
  end if;

  -- ── аналоги по каталогу, машины, узлы ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'аналоги'::text || 'машины'::text;
  else
    if деталь is not null then
      select coalesce(jsonb_agg(jsonb_build_object('code', lib_pn_key(a.alt_pn), 'written', a.alt_pn,
                                                   'kind', a.kind, 'maker', nullif(btrim(a.alt_maker), ''))
                                order by a.kind, a.alt_pn), '[]')
        into аналоги
        from (select * from lib_part_alt a where a.part_id = деталь ->> 'id'
               order by a.kind, a.alt_pn limit 100) a;
      select coalesce(jsonb_agg(jsonb_build_object('id', m.id, 'name', m.name,
                                                   'kind', coalesce(m.kind, m.family_title),
                                                   'segment', m.segment_id, 'maker', nullif(btrim(m.oem), ''))
                                order by m.name), '[]')
        into машины
        from (select m.* from lib_part_models pm join lib_models m on m.id = pm.model_id
               where pm.part_id = деталь ->> 'id' order by m.name limit 50) m;
      select coalesce(jsonb_agg(jsonb_build_object('id', u.id, 'name', u.name, 'parent', р.name,
                                                   'crit', u.crit)), '[]')
        into узлы
        from lib_units u left join lib_units р on р.id = u.parent_id
       where u.id = деталь ->> 'unit_id';
    end if;
    -- Этот код назван аналогом (номером изготовителя, заменой) к детали каталога.
    select coalesce(jsonb_agg(jsonb_build_object('code', x.id, 'written', x.catalog_no, 'kind', x.kind,
                                                 'maker', nullif(btrim(x.oem), ''))
                              order by x.kind, x.catalog_no), '[]')
      into аналог_к
      from (select a.kind, p.id, p.catalog_no, p.oem from lib_part_alt a join lib_parts p on p.id = a.part_id
             where lib_pn_key(a.alt_pn) = k and p.id <> k
             order by a.kind, p.catalog_no limit 50) x;
    -- Бренды изготовителей аналогов и машин, коды — только правдоподобные.
    if реестр then
      ещё := portal_brands_of(array(
        select x ->> 'maker' from jsonb_array_elements(аналоги || аналог_к || машины) x
         where x ->> 'maker' is not null));
    else
      ещё := '{}';
    end if;
    годные := portal_codes_ok(array(select x ->> 'code' from jsonb_array_elements(аналоги || аналог_к) x));
    select coalesce(jsonb_agg(
             (x - 'maker' - 'code')
             || jsonb_build_object('code', case when x ->> 'code' = any(годные) then x ->> 'code' end,
                                   'brand', case when ещё ? (x ->> 'maker')
                                                 then jsonb_build_object('key', ещё -> (x ->> 'maker') -> 0 ->> 0,
                                                                         'name', ещё -> (x ->> 'maker') -> 0 ->> 1)
                                                 when x ->> 'maker' is not null
                                                 then jsonb_build_object('key', null, 'name', left(x ->> 'maker', 120)) end)
             order by n), '[]')
      into аналоги
      from jsonb_array_elements(аналоги) with ordinality as t(x, n);
    select coalesce(jsonb_agg(
             (x - 'maker' - 'code')
             || jsonb_build_object('code', case when x ->> 'code' = any(годные) then x ->> 'code' end,
                                   'brand', case when ещё ? (x ->> 'maker')
                                                 then jsonb_build_object('key', ещё -> (x ->> 'maker') -> 0 ->> 0,
                                                                         'name', ещё -> (x ->> 'maker') -> 0 ->> 1)
                                                 when x ->> 'maker' is not null
                                                 then jsonb_build_object('key', null, 'name', left(x ->> 'maker', 120)) end)
             order by n), '[]')
      into аналог_к
      from jsonb_array_elements(аналог_к) with ordinality as t(x, n);
    select coalesce(jsonb_agg(
             (x - 'maker')
             || jsonb_build_object('brand', case when ещё ? (x ->> 'maker')
                                                 then jsonb_build_object('key', ещё -> (x ->> 'maker') -> 0 ->> 0,
                                                                         'name', ещё -> (x ->> 'maker') -> 0 ->> 1)
                                                 when x ->> 'maker' is not null
                                                 then jsonb_build_object('key', null, 'name', left(x ->> 'maker', 120)) end)
             order by n), '[]')
      into машины
      from jsonb_array_elements(машины) with ordinality as t(x, n);
  end if;

  -- ── кому ещё писать: давали цену по бренду позиции, но не по этому коду ──
  if б_ключ is not null then
    if clock_timestamp() - statement_timestamp() > бюджет then
      усечено := усечено || 'кому ещё писать'::text;
    else
      -- Группа — компания реестра, а не ключ портала: у слитой компании ключей
      -- несколько, и она не должна встать в список дважды или «новой».
      with р as materialized (
        select r.rfq_company, r.code, r.month
          from portal_brand_rows(б_ключ, 20000) r where r.rfq_company is not null
      ), к as (
        select portal_companies(array(
                 select distinct р.rfq_company from р
                 union select distinct x ->> 'rfq_company' from jsonb_array_elements(строки) x
                  where x ->> 'rfq_company' is not null)) as м
      ), свои as (
        select distinct к.м -> (x ->> 'rfq_company') ->> 'id' as cid
          from jsonb_array_elements(строки) x, к where к.м ? (x ->> 'rfq_company')
      ), г as (
        select к.м -> р.rfq_company ->> 'id' as cid, (array_agg(к.м -> р.rfq_company))[1] as company,
               count(distinct р.code)::int as codes, count(*)::int as rows, max(р.month) as last_month
          from р, к where к.м ? р.rfq_company and р.code <> k
         group by 1
      )
      select coalesce(jsonb_agg(jsonb_build_object('company', г.company, 'codes', г.codes,
                                                   'rows', г.rows, 'last_month', г.last_month)
                                order by г.codes desc, г.rows desc, г.cid), '[]')
        into кому
        from (select * from г where г.cid not in (select cid from свои where cid is not null)
               order by г.codes desc, г.rows desc, г.cid limit 15) г;
    end if;
  end if;

  return jsonb_build_object(
    'key', k,
    'written', coalesce(деталь ->> 'catalog_no',
                        (select r ->> 'written' from jsonb_array_elements(строки) r
                          group by 1 order by count(*) desc, 1 limit 1),
                        спрос ->> 'written', запрос),
    'name', coalesce(деталь ->> 'name', спрос ->> 'name',
                     (select left(r ->> 'item_name', 200) from jsonb_array_elements(строки) r
                       where r ->> 'item_name' is not null group by 1 order by count(*) desc, 1 limit 1)),
    'kv_no', деталь ->> 'kv_no',
    'catalog', деталь is not null,
    'brand', case when бренд is not null then jsonb_build_object(
               'key', б_ключ, 'name', б_имя, 'src', бренд -> 'sources' ->> 0, 'disputed', спорно) end,
    'brands', coalesce((select jsonb_agg(x - 'best') from jsonb_array_elements(претензии) x), '[]'),
    'demand', спрос - 'written' - 'name',
    'offers', кп,
    'analogs', аналоги,
    'analog_of', аналог_к,
    'machines', машины,
    'units', узлы,
    'write_to', кому,
    'registry', реестр,
    'partial', to_jsonb(усечено));
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. КАРТОЧКА БРЕНДА. Без реестра брендов карточки нет: ответ {"registry":
--    false}, и страница говорит, что реестр не установлен.
--
--    спрос        — lib_demand_live по дословным написаниям бренда (индекс
--                   lib_demand_oem), не больше 20 000 строк; рядом — строк по
--                   засеву реестра (lib_brand_alias.n_rows источника
--                   lib_demand.oem): в них ячейки из нескольких брендов тоже;
--    коды         — топ-25 по спросу (сделок) и по предложениям (строк КП),
--                   только правдоподобные; каталог — число деталей и 25 из них;
--    машины       — lib_models, чей изготовитель разрешается в этот бренд;
--    поставщики   — давали цену по бренду (portal_brand_rows), топ-30;
--    аналоги      — lib_part_alt к деталям бренда с изготовителем ДРУГОГО
--                   бренда (или не названным).
drop function if exists portal_brand(text);
create function portal_brand(brand_key text) returns jsonb
  language plpgsql stable
  set plan_cache_mode = force_custom_plan
  set jit = off
as $fn$
#variable_conflict use_column
declare
  б        text := btrim(coalesce(portal_brand.brand_key, ''));
  бюджет   interval := coalesce(nullif(current_setting('portal_entity.budget_ms', true), '')::int,
                                5000) * interval '1 millisecond';
  запись   jsonb;
  написания text[];
  спрос    jsonb := '{}';
  по_спросу jsonb := '[]';
  по_кп    jsonb := '[]';
  кп       jsonb := '{}';
  каталог  jsonb := '{}';
  детали   text[];
  машины   jsonb := '[]';
  поставщики jsonb := '[]';
  аналоги  jsonb := '[]';
  годные   text[];
  ещё      jsonb;
  усечено  text[] := '{}';
begin
  if to_regclass('lib_brands') is null or to_regclass('lib_brand_alias') is null
     or to_regclass('lib_brand_map') is null or to_regprocedure('lib_brand_key(text)') is null then
    return jsonb_build_object('registry', false);
  end if;
  if char_length(б) = 0 or char_length(б) > 80 then
    return null;
  end if;
  select to_jsonb(x) into запись
    from (select b.brand_key, b.name, b.country, b.owner, b.former_names from lib_brands b
           where b.brand_key = б) x;
  if запись is null then
    return null;
  end if;
  написания := array(
    select distinct s from (
      select a.spelling as s from lib_brand_alias a
       where a.brand_key = б and a.status in ('разрешено', 'проверено')
      union all select запись ->> 'name') y
     where coalesce(btrim(s), '') <> '');

  -- ── спрос ──
  with d as materialized (
    select lib_pn_key(d.part_number) as code, d.part_number, d.deal_id
      from lib_demand_live d where d.oem = any(написания) limit 20000)
  select jsonb_build_object('rows', count(*), 'deals', count(distinct d.deal_id),
                            'codes', count(distinct d.code) filter (where d.code <> ''),
                            'capped', count(*) >= 20000),
         (select coalesce(jsonb_agg(jsonb_build_object('code', z.code, 'written', z.written,
                                                       'deals', z.deals, 'rows', z.rows)
                                    order by z.deals desc, z.rows desc, z.code), '[]')
            from (select x.code, mode() within group (order by x.part_number) as written,
                         count(distinct x.deal_id)::int as deals, count(*)::int as rows
                    from d x where char_length(x.code) >= 2 group by x.code
                   order by 3 desc, 4 desc, 1 limit 60) z)
    into спрос, по_спросу
    from d;
  спрос := спрос || jsonb_build_object('registry_rows',
    (select coalesce(sum(a.n_rows), 0) from lib_brand_alias a
      where a.brand_key = б and a.status in ('разрешено', 'проверено') and a.source = 'lib_demand.oem'));

  -- ── каталог ──
  детали := array(select p.id from lib_parts p where p.oem = any(написания) order by p.id);
  select jsonb_build_object('parts', cardinality(детали),
           'list', coalesce(jsonb_agg(jsonb_build_object('code', p.id, 'written', p.catalog_no,
                                                         'name', p.name, 'kv_no', p.kv_no)
                                      order by (p.kv_no is null), p.catalog_no), '[]'))
    into каталог
    from (select * from lib_parts p where p.id = any(детали)
           order by (p.kv_no is null), p.catalog_no limit 25) p;

  -- ── предложения и поставщики ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'предложения'::text || 'поставщики'::text;
  else
    with р as materialized (select * from portal_brand_rows(б, 20000)),
         к as (select portal_companies(array(select distinct р.rfq_company from р
                                              where р.rfq_company is not null)) as м)
    select jsonb_build_object('rows', (select count(*) from р), 'capped', (select count(*) from р) >= 20000,
                              'suppliers', (select count(distinct к.м -> x.rfq_company ->> 'id') from р x
                                             where к.м ? x.rfq_company),
                              'rows_unresolved', (select count(*) from р x where not (к.м ? coalesce(x.rfq_company, '')))),
           (select coalesce(jsonb_agg(jsonb_build_object('code', z.code, 'written', z.written, 'rows', z.rows,
                                                         'suppliers', z.sups, 'last_month', z.last_month)
                                      order by z.rows desc, z.code), '[]')
              from (select x.code, mode() within group (order by x.written) as written, count(*)::int as rows,
                           count(distinct x.rfq_company)::int as sups, max(x.month) as last_month
                      from р x where char_length(x.code) >= 2 group by x.code
                     order by 3 desc, 1 limit 60) z),
           -- Группа — компания реестра: у слитой ключей портала несколько.
           -- Несведённые с реестром в список не идут (имени нет), но в числе
           -- строк и поставщиков выше они есть.
           (select coalesce(jsonb_agg(jsonb_build_object('company', z.company, 'codes', z.codes,
                                                         'rows', z.rows, 'last_month', z.last_month)
                                      order by z.codes desc, z.rows desc, z.cid), '[]')
              from (select к.м -> x.rfq_company ->> 'id' as cid, (array_agg(к.м -> x.rfq_company))[1] as company,
                           count(distinct x.code)::int as codes, count(*)::int as rows, max(x.month) as last_month
                      from р x, к where к.м ? x.rfq_company group by 1
                     order by 3 desc, 4 desc, 1 limit 30) z)
      into кп, по_кп, поставщики
      from к;
  end if;

  -- Коды — только правдоподобные, по 25 в каждом списке.
  годные := portal_codes_ok(array(select x ->> 'code' from jsonb_array_elements(по_спросу || по_кп) x));
  select coalesce(jsonb_agg(y.x order by y.n), '[]') into по_спросу
    from (select t.x, row_number() over (order by t.n0) as n
            from jsonb_array_elements(по_спросу) with ordinality as t(x, n0)
           where t.x ->> 'code' = any(годные)) y
   where y.n <= 25;
  select coalesce(jsonb_agg(y.x order by y.n), '[]') into по_кп
    from (select t.x, row_number() over (order by t.n0) as n
            from jsonb_array_elements(по_кп) with ordinality as t(x, n0)
           where t.x ->> 'code' = any(годные)) y
   where y.n <= 25;

  -- ── машины бренда ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'машины'::text || 'аналоги'::text;
  else
    ещё := portal_brands_of(array(select distinct m.oem from lib_models m where coalesce(btrim(m.oem), '') <> ''));
    select coalesce(jsonb_agg(jsonb_build_object('id', m.id, 'name', m.name,
                                                 'kind', coalesce(m.kind, m.family_title),
                                                 'segment', m.segment_id,
                                                 'parts', (select count(*) from lib_part_models pm where pm.model_id = m.id))
                              order by m.name), '[]')
      into машины
      from lib_models m
     where exists (select 1 from jsonb_array_elements(ещё -> left(btrim(m.oem), 200)) y where y ->> 0 = б);

    -- ── аналоги к кодам бренда: изготовитель другого бренда или не назван ──
    select coalesce(jsonb_agg(jsonb_build_object('code', a.part_id, 'written', p.catalog_no,
                                                 'alt_code', lib_pn_key(a.alt_pn), 'alt_written', a.alt_pn,
                                                 'kind', a.kind, 'maker', nullif(btrim(a.alt_maker), ''))
                              order by p.catalog_no, a.kind, a.alt_pn), '[]')
      into аналоги
      from (select * from lib_part_alt a where a.part_id = any(детали)
             order by a.part_id, a.kind, a.alt_pn limit 300) a
      join lib_parts p on p.id = a.part_id;
    ещё := portal_brands_of(array(select x ->> 'maker' from jsonb_array_elements(аналоги) x
                                   where x ->> 'maker' is not null));
    годные := portal_codes_ok(array(select x ->> 'alt_code' from jsonb_array_elements(аналоги) x));
    select coalesce(jsonb_agg(
             (x - 'maker' - 'alt_code')
             || jsonb_build_object(
                  'alt_code', case when x ->> 'alt_code' = any(годные) then x ->> 'alt_code' end,
                  'brand', case when ещё ? (x ->> 'maker')
                                then jsonb_build_object('key', ещё -> (x ->> 'maker') -> 0 ->> 0,
                                                        'name', ещё -> (x ->> 'maker') -> 0 ->> 1)
                                when x ->> 'maker' is not null
                                then jsonb_build_object('key', null, 'name', left(x ->> 'maker', 120)) end)
             order by n), '[]')
      into аналоги
      from jsonb_array_elements(аналоги) with ordinality as t(x, n)
     where not exists (select 1 from jsonb_array_elements(ещё -> (x ->> 'maker')) y where y ->> 0 = б);
    select coalesce(jsonb_agg(x order by n), '[]') into аналоги
      from jsonb_array_elements(аналоги) with ordinality as t(x, n) where n <= 100;
  end if;

  return jsonb_build_object(
    'key', б, 'name', запись ->> 'name', 'country', запись ->> 'country', 'owner', запись ->> 'owner',
    'former_names', запись ->> 'former_names',
    'spellings', coalesce((select jsonb_agg(s order by n desc, s) from (
        select btrim(a.spelling) as s, sum(coalesce(a.n_rows, 0)) as n from lib_brand_alias a
         where a.brand_key = б and a.status in ('разрешено', 'проверено')
           and btrim(a.spelling) <> (запись ->> 'name')
         group by 1 order by 2 desc, 1 limit 30) z), '[]'),
    'demand', спрос,
    'codes_demand', по_спросу,
    'codes_offers', по_кп,
    'offers', кп,
    'catalog', каталог,
    'machines', машины,
    'suppliers', поставщики,
    'analogs', аналоги,
    'registry', true,
    'partial', to_jsonb(усечено));
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. КАРТОЧКА ПОСТАВЩИКА. Как suppliersCut у воркера: ни контактов людей, ни
--    финансовых условий (оплата, аванс, договор) — только кто это и что он
--    котировал. Цена из КП — его предложение нам, она показывается (решение в
--    воркере, SUPPLIERS_FIELDS).
--
--    кто          — показанное имя с источником, ИНН (признаки и реквизиты
--                   Битрикса), домены, страна, город, номер KV-S (если выдан),
--                   карточки Битрикса (номера companyId — ссылка в Битрикс);
--    запросы и КП — факт rfq_stats (sup_fact), если он посчитан; строки КП по
--                   ключам портала (индекс lib_prices_rfqco), не больше 5 000;
--    бренды       — по бренду строки: слово КП → реестр, иначе бренд карточки
--                   запроса, иначе изготовитель по каталогу;
--    коды         — последнее предложение по коду, топ-100 по свежести.
drop function if exists portal_supplier(text);
create function portal_supplier(sup_id text) returns jsonb
  language plpgsql stable
  set plan_cache_mode = force_custom_plan
  set jit = off
as $fn$
#variable_conflict use_column
declare
  номер    text := upper(btrim(coalesce(portal_supplier.sup_id, '')));
  исходный text;
  реестр   boolean := to_regclass('lib_brands') is not null
                      and to_regclass('lib_brand_alias') is not null
                      and to_regclass('lib_brand_map') is not null
                      and to_regprocedure('lib_brand_key(text)') is not null;
  сущность record;
  члены    text[];
  ключи    text[];
  имя      jsonb;
  инн      text[] := '{}';
  стат     jsonb;
  строки   jsonb := '[]';
  квоты    jsonb := '{}';
  ячейки   jsonb := '{}';
  карточки jsonb := '{}';
  каталог  jsonb := '{}';
  коды     jsonb := '[]';
  бренды   jsonb := '[]';
  годные   text[];
  бюджет   interval := coalesce(nullif(current_setting('portal_entity.budget_ms', true), '')::int,
                                5000) * interval '1 millisecond';
  усечено  text[] := '{}';
begin
  if to_regclass('sup_entity') is null or to_regclass('sup_identifier') is null then
    return null;
  end if;
  if номер !~ '^KV-[SG]-[0-9]{6}-[0-9]$' then
    return null;
  end if;
  -- Слитая ведёт на ту, в которую слита (не больше пяти шагов: цепочка
  -- слияний длиннее — ошибка сведения, и ходить по кругу нельзя).
  for шаг in 1 .. 5 loop
    select e.* into сущность from sup_entity e where e.id = номер;
    exit when not found or сущность.resolution <> 'merged' or сущность.merged_into is null;
    исходный := coalesce(исходный, номер);
    номер := сущность.merged_into;
  end loop;
  if сущность.id is null or сущность.resolution = 'merged' then
    return null;
  end if;
  -- Признаки слитых в неё сущностей — тоже её.
  члены := array(select номер union select e.id from sup_entity e where e.merged_into = номер);
  ключи := array(select distinct i.value_norm from sup_identifier i
                  where i.sup_id = any(члены) and i.kind = 'bitrix' and i.status <> 'rejected');
  имя := portal_sup_names(array[номер]) -> номер;
  инн := array(select distinct i.value from sup_identifier i
                where i.sup_id = any(члены) and i.kind = 'inn' and i.status <> 'rejected');
  if to_regclass('sup_display_name') is not null then
    инн := array(select distinct x from unnest(инн || array(
             select d.inn from (select distinct on (d.sup_id) d.sup_id, d.inn from sup_display_name d
                                 where d.sup_id = any(члены) and d.source = 'bitrix:requisite'
                                   and d.rolled_back_at is null
                                 order by d.sup_id, d.id desc) d
              where d.inn is not null)) x where coalesce(x, '') <> '' order by 1);
  end if;
  if to_regclass('sup_fact') is not null then
    execute $q$
      select value from sup_fact
       where subject_kind = 'entity' and subject_id = $1 and field = 'rfq_stats' and status <> 'superseded'
       order by id desc limit 1
    $q$ into стат using номер;
  end if;

  -- ── строки КП поставщика ──
  if cardinality(ключи) > 0 then
    select coalesce(jsonb_agg(to_jsonb(x) order by x.ord), '[]') into строки
      from (select row_number() over (order by p.price_date desc nulls last, p.created_at desc, p.id desc)::int as ord,
                   lib_pn_key(p.part_number) as code, p.part_number as written, p.oem, p.rfq_brands, p.rfq_id,
                   p.price, p.currency, portal_qty(p.qty, p.price, p.total) as qty,
                   nullif(btrim(p.qty_unit), '') as unit, to_char(p.price_date, 'YYYY-MM') as month
              from lib_prices p
             where p.feed = 'разбор КП' and p.rfq_company = any(ключи)
               and coalesce(btrim(p.part_number), '') <> ''
             order by p.price_date desc nulls last, p.created_at desc, p.id desc
             limit 5000) x;
  end if;
  годные := portal_codes_ok(array(select distinct r ->> 'code' from jsonb_array_elements(строки) r));
  select jsonb_build_object('rows', count(*), 'cards', count(distinct r ->> 'rfq_id'),
                            'codes', count(distinct r ->> 'code') filter (where r ->> 'code' = any(годные)),
                            'last_month', max(r ->> 'month'), 'capped', count(*) >= 5000)
    into квоты
    from jsonb_array_elements(строки) r;

  -- ── бренд строки: слово КП → реестр, иначе карточка, иначе каталог ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'бренды'::text || 'коды'::text;
  else
    if реестр and строки <> '[]' then
      -- Разрешаются 300 самых частых ячеек: ключ написания дорог (шапка файла).
      ячейки := portal_brands_of(array(
        select btrim(r ->> 'oem') from jsonb_array_elements(строки) r
         where coalesce(btrim(r ->> 'oem'), '') <> '' group by 1 order by count(*) desc, 1 limit 300));
      if to_regclass('lib_brand_sp176') is not null then
        select coalesce(jsonb_object_agg(s.sp176_id::text, jsonb_build_array(b.brand_key, b.name)), '{}')
          into карточки
          from lib_brand_sp176 s join lib_brands b on b.brand_key = s.brand_key
         where s.sp176_id::text = any(array(
           select distinct btrim(x) from jsonb_array_elements(строки) r
            cross join lateral unnest(string_to_array(r ->> 'rfq_brands', ',')) x
            where btrim(x) ~ '^[0-9]{1,18}$'));
      end if;
      select coalesce(jsonb_object_agg(z.code, jsonb_build_array(z.brand_key, z.name)), '{}') into каталог
        from (select p.id as code, b.brand_key, b.name
                from lib_parts p
                join lib_brand_map m on m.spelling_key = lib_brand_key(p.oem)
                join lib_brands b on b.brand_key = m.brand_key
               where p.id = any(годные) and coalesce(btrim(p.oem), '') <> '') z;
    end if;
    with r as (
      select x.*, n,
             coalesce(ячейки -> left(btrim(x.oem), 200) -> 0,
                      (select карточки -> btrim(y) from unnest(string_to_array(x.rfq_brands, ',')) y
                        where карточки ? btrim(y) limit 1),
                      каталог -> x.code) as бр
        from jsonb_array_elements(строки) with ordinality as t(j, n)
        cross join lateral jsonb_to_record(j) as x(code text, written text, oem text, rfq_brands text,
                                                   price numeric, currency text, qty numeric, unit text,
                                                   month text)
       where x.code = any(годные)
    ), б as (
      select coalesce(r.бр ->> 0, 'слово:' || lower(btrim(r.oem))) as id,
             min(r.бр ->> 0) as key,
             coalesce(min(r.бр ->> 1), min(left(btrim(r.oem), 120))) as name,
             count(distinct r.code)::int as codes, count(*)::int as rows, max(r.month) as last_month
        from r where r.бр is not null or coalesce(btrim(r.oem), '') <> ''
       group by 1
    ), к as (
      select distinct on (r.code) r.code, r.written, r.бр, coalesce(r.бр ->> 1, left(btrim(r.oem), 120)) as bname,
             r.price, r.currency, r.qty, r.unit, r.month, r.n,
             count(*) over (partition by r.code) as offers
        from r order by r.code, r.n
    )
    select (select coalesce(jsonb_agg(jsonb_build_object('brand', jsonb_build_object('key', б.key, 'name', б.name),
                                                         'codes', б.codes, 'rows', б.rows, 'last_month', б.last_month)
                                      order by б.key is null, б.codes desc, б.rows desc, б.name), '[]')
              from (select * from б order by б.key is null, б.codes desc, б.rows desc, б.name limit 40) б),
           (select coalesce(jsonb_agg(jsonb_build_object(
                     'code', к.code, 'written', к.written,
                     'brand', case when к.bname is not null then jsonb_build_object('key', к.бр ->> 0, 'name', к.bname) end,
                     'price', к.price, 'currency', к.currency, 'qty', к.qty, 'unit', к.unit,
                     'month', к.month, 'offers', к.offers)
                     order by к.n), '[]')
              from (select * from к order by к.n limit 100) к)
      into бренды, коды;
  end if;

  return jsonb_build_object(
    'id', номер,
    'merged_from', исходный,
    'name', имя ->> 'name',
    'name_src', имя ->> 'src',
    'number', имя ->> 'number',
    'inn', to_jsonb(инн),
    'domains', coalesce((select jsonb_agg(distinct i.value) from sup_identifier i
                          where i.sup_id = any(члены) and i.kind = 'domain' and i.status <> 'rejected'), '[]'),
    'country', сущность.country,
    'city', сущность.city,
    'status', сущность.status,
    'bitrix', coalesce((select jsonb_agg(v order by v::bigint) from unnest(ключи) v where v ~ '^[0-9]{1,18}$'), '[]'),
    'rfq', case when jsonb_typeof(стат) = 'object' then jsonb_strip_nulls(jsonb_build_object(
             'sent', стат -> 'sent', 'answered', стат -> 'answered', 'quoted', стат -> 'quoted',
             'silent', стат -> 'silent', 'no_outcome', стат -> 'no_outcome', 'cards', стат -> 'cards')) end,
    'quotes', квоты,
    'brands', бренды,
    'codes', коды,
    'registry', реестр,
    'partial', to_jsonb(усечено));
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. Права: функции по умолчанию исполнимы всеми (PUBLIC) — снимаем; роли
--     платформы только через проверку наличия (правило 20). Исполняет
--     сервисный ключ воркера портала, за Cloudflare Access и правом suppliers.
--     Помощники тоже: они вызываются от имени вызывающего.
revoke all on function portal_code(text) from public;
revoke all on function portal_brand(text) from public;
revoke all on function portal_supplier(text) from public;
revoke all on function portal_qty(numeric, numeric, numeric) from public;
revoke all on function portal_mask_re(text) from public;
revoke all on function portal_codes_ok(text[]) from public;
revoke all on function portal_brands_of(text[]) from public;
revoke all on function portal_sup_names(text[]) from public;
revoke all on function portal_companies(text[]) from public;
revoke all on function portal_brand_rows(text, int) from public;
do $$
declare
  кому text;
  сервис text;
  ф text;
begin
  select string_agg(quote_ident(rolname), ', ') into кому
    from pg_roles where rolname in ('anon', 'authenticated');
  select string_agg(quote_ident(rolname), ', ') into сервис
    from pg_roles where rolname = 'service_role';
  foreach ф in array array['portal_code(text)', 'portal_brand(text)', 'portal_supplier(text)',
                           'portal_qty(numeric, numeric, numeric)', 'portal_mask_re(text)',
                           'portal_codes_ok(text[])', 'portal_brands_of(text[])',
                           'portal_sup_names(text[])', 'portal_companies(text[])',
                           'portal_brand_rows(text, int)'] loop
    if кому is not null then
      execute format('revoke all on function %s from %s', ф, кому);
    end if;
    if сервис is not null then
      execute format('grant execute on function %s to %s', ф, сервис);
    end if;
  end loop;
end $$;
commit;
