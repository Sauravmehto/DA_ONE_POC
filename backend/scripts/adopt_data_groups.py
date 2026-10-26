"""Copy data groups out of the warehouse and into the app's own store.

    python scripts/adopt_data_groups.py --dry-run   # what it would take
    python scripts/adopt_data_groups.py             # take it
    python scripts/adopt_data_groups.py --as SAURABH

Run once per deployment. A deployment that never had a warehouse does not run
this at all — it starts with no groups, which filters nothing, and an
administrator creates what they need.

THE WAREHOUSE IS READ, NEVER WRITTEN. Three SELECTs, no transaction, no DDL.

Safe to run twice: a group the store already holds is skipped, never
overwritten, so a change made here after the first run survives the second.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.engine import data_group_store  # noqa: E402
from app.engine.connectors.snowflake import client as snowflake_client  # noqa: E402

GROUPS_TABLE = "LOAN.PUBLIC.MCP_DATA_GROUPS"
MEMBERS_TABLE = "LOAN.PUBLIC.MCP_DATA_GROUP_MEMBERS"
RESOURCES_TABLE = "LOAN.PUBLIC.MCP_DATA_GROUP_RESOURCES"


def _rows(cursor, sql: str) -> list[dict[str, Any]]:
    cursor.execute(sql)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def read_warehouse() -> tuple[list[dict], list[dict], list[dict]]:
    conn = snowflake_client.get_connection()
    cursor = conn.cursor()
    try:
        groups = _rows(cursor, f"SELECT * FROM {GROUPS_TABLE} ORDER BY NAME")
        members = _rows(cursor, f"SELECT * FROM {MEMBERS_TABLE}")
        resources = _rows(cursor, f"SELECT * FROM {RESOURCES_TABLE} ORDER BY CREATED_AT")
    finally:
        cursor.close()
    return groups, members, resources


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _held_by(rows: list[dict], group_id: str) -> list[dict]:
    return [r for r in rows if str(r.get("GROUP_ID")) == group_id]


def adopt(groups: list[dict], members: list[dict], resources: list[dict], *,
          dry_run: bool) -> tuple[int, int]:
    taken = skipped = 0
    for group in groups:
        group_id = str(group["GROUP_ID"])
        name = str(group.get("NAME") or group_id)
        mine = _held_by(members, group_id)
        held = _held_by(resources, group_id)
        # A group holds people. A row naming a warehouse role would count for
        # nothing here, so it is reported rather than copied into silence.
        roles = [m for m in mine if str(m.get("MEMBER_TYPE") or "").upper() != "USER"]
        mine = [m for m in mine if m not in roles]

        if data_group_store.knows_group(group_id):
            skipped += 1
            print(f"  skip  {name!r} — already here")
            continue

        # The store holds names unique case-insensitively, where the warehouse
        # only checked on write. A clash means two groups people read as one.
        clash = data_group_store.group_named(name)
        if clash:
            print(f"  ** {name!r} clashes with a group already here ({clash}); "
                  f"rename one of them and run again **")
            continue

        print(f"  take  {name!r}  {len(mine)} members, {len(held)} filed")
        for role in roles:
            print(f"        not kept: the role {role.get('MEMBER_NAME')!r} — a group "
                  f"holds people now, so add them to it by name")
        if dry_run:
            taken += 1
            continue

        ok = data_group_store.adopt_group(
            {
                "group_id": group_id,
                "name": name,
                "description": _text(group.get("DESCRIPTION")) or "",
                "created_by": _text(group.get("CREATED_BY")),
                "created_at": _text(group.get("CREATED_AT")),
                "updated_by": _text(group.get("UPDATED_BY")),
                "updated_at": _text(group.get("UPDATED_AT")),
            },
            [
                {"member_type": str(m.get("MEMBER_TYPE") or "").upper(),
                 "member_name": str(m.get("MEMBER_NAME") or "").upper(),
                 "created_by": _text(m.get("CREATED_BY")),
                 "created_at": _text(m.get("CREATED_AT"))}
                for m in mine
            ],
            [
                {"resource_type": str(r.get("RESOURCE_TYPE") or ""),
                 "resource_id": str(r.get("RESOURCE_ID") or ""),
                 "is_main": bool(r.get("IS_MAIN")),
                 "created_by": _text(r.get("CREATED_BY")),
                 "created_at": _text(r.get("CREATED_AT"))}
                for r in held
            ],
        )
        taken += 1 if ok else 0
    return taken, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be taken and write nothing")
    parser.add_argument("--as", dest="username",
                        help="read as this linked Snowflake user")
    args = parser.parse_args()

    # The same link the dashboard copy reads on — see adopt_dashboards.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "adopt_dashboards", pathlib.Path(__file__).with_name("adopt_dashboards.py"))
    dashboards_copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboards_copy)

    user, connection = dashboards_copy.connection_for(args.username)
    with snowflake_client.use_connection(connection, identity=(user, None)):
        groups, members, resources = read_warehouse()

    if not groups:
        sys.exit("No data groups found in the warehouse.")

    print(f"{len(groups)} groups, {len(members)} members, {len(resources)} filings"
          + (" — DRY RUN, nothing written" if args.dry_run else ""))
    taken, skipped = adopt(groups, members, resources, dry_run=args.dry_run)
    print(f"\n{taken} {'would be taken' if args.dry_run else 'taken'}, "
          f"{skipped} already here. The warehouse tables are unchanged.")


if __name__ == "__main__":
    main()
