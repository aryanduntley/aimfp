"""
AIMFP Helper Functions - Project Common Utilities

Shared utilities used across multiple project helper files.
Extracted to avoid duplication and improve AI efficiency at scale.

This is the CATEGORY level of the DRY hierarchy:

    utils.py (global)              <- Database-agnostic shared code
        └── project/_common.py     <- THIS FILE: Project-specific shared code
            └── project/{file}.py  <- Individual project helpers

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Global constants defined here per AIMFP FP methodology:
- Read-only validation lookup tables (use Final + frozenset)
- Database schema validation constraints
- Status/priority enumerations

Common utilities (project-specific):
- _check_entity_exists: Generic entity existence check
- _check_file_exists: File-specific existence check
- _check_function_exists: Function-specific existence check
- _check_type_exists: Type-specific existence check
- _check_theme_exists: Theme-specific existence check
- _check_flow_exists: Flow-specific existence check
- _create_deletion_note: Audit note for deletions

Imported from utils.py (global):
- _open_connection: Database connection with row factory
"""

import os
import sqlite3
from typing import Optional, Final

# Import global utilities (DRY - avoid duplication)
from ..utils import (  # noqa: F401 - re-exported for convenience
    _open_connection,
    _open_project_connection,
    get_cached_project_root,
)


# ============================================================================
# Filesystem Path Resolution
# ============================================================================

def _resolve_fs_path(path: str, project_root: Optional[str] = None) -> str:
    """
    Resolve a non-absolute filesystem path against the project root.

    The database stores root-relative paths. Under MCP the server runs with
    cwd == project root, so bare paths resolve correctly; an embedding host
    may run anywhere, so non-absolute paths must be anchored to the root
    explicitly before filesystem checks.

    Args:
        path: Path to resolve (absolute paths returned unchanged)
        project_root: Explicit root; falls back to the cached project root

    Returns:
        Absolute path when a root is available; the path unchanged when the
        path is absolute or no root is established (pre-existing behavior)
    """
    if os.path.isabs(path):
        return path
    root = project_root
    if root is None:
        try:
            root = get_cached_project_root()
        except RuntimeError:
            return path
    return os.path.join(root, path)


# ============================================================================
# Global Constants - Validation Lookup Tables (FP-Compliant)
# ============================================================================
# These match CHECK constraints in project.sql schema
# Use Final + frozenset for immutability and fast membership testing

# Project statuses
VALID_PROJECT_STATUSES: Final[frozenset[str]] = frozenset([
    'active', 'paused', 'completed', 'abandoned'
])

# User directives statuses (NULL handled separately — not in frozenset)
VALID_USER_DIRECTIVES_STATUSES: Final[frozenset[str]] = frozenset([
    'pending_discovery', 'pending_parse', 'in_progress', 'active', 'disabled'
])

# Function roles
VALID_FUNCTION_ROLES: Final[frozenset[str]] = frozenset([
    'factory', 'transformer', 'operator', 'pattern_matcher',
    'accessor', 'validator', 'combinator'
])

# Interaction types
VALID_INTERACTION_TYPES: Final[frozenset[str]] = frozenset([
    'call', 'chain', 'borrow', 'compose', 'pipe'
])

# Task/Milestone/Sidequest statuses (some tables include 'blocked')
VALID_TASK_STATUSES: Final[frozenset[str]] = frozenset([
    'pending', 'in_progress', 'completed', 'blocked'
])

VALID_MILESTONE_STATUSES: Final[frozenset[str]] = frozenset([
    'pending', 'in_progress', 'completed', 'blocked'
])

# Priority levels (used in tasks, subtasks, sidequests)
VALID_PRIORITY_LEVELS: Final[frozenset[str]] = frozenset([
    'low', 'medium', 'high', 'critical'
])

# Note types (must match CHECK constraint in project.sql)
VALID_NOTE_TYPES: Final[frozenset[str]] = frozenset([
    # Original types
    'clarification', 'pivot', 'research', 'entry_deletion',
    'warning', 'error', 'info', 'auto_summary',
    # Semantic types
    'decision', 'evolution', 'analysis', 'task_context',
    'external', 'summary',
    # Deferred work tracking
    'deferred', 'completed', 'obsolete'
])

# Note sources
VALID_NOTE_SOURCES: Final[frozenset[str]] = frozenset([
    'ai', 'user', 'directive'
])

# Severity levels
VALID_SEVERITY_LEVELS: Final[frozenset[str]] = frozenset([
    'info', 'warning', 'error'
])

