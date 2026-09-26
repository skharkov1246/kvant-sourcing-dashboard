-- ДИЛЕРЫ РАЗВЕДКИ БРЕНДОВ, ЗАВЕДЁННЫЕ В РЕЕСТР КОМПАНИЙ — происхождение записи.
--
-- ЗАЧЕМ. Разрешение владельца 26.09.2026: дилеров из разведки брендов
-- (data/brand_research/*.json, раздел dealers), которых в реестре компаний нет,
-- завести в него. Заводит library/load_research_dealers.py: сущность sup_entity
-- с вечным номером KV-S, признаки sup_identifier (домен или ИНН с
-- доказательством) и строку здесь — на каждую запись разведки, откуда сущность
-- взялась: бренд, вид (своя площадка / официальный дилер / дилер), страна,
-- ссылка-доказательство, ключ прогона.
--
-- ПОЧЕМУ СПУТНИК, А НЕ КОЛОНКИ sup_entity. У одной компании бывает несколько
-- брендов и разный вид у каждого (официальный дилер одного, перепродавец
-- другого) — это строки, а не поле. И откат здесь — ПОМЕТКОЙ (правило 5):
-- rolled_back_at, а не удаление; номер KV остаётся выданным навсегда, и
-- повторная запись после отката возвращает той же компании тот же номер.
--
-- ЗАВИСИМОСТИ. sup_entity и sup_роли_которые_есть (suppliers_schema.sql)
-- обязаны стоять раньше; без них файл падает с названной причиной.
-- Применение — Actions → «ZIP base — apply DB migrations» (zip-db.yml), после
-- suppliers_schema.sql, и прогон «Поставщики — дилеры разведки брендов» перед
-- записью.
--
-- ИДЕМПОТЕНТНО. Роли Supabase — только через проверку наличия (правило 20).
-- Списки допустимых значений — отдельным alter (правило 21): «create table if
-- not exists» существующую таблицу не меняет.

set statement_timeout = '5min';
set lock_timeout      = '30s';   -- ALTER TABLE без него ставит ACCESS EXCLUSIVE в очередь

do $$
begin
  if to_regclass(current_schema() || '.sup_entity') is null then
    raise exception 'дилеры разведки требуют sup_entity (suppliers_schema.sql) — примените его раньше';
  end if;
  if to_regprocedure(current_schema() || '.sup_роли_которые_есть(text[])') is null then
    raise exception 'нет sup_роли_которые_есть — примените suppliers_schema.sql раньше';
  end if;
end $$;

-- Одна строка — одна запись dealers разведки, заведённая (или опознанная как
-- заведённая раньше) в сущность sup_id.
--   oem_key, dealer_no — бренд и место записи в разделе dealers его файла;
--   kind      — вид записи (library/offer_role.вид_дилера);
--   key_kind, key_value — сильный ключ, по которому сущность опознаётся;
--   evidence  — ссылка-доказательство: domain_source записи, иначе первая из
--               sources. Только в базе, за входом; в журнал не идёт (правило 17).
create table if not exists sup_research_dealer (
  id             bigserial primary key,
  sup_id         text not null references sup_entity(id) on delete cascade,
  oem_key        text not null,
  dealer_no      int  not null,
  company        text not null,
  kind           text not null,
  country        text,
  key_kind       text not null,
  key_value      text not null,
  evidence       text,
  run_id         text not null,   -- без ключа прогона откат невозможен (правило 6)
  created_at     timestamptz not null default now(),
  rolled_back_at timestamptz      -- откат: пометка, а не удаление (правило 5)
);

do $$
begin
  alter table sup_research_dealer drop constraint if exists sup_research_dealer_kind_check;
  alter table sup_research_dealer add constraint sup_research_dealer_kind_check
    check (kind in ('своя площадка', 'официальный дилер', 'дилер'));
  alter table sup_research_dealer drop constraint if exists sup_research_dealer_key_kind_check;
  alter table sup_research_dealer add constraint sup_research_dealer_key_kind_check
    check (key_kind in ('domain', 'inn'));
end $$;

create unique index if not exists sup_research_dealer_once
  on sup_research_dealer (run_id, oem_key, dealer_no, key_kind, key_value);
create index if not exists sup_research_dealer_run on sup_research_dealer (run_id);
create index if not exists sup_research_dealer_sup
  on sup_research_dealer (sup_id) where rolled_back_at is null;

-- Доступ — как у всего реестра компаний: RLS плюс FORCE, ничего у anon,
-- authenticated и PUBLIC, чтение и запись — сервисному ключу. Метка та же, что
-- у схемы поставщиков: блок 10 suppliers_schema.sql ищет таблицы sup_* без неё
-- и роняет применение.
do $$
declare
  сх        text := current_schema();
  кому      text := sup_роли_которые_есть(array['anon', 'authenticated']);
  служебная text := sup_роли_которые_есть(array['service_role']);
begin
  execute format('comment on table %I.sup_research_dealer is %L', сх, 'suppliers_schema:v1');
  execute format('alter table %I.sup_research_dealer enable row level security', сх);
  execute format('alter table %I.sup_research_dealer force  row level security', сх);
  execute format('revoke all on table %I.sup_research_dealer from public', сх);
  execute format('revoke all on sequence %I.sup_research_dealer_id_seq from public', сх);
  if кому is not null then
    execute format('revoke all on table %I.sup_research_dealer from %s', сх, кому);
    execute format('revoke all on sequence %I.sup_research_dealer_id_seq from %s', сх, кому);
  end if;
  if служебная is not null then
    execute format('grant select, insert, update on table %I.sup_research_dealer to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.sup_research_dealer_id_seq to %s', сх, служебная);
  end if;
end $$;
