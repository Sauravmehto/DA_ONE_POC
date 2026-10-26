"""Bind the connector for sources whose dashboards file_dashboards keeps:
uploaded spreadsheets and folders (DuckDB) and SQL Server databases.

Shared by the agent chat endpoint and the dashboards API so file-dashboard
runs attach the same sources the conversation would.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app.engine import collection_query
from app.engine import excel_sources as engine_sources
from app.engine.connectors.duckdb import client as duckdb_client
from app.engine.file_dashboards import COLLECTION_PREFIX, EXCEL_PREFIX, SQLSERVER_PREFIX


@contextmanager
def bind_upload_source(
    source_key: str,
    identity: tuple[str | None, str | None],
    *,
    collection: Any | None = None,
    excel_source: Any | None = None,
) -> Iterator[None]:
    """Bind the connector for EXCEL:<id>, FOLDER:<id> or SQLSERVER:<id>/<db> for
    the duration of a request.

    A SQL Server source's login is read from the app's own file, so it needs
    no Snowflake connection.
    """
    upper = (source_key or "").upper()
    if upper.startswith(SQLSERVER_PREFIX):
        from app.engine import db_connections
        from app.engine.connectors.sqlserver import client as sqlserver_client

        try:
            source = db_connections.resolve_source(source_key)
        except db_connections.ConnectionSetupError as exc:
            raise ValueError(str(exc)) from exc
        with sqlserver_client.use_connection(source.params, identity=identity):
            yield
    elif upper.startswith(COLLECTION_PREFIX):
        if collection is None:
            collection_id = source_key.split(":", 1)[1]
            collection = engine_sources.get_collection(collection_id)
        if collection is None:
            raise ValueError(f"Folder '{source_key}' is not available.")
        plan = collection_query.plan_for(collection)
        sources, views = plan.as_binding()
        with duckdb_client.use_collection(
            sources, views, documents=plan.documents, identity=identity
        ):
            yield
    elif upper.startswith(EXCEL_PREFIX):
        if excel_source is None:
            dataset_id = source_key.split(":", 1)[1]
            excel_source = engine_sources.get_dataset(dataset_id)
        if excel_source is None:
            raise ValueError(f"Spreadsheet '{source_key}' is not available.")
        database_path = engine_sources.local_database(excel_source)
        with duckdb_client.use_connection(database_path, identity=identity):
            yield
    else:
        raise ValueError(f"Not an upload source: {source_key!r}")
