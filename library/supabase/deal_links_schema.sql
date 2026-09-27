-- СКВОЗНАЯ ЦЕПОЧКА СВОДА: карточка запроса СП-166 → сделка → заказчик.
--
-- ЗАЧЕМ. Недельный свод (scripts/weekly_offers.py) идёт «КП поставщика → запрос
-- → сделка → заказчик → наше КП заказчику». В библиотеке не хранилось, какой
-- сделке служит карточка запроса (parentId2 СП-166 не читался; у файлов
-- карточки lib_files.deal_id и lib_prices.rfq_id — номер самой карточки), и кто
-- заказчик сделки (название и компания сделки не хранились нигде).
--
-- ОТДЕЛЬНЫЕ ТАБЛИЦЫ, А НЕ КОЛОНКА lib_files (library/deal_links.py): связь —
-- свойство карточки, а не файла (один файл висит на нескольких карточках, у
-- карточки без КП файла нет вовсе), разбор и переразбор переписывают строку
-- файла целиком, а откат по ключу прогона снимает свои строки, не трогая файлы.
--
-- КЛЮЧИ — ТЕКСТ ИЗ ЦИФР, как lib_files.deal_id, lib_prices.rfq_id и
-- lib_prices.rfq_company: соединения по индексам без приведения типов.
--
--   lib_rfq_cards  card_id → deal_id (parentId2), title, created_at (createdTime)
--   lib_deals      deal_id → title, company_id (COMPANY_ID), stage, created_at
--   run_id         ключ прогона, записавшего строку (правило 6): разбор ставит
--                  свой при вставке и при ИЗМЕНЕНИИ строки, досчёт истории
--                  (library/backfill_deal_links.py) — свой только при вставке
--                  новой; откат досчёта снимает ровно вставленные им строки.
--   seen_at        когда строку последний раз подтвердил портал.
--
-- ВИД lib_deal_customer — сделка с именем заказчика ИЗ РЕЕСТРА КОМПАНИЙ, без
-- нового обхода компаний портала: company_id → sup_identifier (kind 'bitrix')
-- → корень цепочки слияний → sup_name_shown (через функцию lib_имена_компаний:
-- вид поверх того вида ронял бы схему поставщиков). Реестр собран по поставщикам,
-- поэтому у заказчика, которого там нет, имя пусто, а company_id есть — пусто
-- видно числом (досчёт печатает охват), а не выдумывается. Без схемы
-- поставщиков вид всё равно создаётся — с пустым именем: читатель вида не
-- должен падать от порядка применения файлов.
-- Вид lib_rfq_chain — карточка запроса со сделкой и заказчиком одной строкой.
--
-- ПОРЯДОК (правило 10): таблицы — ради них миграция; виды после.
-- Идемпотентно, в любую схему (search_path). Применяется прогоном «ZIP base —
-- apply DB migrations» (zip-db.yml) после suppliers_schema.sql.

set statement_timeout = '5min';
set lock_timeout      = '10s';   -- правило 12: ALTER TABLE без него встаёт в очередь

create table if not exists lib_rfq_cards (
  card_id    text primary key,
  deal_id    text,
  title      text,
  created_at timestamptz,
  run_id     text not null,
  seen_at    timestamptz not null default now()
);

create table if not exists lib_deals (
  deal_id    text primary key,
  title      text,
  company_id text,
  stage      text,
  created_at timestamptz,
  run_id     text not null,
  seen_at    timestamptz not null default now()
);

-- «create table if not exists» существующую таблицу не меняет (правило 21):
-- колонки, добавленные позже, идут и отдельными операторами.
alter table lib_rfq_cards add column if not exists deal_id    text;
alter table lib_rfq_cards add column if not exists title      text;
alter table lib_rfq_cards add column if not exists created_at timestamptz;
alter table lib_rfq_cards add column if not exists seen_at    timestamptz not null default now();
alter table lib_deals add column if not exists title      text;
alter table lib_deals add column if not exists company_id text;
alter table lib_deals add column if not exists stage      text;
alter table lib_deals add column if not exists created_at timestamptz;
alter table lib_deals add column if not exists seen_at    timestamptz not null default now();

-- Номера — только цифры без ведущего нуля: «0» портала значит «нет» и пишется
-- как null (deal_links.номер). Снимаются и ставятся заново (правило 21).
alter table lib_rfq_cards drop constraint if exists lib_rfq_cards_ids_chk;
alter table lib_rfq_cards add constraint lib_rfq_cards_ids_chk
  check (card_id ~ '^[1-9][0-9]*$' and (deal_id is null or deal_id ~ '^[1-9][0-9]*$'));
alter table lib_deals drop constraint if exists lib_deals_ids_chk;
alter table lib_deals add constraint lib_deals_ids_chk
  check (deal_id ~ '^[1-9][0-9]*$' and (company_id is null or company_id ~ '^[1-9][0-9]*$'));

