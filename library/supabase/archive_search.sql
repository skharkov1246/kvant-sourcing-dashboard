-- Private archive text RPCs. Additive to the reviewed archive_schema:v2 schema.
-- Only imported text units are searched; every extraction version is retained.
-- SECURITY INVOKER plus service_role-only EXECUTE; no Storage or ACL mutation.
-- PostgREST applies function proconfig to the RPC transaction (5s timeout below).
-- A direct SQL caller must set its outer timeout before SELECT; a function-local
-- SET alone does not reset a plain SQL statement's already-running timer.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

DO $guard$
DECLARE signature text; existing regprocedure; existing_index regclass;
BEGIN
  IF obj_description('public.archive_text_units'::regclass, 'pg_class')
     IS DISTINCT FROM 'archive_schema:v2' THEN
    RAISE EXCEPTION 'ARCHIVE_SCHEMA_INCOMPATIBLE';
  END IF;
  FOREACH signature IN ARRAY ARRAY[
    'public.archive_search_text(text,integer,text)',
    'public.archive_search_unit(text,text)',
    'public.archive_search_status()'
  ] LOOP
    existing := to_regprocedure(signature);
    IF existing IS NOT NULL AND obj_description(existing, 'pg_proc')
       IS DISTINCT FROM 'archive_search:v1' THEN
      RAISE EXCEPTION 'ARCHIVE_SEARCH_UNMANAGED_FUNCTION';
    END IF;
  END LOOP;
  existing_index := to_regclass('public.archive_text_units_russian_fts_idx');
  IF existing_index IS NOT NULL AND (
    obj_description(existing_index, 'pg_class') IS DISTINCT FROM 'archive_search:v1'
    OR NOT EXISTS (
      SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
      JOIN pg_am am ON am.oid=c.relam
      WHERE i.indexrelid=existing_index AND i.indrelid='public.archive_text_units'::regclass
        AND i.indisvalid AND i.indisready AND NOT i.indisunique AND i.indpred IS NULL
        AND am.amname='gin' AND i.indnatts=1
        AND pg_get_expr(i.indexprs,i.indrelid) = 'to_tsvector(''russian''::regconfig, text_content)'
    )
  ) THEN
    RAISE EXCEPTION 'ARCHIVE_SEARCH_UNMANAGED_INDEX';
  END IF;
END $guard$;

CREATE INDEX IF NOT EXISTS archive_text_units_russian_fts_idx
  ON public.archive_text_units USING gin(to_tsvector('russian'::regconfig,text_content));
COMMENT ON INDEX public.archive_text_units_russian_fts_idx IS 'archive_search:v1';

