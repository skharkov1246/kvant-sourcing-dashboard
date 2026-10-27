-- ФИНАЛЬНАЯ ЭКОНОМИКА СДЕЛКИ ИЗ ФАЙЛА «ЭКОНОМИКА ПРОЕКТА» — закрытые таблицы.
--
-- ЗАЧЕМ. Решение владельца 23.10.2026: финальная экономика открытой сделки реализации
-- — из файла «Экономика проекта» (общий диск Яндекс 360), 30 файлов в день, по одному,
-- от новых сделок к старым; читается по шаблону, без модели. Пишет прогон «Финальная
-- экономика — чтение файлов» (.github/workflows/econ-files.yml, scripts/econ_files.py,
-- шаблон — scripts/econ_template.py). Вкладка «Реализация» показывает только СВЕРЕННЫЕ
-- строки и только праву «Поставщики: договоры и деньги» (scripts/realization_ctx.py →
-- KV realctx:v1 → DEALCTX).
--
-- ПУТИ ФАЙЛА, ИМЕНИ, ССЫЛКИ ЗДЕСЬ НЕТ. file_key — sha256 пути файла внутри общего диска:
-- по нему видно «тот же файл прочитан снова», а сам путь не восстановить.
--
-- ТРИ ТАБЛИЦЫ, ФУНКЦИЯ И ВИД.
--   econ_file_attempts — каждая попытка скачать файл, удачная или нет; только
--     дописывается и не помечается: попытка потрачена. Предел суток по Москве
--     (30 файлов) держит econ_file_take_slot: под замком считает попытки суток и
--     дописывает новую, только если предел не исчерпан. Два прогона разом его не
--     удвоят.
--   econ_file_reads — итог чтения файла сделки: статус из закрытого списка (правило 15),
--     продажа в валюте подачи и в €, закупка в €, наценка над поставщиком (продажа /
--     закупка − 1 — НЕ маржа: логистика и таможня внутри коэффициента), переторжка,
--     позиций, чем проверено (totals_dev, coef_median, bad_lines — правило 16), курсы
--     файла (fx: единиц валюты подачи за 1 единицу валюты) и сверка с «Суммой» сделки
--     (bitrix_ratio = «Сумма» без НДС, OPPORTUNITY − TAX_VALUE, в валюте подачи / продажа
--     файла без НДС). У переторжки — ещё продажа «Нашего КП» в валюте подачи (kp_sale_sub):
--     «Сумма» сделки, оставленная по ней, — пункт очереди. Числа (продажа, закупка, наценка,
--     продажа КП) — только у «сверено»: у прочих статусов их нет (econ_file_reads_num_chk).
--     Только дописывается; откат прогона —
--     пометкой voided_at (правило 5), одной на строку:
--         update econ_file_reads set voided_at = now() where run_id = '<ключ>';
--   econ_file_runs — прогоны, прошедшие гейт сумм (passed_at), со счётом своих строк на
--     тот миг. Пишет прогон после гейта; только дописывается. Прогон упал, его сняли или
--     он не дошёл до гейта — строки в базе есть, но вкладка их не видит.
--   econ_file_take_slot(run_id, deal_id, предел) — взять попытку суток (true) или нет.
--   econ_file_latest — последнее непомеченное чтение сделки из прошедших гейт прогонов,
--     кроме сбоев доставки и нашего кода («ссылка не раскрылась», «не скачался», «сбой
--     чтения»): сбой сети или разбора не стирает прочитанного. Очередь прогона тоже
--     считает чтением только строки прошедших гейт прогонов (econ_files.База.прочитано).
--
-- ИДЕМПОТЕНТНО (правило 21): create … if not exists, новые колонки — add column if not
-- exists (create table if not exists таблицу не меняет), ограничения — сносом и созданием,
-- функции — create or replace, вид — сносом перед созданием. Роли Supabase — только
-- через проверку наличия (правило 20). Схема — из search_path. Применение — Actions →
-- «ZIP base — apply DB migrations» (zip-db.yml) и сам прогон econ-files.yml перед
-- записью. Файл ни от чего не зависит.

