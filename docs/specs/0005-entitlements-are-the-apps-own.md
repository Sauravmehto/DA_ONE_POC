# 0005 — Entitlements are the app's own records

**Status:** done (2026-09-24) — see Notes
**Touches:** data groups (read and write), the access check on every request,
the admin data-groups page, the app store

## Why

Who may see which database, folder, dashboard and report is decided by three
tables in the customer's warehouse — `MCP_DATA_GROUPS`,
`MCP_DATA_GROUP_MEMBERS`, `MCP_DATA_GROUP_RESOURCES`. They are the last records
the app both reads and writes there, and the only ones left that it needs on an
ordinary request rather than when somebody asks for data.

Three things follow from that, and none of them is about tidiness:

- **A client without that warehouse cannot have entitlements at all.** Every
  other kind of record moved (specs 0001, 0002, 0004), so a SQL Server or
  files-only deployment now has dashboards, reports and uploads — and no way to
  say who may see them.
- **A group cannot span connectors**, because the rules live inside one of
  them. "Fintech" as a business domain covers a Snowflake database, a SQL
  Server database and a folder of uploads; the chain that resolves access
  already handles all three uniformly, but the rows that answer it do not.
- **Reading the rules can refuse a request that has nothing to do with the
  warehouse.** With `DATA_GROUPS_ENFORCED` on, `get_access` raises 503 when the
  rules cannot be read — for every route it guards, including ones serving a
  SQL Server dashboard or an upload.

## Not in scope

This spec moves the records and changes nothing about the model. Named here
because each was discussed and deliberately left:

- **App-account membership.** A member is a `USER` or a `ROLE`, matched against
  the session's Snowflake user and role. That stays exactly as it is. (A
  password account already matches on `USER`, because `snowflake_user` carries
  the app's username for it. `ROLE` will keep matching nothing for such an
  account — a fact, not a change.)
- **Whether ungrouped stays visible.** `allows()` permits anything that
  resolves to no group. That policy is unchanged here.
- **Per-connector entitlements.** Considered and rejected: see *One model, not
  one per connector*.
- **The audit log's Snowflake half.** Already dual-written; retiring it is a
  flag, and its own decision.
- **Dropping the warehouse tables.** Not this spec, and not while the demo
  runs. Same position as 0004.

## The checklist

| | |
|---|---|
| **Reach** — how does someone get to this, from every workspace and every connector? | No new surface. The same admin page, the same groups, the same filtering on every list and every open — in every workspace, including deployments with no warehouse, which is the point. |
| **Permission** — who may do it, and what does everyone else see instead? | Unchanged. Reading and writing groups is an administrator's; everyone else sees what their groups allow. The copy is run by an administrator from the command line. One thing improves: an administrator of a warehouse-less deployment can now manage groups at all, where before the write would have had nowhere to go. |
| **Failure** — what happens when the source is slow, down, or refuses? | This is most of the value. Reading the rules becomes a local SQLite read that cannot fail for want of a warehouse, so `get_access` stops turning a warehouse outage into a 503 on every guarded route. A rules read that genuinely fails (a corrupt store) still refuses rather than letting everything through, exactly as now. |
| **Isolation** — what is scoped per workspace, per connection, per deployment, and what survives a redeploy? | Per deployment, in `APP_DB_PATH` on a pinned volume — so dev and prod stop sharing one set of rules, as they do today. A group spans connectors by design: that is what "app-level" buys. |
| **The model** — what does the AI get, and when? | Nothing new. The chat is already filtered through the same `Access`; where the rows are read from does not reach the model. |
| **Cost** | Lower. Today the rules are read over the signed-in person's warehouse connection and cached for 30 seconds per worker, so four workers re-read them 120 times an hour between them, each read costing a round trip. A local read costs neither a connection nor a login, and the cache can stay as it is or be shortened. |
| **Migration** | A copy, as with dashboards: the three tables are read once and written to the app store, keeping every `GROUP_ID`, membership and assignment so nothing changes hands. Re-runnable; a group the store already holds is skipped. The warehouse is read, never written. Deployments with no warehouse simply start empty. |

