"""Streaming agent endpoint.

Added alongside the legacy POST /chat rather than replacing it, so the current
UI keeps working until the frontend moves over.
"""

import asyncio
import contextlib
import logging
import secrets

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.dashboard_context import build_dashboard_context
from app.agent.loop import Event, format_sse, run_turn
from app.agent.prompts import build_dashboard_system_prompt
from app.agent.tools import ToolContext, tool_definitions
from app.api import access as access_checks
from app.api.access import get_access
from app.api.deps import count_trial_message, get_current_session, has_snowflake_link
from app.api.warehouse import run_without_snowflake, snowflake_binding
from app.conversation import Conversation, ConversationKind, ConversationSummary
from app.core.config import settings
from app.engine import dashboards as engine_dashboards
from app.engine import data_groups
from app.engine import file_dashboards as engine_file_dashboards
from app.engine import source_binding
from app.engine import collection_query
from app.engine import db_connections
from app.engine import excel_sources as engine_sources
from app.engine.connectors.duckdb import client as duckdb_client
from app.engine.connectors.snowflake import client as engine_client
from app.engine.connectors.sqlserver import client as sqlserver_client

#: How an uploaded spreadsheet is named in the `database` slot a conversation
#: already carries, so a thread against a workbook needs no new plumbing.
EXCEL_SOURCE_PREFIX = "EXCEL:"
#: A whole uploaded folder: its spreadsheet tables and its documents at
#: once, so a question can span them.
COLLECTION_PREFIX = "FOLDER:"
#: A database on a saved SQL Server connection: "SQLSERVER:<connection>/<db>".
SQLSERVER_PREFIX = db_connections.SOURCE_PREFIX

#: Source names that carry an id or a database name whose case must be kept.
_SOURCE_PREFIXES = (EXCEL_SOURCE_PREFIX, COLLECTION_PREFIX, SQLSERVER_PREFIX)


def _load_collection(collection_id: str):
    """The folder behind a FOLDER: source name, if it still exists."""
    for candidate in engine_sources.list_collections():
        if candidate.collection_id == collection_id:
            return candidate
    return None


def _normalise_source(database: str | None) -> str | None:
    """Uppercase a warehouse name, but leave a source id alone.

    Snowflake database names are case-insensitive and stored uppercase, so the
    old unconditional .upper() was right for them. It is wrong for
    "EXCEL:<id>" — the id is lowercase hex, and uppercasing it turns a valid
    source into one that cannot be found.
    """
    if not database:
        return None
    for prefix in _SOURCE_PREFIXES:
        if database.upper().startswith(prefix):
            return prefix + database.split(":", 1)[1]
    return database.upper()
