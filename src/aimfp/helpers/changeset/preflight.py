"""
AIMFP Helper Functions - Fan-out Readiness Preflight

`verify_fanout_ready()` — cheap insurance against a whole class of silent fan-out
corruption. A common, costly failure mode: workers branch from a `main` whose project.db
was NOT backfilled with stable keys, or NOT committed, before spawn — so every clone
inherits a mismatched baseline and export_state_changeset can't match entities.

This asserts, on current main, that the project is safe to fan out from:
  (a) every relevant row has a stable identity key (slugs on the work hierarchy,
      entity_key on functions/types) — i.e. backfill_semantic_keys has nothing left to do;
  (b) .aimfp-project/project.db is committed (clean for that path) at the would-be base;
  (c) no pending schema migration (recorded schema_version matches the running schema).

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

Pure read — never mutates. Run on main BEFORE spawning workers.
"""

import subprocess
import sqlite3
from typing import Dict, List

from ..utils import (
    Result,
    resolve_project_root,
    get_project_db_path,
    database_exists,
    _open_connection,
    get_return_statements,
)
from ._common import _HIERARCHY_TABLES, _table_has_column, PROJECT_DB_REL_PATH

# Running-schema version the package expects (kept in step with project.sql's
# schema_version seed). A recorded version below this means a migration is pending.
EXPECTED_SCHEMA_VERSION = "1.11"


def _count_missing_keys(conn: sqlite3.Connection) -> Dict[str, int]:
    """Effect: per-table count of rows still lacking their stable identity key."""
    missing: Dict[str, int] = {}
    for table, _kind, column in _HIERARCHY_TABLES:
        if not _table_has_column(conn, table, column):
            continue
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL OR {column} = ''"
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        n = row[0] if row else 0
        if n:
            missing[table] = n
    return missing


def _project_db_clean(project_root: str) -> bool:
    """Effect: True if project.db has no uncommitted changes (staged or unstaged)."""
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "status", "--porcelain", "--", PROJECT_DB_REL_PATH],
            capture_output=True, text=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == ""


def _recorded_schema_version(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        return row[0] if row else "unknown"
    except sqlite3.OperationalError:
        return "unknown"


def verify_fanout_ready() -> Result:
    """
    Preflight current main before spawning InterCommAIMFP workers.

    Returns:
        Result with data={ready: bool, blockers: [str], missing_keys: {table: count},
                          project_db_committed: bool, schema_version: str}.
        `ready` is True only when there are zero blockers.
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    db_path = get_project_db_path(project_root)
    if not database_exists(db_path):
        return Result(success=False, error="Project database not found")

    conn = _open_connection(db_path)
    try:
        missing = _count_missing_keys(conn)
        schema_version = _recorded_schema_version(conn)
    finally:
        conn.close()

    committed = _project_db_clean(project_root)

    blockers: List[str] = []
    if missing:
        detail = ", ".join(f"{t}={n}" for t, n in missing.items())
        blockers.append(
            f"Rows missing stable identity keys ({detail}). Run backfill_semantic_keys, "
            f"then commit project.db."
        )
    if not committed:
        blockers.append(
            "project.db has uncommitted changes — commit it on main BEFORE spawning workers "
            "so every worktree clone shares the same base."
        )
    if schema_version not in (EXPECTED_SCHEMA_VERSION, "unknown") and schema_version < EXPECTED_SCHEMA_VERSION:
        blockers.append(
            f"Schema version {schema_version} < expected {EXPECTED_SCHEMA_VERSION} — a migration "
            f"is pending. Open the project once to migrate, then re-check."
        )

    return Result(
        success=True,
        data={
            "ready": not blockers,
            "blockers": blockers,
            "missing_keys": missing,
            "project_db_committed": committed,
            "schema_version": schema_version,
        },
        return_statements=get_return_statements("verify_fanout_ready"),
    )
