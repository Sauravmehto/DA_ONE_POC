"""Excel as a data source: upload a folder of workbooks, then ask questions.

Deliberately a SEPARATE namespace from /excel, which belongs to the report
builder. That flow takes a formatted template and refills it from SQL; this one
takes a client's own data workbooks and turns them into something queryable.
They share no endpoints and no state.

Upload accepts MANY files, because that is how these arrive — a folder of
monthly rent rolls, or a deal folder of mixed documents. Files are grouped into
datasets by shape, so twelve months of the same report become one queryable set
rather than twelve unrelated uploads.

Ingestion streams progress because a folder takes tens of seconds. Nothing is
cached in process memory: workbooks and their databases go to shared storage
and the definitions go to Snowflake, so a request landing on a different one of
the four workers behaves identically.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.access import (
    dataset_access,
    folder_access,
    get_access,
    require_dataset,
    require_folder,
)
from app.api.deps import get_current_session
from app.api.warehouse import run_bound, snowflake_binding
from app.engine import data_groups
from app.core.config import settings
from app.engine import collection_documents as engine_documents
from app.engine import excel_sources as engine_sources
from app.engine.connectors.snowflake import client as engine_client
from app.services.snowflake import SnowflakeService
from app.session.base import SessionData

log = logging.getLogger(__name__)

router = APIRouter(prefix="/excel-sources", tags=["excel-sources"])

#: Per file. Workbooks are small; this only stops an accidental huge upload.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
#: Documents legitimately run larger — a scanned-in appraisal with photographs
#: is tens of megabytes before it holds a single readable word.
MAX_DOCUMENT_BYTES = 60 * 1024 * 1024
#: Per batch. A folder is expected to be tens of files, not thousands.
MAX_FILES = 200

WORKBOOK_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"}
#: Documents go to the collection's graph rather than to a dataset, but a
#: folder holds both and the upload should not make the user separate them.
DOCUMENT_SUFFIXES = engine_documents.SUPPORTED_SUFFIXES
SUPPORTED_SUFFIXES = WORKBOOK_SUFFIXES | DOCUMENT_SUFFIXES

#: Files a folder picker will hand over that are not documents: Excel's own
#: lock files, and macOS/Windows directory metadata. Skipped quietly rather
#: than reported as failures, because they are noise, not errors.
_IGNORED_PREFIXES = ("~$", "._")
_IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


async def _run_engine(request: Request, session: SessionData, fn, *args, **kwargs):
    """Run a blocking engine call off the event loop as the signed-in user.

    Uploads are the app's own records, so this needs no Snowflake: the
    person's own connection is bound if they have connected one (records kept
    in Snowflake before uploads moved are then still listed), and otherwise
    no warehouse is — never the shared key-pair connection (spec 0007).
    """
    return await run_bound(request, session, fn, *args, **kwargs)


def _sse(event: str, data: dict[str, Any]) -> str:
    """Encode one SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _is_ignorable(name: str) -> bool:
    base = Path(name).name
    return base in _IGNORED_NAMES or base.startswith(_IGNORED_PREFIXES)


class DatasetSummary(BaseModel):
    dataset_id: str
    name: str
    #: Where the files came from, when they all came from one folder. Lets the
    #: library group the several datasets a mixed folder produced.
    folder: str = ""
    file_count: int
    table_count: int
    total_rows: int
    period_start: str | None = None
    period_end: str | None = None
    updated_at: str


def _summarise(dataset: engine_sources.Dataset) -> DatasetSummary:
    earliest, latest = dataset.period_range
    return DatasetSummary(
        dataset_id=dataset.dataset_id,
        name=dataset.name,
        folder=dataset.folder,
        file_count=dataset.file_count,
        table_count=len(dataset.tables),
        total_rows=dataset.total_rows,
        period_start=earliest,
        period_end=latest,
        updated_at=dataset.updated_at,
    )


class DocumentInfo(BaseModel):
    source_file: str
    file_format: str = ""
    doc_type: str = ""
    page_count: int = 0
    chunk_count: int = 0
    fact_count: int = 0
    is_scanned: bool = False


