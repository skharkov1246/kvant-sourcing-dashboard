-- ЕДИНЫЙ ПОИСК ПОРТАЛА — шаг 1 плана «одна стартовая страница» (24.09.2026).
--
-- ЗАЧЕМ. Сорсер приходит с одним словом в руках: кодом с шильдика, брендом из
-- заявки, ИНН или доменом поставщика, именем машины. До сих пор каждое такое
-- слово искалось на своей странице (номенклатура, бренды, поставщики,
-- библиотека), и переход «код → бренд → другой код → поставщик» требовал знать
-- заранее, где что лежит. Здесь одна функция отвечает на слово строками пяти
-- видов, и каждая строка ведёт на уже существующую страницу. Ни одна страница,
-- ни одна функция и ни одна таблица при этом не меняются: файл только
-- ДОБАВЛЯЕТ функцию и несколько индексов на малых таблицах.
--
-- ЧТО ИЩЕТСЯ И ГДЕ
--   код       — ключ lib_pn_key(запрос), точно и по началу, в четырёх
--               источниках: спрос (lib_demand_live), разбор КП (lib_prices,
--               поток «разбор КП»), каталог (lib_parts: id, catalog_no, kv_no)
--               и аналоги (lib_part_alt.alt_pn). Рядом с кодом — бренд: сначала
--               изготовитель по каталогу, приведённый к имени реестра брендов;
--               нет его — самый частый бренд реестра среди написаний спроса и
--               КП; реестра нет или написание не разрешено — само написание,
--               и тогда brand_key пуст (страница показывает его как слово, а не
--               как бренд реестра).
--   бренд     — lib_brands и написания lib_brand_alias по ключу lib_brand_key;
--   поставщик — sup_entity (кроме слитых: слитый ведёт на того, в кого слит)
--               по номеру KV-S, ИНН/VAT/ОГРН, домену, написаниям и названию;
--               показываемое имя — из вида sup_name_shown, когда он есть;
--   машина    — lib_models по имени, прежнему имени и написаниям (aliases);
--   узел      — lib_units по имени, английскому имени и ключу.
-- Отвечает только агрегатами: счёт сделок, строк, предложений, деталей. Ни
-- номеров сделок, ни файлов, ни почт и телефонов.
--
-- ПОЧЕМУ plpgsql, А НЕ sql. Три опоры поиска могут ещё не стоять в базе: реестр
-- брендов (brands_schema.sql), вид показываемого имени sup_name_shown и
-- проверка правдоподобия кода lib_pn_plausible (соседняя работа). Функция на
-- языке sql проверяет имена таблиц при создании и не создалась бы вовсе;
-- plpgsql разбирает запрос при первом исполнении, поэтому ветка, чьей опоры
-- нет, просто не исполняется. Наличие проверяется во время ВЫЗОВА, а не
-- применения: появится вид — функция начнёт им пользоваться без повторного
-- применения этого файла («инструмент чтения спрашивает у базы», CLAUDE.md).
--
-- ПРЕДЕЛ ВРЕМЕНИ У ФУНКЦИИ НЕ СТАВИТСЯ, И ЭТО РЕШЕНИЕ. «set statement_timeout»
-- в объявлении функции вызов НЕ ограничивает: таймер взводится в начале
-- клиентского оператора, и смена настройки внутри функции его не перевзводит.
-- Проверено 24.09.2026 на PostgreSQL 16: функция с «set statement_timeout =
-- '1s'» и pg_sleep(2) внутри отработала две секунды без отмены — и на sql, и на
-- plpgsql. Поэтому здесь два настоящих ограничения: каждый шаг ограничен числом
-- строк (кандидатов, строк спроса на код), а между видами стоит бюджет времени —
-- исчерпан, и оставшиеся виды не считаются, а в ответ идёт строка вида
-- «усечено» с их названием. Молчаливого усечения нет. Предел оператора целиком
-- держат роль PostgREST и десятисекундный обрыв в воркере.
--
-- ПОЧЕМУ ДИАПАЗОН, А НЕ LIKE. Поиск по началу ключа должен идти по УЖЕ
-- стоящим индексам: lib_demand_pnkey (2,8 млн строк, новый индекс там не
-- строим) и lib_prices_pn_key. Они построены с правилом сравнения базы, а LIKE
-- 'abc%' берёт такой индекс только в локали C. Условие «ключ > abc и ключ <
-- граница» индекс берёт в любой локали; границу считает portal_prefix_hi ниже
-- так, чтобы диапазон ВСЕГДА покрывал все ключи с этим началом — и в C, и в
-- правилах ICU, где кириллица стоит перед латиницей. Лишнее, что диапазон
-- может захватить, снимает starts_with. Свойство проверено тестом на всех
-- правилах сравнения из C, C.utf8 и ICU und, en-US, ru-RU, какие есть в базе
-- (tests/test_portal_search_sql.py); подмена границы на переход «z» → «а»
-- этим тестом ловится.
--
-- ИДЕМПОТЕНТНО: применяется повторно без ошибок. Роли Supabase — только через
-- проверку наличия (правило 20). Применение — Actions → «ZIP base — apply DB
-- migrations» (zip-db.yml), после brands_schema.sql.

