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
-- РОЛИ SUPABASE МОГУТ ОТСУТСТВОВАТЬ. anon и authenticated заводит платформа; на
-- чистом PostgreSQL их нет, и «revoke … from anon» роняет файл с «role "anon"
-- does not exist». Против прода это не проявляется — там роли есть, — зато
-- применить схему где-либо ещё становится нельзя. Ту же ошибку 20.09.2026 нашёл
-- прогон в схеме поставщиков, и она же лежала в ступенях ужесточения ЗИП.
--
-- Роли не создаются: это дело платформы, а не схемы. Снимаем права только с тех,
-- кто есть; нет роли — нечего у неё и снимать.
create or replace function lib_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

do $$
declare кому text := lib_роли_которые_есть(array['anon', 'authenticated']);
begin
  if кому is not null then
    execute format('revoke all on lib_row_junk from %s', кому);
  end if;
end $$;

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
do $$
declare кому text := lib_роли_которые_есть(array['anon', 'authenticated']);
begin
  if кому is not null then
    execute format('revoke all on lib_mark_runs from %s', кому);
  end if;
end $$;

-- ЖУРНАЛ ЗАПИСЕЙ РАСПОЗНАВАНИЯ (library/ocr.py) — по строке на файл и прогон.
--
-- ЗАЧЕМ. Распознавание смешанного PDF ДОБАВЛЯЕТ строки и цены страниц-сканов к
-- разбору текстовых страниц, а повтор распознавания заменяет только своё. Чтобы
-- и повтор, и откат трогали ровно своё, запись помнит ключи того, что вставила
-- (demand_ids, price_ids) и что заменила (replaced_*), и поля файла до записи
-- (prev_file). Ничего не удаляется: заменённые строки помечены в lib_row_junk с
-- тем же run_id, заменённые цены переведены в поток «разбор КП: выведено».
-- Пометка у строки одна (ключ — demand_id), поэтому чужая пометка заменённой
-- строки («проза») переписывается, а прежняя хранится в replaced_marks.
-- Откат: OCR_REVERT=<run_id> python library/ocr.py.
--
-- Только ключи, числа и собственные строки кода — ни наименований, ни текста
-- распознавания (правило 17).
create table if not exists lib_ocr_writes (
  run_id              text        not null,   -- ключ прогона: ocr-<прогон>[-p<часть>]
  file_id             text        not null,
  mode                text        not null,   -- 'файл' | 'страницы'
  pages               int[],                  -- распознанные страницы без текста (с 1)
  demand_ids          bigint[]    not null default '{}',  -- вставленные строки lib_demand
  price_ids           bigint[]    not null default '{}',  -- вставленные цены lib_prices
  replaced_demand_ids bigint[]    not null default '{}',  -- свои прежние строки, помеченные
  replaced_price_ids  bigint[]    not null default '{}',  -- свои прежние цены, выведенные
  replaced_marks      jsonb,                  -- пометки lib_row_junk, переписанные заменой
  prev_file           jsonb,                  -- поля lib_files до записи — для отката
  note                text,                   -- почему столько: причина из кода
  written_at          timestamptz not null default now(),
  reverted_at         timestamptz,
  primary key (run_id, file_id)
);
create index if not exists lib_ocr_writes_file on lib_ocr_writes (file_id);
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'lib_ocr_writes_mode_chk') then
    alter table lib_ocr_writes add constraint lib_ocr_writes_mode_chk
      check (mode in ('файл', 'страницы'));
  end if;
end $$;
alter table lib_ocr_writes enable row level security;
do $$
declare кому text := lib_роли_которые_есть(array['anon', 'authenticated']);
begin
  if кому is not null then
    execute format('revoke all on lib_ocr_writes from %s', кому);
  end if;
end $$;

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
-- ПОЧЕМУ ШАПКА НЕ УЗНАНА. Без этой колонки «header_found = false» стоит у 4 194
-- файлов и не говорит, какую из пяти правок делать: замер 23.09.2026 показал, что
-- за одной пометкой стоят пять разных бед (наименование неизвестно, вторая колонка
-- не узнана, шапка глубже сорока строк, читатель отдал один столбец, ни одного
-- известного слова). Причина живёт один прогон, если её не сохранить (правило 16).
-- Колонка nullable и без default — правка каталога, таблица не переписывается.
alter table lib_files add column if not exists header_miss    text;
-- ПОСТРАНИЧНЫЙ СЧЁТ PDF. Смешанный документ — часть страниц текстовые, часть
-- сканы — до 23.09.2026 терялся молча: одна текстовая страница даёт chars > 0,
-- файл получает «разобран», и отбор распознавания не берёт его НИКОГДА. Сканы
-- внутри такого файла это позиции и цены, которых никто не видел. Колонки
-- nullable и без default — правка каталога, таблица не переписывается.
alter table lib_files add column if not exists pdf_pages       int;
alter table lib_files add column if not exists pdf_pages_text  int;
alter table lib_files add column if not exists pdf_pages_lost  int;
alter table lib_files add column if not exists pdf_mixed       boolean;
-- Частичный индекс: отбор распознавания спрашивает именно смешанные, и их мало.
create index if not exists lib_files_pdf_mixed on lib_files (file_id)
  where pdf_mixed is true;
