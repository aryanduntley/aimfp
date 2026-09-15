"""
AIMFP Helper Functions - Task Files

Links work items (tasks, subtasks, sidequests) to the files worked on for them,
via the polymorphic task_files junction (reference_table + reference_id, the same
shape as items).

Why this exists: flows are architectural groupings, so "files in the task's flows"
is every file in that area of the codebase, not the files a task touched. The
task_files junction records the actual relationship.

Two ways rows get written:
- Automatically: file/function/type tracking helpers call
  link_files_to_current_focus_effect after a successful write. The current focus is the
  most recent in_progress sidequest, else subtask, else task — the same precedence
  aimfp_status reports. Best-effort: auto-linking never fails the tracking call
  (e.g. on a pre-v1.12 database without the table).
- Explicitly: link_files_to_task / unlink_files_from_task, for work done before a
  task was marked in_progress or to correct a link.

Readers: get_task_context, get_task_files, get_sidequest_files.
"""

import sqlite3
from dataclasses import dataclass
from typing import Dict, Final, Iterable, List, Optional, Tuple

from ..utils import get_return_statements
from ._common import (
    _check_entity_exists,
    _check_file_exists,
    _open_project_connection,
    get_cached_project_root,
)


# ============================================================================
# Constants
# ============================================================================

TASK_TYPE_TABLES: Final[Dict[str, str]] = {
    'task': 'tasks',
    'subtask': 'subtasks',
    'sidequest': 'sidequests',
}

# Current-focus precedence: an interruption (sidequest) outranks the subtask it
# paused, which outranks its parent task.
FOCUS_PRECEDENCE: Final[Tuple[Tuple[str, str], ...]] = (
    ('sidequest', 'sidequests'),
    ('subtask', 'subtasks'),
    ('task', 'tasks'),
)


# ============================================================================
# Data Structures
# ============================================================================

@dataclass(frozen=True)
class WorkItemRef:
    """Immutable reference to a task, subtask, or sidequest."""
    reference_table: str
    reference_id: int


@dataclass(frozen=True)
class TaskFilesResult:
    """Result of linking or unlinking task files."""
    success: bool
    reference_table: Optional[str] = None
    reference_id: Optional[int] = None
    added_count: int = 0
    removed_count: int = 0
    skipped_count: int = 0
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Functions
# ============================================================================

def unique_file_ids(file_ids: Iterable[Optional[int]]) -> Tuple[int, ...]:
    """
    Pure: Drop None values and duplicates, preserving first-seen order.

    Args:
        file_ids: File IDs, possibly with None or repeats

    Returns:
        Tuple of distinct file IDs
    """
    return tuple(dict.fromkeys(fid for fid in file_ids if fid is not None))


# ============================================================================
# Effect Functions
# ============================================================================

def query_current_focus_row(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """
    Effect: Get the current in_progress work item row (sidequest > subtask > task).

    The row carries an extra item_type column ('sidequest' | 'subtask' | 'task').

    Args:
        conn: Project database connection

    Returns:
        The most recent in_progress row at the highest-precedence level, or None
    """
    for item_type, table in FOCUS_PRECEDENCE:
        row = conn.execute(
            f"SELECT *, '{item_type}' AS item_type FROM {table} "
            "WHERE status = 'in_progress' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return row
    return None


def _insert_task_files_effect(
    conn: sqlite3.Connection,
    ref: WorkItemRef,
    file_ids: Tuple[int, ...],
) -> int:
    """Effect: INSERT OR IGNORE task_files rows; returns rows actually inserted."""
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO task_files (reference_table, reference_id, file_id) VALUES (?, ?, ?)",
        tuple((ref.reference_table, ref.reference_id, fid) for fid in file_ids),
    )
    return conn.total_changes - before


def query_task_file_ids(
    conn: sqlite3.Connection,
    ref: WorkItemRef,
    include_subtasks: bool = False,
) -> Tuple[int, ...]:
    """
    Effect: Get file IDs linked to a work item, oldest link first.

    Args:
        conn: Project database connection
        ref: The task, subtask, or sidequest
        include_subtasks: For a task, also include files linked to its subtasks

    Returns:
        Tuple of distinct file IDs
    """
    rows = conn.execute(
        "SELECT file_id FROM task_files WHERE reference_table = ? AND reference_id = ? "
        "ORDER BY created_at, rowid",
        (ref.reference_table, ref.reference_id),
    ).fetchall()
    subtask_rows: List[sqlite3.Row] = []
    if include_subtasks and ref.reference_table == 'tasks':
        subtask_rows = conn.execute(
            "SELECT tf.file_id FROM task_files tf "
            "JOIN subtasks s ON tf.reference_table = 'subtasks' AND tf.reference_id = s.id "
            "WHERE s.parent_task_id = ? ORDER BY tf.created_at, tf.rowid",
            (ref.reference_id,),
        ).fetchall()
    return unique_file_ids(r[0] for r in (*rows, *subtask_rows))


def query_task_file_rows(
    conn: sqlite3.Connection,
    ref: WorkItemRef,
    include_subtasks: bool = False,
) -> Tuple[sqlite3.Row, ...]:
    """
    Effect: Get file rows linked to a work item, in link order.

    Args:
        conn: Project database connection
        ref: The task, subtask, or sidequest
        include_subtasks: For a task, also include files linked to its subtasks

    Returns:
        Tuple of files rows
    """
    file_ids = query_task_file_ids(conn, ref, include_subtasks)
    if not file_ids:
        return ()
    placeholders = ','.join('?' for _ in file_ids)
    rows = conn.execute(f"SELECT * FROM files WHERE id IN ({placeholders})", file_ids).fetchall()
    by_id = {row['id']: row for row in rows}
    return tuple(by_id[fid] for fid in file_ids if fid in by_id)


def link_files_to_current_focus_effect(
    conn: sqlite3.Connection,
    file_ids: Iterable[Optional[int]],
) -> Optional[WorkItemRef]:
    """
    Effect: Link files to the current in_progress work item and commit. Best-effort.

    Args:
        conn: Project database connection (caller owns its lifecycle)
        file_ids: Files just tracked (None entries ignored)

    Returns:
        The work item linked to, or None if there is no focus, nothing to link,
        or the database cannot hold links (pre-v1.12 schema)
    """
    ids = unique_file_ids(file_ids)
    if not ids:
        return None
    try:
        focus = query_current_focus_row(conn)
        if focus is None:
            return None
        ref = WorkItemRef(TASK_TYPE_TABLES[focus['item_type']], focus['id'])
        _insert_task_files_effect(conn, ref, ids)
        conn.commit()
        return ref
    except sqlite3.Error:
        return None



# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def _resolve_work_item(
    conn: sqlite3.Connection,
    task_id: int,
    task_type: str,
) -> Tuple[Optional[WorkItemRef], Optional[str]]:
    """Effect: Validate task_type and existence; returns (ref, error)."""
    table = TASK_TYPE_TABLES.get(task_type)
    if table is None:
        return None, f"Invalid task_type '{task_type}'. Valid: {sorted(TASK_TYPE_TABLES)}"
    if not _check_entity_exists(conn, table, task_id):
        return None, f"{task_type.capitalize()} with ID {task_id} not found"
    return WorkItemRef(table, task_id), None