set statement_timeout = '5min';
set lock_timeout      = '10s';   -- правило 12: ALTER TABLE без него встаёт в очередь

create table if not exists econ_file_attempts (
  id       bigserial   primary key,
  at       timestamptz not null default now(),
  run_id   text        not null,
  deal_id  bigint      not null
);

create table if not exists econ_file_reads (
  id           bigserial   primary key,
  run_id       text        not null,             -- без ключа прогона откат невозможен (правило 6)
  method_ver   text        not null,
  deal_id      bigint      not null,
  read_at      timestamptz not null default now(),
  file_key     text,                             -- sha256 пути внутри диска; пути нет
  status       text        not null,
  sub_cur      text,                             -- валюта подачи (D4)
  sale_sub     numeric,                          -- продажа в валюте подачи, без НДС
  sale_eur     numeric,                          -- продажа, € без НДС
  purchase_eur numeric,                          -- закупка, € без НДС
  markup       numeric,                          -- наценка над поставщиком, доля
  rebid        boolean,                          -- продажа — по переторжке
  items        integer,                          -- позиций
  coef_median  numeric,                          -- медиана коэффициента (проверка 2)
  totals_dev   numeric,                          -- отклонение итогов от строк (проверка 1)
  bad_lines    integer     not null default 0,   -- позиций с «нет курса» или ошибкой
  fx           jsonb,                            -- {валюта: единиц валюты подачи за 1}
  deal_cur     text,                             -- валюта «Суммы» сделки при чтении
  bitrix_ratio numeric,                          -- «Сумма» без НДС в валюте подачи / продажа файла
  bitrix_note  text,                             -- почему сверки с суммой нет
  why          text,                             -- где сломалось: ячейка шаблона или проверка
  voided_at    timestamptz                       -- откат: пометка, а не удаление (правило 5)
);
-- вторая ревизия 25.10.2026: у переторжки — продажа «Нашего КП» в валюте подачи (AG2)
alter table econ_file_reads add column if not exists kp_sale_sub numeric;
-- v5 шаблона, 26.10.2026 (правило 16 — храни, почему получилось значение): откуда валюта
-- подачи (D4, D4 на месте при сдвинутых подписях или запасной путь: AE либо формула AF со строкой
-- Z:AA, подтверждённой подписью итога AG2 или строкой K:L), чем сверен её курс (строка Z:AA,
-- строка K:L, подача в евро) и на сколько столбцов вправо сдвинут шаблон. Закрытые списки —
-- econ_template.ИСТОЧНИКИ_ВАЛЮТЫ, ИСТОЧНИКИ_КУРСА, СДВИГИ (сверяет tests/test_econ_files_sql.py).
-- У строк до v5 — пусто.
alter table econ_file_reads add column if not exists sub_cur_src text;
alter table econ_file_reads add column if not exists rate_src text;
alter table econ_file_reads add column if not exists col_shift smallint;

create table if not exists econ_file_runs (
  run_id     text        primary key,
  passed_at  timestamptz not null default now(),
  method_ver text        not null,
  rows_n     integer     not null,               -- непомеченных строк прогона на миг гейта
  verified_n integer     not null                -- из них «сверено»
);

-- курсы файла: объект «код валюты → положительное число», и ничего больше
create or replace function econ_fx_ok(v jsonb) returns boolean
language plpgsql immutable as $$
declare
  к text;
  з jsonb;
begin
  if v is null then
    return true;
  end if;
  if jsonb_typeof(v) <> 'object' then
    return false;
  end if;
  for к, з in select * from jsonb_each(v) loop
    if к !~ '^[A-Z]{3}$' or jsonb_typeof(з) <> 'number' or (з::text)::numeric <= 0 then
      return false;
    end if;
  end loop;
  return true;
end $$;

