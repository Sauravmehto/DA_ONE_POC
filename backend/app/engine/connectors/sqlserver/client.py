"""SQL Server connection lifecycle and safe query execution.

Same contract as connectors/duckdb/client.py — a `use_connection` context
manager, `run_query`, `dry_run_query`, `session_identity` — so the agent tools
and the dashboard engine cannot tell which vendor answered them.

WHO THE QUERY RUNS AS
---------------------
One saved login per connection, shared by everyone who uses it. SQL Server
cannot tell one DataAlphaOne user from another, so what a connection may read
is whatever that login may read. Give it a read-only login (db_datareader).

READ-ONLY, THREE TIMES OVER
---------------------------
Every statement passes sql_safety.validate_sql with dialect="tsql", which
refuses anything but one SELECT and blocks SELECT ... INTO. The connection is
opened without autocommit and rolled back before it closes, so a statement that
somehow wrote would not persist. And the login itself should hold no write
permission.

CONNECTIONS ARE NOT POOLED
--------------------------
Opened per call. A login costs a few hundred milliseconds over the network,
which is small beside a model call, and a pooled connection held by one of four
worker processes would pin a session the server may time out.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dt_time
from decimal import Decimal
from typing import Any
from uuid import UUID

import pymssql

log = logging.getLogger(__name__)

DIALECT = "tsql"

#: Rows one query may return, matching the DuckDB connector's cap.
MAX_ROWS = int(os.environ.get("MCP_MAX_ROWS", "5000"))
LOGIN_TIMEOUT_SECONDS = 15
#: Logins one call may spend before giving up: the first, and one retry when
#: that first times out (see open_connection). Anything that puts a deadline on
#: opening a connection has to budget for all of them, or it cuts off the retry
#: it meant to allow.
LOGIN_ATTEMPTS = 2
QUERY_TIMEOUT_SECONDS = 30
#: The TDS protocol version to speak. Left unset, FreeTDS tries versions in
#: turn, and a version the server does not answer costs the whole login
#: timeout before the next is tried: measured, every login took 16 seconds
#: instead of one. 7.4 is SQL Server 2012 and later; set 7.3 for 2008.
TDS_VERSION = os.environ.get("SQLSERVER_TDS_VERSION", "7.4").strip() or None

#: How long a database's schema description is reused before it is read again.
SCHEMA_CACHE_SECONDS = 600
#: Bounds on the schema description, so a database of thousands of tables does
#: not become a context block larger than the model can use.
MAX_CONTEXT_TABLES = 200
MAX_CONTEXT_COLUMNS = 60


@dataclass(frozen=True)
class ServerParams:
    """Everything needed to open a connection. The password never prints."""

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    database: str | None = None

    def with_database(self, database: str | None) -> "ServerParams":
        return ServerParams(self.host, self.port, self.username, self.password, database)


class NoServerBound(RuntimeError):
    """Raised when a query is attempted with no SQL Server bound to this request."""


# ContextVars rather than thread-locals, for the reason given in the DuckDB
# client: tools run through asyncio.to_thread, which copies the context.
_bound_params: ContextVar[ServerParams | None] = ContextVar("sqlserver_params", default=None)
_bound_identity: ContextVar[tuple | None] = ContextVar("sqlserver_identity", default=None)


# ── connections kept between requests ────────────────────────────────────────
#
# Logging in is the expensive part: measured on a real server, between 3 and 19
# seconds, and every request was paying it. A connection handed back at the end
# of a request is kept for the next one instead of closed.

#: How many idle connections to keep per database.
POOL_SIZE = 4
#: How long an idle connection is kept before it is closed.
POOL_IDLE_SECONDS = 300
#: Idle longer than this and it is proved alive before being handed out. A
#: server, a firewall or a container restart can drop a connection silently.
POOL_PROBE_SECONDS = 60

_pool: dict[tuple, list[tuple[float, Any]]] = {}
_pool_lock = threading.Lock()


def _pool_key(params: ServerParams) -> tuple:
    return (params.host, params.port, params.username, params.database)


def _alive(conn) -> bool:
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
            cursor.fetchall()
        finally:
            cursor.close()
        return True
    except Exception:  # noqa: BLE001 - any failure means "open another"
        return False


def checkout(params: ServerParams):
    """A connection for this database: a kept one, or a new login."""
    now = time.monotonic()
    while True:
        with _pool_lock:
            idle = _pool.get(_pool_key(params)) or []
            entry = idle.pop() if idle else None
        if entry is None:
            return open_connection(params)
        since, conn = entry
        if now - since > POOL_IDLE_SECONDS:
            _close_quietly(conn)
            continue
        if now - since > POOL_PROBE_SECONDS and not _alive(conn):
            _close_quietly(conn)
            continue
        return conn


def checkin(params: ServerParams, conn) -> None:
    """Keep a connection for the next request, or close it if enough are kept."""
    key = _pool_key(params)
    with _pool_lock:
        idle = _pool.setdefault(key, [])
        if len(idle) >= POOL_SIZE:
            keep = False
        else:
            idle.append((time.monotonic(), conn))
            keep = True
    if not keep:
        _close_quietly(conn)


def _close_quietly(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def close_pool() -> None:
    """Close every kept connection. For tests, and for a clean shutdown."""
    with _pool_lock:
        entries = [entry for idle in _pool.values() for entry in idle]
        _pool.clear()
    for _since, conn in entries:
        _close_quietly(conn)


class _Open:
    """The connection this request is using, opened when first needed.

    Every statement used to open its own. Planning a six-panel dashboard meant
    six logins, and on a server that takes seconds over one — sometimes
    refusing it altogether — each panel was a separate chance to lose a panel
    to a timeout. One login now serves the whole request.

    The lock is for the rare caller that runs two statements at once against
    the same bound database: one connection cannot serve both, and waiting is
    better than a second login.
    """

    def __init__(self, params: ServerParams) -> None:
        self.params = params
        self.lock = threading.Lock()
        self.conn = None

    def connection(self):
        if self.conn is None:
            self.conn = checkout(self.params)
        return self.conn

    def drop(self) -> None:
        """Forget a connection that failed; the next statement opens another.

        Closed rather than kept: a connection the server has just refused
        something on is not one to hand to the next request."""
        conn, self.conn = self.conn, None
        if conn is not None:
            _close_quietly(conn)

    def release(self) -> None:
        """Hand the connection back for the next request to use."""
        conn, self.conn = self.conn, None
        if conn is not None:
            checkin(self.params, conn)


_open: ContextVar[_Open | None] = ContextVar("sqlserver_open", default=None)


@contextmanager
def use_connection(params: ServerParams, identity: tuple | None = None):
    """Bind one SQL Server database for the duration of this request.

    `identity` is the signed-in DataAlphaOne user, carried for the audit trail.
    """
    params_token = _bound_params.set(params)
    identity_token = _bound_identity.set(identity)
    open_token = _open.set(_Open(params))
    try:
        yield
    finally:
        held = _open.get()
        if held is not None:
            held.release()
        _open.reset(open_token)
        _bound_params.reset(params_token)
        _bound_identity.reset(identity_token)


def bound_params() -> ServerParams:
    params = _bound_params.get()
    if params is None:
        raise NoServerBound(
            "No SQL Server database is bound to this request. "
            "Wrap the call in sqlserver client.use_connection(params)."
        )
    return params


def connect(params: ServerParams):
    """Open a connection. The caller closes it."""
    return pymssql.connect(
        server=params.host,
        port=str(params.port),
        user=params.username,
        password=params.password,
        database=params.database or "master",
        login_timeout=LOGIN_TIMEOUT_SECONDS,
        timeout=QUERY_TIMEOUT_SECONDS,
        appname="DataAlphaOne",
        charset="UTF-8",
        tds_version=TDS_VERSION,
    )


def _jsonify(value: Any) -> Any:
    """Make one cell JSON-safe, matching the other connectors' output."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        # DECIMAL(n,0) holds whole numbers; as floats a year reads "2026.0".
        if value.as_tuple().exponent >= 0:
            return int(value)
        return float(value)
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def _timed_out(exc: Exception) -> bool:
    """Whether a failure is the server not accepting the login in time."""
    text = str(exc)
    return "20003" in text or "timed out" in text.lower()


