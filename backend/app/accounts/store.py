"""The SQLite file that holds the app's own records: accounts, connections,
saved dashboards, what is known about uploads, reports, data groups and the
audit log.

WHY SQLITE
----------
These records have to exist where Snowflake does not. A client with only files
and SQL Server has no warehouse to keep them in, and asking their IT to run a
database server just for the app's bookkeeping is the setup cost this avoids.
The file sits on the app's own volume and needs nothing installed.

FOUR WORKERS, ONE FILE
----------------------
Production runs four uvicorn workers against this one file. WAL mode lets reads
carry on while a write happens, and busy_timeout makes a second writer wait for
the first rather than fail. Account traffic is a handful of writes a minute, far
below what that handles. Every call opens its own connection: a connection
shared across threads is exactly what sqlite3 refuses.

The file must be on a local disk. SQLite's locking is unreliable on a network
share (Azure Files included), and a corrupted account file is not recoverable.

SCHEMA CHANGES
--------------
`PRAGMA user_version` records which of MIGRATIONS have run. Each runs once, in
order, inside a write transaction, so four workers starting together cannot
apply one twice.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings

#: Applied in order; the index + 1 is the schema version it brings the file to.
#: Append only. Editing a migration that has shipped changes nothing for a
#: file that already ran it.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE users (
        user_id        TEXT PRIMARY KEY,
        -- What the rest of the app records as "who": conversation owners,
        -- created_by, the audit log. For an account first made by a Snowflake
        -- sign-in it is the Snowflake user name, so that person's existing
        -- history stays theirs.
        username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
        email          TEXT UNIQUE COLLATE NOCASE,
        display_name   TEXT,
        is_admin       INTEGER NOT NULL DEFAULT 0,
        is_active      INTEGER NOT NULL DEFAULT 1,
        failed_logins  INTEGER NOT NULL DEFAULT 0,
        locked_until   TEXT,
        created_at     TEXT NOT NULL,
        created_by     TEXT,
        last_login_at  TEXT
    );

    -- One row per way of signing in. kind 'password': secret is the hash.
    -- kind 'snowflake': subject is the Snowflake user, secret its refresh
    -- token, encrypted with TOKEN_ENCRYPTION_KEY.
    CREATE TABLE credentials (
        user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        kind        TEXT NOT NULL,
        subject     TEXT NOT NULL,
        secret      TEXT,
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (user_id, kind),
        UNIQUE (kind, subject)
    );

    CREATE TABLE invitations (
        invitation_id  TEXT PRIMARY KEY,
        token_hash     TEXT NOT NULL UNIQUE,
        email          TEXT NOT NULL COLLATE NOCASE,
        is_admin       INTEGER NOT NULL DEFAULT 0,
        created_by     TEXT,
        created_at     TEXT NOT NULL,
        expires_at     TEXT NOT NULL,
        used_at        TEXT
    );

    -- A Snowflake sign-in started from inside an account, to link it. Keyed by
    -- a hash of the OAuth state so the callback knows whose account to link.
    CREATE TABLE snowflake_link_requests (
        state_hash  TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        expires_at  TEXT NOT NULL
    );
    """,
    """
    -- When the email was proven to belong to this person. NULL: not yet, and a
    -- password sign-in is refused until it is.
    ALTER TABLE users ADD COLUMN email_verified_at TEXT;

    -- Who last decided this account's admin flag: 'admin' (someone on the
    -- Users page) or 'snowflake-role' (promoted once, from ADMIN_ROLES, at a
    -- Snowflake sign-in). NULL: never decided, so a promotion may still apply.
    ALTER TABLE users ADD COLUMN admin_set_by TEXT;

    -- Single-use links sent by email. Only a hash of the token is kept.
    CREATE TABLE email_tokens (
        token_hash  TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        purpose     TEXT NOT NULL,
        email       TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        used_at     TEXT
    );
    """,
    """
    -- The app's own records, beside the accounts: what it used to keep in
    -- Snowflake tables or as JSON in storage. Uploaded files stay in storage;
    -- only what is known about them is here.
    --
    -- JSON columns (a dashboard plan, a list of tags, a spreadsheet's tables)
    -- are written and read whole. "Who" columns hold users.username, as every
    -- other record in the app does, so a keypair development session with no
    -- account row can still own things.

    -- Settings for the whole deployment, and markers for one-time jobs such
    -- as copying records across from Snowflake.
    CREATE TABLE app_settings (
        key         TEXT PRIMARY KEY,
        value       TEXT,
        updated_by  TEXT,
        updated_at  TEXT NOT NULL
    );

    -- Connections to databases other than Snowflake. The password is
    -- encrypted with TOKEN_ENCRYPTION_KEY and cleared when the connection is
    -- removed.
    CREATE TABLE data_connections (
        connection_id  TEXT PRIMARY KEY,
        name           TEXT NOT NULL,
        vendor         TEXT NOT NULL,
        host           TEXT NOT NULL,
        port           INTEGER NOT NULL,
        username       TEXT NOT NULL,
        password_enc   TEXT,
        created_by     TEXT,
        created_at     TEXT NOT NULL,
        is_active      INTEGER NOT NULL DEFAULT 1
    );

    -- One row per saved dashboard. source_key names where its data comes
    -- from: a Snowflake database, SQLSERVER:<connection>/<database>,
    -- EXCEL:<dataset> or FOLDER:<collection>. current_version points into
    -- dashboard_versions.
    CREATE TABLE dashboards (
        report_id        TEXT PRIMARY KEY,
        report_title     TEXT NOT NULL,
        description      TEXT,
        tags             TEXT,
        source_key       TEXT NOT NULL,
        source_type      TEXT NOT NULL,
        database_name    TEXT,
        data_group       TEXT,
        current_version  INTEGER NOT NULL DEFAULT 1,
        created_by       TEXT,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        last_run_at      TEXT,
        run_count        INTEGER NOT NULL DEFAULT 0,
        is_active        INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX dashboards_by_source ON dashboards (source_key);
    CREATE INDEX dashboards_by_owner ON dashboards (created_by COLLATE NOCASE);

    -- Every saved version of every dashboard, the current one included. A
    -- version is never changed once written: an edit or a revert adds one.
    CREATE TABLE dashboard_versions (
        report_id     TEXT NOT NULL REFERENCES dashboards(report_id) ON DELETE CASCADE,
        version       INTEGER NOT NULL,
        report_title  TEXT,
        description   TEXT,
        plan          TEXT NOT NULL,
        edit_note     TEXT,
        created_by    TEXT,
        created_at    TEXT NOT NULL,
        PRIMARY KEY (report_id, version)
    );

    -- Folders of uploaded files, and the spreadsheets uploaded into them.
    CREATE TABLE excel_collections (
        collection_id  TEXT PRIMARY KEY,
        name           TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        is_active      INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE excel_datasets (
        dataset_id     TEXT PRIMARY KEY,
        dataset_name   TEXT NOT NULL,
        fingerprint    TEXT,
        sheet_count    INTEGER,
        tables         TEXT,
        files          TEXT,
        database_size  INTEGER,
        collection_id  TEXT REFERENCES excel_collections(collection_id),
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        is_active      INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX excel_datasets_by_collection ON excel_datasets (collection_id);
    CREATE INDEX excel_datasets_by_fingerprint ON excel_datasets (fingerprint);

    -- What the columns of a spreadsheet layout mean, shared by every upload
    -- with the same layout (fingerprint).
    CREATE TABLE excel_definitions (
        fingerprint  TEXT PRIMARY KEY,
        mapping      TEXT,
        concepts     TEXT,
        updated_by   TEXT,
        updated_at   TEXT NOT NULL
    );

    -- Report Builder: an approved workbook template, and the reports that
    -- fill it.
    CREATE TABLE excel_templates (
        template_id             TEXT PRIMARY KEY,
        template_name           TEXT NOT NULL,
        fingerprint             TEXT,
        tab_roles               TEXT,
        formula_classification  TEXT,
        approved_by             TEXT,
        approved_at             TEXT,
        created_at              TEXT NOT NULL,
        is_active               INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX excel_templates_by_fingerprint ON excel_templates (fingerprint);

    CREATE TABLE excel_reports (
        report_id            TEXT PRIMARY KEY,
        template_id          TEXT REFERENCES excel_templates(template_id),
        report_title         TEXT NOT NULL,
        description          TEXT,
        parameterized_query  TEXT,
        created_by           TEXT,
        created_at           TEXT NOT NULL,
        last_run_at          TEXT,
        run_count            INTEGER NOT NULL DEFAULT 0,
        is_active            INTEGER NOT NULL DEFAULT 1
    );

    -- Data groups: who may see which databases, folders, dashboards and
    -- reports. member_type is USER or ROLE. resource_type is database,
    -- folder, dashboard or report; is_main marks the one group a resource is
    -- listed under.
    CREATE TABLE data_groups (
        group_id     TEXT PRIMARY KEY,
        name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description  TEXT,
        created_by   TEXT,
        created_at   TEXT NOT NULL,
        updated_by   TEXT,
        updated_at   TEXT NOT NULL
    );

    CREATE TABLE data_group_members (
        group_id     TEXT NOT NULL REFERENCES data_groups(group_id) ON DELETE CASCADE,
        member_type  TEXT NOT NULL,
        member_name  TEXT NOT NULL COLLATE NOCASE,
        created_by   TEXT,
        created_at   TEXT NOT NULL,
        PRIMARY KEY (group_id, member_type, member_name)
    );

    CREATE TABLE data_group_resources (
        group_id       TEXT NOT NULL REFERENCES data_groups(group_id) ON DELETE CASCADE,
        resource_type  TEXT NOT NULL,
        resource_id    TEXT NOT NULL,
        is_main        INTEGER NOT NULL DEFAULT 0,
        created_by     TEXT,
        created_at     TEXT NOT NULL,
        PRIMARY KEY (group_id, resource_type, resource_id)
    );
    CREATE INDEX data_group_resources_by_resource
        ON data_group_resources (resource_type, resource_id);

    -- One row per tool call, chat turn and model call, with what it cost.
    -- Written often and never changed.
    CREATE TABLE audit_events (
        event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        event_ts            TEXT NOT NULL,
        audit_user          TEXT,
        auth_mode           TEXT,
        snowflake_user      TEXT,
        snowflake_role      TEXT,
        session_id          TEXT,
        conversation_id     TEXT,
        tool_name           TEXT NOT NULL,
        user_question       TEXT,
        generated_sql       TEXT,
        row_count           INTEGER,
        status              TEXT,
        error_message       TEXT,
        duration_ms         INTEGER,
        client_info         TEXT,
        model               TEXT,
        model_calls         INTEGER,
        input_tokens        INTEGER,
        output_tokens       INTEGER,
        cache_read_tokens   INTEGER,
        cache_write_tokens  INTEGER
    );
    CREATE INDEX audit_events_by_time ON audit_events (event_ts);
    CREATE INDEX audit_events_by_conversation ON audit_events (conversation_id);
    """,
    """
    -- The semantic layer for one source: what its tables and columns MEAN,
    -- which the server itself cannot say. Written and read whole, as the TOML
    -- a person edits, so an exported layer is the document they wrote.
    --
    -- source_key names the source the layer describes, as everywhere else in
    -- the app: 'LOAN' for a Snowflake database, 'SQLSERVER:<connection>/<db>'.
    -- state is 'published' or 'draft': a half-finished draft must be invisible
    -- to the model, so the two are separate rows rather than a flag.
    CREATE TABLE semantic_layers (
        source_key  TEXT NOT NULL,
        state       TEXT NOT NULL,
        document    TEXT NOT NULL,
        version     INTEGER NOT NULL DEFAULT 1,
        updated_by  TEXT,
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (source_key, state)
    );

    -- Every published version, kept so a layer can be compared with, or put
    -- back to, what it said before.
    CREATE TABLE semantic_layer_versions (
        source_key   TEXT NOT NULL,
        version      INTEGER NOT NULL,
        document     TEXT NOT NULL,
        published_by TEXT,
        published_at TEXT NOT NULL,
        PRIMARY KEY (source_key, version)
    );
    """,
    """
    -- What a source's server said its tables and columns are, as the text the
    -- model is given. A cache, not a record: it is re-read when it ages out,
    -- and losing it costs one slow read.
    --
    -- Shared by every worker, because reading it is what the person waits
    -- for. A server slow to accept a login took tens of seconds per read, and
    -- each of the four workers paid that separately, on top of paying it
    -- again after every restart.
    CREATE TABLE source_catalogues (
        source_key   TEXT PRIMARY KEY,
        description  TEXT NOT NULL,
        read_at      TEXT NOT NULL
    );
    """,
    """
    -- A dashboard plan waiting to be saved, or an edit waiting to be
    -- confirmed. Short-lived: it lives from "here is what I propose" until
    -- the person accepts or walks away.
    --
    -- Here rather than in object storage because all four workers must see
    -- it — the worker that proposes a plan is rarely the one that saves it —
    -- and because a dashboard should not touch object storage at all now
    -- that the records are here.
    CREATE TABLE pending_plans (
        plan_id     TEXT PRIMARY KEY,
        kind        TEXT NOT NULL,
        source_key  TEXT NOT NULL,
        payload     TEXT NOT NULL,
        created_by  TEXT,
        created_at  TEXT NOT NULL
    );
    """,
    """
    -- excel_datasets.collection_id pointed at excel_collections, which is
    -- wrong: a dataset is grouped by the folder its files came from whether
    -- or not anyone has named that folder, and only a named one has a row.
    -- The reference refused every upload into an unnamed folder.
    CREATE TABLE excel_datasets_new (
        dataset_id     TEXT PRIMARY KEY,
        dataset_name   TEXT NOT NULL,
        fingerprint    TEXT,
        sheet_count    INTEGER,
        tables         TEXT,
        files          TEXT,
        database_size  INTEGER,
        collection_id  TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        is_active      INTEGER NOT NULL DEFAULT 1
    );
    INSERT INTO excel_datasets_new SELECT dataset_id, dataset_name, fingerprint,
        sheet_count, tables, files, database_size, collection_id, created_at,
        updated_at, is_active FROM excel_datasets;
    DROP TABLE excel_datasets;
    ALTER TABLE excel_datasets_new RENAME TO excel_datasets;
    CREATE INDEX excel_datasets_by_collection ON excel_datasets (collection_id);
    CREATE INDEX excel_datasets_by_fingerprint ON excel_datasets (fingerprint);
    """,
    """
    -- Which database a report's queries run against. A template has none: the
    -- same workbook shape can be filled from two different databases, so what
    -- names a source is the report.
    ALTER TABLE excel_reports ADD COLUMN source_key TEXT;
    CREATE INDEX excel_reports_by_source ON excel_reports (source_key);
    """,
    """
    -- excel_reports.template_id pointed at excel_templates, which refuses a
    -- report whose template was approved before templates moved here — and
    -- those templates are read where they are, not copied. Same mistake as
    -- excel_datasets.collection_id, same fix.
    CREATE TABLE excel_reports_new (
        report_id            TEXT PRIMARY KEY,
        template_id          TEXT,
        report_title         TEXT NOT NULL,
        description          TEXT,
        parameterized_query  TEXT,
        source_key           TEXT,
        created_by           TEXT,
        created_at           TEXT NOT NULL,
        last_run_at          TEXT,
        run_count            INTEGER NOT NULL DEFAULT 0,
        is_active            INTEGER NOT NULL DEFAULT 1
    );
    INSERT INTO excel_reports_new SELECT report_id, template_id, report_title,
        description, parameterized_query, source_key, created_by, created_at,
        last_run_at, run_count, is_active FROM excel_reports;
    DROP TABLE excel_reports;
    ALTER TABLE excel_reports_new RENAME TO excel_reports;
    CREATE INDEX excel_reports_by_source ON excel_reports (source_key);
    """,
    """
    -- The same workbook shape, hashed without the sheets' NAMES. Renaming a
    -- tab changed a template's fingerprint in two places at once, so an
    -- approved template came back unrecognised and a person had to read it
    -- again. Matched only when the exact fingerprint misses, and added beside
    -- it rather than replacing it, because every template approved before
    -- this is stored under the exact one.
    ALTER TABLE excel_templates ADD COLUMN shape_fingerprint TEXT;
    CREATE INDEX excel_templates_by_shape ON excel_templates (shape_fingerprint);
    """,
    """
    -- Bumped whenever every session of this account must end: a password
    -- reset or change. A session carries the value it signed in with, and one
    -- behind the account's is over (spec 0007). Starts at 0, which is what
    -- sessions stored before this column read back as, so the deploy that
    -- adds it signs nobody out.
    ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
    """,
    """
    -- Free trials (spec 0010). Keyed by the email, with Gmail dots and +tags
    -- folded out, and deliberately NOT tied to users: deleting an account must
    -- not give its address a fresh trial. Accounts with no row here are not on
    -- a trial at all, which is what every account made before this reads as.
    CREATE TABLE trials (
        trial_key      TEXT PRIMARY KEY,
        email          TEXT NOT NULL,
        started_at     TEXT NOT NULL,
        ends_at        TEXT NOT NULL,
        messages_used  INTEGER NOT NULL DEFAULT 0,
        extended_by    TEXT
    );
    """,
]

