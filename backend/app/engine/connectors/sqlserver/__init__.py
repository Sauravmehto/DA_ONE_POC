"""SQL Server connector — a database connected from the Connections page.

client.py holds the implementation; this file adapts it to the DBConnector
interface and registers it, the same way connectors/duckdb/ does.
"""

from __future__ import annotations

from typing import Any

from app.engine.connectors import registry
from app.engine.connectors.base import SchemaMap
from app.engine.connectors.sqlserver import client


class SqlServerConnector:
    name = "sqlserver"
    dialect = client.DIALECT
    # Tables are named within the database the connection is on.
    table_name_parts = 2
    MAX_ROWS = client.MAX_ROWS
    #: SQLSERVER:<connection>/<database>. Spelled here rather than imported
    #: from db_connections, which imports this package.
    PREFIX = "SQLSERVER:"

    def owns(self, source_key: str) -> bool:
        return (source_key or "").upper().startswith(self.PREFIX)

    def display_name(self, source_key: str) -> str:
        """"<database> (<connection name>)", or just the database when the
        connection it names is gone."""
        from app.engine import db_connections

        try:
            connection_id, database = db_connections.parse_source_key(source_key)
        except db_connections.ConnectionSetupError:
            return source_key
        saved = db_connections.get_connection(connection_id)
        return f"{database} ({saved.name})" if saved else database

    def unavailable(self, exc: Exception) -> bool:
        # A login refused, a connection lost, a server out of memory: pymssql
        # reports these as operational, and a statement that will not compile
        # as programming.
        return isinstance(exc, (client.pymssql.OperationalError,
                                client.pymssql.InterfaceError))

    def schema_map(self, source_key: str) -> SchemaMap:
        return client.schema_map()

    def catalogue_text(self, source_key: str) -> str:
        return client.schema_context(client.bound_params(), self.display_name(source_key))

    def run_query(self, sql: str) -> dict[str, Any]:
        return client.run_query(sql)

    def dry_run_query(self, sql: str) -> list[str]:
        return client.dry_run_query(sql)

    def session_identity(self) -> tuple[str | None, str | None]:
        return client.session_identity()

    # Bound per request with the saved connection's details and one database:
    #   with connector.use_connection(params, identity=(user, role)):
    use_connection = staticmethod(client.use_connection)


connector = SqlServerConnector()
registry.register("sqlserver", connector)
