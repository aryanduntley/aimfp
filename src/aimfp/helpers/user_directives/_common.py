"""
AIMFP Helper Functions - User Directives Common Utilities

Shared utilities used across multiple user_directives helper files.
Extracted to avoid duplication and improve AI efficiency at scale.

This is the CATEGORY level of the DRY hierarchy:

    utils.py (global)                    <- Database-agnostic shared code
        └── user_directives/_common.py   <- THIS FILE: User directives specific
            └── user_directives/{file}.py <- Individual helpers

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Global constants defined here per AIMFP FP methodology:
- Valid directive statuses
- Valid trigger types
- Valid action types
- Valid note types for user_directives notes table
- Valid severity levels

Imported from utils.py (global):
- _open_connection: Database connection with row factory
- get_user_directives_db_path: Path resolution
"""

import sqlite3
from typing import Dict, Final, FrozenSet, Optional, Tuple

# Import global utilities (DRY - avoid duplication)
from ..utils import (  # noqa: F401 - re-exported for convenience
    _open_connection,
    _open_directives_connection,
    _get_table_sql,
    _parse_check_constraint,
    get_cached_project_root,
    get_user_directives_db_path,
)


# ============================================================================
# Global Constants - Validation Lookup Tables (FP-Compliant)
# ============================================================================
# These match CHECK constraints in user_directives.sql schema
# Use Final + frozenset for immutability and fast membership testing

# Directive statuses (user_directives table)
VALID_DIRECTIVE_STATUSES: Final[frozenset[str]] = frozenset([
    'pending_validation',
    'validated',
    'implementing',
    'implemented',
    'active',
    'paused',
    'error',
    'deprecated'
])

# Trigger types (user_directives table)
VALID_TRIGGER_TYPES: Final[frozenset[str]] = frozenset([
    'time', 'event', 'condition', 'manual'
])

# Action types (user_directives table)
VALID_ACTION_TYPES: Final[frozenset[str]] = frozenset([
    'api_call', 'script_execution', 'function_call', 'command', 'notification'
])

# Note types for notes table
VALID_NOTE_TYPES: Final[frozenset[str]] = frozenset([
    'implementation',
    'validation',
    'execution',
    'dependency',
    'error',
    'optimization',
    'user_feedback',
    'lifecycle',
    'testing',
    'general',
    # Deferred work tracking
    'deferred', 'completed', 'obsolete',
    # Deletion trail: written by delete_user_custom_entry (schema 1.4)
    'entry_deletion'
])

# Who a deletion trail credits (delete_user_custom_entry note_source).
# Stored in metadata_json: this notes table has no source column.
VALID_NOTE_SOURCES: Final[frozenset[str]] = frozenset(['ai', 'user', 'directive'])

# Severity levels (shared across databases)
VALID_SEVERITY_LEVELS: Final[frozenset[str]] = frozenset([
    'info', 'warning', 'error'
])


# ============================================================================
# Validation Utilities
# ============================================================================

def _validate_directive_status(status: str) -> bool:
    """
    Pure: Validate directive status value.

    Args:
        status: Status to validate

    Returns:
        True if valid, False otherwise
    """
    return status in VALID_DIRECTIVE_STATUSES


def _validate_note_type(note_type: str) -> bool:
    """
    Pure: Validate note type.

    Args:
        note_type: Note type to validate

    Returns:
        True if valid, False otherwise
    """
    return note_type in VALID_NOTE_TYPES


def _validate_severity(severity: str) -> bool:
    """
    Pure: Validate severity level.

    Args:
        severity: Severity value to validate

    Returns:
        True if valid, False otherwise
    """
    return severity in VALID_SEVERITY_LEVELS


# ============================================================================
# Entity Existence Check Utilities
# ============================================================================

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """
    Effect: Check if table exists in database.

    Args:
        conn: Database connection
        table: Table name

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    )
    return cursor.fetchone() is not None


def _record_exists(conn: sqlite3.Connection, table: str, record_id: int) -> bool:
    """
    Effect: Check if record with ID exists.

    Args:
        conn: Database connection
        table: Table name
        record_id: Record ID

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (record_id,))
    return cursor.fetchone() is not None


