-- БИБЛИОТЕКА РЫНКОВ — хранилище исследований по всем направлениям оборудования.
--
-- Почему здесь, а не в репозитории: извлечённая из спецификаций номенклатура,
-- цены и поставщики — коммерческие данные заказчиков, а репозиторий публичный
-- (решение владельца от 08.09.2026). Доступ идёт только через воркер сайта,
-- сервисным ключом, после проверки входа Cloudflare Access и права на раздел.
--
-- Идемпотентно: можно прогонять повторно.
-- Применение: Actions → «ZIP base — apply DB migrations» (секрет SUPABASE_DB_URL).

-- Ограничение времени запроса снимаем на эту сессию. Колонки fts ниже —
-- generated always ... stored: их добавление переписывает таблицу целиком, а
-- lib_demand после разбора вложений — это сотни тысяч строк. С дефолтными двумя
-- минутами пула Supabase прогон №4 (11.09.2026) отвалился на «canceling statement
-- due to statement timeout» ровно на 120-й секунде.
set statement_timeout = '45min';
set lock_timeout      = '60s';   -- лучше упасть быстро, чем держать таблицу в блокировке

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Сегменты рынка. Строка = направление оборудования со своими цифрами спроса
--    и оценкой нашей изученности. Заполняется зондом по Битриксу и обновляется.
create table if not exists lib_segments (
  id            text primary key,            -- 'pumps', 'valves', 'gtu' …
  name          text not null,
  note          text,
  demand_deals  int,                         -- сделок за период наблюдения
  demand_sum    numeric,                     -- сумма этих сделок
  demand_rfq    int,                         -- запросов поставщикам
  won_deals     int,                         -- доехало до «Реализации»
  won_sum       numeric,
  depth_score   int,                         -- наша изученность: упоминаний в базе
  priority      int,                         -- место в очереди на исследование
  period_from   date,
  period_to     date,
  updated_at    timestamptz default now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Спрос в натуре: что именно спрашивают заказчики — из файлов, привязанных
--    к сделкам. В Битриксе строк товаров нет, вся номенклатура в спецификациях.
create table if not exists lib_demand (
  id           bigint generated always as identity primary key,
  segment_id   text references lib_segments(id) on delete set null,
  deal_id      text,                         -- карточка Битрикса, откуда взято
  item_name    text not null,
  oem          text,                         -- изготовитель по спецификации
  model        text,
  part_number  text,
  qty          numeric,
  unit         text,
  source       text default 'спецификация сделки',
  source_file  text,                         -- идентификатор файла в Битриксе
  created_at   timestamptz default now()
);
create index if not exists lib_demand_seg on lib_demand (segment_id);
create index if not exists lib_demand_pn  on lib_demand (part_number);
create index if not exists lib_demand_oem on lib_demand (oem);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2а. Каталог запчастей. Это НЕ спрос: lib_demand — что спрашивали, lib_parts —
--     что мы знаем о самой детали. Звено «запчасть» цепочки портала (CLAUDE.md,
--     «Куда мы идём»): каталожный номер, изготовитель, модели оборудования, где
--     применяется, материал, код ТН ВЭД.
create table if not exists lib_parts (
  id           text primary key,            -- нормализованный каталожный номер
  catalog_no   text not null,
  name         text not null,
  oem          text,
  model        text,                        -- модели оборудования, где стоит
  category     text,
  segment_id   text references lib_segments(id) on delete set null,
  hs_code      text,
  material     text,
  applications text,
  target_equipment text,                    -- узел или система: ВСО, ходовая, гидравлика
  aliases      text[],                      -- иные написания каталожного номера
  qty_quarter  numeric,                     -- потребность в квартал
  status       text,                        -- продавали | запрашивали | не трогали
  source       text,
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);
create index if not exists lib_parts_seg  on lib_parts (segment_id);
create index if not exists lib_parts_oem  on lib_parts (oem);
create index if not exists lib_parts_equip on lib_parts (target_equipment);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2в. Машина и узел — первые два звена цепочки портала (CLAUDE.md, «Куда мы
--     идём»). До сих пор их не было вовсе: деталь знала машину строкой
--     («SGT-400», «Cyclone», «Taurus 70, Taurus 70MD»), и свести две записи об
--     одной машине можно было только глазами.
create table if not exists lib_models (
  id           text primary key,            -- нормализованное имя: sgt400
  name         text not null,               -- SGT-400
  oem          text,                        -- Siemens Energy
  family       text,                        -- sgt | finspong | heavy | solar
  family_title text,
  legacy       text,                        -- Cyclone — имя до смены владельца завода
  power        text,
  efficiency   text,
  shafts       text,
  use_case     text,                        -- где стоит: ГПА, генерация, когенерация
  aliases      text[],                      -- все написания, по которым её ищут
  note         text,
  source       text,
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);
create index if not exists lib_models_family on lib_models (family);

-- Узлы машины деревом: система («Горячий тракт») → компонент («Жаровая труба»).
-- Узлы у промышленных ГТУ общие для Solar и Siemens, поэтому дерево одно на все
-- машины, а не своё на каждую.
create table if not exists lib_units (
  id          text primary key,             -- hot | hot.combustion-liner
  parent_id   text references lib_units(id) on delete set null,
  name        text not null,
  name_en     text,
  crit        text,                         -- A останавливает машину, B плановая, C расходник
  aftermarket text,                         -- насколько узел доступен помимо OEM
  note        text,
  source      text,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
create index if not exists lib_units_parent on lib_units (parent_id);

-- Ребро «запчасть → машина». Деталь встаёт на несколько машин, машина собирает
-- тысячи деталей — строкой в lib_parts.model это не выразить: по «Taurus 70,
-- Taurus 70MD» не выбрать обе машины.
create table if not exists lib_part_models (
  part_id    text not null references lib_parts(id) on delete cascade,
  model_id   text not null references lib_models(id) on delete cascade,
  source     text,
  created_at timestamptz default now(),
  primary key (part_id, model_id)
);
create index if not exists lib_part_models_model on lib_part_models (model_id);

alter table lib_parts add column if not exists unit_id    text references lib_units(id) on delete set null;
alter table lib_parts add column if not exists unit_rule  text;   -- чем определён узел
alter table lib_parts add column if not exists pn_pattern text;   -- шифровка номера у OEM
alter table lib_parts add column if not exists qty_demand numeric;-- сколько спрашивали
create index if not exists lib_parts_unit on lib_parts (unit_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2г. Диагностика, дефект и ремонтное решение — середина цепочки портала. Здесь
--     лежит то, что отличает инженерный портал от прайс-листа: как проверяют
--     узел, чем он выходит из строя и что с этим делают.
create table if not exists lib_procedures (
  id            text primary key,
  kind          text not null,              -- инспекция | контроль | ремонт | покрытие | модернизация
  name          text not null,
  unit_id       text references lib_units(id) on delete set null,
  scope         text,                       -- что именно делают
  duration      text,                       -- 3–5 недель
  model_family  text,                       -- к какому семейству машин относится
  performer     text,                       -- кто выполняет, если известно
  performer_key text,                       -- нормализованное имя для связи с lib_suppliers
  source        text,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);
create index if not exists lib_procedures_kind on lib_procedures (kind);
create index if not exists lib_procedures_unit on lib_procedures (unit_id);
create index if not exists lib_procedures_perf on lib_procedures (performer_key);

-- Дефект и решение хранятся вместе: без решения дефект — это жалоба, а не знание.
-- Последствие отделено от причины сознательно: закупщику нужно первое («прогар,
-- вылет фрагментов, мгновенный останов»), инженеру — второе.
create table if not exists lib_defects (
  id          text primary key,
  name        text not null,
  unit_id     text references lib_units(id) on delete set null,
  part_number text,                         -- каталожный номер, если дефект привязан к детали
  model       text,
  cause       text,
  consequence text,
  fix         text,                         -- ремонтное решение
  source      text,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
create index if not exists lib_defects_unit on lib_defects (unit_id);
create index if not exists lib_defects_pn   on lib_defects (part_number);

-- Извлечение из текстов ТЗ добавляет к дефекту происхождение и встречаемость:
-- то, что встретилось в сотне заданий, — типовое требование, а не находка.
alter table lib_defects add column if not exists seen        int default 1;
alter table lib_defects add column if not exists terms       text[];
alter table lib_defects add column if not exists deal_id     text;
alter table lib_defects add column if not exists source_file text;
create index if not exists lib_defects_seen on lib_defects (seen desc);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Поставщики: кто в мире делает это оборудование и его части.
create table if not exists lib_suppliers (
  id           bigint generated always as identity primary key,
  segment_id   text references lib_segments(id) on delete set null,
  name         text not null,
  country      text,
  kind         text,                         -- 'OEM' | 'ODM' | 'дистрибьютор' | 'сервис' | 'трейдер'
  site         text,
  oem_brands   text[],                       -- под какие марки делает
  strengths    text,
  lead_time    text,
  moq          text,
  certificates text,
  sanctions    text,                         -- ограничения на поставку в РФ
  confidence   text default 'med',           -- 'high' | 'med' | 'low'
  source_url   text,
  researched_by text,
  created_at   timestamptz default now(),
  unique (segment_id, name)
);
create index if not exists lib_suppliers_seg on lib_suppliers (segment_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3а. Ребро «запчасть → исполнитель». Без него ответить «кто делает эту деталь»
--     можно только перебором: у поставщика в описании тысяча позиций текстом.
create table if not exists lib_part_suppliers (
  part_id     text   not null references lib_parts(id) on delete cascade,
  supplier_id bigint not null references lib_suppliers(id) on delete cascade,
  makes       text,                         -- что именно делает под эту позицию
  catalog_url text,
  confidence  text default 'med',
  source      text,
  created_at  timestamptz default now(),
  primary key (part_id, supplier_id)
);
create index if not exists lib_part_suppliers_sup on lib_part_suppliers (supplier_id);


-- Исполнители приходят из семи разных исследований, и одна компания встречается
-- под разными написаниями. Ключ — нормализованное имя (library/load_suppliers.py):
-- без кавычек, форм собственности, регистра и пунктуации. Уникальность по
-- coalesce(segment_id,''), а не по segment_id: в SQL NULL не равен NULL, поэтому
-- обычное unique(segment_id, name) пропустило бы любое число дублей без сегмента.
alter table lib_suppliers add column if not exists name_key      text;
alter table lib_suppliers add column if not exists city          text;
alter table lib_suppliers add column if not exists contact_email text;
alter table lib_suppliers add column if not exists contact_phone text;
create unique index if not exists lib_suppliers_key
  on lib_suppliers (coalesce(segment_id, ''), name_key);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Ценообразование: из чего складывается цена и какова она у разных источников.
create table if not exists lib_prices (
  id           bigint generated always as identity primary key,
  segment_id   text references lib_segments(id) on delete set null,
  supplier_id  bigint references lib_suppliers(id) on delete set null,
  item_name    text,
  part_number  text,
  price        numeric,
  currency     text default 'EUR',
  basis        text,                         -- EXW | FOB | CIF | DDP
  qty          numeric,
  qty_unit     text,
  price_date   date,
  source       text,                         -- 'прайс' | 'КП' | 'таможня' | 'маркетплейс' | 'оценка'
  source_url   text,
  confidence   text default 'med',
  note         text,
  created_at   timestamptz default now()
);
create index if not exists lib_prices_seg on lib_prices (segment_id);
create index if not exists lib_prices_pn  on lib_prices (part_number);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Знание об оборудовании: устройство, режимы работы, критерии подбора,
--    типовые отказы, взаимозаменяемость. То, без чего нельзя грамотно
--    разговаривать с заказчиком и отличить аналог от подделки.
create table if not exists lib_knowledge (
  id           bigint generated always as identity primary key,
  segment_id   text references lib_segments(id) on delete set null,
  topic        text not null,                -- 'устройство' | 'подбор' | 'отказы' | 'аналоги' | 'рынок'
  title        text not null,
  body         text not null,
  sources      jsonb,                        -- список ссылок с датами обращения
  confidence   text default 'med',
  researched_by text,
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);
create index if not exists lib_knowledge_seg on lib_knowledge (segment_id, topic);

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Разбор проигрышей: почему сделка не доехала до реализации. Главный источник
--    для проверки гипотезы «глубина понимания цены влияет на конверсию».
create table if not exists lib_losses (
  id           bigint generated always as identity primary key,
  segment_id   text references lib_segments(id) on delete set null,
  deal_id      text,
  reason       text,                         -- 'цена' | 'срок' | 'не нашли' | 'нет аналога' | 'молчание' | 'иное'
  competitor   text,
  our_price    numeric,
  rival_price  numeric,
  currency     text,
  note         text,
  created_at   timestamptz default now()
);
create index if not exists lib_losses_seg on lib_losses (segment_id, reason);

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Поиск. Русская морфология, одна колонка на таблицу — чтобы искать по всей
--    библиотеке одним запросом, а не перебирать таблицы вручную.
alter table lib_demand    add column if not exists fts tsvector
  generated always as (to_tsvector('russian',
    coalesce(item_name,'') || ' ' || coalesce(oem,'') || ' ' || coalesce(model,'') || ' ' || coalesce(part_number,''))) stored;
alter table lib_suppliers add column if not exists fts tsvector
  generated always as (to_tsvector('russian',
    coalesce(name,'') || ' ' || coalesce(country,'') || ' ' || coalesce(strengths,''))) stored;
alter table lib_knowledge add column if not exists fts tsvector
  generated always as (to_tsvector('russian',
    coalesce(title,'') || ' ' || coalesce(body,''))) stored;
create index if not exists lib_demand_fts    on lib_demand    using gin (fts);
create index if not exists lib_suppliers_fts on lib_suppliers using gin (fts);
create index if not exists lib_knowledge_fts on lib_knowledge using gin (fts);

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Доступ. Включаем RLS и НЕ выдаём прав роли anon: библиотека читается только
--    сервисным ключом через воркер, за входом Cloudflare Access. Это сознательно
--    строже, чем у таблиц ЗИП, где anon исторически имеет полный доступ.
alter table lib_segments  enable row level security;
alter table lib_demand    enable row level security;
alter table lib_suppliers enable row level security;
alter table lib_prices    enable row level security;
alter table lib_knowledge enable row level security;
alter table lib_losses    enable row level security;
alter table lib_parts     enable row level security;
alter table lib_part_suppliers enable row level security;
alter table lib_models    enable row level security;
alter table lib_units     enable row level security;
alter table lib_part_models    enable row level security;
alter table lib_procedures     enable row level security;
alter table lib_defects        enable row level security;

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. Реестр разобранных файлов. Нужен для возобновляемости: обход 22 тысяч
--    вложений идёт частями и в несколько заходов, повторно скачивать уже
--    разобранное незачем. Здесь же видно, какая доля файлов нечитаема и почему
--    (например, сканы без текстового слоя — им понадобится распознавание).
create table if not exists lib_files (
  file_id      text primary key,             -- идентификатор вложения в Битриксе
  deal_id      text,
  origin       text,                         -- 'поле сделки' | 'задача' | 'письмо'
  field        text,                         -- имя UF-поля, если из поля
  kind         text,                         -- определён по сигнатуре содержимого
  size_bytes   bigint,
  status       text not null,                -- 'разобран' | 'пусто' | 'не скачался' | 'формат не читаем'
                                             -- | 'текст без спецификации' — текстовый слой есть,
                                             -- но файл признан документом закупки, а не спецификацией
                                             -- (library/docfilter.py). «Пусто» остаётся строго для
                                             -- файлов без текстового слоя, иначе оценка объёма
                                             -- распознавания сканов по нему завышена.
  reason       text,
  chars        int,                          -- сколько текста извлечено
  rows_found   int,                          -- сколько позиций номенклатуры получено
  segment_id   text,
  sha256       text,                         -- чтобы не разбирать один и тот же файл дважды
  processed_at timestamptz default now()
);
create index if not exists lib_files_deal   on lib_files (deal_id);
create index if not exists lib_files_status on lib_files (status);
create index if not exists lib_files_sha    on lib_files (sha256);

alter table lib_files enable row level security;

-- Распознавание сканов (library/ocr.py). Файлы без текстового слоя — 4 059
-- изображений и 8 195 «пусто» на 12.09.2026 — это фотографии и сканы
-- спецификаций: разбор не извлёк из них ни одной позиции. Отметка о
-- распознавании нужна для возобновляемости: повторный прогон пропускает
-- уже распознанное. Колонки nullable и без default — правка каталога.
alter table lib_files add column if not exists defects_at timestamptz;   -- когда из файла вынимали дефекты
create index if not exists lib_files_defects on lib_files (defects_at) where defects_at is null;
alter table lib_files add column if not exists ocr_at    timestamptz;
alter table lib_files add column if not exists ocr_chars int;
create index if not exists lib_files_ocr on lib_files (ocr_at) where ocr_at is null;

