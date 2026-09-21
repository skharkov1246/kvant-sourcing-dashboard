"""Apply the private archive schema; print aggregate readiness, never source data.

The workflow supplies its existing database connection. This program does not
read secrets from PostgreSQL, import customer records, or change portal ACLs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "vpjliavuuxjcvtxbthlp"
SCHEMA_SHA = "4594a3efbfd52fe36855dbb5b6c0ec4a3cc4d1276b2a0e18cabda1145e6036f4"
FUNCTIONS = ("archive_search_text(text,integer,text)", "archive_search_status()",
             "archive_search_unit(text,text)")


class ActivationError(Exception):
    """Messages must be constants without DSNs or remote response bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_dsn(dsn, parse_dsn):
    if not isinstance(dsn, str) or not dsn:
        raise ActivationError("SUPABASE_DB_URL_MISSING")
    try:
        params = parse_dsn(dsn)
    except Exception:
        raise ActivationError("SUPABASE_DB_URL_INVALID") from None
    if any(params.get(key) for key in ("hostaddr", "service", "servicefile")):
        raise ActivationError("SUPABASE_CONNECTION_OVERRIDE_REJECTED")
    host = params.get("host", "")
    direct = host == f"db.{PROJECT}.supabase.co"
    pool = bool(re.fullmatch(r"aws-[a-z0-9-]+\.pooler\.supabase\.com", host))
    if not (direct or pool) or (pool and params.get("user") != f"postgres.{PROJECT}"):
        raise ActivationError("SUPABASE_PROJECT_MISMATCH")
    if params.get("port", "5432") != "5432" or params.get("dbname") != "postgres":
        raise ActivationError("SUPABASE_SESSION_CONNECTION_REQUIRED")
    if not params.get("password"):
        raise ActivationError("SUPABASE_DB_PASSWORD_MISSING")
    params = {key: params[key] for key in ("host", "port", "user", "dbname", "password")
              if key in params}
    params.update(sslmode="require", connect_timeout="20",
                  options="-c statement_timeout=120000 -c lock_timeout=5000")
    return params


def portal_bindings(env, opener=None):
    """Read names/presence only. Never copy or print secret values."""
    account = env.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = env.get("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not token:
        return {"checked": False, "reason": "CLOUDFLARE_CONFIGURATION_UNAVAILABLE"}
    opener = opener or urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/kvant-sourcing-f122",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ActivationError("CLOUDFLARE_RESPONSE_LIMIT")
        body = json.loads(raw)
        if body.get("success") is not True:
            raise ActivationError("CLOUDFLARE_READ_FAILED")
        variables = body["result"]["deployment_configs"]["production"].get("env_vars", {})
        return {"checked": True,
                "service_key_binding_present": "SUPABASE_SERVICE_KEY" in variables,
                "access_audience_binding_present": "CF_ACCESS_AUD" in variables,
                "values_verified": False}
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError, ActivationError):
        return {"checked": False, "reason": "CLOUDFLARE_BINDING_CHECK_FAILED"}


def activate(env):
    import psycopg2
    from psycopg2.extensions import parse_dsn

    params = validate_dsn(env.get("SUPABASE_DB_URL"), parse_dsn)
    base = (ROOT / "library/supabase/archive_schema.sql").read_bytes()
    if hashlib.sha256(base).hexdigest() != SCHEMA_SHA:
        raise ActivationError("REVIEWED_ARCHIVE_SCHEMA_CHANGED")
    search = (ROOT / "library/supabase/archive_search.sql").read_text(encoding="utf-8")
    with psycopg2.connect(**params) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Both reviewed files own their atomic transaction. Never edit lib_*.
            cur.execute(base.decode("utf-8"))
            cur.execute(search)
            cur.execute("NOTIFY pgrst, 'reload schema'")
            for role in ("anon", "authenticated", "service_role"):
                for signature in FUNCTIONS:
                    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                                (role, "public." + signature))
                    if bool(cur.fetchone()[0]) != (role == "service_role"):
                        raise ActivationError("ARCHIVE_RPC_ROLE_CHECK_FAILED")
            cur.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n
                           ON n.oid=c.relnamespace WHERE n.nspname='public'
                           AND c.relname LIKE 'archive\\_%%' ESCAPE '\\'
                           AND c.relkind='r' AND NOT c.relrowsecurity""")
            if cur.fetchone()[0] != 0:
                raise ActivationError("ARCHIVE_RLS_CHECK_FAILED")
            cur.execute("BEGIN READ ONLY")
            try:
                cur.execute("SET LOCAL ROLE service_role")
                cur.execute("SELECT public.archive_search_status()")
                status = cur.fetchone()[0]
                if not isinstance(status, dict):
                    raise ActivationError("ARCHIVE_STATUS_INVALID")
            finally:
                cur.execute("ROLLBACK")
    # Do not print status wholesale: its imported run scope may contain private IDs.
    return {"schema_applied": True, "service_rpc_read_verified": True,
            "anon_authenticated_execute_denied": True, "customer_rows_imported": 0,
            "schema_sha256": SCHEMA_SHA,
            "search_sql_sha256": hashlib.sha256(search.encode()).hexdigest(),
            "portal_bindings": portal_bindings(env),
            "portal_published": False}


def main(env=None):
    try:
        result = activate(os.environ if env is None else env)
    except ActivationError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    except Exception as error:
        # psycopg/urllib exceptions can include private SQL values or credentials.
        report = {"ok": False, "error": "ARCHIVE_ACTIVATION_FAILED"}
        code = getattr(error, "pgcode", None)
        if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code):
            report["sqlstate"] = code
        print(json.dumps(report))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