def _notes_accept_entry_deletion(conn: sqlite3.Connection) -> bool:
    """
    Effect: True if this database's notes CHECK allows 'entry_deletion'.

    False on a user_directives.db still at schema 1.3 (before migrate_databases),
    where inserting the note would violate the CHECK and abort the delete.
    """
    allowed = _parse_check_constraint(_get_table_sql(conn, 'notes') or '', 'note_type')
    return allowed is not None and 'entry_deletion' in allowed


def _cascade_edges(conn: sqlite3.Connection, parent_table: str) -> Tuple[Tuple[str, str, str], ...]:
    """
    Effect: (child_table, child_column, parent_column) for every ON DELETE CASCADE
    foreign key that points at parent_table.
    """
    tables = tuple(
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    return tuple(
        (child, fk[3], fk[4] or 'id')
        for child in tables
        for fk in conn.execute(f"PRAGMA foreign_key_list({child})").fetchall()
        if fk[2] == parent_table and str(fk[6]).upper() == 'CASCADE'
    )


def _merge_id_maps(
    maps: Tuple[Dict[str, Tuple[int, ...]], ...]
) -> Dict[str, Tuple[int, ...]]:
    """Pure: Union {table: ids} maps; ids sorted and de-duplicated per table."""
    tables = sorted({table for m in maps for table in m})
    return {
        table: tuple(sorted({i for m in maps for i in m.get(table, ())}))
        for table in tables
    }


def _collect_cascade_ids(
    conn: sqlite3.Connection,
    table: str,
    record_ids: Tuple[int, ...],
    seen: FrozenSet[Tuple[str, int]] = frozenset(),
) -> Dict[str, Tuple[int, ...]]:
    """
    Effect: Row ids that ON DELETE CASCADE will remove when record_ids are deleted
    from table, keyed by child table, followed recursively. Read-only; call it
    BEFORE the delete. The deleted rows themselves are not included.

    Lets a deletion trail name every row that disappears, so a consumer never
    needs the foreign-key graph to learn that deleting a directive also removed
    its executions, dependencies, implementations, relationships and helpers.
    """
    if not record_ids:
        return {}
    visited = seen | frozenset((table, rid) for rid in record_ids)
    placeholders = ",".join("?" * len(record_ids))
    direct = tuple(
        (child, tuple(
            row[0] for row in conn.execute(
                f"SELECT id FROM {child} WHERE {child_col} IN "
                f"(SELECT {parent_col} FROM {table} WHERE id IN ({placeholders}))",
                record_ids,
            )
            if (child, row[0]) not in visited
        ))
        for child, child_col, parent_col in _cascade_edges(conn, table)
    )
    direct_map = _merge_id_maps(tuple({child: ids} for child, ids in direct))
    nested = tuple(
        _collect_cascade_ids(
            conn, child, ids,
            visited | frozenset((child, i) for i in ids),
        )
        for child, ids in direct_map.items()
    )
    return {
        child: ids
        for child, ids in _merge_id_maps((direct_map,) + nested).items()
        if ids
    }


def _row_name(conn: sqlite3.Connection, table: str, record_id: int) -> Optional[str]:
    """Effect: The row's 'name' column value, or None if the table has no name column."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if 'name' not in columns:
        return None
    row = conn.execute(f"SELECT name FROM {table} WHERE id = ?", (record_id,)).fetchone()
    return row[0] if row else None


def _directive_exists_by_id(conn: sqlite3.Connection, directive_id: int) -> bool:
    """
    Effect: Check if directive exists by ID.

    Args:
        conn: Database connection
        directive_id: Directive ID

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(
        "SELECT id FROM user_directives WHERE id = ?",
        (directive_id,)
    )
    return cursor.fetchone() is not None


def _directive_is_approved(conn: sqlite3.Connection, directive_id: int) -> bool:
    """
    Effect: Check if directive is approved.

    Args:
        conn: Database connection
        directive_id: Directive ID

    Returns:
        True if approved=1, False otherwise (including if not found)
    """
    cursor = conn.execute(
        "SELECT approved FROM user_directives WHERE id = ?",
        (directive_id,)
    )
    row = cursor.fetchone()
    return bool(row['approved']) if row else False
