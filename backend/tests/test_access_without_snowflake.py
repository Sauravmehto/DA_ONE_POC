"""Someone with no Snowflake link uses everything that is not Snowflake, and
nothing reaches for Snowflake on their behalf (spec 0007, phase A).

Deliberately the REAL path: a password account signed in through the API, the
app store's own records, data groups enforced, and no patching of the access
checks. The data-group route tests replace run_as_user, which is why the bug
this guards against went unseen.

Every way a Snowflake connection can be opened is recorded: the person's own
(SnowflakeService), the engine's shared service-account connection, and the
connector itself. The tests assert the record is EMPTY rather than relying on
an exception, because get_connection_or_none() swallows exceptions and a raise
alone would go unnoticed.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
import snowflake.connector
from fastapi.testclient import TestClient

from app.accounts import service as accounts
from app.api import access as access_checks
from app.conversation import Conversation
from app.core.config import settings
from app.engine import data_groups, file_dashboards, report_store
from app.engine import excel_sources as sources
from app.engine import storage as storage_mod
from app.engine.connectors.snowflake import client as sc
from app.engine.data_groups import Assignment, Group, Member, Snapshot
from app.engine.excel_sources import Dataset, DatasetFile, SourceTable
from app.main import app
from app.services.snowflake import SnowflakeService
from app.session.base import SessionData

PASSWORD = "correct horse battery"
NYSA = "nysa@example.com"
PAY, HR = "g-pay", "g-hr"
SQL_OK, SQL_NO = "SQLSERVER:c1/FINTECH_DB", "SQLSERVER:c1/HR_DB"


def _plan(title: str) -> dict:
    return {"title": title, "parameters": [], "panels": [
        {"title": "Total", "sql": "SELECT 1 AS c", "chart_type": "kpi", "x": None,
         "y": "c", "color": None, "orientation": "v", "display": "chart"}]}


def _dataset(dataset_id: str, folder: str, owner: str = NYSA) -> Dataset:
    return Dataset(
        dataset_id=dataset_id, name=dataset_id, fingerprint="fp", sheet_count=1,
        tables=[SourceTable(name="T", row_count=1, columns=["C"], sheets=["S"])],
        files=[DatasetFile(filename=f"{dataset_id}.xlsx", row_count=1)],
        database_size=1, created_at="2026-10-01T00:00:00+00:00", collection_id=folder,
        owner=owner,
    )


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    """Every attempt to open any Snowflake connection, by kind."""
    calls: list[str] = []

    async def own(self, session):
        calls.append("own")
        raise AssertionError("a per-user Snowflake connection was requested")

    async def restore(self, session):
        calls.append("restore")
        raise AssertionError("a linked Snowflake connection was restored")

    def shared():
        calls.append("shared")
        raise AssertionError("the shared Snowflake connection was opened")

    def connect(*args, **kwargs):
        calls.append("connector")
        raise AssertionError("snowflake.connector.connect was called")

    monkeypatch.setattr(SnowflakeService, "get_connection", own)
    monkeypatch.setattr(SnowflakeService, "restore_linked", restore)
    monkeypatch.setattr(sc, "_new_connection", shared)
    monkeypatch.setattr(snowflake.connector, "connect", connect)
    return calls


@pytest.fixture
def world(fresh_accounts, tmp_path, monkeypatch):
    """Two groups. Nysa is in Payments; nobody here is in HR."""
    storage_mod.set_storage(storage_mod.LocalStorage(tmp_path / "storage"))
    monkeypatch.setattr(settings, "DATA_GROUPS_ENFORCED", True)

    ids: dict[str, str] = {}
    for name, source in (("sql_ok", SQL_OK), ("sql_no", SQL_NO), ("xl_ok", "EXCEL:ds_ok")):
        plan_id = file_dashboards._cache_plan(_plan(name), source)
        ids[name] = file_dashboards.save_plan(plan_id, name, created_by=NYSA.upper())["report_id"]
    sources._save_dataset(_dataset("ds_ok", "f_ok"))
    sources._save_dataset(_dataset("ds_no", "f_no"))
    for report_id in ("rep_ok", "rep_no"):
        report_store.save_report({"report_id": report_id, "template_id": "t1",
                                  "report_title": report_id, "parameterized_query": {},
                                  "created_by": NYSA})

    data_groups._cache = (time.monotonic() + 3600, Snapshot(
        configured=True,
        groups=(Group(PAY, "Payments"), Group(HR, "HR")),
        members=(Member(PAY, "USER", NYSA.upper()),),
        assignments=(
            Assignment(PAY, "database", SQL_OK.upper(), True),
            Assignment(HR, "database", SQL_NO.upper(), True),
            Assignment(PAY, "folder", "f_ok", True),
            Assignment(HR, "folder", "f_no", True),
            Assignment(PAY, "report", "rep_ok", True),
            Assignment(HR, "report", "rep_no", True),
        ),
    ))
    yield ids
    storage_mod.set_storage(None)


@pytest.fixture
def nysa(world, opened):
    """Nysa, signed in with her password. She has never connected Snowflake."""
    accounts.create_account(NYSA, PASSWORD, "Nysa")
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/password",
                           json={"email": NYSA, "password": PASSWORD}).status_code == 200
        store = app.state.conversation_store
        for conversation_id, database in (("c_sql_ok", SQL_OK), ("c_sql_no", SQL_NO),
                                          ("c_xl_ok", "EXCEL:ds_ok"), ("c_xl_no", "EXCEL:ds_no"),
                                          ("c_ghost", "EXCEL:ghost")):
            asyncio.run(store.save(Conversation(conversation_id=conversation_id, owner=NYSA,
                                                database=database, title=conversation_id), 600))
        yield client


# --- what she may see, she sees -----------------------------------------------------

def test_allowed_sql_server_upload_report_and_chat_open_without_snowflake(nysa, world, opened):
    for path in (f"/api/v1/dashboards/{world['sql_ok']}/versions",
                 f"/api/v1/dashboards/{world['xl_ok']}/versions",
                 "/api/v1/excel-sources/ds_ok",
                 "/api/v1/excel/reports/rep_ok",
                 "/api/v1/agent/conversations/c_sql_ok",
                 "/api/v1/agent/conversations/c_xl_ok"):
        response = nysa.get(path)
        assert response.status_code == 200, (path, response.status_code, response.text)
    assert opened == []


def test_a_sql_server_dashboard_runs_with_no_warehouse_bound(nysa, world, opened, monkeypatch):
    """The panel queries go to SQL Server (stood in for here: there is no
    server); what matters is that Snowflake is neither bound nor opened."""
    import contextlib

    from app.api.v1.routes import dashboards as dashboard_routes

    seen = []

    def run(report_id, connector, *args):
        seen.append(sc.warehouse_permitted())
        return {"report_id": report_id, "panels": []}

    monkeypatch.setattr(file_dashboards, "run_dashboard", run)
    monkeypatch.setattr(dashboard_routes.source_binding, "bind_upload_source",
                        lambda source_key, identity: contextlib.nullcontext())
    response = nysa.get(f"/api/v1/dashboards/{world['sql_ok']}")
    assert response.status_code == 200, response.text
    assert seen == [False]
    assert opened == []


def test_the_upload_folders_list_without_snowflake(nysa, opened):
    response = nysa.get("/api/v1/excel-sources")
    assert response.status_code == 200, response.text
    assert opened == []


class _SqlServerSource:
    key = SQL_OK
    database = "FINTECH_DB"

    def __init__(self) -> None:
        from app.engine.connectors.sqlserver.client import ServerParams

        self.params = ServerParams("db.example", 1433, "reader", "secret", "FINTECH_DB")

    def context(self) -> str:
        return "Table dbo.LOANS: ID int"


@pytest.fixture
def chat_turn(nysa, monkeypatch):
    """One chat turn as Nysa, with the model call stood in for."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")

    async def no_model_call(**kwargs):
        return
        yield

    monkeypatch.setattr("app.api.v1.routes.agent.run_turn", no_model_call)
    monkeypatch.setattr("app.engine.db_connections.resolve_source",
                        lambda name: _SqlServerSource())

    def run(database: str):
        return nysa.post("/api/v1/agent/chat", json={"message": "loans by month",
                                                     "database": database})
    return run


