-- ЧАТ СДЕЛКИ: ОЦЕНКА ХОДА МОДЕЛЬЮ — курсор чтения и оценки (пилот, тень).
--
-- ЗАЧЕМ. Решение владельца 19.10.2026: по новым сообщениям чата открытых сделок
-- реализации и пресейла модель ставит сделке оценку — идёт / ждёт / стоит, куда
-- движется, риск, на ком мяч, ближайший срок. Пилот теневой: оценки только
-- копятся здесь, дашборд покажет пометки позже, отдельным решением. Пишет
-- scripts/chat_context.py (прогон «Чат сделки — оценка хода моделью»,
-- .github/workflows/chat-context.yml).
--
-- ТЕКСТА ЧАТА ЗДЕСЬ НЕТ, КРОМЕ ЦИТАТЫ ФАКТА (V3, ниже): до 200 знаков, без ссылок, почт,
-- телефонов и имён — и строки «главного» (оценка модели, до 160 знаков, без сумм, ссылок и
-- имён). Всё прочее — номера, даты и значения из закрытых списков. Это не обещание, а
-- ограничение базы: оценка (verdict)
-- проходит проверку deal_chat_verdict_ok — ровно эти ключи, значения только из
-- списков, сроки — датой, доказательства — номерами сообщений из оценённого
-- отрезка. Любая строка с текстом отвергается при вставке. Та же проверка
-- написана на Python (scripts/chat_context.проверить_оценку): два способа, и
-- тест tests/test_chat_context_sql.py сверяет их на каждом случае корпуса.
--
-- ТРИ ТАБЛИЦЫ И ВИД (V2; V3 — ещё три таблицы и четыре вида, ниже).
--   deal_chat_state — курсор: до какого сообщения чат оценён (last_msg_id; 0 —
--     ещё ни разу), когда чат читали в последний раз (checked_at — по нему
--     очередь: давно не читанные первыми), когда в нём последний раз писал
--     человек, сколько вызовов модели подряд по нему не дали годной оценки
--     (fail_streak: после трёх чат неделю не берётся). Курсор оценки
--     сдвигается ТОЛЬКО в одной транзакции с записью оценки и после неё:
--     оценка не записалась — сообщения будут оценены снова. Неудача курсор не
--     трогает, но сдвигает очередь (checked_at): иначе чат, который всегда
--     ломается, стоял бы первым в каждом прогоне.
--   deal_chat_ctx — оценки, только дописываются. Менять строку нельзя, удалять
--     нельзя (триггер), откат прогона — пометкой voided_at (правило 5). Гейт
--     качества прогона не пройден — прогон сам помечает свои оценки и
--     возвращает курсоры их сделок в прежнее состояние. Вручную:
--         update deal_chat_ctx set voided_at = now() where run_id = '<ключ>';
--         delete from deal_chat_state where run_id = '<ключ>';  -- курсор назад
--     Второй оператор лишь возвращает курсор: следующий прогон оценит те же
--     сообщения заново (за деньги — это осознанно).
--   deal_chat_spend — каждый вызов модели и его цена, удачный или нет, только
--     дописывается и не помечается: потраченное потрачено. По нему прогон
--     считает, сколько уже ушло за сутки по Москве, и берёт себе не больше
--     остатка до 5 долларов (решение владельца 19.10.2026). Вызов, чей расход
--     неизвестен (обрыв, таймаут), записан по потолку и с пометкой estimated.
--   deal_chat_ctx_latest — последняя непомеченная оценка каждой сделки.
--
-- V3 (решение владельца 25.10.2026: «Да, и прочитать историю разово до $20»; «Читай весь
-- контекст и подсвечивай что важно на твой взгляд в сделках»). Оценка хода (verdict) —
-- побайтно прежняя: та же deal_chat_ctx и та же проверка. Новое — рядом:
--   deal_chat_facts — факты из чата: деньги (сумма, валюта, вид, сторона, НДС, дата),
--     договор (статус, сторона, где лежит, номер спецификации), сорсинг (сигнал и роль
--     автора), «главное» (вид, важность, одна строка оценки модели). Каждый факт — от
--     строки оценки (ctx_id) или разового чтения истории (hist_id), ровно от одной; его
--     доказательства — номера сообщений ВНУТРИ отрезка своей строки (триггер) и ключи
--     строк сводки по сделке. ЦИТАТА — до 200 знаков, ссылки, почты и телефоны скрыты,
--     имён нет (deal_chat_quote_ok): единственный кусок текста чата в базе, для проверки
--     факта. Только дописывается; повтор того же факта не пишется (fact_key); откат —
--     пометкой voided_at.
--   deal_chat_hist — разовое чтение истории сделки (старые сообщения до курсора суточного
--     прохода), только дописывается. Курсор истории — колонки hist_* в deal_chat_state.
--   deal_chat_holds — брони бюджета: прогон перед вызовом модели бронирует его потолок
--     (deal_chat_take_budget, под замком); строка расхода ссылается на бронь. Предел статьи
--     держит база: «сутки» — 5 долларов на сутки по Москве, «история» — 20 долларов за всё
--     время. Неоплаченная бронь (процесс упал после брони) считается по потолку всегда:
--     бюджет ошибается вверх.
--   deal_chat_money_latest, deal_chat_contract_latest, deal_chat_sourcing_recent,
--   deal_chat_important_latest — последние факты сделки (вкладка «Реализация» через
--     scripts/realization_ctx.py). Новый вызов с пустым «главным» гасит прежние пункты.
--   Гейт снятых фактов суточного прохода помечает ТОЛЬКО факты прогона (оценки хода годны,
--   курсоры стоят); гейт брака и любой гейт истории — откат целиком. База отвергла факт
--   (проверка или триггер) — прогон пишет ту же оценку без фактов, курсор идёт.
--   Откат прогона вручную — тоже пометкой:
--       update deal_chat_facts set voided_at = now() where run_id = '<ключ>' and voided_at is null;
--       update deal_chat_hist  set voided_at = now() where run_id = '<ключ>' and voided_at is null;
--       update deal_chat_state set hist_state = null, hist_low_id = null where hist_run_id = '<ключ>';
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
  method_ver    text        not null,
  fail_streak   integer     not null default 0    -- вызовов подряд без годной оценки; 0 — после записанной
);
-- create table if not exists таблицу не меняет (правило 21): колонка, добавленная
-- после первой редакции файла, — отдельным alter, до ограничений ниже
alter table deal_chat_state add column if not exists fail_streak integer not null default 0;
-- последнее УДАЧНОЕ чтение чата (23.10.2026): checked_at сдвигает и неудача (очередь),
-- а вкладке «Реализация» (scripts/realization_ctx.py) нужно знать, когда чат правда читали
alter table deal_chat_state add column if not exists read_at timestamptz;

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

