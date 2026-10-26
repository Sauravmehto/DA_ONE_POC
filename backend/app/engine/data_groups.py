"""Data groups: which people may see which databases, folders, dashboards and reports.

A data group is a business area (Payments, Logistics, ...). It has MEMBERS —
Snowflake users, or Snowflake roles whose holders all belong — and RESOURCES
assigned to it. A person sees a resource when they share a group with it.

Three tables hold it all (sql/data_groups.sql):

    MCP_DATA_GROUPS           GROUP_ID, NAME, DESCRIPTION
    MCP_DATA_GROUP_MEMBERS    GROUP_ID, MEMBER_TYPE (USER|ROLE), MEMBER_NAME
    MCP_DATA_GROUP_RESOURCES  GROUP_ID, RESOURCE_TYPE, RESOURCE_ID, IS_MAIN

WHERE A RESOURCE BELONGS
------------------------
Assigning every dashboard by hand would not survive the first week, so most
resources inherit. A resource is looked up along a CHAIN, most specific first:

    dashboard on Snowflake     dashboard -> its database
    dashboard on an upload     dashboard -> the upload's folder
    report                     report -> the database its SQL reads
    conversation               its dashboard, else its database or folder
    dataset (one upload)       its folder

The first link with any assignment decides: its groups are the resource's
groups, and its main group is where lists file it. Assigning a dashboard
directly therefore REPLACES what it would inherit rather than adding to it —
one rule, readable from the admin page without working out a union.

A resource with nothing assigned anywhere along its chain is ungrouped, and
ungrouped is visible to everyone. Access control only removes what an admin
has filed somewhere; it never hides something nobody has thought about yet.

ENFORCEMENT
-----------
Reading the rules and enforcing them are separate. With DATA_GROUPS_ENFORCED
off, lists are filed by main group but nothing is hidden, so the grouping can be
checked before anyone loses sight of anything. Admins are never filtered.

If the tables cannot be read — not created yet, or the role lacks SELECT — the
feature is treated as not set up and lists keep their old grouping by database.
With enforcement on, the same failure refuses the request instead: an access
rule that cannot be read must not become "no rules".

The rules are cached per process for CACHE_SECONDS. Production runs four
workers; a change made on one reaches the others within that time.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.engine import data_group_store

log = logging.getLogger(__name__)

#: What can be assigned to a group. A dataset is not listed: it always sits in
#: a folder, and filing the folder is what an admin means.
RESOURCE_TYPES = ("database", "folder", "dashboard", "report")
#: A member is a person. It used to be a person OR a warehouse role, from when
#: these rules lived in the warehouse and its role names were the only names
#: the app had. An organisation with more than one database has no single set
#: of roles to key off, and a person who signs in with a password has none at
#: all — so a group holds people, and only people.
MEMBER_TYPES = ("USER",)

#: How long one process trusts the rules it read.
CACHE_SECONDS = 30

NAME_MAX = 120
DESCRIPTION_MAX = 500

#: One link of a chain: (resource type, resource id). An id of None is skipped,
#: so a dashboard whose database is unknown simply has a shorter chain.
Link = tuple[str, "str | None"]


class DataGroupError(ValueError):
    """A request the rules refuse: an unknown group, a duplicate name."""


class AccessRulesUnavailable(RuntimeError):
    """The rules could not be read while enforcement needs them."""


@dataclass(frozen=True)
class Group:
    group_id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class Member:
    group_id: str
    member_type: str
    member_name: str


@dataclass(frozen=True)
class Assignment:
    group_id: str
    resource_type: str
    resource_id: str
    is_main: bool = False


@dataclass(frozen=True)
class Placement:
    """Where a resource belongs, as far as access and filing are concerned."""

    group_ids: frozenset[str] = frozenset()
    main_group_id: str | None = None
    #: The link that decided it; None when the resource is ungrouped.
    decided_by: tuple[str, str] | None = None

    @property
    def grouped(self) -> bool:
        return bool(self.group_ids)


def normalise_id(resource_type: str, resource_id: str | None) -> str | None:
    """The stored form of a resource id. Database names are case-insensitive in
    Snowflake and stored upper case; every other id is an opaque token."""
    if resource_id is None:
        return None
    value = str(resource_id).strip()
    if not value:
        return None
    return value.upper() if resource_type == "database" else value


def normalise_member(member_type: str, member_name: str) -> tuple[str, str]:
    kind = str(member_type or "USER").strip().upper()
    if kind not in MEMBER_TYPES:
        raise DataGroupError(f"A group holds people: member type must be USER, "
                             f"not {member_type!r}.")
    name = str(member_name or "").strip().upper()
    if not name:
        raise DataGroupError("A member needs a name.")
    return kind, name


@dataclass(frozen=True)
class Snapshot:
    """The rules as read at one moment. `configured` is False when the tables
    could not be read, which means the feature is not set up."""

    configured: bool
    groups: tuple[Group, ...] = ()
    members: tuple[Member, ...] = ()
    assignments: tuple[Assignment, ...] = ()
    _by_resource: dict[tuple[str, str], list[Assignment]] = field(
        default_factory=dict, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        index: dict[tuple[str, str], list[Assignment]] = {}
        known = {g.group_id for g in self.groups}
        for item in self.assignments:
            if item.group_id in known:
                index.setdefault((item.resource_type, item.resource_id), []).append(item)
        self._by_resource.update(index)

    def group(self, group_id: str | None) -> Group | None:
        return next((g for g in self.groups if g.group_id == group_id), None)

    def group_name(self, group_id: str | None) -> str | None:
        found = self.group(group_id)
        return found.name if found else None

    def groups_of(self, user: str | None) -> frozenset[str]:
        """The groups this person is in."""
        if not user:
            return frozenset()
        wanted = ("USER", user.strip().upper())
        known = {g.group_id for g in self.groups}
        return frozenset(
            m.group_id for m in self.members
            if (m.member_type, m.member_name) == wanted and m.group_id in known
        )

    def assigned(self, resource_type: str, resource_id: str | None) -> list[Assignment]:
        key = normalise_id(resource_type, resource_id)
        if key is None:
            return []
        return list(self._by_resource.get((resource_type, key), ()))

    def placement(self, chain: Iterable[Link]) -> Placement:
        """Where the first link with any assignment puts this resource."""
        for resource_type, resource_id in chain:
            found = self.assigned(resource_type, resource_id)
            if not found:
                continue
            main = next((a.group_id for a in found if a.is_main), None)
            if main is None:
                # No main marked: the first assignment made is the filing.
                main = found[0].group_id
            return Placement(
                group_ids=frozenset(a.group_id for a in found),
                main_group_id=main,
                decided_by=(resource_type, normalise_id(resource_type, resource_id) or ""),
            )
        return Placement()


@dataclass(frozen=True)
class Access:
    """One signed-in person's view of the rules."""

    snapshot: Snapshot
    user: str | None
    role: str | None
    is_admin: bool
    enforced: bool

    @property
    def filtering(self) -> bool:
        """Whether anything is hidden from this person."""
        return self.enforced and self.snapshot.configured and not self.is_admin

    @property
    def my_groups(self) -> frozenset[str]:
        return self.snapshot.groups_of(self.user)

    def allows(self, chain: Iterable[Link]) -> bool:
        if not self.filtering:
            return True
        placement = self.snapshot.placement(chain)
        return not placement.grouped or bool(placement.group_ids & self.my_groups)

    def placement(self, chain: Iterable[Link]) -> Placement:
        return self.snapshot.placement(chain)


