"""
AIMFP Helper Functions - Global Database Info

Cross-database helper that reports on all available AIMFP databases.
Returns existence, path, and basic metadata for each database.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- get_databases: Get list of all available databases with metadata
"""

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple

# Import global utilities
from ..utils import (
    get_return_statements,
    get_core_db_path,
    get_project_db_path,
    get_user_preferences_db_path,
    get_user_directives_db_path,
    database_exists,
    resolve_project_root,
    _open_connection,
    _close_connection,
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class DatabaseInfo:
    """Immutable database metadata."""
    name: str
    db_file: str
    path: str
    exists: bool
    table_count: int = 0
    description: str = ""


@dataclass(frozen=True)
class DatabasesResult:
    """Result of get_databases."""
    success: bool
    databases: Tuple[DatabaseInfo, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Effect Functions - Database Inspection
# ============================================================================

def _count_tables(db_path: str) -> int:
    """
    Effect: Count tables in a database.

    Args:
        db_path: Path to database file

    Returns:
        Number of tables, or 0 if database cannot be read
    """
    try:
        # readonly: counting tables must not reconfigure the database (a
        # read-write open rewrites journal_mode into the header, and this runs
        # against aimfp_core.db, which ships read-only) and must not CREATE one
        # — sqlite3.connect on a missing path would otherwise leave an empty
        # database behind as the side effect of an inspection.
        conn = _open_connection(db_path, readonly=True)
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        row = cursor.fetchone()
        count = row['cnt'] if row else 0
        _close_connection(conn)
        return count
    except Exception:
        return 0


# ============================================================================
# Public Helper Functions
# ============================================================================

def get_databases() -> DatabasesResult:
    """
    Get list of all available databases with metadata.
    Reports on all 4 AIMFP databases: core, project, user_preferences, user_directives.

    Returns:
        DatabasesResult with database info for all 4 databases

    Example:
        >>> result = get_databases()
        >>> for db in result.databases:
        ...     print(f"{db.name}: {'exists' if db.exists else 'not found'} ({db.table_count} tables)")
        aimfp_core: exists (8 tables)
        project: exists (20 tables)
        user_preferences: exists (7 tables)
        user_directives: not found (0 tables)
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return DatabasesResult(
            success=False,
            error=f"Cannot resolve project root: {str(e)}"
        )

    try:
        # Resolve all database paths
        core_path = get_core_db_path()
        project_path = get_project_db_path(project_root)
        prefs_path = get_user_preferences_db_path(project_root)
        directives_path = get_user_directives_db_path(project_root)

        # Build database info for each
        databases = []

        # Core database (read-only, MCP server install)
        core_exists = database_exists(core_path)
        databases.append(DatabaseInfo(
            name="aimfp_core",
            db_file="aimfp_core.db",
            path=core_path,
            exists=core_exists,
            table_count=_count_tables(core_path) if core_exists else 0,
            description="Read-only reference database with directives, helpers, flows. Located in MCP server installation."
        ))

        # Project database (per-project state)
        project_exists = database_exists(project_path)
        databases.append(DatabaseInfo(
            name="project",
            db_file="project.db",
            path=project_path,
            exists=project_exists,
            table_count=_count_tables(project_path) if project_exists else 0,
            description="Per-project state tracking: files, functions, types, tasks, flows, themes."
        ))

        # User preferences database
        prefs_exists = database_exists(prefs_path)
        databases.append(DatabaseInfo(
            name="user_preferences",
            db_file="user_preferences.db",
            path=prefs_path,
            exists=prefs_exists,
            table_count=_count_tables(prefs_path) if prefs_exists else 0,
            description="User settings, directive preferences, opt-in tracking features."
        ))

        # User directives database (Use Case 2 only)
        directives_exists = database_exists(directives_path)
        databases.append(DatabaseInfo(
            name="user_directives",
            db_file="user_directives.db",
            path=directives_path,
            exists=directives_exists,
            table_count=_count_tables(directives_path) if directives_exists else 0,
            description="Use Case 2 only: user-defined automation directives, executions, dependencies."
        ))

        return_stmts = get_return_statements("get_databases")

        return DatabasesResult(
            success=True,
            databases=tuple(databases),
            return_statements=return_stmts
        )

    except Exception as e:
        return DatabasesResult(
            success=False,
            error=f"Error checking databases: {str(e)}"
        )
