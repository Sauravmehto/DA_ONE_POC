"""Saved dashboards in the app's own SQLite file (app.accounts.store).

One row per dashboard in `dashboards`, and one row per saved version in
`dashboard_versions`, the current one included. A version is never changed
after it is written: an edit, a layout change or a revert adds the next one
and moves `current_version` to it, in one transaction, so two workers saving
at once cannot both claim the same number.

Records come back in the shape file_dashboards has always used for its JSON
records (report_id, version, report_title, dashboard_plan, last_edit_note...),
so code that reads a record does not need to know where it was kept.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


class StaleVersion(Exception):
    """The dashboard moved on after the change being saved was based on it."""


def _store():
    from app.accounts import store

    return store


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


_RECORD_SELECT = (
    "SELECT d.report_id, d.report_title, d.description, d.tags, d.source_key, "
    "d.source_type, d.database_name, d.data_group, d.current_version, d.created_by, "
    "d.created_at, d.updated_at, d.last_run_at, d.run_count, d.is_active, "
    "v.plan, v.edit_note, v.created_by AS updated_by "
    "FROM dashboards d JOIN dashboard_versions v "
    "ON v.report_id = d.report_id AND v.version = d.current_version "
)


def _record(row) -> dict[str, Any]:
    return {
        "report_id": row["report_id"],
        "version": row["current_version"],
        "report_title": row["report_title"],
        "description": row["description"] or "",
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "source_key": row["source_key"],
        "source_type": row["source_type"],
        "database_name": row["database_name"],
        "data_group": row["data_group"],
        "dashboard_plan": json.loads(row["plan"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        # Who made it, and who last changed it: an edit writes a version under
        # the editor's name, and reading one for the other hands a dashboard
        # to whoever touched it last.
        "updated_by": row["updated_by"] or row["created_by"],
        "updated_at": row["updated_at"],
        "last_run_at": row["last_run_at"],
        "run_count": row["run_count"],
        "is_active": bool(row["is_active"]),
        "last_edit_note": row["edit_note"],
    }


def _insert_dashboard(conn, record: dict[str, Any], current_version: int) -> int:
    return conn.execute(
        "INSERT OR IGNORE INTO dashboards (report_id, report_title, description, tags, "
        "source_key, source_type, database_name, data_group, current_version, "
        "created_by, created_at, updated_at, last_run_at, run_count, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record["report_id"], record["report_title"], record.get("description") or "",
            json.dumps(record["tags"]) if record.get("tags") else None,
            record["source_key"], record["source_type"],
            record.get("database_name"), record.get("data_group"), current_version,
            record.get("created_by"), record["created_at"],
            record.get("updated_at") or record["created_at"],
            record.get("last_run_at"), int(record.get("run_count") or 0),
            1 if record.get("is_active", True) else 0,
        ),
    ).rowcount


def _insert_version(conn, report_id: str, version: int, plan: dict[str, Any], *,
                    title: str | None, description: str | None, edit_note: str | None,
                    created_by: str | None, created_at: str) -> None:
    conn.execute(
        "INSERT INTO dashboard_versions (report_id, version, report_title, description, "
        "plan, edit_note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (report_id, version, title, description, json.dumps(plan), edit_note,
         created_by, created_at),
    )


def create(record: dict[str, Any]) -> None:
    """Save a new dashboard as version 1. `record` is in the JSON record shape."""
    with _store().write() as conn:
        _insert_dashboard(conn, record, 1)
        _insert_version(
            conn, record["report_id"], 1, record["dashboard_plan"],
            title=record["report_title"], description=record.get("description") or "",
            edit_note=None, created_by=record.get("created_by"),
            created_at=record["created_at"],
        )


def read(report_id: str) -> dict[str, Any] | None:
    """The active dashboard with its current plan, or None."""
    with _store().read() as conn:
        row = conn.execute(
            _RECORD_SELECT + "WHERE d.report_id = ? AND d.is_active = 1", (report_id,)
        ).fetchone()
    return _record(row) if row else None


def known(report_id: str) -> bool:
    """Whether this id was ever saved here, removed ones included."""
    with _store().read() as conn:
        return conn.execute(
            "SELECT 1 FROM dashboards WHERE report_id = ?", (report_id,)
        ).fetchone() is not None


def list_records(*, source_key: str | None = None, owner: str | None = None,
                 source_types: list[str] | None = None,
                 limit: int = 500) -> list[dict[str, Any]]:
    """Active dashboards, most recently run or made first.

    `source_types` narrows to kinds of source ("snowflake", "sqlserver",
    "file"). Two callers list dashboards — the warehouse engine and the one
    that serves every other source — and since both now read this table, each
    asks for its own kind or the same dashboard is listed twice."""
    where, args = ["d.is_active = 1"], []
    if source_key:
        where.append("d.source_key = ?")
        args.append(source_key)
    if source_types:
        where.append("d.source_type IN (" + ",".join("?" * len(source_types)) + ")")
        args.extend(source_types)
    if owner:
        where.append("d.created_by = ? COLLATE NOCASE")
        args.append(owner)
    with _store().read() as conn:
        rows = conn.execute(
            _RECORD_SELECT + "WHERE " + " AND ".join(where)
            + " ORDER BY COALESCE(d.last_run_at, d.created_at) DESC LIMIT ?",
            (*args, int(limit)),
        ).fetchall()
    return [_record(row) for row in rows]


def add_version(report_id: str, plan: dict[str, Any], *, edit_note: str,
                title: str | None = None, description: str | None = None,
                created_by: str | None = None, base_version: int | None = None) -> int:
    """Save `plan` as the next version and make it current. Returns its number.

    `title` and `description`, when given, replace the dashboard's; otherwise
    they carry over. With `base_version`, the save is refused (StaleVersion)
    if the dashboard is no longer at that version."""
    now = _now()
    with _store().write() as conn:
        row = conn.execute(
            "SELECT current_version, report_title, description FROM dashboards "
            "WHERE report_id = ? AND is_active = 1",
            (report_id,),
        ).fetchone()
        if row is None:
            raise LookupError(report_id)
        current = int(row["current_version"])
        if base_version is not None and int(base_version) != current:
            raise StaleVersion(report_id)
        latest = conn.execute(
            "SELECT MAX(version) FROM dashboard_versions WHERE report_id = ?", (report_id,)
        ).fetchone()[0]
        version = max(current, int(latest or 0)) + 1
        new_title = title if title is not None else row["report_title"]
        new_description = description if description is not None else row["description"]
        _insert_version(conn, report_id, version, plan, title=new_title,
                        description=new_description, edit_note=edit_note,
                        created_by=created_by, created_at=now)
        conn.execute(
            "UPDATE dashboards SET current_version = ?, report_title = ?, description = ?, "
            "updated_at = ? WHERE report_id = ?",
            (version, new_title, new_description, now, report_id),
        )
    return version


def record_run(report_id: str) -> tuple[int, str]:
    """Count one run. Returns (run_count, last_run_at) after it."""
    now = _now()
    with _store().write() as conn:
        conn.execute(
            "UPDATE dashboards SET run_count = run_count + 1, last_run_at = ? "
            "WHERE report_id = ?",
            (now, report_id),
        )
        row = conn.execute(
            "SELECT run_count FROM dashboards WHERE report_id = ?", (report_id,)
        ).fetchone()
    return (int(row["run_count"]) if row else 0), now


def deactivate(report_id: str) -> bool:
    """Retire a dashboard. Its rows are kept, so it can be restored."""
    with _store().write() as conn:
        return conn.execute(
            "UPDATE dashboards SET is_active = 0, updated_at = ? "
            "WHERE report_id = ? AND is_active = 1",
            (_now(), report_id),
        ).rowcount > 0


def set_group(report_id: str, group: str | None) -> bool:
    """File a dashboard under `group`; None puts it back where its source
    would place it. Returns whether there was an active dashboard to file."""
    with _store().write() as conn:
        return conn.execute(
            "UPDATE dashboards SET data_group = ?, updated_at = ? "
            "WHERE report_id = ? AND is_active = 1",
            (group or None, _now(), report_id),
        ).rowcount > 0


def make_current(report_id: str, version: int) -> bool:
    """Make an existing version the current one. Nothing is written and
    nothing is lost — a revert is a pointer move, and the version reverted
    away from stays where it is. False if there is no such version."""
    with _store().write() as conn:
        exists = conn.execute(
            "SELECT 1 FROM dashboard_versions WHERE report_id = ? AND version = ?",
            (report_id, int(version)),
        ).fetchone()
        if exists is None:
            return False
        row = conn.execute(
            "SELECT report_title, description FROM dashboard_versions "
            "WHERE report_id = ? AND version = ?",
            (report_id, int(version)),
        ).fetchone()
        sets = ["current_version = ?", "updated_at = ?"]
        args: list[Any] = [int(version), _now()]
        # A version written before titles were kept per version has none; the
        # dashboard keeps the title it has rather than losing it to a NULL.
        if row["report_title"] is not None:
            sets.append("report_title = ?")
            args.append(row["report_title"])
        if row["description"] is not None:
            sets.append("description = ?")
            args.append(row["description"])
        conn.execute(
            f"UPDATE dashboards SET {', '.join(sets)} WHERE report_id = ? AND is_active = 1",
            (*args, report_id),
        )
    return True


def owner(report_id: str) -> str | None:
    """Who made this dashboard: the author of its FIRST version. The current
    version's author is whoever edited it last, which is a different fact."""
    with _store().read() as conn:
        row = conn.execute(
            "SELECT created_by FROM dashboard_versions WHERE report_id = ? "
            "ORDER BY version LIMIT 1",
            (report_id,),
        ).fetchone()
    return row["created_by"] if row and row["created_by"] else None