def _login_refused(exc: Exception) -> bool:
    """Whether the server rejected the login itself.

    Never retried: the answer will not change, and repeated failures are how a
    SQL Server account gets locked out."""
    text = str(exc)
    return "18456" in text or "Login failed" in text


def open_connection(params: ServerParams):
    """Open a connection, trying once more if the login times out.

    A server reached over a tunnel can leave a login unanswered for longer
    than the timeout while the very next attempt is served in a second or two.
    Measured against one: fifteen logins ran from 0.36s to 18.99s, a median of
    1s, and one in fifteen over the driver's own timeout — which does not cover
    every stage of a login, so a login can outlast it and still succeed. One
    retry turns that into a slow answer rather than a failed page.

    LOGIN_ATTEMPTS counts the logins this may cost, for callers timing it.
    """
    try:
        return connect(params)
    except pymssql.Error as exc:
        if _login_refused(exc):
            # A login naming a database the server cannot open is refused as
            # though the credentials were wrong. Say which it was: a database
            # being restored, recovering or offline is the server's business
            # to fix, and "Login failed" sends people to the wrong problem.
            raise _named_reason(params, exc) from exc
        if not _timed_out(exc):
            raise
        log.info("Login to %s timed out; trying once more.", params.host)
        return connect(params)


def _named_reason(params: ServerParams, exc: pymssql.Error) -> pymssql.Error:
    """The refusal to raise: the database's state when that is what refused
    the login, otherwise the server's own message."""
    state = _database_state(params)
    if state and state != "ONLINE":
        readable = state.replace("_", " ").title()
        return pymssql.OperationalError(
            f"The database '{params.database}' is not available: SQL Server "
            f"reports it as {readable}. Whoever runs the server needs to bring "
            f"it back online."
        )
    return exc


