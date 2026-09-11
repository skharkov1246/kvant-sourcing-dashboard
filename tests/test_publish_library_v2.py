"""Synthetic records and mocked transports only; no credentials or live data."""
from contextlib import contextmanager
import copy
import importlib.util
import json
import re
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("publisher_v2", SCRIPTS / "publish_library_v2.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

SEGMENTS = [{"id": "s1", "name": "Synthetic equipment", "note": "Test only"},
            {"id": "s2", "name": "Synthetic other", "note": ""}]
NOW = "2026-01-01T00:00:00Z"


def row(n, segment="s1", managed=True):
    identity = f"synthetic:{n:06d}"
    sources = {"kind": p.KINDS[n % 4], "typedfields": {"part_number": f"PN-{n}/A.1"},
               "references": [{"url": "https://example.test/source", "locator": "Sheet 1"}]}
    if managed:
        sources.update(importer_id=identity, publication_approved=True)
    return {"id": identity, "segment_id": segment, "title": f"Synthetic item {n}", "topic": "QA",
            "body": "Synthetic full evidence Ω supplier μ\n" + str(n), "sources": sources,
            "confidence": "med", "updated_at": NOW, "extra_original": {"keep": True}}


class CF:
    def __init__(self, legacy=None):
        self.values, self.puts, self.reads = {}, [], []
        self.failure = None
        if legacy is not None:
            self.values[p.v1.CURRENT_KEY] = p.encode({"version": 1, "revision": "legacy-r1",
                "published_at": NOW, "segments": SEGMENTS, "articles": legacy})

    def namespace(self): return "a" * 32
    def draft_keys(self, namespace): return sorted(k for k in self.values if k.startswith(p.v1.DRAFT_PREFIX))
    def get(self, namespace, key):
        self.reads.append(key)
        return self.values.get(key)
    def put(self, namespace, key, raw):
        if self.failure and self.failure(key): raise p.v1.PublishError("SYNTHETIC_WRITE_FAILURE")
        self.puts.append(key)
        self.values[key] = raw
    def verify(self, namespace, key, raw):
        if self.failure and self.failure("verify:" + key): raise p.v1.PublishError("KV_READBACK_NOT_CONFIRMED")
        assert self.values.get(key) == raw
    def preserve(self, namespace, key, raw):
        old = self.values.get(key)
        p.v1.require(old is None or old == raw, "HISTORY_REVISION_CONFLICT")
        if old is None: self.put(namespace, key, raw)
        self.verify(namespace, key, raw)


class DB:
    def __init__(self, rows):
        self.rows = rows
        self.inserts, self.iterated = [], 0
    @contextmanager
    def stream(self):
        def source():
            for r in sorted(self.rows, key=lambda x: x["id"]):
                self.iterated += 1
                yield copy.deepcopy(r)
        yield copy.deepcopy(SEGMENTS), source()
    def insert_drafts(self, rows):
        self.inserts.extend(copy.deepcopy(rows))
        for r in rows:
            if not any(old["id"] == r["id"] for old in self.rows): self.rows.append(copy.deepcopy(r))


def manifest(cf):
    pointer = p.v1.decode(cf.values[p.CURRENT])
    store = p.Store(cf, cf.namespace())
    return store, store.get(pointer["manifest"])


def materialized(cf):
    store, doc = manifest(cf)
    records = list(p.entries(store, doc["directory"], "directory"))
    return {r["id"]: p.stored_article(store, r) for r in records}


def test_lossless_195_migration_retains_unrelated_and_exact_legacy_history():
    old = [row(i, managed=i != 194) for i in range(195)]
    old[194]["sources"] = [{"verbatim": "Do not drop original array"}]
    old[194]["updated_at"] = "2026-01-01T03:00:00+03:00"
    cf = CF(old)
    original_raw = cf.values[p.v1.CURRENT_KEY]
    result = p.run(DB(old[:194]), cf, NOW)
    assert result["articles"] == 195
    assert materialized(cf) == {r["id"]: r for r in old}
    assert cf.values[p.v1.CURRENT_KEY] == original_raw
    assert cf.values["library:history:legacy-r1"] == original_raw
    assert p.v1.CURRENT_KEY not in cf.puts
    assert len(cf.values[p.CURRENT]) < 1024
    assert cf.puts[-1] == p.CURRENT
    _, doc = manifest(cf)
    assert doc["counts_by_confidence"] == {"med": 195}
    assert doc["segments"][0]["counts_by_confidence"] == {"med": 195}


def test_real_scale_beyond_5000_is_sharded_and_memory_fetch_is_not_whole_document():
    rows = [row(i, "s1" if i % 2 else "s2") for i in range(5101)]
    cf, db = CF(), DB(rows)
    result = p.run(db, cf, NOW)
    store, doc = manifest(cf)
    assert result["articles"] == db.iterated == 5101
    assert len(cf.values[p.CURRENT]) < 1024
    assert len(list(p.entries(store, doc["directory"], "directory"))) == 5101
    assert max(len(raw) for key, raw in cf.values.items() if key.startswith(p.PREFIX)) <= p.MAX_VALUE_BYTES
    assert doc["segments"][0]["article_count"] + doc["segments"][1]["article_count"] == 5101
    bodies = [p.v1.decode(raw) for key, raw in cf.values.items() if key.startswith(p.PREFIX)
              and p.v1.decode(raw).get("type") == "articles"]
    assert len(bodies) == 103  # 50 records per body block, not 5101 HTTP values.
    assert sum(len(b["items"]) for b in bodies) == 5101


def test_noop_and_new_revisions_preserve_old_reachable_bodies():
    cf, db = CF(), DB([row(1), row(2)])
    p.run(db, cf, NOW)
    old_pointer = cf.values[p.CURRENT]
    old_revision = p.v1.decode(old_pointer)["revision"]
    writes = len(cf.puts)
    assert p.run(db, cf, NOW)["changed"] is False
    assert len(cf.puts) == writes
    db.rows[0]["body"] += " new observation"
    assert p.run(db, cf, NOW)["changed"] is True
    assert cf.values["library:v2:revision:" + old_revision] == old_pointer
    old_store = p.Store(cf, cf.namespace())
    old_manifest = old_store.get(p.v1.decode(old_pointer)["manifest"])
    old_entry = next(p.entries(old_store, old_manifest["directory"], "directory"))
    assert p.stored_article(old_store, old_entry)["body"] == row(1)["body"]


def test_unmanaged_collision_fails_without_current_or_legacy_mutation():
    cf = CF([row(1, managed=False)])
    old = dict(cf.values)
    with pytest.raises(p.v1.PublishError, match="MANAGED_ID_CONFLICT"):
        p.run(DB([row(1)]), cf, NOW)
    assert cf.values == old


def test_retained_legacy_optional_omissions_remain_exact_and_confidence_bins_literal():
    old = {k: v for k, v in row(1, managed=False).items() if k in ("id", "segment_id", "title", "body")}
    cf = CF([old])
    incoming = [row(2), row(3, "s2")]
    incoming[0]["confidence"] = "low"
    incoming[0]["sources"]["review_status"] = "historical_reference_unverified"
    incoming[1]["confidence"] = "high"
    p.run(DB(incoming), cf, NOW)
    assert materialized(cf)[old["id"]] == old
    store, doc = manifest(cf)
    assert doc["counts_by_confidence"] == {"high": 1, "low": 1, "med": 1}
    first = doc["segments"][0]
    assert first["counts_by_confidence"] == {"low": 1, "med": 1}
    catalog = list(p.entries(store, first["indexes"]["price"]["catalog"], "catalog"))
    assert catalog[0]["sources"]["review_status"] == "historical_reference_unverified"


@pytest.mark.parametrize("field", ["missing", "hash", "count"])
def test_previous_shard_corruption_prevents_promotion(field):
    cf = CF()
    p.run(DB([row(1)]), cf, NOW)
    previous = cf.values[p.CURRENT]
    store, doc = manifest(cf)
    key = p.PREFIX + doc["directory"]["sha256"]
    if field == "missing": del cf.values[key]
    elif field == "hash": cf.values[key] = b"{}"
    else:
        node = p.v1.decode(cf.values[key])
        node["items"] = []
        bad = store.put(node)
        doc["directory"] = {**doc["directory"], **bad}
        pointer = p.v1.decode(previous)
        pointer["manifest"] = store.put(doc)
        cf.values[p.CURRENT] = p.encode(pointer)
        previous = cf.values[p.CURRENT]
    with pytest.raises(p.v1.PublishError): p.run(DB([]), cf, NOW)
    assert cf.values[p.CURRENT] == previous


def test_midwrite_or_readback_failure_never_promotes():
    for prefix in (p.PREFIX, "verify:" + p.PREFIX):
        cf = CF([row(1)])
        cf.failure = lambda key: key.startswith(prefix)
        with pytest.raises(p.v1.PublishError): p.run(DB([row(2)]), cf, NOW)
        assert p.CURRENT not in cf.values
        assert p.v1.CURRENT_KEY not in cf.puts


def test_draft_status_after_db_reread_and_verified_v2_current_only():
    cf, db = CF(), DB([])
    draft = row(8)
    draft["id"] = "draft:12345678-1234-1234-1234-123456789012"
    draft.pop("extra_original")
    draft["sources"].pop("importer_id")
    draft["sources"].pop("publication_approved")
    key = p.v1.DRAFT_PREFIX + draft["id"]
    cf.values[key] = p.encode({"version": 1, "status": "pending", "created_at": NOW, "article": draft})
    result = p.run(db, cf, NOW)
    assert result["drafts_published"] == 1
    assert cf.puts.index(p.CURRENT) < cf.puts.index(key)
    assert p.v1.decode(cf.values[key])["article"] == draft
    assert p.v1.decode(cf.values[key])["status"] == "published"
    actual = materialized(cf)[draft["id"]]
    assert actual["sources"]["origin"] == "portal-owner-draft"


def test_draft_db_mismatch_does_not_publish_any_pointer_or_draft():
    class Missing(DB):
        def insert_drafts(self, rows): pass
    cf = CF()
    draft = row(9)
    key = p.v1.DRAFT_PREFIX + draft["id"]
    cf.values[key] = p.encode({"version": 1, "status": "pending", "created_at": NOW, "article": draft})
    original = cf.values[key]
    with pytest.raises(p.v1.PublishError, match="DATABASE_READBACK_MISMATCH"):
        p.run(Missing([]), cf, NOW)
    assert cf.values[key] == original
    assert p.CURRENT not in cf.values


def test_summary_explicitly_truncates_metadata_but_full_record_and_search_preserve_it():
    value = row(1)
    value["sources"]["typedfields"] = {"long": "not-lost-" * 4000}
    short = p.summary(value)
    assert short["sources_truncated"] is True
    assert len(p.encode(short["sources"])) <= p.SUMMARY_SOURCES_BYTES
    assert "not-lost-" * 4000 in p.search_text(value)
    cf = CF()
    p.run(DB([value]), cf, NOW)
    assert materialized(cf)[value["id"]] == value


def test_source_or_article_limits_fail_without_current_not_silent_trim(monkeypatch):
    monkeypatch.setattr(p, "MAX_ARTICLES", 2)
    cf = CF()
    with pytest.raises(p.v1.PublishError, match="ARTICLE_LIMIT"):
        p.run(DB([row(i) for i in range(3)]), cf, NOW)
    assert p.CURRENT not in cf.values


def test_only_fixed_v2_keys_and_namespace_allowed():
    cf = p.Cloudflare("a" * 32, "synthetic-token", opener=object())
    for key in (p.CURRENT, p.PREFIX + "0" * 64, "library:v2:revision:synthetic-1"):
        assert "%3A" in cf.value_path("b" * 32, key)
    for key in ("acl:v1", p.PREFIX + "../", "library:v2:revision:../secret"):
        with pytest.raises(p.v1.PublishError): cf.value_path("b" * 32, key)


def test_safe_failure_output_never_raw_values(monkeypatch, capsys):
    def failure(*a, **k): raise RuntimeError("synthetic-token PRIVATE body")
    monkeypatch.setattr(p, "Database", failure)
    assert p.main({}) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "PUBLISH_FAILED"}