def link_files_to_task(
    task_id: int,
    file_ids: List[int],
    task_type: str = 'task',
    project_root: Optional[str] = None,
) -> TaskFilesResult:
    """
    Link files to a task, subtask, or sidequest (task_files junction).

    Tracking helpers link automatically while a work item is in_progress; use this
    for files worked on earlier or to correct a link. Skips existing links.

    Args:
        task_id: ID of the task/subtask/sidequest
        file_ids: File IDs to link
        task_type: 'task', 'subtask', or 'sidequest'

    Returns:
        TaskFilesResult with added/skipped counts
    """
    ids = unique_file_ids(file_ids or ())
    if not ids:
        return TaskFilesResult(success=False, error="file_ids cannot be empty")

    conn = _open_project_connection(project_root or get_cached_project_root())
    try:
        ref, error = _resolve_work_item(conn, task_id, task_type)
        if error:
            return TaskFilesResult(success=False, error=error)
        missing = tuple(fid for fid in ids if not _check_file_exists(conn, fid))
        if missing:
            return TaskFilesResult(success=False, error=f"File IDs not found: {list(missing)}")

        added = _insert_task_files_effect(conn, ref, ids)
        conn.commit()
        return TaskFilesResult(
            success=True,
            reference_table=ref.reference_table,
            reference_id=ref.reference_id,
            added_count=added,
            skipped_count=len(ids) - added,
            return_statements=get_return_statements("link_files_to_task"),
        )
    except Exception as e:
        return TaskFilesResult(success=False, error=f"Link failed: {str(e)}")
    finally:
        conn.close()


def unlink_files_from_task(
    task_id: int,
    file_ids: List[int],
    task_type: str = 'task',
    project_root: Optional[str] = None,
) -> TaskFilesResult:
    """
    Remove file links from a task, subtask, or sidequest. Does not touch the files.

    Args:
        task_id: ID of the task/subtask/sidequest
        file_ids: File IDs to unlink
        task_type: 'task', 'subtask', or 'sidequest'

    Returns:
        TaskFilesResult with removed/skipped counts
    """
    ids = unique_file_ids(file_ids or ())
    if not ids:
        return TaskFilesResult(success=False, error="file_ids cannot be empty")

    conn = _open_project_connection(project_root or get_cached_project_root())
    try:
        ref, error = _resolve_work_item(conn, task_id, task_type)
        if error:
            return TaskFilesResult(success=False, error=error)

        before = conn.total_changes
        conn.executemany(
            "DELETE FROM task_files WHERE reference_table = ? AND reference_id = ? AND file_id = ?",
            tuple((ref.reference_table, ref.reference_id, fid) for fid in ids),
        )
        removed = conn.total_changes - before
        conn.commit()
        return TaskFilesResult(
            success=True,
            reference_table=ref.reference_table,
            reference_id=ref.reference_id,
            removed_count=removed,
            skipped_count=len(ids) - removed,
            return_statements=get_return_statements("unlink_files_from_task"),
        )
    except Exception as e:
        return TaskFilesResult(success=False, error=f"Unlink failed: {str(e)}")
    finally:
        conn.close()
