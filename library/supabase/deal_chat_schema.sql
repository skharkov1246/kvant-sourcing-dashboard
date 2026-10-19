-- ЧАТ СДЕЛКИ: ОЦЕНКА ХОДА МОДЕЛЬЮ — курсор чтения и оценки (пилот, тень).
--
-- ЗАЧЕМ. Решение владельца 19.10.2026: по новым сообщениям чата открытых сделок
-- реализации и пресейла модель ставит сделке оценку — идёт / ждёт / стоит, куда
-- движется, риск, на ком мяч, ближайший срок. Пилот теневой: оценки только
-- копятся здесь, дашборд покажет пометки позже, отдельным решением. Пишет
-- scripts/chat_context.py (прогон «Чат сделки — оценка хода моделью»,
-- .github/workflows/chat-context.yml).
--
-- ТЕКСТА ЧАТА ЗДЕСЬ НЕТ И БЫТЬ НЕ МОЖЕТ. Хранятся только номера, даты и значения
-- из закрытых списков. Это не обещание, а ограничение базы: оценка (verdict)
-- проходит проверку deal_chat_verdict_ok — ровно эти ключи, значения только из
-- списков, сроки — датой, доказательства — номерами сообщений из оценённого
-- отрезка. Любая строка с текстом отвергается при вставке. Та же проверка
-- написана на Python (scripts/chat_context.проверить_оценку): два способа, и
-- тест tests/test_chat_context_sql.py сверяет их на каждом случае корпуса.
--
-- ДВЕ ТАБЛИЦЫ И ВИД.
--   deal_chat_state — курсор: до какого сообщения чат оценён (last_msg_id; 0 —
--     ещё ни разу), когда чат читали в последний раз (checked_at — по нему
--     очередь: давно не читанные первыми), когда в нём последний раз писал
--     человек. Курсор сдвигается ТОЛЬКО в одной транзакции с записью оценки и
--     после неё: оценка не записалась — сообщения будут оценены снова.
--   deal_chat_ctx — оценки, только дописываются. Менять строку нельзя, удалять
--     нельзя (триггер), откат прогона — пометкой voided_at (правило 5):
--         update deal_chat_ctx set voided_at = now() where run_id = '<ключ>';
--         delete from deal_chat_state where run_id = '<ключ>';  -- курсор назад
--     Второй оператор лишь возвращает курсор: следующий прогон оценит те же
--     сообщения заново (за деньги — это осознанно).
--   deal_chat_ctx_latest — последняя непомеченная оценка каждой сделки.
--
-- ИДЕМПОТЕНТНО (правило 21): create … if not exists, ограничения — сносом и
-- созданием, функции — create or replace, вид — сносом перед созданием.
-- Роли Supabase — только через проверку наличия (правило 20). Применение —
-- Actions → «ZIP base — apply DB migrations» (zip-db.yml) и сам прогон
-- chat-context.yml перед записью. Файл ни от чего не зависит.

set statement_timeout = '5min';
set lock_timeout      = '30s';   -- ALTER TABLE без него ставит ACCESS EXCLUSIVE в очередь

-- Закрытые списки. Совпадают с кортежами scripts/chat_context.py дословно —
-- это проверяет tests/test_chat_context.py по тексту этого файла.
create or replace function deal_chat_verdict_ok(v jsonb, lo bigint, hi bigint)
returns boolean language plpgsql immutable as $$
declare
  ключи      constant text[] := array['state', 'direction', 'risk', 'risk_kinds', 'blocker',
                                      'ball_role', 'reply_expected', 'due', 'evidence_msg_ids',
                                      'confidence'];
  состояния  constant text[] := array['идёт','ждёт_клиента','ждёт_поставщика','ждёт_нас','стоит','неясно'];
  направления constant text[] := array['вперёд','стоит','назад','неясно'];
  риски      constant text[] := array['низкий','средний','высокий'];
  виды_риска constant text[] := array['срок','оплата','техника','документы','цена','отказ_клиента','поставщик','претензия'];
  препятствия constant text[] := array['ответ','оплата','документы','техника','цена','поставка','согласование','другое'];
  роли       constant text[] := array['кам','сорсер','руководитель_сорсинга','сопровождение','логист','ответственный','клиент','поставщик','перевозчик','неясно'];
  виды_срока constant text[] := array['отгрузка','доставка','оплата','документы','ответ','подача','встреча','другое'];
  x jsonb;
  э jsonb;
  n int;
  d date;