def test_source_bundle_has_named_server_cursor_and_readonly_transaction():
    source = (SCRIPTS / "publish_library_v2.py").read_text()
    assert 'cursor(name="private_library_v2")' in source
    assert "fetchmany(50)" in source
    assert "LIMIT 250001" in p.ORDERED_SQL
    assert "COLLATE \"C\"" in p.ORDERED_SQL


def test_actual_database_session_uses_readonly_snapshot_and_50_row_fetches():
    records = []
    for n in range(102):
        r = row(n)
        records.append(tuple(r[k] for k in ("segment_id", "title", "topic", "body", "sources", "confidence", "updated_at")))
    class Cursor:
        def __init__(self, named): self.named, self.offset, self.calls = named, 0, []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None): self.calls.append((sql, params))
        def fetchmany(self, size):
            if not self.named: return [(x["id"], x["name"], x["note"]) for x in SEGMENTS]
            if self.named == "private_library_relations":
                assert size == 50
                return []
            assert size == 50
            result = records[self.offset:self.offset + size]; self.offset += len(result)
            return result
    class Connection:
        def __init__(self): self.cursors, self.session, self.rollbacks, self.closed = [], None, 0, False
        def set_session(self, **kwargs): self.session = kwargs
        def cursor(self, name=None):
            cursor = Cursor(name); self.cursors.append(cursor); return cursor
        def rollback(self): self.rollbacks += 1
        def close(self): self.closed = True
    connection = Connection()
    class Driver:
        @staticmethod
        def parse_dsn(dsn): return {"host": f"db.{p.v1.PROJECT_REF}.supabase.co", "user": "postgres", "password": "synthetic"}
        @staticmethod
        def connect(**kwargs):
            assert kwargs["sslmode"] == "require" and kwargs["connect_timeout"] == 10
            assert "default_transaction_read_only=on" in kwargs["options"]
            return connection
    db = p.Database("synthetic", driver=Driver)
    with db.stream() as (segments, stream):
        assert segments == SEGMENTS
        assert len(list(stream)) == 102
    assert connection.session == {"readonly": True, "autocommit": False, "isolation_level": "REPEATABLE READ"}
    assert connection.cursors[1].named == "private_library_relations"
    assert connection.cursors[1].calls == [(p.RELATIONS_SQL, (p.v1.MANAGER, p.MAX_RELATION_EDGES + 1,
                                                          p.v1.MANAGER, p.MAX_RELATION_EDGES + 1))]
    assert connection.cursors[2].named == "private_library_v2"
    assert connection.cursors[2].itersize == 50
    assert connection.cursors[2].calls == [(p.ORDERED_SQL, (p.v1.MANAGER,))]
    assert connection.rollbacks == 1 and connection.closed


