"""
AIMFP Database - Foundation Connection Layer

Single source of truth for ALL database operations in the AIMFP package.
Every module that needs database access imports from here.

Databases managed:
    1. aimfp_core.db        — Global read-only (directives, helpers, flows)
    2. project.db           — Per-project mutable (files, functions, tasks)
    3. user_preferences.db  — Per-project mutable (settings, preferences)
    4. user_directives.db   — Per-project optional (Use Case 2 automation)
    5. mcp_runtime.db       — Global mutable (MCP server runtime state)

DRY hierarchy position:
    database/connection.py  (THIS FILE — foundation, imported by everything)
        └── helpers/utils.py (re-exports for backward compatibility)
            └── helpers/{category}/_common.py (category re-exports)
                └── helpers/{category}/{file}.py (individual helpers)
        └── watchdog/ (imports directly)
        └── wrappers/ (imports directly)
        └── mcp_server/ (imports directly)

Design:
    - Pure functions for path resolution and data conversion
    - Effect functions (prefixed _effect_) for I/O operations
    - All connections use row_factory for dict-like access
    - Immutable result types (frozen dataclasses)
    - No OOP beyond frozen dataclasses
"""

import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any, Final


# ============================================================================
# Global Constants
# ============================================================================

AIMFP_PROJECT_DIR: Final[str] = ".aimfp-project"

CORE_DB_NAME: Final[str] = "aimfp_core.db"
PROJECT_DB_NAME: Final[str] = "project.db"
USER_PREFERENCES_DB_NAME: Final[str] = "user_preferences.db"
USER_DIRECTIVES_DB_NAME: Final[str] = "user_directives.db"
MCP_RUNTIME_DB_NAME: Final[str] = "mcp_runtime.db"


# ============================================================================
# Project Root Cache (set once per server session)
# ============================================================================

_cached_project_root: Optional[str] = None


def set_project_root(project_root: str) -> None:
    """Effect: Cache the project root for the current server session."""
    global _cached_project_root
    _cached_project_root = project_root


def get_cached_project_root() -> str:
    """
    Get cached project root. Raises RuntimeError if not set.

    Called by helpers to resolve their database path without parameters.
    """
    if _cached_project_root is None:
        raise RuntimeError(
            "Project root not established. "
            "Call aimfp_init or aimfp_run first."
        )
    return _cached_project_root


def resolve_project_root() -> str:
    """
    Resolve project root: cache first, discovery fallback.

    Cascade:
        1. Return cached value if set
        2. Discover from .aimfp-project/project.db in cwd
        3. Raise RuntimeError if neither works

    Caches discovered value for subsequent calls.
    """
    if _cached_project_root is not None:
        return _cached_project_root
    discovered = _discover_project_root()
    if discovered is not None:
        set_project_root(discovered)
        return discovered
    raise RuntimeError(
        "Project root not established. "
        "Call aimfp_init or aimfp_run first."
    )


def clear_project_root_cache() -> None:
    """Effect: Clear the cache (for testing)."""
    global _cached_project_root
    _cached_project_root = None


