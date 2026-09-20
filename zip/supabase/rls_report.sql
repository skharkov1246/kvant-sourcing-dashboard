-- ЧТО ОТКРЫТО РОЛИ anon В СХЕМЕ public. Только чтение каталога, ничего не меняет.
-- Вызывается прогоном «ЗИП — ужесточение доступа» до и после применения ступени:
-- два одинаковых замера вокруг изменения показывают, что именно оно сделало.
--
-- ПОЧЕМУ НЕ СЧИТАЕМ ГРАНТЫ. Грант сам по себе ничего не открывает: у таблиц lib_*
-- он есть, но RLS включён и не заведено ни одной политики — роль anon не прочитает
-- ни строки. Прогон 20.09.2026 19:17 напечатал «572 права», и это число пугает,
-- ничего при этом не измеряя. Таблица открыта, когда у роли есть грант И
-- (RLS выключен ИЛИ для неё заведена разрешающая политика).
--
-- ПРЕДСТАВЛЕНИЯ СЧИТАЮТСЯ ОТДЕЛЬНО И НАМЕРЕННО. Обычное представление исполняется
-- правами владельца и RLS таблиц под собой обходит, поэтому грант на представление
-- закрытую таблицу открывает обратно. Считать его вместе с таблицами нельзя —
-- механика другая; не считать вовсе — значит врать, что закрыто всё.

\pset tuples_only on
\pset format unaligned

with grant_ as (
  select distinct table_name
    from information_schema.role_table_grants
   where table_schema = 'public'
     and grantee in ('anon', 'authenticated')
), policy_ as (
  select distinct tablename
    from pg_policies
   where schemaname = 'public'
     and (roles @> array['anon']::name[]
       or roles @> array['authenticated']::name[]
       or roles @> array['public']::name[])
), rel as (
  select c.relname, c.relkind, c.relrowsecurity
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
    join grant_ g on g.table_name = c.relname
)
select 'таблиц, ДОСТИЖИМЫХ для anon: ' || count(*) filter (
         where relkind in ('r', 'p')
           and (not relrowsecurity or relname in (select tablename from policy_)))
    || E'\n' ||
       'таблиц с грантом, но закрытых RLS: ' || count(*) filter (
         where relkind in ('r', 'p')
           and relrowsecurity and relname not in (select tablename from policy_))
    || E'\n' ||
       'представлений с грантом anon (RLS не защищает): ' || count(*) filter (
         where relkind in ('v', 'm'))
  from rel;

-- Адресно по ступени 1: её одиннадцать таблиц браузер не запрашивает, после
-- применения здесь обязан быть ноль.
select 'из них грантов на mach_* и objects: ' || count(*)
  from information_schema.role_table_grants
 where table_schema = 'public'
   and grantee in ('anon', 'authenticated')
   and (table_name like 'mach\_%' or table_name = 'objects');