do $$
begin
  alter table econ_file_attempts drop constraint if exists econ_file_attempts_chk;
  alter table econ_file_attempts add constraint econ_file_attempts_chk
    check (deal_id > 0 and run_id ~ '^[A-Za-z0-9._-]{1,80}$');

  alter table econ_file_reads drop constraint if exists econ_file_reads_keys_chk;
  alter table econ_file_reads add constraint econ_file_reads_keys_chk
    check (deal_id > 0 and run_id ~ '^[A-Za-z0-9._-]{1,80}$' and method_ver ~ '^[a-z0-9_.:-]{1,60}$'
           and (file_key is null or file_key ~ '^[0-9a-f]{64}$')
           and (sub_cur is null or sub_cur ~ '^[A-Z]{3}$')
           and (deal_cur is null or deal_cur ~ '^[A-Z]{3}$')
           -- «где сломалось» — адрес ячейки шаблона или имя проверки, места для текста нет
           and (why is null or why ~ '^[A-Z]{1,3}[0-9]{1,4}$' or why in ('AK:AL', 'позиции', 'проверка 1', 'проверка 2')));
  -- Список — дословно scripts/econ_files.СТАТУСЫ (сверяет tests/test_econ_files_sql.py).
  alter table econ_file_reads drop constraint if exists econ_file_reads_status_chk;
  alter table econ_file_reads add constraint econ_file_reads_status_chk
    check (status in ('сверено', 'не открылся', 'шаблон другой', 'валюта подачи не распознана',
                      'нет сохранённых значений', 'нет позиций', 'нет курса или ошибка',
                      'переторжка заполнена не везде', 'итоги не сходятся', 'неправдоподобно',
                      'ссылка не раскрылась', 'не общий диск', 'не xlsx', 'не скачался', 'сбой чтения'));
  alter table econ_file_reads drop constraint if exists econ_file_reads_note_chk;
  alter table econ_file_reads add constraint econ_file_reads_note_chk
    check ((bitrix_note is null or bitrix_note in ('валюта сделки другая', 'суммы нет'))
           and not (bitrix_note is not null and bitrix_ratio is not null)
           and (bitrix_ratio is null or bitrix_ratio >= 0));
  -- пути чтения (v5): закрытые списки; курс сверен — только у сверенной строки
  alter table econ_file_reads drop constraint if exists econ_file_reads_src_chk;
  alter table econ_file_reads add constraint econ_file_reads_src_chk
    check ((sub_cur_src is null or (sub_cur_src in ('D4', 'D4 вне сдвига', 'AE', 'AE и подпись AG2',
                                                    'формула AF, Z:AA и K:L', 'формула AF, Z:AA и подпись AG2')
                                    and sub_cur is not null))
           and (rate_src is null or (rate_src in ('Z:AA', 'K:L', 'евро') and status = 'сверено'))
           and (col_shift is null or col_shift in (0, 1)));
  alter table econ_file_reads drop constraint if exists econ_file_reads_fx_chk;
  alter table econ_file_reads add constraint econ_file_reads_fx_chk check (econ_fx_ok(fx));
  -- Суммы — только у сверенной строки: у несверенной их нет вовсе (их положительность и
  -- согласованность — в econ_file_reads_verified_chk). Отрицательная «продажа»
  -- неправдоподобного файла не встаёт в базу и не роняет прогон.
  alter table econ_file_reads drop constraint if exists econ_file_reads_num_chk;
  alter table econ_file_reads add constraint econ_file_reads_num_chk
    check ((items is null or items >= 0) and bad_lines >= 0
           and (totals_dev is null or totals_dev >= 0)
           and (status = 'сверено' or num_nulls(sale_sub, sale_eur, purchase_eur, markup, kp_sale_sub) = 5));
  -- Сверенная строка несёт все числа, и они согласованы: продажа не ниже закупки,
  -- наценка — из них же, позиции есть, сбоев нет. Иначе публиковать нечего.
  alter table econ_file_reads drop constraint if exists econ_file_reads_verified_chk;
  alter table econ_file_reads add constraint econ_file_reads_verified_chk
    check (status <> 'сверено' or (
           -- сравнение с NULL проверку не роняет — каждое число названо явно
           num_nonnulls(file_key, sub_cur, sale_sub, sale_eur, purchase_eur, markup, rebid, items,
                        coef_median, totals_dev) = 10
           and sale_sub > 0 and purchase_eur > 0
           and sale_eur >= purchase_eur and rebid is not null and items > 0 and bad_lines = 0
           and totals_dev <= 0.005 and coef_median between 1 and 5
           and abs(markup - (sale_eur / purchase_eur - 1)) <= 0.00001
           -- продажа «Нашего КП» — только у переторжки; у строк до 25.10.2026 её нет вовсе
           and (kp_sale_sub is null or (rebid and kp_sale_sub > 0))));
  alter table econ_file_runs drop constraint if exists econ_file_runs_chk;
  alter table econ_file_runs add constraint econ_file_runs_chk
    check (run_id ~ '^[A-Za-z0-9._-]{1,80}$' and method_ver ~ '^[a-z0-9_.:-]{1,60}$'
           and rows_n >= 0 and verified_n between 0 and rows_n);
