# Модель данных раздела `/suppliers`

Префикс `sup_`. Живёт в том же проекте Supabase, рядом с `lib_*`, в контуре
upsert. Защита — по образцу `archive_*`, строжайшему из существующих:
`ENABLE` + `FORCE ROW LEVEL SECURITY`, полный `REVOKE` с `anon` и
`authenticated`, гранты только `service_role`.

## Что переиспользуется, а не создаётся заново

| нужное ТЗ | уже есть | что делаем |
|---|---|---|
| каталог деталей и вечный номер | `pnw/data/item_master.json`, `kv_registry.json`, 13 164 номера | переносим реестр в `sup_part_number`, номера сохраняем |
| кросс-ссылки номеров | `pnw/data/crossrefs.json` — 753 строки с `number_norm`, `kind`, `source` | переносим в `sup_pn_alias` |
| карточки исполнителей | `lib_suppliers` 4 535, `lib_part_suppliers` 4 726 | остаются; получают `sup_id` внешним ключом |
| наличие и цена у продавца | `ship_*` по 1 642 артикулам ЛУКОЙЛ | переносим с тремя осями целиком |
| разбор вложений | `library/indexer.py` | не дублируем, вызываем |
| слой пометок человека | `lib_row_junk` + `lib_mark_runs` | копируем механику в `sup_override` |
| заказы поставщикам | СП-172 через `contracts.py` | читаем, не копируем |
| нормализация названия | `library/load_suppliers.py:53`, `pnw/tools/build_suppliers.py:38` | одна из них становится канонической |

## Ядро: личность

```sql
-- Вечный бессмысленный ключ по регламенту pnw/НУМЕРАЦИЯ.md.
-- Всё, что может измениться, живёт в атрибутах, а не в номере.
create table if not exists sup_entity (
  id            text primary key,            -- KV-S-000123-7 / KV-G-000045-2
  kind          text not null,               -- 'legal' | 'group'
  parent_id     text references sup_entity(id),
  display_name  text not null,               -- для человека; НЕ ключ
  country       text,
  city          text,
  roles         text[],                      -- manufacturer|distributor|trader|service|logistics
  status        text not null default 'active',   -- active|restricted|blocked|inactive
  first_seen    date,
  last_activity date,
  resolution    text not null default 'candidate', -- candidate|resolved|merged
  confidence    numeric(3,2),
  created_at    timestamptz not null default now()
);

-- Номера-спутники: у поставщика один KV и сколько угодно чужих.
-- Все участвуют в поиске. Образец — номера-спутники детали.
create table if not exists sup_identifier (
  sup_id     text not null references sup_entity(id) on delete cascade,
  kind       text not null,   -- bitrix|inn|vat|domain|legal|trading|alias|reestr
  value      text not null,
  value_norm text not null,   -- ключ поиска: буквы и цифры, верхний регистр
  source     text not null,   -- откуда взято
  evidence   text,            -- ссылка на документ/строку
  status     text not null default 'stated',  -- verified|stated|inferred|candidate|rejected
  valid_from date, valid_to date,
  primary key (sup_id, kind, value_norm)
);
create index on sup_identifier (value_norm);
```

Слияние `ABC GmbH` / `ABC Germany` — это строка `alias` со ссылкой на
доказательство, а не перезапись `display_name`. Разделение обратимо: снимается
строка алиаса, сущности остаются.

## Провенанс: один механизм на все факты

```sql
-- Любой существенный факт несёт, откуда он и насколько ему верить.
-- Без этого следующая ошибка снова будет неизмеримой (CLAUDE.md, правило 16).
create table if not exists sup_fact (
  id           bigserial primary key,
  subject_kind text not null,        -- entity|contact|quote_line|part_supplier|term
  subject_id   text not null,
  field        text not null,
  value        jsonb not null,
  scope        jsonb,                -- {our_entity_id, pn, currency, …}
  status       text not null,        -- verified|stated|inferred|candidate|rejected|superseded
  source_type  text not null,        -- bitrix|document|web|manual|derived
  source_id    text,
  source_span  jsonb,                -- страница, ячейка, смещение
  source_at    timestamptz,
  ingested_at  timestamptz not null default now(),
  method       text not null,        -- имя правила
  method_ver   text not null,        -- версия правила: без неё сравнить прогоны нельзя
  confidence   numeric(3,2),
  verified_by  text, verified_at timestamptz,
  supersedes   bigint references sup_fact(id),
  run_id       text not null         -- ключ прогона: без него откат невозможен
);
```