set statement_timeout = '10min';
set lock_timeout      = '10s';   -- индексы ниже строятся на малых таблицах

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Индексы. Только малые таблицы: деталей 13,5 тыс., аналогов 1,7 тыс.,
--    брендов 2,4 тыс., поставщиков — тысячи. Каждый индекс — сотни килобайт.
--    На lib_demand новых индексов нет: поиск кода по спросу идёт по
--    lib_demand_pnkey (schema_junk.sql).
create index if not exists lib_parts_kv_key on lib_parts (lib_pn_key(kv_no));
create index if not exists lib_part_alt_key on lib_part_alt (lib_pn_key(alt_pn));
do $$
begin
  if to_regclass('lib_brands') is not null then
    create index if not exists lib_brands_name_prefix
      on lib_brands (lower(name) text_pattern_ops);
  end if;
  if to_regclass('sup_entity') is not null then
    create index if not exists sup_entity_name_prefix
      on sup_entity (lower(display_name) text_pattern_ops);
  end if;
  -- Индекс спроса строится в schema_junk.sql CONCURRENTLY; оборванное
  -- построение оставляет его НЕДЕЙСТВИТЕЛЬНЫМ, и поиск по спросу тогда читает
  -- таблицу целиком. Говорим об этом в журнале применения, а не молчим.
  if not exists (select 1 from pg_index
                  where indexrelid = to_regclass('lib_demand_pnkey') and indisvalid) then
    raise warning 'lib_demand_pnkey нет или он недействителен: поиск кода по спросу пойдёт полным чтением lib_demand — примените schema_junk.sql';
  end if;
end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Верхняя граница диапазона «ключ начинается с k».
--
--    Ищется самый правый знак k, у которого есть следующий ВНУТРИ СВОЕГО
--    алфавита; граница — всё до него плюс этот следующий знак. Последний знак
--    алфавита (9, z, я) следующего не имеет: берётся знак левее, и диапазон
--    становится шире, но не уже. Переход через границу алфавитов («z» → «а»)
--    не делается намеренно: порядок письменностей у правил сравнения разный
--    (в ICU ru-RU кириллица стоит перед латиницей), а переход внутри алфавита
--    везде одинаков. «й» идёт как «и», а следующий у «и» — «к». Это страховка:
--    в ICU у «й» своя буква (проверено тестом), но по разложению Юникода «й» —
--    это «и» со знаком, и в правилах, которые так её и сравнивают, строки на
--    «ий…» и «ии…» идут вперемешку — граница «й» потеряла бы часть из них.
--    Цена страховки — диапазон чуть шире, лишнее снимает starts_with.
--    Заглавные — для value_norm реестра поставщиков (буквы и цифры, верхний
--    регистр). Нет подходящего знака вовсе — null: тогда только точный поиск.
create or replace function portal_prefix_hi(k text) returns text
language plpgsql immutable parallel safe as $fn$
declare
  алфавиты constant text[] := array[
    '0123456789',
    'abcdefghijklmnopqrstuvwxyz',
    'абвгдежзиклмнопрстуфхцчшщъыьэюя',
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
    'АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'];
  алфавит text;
  знак text;
  место int;
begin
  for i in reverse coalesce(char_length(k), 0) .. 1 loop
    знак := substr(k, i, 1);
    if знак = 'й' then знак := 'и'; elsif знак = 'Й' then знак := 'И'; end if;
    foreach алфавит in array алфавиты loop
      место := strpos(алфавит, знак);
      if место > 0 and место < char_length(алфавит) then
        return left(k, i - 1) || substr(алфавит, место + 1, 1);
      end if;
    end loop;
  end loop;
  return null;
end $fn$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Поиск.
--
--    РАНГ (rank): 0 — точное совпадение ключа, номера, ИНН, домена или имени;
--    1 — совпадение по началу; 2 — вхождение в середину имени (только для
--    имён, от трёх знаков). Внутри вида строки идут по рангу, потом по весу
--    свидетельств (сделки + предложения + каталог), потом по длине и ключу.
--    Воркер складывает виды по лучшему рангу: точный поставщик по ИНН встаёт
--    выше кодов, начинающихся с тех же цифр.
--
--    ОТБОР КОДОВ ДО СЧЁТА. Кандидаты по началу ключа собираются «прыжками по
--    индексу» (рекурсивный запрос: следующий ключ больше текущего, по одному
--    чтению индекса на ключ), по 2×lim из каждого источника, и до тяжёлого счёта
--    доходит lim+1 ближайших (точный, потом короче, потом по алфавиту). Поэтому
--    стоимость не зависит от того, сколько строк у популярного кода: сотни
--    тысяч «подшипников» не читаются вовсе. Строк спроса и КП на один код
--    считается не больше 5 000; упёрлись — counts.capped, усечение видно.
--
--    Порядок видов — от дешёвых к дорогому: коды последними, чтобы бюджет
--    времени отрезал в худшем случае их, а не поставщиков и бренды.
--
--    Возвращаемые колонки меняются только сносом функции (create or replace
--    меняет тело, но не состав колонок), поэтому снос стоит перед созданием —
--    в одной транзакции с выдачей прав, как у видов (правило «сноси перед
--    созданием», tests/test_schema_views_droppable.py).
begin;
drop function if exists portal_search(text, int);
create function portal_search(q text, lim int default 20)
returns table (kind text, key text, title text, subtitle text, brand text, brand_key text,
               brand_src text, segment text, counts jsonb, source text, rank int)
