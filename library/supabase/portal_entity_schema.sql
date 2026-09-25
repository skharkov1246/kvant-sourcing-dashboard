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
-- ШАГ 3 (25.09.2026): portal_model(ключ машины) и portal_unit(ключ узла) —
-- карточки машины и узла (раздел 11): узлы машины по деталям и типовым деревом,
-- детали парой «код + бренд», парк, ведомость, признаки, дефекты, ремонт.
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
--     реестра идёт, только если реестрового нет вовсе, и показывается словом.
--     Слово, которое реестр пометил «не бренд» или «отклонено» («любой»,
--     страна, указание к закупке), и слово, равное самому коду (одна колонка
--     шапки досталась и номеру, и изготовителю), брендом не считаются вовсе;
--   · «спорно» — если источники, называющие бренд ПОЗИЦИИ (все, кроме КП),
--     разрешаются в разные бренды реестра.
--     Слово поставщика в КП — утверждение о его ПРЕДЛОЖЕНИИ, а не о позиции:
--     оно делает строку аналогом, а позицию спорной не делает. Кроме случая,
--     когда кроме КП бренд не назван никем: тогда спорно, если КП расходятся,
--     и строки КП по бренду НЕ делятся вовсе (brand_judged = false) — иначе
--     бренд позиции выбрало бы голосование поставщиков, и оригинал поставщика
--     в меньшинстве ушёл бы в аналоги. Нет реестра — спор не судится (null);
--   · аналоги отдельно от оригинала: строка КП — аналог, если её бренд реестра
--     не тот, что у позиции, или поставщик пишет «аналог / эквивалент /
--     equivalent / replacement» (portal_says_analog). Строка без бренда или со
--     словом не из реестра остаётся в оригинале С ПОМЕТКОЙ «оригинал не
--     подтверждён» — обвинять её нечем (правило 7), выдавать за подтверждённую
--     нельзя. Взаимозаменяемость из каталога (lib_part_alt) — отдельным списком;
--   · только человеческие имена: компания — вид sup_name_shown (без него —
--     display_name, если он не похож на ключ), бренд — lib_brands.name, машина
--     и узел — их имена. Номер элемента СП-176 наружу не идёт: без бренда
--     реестра — написанием самого справочника, а без него не показывается.
--     Номера карточек Битрикса (компании и запроса) идут ТОЛЬКО отдельными
--     полями для ссылки «карточка в Битриксе ↗», в тексте страницы их нет;
--   · количество — только правдоподобное: portal_qty — правило
--     crossref.КОЛ_ЧИТАЕТСЯ буква в букву (сверяет тест), иначе null и
--     «не знаем» на странице, а сама строка не прячется. Сумма строки при
--     нечитаемом количестве тоже не показывается: проверить её нечем;
--   · код, отвергнутый правилом правдоподобия (lib_pn_plausible — двойник
--     docfilter.sql_код_годен, каталог защищает), карточки не получает: ответ
--     {"rejected": true}, а в списках такой код идёт без ссылки или не идёт.
--
-- КОМПАНИЯ — КОРЕНЬ ЦЕПОЧКИ СЛИЯНИЙ, как в codes_sql.SUPPLIER_CTES: A → B → C
-- даёт C (не больше двадцати шагов; круг — ошибка сведения, и тогда компания
-- остаётся собой). Счёт поставщиков — по компаниям реестра, а не по ключам
-- портала: у одной компании бывает несколько карточек Битрикса.
--
-- ДЕТАЛЬ, ЧЕЙ id НЕ РАВЕН КЛЮЧУ КАТАЛОЖНОГО НОМЕРА (таких 82: «ht55norm» у
-- номера HT-55), ищется в спросе и КП по ОБОИМ ключам — иначе карточка по
-- ссылке из каталога и карточка по номеру показывали бы разное.
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
-- (lib_brand_alias.spelling), и только тем, чей ключ ОДНОЗНАЧЕН (lib_brand_map:
-- написание, сведённое к двум брендам, не считается ни одному). Ключ написания
-- lib_brand_key стоит ~60 мкс на ячейку, и разрешать им все ячейки таблицы на
-- каждый вызов — секунды (замер 25.09.2026: 8 000 ячеек — 0,46 с, с делением
-- ячейки на части — 1 с). Цена — ячейка «SKF, FAG» бренду SKF не
-- засчитывается; поэтому счёт по бренду подписан на странице «не меньше».
--
-- ИДЕМПОТЕНТНО: применяется повторно без ошибок. Роли Supabase — только через
-- проверку наличия (правило 20). Применение — Actions → «ZIP base — apply DB
-- migrations» (zip-db.yml), после portal_schema.sql.

set statement_timeout = '10min';
set lock_timeout      = '10s';

-- ВЕСЬ ФАЙЛ — ОДНОЙ ТРАНЗАКЦИЕЙ, помощники тоже. Прежде помощники создавались
-- до begin, а права снимались в конце транзакции: при первом применении они
-- получали права Supabase по умолчанию (execute у anon и authenticated), и
-- упавшая транзакция оставила бы их открытыми через /rest/v1/rpc.
begin;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Количество, которое читается. Тело — crossref.КОЛ_ЧИТАЕТСЯ дословно, без
--    псевдонима «p.» (tests/test_portal_entity_sql.py сверяет строку): больше
--    миллиона или «количество × цена ≠ сумма» с допуском разборщика —
--    количества нет. ОДНО ВЫРАЖЕНИЕ БЕЗ FROM — И ЭТО ЗАМЕР: такую функцию
--    планировщик встраивает в запрос, а с подзапросом «from (select …) p» она
--    исполнялась отдельным вызовом на каждую строку, и карточка поставщика с
--    5 000 строками КП тратила на это больше половины времени (25.09.2026).
--    Количество спроса — тем же правилом без цены и суммы: остаётся предел.
create or replace function portal_qty(qty numeric, price numeric, total numeric) returns numeric
  language sql immutable parallel safe as $$
    select case when qty > 0 and qty <= 1000000 and not coalesce(price > 0 and total > 0 and abs(price * qty - total) > greatest(0.5, qty * 0.005), false)
                then qty end
  $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1а. Поставщик сам пишет, что это аналог. Слова — закрытым списком (правило 7:
--     обвинять только закрытым списком), граница слова — явным классом, а не
--     «\m» (у PostgreSQL слово по «\m» — по локали базы, см.
--     codes_sql.LEGAL_FORMS_KEY). Прилагательные не обвиняют: «датчик давления
--     аналоговый 4–20 мА», «аналогичный», «эквивалентный диаметр» — описание
--     изделия, а не отказ от оригинала. Одно выражение без FROM — встраивается.
create or replace function portal_says_analog(t text) returns boolean
  language sql immutable parallel safe as $$
    select coalesce(lower(t) ~ '(^|[^0-9a-zа-яё])(аналог(?!ов[а-яё]|ичн)|эквивалент(?!н)|equivalent|replacement)', false)
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
--    защищает»). Каталог проверяется ОДНИМ проходом и только для отвергнутых:
--    индекса по ключу каталожного номера нет, и чтение lib_parts на каждый
--    отвергнутый код (13 тыс. деталей на код) у поставщика с тысячами кодов
--    стоило бы секунд.
create or replace function portal_codes_ok(keys text[]) returns text[]
  language plpgsql stable as $fn$
declare
  годные text[];
begin
  if to_regprocedure('lib_pn_plausible(text)') is null then
    return array(select distinct x from unnest(keys) x where coalesce(x, '') <> '');
  end if;
  execute $q$
    with к as (
      select distinct x from unnest($1) x where coalesce(x, '') <> ''
    ), плохие as materialized (
      select к.x from к where not lib_pn_plausible(к.x)
    ), защищены as (
      select p.id as x from lib_parts p where p.id in (select x from плохие)
      union
      select lib_pn_key(p.catalog_no) from lib_parts p
       where exists (select 1 from плохие) and lib_pn_key(p.catalog_no) in (select x from плохие)
    )
    select coalesce(array_agg(к.x), '{}') from к
     where к.x not in (select x from плохие) or к.x in (select x from защищены)
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

-- 4а. Ячейки, которые брендом не являются по слову реестра: ключ целой ячейки
--     стоит в lib_brand_alias со статусом «не бренд» (засев: пометка незнания,
--     страна, указание к закупке — закрытыми списками) или «отклонено»
--     (человек), и ни к одному бренду не сведён. «любой» в колонке
--     изготовителя — не бренд позиции, и в шапку карточки он не идёт.
--     Нет реестра — не отсеивается ничего (защищать щедро, правило 7).
create or replace function portal_not_brands(cells text[]) returns text[]
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  пары    jsonb;
  ключи   text[];
  плохие  text[];
  хорошие text[];
begin
  if to_regclass('lib_brand_alias') is null or to_regclass('lib_brand_map') is null
     or to_regprocedure('lib_brand_key(text)') is null then
    return '{}';
  end if;
  select coalesce(jsonb_agg(jsonb_build_array(c.cell, lib_brand_key(c.cell))), '[]') into пары
    from (select distinct left(btrim(x), 200) as cell from unnest(cells) x
           where coalesce(btrim(x), '') <> '') c;
  ключи := array(select distinct x ->> 1 from jsonb_array_elements(пары) x where x ->> 1 <> '');
  if cardinality(ключи) = 0 then
    return '{}';
  end if;
  плохие := array(select distinct a.spelling_key from lib_brand_alias a
                   where a.spelling_key = any(ключи) and a.status in ('не бренд', 'отклонено'));
  if cardinality(плохие) = 0 then
    return '{}';
  end if;
  хорошие := array(select m.spelling_key from lib_brand_map m where m.spelling_key = any(плохие));
  return array(select x ->> 0 from jsonb_array_elements(пары) x
                where x ->> 1 = any(плохие) and not (x ->> 1 = any(хорошие)));
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
-- ведёт на КОРЕНЬ цепочки слияний (codes_sql.SUPPLIER_CTES: A → B → C даёт C,
-- не больше двадцати шагов; круг или цепочка длиннее — компания остаётся
-- собой). Ключ без сущности в ответ не идёт: страница ставит на такую строку
-- ссылку в карточку Битрикса с подписью «нет в справочнике поставщиков».
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
  with recursive и as (
    select distinct on (i.value_norm) i.value_norm, i.sup_id
      from sup_identifier i join sup_entity e on e.id = i.sup_id
     where i.kind = 'bitrix' and i.status <> 'rejected' and i.value_norm = any(keys)
     order by i.value_norm, (e.merged_into is not null), e.id
  ), вверх (value_norm, start, id, depth) as (
    select и.value_norm, и.sup_id, и.sup_id, 0 from и
    union all
    select в.value_norm, в.start, e.merged_into, в.depth + 1
      from вверх в join sup_entity e on e.id = в.id
     where e.merged_into is not null and в.depth < 20
  )
  select coalesce(jsonb_object_agg(x.value_norm, x.sup_id), '{}') into карта
    from (select distinct on (в.value_norm) в.value_norm,
                 case when e.merged_into is null then в.id else в.start end as sup_id
            from вверх в join sup_entity e on e.id = в.id
           order by в.value_norm, в.depth desc) x;
  имена := portal_sup_names(array(select distinct v from jsonb_each_text(карта) t(k, v)));
  return (select coalesce(jsonb_object_agg(t.k, имена -> t.v), '{}')
            from jsonb_each_text(карта) t(k, v) where имена ? t.v);
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Написания бренда. Только те, чей ключ ОДНОЗНАЧЕН: lib_brand_map отдаёт
--    ключ написания, сведённый ровно к одному бренду. Написание «Kel», которое
--    один источник сводит к Kelton, а другой к SKF, не засчитывается ни
--    одному: иначе одна строка спроса стояла бы у двух брендов, а карточка
--    кода этой строки бренда бы не показывала. Имя бренда из реестра —
--    написание всегда.
create or replace function portal_brand_keys(b text) returns text[]
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  ключи text[];
begin
  if to_regclass('lib_brand_alias') is null or to_regclass('lib_brand_map') is null
     or coalesce(b, '') = '' then
    return '{}';
  end if;
  ключи := array(select distinct a.spelling_key from lib_brand_alias a
                  where a.brand_key = b and a.status in ('разрешено', 'проверено'));
  return array(select m.spelling_key from lib_brand_map m
                where m.spelling_key = any(ключи) and m.brand_key = b);
