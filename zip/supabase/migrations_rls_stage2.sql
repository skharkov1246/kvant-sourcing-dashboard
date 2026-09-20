-- УЖЕСТОЧЕНИЕ ДОСТУПА К БАЗЕ ЗИП, СТУПЕНЬ 2: таблицы, которые нужны сайту.
--
-- НЕ ПРИМЕНЯТЬ, ПОКА НЕ ВЫПОЛНЕНО УСЛОВИЕ НИЖЕ. Эта ступень опаснее первой:
-- перечисленные таблицы браузер действительно запрашивает — через прокси воркера,
-- но запрашивает. Если воркер ходит в базу публикуемым ключом, а не сервисным,
-- снятие прав у anon положит сайт ЗИП целиком.
--
-- УСЛОВИЕ. zip/site/_worker.js:216 берёт
--     const key = (env && env.SUPABASE_SERVICE_KEY) || SUPA_FALLBACK_KEY;
-- Сервисная роль обходит RLS, публикуемый — нет. Значит перед применением надо
-- убедиться в двух вещах, и обе проверяются не в репозитории:
--
--   1. секрет SUPABASE_SERVICE_KEY задан в Cloudflare для проекта kvant-zip
--      И ИМЕННО В ОКРУЖЕНИИ Production (у Pages переменные задаются раздельно
--      для Production и Preview);
--   2. сайт ЗИП после этого пересобран и работает — читает и сохраняет.
--      Документация Cloudflare прямо не говорит, подхватит ли новый секрет уже
--      идущий деплой, а страница про секреты Workers пишет, что задавать надо
--      «before a deployment that uses those secrets». Пересборка — один клик:
--      Actions → «ZIP base deploy» → Run workflow.
--
-- Владелец подтвердил 20.09.2026, что секрет задан. Окружение по снимку панели
-- не различимо, и пересборка после этого не делалась, — поэтому ступень 2 лежит
-- отдельным файлом и ждёт этих двух проверок.
--
-- ПОСЛЕ ПРИМЕНЕНИЯ запасной ключ в zip/site/_worker.js:63 становится бесполезен
-- и его надо убрать из кода. Только тогда имеет смысл учить гейт шаблонам
-- sb_publishable_ и sb_secret_ (пункт 0.6 плана): пока ключ в дереве, шаблон
-- красит каждый pull request.
--
-- Идемпотентно.

set statement_timeout = '5min';
set lock_timeout      = '30s';

do $$
declare n text;
begin
  foreach n in array array[
    'price_records',   -- цены: сайт читает и пишет
    'drawings',        -- чертежи: читает, пишет, удаляет
    'samples',         -- образцы: то же
    'gt_notes',        -- правки инженеров ГТУ
    -- четыре таблицы, чей DDL в репозитории отсутствует
    -- (zip/supabase/migrations.sql:6 называет их созданными ранее),
    -- поэтому их политики здесь снимаются вслепую, но по тем же именам
    'positions', 'odm_suppliers', 'rfq_requests', 'change_log'
  ] loop
    if to_regclass('public.' || n) is null then
      raise notice 'таблицы % нет — пропускаю', n;
      continue;
    end if;
    execute format('alter table public.%I enable row level security', n);
    execute format('alter table public.%I force  row level security', n);
    execute format('drop policy if exists %I on public.%I', n || '_all', n);
    execute format('revoke all on table public.%I from anon, authenticated', n);
    execute format('grant select, insert, update, delete on table public.%I to service_role', n);
  end loop;
end $$;

-- Объекты хранилища: те же два бакета, что открыты в migrations.sql.
do $$
begin
  if to_regclass('storage.objects') is not null then
    drop policy if exists drawings_read   on storage.objects;
    drop policy if exists drawings_write  on storage.objects;
    drop policy if exists drawings_delete on storage.objects;
    drop policy if exists samples_read    on storage.objects;
    drop policy if exists samples_write   on storage.objects;
    drop policy if exists samples_delete  on storage.objects;
  end if;
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
begin
  for v in
    select distinct u.view_name
      from information_schema.view_table_usage u
      join pg_class c on c.relname = u.view_name
      join pg_namespace ns on ns.oid = c.relnamespace and ns.nspname = 'public'
     where u.view_schema = 'public' and u.table_schema = 'public'
       and u.table_name in ('price_records', 'drawings', 'samples', 'gt_notes',
                            'positions', 'odm_suppliers', 'rfq_requests', 'change_log')
       -- Трогаем только те, что RLS ОБХОДЯТ. Вид с security_invoker исполняется
       -- правами вызывающего: закрытую таблицу под собой он уже не отдаёт, и
       -- снимать с него права не за что. Снять — ничего не выиграть в защите и
       -- сломать будущего читателя.
       and not coalesce('security_invoker=true' = any (c.reloptions), false)
  loop
    execute format('revoke all on table public.%I from anon, authenticated', v);
    execute format('grant select on table public.%I to service_role', v);
    закрыто := закрыто + 1;
    raise notice 'представление % обходило RLS закрытой таблицы — права сняты', v;
  end loop;
  raise notice 'представлений закрыто: %', закрыто;
end $$;

-- ЧТО ЭТА ПРОВЕРКА ЛОВИТ, А ЧТО НЕТ. Она списочная, и поймать таблицу, которую
-- добавили в базу, но забыли здесь, НЕ МОЖЕТ: у таблиц второй ступени нет общего
-- шаблона имени, в отличие от mach_* первой ступени, где проверка идёт по образцу
-- и такую забывчивость ловит (проверено прогоном: незаявленная mach_novaya роняет
-- ступень 1 с кодом 3). Самопроверка здесь подтверждает лишь, что перечисленное
-- закрыто. Новая таблица базы ЗИП, открытая anon, обнаружится только замером
-- scripts/zip_rls_readiness.py, который сверяет политики с белым списком воркера.
--
-- Имена печатаем, а не считаем: «осталось 3 прав» может быть и одной таблицей
-- с тремя грантами, и тремя таблицами — по такому сообщению непонятно, что чинить.
do $$
declare забытые text;
begin
  select string_agg(distinct table_name, ', ' order by table_name) into забытые
  from information_schema.role_table_grants
  where table_schema = 'public'
    and grantee in ('anon', 'authenticated')
    and table_name in ('price_records', 'drawings', 'samples', 'gt_notes',
                       'positions', 'odm_suppliers', 'rfq_requests', 'change_log');
  if забытые is not null then
    raise exception 'у anon/authenticated остались права на таблицы ступени 2: %', забытые;
  end if;
end $$;
