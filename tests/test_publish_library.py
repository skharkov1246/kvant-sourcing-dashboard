"""Synthetic private-publisher contracts. No credentials, files of content, or network."""
from collections import deque
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import types
from urllib import error, parse

import pytest

from scripts import publish_library as p

ACCOUNT = "a" * 32
NAMESPACE = "b" * 32
NOW = "2026-09-11T12:00:00Z"
SEGMENT = {"id": "equipment", "name": "Synthetic equipment", "note": ""}


def item(sid="research:one", **changes):
    value = {"id": sid, "segment_id": "equipment", "title": "Synthetic research", "topic": "selection",
             "body": "Synthetic private body", "sources": {"importer_id": sid, "publication_approved": True,
             "kind": "knowledge", "typedfields": {}, "references": [{"label": "Synthetic source", "date": NOW}]},
             "confidence": "high", "updated_at": NOW}
    value.update(changes)
    return value


def doc(articles=None, segments=None):
    return {"segments": [copy.deepcopy(SEGMENT)] if segments is None else segments,
            "articles": [item()] if articles is None else articles}


def old_snapshot(articles=None, segments=None):
    return {"version": 1, "revision": "old-revision", "published_at": NOW, **doc(articles, segments)}


def draft(sid="draft:test-one", **changes):
    row = item(sid, updated_at="2026-09-11T12:00:00.123Z")
    row["sources"] = {"kind": "supplier", "typedfields": {"country": "Fixture", "terms": ["sample"]}}
    value = {"version": 1, "status": "pending", "created_at": NOW, "article": row, "extra_envelope": {"retain": True}}
    value.update(changes)
    return value


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class Response:
    def __init__(self, value, length=None):
        self.body = value
        self.status = 200
        self.headers = {"Content-Length": str(len(value) if length is None else length)}
        self.read_limits = []

    def read(self, limit):
        self.read_limits.append(limit)
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Transport:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []
        self.bindings = {"ACL": {"namespace_id": NAMESPACE}}
        self.pages = None
        self.stale = {}
        self.on_put = None
        self.faults = deque()

    def open(self, req, timeout):
        assert timeout == 30
        assert req.full_url.startswith(f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/")
        assert req.get_header("Authorization") == "Bearer synthetic-token"
        u = parse.urlsplit(req.full_url)
        self.calls.append((req.get_method(), u.path, u.query, req.data))
        if self.faults:
            fault = self.faults.popleft()
            if isinstance(fault, Exception):
                raise fault
            return fault
        if u.path.endswith("/pages/projects/" + p.PAGES_PROJECT):
            assert req.get_method() == "GET"
            return Response(raw({"success": True, "result": {"deployment_configs": {"production": {"kv_namespaces": self.bindings}}}}))
        if u.path.endswith("/keys"):
            query = parse.parse_qs(u.query)
            assert query["prefix"] == [p.DRAFT_PREFIX]
            assert query["limit"] == ["100"]
            if self.pages is not None:
                rows, cursor = self.pages.pop(0)
            else:
                rows, cursor = [{"name": k} for k in sorted(self.values) if k.startswith(p.DRAFT_PREFIX)], ""
            return Response(raw({"success": True, "result": rows, "result_info": {"cursor": cursor}}))
        key = parse.unquote(u.path.split("/values/", 1)[1])
        assert u.path.endswith(parse.quote(key, safe=""))
        if req.get_method() == "GET":
            sequence = self.stale.get(key)
            data = sequence.popleft() if sequence else self.values.get(key)
            if data is None:
                raise error.HTTPError(req.full_url, 404, "synthetic private error", {}, io.BytesIO(b"do not log"))
            return Response(data)
        assert req.get_method() == "PUT"
        if self.on_put is None or self.on_put(key, req.data):
            self.values[key] = req.data
        return Response(raw({"success": True, "result": None}))

    @property
    def written_keys(self):
        return [parse.unquote(path.split("/values/", 1)[1]) for method, path, _, _ in self.calls if method == "PUT"]


def cloudflare(transport=None):
    transport = transport or Transport()
    sleeps = []
    return p.Cloudflare(ACCOUNT, "synthetic-token", opener=transport, sleep=sleeps.append), transport, sleeps


def to_db(row):
    date = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")) if row["updated_at"] else None
    return (row["segment_id"], row["title"], row["topic"], row["body"], copy.deepcopy(row["sources"]), row["confidence"], date)


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        c, d = self.connection, self.connection.driver
        d.statements.append((sql, params, c.readonly))
        if d.fail:
            raise RuntimeError("SECRET-DB-CONTENT and password must never appear")
        if sql == p.SEGMENTS_SQL:
            self.rows = [(s["id"], s["name"], s["note"]) for s in d.segments]
        elif sql == p.KNOWLEDGE_SQL:
            assert params == (p.MANAGER,)
            self.rows = [to_db(row) for manager, row in d.rows if manager == p.MANAGER and p.managed(row)]
        elif sql == p.EXISTING_SQL:
            assert c.readonly is False
            self.rows = [(manager, *to_db(row)) for manager, row in d.rows if row["sources"]["importer_id"] in params[0]]
        elif sql == p.INSERT_SQL:
            assert c.readonly is False
            row = p.db_article((params[0], params[1], params[2], params[3], json.loads(params[4]), params[5],
                                datetime.fromisoformat(params[6].replace("Z", "+00:00"))))
            c.pending.append((params[7], row))
        else:
            assert sql == "SELECT pg_advisory_xact_lock(18247, 1)" and c.readonly is False
            self.rows = [(None,)]

    def fetchmany(self, size):
        assert size in (101, p.MAX_ARTICLES + 1)
        return self.rows[:size]


class Connection:
    def __init__(self, driver):
        self.driver = driver
        self.readonly = None
        self.pending = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def set_session(self, **options):
        assert options["autocommit"] is False and options["isolation_level"] == "REPEATABLE READ"
        self.readonly = options["readonly"]

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.driver.rows.extend(self.pending)
        self.pending = []
        self.commits += 1

    def rollback(self):
        self.pending = []
        self.rollbacks += 1

    def close(self):
        self.closed = True


class Driver:
    def __init__(self, articles=None):
        self.rows = [(p.MANAGER, row) for row in (articles if articles is not None else [item()])]
        self.segments = [copy.deepcopy(SEGMENT)]
        self.connections = []
        self.statements = []
        self.fail = False
        self.parameters = {"host": f"db.{p.PROJECT_REF}.supabase.co", "user": "postgres", "password": "synthetic-password"}

    def parse_dsn(self, dsn):
        assert dsn == "synthetic-dsn"
        return self.parameters

    def connect(self, **options):
        assert options["sslmode"] == "require" and options["connect_timeout"] == 10
        assert "default_transaction_read_only=on" in options["options"]
        assert "statement_timeout=15000" in options["options"]
        c = Connection(self)
        self.connections.append(c)
        return c


def database(driver=None):
    driver = driver or Driver()
    return p.Database("synthetic-dsn", driver=driver), driver


def test_normal_read_is_readonly_filtered_bounded_and_closed():
    db, driver = database()
    assert db.read() == doc()
    assert "sources->>'publication_approved' = 'true'" in p.KNOWLEDGE_SQL
    assert "LIMIT 5001" in p.KNOWLEDGE_SQL and "LIMIT 101" in p.SEGMENTS_SQL
    assert driver.connections[0].readonly is True
    assert driver.connections[0].commits == 0 and driver.connections[0].closed
    assert all(sql.startswith("SELECT") and readonly is True for sql, _, readonly in driver.statements)


@pytest.mark.parametrize("options", [
    {"host": "db.other.supabase.co"}, {"host": "db.vpjliavuuxjcvtxbthlp.supabase.co.attacker.test"},
    {"host": "aws-0.pooler.supabase.com", "user": "postgres.other"}, {"hostaddr": "127.0.0.1"},
    {"service": "override"}, {"options": "-c transaction_read_only=off"}, {"sslmode": "disable"},
    {"port": "1234"}, {"dbname": "other"}, {"host": "localhost"},
])
def test_dsn_rejects_unapproved_project_and_overrides(options):
    driver = Driver(); driver.parameters.update(options)
    with pytest.raises(p.PublishError):
        database(driver)
    assert driver.connections == []


def test_pooler_project_username_and_forced_timeouts():
    driver = Driver(); driver.parameters.update(host="aws-0-eu-central-1.pooler.supabase.com", user="postgres." + p.PROJECT_REF, port="6543", connect_timeout="999")
    db, _ = database(driver)
    assert db.parameters["connect_timeout"] == 10
    assert db.parameters["dbname"] == "postgres"
    db.read()


def test_libpq_environment_cannot_override_the_approved_host(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    with pytest.raises(p.PublishError, match="UNSAFE_POSTGRES_ENV"):
        database()


@pytest.mark.parametrize("kind,count", [("articles", p.MAX_ARTICLES + 1), ("segments", 101)])
def test_database_rejects_limit_sentinel(kind, count):
    driver = Driver()
    if kind == "articles":
        driver.rows = [(p.MANAGER, item(f"id:{i}")) for i in range(count)]
    else:
        driver.segments = [{**SEGMENT, "id": f"segment:{i}"} for i in range(count)]
    db, _ = database(driver)
    with pytest.raises(p.PublishError, match="LIMIT"):
        db.read()
    assert driver.connections[0].commits == 0 and driver.connections[0].closed


def test_merge_retains_unrelated_fields_and_sources_without_mutation():
    unrelated = item("manual", sources=[{"nested": [1, "keep"]}], extra={"retain": True})
    before = old_snapshot([unrelated, item()]); frozen = copy.deepcopy(before)
    incoming = doc([item(body="Updated synthetic body")])
    merged = p.merge(before, incoming)
    assert merged["articles"][0] == unrelated and before == frozen
    assert merged["articles"][1] == incoming["articles"][0]


def test_unmanaged_same_id_conflict_is_not_overwritten():
    before = old_snapshot([item(sources={"manual": True})])
    with pytest.raises(p.PublishError, match="MANAGED_ID_CONFLICT"):
        p.merge(before, doc())


@pytest.mark.parametrize("change", [{"id": "../escape"}, {"id": "x" * 161}, {"segment_id": "missing"},
                                    {"title": "😀" * 151}, {"body": "x" * 160001}, {"sources": "invalid"}])
def test_article_and_reference_validation(change):
    with pytest.raises(p.PublishError):
        p.document(doc([item(**change)]))


def test_duplicates_and_utf8_payload_limit_are_rejected():
    with pytest.raises(p.PublishError, match="DUPLICATE_ARTICLE"):
        p.document(doc([item(), item()]))
    with pytest.raises(p.PublishError, match="LIBRARY_TOO_LARGE"):
        p.encode({"body": "я" * (p.MAX_BYTES // 2)})
    with pytest.raises(p.PublishError, match="DUPLICATE_JSON_KEY"):
        p.decode(b'{"version":1,"version":2}')


def test_namespace_uses_acl_or_visits_without_configuration_writes():
    cf, transport, _ = cloudflare()
    assert cf.namespace() == NAMESPACE
    transport.bindings = {"VISITS": {"namespace_id": NAMESPACE}}
    assert cf.namespace() == NAMESPACE
    transport.bindings["ACL"] = {}
    with pytest.raises(p.PublishError, match="KV_BINDING_MISSING"):
        cf.namespace()
    assert all(call[0] == "GET" for call in transport.calls)


def test_history_is_exact_raw_and_saved_before_new_current():
    before = old_snapshot(); before["additional_metadata"] = {"retain": True}
    exact = json.dumps(before, indent=3).encode()
    cf, transport, _ = cloudflare(Transport({p.CURRENT_KEY: exact}))
    result, changed = p.publish(cf, NAMESPACE, exact, doc([item(body="new")]))
    assert changed and re.fullmatch(r"[a-f0-9-]{36}", result["revision"])
    assert transport.values["library:history:old-revision"] == exact
    assert transport.written_keys == ["library:history:old-revision", "library:history:" + result["revision"], p.CURRENT_KEY]
    assert transport.values[p.CURRENT_KEY] == transport.values["library:history:" + result["revision"]]
    assert result["additional_metadata"] == before["additional_metadata"]


def test_equal_articles_and_segments_is_noop_even_if_raw_whitespace_differs():
    before = old_snapshot(); exact = json.dumps(before, indent=4).encode()
    cf, transport, _ = cloudflare(Transport({p.CURRENT_KEY: exact}))
    result, changed = p.publish(cf, NAMESPACE, exact, doc())
    assert not changed and result == before and transport.written_keys == []


def test_conflicting_history_is_never_overwritten():
    exact = raw(old_snapshot())
    cf, transport, _ = cloudflare(Transport({p.CURRENT_KEY: exact, "library:history:old-revision": b"different exact bytes"}))
    with pytest.raises(p.PublishError, match="HISTORY_REVISION_CONFLICT"):
        p.publish(cf, NAMESPACE, exact, doc([item(body="new")]))
    assert transport.written_keys == [] and transport.values[p.CURRENT_KEY] == exact


def test_concurrent_current_edit_after_history_aborts_pointer_update():
    exact = raw(old_snapshot()); cf, transport, _ = cloudflare(Transport({p.CURRENT_KEY: exact}))
    def mutate_on_backup(key, data):
        if key.startswith("library:history:"):
            transport.values[p.CURRENT_KEY] = raw(old_snapshot([item("other")]))
        return True
    transport.on_put = mutate_on_backup
    with pytest.raises(p.PublishError, match="CURRENT_LIBRARY_CHANGED"):
        p.publish(cf, NAMESPACE, exact, doc([item(body="new")]))
    assert p.CURRENT_KEY not in transport.written_keys


def test_eventual_consistency_verification_is_bounded_and_readonly():
    cf, transport, sleeps = cloudflare(Transport({p.CURRENT_KEY: b"new"}))
    transport.stale[p.CURRENT_KEY] = deque([b"old", None, b"new"])
    cf.verify(NAMESPACE, p.CURRENT_KEY, b"new")
    assert sleeps == [2, 5] and transport.written_keys == []
    with pytest.raises(p.PublishError, match="KV_READBACK_NOT_CONFIRMED"):
        cf.verify(NAMESPACE, p.CURRENT_KEY, b"never observed")
    assert len(transport.calls) == 3 + len(p.READBACK_DELAYS)


@pytest.mark.parametrize("response,code", [(Response(b"{}", 3), "INCOMPLETE_HTTP_RESPONSE"),
                                         (Response(b"x", p.MAX_BYTES + 1), "RESPONSE_TOO_LARGE")])
def test_http_body_length_and_limit(response, code):
    cf, transport, _ = cloudflare(); transport.faults.append(response)
    with pytest.raises(p.PublishError, match=code):
        cf.get(NAMESPACE, p.CURRENT_KEY)
    assert all(limit <= p.MAX_BYTES + 1 for limit in response.read_limits)


def test_http_permission_and_redirect_are_not_retried():
    for status, code in [(403, "CLOUDFLARE_KV_ACCESS_DENIED"), (302, "CLOUDFLARE_REDIRECT_REJECTED")]:
        cf, transport, sleeps = cloudflare()
        transport.faults.append(error.HTTPError("https://invalid.example", status, "private", {}, io.BytesIO(b"private")))
        with pytest.raises(p.PublishError, match=code):
            cf.get(NAMESPACE, p.CURRENT_KEY)
        assert len(transport.calls) == 1 and sleeps == []
    with pytest.raises(p.PublishError, match="REDIRECT"):
        p.NoRedirect().redirect_request(None, None, None, None, None)


def test_http_transport_retry_has_a_cap():
    cf, transport, sleeps = cloudflare()
    transport.faults.extend([error.URLError("SECRET URL") for _ in range(3)])
    with pytest.raises(p.PublishError, match="RETRIES_EXHAUSTED"):
        cf.get(NAMESPACE, p.CURRENT_KEY)
    assert len(transport.calls) == 3 and sleeps == [2, 5]


def test_uncertain_put_is_read_back_without_repeating_a_write():
    cf, transport, _ = cloudflare(Transport({p.CURRENT_KEY: b"committed"}))
    transport.faults.append(error.URLError("response lost after remote commit"))
    cf.put(NAMESPACE, p.CURRENT_KEY, b"committed")
    assert transport.written_keys == [p.CURRENT_KEY]
    assert [call[0] for call in transport.calls] == ["PUT", "GET"]


def test_uncertain_put_never_overwrites_a_concurrent_new_value():
    cf, transport, _ = cloudflare(Transport({p.CURRENT_KEY: b"later editor"}))
    transport.faults.append(error.URLError("response lost"))
    with pytest.raises(p.PublishError, match="KV_READBACK_NOT_CONFIRMED"):
        cf.put(NAMESPACE, p.CURRENT_KEY, b"mine")
    assert transport.written_keys == [p.CURRENT_KEY]
    assert transport.values[p.CURRENT_KEY] == b"later editor"


def test_pending_draft_is_committed_read_back_published_then_marked_without_losing_envelope():
    d = draft(); key = p.DRAFT_PREFIX + d["article"]["id"]
    cf, transport, _ = cloudflare(Transport({key: raw(d)})); db, driver = database()
    result = p.run(db, cf)
    assert result == {"ok": True, "changed": True, "segments": 1, "articles": 2, "drafts_published": 1}
    assert [c.readonly for c in driver.connections] == [True, False, True]
    assert driver.connections[1].commits == 1
    completed = json.loads(transport.values[key])
    assert completed["article"] == d["article"] and completed["extra_envelope"] == d["extra_envelope"]
    assert completed["status"] == "published"
    snap = json.loads(transport.values[p.CURRENT_KEY]); inserted = next(a for a in snap["articles"] if a["id"] == d["article"]["id"])
    assert inserted["sources"]["typedfields"] == d["article"]["sources"]["typedfields"]
    assert inserted["sources"]["origin"] == "portal-owner-draft" and inserted["sources"]["publication_approved"] is True
    assert inserted["updated_at"] == "2026-09-11T12:00:00.123000Z"
    assert transport.written_keys[-1] == key


def test_committed_pending_draft_retry_is_idempotent():
    d = draft(); key = p.DRAFT_PREFIX + d["article"]["id"]
    cf, transport, _ = cloudflare(Transport({key: raw(d)})); db, driver = database()
    articles = [row["article"] for row in p.pending_drafts(cf, NAMESPACE, driver.segments)]
    db.insert_drafts(articles)
    count = len(driver.rows)
    p.run(db, cf)
    assert len(driver.rows) == count
    assert sum(sql == p.INSERT_SQL for sql, _, _ in driver.statements) == 1


@pytest.mark.parametrize("manager,different", [("human", False), (p.MANAGER, True)])
def test_draft_existing_conflict_rolls_back_and_preserves_pending(manager, different):
    d = draft(); key = p.DRAFT_PREFIX + d["article"]["id"]
    cf, transport, _ = cloudflare(Transport({key: raw(d)})); db, driver = database()
    row = p.pending_drafts(cf, NAMESPACE, driver.segments)[0]["article"]
    if different:
        row["body"] = "Existing different private body"
    driver.rows.append((manager, row))
    with pytest.raises(p.PublishError, match="DRAFT_ID_CONFLICT"):
        p.run(db, cf)
    assert transport.values[key] == raw(d) and transport.written_keys == []
    assert driver.connections[-1].commits == 0


def test_failed_current_readback_does_not_mark_draft_published():
    d = draft(); key = p.DRAFT_PREFIX + d["article"]["id"]
    cf, transport, _ = cloudflare(Transport({key: raw(d)})); db, driver = database()
    transport.on_put = lambda key, _: key != p.CURRENT_KEY
    with pytest.raises(p.PublishError, match="KV_READBACK_NOT_CONFIRMED"):
        p.run(db, cf)
    assert json.loads(transport.values[key])["status"] == "pending"
    assert driver.connections[1].commits == 1 and key not in transport.written_keys


def test_draft_changed_after_publication_is_not_overwritten():
    d = draft(); key = p.DRAFT_PREFIX + d["article"]["id"]
    cf, transport, _ = cloudflare(Transport({key: raw(d)})); db, _ = database()
    modified = raw(draft(extra_envelope={"later": True}))
    def change(key_written, _):
        if key_written == p.CURRENT_KEY:
            transport.values[key] = modified
        return True
    transport.on_put = change
    with pytest.raises(p.PublishError, match="DRAFT_CHANGED"):
        p.run(db, cf)
    assert transport.values[key] == modified and key not in transport.written_keys


@pytest.mark.parametrize("change", ["wrong-key", "unknown-segment", "wrong-kind", "typed-list"])
def test_invalid_draft_never_opens_write_session(change):
    d = draft(); key = p.DRAFT_PREFIX + d["article"]["id"]
    if change == "wrong-key": key += "x"
    if change == "unknown-segment": d["article"]["segment_id"] = "not-in-database"
    if change == "wrong-kind": d["article"]["sources"]["kind"] = "unknown"
    if change == "typed-list": d["article"]["sources"]["typedfields"] = []
    cf, transport, _ = cloudflare(Transport({key: raw(d)})); db, driver = database()
    with pytest.raises(p.PublishError): p.run(db, cf)
    assert all(c.readonly for c in driver.connections) and transport.written_keys == []


def test_draft_pagination_full_and_over_100_keynames_are_allowed():
    cf, transport, _ = cloudflare()
    transport.pages = [([{"name": "library:draft:draft:a"}], "page-2"), ([{"name": "library:draft:draft:b"}], "")]
    assert cf.draft_keys(NAMESPACE) == ["library:draft:draft:a", "library:draft:draft:b"]
    transport.pages = [([{"name": f"library:draft:draft:{i}"} for i in range(100)], "next"),
                       ([{"name": "library:draft:draft:101"}], "")]
    assert len(cf.draft_keys(NAMESPACE)) == 101
    assert transport.written_keys == []


def test_501_published_drafts_do_not_block_one_new_pending():
    values = {p.DRAFT_PREFIX + f"draft:done-{i:03}": raw(draft(f"draft:done-{i:03}", status="published")) for i in range(501)}
    new = draft("draft:new"); values[p.DRAFT_PREFIX + new["article"]["id"]] = raw(new)
    cf, transport, _ = cloudflare(Transport(values)); db, driver = database()
    names = sorted(values)
    transport.pages = [([{"name": k} for k in names[i:i+100]], str(i+100) if i+100 < len(names) else "") for i in range(0, len(names), 100)]
    result = p.run(db, cf)
    assert result["drafts_published"] == 1 and len(driver.rows) == 2
    assert json.loads(transport.values[p.DRAFT_PREFIX + new["article"]["id"]])["status"] == "published"


def test_more_than_100_pending_processes_first_batch_and_retains_remaining():
    values = {p.DRAFT_PREFIX + f"draft:pending-{i:03}": raw(draft(f"draft:pending-{i:03}")) for i in range(101)}
    cf, transport, _ = cloudflare(Transport(values)); db, driver = database()
    names = sorted(values)
    transport.pages = [([{"name": k} for k in names[:100]], "next"), ([{"name": names[-1]}], "")]
    result = p.run(db, cf)
    assert result["drafts_published"] == 100 and result["draft_keys_deferred"] == 1
    assert result["next_manual_run_required"] is True
    assert transport.values[names[-1]] == values[names[-1]]
    assert len(driver.rows) == 101


def test_draft_repeated_cursor_and_duplicate_key_are_rejected():
    cf, transport, _ = cloudflare()
    transport.pages = [([{"name": "library:draft:a"}], "loop"), ([{"name": "library:draft:b"}], "loop")]
    with pytest.raises(p.PublishError, match="PAGINATION"): cf.draft_keys(NAMESPACE)
    transport.pages = [([{"name": "library:draft:a"}, {"name": "library:draft:a"}], "")]
    with pytest.raises(p.PublishError, match="REPEATED_DRAFT_KEY"): cf.draft_keys(NAMESPACE)


def test_published_and_error_drafts_are_not_automatically_written_again():
    values = {p.DRAFT_PREFIX + f"draft:{status}": raw(draft(f"draft:{status}", status=status)) for status in ("published", "error")}
    cf, transport, _ = cloudflare(Transport(values)); db, driver = database()
    result = p.run(db, cf)
    assert result["drafts_published"] == 0 and all(c.readonly for c in driver.connections)
    assert not any(k.startswith(p.DRAFT_PREFIX) for k in transport.written_keys)


def test_preflight_combined_capacity_prevents_database_insert():
    before = old_snapshot([item(f"old:{i}") for i in range(p.MAX_ARTICLES)])
    d = draft(); key = p.DRAFT_PREFIX + d["article"]["id"]
    cf, transport, _ = cloudflare(Transport({p.CURRENT_KEY: raw(before), key: raw(d)})); db, driver = database()
    with pytest.raises(p.PublishError, match="ARTICLE_LIMIT"):
        p.run(db, cf)
    assert all(c.readonly for c in driver.connections) and transport.written_keys == []


def test_main_logs_only_counts_or_safe_codes(monkeypatch, capsys):
    db, driver = database(); driver.fail = True
    cf, _, _ = cloudflare()
    monkeypatch.setattr(p, "Database", lambda *args: db)
    monkeypatch.setattr(p, "Cloudflare", lambda *args: cf)
    assert p.main({"SUPABASE_DB_URL": "SECRET-DSN", "CLOUDFLARE_API_TOKEN": "SECRET-TOKEN"}) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": "DATABASE_READ_FAILED"}
    assert output.err == "" and "SECRET" not in output.out and "private body" not in output.out


def test_permission_message_is_safe_and_clear(monkeypatch, capsys):
    def denied(*args): raise p.PublishError("CLOUDFLARE_KV_ACCESS_DENIED")
    monkeypatch.setattr(p, "Cloudflare", denied)
    assert p.main({}) == 1
    result = json.loads(capsys.readouterr().out)
    assert "Workers KV Storage" in result["message"]


def test_workflow_has_only_authorized_triggers_secrets_and_no_content_artifacts():
    text = (Path(__file__).parents[1] / ".github/workflows/library-publish.yml").read_text()
    assert "workflow_dispatch" in text and "branches: [main]" in text
    assert "types: [library-update]" in text and "repository_dispatch" in text
    assert "pull_request" not in text and "schedule:" not in text
    assert "contents: read" in text and "cancel-in-progress: false" in text
    assert "group: library-publish" in text and 'python-version: "3.12"' in text
    assert re.search(r"psycopg2-binary==\d+\.\d+\.\d+", text)
    assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == {"SUPABASE_DB_URL", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    assert "upload-artifact" not in text and "client_payload" not in text
