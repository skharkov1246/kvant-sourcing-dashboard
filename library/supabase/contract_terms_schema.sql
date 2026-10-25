-- УСЛОВИЯ ДОГОВОРОВ И ФЛАГИ СВЕРКИ «ЗАКАЗЧИК ↔ ПОСТАВЩИК» — закрытые таблицы.
--
-- Пилот (решение владельца 19.10.2026): 30 последних сделок реализации, бюджет
-- модели 10 USD на прогон, запуск только вручную — прогон «Договоры — чтение
-- условий (пилот)» (.github/workflows/contract-terms.yml, scripts/contract_terms.py).
-- Условия договоров — коммерческие сведения: строки только здесь, браузеру (anon,
-- authenticated) не выдаются; на дашборд — отдельной правкой и только праву
-- «Поставщики: договоры и деньги». В репозитории — только эта схема и код.
--
--   contract_terms       одна строка — один файл документа одной сделки: карточки
--                        договора (СП-1174 разовый договор / спецификация / ДС,
--                        СП-1192 рамочный, прежние СП-132 разовый / ДС /
--                        спецификация и СП-175 рамочный / ген. соглашение) или
--                        Purchase order заказа СП-172 (card_entity 172, card_id —
--                        номер заказа). Рамочный под спецификациями нескольких
--                        сделок и заказов — строка у каждой пары «сделка, заказ»
--                        (ключ с deal_id и order_key = coalesce(order_id, 0), с
--                        25.10.2026). link_source — откуда документ у сделки:
--                        deal_link / order_link — карточка связана со сделкой (без
--                        связи с заказом и с ней), order_only — только с её заказом,
--                        framework — рамочный над спецификацией сделки,
--                        purchase_order — файл самого заказа без документа
--                        поставщика (scripts/contract_terms.py, ИСТОЧНИКИ).
--                        Карточка без файла и файл, который не
--                        прочитан, — тоже строка: status говорит, прочитан ли он
--                        и если нет — почему (правило 15, статус не врёт):
--                          ok        прочитан моделью в этом прогоне;
--                          reused    условия того же файла (sha256), прочитанного
--                                    раньше той же моделью и версией, — модель
--                                    второй раз не оплачивается;
--                          no_file   в карточке нет файла договора;
--                          download  файл не скачать и следующим прогоном: у
--                                    портала его нет (404 / 410), тело пустое, у
--                                    поля нет ссылки (причина — note);
--                          fetch     временный отказ закачки: сеть, 5xx, лимит
--                                    портала дольше ожидания, страница входа
--                                    (с 25.10.2026; прежде писался download);
--                          limit     файл больше предела чтения (страниц, кадров,
--                                    знаков) — модели не отдавался: условия из
--                                    части договора дали бы ложные флаги;
--                          budget    бюджет прогона кончился раньше, чем дошла
--                                    очередь;
--                          time      срок прогона вышел раньше, чем дошла очередь;
--                          format, model, parse, api — отказ чтения по классу.
--                        terms — что извлекла модель по contract_b2b.TERMS_SCHEMA,
--                        evidence — поле → {quote, page}: почему получилось
--                        значение (правило 16); dropped — поля, у которых модель
--                        дала значение без цитаты и оно снято («не доказано», а
--                        не «в договоре нет»). side — сторона документа, её
--                        называет модель (решение владельца 22.10.2026),
--                        side_source — 'model'; модель не назвала — сторона по
--                        карточке (решение владельца 25.10.2026), side_source
--                        'card', side = side_company; у непрочитанного и у
--                        документа, о стороне которого не сказали ни модель, ни
--                        компания, — null. Пересчёт без модели (contract_terms.py
--                        --mode reflag) пишет копии строк прежнего прогона со
--                        своим ключом и статусом reused. side_company —
--                        сверка (компания карточки против компании сделки и
--                        заказов СП-172); справочно: side_link — сторона по
--                        связи карточки (parentId172 — заказ поставщику),
--                        side_template — подпись «Чей шаблон» / «Тип документа».
--   contract_b2b_flags   флаг риска (contract_b2b.flags) по паре «условия
--                        заказчика сделки ↔ условия поставщика заказа order_id».
--   contract_deal_outcome исход разбора договоров сделки в прогоне (с 23.10.2026):
--                        пара / недочитана / не читается / без стороны / спорная /
--                        поставщик без заказа / нет пары / нет договоров
--                        («недочитана» — файл ещё не прочитан: бюджет, срок,
--                        временный отказ; «не читается» — файла нет у портала,
--                        формат, предел чтения: нужен человек). Пишется одной транзакцией
--                        с флагами и только у прогона, прошедшего гейт стороны: по
--                        нему вкладка «Реализация» отличает «договоров нет» и «рисков
--                        нет» от «сделку не читали» (scripts/realization_ctx.py).
--
-- Каждая строка с ключом прогона run_id (правило 6): откат —
--   delete from contract_deal_outcome where run_id = '…';
--   delete from contract_b2b_flags where run_id = '…';
--   delete from contract_terms     where run_id = '…';
-- Деньги — numeric (DATA_MODEL.md), версия правила — method_ver.
--
-- Повторяемость (правило 21): create … if not exists; колонки, добавленные позже, —
-- add column if not exists; проверки — drop/add по имени В СВОЕЙ ТАБЛИЦЕ. Схема —
-- из search_path, а не public: тест применяет файл в своей схеме. Роли платформы —
-- только через проверку наличия (правило 20). Применяется прогоном «ZIP base —
-- apply DB migrations» (zip-db.yml); писать в таблицы — только service_role.

