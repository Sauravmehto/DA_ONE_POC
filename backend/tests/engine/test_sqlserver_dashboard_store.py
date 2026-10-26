"""SQL Server dashboards are kept in the app's SQLite file; upload dashboards
are still kept as JSON. A SQL Server dashboard left over as JSON is copied in
once and read from SQLite after that."""

from __future__ import annotations

import pytest

from app.engine import dashboard_store, file_dashboards
from app.engine import storage as storage_mod
from app.engine.dashboards import DashboardError

SQLSERVER = "SQLSERVER:c1/FINANCE_DB"
EXCEL = "EXCEL:ds1"


class _Connector:
    def dry_run_query(self, sql):
        return ["c"]

    def run_query(self, sql):
        return {"columns": ["c"], "rows": [[1]], "row_count": 1, "sql": sql}

    def session_identity(self):
        return ("ALICE", None)


def _plan(title="Revenue", sql="SELECT 1 AS c"):
    return {
        "title": title,
        "panels": [{"title": "Total", "sql": sql, "chart_type": "kpi", "x": None,
                    "y": "c", "color": None, "orientation": "v", "display": "chart"}],
        "parameters": [],
    }


@pytest.fixture
def stores(tmp_path, fresh_accounts):
    storage_mod.set_storage(storage_mod.LocalStorage(tmp_path / "storage"))
    yield tmp_path / "storage"
    storage_mod.set_storage(None)


def _save(source_key=SQLSERVER, title="Revenue"):
    plan_id = file_dashboards._cache_plan(_plan(title), source_key)
    return file_dashboards.save_plan(plan_id, title, "monthly", created_by="ALICE")["report_id"]


def test_a_sql_server_dashboard_is_saved_to_sqlite_not_json(stores):
    report_id = _save()
    assert dashboard_store.read(report_id)["report_title"] == "Revenue"
    assert storage_mod.get_json(storage_mod.file_dashboard_key(report_id)) is None
    rec = file_dashboards.read_record(report_id)
    assert (rec["version"], rec["source_type"], rec["created_by"]) == (1, "sqlserver", "ALICE")


def test_an_upload_dashboard_is_saved_to_sqlite_too(stores):
    """Uploads were the last records written as loose JSON files."""
    report_id = _save(EXCEL)
    assert dashboard_store.read(report_id)["source_key"] == EXCEL
    assert storage_mod.get_json(storage_mod.file_dashboard_key(report_id)) is None


def test_both_kinds_are_listed_together(stores):
    sql_id, file_id = _save(title="A"), _save(EXCEL, title="B")
    listed = {d["report_id"]: d["source_type"] for d in file_dashboards.list_dashboards()}
    assert listed == {sql_id: "sqlserver", file_id: "file"}
    only = file_dashboards.list_dashboards(source_key=SQLSERVER)
    assert [d["report_id"] for d in only] == [sql_id]


def test_runs_are_counted(stores):
    report_id = _save()
    file_dashboards.run_dashboard(report_id, _Connector())
    out = file_dashboards.run_dashboard(report_id, _Connector())
    assert out["run_count"] == 2
    assert dashboard_store.read(report_id)["run_count"] == 2


def test_edits_add_versions_and_a_revert_restores_an_old_plan(stores):
    report_id = _save()
    file_dashboards.update_plan(report_id, _Connector(), _plan("Revenue v2", "SELECT 2 AS c"),
                                edit_note="second query")
    file_dashboards.update_layout(report_id, [], title="Revenue v3")
    assert file_dashboards.read_record(report_id)["version"] == 3

    versions = file_dashboards.list_versions(report_id)["versions"]
    assert [(v["version"], v["is_current"]) for v in versions] == [
        (1, False), (2, False), (3, True)]
    assert versions[1]["edit_note"] == "second query"

    out = file_dashboards.revert(report_id, 1)
    assert out["version"] == 4
    rec = file_dashboards.read_record(report_id)
    assert rec["dashboard_plan"]["panels"][0]["sql"] == "SELECT 1 AS c"
    assert rec["last_edit_note"] == "Reverted to version 1"


def test_a_previewed_edit_is_not_committed_over_a_newer_version(stores, monkeypatch):
    report_id = _save()
    file_dashboards._cache_edit(report_id, {
        "plan": _plan("Edited"), "changes_description": "rename",
        "base_version": 1, "report_title": "Revenue", "description": "", "diff": {},
    })
    file_dashboards.update_layout(report_id, [], title="Someone else's rename")
    with pytest.raises(DashboardError, match="changed after the edit was previewed"):
        file_dashboards.commit_edit(report_id, "rename")
    assert file_dashboards.read_record(report_id)["version"] == 2


def test_a_removed_dashboard_is_gone(stores):
    report_id = _save()
    assert file_dashboards.deactivate(report_id)
    assert file_dashboards.read_record(report_id) is None
    assert file_dashboards.list_dashboards() == []
    assert not file_dashboards.deactivate(report_id)


