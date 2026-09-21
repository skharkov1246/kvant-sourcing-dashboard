-- LOCAL PROPOSAL ONLY. PostgreSQL 15+ / Supabase. No external execution performed.
-- Archive v2 proposal; additive to library/supabase/schema.sql (blob edafe3f929333c3774c99e5e35deea63359de668).
-- No bucket creation, existing-table changes, default-privilege changes or credentials.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

-- Refuse name collisions or changed security instead of silently adopting tables.
DO $archive$
DECLARE n text; r regclass; p regprocedure;
BEGIN
  FOREACH n IN ARRAY ARRAY[
    'archive_sources','archive_ingestion_runs','archive_storage_objects','archive_storage_verifications',
    'archive_blobs','archive_blob_parts','archive_import_batches','archive_current_selections',
    'archive_index_auxiliary_rows','archive_objects',
    'archive_object_versions','archive_version_provenance','archive_object_links',
    'archive_attachments','archive_extraction_versions','archive_text_units',
    'archive_part_numbers','archive_facts','archive_evidence',
    'archive_processing_jobs','archive_processing_attempts'
  ] LOOP
    r := to_regclass('public.' || n);
    IF r IS NOT NULL THEN
      IF obj_description(r, 'pg_class') IS DISTINCT FROM 'archive_schema:v2' THEN
        RAISE EXCEPTION 'Unmanaged or incompatible archive relation: %', n;
      END IF;
      IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = r) THEN
        RAISE EXCEPTION 'Review existing policies before reapplying archive schema: %', n;
      END IF;
    END IF;
  END LOOP;
  FOREACH n IN ARRAY ARRAY['archive_reject_mutation','archive_validate_lineage','archive_require_evidence'] LOOP
    p := to_regprocedure('public.' || n || '()');
    IF p IS NOT NULL AND obj_description(p, 'pg_proc') IS DISTINCT FROM 'archive_schema:v2' THEN
      RAISE EXCEPTION 'Unmanaged archive function: %', n;
    END IF;
  END LOOP;
  p := to_regprocedure('public.archive_apply_import_batch(jsonb)');
  IF p IS NOT NULL AND obj_description(p, 'pg_proc') IS DISTINCT FROM 'archive_schema:v2' THEN
    RAISE EXCEPTION 'Unmanaged archive import function';
  END IF;
  FOREACH n IN ARRAY ARRAY[
    'archive_blob_parts_storage_idx','archive_current_selections_object_idx',
    'archive_object_versions_history_idx','archive_version_provenance_run_idx','archive_object_links_target_idx',
    'archive_attachments_file_idx','archive_attachments_version_idx','archive_attachments_blob_idx',
    'archive_extraction_versions_blob_key','archive_extraction_versions_source_key',
    'archive_text_units_fts_idx','archive_part_numbers_exact_idx','archive_evidence_source_idx',
    'archive_processing_jobs_ready_idx','archive_processing_jobs_lease_idx'
  ] LOOP
    r := to_regclass('public.' || n);
    IF r IS NOT NULL AND obj_description(r, 'pg_class')
      IS DISTINCT FROM 'archive_schema:v2:index:' || md5(pg_get_indexdef(r)) THEN
      RAISE EXCEPTION 'Unmanaged or changed archive index: %', n;
    END IF;
  END LOOP;
END $archive$;

CREATE TABLE IF NOT EXISTS public.archive_sources (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  system_name text NOT NULL DEFAULT 'bitrix24',
  portal text COLLATE "C" NOT NULL CHECK (portal <> '' AND portal !~ '[:/@?#[:space:]]'),
  label text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (system_name, portal)
);

CREATE TABLE IF NOT EXISTS public.archive_ingestion_runs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL REFERENCES public.archive_sources(id),
  run_key text COLLATE "C" NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'collecting' CHECK (status IN ('collecting','complete','partial','failed')),
  scope jsonb NOT NULL CHECK (jsonb_typeof(scope) = 'object'),
  coverage jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(coverage) = 'object'),
  collector_version text NOT NULL,
  error_summary jsonb,
  CHECK (finished_at IS NULL OR finished_at >= started_at),
  CHECK (status = 'collecting' OR finished_at IS NOT NULL),
  UNIQUE (source_id, run_key), UNIQUE (id, source_id)
);

-- Physical Storage objects are <=16 MiB parts or small complete originals.
-- Their registry does not, by itself, claim successful remote byte verification.
CREATE TABLE IF NOT EXISTS public.archive_storage_objects (
  sha256 text COLLATE "C" PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  bucket_name text NOT NULL CHECK (bucket_name <> '' AND bucket_name !~ '[:/?#]'),
  object_key text COLLATE "C" NOT NULL CHECK (object_key <> '' AND object_key !~ '^/|://'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 16777216),
  UNIQUE (bucket_name, object_key)
);
CREATE TABLE IF NOT EXISTS public.archive_storage_verifications (
  storage_sha256 text NOT NULL REFERENCES public.archive_storage_objects(sha256),
  receipt_sha256 text COLLATE "C" NOT NULL CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
  verified_at timestamptz NOT NULL,
  method text NOT NULL CHECK (method = 'authenticated-readback-sha256'),
  receipt jsonb NOT NULL,
  PRIMARY KEY (storage_sha256, receipt_sha256)
);

-- Logical original bytes, possibly assembled from several physical Storage parts.
-- For gzip, sha256 hashes stored compressed bytes and decoded_sha256 raw JSON.
CREATE TABLE IF NOT EXISTS public.archive_blobs (
  sha256 text COLLATE "C" PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  media_type text NOT NULL DEFAULT 'application/octet-stream',
  content_encoding text NOT NULL DEFAULT 'identity' CHECK (content_encoding IN ('identity','gzip')),
  decoded_sha256 text COLLATE "C" CHECK (decoded_sha256 ~ '^[0-9a-f]{64}$'),
  decoded_size_bytes bigint CHECK (decoded_size_bytes >= 0),
  local_verified_at timestamptz NOT NULL,
  CHECK ((decoded_sha256 IS NULL) = (decoded_size_bytes IS NULL)),
  CHECK (content_encoding <> 'gzip' OR decoded_sha256 IS NOT NULL),
  CHECK (content_encoding <> 'identity' OR decoded_sha256 IS NULL OR (decoded_sha256=sha256 AND decoded_size_bytes=size_bytes))
);
CREATE TABLE IF NOT EXISTS public.archive_blob_parts (
  blob_sha256 text NOT NULL REFERENCES public.archive_blobs(sha256),
  layout_sha256 text COLLATE "C" NOT NULL CHECK (layout_sha256 ~ '^[0-9a-f]{64}$'),
  part_index integer NOT NULL CHECK (part_index >= 0),
  storage_sha256 text NOT NULL REFERENCES public.archive_storage_objects(sha256),
  byte_offset bigint NOT NULL CHECK (byte_offset >= 0),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 16777216),
  PRIMARY KEY (blob_sha256, layout_sha256, part_index)
);
CREATE INDEX IF NOT EXISTS archive_blob_parts_storage_idx ON public.archive_blob_parts(storage_sha256);

