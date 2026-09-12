#!/usr/bin/env python3
"""Publish the approved private library in memory; never persist or log its contents.

The normal database session is read-only. Only validated owner drafts permit a
separate, bounded insert transaction. KV has no compare-and-swap: the workflow
serializes publishers, preserves both versions, and checks for concurrent edits.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import http.client
import json
import os
import re
import sys
import time
from urllib import error, parse, request
import uuid

PROJECT_REF = "vpjliavuuxjcvtxbthlp"
PAGES_PROJECT = "kvant-sourcing-f122"
MANAGER = "Codex/private-library-v1"
CURRENT_KEY = "library:v1"
DRAFT_PREFIX = "library:draft:"
MAX_BYTES = 4 * 1024 * 1024
MAX_ARTICLES = 5000
MAX_SEGMENTS = 100
MAX_DRAFTS = 100
DRAFT_KEY_LIMIT = 10000
DRAFT_PAGE_LIMIT = 100
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,159}\Z")
CF_ID = re.compile(r"[a-fA-F0-9]{32}\Z")
KINDS = {"knowledge", "supplier", "price", "component"}
READBACK_DELAYS = (0, 2, 5, 15, 30, 30)
TRANSPORT_REASONS = frozenset(("http_error", "timeout", "network_error", "incomplete_http"))
TRANSPORT_HTTP_STATUSES = frozenset((429, 500, 502, 503, 504))
ARTICLE_COLUMNS = "segment_id, title, topic, body, sources, confidence, updated_at"
KNOWLEDGE_SQL = f"""SELECT {ARTICLE_COLUMNS} FROM public.lib_knowledge
WHERE researched_by = %s AND sources->>'publication_approved' = 'true'
ORDER BY sources->>'importer_id', id LIMIT 5001"""
SEGMENTS_SQL = "SELECT id, name, note FROM public.lib_segments ORDER BY id LIMIT 101"
EXISTING_SQL = f"""SELECT researched_by, {ARTICLE_COLUMNS} FROM public.lib_knowledge
WHERE sources->>'importer_id' = ANY(%s) ORDER BY id LIMIT 101 FOR UPDATE"""
INSERT_SQL = """INSERT INTO public.lib_knowledge
(segment_id, title, topic, body, sources, confidence, updated_at, researched_by)
VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)"""


class PublishError(Exception):
    """Only constant safe codes may cross the logging boundary."""


def require(condition, code):
    if not condition:
        raise PublishError(code)


def encode(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise PublishError("INVALID_JSON") from None
    require(len(raw) <= MAX_BYTES, "LIBRARY_TOO_LARGE")
    return raw


def decode(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, "RESPONSE_TOO_LARGE")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(PublishError("INVALID_JSON")))
    except (ValueError, UnicodeError, RecursionError):
        raise PublishError("INVALID_JSON") from None


def stable_id(value):
    require(isinstance(value, str) and ID.fullmatch(value), "INVALID_STABLE_ID")
    return value


def string(value, maximum, optional=False):
    if optional and value is None:
        return ""
    require(isinstance(value, str), "INVALID_FIELD")
    try:
        size = len(value.encode("utf-16-le")) // 2  # Same limits as the portal worker.
    except UnicodeError:
        raise PublishError("INVALID_FIELD") from None
    require(size <= maximum and (optional or bool(value.strip())), "INVALID_FIELD")
    return value


def timestamp(value, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        require(value.tzinfo is not None, "INVALID_TIMESTAMP")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    require(isinstance(value, str) and len(value) <= 40 and
            re.match(r"^\d{4}-\d{2}-\d{2}T", value), "INVALID_TIMESTAMP")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.tzinfo is not None, "INVALID_TIMESTAMP")
    except ValueError:
        raise PublishError("INVALID_TIMESTAMP") from None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


SERVICE_STAGES = ("inspection", "diagnostics", "failure_analysis", "repair", "maintenance", "post_repair_verification")
SERVICE_STAGE_LABELS = {"inspection": "Осмотр", "diagnostics": "Диагностика", "failure_analysis": "Анализ отказов",
    "repair": "Ремонт", "maintenance": "Техническое обслуживание", "post_repair_verification": "Проверка после ремонта"}
# ECMAScript String.trim whitespace: validate the same bytes as the Worker.
SERVICE_WHITESPACE = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def service_knowledge(sources):
    """Validate additive reference knowledge; never normalise or rewrite evidence."""
    if not isinstance(sources, dict) or "service_knowledge" not in sources:
        return None
    value = sources["service_knowledge"]
    code = "INVALID_SERVICE_KNOWLEDGE"

    def fields(obj, names):
        require(isinstance(obj, dict) and set(obj) == set(names.split()), code)

    def text(item, maximum, empty=False, nullable=False):
        if item is None and nullable:
            return
        require(isinstance(item, str), code)
        try:
            length = len(item.encode("utf-16-le")) // 2
        except UnicodeError:
            raise PublishError(code) from None
        require(length <= maximum and (empty or bool(item.strip(SERVICE_WHITESPACE))), code)

    def sequence(items, maximum, minimum=0):
        require(isinstance(items, list) and minimum <= len(items) <= maximum, code)

    def strings(items, count, maximum):
        sequence(items, count)
        for item in items:
            text(item, maximum)

    refs = sources.get("references")
    require(sources.get("kind") == "knowledge" and isinstance(refs, list), code)

    def indices(items, minimum=0):
        sequence(items, 8, minimum)
        # JSON numbers have one type in the Worker: match Number.isSafeInteger
        # without admitting booleans or changing the original numeric value.
        require(all(type(i) in (int, float) and 0 <= i < len(refs) and i == int(i) for i in items), code)
        for i in items:
            ref = refs[int(i)]
            require((isinstance(ref, str) and bool(ref.strip(SERVICE_WHITESPACE))) or
                    (isinstance(ref, dict) and any(isinstance(ref.get(k), str) and ref[k].strip(SERVICE_WHITESPACE)
                     for k in ("title", "name", "url", "sha256", "source_sha256"))), code)

    fields(value, "schema_version equipment_family model_scope assembly service_stage evidence applicability diagnostic_checks repair_decision customer_content engineering_procedure")
    require(type(value["schema_version"]) in (int, float) and value["schema_version"] == 1 and
            value["customer_content"] is False and value["engineering_procedure"] is False, code)
    require(isinstance(value["service_stage"], str) and value["service_stage"] in SERVICE_STAGES, code)
    family = value["equipment_family"]
    if family is not None:
        fields(family, "id label")
        text(family["id"], 80)
        require(ID.fullmatch(family["id"]) is not None, code)
        text(family["label"], 160)
    scope = value["model_scope"]
    fields(scope, "level manufacturers models note")
    require(scope["level"] in ("unknown", "generic", "family", "model"), code)
    strings(scope["manufacturers"], 8, 120)
    strings(scope["models"], 12, 120)
    require(scope["level"] != "model" or bool(scope["models"]), code)
    text(scope["note"], 1000, True)
    text(value["assembly"], 300, nullable=True)
    sequence(value["evidence"], 32, 1)
    for item in value["evidence"]:
        fields(item, "reference_index claim")
        indices([item["reference_index"]], 1)
        text(item["claim"], 1000)
    application = value["applicability"]
    fields(application, "status note limits")
    require(application["status"] in ("unknown", "generic_reference", "source_scoped"), code)
    text(application["note"], 2000, True)
    strings(application["limits"], 20, 1000)
    sequence(value["diagnostic_checks"], 24)
    for item in value["diagnostic_checks"]:
        fields(item, "check method result_interpretation reference_indices basis")
        text(item["check"], 600)
        text(item["method"], 1500, nullable=True)
        text(item["result_interpretation"], 1500, nullable=True)
        require(item["basis"] in ("source_guidance", "proposed_workflow"), code)
        indices(item["reference_indices"], 1)
    decision = value["repair_decision"]
    fields(decision, "status note reference_indices")
    require(decision["status"] in ("not_established", "assessment_framework", "source_guidance"), code)
    text(decision["note"], 2000, True)
    indices(decision["reference_indices"], 0 if decision["status"] == "not_established" else 1)
    require(len(encode(value)) <= 32 * 1024, code)
    return value


def service_summary(sources):
    value = service_knowledge(sources)
    if value is None:
        return None
    return {key: copy.deepcopy(value[key]) for key in
            ("schema_version", "equipment_family", "model_scope", "assembly", "service_stage")}


def article(value):
    require(isinstance(value, dict), "INVALID_ARTICLE")
    # Preserve additional properties on unrelated existing articles and sources.
    result = copy.deepcopy(value)
    result.update(id=stable_id(value.get("id")), segment_id=stable_id(value.get("segment_id")),
                  title=string(value.get("title"), 300), topic=string(value.get("topic"), 200, True),
                  body=string(value.get("body"), 160000),
                  confidence=string("med" if value.get("confidence") is None else value["confidence"], 40),
                  updated_at=timestamp(value.get("updated_at"), True))
    result["sources"] = {} if value.get("sources") is None else copy.deepcopy(value["sources"])
    require(isinstance(result["sources"], (dict, list)), "INVALID_SOURCES")
    service_knowledge(result["sources"])
    encode(result)
    return result


def document(value):
    require(isinstance(value, dict), "INVALID_LIBRARY")
    segments, articles = value.get("segments"), value.get("articles")
    require(isinstance(segments, list) and len(segments) <= MAX_SEGMENTS, "SEGMENT_LIMIT")
    require(isinstance(articles, list) and len(articles) <= MAX_ARTICLES, "ARTICLE_LIMIT")
    normalized_segments = []
    for row in segments:
        require(isinstance(row, dict), "INVALID_SEGMENT")
        normalized_segments.append({**copy.deepcopy(row), "id": stable_id(row.get("id")),
                                    "name": string(row.get("name"), 300), "note": string(row.get("note"), 10000, True)})
    normalized_articles = [article(row) for row in articles]
    ids = [row["id"] for row in normalized_segments]
    require(len(set(ids)) == len(ids), "DUPLICATE_SEGMENT")
    article_ids = [row["id"] for row in normalized_articles]
    require(len(set(article_ids)) == len(article_ids), "DUPLICATE_ARTICLE")
    require(all(row["segment_id"] in set(ids) for row in normalized_articles), "UNKNOWN_SEGMENT")
    result = {"segments": normalized_segments, "articles": normalized_articles}
    encode(result)
    return result


def snapshot(raw):
    if raw is None:
        return {"version": 1, "revision": None, "published_at": None, "segments": [], "articles": []}
    value = decode(raw)
    require(isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1, "INVALID_VERSION")
    stable_id(value.get("revision"))
    timestamp(value.get("published_at"))
    document(value)
    return value


def managed(row):
    sources = row.get("sources")
    return (isinstance(sources, dict) and sources.get("importer_id") == row.get("id")
            and (sources.get("publication_approved") is True or sources.get("publication_approved") == "true"))


def merge(previous, incoming):
    document(previous)
    document(incoming)
    before = {row["id"]: copy.deepcopy(row) for row in previous["articles"]}
    for row in incoming["articles"]:
        require(managed(row), "UNMANAGED_SOURCE_ARTICLE")
        if row["id"] in before:
            require(managed(before[row["id"]]), "MANAGED_ID_CONFLICT")
        before[row["id"]] = copy.deepcopy(row)
    segments = {row["id"]: copy.deepcopy(row) for row in previous["segments"]}
    segments.update({row["id"]: copy.deepcopy(row) for row in incoming["segments"]})
    result = {"segments": list(segments.values()), "articles": list(before.values())}
    document(result)
    return result


def validated_connection(dsn, parser):
    require(isinstance(dsn, str) and bool(dsn), "SUPABASE_DB_URL_MISSING")
    try:
        values = parser(dsn)
    except Exception:
        raise PublishError("INVALID_SUPABASE_DSN") from None
    allowed = {"host", "port", "user", "password", "dbname", "sslmode", "connect_timeout", "application_name"}
    require(isinstance(values, dict) and not (set(values) - allowed), "UNSAFE_SUPABASE_DSN_OPTION")
    host, user = values.get("host", ""), values.get("user", "")
    direct = host == f"db.{PROJECT_REF}.supabase.co"
    pooler = bool(re.fullmatch(r"(?:[a-z0-9-]+\.)*pooler\.supabase\.com", host)) and PROJECT_REF in user
    require((direct or pooler) and bool(user), "UNAPPROVED_SUPABASE_PROJECT")
    require(bool(values.get("password")), "SUPABASE_PASSWORD_MISSING")
    require(values.get("port", "5432") in ("5432", "6543"), "UNAPPROVED_SUPABASE_PORT")
    require(values.get("dbname", "postgres") == "postgres", "UNAPPROVED_SUPABASE_DATABASE")
    require(values.get("sslmode", "require") == "require", "SUPABASE_SSL_REQUIRED")
    return {key: values[key] for key in ("host", "port", "user", "password", "dbname") if key in values} | {
        "dbname": "postgres", "port": values.get("port", "5432"),
        "sslmode": "require", "connect_timeout": 10, "application_name": "private-library-publisher",
        "options": "-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=5000"}


def db_article(row):
    require(isinstance(row, (tuple, list)) and len(row) == 7, "INVALID_DATABASE_ROW")
    result = dict(zip(("segment_id", "title", "topic", "body", "sources", "confidence", "updated_at"), row))
    require(isinstance(result["sources"], dict), "INVALID_MANAGED_SOURCES")
    result["id"] = result["sources"].get("importer_id")
    result["updated_at"] = timestamp(result["updated_at"], True)
    return article(result)


class Database:
    def __init__(self, dsn, driver=None):
        require(not any(os.environ.get(name) for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")),
                "UNSAFE_POSTGRES_ENV")
        if driver is None:
            try:
                import psycopg2 as driver
                from psycopg2.extensions import parse_dsn
            except ImportError:
                raise PublishError("PSYCOPG2_REQUIRED") from None
        else:
            parse_dsn = driver.parse_dsn
        self.driver = driver
        self.parameters = validated_connection(dsn, parse_dsn)

    def connect(self, readonly=True):
        connection = self.driver.connect(**self.parameters)
        try:
            connection.set_session(readonly=readonly, autocommit=False, isolation_level="REPEATABLE READ")
        except Exception:
            connection.close()
            raise
        return connection

    def read(self):
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute(SEGMENTS_SQL)
                rows = cursor.fetchmany(MAX_SEGMENTS + 1)
                require(len(rows) <= MAX_SEGMENTS, "SEGMENT_LIMIT")
                segments = [{"id": row[0], "name": row[1], "note": row[2] or ""} for row in rows]
                cursor.execute(KNOWLEDGE_SQL, (MANAGER,))
                rows = cursor.fetchmany(MAX_ARTICLES + 1)
                require(len(rows) <= MAX_ARTICLES, "ARTICLE_LIMIT")
                articles = [db_article(row) for row in rows]
            result = document({"segments": segments, "articles": articles})
            require(all(managed(row) for row in result["articles"]), "UNAPPROVED_DATABASE_ROW")
            return result
        except PublishError:
            raise
        except Exception:
            raise PublishError("DATABASE_READ_FAILED") from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()

    def insert_drafts(self, articles):
        if not articles:
            return
        require(len(articles) <= MAX_DRAFTS, "DRAFT_LIMIT")
        connection = None
        try:
            connection = self.connect(readonly=False)
            with connection.cursor() as cursor:
                # Fixed transaction advisory lock serializes this importer, in addition
                # to Actions concurrency. Manual edits are never overwritten.
                cursor.execute("SELECT pg_advisory_xact_lock(18247, 1)")
                cursor.execute(EXISTING_SQL, ([row["id"] for row in articles],))
                old_rows = cursor.fetchmany(MAX_DRAFTS + 1)
                require(len(old_rows) <= MAX_DRAFTS, "DRAFT_ID_CONFLICT")
                existing = {}
                for row in old_rows:
                    require(row[0] == MANAGER, "DRAFT_ID_CONFLICT")
                    old = db_article(row[1:])
                    require(old["id"] not in existing, "DRAFT_ID_CONFLICT")
                    existing[old["id"]] = old
                for row in articles:
                    if row["id"] in existing:
                        require(encode(existing[row["id"]]) == encode(row), "DRAFT_ID_CONFLICT")
                        continue
                    cursor.execute(INSERT_SQL, (row["segment_id"], row["title"], row["topic"], row["body"],
                        encode(row["sources"]).decode("utf-8"), row["confidence"], row["updated_at"], MANAGER))
            connection.commit()
        except PublishError:
            raise
        except Exception:
            raise PublishError("DATABASE_DRAFT_WRITE_FAILED") from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PublishError("CLOUDFLARE_REDIRECT_REJECTED")


class Cloudflare:
    def __init__(self, account, token, opener=None, sleep=time.sleep):
        require(isinstance(account, str) and CF_ID.fullmatch(account), "INVALID_CLOUDFLARE_ACCOUNT")
        require(isinstance(token, str) and bool(token) and "\r" not in token and "\n" not in token, "CLOUDFLARE_TOKEN_MISSING")
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{account}"
        self.token = token
        self.opener = opener or request.build_opener(request.ProxyHandler({}), NoRedirect())
        self.sleep = sleep

    def call(self, method, path, body=None, missing=False):
        require(method in ("GET", "PUT") and path.startswith("/") and not path.startswith("//"), "INVALID_API_REQUEST")
        if body is not None:
            require(isinstance(body, bytes) and len(body) <= MAX_BYTES, "LIBRARY_TOO_LARGE")
        attempts = 3 if method == "GET" else 1
        transport_reason, transport_status = None, None
        for attempt in range(attempts):
            req = request.Request(self.base + path, data=body, method=method,
                headers={"Authorization": "Bearer " + self.token, "Accept": "application/json", "Content-Type": "application/json"})
            try:
                with self.opener.open(req, timeout=30) as response:
                    status = response.status
                    require(status == 200, "CLOUDFLARE_UNEXPECTED_STATUS")
                    length = response.headers.get("Content-Length")
                    if length is not None:
                        require(length.isdigit() and int(length) <= MAX_BYTES, "RESPONSE_TOO_LARGE")
                    raw = response.read(MAX_BYTES + 1)
                    require(len(raw) <= MAX_BYTES, "RESPONSE_TOO_LARGE")
                    require(length is None or len(raw) == int(length), "INCOMPLETE_HTTP_RESPONSE")
                    return raw
            except error.HTTPError as failure:
                status = failure.code
                failure.close()
                if status == 404 and missing:
                    return None
                if status in (401, 403):
                    raise PublishError("CLOUDFLARE_KV_ACCESS_DENIED" if "/storage/kv/" in path else "CLOUDFLARE_PROJECT_ACCESS_DENIED") from None
                if 300 <= status < 400:
                    raise PublishError("CLOUDFLARE_REDIRECT_REJECTED") from None
                if status != 429 and status not in (500, 502, 503, 504):
                    raise PublishError("CLOUDFLARE_HTTP_FAILED") from None
                transport_reason, transport_status = "http_error", status
            except (error.URLError, TimeoutError, OSError, http.client.HTTPException) as failure:
                transport_status = None
                if isinstance(failure, TimeoutError) or (isinstance(failure, error.URLError) and
                                                         isinstance(failure.reason, TimeoutError)):
                    transport_reason = "timeout"
                elif isinstance(failure, http.client.HTTPException):
                    transport_reason = "incomplete_http"
                else:
                    transport_reason = "network_error"
            if method == "PUT":
                # A timeout may follow a successful write. Never repeat a PUT
                # blindly over a later editor; inspect the exact value instead.
                uncertain = PublishError("CLOUDFLARE_WRITE_OUTCOME_UNCONFIRMED")
                uncertain.transport_reason = transport_reason
                uncertain.transport_http_status = transport_status
                raise uncertain from None
            if attempt < attempts - 1:
                self.sleep((2, 5)[attempt])
        exhausted = PublishError("CLOUDFLARE_RETRIES_EXHAUSTED")
        exhausted.transport_reason = transport_reason
        exhausted.transport_http_status = transport_status
        raise exhausted from None

    def envelope(self, method, path, body=None):
        value = decode(self.call(method, path, body))
        require(isinstance(value, dict) and value.get("success") is True, "CLOUDFLARE_API_FAILED")
        return value

    def namespace(self):
        result = self.envelope("GET", f"/pages/projects/{PAGES_PROJECT}").get("result")
        try:
            namespaces = result["deployment_configs"]["production"]["kv_namespaces"]
            require(isinstance(namespaces, dict), "INVALID_KV_BINDING")
            binding = namespaces.get("ACL") if "ACL" in namespaces else namespaces.get("VISITS")
            namespace = binding["namespace_id"]
        except (KeyError, TypeError):
            raise PublishError("KV_BINDING_MISSING") from None
        require(isinstance(namespace, str) and CF_ID.fullmatch(namespace), "INVALID_KV_BINDING")
        return namespace

    def value_path(self, namespace, key):
        require(isinstance(namespace, str) and CF_ID.fullmatch(namespace), "INVALID_KV_BINDING")
        require(key == CURRENT_KEY or (key.startswith("library:history:") and ID.fullmatch(key[16:])) or
                (key.startswith(DRAFT_PREFIX) and ID.fullmatch(key[len(DRAFT_PREFIX):])), "INVALID_KV_KEY")
        return f"/storage/kv/namespaces/{namespace}/values/{parse.quote(key, safe='')}"

    def get(self, namespace, key):
        return self.call("GET", self.value_path(namespace, key), missing=True)

    def put(self, namespace, key, raw):
        try:
            self.envelope("PUT", self.value_path(namespace, key), raw)
        except PublishError as failure:
            if str(failure) != "CLOUDFLARE_WRITE_OUTCOME_UNCONFIRMED":
                raise
            self.verify(namespace, key, raw)

    def verify(self, namespace, key, expected):
        for delay in READBACK_DELAYS:
            if delay:
                self.sleep(delay)
            if self.get(namespace, key) == expected:
                return
        raise PublishError("KV_READBACK_NOT_CONFIRMED")

    def preserve(self, namespace, key, raw):
        old = self.get(namespace, key)
        require(old is None or old == raw, "HISTORY_REVISION_CONFLICT")
        if old is None:
            self.put(namespace, key, raw)
        self.verify(namespace, key, raw)

    def draft_keys(self, namespace):
        keys, seen_cursors, cursor = [], set(), None
        for _ in range(DRAFT_PAGE_LIMIT):
            query = {"prefix": DRAFT_PREFIX, "limit": "100"}
            if cursor:
                query["cursor"] = cursor
            value = self.envelope("GET", f"/storage/kv/namespaces/{namespace}/keys?{parse.urlencode(query)}")
            rows = value.get("result")
            require(isinstance(rows, list), "INVALID_DRAFT_LIST")
            for row in rows:
                key = row.get("name") if isinstance(row, dict) else None
                require(isinstance(key, str) and key.startswith(DRAFT_PREFIX), "INVALID_DRAFT_KEY")
                stable_id(key[len(DRAFT_PREFIX):])
                require(key not in keys, "REPEATED_DRAFT_KEY")
                keys.append(key)
                require(len(keys) <= DRAFT_KEY_LIMIT, "DRAFT_KEY_LIMIT")
            info = value.get("result_info")
            require(isinstance(info, dict), "INVALID_DRAFT_PAGINATION")
            cursor = info.get("cursor")
            if not cursor:
                return keys
            require(isinstance(cursor, str) and len(cursor) <= 2048 and cursor not in seen_cursors and bool(rows), "INVALID_DRAFT_PAGINATION")
            seen_cursors.add(cursor)
        raise PublishError("DRAFT_PAGE_LIMIT")


def pending_drafts(cf, namespace, segments, metrics=None):
    known_segments = {row["id"] for row in segments}
    result = []
    keys = cf.draft_keys(namespace)
    if metrics is not None:
        metrics.update(draft_keys_seen=len(keys), draft_keys_deferred=0)
    for index, key in enumerate(keys):
        if len(result) == MAX_DRAFTS:
            if metrics is not None:
                metrics["draft_keys_deferred"] = len(keys) - index
            break
        raw = cf.get(namespace, key)
        require(raw is not None, "DRAFT_DISAPPEARED")
        value = decode(raw)
        require(isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1, "INVALID_DRAFT")
        require(value.get("status") in ("pending", "published", "error"), "INVALID_DRAFT_STATUS")
        original = article(value.get("article"))
        require(key == DRAFT_PREFIX + original["id"], "DRAFT_KEY_MISMATCH")
        if value["status"] != "pending":
            continue
        timestamp(value.get("created_at"))
        row = copy.deepcopy(original)
        require(row["segment_id"] in known_segments, "DRAFT_UNKNOWN_SEGMENT")
        sources = row["sources"]
        require(isinstance(sources, dict) and sources.get("kind") in KINDS and
                isinstance(sources.get("typedfields", {}), dict), "INVALID_DRAFT_SOURCES")
        require(sources.get("importer_id", row["id"]) == row["id"], "DRAFT_KEY_MISMATCH")
        sources.update(importer_id=row["id"], publication_approved=True, origin="portal-owner-draft")
        row["updated_at"] = timestamp(row["updated_at"] or value["created_at"])
        result.append({"key": key, "raw": raw, "envelope": value, "article": row})
    return result


def publish(cf, namespace, old_raw, incoming, now=None):
    previous = snapshot(old_raw)
    merged = merge(previous, incoming)
    if encode({"segments": previous["segments"], "articles": previous["articles"]}) == encode(merged):
        cf.verify(namespace, CURRENT_KEY, old_raw)
        return previous, False
    value = {**copy.deepcopy(previous), "version": 1, "revision": str(uuid.uuid4()),
             "published_at": timestamp(now or datetime.now(timezone.utc)), **merged}
    new_raw = encode(value)
    require(cf.get(namespace, CURRENT_KEY) == old_raw, "CURRENT_LIBRARY_CHANGED")
    if previous["revision"]:
        cf.preserve(namespace, "library:history:" + previous["revision"], old_raw)
    cf.preserve(namespace, "library:history:" + value["revision"], new_raw)
    require(cf.get(namespace, CURRENT_KEY) == old_raw, "CURRENT_LIBRARY_CHANGED")
    cf.put(namespace, CURRENT_KEY, new_raw)
    cf.verify(namespace, CURRENT_KEY, new_raw)
    return value, True


def run(db, cf):
    namespace = cf.namespace()
    old_raw = cf.get(namespace, CURRENT_KEY)
    previous = snapshot(old_raw)
    incoming = db.read()
    draft_metrics = {}
    drafts = pending_drafts(cf, namespace, incoming["segments"], draft_metrics)
    # Check all conflicts and combined capacity before permitting any DB insert.
    prospective = merge({"segments": incoming["segments"], "articles": incoming["articles"]},
                        {"segments": incoming["segments"], "articles": [item["article"] for item in drafts]})
    preflight = {**merge(previous, prospective), "version": 1, "revision": str(uuid.uuid4()),
                 "published_at": timestamp(datetime.now(timezone.utc))}
    encode(preflight)
    for item in drafts:
        require(cf.get(namespace, item["key"]) == item["raw"], "DRAFT_CHANGED")
    if drafts:
        db.insert_drafts([item["article"] for item in drafts])
        incoming = db.read()
        actual = {row["id"]: row for row in incoming["articles"]}
        for item in drafts:
            require(encode(actual.get(item["article"]["id"])) == encode(item["article"]), "DATABASE_READBACK_MISMATCH")
    value, changed = publish(cf, namespace, old_raw, incoming)
    # No deletion: an interrupted run remains recoverable and insert-idempotent.
    for item in drafts:
        require(cf.get(namespace, item["key"]) == item["raw"], "DRAFT_CHANGED")
        completed = {**item["envelope"], "status": "published", "published_at": timestamp(datetime.now(timezone.utc)),
                     "publication_revision": value["revision"]}
        raw = encode(completed)
        cf.put(namespace, item["key"], raw)
        cf.verify(namespace, item["key"], raw)
    result = {"ok": True, "changed": changed, "segments": len(value["segments"]), "articles": len(value["articles"]),
              "drafts_published": len(drafts)}
    if draft_metrics["draft_keys_deferred"]:
        result.update(draft_keys_deferred=draft_metrics["draft_keys_deferred"], next_manual_run_required=True)
    return result


def main(environ=None):
    env = os.environ if environ is None else environ
    try:
        cf = Cloudflare(env.get("CLOUDFLARE_ACCOUNT_ID"), env.get("CLOUDFLARE_API_TOKEN"))
        db = Database(env.get("SUPABASE_DB_URL"))
        result = run(db, cf)
    except PublishError as failure:
        result = {"ok": False, "error": str(failure)}
        if str(failure) == "CLOUDFLARE_KV_ACCESS_DENIED":
            result["message"] = "The existing Cloudflare token needs Workers KV Storage read/write access to the configured namespace."
    except KeyboardInterrupt:
        result = {"ok": False, "error": "INTERRUPTED"}
    except Exception:
        result = {"ok": False, "error": "PUBLISH_FAILED"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
