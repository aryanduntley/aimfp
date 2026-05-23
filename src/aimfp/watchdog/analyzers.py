"""
AIMFP Watchdog - File Analysis Functions

Pure functions for analyzing file changes: timestamp comparison,
function diffing, and reminder generation.
Effect functions for database queries.
"""

import re
from typing import Dict, Any, Optional, Pattern, Tuple

from ..database.connection import (
    _effect_query_one,
    _effect_query_all,
    _effect_execute,
)
from ..wrappers.file_ops import _effect_file_mtime
from .config import (
    REMINDER_TIMESTAMP_SYNCED,
    REMINDER_NEW_FILE,
    REMINDER_MISSING_FUNCTION,
    REMINDER_MISSING_DB_FUNCTION,
    REMINDER_FILE_DELETED,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)
from .reminders import create_reminder


# ============================================================================
# Pure Functions
# ============================================================================

def check_timestamp_stale(db_updated_at: Optional[str], file_mtime: float) -> bool:
    """
    Pure: Check if the DB timestamp is stale compared to file mtime.

    db_updated_at is an ISO string or None. file_mtime is a Unix timestamp.
    Returns True if DB timestamp is older than file mtime or if DB has no timestamp.
    """
    if db_updated_at is None:
        return True
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(db_updated_at.replace('Z', '+00:00'))
        db_ts = dt.timestamp()
        return db_ts < file_mtime
    except (ValueError, AttributeError):
        return True


def extract_function_names(
    file_content: str,
    pattern: Pattern[str],
) -> frozenset[str]:
    """
    Pure: Extract all function names from file content using regex pattern.

    Handles patterns with multiple capture groups (JS/TS style).
    Returns frozenset of unique function names found.
    """
    names = set()
    for match in pattern.finditer(file_content):
        groups = match.groups()
        for g in groups:
            if g is not None:
                names.add(g)
    return frozenset(names)


def find_unregistered_functions(
    file_function_names: frozenset[str],
    db_function_names: frozenset[str],
) -> frozenset[str]:
    """
    Pure: Find functions in the file that are not in the database.

    DB function names use the AIMFP ID-prefix convention (e.g., f_42_calculate_total).
    We check if any file function name appears as a suffix in any DB name.
    """
    registered = set()
    for file_name in file_function_names:
        for db_name in db_function_names:
            # DB names follow pattern: f_{id}_{name} or just the raw name
            if db_name == file_name or db_name.endswith(f"_{file_name}"):
                registered.add(file_name)
                break
    return file_function_names - registered


def find_missing_db_functions(
    file_content: str,
    db_function_names: frozenset[str],
) -> frozenset[str]:
    """
    Pure: Find DB-registered functions whose ID-prefixed names are not in the file.

    Only checks finalized functions (those with f_{id}_ prefix pattern).
    """
    missing = set()
    for db_name in db_function_names:
        if re.match(r'^f_\d+_', db_name) and db_name not in file_content:
            missing.add(db_name)
    return frozenset(missing)


def generate_file_reminders(
    relative_path: str,
    file_content: str,
    db_file_row: Optional[Dict[str, Any]],
    db_functions: Tuple[Dict[str, Any], ...],
    function_pattern: Optional[Pattern[str]],
    file_mtime: float,
) -> Tuple[Dict[str, str], ...]:
    """
    Pure: Generate all reminders for a single file change event.

    Args:
        relative_path: File path relative to source directory
        file_content: Current file content
        db_file_row: Row from files table, or None if not registered
        db_functions: Finalized functions for this file from DB
        function_pattern: Compiled regex for function detection
        file_mtime: File's OS modification timestamp

    Returns:
        Tuple of reminder dicts
    """
    reminders = []

    # Not registered in DB
    if db_file_row is None:
        reminders.append(create_reminder(
            REMINDER_NEW_FILE,
            SEVERITY_INFO,
            relative_path,
            f"New file detected but not registered in database. "
            f"Consider registering via project_file_create.",
        ))
        return tuple(reminders)

    # Timestamp check
    db_updated_at = db_file_row.get('updated_at')
    if check_timestamp_stale(db_updated_at, file_mtime):
        reminders.append(create_reminder(
            REMINDER_TIMESTAMP_SYNCED,
            SEVERITY_INFO,
            relative_path,
            f"File modified, DB updated_at was stale — automatically corrected.",
        ))

    # Function diffing (only if we have a pattern and finalized functions)
    if function_pattern is not None and db_functions:
        file_names = extract_function_names(file_content, function_pattern)
        db_names = frozenset(f.get('name', '') for f in db_functions)

        # Functions in file but not in DB
        unregistered = find_unregistered_functions(file_names, db_names)
        for name in sorted(unregistered):
            reminders.append(create_reminder(
                REMINDER_MISSING_FUNCTION,
                SEVERITY_WARNING,
                relative_path,
                f"Function '{name}' found in file but not in database. "
                f"Consider registering via project_function_create.",
            ))

        # DB functions not in file
        missing = find_missing_db_functions(file_content, db_names)
        for name in sorted(missing):
            reminders.append(create_reminder(
                REMINDER_MISSING_DB_FUNCTION,
                SEVERITY_WARNING,
                relative_path,
                f"Database function '{name}' not found in file. "
                f"May have been removed or renamed.",
            ))

    return tuple(reminders)


