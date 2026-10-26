"""The audit trail exists in every deployment, warehouse or not.

Until this, the only audit table was in Snowflake, so a client with
spreadsheets and a SQL Server recorded nothing: no record of who asked what,
and nothing for the cost reporting to read."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.engine import audit, audit_store


@pytest.fixture
def no_snowflake(monkeypatch, fresh_accounts):
    """A deployment with no warehouse to write to."""
    def refuse(row):
        raise RuntimeError("Missing required environment variable: SNOWFLAKE_USER")

    monkeypatch.setattr(audit.snowflake_client, "write_audit", refuse)
    yield


def test_an_event_is_recorded_without_a_warehouse(no_snowflake):
    audit.log_event(
        "run_safe_sql", "ok", snowflake_user="ALICE", question="how many loans?",
        sql="SELECT 1", row_count=1, duration_ms=42,
    )
    rows = audit_store.read(limit=5)
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "run_safe_sql"
    assert rows[0]["user_question"] == "how many loans?"
    assert rows[0]["row_count"] == 1
    assert rows[0]["duration_ms"] == 42


def test_what_a_model_call_cost_is_recorded(no_snowflake):
    audit.log_event(
        "ai:dashboard_plan", "ok", model="claude-opus-4-8", model_calls=1,
        input_tokens=12, output_tokens=3400, cache_read_tokens=48000,
        cache_write_tokens=0, conversation_id="c1",
    )
    row = audit_store.read(limit=1)[0]
    assert (row["model"], row["model_calls"]) == ("claude-opus-4-8", 1)
    assert (row["input_tokens"], row["output_tokens"]) == (12, 3400)
    assert row["cache_read_tokens"] == 48000
    assert audit_store.read(limit=5, conversation_id="c1")[0]["event_id"] == row["event_id"]


def test_a_failing_warehouse_write_does_not_lose_the_row(no_snowflake):
    """Both writes are best-effort, and they fail independently."""
    audit.log_event("plan_dashboard", "error", error="Snowflake said no")
    assert audit_store.read(limit=1)[0]["status"] == "error"


def test_a_failing_store_write_does_not_break_the_request(monkeypatch, fresh_accounts):
    def refuse(**kwargs):
        raise RuntimeError("disk is full")

    monkeypatch.setattr(audit_store, "_store", refuse)
    monkeypatch.setattr(audit.snowflake_client, "write_audit", lambda row: None)
    audit.log_event("run_safe_sql", "ok")   # must not raise


def test_the_warehouse_write_can_be_turned_off(monkeypatch, fresh_accounts):
    wrote = []
    monkeypatch.setattr(audit.snowflake_client, "write_audit", lambda row: wrote.append(row))
    monkeypatch.setattr(settings, "AUDIT_TO_SNOWFLAKE", False)
    audit.log_event("run_safe_sql", "ok")
    assert wrote == [], "wrote to Snowflake with the setting off"
    assert audit_store.read(limit=1)[0]["tool_name"] == "run_safe_sql"


def test_both_are_written_when_a_warehouse_is_there(monkeypatch, fresh_accounts):
    wrote = []
    monkeypatch.setattr(audit.snowflake_client, "write_audit", lambda row: wrote.append(row))
    audit.log_event("run_safe_sql", "ok", snowflake_user="ALICE")
    assert len(wrote) == 1, "the Snowflake trail the demo reads stopped being written"
    assert audit_store.read(limit=1)[0]["snowflake_user"] == "ALICE"
