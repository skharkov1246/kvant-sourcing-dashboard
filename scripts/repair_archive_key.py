"""One-time fixed Vault -> production Pages binding transfer; no key outputs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "vpjliavuuxjcvtxbthlp"
ACCOUNT = "183c943a62280a0a37852a107ba1da26"
PAGES = "kvant-sourcing-f122"
CF_URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/pages/projects/{PAGES}"
SB_URL = f"https://{PROJECT}.supabase.co/rest/v1/rpc/"
VAULT_NAME = "kvant_portal_archive_key_repair_20260912"
TABLE = "archive_key_repair_private.portal_key_20260912"
RPC = "public.archive_stage_portal_key_20260912(text)"
MARKER = "kvant-portal-one-time-key-repair:20260912:v1"
SQL_PATH = ROOT / "library/supabase/archive_key_repair_20260912.sql"
SQL_SHA256 = "0a08ef49fee5e7fd4006498446b322e6336d769bbdc6434035cb8c79c6a8bc8e"
TERMINAL = {"complete", "failed", "expired", "application_unknown"}
SAFE = {"CONFIG_INVALID", "DB_TARGET_INVALID", "DB_REQUIRED", "DB_OWNER_INVALID", "BUSY", "SQL_CHANGED", "STATE_INVALID", "VAULT_UNSAFE", "VAULT_KEY_INVALID", "EXPIRED", "RPC_UNSAFE", "HTTP_FAILED", "HTTP_RESPONSE_INVALID", "HTTP_LIMIT", "PROBE_REJECTED", "CF_CHANGED", "PATCH_UNKNOWN", "REPAIR_FAILED", "CLEANUP_FAILED"}


class RepairError(Exception):
    pass


def require(ok, code):
    if not ok:
        raise RepairError(code)


def valid_key(key):
    return isinstance(key, str) and 18 <= len(key) <= 512 and re.fullmatch(r"sb_secret_[A-Za-z0-9_-]{8,}", key) is not None


def dsn_params(raw, parse):
    require(isinstance(raw, str) and raw, "DB_REQUIRED")
    try:
        value = parse(raw)
    except Exception:  # noqa: BLE001 -- privacy boundary: raw errors may contain credentials.
        raise RepairError("DB_TARGET_INVALID") from None
    require(not any(value.get(k) for k in ("hostaddr", "service", "servicefile")), "DB_TARGET_INVALID")
    host, user = value.get("host"), value.get("user")
    direct = host == f"db.{PROJECT}.supabase.co" and user == "postgres"
    pool = bool(re.fullmatch(r"aws-[a-z0-9-]+\.pooler\.supabase\.com", host or "")) and user == f"postgres.{PROJECT}"
    require((direct or pool) and value.get("port", "5432") == "5432" and value.get("dbname") == "postgres" and value.get("password"), "DB_TARGET_INVALID")
    clean = {k: value[k] for k in ("host", "port", "user", "dbname", "password") if k in value}
    clean.update(sslmode="require", connect_timeout=15, options="-c statement_timeout=15000 -c lock_timeout=5000")
    return clean


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.calls = 0
        self.total = 0

    def call(self, method, url, credential, payload=None):
        allowed = {("POST", SB_URL + "archive_search_status"), ("POST", SB_URL + "archive_search_text"), ("GET", CF_URL), ("PATCH", CF_URL)}
        require((method, url) in allowed and self.calls < 5, "HTTP_LIMIT")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"}
        if url.startswith(SB_URL):
            require(valid_key(credential), "VAULT_KEY_INVALID")
            headers["apikey"] = credential
        else:
            headers["Authorization"] = "Bearer " + credential
        data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        require(data is None or len(data) <= 4096, "HTTP_LIMIT")
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        self.calls += 1
        try:
            with self.opener.open(request, timeout=20) as response:
                require(response.status == 200 and response.geturl() == url, "HTTP_FAILED")
                require((response.headers.get("Content-Type") or "").lower().startswith("application/json"), "HTTP_RESPONSE_INVALID")
                raw = response.read(1024 * 1024 + 1)
            self.total += len(raw)
            require(len(raw) <= 1024 * 1024 and self.total <= 5 * 1024 * 1024, "HTTP_LIMIT")
            def pairs(rows):
                result = {}
                for k, v in rows:
                    require(k not in result, "HTTP_RESPONSE_INVALID")
                    result[k] = v
                return result
            return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(RepairError("HTTP_RESPONSE_INVALID")))
        except urllib.error.HTTPError as error:
            error.close()
            raise RepairError("HTTP_FAILED") from None
        except RepairError:
            raise
        except Exception:  # noqa: BLE001 -- privacy boundary: raw errors may contain credentials.
            raise RepairError("HTTP_FAILED") from None


def status_valid(value):
    require(isinstance(value, dict) and value.get("scope") == "imported_archive_text" and value.get("all_versions") is True and value.get("full_archive") is False, "PROBE_REJECTED")
    require(value.get("semantic_understanding_measured") is False and value.get("storage_files_outside_import_not_searched") is True, "PROBE_REJECTED")
    counts = value.get("counts")
    require(isinstance(counts, dict), "PROBE_REJECTED")
    for key in ("objects", "object_versions", "extraction_versions", "text_units", "logical_blobs", "attachment_observations", "import_batches", "ingestion_runs"):
        require(isinstance(counts.get(key), str) and re.fullmatch(r"0|[1-9][0-9]{0,19}", counts[key]), "PROBE_REJECTED")
    require(int(counts["text_units"]) > 0 and int(counts["object_versions"]) > 0, "PROBE_REJECTED")


def search_valid(value):
    require(isinstance(value, dict) and value.get("scope") == "imported_archive_text" and value.get("all_versions") is True and value.get("full_archive") is False, "PROBE_REJECTED")
    require(value.get("search_mode") == "plain_terms" and value.get("order") == "extraction_id_unit_key_asc" and type(value.get("has_more")) is bool, "PROBE_REJECTED")
    items = value.get("items")
    require(isinstance(items, list) and len(items) == 1 and type(value.get("returned")) is int and value["returned"] == 1, "PROBE_REJECTED")
    item = items[0]
    require(isinstance(item, dict) and isinstance(item.get("text"), str) and 0 < len(item["text"]) <= 1600 and isinstance(item.get("locator"), dict), "PROBE_REJECTED")
    require(item.get("is_excerpt") is True and type(item.get("text_length")) is int and len(item["text"]) <= item["text_length"] <= 16000 and item.get("text_truncated") is (item["text_length"] > len(item["text"])), "PROBE_REJECTED")
    require(isinstance(item.get("extraction_id"), str) and re.fullmatch(r"[1-9][0-9]{0,18}", item["extraction_id"]), "PROBE_REJECTED")
    for name in ("extraction_output_sha256", "text_sha256"):
        require(isinstance(item.get(name), str) and re.fullmatch(r"[a-f0-9]{64}", item[name]), "PROBE_REJECTED")


def cf_config(value):
    require(isinstance(value, dict) and value.get("success") is True and isinstance(value.get("result"), dict), "HTTP_RESPONSE_INVALID")
    result = value["result"]
    require(result.get("name") == PAGES and result.get("subdomain") == PAGES + ".pages.dev", "HTTP_RESPONSE_INVALID")
    configs = result.get("deployment_configs")
    require(isinstance(configs, dict) and isinstance(configs.get("production"), dict), "HTTP_RESPONSE_INVALID")
    prod = configs["production"]
    variables = prod.get("env_vars")
    require(isinstance(variables, dict), "HTTP_RESPONSE_INVALID")
    for variable in variables.values():
        require(isinstance(variable, dict) and variable.get("type") in ("plain_text", "secret_text"), "HTTP_RESPONSE_INVALID")
    return configs


class Database:
    def __init__(self, conn):
        self.conn = conn
        self.conn.autocommit = True

    def one(self, sql, args=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchmany(2)
            require(len(rows) == 1, "STATE_INVALID")
            return rows[0]

    def execute(self, sql, args=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.rowcount

    def start(self):
        require(self.one("SELECT current_user")[0] == "postgres", "DB_OWNER_INVALID")
        require(self.one("SELECT pg_try_advisory_lock(20260912, 712)")[0] is True, "BUSY")
        raw = SQL_PATH.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == SQL_SHA256, "SQL_CHANGED")
        self.execute(raw.decode("utf-8"))
        self.execute("NOTIFY pgrst, 'reload schema'")
        self.metadata()

    def metadata(self):
        row = self.one("""SELECT pg_get_userbyid(c.relowner), pg_get_userbyid(n.nspowner), obj_description(c.oid, 'pg_class'),
          EXISTS (SELECT 1 FROM aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) x WHERE x.grantee <> c.relowner),
          EXISTS (SELECT 1 FROM aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) x WHERE x.grantee <> n.nspowner)
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass(%s)""", (TABLE,))
        require(row == ("postgres", "postgres", MARKER, False, False), "STATE_INVALID")
        phase = self.one(f"SELECT phase FROM {TABLE} WHERE singleton")[0]
        oid = self.one("SELECT to_regprocedure(%s)::oid", (RPC,))[0]
        if oid is None:
            require(phase in TERMINAL, "RPC_UNSAFE")
        else:
            props = self.one("""SELECT pg_get_userbyid(p.proowner), obj_description(p.oid,'pg_proc'), p.prosecdef,
              p.proconfig @> ARRAY['search_path=""'],
              EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                WHERE a.grantee NOT IN (p.proowner, (SELECT oid FROM pg_roles WHERE rolname='service_role'))
                   OR (a.grantee<>p.proowner AND a.is_grantable))
              FROM pg_proc p WHERE p.oid=%s""", (oid,))
            require(props == ("postgres", MARKER, True, True, False), "RPC_UNSAFE")
            for role in ("anon", "authenticated", "service_role"):
                allowed = self.one("SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, RPC))[0]
                require(allowed is (role == "service_role"), "RPC_UNSAFE")
        for role in ("anon", "authenticated"):
            unsafe = self.one("""SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname='vault' AND c.relkind IN ('r','v','m','p') AND has_any_column_privilege(%s,c.oid,'SELECT'))""", (role,))[0]
            require(unsafe is False, "VAULT_UNSAFE")

    def state(self):
        self.metadata()
        phase, sid, live = self.one(f"SELECT phase,secret_id,clock_timestamp()<expires_at FROM {TABLE} WHERE singleton")
        require(phase in TERMINAL | {"awaiting", "staged", "validating", "applying"}, "STATE_INVALID")
        return phase, sid, live

    def key(self):
        sid = self.one(f"UPDATE {TABLE} SET phase='validating',updated_at=clock_timestamp() WHERE singleton AND phase='staged' AND clock_timestamp()<expires_at RETURNING secret_id")[0]
        require(sid is not None, "VAULT_KEY_INVALID")
        key = self.one("SELECT decrypted_secret FROM vault.decrypted_secrets WHERE id=%s AND name=%s", (sid, VAULT_NAME))[0]
        require(valid_key(key), "VAULT_KEY_INVALID")
        return key

    def applying(self):
        require(self.execute(f"UPDATE {TABLE} SET phase='applying',updated_at=clock_timestamp() WHERE singleton AND phase='validating' AND clock_timestamp()<expires_at") == 1, "EXPIRED")

    def cleanup(self, terminal):
        require(terminal in TERMINAL, "STATE_INVALID")
        # The exact UUID from our owner-only journal is the only deletable secret.
        self.execute("BEGIN")
        try:
            phase, sid = self.one(f"SELECT phase,secret_id FROM {TABLE} WHERE singleton FOR UPDATE")
            if sid is not None:
                self.execute("DELETE FROM vault.secrets WHERE id=%s AND name=%s", (sid, VAULT_NAME))
            oid = self.one("SELECT to_regprocedure(%s)::oid", (RPC,))[0]
            if oid is not None:
                owner, marker = self.one("SELECT pg_get_userbyid(proowner),obj_description(oid,'pg_proc') FROM pg_proc WHERE oid=%s", (oid,))
                require(owner == "postgres" and marker == MARKER, "RPC_UNSAFE")
                self.execute("DROP FUNCTION public.archive_stage_portal_key_20260912(text)")
            final = phase if phase in TERMINAL else terminal
            self.execute(f"UPDATE {TABLE} SET phase=%s,secret_id=NULL,updated_at=clock_timestamp() WHERE singleton", (final,))
            self.execute("COMMIT")
        except Exception:
            self.execute("ROLLBACK")
            raise
        self.execute("NOTIFY pgrst, 'reload schema'")


def repair(db, http, token):
    db.start()
    phase, _, live = db.state()
    if phase in TERMINAL:
        db.cleanup(phase)
        return {"phase": phase, "ready_to_publish": phase == "complete"}
    if not live:
        db.cleanup("expired")
        return {"phase": "expired", "ready_to_publish": False}
    if phase == "awaiting":
        return {"phase": "awaiting", "ready_to_publish": False}
    if phase in ("validating", "applying"):
        final = "application_unknown" if phase == "applying" else "failed"
        db.cleanup(final)
        return {"phase": final, "ready_to_publish": False}
    applying = False
    key = None
    try:
        key = db.key()
        status_valid(http.call("POST", SB_URL + "archive_search_status", key, {}))
        search_valid(http.call("POST", SB_URL + "archive_search_text", key, {"search_query": "компрессор", "result_limit": 1, "source_kind": None}))
        before = cf_config(http.call("GET", CF_URL, token))
        payload = {"env_vars": {"SUPABASE_SERVICE_KEY": {"type": "secret_text", "value": key}}}
        if "wrangler_config_hash" in before["production"]:
            value = before["production"]["wrangler_config_hash"]
            require(value is None or isinstance(value, str), "HTTP_RESPONSE_INVALID")
            payload["wrangler_config_hash"] = value
        db.applying()  # durable one-PATCH barrier + fresh database TTL check
        applying = True
        cf_config(http.call("PATCH", CF_URL, token, {"deployment_configs": {"production": payload}}))
        after = cf_config(http.call("GET", CF_URL, token))
        require(after["production"]["env_vars"].get("SUPABASE_SERVICE_KEY", {}).get("type") == "secret_text", "CF_CHANGED")
        def unchanged(configs):
            clean = json.loads(json.dumps(configs))
            clean["production"]["env_vars"].pop("SUPABASE_SERVICE_KEY", None)
            return clean
        require(unchanged(before) == unchanged(after), "CF_CHANGED")
        db.cleanup("complete")
        return {"phase": "complete", "ready_to_publish": True}
    except Exception:  # noqa: BLE001 -- privacy boundary: raw errors may contain credentials.
        try:
            db.cleanup("application_unknown" if applying else "failed")
        except Exception:  # noqa: BLE001 -- privacy boundary: raw errors may contain credentials.
            raise RepairError("CLEANUP_FAILED") from None
        raise RepairError("PATCH_UNKNOWN" if applying else "REPAIR_FAILED") from None
    finally:
        key = None


def main(env=None):
    env = os.environ if env is None else env
    result = {"phase": "failed", "ready_to_publish": False}
    try:
        import psycopg2
        from psycopg2.extensions import parse_dsn
        require(env.get("CLOUDFLARE_ACCOUNT_ID") == ACCOUNT, "CONFIG_INVALID")
        token = env.get("CLOUDFLARE_API_TOKEN")
        require(isinstance(token, str) and 1 <= len(token) <= 4096 and not any(c.isspace() for c in token), "CONFIG_INVALID")
        conn = psycopg2.connect(**dsn_params(env.get("SUPABASE_DB_URL"), parse_dsn))
        try:
            result = repair(Database(conn), Http(), token)
        finally:
            conn.close()
    except Exception as error:  # noqa: BLE001 -- output is restricted to constant error codes.
        code = str(error) if isinstance(error, RepairError) and str(error) in SAFE else "REPAIR_FAILED"
        print(json.dumps({"ok": False, "error": code, **result}))
        return 1
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("ready_to_publish=" + ("true" if result["ready_to_publish"] else "false") + "\n")
    print(json.dumps({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
