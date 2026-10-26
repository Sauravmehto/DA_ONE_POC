"""Data groups in the app's own store: who may see which source.

They were three tables in the customer's warehouse, which made entitlements a
warehouse feature: a deployment without one could not say who may see
anything, a group could not span connectors because the rules lived inside
one, and reading them cost a round trip on requests that never touched the
warehouse at all.

Only the storage is here. What a group MEANS — that access is inherited from a
resource's source, that the first assigned link in a chain decides, that
ungrouped is visible — stays in data_groups.py, which is the same for every
connector and should never have to know where these rows are kept.

Each write is one transaction, because the decisions above it are composite:
moving which group a resource is filed under clears the old one and sets the
new, and half of that is worse than neither.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _store():
    from app.accounts import store

    return store


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# ── reading ──────────────────────────────────────────────────────────────────


def read_all() -> tuple[list[Any], list[Any], list[Any]]:
    """Every group, member and assignment, in one connection.

    Assignments come back oldest first: when nothing is marked as the main
    group, the first one made is the filing, and that order is what says so.
    """
    with _store().read() as conn:
        groups = conn.execute(
            "SELECT group_id, name, description FROM data_groups ORDER BY name"
        ).fetchall()
        members = conn.execute(
            "SELECT group_id, member_type, member_name FROM data_group_members"
        ).fetchall()
        assignments = conn.execute(
            "SELECT group_id, resource_type, resource_id, is_main "
            "FROM data_group_resources ORDER BY created_at"
        ).fetchall()
    return list(groups), list(members), list(assignments)


# ── groups ───────────────────────────────────────────────────────────────────


def insert_group(group_id: str, name: str, description: str,
                 actor: str | None) -> None:
    now = _now()
    with _store().write() as conn:
        conn.execute(
            "INSERT INTO data_groups (group_id, name, description, created_by, "
            "created_at, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group_id, name, description, actor, now, actor, now),
        )


def rename_group(group_id: str, name: str, description: str,
                 actor: str | None) -> None:
    with _store().write() as conn:
        conn.execute(
            "UPDATE data_groups SET name = ?, description = ?, updated_by = ?, "
            "updated_at = ? WHERE group_id = ?",
            (name, description, actor, _now(), group_id),
        )


def delete_group(group_id: str) -> None:
    """Remove a group. Its members and assignments go with it — the schema
    cascades — so what it held becomes ungrouped, or inherits again."""
    with _store().write() as conn:
        conn.execute("DELETE FROM data_group_resources WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM data_group_members WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM data_groups WHERE group_id = ?", (group_id,))


# ── members ──────────────────────────────────────────────────────────────────


def add_member(group_id: str, member_type: str, member_name: str,
               actor: str | None) -> None:
    with _store().write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO data_group_members (group_id, member_type, "
            "member_name, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (group_id, member_type, member_name, actor, _now()),
        )


def remove_member(group_id: str, member_type: str, member_name: str) -> None:
    with _store().write() as conn:
        conn.execute(
            "DELETE FROM data_group_members WHERE group_id = ? AND member_type = ? "
            "AND member_name = ?",
            (group_id, member_type, member_name),
        )


# ── what a group holds ───────────────────────────────────────────────────────


def save_assignment(group_id: str, resource_type: str, resource_id: str, *,
                    make_main: bool, actor: str | None) -> None:
    """Put a resource in a group, in one transaction.

    `make_main` files it here: every other group's claim on the same resource
    stops being the main one first, so a resource is never filed twice and
    never filed nowhere."""
    now = _now()
    with _store().write() as conn:
        if make_main:
            conn.execute(
                "UPDATE data_group_resources SET is_main = 0 "
                "WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            )
        conn.execute(
            "INSERT INTO data_group_resources (group_id, resource_type, resource_id, "
            "is_main, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (group_id, resource_type, resource_id) DO UPDATE SET "
            "is_main = excluded.is_main",
            (group_id, resource_type, resource_id, 1 if make_main else 0, actor, now),
        )


def drop_assignment(group_id: str, resource_type: str, resource_id: str, *,
                    promote: str | None = None) -> None:
    """Take a resource out of a group. `promote` names the group that becomes
    its filing when the one removed was it."""
    with _store().write() as conn:
        conn.execute(
            "DELETE FROM data_group_resources WHERE group_id = ? AND resource_type = ? "
            "AND resource_id = ?",
            (group_id, resource_type, resource_id),
        )
        if promote:
            conn.execute(
                "UPDATE data_group_resources SET is_main = 1 WHERE group_id = ? "
                "AND resource_type = ? AND resource_id = ?",
                (promote, resource_type, resource_id),
            )


def clear_assignments(resource_type: str, resource_id: str) -> None:
    """Take a resource out of every group, so it inherits again."""
    with _store().write() as conn:
        conn.execute(
            "DELETE FROM data_group_resources WHERE resource_type = ? AND resource_id = ?",
            (resource_type, resource_id),
        )


# ── the copy ─────────────────────────────────────────────────────────────────


def adopt_group(group: dict[str, Any], members: list[dict[str, Any]],
                assignments: list[dict[str, Any]]) -> bool:
    """Copy one group with everything it holds, in one transaction. Does
    nothing and returns False if this group is already here."""
    now = _now()
    with _store().write() as conn:
        written = conn.execute(
            "INSERT OR IGNORE INTO data_groups (group_id, name, description, created_by, "
            "created_at, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group["group_id"], group["name"], group.get("description") or "",
             group.get("created_by"), group.get("created_at") or now,
             group.get("updated_by"), group.get("updated_at") or now),
        ).rowcount
        if not written:
            return False
        for member in members:
            conn.execute(
                "INSERT OR IGNORE INTO data_group_members (group_id, member_type, "
                "member_name, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (group["group_id"], member["member_type"], member["member_name"],
                 member.get("created_by"), member.get("created_at") or now),
            )
        for assignment in assignments:
            conn.execute(
                "INSERT OR IGNORE INTO data_group_resources (group_id, resource_type, "
                "resource_id, is_main, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (group["group_id"], assignment["resource_type"],
                 assignment["resource_id"], 1 if assignment.get("is_main") else 0,
                 assignment.get("created_by"), assignment.get("created_at") or now),
            )
    return True


def knows_group(group_id: str) -> bool:
    with _store().read() as conn:
        return conn.execute(
            "SELECT 1 FROM data_groups WHERE group_id = ?", (group_id,)
        ).fetchone() is not None


def group_named(name: str) -> str | None:
    """The id of the group with this name, case-insensitively — the store
    holds names unique that way, where the warehouse only checked on write."""
    with _store().read() as conn:
        row = conn.execute(
            "SELECT group_id FROM data_groups WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
    return row["group_id"] if row else None