def test_draft_unmanaged_collision_rejected_before_database_insert():
    old = row(1, managed=False)
    cf, db = CF([old]), DB([])
    draft = row(1)
    key = p.v1.DRAFT_PREFIX + draft["id"]
    cf.values[key] = p.encode({"version": 1, "status": "pending", "created_at": NOW, "article": draft})
    with pytest.raises(p.v1.PublishError, match="MANAGED_ID_CONFLICT"):
        p.run(db, cf, NOW)
    assert db.inserts == []
    assert p.CURRENT not in cf.values


def relation_projection(**changes):
    values = {"supplier_db_id": 1, "supplier_id": row(1)["id"],
              "name": "Synthetic candidate", "edge": {"article_id": row(3)["id"], "position_id": 7,
              "part_number": "T-007", "source_pointer": "/6"},
              "target_db_id": 3, "target_id": row(3)["id"],
              "part_number": "T-007", "evidence": [{"sha256": "a" * 64, "url": "https://example.test/source",
              "json_pointer": "/6", "repository_path": "zip/data/positions.json"}]}
    values.update(changes)
    return tuple(values.values())


class RelatedDB(DB):
    def __init__(self, rows, projection):
        super().__init__(rows)
        self.projection = projection
    @contextmanager
    def stream(self):
        with super().stream() as (segments, incoming):
            relations, metrics = p.supplier_relations(self.projection)
            yield segments, p.SourceRows(incoming, relations, metrics)


