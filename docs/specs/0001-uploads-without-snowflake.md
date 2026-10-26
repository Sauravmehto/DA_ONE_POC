# 0001 — Uploaded files and folders work without Snowflake

**Status:** building (2026-09-23) — the last check is the live one
**Touches:** uploads (Excel and documents), file dashboards, the chat's edit
path, the audit log, the Files workspace, the app store

## Why

A client with spreadsheets and no warehouse is the case this product is
supposed to serve, and today it does not. Uploading a file writes its record to
a Snowflake table; a dashboard built on that file is written as JSON into
object storage; every model call and tool call is audited to a Snowflake table.
So the Files workspace needs a warehouse to list what you uploaded yesterday,
and a client without one has no audit trail at all.

SQL Server has already made this journey. This does the same for uploads.

## Standing constraint: nothing in Snowflake is touched

A demo is running against Snowflake, and **prod and dev share those tables**.
For the whole of this spec:

- no Snowflake table is created, altered, renamed or dropped;
- no existing Snowflake row is written or deleted;
- nothing is copied out of Snowflake;
- the Snowflake paths that work today keep working, untouched.

Everything here is **additive**: new records go to the app store, and what is
already in Snowflake or in JSON is left exactly as it is.

## Not in scope

- **Migrating anything.** No copy of Snowflake upload records, no copy of JSON
  dashboards, and no rewriting of either. Decided in review: what exists keeps
  being read where it is, and everything new is written to the app store.
- **Snowflake dashboards** (`MCP_SAVED_DASHBOARDS`) — untouched, their own
  spec later.
- **The Report Builder** — spec 0002.
- **Data groups** — their own spec. They keep reading their rules from
  Snowflake, and are skipped when there are none, as today.
- **Turning the Snowflake audit write off.** The new audit records are written
  in addition to it, not instead of it.

## The checklist

| | |
|---|---|
| **Reach** | Nothing new to reach. The Files workspace, its uploads, folders and dashboards stop refusing when Snowflake is absent. A person signed in with a password and no Snowflake link can upload, list, chat, build a dashboard, and edit it from the chat. |
| **Permission** | Unchanged. An upload is visible to whoever can see the workspace; a dashboard is editable by the person who made it or an administrator. Audit rows record the signed-in person exactly as the Snowflake ones do. |
| **Failure** | Records and files fail separately: a dashboard whose file is missing from storage still lists and opens, with its panels reporting the missing source. An audit write that fails is logged and never breaks the request it describes — as today. If Snowflake is present but unreachable, uploads and dashboards kept in the app store are unaffected. |
| **Isolation** | Records are per deployment. Dev and prod already have separate app stores; this puts uploads and their dashboards inside that boundary for the first time. The storage bucket may be shared: a record in one deployment's store is what makes a file visible there, and ids are UUIDs, so nothing crosses. |
| **The model** | Unchanged in content. A spreadsheet's meaning still comes from the definitions agreed for its shape; a folder's from its datasets and documents. Read from SQLite, so an upload chat no longer waits on a warehouse query to resolve its source. |
| **Cost** | Removes the Snowflake queries an upload page load and an upload chat turn make today. Audit gains one local write per event, which is µs. Nothing is copied, so there is no migration cost. |
| **Migration** | **None.** Every *write* goes to the app store, from now on. Every *read* checks the app store first and falls back to where a record already lives — the JSON file, or the Snowflake table when the session can reach it — so what exists today keeps working untouched. Nothing is copied, and nothing old is ever written back to. A deployment with no Snowflake and no old files simply starts empty. |

## Behaviour

### 1. Upload records

`app/engine/excel_sources.py` keeps its function signatures and changes where
it reads and writes:

- **Writes** — new datasets, folders and definitions go to `excel_datasets`,
  `excel_collections`, `excel_definitions` in the app store. Renames, removals
  and deletes apply there.
- **Reads** — the app store first. When a record is not there and the session
  has Snowflake, the old table is read, so an upload made before this change
  still lists and still opens exactly as it does today. A deployment with no
  Snowflake never asks.
- **Never** writes to a Snowflake table, including an update to a record it
  read from one. A record read from the old table and then changed (renamed, a
  file removed) is written to the app store, and the app store answers for it
  from then on. The Snowflake row is left as it was.

### 2. Dashboards on uploads

`file_dashboards` already keeps SQL Server dashboards in the app store and
upload dashboards as JSON, chosen by `_in_app_store(source_key)`. That test
becomes "always true":

- **new** upload dashboards are saved to the app store, with versions;
- **existing** JSON dashboards are still listed and still open, exactly as
  today. They are never copied. Editing one writes the new version to the app
  store and leaves the JSON file as it was;
- no dashboard of any kind is written to object storage from here on.

### 3. The chat's edit path

Today, asking the chat to edit a dashboard in an Excel- or PDF-scoped
conversation falls through to the Snowflake path whenever the title does not
match exactly (`app/agent/tools.py:_edit_dashboard`, after the `file_matches`
block). Without Snowflake configured, that raises `Missing required environment
variable: SNOWFLAKE_USER` — the error seen in testing.

