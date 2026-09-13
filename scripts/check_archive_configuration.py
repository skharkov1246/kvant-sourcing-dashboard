"""Read one fixed Pages project; report binding metadata only, never values.

Schema: https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/get/
No Supabase/SQL, file writes, redirects, retries, credential inspection or mutation.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

ACCOUNT = '183c943a62280a0a37852a107ba1da26'
PROJECT = 'kvant-sourcing-f122'
URL = f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/pages/projects/{PROJECT}'
BINDING = 'SUPABASE_SERVICE_KEY'
MAX_BYTES = 1024 * 1024
TIMEOUT = 30


class DiagnosticError(Exception):
    """Only fixed error identifiers may be exposed."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def object_without_duplicates(pairs):
    result = {}
    for key, item in pairs:
        if key in result:
            raise DiagnosticError('RESPONSE_SCHEMA_INVALID')
        result[key] = item
    return result


def reject_constant(_):
    raise DiagnosticError('RESPONSE_SCHEMA_INVALID')


def child(value, name):
    return value.get(name) if isinstance(value, dict) else None


def binding_metadata(container):
    """Absent env_vars is unknown, even on an otherwise valid deployment."""
    variables = child(container, 'env_vars')
    if not isinstance(variables, dict):
        return {'presence': 'unknown', 'type': None}
    if BINDING not in variables:
        return {'presence': 'absent', 'type': None}
    binding = variables[BINDING]
    kind = child(binding, 'type')
    # Deliberately never inspect or copy the value property.
    return {'presence': 'present', 'type': kind if kind in ('plain_text', 'secret_text') else 'unknown'}


def safe_id(value):
    if isinstance(value, str) and 8 <= len(value) <= 80 and re.fullmatch(r'[a-fA-F0-9]+(?:-[a-fA-F0-9]+)*', value):
        return value
    return None


def safe_branch(value):
    if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', value)
            and '..' not in value and not value.endswith('/') and not value.startswith(('sb_secret_', 'eyJ'))):
        return value
    return None


def summarize(body):
    if not isinstance(body, dict) or body.get('success') is not True:
        raise DiagnosticError('API_NOT_SUCCESSFUL')
    result = body.get('result')
    if not isinstance(result, dict) or result.get('name') != PROJECT:
        raise DiagnosticError('RESPONSE_SCHEMA_INVALID')
    production = child(child(result, 'deployment_configs'), 'production')
    deployment = child(result, 'canonical_deployment')
    # An explicitly different environment is not trustworthy production evidence.
    if isinstance(deployment, dict) and deployment.get('environment', 'production') != 'production':
        deployment = None
    metadata = child(child(deployment, 'deployment_trigger'), 'metadata')
    commit = child(metadata, 'commit_hash')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', commit):
        commit = None
    return {
        'ok': True,
        'production_binding': binding_metadata(production),
        'canonical_deployment': {
            'binding': binding_metadata(deployment),
            'id': safe_id(child(deployment, 'id')),
            'branch': safe_branch(child(metadata, 'branch')),
            'commit': commit,
        },
        'values_verified': False,
        'runtime_binding_verified': False,
    }


def check_configuration(env, opener=None):
    account = env.get('CLOUDFLARE_ACCOUNT_ID')
    token = env.get('CLOUDFLARE_API_TOKEN')
    if account != ACCOUNT or not isinstance(token, str) or not token or len(token) > 8192 or re.search(r'[\s\x00-\x1f\x7f]', token):
        raise DiagnosticError('CONFIGURATION_UNAVAILABLE')
    opener = opener or urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(URL, method='GET', headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            if response.status != 200:
                raise DiagnosticError('HTTP_READ_FAILED')
            if response.geturl() != URL:
                raise DiagnosticError('UNEXPECTED_RESPONSE_URL')
            if response.headers.get_content_type() != 'application/json':
                raise DiagnosticError('RESPONSE_SCHEMA_INVALID')
            declared = response.headers.get('Content-Length')
            if declared is not None and (not re.fullmatch(r'[0-9]+', declared) or int(declared) > MAX_BYTES):
                raise DiagnosticError('RESPONSE_LIMIT')
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise DiagnosticError('RESPONSE_LIMIT')
        body = json.loads(raw, object_pairs_hook=object_without_duplicates, parse_constant=reject_constant)
        return summarize(body)
    except DiagnosticError:
        raise
    except urllib.error.HTTPError:
        # Do not read error bodies/headers or serialize exception text.
        raise DiagnosticError('HTTP_READ_FAILED') from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise DiagnosticError('TRANSPORT_READ_FAILED') from None
    except (ValueError, TypeError, KeyError, RecursionError):
        raise DiagnosticError('RESPONSE_SCHEMA_INVALID') from None


ERRORS = frozenset({'CONFIGURATION_UNAVAILABLE', 'API_NOT_SUCCESSFUL', 'RESPONSE_SCHEMA_INVALID',
                    'HTTP_READ_FAILED', 'UNEXPECTED_RESPONSE_URL', 'RESPONSE_LIMIT', 'TRANSPORT_READ_FAILED'})


def main(env=None):
    try:
        report = check_configuration(os.environ if env is None else env)
        code = 0
    except DiagnosticError as error:
        reason = error.args[0] if len(error.args) == 1 and isinstance(error.args[0], str) and error.args[0] in ERRORS else 'DIAGNOSTIC_FAILED'
        report = {'ok': False, 'error': reason}
        code = 1
    except Exception:  # noqa: BLE001 - final privacy boundary must never print an arbitrary exception.
        report = {'ok': False, 'error': 'DIAGNOSTIC_FAILED'}
        code = 1
    print(json.dumps(report, ensure_ascii=True, separators=(',', ':')))
    return code


if __name__ == '__main__':
    sys.exit(main())