_ready_for: str | None = None
_ready_lock = threading.Lock()


def _path() -> Path:
    return Path(settings.APP_DB_PATH)


def _open() -> sqlite3.Connection:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: transactions are opened explicitly below, so a
    # write can take the lock up front instead of upgrading halfway through.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for index in range(version, len(MIGRATIONS)):
            for statement in _statements(MIGRATIONS[index]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {index + 1}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _statements(script: str) -> list[str]:
    """Split a migration into statements. executescript() would commit the
    open transaction first, which is the one thing a migration must not do."""
    out: list[str] = []
    buffer: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if sqlite3.complete_statement(statement):
                out.append(statement)
                buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        out.append(tail)
    return out


def _ensure_schema() -> None:
    """Bring the file up to date, once per process per path."""
    global _ready_for
    target = str(_path().resolve())
    if _ready_for == target:
        return
    with _ready_lock:
        if _ready_for == target:
            return
        conn = _open()
        try:
            _migrate(conn)
        finally:
            conn.close()
        _ready_for = target


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    """A connection for reading. No transaction is held open."""
    _ensure_schema()
    conn = _open()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """A connection inside a write transaction, committed on success and rolled
    back on any error. IMMEDIATE takes the write lock at the start, so two
    workers never both read a value and then both try to change it."""
    _ensure_schema()
    conn = _open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def reset_for_tests() -> None:
    """Forget that the schema was checked, after a test points APP_DB_PATH at a
    fresh file."""
    global _ready_for
    with _ready_lock:
        _ready_for = None
