-- РАЗМЕТКА «ЭТО НЕ НОМЕНКЛАТУРА» — текст тендерных документов в lib_demand.
--
-- ЗАЧЕМ. Разбор текстовых вложений принимал за позицию спецификации ЛЮБУЮ строку
-- длиннее восьми знаков (library/indexer.py, ветка разбора текста). В результате
-- в спрос попал текст извещений о закупке, проектов договоров и форм КП: на
-- 12.09.2026 это 369 171 строка из 1 451 732, четверть базы. Мерить спрос по
-- такой базе нельзя — доли по сегментам занижены на четверть.
--
-- ПОЧЕМУ ПОМЕТКА, А НЕ УДАЛЕНИЕ. Правило может ошибиться на настоящей позиции из
-- сегмента, которого ещё нет в словаре (крепёж, РТИ, кабель, спецодежда). Пометка
-- обратима: снять её — одна строка, восстановить удалённое — нечем.
--
-- Идемпотентно: можно прогонять повторно. Применяется тем же zip-db.yml.

-- ─────────────────────────────────────────────────────────────────────────────
-- Вердикт «эта строка — не номенклатура». Разметка, а НЕ удаление.
--
-- Разбор текстовых файлов принимал за позицию любую строку длиннее восьми знаков
-- (library/indexer.py, ветка разбора текста): в lib_demand легла тендерная проза.
--
-- Пометка живёт в ОТДЕЛЬНОЙ таблице, а не колонкой lib_demand. Причина
-- эксплуатационная: lib_demand.fts — generated always … stored, плюс четыре
-- btree и GIN. UPDATE 300 тыс. строк создаёт новые версии строк во ВСЕХ
-- индексах, пересчитывает to_tsvector и раздувает таблицу примерно на четверть,
-- а откат таким же UPDATE на пуле Supabase не укладывается в statement timeout.
-- INSERT в таблицу-спутник и DELETE по run_id стоят секунды и не трогают GIN.
set statement_timeout = '20min';
set lock_timeout      = '10s';

create table if not exists lib_row_junk (
  demand_id  bigint primary key references lib_demand(id) on delete cascade,
  rule       text        not null,          -- версия правила, напр. 'proza-v1'
  run_id     text        not null,          -- ключ отката: одна пачка = один прогон
  marks      text,                          -- какие признаки сработали: 'лексика,оборот'
  marked_at  timestamptz not null default now(),
  revoked_at timestamptz,                   -- снятие пометки (словарь дорос) — не delete
  revoked_by text
);
create index if not exists lib_row_junk_run    on lib_row_junk (run_id);
create index if not exists lib_row_junk_active on lib_row_junk (demand_id) where revoked_at is null;
alter table lib_row_junk enable row level security;
revoke all on lib_row_junk from anon, authenticated;

-- Журнал прогонов разметки. Только агрегаты — таблицу можно показывать целиком.
create table if not exists lib_mark_runs (
  run_id        text primary key,
  rule          text not null,
  mode          text not null,              -- 'разметка' | 'откат'
  params        jsonb,                      -- пороги этого прогона
  started_at    timestamptz default now(),
  finished_at   timestamptz,
  files_total   int, files_marked int,
  rows_total    int, rows_marked  int,
  rows_dict_hit int,                        -- строк, которые узнаёт словарь
  rows_fp_shadow int,                       -- из них помечено бы при отключённой защите словарём
  reverted_at   timestamptz, reverted_reason text, note text
);
alter table lib_mark_runs enable row level security;
revoke all on lib_mark_runs from anon, authenticated;

-- Обратимость наследования сегмента. reclassify.py сейчас не оставляет следа,
-- каким правилом проставлен segment_id, и откатить неудачное наследование нечем.
-- Колонки nullable и без default — правка каталога, таблица не переписывается.
alter table lib_demand add column if not exists segment_rule text;   -- 'строка'|'файл'|'сделка'|'словарь'|'ручная'
alter table lib_demand add column if not exists segment_run  text;

