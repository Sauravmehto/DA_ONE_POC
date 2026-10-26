"""A dashboard plan waiting to be saved, or an edit waiting to be confirmed.

Short-lived by nature: one of these lives from "here is what I propose" until
the person accepts it or walks away. It is not a record of anything — losing
one costs a re-plan, not data.

It is shared rather than held in a worker's memory because the worker that
proposes a plan is rarely the one that saves it, and it is here rather than in
object storage because a dashboard has no other reason to touch storage now
that the records are in the app store.

Old ones are cleared as they are noticed, so nothing has to sweep them.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

#: How long a proposal is kept. Long enough to think about over lunch, short
#: enough that the table never becomes a place things accumulate.
KEEP_HOURS = 24


def _store():
    from app.accounts import store

    return store


def put(plan_id: str, kind: str, source_key: str, payload: dict[str, Any],
        created_by: str | None = None) -> None:
    now = datetime.now(UTC)
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO pending_plans (plan_id, kind, source_key, payload, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (plan_id) DO UPDATE SET "
            "kind = excluded.kind, source_key = excluded.source_key, "
            "payload = excluded.payload, created_at = excluded.created_at",
            (plan_id, kind, source_key, json.dumps(payload), created_by,
             now.isoformat()),
        )
        conn.execute(
            "DELETE FROM pending_plans WHERE created_at < ?",
            ((now - timedelta(hours=KEEP_HOURS)).isoformat(),),
        )


def get(plan_id: str) -> dict[str, Any] | None:
    with _store().read() as conn:
        row = conn.execute(
            "SELECT payload FROM pending_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        log.warning("A pending plan could not be read back: %s", plan_id)
        return None


def drop(plan_id: str) -> None:
    with _store().write() as conn:
        conn.execute("DELETE FROM pending_plans WHERE plan_id = ?", (plan_id,))
