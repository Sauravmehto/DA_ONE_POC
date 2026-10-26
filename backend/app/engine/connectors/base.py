"""Vendor-agnostic data-source interface.

Every connector (Snowflake, SQL Server, DuckDB for uploads; Postgres, Oracle
and the rest later) implements this Protocol so core modules (dashboards,
the semantic layer, sql_safety, the chat) never import a vendor-specific
module directly — they ask connectors.registry for the connector that owns a
source instead.

A FEATURE IS WRITTEN ONCE
------------------------
Anything true of every database — the semantic layer and its editor,
dashboards and their versions, entitlements, auditing — belongs to the core
modules and is written against this interface. Only what genuinely differs per
vendor belongs in a connector: how its schema is read, which SQL dialect it
speaks, how a source of its kind is named. Adding a vendor should be one
package plus a registration, and should never require editing this file or
any core module.

HOW A SOURCE IS NAMED
---------------------
One string identifies a source everywhere in the app: "LOAN" for a Snowflake
database, "SQLSERVER:<connection>/<database>", "EXCEL:<dataset>",
"FOLDER:<collection>". `owns` is how a connector claims the ones that are its.

BOUND, NOT PASSED
-----------------
The methods that reach the server (run_query, schema_map, catalogue_text)
assume this request has already bound a connection — see each connector's
use_connection. That is what lets one query run as the signed-in person
without the signature of every core function carrying a connection.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

#: {FULL.TABLE.NAME: {COLUMN: DATA_TYPE}}, upper case, as the semantic layer's
#: validation and coverage compare against names a person typed.
SchemaMap = dict[str, dict[str, str]]


@runtime_checkable
class DBConnector(Protocol):
    # The name this connector is registered under ("snowflake", "sqlserver").
    name: str

    # The sqlglot dialect name this connector's SQL is validated against:
    # sql_safety.validate_sql(sql, dialect=connector.dialect).
    dialect: str

    def run_query(self, sql: str) -> dict[str, Any]:
        """Validate, cap, and execute a read-only query.

        Returns {columns, rows, row_count, sql} — the same shape every panel
        renderer and the webapp already consume today.
        """
        ...

    def dry_run_query(self, sql: str) -> list[str]:
        """Compile a query with zero rows fetched, to prove it resolves
        (columns/joins are valid) without paying for real execution.

        Returns the result column names.
        """
        ...

    def session_identity(self) -> tuple[str | None, str | None]:
        """Return (user, role) for the live session, for the audit trail."""
        ...

    # How many parts a table's name has here: 3 for DATABASE.SCHEMA.TABLE,
    # 2 for SCHEMA.TABLE, 1 for a bare name.
    table_name_parts: int

    def owns(self, source_key: str) -> bool:
        """Whether this connector is the one that serves `source_key`."""
        ...

    def display_name(self, source_key: str) -> str:
        """What to call this source on screen."""
        ...

    def schema_map(self, source_key: str) -> SchemaMap:
        """Every table and column the bound source has, for the semantic
        layer's editor, its coverage figures and its validation.

        One read, not one per table: a layer is validated whole, and a
        round trip per table would make publishing a wide database slow.
        """
        ...

    def unavailable(self, exc: Exception) -> bool:
        """Whether a failure was the source being unreachable, rather than
        anything wrong with the SQL.

        The difference matters: SQL that does not compile is a panel to drop
        and say so, while a server that will not answer is a reason to stop —
        dropping panels then quietly turns an outage into a smaller
        dashboard.
        """
        ...

    def catalogue_text(self, source_key: str) -> str:
        """The bound source's tables and columns as a text block for the
        model. What EXISTS — the semantic layer says what it MEANS."""
        ...
