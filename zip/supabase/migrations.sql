-- База ЗИП — расширение схемы Supabase: цены/таможня + чертежи.
-- Идемпотентно: можно прогонять повторно. Применение:
--   • Supabase → SQL Editor → вставить и Run;  ИЛИ
--   • CI (zip-db.yml): psql "$SUPABASE_DB_URL" -f zip/supabase/migrations.sql
--
-- Существующие таблицы (созданы ранее): positions, odm_suppliers, rfq_requests, change_log.
-- Ключ связи — positions.id (в оффлайн-SEED роль id играет pp; в БД это настоящий id).

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. price_records — цены конкурентов и таможенная статистика по годам.
--    Одна строка = один ценовой факт (отгрузка по таможне / позиция прайса / КП / сделка Bitrix).
create table if not exists price_records (
  id           bigint generated always as identity primary key,
  position_id  bigint not null references positions(id) on delete cascade,
  year         int,                     -- год факта (для таможни — год отгрузки)
  source       text not null,           -- 'таможня' | 'глобус-вэд' | 'маркетплейс' | 'кп' | 'bitrix' | 'прочее'
  importer     text,                    -- получатель (для таможни)
  exporter     text,                    -- отправитель/производитель
  country      text,                    -- страна происхождения/отправления
  qty          numeric,                 -- количество
  qty_unit     text,                    -- ед. изм. (шт/кг/компл)
  unit_price   numeric,                 -- цена за единицу
  total_price  numeric,                 -- сумма (если известна вместо/вместе с unit_price)
  currency     text default 'EUR',      -- 'EUR' | 'USD' | 'RUB' | 'CNY'
  incoterms    text,                    -- условия поставки (CIF/FOB/…)
  customs_decl text,                    -- № ГТД/ДТ (если есть)
  hs_code      text,                    -- ТН ВЭД по факту
  url          text,                    -- ссылка на источник (если открытый)
  confidence   text default 'med',      -- 'high' | 'med' | 'low'
  note         text,
  created_by   text,                    -- 'workflow' | email правившего вручную
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);
create index if not exists price_records_pos_year on price_records (position_id, year);
create index if not exists price_records_source   on price_records (source);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. drawings — чертежи/файлы по позиции (сами файлы — в Storage-бакете 'drawings').
create table if not exists drawings (
  id           bigint generated always as identity primary key,
  position_id  bigint not null references positions(id) on delete cascade,
  title        text,                    -- напр. «Чертёж хвостовика, ревизия B»
  rev          text,                    -- ревизия
  status       text default 'чертёж',   -- 'замер' | 'чертёж' | 'образец' | 'испытан' | 'одобрен'
  storage_path text,                    -- путь в бакете drawings (напр. '551/hvostovik-revB.pdf')
  mime         text,
  size_bytes   bigint,
  note         text,
  uploaded_by  text,
  uploaded_at  timestamptz default now()
);
create index if not exists drawings_pos on drawings (position_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Триггер updated_at для price_records (drawings обновляется реже — не критично).
create or replace function zip_touch_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

drop trigger if exists price_records_touch on price_records;
create trigger price_records_touch before update on price_records
  for each row execute function zip_touch_updated_at();

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Storage-бакет для чертежей (приватный). Создание идемпотентно.
insert into storage.buckets (id, name, public)
values ('drawings', 'drawings', false)
on conflict (id) do nothing;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. RLS-политики.
--    Сайт открыт (по решению владельца) и работает с anon-ключом, поэтому
--    даём anon read/write. Когда поставите пароль-гейт (_worker.js.example) —
--    доступ всё равно останется на anon-ключе, но за Basic-Auth воркера.
alter table price_records enable row level security;
alter table drawings      enable row level security;

drop policy if exists price_all on price_records;
create policy price_all on price_records for all
  to anon, authenticated using (true) with check (true);

drop policy if exists draw_all on drawings;
create policy draw_all on drawings for all
  to anon, authenticated using (true) with check (true);

-- Storage-объекты бакета drawings: чтение/запись/удаление для anon.
drop policy if exists drawings_read   on storage.objects;
create policy drawings_read on storage.objects for select
  to anon, authenticated using (bucket_id = 'drawings');

drop policy if exists drawings_write  on storage.objects;
create policy drawings_write on storage.objects for insert
  to anon, authenticated with check (bucket_id = 'drawings');

drop policy if exists drawings_delete on storage.objects;
create policy drawings_delete on storage.objects for delete
  to anon, authenticated using (bucket_id = 'drawings');

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. positions — master-data локализации (ERP): статус воронки, критичность,
--    потребность, срок, MOQ, ответственный. Идемпотентно.
alter table positions add column if not exists loc_status     text;   -- не начато|поиск|RFQ|образцы|тест|одобрено|серия|отложено
alter table positions add column if not exists criticality    text;   -- A|B|C (ABC-анализ)
alter table positions add column if not exists owner          text;   -- ответственный за локализацию
alter table positions add column if not exists annual_qty     numeric;-- годовая потребность
alter table positions add column if not exists lead_time_days int;    -- срок поставки, дней
alter table positions add column if not exists moq            numeric;-- минимальная партия

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. odm_suppliers — скоркарта и контакты (CRM-квалификация поставщика).
alter table odm_suppliers add column if not exists contact_email  text;
alter table odm_suppliers add column if not exists contact_wechat text;
alter table odm_suppliers add column if not exists contact_phone  text;
alter table odm_suppliers add column if not exists incoterms      text;
alter table odm_suppliers add column if not exists payment_terms  text;
alter table odm_suppliers add column if not exists certs          text;   -- ISO/CE/… через запятую
alter table odm_suppliers add column if not exists score_price    int;    -- 1..5
alter table odm_suppliers add column if not exists score_quality  int;
alter table odm_suppliers add column if not exists score_lead     int;
alter table odm_suppliers add column if not exists score_comm     int;    -- коммуникация
alter table odm_suppliers add column if not exists score_cert     int;    -- сертификация

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. samples — образцы: жизненный цикл заказ→склад→тест→решение + складская ячейка.
create table if not exists samples (
  id              bigint generated always as identity primary key,
  position_id     bigint not null references positions(id) on delete cascade,
  odm_supplier_id bigint references odm_suppliers(id) on delete set null,
  supplier_name   text,                       -- на случай образца вне списка ODM
  sample_no       text,                       -- внутренний № образца
  status          text default 'заказан',     -- заказан|в пути|на складе|на тесте|одобрен|брак|возврат
  ordered_at      date,
  received_at     date,
  qty             numeric,
  unit_price      numeric,
  currency        text default 'USD',
  warehouse       text,                        -- склад
  bin             text,                        -- ячейка/место хранения
  test_status     text,                        -- ожидает|годен|не годен
  test_result     text,                        -- заметка по итогам теста
  photo_path      text,                        -- фото образца в бакете samples
  note            text,
  created_by      text,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);
create index if not exists samples_pos    on samples (position_id);
create index if not exists samples_status on samples (status);
create index if not exists samples_bin    on samples (bin);

drop trigger if exists samples_touch on samples;
create trigger samples_touch before update on samples
  for each row execute function zip_touch_updated_at();

alter table samples enable row level security;
drop policy if exists samples_all on samples;
create policy samples_all on samples for all
  to anon, authenticated using (true) with check (true);

-- бакет для фото образцов
insert into storage.buckets (id, name, public)
values ('samples', 'samples', false)
on conflict (id) do nothing;

drop policy if exists samples_read on storage.objects;
create policy samples_read on storage.objects for select
  to anon, authenticated using (bucket_id = 'samples');
drop policy if exists samples_write on storage.objects;
create policy samples_write on storage.objects for insert
  to anon, authenticated with check (bucket_id = 'samples');
drop policy if exists samples_delete on storage.objects;
create policy samples_delete on storage.objects for delete
  to anon, authenticated using (bucket_id = 'samples');
