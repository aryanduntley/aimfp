"""
AIMFP Helper Functions - Project Metadata

Project metadata management, blueprint tracking, infrastructure, and state database initialization.
Handles project lifecycle, source directory management, and state DB setup.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- create_project: Initialize project entry
- get_project: Get project metadata
- update_project: Update project metadata
- blueprint_has_changed: Check if ProjectBlueprint.md has changed
- get_infrastructure_by_type: Get infrastructure entries by type
- get_all_infrastructure: Get all infrastructure entries (including empty values)
- get_source_directory: Get project source directory
- update_source_directory: Update source directory (with failsafe insert if not exists)
- get_project_root: Get project root directory
- update_project_root: Update project root directory (with failsafe insert if not exists)
"""

import sqlite3
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any
from pathlib import Path

from ..utils import get_return_statements

# Import common project utilities (DRY principle)
from ._common import get_cached_project_root, _open_project_connection


# ============================================================================
# Global Constants
# ============================================================================

from typing import Final

# Infrastructure types
INFRASTRUCTURE_TYPE_PROJECT_ROOT: Final[str] = 'project_root'
INFRASTRUCTURE_TYPE_SOURCE_DIR: Final[str] = 'source_directory'

# Project statuses
VALID_PROJECT_STATUSES: Final[frozenset[str]] = frozenset([
    'active', 'paused', 'completed', 'abandoned'
])

# User directives statuses
VALID_USER_DIRECTIVES_STATUSES: Final[frozenset[str]] = frozenset([
    'pending_discovery', 'pending_parse', 'in_progress', 'active', 'disabled'
])


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class ProjectRecord:
    """Immutable project record."""
    id: int
    name: str
    purpose: str
    goals: Tuple[str, ...]  # Parsed from JSON
    status: str
    version: int
    user_directives_status: Optional[str]
    last_known_git_hash: Optional[str]
    last_git_sync: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class InfrastructureRecord:
    """Immutable infrastructure record."""
    id: int
    type: str
    value: Optional[str]
    description: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AddResult:
    """Result of add operation."""
    success: bool
    project_id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectResult:
    """Result of get_project operation."""
    success: bool
    project: Optional[ProjectRecord] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class UpdateResult:
    """Result of update operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BlueprintChangeResult:
    """Result of blueprint change check."""
    success: bool
    changed: bool = False
    method: Optional[str] = None  # 'git' or 'filesystem'
    error: Optional[str] = None


@dataclass(frozen=True)
class InfrastructureResult:
    """Result of infrastructure query."""
    success: bool
    infrastructure: Tuple[InfrastructureRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceDirResult:
    """Result of source directory operations."""
    success: bool
    data: Optional[str] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()



# ============================================================================
# Validation Functions
# ============================================================================

def _validate_project_status(status: str) -> bool:
    """Pure: Validate project status."""
    return status in VALID_PROJECT_STATUSES


def _validate_user_directives_status(status: Optional[str]) -> bool:
    """Pure: Validate user directives status."""
    return status is None or status in VALID_USER_DIRECTIVES_STATUSES


def _make_relative_source_dir(source_dir: str, project_root: str) -> str:
    """
    Pure: Convert absolute source directory to relative if it's under project_root.

    Args:
        source_dir: Source directory path (absolute or relative)
        project_root: Project root directory (absolute)

    Returns:
        Relative path if convertible, original path otherwise
    """
    if source_dir.startswith('/'):
        # Normalize both paths (remove trailing slashes)
        normalized_root = project_root.rstrip('/')
        normalized_dir = source_dir.rstrip('/')
        if normalized_dir.startswith(normalized_root + '/'):
            return normalized_dir[len(normalized_root) + 1:]
    return source_dir


def _validate_source_dir(source_dir: str) -> Optional[str]:
    """
    Pure: Validate source directory path.

    Args:
        source_dir: Source directory path

    Returns:
        Error message if invalid, None if valid
    """
    # Check for absolute paths (after relative conversion attempt)
    if source_dir.startswith('/'):
        return f"Invalid source directory: {source_dir}. Must be relative path (or absolute path under project root)."

    # Check for parent references
    if '..' in source_dir:
        return f"Invalid source directory: {source_dir}. Must not contain parent references."

    return None


# ============================================================================
# Effect Functions - Git Operations
# ============================================================================

def _get_git_hash() -> Optional[str]:
    """
    Effect: Get current Git commit hash.

    Returns:
        Git hash or None if not in git repo
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _check_git_diff(file_path: str) -> bool:
    """
    Effect: Check if file has uncommitted changes in Git.

    Args:
        file_path: Path to file

    Returns:
        True if file has changes, False otherwise
    """
    try:
        result = subprocess.run(
            ['git', 'diff', '--quiet', file_path],
            timeout=5
        )
        # Return code 0 means no changes, 1 means changes
        return result.returncode != 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ============================================================================
