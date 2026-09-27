-- ДАТА ПРИХОДА ФАЙЛА: когда файл появился у нас и когда — у источника.
--
-- ЗАЧЕМ. У lib_files не было даты появления вовсе. processed_at переписывает
-- каждая обработка: вставка разбора при конфликте (indexer.вставка_файлов),
-- UPDATE переразбора (reparse.правка_файла), распознавание целиком
-- (ocr.ФАЙЛ_ЦЕЛИКОМ). Поэтому недельный свод (scripts/weekly_offers.py) не мог
-- сказать, какие КП пришли за неделю: переразобранный старый КП выглядел
-- свежим, а у файла без строк цены даты прихода не было никакой.
--
-- ДВЕ КОЛОНКИ, ДВА РАЗНЫХ ФАКТА.
--   first_seen_at     — когда строка файла ВПЕРВЫЕ легла в lib_files. Ставится
--                       значением по умолчанию при первой вставке и больше не
--                       меняется: ни одна запись её не называет, а триггер ниже
--                       не даёт переписать записанное даже тому, кто назовёт.
--                       Это ВЕРХНЯЯ граница прихода: позже, чем мы увидели
--                       файл, он прийти не мог. У строк, записанных до этой
--                       миграции, пусто — когда их увидели впервые, не знает
--                       никто, и выдумывать это значение нельзя.
--   source_created_at — когда файл появился У ИСТОЧНИКА: письмо легло в CRM
--                       (CREATED дела-письма), файл загружен в поле карточки
--                       или сделки (заголовок ответа закачки). Точная дата, если
--                       известна; иначе пусто.
--   source_date_src   — откуда source_created_at (правило 16: сохраняй, почему
--                       получилось значение). Закрытый список ниже.
--   source_date_run   — ключ прогона досчёта (library/backfill_file_dates.py),
--                       записавшего дату (правило 6). У даты, поставленной
--                       разбором, пусто. Досчёт пишет ТОЛЬКО в пустую дату,
--                       поэтому откат — снять свои три колонки по ключу, и
--                       файл вернётся ровно к прежнему виду.
--
-- ПОЧЕМУ ТРИГГЕР, А НЕ ТОЛЬКО ДИСЦИПЛИНА. Разбор пишет lib_files по колонкам,
-- которые есть в базе (indexer.проверить_колонки), и правка, добавившая
-- first_seen_at в список записи, молча превратила бы «впервые» в «последний
-- раз» — ровно то, что случилось с processed_at. Триггер срабатывает только на
-- UPDATE, НАЗЫВАЮЩИЙ колонку (update of first_seen_at), и на вставке: прочие
-- записи его не задевают. UPDATE не меняет колонку вовсе — ни записанную, ни
-- пустую; понадобится исправить — триггер снимают сознательно, а не заодно.
--
-- ПОРЯДОК (правило 10): сначала колонки, значение по умолчанию и триггер —
-- ради них миграция; ограничение вида и индексы — после.
--
-- Идемпотентно: можно прогонять повторно, в любую схему (search_path).
-- Применяется прогоном «ZIP base — apply DB migrations» (zip-db.yml) после
-- schema.sql и schema_junk.sql.

set statement_timeout = '5min';
set lock_timeout      = '10s';   -- правило 12: ALTER TABLE без него встаёт в очередь

do $$
begin
  if to_regclass(current_schema() || '.lib_files') is null then
    raise exception 'даты прихода требуют lib_files (library/supabase/schema.sql) — примените его раньше';
  end if;
end $$;

-- ADD COLUMN без значения по умолчанию — правка каталога: существующие строки
-- остаются пустыми, таблица не переписывается. Значение по умолчанию ставится
-- ОТДЕЛЬНЫМ оператором: в одном операторе с add column оно легло бы во ВСЕ
-- прежние строки временем миграции, и старые файлы стали бы «новыми».
alter table lib_files add column if not exists first_seen_at     timestamptz;
alter table lib_files alter column first_seen_at set default now();
alter table lib_files add column if not exists source_created_at timestamptz;
alter table lib_files add column if not exists source_date_src   text;
alter table lib_files add column if not exists source_date_run   text;

create or replace function lib_files_first_seen() returns trigger
language plpgsql as $$
begin
  if tg_op = 'UPDATE' then
    -- Не переписывается НИКАК, и пустое тоже не заполняется: у строк до
    -- миграции первого появления не знает никто, а заполнить их «сейчас»
    -- значило бы объявить новыми все старые файлы разом.
    new.first_seen_at := old.first_seen_at;
  else
    -- Вставка с явным null не должна оставить строку без даты.
    new.first_seen_at := coalesce(new.first_seen_at, now());
  end if;
  return new;
end $$;

drop trigger if exists lib_files_first_seen on lib_files;
create trigger lib_files_first_seen
  before insert or update of first_seen_at on lib_files
  for each row execute function lib_files_first_seen();

-- Роли Supabase — только через проверку наличия (правило 20): на чистом
-- PostgreSQL их нет, и «revoke … from anon» уронил бы файл целиком. Роли не
-- создаются: это дело платформы.
do $$
declare кому text;
begin
  select string_agg(quote_ident(r.rolname), ', ') into кому
    from pg_roles r where r.rolname in ('anon', 'authenticated');
  if кому is not null then
    execute format('revoke all on function lib_files_first_seen() from %s', кому);
  end if;
end $$;

-- Вид источника даты — закрытым списком, и дата без источника (или источник
-- без даты) невозможны: пара пишется вместе и вместе снимается откатом.
-- Снимается и ставится заново при каждом применении (правило 21): «if not
-- exists» пропустил бы новый вид, и на живой базе запись с ним падала бы, а на
-- свежей проходила. Ищется по самой таблице (alter table … if exists), а не по
-- имени во всей базе: тесты применяют файл в несколько схем одной базы.
alter table lib_files drop constraint if exists lib_files_source_date_src_chk;
alter table lib_files add constraint lib_files_source_date_src_chk
  check (case when source_created_at is null then source_date_src is null
              else coalesce(source_date_src in ('письмо: создано в CRM',
                                                'закачка: Last-Modified'), false) end);

create index if not exists lib_files_first_seen  on lib_files (first_seen_at);
create index if not exists lib_files_source_date on lib_files (source_created_at);
create index if not exists lib_files_source_run  on lib_files (source_date_run)
  where source_date_run is not null;