create table if not exists deal_chat_spend (
  id            bigserial   primary key,
  at            timestamptz not null default now(),
  run_id        text        not null,
  part          smallint    not null,             -- часть прогона (правило дробления 22.09.2026)
  deal_id       bigint      not null,
  model         text        not null,             -- ответившая модель, а без ответа — запрошенная
  outcome       text        not null,             -- закрытый список ниже
  defect        text,                             -- класс брака — только у outcome «брак»
  api_status    smallint,                         -- код ответа API при сбое
  tokens_in     integer     not null default 0,
  tokens_out    integer     not null default 0,
  usd           numeric(12, 6) not null default 0,
  estimated     boolean     not null default false  -- расход неизвестен: записан потолок вызова
);

do $$
begin
  alter table deal_chat_state drop constraint if exists deal_chat_state_ids_check;
  alter table deal_chat_state add constraint deal_chat_state_ids_check
    check (deal_id > 0 and chat_id > 0 and last_msg_id >= 0 and fail_streak >= 0);
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

  -- расход: тоже ни одного поля для текста; исход и брак — из закрытых списков,
  -- совпадающих с ИСХОДЫ и КЛАССЫ_БРАКА в scripts/chat_context.py (сверяет тест)
  alter table deal_chat_spend drop constraint if exists deal_chat_spend_ids_check;
  alter table deal_chat_spend add constraint deal_chat_spend_ids_check
    check (deal_id > 0 and part between 1 and 50 and (api_status is null or api_status between 100 and 599));
  alter table deal_chat_spend drop constraint if exists deal_chat_spend_keys_check;
  alter table deal_chat_spend add constraint deal_chat_spend_keys_check
    check (run_id ~ '^[A-Za-z0-9._-]{1,80}$' and model ~ '^[a-z0-9.-]{1,80}$');
  alter table deal_chat_spend drop constraint if exists deal_chat_spend_outcome_check;
  alter table deal_chat_spend add constraint deal_chat_spend_outcome_check
    check (outcome = any (array['записано','брак','отказ','сбой_api','сбой_записи'])
           and (outcome = 'брак') = (defect is not null)
           and (defect is null or defect = any (array['не JSON','обрезан','не объект','ключи','state','direction','risk','ball_role','reply_expected','risk_kinds','blocker','due','evidence','confidence','verdict','money','contract','sourcing','important'])));
  alter table deal_chat_spend drop constraint if exists deal_chat_spend_spend_check;
  alter table deal_chat_spend add constraint deal_chat_spend_spend_check
    check (tokens_in >= 0 and tokens_out >= 0 and usd >= 0);
end $$;

create index if not exists deal_chat_ctx_deal
  on deal_chat_ctx (deal_id, assessed_at desc, id desc) where voided_at is null;
create index if not exists deal_chat_ctx_run on deal_chat_ctx (run_id);
create index if not exists deal_chat_state_checked on deal_chat_state (checked_at);
create index if not exists deal_chat_spend_at on deal_chat_spend (at);
create index if not exists deal_chat_spend_run on deal_chat_spend (run_id);

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

-- Расход — только дописывается, без пометок: суточный остаток бюджета
-- считается суммой, и правка задним числом открыла бы деньги сверх предела.
create or replace function deal_chat_spend_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'deal_chat_spend только дописывается: потраченное не правится и не удаляется';
end $$;

drop trigger if exists deal_chat_spend_append_only on deal_chat_spend;
create trigger deal_chat_spend_append_only before update or delete on deal_chat_spend
  for each row execute function deal_chat_spend_append_only();
drop trigger if exists deal_chat_spend_no_truncate on deal_chat_spend;
create trigger deal_chat_spend_no_truncate before truncate on deal_chat_spend
  for each statement execute function deal_chat_spend_append_only();

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

-- ══ V3 (25.10.2026): факты из чата, разовое чтение истории, брони бюджета ══════════════

-- Строка «главного»: до 160 знаков, без ссылок и доменов (bit.ly/… — тоже), почт и скобок, без
-- валют, множителей сумм («500 т.р.», «500к»), сумм словами («миллион», «тысяч»), телефонов
-- («345-67-89», десять цифр после снятия пробелов, скобок, дефисов и плюса) и чисел из четырёх
-- и более цифр (даты ДД.ММ[.ГГГГ] и ГГГГ-ММ-ДД — можно). Те же образцы —
-- в scripts/chat_context.текст_главного_ок (ОБРАЗЕЦ_*; согласие сверяет тест). Латиница —
-- после lower(), кириллица — обоими регистрами явно: регистр кириллицы зависит от локали.
create or replace function deal_chat_statement_ok(s text)
returns boolean language plpgsql immutable as $$
declare
  н text;
