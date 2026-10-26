# 0004 — Every dashboard is a record in the app's own store

**Status:** done (2026-09-24) — see Notes for what changed during the build
**Touches:** dashboards (save, list, run, edit, versions), the entitlement
check, the Snowflake connector, the app store

## Why

Dashboards are the last thing the app keeps inside a customer's warehouse.
`LOAN.PUBLIC.MCP_SAVED_DASHBOARDS` holds one row per version, and everything
about a dashboard — its plan, its history, who made it, which database it
belongs to — lives there. A client without Snowflake therefore had no
dashboards at all until spec 0001 gave uploads and SQL Server their own path,
and the app now has two of everything: `file_dashboards` for sources that had
nowhere else to go, `dashboards.py` plus the Snowflake client for the ones
that did. The two behave differently, are tested separately, and disagree in
small ways nobody finds until a customer does.

It is also the wrong place for the record. A dashboard is the app's own
metadata: what a person built, when, and out of which panels. Keeping it in
the warehouse means dev and prod share one list, a demo and a developer write
to the same table, and the app cannot be sold to anyone who does not have that
warehouse.

## Not in scope

- **Data groups.** `MCP_DATA_GROUP*` stays in Snowflake. Dashboards carry the
  group *name* they already carry; who may see what is still decided there.
  Its own spec.
- **The audit table.** `MCP_PROMPT_LOG` is already written in both places
  (spec 0001). Retiring the Snowflake half is a separate, later decision.
- **Renaming `file_dashboards`.** The name was wrong the moment SQL Server
  dashboards went into it, and reads worse now that it decides which engine
  serves a warehouse one. A rename with no behaviour in it — the code-cleanup
  spec, which also merges the two engines and deletes the warehouse writer.
- **Dropping the Snowflake table.** Explicitly not this spec. See *The end of
  the table* below.

## The checklist

| | |
|---|---|
| **Reach** — how does someone get to this, from every workspace and every connector? | No new surface at all. The same dashboard list, the same page, the same chat tools, in every workspace. What changes is which store answers, and a person should not be able to tell — that is the acceptance check. |
| **Permission** — who may do it, and what does everyone else see instead? | Unchanged, with one rule that had to be written down rather than left implicit — see the Notes: a warehouse dashboard is shown only to someone who has connected a warehouse, because binding a connection used to enforce that by accident. Otherwise unchanged. A dashboard's author is still the author of its first version, editing still requires being that person or an administrator, and data-group filtering still runs on every list and every open. The copy preserves `CREATED_BY` on every version, so nobody's ownership moves. The copy itself is run by an administrator from the command line, never from the UI. |
| **Failure** — what happens when the source is slow, down, or refuses? | Better than today in one specific way: reading a dashboard's *record* stops needing Snowflake, so a warehouse that is slow or down no longer stops the list from rendering or a SQL Server dashboard from opening. Running the panels still needs whatever database the dashboard reads, and says which one when it cannot. If the copy cannot reach Snowflake it fails before writing anything and says so; a half-copy is not a state this can end in. |
| **Isolation** — what is scoped per workspace, per connection, per deployment, and what survives a redeploy? | This is the point of the work. The app store is per deployment (`APP_DB_PATH` on a pinned volume), so dev's dashboards and prod's stop being one list. The copy is run once per deployment against that deployment's own store. Nothing is written back to Snowflake, so a dashboard made in dev cannot appear in the demo. |
| **The model** — what does the AI get, and when? | Nothing new. `list_dashboards`, `run_dashboard` and the edit tools return what they return today; the model is not told where the record came from, because it should not matter. |
| **Cost** | One `SELECT` over the table at copy time, once per deployment. After that, every dashboard read is a local SQLite read instead of a Snowflake round trip — the list page currently costs a warehouse query and, on a cold connection, a login. |
| **Migration** | A copy, not a take-over: every version of every active dashboard is read from Snowflake and written to the app store, keeping `REPORT_ID`, `VERSION` and every timestamp. Snowflake is read and not modified. Re-runnable, and it never overwrites a dashboard the app store already holds. Dashboards created in Snowflake *after* the copy are not chased — the owner's decision, recorded below. |

