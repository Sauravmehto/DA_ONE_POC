"""What is known about uploaded spreadsheets and folders, in the app store.

The files themselves live in object storage; these are the records that make
them visible — which workbooks a dataset holds, which folder it sits in, and
what a shape's columns were agreed to mean.

WHY HERE
--------
They used to be rows in Snowflake tables, so a client with spreadsheets and no
warehouse could not list what they uploaded yesterday. That is the case this
product is meant to serve.

WHAT THIS DOES NOT DO
---------------------
It never writes to Snowflake, and nothing is copied out of one. Records made
before this are read where they already are (see excel_sources), and left
exactly as they were.

Rows are stored with the same shapes excel_sources already uses: a dataset's
tables and files as JSON, a definition's mapping and concepts as JSON.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

DATASET_COLUMNS = (
    "dataset_id", "dataset_name", "fingerprint", "sheet_count", "tables",
    "files", "database_size", "collection_id", "owner", "created_at",
    "updated_at",
)


def _store():
    from app.accounts import store

    return store


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(raw: Any) -> list:
    if not raw:
        return []
    return json.loads(raw) if isinstance(raw, str) else raw


# ── datasets ─────────────────────────────────────────────────────────────────


def _dataset_row(row) -> dict[str, Any]:
    return {
        "dataset_id": row["dataset_id"],
        "name": row["dataset_name"],
        "fingerprint": row["fingerprint"] or "",
        "sheet_count": int(row["sheet_count"] or 0),
        "tables": _loads(row["tables"]),
        "files": _loads(row["files"]),
        "database_size": int(row["database_size"] or 0),
        "collection_id": row["collection_id"] or "",
        #: Who created this dataset (spec 0012). "" on a row written before
        #: that migration — treated as "not recorded", not "owned by nobody".
        "owner": row["owner"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def save_dataset(record: dict[str, Any]) -> None:
    """Write a dataset's current state, replacing any earlier row for it.

    owner is written on INSERT only — the ON CONFLICT branch deliberately
    does not touch it, so appending more files to a dataset never changes who
    it belongs to (spec 0012)."""
    now = _now()
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO excel_datasets (dataset_id, dataset_name, fingerprint, "
            "sheet_count, tables, files, database_size, collection_id, owner, "
            "created_at, updated_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT (dataset_id) DO UPDATE SET "
            "dataset_name = excluded.dataset_name, fingerprint = excluded.fingerprint, "
            "sheet_count = excluded.sheet_count, tables = excluded.tables, "
            "files = excluded.files, database_size = excluded.database_size, "
            "collection_id = excluded.collection_id, updated_at = excluded.updated_at, "
            "is_active = 1",
            (
                record["dataset_id"], record["name"], record.get("fingerprint") or "",
                int(record.get("sheet_count") or 0),
                json.dumps(record.get("tables") or []),
                json.dumps(record.get("files") or []),
                int(record.get("database_size") or 0),
                record.get("collection_id") or "",
                record.get("owner") or None,
                record.get("created_at") or now, now,
            ),
        )


def list_datasets() -> list[dict[str, Any]]:
    """Every active dataset, most recently updated first."""
    with _store().read() as conn:
        rows = conn.execute(
            "SELECT * FROM excel_datasets WHERE is_active = 1 ORDER BY updated_at DESC"
        ).fetchall()
    return [_dataset_row(row) for row in rows]


def knows_dataset(dataset_id: str) -> bool:
    """Whether this dataset is ours, active or retired. A retired one is not
    read from the old place again — it was deleted here."""
    with _store().read() as conn:
        return conn.execute(
            "SELECT 1 FROM excel_datasets WHERE dataset_id = ?", (dataset_id,)
        ).fetchone() is not None


def deactivate_dataset(dataset_id: str) -> bool:
    with _store().write() as conn:
        return conn.execute(
            "UPDATE excel_datasets SET is_active = 0, updated_at = ? "
            "WHERE dataset_id = ? AND is_active = 1",
            (_now(), dataset_id),
        ).rowcount > 0


# ── folders ──────────────────────────────────────────────────────────────────


def collection_names() -> dict[str, tuple[str, str, str, str]]:
    """Stored folder names, by id: (name, created_at, updated_at, owner).

    owner is "" when not recorded (spec 0012) — a collection named before that
    migration, or one named by promotion (rename_collection) whose datasets
    themselves carry the real owner (list_collections falls back to that)."""
    with _store().read() as conn:
        rows = conn.execute(
            "SELECT collection_id, name, created_at, updated_at, owner "
            "FROM excel_collections WHERE is_active = 1"
        ).fetchall()
    return {
        r["collection_id"]: (r["name"], r["created_at"], r["updated_at"], r["owner"] or "")
        for r in rows
    }


def save_collection_name(collection_id: str, name: str, owner: str = "") -> None:
    """owner is written on INSERT only (first-writer-wins, like
    save_dataset's) — a rename later never changes who a folder belongs to."""
    now = _now()
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO excel_collections (collection_id, name, owner, "
            "created_at, updated_at, is_active) VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT (collection_id) DO UPDATE SET "
            "name = excluded.name, updated_at = excluded.updated_at, is_active = 1",
            (collection_id, name, owner or None, now, now),
        )


def knows_collection(collection_id: str) -> bool:
    with _store().read() as conn:
        return conn.execute(
            "SELECT 1 FROM excel_collections WHERE collection_id = ?", (collection_id,)
        ).fetchone() is not None


def deactivate_collection(collection_id: str) -> bool:
    with _store().write() as conn:
        return conn.execute(
            "UPDATE excel_collections SET is_active = 0, updated_at = ? "
            "WHERE collection_id = ? AND is_active = 1",
            (_now(), collection_id),
        ).rowcount > 0


# ── what a shape's columns mean ──────────────────────────────────────────────


def save_definition(fingerprint: str, mapping: dict[str, Any],
                    concepts: list[str], owner: str | None) -> None:
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO excel_definitions (fingerprint, mapping, concepts, "
            "updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (fingerprint) DO UPDATE SET "
            "mapping = excluded.mapping, concepts = excluded.concepts, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (fingerprint, json.dumps(mapping), json.dumps(concepts), owner, _now()),
        )


def load_definition(fingerprint: str) -> dict[str, Any] | None:
    with _store().read() as conn:
        row = conn.execute(
            "SELECT mapping FROM excel_definitions WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
    return json.loads(row["mapping"]) if row and row["mapping"] else None


def concepts() -> list[str]:
    """Every concept the dictionary here already holds."""
    with _store().read() as conn:
        rows = conn.execute("SELECT concepts FROM excel_definitions").fetchall()
    seen: set[str] = set()
    for row in rows:
        seen.update(str(c) for c in _loads(row["concepts"]))
    return sorted(seen)
