"""Actual PostgreSQL archive search tests, with synthetic data and a local-only DSN.

The same FIXTURE_SQL may be exercised in an offline PostgreSQL WASM runtime.
No private archive file, portal identifier, credential or real case is a fixture.
"""
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCHEMA_FILE = REPO / "library/supabase/archive_schema.sql"
SEARCH_FILE = REPO / "library/supabase/archive_search.sql"

FIXTURE_SQL = r"""
INSERT INTO public.archive_sources(id,portal) OVERRIDING SYSTEM VALUE VALUES(1,'synthetic.invalid');
INSERT INTO public.archive_ingestion_runs
 (id,source_id,run_key,started_at,finished_at,status,scope,coverage,collector_version)
 OVERRIDING SYSTEM VALUE VALUES
 (1,1,'synthetic-run','2026-01-01T00:00:00Z','2026-01-02T00:00:00Z','partial','{"fixture":true}',
  '{"fixture":true,"full_archive":false,"semantic_reviewed":false}', 'synthetic-v1');
INSERT INTO public.archive_blobs
 (sha256,size_bytes,local_verified_at) VALUES(repeat('a',64),42,'2026-01-01T00:00:00Z');
INSERT INTO public.archive_objects(id,source_id,entity_type,external_id)
 OVERRIDING SYSTEM VALUE VALUES
 (1,1,'crm:2','synthetic-1'),(2,1,'mail_message','synthetic-2'),
 (3,1,'unknown_machine_event','synthetic-3'),(4,1,'activity','synthetic-4'),
 (5,1,'timeline_comment','synthetic-5'),(6,1,'chat_message','synthetic-6'),
 (7,1,'task','synthetic-7'),(8,1,'crm_custom_mystery','synthetic-8'),
 (9,1,'crm_activity','synthetic-9');
INSERT INTO public.archive_object_versions
 (id,source_id,object_id,payload_sha256,parser_version,payload,title,body,body_sha256,
  source_created_at,source_updated_at,observed_at,raw_blob_sha256,raw_json_pointer)
 OVERRIDING SYSTEM VALUE
 SELECT i,1,CASE WHEN i=10 THEN 1 ELSE i END,lpad(i::text,64,'0'),'synthetic-v1',
  jsonb_build_object('synthetic_id',i),
  CASE WHEN i=1 THEN repeat('З',310) ELSE 'Синтетический документ '||i END,
  'Синтетический текст',repeat('b',64),
  CASE WHEN i=2 THEN NULL ELSE '2026-01-01T00:00:00Z'::timestamptz END,
  CASE WHEN i=2 THEN NULL ELSE '2026-01-02T00:00:00Z'::timestamptz END,
  '2026-01-03T00:00:00Z',repeat('a',64),'/synthetic/'||i
 FROM generate_series(1,10) i;
INSERT INTO public.archive_extraction_versions
 (id,input_source_version_id,extractor_name,extractor_version,config_sha256,output_sha256,status)
 OVERRIDING SYSTEM VALUE
 SELECT i,i,'synthetic','v1',repeat('c',64),lpad(i::text,64,'1'),'complete'
 FROM generate_series(1,10) i;
INSERT INTO public.archive_extraction_versions
 (id,input_blob_sha256,extractor_name,extractor_version,config_sha256,output_sha256,status,details)
 OVERRIDING SYSTEM VALUE VALUES
 (11,repeat('a',64),'synthetic-pdf','v1',repeat('c',64),repeat('d',64),'partial',
  '{"name":"Синтетическое вложение"}');
INSERT INTO public.archive_text_units(extraction_id,unit_key,unit_kind,locator,text_content,text_sha256)
 SELECT i,'unit:1','body',jsonb_build_object('char_start',0,'synthetic_id',i),
  CASE i
   WHEN 1 THEN 'Синтетический насос AB-123, цитата "Тест"; старая версия. Ёж.'
   WHEN 2 THEN 'Синтетический насос из письма, неизвестная дата.'
   WHEN 10 THEN 'Синтетический насос AB123, новая версия.'
   ELSE 'Синтетический насос с типом '||i END,
  repeat('e',64) FROM generate_series(1,10) i;
INSERT INTO public.archive_text_units VALUES
 (11,'page:1','page','{"page":1,"member":"synthetic.pdf"}',
  'Синтетический насос AB-123 в архивном PDF.',repeat('f',64),DEFAULT),
 (1,'unit:long','body','{"char_start":0}',
  'начало '||repeat('абв ',1000)||'концевоймаркер',repeat('f',64),DEFAULT),
 (1,'unit:oversized','body',jsonb_build_object('oversized',repeat('x',8200)),
  'oversizedmetadata',repeat('f',64),DEFAULT);
INSERT INTO public.archive_text_units(extraction_id,unit_key,unit_kind,locator,text_content,text_sha256)
 SELECT 1,'perf:'||lpad(i::text,6,'0'),'paragraph',jsonb_build_object('fixture_row',i),
  CASE WHEN i=11999 THEN 'повторяемый уникальныймаркер' ELSE 'повторяемый синтетический' END,
  repeat('e',64) FROM generate_series(1,12000) i;
ANALYZE public.archive_text_units;
ANALYZE public.archive_extraction_versions;
ANALYZE public.archive_object_versions;
ANALYZE public.archive_objects;
"""


