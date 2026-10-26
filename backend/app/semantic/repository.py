"""Where semantic layer documents live.

The same seam the codebase already uses for sessions, conversations and
connectors: a Protocol with two implementations — the app's own SQLite file
(AppStoreRepository, what deployments use) and the metadata folder
(LocalDirectoryRepository, still handy in tests).

A layer is addressed by the source it describes, whatever vendor serves it:
"LOAN" for a Snowflake database, "SQLSERVER:<connection>/<database>" for one
on a saved connection. Nothing in this package knows one vendor from another.

Draft and published are separate objects, not a flag inside one. A draft that
is half-finished must be invisible to the model — publishing is what makes a
layer authoritative, so the two states cannot share a slot.

`version_marker()` exists for the S3 case: with several workers, a document
published by one process is otherwise invisible to the others until restart.
A cheap marker that changes on every write lets readers poll instead of
reloading everything on a timer.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from app.semantic import store

log = logging.getLogger(__name__)

_DRAFT_SUFFIX = ".draft.toml"
_LIVE_SUFFIX = ".toml"


def _safe_key(database: str) -> str:
    """A database name reduced to a safe object/file name.

    Never trust the name to be a safe path component: it reaches us from an
    API request, and a traversal here would write outside the store.
    """
    cleaned = "".join(c for c in database if c.isalnum() or c in "-_").lower()
    if not cleaned:
        raise ValueError(f"Database name yields an empty key: {database!r}")
    return cleaned


class MetadataRepository(Protocol):
    def list_published(self) -> list[str]: ...

    def read_published(self, database: str) -> bytes | None: ...

    def read_draft(self, database: str) -> bytes | None: ...

    def write_draft(self, database: str, data: bytes) -> None: ...

    def publish(self, database: str, data: bytes) -> None: ...

    def discard_draft(self, database: str) -> None: ...

    def version_marker(self) -> str: ...


class LocalDirectoryRepository:
    """Files under backend/config/metadata/ — the development default.

    Drafts go in a `drafts/` subdirectory rather than beside their published
    document. Both loaders glob `*.toml` in the config directory and key on the
    `database` field inside each file, so a draft sitting alongside would be
    loaded as though it were live — the exact thing publish/draft exists to
    prevent. A subdirectory keeps drafts outside that glob entirely.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._drafts = directory / "drafts"

    def _published_path(self, database: str) -> Path:
        return self._dir / f"{_safe_key(database)}{_LIVE_SUFFIX}"

    def _draft_path(self, database: str) -> Path:
        return self._drafts / f"{_safe_key(database)}{_DRAFT_SUFFIX}"

    def list_published(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(p.stem for p in self._dir.glob(f"*{_LIVE_SUFFIX}"))

    def read_published(self, database: str) -> bytes | None:
        path = self._published_path(database)
        return path.read_bytes() if path.is_file() else None

    def read_draft(self, database: str) -> bytes | None:
        path = self._draft_path(database)
        return path.read_bytes() if path.is_file() else None

    def write_draft(self, database: str, data: bytes) -> None:
        self._drafts.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._draft_path(database), data)

    def publish(self, database: str, data: bytes) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._published_path(database), data)
        self.discard_draft(database)

    def discard_draft(self, database: str) -> None:
        self._draft_path(database).unlink(missing_ok=True)

    def version_marker(self) -> str:
        """Changes whenever any published document changes.

        Modification times rather than content hashes: this is checked on a
        short interval and only has to detect change, not describe it.
        """
        if not self._dir.is_dir():
            return "0"
        stamps = sorted(
            f"{p.name}:{p.stat().st_mtime_ns}"
            for p in self._dir.glob(f"*{_LIVE_SUFFIX}")
        )
        return "|".join(stamps)


class AppStoreRepository:
    """Layers in the app's own SQLite file — what a deployment uses.

    Each deployment keeps its own, so dev and prod layers never mix, and a
    redeploy cannot put an old layer back. See app/semantic/store.py for why
    the folder could not stay.
    """

    def __init__(self, published_by: str | None = None) -> None:
        # Recorded against a publish, so a layer says who last changed it.
        self._who = published_by

    def as_(self, published_by: str | None) -> "AppStoreRepository":
        return AppStoreRepository(published_by)

    def list_published(self) -> list[str]:
        return store.published_sources()

    def read_published(self, source_key: str) -> bytes | None:
        return store.read(source_key, store.PUBLISHED)

    def read_draft(self, source_key: str) -> bytes | None:
        return store.read(source_key, store.DRAFT)

    def write_draft(self, source_key: str, data: bytes) -> None:
        store.write_draft(source_key, data, self._who)

    def publish(self, source_key: str, data: bytes) -> None:
        store.publish(source_key, data, self._who)

    def discard_draft(self, source_key: str) -> None:
        store.discard_draft(source_key)

    def version_marker(self) -> str:
        return store.version_marker()


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file, then replace.

    An interrupted write must never leave a half-written TOML behind: the
    loaders would skip it, silently taking that database offline.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