## Behaviour

### One store, two engines — and why it is not one of each

The record moves. The engine does not.

The draft of this spec said the app-store path would take over by itself,
because the routes choose between the two by asking
`file_dashboards.exists(report_id)` and the copy makes that true for every
dashboard. Reading the two engines side by side before writing any code showed
what that would have cost. `dashboards.py` does three things to a warehouse
dashboard that `file_dashboards` does not:

- it binds date parameters against the semantic layer
  (`_validate_date_bindings`, on planning, on editing and on a direct plan
  update) and refuses an edit whose date parameter does not name a declared
  column;
- it plans with the database's dashboard heuristics;
- it reverts by making an old version current again — a pointer move — where
  the other writes a new version holding the old plan.

Letting the file engine take over would have changed all three for the
dashboards a demo is running on, in the same change that moves where they are
stored. If a dashboard then looked different, there would be no way to tell
which half did it. So this spec moves the record and nothing else, and the
engines are merged by a later one, once what is warehouse-specific about those
three has been generalised or deliberately dropped.

What changes:

- `dashboard_records.py` answers the calls the Snowflake client answered, in
  the same shape, from the app store. `dashboards.py` swaps 18 call sites to
  it and keeps `run_query`, `dry_run_query` and `session_identity` on the
  warehouse client — a test asserts that is all it still reaches for.
- `file_dashboards.served_here(report_id)` replaces `exists(report_id)` as the
  dispatch, in the routes, the chat tools and the agent route. Being a record
  in the app store no longer tells the engines apart; the source does.
- `source_type()` answers `"snowflake"` for a warehouse source, where it
  answered `"sqlserver"` or fell through to `"file"`. `_dialect()` likewise.
- Each engine lists only its own kind (`list_records(source_types=...)`).
  Both read the same table now, so the list that concatenates them would
  otherwise show every warehouse dashboard twice — as would the data-groups
  page, which merges the same two lists.
- A call that only touches records stops having a warehouse session bound
  around it: listing, reading a saved plan, version history, reverting,
  retiring and filing. That is what makes the list render without a warehouse
  rather than merely being able to.

### Where a dashboard came from

One table for every dashboard means the origin is a stored fact, not a guess.
`dashboards` already carries both halves of it on every row — `source_key`,
which names the exact source (`LOAN`, `SQLSERVER:<connection>/<database>`,
`EXCEL:<id>`, `FOLDER:<id>`), and `source_type`, the coarse label the list
shows — and `dashboard_store` returns them faithfully. `file_dashboards` then
discards the stored label and recomputes it from the key's prefix, which is
why it can only answer `"sqlserver"` or `"file"`.

That was the only option when dashboards lived in two places, because a
dashboard kept as JSON has no such column. It is the wrong one now: the label
is known at save time, when the source is in hand, and deriving it on every
read means a change to a prefix silently relabels every dashboard ever made.

- The stored `source_type` is what the list and the dashboard page report.
- Deriving it from the key stays, as the fallback for records still kept as
  JSON — the one case that has nothing stored.
- It learns a third answer, `"snowflake"`. Three, not four: an upload stays
  one label, because `source_key` already says whether it was a spreadsheet or
  a folder for anything that needs to know.

### What the Snowflake client keeps

The connector keeps running queries — that is its job. What it loses is the
dashboard record: `write_saved_dashboard`, `insert_dashboard_version`,
`read_saved_dashboard`, `list_dashboard_versions`, `deactivate_saved_dashboard`,
`resolve_report_ids`, `revert_dashboard_version`, `touch_saved_dashboard_run`,
`set_dashboard_group` and `dashboard_owner` stop being called. They are left in
place, unused, for the length of this spec — deleting them is the cleanup
spec's job, and leaving them costs nothing while the copy is being trusted.
Their tests stay too, pointed at the client, so the code is still trustworthy
if anything has to go back to it.