Правило: **Candidate и Inferred никогда не повышаются до Verified автоматически.**

## Котировка как документ

Сейчас такой сущности нет вовсе — это главный структурный пробел.

```sql
create table if not exists sup_quote (
  id          bigserial primary key,
  sup_id      text not null references sup_entity(id),
  rfq_id      bigint,                -- карточка СП-166
  deal_id     bigint,
  revision    int  not null default 1,
  quote_date  date,
  doc_sha1    text,                  -- ключ документа: 32 % вложений — копии
  doc_side    text not null,         -- 'supplier' | 'ours'  ← без этого наш же
                                     --   Request file зачитывается поставщику
  parse_path  text, parse_conf numeric(3,2),
  review      text not null default 'new',
  run_id      text not null
);

create table if not exists sup_quote_line (
  id            bigserial primary key,
  quote_id      bigint not null references sup_quote(id) on delete cascade,
  line_no       int,
  pn_original   text not null,       -- КАК В ДОКУМЕНТЕ: ведущие нули, пробелы, суффиксы
  pn_norm       text,                -- буквы и цифры, верхний регистр
  pn_rule_ver   text not null,       -- версия правила нормализации
  maker_claimed text,                -- заявлено поставщиком
  maker_verified text,               -- подтверждено — ДРУГОЕ поле, не то же самое
  descr_original text,
  qty           numeric, unit text,
  price_basis   text,                -- unit|pack|set|lot  ← без него сравнение запрещено
  pack_qty      numeric,
  unit_price    numeric(18,4),       -- деньги НЕ во float
  line_total    numeric(18,4),
  currency      char(3),
  incoterms     text, named_place text,
  lead_time     int, lead_unit text,
  moq           numeric, validity date, warranty text,
  condition     text,                -- new|refurbished|used|unknown
  origin        text,
  comparable    boolean not null default false,
  comparable_why text                -- почему строка исключена из сравнения цен
);
```

**Правило сопоставимости.** Строка с неизвестной валютой, единицей или
`price_basis` не участвует в сравнении цен и не попадает в сумму. Она остаётся
видимой, с текстовой причиной в `comparable_why`.

## Наличие: три оси, складывать нельзя

```sql
create table if not exists sup_availability (
  sup_id      text not null references sup_entity(id),
  pn_norm     text not null,
  verdict     text not null,   -- in_stock|available_lead|pn_found_no_stock|oem_only|pn_not_found
  stock_grade text,            -- твёрдый|частичный|условный
  covers_qty  text not null,   -- full|partial|no|unknown   ← теряется сегодня
  stock_qty   numeric, lead_time int,
  price       numeric(18,4), currency char(3),
  price_basis text, pack_qty numeric,
  checked_at  timestamptz not null,   -- ← колонки даты проверки сегодня НЕТ
  method      text not null, method_ver text not null,
  source_url  text,
  run_id      text not null,
  primary key (sup_id, pn_norm, checked_at)
);
```

Две колонки помечены стрелками: обе сегодня теряются при переносе в
`lib_part_suppliers` (`library/load_parts.py:160-171`, `schema.sql:341-362`).
Именно они решают, идёт ли строка в деньги.

## Переговоры

```sql
create table if not exists sup_price_event (
  id         bigserial primary key,
  sup_id     text not null references sup_entity(id),
  pn_norm    text,
  kind       text not null,   -- initial|clarification|counteroffer|revised|agreed|po
  prev_id    bigint references sup_price_event(id),
  actor      text, at timestamptz not null,
  amount     numeric(18,4), currency char(3),
  qty_basis  numeric, price_basis text,
  source_id  text, run_id text not null
);
```

Скидка считается только между сопоставимыми начальным и конечным событием.
Несопоставимые — не считается вовсе, а не считается приблизительно.

## Наше юрлицо, условия, заказы

