import hashlib
import importlib.util
import io
import json
import sys
import types
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'scripts/repair_archive_key.py'
spec = importlib.util.spec_from_file_location('repair_tested', PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
KEY = 'sb_secret_SYNTHETIC_REPAIR_ONLY'
TOKEN = 'SYNTHETIC_CF_TOKEN'


def status():
    return {'scope': 'imported_archive_text', 'all_versions': True, 'full_archive': False,
            'storage_files_outside_import_not_searched': True, 'semantic_understanding_measured': False,
            'counts': {x: '1' for x in ('objects', 'object_versions', 'extraction_versions', 'text_units', 'logical_blobs', 'attachment_observations', 'import_batches', 'ingestion_runs')}}


def search():
    return {'scope': 'imported_archive_text', 'all_versions': True, 'full_archive': False,
            'search_mode': 'plain_terms', 'order': 'extraction_id_unit_key_asc', 'has_more': True, 'returned': 1,
            'items': [{'text': 'Синтетический компрессор', 'locator': {}, 'extraction_id': '12', 'is_excerpt': True,
                       'text_length': len('Синтетический компрессор'), 'text_truncated': False, 'extraction_output_sha256': 'a' * 64, 'text_sha256': 'b' * 64}]}


def project(new=False):
    return {'success': True, 'result': {'name': r.PAGES, 'subdomain': r.PAGES + '.pages.dev', 'deployment_configs': {
        'production': {'wrangler_config_hash': 'synthetic-hash', 'kv_namespaces': {'ACL': {'namespace_id': 'keep'}},
                       'env_vars': {'OTHER': {'type': 'plain_text', 'value': 'unchanged'}, 'SUPABASE_SERVICE_KEY': {'type': 'secret_text', 'value': 'redacted' if new else 'old'}}},
        'preview': {'env_vars': {'OTHER': {'type': 'plain_text', 'value': 'keep-preview'}}}}}}


class FakeDB:
    def __init__(self, phase='staged', live=True):
        self.phase, self.live = phase, live
        self.keys = 0
        self.cleanups = []
        self.barriers = 0

    def start(self):
        pass

    def state(self):
        return self.phase, 'synthetic-uuid', self.live

    def key(self):
        self.keys += 1
        self.phase = 'validating'
        return KEY

    def applying(self):
        if not self.live:
            raise r.RepairError('EXPIRED')
        self.phase = 'applying'
        self.barriers += 1

    def cleanup(self, terminal):
        self.cleanups.append(terminal)
        if self.phase not in r.TERMINAL:
            self.phase = terminal


class FakeHttp:
    def __init__(self, db, fail=None, mutate=None):
        self.db, self.fail, self.mutate = db, fail, mutate
        self.calls = []

    def call(self, method, url, credential, payload=None):
        self.calls.append((method, url, credential, payload))
        i = len(self.calls)
        if i == self.fail:
            raise RuntimeError(KEY + TOKEN + 'PRIVATE_RESPONSE')
        if i == 1:
            assert method == 'POST' and url == r.SB_URL + 'archive_search_status' and credential == KEY and payload == {}
            return status()
        if i == 2:
            assert method == 'POST' and url == r.SB_URL + 'archive_search_text' and credential == KEY
            assert payload == {'search_query': 'компрессор', 'result_limit': 1, 'source_kind': None}
            out = search()
            if self.mutate == 'bad_search':
                out['returned'] = 0
            return out
        if i == 3:
            assert method == 'GET' and url == r.CF_URL and credential == TOKEN
            if self.mutate == 'expired':
                self.db.live = False
            return project()
        if i == 4:
            assert self.db.phase == 'applying' and self.db.barriers == 1
            assert method == 'PATCH' and url == r.CF_URL and credential == TOKEN
            assert payload == {'deployment_configs': {'production': {'env_vars': {'SUPABASE_SERVICE_KEY': {'type': 'secret_text', 'value': KEY}}, 'wrangler_config_hash': 'synthetic-hash'}}}
            return project(True)
        assert i == 5 and method == 'GET' and url == r.CF_URL
        out = project(True)
        if self.mutate == 'preview':
            out['result']['deployment_configs']['preview']['env_vars']['OTHER']['value'] = 'changed'
        if self.mutate == 'kv':
            out['result']['deployment_configs']['production']['kv_namespaces'] = {}
        if self.mutate == 'other_env':
            out['result']['deployment_configs']['production']['env_vars']['OTHER']['value'] = 'changed'
        return out


def test_success_one_patch_after_two_live_validations_preserves_other_bindings():
    db = FakeDB()
    http = FakeHttp(db)
    result = r.repair(db, http, TOKEN)
    assert result == {'phase': 'complete', 'ready_to_publish': True}
    assert db.cleanups == ['complete'] and db.keys == 1 and len(http.calls) == 5
    assert sum(x[0] == 'PATCH' for x in http.calls) == 1


@pytest.mark.parametrize('phase,live,expected', [('awaiting', True, 'awaiting'), ('awaiting', False, 'expired'),
    ('staged', False, 'expired'), ('validating', True, 'failed'), ('applying', True, 'application_unknown'),
    ('complete', True, 'complete'), ('failed', True, 'failed'), ('expired', True, 'expired'), ('application_unknown', True, 'application_unknown')])
def test_idle_expired_and_terminal_never_read_key_or_patch(phase, live, expected):
    db = FakeDB(phase, live)
    http = FakeHttp(db)
    result = r.repair(db, http, TOKEN)
    assert result == {'phase': expected, 'ready_to_publish': expected == 'complete'}
    assert db.keys == 0 and http.calls == []


@pytest.mark.parametrize('failure', [1, 2, 3, 4, 5])
def test_network_error_cleanup_and_no_blind_retry(failure):
    db = FakeDB()
    http = FakeHttp(db, fail=failure)
    with pytest.raises(r.RepairError, match='^' + ('PATCH_UNKNOWN' if failure >= 4 else 'REPAIR_FAILED') + '$'):
        r.repair(db, http, TOKEN)
    assert db.cleanups == ['application_unknown' if failure >= 4 else 'failed']
    assert sum(x[0] == 'PATCH' for x in http.calls) == (1 if failure >= 4 else 0)
    again = FakeHttp(db)
    r.repair(db, again, TOKEN)
    assert not again.calls


@pytest.mark.parametrize('mutation', ['expired', 'bad_search', 'preview', 'kv', 'other_env'])
def test_expiry_contract_failure_or_other_binding_change_never_publishes(mutation):
    db = FakeDB()
    http = FakeHttp(db, mutate=mutation)
    with pytest.raises(r.RepairError):
        r.repair(db, http, TOKEN)
    assert db.phase != 'complete'
    if mutation in ['expired', 'bad_search']:
        assert not any(x[0] == 'PATCH' for x in http.calls)


@pytest.mark.parametrize('value', [None, '', 1, ' '+KEY, KEY+' ', '"'+KEY+'"', KEY+'\\', 'sb_publishable_SYNTHETIC', KEY[:15]+'\n'+KEY[15:]])
def test_staged_key_is_never_normalized(value):
    assert r.valid_key(value) is False


def test_SQL_pin_and_workflow_scope():
    text = r.SQL_PATH.read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == r.SQL_SHA256
    assert "SELECT 'awaiting', t, t + interval '2 hours'" in text
    assert "REVOKE ALL ON FUNCTION public.archive_stage_portal_key_20260912(text) FROM PUBLIC, anon, authenticated" in text
    assert 'SET search_path = \'\'' in text and 'SECURITY DEFINER' in text
    assert 'CREATE OR REPLACE' not in text and 'CREATE EXTENSION' not in text
    assert text.strip().endswith('COMMIT;')
    workflow = (r.ROOT / '.github/workflows/archive-key-repair.yml').read_text()
    assert "github.ref == 'refs/heads/main'" in workflow
    repair_scope, publish_scope = workflow.split('\n  publish:', 1)
    assert 'contents: read' in repair_scope and 'actions: write' not in repair_scope
    assert 'needs: repair' in publish_scope
    assert "if: github.ref == 'refs/heads/main' && needs.repair.outputs.ready_to_publish == 'true'" in publish_scope
    assert 'actions: write' in publish_scope and workflow.count('actions: write') == 1
    assert 'secrets.' not in publish_scope and 'GH_TOKEN: ${{ github.token }}' in publish_scope
    assert 'always()' not in publish_scope and 'failure()' not in publish_scope
    assert 'run: python scripts/dispatch_archive_republish.py' in publish_scope
    assert 'SUPABASE_SECRET_KEY' not in workflow and 'upload-artifact' not in workflow
    assert 'ready_to_publish: ${{ steps.repair.outputs.ready_to_publish }}' in workflow


@pytest.mark.parametrize('extra', [{'hostaddr':'1.2.3.4'}, {'service':'x'}, {'servicefile':'x'}, {'host':'attacker.example'}, {'user':'other'}, {'port':'6543'}])
def test_dsn_scope(extra):
    params = {'host': f'db.{r.PROJECT}.supabase.co', 'user':'postgres', 'dbname':'postgres', 'password':'SYNTHETIC'}
    params.update(extra)
    with pytest.raises(r.RepairError):
        r.dsn_params('SYNTHETIC_DSN', lambda _:params)


def test_main_suppresses_arbitrary_exception_and_key(capsys, monkeypatch):
    fake = types.ModuleType('psycopg2')
    extensions = types.ModuleType('psycopg2.extensions')
    extensions.parse_dsn = lambda _: {'host': f'db.{r.PROJECT}.supabase.co', 'user':'postgres', 'dbname':'postgres', 'password':'SYNTHETIC'}
    def failed(**kwargs):
        raise RuntimeError(KEY + TOKEN)
    fake.connect = failed
    monkeypatch.setitem(sys.modules, 'psycopg2', fake)
    monkeypatch.setitem(sys.modules, 'psycopg2.extensions', extensions)
    assert r.main({'CLOUDFLARE_ACCOUNT_ID':r.ACCOUNT, 'CLOUDFLARE_API_TOKEN':TOKEN, 'SUPABASE_DB_URL':'SYNTHETIC_DSN'}) == 1
    out = capsys.readouterr()
    assert KEY not in out.out+out.err and TOKEN not in out.out+out.err
    assert json.loads(out.out)['error'] == 'REPAIR_FAILED'


def test_http_fixed_routes_no_proxy_and_bounded_transport():
    class Reply(io.BytesIO):
        status = 200
        @property
        def headers(self): return {'Content-Type':'application/json'}
        def geturl(self): return r.SB_URL+'archive_search_status'
    class Opener:
        def open(self, req, timeout):
            assert timeout == 20 and req.method == 'POST'
            assert req.headers['Apikey'] == KEY and 'Authorization' not in req.headers
            return Reply(json.dumps(status()).encode())
    h = r.Http(Opener())
    assert h.call('POST',r.SB_URL+'archive_search_status',KEY,{})['scope'] == 'imported_archive_text'
    with pytest.raises(r.RepairError): h.call('POST','https://attacker.example',KEY,{})
    assert h.calls == 1


def test_rpc_probe_rejects_empty_and_wrong_types():
    for value in [{}, {'items':[]}, dict(search(),returned=True), dict(search(),items=[])]:
        with pytest.raises(r.RepairError): r.search_valid(value)
    broken = status(); broken['counts']['text_units'] = 1
    with pytest.raises(r.RepairError): r.status_valid(broken)
