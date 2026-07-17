"""
AIMFP Helper Functions - Project Files (Part 2)

File update, change detection, and deletion operations for project database.
Implements file metadata management and comprehensive deletion validation.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- update_file: Update file metadata (name, path, language)
- file_has_changed: Check if file changed using Git or filesystem timestamp
- update_file_timestamp: Sub-helper for timestamp updates (called by function updates)
- delete_file: Delete file with comprehensive cross-reference validation
"""

import os
import sqlite3
import subprocess
from dataclasses import dataclass
from typing import Optional, List, Tuple

from ..utils import get_return_statements

# Import common project utilities (DRY principle)
from ._common import (
    _open_connection,
    _check_file_exists,
    _create_deletion_note,
    get_cached_project_root,
    _open_project_connection,
    _resolve_fs_path,
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class UpdateResult:
    """Result of file update operation."""
    success: bool
    file_id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class ChangeDetectionResult:
    """Result of file change detection."""
    success: bool
    changed: Optional[bool] = None
    method: Optional[str] = None  # 'git' or 'filesystem'
    error: Optional[str] = None


@dataclass(frozen=True)
class TimestampUpdateResult:
    """Result of timestamp update operation."""
    success: bool
    error: Optional[str] = None


@dataclass(frozen=True)
class DeletionDependencies:
    """Dependencies blocking file deletion."""
    functions: Tuple[str, ...] = ()  # Function names
    types: Tuple[str, ...] = ()  # Type names
    file_flows: Tuple[str, ...] = ()  # Flow names
    module_files: Tuple[str, ...] = ()  # Module names (auto-cleared, not blocking)


@dataclass(frozen=True)
class DeleteResult:
    """Result of file deletion operation."""
    success: bool
    deleted_file_id: Optional[int] = None
    error: Optional[str] = None
    dependencies: Optional[DeletionDependencies] = None
    cleared_file_flows: Tuple[str, ...] = ()  # Flow names auto-cleared during deletion
    cleared_module_files: Tuple[str, ...] = ()  # Module names auto-cleared during deletion
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


# ============================================================================
# Pure Helper Functions
# ============================================================================

def validate_file_id_pattern(name: str, file_id: int) -> bool:
    """
    Validate that file name contains _id_{file_id} pattern.

    Pure function - no side effects, deterministic.

    Args:
        name: File name to validate
        file_id: Expected file ID

    Returns:
        True if pattern found, False otherwise
    """
    expected_pattern = f"_id_{file_id}"
    return expected_pattern in name


def build_update_query(
    file_id: int,
    name: Optional[str],
    path: Optional[str],
    language: Optional[str]
) -> Tuple[str, Tuple]:
    """
    Build SQL UPDATE query with only non-NULL fields.

    Pure function - deterministic query building.

    Args:
        file_id: File ID to update
        name: New name (None = don't update)
        path: New path (None = don't update)
        language: New language (None = don't update)

    Returns:
        Tuple of (sql_query, parameters)
    """
    updates = []
    params = []

    if name is not None:
        updates.append("name = ?")
        params.append(name)

    if path is not None:
        updates.append("path = ?")
        params.append(path)

    if language is not None:
        updates.append("language = ?")
        params.append(language)

    # Always update timestamp
    updates.append("updated_at = CURRENT_TIMESTAMP")

    # Build query
    sql = f"UPDATE files SET {', '.join(updates)} WHERE id = ?"
    params.append(file_id)

    return (sql, tuple(params))


# ============================================================================
# Database Effect Functions
# ============================================================================

def _check_is_reserved(conn: sqlite3.Connection, file_id: int) -> bool:
    """
    Effect: Check if file is reserved.

    Args:
        conn: Database connection
        file_id: File ID to check

    Returns:
        True if reserved, False otherwise
    """
    cursor = conn.execute(
        "SELECT is_reserved FROM files WHERE id = ?",
        (file_id,)
    )
    row = cursor.fetchone()
    return bool(row["is_reserved"]) if row else False


def _get_file_path(conn: sqlite3.Connection, file_id: int) -> Optional[str]:
    """
    Effect: Get file path from database.

    Args:
        conn: Database connection
        file_id: File ID

    Returns:
        File path or None if not found
    """
    cursor = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,))
    row = cursor.fetchone()
    return row["path"] if row else None