CREATE TABLE IF NOT EXISTS public.archive_objects (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL REFERENCES public.archive_sources(id),
  entity_type text COLLATE "C" NOT NULL CHECK (entity_type <> ''),
  external_id text COLLATE "C" NOT NULL CHECK (external_id <> ''),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, entity_type, external_id), UNIQUE (id, source_id)
);

CREATE TABLE IF NOT EXISTS public.archive_object_versions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL,
  object_id bigint NOT NULL,
  payload_sha256 text COLLATE "C" NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  payload_hash_scheme text NOT NULL DEFAULT 'python-json-sort-utf8-compact:v1',
  parser_version text NOT NULL,
  payload jsonb NOT NULL,
  title text NOT NULL DEFAULT '',
  body text NOT NULL DEFAULT '',
  body_sha256 text COLLATE "C" NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
  metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
  source_created_at timestamptz,
  source_updated_at timestamptz,
  observed_at timestamptz NOT NULL,
  raw_blob_sha256 text NOT NULL,
  raw_json_pointer text NOT NULL CHECK (raw_json_pointer = '' OR left(raw_json_pointer,1) = '/'),
  FOREIGN KEY (object_id, source_id) REFERENCES public.archive_objects(id, source_id),
  FOREIGN KEY (raw_blob_sha256) REFERENCES public.archive_blobs(sha256),
  UNIQUE (object_id, payload_sha256, payload_hash_scheme, parser_version),
  UNIQUE (id, source_id)
);
CREATE INDEX IF NOT EXISTS archive_object_versions_history_idx
  ON public.archive_object_versions (object_id, source_updated_at DESC, observed_at DESC);

CREATE TABLE IF NOT EXISTS public.archive_version_provenance (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL,
  version_id bigint NOT NULL,
  run_id bigint NOT NULL,
  raw_blob_sha256 text NOT NULL REFERENCES public.archive_blobs(sha256),
  json_pointer text NOT NULL CHECK (json_pointer = '' OR left(json_pointer,1) = '/'),
  source_relative_path text NOT NULL,
  observed_at timestamptz NOT NULL,
  FOREIGN KEY (version_id, source_id) REFERENCES public.archive_object_versions(id, source_id),
  FOREIGN KEY (run_id, source_id) REFERENCES public.archive_ingestion_runs(id, source_id),
  UNIQUE (version_id, run_id, raw_blob_sha256, source_relative_path, json_pointer)
);

CREATE INDEX IF NOT EXISTS archive_version_provenance_run_idx ON public.archive_version_provenance(run_id,version_id);

-- Versioned CRM bindings / task links / mail thread membership. Targets may be
-- placeholders whose body has not been obtained; absence of a version is visible.
CREATE TABLE IF NOT EXISTS public.archive_object_links (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL,
  origin_version_id bigint NOT NULL,
  target_source_id bigint NOT NULL,
  target_object_id bigint NOT NULL,
  relationship text NOT NULL,
  origin_json_pointer text NOT NULL,
  FOREIGN KEY (origin_version_id, source_id) REFERENCES public.archive_object_versions(id, source_id),
  FOREIGN KEY (target_object_id, target_source_id) REFERENCES public.archive_objects(id, source_id),
  UNIQUE (origin_version_id, target_object_id, relationship, origin_json_pointer)
);
CREATE INDEX IF NOT EXISTS archive_object_links_target_idx
  ON public.archive_object_links (target_object_id, relationship);

-- Append-only attachment OBSERVATIONS. An unresolved reference is kept; a later
-- resolved observation links the bytes and may supersede the earlier observation.
-- Several file IDs and several source objects may refer to the same blob.
CREATE TABLE IF NOT EXISTS public.archive_attachments (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL,
  source_version_id bigint NOT NULL,
  file_namespace text COLLATE "C" NOT NULL,
  file_external_id text COLLATE "C" NOT NULL CHECK (file_external_id <> ''),
  field_path text NOT NULL,
  file_name text,
  source_size_bytes bigint CHECK (source_size_bytes >= 0),
  resolution_state text NOT NULL CHECK (resolution_state IN ('unresolved','resolved','unavailable')),
  blob_sha256 text REFERENCES public.archive_blobs(sha256),
  resolution_reason text,
  observation_sha256 text COLLATE "C" NOT NULL CHECK (observation_sha256 ~ '^[0-9a-f]{64}$'),
  observed_at timestamptz NOT NULL,
  supersedes_attachment_id bigint,
  FOREIGN KEY (source_version_id, source_id) REFERENCES public.archive_object_versions(id, source_id),
  FOREIGN KEY (supersedes_attachment_id, source_id) REFERENCES public.archive_attachments(id, source_id),
  CHECK ((resolution_state = 'resolved') = (blob_sha256 IS NOT NULL)),
  CHECK (supersedes_attachment_id IS NULL OR supersedes_attachment_id < id),
  UNIQUE (source_id, observation_sha256), UNIQUE (id, source_id)
);
CREATE INDEX IF NOT EXISTS archive_attachments_file_idx
  ON public.archive_attachments (source_id, file_namespace, file_external_id);
CREATE INDEX IF NOT EXISTS archive_attachments_version_idx
  ON public.archive_attachments (source_version_id);
CREATE INDEX IF NOT EXISTS archive_attachments_blob_idx
  ON public.archive_attachments (blob_sha256) WHERE blob_sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.archive_extraction_versions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  input_blob_sha256 text REFERENCES public.archive_blobs(sha256),
  input_source_version_id bigint REFERENCES public.archive_object_versions(id),
  extractor_name text NOT NULL,
  extractor_version text NOT NULL,
  config_sha256 text COLLATE "C" NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  output_sha256 text COLLATE "C" NOT NULL CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
  output_blob_sha256 text REFERENCES public.archive_blobs(sha256),
  status text NOT NULL CHECK (status IN ('complete','partial','empty','unsupported','needs_ocr','failed')),
  details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(input_blob_sha256, input_source_version_id) = 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS archive_extraction_versions_blob_key
  ON public.archive_extraction_versions
  (input_blob_sha256, extractor_name, extractor_version, config_sha256, output_sha256)
  WHERE input_blob_sha256 IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS archive_extraction_versions_source_key
  ON public.archive_extraction_versions
  (input_source_version_id, extractor_name, extractor_version, config_sha256, output_sha256)
  WHERE input_source_version_id IS NOT NULL;

