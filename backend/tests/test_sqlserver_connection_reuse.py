"""One login per request, not one per statement.

Planning a six-panel dashboard dry-runs six queries. Each used to open its own
connection, so on a server slow to accept a login every panel was a separate
chance to lose that panel to a timeout — which is how a dashboard came back a
panel short."""

from __future__ import annotations

import pytest

from app.engine.connectors.sqlserver import client

PARAMS = client.ServerParams("db.example", 1433, "reader", "secret", "FINANCE_DB")


class FakeCursor:
    """Answers one-column rows, and nothing at all for the catalogue's queries,
    whose rows this test does not care about."""

    def __init__(self, conn):
        self.conn = conn
        self.rows: list[list] = []

    def execute(self, sql, args=None):
        self.conn.statements.append(sql)
        if self.conn.server.refuse_next:
            self.conn.server.refuse_next -= 1
            raise client.pymssql.OperationalError(
                (8645, b"A timeout occurred while waiting for memory resources")
            )
        catalogue = ("INFORMATION_SCHEMA" in sql or "sys.objects" in sql
                     or "sys.foreign_keys" in sql)
        self.rows = [] if catalogue else [[1]]

    @property
    def description(self):
        return [("N",)]

    def fetchall(self):
        return list(self.rows)

    def fetchmany(self, n):
        return list(self.rows)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, server):
        self.server = server
        self.statements: list[str] = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class FakeServer:
    """Counts logins, and can refuse the next statements the way a loaded
    SQL Server does."""

    def __init__(self, refuse_next: int = 0) -> None:
        self.logins = 0
        self.refuse_next = refuse_next
        self.connections: list[FakeConnection] = []

    def connect(self, **kwargs):
        self.logins += 1
        conn = FakeConnection(self)
        self.connections.append(conn)
        return conn


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(client.pymssql, "connect", fake.connect)
    return fake


def test_a_requests_statements_share_one_login(server):
    with client.use_connection(PARAMS):
        for _ in range(6):
            client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 1, f"{server.logins} logins for six panels"
    assert len(server.connections[0].statements) == 6


def test_the_connection_is_handed_back_when_the_request_ends(server):
    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
    assert not server.connections[0].closed, "the next request has to log in again"
    client.close_pool()
    assert server.connections[0].closed, "a connection outlived the pool"


def test_statements_outside_a_request_share_the_pool_too(server):
    """Listing databases and testing a connection are not inside a bound
    request, and each used to log in for itself."""
    client._fetch(PARAMS, "SELECT 1")
    client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 1
    assert not any(c.closed for c in server.connections)


def test_another_database_is_not_run_over_this_requests_connection(server):
    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
        client._fetch(PARAMS.with_database("master"), "SELECT @@VERSION")
    assert server.logins == 2, "the other database was queried over the bound one"


def test_a_request_that_asks_nothing_never_logs_in(server):
    with client.use_connection(PARAMS):
        pass
    assert server.logins == 0


def test_a_query_the_server_refuses_is_run_again_on_a_fresh_connection(server):
    """'A timeout occurred while waiting for memory resources … Rerun the
    query' is the server asking for exactly that. Unanswered, it cost the
    dashboard a panel."""
    server.refuse_next = 1
    with client.use_connection(PARAMS):
        columns, rows = client._fetch(PARAMS, "SELECT 1")
    assert (columns, rows) == (["N"], [[1]])
    assert server.logins == 2, "the refused statement was not retried on a new connection"


def test_a_server_that_keeps_refusing_reports_it(server):
    server.refuse_next = 5
    with client.use_connection(PARAMS):
        with pytest.raises(client.pymssql.OperationalError):
            client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 2, "retried more than once"


def test_the_catalogue_is_read_over_the_requests_connection(server):
    client._schema_cache.clear()
    with client.use_connection(PARAMS):
        client.schema_context(PARAMS, "FINANCE_DB")
    assert server.logins == 1
    client._schema_cache.clear()


def test_a_rejected_login_is_not_tried_again(server, monkeypatch):
    """Repeated failures are how a SQL Server account gets locked out, and the
    answer to a wrong password does not change on the second ask."""
    def refuse(**kwargs):
        server.logins += 1
        raise client.pymssql.OperationalError(
            (18456, b"Login failed for user 'sa'.")
        )

    monkeypatch.setattr(client.pymssql, "connect", refuse)
    client._state_cache.clear()
    with client.use_connection(PARAMS):
        for _ in range(3):
            with pytest.raises(client.pymssql.OperationalError):
                client._fetch(PARAMS, "SELECT 1")
    # One refused login per statement, and one ask about the database, once:
    # a server that refuses is not hammered on behalf of three statements.
    assert server.logins == 4, f"{server.logins} attempts for three statements"
    client._state_cache.clear()