## Behaviour

### The model does not change

Worth stating plainly, because it is what makes the admin burden reasonable
and it is easy to lose in a move.

- **Access is inherited, not granted per artefact.** `placement()` walks a
  resource's chain — `[("dashboard", id), ("database", "FINTECH_DB")]` — and
  stops at the first link with an assignment. A dashboard nobody assigned is
  filed under its database's group, automatically, the moment it is made.
- **So an administrator groups sources, not artefacts.** Databases and folders:
  a small, slowly changing set. Dashboards, reports and chats follow. Assigning
  a dashboard directly stays as the override it is today.
- **Ungrouped is visible.** Grouping two sensitive databases and nothing else
  is a complete job, not a half-done one.
- **Membership is by user or role**, so "everyone in the Fintech team" is one
  row rather than a list of names to maintain.

All of that already works. This spec must not change any of it, and the
acceptance checks are written to catch it if it does.

### One model, not one per connector

Rejected, recorded so it is not re-argued: entitlements per connector would
mean the same team needs a group under each ("Fintech (Snowflake)", "Fintech
(files)") with the same people in both, a resource that spans two connectors
has no single home, and `placement()` is re-implemented per vendor — the
per-connector one-off this codebase has spent three specs removing. A group is
about people and a business domain, not about which server the bytes are on.

### What changes

- `data_groups.py` reads and writes the app store. `read_snapshot()` becomes a
  read of three local tables; `_execute()` becomes a write transaction on them.
  Everything above it — `Snapshot`, `placement`, `allows`, the chains — is
  untouched, because none of it knows where the rows came from.
- `get_access` stops needing a warehouse connection to read the rules, and so
  stops 503-ing when there is none.
- The two calls spec 0004 left bound in the dashboards list — `folder_lookup`
  and reading a chain from a dashboard's SQL — are re-examined. The first reads
  uploads, which are already local. The second is only reached for a dashboard
  with no recorded database, which the copy fills in.
- The admin page is unchanged. It already lists databases, folders, dashboards
  and reports to assign; where the assignment is stored is not something it
  knows.

### The copy

`scripts/adopt_data_groups.py`, the same shape as `adopt_dashboards.py`:

```
python scripts/adopt_data_groups.py --dry-run   # what it would take
python scripts/adopt_data_groups.py             # take it
```

- Reads the three warehouse tables in one pass, as a linked account.
- Writes each group with its members and assignments in one transaction.
- **Skips a group the app store already holds**, so a second run cannot
  overwrite a change made here after the first.
- Writes nothing to the warehouse.
- A deployment with no warehouse does not run it, and starts with no groups —
  which is "not configured", and everything is visible, exactly as today.

## Data

The tables exist already, created unused by app-store migration 3:

```sql
data_groups            (group_id PK, name UNIQUE NOCASE, description,
                        created_by, created_at, updated_by, updated_at)
data_group_members     (group_id, member_type, member_name, …,
                        PRIMARY KEY (group_id, member_type, member_name))
data_group_resources   (group_id, resource_type, resource_id, is_main, …,
                        PRIMARY KEY (group_id, resource_type, resource_id))
```

They match the warehouse's columns, so the copy is a straight transfer. Two
differences to hold in mind rather than smooth over:

- `data_groups.name` is `UNIQUE COLLATE NOCASE` here, where the warehouse
  enforces uniqueness only in `create_group`'s check. Two groups differing
  only in case would be legal there and refused here. The copy must report that
  rather than fail on it.
- `data_group_members` and `data_group_resources` have real primary keys, so a
  duplicate row that the warehouse tolerated is refused. The same lesson as
  0004's duplicate versions: report what is not kept, never drop it silently.

## Acceptance checks

1. **Done.** A group made in the app store is enforced exactly as one read
   from the warehouse was. Held by the data-group tests, which now round-trip
   through the store rather than asserting the SQL that used to be sent.
2. **Done.** `test_a_dashboard_inherits_its_databases_group` and
   `test_assigning_a_dashboard_replaces_what_it_inherits` were already there
   and still pass untouched — the model did not move.
3. **Done.** `test_no_warehouse_is_needed_to_read_or_write` creates a group,
   adds a member and files a resource with the warehouse connection rigged to
   fail if anything reaches for it.
4. **Done.** Same test: the check is that no connection is opened at all, not
   that no write is issued.
5. **Done, measured.** With `DATA_GROUPS_ENFORCED=true` and the warehouse
   unreachable, `GET /dashboards` answered normally and filed all 26
   dashboards into their groups, where before it would have been a 503.
6. **Done, against the real rules.** All 8 groups, 1 member and 13 filings
   copied. Groups, members and filings compare equal between the warehouse and
   the store, and placement is identical for all 26 dashboards — 26 of 26,
   with the same counts the page showed while the rules still came from
   Snowflake.
7. **Done.** The copy skips a group the store already holds; a second run
   takes nothing.
8. **Done.** A name that clashes case-insensitively with a group already here
   is named and skipped, and the rest of the copy completes. The real rules
   had no clash.
9. **Done.** `test_nothing_set_up_is_not_configured` plus check 3: a
   deployment that never had a warehouse creates groups and has them enforced.
10. **Done.** Unchanged from before: `snapshot(required=True)` still raises
    `AccessRulesUnavailable`, and `get_access` still answers 503 rather than
    letting everything through.

## Open questions

1. **The cache.** 30 seconds per worker exists because reading cost a warehouse
   round trip. A local read does not. Recommendation: keep the cache but drop
   the TTL sharply, or invalidate across workers on write — the current
   behaviour is that an admin's change takes up to 30 seconds to reach the
   other three workers, which is visible to a person who just made it.
2. **What an admin without a warehouse sees in the assignable list.** Databases
   come from the warehouse's catalogue today. For a deployment with none, the
   list is SQL Server databases and folders. Recommendation: no special case —
   list what the deployment has.
3. **Whether `ROLE` membership should warn when nobody can match it.** A
   deployment with no warehouse can hold `ROLE` members that will never match
   anyone. Recommendation: leave it; it is the model question the owner wants
   to think about, and a warning now would pre-empt that decision.

## Notes

**Built 2026-09-24. The model did not change, which was the point.**

`data_group_store.py` holds the rows; `data_groups.py` keeps every decision —
placement, inheritance, what a main group means — and is now the same code for
every connector with nothing vendor-specific left in it. The Snowflake import
is gone from it entirely.

**`configured` had to change meaning, and it matters.** It used to mean "the
warehouse tables could be read". The app store's tables always exist, so read
literally it would be true everywhere — and a deployment that has never
created a group would start filing every dashboard under "Ungrouped" instead
of by its database, which is what the page does today when nothing is set up.
It now means **at least one group exists**. That keeps the current behaviour
for a deployment with no rules, and it is what makes "a deployment with no
warehouse starts empty and filters nothing" true rather than an accident.

**The write tests were asserting the wrong thing.** They recorded the SQL
statements sent to the warehouse — table names, statement order, bound
parameter positions. There is no SQL to assert now, and the outcome was always
what mattered: which group a resource is filed under, and which groups can see
it. Rewritten that way they are shorter, and they would have caught a
reordering bug the old ones would have passed.

**What the real rules look like, and why it answers the question about admin
burden.** 8 groups, 13 filings — and **12 of the 13 are databases**. Exactly
one is a dashboard, filed directly as an override. Nobody has been curating
artefacts; they grouped their databases once and 25 dashboards inherited. That
is the model working, visible in the data.

**Still needs a warehouse:** `/data-groups/catalog`, which lists what can be
filed. It reads documented databases, folders, dashboards and reports, each
guarded so one failing source does not empty the page, and it is
administrator-only. Left bound deliberately — unbinding it is worth doing when
the list of databases itself stops coming from the warehouse catalogue, which
is not this spec.
