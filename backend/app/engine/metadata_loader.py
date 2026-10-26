"""Loads the hand-curated semantic layers that give Claude business meaning.

A database tells you what EXISTS (tables, column types). A layer tells Claude
what things MEAN: what a column represents, which tables must never be joined,
and the exact SQL for named business metrics.

Without this, Claude writes syntactically valid SQL that is semantically wrong.

Layers live in the app's own store, one per source, and are read here by
source key — a Snowflake database name, or a source on any other connector.
See app/semantic/store.py.

Hardening note: each document is parsed inside its own try/except. A single
broken layer (e.g. a missing closing quote) degrades to "that one source has no
context" instead of taking every source offline with it.
"""

import logging
import sys
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _named(source_key: str) -> str:
    """What to call a source in the model's context.

    A warehouse database is its own name. A source on a saved connection is
    "SQLSERVER:<connection>/<database>", and the connection id means nothing
    to the model — the database does."""
    if ":" not in source_key:
        return source_key.upper()
    return source_key.rsplit("/", 1)[-1] or source_key


class MetadataStore:
    def __init__(self, documents: Any = None) -> None:
        # Where layers are read from: the app's store by default, or anything
        # with the same two methods (see semantic.store.FolderDocuments).
        self._documents = documents
        self._configs: dict[str, dict[str, Any]] = {}
        self._failed: dict[str, str] = {}  # source -> error, for diagnostics
        # Lookups are case-insensitive, but a source is listed as it is
        # written: a SQL Server source carries a connection id that is not.
        self._named: dict[str, str] = {}
        self._loaded = False
        self._marker = ""

    def _source(self):
        """Where layers are read from. Imported on use, not at module load:
        the semantic package reads these loaders back when it publishes."""
        if self._documents is not None:
            return self._documents
        from app.semantic import store as layer_store

        return layer_store

    def invalidate(self) -> None:
        """Drop the cache so the next read reloads the layers.

        Kept for the publishing path, which invalidates both loaders in its own
        process. It is no longer what makes a publish visible: _load compares
        the store's version marker on every read, so OTHER worker processes —
        which never see this call — pick the change up on their next request.
        """
        self._loaded = False
        self._marker = ""
        self._configs.clear()
        self._failed.clear()

    def _load(self) -> None:
        source = self._source()
        marker = source.version_marker()
        if self._loaded and marker == self._marker:
            return

        # Rebuilt from scratch, not merged: a reload has to drop a source whose
        # layer was removed, and replace one that no longer parses.
        self._configs = {}
        self._failed = {}
        self._named = {}
        for source_key, document in source.published_documents().items():
            try:
                cfg = tomllib.loads(document.decode("utf-8"))
            except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
                # One malformed layer must not blow up the rest.
                self._failed[source_key] = str(exc)
                log.error("Skipping unparseable layer for %s: %s", source_key, exc)
                continue
            self._configs[source_key.upper()] = cfg
            self._named[source_key.upper()] = source_key
        self._marker = marker
        self._loaded = True

    def documented_databases(self) -> list[str]:
        self._load()
        return sorted(self._named.values())

    def dashboard_heuristics(self, database: str) -> str:
        """Domain-specific dashboard guidance for a database (the config's
        `dashboard_heuristics` block), fed to the dashboard planner. Empty string
        if the database has none."""
        self._load()
        cfg = self._configs.get(database.upper()) or {}
        return str(cfg.get("dashboard_heuristics") or "").strip()

    def failed_configs(self) -> dict[str, str]:
        """Filenames that failed to parse, with their error. Surfaced so a
        broken config is visible rather than silently missing."""
        self._load()
        return dict(self._failed)

    def _table_meta(self, database: str, table_full_name: str) -> dict[str, Any] | None:
        """The config block for one fully-qualified table, matched case-insensitively."""
        self._load()
        cfg = self._configs.get(database.upper()) or {}
        want = str(table_full_name).upper()
        for name, meta in (cfg.get("tables") or {}).items():
            if str(name).upper() == want:
                return meta
        return None

    def date_columns(self, database: str, table_full_name: str) -> dict[str, str]:
        """The table's declared date dimensions as {COLUMN_UPPER: role}. Empty when
        the table has no annotated date column (e.g. PC_RISK_LIMITS)."""
        meta = self._table_meta(database, table_full_name) or {}
        return {str(c).upper(): str(r) for c, r in (meta.get("date_roles") or {}).items()}

    def resolve_date_column(self, database: str, table_full_name: str,
                            role: str | None = None) -> dict[str, Any]:
        """Resolve which date column a date parameter binds to for a table, FROM
        METADATA — never guessed. Returns {status, table, column, candidates, reason}:

          - "ok"           exactly one column matches (column is set)
          - "unsupported"  the table has no annotated date column at all → it
                           cannot support a date parameter; the caller must ask.
          - "ambiguous"    more than one column could apply (role given but
                           several match, or no role and several date columns) →
                           the caller must ask which one.
          - "role_not_found" a role was requested that this table doesn't declare
                           (its date columns exist but none has that role).

        The caller (never this module) decides to stop and ask on anything but "ok".
        """
        cols = self.date_columns(database, table_full_name)  # {COL: role}
        candidates = sorted(f"{c} ({r})" for c, r in cols.items())
        if not cols:
            return {"status": "unsupported", "table": table_full_name, "column": None,
                    "candidates": [], "reason": "no annotated date column on this table"}
        if role:
            matches = [c for c, r in cols.items() if r == role]
            if len(matches) == 1:
                return {"status": "ok", "table": table_full_name, "column": matches[0],
                        "role": role, "candidates": candidates}
            if not matches:
                return {"status": "role_not_found", "table": table_full_name, "column": None,
                        "candidates": candidates,
                        "reason": f"no column with role '{role}' on this table"}
            return {"status": "ambiguous", "table": table_full_name, "column": None,
                    "candidates": sorted(matches), "reason": f"multiple columns with role '{role}'"}
        if len(cols) == 1:
            c = next(iter(cols))
            return {"status": "ok", "table": table_full_name, "column": c,
                    "role": cols[c], "candidates": candidates}
        return {"status": "ambiguous", "table": table_full_name, "column": None,
                "candidates": candidates,
                "reason": "table has several date columns; a role must pick one"}

    def currency(self, database: str | None) -> str | None:
        """ISO 4217 code every amount in `database` is reported in, or None.

        None for an undeclared database (keeps the dollar default) and for a malformed value."""
        if not database:
            return None
        self._load()
        raw = (self._configs.get(database.upper()) or {}).get("currency")
        code = raw.strip().upper() if isinstance(raw, str) else ""
        return code if len(code) == 3 and code.isascii() and code.isalpha() else None

    def build_context(self, database: str) -> str:
        """Render everything Claude needs to know about a database, as text."""
        self._load()
        cfg = self._configs.get(database.upper())
        if not cfg:
            return (
                f"No curated business context exists for '{database}'. "
                "Rely on the raw schema and be explicit about assumptions."
            )

        out: list[str] = []
        out.append(f"# {cfg.get('display_name', database)} ({_named(database)})")
        if cfg.get("description"):
            out.append(str(cfg["description"]).strip())

        # Stated once, here, rather than left to the model to infer from column
        # names: a KPI row carries no column_formats, so FORMAT is the only
        # place its currency can be declared, and bare 'currency' prints a
        # dollar sign over dirhams.
        code = self.currency(database)
        if code:
            out.append(
                f"\nReporting currency: {code}. Every monetary amount in this "
                f"database is in {code}, never US dollars. Write every money KPI "
                f"row as FORMAT 'currency:{code}' and every money column in "
                f'column_formats as {{"format": "currency", "currency": "{code}"}}.'
            )

        if cfg.get("join_rules"):
            out.append("\n## Join rules (follow these exactly)")
            out.append(str(cfg["join_rules"]).strip())

        tables = cfg.get("tables") or {}
        if tables:
            out.append("\n## Tables")
            for full_name, meta in tables.items():
                out.append(f"\n### {full_name}")
                if meta.get("description"):
                    out.append(str(meta["description"]).strip())
                if meta.get("notes"):
                    # Gotchas: enum values, text-encoded numbers, PII flags.
                    out.append(f"IMPORTANT: {str(meta['notes']).strip()}")
                cols = meta.get("key_columns") or {}
                if cols:
                    out.append("Key columns:")
                    for col, desc in cols.items():
                        out.append(f"  - {col}: {desc}")
                date_roles = meta.get("date_roles") or {}
                if date_roles:
                    out.append(
                        "Date columns (bind a date parameter to one of these BY ROLE; "
                        "never guess a date column from its name):")
                    for col, role in date_roles.items():
                        out.append(f"  - {col}: role={role}")
                else:
                    out.append(
                        "Date columns: NONE — this table has no as-of/date dimension, "
                        "so it cannot be filtered by a date parameter.")

        metrics = cfg.get("metrics") or {}
        if metrics:
            out.append("\n## Business metrics (use this exact SQL, do not invent your own)")
            for name, meta in metrics.items():
                if not isinstance(meta, dict):
                    continue
                out.append(f"\n### {name}")
                if meta.get("definition"):
                    out.append(f"Definition: {meta['definition']}")
                if meta.get("sql"):
                    out.append(f"SQL: {meta['sql']}")

        return "\n".join(out)


metadata_store = MetadataStore()