def _get_file_updated_at(conn: sqlite3.Connection, file_id: int) -> Optional[str]:
    """
    Effect: Get file's updated_at timestamp.

    Args:
        conn: Database connection
        file_id: File ID

    Returns:
        Timestamp string or None if not found
    """
    cursor = conn.execute(
        "SELECT updated_at FROM files WHERE id = ?",
        (file_id,)
    )
    row = cursor.fetchone()
    return row["updated_at"] if row else None


def _update_file_effect(conn: sqlite3.Connection, sql: str, params: Tuple) -> None:
    """
    Effect: Execute file update query.

    Args:
        conn: Database connection
        sql: UPDATE query
        params: Query parameters
    """
    conn.execute(sql, params)
    conn.commit()


def _update_timestamp_effect(conn: sqlite3.Connection, file_id: int) -> None:
    """
    Effect: Update file timestamp.

    Args:
        conn: Database connection
        file_id: File ID to update
    """
    conn.execute(
        "UPDATE files SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (file_id,)
    )
    conn.commit()


def _check_git_available() -> bool:
    """
    Effect: Check if git is available in environment.

    Returns:
        True if git command exists, False otherwise
    """
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            check=True,
            timeout=2
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _git_file_changed(file_path: str, cwd: Optional[str] = None) -> bool:
    """
    Effect: Check if file has uncommitted changes in Git.

    Args:
        file_path: Path to file
        cwd: Directory to run git in (defaults to process cwd — the repo root
             under MCP; embedding hosts pass the project root explicitly)

    Returns:
        True if file has changes, False if clean
    """
    try:
        # git diff --quiet returns 0 if no changes, 1 if changes exist
        result = subprocess.run(
            ["git", "diff", "--quiet", file_path],
            capture_output=True,
            timeout=5,
            cwd=cwd
        )
        return result.returncode != 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _get_filesystem_mtime(file_path: str) -> Optional[float]:
    """
    Effect: Get file's last modified time from filesystem.

    Args:
        file_path: Path to file

    Returns:
        Modification timestamp or None if file doesn't exist
    """
    try:
        return os.path.getmtime(file_path)
    except (OSError, FileNotFoundError):
        return None


def _parse_sqlite_timestamp(timestamp_str: str) -> float:
    """
    Pure: Parse SQLite CURRENT_TIMESTAMP format to Unix timestamp.

    SQLite CURRENT_TIMESTAMP format: 'YYYY-MM-DD HH:MM:SS'

    Args:
        timestamp_str: SQLite timestamp string

    Returns:
        Unix timestamp (seconds since epoch)
    """
    from datetime import datetime
    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    return dt.timestamp()


def _get_deletion_dependencies(
    conn: sqlite3.Connection,
    file_id: int
) -> DeletionDependencies:
    """
    Effect: Query dependencies blocking file deletion.

    Args:
        conn: Database connection
        file_id: File ID to check

    Returns:
        DeletionDependencies with lists of blocking entities
    """
    # Check functions
    cursor = conn.execute(
        "SELECT name FROM functions WHERE file_id = ?",
        (file_id,)
    )
    functions = tuple(row["name"] for row in cursor.fetchall())

    # Check types
    cursor = conn.execute(
        "SELECT name FROM types WHERE file_id = ?",
        (file_id,)
    )
    types = tuple(row["name"] for row in cursor.fetchall())

    # Check file_flows
    cursor = conn.execute(
        """
        SELECT f.name
        FROM file_flows ff
        JOIN flows f ON ff.flow_id = f.id
        WHERE ff.file_id = ?
        """,
        (file_id,)
    )
    file_flows = tuple(row["name"] for row in cursor.fetchall())

    # Check module_files
    cursor = conn.execute(
        """
        SELECT m.name
        FROM module_files mf
        JOIN modules m ON mf.module_id = m.id
        WHERE mf.file_id = ?
        """,
        (file_id,)
    )
    module_files = tuple(row["name"] for row in cursor.fetchall())

    return DeletionDependencies(
        functions=functions,
        types=types,
        file_flows=file_flows,
        module_files=module_files,
    )


def _clear_file_flows_effect(conn: sqlite3.Connection, file_id: int) -> None:
    """
    Effect: Remove all file_flows entries for a file.

    Args:
        conn: Database connection
        file_id: File ID to clear flows for
    """
    conn.execute("DELETE FROM file_flows WHERE file_id = ?", (file_id,))
    conn.commit()