def test_a_chat_on_sql_server_needs_no_snowflake(chat_turn, opened):
    reply = chat_turn(SQL_OK)
    assert reply.status_code == 200, reply.text
    assert opened == []


def test_a_chat_on_a_warehouse_database_asks_her_to_connect_snowflake(chat_turn, opened,
                                                                      monkeypatch):
    from app.engine.metadata_loader import metadata_store

    monkeypatch.setattr(metadata_store, "documented_databases", lambda: {"PAYMENTS_DB"})
    reply = chat_turn("PAYMENTS_DB")
    assert reply.status_code == 409
    assert reply.json()["code"] == "snowflake_not_linked"
    assert opened == []


# --- what she may not, she does not ---------------------------------------------------

def test_resources_outside_her_groups_stay_hidden(nysa, world, opened):
    for path in (f"/api/v1/dashboards/{world['sql_no']}/versions",
                 "/api/v1/excel-sources/ds_no",
                 "/api/v1/excel/reports/rep_no",
                 "/api/v1/agent/conversations/c_sql_no",
                 "/api/v1/agent/conversations/c_xl_no"):
        response = nysa.get(path)
        assert response.status_code == 404, (path, response.status_code, response.text)
    assert opened == []


def test_a_record_only_snowflake_could_hold_is_refused_not_let_through(nysa, opened):
    """Not in the app store, so only a Snowflake lookup could place it in a
    group, and she has no Snowflake to look with: 404, never "ungrouped"."""
    for path in ("/api/v1/dashboards/legacy-dashboard/versions",
                 "/api/v1/excel-sources/ghost",
                 "/api/v1/excel/reports/legacy-report",
                 "/api/v1/agent/conversations/c_ghost"):
        response = nysa.get(path)
        assert response.status_code == 404, (path, response.status_code, response.text)
    assert opened == []


