"""Sources across every connector, for routes that work on any of them.

A feature that is true of every database — the semantic layer, and what
follows it — is written against this rather than against one vendor. Listing
what a person may work on, binding the right connection for a request and
deciding who may author are the three things such a route needs, and each one
asks the connector rather than testing the name itself.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.api.deps import (can_author_snowflake, has_snowflake_link,
                          session_is_admin, snowflake_session)
from app.core.config import settings
from app.engine import db_connections, source_binding
from app.engine.connectors import registry
from app.engine.connectors.snowflake import client as engine_client
from app.services.snowflake import SnowflakeNotLinked, SnowflakeService
from app.session.base import SessionData

log = logging.getLogger(__name__)

#: Snowflake's own databases, which nobody authors a layer for.
SKIP_DATABASES = {"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"}


@dataclass(frozen=True)
class Source:
    """One database a layer can be written for, whoever serves it."""

    key: str
    display_name: str
    connector: str


def connector_for(source_key: str):
    try:
        return registry.for_source(source_key)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


async def list_sources(
    request: Request,
    session: SessionData,
    *,
    connector: str | None = None,
    connection_id: str | None = None,
) -> list[Source]:
    """The databases this person can work on, from every connector.

    `connector` and `connection_id` narrow it to one workspace's own sources —
    a SQL Server connection's databases have nothing to do with a warehouse's,
    and asking for them costs a query to a warehouse that was not wanted.

    A connector that cannot be reached contributes nothing rather than failing
    the list: one unreachable SQL Server should not hide the warehouse.
    """
    sources: list[Source] = []

    # A stored link, not a live session: a password sign-in has not opened its
    # warehouse connection yet, and asking it to would be the first thing this
    # call does anyway.
    if await has_snowflake_link(session) and connector in (None, "snowflake"):
        try:
            service = SnowflakeService(
                settings, request.app.state.session_store, request.app.state.token_encryptor
            )
            rows = await service.execute_query_dict(session, "SHOW DATABASES")
            for row in rows:
                name = str(row.get("name") or row.get("NAME") or "").upper()
                if name and name not in SKIP_DATABASES:
                    sources.append(Source(name, name, "snowflake"))
        except SnowflakeNotLinked:
            pass
        except Exception:  # noqa: BLE001 - a warehouse that will not answer
            log.warning("Could not list Snowflake databases", exc_info=True)

    if connector not in (None, "sqlserver"):
        return sources

    for connection in await asyncio.to_thread(db_connections.list_connections):
        if connection_id and connection.connection_id != connection_id:
            continue
        try:
            databases = await asyncio.to_thread(
                db_connections.databases_for, connection.connection_id
            )
        except Exception:  # noqa: BLE001 - a server that will not answer now
            log.info("Could not list databases on %s", connection.name, exc_info=True)
            continue
        for database in databases:
            sources.append(Source(
                db_connections.source_key(connection.connection_id, database),
                f"{database} ({connection.name})",
                "sqlserver",
            ))

    return sources


async def run_on_source(request: Request, session: SessionData, source_key: str,
                        fn, *args):
    """Run a blocking call with the right connection bound for `source_key`."""
    connector = connector_for(source_key)

    if connector.name == "snowflake":
        service = SnowflakeService(
            settings, request.app.state.session_store, request.app.state.token_encryptor
        )
        connection = await service.get_connection(session)
        identity = (session.snowflake_user, session.role)

        def as_snowflake():
            with engine_client.use_connection(connection, identity=identity):
                return fn(*args)

        return await asyncio.to_thread(as_snowflake)

    identity = (session.snowflake_user, session.role)

    def bound():
        with source_binding.bind_upload_source(source_key, identity):
            return fn(*args)

    try:
        return await asyncio.to_thread(bound)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


async def require_author(request: Request, session: SessionData, source_key: str) -> None:
    """Refuse anyone who may not write this source's semantic layer.

    A warehouse database keeps its own rule — the layer describes data the
    person's Snowflake role must itself be able to see. Everything else is an
    administrator's to write, because a connection's login is shared by
    everyone and there is no per-person role behind it.
    """
    connector = connector_for(source_key)

    if connector.name != "snowflake":
        if not await session_is_admin(session):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an administrator can write this database's semantic layer.",
            )
        return

    # A Snowflake sign-in that has not opened its connection yet is asked to
    # connect (409), not told it lacks a role it was never checked for.
    session = await snowflake_session(request, session)
    if not await can_author_snowflake(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Saving this needs a Snowflake role listed in ADMIN_ROLES. "
                f"Your active Snowflake role is {session.role or 'not known'}."
            ),
        )
    await require_visible(request, session, source_key)


async def require_visible(request: Request, session: SessionData, source_key: str) -> None:
    """Refuse a Snowflake database this person's role cannot see.

    The list only offers databases the role can see; this closes the gap
    between what the UI shows and what the API accepts, because a name typed
    into a URL was never offered. A layer is worth protecting on its own: it
    spells out every table, column meaning, join rule and metric formula.
    """
    if connector_for(source_key).name != "snowflake":
        return
    service = SnowflakeService(
        settings, request.app.state.session_store, request.app.state.token_encryptor
    )
    visible = await service.visible_databases(session)
    if source_key.upper() not in visible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your Snowflake role does not have access to '{source_key}'.",
        )