def _git_toplevel(start_dir: str) -> Optional[str]:
    """
    Effect: Return the git working-tree top-level for ``start_dir``, or None.

    Runs ``git rev-parse --show-toplevel``. For a linked git worktree this
    returns the WORKTREE's path (not the shared main checkout) — which is exactly
    what binds project.db to the worktree's own .aimfp-project copy. Returns None
    for non-git directories or when git is unavailable.

    Deliberately NOT ``--git-common-dir``: that maps every linked worktree back
    to the main repo (correct for a shared coordination DB, wrong for per-worker
    project.db). See docs/intercommaimfptools/WORKTREE-ISOLATION-BUG.md.
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            cwd=start_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return top or None


def _has_project_db(root: str) -> bool:
    """Effect: True if ``root`` holds an initialized .aimfp-project/project.db."""
    aimfp_dir = Path(root) / AIMFP_PROJECT_DIR
    return aimfp_dir.is_dir() and os.path.exists(str(aimfp_dir / PROJECT_DB_NAME))


def _discover_project_root() -> Optional[str]:
    """
    Effect: Discover the LIVE project root from the server's CWD.

    Worktree-aware: when CWD lies inside a git working tree, the root resolves to
    that tree's top-level (``git rev-parse --show-toplevel``). For a linked git
    worktree this is the WORKTREE path, so a server launched inside a worktree
    binds to the WORKTREE's own .aimfp-project/project.db — not the shared main
    checkout's. This is the fix for the worktree-isolation bug: parallel workers
    must each read/write their own worktree's project.db, never race main's.

    Resolution order:
        1. git top-level of CWD, if it holds .aimfp-project/project.db
        2. CWD itself, if it holds .aimfp-project/project.db (non-git / single tree)
        3. None (project not initialized)

    Returns the LIVE on-disk root, NOT the stored infrastructure.project_root
    (which may be an absolute path to a different checkout — that staleness is
    the bug). Callers that surface the stored value reconcile it to this live
    root (see aimfp_run -> _reconcile_stored_project_root).

    Does NOT walk up parent directories beyond the git top-level — the MCP server
    is started in the project (or worktree) directory by the AI client.
    """
    toplevel = _git_toplevel(str(Path.cwd()))
    if toplevel is not None and _has_project_db(toplevel):
        return toplevel

    cwd = str(Path.cwd())
    if _has_project_db(cwd):
        return cwd

    return None


# ============================================================================
# Result Types (Immutable)
# ============================================================================

@dataclass(frozen=True)
class Result:
    """Generic immutable result type for operations."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryResult:
    """Immutable result for database query operations."""
    success: bool
    rows: Tuple[Dict[str, Any], ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaResult:
    """Immutable result for schema introspection operations."""
    success: bool
    tables: Tuple[str, ...] = ()
    fields: Tuple[Dict[str, Any], ...] = ()
    error: Optional[str] = None


# ============================================================================
# Database Path Resolution — Global Databases
# ============================================================================

def _get_database_dir() -> str:
    """Pure: Get the directory containing global databases (src/aimfp/database/)."""
    return str(Path(__file__).parent)


def get_core_db_path() -> str:
    """Pure: Get absolute path to aimfp_core.db."""
    return str(Path(_get_database_dir()) / CORE_DB_NAME)


def get_mcp_runtime_db_path() -> str:
    """Pure: Get absolute path to mcp_runtime.db."""
    return str(Path(_get_database_dir()) / MCP_RUNTIME_DB_NAME)


# ============================================================================
# Database Path Resolution — Per-Project Databases
# ============================================================================

def get_aimfp_project_dir(project_root: str) -> str:
    """Pure: Get absolute path to .aimfp-project directory."""
    return str(Path(project_root) / AIMFP_PROJECT_DIR)


def get_project_db_path(project_root: str) -> str:
    """Pure: Get absolute path to project.db for a given project."""
    return str(Path(project_root) / AIMFP_PROJECT_DIR / PROJECT_DB_NAME)


def get_user_preferences_db_path(project_root: str) -> str:
    """Pure: Get absolute path to user_preferences.db for a given project."""
    return str(Path(project_root) / AIMFP_PROJECT_DIR / USER_PREFERENCES_DB_NAME)


def get_user_directives_db_path(project_root: str) -> str:
    """Pure: Get absolute path to user_directives.db for a given project."""
    return str(Path(project_root) / AIMFP_PROJECT_DIR / USER_DIRECTIVES_DB_NAME)



def database_exists(db_path: str) -> bool:
    """Pure: Check if database file exists."""
    return os.path.exists(db_path)


# ============================================================================
# Connection Management
# ============================================================================

def _open_connection(db_path: str) -> sqlite3.Connection:
    """
    Effect: Open database connection with row factory and performance pragmas.

    Row factory enables dict-like access to columns by name.
    Pragmas applied:
        - WAL journal mode: better concurrent read/write performance
        - synchronous=NORMAL: safe with WAL, faster than FULL
        - temp_store=memory: temp tables in RAM instead of disk
        - cache_size=10000: ~40MB page cache (10K × 4KB pages)
        - foreign_keys=ON: enforce referential integrity
    Caller is responsible for closing the connection.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _close_connection(conn: sqlite3.Connection) -> None:
    """Effect: Close database connection safely."""
    if conn:
        conn.close()


# ============================================================================
# Convenience Connection Openers (per database)
# ============================================================================

def _open_core_connection() -> sqlite3.Connection:
    """
    Effect: Open connection to aimfp_core.db (global, read-only).

    Raises FileNotFoundError if database does not exist.
    """
    db_path = get_core_db_path()
    if not database_exists(db_path):
        raise FileNotFoundError(f"Core database not found: {db_path}")
    return _open_connection(db_path)


def _open_project_connection(project_root: str) -> sqlite3.Connection:
    """
    Effect: Open connection to project.db.

    Raises FileNotFoundError if database does not exist.
    """
    db_path = get_project_db_path(project_root)
    if not database_exists(db_path):
        raise FileNotFoundError(f"Project database not found: {db_path}")
    return _open_connection(db_path)


def _open_preferences_connection(project_root: str) -> sqlite3.Connection:
    """
    Effect: Open connection to user_preferences.db.

    Raises FileNotFoundError if database does not exist.
    """
    db_path = get_user_preferences_db_path(project_root)
    if not database_exists(db_path):
        raise FileNotFoundError(f"User preferences database not found: {db_path}")
    return _open_connection(db_path)


def _open_directives_connection(project_root: str) -> sqlite3.Connection:
    """
    Effect: Open connection to user_directives.db.

    Raises FileNotFoundError if database does not exist.
    """
    db_path = get_user_directives_db_path(project_root)
    if not database_exists(db_path):
        raise FileNotFoundError(f"User directives database not found: {db_path}")
    return _open_connection(db_path)


def _open_mcp_runtime_connection() -> sqlite3.Connection:
    """
    Effect: Open connection to mcp_runtime.db (global, mutable).

    Creates the database if it does not exist.
    """
    db_path = get_mcp_runtime_db_path()
    return _open_connection(db_path)



# ============================================================================
# Stateless Query Functions (open-close per call)
# ============================================================================

def _effect_query_one(
    db_path: str,
    sql: str,
    params: Tuple[Any, ...] = (),
) -> Optional[Dict[str, Any]]:
    """
    Effect: Execute a query and return the first row as a dict.

    Connection opened and closed per call. Returns None if no rows.
    """
    conn = _open_connection(db_path)
    try:
        cursor = conn.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _effect_query_all(
    db_path: str,
    sql: str,
    params: Tuple[Any, ...] = (),
) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Execute a query and return all rows as a tuple of dicts.

    Connection opened and closed per call.
    """
    conn = _open_connection(db_path)
    try:
        cursor = conn.execute(sql, params)
        return tuple(dict(row) for row in cursor.fetchall())
    finally:
        conn.close()


def _effect_execute(
    db_path: str,
    sql: str,
    params: Tuple[Any, ...] = (),
) -> int:
    """
    Effect: Execute a write statement and return rows affected.

    Connection opened and closed per call. Auto-commits.
    """
    conn = _open_connection(db_path)
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ============================================================================
# Row Conversion Utilities
# ============================================================================

def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Pure: Convert SQLite Row to dict."""
    return dict(row)


def rows_to_tuple(rows: List[sqlite3.Row]) -> Tuple[Dict[str, Any], ...]:
    """Pure: Convert list of SQLite Rows to tuple of dicts."""
    return tuple(row_to_dict(row) for row in rows)


# ============================================================================
# JSON Parsing Utilities
# ============================================================================

def parse_json_field(value: Optional[str]) -> Optional[Any]:
    """Pure: Safely parse JSON field from database. Returns None on failure."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def json_to_tuple(value: Optional[str]) -> Tuple[Any, ...]:
    """Pure: Parse JSON array field to tuple. Returns empty tuple on failure."""
    parsed = parse_json_field(value)
    if isinstance(parsed, list):
        return tuple(parsed)
    return ()


# ============================================================================
# Schema Introspection Utilities
# ============================================================================

def _get_table_names(conn: sqlite3.Connection) -> Tuple[str, ...]:
    """Effect: Get all table names from database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return tuple(row['name'] for row in cursor.fetchall())


def _get_table_info(conn: sqlite3.Connection, table_name: str) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get column info for a table using PRAGMA table_info."""
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    return tuple(
        {
            'cid': row['cid'],
            'name': row['name'],
            'type': row['type'],
            'notnull': bool(row['notnull']),
            'default_value': row['dflt_value'],
            'is_primary_key': bool(row['pk'])
        }
        for row in cursor.fetchall()
    )


def _get_table_sql(conn: sqlite3.Connection, table_name: str) -> Optional[str]:
    """Effect: Get CREATE TABLE SQL statement for a table."""
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    row = cursor.fetchone()
    return row['sql'] if row else None


def _parse_check_constraint(sql: str, field_name: str) -> Optional[Tuple[str, ...]]:
    """
    Pure: Parse CHECK constraint values from CREATE TABLE SQL.

    Extracts values from CHECK (field IN ('val1', 'val2', ...)) patterns.
    """
    pattern = rf"CHECK\s*\(\s*{re.escape(field_name)}\s+IN\s*\(([^)]+)\)\s*\)"
    match = re.search(pattern, sql, re.IGNORECASE)
    if not match:
        return None
    values_str = match.group(1)
    values = re.findall(r"['\"]([^'\"]+)['\"]", values_str)
    return tuple(values) if values else None


# ============================================================================
# Return Statements Fetcher
# ============================================================================

def get_return_statements(helper_name: str) -> Tuple[str, ...]:
    """
    Fetch return statements for a helper from core database,
    automatically merging with custom user-defined return statements
    when a project is initialized (cached project root available).

    Return statements are forward-thinking guidance for AI,
    providing next steps and context after a helper executes.
    Returns empty tuple on any error (graceful degradation).

    Args:
        helper_name: Name of the helper function

    Returns:
        Tuple of return statement strings (core + custom if project active)
    """
    # Fetch core return statements from aimfp_core.db
    core_stmts: Tuple[str, ...] = ()
    try:
        core_db = get_core_db_path()
        if not database_exists(core_db):
            return ()

        conn = _open_connection(core_db)
        try:
            cursor = conn.execute(
                "SELECT return_statements FROM helper_functions WHERE name = ?",
                (helper_name,)
            )
            row = cursor.fetchone()
            if row is not None:
                return_statements_json = row['return_statements']
                if return_statements_json is not None:
                    if isinstance(return_statements_json, str):
                        statements = json.loads(return_statements_json)
                    else:
                        statements = return_statements_json
                    if isinstance(statements, list):
                        core_stmts = tuple(statements)
        finally:
            conn.close()
    except Exception:
        pass

    # Merge custom return statements from user_preferences.db if project is active
    try:
        prefs_db = get_user_preferences_db_path(_cached_project_root)
        if _cached_project_root and prefs_db and os.path.exists(prefs_db):
            prefs_conn = _open_connection(prefs_db)
            try:
                cursor = prefs_conn.execute(
                    "SELECT statement FROM custom_return_statements "
                    "WHERE helper_name = ? AND active = 1 ORDER BY id",
                    (helper_name,)
                )
                custom_rows = cursor.fetchall()
                custom_stmts = tuple(row['statement'] for row in custom_rows)
                return core_stmts + custom_stmts
            finally:
                prefs_conn.close()
    except Exception:
        pass

    return core_stmts
