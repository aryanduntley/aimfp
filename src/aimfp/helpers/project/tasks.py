"""
AIMFP Helper Functions - Project Tasks

Task and milestone management operations for project database.
Implements CRUD and query operations for completion paths, milestones, and tasks.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- add_milestone: Add milestone to completion path
- get_milestones_by_path: Get all milestones for a completion path
- get_milestones_by_status: Get milestones filtered by status
- get_incomplete_milestones: Get all non-completed milestones
- update_milestone: Update milestone metadata
- delete_milestone: Delete milestone with task validation
- add_task: Add task to milestone
- get_incomplete_tasks_by_milestone: Get open tasks for a milestone with related subtasks/sidequests
- get_incomplete_tasks: Get all incomplete tasks with subtasks/sidequests
- get_tasks_by_milestone: Get all tasks for a milestone (any status)
- get_tasks_comprehensive: Advanced task search with multiple filters
- get_task_flows: Get flow IDs for a task
- get_task_files: Get all files related to task via flows (orchestrator)
- update_task: Update task metadata
- delete_task: Delete task with item validation
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
    VALID_MILESTONE_STATUSES,
    VALID_PRIORITY_LEVELS
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class MilestoneRecord:
    """Immutable milestone record from database."""
    id: int
    completion_path_id: int
    name: str
    status: str
    description: Optional[str]
    created_at: str
    updated_at: str
    slug: Optional[str] = None  # stable cross-clone identity (see helpers/shared/slugs.py)


@dataclass(frozen=True)
class TaskRecord:
    """Immutable task record from database."""
    id: int
    milestone_id: int
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
class MilestoneQueryResult:
    """Result of milestone query operation."""
    success: bool
    milestones: Tuple[MilestoneRecord, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class TaskQueryResult:
    """Result of task query operation."""
    success: bool
    tasks: Tuple[TaskRecord, ...] = ()
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
# Effect Functions - Database Operations
# ============================================================================

def _insert_milestone(
    conn: sqlite3.Connection,
    completion_path_id: int,
    name: str,
    status: str,
    description: Optional[str]
) -> int:
    """
    Effect: Insert milestone into database.

    Args:
        conn: Database connection
        completion_path_id: Completion path ID
        name: Milestone name
        status: Milestone status
        description: Optional description

    Returns:
        New milestone ID
    """
    slug = mint_slug("milestone", name)
    cursor = conn.execute(
        """
        INSERT INTO milestones (slug, completion_path_id, name, status, description)
        VALUES (?, ?, ?, ?, ?)
        """,
        (slug, completion_path_id, name, status, description)
    )
    conn.commit()
    return cursor.lastrowid


def _query_milestones_by_path(
    conn: sqlite3.Connection,
    completion_path_id: int
) -> Tuple[MilestoneRecord, ...]:
    """
    Effect: Query milestones by completion path ID.

    Args:
        conn: Database connection
        completion_path_id: Completion path ID

    Returns:
        Tuple of milestone records
    """
    cursor = conn.execute(
        "SELECT * FROM milestones WHERE completion_path_id = ?",
        (completion_path_id,)
    )
    rows = cursor.fetchall()
    return tuple(
        MilestoneRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            completion_path_id=row['completion_path_id'],
            name=row['name'],
            status=row['status'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_milestones_by_status(
    conn: sqlite3.Connection,
    status: str
) -> Tuple[MilestoneRecord, ...]:
    """
    Effect: Query milestones by status.

    Args:
        conn: Database connection
        status: Milestone status

    Returns:
        Tuple of milestone records
    """
    cursor = conn.execute(
        "SELECT * FROM milestones WHERE status = ?",
        (status,)
    )
    rows = cursor.fetchall()
    return tuple(
        MilestoneRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            completion_path_id=row['completion_path_id'],
            name=row['name'],
            status=row['status'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _query_incomplete_milestones(
    conn: sqlite3.Connection
) -> Tuple[MilestoneRecord, ...]:
    """
    Effect: Query all incomplete milestones.

    Args:
        conn: Database connection

    Returns:
        Tuple of milestone records
    """
    cursor = conn.execute(
        "SELECT * FROM milestones WHERE status != 'completed'"
    )
    rows = cursor.fetchall()
    return tuple(
        MilestoneRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            completion_path_id=row['completion_path_id'],
            name=row['name'],
            status=row['status'],
            description=row['description'],
            created_at=row['created_at'],
            updated_at=row['updated_at']
        )
        for row in rows
    )


def _update_milestone_fields(
    conn: sqlite3.Connection,
    milestone_id: int,
    name: Optional[str],
    completion_path_id: Optional[int],
    status: Optional[str],
    description: Optional[str]
) -> None:
    """
    Effect: Update milestone fields (only non-None values).

    Args:
        conn: Database connection
        milestone_id: Milestone ID
        name: New name (None = don't update)
        completion_path_id: New completion path ID (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)
    """
    # Build dynamic UPDATE query
    fields = []
    values = []

    if name is not None:
        fields.append("name = ?")
        values.append(name)

    if completion_path_id is not None:
        fields.append("completion_path_id = ?")
        values.append(completion_path_id)

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if description is not None:
        fields.append("description = ?")
        values.append(description)

    if not fields:
        return  # Nothing to update

    # Add milestone_id to values
    values.append(milestone_id)

    # Execute update
    query = f"UPDATE milestones SET {', '.join(fields)} WHERE id = ?"
    conn.execute(query, values)
    conn.commit()


def _delete_milestone(conn: sqlite3.Connection, milestone_id: int) -> None:
    """
    Effect: Delete milestone from database.

    Args:
        conn: Database connection
        milestone_id: Milestone ID
    """
    conn.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
    conn.commit()


def _insert_task(
    conn: sqlite3.Connection,
    milestone_id: int,
    name: str,
    status: str,
    priority: str,
    description: Optional[str],
    flow_ids: Optional[List[int]]
) -> int:
    """
    Effect: Insert task into database.

    Args:
        conn: Database connection
        milestone_id: Milestone ID
        name: Task name
        status: Task status
        priority: Task priority
        description: Optional description
        flow_ids: Optional flow IDs (JSON array)

    Returns:
        New task ID
    """
    # Convert flow_ids to JSON string
    flow_ids_json = json.dumps(flow_ids) if flow_ids is not None else None

    slug = mint_slug("task", name)
    cursor = conn.execute(
        """
        INSERT INTO tasks (slug, milestone_id, name, status, priority, description, flow_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (slug, milestone_id, name, status, priority, description, flow_ids_json)
    )
    conn.commit()
    return cursor.lastrowid


def _query_tasks_by_milestone(
    conn: sqlite3.Connection,
    milestone_id: int
) -> Tuple[TaskRecord, ...]:
    """
    Effect: Query tasks by milestone ID.

    Args:
        conn: Database connection
        milestone_id: Milestone ID

    Returns:
        Tuple of task records
    """
    cursor = conn.execute(
        "SELECT * FROM tasks WHERE milestone_id = ?",
        (milestone_id,)
    )
    rows = cursor.fetchall()
    return tuple(
        TaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            milestone_id=row['milestone_id'],
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


def _query_incomplete_tasks_by_milestone(
    conn: sqlite3.Connection,
    milestone_id: int,
    skip_pending: bool
) -> Tuple[TaskRecord, ...]:
    """
    Effect: Query incomplete tasks by milestone ID.

    Args:
        conn: Database connection
        milestone_id: Milestone ID
        skip_pending: If true, excludes pending tasks

    Returns:
        Tuple of task records
    """
    if skip_pending:
        cursor = conn.execute(
            "SELECT * FROM tasks WHERE milestone_id = ? AND status IN ('in_progress', 'blocked')",
            (milestone_id,)
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM tasks WHERE milestone_id = ? AND status != 'completed'",
            (milestone_id,)
        )

    rows = cursor.fetchall()
    return tuple(
        TaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            milestone_id=row['milestone_id'],
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


def _query_incomplete_tasks(
    conn: sqlite3.Connection
) -> Tuple[TaskRecord, ...]:
    """
    Effect: Query all incomplete tasks.

    Args:
        conn: Database connection

    Returns:
        Tuple of task records
    """
    cursor = conn.execute(
        "SELECT * FROM tasks WHERE status != 'completed'"
    )
    rows = cursor.fetchall()
    return tuple(
        TaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            milestone_id=row['milestone_id'],
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


def _query_tasks_comprehensive(
    conn: sqlite3.Connection,
    status: Optional[str],
    limit: Optional[int],
    date_range_created: Optional[List[str]],
    date_range_updated: Optional[List[str]],
    milestone_id: Optional[int],
    priority: Optional[str]
) -> Tuple[TaskRecord, ...]:
    """
    Effect: Query tasks with multiple filters.

    Args:
        conn: Database connection
        status: Optional status filter
        limit: Optional result limit
        date_range_created: Optional created date range [start, end]
        date_range_updated: Optional updated date range [start, end]
        milestone_id: Optional milestone filter
        priority: Optional priority filter

    Returns:
        Tuple of task records
    """
    # Build dynamic query
    where_clauses = []
    values = []

    if status is not None:
        where_clauses.append("status = ?")
        values.append(status)

    if milestone_id is not None:
        where_clauses.append("milestone_id = ?")
        values.append(milestone_id)

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
    query = "SELECT * FROM tasks"
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    if limit is not None:
        query += f" LIMIT {limit}"

    cursor = conn.execute(query, values)
    rows = cursor.fetchall()
    return tuple(
        TaskRecord(
            id=row['id'],
            slug=row['slug'] if 'slug' in row.keys() else None,
            milestone_id=row['milestone_id'],
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


def _query_task_flow_ids(
    conn: sqlite3.Connection,
    task_id: int
) -> Optional[List[int]]:
    """
    Effect: Query flow IDs for a task.

    Args:
        conn: Database connection
        task_id: Task ID

    Returns:
        List of flow IDs or None
    """
    cursor = conn.execute(
        "SELECT flow_ids FROM tasks WHERE id = ?",
        (task_id,)
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


def _update_task_fields(
    conn: sqlite3.Connection,
    task_id: int,
    name: Optional[str],
    milestone_id: Optional[int],
    status: Optional[str],
    description: Optional[str],
    flow_ids: Optional[List[int]],
    priority: Optional[str]
) -> None:
    """
    Effect: Update task fields (only non-None values).

    Args:
        conn: Database connection
        task_id: Task ID
        name: New name (None = don't update)
        milestone_id: New milestone ID (None = don't update)
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

    if milestone_id is not None:
        fields.append("milestone_id = ?")
        values.append(milestone_id)

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

    # Add task_id to values
    values.append(task_id)

    # Execute update
    query = f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?"
    conn.execute(query, values)
    conn.commit()


def _delete_task(conn: sqlite3.Connection, task_id: int) -> None:
    """
    Effect: Delete task from database.

    Args:
        conn: Database connection
        task_id: Task ID
    """
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()


# ============================================================================
# Public Helper Functions
# ============================================================================

def add_milestone(
    completion_path_id: int,
    name: str,
    status: str = "pending",
    description: Optional[str] = None
) -> AddResult:
    """
    Add milestone to completion path.

    Args:
        completion_path_id: Completion path ID this milestone belongs to
        name: Milestone name
        status: Milestone status ('pending', 'in_progress', 'completed', 'blocked')
        description: Optional milestone description

    Returns:
        AddResult with new milestone ID on success
    """
    # Validate status
    if not _validate_status(status, VALID_MILESTONE_STATUSES):
        return AddResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_MILESTONE_STATUSES)}"
        )

    # Open connection and insert
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check completion path exists
        if not _check_entity_exists(conn, "completion_path", completion_path_id):
            conn.close()
            return AddResult(
                success=False,
                error=f"Completion path ID {completion_path_id} not found"
            )

        # Insert milestone
        milestone_id = _insert_milestone(conn, completion_path_id, name, status, description)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("add_milestone")

        return AddResult(
            success=True,
            id=milestone_id,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return AddResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_milestones_by_path(
    completion_path_id: int
) -> MilestoneQueryResult:
    """
    Get all milestones for a completion path.

    Args:
        completion_path_id: Completion path ID

    Returns:
        MilestoneQueryResult with milestones
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        milestones = _query_milestones_by_path(conn, completion_path_id)
        conn.close()

        return MilestoneQueryResult(
            success=True,
            milestones=milestones
        )

    except Exception as e:
        conn.close()
        return MilestoneQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_milestones_by_status(
    status: str
) -> MilestoneQueryResult:
    """
    Get milestones filtered by status.

    Args:
        status: Milestone status ('pending', 'in_progress', 'completed', 'blocked')

    Returns:
        MilestoneQueryResult with filtered milestones
    """
    # Validate status
    if not _validate_status(status, VALID_MILESTONE_STATUSES):
        return MilestoneQueryResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_MILESTONE_STATUSES)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        milestones = _query_milestones_by_status(conn, status)
        conn.close()

        return MilestoneQueryResult(
            success=True,
            milestones=milestones
        )

    except Exception as e:
        conn.close()
        return MilestoneQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_incomplete_milestones() -> MilestoneQueryResult:
    """
    Get all non-completed milestones.

    Returns:
        MilestoneQueryResult with incomplete milestones
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        milestones = _query_incomplete_milestones(conn)
        conn.close()

        return MilestoneQueryResult(
            success=True,
            milestones=milestones
        )

    except Exception as e:
        conn.close()
        return MilestoneQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_milestone(
    id: int,
    name: Optional[str] = None,
    completion_path_id: Optional[int] = None,
    status: Optional[str] = None,
    description: Optional[str] = None
) -> UpdateResult:
    """
    Update milestone metadata.

    Args:
        id: Milestone ID
        name: New name (None = don't update)
        completion_path_id: New completion path ID (None = don't update)
        status: New status (None = don't update)
        description: New description (None = don't update)

    Returns:
        UpdateResult with success status
    """
    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_MILESTONE_STATUSES):
        return UpdateResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_MILESTONE_STATUSES)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check milestone exists
        if not _check_entity_exists(conn, "milestones", id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Milestone ID {id} not found"
            )

        # Check new completion path exists if provided
        if completion_path_id is not None and not _check_entity_exists(conn, "completion_path", completion_path_id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Completion path ID {completion_path_id} not found"
            )

        # Completion gate: refuse if incomplete tasks exist
        if status == 'completed':
            incomplete_tasks = _query_incomplete_tasks_by_milestone(conn, id, skip_pending=False)
            if incomplete_tasks:
                task_names = ", ".join(f"'{t.name}'" for t in incomplete_tasks[:3])
                suffix = f" and {len(incomplete_tasks) - 3} more" if len(incomplete_tasks) > 3 else ""
                conn.close()
                return UpdateResult(
                    success=False,
                    error=f"Cannot complete milestone: {len(incomplete_tasks)} incomplete task(s) exist "
                          f"({task_names}{suffix}). Complete or remove all tasks before marking milestone as completed."
                )

        # Update milestone
        _update_milestone_fields(conn, id, name, completion_path_id, status, description)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_milestone")

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


def delete_milestone(
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion"
) -> DeleteResult:
    """
    Delete milestone with task validation.

    Args:
        id: Milestone ID
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
        # Check milestone exists
        if not _check_entity_exists(conn, "milestones", id):
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Milestone ID {id} not found"
            )

        # Check for incomplete tasks
        incomplete_tasks = _query_incomplete_tasks_by_milestone(conn, id, skip_pending=False)
        if incomplete_tasks:
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Cannot delete milestone: {len(incomplete_tasks)} incomplete task(s) exist"
            )

        # Create audit note
        _create_deletion_note(conn, "milestones", id, note_reason, note_severity, note_source, note_type)

        # Delete milestone
        _delete_milestone(conn, id)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("delete_milestone")

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


def add_task(
    milestone_id: int,
    name: str,
    status: str = "pending",
    priority: str = "medium",
    description: Optional[str] = None,
    flow_ids: Optional[List[int]] = None
) -> AddResult:
    """
    Add task to milestone.

    Args:
        milestone_id: Milestone ID
        name: Task name
        status: Task status ('pending', 'in_progress', 'completed', 'blocked')
        priority: Task priority ('low', 'medium', 'high', 'critical')
        description: Optional task description
        flow_ids: Optional JSON array of flow IDs

    Returns:
        AddResult with new task ID on success
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
        # Check milestone exists
        if not _check_entity_exists(conn, "milestones", milestone_id):
            conn.close()
            return AddResult(
                success=False,
                error=f"Milestone ID {milestone_id} not found"
            )

        # Insert task
        task_id = _insert_task(conn, milestone_id, name, status, priority, description, flow_ids)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("add_task")

        return AddResult(
            success=True,
            id=task_id,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return AddResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_incomplete_tasks_by_milestone(
    milestone_id: int,
    skip_pending: bool = False
) -> TaskQueryResult:
    """
    Get open tasks for a milestone with related subtasks/sidequests.

    Args:
        milestone_id: Milestone ID
        skip_pending: If true, excludes pending tasks

    Returns:
        TaskQueryResult with incomplete tasks
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        tasks = _query_incomplete_tasks_by_milestone(conn, milestone_id, skip_pending)
        conn.close()

        return TaskQueryResult(
            success=True,
            tasks=tasks
        )

    except Exception as e:
        conn.close()
        return TaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_incomplete_tasks() -> TaskQueryResult:
    """
    Get all incomplete tasks with subtasks/sidequests.

    Returns:
        TaskQueryResult with all incomplete tasks
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        tasks = _query_incomplete_tasks(conn)
        conn.close()

        return TaskQueryResult(
            success=True,
            tasks=tasks
        )

    except Exception as e:
        conn.close()
        return TaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_tasks_by_milestone(
    milestone_id: int
) -> TaskQueryResult:
    """
    Get all tasks for a milestone (any status).

    Args:
        milestone_id: Milestone ID

    Returns:
        TaskQueryResult with all tasks for milestone
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        tasks = _query_tasks_by_milestone(conn, milestone_id)
        conn.close()

        return TaskQueryResult(
            success=True,
            tasks=tasks
        )

    except Exception as e:
        conn.close()
        return TaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_tasks_comprehensive(
    status: Optional[str] = None,
    limit: Optional[int] = None,
    date_range_created: Optional[List[str]] = None,
    date_range_updated: Optional[List[str]] = None,
    milestone_id: Optional[int] = None,
    priority: Optional[str] = None
) -> TaskQueryResult:
    """
    Advanced task search with multiple filters.

    Args:
        status: Optional status filter ('pending', 'in_progress', 'completed', 'blocked')
        limit: Optional maximum results
        date_range_created: Optional created date range [start_date, end_date]
        date_range_updated: Optional updated date range [start_date, end_date]
        milestone_id: Optional milestone filter
        priority: Optional priority filter ('low', 'medium', 'high', 'critical')

    Returns:
        TaskQueryResult with filtered tasks
    """
    # Validate status if provided
    if status is not None and not _validate_status(status, VALID_TASK_STATUSES):
        return TaskQueryResult(
            success=False,
            error=f"Invalid status: {status}. Must be one of: {', '.join(VALID_TASK_STATUSES)}"
        )

    # Validate priority if provided
    if priority is not None and not _validate_priority(priority):
        return TaskQueryResult(
            success=False,
            error=f"Invalid priority: {priority}. Must be one of: {', '.join(VALID_PRIORITY_LEVELS)}"
        )

    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        tasks = _query_tasks_comprehensive(
            conn, status, limit, date_range_created, date_range_updated, milestone_id, priority
        )
        conn.close()

        return TaskQueryResult(
            success=True,
            tasks=tasks
        )

    except Exception as e:
        conn.close()
        return TaskQueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_task_flows(
    task_id: int
) -> FlowIdsResult:
    """
    Get flow IDs for a task.

    Args:
        task_id: Task ID

    Returns:
        FlowIdsResult with flow IDs array
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check task exists
        if not _check_entity_exists(conn, "tasks", task_id):
            conn.close()
            return FlowIdsResult(
                success=False,
                error=f"Task ID {task_id} not found"
            )

        # Query flow IDs
        flow_ids = _query_task_flow_ids(conn, task_id)
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


def get_task_files(
    task_id: int
) -> FilesResult:
    """
    Get all files related to task via flows (orchestrator).

    Args:
        task_id: Task ID

    Returns:
        FilesResult with related files
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check task exists
        if not _check_entity_exists(conn, "tasks", task_id):
            conn.close()
            return FilesResult(
                success=False,
                error=f"Task ID {task_id} not found"
            )

        # Query flow IDs for task
        flow_ids = _query_task_flow_ids(conn, task_id)

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


def update_task(
    id: int,
    name: Optional[str] = None,
    milestone_id: Optional[int] = None,
    status: Optional[str] = None,
    description: Optional[str] = None,
    flow_ids: Optional[List[int]] = None,
    priority: Optional[str] = None
) -> UpdateResult:
    """
    Update task metadata.

    Args:
        id: Task ID
        name: New name (None = don't update)
        milestone_id: New milestone ID (None = don't update)
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
        # Check task exists
        if not _check_entity_exists(conn, "tasks", id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Task ID {id} not found"
            )

        # Check new milestone exists if provided
        if milestone_id is not None and not _check_entity_exists(conn, "milestones", milestone_id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Milestone ID {milestone_id} not found"
            )

        # Completion gate: refuse if incomplete items exist
        if status == 'completed':
            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM items WHERE reference_table = 'tasks' AND reference_id = ? AND status != 'completed'",
                (id,)
            )
            row = cursor.fetchone()
            incomplete_count = row['count']
            if incomplete_count > 0:
                conn.close()
                return UpdateResult(
                    success=False,
                    error=f"Cannot complete task: {incomplete_count} incomplete item(s) exist. "
                          f"Complete or remove all items before marking task as completed."
                )

        # Update task
        _update_task_fields(conn, id, name, milestone_id, status, description, flow_ids, priority)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_task")

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


def delete_task(
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion"
) -> DeleteResult:
    """
    Delete task with item validation.

    Args:
        id: Task ID
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
        # Check task exists
        if not _check_entity_exists(conn, "tasks", id):
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Task ID {id} not found"
            )

        # Check for incomplete items
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM items WHERE reference_table = 'tasks' AND reference_id = ? AND status != 'completed'",
            (id,)
        )
        row = cursor.fetchone()
        incomplete_count = row['count']

        if incomplete_count > 0:
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Cannot delete task: {incomplete_count} incomplete item(s) exist"
            )

        # Create audit note
        _create_deletion_note(conn, "tasks", id, note_reason, note_severity, note_source, note_type)

        # Delete task
        _delete_task(conn, id)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("delete_task")

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
