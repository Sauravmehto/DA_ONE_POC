"""Read the audit trail — who asked what, and what it cost.

The trail is a table in the app's own SQLite file, so anything that speaks
SQLite can read it. This exists so nobody has to remember the column names or
how the token counts add up.

    python scripts/audit.py                    # the last 50 events
    python scripts/audit.py --spend            # what each day cost, by model
    python scripts/audit.py --who              # spend per person
    python scripts/audit.py --errors           # what failed
    python scripts/audit.py --conversation c1  # one thread, end to end
    python scripts/audit.py --db /app/data/app.db --limit 200

On a server the file is at /app/data/app.db inside the backend container:

    docker exec -it <container> python scripts/audit.py --spend

READING THE TOKEN COUNTS. `input_tokens` is not the size of the prompt: the
API reports it as the part that was NOT served from cache. The prompt is
input + cache_read + cache_write. A cache read is billed at about a tenth of
an input token and a write at 1.25x, so a conversation that writes the cache
every turn and never reads it costs more than no caching at all.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "app.db"

PROMPT = ("COALESCE(input_tokens,0) + COALESCE(cache_read_tokens,0) "
          "+ COALESCE(cache_write_tokens,0)")


def connect(path: Path) -> sqlite3.Connection:
    """Open read-only, so this can never hold up the running app."""
    if not path.exists():
        sys.exit(f"No app store at {path}. Pass --db, or check APP_DB_PATH.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def show(rows, headers: list[str]) -> None:
    if not rows:
        print("Nothing recorded yet.")
        return
    widths = [max(len(h), *(len(str(r[i] if r[i] is not None else "")) for r in rows))
              for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(str(v if v is not None else "").ljust(w)
                        for v, w in zip(row, widths)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path,
                        default=Path(os.environ.get("APP_DB_PATH") or DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--spend", action="store_true", help="per day and model")
    parser.add_argument("--who", action="store_true", help="per person")
    parser.add_argument("--errors", action="store_true", help="what failed")
    parser.add_argument("--conversation", help="one chat thread, oldest first")
    args = parser.parse_args()

    conn = connect(args.db)

    if args.spend:
        rows = conn.execute(
            f"SELECT substr(event_ts, 1, 10) AS day, model, COUNT(*) AS events, "
            f"SUM(COALESCE(model_calls,0)) AS calls, SUM({PROMPT}) AS prompt_tokens, "
            "SUM(COALESCE(cache_read_tokens,0)) AS from_cache, "
            "SUM(COALESCE(output_tokens,0)) AS output_tokens "
            "FROM audit_events WHERE model IS NOT NULL "
            "GROUP BY day, model ORDER BY day DESC, model LIMIT ?",
            (args.limit,),
        ).fetchall()
        show([tuple(r) for r in rows],
             ["day", "model", "events", "calls", "prompt", "from cache", "output"])
        return

    if args.who:
        rows = conn.execute(
            f"SELECT audit_user, COUNT(*) AS events, "
            f"SUM({PROMPT}) AS prompt_tokens, "
            "SUM(COALESCE(output_tokens,0)) AS output_tokens "
            "FROM audit_events WHERE model IS NOT NULL "
            "GROUP BY audit_user ORDER BY prompt_tokens DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
        show([tuple(r) for r in rows], ["who", "events", "prompt", "output"])
        return

    if args.errors:
        rows = conn.execute(
            "SELECT event_ts, audit_user, tool_name, error_message "
            "FROM audit_events WHERE status = 'error' "
            "ORDER BY event_id DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
        show([(r["event_ts"][:19], r["audit_user"], r["tool_name"],
               (r["error_message"] or "")[:80]) for r in rows],
             ["when", "who", "tool", "error"])
        return

    if args.conversation:
        rows = conn.execute(
            f"SELECT event_ts, tool_name, status, model, {PROMPT} AS prompt_tokens, "
            "COALESCE(output_tokens,0) AS output_tokens, duration_ms "
            "FROM audit_events WHERE conversation_id = ? ORDER BY event_id",
            (args.conversation,),
        ).fetchall()
        show([(r["event_ts"][:19], r["tool_name"], r["status"], r["model"] or "",
               r["prompt_tokens"], r["output_tokens"], r["duration_ms"]) for r in rows],
             ["when", "tool", "status", "model", "prompt", "output", "ms"])
        return

    rows = conn.execute(
        "SELECT event_ts, audit_user, tool_name, status, "
        "COALESCE(row_count, '') AS rows_, duration_ms, "
        "COALESCE(user_question, '') AS question "
        "FROM audit_events ORDER BY event_id DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    show([(r["event_ts"][:19], r["audit_user"], r["tool_name"], r["status"],
           r["rows_"], r["duration_ms"], r["question"][:60]) for r in rows],
         ["when", "who", "tool", "status", "rows", "ms", "question"])


if __name__ == "__main__":
    main()