end $$;

-- одно чтение сделки на прогон: повтор записи того же прогона не задваивает
create unique index if not exists econ_file_reads_run_deal on econ_file_reads (run_id, deal_id);
create index if not exists econ_file_reads_deal
  on econ_file_reads (deal_id, read_at desc, id desc) where voided_at is null;
create index if not exists econ_file_attempts_at on econ_file_attempts (at);
create index if not exists econ_file_attempts_run on econ_file_attempts (run_id);

-- Чтение только дописывается: удалить нельзя, поменять — лишь пометку отката, и один раз.
create or replace function econ_file_reads_append_only() returns trigger
language plpgsql as $$
begin
  if tg_op in ('DELETE', 'TRUNCATE') then
    raise exception 'econ_file_reads только дописывается: удалять нельзя, откат — пометкой voided_at';
  end if;
  if old.voided_at is not null
     or (to_jsonb(new) - 'voided_at') is distinct from (to_jsonb(old) - 'voided_at') then
    raise exception 'econ_file_reads только дописывается: меняется лишь пометка отката voided_at, и один раз';
  end if;
  return new;
end $$;

drop trigger if exists econ_file_reads_append_only on econ_file_reads;
create trigger econ_file_reads_append_only before update or delete on econ_file_reads
  for each row execute function econ_file_reads_append_only();
drop trigger if exists econ_file_reads_no_truncate on econ_file_reads;
create trigger econ_file_reads_no_truncate before truncate on econ_file_reads
  for each statement execute function econ_file_reads_append_only();

-- Попытки — только дописываются, без пометок: предел суток считается их числом.
create or replace function econ_file_attempts_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'econ_file_attempts только дописывается: попытка не правится и не удаляется';
end $$;

-- Решение гейта — тоже: прогон, прошедший гейт, не «распроходит»; снять его строки с
-- вкладки — пометкой voided_at в econ_file_reads.
create or replace function econ_file_runs_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'econ_file_runs только дописывается: решение гейта не правится; откат — voided_at у строк';
end $$;

drop trigger if exists econ_file_runs_append_only on econ_file_runs;
create trigger econ_file_runs_append_only before update or delete on econ_file_runs
  for each row execute function econ_file_runs_append_only();
drop trigger if exists econ_file_runs_no_truncate on econ_file_runs;
create trigger econ_file_runs_no_truncate before truncate on econ_file_runs
  for each statement execute function econ_file_runs_append_only();

drop trigger if exists econ_file_attempts_append_only on econ_file_attempts;
create trigger econ_file_attempts_append_only before update or delete on econ_file_attempts
  for each row execute function econ_file_attempts_append_only();
drop trigger if exists econ_file_attempts_no_truncate on econ_file_attempts;
create trigger econ_file_attempts_no_truncate before truncate on econ_file_attempts
  for each statement execute function econ_file_attempts_append_only();

-- Попытка суток по Москве (UTC+3, без перехода на летнее время с 2014 г.). Замок
-- транзакции держит счёт и запись вместе: второй прогон ждёт и считает уже с ней.
-- Предел владельца — 30 в сутки (23.10.2026); больший вход его не поднимет.
create or replace function econ_file_take_slot(p_run_id text, p_deal_id bigint, p_cap integer)
returns boolean language plpgsql as $$
declare
  предел constant integer := 30;
  начало timestamptz :=
    (date_trunc('day', (now() at time zone 'UTC') + interval '3 hours') - interval '3 hours') at time zone 'UTC';
  взято  integer;