@pytest.mark.parametrize("database_ids", [(1, 3), ("1", "3"),
    ("10000000-0000-0000-0000-000000000001", "20000000-0000-0000-0000-000000000001")])
def test_exact_candidate_join_preserves_canonical_sources_and_old_snapshot_bytes(database_ids):
    supplier, component = row(1), row(3)
    supplier["sources"]["candidate_position_links"] = [relation_projection()[3]]
    component["sources"]["component_fields"] = {"part_number": "T-007"}
    original = p.encode([supplier, component])
    cf = CF([supplier, component])
    old = cf.values[p.v1.CURRENT_KEY]
    db = RelatedDB([supplier, component], [relation_projection(
        supplier_db_id=database_ids[0], target_db_id=database_ids[1])])
    result = p.run(db, cf, NOW)
    actual = materialized(cf)
    assert p.encode(db.rows) == original
    assert cf.values[p.v1.CURRENT_KEY] == cf.values["library:history:legacy-r1"] == old
    assert actual[supplier["id"]] == supplier
    projected = copy.deepcopy(actual[component["id"]])
    relations = projected["sources"].pop("library_relations")
    assert projected == component
    assert relations["producer"] == "publisher-v2"
    assert relations["candidate_suppliers"][0] == {"article_id": supplier["id"], "name": "Synthetic candidate",
        "relation_type": "historical_supplier_candidate", "position_id": 7, "part_number": "T-007",
        "json_pointer": "/6", "source_sha256": "a" * 64, "source_url": "https://example.test/source"}
    assert result["supplier_relations"] == {"edges": 1, "linked": 1, "missing_target": 0, "missing_evidence": 0, "duplicate_edges": 0}
    assert p.summary(actual[component["id"]])["sources"]["library_relations"] == relations
    previous = dict(cf.values)
    assert p.run(db, cf, NOW)["changed"] is False
    assert all(cf.values[k] == value for k, value in previous.items())


