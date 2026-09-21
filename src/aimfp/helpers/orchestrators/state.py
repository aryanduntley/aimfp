"""
AIMFP Helper Functions - Orchestrator State Helpers

State query and update operations for the orchestrator module.

Helpers in this file:
- get_current_progress: Flexible scoped status queries
- update_project_state: Action-based state transitions
- batch_update_progress: Atomic multi-item updates

Only update_project_state and batch_update_progress write data.
All helpers target project.db only.
"""

import sqlite3
from typing import Optional, Tuple, Dict, Any, List

from ._common import (
    _open_project_connection,
    _close_connection,
    resolve_project_root,
    get_return_statements,
    row_to_dict,
    rows_to_tuple,
    Result,
    VALID_STATE_ACTIONS,
    ACTION_STATUS_MAP,
    ACTION_TARGET_MAP,
    VALID_PROGRESS_SCOPES,
    TASK_TABLE_MAP,
)


# ============================================================================
# get_current_progress
# ============================================================================

def get_current_progress(
    scope: Optional[str] = None,
    detail_level: str = "standard",
    filters: Optional[Dict[str, Any]] = None,
) -> Result:
    """
    Single entry point for scoped project status queries.

    Replaces multiple separate helper calls with one flexible query.
    Routes to appropriate tables based on scope parameter.

    Args:
        scope: What to retrieve — 'tasks', 'milestones', 'completion_paths',
               'files', 'functions', 'flows', 'themes', 'infrastructure', 'all'.
               None returns summary counts for all entity types.
        detail_level: 'minimal', 'standard' (default), 'full'
        filters: WHERE-like conditions — {field: value}

    Returns:
        Result with data varying by scope
    """
    if scope is not None and scope not in VALID_PROGRESS_SCOPES:
        return Result(
            success=False,
            error=f"Invalid scope '{scope}'. Valid: {sorted(VALID_PROGRESS_SCOPES)}",
        )

    if detail_level not in ('minimal', 'standard', 'full'):
        return Result(
            success=False,
            error=f"Invalid detail_level '{detail_level}'. Valid: minimal, standard, full",
        )

    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    try:
        conn = _open_project_connection(project_root)
        try:
            if scope is None:
                # Summary counts for all entity types
                data = _get_summary_counts(conn)
            elif scope == 'all':
                data = _get_all_progress(conn, detail_level, filters)
            else:
                data = _get_scoped_progress(conn, scope, detail_level, filters)

            return Result(
                success=True,
                data=data,
                return_statements=get_return_statements("get_current_progress"),
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return Result(success=False, error=str(e))
    except Exception as e:
        return Result(success=False, error=f"Failed to get progress: {str(e)}")


def _get_summary_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Effect: Get counts for all main entity types."""
    tables = [
        'completion_path', 'milestones', 'tasks', 'subtasks', 'sidequests',
        'files', 'functions', 'flows', 'themes', 'infrastructure',
    ]
    counts = {}
    for table in tables:
        cursor = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}")
        row = cursor.fetchone()
        counts[table] = row['cnt'] if row else 0
    return counts


def _get_all_progress(
    conn: sqlite3.Connection,
    detail_level: str,
    filters: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Effect: Get progress data for all scopes."""
    scopes = [
        'tasks', 'milestones', 'completion_paths', 'files',
        'functions', 'flows', 'themes', 'infrastructure',
    ]
    data = {}
    for scope in scopes:
        data[scope] = _get_scoped_progress(conn, scope, detail_level, filters)
    return data


def _get_scoped_progress(
    conn: sqlite3.Connection,
    scope: str,
    detail_level: str,
    filters: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Effect: Get progress data for a single scope."""
    # Map scope to table name
    table_map = {
        'tasks': 'tasks',
        'milestones': 'milestones',
        'completion_paths': 'completion_path',
        'files': 'files',
        'functions': 'functions',
        'flows': 'flows',
        'themes': 'themes',
        'infrastructure': 'infrastructure',
    }
    table = table_map.get(scope)
    if table is None:
        return {'error': f"Unknown scope: {scope}"}

    # Build query
    where_parts = []
    params = []

    if filters:
        for field, value in filters.items():
            where_parts.append(f"{field} = ?")
            params.append(value)

    where_clause = (' WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    # Count
    cursor = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}{where_clause}", params)
    count = cursor.fetchone()['cnt']

    # Records based on detail_level
    if detail_level == 'minimal':
        return {'count': count}

    limit_clause = ''
    if detail_level == 'standard':
        limit_clause = ' LIMIT 50'

    cursor = conn.execute(
        f"SELECT * FROM {table}{where_clause} ORDER BY id{limit_clause}", params
    )
    records = rows_to_tuple(cursor.fetchall())

    return {
        'count': count,
        'records': records,
    }


# ============================================================================
# update_project_state
# ============================================================================

def update_project_state(
    action: str,
    target_type: str,
    target_id: int,
    data: Optional[Dict[str, Any]] = None,
    create_note: bool = False,
) -> Result:
    """
    Single entry point for common project state updates.

    Maps action to appropriate status and timestamp updates.
    Optionally logs the action as a note.

    Args:
        action: Operation name (e.g., 'start_task', 'complete_task', 'block_subtask')
        target_type: Entity type ('task', 'subtask', 'sidequest', 'milestone', 'completion_path')
        target_id: ID of target entity
        data: Optional action-specific data (e.g., blocked_by, completion_notes)
        create_note: Auto-log action as note in notes table

    Returns:
        Result with data={success, updated_entity, note_created, note_id}
    """
    if action not in VALID_STATE_ACTIONS:
        return Result(
            success=False,
            error=f"Invalid action '{action}'. Valid: {sorted(VALID_STATE_ACTIONS)}",
        )

    new_status = ACTION_STATUS_MAP.get(action)
    if new_status is None:
        return Result(success=False, error=f"No status mapping for action '{action}'")

    # Resolve table from target_type
    table_map = {
        'task': 'tasks',
        'subtask': 'subtasks',
        'sidequest': 'sidequests',
        'milestone': 'milestones',
        'completion_path': 'completion_path',
    }
    table = table_map.get(target_type)
    if table is None:
        return Result(
            success=False,
            error=f"Invalid target_type '{target_type}'. "
                  f"Valid: {sorted(table_map.keys())}",
        )

    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    try:
        conn = _open_project_connection(project_root)
        try:
            # Verify entity exists
            cursor = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (target_id,)
            )
            entity_row = cursor.fetchone()
            if entity_row is None:
                return Result(
                    success=False,
                    error=f"{target_type} with id {target_id} not found in {table}",
                )

            # Update status
            conn.execute(
                f"UPDATE {table} SET status = ? WHERE id = ?",
                (new_status, target_id)
            )

            # Apply additional data fields if provided
            if data:
                _apply_additional_data(conn, table, target_id, data)

            conn.commit()

            # Fetch updated entity
            cursor = conn.execute(
                f"SELECT * FROM {table} WHERE id = ?", (target_id,)
            )
            updated_entity = row_to_dict(cursor.fetchone())

            result_data = {
                'success': True,
                'updated_entity': updated_entity,
                'note_created': False,
                'note_id': None,
            }

            # Optionally create note
            if create_note:
                note_id = _create_action_note(
                    conn, action, target_type, target_id, new_status
                )
                result_data['note_created'] = True
                result_data['note_id'] = note_id

            return Result(
                success=True,
                data=result_data,
                return_statements=get_return_statements("update_project_state"),
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return Result(success=False, error=str(e))
    except Exception as e:
        return Result(success=False, error=f"Failed to update state: {str(e)}")


_TABLE_SAFE_FIELDS: Dict[str, frozenset] = {
    'tasks': frozenset({'description', 'priority', 'name', 'blocked_by'}),
    'subtasks': frozenset({'description', 'priority', 'name'}),
    'sidequests': frozenset({'description', 'priority', 'name', 'flow_ids'}),
    'milestones': frozenset({'description', 'name'}),
    'completion_path': frozenset({'description', 'name'}),
    'items': frozenset({'description', 'name', 'status'}),
}


def _apply_additional_data(
    conn: sqlite3.Connection,
    table: str,
    target_id: int,
    data: Dict[str, Any],
) -> None:
    """Effect: Apply additional data fields to entity. Only updates known safe fields per table."""
    safe_fields = _TABLE_SAFE_FIELDS.get(table, frozenset())
    for field, value in data.items():
        if field in safe_fields:
            conn.execute(
                f"UPDATE {table} SET {field} = ? WHERE id = ?",
                (value, target_id)
            )


def _create_action_note(
    conn: sqlite3.Connection,
    action: str,
    target_type: str,
    target_id: int,
    new_status: str,
) -> int:
    """Effect: Create audit note for state change action."""
    ref_table = TASK_TABLE_MAP.get(target_type, target_type + 's')
    content = f"Action: {action} — status changed to '{new_status}'"

    cursor = conn.execute(
        """
        INSERT INTO notes (content, source, severity, note_type, reference_table, reference_id)
        VALUES (?, 'ai', 'info', 'task_context', ?, ?)
        """,
        (content, ref_table, target_id)
    )
    conn.commit()
    return cursor.lastrowid


# ============================================================================
# batch_update_progress
# ============================================================================

def batch_update_progress(
    updates: List[Dict[str, Any]],
    transaction: bool = True,
    continue_on_error: bool = False,
) -> Result:
    """
    Update multiple project items. Optionally atomic via transaction.

    Each update dict: {target_type, target_id, action, data (optional)}.

    Args:
        updates: List of update dicts [{target_type, target_id, action, data?}, ...]
        transaction: If True, all-or-nothing (rolls back on failure)
        continue_on_error: If True, process remaining updates even if one fails

    Returns:
        Result with data={
            success: bool,
            updated_count: int,
            failed_count: int,
            results: [
                {success: bool, target_type, target_id, action, error?}
            ]
        }
    """
    if not updates:
        return Result(
            success=True,
            data={
                'success': True,
                'updated_count': 0,
                'failed_count': 0,
                'results': (),
            },
            return_statements=get_return_statements("batch_update_progress"),
        )

    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    results = []
    updated_count = 0
    failed_count = 0

    try:
        conn = _open_project_connection(project_root)
        try:
            if transaction:
                # IMMEDIATE, not a plain deferred BEGIN. Each update below
                # SELECTs the row and then UPDATEs it, so a deferred
                # transaction takes its read snapshot first and only asks for
                # the write lock afterwards. If a parallel writer commits in
                # between, SQLite returns SQLITE_BUSY_SNAPSHOT *immediately* and
                # does NOT invoke the busy handler — the snapshot is stale, so
                # waiting cannot help and no busy timeout fixes it. Taking the
                # write lock up front is the only thing that does. Same reason
                # items_notes.py::update_items uses BEGIN IMMEDIATE.
                conn.execute("BEGIN IMMEDIATE")

            for update in updates:
                action = update.get('action', '')
                target_type = update.get('target_type', '')
                target_id = update.get('target_id')
                update_data = update.get('data')

                try:
                    # Validate action
                    if action not in VALID_STATE_ACTIONS:
                        raise ValueError(f"Invalid action: {action}")

                    new_status = ACTION_STATUS_MAP.get(action)
                    table_map = {
                        'task': 'tasks', 'subtask': 'subtasks',
                        'sidequest': 'sidequests', 'item': 'items',
                        'milestone': 'milestones',
                        'completion_path': 'completion_path',
                    }
                    table = table_map.get(target_type)
                    if table is None:
                        raise ValueError(f"Invalid target_type: {target_type}")

                    # Verify exists
                    cursor = conn.execute(
                        f"SELECT id FROM {table} WHERE id = ?", (target_id,)
                    )
                    if cursor.fetchone() is None:
                        raise ValueError(
                            f"{target_type} id {target_id} not found"
                        )

                    # Update
                    conn.execute(
                        f"UPDATE {table} SET status = ? WHERE id = ?",
                        (new_status, target_id)
                    )

                    if update_data:
                        _apply_additional_data(conn, table, target_id, update_data)

                    results.append({
                        'success': True,
                        'target_type': target_type,
                        'target_id': target_id,
                        'action': action,
                    })
                    updated_count += 1

                except Exception as e:
                    failed_count += 1
                    results.append({
                        'success': False,
                        'target_type': target_type,
                        'target_id': target_id,
                        'action': action,
                        'error': str(e),
                    })

                    if transaction:
                        conn.execute("ROLLBACK")
                        return Result(
                            success=False,
                            data={
                                'success': False,
                                'updated_count': 0,
                                'failed_count': failed_count,
                                'results': tuple(results),
                            },
                            error=f"Transaction rolled back: {str(e)}",
                            return_statements=get_return_statements(
                                "batch_update_progress"
                            ),
                        )

                    if not continue_on_error:
                        break

            if transaction and failed_count == 0:
                conn.execute("COMMIT")
            elif not transaction:
                conn.commit()

            all_success = failed_count == 0

            return Result(
                success=all_success,
                data={
                    'success': all_success,
                    'updated_count': updated_count,
                    'failed_count': failed_count,
                    'results': tuple(results),
                },
                return_statements=get_return_statements("batch_update_progress"),
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return Result(success=False, error=str(e))
    except Exception as e:
        return Result(success=False, error=f"Batch update failed: {str(e)}")
