"""What happens when time runs out: idle Snowflake connections, expired
Snowflake sign-ins, and responses that must never be reused."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import TokenEncryptor
from app.services import connection_cache as cache_module
from app.services.connection_cache import ConnectionCache
from app.services.snowflake import SnowflakeNotLinked, SnowflakeService, is_session_gone
from app.session.base import SessionData
from app.session.memory import InMemorySessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Conn:
    """A connection whose server may or may not still answer."""

    def __init__(self, alive=True):
        self.alive = alive
        self.probes = 0
        self.closed = False

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def cursor(self):
        conn = self

        class Cursor:
            def execute(self, sql, timeout=None):
                conn.probes += 1
                if not conn.alive:
                    raise RuntimeError("390114: Authentication token has expired")

            def close(self):
                pass

        return Cursor()


# --- idle connections -------------------------------------------------------------

def test_a_connection_in_use_is_not_checked():
    clock = Clock()
    cache = ConnectionCache(clock=clock, idle_check_seconds=600)
    first = cache.get_or_create("s", Conn)
    clock.now += 60
    assert cache.get_or_create("s", Conn) is first
    assert first.probes == 0


def test_an_idle_connection_that_still_answers_is_reused():
    clock = Clock()
    cache = ConnectionCache(clock=clock, idle_check_seconds=600)
    first = cache.get_or_create("s", Conn)
    clock.now += 601
    assert cache.get_or_create("s", Conn) is first
    assert first.probes == 1


def test_an_idle_connection_snowflake_has_ended_is_replaced_quietly():
    clock = Clock()
    cache = ConnectionCache(clock=clock, idle_check_seconds=600)
    first = cache.get_or_create("s", Conn)
    first.alive = False
    clock.now += 601
    second = cache.get_or_create("s", Conn)
    assert second is not first
    assert first.closed and not second.closed


def test_the_ended_session_errors_are_recognised():
    assert is_session_gone(RuntimeError("390114 (08001): Authentication token has expired."))
    assert is_session_gone(RuntimeError("Session no longer exists. New login required."))
    assert not is_session_gone(RuntimeError("SQL compilation error: invalid identifier 'X'"))


# --- an expired Snowflake sign-in ---------------------------------------------------

def test_an_expired_snowflake_sign_in_asks_to_connect_again_not_a_500():
    encryptor = TokenEncryptor(Fernet.generate_key().decode())
    store = InMemorySessionStore()
    service = SnowflakeService(settings, store, encryptor)
    service._oauth = AsyncMock()
    service._oauth.refresh_tokens.side_effect = RuntimeError("invalid_grant")
    session = SessionData(
        session_id="s1", snowflake_user="ALICE", access_token="old",
        refresh_token_encrypted=encryptor.encrypt("dead-refresh"),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1), auth_mode="oauth",
    )
    asyncio.run(store.set("s1", session, 600))

    with pytest.raises(SnowflakeNotLinked, match="expired"):
        asyncio.run(service.ensure_fresh_session(session))

    kept = asyncio.run(store.get("s1"))
    assert kept is not None, "the app sign-in survives; only Snowflake is dropped"
    assert kept.access_token == "" and not kept.has_snowflake


# --- responses ----------------------------------------------------------------------

@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_api_responses_are_never_stored(client):
    for path in ["/api/v1/auth/capabilities", "/api/v1/auth/me"]:
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_pages_outside_the_api_are_left_alone(client):
    assert "cache-control" not in client.get("/health").headers


def test_an_ended_snowflake_session_is_a_409_and_the_connection_is_dropped(client, monkeypatch):
    import snowflake.connector.errors as errors

    from app.api.deps import get_current_session
    from app.main import app

    session = SessionData(session_id="sx", snowflake_user="ALICE", access_token="t",
                          token_expires_at=datetime.now(UTC) + timedelta(hours=1))
    app.dependency_overrides[get_current_session] = lambda: session
    evicted = []
    monkeypatch.setattr(cache_module.connection_cache, "evict", lambda sid: evicted.append(sid))

    async def gone(self, s, sql, params=None):
        raise errors.ProgrammingError(msg="Authentication token has expired.", errno=390114)

    monkeypatch.setattr(SnowflakeService, "execute_query_dict", gone)
    try:
        client.cookies.set("session_id", "sx")
        response = client.get("/api/v1/databases")
    finally:
        app.dependency_overrides.pop(get_current_session, None)

    assert response.status_code == 409
    assert response.json()["code"] == "snowflake_session_expired"
    assert evicted == ["sx"]