#: A database's state, remembered briefly per (server, database). Asking costs
#: a login of its own, and a refused statement is usually one of several in a
#: row — each of which would otherwise ask again.
STATE_CACHE_SECONDS = 60
_state_cache: dict[tuple, tuple[float, str | None]] = {}


def _database_state(params: ServerParams) -> str | None:
    """What the server says about the database this login asked for.

    Read over master, which a login can open when the database itself cannot
    be. Best-effort: a server that will not answer this either tells us
    nothing, and the caller still reports the original refusal."""
    if not params.database or params.database.lower() == "master":
        return None
    key = (params.host, params.port, params.username, params.database)
    now = time.monotonic()
    with _schema_lock:
        hit = _state_cache.get(key)
    if hit and now - hit[0] < STATE_CACHE_SECONDS:
        return hit[1]
    state = _read_database_state(params)
    with _schema_lock:
        _state_cache[key] = (time.monotonic(), state)
    return state


def _read_database_state(params: ServerParams) -> str | None:
    try:
        conn = connect(params.with_database("master"))
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT state_desc FROM sys.databases WHERE name = %s",
                    (params.database,),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            conn.close()
    except pymssql.Error:
        return None
    return str(row[0]) if row else None


def _rows_on(conn, sql: str, args: tuple | None = None,
             max_rows: int | None = None) -> tuple[list[str], list[list[Any]]]:
    """Run one statement on an open connection."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, args)
        columns = [d[0] for d in cursor.description or []]
        raw = cursor.fetchmany(max_rows) if max_rows is not None else cursor.fetchall()
        return columns, [[_jsonify(cell) for cell in row] for row in raw]
    finally:
        cursor.close()


@contextmanager
def _connection_for(params: ServerParams):
    """This request's connection when it is for the same database, else one
    opened for this block and closed after it."""
    held = _open.get()
    if held is not None and held.params == params:
        with held.lock:
            yield held.connection()
        return
    conn = open_connection(params)
    try:
        yield conn
    finally:
        conn.close()


def _fetch(params: ServerParams, sql: str, args: tuple | None = None,
           max_rows: int | None = None) -> tuple[list[str], list[list[Any]]]:
    """Run one statement and return (columns, rows).

    Over this request's connection when it is for the same database —
    otherwise over one opened for this statement alone."""
    held = _open.get()
    if held is not None and held.params == params:
        return _fetch_held(held, sql, args, max_rows)

    # Outside a bound request — listing databases, testing a connection — the
    # connection still comes from and goes back to the pool, so the first page
    # load of the day leaves one open for the dashboard that follows.
    conn = checkout(params)
    try:
        columns, rows = _rows_on(conn, sql, args, max_rows)
        conn.rollback()
    except BaseException:
        _close_quietly(conn)
        raise
    checkin(params, conn)
    return columns, rows


def _fetch_held(held: _Open, sql: str, args: tuple | None,
                max_rows: int | None) -> tuple[list[str], list[list[Any]]]:
    """Run a statement on the request's connection, once more on a fresh one if
    the server drops it or asks for the query to be re-run.

    Both happen on a loaded server — a dropped connection, and 'a timeout
    occurred while waiting for memory resources … Rerun the query'. Left
    alone, either one silently cost a dashboard a panel."""
    for attempt in (1, 2):
        try:
            with held.lock:
                columns, rows = _rows_on(held.connection(), sql, args, max_rows)
                held.connection().rollback()
            return columns, rows
        except pymssql.OperationalError as exc:
            held.drop()
            if attempt == 2 or _login_refused(exc):
                raise
            log.info("Retrying a statement after the server refused it: %s", exc)
    raise AssertionError("unreachable")


def run_query(sql: str, max_rows: int | None = None) -> dict[str, Any]:
    """Validate, cap, and execute a read-only query against the bound database."""
    from app.engine.sql_safety import enforce_row_limit, validate_sql

    cap = min(int(max_rows or MAX_ROWS), MAX_ROWS)
    safe_sql = enforce_row_limit(validate_sql(sql, dialect=DIALECT), cap, dialect=DIALECT)
    columns, rows = _fetch(bound_params(), safe_sql, max_rows=cap)
    return {"columns": columns, "rows": rows, "row_count": len(rows), "sql": safe_sql}


def dry_run_query(sql: str) -> list[str]:
    """Prove a query resolves without running it, returning its column names.

    sp_describe_first_result_set compiles the statement and reports its result
    shape without executing it, so a missing column or table fails here the way
    it would at run time. A few valid statements cannot be described that way
    (temporary tables, some dynamic constructs); those fall back to running the
    query capped at zero rows.
    """
    from app.engine.sql_safety import enforce_row_limit, validate_sql

    safe_sql = validate_sql(sql, dialect=DIALECT)
    params = bound_params()
    try:
        columns, rows = _fetch(
            params, "EXEC sp_describe_first_result_set @tsql = %s", (safe_sql,)
        )
    except pymssql.Error as exc:
        if "could not be determined" not in str(exc):
            raise
        zero = enforce_row_limit(safe_sql, 0, dialect=DIALECT)
        columns, _ = _fetch(params, zero, max_rows=0)
        return columns
    name_at = columns.index("name") if "name" in columns else 2
    return [str(row[name_at]) for row in rows if row[name_at] is not None]


def session_identity() -> tuple[str | None, str | None]:
    """The signed-in DataAlphaOne user, for the audit trail.

    Not the SQL Server login: every user of a connection shares that login, so
    recording it would make every audit row name the same account.
    """
    identity = _bound_identity.get()
    if isinstance(identity, tuple) and len(identity) == 2:
        return identity
    return (None, None)


# ── catalogue ────────────────────────────────────────────────────────────────


def server_version(params: ServerParams) -> str:
    """The server's product version line, e.g. "Microsoft SQL Server 2022 ..."."""
    _, rows = _fetch(params.with_database("master"), "SELECT @@VERSION")
    return str(rows[0][0]).splitlines()[0].strip() if rows else ""