# --- chains -------------------------------------------------------------------

FOLDER_PREFIX = "FOLDER:"
EXCEL_PREFIX = "EXCEL:"


def source_chain(source: str | None,
                 dataset_folder: Callable[[str], str | None] | None = None) -> list[Link]:
    """The chain for a chat or dashboard SOURCE: a warehouse database, an
    uploaded folder ("FOLDER:<id>") or one uploaded dataset ("EXCEL:<id>")."""
    value = (source or "").strip()
    if not value:
        return []
    upper = value.upper()
    if upper.startswith(FOLDER_PREFIX):
        return [("folder", value.split(":", 1)[1])]
    if upper.startswith(EXCEL_PREFIX):
        dataset_id = value.split(":", 1)[1]
        folder = dataset_folder(dataset_id) if dataset_folder else None
        return [("folder", folder)] if folder else []
    return [("database", value)]


def dashboard_chain(report_id: str, *, database: str | None = None,
                    source_key: str | None = None,
                    dataset_folder: Callable[[str], str | None] | None = None) -> list[Link]:
    chain: list[Link] = [("dashboard", report_id)]
    if source_key:
        chain += source_chain(source_key, dataset_folder)
    elif database:
        chain.append(("database", database))
    return chain


def report_chain(report_id: str, database: str | None) -> list[Link]:
    chain: list[Link] = [("report", report_id)]
    if database:
        chain.append(("database", database))
    return chain


