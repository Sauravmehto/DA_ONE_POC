"""Data groups through the API: lists filed and filtered, direct links refused,
and the admin surface closed to everyone else."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import tools
from app.api import access as access_checks
from app.api.access import get_access
from app.api.deps import get_current_session, require_admin
from app.conversation import Conversation
from app.core.config import settings
from app.engine import data_groups as dg
from app.engine.data_groups import Access, Assignment, Group, Member, Snapshot
from app.main import app
from app.session.base import SessionData

PAY, CREDIT = "g-pay", "g-credit"

RULES = Snapshot(
    configured=True,
    groups=(Group(PAY, "Payments"), Group(CREDIT, "PrivateCredit")),
    members=(Member(PAY, "USER", "NYSA"),),
    assignments=(
        Assignment(PAY, "database", "PAYMENTS_DB", True),
        Assignment(CREDIT, "database", "FINTECH_DB", True),
        Assignment(CREDIT, "folder", "credit-folder", True),
        Assignment(PAY, "database", "SQLSERVER:C1/FINTECH_DB", True),
        Assignment(PAY, "dashboard", "moved", True),
    ),
)

DASHBOARDS = [
    {"report_id": "pay1", "report_title": "Settlements", "database": "PAYMENTS_DB"},
    {"report_id": "credit1", "report_title": "Loan book", "database": "FINTECH_DB"},
    {"report_id": "hosp1", "report_title": "Occupancy", "database": "HOSPITALITY_DB"},
    {"report_id": "moved", "report_title": "Moved one", "database": "FINTECH_DB"},
]
FILE_DASHBOARDS = [
    {"report_id": "file1", "report_title": "From a folder", "source_type": "file",
     "source_key": "FOLDER:credit-folder"},
    {"report_id": "server1", "report_title": "Origination funnel",
     "source_type": "sqlserver", "source_key": "SQLSERVER:c1/FINTECH_DB"},
]


@pytest.fixture
def session() -> SessionData:
    return SessionData(
        session_id="groups", snowflake_user="NYSA", role="ANALYST", access_token="t",
        refresh_token_encrypted="", token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        auth_mode="oauth",
    )


@pytest.fixture
def client(monkeypatch, session):
    """The API as NYSA, a Payments member, with Snowflake calls replaced."""
    state: dict[str, Any] = {"rules": RULES, "enforced": True, "admin": False}

    async def fake_access():
        return Access(state["rules"], session.snowflake_user, session.role,
                      is_admin=state["admin"], enforced=state["enforced"])

    async def direct(request, sess, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    from app.api.v1.routes import dashboards as dash_routes
    from app.api.v1.routes import data_groups as group_routes
    from app.api.v1.routes import excel_reports as report_routes
    from app.api.v1.routes import excel_sources as source_routes
    from app.engine import dashboards as engine_dashboards
    from app.engine import excel_reports, excel_sources, file_dashboards

    monkeypatch.setattr(access_checks, "run_as_user", direct)
    monkeypatch.setattr(group_routes, "run_bound", direct)
    monkeypatch.setattr(dash_routes, "_run_snowflake_engine", direct)
    monkeypatch.setattr(report_routes, "_run_engine", direct)
    monkeypatch.setattr(source_routes, "_run_engine", direct)
    monkeypatch.setattr(engine_dashboards, "list_dashboards",
                        lambda limit=200: [dict(d) for d in DASHBOARDS])
    monkeypatch.setattr(file_dashboards, "list_dashboards",
                        lambda source_key=None, limit=200: [dict(d) for d in FILE_DASHBOARDS])
    monkeypatch.setattr(file_dashboards, "read_record", lambda rid: None)

    def chain(report_id):
        found = next((d for d in DASHBOARDS if d["report_id"] == report_id), None)
        return dg.dashboard_chain(report_id, database=found["database"]) if found else None

    monkeypatch.setattr(access_checks, "dashboard_chain", chain)
    monkeypatch.setattr(excel_reports, "list_reports", lambda limit=50, source_key=None: [
        {"report_id": "r-pay", "report_title": "Pay pack", "database": "PAYMENTS_DB"},
        {"report_id": "r-credit", "report_title": "Credit pack", "database": "FINTECH_DB"},
    ])
    monkeypatch.setattr(excel_sources, "list_collections", lambda: [
        SimpleNamespace(collection_id="credit-folder", name="Credit files"),
        SimpleNamespace(collection_id="open-folder", name="Open files"),
    ])
    monkeypatch.setattr(source_routes, "_summarise_collection",
                        lambda c: {"collection_id": c.collection_id, "name": c.name,
                                   "is_named": True, "file_count": 0, "total_rows": 0,
                                   "updated_at": "2026-09-16", "datasets": []})

    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[get_access] = fake_access
    with TestClient(app) as test_client:
        test_client.state = state
        yield test_client
    app.dependency_overrides.clear()


def _ids(items, key="report_id"):
    return sorted(item[key] for item in items)


def test_the_dashboards_list_shows_only_my_groups_and_ungrouped(client):
    body = client.get("/api/v1/dashboards").json()
    assert _ids(body["dashboards"]) == ["hosp1", "moved", "pay1", "server1"]
    groups = {d["report_id"]: d["data_group"] for d in body["dashboards"]}
    assert groups == {"pay1": "Payments", "moved": "Payments", "hosp1": "Ungrouped",
                      "server1": "Payments"}
    assert body["data_groups"] == {"configured": True, "enforced": True}


def test_unenforced_rules_file_everything_but_hide_nothing(client):
    client.state["enforced"] = False
    body = client.get("/api/v1/dashboards").json()
    groups = {d["report_id"]: d["data_group"] for d in body["dashboards"]}
    assert groups == {"pay1": "Payments", "credit1": "PrivateCredit", "hosp1": "Ungrouped",
                      "moved": "Payments", "file1": "PrivateCredit",
                      "server1": "Payments"}
    moved = next(d for d in body["dashboards"] if d["report_id"] == "moved")
    assert moved["data_group_direct"] is True


def test_admins_see_everything(client):
    client.state["admin"] = True
    assert len(client.get("/api/v1/dashboards").json()["dashboards"]) == 6


def test_without_the_tables_the_list_is_filed_by_database_as_before(client):
    client.state["rules"] = Snapshot(configured=False)
    body = client.get("/api/v1/dashboards").json()
    groups = {d["report_id"]: d["data_group"] for d in body["dashboards"]}
    assert groups["credit1"] == "FINTECH_DB"
    assert groups["file1"] == "Dashboards created from files"


def test_a_direct_link_outside_my_groups_is_not_found(client, monkeypatch):
    from app.api.v1.routes import dashboards as dash_routes

    monkeypatch.setattr(dash_routes, "_is_file_dashboard", lambda rid: False)
    ran = []
    monkeypatch.setattr(dash_routes.engine_dashboards, "run_dashboard",
                        lambda rid, values=None: ran.append(rid) or {"report_id": rid})
    assert client.get("/api/v1/dashboards/credit1").status_code == 404
    assert client.get("/api/v1/dashboards/credit1/versions").status_code == 404
    assert client.get("/api/v1/dashboards/pay1").status_code == 200
    assert ran == ["pay1"]


def test_a_sql_server_database_is_hidden_like_any_other(client, monkeypatch):
    """The connections list builds the source picker. Without this a group
    could hold a SQL Server database and still not hide it — a rule that looks
    enforced and is not."""
    from app.api.v1.routes import connections as connection_routes
    from app.engine import db_connections

    monkeypatch.setattr(
        db_connections, "list_connections",
        lambda: [db_connections.SavedConnection(
            "c1", "DA SQL Server", "sqlserver", "db.example", 1433, "reader")])
    monkeypatch.setattr(db_connections, "databases_for",
                        lambda _id: ["PAYMENTS_DB", "SECRET_DB"])
    monkeypatch.setattr(connection_routes, "_copy_from_snowflake",
                        _no_copy := _AsyncNone())

    client.state["rules"] = Snapshot(
        configured=True,
        groups=RULES.groups,
        members=RULES.members,
        assignments=(
            Assignment(PAY, "database", "SQLSERVER:C1/PAYMENTS_DB", True),
            Assignment(CREDIT, "database", "SQLSERVER:C1/SECRET_DB", True),
        ),
    )

    listed = client.get("/api/v1/connections").json()["connections"]
    assert [c["databases"] for c in listed] == [["PAYMENTS_DB"]],         "the one filed under a group NYSA is not in must not be offered"


class _AsyncNone:
    async def __call__(self, *args, **kwargs):
        return None


def test_a_sql_server_dashboard_inherits_its_databases_group(client):
    """Filing the database is the whole point: every dashboard built on it
    follows, with nobody filing dashboards one at a time. The list read a
    `database` column that a SQL Server row does not carry, so those
    dashboards came back ungrouped and the filing looked broken."""
    listed = client.get("/api/v1/dashboards").json()["dashboards"]
    server = next(d for d in listed if d["report_id"] == "server1")
    assert server["data_group"] == "Payments"
    assert server["data_group_direct"] is False, "inherited, not filed by hand"


def test_reports_and_folders_are_filtered_the_same_way(client):
    reports = client.get("/api/v1/excel/reports").json()["reports"]
    assert _ids(reports) == ["r-pay"]
    folders = client.get("/api/v1/excel-sources").json()
    assert _ids(folders, "collection_id") == ["open-folder"]
    assert client.patch("/api/v1/excel-sources/collections/credit-folder",
                        json={"name": "x"}).status_code == 404


def test_chats_on_a_hidden_source_are_hidden_and_refused(client):
    store = app.state.conversation_store
    import asyncio

    for cid, database in (("c-pay", "PAYMENTS_DB"), ("c-credit", "FINTECH_DB")):
        asyncio.run(store.save(Conversation(conversation_id=cid, owner="NYSA",
                                            database=database, title=cid), 3600))
    listed = client.get("/api/v1/agent/conversations").json()
    assert "c-credit" not in [c["conversation_id"] for c in listed]
    assert "c-pay" in [c["conversation_id"] for c in listed]
    assert client.get("/api/v1/agent/conversations/c-credit").status_code == 404
    refused = client.post("/api/v1/agent/chat", json={"message": "hi", "conversation_id": "c-credit"})
    assert refused.status_code in (404, 503)
    if refused.status_code == 503:
        assert "AI" in refused.text  # AI switched off in this environment, checked first


def test_a_new_chat_on_a_hidden_database_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    response = client.post("/api/v1/agent/chat",
                           json={"message": "hi", "database": "FINTECH_DB"})
    assert response.status_code == 404


def test_the_admin_surface_is_closed_to_non_admins(client):
    assert client.get("/api/v1/data-groups").status_code == 403
    assert client.post("/api/v1/data-groups", json={"name": "X"}).status_code == 403


def test_mine_lists_my_groups(client):
    body = client.get("/api/v1/data-groups/mine").json()
    assert body["groups"] == [{"group_id": PAY, "name": "Payments"}]
    assert body["enforced"] is True and body["configured"] is True


def test_admins_manage_groups(client, monkeypatch, session):
    app.dependency_overrides[require_admin] = lambda: session
    created = []
    monkeypatch.setattr(dg, "create_group", lambda name, description="", actor=None:
                        created.append((name, actor)) or Group("new", name, description))
    monkeypatch.setattr(dg, "snapshot", lambda required=False: RULES)
    response = client.post("/api/v1/data-groups", json={"name": "Logistics"})
    assert response.status_code == 200 and response.json()["group_id"] == "new"
    assert created == [("Logistics", "NYSA")]
    listed = client.get("/api/v1/data-groups").json()
    assert [g["name"] for g in listed["groups"]] == ["Payments", "PrivateCredit"]


def test_a_refused_change_is_a_400(client, monkeypatch, session):
    app.dependency_overrides[require_admin] = lambda: session

    def refuse(*args, **kwargs):
        raise dg.DataGroupError("A group named \"Payments\" already exists.")

    monkeypatch.setattr(dg, "create_group", refuse)
    response = client.post("/api/v1/data-groups", json={"name": "Payments"})
    assert response.status_code == 400 and "already exists" in response.text


def test_moving_a_dashboard_between_groups_is_for_admins(client):
    assert client.put("/api/v1/dashboards/pay1/group",
                      json={"group_id": CREDIT}).status_code == 403


def test_the_chat_cannot_list_or_run_a_hidden_dashboard(monkeypatch):
    from app.engine import dashboards as engine_dashboards
    from app.engine import file_dashboards

    view = Access(RULES, "NYSA", "ANALYST", is_admin=False, enforced=True)
    ctx = tools.ToolContext(database="FINTECH_DB", access=view)
    monkeypatch.setattr(engine_dashboards, "list_dashboards",
                        lambda limit=50: [dict(d) for d in DASHBOARDS])
    monkeypatch.setattr(file_dashboards, "exists", lambda rid: False)
    monkeypatch.setattr(access_checks, "dashboard_chain", lambda rid: dg.dashboard_chain(
        rid, database=next(d["database"] for d in DASHBOARDS if d["report_id"] == rid)))
    listed = tools._list_saved_dashboards(ctx)
    assert _ids(listed["dashboards"]) == ["hosp1", "moved", "pay1"]
    assert "error" in tools._run_saved_dashboard(ctx, "credit1")


def test_a_dashboard_with_no_recorded_database_is_filed_by_its_sql(client, monkeypatch):
    """The list and opening the dashboard must agree: both read its SQL."""
    from app.engine import dashboards as engine_dashboards

    rows = [dict(d) for d in DASHBOARDS] + [
        {"report_id": "unrecorded", "report_title": "Customer overview", "database": None}]
    monkeypatch.setattr(engine_dashboards, "list_dashboards", lambda limit=200: rows)
    monkeypatch.setattr(access_checks, "dashboard_chain", lambda rid: dg.dashboard_chain(
        rid, database="FINTECH_DB" if rid == "unrecorded"
        else next(d["database"] for d in DASHBOARDS if d["report_id"] == rid)))

    listed = _ids(client.get("/api/v1/dashboards").json()["dashboards"])
    assert "unrecorded" not in listed
    client.state["enforced"] = False
    filed = {d["report_id"]: d["data_group"]
             for d in client.get("/api/v1/dashboards").json()["dashboards"]}
    assert filed["unrecorded"] == "PrivateCredit"