def _json_sql_server_dashboard(report_id="old1"):
    """A SQL Server dashboard as it was kept before: a JSON record at
    version 3, with versions 1 and 2 archived."""
    storage_mod.put_json(storage_mod.file_dashboard_key(report_id), {
        "report_id": report_id, "version": 3, "report_title": "Old one",
        "description": "", "source_key": SQLSERVER, "source_type": "sqlserver",
        "dashboard_plan": _plan("Old one", "SELECT 3 AS c"), "created_by": "BOB",
        "created_at": "2026-09-01T10:00:00+00:00", "last_run_at": None,
        "run_count": 5, "is_active": True, "last_edit_note": "third",
    })
    for version, archived_at in ((1, "2026-09-02T10:00:00+00:00"),
                                 (2, "2026-09-03T10:00:00+00:00")):
        storage_mod.put_json(storage_mod.file_version_key(report_id, version), {
            "version": version, "dashboard_plan": _plan("Old one", f"SELECT {version} AS c"),
            "edit_note": None if version == 1 else "second", "archived_at": archived_at,
        })
    return report_id


def test_a_dashboard_written_before_is_listed_but_not_copied(stores):
    """Nothing is migrated: an older record is read where it is."""
    report_id = _json_sql_server_dashboard()
    listed = file_dashboards.list_dashboards()
    assert [(d["report_id"], d["run_count"], d["version"]) for d in listed] == [
        (report_id, 5, 3)]
    assert not dashboard_store.known(report_id), "it was copied on sight"


def test_a_dashboard_written_before_opens_as_it_is(stores):
    report_id = _json_sql_server_dashboard()
    assert file_dashboards.read_record(report_id)["created_by"] == "BOB"
    assert not dashboard_store.known(report_id), "reading it moved it"


def test_changing_an_older_dashboard_moves_it_here_and_leaves_the_file(stores):
    """A record moves when someone changes it, not before — and forward only:
    the new version lands here and the file it came from is untouched."""
    report_id = _json_sql_server_dashboard()
    file_dashboards.update_layout(report_id, [], title="Renamed")

    assert dashboard_store.read(report_id)["version"] == 4
    assert dashboard_store.version_plan(report_id, 2)["panels"][0]["sql"] == "SELECT 2 AS c"
    assert storage_mod.get_json(storage_mod.file_dashboard_key(report_id))["version"] == 3


def test_retiring_an_older_dashboard_does_not_write_to_its_file(stores):
    report_id = _json_sql_server_dashboard()
    assert file_dashboards.deactivate(report_id)
    assert file_dashboards.read_record(report_id) is None
    assert file_dashboards.list_dashboards() == []
    assert storage_mod.get_json(storage_mod.file_dashboard_key(report_id))["is_active"] is True


def test_a_dashboard_removed_here_is_not_brought_back_by_its_file(stores):
    report_id = _json_sql_server_dashboard()
    file_dashboards.read_record(report_id)
    file_dashboards.deactivate(report_id)
    assert file_dashboards.read_record(report_id) is None
    assert file_dashboards.list_dashboards() == []


# ── a server that will not answer is not a smaller dashboard ────────────────


class _Unreachable(_Connector):
    """A connector whose source is down: every panel fails the same way."""

    def dry_run_query(self, sql):
        raise RuntimeError("Login failed for user 'sa'")

    def unavailable(self, exc):
        return True


class _BadSql(_Connector):
    """A source that answers, about SQL that does not compile."""

    def dry_run_query(self, sql):
        raise RuntimeError("Invalid column name 'NOPE'")

    def unavailable(self, exc):
        return False


def test_a_database_that_will_not_answer_stops_the_edit(stores):
    report_id = _save()
    with pytest.raises(DashboardError, match="did not answer"):
        file_dashboards.update_plan(
            report_id, _Unreachable(), _plan("Revenue v2", "SELECT 2 AS c"),
            edit_note="second query",
        )
    rec = file_dashboards.read_record(report_id)
    assert rec["version"] == 1, "a version was saved without checking its panels"


def test_a_panel_the_server_rejects_is_reported_as_the_panels_own_fault(stores):
    report_id = _save()
    with pytest.raises(DashboardError, match="failed validation"):
        file_dashboards.update_plan(
            report_id, _BadSql(), _plan("Revenue v2", "SELECT NOPE AS c"),
            edit_note="second query",
        )


def test_panels_are_only_dropped_when_the_source_answered(stores):
    kept, skipped = file_dashboards._validate_panels(
        _BadSql(), _plan()["panels"]
    )
    assert kept == [] and len(skipped) == 1
    assert "failed to compile" in skipped[0]["reason"]

    with pytest.raises(DashboardError, match="did not answer"):
        file_dashboards._validate_panels(_Unreachable(), _plan()["panels"])
