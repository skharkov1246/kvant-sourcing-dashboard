-- Прайс-бук КП (разбор моделью) — закрытые таблицы.
--
-- Данные коммерческие: в репозитории только эта схема и загрузчик
-- (library/load_pricebook.py), строки пишет владелец или прогон с секретом.
-- Роли платформы называются только через проверку наличия (LESSONS, правило 20):
-- на чистом PostgreSQL их нет, и файл обязан примениться и там.
--
-- Повторяемость (LESSONS, правило 21): create … if not exists, ограничения —
-- отдельными drop/add, функция и вид — create or replace.

create table if not exists pb_kp_headers (
  pb_version     text        not null,
  request_id     bigint      not null,
  deal_id        bigint,
  supplier_id    bigint,
  doc_type       text,
  doc_date       text,
  price_date     date,
  currency_main  text,
  incoterm       text,
  vat_included   boolean,
  payment_class  text,
  lead_weeks_max numeric,
  valid_until    text,
  is_selected    boolean,
  confidence     numeric,
  model          text,
  ocr            boolean,
  run_id         text        not null,
  loaded_at      timestamptz not null default now(),
  primary key (pb_version, request_id)
);

-- Строка — по порядку в файле (row_no): номер позиции line_no в документе
-- повторяется, ключ по нему терял бы строки (замер 01.10.2026: 417 строк).
create table if not exists pb_kp_lines (
  pb_version           text        not null,
  row_no               integer     not null,
  request_id           bigint      not null,
  line_no              text,
  part_number          text,
  part_key             text,
  brand_manufacturer   text,
  description_original text,
  qty                  numeric,
  unit                 text,
  unit_price           numeric,
  total_price          numeric,
  currency_eff         text,
  unit_price_rub       numeric,
  offer_kind           text,
  price_in_source      boolean,
  confidence           numeric,
  price_date           date,
  is_selected          boolean,
  supplier_id          bigint,
  deal_id              bigint,
  reason               text,
  run_id               text        not null,
  loaded_at            timestamptz not null default now(),
  primary key (pb_version, row_no)
);

create index if not exists pb_kp_lines_part_key on pb_kp_lines (part_key);
create index if not exists pb_kp_lines_request on pb_kp_lines (pb_version, request_id);
create index if not exists pb_kp_lines_run on pb_kp_lines (run_id);

-- Причина несравнимости — второй способ счёта, на SQL. Загрузчик пишет причину,
-- посчитанную Python (library/pricebook.py); вид пересчитывает её здесь, и
-- расхождение двух способов — дефект одного из них (CLAUDE.md, «Качество
-- прежде скорости»). Порядок проверок — как в pricebook.ПРИЧИНЫ.
create or replace function pb_reason(
  unit_price numeric, currency_eff text, doc_type text, confidence numeric,
  price_in_source boolean, part_key text, incoterm text, vat_included boolean
) returns text language sql immutable as $$
  select case
    when unit_price is null then 'нет цены'
    when upper(coalesce(currency_eff, '')) not in ('RUB','USD','EUR','CNY','AED','GBP') then 'нет валюты'
    when doc_type = 'pricelist' then 'прайс-лист'
    when coalesce(confidence, 0) < 0.55 then 'низкая уверенность'
    when coalesce(price_in_source, false) is false then 'цена не найдена в исходнике'
    when length(coalesce(part_key, '')) < 3 then 'нет артикула'
    when upper(coalesce(incoterm, '')) not in ('EXW','FCA') then 'базис не FCA/EXW'
    when vat_included is null and upper(currency_eff) = 'RUB' then 'НДС не известен'
  end
$$;

-- Снос перед созданием: «create or replace view» не меняет имена и состав
-- колонок, и правка таблицы уронила бы миграцию на живой базе.
drop view if exists pb_kp_lines_checked;
create view pb_kp_lines_checked as
select l.*,
       pb_reason(l.unit_price, l.currency_eff, h.doc_type, l.confidence,
                 l.price_in_source, l.part_key, h.incoterm, h.vat_included) as reason_sql
  from pb_kp_lines l
  left join pb_kp_headers h on h.pb_version = l.pb_version and h.request_id = l.request_id;

create or replace function pb_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

do $$
declare n text;
declare сх text := current_schema();
declare кому text := pb_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := pb_роли_которые_есть(array['service_role']);
begin
  foreach n in array array['pb_kp_headers', 'pb_kp_lines'] loop
    execute format('comment on table %I.%I is %L', сх, n, 'pricebook_schema:v1');
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
  execute format('revoke all on %I.pb_kp_lines_checked from public', сх);
  if кому is not null then
    execute format('revoke all on %I.pb_kp_lines_checked from %s', сх, кому);
  end if;
  if служебная is not null then
    execute format('grant select on %I.pb_kp_lines_checked to %s', сх, служебная);
  end if;
end $$;