`_stored_database()` in `api/access.py` needs no change after all, and the
reason is worth recording. It asks the warehouse which database a dashboard
belongs to, and the data-group check depends on the answer — but it is only
reached when `file_dashboards.read_record()` finds nothing. A copied dashboard
is found, and its chain is built from `source_key`, which `source_chain()`
turns into exactly the `("database", NAME)` link the warehouse branch produced.
So the copy has to set `source_key` the way that branch worked the database
out — the stored column, then the title, then the plan's SQL — and a test
asserts the two chains are equal.

### The copy

`scripts/adopt_dashboards.py`, run by an administrator against one deployment:

```
python scripts/adopt_dashboards.py --dry-run      # what it would take, and from where
python scripts/adopt_dashboards.py               # take it
python scripts/adopt_dashboards.py --report-id X # one dashboard
```

- Reads every row of `MCP_SAVED_DASHBOARDS` where `IS_ACTIVE = TRUE`, all
  versions, oldest first.
- Writes one `dashboards` row per `REPORT_ID` (from its current version) and
  one `dashboard_versions` row per version, keeping the original `VERSION`
  numbers, `CREATED_BY`, `CREATED_AT`, `EDIT_NOTE`, `RUN_COUNT` and
  `LAST_RUN_AT`.
- `source_key` is `'LOAN'` — the warehouse source key the rest of the app
  already uses — and `database_name` and `data_group` come across as they are.
- **Skips any `REPORT_ID` the app store already knows**, and says how many it
  skipped. This is what makes it safe to run twice, and what stops it
  overwriting an edit made here after the first run.
- Writes nothing to Snowflake, and opens no write transaction there. A
  `--dry-run` that reports what it would do is the first thing built.

`dashboard_store.adopt()` already does most of this — it was written to take
over a dashboard kept as JSON, inserts the record and its versions in one
transaction, and returns False without touching anything if the dashboard is
already here. It needs one change: it reconstructs each old version's author
and title from the current record, because a JSON archive does not keep them.
A Snowflake version row does, so the copy passes whole version rows rather
than snapshots, and each version keeps the person who wrote it.

### The end of the table

Not in this spec, and worth writing down so it is not assumed: after this, the
app stops reading and writing `MCP_SAVED_DASHBOARDS`, but the table stays
exactly as it is. Prod is still serving a demo from it on the old build.
Dropping it is a separate decision, taken after prod runs on the app store and
has been seen to work, and it is the last step rather than part of this one.

## Data

No new tables: `dashboards` and `dashboard_versions` (app-store migration 3)
already hold exactly what the Snowflake table holds, split into the record and
its versions.

| Snowflake column | App store |
|---|---|
| `REPORT_ID` | `dashboards.report_id`, `dashboard_versions.report_id` |
| `VERSION` | `dashboard_versions.version`, `dashboards.current_version` |
| `REPORT_TITLE`, `DESCRIPTION` | both tables |
| `DASHBOARD_PLAN` | `dashboard_versions.plan` (JSON text) |
| `TAGS` | `dashboards.tags` (JSON text) |
| `DATABASE_NAME`, `DATA_GROUP` | `dashboards.database_name`, `.data_group` |
| `CREATED_BY`, `CREATED_AT` | both tables |
| `EDIT_NOTE` | `dashboard_versions.edit_note` |
| `RUN_COUNT`, `LAST_RUN_AT` | `dashboards.run_count`, `.last_run_at` |
| `IS_CURRENT` | implied by `dashboards.current_version` |
| `IS_ACTIVE` | `dashboards.is_active`; inactive rows are not copied |
| `VERSION_ID` | dropped — nothing reads it |

Records written as JSON files before spec 0001 keep being read where they are,
unchanged: that path already exists and this does not touch it.

## Acceptance checks

1. **Done, against the real dashboards.** All 25 were copied; each copied
   plan was compared with the warehouse's own and all 25 are identical.
   *Fintech: Growth, Customers & Risk* (v38, `FINTECH_DB`) and
   *E-Commerce: Post-Purchase Experience* (v27, `ECOMMERCE_DB`) were run
   through the engine — 21 panels / 441 rows and 20 panels / 615 rows, no
   panel failing — and the first was then opened in the running app, where it
   drew its KPIs, its narrative and every chart.
