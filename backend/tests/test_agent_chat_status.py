"""A chat on a SQL Server database says what it is doing while it reads the
server's tables, instead of sitting silent through a slow login."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_session
from app.core.config import settings
from app.engine.connectors.sqlserver.client import ServerParams
from app.main import app
from app.session.base import SessionData

SOURCE = "SQLSERVER:c1/FINANCE_DB"


class _Source:
    """A saved SQL Server database whose catalogue takes a while to read."""

    def __init__(self, fail: bool = False) -> None:
        self.key = SOURCE
        self.database = "FINANCE_DB"
        self.params = ServerParams("db.example", 1433, "reader", "secret", "FINANCE_DB")
        self.reads = 0
        self.fail = fail

    def context(self) -> str:
        self.reads += 1
        if self.fail:
            raise RuntimeError("TDS server connection timed out")
        return "Table dbo.GL: ID int"


async def _no_connection(self, session):
    """This chat needs no Snowflake: the source is a saved connection."""
    return None


@pytest.fixture
def turn(monkeypatch):
    """One chat turn against a SQL Server source, as raw SSE text."""
    session = SessionData(
        session_id="s1", snowflake_user="ALICE", role="ANALYST", auth_mode="password",
        access_token="", refresh_token="", account="acct", user_id="u1",
    )
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")

    async def no_model_call(**kwargs):
        # The model is never called: this is about what happens before it.
        return
        yield

    monkeypatch.setattr("app.api.v1.routes.agent.run_turn", no_model_call)
    monkeypatch.setattr(
        "app.api.v1.routes.agent.SnowflakeService.get_connection", _no_connection
    )
    app.dependency_overrides[get_current_session] = lambda: session

    def run(source: _Source) -> str:
        monkeypatch.setattr(
            "app.engine.db_connections.resolve_source", lambda name: source
        )
        with TestClient(app) as client:
            with client.stream(
                "POST", "/api/v1/agent/chat",
                json={"message": "sales by month", "database": SOURCE},
            ) as reply:
                assert reply.status_code == 200
                return reply.read().decode()

    yield run
    app.dependency_overrides.pop(get_current_session, None)


def test_the_chat_says_it_is_reading_the_database_first(turn):
    source = _Source()
    text = turn(source)
    assert "event: status" in text, "the chat was silent while the server was read"
    assert "Reading FINANCE_DB" in text
    assert source.reads == 1, "the catalogue was read more than once for one turn"


def test_a_server_that_will_not_answer_is_reported_in_the_chat(turn):
    text = turn(_Source(fail=True))
    assert "event: error" in text
    assert "Could not read the SQL Server database 'FINANCE_DB'" in text