def generate_delete_reminders(
    relative_path: str,
    is_registered: bool,
) -> Tuple[Dict[str, str], ...]:
    """
    Pure: Generate reminders for a file deletion event.
    """
    if not is_registered:
        return ()
    return (create_reminder(
        REMINDER_FILE_DELETED,
        SEVERITY_WARNING,
        relative_path,
        f"DB-registered file no longer exists on disk. "
        f"Consider updating database records.",
    ),)


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_get_file_by_path(
    project_db_path: str,
    relative_path: str,
) -> Optional[Dict[str, Any]]:
    """Effect: Query files table for a file by its relative path."""
    return _effect_query_one(
        project_db_path,
        "SELECT * FROM files WHERE path = ? AND is_reserved = 0",
        (relative_path,),
    )


def _effect_is_file_reserved(
    project_db_path: str,
    relative_path: str,
) -> bool:
    """Effect: Check if a file path has a reserved (not yet finalized) entry."""
    row = _effect_query_one(
        project_db_path,
        "SELECT id FROM files WHERE path = ? AND is_reserved = 1",
        (relative_path,),
    )
    return row is not None


def _effect_get_finalized_functions(
    project_db_path: str,
    file_id: int,
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get all finalized functions for a file."""
    return _effect_query_all(
        project_db_path,
        "SELECT * FROM functions WHERE file_id = ? AND is_reserved = 0",
        (file_id,),
    )


def _effect_update_file_timestamp(
    project_db_path: str,
    file_id: int,
    new_timestamp: str,
) -> int:
    """Effect: Update a file's updated_at timestamp in the database."""
    return _effect_execute(
        project_db_path,
        "UPDATE files SET updated_at = ? WHERE id = ?",
        (new_timestamp, file_id),
    )


def _effect_get_all_finalized_file_paths(
    project_db_path: str,
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get all finalized (non-reserved) files with their paths."""
    return _effect_query_all(
        project_db_path,
        "SELECT id, path FROM files WHERE is_reserved = 0",
    )


def _effect_get_all_known_file_paths(
    project_db_path: str,
) -> frozenset[str]:
    """Effect: Get all file paths from DB (both reserved and finalized)."""
    rows = _effect_query_all(
        project_db_path,
        "SELECT path FROM files",
    )
    return frozenset(row.get('path', '') for row in rows if row.get('path'))


def reconcile_unregistered_files(
    source_directory: str,
    project_root: str,
    db_file_paths: frozenset[str],
    excluded_dirs: frozenset[str],
    excluded_extensions: frozenset[str],
    ignore_patterns: Tuple[str, ...] = (),
) -> Tuple[Dict[str, str], ...]:
    """
    Check source directory for files not registered in DB.

    Walks the source directory, applies exclusion rules, and returns
    reminders for files that exist on disk but aren't tracked in the database.
    Called at watchdog startup to catch files created outside AIMFP tracking.

    Args:
        source_directory: Absolute path to the source directory to walk
        project_root: Absolute path to the project root directory
            (DB paths are stored relative to project_root)
        db_file_paths: All known file paths from the database (reserved + finalized)
        excluded_dirs: Directory names to skip during walk
        excluded_extensions: File extensions to skip
        ignore_patterns: .watchdogignore glob patterns (project-relative)

    Returns:
        Tuple of reminder dicts for unregistered files
    """
    import os
    from .config import should_exclude, matches_ignore_patterns

    reminders = []
    for dirpath, dirnames, filenames in os.walk(source_directory):
        # Prune excluded directories in-place to prevent descent. Drop both
        # built-in excluded names and any dir matching a .watchdogignore pattern
        # (the latter keyed on the project-relative dir path so anchored
        # patterns like 'packages/host/extension' prune the whole subtree).
        dirnames[:] = [
            d for d in dirnames
            if d not in excluded_dirs
            and not matches_ignore_patterns(
                os.path.relpath(os.path.join(dirpath, d), project_root),
                ignore_patterns,
            )
        ]

        for filename in filenames:
            absolute_path = os.path.join(dirpath, filename)
            relative_path = os.path.relpath(absolute_path, project_root)

            if should_exclude(
                relative_path, excluded_dirs, excluded_extensions, ignore_patterns
            ):
                continue

            if relative_path not in db_file_paths:
                reminders.append(create_reminder(
                    REMINDER_NEW_FILE,
                    SEVERITY_INFO,
                    relative_path,
                    "File exists on disk but not registered in database. "
                    "Detected during startup reconciliation — file was likely "
                    "created outside AIMFP tracking. Consider registering "
                    "via reserve_file.",
                ))
    return tuple(reminders)


def reconcile_deleted_files(
    finalized_files: Tuple[Dict[str, Any], ...],
    project_root: str,
) -> Tuple[Dict[str, str], ...]:
    """
    Pure: Check all finalized file paths against disk.

    Returns deletion reminders for files registered in DB but missing from disk.
    Called at watchdog startup to catch files deleted between sessions.

    Args:
        finalized_files: Tuples of {id, path} from the files table
        project_root: Absolute path to the project root directory
            (DB paths are stored relative to project_root)

    Returns:
        Tuple of reminder dicts for missing files
    """
    import os

    reminders = []
    for file_row in finalized_files:
        relative_path = file_row.get('path', '')
        if not relative_path:
            continue
        absolute_path = os.path.join(project_root, relative_path)
        if not os.path.isfile(absolute_path):
            reminders.append(create_reminder(
                REMINDER_FILE_DELETED,
                SEVERITY_WARNING,
                relative_path,
                f"DB-registered file no longer exists on disk. "
                f"Detected during startup reconciliation — file was likely "
                f"deleted outside AIMFP tracking. Consider updating database records.",
            ))
    return tuple(reminders)
