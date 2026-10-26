"""Never having connected a warehouse is a fact, not a failure.

A person who signs in with a password and has no Snowflake was told, on an
ordinary visit, to "connect your Snowflake account to use Snowflake data" —
an error message about somebody else's problem, on a deployment that may have
no warehouse at all.

The distinction that fixes it is between a LINK and a CONNECTION. No link
means no warehouse databases and no warehouse dashboards: an empty list. A
link whose stored token has died is worth saying out loud, because connecting
again is something the person can actually do.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.accounts import store as accounts_store
from app.api.deps import get_current_session
from app.main import app
from app.session.base import SessionData


@pytest.fixture(autouse=True)
def store(fresh_accounts):
    yield


def _account(user_id: str, username: str, *, linked: bool) -> None:
    with accounts_store.write() as conn:
        conn.execute(
            "INSERT INTO users (user_id, username, is_admin, is_active, created_at, "
            "failed_logins) VALUES (?, ?, 0, 1, '2026-09-24T00:00:00', 0)",
            (user_id, username),
        )
        if linked:
            conn.execute(
                "INSERT INTO credentials (user_id, kind, subject, secret, updated_at) "
                "VALUES (?, 'snowflake', ?, 'encrypted', '2026-09-24T00:00:00')",
                (user_id, username),
            )


def _as(user_id: str, username: str):
    session = SessionData(session_id="s1", user_id=user_id,
                          snowflake_user=username, auth_mode="password")
    app.dependency_overrides[get_current_session] = lambda: session
    return TestClient(app)


def test_a_password_account_is_not_told_to_connect_snowflake():
    _account("u1", "NOLINK", linked=False)
    client = _as("u1", "NOLINK")
    try:
        with client:
            for path in ("/api/v1/databases", "/api/v1/sources",
                         "/api/v1/dashboards", "/api/v1/connections"):
                answer = client.get(path)
                assert answer.status_code == 200, f"{path} refused"
                assert "Connect your Snowflake" not in answer.text, path
            assert client.get("/api/v1/databases").json() == []
    finally:
        app.dependency_overrides.clear()


def test_a_connected_account_whose_token_died_is_still_told(monkeypatch):
    """The message is worth keeping where it is actionable: this person HAS
    connected Snowflake, and needs to do it again."""
    from app.services.snowflake import SnowflakeNotLinked, SnowflakeService

    async def dead(self, *args, **kwargs):
        raise SnowflakeNotLinked()

    monkeypatch.setattr(SnowflakeService, "get_connection", dead)
    monkeypatch.setattr(SnowflakeService, "execute_query_dict", dead)

    _account("u2", "LINKED", linked=True)
    client = _as("u2", "LINKED")
    try:
        with client:
            answer = client.get("/api/v1/databases")
            assert answer.status_code == 409
            assert "Connect your Snowflake" in answer.text
    finally:
        app.dependency_overrides.clear()


def test_a_link_is_read_locally_not_by_connecting(monkeypatch):
    """The check must not itself need the warehouse, or it would refuse during
    an outage the same way the thing it replaced did."""
    from app.engine.connectors.snowflake import client as snowflake_client

    monkeypatch.setattr(snowflake_client, "get_connection", lambda: pytest.fail(
        "asking whether a link exists must not open a connection"))

    _account("u3", "NOLINK", linked=False)
    client = _as("u3", "NOLINK")
    try:
        with client:
            assert client.get("/api/v1/databases").status_code == 200
    finally:
        app.dependency_overrides.clear()
