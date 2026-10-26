import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ["SESSION_BACKEND"] = "memory"
os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["JWT_SECRET"] = "test-secret"
os.environ["OAUTH_CLIENT_ID"] = "test-client"
os.environ["OAUTH_CLIENT_SECRET"] = "test-secret"
os.environ["SNOWFLAKE_ACCOUNT"] = "test-account"
os.environ["SNOWFLAKE_WAREHOUSE"] = "TEST_WH"

from app.api.v1.routes import auth as auth_routes
from app.main import app
from app.services.oauth import OAuthTokens
from app.session.base import SessionData


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_me_unauthenticated(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_there_is_no_route_that_signs_in_through_snowflake(client: TestClient) -> None:
    assert client.get("/api/v1/auth/login", follow_redirects=False).status_code == 404


@patch("app.api.v1.routes.auth._get_oauth_service")
def test_callback_without_a_connect_request_starts_no_session(
    mock_oauth_factory,
    client: TestClient,
) -> None:
    """What used to sign a Snowflake user in: a valid state and a code, no
    signed-in account behind them. It now does nothing but say so."""
    oauth_service = AsyncMock()
    oauth_service.exchange_code.return_value = OAuthTokens(
        access_token="access-123", refresh_token="refresh-456", expires_in=3600,
        username="ALICE",
    )
    mock_oauth_factory.return_value = oauth_service

    state = "fixed-test-state"
    asyncio.run(app.state.session_store.set_oauth_state(state, 600))
    response = client.get(f"/api/v1/auth/callback?code=test-code&state={state}",
                          follow_redirects=False)

    assert response.status_code == 302
    assert "/login?error=" in response.headers["location"]
    assert "session_id" not in response.cookies
    oauth_service.exchange_code.assert_not_called()
    assert client.get("/api/v1/auth/me").status_code == 401

@pytest.mark.asyncio
async def test_logout_clears_session(client: TestClient) -> None:
    session = SessionData(
        session_id="logout-session",
        snowflake_user="BOB",
        access_token="token",
        refresh_token_encrypted="enc",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    await app.state.session_store.set("logout-session", session, 3600)

    client.cookies.set("session_id", "logout-session")
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 401