# --- reading ------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: tuple[float, Snapshot] | None = None


def invalidate() -> None:
    """Forget the cached rules in this process, after a change."""
    global _cache
    with _cache_lock:
        _cache = None


def cached_snapshot() -> Snapshot | None:
    """The cached rules if still fresh, without touching Snowflake."""
    with _cache_lock:
        if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
    return None


def read_snapshot() -> Snapshot:
    """The rules, read whole from the app's own store.

    `configured` means somebody has set entitlements up — at least one group.
    A deployment with none filters nothing, which is what an empty warehouse
    table meant before, and is why a dashboard list falls back to filing by
    database rather than calling everything ungrouped."""
    groups, members, assignments = data_group_store.read_all()
    return Snapshot(
        configured=bool(groups),
        groups=tuple(
            Group(str(row["group_id"]), str(row["name"]), str(row["description"] or ""))
            for row in groups
        ),
        members=tuple(
            Member(str(row["group_id"]), str(row["member_type"]).upper(),
                   str(row["member_name"]).upper())
            for row in members
        ),
        assignments=tuple(
            Assignment(str(row["group_id"]), str(row["resource_type"]),
                       normalise_id(str(row["resource_type"]), row["resource_id"]) or "",
                       bool(row["is_main"]))
            for row in assignments
        ),
    )


def snapshot(*, required: bool = False) -> Snapshot:
    """The rules, from the cache when fresh.

    `required` is set when enforcement depends on the answer: a failure then
    raises AccessRulesUnavailable instead of reporting "not set up".
    """
    global _cache
    fresh = cached_snapshot()
    if fresh is not None and (fresh.configured or not required):
        return fresh
    try:
        loaded = read_snapshot()
    except Exception as exc:  # noqa: BLE001 - a missing table and a missing grant look alike
        if required:
            raise AccessRulesUnavailable(
                "Access rules could not be read, so this request was refused. "
                f"An administrator needs to check the data group tables: {exc}"
            ) from exc
        with _cache_lock:
            previous = _cache[1] if _cache is not None else None
        if previous is not None and previous.configured:
            # Readable a moment ago, so this is one role without the grant, not
            # a missing table: keep the rules rather than switching everyone
            # back to "not set up" for the next CACHE_SECONDS.
            return previous
        log.warning("data group tables unreadable; treating data groups as not set up: %s",
                    exc)
        loaded = Snapshot(configured=False)
    with _cache_lock:
        _cache = (time.monotonic(), loaded)
    return loaded


# --- writing (admins) ---------------------------------------------------------

def _written() -> None:
    """A change here is not visible to the other workers until their cached
    copy ages out, so forget this one's straight away."""
    invalidate()


def _fresh() -> Snapshot:
    """The rules straight from the tables, for a write that must not act on a
    stale copy."""
    invalidate()
    current = snapshot(required=True)
    return current


def _clean_name(name: str) -> str:
    value = " ".join(str(name or "").split())
    if not value:
        raise DataGroupError("A group needs a name.")
    if len(value) > NAME_MAX:
        raise DataGroupError(f"A group name must be {NAME_MAX} characters or fewer.")
    return value


def _require_group(current: Snapshot, group_id: str) -> Group:
    found = current.group(group_id)
    if found is None:
        raise DataGroupError("No such group.")
    return found


def create_group(name: str, description: str = "", *, actor: str | None = None) -> Group:
    current = _fresh()
    clean = _clean_name(name)
    if any(g.name.lower() == clean.lower() for g in current.groups):
        raise DataGroupError(f'A group named "{clean}" already exists.')
    group = Group(uuid.uuid4().hex[:16], clean, str(description or "").strip()[:DESCRIPTION_MAX])
    data_group_store.insert_group(group.group_id, group.name, group.description, actor)
    _written()
    return group


