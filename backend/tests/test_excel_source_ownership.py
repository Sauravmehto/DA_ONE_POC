"""Uploaded folders belong to whoever uploaded them (spec 0012).

Before this, excel_collections/excel_datasets had no owner at all, so any
signed-in account could list and operate on every other account's files.
These are the real path — password accounts signed in through the API, no
patching of the access checks — the same shape test_access_without_snowflake
uses for data_groups, for the same reason: patching the check is how a bug in
the check itself goes unseen.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.accounts import service as accounts
from app.engine import excel_sources as sources
from app.engine import storage as storage_mod
from app.engine.excel_sources import Dataset, DatasetFile, SourceTable
from app.main import app

PASSWORD = "correct horse battery"
ANA = "ana@example.com"
BEN = "ben@example.com"


def _dataset(dataset_id: str, folder: str, owner: str) -> Dataset:
    return Dataset(
        dataset_id=dataset_id, name=dataset_id, fingerprint="fp", sheet_count=1,
        tables=[SourceTable(name="T", row_count=1, columns=["C"], sheets=["S"])],
        files=[DatasetFile(filename=f"{dataset_id}.xlsx", row_count=1, uploaded_by=owner)],
        database_size=1, created_at="2026-10-01T00:00:00+00:00", collection_id=folder,
        owner=owner,
    )


def _sign_in(client: TestClient, email: str) -> None:
    response = client.post("/api/v1/auth/password", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def test_each_account_sees_only_its_own_folders(fresh_accounts, tmp_path, monkeypatch):
    storage_mod.set_storage(storage_mod.LocalStorage(tmp_path / "storage"))
    accounts.create_account(ANA, PASSWORD, "Ana")
    accounts.create_account(BEN, PASSWORD, "Ben")
    sources._save_dataset(_dataset("ds_ana", "f_ana", ANA))
    sources._save_dataset(_dataset("ds_ben", "f_ben", BEN))

    with TestClient(app) as client:
        _sign_in(client, ANA)
        listed = client.get("/api/v1/excel-sources").json()
        assert [c["collection_id"] for c in listed] == ["f_ana"]

    with TestClient(app) as client:
        _sign_in(client, BEN)
        listed = client.get("/api/v1/excel-sources").json()
        assert [c["collection_id"] for c in listed] == ["f_ben"]

    storage_mod.set_storage(None)


def test_one_account_cannot_reach_another_s_dataset_by_id(fresh_accounts, tmp_path):
    storage_mod.set_storage(storage_mod.LocalStorage(tmp_path / "storage"))
    accounts.create_account(ANA, PASSWORD, "Ana")
    accounts.create_account(BEN, PASSWORD, "Ben")
    sources._save_dataset(_dataset("ds_ana", "f_ana", ANA))

    with TestClient(app) as client:
        _sign_in(client, BEN)
        assert client.get("/api/v1/excel-sources/ds_ana").status_code == 404
        assert client.delete("/api/v1/excel-sources/ds_ana").status_code == 404
        assert client.patch(
            "/api/v1/excel-sources/collections/f_ana", json={"name": "Taken"}
        ).status_code == 404
        assert client.delete("/api/v1/excel-sources/collections/f_ana").status_code == 404

    with TestClient(app) as client:
        _sign_in(client, ANA)
        assert client.get("/api/v1/excel-sources/ds_ana").status_code == 200

    storage_mod.set_storage(None)


def test_a_folder_with_no_recorded_owner_is_invisible_to_everyone(fresh_accounts, tmp_path):
    """A folder from before spec 0012 that the backfill could not resolve
    (differing or missing uploaded_by) stays owner="" — admin-only until
    claimed, which today means nobody sees it through these routes."""
    storage_mod.set_storage(storage_mod.LocalStorage(tmp_path / "storage"))
    accounts.create_account(ANA, PASSWORD, "Ana")
    sources._save_dataset(
        Dataset(
            dataset_id="ds_orphan", name="ds_orphan", fingerprint="fp", sheet_count=1,
            tables=[SourceTable(name="T", row_count=1, columns=["C"], sheets=["S"])],
            files=[DatasetFile(filename="ds_orphan.xlsx", row_count=1)],
            database_size=1, created_at="2026-10-01T00:00:00+00:00",
            collection_id="f_orphan", owner="",
        )
    )

    with TestClient(app) as client:
        _sign_in(client, ANA)
        assert client.get("/api/v1/excel-sources").json() == []
        assert client.get("/api/v1/excel-sources/ds_orphan").status_code == 404

    storage_mod.set_storage(None)
