"""Activation checks use invented connections and never contact production."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "archive_activation", Path(__file__).resolve().parents[1] / "scripts/activate_archive_search.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def good_params():
    return {"host": "aws-0-eu-central-1.pooler.supabase.com", "port": "5432",
            "user": "postgres." + mod.PROJECT, "dbname": "postgres", "password": "invented"}


@pytest.mark.parametrize("change", [
    {"host": "attacker.example"}, {"user": "postgres.otherproject"},
    {"port": "6543"}, {"dbname": "another"}, {"password": ""},
    {"hostaddr": "127.0.0.1"}, {"service": "another"}, {"servicefile": "/tmp/other"},
])
def test_other_connections_fail_before_database_contact(change):
    with pytest.raises(mod.ActivationError):
        mod.validate_dsn("invented", lambda _: good_params() | change)


def test_session_connection_has_server_timeouts_and_tls():
    params = mod.validate_dsn("invented", lambda _: good_params() | {
        "sslmode": "disable", "passfile": "/tmp/other", "options": "invented override"})
    assert params["sslmode"] == "require"
    assert params["connect_timeout"] == "20"
    assert "statement_timeout=120000" in params["options"]
    assert "lock_timeout=5000" in params["options"]
    assert "passfile" not in params


def test_parse_error_is_not_echoed():
    def bad(_):
        raise ValueError("invented-sensitive-connection")
    with pytest.raises(mod.ActivationError) as caught:
        mod.validate_dsn("invented", bad)
    assert str(caught.value) == "SUPABASE_DB_URL_INVALID"


def test_remote_configuration_is_reduced_to_presence_booleans():
    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url.endswith("/pages/projects/kvant-sourcing-f122")
            assert timeout == 30
            return io.BytesIO(json.dumps({"success": True, "result": {
                "deployment_configs": {"production": {"env_vars": {
                    "SUPABASE_SERVICE_KEY": {"type": "secret_text", "value": "invented-private"},
                    "CF_ACCESS_AUD": {"type": "plain_text", "value": "invented-audience"}}}}}}).encode())
    result = mod.portal_bindings({"CLOUDFLARE_ACCOUNT_ID": "a" * 32,
                                  "CLOUDFLARE_API_TOKEN": "invented-token"}, FakeOpener())
    assert result == {"checked": True, "service_key_binding_present": True,
                      "access_audience_binding_present": True, "values_verified": False}
    assert "invented" not in json.dumps(result)


def test_missing_configuration_never_contacts_cloudflare():
    class NoNetwork:
        def open(self, *_args, **_kwargs):
            raise AssertionError("network must not run")
    assert mod.portal_bindings({}, NoNetwork())["checked"] is False


def test_opaque_database_failure_never_prints_raw_exception(monkeypatch, capsys):
    def fail(_):
        raise RuntimeError("invented-sensitive-row-or-password")
    monkeypatch.setattr(mod, "activate", fail)
    assert mod.main({}) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False, "error": "ARCHIVE_ACTIVATION_FAILED"}


def test_frozen_schema_has_expected_digest():
    raw = (mod.ROOT / "library/supabase/archive_schema.sql").read_bytes()
    assert mod.hashlib.sha256(raw).hexdigest() == mod.SCHEMA_SHA
