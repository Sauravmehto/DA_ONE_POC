"""Report Builder records in the app's own store: approved templates, and the
reports that fill them.

A template is a workbook shape somebody has approved for regeneration. A
report is that template plus the queries that fill it, against one database.
The workbooks themselves — the template file and every generated copy — stay
in object storage; these are the records that make them findable.

WHY HERE
--------
They were rows in Snowflake tables, so the Report Builder was a Snowflake
feature: a client on SQL Server had none. The work it does is the same
whatever database is underneath.

A template is NOT tied to a source. The same workbook shape can be filled from
two different databases, so what a report names is its source; the template
names only its shape (the fingerprint).

Nothing here writes to Snowflake, and nothing is copied out of one. Records
made before this are read where they are — see excel_reports.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def _store():
    from app.accounts import store

    return store


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


# ── templates ────────────────────────────────────────────────────────────────


def _template(row) -> dict[str, Any]:
    """In the shape excel_reports already reads: upper-case keys, as the old
    rows came back, so nothing above this has to know where it came from."""
    return {
        "TEMPLATE_ID": row["template_id"],
        "TEMPLATE_NAME": row["template_name"],
        "FINGERPRINT": row["fingerprint"],
        "SHAPE_FINGERPRINT": row["shape_fingerprint"],
        "TAB_ROLES": _loads(row["tab_roles"]) or {},
        "FORMULA_CLASSIFICATION": _loads(row["formula_classification"]) or {},
        "APPROVED_BY": row["approved_by"],
        "APPROVED_AT": row["approved_at"],
        "CREATED_AT": row["created_at"],
    }


def save_template(record: dict[str, Any]) -> None:
    now = _now()
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO excel_templates (template_id, template_name, fingerprint, "
            "shape_fingerprint, tab_roles, formula_classification, approved_by, "
            "approved_at, created_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT (template_id) DO UPDATE SET "
            "template_name = excluded.template_name, fingerprint = excluded.fingerprint, "
            "shape_fingerprint = excluded.shape_fingerprint, "
            "tab_roles = excluded.tab_roles, "
            "formula_classification = excluded.formula_classification, "
            "approved_by = excluded.approved_by, approved_at = excluded.approved_at, "
            "is_active = 1",
            (
                record["template_id"], record["template_name"], record["fingerprint"],
                record.get("shape_fingerprint"),
                json.dumps(record.get("tab_roles") or {}),
                json.dumps(record.get("formula_classification") or {}),
                record.get("approved_by"), record.get("approved_at") or now, now,
            ),
        )


def read_template(template_id: str) -> dict[str, Any] | None:
    with _store().read() as conn:
        row = conn.execute(
            "SELECT * FROM excel_templates WHERE template_id = ? AND is_active = 1",
            (template_id,),
        ).fetchone()
    return _template(row) if row else None


def template_name(template_id: str) -> str | None:
    """A template's name, retired or not: a report keeps naming the template
    it was made from after that template is removed."""
    with _store().read() as conn:
        row = conn.execute(
            "SELECT template_name FROM excel_templates WHERE template_id = ?",
            (template_id,),
        ).fetchone()
    return row["template_name"] if row else None


def template_for_shape(fingerprint: str,
                       shape_fingerprint: str | None = None) -> dict[str, Any] | None:
    """The approved template for this workbook shape, newest first.

    The exact fingerprint first. Failing that, the shape one — which is the
    same hash without the sheets' names, so a template whose tab was renamed
    is found rather than sent back for approval."""
    with _store().read() as conn:
        row = conn.execute(
            "SELECT * FROM excel_templates WHERE fingerprint = ? AND is_active = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        if row is None and shape_fingerprint:
            row = conn.execute(
                "SELECT * FROM excel_templates WHERE shape_fingerprint = ? "
                "AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
                (shape_fingerprint,),
            ).fetchone()
    return _template(row) if row else None


# ── reports ──────────────────────────────────────────────────────────────────


def _report(row) -> dict[str, Any]:
    return {
        "REPORT_ID": row["report_id"],
        "TEMPLATE_ID": row["template_id"],
        "REPORT_TITLE": row["report_title"],
        "DESCRIPTION": row["description"],
        "PARAMETERIZED_QUERY": _loads(row["parameterized_query"]),
        "SOURCE_KEY": row["source_key"],
        "CREATED_BY": row["created_by"],
        "CREATED_AT": row["created_at"],
        "RUN_COUNT": int(row["run_count"] or 0),
        "LAST_RUN_AT": row["last_run_at"],
    }


def save_report(record: dict[str, Any]) -> None:
    now = _now()
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO excel_reports (report_id, template_id, report_title, "
            "description, parameterized_query, source_key, created_by, created_at, "
            "run_count, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1) "
            "ON CONFLICT (report_id) DO UPDATE SET "
            "template_id = excluded.template_id, report_title = excluded.report_title, "
            "description = excluded.description, "
            "parameterized_query = excluded.parameterized_query, "
            "source_key = excluded.source_key, is_active = 1",
            (
                record["report_id"], record["template_id"], record["report_title"],
                record.get("description"),
                json.dumps(record.get("parameterized_query") or {}),
                record.get("source_key") or "", record.get("created_by"), now,
            ),
        )


def read_report(report_id: str) -> dict[str, Any] | None:
    with _store().read() as conn:
        row = conn.execute(
            "SELECT * FROM excel_reports WHERE report_id = ? AND is_active = 1",
            (report_id,),
        ).fetchone()
    return _report(row) if row else None


def knows_report(report_id: str) -> bool:
    """Whether this report is ours, retired ones included."""
    with _store().read() as conn:
        return conn.execute(
            "SELECT 1 FROM excel_reports WHERE report_id = ?", (report_id,)
        ).fetchone() is not None


def list_reports(source_key: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Reports, newest first, optionally only this source's."""
    where, args = ["is_active = 1"], []
    if source_key:
        where.append("source_key = ?")
        args.append(source_key)
    with _store().read() as conn:
        rows = conn.execute(
            "SELECT * FROM excel_reports WHERE " + " AND ".join(where)
            + " ORDER BY created_at DESC LIMIT ?",
            (*args, int(limit)),
        ).fetchall()
    return [_report(row) for row in rows]


def record_run(report_id: str) -> tuple[int, str | None]:
    """Count one run. Returns (run_count, last_run_at) after it."""
    now = _now()
    with _store().write() as conn:
        conn.execute(
            "UPDATE excel_reports SET run_count = run_count + 1, last_run_at = ? "
            "WHERE report_id = ? AND is_active = 1",
            (now, report_id),
        )
        row = conn.execute(
            "SELECT run_count FROM excel_reports WHERE report_id = ?", (report_id,)
        ).fetchone()
    return (int(row["run_count"]) if row else 0), now


def deactivate_report(report_id: str) -> bool:
    with _store().write() as conn:
        return conn.execute(
            "UPDATE excel_reports SET is_active = 0 WHERE report_id = ? AND is_active = 1",
            (report_id,),
        ).rowcount > 0
