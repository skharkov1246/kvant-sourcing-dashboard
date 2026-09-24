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
    'ambiguous_match', 'different_legal_form', 'different_tax_number',
    'currency_unknown', 'unit_unknown',
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

-- 7а. Имя для показа из карточки компании портала (library/company_names.py).
--
--     ЗАЧЕМ. display_name — это norm_name от названия: «supremevalves»,
--     «ethosenergybloomfieldwgpwindustrialturbineservices». Как ключ сведения он
--     верен, как вывеска компании — нет: владелец 24.09.2026 — «наименование как
--     веб-сайт компании не работает». Настоящее название лежит в Битриксе:
--     TITLE карточки и наименование в реквизитах.
--
--     ПОЧЕМУ СПУТНИК, А НЕ ПЕРЕЗАПИСЬ display_name. Правило 5: пометка, а не
--     удаление. Строка здесь только добавляется; откат прогона ставит
--     rolled_back_at, и действующим снова становится прежнее имя — его строка
--     никуда не делась, а previous_name хранит, что было до прогона.
--
--     ИНН ИЗ РЕКВИЗИТОВ ЖИВЁТ ЗДЕСЬ ЖЕ, а не строкой sup_identifier. Признак inn
--     в sup_identifier опознаёт и запрещает слияния (load_supplier_master,
--     ЕДИНОЛИЧНЫЕ), а проверка «такой ИНН уже у другой сущности — не воруем»
--     живёт только в памяти прогона сведения. Второй писатель признаков в обход
--     неё мог бы положить один ИНН двум сущностям. Сведение и так читает
--     реквизиты на каждом прогоне и запишет номер само; здесь он — для показа и
--     для замера «сколько очереди снимет следующий прогон».
create table if not exists sup_display_name (
  id             bigserial primary key,
  sup_id         text not null references sup_entity(id) on delete cascade,
  source         text not null check (source in ('bitrix:title', 'bitrix:requisite')),
  name           text check (name is null or btrim(name) <> ''),
  previous_name  text,            -- действующее имя этого источника ДО прогона
  card_id        text,            -- карточка компании портала, откуда взято
  full_name      text,            -- полное наименование из реквизитов
  inn            text,
  kpp            text,
  ogrn           text,
  run_id         text not null,   -- без ключа прогона откат невозможен (правило 6)
  created_at     timestamptz not null default now(),
  rolled_back_at timestamptz,     -- откат: пометка, а не удаление (правило 5)
  check (name is not null or inn is not null)
);
create index if not exists sup_display_name_active
  on sup_display_name (sup_id, source, id desc) where rolled_back_at is null;