def _clear_module_files_effect(conn: sqlite3.Connection, file_id: int) -> None:
    """
    Effect: Remove all module_files entries for a file.

    Args:
        conn: Database connection
        file_id: File ID to clear module assignments for
    """
    conn.execute("DELETE FROM module_files WHERE file_id = ?", (file_id,))
    conn.commit()


def _delete_file_effect(conn: sqlite3.Connection, file_id: int) -> None:
    """
    Effect: Delete file from database.

    Args:
        conn: Database connection
        file_id: File ID to delete
    """
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    conn.commit()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def update_file(
    file_id: int,
    name: Optional[str] = None,
    path: Optional[str] = None,
    language: Optional[str] = None,
    project_root: Optional[str] = None
) -> UpdateResult:
    """
    Update file metadata.

    Only updates non-NULL parameters. Name changes must preserve _id_xxx suffix.
    Automatically updates timestamp.

    Args:
        file_id: File ID to update
        name: New file name (None = don't update)
        path: New file path (None = don't update)
        language: New language (None = don't update)

    Returns:
        UpdateResult with success status and file_id

    Example:
        >>> # Update only the language
        >>> result = update_file(42, language="typescript")
        >>> result.success
        True

        >>> # Update name and path
        >>> result = update_file(
        ...     42,
        ...     name="calculator_id_42.ts",
        ...     path="src/calculator_id_42.ts"
        ... )
        >>> result.success
        True
    """
    # Validate at least one parameter is provided
    if name is None and path is None and language is None:
        return UpdateResult(
            success=False,
            error="At least one parameter (name, path, language) must be provided"
        )

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if file exists
        if not _check_file_exists(conn, file_id):
            return UpdateResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        # Validate name pattern if name is being updated and entity uses id_in_name
        if name is not None:
            cursor = conn.execute("SELECT id_in_name FROM files WHERE id = ?", (file_id,))
            row = cursor.fetchone()
            if row and bool(row["id_in_name"]) and not validate_file_id_pattern(name, file_id):
                return UpdateResult(
                    success=False,
                    error=f"File name must contain '_id_{file_id}' pattern"
                )

        # Pure: build update query
        sql, params = build_update_query(file_id, name, path, language)

        # Effect: execute update
        _update_file_effect(conn, sql, params)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("update_file")

        return UpdateResult(
            success=True,
            file_id=file_id,
            return_statements=return_statements
        )

    except Exception as e:
        return UpdateResult(
            success=False,
            error=f"Database update failed: {str(e)}"
        )

    finally:
        conn.close()


def file_has_changed(
    file_id: int,
    project_root: Optional[str] = None
) -> ChangeDetectionResult:
    """
    Check if file changed using Git (if available) or filesystem timestamp.

    Prefers Git method for accuracy. Falls back to filesystem timestamp comparison.

    Args:
        file_id: File ID to check

    Returns:
        ChangeDetectionResult with changed flag and detection method

    Example:
        >>> result = file_has_changed(42)
        >>> result.success
        True
        >>> result.changed
        True
        >>> result.method
        'git'
    """
    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if file exists
        if not _check_file_exists(conn, file_id):
            return ChangeDetectionResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        # Get file path from database
        file_path = _get_file_path(conn, file_id)
        if file_path is None:
            return ChangeDetectionResult(
                success=False,
                error=f"File path not found for ID {file_id}"
            )

        # Check if file exists on filesystem (root-relative DB paths resolve
        # against the project root — cwd is not guaranteed to be the root)
        fs_path = _resolve_fs_path(file_path, project_root)
        if not os.path.exists(fs_path):
            return ChangeDetectionResult(
                success=False,
                error=f"File does not exist at path: {file_path}"
            )

        # Try Git method first
        if _check_git_available():
            changed = _git_file_changed(fs_path, cwd=project_root)
            return ChangeDetectionResult(
                success=True,
                changed=changed,
                method="git"
            )

        # Fall back to filesystem timestamp comparison
        db_timestamp_str = _get_file_updated_at(conn, file_id)
        if db_timestamp_str is None:
            return ChangeDetectionResult(
                success=False,
                error="Database timestamp not found"
            )

        db_timestamp = _parse_sqlite_timestamp(db_timestamp_str)
        fs_mtime = _get_filesystem_mtime(fs_path)

        if fs_mtime is None:
            return ChangeDetectionResult(
                success=False,
                error=f"Could not read file modification time: {file_path}"
            )

        # File changed if filesystem mtime is newer than database timestamp
        changed = fs_mtime > db_timestamp

        return ChangeDetectionResult(
            success=True,
            changed=changed,
            method="filesystem"
        )

    except Exception as e:
        return ChangeDetectionResult(
            success=False,
            error=f"Change detection failed: {str(e)}"
        )

    finally:
        conn.close()