set statement_timeout = '5min';
set lock_timeout      = '10s';   -- правило 12: ALTER TABLE без него встаёт в очередь

create table if not exists contract_terms (
  run_id      text        not null,
  method_ver  text        not null,
  deal_id     bigint      not null,
  card_entity integer     not null,
  card_id     bigint      not null,
  order_id    bigint,
  side        text,
  side_source text,
  side_template text,
  side_company  text,
  template    text,
  doc_type    text,
  file_field  text,
  file_id     text        not null default '',
  sha256      text,
  pages       integer,
  note        text,
  status      text        not null,
  model       text,
  terms       jsonb,
  evidence    jsonb,
  dropped     jsonb,
  usd         numeric(12, 6) not null default 0,
  created_at  timestamptz not null default now(),
  -- окончательный ключ — с order_key: колонка добавляется ниже (add column), там же
  -- ключ пересобирается — одним путём у новой таблицы и у таблицы прежней версии
  primary key (run_id, deal_id, card_entity, card_id, file_id)
);

create table if not exists contract_b2b_flags (
  run_id     text        not null,
  method_ver text        not null,
  deal_id    bigint      not null,
  order_id   bigint,
  code       text        not null,
  days       integer,
  created_at timestamptz not null default now()
);

create table if not exists contract_deal_outcome (
  run_id     text        not null,
  method_ver text        not null,
  deal_id    bigint      not null,
  outcome    text        not null,
  created_at timestamptz not null default now(),
  primary key (run_id, deal_id)
);

-- «create table if not exists» существующую таблицу не меняет (правило 21):
-- колонки, добавленные позже первой версии, — отдельными операторами здесь.
alter table contract_terms add column if not exists side_link text;   -- 22.10.2026
alter table contract_terms add column if not exists link_source text; -- 25.10.2026
-- заказ в ключе строки (25.10.2026): рамочный поставщика над спецификациями двух заказов
-- сделки — документ каждого заказа; order_id может быть пуст, а колонка ключа — нет
alter table contract_terms add column if not exists order_key bigint
  generated always as (coalesce(order_id, 0)) stored;
-- Строки версии до 22.10.2026: сторона — по связи карточки (side_source order_link /
-- deal_link). Связь переезжает в side_link, сторона — из ответа модели (только у
-- прочитанных), как пишет нынешний код; иначе новая проверка стороны не встанет.
-- Повтор — без изменений: условие по старым side_source.
update contract_terms
   set side_link   = coalesce(side_link, side),
       side        = case when terms->>'side' in ('customer', 'supplier') then terms->>'side' end,
       side_source = case when terms->>'side' in ('customer', 'supplier') then 'model' end
 where side_source in ('order_link', 'deal_link');

alter table contract_terms drop constraint if exists contract_terms_status_chk;
alter table contract_terms add constraint contract_terms_status_chk
  check (status in ('ok', 'reused', 'no_file', 'download', 'fetch', 'format', 'limit', 'budget',
                    'time', 'model', 'parse', 'api'));
-- side_source (25.10.2026, решение владельца): 'model' — сторону назвала модель; 'card' —
-- модель не назвала, сторона по компании карточки («сторона по карточке»: компания сделки —
-- заказчик, компания заказа СП-172 сделки и Purchase order заказа — поставщик), и тогда
-- side обязана совпасть с side_company. Список — дословно contract_terms.ИСТОЧНИКИ_СТОРОНЫ
-- (сверяет tests/test_contract_terms.py). Строки прежних версий ('model' или null) проходят
-- проверку как были. Повтор — тот же drop/add по имени (правило 21).
alter table contract_terms drop constraint if exists contract_terms_side_chk;
alter table contract_terms add constraint contract_terms_side_chk
  check ((side is null or side in ('customer', 'supplier'))
         and (side_link is null or side_link in ('customer', 'supplier'))
         and (side_source is null or side_source in ('model', 'card'))
         and ((side is null) = (side_source is null))
         and (side_source is distinct from 'card' or side = side_company)
         and (side_template is null or side_template in ('customer', 'supplier'))
         and (side_company is null or side_company in ('customer', 'supplier')));