class CollectionSummary(BaseModel):
    collection_id: str
    name: str
    #: False while the collection is grouped on the fly rather than stored. The
    #: client does not need to care: renaming one works either way.
    is_named: bool
    file_count: int
    total_rows: int
    period_start: str | None = None
    period_end: str | None = None
    updated_at: str
    datasets: list[DatasetSummary]
    documents: list[DocumentInfo] = []


class RenameRequest(BaseModel):
    name: str


def _summarise_collection(
    collection: engine_sources.Collection,
) -> CollectionSummary:
    earliest, latest = collection.period_range
    return CollectionSummary(
        collection_id=collection.collection_id,
        name=collection.name,
        is_named=collection.is_named,
        file_count=collection.file_count,
        total_rows=collection.total_rows,
        period_start=earliest,
        period_end=latest,
        updated_at=collection.updated_at,
        datasets=[_summarise(d) for d in collection.datasets],
        documents=[
            DocumentInfo(
                source_file=d.source_file, file_format=d.file_format,
                doc_type=d.doc_type, page_count=d.page_count,
                chunk_count=d.chunk_count, fact_count=d.fact_count,
                is_scanned=d.is_scanned,
            )
            for d in engine_documents.list_documents(collection.collection_id)
        ],
    )


@router.get("", response_model=list[CollectionSummary])
async def list_collections(
    request: Request,
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> list[CollectionSummary]:
    """The library: folders holding the sources built from them."""
    collections = await _run_engine(
        request, session, engine_sources.list_collections
    )
    return [
        _summarise_collection(c) for c in collections
        if access.allows([("folder", c.collection_id)])
    ]


# Declared before /{dataset_id}, which would otherwise match "collections".
@router.patch("/collections/{collection_id:path}", response_model=CollectionSummary)
async def rename_collection(
    collection_id: str,
    body: RenameRequest,
    request: Request,
    session: SessionData = Depends(get_current_session),
    _access: data_groups.Access = Depends(folder_access),
) -> CollectionSummary:
    """Rename a folder. The datasets inside keep their own names."""
    name = body.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A folder needs a name.",
        )
    try:
        collection = await _run_engine(
            request, session, engine_sources.rename_collection, collection_id, name
        )
    except engine_sources.PersistenceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such folder."
        )
    return _summarise_collection(collection)


@router.delete("/collections/{collection_id}")
async def delete_collection(
    collection_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    _access: data_groups.Access = Depends(folder_access),
) -> dict[str, Any]:
    """Remove a folder and everything in it."""
    removed = await _run_engine(
        request, session, engine_sources.delete_collection, collection_id
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such folder."
        )
    return {"collection_id": collection_id, "deleted": True}


@router.delete("/collections/{collection_id}/documents/{filename:path}")
async def remove_collection_document(
    collection_id: str,
    filename: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    _access: data_groups.Access = Depends(folder_access),
) -> dict[str, Any]:
    """Take one document back out of a folder."""
    removed = await _run_engine(
        request,
        session,
        engine_documents.remove_document,
        collection_id,
        Path(filename).name,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That folder has no such document.",
        )
    return {"collection_id": collection_id, "removed": Path(filename).name}


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    _access: data_groups.Access = Depends(dataset_access),
) -> dict[str, Any]:
    """One dataset, with its tables and the files that make it up."""
    dataset = await _run_engine(
        request, session, engine_sources.get_dataset, dataset_id
    )
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such dataset."
        )
    return dataset.as_dict()


@router.delete("/{dataset_id}")
async def delete_dataset(
    dataset_id: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    _access: data_groups.Access = Depends(dataset_access),
) -> dict[str, Any]:
    """Remove a dataset and every file in it."""
    removed = await _run_engine(
        request, session, engine_sources.delete_dataset, dataset_id
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such dataset."
        )
    return {"dataset_id": dataset_id, "deleted": True}


