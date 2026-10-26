"""Where the warehouse engine's dashboard records live: the app's own store.

`dashboards.py` plans, runs and edits a warehouse dashboard. It used to keep
the result in the warehouse too — one row per version in MCP_SAVED_DASHBOARDS —
which made a dashboard part of the customer's data rather than the app's own
metadata, gave dev and prod one shared list, and left a client without that
warehouse unable to save a dashboard at all.

This module answers the same calls the Snowflake client answered, in the same
shape (upper-case keys, plans and tags already parsed), so `dashboards.py`
reads and writes records without knowing where they are kept. Nothing here
writes to a warehouse. The functions keep the names they had there, because a
name change would have hidden which call sites moved and which did not.

Reading a record no longer needs a warehouse session, so a dashboard opens,
and the list renders, while the warehouse is slow or down. Running its panels
still needs it — that is the part that reads the data.
"""

from __future__ import annotations

from typing import Any

from app.engine import dashboard_store

#: How the store labels a warehouse dashboard's source. Every dashboard
#: records where it came from; this is the one this module keeps.
SOURCE_TYPE = "snowflake"


def _upper(record: dict[str, Any]) -> dict[str, Any]:
    """A store record in the shape the warehouse table returned."""
    return {
        "REPORT_ID": record["report_id"],
        "VERSION_ID": f"{record['report_id']}_v{record['version']}",
        "VERSION": record["version"],
        "REPORT_TITLE": record["report_title"],
        "DESCRIPTION": record.get("description") or "",
        "TAGS": record.get("tags") or [],
        "DASHBOARD_PLAN": record.get("dashboard_plan") or {},
        "DATABASE_NAME": record.get("database_name"),
        "DATA_GROUP": record.get("data_group"),
        "CREATED_BY": record.get("created_by"),
        "CREATED_AT": record.get("created_at"),
        "LAST_RUN_AT": record.get("last_run_at"),
        "RUN_COUNT": record.get("run_count") or 0,
        "SOURCE_KEY": record.get("source_key"),
        "SOURCE_TYPE": record.get("source_type") or SOURCE_TYPE,
    }


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _source_key(row: dict[str, Any]) -> str:
    """Which source a warehouse dashboard reads.

    The database it was planned against, which is what every other kind of
    source records and what the data-group check resolves a dashboard's group
    from."""
    return (row.get("database_name") or "").strip().upper()


def write_saved_dashboard(row: dict[str, Any]) -> None:
    """Save the first version of a dashboard."""
    dashboard_store.create({
        "report_id": row["report_id"],
        "version": 1,
        "report_title": row["report_title"],
        "description": row.get("description") or "",
        "tags": row.get("tags") or [],
        "source_key": _source_key(row),
        "source_type": SOURCE_TYPE,
        "database_name": row.get("database_name"),
        "data_group": row.get("data_group"),
        "dashboard_plan": row.get("dashboard_plan") or {},
        "created_by": row.get("created_by"),
        "created_at": _now(),
        "last_run_at": None,
        "run_count": 0,
        "is_active": True,
    })


def read_saved_dashboard(report_id: str) -> dict[str, Any] | None:
    record = dashboard_store.read(report_id)
    return _upper(record) if record else None


def insert_dashboard_version(row: dict[str, Any]) -> int:
    """Add the next version and make it current. Returns the number written,
    which is past the HIGHEST this dashboard has rather than past the current
    one — after a revert those differ.

    `database_name` on the row is ignored: which source a dashboard reads is
    recorded once on the dashboard, not repeated on every version, so an edit
    cannot drop it and take the dashboard out of its group."""
    return dashboard_store.add_version(
        row["report_id"], row.get("dashboard_plan") or {},
        edit_note=row.get("edit_note") or "",
        title=row.get("report_title"),
        description=row.get("description"),
        created_by=row.get("created_by"),
    )


def list_saved_dashboards(limit: int = 50) -> list[dict[str, Any]]:
    """Warehouse dashboards, most recently run or made first.

    Only warehouse ones: every dashboard is in this table now, and the module
    that serves the other sources lists those."""
    owners = dashboard_store.owners()
    out: list[dict[str, Any]] = []
    for record in dashboard_store.list_records(source_types=[SOURCE_TYPE],
                                               limit=max(1, min(int(limit), 500))):
        row = _upper(record)
        # OWNER is the first version's author; the current version's author is
        # whoever last edited it, which is a different fact and its own column.
        row["OWNER"] = owners.get(record["report_id"]) or record.get("created_by")
        row["UPDATED_BY"] = record.get("updated_by") or record.get("created_by")
        row["UPDATED_AT"] = record.get("updated_at") or record.get("created_at")
        out.append(row)
    return out


def list_dashboard_versions(report_id: str) -> list[dict[str, Any]]:
    return [
        {
            "VERSION": v["version"],
            "VERSION_ID": v["version_id"],
            "EDIT_NOTE": v["edit_note"],
            "CREATED_BY": v["created_by"],
            "CREATED_AT": v["created_at"],
            "IS_CURRENT": v["is_current"],
        }
        for v in dashboard_store.versions(report_id)
    ]


def dashboard_owner(report_id: str) -> str | None:
    return dashboard_store.owner(report_id)


def resolve_report_ids(id_or_title: str) -> list[dict[str, Any]]:
    """Warehouse dashboards whose id or title matches, case-insensitively."""
    wanted = (id_or_title or "").strip().lower()
    if not wanted:
        return []
    return [
        {"report_id": r["report_id"], "report_title": r["report_title"],
         "version": r["version"]}
        for r in dashboard_store.list_records(source_types=[SOURCE_TYPE], limit=500)
        if (r["report_id"] or "").lower() == wanted
        or (r["report_title"] or "").strip().lower() == wanted
    ]


def set_dashboard_group(report_id: str, group: str | None) -> None:
    dashboard_store.set_group(report_id, group)


def touch_saved_dashboard_run(report_id: str) -> tuple[int, str]:
    return dashboard_store.record_run(report_id)


def deactivate_saved_dashboard(report_id: str) -> int:
    """Retire a dashboard. Returns how many versions it had, which is what the
    warehouse table returned (a row per version), and 0 when there was nothing
    active to retire."""
    count = dashboard_store.version_count(report_id)
    return count if dashboard_store.deactivate(report_id) else 0


def revert_dashboard_version(report_id: str, version: int) -> bool:
    return dashboard_store.make_current(report_id, int(version))
