"""Editing a dashboard from a chat scoped to an uploaded file, a folder or a
SQL Server database.

The chat used to fall through to the Snowflake path whenever the name did not
match a file dashboard exactly — including on a deployment with no Snowflake,
where it failed with "Missing required environment variable: SNOWFLAKE_USER"
instead of saying the dashboard was not found."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import tools

EXCEL = "EXCEL:ds1"


def _ctx(database=EXCEL, connector_name="duckdb"):
    return SimpleNamespace(
        database=database,
        snowflake_user="ALICE",
        access=None,
        uses_saved_file_dashboards=True,
        connector_name=connector_name,
        connector=object(),
        semantic_context="Table SALES: AMOUNT double",
    )


@pytest.fixture
def no_warehouse(monkeypatch):
    """Any reach for Snowflake fails the way it does with none configured."""
    def refuse(*args, **kwargs):
        raise RuntimeError("Missing required environment variable: SNOWFLAKE_USER")

    monkeypatch.setattr(tools.dashboard_records, "dashboard_owner", refuse)
    monkeypatch.setattr(tools.dashboards, "_resolve_one", refuse)
    monkeypatch.setattr(tools.dashboards, "preview_edit", refuse)
    monkeypatch.setattr(tools.dashboards, "commit_edit", refuse)
    yield


def test_a_name_that_matches_nothing_says_so_and_lists_what_is_here(
    no_warehouse, monkeypatch
):
    monkeypatch.setattr(tools.file_dashboards, "resolve_report_ids", lambda q: [])
    monkeypatch.setattr(
        tools.file_dashboards, "list_dashboards",
        lambda **kw: [{"report_id": "abc123", "report_title": "Sales by month"}],
    )

    out = tools._edit_dashboard(_ctx(), "my funnel dashboard", "add a panel")

    assert out["status"] == "not_found"
    assert "Sales by month" in out["message"]
    assert "SNOWFLAKE" not in out["message"].upper()


def test_it_says_when_nothing_has_been_saved_on_this_source(no_warehouse, monkeypatch):
    monkeypatch.setattr(tools.file_dashboards, "resolve_report_ids", lambda q: [])
    monkeypatch.setattr(tools.file_dashboards, "list_dashboards", lambda **kw: [])

    out = tools._edit_dashboard(_ctx(), "anything", "add a panel")

    assert out["status"] == "not_found"
    assert "Nothing has been saved" in out["message"]


def test_a_dashboard_on_this_source_is_still_edited(no_warehouse, monkeypatch):
    monkeypatch.setattr(
        tools.file_dashboards, "resolve_report_ids",
        lambda q: [{"report_id": "abc123", "report_title": "Sales by month"}],
    )
    monkeypatch.setattr(
        tools.file_dashboards, "read_record",
        lambda rid: {"report_id": rid, "created_by": "ALICE", "source_key": EXCEL},
    )
    monkeypatch.setattr(
        tools.file_dashboards, "preview_edit",
        lambda *args, **kwargs: {"status": "preview", "report_id": "abc123"},
    )

    out = tools._edit_dashboard(_ctx(), "Sales by month", "add a panel")

    assert out["status"] == "preview"


def test_a_warehouse_chat_still_uses_the_warehouse_path(monkeypatch):
    """The guard is for file-scoped conversations only."""
    ctx = _ctx(database="LOAN", connector_name="snowflake")
    ctx.uses_saved_file_dashboards = False

    monkeypatch.setattr(tools.file_dashboards, "resolve_report_ids", lambda q: [])
    monkeypatch.setattr(
        tools.dashboards, "_resolve_one", lambda q: {"status": "ok", "report_id": "sf1"}
    )
    monkeypatch.setattr(tools.dashboard_records, "dashboard_owner", lambda rid: "ALICE")
    monkeypatch.setattr(
        tools.dashboards, "preview_edit",
        lambda *args, **kwargs: {"status": "preview", "report_id": "sf1"},
    )

    assert tools._edit_dashboard(ctx, "Servicer summary", "add a panel")["report_id"] == "sf1"
