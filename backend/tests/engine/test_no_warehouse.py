"""The engine's no-warehouse binding and the web app's refusal of the shared
connection fail closed: nothing under them can open a Snowflake connection,
and leaving them puts back exactly what was there (spec 0007)."""

from __future__ import annotations

import pytest

from app.engine.connectors.snowflake import client as sc


@pytest.fixture
def shared_opened(monkeypatch) -> list[str]:
    calls: list[str] = []

    def shared():
        calls.append("shared")
        raise AssertionError("the shared Snowflake connection was opened")

    monkeypatch.setattr(sc, "_new_connection", shared)
    monkeypatch.setattr(sc, "_conn", None)
    return calls


def test_no_warehouse_refuses_before_anything_is_opened(shared_opened):
    with sc.no_warehouse(("ANA", None)):
        with pytest.raises(sc.WarehouseNotBound):
            sc.get_connection()
        assert sc.get_connection_or_none() is None
        assert sc.warehouse_permitted() is False
    assert shared_opened == []


def test_identity_under_no_warehouse_is_answered_without_connecting(shared_opened):
    with sc.no_warehouse(("ANA", "ANALYST")):
        assert sc.session_identity() == ("ANA", "ANALYST")
    with sc.no_warehouse():
        assert sc.session_identity() == (None, None)
    assert shared_opened == []


def test_leaving_no_warehouse_restores_the_binding_before_it(shared_opened):
    with sc.use_connection("outer", identity=("ANA", None)):
        with sc.no_warehouse():
            with pytest.raises(sc.WarehouseNotBound):
                sc.get_connection()
            with sc.use_connection("inner"):
                assert sc.get_connection() == "inner"
            with pytest.raises(sc.WarehouseNotBound):
                sc.get_connection()
        assert sc.get_connection() == "outer"
    assert sc._request_connection.get() is None
    assert shared_opened == []


def test_an_exception_inside_no_warehouse_still_restores_the_binding():
    with sc.use_connection("outer"):
        with pytest.raises(ValueError):
            with sc.no_warehouse():
                raise ValueError("boom")
        assert sc.get_connection() == "outer"


def test_the_web_app_refuses_the_shared_connection(shared_opened):
    sc.refuse_shared_connection()
    try:
        with pytest.raises(sc.WarehouseNotBound):
            sc.get_connection()
        assert sc.get_connection_or_none() is None
        assert sc.session_identity() == (None, None)
        assert sc.warehouse_permitted() is False
        with sc.use_connection("own"):
            assert sc.get_connection() == "own"     # a person's own still works
    finally:
        sc.allow_shared_connection()
    assert shared_opened == []


def test_the_app_refuses_it_while_running_and_gives_it_back_on_shutdown():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app):
        assert sc._shared_allowed is False
    assert sc._shared_allowed is True
