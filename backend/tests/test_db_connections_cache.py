"""Saved SQL Server connections: kept in the app's SQLite file, copied once from
the Snowflake table they used to live in, and one server login per burst of
requests."""

import threading
import time
from datetime import datetime

import pytest
from cryptography.fernet import Fernet

from app.engine import db_connections
from app.engine.connectors.sqlserver import client as sqlserver_client

KEY = Fernet.generate_key()


@pytest.fixture
def saved(fresh_accounts, monkeypatch):
    monkeypatch.setattr(db_connections, "_fernet", lambda: Fernet(KEY))
    monkeypatch.setattr(db_connections, "_import_tried", False)
    monkeypatch.setenv("DB_CONNECTION_ALLOWED_HOSTS", "db.example")
    # The list of databases is kept in the app store now, which fresh_accounts
    # gives each test new — so there is no process-wide cache to clear.
    connection = db_connections.create_connection(
        "Finance", sqlserver_client.ServerParams("db.example", 1433, "reader", "secret"),
        "ALICE",
    )
    yield connection


def test_a_saved_connection_is_listed_and_opens_with_its_password(saved):
    assert [c.name for c in db_connections.list_connections()] == ["Finance"]
    params = db_connections.server_params(saved.connection_id, "FINANCE_DB")
    assert (params.host, params.username, params.password, params.database) == (
        "db.example", "reader", "secret", "FINANCE_DB")


def test_the_password_is_stored_encrypted(saved):
    from app.accounts import store

    with store.read() as conn:
        stored = conn.execute("SELECT password_enc FROM data_connections").fetchone()[0]
    assert "secret" not in stored
    assert Fernet(KEY).decrypt(stored.encode()) == b"secret"


def test_a_deleted_connection_is_gone_at_once(saved):
    assert db_connections.delete_connection(saved.connection_id)
    assert db_connections.list_connections() == []
    with pytest.raises(db_connections.ConnectionSetupError, match="no longer exists"):
        db_connections.server_params(saved.connection_id)
    assert not db_connections.delete_connection(saved.connection_id)


def test_requests_that_arrive_together_share_one_server_login(saved, monkeypatch):
    logins = []

    def list_databases(params):
        logins.append(params.host)
        time.sleep(0.05)
        return ["FINANCE_DB"]

    monkeypatch.setattr(db_connections.sqlserver_client, "list_databases", list_databases)
    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(db_connections.databases_for(saved.connection_id))
        )
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [["FINANCE_DB"]] * 5
    assert len(logins) == 1


def test_a_request_behind_a_stuck_login_gives_up_instead_of_queueing(saved, monkeypatch):
    """A server slow to accept a login held every later request for as long as
    that login took. Now the next one waits only until the deadline."""
    release = threading.Event()

    def stuck(params):
        release.wait(5)
        return ["FINANCE_DB"]

    monkeypatch.setattr(db_connections.sqlserver_client, "list_databases", stuck)
    monkeypatch.setattr(db_connections, "SERVER_DEADLINE_SECONDS", 0.2)
    first = threading.Thread(target=lambda: db_connections.databases_for(saved.connection_id))
    first.start()
    time.sleep(0.05)

    started = time.monotonic()
    with pytest.raises(db_connections.ConnectionSetupError, match="still answering"):
        db_connections.databases_for(saved.connection_id)
    assert time.monotonic() - started < 2

    release.set()
    first.join()
    assert db_connections.databases_for(saved.connection_id) == ["FINANCE_DB"]


def test_the_server_is_told_which_protocol_version_to_speak(monkeypatch):
    """Left to FreeTDS, the version is guessed, and a guess the server ignores
    costs the whole login timeout on every login."""
    from app.engine.connectors.sqlserver import client

    seen = {}
    monkeypatch.setattr(client.pymssql, "connect", lambda **kwargs: seen.update(kwargs))
    client.connect(client.ServerParams("db.example", 1433, "reader", "secret"))
    assert seen["tds_version"] == client.TDS_VERSION == "7.4"
    assert seen["login_timeout"] == client.LOGIN_TIMEOUT_SECONDS


