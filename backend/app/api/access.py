"""Data-group access for routes: who is asking, and may they see this.

Every route that hands over a dashboard, report, folder, database or chat asks
here. The rules themselves live in engine/data_groups.py; this module reads
them as the signed-in user and turns a refusal into the same 404 a missing id
gets, so a link to something outside your groups says nothing about whether it
exists.

Lookups that find a resource's chain (which database a dashboard reads, which
folder a dataset sits in) cost a query, so they only run when this person is
actually being filtered.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import get_current_session, session_is_admin
from app.core.config import settings
from app.engine import data_groups
from app.engine.connectors.snowflake import client as engine_client
from app.services.snowflake import SnowflakeService
from app.session.base import SessionData

log = logging.getLogger(__name__)


async def run_as_user(request: Request, session: SessionData, fn: Callable[..., Any],
                      *args: Any) -> Any:
    """A blocking engine call, off the event loop, on this user's connection.
    Needs one: for the lookups that may run without, see _resolve below."""
    service = SnowflakeService(
        settings, request.app.state.session_store, request.app.state.token_encryptor
    )
    connection = await service.get_connection(session)
    identity = (session.snowflake_user, session.role)

    def call():
        with engine_client.use_connection(connection, identity=identity):
            return fn(*args)

    return await asyncio.to_thread(call)


async def get_access(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> data_groups.Access:
    """This person's view of the data-group rules.

    The rules come from the process cache when fresh, so most requests cost no
    query. With enforcement on, rules that cannot be read refuse the request
    (503) rather than letting everything through.
    """
    is_admin = await session_is_admin(session)
    enforced = bool(settings.DATA_GROUPS_ENFORCED)
    required = enforced and not is_admin

    current = data_groups.cached_snapshot()
    if current is None or (required and not current.configured):
        try:
            # The rules are the app's own records now, so reading them needs no
            # warehouse — which is what stopped a warehouse outage refusing
            # every request this guards, including ones serving a SQL Server
            # dashboard or an upload.
            current = await asyncio.to_thread(data_groups.snapshot, required=required)
        except data_groups.AccessRulesUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - no connection to read the rules with
            if required:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Access rules could not be read, so this request was refused.",
                ) from exc
            log.warning("could not read data groups: %s", exc)
            current = data_groups.Snapshot(configured=False)

    return data_groups.Access(
        snapshot=current,
        user=session.snowflake_user,
        role=session.role,
        is_admin=is_admin,
        enforced=enforced,
    )


def not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


# --- chains that need a lookup (run on the user's connection) -----------------

def dataset_folder(dataset_id: str) -> str | None:
    from app.engine import excel_sources

    dataset = excel_sources.get_dataset(dataset_id)
    return (dataset.collection_id or None) if dataset else None


def folder_lookup() -> Callable[[str], str | None]:
    """Dataset id -> folder id for every dataset, read once for a whole list."""
    from app.engine import excel_sources

    mapping = {d.dataset_id: d.collection_id for d in excel_sources.list_datasets()}
    return lambda dataset_id: mapping.get(dataset_id) or None


def dashboard_chain(report_id: str) -> list[data_groups.Link] | None:
    """The chain for one saved dashboard, or None when there is no such dashboard."""
    from app.engine import dashboards, file_dashboards

    rec = file_dashboards.read_record(report_id)
    if rec is not None:
        return data_groups.dashboard_chain(
            report_id, source_key=rec.get("source_key"), dataset_folder=dataset_folder)
    saved = engine_client.read_saved_dashboard(report_id)
    if saved is None:
        return None
    # The same order the dashboards list uses — stored column, title, then
    # SQL — so the list never files a dashboard where opening it disagrees.
    plan = saved.get("DASHBOARD_PLAN") or {}
    database = (_stored_database(report_id)
                or dashboards.infer_database_from_title(saved.get("REPORT_TITLE"))
                or dashboards._infer_database(plan))
    return data_groups.dashboard_chain(report_id, database=database)


def _stored_database(report_id: str) -> str | None:
    """The DATABASE_NAME recorded for a dashboard's current version, if the
    table has that column."""
    if not engine_client._has_group_columns():
        return None
    conn = engine_client.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT DATABASE_NAME FROM {engine_client.SAVED_DASHBOARDS_TABLE} "
            "WHERE REPORT_ID = %s AND IS_CURRENT = TRUE AND IS_ACTIVE = TRUE",
            (report_id,),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
    value = (row[0] if row else None) or ""
    return str(value).strip() or None


def report_chain(report_id: str) -> list[data_groups.Link] | None:
    from app.engine import excel_reports

    rec = excel_reports._read_report(report_id)
    if rec is None:
        return None
    return data_groups.report_chain(
        report_id, excel_reports.report_database(rec.get("PARAMETERIZED_QUERY")))


def conversation_chain(database: str | None, dashboard_id: str | None
                       ) -> list[data_groups.Link]:
    """A chat belongs where its dashboard belongs, else where its source does."""
    if dashboard_id:
        return dashboard_chain(dashboard_id) or [("dashboard", dashboard_id)]
    return data_groups.source_chain(database, dataset_folder)


# --- the same chains, from the app store alone ----------------------------------
#
# Every record the app makes now lives in its own store. These read only that,
# under no_warehouse(), and say NotLocal for anything the store does not hold
# — which can only be a record made before it moved, kept in Snowflake. Who
# may then look it up there is decided in _resolve, never here.

class NotLocal(Exception):
    """The app store does not hold this record."""


def dataset_folder_local(dataset_id: str) -> str | None:
    from app.engine import excel_sources

    dataset = excel_sources.get_dataset(dataset_id)
    if dataset is None:
        raise NotLocal(dataset_id)
    return dataset.collection_id or None


def dashboard_chain_local(report_id: str) -> list[data_groups.Link]:
    from app.engine import file_dashboards

    rec = file_dashboards.read_record(report_id)
    if rec is None:
        raise NotLocal(report_id)
    return data_groups.dashboard_chain(
        report_id, source_key=rec.get("source_key"), dataset_folder=dataset_folder_local)


def report_chain_local(report_id: str) -> list[data_groups.Link]:
    from app.engine import excel_reports

    rec = excel_reports._read_report(report_id)
    if rec is None:
        raise NotLocal(report_id)
    return data_groups.report_chain(
        report_id, excel_reports.report_database(rec.get("PARAMETERIZED_QUERY")))


def conversation_chain_local(database: str | None, dashboard_id: str | None
                             ) -> list[data_groups.Link]:
    if dashboard_id:
        return dashboard_chain_local(dashboard_id)
    return data_groups.source_chain(database, dataset_folder_local)


class _Unresolvable(Exception):
    """Not in the app store, and this person has no Snowflake of their own to
    look further with."""


async def _resolve(request: Request, session: SessionData, local: Callable[..., Any],
                   legacy: Callable[..., Any], *args: Any) -> Any:
    """A resource's chain: from the app store first, for everyone, with no
    Snowflake bound. Only a record the store does not hold is looked up again,
    and only on the person's OWN connection, as before. Someone with no
    Snowflake link gets _Unresolvable, which every check answers with 404:
    nothing that cannot be placed is let through."""
    from app.api.deps import has_snowflake_link
    from app.api.warehouse import run_without_snowflake
    from app.engine.connectors.snowflake.client import WarehouseNotBound

    try:
        return await run_without_snowflake(session, local, *args)
    except (NotLocal, WarehouseNotBound):
        pass
    if not await has_snowflake_link(session):
        raise _Unresolvable()
    return await run_as_user(request, session, legacy, *args)


def local_datasets() -> dict[str, str | None]:
    """Dataset id -> folder id, for every dataset the app store holds."""
    from app.engine import excel_sources

    return {d.dataset_id: d.collection_id or None for d in excel_sources.list_datasets()}


# --- checks -------------------------------------------------------------------

async def require_dashboard(request: Request, session: SessionData,
                            access: data_groups.Access, report_id: str) -> None:
    """404 unless this person may see the dashboard. A dashboard that does not
    exist is left to the route, which already says so."""
    if not access.filtering:
        return
    detail = f"No active saved dashboard with report_id '{report_id}'."
    try:
        chain = await _resolve(request, session, dashboard_chain_local, dashboard_chain,
                               report_id)
    except _Unresolvable:
        raise not_found(detail) from None
    if chain is not None and not access.allows(chain):
        raise not_found(detail)


async def require_report(request: Request, session: SessionData,
                         access: data_groups.Access, report_id: str) -> None:
    if not access.filtering:
        return
    try:
        chain = await _resolve(request, session, report_chain_local, report_chain, report_id)
    except _Unresolvable:
        raise not_found("Report not found.") from None
    if chain is not None and not access.allows(chain):
        raise not_found("Report not found.")


async def require_owned_folder(session: SessionData, collection_id: str) -> None:
    """A folder is visible to its owner, full stop (spec 0012) — not an
    administrator, and not data_groups, which this runs ahead of. A folder
    with no owner on record (made before this existed, or an ambiguous
    backfill) is visible to nobody here; an administrator claims it
    elsewhere."""
    from app.engine import excel_sources

    collection = await asyncio.to_thread(excel_sources.get_collection, collection_id)
    if collection is None or not collection.owner or collection.owner != session.snowflake_user:
        raise not_found("No such folder.")


async def require_folder(session: SessionData, access: data_groups.Access,
                         collection_id: str) -> None:
    await require_owned_folder(session, collection_id)
    if access.filtering and not access.allows([("folder", collection_id)]):
        raise not_found("No such folder.")


async def require_dataset(request: Request, session: SessionData,
                          access: data_groups.Access, dataset_id: str) -> None:
    from app.engine import excel_sources

    dataset = await asyncio.to_thread(excel_sources.get_dataset, dataset_id)
    if dataset is None or not dataset.owner or dataset.owner != session.snowflake_user:
        raise not_found("No such dataset.")
    if not access.filtering:
        return
    try:
        folder = await _resolve(request, session, dataset_folder_local, dataset_folder,
                                dataset_id)
    except _Unresolvable:
        raise not_found("No such dataset.") from None
    if folder and not access.allows([("folder", folder)]):
        raise not_found("No such dataset.")


async def require_source(request: Request, session: SessionData,
                         access: data_groups.Access, database: str | None,
                         dashboard_id: str | None = None,
                         detail: str = "Not found") -> None:
    """For a chat: its dashboard if it has one, else its database or upload."""
    if not access.filtering:
        return
    try:
        chain = await _resolve(request, session, conversation_chain_local,
                               conversation_chain, database, dashboard_id)
    except _Unresolvable:
        raise not_found(detail) from None
    if not access.allows(chain):
        raise not_found(detail)


# --- route dependencies -------------------------------------------------------

async def dashboard_access(
    report_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> data_groups.Access:
    """For a route under /dashboards/{report_id}: refuses a dashboard outside
    this person's groups before the route runs."""
    await require_dashboard(request, session, access, report_id)
    return access


async def report_access(
    report_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> data_groups.Access:
    await require_report(request, session, access, report_id)
    return access


async def dataset_access(
    dataset_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> data_groups.Access:
    await require_dataset(request, session, access, dataset_id)
    return access


async def folder_access(
    collection_id: str,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> data_groups.Access:
    await require_folder(session, access, collection_id)
    return access


async def database_access(
    database: str,
    access: data_groups.Access = Depends(get_access),
) -> data_groups.Access:
    if access.filtering and not access.allows([("database", database)]):
        raise not_found(f"Database '{database}' not found.")
    return access
