"""Free trials: one per email address, kept apart from the account.

The trial belongs to the EMAIL, not the account row. Deleting an account and
signing up again with the same address finds the same row, so the clock does
not restart. Gmail ignores dots and "+tags" in the local part, so those are
folded out of the key: a+1@gmail.com and a.@gmail.com are one mailbox and one
trial. Addresses are not confirmed (spec 0010), so this only stops cheap reuse.

Administrators and accounts that never had a row are not on a trial; the caller
decides who is exempt, this module only answers for an address.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.accounts import store
from app.core.config import settings

_GMAIL = {"gmail.com", "googlemail.com"}


def trial_key(email: str) -> str:
    local, _, domain = email.strip().lower().partition("@")
    if domain in _GMAIL:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    else:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def _now() -> datetime:
    return datetime.now(UTC)


def start(email: str) -> None:
    """Begin a trial for this address. An address that already had one keeps
    its original dates."""
    moment = _now()
    with store.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO trials (trial_key, email, started_at, ends_at) "
            "VALUES (?, ?, ?, ?)",
            (trial_key(email), email, moment.isoformat(),
             (moment + timedelta(days=settings.TRIAL_DAYS)).isoformat()),
        )


def status(email: str | None) -> dict[str, Any] | None:
    """None when this address has no trial (so nothing limits it)."""
    if not email:
        return None
    with store.read() as conn:
        row = conn.execute(
            "SELECT started_at, ends_at, messages_used FROM trials WHERE trial_key = ?",
            (trial_key(email),),
        ).fetchone()
    if row is None:
        return None
    remaining = datetime.fromisoformat(row["ends_at"]) - _now()
    cap = settings.TRIAL_MESSAGE_CAP
    return {
        "started_at": row["started_at"],
        "ends_at": row["ends_at"],
        "days_left": max(0, -(-int(remaining.total_seconds()) // 86400)),
        "expired": remaining.total_seconds() <= 0,
        "messages_used": row["messages_used"],
        "message_cap": cap or None,
    }


def extend(email: str, days: int, by: str | None) -> dict[str, Any] | None:
    """Give this address `days` more, counted from now if it has run out."""
    current = status(email)
    if current is None:
        return None
    base = max(datetime.fromisoformat(current["ends_at"]), _now())
    with store.write() as conn:
        conn.execute(
            "UPDATE trials SET ends_at = ?, extended_by = ? WHERE trial_key = ?",
            ((base + timedelta(days=days)).isoformat(), by, trial_key(email)),
        )
    return status(email)


def use_message(email: str | None) -> bool:
    """Count one chat message against the cap. False means the cap is reached.
    No trial row, or no cap configured, is always allowed."""
    cap = settings.TRIAL_MESSAGE_CAP
    if not email or not cap:
        return True
    with store.write() as conn:
        if conn.execute("SELECT 1 FROM trials WHERE trial_key = ?",
                        (trial_key(email),)).fetchone() is None:
            return True
        updated = conn.execute(
            "UPDATE trials SET messages_used = messages_used + 1 "
            "WHERE trial_key = ? AND messages_used < ?",
            (trial_key(email), cap),
        ).rowcount
    return updated > 0
