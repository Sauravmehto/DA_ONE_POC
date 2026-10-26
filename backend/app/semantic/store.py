"""Where semantic layers are kept: the app's own SQLite file.

ONE LAYER PER SOURCE, NOT PER WAREHOUSE
---------------------------------------
A layer is keyed by the source it describes, the same string the rest of the
app uses: 'LOAN' for a Snowflake database, 'SQLSERVER:<connection>/<database>'
for a database on a saved connection. Every connector's sources can have one;
nothing here knows which vendor it is talking about.

WHY NOT THE FOLDER
------------------
Layers used to be TOML files under backend/config/metadata/, baked into the
image. Publishing one on a server wrote into the container, so the next
redeploy silently put the old layer back. And a SQL Server layer could never
be a committed file at all: it names a connection that exists on one
deployment only. In SQLite each deployment keeps its own layers and a redeploy
cannot lose them.

The document itself is still the TOML a person wrote, stored as written, so
what comes back out is what went in — comments included.

THE FILES ARE STILL THE STARTING POINT
--------------------------------------
On first use the TOMLs in config/metadata are copied in as published layers,
once. After that the app is the authority and the files are left alone.
"""

from __future__ import annotations

import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

PUBLISHED = "published"
DRAFT = "draft"

#: Set in app_settings once config/metadata has been copied in.
SEEDED = "imported.files.semantic_layers"


def _store():
    from app.accounts import store

    return store


def _now() -> str:
    return datetime.now(UTC).isoformat()


def source_key_of(document: bytes, fallback: str) -> str:
    """The source a TOML document describes: its `database` key, upper-cased
    for a warehouse database, or the source key as written for anything with a
    prefix. `fallback` is used when the document names nothing."""
    try:
        named = str(tomllib.loads(document.decode("utf-8")).get("database") or "")
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        named = ""
    if not named:
        return fallback
    return named if ":" in named else named.upper()


class FolderDocuments:
    """The layers in a folder of TOMLs, in the shape the loaders read.

    The store is where a deployment's layers live; this is for tests, and for
    reading a folder without copying it in."""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def published_documents(self) -> dict[str, bytes]:
        if not self._dir.is_dir():
            return {}
        out: dict[str, bytes] = {}
        for path in sorted(self._dir.glob("*.toml")):
            data = path.read_bytes()
            out[source_key_of(data, path.stem.upper())] = data
        return out

    def version_marker(self) -> str:
        """Name, modification time AND size. Two writes a moment apart can
        share a timestamp — the clock behind st_mtime is coarser than the gap
        — and then an edit is invisible."""
        if not self._dir.is_dir():
            return "0"
        return "|".join(sorted(
            f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}"
            for p in self._dir.glob("*.toml")
        ))


# ── seeding from the files ───────────────────────────────────────────────────


def seed_from_files(directory: Path) -> int:
    """Copy the TOMLs in `directory` in as published layers, once per
    deployment. Returns how many were copied."""
    with _store().read() as conn:
        if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (SEEDED,)).fetchone():
            return 0

    documents: dict[str, bytes] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*.toml")):
            data = path.read_bytes()
            key = source_key_of(data, path.stem.upper())
            if key:
                documents[key] = data

    now = _now()
    with _store().write() as conn:
        # Checked again inside the write: four workers start together.
        if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (SEEDED,)).fetchone():
            return 0
        copied = 0
        for key, data in documents.items():
            copied += conn.execute(
                "INSERT OR IGNORE INTO semantic_layers "
                "(source_key, state, document, version, updated_by, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (key, PUBLISHED, data.decode("utf-8"), "setup", now),
            ).rowcount
            conn.execute(
                "INSERT OR IGNORE INTO semantic_layer_versions "
                "(source_key, version, document, published_by, published_at) "
                "VALUES (?, 1, ?, ?, ?)",
                (key, data.decode("utf-8"), "setup", now),
            )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (SEEDED, str(copied), "setup", now),
        )
    if copied:
        log.info("Copied %d semantic layer(s) from %s", copied, directory)
    return copied


def _ready() -> None:
    """Seed on first use, so no deployment has to be told to run anything."""
    from app.core.config import settings

    try:
        seed_from_files(settings.metadata_dir)
    except Exception:  # noqa: BLE001 - a layer that cannot be copied must not
        # take the app down; the folder is still readable by hand.
        log.warning("Could not copy semantic layers from the metadata folder", exc_info=True)


