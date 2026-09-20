-- КАНОНИЧЕСКИЙ РЕЕСТР ПОСТАВЩИКОВ — ядро раздела /suppliers.
--
-- Зачем. Поставщики живут в шести несведённых реестрах с шестью разными ключами:
-- supplier_stats по тексту названия, lib_suppliers по norm(имя), pnw по слагу от
-- сайта, плюс досье ГТУ, продавцы заявки и CRM ЗИП. Одна компания в трёх
-- написаниях — три «поставщика», и статистика по ней расщеплена. ТЗ запрещает
-- вторую копию истины; здесь она сводится в одну, и ни одно имя не
-- перезаписывается: совпадения живут строками sup_identifier с доказательством.
--
-- Почему здесь, а не в репозитории: контакты, цены и условия — коммерческие
-- данные, а репозиторий публичный (решение владельца от 20.09.2026: данные
-- только за входом, в git — ничего).
--
-- Почему в контуре библиотеки, а не base/: контур base/ уничтожающий —
-- base/export_kb.py:141 ставит DROP TABLE на каждую из 12 таблиц, build_db.py
-- делает DELETE FROM по шести таблицам ядра, supplier_stats роняется целиком.
-- Подтверждённая человеком корректировка, положенная туда, исчезает безвозвратно.
--
-- Идемпотентно: можно прогонять повторно. Ничего не удаляет.
-- Применение — отдельным ручным workflow, НЕ автоматически: схема заводит
-- таблицы, но сведение реестров делается только после замера
-- (Actions → «Поставщики — замер сведения»), правило 3 CLAUDE.md.

