"""Which Snowflake, if any, an engine call runs with.

One rule for the whole web app (spec 0007): engine code runs on the signed-in
person's OWN Snowflake connection, or with no Snowflake at all. Never on the
engine's shared service-account connection, which the app refuses at startup.

- `snowflake_binding(..., required=True)`: the work is Snowflake's (a query on
  a warehouse database). No connection is a 409 "connect Snowflake".
- `snowflake_binding(...)`: the work is the app's own (uploads, SQL Server,
  records). The person's connection is bound if they have connected one, so
  older records kept in Snowflake are still found and audit rows are written
  as them; otherwise nothing is, and engine code that reaches for Snowflake
  gets WarehouseNotBound before any connection is opened.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from fastapi import Request

from app.api.deps import has_snowflake_link
from app.core.config import settings
from app.engine.connectors.snowflake import client as engine_client
from app.services.snowflake import SnowflakeNotLinked, SnowflakeService
from app.session.base import SessionData

Binding = Callable[[], AbstractContextManager]


def _identity(session: SessionData) -> tuple[str | None, str | None]:
    return (session.snowflake_user, session.role)


def without_snowflake(session: SessionData) -> Binding:
    identity = _identity(session)
    return lambda: engine_client.no_warehouse(identity)


async def snowflake_binding(request: Request, session: SessionData, *,
                            required: bool = False) -> Binding:
    """A context-manager factory binding this person's Snowflake, or none.

    A factory rather than a context manager, because what it binds is a
    ContextVar: it must be entered in the thread or generator that runs the
    engine code, not here."""
    identity = _identity(session)
    if await has_snowflake_link(session):
        service = SnowflakeService(
            settings, request.app.state.session_store, request.app.state.token_encryptor
        )
        try:
            connection = await service.get_connection(session)
        except SnowflakeNotLinked:
            # Linked once, and that link no longer works. Snowflake's own work
            # says so; the app's own work carries on without it.
            if required:
                raise
            return without_snowflake(session)
        return lambda: engine_client.use_connection(connection, identity=identity)
    if required:
        raise SnowflakeNotLinked()
    return without_snowflake(session)


async def run_bound(request: Request, session: SessionData, fn: Callable[..., Any],
                    *args: Any, required: bool = False, **kwargs: Any) -> Any:
    """A blocking engine call, off the event loop, bound as above."""
    binding = await snowflake_binding(request, session, required=required)

    def call():
        with binding():
            return fn(*args, **kwargs)

    return await asyncio.to_thread(call)


async def run_without_snowflake(session: SessionData, fn: Callable[..., Any],
                                *args: Any, **kwargs: Any) -> Any:
    """A blocking engine call that must stay local, whoever is asking."""
    binding = without_snowflake(session)

    def call():
        with binding():
            return fn(*args, **kwargs)

    return await asyncio.to_thread(call)