-- Состояние файла: каким путём разобран и что решило правило.
alter table lib_files add column if not exists parse_path     text;      -- 'таблица' | 'текст'
alter table lib_files add column if not exists header_found   boolean;   -- шапка спецификации найдена (только новые разборы)
alter table lib_files add column if not exists doc_class      text;      -- 'документация' | 'спецификация' | 'неясно'
alter table lib_files add column if not exists class_rule     text;
alter table lib_files add column if not exists class_run      text;
alter table lib_files add column if not exists class_at       timestamptz;
alter table lib_files add column if not exists text_lines     int;
alter table lib_files add column if not exists item_lines     int;
alter table lib_files add column if not exists parser_version smallint;
create index if not exists lib_files_class on lib_files (doc_class);

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'lib_files_doc_class_chk') then
    alter table lib_files add constraint lib_files_doc_class_chk
      check (doc_class is null or doc_class in ('документация','спецификация','неясно'));
  end if;
end $$;

-- Контракт для всех потребителей: в новом коде обращаться сюда, а не в lib_demand.
-- drop+create, а не create or replace: replace не переживёт добавления колонки
-- в lib_demand и уронит файл миграции. security_invoker требует PostgreSQL 15+.
drop view if exists lib_demand_live;
create view lib_demand_live with (security_invoker = true) as
  select d.* from lib_demand d
   where not exists (select 1 from lib_row_junk j
                      where j.demand_id = d.id and j.revoked_at is null);
revoke all on lib_demand_live from anon, authenticated;

-- ─────────────────────────────────────────────────────────────────────────────
-- Задним числом: каким путём разобраны уже лежащие файлы. Нужно, чтобы расслоить
-- калибровку по пути разбора — весь риск в текстовом пути.
--
-- Признак «таблица» = у файла есть хоть одна строка со структурной колонкой:
-- unit/qty/oem заполняются ТОЛЬКО в items_from_rows при найденной шапке.
--
-- ПОДЗАПРОС ОБЯЗАН БЫТЬ НЕЗАВИСИМЫМ ОТ ОБНОВЛЯЕМОЙ СТРОКИ. Первая версия
-- спрашивала «exists (… where d.source_file = f.file_id)»: такой подзапрос
-- коррелирован, внутри CASE он не хешируется и выполняется заново для каждого из
-- пятнадцати тысяч файлов по таблице в полтора миллиона строк. Прогон 12.09.2026
-- не уложился в двенадцать минут и был снят. Форма «file_id in (select …)» ни на
-- что во внешней строке не ссылается, поэтому планировщик считает её один раз и
-- складывает в хеш.
--
-- Досчёт стоит ПОСЛЕ представления: он не нужен ни разметке, ни сводке, и если
-- когда-нибудь снова окажется медленным, он не должен заблокировать то, ради чего
-- вся миграция затевалась.
update lib_files f set parse_path = case
    when f.kind = 'pdf' then 'текст'
    when f.file_id in (select source_file from lib_demand
                        where qty is not null
                           or coalesce(btrim(unit), '') <> ''
                           or coalesce(btrim(oem), '') <> '') then 'таблица'
    when f.kind = 'старый office' then 'таблица'
    else 'текст' end
 where f.parse_path is null and f.status = 'разобран';

-- ─────────────────────────────────────────────────────────────────────────────
-- Индекс под переразбор (library/reparse.py). Выборка старых строк файла без
-- него — последовательный проход по полутора миллионам строк на каждый из тысяч
-- файлов; ровно на такой коррелированной выборке 12.09.2026 уже подвисла
-- миграция. Строится CONCURRENTLY: обычный CREATE INDEX берёт SHARE-блокировку и
-- остановит запись индексатора на всё время построения.
--
-- CONCURRENTLY нельзя выполнять внутри транзакции — psql выполняет каждый
-- оператор отдельно, поэтому здесь это работает. Оператор стоит ПОСЛЕДНИМ:
-- если он не пройдёт (например, при идущем прогоне индексатора), всё
-- остальное уже применено.
create index concurrently if not exists lib_demand_src on lib_demand (source_file);
