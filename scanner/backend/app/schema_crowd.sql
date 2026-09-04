-- Краудсорсинговый контур «скан за вознаграждение» (docs/15).
-- Применяется ПОВЕРХ schema.sql (+ schema_ri.sql при наличии).
--
-- Принципы:
--   C-1  платим за ПРИНЯТЫЙ уникальный скан, а не за присланный файл —
--        приёмка та же, что у штатных сессий (auto_accept + ревью)
--   C-2  реестр вознаграждений append-only: начисление, бонус, клобэк,
--        выплата — отдельные строки; баланс = сумма, историю не правим
--   C-3  тариф — данные (reward_policy), не константа в коде
--   C-4  данные о субпоставщике от внешнего участника входят в трассировку
--        строго как confidence='declared' (I-7) до перекрёстной проверки
--   C-5  рельса выплаты — свойство батча; крипто-рельса за флагом тенанта
--        и по умолчанию выключена (юр. ограничения РФ — docs/15 §5)

CREATE TABLE contributor (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  display_name  TEXT NOT NULL,
  phone_hash    TEXT NOT NULL,                -- идентификация без хранения номера
  -- Рельсы выплат, доступные этому участнику (проверенные реквизиты)
  npd_inn       TEXT,                         -- самозанятый (НПД)
  gph_details   JSONB,                        -- реквизиты для договора ГПХ
  crypto_wallet TEXT,                         -- используется ТОЛЬКО при crypto_rail_enabled
  status        TEXT NOT NULL DEFAULT 'ACTIVE'
                  CHECK (status IN ('ACTIVE','SUSPENDED','BANNED')),
  -- Репутация: скользящая доля принятых сканов, обновляется приёмкой
  acceptance_rate NUMERIC(4,3) NOT NULL DEFAULT 1.0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, phone_hash)
);

CREATE TABLE reward_policy (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                 UUID NOT NULL REFERENCES tenant(id),
  active_from               DATE NOT NULL,
  base_scan_cents_eur       INT NOT NULL,     -- 500 = 5 € за принятый уникальный скан
  new_supplier_bonus_cents  INT NOT NULL DEFAULT 0,  -- бонус за ПОДТВЕРЖДЁННОГО нового субпоставщика
  daily_cap_scans           INT NOT NULL DEFAULT 40, -- потолок оплачиваемых сканов в день
  hourly_flag_threshold     INT NOT NULL DEFAULT 12, -- быстрее — на ручной аудит
  min_quality_score         NUMERIC(3,2) NOT NULL DEFAULT 0.6,
  audit_sample_pct          INT NOT NULL DEFAULT 10  -- случайная выборка на аудит
);

-- Кампания: управляемый кран расхода с жёстким потолком (docs/15 §3)
CREATE TABLE crowd_campaign (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenant(id),
  title            TEXT NOT NULL,
  budget_cents_eur INT  NOT NULL CHECK (budget_cents_eur >= 0),
  state            TEXT NOT NULL DEFAULT 'ACTIVE'
                     CHECK (state IN ('ACTIVE','PAUSED','CLOSED')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE crowd_submission (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  contributor_id UUID NOT NULL REFERENCES contributor(id),
  campaign_id   UUID REFERENCES crowd_campaign(id),
  session_id    UUID REFERENCES scan_session(id), -- скан идёт штатным протоколом kv_crowd
  -- Дедуп: точный по содержимому и перцептивный по кадрам
  content_sha256 TEXT NOT NULL,
  phash          TEXT,
  supplier_claim JSONB,                        -- заявленный субпоставщик (C-4: declared)
  state         TEXT NOT NULL DEFAULT 'RECEIVED' CHECK (state IN
                  ('RECEIVED','ACCEPTED','REJECTED','FLAGGED')),
  reject_reason TEXT,                          -- duplicate | quality | fraud_suspect | cap_exceeded
  submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at   TIMESTAMPTZ,
  UNIQUE (tenant_id, content_sha256)           -- один и тот же файл не платится дважды
);

CREATE INDEX crowd_submission_by_contributor
  ON crowd_submission (contributor_id, submitted_at);

-- Реестр вознаграждений: только INSERT (C-2)
CREATE TABLE bounty_ledger (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  contributor_id UUID NOT NULL REFERENCES contributor(id),
  submission_id UUID REFERENCES crowd_submission(id),
  campaign_id   UUID REFERENCES crowd_campaign(id),
  kind          TEXT NOT NULL CHECK (kind IN ('ACCRUAL','BONUS','CLAWBACK','PAYOUT')),
  amount_cents_eur INT NOT NULL,               -- знак: начисления +, клобэк/выплата −
  note          TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE UPDATE, DELETE ON bounty_ledger FROM PUBLIC;

CREATE TABLE payout_batch (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  rail          TEXT NOT NULL CHECK (rail IN ('NPD','GPH','CRYPTO')),
  fx_rate_rub_per_eur NUMERIC(10,4),           -- фиксация курса на момент батча
  status        TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','EXECUTED','CANCELLED')),
  executed_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payout_item (
  batch_id      UUID NOT NULL REFERENCES payout_batch(id),
  contributor_id UUID NOT NULL REFERENCES contributor(id),
  amount_cents_eur INT NOT NULL CHECK (amount_cents_eur > 0),
  ledger_entry_id UUID REFERENCES bounty_ledger(id), -- проставляется при EXECUTED
  -- Чек НПД: N-1 — выплата НПД без чека не существует (npd.py)
  receipt_id       TEXT,
  receipt_url      TEXT,
  amount_rub_kopecks BIGINT,
  PRIMARY KEY (batch_id, contributor_id),
  CONSTRAINT npd_payout_needs_receipt CHECK (
    receipt_id IS NOT NULL OR amount_rub_kopecks IS NULL
  )
);

-- Крипто-рельса: за явным флагом тенанта, по умолчанию ВЫКЛЮЧЕНА (C-5)
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS crypto_rail_enabled BOOLEAN NOT NULL DEFAULT false;

-- RLS — тот же рубеж, что и в schema.sql
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'contributor','reward_policy','crowd_campaign','crowd_submission',
    'bounty_ledger','payout_batch','payout_item'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    IF t <> 'payout_item' THEN
      EXECUTE format($f$
        CREATE POLICY tenant_isolation ON %I
          USING (tenant_id = current_tenant())
      $f$, t);
    ELSE
      -- payout_item наследует тенанта от батча
      EXECUTE $f$
        CREATE POLICY tenant_isolation ON payout_item
          USING (EXISTS (SELECT 1 FROM payout_batch b
                          WHERE b.id = batch_id AND b.tenant_id = current_tenant()))
      $f$;
    END IF;
  END LOOP;
END $$;