begin
  if v is null or jsonb_typeof(v) <> 'object' then
    return false;
  end if;
  -- ровно эти ключи: ни одного лишнего (лишний ключ — место для текста)
  if not (v ?& ключи) or (v - ключи) <> '{}'::jsonb then
    return false;
  end if;

  if jsonb_typeof(v->'state') <> 'string' or not (v->>'state' = any (состояния)) then
    return false;
  end if;
  if jsonb_typeof(v->'direction') <> 'string' or not (v->>'direction' = any (направления)) then
    return false;
  end if;
  if jsonb_typeof(v->'risk') <> 'string' or not (v->>'risk' = any (риски)) then
    return false;
  end if;
  if jsonb_typeof(v->'ball_role') <> 'string' or not (v->>'ball_role' = any (роли)) then
    return false;
  end if;
  if jsonb_typeof(v->'reply_expected') <> 'boolean' then
    return false;
  end if;

  -- виды риска: не больше трёх, без повторов, из списка
  x := v->'risk_kinds';
  if jsonb_typeof(x) <> 'array' or jsonb_array_length(x) > 3 then
    return false;
  end if;
  for э in select * from jsonb_array_elements(x) loop
    if jsonb_typeof(э) <> 'string' or not ((э #>> '{}') = any (виды_риска)) then
      return false;
    end if;
  end loop;
  if (select count(distinct e) from jsonb_array_elements_text(x) e) <> jsonb_array_length(x) then
    return false;
  end if;

  -- что держит сделку: null или {kind, on_role}
  x := v->'blocker';
  if jsonb_typeof(x) = 'object' then
    if not (x ?& array['kind', 'on_role']) or (x - array['kind', 'on_role']) <> '{}'::jsonb then
      return false;
    end if;
    if jsonb_typeof(x->'kind') <> 'string' or not (x->>'kind' = any (препятствия)) then
      return false;
    end if;
    if jsonb_typeof(x->'on_role') <> 'string' or not (x->>'on_role' = any (роли)) then
      return false;
    end if;
  elsif jsonb_typeof(x) <> 'null' then
    return false;
  end if;

  -- ближайший срок: null или {date ГГГГ-ММ-ДД, kind}
  x := v->'due';
  if jsonb_typeof(x) = 'object' then
    if not (x ?& array['date', 'kind']) or (x - array['date', 'kind']) <> '{}'::jsonb then
      return false;
    end if;
    if jsonb_typeof(x->'kind') <> 'string' or not (x->>'kind' = any (виды_срока)) then
      return false;
    end if;
    if jsonb_typeof(x->'date') <> 'string' or (x->>'date') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      return false;
    end if;
    begin
      d := (x->>'date')::date;
    exception when others then
      return false;
    end;
    if to_char(d, 'YYYY-MM-DD') <> x->>'date' then
      return false;
    end if;
  elsif jsonb_typeof(x) <> 'null' then
    return false;
  end if;

  -- доказательства: от одного до пяти номеров сообщений оценённого отрезка
  x := v->'evidence_msg_ids';
  if jsonb_typeof(x) <> 'array' then
    return false;
  end if;
  n := jsonb_array_length(x);
  if n < 1 or n > 5 then
    return false;
  end if;
  for э in select * from jsonb_array_elements(x) loop
    if jsonb_typeof(э) <> 'number' or (э::text) !~ '^[1-9][0-9]{0,17}$' then
      return false;
    end if;
    if (э::text)::bigint < lo or (э::text)::bigint > hi then
      return false;
    end if;
  end loop;
  if (select count(distinct e) from jsonb_array_elements_text(x) e) <> n then
    return false;
  end if;

  -- уверенность 0…1
  if jsonb_typeof(v->'confidence') <> 'number' then
    return false;
  end if;
  if (v->>'confidence')::numeric < 0 or (v->>'confidence')::numeric > 1 then
    return false;
  end if;
  return true;
end $$;

create table if not exists deal_chat_state (
  deal_id       bigint      primary key,
  chat_id       bigint      not null,
  last_msg_id   bigint      not null default 0,   -- последнее оценённое сообщение; 0 — не оценивали
  last_human_at timestamptz,                      -- последнее сообщение человека из прочитанных
  checked_at    timestamptz not null default now(),
  run_id        text        not null,             -- прогон, создавший строку или сдвинувший курсор
  method_ver    text        not null
);

create table if not exists deal_chat_ctx (
  id            bigserial   primary key,
  deal_id       bigint      not null,
  assessed_at   timestamptz not null default now(),
  model         text        not null,             -- модель, давшая ответ (с учётом запасной)
  prompt_sha256 text        not null,             -- отпечаток запроса целиком; сам запрос не хранится
  method_ver    text        not null,
  msg_from_id   bigint      not null,
  msg_to_id     bigint      not null,
  verdict       jsonb       not null,
  confidence    numeric     generated always as ((verdict->>'confidence')::numeric) stored,
  tokens_in     integer     not null default 0,
  tokens_out    integer     not null default 0,
  usd           numeric(12, 6) not null default 0,
  run_id        text        not null,             -- без ключа прогона откат невозможен (правило 6)
  voided_at     timestamptz                       -- откат: пометка, а не удаление (правило 5)
);

do $$
begin
  alter table deal_chat_state drop constraint if exists deal_chat_state_ids_check;
  alter table deal_chat_state add constraint deal_chat_state_ids_check
    check (deal_id > 0 and chat_id > 0 and last_msg_id >= 0);
  alter table deal_chat_state drop constraint if exists deal_chat_state_keys_check;
  alter table deal_chat_state add constraint deal_chat_state_keys_check
    check (run_id ~ '^[A-Za-z0-9._-]{1,80}$' and method_ver ~ '^[a-z0-9.-]{1,40}$');

  alter table deal_chat_ctx drop constraint if exists deal_chat_ctx_ids_check;
  alter table deal_chat_ctx add constraint deal_chat_ctx_ids_check
    check (deal_id > 0 and msg_from_id > 0 and msg_from_id <= msg_to_id);
  -- служебные поля — тоже без места для текста
  alter table deal_chat_ctx drop constraint if exists deal_chat_ctx_keys_check;
  alter table deal_chat_ctx add constraint deal_chat_ctx_keys_check
    check (run_id ~ '^[A-Za-z0-9._-]{1,80}$' and method_ver ~ '^[a-z0-9.-]{1,40}$'
           and model ~ '^[a-z0-9.-]{1,80}$' and prompt_sha256 ~ '^[0-9a-f]{64}$');
  alter table deal_chat_ctx drop constraint if exists deal_chat_ctx_spend_check;
  alter table deal_chat_ctx add constraint deal_chat_ctx_spend_check
    check (tokens_in >= 0 and tokens_out >= 0 and usd >= 0);
  alter table deal_chat_ctx drop constraint if exists deal_chat_ctx_verdict_check;
  alter table deal_chat_ctx add constraint deal_chat_ctx_verdict_check
    check (deal_chat_verdict_ok(verdict, msg_from_id, msg_to_id));
end $$;

create index if not exists deal_chat_ctx_deal
  on deal_chat_ctx (deal_id, assessed_at desc, id desc) where voided_at is null;
create index if not exists deal_chat_ctx_run on deal_chat_ctx (run_id);
create index if not exists deal_chat_state_checked on deal_chat_state (checked_at);

-- Только дописывается: удаление и очистка запрещены, правка — только пометка
-- отката, и один раз. Владелец базы обходит права, но не триггер.
create or replace function deal_chat_ctx_append_only() returns trigger
language plpgsql as $$
begin
  if tg_op in ('DELETE', 'TRUNCATE') then
    raise exception 'deal_chat_ctx только дописывается: удалять нельзя, откат — пометкой voided_at';
  end if;
  -- confidence — вычисляемая колонка: в BEFORE-триггере у NEW она ещё пустая,
  -- а следует она из verdict, который сравнивается
  if old.voided_at is not null
     or (to_jsonb(new) - 'voided_at' - 'confidence')
        is distinct from (to_jsonb(old) - 'voided_at' - 'confidence') then
    raise exception 'deal_chat_ctx только дописывается: меняется лишь пометка отката voided_at, и один раз';
  end if;
  return new;
end $$;

drop trigger if exists deal_chat_ctx_append_only on deal_chat_ctx;
create trigger deal_chat_ctx_append_only before update or delete on deal_chat_ctx
  for each row execute function deal_chat_ctx_append_only();
drop trigger if exists deal_chat_ctx_no_truncate on deal_chat_ctx;
create trigger deal_chat_ctx_no_truncate before truncate on deal_chat_ctx
  for each statement execute function deal_chat_ctx_append_only();

-- Снос перед созданием: «create or replace view» не меняет состав колонок.
drop view if exists deal_chat_ctx_latest;
create view deal_chat_ctx_latest with (security_invoker = true) as
select distinct on (c.deal_id)
       c.deal_id, c.assessed_at, c.model, c.method_ver, c.msg_from_id, c.msg_to_id,
       c.verdict->>'state'                         as state,
       c.verdict->>'direction'                     as direction,
       c.verdict->>'risk'                          as risk,
       c.verdict->>'ball_role'                     as ball_role,
       (c.verdict->>'reply_expected')::boolean     as reply_expected,
       (c.verdict->'due'->>'date')::date           as due_date,
       c.verdict->'due'->>'kind'                   as due_kind,
       c.verdict, c.confidence, c.run_id
  from deal_chat_ctx c
 where c.voided_at is null
 order by c.deal_id, c.assessed_at desc, c.id desc;

create or replace function deal_chat_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

-- Доступ: RLS плюс FORCE, ничего у PUBLIC, anon и authenticated; сервисному
-- ключу — чтение и запись курсора, у оценок — чтение, вставка и пометка отката.
do $$
declare
  сх        text := current_schema();
  кому      text := deal_chat_роли_которые_есть(array['anon', 'authenticated']);
  служебная text := deal_chat_роли_которые_есть(array['service_role']);
  n         text;
begin
  foreach n in array array['deal_chat_state', 'deal_chat_ctx'] loop
    execute format('comment on table %I.%I is %L', сх, n, 'deal_chat_schema:v1');
    execute format('alter table %I.%I enable row level security', сх, n);
    execute format('alter table %I.%I force  row level security', сх, n);
    execute format('revoke all on table %I.%I from public', сх, n);
    if кому is not null then
      execute format('revoke all on table %I.%I from %s', сх, n, кому);
    end if;
  end loop;
  execute format('revoke all on sequence %I.deal_chat_ctx_id_seq from public', сх);
  execute format('revoke all on %I.deal_chat_ctx_latest from public', сх);
  if кому is not null then
    execute format('revoke all on sequence %I.deal_chat_ctx_id_seq from %s', сх, кому);
    execute format('revoke all on %I.deal_chat_ctx_latest from %s', сх, кому);
  end if;
  if служебная is not null then
    execute format('grant select, insert, update, delete on table %I.deal_chat_state to %s', сх, служебная);
    execute format('grant select, insert on table %I.deal_chat_ctx to %s', сх, служебная);
    execute format('grant update (voided_at) on table %I.deal_chat_ctx to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.deal_chat_ctx_id_seq to %s', сх, служебная);
    execute format('grant select on %I.deal_chat_ctx_latest to %s', сх, служебная);
  end if;
end $$;