def update_file_timestamp(
    file_id: int,
    project_root: Optional[str] = None
) -> TimestampUpdateResult:
    """
    Update file timestamp (sub-helper called automatically after function updates).

    This is a sub-helper not exposed as MCP tool. Used internally by function
    update operations to mark files as recently modified.

    Args:
        file_id: File ID to update timestamp for

    Returns:
        TimestampUpdateResult with success status

    Example:
        >>> # Internal use only - called by update_function helper
        >>> result = update_file_timestamp(42)
        >>> result.success
        True
    """
    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if file exists
        if not _check_file_exists(conn, file_id):
            return TimestampUpdateResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        # Effect: update timestamp
        _update_timestamp_effect(conn, file_id)

        return TimestampUpdateResult(success=True)

    except Exception as e:
        return TimestampUpdateResult(
            success=False,
            error=f"Timestamp update failed: {str(e)}"
        )

    finally:
        conn.close()


def delete_file(
    file_id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion",
    project_root: Optional[str] = None
) -> DeleteResult:
    """
    Delete file with comprehensive cross-reference validation.

    Functions and types must be removed manually before deletion.
    File_flows entries are auto-cleared when no other dependencies remain.

    Args:
        file_id: File ID to delete
        note_reason: Deletion reason
        note_severity: 'info', 'warning', 'error'
        note_source: 'ai' or 'user'
        note_type: Note type (default: 'entry_deletion')

    Returns:
        DeleteResult with success status or dependency list

    Example:
        >>> result = delete_file(
        ...     42,
        ...     note_reason="File no longer needed after refactor",
        ...     note_severity="info",
        ...     note_source="ai"
        ... )
        >>> result.success
        True
        >>> result.deleted_file_id
        42
    """
    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if file exists
        if not _check_file_exists(conn, file_id):
            return DeleteResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        # Check if file is reserved
        if _check_is_reserved(conn, file_id):
            return DeleteResult(
                success=False,
                error="file_reserved",
                dependencies=None
            )

        # Check for dependencies
        deps = _get_deletion_dependencies(conn, file_id)

        # Functions and types are hard blockers — must be removed manually
        if deps.functions or deps.types:
            return DeleteResult(
                success=False,
                error="dependencies_exist",
                dependencies=deps
            )

        # Auto-clear file_flows when functions/types are clean
        cleared_flows: Tuple[str, ...] = ()
        if deps.file_flows:
            cleared_flows = deps.file_flows
            _clear_file_flows_effect(conn, file_id)

        # Auto-clear module_files when functions/types are clean
        cleared_modules: Tuple[str, ...] = ()
        if deps.module_files:
            cleared_modules = deps.module_files
            _clear_module_files_effect(conn, file_id)

        # Proceed with deletion
        _create_deletion_note(
            conn,
            "files",
            file_id,
            note_reason,
            note_severity,
            note_source,
            note_type
        )

        _delete_file_effect(conn, file_id)

        # Build return statements
        return_statements = get_return_statements("delete_file")
        if cleared_flows:
            flow_list = ", ".join(cleared_flows)
            return_statements = return_statements + (
                f"Auto-cleared file_flows entries: [{flow_list}]. Verify these flows still have adequate file coverage.",
            )
        if cleared_modules:
            module_list = ", ".join(cleared_modules)
            return_statements = return_statements + (
                f"Auto-cleared module_files entries: [{module_list}]. Verify these modules still have adequate file coverage via get_module_files.",
            )

        return DeleteResult(
            success=True,
            deleted_file_id=file_id,
            cleared_file_flows=cleared_flows,
            cleared_module_files=cleared_modules,
            return_statements=return_statements
        )

    except Exception as e:
        return DeleteResult(
            success=False,
            error=f"Database deletion failed: {str(e)}"
        )

    finally:
        conn.close()
