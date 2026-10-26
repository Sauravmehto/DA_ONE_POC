"""A semantic layer is written the same way for every connector's databases.

The layer is what turns a bare table listing into something the model can
reason about; it was only ever available for Snowflake, which is why SQL Server
answers had column names and nothing else to go on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_session
from app.engine import db_connections
from app.main import app
from app.session.base import SessionData

SOURCE = "SQLSERVER:c1/FINANCE_DB"

DOCUMENT = {
    "database": SOURCE,
    "display_name": "Finance",
    "description": "Ledger extracts",
    "tables": {
        "DBO.GL": {
            "description": "One row per posting.",
            "key_columns": {"AMOUNT": "Posted amount in AED."},
        }
    },
}

SCHEMA = {"DBO.GL": {"AMOUNT": "decimal", "POSTED_ON": "date"}}


@pytest.fixture
def client(monkeypatch, fresh_accounts):
    """An admin signed in with a password, and one SQL Server database."""
    session = SessionData(session_id="s1", snowflake_user="ADMIN", auth_mode="password")

    monkeypatch.setattr(
        "app.api.sources.session_is_admin", _yes
    )
    monkeypatch.setattr(
        "app.api.v1.routes.semantic.session_is_admin", _yes
    )
    monkeypatch.setattr(
        db_connections, "list_connections",
        lambda: [db_connections.SavedConnection("c1", "DA SQL Server", "sqlserver",
                                                "db.example", 1433, "reader")],
    )
    monkeypatch.setattr(db_connections, "databases_for", lambda _id: ["FINANCE_DB"])
    monkeypatch.setattr(
        "app.api.sources.run_on_source", _schema_instead_of_the_server
    )

    app.dependency_overrides[get_current_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_current_session, None)


async def _yes(session) -> bool:
    return True


async def _no(session) -> bool:
    return False


async def _schema_instead_of_the_server(request, session, source_key, fn, *args):
    """The server is not there to be asked; its tables are these."""
    return SCHEMA


def test_a_sql_server_database_is_offered_alongside_the_warehouses(client):
    listed = client.get("/api/v1/admin/semantic/databases").json()
    assert [(s["database"], s["connector"], s["can_edit"]) for s in listed] == [
        (SOURCE, "sqlserver", True)]
    assert listed[0]["display_name"] == "FINANCE_DB (DA SQL Server)"


def test_a_layer_can_be_drafted_and_published_for_it(client):
    draft = client.post(f"/api/v1/admin/semantic/{SOURCE}/draft", json=DOCUMENT)
    assert draft.status_code == 200, draft.text
    assert draft.json()["has_draft"] is True
    assert draft.json()["database"] == SOURCE
    # Coverage is measured against what the server actually has.
    assert draft.json()["coverage"]["total_tables"] == 1
    assert draft.json()["coverage"]["documented_tables"] == 1

    published = client.post(f"/api/v1/admin/semantic/{SOURCE}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["is_published"] is True

    detail = client.get(f"/api/v1/admin/semantic/{SOURCE}").json()
    assert detail["is_published"] is True
    assert detail["document"]["tables"]["DBO.GL"]["description"] == "One row per posting."


def test_the_layer_reaches_the_model_through_the_loader(client):
    client.post(f"/api/v1/admin/semantic/{SOURCE}/draft", json=DOCUMENT)
    client.post(f"/api/v1/admin/semantic/{SOURCE}/publish")

    from app.engine.metadata_loader import MetadataStore

    context = MetadataStore().build_context(SOURCE)
    assert "One row per posting." in context
    assert "Posted amount in AED." in context


def test_only_an_administrator_may_write_it(client, monkeypatch):
    monkeypatch.setattr("app.api.sources.session_is_admin", _no)
    refused = client.post(f"/api/v1/admin/semantic/{SOURCE}/draft", json=DOCUMENT)
    assert refused.status_code == 403
    assert "administrator" in refused.json()["detail"]

    # Reading is not writing: the list and the document stay open.
    assert client.get("/api/v1/admin/semantic/databases").status_code == 200


def test_the_list_can_be_scoped_to_one_connection(client):
    """A SQL Server workspace and a warehouse are separate places; opening the
    Builder from one should not list the other, nor query it."""
    scoped = client.get(
        "/api/v1/admin/semantic/databases?connector=sqlserver&connection=c1"
    ).json()
    assert [s["database"] for s in scoped] == [SOURCE]

    other = client.get(
        "/api/v1/admin/semantic/databases?connector=sqlserver&connection=nope"
    ).json()
    assert other == []

    warehouses = client.get(
        "/api/v1/admin/semantic/databases?connector=snowflake"
    ).json()
    assert warehouses == [], "a SQL Server database was listed as a warehouse's"