def owners() -> dict[str, str]:
    """{report_id: who made it} for every dashboard, in one read — a list of
    several hundred cannot afford a query each to name their authors."""
    with _store().read() as conn:
        rows = conn.execute(
            "SELECT report_id, created_by FROM dashboard_versions v "
            "WHERE v.version = (SELECT MIN(version) FROM dashboard_versions w "
            "                   WHERE w.report_id = v.report_id)"
        ).fetchall()
    return {row["report_id"]: row["created_by"] for row in rows if row["created_by"]}


def version_count(report_id: str) -> int:
    with _store().read() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM dashboard_versions WHERE report_id = ?",
            (report_id,),
        ).fetchone()[0])


def adopt_records(record: dict[str, Any], history: list[dict[str, Any]]) -> bool:
    """Copy a dashboard kept elsewhere, with its whole history, into the
    tables. Does nothing and returns False if it is already here.

    `history` holds a full row per version — {version, plan, report_title,
    description, edit_note, created_by, created_at} — unlike adopt(), which
    reconstructs what a JSON archive does not keep. A warehouse row keeps all
    of it, so each version arrives with the person who actually wrote it."""
    current = int(record.get("version") or 1)
    with _store().write() as conn:
        if not _insert_dashboard(conn, record, current):
            return False
        for version in sorted(history, key=lambda v: int(v["version"])):
            _insert_version(
                conn, record["report_id"], int(version["version"]),
                version.get("plan") or {},
                title=version.get("report_title"),
                description=version.get("description"),
                edit_note=version.get("edit_note"),
                created_by=version.get("created_by"),
                created_at=version.get("created_at") or record["created_at"],
            )
    return True