# Effect Functions - Database Operations
# ============================================================================

def _insert_project(
    conn: sqlite3.Connection,
    name: str,
    purpose: str,
    goals: List[str],
    status: str,
    version: int,
    user_directives_status: Optional[str]
) -> int:
    """Effect: Insert project record."""
    goals_json = json.dumps(goals)
    git_hash = _get_git_hash()

    cursor = conn.execute(
        """
        INSERT INTO project (name, purpose, goals_json, status, version, user_directives_status, last_known_git_hash, last_git_sync)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (name, purpose, goals_json, status, version, user_directives_status, git_hash)
    )
    conn.commit()
    return cursor.lastrowid


def _query_project(conn: sqlite3.Connection) -> Optional[ProjectRecord]:
    """Effect: Query project record."""
    cursor = conn.execute("SELECT * FROM project LIMIT 1")
    row = cursor.fetchone()

    if row is None:
        return None

    # Parse goals JSON
    goals = tuple(json.loads(row['goals_json']))

    return ProjectRecord(
        id=row['id'],
        name=row['name'],
        purpose=row['purpose'],
        goals=goals,
        status=row['status'],
        version=row['version'],
        user_directives_status=row['user_directives_status'],
        last_known_git_hash=row['last_known_git_hash'],
        last_git_sync=row['last_git_sync'],
        created_at=row['created_at'],
        updated_at=row['updated_at']
    )


def _update_project_fields(
    conn: sqlite3.Connection,
    name: Optional[str],
    purpose: Optional[str],
    goals: Optional[List[str]],
    status: Optional[str],
    version: Optional[int],
    user_directives_status: Optional[str]
) -> None:
    """Effect: Update project fields (only non-None values)."""
    fields = []
    values = []

    if name is not None:
        fields.append("name = ?")
        values.append(name)

    if purpose is not None:
        fields.append("purpose = ?")
        values.append(purpose)

    if goals is not None:
        fields.append("goals_json = ?")
        values.append(json.dumps(goals))

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if version is not None:
        fields.append("version = ?")
        values.append(version)

    if user_directives_status is not None:
        fields.append("user_directives_status = ?")
        values.append(user_directives_status)

    if not fields:
        return  # Nothing to update

    query = f"UPDATE project SET {', '.join(fields)} WHERE id = 1"
    conn.execute(query, values)
    conn.commit()


def _query_infrastructure_by_type(conn: sqlite3.Connection, infra_type: str) -> Tuple[InfrastructureRecord, ...]:
    """Effect: Query infrastructure by type."""
    cursor = conn.execute(
        "SELECT * FROM infrastructure WHERE type = ? ORDER BY created_at",
        (infra_type,)
    )
    rows = cursor.fetchall()

    return tuple(
        InfrastructureRecord(
            id=row['id'],
            type=row['type'],
            value=row['value'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_all_infrastructure(conn: sqlite3.Connection) -> Tuple[InfrastructureRecord, ...]:
    """Effect: Query all infrastructure entries."""
    cursor = conn.execute("SELECT * FROM infrastructure ORDER BY created_at")
    rows = cursor.fetchall()

    return tuple(
        InfrastructureRecord(
            id=row['id'],
            type=row['type'],
            value=row['value'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _get_source_dir_value(conn: sqlite3.Connection) -> Optional[str]:
    """Effect: Get source directory value from infrastructure."""
    cursor = conn.execute(
        "SELECT value FROM infrastructure WHERE type = ?",
        (INFRASTRUCTURE_TYPE_SOURCE_DIR,)
    )
    row = cursor.fetchone()
    return row['value'] if row else None


def _insert_source_dir(conn: sqlite3.Connection, source_dir: str) -> None:
    """Effect: Insert source directory into infrastructure."""
    conn.execute(
        "INSERT INTO infrastructure (type, value, description) VALUES (?, ?, ?)",
        (INFRASTRUCTURE_TYPE_SOURCE_DIR, source_dir, 'Primary source code directory')
    )
    conn.commit()


def _update_source_dir(conn: sqlite3.Connection, new_source_dir: str) -> None:
    """Effect: Update source directory in infrastructure."""
    conn.execute(
        "UPDATE infrastructure SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE type = ?",
        (new_source_dir, INFRASTRUCTURE_TYPE_SOURCE_DIR)
    )
    conn.commit()


def _get_project_root_value(conn: sqlite3.Connection) -> Optional[str]:
    """Effect: Get project root value from infrastructure."""
    cursor = conn.execute(
        "SELECT value FROM infrastructure WHERE type = ?",
        (INFRASTRUCTURE_TYPE_PROJECT_ROOT,)
    )
    row = cursor.fetchone()
    return row['value'] if row else None


def _insert_project_root(conn: sqlite3.Connection, project_root: str) -> None:
    """Effect: Insert project root into infrastructure."""
    conn.execute(
        "INSERT INTO infrastructure (type, value, description) VALUES (?, ?, ?)",
        (INFRASTRUCTURE_TYPE_PROJECT_ROOT, project_root, 'Full path to project root directory')
    )
    conn.commit()


def _update_project_root(conn: sqlite3.Connection, new_project_root: str) -> None:
    """Effect: Update project root in infrastructure."""
    conn.execute(
        "UPDATE infrastructure SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE type = ?",
        (new_project_root, INFRASTRUCTURE_TYPE_PROJECT_ROOT)
    )
    conn.commit()


# ============================================================================
# Public Helper Functions
# ============================================================================

def create_project(
    name: str,
    purpose: str,
    goals: List[str],
    status: str = "active",
    version: int = 1,
    user_directives_status: Optional[str] = None
) -> AddResult:
    """
    Initialize project entry (one per database).

    Args:
        name: Project name (e.g., 'MatrixCalculator')
        purpose: Project purpose
        goals: Array of goal strings
        status: Project status ('active', 'paused', 'completed', 'abandoned')
        version: Version number (starts at 1)
        user_directives_status: Optional status (NULL, 'in_progress', 'active', 'disabled')

    Returns:
        AddResult with project ID
    """
    # Validate status
    if not _validate_project_status(status):
        return AddResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_PROJECT_STATUSES)}"
        )

    # Validate user_directives_status
    if not _validate_user_directives_status(user_directives_status):
        return AddResult(
            success=False,
            error=f"Invalid user_directives_status: {user_directives_status}. Must be one of: {', '.join(VALID_USER_DIRECTIVES_STATUSES)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if project already exists
        existing = _query_project(conn)
        if existing is not None:
            conn.close()
            return AddResult(
                success=False,
                error="Project already exists in database"
            )

        # Insert project
        project_id = _insert_project(conn, name, purpose, goals, status, version, user_directives_status)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("create_project")

        return AddResult(
            success=True,
            project_id=project_id,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return AddResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_project() -> ProjectResult:
    """
    Get project metadata (single entry).

    Returns:
        ProjectResult with project metadata or None if not initialized
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        project = _query_project(conn)
        conn.close()

        return ProjectResult(
            success=True,
            project=project
        )

    except Exception as e:
        conn.close()
        return ProjectResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_project(
    name: Optional[str] = None,
    purpose: Optional[str] = None,
    goals: Optional[List[str]] = None,
    status: Optional[str] = None,
    version: Optional[int] = None,
    user_directives_status: Optional[str] = None
) -> UpdateResult:
    """
    Update project metadata.

    Args:
        name: New name (None = don't update)
        purpose: New purpose (None = don't update)
        goals: New goals array (None = don't update)
        status: New status (None = don't update)
        version: New version (None = don't update)
        user_directives_status: New status (None = don't update)

    Returns:
        UpdateResult with success status
    """
    # Validate status if provided
    if status is not None and not _validate_project_status(status):
        return UpdateResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_PROJECT_STATUSES)}"
        )

    # Validate user_directives_status if provided (allow explicit None to clear)
    # Note: This allows setting it to None, which is valid

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if project exists
        existing = _query_project(conn)
        if existing is None:
            conn.close()
            return UpdateResult(
                success=False,
                error="No project exists in database"
            )

        # Update project
        _update_project_fields(conn, name, purpose, goals, status, version, user_directives_status)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_project")

        return UpdateResult(
            success=True,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return UpdateResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def blueprint_has_changed(blueprint_path: str) -> BlueprintChangeResult:
    """
    Check if ProjectBlueprint.md has changed using Git or filesystem timestamp.

    Args:
        blueprint_path: Path to ProjectBlueprint.md file

    Returns:
        BlueprintChangeResult with changed status and method
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Get project record
        project = _query_project(conn)
        conn.close()

        if project is None:
            return BlueprintChangeResult(
                success=False,
                error="No project exists in database"
            )

        # Try Git method first
        if _check_git_diff(blueprint_path):
            return BlueprintChangeResult(
                success=True,
                changed=True,
                method='git'
            )

        # Fallback to filesystem timestamp method
        if os.path.exists(blueprint_path):
            file_mtime = os.path.getmtime(blueprint_path)
            # Parse updated_at timestamp from database
            import datetime
            try:
                db_time = datetime.datetime.fromisoformat(project.updated_at).timestamp()
                if file_mtime > db_time:
                    return BlueprintChangeResult(
                        success=True,
                        changed=True,
                        method='filesystem'
                    )
            except (ValueError, AttributeError):
                pass

        return BlueprintChangeResult(
            success=True,
            changed=False,
            method='git'
        )

    except Exception as e:
        conn.close()
        return BlueprintChangeResult(
            success=False,
            error=f"Error checking blueprint: {str(e)}"
        )


def get_infrastructure_by_type(type: str) -> InfrastructureResult:
    """
    Get all infrastructure of specific type.

    Args:
        type: Infrastructure type (e.g., 'language', 'package', 'testing')

    Returns:
        InfrastructureResult with infrastructure entries (empty if none found)
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        infrastructure = _query_infrastructure_by_type(conn, type)
        conn.close()

        return InfrastructureResult(
            success=True,
            infrastructure=infrastructure
        )

    except Exception as e:
        conn.close()
        return InfrastructureResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_all_infrastructure() -> InfrastructureResult:
    """
    Get all infrastructure entries including standard fields (even if empty).

    Returns complete infrastructure table for session bundling and status reports.
    Used by aimfp_run to bundle infrastructure in session context.

    Returns:
        InfrastructureResult with all infrastructure entries (empty tuple if table doesn't exist)
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        infrastructure = _query_all_infrastructure(conn)
        conn.close()

        # Fetch return statements (only on success)
        return_stmts = get_return_statements("get_all_infrastructure")

        return InfrastructureResult(
            success=True,
            infrastructure=infrastructure,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        # Return empty array if table doesn't exist or database not initialized
        return InfrastructureResult(
            success=True,  # Not an error - just empty
            infrastructure=(),
            return_statements=()
        )


def get_source_directory() -> SourceDirResult:
    """
    Get project source directory from infrastructure table.

    Returns:
        SourceDirResult with source directory path or error
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        source_dir = _get_source_dir_value(conn)
        conn.close()

        if source_dir is None:
            return SourceDirResult(
                success=False,
                error="Source directory not configured. Must call add_source_directory() first."
            )

        # Fetch return statements
        return_stmts = get_return_statements("get_source_directory")

        return SourceDirResult(
            success=True,
            data=source_dir,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return SourceDirResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_source_directory(new_source_dir: str) -> SourceDirResult:
    """
    Update source directory in infrastructure table.

    Failsafe: If source_directory entry doesn't exist, it will be created.
    This should never happen (initialized by SQL), but provides safety.

    Args:
        new_source_dir: New source directory path

    Returns:
        SourceDirResult with success status
    """
    # Auto-convert absolute paths to relative
    project_root = get_cached_project_root()
    new_source_dir = _make_relative_source_dir(new_source_dir, project_root)

    # Validate new_source_dir
    error = _validate_source_dir(new_source_dir)
    if error:
        return SourceDirResult(success=False, error=error)
    conn = _open_project_connection(project_root)

    try:
        # Check if exists
        existing = _get_source_dir_value(conn)

        if existing is None:
            # Failsafe: Insert if not exists (should never happen, but safe)
            _insert_source_dir(conn, new_source_dir)
        else:
            # Normal path: Update existing entry
            _update_source_dir(conn, new_source_dir)

        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_source_directory")

        return SourceDirResult(
            success=True,
            data=new_source_dir,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return SourceDirResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_project_root() -> SourceDirResult:
    """
    Get project root directory from infrastructure table.

    Returns:
        SourceDirResult with project root path or error
    """
    cached_root = get_cached_project_root()
    conn = _open_project_connection(cached_root)

    try:
        project_root = _get_project_root_value(conn)
        conn.close()

        if project_root is None:
            return SourceDirResult(
                success=False,
                error="Project root not set in infrastructure table. "
                      "This indicates aimfp_init did not complete correctly — "
                      "the project_root row should be populated during initialization. "
                      "Re-run aimfp_init or manually call update_project_root()."
            )

        return SourceDirResult(
            success=True,
            data=project_root
        )

    except Exception as e:
        conn.close()
        return SourceDirResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_project_root(new_project_root: str) -> SourceDirResult:
    """
    Update project root in infrastructure table.

    Failsafe: If project_root entry doesn't exist, it will be created.
    This should never happen (initialized by SQL), but provides safety.

    Args:
        new_project_root: New project root path (absolute path)

    Returns:
        SourceDirResult with success status
    """
    # Validate it's an absolute path
    if not os.path.isabs(new_project_root):
        return SourceDirResult(
            success=False,
            error=f"Project root must be an absolute path, got: {new_project_root}"
        )

    cached_root = get_cached_project_root()
    conn = _open_project_connection(cached_root)

    try:
        # Check if exists
        existing = _get_project_root_value(conn)

        if existing is None:
            # Failsafe: Insert if not exists (should never happen, but safe)
            _insert_project_root(conn, new_project_root)
        else:
            # Normal path: Update existing entry
            _update_project_root(conn, new_project_root)

        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_project_root")

        return SourceDirResult(
            success=True,
            data=new_project_root,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return SourceDirResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
