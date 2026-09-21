"""Synthetic diagnostics and parameter-binding regressions; CI DB is localhost only."""
import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'scripts/repair_archive_key.py'
spec = importlib.util.spec_from_file_location('repair_diagnostic_tested', PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
PRIVATE = 'SYNTHETIC_PRIVATE_NOT_FOR_OUTPUT'
CI_DSN = 'postgresql://postgres:synthetic-library-ci@127.0.0.1:5432/library_sql_test'


class Cursor:
    rowcount = 1
    def __init__(self, calls, rows):
        self.calls, self.rows = calls, rows
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, *args):
        self.calls.append(args)
        if len(args) == 2 and args[1] == () and '%' in args[0]:
            raise IndexError(PRIVATE)
    def fetchmany(self, count):
        assert count == 2
        return self.rows


class Connection:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = [('synthetic',)] if rows is None else rows
        self.closed = False
    def cursor(self):
        return Cursor(self.calls, self.rows)
    def close(self):
        self.closed = True


@pytest.mark.parametrize('method', ['execute', 'one'])
def test_no_args_literal_percent_and_pinned_install_sql(method):
    conn = Connection()
    db = r.Database(conn)
    sql = r.SQL_PATH.read_text()
    assert '%ROWTYPE' in sql
    getattr(db, method)(sql)
    getattr(db, method)("SELECT '100% literal'", ())
    assert conn.calls == [(sql,), ("SELECT '100% literal'",)]


@pytest.mark.parametrize('method', ['execute', 'one'])
def test_nonempty_parameters_are_bound_unchanged(method):
    conn = Connection()
    db = r.Database(conn)
    args = ('synthetic-value',)
    getattr(db, method)('SELECT %s', args)
    assert conn.calls == [('SELECT %s', args)]
    assert conn.calls[0][1] is args


def test_start_passes_entire_pinned_sql_raw(monkeypatch):
    conn = Connection()
    db = r.Database(conn)
    def one(sql, args=()):
        return ('postgres',) if sql == 'SELECT current_user' else (True,)
    monkeypatch.setattr(db, 'one', one)
    monkeypatch.setattr(db, 'metadata', lambda: None)
    db.start()
    assert conn.calls == [(r.SQL_PATH.read_text(),), ("NOTIFY pgrst, 'reload schema'",)]
    assert db.progress['stage'] == 'repair'


class DatabaseFailure(Exception):
    def __init__(self, code, primary):
        self.pgcode = code
        self.diag = types.SimpleNamespace(message_primary=primary)
    def __str__(self):
        raise AssertionError('arbitrary exception text must not be accessed')


@pytest.mark.parametrize('code', ['P0001', '42501', '23502', 'XX000', '08006'])
def test_valid_sqlstate_is_exact(code):
    result = r.safe_diagnostics(DatabaseFailure(code, PRIVATE), {'stage': 'start_install'})
    assert result == {'stage': 'start_install', 'sqlstate': code}


@pytest.mark.parametrize('code', [None, '', 1, b'P0001', 'p0001', 'P0001\n', ' P0001', 'P0001 '+PRIVATE, 'P0001\x00', 'Ａ0001'])
def test_sqlstate_never_normalized_or_coerced(code):
    assert r.safe_diagnostics(DatabaseFailure(code, PRIVATE), {'stage':'connect'}) == {'stage':'connect'}


@pytest.mark.parametrize('primary', [None, 1, b'REPAIR_VAULT_UNAVAILABLE', PRIVATE, 'REPAIR_VAULT_UNAVAILABLE '+PRIVATE, '\nREPAIR_VAULT_UNAVAILABLE'])
def test_primary_message_only_exact_whitelist(primary):
    assert r.safe_diagnostics(DatabaseFailure(None, primary), {'stage':'metadata'}) == {'stage':'metadata'}


def test_all_own_sql_messages_and_no_other_messages_allowed():
    sql_messages = set(re.findall(r"RAISE EXCEPTION '([^']+)'", r.SQL_PATH.read_text()))
    assert sql_messages == r.SQL_ERRORS
    for message in sql_messages:
        assert r.safe_diagnostics(DatabaseFailure('P0001', message), {'stage':'start_install'}) == {
            'stage':'start_install', 'sqlstate':'P0001', 'sql_error':message}


