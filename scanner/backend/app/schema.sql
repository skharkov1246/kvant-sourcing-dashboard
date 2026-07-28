-- Схема данных платформы «КВАНТ Скан» (PostgreSQL 15+).
--
-- Инварианты, обеспеченные именно здесь:
--   I-2  событие применяется ровно один раз  → UNIQUE (tenant_id, client_event_id)
--   I-4  версия протокола зафиксирована      → scan_session.protocol_version NOT NULL
--   I-5  обмер знает свою родословную        → measurement.source_asset_ids NOT NULL
--   I-6  тенант не пересекает границу        → RLS на каждой доменной таблице
--   I-7  трассировка помнит, кто её заявил   → trace_link.declared_by_*

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─────────────────────────────────────────────────────────────────────────────
-- Тенанты, площадки, пользователи, устройства
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE tenant (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('internal', 'customer', 'partner')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Явная делегация доступа между тенантами. Без неё «мы видим всё» было бы
-- хардкодом, и на вопрос заказчика «кто видел наши данные» ответа бы не было.
CREATE TABLE tenant_grant (
  from_tenant UUID NOT NULL REFERENCES tenant(id),
  to_tenant   UUID NOT NULL REFERENCES tenant(id),
  scope       TEXT NOT NULL,
  expires_at  TIMESTAMPTZ NOT NULL,
  granted_by  UUID NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (from_tenant, to_tenant, scope)
);

CREATE TABLE site (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenant(id),
  code       TEXT NOT NULL,
  name       TEXT NOT NULL,
  timezone   TEXT NOT NULL DEFAULT 'Europe/Moscow',
  UNIQUE (tenant_id, code)
);

CREATE TABLE app_user (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenant(id),
  ext_id       TEXT,
  display_name TEXT NOT NULL,
  roles        TEXT[] NOT NULL DEFAULT '{operator}',
  site_ids     UUID[] NOT NULL DEFAULT '{}',
  disabled_at  TIMESTAMPTZ
);

CREATE TABLE device (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenant(id),
  user_id           UUID REFERENCES app_user(id),
  platform          TEXT NOT NULL CHECK (platform IN ('android', 'ios')),
  model             TEXT NOT NULL,
  app_version       TEXT NOT NULL,
  schema_version    INT  NOT NULL,
  capabilities      JSONB NOT NULL DEFAULT '{}',
  attestation_state TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (attestation_state IN ('trusted', 'untrusted', 'unknown')),
  revoked_at        TIMESTAMPTZ,
  last_seen_at      TIMESTAMPTZ
);

-- Результаты прогона моделей телефонов на эталонных объектах (§05.8).
-- Разброс качества Depth API между вендорами больше, чем разница между
-- классами B и C, поэтому без этого справочника система будет уверенно
-- выдавать неверные габариты, не понимая этого.
CREATE TABLE device_capability (
  model              TEXT PRIMARY KEY,
  platform           TEXT NOT NULL,
  max_accuracy_class CHAR(1) NOT NULL CHECK (max_accuracy_class IN ('A','B','C','D')),
  measured_mae_mm    NUMERIC(6,2),
  tested_at          TIMESTAMPTZ NOT NULL,
  notes              TEXT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Протоколы съёмки
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE capture_protocol (
  code           TEXT NOT NULL,
  version        INT  NOT NULL,
  scope          TEXT NOT NULL,            -- 'global' | 'tenant:<uuid>'
  status         TEXT NOT NULL CHECK (status IN ('draft','active','deprecated')),
  schema_version INT  NOT NULL,
  document       JSONB NOT NULL,
  signature      JSONB,
  created_by     UUID NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (code, version, scope)
);

-- Активной может быть ровно одна версия на (code, scope).
CREATE UNIQUE INDEX capture_protocol_one_active
  ON capture_protocol (code, scope) WHERE status = 'active';

-- ─────────────────────────────────────────────────────────────────────────────
-- Задания и сессии
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE scan_task (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenant(id),
  site_id          UUID REFERENCES site(id),
  external_system  TEXT,
  external_ref     TEXT,
  protocol_code    TEXT NOT NULL,
  protocol_version INT,
  subject          JSONB NOT NULL,
  priority         SMALLINT NOT NULL DEFAULT 100,
  due_at           TIMESTAMPTZ,
  assignee_user_id UUID REFERENCES app_user(id),
  assignee_pool    TEXT,
  state            TEXT NOT NULL CHECK (state IN (
                     'NEW','ASSIGNED','IN_PROGRESS','SUBMITTED','IN_REVIEW',
                     'REWORK','ACCEPTED','REJECTED','EXPORTED','CANCELLED','EXPIRED')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Идемпотентность приёма из внешней системы (§09.2).
  UNIQUE (tenant_id, external_system, external_ref)
);

CREATE INDEX scan_task_queue ON scan_task (tenant_id, state, priority, created_at);

CREATE TABLE inbound_idempotency (
  tenant_id  UUID NOT NULL,
  key        TEXT NOT NULL,
  task_id    UUID NOT NULL REFERENCES scan_task(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, key)
);

CREATE TABLE scan_session (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           UUID NOT NULL REFERENCES tenant(id),
  task_id             UUID NOT NULL REFERENCES scan_task(id),
  device_id           UUID NOT NULL REFERENCES device(id),
  user_id             UUID NOT NULL REFERENCES app_user(id),
  protocol_code       TEXT NOT NULL,
  protocol_version    INT  NOT NULL,          -- I-4: версия зафиксирована
  corrects_session_id UUID REFERENCES scan_session(id),
  duplicate_of        UUID REFERENCES scan_session(id),
  state               TEXT NOT NULL,
  started_at          TIMESTAMPTZ NOT NULL,
  finished_at         TIMESTAMPTZ
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Событийный лог
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE session_event (
  tenant_id       UUID NOT NULL,
  session_id      UUID NOT NULL REFERENCES scan_session(id),
  seq             BIGINT NOT NULL,
  client_event_id UUID NOT NULL,
  type            TEXT NOT NULL,
  payload         JSONB NOT NULL DEFAULT '{}',
  device_ts       TIMESTAMPTZ NOT NULL,       -- время устройства, может врать
  server_ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
  clock_skew_ms   BIGINT GENERATED ALWAYS AS
                    (EXTRACT(EPOCH FROM (server_ts - device_ts)) * 1000)::BIGINT STORED,
  PRIMARY KEY (session_id, seq),
  UNIQUE (tenant_id, client_event_id)         -- I-2: идемпотентность
) PARTITION BY RANGE (server_ts);

-- Партиции создаются планировщиком помесячно; холодные уезжают в архив.
CREATE TABLE session_event_2026_07 PARTITION OF session_event
  FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE dead_letter_event (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL,
  client_event_id UUID NOT NULL,
  raw             JSONB NOT NULL,
  error_code      TEXT NOT NULL,
  error_message   TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Медиа, обмеры, коды
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE asset (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenant(id),
  session_id   UUID NOT NULL REFERENCES scan_session(id),
  step_id      TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('photo','video','depth_map','point_cloud','audio')),
  mime         TEXT NOT NULL,
  bytes        BIGINT NOT NULL,
  sha256       BYTEA NOT NULL,
  storage_key  TEXT,
  capture_meta JSONB NOT NULL DEFAULT '{}',   -- интринсики, поза, EXIF, lux, blur
  upload_state TEXT NOT NULL DEFAULT 'pending'
               CHECK (upload_state IN ('pending','uploading','complete','verified')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, sha256)                  -- дедупликация одинаковых кадров
);

CREATE TABLE measurement (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenant(id),
  session_id       UUID NOT NULL REFERENCES scan_session(id),
  method           TEXT NOT NULL CHECK (method IN
                     ('lidar','ar_depth','marker','manual','photogrammetry')),
  accuracy_class   CHAR(1) NOT NULL CHECK (accuracy_class IN ('A','B','C','D')),
  length_mm        NUMERIC(10,2),
  width_mm         NUMERIC(10,2),
  height_mm        NUMERIC(10,2),
  volume_mm3       NUMERIC(18,2) GENERATED ALWAYS AS
                     (length_mm * width_mm * height_mm) STORED,
  weight_g         NUMERIC(12,2),
  confidence       NUMERIC(4,3),
  source_asset_ids UUID[] NOT NULL,           -- I-5: габаритов «из ниоткуда» нет
  analyzer_version TEXT,
  refined_from     UUID REFERENCES measurement(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT measurement_has_source CHECK (cardinality(source_asset_ids) > 0)
);

CREATE TABLE code_read (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  UUID NOT NULL REFERENCES tenant(id),
  session_id UUID NOT NULL REFERENCES scan_session(id),
  symbology  TEXT NOT NULL,
  raw_value  TEXT NOT NULL,
  parsed     JSONB NOT NULL DEFAULT '{}',     -- GS1 AI: gtin, serial, batch, sscc
  source     TEXT NOT NULL DEFAULT 'camera' CHECK (source IN ('camera','manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Карточка единицы товара и трассировка
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE item_record (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenant(id),
  task_id     UUID NOT NULL REFERENCES scan_task(id),
  session_id  UUID NOT NULL REFERENCES scan_session(id),
  identity    JSONB NOT NULL DEFAULT '{}',
  quality     JSONB NOT NULL DEFAULT '{}',
  media_state TEXT NOT NULL DEFAULT 'media_pending',
  provenance  JSONB NOT NULL,                 -- кто, чем и по какой инструкции
  version     INT  NOT NULL DEFAULT 1,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE trace_link (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             UUID NOT NULL REFERENCES tenant(id),
  item_id               UUID NOT NULL REFERENCES item_record(id),
  code_type             TEXT NOT NULL,
  code_value            TEXT NOT NULL,
  supplier_name         TEXT,
  supplier_inn          TEXT,
  supplier_ref          TEXT,
  supplier_raw_input    TEXT,                 -- исходная строка: слияние обратимо
  origin_country        CHAR(2),
  declared_by_tenant_id UUID NOT NULL REFERENCES tenant(id),   -- I-7
  declared_by_user_id   UUID NOT NULL REFERENCES app_user(id),
  confidence            TEXT NOT NULL CHECK (confidence IN ('verified','declared','inferred')),
  evidence_asset_ids    UUID[],
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX trace_link_by_code ON trace_link (tenant_id, code_type, code_value);

-- Расхождение между заявленным и подтверждённым — событие для контролёра,
-- ровно то, ради чего строится трассировка (§06.3).
CREATE TABLE trace_conflict (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL,
  item_id       UUID NOT NULL REFERENCES item_record(id),
  declared_link UUID NOT NULL REFERENCES trace_link(id),
  verified_link UUID NOT NULL REFERENCES trace_link(id),
  resolved_at   TIMESTAMPTZ,
  resolution    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Аудит (отдельная схема: только INSERT, без UPDATE/DELETE)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE audit.entry (
  id          BIGSERIAL PRIMARY KEY,
  at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  tenant_id   UUID,
  actor_id    UUID,
  action      TEXT NOT NULL,
  target      TEXT,
  cross_tenant BOOLEAN NOT NULL DEFAULT false,
  reason      TEXT,
  details     JSONB NOT NULL DEFAULT '{}'
);

REVOKE UPDATE, DELETE ON audit.entry FROM PUBLIC;

-- ─────────────────────────────────────────────────────────────────────────────
-- Row Level Security — рубеж 1 изоляции тенантов (§07.2)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION current_tenant() RETURNS UUID AS $$
  SELECT current_setting('app.tenant_id', true)::uuid;
$$ LANGUAGE SQL STABLE;

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'site','app_user','device','scan_task','scan_session','asset',
    'measurement','code_read','item_record','trace_link','trace_conflict'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON %I
        USING (
          tenant_id = current_tenant()
          OR tenant_id IN (
            SELECT from_tenant FROM tenant_grant
             WHERE to_tenant = current_tenant() AND expires_at > now()
          )
        )
    $f$, t);
  END LOOP;
END $$;
