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

-- РОЛИ SUPABASE МОГУТ ОТСУТСТВОВАТЬ. anon, authenticated и service_role заводит
-- платформа; на чистом PostgreSQL их нет, и «revoke … from anon» роняет весь файл
-- с «role "anon" does not exist». Прогон против прода этого не покажет никогда —
-- там роли есть; зато проверить ужесточение где-либо ещё становится невозможно.
-- За эту сессию я трижды создавал роли руками, чтобы прогнать этот файл локально,
-- и трижды не замечал, что сам файл к такому не готов. Ту же ошибку в схеме
-- поставщиков нашёл прогон 20.09.2026 20:40.
--
-- Роли здесь НЕ СОЗДАЮТСЯ: это дело платформы, а не миграции. Права снимаются
-- только с тех, кто есть; нет роли — нечего у неё и снимать.
create or replace function zip_роли_которые_есть(имена text[]) returns text as $$
  select string_agg(quote_ident(r.rolname), ', ')
    from pg_roles r where r.rolname = any (имена);
$$ language sql stable;

do $$
declare n text;
declare найдено int := 0;
declare кому text := zip_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := zip_роли_которые_есть(array['service_role']);
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
    if кому is not null then
      execute format('revoke all on table public.%I from %s', n, кому);
    end if;
    if служебная is not null then
      execute format('grant select, insert, update, delete on table public.%I to %s',
                     n, служебная);
    end if;
  end loop;
  raise notice 'обработано таблиц: %', найдено;
end $$;

-- ПРЕДСТАВЛЕНИЯ НАД ЗАКРЫТЫМИ ТАБЛИЦАМИ. Обычное представление исполняется
-- правами ВЛАДЕЛЬЦА и RLS таблиц под собой не применяет. Закрыть таблицу и
-- оставить открытым представление над ней — значит не закрыть ничего: данные
-- продолжают читаться, только через другое имя.
--
-- Нашёл это не разбор, а сам прогон: 20.09.2026 самопроверка ниже уперлась в
-- mach_channels_ask — «select * from mach_channels», то есть ровно обход
-- ужесточения, которое эта миграция и делает. Первая версия файла его не видела.
--
-- Ищем по ЗАВИСИМОСТИ, а не по имени: представление может называться как угодно,
-- а читать закрытую таблицу. Имя ловит только то, что кто-то не забыл назвать
-- правильно.
do $$
declare v text;
declare закрыто int := 0;
declare кому text := zip_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := zip_роли_которые_есть(array['service_role']);
begin
  for v in
    select distinct u.view_name
      from information_schema.view_table_usage u
      join pg_class c on c.relname = u.view_name
      join pg_namespace ns on ns.oid = c.relnamespace and ns.nspname = 'public'
     where u.view_schema = 'public' and u.table_schema = 'public'
       and (u.table_name like 'mach\_%' or u.table_name = 'objects')
       -- Трогаем только те, что RLS ОБХОДЯТ. Вид с security_invoker исполняется
       -- правами вызывающего: закрытую таблицу под собой он уже не отдаёт, и
       -- снимать с него права не за что. Снять — ничего не выиграть в защите и
       -- сломать будущего читателя.
       and not coalesce('security_invoker=true' = any (c.reloptions), false)
  loop
    if кому is not null then
      execute format('revoke all on table public.%I from %s', v, кому);
    end if;
    if служебная is not null then
      execute format('grant select on table public.%I to %s', v, служебная);
    end if;
    закрыто := закрыто + 1;
    raise notice 'представление % обходило RLS закрытой таблицы — права сняты', v;
  end loop;
  raise notice 'представлений закрыто: %', закрыто;
end $$;

-- Последовательности этих таблиц — той же строгости, иначе anon двигает счётчик.
do $$
declare s text;
declare кому text := zip_роли_которые_есть(array['anon', 'authenticated']);
declare служебная text := zip_роли_которые_есть(array['service_role']);
begin
  for s in select c.relname
             from pg_class c
             join pg_namespace ns on ns.oid = c.relnamespace
            where ns.nspname = 'public' and c.relkind = 'S'
              and (c.relname like 'mach\_%' or c.relname like 'objects\_%')
  loop
    if кому is not null then
      execute format('revoke all on sequence public.%I from %s', s, кому);
    end if;
    if служебная is not null then
      execute format('grant usage, select on sequence public.%I to %s', s, служебная);
    end if;
  end loop;
end $$;

-- Проверка: ни у одной таблицы ступени 1 не должно остаться прав у anon.
--
-- ШИРЕ СПИСКА ВЫШЕ, И НАМЕРЕННО. Список имён в цикле закрыт, а проверка идёт по
-- образцу mach_*: таблица, добавленная в базу позже и в список не внесённая,
-- останется открытой — и уронит этот прогон вместо того, чтобы тихо висеть
-- открытой. Красный здесь означает «допиши имя в список», а не «ужесточение не
-- применилось»: блоки выше отработали своей транзакцией и откату не подлежат.
--
-- Имена печатаем, а не считаем: «осталось 3 прав» может быть и одной таблицей
-- с тремя грантами, и тремя таблицами — по такому сообщению непонятно, что чинить.
do $$
declare забытые text;
begin
  select string_agg(distinct g.table_name, ', ' order by g.table_name) into забытые
  from information_schema.role_table_grants g
  join pg_class c on c.relname = g.table_name
  join pg_namespace ns on ns.oid = c.relnamespace and ns.nspname = 'public'
  where g.table_schema = 'public'
    and g.grantee in ('anon', 'authenticated')
    and (g.table_name like 'mach\_%' or g.table_name = 'objects')
    -- Вид с security_invoker закрытую таблицу под собой не отдаёт: RLS работает
    -- по вызывающему. Он тут не нарушение, и ронять из-за него прогон незачем.
    and not coalesce('security_invoker=true' = any (c.reloptions), false);
  if забытые is not null then
    raise exception 'у anon/authenticated остались права на таблицы ступени 1: %'
                    '. Впишите их в список выше и прогоните заново', забытые;
  end if;
end $$;
