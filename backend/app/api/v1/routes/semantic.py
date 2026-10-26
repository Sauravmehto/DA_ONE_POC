"""Admin API for authoring semantic layers.

Separate router from /databases, which is the read-only view the chat and
Explore surfaces use. This one writes, so it is namespaced under /admin and is
the only place in the API that can change what the model believes a database
means.

The list endpoint deliberately includes databases with no layer at all: those
are exactly the ones an admin needs to find, and they are invisible in chat
today because the agent endpoint rejects any database that is not documented.

Every connector's databases are offered, not just the warehouse's: a layer is
what turns a bare table listing into something the model can reason about, and
that is worth as much for a SQL Server database as for a Snowflake one. Which
connector serves a source, how its schema is read and who may write its layer
are all asked of app.api.sources.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api import sources
from app.api.deps import can_author_snowflake, get_current_session, session_is_admin
from app.semantic import (
    AppStoreRepository,
    DocumentNotFoundError,
    Issue,
    SemanticDocument,
    SemanticLayerService,
    ValidationFailedError,
    validate_document,
)
from app.services.explore import InvalidIdentifierError
from app.session.base import SessionData

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/semantic", tags=["semantic-admin"])


def _service(published_by: str | None = None) -> SemanticLayerService:
    """The layer service, writing to the app's own store.

    `published_by` is recorded against a draft or a publish, so a layer says
    who last changed it."""
    return SemanticLayerService(AppStoreRepository(published_by))


async def layer_author(
    database: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> SessionData:
    """This session, if it may write `database`'s layer.

    A dependency rather than a check inside each route, so a request nobody is
    allowed to make is refused before its body is even read."""
    await sources.require_author(request, session, database)
    return session


async def _may_write(request: Request, session: SessionData, source_key: str) -> bool:
    """Whether this person may write the layer — asked without refusing, so the
    editor can open read-only rather than showing an error."""
    try:
        await sources.require_author(request, session, source_key)
    except Exception:  # noqa: BLE001 - any refusal means "not this person"
        return False
    return True


async def _schema(request: Request, session: SessionData, source_key: str) -> dict:
    """The source's live tables and columns, read through its own connector.

    Every connector reads its own schema, so this is the same call for a
    warehouse database, a SQL Server one, and whatever is added next."""
    connector = sources.connector_for(source_key)
    return await sources.run_on_source(
        request, session, source_key, connector.schema_map, source_key
    )


# ── Response models ───────────────────────────────────────────────────────


class IssueOut(BaseModel):
    severity: str
    field: str
    message: str

    @classmethod
    def of(cls, issue: Issue) -> "IssueOut":
        return cls(severity=issue.severity, field=issue.field, message=issue.message)


class LayerSummary(BaseModel):
    database: str
    display_name: str = ""
    #: Which connector serves it ("snowflake", "sqlserver"), and whether this
    #: person may write its layer — the two differ per connector.
    connector: str = "snowflake"
    can_edit: bool = False
    is_published: bool = False
    has_draft: bool = False
    documented_tables: int = 0


class Coverage(BaseModel):
    """How much of the live schema the document actually describes.

    The number that tells an admin where to spend their time, and the reason
    the editor shows the schema beside the document rather than alone.
    """

    total_tables: int = 0
    documented_tables: int = 0
    total_columns: int = 0
    documented_columns: int = 0
    tables_with_date_roles: int = 0


class SourceTable(BaseModel):
    """One live table, as the editor lists it."""

    # `schema` shadows a BaseModel attribute, so it is stored under another
    # name and exposed as "schema" in the JSON.
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(default="", alias="schema")
    name: str
    full_name: str
    columns: int = 0


class LayerDetail(BaseModel):
    database: str
    #: What to call this source on screen, from the connector that serves it —
    #: "FINTECH_DB (DA SQL Server)" rather than the key the layer is stored
    #: under. A source with no layer yet has no display name of its own, and
    #: showing the raw key twice told a person nothing they did not type.
    source_name: str = ""
    document: SemanticDocument | None
    is_published: bool
    has_draft: bool
    can_edit: bool = False
    coverage: Coverage
    issues: list[IssueOut] = Field(default_factory=list)


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/databases", response_model=list[LayerSummary])
async def list_layers(
    request: Request,
    session: SessionData = Depends(get_current_session),
    connector: str | None = None,
    connection: str | None = None,
) -> list[LayerSummary]:
    """The databases this person can work on.

    `connector` and `connection` narrow it to one workspace's sources: a
    warehouse's databases and a SQL Server connection's are separate places,
    and someone documenting one is not looking at the other."""
    service = _service(session.snowflake_user)
    may_author_snowflake = await can_author_snowflake(session)
    is_admin = await session_is_admin(session)

    out: list[LayerSummary] = []
    listed = await sources.list_sources(
        request, session, connector=connector, connection_id=connection
    )
    for source in listed:
        state = service.get_state(source.key)
        doc = state.editable
        out.append(
            LayerSummary(
                database=source.key,
                display_name=(doc.display_name if doc else "") or source.display_name,
                connector=source.connector,
                can_edit=may_author_snowflake if source.connector == "snowflake" else is_admin,
                is_published=state.is_published,
                has_draft=state.has_draft,
                documented_tables=len(doc.tables) if doc else 0,
            )
        )
    return out


@router.get("/{database:path}/tables", response_model=list[SourceTable])
async def list_source_tables(
    database: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> list[SourceTable]:
    """The source's tables, to document against.

    Read through the connector, so the editor shows a SQL Server database's
    tables the same way it shows a warehouse's. Named as that source names
    them, which is how a layer must key them."""
    await sources.require_visible(request, session, database)
    schema = await _schema(request, session, database)

    tables: list[SourceTable] = []
    for full_name, columns in sorted(schema.items()):
        parts = full_name.split(".")
        tables.append(SourceTable(
            schema=".".join(parts[:-1]),
            name=parts[-1],
            full_name=full_name,
            columns=len(columns),
        ))
    return tables


@router.get("/{database:path}", response_model=LayerDetail)
async def get_layer(
    database: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> LayerDetail:
    """The editable document plus coverage against the live schema."""
    await sources.require_visible(request, session, database)

    service = _service(session.snowflake_user)
    state = service.get_state(database)

    try:
        schema = await _schema(request, session, database)
    except InvalidIdentifierError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc

    doc = state.editable
    return LayerDetail(
        database=database,
        source_name=sources.connector_for(database).display_name(database),
        can_edit=await _may_write(request, session, database),
        document=doc,
        is_published=state.is_published,
        has_draft=state.has_draft,
        coverage=_coverage(doc, schema),
        issues=[
            IssueOut.of(i)
            for i in (validate_document(
                doc, schema=schema or None,
                table_parts=sources.connector_for(database).table_name_parts,
            ) if doc else [])
        ],
    )


@router.post("/{database:path}/draft", response_model=LayerDetail)
async def save_draft(
    database: str,
    document: SemanticDocument,
    request: Request,
    session: SessionData = Depends(layer_author),
) -> LayerDetail:
    """Save work in progress. Invisible to the model until published."""

    service = _service(session.snowflake_user)
    issues = service.save_draft(
        database, document, table_parts=sources.connector_for(database).table_name_parts
    )

    try:
        schema = await _schema(request, session, database)
    except InvalidIdentifierError:
        schema = {}

    return LayerDetail(
        database=database,
        document=document,
        is_published=service.get_state(database).is_published,
        has_draft=True,
        coverage=_coverage(document, schema),
        issues=[IssueOut.of(i) for i in issues],
    )


@router.post("/{database:path}/publish", response_model=LayerDetail)
async def publish(
    database: str,
    request: Request,
    session: SessionData = Depends(layer_author),
) -> LayerDetail:
    """Make the draft authoritative, after validating against the live schema."""

    service = _service(session.snowflake_user)

    try:
        schema = await _schema(request, session, database)
    except InvalidIdentifierError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc

    try:
        warnings = service.publish(
            database, schema=schema,
            table_parts=sources.connector_for(database).table_name_parts,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=str(exc)) from exc
    except ValidationFailedError as exc:
        # 422 with the full issue list: the editor highlights each offending
        # field rather than showing one opaque failure message.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Cannot publish: the document has errors.",
                "issues": [IssueOut.of(i).model_dump() for i in exc.issues],
            },
        ) from exc

    state = service.get_state(database)
    return LayerDetail(
        database=database,
        document=state.published,
        is_published=True,
        has_draft=False,
        coverage=_coverage(state.published, schema),
        issues=[IssueOut.of(i) for i in warnings],
    )


@router.delete("/{database:path}/draft", status_code=status.HTTP_204_NO_CONTENT)
async def discard_draft(
    database: str,
    request: Request,
    session: SessionData = Depends(layer_author),
) -> None:
    """Throw away the draft, reverting the editor to the published document."""
    _service(session.snowflake_user).discard_draft(database)


# ── Helpers ───────────────────────────────────────────────────────────────


def _coverage(doc: SemanticDocument | None, schema: dict[str, dict[str, str]]) -> Coverage:
    total_tables = len(schema)
    total_columns = sum(len(cols) for cols in schema.values())
    if doc is None:
        return Coverage(total_tables=total_tables, total_columns=total_columns)

    documented_tables = 0
    documented_columns = 0
    tables_with_dates = 0
    for name, table in doc.tables.items():
        live = schema.get(name.upper())
        if live is None:
            continue  # documented but no longer exists; validation flags it
        documented_tables += 1
        documented_columns += sum(
            1 for col in table.key_columns if col.upper() in live
        )
        if table.date_roles:
            tables_with_dates += 1

    return Coverage(
        total_tables=total_tables,
        documented_tables=documented_tables,
        total_columns=total_columns,
        documented_columns=documented_columns,
        tables_with_date_roles=tables_with_dates,
    )