CREATE OR REPLACE FUNCTION public.archive_search_text(
  search_query text, result_limit integer DEFAULT 20, source_kind text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog
SET statement_timeout = '5s'
SET lock_timeout = '1s'
SET plan_cache_mode = 'force_custom_plan'
AS $search$
DECLARE
  query_terms tsquery;
  russian_terms tsquery;
  normalized_query text;
  requested_kind text := source_kind;
  selected_rows jsonb;
  result jsonb;
BEGIN
  -- Same end-whitespace set as ECMAScript String.trim(); never alter source text.
  normalized_query := btrim(search_query, U&'\0009\000A\000B\000C\000D\0020\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000\FEFF');
  IF normalized_query IS NULL OR char_length(normalized_query) NOT BETWEEN 2 AND 200
     OR octet_length(normalized_query) > 800 OR normalized_query ~ U&'[\0001-\001F\007F]'
     OR result_limit IS NULL
     OR result_limit NOT BETWEEN 1 AND 50 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'ARCHIVE_SEARCH_INVALID_INPUT';
  END IF;
  IF requested_kind IS NOT NULL AND requested_kind <> ALL(
    ARRAY['crm','activity','timeline','mail','chat','task','attachment','other']
  ) THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'ARCHIVE_SEARCH_INVALID_KIND';
  END IF;
  -- Literal plain words, not tsquery operators or user-supplied SQL.
  query_terms := plainto_tsquery('simple'::regconfig, normalized_query);
  russian_terms := plainto_tsquery('russian'::regconfig, normalized_query);
  IF numnode(query_terms) = 0 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'ARCHIVE_SEARCH_EMPTY_TERMS';
  END IF;

  WITH candidates AS MATERIALIZED (
    SELECT u.extraction_id, u.unit_key, u.unit_kind, u.locator, u.text_content, u.text_sha256,
           e.input_blob_sha256, e.output_sha256, e.input_source_version_id,
           coalesce(v.title, e.details->>'name', '') AS title,
           v.source_created_at, v.source_updated_at, v.raw_blob_sha256, v.raw_json_pointer, o.external_id,
           o.entity_type,
           CASE
             WHEN e.input_blob_sha256 IS NOT NULL THEN 'attachment'
             WHEN o.entity_type IN ('activity','crm_activity') THEN 'activity'
             WHEN o.entity_type IN ('timeline','timeline_comment','crm_timeline','crm_timeline_comment') THEN 'timeline'
             WHEN o.entity_type IN ('mail','mail_message','mail_activity') THEN 'mail'
             WHEN o.entity_type IN ('chat','chat_message','im_message','im_chat') THEN 'chat'
             WHEN o.entity_type IN ('task','task_comment') THEN 'task'
             WHEN o.entity_type IN ('attachment','disk_file','internal_file') THEN 'attachment'
             WHEN o.entity_type = 'crm' OR left(o.entity_type,4) IN ('crm:','crm_') THEN 'crm'
             ELSE 'other'
           END AS category
    FROM public.archive_text_units u
    JOIN public.archive_extraction_versions e ON e.id = u.extraction_id
    LEFT JOIN public.archive_object_versions v ON v.id = e.input_source_version_id
    LEFT JOIN public.archive_objects o ON o.id = v.object_id
    WHERE (u.fts @@ query_terms OR to_tsvector('russian'::regconfig,u.text_content) @@ russian_terms)
      AND (requested_kind IS NULL OR requested_kind = CASE
        WHEN e.input_blob_sha256 IS NOT NULL THEN 'attachment'
        WHEN o.entity_type IN ('activity','crm_activity') THEN 'activity'
        WHEN o.entity_type IN ('timeline','timeline_comment','crm_timeline','crm_timeline_comment') THEN 'timeline'
        WHEN o.entity_type IN ('mail','mail_message','mail_activity') THEN 'mail'
        WHEN o.entity_type IN ('chat','chat_message','im_message','im_chat') THEN 'chat'
        WHEN o.entity_type IN ('task','task_comment') THEN 'task'
        WHEN o.entity_type IN ('attachment','disk_file','internal_file') THEN 'attachment'
        WHEN o.entity_type = 'crm' OR left(o.entity_type,4) IN ('crm:','crm_') THEN 'crm'
        ELSE 'other' END)
    ORDER BY u.extraction_id, u.unit_key COLLATE "C"
    LIMIT result_limit + 1
  )
  SELECT coalesce(jsonb_agg(jsonb_build_object(
    'extraction_id', extraction_id::text,
    'unit_key', unit_key, 'unit_kind', unit_kind,
    'source_kind', category, 'source_entity_type', entity_type,
    'source_id', external_id, 'source_version_id', input_source_version_id::text,
    'title', left(title,300), 'title_truncated', char_length(title)>300,
    'text', left(text_content,1600), 'text_length', char_length(text_content),
    'text_truncated', char_length(text_content)>1600, 'is_excerpt', true,
    'locator', locator, 'source_created_at', source_created_at,
    'source_updated_at', source_updated_at, 'input_blob_sha256', input_blob_sha256,
    'extraction_output_sha256', output_sha256,
    'raw_blob_sha256',raw_blob_sha256,'raw_json_pointer',raw_json_pointer,'text_sha256',text_sha256
  ) ORDER BY extraction_id, unit_key COLLATE "C"), '[]'::jsonb)
  INTO selected_rows FROM candidates;

  -- Preserve every returned locator. Refuse oversized metadata rather than silently
  -- discard source coordinates. Validate the +1 row as well as visible rows.
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(selected_rows) AS j(item)
    WHERE octet_length(item->>'unit_key')>1024
       OR octet_length(item->>'source_id')>1024
       OR octet_length(item->>'source_entity_type')>512
       OR octet_length((item->'locator')::text)>8192
       OR octet_length(item->>'raw_json_pointer')>8192
  ) THEN
    RAISE EXCEPTION USING ERRCODE='54000', MESSAGE='ARCHIVE_SEARCH_METADATA_TOO_LARGE';
  END IF;
  result := jsonb_build_object(
    'items', coalesce((SELECT jsonb_agg(item ORDER BY ordinal)
      FROM jsonb_array_elements(selected_rows) WITH ORDINALITY AS x(item,ordinal)
      WHERE ordinal<=result_limit), '[]'::jsonb),
    'returned', least(result_limit,jsonb_array_length(selected_rows)),
    'has_more', jsonb_array_length(selected_rows)>result_limit,
    'scope','imported_archive_text','all_versions',true,'full_archive',false,
    'search_mode','plain_terms','order','extraction_id_unit_key_asc'
  );
  IF octet_length(result::text)>1048576 THEN
    RAISE EXCEPTION USING ERRCODE='54000', MESSAGE='ARCHIVE_SEARCH_RESPONSE_TOO_LARGE';
  END IF;
  RETURN result;
