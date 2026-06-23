"""
AIMFP Helper Functions - Changeset Common Helpers

Shared internals for the semantic changeset tools. Phase 1 contents: the slug
backfill effect used both by the optional `backfill_semantic_keys` tool and (lazily)
by `export_state_changeset` so a first export "just works" on a DB whose rows predate
the slug column.

(db-at-commit extraction and entity/reference diffing land here in Phases 2–3.)
"""

import json
import os
import subprocess
import tempfile
import sqlite3
from typing import Dict, Tuple, Optional, Any

from ..shared.slugs import mint_slug
from ..utils import AIMFP_PROJECT_DIR

# Relative path of the committed project DB inside the repo (for `git show`).
PROJECT_DB_REL_PATH = f"{AIMFP_PROJECT_DIR}/project.db"


# (table, kind, column) for every table that carries a stable minted key.
# `kind` is the key prefix, matching the minting done at creation time.
# Work-hierarchy rows use `slug`; code entities (Stage 2) use `entity_key`.
_HIERARCHY_TABLES: Tuple[Tuple[str, str, str], ...] = (
    ("milestones", "milestone", "slug"),
    ("tasks", "task", "slug"),
    ("subtasks", "subtask", "slug"),
    ("sidequests", "sidequest", "slug"),
    ("items", "item", "slug"),
    ("functions", "fn", "entity_key"),
    ("types", "ty", "entity_key"),
)


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Effect: True if `table` exists and has `column` (tolerant of very old DBs)."""
    try:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return False
    return column in cols


def _effect_mint_missing_slugs(
    conn: sqlite3.Connection,
    tables: Optional[Tuple[Tuple[str, str], ...]] = None,
) -> Dict[str, int]:
    """
    Effect: Mint a stable slug for every hierarchy row missing one. Idempotent.

    Only rows with NULL/empty slug are touched, so re-running is a no-op and re-exports
    are stable. Skips any table that lacks a `slug` column (pre-migration DB) rather than
    erroring. Commits once at the end.

    Args:
        conn: Open project.db connection
        tables: Optional override of (table, kind) pairs; defaults to all hierarchy tables

    Returns:
        Dict mapping table name -> number of rows newly slugged
    """
    targets = tables if tables is not None else _HIERARCHY_TABLES
    counts: Dict[str, int] = {}

    for table, kind, column in targets:
        if not _table_has_column(conn, table, column):
            continue

        rows = conn.execute(
            f"SELECT id, name FROM {table} WHERE {column} IS NULL OR {column} = ''"
        ).fetchall()

        minted = 0
        for row in rows:
            row_id, name = row[0], row[1]
            conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE id = ?",
                (mint_slug(kind, name), row_id),
            )
            minted += 1
        counts[table] = minted

    conn.commit()
    return counts


# ============================================================================
# DB-at-commit extraction (read committed project.db from a git ref)
# ============================================================================

def _effect_extract_db_at_commit(
    project_root: str,
    commit: str,
    rel_path: str = PROJECT_DB_REL_PATH,
) -> Optional[str]:
    """
    Effect: Extract the committed project.db blob at `commit` into a temp file.

    Uses ``git -C <root> show <commit>:<rel_path>`` (binary). Returns the temp file
    path, or None if the blob does not exist at that commit (e.g. the DB predates
    that point) or git is unavailable. Caller owns the temp file (delete when done).
    """
    try:
        result = subprocess.run(
            ["git", "-C", project_root, "show", f"{commit}:{rel_path}"],
            capture_output=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0 or not result.stdout:
        return None

    fd, path = tempfile.mkstemp(suffix=".db", prefix="aimfp_changeset_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(result.stdout)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return path


def _open_readonly(db_path: str) -> sqlite3.Connection:
    """Effect: Open a SQLite DB read-only with Row factory."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# Semantic key indexes (id <-> stable semantic key, per table)
# ============================================================================

def serialize_key(key: Dict[str, Any]) -> str:
    """Pure: Canonical string form of a semantic key dict (for set/dict membership)."""
    return json.dumps(key, sort_keys=True)


def _row_get(row, col):
    """Effect: Read a column from a sqlite3.Row, returning None if the column is absent."""
    try:
        return row[col] if col in row.keys() else None
    except (IndexError, KeyError):
        return None


def code_entity_key(file_path_value, name, entity_key):
    """
    Pure: Stable key for a function/type. Prefers the immutable `entity_key` (Stage 2) so
    rename/move become 'modify' ops; falls back to the natural `(file, name)` key when
    entity_key is absent (pre-Stage-2 / un-backfilled rows → v1.10 behavior preserved).
    """
    if entity_key:
        return {"entity_key": entity_key}
    return {"file": file_path_value, "name": name}


def _safe_rows(conn: Optional[sqlite3.Connection], sql: str):
    """Effect: Run a query tolerantly; return [] if table missing or conn is None."""
    if conn is None:
        return []
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []


def build_key_indexes(conn: Optional[sqlite3.Connection]) -> Dict[str, Dict[str, Any]]:
    """
    Effect: Build id<->semantic-key indexes for every keyed table in a project.db.

    Returns a dict: ``{table_name: {"id2key": {id: keydict}, "key2id": {serialized: id}}}``.
    A None connection (e.g. base commit lacking the DB) yields empty indexes.

    Stable keys (integer-free, cross-clone):
      files: {path} · functions/types: {file, name} · modules/themes/flows/completion_path: {name}
      milestones/tasks/subtasks/sidequests/items: {slug}
    Build order matters: files before functions/types (need file path); parents before children.
    """
    idx: Dict[str, Dict[str, Any]] = {}

    def register(table: str, pairs):
        id2key, key2id = {}, {}
        for row_id, key in pairs:
            id2key[row_id] = key
            key2id[serialize_key(key)] = row_id
        idx[table] = {"id2key": id2key, "key2id": key2id}

    # files (key: path)
    register("files", (
        (r["id"], {"path": r["path"]}) for r in _safe_rows(conn, "SELECT id, path FROM files")
    ))
    files_id2key = idx["files"]["id2key"]

    def file_path(file_id):
        if file_id is None:
            return None
        k = files_id2key.get(file_id)
        return k["path"] if k else None

    # simple name-keyed tables
    for table in ("modules", "themes", "flows", "completion_path"):
        register(table, (
            (r["id"], {"name": r["name"]}) for r in _safe_rows(conn, f"SELECT id, name FROM {table}")
        ))

    # functions / types (key: entity_key if present, else file path + name)
    register("functions", (
        (r["id"], code_entity_key(file_path(r["file_id"]), r["name"], _row_get(r, "entity_key")))
        for r in _safe_rows(conn, "SELECT * FROM functions")
    ))
    register("types", (
        (r["id"], code_entity_key(file_path(r["file_id"]), r["name"], _row_get(r, "entity_key")))
        for r in _safe_rows(conn, "SELECT * FROM types")
    ))

    # slug-keyed work hierarchy
    for table in ("milestones", "tasks", "subtasks", "sidequests", "items"):
        register(table, (
            (r["id"], {"slug": r["slug"]}) for r in _safe_rows(conn, f"SELECT id, slug FROM {table}")
        ))

    return idx


def key_has_null(key: Optional[Dict[str, Any]]) -> bool:
    """Pure: True if the key is missing or contains any None component (unresolvable)."""
    if not key:
        return True
    return any(v is None for v in key.values())
