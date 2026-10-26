"""Every dashboard is a record in the app's own store — spec 0004.

A dashboard is the app's own metadata: what somebody built, when, and out of
which panels. Keeping it in the customer's warehouse gave dev and prod one
shared list, meant a client without that warehouse could not save one at all,
and made every read of a record cost a warehouse round trip.

What did NOT move is the engine. A warehouse dashboard is still planned,
edited and reverted by dashboards.py, which binds date parameters against the
semantic layer and reverts by moving a pointer rather than writing a new
version. Only where the record lives changed.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.engine import dashboard_records, dashboard_store, dashboards, file_dashboards

WAREHOUSE = "FINTECH_DB"


@pytest.fixture(autouse=True)
def store(fresh_accounts):
    yield


def _plan(title: str = "Portfolio") -> dict:
    return {"title": title,
            "panels": [{"title": "By sector", "chart_type": "bar",
                        "sql": "SELECT 1 FROM FINTECH_DB.PUBLIC.L"}],
            "parameters": []}


def _saved(report_id: str = "r1", title: str = "Portfolio", database: str = WAREHOUSE):
    dashboard_records.write_saved_dashboard({
        "report_id": report_id, "report_title": title, "description": "d",
        "tags": [], "dashboard_plan": _plan(title), "created_by": "MAKER",
        "database_name": database,
    })


# ── the record ──────────────────────────────────────────────────────────────


def test_a_saved_warehouse_dashboard_is_a_row_here():
    _saved()
    rec = dashboard_records.read_saved_dashboard("r1")

    assert rec["REPORT_ID"] == "r1"
    assert rec["VERSION"] == 1
    assert rec["DASHBOARD_PLAN"]["title"] == "Portfolio"
    assert rec["DATABASE_NAME"] == WAREHOUSE
    assert dashboard_store.read("r1") is not None, "it is in the app store"


def test_an_edit_adds_a_version_and_keeps_the_old_one():
    _saved()
    written = dashboard_records.insert_dashboard_version({
        "report_id": "r1", "dashboard_plan": _plan("Portfolio v2"),
        "report_title": "Portfolio", "description": "d", "tags": [],
        "created_by": "EDITOR", "edit_note": "second panel",
    })

    assert written == 2
    assert [v["version"] for v in dashboard_store.versions("r1")] == [1, 2]
    assert dashboard_store.version_plan("r1", 1)["title"] == "Portfolio"


def test_the_maker_is_the_first_versions_author():
    """Editing inserts a version under the editor's name, so reading the
    current one hands every dashboard to whoever touched it last."""
    _saved()
    dashboard_records.insert_dashboard_version({
        "report_id": "r1", "dashboard_plan": _plan(), "report_title": "Portfolio",
        "description": "d", "tags": [], "created_by": "EDITOR", "edit_note": "e",
    })

    assert dashboard_records.dashboard_owner("r1") == "MAKER"
    assert dashboard_records.list_saved_dashboards()[0]["UPDATED_BY"] == "EDITOR"


def test_a_revert_moves_a_pointer_and_writes_nothing():
    """The warehouse engine's revert has always been a pointer move — the
    version reverted away from stays exactly where it is."""
    _saved()
    dashboard_records.insert_dashboard_version({
        "report_id": "r1", "dashboard_plan": _plan("v2"), "report_title": "Portfolio",
        "description": "d", "tags": [], "created_by": "EDITOR", "edit_note": "e",
    })

    assert dashboard_records.revert_dashboard_version("r1", 1)
    assert dashboard_records.read_saved_dashboard("r1")["VERSION"] == 1
    assert [v["version"] for v in dashboard_store.versions("r1")] == [1, 2], \
        "reverting must not write a version, nor drop one"
    assert dashboard_records.revert_dashboard_version("r1", 9) is False


def test_a_retired_dashboard_is_gone_but_not_destroyed():
    _saved()
    assert dashboard_records.deactivate_saved_dashboard("r1") == 1
    assert dashboard_records.read_saved_dashboard("r1") is None
    assert dashboard_store.known("r1"), "a soft delete keeps the history"
    assert dashboard_records.deactivate_saved_dashboard("r1") == 0


def test_running_counts_without_making_a_version():
    _saved()
    assert dashboard_records.touch_saved_dashboard_run("r1")[0] == 1
    assert dashboard_records.read_saved_dashboard("r1")["RUN_COUNT"] == 1
    assert len(dashboard_store.versions("r1")) == 1


# ── nothing reaches the warehouse ───────────────────────────────────────────


def test_no_dashboard_record_is_written_to_the_warehouse(monkeypatch):
    """The check that cannot be traded: prod is serving a demo from that table
    and dev must not appear in it."""
    from app.engine.connectors.snowflake import client as snowflake_client

    for name in ("write_saved_dashboard", "insert_dashboard_version",
                 "deactivate_saved_dashboard", "revert_dashboard_version",
                 "set_dashboard_group", "touch_saved_dashboard_run"):
        monkeypatch.setattr(
            snowflake_client, name,
            lambda *a, _n=name, **k: pytest.fail(f"{_n} reached the warehouse"),
        )
    monkeypatch.setattr(
        snowflake_client, "get_connection",
        lambda: pytest.fail("a dashboard record opened a warehouse connection"),
    )

    _saved()
    dashboard_records.insert_dashboard_version({
        "report_id": "r1", "dashboard_plan": _plan("v2"), "report_title": "P",
        "description": "", "tags": [], "created_by": "EDITOR", "edit_note": "e",
    })
    dashboard_records.set_dashboard_group("r1", "RealEstate")
    dashboard_records.revert_dashboard_version("r1", 1)
    dashboard_records.touch_saved_dashboard_run("r1")
    dashboard_records.deactivate_saved_dashboard("r1")


def test_the_engine_reaches_the_warehouse_only_to_query():
    """Planning and running still need it — that is where the data is. Records
    do not, which is why a list renders while the warehouse is down."""
    import re

    source = pathlib.Path(dashboards.__file__).read_text(encoding="utf-8")
    calls = set(re.findall(r"snowflake_client\.(\w+)", source))
    assert calls <= {"run_query", "dry_run_query", "session_identity",
                     "DASHBOARD_MAX_ROWS"}, calls


# ── which engine serves which dashboard ─────────────────────────────────────


def test_a_warehouse_dashboard_is_not_served_by_the_file_engine():
    """Being in the app store is no longer what tells them apart."""
    _saved()
    assert file_dashboards.exists("r1"), "it is a record here like any other"
    assert not file_dashboards.served_here("r1")
    assert file_dashboards.is_warehouse_source(WAREHOUSE)


def test_each_engine_lists_only_its_own():
    """Both read the same table now, so a dashboard listed by both would be
    shown to a person twice."""
    _saved("r1", "Warehouse pack")
    dashboard_store.create({
        "report_id": "r2", "version": 1, "report_title": "Server pack",
        "description": "", "tags": [], "source_key": "SQLSERVER:c1/OPS",
        "source_type": "sqlserver", "dashboard_plan": _plan(), "created_by": "MAKER",
        "created_at": "2026-01-01T00:00:00", "last_run_at": None, "run_count": 0,
        "is_active": True,
    })

    assert [d["report_id"] for d in dashboards.list_dashboards()] == ["r1"]
    assert [d["report_id"] for d in file_dashboards.list_dashboards()] == ["r2"]


# ── where a dashboard came from ─────────────────────────────────────────────


def test_the_list_says_where_each_dashboard_came_from():
    _saved("r1")
    dashboard_store.create({
        "report_id": "r2", "version": 1, "report_title": "Uploads", "description": "",
        "tags": [], "source_key": "FOLDER:abc", "source_type": "file",
        "dashboard_plan": _plan(), "created_by": "MAKER",
        "created_at": "2026-01-01T00:00:00", "last_run_at": None, "run_count": 0,
        "is_active": True,
    })

    assert dashboards.list_dashboards()[0]["source_type"] == "snowflake"
    assert file_dashboards.list_dashboards()[0]["source_type"] == "file"


def test_the_label_is_read_from_the_record_not_guessed(monkeypatch):
    """It is recorded at save time, when the source is in hand. Working it out
    again on every read means a change to a prefix relabels history."""
    dashboard_store.create({
        "report_id": "r2", "version": 1, "report_title": "Uploads", "description": "",
        "tags": [], "source_key": "FOLDER:abc", "source_type": "sqlserver",
        "dashboard_plan": _plan(), "created_by": "MAKER",
        "created_at": "2026-01-01T00:00:00", "last_run_at": None, "run_count": 0,
        "is_active": True,
    })
    monkeypatch.setattr(file_dashboards, "source_type",
                        lambda key: pytest.fail("the stored label was ignored"))

    assert file_dashboards.list_dashboards()[0]["source_type"] == "sqlserver"


def test_an_edit_does_not_change_where_it_came_from():
    _saved()
    dashboard_records.insert_dashboard_version({
        "report_id": "r1", "dashboard_plan": _plan("v2"), "report_title": "P",
        "description": "", "tags": [], "created_by": "EDITOR", "edit_note": "e",
    })
    assert dashboard_records.read_saved_dashboard("r1")["SOURCE_TYPE"] == "snowflake"


# ── who is shown a warehouse dashboard ─────────────────────────


def _client(*, linked: bool):
    """A signed-in person, with or without a Snowflake account connected."""
    from fastapi.testclient import TestClient

    from app.accounts import store
    from app.api.deps import get_current_session
    from app.main import app
    from app.session.base import SessionData

    user_id = "u-linked" if linked else "u-nolink"
    with store.write() as conn:
        conn.execute(
            "INSERT INTO users (user_id, username, is_admin, is_active, created_at, "
            "failed_logins) VALUES (?, ?, 0, 1, '2026-09-24T00:00:00', 0)",
            (user_id, "LINKED" if linked else "NOLINK"),
        )
        if linked:
            conn.execute(
                "INSERT INTO credentials (user_id, kind, subject, secret, updated_at) "
                "VALUES (?, 'snowflake', 'SOMEBODY', 'encrypted', '2026-09-24T00:00:00')",
                (user_id,),
            )

    session = SessionData(session_id="s1", user_id=user_id,
                          snowflake_user="SOMEBODY", auth_mode="password")
    app.dependency_overrides[get_current_session] = lambda: session
    return TestClient(app), app


def _a_dashboard_of_each_kind():
    _saved("wh", "Warehouse pack")
    dashboard_store.create({
        "report_id": "srv", "version": 1, "report_title": "Server pack",
        "description": "", "tags": [], "source_key": "SQLSERVER:c1/OPS",
        "source_type": "sqlserver", "dashboard_plan": _plan(), "created_by": "MAKER",
        "created_at": "2026-01-01T00:00:00", "last_run_at": None, "run_count": 0,
        "is_active": True,
    })


def test_a_person_who_has_not_connected_a_warehouse_is_not_shown_its_dashboards():
    """Binding a warehouse connection used to keep these from an unlinked
    account, by accident rather than by rule — the list could not be built
    without one. Reading a record stopped needing one, so the rule has to be
    written down, or an account with no Snowflake at all sees every title,
    editor and version of dashboards it cannot open."""
    _a_dashboard_of_each_kind()
    client, app = _client(linked=False)
    try:
        with client:
            listed = client.get("/api/v1/dashboards").json()["dashboards"]
            assert [d["report_id"] for d in listed] == ["srv"]

            assert client.get("/api/v1/dashboards/wh/versions").status_code == 409
            assert client.get("/api/v1/dashboards/wh/config").status_code in (403, 409)
    finally:
        app.dependency_overrides.clear()


def test_a_connected_person_is_shown_them_even_when_the_warehouse_is_down(monkeypatch):
    """The distinction that matters: a link is local, a connection is not. A
    warehouse that cannot be reached must not empty the list."""
    from app.engine.connectors.snowflake import client as snowflake_client

    _a_dashboard_of_each_kind()
    monkeypatch.setattr(snowflake_client, "get_connection", lambda: pytest.fail(
        "listing must not need the warehouse"))

    client, app = _client(linked=True)
    try:
        with client:
            listed = client.get("/api/v1/dashboards").json()["dashboards"]
            assert sorted(d["report_id"] for d in listed) == ["srv", "wh"]
            assert client.get("/api/v1/dashboards/wh/versions").status_code == 200
    finally:
        app.dependency_overrides.clear()


# ── the copy ────────────────────────────────────────────────────────────────


def _warehouse_rows() -> list[dict]:
    """Three versions of one dashboard, as the warehouse table returns them."""
    return [
        {"REPORT_ID": "w1", "VERSION": 1, "REPORT_TITLE": "Exposure",
         "DESCRIPTION": "first", "TAGS": json.dumps(["risk"]),
         "DASHBOARD_PLAN": json.dumps(_plan("v1")), "CREATED_BY": "PRIYA",
         "CREATED_AT": "2026-01-01T00:00:00", "EDIT_NOTE": None, "IS_CURRENT": False,
         "LAST_RUN_AT": None, "RUN_COUNT": 0, "DATABASE_NAME": WAREHOUSE,
         "DATA_GROUP": None},
        {"REPORT_ID": "w1", "VERSION": 2, "REPORT_TITLE": "Exposure",
         "DESCRIPTION": "second", "TAGS": json.dumps(["risk"]),
         "DASHBOARD_PLAN": json.dumps(_plan("v2")), "CREATED_BY": "ARJUN",
         "CREATED_AT": "2026-02-01T00:00:00", "EDIT_NOTE": "added a panel",
         "IS_CURRENT": False, "LAST_RUN_AT": None, "RUN_COUNT": 2,
         "DATABASE_NAME": WAREHOUSE, "DATA_GROUP": None},
        {"REPORT_ID": "w1", "VERSION": 3, "REPORT_TITLE": "Exposure",
         "DESCRIPTION": "third", "TAGS": json.dumps(["risk"]),
         "DASHBOARD_PLAN": json.dumps(_plan("v3")), "CREATED_BY": "ARJUN",
         "CREATED_AT": "2026-03-01T00:00:00", "EDIT_NOTE": "renamed",
         "IS_CURRENT": True, "LAST_RUN_AT": "2026-03-02T00:00:00", "RUN_COUNT": 5,
         "DATABASE_NAME": WAREHOUSE, "DATA_GROUP": "RealEstate"},
    ]


def _adopt(rows, **kwargs):
    import importlib.util

    path = (pathlib.Path(__file__).resolve().parents[1] / "scripts"
            / "adopt_dashboards.py")
    spec = importlib.util.spec_from_file_location("adopt_dashboards", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.adopt(rows, **kwargs)


def test_a_copied_dashboard_arrives_with_its_whole_history():
    _adopt(_warehouse_rows(), dry_run=False)

    rec = dashboard_records.read_saved_dashboard("w1")
    assert rec["VERSION"] == 3
    assert rec["DASHBOARD_PLAN"]["title"] == "v3"
    assert rec["TAGS"] == ["risk"]
    assert rec["DATA_GROUP"] == "RealEstate"
    assert rec["RUN_COUNT"] == 5 and rec["LAST_RUN_AT"] == "2026-03-02T00:00:00"

    history = dashboard_store.versions("w1")
    assert [v["version"] for v in history] == [1, 2, 3]
    assert [v["created_by"] for v in history] == ["PRIYA", "ARJUN", "ARJUN"]
    assert [v["edit_note"] for v in history] == [None, "added a panel", "renamed"]
    assert dashboard_store.version_plan("w1", 1)["title"] == "v1"


def test_a_copied_dashboard_keeps_its_maker():
    _adopt(_warehouse_rows(), dry_run=False)
    assert dashboard_records.dashboard_owner("w1") == "PRIYA"


def test_a_copied_dashboard_resolves_to_the_same_group():
    """Entitlements follow the source, so the copy has to land on the same one
    the warehouse row did, or a dashboard changes hands on the way over."""
    from app.engine import data_groups

    _adopt(_warehouse_rows(), dry_run=False)
    rec = dashboard_store.read("w1")

    assert rec["source_key"] == WAREHOUSE
    assert data_groups.dashboard_chain("w1", source_key=rec["source_key"]) == \
        data_groups.dashboard_chain("w1", database=WAREHOUSE)


def test_the_database_is_read_from_the_title_when_the_column_is_empty():
    """The order the entitlement check has always used: the column, the title,
    then the plan's SQL."""
    rows = _warehouse_rows()
    for row in rows:
        row["DATABASE_NAME"] = None
        row["REPORT_TITLE"] = "FINTECH_DB exposure"
    _adopt(rows, dry_run=False)

    assert dashboard_store.read("w1")["source_key"] == WAREHOUSE