END $search$;

CREATE OR REPLACE FUNCTION public.archive_search_unit(extraction_id text, unit_key text)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog
SET statement_timeout = '5s'
SET lock_timeout = '1s'
AS $detail$
DECLARE wanted_id bigint; wanted_key text := unit_key; item jsonb; result jsonb;
BEGIN
  IF extraction_id IS NULL OR extraction_id !~ '^[1-9][0-9]{0,18}$'
     OR (char_length(extraction_id)=19 AND extraction_id COLLATE "C">'9223372036854775807' COLLATE "C")
     OR wanted_key IS NULL OR octet_length(wanted_key) NOT BETWEEN 1 AND 1024
     OR wanted_key ~ U&'[\0001-\001F\007F]' THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='ARCHIVE_SEARCH_INVALID_UNIT';
  END IF;
  wanted_id := extraction_id::bigint;
  SELECT jsonb_build_object(
    'extraction_id',u.extraction_id::text,'unit_key',u.unit_key,'unit_kind',u.unit_kind,
    'source_kind',CASE
      WHEN e.input_blob_sha256 IS NOT NULL THEN 'attachment'
      WHEN o.entity_type IN ('activity','crm_activity') THEN 'activity'
      WHEN o.entity_type IN ('timeline','timeline_comment','crm_timeline','crm_timeline_comment') THEN 'timeline'
      WHEN o.entity_type IN ('mail','mail_message','mail_activity') THEN 'mail'
      WHEN o.entity_type IN ('chat','chat_message','im_message','im_chat') THEN 'chat'
      WHEN o.entity_type IN ('task','task_comment') THEN 'task'
      WHEN o.entity_type IN ('attachment','disk_file','internal_file') THEN 'attachment'
      WHEN o.entity_type = 'crm' OR left(o.entity_type,4) IN ('crm:','crm_') THEN 'crm'
      ELSE 'other' END,
    'source_entity_type',o.entity_type,'source_id',o.external_id,
    'source_version_id',e.input_source_version_id::text,
    'title',left(coalesce(v.title,e.details->>'name',''),300),
    'title_truncated',char_length(coalesce(v.title,e.details->>'name',''))>300,
    'text',u.text_content,'text_length',char_length(u.text_content),
    'text_truncated',false,'is_excerpt',false,'locator',u.locator,
    'source_created_at',v.source_created_at,'source_updated_at',v.source_updated_at,
    'input_blob_sha256',e.input_blob_sha256,'extraction_output_sha256',e.output_sha256,
    'raw_blob_sha256',v.raw_blob_sha256,'raw_json_pointer',v.raw_json_pointer,'text_sha256',u.text_sha256
  ) INTO item
  FROM public.archive_text_units u
  JOIN public.archive_extraction_versions e ON e.id=u.extraction_id
  LEFT JOIN public.archive_object_versions v ON v.id=e.input_source_version_id
  LEFT JOIN public.archive_objects o ON o.id=v.object_id
  WHERE u.extraction_id=wanted_id AND u.unit_key=wanted_key;
  IF item IS NOT NULL AND (
    octet_length(item->>'source_id')>1024 OR octet_length(item->>'source_entity_type')>512
    OR octet_length((item->'locator')::text)>8192
    OR octet_length(item->>'raw_json_pointer')>8192
  ) THEN
    RAISE EXCEPTION USING ERRCODE='54000', MESSAGE='ARCHIVE_SEARCH_METADATA_TOO_LARGE';
  END IF;
  result := jsonb_build_object('found',item IS NOT NULL,'item',item,
    'scope','imported_archive_text','all_versions',true,'full_archive',false);
  IF octet_length(result::text)>131072 THEN
    RAISE EXCEPTION USING ERRCODE='54000', MESSAGE='ARCHIVE_SEARCH_RESPONSE_TOO_LARGE';
  END IF;
  RETURN result;
