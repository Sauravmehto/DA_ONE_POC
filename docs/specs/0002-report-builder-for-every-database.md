# 0002 — The Report Builder works on any database

**Status:** done (2026-09-24)
**Touches:** Report Builder (templates, mapping, blocks, runs), the connector
interface, the app store, the tools UI

## Why

The Report Builder does the same work whatever database is underneath: read an
approved workbook template, work out which of its cells are figures and which
are structure, map each block to a query, run those queries, and write the
numbers back into the workbook. Only two things are actually vendor-specific —
the SQL dialect the queries are written in, and the connection they run over.

Today the whole feature is Snowflake's. Templates and reports are rows in
`MCP_EXCEL_TEMPLATES` and `MCP_EXCEL_REPORTS`; every route is gated on a
Snowflake role; the executor opens a Snowflake connection. A client on SQL
Server sees no Report Builder at all — and we have just hidden the menu item
from them, which is the wrong fix.

## Not in scope

- **Uploaded files as a report source.** A workbook built from a spreadsheet
  is a different feature; this is about databases.
- **The template scanner's Excel handling** — openpyxl work, already
  vendor-neutral, unchanged.
- **Where generated workbooks are stored** — object storage, unchanged.

## Noted, not in scope: how a template is scanned

Raised in review, and worth writing down before it is forgotten. This spec
keeps the scanner exactly as it is — the same structure and code for every
connector — but the approach has limits that will decide how good the Report
Builder can be, and they are not a dialect problem.

`excel_template_scanner.py` is deliberately deterministic: it reads a workbook
and classifies each formula by shape alone, into "safe to regenerate", "needs
one human review", or "leave as it is". That is the right instinct — a report
that silently rewrites a formula it misunderstood is worse than one that asks.
Where it runs short:

1. **The safe list is a fixed set of functions.** SUM, SUMIF(S), COUNTIF(S),
   AVERAGEIF and SUMPRODUCT over a range regenerate automatically. Everything
   else needs review — so a real template built on INDEX/MATCH, VLOOKUP,
   XLOOKUP, IFERROR wrappers or array formulas arrives as a wall of flags, and
   the person approving it stops reading.
2. **A repeated row is only recognised when it is exactly one column and
   exactly one row apart.** Fill-down blocks that span two columns, or repeat
   every other row, are not seen as one block, so each cell is flagged
   separately.
3. **Header and data-table detection are heuristics** aimed at tables. The
   form-shaped sheets that are most of finance — line items down column A,
   values in B, sections interleaved — do not have a header row to find.
4. **The fingerprint covers sheet names.** Renaming a tab makes an approved
   template unrecognised, and it has to be approved again.
5. **Nothing learns from a bad regeneration.** When a rebuilt report comes out
   wrong, that outcome is not recorded anywhere, so the same template fails the
   same way next time.

A better way probably means letting the model read the structural evidence the
scanner already produces and propose an interpretation for the cases the rules
cannot settle — with the human approval that already exists as the gate, rather
than in place of it. It should start by measuring what the scanner gets right
and wrong on real templates: changing it on a hunch would be changing the one
part of the product whose whole job is not to guess.

That is now [spec 0003](0003-scanning-a-template-properly.md).

## The checklist

| | |
|---|---|
| **Reach** | Report Builder appears in Tools in every workspace, beside the Semantic Builder, scoped to that workspace like the Builder is. Inside it, a report names the source it runs against, chosen from that workspace's databases. |
| **Permission** | The same rule as a semantic layer: a Snowflake source needs a Snowflake role in `ADMIN_ROLES`; every other source is an administrator's. Running an existing report stays open to anyone who may see it — running is not authoring. Deleting stays with administrators. |
| **Failure** | A source that will not answer fails the run with a message naming it, and does not write a half-filled workbook. A block whose SQL is wrong fails that block, names it, and the report says which blocks are missing rather than presenting a workbook that looks complete. The row cap and its "there were more rows" warning work the same on every connector. |
| **Isolation** | Templates and reports are per deployment, in the app store. A report belongs to one source key; it is offered only inside that workspace. Two connections to different SQL Servers keep separate reports. |
| **The model** | Nothing, as it turns out: the Report Builder has no model step. Its SQL is typed by an administrator in the binder, so what matters is that the editor says which dialect to write and that the query is proven against the database it will run on. Both are now true. If an assistant ever writes this SQL, it wants the dialect and the semantic layer the way the dashboard planner does. |
| **Cost** | One connection per run, from the pool, rather than a login per block. A run's queries are unchanged in number. The prompts grow by the dialect rules and, where present, the semantic layer. |
| **Migration** | **None**, as in 0001. Every write goes to the app store; templates and reports made before this are read where they are, when a session can reach them, and move here only when someone changes one. A report recorded before this has no source key — it is Snowflake's, and is treated as such until it is saved again. Nothing in Snowflake is written, altered or copied. |

## Behaviour

### The engine

`app/engine/excel_multiblock.py` already takes an executor, with
`snowflake_executor()` as one implementation — the seam exists. It becomes
`executor_for(source_key)`, built from the connector:

- SQL is validated with `connector.dialect` (the read-only gate is unchanged);
- the row cap is applied the connector's way — `LIMIT` for Snowflake and
  DuckDB, `TOP` for SQL Server — through the existing `enforce_row_limit`;