def test_the_chat_list_shows_what_she_may_see_and_hides_what_cannot_be_placed(nysa, opened):
    listed = {c["conversation_id"] for c in nysa.get("/api/v1/agent/conversations").json()}
    assert listed == {"c_sql_ok", "c_xl_ok"}
    assert opened == []


def test_an_administrator_without_snowflake_gets_the_catalog(world, opened):
    accounts.create_account("admin@example.com", PASSWORD, is_admin=True)
    with TestClient(app) as client:
        client.post("/api/v1/auth/password", json={"email": "admin@example.com",
                                                   "password": PASSWORD})
        response = client.get("/api/v1/data-groups/catalog")
    assert response.status_code == 200, response.text
    assert {"f_ok", "f_no"} <= {item["id"] for item in response.json()["folder"]}
    assert opened == []


# --- someone who HAS connected Snowflake --------------------------------------------------

def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_store=None, token_encryptor=None)))


def _linked_session() -> SessionData:
    return SessionData(session_id="linked", snowflake_user="ANA", role="ANALYST",
                       access_token="live", auth_mode="password", user_id="u1")


def test_a_linked_person_finds_a_local_record_without_opening_snowflake(world, monkeypatch):
    asked = []

    async def own(self, session):
        asked.append(session.session_id)
        return "own-connection"

    monkeypatch.setattr(SnowflakeService, "get_connection", own)
    chain = asyncio.run(access_checks._resolve(
        _request(), _linked_session(), access_checks.dashboard_chain_local,
        access_checks.dashboard_chain, world["sql_ok"]))
    assert [(kind, value.upper()) for kind, value in chain][-1] == ("database", SQL_OK.upper())
    assert asked == []


def test_a_linked_person_finds_an_older_record_on_their_own_connection(world, monkeypatch):
    seen = []

    async def own(self, session):
        return "own-connection"

    def legacy_chain(report_id):
        seen.append(sc._request_connection.get())
        return [("dashboard", report_id), ("database", "PAYMENTS_DB")]

    monkeypatch.setattr(SnowflakeService, "get_connection", own)
    chain = asyncio.run(access_checks._resolve(
        _request(), _linked_session(), access_checks.dashboard_chain_local, legacy_chain,
        "legacy-dashboard"))
    assert chain == [("dashboard", "legacy-dashboard"), ("database", "PAYMENTS_DB")]
    assert seen == ["own-connection"]


def test_someone_never_linked_cannot_resolve_an_older_record(world, opened):
    unlinked = SessionData(session_id="u", snowflake_user=NYSA, auth_mode="password")
    with pytest.raises(access_checks._Unresolvable):
        asyncio.run(access_checks._resolve(
            _request(), unlinked, access_checks.dashboard_chain_local,
            access_checks.dashboard_chain, "legacy-dashboard"))
    assert opened == []