END $detail$;

CREATE OR REPLACE FUNCTION public.archive_search_status()
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = pg_catalog
SET statement_timeout = '5s'
SET lock_timeout = '1s'
AS $status$
DECLARE result jsonb; recent jsonb;
BEGIN
  SELECT jsonb_build_object('id',r.id::text,'status',r.status,
    'started_at',r.started_at,'finished_at',r.finished_at,'coverage',r.coverage)
  INTO recent FROM public.archive_ingestion_runs r ORDER BY r.id DESC LIMIT 1;
  IF recent IS NOT NULL AND octet_length((recent->'coverage')::text)>65536 THEN
    RAISE EXCEPTION USING ERRCODE='54000', MESSAGE='ARCHIVE_SEARCH_COVERAGE_TOO_LARGE';
  END IF;
  -- Exact imported row counts, intentionally separate from the search RPC.
  -- No invented count of files still in Storage, aliases, messages, or knowledge.
  result := jsonb_build_object(
    'scope','imported_archive_text','all_versions',true,'full_archive',false,
    'counts',jsonb_build_object(
      'objects',(SELECT count(*)::text FROM public.archive_objects),
      'object_versions',(SELECT count(*)::text FROM public.archive_object_versions),
      'extraction_versions',(SELECT count(*)::text FROM public.archive_extraction_versions),
      'text_units',(SELECT count(*)::text FROM public.archive_text_units),
      'logical_blobs',(SELECT count(*)::text FROM public.archive_blobs),
      'attachment_observations',(SELECT count(*)::text FROM public.archive_attachments),
      'import_batches',(SELECT count(*)::text FROM public.archive_import_batches),
      'ingestion_runs',(SELECT count(*)::text FROM public.archive_ingestion_runs)),
    'last_run',recent,'storage_files_outside_import_not_searched',true,
    'semantic_understanding_measured',false
  );
  IF octet_length(result::text)>262144 THEN
    RAISE EXCEPTION USING ERRCODE='54000', MESSAGE='ARCHIVE_SEARCH_RESPONSE_TOO_LARGE';
  END IF;
  RETURN result;
END $status$;

COMMENT ON FUNCTION public.archive_search_text(text,integer,text) IS 'archive_search:v1';
COMMENT ON FUNCTION public.archive_search_unit(text,text) IS 'archive_search:v1';
COMMENT ON FUNCTION public.archive_search_status() IS 'archive_search:v1';
REVOKE ALL ON FUNCTION public.archive_search_text(text,integer,text) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.archive_search_unit(text,text) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION public.archive_search_status() FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION public.archive_search_text(text,integer,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.archive_search_unit(text,text) TO service_role;
GRANT EXECUTE ON FUNCTION public.archive_search_status() TO service_role;
COMMIT;
