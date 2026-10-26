"""Copy saved dashboards out of the warehouse and into the app's own store.

    python scripts/adopt_dashboards.py --dry-run          # what it would take
    python scripts/adopt_dashboards.py                    # take it
    python scripts/adopt_dashboards.py --report-id <id>   # one dashboard
    python scripts/adopt_dashboards.py --as SAURABH       # on that person's link

It reads the warehouse as a person who has connected Snowflake in the app,
using the refresh token their account already stores — there is no service
account to borrow, and this machine holds no warehouse credentials of its own.
With one linked account it uses that one; with several, name one with --as.

Run once per deployment, against that deployment's own store — which is the
point of the move: dev and prod shared one warehouse table, so a dashboard
made while testing appeared in the list somebody else was demonstrating.

THE WAREHOUSE IS READ, NEVER WRITTEN. One SELECT, no transaction, no DDL. The
table is left exactly as it is, and everything already running against it keeps
running.

Safe to run twice: a dashboard the store already holds is skipped, never
overwritten, so an edit made here after the first run survives the second.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.engine import dashboard_store  # noqa: E402
from app.engine.connectors.snowflake import client as snowflake_client  # noqa: E402

COLUMNS = [
    "REPORT_ID", "VERSION", "REPORT_TITLE", "DESCRIPTION", "TAGS",
    "DASHBOARD_PLAN", "CREATED_BY", "CREATED_AT", "EDIT_NOTE", "IS_CURRENT",
    "LAST_RUN_AT", "RUN_COUNT",
]
#: Only on a table that has had sql/dashboard_database_name.sql run against it.
GROUP_COLUMNS = ["DATABASE_NAME", "DATA_GROUP"]


def _parsed(value: Any, empty: Any) -> Any:
    """VARIANT columns arrive as JSON text from the connector."""
    if value is None or value == "":
        return empty
    return json.loads(value) if isinstance(value, str) else value


def read_warehouse(report_id: str | None = None) -> list[dict[str, Any]]:
    """Every version of every active dashboard, oldest first."""
    columns = list(COLUMNS)
    if snowflake_client._has_group_columns():
        columns += GROUP_COLUMNS
    sql = (
        f"SELECT {', '.join(columns)} FROM {snowflake_client.SAVED_DASHBOARDS_TABLE} "
        "WHERE IS_ACTIVE = TRUE "
        + ("AND REPORT_ID = %s " if report_id else "")
        + "ORDER BY REPORT_ID, VERSION"
    )
    conn = snowflake_client.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (report_id,) if report_id else None)
        names = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return [dict(zip(names, row)) for row in rows]


def _source_key(current: dict[str, Any], plan: dict[str, Any]) -> str:
    """Which source the dashboard reads.

    The same order the entitlement check has always used — the stored column,
    then the title, then the plan's own SQL — so a dashboard lands in the group
    it was already in rather than in a new one."""
    from app.engine import dashboards

    stored = (current.get("DATABASE_NAME") or "").strip()
    return (stored
            or dashboards.infer_database_from_title(current.get("REPORT_TITLE") or "")
            or dashboards._infer_database(plan)
            or "").strip().upper()


def group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["REPORT_ID"], []).append(row)
    return out


def one_row_per_version(
    versions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a dashboard's rows into the ones to keep and the ones that cannot
    be kept, because two of them claim the same version number.

    That happens on a dashboard saved after a revert, before the warehouse
    client started numbering from the highest version rather than the current
    one: reverting v23 to v22 and saving wrote a second v23 beside the first.
    The history is genuinely ambiguous — a revert to that number in the
    warehouse sets IS_CURRENT on both rows — so the copy keeps the one saved
    LAST under that number and reports the other rather than dropping it
    quietly. A version here is one plan, which is what makes a revert mean
    something.
    """
    claimed: dict[int, list[dict[str, Any]]] = {}
    for row in versions:
        claimed.setdefault(int(row["VERSION"]), []).append(row)

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for version in sorted(claimed):
        rows = claimed[version]
        if len(rows) == 1:
            kept.append(rows[0])
            continue
        # The one the dashboard is actually on, if it is one of them; failing
        # that, the one saved last under that number.
        winner = next((r for r in rows if r.get("IS_CURRENT")), None) or max(
            rows, key=lambda r: str(r.get("CREATED_AT") or ""))
        kept.append(winner)
        dropped.extend(r for r in rows if r is not winner)
    return kept, dropped