def list_databases(params: ServerParams) -> list[str]:
    """User databases this login can open, system databases excluded."""
    _, rows = _fetch(
        params.with_database("master"),
        "SELECT name FROM sys.databases "
        "WHERE database_id > 4 AND state_desc = 'ONLINE' AND HAS_DBACCESS(name) = 1 "
        "ORDER BY name",
    )
    return [str(row[0]) for row in rows]


def list_tables() -> list[dict[str, Any]]:
    """Tables and views in the bound database, with approximate row counts."""
    _, rows = _fetch(
        bound_params(),
        "SELECT s.name, o.name, o.type_desc, "
        "(SELECT SUM(p.rows) FROM sys.partitions p "
        " WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) "
        "FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id "
        "WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0 "
        "ORDER BY s.name, o.name",
    )
    return [
        {
            "name": f"{schema}.{name}",
            "type": "view" if kind == "VIEW" else "table",
            "rows": int(count) if count is not None else None,
        }
        for schema, name, kind, count in rows
    ]


def schema_map() -> dict[str, dict[str, str]]:
    """Every column in the bound database as {SCHEMA.TABLE: {COLUMN: TYPE}}.

    Named as the model is told to write them (schema and table, no database),
    and upper case like every other connector's, because the semantic layer
    compares these against names typed by a person into the editor.

    Reused for SCHEMA_CACHE_SECONDS, as the description is: the layer editor
    asks for it on every page, and on a slow server each read is a wait.
    """
    params = bound_params()
    key = (params.host, params.port, params.username, params.database)
    now = time.monotonic()
    with _schema_lock:
        hit = _schema_maps.get(key)
    if hit and now - hit[0] < SCHEMA_CACHE_SECONDS:
        return hit[1]
    columns = _read_schema_map(params)
    with _schema_lock:
        _schema_maps[key] = (time.monotonic(), columns)
    return columns