- the pre-cap `COUNT(*)`, which is what lets a run say "there were more rows",
  is expressed per dialect;
- the connection comes from `sources.run_on_source`, so a run makes one login.

`excel_reports.py` stops importing the Snowflake client: its reads and writes
go to the app store, and its queries go through the executor.

### Records

`excel_templates` and `excel_reports` tables already exist (migration 3). Both
gain a `source_key`, so a report knows which database it runs against.
Template records are keyed by the workbook's fingerprint as they are today,
and a template is not tied to a source — the same workbook shape can be filled
from two different databases.

### Routes

The scan, approve, plan, save, blocks and publish routes move from
`require_snowflake_author` to the per-source rule in `app/api/sources.py`, and
bind through `run_on_source`. Listing and running keep their current
permissions. Every route that takes a report or template gains the source in
its payload or reads it from the record.

### UI

- Report Builder returns to Tools in every workspace, scoped the same way as
  the Semantic Builder, and the "needs Snowflake" refusal goes.
- A new report asks which database it runs against, from that workspace's
  list. With one database, it is chosen already.
- A report's page names its source, as a dashboard's does.

## Data

```sql
ALTER TABLE excel_templates ADD COLUMN source_key TEXT;   -- nullable: shape only
ALTER TABLE excel_reports   ADD COLUMN source_key TEXT;   -- required for new rows
```

Copied rows keep their ids, so existing links and any saved workbook paths
keep working.

## Acceptance checks

1. An administrator with **only a SQL Server connection** can scan a workbook
   template, approve it, plan its blocks, save a report against a SQL Server
   database, and run it to a filled workbook. (Test with a stub executor, and
   checked once against the real server.)
2. The SQL the planner writes for a SQL Server report is T-SQL: `TOP` not
   `LIMIT`, and it passes the read-only gate in that dialect. (Test.)
3. A run makes **one** login to the source regardless of how many blocks it
   has. (Test, counting connections.)
4. A block whose SQL fails names that block and does not fail the others; a
   source that cannot be reached fails the run with the source named, and
   writes no workbook. (Tests.)
5. A report's row cap and its "more rows existed" warning behave the same on
   Snowflake and SQL Server. (Test per connector.)
6. Templates and reports recorded before this are listed and run exactly as
   they are today for someone with Snowflake, and nothing is written back to
   Snowflake — a test fails if any Report Builder path opens a Snowflake
   connection for a source that is not Snowflake's. (Tests.)
7. A report saved after this change is in the app store, names its source, and
   is listed without a Snowflake connection. (Test.)
8. Where a source has a published semantic layer, the mapping prompt contains
   it. (Test.)
9. The Report Builder appears in Tools in a SQL Server workspace and lists only
   that workspace's reports.
10. The whole backend suite passes; the Report Builder is exercised in the
    running app end to end on one SQL Server database.

## Open questions

1. **Does a template belong to a source, or only to a workbook shape?**
   Recommendation: only to the shape, as above, so one template can be filled
   from two databases. Costs nothing now and is hard to undo later.
2. **Old reports whose saved query names a Snowflake database** — stamp them
   with that database as their source key, or leave the source key empty and
   treat empty as "Snowflake, default database"? Recommendation: stamp them at
   copy time; an empty column that means something is a trap.
3. **When is the scanner's turn?** Recommendation: after this spec ships, and
   starting with a measurement — a set of real templates, what the scanner says
   about each, and what a person had to correct. Without that there is no way
   to tell an improvement from a change.
4. **Report Builder is behind `SHOW_REPORT_BUILDER`.** Does it stay behind a
   flag once it works everywhere? Recommendation: yes for now, one flag for the
   whole feature, not per connector.

## Notes

**Built 2026-09-24.** Checked end to end against the real SQL Server with
Snowflake blocked: scan, approve, plan (dry-run in T-SQL), save, and a
generated workbook of 5 rows, in 11.3s. The test artefacts were removed
afterwards.

Three things differ from the spec above:

- **The Report Builder has no model step.** The checklist row above claimed a
  mapping prompt; the SQL is typed by a person. Corrected rather than built.
- **`plan_report` dry-ran every query against Snowflake**, whatever database
  the report named — so a T-SQL query would have been rejected before it could
  ever be saved. It is proven against its own source now.
- **`excel_reports.template_id` had a foreign key to `excel_templates`**, which
  refuses a report whose template was approved before templates moved here —
  and those are read where they are, not copied. Migration 9 rebuilds the
  table without it, as migration 7 did for uploads.

**Approved 2026-09-23.** Migration brought into line with 0001 and the
decision it settled: records move forward, nothing is copied, and nothing in
Snowflake is touched.

**Reviewed 2026-09-23.** Two points raised:

- The dialect is the database's, decided by the connector — already how this
  spec is written: SQL is validated with `connector.dialect`, the row cap and
  its pre-cap count are expressed per dialect, and the planner is told which
  dialect to write, as the dashboard planner already is.
- How the Report Builder scans a template needs a better way. Recorded above,
  and deliberately left out of this spec: the same structure and code carry
  over to every connector first.

_(kept during the build)_