def local_test_dsn(raw, parse_dsn):
    parts = parse_dsn(raw)
    if (parts.get("host") not in {"localhost", "127.0.0.1", "::1"}
            or parts.get("dbname") != "library_sql_test"
            or parts.get("hostaddr") not in {None, "127.0.0.1", "::1"}
            or parts.get("service") is not None):
        raise ValueError("Archive SQL tests require the isolated local test database")
    return parts


@pytest.fixture(scope="module")
def db():
    raw = os.environ.get("LIBRARY_SQL_TEST_DSN")
    if not raw:
        pytest.skip("LIBRARY_SQL_TEST_DSN is absent; no remote database fallback")
    psycopg2 = pytest.importorskip("psycopg2")
    sql = pytest.importorskip("psycopg2.sql")
    local_test_dsn(raw, psycopg2.extensions.parse_dsn)
    conn = psycopg2.connect(raw, options="-c statement_timeout=15000 -c lock_timeout=3000")
    conn.autocommit = True
    with conn.cursor() as cur:
        # A localhost-published CI container sees its own Docker interface here;
        # local_test_dsn already restricts the client's actual destination.
        cur.execute("SELECT current_database()")
        assert cur.fetchone()[0] == "library_sql_test"
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'archive\\_%' ESCAPE '\\'")
        assert not cur.fetchall(), "Refuse to adopt or drop pre-existing archive tables"
        cur.execute("""DO $$ BEGIN
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF;
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF;
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role BYPASSRLS; END IF;
        END $$""")
        cur.execute(SCHEMA_FILE.read_text())
        cur.execute(SEARCH_FILE.read_text())
        cur.execute(FIXTURE_SQL)
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'archive\\_%' ESCAPE '\\'")
            tables = [row[0] for row in cur.fetchall()]
            assert len(tables) == 21
            for table in tables:
                cur.execute(sql.SQL("DROP TABLE IF EXISTS public.{} CASCADE").format(sql.Identifier(table)))
            for signature in ["archive_search_text(text,integer,text)", "archive_search_unit(text,text)",
                              "archive_search_status()", "archive_apply_import_batch(jsonb)",
                              "archive_require_evidence()", "archive_validate_lineage()", "archive_reject_mutation()"]:
                cur.execute(f"DROP FUNCTION IF EXISTS public.{signature} CASCADE")
        conn.close()


def query(db, statement, params=()):
    with db.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchall()


def search(db, text, limit=20, kind=None):
    return query(db, "SELECT public.archive_search_text(%s,%s,%s)", (text, limit, kind))[0][0]


def test_role_boundary_and_invoker(db):
    for role in ["anon", "authenticated"]:
        with db.cursor() as cur:
            cur.execute(f"SET ROLE {role}")
        try:
            for statement in ["SELECT public.archive_search_text('насос')",
                              "SELECT public.archive_search_unit('1','unit:1')",
                              "SELECT public.archive_search_status()",
                              "SELECT * FROM public.archive_text_units"]:
                with pytest.raises(Exception) as caught:
                    query(db, statement)
                assert caught.value.pgcode == "42501"
        finally:
            with db.cursor() as cur:
                cur.execute("RESET ROLE")
    rows = query(db, """SELECT p.proname,p.prosecdef,p.provolatile,p.proconfig
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname IN
      ('archive_search_text','archive_search_unit','archive_search_status')""")
    assert len(rows) == 3
    assert all(not row[1] and row[2] == "s" and "statement_timeout=5s" in row[3] for row in rows)
    with db.cursor() as cur:
        cur.execute("SET ROLE service_role")
    try:
        assert search(db, "насос")["returned"] == 11
    finally:
        with db.cursor() as cur:
            cur.execute("RESET ROLE")


