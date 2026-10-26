# DataAlphaOne — how the system is put together

What exists today, and where each kind of thing lives. Read this before
changing anything; it is meant to save you reading the code to find out where
something belongs.

Companion documents: [DECISIONS.md](DECISIONS.md) for why things are the way
they are, and [specs/](specs/) for work that is planned or under way.

---

## 1. The shape

```
                    ┌────────────────────────────────┐
                    │  App: chat · dashboards ·      │
                    │  reports · administration      │
                    └───────┬────────────────┬───────┘
        sessions            │                │  uploads, generated workbooks
   ┌──────────┐  ◄──────────┘                ▼
   │  Redis   │                    ┌──────────────────┐
   └──────────┘  ┌──────────────┐  │  Object storage  │
                 │ SQLite       │  │  S3 or a volume  │
                 │ app store    │  └──────────────────┘
                 │ (APP_DB_PATH)│
                 └──────┬───────┘
                        │  source key
      ┌─────────────────┼──────────────────┐
 ┌────▼─────┐    ┌──────▼──────┐    ┌──────▼──────┐
 │Snowflake │    │ SQL Server  │    │ Files via   │
 │per-user  │    │ shared login│    │ DuckDB      │
 └──────────┘    └─────────────┘    └─────────────┘
```

**The rule:** what the app knows *about* data lives in the app store. The data
itself stays where it is. A source never holds the metadata that describes it.

## 2. How a source is named

One string identifies a source everywhere — in a conversation, a dashboard, a
semantic layer, an entitlement:

| Source | Key |
|---|---|
| Snowflake database | `LOAN` (bare name, upper case) |
| SQL Server database | `SQLSERVER:<connection id>/<database>` |
| Uploaded spreadsheet | `EXCEL:<dataset id>` |
| Folder of uploads | `FOLDER:<collection id>` |

`app/engine/connectors/registry.py:for_source()` hands back the connector that
serves a key. Core code asks the registry; it never tests the prefix itself.

## 3. The connector interface

`app/engine/connectors/base.py` defines what every vendor provides. Anything
true of *every* database is written once against this; only what genuinely
differs per vendor goes in the connector.

| The connector provides | Used for |
|---|---|
| `name`, `dialect`, `table_name_parts` | routing, SQL validation, layer keys |
| `owns`, `display_name` | which sources are its, what to call them |
| `run_query`, `dry_run_query` | panels, chat answers, validation |
| `schema_map`, `catalogue_text` | the semantic layer editor, the model's context |
| `session_identity` | the audit trail |
| `unavailable(exc)` | telling "the SQL is wrong" from "the server is down" |

Connections are bound per request through a ContextVar (`use_connection`), so
engine code takes no connection argument and a query runs as the signed-in
person.

`app/api/sources.py` is the route-level companion: list the sources a person
can work on (optionally scoped to one workspace), bind the right connection,
and decide who may author.

## 4. The app store (SQLite)

One file per deployment, at `APP_DB_PATH`, managed by
`app/accounts/store.py`. `PRAGMA user_version` records which migrations have
run; migrations are append-only.

**In use now**

| Tables | Holds |
|---|---|
| `users`, `credentials`, `invitations`, `email_tokens`, `snowflake_link_requests` | accounts and sign-in |
| `app_settings` | deployment settings, and markers for one-time copies |
| `data_connections` | SQL Server connections, password encrypted |
| `dashboards`, `dashboard_versions` | every dashboard, whatever it reads, every version kept |
| `semantic_layers`, `semantic_layer_versions` | layers as the TOML a person wrote |
| `source_catalogues` | a source's tables and columns, cached and shared |

| `excel_collections`, `excel_datasets`, `excel_definitions` | uploaded spreadsheets, their folders, and what their columns mean |
| `excel_templates`, `excel_reports` | Report Builder: approved workbook shapes, and the reports that fill them |
| `audit_events` | one row per tool call, chat turn and model call, with what it cost |
| `pending_plans` | a dashboard plan proposed but not yet saved |

| `data_groups`, `data_group_members`, `data_group_resources` | who may see which source, and what each one holds |

## 5. Where everything lives today

Records move forward rather than being migrated: when a kind of record moves to
the app store, new ones are written there and the old place is read as a
fallback. Nothing in Snowflake is written or altered — prod and dev share those
tables. See DECISIONS.md.

Dashboards are the exception, and deliberately: their history would otherwise
span two places, so they are COPIED out once by
`scripts/adopt_dashboards.py`, which an administrator runs per deployment. It
reads the warehouse and never writes to it, skips any dashboard the store
already holds, and leaves the table exactly as it is.

| Thing | Where it is now | Where it is going |
|---|---|---|
| Accounts | SQLite | — |
| SQL Server connections | SQLite | — |
| SQL Server dashboards | SQLite | — |
| Semantic layers | SQLite (seeded once from `config/metadata/*.toml`) | — |
| Snowflake dashboards | SQLite (copied once; the warehouse table is left as it is) | — |
| Upload dashboards | SQLite; records written as JSON before are read where they are | — |
| Upload records (datasets, folders, definitions) | SQLite; older ones read from Snowflake `MCP_EXCEL_*` | — |
| Report Builder templates and reports | SQLite; older ones read from Snowflake | — |
| Data groups | SQLite (copied once; the warehouse tables are left as they are) | — |
| Audit log | SQLite, and Snowflake `MCP_PROMPT_LOG` while `AUDIT_TO_SNOWFLAKE` is on | SQLite alone |
| Uploaded files, generated workbooks, plan previews | Object storage | — |
| Sessions, conversations | Redis (file store in development) | — |

## 6. What runs where

- **Sessions** are short-lived and read on every request: Redis, not SQLite.
- **Uploaded files** go to object storage, never the app server's disk.
- **Four uvicorn workers** share the SQLite file (WAL, busy timeout). Anything
  cached in a worker's memory must be able to notice another worker's change —
  semantic layers compare a version marker, catalogues have a shared table.
- **Each deployment keeps its own app store.** Dev and prod are separate
  Portainer stacks with pinned volume names (`dataalpha_app_data_dev` and
  `_prod`), so nothing is shared between them.

## 7. Connecting to a slow source

SQL Server taught us what this costs, and the handling is now general enough
to reuse:

- one login per request, connections kept in a pool between requests;
- a statement refused for want of resources is retried once on a fresh
  connection; a rejected login never is;
- catalogues are read once per deployment and shared, refreshed before they go
  stale, and read ahead when a connection is added;
- an unreachable source stops the work and says so, rather than quietly
  producing less.

## 8. Where the model's knowledge comes from

1. **The catalogue** — what exists, read from the source.
2. **The semantic layer** — what it means, written by an administrator, stored
   per source. Without one, the model has column names and nothing else.
3. **The conversation** and the dashboard's own plan.

`app/engine/metadata_loader.py` (engine) and `app/metadata/loader.py` (app)
both read published layers from the app store and cache them per worker.