-- Full bodies stay in object_versions. Index the COMPLETE text in bounded units,
-- avoiding the PostgreSQL tsvector size limit for large emails/documents.
CREATE TABLE IF NOT EXISTS public.archive_text_units (
  extraction_id bigint NOT NULL REFERENCES public.archive_extraction_versions(id),
  unit_key text COLLATE "C" NOT NULL,
  unit_kind text NOT NULL CHECK (unit_kind IN ('body','page','sheet','cell','paragraph','table_row','ocr_region','archive_member','other')),
  locator jsonb NOT NULL CHECK (jsonb_typeof(locator) = 'object'),
  text_content text NOT NULL CHECK (char_length(text_content) <= 16000),
  text_sha256 text COLLATE "C" NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
  fts tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, text_content)) STORED,
  PRIMARY KEY (extraction_id, unit_key)
);
CREATE INDEX IF NOT EXISTS archive_text_units_fts_idx ON public.archive_text_units USING gin (fts);

CREATE TABLE IF NOT EXISTS public.archive_part_numbers (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  extraction_id bigint NOT NULL,
  unit_key text COLLATE "C" NOT NULL,
  exact_value text COLLATE "C" NOT NULL CHECK (exact_value <> ''),
  char_start integer NOT NULL CHECK (char_start >= 0),
  char_end integer NOT NULL CHECK (char_end > char_start),
  method text NOT NULL,
  method_version text NOT NULL,
  confidence numeric CHECK (confidence BETWEEN 0 AND 1),
  FOREIGN KEY (extraction_id, unit_key) REFERENCES public.archive_text_units(extraction_id, unit_key),
  UNIQUE (extraction_id, unit_key, char_start, char_end, method, method_version)
);
CREATE INDEX IF NOT EXISTS archive_part_numbers_exact_idx
  ON public.archive_part_numbers USING hash (exact_value COLLATE "C");

CREATE TABLE IF NOT EXISTS public.archive_facts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL,
  subject_object_id bigint NOT NULL,
  fact_key text COLLATE "C" NOT NULL,
  value jsonb,
  assessment text NOT NULL CHECK (assessment IN ('confirmed','inferred','unknown')),
  method text NOT NULL,
  method_version text NOT NULL,
  confidence numeric CHECK (confidence BETWEEN 0 AND 1),
  assertion_sha256 text COLLATE "C" NOT NULL CHECK (assertion_sha256 ~ '^[0-9a-f]{64}$'),
  rationale text,
  supersedes_fact_id bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (subject_object_id, source_id) REFERENCES public.archive_objects(id, source_id),
  FOREIGN KEY (supersedes_fact_id, subject_object_id, fact_key)
    REFERENCES public.archive_facts(id, subject_object_id, fact_key),
  CHECK (assessment = 'unknown' OR (value IS NOT NULL AND value <> 'null'::jsonb)),
  CHECK (supersedes_fact_id IS NULL OR supersedes_fact_id < id),
  UNIQUE (subject_object_id, fact_key, assertion_sha256),
  UNIQUE (id, source_id), UNIQUE (id, subject_object_id, fact_key)
);

-- A fact may have many evidence rows. Evidence can cite a payload field directly,
-- or a text unit. source_version_id always identifies the historical CRM context.
CREATE TABLE IF NOT EXISTS public.archive_evidence (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id bigint NOT NULL,
  fact_source_id bigint NOT NULL,
  fact_id bigint NOT NULL,
  source_version_id bigint NOT NULL,
  extraction_id bigint,
  unit_key text COLLATE "C",
  locator jsonb NOT NULL CHECK (jsonb_typeof(locator) = 'object'),
  quoted_text text NOT NULL CHECK (btrim(quoted_text) <> ''),
  role text NOT NULL CHECK (role IN ('supports','contradicts','context')),
  evidence_sha256 text COLLATE "C" NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (fact_id, fact_source_id) REFERENCES public.archive_facts(id, source_id),
  FOREIGN KEY (source_version_id, source_id) REFERENCES public.archive_object_versions(id, source_id),
  FOREIGN KEY (extraction_id, unit_key) REFERENCES public.archive_text_units(extraction_id, unit_key),
  CHECK ((extraction_id IS NULL) = (unit_key IS NULL)),
  CHECK (locator ? 'char_start' AND locator ? 'char_end'),
  CHECK (extraction_id IS NOT NULL OR (locator ? 'json_pointer'
    AND jsonb_typeof(locator -> 'json_pointer') = 'string')),
  UNIQUE (fact_id, evidence_sha256)
);
CREATE INDEX IF NOT EXISTS archive_evidence_source_idx ON public.archive_evidence (source_version_id);

CREATE TABLE IF NOT EXISTS public.archive_processing_jobs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job_kind text NOT NULL CHECK (job_kind IN ('download','extract','index','review')),
  attachment_id bigint REFERENCES public.archive_attachments(id),
  blob_sha256 text REFERENCES public.archive_blobs(sha256),
  source_version_id bigint REFERENCES public.archive_object_versions(id),
  extraction_id bigint REFERENCES public.archive_extraction_versions(id),
  fact_id bigint REFERENCES public.archive_facts(id),
  target_key text GENERATED ALWAYS AS (CASE
    WHEN attachment_id IS NOT NULL THEN 'attachment:' || attachment_id::text
    WHEN blob_sha256 IS NOT NULL THEN 'blob:' || blob_sha256
    WHEN source_version_id IS NOT NULL THEN 'version:' || source_version_id::text
    WHEN extraction_id IS NOT NULL THEN 'extraction:' || extraction_id::text
    ELSE 'fact:' || fact_id::text END) STORED,
  processor_name text NOT NULL,
  processor_version text NOT NULL,
  config_sha256 text COLLATE "C" NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','retry','succeeded','blocked','failed')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_token uuid,
  lease_expires_at timestamptz,
  attempt_started_at timestamptz,
  last_error jsonb,
  result_refs jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(result_refs) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(attachment_id, blob_sha256, source_version_id, extraction_id, fact_id) = 1),
  CHECK ((job_kind = 'download' AND attachment_id IS NOT NULL)
      OR (job_kind = 'extract' AND (blob_sha256 IS NOT NULL OR source_version_id IS NOT NULL))
      OR (job_kind = 'index' AND extraction_id IS NOT NULL)
      OR (job_kind = 'review' AND (fact_id IS NOT NULL OR source_version_id IS NOT NULL))),
  CHECK ((status = 'running') = (lease_token IS NOT NULL)),
  CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL)),
  CHECK ((lease_token IS NULL) = (attempt_started_at IS NULL)),
  UNIQUE (job_kind, target_key, processor_name, processor_version, config_sha256)
);
CREATE INDEX IF NOT EXISTS archive_processing_jobs_ready_idx
  ON public.archive_processing_jobs (next_attempt_at, id) WHERE status IN ('queued','retry');
CREATE INDEX IF NOT EXISTS archive_processing_jobs_lease_idx
  ON public.archive_processing_jobs (lease_expires_at) WHERE status = 'running';

