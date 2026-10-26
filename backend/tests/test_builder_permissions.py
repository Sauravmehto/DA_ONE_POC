"""Who may use the Snowflake tools (semantic layer, report builder) and who may
change a dashboard through the chat."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent import tools
from app.api.deps import get_current_session
from app.core.config import settings
from app.main import app
from app.session.base import SessionData


def snowflake_session(role: str | None) -> SessionData:
    return SessionData(
        session_id="s-builder", snowflake_user="ANALYST", role=role, access_token="tok",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1), auth_mode="oauth",
    )


@pytest.fixture
def as_session():
    with TestClient(app) as client:
        def use(session: SessionData) -> TestClient:
            app.dependency_overrides[get_current_session] = lambda: session
            return client

        yield use
    app.dependency_overrides.pop(get_current_session, None)


def test_writing_a_snowflake_layer_needs_snowflake_connected(as_session, fresh_accounts):
    client = as_session(SessionData(session_id="s-pw", snowflake_user="ana@example.com",
                                    auth_mode="password"))
    response = client.post("/api/v1/admin/semantic/LOAN/draft", json={})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "snowflake_not_linked"


def test_the_layer_list_does_not_need_snowflake(as_session, fresh_accounts):
    """Every connector's databases can have a layer, so the list is offered to
    anyone signed in — it simply holds no warehouse databases for someone who
    has not connected Snowflake."""
    client = as_session(SessionData(session_id="s-pw", snowflake_user="ana@example.com",
                                    auth_mode="password"))
    response = client.get("/api/v1/admin/semantic/databases")
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_saving_the_semantic_layer_needs_an_admin_snowflake_role(as_session, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_ROLES", ["DATAALPHA_ADMIN"])
    client = as_session(snowflake_session("PRIVATE_CREDIT_ANALYST"))
    for method, path in [("post", "/api/v1/admin/semantic/LOAN/draft"),
                         ("post", "/api/v1/admin/semantic/LOAN/publish"),
                         ("delete", "/api/v1/admin/semantic/LOAN/draft")]:
        response = (client.post(path, json={}) if method == "post" else client.delete(path))
        assert response.status_code == 403, (path, response.text)
        assert "ADMIN_ROLES" in response.json()["detail"]
        assert "PRIVATE_CREDIT_ANALYST" in response.json()["detail"]


def test_only_an_admin_snowflake_role_may_author(monkeypatch):
    import asyncio

    from app.api.deps import can_author_snowflake

    monkeypatch.setattr(settings, "ADMIN_ROLES", ["DATAALPHA_ADMIN"])
    assert asyncio.run(can_author_snowflake(snowflake_session("dataalpha_admin")))
    assert not asyncio.run(can_author_snowflake(snowflake_session("ANALYST")))
    assert not asyncio.run(can_author_snowflake(snowflake_session(None)))
    no_snowflake = SessionData(session_id="p", snowflake_user="a", role="DATAALPHA_ADMIN",
                               auth_mode="password")
    assert not asyncio.run(can_author_snowflake(no_snowflake))


def test_the_report_builder_steps_need_an_admin_snowflake_role(as_session, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_ROLES", ["DATAALPHA_ADMIN"])
    client = as_session(snowflake_session("PRIVATE_CREDIT_ANALYST"))
    for path in ["/api/v1/excel/templates/scan", "/api/v1/excel/reports/plan",
                 "/api/v1/excel/multiblock/run", "/api/v1/excel/scans/s1/publish",
                 "/api/v1/excel/scans/s1/blocks"]:
        response = client.post(path, json={})
        assert response.status_code == 403, (path, response.status_code, response.text[:200])


def test_the_author_check_reports_the_role(as_session, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_ROLES", ["DATAALPHA_ADMIN"])
    client = as_session(snowflake_session("DATAALPHA_ADMIN"))
    assert client.get("/api/v1/auth/snowflake/author").json() == {
        "can_author": True, "role": "DATAALPHA_ADMIN"}


# --- editing dashboards through the chat -----------------------------------------------

def ctx(user: str, is_admin: bool):
    return SimpleNamespace(snowflake_user=user,
                           access=SimpleNamespace(is_admin=is_admin, filtering=False))


def test_the_chat_lets_the_maker_or_an_admin_change_a_dashboard():
    assert tools._may_edit(ctx("ana@example.com", False), "ANA@example.com")
    assert tools._may_edit(ctx("bob@example.com", True), "ana@example.com")
    assert not tools._may_edit(ctx("bob@example.com", False), "ana@example.com")
    assert not tools._may_edit(ctx("bob@example.com", False), None)
