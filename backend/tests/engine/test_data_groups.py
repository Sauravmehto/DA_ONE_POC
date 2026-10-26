"""Data groups: where a resource belongs, who may see it, and the admin writes."""

from __future__ import annotations

import pytest

from app.engine import data_groups as dg
from app.engine.data_groups import Access, Assignment, Group, Member, Snapshot

PAY, LOG, CREDIT = "g-pay", "g-log", "g-credit"


def rules(*assignments: Assignment, members: tuple[Member, ...] = ()) -> Snapshot:
    return Snapshot(
        configured=True,
        groups=(Group(PAY, "Payments"), Group(LOG, "Logistics"), Group(CREDIT, "PrivateCredit")),
        members=members or (
            Member(PAY, "USER", "NYSA"),
            Member(LOG, "USER", "NYSA"),
            Member(CREDIT, "USER", "SAURABH"),
        ),
        assignments=assignments,
    )


DB_PAY = Assignment(PAY, "database", "PAYMENTS_DB", True)
DB_CREDIT = Assignment(CREDIT, "database", "FINTECH_DB", True)


def access(snapshot: Snapshot, user="NYSA", role="ANALYST", *, admin=False, enforced=True):
    return Access(snapshot, user, role, is_admin=admin, enforced=enforced)


# --- placement ----------------------------------------------------------------

def test_a_dashboard_inherits_its_databases_group():
    placement = rules(DB_PAY).placement(dg.dashboard_chain("d1", database="payments_db"))
    assert placement.group_ids == {PAY}
    assert placement.main_group_id == PAY
    assert placement.decided_by == ("database", "PAYMENTS_DB")


def test_assigning_a_dashboard_replaces_what_it_inherits():
    snapshot = rules(DB_PAY, Assignment(LOG, "dashboard", "d1", True))
    placement = snapshot.placement(dg.dashboard_chain("d1", database="PAYMENTS_DB"))
    assert placement.group_ids == {LOG}
    assert placement.decided_by == ("dashboard", "d1")


def test_the_main_group_is_the_marked_one_else_the_first():
    marked = rules(Assignment(PAY, "report", "r1"), Assignment(LOG, "report", "r1", True))
    assert marked.placement([("report", "r1")]).main_group_id == LOG
    unmarked = rules(Assignment(PAY, "report", "r1"), Assignment(LOG, "report", "r1"))
    assert unmarked.placement([("report", "r1")]).main_group_id == PAY


def test_nothing_assigned_anywhere_is_ungrouped():
    placement = rules(DB_PAY).placement(dg.dashboard_chain("d1", database="HOSPITALITY_DB"))
    assert not placement.grouped and placement.main_group_id is None


def test_an_assignment_to_a_deleted_group_counts_for_nothing():
    snapshot = rules(Assignment("gone", "database", "PAYMENTS_DB", True))
    assert not snapshot.placement([("database", "PAYMENTS_DB")]).grouped


def test_membership_is_the_person_and_nothing_else():
    """A group holds people. It used to hold warehouse roles too, from when
    these rules lived in the warehouse — an organisation with more than one
    database has no single set of roles to key off, and somebody who signs in
    with a password has none at all."""
    snapshot = rules()
    assert snapshot.groups_of("nysa") == {PAY, LOG}
    assert snapshot.groups_of("SAURABH") == {CREDIT}
    assert snapshot.groups_of("NOBODY") == frozenset()
    assert snapshot.groups_of(None) == frozenset()


def test_a_warehouse_role_is_not_a_member():
    """A row naming one counts for nothing, so a rule copied from a warehouse
    that had them does not quietly keep working."""
    snapshot = rules(members=(Member(CREDIT, "ROLE", "CREDIT_ANALYST"),))
    assert snapshot.groups_of("CREDIT_ANALYST") == frozenset()
    with pytest.raises(dg.DataGroupError, match="holds people"):
        dg.normalise_member("ROLE", "CREDIT_ANALYST")


def test_sources_chain_to_their_folder_or_database():
    assert dg.source_chain("FOLDER:abc") == [("folder", "abc")]
    assert dg.source_chain("EXCEL:ds1", lambda d: "abc") == [("folder", "abc")]
    assert dg.source_chain("EXCEL:ds1", lambda d: None) == []
    assert dg.source_chain("fintech_db") == [("database", "fintech_db")]
    assert dg.source_chain(None) == []


def test_an_upload_dashboard_inherits_its_folder():
    snapshot = rules(Assignment(LOG, "folder", "abc", True))
    chain = dg.dashboard_chain("f1", source_key="EXCEL:ds1", dataset_folder=lambda d: "abc")
    assert snapshot.placement(chain).main_group_id == LOG


# --- access -------------------------------------------------------------------

CHAIN_CREDIT = dg.dashboard_chain("d9", database="FINTECH_DB")
CHAIN_PAY = dg.dashboard_chain("d8", database="PAYMENTS_DB")
CHAIN_NONE = dg.dashboard_chain("d7", database="HOSPITALITY_DB")