-- Finalized attempts are append-only. The active attempt lives in the leased job.
CREATE TABLE IF NOT EXISTS public.archive_processing_attempts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job_id bigint NOT NULL REFERENCES public.archive_processing_jobs(id),
  attempt_number integer NOT NULL CHECK (attempt_number > 0),
  lease_token uuid NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz NOT NULL CHECK (finished_at >= started_at),
  outcome text NOT NULL CHECK (outcome IN ('succeeded','retry','blocked','failed','lease_expired')),
  error jsonb,
  result_refs jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(result_refs) = 'object'),
  UNIQUE (job_id, attempt_number), UNIQUE (job_id, lease_token)
);

-- Each COPY batch is one transaction; its receipt is inserted only at the end.
CREATE TABLE IF NOT EXISTS public.archive_import_batches (
  batch_sha256 text COLLATE "C" PRIMARY KEY CHECK (batch_sha256 ~ '^[0-9a-f]{64}$'),
  run_id bigint NOT NULL REFERENCES public.archive_ingestion_runs(id),
  importer_version text NOT NULL,
  row_count integer NOT NULL CHECK (row_count > 0),
  completed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.archive_current_selections (
  run_id bigint NOT NULL,
  source_id bigint NOT NULL,
  object_id bigint NOT NULL,
  version_id bigint NOT NULL,
  selection_key text NOT NULL,
  selection_metadata jsonb NOT NULL,
  FOREIGN KEY (run_id, source_id) REFERENCES public.archive_ingestion_runs(id, source_id),
  FOREIGN KEY (object_id, source_id) REFERENCES public.archive_objects(id, source_id),
  FOREIGN KEY (version_id, source_id) REFERENCES public.archive_object_versions(id, source_id),
  PRIMARY KEY (run_id, selection_key)
);
CREATE INDEX IF NOT EXISTS archive_current_selections_object_idx ON public.archive_current_selections(object_id, run_id DESC);
-- Preserve remaining SQLite service rows, including current-only manifest links.
CREATE TABLE IF NOT EXISTS public.archive_index_auxiliary_rows (
  run_id bigint NOT NULL REFERENCES public.archive_ingestion_runs(id),
  table_name text NOT NULL,
  row_sha256 text COLLATE "C" NOT NULL CHECK (row_sha256 ~ '^[0-9a-f]{64}$'),
  row_payload jsonb NOT NULL,
  PRIMARY KEY (run_id, table_name, row_sha256)
);

CREATE OR REPLACE FUNCTION public.archive_reject_mutation()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $archive$
BEGIN
  RAISE EXCEPTION 'Archive history is append-only: %.% %', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END $archive$;
COMMENT ON FUNCTION public.archive_reject_mutation() IS 'archive_schema:v2';
REVOKE ALL ON FUNCTION public.archive_reject_mutation() FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.archive_validate_lineage()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $archive$
DECLARE old_attachment public.archive_attachments%ROWTYPE;
        input_version bigint; input_blob text; unit_text text; pointer_parts text[];
        first_char integer; final_char integer;
BEGIN
  IF TG_TABLE_NAME = 'archive_attachments' THEN
   IF NEW.supersedes_attachment_id IS NOT NULL THEN
    SELECT * INTO old_attachment FROM public.archive_attachments WHERE id = NEW.supersedes_attachment_id;
    IF NOT FOUND OR ROW(old_attachment.source_id, old_attachment.source_version_id,
        old_attachment.file_namespace, old_attachment.file_external_id, old_attachment.field_path)
      IS DISTINCT FROM ROW(NEW.source_id, NEW.source_version_id,
        NEW.file_namespace, NEW.file_external_id, NEW.field_path) THEN
      RAISE EXCEPTION 'Attachment supersession must preserve the source reference';
    END IF;
   END IF;
  ELSIF TG_TABLE_NAME = 'archive_evidence' THEN
   IF NEW.extraction_id IS NOT NULL THEN
    SELECT input_source_version_id, input_blob_sha256 INTO input_version, input_blob
      FROM public.archive_extraction_versions WHERE id = NEW.extraction_id;
    IF NOT FOUND OR NOT (coalesce(input_version = NEW.source_version_id, false)
      OR (input_blob IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.archive_attachments
        WHERE source_version_id = NEW.source_version_id AND blob_sha256 = input_blob))) THEN
      RAISE EXCEPTION 'Evidence extraction does not belong to the cited source version';
    END IF;
    SELECT text_content INTO unit_text FROM public.archive_text_units
      WHERE extraction_id = NEW.extraction_id AND unit_key = NEW.unit_key;
   ELSE
    IF NEW.locator ->> 'json_pointer' = '' THEN
      pointer_parts := ARRAY[]::text[];
    ELSIF NEW.locator ->> 'json_pointer' = '/' THEN
      pointer_parts := ARRAY['']::text[];
    ELSIF left(NEW.locator ->> 'json_pointer', 1) = '/' THEN
      SELECT array_agg(replace(replace(part, '~1', '/'), '~0', '~') ORDER BY ord)
        INTO pointer_parts
        FROM unnest(string_to_array(substring(NEW.locator ->> 'json_pointer' FROM 2), '/'))
          WITH ORDINALITY AS p(part, ord);
    ELSE
      RAISE EXCEPTION 'Evidence requires an RFC 6901 JSON pointer';
    END IF;
    SELECT payload #>> pointer_parts INTO unit_text FROM public.archive_object_versions
      WHERE id = NEW.source_version_id;
   END IF;
   first_char := (NEW.locator ->> 'char_start')::integer;
   final_char := (NEW.locator ->> 'char_end')::integer;
   IF unit_text IS NULL OR first_char IS NULL OR final_char IS NULL
      OR first_char < 0 OR final_char <= first_char OR final_char > char_length(unit_text)
      OR substring(unit_text FROM first_char + 1 FOR final_char - first_char) COLLATE "C"
        IS DISTINCT FROM NEW.quoted_text COLLATE "C" THEN
     RAISE EXCEPTION 'Evidence quote must exactly match the cited text span';
   END IF;
  ELSIF TG_TABLE_NAME = 'archive_part_numbers' THEN
    SELECT text_content INTO unit_text FROM public.archive_text_units
      WHERE extraction_id = NEW.extraction_id AND unit_key = NEW.unit_key;
    IF NOT FOUND OR NEW.char_end > char_length(unit_text) OR
      substring(unit_text FROM NEW.char_start + 1 FOR NEW.char_end - NEW.char_start) COLLATE "C"
        IS DISTINCT FROM NEW.exact_value COLLATE "C" THEN
      RAISE EXCEPTION 'Part number must exactly match its original text span';
    END IF;
  END IF;
  RETURN NEW;
