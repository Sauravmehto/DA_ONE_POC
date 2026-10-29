"""One-off: give every folder that predates spec 0012 an owner, where one can
be told.

    pipenv run python scripts/backfill_collection_owners.py        # dry run
    pipenv run python scripts/backfill_collection_owners.py --apply

Before this, excel_collections/excel_datasets had no owner column at all —
every signed-in account could see every folder. Going forward, every new
upload is stamped with its uploader (app/engine/excel_sources.py); this
script is only for what was already there.

For each folder (grouped exactly as app.engine.excel_sources.list_collections
already groups datasets into one), it looks at every file's own uploaded_by
(recorded on each DatasetFile since the app store has existed):

- Exactly one person uploaded every file in it -> that folder, and every
  dataset in it, is assigned to them.
- Different people, or no uploaded_by recorded at all (pre-dates that field
  too) -> left alone. Nobody can see that folder through the ordinary routes
  until an administrator assigns it by hand (not yet built — see spec 0012,
  "admin visibility into orphaned folders").

Prints what it would do; writes nothing until --apply. Safe to run more than
once — a folder already given an owner (by this script or by a real upload)
is skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so app imports

from app.accounts import store
from app.engine import excel_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="write the assignments; otherwise only print them")
    args = parser.parse_args()

    assigned = 0
    orphaned = 0
    for collection in excel_sources.list_collections():
        if collection.owner:
            continue  # already owned — a real upload, or an earlier run of this script
        uploaders = {
            f.uploaded_by for d in collection.datasets for f in d.files if f.uploaded_by
        }
        if len(uploaders) != 1:
            orphaned += 1
            print(
                f"[orphaned] {collection.collection_id}  {collection.name!r}  "
                f"({len(uploaders)} distinct uploader(s): {sorted(uploaders)})"
            )
            continue
        owner = uploaders.pop()
        assigned += 1
        print(f"[assign]   {collection.collection_id}  {collection.name!r}  -> {owner}")
        if args.apply:
            with store.write() as conn:
                conn.execute(
                    "UPDATE excel_collections SET owner = ? WHERE collection_id = ?",
                    (owner, collection.collection_id),
                )
                conn.execute(
                    "UPDATE excel_datasets SET owner = ? WHERE collection_id = ? "
                    "AND (owner IS NULL OR owner = '')",
                    (owner, collection.collection_id),
                )

    print(
        f"\n{assigned} folder(s) assigned, {orphaned} left orphaned "
        f"(no owner — invisible until an administrator assigns one)."
        + ("" if args.apply else "\nDry run — nothing written. Re-run with --apply.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
