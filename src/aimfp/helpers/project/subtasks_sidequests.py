"""
AIMFP Helper Functions - Project Subtasks & Sidequests

Subtask and sidequest management operations for project database.
Implements CRUD and query operations for subtasks (focused task breakdown)
and sidequests (urgent interruptions).

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- add_subtask: Add subtask to task
- get_incomplete_subtasks: Get all non-completed subtasks
- get_incomplete_subtasks_by_task: Get incomplete subtasks for specific task
- get_subtasks_by_task: Get subtasks for task, optionally filtered by status
- get_subtasks_comprehensive: Advanced subtask search
- update_subtask: Update subtask metadata
- delete_subtask: Delete subtask with item validation
- add_sidequest: Add sidequest (urgent interruption)
- get_incomplete_sidequests: Get all non-completed sidequests
- get_sidequests_comprehensive: Advanced sidequest search
- get_sidequest_flows: Get flow IDs for a sidequest
- get_sidequest_files: Get all files related to sidequest via flows (orchestrator)
- update_sidequest: Update sidequest metadata
- delete_sidequest: Delete sidequest with item validation
"""

import sqlite3
import json
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

from ..utils import get_return_statements
from ..shared.slugs import mint_slug

# Import common project utilities (DRY principle)
from ._common import (
    _open_connection,
    get_cached_project_root,
    _open_project_connection,
    _check_entity_exists,
    _create_deletion_note,
    _validate_status,
    _validate_priority,
    VALID_TASK_STATUSES,
    VALID_PRIORITY_LEVELS
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class SubtaskRecord:
    """Immutable subtask record from database."""
    id: int
    parent_task_id: int
    name: str
    status: str
    priority: str
    description: Optional[str]
    created_at: str
    updated_at: str
    slug: Optional[str] = None  # stable cross-clone identity (see helpers/shared/slugs.py)


@dataclass(frozen=True)
class SidequestRecord:
    """Immutable sidequest record from database."""
    id: int
    paused_task_id: int
    paused_subtask_id: Optional[int]
    name: str
    status: str
    priority: str
    description: Optional[str]
    flow_ids: Optional[str]  # JSON array
    created_at: str
    updated_at: str
    slug: Optional[str] = None  # stable cross-clone identity (see helpers/shared/slugs.py)


@dataclass(frozen=True)
class FileRecord:
    """Immutable file record from database."""
    id: int
    name: str
    path: str
    language: str


@dataclass(frozen=True)
class AddResult:
    """Result of add operation."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdateResult:
    """Result of update operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeleteResult:
    """Result of delete operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SubtaskQueryResult:
    """Result of subtask query operation."""
    success: bool
    subtasks: Tuple[SubtaskRecord, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class SidequestQueryResult:
    """Result of sidequest query operation."""
    success: bool
    sidequests: Tuple[SidequestRecord, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class FlowIdsResult:
    """Result of flow IDs query."""
    success: bool
    flow_ids: Tuple[int, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class FilesResult:
    """Result of files query."""
    success: bool
    files: Tuple[FileRecord, ...] = ()
    error: Optional[str] = None


# ============================================================================
# Effect Functions - Subtask Operations
# ============================================================================

def _insert_subtask(
    conn: sqlite3.Connection,
    parent_task_id: int,
    name: str,
    status: str,
    priority: str,
    description: Optional[str]
) -> int:
    """
    Effect: Insert subtask into database.

    Args:
        conn: Database connection
        parent_task_id: Parent task ID
        name: Subtask name
        status: Subtask status
        priority: Subtask priority
        description: Optional description

    Returns:
        New subtask ID
    """
    slug = mint_slug("subtask", name)
    cursor = conn.execute(
        """
        INSERT INTO subtasks (slug, parent_task_id, name, status, priority, description)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (slug, parent_task_id, name, status, priority, description)
    )
    conn.commit()
    return cursor.lastrowid