-- ТОЧНЫЙ ФОРМАТ ФАЙЛА. Крупный вид говорит «прочее» у csv, txt, rtf, html, xml и
-- двоичного мусора разом — по нему нельзя понять, какая стратегия чтения
-- сработала и какую чинить. Замер 23.09.2026: «прочее» 410 файлов, все 410 без
-- позиций, и что это за файлы, база не знает.
alter table lib_files add column if not exists subkind         text;
-- ПАПКА ДОКУМЕНТА. Требование владельца 23.09.2026: различать предложения
-- поставщиков нам, запросы заказчиков нам и наши исходящие предложения — и
-- складывать раздельно. Первая редакция ставила папку по содержимому; с того же
-- дня (замечание владельца «при чём тут КВАНТ, в системе запросов уже есть вся
-- информация») папку ставит СИСТЕМА — сущность карточки и код поля
-- (library/doc_folder.py), а содержимое (library/doc_kind.py) только сверяет.
-- doc_kind_conf говорит, откуда папка: 1.0 — код поля, 0.8/0.6 — название поля,
-- 0.0 — папки нет, в том числе у поля, опознанного кодом.
-- doc_kind_why хранит, откуда папка и что сказало содержимое; расхождение
-- начинается словом «расхождение:» (правило 16: сохраняй, почему получилось
-- значение). Строки, записанные до правки, остаются папкой по содержимому,
-- пока их не перепишет разбор или переразбор.
alter table lib_files add column if not exists doc_kind        text;
alter table lib_files add column if not exists doc_kind_conf   real;
alter table lib_files add column if not exists doc_kind_why    text;
create index if not exists lib_files_doc_kind on lib_files (doc_kind);
-- ПУТЬ ЧТЕНИЯ: какой читатель каскада взял файл и что с текстом сделали
-- («read_pdf:pdftotext → починка: cp1251», «read_mail:msg → частей 3, прочитано 2»).
-- Без него следующая потеря снова неизмерима: видно, ЧТО файл не прочитан, и не
-- видно, КТО его читал (правило 16). Только имена читателей и счётчики — ни имён
-- файлов, ни содержимого (правило 17: колонка доезжает до сводок).
alter table lib_files add column if not exists read_chain      text;
-- НАША КОМПАНИЯ КАРТОЧКИ: название нашего юрлица, от которого заведена сделка или
-- карточка запроса поставщику (mycompanyId, по списку компаний с флагом «моя
-- компания»). Запросы уходят от разных наших компаний, и «мы» — это не одно имя.
-- Пусто — поле карточки не заполнено или компания не из списка; никогда не
-- «КВАНТ по умолчанию». Колонка nullable и без default — правка каталога,
-- таблица не переписывается.
alter table lib_files add column if not exists our_company     text;
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
do $$
declare кому text := lib_роли_которые_есть(array['anon', 'authenticated']);
begin
  if кому is not null then
    execute format('revoke all on lib_demand_live from %s', кому);
  end if;
end $$;

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

-- ─────────────────────────────────────────────────────────────────────────────
-- Смычка спроса с каталогом. В lib_demand полтора миллиона строк с артикулами,
-- в lib_parts — двенадцать тысяч опознанных деталей с машиной, узлом и
-- исполнителями. Пока они не связаны, спрос остаётся текстом: по нему нельзя
-- сказать ни какую машину чаще всего спрашивают, ни какой узел.
--
-- Ключ считается одной функцией на оба конца — так же, как part_key в
-- загрузчиках (без регистра, пунктуации и с «ё» → «е»). Если развести правило
-- по двум местам, оно разойдётся, и связь молча опустеет.
-- КЛЮЧ АРТИКУЛА ПЕРЕЕХАЛ В schema.sql. Он применяется первым, а ключ нужен уже
-- там: виду lib_work_queue и индексу по ключу цены. Две копии одного правила
-- расходятся молча, поэтому здесь её больше нет — только ссылка.

-- ИНДЕКС ПО КЛЮЧУ КАТАЛОЖНОГО НОМЕРА. Заведён 22.09.2026 по замеру: у 82 деталей
-- из 13 501 id не равен lib_pn_key(catalog_no) — id считается по catalog_norm,
-- когда тот заполнен. Такая деталь из соединения по id выпадает МОЛЧА: связи
-- нет, и это неотличимо от «детали в каталоге нет». Тридцать артикулов котировок
-- попали именно в этот хвост, и все тридцать ведут к машине.
--
-- Без индекса соединение по выражению читает таблицу целиком на каждую строку:
-- замер мостов на этом упал по statement_timeout. lib_pn_key объявлена immutable,
-- поэтому индекс по выражению допустим.
create index if not exists lib_parts_cat_key on lib_parts (lib_pn_key(catalog_no));

drop view if exists lib_demand_catalog;
create view lib_demand_catalog with (security_invoker = true) as
  select d.id, d.deal_id, d.item_name, d.part_number, d.qty, d.unit,
         d.segment_id as demand_segment, d.source,
         p.id as part_id, p.name as part_name, p.oem, p.unit_id, p.model
    from lib_demand d
    join lib_parts p on p.id = lib_pn_key(d.part_number)
   where coalesce(btrim(d.part_number), '') <> '';

-- Индекс по тому же выражению: без него соединение полутора миллионов строк с
-- каталогом — последовательный проход с пересчётом функции на каждой строке.
-- CONCURRENTLY и последним оператором — по той же причине, что выше.
create index concurrently if not exists lib_demand_pnkey
  on lib_demand (lib_pn_key(part_number));