def test_missing_target_or_exact_evidence_never_creates_a_guessed_link():
    index, metrics = p.supplier_relations([
        relation_projection(target_db_id=None, target_id=None, part_number=None),
        relation_projection(evidence=[{"sha256": "a" * 64, "json_pointer": "/wrong"}])])
    assert index == {}
    assert metrics == {"edges": 2, "linked": 0, "missing_target": 1, "missing_evidence": 1, "duplicate_edges": 0}
    # Missing supplier/target in the actual emitted stream cannot be published,
    # even if a buggy projection claims the target exists.
    for records in ([row(1)], [row(3)]):
        cf = CF()
        with pytest.raises(p.v1.PublishError, match="RELATION_TARGET_NOT_PUBLISHED"):
            p.run(RelatedDB(records, [relation_projection()]), cf, NOW)
        assert p.CURRENT not in cf.values


@pytest.mark.parametrize("changes,error", [
    ({"supplier_db_id": "synthetic:000001"}, "INVALID_RELATION_DATABASE_ID"),
    ({"target_id": "synthetic:other"}, "INVALID_RELATION_TARGET"),
    ({"part_number": "T-008"}, "RELATION_PART_NUMBER_MISMATCH"),
    ({"evidence": [{"sha256": "a" * 64, "url": "javascript:bad", "json_pointer": "/6", "repository_path": "zip/data/positions.json"}]}, "INVALID_RELATION_SOURCE_URL")])
def test_unsafe_or_inconsistent_candidate_projection_fails(changes, error):
    with pytest.raises(p.v1.PublishError, match=error):
        p.supplier_relations([relation_projection(**changes)])


@pytest.mark.parametrize("field", ["supplier_db_id", "target_db_id"])
@pytest.mark.parametrize("different_id", [4, "4", "30000000-0000-0000-0000-000000000001"])
def test_relation_stable_id_collision_does_not_collapse_database_rows(field, different_id):
    with pytest.raises(p.v1.PublishError, match="RELATION_ID_COLLISION"):
        p.supplier_relations([relation_projection(), relation_projection(**{field: different_id})])