@pytest.mark.parametrize("text,limit,kind", [
    (None,20,None), ("a",20,None), ("a"*201,20,None), ("---",20,None),
    ("  ",20,None), ("насос",None,None), ("насос",0,None),
    ("насос",51,None), ("насос",20,""), ("насос",20,"unknown"),
    ("на\nсос",20,None), ("на\x7fсос",20,None), ("\ufeffа\ufeff",20,None),
])
def test_search_guards(db,text,limit,kind):
    with pytest.raises(Exception) as caught:
        search(db,text,limit,kind)
    assert caught.value.pgcode == "22023"
    assert "ARCHIVE_SEARCH_" in str(caught.value)


def test_all_versions_kinds_quotes_and_cyrillic(db):
    result = search(db,"НАСОС")
    assert result["returned"] == 11 and not result["has_more"]
    assert result["all_versions"] is True and result["full_archive"] is False
    assert result["scope"] == "imported_archive_text" and result["search_mode"] == "plain_terms"
    assert [x["extraction_id"] for x in result["items"]] == [str(i) for i in range(1,12)]
    old,new = result["items"][0],result["items"][9]
    assert old["source_id"] == new["source_id"] == "synthetic-1"
    assert old["source_version_id"] != new["source_version_id"]
    assert old["raw_blob_sha256"] == "a"*64 and old["raw_json_pointer"] == "/synthetic/1"
    assert old["text_sha256"] == "e"*64
    assert len(old["title"]) == 300 and old["title_truncated"]
    assert search(db,'"AB-123"')["returned"] == 2
    assert search(db,"AB123")["returned"] == 1
    assert search(db,"АB123")["returned"] == 0  # Cyrillic A is not Latin A.
    assert search(db,"ёж")["returned"] == 1
    assert search(db,"насосами")["returned"] == 11  # Russian morphology, not an unindexed scan.
    assert search(db,"\ufeff\u00a0 насосами \t\n")["returned"] == 11
    assert search(db,"' OR 1=1 --")["returned"] == 0
    expected={"crm":3,"mail":1,"other":1,"activity":2,"timeline":1,"chat":1,"task":1,"attachment":1}
    for kind,count in expected.items():
        filtered=search(db,"насос",50,kind)
        assert filtered["returned"] == count
        assert all(x["source_kind"] == kind for x in filtered["items"])
    assert search(db,"насос",50,"other")["items"][0]["source_entity_type"] == "unknown_machine_event"


def test_excerpt_full_unit_and_blob_lineage(db):
    one=search(db,"концевоймаркер")["items"][0]
    assert one["text_length"] > 1600 and one["text_truncated"] and one["is_excerpt"]
    assert "концевоймаркер" not in one["text"]  # It was indexed beyond the excerpt.
    detail=query(db,"SELECT public.archive_search_unit(%s,%s)",(one["extraction_id"],one["unit_key"]))[0][0]
    assert detail["found"] and "концевоймаркер" in detail["item"]["text"]
    assert not detail["item"]["is_excerpt"] and not detail["item"]["text_truncated"]
    blob=search(db,"насос",20,"attachment")["items"][0]
    assert blob["input_blob_sha256"] == "a"*64 and blob["extraction_output_sha256"] == "d"*64
    assert blob["source_version_id"] is None and blob["source_id"] is None
    assert blob["source_created_at"] is None and blob["source_entity_type"] is None
    assert blob["raw_blob_sha256"] is None and blob["raw_json_pointer"] is None
    assert blob["text_sha256"] == "f"*64
    assert blob["locator"] == {"page":1,"member":"synthetic.pdf"}
    missing=query(db,"SELECT public.archive_search_unit('9223372036854775807','missing')")[0][0]
    assert missing["found"] is False and missing["item"] is None


@pytest.mark.parametrize("identity,key",[(None,"unit:1"),("0","unit:1"),("-1","unit:1"),
    ("01","unit:1"),("+1","unit:1"),("1.0","unit:1"),(" 1","unit:1"),
    ("9223372036854775808","unit:1"),("1",None),("1",""),("1","x"*1025),
    ("1","я"*513),("1","unit:\n1"),("1","unit:\x7f1")])
