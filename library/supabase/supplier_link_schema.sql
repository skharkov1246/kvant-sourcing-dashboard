-- СВЯЗЬ ДВУХ РЕЕСТРОВ ПОСТАВЩИКОВ — шаг 4 плана «одна стартовая страница»
-- (25.09.2026). Мерило шага — доля поставщиков разведки, сведённых с реестром
-- компаний.
--
-- ЗАЧЕМ. Поставщики живут в двух реестрах без связи между ними:
--   · реестр компаний портала — sup_entity с вечным номером KV-S и признаками
--     sup_identifier (ИНН, домен, написания, companyId Битрикса);
--   · реестр разведки — lib_suppliers (кто делает, ОЕМ, дистрибьютор) и ребро
--     «деталь → исполнитель» lib_part_suppliers с проверкой наличия.
-- Следствие: строка «кто делает» на карточке кода ведёт в никуда, а поставщик
-- разведки, которого давно знает Битрикс, выглядит чужим.
--
-- ПОЧЕМУ ТАБЛИЦА-СПУТНИК, А НЕ ПРИЗНАК В sup_identifier. Там есть вид reestr
-- («ключ в одном из реестров»), но у признаков один писатель — сведение
-- (library/load_supplier_master.py): только оно держит в памяти запрет «такой
-- признак уже у другой сущности — не воруем» (см. пояснение к sup_display_name
-- в suppliers_schema.sql). Второй писатель в обход него — путь к одному
-- признаку у двух сущностей. К тому же связь несёт то, чего у признака нет:
-- правило, уверенность, кандидатство и спор. И откат здесь — ПОМЕТКОЙ, а откат
-- сведения (rollback_run.sql) признаки удаляет.
--
-- ЧТО ЗДЕСЬ ПИШЕТСЯ (library/supplier_link.py, APPLY=1): пара «поставщик
-- разведки → сущность реестра» с правилом, уверенностью и ключом прогона.
--   status = link      — сильный ключ, закрытым списком: ИНН или VAT точно,
--                        домен сайта или почты точно, кроме общих доменов
--                        (почтовые хостинги, площадки — закрытый список в
--                        scripts/supplier_registry_overlap.py). Только их
--                        читает портал;
--   status = candidate — совпало лишь имя (то же правило norm_name, каким
--                        сведение пишет написания). Кандидат ждёт человека и
--                        связью не становится никогда: это держит проверка
--                        sup_research_link_name_not_link ниже;
--   status = conflict  — сильный ключ есть, но ведёт в две разные сущности
--                        или по домену, который носит слишком много разных
--                        поставщиков разведки. Виден числом, связью не идёт.
-- Слияния и удаления здесь нет: ни одна строка реестров не меняется.
--
-- ДЕЙСТВУЮЩИЙ ПРОГОН — последний не откаченный. Прогон пишет картину целиком
-- (одной транзакцией), поэтому «действующие связи» — это строки прогона, чья
-- строка самая новая среди не откаченных. Откат ставит rolled_back_at (правило
-- 5 — пометка, а не удаление), и действующим снова становится прежний прогон,
-- как у sup_display_name.
--
-- ЗАВИСИМОСТИ. sup_entity (suppliers_schema.sql) и lib_suppliers (schema.sql)
-- обязаны стоять раньше: связь без них бессмысленна, и файл падает с названной
-- причиной. Функции портала (portal_entity_schema.sql) этот файл НЕ требуют:
-- они спрашивают о виде sup_research_link_live при вызове, и без него
-- работают как раньше. Применение — Actions → «ZIP base — apply DB migrations»
-- (zip-db.yml), после suppliers_schema.sql, до portal_*; и прогон «Поставщики —
-- связь разведки с реестром» перед записью.
--
-- ИДЕМПОТЕНТНО: применяется повторно без ошибок. Роли Supabase — только через
-- проверку наличия (правило 20).

set statement_timeout = '5min';
set lock_timeout      = '30s';   -- ALTER TABLE без него ставит ACCESS EXCLUSIVE в очередь