# ── what the server actually refused ────────────────────────────────────────


def test_a_database_that_is_not_online_is_named_as_the_reason(monkeypatch):
    client._state_cache.clear()
    """SQL Server refuses a login that asks for a database it cannot open, and
    the message it sends is "Login failed" — which sends people looking at
    passwords instead of at the database."""
    class Cursor:
        def execute(self, sql, args=None):
            assert "sys.databases" in sql

        def fetchone(self):
            return ["RECOVERY_PENDING"]

        def close(self):
            pass

    class Master:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    def connect(**kwargs):
        if kwargs["database"] == "master":
            return Master()
        raise client.pymssql.OperationalError((18456, b"Login failed for user 'sa'."))

    monkeypatch.setattr(client.pymssql, "connect", connect)
    with pytest.raises(client.pymssql.OperationalError, match="Recovery Pending"):
        client.open_connection(PARAMS)


def test_a_refusal_with_no_explanation_is_passed_on_as_it_came(monkeypatch):
    client._state_cache.clear()

    def connect(**kwargs):
        raise client.pymssql.OperationalError((18456, b"Login failed for user 'sa'."))

    monkeypatch.setattr(client.pymssql, "connect", connect)
    with pytest.raises(client.pymssql.OperationalError, match="Login failed"):
        client.open_connection(PARAMS)


# ── connections kept between requests ───────────────────────────────────────


@pytest.fixture(autouse=True)
def _empty_pool():
    client.close_pool()
    yield
    client.close_pool()


def test_a_second_request_reuses_the_first_ones_connection(server):
    """Logging in is the expensive part — 3 to 19 seconds on a real server —
    and every request was paying it."""
    for _ in range(3):
        with client.use_connection(PARAMS):
            client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 1, f"{server.logins} logins for three requests"


def test_a_connection_that_died_while_idle_is_replaced(server, monkeypatch):
    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
    kept = server.connections[0]

    # Old enough to be proved alive first, and no longer answering.
    monkeypatch.setattr(client, "POOL_PROBE_SECONDS", -1)
    monkeypatch.setattr(client, "_alive", lambda conn: False)

    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 2
    assert kept.closed, "a dead connection was left open"


def test_a_connection_left_idle_too_long_is_not_kept(server, monkeypatch):
    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
    monkeypatch.setattr(client, "POOL_IDLE_SECONDS", -1)
    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 2


def test_a_connection_the_server_refused_on_is_not_handed_on(server):
    server.refuse_next = 1
    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")   # refused once, retried on a new one
    assert server.connections[0].closed, "the refused connection was kept"

    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 2, "the healthy connection was not reused"


def test_only_so_many_are_kept(server, monkeypatch):
    monkeypatch.setattr(client, "POOL_SIZE", 2)
    kept = [client.checkout(PARAMS) for _ in range(3)]
    assert server.logins == 3, "three at once need three connections"
    for conn in kept:
        client.checkin(PARAMS, conn)
    assert sum(1 for c in server.connections if c.closed) == 1, "kept more than the cap"


def test_a_statement_outside_a_request_also_uses_the_pool(server):
    """Listing databases for the connections page leaves a connection open for
    the dashboard opened straight after it."""
    client._fetch(PARAMS, "SELECT 1")
    with client.use_connection(PARAMS):
        client._fetch(PARAMS, "SELECT 1")
    assert server.logins == 1


def test_a_login_that_times_out_is_tried_LOGIN_ATTEMPTS_times(monkeypatch):
    """The constant callers budget against is the number of logins spent."""
    client._state_cache.clear()
    attempts = []

    def connect(**kwargs):
        attempts.append(kwargs["server"])
        raise client.pymssql.OperationalError(
            (20002, b"DB-Lib error message 20003: TDS server connection timed out")
        )

    monkeypatch.setattr(client.pymssql, "connect", connect)
    with pytest.raises(client.pymssql.OperationalError):
        client.open_connection(PARAMS)

    assert len(attempts) == client.LOGIN_ATTEMPTS