def test_a_member_sees_their_groups_and_ungrouped_things_only():
    view = access(rules(DB_PAY, DB_CREDIT))
    assert view.filtering
    assert view.allows(CHAIN_PAY)
    assert not view.allows(CHAIN_CREDIT)
    assert view.allows(CHAIN_NONE)


def test_a_member_sees_their_groups_and_not_the_others():
    view = access(rules(DB_PAY, DB_CREDIT), user="SAURABH")
    assert view.allows(CHAIN_CREDIT) and not view.allows(CHAIN_PAY)


@pytest.mark.parametrize("kwargs", [
    {"admin": True},
    {"enforced": False},
])
def test_admins_and_unenforced_rules_hide_nothing(kwargs):
    view = access(rules(DB_PAY, DB_CREDIT), **kwargs)
    assert not view.filtering
    assert view.allows(CHAIN_CREDIT)


def test_rules_that_are_not_set_up_hide_nothing():
    view = access(Snapshot(configured=False))
    assert not view.filtering and view.allows(CHAIN_CREDIT)


# --- reading ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_cache():
    dg.invalidate()
    yield
    dg.invalidate()


def test_unreadable_rules_refuse_when_required_and_read_as_unset_otherwise(monkeypatch):
    def fail():
        raise RuntimeError("Object 'MCP_DATA_GROUPS' does not exist or not authorized.")

    monkeypatch.setattr(dg, "read_snapshot", fail)
    with pytest.raises(dg.AccessRulesUnavailable):
        dg.snapshot(required=True)
    assert dg.snapshot(required=False).configured is False
    # Still refused when required, even with an "unset" answer cached.
    with pytest.raises(dg.AccessRulesUnavailable):
        dg.snapshot(required=True)


def test_one_role_without_the_grant_does_not_unset_the_rules_for_everyone(monkeypatch):
    monkeypatch.setattr(dg, "read_snapshot", lambda: rules(DB_PAY))
    dg.snapshot()
    monkeypatch.setattr(dg, "CACHE_SECONDS", -1)  # force a re-read

    def fail():
        raise RuntimeError("not authorized")

    monkeypatch.setattr(dg, "read_snapshot", fail)
    assert dg.snapshot().configured is True


def test_rules_are_cached_until_invalidated(monkeypatch):
    calls = []

    def read():
        calls.append(1)
        return rules(DB_PAY)

    monkeypatch.setattr(dg, "read_snapshot", read)
    dg.snapshot()
    dg.snapshot()
    assert len(calls) == 1
    dg.invalidate()
    dg.snapshot()
    assert len(calls) == 2


# --- writing ------------------------------------------------------------------
#
# These used to assert the SQL sent to the warehouse — statement by statement,
# table name and all. The rules are the app's own records now, so what is
# worth holding is what a person ends up with: which group a resource is filed
# under, and which groups can see it.


@pytest.fixture
def store(fresh_accounts):
    """An empty rule set, and no cached copy of a previous test's."""
    dg.invalidate()
    yield
    dg.invalidate()


def _groups(store) -> tuple[str, str]:
    return (dg.create_group("Payments").group_id,
            dg.create_group("Logistics").group_id)


def test_a_group_round_trips(store):
    group = dg.create_group("Payments", "Everything payments", actor="ADMIN")
    current = dg.snapshot()

    assert current.configured
    assert [(g.group_id, g.name, g.description) for g in current.groups] == [
        (group.group_id, "Payments", "Everything payments")]


def test_nothing_set_up_is_not_configured(store):
    """An empty store is a deployment nobody has filed anything in, which
    filters nothing — the same as an empty table before."""
    assert dg.snapshot().configured is False


def test_a_duplicate_group_name_is_refused(store):
    dg.create_group("Payments")
    with pytest.raises(dg.DataGroupError, match="already exists"):
        dg.create_group(" payments ")


def test_the_first_group_a_resource_gets_is_its_main_group(store):
    pay, _log = _groups(store)
    assignment = dg.assign(pay, "database", "payments_db")

    assert assignment.is_main and assignment.resource_id == "PAYMENTS_DB"
    placement = dg.snapshot().placement([("database", "PAYMENTS_DB")])
    assert placement.main_group_id == pay and placement.group_ids == {pay}


def test_making_a_group_main_clears_the_others(store):
    pay, log = _groups(store)
    dg.assign(pay, "database", "PAYMENTS_DB")
    dg.assign(log, "database", "PAYMENTS_DB", is_main=True)

    placement = dg.snapshot().placement([("database", "PAYMENTS_DB")])
    assert placement.main_group_id == log, "filed under the newest, not both"
    assert placement.group_ids == {pay, log}, "and still visible to the first"


def test_an_extra_group_is_access_only(store):
    pay, log = _groups(store)
    dg.assign(pay, "database", "PAYMENTS_DB")

    assert dg.assign(log, "database", "PAYMENTS_DB").is_main is False
    assert dg.snapshot().placement([("database", "PAYMENTS_DB")]).main_group_id == pay