def _read_schema_map(params: ServerParams) -> dict[str, dict[str, str]]:
    _, rows = _fetch(
        params,
        "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE "
        "FROM INFORMATION_SCHEMA.COLUMNS c "
        "JOIN INFORMATION_SCHEMA.TABLES t "
        "  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
        "WHERE t.TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA') "
        "ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION",
    )
    out: dict[str, dict[str, str]] = {}
    for schema, table, column, data_type in rows:
        out.setdefault(f"{schema}.{table}".upper(), {})[str(column).upper()] = str(data_type)
    return out


_schema_cache: dict[tuple, tuple[float, str]] = {}
_schema_maps: dict[tuple, tuple[float, dict[str, dict[str, str]]]] = {}
_schema_lock = threading.Lock()

#: How to write SQL for this source. Placed at the top of the context block so
#: the chat, the SQL generator and the dashboard planner all read the same rules.
_TSQL_GUIDE = """This source is Microsoft SQL Server. Write T-SQL — not Snowflake, not DuckDB:
- Name tables with their schema, as listed below: [Sales].[Customer]. A database prefix is optional; never omit the schema.
- Limit rows with SELECT TOP n ... and an ORDER BY. There is no LIMIT, OFFSET-without-ORDER BY, or QUALIFY.
- Month buckets: DATEFROMPARTS(YEAR(d), MONTH(d), 1). Quarter labels: CONCAT(YEAR(d), '-Q', DATEPART(quarter, d)). Years as text: CAST(YEAR(d) AS varchar(4)).
- Integer division truncates: multiply by 1.0 before dividing, and use NULLIF(denominator, 0).
- ORDER BY is not allowed inside a CTE, view or subquery unless it also has TOP; order the outer SELECT.
- A column alias cannot be used in the WHERE or GROUP BY of the same SELECT; repeat the expression or compute it in a CTE.
- Use ISNULL/COALESCE, CONCAT, and CAST/CONVERT. Booleans are bit columns compared with 1 and 0.
- No semantic layer has been written for this database. Column meanings come from their names and types only, so say so when a figure depends on reading a column's meaning."""


def schema_context(params: ServerParams, label: str | None = None) -> str:
    """Describe the bound database for the model: tables, columns, row counts
    and the foreign keys that say how tables join.

    Read from the catalogue rather than written by hand, because nobody has
    written a semantic layer for a database connected on the day. Cached per
    database for SCHEMA_CACHE_SECONDS, per worker process.
    """
    key = (params.host, params.port, params.username, params.database)
    now = time.monotonic()
    with _schema_lock:
        cached = _schema_cache.get(key)
        if cached and now - cached[0] < SCHEMA_CACHE_SECONDS:
            return cached[1]

    text = _build_schema_context(params, label)
    with _schema_lock:
        _schema_cache[key] = (time.monotonic(), text)
    return text


