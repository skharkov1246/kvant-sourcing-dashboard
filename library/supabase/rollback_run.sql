-- ОТКАТ ОДНОГО ПРОГОНА СВЕДЕНИЯ ПО ЕГО КЛЮЧУ. Адрес прогона — :run_id.
--
--     psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -v run_id="'merge-1234567890'" \
--          -f library/supabase/rollback_run.sql
--
-- ЗАЧЕМ. Каждая строка записи несёт run_id — ровно ради этого (CLAUDE.md,
-- правило 6). Первый боевой прогон 20.09.2026 записал 9 603 сущности, и 171 из
-- них склеилась неверно: опознание по имени не спрашивало домен, поэтому две
-- компании с одинаковым названием и РАЗНЫМИ сайтами получили один номер.
-- Сведение их разводило, запись склеивала обратно.
--
-- ПОЧЕМУ ОТКАТ ЦЕЛИКОМ, А НЕ ПОЧИНКА 171. Номер выдаётся навсегда, и разбирать
-- его задним числом опаснее, чем переиздать всё, пока номера никем не
-- использованы: их выдали минуты назад, ни один не ушёл в договор. Целиком —
-- одно понятное состояние, по частям — состояние, которого никто не проверял.
--
-- ПОРЯДОК УДАЛЕНИЯ ОТ ЗАВИСИМЫХ К ГЛАВНОЙ. sup_number_registry ссылается на
-- sup_entity через on delete restrict: удали сущность первой — получишь отказ.
-- Это не помеха, а страховка от случайного удаления сущности с выданным номером.
--
-- УДАЛЯЕТСЯ ТОЛЬКО ЭТОТ ПРОГОН. Строки других run_id не трогаются, поэтому
-- откатить можно и один прогон из нескольких.

\set ON_ERROR_STOP on

begin;

-- Сущности этого прогона узнаются по их строке в реестре номеров: именно там
-- лежит «кому выдан номер и каким прогоном».
create temporary table откатываемые on commit drop as
  select sup_id from sup_number_registry where run_id = :run_id;

select 'сущностей к откату: ' || count(*) from откатываемые;

delete from sup_review      where run_id = :run_id;
delete from sup_identifier  where run_id = :run_id;
delete from sup_number_registry where run_id = :run_id;
delete from sup_entity      where id in (select sup_id from откатываемые);

select 'осталось сущностей в базе: ' || (select count(*) from sup_entity)
    || ', номеров: '                 || (select count(*) from sup_number_registry)
    || ', признаков: '               || (select count(*) from sup_identifier)
    || ', строк проверки: '          || (select count(*) from sup_review);

commit;