def _query_incomplete_subtasks(
    conn: sqlite3.Connection
) -> Tuple[SubtaskRecord, ...]:
    """
    Effect: Query all incomplete subtasks.

    Args:
        conn: Database connection

    Returns:
        Tuple of subtask records
    """
    cursor = conn.execute(
        "SELECT * FROM subtasks WHERE status != 'completed'"
    )
    rows = cursor.fetchall()
    return tuple(
        SubtaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            parent_task_id=row['parent_task_id'],
            name=row['name'],
            status=row['status'],
            priority=row['priority'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_incomplete_subtasks_by_task(
    conn: sqlite3.Connection,
    task_id: int
) -> Tuple[SubtaskRecord, ...]:
    """
    Effect: Query incomplete subtasks by task ID.

    Args:
        conn: Database connection
        task_id: Task ID

    Returns:
        Tuple of subtask records
    """
    cursor = conn.execute(
        "SELECT * FROM subtasks WHERE parent_task_id = ? AND status != 'completed'",
        (task_id,)
    )
    rows = cursor.fetchall()
    return tuple(
        SubtaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            parent_task_id=row['parent_task_id'],
            name=row['name'],
            status=row['status'],
            priority=row['priority'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_subtasks_by_task(
    conn: sqlite3.Connection,
    task_id: int,
    status: Optional[str]
) -> Tuple[SubtaskRecord, ...]:
    """
    Effect: Query subtasks by task ID, optionally filtered by status.

    Args:
        conn: Database connection
        task_id: Task ID
        status: Optional status filter

    Returns:
        Tuple of subtask records
    """
    if status is None:
        cursor = conn.execute(
            "SELECT * FROM subtasks WHERE parent_task_id = ?",
            (task_id,)
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM subtasks WHERE parent_task_id = ? AND status = ?",
            (task_id, status)
        )

    rows = cursor.fetchall()
    return tuple(
        SubtaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            parent_task_id=row['parent_task_id'],
            name=row['name'],
            status=row['status'],
            priority=row['priority'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_subtasks_comprehensive(
    conn: sqlite3.Connection,
    status: Optional[str],
    limit: Optional[int],
    date_range_created: Optional[List[str]],
    date_range_updated: Optional[List[str]],
    task_id: Optional[int],
    priority: Optional[str]
) -> Tuple[SubtaskRecord, ...]:
    """
    Effect: Query subtasks with multiple filters.

    Args:
        conn: Database connection
        status: Optional status filter
        limit: Optional result limit
        date_range_created: Optional created date range [start, end]
        date_range_updated: Optional updated date range [start, end]
        task_id: Optional task filter
        priority: Optional priority filter

    Returns:
        Tuple of subtask records
    """
    # Build dynamic query
    where_clauses = []
    values = []

    if status is not None:
        where_clauses.append("status = ?")
        values.append(status)

    if task_id is not None:
        where_clauses.append("parent_task_id = ?")
        values.append(task_id)

    if priority is not None:
        where_clauses.append("priority = ?")
        values.append(priority)

    if date_range_created is not None and len(date_range_created) == 2:
        where_clauses.append("created_at BETWEEN ? AND ?")
        values.extend(date_range_created)

    if date_range_updated is not None and len(date_range_updated) == 2:
        where_clauses.append("updated_at BETWEEN ? AND ?")
        values.extend(date_range_updated)

    # Build final query
    query = "SELECT * FROM subtasks"
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    if limit is not None:
        query += f" LIMIT {limit}"

    cursor = conn.execute(query, values)
    rows = cursor.fetchall()
    return tuple(
        SubtaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            parent_task_id=row['parent_task_id'],
            name=row['name'],
            status=row['status'],
            priority=row['priority'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _update_subtask_fields(
    conn: sqlite3.Connection,
    subtask_id: int,
    name: Optional[str],
    task_id: Optional[int],
    status: Optional[str],
    description: Optional[str],
    priority: Optional[str]
) -> None:
    """
    Effect: Update subtask fields (only non-None values).

    Args:
        conn: Database connection
        subtask_id: Subtask ID
        name: New name (None = don't update)
        task_id: New task ID (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)
        priority: New priority (None = don't update)
    """
    # Build dynamic UPDATE query
    fields = []
    values = []

    if name is not None:
        fields.append("name = ?")
        values.append(name)

    if task_id is not None:
        fields.append("parent_task_id = ?")
        values.append(task_id)

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if description is not None:
        fields.append("description = ?")
        values.append(description)

    if priority is not None:
        fields.append("priority = ?")
        values.append(priority)

    if not fields:
        return  # Nothing to update

    # Add subtask_id to values
    values.append(subtask_id)

    # Execute update
    query = f"UPDATE subtasks SET {', '.join(fields)} WHERE id = ?"
    conn.execute(query, values)
    conn.commit()


def _delete_subtask(conn: sqlite3.Connection, subtask_id: int) -> None:
    """
    Effect: Delete subtask from database.

    Args:
        conn: Database connection
        subtask_id: Subtask ID
    """
    conn.execute("DELETE FROM subtasks WHERE id = ?", (subtask_id,))
    conn.commit()


# ============================================================================
# Effect Functions - Sidequest Operations
# ============================================================================

def _insert_sidequest(
    conn: sqlite3.Connection,
    paused_task_id: int,
    paused_subtask_id: Optional[int],
    name: str,
    status: str,
    priority: str,
    description: Optional[str],
    flow_ids: Optional[List[int]]
) -> int:
    """
    Effect: Insert sidequest into database.

    Args:
        conn: Database connection
        paused_task_id: Paused task ID
        paused_subtask_id: Optional paused subtask ID
        name: Sidequest name
        status: Sidequest status
        priority: Sidequest priority
        description: Optional description
        flow_ids: Optional flow IDs (JSON array)

    Returns:
        New sidequest ID
    """
    # Convert flow_ids to JSON string
    flow_ids_json = json.dumps(flow_ids) if flow_ids is not None else None

    slug = mint_slug("sidequest", name)
    cursor = conn.execute(
        """
        INSERT INTO sidequests (slug, paused_task_id, paused_subtask_id, name, status, priority, description, flow_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (slug, paused_task_id, paused_subtask_id, name, status, priority, description, flow_ids_json)
    )
    conn.commit()
    return cursor.lastrowid


def _query_incomplete_sidequests(
    conn: sqlite3.Connection
) -> Tuple[SidequestRecord, ...]:
    """
    Effect: Query all incomplete sidequests.

    Args:
        conn: Database connection

    Returns:
        Tuple of sidequest records
    """
    cursor = conn.execute(
        "SELECT * FROM sidequests WHERE status != 'completed'"
    )
    rows = cursor.fetchall()
    return tuple(
        SidequestRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            paused_task_id=row['paused_task_id'],
            paused_subtask_id=row['paused_subtask_id'],
            name=row['name'],
            status=row['status'],
            priority=row['priority'],
            description=row['description'],
            flow_ids=row['flow_ids'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_sidequests_comprehensive(
    conn: sqlite3.Connection,
    status: Optional[str],
    limit: Optional[int],
    date_range_created: Optional[List[str]],
    date_range_updated: Optional[List[str]],
    task_id: Optional[int],
    subtask_id: Optional[int],
    priority: Optional[str]
) -> Tuple[SidequestRecord, ...]:
    """
    Effect: Query sidequests with multiple filters.

    Args:
        conn: Database connection
        status: Optional status filter
        limit: Optional result limit
        date_range_created: Optional created date range [start, end]
        date_range_updated: Optional updated date range [start, end]
        task_id: Optional paused task filter
        subtask_id: Optional paused subtask filter
        priority: Optional priority filter

    Returns:
        Tuple of sidequest records
    """
    # Build dynamic query
    where_clauses = []
    values = []

    if status is not None:
        where_clauses.append("status = ?")
        values.append(status)

    if task_id is not None:
        where_clauses.append("paused_task_id = ?")
        values.append(task_id)

    if subtask_id is not None:
        where_clauses.append("paused_subtask_id = ?")
        values.append(subtask_id)

    if priority is not None:
        where_clauses.append("priority = ?")
        values.append(priority)

    if date_range_created is not None and len(date_range_created) == 2:
        where_clauses.append("created_at BETWEEN ? AND ?")
        values.extend(date_range_created)

    if date_range_updated is not None and len(date_range_updated) == 2:
        where_clauses.append("updated_at BETWEEN ? AND ?")
        values.extend(date_range_updated)

    # Build final query
    query = "SELECT * FROM sidequests"
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    if limit is not None:
        query += f" LIMIT {limit}"

    cursor = conn.execute(query, values)
    rows = cursor.fetchall()
    return tuple(
        SidequestRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            paused_task_id=row['paused_task_id'],
            paused_subtask_id=row['paused_subtask_id'],
            name=row['name'],
            status=row['status'],
            priority=row['priority'],
            description=row['description'],
            flow_ids=row['flow_ids'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_sidequest_flow_ids(
    conn: sqlite3.Connection,
    sidequest_id: int
) -> Optional[List[int]]:
    """
    Effect: Query flow IDs for a sidequest.

    Args:
        conn: Database connection
        sidequest_id: Sidequest ID

    Returns:
        List of flow IDs or None
    """
    cursor = conn.execute(
        "SELECT flow_ids FROM sidequests WHERE id = ?",
        (sidequest_id,)
    )
    row = cursor.fetchone()

    if row is None or row['flow_ids'] is None:
        return None

    # Parse JSON array
    return json.loads(row['flow_ids'])


def _query_files_by_flow_ids(
    conn: sqlite3.Connection,
    flow_ids: List[int]
) -> Tuple[FileRecord, ...]:
    """
    Effect: Query files by flow IDs.

    Args:
        conn: Database connection
        flow_ids: List of flow IDs

    Returns:
        Tuple of file records
    """
    if not flow_ids:
        return ()

    # Build query with placeholders
    placeholders = ','.join('?' * len(flow_ids))
    query = f"""
        SELECT DISTINCT f.id, f.name, f.path, f.language
        FROM files f
        JOIN file_flows ff ON f.id = ff.file_id
        WHERE ff.flow_id IN ({placeholders})
    """

    cursor = conn.execute(query, flow_ids)
    rows = cursor.fetchall()
    return tuple(
        FileRecord(
            id=row['id'],
            name=row['name'],
            path=row['path'],
            language=row['language']
        )
        for row in rows
    )


def _update_sidequest_fields(
    conn: sqlite3.Connection,
    sidequest_id: int,
    name: Optional[str],
    paused_task_id: Optional[int],
    paused_subtask_id: Optional[int],
    status: Optional[str],
    description: Optional[str],
    flow_ids: Optional[List[int]],
    priority: Optional[str]
) -> None:
    """
    Effect: Update sidequest fields (only non-None values).

    Args:
        conn: Database connection
        sidequest_id: Sidequest ID
        name: New name (None = don't update)
        paused_task_id: New paused task ID (None = don't update)
        paused_subtask_id: New paused subtask ID (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)
        flow_ids: New flow IDs (None = don't update)
        priority: New priority (None = don't update)
    """
    # Build dynamic UPDATE query
    fields = []
    values = []

    if name is not None:
        fields.append("name = ?")
        values.append(name)

    if paused_task_id is not None:
        fields.append("paused_task_id = ?")
        values.append(paused_task_id)

    if paused_subtask_id is not None:
        fields.append("paused_subtask_id = ?")
        values.append(paused_subtask_id)

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if description is not None:
        fields.append("description = ?")
        values.append(description)

    if flow_ids is not None:
        fields.append("flow_ids = ?")
        values.append(json.dumps(flow_ids))

    if priority is not None:
        fields.append("priority = ?")
        values.append(priority)

    if not fields:
        return  # Nothing to update

    # Add sidequest_id to values
    values.append(sidequest_id)

    # Execute update
    query = f"UPDATE sidequests SET {', '.join(fields)} WHERE id = ?"
    conn.execute(query, values)
    conn.commit()


def _delete_sidequest(conn: sqlite3.Connection, sidequest_id: int) -> None:
    """
    Effect: Delete sidequest from database.

    Args:
        conn: Database connection
        sidequest_id: Sidequest ID
    """
    conn.execute("DELETE FROM sidequests WHERE id = ?", (sidequest_id,))
    conn.commit()


# ============================================================================
# Public Helper Functions - Subtasks
# ============================================================================

def add_subtask(
    parent_task_id: int,
    name: str,
    status: str = "pending",
    priority: str = "high",
    description: Optional[str] = None
) -> AddResult:
    """
    Add subtask to task.

    Args:
        parent_task_id: Parent task ID
        name: Subtask name
        status: Subtask status ('pending', 'in_progress', 'completed', 'blocked')
        priority: Subtask priority ('low', 'medium', 'high', 'critical')
        description: Optional subtask description

    Returns:
        AddResult with new subtask ID on success
    """
    # Validate status
    if not _validate_status(status, VALID_TASK_STATUSES):
        return AddResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    # Validate priority
    if not _validate_priority(priority):
        return AddResult(
            success=False,
            error=f"Invalid priority: {priority}. Must be one of: {', '.join(VALID_PRIORITY_LEVELS)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check parent task exists
        if not _check_entity_exists(conn, "tasks", parent_task_id):
            conn.close()
            return AddResult(
                success=False,
                error=f"Task ID {parent_task_id} not found"
            )

        # Insert subtask
        subtask_id = _insert_subtask(conn, parent_task_id, name, status, priority, description)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("add_subtask")

        return AddResult(
            success=True,
            id=subtask_id,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return AddResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_incomplete_subtasks() -> SubtaskQueryResult:
    """
    Get all non-completed subtasks.

    Returns:
        SubtaskQueryResult with incomplete subtasks
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        subtasks = _query_incomplete_subtasks(conn)
        conn.close()

        return SubtaskQueryResult(
            success=True,
            subtasks=subtasks
        )

    except Exception as e:
        conn.close()
        return SubtaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_incomplete_subtasks_by_task(
    task_id: int
) -> SubtaskQueryResult:
    """
    Get incomplete subtasks for specific task.

    Args:
        task_id: Task ID

    Returns:
        SubtaskQueryResult with incomplete subtasks for task
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        subtasks = _query_incomplete_subtasks_by_task(conn, task_id)
        conn.close()

        return SubtaskQueryResult(
            success=True,
            subtasks=subtasks
        )

    except Exception as e:
        conn.close()
        return SubtaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_subtasks_by_task(
    task_id: int,
    status: Optional[str] = None
) -> SubtaskQueryResult:
    """
    Get subtasks for task, optionally filtered by status.

    Args:
        task_id: Task ID
        status: Optional status filter ('pending', 'in_progress', 'completed', 'blocked')

    Returns:
        SubtaskQueryResult with subtasks for task
    """
    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_TASK_STATUSES):
        return SubtaskQueryResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        subtasks = _query_subtasks_by_task(conn, task_id, status)
        conn.close()

        return SubtaskQueryResult(
            success=True,
            subtasks=subtasks
        )

    except Exception as e:
        conn.close()
        return SubtaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_subtasks_comprehensive(
    status: Optional[str] = None,
    limit: Optional[int] = None,
    date_range_created: Optional[List[str]] = None,
    date_range_updated: Optional[List[str]] = None,
    task_id: Optional[int] = None,
    priority: Optional[str] = None
) -> SubtaskQueryResult:
    """
    Advanced subtask search with multiple filters.

    Args:
        status: Optional status filter ('pending', 'in_progress', 'completed', 'blocked')
        limit: Optional maximum results
        date_range_created: Optional created date range [start_date, end_date]
        date_range_updated: Optional updated date range [start_date, end_date]
        task_id: Optional task filter
        priority: Optional priority filter ('low', 'medium', 'high', 'critical')

    Returns:
        SubtaskQueryResult with filtered subtasks
    """
    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_TASK_STATUSES):
        return SubtaskQueryResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    # Validate priority if provided
    if priority is not None and not _validate_priority(priority):
        return SubtaskQueryResult(
            success=False,
            error=f"Invalid priority: {priority}. Must be one of: {', '.join(VALID_PRIORITY_LEVELS)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        subtasks = _query_subtasks_comprehensive(
            conn, status, limit, date_range_created, date_range_updated, task_id, priority
        )
        conn.close()

        return SubtaskQueryResult(
            success=True,
            subtasks=subtasks
        )

    except Exception as e:
        conn.close()
        return SubtaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_subtask(
    id: int,
    name: Optional[str] = None,
    task_id: Optional[int] = None,
    status: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[str] = None
) -> UpdateResult:
    """
    Update subtask metadata.

    Args:
        id: Subtask ID
        name: New name (None = don't update)
        task_id: New task ID (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)
        priority: New priority (None = don't update)

    Returns:
        UpdateResult with success status
    """
    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_TASK_STATUSES):
        return UpdateResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    # Validate priority if provided
    if priority is not None and not _validate_priority(priority):
        return UpdateResult(
            success=False,
            error=f"Invalid priority: {priority}. Must be one of: {', '.join(VALID_PRIORITY_LEVELS)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check subtask exists
        if not _check_entity_exists(conn, "subtasks", id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Subtask ID {id} not found"
            )

        # Check new task exists if provided
        if task_id is not None and not _check_entity_exists(conn, "tasks", task_id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Task ID {task_id} not found"
            )

        # Update subtask
        _update_subtask_fields(conn, id, name, task_id, status, description, priority)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_subtask")

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


def delete_subtask(
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion"
) -> DeleteResult:
    """
    Delete subtask with item validation.

    Args:
        id: Subtask ID
        note_reason: Deletion reason
        note_severity: Note severity ('info', 'warning', 'error')
        note_source: Note source ('ai' or 'user')
        note_type: Note type ('entry_deletion')

    Returns:
        DeleteResult with success status
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check subtask exists
        if not _check_entity_exists(conn, "subtasks", id):
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Subtask ID {id} not found"
            )

        # Check for incomplete items
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM items WHERE reference_table = 'subtasks' AND reference_id = ? AND status != 'completed'",
            (id,)
        )
        row = cursor.fetchone()
        incomplete_count = row['count']

        if incomplete_count > 0:
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Cannot delete subtask: {incomplete_count} incomplete item(s) exist"
            )

        # Create audit note
        _create_deletion_note(conn, "subtasks", id, note_reason, note_severity, note_source, note_type)

        # Delete subtask
        _delete_subtask(conn, id)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("delete_subtask")

        return DeleteResult(
            success=True,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return DeleteResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# Public Helper Functions - Sidequests
# ============================================================================

def add_sidequest(
    paused_task_id: int,
    name: str,
    status: str = "pending",
    priority: str = "critical",
    description: Optional[str] = None,
    paused_subtask_id: Optional[int] = None,
    flow_ids: Optional[List[int]] = None
) -> AddResult:
    """
    Add sidequest (urgent interruption).

    Args:
        paused_task_id: Paused task ID
        name: Sidequest name
        status: Sidequest status ('pending', 'in_progress', 'completed', 'blocked')
        priority: Sidequest priority ('low', 'medium', 'high', 'critical')
        description: Optional sidequest description
        paused_subtask_id: Optional paused subtask ID (if sidequest during subtask)
        flow_ids: Optional JSON array of flow IDs

    Returns:
        AddResult with new sidequest ID on success
    """
    # Validate status
    if not _validate_status(status, VALID_TASK_STATUSES):
        return AddResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    # Validate priority
    if not _validate_priority(priority):
        return AddResult(
            success=False,
            error=f"Invalid priority: {priority}. Must be one of: {', '.join(VALID_PRIORITY_LEVELS)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check paused task exists
        if not _check_entity_exists(conn, "tasks", paused_task_id):
            conn.close()
            return AddResult(
                success=False,
                error=f"Task ID {paused_task_id} not found"
            )

        # Check paused subtask exists if provided
        if paused_subtask_id is not None and not _check_entity_exists(conn, "subtasks", paused_subtask_id):
            conn.close()
            return AddResult(
                success=False,
                error=f"Subtask ID {paused_subtask_id} not found"
            )

        # Insert sidequest
        sidequest_id = _insert_sidequest(
            conn, paused_task_id, paused_subtask_id, name, status, priority, description, flow_ids
        )
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("add_sidequest")

        return AddResult(
            success=True,
            id=sidequest_id,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return AddResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_incomplete_sidequests() -> SidequestQueryResult:
    """
    Get all non-completed sidequests.

    Returns:
        SidequestQueryResult with incomplete sidequests
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        sidequests = _query_incomplete_sidequests(conn)
        conn.close()

        return SidequestQueryResult(
            success=True,
            sidequests=sidequests
        )

    except Exception as e:
        conn.close()
        return SidequestQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_sidequests_comprehensive(
    status: Optional[str] = None,
    limit: Optional[int] = None,
    date_range_created: Optional[List[str]] = None,
    date_range_updated: Optional[List[str]] = None,
    task_id: Optional[int] = None,
    subtask_id: Optional[int] = None,
    priority: Optional[str] = None
) -> SidequestQueryResult:
    """
    Advanced sidequest search with multiple filters.

    Args:
        status: Optional status filter ('pending', 'in_progress', 'completed', 'blocked')
        limit: Optional maximum results
        date_range_created: Optional created date range [start_date, end_date]
        date_range_updated: Optional updated date range [start_date, end_date]
        task_id: Optional paused task filter
        subtask_id: Optional paused subtask filter
        priority: Optional priority filter ('low', 'medium', 'high', 'critical')

    Returns:
        SidequestQueryResult with filtered sidequests
    """
    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_TASK_STATUSES):
        return SidequestQueryResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    # Validate priority if provided
    if priority is not None and not _validate_priority(priority):
        return SidequestQueryResult(
            success=False,
            error=f"Invalid priority: {priority}. Must be one of: {', '.join(VALID_PRIORITY_LEVELS)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        sidequests = _query_sidequests_comprehensive(
            conn, status, limit, date_range_created, date_range_updated, task_id, subtask_id, priority
        )
        conn.close()

        return SidequestQueryResult(
            success=True,
            sidequests=sidequests
        )

    except Exception as e:
        conn.close()
        return SidequestQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_sidequest_flows(
    sidequest_id: int
) -> FlowIdsResult:
    """
    Get flow IDs for a sidequest.

    Args:
        sidequest_id: Sidequest ID

    Returns:
        FlowIdsResult with flow IDs array
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check sidequest exists
        if not _check_entity_exists(conn, "sidequests", sidequest_id):
            conn.close()
            return FlowIdsResult(
                success=False,
                error=f"Sidequest ID {sidequest_id} not found"
            )

        # Query flow IDs
        flow_ids = _query_sidequest_flow_ids(conn, sidequest_id)
        conn.close()

        return FlowIdsResult(
            success=True,
            flow_ids=tuple(flow_ids) if flow_ids is not None else ()
        )

    except Exception as e:
        conn.close()
        return FlowIdsResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_sidequest_files(
    sidequest_id: int
) -> FilesResult:
    """
    Get all files related to sidequest via flows (orchestrator).

    Args:
        sidequest_id: Sidequest ID

    Returns:
        FilesResult with related files
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check sidequest exists
        if not _check_entity_exists(conn, "sidequests", sidequest_id):
            conn.close()
            return FilesResult(
                success=False,
                error=f"Sidequest ID {sidequest_id} not found"
            )

        # Query flow IDs for sidequest
        flow_ids = _query_sidequest_flow_ids(conn, sidequest_id)

        if flow_ids is None or not flow_ids:
            conn.close()
            return FilesResult(
                success=True,
                files=()
            )

        # Query files by flow IDs
        files = _query_files_by_flow_ids(conn, flow_ids)
        conn.close()

        return FilesResult(
            success=True,
            files=files
        )

    except Exception as e:
        conn.close()
        return FilesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_sidequest(
    id: int,
    name: Optional[str] = None,
    paused_task_id: Optional[int] = None,
    paused_subtask_id: Optional[int] = None,
    status: Optional[str] = None,
    description: Optional[str] = None,
    flow_ids: Optional[List[int]] = None,
    priority: Optional[str] = None
) -> UpdateResult:
    """
    Update sidequest metadata.

    Args:
        id: Sidequest ID
        name: New name (None = don't update)
        paused_task_id: New paused task ID (None = don't update)
        paused_subtask_id: New paused subtask ID (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)
        flow_ids: New flow IDs array (None = don't update)
        priority: New priority (None = don't update)

    Returns:
        UpdateResult with success status
    """
    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_TASK_STATUSES):
        return UpdateResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    # Validate priority if provided
    if priority is not None and not _validate_priority(priority):
        return UpdateResult(
            success=False,
            error=f"Invalid priority: {priority}. Must be one of: {', '.join(VALID_PRIORITY_LEVELS)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check sidequest exists
        if not _check_entity_exists(conn, "sidequests", id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Sidequest ID {id} not found"
            )

        # Check new task exists if provided
        if paused_task_id is not None and not _check_entity_exists(conn, "tasks", paused_task_id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Task ID {paused_task_id} not found"
            )

        # Check new subtask exists if provided
        if paused_subtask_id is not None and not _check_entity_exists(conn, "subtasks", paused_subtask_id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Subtask ID {paused_subtask_id} not found"
            )

        # Update sidequest
        _update_sidequest_fields(
            conn, id, name, paused_task_id, paused_subtask_id, status, description, flow_ids, priority
        )
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_sidequest")

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


def delete_sidequest(
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion"
) -> DeleteResult:
    """
    Delete sidequest with item validation.

    Args:
        id: Sidequest ID
        note_reason: Deletion reason
        note_severity: Note severity ('info', 'warning', 'error')
        note_source: Note source ('ai' or 'user')
        note_type: Note type ('entry_deletion')

    Returns:
        DeleteResult with success status
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check sidequest exists
        if not _check_entity_exists(conn, "sidequests", id):
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Sidequest ID {id} not found"
            )

        # Check for incomplete items
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM items WHERE reference_table = 'sidequests' AND reference_id = ? AND status != 'completed'",
            (id,)
        )
        row = cursor.fetchone()
        incomplete_count = row['count']

        if incomplete_count > 0:
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Cannot delete sidequest: {incomplete_count} incomplete item(s) exist"
            )

        # Create audit note
        _create_deletion_note(conn, "sidequests", id, note_reason, note_severity, note_source, note_type)

        # Delete sidequest
        _delete_sidequest(conn, id)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("delete_sidequest")

        return DeleteResult(
            success=True,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return DeleteResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