def _build_schema_context(params: ServerParams, label: str | None) -> str:
    """Read the catalogue over ONE login.

    It used to take three — columns, row counts, foreign keys — each opening
    its own connection. On a server where a login costs seconds, the chat sat
    waiting for all three before it could say anything, and one timing out
    failed the whole read.
    """
    with _connection_for(params) as conn:
        _, column_rows = _rows_on(
            conn,
            "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, t.TABLE_TYPE, c.COLUMN_NAME, c.DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS c "
            "JOIN INFORMATION_SCHEMA.TABLES t "
            "  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
            "WHERE t.TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA') "
            "ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION",
        )

        # Row counts and foreign keys are worth having but not worth failing
        # for: a catalogue without them still names every table and column.
        counts: dict[str, int | None] = {}
        try:
            _, count_rows = _rows_on(
                conn,
                "SELECT s.name, o.name, "
                "(SELECT SUM(p.rows) FROM sys.partitions p "
                " WHERE p.object_id = o.object_id AND p.index_id IN (0, 1)) "
                "FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id "
                "WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0",
            )
            counts = {
                f"{schema}.{table}": int(rows) if rows is not None else None
                for schema, table, rows in count_rows
            }
        except pymssql.Error:
            log.warning("Could not read row counts for %s", params.database, exc_info=True)

        foreign_keys: list[str] = []
        try:
            _, fk_rows = _rows_on(
                conn,
                "SELECT OBJECT_SCHEMA_NAME(fk.parent_object_id), OBJECT_NAME(fk.parent_object_id), "
                "COL_NAME(fkc.parent_object_id, fkc.parent_column_id), "
                "OBJECT_SCHEMA_NAME(fk.referenced_object_id), OBJECT_NAME(fk.referenced_object_id), "
                "COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) "
                "FROM sys.foreign_keys fk "
                "JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id "
                "ORDER BY 1, 2",
            )
            foreign_keys = [
                f"[{ps}].[{pt}].{pc} -> [{rs}].[{rt}].{rc}"
                for ps, pt, pc, rs, rt, rc in fk_rows
            ]
        except pymssql.Error:
            log.warning("Could not read foreign keys for %s", params.database, exc_info=True)
        conn.rollback()

    tables: dict[str, dict[str, Any]] = {}
    for schema, table, table_type, column, data_type in column_rows:
        name = f"{schema}.{table}"
        entry = tables.setdefault(
            name, {"schema": schema, "table": table, "view": table_type == "VIEW", "columns": []}
        )
        entry["columns"].append(f"{column} {data_type}")

    title = label or params.database or "SQL Server"
    lines = [f"# SQL Server database: {title}", "", _TSQL_GUIDE, "", "## Tables and views"]
    shown = list(tables.items())[:MAX_CONTEXT_TABLES]
    for name, entry in shown:
        columns = entry["columns"]
        extra = ""
        if len(columns) > MAX_CONTEXT_COLUMNS:
            extra = f", … {len(columns) - MAX_CONTEXT_COLUMNS} more columns"
            columns = columns[:MAX_CONTEXT_COLUMNS]
        count = counts.get(name)
        size = f", about {count:,} rows" if count is not None else ""
        kind = "view" if entry["view"] else "table"
        lines.append(
            f"- [{entry['schema']}].[{entry['table']}] ({kind}{size}): "
            + ", ".join(columns) + extra
        )
    if len(tables) > MAX_CONTEXT_TABLES:
        lines.append(
            f"- … {len(tables) - MAX_CONTEXT_TABLES} more tables not listed; "
            "use list_tables to see them."
        )
    if foreign_keys:
        lines += ["", "## Foreign keys (how tables join)"]
        lines += [f"- {fk}" for fk in foreign_keys[:400]]
    return "\n".join(lines)