begin
  perform pg_advisory_xact_lock(hashtext('econ_file_take_slot'));
  select count(*) into взято from econ_file_attempts where at >= начало;
  if взято >= least(greatest(coalesce(p_cap, 0), 0), предел) then
    return false;
  end if;
  insert into econ_file_attempts (run_id, deal_id) values (p_run_id, p_deal_id);
  return true;
end $$;

-- Снос перед созданием: «create or replace view» не меняет состав колонок. Только строки
-- прогонов, прошедших гейт (econ_file_runs): упавший или снятый прогон вкладке не виден.
drop view if exists econ_file_latest;
create view econ_file_latest with (security_invoker = true) as
select distinct on (r.deal_id)
       r.deal_id, r.read_at, r.status, r.sub_cur, r.sale_sub, r.sale_eur, r.purchase_eur, r.markup,
       r.rebid, r.items, r.fx, r.deal_cur, r.bitrix_ratio, r.bitrix_note, r.kp_sale_sub, r.method_ver, r.run_id,
       r.sub_cur_src, r.rate_src, r.col_shift
  from econ_file_reads r
  join econ_file_runs g on g.run_id = r.run_id
 where r.voided_at is null
   and r.status not in ('ссылка не раскрылась', 'не скачался', 'сбой чтения')
 order by r.deal_id, r.read_at desc, r.id desc;

create or replace function econ_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

-- Доступ: RLS плюс FORCE, ничего у PUBLIC, anon и authenticated; сервисному ключу —
-- чтение и вставка, у чтений — ещё пометка отката. У econ_file_runs последовательности нет.
do $$
declare
  сх        text := current_schema();
  кому      text := econ_роли_которые_есть(array['anon', 'authenticated']);
  служебная text := econ_роли_которые_есть(array['service_role']);
  n         text;
begin
  foreach n in array array['econ_file_attempts', 'econ_file_reads', 'econ_file_runs'] loop
    execute format('comment on table %I.%I is %L', сх, n, 'econ_files_schema:v1');
    execute format('alter table %I.%I enable row level security', сх, n);
    execute format('alter table %I.%I force  row level security', сх, n);
    execute format('revoke all on table %I.%I from public', сх, n);
    if кому is not null then
      execute format('revoke all on table %I.%I from %s', сх, n, кому);
    end if;
  end loop;
  foreach n in array array['econ_file_attempts_id_seq', 'econ_file_reads_id_seq'] loop
    execute format('revoke all on sequence %I.%I from public', сх, n);
    if кому is not null then
      execute format('revoke all on sequence %I.%I from %s', сх, n, кому);
    end if;
  end loop;
  execute format('revoke all on %I.econ_file_latest from public', сх);
  execute format('revoke all on function %I.econ_file_take_slot(text, bigint, integer) from public', сх);
  if кому is not null then
    execute format('revoke all on %I.econ_file_latest from %s', сх, кому);
  end if;
  if служебная is not null then
    execute format('grant select, insert on table %I.econ_file_attempts to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.econ_file_attempts_id_seq to %s', сх, служебная);
    execute format('grant select, insert on table %I.econ_file_reads to %s', сх, служебная);
    execute format('grant update (voided_at) on table %I.econ_file_reads to %s', сх, служебная);
    execute format('grant usage, select on sequence %I.econ_file_reads_id_seq to %s', сх, служебная);
    execute format('grant select, insert on table %I.econ_file_runs to %s', сх, служебная);
    execute format('grant select on %I.econ_file_latest to %s', сх, служебная);
    execute format('grant execute on function %I.econ_file_take_slot(text, bigint, integer) to %s',
                   сх, служебная);
  end if;
  execute format('revoke all on function %I.econ_роли_которые_есть(text[]) from public', сх);
end $$;