end $fn$;

create or replace function portal_brand_spellings(b text) returns text[]
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  ключи text[];
begin
  if to_regclass('lib_brands') is null or to_regclass('lib_brand_alias') is null
     or coalesce(b, '') = '' then
    return '{}';
  end if;
  ключи := portal_brand_keys(b);
  return array(
    select distinct s from (
      select a.spelling as s from lib_brand_alias a
       where a.brand_key = b and a.status in ('разрешено', 'проверено') and a.spelling_key = any(ключи)
      union all
      select x.name from lib_brands x where x.brand_key = b) y
     where coalesce(btrim(s), '') <> '');
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Строки КП по бренду: слово поставщика (дословное однозначное написание,
--    индекс lib_prices_oem), карточка запроса (элемент СП-176 бренда в
--    rfq_brands) и каталог (детали бренда по lib_parts.oem → ключ id и ключ
--    каталожного номера, индекс lib_prices_pn_key). Строка, найденная двумя
--    путями, считается один раз (первым путём). Путь карточки читает строки КП
--    целиком (у rfq_brands индекса нет): десятки тысяч строк, десятки
--    миллисекунд.
--
--    analog — строка есть ответ АНАЛОГОМ на спрос по бренду: поставщик пишет
--    «аналог» или его слово разрешается в ДРУГОЙ бренд реестра (запрос на
--    Kelton, ответ SKF). Такая строка — не «цена по бренду Kelton», и кто её
--    дал, не «давал цену по бренду». Разрешаются 300 самых частых чужих ячеек
--    (ключ написания дорог, шапка файла); слово не из реестра и ячейка за
--    пределом строку не обвиняют (правило 7). Предел — самые СВЕЖИЕ строки.
drop function if exists portal_brand_rows(text, int);
create function portal_brand_rows(b text, lim int default 20000)
returns table (id bigint, code text, written text, rfq_company text, month text, via text, analog boolean)
  language plpgsql stable set plan_cache_mode = force_custom_plan set jit = off as $fn$
#variable_conflict use_column
declare
  предел    int := least(greatest(coalesce(lim, 20000), 1), 50000);
  написания text[];
  элементы  text[] := '{}';
  коды      text[];
