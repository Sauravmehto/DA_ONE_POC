"""Connections to databases other than Snowflake: list, test, add, remove.

Adding, testing and removing are for administrators, because a connection's
login is shared by everyone who uses it. Listing is open to any signed-in user,
since the chat's source picker is built from it. No route ever returns a
password, and none needs Snowflake: connections are kept in the app's own
file.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.access import get_access
from app.api.deps import get_current_session, require_admin
from app.core.config import settings
from app.engine import db_connections
from app.engine.data_groups import Access
from app.engine.connectors.snowflake import client as engine_client
from app.engine.connectors.sqlserver import client as sqlserver_client
from app.services.snowflake import SnowflakeNotLinked, SnowflakeService
from app.session.base import SessionData

log = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["connections"])


async def _copy_from_snowflake(request: Request, session: SessionData) -> None:
    """Copy connections saved in Snowflake before they were kept here, the
    first time someone with Snowflake connected asks for the list. Never
    stops the list: a failure only means the copy is tried again after a
    restart."""
    if not db_connections.import_needed():
        return
    service = SnowflakeService(
        settings, request.app.state.session_store, request.app.state.token_encryptor
    )
    identity = (session.snowflake_user, session.role)
    try:
        # Reconnects a linked account whose Snowflake session is not open yet.
        connection = await service.get_connection(session)

        def call():
            with engine_client.use_connection(connection, identity=identity):
                return db_connections.import_from_snowflake(session.snowflake_user)

        await asyncio.to_thread(call)
    except SnowflakeNotLinked:
        return
    except Exception:  # noqa: BLE001
        log.warning("Could not copy saved connections from Snowflake", exc_info=True)


def _server_error(exc: Exception) -> str:
    """A SQL Server failure as one readable sentence.

    pymssql reports errors as a tuple whose second item is the server's bytes,
    followed by FreeTDS's own lines; the first line is the one a person needs.
    """
    args = getattr(exc, "args", ())
    if len(args) >= 2 and isinstance(args[1], (bytes, bytearray)):
        text = args[1].decode("utf-8", errors="replace")
    else:
        text = str(exc)
    first = text.split("DB-Lib error")[0].strip() or text.strip()
    if "Adaptive Server connection failed" in text or "Unable to connect" in text:
        return "Could not reach the server. Check the host and port, and that the backend can reach it."
    return first.splitlines()[0][:300] if first else "The server refused the connection."


class ConnectionInput(BaseModel):
    name: str = Field(default="", max_length=120)
    host: str
    port: int = db_connections.DEFAULT_PORT
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


def _params(body: ConnectionInput) -> sqlserver_client.ServerParams:
    try:
        host = db_connections.check_host(body.host)
        port = db_connections.check_port(body.port)
    except db_connections.ConnectionSetupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return sqlserver_client.ServerParams(host, port, body.username.strip(), body.password)


def _probe(params: sqlserver_client.ServerParams) -> dict[str, Any]:
    return {
        "server_version": sqlserver_client.server_version(params),
        "databases": sqlserver_client.list_databases(params),
    }


async def _probe_or_400(params: sqlserver_client.ServerParams) -> dict[str, Any]:
    try:
        # Bounded, because nothing else bounds it. The driver's login timeout
        # does not cover every stage of a login, so a server that never
        # finishes accepting one left the dialog spinning with nothing to read
        # and no way to tell whether it had saved.
        return await asyncio.wait_for(
            asyncio.to_thread(_probe, params),
            timeout=db_connections.SERVER_DEADLINE_SECONDS,
        )
    except TimeoutError as exc:      # before Exception: TimeoutError is one
        log.info("SQL Server connection test to %s did not finish in %ss",
                 params.host, db_connections.SERVER_DEADLINE_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"The server did not answer within "
                f"{int(db_connections.SERVER_DEADLINE_SECONDS)} seconds. "
                "Nothing was saved. Check the host and port are reachable "
                "from the backend, then try again."
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the form
        log.info("SQL Server connection test to %s failed: %s", params.host, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_server_error(exc)
        ) from exc


@router.get("")
async def list_connections(
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: Access = Depends(get_access),
) -> dict[str, Any]:
    """Saved connections, each with the databases its login can open.

    A connection that cannot be reached right now is still listed, with the
    reason, so it can be seen and removed rather than silently disappearing.

    Databases this person's groups do not reach are left out, the same way the
    warehouse's are. Without this a group could hold a SQL Server database and
    still not hide it, which is a rule that looks enforced and is not.
    """
    await _copy_from_snowflake(request, session)
    try:
        saved = await asyncio.to_thread(db_connections.list_connections)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read saved connections", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not read saved connections: {exc}",
        ) from exc

    async def describe(connection: db_connections.SavedConnection) -> dict[str, Any]:
        item = connection.public()
        try:
            # A deadline of our own: the driver's login timeout does not cover
            # every stage of a login, and a server slow to accept one held this
            # page for minutes. The listing carries on in the background and
            # fills the cache, so a later visit gets the answer at once.
            item["databases"] = await asyncio.wait_for(
                asyncio.to_thread(db_connections.databases_for, connection.connection_id),
                timeout=db_connections.SERVER_DEADLINE_SECONDS,
            )
            item["error"] = None
        except TimeoutError:
            # Still connecting, not failed: the listing runs on in its thread
            # and fills the cache, so the next look at this page is served at
            # once. Saying "it may be busy" sent people to look at a server
            # that was answering fine.
            item["databases"], item["error"] = [], (
                "Still connecting to this server. Its databases will appear "
                "shortly."
            )
        except db_connections.ConnectionSetupError as exc:
            item["databases"], item["error"] = [], str(exc)
        except Exception as exc:  # noqa: BLE001
            item["databases"], item["error"] = [], _server_error(exc)
        return item

    items = await asyncio.gather(*(describe(c) for c in saved))
    for item in items:
        item["databases"] = [
            database for database in item["databases"]
            if access.allows([("database",
                               db_connections.source_key(item["connection_id"], database))])
        ]
    return {
        "connections": list(items),
        "allowed_hosts": db_connections.allowed_hosts(),
    }


@router.post("/test")
async def test_connection(
    body: ConnectionInput,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    """Log in with these details and list the databases, saving nothing."""
    return {"ok": True, **await _probe_or_400(_params(body))}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionInput,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    """Save a connection. Tested first: one that cannot log in is not saved."""
    params = _params(body)
    probe = await _probe_or_400(params)
    name = body.name.strip() or f"SQL Server ({params.host})"
    try:
        saved = await asyncio.to_thread(
            db_connections.create_connection, name, params, session.snowflake_user
        )
    except db_connections.ConnectionSetupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    databases = probe.get("databases") or []
    # Testing the connection just listed these. Keep them, or the page that
    # reloads as the dialog closes pays for another login to learn the same
    # thing, and the connection it has just saved is missing until it does.
    db_connections.remember_databases(saved.connection_id, databases)
    # Read what each database holds now, in the background, so the first
    # question asked of this server does not wait for a login.
    db_connections.read_catalogues_soon(saved.connection_id, databases)
    return {**saved.public(), **probe, "error": None}


@router.delete("/{connection_id}")
async def delete_connection(
    connection_id: str,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    removed = await asyncio.to_thread(db_connections.delete_connection, connection_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such connection.")
    return {"connection_id": connection_id, "deleted": True}
