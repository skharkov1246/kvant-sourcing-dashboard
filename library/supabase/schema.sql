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
-- ─────────────────────────────────────────────────────────────────────────────
-- КЛЮЧ АРТИКУЛА. Одна функция на всю базу: без регистра, без пунктуации, «ё» → «е».
-- Так же считает part_key в загрузчиках; разведи правило по двум местам — оно
-- разойдётся, и связь молча опустеет.
--
-- ПОЧЕМУ ЗДЕСЬ, А НЕ В schema_junk.sql. Раньше она жила там, а этот файл
-- применяется ПЕРВЫМ. 22.09.2026 вид lib_work_queue и индекс по ключу цены стали
-- её вызывать — и схема перестала применяться на ЧИСТОЙ базе: «function
-- lib_pn_key(text) does not exist». Против прода это не проявлялось никогда, там
-- функция уже есть; поймал тест применения на чистой базе. Объявление должно
-- стоять раньше первого использования, а использований теперь два файла.
create or replace function lib_pn_key(t text) returns text
  language sql immutable parallel safe as $$
    -- Порядок важен: сначала регистр, потом «ё». В обратном порядке заглавная
    -- «Ё» переживает замену, после lower() становится «ё» и вылетает как
    -- посторонний знак — ключ «шайба12» вместо «шайба12е». Ровно на этом
    -- разошлись SQL и Python при первой сверке.
    select left(regexp_replace(replace(lower(coalesce(t, '')), 'ё', 'е'),
                               '[^0-9a-zа-я]', '', 'g'), 80)
  $$;

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

-- Наш внутренний номер. Сорсер и склад говорят номерами KV, а в библиотеке
-- их не было вовсе: поиск по «KV-000753-4» не находил ничего, хотя номер выдан
-- и закреплён за деталью навсегда (pnw/data/kv_registry.json — номера не
-- переиздаются и не переиспользуются).
alter table lib_parts add column if not exists kv_no text;
create index if not exists lib_parts_kv on lib_parts (kv_no);

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
-- Направление и вид машины: реестр dict/machine.json разводит обозначения по
-- сегментам КВАНТа (gtu, gsho) и видам (турбина, горная машина). Без этого
-- справочник машин отвечает только по ГТУ, а половина реестра — горно-шахтное.
alter table lib_models add column if not exists segment_id text
  references lib_segments(id) on delete set null;
alter table lib_models add column if not exists kind text;
create index if not exists lib_models_segment on lib_models (segment_id);

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

-- Взаимозаменяемость: чей это на самом деле номер и чем позицию можно закрыть.
-- Каталожный номер сборщика почти никогда не номер изготовителя: Telsmith 14T47 —
-- это серийный подшипник SKF/Timken, и без такой связи сорсер ищет несуществующую
-- деталь у несуществующего изготовителя.
create table if not exists lib_part_alt (
  part_id    text not null references lib_parts(id) on delete cascade,
  alt_pn     text not null,
  kind       text not null,              -- номер изготовителя | замена | наш номер | аналог
  alt_maker  text,
  evidence   text,
  confidence text default 'med',
  source     text,
  created_at timestamptz default now(),
  primary key (part_id, alt_pn, kind)
);
create index if not exists lib_part_alt_pn on lib_part_alt (alt_pn);