create index if not exists lib_rfq_cards_deal on lib_rfq_cards (deal_id) where deal_id is not null;
create index if not exists lib_rfq_cards_run  on lib_rfq_cards (run_id);
create index if not exists lib_deals_company  on lib_deals (company_id) where company_id is not null;
create index if not exists lib_deals_run      on lib_deals (run_id);

-- Сделка с заказчиком. Имя — из реестра, если он есть в этой схеме.
do $$
begin
  execute 'drop view if exists lib_rfq_chain';
  execute 'drop view if exists lib_deal_customer';
  execute 'drop function if exists lib_имена_компаний()';
  if to_regclass(current_schema() || '.sup_identifier') is not null
     and to_regclass(current_schema() || '.sup_entity') is not null
     and to_regclass(current_schema() || '.sup_name_shown') is not null then
    -- ИМЯ — ФУНКЦИЕЙ, А НЕ СОЕДИНЕНИЕМ С ВИДОМ sup_name_shown. Схема поставщиков
    -- пересобирает тот вид drop + create (tests/test_schema_views_droppable.py), и
    -- вид поверх него ронял её повторное применение — zip-db, supplier-merge,
    -- suppliers-names: «cannot drop view sup_name_shown because other objects
    -- depend on it» (та же ловушка — у lib_prices_live в schema_junk.sql). Тело
    -- функции на языке sql имя вида разрешает при вызове и зависимости в базе не
    -- пишет, а планировщик встраивает его в запрос обычным подзапросом.
    execute $f$
      create function lib_имена_компаний()
        returns table (sup_id text, name text, name_source text)
        language sql stable
        as $b$ select n.sup_id::text, n.name::text, n.name_source::text from sup_name_shown n $b$
    $f$;
    execute $v$
      create view lib_deal_customer with (security_invoker = true) as
      with recursive walk (id, root, depth) as (
        select id, id, 0 from sup_entity where merged_into is null
        union all
        select e.id, w.root, w.depth + 1
          from sup_entity e join walk w on e.merged_into = w.id
         where w.depth < 20
      ),
      bx as (
        -- Один номер портала — одна сущность: сначала проверенная, потом по номеру.
        select distinct on (i.value_norm) i.value_norm as company_id,
               coalesce(w.root, i.sup_id) as sup_id
          from sup_identifier i
          left join walk w on w.id = i.sup_id
         where i.kind = 'bitrix' and i.status <> 'rejected'
         order by i.value_norm, (i.status = 'verified') desc, i.sup_id
      )
      select d.deal_id, d.title, d.company_id, d.stage, d.created_at,
             b.sup_id                                         as company_sup_id,
             n.name                                           as company_title,
             case when n.name is not null then 'реестр компаний: ' || n.name_source end
                                                              as company_title_src,
             d.run_id, d.seen_at
        from lib_deals d
        left join bx b on b.company_id = d.company_id
        left join lib_имена_компаний() n on n.sup_id = b.sup_id
    $v$;
  else
    execute $v$
      create view lib_deal_customer with (security_invoker = true) as
      select d.deal_id, d.title, d.company_id, d.stage, d.created_at,
             null::text as company_sup_id, null::text as company_title,
             null::text as company_title_src, d.run_id, d.seen_at
        from lib_deals d
    $v$;
  end if;
  execute $v$
    create view lib_rfq_chain with (security_invoker = true) as
    select c.card_id, c.title as card_title, c.created_at as card_created_at,
           c.deal_id, d.title as deal_title, d.stage as deal_stage,
           d.created_at as deal_created_at, d.company_id, d.company_sup_id,
           d.company_title, d.company_title_src
      from lib_rfq_cards c
      left join lib_deal_customer d on d.deal_id = c.deal_id
  $v$;
end $$;

-- Роли Supabase — только через проверку наличия (правило 20): на чистом
-- PostgreSQL их нет, и «revoke … from anon» уронил бы файл целиком. Названия
-- сделок и номера компаний — коммерческие сведения: браузеру не выдаются.
do $$
declare кому text;
declare служебная text;
begin
  select string_agg(quote_ident(r.rolname), ', ') into кому
    from pg_roles r where r.rolname in ('anon', 'authenticated');
  select string_agg(quote_ident(r.rolname), ', ') into служебная
    from pg_roles r where r.rolname = 'service_role';
  execute 'revoke all on lib_rfq_cards, lib_deals, lib_deal_customer, lib_rfq_chain from public';
  if кому is not null then
    execute format('revoke all on lib_rfq_cards, lib_deals, lib_deal_customer, lib_rfq_chain from %s',
                   кому);
  end if;
  if служебная is not null then
    execute format('grant select on lib_rfq_cards, lib_deals, lib_deal_customer, lib_rfq_chain to %s',
                   служебная);
  end if;
  -- Функция имён есть только при схеме поставщиков; права — те же, что у видов.
  if to_regprocedure('lib_имена_компаний()') is not null then
    execute 'revoke all on function lib_имена_компаний() from public';
    if кому is not null then
      execute format('revoke all on function lib_имена_компаний() from %s', кому);
    end if;
    if служебная is not null then
      execute format('grant execute on function lib_имена_компаний() to %s', служебная);
    end if;
  end if;
end $$;