END $archive$;
COMMENT ON FUNCTION public.archive_validate_lineage() IS 'archive_schema:v2';
REVOKE ALL ON FUNCTION public.archive_validate_lineage() FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.archive_require_evidence()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $archive$
BEGIN
  IF NEW.assessment = 'confirmed' AND NOT EXISTS (
    SELECT 1 FROM public.archive_evidence WHERE fact_id = NEW.id AND role = 'supports') THEN
    RAISE EXCEPTION 'Confirmed fact requires supporting evidence in the same transaction';
  END IF;
  RETURN NEW;
END $archive$;
COMMENT ON FUNCTION public.archive_require_evidence() IS 'archive_schema:v2';
REVOKE ALL ON FUNCTION public.archive_require_evidence() FROM PUBLIC, anon, authenticated, service_role;

DO $archive$
DECLARE n text;
BEGIN
  FOREACH n IN ARRAY ARRAY['archive_attachments','archive_evidence','archive_part_numbers'] LOOP
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass('public.' || n)
      AND tgname = 'archive_lineage' AND (tgfoid <> 'public.archive_validate_lineage()'::regprocedure
      OR tgtype <> 7 OR tgenabled <> 'O')) THEN
      RAISE EXCEPTION 'Incompatible archive_lineage trigger on %', n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass('public.' || n)
                   AND tgname = 'archive_lineage') THEN
      EXECUTE format('CREATE TRIGGER archive_lineage BEFORE INSERT ON public.%I '
                     'FOR EACH ROW EXECUTE FUNCTION public.archive_validate_lineage()', n);
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'public.archive_facts'::regclass
    AND tgname = 'archive_fact_evidence_required' AND
      (tgfoid <> 'public.archive_require_evidence()'::regprocedure OR tgtype <> 5
       OR tgenabled <> 'O' OR NOT tgdeferrable OR NOT tginitdeferred)) THEN
    RAISE EXCEPTION 'Incompatible archive_fact_evidence_required trigger';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'public.archive_facts'::regclass
                 AND tgname = 'archive_fact_evidence_required') THEN
    CREATE CONSTRAINT TRIGGER archive_fact_evidence_required
      AFTER INSERT ON public.archive_facts DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION public.archive_require_evidence();
  END IF;
END $archive$;

-- Only these named tables/sequences are touched. No lib_*, storage.*, schema
-- grants, default privileges, public endpoints, or application policies change.
DO $archive$
DECLARE n text; seq_name text; immutable boolean;
BEGIN
  FOREACH n IN ARRAY ARRAY[
    'archive_sources','archive_ingestion_runs','archive_storage_objects','archive_storage_verifications',
    'archive_blobs','archive_blob_parts','archive_import_batches','archive_current_selections',
    'archive_index_auxiliary_rows','archive_objects',
    'archive_object_versions','archive_version_provenance','archive_object_links',
    'archive_attachments','archive_extraction_versions','archive_text_units',
    'archive_part_numbers','archive_facts','archive_evidence',
    'archive_processing_jobs','archive_processing_attempts'
  ] LOOP
    immutable := n <> ALL (ARRAY['archive_sources','archive_ingestion_runs','archive_processing_jobs']);
    EXECUTE format('COMMENT ON TABLE public.%I IS %L', n, 'archive_schema:v2');
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', n);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', n);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated, service_role', n);
    EXECUTE format('GRANT SELECT, INSERT ON TABLE public.%I TO service_role', n);
    IF NOT immutable THEN
      IF n = 'archive_sources' THEN
        GRANT UPDATE (label) ON public.archive_sources TO service_role;
      ELSIF n = 'archive_ingestion_runs' THEN
        GRANT UPDATE (finished_at, status, coverage, error_summary)
          ON public.archive_ingestion_runs TO service_role;
      ELSE
        GRANT UPDATE (status, attempts, max_attempts, next_attempt_at, lease_token,
          lease_expires_at, attempt_started_at, last_error, result_refs, updated_at)
          ON public.archive_processing_jobs TO service_role;
      END IF;
    ELSE
      IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass('public.' || n)
        AND tgname IN ('archive_immutable_row','archive_immutable_truncate')
        AND (tgfoid <> 'public.archive_reject_mutation()'::regprocedure OR tgenabled <> 'O'
             OR (tgname = 'archive_immutable_row' AND tgtype <> 27)
             OR (tgname = 'archive_immutable_truncate' AND tgtype <> 34))) THEN
        RAISE EXCEPTION 'Incompatible immutability trigger on %', n;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass('public.' || n)
                     AND tgname = 'archive_immutable_row') THEN
        EXECUTE format('CREATE TRIGGER archive_immutable_row BEFORE UPDATE OR DELETE ON public.%I '
                       'FOR EACH ROW EXECUTE FUNCTION public.archive_reject_mutation()', n);
      END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass('public.' || n)
                     AND tgname = 'archive_immutable_truncate') THEN
        EXECUTE format('CREATE TRIGGER archive_immutable_truncate BEFORE TRUNCATE ON public.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION public.archive_reject_mutation()', n);
      END IF;
    END IF;
    SELECT pg_get_serial_sequence('public.' || n, 'id') INTO seq_name
      WHERE EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass('public.' || n)
                    AND attname = 'id' AND NOT attisdropped);
    IF seq_name IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC, anon, authenticated, service_role', seq_name);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO service_role', seq_name);
    END IF;
  END LOOP;
  FOREACH n IN ARRAY ARRAY[
    'archive_blob_parts_storage_idx','archive_current_selections_object_idx',
    'archive_object_versions_history_idx','archive_version_provenance_run_idx','archive_object_links_target_idx',
    'archive_attachments_file_idx','archive_attachments_version_idx','archive_attachments_blob_idx',
    'archive_extraction_versions_blob_key','archive_extraction_versions_source_key',
    'archive_text_units_fts_idx','archive_part_numbers_exact_idx','archive_evidence_source_idx',
    'archive_processing_jobs_ready_idx','archive_processing_jobs_lease_idx'
  ] LOOP
    EXECUTE format('COMMENT ON INDEX public.%I IS %L', n,
      'archive_schema:v2:index:' || md5(pg_get_indexdef(to_regclass('public.' || n))));
  END LOOP;
