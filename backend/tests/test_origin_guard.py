"""Requests that change something must come from a listed site (spec 0007,
phase E)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.accounts import service as accounts
from app.core.config import settings
from app.core.origin_guard import allowed_origins, origin_of
from app.main import app

PASSWORD = "correct horse battery"
HOME = "https://one.example"


@pytest.fixture
def client(fresh_accounts, monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", HOME)
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://localhost:5173"])
    # Local-development rules for the startup check: the test client speaks
    # http, so it could not send back a Secure cookie.
    monkeypatch.setattr(settings, "DEBUG", True)
    accounts.create_account("ana@example.com", PASSWORD)
    with TestClient(app) as test_client:
        yield test_client


def _sign_in(client, **headers):
    return client.post("/api/v1/auth/password", headers=headers,
                       json={"email": "ana@example.com", "password": PASSWORD})


def test_the_frontend_and_listed_origins_may_change_things(client):
    assert _sign_in(client, Origin=HOME).status_code == 200
    assert _sign_in(client, Origin="http://localhost:5173").status_code == 200
    assert _sign_in(client, Origin="HTTPS://ONE.EXAMPLE").status_code == 200


def test_another_site_is_refused_before_the_route_runs(client):
    refused = _sign_in(client, Origin="https://evil.example")
    assert refused.status_code == 403
    assert "session_id" not in refused.cookies


def test_lookalikes_and_null_are_refused(client):
    for origin in ("https://one.example.evil.example", "http://one.example",
                   "https://one.example:8443", "null", "not a url"):
        assert _sign_in(client, Origin=origin).status_code == 403, origin


def test_referer_stands_in_when_there_is_no_origin(client):
    assert _sign_in(client, Referer=f"{HOME}/login").status_code == 200
    assert _sign_in(client, Referer="https://evil.example/page").status_code == 403


def test_a_request_with_neither_header_is_a_script_not_a_browser(client):
    assert _sign_in(client).status_code == 200


def test_an_origin_beats_a_friendly_referer(client):
    assert _sign_in(client, Origin="https://evil.example", Referer=f"{HOME}/").status_code == 403


def test_the_host_and_forwarded_headers_are_not_trusted(client):
    forged = _sign_in(client, Origin="https://evil.example", Host="one.example",
                      **{"X-Forwarded-Host": "one.example"})
    assert forged.status_code == 403


def test_signed_in_requests_that_change_things_are_checked_too(client):
    _sign_in(client, Origin=HOME)
    assert client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"}
                       ).status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200, "refused: still signed in"
    assert client.post("/api/v1/auth/logout", headers={"Origin": HOME}).status_code == 204


def test_reads_are_not_checked_so_the_snowflake_callback_still_lands(client):
    assert client.get("/api/v1/auth/capabilities",
                      headers={"Origin": "https://evil.example"}).status_code == 200


def test_origin_helpers():
    assert origin_of("https://One.Example:443/path?q=1") == "https://one.example:443"
    assert origin_of("nonsense") is None
    assert allowed_origins(["http://a.test", "bad"], "https://b.test/app") == frozenset(
        {"http://a.test", "https://b.test"})
