"""
AIMFP Helper Functions - Project Themes & Flows (Part 2)

Theme-flow relationships, file-flow relationships, and completion path management.
Handles relationship queries and completion path lifecycle operations.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- get_flows_for_theme: Get all flows for a theme
- get_themes_for_flow: Get all themes for a flow
- get_files_by_flow: Get all files for a flow
- get_flows_for_file: Get all flows for a file
- add_completion_path: Add completion path stage
- get_all_completion_paths: Get all completion paths ordered
- get_next_completion_path: Get lowest order_index non-completed
- get_completion_paths_by_status: Get completion paths filtered by status
- get_incomplete_completion_paths: Get all non-completed paths
- update_completion_path: Update completion path metadata
- delete_completion_path: Delete completion path with validation
- reorder_completion_path: Change order_index for a completion path
- reorder_all_completion_paths: Fix gaps and duplicates in order_index
- swap_completion_paths_order: Swap order_index of two completion paths
- add_file_to_flow: Link a file to a flow
- add_file_flows: Link multiple files to flows (batch)
- remove_file_from_flow: Remove a file-flow link
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, List, Tuple

from ..utils import get_return_statements

# Import common project utilities (DRY principle)
from ._common import (
    _open_connection,
    get_cached_project_root,
    _open_project_connection,
    _create_deletion_note,
    _validate_status,
    VALID_MILESTONE_STATUSES
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class FlowRecord:
    """Immutable flow record from database."""
    id: int
    name: str
    description: Optional[str]
    ai_generated: bool
    confidence_score: Optional[float]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ThemeRecord:
    """Immutable theme record from database."""
    id: int
    name: str
    description: Optional[str]
    ai_generated: bool
    confidence_score: Optional[float]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FileRecord:
    """Immutable file record from database."""
    id: int
    name: str
    path: str
    language: str
    is_reserved: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CompletionPathRecord:
    """Immutable completion path record from database."""
    id: int
    name: str
    order_index: int
    status: str
    description: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FlowsQueryResult:
    """Result of flows query operation."""
    success: bool
    flows: Tuple[FlowRecord, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class ThemesQueryResult:
    """Result of themes query operation."""
    success: bool
    themes: Tuple[ThemeRecord, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class FilesQueryResult:
    """Result of files query operation."""
    success: bool
    files: Tuple[FileRecord, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class AddCompletionPathResult:
    """Result of completion path addition operation."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletionPathsQueryResult:
    """Result of completion paths query operation."""
    success: bool
    paths: Tuple[CompletionPathRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletionPathQueryResult:
    """Result of single completion path query operation."""
    success: bool
    path: Optional[CompletionPathRecord] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdateCompletionPathResult:
    """Result of completion path update operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeleteCompletionPathResult:
    """Result of completion path deletion operation."""
    success: bool
    error: Optional[str] = None
    milestones: Tuple[str, ...] = ()  # Milestone names blocking deletion
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ReorderResult:
    """Result of reorder operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ReorderAllResult:
    """Result of reorder all operation."""
    success: bool
    renumbered_count: int = 0
    duplicates_found: Tuple[dict, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AddFileFlowResult:
    """Result of adding a file-flow link."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AddFileFlowsBatchResult:
    """Result of adding multiple file-flow links."""
    success: bool
    added_count: int = 0
    skipped_count: int = 0
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RemoveFileFlowResult:
    """Result of removing a file-flow link."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helper Functions
# ============================================================================

def row_to_flow_record(row: sqlite3.Row) -> FlowRecord:
    """
    Convert database row to immutable FlowRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable FlowRecord
    """
    return FlowRecord(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        ai_generated=bool(row["ai_generated"]),
        confidence_score=row["confidence_score"],
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


def row_to_theme_record(row: sqlite3.Row) -> ThemeRecord:
    """
    Convert database row to immutable ThemeRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable ThemeRecord
    """
    return ThemeRecord(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        ai_generated=bool(row["ai_generated"]),
        confidence_score=row["confidence_score"],
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


def row_to_file_record(row: sqlite3.Row) -> FileRecord:
    """
    Convert database row to immutable FileRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable FileRecord
    """
    return FileRecord(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        language=row["language"],
        is_reserved=bool(row["is_reserved"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


def row_to_completion_path_record(row: sqlite3.Row) -> CompletionPathRecord:
    """
    Convert database row to immutable CompletionPathRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable CompletionPathRecord
    """
    return CompletionPathRecord(
        id=row["id"],
        name=row["name"],
        order_index=row["order_index"],
        status=row["status"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


def build_completion_path_update_query(
    path_id: int,
    name: Optional[str],
    status: Optional[str],
    description: Optional[str]
) -> Tuple[str, Tuple]:
    """
    Build SQL UPDATE query for completion path with only non-NULL fields.

    Pure function - deterministic query building.

    Args:
        path_id: Completion path ID to update
        name: New name (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)

    Returns:
        Tuple of (sql_query, parameters)
    """
    updates = []
    params_list = []

    if name is not None:
        updates.append("name = ?")
        params_list.append(name)

    if status is not None:
        updates.append("status = ?")
        params_list.append(status)

    if description is not None:
        updates.append("description = ?")
        params_list.append(description)

    # Always update timestamp
    updates.append("updated_at = CURRENT_TIMESTAMP")

    # Build query
    sql = f"UPDATE completion_path SET {', '.join(updates)} WHERE id = ?"
    params_list.append(path_id)

    return (sql, tuple(params_list))


# ============================================================================
# Database Effect Functions
# ============================================================================

def _get_flows_for_theme_effect(
    conn: sqlite3.Connection,
    theme_id: int
) -> List[sqlite3.Row]:
    """
    Effect: Query flows for theme via junction table.

    Args:
        conn: Database connection
        theme_id: Theme ID

    Returns:
        List of row objects
    """
    cursor = conn.execute(
        """
        SELECT f.*
        FROM flows f
        JOIN flow_themes ft ON f.id = ft.flow_id
        WHERE ft.theme_id = ?
        """,
        (theme_id,)
    )
    return cursor.fetchall()


def _get_themes_for_flow_effect(
    conn: sqlite3.Connection,
    flow_id: int
) -> List[sqlite3.Row]:
    """
    Effect: Query themes for flow via junction table.

    Args:
        conn: Database connection
        flow_id: Flow ID

    Returns:
        List of row objects
    """
    cursor = conn.execute(
        """
        SELECT t.*
        FROM themes t
        JOIN flow_themes ft ON t.id = ft.theme_id
        WHERE ft.flow_id = ?
        """,
        (flow_id,)
    )
    return cursor.fetchall()


def _get_files_by_flow_effect(
    conn: sqlite3.Connection,
    flow_id: int
) -> List[sqlite3.Row]:
    """
    Effect: Query files for flow via junction table.

    Args:
        conn: Database connection
        flow_id: Flow ID

    Returns:
        List of row objects
    """
    cursor = conn.execute(
        """
        SELECT f.*
        FROM files f
        JOIN file_flows ff ON f.id = ff.file_id
        WHERE ff.flow_id = ?
        """,
        (flow_id,)
    )
    return cursor.fetchall()


def _get_flows_for_file_effect(
    conn: sqlite3.Connection,
    file_id: int
) -> List[sqlite3.Row]:
    """
    Effect: Query flows for file via junction table.

    Args:
        conn: Database connection
        file_id: File ID

    Returns:
        List of row objects
    """
    cursor = conn.execute(
        """
        SELECT f.*
        FROM flows f
        JOIN file_flows ff ON f.id = ff.flow_id
        WHERE ff.file_id = ?
        """,
        (file_id,)
    )
    return cursor.fetchall()


def _add_completion_path_effect(
    conn: sqlite3.Connection,
    name: str,
    status: str,
    description: Optional[str],
    order_index: int
) -> int:
    """
    Effect: Insert completion path into database.

    Args:
        conn: Database connection
        name: Completion path name
        status: Status ('pending', 'in_progress', 'completed')
        description: Description
        order_index: Order position

    Returns:
        Inserted completion path ID
    """
    cursor = conn.execute(
        """
        INSERT INTO completion_path (name, status, description, order_index)
        VALUES (?, ?, ?, ?)
        """,
        (name, status, description, order_index)
    )
    conn.commit()
    return cursor.lastrowid


def _get_all_completion_paths_effect(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """
    Effect: Query all completion paths ordered by order_index.

    Args:
        conn: Database connection

    Returns:
        List of row objects ordered by order_index ASC
    """
    cursor = conn.execute("SELECT * FROM completion_path ORDER BY order_index ASC")
    return cursor.fetchall()


def _get_next_completion_path_effect(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """
    Effect: Query next incomplete completion path.

    Args:
        conn: Database connection

    Returns:
        Row object or None
    """
    cursor = conn.execute(
        """
        SELECT * FROM completion_path
        WHERE status != 'completed'
        ORDER BY order_index ASC
        LIMIT 1
        """
    )
    return cursor.fetchone()


def _get_completion_paths_by_status_effect(
    conn: sqlite3.Connection,
    status: str
) -> List[sqlite3.Row]:
    """
    Effect: Query completion paths by status.

    Args:
        conn: Database connection
        status: Status to filter by

    Returns:
        List of row objects ordered by order_index ASC
    """
    cursor = conn.execute(
        """
        SELECT * FROM completion_path
        WHERE status = ?
        ORDER BY order_index ASC
        """,
        (status,)
    )
    return cursor.fetchall()


def _get_incomplete_completion_paths_effect(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """
    Effect: Query all incomplete completion paths.

    Args:
        conn: Database connection

    Returns:
        List of row objects ordered by order_index ASC
    """
    cursor = conn.execute(
        """
        SELECT * FROM completion_path
        WHERE status != 'completed'
        ORDER BY order_index ASC
        """
    )
    return cursor.fetchall()


def _check_completion_path_exists(conn: sqlite3.Connection, path_id: int) -> bool:
    """
    Effect: Check if completion path ID exists.

    Args:
        conn: Database connection
        path_id: Completion path ID to check

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute("SELECT id FROM completion_path WHERE id = ?", (path_id,))
    return cursor.fetchone() is not None


def _update_completion_path_effect(conn: sqlite3.Connection, sql: str, params: Tuple) -> None:
    """
    Effect: Execute completion path update query.

    Args:
        conn: Database connection
        sql: UPDATE query
        params: Query parameters
    """
    conn.execute(sql, params)
    conn.commit()


def _get_milestones_for_path_effect(conn: sqlite3.Connection, path_id: int) -> List[str]:
    """
    Effect: Get milestone names for completion path (blocking deletion check).

    Args:
        conn: Database connection
        path_id: Completion path ID

    Returns:
        List of milestone names
    """
    cursor = conn.execute(
        "SELECT name FROM milestones WHERE completion_path_id = ?",
        (path_id,)
    )
    return [row["name"] for row in cursor.fetchall()]


def _delete_completion_path_effect(conn: sqlite3.Connection, path_id: int) -> None:
    """
    Effect: Delete completion path from database.

    Args:
        conn: Database connection
        path_id: Completion path ID to delete
    """
    conn.execute("DELETE FROM completion_path WHERE id = ?", (path_id,))
    conn.commit()


def _reorder_completion_path_effect(
    conn: sqlite3.Connection,
    path_id: int,
    new_order_index: int
) -> None:
    """
    Effect: Update order_index for completion path.

    Args:
        conn: Database connection
        path_id: Completion path ID
        new_order_index: New order position
    """
    conn.execute(
        "UPDATE completion_path SET order_index = ? WHERE id = ?",
        (new_order_index, path_id)
    )
    conn.commit()


def _reorder_all_completion_paths_effect(conn: sqlite3.Connection) -> Tuple[int, List[dict]]:
    """
    Effect: Renumber all completion paths to 1, 2, 3... sequence.

    Args:
        conn: Database connection

    Returns:
        Tuple of (renumbered_count, duplicates_found)
    """
    # Get all paths ordered by current order_index
    cursor = conn.execute("SELECT id, order_index FROM completion_path ORDER BY order_index ASC, id ASC")
    paths = cursor.fetchall()

    duplicates_found = []
    renumbered_count = 0

    for new_index, row in enumerate(paths, start=1):
        path_id = row["id"]
        old_index = row["order_index"]

        if old_index != new_index:
            # Track if this was a duplicate
            if any(p["order_index"] == old_index for p in paths[:new_index-1]):
                duplicates_found.append({
                    "id": path_id,
                    "old_order": old_index,
                    "new_order": new_index
                })

            conn.execute(
                "UPDATE completion_path SET order_index = ? WHERE id = ?",
                (new_index, path_id)
            )
            renumbered_count += 1

    conn.commit()
    return (renumbered_count, duplicates_found)


def _swap_completion_paths_order_effect(
    conn: sqlite3.Connection,
    id1: int,
    id2: int
) -> None:
    """
    Effect: Swap order_index values between two completion paths.

    Args:
        conn: Database connection
        id1: First completion path ID
        id2: Second completion path ID
    """
    # Get current order_index values
    cursor = conn.execute("SELECT order_index FROM completion_path WHERE id = ?", (id1,))
    order1 = cursor.fetchone()["order_index"]

    cursor = conn.execute("SELECT order_index FROM completion_path WHERE id = ?", (id2,))
    order2 = cursor.fetchone()["order_index"]

    # Use temporary negative value to avoid constraint violations
    conn.execute("UPDATE completion_path SET order_index = ? WHERE id = ?", (-1, id1))
    conn.execute("UPDATE completion_path SET order_index = ? WHERE id = ?", (order1, id2))
    conn.execute("UPDATE completion_path SET order_index = ? WHERE id = ?", (order2, id1))

    conn.commit()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def get_flows_for_theme(
    theme_id: int,
    project_root: Optional[str] = None
) -> FlowsQueryResult:
    """
    Get all flows for a theme.

    Queries flow_themes junction table to find flows associated with theme.

    Args:
        theme_id: Theme ID

    Returns:
        FlowsQueryResult with tuple of flow records

    Example:
        >>> result = get_flows_for_theme(1)
        >>> result.success
        True
        >>> len(result.flows)
        3
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_flows_for_theme_effect(conn, theme_id)
        flow_records = tuple(row_to_flow_record(row) for row in rows)

        return FlowsQueryResult(
            success=True,
            flows=flow_records
        )

    except Exception as e:
        return FlowsQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_themes_for_flow(
    flow_id: int,
    project_root: Optional[str] = None
) -> ThemesQueryResult:
    """
    Get all themes for a flow.

    Queries flow_themes junction table to find themes associated with flow.

    Args:
        flow_id: Flow ID

    Returns:
        ThemesQueryResult with tuple of theme records

    Example:
        >>> result = get_themes_for_flow(5)
        >>> result.success
        True
        >>> len(result.themes)
        2
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_themes_for_flow_effect(conn, flow_id)
        theme_records = tuple(row_to_theme_record(row) for row in rows)

        return ThemesQueryResult(
            success=True,
            themes=theme_records
        )

    except Exception as e:
        return ThemesQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_files_by_flow(
    flow_id: int,
    project_root: Optional[str] = None
) -> FilesQueryResult:
    """
    Get all files for a flow.

    Queries file_flows junction table to find files associated with flow.

    Args:
        flow_id: Flow ID

    Returns:
        FilesQueryResult with tuple of file records

    Example:
        >>> result = get_files_by_flow(5)
        >>> result.success
        True
        >>> len(result.files)
        8
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_files_by_flow_effect(conn, flow_id)
        file_records = tuple(row_to_file_record(row) for row in rows)

        return FilesQueryResult(
            success=True,
            files=file_records
        )

    except Exception as e:
        return FilesQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_flows_for_file(
    file_id: int,
    project_root: Optional[str] = None
) -> FlowsQueryResult:
    """
    Get all flows for a file.

    Queries file_flows junction table to find flows associated with file.

    Args:
        file_id: File ID

    Returns:
        FlowsQueryResult with tuple of flow records

    Example:
        >>> result = get_flows_for_file(42)
        >>> result.success
        True
        >>> len(result.flows)
        2
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_flows_for_file_effect(conn, file_id)
        flow_records = tuple(row_to_flow_record(row) for row in rows)

        return FlowsQueryResult(
            success=True,
            flows=flow_records
        )

    except Exception as e:
        return FlowsQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def add_completion_path(
    name: str,
    status: str = "pending",
    description: Optional[str] = None,
    order_index: int = 1,
    project_root: Optional[str] = None
) -> AddCompletionPathResult:
    """
    Add completion path stage.

    Creates new completion path record in completion_path table.

    Args:
        name: Completion path name (e.g., 'setup', 'core dev', 'finish')
        status: Status ('pending', 'in_progress', 'completed')
        description: Description (optional)
        order_index: Order position (1, 2, 3...)

    Returns:
        AddCompletionPathResult with success status and new path ID

    Example:
        >>> result = add_completion_path(
        ...     "Core Development",
        ...     status="pending",
        ...     order_index=2
        ... )
        >>> result.success
        True
        >>> result.id
        2
    """
    # Validate status before database operation
    if not _validate_status(status, VALID_MILESTONE_STATUSES):
        valid_statuses = ', '.join(sorted(VALID_MILESTONE_STATUSES))
        return AddCompletionPathResult(
            success=False,
            error=f"Invalid status '{status}', must be one of: {valid_statuses}"
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        path_id = _add_completion_path_effect(
            conn,
            name,
            status,
            description,
            order_index
        )

        return_statements = get_return_statements("add_completion_path")

        return AddCompletionPathResult(
            success=True,
            id=path_id,
            return_statements=return_statements
        )

    except Exception as e:
        return AddCompletionPathResult(
            success=False,
            error=f"Failed to add completion path: {str(e)}"
        )

    finally:
        conn.close()


def get_all_completion_paths(project_root: Optional[str] = None) -> CompletionPathsQueryResult:
    """
    Get all completion paths ordered by order_index.

    Queries all records from completion_path table ordered by order_index ASC.

    Returns:
        CompletionPathsQueryResult with tuple of completion path records

    Example:
        >>> result = get_all_completion_paths()
        >>> result.success
        True
        >>> len(result.paths)
        5
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_all_completion_paths_effect(conn)
        path_records = tuple(row_to_completion_path_record(row) for row in rows)

        return_statements = get_return_statements("get_all_completion_paths")

        return CompletionPathsQueryResult(
            success=True,
            paths=path_records,
            return_statements=return_statements
        )

    except Exception as e:
        return CompletionPathsQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_next_completion_path(project_root: Optional[str] = None) -> CompletionPathQueryResult:
    """
    Get lowest order_index with status != completed.

    Queries for next incomplete completion path.

    Returns:
        CompletionPathQueryResult with completion path record or None

    Example:
        >>> result = get_next_completion_path()
        >>> result.success
        True
        >>> result.path.name
        'Core Development'
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        row = _get_next_completion_path_effect(conn)

        if row is None:
            return CompletionPathQueryResult(
                success=True,
                path=None
            )

        path_record = row_to_completion_path_record(row)

        return_statements = get_return_statements("get_next_completion_path")

        return CompletionPathQueryResult(
            success=True,
            path=path_record,
            return_statements=return_statements
        )

    except Exception as e:
        return CompletionPathQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_completion_paths_by_status(
    status: str,
    project_root: Optional[str] = None
) -> CompletionPathsQueryResult:
    """
    Get completion paths filtered by status.

    Queries completion paths with specific status ordered by order_index.

    Args:
        status: Status to filter by ('pending', 'in_progress', 'completed')

    Returns:
        CompletionPathsQueryResult with tuple of completion path records

    Example:
        >>> result = get_completion_paths_by_status("in_progress")
        >>> result.success
        True
        >>> len(result.paths)
        1
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_completion_paths_by_status_effect(conn, status)
        path_records = tuple(row_to_completion_path_record(row) for row in rows)

        return_statements = get_return_statements("get_completion_paths_by_status")

        return CompletionPathsQueryResult(
            success=True,
            paths=path_records,
            return_statements=return_statements
        )

    except Exception as e:
        return CompletionPathsQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_incomplete_completion_paths(project_root: Optional[str] = None) -> CompletionPathsQueryResult:
    """
    Get all non-completed paths.

    Queries completion paths where status != 'completed' ordered by order_index.

    Returns:
        CompletionPathsQueryResult with tuple of completion path records

    Example:
        >>> result = get_incomplete_completion_paths()
        >>> result.success
        True
        >>> len(result.paths)
        3
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_incomplete_completion_paths_effect(conn)
        path_records = tuple(row_to_completion_path_record(row) for row in rows)

        return_statements = get_return_statements("get_incomplete_completion_paths")

        return CompletionPathsQueryResult(
            success=True,
            paths=path_records,
            return_statements=return_statements
        )

    except Exception as e:
        return CompletionPathsQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def update_completion_path(
    id: int,
    name: Optional[str] = None,
    status: Optional[str] = None,
    description: Optional[str] = None,
    project_root: Optional[str] = None
) -> UpdateCompletionPathResult:
    """
    Update completion path.

    Only updates non-NULL parameters. Order_index cannot be updated here - use reorder helpers.

    Args:
        id: Completion path ID
        name: New name (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)

    Returns:
        UpdateCompletionPathResult with success status

    Example:
        >>> result = update_completion_path(2, status="in_progress")
        >>> result.success
        True
    """
    # Validate at least one parameter is provided
    if all(v is None for v in [name, status, description]):
        return UpdateCompletionPathResult(
            success=False,
            error="At least one parameter must be provided for update"
        )

    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_MILESTONE_STATUSES):
        valid_statuses = ', '.join(sorted(VALID_MILESTONE_STATUSES))
        return UpdateCompletionPathResult(
            success=False,
            error=f"Invalid status '{status}', must be one of: {valid_statuses}"
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_completion_path_exists(conn, id):
            return UpdateCompletionPathResult(
                success=False,
                error=f"Completion path with ID {id} not found"
            )

        sql, params = build_completion_path_update_query(id, name, status, description)
        _update_completion_path_effect(conn, sql, params)

        return_statements = get_return_statements("update_completion_path")

        return UpdateCompletionPathResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return UpdateCompletionPathResult(
            success=False,
            error=f"Failed to update completion path: {str(e)}"
        )

    finally:
        conn.close()


def delete_completion_path(
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion",
    project_root: Optional[str] = None
) -> DeleteCompletionPathResult:
    """
    Delete completion path with milestone validation.

    Validates no milestones are linked to completion path before deletion.

    Args:
        id: Completion path ID to delete
        note_reason: Deletion reason
        note_severity: 'info', 'warning', 'error'
        note_source: 'ai' or 'user'
        note_type: Note type (default: 'entry_deletion')

    Returns:
        DeleteCompletionPathResult with success status or blocking milestones list

    Example:
        >>> result = delete_completion_path(
        ...     2,
        ...     note_reason="Path no longer needed",
        ...     note_severity="info",
        ...     note_source="ai"
        ... )
        >>> result.success
        True
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_completion_path_exists(conn, id):
            return DeleteCompletionPathResult(
                success=False,
                error=f"Completion path with ID {id} not found"
            )

        # Check for milestones linked to completion path
        milestone_names = _get_milestones_for_path_effect(conn, id)

        if milestone_names:
            return DeleteCompletionPathResult(
                success=False,
                error="milestones_exist",
                milestones=tuple(milestone_names)
            )

        # No blocking milestones - proceed with deletion
        _create_deletion_note(
            conn,
            "completion_path",
            id,
            note_reason,
            note_severity,
            note_source,
            note_type
        )

        _delete_completion_path_effect(conn, id)

        return_statements = get_return_statements("delete_completion_path")

        return DeleteCompletionPathResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return DeleteCompletionPathResult(
            success=False,
            error=f"Failed to delete completion path: {str(e)}"
        )

    finally:
        conn.close()


def reorder_completion_path(
    id: int,
    new_order_index: int,
    project_root: Optional[str] = None
) -> ReorderResult:
    """
    Change order_index for a completion path.

    Updates order_index field only. May create gaps in sequence - consider reorder_all after use.

    Args:
        id: Completion path ID
        new_order_index: New order position

    Returns:
        ReorderResult with success status

    Example:
        >>> result = reorder_completion_path(3, 1)
        >>> result.success
        True
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_completion_path_exists(conn, id):
            return ReorderResult(
                success=False,
                error=f"Completion path with ID {id} not found"
            )

        _reorder_completion_path_effect(conn, id, new_order_index)

        return_statements = get_return_statements("reorder_completion_path")

        return ReorderResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return ReorderResult(
            success=False,
            error=f"Failed to reorder completion path: {str(e)}"
        )

    finally:
        conn.close()


def reorder_all_completion_paths(project_root: Optional[str] = None) -> ReorderAllResult:
    """
    Fix gaps and duplicates in order_index.

    Renumbers all completion paths to 1, 2, 3, 4... sequence. Preserves relative order while closing gaps.

    Returns:
        ReorderAllResult with renumbered_count and duplicates_found

    Example:
        >>> result = reorder_all_completion_paths()
        >>> result.success
        True
        >>> result.renumbered_count
        2
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        renumbered_count, duplicates = _reorder_all_completion_paths_effect(conn)

        return_statements = get_return_statements("reorder_all_completion_paths")

        return ReorderAllResult(
            success=True,
            renumbered_count=renumbered_count,
            duplicates_found=tuple(duplicates),
            return_statements=return_statements
        )

    except Exception as e:
        return ReorderAllResult(
            success=False,
            error=f"Failed to reorder all completion paths: {str(e)}"
        )

    finally:
        conn.close()


def swap_completion_paths_order(
    id1: int,
    id2: int,
    project_root: Optional[str] = None
) -> ReorderResult:
    """
    Swap order_index of two completion paths.

    Exchanges order_index values between two paths. Temporarily allows duplicates during swap - atomic operation.

    Args:
        id1: First completion path ID
        id2: Second completion path ID

    Returns:
        ReorderResult with success status

    Example:
        >>> result = swap_completion_paths_order(1, 3)
        >>> result.success
        True
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_completion_path_exists(conn, id1):
            return ReorderResult(
                success=False,
                error=f"Completion path with ID {id1} not found"
            )

        if not _check_completion_path_exists(conn, id2):
            return ReorderResult(
                success=False,
                error=f"Completion path with ID {id2} not found"
            )

        _swap_completion_paths_order_effect(conn, id1, id2)

        return_statements = get_return_statements("swap_completion_paths_order")

        return ReorderResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return ReorderResult(
            success=False,
            error=f"Failed to swap completion paths order: {str(e)}"
        )

    finally:
        conn.close()


# ============================================================================
# File-Flow Linking Functions
# ============================================================================

def _check_file_exists(conn: sqlite3.Connection, file_id: int) -> bool:
    """Effect: Check if file ID exists in database."""
    cursor = conn.execute("SELECT id FROM files WHERE id = ?", (file_id,))
    return cursor.fetchone() is not None


def _check_flow_exists(conn: sqlite3.Connection, flow_id: int) -> bool:
    """Effect: Check if flow ID exists in database."""
    cursor = conn.execute("SELECT id FROM flows WHERE id = ?", (flow_id,))
    return cursor.fetchone() is not None


def _check_file_flow_exists(conn: sqlite3.Connection, file_id: int, flow_id: int) -> bool:
    """Effect: Check if file-flow link already exists."""
    cursor = conn.execute(
        "SELECT 1 FROM file_flows WHERE file_id = ? AND flow_id = ?",
        (file_id, flow_id)
    )
    return cursor.fetchone() is not None


def _add_file_flow_effect(conn: sqlite3.Connection, file_id: int, flow_id: int) -> None:
    """Effect: Insert file-flow link into junction table."""
    conn.execute(
        "INSERT INTO file_flows (file_id, flow_id) VALUES (?, ?)",
        (file_id, flow_id)
    )
    conn.commit()


def _remove_file_flow_effect(conn: sqlite3.Connection, file_id: int, flow_id: int) -> None:
    """Effect: Delete file-flow link from junction table."""
    conn.execute(
        "DELETE FROM file_flows WHERE file_id = ? AND flow_id = ?",
        (file_id, flow_id)
    )
    conn.commit()


def add_file_to_flow(
    file_id: int,
    flow_id: int,
    project_root: Optional[str] = None
) -> AddFileFlowResult:
    """
    Link a file to a flow in the file_flows junction table.

    Every file should be linked to at least one flow after finalization.
    This connects the file to the project's architectural structure.

    Args:
        file_id: File ID (from reserve_file/finalize_file)
        flow_id: Flow ID (from add_flow or get_all_flows)

    Returns:
        AddFileFlowResult with success status

    Example:
        >>> result = add_file_to_flow(file_id=42, flow_id=3)
        >>> result.success
        True
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_file_exists(conn, file_id):
            return AddFileFlowResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        if not _check_flow_exists(conn, flow_id):
            return AddFileFlowResult(
                success=False,
                error=f"Flow with ID {flow_id} not found"
            )

        if _check_file_flow_exists(conn, file_id, flow_id):
            return AddFileFlowResult(
                success=False,
                error=f"File {file_id} is already linked to flow {flow_id}"
            )

        _add_file_flow_effect(conn, file_id, flow_id)

        return_statements = get_return_statements("add_file_to_flow")

        return AddFileFlowResult(
            success=True,
            return_statements=return_statements,
        )

    except Exception as e:
        return AddFileFlowResult(
            success=False,
            error=f"Failed to link file to flow: {str(e)}",
        )

    finally:
        conn.close()


def add_file_flows(
    links: List[Tuple[int, int]],
    project_root: Optional[str] = None
) -> AddFileFlowsBatchResult:
    """
    Link multiple files to flows in the file_flows junction table (batch).

    More efficient than multiple single calls. Skips duplicates silently.

    Args:
        links: List of (file_id, flow_id) tuples to create

    Returns:
        AddFileFlowsBatchResult with added/skipped counts

    Example:
        >>> result = add_file_flows([(42, 3), (42, 5), (43, 3)])
        >>> result.success
        True
        >>> result.added_count
        3
    """
    if not links:
        return AddFileFlowsBatchResult(
            success=False,
            error="Links list cannot be empty",
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        added = 0
        skipped = 0

        for file_id, flow_id in links:
            if not _check_file_exists(conn, file_id):
                return AddFileFlowsBatchResult(
                    success=False,
                    error=f"File with ID {file_id} not found",
                )

            if not _check_flow_exists(conn, flow_id):
                return AddFileFlowsBatchResult(
                    success=False,
                    error=f"Flow with ID {flow_id} not found",
                )

            if _check_file_flow_exists(conn, file_id, flow_id):
                skipped += 1
                continue

            _add_file_flow_effect(conn, file_id, flow_id)
            added += 1

        return_statements = get_return_statements("add_file_flows")

        return AddFileFlowsBatchResult(
            success=True,
            added_count=added,
            skipped_count=skipped,
            return_statements=return_statements,
        )

    except Exception as e:
        return AddFileFlowsBatchResult(
            success=False,
            error=f"Batch link failed: {str(e)}",
        )

    finally:
        conn.close()


def remove_file_from_flow(
    file_id: int,
    flow_id: int,
    project_root: Optional[str] = None
) -> RemoveFileFlowResult:
    """
    Remove a file-flow link from the file_flows junction table.

    Use when reassigning a file to a different flow or when preparing
    a file for deletion. Does not delete the file or the flow.

    Args:
        file_id: File ID to unlink
        flow_id: Flow ID to unlink from

    Returns:
        RemoveFileFlowResult with success status

    Example:
        >>> result = remove_file_from_flow(file_id=42, flow_id=3)
        >>> result.success
        True
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_file_exists(conn, file_id):
            return RemoveFileFlowResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        if not _check_flow_exists(conn, flow_id):
            return RemoveFileFlowResult(
                success=False,
                error=f"Flow with ID {flow_id} not found"
            )

        if not _check_file_flow_exists(conn, file_id, flow_id):
            return RemoveFileFlowResult(
                success=False,
                error=f"File {file_id} is not linked to flow {flow_id}"
            )

        _remove_file_flow_effect(conn, file_id, flow_id)

        return_statements = get_return_statements("remove_file_from_flow")

        return RemoveFileFlowResult(
            success=True,
            return_statements=return_statements,
        )

    except Exception as e:
        return RemoveFileFlowResult(
            success=False,
            error=f"Failed to remove file-flow link: {str(e)}",
        )

    finally:
        conn.close()