END $archive$;
-- Restricted archive import RPC. Receives canonical JSON TEXT plus its UTF-8 hash;
-- jsonb::text is never used to reproduce Python serialization hashes.
CREATE OR REPLACE FUNCTION public.archive_apply_import_batch(batch jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $import$
DECLARE ctx jsonb; stage_row record; d jsonb; k text; source_key bigint; import_run_id bigint;
  import_object_id bigint; version_key bigint; target_key bigint; extraction_key bigint;
  ref jsonb; part jsonb; candidate jsonb; existing record; batch_hash text;
  expected_rows bigint; actual_rows bigint; byte_total bigint; part_count bigint;
BEGIN
  IF jsonb_typeof(batch) IS DISTINCT FROM 'array' OR jsonb_array_length(batch)<2 OR jsonb_array_length(batch)>501 OR octet_length(batch::text)>12582912 THEN RAISE EXCEPTION 'Invalid bounded archive batch'; END IF;
  IF EXISTS (SELECT 1 FROM jsonb_to_recordset(batch) AS staged(seq integer,row_sha256 text,json_text text)
    WHERE seq IS NULL OR row_sha256 IS NULL OR json_text IS NULL OR octet_length(json_text)>8388608) THEN RAISE EXCEPTION 'Invalid archive batch row'; END IF;
  IF (SELECT count(DISTINCT seq)<>count(*) OR min(seq)<>0 OR max(seq)<>count(*)-1 FROM jsonb_to_recordset(batch) AS staged(seq integer,row_sha256 text,json_text text)) THEN RAISE EXCEPTION 'Archive batch sequence is incomplete'; END IF;
  PERFORM pg_advisory_xact_lock(764937291); -- Serialize archive import batches.
  IF EXISTS (SELECT 1 FROM jsonb_to_recordset(batch) AS staged(seq integer,row_sha256 text,json_text text) WHERE
    row_sha256 <> encode(sha256(convert_to(json_text,'UTF8')),'hex')) THEN
    RAISE EXCEPTION 'Import row checksum mismatch';
  END IF;
  SELECT json_text::jsonb -> 'data' INTO ctx FROM jsonb_to_recordset(batch) AS staged(seq integer,row_sha256 text,json_text text) WHERE seq=0;
  IF ctx IS NULL OR (ctx->>'importer_version') IS DISTINCT FROM 'corpus-normalized-export:v1'
     OR (ctx->>'portal') IS DISTINCT FROM 'kvantpro.bitrix24.ru' THEN
    RAISE EXCEPTION 'Unapproved import context';
  END IF;
  SELECT encode(sha256(convert_to(string_agg(row_sha256,'' ORDER BY seq),'UTF8')),'hex'),count(*)
    INTO batch_hash,expected_rows FROM jsonb_to_recordset(batch) AS staged(seq integer,row_sha256 text,json_text text);
  IF EXISTS (SELECT 1 FROM public.archive_import_batches WHERE batch_sha256=batch_hash) THEN
    RETURN jsonb_build_object('batch_sha256',batch_hash,'status','already_committed','rows',expected_rows);
  END IF;
  INSERT INTO public.archive_sources(system_name,portal,label)
    VALUES('bitrix24',ctx->>'portal','Private sales archive') ON CONFLICT DO NOTHING;
  SELECT id INTO source_key FROM public.archive_sources WHERE system_name='bitrix24' AND portal=ctx->>'portal';
  INSERT INTO public.archive_ingestion_runs(source_id,run_key,started_at,status,scope,collector_version)
    VALUES(source_key,ctx->>'run_key',(ctx->>'observed_at')::timestamptz,'collecting',ctx->'scope',ctx->>'importer_version')
    ON CONFLICT DO NOTHING;
  SELECT r.id,r.scope INTO existing FROM public.archive_ingestion_runs r WHERE r.source_id=source_key AND r.run_key=ctx->>'run_key';
  import_run_id:=existing.id;
  IF existing.scope IS DISTINCT FROM ctx->'scope' THEN RAISE EXCEPTION 'Import scope conflict'; END IF;

  FOR stage_row IN SELECT json_text::jsonb AS envelope FROM jsonb_to_recordset(batch) AS staged(seq integer,row_sha256 text,json_text text) WHERE seq>0 ORDER BY seq LOOP
    d:=stage_row.envelope->'data'; k:=stage_row.envelope->>'kind';
    IF k='storage_object' THEN
      INSERT INTO public.archive_storage_objects(sha256,bucket_name,object_key,size_bytes)
        VALUES(d->>'sha256',d->>'bucket_name',d->>'object_key',(d->>'size_bytes')::bigint) ON CONFLICT DO NOTHING;
      IF NOT EXISTS (SELECT 1 FROM public.archive_storage_objects WHERE sha256=d->>'sha256'
        AND bucket_name=d->>'bucket_name' AND object_key=d->>'object_key' AND size_bytes=(d->>'size_bytes')::bigint)
        THEN RAISE EXCEPTION 'Physical Storage identity conflict'; END IF;
    ELSIF k='storage_verification' THEN
      IF d->>'receipt_sha256' <> encode(sha256(convert_to(d->>'receipt_text','UTF8')),'hex')
        OR (d->>'receipt_text')::jsonb->>'status' <> 'verified' THEN
        RAISE EXCEPTION 'Invalid Storage verification receipt'; END IF;
      IF NOT EXISTS (SELECT 1 FROM public.archive_storage_objects s
        WHERE s.sha256=d->>'storage_sha256' AND s.sha256=(d->>'receipt_text')::jsonb->>'sha256'
        AND s.size_bytes=((d->>'receipt_text')::jsonb->>'bytes')::bigint
        AND s.bucket_name=(d->>'receipt_text')::jsonb->>'bucket'
        AND s.object_key=(d->>'receipt_text')::jsonb->>'object_key') THEN RAISE EXCEPTION 'Storage receipt identity mismatch'; END IF;
      INSERT INTO public.archive_storage_verifications(storage_sha256,receipt_sha256,verified_at,method,receipt)
        VALUES(d->>'storage_sha256',d->>'receipt_sha256',(d->>'verified_at')::timestamptz,
          'authenticated-readback-sha256',(d->>'receipt_text')::jsonb) ON CONFLICT DO NOTHING;
    ELSIF k='blob' THEN
      IF d->>'layout_sha256' <> encode(sha256(convert_to(d->>'layout_text','UTF8')),'hex') THEN
        RAISE EXCEPTION 'Storage layout checksum mismatch'; END IF;
      INSERT INTO public.archive_blobs(sha256,size_bytes,media_type,content_encoding,decoded_sha256,decoded_size_bytes,local_verified_at)
        VALUES(d->>'sha256',(d->>'size_bytes')::bigint,d->>'media_type',d->>'content_encoding',
          d->>'decoded_sha256',(d->>'decoded_size_bytes')::bigint,(ctx->>'local_verified_at')::timestamptz) ON CONFLICT DO NOTHING;
      IF NOT EXISTS (SELECT 1 FROM public.archive_blobs WHERE sha256=d->>'sha256'
        AND size_bytes=(d->>'size_bytes')::bigint AND content_encoding=d->>'content_encoding'
        AND decoded_sha256 IS NOT DISTINCT FROM d->>'decoded_sha256'
        AND decoded_size_bytes IS NOT DISTINCT FROM (d->>'decoded_size_bytes')::bigint)
        THEN RAISE EXCEPTION 'Logical blob identity conflict'; END IF;
      FOR part IN SELECT value FROM jsonb_array_elements((d->>'layout_text')::jsonb) LOOP
        INSERT INTO public.archive_blob_parts(blob_sha256,layout_sha256,part_index,storage_sha256,byte_offset,size_bytes)
          VALUES(d->>'sha256',d->>'layout_sha256',(part->>'index')::integer,part->>'sha256',
            (part->>'offset')::bigint,(part->>'bytes')::bigint) ON CONFLICT DO NOTHING;
        IF NOT EXISTS (SELECT 1 FROM public.archive_blob_parts WHERE blob_sha256=d->>'sha256'
          AND layout_sha256=d->>'layout_sha256' AND part_index=(part->>'index')::integer
          AND storage_sha256=part->>'sha256' AND byte_offset=(part->>'offset')::bigint AND size_bytes=(part->>'bytes')::bigint)
          THEN RAISE EXCEPTION 'Storage part conflict'; END IF;
      END LOOP;
      SELECT count(*),sum(size_bytes) INTO part_count,byte_total FROM public.archive_blob_parts
        WHERE blob_sha256=d->>'sha256' AND layout_sha256=d->>'layout_sha256';
      IF part_count<>jsonb_array_length((d->>'layout_text')::jsonb) OR byte_total<>(d->>'size_bytes')::bigint
        OR EXISTS (SELECT 1 FROM (
          SELECT p.*,row_number() OVER(ORDER BY p.part_index)-1 expected_index,
            coalesce(sum(p.size_bytes) OVER(ORDER BY p.part_index ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0) expected_offset
          FROM public.archive_blob_parts p WHERE p.blob_sha256=d->>'sha256' AND p.layout_sha256=d->>'layout_sha256'
        ) ordered JOIN public.archive_storage_objects s ON s.sha256=ordered.storage_sha256
        WHERE ordered.part_index<>ordered.expected_index OR ordered.byte_offset<>ordered.expected_offset OR ordered.size_bytes<>s.size_bytes)
        THEN RAISE EXCEPTION 'Storage layout is incomplete or unordered'; END IF;
    ELSIF k='version' THEN
      IF d->>'payload_sha256'<>encode(sha256(convert_to(d->>'payload_text','UTF8')),'hex') OR
         d->>'body_sha256'<>encode(sha256(convert_to(d->>'body','UTF8')),'hex') THEN
        RAISE EXCEPTION 'Version payload or body checksum mismatch'; END IF;
      INSERT INTO public.archive_objects(source_id,entity_type,external_id)
        VALUES(source_key,d->>'entity_type',d->>'external_id') ON CONFLICT DO NOTHING;
      SELECT id INTO import_object_id FROM public.archive_objects WHERE source_id=source_key AND entity_type=d->>'entity_type' AND external_id=d->>'external_id';
      INSERT INTO public.archive_object_versions(source_id,object_id,payload_sha256,parser_version,payload,title,body,body_sha256,metadata,
        source_created_at,source_updated_at,observed_at,raw_blob_sha256,raw_json_pointer)
      VALUES(source_key,import_object_id,d->>'payload_sha256',d->>'parser_version',(d->>'payload_text')::jsonb,d->>'title',d->>'body',d->>'body_sha256',d->'metadata',
        (d->>'created_at')::timestamptz,(d->>'updated_at')::timestamptz,(d->>'observed_at')::timestamptz,d->>'raw_blob_sha256',d->>'raw_json_pointer') ON CONFLICT DO NOTHING;
      SELECT * INTO existing FROM public.archive_object_versions WHERE object_id=import_object_id AND payload_sha256=d->>'payload_sha256'
        AND parser_version=d->>'parser_version' AND payload_hash_scheme='python-json-sort-utf8-compact:v1';
      version_key:=existing.id;
      IF version_key IS NULL OR existing.body_sha256<>d->>'body_sha256' OR existing.title<>d->>'title'
        OR existing.metadata IS DISTINCT FROM d->'metadata' OR existing.payload IS DISTINCT FROM (d->>'payload_text')::jsonb THEN
        RAISE EXCEPTION 'Immutable normalized version conflict'; END IF;
      FOR ref IN SELECT value FROM jsonb_array_elements(d->'provenance') LOOP
        INSERT INTO public.archive_version_provenance(source_id,version_id,run_id,raw_blob_sha256,json_pointer,source_relative_path,observed_at)
          VALUES(source_key,version_key,import_run_id,ref->>'raw_blob_sha256',ref->>'json_pointer',ref->>'source_relative_path',(ref->>'observed_at')::timestamptz)
          ON CONFLICT DO NOTHING;
      END LOOP;
      FOR ref IN SELECT value FROM jsonb_array_elements(d->'links') LOOP
        INSERT INTO public.archive_objects(source_id,entity_type,external_id)
          VALUES(source_key,ref->>'target_type',ref->>'target_id') ON CONFLICT DO NOTHING;
        SELECT id INTO target_key FROM public.archive_objects WHERE source_id=source_key AND entity_type=ref->>'target_type' AND external_id=ref->>'target_id';
        INSERT INTO public.archive_object_links(source_id,origin_version_id,target_source_id,target_object_id,relationship,origin_json_pointer)
          VALUES(source_key,version_key,source_key,target_key,ref->>'relationship',d->>'raw_json_pointer') ON CONFLICT DO NOTHING;
      END LOOP;
      INSERT INTO public.archive_extraction_versions(input_source_version_id,extractor_name,extractor_version,config_sha256,output_sha256,status,details)
        VALUES(version_key,'corpus-plain-text',d->>'parser_version',ctx->>'text_config_sha256',d->>'body_sha256',d->>'text_status',d->'text_details')
        ON CONFLICT DO NOTHING;
    ELSIF k IN ('text_unit','selection') THEN
      SELECT v.id,v.object_id INTO version_key,import_object_id FROM public.archive_object_versions v JOIN public.archive_objects o ON o.id=v.object_id
        WHERE o.source_id=source_key AND o.entity_type=d->>'entity_type' AND o.external_id=d->>'external_id'
        AND v.payload_sha256=d->>'payload_sha256' AND v.parser_version=d->>'parser_version'
        AND v.payload_hash_scheme='python-json-sort-utf8-compact:v1';
      IF version_key IS NULL THEN RAISE EXCEPTION 'Referenced version was not imported'; END IF;
      IF k='selection' THEN
        INSERT INTO public.archive_current_selections(run_id,source_id,object_id,version_id,selection_key,selection_metadata)
          VALUES(import_run_id,source_key,import_object_id,version_key,d->>'selection_key',d->'metadata') ON CONFLICT DO NOTHING;
        IF NOT EXISTS (SELECT 1 FROM public.archive_current_selections s WHERE s.run_id=import_run_id
          AND s.selection_key=d->>'selection_key' AND s.version_id=version_key AND s.selection_metadata=d->'metadata')
          THEN RAISE EXCEPTION 'Current version selection conflict'; END IF;
      ELSE
        SELECT e.id INTO extraction_key FROM public.archive_extraction_versions e JOIN public.archive_object_versions v ON v.id=e.input_source_version_id
          WHERE e.input_source_version_id=version_key AND e.extractor_name='corpus-plain-text' AND e.extractor_version=d->>'parser_version'
          AND e.config_sha256=ctx->>'text_config_sha256' AND e.output_sha256=v.body_sha256;
        IF extraction_key IS NULL OR d->>'text_sha256'<>encode(sha256(convert_to(d->>'text','UTF8')),'hex') THEN
          RAISE EXCEPTION 'Text unit source or checksum mismatch'; END IF;
        IF NOT EXISTS (SELECT 1 FROM public.archive_object_versions WHERE id=version_key
          AND substring(body FROM (d->'locator'->>'char_start')::integer+1 FOR (d->'locator'->>'char_end')::integer-(d->'locator'->>'char_start')::integer)=d->>'text')
          THEN RAISE EXCEPTION 'Text unit does not match full normalized body'; END IF;
        INSERT INTO public.archive_text_units(extraction_id,unit_key,unit_kind,locator,text_content,text_sha256)
          VALUES(extraction_key,d->>'unit_key','body',d->'locator',d->>'text',d->>'text_sha256') ON CONFLICT DO NOTHING;
        IF NOT EXISTS (SELECT 1 FROM public.archive_text_units WHERE extraction_id=extraction_key AND unit_key=d->>'unit_key'
          AND text_sha256=d->>'text_sha256' AND locator=d->'locator') THEN RAISE EXCEPTION 'Text unit identity conflict'; END IF;
        FOR candidate IN SELECT value FROM jsonb_array_elements(d->'code_candidates') LOOP
          INSERT INTO public.archive_part_numbers(extraction_id,unit_key,exact_value,char_start,char_end,method,method_version)
            VALUES(extraction_key,d->>'unit_key',candidate->>'text',(candidate->>'start')::integer,(candidate->>'end')::integer,
              'lexical-code-candidate','v1') ON CONFLICT DO NOTHING;
        END LOOP;
      END IF;
    ELSIF k='auxiliary' THEN
      IF d->>'row_sha256'<>encode(sha256(convert_to(d->>'row_text','UTF8')),'hex') THEN RAISE EXCEPTION 'Auxiliary row checksum mismatch'; END IF;
      INSERT INTO public.archive_index_auxiliary_rows(run_id,table_name,row_sha256,row_payload)
        VALUES(import_run_id,d->>'table_name',d->>'row_sha256',(d->>'row_text')::jsonb) ON CONFLICT DO NOTHING;
    ELSIF k='finalize' THEN
      SELECT count(DISTINCT version_id) INTO actual_rows FROM public.archive_version_provenance WHERE run_id=import_run_id;
      IF actual_rows<>(d->>'versions')::bigint THEN RAISE EXCEPTION 'Imported version coverage mismatch'; END IF;
      SELECT count(*) INTO actual_rows FROM public.archive_version_provenance WHERE run_id=import_run_id;
      IF actual_rows<>(d->>'provenance')::bigint THEN RAISE EXCEPTION 'Imported provenance coverage mismatch'; END IF;
      SELECT count(*) INTO actual_rows FROM public.archive_current_selections WHERE run_id=import_run_id;
      IF actual_rows<>(d->>'selections')::bigint THEN RAISE EXCEPTION 'Current selection coverage mismatch'; END IF;
      SELECT count(*) INTO actual_rows FROM public.archive_object_links l WHERE l.origin_version_id IN
        (SELECT p.version_id FROM public.archive_version_provenance p WHERE p.run_id=import_run_id);
      IF actual_rows<>(d->>'versioned_links')::bigint THEN RAISE EXCEPTION 'Versioned relationship coverage mismatch'; END IF;
      SELECT count(*) INTO actual_rows FROM public.archive_index_auxiliary_rows WHERE run_id=import_run_id;
      IF actual_rows<>(d->>'auxiliary_rows')::bigint THEN RAISE EXCEPTION 'Auxiliary source row coverage mismatch'; END IF;
      SELECT count(*) INTO actual_rows FROM public.archive_text_units u JOIN public.archive_extraction_versions e ON e.id=u.extraction_id
        WHERE e.extractor_name='corpus-plain-text' AND e.config_sha256=ctx->>'text_config_sha256' AND e.input_source_version_id IN
        (SELECT p.version_id FROM public.archive_version_provenance p WHERE p.run_id=import_run_id);
      IF actual_rows<>(d->>'text_units')::bigint THEN RAISE EXCEPTION 'Text unit coverage mismatch'; END IF;
      SELECT sum(char_length(body)) INTO actual_rows FROM public.archive_object_versions WHERE id IN
        (SELECT p.version_id FROM public.archive_version_provenance p WHERE p.run_id=import_run_id);
      IF coalesce(actual_rows,0)<>(d->>'body_characters')::bigint THEN RAISE EXCEPTION 'Full text character coverage mismatch'; END IF;
      IF EXISTS (SELECT 1 FROM public.archive_object_versions v
        WHERE v.id IN(SELECT version_id FROM public.archive_version_provenance WHERE run_id=import_run_id)
        AND v.body_sha256 IS DISTINCT FROM coalesce((SELECT encode(sha256(convert_to(string_agg(u.text_content,'' ORDER BY u.unit_key),'UTF8')),'hex')
          FROM public.archive_extraction_versions e JOIN public.archive_text_units u ON u.extraction_id=e.id
          WHERE e.input_source_version_id=v.id AND e.extractor_name='corpus-plain-text' AND e.config_sha256=ctx->>'text_config_sha256'
          AND e.extractor_version=v.parser_version AND e.output_sha256=v.body_sha256),encode(sha256(''::bytea),'hex')))
        THEN RAISE EXCEPTION 'Search units do not reconstruct every historical body'; END IF;
      UPDATE public.archive_ingestion_runs SET finished_at=now(),status='partial',coverage=d||jsonb_build_object('normalization_complete',true,
        'portal_collection_complete',false,'semantic_reviewed_by_importer',false) WHERE id=import_run_id;
    ELSE
      RAISE EXCEPTION 'Unsupported import row kind';
    END IF;
  END LOOP;
  INSERT INTO public.archive_import_batches(batch_sha256,run_id,importer_version,row_count)
    VALUES(batch_hash,import_run_id,ctx->>'importer_version',expected_rows);
  RETURN jsonb_build_object('batch_sha256',batch_hash,'status','committed','rows',expected_rows);
END $import$;

COMMENT ON FUNCTION public.archive_apply_import_batch(jsonb) IS 'archive_schema:v2';
REVOKE ALL ON FUNCTION public.archive_apply_import_batch(jsonb) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.archive_apply_import_batch(jsonb) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