begin
  if to_regclass('lib_brands') is null or to_regclass('lib_brand_alias') is null
     or to_regclass('lib_brand_map') is null or coalesce(b, '') = '' then
    return;
  end if;
  написания := portal_brand_spellings(b);
  if to_regclass('lib_brand_sp176') is not null then
    элементы := array(select s.sp176_id::text from lib_brand_sp176 s where s.brand_key = b);
  end if;
  коды := array(select distinct x from lib_parts p
                 cross join lateral unnest(array[p.id, lib_pn_key(p.catalog_no)]) x
                where p.oem = any(написания) and coalesce(x, '') <> '');
  return query
    with z as materialized (
      select distinct on (z0.id) z0.*
        from (
          select p.id, p.part_number, p.rfq_company, p.price_date, p.created_at, p.oem,
                 portal_says_analog(concat_ws(' ', p.item_name, p.note, p.oem)) as says,
                 'слово КП'::text as via, 0 as ord
            from lib_prices p
           where p.feed = 'разбор КП' and p.oem = any(написания)
          union all
          select p.id, p.part_number, p.rfq_company, p.price_date, p.created_at, p.oem,
                 portal_says_analog(concat_ws(' ', p.item_name, p.note, p.oem)), 'карточка запроса', 1
            from lib_prices p
           where p.feed = 'разбор КП' and cardinality(элементы) > 0 and p.rfq_brands is not null
             -- Обрубок в конце (обрезка price_store на 200 знаках) снимается,
             -- как codes_sql.CARD_KEYS: «…,5050» может быть началом «50501».
             and string_to_array(regexp_replace(
                   case when length(p.rfq_brands) >= 200 then regexp_replace(p.rfq_brands, ',[^,]*$', '')
                        else p.rfq_brands end, '\s', '', 'g'), ',') && элементы
          union all
          select p.id, p.part_number, p.rfq_company, p.price_date, p.created_at, p.oem,
                 portal_says_analog(concat_ws(' ', p.item_name, p.note, p.oem)), 'каталог', 2
            from lib_prices p
           where p.feed = 'разбор КП' and cardinality(коды) > 0
             and lib_pn_key(p.part_number) = any(коды)
        ) z0
       where coalesce(btrim(z0.part_number), '') <> ''
       order by z0.id, z0.ord
    ), чужие as materialized (
      -- MATERIALIZED обязателен: без него одноразовый CTE встраивается в запрос,
      -- и stable-функция считалась бы заново на каждую строку z (замер
      -- 25.09.2026: 54 с вместо десятков миллисекунд на 6 тыс. строк).
      select portal_brands_of(array(
               select q.c from (select left(btrim(z.oem), 200) as c, count(*) as n from z
                                 where coalesce(btrim(z.oem), '') <> '' and not (z.oem = any(написания))
                                 group by 1 order by 2 desc, 1 limit 300) q)) as м
    )
    select z.id, lib_pn_key(z.part_number), z.part_number, z.rfq_company, to_char(z.price_date, 'YYYY-MM'), z.via,
           z.says or (z.oem is not null and ч.м ? left(btrim(z.oem), 200)
                      and not exists (select 1 from jsonb_array_elements(ч.м -> left(btrim(z.oem), 200)) y
                                       where y ->> 0 = b))
      from z, чужие ч
     order by z.price_date desc nulls last, z.created_at desc, z.id desc
     limit предел;
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. КАРТОЧКА КОДА.
--
--    Разделы и их источники:
--      код и бренд — написание (каталог, иначе самое частое в спросе и КП),
--                    бренд с источником и признаком спорности (шапка файла);
--      спрос       — lib_demand_live по ключам (не больше 5 000 строк): сделок,
--                    строк, единиц (сумма правдоподобных количеств — только в
--                    одной единице, как в crossref.СПРОС_SQL), последний месяц
--                    ЗАНЕСЕНИЯ строки (даты сделки в базе нет), заказчиков —
--                    null: связи «сделка → заказчик» в базе нет, и страница
--                    говорит «не знаем», а не ноль;
--      предложения — lib_prices «разбор КП» по ключам (не больше 2 000, новые
--                    первыми), оригинал и аналоги двумя списками по 100, с
--                    условиями КП (базис, оплата, сроки) и их источником —
--                    «в КП не указано» и «разбор не дошёл» различаются, как на
--                    /nomenclature; сводка цен по валюте для каждого списка;
--      аналоги     — lib_part_alt детали и обратная связь «этот код — аналог к»;
--      машины, узлы — lib_part_models → lib_models, lib_parts.unit_id → lib_units;
--      кто делает  — lib_part_suppliers детали: роль, наличие у продавца, цена и
--                    срок из записи проверки (слово продавца, как записано);
--      кому писать — поставщики, дававшие цену ОРИГИНАЛА по бренду позиции на
--                    ДРУГИЕ коды и не дававшие на этот (portal_brand_rows).
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
  ключи    text[];            -- ключ адреса и ключи детали каталога (id и номер)
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
  кп_я     jsonb := '{}';    -- ячейка изготовителя КП (не «аналог») → строк
  карточки jsonb := '{}';    -- элемент СП-176 → {n, key, name}
  маски    jsonb := '[]';
  альт_изг jsonb := '{}';    -- изготовитель по каталогу аналогов → строк
  есть_альт boolean;
  все_ячейки text[];
  кп_проверены text[] := '{}';  -- ячейки КП, отправленные в реестр
  ячейки   jsonb := '{}';    -- ячейка → бренды реестра
  не_бренды text[] := '{}';  -- ячейки, которые реестр брендом не считает
  претензии jsonb := '[]';
  бренд    jsonb;
  б_ключ   text;
  б_имя    text;
  спорно   boolean;
  спор_кп  boolean := false; -- бренд позиции называют только КП, и они расходятся
  суждение boolean := false; -- делятся ли строки КП на оригинал и аналог по бренду
  компании jsonb := '{}';
  кп       jsonb;
  аналоги  jsonb := '[]';
  аналог_к jsonb := '[]';
  машины   jsonb := '[]';
  узлы     jsonb := '[]';
  делают   jsonb := '[]';
  делают_n int := 0;
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

  -- Деталь, чей id не равен ключу номера («ht55norm» у HT-55), ищется по обоим
  -- ключам: и по ссылке из каталога, и по номеру карточка одна и та же.
  ключи := array(select distinct x from unnest(array[k, деталь ->> 'id', lib_pn_key(деталь ->> 'catalog_no')]) x
                  where coalesce(x, '') <> '');

  -- ── этот код в каталоге аналогов (lib_part_alt.alt_pn, индекс lib_part_alt_key) ──
  есть_альт := exists (select 1 from lib_part_alt a where lib_pn_key(a.alt_pn) = any(ключи));
  if есть_альт then
    select coalesce(jsonb_object_agg(z.c, z.n), '{}') into альт_изг
      from (select btrim(a.alt_maker) as c, count(*)::int as n from lib_part_alt a
             where lib_pn_key(a.alt_pn) = any(ключи) and coalesce(btrim(a.alt_maker), '') <> ''
             group by 1 order by 2 desc, 1 limit 10) z;
  end if;

  -- ── спрос ──
  with d as materialized (
    select d.deal_id, d.part_number, d.item_name, d.oem, d.qty, d.unit, d.created_at
      from lib_demand_live d where lib_pn_key(d.part_number) = any(ключи) limit 5000)
  select jsonb_build_object(
           'rows', count(*), 'deals', count(distinct d.deal_id),
           'units', count(distinct nullif(btrim(d.unit), '')),
           -- Одно правило количества на всю карточку: portal_qty без цены и
           -- суммы — это предел в миллион, а не второй литерал рядом.
           'qty', case when count(distinct nullif(btrim(d.unit), '')) <= 1
                       then sum(portal_qty(d.qty, null, null)) end,
           'unit', case when count(distinct nullif(btrim(d.unit), '')) = 1
                        then min(nullif(btrim(d.unit), '')) end,
           'qty_hidden', count(*) filter (where d.qty is not null and portal_qty(d.qty, null, null) is null),
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
               nullif(btrim(p.basis), '') as basis, p.basis_src,
               p.lead_days, p.lead_src, p.make_days, p.make_src,
               nullif(btrim(p.pay_terms), '') as pay_terms, p.pay_advance_pct, p.pay_src,
               to_char(p.price_date, 'YYYY-MM') as month, p.price_date_src as month_src,
               portal_says_analog(concat_ws(' ', p.item_name, p.note, p.oem)) as says_analog
          from lib_prices p
         where p.feed = 'разбор КП' and lib_pn_key(p.part_number) = any(ключи)
         order by p.price_date desc nulls last, p.created_at desc, p.id desc
         limit 2000) x;
    -- Слово строки «аналог SKF» — о предложении, не о позиции: в претензии
    -- на бренд позиции такие строки не идут.
    select coalesce(jsonb_object_agg(z.c, z.n), '{}') into кп_я
      from (select btrim(r ->> 'oem') as c, count(*)::int as n from jsonb_array_elements(строки) r
             where coalesce(btrim(r ->> 'oem'), '') <> '' and not (r ->> 'says_analog')::boolean
             group by 1 order by 2 desc, 1 limit 30) z;
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

  -- ── бренды реестра по всем ячейкам разом; ячейки «не бренд» ──
  кп_проверены := array(select q.c from (select left(btrim(r ->> 'oem'), 200) as c, count(*) as n
                                           from jsonb_array_elements(строки) r
                                          where coalesce(btrim(r ->> 'oem'), '') <> ''
                                          group by 1 order by 2 desc, 1 limit 200) q);
  все_ячейки := array(
    select деталь ->> 'oem'
    union all select jsonb_object_keys(спрос_я)
    union all select jsonb_object_keys(кп_я)
    union all select jsonb_object_keys(альт_изг)
    union all select jsonb_array_elements_text(маски)
    -- Ячейки строк КП — 200 самых частых: у одного кода разных написаний
    -- изготовителя десятки, а ключ написания дорог (шапка файла). Строка с
    -- ячейкой за пределом не обвиняется и помечается «не проверено».
    union all select unnest(кп_проверены));
  if реестр then
    ячейки := portal_brands_of(все_ячейки);
    не_бренды := portal_not_brands(все_ячейки);
  end if;
  -- Ячейка, равная самому коду, — столкновение колонок шапки, а не бренд.
  не_бренды := не_бренды || array(select distinct left(btrim(c), 200) from unnest(все_ячейки) c
                                   where coalesce(btrim(c), '') <> '' and lib_pn_key(c) = any(ключи));

  -- ── претензии на бренд позиции ──
  with ячейки_и as (
    select 'каталог'::text as src, 0 as ord, left(btrim(деталь ->> 'oem'), 200) as cell, 1 as n
     where coalesce(btrim(деталь ->> 'oem'), '') <> ''
    union all select 'каталог аналогов', 1, left(btrim(e.key), 200), e.value::int from jsonb_each(альт_изг) e
    union all select 'спецификация', 2, left(btrim(e.key), 200), e.value::int from jsonb_each(спрос_я) e
    union all select 'маска кода', 4, left(btrim(m), 200), 1 from jsonb_array_elements_text(маски) m
    union all select 'КП', 5, left(btrim(e.key), 200), e.value::int from jsonb_each(кп_я) e
  ), чистые as (
    select и.* from ячейки_и и where not (и.cell = any(не_бренды))
  ), претензия as (
    select и.ord, b ->> 0 as key, b ->> 1 as name, и.n
      from чистые и cross join lateral jsonb_array_elements(ячейки -> и.cell) b
     where ячейки ? и.cell
    union all
    select и.ord, null, и.cell, и.n from чистые и where not (ячейки ? и.cell)
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
  -- Бренд позиции назвали только КП, и они расходятся: делить строки по бренду
  -- значило бы выбрать оригинал голосованием поставщиков. Не делим.
  спор_кп := б_ключ is not null and (бренд ->> 'best')::int = 5 and coalesce(спорно, false);
  суждение := б_ключ is not null and not спор_кп;

  -- ── предложения: оригинал и аналоги ──
  if строки <> '[]' then
    компании := portal_companies(array(select distinct r ->> 'rfq_company' from jsonb_array_elements(строки) r
                                        where r ->> 'rfq_company' is not null));
    with r as (
      select x.*,
             case when coalesce(btrim(x.oem), '') = '' then null
                  when left(btrim(x.oem), 200) = any(не_бренды) then null
                  else left(btrim(x.oem), 200) end as cell
        from jsonb_to_recordset(строки) as x(ord int, written text, oem text, rfq_company text, rfq_id text,
                                             price numeric, currency text, qty numeric, qty_hidden boolean,
                                             unit text, total numeric, basis text, basis_src text,
                                             lead_days int, lead_src text, make_days int, make_src text,
                                             pay_terms text, pay_advance_pct int, pay_src text,
                                             month text, month_src text, says_analog boolean)
    ), r2 as (
      select r.*, ячейки -> r.cell as row_brands,
             -- Компания — корень реестра; несведённая — ключом портала.
             case when r.rfq_company is null then null
                  else coalesce(компании -> r.rfq_company ->> 'id', 'bx:' || r.rfq_company) end as cid
        from r
    ), r3 as (
      select r2.*,
             -- В ячейке «SKF, FAG» у позиции SKF показывается SKF, а не
             -- алфавитно первый.
             coalesce((select y from jsonb_array_elements(r2.row_brands) y where y ->> 0 = б_ключ limit 1),
                      r2.row_brands -> 0) as rb
        from r2
    ), o as (
      select r3.*,
             case when суждение and r3.row_brands is not null
                       and not exists (select 1 from jsonb_array_elements(r3.row_brands) y where y ->> 0 = б_ключ)
                  then 'бренд строки — ' || (r3.rb ->> 1) || ', у позиции — ' || б_имя
                  when r3.says_analog then 'поставщик пишет «аналог»' end as why,
             case when суждение and r3.row_brands is null
                  then case when r3.cell is null then 'бренд в КП не назван'
                            when not (r3.cell = any(кп_проверены))
                            then 'в КП «' || left(r3.cell, 60) || '» — по реестру не проверено'
                            else 'в КП «' || left(r3.cell, 60) || '» — такого бренда в реестре нет' end end as unconfirmed,
             jsonb_build_object(
               'company', компании -> r3.rfq_company,
               -- Компании нет в справочнике: номер карточки Битрикса — только
               -- для ссылки «карточка в Битриксе ↗», в тексте страницы его нет.
               'bx', case when r3.rfq_company ~ '^[0-9]{1,18}$' and not (компании ? r3.rfq_company)
                          then r3.rfq_company end,
               'unresolved', r3.rfq_company is not null and not (компании ? r3.rfq_company),
               'brand', case when r3.rb is not null
                             then jsonb_build_object('key', r3.rb ->> 0, 'name', r3.rb ->> 1)
                             when r3.cell is not null
                             then jsonb_build_object('key', null, 'name', left(r3.cell, 120)) end,
               'written', r3.written, 'price', r3.price, 'currency', r3.currency, 'qty', r3.qty,
               'qty_hidden', r3.qty_hidden, 'unit', r3.unit,
               -- Количество не читается — сумма тоже под вопросом: какое из двух
               -- чисел верно, не знаем. Поэтому её нет, а есть признак.
               'total', case when not r3.qty_hidden then r3.total end,
               'total_hidden', r3.qty_hidden and r3.total is not null,
               'basis', r3.basis, 'basis_src', r3.basis_src,
               'lead_days', r3.lead_days, 'lead_src', r3.lead_src,
               'make_days', r3.make_days, 'make_src', r3.make_src,
               'pay_terms', r3.pay_terms, 'pay_advance_pct', r3.pay_advance_pct, 'pay_src', r3.pay_src,
               'month', r3.month, 'month_src', r3.month_src,
               -- Номер карточки запроса (СП-166) — для ссылки на первоисточник цены.
               'rfq', case when r3.rfq_id ~ '^[0-9]{1,18}$' then r3.rfq_id end) as j
        from r3
    )
    select jsonb_build_object(
             'rows', count(*), 'suppliers', count(distinct o.cid), 'cards', count(distinct o.rfq_id),
             'capped', count(*) >= 2000, 'brand_judged', суждение, 'brand_disputed', спор_кп,
             'original', coalesce((select jsonb_agg(x.j || jsonb_build_object('unconfirmed', x.unconfirmed) order by x.ord)
                                     from (select * from o where o.why is null order by o.ord limit 100) x), '[]'),
             'analog', coalesce((select jsonb_agg(x.j || jsonb_build_object('why', x.why) order by x.ord)
                                   from (select * from o where o.why is not null order by o.ord limit 100) x), '[]'),
             'original_n', count(*) filter (where o.why is null),
             'analog_n', count(*) filter (where o.why is not null),
             'unconfirmed_n', count(*) filter (where o.why is null and o.unconfirmed is not null),
             -- Ориентир цены: по каждой валюте отдельно (пересчёта по курсу нет
             -- намеренно) — последняя, разброс, сколько компаний.
             'prices', coalesce((
               select jsonb_agg(jsonb_build_object(
                        'group', z.g, 'currency', z.cur, 'rows', z.n, 'companies', z.nc,
                        'min', z.mn, 'max', z.mx,
                        'last', jsonb_build_object('price', z.lp, 'month', z.lm,
                                                   'company', z.lc -> 'company', 'bx', z.lc -> 'bx'))
                      order by z.g desc, z.n desc, z.cur)
                 from (select case when x.why is null then 'original' else 'analog' end as g, x.currency as cur,
                              count(*)::int as n, count(distinct x.cid)::int as nc,
                              min(x.price) as mn, max(x.price) as mx,
                              (array_agg(x.price order by x.ord))[1] as lp,
                              (array_agg(x.month order by x.ord))[1] as lm,
                              (array_agg(x.j order by x.ord))[1] as lc
                         from o x where x.price is not null group by 1, 2) z), '[]'))
      into кп
      from o;
  else
    кп := jsonb_build_object('rows', 0, 'suppliers', 0, 'cards', 0, 'capped', false,
                             'brand_judged', суждение, 'brand_disputed', спор_кп,
                             'original', '[]'::jsonb, 'analog', '[]'::jsonb,
                             'original_n', 0, 'analog_n', 0, 'unconfirmed_n', 0, 'prices', '[]'::jsonb);
  end if;

  -- ── аналоги по каталогу, машины, узлы, кто делает ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'аналоги'::text || 'машины'::text || 'кто делает'::text;
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
                                                   'segment', m.segment_id, 'segment_name', s.name,
                                                   'maker', nullif(btrim(m.oem), ''))
                                order by m.name), '[]')
        into машины
        from (select m.* from lib_part_models pm join lib_models m on m.id = pm.model_id
               where pm.part_id = деталь ->> 'id' order by m.name limit 50) m
        left join lib_segments s on s.id = m.segment_id;
      select coalesce(jsonb_agg(jsonb_build_object('id', u.id, 'name', u.name, 'parent', р.name,
                                                   'crit', u.crit)), '[]')
        into узлы
        from lib_units u left join lib_units р on р.id = u.parent_id
       where u.id = деталь ->> 'unit_id';
      -- Кто делает деталь и у кого проверено наличие. Слово продавца и срок —
      -- как записаны проверкой; контактов нет (их в выборке нет вовсе).
      select count(*)::int into делают_n from lib_part_suppliers ps where ps.part_id = деталь ->> 'id';
      select coalesce(jsonb_agg(jsonb_build_object(
               'name', x.name, 'role', nullif(btrim(x.role), ''), 'country', nullif(btrim(x.country), ''),
               'makes', left(nullif(btrim(x.makes), ''), 300), 'verdict', nullif(btrim(x.verdict), ''),
               'in_stock', nullif(btrim(x.in_stock), ''), 'stock_qty', nullif(btrim(x.stock_qty), ''),
               'lead_time', nullif(btrim(x.lead_time), ''), 'price', x.price,
               'currency', case when x.price is not null then nullif(btrim(x.currency), '') end)
             order by x.n), '[]')
        into делают
        from (select ps.makes, ps.verdict, ps.in_stock, ps.stock_qty, ps.lead_time, ps.price, ps.currency,
                     s.name, s.kind as role, s.country,
                     row_number() over (order by (ps.verdict is null), (ps.in_stock is distinct from 'yes'),
                                                 s.name, s.id) as n
                from lib_part_suppliers ps join lib_suppliers s on s.id = ps.supplier_id
               where ps.part_id = деталь ->> 'id'
               order by n limit 30) x;
    end if;
    -- Этот код назван аналогом (номером изготовителя, заменой) к детали каталога.
    select coalesce(jsonb_agg(jsonb_build_object('code', x.id, 'written', x.catalog_no, 'kind', x.kind,
                                                 'maker', nullif(btrim(x.oem), ''))
                              order by x.kind, x.catalog_no), '[]')
      into аналог_к
      from (select a.kind, p.id, p.catalog_no, p.oem from lib_part_alt a join lib_parts p on p.id = a.part_id
             where lib_pn_key(a.alt_pn) = any(ключи) and not (p.id = any(ключи))
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

  -- ── кому ещё писать: давали цену ОРИГИНАЛА по бренду позиции, но не по
  --    этому коду. Бренд позиции не установлен (спор КП) — подбирать не по чему.
  if суждение then
    if clock_timestamp() - statement_timestamp() > бюджет then
      усечено := усечено || 'кому ещё писать'::text;
    else
      -- Группа — компания реестра, а не ключ портала: у слитой компании ключей
      -- несколько, и она не должна встать в список дважды или «новой».
      with р as materialized (
        select r.rfq_company, r.code, r.month
          from portal_brand_rows(б_ключ, 20000) r where r.rfq_company is not null and not r.analog
      ), к as materialized (
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
          from р, к where к.м ? р.rfq_company and not (р.code = any(ключи))
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
    'makers', делают,
    'makers_n', делают_n,
    'write_to', кому,
    'registry', реестр,
    'partial', to_jsonb(усечено));
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. КАРТОЧКА БРЕНДА. Без реестра брендов карточки нет: ответ {"registry":
--    false}, и страница говорит, что реестр не установлен.
--
--    спрос        — lib_demand по однозначным дословным написаниям бренда
--                   (индекс lib_demand_oem), не больше 20 000 строк; пометки
--                   lib_row_junk снимаются по индексу для прочитанных строк, а
--                   не анти-соединением со всей таблицей пометок; кодов — только
--                   правдоподобных, как в списках; рядом — строк по засеву
--                   реестра (lib_brand_alias.n_rows источника lib_demand.oem):
--                   в них ячейки из нескольких брендов тоже;
--    коды         — топ-25 по спросу (сделок) и по предложениям оригинала
--                   (строк КП), только правдоподобные; каталог — число деталей
--                   и 25 из них;
--    машины       — lib_models, чей изготовитель разрешается в этот бренд;
--    поставщики   — давали цену оригинала по бренду (portal_brand_rows), топ-30;
--                   ответы аналогом на спрос по бренду — только числом;
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
  ключи_бр text[];
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
  ключи_бр := portal_brand_keys(б);
  написания := portal_brand_spellings(б);

  -- ── спрос ──
  with d0 as materialized (
    select d.id, lib_pn_key(d.part_number) as code, d.part_number, d.deal_id
      from lib_demand d where d.oem = any(написания) limit 20000
  ), мусор as materialized (
    -- Пометки — только прочитанных строк, по индексу первичного ключа: хеш по
    -- всем действующим пометкам (~300 тыс.) при малом work_mem ушёл бы в
    -- файлы.
    select j.demand_id from lib_row_junk j
     where j.revoked_at is null and j.demand_id = any(array(select d0.id from d0))
  ), d as materialized (
    select d0.* from d0 where not exists (select 1 from мусор m where m.demand_id = d0.id)
  )
  select jsonb_build_object('rows', count(*), 'deals', count(distinct d.deal_id),
                            'codes', cardinality(portal_codes_ok(array(
                                       select distinct x.code from d x where char_length(x.code) >= 2))),
                            'capped', (select count(*) from d0) >= 20000),
         (select coalesce(jsonb_agg(jsonb_build_object('code', z.code, 'written', z.written,
                                                       'deals', z.deals, 'rows', z.rows)
                                    order by z.deals desc, z.rows desc, z.code), '[]')
            from (select x.code, mode() within group (order by x.part_number) as written,
                         count(distinct x.deal_id)::int as deals, count(*)::int as rows
                    from d x where char_length(x.code) >= 2 group by x.code
                   order by 3 desc, 4 desc, 1 limit 100) z)
    into спрос, по_спросу
    from d;
  спрос := спрос || jsonb_build_object('registry_rows',
    (select coalesce(sum(a.n_rows), 0) from lib_brand_alias a
      where a.brand_key = б and a.status in ('разрешено', 'проверено') and a.source = 'lib_demand.oem'
        and a.spelling_key = any(ключи_бр)));

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
    with р0 as materialized (select * from portal_brand_rows(б, 20000)),
         р as (select * from р0 where not р0.analog),
         -- MATERIALIZED обязателен: к упомянут во FROM один раз, и без него
         -- планировщик встраивает его, а portal_companies считается заново на
         -- каждую строку каждого подзапроса (замер 25.09.2026: 41 с вместо
         -- десятков миллисекунд).
         к as materialized (select portal_companies(array(select distinct р.rfq_company from р
                                                           where р.rfq_company is not null)) as м)
    select jsonb_build_object('rows', (select count(*) from р), 'capped', (select count(*) from р0) >= 20000,
                              'analog_rows', (select count(*) from р0 where р0.analog),
                              'suppliers', (select count(distinct к.м -> x.rfq_company ->> 'id') from р x
                                             where к.м ? x.rfq_company),
                              'rows_unresolved', (select count(*) from р x where not (к.м ? coalesce(x.rfq_company, '')))),
           (select coalesce(jsonb_agg(jsonb_build_object('code', z.code, 'written', z.written, 'rows', z.rows,
                                                         'suppliers', z.sups, 'last_month', z.last_month)
                                      order by z.rows desc, z.code), '[]')
              from (select x.code, mode() within group (order by x.written) as written, count(*)::int as rows,
                           -- Поставщик — компания реестра, несведённая — ключом портала.
                           count(distinct case when x.rfq_company is not null
                                               then coalesce(к.м -> x.rfq_company ->> 'id', 'bx:' || x.rfq_company) end)::int as sups,
                           max(x.month) as last_month
                      from р x where char_length(x.code) >= 2 group by x.code
                     order by 3 desc, 1 limit 100) z),
           -- Группа — компания реестра: у слитой ключей портала несколько.
           -- Несведённые с реестром в список не идут (имени нет), но в числе
           -- строк и поставщиков выше они есть.
           (select coalesce(jsonb_agg(jsonb_build_object('company', z.company, 'codes', z.codes,
                                                         'rows', z.rows, 'last_month', z.last_month)
                                      order by z.codes desc, z.rows desc, z.cid), '[]')
              from (select к.м -> x.rfq_company ->> 'id' as cid, (array_agg(к.м -> x.rfq_company))[1] as company,
                           count(distinct x.code)::int as codes, count(*)::int as rows, max(x.month) as last_month
                      from р x where к.м ? x.rfq_company group by 1
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
                                                 'segment', m.segment_id, 'segment_name', s.name,
                                                 'parts', (select count(*) from lib_part_models pm where pm.model_id = m.id))
                              order by m.name), '[]')
      into машины
      from lib_models m left join lib_segments s on s.id = m.segment_id
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
           and a.spelling_key = any(ключи_бр)
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
-- 10. КАРТОЧКА ПОСТАВЩИКА. Как suppliersCut у воркера: ни контактов людей, ни
--    финансовых условий (оплата, аванс, договор) — только кто это и что он
--    котировал. Цена из КП — его предложение нам, она показывается (решение в
--    воркере, SUPPLIERS_FIELDS).
--
--    кто          — показанное имя с источником, ИНН (признаки и реквизиты
--                   Битрикса), домены, страна, город, номер KV-S (если выдан),
--                   карточки Битрикса (номера companyId — только для ссылки);
--    члены        — корень цепочки слияний и ВСЕ слитые в него по цепочке
--                   (не больше двадцати шагов, как codes_sql.SUPPLIER_CTES);
--    запросы и КП — факт rfq_stats (sup_fact), если он посчитан; строки КП по
--                   ключам портала (индекс lib_prices_rfqco), не больше 5 000;
--    бренды       — только то, что назвал САМ поставщик (слово КП → реестр);
--                   строки, где он бренд не назвал, считаются отдельно —
--                   брендом запроса или каталога: «что мы спрашивали» и «что он
--                   предлагает» — разные утверждения;
--    коды         — последнее предложение по коду, топ-100 по свежести, с
--                   источником бренда и пометкой «оригинал / аналог»: по бренду
--                   запроса или каталога против названного поставщиком.
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
  ячейки_с text[];
  ячейки   jsonb := '{}';
  не_бренды text[] := '{}';
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
  -- Слитая ведёт на корень цепочки слияний (не больше двадцати шагов:
  -- цепочка длиннее или круг — ошибка сведения, и ходить по кругу нельзя).
  for шаг in 1 .. 20 loop
    select e.* into сущность from sup_entity e where e.id = номер;
    exit when not found or сущность.merged_into is null;
    исходный := coalesce(исходный, номер);
    номер := сущность.merged_into;
  end loop;
  if сущность.id is null or сущность.merged_into is not null or сущность.resolution = 'merged' then
    return null;
  end if;
  -- Признаки слитых в неё сущностей — тоже её, по всей цепочке.
  члены := array(
    with recursive вниз (id, depth) as (
      select номер, 0
      union all
      select e.id, в.depth + 1 from sup_entity e join вниз в on e.merged_into = в.id where в.depth < 20)
    select distinct вниз.id from вниз);
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
                   lib_pn_key(p.part_number) as code, p.part_number as written, p.oem,
                   -- Обрубок в конце (обрезка на 200 знаках) — не элемент СП-176.
                   case when length(p.rfq_brands) >= 200 then regexp_replace(p.rfq_brands, ',[^,]*$', '')
                        else p.rfq_brands end as rfq_brands,
                   p.rfq_id, p.price, p.currency, portal_qty(p.qty, p.price, p.total) as qty,
                   nullif(btrim(p.qty_unit), '') as unit, to_char(p.price_date, 'YYYY-MM') as month,
                   portal_says_analog(concat_ws(' ', p.item_name, p.note, p.oem)) as says_analog
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

  -- ── бренд строки: названный поставщиком (слово КП → реестр) и спрошенный
  --    (карточка запроса, иначе каталог) — порознь ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'бренды'::text || 'коды'::text;
  else
    if реестр and строки <> '[]' then
      -- Разрешаются 300 самых частых ячеек: ключ написания дорог (шапка файла).
      ячейки_с := array(
        select btrim(r ->> 'oem') from jsonb_array_elements(строки) r
         where coalesce(btrim(r ->> 'oem'), '') <> '' group by 1 order by count(*) desc, 1 limit 300);
      ячейки := portal_brands_of(ячейки_с);
      не_бренды := portal_not_brands(ячейки_с);
      if to_regclass('lib_brand_sp176') is not null then
        select coalesce(jsonb_object_agg(s.sp176_id::text, jsonb_build_array(b.brand_key, b.name)), '{}')
          into карточки
          from lib_brand_sp176 s join lib_brands b on b.brand_key = s.brand_key
         where s.sp176_id::text = any(array(
           select distinct btrim(x) from jsonb_array_elements(строки) r
            cross join lateral unnest(string_to_array(r ->> 'rfq_brands', ',')) x
            where btrim(x) ~ '^[0-9]{1,18}$'));
      end if;
      -- Каталог — по id детали и по ключу её номера (id бывает не равен ключу).
      select coalesce(jsonb_object_agg(z.code, jsonb_build_array(z.brand_key, z.name)), '{}') into каталог
        from (select distinct on (x.code) x.code, b.brand_key, b.name
                from (select p.id as code, p.oem from lib_parts p where p.id = any(годные)
                      union all
                      select lib_pn_key(p.catalog_no), p.oem from lib_parts p
                       where lib_pn_key(p.catalog_no) in (select unnest(годные))) x
                join lib_brand_map m on m.spelling_key = lib_brand_key(x.oem)
                join lib_brands b on b.brand_key = m.brand_key
               where coalesce(btrim(x.oem), '') <> ''
               order by x.code, b.brand_key) z;
    end if;
    with r as (
      select x.*, n,
             case when coalesce(btrim(x.oem), '') = '' then null
                  when left(btrim(x.oem), 200) = any(не_бренды) then null
                  else left(btrim(x.oem), 200) end as cell,
             (select карточки -> btrim(y) from unnest(string_to_array(x.rfq_brands, ',')) y
               where карточки ? btrim(y) limit 1) as asked_card,
             каталог -> x.code as asked_cat
        from jsonb_array_elements(строки) with ordinality as t(j, n)
        cross join lateral jsonb_to_record(j) as x(code text, written text, oem text, rfq_brands text,
                                                   price numeric, currency text, qty numeric, unit text,
                                                   month text, says_analog boolean)
       where x.code = any(годные)
    ), r2 as (
      select r.*, coalesce(r.asked_card, r.asked_cat) as asked, ячейки -> r.cell as named_list
        from r
    ), r3 as (
      select r2.*,
             case when r2.named_list is not null
                  then coalesce((select y from jsonb_array_elements(r2.named_list) y
                                  where y ->> 0 = r2.asked ->> 0 limit 1), r2.named_list -> 0) end as named
        from r2
    ), r4 as (
      select r3.*,
             -- Бренд строки: названный поставщиком (реестр, иначе его слово), и
             -- лишь когда он не назвал ничего — бренд запроса или каталога.
             case when r3.named is not null then jsonb_build_object('key', r3.named ->> 0, 'name', r3.named ->> 1)
                  when r3.cell is not null then jsonb_build_object('key', null, 'name', left(r3.cell, 120))
                  when r3.asked is not null then jsonb_build_object('key', r3.asked ->> 0, 'name', r3.asked ->> 1)
             end as brand,
             case when r3.cell is not null then 'назвал поставщик'
                  when r3.asked_card is not null then 'бренд запроса'
                  when r3.asked_cat is not null then 'по каталогу' end as brand_src,
             case when r3.says_analog then 'аналог'
                  when r3.named is not null and r3.asked is not null and r3.named ->> 0 <> r3.asked ->> 0 then 'аналог'
                  when r3.named is not null and r3.asked is not null then 'оригинал' end as verdict,
             case when r3.says_analog then 'поставщик пишет «аналог»'
                  when r3.named is not null and r3.asked is not null and r3.named ->> 0 <> r3.asked ->> 0
                  then 'назвал ' || (r3.named ->> 1) || ', а '
                       || case when r3.asked_card is not null then 'спрашивали ' else 'по каталогу — ' end
                       || (r3.asked ->> 1) end as why
        from r3
    ), б as (
      -- Бренды поставщика: названное им самим; не названное — отдельным счётом
      -- по бренду запроса или каталога.
      select coalesce(r4.brand ->> 'key', 'слово:' || lower(r4.brand ->> 'name')) as id,
             min(r4.brand ->> 'key') as key, min(r4.brand ->> 'name') as name,
             count(distinct r4.code) filter (where r4.brand_src = 'назвал поставщик')::int as named_codes,
             count(distinct r4.code) filter (where r4.brand_src <> 'назвал поставщик')::int as asked_codes,
             count(*)::int as rows, max(r4.month) as last_month
        from r4 where r4.brand is not null
       group by 1
    ), к as (
      select distinct on (r4.code) r4.code, r4.written, r4.brand, r4.brand_src, r4.verdict, r4.why,
             r4.price, r4.currency, r4.qty, r4.unit, r4.month, r4.n,
             count(*) over (partition by r4.code) as offers
        from r4 order by r4.code, r4.n
    )
    select (select coalesce(jsonb_agg(jsonb_build_object('brand', jsonb_build_object('key', б.key, 'name', б.name),
                                                         'named_codes', б.named_codes, 'asked_codes', б.asked_codes,
                                                         'rows', б.rows, 'last_month', б.last_month)
                                      order by б.named_codes desc, б.key is null, б.asked_codes desc, б.rows desc, б.name), '[]')
              from (select * from б order by б.named_codes desc, б.key is null, б.asked_codes desc, б.rows desc, б.name
                     limit 40) б),
           (select coalesce(jsonb_agg(jsonb_build_object(
                     'code', к.code, 'written', к.written, 'brand', к.brand, 'brand_src', к.brand_src,
                     'verdict', к.verdict, 'why', к.why,
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
-- 11. МАШИНА И УЗЕЛ — шаг 3 плана «одна стартовая страница» (25.09.2026).
--     Первые два звена цепочки портала (CLAUDE.md, «Куда мы идём»): по машине
--     выйти на узлы и детали, по узлу — на машины, детали, признаки, дефекты и
--     ремонт. Мерило шага — все машины lib_models и все узлы lib_units
--     открываются карточкой (на живой базе 132 и 126).
--
--     КАК УЗЕЛ СВЯЗАН С МАШИНОЙ. Колонки «машина → узел» в базе нет, и это не
--     упущение (schema.sql, 2в): узлы ГТУ общие для Solar и Siemens, поэтому
--     дерево одно на все машины направления. Связь двух видов, и карточка
--     показывает их порознь, не складывая:
--       · ПО ДЕТАЛЯМ — измерено: lib_part_models (машина → деталь) →
--         lib_parts.unit_id (деталь → узел). «У машины N деталей в узле X»;
--         деталь без узла — отдельным числом, а не молчанием;
--       · ТИПОВОЕ ДЕРЕВО НАПРАВЛЕНИЯ — lib_units двумя деревьями
--         (load_equipment.build_units): ГПУ — под префиксом «gpu.», остальное —
--         ГТУ (8 систем номенклатуры ЗИП и 6 разметки партномеров, 14 корней).
--         Направление машины — её сегмент (gtu, gpu), без сегмента — семейство
--         справочника моделей (portal_model_dir). У горно-шахтного и прочего
--         оборудования типового дерева в библиотеке нет, и карточка так и
--         говорит, а не подставляет чужое.
--     Признаки, дефекты и ремонтные операции привязаны к узлу (unit_id) и
--     достаются машине по её узлам — обоим видам связи и их предкам. Дефект,
--     записанный для ДРУГОЙ машины (lib_defects.model), к этой не идёт;
--     операция другого семейства (lib_procedures.model_family) — тоже.
--
--     ДЕТАЛЬ — ТОЛЬКО ПАРОЙ «КОД + БРЕНД» (распоряжение владельца): бренд —
--     изготовитель по каталогу (lib_parts.oem), приведённый к реестру брендов
--     тем же portal_brands_of, что у карточки кода; не разрешился — словом.
--     Код каталога — код всегда (каталог защищает, как в portal_codes_ok).
--     Списки ограничены пределом, и рядом всегда общее число: молчаливого
--     усечения нет.
--
--     ВРЕМЯ. Таблицы здесь малые (машин 132, узлов 126, деталей 13 тыс., связей
--     10 тыс.), но порядок тот же, что у соседних карточек: между разделами —
--     бюджет portal_entity.budget_ms, и несчитанный раздел идёт в "partial".
--     Номеров сделок и файлов (lib_defects.deal_id, source_file) в ответе нет.

-- Направление машины: сегмент, без сегмента — семейство справочника моделей
-- (load_equipment.build_models: sgt, finspong, heavy, solar, ansaldo — ГТУ;
-- gpu — ГПУ). Одно выражение без FROM — встраивается в запрос.
create or replace function portal_model_dir(segment text, family text) returns text
  language sql immutable parallel safe as $$
    select case when segment = 'gpu' or (segment is null and family = 'gpu') then 'gpu'
                when segment = 'gtu' or (segment is null and family in ('sgt', 'finspong', 'heavy', 'solar', 'ansaldo'))
                then 'gtu' end
  $$;

-- Дерево узла: «gpu.» — ГПУ, остальное — ГТУ (load_equipment.build_units).
create or replace function portal_unit_dir(unit_id text) returns text
  language sql immutable parallel safe as $$
    select case when coalesce(unit_id, '') = '' then null when left(unit_id, 4) = 'gpu.' then 'gpu' else 'gtu' end
  $$;

-- Текст справочника в карточку — целиком до предела, а дальше с видимым «…»:
-- обрезка без знака выдавала бы кусок за всё.
create or replace function portal_cut(t text, n int) returns text
  language sql immutable parallel safe as $$
    select case when t is null or btrim(t) = '' then null
                when char_length(t) > n then rtrim(left(t, n)) || '…' else t end
  $$;

-- Узлы и все вложенные / узлы и все предки. Глубина — не больше десяти:
-- круг в parent_id — ошибка справочника, и ходить по нему нельзя.
create or replace function portal_unit_down(ids text[]) returns text[]
  language plpgsql stable as $fn$
begin
  return array(
    with recursive вниз (id, depth) as (
      select u.id, 0 from lib_units u where u.id = any(ids)
      union all
      select c.id, в.depth + 1 from lib_units c join вниз в on c.parent_id = в.id where в.depth < 10)
    select distinct вниз.id from вниз);
end $fn$;

create or replace function portal_unit_up(ids text[]) returns text[]
  language plpgsql stable as $fn$
begin
  return array(
    with recursive вверх (id, parent_id, depth) as (
      select u.id, u.parent_id, 0 from lib_units u where u.id = any(ids)
      union all
      select p.id, p.parent_id, в.depth + 1 from lib_units p join вверх в on p.id = в.parent_id where в.depth < 10)
    select distinct вверх.id from вверх);
end $fn$;

-- Детали списком: код, бренд (соседним полем), наименование, узел, наш номер.
-- Порядок — сначала с нашим номером KV (их знают склад и сорсер), потом по
-- потребности из сводки партномеров (lib_parts.qty_demand), потом по номеру.
create or replace function portal_part_list(ids text[], lim int) returns jsonb
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  список jsonb;
  бренды jsonb := '{}';
begin
  select coalesce(jsonb_agg(to_jsonb(x) order by x.n), '[]') into список
    from (select p.id, p.catalog_no, left(p.name, 200) as name, nullif(left(btrim(p.oem), 200), '') as oem,
                 p.kv_no, p.unit_id, u.name as unit_name,
                 row_number() over (order by (p.kv_no is null), p.qty_demand desc nulls last, p.catalog_no, p.id) as n
            from lib_parts p left join lib_units u on u.id = p.unit_id
           where p.id = any(ids)
           order by n limit least(greatest(coalesce(lim, 100), 1), 500)) x;
  if to_regclass('lib_brands') is not null and to_regclass('lib_brand_map') is not null
     and to_regprocedure('lib_brand_key(text)') is not null then
    бренды := portal_brands_of(array(select distinct x ->> 'oem' from jsonb_array_elements(список) x
                                      where x ->> 'oem' is not null));
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object(
            'code', x ->> 'id', 'written', x ->> 'catalog_no', 'name', x ->> 'name', 'kv_no', x ->> 'kv_no',
            'brand', case when бренды ? (x ->> 'oem')
                          then jsonb_build_object('key', бренды -> (x ->> 'oem') -> 0 ->> 0,
                                                  'name', бренды -> (x ->> 'oem') -> 0 ->> 1)
                          when x ->> 'oem' is not null
                          then jsonb_build_object('key', null, 'name', left(x ->> 'oem', 120)) end,
            'unit', case when x ->> 'unit_id' is not null
                         then jsonb_build_object('id', x ->> 'unit_id', 'name', x ->> 'unit_name') end)
          order by (x ->> 'n')::int), '[]')
            from jsonb_array_elements(список) x);
end $fn$;

-- Признаки узлов: что меряют, что обычно значит, чем подтвердить; связанные
-- дефекты (lib_symptom_defects) и операции подтверждения (lib_symptom_ops).
-- Справочник — заготовка по общей практике: уверенность отдаётся как есть.
create or replace function portal_symptoms_of(units text[], lim int) returns jsonb
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  выход jsonb;
begin
  with s as materialized (
    select s.*, u.name as unit_name, row_number() over (order by u.name, s.name, s.id) as n
      from lib_symptoms s left join lib_units u on u.id = s.unit_id
     where s.unit_id = any(units))
  select jsonb_build_object('n', (select count(*) from s),
    'list', coalesce((select jsonb_agg(jsonb_build_object(
        'name', x.name,
        'unit', case when x.unit_id is not null then jsonb_build_object('id', x.unit_id, 'name', x.unit_name) end,
        'measure', portal_cut(x.measure, 600), 'defect', portal_cut(x.defect, 600),
        'confirm', portal_cut(x.confirm, 600), 'basis', portal_cut(x.basis, 300),
        'confidence', x.confidence, 'source', portal_cut(x.source, 200),
        'defects', (select coalesce(jsonb_agg(jsonb_build_object('name', d.name) order by d.name), '[]')
                      from (select d.name from lib_symptom_defects sd join lib_defects d on d.id = sd.defect_id
                             where sd.symptom_id = x.id order by d.name limit 8) d),
        'ops', (select coalesce(jsonb_agg(jsonb_build_object('kind', p.kind, 'name', p.name) order by p.kind, p.name), '[]')
                  from (select p.kind, p.name from lib_symptom_ops so join lib_procedures p on p.id = so.procedure_id
                         where so.symptom_id = x.id order by p.kind, p.name limit 8) p))
      order by x.n) from s x where x.n <= least(greatest(coalesce(lim, 50), 1), 200)), '[]'))
    into выход;
  return выход;
end $fn$;

-- Дефекты и ремонтные решения. Три пути, и путь назван (via):
--   деталь — part_number дефекта — деталь из списка parts (ключом номера);
--   машина — lib_defects.model называет машину: ключ имени, прежнего имени или
--            написания (от четырёх знаков) входит в ключ поля model;
--   узел    — дефект узла из units; если заданы имена машины (names), дефект,
--            записанный для другой машины (model не пуст и не её), не идёт.
-- Для карточки узла parts и names — null: идут все дефекты узла, и поле model
-- показывает, для какой машины дефект записан. Таблица малая (десятки строк;
-- извлечение из текстов ТЗ даст тысячи) — читается целиком за один проход.
create or replace function portal_defects_of(units text[], parts text[], names text[], lim int) returns jsonb
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  строки jsonb;
  годные text[];
  бренды jsonb := '{}';
begin
  with d as (
    select d.id, d.name, d.unit_id, d.part_number, d.model, d.cause, d.consequence, d.fix, d.source, d.seen,
           u.name as unit_name,
           case when parts is not null and coalesce(btrim(d.part_number), '') <> ''
                     and lib_pn_key(d.part_number) = any(parts) then 'деталь'
                when names is not null and coalesce(btrim(d.model), '') <> ''
                     and exists (select 1 from unnest(names) k where strpos(lib_pn_key(d.model), k) > 0) then 'машина'
                when d.unit_id = any(units) and (names is null or coalesce(btrim(d.model), '') = '') then 'узел'
           end as via
      from lib_defects d left join lib_units u on u.id = d.unit_id
  ), о as (
    select d.*, row_number() over (order by case d.via when 'деталь' then 0 when 'машина' then 1 else 2 end,
                                            d.seen desc nulls last, d.name, d.id) as n
      from d where d.via is not null
  )
  select jsonb_build_object('n', (select count(*) from о),
           'list', coalesce((select jsonb_agg(jsonb_build_object(
               'name', x.name,
               'unit', case when x.unit_id is not null then jsonb_build_object('id', x.unit_id, 'name', x.unit_name) end,
               'model', nullif(btrim(x.model), ''), 'via', x.via,
               'cause', portal_cut(x.cause, 600), 'consequence', portal_cut(x.consequence, 800),
               'fix', portal_cut(x.fix, 800), 'source', portal_cut(x.source, 200),
               'written', nullif(btrim(x.part_number), ''),
               'key', case when coalesce(btrim(x.part_number), '') <> '' then lib_pn_key(x.part_number) end,
               'oem', (select nullif(left(btrim(p.oem), 200), '') from lib_parts p
                        where p.id = lib_pn_key(x.part_number) limit 1),
               'ops', (select coalesce(jsonb_agg(jsonb_build_object('kind', p.kind, 'name', p.name) order by p.kind, p.name), '[]')
                         from (select p.kind, p.name from lib_defect_ops dop join lib_procedures p on p.id = dop.procedure_id
                                where dop.defect_id = x.id order by p.kind, p.name limit 8) p))
             order by x.n) from о x where x.n <= least(greatest(coalesce(lim, 50), 1), 200)), '[]'))
    into строки;
  -- Код детали дефекта — только правдоподобный; бренд — изготовитель по
  -- каталогу, а без каталога — «не назван» (номер без бренда не показывается
  -- как позиция: страница ставит рядом «бренд не назван»).
  годные := portal_codes_ok(array(select x ->> 'key' from jsonb_array_elements(строки -> 'list') x
                                   where x ->> 'key' is not null));
  if to_regclass('lib_brands') is not null and to_regclass('lib_brand_map') is not null
     and to_regprocedure('lib_brand_key(text)') is not null then
    бренды := portal_brands_of(array(select distinct x ->> 'oem' from jsonb_array_elements(строки -> 'list') x
                                      where x ->> 'oem' is not null));
  end if;
  return jsonb_build_object('n', строки -> 'n',
    'list', coalesce((select jsonb_agg(
        (x - 'key' - 'oem')
        || jsonb_build_object(
             'code', case when x ->> 'key' = any(годные) and char_length(x ->> 'key') >= 2 then x ->> 'key' end,
             'brand', case when x ->> 'written' is null then null
                           when бренды ? (x ->> 'oem')
                           then jsonb_build_object('key', бренды -> (x ->> 'oem') -> 0 ->> 0,
                                                   'name', бренды -> (x ->> 'oem') -> 0 ->> 1)
                           when x ->> 'oem' is not null
                           then jsonb_build_object('key', null, 'name', left(x ->> 'oem', 120)) end)
        order by n) from jsonb_array_elements(строки -> 'list') with ordinality as t(x, n)), '[]'));
end $fn$;

-- Ремонтные операции узлов: инспекции, контроль, ремонт, покрытия,
-- модернизация. by_family — для машины: операция, записанная для другого
-- семейства (model_family), к ней не идёт, а операция своего семейства без
-- узла — идёт. Для узла by_family = false: все операции узла с подписью
-- семейства.
create or replace function portal_procedures_of(units text[], family text, by_family boolean, lim int) returns jsonb
  language plpgsql stable set plan_cache_mode = force_custom_plan as $fn$
declare
  выход jsonb;
begin
  with о as materialized (
    select p.*, u.name as unit_name,
           row_number() over (order by case p.kind when 'инспекция' then 0 when 'контроль' then 1 when 'ремонт' then 2
                                                   when 'покрытие' then 3 when 'модернизация' then 4 else 5 end,
                                       p.name, p.id) as n
      from lib_procedures p left join lib_units u on u.id = p.unit_id
     where (p.unit_id = any(units)
            and (not coalesce(by_family, false) or p.model_family is null or p.model_family = family))
        or (coalesce(by_family, false) and family is not null and p.unit_id is null and p.model_family = family))
  select jsonb_build_object('n', (select count(*) from о),
    'list', coalesce((select jsonb_agg(jsonb_build_object(
        'kind', x.kind, 'name', x.name,
        'unit', case when x.unit_id is not null then jsonb_build_object('id', x.unit_id, 'name', x.unit_name) end,
        'scope', portal_cut(x.scope, 400), 'duration', nullif(btrim(x.duration), ''),
        'family', nullif(btrim(x.model_family), ''), 'performer', nullif(btrim(x.performer), ''),
        'source', portal_cut(x.source, 200))
      order by x.n) from о x where x.n <= least(greatest(coalesce(lim, 60), 1), 200)), '[]'))
    into выход;
  return выход;
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 11а. КАРТОЧКА МАШИНЫ.
--
--    имя          — имя, прежнее имя, написания (без повторов имени);
--    изготовитель — lib_models.oem → бренды реестра (portal_brands_of, та же
--                   ступень, что у машин карточки кода); ячейка «GE / Siemens /
--                   Alstom» — тремя брендами, неразрешённая — словом, и сама
--                   ячейка рядом (maker_cell), чтобы не потерять несведённую часть;
--    паспорт      — сегмент, вид, семейство, мощность, КПД, валы (у ГПУ —
--                   цилиндры, у Ansaldo — ступени: так их пишет загрузчик),
--                   применение, примечание, откуда машина в справочнике;
--    узлы         — по деталям (измерено) и типовое дерево направления;
--    детали       — lib_part_models → lib_parts, 100 из всех, число рядом;
--    парк         — lib_fleet этой машины: число и 50 площадок;
--    ведомость    — lib_bom этой машины: число и 100 строк;
--    признаки, дефекты, ремонт — по узлам машины (см. шапку раздела 11).
drop function if exists portal_model(text);
create function portal_model(model_id text) returns jsonb
  language plpgsql stable
  set plan_cache_mode = force_custom_plan
  set jit = off
as $fn$
#variable_conflict use_column
declare
  ид       text := btrim(coalesce(portal_model.model_id, ''));
  бюджет   interval := coalesce(nullif(current_setting('portal_entity.budget_ms', true), '')::int,
                                5000) * interval '1 millisecond';
  реестр   boolean := to_regclass('lib_brands') is not null
                      and to_regclass('lib_brand_alias') is not null
                      and to_regclass('lib_brand_map') is not null
                      and to_regprocedure('lib_brand_key(text)') is not null;
  м        record;
  направление text;
  откуда   text;
  имена    text[];
  детали   text[];
  по_узлу  jsonb := '{}';    -- узел → деталей машины в нём
  без_узла int := 0;
  узлы     jsonb := '[]';
  дерево   jsonb;
  узлы_все text[] := '{}';
  ячейки   jsonb := '{}';
  изг      jsonb := '[]';
  список   jsonb := '[]';
  парк     jsonb := '[]';
  парк_n   int := 0;
  ведомость jsonb := '[]';
  ведомость_n int := 0;
  годные   text[];
  признаки jsonb := jsonb_build_object('n', 0, 'list', '[]'::jsonb);
  дефекты  jsonb := jsonb_build_object('n', 0, 'list', '[]'::jsonb);
  ремонт   jsonb := jsonb_build_object('n', 0, 'list', '[]'::jsonb);
  усечено  text[] := '{}';
begin
  if char_length(ид) = 0 or char_length(ид) > 120 then
    return null;
  end if;
  select m.* into м from lib_models m where m.id = ид;
  if not found then
    return null;
  end if;
  направление := portal_model_dir(м.segment_id, м.family);
  откуда := case when направление is null then null
                 when м.segment_id in ('gtu', 'gpu') then 'сегмент' else 'семейство' end;
  -- Ключи имён машины — для дефектов «записан для этой машины». Прежнее имя и
  -- написания делятся по запятой, «;» и «/»: «Frame 5 (1 вал), MS5001PA».
  имена := array(
    select distinct z.k from (
      select lib_pn_key(btrim(s)) as k
        from unnest(array[м.name, м.legacy, м.id] || coalesce(м.aliases, '{}'::text[])) x
        cross join lateral regexp_split_to_table(coalesce(x, ''), '\s*[,;/]\s*') s) z
     where char_length(z.k) >= 4);

  -- ── изготовитель ──
  if coalesce(btrim(м.oem), '') <> '' then
    if реестр then
      ячейки := portal_brands_of(array[м.oem]);
    end if;
    изг := coalesce((select jsonb_agg(jsonb_build_object('key', y ->> 0, 'name', y ->> 1))
                       from jsonb_array_elements(ячейки -> left(btrim(м.oem), 200)) y),
                    jsonb_build_array(jsonb_build_object('key', null, 'name', left(btrim(м.oem), 120))));
  end if;

  -- ── детали машины и их узлы (счёт — всегда: он дешёв и нужен разделам) ──
  детали := array(select pm.part_id from lib_part_models pm where pm.model_id = ид);
  select coalesce(jsonb_object_agg(z.unit_id, z.n), '{}') into по_узлу
    from (select p.unit_id, count(*)::int as n from lib_parts p
           where p.id = any(детали) and p.unit_id is not null group by 1) z;
  select count(*)::int into без_узла from lib_parts p where p.id = any(детали) and p.unit_id is null;
  -- Узлы машины для признаков, дефектов и ремонта: по деталям — с предками,
  -- и всё дерево направления.
  узлы_все := array(
    select distinct x from unnest(
      portal_unit_up(array(select jsonb_object_keys(по_узлу)))
      || array(select u.id from lib_units u where направление is not null and portal_unit_dir(u.id) = направление)) x);

  -- ── узлы ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'узлы'::text;
  else
    select coalesce(jsonb_agg(jsonb_build_object(
             'id', u.id, 'name', u.name, 'crit', u.crit,
             'parent', case when р.id is not null then jsonb_build_object('id', р.id, 'name', р.name) end,
             'parts', (e.value)::int,
             'typical', coalesce(направление is not null and portal_unit_dir(u.id) = направление, false))
           order by (e.value)::int desc, u.name), '[]')
      into узлы
      from jsonb_each_text(по_узлу) e join lib_units u on u.id = e.key
      left join lib_units р on р.id = u.parent_id;
    if направление is not null then
      select jsonb_build_object('dir', направление, 'via', откуда,
               'systems', coalesce(jsonb_agg(jsonb_build_object(
                   'id', r.id, 'name', r.name, 'crit', r.crit,
                   'children', (select count(*) from lib_units c where c.parent_id = r.id),
                   'parts', (select coalesce(sum((по_узлу ->> x)::int), 0)
                               from unnest(portal_unit_down(array[r.id])) x))
                 order by coalesce(r.crit, 'Z'), r.name), '[]'))
        into дерево
        from lib_units r where r.parent_id is null and portal_unit_dir(r.id) = направление;
    end if;
  end if;

  -- ── детали списком ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'детали'::text;
  else
    список := portal_part_list(детали, 100);
  end if;

  -- ── парк ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'парк'::text;
  else
    select count(*)::int into парк_n from lib_fleet f where f.model_id = ид;
    select coalesce(jsonb_agg(jsonb_build_object(
             'site', f.site, 'owner', nullif(btrim(f.owner), ''), 'units', nullif(btrim(f.units), ''),
             'year', nullif(btrim(f.year), ''), 'written', nullif(btrim(f.model_raw), ''),
             'note', portal_cut(f.note, 400))
           order by f.site, f.id), '[]')
      into парк
      from (select * from lib_fleet f where f.model_id = ид order by f.site, f.id limit 50) f;
  end if;

  -- ── ведомость ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'ведомость'::text;
  else
    select count(*)::int into ведомость_n from lib_bom b where b.model_id = ид;
    if ведомость_n > 0 then
      select coalesce(jsonb_agg(to_jsonb(x) order by x.n), '[]') into ведомость
        from (select b.part_id, b.part_no, left(b.name, 200) as name, nullif(btrim(b.node), '') as node,
                     nullif(btrim(b.qty), '') as qty, nullif(btrim(b.position_no), '') as position,
                     nullif(left(btrim(p.oem), 200), '') as oem,
                     row_number() over (order by b.node nulls last, b.position_no, b.part_no, b.id) as n
                from lib_bom b left join lib_parts p on p.id = b.part_id
               where b.model_id = ид
               order by n limit 100) x;
      годные := portal_codes_ok(array(select lib_pn_key(x ->> 'part_no') from jsonb_array_elements(ведомость) x
                                       where x ->> 'part_id' is null));
      if реестр then
        ячейки := portal_brands_of(array(select distinct x ->> 'oem' from jsonb_array_elements(ведомость) x
                                          where x ->> 'oem' is not null));
      end if;
      select coalesce(jsonb_agg(jsonb_build_object(
               -- Строка ведомости с деталью каталога — её код; без неё —
               -- ключ номера, если он правдоподобен.
               'code', coalesce(x ->> 'part_id',
                                case when lib_pn_key(x ->> 'part_no') = any(годные)
                                      and char_length(lib_pn_key(x ->> 'part_no')) >= 2
                                     then lib_pn_key(x ->> 'part_no') end),
               'written', x ->> 'part_no', 'name', x ->> 'name', 'node', x ->> 'node', 'qty', x ->> 'qty',
               'position', x ->> 'position',
               'brand', case when ячейки ? (x ->> 'oem')
                             then jsonb_build_object('key', ячейки -> (x ->> 'oem') -> 0 ->> 0,
                                                     'name', ячейки -> (x ->> 'oem') -> 0 ->> 1)
                             when x ->> 'oem' is not null
                             then jsonb_build_object('key', null, 'name', left(x ->> 'oem', 120)) end)
             order by (x ->> 'n')::int), '[]')
        into ведомость
        from jsonb_array_elements(ведомость) x;
    end if;
  end if;

  -- ── признаки, дефекты, ремонт ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'признаки'::text || 'дефекты'::text || 'ремонт'::text;
  else
    признаки := portal_symptoms_of(узлы_все, 50);
    дефекты := portal_defects_of(узлы_все, детали, имена, 50);
    ремонт := portal_procedures_of(узлы_все, м.family, true, 60);
  end if;

  return jsonb_build_object(
    'id', м.id,
    'name', м.name,
    'legacy', nullif(btrim(м.legacy), ''),
    'aliases', coalesce((
      select jsonb_agg(z.s order by z.s) from (
        select distinct on (lib_pn_key(x)) btrim(x) as s
          from unnest(coalesce(м.aliases, '{}'::text[])) x
         where coalesce(btrim(x), '') <> ''
           and lib_pn_key(x) <> lib_pn_key(м.name) and lib_pn_key(x) <> lib_pn_key(м.legacy)
         order by lib_pn_key(x), btrim(x) limit 30) z), '[]'),
    'makers', изг,
    'maker_cell', nullif(btrim(м.oem), ''),
    'segment', м.segment_id,
    'segment_name', (select s.name from lib_segments s where s.id = м.segment_id),
    'kind', nullif(btrim(м.kind), ''),
    'family', nullif(btrim(м.family_title), ''),
    'power', nullif(btrim(м.power), ''),
    'efficiency', nullif(btrim(м.efficiency), ''),
    'shafts', nullif(btrim(м.shafts), ''),
    -- Поле shafts загрузчик заполняет по-разному: у ГПУ — цилиндры (паспорт
    -- cyl), у Ansaldo — ступени (stages), у прочих — валы.
    'shafts_label', case when м.family = 'gpu' then 'Цилиндры' when м.family = 'ansaldo' then 'Ступени'
                         else 'Валы' end,
    'use_case', nullif(btrim(м.use_case), ''),
    'note', portal_cut(м.note, 600),
    'source', nullif(btrim(м.source), ''),
    'dir', направление,
    'dir_via', откуда,
    'parts', jsonb_build_object('total', cardinality(детали), 'with_unit', cardinality(детали) - без_узла,
                                'no_unit', без_узла, 'list', список),
    'units', узлы,
    'tree', дерево,
    'fleet', jsonb_build_object('n', парк_n, 'list', парк),
    'bom', jsonb_build_object('n', ведомость_n, 'list', ведомость),
    'symptoms', признаки,
    'defects', дефекты,
    'procedures', ремонт,
    'registry', реестр,
    'partial', to_jsonb(усечено));
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 11б. КАРТОЧКА УЗЛА.
--
--    узел     — имя, английское имя, критичность, доступность помимо OEM,
--               примечание, откуда; дерево (ГТУ/ГПУ); путь от корня; вложенные
--               узлы с числом деталей;
--    машины   — две связи порознь: у машины есть детали каталога в этом узле
--               или его вложенных (число), и машина — того же направления, что
--               дерево узла (типово); список до 80, числа рядом;
--    детали   — узла и вложенных, 100 из всех, число рядом;
--    признаки, дефекты, ремонт — узла, вложенных и предков: признак системы
--               («разброс по термопарам» горячего тракта) касается и её
--               компонента. У каждой строки назван её узел.
drop function if exists portal_unit(text);
create function portal_unit(unit_id text) returns jsonb
  language plpgsql stable
  set plan_cache_mode = force_custom_plan
  set jit = off
as $fn$
#variable_conflict use_column
declare
  ид       text := btrim(coalesce(portal_unit.unit_id, ''));
  бюджет   interval := coalesce(nullif(current_setting('portal_entity.budget_ms', true), '')::int,
                                5000) * interval '1 millisecond';
  реестр   boolean := to_regclass('lib_brands') is not null
                      and to_regclass('lib_brand_alias') is not null
                      and to_regclass('lib_brand_map') is not null
                      and to_regprocedure('lib_brand_key(text)') is not null;
  у        record;
  дерево   text;
  вниз     text[];
  вверх    text[];
  путь     jsonb := '[]';
  дети     jsonb := '[]';
  детали   text[];
  здесь    int := 0;
  машины   jsonb := jsonb_build_object('n', 0, 'typical_n', 0, 'with_parts', 0, 'list', '[]'::jsonb);
  ячейки   jsonb := '{}';
  список   jsonb := '[]';
  признаки jsonb := jsonb_build_object('n', 0, 'list', '[]'::jsonb);
  дефекты  jsonb := jsonb_build_object('n', 0, 'list', '[]'::jsonb);
  ремонт   jsonb := jsonb_build_object('n', 0, 'list', '[]'::jsonb);
  усечено  text[] := '{}';
begin
  if char_length(ид) = 0 or char_length(ид) > 120 then
    return null;
  end if;
  select u.* into у from lib_units u where u.id = ид;
  if not found then
    return null;
  end if;
  дерево := portal_unit_dir(у.id);
  вниз := portal_unit_down(array[ид]);
  вверх := portal_unit_up(array[ид]);
  -- Путь от корня до родителя.
  with recursive п (id, name, parent_id, depth) as (
    select u.id, u.name, u.parent_id, 0 from lib_units u where u.id = у.parent_id
    union all
    select r.id, r.name, r.parent_id, п.depth + 1 from lib_units r join п on r.id = п.parent_id where п.depth < 10)
  select coalesce(jsonb_agg(jsonb_build_object('id', п.id, 'name', п.name) order by п.depth desc), '[]')
    into путь from п;
  детали := array(select p.id from lib_parts p where p.unit_id = any(вниз));
  select count(*)::int into здесь from lib_parts p where p.unit_id = ид;
  select coalesce(jsonb_agg(jsonb_build_object(
           'id', c.id, 'name', c.name, 'crit', c.crit,
           'children', (select count(*) from lib_units g where g.parent_id = c.id),
           'parts', (select count(*) from lib_parts p where p.unit_id = any(portal_unit_down(array[c.id]))))
         order by coalesce(c.crit, 'Z'), c.name), '[]')
    into дети
    from lib_units c where c.parent_id = ид;

  -- ── машины ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'машины'::text;
  else
    with д as (
      select pm.model_id, count(distinct pm.part_id)::int as n
        from lib_part_models pm where pm.part_id = any(детали) group by 1
    ), все as materialized (
      select m.id, m.name, m.oem, m.kind, m.family_title, m.segment_id, coalesce(д.n, 0) as n,
             coalesce(portal_model_dir(m.segment_id, m.family) = дерево, false) as typical
        from lib_models m left join д on д.model_id = m.id
       where д.model_id is not null or portal_model_dir(m.segment_id, m.family) = дерево
    ), показ as (
      select * from все order by все.n desc, все.name, все.id limit 80
    )
    select jsonb_build_object('n', (select count(*) from все),
             'typical_n', (select count(*) from все where все.typical),
             'with_parts', (select count(*) from все where все.n > 0),
             'list', coalesce((select jsonb_agg(jsonb_build_object(
                         'id', x.id, 'name', x.name, 'kind', coalesce(x.kind, x.family_title),
                         'segment', x.segment_id, 'segment_name', s.name,
                         'maker', nullif(left(btrim(x.oem), 200), ''), 'parts', x.n, 'typical', x.typical)
                       order by x.n desc, x.name, x.id)
                         from показ x left join lib_segments s on s.id = x.segment_id), '[]'))
      into машины;
    if реестр then
      ячейки := portal_brands_of(array(select distinct x ->> 'maker' from jsonb_array_elements(машины -> 'list') x
                                        where x ->> 'maker' is not null));
    end if;
    машины := jsonb_set(машины, '{list}', coalesce((select jsonb_agg(
        (x - 'maker')
        || jsonb_build_object('brand', case when ячейки ? (x ->> 'maker')
                                            then jsonb_build_object('key', ячейки -> (x ->> 'maker') -> 0 ->> 0,
                                                                    'name', ячейки -> (x ->> 'maker') -> 0 ->> 1)
                                            when x ->> 'maker' is not null
                                            then jsonb_build_object('key', null, 'name', left(x ->> 'maker', 120)) end)
        order by n) from jsonb_array_elements(машины -> 'list') with ordinality as t(x, n)), '[]'::jsonb));
  end if;

  -- ── детали ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'детали'::text;
  else
    список := portal_part_list(детали, 100);
  end if;

  -- ── признаки, дефекты, ремонт ──
  if clock_timestamp() - statement_timestamp() > бюджет then
    усечено := усечено || 'признаки'::text || 'дефекты'::text || 'ремонт'::text;
  else
    признаки := portal_symptoms_of(вниз || вверх, 50);
    дефекты := portal_defects_of(вниз || вверх, null, null, 50);
    ремонт := portal_procedures_of(вниз || вверх, null, false, 60);
  end if;

  return jsonb_build_object(
    'id', у.id,
    'name', у.name,
    'name_en', nullif(btrim(у.name_en), ''),
    'crit', nullif(btrim(у.crit), ''),
    'aftermarket', nullif(btrim(у.aftermarket), ''),
    'note', portal_cut(у.note, 600),
    'source', nullif(btrim(у.source), ''),
    'dir', дерево,
    'path', путь,
    'children', дети,
    'machines', машины,
    'parts', jsonb_build_object('total', cardinality(детали), 'here', здесь, 'list', список),
    'symptoms', признаки,
    'defects', дефекты,
    'procedures', ремонт,
    'registry', реестр,
    'partial', to_jsonb(усечено));
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 12. Права: функции по умолчанию исполнимы всеми (PUBLIC) — снимаем; роли
--     платформы только через проверку наличия (правило 20). Исполняет
--     сервисный ключ воркера портала, за Cloudflare Access и правом suppliers
--     (машина и узел — ещё и правом библиотеки, сайт knowledge).
--     Помощники тоже: они вызываются от имени вызывающего. Список один — массив
--     цикла ниже; тест прав читает его отсюда же, и новая функция без строки в
--     нём не пройдёт проверку «у каждой функции файла права сняты».
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
                           'portal_qty(numeric, numeric, numeric)', 'portal_says_analog(text)',
                           'portal_mask_re(text)', 'portal_codes_ok(text[])', 'portal_brands_of(text[])',
                           'portal_not_brands(text[])', 'portal_sup_names(text[])',
                           'portal_companies(text[])', 'portal_brand_keys(text)',
                           'portal_brand_spellings(text)', 'portal_brand_rows(text, int)',
                           'portal_model(text)', 'portal_unit(text)',
                           'portal_model_dir(text, text)', 'portal_unit_dir(text)', 'portal_cut(text, int)',
                           'portal_unit_down(text[])', 'portal_unit_up(text[])', 'portal_part_list(text[], int)',
                           'portal_symptoms_of(text[], int)', 'portal_defects_of(text[], text[], text[], int)',
                           'portal_procedures_of(text[], text, boolean, int)'] loop
    execute format('revoke all on function %s from public', ф);
    if кому is not null then
      execute format('revoke all on function %s from %s', ф, кому);
    end if;
    if сервис is not null then
      execute format('grant execute on function %s to %s', ф, сервис);
    end if;
  end loop;
end $$;
commit;
