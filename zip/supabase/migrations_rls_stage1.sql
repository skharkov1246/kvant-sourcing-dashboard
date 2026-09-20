-- УЖЕСТОЧЕНИЕ ДОСТУПА К БАЗЕ ЗИП, СТУПЕНЬ 1: таблицы без единого потребителя.
--
-- НЕ ПРИМЕНЯЕТСЯ АВТОМАТИЧЕСКИ. В список zip-db.yml этот файл не внесён
-- намеренно: снятие прав у роли — изменение доступа в проде, и его включает
-- владелец отдельным решением (ТЗ §1.10, CLAUDE.md «не трогать без владельца»).
--
-- ЧТО ЗАКРЫВАЕТ. Одиннадцать таблиц, которым zip/supabase/migrations.sql выдал
-- роли anon политику «for all … using (true) with check (true)», то есть чтение,
-- запись И удаление. Публикуемый ключ лежит в публичном репозитории
-- (zip/site/_worker.js:63), значит доступ к ним есть у кого угодно.
--
-- ПОЧЕМУ ЭТИ ОДИННАДЦАТЬ БЕЗОПАСНЫ ПРЯМО СЕЙЧАС. Их не запрашивает браузер: в
-- белом списке прокси воркера (ZIP_DB_METHODS, zip/site/_worker.js:73-83) их нет,
-- и ни одна страница к ним не обращается. Измерено scripts/zip_rls_readiness.py,
-- закреплено tests/test_zip_rls_readiness.py. Сайт ЗИП от их закрытия не меняется
-- вообще — закрывать можно независимо от того, каким ключом ходит воркер.
--
-- Идемпотентно: повторный прогон ничего не ломает.

set statement_timeout = '5min';
set lock_timeout      = '30s';   -- ALTER без него ставит ACCESS EXCLUSIVE в очередь

do $$
declare n text;
declare найдено int := 0;
begin
  foreach n in array array[
    -- досье машины: заполняются сидом и серверными сборщиками, читаются
    -- скриптами репозитория. Из браузера к ним не обращается ничто.
    'mach_machines', 'mach_docs', 'mach_parts', 'mach_part_alts',
    'mach_channels', 'mach_prices', 'mach_specs', 'mach_tenders',
    'mach_customs', 'mach_faults',
    -- объекты: тем же порядком
    'objects'
  ] loop
    if to_regclass('public.' || n) is null then
      raise notice 'таблицы % нет — пропускаю', n;
      continue;
    end if;
    найдено := найдено + 1;
    execute format('alter table public.%I enable row level security', n);
    execute format('alter table public.%I force  row level security', n);
    -- политику «всё разрешено anon» снимаем поимённо: имена заданы в
    -- zip/supabase/migrations.sql и здесь повторены, чтобы снятие было явным.
    execute format('drop policy if exists %I on public.%I', n || '_all', n);
    execute format('revoke all on table public.%I from anon, authenticated', n);
    execute format('grant select, insert, update, delete on table public.%I to service_role', n);
  end loop;
  raise notice 'обработано таблиц: %', найдено;
end $$;

-- Последовательности этих таблиц — той же строгости, иначе anon двигает счётчик.
do $$
declare s text;
begin
  for s in select c.relname
             from pg_class c
             join pg_namespace ns on ns.oid = c.relnamespace
            where ns.nspname = 'public' and c.relkind = 'S'
              and (c.relname like 'mach\_%' or c.relname like 'objects\_%')
  loop
    execute format('revoke all on sequence public.%I from anon, authenticated', s);
    execute format('grant usage, select on sequence public.%I to service_role', s);
  end loop;
end $$;

-- Проверка: ни у одной обработанной таблицы не должно остаться прав у anon.
do $$
declare осталось int;
begin
  select count(*) into осталось
  from information_schema.role_table_grants
  where table_schema = 'public'
    and grantee in ('anon', 'authenticated')
    and (table_name like 'mach\_%' or table_name = 'objects');
  if осталось > 0 then
    raise exception 'у anon/authenticated осталось % прав на таблицы ступени 1', осталось;
  end if;
end $$;