set statement_timeout = '15min';
set lock_timeout      = '60s';   -- ALTER TABLE без него ставит ACCESS EXCLUSIVE в очередь

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Личность. Вечный бессмысленный ключ по регламенту pnw/НУМЕРАЦИЯ.md:
--    KV-S-NNNNNN-C для юрлица, KV-G-NNNNNN-C для корпоративной группы,
--    контрольная цифра Луна. Номер не несёт ни страны, ни имени, ни домена —
--    всё, что может измениться, живёт в атрибутах. Обоснование регламента дано
--    на наших же случаях: ребрендинг Atlas Copco → Epiroc не меняет артикул,
--    но сменил бы номер, привязанный к бренду, а он уже в договорах.
create table if not exists sup_entity (
  id            text primary key
                check (id ~ '^KV-[SG]-[0-9]{6}-[0-9]$'),
  kind          text not null check (kind in ('legal', 'group')),
  parent_id     text references sup_entity(id),
  display_name  text not null,          -- для человека; НЕ ключ и не основание слияния
  country       text,
  city          text,
  roles         text[],                 -- manufacturer|distributor|trader|service|logistics
  status        text not null default 'active'
                check (status in ('active', 'restricted', 'blocked', 'inactive')),
  first_seen    date,
  last_activity date,
  resolution    text not null default 'candidate'
                check (resolution in ('candidate', 'resolved', 'merged')),
  merged_into   text references sup_entity(id),   -- при resolution='merged'
  confidence    numeric(3,2) check (confidence between 0 and 1),
  note          text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists sup_entity_kind    on sup_entity (kind, status);
create index if not exists sup_entity_country on sup_entity (country);
create index if not exists sup_entity_merged  on sup_entity (merged_into)
  where merged_into is not null;

-- 2. Номера-спутники. У поставщика один KV и сколько угодно чужих — по образцу
--    спутников детали (свой / бренд / изготовитель / поставщик / аналог /
--    заказчика). Все участвуют в поиске. Слияние «ABC GmbH» и «ABC Germany» —
--    это строка kind='alias' со ссылкой на доказательство, а НЕ перезапись
--    display_name: тогда слияние обратимо снятием строки.
create table if not exists sup_identifier (
  sup_id     text not null references sup_entity(id) on delete cascade,
  kind       text not null check (kind in (
               'bitrix',    -- companyId портала: стабильный ID, уже есть в rfq.supplier_id
               'inn', 'vat', 'ogrn',
               'domain',    -- домен сайта и почты
               'legal', 'trading', 'alias',
               'reestr')),  -- ключ в одном из шести существующих реестров
  value      text not null,
  value_norm text not null,   -- ключ поиска: буквы и цифры, верхний регистр
  source     text not null,   -- откуда взято: имя набора или таблицы
  evidence   text,            -- ссылка на документ, строку, страницу
  status     text not null default 'stated'
             check (status in ('verified', 'stated', 'inferred', 'candidate', 'rejected')),
  valid_from date,
  valid_to   date,
  run_id     text not null,   -- без ключа прогона откат невозможен (правило 6)
  created_at timestamptz not null default now(),
  primary key (sup_id, kind, value_norm)
);
create index if not exists sup_identifier_norm   on sup_identifier (value_norm);
create index if not exists sup_identifier_kind   on sup_identifier (kind, value_norm);
create index if not exists sup_identifier_run    on sup_identifier (run_id);
-- Один и тот же bitrix companyId не может принадлежать двум сущностям: это
-- системный идентификатор портала, и его раздвоение означало бы ошибку сведения.
create unique index if not exists sup_identifier_bitrix_once
  on sup_identifier (value_norm) where kind = 'bitrix' and status <> 'rejected';

-- 3. Провенанс. Каждый существенный факт несёт, откуда он и насколько ему верить.
--    Без этого следующая ошибка снова будет неизмеримой (правило 16).
create table if not exists sup_fact (
  id           bigserial primary key,
  subject_kind text not null,        -- entity|contact|quote_line|part_supplier|term
  subject_id   text not null,
  field        text not null,
  value        jsonb not null,
  scope        jsonb,                -- {our_entity_id, pn, currency, …}
  status       text not null check (status in (
                 'verified', 'stated', 'inferred', 'candidate', 'rejected', 'superseded')),
  source_type  text not null check (source_type in (
                 'bitrix', 'document', 'web', 'manual', 'derived')),
  source_id    text,
  source_span  jsonb,                -- страница, ячейка, смещение в тексте
  source_at    timestamptz,
  ingested_at  timestamptz not null default now(),
  method       text not null,        -- имя правила
  method_ver   text not null,        -- версия правила: без неё прогоны несравнимы
  confidence   numeric(3,2) check (confidence between 0 and 1),
  verified_by  text,
  verified_at  timestamptz,
  supersedes   bigint references sup_fact(id),
  run_id       text not null
);
create index if not exists sup_fact_subject on sup_fact (subject_kind, subject_id, field);
create index if not exists sup_fact_run     on sup_fact (run_id);
create index if not exists sup_fact_status  on sup_fact (status)
  where status in ('candidate', 'inferred');

-- 4. Слой человека. Пометка живёт в таблице-спутнике, а не колонкой основной
--    таблицы — образец lib_row_junk: ошибочную пометку снимают одной командой,
--    а UPDATE сотен тысяч строк раздувает таблицу и не укладывается в таймаут.
create table if not exists sup_override (
  id              bigserial primary key,
  subject_kind    text not null,
  subject_id      text not null,
  field           text not null,
  source_value    jsonb,
  corrected_value jsonb not null,
  author          text not null,
  reason          text not null,
  evidence        text,
  scope           jsonb,
  approved_by     text,
  approved_at     timestamptz,
  conflict        text not null default 'none'
                  check (conflict in ('none', 'pending', 'resolved')),
  version         int not null default 1,   -- optimistic locking
  run_id          text not null,
  created_at      timestamptz not null default now()
);
-- Одна действующая корректировка на факт: вторая означала бы, что двое
-- перезаписали друг друга молча.
create unique index if not exists sup_override_one_active
  on sup_override (subject_kind, subject_id, field)
  where conflict <> 'resolved';
create index if not exists sup_override_conflict on sup_override (conflict)
  where conflict = 'pending';

-- 5. Очередь проверки. На ручную проверку идёт НЕ каждое поле, а приоритетное:
--    неоднозначное сведение, неизвестная валюта, противоречие условий, дорогая
--    котировка, расхождение суффикса артикула, чувствительное изменение.
create table if not exists sup_review (
  id           bigserial primary key,
  kind         text not null check (kind in (
                 'ambiguous_match', 'currency_unknown', 'unit_unknown', 'term_conflict',
                 'high_value', 'pn_suffix', 'entity_uncertain', 'low_conf_link',
                 'override_conflict', 'sensitive_change')),
  subject_kind text,
  subject_id   text,
  priority     int not null default 5 check (priority between 1 and 9),
  payload      jsonb not null,
  opened_at    timestamptz not null default now(),
  closed_at    timestamptz,
  closed_by    text,
  outcome      text,
  run_id       text not null
);
-- ДОБАВЛЕНИЕ ВИДА В СПИСОК — отдельным alter, а не правкой create выше.
-- «create table if not exists» существующую таблицу не меняет: у неё останется
-- прежний check, и вставка нового вида упадёт на живой базе, пройдя все тесты
-- на свежей. Поэтому список видов приводится к текущему явно, при каждом прогоне.
--
-- different_legal_form появился 20.09.2026 вместе с запретом сливать «ООО
-- Ромашка» и «АО Ромашка»: norm_name правовую форму вырезает, после неё имена
-- неотличимы, а юрлица разные. Складывать такие случаи в entity_uncertain можно,
-- но тогда из очереди не видно, что именно проверять человеку.
do $$
begin
  alter table sup_review drop constraint if exists sup_review_kind_check;
  alter table sup_review add constraint sup_review_kind_check check (kind in (
    'ambiguous_match', 'different_legal_form', 'currency_unknown', 'unit_unknown',
    'term_conflict', 'high_value', 'pn_suffix', 'entity_uncertain', 'low_conf_link',
    'override_conflict', 'sensitive_change'));
end $$;

create index if not exists sup_review_open on sup_review (priority, opened_at)
  where closed_at is null;
create index if not exists sup_review_kind on sup_review (kind) where closed_at is null;

-- 6. Реестр выданных номеров: одна строка на сущность, номер выдан навсегда.
--    Освободившиеся не переиспользуются. Так же устроен pnw/data/kv_registry.json,
--    и там это проверено — три пересборки, 13 164 из 13 164 сохранили номер.
--
--    ПОЧЕМУ ЗДЕСЬ НЕТ КОЛОНКИ «устойчивый ключ». Сначала она была, и это ломало
--    главное свойство номера. Опознать поставщика можно по домену, по имени, по
--    companyId — и набор признаков РАСТЁТ со временем: в первый прогон компания
--    известна только именем, во второй у неё появляется сайт. Если ключ строки
--    реестра складывать из признака, второй прогон не найдёт первую строку и
--    выдаст ТОТ ЖЕ компании второй номер. Номер, который уходит в договоры,
--    раздваиваться не может.
--
--    Поэтому опознание живёт в sup_identifier, где у сущности сколько угодно
--    ключей (домен, ИНН, имя, алиас, companyId), а здесь — только «сущность →
--    её номер». Новый признак добавляется строкой в sup_identifier и номера не
--    трогает.
create table if not exists sup_number_registry (
  sup_id      text primary key references sup_entity(id) on delete restrict,
  seq         int  not null unique,   -- шесть цифр номера
  assigned_at timestamptz not null default now(),
  run_id      text not null
);

-- 7. Наше юридическое лицо. Список пока пуст: владелец сказал, что юрлиц
--    несколько, а перечень не готов (решение от 20.09.2026). Колонка заложена
--    сразу, потому что переделывать модель и права дороже пустой таблицы.
create table if not exists our_entity (
  id     text primary key,
  name   text not null,
  inn    text,
  active boolean not null default true,
  note   text
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Эффективное значение: подтверждённое человеком поверх импортированного,
--    а исходное остаётся читаемым. Следующий импорт корректировку не трогает;
--    при противоречии пишется строка в sup_review, и до разбора эффективным
--    остаётся подтверждённое человеком.
create or replace view sup_effective with (security_invoker = true) as
select f.subject_kind,
       f.subject_id,
       f.field,
       coalesce(o.corrected_value, f.value)            as value,
       case when o.id is null then f.status else 'verified' end as status,
       f.value                                          as imported_value,
       f.status                                         as imported_status,
       o.author                                         as corrected_by,
       o.reason                                         as correction_reason,
       o.conflict                                       as conflict,
       f.source_type, f.source_id, f.method, f.method_ver, f.confidence,
       f.run_id
from sup_fact f
left join sup_override o
       on o.subject_kind = f.subject_kind
      and o.subject_id   = f.subject_id
      and o.field        = f.field
      and o.conflict    <> 'resolved'
where f.status <> 'superseded';

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. Доступ. Строжайший из существующих в базе образцов — archive_*: RLS плюс
--    FORCE, полный REVOKE и гранты только service_role. Читается исключительно
--    воркером сервисным ключом, после проверки входа Cloudflare Access и права
--    на раздел. Роль anon не получает ничего: в этой же базе тринадцать таблиц
--    ЗИП исторически открыты ей на запись и удаление, и повторять это нельзя.
-- Схема берётся из search_path, а не прибита к public: в проде это public, а
-- тест применяет файл в отдельной схеме одноразовой базы — иначе таблицы
-- остались бы в public общей базы CI и мешали соседним тестам.
do $$
declare n text;
declare сх text := current_schema();
begin
  foreach n in array array[
    'sup_entity', 'sup_identifier', 'sup_fact', 'sup_override',
    'sup_review', 'sup_number_registry', 'our_entity'
  ] loop
    execute format('comment on table %I.%I is %L', сх, n, 'suppliers_schema:v1');
    execute format('alter table %I.%I enable row level security', сх, n);
    execute format('alter table %I.%I force  row level security', сх, n);
    execute format('revoke all on table %I.%I from public, anon, authenticated', сх, n);
    execute format('grant select, insert, update, delete on table %I.%I to service_role',
                   сх, n);
  end loop;
end $$;

revoke all on sup_effective from public, anon, authenticated;
grant select on sup_effective to service_role;

-- Последовательности: та же строгость, иначе anon может двигать счётчик.
do $$
declare s text;
begin
  for s in select sequence_name from information_schema.sequences
            where sequence_schema = 'public' and sequence_name like 'sup_%'
  loop
    execute format('revoke all on sequence public.%I from public, anon, authenticated', s);
    execute format('grant usage, select on sequence public.%I to service_role', s);
  end loop;
end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. Проверка после применения. Падаем громко, а не оставляем открытую таблицу
--     (образец — scripts/activate_archive_search.py:107-112).
--
--     ЧТО ЭТА ПРОВЕРКА МОЖЕТ И ЧЕГО НЕ МОЖЕТ — выяснено прогоном, а не чтением.
--     Первая версия смотрела, у всех ли помеченных таблиц стоит FORCE RLS, и
--     оказалась слепой: блок 9 выше переприменяет защиту, поэтому к моменту
--     проверки он уже починил всё, что мог. Снятый вручную FORCE и выданное anon
--     право прогон восстановил и отчитался «чисто» — проверка подтверждала
--     собственную работу блока 9, а не состояние схемы.
--
--     Настоящая дыра другая: таблица, ДОБАВЛЕННАЯ В ФАЙЛ, но забытая в массиве
--     блока 9. Метки она не получит, защиты тоже, и счёт помеченных сойдётся —
--     потому что считались только помеченные. Поэтому проверка перевёрнута: она
--     ищет таблицы, которые ПО ИМЕНИ принадлежат этой схеме, но метки не несут.
do $$
declare забыто text[];
declare открыто int;
begin
  -- Таблица принадлежит схеме по имени, но блок 9 её не тронул.
  select coalesce(array_agg(c.relname order by c.relname), '{}') into забыто
  from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
  where ns.nspname = current_schema() and c.relkind = 'r'
    and (c.relname like 'sup\_%' or c.relname = 'our_entity')
    and coalesce(obj_description(c.oid, 'pg_class'), '') <> 'suppliers_schema:v1';

  if array_length(забыто, 1) > 0 then
    raise exception 'таблицы схемы поставщиков без метки и без защиты: %. '
                    'Добавь их в массив блока 9', array_to_string(забыто, ', ');
  end if;

  -- Защита на помеченных: страховка на случай, если блок 9 её не выставил.
  select count(*) into открыто
  from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
  where ns.nspname = current_schema() and c.relkind = 'r'
    and obj_description(c.oid, 'pg_class') = 'suppliers_schema:v1'
    and (not c.relrowsecurity or not c.relforcerowsecurity);

  if открыто > 0 then
    raise exception 'таблиц схемы поставщиков без FORCE RLS: %', открыто;
  end if;

  -- Ни одного права у anon, authenticated и PUBLIC. В этой же базе тринадцать
  -- таблиц ЗИП открыты anon на запись и удаление, и повторять это нельзя.
  select count(*) into открыто
  from information_schema.role_table_grants g
  join pg_class c on c.relname = g.table_name
  join pg_namespace ns on ns.oid = c.relnamespace and ns.nspname = g.table_schema
  where g.grantee in ('anon', 'authenticated', 'PUBLIC')
    and g.table_schema = current_schema()
    and obj_description(c.oid, 'pg_class') = 'suppliers_schema:v1';

  if открыто > 0 then
    raise exception 'у anon/authenticated/PUBLIC есть % прав на таблицы схемы поставщиков',
      открыто;
  end if;
end $$;
