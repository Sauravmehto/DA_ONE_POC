"""DuckDB connector — uploaded spreadsheets, queried as SQL.

client.py holds the implementation; this file adapts it to the DBConnector
interface and registers it, exactly as connectors/snowflake/ does. Nothing in
connectors/base.py, connectors/registry.py or the Snowflake package changed to
add this one, which was the point of that structure existing.
"""

from __future__ import annotations

from typing import Any

from app.engine.connectors import registry
from app.engine.connectors.base import SchemaMap
from app.engine.connectors.duckdb import client


class DuckDBConnector:
    name = "duckdb"
    dialect = "duckdb"
    # An uploaded workbook's sheets are tables with no schema above them.
    table_name_parts = 1
    #: An uploaded spreadsheet, and a folder of uploads.
    PREFIXES = ("EXCEL:", "FOLDER:")

    def owns(self, source_key: str) -> bool:
        return (source_key or "").upper().startswith(self.PREFIXES)

    def display_name(self, source_key: str) -> str:
        from app.engine import excel_sources

        key = source_key or ""
        source_id = key.split(":", 1)[1] if ":" in key else key
        if key.upper().startswith("FOLDER:"):
            collection = excel_sources.get_collection(source_id)
            return collection.name if collection else key
        dataset = excel_sources.get_dataset(source_id)
        return dataset.dataset_name if dataset else key

    def unavailable(self, exc: Exception) -> bool:
        return False

    def schema_map(self, source_key: str) -> SchemaMap:
        return client.schema_map()

    def catalogue_text(self, source_key: str) -> str:
        return client.catalogue_text()

    def run_query(self, sql: str) -> dict[str, Any]:
        return client.run_query(sql)

    def dry_run_query(self, sql: str) -> list[str]:
        return client.dry_run_query(sql)

    def session_identity(self) -> tuple[str | None, str | None]:
        return client.session_identity()

    # Bound per request, like the Snowflake connector — except what is bound is
    # a path to an uploaded source rather than a warehouse session:
    #   with connector.use_connection(path, identity=(user, role)):
    #       ...engine calls read that spreadsheet...
    use_connection = staticmethod(client.use_connection)


connector = DuckDBConnector()
registry.register("duckdb", connector)
