"""Semantic layers live in the app's own file, one per source, whatever
connector serves it. The TOMLs in config/metadata are the starting point,
copied in once."""

from __future__ import annotations

import pytest

from app.engine.metadata_loader import MetadataStore
from app.semantic import AppStoreRepository
from app.semantic.store import DRAFT, PUBLISHED
from app.semantic import store

SQLSERVER = "SQLSERVER:c1/FINANCE_DB"

LOAN = b'''database = "LOAN"
display_name = "Loan Portfolio"
description = "Servicing data"
join_rules = """
- Never join the two servicer feeds.
"""
'''

FINANCE = b'''database = "SQLSERVER:c1/FINANCE_DB"
display_name = "Finance"
description = "Ledger extracts"

[tables."dbo.GL"]
description = "One row per posting."
'''


@pytest.fixture
def files(tmp_path, fresh_accounts, monkeypatch):
    """An empty store, and a metadata folder holding one Snowflake layer."""
    folder = tmp_path / "metadata"
    folder.mkdir()
    (folder / "loan.toml").write_bytes(LOAN)
    from app.core.config import settings

    monkeypatch.setattr(type(settings), "metadata_dir", property(lambda _: folder))
    return folder


def test_the_files_are_copied_in_once(files):
    assert store.published_sources() == ["LOAN"]
    assert store.read("LOAN") == LOAN

    # A file added later is not copied: the app is the authority from now on.
    (files / "extra.toml").write_bytes(b'database = "EXTRA"\n')
    assert store.published_sources() == ["LOAN"]


def test_a_layer_can_be_written_for_any_connectors_source(files):
    store.publish(SQLSERVER, FINANCE, "ADMIN")
    assert sorted(store.published_sources()) == ["LOAN", SQLSERVER]
    assert store.read(SQLSERVER) == FINANCE


def test_a_draft_is_invisible_until_it_is_published(files):
    store.write_draft(SQLSERVER, FINANCE, "ADMIN")
    assert store.read(SQLSERVER, PUBLISHED) is None
    assert store.read(SQLSERVER, DRAFT) == FINANCE

    store.publish(SQLSERVER, FINANCE, "ADMIN")
    assert store.read(SQLSERVER, PUBLISHED) == FINANCE
    assert store.read(SQLSERVER, DRAFT) is None, "the draft became the published layer"


def test_every_publish_is_kept_as_a_version(files):
    store.publish(SQLSERVER, FINANCE, "ADMIN")
    store.publish(SQLSERVER, FINANCE + b'\nnotes = "second"\n', "SAURABH")
    versions = store.versions(SQLSERVER)
    assert [(v["version"], v["published_by"]) for v in versions] == [
        (2, "SAURABH"), (1, "ADMIN")]
    assert store.version_document(SQLSERVER, 1) == FINANCE


def test_the_document_comes_back_exactly_as_it_was_written(files):
    store.publish(SQLSERVER, FINANCE, "ADMIN")
    assert store.read(SQLSERVER).decode() == FINANCE.decode(), "comments or spacing changed"


def test_another_workers_publish_is_seen_without_a_restart(files):
    """Each worker has its own loader cache; only the marker tells it to
    reload, and a publish elsewhere never calls invalidate() here."""
    loader = MetadataStore()
    assert loader.documented_databases() == ["LOAN"]

    AppStoreRepository("ADMIN").publish(SQLSERVER, FINANCE)

    assert loader.documented_databases() == ["LOAN", SQLSERVER]
    assert "One row per posting." in loader.build_context(SQLSERVER)


def test_a_broken_layer_does_not_take_the_others_offline(files):
    store.publish(SQLSERVER, b'database = "x"\nbroken = "no closing quote\n', "ADMIN")
    loader = MetadataStore()
    assert loader.documented_databases() == ["LOAN"]
    assert SQLSERVER in loader.failed_configs()