def test_removing_the_main_group_promotes_the_next(store):
    pay, log = _groups(store)
    dg.assign(pay, "database", "PAYMENTS_DB")
    dg.assign(log, "database", "PAYMENTS_DB")

    dg.unassign(pay, "database", "PAYMENTS_DB")

    placement = dg.snapshot().placement([("database", "PAYMENTS_DB")])
    assert placement.group_ids == {log} and placement.main_group_id == log


def test_clearing_the_filing_lets_a_resource_inherit_again(store):
    pay, _log = _groups(store)
    dg.assign(pay, "dashboard", "d1")
    dg.set_main_group("dashboard", "d1", None)

    assert not dg.snapshot().placement([("dashboard", "d1")]).grouped


def test_a_member_is_added_once_and_removed(store):
    pay, _log = _groups(store)
    dg.add_member(pay, "USER", "nysa")
    dg.add_member(pay, "user", " NYSA ")

    assert dg.snapshot().groups_of("NYSA") == {pay}

    dg.remove_member(pay, "USER", "NYSA")
    assert dg.snapshot().groups_of("NYSA") == frozenset()


def test_deleting_a_group_takes_its_members_and_filings_with_it(store):
    pay, _log = _groups(store)
    dg.add_member(pay, "USER", "NYSA")
    dg.assign(pay, "database", "PAYMENTS_DB")

    dg.delete_group(pay)
    current = dg.snapshot()

    assert current.group(pay) is None
    assert current.groups_of("NYSA") == frozenset()
    assert not current.placement([("database", "PAYMENTS_DB")]).grouped


def test_unknown_types_and_groups_are_refused(store):
    pay, _log = _groups(store)
    with pytest.raises(dg.DataGroupError):
        dg.assign(pay, "table", "X")
    with pytest.raises(dg.DataGroupError):
        dg.assign("nope", "database", "X")
    with pytest.raises(dg.DataGroupError):
        dg.add_member(pay, "TEAM", "X")


def test_a_change_is_not_served_from_a_stale_copy(store):
    """The rules are cached per worker; a change made through this one has to
    be visible to it immediately, or an admin sees their own edit ignored."""
    pay, _log = _groups(store)
    dg.snapshot()                      # warm the cache
    dg.assign(pay, "database", "PAYMENTS_DB")

    assert dg.snapshot().placement([("database", "PAYMENTS_DB")]).main_group_id == pay


def test_no_warehouse_is_needed_to_read_or_write(store, monkeypatch):
    """The point of the move: entitlements are the app's, so a deployment
    without a warehouse — or with one that is down — still has them."""
    from app.engine.connectors.snowflake import client as snowflake_client

    monkeypatch.setattr(snowflake_client, "get_connection", lambda: pytest.fail(
        "data groups must not need the warehouse"))

    group = dg.create_group("Payments")
    dg.add_member(group.group_id, "USER", "NYSA")
    dg.assign(group.group_id, "database", "PAYMENTS_DB")

    assert dg.snapshot().groups_of("NYSA") == {group.group_id}

# --- a group holds every kind of database -------------------------------------


def test_a_sql_server_database_can_be_filed_and_is_filtered(store, monkeypatch):
    """A group that cannot hold a SQL Server database cannot govern its
    dashboards: each one would fall through to ungrouped, and an administrator
    would be filing artefacts one by one instead of filing the source once."""
    from app.engine import db_connections

    monkeypatch.setattr(
        db_connections, "list_connections",
        lambda: [db_connections.SavedConnection(
            "c1", "DA SQL Server", "sqlserver", "db.example", 1433, "reader")])
    monkeypatch.setattr(db_connections, "databases_for", lambda _id: ["FINANCE_DB"])

    from app.api.v1.routes.data_groups import _catalog

    offered = {item["id"] for item in _catalog()["database"]}
    assert "SQLSERVER:C1/FINANCE_DB" in offered
    assert "_DEFAULT" not in offered, "a fallback config is not a database"

    group = dg.create_group("Credit Analyst")
    dg.add_member(group.group_id, "USER", "NYSA")
    dg.assign(group.group_id, "database", "SQLSERVER:c1/FINANCE_DB")

    rules_now = dg.snapshot()
    chain = dg.dashboard_chain("d1", source_key="SQLSERVER:c1/FINANCE_DB")
    assert rules_now.placement(chain).main_group_id == group.group_id

    mine = Access(rules_now, "NYSA", None, is_admin=False, enforced=True)
    theirs = Access(rules_now, "SOMEBODY", None, is_admin=False, enforced=True)
    assert mine.allows(chain)
    assert not theirs.allows(chain), "filed, so it is hidden from everyone else"


def test_an_ungrouped_source_stays_visible(store):
    """The policy, stated: you lock down what you deliberately group, and
    grouping two databases and nothing else is a complete job."""
    group = dg.create_group("Credit Analyst")
    dg.assign(group.group_id, "database", "FINTECH_DB")

    view = Access(dg.snapshot(), "SOMEBODY", None, is_admin=False, enforced=True)
    assert not view.allows(dg.dashboard_chain("d1", database="FINTECH_DB"))
    assert view.allows(dg.dashboard_chain("d2", database="LOGISTICS_DB"))

