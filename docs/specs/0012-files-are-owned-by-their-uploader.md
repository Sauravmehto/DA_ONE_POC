# 0012 — Files are owned by their uploader

**Status:** done (2026-10-29)
**Touches:** Files (Excel source) backend — storage, engine, routes

## Why

Two accounts signed in locally and each could see the other's uploaded
folders. Traced to the backend: `excel_collections` and `excel_datasets` had
no owner column at all, and `GET /excel-sources` applied only the optional,
admin-configured `data_groups` entitlement system — a no-op unless an
administrator has set up groups, which nobody had. Every signed-in account
could list, rename, and delete every other account's uploads. Chats were
already correctly scoped per owner (`conversation_store.list_for_owner`); this
was the one gap.

## Not in scope

- An admin UI to assign an orphaned (unclaimed) folder to someone. Until that
  exists, an owner-less folder is invisible to everyone, not just non-admins.
- Any change to `data_groups` itself, or to Snowflake/SQL-Server database
  access (already scoped by role, not ownership).

## The checklist

| | |
|---|---|
| **Reach** | Every Files route: list, get, rename, delete, add documents/files, remove document/file, upload (joining an existing folder). |
| **Permission** | A folder is visible only to its owner — no administrator exception. A folder with no owner on record (made before this, or ambiguous after backfill) is visible to nobody through these routes. |
| **Failure** | Unresolvable ownership (mixed or missing `uploaded_by` across a folder's files) leaves it owner="" rather than guessing — invisible, not deleted, not merged into anyone's list. |
| **Isolation** | Per-account, enforced centrally in `app/api/access.py`'s `require_folder`/`require_dataset` (used by every route's dependency) and inline in `list_collections`, not scattered per-route. |
| **The model** | Unaffected — this is a visibility boundary, not a change to what the agent is told once a folder is already open. |
| **Cost** | One extra `get_collection`/`get_dataset` lookup per request (already cheap, in-process SQLite); no new round trips to Snowflake. |
| **Migration** | `scripts/backfill_collection_owners.py`, run once by hand (dry run by default, `--apply` to write). |

## Behaviour

- **Storage** — `excel_collections.owner` and `excel_datasets.owner` (new
  columns, nullable). Written once at creation, never changed by a later
  upload into the same folder (`app/engine/upload_store.py`'s `save_dataset`/
  `save_collection_name`: owner is in the INSERT, excluded from the `ON
  CONFLICT DO UPDATE SET` clause — first write wins).
- **Engine** (`app/engine/excel_sources.py`) — `Dataset.owner` and
  `Collection.owner`. Uploading into an existing folder inherits that
  folder's established owner (`ingest_files`'s `collection_owner` resolution),
  not the new uploader — one folder, one consistent owner, however many
  people later add files to it. `list_collections()` derives a collection's
  owner from its datasets when the collection row itself carries none (a
  document-only folder, or one from before this existed): the common owner if
  every dataset agrees, else `""`.
- **API** (`app/api/access.py`) — `require_owned_folder`/`require_folder`/
  `require_dataset` now check ownership first, ahead of and independent from
  `data_groups.Access` — unlike that system, this check is always on, not
  conditional on `DATA_GROUPS_ENFORCED`. A mismatch is a 404 ("No such
  folder"/"No such dataset"), matching how `data_groups` already hid a
  resource rather than revealing it exists with a 403.
  `app/api/v1/routes/excel_sources.py`'s `list_collections` filters inline
  the same way, before consulting `access.allows(...)`.

## Data

Two new nullable columns (migration in `app/accounts/store.py`'s
`MIGRATIONS`). `NULL`/`""` means "not recorded" — never written by anything
but the one-off backfill script and (going forward) never produced by a real
upload, which always has a signed-in uploader.

## Acceptance checks

1. Two accounts, each uploads: `GET /excel-sources` for one lists only its
   own folder.
2. One account cannot `GET`/`DELETE`/`PATCH` the other's `collection_id` or
   `dataset_id` by id — 404, not 403.
3. A folder with owner `""` (ambiguous/legacy) is absent from every account's
   list and returns 404 by id.
4. `scripts/backfill_collection_owners.py` (dry run) correctly separates
   single-uploader folders from mixed/unknown ones; `--apply` writes only the
   former.
5. `tests/test_excel_source_ownership.py` covers 1–3.

## Open questions

- **Admin visibility into orphaned folders** — deferred (see Not in scope).
  Until built, run the backfill script, check its "orphaned" output, and
  resolve those manually (direct `UPDATE excel_collections/excel_datasets SET
  owner = ...`) if the data is needed.

## Notes

- `backend/app/accounts/store.py` — migration adding `owner` to
  `excel_collections`/`excel_datasets`.
- `backend/app/engine/upload_store.py` — `owner` threaded through
  `save_dataset`/`collection_names`/`save_collection_name`, first-write-wins.
- `backend/app/engine/excel_sources.py` — `Dataset.owner`/`Collection.owner`;
  owner inheritance when joining an existing folder
  (`ingest_files`/`_load_shape`/`_name_new_collection`/
  `_name_documents_only_collection`/`rename_collection`); derivation in
  `list_collections()`.
- `backend/app/api/access.py` — ownership check in `require_owned_folder`/
  `require_folder`/`require_dataset`; `folder_access` now also takes `session`.
- `backend/app/api/v1/routes/excel_sources.py` — owner check in
  `list_collections`; `upload_files`'s direct `require_folder` call updated
  for the new signature.
- `backend/scripts/backfill_collection_owners.py` — new, one-off.
- `backend/tests/test_excel_source_ownership.py` — new.
- `backend/tests/test_access_without_snowflake.py`,
  `backend/tests/test_data_group_routes.py` — existing fixtures updated to
  stamp an `owner` on their test datasets/collections, since the real
  ownership check now runs ahead of the `data_groups` checks those tests
  exercise.
- Not run against the real local `backend/data/app.db` in this session — its
  existing folders have no owner yet. Run
  `pipenv run python scripts/backfill_collection_owners.py` (dry run) first to
  see what it would assign, then `--apply`, before relying on those folders
  being visible again.