def test_a_host_taken_off_the_allowlist_stops_a_saved_connection(saved, monkeypatch):
    db_connections.server_params(saved.connection_id)
    monkeypatch.setenv("DB_CONNECTION_ALLOWED_HOSTS", "elsewhere.example")
    with pytest.raises(db_connections.ConnectionSetupError):
        db_connections.server_params(saved.connection_id)


# ── copying from the Snowflake table ─────────────────────────────────────────


class _SnowflakeTable:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.reads = 0

    def get_connection(self):
        table = self

        class Cursor:
            def execute(self, sql, params=None):
                table.reads += 1
                if table.error:
                    raise table.error

            def fetchall(self):
                return list(table.rows)

            def close(self):
                pass

        class Conn:
            def cursor(self):
                return Cursor()

        return Conn()


def _snowflake_row(connection_id, password=b"old-secret"):
    return (connection_id, "Ops", "sqlserver", "db.example", 1433, "ops",
            Fernet(KEY).encrypt(password).decode(), "BOB", datetime(2026, 9, 1, 8, 30))


def test_connections_saved_in_snowflake_are_copied_once(saved, monkeypatch):
    table = _SnowflakeTable([_snowflake_row("sf1"), _snowflake_row(saved.connection_id)])
    monkeypatch.setattr(db_connections, "_snowflake", lambda: table)

    assert db_connections.import_needed()
    assert db_connections.import_from_snowflake("ADMIN") == 1

    names = {c.connection_id: c for c in db_connections.list_connections()}
    assert set(names) == {saved.connection_id, "sf1"}
    assert names[saved.connection_id].name == "Finance"   # the one already here is kept
    assert names["sf1"].created_at.startswith("2026-09-01T08:30:00")
    assert db_connections.server_params("sf1").password == "old-secret"

    monkeypatch.setattr(db_connections, "_import_tried", False)
    assert not db_connections.import_needed()
    assert db_connections.import_from_snowflake("ADMIN") == 0
    assert table.reads == 1


def test_a_failed_copy_is_not_retried_until_a_restart(saved, monkeypatch):
    table = _SnowflakeTable(error=RuntimeError("Object does not exist or not authorized"))
    monkeypatch.setattr(db_connections, "_snowflake", lambda: table)

    assert db_connections.import_from_snowflake("ADMIN") == 0
    assert not db_connections.import_needed()
    assert [c.name for c in db_connections.list_connections()] == ["Finance"]

    # After a restart it is tried again, since nothing was marked as copied.
    monkeypatch.setattr(db_connections, "_import_tried", False)
    assert db_connections.import_needed()


def test_the_database_list_outlives_this_worker(saved, monkeypatch):
    """It used to live in one worker's memory for two minutes, so four workers
    paid for the same answer four times over and paid again after a restart —
    and listing means a login, which this kind of server is slow to accept."""
    logins: list[int] = []

    def listing(params):
        logins.append(1)
        return ["FINANCE_DB", "OPS_DB"]

    monkeypatch.setattr(sqlserver_client, "list_databases", listing)

    assert db_connections.databases_for(saved.connection_id) == ["FINANCE_DB", "OPS_DB"]
    assert len(logins) == 1

    # A different worker: nothing in this process's memory, everything in the
    # store they share.
    assert db_connections.databases_for(saved.connection_id) == ["FINANCE_DB", "OPS_DB"]
    assert len(logins) == 1, "the server was asked twice for the same list"

    from app.engine import catalogue_cache

    assert catalogue_cache.read(
        db_connections.databases_key(saved.connection_id),
        db_connections.DATABASE_LIST_SECONDS,
    ) is not None