```sql
create table if not exists our_entity (              -- список пока пуст
  id text primary key, name text not null, inn text, active boolean default true
);

create table if not exists sup_terms (               -- версионируемое условие
  id bigserial primary key,
  sup_id text not null references sup_entity(id),
  our_entity_id text references our_entity(id),      -- заложено сразу
  field text not null, value jsonb not null,
  valid_from date, valid_to date,
  contract_ref text, approved_by text, approved_at timestamptz,
  source_id text, run_id text not null
);

create table if not exists sup_order (               -- проекция СП-172, не копия
  id bigint primary key,                             -- id карточки СП-172
  sup_id text references sup_entity(id),
  our_entity_id text references our_entity(id),
  deal_id bigint, po_number text,
  amount numeric(18,4), currency char(3),
  ordered_at date, promised_at date,
  stage text, stage_at timestamptz,
  synced_at timestamptz not null
);
```

Таблиц `sup_payment`, `sup_delivery`, `sup_incident` **не создаём**: источника
нет (решение владельца 20.09.2026). Интерфейс показывает «Нет данных об оплатах»,
а не ноль. Схема под будущий импорт описана здесь и заводится вместе с первым
источником, чтобы пустая таблица не выглядела фактом.

## Слой человека

```sql
-- Образец проверен на lib_row_junk: пометка живёт в таблице-спутнике,
-- снимается одной командой, обязательно несёт run_id.
create table if not exists sup_override (
  id bigserial primary key,
  subject_kind text not null, subject_id text not null, field text not null,
  source_value jsonb, corrected_value jsonb not null,
  author text not null, at timestamptz not null default now(),
  reason text not null, evidence text,
  scope jsonb, approved_by text, approved_at timestamptz,
  conflict text not null default 'none',   -- none|pending|resolved
  run_id text not null,
  version int not null default 1           -- optimistic locking
);

create table if not exists sup_review (      -- очередь на проверку, с приоритетом
  id bigserial primary key,
  kind text not null,       -- ambiguous_match|currency_unknown|term_conflict|
                            -- high_value|pn_suffix|entity_uncertain|low_conf_link
  subject_kind text, subject_id text,
  priority int not null default 5,
  opened_at timestamptz not null default now(),
  closed_at timestamptz, closed_by text, outcome text,
  payload jsonb not null
);
```

Эффективное значение — представление:

```sql
create view sup_effective with (security_invoker = true) as
select f.subject_kind, f.subject_id, f.field,
       coalesce(o.corrected_value, f.value) as value,
       case when o.id is null then f.status else 'verified' end as status,
       f.value as imported_value, o.author as corrected_by, o.reason
from sup_fact f
left join sup_override o
  on o.subject_kind = f.subject_kind and o.subject_id = f.subject_id
 and o.field = f.field and o.conflict <> 'pending';
```

Следующий импорт корректировку не трогает. При противоречии пишется строка в
`sup_review` со статусом `conflict = 'pending'`, и до разбора эффективным
остаётся подтверждённое человеком.

## Индексы

```sql
create index on sup_identifier (value_norm);
create index on sup_quote_line (pn_norm);
create index on sup_quote_line (quote_id, line_no);
create index on sup_availability (pn_norm, verdict);
create index on sup_order (sup_id, ordered_at desc);
create index on sup_fact (subject_kind, subject_id, field);
create index on sup_fact (run_id);
create index on sup_override (subject_kind, subject_id, field);
```

## Правила, обязательные к соблюдению

1. Деньги — `numeric`, не `float`. В `base/` сейчас `REAL`.
2. Оригинальный артикул хранится строкой без потерь рядом с нормализованным.
3. Версия правила (`method_ver`, `pn_rule_ver`) — обязательна: без неё прогоны
   несравнимы.
4. `run_id` — в каждой записи, иначе откат невозможен (`CLAUDE.md`, правило 6).
5. Пометка, а не удаление (правило 5).
6. Подзапрос внутри `CASE` не ссылается на обновляемую строку (правило 8).
7. `statement_timeout` — в строке подключения, не через `SET` (правило 9).
8. `lock_timeout` обязателен для `ALTER TABLE` (правило 12).
9. Миграции additive-first; `DROP` и `NOT NULL` — только после аудита данных.