-- С 25.10.2026 рамочный — документ каждой сделки, под которой стоит его спецификация, и
-- каждого её заказа: одна карточка и файл в прогоне — строка на пару «сделка, заказ».
-- Ключ без deal_id и order_key молча снял бы строки второй (on conflict do nothing) — он
-- пересобирается; строки прежней версии уникальны и по новому ключу (в нём колонок
-- больше). Повтор — без изменений: ключ уже с deal_id и order_key.
do $$
declare сх text := current_schema();
declare имя text;
declare полный boolean;
begin
  select k.conname, coalesce(bool_or(a.attname = 'deal_id'), false) and coalesce(bool_or(a.attname = 'order_key'), false)
    into имя, полный
    from pg_constraint k
    join pg_attribute a on a.attrelid = k.conrelid and a.attnum = any (k.conkey)
   where k.conrelid = format('%I.contract_terms', сх)::regclass and k.contype = 'p'
   group by k.conname;
  if имя is null or not полный then
    if имя is not null then
      execute format('alter table %I.contract_terms drop constraint %I', сх, имя);
    end if;
    execute format('alter table %I.contract_terms add constraint contract_terms_pkey '
                   'primary key (run_id, deal_id, card_entity, card_id, file_id, order_key)', сх);
  end if;
end $$;

-- 132, 175 — прежние смарт-процессы договоров, 172 — Purchase order заказа (25.10.2026)
alter table contract_terms drop constraint if exists contract_terms_entity_chk;
alter table contract_terms add constraint contract_terms_entity_chk
  check (card_entity in (1174, 1192, 132, 175, 172));
-- Список — дословно contract_terms.ИСТОЧНИКИ (сверяет tests/test_contract_terms.py);
-- у строк до 25.10.2026 — null.
alter table contract_terms drop constraint if exists contract_terms_link_chk;
alter table contract_terms add constraint contract_terms_link_chk
  check (link_source is null
         or link_source in ('deal_link', 'order_link', 'order_only', 'framework', 'purchase_order'));
-- Прочитанный файл несёт условия, непрочитанный — нет: иначе строка со статусом
-- отказа выглядела бы прочитанной.
alter table contract_terms drop constraint if exists contract_terms_terms_chk;
alter table contract_terms add constraint contract_terms_terms_chk
  check ((status in ('ok', 'reused')) = (terms is not null));
alter table contract_terms drop constraint if exists contract_terms_usd_chk;
alter table contract_terms add constraint contract_terms_usd_chk check (usd >= 0);

-- Список — дословно contract_terms.ИСХОДЫ (сверяет tests/test_contract_terms_sql.py).
alter table contract_deal_outcome drop constraint if exists contract_deal_outcome_chk;
alter table contract_deal_outcome add constraint contract_deal_outcome_chk
  check (outcome in ('пара', 'недочитана', 'не читается', 'без стороны', 'спорная',
                     'поставщик без заказа', 'нет пары', 'нет договоров')
         and deal_id > 0);

alter table contract_b2b_flags drop constraint if exists contract_b2b_flags_code_chk;
alter table contract_b2b_flags add constraint contract_b2b_flags_code_chk
  check (code ~ '^[A-Za-z]+$');

create index if not exists contract_terms_run  on contract_terms (run_id);
create index if not exists contract_terms_deal on contract_terms (deal_id);
-- Повтор чтения по тому же файлу берётся отсюда (status = ok), а не из модели.
-- method_ver несёт отпечаток подсказки и схемы ответа (scripts/contract_terms.py,
-- версия_метода): правка правил извлечения сама делает старые ответы чужими.
create index if not exists contract_terms_sha  on contract_terms (sha256, model, method_ver)
  where status = 'ok';
-- Флаг пары — один на прогон: повтор записи того же прогона не задваивает.
create unique index if not exists contract_b2b_flags_key
  on contract_b2b_flags (run_id, deal_id, coalesce(order_id, 0), code);
create index if not exists contract_b2b_flags_deal on contract_b2b_flags (deal_id);
-- последний исход сделки — по нему читает свод для вкладки
create index if not exists contract_deal_outcome_deal on contract_deal_outcome (deal_id, created_at desc);

create or replace function ct_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

-- Строки коммерческие: браузеру их не выдаёт ни одна роль, читает и пишет только
-- service_role (прогон с секретом). RLS включена и принудительна: даже владелец
-- таблицы без политики не видит строк через API.
do $$
declare n text;
declare сх text := current_schema();
declare кому text := ct_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := ct_роли_которые_есть(array['service_role']);
begin
  foreach n in array array['contract_terms', 'contract_b2b_flags', 'contract_deal_outcome'] loop
    execute format('comment on table %I.%I is %L', сх, n, 'contract_terms_schema:v1');
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
  execute format('revoke all on function %I.ct_роли_которые_есть(text[]) from public', сх);
end $$;
