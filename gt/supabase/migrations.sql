-- Библиотека ГТУ — правки инженеров: заметки, статусы, оценки, причины скрытия.
-- Идемпотентно: можно прогонять повторно. Применение:
--   • Supabase → SQL Editor → вставить и Run;  ИЛИ
--   • CI (zip-db.yml): psql "$SUPABASE_DB_URL" -f gt/supabase/migrations.sql
--
-- ЗАЧЕМ ТАБЛИЦА. До неё правки жили в localStorage браузера каждого сорсера и
-- никуда больше не уезжали: их не видели коллеги, они не переживали смену
-- браузера, а привязка к нормализованному имени компании рвалась при каждом
-- схлопывании дублей — заметка оставалась в хранилище, но исчезала из выдачи.
-- Здесь правка живёт отдельно от карточки и переживает любую пересборку сайта.

create table if not exists gt_notes (
  id      bigint generated always as identity primary key,
  scope   text not null,                 -- страница: index | rfq | hot | solar | cummins | lm6000 | f4000 | v643a | ms6001b | wizard
  key     text not null,                 -- к чему привязана: нормализованное имя компании, PN или код позиции
  names   jsonb default '[]'::jsonb,     -- как компания называлась в момент правки (включая altNames после склейки)
  kind    text not null default 'note',  -- note | st | ex | why | exwhy | pn_note | pn_st
  text    text not null default '',
  author  text not null default '',      -- почта вошедшего; проставляет воркер по подписи Cloudflare Access
  at      timestamptz not null default now(),
  removed boolean not null default false -- снятая правка не удаляется, а помечается: история не теряется
);

create index if not exists gt_notes_scope_key on gt_notes (scope, key);
create index if not exists gt_notes_author    on gt_notes (author);
create index if not exists gt_notes_at        on gt_notes (at desc);

comment on table gt_notes is
  'Правки инженеров в библиотеке ГТУ: заметки, статусы, оценки. Автор проставляется воркером по подписи входа.';
comment on column gt_notes.key is
  'Нормализованное имя компании (nrm) или PN. Ищется по набору кандидатов: ключ + altNames, иначе правка осиротеет при склейке дублей.';
comment on column gt_notes.removed is
  'Снятие правки. Строки не удаляются физически — иначе потеря становится необратимой.';

-- Доступ. Сайт ходит не напрямую, а через прокси /db в воркере (zip/site/_worker.js):
-- ключ Supabase в браузер не попадает, а до прокси добирается только тот, кто прошёл
-- Cloudflare Access и имеет право «gt». Политика повторяет принятую в базе ЗИП.
alter table gt_notes enable row level security;

drop policy if exists gt_notes_all on gt_notes;
create policy gt_notes_all on gt_notes for all
  to anon, authenticated using (true) with check (true);

-- Физическое удаление закрыто и на стороне базы: воркер отбивает DELETE, но если
-- кто-то придёт мимо него — правки всё равно не пропадут.
revoke delete on gt_notes from anon, authenticated;