2. **Done.** Version history is complete and in order, with the original
   numbers, authors and edit notes, and reverting works —
   `test_a_copied_dashboard_arrives_with_its_whole_history`, and driven
   through `GET /dashboards/{id}/versions` and `POST /revert` against a copy
   of the dev store, with no warehouse reachable.
3. **Done.** Editing a copied dashboard writes a new version in the app store
   and nothing to Snowflake:
   `test_no_dashboard_record_is_written_to_the_warehouse` fails the test if
   any warehouse write is called or a connection is even opened.
4. **Done.** Each engine lists only its own kind, so every dashboard appears
   exactly once — `test_each_engine_lists_only_its_own`, and confirmed through
   the list route.
5. **Done.** A copied dashboard resolves to the same data group it did
   before: `test_a_copied_dashboard_resolves_to_the_same_group` asserts the
   chain built from `source_key` equals the one built from the database, and
   `test_the_database_is_read_from_the_title_when_the_column_is_empty` holds
   the fallback order the entitlement check has always used.
6. **Done.** `test_running_the_copy_twice_keeps_an_edit_made_in_between` — the
   second run takes nothing, skips one, and the edit made in between is still
   current. `test_a_retired_dashboard_is_not_copied_back` covers the other
   way a re-run could undo a decision.
7. **Done by construction, not by test.** The copy reads everything in one
   `SELECT` before writing anything, and each dashboard is written in one
   transaction, so a warehouse that disappears mid-read writes nothing at all.
   Simulating a half-read connection would test the driver, not this.
8. **Done, and measured on the real table.** No warehouse write statement is
   issued: check 3's test covers the calls, and
   `test_the_engine_reaches_the_warehouse_only_to_query` asserts
   `dashboards.py` reaches the client for `run_query`, `dry_run_query`,
   `session_identity` and the row cap and nothing else. After three real runs
   of one dashboard, the warehouse still records 25 runs and the app store
   records 28 — the clearest evidence the writes land here and only here.
9. **Done.** A new warehouse dashboard is saved to the app store by the same
   code path a copied one is read through —
   `test_a_saved_warehouse_dashboard_is_a_row_here`.
10. **Done, and it needed more than the copy.** The list route bound a
    warehouse session around what is now a local read, so it refused with
    `snowflake_not_linked` even though nothing it needed was in the warehouse.
    Listing, reading a saved plan, version history, reverting, retiring and
    filing no longer bind one. Verified by driving those routes with no
    warehouse connection at all.
11. **Done.** The list says where each dashboard came from, from the stored
    row rather than the key's prefix —
    `test_the_label_is_read_from_the_record_not_guessed` fails if anything
    recomputes it, and `test_an_edit_does_not_change_where_it_came_from`
    covers the label surviving an edit.

## Open questions

1. **Dashboards created in Snowflake after the copy.** Settled by the owner:
   not chased. Prod keeps running on the old build and its own table; anything
   made there after dev's copy is recreated later if it is wanted.
2. **Dual-writing to Snowflake during the transition.** Considered and
   rejected, so it is not re-argued: prod and dev share
   `MCP_SAVED_DASHBOARDS`, so a dashboard made in dev while testing would
   appear in the running demo's list — the overlap the app store exists to
   prevent — and it would mean writing to a table frozen for the demo. It also
   buys nothing: prod is on the old build and does not read the app store, so
   Snowflake remains its complete record without any help.
3. **What `source_type` should be called once every source is in one store.**
   The field is consumed by the frontend badge. Recommendation: leave the name
   and let the cleanup spec rename it, so this change carries no UI churn —
   what it *answers* is settled above.
4. **Whether a dashboard should record that it was copied from the warehouse.**
   Recommendation: no. It is a one-off fact about a fixed set of rows, true
   for about a month; `source_key = 'LOAN'` and the copy script's own output
   answer it, and a column that means nothing after the transition is how a
   schema rots. Cheap to add later if the transition turns out to need it.