def test_internal_database_identity_canonicalization_is_typed_and_never_a_public_id():
    assert p.database_identity(1) == p.database_identity("1") == ("bigint", 1)
    assert p.database_identity(9223372036854775807) == p.database_identity("9223372036854775807")
    value = p.uuid.UUID("abcdefab-cdef-abcd-efab-cdefabcdefab")
    assert p.database_identity(value) == p.database_identity(str(value).upper()) == ("uuid", str(value))
    assert p.database_identity(1) != p.database_identity("00000000-0000-0000-0000-000000000001")
    with pytest.raises(p.v1.PublishError, match="RELATION_ID_COLLISION"):
        p.supplier_relations([relation_projection(), relation_projection(
            supplier_db_id="00000000-0000-0000-0000-000000000001")])
    index, metrics = p.supplier_relations([relation_projection(),
        relation_projection(supplier_db_id="1", target_db_id="3")])
    assert metrics["linked"] == metrics["duplicate_edges"] == 1
    assert set(index) == {row(3)["id"]}
    assert index[row(3)["id"]][0]["article_id"] == row(1)["id"]
    uuid_projection = relation_projection(supplier_db_id=value, target_db_id=str(value).replace("abcdefab-", "12345678-", 1))
    canonical_projection = relation_projection(supplier_db_id=str(value).upper(), target_db_id=uuid_projection[4])
    _, metrics = p.supplier_relations([uuid_projection, canonical_projection])
    assert metrics["linked"] == metrics["duplicate_edges"] == 1


@pytest.mark.parametrize("invalid", [None, True, False, 0, -1, 9223372036854775808,
    1.0, [], {}, "", "0", "-1", "+1", "01", " 1", "1 ", "1.0", "١", "１",
    "9223372036854775808", "00000000000000000000000000000001", "synthetic:000001"])
def test_invalid_database_identity_fails_before_current_pointer_write(invalid):
    with pytest.raises(p.v1.PublishError, match="INVALID_RELATION_DATABASE_ID"):
        p.database_identity(invalid)
    cf = CF()
    with pytest.raises(p.v1.PublishError, match="INVALID_RELATION_DATABASE_ID"):
        p.run(RelatedDB([row(1), row(3)], [relation_projection(supplier_db_id=invalid)]), cf, NOW)
    assert p.CURRENT not in cf.values
    if invalid is not None:
        with pytest.raises(p.v1.PublishError, match="INVALID_RELATION_DATABASE_ID"):
            p.run(RelatedDB([row(1), row(3)], [relation_projection(target_db_id=invalid)]), cf, NOW)
        assert p.CURRENT not in cf.values


@pytest.mark.parametrize("limit,error", [("MAX_RELATION_EDGES", "RELATION_EDGE_LIMIT"),
    ("MAX_COMPONENT_RELATIONS", "COMPONENT_RELATION_LIMIT"), ("MAX_RELATION_BYTES", "RELATION_BYTE_LIMIT")])
def test_relation_bounds_fail_without_truncation_or_pointer_update(monkeypatch, limit, error):
    monkeypatch.setattr(p, limit, 0)
    cf = CF()
    with pytest.raises(p.v1.PublishError, match=error):
        p.run(RelatedDB([row(1), row(3)], [relation_projection()]), cf, NOW)
    assert p.CURRENT not in cf.values


def test_relations_never_replace_a_source_owned_field_or_promote_brand_to_supplier():
    component = row(3)
    component["sources"]["library_relations"] = {"original": "keep"}
    before = p.encode(component)
    cf = CF()
    with pytest.raises(p.v1.PublishError, match="DERIVED_RELATION_FIELD_CONFLICT"):
        p.run(RelatedDB([row(1), component], [relation_projection()]), cf, NOW)
    assert p.encode(component) == before
    assert p.CURRENT not in cf.values
    assert "'component'" in p.RELATIONS_SQL and "'supplier'" in p.RELATIONS_SQL
    assert "t.sources->>'importer_id'=e.link->>'article_id'" in p.RELATIONS_SQL
    assert "body" not in p.RELATIONS_SQL
    assert p.RELATIONS_SQL.count("publication_approved") == 2