language plpgsql stable
-- Параметры запроса подставляются как значения, а не как «какой-то текст»:
-- иначе после пятого вызова план может стать общим, и диапазон по ключу
-- перестанет доходить до индекса. Эта настройка у функции действует — в отличие
-- от предела времени (см. шапку).
set plan_cache_mode = force_custom_plan
-- JIT здесь только мешает: оценка строк по jsonb (сто по умолчанию) раздувает
-- стоимость выше порога, и на выборку в миллисекунду уходило 40 мс компиляции
-- (замер 24.09.2026 на синтетике: 47 мс из 47 — JIT).
set jit = off
as $fn$
#variable_conflict use_column
declare
  запрос   text := btrim(coalesce(q, ''));
  предел   int  := least(greatest(coalesce(lim, 20), 1), 50);
  -- Бюджет времени на виды после первого. Настройка сеанса нужна тесту
  -- («усечено» без неё не проверить); в работе её никто не ставит.
  бюджет   interval := coalesce(nullif(current_setting('portal_search.budget_ms', true), '')::int,
                                5000) * interval '1 millisecond';
  ключ     text;          -- ключ кода и имени: lib_pn_key(запрос)
  граница  text;          -- верхняя граница диапазона «начинается с ключа»
  норма    text;          -- ключ поиска признака поставщика (value_norm)
  граница_н text;
  домен    text;          -- тот же ключ, но из адреса сайта или почты
  образец  text;          -- «начинается с» для LIKE, спецзнаки экранированы
  вхождение text;         -- «содержит» для LIKE
  ключ_б   text;          -- ключ написания бренда: lib_brand_key(запрос)
  граница_б text;
  имена    jsonb := '{}'; -- показываемые имена поставщиков (sup_name_shown)
  по_имени jsonb := '{}'; -- совпадения по показываемому имени: sup_id → [ранг, имя]
  буквы    boolean;
  отбор    jsonb;
  кандидаты jsonb := '{}';
  с_размером text[];
  коды     jsonb;
  написания text[];
  бренды   jsonb := '{}'; -- написание → [ключ бренда, имя бренда]
  выданы   text[] := '{}';  -- у кого из найденных поставщиков выдан вечный номер
  реестр   boolean := to_regclass('lib_brands') is not null
                      and to_regclass('lib_brand_alias') is not null
                      and to_regclass('lib_brand_map') is not null;