def update_group(group_id: str, *, name: str | None = None, description: str | None = None,
                 actor: str | None = None) -> Group:
    current = _fresh()
    group = _require_group(current, group_id)
    new_name = group.name if name is None else _clean_name(name)
    if any(g.name.lower() == new_name.lower() and g.group_id != group_id
           for g in current.groups):
        raise DataGroupError(f'A group named "{new_name}" already exists.')
    new_description = (group.description if description is None
                       else str(description).strip()[:DESCRIPTION_MAX])
    data_group_store.rename_group(group_id, new_name, new_description, actor)
    _written()
    return Group(group_id, new_name, new_description)


def delete_group(group_id: str) -> None:
    """Remove a group with its members and assignments. What it held becomes
    ungrouped, or falls back to what it inherits."""
    _require_group(_fresh(), group_id)
    data_group_store.delete_group(group_id)
    _written()


def add_member(group_id: str, member_type: str, member_name: str, *,
               actor: str | None = None) -> Member:
    current = _fresh()
    _require_group(current, group_id)
    kind, name = normalise_member(member_type, member_name)
    member = Member(group_id, kind, name)
    if member in current.members:
        return member
    data_group_store.add_member(group_id, kind, name, actor)
    _written()
    return member


def remove_member(group_id: str, member_type: str, member_name: str) -> None:
    kind, name = normalise_member(member_type, member_name)
    data_group_store.remove_member(group_id, kind, name)
    _written()


def _check_resource(resource_type: str, resource_id: str) -> str:
    if resource_type not in RESOURCE_TYPES:
        raise DataGroupError(
            f"Resource type must be one of {', '.join(RESOURCE_TYPES)}, not {resource_type!r}.")
    key = normalise_id(resource_type, resource_id)
    if key is None:
        raise DataGroupError("A resource needs an id.")
    return key


def assign(group_id: str, resource_type: str, resource_id: str, *, is_main: bool = False,
           actor: str | None = None) -> Assignment:
    """Put a resource in a group. The first group a resource is put in becomes
    its main group; `is_main` moves the main group here."""
    current = _fresh()
    _require_group(current, group_id)
    key = _check_resource(resource_type, resource_id)
    existing = current.assigned(resource_type, key)
    make_main = is_main or not existing
    data_group_store.save_assignment(group_id, resource_type, key,
                                     make_main=make_main, actor=actor)
    _written()
    return Assignment(group_id, resource_type, key, make_main)


def unassign(group_id: str, resource_type: str, resource_id: str) -> None:
    """Take a resource out of a group. If that was its main group, the oldest
    remaining group becomes the main one."""
    current = _fresh()
    key = _check_resource(resource_type, resource_id)
    existing = current.assigned(resource_type, key)
    removed = next((a for a in existing if a.group_id == group_id), None)
    if removed is None:
        return
    remaining = [a for a in existing if a.group_id != group_id]
    promote = (remaining[0].group_id
               if removed.is_main and remaining and not any(a.is_main for a in remaining)
               else None)
    data_group_store.drop_assignment(group_id, resource_type, key, promote=promote)
    _written()


def set_main_group(resource_type: str, resource_id: str, group_id: str | None, *,
                   actor: str | None = None) -> None:
    """File a resource under one group, keeping any other groups it is in for
    access. None removes every assignment of its own, so it inherits again."""
    key = _check_resource(resource_type, resource_id)
    if group_id is None:
        _fresh()
        data_group_store.clear_assignments(resource_type, key)
        _written()
        return
    assign(group_id, resource_type, key, is_main=True, actor=actor)


def describe(current: Snapshot) -> list[dict[str, Any]]:
    """Every group with its members and assignments, for the admin page."""
    out = []
    for group in current.groups:
        out.append({
            "group_id": group.group_id,
            "name": group.name,
            "description": group.description,
            "members": [
                {"member_type": m.member_type, "member_name": m.member_name}
                for m in current.members if m.group_id == group.group_id
            ],
            "resources": [
                {"resource_type": a.resource_type, "resource_id": a.resource_id,
                 "is_main": a.is_main}
                for a in current.assignments if a.group_id == group.group_id
            ],
        })
    return out
