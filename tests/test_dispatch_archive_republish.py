"""Fixed publication protocol: mocks only; no GitHub requests or real token."""
import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/dispatch_archive_republish.py"
spec = importlib.util.spec_from_file_location("peer_dispatch", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
TOKEN = "SYNTHETIC_JOB_TOKEN_NO_REAL_CREDENTIAL"
SHA = "c" * 40


def env():
    return {"GH_TOKEN": TOKEN, "GITHUB_SHA": SHA, "GITHUB_REPOSITORY": module.REPOSITORY,
            "GITHUB_REF": "refs/heads/main", "ARCHIVE_KEY_REPAIRED": "true"}


class Response:
    def __init__(self, status, body=b""):
        self.status = status
        self.body = body
        self.closed = False
        self.limits = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, maximum):
        self.limits.append(maximum)
        return self.body[:maximum]


class Opener:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def verified():
    return Response(200, json.dumps({"commit": {"sha": SHA}}).encode())


def test_exact_two_request_protocol():
    first, second = verified(), Response(204)
    opener = Opener([first, second])
    result = module.dispatch(env(), opener)
    assert result == {"ok": True, "publication_requested": True, "ref": "main", "sha": SHA,
                      "portal_end_to_end_verified": False}
    assert len(opener.calls) == 2
    get, post = [x[0] for x in opener.calls]
    assert get.full_url == module.API + "/branches/main" and get.get_method() == "GET"
    assert post.full_url == module.API + "/actions/workflows/deploy.yml/dispatches"
    assert post.get_method() == "POST" and post.data == b'{"ref":"main"}'
    assert all(timeout == 30 for _, timeout in opener.calls)
    assert first.limits == [131073] and second.limits == []
    assert first.closed and second.closed
    assert all(r.get_header("Authorization") == "Bearer " + TOKEN for r, _ in opener.calls)
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("key,value", [
    ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_REF", "refs/heads/feature"),
    ("GITHUB_REF", "refs/pull/1/merge"), ("ARCHIVE_KEY_REPAIRED", "false"),
    ("ARCHIVE_KEY_REPAIRED", "True"), ("ARCHIVE_KEY_REPAIRED", ""),
    ("GH_TOKEN", ""), ("GITHUB_SHA", "c" * 39), ("GITHUB_SHA", "C" * 40),
    ("GITHUB_SHA", "c" * 40 + "\n"),
])
def test_invalid_context_no_request(key, value):
    values = env()
    values[key] = value
    opener = Opener([])
    with pytest.raises(module.PublishError, match="PUBLICATION_CONTEXT_REJECTED"):
        module.dispatch(values, opener)
    assert opener.calls == []


@pytest.mark.parametrize("body", [
    b"[]", b"null", b"{}", b'{"commit":null}', b'{"commit":{"sha":"different"}}',
    b"bad JSON", b"\xff", b"x" * 131073,
])
def test_wrong_or_oversized_main_response_never_dispatches(body):
    response = Response(200, body)
    opener = Opener([response])
    with pytest.raises(module.PublishError) as error:
        module.dispatch(env(), opener)
    assert TOKEN not in str(error.value)
    assert len(opener.calls) == 1 and response.closed


@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500])
def test_main_http_error_closes_without_retry_or_dispatch(status):
    stream = io.BytesIO(TOKEN.encode())
    error = urllib.error.HTTPError(module.API + "?token=" + TOKEN, status, TOKEN, {"Private": TOKEN}, stream)
    opener = Opener([error])
    with pytest.raises(module.PublishError, match="GITHUB_REQUEST_FAILED"):
        module.dispatch(env(), opener)
    assert stream.closed and len(opener.calls) == 1


@pytest.mark.parametrize("status", [200, 201, 202, 301, 400, 401, 403, 500])
def test_only_dispatch_204_accepted(status):
    response = Response(status, TOKEN.encode())
    opener = Opener([verified(), response])
    with pytest.raises(module.PublishError, match="PUBLICATION_NOT_ACCEPTED"):
        module.dispatch(env(), opener)
    assert len(opener.calls) == 2 and response.closed and response.limits == []


def test_main_error_output_safe(monkeypatch, capsys):
    monkeypatch.setattr(module.urllib.request, "build_opener", lambda _: Opener([
        urllib.error.URLError(TOKEN)]))
    assert module.main(env()) == 1
    result = capsys.readouterr()
    assert TOKEN not in result.out + result.err
    assert json.loads(result.out) == {"ok": False, "error": "PUBLICATION_CHECK_OR_DISPATCH_FAILED"}


def test_redirect_disallowed():
    assert module.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None