do $$
begin
  if to_regclass(current_schema() || '.sup_entity') is null
     or to_regclass(current_schema() || '.lib_suppliers') is null then
    raise exception 'связь реестров требует sup_entity (suppliers_schema.sql) и lib_suppliers (schema.sql) — примените их раньше';
  end if;
  if to_regprocedure(current_schema() || '.sup_роли_которые_есть(text[])') is null then
    raise exception 'нет sup_роли_которые_есть — примените suppliers_schema.sql раньше';
  end if;
end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Пара «поставщик разведки → сущность реестра».
--
--    research_key — lib_suppliers.name_key в момент записи. Страховка от чужого
--    номера: пересоберут таблицу разведки с новыми id — связь с несовпавшим
--    ключом читатели не возьмут, а не покажут чужую компанию.
--    sup_id — сущность, которая НЕСЁТ совпавший признак, а не корень цепочки
--    слияний: корень считает вид ниже на чтении, и слияние, случившееся после
--    прогона, подхватывается без нового прогона.
--    evidence — совпавшее значение (домен, ИНН, ключ имени). Только в базе, за
--    входом; в журнал прогона не идёт никогда (правило 17).
--    note — почему не связь: у кандидата и спора.
--
--    ON DELETE CASCADE по обеим ссылкам: откат сведения (rollback_run.sql)
--    удаляет сущности, и связь с удалённой сущностью значила бы связь с
--    номером, который следующий прогон может выдать другой компании.
create table if not exists sup_research_link (
  id             bigserial primary key,
  research_id    bigint not null references lib_suppliers(id) on delete cascade,
  research_key   text,
  sup_id         text   not null references sup_entity(id) on delete cascade,
  rule           text   not null,
  status         text   not null,
  confidence     numeric(3,2) not null check (confidence between 0 and 1),
  evidence       text,
  note           text,
  run_id         text   not null,   -- без ключа прогона откат невозможен (правило 6)
  created_at     timestamptz not null default now(),
  rolled_back_at timestamptz        -- откат: пометка, а не удаление (правило 5)
);

-- Списки видов — отдельным alter при каждом применении, а не только в create:
-- «create table if not exists» существующую таблицу не меняет, и новый вид на
-- живой базе упал бы на вставке, пройдя все проверки на свежей (правило 21).
do $$
begin
  alter table sup_research_link drop constraint if exists sup_research_link_rule_check;
  alter table sup_research_link add constraint sup_research_link_rule_check
    check (rule in ('инн', 'vat', 'домен сайта', 'домен почты', 'имя+страна', 'имя'));
  alter table sup_research_link drop constraint if exists sup_research_link_status_check;
  alter table sup_research_link add constraint sup_research_link_status_check
    check (status in ('link', 'candidate', 'conflict'));
  -- Имя связью не бывает НИКОГДА — только кандидатом. Правило держит база, а
  -- не одна память писателя: второй писатель тоже упрётся.
  alter table sup_research_link drop constraint if exists sup_research_link_name_not_link;
  alter table sup_research_link add constraint sup_research_link_name_not_link
    check (status <> 'link' or rule in ('инн', 'vat', 'домен сайта', 'домен почты'));
end $$;

create unique index if not exists sup_research_link_once
  on sup_research_link (run_id, research_id, sup_id, rule);
create index if not exists sup_research_link_run on sup_research_link (run_id);
create index if not exists sup_research_link_research
  on sup_research_link (research_id) where rolled_back_at is null;
create index if not exists sup_research_link_sup
  on sup_research_link (sup_id) where rolled_back_at is null;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Действующие связи — одно место для всех читателей (портал, замер).