from app.engine.metadata_loader import metadata_store
from app.services.snowflake import SnowflakeService
from app.session.base import SessionData

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRequest(BaseModel):
    message: str
    database: str | None = None          # required on the first turn only
    conversation_id: str | None = None   # omitted starts a new conversation
    # Scopes a NEW conversation to one saved dashboard: the prompt is built
    # around that dashboard and the mutating tools are withheld. Ignored when
    # conversation_id is given — the scope is fixed when the thread starts.
    dashboard_id: str | None = None


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    request: Request,
    dashboard_id: str | None = None,
    kind: ConversationKind = "chat",
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> list[ConversationSummary]:
    """Conversations for this user.

    Without `dashboard_id` this is the sidebar's list and dashboard-scoped
    threads are EXCLUDED — they belong to their dashboard, and a few questions
    per dashboard would otherwise bury the main threads. Pass a dashboard_id to
    get exactly that dashboard's threads instead.

    `kind` separates a dashboard's Ask threads ("chat", the default) from its
    Edit-panel assistant threads ("dashboard_edit"), so neither panel reopens
    the other's conversation.
    """
    summaries = await request.app.state.conversation_store.list_for_owner(
        session.snowflake_user
    )
    summaries = [s for s in summaries if s.kind == kind]
    if dashboard_id:
        await access_checks.require_dashboard(request, session, access, dashboard_id)
        return [s for s in summaries if s.dashboard_id == dashboard_id]
    summaries = [s for s in summaries if not s.dashboard_id]
    if not access.filtering:
        return summaries
    # A chat on a source outside this person's groups is hidden with it.
    dataset_folder = None
    if any((s.database or "").upper().startswith(data_groups.EXCEL_PREFIX) for s in summaries):
        if await has_snowflake_link(session):
            dataset_folder = await access_checks.run_as_user(
                request, session, access_checks.folder_lookup)
        else:
            # The app store alone. A chat on an upload it does not hold cannot
            # be placed in a group, so it is hidden rather than shown.
            known = await run_without_snowflake(session, access_checks.local_datasets)
            prefix = len(data_groups.EXCEL_PREFIX)
            summaries = [
                s for s in summaries
                if not (s.database or "").upper().startswith(data_groups.EXCEL_PREFIX)
                or (s.database or "")[prefix:] in known
            ]
            dataset_folder = known.get
    return [
        s for s in summaries
        if access.allows(data_groups.source_chain(s.database, dataset_folder))
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> Conversation:
    conversation = await request.app.state.conversation_store.get(conversation_id)
    if conversation is None or conversation.owner != session.snowflake_user:
        # Same response either way: a wrong owner must not be distinguishable
        # from a missing id, or the endpoint leaks which ids exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Conversation not found")
    await access_checks.require_source(request, session, access, conversation.database,
                                       conversation.dashboard_id,
                                       detail="Conversation not found")
    return conversation


class ConversationUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> ConversationSummary:
    """Rename or pin/unpin a conversation."""
    conversation = await request.app.state.conversation_store.get(conversation_id)
    if conversation is None or conversation.owner != session.snowflake_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Conversation not found")
    await access_checks.require_source(request, session, access, conversation.database,
                                       conversation.dashboard_id,
                                       detail="Conversation not found")
    if body.title is not None:
        conversation.title = body.title.strip() or None
    if body.pinned is not None:
        conversation.pinned = body.pinned
    conversation.updated_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    await request.app.state.conversation_store.save(
        conversation, settings.SESSION_TTL_SECONDS
    )
    return ConversationSummary(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        database=conversation.database,
        dashboard_id=conversation.dashboard_id,
        kind=conversation.kind,
        pinned=conversation.pinned,
        message_count=len(conversation.messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> None:
    conversation = await request.app.state.conversation_store.get(conversation_id)
    if conversation is not None and conversation.owner == session.snowflake_user:
        await request.app.state.conversation_store.delete(conversation_id)


@router.post("/chat")
async def agent_chat(
    body: AgentRequest,
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> StreamingResponse:
    """Run one turn, streaming events over SSE."""
    if not settings.ai_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="AI is not configured (missing ANTHROPIC_API_KEY).")

    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Message is empty.")

    await count_trial_message(session)

    store = request.app.state.conversation_store

    if body.conversation_id:
        conversation = await store.get(body.conversation_id)
        if conversation is None or conversation.owner != session.snowflake_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Conversation not found")
        if conversation.kind != "chat":
            # An Edit-panel thread holds plain-text turns and belongs to the
            # edit assistant; the agent loop resuming it would hand those turns
            # the full tool set.
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="That conversation belongs to a dashboard's edit assistant.")
        dashboard_id = conversation.dashboard_id
    else:
        dashboard_id = body.dashboard_id
        # A dashboard-scoped thread takes its database from the dashboard's own
        # plan, so the client never has to know or send it.
        if not dashboard_id and not body.database:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="database is required to start a conversation.")
        conversation = Conversation(
            conversation_id=secrets.token_urlsafe(16),
            owner=session.snowflake_user,
            database=_normalise_source(body.database),
            dashboard_id=dashboard_id,
            # First message doubles as the title until something better exists.
            title=message[:80],
        )

    # Checked on every turn, not only the first: a person moved out of a group
    # loses the thread along with the source it reads.
    await access_checks.require_source(
        request, session, access, conversation.database, dashboard_id,
        detail="Conversation not found" if body.conversation_id
        else "That source is not available to you.",
    )

    snowflake_service = SnowflakeService(
        settings, request.app.state.session_store, request.app.state.token_encryptor
    )
    # Bind THIS user's connection for the engine's duration (see the contextvar
    # seam in the engine's Snowflake client): engine queries then run as the
    # signed-in user, and never over the shared service-account connection.
    # Someone who has not connected Snowflake gets no warehouse at all, which
    # is all a SQL Server or upload turn needs; a Snowflake turn asks for the
    # connection itself below, and gets 409 without one.
    snowflake = await snowflake_binding(request, session)
    identity = (session.snowflake_user, session.role)

    dashboard_config: dict | None = None
    if dashboard_id:
        # Metadata only — this reads the saved plan and runs no panel SQL, so
        # asking a question does not count as a run of the dashboard. Re-read
        # every turn rather than cached on the conversation: a dashboard edited
        # mid-thread must not keep being described by its old panels.
        def _read_config():
            if engine_file_dashboards.served_here(dashboard_id):
                return engine_file_dashboards.get_dashboard_config(dashboard_id)
            with snowflake():
                return engine_dashboards.get_dashboard_config(dashboard_id)

        try:
            dashboard_config = await asyncio.to_thread(_read_config)
        except engine_dashboards.DashboardError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=str(exc)) from exc

        # The plan is the authority on which database this dashboard reads.
        conversation.database = (
            dashboard_config.get("source_key")
            or dashboard_config.get("database")
            or ""
        )
        if conversation.database and not conversation.database.upper().startswith(
            _SOURCE_PREFIXES
        ):
            conversation.database = conversation.database.upper()

    database = conversation.database or ""

    # An uploaded spreadsheet is addressed as "EXCEL:<source_id>". It has no
    # curated semantic layer and no Snowflake grant behind it, so both checks
    # below are answered differently: its meaning comes from the definitions
    # agreed for its shape, and its access from who uploaded it.
    excel_source = None
    collection = None
    sqlserver_source = None
    if database.upper().startswith(SQLSERVER_PREFIX):
        # A database on a saved SQL Server connection. Like an upload it has no
        # Snowflake grant behind it: the saved login decides what it can read.
        try:
            sqlserver_source = await asyncio.to_thread(db_connections.resolve_source, database)
        except db_connections.ConnectionSetupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=str(exc)) from exc
        conversation.database = database = sqlserver_source.key
    elif database.upper().startswith(COLLECTION_PREFIX):
        collection_id = database.split(":", 1)[1]

        def _resolve_collection():
            with snowflake():
                return _load_collection(collection_id)

        collection = await asyncio.to_thread(_resolve_collection)
        if collection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That folder is not available to you.",
            )
    elif database.upper().startswith(EXCEL_SOURCE_PREFIX):
        source_id = database.split(":", 1)[1]

        def _resolve_dataset():
            with snowflake():
                return engine_sources.get_dataset(source_id)

        excel_source = await asyncio.to_thread(_resolve_dataset)
        if excel_source is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That spreadsheet is not available to you.",
            )
    elif dashboard_id and engine_file_dashboards.served_here(dashboard_id):
        # File-dashboard scoped chat: bind the upload source from the saved plan.
        pass
    elif database not in metadata_store.documented_databases():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No curated context for database '{database}'.",
        )

    # A semantic layer exists for every documented database regardless of who
    # is asking, so "documented" alone is not permission to use one. Check the
    # database against what this user's role can actually see.
    #
    # Snowflake would refuse the queries anyway — the reason to check here is
    # the semantic layer itself. It goes into the model's context and spells
    # out every table, what each column means, the join rules and the exact SQL
    # for each business metric. Answering "what's in this database?" from that
    # block needs no query at all, so without this check the layer is readable
    # by anyone who can name the database.
    #
    # Only on the first turn: the database is locked for the life of a thread,
    # so re-checking each turn would add a round trip per message to re-answer
    # a settled question.
    warehouse_turn = (
        excel_source is None
        and collection is None
        and sqlserver_source is None
        and not (dashboard_id and engine_file_dashboards.served_here(dashboard_id))
    )
    # A turn on a warehouse database is Snowflake's own work: it needs this
    # person's connection, and someone who has not connected one is told so
    # (409) before anything is attempted on their behalf.
    warehouse = (await snowflake_binding(request, session, required=True)
                 if warehouse_turn else None)
    if not body.conversation_id and warehouse_turn:
        visible = await snowflake_service.visible_databases(session)
        if database.upper() not in visible:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Your Snowflake role does not have access to '{database}'."
                ),
            )

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    ctx = ToolContext(
        database=database,
        snowflake_user=session.snowflake_user,
        role=session.role,
        excel_source=excel_source,
        collection=collection,
        sqlserver=sqlserver_source,
        access=access,
    )

    if dashboard_config is not None:
        tools = tool_definitions(read_only=True)
    else:
        tools = None

    # Which connector the engine talks to for the duration of this turn. Both
    # bind the same way — the Snowflake one binds this user's warehouse session,
    # the DuckDB one binds this worker's cached copy of the uploaded file —
    # so nothing downstream has to know which it got.
    #
    # Made here, entered inside the stream: what binds a connection is a
    # ContextVar, and one set in this request cannot be reset from the
    # generator, which starlette runs in a context of its own.
    if sqlserver_source is not None:
        # Both bound: queries go to SQL Server, while the audit trail and the
        # saved-dashboard lookups still write to Snowflake as this user.
        bindings = [
            snowflake(),
            sqlserver_client.use_connection(sqlserver_source.params, identity=identity),
        ]
    elif collection is not None:
        # A folder binds every source in it at once: each dataset's database
        # plus the documents, attached read-only under one connection.
        plan = await asyncio.to_thread(collection_query.plan_for, collection)
        sources, views = plan.as_binding()
        bindings = [duckdb_client.use_collection(
            sources, views, documents=plan.documents, identity=identity
        )]
    elif excel_source is not None:
        database_path = await asyncio.to_thread(
            engine_sources.local_database, excel_source
        )
        bindings = [duckdb_client.use_connection(database_path, identity=identity)]
    elif warehouse is None:
        bindings = [snowflake()]        # a file dashboard's own chat
    else:
        bindings = [warehouse()]

    async def stream():
        try:
            system = None   # run_turn builds the main-chat prompt
            if sqlserver_source is not None:
                # Reading a SQL Server's tables and columns means logging in,
                # and a busy server can take half a minute over it. Said out
                # loud, because the alternative is a chat that looks stuck.
                yield format_sse(Event("status", {
                    "message": f"Reading {sqlserver_source.database}…"
                }))
                try:
                    await asyncio.to_thread(sqlserver_source.context)
                except Exception as exc:  # noqa: BLE001 - pymssql errors share no base worth naming
                    log.warning("Could not read %s", sqlserver_source.key, exc_info=True)
                    yield format_sse(Event("error", {
                        "message": (
                            f"Could not read the SQL Server database "
                            f"'{sqlserver_source.database}': {exc}"
                        )
                    }))
                    return
            if dashboard_config is not None:
                # A SQL Server database has no curated layer; its catalogue
                # description stands in. It is cached by the read above.
                override = (
                    await asyncio.to_thread(sqlserver_source.context)
                    if sqlserver_source is not None else None
                )
                system = build_dashboard_system_prompt(
                    database, build_dashboard_context(dashboard_config), override
                )
            with contextlib.ExitStack() as bound:
                for binding in bindings:
                    bound.enter_context(binding)
                async for event in run_turn(
                    client=client,
                    settings=settings,
                    conversation=conversation,
                    user_message=message,
                    ctx=ctx,
                    system=system,
                    tools=tools,
                ):
                    yield format_sse(event)
        finally:
            # Persist whatever happened, including a turn that failed midway —
            # losing the exchange would be worse than storing a partial one.
            await store.save(conversation, settings.CONVERSATION_TTL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let a proxy buffer the stream
        },
    )
