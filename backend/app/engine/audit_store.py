"""The audit log in the app's own store.

Every tool call, chat turn and model call is recorded here, whatever the
deployment is connected to. Until now the only audit trail was a Snowflake
table, so a client with files and a SQL Server — the case this product is
meant to serve — had none at all: no record of who asked what, and nothing for
the cost reporting to read.

The Snowflake write continues beside this one while the deployments that use
it need it. Retiring that is its own decision, not a side effect of this.

Best-effort, like the Snowflake write it sits beside: a failure here is logged
and never breaks the request it describes. An audit trail that can take the
product down is worse than one with a gap.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

#: The columns written, in the order the INSERT names them. Every one is
#: optional except the event's time and what it was.
COLUMNS = (
    "audit_user", "auth_mode", "snowflake_user", "snowflake_role", "session_id",
    "conversation_id", "tool_name", "user_question", "generated_sql", "row_count",
    "status", "error_message", "duration_ms", "client_info", "model",
    "model_calls", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens",
)


def _store():
    from app.accounts import store

    return store


def write(row: dict[str, Any]) -> None:
    """Record one event. Never raises."""
    try:
        values = tuple(row.get(name) for name in COLUMNS)
        with _store().write() as conn:
            conn.execute(
                f"INSERT INTO audit_events (event_ts, {', '.join(COLUMNS)}) "
                f"VALUES (?{', ?' * len(COLUMNS)})",
                (datetime.now(UTC).isoformat(), *values),
            )
    except Exception:  # noqa: BLE001 - auditing is best-effort
        log.warning("Could not write an audit row for %s", row.get("tool_name"),
                    exc_info=True)


def read(limit: int = 100, conversation_id: str | None = None) -> list[dict[str, Any]]:
    """Recent events, newest first. For the cost reporting and for support."""
    where, args = "", []
    if conversation_id:
        where = "WHERE conversation_id = ? "
        args.append(conversation_id)
    with _store().read() as conn:
        rows = conn.execute(
            f"SELECT event_id, event_ts, {', '.join(COLUMNS)} FROM audit_events "
            f"{where}ORDER BY event_id DESC LIMIT ?",
            (*args, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]