--
--    Одна строка на поставщика разведки, и только если все его связи
--    действующего прогона ведут в ОДИН корень цепочки слияний. Два корня —
--    спор, и строки нет: читатель покажет его как прежде, без ссылки.
--    Корень — как у portal_companies: A → B → C даёт C, не больше двадцати
--    шагов; круг или цепочка длиннее — сущность остаётся собой.
--    rule — сильнейшее правило (по уверенности), rules — все сработавшие.
--
--    Сначала уронить, потом создать: «create or replace view» не меняет состав
--    колонок в середине, и падает ровно живая база, где вид уже стоит.
drop view if exists sup_research_link_live;
create view sup_research_link_live with (security_invoker = true) as
with recursive прогон as (
  select l.run_id from sup_research_link l
   where l.rolled_back_at is null
   order by l.id desc limit 1
), связи as (
  select l.research_id, l.sup_id, l.rule, l.confidence
    from sup_research_link l
    join прогон p on p.run_id = l.run_id
    join lib_suppliers s on s.id = l.research_id and s.name_key is not distinct from l.research_key
   where l.rolled_back_at is null and l.status = 'link'
), вверх (research_id, start, id, depth) as (
  select distinct с.research_id, с.sup_id, с.sup_id, 0 from связи с
  union all
  select в.research_id, в.start, e.merged_into, в.depth + 1
    from вверх в join sup_entity e on e.id = в.id
   where e.merged_into is not null and в.depth < 20
), корни as (
  select distinct on (в.research_id, в.start) в.research_id, в.start,
         case when e.merged_into is null then в.id else в.start end as root
    from вверх в join sup_entity e on e.id = в.id
   order by в.research_id, в.start, в.depth desc
)
select с.research_id,
       min(к.root)                                              as sup_id,
       (array_agg(с.rule order by с.confidence desc, с.rule))[1] as rule,
       max(с.confidence)                                         as confidence,
       array_agg(distinct с.rule order by с.rule)                as rules
  from связи с
  join корни к on к.research_id = с.research_id and к.start = с.sup_id
 group by с.research_id
having count(distinct к.root) = 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Доступ — как у всего реестра компаний: RLS плюс FORCE, ничего у anon,
--    authenticated и PUBLIC, чтение и запись — сервисному ключу.
--
--    МЕТКА ТА ЖЕ, ЧТО У СХЕМЫ ПОСТАВЩИКОВ (suppliers_schema:v1). Проверка
--    блока 10 suppliers_schema.sql ищет таблицы sup_* без этой метки и роняет
--    применение: с другой меткой следующий прогон миграций упал бы на ней.
--    Метка значит «таблица реестра поставщиков под его защитой» — это правда.
do $$
declare
  сх        text := current_schema();
  кому      text := sup_роли_которые_есть(array['anon', 'authenticated']);
  служебная text := sup_роли_которые_есть(array['service_role']);
begin
  execute format('comment on table %I.sup_research_link is %L', сх, 'suppliers_schema:v1');
  execute format('alter table %I.sup_research_link enable row level security', сх);
  execute format('alter table %I.sup_research_link force  row level security', сх);
  execute format('revoke all on table %I.sup_research_link from public', сх);
  execute format('revoke all on table %I.sup_research_link_live from public', сх);
  execute format('revoke all on sequence %I.sup_research_link_id_seq from public', сх);
  if кому is not null then
    execute format('revoke all on table %I.sup_research_link from %s', сх, кому);
    execute format('revoke all on table %I.sup_research_link_live from %s', сх, кому);
    execute format('revoke all on sequence %I.sup_research_link_id_seq from %s', сх, кому);
  end if;
  if служебная is not null then
    execute format('grant select, insert, update on table %I.sup_research_link to %s', сх, служебная);
    execute format('grant select on table %I.sup_research_link_live to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.sup_research_link_id_seq to %s', сх, служебная);
  end if;
end $$;

-- Проверка после применения: падаем громко, а не оставляем открытую таблицу.
do $$
declare открыто int;
begin
  select count(*) into открыто
    from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
   where ns.nspname = current_schema() and c.relname = 'sup_research_link'
     and (not c.relrowsecurity or not c.relforcerowsecurity);
  if открыто > 0 then
    raise exception 'sup_research_link без FORCE RLS';
  end if;
  select count(*) into открыто
    from information_schema.role_table_grants g
   where g.table_schema = current_schema()
     and g.table_name in ('sup_research_link', 'sup_research_link_live')
     and g.grantee in ('anon', 'authenticated', 'PUBLIC');
  if открыто > 0 then
    raise exception 'у anon/authenticated/PUBLIC есть % прав на связь реестров', открыто;
  end if;
end $$;