# Branch statuses
VALID_BRANCH_STATUSES: Final[frozenset[str]] = frozenset([
    'active', 'merged', 'abandoned'
])

# Reference tables for notes (where notes can point)
VALID_REFERENCE_TABLES: Final[frozenset[str]] = frozenset([
    'tasks', 'subtasks', 'sidequests', 'files', 'functions',
    'types', 'themes', 'flows', 'milestones', 'modules'
])


# ============================================================================
# Validation Utilities
# ============================================================================

def _validate_status(status: str, valid_statuses: frozenset[str]) -> bool:
    """
    Pure: Validate status value against allowed set.

    Args:
        status: Status value to validate
        valid_statuses: Set of valid status values

    Returns:
        True if valid, False otherwise

    Example:
        _validate_status('completed', VALID_TASK_STATUSES)  # True
        _validate_status('invalid', VALID_TASK_STATUSES)    # False
    """
    return status in valid_statuses


def _validate_priority(priority: str) -> bool:
    """
    Pure: Validate priority value.

    Args:
        priority: Priority value to validate

    Returns:
        True if valid, False otherwise
    """
    return priority in VALID_PRIORITY_LEVELS


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

def _check_entity_exists(
    conn: sqlite3.Connection,
    table: str,
    entity_id: int
) -> bool:
    """
    Effect: Check if entity ID exists in table.

    Generic existence checker for any table with id column.

    Args:
        conn: Database connection
        table: Table name (e.g., 'files', 'functions', 'types')
        entity_id: Entity ID to check

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (entity_id,))
    return cursor.fetchone() is not None


# Specific wrappers for type safety and clarity
def _check_file_exists(conn: sqlite3.Connection, file_id: int) -> bool:
    """
    Effect: Check if file ID exists.

    Type-safe wrapper around _check_entity_exists.

    Args:
        conn: Database connection
        file_id: File ID to check

    Returns:
        True if exists, False otherwise
    """
    return _check_entity_exists(conn, "files", file_id)


def _check_function_exists(conn: sqlite3.Connection, function_id: int) -> bool:
    """
    Effect: Check if function ID exists.

    Type-safe wrapper around _check_entity_exists.

    Args:
        conn: Database connection
        function_id: Function ID to check

    Returns:
        True if exists, False otherwise
    """
    return _check_entity_exists(conn, "functions", function_id)


def _check_type_exists(conn: sqlite3.Connection, type_id: int) -> bool:
    """
    Effect: Check if type ID exists.

    Type-safe wrapper around _check_entity_exists.

    Args:
        conn: Database connection
        type_id: Type ID to check

    Returns:
        True if exists, False otherwise
    """
    return _check_entity_exists(conn, "types", type_id)


def _check_theme_exists(conn: sqlite3.Connection, theme_id: int) -> bool:
    """
    Effect: Check if theme ID exists.

    Type-safe wrapper around _check_entity_exists.

    Args:
        conn: Database connection
        theme_id: Theme ID to check

    Returns:
        True if exists, False otherwise
    """
    return _check_entity_exists(conn, "themes", theme_id)


def _check_flow_exists(conn: sqlite3.Connection, flow_id: int) -> bool:
    """
    Effect: Check if flow ID exists.

    Type-safe wrapper around _check_entity_exists.

    Args:
        conn: Database connection
        flow_id: Flow ID to check

    Returns:
        True if exists, False otherwise
    """
    return _check_entity_exists(conn, "flows", flow_id)


# ============================================================================
# Audit Note Utilities
# ============================================================================

def _create_deletion_note(
    conn: sqlite3.Connection,
    reference_table: str,
    reference_id: int,
    reason: str,
    severity: str,
    source: str,
    note_type: str
) -> None:
    """
    Effect: Create note entry for deletion audit trail.

    Used across multiple helpers to maintain audit trail when deleting entities.

    Args:
        conn: Database connection
        reference_table: Table name (e.g., 'files', 'functions', 'types', 'themes', 'flows')
        reference_id: Deleted record ID
        reason: Deletion reason
        severity: 'info', 'warning', 'error'
        source: 'ai' or 'user'
        note_type: Note type (e.g., 'entry_deletion')
    """
    conn.execute(
        """
        INSERT INTO notes (
            content,
            source,
            severity,
            note_type,
            reference_table,
            reference_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (reason, source, severity, note_type, reference_table, reference_id)
    )
    conn.commit()