def test_detail_guards(db,identity,key):
    with pytest.raises(Exception) as caught:
        query(db,"SELECT public.archive_search_unit(%s,%s)",(identity,key))
    assert caught.value.pgcode == "22023"


def test_metadata_is_not_silently_truncated(db):
    for statement,params in [
        ("SELECT public.archive_search_text(%s)",("oversizedmetadata",)),
        ("SELECT public.archive_search_unit(%s,%s)",("1","unit:oversized")),
    ]:
        with pytest.raises(Exception) as caught:
            query(db,statement,params)
        assert caught.value.pgcode == "54000"
        assert "ARCHIVE_SEARCH_METADATA_TOO_LARGE" in str(caught.value)


def test_status_counts_are_exact_and_only_imported(db):
    status=query(db,"SELECT public.archive_search_status()")[0][0]
    expected={"objects":"9","object_versions":"10","extraction_versions":"11",
        "text_units":"12013","logical_blobs":"1","attachment_observations":"0",
        "import_batches":"0","ingestion_runs":"1"}
    assert status["counts"] == expected
    assert status["last_run"]["status"] == "partial"
    assert status["last_run"]["coverage"]["fixture"] is True
    assert status["storage_files_outside_import_not_searched"] is True
    assert status["semantic_understanding_measured"] is False


def test_order_limit_and_real_index_plans(db):
    first=search(db,"повторяемый",20)
    again=search(db,"повторяемый",20)
    assert first == again and first["returned"] == 20 and first["has_more"]
    assert [x["unit_key"] for x in first["items"]] == [f"perf:{i:06}" for i in range(1,21)]
    explain="""EXPLAIN (ANALYZE,FORMAT JSON) SELECT u.extraction_id,u.unit_key
      FROM public.archive_text_units u JOIN public.archive_extraction_versions e ON e.id=u.extraction_id
      LEFT JOIN public.archive_object_versions v ON v.id=e.input_source_version_id
      LEFT JOIN public.archive_objects o ON o.id=v.object_id
      WHERE u.fts @@ plainto_tsquery('simple',%s)
        OR to_tsvector('russian',u.text_content) @@ plainto_tsquery('russian',%s)
      ORDER BY u.extraction_id,u.unit_key COLLATE "C" LIMIT 21"""
    rare=query(db,explain,("уникальныймаркер",)*2)[0][0][0]
    common=query(db,explain,("повторяемый",)*2)[0][0][0]
    assert "archive_text_units_fts_idx" in str(rare)
    assert "archive_text_units_russian_fts_idx" in str(rare)
    assert rare["Plan"]["Actual Rows"] == 1
    assert common["Plan"]["Actual Rows"] == 21
    assert rare["Execution Time"] < 15000 and common["Execution Time"] < 15000


def test_idempotent_install_keeps_data(db):
    before=query(db,"SELECT public.archive_search_status()")[0][0]
    with db.cursor() as cur:
        cur.execute(SEARCH_FILE.read_text())
    assert query(db,"SELECT public.archive_search_status()")[0][0] == before


@pytest.mark.parametrize("parts", [
    {"host":"remote.invalid","dbname":"library_sql_test"},
    {"host":"127.0.0.1","dbname":"postgres"},
    {"host":"127.0.0.1","dbname":"library_sql_test","hostaddr":"192.0.2.1"},
    {"host":"127.0.0.1","dbname":"library_sql_test","service":"production"},
    {"dbname":"library_sql_test"},
])
def test_no_production_dsn_fallback(parts):
    with pytest.raises(ValueError):
        local_test_dsn("synthetic",lambda _:parts)


def test_managed_index_guard(db):
    with db.cursor() as cur:
        cur.execute("COMMENT ON INDEX public.archive_text_units_russian_fts_idx IS 'unmanaged-synthetic'")
    try:
        with pytest.raises(Exception) as caught:
            with db.cursor() as cur:
                cur.execute(SEARCH_FILE.read_text())
        assert "ARCHIVE_SEARCH_UNMANAGED_INDEX" in str(caught.value)
    finally:
        with db.cursor() as cur:
            cur.execute("ROLLBACK")
            cur.execute("COMMENT ON INDEX public.archive_text_units_russian_fts_idx IS 'archive_search:v1'")