begin
  if s is null or btrim(s) = '' or char_length(s) > 160 then
    return false;
  end if;
  н := lower(s);
  if н ~ 'https?:|www[.]|@|\[|\]|[a-z0-9А-Яа-яЁё-]+[.](ru|su|com|net|org|me|io|cn|de|by|kz|ua|uz|pro|biz|info|ai|app|eu|uk|ae|in|tr|ly|ms|cc|co|us|online|site|tech|shop|xyz|it|fr|es|nl|pl|ch|jp|kr|ge|рф|РФ)([^a-z0-9]|$)|[a-z0-9-]+([.][a-z0-9-]+)*[.][a-z]{2,}/' then
    return false;
  end if;
  -- сумма словами: «полтора миллиона», «млн» без цифры рядом (ревью 25.10.2026)
  -- и «двести тысяч» (ревью 28.10.2026)
  if н ~ '(^|[^А-Яа-яЁё])([Мм][Лл][Нн]|[Мм][Лл][Рр][Дд])([^А-Яа-яЁё]|$)|[Мм][Ии][Лл][Лл][Ии][Оо][Нн]|[Мм][Ии][Лл][Лл][Ии][Аа][Рр][Дд]|[Тт][Ыы][Сс][Яя][Чч]' then
    return false;
  end if;
  if н ~ '₽|€|[$]|£|¥|(^|[^a-z])(eur|usd|cny|aed|inr|gbp|jpy|rub|rur)([^a-z]|$)|(^|[^А-Яа-яЁё])([Рр][Уу][Бб]([.Лл]|[^А-Яа-яЁё]|$)|[Ее][Вв][Рр][Оо]([^А-Яа-яЁё]|$)|[Дд][Оо][Лл][Лл][Аа][Рр]|[Юю][Аа][Нн]|[Дд][Ии][Рр][Хх][Аа][Мм]|[Рр][Уу][Пп][Ии])' then
    return false;
  end if;
  -- «т.р.», «тр», «500к» (кириллица вплотную к цифре) — тоже множители (ревью 28.10.2026)
  if н ~ '[0-9][  ]*([Тт][Ыы][Сс]|[Мм][Лл][Нн]|[Мм][Лл][Рр][Дд]|[Мм][Ии][Лл][Лл][Ии]|[Тт][.]?[  ]?[Рр]([.]|[^А-Яа-яЁё]|$)|k([^a-z]|$))|[0-9][Кк]([^А-Яа-яЁё]|$)' then
    return false;
  end if;
  -- местный номер «345-67-89» (ревью 28.10.2026)
  if н ~ '(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{2}(?![0-9])' then
    return false;
  end if;
  н := regexp_replace(н, '(?<![0-9.])[0-9]{1,2}[.][0-9]{1,2}([.]([0-9]{4}|[0-9]{2}))?(?![0-9])|(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])', ' ', 'g');
  н := regexp_replace(н, '(?<=[0-9])[ .,''   ]+(?=[0-9])', '', 'g');
  -- телефон: десять цифр подряд после снятия пробелов, скобок, дефисов и плюса
  if translate(н, E' \t\r\n   ()[]-+', '') ~ '[0-9]{10,}' then
    return false;
  end if;
  return н !~ '[0-9]{4,}';
end $$;

-- Цитата факта: до 200 знаков, без ссылок и доменов, почт и упоминаний; без десяти цифр подряд
-- после снятия пробелов, скобок и дефисов (телефон). Как scripts/chat_context.цитата_ок.
create or replace function deal_chat_quote_ok(q text)
returns boolean language sql immutable as $$
  select q is null
      or (char_length(q) between 1 and 200
          and lower(q) !~ 'http|www[.]|@|\[user=|[a-z0-9А-Яа-яЁё-]+[.](ru|su|com|net|org|me|io|cn|de|by|kz|ua|uz|pro|biz|info|ai|app|eu|uk|ae|in|tr|ly|ms|cc|co|us|online|site|tech|shop|xyz|it|fr|es|nl|pl|ch|jp|kr|ge|рф|РФ)([^a-z0-9]|$)|[a-z0-9-]+([.][a-z0-9-]+)*[.][a-z]{2,}/'
          and translate(q, E' \t\r\n   ()[]-', '') !~ '[0-9]{10,}')
$$;

-- Номер спецификации: до 20 знаков без пробелов, с цифрой, без десяти цифр подряд.
create or replace function deal_chat_spec_ok(s text)
returns boolean language sql immutable as $$
  select s is null or (s ~ '^[0-9A-Za-zА-ЯЁа-яё№/._-]{1,20}$' and s ~ '[0-9]' and s !~ '[0-9]{10,}')
$$;

-- Факт: закрытые списки — дословно как кортежи scripts/chat_context.py (сверяет тест).
-- Деньги и сорсинг — одно сообщение (msg_id и есть доказательство); договор — 1–5
-- сообщений, msg_id — последнее из них; «главное» — сообщения и (или) ключи сводки, хотя бы
-- одно сообщение или строка учёта (не одни prev, facts, imp). Ключи сводки — только у «главного».
create or replace function deal_chat_fact_ok(p_kind text, d jsonb, p_msg bigint, p_ids bigint[], p_keys text[])
returns boolean language plpgsql immutable as $$
declare
  виды_факта   constant text[] := array['деньги','договор','сорсинг','главное'];
  виды_денег   constant text[] := array['наше_кп','переторжка','итог','счёт','оплата'];
  стороны_денег constant text[] := array['продажа','закупка','неясно'];
  ндс          constant text[] := array['с_ндс','без_ндс','не_указано'];
  валюты       constant text[] := array['RUB','EUR','USD','CNY','AED','INR','GBP','JPY','неясно'];
  статусы      constant text[] := array['подписан','отправлен','у_клиента','на_согласовании','правки','неясно'];
  стороны_дог  constant text[] := array['заказчик','поставщик','неясно'];
  где          constant text[] := array['портал','эдо','почта','бумага','у_клиента','у_нас','у_поставщика','неясно'];
  сигналы      constant text[] := array['запрос_отправлен','ответил','кп_получено','отказ','нет_ответа','запрошено_повторно'];
  роли_автора  constant text[] := array['кам','сорсер','руководитель_сорсинга','сопровождение','ответственный','сотрудник','служебная_запись','система'];
  виды_главного constant text[] := array['срыв_срока_клиента','деньги_оплата','расхождение_суммы','договор_условия','поставщик','клиент_недоволен','решение_руководителя','документы_упд','иное'];
  важности     constant text[] := array['высокая','средняя'];
  ключи_сводки constant text[] := array['stage','dl','o1','o2','o3','o4','o5','o6','o7','o8','deal','contract','econ','presale','prev','facts','imp'];
  -- прошлые выводы самой модели: одни они «главное» не доказывают (ревью 25.10.2026)
  ключи_прошлого constant text[] := array['prev','facts','imp'];
  ids  bigint[] := coalesce(p_ids, '{}');
  keys text[]   := coalesce(p_keys, '{}');
  n_ids int := coalesce(cardinality(p_ids), 0);
  n_keys int := coalesce(cardinality(p_keys), 0);
  дата date;