def test_display_family_dictionary_and_russian_search_remain_in_sync_without_source_rewrite():
    html = (SCRIPTS.parent / "public" / "library.html").read_text()
    labels = json.loads(re.search(r"const COMPONENT_FAMILIES=(\{[^\n]+\});", html).group(1))
    assert labels == p.COMPONENT_FAMILIES
    qualified = json.loads(re.search(r"const OEM_COMPONENT_FAMILIES=(\{[^\n]+\});", html).group(1))
    assert qualified == p.OEM_COMPONENT_FAMILIES
    for name in ("PENTAIR_ACCESSORY_FAMILIES", "PENTAIR_DESCRIPTION_LABELS", "VALVE_FLOW_LABELS"):
        literal = re.search(r"const " + name + r"=([^\n]+);", html).group(1)
        assert json.loads(literal) == getattr(p, name)
    for family, query in (("spherical_roller", "сферический роликовый подшипник"),
                          ("electric_motor", "электродвигатель")):
        component = row(3)
        component["sources"]["component_fields"] = {"part_number": "T-007", "family": family}
        before = p.encode(component)
        cf = CF()
        p.run(DB([component]), cf, NOW)
        store, doc = manifest(cf)
        root = doc["segments"][0]["indexes"]["component"]["search"]
        indexed = list(p.entries(store, root, "search"))
        assert query in indexed[0]["text"]
        assert p.encode(materialized(cf)[component["id"]]) == before


def test_relation_evidence_rejects_same_pointer_in_a_different_source_file():
    evidence = [{"sha256": "a" * 64, "url": "https://example.test/source", "json_pointer": "/6",
                 "repository_path": "zip/data/odm_suppliers.json"}]
    index, metrics = p.supplier_relations([relation_projection(evidence=evidence)])
    assert index == {} and metrics["missing_evidence"] == 1
    assert "'json_pointers' ? (e.link->>'source_pointer')" in p.RELATIONS_SQL
    assert "r->>'repository_path'='zip/data/positions.json'" in p.RELATIONS_SQL


def test_every_relation_edge_is_accounted_for_including_exact_duplicate_observations():
    projection = [relation_projection(), relation_projection(),
                  relation_projection(target_db_id=None, target_id=None), relation_projection(evidence=[])]
    before = p.encode([[str(x) for x in r] for r in projection])
    index, metrics = p.supplier_relations(projection)
    assert metrics == {"edges": 4, "linked": 1, "missing_target": 1, "missing_evidence": 1, "duplicate_edges": 1}
    assert metrics["edges"] == sum(metrics[key] for key in ("linked", "missing_target", "missing_evidence", "duplicate_edges"))
    assert len(index[row(3)["id"]]) == 1
    assert p.encode([[str(x) for x in r] for r in projection]) == before


def test_explicit_tool_family_is_oem_qualified_never_inferred_from_order_code():
    fields = {"oem": "Dormer Pramet", "family": "R200", "part_number": "SYNTHETIC-007_"}
    assert p.known_component_family(fields) == "Твердосплавное центровочное сверло"
    assert p.known_component_family({**fields, "family": "R7131"}) == "Твердосплавное ступенчатое сверло"
    assert p.known_component_family({**fields, "family": "R6011"}) == "Твердосплавное сверло для засверливания"
    assert p.known_component_family({**fields, "oem": "Different OEM"}) == ""
    assert p.known_component_family({"oem": "Dormer Pramet", "part_number": "R200-SYNTHETIC"}) == ""
    component = row(3)
    component["sources"]["component_fields"] = fields
    component["segment_id"] = "welding"
    before = p.encode(component)
    assert "центровочное сверло" in p.search_text(component)
    assert p.component_type_label(p.summary(component)["sources"], "welding") == p.component_type_label(component["sources"], "welding")
    assert p.encode(component) == before


def test_accessory_flag_prevents_filter_housing_family_from_becoming_product_type():
    source = {"component_fields": {"oem": "Pentair", "family": "PENTEK SLIM LINE FILTER HOUSINGS",
                                    "part_number": "SYNTHETIC-007", "is_accessory": True}}
    assert p.component_type_label(source, "water") == "Принадлежность системы фильтрации"
    source["component_fields"]["is_accessory"] = None
    assert p.component_type_label(source, "water") == ""


