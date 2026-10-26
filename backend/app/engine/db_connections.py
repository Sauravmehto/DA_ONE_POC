"""Saved connections to databases other than Snowflake — SQL Server today.

An administrator adds one on the Connections page: a name, a host, a port and a
login. Everyone signed in can then chat with, and build dashboards from, the
databases that login can open.

WHERE THEY ARE KEPT, AND WHY
----------------------------
In the app's own SQLite file (app.accounts.store), so a deployment with no
Snowflake can still have them. Not in a worker's memory, which the other three
workers cannot see, and not in config/, which is rebuilt on every redeploy.
Connections saved before this lived in a Snowflake table; they are copied
across once, the first time someone with Snowflake connected opens the list.

The password is encrypted with TOKEN_ENCRYPTION_KEY before it is written and
decrypted only to open a connection. It is never returned by any route. Without
that key set, main.py gives each worker process its own random key, so a
password saved by one worker could not be read by another — saving is refused
instead.

WHICH HOSTS
-----------
Only those named in DB_CONNECTION_ALLOWED_HOSTS. The form sends the backend to
whatever address is typed into it, and the backend shares a network with other
services; an allowlist keeps the form from being a way to reach them.

HOW A SOURCE IS NAMED
---------------------
A conversation or a dashboard records its source as one string. A database on
a saved connection is "SQLSERVER:<connection_id>/<database>", alongside the
"EXCEL:" and "FOLDER:" names uploads already use.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.engine.connectors.sqlserver import client as sqlserver_client

log = logging.getLogger(__name__)

#: Where connections were saved before they moved to SQLite. Read only, to
#: copy them across.
TABLE = os.environ.get("MCP_DB_CONNECTIONS_TABLE", "LOAN.PUBLIC.MCP_DB_CONNECTIONS")

SOURCE_PREFIX = "SQLSERVER:"
VENDOR = "sqlserver"
DEFAULT_PORT = 1433

#: How long a connection's database list is reused before asking the server again.
#: How long a connection's list of databases is used before the server is
#: asked again. An hour rather than the two minutes it was: the list is kept
#: in the app's store now, so every worker shares one read instead of each
#: paying its own, and it survives a restart. A database added on the server
#: appears within the hour, or at once if the connection is saved again.
DATABASE_LIST_SECONDS = 3600

#: How long a request waits for a server to log in and answer something small:
#: listing its databases when a page is drawn, or proving it works when a
#: connection is saved.
#:
#: Room for every login the call may spend, not just the first. It was the
#: login timeout plus five, which is less than two logins take — so a server
#: whose first login timed out was given up on while its retry, the one written
#: for exactly that server, was still running and about to succeed.
SERVER_DEADLINE_SECONDS = (
    sqlserver_client.LOGIN_TIMEOUT_SECONDS * sqlserver_client.LOGIN_ATTEMPTS + 5
)

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")


class ConnectionSetupError(ValueError):
    """A connection cannot be saved or opened; the message says why."""


@dataclass
class SavedConnection:
    connection_id: str
    name: str
    vendor: str
    host: str
    port: int
    username: str
    created_by: str | None = None
    created_at: str | None = None

    def public(self) -> dict[str, Any]:
        """Everything about the connection except its password."""
        return {
            "connection_id": self.connection_id,
            "name": self.name,
            "vendor": self.vendor,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


# ── source names ─────────────────────────────────────────────────────────────


def is_source_key(name: str | None) -> bool:
    return bool(name) and name.upper().startswith(SOURCE_PREFIX)


def source_key(connection_id: str, database: str) -> str:
    return f"{SOURCE_PREFIX}{connection_id}/{database}"


def parse_source_key(name: str) -> tuple[str, str]:
    """(connection_id, database) from a SQLSERVER: source name."""
    if not is_source_key(name):
        raise ConnectionSetupError(f"Not a SQL Server source: {name!r}")
    body = name[len(SOURCE_PREFIX):]
    connection_id, _, database = body.partition("/")
    if not connection_id or not database:
        raise ConnectionSetupError(f"Malformed SQL Server source: {name!r}")
    return connection_id, database


# ── validation ───────────────────────────────────────────────────────────────


def allowed_hosts() -> list[str]:
    raw = os.environ.get("DB_CONNECTION_ALLOWED_HOSTS", "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def check_host(host: str) -> str:
    """The host, trimmed, if a connection may point at it; otherwise raise."""
    host = (host or "").strip()
    if not _HOST_RE.match(host):
        raise ConnectionSetupError("Enter a host name or IP address, without a port.")
    allowed = allowed_hosts()
    if not allowed:
        raise ConnectionSetupError(
            "No hosts are allowed for database connections on this deployment. "
            "Set DB_CONNECTION_ALLOWED_HOSTS."
        )
    if host.lower() not in allowed:
        raise ConnectionSetupError(
            f"{host} is not an allowed host. Allowed: {', '.join(allowed)}."
        )
    return host


def check_port(port: Any) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError):
        raise ConnectionSetupError("Port must be a number.") from None
    if not 1 <= value <= 65535:
        raise ConnectionSetupError("Port must be between 1 and 65535.")
    return value


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise ConnectionSetupError(
            "TOKEN_ENCRYPTION_KEY is not set, so a saved password could not be "
            "read back. Set it on the backend before saving a connection."
        )
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise ConnectionSetupError("TOKEN_ENCRYPTION_KEY is not a valid key.") from exc


# ── persistence ──────────────────────────────────────────────────────────────


def _store():
    from app.accounts import store

    return store


def _snowflake():
    from app.engine.connectors.snowflake import client as snowflake_client

    return snowflake_client


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _iso(moment: Any) -> str:
    """A Snowflake TIMESTAMP_NTZ, which the app writes in UTC, as ISO text."""
    if isinstance(moment, datetime):
        return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).isoformat()
    return str(moment) if moment else _now()


_COLUMNS = "connection_id, name, vendor, host, port, username, created_by, created_at"


def _row_to_connection(row) -> SavedConnection:
    return SavedConnection(
        connection_id=row["connection_id"],
        name=row["name"],
        vendor=row["vendor"],
        host=row["host"],
        port=int(row["port"]),
        username=row["username"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _active_rows() -> list:
    with _store().read() as conn:
        return conn.execute(
            f"SELECT {_COLUMNS}, password_enc FROM data_connections "
            "WHERE is_active = 1 ORDER BY created_at"
        ).fetchall()


def _active_row(connection_id: str):
    with _store().read() as conn:
        return conn.execute(
            f"SELECT {_COLUMNS}, password_enc FROM data_connections "
            "WHERE connection_id = ? AND is_active = 1",
            (connection_id,),
        ).fetchone()


def list_connections() -> list[SavedConnection]:
    return [_row_to_connection(row) for row in _active_rows()]


def get_connection(connection_id: str) -> SavedConnection | None:
    row = _active_row(connection_id)
    return _row_to_connection(row) if row else None


def server_params(connection_id: str, database: str | None = None) -> sqlserver_client.ServerParams:
    """Everything needed to open the saved connection, password decrypted."""
    row = _active_row(connection_id)
    if row is None or not row["password_enc"]:
        raise ConnectionSetupError("That connection no longer exists.")
    # Re-checked on every use, not only on save: taking a host off the
    # allowlist must stop connections already saved to it.
    check_host(row["host"])
    try:
        password = _fernet().decrypt(row["password_enc"].encode()).decode()
    except Exception as exc:  # noqa: BLE001 - InvalidToken, or a corrupt value
        raise ConnectionSetupError(
            "The saved password could not be decrypted — TOKEN_ENCRYPTION_KEY has "
            "changed since it was saved. Delete the connection and add it again."
        ) from exc
    return sqlserver_client.ServerParams(
        row["host"], int(row["port"]), row["username"], password, database
    )


def create_connection(name: str, params: sqlserver_client.ServerParams,
                      created_by: str | None) -> SavedConnection:
    name = (name or "").strip()[:120]
    if not name:
        raise ConnectionSetupError("Give the connection a name.")
    encrypted = _fernet().encrypt(params.password.encode()).decode()
    connection_id = uuid.uuid4().hex[:16]
    created_at = _now()
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO data_connections (connection_id, name, vendor, host, port, "
            "username, password_enc, created_by, created_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (connection_id, name, VENDOR, params.host, params.port, params.username,
             encrypted, created_by, created_at),
        )
    return SavedConnection(connection_id, name, VENDOR, params.host, params.port,
                           params.username, created_by, created_at)


def delete_connection(connection_id: str) -> bool:
    with _store().write() as conn:
        changed = conn.execute(
            "UPDATE data_connections SET is_active = 0, password_enc = NULL "
            "WHERE connection_id = ? AND is_active = 1",
            (connection_id,),
        ).rowcount
    from app.engine import catalogue_cache as _cache

    listed = None
    try:
        stored = _cache.read(databases_key(connection_id), DATABASE_LIST_SECONDS)
        listed = json.loads(stored) if stored else None
    except ValueError:
        listed = None
    _cache.forget(databases_key(connection_id))
    if changed:
        # The catalogues of its databases go with it, so a connection added
        # again to the same server is described afresh.
        from app.engine import catalogue_cache

        for database in (listed or []):
            _cache.forget(source_key(connection_id, database))
    return changed > 0


# ── connections saved before they were kept here ────────────────────────────

#: Set in app_settings once the Snowflake table has been copied.
IMPORT_MARKER = "imported.snowflake.db_connections"

_import_tried = False
_import_lock = threading.Lock()


def import_needed() -> bool:
    """Whether connections saved in Snowflake may still need copying here.

    Asked once per process: after the first attempt, successful or not, the
    answer is no until a restart, so a deployment without the old table does
    not try on every page load."""
    if _import_tried:
        return False
    with _store().read() as conn:
        done = conn.execute(
            "SELECT 1 FROM app_settings WHERE key = ?", (IMPORT_MARKER,)
        ).fetchone()
    return done is None


def import_from_snowflake(imported_by: str | None) -> int:
    """Copy the connections saved in the Snowflake table, once per deployment.

    Needs the signed-in person's Snowflake connection bound. The passwords are
    copied still encrypted, so they read back with the same
    TOKEN_ENCRYPTION_KEY. A connection already here is left as it is. Returns
    how many were copied."""
    global _import_tried
    with _import_lock:
        if not import_needed():
            return 0
        _import_tried = True
        cursor = _snowflake().get_connection().cursor()
        try:
            cursor.execute(
                "SELECT CONNECTION_ID, NAME, VENDOR, HOST, PORT, USERNAME, PASSWORD_ENC, "
                f"CREATED_BY, CREATED_AT FROM {TABLE} WHERE IS_ACTIVE = TRUE"
            )
            rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - no table, or no grant on it
            log.info("Saved connections were not copied from %s: %s", TABLE, exc)
            return 0
        finally:
            cursor.close()

        copied = 0
        with _store().write() as conn:
            for (connection_id, name, vendor, host, port, username, password_enc,
                 created_by, created_at) in rows:
                copied += conn.execute(
                    "INSERT OR IGNORE INTO data_connections (connection_id, name, vendor, "
                    "host, port, username, password_enc, created_by, created_at, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (connection_id, name, vendor or VENDOR, host, int(port or DEFAULT_PORT),
                     username, password_enc, created_by,
                     _iso(created_at)),
                ).rowcount
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (IMPORT_MARKER, str(copied), imported_by, _now()),
            )
        log.info("Copied %d saved connection(s) from %s", copied, TABLE)
        return copied


# ── what a connection reaches ────────────────────────────────────────────────

_database_lock = threading.Lock()
_listing_locks: dict[str, threading.Lock] = {}


def databases_key(connection_id: str) -> str:
    """Where this connection's list of databases is stored. Deliberately not a
    source key: it describes the connection, not one database on it."""
    return f"SQLSERVER-DATABASES:{connection_id}"


def databases_for(connection_id: str) -> list[str]:
    """Databases the saved login can open, reused for DATABASE_LIST_SECONDS.

    Kept in the app's store rather than in this worker's memory. Listing them
    means a login, this kind of server can take seconds to accept one, and
    three separate pages ask for the list — so each of four workers was paying
    for the same unchanging answer every couple of minutes, and paying again
    after every restart.

    Requests that arrive together share one login to the server: the first
    signs in and lists, the rest wait for its answer."""
    from app.engine import catalogue_cache

    key = databases_key(connection_id)

    def read_server() -> str:
        return json.dumps(sqlserver_client.list_databases(server_params(connection_id)))

    def cached() -> list[str] | None:
        stored = catalogue_cache.read(key, DATABASE_LIST_SECONDS)
        if stored is None:
            return None
        try:
            return json.loads(stored)
        except ValueError:      # stored by an older shape; read it again
            return None

    names = cached()
    if names is not None:
        # Nearly stale: replace it in the background, so the person who asks
        # at the hour is served the stored list rather than a login.
        elapsed = catalogue_cache.age(key)
        if elapsed is not None and elapsed >= DATABASE_LIST_SECONDS * catalogue_cache.REFRESH_AFTER:
            catalogue_cache.refresh_soon(key, read_server)
        return names
    with _database_lock:
        lock = _listing_locks.setdefault(connection_id, threading.Lock())
    # Bounded: when an earlier login to this server is stuck, a request behind
    # it is refused after the deadline instead of queueing for as long as that
    # login takes.
    if not lock.acquire(timeout=SERVER_DEADLINE_SECONDS):
        raise ConnectionSetupError(
            "This server is still answering an earlier request. Try again in a minute."
        )
    try:
        names = cached()
        if names is not None:
            return names
        params = server_params(connection_id)
        names = sqlserver_client.list_databases(params)
        try:
            catalogue_cache.write(key, json.dumps(names))
        except Exception:  # noqa: BLE001 - the list was read; storing it is the bonus
            log.warning("Could not store the database list for %s", connection_id,
                        exc_info=True)
        return names
    finally:
        lock.release()


def remember_databases(connection_id: str, databases: list[str]) -> None:
    """Keep a list of databases that has just been read somewhere else.

    Saving a connection tests it by listing its databases, and that answer was
    thrown away. So the page reloading the moment the dialog closed found
    nothing stored and logged in again — at the one moment the list was certain
    not to be there, which is why a connection that had just been saved took
    another login to appear, and why reloading by hand seemed to fix it: by
    then the background listing had finished.
    """
    from app.engine import catalogue_cache

    try:
        catalogue_cache.write(databases_key(connection_id), json.dumps(list(databases)))
    except Exception:  # noqa: BLE001 - the connection is saved; storing this is the bonus
        log.warning("Could not store the database list for %s", connection_id,
                    exc_info=True)


def read_catalogues_soon(connection_id: str, databases: list[str]) -> None:
    """Read each database's catalogue in the background and store it.

    Called when a connection is added: reading a catalogue means a login, and
    a slow server can take half a minute over one. Done here, the first person
    to ask a question finds it already read."""
    from app.engine import catalogue_cache

    saved = get_connection(connection_id)
    if saved is None:
        return
    for database in databases:
        # The same label the chat would build, so what is stored now is what
        # a reader would have read for itself.
        catalogue_cache.refresh_soon(
            source_key(connection_id, database),
            lambda db=database: sqlserver_client.schema_context(
                server_params(connection_id, db), f"{db} ({saved.name})"
            ),
        )


@dataclass
class SqlServerSource:
    """One database on a saved connection, as a chat or dashboard uses it."""

    connection: SavedConnection
    database: str
    params: sqlserver_client.ServerParams

    @property
    def key(self) -> str:
        return source_key(self.connection.connection_id, self.database)

    @property
    def display_name(self) -> str:
        return f"{self.database} ({self.connection.name})"

    def context(self) -> str:
        """What this database holds, for the model.

        Shared across workers: reading it means a login, and on a slow server
        that is what a person waits for before the chat can answer."""
        from app.engine import catalogue_cache

        return catalogue_cache.get_or_read(
            self.key,
            lambda: sqlserver_client.schema_context(self.params, self.display_name),
        )


def resolve_source(name: str) -> SqlServerSource:
    """The saved connection and database behind a SQLSERVER: source name."""
    connection_id, database = parse_source_key(name)
    connection = get_connection(connection_id)
    if connection is None:
        raise ConnectionSetupError("That SQL Server connection no longer exists.")
    return SqlServerSource(connection, database, server_params(connection_id, database))