def versions(report_id: str) -> list[dict[str, Any]]:
    """Every version, oldest first, without the plans."""
    with _store().read() as conn:
        current = conn.execute(
            "SELECT current_version FROM dashboards WHERE report_id = ?", (report_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT version, edit_note, created_by, created_at FROM dashboard_versions "
            "WHERE report_id = ? ORDER BY version",
            (report_id,),
        ).fetchall()
    current_version = int(current["current_version"]) if current else None
    return [
        {
            "version": row["version"],
            "version_id": f"{report_id}_v{row['version']}",
            "edit_note": row["edit_note"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "is_current": row["version"] == current_version,
        }
        for row in rows
    ]


def version_plan(report_id: str, version: int) -> dict[str, Any] | None:
    with _store().read() as conn:
        row = conn.execute(
            "SELECT plan FROM dashboard_versions WHERE report_id = ? AND version = ?",
            (report_id, int(version)),
        ).fetchone()
    return json.loads(row["plan"]) if row else None


def adopt(record: dict[str, Any], archived: list[dict[str, Any]]) -> bool:
    """Copy a dashboard kept as JSON, with its archived versions, into the
    tables. Does nothing if it is already here. Returns whether it was copied.

    `archived` holds the JSON version snapshots ({version, dashboard_plan,
    edit_note, archived_at}). A snapshot's archived_at is when the NEXT
    version was saved, so it becomes that next version's created_at."""
    current = int(record.get("version") or 1)
    snapshots = {int(s["version"]): s for s in archived
                 if s.get("version") is not None and int(s["version"]) < current}
    saved_at = {v + 1: s.get("archived_at") for v, s in snapshots.items()}
    with _store().write() as conn:
        if not _insert_dashboard(conn, record, current):
            return False
        for version, snap in sorted(snapshots.items()):
            _insert_version(
                conn, record["report_id"], version, snap.get("dashboard_plan") or {},
                title=None, description=None, edit_note=snap.get("edit_note"),
                created_by=record.get("created_by") if version == 1 else None,
                created_at=(record["created_at"] if version == 1
                            else saved_at.get(version) or record["created_at"]),
            )
        _insert_version(
            conn, record["report_id"], current, record.get("dashboard_plan") or {},
            title=record["report_title"], description=record.get("description") or "",
            edit_note=record.get("last_edit_note"),
            created_by=record.get("created_by") if current == 1 else None,
            created_at=(record["created_at"] if current == 1
                        else saved_at.get(current) or record["created_at"]),
        )
    return True