def test_raising_diagnostic_properties_and_unknown_stage_do_not_leak():
    class Hostile(Exception):
        @property
        def pgcode(self):
            raise RuntimeError(PRIVATE)
        @property
        def diag(self):
            raise RuntimeError(PRIVATE)
        def __str__(self):
            raise AssertionError('no stringification')
    for stage in (PRIVATE, None, [], {}, 1):
        assert r.safe_diagnostics(Hostile(), {'stage':stage}) == {'stage':'repair'}
    assert r.safe_diagnostics(IndexError(PRIVATE), {'stage':'start_install'}) == {
        'stage':'start_install', 'exception_type':'IndexError'}
    assert r.safe_diagnostics(RuntimeError(PRIVATE), {'stage':'connect'}) == {'stage':'connect'}


def install_fake_driver(monkeypatch, connect):
    driver = types.ModuleType('psycopg2')
    ext = types.ModuleType('psycopg2.extensions')
    ext.parse_dsn = lambda _: {'host':f'db.{r.PROJECT}.supabase.co','user':'postgres','dbname':'postgres','password':PRIVATE}
    driver.connect = connect
    monkeypatch.setitem(sys.modules, 'psycopg2', driver)
    monkeypatch.setitem(sys.modules, 'psycopg2.extensions', ext)


def environment():
    return {'CLOUDFLARE_ACCOUNT_ID':r.ACCOUNT,'CLOUDFLARE_API_TOKEN':PRIVATE,'SUPABASE_DB_URL':PRIVATE}


@pytest.mark.parametrize('stage', ['connect', 'start_install', 'metadata'])
def test_main_safe_stage_and_database_error_no_http(monkeypatch, capsys, stage):
    conn = Connection()
    failure = DatabaseFailure('P0001', 'REPAIR_VAULT_UNAVAILABLE')
    def connect(**kwargs):
        if stage == 'connect':
            raise failure
        return conn
    install_fake_driver(monkeypatch, connect)
    def failed_repair(db, http, token):
        db.progress['stage'] = stage
        raise failure
    monkeypatch.setattr(r, 'repair', failed_repair)
    monkeypatch.setattr(r.Http, 'call', lambda *args: pytest.fail('no network'))
    assert r.main(environment()) == 1
    out = capsys.readouterr()
    assert not out.err and PRIVATE not in out.out
    assert json.loads(out.out) == {'ok':False,'error':'REPAIR_FAILED','phase':'failed','ready_to_publish':False,
                                   'stage':stage,'sqlstate':'P0001','sql_error':'REPAIR_VAULT_UNAVAILABLE'}
    assert conn.closed is (stage != 'connect')


def test_metadata_failure_retains_stage(monkeypatch):
    db = r.Database(Connection(), {'stage':'repair'})
    def failed(*args):
        raise DatabaseFailure('42501', PRIVATE)
    monkeypatch.setattr(db, 'one', failed)
    with pytest.raises(DatabaseFailure):
        db.metadata()
    assert db.progress['stage'] == 'metadata'


def test_actual_psycopg_literal_percent_and_pinned_sql():
    dsn = os.environ.get('LIBRARY_SQL_TEST_DSN')
    if not dsn:
        pytest.skip('Local synthetic PostgreSQL CI service is not configured')
    # Reject remote, production, alternative databases, query options, or injected DSNs.
    assert dsn == CI_DSN, 'Only the fixed localhost synthetic CI database is allowed'
    import psycopg2
    conn = psycopg2.connect(dsn, connect_timeout=5)
    try:
        sql = r.SQL_PATH.read_text()
        with conn.cursor() as cur:
            with pytest.raises(IndexError):
                cur.mogrify(sql, ())
            assert cur.mogrify(sql) == sql.encode('utf-8')
        db = r.Database(conn)
        db.execute('DO $$ DECLARE state pg_catalog.pg_class%ROWTYPE; BEGIN NULL; END $$;')
        assert db.one("SELECT '100% literal'") == ('100% literal',)
        assert db.one('SELECT %s', ('synthetic-value',)) == ('synthetic-value',)
    finally:
        conn.close()
