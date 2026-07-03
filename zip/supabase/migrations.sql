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
