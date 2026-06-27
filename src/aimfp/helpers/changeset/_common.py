"""
AIMFP Helper Functions - Changeset Common Helpers

Shared internals for the semantic changeset tools. Phase 1 contents: the slug
backfill effect used both by the optional `backfill_semantic_keys` tool and (lazily)
by `export_state_changeset` so a first export "just works" on a DB whose rows predate
the slug column.

(db-at-commit extraction and entity/reference diffing land here in Phases 2–3.)
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import sqlite3
from typing import Dict, List, Tuple, Optional, Any

from ..shared.slugs import mint_slug
from ..utils import AIMFP_PROJECT_DIR

# Relative path of the committed project DB inside the repo (for `git show`).
PROJECT_DB_REL_PATH = f"{AIMFP_PROJECT_DIR}/project.db"

# ============================================================================
# InterCommAIMFP presence gating (§3 of MERGE-ORCHESTRATOR-AND-BRIDGES.md)
# ============================================================================
#
# Every tool in this package is an InterCommAIMFP *addon surface*. The tools stay
# registered always (consistent with the existing changeset tools, which are also
# always present and merely labelled "for use with InterCommAIMFP"), but anything
# that should only fire / be advised when the coordination layer is installed keys
# off this cheap, one-directional presence signal:
#
#     <project_root>/.intercomm-aimfp/intercomm.db
#
# AIMFP only checks that the file EXISTS — it never reads inside that DB (doing so
# would couple AIMFP to InterComm's schema, violating the boundary in §6). When the
# signal is absent, AIMFP behaves exactly as it does today.

INTERCOMM_DIR_NAME = ".intercomm-aimfp"
INTERCOMM_DB_NAME = "intercomm.db"


def intercomm_present(project_root: str) -> bool:
    """
    Effect: True if InterCommAIMFP's shared coordination DB exists at the project root.

    Presence-only signal — a single ``path.exists`` against the AIMFP-resolved
    (worktree-aware) project root. AIMFP MUST NOT read inside that DB; existence is
    the only signal the addon surface needs.
    """
    return os.path.exists(os.path.join(project_root, INTERCOMM_DIR_NAME, INTERCOMM_DB_NAME))


# ============================================================================
# Server-side changeset persistence (§2.1 — the changeset_id handle)
# ============================================================================
#
# The merge path's one ergonomic wall: an LLM harness has no pipe between two tool
# calls, so feeding an 8k-token changeset from export -> apply meant re-transcribing
# the whole object by hand. Instead export persists the changeset under the project
# and returns a small handle; apply reloads it by handle. The id is a pure function
# of (base_commit, branch) so re-exporting a branch is idempotent (same id, same file).

CHANGESET_DIR_REL = f"{AIMFP_PROJECT_DIR}/changesets"


def changeset_id_for(base_commit: Optional[str], branch: str) -> str:
    """Pure: stable handle for the changeset of (base_commit -> branch tip)."""
    digest = hashlib.sha1(f"{base_commit or ''}\x00{branch}".encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", branch or "branch").strip("-") or "branch"
    return f"cs-{safe}-{digest}"


def _changeset_dir(project_root: str) -> str:
    """Pure: absolute path of the per-project changeset store."""
    return os.path.join(project_root, CHANGESET_DIR_REL)


def _changeset_path(project_root: str, changeset_id: str) -> str:
    """Pure: absolute path of one persisted changeset JSON file."""
    return os.path.join(_changeset_dir(project_root), f"{changeset_id}.json")


def _effect_persist_changeset(project_root: str, changeset_id: str,
                              changeset: Dict[str, Any]) -> Optional[str]:
    """
    Effect: Write a changeset to ``.aimfp-project/changesets/<id>.json``.

    Returns the path on success, or None if the directory/file could not be written
    (persistence is best-effort — export still returns the full inline object too, so
    a write failure degrades to the legacy hand-carry rather than failing the export).
    """
    try:
        os.makedirs(_changeset_dir(project_root), exist_ok=True)
        path = _changeset_path(project_root, changeset_id)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(changeset, fh, sort_keys=True, indent=2)
        return path
    except OSError:
        return None


def _effect_load_changeset(project_root: str, changeset_id: str) -> Optional[Dict[str, Any]]:
    """Effect: Load a persisted changeset by handle, or None if absent/unreadable."""
    path = _changeset_path(project_root, changeset_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# ============================================================================
# Cheap changeset summary (§5.3) — counts only, never the full object
# ============================================================================

def summarize_changeset(changeset: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure: small counts block for a changeset — affordable to read for every branch.

    Shape::

        {
          "entities": {kind: {add, modify, delete}, ...},
          "references": {kind: {add, remove}, ...},
          "touched_files": [path, ...],
          "touched_modules": [name, ...],
          "totals": {"entities": int, "references": int, "warnings": int},
        }
    """
    entities = changeset.get("entities") or []
    references = changeset.get("references") or []
    warnings = changeset.get("warnings") or []

    ent_counts: Dict[str, Dict[str, int]] = {}
    touched_files: set = set()
    touched_modules: set = set()
    for e in entities:
        kind = e.get("kind", "?")
        op = e.get("op", "?")
        ent_counts.setdefault(kind, {"add": 0, "modify": 0, "delete": 0})
        if op in ent_counts[kind]:
            ent_counts[kind][op] += 1
        key = e.get("semantic_key") or {}
        if kind == "files" and key.get("path"):
            touched_files.add(key["path"])
        if kind == "modules" and key.get("name"):
            touched_modules.add(key["name"])
        attrs = e.get("attributes") or {}
        if attrs.get("file"):
            touched_files.add(attrs["file"])

    ref_counts: Dict[str, Dict[str, int]] = {}
    for r in references:
        kind = r.get("kind", "?")
        op = r.get("op", "?")
        ref_counts.setdefault(kind, {"add": 0, "remove": 0})
        if op in ref_counts[kind]:
            ref_counts[kind][op] += 1
        if r.get("file"):
            f = r["file"]
            if isinstance(f, dict) and f.get("path"):
                touched_files.add(f["path"])

    return {
        "entities": ent_counts,
        "references": ref_counts,
        "touched_files": sorted(touched_files),
        "touched_modules": sorted(touched_modules),
        "totals": {
            "entities": len(entities),
            "references": len(references),
            "warnings": len(warnings),
        },
    }


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
