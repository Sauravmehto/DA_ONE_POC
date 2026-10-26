"""The Report Builder works on any database, not just the warehouse.

Reading a template, working out which cells are figures, mapping blocks to
queries and writing the numbers back is the same work whatever is underneath.
Only the dialect and the connection differ — so a client on SQL Server had no
Report Builder at all, and that was an accident of where the records happened
to live."""

from __future__ import annotations

import pytest

from app.engine import excel_multiblock, excel_reports, report_store

SQLSERVER = "SQLSERVER:c1/FINANCE_DB"
WAREHOUSE = "LOAN"


@pytest.fixture(autouse=True)
def store(fresh_accounts):
    yield


def _report(report_id="r1", source_key=SQLSERVER, title="Monthly pack"):
    return {
        "report_id": report_id,
        "template_id": "t1",
        "report_title": title,
        "description": "",
        "parameterized_query": {"kind": "multiblock", "block_sql": {"Data!2": "SELECT 1"}},
        "source_key": source_key,
        "created_by": "ADMIN",
    }


# ── records ─────────────────────────────────────────────────────────────────


def test_a_report_knows_which_database_it_reads(store):
    report_store.save_report(_report())
    assert report_store.read_report("r1")["SOURCE_KEY"] == SQLSERVER


def test_a_workspace_sees_only_its_own_reports(store):
    report_store.save_report(_report("r1", SQLSERVER))
    report_store.save_report(_report("r2", "SQLSERVER:c2/OPS_DB"))
    report_store.save_report(_report("r3", WAREHOUSE))

    assert [r["REPORT_ID"] for r in report_store.list_reports(source_key=SQLSERVER)] == ["r1"]
    assert len(report_store.list_reports()) == 3


def test_a_template_is_a_workbook_shape_not_a_database(store):
    """The same pack can be filled from two databases, so a template belongs to
    the shape it was approved for and nothing else."""
    report_store.save_template({
        "template_id": "t1", "template_name": "Monthly pack", "fingerprint": "fp1",
        "tab_roles": {"Data": "data"}, "formula_classification": {},
        "approved_by": "ADMIN",
    })
    assert report_store.template_for_shape("fp1")["TEMPLATE_ID"] == "t1"

    report_store.save_report(_report("r1", SQLSERVER))
    report_store.save_report(_report("r2", WAREHOUSE))
    assert {r["SOURCE_KEY"] for r in report_store.list_reports()} == {SQLSERVER, WAREHOUSE}


def test_a_renamed_tab_finds_the_template_it_was_approved_as(store):
    """A workbook whose tab was renamed hashes differently in two places at
    once, so it came back as a template nobody had approved. The shape hash —
    the same workbook without the sheets' names — is matched when the exact
    one misses."""
    report_store.save_template({
        "template_id": "t1", "template_name": "Monthly pack", "fingerprint": "fp1",
        "shape_fingerprint": "shape1", "tab_roles": {}, "formula_classification": {},
        "approved_by": "ADMIN",
    })

    assert report_store.template_for_shape("fp-renamed", "shape1")["TEMPLATE_ID"] == "t1"
    assert report_store.template_for_shape("fp1", "shape-other")["TEMPLATE_ID"] == "t1", \
        "the exact hash is still what is asked first"
    assert report_store.template_for_shape("fp-renamed", "shape-other") is None
    assert report_store.template_for_shape("fp-renamed") is None


def test_a_template_approved_before_the_shape_hash_is_still_found(store):
    """Nothing is re-approved to gain this: a template stored with no shape
    hash is found by the exact one exactly as before."""
    report_store.save_template({
        "template_id": "t0", "template_name": "Old pack", "fingerprint": "fp0",
        "tab_roles": {}, "formula_classification": {}, "approved_by": "ADMIN",
    })
    assert report_store.template_for_shape("fp0")["TEMPLATE_ID"] == "t0"
    assert report_store.read_template("t0")["SHAPE_FINGERPRINT"] is None
    assert report_store.template_for_shape("fp-other", None) is None


def test_runs_are_counted_on_the_report(store):
    report_store.save_report(_report())
    assert report_store.record_run("r1")[0] == 1
    assert report_store.read_report("r1")["RUN_COUNT"] == 1


def test_a_retired_report_is_gone_from_the_list(store):
    report_store.save_report(_report())
    assert report_store.deactivate_report("r1")
    assert report_store.list_reports() == []
    assert report_store.knows_report("r1"), "it is still ours, just retired"


# ── the dialect is the database's ───────────────────────────────────────────


def test_a_sql_server_report_is_capped_the_way_sql_server_caps(store, monkeypatch):
    """LIMIT is not T-SQL. A cap written the warehouse's way would fail on the
    server, or worse, be dropped and return everything."""
    ran: list[str] = []

    class _Connector:
        name = "sqlserver"
        dialect = "tsql"

        def owns(self, key):
            return key.upper().startswith("SQLSERVER:")

        def run_query(self, sql):
            ran.append(sql)
            return {"columns": ["N"], "rows": [[1]], "row_count": 1, "sql": sql}

    monkeypatch.setattr(
        "app.engine.connectors.registry.for_source", lambda key: _Connector()
    )
    execute = excel_multiblock.executor_for(SQLSERVER, row_cap=100)
    execute("SELECT N FROM dbo.GL")

    assert ran, "nothing ran"
    assert "TOP" in ran[0].upper(), f"not T-SQL: {ran[0]}"
    assert "LIMIT" not in ran[0].upper()