begin
  if char_length(запрос) < 2 or char_length(запрос) > 80 then
    return;
  end if;
  ключ := lib_pn_key(запрос);
  граница := case when char_length(ключ) >= 3 then portal_prefix_hi(ключ) end;
  образец := replace(replace(replace(lower(запрос), '\', '\\'), '%', '\%'), '_', '\_');
  вхождение := case when char_length(запрос) >= 3 then '%' || образец || '%' else образец || '%' end;
  образец := образец || '%';

  -- ── ПОСТАВЩИКИ ────────────────────────────────────────────────────────────
  if to_regclass('sup_entity') is not null and to_regclass('sup_identifier') is not null then
    -- Ключ признака — тем же правилом, каким его пишет сведение
    -- (library/load_supplier_master.норма): буквы и цифры, верхний регистр.
    норма := regexp_replace(upper(запрос), '[^0-9A-ZА-Я]', '', 'g');
    граница_н := case when char_length(норма) >= 3 then portal_prefix_hi(норма) end;
    домен := regexp_replace(upper(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
               lower(запрос), '^.*@', ''), '^[a-z][a-z0-9+.-]*://', ''), '^www\.', ''), '[/?#:].*$', '')),
             '[^0-9A-ZА-Я]', '', 'g');
    -- Без единой буквы (ИНН, цифры кода) и у номера KV-S имя не ищется:
    -- совпадение «7700…» внутри названия — случайность, а не ответ, и стоит
    -- полного чтения вида.
    буквы := запрос ~ '[A-Za-zА-Яа-яЁё]' and норма !~ '^KV[SG][0-9]{3,}';
    if буквы and to_regclass('sup_name_shown') is not null then
      -- Вид считается целиком (он собирает имя из трёх источников), поэтому по
      -- нему — один проход с пределом: и совпадения, и их имена разом.
      execute $q$
        select coalesce(jsonb_object_agg(sup_id, jsonb_build_array(r, name)), '{}') from (
          select sup_id, name,
                 case when lower(name) = lower($3) then 0 when lower(name) like $4 then 1 else 2 end as r
            from sup_name_shown where lower(name) like $1
           order by 3, char_length(name), 1 limit $2) x
      $q$ into по_имени using вхождение, предел * 3, запрос, образец;
    end if;
    select coalesce(jsonb_object_agg(b.id, jsonb_build_object('r', b.r, 'how', b.how, 'val', b.val)), '{}')
      into отбор
      from (
        select b.* from (
          -- Слитая сущность ведёт на ту, в которую слита: поиск по её ИНН или
          -- домену не должен кончаться пустой карточкой.
          select coalesce(case when e.resolution = 'merged' then e.merged_into end, e.id) as id,
                 min(m.r) as r,
                 (array_agg(m.how order by m.r, m.how))[1] as how,
                 (array_agg(m.val order by m.r, m.how))[1] as val
            from (
              select s.id, 0 as r, 'номер'::text as how, null::text as val
                from sup_entity s where s.id = upper(запрос)
              union all
              -- Номер набран без дефисов или не до конца: «KVS000011», «KV-S-0000».
              select s.id, case when replace(s.id, '-', '') = норма then 0 else 1 end, 'номер', null
                from sup_entity s
               where норма ~ '^KV[SG][0-9]{3,}' and starts_with(replace(s.id, '-', ''), норма)
              union all
              select i.sup_id,
                     case when i.value_norm in (норма, домен) then 0 else 1 end,
                     case i.kind when 'inn' then 'ИНН' when 'vat' then 'VAT' when 'ogrn' then 'ОГРН'
                                 when 'domain' then 'домен' else 'написание' end,
                     i.value
                from sup_identifier i
               where i.status <> 'rejected'
                 and i.kind in ('inn', 'vat', 'ogrn', 'domain', 'alias', 'trading')
                 and char_length(норма) >= 2
                 and (i.value_norm = норма
                      or (граница_н is not null and i.value_norm > норма and i.value_norm < граница_н
                          and starts_with(i.value_norm, норма)))
              union all
              select i.sup_id, 0, 'домен', i.value
                from sup_identifier i
               where i.status <> 'rejected' and i.kind = 'domain'
                 and char_length(домен) >= 4 and домен <> норма and i.value_norm = домен
              union all
              select s.id,
                     case when lower(s.display_name) = lower(запрос) then 0
                          when lower(s.display_name) like образец then 1 else 2 end,
                     'название', null
                from sup_entity s where буквы and lower(s.display_name) like вхождение
              union all
              select p.key, (p.value ->> 0)::int, 'название', null from jsonb_each(по_имени) p
            ) m
            join sup_entity e on e.id = m.id
           group by 1
        ) b
        join sup_entity e2 on e2.id = b.id and e2.resolution <> 'merged'
        -- До счёта предложений — не больше трёх пределов: «содержит» по
        -- короткому слову находит сотни компаний, а предложения считаются на
        -- каждую отдельным чтением.
        order by b.r, char_length(e2.display_name), b.id
        limit предел * 3
      ) b;
    select coalesce(jsonb_object_agg(p.key, p.value ->> 1), '{}') into имена
      from jsonb_each(по_имени) p where p.value ->> 1 is not null;
    if to_regclass('sup_name_shown') is not null
       and exists (select 1 from jsonb_object_keys(отбор) k where not имена ? k) then
      execute $q$
        select $2 || coalesce(jsonb_object_agg(sup_id, name), '{}') from sup_name_shown
         where sup_id = any($1) and name is not null
      $q$ into имена using array(select k from jsonb_object_keys(отбор) k where not имена ? k), имена;
    end if;
    -- НОМЕР В ПОДПИСИ — ТОЛЬКО ВЫДАННЫЙ. Сущность без строки в
    -- sup_number_registry ждёт ИНН: раздел «Поставщики» и карточка /p пишут у
    -- неё «номер не выдан», и подпись поиска не должна называть номером то,
    -- что им не является (ключ записи остаётся в адресе ссылки).
    if to_regclass('sup_number_registry') is not null then
      execute 'select coalesce(array_agg(sup_id), ''{}'') from sup_number_registry where sup_id = any($1)'
        into выданы using array(select k from jsonb_object_keys(отбор) k);
    end if;
    return query
      select 'поставщик'::text, e.id, coalesce(имена ->> e.id, e.display_name),
             concat_ws(' · ', case when e.id = any(выданы) then e.id else 'номер не выдан' end,
                       case o.how when 'ИНН' then 'ИНН ' || o.val when 'VAT' then 'VAT ' || o.val
                                  when 'ОГРН' then 'ОГРН ' || o.val when 'домен' then o.val
                                  when 'написание' then 'написание «' || o.val || '»' end,
                       nullif(concat_ws(', ', e.city, e.country), '')),
             null::text, null::text, null::text, null::text,
             jsonb_build_object('offers', coalesce(ц.rows, 0), 'codes', coalesce(ц.codes, 0)),
             o.how, o.r
        from jsonb_each(отбор) x
        cross join lateral jsonb_to_record(x.value) as o(r int, how text, val text)
        join sup_entity e on e.id = x.key
        -- Предложения поставщика: строки разбора КП с карточек его ключей портала.
        left join lateral (
          select count(*)::int as rows, count(distinct lib_pn_key(p.part_number))::int as codes
            from lib_prices p
           where p.feed = 'разбор КП'
             and p.rfq_company in (select i.value_norm from sup_identifier i
                                    where i.sup_id = e.id and i.kind = 'bitrix'
                                      and i.status <> 'rejected')) ц on true
       order by o.r, coalesce(ц.rows, 0) desc, e.id
       limit предел;
  end if;

  -- ── БРЕНДЫ ────────────────────────────────────────────────────────────────
  if реестр and clock_timestamp() - statement_timestamp() <= бюджет then
    ключ_б := lib_brand_key(запрос);
    граница_б := case when char_length(ключ_б) >= 3 then portal_prefix_hi(ключ_б) end;
    if char_length(ключ_б) >= 2 then
      return query
        with m as (
          select a.brand_key, case when a.spelling_key = ключ_б then 0 else 1 end as r, a.spelling
            from lib_brand_alias a
           where a.status in ('разрешено', 'проверено') and a.brand_key is not null
             and (a.spelling_key = ключ_б
                  or (граница_б is not null and a.spelling_key > ключ_б and a.spelling_key < граница_б
                      and starts_with(a.spelling_key, ключ_б)))
          union all
          select b.brand_key,
                 case when b.brand_key = ключ_б or lower(b.name) = lower(запрос) then 0
                      when lower(b.name) like образец then 1 else 2 end,
                 null
            from lib_brands b
           where b.brand_key = ключ_б or lower(b.name) like вхождение
        ), лучшие as (
          select m.brand_key, min(m.r) as r,
                 (array_agg(m.spelling order by m.r, m.spelling) filter (where m.spelling is not null))[1] as spelling
            from m group by m.brand_key
        )
        select 'бренд'::text, b.brand_key, b.name,
               concat_ws(' · ',
                         case when л.spelling is not null and lib_brand_key(л.spelling) <> lib_brand_key(b.name)
                              then 'написание «' || л.spelling || '»' end,
                         b.owner, b.country,
                         case when b.former_names is not null then 'прежде: ' || b.former_names end),
               null::text, null::text, null::text, null::text,
               jsonb_build_object('spellings', coalesce(с.spellings, 0), 'rows', coalesce(с.rows, 0)),
               'реестр брендов'::text, л.r
          from лучшие л
          join lib_brands b on b.brand_key = л.brand_key
          left join lateral (
            select count(*)::int as spellings, coalesce(sum(a.n_rows), 0)::bigint as rows
              from lib_brand_alias a
             where a.brand_key = b.brand_key and a.status in ('разрешено', 'проверено')) с on true
         order by л.r, coalesce(с.rows, 0) desc, b.name
         limit предел;
    end if;
  elsif реестр then
    return query select 'усечено'::text, 'бренд'::text, null::text, null::text, null::text, null::text,
                        null::text, null::text, null::jsonb, null::text, 99;
  end if;

  -- ── МАШИНЫ ────────────────────────────────────────────────────────────────
  if clock_timestamp() - statement_timestamp() > бюджет then
    return query select 'усечено'::text, 'машина'::text, null::text, null::text, null::text, null::text,
                        null::text, null::text, null::jsonb, null::text, 99;
  elsif char_length(ключ) >= 2 then
    return query
      select 'машина'::text, m.id, m.name,
             concat_ws(' · ',
                       case when x.via is not null
                             and lib_pn_key(x.via) not in (lib_pn_key(m.name), lib_pn_key(m.legacy))
                            then 'по написанию «' || x.via || '»' end,
                       m.oem, coalesce(m.kind, m.family_title), m.use_case,
                       case when m.legacy is not null then 'прежнее имя ' || m.legacy end),
             null::text, null::text, null::text, m.segment_id,
             jsonb_build_object('parts', с.parts, 'fleet', с.fleet),
             'справочник машин'::text, x.r
        from lib_models m
        cross join lateral (
          select min(v.r) as r, (array_agg(v.s order by v.r, v.ord))[1] as via
            from (select u.s, u.ord,
                         case when lib_pn_key(u.s) = ключ then 0
                              when starts_with(lib_pn_key(u.s), ключ) then 1
                              when char_length(ключ) >= 3 and strpos(lib_pn_key(u.s), ключ) > 0 then 2 end as r
                    from unnest(array[m.name, m.id, m.legacy] || coalesce(m.aliases, '{}'::text[]))
                         with ordinality as u(s, ord)
                   where coalesce(btrim(u.s), '') <> '') v
           where v.r is not null) x
        cross join lateral (
          select (select count(*) from lib_part_models pm where pm.model_id = m.id)::int as parts,
                 (select count(*) from lib_fleet f where f.model_id = m.id)::int as fleet) с
       where x.r is not null
       order by x.r, с.parts desc, m.name
       limit предел;
  end if;

  -- ── УЗЛЫ ──────────────────────────────────────────────────────────────────
  if clock_timestamp() - statement_timestamp() > бюджет then
    return query select 'усечено'::text, 'узел'::text, null::text, null::text, null::text, null::text,
                        null::text, null::text, null::jsonb, null::text, 99;
  elsif char_length(ключ) >= 2 then
    return query
      select 'узел'::text, u.id, u.name,
             concat_ws(' · ', р.name, u.name_en,
                       case when u.crit is not null then 'критичность ' || u.crit end),
             null::text, null::text, null::text, null::text,
             jsonb_build_object('parts', с.parts, 'children', с.children),
             'справочник узлов'::text, x.r
        from lib_units u
        left join lib_units р on р.id = u.parent_id
        cross join lateral (
          select min(case when lib_pn_key(v.s) = ключ then 0
                          when starts_with(lib_pn_key(v.s), ключ) then 1
                          when char_length(ключ) >= 3 and strpos(lib_pn_key(v.s), ключ) > 0 then 2 end) as r
            from unnest(array[u.name, u.name_en, u.id]) as v(s)
           where coalesce(btrim(v.s), '') <> '') x
        cross join lateral (
          select (select count(*) from lib_parts p where p.unit_id = u.id)::int as parts,
                 (select count(*) from lib_units c where c.parent_id = u.id)::int as children) с
       where x.r is not null
       order by x.r, с.parts desc, u.name
       limit предел;
  end if;

  -- ── КОДЫ ──────────────────────────────────────────────────────────────────
  if clock_timestamp() - statement_timestamp() > бюджет then
    return query select 'усечено'::text, 'код'::text, null::text, null::text, null::text, null::text,
                        null::text, null::text, null::jsonb, null::text, 99;
    return;
  end if;
  if char_length(ключ) < 2 then
    return;
  end if;
  -- Сам ключ — кандидат всегда: есть ли он где-нибудь, решит счёт ниже.
  кандидаты := jsonb_build_object(ключ, 0);
  if граница is not null then
    select coalesce(jsonb_object_agg(c.c, 1), '{}') || кандидаты into кандидаты
      from (
        select x.c from (
          -- Спрос: прыжки по lib_demand_pnkey, по одному чтению индекса на ключ.
          with recursive t(c) as (
            (select lib_pn_key(d.part_number) from lib_demand d
              where lib_pn_key(d.part_number) > ключ and lib_pn_key(d.part_number) < граница
              order by lib_pn_key(d.part_number) limit 1)
            union all
            select (select lib_pn_key(d.part_number) from lib_demand d
                     where lib_pn_key(d.part_number) > t.c and lib_pn_key(d.part_number) < граница
                     order by lib_pn_key(d.part_number) limit 1)
              from t where t.c is not null)
          select t.c from t where t.c is not null limit предел * 2) x
        union
        select x.c from (
          -- Разбор КП: те же прыжки по частичному индексу lib_prices_pn_key.
          with recursive t(c) as (
            (select lib_pn_key(p.part_number) from lib_prices p
              where p.feed = 'разбор КП'
                and lib_pn_key(p.part_number) > ключ and lib_pn_key(p.part_number) < граница
              order by lib_pn_key(p.part_number) limit 1)
            union all
            select (select lib_pn_key(p.part_number) from lib_prices p
                     where p.feed = 'разбор КП'
                       and lib_pn_key(p.part_number) > t.c and lib_pn_key(p.part_number) < граница
                     order by lib_pn_key(p.part_number) limit 1)
              from t where t.c is not null)
          select t.c from t where t.c is not null limit предел * 2) x
        union
        select x.c from (select p.id as c from lib_parts p
                          where p.id > ключ and p.id < граница order by p.id limit предел * 2) x
        union
        select x.c from (select distinct lib_pn_key(p.catalog_no) as c from lib_parts p
                          where lib_pn_key(p.catalog_no) > ключ and lib_pn_key(p.catalog_no) < граница
                          order by 1 limit предел * 2) x
        union
        select x.c from (select distinct lib_pn_key(a.alt_pn) as c from lib_part_alt a
                          where lib_pn_key(a.alt_pn) > ключ and lib_pn_key(a.alt_pn) < граница
                          order by 1 limit предел * 2) x
      ) c
     where starts_with(c.c, ключ);
  end if;
  -- Наш номер KV ведёт на деталь, ключ которой с ним не совпадает: такие
  -- кандидаты не проходят фильтр «начинается с ключа» и добавляются отдельно.
  if ключ like 'kv%' then
    select кандидаты || coalesce(jsonb_object_agg(x.id, x.r), '{}') into кандидаты
      from (select p.id, min(case when lib_pn_key(p.kv_no) = ключ then 0 else 1 end) as r
              from lib_parts p
             where lib_pn_key(p.kv_no) = ключ
                or (граница is not null and lib_pn_key(p.kv_no) > ключ
                    and lib_pn_key(p.kv_no) < граница and starts_with(lib_pn_key(p.kv_no), ключ))
             group by p.id order by 2, 1 limit предел) x;
  end if;
  -- Марки материала и обозначения стандартов («SS316», «ГОСТ 8752») кодом не
  -- являются. Проверка — соседней работы; пока её нет, кандидаты идут как есть.
  -- Стандарт с размером («DIN 471 25») — код: кандидат — ключ, и написания
  -- судит lib_pn_std_sized (schema.sql), если она уже стоит в базе.
  if to_regprocedure('lib_pn_plausible(text)') is not null then
    с_размером := '{}';
    if to_regprocedure('lib_pn_std_sized(text[])') is not null then
      execute 'select lib_pn_std_sized(array(select jsonb_object_keys($1)))'
        into с_размером using кандидаты;
    end if;
    execute $q$
      select coalesce(jsonb_object_agg(e.key, e.value), '{}')
        from jsonb_each($1) e where lib_pn_plausible(e.key) or e.key = any($2)
    $q$ into кандидаты using кандидаты, с_размером;
  end if;
  if кандидаты = '{}' then
    return;
  end if;

  select coalesce(jsonb_agg(to_jsonb(р) order by р.r, р.weight desc, char_length(р.code), р.code), '[]')
    into коды
    from (
      with c as (
        select e.key as code, e.value::int as r
          from jsonb_each(кандидаты) e
         order by e.value::int, char_length(e.key), e.key
         limit предел + 1
      ), спрос as materialized (
        select c.code, x.deal_id, x.part_number, x.item_name, x.oem
          from c cross join lateral (
            select d.deal_id, d.part_number, d.item_name, d.oem
              from lib_demand_live d where lib_pn_key(d.part_number) = c.code limit 5000) x
      ), кп as materialized (
        select c.code, x.part_number, x.item_name, x.oem, x.rfq_company
          from c cross join lateral (
            select p.part_number, p.item_name, p.oem, p.rfq_company
              from lib_prices p
             where p.feed = 'разбор КП' and lib_pn_key(p.part_number) = c.code limit 5000) x
      ), dem as (
        select s.code, count(*)::int as rows, count(distinct s.deal_id)::int as deals,
               mode() within group (order by s.part_number) as written,
               left(mode() within group (order by s.item_name), 160) as name,
               count(*) >= 5000 as capped
          from спрос s group by s.code
      ), prc as (
        select k.code, count(*)::int as rows, count(distinct k.rfq_company)::int as sups,
               mode() within group (order by k.part_number) as written,
               left(mode() within group (order by k.item_name), 160) as name,
               count(*) >= 5000 as capped
          from кп k group by k.code
      ), нап as (
        select z.code, jsonb_agg(jsonb_build_array(z.s, z.n) order by z.n desc, z.s) as spellings
          from (select y.code, y.s, sum(y.n)::int as n,
                       row_number() over (partition by y.code order by sum(y.n) desc, y.s) as место
                  from (select s.code, btrim(s.oem) as s, 1 as n from спрос s where coalesce(btrim(s.oem), '') <> ''
                        union all
                        select k.code, btrim(k.oem), 1 from кп k where coalesce(btrim(k.oem), '') <> '') y
                 group by y.code, y.s) z
         where z.место <= 5
         group by z.code
      ), cat as (
        select c.code, count(*)::int as parts,
               (array_agg(p.catalog_no order by (p.id = c.code) desc, p.id))[1] as catalog_no,
               (array_agg(p.name order by (p.id = c.code) desc, p.id))[1] as name,
               (array_agg(btrim(p.oem) order by (p.id = c.code) desc, p.id)
                  filter (where coalesce(btrim(p.oem), '') <> ''))[1] as oem,
               (array_agg(p.kv_no order by (p.id = c.code) desc, p.id)
                  filter (where p.kv_no is not null))[1] as kv_no
          from c join lib_parts p on p.id = c.code or lib_pn_key(p.catalog_no) = c.code
         group by c.code
      ), alts as (
        select c.code, count(*)::int as n from c join lib_part_alt a on a.part_id = c.code group by c.code
      ), ana as (
        select c.code, count(*)::int as n,
               (array_agg(a.alt_pn order by a.part_id, a.kind))[1] as written,
               (array_agg(p.catalog_no order by a.part_id, a.kind))[1] as of_catalog,
               (array_agg(a.kind order by a.part_id, a.kind))[1] as kind,
               (array_agg(btrim(a.alt_maker) order by a.part_id, a.kind)
                  filter (where coalesce(btrim(a.alt_maker), '') <> ''))[1] as maker
          from c join lib_part_alt a on lib_pn_key(a.alt_pn) = c.code
          join lib_parts p on p.id = a.part_id
         group by c.code
      )
      select c.code, c.r,
             coalesce(cat.catalog_no, prc.written, dem.written, ana.written, c.code) as title,
             nullif(concat_ws(' · ', coalesce(cat.name, dem.name, prc.name),
                              case when ana.n > 0 then 'аналог к ' || ana.of_catalog
                                                       || coalesce(' (' || ana.kind || ')', '') end,
                              case when cat.kv_no is not null then 'наш номер ' || cat.kv_no end), '') as subtitle,
             cat.oem as part_oem, ana.maker as alt_maker, нап.spellings,
             jsonb_strip_nulls(jsonb_build_object(
               'deals', dem.deals, 'demand_rows', dem.rows, 'offers', prc.rows, 'suppliers', prc.sups,
               'catalog', cat.parts, 'analogs', alts.n, 'analog_of', ana.n,
               'capped', case when dem.capped or prc.capped then true end)) as counts,
             concat_ws(' · ', case when dem.rows > 0 then 'спрос' end, case when prc.rows > 0 then 'КП' end,
                       case when cat.parts > 0 then 'каталог' end, case when ana.n > 0 then 'аналог' end) as source,
             coalesce(dem.deals, 0) + coalesce(prc.rows, 0) + 5 * coalesce(cat.parts, 0)
               + coalesce(ana.n, 0) as weight
        from c
        left join dem on dem.code = c.code
        left join prc on prc.code = c.code
        left join нап on нап.code = c.code
        left join cat on cat.code = c.code
        left join alts on alts.code = c.code
        left join ana on ana.code = c.code
       where coalesce(dem.rows, 0) + coalesce(prc.rows, 0) + coalesce(cat.parts, 0) + coalesce(ana.n, 0) > 0
    ) р;

  -- Бренд по реестру: все написания кодов разом, одним обращением к карте.
  if реестр and коды <> '[]' then
    написания := array(
      select distinct s from (
        select x.value ->> 'part_oem' as s from jsonb_array_elements(коды) x
        union all
        select x.value ->> 'alt_maker' from jsonb_array_elements(коды) x
        union all
        -- У кода без написаний здесь JSON null, а не пусто: coalesce его не берёт.
        select sp.value ->> 0 from jsonb_array_elements(коды) x
          cross join lateral jsonb_array_elements(
            case when jsonb_typeof(x.value -> 'spellings') = 'array' then x.value -> 'spellings'
                 else '[]'::jsonb end) sp
      ) y where coalesce(btrim(s), '') <> '');
    execute $q$
      select coalesce(jsonb_object_agg(u.s, jsonb_build_array(b.brand_key, b.name)), '{}')
        from unnest($1::text[]) as u(s)
        join lib_brand_map m on m.spelling_key = lib_brand_key(u.s)
        join lib_brands b on b.brand_key = m.brand_key
    $q$ into бренды using написания;
  end if;

  return query
    select 'код'::text, x.code, x.title, x.subtitle,
           coalesce(бр.name, x.part_oem, x.spellings -> 0 ->> 0, x.alt_maker),
           бр.key,
           case when бр.key is not null then бр.src
                when x.part_oem is not null then 'каталог'
                when x.spellings -> 0 is not null then 'написание'
                when x.alt_maker is not null then 'аналог' end,
           null::text, x.counts, x.source, x.r
      from jsonb_to_recordset(коды) as x(code text, r int, title text, subtitle text, part_oem text,
                                         alt_maker text, spellings jsonb, counts jsonb, source text,
                                         weight int)
      left join lateral (
        -- Сначала изготовитель по каталогу, потом самый частый бренд реестра
        -- среди написаний спроса и КП (частоты написаний одного бренда
        -- складываются), потом изготовитель аналога.
        select v.key, v.name, v.src
          from (select бренды -> x.part_oem ->> 0 as key, бренды -> x.part_oem ->> 1 as name,
                       'каталог'::text as src, 0 as ord, 0 as n
                union all
                select бренды -> (sp.e ->> 0) ->> 0, бренды -> (sp.e ->> 0) ->> 1, 'частота', 1,
                       sum((sp.e ->> 1)::int) over (partition by бренды -> (sp.e ->> 0) ->> 0)
                  from jsonb_array_elements(coalesce(x.spellings, '[]')) as sp(e)
                union all
                select бренды -> x.alt_maker ->> 0, бренды -> x.alt_maker ->> 1, 'аналог', 2, 0) v
         where v.key is not null
         order by v.ord, v.n desc, v.key
         limit 1) бр on true
     order by x.r, x.weight desc, char_length(x.code), x.code
     limit предел;
end $fn$;

-- Права: функции по умолчанию исполнимы всеми (PUBLIC) — снимаем; роли
-- платформы только через проверку наличия (правило 20). Исполняет сервисный
-- ключ воркера портала, за Cloudflare Access и правом suppliers.
revoke all on function portal_search(text, int) from public;
revoke all on function portal_prefix_hi(text) from public;
do $$
declare
  кому text;
  сервис text;
begin
  select string_agg(quote_ident(rolname), ', ') into кому
    from pg_roles where rolname in ('anon', 'authenticated');
  select string_agg(quote_ident(rolname), ', ') into сервис
    from pg_roles where rolname = 'service_role';
  if кому is not null then
    execute format('revoke all on function portal_search(text, int) from %s', кому);
    execute format('revoke all on function portal_prefix_hi(text) from %s', кому);
  end if;
  if сервис is not null then
    execute format('grant execute on function portal_search(text, int) to %s', сервис);
    execute format('grant execute on function portal_prefix_hi(text) to %s', сервис);
  end if;
end $$;
commit;