In a conversation scoped to an upload or a SQL Server source, the tool never
reaches the Snowflake path. When nothing matches it says so and lists the
dashboards that exist on this source, so the model can ask which one.

### 4. The audit log

`app/engine/audit.py` writes every tool call, chat turn and model call — with
its token counts — to the app store's `audit_events`, always. The Snowflake
write stays exactly as it is, in addition, while the demo needs it; retiring it
is a later spec.

A client with no Snowflake therefore has a full audit trail for the first
time — which is also what the cost reporting reads.

### UI

No new screens. What changes is what stops refusing: the Files workspace, its
uploads and its dashboards work with no Snowflake link.

## Data

Tables already exist (migration 3): `excel_collections`, `excel_datasets`,
`excel_definitions`, `audit_events`, `dashboards`, `dashboard_versions`.

One new migration adds the previews, so dashboards stop writing to object
storage entirely:

```sql
CREATE TABLE pending_plans (
    plan_id     TEXT PRIMARY KEY,   -- or report_id, for a staged edit
    kind        TEXT NOT NULL,      -- 'plan' | 'edit'
    source_key  TEXT NOT NULL,
    payload     TEXT NOT NULL,      -- the plan or the staged edit, as JSON
    created_by  TEXT,
    created_at  TEXT NOT NULL
);
```

Ids are unchanged everywhere.

## Acceptance checks

1. A person signed in with a password and **no Snowflake link** can upload a
   spreadsheet, see it listed, chat with it, build a dashboard on it, run it,
   and edit it from the chat. (Tests, and checked in the running app.)
2. Asking the chat to edit a dashboard in an upload-scoped conversation never
   reaches the Snowflake path: a name that matches nothing answers with the
   dashboards on this source, not an error about `SNOWFLAKE_USER`. (Test.)
3. Every tool call, chat turn and model call writes a row to `audit_events`
   with its user, source, token counts and duration — including in a
   deployment with no Snowflake. (Test.)
4. The Snowflake audit write still happens as before when a connection is
   bound, and a failure of either write never fails the request. (Test.)
5. **No Snowflake write of any kind** happens in an upload path: a test fails
   if `snowflake_client.get_connection` is used while uploading, listing,
   saving or editing an upload dashboard. (Test — this is the demo's
   protection.)
6. An upload record that exists only in Snowflake still lists and opens for
   someone with Snowflake; renaming it writes to the app store and the
   Snowflake row is unchanged. (Test.)
7. An upload dashboard saved as JSON before this change still lists and opens;
   editing it writes the new version to the app store and leaves the JSON
   file untouched. (Test with a fixture.)
8. After this change, saving, editing or running a dashboard writes nothing to
   object storage. (Test that fails if `storage.put_json` is called.)
9. The whole backend suite passes, and the Files workspace is checked in the
   running app: upload, list, open a dashboard, edit it from the chat, revert
   it — with the Snowflake link removed from the session.

## Open questions

1. **Reads of old records have no setting behind them** — the app store is
   asked first, the old place only when a record is missing and reachable.
   Settled in review: what already exists must keep working, and a flag is one
   more thing to get wrong in prod.
2. **Audit rows are written twice for a while** (app store and Snowflake).
   Recommendation: accept it. The Snowflake write is what the demo's cost
   reporting reads; retiring it is a decision for after the demo.
3. **`audit_events` will grow.** Recommendation: leave it for this spec, and
   add a retention setting when the cost reporting moves across — the table is
   append-only and a few thousand rows a week.

## Notes

**Built 2026-09-23.** Four things landed, in this order: the audit log, the
chat's edit path, the upload records, the dashboards and their previews. Three
things differ from the spec above, each because the code said so:

- **`_ensure_tables()` is now a no-op.** It ran `CREATE TABLE IF NOT EXISTS`
  against Snowflake on the way into every upload path — a write, and one the
  standing constraint forbids. Nothing creates a Snowflake table any more.
- **A record moves when someone changes it, not when it is read.** Reading an
  older dashboard or upload leaves it exactly where it is; an edit, a layout
  change, a revert or a delete writes it here and the app store answers for it
  from then on. Running a dashboard does not move it: a run is not a change.
- **`excel_datasets` had a foreign key to `excel_collections`** (migration 3),
  which refused every upload into a folder nobody had named — and a folder
  only has a row once it is named. Migration 7 rebuilds the table without it.

**Revised 2026-09-23 after review:**
- Writes are fresh, reads are not: everything new goes to the app store, while
  records that already exist keep being read where they are, so nothing that
  works today stops working.
- Migration dropped entirely — existing upload records and JSON dashboards are
  read, never copied (review: not worth it for the few that exist).
- The standing constraint added: nothing in Snowflake is touched, because prod
  and dev share those tables and a demo is running.
- The audit log brought into scope: a client without Snowflake had nowhere for
  it to go.
- The chat's edit path for Excel and PDF dashboards brought into scope.
