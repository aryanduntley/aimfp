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

import difflib
import os
import sqlite3
from typing import FrozenSet, Iterable, Optional, Final, Tuple

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


def check_trackable_file_path(path: str, project_root: Optional[str] = None) -> Optional[str]:
    """
    Effect: Why ``path`` cannot be tracked as a file, or None when it can.

    finalize_file used to check only os.path.exists, so '.' (the project root)
    or any directory passed. A tracked file must be a regular file, and a
    relative path must stay inside the project. Each message names the fix.

    Args:
        path: Project-relative (or absolute) path as given by the caller
        project_root: Explicit root; falls back to the cached project root

    Returns:
        Error message, or None when the path is a regular file
    """
    stripped = (path or '').strip()
    if stripped in ('', '.', './', '..') or stripped.endswith('/'):
        return (
            f"Path {path!r} does not name a file. Pass the file's path relative "
            "to the project root, e.g. 'src/calc.py'."
        )
    if not os.path.isabs(stripped) and '..' in stripped.replace('\\', '/').split('/'):
        return (
            f"Path {path!r} leaves the project. Pass a path relative to the "
            "project root without '..'."
        )
    resolved = _resolve_fs_path(stripped, project_root)
    if os.path.isdir(resolved):
        return (
            f"Path {path!r} is a directory, not a file. Pass the file's path "
            "relative to the project root, e.g. 'src/calc.py'."
        )
    if not os.path.isfile(resolved):
        return f"File does not exist at path: {path}"
    return None


def missing_function_warning(
    name: str,
    file_path: str,
    defined_names: FrozenSet[str],
) -> Optional[str]:
    """
    Pure: A warning when ``name`` is not defined in the file, with a did-you-mean.

    Returns None when the name is present.
    """
    if name in defined_names:
        return None
    close = difflib.get_close_matches(name, sorted(defined_names), n=3, cutoff=0.6)
    hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
    return (
        f"Function '{name}' is not defined in {file_path}.{hint} Make the tracked "
        "name match the code: rename it with update_function, or rename the "
        "function in the source."
    )


def _effect_defined_function_names(
    file_path: str,
    project_root: Optional[str],
) -> Optional[FrozenSet[str]]:
    """
    Effect: Names of every function defined in a source file, or None when the
    file cannot be checked (unreadable, or a language AIMFP only pattern-matches).

    Only full-fidelity extraction (Python, via ast) is trusted: name-only regex
    extraction misses forms like arrow functions and would warn falsely.
    """
    from ...watchdog.config import detect_language
    from ..catalog.extract import extract_entities

    language = detect_language(file_path)
    if language is None:
        return None
    try:
        with open(_resolve_fs_path(file_path, project_root), encoding='utf-8') as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    entities = extract_entities(source, language)
    if entities.fidelity != 'full':
        return None
    return frozenset(fn.name for fn in entities.functions)


def function_name_warnings(
    conn: sqlite3.Connection,
    named_functions: Iterable[Tuple[int, str]],
    project_root: Optional[str],
) -> Tuple[str, ...]:
    """
    Effect: Warnings for tracked function names absent from their file's source.

    Finalize never opened the source, so a model could finalize
    'print_hi_id_1' while the code says 'print_hi'. Warns rather than refuses;
    files that cannot be checked are skipped. Each file is parsed once.

    Args:
        conn: Open project.db connection (reads files.path)
        named_functions: (file_id, function name) pairs
        project_root: Root for resolving file paths

    Returns:
        One warning per name not found, in input order
    """
    pairs = tuple(named_functions)
    file_ids = tuple(dict.fromkeys(file_id for file_id, _ in pairs))
    paths = {
        file_id: row[0]
        for file_id in file_ids
        for row in (conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone(),)
        if row is not None and row[0]
    }
    defined = {
        file_id: _effect_defined_function_names(path, project_root)
        for file_id, path in paths.items()
    }
    return tuple(
        warning
        for file_id, name in pairs
        if defined.get(file_id) is not None
        for warning in (missing_function_warning(name, paths[file_id], defined[file_id]),)
        if warning
    )


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