def test_a_dry_run_writes_nothing():
    module, (taken, skipped) = _adopt(_warehouse_rows(), dry_run=True)
    assert (taken, skipped) == (1, 0)
    assert dashboard_store.known("w1") is False


def test_running_the_copy_twice_keeps_an_edit_made_in_between():
    _adopt(_warehouse_rows(), dry_run=False)
    dashboard_records.insert_dashboard_version({
        "report_id": "w1", "dashboard_plan": _plan("edited here"),
        "report_title": "Exposure", "description": "", "tags": [],
        "created_by": "SAURABH", "edit_note": "changed after the copy",
    })

    _module, (taken, skipped) = _adopt(_warehouse_rows(), dry_run=False)

    assert (taken, skipped) == (0, 1)
    rec = dashboard_records.read_saved_dashboard("w1")
    assert rec["VERSION"] == 4
    assert rec["DASHBOARD_PLAN"]["title"] == "edited here"
    assert len(dashboard_store.versions("w1")) == 4


def test_two_rows_claiming_one_version_do_not_stop_the_copy():
    """A dashboard saved after a revert, before the warehouse client numbered
    from the highest version, has two rows both calling themselves v2. The
    history is already ambiguous — a revert to that number sets IS_CURRENT on
    both — so the copy keeps the one saved last under it and says which it did
    not keep, rather than refusing the whole dashboard or dropping one in
    silence."""
    rows = _warehouse_rows()
    stale = dict(rows[1])
    stale["CREATED_AT"] = "2026-01-15T00:00:00"
    stale["EDIT_NOTE"] = "temporary probe (will be reverted)"
    stale["DASHBOARD_PLAN"] = json.dumps(_plan("the probe"))
    rows.insert(1, stale)

    module, (taken, skipped) = _adopt(rows, dry_run=False)

    assert (taken, skipped) == (1, 0)
    assert [v["version"] for v in dashboard_store.versions("w1")] == [1, 2, 3]
    assert dashboard_store.version_plan("w1", 2)["title"] == "v2",         "the row saved last under that number is the one kept"

    _kept, dropped = module.one_row_per_version(rows)
    assert [r["EDIT_NOTE"] for r in dropped] == ["temporary probe (will be reverted)"]


def test_the_version_a_dashboard_is_on_wins_a_clash():
    """Whenever it was written: losing it would change which plan the
    dashboard shows, which is the one thing a copy must not do."""
    rows = _warehouse_rows()
    late = dict(rows[2])
    late["CREATED_AT"] = "2026-12-31T00:00:00"
    late["IS_CURRENT"] = False
    late["DASHBOARD_PLAN"] = json.dumps(_plan("not the current one"))
    rows.append(late)

    _adopt(rows, dry_run=False)

    assert dashboard_records.read_saved_dashboard("w1")["DASHBOARD_PLAN"]["title"] == "v3"


def test_a_retired_dashboard_is_not_copied_back():
    _adopt(_warehouse_rows(), dry_run=False)
    dashboard_records.deactivate_saved_dashboard("w1")

    _adopt(_warehouse_rows(), dry_run=False)

    assert dashboard_records.read_saved_dashboard("w1") is None, \
        "a dashboard deleted here must not come back on the next run"