begin
  if p_kind is null or not (p_kind = any (виды_факта)) or d is null or jsonb_typeof(d) <> 'object' then
    return false;
  end if;
  if n_ids > 5 or n_keys > 5 or array_position(ids, null) is not null or array_position(keys, null) is not null then
    return false;
  end if;
  if (select count(distinct x) from unnest(ids) x) <> n_ids or (select count(distinct x) from unnest(keys) x) <> n_keys
     or exists (select 1 from unnest(ids) x where x <= 0)
     or exists (select 1 from unnest(keys) x where not (x = any (ключи_сводки))) then
    return false;
  end if;

  if p_kind = 'главное' then
    return (d ?& array['kind', 'severity']) and (d - array['kind', 'severity']) = '{}'::jsonb
       and jsonb_typeof(d->'kind') = 'string' and (d->>'kind') = any (виды_главного)
       and jsonb_typeof(d->'severity') = 'string' and (d->>'severity') = any (важности)
       and (n_ids >= 1 or exists (select 1 from unnest(keys) k where not (k = any (ключи_прошлого))))
       and (p_msg is null or p_msg = any (ids));
  end if;
  -- деньги, договор, сорсинг — только сообщения
  if n_keys > 0 or p_msg is null or p_msg <= 0 or not (p_msg = any (ids)) then
    return false;
  end if;

  if p_kind = 'деньги' then
    if not (d ?& array['amount', 'currency', 'money_kind', 'side', 'vat', 'date'])
       or (d - array['amount', 'currency', 'money_kind', 'side', 'vat', 'date']) <> '{}'::jsonb
       or ids <> array[p_msg] then
      return false;
    end if;
    if jsonb_typeof(d->'amount') <> 'number' or (d->>'amount')::numeric <= 0
       or (d->>'amount')::numeric >= 10000000000000 then
      return false;
    end if;
    if jsonb_typeof(d->'currency') <> 'string' or not ((d->>'currency') = any (валюты))
       or jsonb_typeof(d->'money_kind') <> 'string' or not ((d->>'money_kind') = any (виды_денег))
       or jsonb_typeof(d->'side') <> 'string' or not ((d->>'side') = any (стороны_денег))
       or jsonb_typeof(d->'vat') <> 'string' or not ((d->>'vat') = any (ндс)) then
      return false;
    end if;
    if jsonb_typeof(d->'date') = 'string' then
      if (d->>'date') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
        return false;
      end if;
      begin
        дата := (d->>'date')::date;
      exception when others then
        return false;
      end;
      return to_char(дата, 'YYYY-MM-DD') = d->>'date';
    end if;
    return jsonb_typeof(d->'date') = 'null';
  end if;

  if p_kind = 'договор' then
    return (d ?& array['status', 'side', 'where', 'spec_no'])
       and (d - array['status', 'side', 'where', 'spec_no']) = '{}'::jsonb
       and n_ids >= 1 and p_msg = (select max(x) from unnest(ids) x)
       and jsonb_typeof(d->'status') = 'string' and (d->>'status') = any (статусы)
       and jsonb_typeof(d->'side') = 'string' and (d->>'side') = any (стороны_дог)
       and jsonb_typeof(d->'where') = 'string' and (d->>'where') = any (где)
       and (jsonb_typeof(d->'spec_no') = 'null'
            or (jsonb_typeof(d->'spec_no') = 'string' and deal_chat_spec_ok(d->>'spec_no')));
  end if;

  -- сорсинг: сигнал и роль автора (роль ставит код по автору сообщения, не модель)
  return (d ?& array['signal', 'author_role']) and (d - array['signal', 'author_role']) = '{}'::jsonb
     and ids = array[p_msg]
     and jsonb_typeof(d->'signal') = 'string' and (d->>'signal') = any (сигналы)
     and jsonb_typeof(d->'author_role') = 'string' and (d->>'author_role') = any (роли_автора);
end $$;

-- Дата суммы — не позже дня сообщения по Москве (UTC+3): обещанная оплата — не пришедшая
-- (ревью 25.10.2026). Как scripts/chat_context.дата_денег_ок. Не деньги или даты нет — годно.
create or replace function deal_chat_money_date_ok(p_kind text, d jsonb, p_at timestamptz)
returns boolean language plpgsql immutable as $$
declare
  дата date;
begin
  if p_kind is distinct from 'деньги' or d is null or jsonb_typeof(d->'date') is distinct from 'string' then
    return true;
  end if;
  begin
    дата := (d->>'date')::date;
  exception when others then
    return false;
  end;
  return p_at is not null and дата <= ((p_at at time zone 'UTC') + interval '3 hours')::date;
end $$;