def test_a_warehouse_report_is_unchanged(store, monkeypatch):
    ran: list[str] = []

    class _Connector:
        name = "snowflake"
        dialect = "snowflake"

        def run_query(self, sql):
            ran.append(sql)
            return {"columns": ["N"], "rows": [[1]], "row_count": 1, "sql": sql}

    monkeypatch.setattr(
        "app.engine.connectors.registry.for_source", lambda key: _Connector()
    )
    excel_multiblock.executor_for("", row_cap=100)("SELECT N FROM T")
    assert "LIMIT" in ran[0].upper()


def test_one_connection_serves_a_whole_run(store, monkeypatch):
    """A run used to open a connection per block. On a server where logging in
    costs seconds, that is the difference between a report and a timeout."""
    logins: list[str] = []

    class _Connector:
        name = "sqlserver"
        dialect = "tsql"

        def run_query(self, sql):
            return {"columns": ["N"], "rows": [[1]], "row_count": 1, "sql": sql}

    def for_source(key):
        logins.append(key)
        return _Connector()

    monkeypatch.setattr("app.engine.connectors.registry.for_source", for_source)
    execute = excel_multiblock.executor_for(SQLSERVER, row_cap=100)
    for _ in range(5):
        execute("SELECT N FROM dbo.GL")

    assert len(logins) == 1, f"the connector was resolved {len(logins)} times"


def test_the_row_cap_notice_counts_on_the_same_database(store, monkeypatch):
    """'Showing the first N of M' has to be M from the report's own database."""
    asked: list[tuple[str, str]] = []

    class _Connector:
        name = "sqlserver"
        dialect = "tsql"

        def run_query(self, sql):
            asked.append((sql, "count" if "COUNT(*)" in sql.upper() else "rows"))
            if "COUNT(*)" in sql.upper():
                return {"columns": ["N"], "rows": [[4200]], "row_count": 1, "sql": sql}
            return {"columns": ["N"], "rows": [[1], [2]], "row_count": 2, "sql": sql}

    monkeypatch.setattr(
        "app.engine.connectors.registry.for_source", lambda key: _Connector()
    )
    result = excel_multiblock.executor_for(SQLSERVER, row_cap=2)("SELECT N FROM dbo.GL")

    assert result["row_cap"] == 2
    assert result["total_rows"] == 4200, "the count did not reach the report's database"
    assert [kind for _sql, kind in asked] == ["rows", "count"]


def test_a_count_that_fails_does_not_fail_the_report(store, monkeypatch):
    class _Connector:
        name = "sqlserver"
        dialect = "tsql"

        def run_query(self, sql):
            if "COUNT(*)" in sql.upper():
                raise RuntimeError("the server said no")
            return {"columns": ["N"], "rows": [[1]], "row_count": 1, "sql": sql}

    monkeypatch.setattr(
        "app.engine.connectors.registry.for_source", lambda key: _Connector()
    )
    result = excel_multiblock.executor_for(SQLSERVER, row_cap=1)("SELECT N FROM dbo.GL")
    assert result["total_rows"] is None
    assert result["rows"] == [[1]]


def test_the_count_asks_the_reports_own_database(store, monkeypatch):
    seen: list[str] = []

    class _Connector:
        name = "sqlserver"
        dialect = "tsql"

        def run_query(self, sql):
            return {"columns": ["N"], "rows": [[7]], "row_count": 1, "sql": sql}

    def for_source(key):
        seen.append(key)
        return _Connector()

    monkeypatch.setattr("app.engine.connectors.registry.for_source", for_source)
    assert excel_reports.count_total_rows("SELECT 1", SQLSERVER) == 7
    assert seen == [SQLSERVER]


# ── the list of databases a report can be built on ──────────────────────────


def test_a_workspace_is_offered_its_own_databases(store, monkeypatch):
    """The Report Builder and the Semantic Builder ask the same question, so
    they ask it in one place."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_session
    from app.engine import db_connections
    from app.main import app
    from app.session.base import SessionData

    async def yes(session):
        return True

    monkeypatch.setattr("app.api.sources.session_is_admin", yes)
    monkeypatch.setattr(
        db_connections, "list_connections",
        lambda: [db_connections.SavedConnection("c1", "DA SQL Server", "sqlserver",
                                                "db.example", 1433, "reader")],
    )
    monkeypatch.setattr(db_connections, "databases_for", lambda _id: ["FINANCE_DB"])

    session = SessionData(session_id="s1", snowflake_user="ADMIN", auth_mode="password")
    app.dependency_overrides[get_current_session] = lambda: session
    try:
        with TestClient(app) as client:
            listed = client.get("/api/v1/sources?connector=sqlserver&connection=c1").json()
    finally:
        app.dependency_overrides.pop(get_current_session, None)

    assert [(s["key"], s["connector"], s["can_edit"]) for s in listed] == [
        (SQLSERVER, "sqlserver", True)]
    assert listed[0]["display_name"] == "FINANCE_DB (DA SQL Server)"
