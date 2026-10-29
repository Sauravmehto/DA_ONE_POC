"""An upload returns as soon as the bytes are in, and reading continues after.

The hosted proxy drops a request that stays open (spec 0014). This checks the
request itself does not wait on ingest, and that only the person who started
the job can see it.
"""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from app.accounts import service as accounts
from app.engine.excel_sources import BatchResult
from app.main import app

PASSWORD = "correct horse battery"
ANA = "ana@example.com"
BEN = "ben@example.com"


def _sign_in(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/password", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def test_upload_returns_a_job_and_finishes_in_the_background(fresh_accounts, monkeypatch):
    from app.api.v1.routes import excel_sources as routes

    started = threading.Event()
    release = threading.Event()

    def slow(*_args, on_progress=None, **_kwargs):
        if on_progress is not None:
            on_progress(40, "Reading notes.txt")
        started.set()
        assert release.wait(5)
        return BatchResult()

    monkeypatch.setattr(routes.engine_sources, "ingest_files", slow)
    accounts.create_account(ANA, PASSWORD, "Ana")
    accounts.create_account(BEN, PASSWORD, "Ben")

    with TestClient(app) as client:
        _sign_in(client, ANA)
        assert client.get("/api/v1/excel-sources/ping").status_code == 200
        began = time.perf_counter()
        response = client.post(
            "/api/v1/excel-sources/upload",
            files=[("files", ("notes.txt", b"a short note", "text/plain"))],
        )
        assert response.status_code == 200, response.text
        assert time.perf_counter() - began < 2
        job_id = response.json()["job_id"]
        assert started.wait(2)

        running = client.get(f"/api/v1/excel-sources/uploads/{job_id}")
        assert running.status_code == 200
        assert running.json()["status"] == "running"
        assert running.json()["percent"] == 40

        release.set()
        finished = {}
        for _ in range(50):
            finished = client.get(f"/api/v1/excel-sources/uploads/{job_id}").json()
            if finished["status"] != "running":
                break
            time.sleep(0.05)
        assert finished["status"] == "complete"
        assert finished["result"]["datasets"] == []

    with TestClient(app) as client:
        _sign_in(client, BEN)
        assert client.get(f"/api/v1/excel-sources/uploads/{job_id}").status_code == 404
