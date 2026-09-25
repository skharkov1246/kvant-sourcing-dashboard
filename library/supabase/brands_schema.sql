-- РЕЕСТР БРЕНДОВ: этап 8.2 плана (docs/suppliers/IMPLEMENTATION_PLAN.md).
--
-- ЗАЧЕМ. Сущности «бренд» в рабочем контуре не было: бренд лежал свободным
-- текстом в восьми колонках, и семь несогласованных правил сводили написания
-- каждое по-своему. Здесь две таблицы и одно правило:
--
--   lib_brands       — бренд: ключ, имя, владелец, прежние имена, страна;
--   lib_brand_alias  — написание → ключ бренда, откуда и где встречено, статус,
--                      ключ прогона. Неразрешённое написание не выбрасывается,
--                      а стоит здесь со статусом «в очереди» или «спорно».
--
-- КЛЮЧ БРЕНДА — oem_key из dict/oem.json; новый ключ считается тем же nkey
-- (scripts/build_dict.py). Так факты роли (8.3) ссылаются на бренд по ключу, не
-- дожидаясь таблицы. КЛЮЧ НАПИСАНИЯ — lib_brand_key() ниже, то же правило, что
-- у запроса страницы /brands (library/codes_sql.py, brand_pipeline и
-- ключ_написания): одно правило с версией, записанной в каждой строке (rule).
--
-- БРЕНД СТРОКИ РАЗРЕШАЕТСЯ ВИДОМ, А НЕ UPDATE (CLAUDE.md, правило 5): в
-- lib_demand 1,45 млн строк. Вид lib_brand_map отдаёт «ключ написания → ключ
-- бренда» только для однозначных написаний; запросы соединяются с ним.
--
-- ИДЕМПОТЕНТНО: файл применяется повторно без ошибок и без потерь. Роли Supabase
-- называются только через проверку наличия (правило 20), проверки видов —
-- отдельным do-блоком (правило 21), виды пересоздаются через drop (тест
-- tests/test_schema_views_droppable.py).
--
-- Засев и откат — library/load_brands.py (прогон «Библиотека — реестр брендов»).

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Ключ написания. Порт library/codes_sql.ключ_написания: нижний регистр,
--    ё → е, свёртка диакритики, снятие правовых форм по границе слова, только
--    буквы и цифры, 40 знаков, латинские двойники в смешанном ключе. Граница
--    правовой формы — явный класс [0-9a-zа-я_], а не \m…\M: у \m слово — любая
--    буква по локали, и Python повторить это не может (24.09.2026, «іао»). Строки
--    констант — дословно из codes_sql (DIACRITICS_*, LEGAL_FORMS_KEY, HOMO_*);
--    расхождение ловит tests/test_brand_registry_sql.py.
--    Работает верно только в базе с локалью UTF-8 (правило 21а): в локали C
--    lower() не складывает кириллицу.
create or replace function lib_brand_key(t text) returns text as $$
  select case when x.k ~ '[a-z]' and x.k ~ '[а-я]'
              then translate(x.k, 'аевкмнорстху', 'aebkmhopctxy')
              else x.k end
    from (select left(regexp_replace(regexp_replace(
                   translate(replace(lower(coalesce(t, '')), 'ё', 'е'),
                             'äöüåáàâãéèêëíìîïóòôõúùûñçøšžčřýłæœß',
                             'aouaaaaaeeeeiiiioooouuuncoszcrylaos'),
                   '(?<![0-9a-zа-я_])(ооо|оао|зао|пао|ао|llc|ltd|inc|gmbh|s\.p\.a|spa|co|corp|company|limited|holding|group|a/s|ab|bv|nv|sas|sa|plc|pte|kg|ag|oy|oyj|srl|as)(?![0-9a-zа-я_])',
                   ' ', 'g'),
                 '[^0-9a-zа-я]', '', 'g'), 40) as k) x
