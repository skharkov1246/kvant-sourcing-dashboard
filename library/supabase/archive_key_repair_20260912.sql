-- Run only as postgres. Never changes permissions on existing objects or Vault.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
DO $install$
DECLARE
  role_name text;
  existing_schema oid;
BEGIN
  IF current_user <> 'postgres' THEN
    RAISE EXCEPTION 'REPAIR_POSTGRES_REQUIRED';
  END IF;
  IF pg_catalog.to_regclass('vault.secrets') IS NULL OR
     pg_catalog.to_regclass('vault.decrypted_secrets') IS NULL THEN
    RAISE EXCEPTION 'REPAIR_VAULT_UNAVAILABLE';
  END IF;
  FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (
      SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'vault' AND c.relkind IN ('r','v','m','p')
        AND pg_catalog.has_any_column_privilege(role_name, c.oid, 'SELECT')
    ) THEN RAISE EXCEPTION 'REPAIR_VAULT_READ_UNSAFE'; END IF;
  END LOOP;
  SELECT oid INTO existing_schema FROM pg_catalog.pg_namespace WHERE nspname = 'archive_key_repair_private';
  IF existing_schema IS NOT NULL THEN
    -- The Python runner verifies the existing marker/owner/phase. Never reopen.
    RETURN;
  END IF;
  IF pg_catalog.to_regprocedure('public.archive_stage_portal_key_20260912(text)') IS NOT NULL OR
     EXISTS (SELECT 1 FROM vault.secrets WHERE name = 'kvant_portal_archive_key_repair_20260912') THEN
    RAISE EXCEPTION 'REPAIR_NAME_COLLISION';
  END IF;
  CREATE SCHEMA archive_key_repair_private AUTHORIZATION postgres;
  REVOKE ALL ON SCHEMA archive_key_repair_private FROM PUBLIC, anon, authenticated, service_role;
  CREATE TABLE archive_key_repair_private.portal_key_20260912 (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    phase text NOT NULL CHECK (phase IN ('awaiting','staged','validating','applying','complete','failed','expired','application_unknown')),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    expires_at timestamptz NOT NULL,
    secret_id uuid,
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp(),
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '2 hours')
  );
  ALTER TABLE archive_key_repair_private.portal_key_20260912 OWNER TO postgres;
  REVOKE ALL ON TABLE archive_key_repair_private.portal_key_20260912 FROM PUBLIC, anon, authenticated, service_role;
  COMMENT ON TABLE archive_key_repair_private.portal_key_20260912 IS 'kvant-portal-one-time-key-repair:20260912:v1';
  INSERT INTO archive_key_repair_private.portal_key_20260912(phase, created_at, expires_at)
    SELECT 'awaiting', t, t + interval '2 hours' FROM (SELECT pg_catalog.clock_timestamp() AS t) x;
  EXECUTE $ddl$
    CREATE FUNCTION public.archive_stage_portal_key_20260912(service_key text)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = '' SET statement_timeout = '5s' SET lock_timeout = '1s'
    AS $function$
    DECLARE state archive_key_repair_private.portal_key_20260912%ROWTYPE; sid uuid; caller_role text;
    BEGIN
      IF service_key IS NULL OR pg_catalog.octet_length(service_key) NOT BETWEEN 18 AND 512 OR
         service_key !~ '^sb_secret_[A-Za-z0-9_-]{8,}$' THEN
        RAISE EXCEPTION 'REPAIR_STAGE_INVALID';
      END IF;
      SELECT * INTO STRICT state FROM archive_key_repair_private.portal_key_20260912 WHERE singleton FOR UPDATE;
      IF state.phase <> 'awaiting' OR state.secret_id IS NOT NULL OR
         pg_catalog.clock_timestamp() >= state.expires_at THEN
        RAISE EXCEPTION 'REPAIR_STAGE_CLOSED';
      END IF;
      FOREACH caller_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (
          SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='vault' AND c.relkind IN ('r','v','m','p')
            AND pg_catalog.has_any_column_privilege(caller_role,c.oid,'SELECT')
        ) THEN RAISE EXCEPTION 'REPAIR_STAGE_UNSAFE'; END IF;
      END LOOP;
      IF EXISTS (SELECT 1 FROM vault.secrets WHERE name = 'kvant_portal_archive_key_repair_20260912') THEN
        RAISE EXCEPTION 'REPAIR_STAGE_COLLISION';
      END IF;
      sid := vault.create_secret(service_key, 'kvant_portal_archive_key_repair_20260912', 'One-time portal configuration transfer; expires within two hours.');
      UPDATE archive_key_repair_private.portal_key_20260912
        SET phase = 'staged', secret_id = sid, updated_at = pg_catalog.clock_timestamp()
        WHERE singleton;
      RETURN true;
    EXCEPTION WHEN OTHERS THEN
      RAISE EXCEPTION 'REPAIR_STAGE_REJECTED' USING ERRCODE = 'P0001';
    END;
    $function$
  $ddl$;
  ALTER FUNCTION public.archive_stage_portal_key_20260912(text) OWNER TO postgres;
  COMMENT ON FUNCTION public.archive_stage_portal_key_20260912(text) IS 'kvant-portal-one-time-key-repair:20260912:v1';
  REVOKE ALL ON FUNCTION public.archive_stage_portal_key_20260912(text) FROM PUBLIC, anon, authenticated;
  GRANT EXECUTE ON FUNCTION public.archive_stage_portal_key_20260912(text) TO service_role;
  -- Reject unexpected inherited default ACLs before this installation commits.
  -- Do not modify existing default privileges or unrelated roles/objects.
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_namespace n,
      LATERAL pg_catalog.aclexplode(COALESCE(n.nspacl, pg_catalog.acldefault('n',n.nspowner))) x
    WHERE n.nspname='archive_key_repair_private' AND x.grantee<>n.nspowner
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c,
      LATERAL pg_catalog.aclexplode(COALESCE(c.relacl, pg_catalog.acldefault('r',c.relowner))) x
    WHERE c.oid='archive_key_repair_private.portal_key_20260912'::regclass AND x.grantee<>c.relowner
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p,
      LATERAL pg_catalog.aclexplode(COALESCE(p.proacl, pg_catalog.acldefault('f',p.proowner))) x
    WHERE p.oid='public.archive_stage_portal_key_20260912(text)'::regprocedure
      AND (x.grantee NOT IN (p.proowner,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='service_role'))
        OR (x.grantee<>p.proowner AND x.is_grantable))
  ) THEN RAISE EXCEPTION 'REPAIR_DEFAULT_ACL_UNSAFE'; END IF;
  FOREACH role_name IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
    IF pg_catalog.has_schema_privilege(role_name,'archive_key_repair_private','USAGE') OR
       pg_catalog.has_any_column_privilege(role_name,'archive_key_repair_private.portal_key_20260912','SELECT') OR
       pg_catalog.has_function_privilege(role_name,'public.archive_stage_portal_key_20260912(text)','EXECUTE')
         IS DISTINCT FROM (role_name='service_role') THEN
      RAISE EXCEPTION 'REPAIR_EFFECTIVE_ACL_UNSAFE';
    END IF;
  END LOOP;
END
$install$;
COMMIT;
