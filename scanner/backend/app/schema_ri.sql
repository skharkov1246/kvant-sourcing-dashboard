-- Дельта схемы под регламенты реверс-инжиниринга КВАНТ (Р-00…Р-13).
-- Применяется ПОВЕРХ schema.sql. Здесь центр тяжести домена смещается
-- с сессии на ДЕТАЛЬ: деталь живёт годами, переживает несколько замеров,
-- несколько проектов RE-ГГГГ-NNN и несколько событий замера.
--
-- Инварианты, добавленные этой дельтой:
--   R-1  код KV-D-NNNNN выдаётся один раз и навсегда, в т.ч. забракованным (Р-03 п.3)
--   R-2  ни одна позиция не попадает в реестр минуя буфер (Р-13 п.4)
--   R-3  свободный ввод в справочные поля запрещён (Р-03 п.2)
--   R-4  запись о браковке сохраняется — она предотвращает повторный замер (Р-03 п.5)
--   R-5  статусы «Замер» и «РКД» — независимые оси с гейтами переходов (Р-02 п.9.3, Р-03 п.5)

-- ─────────────────────────────────────────────────────────────────────────────
-- Справочники: значения только отсюда (Р-03 разд. 6)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE directory (
  code       TEXT PRIMARY KEY,   -- brand | equipment_type | measure_method |
                                 -- measure_status | rkd_status | reject_reason
  title      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE directory_entry (
  directory_code TEXT NOT NULL REFERENCES directory(code),
  value          TEXT NOT NULL,
  canonical      BOOLEAN NOT NULL DEFAULT true,
  -- Служебные значения Р-13 п.5: «БРЕНД НЕ ОПРЕДЕЛЁН», «УСТАНОВКА НЕ ОПРЕДЕЛЕНА».
  -- Это легальные значения справочника, а не свободный текст.
  service        BOOLEAN NOT NULL DEFAULT false,
  sort_order     INT NOT NULL DEFAULT 0,
  disabled_at    TIMESTAMPTZ,
  PRIMARY KEY (directory_code, value)
);

INSERT INTO directory (code, title) VALUES
  ('brand',          'Бренды (OEM)'),
  ('equipment_type', 'Типы оборудования (ISO 14224)'),
  ('measure_method', 'Методы замера'),
  ('measure_status', 'Статусы оси «Замер»'),
  ('rkd_status',     'Статусы оси «РКД»'),
  ('reject_reason',  'Причины браковки');

INSERT INTO directory_entry (directory_code, value, service, sort_order) VALUES
  ('brand',          'БРЕНД НЕ ОПРЕДЕЛЁН',        true, 0),
  ('equipment_type', 'УСТАНОВКА НЕ ОПРЕДЕЛЕНА',   true, 0),
  ('measure_method', 'ручной',                    false, 1),
  ('measure_method', 'КИМ',                       false, 2),
  ('measure_method', '3D-скан',                   false, 3),
  ('measure_method', 'комбинированный',           false, 4),
  ('measure_status', 'Не начато',                 false, 1),
  ('measure_status', 'В работе',                  false, 2),
  ('measure_status', 'На проверке',               false, 3),
  ('measure_status', 'Принят',                    false, 4),
  ('rkd_status',     'Заявка',                    false, 1),
  ('rkd_status',     'Замер',                     false, 2),
  ('rkd_status',     'Моделирование',             false, 3),
  ('rkd_status',     'КД',                        false, 4),
  ('rkd_status',     'Изготовление образца',      false, 5),
  ('rkd_status',     'Испытания',                 false, 6),
  ('rkd_status',     'Освоено',                   false, 7),
  ('rkd_status',     'Архив',                     false, 8),
  ('rkd_status',     'На стопе (нет продажи)',    false, 90),  -- приостановка, не терминал (Р-13 п.5.4)
  ('rkd_status',     'Забраковано',               false, 99),  -- терминал вне траектории (Р-03 п.5)
  ('reject_reason',  'нет маркировки',            false, 1),
  ('reject_reason',  'нет смысла замерять',       false, 2),
  ('reject_reason',  'экономически нецелесообразно', false, 3),
  ('reject_reason',  'технически нереализуемо',   false, 4);

-- ─────────────────────────────────────────────────────────────────────────────
-- Выдача кодов: сквозная, монотонная, невозвратная (Р-03 п.3)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE SEQUENCE kv_part_code_seq START 1;

CREATE TABLE kv_code_ledger (
  kv_code    TEXT PRIMARY KEY CHECK (kv_code ~ '^KV-D-[0-9]{5}$'),
  tenant_id  UUID NOT NULL REFERENCES tenant(id),
  issued_by  UUID NOT NULL REFERENCES app_user(id),
  issued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Ни UPDATE, ни DELETE: код не переиспользуется даже для ошибочных записей.
REVOKE UPDATE, DELETE ON kv_code_ledger FROM PUBLIC;

-- ─────────────────────────────────────────────────────────────────────────────
-- Деталь — центральная сущность (22 поля реестра, Р-03 п.4)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE part_record (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenant(id),
  kv_code          TEXT NOT NULL UNIQUE REFERENCES kv_code_ledger(kv_code),
  name_ru          TEXT NOT NULL,               -- «существительное + признаки»: «Вал ротора»
  name_en          TEXT,
  article_oem      TEXT,
  article_norm     TEXT,                        -- нормализация для дедупа
  brand            TEXT NOT NULL,               -- из справочника brand (проверка триггером)
  manufacturer     TEXT,
  equipment_type   TEXT NOT NULL,               -- из справочника equipment_type (ISO 14224)
  equipment_model  TEXT,
  equipment_serial TEXT,
  unit_node        TEXT,                        -- узел
  customer_ref     TEXT,                        -- заказчик / сделка
  material         TEXT,
  measure_method   TEXT,                        -- из справочника measure_method
  status_measure   TEXT NOT NULL DEFAULT 'Не начато',
  status_rkd       TEXT NOT NULL DEFAULT 'Заявка',
  reject_reason    TEXT,                        -- обязательно при «Забраковано» (CHECK ниже)
  stop_condition   TEXT,                        -- условие снятия со стопа + владелец (Р-13 п.5.4)
  folder_url       TEXT,                        -- ссылка на папку детали в Nextcloud (Р-02)
  surveyor         TEXT,                        -- Замерщик
  responsible_rkd  TEXT,                        -- Ответственный за РКД
  keywords         TEXT[] NOT NULL DEFAULT '{}',-- Ключевые слова/синонимы — обязательное (Р-04 п.5)
  category         CHAR(1) CHECK (category IN ('A','B','C','D','E','F','G','H')),
  notes            TEXT,
  supplier_slots   JSONB NOT NULL DEFAULT '{}', -- П1 Китай / П2 РФ / П3 (Р-03 п.4 поле 22)
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT reject_needs_reason CHECK (
    status_rkd <> 'Забраковано' OR reject_reason IS NOT NULL
  ),
  CONSTRAINT stop_needs_condition CHECK (
    status_rkd <> 'На стопе (нет продажи)' OR stop_condition IS NOT NULL
  )
);

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX part_dedup_article ON part_record (tenant_id, article_norm)
  WHERE article_norm IS NOT NULL;
CREATE INDEX part_dedup_name ON part_record USING gin (name_ru gin_trgm_ops);
CREATE INDEX part_keywords ON part_record USING gin (keywords);

-- Проекты РИ: RE-ГГГГ-NNN, многие-ко-многим (одна деталь — несколько проектов, Р-02)
CREATE TABLE re_project (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenant(id),
  code       TEXT NOT NULL CHECK (code ~ '^RE-[0-9]{4}-[0-9]{3}$'),
  deal_ref   TEXT,                              -- номер сделки Bitrix24
  UNIQUE (tenant_id, code)
);

CREATE TABLE part_project (
  part_id    UUID NOT NULL REFERENCES part_record(id),
  project_id UUID NOT NULL REFERENCES re_project(id),
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (part_id, project_id)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Событие замера: выезд / склад (Р-02 разд. 9)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE survey_event (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  kind        TEXT NOT NULL CHECK (kind IN ('field','warehouse')),
  event_date  DATE NOT NULL,
  customer    TEXT,
  order_no    TEXT,
  folder_url  TEXT,                             -- папка события в Nextcloud
  -- Пара ответственных: «ничьих» позиций быть не должно (Р-02 п.9.4)
  commercial_owner UUID REFERENCES app_user(id),
  engineer_owner   UUID REFERENCES app_user(id),
  closed_at   TIMESTAMPTZ,                      -- закрыто = все позиции терминальны (Р-02 п.9.5.8)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Буфер «Очередь на добавление» (Р-13 разд. 4)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE buffer_row (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenant(id),
  event_id         UUID NOT NULL REFERENCES survey_event(id),
  temp_folder_name TEXT NOT NULL,               -- {№ заказа}_{артикул} | {дата}_POSNN_{имя}
  raw_folder_url   TEXT,                        -- NULL → строка не разбирается (Р-13 п.4.2)
  brand            TEXT,                        -- из справочника, включая служебное значение
  equipment_type   TEXT,
  customer_ref     TEXT,
  surveyor         TEXT,
  note             TEXT,
  unplanned        BOOLEAN NOT NULL DEFAULT false,  -- «подброшенная» позиция (Р-13 п.5.3)
  unplanned_by     TEXT,
  state            TEXT NOT NULL DEFAULT 'PENDING' CHECK (state IN
                     ('PENDING','RETURNED','NEEDS_INFO',
                      'CLOSED_CODED','CLOSED_DUPLICATE','CLOSED_REJECTED')),
  needs_info_assignee TEXT,
  needs_info_due      DATE,
  resolution_kv_code  TEXT REFERENCES kv_code_ledger(kv_code),
  duplicate_of        TEXT REFERENCES kv_code_ledger(kv_code),
  created_on       DATE NOT NULL DEFAULT CURRENT_DATE,
  resolved_at      TIMESTAMPTZ,
  resolved_by      UUID REFERENCES app_user(id)  -- только владелец реестра
);

CREATE INDEX buffer_open ON buffer_row (tenant_id, state, created_on)
  WHERE state IN ('PENDING','NEEDS_INFO','RETURNED');

-- ─────────────────────────────────────────────────────────────────────────────
-- Документы детали: пара «исходник + нейтральный формат» (Р-13 п.3.2)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE part_document (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenant(id),
  part_id    UUID NOT NULL REFERENCES part_record(id),
  doc_type   TEXT NOT NULL CHECK (doc_type IN ('ПЗ','ЭСК','ФОТО','3D','СКАН','ЧЕРТ','ПМИ')),
  version    INT  NOT NULL,
  status     CHAR(1) NOT NULL DEFAULT 'Ч' CHECK (status IN ('Ч','П','У')),  -- Р-12
  file_url   TEXT NOT NULL,
  is_source  BOOLEAN NOT NULL,                  -- исходник CAD или нейтральный формат
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (part_id, doc_type, version, is_source)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Рабочий календарь: сроки регламентов считаются в рабочих днях
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE work_calendar (
  d          DATE PRIMARY KEY,
  is_working BOOLEAN NOT NULL
);

CREATE FUNCTION business_days_since(since DATE, until DATE DEFAULT CURRENT_DATE)
RETURNS BIGINT AS $$
  SELECT count(*) FROM work_calendar
   WHERE d > since AND d <= until AND is_working;
$$ LANGUAGE SQL STABLE;

-- Перенацеливание трассировки с сессии на деталь + слоты поставщиков
ALTER TABLE trace_link
  ADD COLUMN part_id UUID REFERENCES part_record(id),
  ADD COLUMN supplier_slot TEXT CHECK (supplier_slot IN ('P1_CN','P2_RU','P3'));

-- RLS на новые доменные таблицы — тот же рубеж 1, что и в schema.sql
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'part_record','re_project','survey_event','buffer_row','part_document'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON %I
        USING (tenant_id = current_tenant())
    $f$, t);
  END LOOP;
END $$;