## Notes

**Built 2026-09-24.**

**The engines did not merge, and that was the main decision.** The draft had
the file engine taking over every dashboard once the copy made
`exists(report_id)` true. Reading both engines first showed that would also
have changed how a warehouse dashboard is edited (date parameters are bound
against the semantic layer only on that side) and how it is reverted (a
pointer move there, a new version here) — for the dashboards a demo is running
on, in the same change that moves the store. Two changes at once, with no way
to tell which one caused a difference. So `dashboard_records.py` moves the
record and leaves the engine alone, and `served_here()` replaces `exists()`
everywhere the two were told apart. The merge is the cleanup spec's, once
those three warehouse-specific behaviours are generalised or dropped.

**`_stored_database()` needed no change, which the draft got wrong.** It is
only reached when the app store does not hold the dashboard, and a copied one
is held — its chain comes from `source_key`, which `source_chain()` turns into
the same `("database", NAME)` link. What that moves the weight onto is the
copy: it has to work the database out the way the entitlement check always
did — the stored column, then the title, then the plan's SQL — and there are
tests on both halves.

**Two bugs the tests caught, neither of them in the copy.**

- `UPDATED_BY` came back as the maker rather than the last editor, because the
  app store's `dashboards.created_by` is written once and an edit adds a
  version under a different name. The record now carries the current version's
  author beside the dashboard's, which is the distinction the list has always
  drawn between "who owns it" and "who touched it last".
- The list route bound a warehouse session around a call that had become a
  local read, so it refused outright without one — acceptance check 10 failing
  on the very thing the move was supposed to fix. Six record-only routes no
  longer bind one.

**What is left needing a warehouse, and why.** Running a dashboard's panels,
planning one, and the two access-control refinements the list makes when data
groups are configured (resolving an upload's folder, and reading a chain from
SQL for a row with no recorded database). The first two are reading the
customer's data, which is the point. The last two are unchanged from before
and out of scope here.

**Still to do by hand, with a warehouse session:** `--dry-run` to read what
would be taken, then the copy, then acceptance check 1 against a named real
dashboard.

**The copy hit something real on the first run: two rows claiming one
version.** One dashboard of 25 — *E-Commerce: Post-Purchase Experience* — had
six rows under three version numbers (18, 23 and 24), from saves made after a
revert, before the warehouse client started numbering from the highest version
rather than the current one. The app store makes `(report_id, version)` a
primary key, so the copy stopped on it.

That constraint is right and was kept. A version is one plan, which is what
makes reverting to it mean anything; in the warehouse a revert to v23 sets
`IS_CURRENT` on *both* rows, so that history is already ambiguous. The copy now
keeps the row the dashboard is actually on, or failing that the one saved last
under that number, and **prints the rows it did not keep** with their dates,
authors and notes rather than dropping them in silence. On the real data the
three it let go were all labelled "temporary probe (will be reverted)".

Worth recording about the failure itself: it stopped after 18 of 25
dashboards, and the store was left consistent — no orphaned versions, no
dashboard without its current version — because each dashboard is written in
one transaction. Re-running took the remaining 7 and skipped the 18. That is
the re-runnability of check 6 doing its job for a reason it was not designed
for.

**A rule that had been enforced by accident.** Unbinding the list from a
warehouse session fixed check 10 and broke something nobody had written down:
needing a connection to build the list was also what kept warehouse dashboards
away from an account that had never connected one. With the records local, a
password-only account saw all 25 titles, their editors and their whole version
history — everything except the data, which still refused.

The rule is now explicit and, importantly, is about a LINK rather than a
CONNECTION: `_has_warehouse()` asks whether this account has connected a
warehouse at all, which is a local read of its stored credential. So a
warehouse that is down still lists its dashboards — the thing check 10 is
about — while an account with no warehouse is not shown them. The same gate
covers reading a saved plan, version history, reverting, retiring and filing.
Two tests hold the two halves apart, because it would be easy to fix one by
breaking the other.

