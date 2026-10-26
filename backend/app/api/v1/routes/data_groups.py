"""Data groups: create them, add people, and file databases, folders,
dashboards and reports under them. Administrators only, apart from /mine.

The rules and how they apply live in engine/data_groups.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.access import get_access
from app.api.deps import get_current_session, require_admin
from app.api.warehouse import run_bound
from app.core.config import settings
from app.engine import data_groups
from app.session.base import SessionData

log = logging.getLogger(__name__)

router = APIRouter(prefix="/data-groups", tags=["data-groups"])

SETUP_HINT = (
    "Data groups are not set up. Run backend/sql/data_groups.sql as the owner of "
    "the schema, then grant the application roles access to the three tables."
)


class GroupBody(BaseModel):
    name: str | None = None
    description: str | None = None


class MemberBody(BaseModel):
    member_type: str
    member_name: str


class ResourceBody(BaseModel):
    resource_type: str
    resource_id: str
    is_main: bool = False


async def _write(request: Request, session: SessionData, fn: Callable[[], Any]) -> Any:
    """Run an admin change off the event loop, mapping refusals.

    No warehouse connection: the rules are the app's own records, so an
    administrator of a deployment that has none can still manage groups."""
    try:
        return await asyncio.to_thread(fn)
    except data_groups.DataGroupError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except data_groups.AccessRulesUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=SETUP_HINT) from exc


def _catalog() -> dict[str, list[dict[str, str]]]:
    """Everything that can be filed under a group, with the name people know it by.

    Runs on the admin's connection. Each source is read separately so one that
    fails (no upload tables yet, say) leaves the others offered.
    """
    from app.engine import (dashboards, db_connections, excel_reports, excel_sources,
                            file_dashboards)
    from app.engine.metadata_loader import metadata_store

    out: dict[str, list[dict[str, str]]] = {t: [] for t in data_groups.RESOURCE_TYPES}

    def attempt(kind: str, read: Callable[[], list[dict[str, str]]]) -> None:
        try:
            out[kind] = read()
        except Exception as exc:  # noqa: BLE001 - one missing source must not empty the page
            log.warning("could not list %s for data groups: %s", kind, exc)

    def databases() -> list[dict[str, str]]:
        """Every database this deployment can reach, whatever serves it.

        A group that cannot hold a SQL Server database cannot govern its
        dashboards either — they would fall through to ungrouped one by one,
        and an administrator would be filing artefacts instead of sources,
        which is the thing this model exists to avoid."""
        items = []
        for name in sorted(metadata_store.documented_databases()):
            if name.upper() == "_DEFAULT":
                continue        # a fallback config, not a database
            label = name
            try:
                from app.metadata.loader import metadata_service

                label = metadata_service.describe_database(name).get("display_name") or name
            except Exception:  # noqa: BLE001 - the plain name is a fine label
                pass
            items.append({"id": name.upper(), "label": label})

        for connection in db_connections.list_connections():
            try:
                found = db_connections.databases_for(connection.connection_id)
            except Exception as exc:  # noqa: BLE001 - one server that will not answer
                log.warning("could not list databases on %s for data groups: %s",
                            connection.name, exc)
                continue
            for database in found:
                items.append({
                    "id": db_connections.source_key(
                        connection.connection_id, database).upper(),
                    "label": f"{database} ({connection.name})",
                })
        return items

    def folders() -> list[dict[str, str]]:
        return [{"id": c.collection_id, "label": c.name}
                for c in excel_sources.list_collections()]

    def dashboard_items() -> list[dict[str, str]]:
        items = [{"id": d["report_id"], "label": d.get("report_title") or d["report_id"]}
                 for d in dashboards.list_dashboards(500)]
        items += [{"id": d["report_id"], "label": d.get("report_title") or d["report_id"]}
                  for d in file_dashboards.list_dashboards(limit=500)]
        return sorted(items, key=lambda i: i["label"].lower())

    def reports() -> list[dict[str, str]]:
        return [{"id": r["report_id"], "label": r.get("report_title") or r["report_id"]}
                for r in excel_reports.list_reports(500)]

    attempt("database", databases)
    attempt("folder", folders)
    attempt("dashboard", dashboard_items)
    attempt("report", reports)
    return out


@router.get("/mine")
async def my_groups(
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> dict[str, Any]:
    """The groups the signed-in person is in, and whether they are enforced."""
    rules = access.snapshot
    mine = access.my_groups
    return {
        "configured": rules.configured,
        "enforced": access.enforced,
        "is_admin": access.is_admin,
        "groups": [{"group_id": g.group_id, "name": g.name}
                   for g in rules.groups if g.group_id in mine],
    }


@router.get("")
async def list_groups(
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    """Every group with its members and what is filed under it."""
    try:
        rules = await asyncio.to_thread(data_groups.snapshot, required=True)
    except data_groups.AccessRulesUnavailable:
        return {"configured": False, "enforced": settings.DATA_GROUPS_ENFORCED,
                "setup_hint": SETUP_HINT, "groups": []}
    return {
        "configured": True,
        "enforced": settings.DATA_GROUPS_ENFORCED,
        "groups": data_groups.describe(rules),
    }


@router.get("/catalog")
async def catalog(
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    """What can be filed under a group, by type. An administrator who has not
    connected Snowflake still gets every SQL Server database, folder and
    app-store record; only what lives in Snowflake is missing (spec 0007)."""
    return await run_bound(request, session, _catalog)


@router.post("")
async def create_group(
    body: GroupBody,
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    group = await _write(request, session, lambda: data_groups.create_group(
        body.name or "", body.description or "", actor=session.snowflake_user))
    return {"group_id": group.group_id, "name": group.name, "description": group.description}


@router.patch("/{group_id}")
async def update_group(
    group_id: str,
    body: GroupBody,
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    group = await _write(request, session, lambda: data_groups.update_group(
        group_id, name=body.name, description=body.description,
        actor=session.snowflake_user))
    return {"group_id": group.group_id, "name": group.name, "description": group.description}


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    await _write(request, session, lambda: data_groups.delete_group(group_id))
    return {"group_id": group_id, "deleted": True}


@router.post("/{group_id}/members")
async def add_member(
    group_id: str,
    body: MemberBody,
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    member = await _write(request, session, lambda: data_groups.add_member(
        group_id, body.member_type, body.member_name, actor=session.snowflake_user))
    return {"group_id": group_id, "member_type": member.member_type,
            "member_name": member.member_name}


@router.delete("/{group_id}/members/{member_type}/{member_name}")
async def remove_member(
    group_id: str,
    member_type: str,
    member_name: str,
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    await _write(request, session, lambda: data_groups.remove_member(
        group_id, member_type, member_name))
    return {"group_id": group_id, "removed": True}


@router.post("/{group_id}/resources")
async def assign_resource(
    group_id: str,
    body: ResourceBody,
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    assignment = await _write(request, session, lambda: data_groups.assign(
        group_id, body.resource_type, body.resource_id, is_main=body.is_main,
        actor=session.snowflake_user))
    return {"group_id": group_id, "resource_type": assignment.resource_type,
            "resource_id": assignment.resource_id, "is_main": assignment.is_main}


@router.delete("/{group_id}/resources/{resource_type}/{resource_id:path}")
async def unassign_resource(
    group_id: str,
    resource_type: str,
    resource_id: str,
    request: Request,
    session: SessionData = Depends(require_admin),
) -> dict[str, Any]:
    await _write(request, session, lambda: data_groups.unassign(
        group_id, resource_type, resource_id))
    return {"group_id": group_id, "removed": True}
