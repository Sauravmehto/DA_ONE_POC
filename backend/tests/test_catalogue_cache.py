"""A source's catalogue is read once and shared, and a slow or failing server
costs one read rather than every worker's first question."""

from __future__ import annotations

import pytest

from app.engine import catalogue_cache


@pytest.fixture(autouse=True)
def store(fresh_accounts):
    yield


def test_the_second_reader_does_not_ask_the_server(store):
    reads = []

    def read_it():
        reads.append(1)
        return "Table dbo.GL: ID int"

    first = catalogue_cache.get_or_read("SQLSERVER:c1/FIN", read_it)
    second = catalogue_cache.get_or_read("SQLSERVER:c1/FIN", read_it)
    assert first == second == "Table dbo.GL: ID int"
    assert len(reads) == 1, "a worker that had not read it went to the server anyway"


def test_it_is_read_again_once_it_is_stale(store):
    catalogue_cache.get_or_read("SQLSERVER:c1/FIN", lambda: "old")
    fresh = catalogue_cache.get_or_read("SQLSERVER:c1/FIN", lambda: "new", fresh_seconds=0)
    assert fresh == "new"


def test_each_source_is_kept_apart(store):
    catalogue_cache.get_or_read("SQLSERVER:c1/FIN", lambda: "finance")
    catalogue_cache.get_or_read("SQLSERVER:c1/OPS", lambda: "ops")
    assert catalogue_cache.read("SQLSERVER:c1/FIN") == "finance"
    assert catalogue_cache.read("SQLSERVER:c1/OPS") == "ops"


def test_a_removed_connection_takes_its_catalogue_with_it(store):
    catalogue_cache.get_or_read("SQLSERVER:c1/FIN", lambda: "finance")
    catalogue_cache.forget("SQLSERVER:c1/FIN")
    assert catalogue_cache.read("SQLSERVER:c1/FIN") is None


def test_a_server_that_refuses_is_not_remembered_as_an_answer(store):
    with pytest.raises(RuntimeError):
        catalogue_cache.get_or_read("SQLSERVER:c1/FIN", _refuse)
    assert catalogue_cache.read("SQLSERVER:c1/FIN") is None


def _refuse() -> str:
    raise RuntimeError("TDS server connection timed out")


# ── read ahead, so nobody waits for a login ──────────────────────────────────


def _wait_for(check, seconds: float = 5.0) -> bool:
    """Background reads run on their own thread; give one a moment to finish."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def test_a_catalogue_can_be_read_before_anyone_asks(store):
    assert catalogue_cache.refresh_soon("SQLSERVER:c1/FIN", lambda: "read ahead")
    assert _wait_for(lambda: catalogue_cache.read("SQLSERVER:c1/FIN") == "read ahead")


def test_a_second_reader_waits_for_the_first_rather_than_logging_in_too(store):
    import threading

    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "finance"

    assert catalogue_cache.refresh_soon("SQLSERVER:c1/FIN", slow)
    started.wait(5)
    assert not catalogue_cache.refresh_soon("SQLSERVER:c1/FIN", slow), "logged in twice"
    release.set()
    assert _wait_for(lambda: catalogue_cache.read("SQLSERVER:c1/FIN") == "finance")


def _aged(source_key: str, seconds: float) -> None:
    """Back-date a stored catalogue, as though it had been read that long ago."""
    from datetime import UTC, datetime, timedelta

    from app.accounts import store as app_store

    when = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    with app_store.write() as conn:
        conn.execute(
            "UPDATE source_catalogues SET read_at = ? WHERE source_key = ?",
            (when, source_key),
        )


def test_a_nearly_stale_catalogue_is_served_now_and_replaced_behind_it(store):
    catalogue_cache.write("SQLSERVER:c1/FIN", "old")
    _aged("SQLSERVER:c1/FIN", 900)  # 90% through a 1000-second life

    served = catalogue_cache.get_or_read(
        "SQLSERVER:c1/FIN", lambda: "new", fresh_seconds=1000
    )
    assert served == "old", "the person waited for a login they did not need"
    assert _wait_for(lambda: catalogue_cache.read("SQLSERVER:c1/FIN") == "new"), (
        "the catalogue was left to go stale, so the next person pays for it"
    )


def test_a_fresh_catalogue_is_left_alone(store):
    catalogue_cache.write("SQLSERVER:c1/OPS", "current")
    reads = []
    served = catalogue_cache.get_or_read(
        "SQLSERVER:c1/OPS", lambda: reads.append(1) or "new", fresh_seconds=1000
    )
    assert served == "current"
    assert not _wait_for(lambda: reads, seconds=0.5), "read again for no reason"


def test_a_stale_catalogue_is_read_before_it_is_used(store):
    catalogue_cache.write("SQLSERVER:c1/FIN", "old")
    _aged("SQLSERVER:c1/FIN", 5000)
    assert catalogue_cache.get_or_read(
        "SQLSERVER:c1/FIN", lambda: "new", fresh_seconds=1000
    ) == "new"