def test_water_types_require_explicit_scope_flags_and_common_description():
    source = {"component_fields": {"oem": "Pentair", "family": "PENTEK ST SERIES STAINLESS STEEL FILTER HOUSINGS", "is_accessory": True},
              "typedfields": {"specification": {"catalogue_fields_as_printed": {"DESCRIPTION": "ST Gasket , BUNA-N"}}}}
    assert p.component_type_label(source, "water") == "Прокладка ST · ST Gasket , BUNA-N"
    assert p.component_type_label(source, "pumps") == ""
    source["component_fields"]["family"] = "Unknown family"
    assert p.component_type_label(source, "water") == ""
    source["component_fields"].update(family="PENTEK SLIM LINE FILTER HOUSINGS", is_accessory=False)
    assert p.component_type_label(source, "water") == "Корпус фильтра"
    source["component_fields"].update(family="PENTEK QUICK-CHANGE FILTRATION SYSTEMS")
    assert p.component_type_label(source, "water") == ""
    source["typedfields"]["specification"]["catalogue_fields_as_printed"]["CARTRIDGE COLOR"] = "White"
    assert p.component_type_label(source, "water") == "Сменный картридж фильтра"
    source["component_fields"].update(family="Pentek water filtration")
    source["typedfields"]["specification"]["catalogue_fields_as_printed"] = {"DESCRIPTION": "Thin Film Membrane"}
    assert p.component_type_label(source, "water") == "Тонкоплёночная мембрана"
    source["component_fields"].update(family="PENTEK SLIM LINE FILTER HOUSINGS", is_accessory=True)
    source["typedfields"]["specification"]["catalogue_fields_as_printed"] = {}
    source["original_record"] = {"catalogue_observations": [{"description": "Viton"}, {"description": "Silicone"}]}
    assert p.component_type_label(source, "water") == "Принадлежность системы фильтрации"


def test_equipment_types_do_not_copy_engineering_parameters_or_guess_missing_families():
    cases = [({"oem": "Danfoss", "family": "XB51L-1 SB"}, "heat", "Паяный пластинчатый теплообменник"),
             ({"oem": "Tsurumi", "family": "KTZ"}, "pumps", "Погружной дренажный насос"),
             ({"oem": "Swagelok", "family": "40GX"}, "valves", "Шаровой кран"),
             ({"oem": "Grundfos", "part_number": "SYNTHETIC"}, "pumps", "")]
    for fields, segment, expected in cases:
        source = {"component_fields": fields, "typedfields": {"specification": {"pressure": None, "orifice_mm": None}}}
        before = p.encode(source)
        assert p.component_type_label(source, segment) == expected
        assert p.encode(source) == before
    source = {"component_fields": {"oem": "Swagelok"}, "typedfields": {"specification": {"flow_pattern": "three_way_switching"}}}
    assert p.component_type_label(source, "valves") == "Арматура: трёхходовая переключающая"
    source["typedfields"]["specification"]["valve_type"] = "ball"
    assert p.component_type_label(source, "valves") == "Шаровой кран"


def test_russian_display_label_search_accepts_yo_and_e_without_rewriting_full_source():
    component = row(3)
    component["sources"]["component_fields"] = {"family": "hydraulic_gear_pump", "part_number": "SYNTHETIC"}
    before = p.encode(component)
    search = p.search_text(component)
    assert "шестерённый гидравлический насос" in search
    assert "шестеренный гидравлический насос" in search
    assert search.count(component["body"].lower()) == 1
    assert p.encode(component) == before


def test_filter_cartridge_label_does_not_invent_a_water_treatment_application():
    source = {"component_fields": {"oem": "Pentair", "family": "PENTEK ELPC ELECTROPLATING CARBON CARTRIDGES", "is_accessory": False}}
    assert p.component_type_label(source, "water") == "Фильтрующий картридж"


if __name__ == "__main__":
    # Synthetic cross-runtime fixture over stdout only, consumed by Node tests.
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 195
    cf = CF([row(i, managed=i != 194) for i in range(min(count, 195))])
    p.run(DB([row(i) for i in range(count) if i != 194]), cf, NOW)
    print(json.dumps({k: v.decode() for k, v in cf.values.items()}, ensure_ascii=False))