create index if not exists sup_display_name_run on sup_display_name (run_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Эффективное значение: подтверждённое человеком поверх импортированного,
--    а исходное остаётся читаемым. Следующий импорт корректировку не трогает;
--    при противоречии пишется строка в sup_review, и до разбора эффективным
--    остаётся подтверждённое человеком.
-- Уронить перед созданием — по той же причине, что у sup_quote_price ниже:
-- «create or replace view» отказывается менять состав колонок в середине,
-- и делает это ТОЛЬКО там, где вид уже стоит, то есть на живой базе.
-- Гранты этот же файл выдаёт заново ниже (revoke/grant по sup_effective),
-- поэтому drop их не уносит.
drop view if exists sup_effective;
create view sup_effective with (security_invoker = true) as
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
-- РОЛИ SUPABASE МОГУТ ОТСУТСТВОВАТЬ, и файл обязан это переживать. anon,
-- authenticated и service_role заводит платформа; на чистом PostgreSQL — том, на
-- котором идут проверки записи, — их нет, и «revoke … from anon» роняет весь файл
-- с «role "anon" does not exist». Прогон 20.09.2026 20:40 так и упал: локально я
-- роли создал руками и поэтому ошибки не увидел, а прогон увидел.
--
-- Роли здесь НЕ СОЗДАЮТСЯ: заводить платформенные роли — не дело схемы данных, и
-- в чужой базе это лишнее. Вместо этого снимаем права только с тех, кто есть, а
-- отсутствие роли значит, что и снимать у неё нечего.
create or replace function sup_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

-- Схема берётся из search_path, а не прибита к public: в проде это public, а
-- тест применяет файл в отдельной схеме одноразовой базы — иначе таблицы
-- остались бы в public общей базы CI и мешали соседним тестам.
do $$
declare n text;
declare сх text := current_schema();
declare кому text := sup_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := sup_роли_которые_есть(array['service_role']);
begin
  foreach n in array array[
    'sup_entity', 'sup_identifier', 'sup_fact', 'sup_override',
    'sup_review', 'sup_number_registry', 'our_entity', 'sup_display_name'
  ] loop
    execute format('comment on table %I.%I is %L', сх, n, 'suppliers_schema:v1');
    execute format('alter table %I.%I enable row level security', сх, n);
    execute format('alter table %I.%I force  row level security', сх, n);
    execute format('revoke all on table %I.%I from public', сх, n);
    if кому is not null then
      execute format('revoke all on table %I.%I from %s', сх, n, кому);
    end if;
    if служебная is not null then
      execute format('grant select, insert, update, delete on table %I.%I to %s',
                     сх, n, служебная);
    end if;
  end loop;
end $$;

do $$
declare кому text := sup_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := sup_роли_которые_есть(array['service_role']);
begin
  revoke all on sup_effective from public;
  if кому is not null then
    execute format('revoke all on sup_effective from %s', кому);
  end if;
  if служебная is not null then
    execute format('grant select on sup_effective to %s', служебная);
  end if;
end $$;

-- 8а. ИМЯ ДЛЯ ПОКАЗА — ОДИН ВЫБОР НА ВСЕ СТРАНИЦЫ. /suppliers, /nomenclature и
--     выгрузки брендов (library/codes_sql.py) берут имя компании отсюда, а не
--     каждая своим coalesce: три разных выбора дали бы одной компании три
--     разных имени на трёх страницах.
--
--     Порядок — от того, что видят люди, к тому, что осталось:
--       1. TITLE карточки компании портала;
--       2. наименование из её реквизитов (краткое, иначе полное);
--       3. написание из признаков (trading, alias), если оно не похоже на ключ;
--       4. display_name, если он не похож на ключ;
--       5. домен сайта — лучше адреса, чем сжатой строки;
--       6. ничего: name пуст, name_source = 'ключ реестра'. Пусто, а не ключ,
--          чтобы читатель сам решил, чем подписать строку: у выгрузок брендов
--          дальше идут имя из очереди проверки и старый справочник, у страницы —
--          display_name. Отдай вид ключ — он встал бы впереди их всех.
--
--     «ПОХОЖЕ НА КЛЮЧ» — одна функция, её же зовёт Python (company_names.как_ключ,
--     сверка на одном корпусе — tests/test_company_names_sql.py): ключ портала
--     «bitrix:2002» и выход norm_name — строчные буквы и цифры без пробелов.
create or replace function sup_имя_как_ключ(t text) returns boolean as $$
  select t is null or btrim(t) = ''
      or t ~ '^[a-z_]+:\S+$'
      or t ~ '^[a-zа-яё0-9]+$'
$$ language sql immutable;

drop view if exists sup_name_shown;
create view sup_name_shown with (security_invoker = true) as
with active as (
  -- Действующая строка источника — последняя не откаченная. Откат прогона
  -- помечает его строки, и действующей снова становится прежняя.
  select distinct on (sup_id, source) sup_id, source, name
    from sup_display_name
   where rolled_back_at is null
   order by sup_id, source, id desc
), written as (
  select distinct on (i.sup_id) i.sup_id, i.value as name
    from sup_identifier i
   -- legal сюда не входит: в этом реестре он несёт правовую ФОРМУ («ооо»,
   -- «gmbh & co kg» — load_supplier_master.признаки), а не наименование.
   where i.status <> 'rejected' and i.kind in ('trading', 'alias')
     and not sup_имя_как_ключ(i.value)
   order by i.sup_id, (i.kind = 'trading') desc, length(i.value) desc, i.value
), domain as (
  select distinct on (i.sup_id) i.sup_id, i.value as name
    from sup_identifier i
   where i.status <> 'rejected' and i.kind = 'domain'
   order by i.sup_id, (i.status = 'verified') desc, i.value
)
select e.id as sup_id,
       coalesce(case when not sup_имя_как_ключ(t.name) then t.name end,
                case when not sup_имя_как_ключ(r.name) then r.name end,
                w.name,
                case when not sup_имя_как_ключ(e.display_name) then e.display_name end,
                d.name)                                             as name,
       case when not sup_имя_как_ключ(t.name) then 'bitrix:title'
            when not sup_имя_как_ключ(r.name) then 'bitrix:requisite'
            when w.name is not null then 'написание'
            when not sup_имя_как_ключ(e.display_name) then 'реестр'
            when d.name is not null then 'домен'
            else 'ключ реестра' end                                 as name_source
  from sup_entity e
  left join active t  on t.sup_id = e.id and t.source = 'bitrix:title'
  left join active r  on r.sup_id = e.id and r.source = 'bitrix:requisite'
  left join written w on w.sup_id = e.id
  left join domain d  on d.sup_id = e.id;

do $$
declare кому text := sup_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := sup_роли_которые_есть(array['service_role']);
begin
  revoke all on sup_name_shown from public;
  if кому is not null then
    execute format('revoke all on sup_name_shown from %s', кому);
  end if;
  if служебная is not null then
    execute format('grant select on sup_name_shown to %s', служебная);
  end if;
end $$;

-- Последовательности: та же строгость, иначе anon может двигать счётчик.
do $$
declare s text;
declare кому text := sup_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := sup_роли_которые_есть(array['service_role']);
begin
  for s in select sequence_name from information_schema.sequences
            where sequence_schema = 'public' and sequence_name like 'sup_%'
  loop
    execute format('revoke all on sequence public.%I from public', s);
    if кому is not null then
      execute format('revoke all on sequence public.%I from %s', s, кому);
    end if;
    if служебная is not null then
      execute format('grant usage, select on sequence public.%I to %s', s, служебная);
    end if;
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. Цена из разобранного КП, привязанная к компании реестра.
--
-- ЗАЧЕМ ВИД, А НЕ КОЛОНКА. lib_prices.supplier_id — внешний ключ на lib_suppliers,
-- старый справочник; ключ портала из карточки запроса ведёт в sup_identifier
-- нового реестра. Это разные реестры, и записать одно в другое значит сломать
-- целостность ради видимости связи. Вид соединяет их, ничего не дублируя и не
-- устаревая: разбор пишет только rfq_company, связь считается на чтении.
--
-- ПОЧЕМУ ЧЕРЕЗ ПРОВЕРКУ НАЛИЧИЯ. Файл применяется и к одноразовой базе теста,
-- где таблиц библиотеки нет вовсе; без проверки падал бы ВЕСЬ файл (та же мина,
-- что с ролями anon — правило 20 CLAUDE.md).
do $$
begin
  if to_regclass(current_schema() || '.lib_prices') is null
     or to_regclass(current_schema() || '.sup_identifier') is null then
    raise notice 'lib_prices или sup_identifier нет — вид sup_quote_price пропущен';
    return;
  end if;
  -- СНАЧАЛА УРОНИТЬ, ПОТОМ СОЗДАТЬ. «create or replace view» разрешает лишь
  -- ДОПИСАТЬ колонки в конец: изменить состав в середине он отказывается —
  -- «cannot change name of view column "price" to "oem"». На чистой базе этого
  -- не видно никогда, потому что вида ещё нет, — зелены и preflight, и гейт,
  -- а падает ровно живая база, где вид уже стоит. Проверено 21.09.2026
  -- прогоном «схема main, поверх неё новая»: psql вернул 3, вид остался
  -- прежним. Тот же приём уже применён к lib_demand_catalog в schema_junk.sql.
  --
  -- Грантов на виде нет (только владельца), поэтому drop ничего не уносит.
  execute $v$ drop view if exists sup_quote_price $v$;
  execute $v$
    create view sup_quote_price with (security_invoker = true) as
    select p.id,
           p.rfq_id,
           p.rfq_company,
           i.sup_id,
           -- Вечный бессмысленный номер и есть id сущности (KV-S-NNNNNN-C).
           e.id                as supplier_number,
           e.display_name      as supplier_name,
           e.status            as supplier_status,
           p.item_name,
           p.part_number,
           -- Изготовитель и бренд идут В ВИД, а не только в таблицу: вопрос
           -- «что этот поставщик котирует и по чьему оборудованию» — один
           -- вопрос, и отвечать на него двумя запросами незачем. oem назван
           -- поставщиком в самом файле, rfq_brands — проставлен на карточке
           -- запроса; это разные утверждения, поэтому и колонки разные.
           p.oem,
           p.rfq_brands,
           p.price,
           p.currency,
           p.qty,
           p.qty_unit,
           p.basis,
           p.lead_days,
           p.confidence,
           p.note,
           -- ОТКУДА ЦЕНА: «КП» — разбор текста файла, «распознавание скана» —
           -- tesseract по странице без текстового слоя. Поток у них один, а
           -- качество разное: латиница в кириллице путается, и ошибка скана
           -- должна быть отличима от ошибки поставщика в любой выборке.
           p.source,
           p.source_url,
           p.created_at
      from lib_prices p
      -- Связь односторонняя и необязательная: цена без поставщика остаётся
      -- видна. Скрыть её значило бы потерять цифру, которая есть.
      left join sup_identifier i
             on i.kind = 'bitrix'
            and i.status <> 'rejected'
            -- Соединяем по value_norm, а не по value: это объявленный ключ
            -- поиска, и по нему же стоит уникальный частичный индекс
            -- (kind='bitrix' and status<>'rejected'). У числового ключа портала
            -- нормализованное значение совпадает с исходным.
            and i.value_norm = p.rfq_company
      left join sup_entity e on e.id = i.sup_id
     where p.feed = 'разбор КП'
  $v$;
end $$;