def test_a_list_old_enough_to_be_stale_is_read_again(saved, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(sqlserver_client, "list_databases",
                        lambda params: calls.append(1) or ["FINANCE_DB"])
    monkeypatch.setattr(db_connections, "DATABASE_LIST_SECONDS", 0)

    db_connections.databases_for(saved.connection_id)
    db_connections.databases_for(saved.connection_id)

    assert len(calls) == 2, "a list past its life must not be served"


def test_deleting_a_connection_forgets_its_databases(saved, monkeypatch):
    from app.engine import catalogue_cache

    monkeypatch.setattr(sqlserver_client, "list_databases",
                        lambda params: ["FINANCE_DB"])
    db_connections.databases_for(saved.connection_id)
    key = db_connections.databases_key(saved.connection_id)
    assert catalogue_cache.read(key, 3600) is not None

    db_connections.delete_connection(saved.connection_id)

    assert catalogue_cache.read(key, 3600) is None
    assert catalogue_cache.read(
        db_connections.source_key(saved.connection_id, "FINANCE_DB"), 3600) is None


def test_the_listing_waits_for_every_login_a_retry_may_spend():
    """The deadline has to outlast the retry, or it cancels it.

    open_connection tries a second login when the first times out, because a
    server reached over a tunnel answers the next one in a second. The page
    put its own deadline on the whole listing, and that deadline was one login
    plus five seconds — so the retry was still running when the page gave up,
    and a server that was about to answer was shown as not answering."""
    assert db_connections.SERVER_DEADLINE_SECONDS >= (
        sqlserver_client.LOGIN_TIMEOUT_SECONDS * sqlserver_client.LOGIN_ATTEMPTS
    )


def test_a_list_read_while_saving_is_not_read_again(saved, monkeypatch):
    """Saving a connection tests it by listing its databases. Keep that.

    The page reloads as the dialog closes, and it used to find nothing stored
    and log in again — at the one moment the list was certain not to be there.
    So a connection that had just been saved was missing until a second login
    finished, and reloading by hand looked like the thing that fixed it."""
    calls: list[str] = []

    def list_databases(params):
        calls.append(params.host)
        return ["FINANCE_DB"]

    monkeypatch.setattr(sqlserver_client, "list_databases", list_databases)

    db_connections.remember_databases(saved.connection_id, ["FINANCE_DB", "RISK_DB"])

    assert db_connections.databases_for(saved.connection_id) == ["FINANCE_DB", "RISK_DB"]
    assert calls == [], "the databases were read while saving; reading them again is the bug"


def test_a_server_that_never_answers_does_not_hang_the_save(fresh_accounts, monkeypatch):
    """Saving waits on the server, so something has to stop waiting.

    A connection is tested before it is saved, and nothing bounded that wait.
    The driver's own login timeout does not cover every stage of a login, so a
    server that never finished accepting one left the request open: the dialog
    went on loading with nothing to read and no way to tell whether it had
    saved anything. It answers now, and says that it saved nothing."""
    from fastapi.testclient import TestClient

    from app.api.deps import require_admin
    from app.api.v1.routes import connections as routes
    from app.main import app
    from app.session.base import SessionData

    monkeypatch.setenv("DB_CONNECTION_ALLOWED_HOSTS", "db.example")
    monkeypatch.setattr(db_connections, "SERVER_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(routes, "_probe", lambda params: time.sleep(2))

    app.dependency_overrides[require_admin] = lambda: SessionData(
        session_id="s", user_id="u", snowflake_user="SAURABH", auth_mode="password")
    try:
        with TestClient(app) as http:
            response = http.post("/api/v1/connections", json={
                "name": "Never answers", "host": "db.example", "port": 1433,
                "username": "sa", "password": "secret",
            })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 504, response.text
    assert "did not answer" in response.json()["detail"]
    assert db_connections.list_connections() == [], "a test that timed out saved nothing"