# ── reading ──────────────────────────────────────────────────────────────────


def published_documents() -> dict[str, bytes]:
    """Every published layer, as {source_key: TOML}."""
    _ready()
    with _store().read() as conn:
        rows = conn.execute(
            "SELECT source_key, document FROM semantic_layers WHERE state = ?",
            (PUBLISHED,),
        ).fetchall()
    return {row["source_key"]: row["document"].encode("utf-8") for row in rows}


def read(source_key: str, state: str = PUBLISHED) -> bytes | None:
    _ready()
    with _store().read() as conn:
        row = conn.execute(
            "SELECT document FROM semantic_layers WHERE source_key = ? AND state = ?",
            (source_key, state),
        ).fetchone()
    return row["document"].encode("utf-8") if row else None


def version_marker() -> str:
    """Changes whenever any layer does.

    The other three workers never see a publish made by this one. Rather than
    reload on a timer, each reader compares this marker and reloads only when
    it moves."""
    _ready()
    with _store().read() as conn:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at), ''), COALESCE(SUM(version), 0) "
            "FROM semantic_layers WHERE state = ?",
            (PUBLISHED,),
        ).fetchone()
    return f"{row[0]}:{row[1]}:{row[2]}"


def published_sources() -> list[str]:
    _ready()
    with _store().read() as conn:
        return [
            row["source_key"]
            for row in conn.execute(
                "SELECT source_key FROM semantic_layers WHERE state = ? ORDER BY source_key",
                (PUBLISHED,),
            )
        ]


# ── writing ──────────────────────────────────────────────────────────────────


def write_draft(source_key: str, document: bytes, updated_by: str | None = None) -> None:
    _ready()
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO semantic_layers "
            "(source_key, state, document, version, updated_by, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?) "
            "ON CONFLICT (source_key, state) DO UPDATE SET "
            "document = excluded.document, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (source_key, DRAFT, document.decode("utf-8"), updated_by, _now()),
        )


def discard_draft(source_key: str) -> None:
    _ready()
    with _store().write() as conn:
        conn.execute(
            "DELETE FROM semantic_layers WHERE source_key = ? AND state = ?",
            (source_key, DRAFT),
        )


def publish(source_key: str, document: bytes, published_by: str | None = None) -> int:
    """Make `document` the live layer and keep it as a version. Returns the
    new version number. The draft, if any, is cleared: it has become this."""
    _ready()
    now = _now()
    text = document.decode("utf-8")
    with _store().write() as conn:
        row = conn.execute(
            "SELECT version FROM semantic_layers WHERE source_key = ? AND state = ?",
            (source_key, PUBLISHED),
        ).fetchone()
        latest = conn.execute(
            "SELECT MAX(version) FROM semantic_layer_versions WHERE source_key = ?",
            (source_key,),
        ).fetchone()[0]
        version = max(int(row["version"]) if row else 0, int(latest or 0)) + 1
        conn.execute(
            "INSERT INTO semantic_layers "
            "(source_key, state, document, version, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (source_key, state) DO UPDATE SET "
            "document = excluded.document, version = excluded.version, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (source_key, PUBLISHED, text, version, published_by, now),
        )
        conn.execute(
            "INSERT INTO semantic_layer_versions "
            "(source_key, version, document, published_by, published_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_key, version, text, published_by, now),
        )
        conn.execute(
            "DELETE FROM semantic_layers WHERE source_key = ? AND state = ?",
            (source_key, DRAFT),
        )
    return version


def versions(source_key: str) -> list[dict[str, str | int]]:
    """Every published version of a layer, newest first, without documents."""
    _ready()
    with _store().read() as conn:
        rows = conn.execute(
            "SELECT version, published_by, published_at FROM semantic_layer_versions "
            "WHERE source_key = ? ORDER BY version DESC",
            (source_key,),
        ).fetchall()
    return [dict(row) for row in rows]


def version_document(source_key: str, version: int) -> bytes | None:
    _ready()
    with _store().read() as conn:
        row = conn.execute(
            "SELECT document FROM semantic_layer_versions "
            "WHERE source_key = ? AND version = ?",
            (source_key, int(version)),
        ).fetchone()
    return row["document"].encode("utf-8") if row else None
