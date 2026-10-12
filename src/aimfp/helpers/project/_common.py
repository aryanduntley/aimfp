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
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, Iterable, Optional, Final, Tuple

if TYPE_CHECKING:  # annotation only: catalog imports project, so no runtime import here
    from ..catalog.extract import ExtractedEntities

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


def strip_dot_slash(path: str) -> str:
    """Pure: A project-relative path without surrounding space or a leading './'."""
    cleaned = path.strip()
    return cleaned[2:] if cleaned.startswith('./') else cleaned


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


def _effect_full_entities(
    file_path: str,
    project_root: Optional[str],
) -> Optional["ExtractedEntities"]:
    """
    Effect: A source file's extracted entities when full-fidelity extraction
    applies (Python, via ast), else None (unreadable, unparseable, or a
    language AIMFP only pattern-matches).

    Only full fidelity is trusted for checks against the code: name-only regex
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
    return entities if entities.fidelity == 'full' else None


def _effect_defined_function_names(
    file_path: str,
    project_root: Optional[str],
) -> Optional[FrozenSet[str]]:
    """
    Effect: Names of every function defined in a source file, or None when the
    file cannot be checked (see _effect_full_entities).
    """
    entities = _effect_full_entities(file_path, project_root)
    if entities is None:
        return None
    return frozenset(fn.name for fn in entities.functions)


def _effect_file_paths(conn: sqlite3.Connection, file_ids: Iterable[int]) -> Dict[int, str]:
    """Effect: file id -> recorded path, for the ids that exist and have a path."""
    return {
        file_id: row[0]
        for file_id in dict.fromkeys(file_ids)
        for row in (conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone(),)
        if row is not None and row[0]
    }


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
    paths = _effect_file_paths(conn, (file_id for file_id, _ in pairs))
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


def _effect_top_level_functions(
    conn: sqlite3.Connection,
    file_ids: Iterable[int],
    project_root: Optional[str],
) -> Dict[int, Dict[str, object]]:
    """
    Effect: file id -> {function name -> ExtractedFunction} for top-level
    functions of every checkable file (each file read once).
    """
    paths = _effect_file_paths(conn, file_ids)
    found = {file_id: _effect_full_entities(path, project_root) for file_id, path in paths.items()}
    return {
        file_id: {fn.name: fn for fn in entities.functions if not fn.is_nested}
        for file_id, entities in found.items()
        if entities is not None
    }


def function_signature_warnings(
    conn: sqlite3.Connection,
    entries: Iterable[Tuple[int, str, Optional[str]]],
    project_root: Optional[str],
) -> Tuple[str, ...]:
    """
    Effect: PARAM CHECK warnings for recorded parameters that disagree with the
    function's signature in its file (names, order, required vs default).

    Warns rather than refuses, like the NAME CHECK; functions or files that
    cannot be checked are skipped.

    Args:
        conn: Open project.db connection (reads files.path)
        entries: (file_id, function name, recorded parameters JSON) triples
        project_root: Root for resolving file paths

    Returns:
        One warning per disagreeing function, in input order
    """
    from ..catalog.extract import parameter_mismatches
    from ..utils import decode_json_layers

    triples = tuple(entries)
    defined = _effect_top_level_functions(conn, (file_id for file_id, _, _ in triples), project_root)
    return tuple(
        f"PARAM CHECK '{name}': {'; '.join(problems)}. Make the recorded parameters match "
        "the code (update_function), or fix the signature."
        for file_id, name, params_json in triples
        if params_json is not None and name in defined.get(file_id, {})
        for problems in (parameter_mismatches(decode_json_layers(params_json), defined[file_id][name].parameters),)
        if problems
    )


def store_source_hash(conn: sqlite3.Connection, function_id: int, digest: Optional[str]) -> bool:
    """
    Effect: Record a function's code hash (no commit). A no-op on a database not
    yet migrated to schema 1.15. Returns whether a hash was written.
    """
    if not digest:
        return False
    columns = {row[1] for row in conn.execute("PRAGMA table_info(functions)").fetchall()}
    if 'source_hash' not in columns:
        return False
    conn.execute("UPDATE functions SET source_hash = ? WHERE id = ?", (digest, function_id))
    return True


def record_source_hashes(
    conn: sqlite3.Connection,
    entries: Iterable[Tuple[int, int, str]],
    project_root: Optional[str],
) -> int:
    """
    Effect: Record the current code hash of each function whose description was
    just written, so audit_descriptions can tell when the code moves on without
    it (no commit). Functions that cannot be found in their file at full
    fidelity are left unhashed.

    Args:
        conn: Open project.db connection
        entries: (function_id, file_id, function name) triples
        project_root: Root for resolving file paths

    Returns:
        Number of hashes written
    """
    triples = tuple(entries)
    defined = _effect_top_level_functions(conn, (file_id for _, file_id, _ in triples), project_root)
    return sum(
        1 for function_id, file_id, name in triples
        if name in defined.get(file_id, {})
        and store_source_hash(conn, function_id, defined[file_id][name].body_hash)
    )


def audit_completion_gate(project_root: str, table: str, item_id: int, status: Optional[str],
                conn: sqlite3.Connection, override_reason: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    Effect: The tracking-audit completion gate for a status change (helpers/audit/gate.py).

    Runs only on a transition to 'completed'. Returns (refusal error or None,
    audit summary for the result or None).
    """
    if status != 'completed':
        return (None, None)
    row = conn.execute(f"SELECT status FROM {table} WHERE id = ?", (item_id,)).fetchone()
    if row is not None and row[0] == 'completed':
        return (None, None)
    from ..audit.gate import check_completion, gate_summary
    error, verdict = check_completion(project_root, table, item_id, override_reason, conn)
    return (error, gate_summary(verdict))


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
