"""Offline only: fake HTTP transport, synthetic values, no actual credentials."""
import contextlib
import copy
import email.message
import importlib.util
import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('archive_configuration', Path(__file__).resolve().parents[1] / 'scripts/check_archive_configuration.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
ENV = {'CLOUDFLARE_ACCOUNT_ID': m.ACCOUNT, 'CLOUDFLARE_API_TOKEN': 'SYNTHETIC_CF_TOKEN'}


def fixture():
    return {'success': True, 'result': {'name': m.PROJECT,
        'deployment_configs': {'production': {'env_vars': {m.BINDING: {'type': 'secret_text', 'value': 'SYNTHETIC_PRIVATE_VALUE'}}}},
        'canonical_deployment': {'id': '12345678-aaaa-bbbb-cccc-1234567890ab', 'environment': 'production',
            'deployment_trigger': {'metadata': {'branch': 'main', 'commit_hash': 'a' * 40, 'commit_message': 'SYNTHETIC_PRIVATE_MESSAGE'}},
            'env_vars': {m.BINDING: {'type': 'secret_text', 'value': 'ANOTHER_SYNTHETIC_PRIVATE_VALUE'}}}}}


class Response(io.BytesIO):
    def __init__(self, body=None, *, raw=None, status=200, content_type='application/json', size=None, url=m.URL):
        super().__init__(json.dumps(body).encode() if raw is None else raw)
        self.status = status
        self.url = url
        self.reads = []
        self.headers = email.message.Message()
        self.headers['Content-Type'] = content_type
        if size is not None:
            self.headers['Content-Length'] = size

    def read(self, size=-1):
        self.reads.append(size)
        return super().read(size)

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class DiagnosticTests(unittest.TestCase):
    def test_only_presence_type_and_deployment_metadata_escape(self):
        value = m.summarize(fixture())
        self.assertEqual(value['production_binding'], {'presence': 'present', 'type': 'secret_text'})
        self.assertEqual(value['canonical_deployment']['binding'], value['production_binding'])
        self.assertEqual(value['canonical_deployment']['branch'], 'main')
        self.assertFalse(value['values_verified'])
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertEqual(set(value['canonical_deployment']), {'binding', 'id', 'branch', 'commit'})

    def test_binding_value_property_is_never_accessed(self):
        class Tripwire(dict):
            def get(self, key, default=None):
                if key == 'value':
                    raise AssertionError('Do not inspect credentials')
                return super().get(key, default)
            def __getitem__(self, key):
                if key == 'value':
                    raise AssertionError('Do not inspect credentials')
                return super().__getitem__(key)
        value = fixture()
        value['result']['canonical_deployment']['env_vars'][m.BINDING] = Tripwire(type='secret_text', value=object())
        self.assertEqual(m.summarize(value)['canonical_deployment']['binding']['type'], 'secret_text')

    def test_absent_canonical_env_is_unknown_not_absent(self):
        for replacement in ['missing', None, [], 'invalid']:
            value = fixture()
            if replacement == 'missing':
                del value['result']['canonical_deployment']['env_vars']
            else:
                value['result']['canonical_deployment']['env_vars'] = replacement
            self.assertEqual(m.summarize(value)['canonical_deployment']['binding']['presence'], 'unknown')
        value = fixture()
        value['result']['canonical_deployment']['env_vars'] = {}
        self.assertEqual(m.summarize(value)['canonical_deployment']['binding']['presence'], 'absent')

    def test_production_and_canonical_are_independent(self):
        value = fixture()
        value['result']['canonical_deployment']['env_vars'] = {}
        out = m.summarize(value)
        self.assertEqual(out['production_binding']['presence'], 'present')
        self.assertEqual(out['canonical_deployment']['binding']['presence'], 'absent')
        del value['result']['deployment_configs']
        self.assertEqual(m.summarize(value)['production_binding']['presence'], 'unknown')

    def test_binding_types_are_closed_and_values_ignored(self):
        for kind in ['plain_text', 'secret_text', 'PRIVATE_BAD_TYPE', None, {}]:
            value = fixture()
            value['result']['deployment_configs']['production']['env_vars'][m.BINDING]['type'] = kind
            out = m.summarize(value)['production_binding']
            self.assertEqual(out['type'], kind if kind in ('plain_text', 'secret_text') else 'unknown')
            self.assertNotIn('PRIVATE', json.dumps(out))

    def test_invalid_or_missing_deployment_metadata_does_not_fabricate_it(self):
        value = fixture()
        value['result']['canonical_deployment']['environment'] = 'preview'
        out = m.summarize(value)['canonical_deployment']
        self.assertEqual(out, {'binding': {'presence': 'unknown', 'type': None}, 'id': None, 'branch': None, 'commit': None})
        value = fixture()
        value['result']['canonical_deployment']['id'] = '<PRIVATE>'
        value['result']['canonical_deployment']['deployment_trigger']['metadata'] = {'branch': 'sb_secret_SYNTHETIC', 'commit_hash': 'PRIVATE'}
        out = m.summarize(value)['canonical_deployment']
        self.assertIsNone(out['id'])
        self.assertIsNone(out['branch'])
        self.assertIsNone(out['commit'])

    def test_fixed_get_timeout_no_redirect_and_single_request(self):
        response = Response(fixture())
        opener = Opener(response)
        m.check_configuration(ENV, opener)
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, m.URL)
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(timeout, 30)
        self.assertEqual(response.reads, [m.MAX_BYTES + 1])
        self.assertIsNone(m.NoRedirect().redirect_request(None, None, 302, 'x', {}, 'https://other.invalid'))

    def test_wrong_account_and_missing_token_never_open(self):
        for mutation in [{'CLOUDFLARE_ACCOUNT_ID': 'a'*32}, {'CLOUDFLARE_API_TOKEN': ''}, {'CLOUDFLARE_API_TOKEN': 'token\nheader'}]:
            opener = Opener(Response(fixture()))
            with self.assertRaises(m.DiagnosticError):
                m.check_configuration({**ENV, **mutation}, opener)
            self.assertEqual(opener.calls, [])

    def test_response_cap_applies_with_and_without_content_length(self):
        for response in [Response(raw=b'x'*(m.MAX_BYTES+1)), Response(fixture(), size=str(m.MAX_BYTES+1)), Response(fixture(), size='invalid')]:
            with self.assertRaisesRegex(m.DiagnosticError, '^RESPONSE_LIMIT$'):
                m.check_configuration(ENV, Opener(response))

    def test_http_errors_not_read_or_retried(self):
        class NeverRead(io.BytesIO):
            def read(self, *_):
                raise AssertionError('Error body must not be read')
        for code in [301, 302, 403, 429, 500]:
            error = urllib.error.HTTPError(m.URL, code, 'SYNTHETIC_PRIVATE_REASON', {}, NeverRead(b'PRIVATE_BODY'))
            opener = Opener(error=error)
            with self.assertRaisesRegex(m.DiagnosticError, '^HTTP_READ_FAILED$'):
                m.check_configuration(ENV, opener)
            self.assertEqual(len(opener.calls), 1)

    def test_invalid_json_duplicate_keys_and_untrusted_schema_fail_closed(self):
        bad = [b'{"success":true,"success":false}', b'{"success":NaN}', b'PRIVATE_NOT_JSON', b'[]']
        for raw in bad:
            with self.assertRaises(m.DiagnosticError):
                m.check_configuration(ENV, Opener(Response(raw=raw)))
        for response in [Response(fixture(), url='https://other.invalid'), Response(fixture(), content_type='text/html')]:
            with self.assertRaises(m.DiagnosticError):
                m.check_configuration(ENV, Opener(response))
        for body in [{'success': True, 'result': {}}, {'success': False, 'errors': ['PRIVATE']}]:
            with self.assertRaises(m.DiagnosticError):
                m.summarize(body)

    def test_main_prints_closed_error_without_raw_details(self):
        for error in [RuntimeError('PRIVATE_KEY_RESPONSE'), m.DiagnosticError('PRIVATE_KEY_RESPONSE'), m.DiagnosticError({'value': 'PRIVATE'}), urllib.error.URLError('PRIVATE_URL')]:
            capture = io.StringIO()
            with patch.object(m, 'check_configuration', side_effect=error), contextlib.redirect_stdout(capture):
                self.assertEqual(m.main(ENV), 1)
            self.assertNotIn('PRIVATE', capture.getvalue())
            self.assertEqual(json.loads(capture.getvalue()), {'ok': False, 'error': 'DIAGNOSTIC_FAILED'})

    def test_timeouts_and_network_errors_are_single_attempt_safe_failures(self):
        for error in [TimeoutError('PRIVATE_TIMEOUT'), urllib.error.URLError('PRIVATE_URL'), OSError('PRIVATE_SOCKET')]:
            opener = Opener(error=error)
            with self.assertRaisesRegex(m.DiagnosticError, '^TRANSPORT_READ_FAILED$'):
                m.check_configuration(ENV, opener)
            self.assertEqual(len(opener.calls), 1)

    def test_non_200_response_is_not_read(self):
        response = Response(raw=b'PRIVATE_REDIRECT', status=302)
        with self.assertRaisesRegex(m.DiagnosticError, '^HTTP_READ_FAILED$'):
            m.check_configuration(ENV, Opener(response))
        self.assertEqual(response.reads, [])

    def test_missing_entire_canonical_deployment_preserves_unknown(self):
        for value in [None, [], 'PRIVATE']:
            source = fixture()
            source['result']['canonical_deployment'] = value
            out = m.summarize(source)['canonical_deployment']
            self.assertEqual(out, {'binding': {'presence': 'unknown', 'type': None}, 'id': None, 'branch': None, 'commit': None})

    def test_all_preview_bindings_and_unrequested_fields_are_ignored(self):
        source = fixture()
        source['result']['deployment_configs']['preview'] = {'env_vars': {'OTHER_PRIVATE_KEY': {'type': 'secret_text', 'value': 'PRIVATE'}}}
        source['result']['canonical_deployment']['build_config'] = {'web_analytics_token': 'PRIVATE'}
        self.assertNotIn('PRIVATE', json.dumps(m.summarize(source)))

    def test_schema_summarization_does_not_mutate_received_metadata(self):
        source = fixture()
        before = copy.deepcopy(source)
        m.summarize(source)
        self.assertEqual(source, before)


if __name__ == '__main__':
    unittest.main()