@router.delete("/{dataset_id}/files/{filename:path}")
async def remove_file(
    dataset_id: str,
    filename: str,
    request: Request,
    session: SessionData = Depends(get_current_session),
    _access: data_groups.Access = Depends(dataset_access),
) -> dict[str, Any]:
    """Take one workbook back out of a dataset, leaving the others in place.

    `:path` because a workbook name may hold anything a file system allows; the
    client sends it encoded and only the leaf is matched.
    """
    try:
        outcome = await _run_engine(
            request,
            session,
            engine_sources.remove_file,
            dataset_id,
            Path(filename).name,
        )
    except engine_sources.PersistenceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if not outcome.removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That dataset has no such file.",
        )
    if outcome.dataset_deleted:
        return {"dataset_deleted": True, "dataset_id": dataset_id}
    assert outcome.dataset is not None
    return outcome.dataset.as_dict()


@router.post("/upload")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    dataset_id: str = Form(""),
    collection_id: str = Form(""),
    session: SessionData = Depends(get_current_session),
    access: data_groups.Access = Depends(get_access),
) -> StreamingResponse:
    """Upload one or many workbooks — typically a whole folder.

    Each upload starts a new folder. Pass `collection_id` to add these files to
    an existing one instead — matching shapes join the dataset already there,
    new shapes become new datasets inside it, and documents join its graph.

    Frames:
        progress  {percent, stage}  — emitted as real work completes
        complete  the batch result: datasets touched, per-file outcomes
        failed    {detail}          — ingestion could not finish

    Argument validation happens BEFORE streaming starts, so a bad request still
    gets a real 4xx. Failures after the first byte arrive as a `failed` frame,
    because the status line is long gone by then.
    """
    # Adding to a folder, or to a dataset inside one, needs sight of it.
    if collection_id:
        await require_folder(access, collection_id)
    if dataset_id:
        await require_dataset(request, session, access, dataset_id)

    payload: list[tuple[str, bytes]] = []
    skipped: list[str] = []

    for upload in files[:MAX_FILES]:
        name = upload.filename or "upload.xlsx"
        if _is_ignorable(name):
            skipped.append(Path(name).name)
            continue
        if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
            skipped.append(Path(name).name)
            continue
        data = await upload.read()
        if not data:
            skipped.append(Path(name).name)
            continue
        cap = (
            MAX_DOCUMENT_BYTES
            if Path(name).suffix.lower() in DOCUMENT_SUFFIXES
            else MAX_UPLOAD_BYTES
        )
        if len(data) > cap:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{Path(name).name} exceeds {cap // (1024 * 1024)}MB.",
            )
        payload.append((name, data))

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No readable files in that selection. Expected one of: "
                f"{', '.join(sorted(SUPPORTED_SUFFIXES))}."
            ),
        )

    owner = session.snowflake_user
    # One binding for the whole batch: the signed-in user's own Snowflake if
    # they have connected it, else no warehouse — never the legacy key-pair
    # service account. An upload is written to the app store either way, so
    # someone without Snowflake can upload (spec 0007).
    snowflake = await snowflake_binding(request, session)

    async def stream():
        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: str, data: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        def work() -> None:
            try:
                with snowflake():
                    result = engine_sources.ingest_files(
                        payload,
                        owner=owner,
                        on_progress=lambda pct, stage: emit(
                            "progress", {"percent": pct, "stage": stage}
                        ),
                        add_to=dataset_id.strip() or None,
                        add_to_collection=collection_id.strip() or None,
                    )
                data = result.as_dict()
                if skipped:
                    data["skipped"] = skipped
                emit("complete", data)
            except engine_sources.PersistenceUnavailable as exc:
                # Loud, not silent: an upload that cannot be recorded would
                # otherwise look successful and then not appear anywhere.
                log.error("Upload could not be persisted: %s", exc)
                emit("failed", {"detail": str(exc)})
            except Exception as exc:
                log.exception("Ingestion failed")
                emit("failed", {"detail": f"Could not read these files: {exc}"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield _sse(event, data)
        finally:
            await task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
