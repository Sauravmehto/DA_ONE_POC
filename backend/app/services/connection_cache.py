"""One warehouse connection per signed-in session, shared by its requests.

A connection leaves the cache in one of two ways, and they are deliberately
different:

    retire(session_id)   the session's OAuth token was refreshed. Requests
                         already holding the connection keep using it; it is
                         closed once RETIRE_GRACE_SECONDS have passed.
    evict(session_id)    logout or a role switch. Closed immediately: the
                         identity it carries is no longer the one in use.

Retiring used to close the connection on the spot. A token refresh fires from
whichever request of that user arrives inside the last two minutes of the
token's life, so it closed the connection out from under anything else still
running — a dashboard build several minutes in failed every remaining query
with "250002 (08003): Connection is closed". An established Snowflake session
does not depend on the access token it was opened with, so there is nothing to
gain from closing it early.
"""

import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

#: How long a retired connection stays open for the requests still using it.
#: Longer than any single chat turn — twelve tool rounds, each of which may
#: stream a whole dashboard plan — and far shorter than Snowflake's idle session
#: timeout, so a retired session is still ours to close when this runs out.
RETIRE_GRACE_SECONDS = 30 * 60

#: A connection unused for longer than this is checked before it is handed out
#: again. Snowflake ends an idle session on its side, and the connection object
#: goes on looking open: only the next query finds out, as "Authentication
#: token has expired". Checking after an idle spell turns that into a quiet
#: reconnect instead of a failed page.
IDLE_CHECK_SECONDS = 10 * 60


def _is_closed(connection: object) -> bool:
    """Whether a cached connection can no longer be used.

    A connection whose check itself fails is treated as closed: handing it out
    only moves the failure into the next query.
    """
    check = getattr(connection, "is_closed", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:  # noqa: BLE001
        return True


def _is_alive(connection: object) -> bool:
    """Whether the server still answers on this connection: one trivial query.
    Anything without a cursor is taken at its word (is_closed decides)."""
    open_cursor = getattr(connection, "cursor", None)
    if not callable(open_cursor):
        return True
    cursor = None
    try:
        cursor = open_cursor()
        cursor.execute("SELECT 1", timeout=10)
        return True
    except Exception:  # noqa: BLE001 - any failure means "do not reuse"
        return False
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:  # noqa: BLE001
                pass


def _close_quietly(connection: object) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - a dead connection often fails to close
            pass


class ConnectionCache:
    def __init__(
        self,
        grace_seconds: float = RETIRE_GRACE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        idle_check_seconds: float = IDLE_CHECK_SECONDS,
    ) -> None:
        self._connections: dict[str, object] = {}
        #: When each session's connection was last handed out.
        self._last_used: dict[str, float] = {}
        self._idle_check_seconds = idle_check_seconds
        #: (close-at, connection) for connections retired but maybe still in use.
        self._retired: list[tuple[float, object]] = []
        self._grace_seconds = grace_seconds
        self._clock = clock
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, factory: Callable[[], T]) -> T:
        """The session's connection, opening a new one if there is none usable.

        A connection that has been closed by any route — the server ending the
        session, the network dropping it — is replaced here rather than handed
        out again. Nothing else notices, so without this check one closed
        connection failed every request of that session until the token next
        refreshed.
        """
        with self._lock:
            due = self._take_due()
            connection = self._connections.get(session_id)
            if connection is not None and _is_closed(connection):
                del self._connections[session_id]
                connection = None
            idle = (
                connection is not None
                and self._clock() - self._last_used.get(session_id, self._clock())
                > self._idle_check_seconds
            )
        for stale in due:
            _close_quietly(stale)

        # Checked outside the lock: a round trip to Snowflake must not hold up
        # every other session's request.
        if idle and not _is_alive(connection):
            with self._lock:
                if self._connections.get(session_id) is connection:
                    del self._connections[session_id]
            _close_quietly(connection)

        with self._lock:
            connection = self._connections.get(session_id)
            if connection is None or _is_closed(connection):
                connection = factory()
                self._connections[session_id] = connection
            self._last_used[session_id] = self._clock()
        return connection  # type: ignore[return-value]

    def retire(self, session_id: str) -> None:
        """Stop handing out this session's connection, without closing it yet."""
        with self._lock:
            connection = self._connections.pop(session_id, None)
            self._last_used.pop(session_id, None)
            if connection is not None:
                self._retired.append((self._clock() + self._grace_seconds, connection))
            due = self._take_due()
        for stale in due:
            _close_quietly(stale)

    def evict(self, session_id: str) -> None:
        """Remove and close this session's connection now."""
        with self._lock:
            connection = self._connections.pop(session_id, None)
            self._last_used.pop(session_id, None)
            due = self._take_due()
        if connection is not None:
            _close_quietly(connection)
        for stale in due:
            _close_quietly(stale)

    def _take_due(self) -> list[object]:
        """Retired connections whose grace period is over. Call with the lock held.

        Swept whenever the cache is used rather than on a timer thread: a
        connection overstays only while nobody is using the app at all, and
        then it is closed by the next request or by process exit.
        """
        now = self._clock()
        due = [conn for close_at, conn in self._retired if close_at <= now]
        if due:
            self._retired = [(t, c) for t, c in self._retired if t > now]
        return due


connection_cache = ConnectionCache()