-- Значения строк сводки, на которые сослалось «главное» (правило 16 «храни, почему»):
-- объект «ключ → строка до 300 знаков», ключи — из доказательств.
create or replace function deal_chat_vals_ok(v jsonb, p_keys text[])
returns boolean language sql immutable as $$
  select v is null
      or (jsonb_typeof(v) = 'object'
          and not exists (select 1 from jsonb_each(v) e
                           where not (e.key = any (coalesce(p_keys, '{}')))
                              or jsonb_typeof(e.value) <> 'string' or char_length(e.value #>> '{}') > 300))
$$;

-- Курсор разового чтения истории — в той же строке, что суточный курсор: история не
-- трогает ни суточный курсор, ни очередь (checked_at).
alter table deal_chat_state add column if not exists hist_low_id      bigint;       -- самое старое прочитанное
alter table deal_chat_state add column if not exists hist_state       text;         -- конец_чата | предел; null — не читали
alter table deal_chat_state add column if not exists hist_at          timestamptz;  -- последняя попытка
alter table deal_chat_state add column if not exists hist_run_id      text;
alter table deal_chat_state add column if not exists hist_fail_streak integer not null default 0;

create table if not exists deal_chat_holds (
  id          bigserial      primary key,
  at          timestamptz    not null default now(),
  run_id      text           not null,
  part        smallint       not null,
  deal_id     bigint         not null,
  kind        text           not null,           -- статья: сутки | история
  ceiling_usd numeric(12, 6) not null            -- потолок вызова: столько засчитано до оплаты
);

-- Вызов chat-ctx-2 (ревью 25.10.2026): сколько пунктов «главного» модель вернула до проверок
-- (меньше записанных — «пункты сняты проверкой», а не «важного нет») и была ли у модели сводка
-- сборки (сроки, заказы, договоры, экономика) или только стадия и прошлые выводы
alter table deal_chat_ctx add column if not exists imp_proposed smallint;
alter table deal_chat_ctx add column if not exists with_digest  boolean;

-- статья расхода и бронь вызова: прежние строки — суточные и без брони
alter table deal_chat_spend add column if not exists kind    text not null default 'сутки';
alter table deal_chat_spend add column if not exists hold_id bigint;

create table if not exists deal_chat_hist (
  id            bigserial   primary key,
  deal_id       bigint      not null,
  assessed_at   timestamptz not null default now(),
  model         text        not null,
  prompt_sha256 text        not null,             -- отпечаток запроса; сам запрос не хранится
  method_ver    text        not null,
  msg_from_id   bigint      not null,             -- отрезок отправленных модели сообщений
  msg_to_id     bigint      not null,
  msgs          integer     not null,             -- сообщений отправлено
  pages         smallint    not null,             -- страниц по 50 прочитано
  reached       text        not null,             -- конец_чата | предел
  digest_sha256 text,                             -- отпечаток строк сводки запроса
  imp_proposed  smallint,                         -- пунктов «главного» в ответе до проверок
  with_digest   boolean,                          -- была ли сводка сборки (не только стадия)
  tokens_in     integer     not null default 0,
  tokens_out    integer     not null default 0,
  usd           numeric(12, 6) not null default 0,
  run_id        text        not null,
  voided_at     timestamptz
);

-- таблица истории могла появиться до этих колонок (правило 21: create … if not exists её не меняет)
alter table deal_chat_hist add column if not exists imp_proposed smallint;
alter table deal_chat_hist add column if not exists with_digest  boolean;

create table if not exists deal_chat_facts (
  id               bigserial   primary key,
  deal_id          bigint      not null,
  run_id           text        not null,
  method_ver       text        not null,
  pass             text        not null,          -- сутки | история
  -- строка оценки или истории; связь держит триггер deal_chat_facts_range, а не внешний
  -- ключ: с ним truncate оценок отвечал бы «нельзя из-за ключа», а не запретом триггера
  ctx_id           bigint,
  hist_id          bigint,
  kind             text        not null,          -- деньги | договор | сорсинг | главное
  msg_id           bigint,                        -- сообщение факта; у «главного» — последнее из доказательств
  msg_at           timestamptz,                   -- когда оно написано (по порталу)
  evidence_msg_ids bigint[]    not null default '{}',
  evidence_keys    text[]      not null default '{}',
  evidence_vals    jsonb,                         -- строки сводки по ключам (правило 16)
  data             jsonb       not null,          -- значения закрытых списков и сумма
  quote            text,                          -- до 200 знаков, без контактов и имён
  statement        text,                          -- только у «главного»: строка оценки модели
  confidence       numeric,                       -- только у «главного»: уверенность в списке
  fact_key         text        generated always as
                     (md5(kind || ':' || coalesce(msg_id::text, '') || ':' || data::text)) stored,
  created_at       timestamptz not null default now(),
  voided_at        timestamptz
);

do $$
begin
  alter table deal_chat_state drop constraint if exists deal_chat_state_hist_check;
  alter table deal_chat_state add constraint deal_chat_state_hist_check
    check ((hist_state is null or hist_state = any (array['конец_чата','предел']))
           and hist_fail_streak >= 0 and (hist_low_id is null or hist_low_id > 0)
           and (hist_run_id is null or hist_run_id ~ '^[A-Za-z0-9._-]{1,80}$'));

  alter table deal_chat_holds drop constraint if exists deal_chat_holds_check;
  alter table deal_chat_holds add constraint deal_chat_holds_check
    check (deal_id > 0 and part between 1 and 50 and ceiling_usd > 0 and kind = any (array['сутки','история'])
           and run_id ~ '^[A-Za-z0-9._-]{1,80}$');

  alter table deal_chat_spend drop constraint if exists deal_chat_spend_kind_check;
  alter table deal_chat_spend add constraint deal_chat_spend_kind_check check (kind = any (array['сутки','история']));
  alter table deal_chat_spend drop constraint if exists deal_chat_spend_hold_fk;
  alter table deal_chat_spend add constraint deal_chat_spend_hold_fk foreign key (hold_id) references deal_chat_holds (id);

  alter table deal_chat_ctx drop constraint if exists deal_chat_ctx_imp_check;
  alter table deal_chat_ctx add constraint deal_chat_ctx_imp_check
    check (imp_proposed is null or imp_proposed between 0 and 50);
  alter table deal_chat_hist drop constraint if exists deal_chat_hist_imp_check;
  alter table deal_chat_hist add constraint deal_chat_hist_imp_check
    check (imp_proposed is null or imp_proposed between 0 and 50);

  alter table deal_chat_hist drop constraint if exists deal_chat_hist_check;
  alter table deal_chat_hist add constraint deal_chat_hist_check
    check (deal_id > 0 and msg_from_id > 0 and msg_from_id <= msg_to_id and msgs >= 1 and pages between 1 and 6
           and reached = any (array['конец_чата','предел'])
           and run_id ~ '^[A-Za-z0-9._-]{1,80}$' and method_ver ~ '^[a-z0-9.-]{1,40}$'
           and model ~ '^[a-z0-9.-]{1,80}$' and prompt_sha256 ~ '^[0-9a-f]{64}$'
           and (digest_sha256 is null or digest_sha256 ~ '^[0-9a-f]{64}$')
           and tokens_in >= 0 and tokens_out >= 0 and usd >= 0);

  alter table deal_chat_facts drop constraint if exists deal_chat_facts_src_check;
  alter table deal_chat_facts add constraint deal_chat_facts_src_check
    check ((ctx_id is null) <> (hist_id is null)
           and pass = case when ctx_id is not null then 'сутки' else 'история' end);
  alter table deal_chat_facts drop constraint if exists deal_chat_facts_keys_check;
  alter table deal_chat_facts add constraint deal_chat_facts_keys_check
    check (deal_id > 0 and run_id ~ '^[A-Za-z0-9._-]{1,80}$' and method_ver ~ '^[a-z0-9.-]{1,40}$');
  alter table deal_chat_facts drop constraint if exists deal_chat_facts_fact_check;
  alter table deal_chat_facts add constraint deal_chat_facts_fact_check
    check (deal_chat_fact_ok(kind, data, msg_id, evidence_msg_ids, evidence_keys)
           and deal_chat_vals_ok(evidence_vals, evidence_keys)
           and deal_chat_money_date_ok(kind, data, msg_at)
           and (evidence_vals is null or kind = 'главное'));
  alter table deal_chat_facts drop constraint if exists deal_chat_facts_text_check;
  alter table deal_chat_facts add constraint deal_chat_facts_text_check
    check (deal_chat_quote_ok(quote)
           and (kind = 'главное') = (statement is not null)
           and (statement is null or deal_chat_statement_ok(statement))
           and (kind = 'главное') = (confidence is not null)
           and (confidence is null or confidence between 0 and 1));
end $$;

create unique index if not exists deal_chat_spend_hold on deal_chat_spend (hold_id) where hold_id is not null;
create index if not exists deal_chat_spend_kind_at on deal_chat_spend (kind, at);
create index if not exists deal_chat_holds_kind_at on deal_chat_holds (kind, at);
create index if not exists deal_chat_hist_deal on deal_chat_hist (deal_id, assessed_at desc, id desc) where voided_at is null;
create index if not exists deal_chat_hist_run on deal_chat_hist (run_id);
create index if not exists deal_chat_facts_deal on deal_chat_facts (deal_id, kind, msg_at desc) where voided_at is null;
create index if not exists deal_chat_facts_run on deal_chat_facts (run_id);
create index if not exists deal_chat_facts_ctx on deal_chat_facts (ctx_id) where ctx_id is not null;
create index if not exists deal_chat_facts_hist on deal_chat_facts (hist_id) where hist_id is not null;
-- повтор того же факта (вид, сообщение, данные) не пишется: история и сутки его не задвоят
create unique index if not exists deal_chat_facts_once
  on deal_chat_facts (deal_id, kind, msg_id, fact_key) where voided_at is null and kind <> 'главное';

-- Доказательства факта — внутри отрезка своей строки оценки или истории, та же сделка и
-- тот же прогон (третья проверка после Python и deal_chat_fact_ok).
create or replace function deal_chat_facts_range() returns trigger
language plpgsql as $$
declare
  сделка bigint; прогон text; lo bigint; hi bigint;
begin
  if (new.ctx_id is null) = (new.hist_id is null) then
    return new;                                   -- ни одного или оба источника — отвергнет ограничение строки
  end if;
  if new.ctx_id is not null then
    select deal_id, run_id, msg_from_id, msg_to_id into сделка, прогон, lo, hi from deal_chat_ctx where id = new.ctx_id;
  else
    select deal_id, run_id, msg_from_id, msg_to_id into сделка, прогон, lo, hi from deal_chat_hist where id = new.hist_id;
  end if;
  if сделка is distinct from new.deal_id or прогон is distinct from new.run_id then
    raise exception 'deal_chat_facts: факт не своей строки оценки (её нет, сделка или прогон другие)';
  end if;
  if exists (select 1 from unnest(new.evidence_msg_ids) x where x < lo or x > hi) then
    raise exception 'deal_chat_facts: доказательство вне отрезка своей строки';
  end if;
  return new;
end $$;

drop trigger if exists deal_chat_facts_range on deal_chat_facts;
create trigger deal_chat_facts_range before insert on deal_chat_facts
  for each row execute function deal_chat_facts_range();

-- Только дописываются: факты и строки истории меняют лишь пометку отката, и один раз;
-- брони не меняются и не удаляются (сумма брони — часть остатка бюджета).
create or replace function deal_chat_mark_only() returns trigger
language plpgsql as $$
begin
  if tg_op in ('DELETE', 'TRUNCATE') then
    raise exception '% только дописывается: удалять нельзя, откат — пометкой voided_at', tg_table_name;
  end if;
  -- fact_key — вычисляемая колонка: в BEFORE-триггере у NEW она ещё пустая
  if old.voided_at is not null
     or (to_jsonb(new) - 'voided_at' - 'fact_key') is distinct from (to_jsonb(old) - 'voided_at' - 'fact_key') then
    raise exception '% только дописывается: меняется лишь пометка отката voided_at, и один раз', tg_table_name;
  end if;
  return new;
end $$;

create or replace function deal_chat_holds_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'deal_chat_holds только дописывается: бронь не правится и не удаляется';
end $$;

drop trigger if exists deal_chat_facts_mark_only on deal_chat_facts;
create trigger deal_chat_facts_mark_only before update or delete on deal_chat_facts
  for each row execute function deal_chat_mark_only();
drop trigger if exists deal_chat_facts_no_truncate on deal_chat_facts;
create trigger deal_chat_facts_no_truncate before truncate on deal_chat_facts
  for each statement execute function deal_chat_mark_only();
drop trigger if exists deal_chat_hist_mark_only on deal_chat_hist;
create trigger deal_chat_hist_mark_only before update or delete on deal_chat_hist
  for each row execute function deal_chat_mark_only();
drop trigger if exists deal_chat_hist_no_truncate on deal_chat_hist;
create trigger deal_chat_hist_no_truncate before truncate on deal_chat_hist
  for each statement execute function deal_chat_mark_only();
drop trigger if exists deal_chat_holds_append_only on deal_chat_holds;
create trigger deal_chat_holds_append_only before update or delete on deal_chat_holds
  for each row execute function deal_chat_holds_append_only();
drop trigger if exists deal_chat_holds_no_truncate on deal_chat_holds;
create trigger deal_chat_holds_no_truncate before truncate on deal_chat_holds
  for each statement execute function deal_chat_holds_append_only();

-- Бронь бюджета вызова модели: под замком транзакции считает расход статьи в окне,
-- потолки неоплаченных брон в окне и новый потолок; помещается в предел — бронь (её
-- номер), нет — null. Окно «сутки» — с полуночи по Москве (UTC+3, без перехода на летнее
-- время с 2014 г.), «история» — за всё время. Пределы — решения владельца (19.10.2026 и
-- 25.10.2026); больший вход их не поднимет. Как econ_file_take_slot.
create or replace function deal_chat_take_budget(p_run_id text, p_part integer, p_deal_id bigint, p_kind text,
                                                 p_ceiling numeric)
returns bigint language plpgsql as $$
declare
  предел numeric := case p_kind when 'сутки' then 5 when 'история' then 20 end;
  начало timestamptz := case p_kind
    when 'сутки' then (date_trunc('day', (now() at time zone 'UTC') + interval '3 hours') - interval '3 hours')
                      at time zone 'UTC'
    else '-infinity'::timestamptz end;
  потрачено numeric;
  брони     numeric;
  номер     bigint;
begin
  if предел is null then
    raise exception 'deal_chat_take_budget: статья вне списка (сутки, история)';
  end if;
  if p_ceiling is null or p_ceiling <= 0 then
    raise exception 'deal_chat_take_budget: потолок вызова должен быть больше нуля';
  end if;
  perform pg_advisory_xact_lock(hashtext('deal_chat_take_budget'));
  -- метка расхода — время его брони (ревью 25.10.2026): вызов, забронированный после полуночи,
  -- а записанный с меткой раннера до неё, считается в те же сутки, что бронь
  select coalesce(sum(s.usd), 0) into потрачено
    from deal_chat_spend s left join deal_chat_holds h on h.id = s.hold_id
   where s.kind = p_kind and coalesce(h.at, s.at) >= начало;
  select coalesce(sum(h.ceiling_usd), 0) into брони
    from deal_chat_holds h
   where h.kind = p_kind and h.at >= начало
     and not exists (select 1 from deal_chat_spend s where s.hold_id = h.id);
  if потрачено + брони + p_ceiling > предел then
    return null;
  end if;
  insert into deal_chat_holds (run_id, part, deal_id, kind, ceiling_usd)
  values (p_run_id, p_part, p_deal_id, p_kind, p_ceiling) returning id into номер;
  return номер;
end $$;

-- Последние факты сделки. Снос перед созданием: «create or replace view» не меняет колонки.
drop view if exists deal_chat_money_latest;
create view deal_chat_money_latest with (security_invoker = true) as
select distinct on (f.deal_id, f.data->>'money_kind')
       f.deal_id, f.data->>'money_kind' as money_kind, (f.data->>'amount')::numeric as amount,
       f.data->>'currency' as currency, f.data->>'vat' as vat, (f.data->>'date')::date as named_date,
       f.msg_id, f.msg_at, f.pass, f.quote, f.run_id
  from deal_chat_facts f
 where f.voided_at is null and f.kind = 'деньги' and f.data->>'side' = 'продажа'
 order by f.deal_id, f.data->>'money_kind', f.msg_at desc nulls last, f.id desc;

drop view if exists deal_chat_contract_latest;
create view deal_chat_contract_latest with (security_invoker = true) as
select distinct on (f.deal_id, f.data->>'side')
       f.deal_id, f.data->>'side' as side, f.data->>'status' as status, f.data->>'where' as place,
       f.data->>'spec_no' as spec_no, f.msg_id, f.msg_at, f.pass, f.quote, f.run_id
  from deal_chat_facts f
 where f.voided_at is null and f.kind = 'договор'
 order by f.deal_id, f.data->>'side', f.msg_at desc nulls last, f.id desc;

drop view if exists deal_chat_sourcing_recent;
create view deal_chat_sourcing_recent with (security_invoker = true) as
select deal_id, signal, author_role, msg_id, msg_at, pass, quote, run_id
  from (select f.deal_id, f.data->>'signal' as signal, f.data->>'author_role' as author_role, f.msg_id, f.msg_at,
               f.pass, f.quote, f.run_id,
               row_number() over (partition by f.deal_id order by f.msg_at desc nulls last, f.id desc) as n
          from deal_chat_facts f
         where f.voided_at is null and f.kind = 'сорсинг') x
 where n <= 5;

-- «Главное» — пункты ПОСЛЕДНЕГО вызова сделки: последней оценки chat-ctx-2, а если суточных
-- оценок chat-ctx-2 нет — последнего разового чтения истории (ревью 25.10.2026: история
-- читает старые сообщения и свежее «главное» не гасит). Новый вызов с пустым списком гасит
-- прежние пункты — строка вызова без пунктов (kind пуст). Помеченные откатом вызовы и факты не
-- видны. proposed — сколько пунктов вернула модель до проверок (больше записанных — «сняты
-- проверкой»); with_digest — была ли сводка сборки; ev_at — дата доказательства пункта (его
-- последнего сообщения, а без сообщений — самой оценки): свежесть — по ней, не по дате вызова.
drop view if exists deal_chat_important_latest;
create view deal_chat_important_latest with (security_invoker = true) as
with вызовы as (
  select c.deal_id, c.assessed_at, c.id as ctx_id, null::bigint as hist_id, 'сутки'::text as pass,
         c.imp_proposed as proposed, c.with_digest
    from deal_chat_ctx c
   where c.voided_at is null and c.method_ver <> 'chat-ctx-1'
  union all
  select h.deal_id, h.assessed_at, null::bigint, h.id, 'история'::text, h.imp_proposed, h.with_digest
    from deal_chat_hist h
   where h.voided_at is null
), последний as (
  select distinct on (deal_id) deal_id, assessed_at, ctx_id, hist_id, pass, proposed, with_digest
    from вызовы
   order by deal_id, (pass = 'сутки') desc, assessed_at desc, coalesce(ctx_id, hist_id) desc
)
select l.deal_id, l.assessed_at, l.pass, f.id as fact_id, f.data->>'kind' as kind, f.data->>'severity' as severity,
       f.statement, f.confidence, f.evidence_keys, f.evidence_msg_ids, f.msg_at, f.run_id,
       l.proposed, l.with_digest, coalesce(f.msg_at, l.assessed_at) as ev_at
  from последний l
  left join deal_chat_facts f
    on f.voided_at is null and f.kind = 'главное'
   and ((l.ctx_id is not null and f.ctx_id = l.ctx_id) or (l.hist_id is not null and f.hist_id = l.hist_id));



create or replace function deal_chat_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

-- Доступ: RLS плюс FORCE, ничего у PUBLIC, anon и authenticated; сервисному
-- ключу — чтение и запись курсора, у оценок, фактов и истории — чтение, вставка и
-- пометка отката, у расхода и брон — чтение и вставка, виды — чтение, бронь бюджета —
-- вызов функции.
do $$
declare
  сх        text := current_schema();
  кому      text := deal_chat_роли_которые_есть(array['anon', 'authenticated']);
  служебная text := deal_chat_роли_которые_есть(array['service_role']);
  n         text;
begin
  foreach n in array array['deal_chat_state', 'deal_chat_ctx', 'deal_chat_spend', 'deal_chat_holds',
                           'deal_chat_hist', 'deal_chat_facts'] loop
    execute format('comment on table %I.%I is %L', сх, n, 'deal_chat_schema:v3');
    execute format('alter table %I.%I enable row level security', сх, n);
    execute format('alter table %I.%I force  row level security', сх, n);
    execute format('revoke all on table %I.%I from public', сх, n);
    if кому is not null then
      execute format('revoke all on table %I.%I from %s', сх, n, кому);
    end if;
  end loop;
  foreach n in array array['deal_chat_ctx_id_seq', 'deal_chat_spend_id_seq', 'deal_chat_holds_id_seq',
                           'deal_chat_hist_id_seq', 'deal_chat_facts_id_seq'] loop
    execute format('revoke all on sequence %I.%I from public', сх, n);
    if кому is not null then
      execute format('revoke all on sequence %I.%I from %s', сх, n, кому);
    end if;
  end loop;
  foreach n in array array['deal_chat_ctx_latest', 'deal_chat_money_latest', 'deal_chat_contract_latest',
                           'deal_chat_sourcing_recent', 'deal_chat_important_latest'] loop
    execute format('revoke all on %I.%I from public', сх, n);
    if кому is not null then
      execute format('revoke all on %I.%I from %s', сх, n, кому);
    end if;
  end loop;
  execute format('revoke all on function %I.deal_chat_take_budget(text, integer, bigint, text, numeric) from public',
                 сх);
  if кому is not null then
    execute format('revoke all on function %I.deal_chat_take_budget(text, integer, bigint, text, numeric) from %s',
                   сх, кому);
  end if;
  if служебная is not null then
    execute format('grant select, insert, update, delete on table %I.deal_chat_state to %s', сх, служебная);
    execute format('grant select, insert on table %I.deal_chat_ctx to %s', сх, служебная);
    execute format('grant update (voided_at) on table %I.deal_chat_ctx to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.deal_chat_ctx_id_seq to %s', сх, служебная);
    execute format('grant select, insert on table %I.deal_chat_spend to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.deal_chat_spend_id_seq to %s', сх, служебная);
    execute format('grant select, insert on table %I.deal_chat_holds to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.deal_chat_holds_id_seq to %s', сх, служебная);
    foreach n in array array['deal_chat_hist', 'deal_chat_facts'] loop
      execute format('grant select, insert on table %I.%I to %s', сх, n, служебная);
      execute format('grant update (voided_at) on table %I.%I to %s', сх, n, служебная);
      execute format('grant usage, select on sequence %I.%I to %s', сх, n || '_id_seq', служебная);
    end loop;
    foreach n in array array['deal_chat_ctx_latest', 'deal_chat_money_latest', 'deal_chat_contract_latest',
                             'deal_chat_sourcing_recent', 'deal_chat_important_latest'] loop
      execute format('grant select on %I.%I to %s', сх, n, служебная);
    end loop;
    execute format('grant execute on function %I.deal_chat_take_budget(text, integer, bigint, text, numeric) to %s',
                   сх, служебная);
  end if;
end $$;