$$ language sql immutable parallel safe;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Бренд.
create table if not exists lib_brands (
  brand_key    text primary key,              -- oem_key словаря либо nkey(имени)
  name         text not null,                 -- каноническое имя
  owner        text,                          -- владелец (атлас изготовителей)
  former_names text,                          -- прежние имена (атлас)
  country      text,
  sources      text[] not null default '{}',  -- откуда заведён: dict/oem.json, атлас, СП-176
  rule         text not null,                 -- версия правила ключа
  run_id       text not null,                 -- прогон, заведший бренд: откат по нему
  created_at   timestamptz not null default now()
);
create index if not exists lib_brands_run on lib_brands (run_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Написание → бренд. Одна строка — одно написание из одного источника в одном
--    месте: повторный засев встаёт в уникальный ключ и дублей не создаёт.
--
--    Статусы. Засев ставит четыре: «разрешено» (ключ бренда есть), «спорно»
--    (написание сводится к нескольким брендам — candidates), «в очереди» (бренда
--    не нашлось), «не бренд» (пометка незнания, страна, указание к закупке —
--    закрытыми списками, note — причина). Человек ставит ещё два: «проверено» и
--    «отклонено»; их засев не трогает никогда.
--
--    Повторный засев меняет строку в двух случаях: была «в очереди» или «спорно»,
--    стала «разрешено»; либо строка словаря dict/oem.json, и суждение файла о ней
--    стало другим (вид записи — library/oem_kind.py). Прежнее состояние
--    сохраняется в prev_*, и откат прогона возвращает его, а не удаляет строку.
create table if not exists lib_brand_alias (
  id             bigint generated always as identity primary key,
  spelling       text not null,               -- написание как встречено
  spelling_key   text not null,               -- lib_brand_key(spelling)
  source         text not null,               -- dict/oem.json, zip/data/oem_atlas.json, OEM_ALIAS,
                                              -- СП-176, lib_suppliers, lib_prices.oem, lib_demand.oem, lib_parts.oem
  seen_at        text not null default '',    -- где встречено: поле файла, СП-176#id, lib_suppliers#id
  sp176_id       bigint,                      -- элемент справочника марок портала
  brand_key      text,                        -- пусто у неразрешённого
  status         text not null,
  candidates     text[],                      -- у спорного: между какими ключами выбор
  n_rows         bigint,                      -- строк данных с этим написанием на момент засева
  note           text,                        -- причина «не бренд» или способ разрешения
  rule           text not null,
  run_id         text not null,
  prev_status    text,
  prev_brand_key text,
  prev_run_id    text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (source, spelling, seen_at)
);
create index if not exists lib_brand_alias_key   on lib_brand_alias (spelling_key);
create index if not exists lib_brand_alias_brand on lib_brand_alias (brand_key);
create index if not exists lib_brand_alias_run   on lib_brand_alias (run_id);
create index if not exists lib_brand_alias_sp176 on lib_brand_alias (sp176_id);

-- Проверки — отдельными блоками: create table if not exists существующую таблицу
-- не меняет (правило 21). Новый статус добавляется сносом и созданием проверки.
-- Снос — «drop constraint if exists» у самой таблицы, а не поиск имени в
-- pg_constraint: тот видит проверки всех схем базы, и при второй схеме с такой
-- же таблицей находил чужую, сносил несуществующую — и файл падал целиком
-- (24.09.2026, тестовая база с двумя схемами).
do $$
begin
  alter table lib_brand_alias drop constraint if exists lib_brand_alias_статус;
  alter table lib_brand_alias add constraint lib_brand_alias_статус
    check (status in ('разрешено', 'спорно', 'в очереди', 'не бренд', 'проверено', 'отклонено'));
  alter table lib_brand_alias drop constraint if exists lib_brand_alias_ключ_при_статусе;
  -- Ключ бренда есть ровно у разрешённых: «в очереди» с ключом — это
  -- разрешённое, которое никто не увидит в lib_brand_map, а «разрешено» без
  -- ключа — пропажа, выглядящая решением.
  alter table lib_brand_alias add constraint lib_brand_alias_ключ_при_статусе
    check ((status in ('разрешено', 'проверено')) = (brand_key is not null));
end $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Виды. Сносятся все до создания: состав колонок можно менять.
drop view if exists lib_brand_queue;
drop view if exists lib_brand_sp176;
drop view if exists lib_brand_map;

-- Ключ написания → бренд. Только однозначные: написание, сведённое к двум
-- брендам, не разрешает ничего (неверная склейка на странице неотличима от
-- верной) — оно видно в очереди статусом «спорно».
create view lib_brand_map with (security_invoker = true) as
  select spelling_key, min(brand_key) as brand_key
    from lib_brand_alias
   where status in ('разрешено', 'проверено') and brand_key is not null
     and spelling_key <> ''
   group by spelling_key
  having count(distinct brand_key) = 1;

-- Элемент СП-176 → бренд. По нему разрешаются ключи lib_prices.rfq_brands.
create view lib_brand_sp176 with (security_invoker = true) as
  select sp176_id, min(brand_key) as brand_key
    from lib_brand_alias
   where sp176_id is not null and status in ('разрешено', 'проверено')
     and brand_key is not null
   group by sp176_id
  having count(distinct brand_key) = 1;

-- Очередь неразрешённых написаний.
create view lib_brand_queue with (security_invoker = true) as
  select source, status, spelling, spelling_key, seen_at, sp176_id, candidates,
         n_rows, note, run_id, created_at
    from lib_brand_alias
   where status in ('в очереди', 'спорно');

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Доступ. Браузер эти таблицы не читает: RLS включён, права сняты с anon и
--    authenticated, если такие роли есть (правило 20: на чистом PostgreSQL их
--    нет, и названная напрямую роль уронила бы весь файл).
do $$
declare n text;
declare кому text;
declare служебная text;
begin
  select string_agg(quote_ident(rolname), ', ') into кому
    from pg_roles where rolname in ('anon', 'authenticated');
  select string_agg(quote_ident(rolname), ', ') into служебная
    from pg_roles where rolname = 'service_role';
  foreach n in array array['lib_brands', 'lib_brand_alias'] loop
    execute format('alter table %I enable row level security', n);
    execute format('revoke all on table %I from public', n);
    if кому is not null then
      execute format('revoke all on table %I from %s', n, кому);
    end if;
    if служебная is not null then
      execute format('grant select, insert, update, delete on table %I to %s', n, служебная);
    end if;
  end loop;
  foreach n in array array['lib_brand_map', 'lib_brand_sp176', 'lib_brand_queue'] loop
    execute format('revoke all on %I from public', n);
    if кому is not null then
      execute format('revoke all on %I from %s', n, кому);
    end if;
    if служебная is not null then
      execute format('grant select on %I to %s', n, служебная);
    end if;
  end loop;
end $$;
