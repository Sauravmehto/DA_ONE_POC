"""Uploads, their folders and their dashboards work with no warehouse at all —
and touch nothing in Snowflake when there is one.

A client with spreadsheets and no Snowflake is the case this product is meant
to serve. It is also the case that used to fail: an upload's record was a row
in a Snowflake table, so the Files workspace could not even list what was
uploaded yesterday."""

from __future__ import annotations

import pytest

from app.engine import file_dashboards, upload_store
from app.engine import storage as storage_mod
from app.engine.excel_sources import Dataset, DatasetFile, SourceTable

EXCEL = "EXCEL:ds1"
FOLDER = "FOLDER:c1"


@pytest.fixture
def no_warehouse(tmp_path, fresh_accounts, monkeypatch):
    """A deployment with no Snowflake: any reach for one fails the way it does
    when nothing is configured."""
    import app.engine.excel_sources as sources

    def refuse(*args, **kwargs):
        raise RuntimeError("Missing required environment variable: SNOWFLAKE_USER")

    monkeypatch.setattr(sources, "_snowflake_available", lambda: False)
    monkeypatch.setattr(
        "app.engine.connectors.snowflake.client.get_connection", refuse
    )
    storage_mod.set_storage(storage_mod.LocalStorage(tmp_path / "storage"))
    yield
    storage_mod.set_storage(None)


def _dataset(dataset_id="ds1", name="Rent roll", collection_id="") -> Dataset:
    return Dataset(
        dataset_id=dataset_id, name=name, fingerprint="fp1", sheet_count=2,
        tables=[SourceTable(name="RENTS", row_count=10, columns=["UNIT", "RENT"],
                            sheets=["Rents"])],
        files=[DatasetFile(filename="rents.xlsx", row_count=10)],
        database_size=2048, created_at="2026-09-01T10:00:00+00:00",
        collection_id=collection_id,
    )


class _Connector:
    def dry_run_query(self, sql):
        return ["c"]

    def run_query(self, sql):
        return {"columns": ["c"], "rows": [[1]], "row_count": 1, "sql": sql}

    def session_identity(self):
        return ("ALICE", None)


def _plan(title="Rents", sql="SELECT 1 AS c"):
    return {
        "title": title,
        "panels": [{"title": "Total", "sql": sql, "chart_type": "kpi", "x": None,
                    "y": "c", "color": None, "orientation": "v", "display": "chart"}],
        "parameters": [],
    }


# ── the records ─────────────────────────────────────────────────────────────


def test_an_upload_is_recorded_and_listed_without_a_warehouse(no_warehouse):
    import app.engine.excel_sources as sources

    sources._save_dataset(_dataset())
    listed = sources.list_datasets()
    assert [(d.dataset_id, d.name, d.sheet_count) for d in listed] == [
        ("ds1", "Rent roll", 2)]
    assert listed[0].tables[0].name == "RENTS"
    assert listed[0].files[0].filename == "rents.xlsx"
    assert sources.get_dataset("ds1").database_size == 2048


def test_an_upload_can_be_retired(no_warehouse):
    import app.engine.excel_sources as sources

    sources._save_dataset(_dataset())
    assert sources.delete_dataset("ds1")
    assert sources.list_datasets() == []
    assert not sources.delete_dataset("ds1")


def test_a_folder_keeps_its_name(no_warehouse):
    import app.engine.excel_sources as sources

    sources._write_collection_name("c1", "Q3 pack")
    assert sources._collection_names()["c1"][0] == "Q3 pack"
    assert sources.deactivate_collection("c1")
    assert "c1" not in sources._collection_names()


def test_what_a_shape_means_is_remembered(no_warehouse):
    import app.engine.excel_sources as sources

    mapping = {"regions": [{"fields": [{"concept": "rent"}, {"concept": "unit"}]}]}
    sources._save_definition("fp1", mapping, "ALICE")
    assert sources.load_definition("fp1") == mapping
    assert sources.known_concepts() == ["rent", "unit"]
    assert upload_store.load_definition("fp1") == mapping


# ── dashboards on an upload ─────────────────────────────────────────────────


def test_a_dashboard_on_an_upload_is_saved_run_and_edited(no_warehouse):
    plan_id = file_dashboards._cache_plan(_plan(), EXCEL)
    report_id = file_dashboards.save_plan(plan_id, "Rents", created_by="ALICE")["report_id"]

    rec = file_dashboards.read_record(report_id)
    assert (rec["source_type"], rec["version"]) == ("file", 1)

    file_dashboards.run_dashboard(report_id, _Connector())
    assert file_dashboards.read_record(report_id)["run_count"] == 1

    file_dashboards.update_plan(report_id, _Connector(), _plan("Rents v2", "SELECT 2 AS c"),
                                edit_note="second query")
    assert file_dashboards.read_record(report_id)["version"] == 2
    assert [v["version"] for v in file_dashboards.list_versions(report_id)["versions"]] == [1, 2]


def test_nothing_about_a_dashboard_is_written_to_storage(no_warehouse, monkeypatch):
    """Plans, edits and records all used to go to object storage. A dashboard
    has no reason to touch it now that the records are in the app store."""
    written: list[str] = []
    real_put = storage_mod.put_json
    monkeypatch.setattr(
        storage_mod, "put_json",
        lambda key, value: (written.append(key), real_put(key, value))[1],
    )

    plan_id = file_dashboards._cache_plan(_plan(), FOLDER)
    report_id = file_dashboards.save_plan(plan_id, "Rents", created_by="ALICE")["report_id"]
    file_dashboards.run_dashboard(report_id, _Connector())
    file_dashboards.update_layout(report_id, [], title="Renamed")
    file_dashboards.deactivate(report_id)

    assert written == [], f"still writing to storage: {written}"


def test_a_plan_proposed_by_one_worker_is_saved_by_another(no_warehouse):
    """Each worker has its own memory; the proposal has to outlive that."""
    plan_id = file_dashboards._cache_plan(_plan(), EXCEL)
    file_dashboards._PLAN_CACHE.clear()   # a different worker

    saved = file_dashboards.save_plan(plan_id, "Rents", created_by="ALICE")
    assert saved["saved"] is True


# ── the demo's protection ───────────────────────────────────────────────────


def test_no_upload_path_ever_opens_a_snowflake_connection(no_warehouse):
    """Prod and dev share the Snowflake tables and a demo is running, so
    nothing here may reach for one — a failure of this test is the thing that
    would put a customer's data at risk, not a slow page."""
    import app.engine.excel_sources as sources

    sources._save_dataset(_dataset(collection_id="c1"))
    sources._write_collection_name("c1", "Q3 pack")
    sources._save_definition("fp1", {"regions": []}, "ALICE")
    sources.list_datasets()
    sources.get_dataset("ds1")
    sources.load_definition("fp1")
    sources.known_concepts()
    sources._collection_names()

    plan_id = file_dashboards._cache_plan(_plan(), EXCEL)
    report_id = file_dashboards.save_plan(plan_id, "Rents", created_by="ALICE")["report_id"]
    file_dashboards.run_dashboard(report_id, _Connector())
    file_dashboards.list_dashboards()
    file_dashboards.update_layout(report_id, [], title="Renamed")
    file_dashboards.list_versions(report_id)
    file_dashboards.revert(report_id, 1)
    file_dashboards.deactivate(report_id)
    # Reaching for Snowflake raises in this fixture; getting here means nothing did.
