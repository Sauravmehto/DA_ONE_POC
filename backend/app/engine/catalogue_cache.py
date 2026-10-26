"""A source's catalogue — its tables and columns as the model is shown them —
kept in the app's store so every worker shares one read.

Reading a catalogue is what a person waits for before a chat can answer. On a
server slow to accept a login it took tens of seconds, each of the four
workers paid it separately, and every restart paid it again. Here it is read
once per deployment for as long as it is fresh.

A cache, not a record: it is re-read when it ages out, and losing the table
costs one slow read.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Callable

log = logging.getLogger(__name__)

#: How much of its life a stored catalogue may have used before it is read
#: again in the background. At 0.8 an hour-old catalogue is refreshed after 48
#: minutes, so the person asking at the hour is served the stored one rather
#: than waiting for a login.
REFRESH_AFTER = 0.8

#: Sources being read right now, so a second reader does not start its own.
_reading: set[str] = set()
_reading_lock = threading.Lock()

def _fresh_seconds() -> int:
    """How long a stored catalogue is used before the source is asked again."""
    from app.core.config import settings

    return int(settings.SOURCE_CATALOGUE_SECONDS)


def _store():
    from app.accounts import store

    return store


def _stored(source_key: str) -> tuple[str, datetime] | None:
    with _store().read() as conn:
        row = conn.execute(
            "SELECT description, read_at FROM source_catalogues WHERE source_key = ?",
            (source_key,),
        ).fetchone()
    if row is None:
        return None
    try:
        return row["description"], datetime.fromisoformat(row["read_at"])
    except ValueError:
        return None


def read(source_key: str, fresh_seconds: int | None = None) -> str | None:
    """The stored catalogue, if it was read recently enough."""
    fresh_seconds = _fresh_seconds() if fresh_seconds is None else fresh_seconds
    stored = _stored(source_key)
    if stored is None:
        return None
    description, read_at = stored
    if datetime.now(UTC) - read_at > timedelta(seconds=fresh_seconds):
        return None
    return description


def age(source_key: str) -> float | None:
    """Seconds since this was read, or None if it was never stored."""
    stored = _stored(source_key)
    if stored is None:
        return None
    return (datetime.now(UTC) - stored[1]).total_seconds()


def write(source_key: str, description: str) -> None:
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO source_catalogues (source_key, description, read_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (source_key) DO UPDATE SET "
            "description = excluded.description, read_at = excluded.read_at",
            (source_key, description, datetime.now(UTC).isoformat()),
        )


def forget(source_key: str) -> None:
    with _store().write() as conn:
        conn.execute("DELETE FROM source_catalogues WHERE source_key = ?", (source_key,))


def refresh_soon(source_key: str, reader: Callable[[], str]) -> bool:
    """Read the catalogue on a background thread and store it.

    Returns whether a read was started: one already under way is not joined by
    a second. Used to fill the store when a connection is added, and to
    replace a catalogue that is about to go stale, so nobody waits for a login
    that could have happened earlier."""
    with _reading_lock:
        if source_key in _reading:
            return False
        _reading.add(source_key)

    def run() -> None:
        try:
            write(source_key, reader())
        except Exception:  # noqa: BLE001 - a server that will not answer now
            # is not an error anybody asked about; the next reader finds out.
            log.info("Could not read the catalogue for %s", source_key, exc_info=True)
        finally:
            with _reading_lock:
                _reading.discard(source_key)

    threading.Thread(target=run, name=f"catalogue:{source_key}", daemon=True).start()
    return True


def get_or_read(source_key: str, reader: Callable[[], str],
                fresh_seconds: int | None = None) -> str:
    """The stored catalogue, or `reader()`'s, which is then stored.

    A stored catalogue that is nearly stale is handed back and replaced in the
    background, so the wait for a slow server falls on nobody.

    A failure to store is not a failure to answer: the catalogue was read, and
    the caller wants it more than the cache does."""
    fresh_seconds = _fresh_seconds() if fresh_seconds is None else fresh_seconds
    stored = _stored(source_key)
    if stored is not None:
        description, read_at = stored
        age = (datetime.now(UTC) - read_at).total_seconds()
        if age <= fresh_seconds:
            if age >= fresh_seconds * REFRESH_AFTER:
                refresh_soon(source_key, reader)
            return description

    description = reader()
    try:
        write(source_key, description)
    except Exception:  # noqa: BLE001
        log.warning("Could not store the catalogue for %s", source_key, exc_info=True)
    return description