-- Как читать номер: вход в цепочку с того, что у сорсера есть на руках — шильдик
-- или строка из заявки. «MW21215M» — завод Линкольн, пять цифр и буква ревизии;
-- семь цифр без разделителей — Cummins, и у него номера образуют цепочку замен.
-- Ловушки хранятся отдельным полем: именно они стоят денег («401088700» — это
-- тот же 4010887 с лишними нулями).
create table if not exists lib_pn_patterns (
  id         text primary key,
  oem        text not null,
  pattern    text,                       -- MW#####X[/NN] или словесное описание
  meaning    text,
  examples   text,
  traps      text,
  status     text,                       -- подтверждено закупкой | из каталога | гипотеза
  source     text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists lib_pn_patterns_oem on lib_pn_patterns (oem);

-- Ведомость: из чего собрана машина, с уровнем вложенности и количеством. Без
-- неё «узел → запчасть» держится на словах описания, а не на конструкции.
create table if not exists lib_bom (
  id        text primary key,
  machine   text not null,
  model_id  text references lib_models(id) on delete set null,
  scheme    text,
  level     int,
  part_id   text references lib_parts(id) on delete set null,
  part_no   text not null,
  own_no    text,                        -- наш внутренний номер, если заведён
  qty       text,
  name      text,
  source    text,
  created_at timestamptz default now()
);
-- Узел в ведомости — как он назван в самой ведомости («ВАЛ ЭКСЦЕНТРИКОВЫЙ»),
-- а не как в нашем дереве узлов: у дробилки свои сборки, сводить их к турбинным
-- нельзя. Номер позиции и страница чертежа нужны, чтобы найти деталь в каталоге
-- изготовителя.
alter table lib_bom add column if not exists node text;
alter table lib_bom add column if not exists position_no text;
alter table lib_bom add column if not exists page text;
create index if not exists lib_bom_node on lib_bom (node);
create index if not exists lib_bom_machine on lib_bom (machine);
create index if not exists lib_bom_part on lib_bom (part_id);

-- Парк: какая машина где стоит и чья. Без этого справочник машин отвечает «что
-- бывает», а не «что чинить у этого заказчика», а сорсинг живёт вторым вопросом.
create table if not exists lib_fleet (
  id         text primary key,
  site       text not null,               -- площадка: ТЭЦ, энергоблок, КС
  owner      text,
  model_id   text references lib_models(id) on delete set null,
  model_raw  text,                        -- как машина названа в источнике
  units      text,                        -- сколько машин на площадке
  year       text,
  note       text,
  source     text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists lib_fleet_model on lib_fleet (model_id);
create index if not exists lib_fleet_owner on lib_fleet (owner);

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
-- Признак — вход в цепочку с той стороны, с которой приходит эксплуатация:
-- «выросла вибрация», «разброс по термопарам». Отдельная таблица, а не поле
-- дефекта: один признак ведёт к нескольким дефектам, и один дефект даёт
-- несколько признаков.
create table if not exists lib_symptoms (
  id         text primary key,
  name       text not null,
  unit_id    text references lib_units(id) on delete set null,
  measure    text,                        -- по чему видно: что и чем меряют
  defect     text,                        -- что это обычно значит
  confirm    text,                        -- чем подтвердить
  basis      text,                        -- откуда связка взята
  confidence text default 'low',
  source     text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists lib_symptoms_unit on lib_symptoms (unit_id);

-- Ребро «признак → чем подтвердить». Текстом это уже написано в самом признаке,
-- но по тексту нельзя выбрать «все признаки, которые проверяются вихретоковым
-- контролем», а сорсеру и инженеру нужно именно это: на инспекции время
-- ограничено, и она планируется от метода, а не от симптома.
create table if not exists lib_symptom_ops (
  symptom_id   text not null references lib_symptoms(id) on delete cascade,
  procedure_id text not null references lib_procedures(id) on delete cascade,
  source       text,
  created_at   timestamptz default now(),
  primary key (symptom_id, procedure_id)
);
create index if not exists lib_symptom_ops_proc on lib_symptom_ops (procedure_id);

-- Признак → дефект и дефект → ремонтное решение. До этих двух таблиц середина
-- цепочки связывалась текстом: у признака в поле defect было написано
-- «износ или проворот вкладыша», а в справочнике дефектов лежала запись с таким
-- именем — и перейти по ней было нельзя, потому что связи не было. Обе таблицы
-- многие-ко-многим сознательно: один признак даёт несколько дефектов (рост
-- вибрации 1× — и дисбаланс, и износ вкладыша), и один дефект лечится
-- несколькими операциями (прогар жаровой трубы — купонный ремонт И покрытие).
create table if not exists lib_symptom_defects (
  symptom_id text not null references lib_symptoms(id) on delete cascade,
  defect_id  text not null references lib_defects(id)  on delete cascade,
  source     text,
  created_at timestamptz default now(),
  primary key (symptom_id, defect_id)
);
create index if not exists lib_symptom_defects_defect on lib_symptom_defects (defect_id);

create table if not exists lib_defect_ops (
  defect_id    text not null references lib_defects(id)    on delete cascade,
  procedure_id text not null references lib_procedures(id) on delete cascade,
  source       text,
  created_at   timestamptz default now(),
  primary key (defect_id, procedure_id)
);
create index if not exists lib_defect_ops_proc on lib_defect_ops (procedure_id);

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

-- Проверка наличия у продавцов добавляет к ребру то, ради чего сорсер и звонит:
-- есть ли на складе, за сколько и когда. Вердикт хранится словом продавца
-- («oem_only», «pn_not_found»), а не сводится к «да/нет»: разница между «номер
-- не найден» и «только у OEM» — это две разные дальнейшие работы.
alter table lib_part_suppliers add column if not exists verdict   text;
alter table lib_part_suppliers add column if not exists in_stock  text;
alter table lib_part_suppliers add column if not exists stock_qty text;
alter table lib_part_suppliers add column if not exists lead_time text;
alter table lib_part_suppliers add column if not exists price     numeric;
alter table lib_part_suppliers add column if not exists currency  text;
create index if not exists lib_part_suppliers_verdict on lib_part_suppliers (verdict);


-- Исполнители приходят из семи разных исследований, и одна компания встречается
-- под разными написаниями. Ключ — нормализованное имя (library/load_suppliers.py):
-- без кавычек, форм собственности, регистра и пунктуации. Уникальность по
-- coalesce(segment_id,''), а не по segment_id: в SQL NULL не равен NULL, поэтому
-- обычное unique(segment_id, name) пропустило бы любое число дублей без сегмента.
alter table lib_suppliers add column if not exists name_key      text;
alter table lib_suppliers add column if not exists city          text;
alter table lib_suppliers add column if not exists contact_email text;
alter table lib_suppliers add column if not exists contact_phone text;
-- Стадия переписки и дата последнего касания: самое прикладное, что о поставщике
-- вообще можно знать — отвечает на «звонить ли снова». Приходит из CRM-выгрузки.
alter table lib_suppliers add column if not exists stage     text;
alter table lib_suppliers add column if not exists last_comm text;
create unique index if not exists lib_suppliers_key
  on lib_suppliers (coalesce(segment_id, ''), name_key);

-- А вот это мина, и вот почему. Ключ выше включает сегмент, а сегмент считается
-- правилом по тексту о компании. Стоит правилу измениться — и та же компания
-- получает другой сегмент, обычный ключ не срабатывает, и в таблице появляется
-- второй экземпляр одной фирмы. Обнаружено сравнением двух прогонов: 77 таких
-- пар из 4 480 после расширения списка источников.
--
-- Правильный ключ — имя без сегмента: компания это компания, а сегмент у неё
-- признак. Индекс строится ТОЛЬКО если дублей ещё нет, и молча ничего не делает,
-- если они уже появились: удалять чужие строки миграция не должна, это решение
-- владельца (CLAUDE.md). Тогда в журнале останется предупреждение.
do $$
declare дублей int;
begin
  if exists (select 1 from information_schema.columns
              where table_name = 'lib_suppliers' and column_name = 'name_key') then
    select count(*) into дублей from (
      select name_key from lib_suppliers where name_key is not null
       group by name_key having count(*) > 1) t;
    if дублей = 0 then
      create unique index if not exists lib_suppliers_name_key
        on lib_suppliers (name_key) where name_key is not null;
    else
      raise warning 'lib_suppliers: % дублей по name_key — уникальный индекс не построен, нужна ручная сверка', дублей;
    end if;
  end if;
end $$;

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
  source       text,                         -- 'прайс' | 'КП' | 'распознавание скана' | 'таможня' | 'маркетплейс' | 'оценка'
  source_url   text,
  confidence   text default 'med',
  note         text,
  created_at   timestamptz default now()
);
create index if not exists lib_prices_seg on lib_prices (segment_id);
create index if not exists lib_prices_pn  on lib_prices (part_number);
-- Цена без привязки к детали и без источника поступления — это просто число.
-- part_id связывает её с каталогом, feed помечает поток, из которого она пришла:
-- по feed загрузчик снимает свои прежние строки и потому идемпотентен (у цены
-- нет естественного ключа — одна деталь законно имеет и минимум, и максимум).
alter table lib_prices add column if not exists part_id  text
  references lib_parts(id) on delete cascade;
alter table lib_prices add column if not exists feed     text;
alter table lib_prices add column if not exists country  text;
alter table lib_prices add column if not exists year     int;
alter table lib_prices add column if not exists exporter text;
create index if not exists lib_prices_part on lib_prices (part_id);
create index if not exists lib_prices_feed on lib_prices (feed);
-- Цена из разобранного КП поставщика (feed = 'разбор КП'). Срок поставки и
-- карточка запроса — часть самой котировки: цена без срока не решение о закупке,
-- а карточка связывает цену с поставщиком, когда реестр до неё дойдёт
-- (supplier_id разбор не ставит: сопоставление карточки с компанией — отдельный
-- проход, и выдавать догадку за связь нельзя).
alter table lib_prices add column if not exists lead_days int;
alter table lib_prices add column if not exists rfq_id    text;
-- Компания-поставщик из карточки запроса, как её знает Битрикс. Записывается
-- в момент разбора: карточка в этот миг уже прочитана, а отдельный проход
-- стоил бы второго сплошного чтения портала. В supplier_id не пишется —
-- там внешний ключ на lib_suppliers, а ключ портала ведёт в sup_identifier
-- нового реестра: сведение двух реестров это отдельная работа, и подменять
-- один идентификатор другим нельзя.
alter table lib_prices add column if not exists rfq_company text;
-- РАЗРЕЗ ПО БРЕНДУ И МАШИНЕ. Цена без ответа на вопрос «к чему это» сравнима
-- только сама с собой: подшипник за 1 200 евро дорог или дёшев в зависимости от
-- того, в какой машине он стоит и чей он.
--
-- oem — производитель ИЗ СТРОКИ ФАЙЛА. Разборщик его находил и раньше (колонка
-- «производитель», «изготовитель», «бренд», «марка», «OEM»), но в строку цены не
-- писал: он уходил только в спрос.
--
-- rfq_brands — бренды С КАРТОЧКИ запроса (ufCrm18Brands), ключами, как и
-- rfq_company. Поле многозначное, поэтому храним список через запятую. Имена не
-- разрешаем здесь по той же причине, что и у поставщика: сопоставление ключа со
-- справочником — отдельный проход, а догадка вместо связи хуже пустоты.
--
-- Машина отдельной колонкой НЕ хранится намеренно: связь «деталь → машина» уже
-- есть в lib_part_models (9 869 связей), и ключ к ней — part_number, который
-- заполнен у 96 % строк цены. Вторая копия этой связи разошлась бы с первой.
alter table lib_prices add column if not exists oem        text;
alter table lib_prices add column if not exists rfq_brands text;
create index if not exists lib_prices_oem on lib_prices (oem);
create index if not exists lib_prices_rfq on lib_prices (rfq_id);
create index if not exists lib_prices_rfqco on lib_prices (rfq_company);
-- ─────────────────────────────────────────────────────────────────────────────
-- КОММЕРЧЕСКИЕ УСЛОВИЯ ПРЕДЛОЖЕНИЯ. Распоряжение владельца, 22.09.2026: «Очень
-- важно всегда искать в каждом входящем офере сопоставление кодов, цены, цены за
-- штуку, суммы, базис поставки (DDP, EXW), условия оплаты (LC, 30/70, 50/50 и
-- подобное), срок производства, срок поставки. Если их нет, нужно перепроверять
-- дважды и писать, что это не отсутствует, а верифицировано отсутствует».
--
-- СУММА ХРАНИТСЯ, А НЕ ТОЛЬКО УЧАСТВУЕТ В РАСЧЁТЕ. Прежде сумма строки читалась,
-- но в базу не попадала: из неё выводилась цена делением на количество, и на этом
-- след терялся. Проверить «цена × количество = сумма» задним числом было нечем, а
-- это единственная самопроверка ценовой строки, какая у нас есть.
alter table lib_prices add column if not exists total numeric(18,4);

-- СРОК ПРОИЗВОДСТВА ОТДЕЛЬНО ОТ СРОКА ПОСТАВКИ. Прежде был один lead_days, и
-- «срок изготовления 8 недель» с «поставка со склада» попадали в одно поле — что
-- встретилось раньше. Для закупки это разные величины: первая говорит, когда
-- деталь появится, вторая — когда доедет.
alter table lib_prices add column if not exists make_days int;

-- УСЛОВИЯ ОПЛАТЫ. pay_terms — нормализованная запись («30/70», «предоплата 100%
-- T/T», «отсрочка 30 дн», «LC»). pay_advance_pct — доля аванса числом, когда она
-- видна: именно она решает, сколько денег уходит до поставки, а из строки «30/70»
-- это видно только после разбора.
alter table lib_prices add column if not exists pay_terms       text;
alter table lib_prices add column if not exists pay_advance_pct smallint;

-- ОТКУДА ВЗЯТО КАЖДОЕ ПОЛЕ — И ЭТО НЕ СЛУЖЕБНАЯ МЕЛОЧЬ, А ТРЕБОВАНИЕ ВЛАДЕЛЬЦА.
-- Пустое поле означало разом две несовместимые вещи: «в КП не указано» (факт о
-- предложении — можно спросить поставщика) и «разбор не дошёл» (наш недочёт —
-- спрашивать надо разборщик). Выводы и действия разные, поэтому источник пишется
-- явно, значениями из library/offer_terms.py:
--
--   строка        — из колонки этой позиции;
--   файл          — из общих условий КП под таблицей (относится ко всем строкам);
--   нет           — ПРОВЕРЕНО ДВАЖДЫ: искали и в строке, и в тексте файла, не
--                   написано. Это и есть «верифицировано отсутствует»;
--   несколько     — в файле несколько разных значений, угадывать нельзя;
--   не проверено  — смотреть было нечего (скан без текстового слоя).
alter table lib_prices add column if not exists basis_src text;
alter table lib_prices add column if not exists pay_src   text;
alter table lib_prices add column if not exists lead_src  text;
alter table lib_prices add column if not exists make_src  text;

-- Проверка списком значений — ОТДЕЛЬНЫМ alter, а не внутри create table:
-- «create table if not exists» существующую таблицу НЕ меняет (CLAUDE.md,
-- правило 21), и на свежей базе всё было бы зелено, а на живой вставка падала.
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'lib_prices_basis_src_chk') then
    alter table lib_prices add constraint lib_prices_basis_src_chk
      check (basis_src is null or basis_src in ('строка','файл','нет','несколько','не проверено'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'lib_prices_pay_src_chk') then
    alter table lib_prices add constraint lib_prices_pay_src_chk
      check (pay_src is null or pay_src in ('строка','файл','нет','несколько','не проверено'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'lib_prices_lead_src_chk') then
    alter table lib_prices add constraint lib_prices_lead_src_chk
      check (lead_src is null or lead_src in ('строка','файл','нет','несколько','не проверено'));
  end if;
  if not exists (select 1 from pg_constraint where conname = 'lib_prices_make_src_chk') then
    alter table lib_prices add constraint lib_prices_make_src_chk
      check (make_src is null or make_src in ('строка','файл','нет','несколько','не проверено'));
  end if;
end $$;

-- Под вопрос «по каким позициям условия верифицировано отсутствуют» — это прямой
-- список того, что надо переспросить у поставщика.
create index if not exists lib_prices_pay on lib_prices (pay_terms);
create index if not exists lib_prices_src on lib_prices (basis_src, pay_src);

-- ИНДЕКС ПО КЛЮЧУ НОМЕРА ЦЕНЫ. Нужен виду lib_work_queue: он проверяет наличие
-- цены по ключу, и без индекса по выражению проверка читает таблицу цен целиком на
-- каждую позицию очереди. Частичный — только по потоку разбора КП: остальные
-- потоки вид не спрашивает, и включать их значило бы платить за них записью.
-- lib_pn_key объявлена immutable, поэтому индекс по выражению допустим.
create index if not exists lib_prices_pn_key on lib_prices (lib_pn_key(part_number))
  where feed = 'разбор КП';


-- ВНИМАНИЕ: источник — платная подписка (glbs.io), условия которой, как правило,
-- запрещают перепубликацию. Таблица и представление ниже живут только в закрытой
-- базе; на страницы портала числа из деклараций не выносятся.
--
-- Реестр таможенных декларации: кто фактически вёз такую номенклатуру, из какой
-- страны, под какой маркой, на каких условиях и по какой цене за килограмм. Это
-- не мнение и не оценка, а совершённые сделки — единственный наш источник,
-- который отвечает на вопрос «кто это уже возит», а не «кого мы нашли».
--
-- match хранит надёжность сопоставления словом источника, а не сводится к
-- «да/нет»: strong — декларация нашлась по партномеру (таких 80 из 42 314),
-- weak — только по коду ТН ВЭД и описанию. Между ними две разные дальнейшие
-- работы, и связь «деталь → поставка» ставится только по strong.
--
-- Импортёр лежит здесь с ИНН, но в lib_suppliers не идёт: он покупатель, а не
-- исполнитель, и смешать их значит сломать единственный вопрос, на который
-- справочник исполнителей отвечает.
create table if not exists lib_customs (
  id           text primary key,           -- хеш содержимого строки: прогон идемпотентен
  decl_date    text,
  hs10         text,
  hs4          text,                       -- товарная группа, по ней считается ориентир
  part_number  text,                       -- только для strong
  brand        text,
  exporter     text,
  importer     text,
  importer_inn text,
  origin       text,
  dispatch     text,
  incoterms    text,
  currency     text,
  net_kg       numeric,
  usd_kg       numeric,
  value_usd    numeric,
  descr        text,
  match        text,                       -- strong | weak
  source       text,                       -- файл выгрузки: без него выборку не повторить
  created_at   timestamptz default now()
);
-- Узел выводится из описания товара в декларации тем же правилом, что и для
-- каталога, но доверие к нему ниже: описание пишет декларант, а не инженер, и
-- сверить его не с чем — ручной разметки деклараций у нас нет. Поэтому узел
-- здесь отвечает на вопрос «кто возит детали ротора», а не служит разметкой.
alter table lib_customs add column if not exists unit_id text
  references lib_units(id) on delete set null;
create index if not exists lib_customs_unit  on lib_customs (unit_id);
create index if not exists lib_customs_hs4   on lib_customs (hs4);
create index if not exists lib_customs_pn    on lib_customs (part_number);
create index if not exists lib_customs_exp   on lib_customs (exporter);
create index if not exists lib_customs_brand on lib_customs (brand);

-- Ценовой ориентир по товарной группе, а НЕ цена детали: в одной группе лежат и
-- коронка, и корпус, и расходник. Отвечает «двадцать долларов за килограмм для
-- этой группы — дорого или дёшево», и только на это. Меньше двадцати строк в
-- группе — медиана шум, поэтому такие группы отброшены прямо в представлении.
-- Сносится перед созданием: на живой базе «create or replace» падает, если
-- у вида поменялось имя колонки (tests/test_schema_views_droppable.py).
drop view if exists lib_customs_bench;
create or replace view lib_customs_bench
  with (security_invoker = true) as
select hs4,
       count(*)                                                as поставок,
       round(percentile_cont(0.25) within group (order by usd_kg)::numeric, 2) as p25,
       round(percentile_cont(0.50) within group (order by usd_kg)::numeric, 2) as медиана,
       round(percentile_cont(0.75) within group (order by usd_kg)::numeric, 2) as p75,
       min(decl_date)                                          as с_даты,
       max(decl_date)                                          as по_дату
  from lib_customs
 where hs4 is not null and usd_kg is not null
 group by hs4
having count(*) >= 20;

-- Тот же ориентир, но по узлу машины, а не по товарной группе: «сколько стоит
-- килограмм деталей ротора» — вопрос инженера, а «8431» — вопрос таможни.
-- Порог тот же: меньше двадцати поставок — не ориентир, а шум.
drop view if exists lib_customs_bench_unit;
create or replace view lib_customs_bench_unit
  with (security_invoker = true) as
select unit_id,
       count(*)                                                as поставок,
       count(distinct exporter)                                as экспортёров,
       round(percentile_cont(0.50) within group (order by usd_kg)::numeric, 2) as медиана,
       round(percentile_cont(0.75) within group (order by usd_kg)::numeric, 2) as p75
  from lib_customs
 where unit_id is not null and usd_kg is not null
 group by unit_id
having count(*) >= 20;

-- Расход и стоимость обслуживания: что на машине меняют, как часто и почём.
-- Звено «ремонтное решение» знало ЧТО делают, но не знало НИ КОГДА, НИ ПОЧЁМ:
-- 81 операция из 89 без интервала. Здесь и то, и другое — по узлу и по машине.
--
-- ЭТО РАСЧЁТ, А НЕ НАШИ СЧЕТА: источник собран по типовым интервалам ТО
-- изготовителей и нашим ценовым вилкам при 8 000 часов работы в год. Допущение
-- по часам хранится в строке (hours_year), источник назван расчётом. Смешать
-- оценку с фактом здесь дешевле всего, а разделить потом — дороже всего.
--
-- Цена за единицу и цена за год — разные колонки: годовая получается умножением
-- на расход (у головки блока восемь штук в год), и подстановка одной вместо
-- другой завысила бы позицию в восемь раз.
create table if not exists lib_consumption (
  id          text primary key,             -- хеш «машина + позиция»: прогон идемпотентен
  model_id    text references lib_models(id) on delete set null,
  model_raw   text,                          -- как машина названа в источнике
  unit_id     text references lib_units(id) on delete set null,
  name        text not null,
  qty_year    numeric,                       -- сколько штук в год
  usd_unit    numeric,                       -- цена за единицу
  usd_year    numeric,                       -- стоимость в год
  interval_h  numeric,                       -- интервал замены, моточасы
  hours_year  numeric,                       -- допущение о наработке, при котором считано
  note        text,
  source      text,
  created_at  timestamptz default now()
);
create index if not exists lib_consumption_model on lib_consumption (model_id);
create index if not exists lib_consumption_unit  on lib_consumption (unit_id);

-- Годовая стоимость содержания по машине: чем она набирается и что в ней главное.
-- Отвечает на вопрос, который задают первым, когда выбирают между ремонтом и
-- заменой, и который до сих пор не отвечался вовсе.
drop view if exists lib_maintenance_cost;
create or replace view lib_maintenance_cost
  with (security_invoker = true) as
select coalesce(m.name, c.model_raw)         as машина,
       c.model_id                              as ключ_машины,
       count(*)                                as позиций,
       round(sum(c.usd_year)::numeric, 0)      as usd_в_год,
       round(min(c.interval_h)::numeric, 0)    as самый_частый_интервал_ч,
       max(c.hours_year)                       as при_наработке_ч
  from lib_consumption c
  left join lib_models m on m.id = c.model_id
 group by coalesce(m.name, c.model_raw), c.model_id;

-- Очередь работ по деньгам: где у нас нет цены, а сумма по позиции большая.
-- Список работ сорсера шёл по порядку строк заявки, а не по деньгам; здесь он
-- отсортирован суммой. 757 позиций на 2,17 млн долларов, и ни по одной цены нет.
--
-- ЦЕН ЗДЕСЬ НЕТ — ЕСТЬ АДРЕС: сделка, имя файла, сколько в нём строк и сколько с
-- ценой. По адресу цену достаёт library/quotes.py. Источник считан с
-- отрицательным контролем: выдуманные номера (перестановка цифр внутри
-- настоящего) дали 0,0 % совпадений против 60,4 % у настоящих, и это записано
-- в source каждой строки — иначе через месяц не отличить измеренное от
-- правдоподобного.
create table if not exists lib_exposure (
  id               text primary key,          -- нормализованный номер позиции
  part_id          text references lib_parts(id) on delete set null,
  part_number      text not null,
  name             text,
  qty              numeric,
  usd_exposure     numeric,                   -- сколько денег стоит за позицией
  have_price       boolean default false,
  deal             text,                      -- адрес: в какой сделке искать
  file             text,                      -- и в каком файле
  file_rows        int,
  file_rows_priced int,                       -- сколько строк файла с ценой
  addresses        int,                       -- сколько всего адресов у позиции
  source           text,
  created_at       timestamptz default now(),
  updated_at       timestamptz default now()
);
create index if not exists lib_exposure_usd  on lib_exposure (usd_exposure desc);
create index if not exists lib_exposure_part on lib_exposure (part_id);

-- Очередь работ: позиции без цены по убыванию суммы, с адресом и с тем, что о
-- детали уже известно. Сорсер начинает сверху, а не с первой строки заявки.
--
-- «БЕЗ ЦЕНЫ» СПРАШИВАЕТСЯ У БАЗЫ, А НЕ ТОЛЬКО У ФЛАГА ИСТОЧНИКА. have_price
-- посчитан при сборке файла-источника и с тех пор не меняется: цена, извлечённая
-- завтра разбором КП, его не сдвинет, и очередь вечно показывала бы уже сделанную
-- работу. Поэтому вид дополнительно смотрит в lib_prices.
--
-- НО ЗАКРЫВАЕТ ПОЗИЦИЮ ТОЛЬКО ЦЕНА ИЗ КП ПОСТАВЩИКА («разбор КП»). Проверено на
-- копии базы: если считать ценой ЛЮБУЮ строку lib_prices, очередь падает с 754
-- позиций и 2 168 592 долларов до 415 позиций и 388 долларов — то есть работа
-- объявляется сделанной, потому что у детали есть ценовой коридор RFQ или оценка
-- по типу из каталога ЗИП. Оценка — не предложение поставщика; это та же ошибка,
-- что «наличие — не одно состояние» в правилах выгрузок владельцу.
drop view if exists lib_work_queue;
create or replace view lib_work_queue
  with (security_invoker = true) as
select e.part_number,
       e.name,
       e.qty,
       round(e.usd_exposure::numeric, 0)      as usd,
       e.deal                                  as где_искать,
       e.file                                  as файл,
       e.file_rows_priced                      as строк_с_ценой_в_файле,
       p.unit_id                               as узел,
       p.kv_no                                 as наш_номер,
       (select count(*) from lib_part_suppliers s where s.part_id = p.id) as исполнителей
  from lib_exposure e
  left join lib_parts p on p.id = e.part_id
 where not e.have_price
   -- ЖИВАЯ ПРОВЕРКА ЦЕНЫ — ПО КЛЮЧУ НОМЕРА, А НЕ ПО part_id. Прежняя форма
   -- (pr.part_id = e.part_id) не срабатывала НИКОГДА, по двум причинам сразу:
   --
   --   1. поток «разбор КП» part_id не пишет вовсе (library/price_store.py
   --      кладёт None: реестры деталей и котировок не сведены);
   --   2. у позиции вне каталога e.part_id тоже NULL (library/load_exposure.py
   --      ставит его только для ключей, найденных в каталоге), а NULL = NULL
   --      истиной не бывает.
   --
   -- Из-за этого очередь работ вечно показывала уже прокотированные позиции, и
   -- заметить это было нечем: пустая проверка выглядит как «цен ещё нет».
   -- Ключ один и тот же: lib_exposure.id считается part_key(номер), а это та же
   -- нормализация, что lib_pn_key. Соединение по ключу — как во всём остальном
   -- коде (см. СЦЕПКУ в library/crossref.py).
   --
   -- have_price оставлен рядом намеренно: это снимок «цена была у нас на момент
   -- выгрузки», факт из источника, а не живая проверка. Одно не заменяет другое.
   and not exists (select 1 from lib_prices pr
                    where lib_pn_key(pr.part_number) = e.id
                      and pr.price is not null
                      and pr.feed = 'разбор КП')
 order by e.usd_exposure desc nulls last;

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
-- Статья знает свой сегмент, но не узел и не машину, а спрашивают именно так:
-- «что мы знаем про горячий тракт SGT-400». Связи ставит library/link_knowledge.py
-- по тем же правилам, что размечают позиции, — правило одно на оба места.
alter table lib_knowledge add column if not exists unit_id  text
  references lib_units(id) on delete set null;
alter table lib_knowledge add column if not exists model_id text
  references lib_models(id) on delete set null;
alter table lib_knowledge add column if not exists link_rule text;   -- чем связь поставлена
create index if not exists lib_knowledge_unit  on lib_knowledge (unit_id);
create index if not exists lib_knowledge_model on lib_knowledge (model_id);

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
alter table lib_fleet          enable row level security;
alter table lib_part_alt       enable row level security;
alter table lib_bom            enable row level security;
alter table lib_symptoms       enable row level security;
alter table lib_pn_patterns    enable row level security;
alter table lib_symptom_ops    enable row level security;
alter table lib_symptom_defects enable row level security;
alter table lib_defect_ops     enable row level security;
alter table lib_customs        enable row level security;
alter table lib_consumption    enable row level security;
alter table lib_exposure       enable row level security;

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


-- ЖУРНАЛ ЧИСЛОВЫХ ЗАМЕРОВ. Заведён 22.09.2026, потому что числовой истории
-- прогонов в проекте не было вовсе. «Журнал прогона» из CLAUDE.md — это stdout
-- задания Actions, и он живёт девяносто дней; `/admin/log` — журнал действий
-- людей, цифрам там не место. Поэтому «динамику день ко дню» до сих дня
-- приходилось сверять глазами по логам, а после их истечения — никак.
--
-- Таблица нарочно общая, а не «таблица про коды»: замеров у нас много
-- (воронка разбора, охват спроса, гигиена Bitrix), и каждому своя таблица —
-- это пять миграций вместо одной. Ключ — пара «замер + прогон»: повторный
-- запуск того же прогона не плодит точки, а перезаписывает свою.
--
-- ТОЛЬКО АГРЕГАТЫ (CLAUDE.md, правило 17): счёт кодов, строк и файлов. Ни
-- наименований позиций, ни номеров сделок, ни компаний — таблицу можно
-- показывать целиком. Колонка названа nums, а не values: values в PostgreSQL
-- — зарезервированное слово, и запрос к ней пришлось бы кавычить везде.
create table if not exists lib_metric_runs (
  metric      text not null,                 -- 'коды_и_цены', 'воронка_разбора', …
  run_key     text not null,                 -- номер прогона Actions либо метка времени
  measured_at timestamptz not null default now(),
  nums        jsonb not null,                -- {имя: число} — только числа
  note        text,                          -- оговорка к точке, если она есть
  primary key (metric, run_key)
);
create index if not exists lib_metric_runs_time on lib_metric_runs (metric, measured_at desc);

alter table lib_metric_runs enable row level security;

-- СТОРОНА ДОКУМЕНТА У ФАЙЛА. Заведено 22.09.2026 по замеру, который показал, что
-- «поле сделки» — это не одна сторона, а четыре:
--
--   заказчик   607 284 строки спроса · 75 029 кодов — ЭТО И ЕСТЬ НАШ СПРОС (41,7 %)
--   мы         350 179 строк · 45 102 кода — наши исходящие: «Offer from us»,
--              «Processed file for supplier», «Result file»
--   поставщик  432 016 строк · 69 361 код — предложения поставщиков, лежащие
--              в поле СДЕЛКИ, а не в карточке запроса («Offer from supplier(s)»)
--   внутренний  68 026 строк · 169 кодов — «Economics of the project»
--   неизвестно     268 строк · 7 кодов — правило покрывает почти всё
--
-- То есть 58 % строк «нашего спроса» спросом не были: две трети этого — уже
-- прокотированные позиции и наши же исходящие цены. Отбор по origin их не ловил:
-- все они лежат в полях сделки.
--
-- ПОЧЕМУ НУЖНО ХРАНИТЬ НАЗВАНИЕ. В field лежит КОД поля (ufCrm_…), а сторона
-- определяется по названию (library/doc_side.py). Названия даёт crm.item.fields —
-- один вызов, но только при доступе к порталу, а запросы к базе идут без него.
-- Поэтому название и вычисленная сторона хранятся рядом с файлом.
alter table lib_files add column if not exists field_title text;
alter table lib_files add column if not exists side text;

-- Проверка вида — отдельным блоком: create table if not exists существующую
-- таблицу не меняет, а add column ограничение не несёт (CLAUDE.md, правило 21).
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'lib_files_side_вид') then
    alter table lib_files add constraint lib_files_side_вид
      check (side is null or side in ('заказчик', 'мы', 'поставщик',
                                      'внутренний', 'неизвестно'));
  end if;
end $$;

create index if not exists lib_files_side on lib_files (side);
