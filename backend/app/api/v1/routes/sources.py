"""The databases a person can work on, from every connector.

One list, asked for by anything that has to offer a choice of database: the
semantic layer editor, the Report Builder, and whatever comes next. Scoped to
a workspace, because a warehouse's databases and a SQL Server connection's are
separate places.

The rules live in app/api/sources.py; this is only the way in over HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api import sources
from app.api.deps import get_current_session
from app.session.base import SessionData

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceOut(BaseModel):
    key: str
    display_name: str
    connector: str
    #: Whether this person may author against it — the rule differs per
    #: connector, so the caller should not work it out for itself.
    can_edit: bool = False


@router.get("", response_model=list[SourceOut])
async def list_sources(
    request: Request,
    session: SessionData = Depends(get_current_session),
    connector: str | None = None,
    connection: str | None = None,
) -> list[SourceOut]:
    """Every database this person can work on, narrowed to one workspace when
    `connector` and `connection` say so."""
    listed = await sources.list_sources(
        request, session, connector=connector, connection_id=connection
    )
    out: list[SourceOut] = []
    for source in listed:
        out.append(SourceOut(
            key=source.key,
            display_name=source.display_name,
            connector=source.connector,
            can_edit=await _may_author(request, session, source.key),
        ))
    return out


async def _may_author(request: Request, session: SessionData, source_key: str) -> bool:
    try:
        await sources.require_author(request, session, source_key)
    except Exception:  # noqa: BLE001 - any refusal means "not this person"
        return False
    return True