def as_record(
    versions: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """One dashboard's warehouse rows as (record, history, rows not kept)."""
    versions, dropped = one_row_per_version(versions)
    current = next((r for r in versions if r.get("IS_CURRENT")), versions[-1])
    first = versions[0]
    plan = _parsed(current.get("DASHBOARD_PLAN"), {})

    record = {
        "report_id": current["REPORT_ID"],
        "version": int(current["VERSION"]),
        "report_title": current.get("REPORT_TITLE") or current["REPORT_ID"],
        "description": current.get("DESCRIPTION") or "",
        "tags": _parsed(current.get("TAGS"), []),
        "source_key": _source_key(current, plan),
        "source_type": "snowflake",
        "database_name": (current.get("DATABASE_NAME") or "").strip() or None,
        "data_group": (current.get("DATA_GROUP") or "").strip() or None,
        "dashboard_plan": plan,
        # The maker is the FIRST version's author; CREATED_BY on later rows is
        # whoever edited it, and reading the current row would hand every
        # dashboard to its last editor.
        "created_by": first.get("CREATED_BY"),
        "created_at": str(first.get("CREATED_AT") or ""),
        "updated_at": str(current.get("CREATED_AT") or ""),
        "last_run_at": max((str(r["LAST_RUN_AT"]) for r in versions
                            if r.get("LAST_RUN_AT")), default=None),
        "run_count": max((int(r.get("RUN_COUNT") or 0) for r in versions), default=0),
        "is_active": True,
    }
    history = [
        {
            "version": int(r["VERSION"]),
            "plan": _parsed(r.get("DASHBOARD_PLAN"), {}),
            "report_title": r.get("REPORT_TITLE"),
            "description": r.get("DESCRIPTION") or "",
            "edit_note": r.get("EDIT_NOTE"),
            "created_by": r.get("CREATED_BY"),
            "created_at": str(r.get("CREATED_AT") or ""),
        }
        for r in versions
    ]
    return record, history, dropped


def adopt(rows: list[dict[str, Any]], *, dry_run: bool) -> tuple[int, int]:
    taken = skipped = 0
    for report_id, versions in sorted(group(rows).items()):
        record, history, dropped = as_record(versions)
        if dashboard_store.known(report_id):
            skipped += 1
            print(f"  skip  {report_id}  {record['report_title'][:48]!r} — already here")
            continue
        line = (f"  take  {report_id}  {record['report_title'][:48]!r}  "
                f"v{record['version']} ({len(history)} versions)  "
                f"source={record['source_key'] or '(none)'}")
        if not record["source_key"]:
            line += "  ** no database: it will not resolve to a data group **"
        print(line)
        for row in dropped:
            print(f"        not kept: a second row claiming v{row['VERSION']}, "
                  f"saved {row.get('CREATED_AT')} by {row.get('CREATED_BY')} "
                  f"— {(row.get('EDIT_NOTE') or 'no note')[:40]!r}")
        if not dry_run:
            dashboard_store.adopt_records(record, history)
        taken += 1
    return taken, skipped


def linked_accounts() -> list[tuple[str, str]]:
    """(user_id, Snowflake user) for every account that has connected one."""
    from app.accounts import store

    with store.read() as conn:
        return [(row["user_id"], row["subject"]) for row in conn.execute(
            "SELECT c.user_id, c.subject FROM credentials c JOIN users u "
            "ON u.user_id = c.user_id WHERE c.kind = 'snowflake' AND u.is_active = 1 "
            "ORDER BY c.subject"
        )]


def connection_for(username: str | None):
    """A warehouse connection on a linked account's stored token."""
    import asyncio
    import uuid

    from cryptography.fernet import Fernet

    from app.core.config import settings
    from app.core.security import TokenEncryptor
    from app.services.snowflake import SnowflakeService
    from app.session import get_session_store
    from app.session.base import SessionData

    accounts = linked_accounts()
    if not accounts:
        sys.exit("No account here has connected Snowflake. Connect one in the "
                 "app first, then run this again.")
    if username:
        accounts = [a for a in accounts if a[1].upper() == username.upper()]
        if not accounts:
            sys.exit(f"No linked account for {username!r}.")
    elif len(accounts) > 1:
        sys.exit("Several accounts have connected Snowflake. Name one with "
                 "--as: " + ", ".join(name for _id, name in accounts))

    user_id, snowflake_user = accounts[0]
    if not settings.TOKEN_ENCRYPTION_KEY:
        sys.exit("TOKEN_ENCRYPTION_KEY is not set, so the stored token cannot "
                 "be read. Set it to the value this deployment signs with.")

    service = SnowflakeService(
        settings,
        get_session_store(settings),
        TokenEncryptor(settings.TOKEN_ENCRYPTION_KEY or Fernet.generate_key().decode()),
    )
    session = SessionData(
        session_id=f"adopt-{uuid.uuid4().hex[:8]}",
        user_id=user_id,
        snowflake_user=snowflake_user,
        auth_mode="password",
    )
    print(f"reading the warehouse as {snowflake_user}")
    return snowflake_user, asyncio.run(service.get_connection(session))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be taken and write nothing")
    parser.add_argument("--report-id", help="one dashboard, by id")
    parser.add_argument("--as", dest="username",
                        help="read as this linked Snowflake user")
    args = parser.parse_args()

    snowflake_user, connection = connection_for(args.username)
    with snowflake_client.use_connection(connection, identity=(snowflake_user, None)):
        run(args)


def run(args) -> None:
    rows = read_warehouse(args.report_id)
    if not rows:
        sys.exit("No active dashboards found in the warehouse.")

    print(f"{len(rows)} version rows across {len(group(rows))} dashboards"
          + (" — DRY RUN, nothing written" if args.dry_run else ""))
    taken, skipped = adopt(rows, dry_run=args.dry_run)
    print(f"\n{taken} {'would be taken' if args.dry_run else 'taken'}, "
          f"{skipped} already here. The warehouse table is unchanged.")


if __name__ == "__main__":
    main()
