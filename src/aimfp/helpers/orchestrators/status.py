"""
AIMFP Helper Functions - Orchestrator Status Helpers

Read-only status and context retrieval from project.db.

Helpers in this file:
- get_project_status: Hierarchy-aware project status with counts, context, files
- get_task_context: Complete context for resuming a specific task/subtask/sidequest

All helpers target project.db only. No decision logic — AI interprets data.
"""

import sqlite3
from typing import Optional, Tuple, Dict, Any, List

from ._common import (
    _open_project_connection,
    _close_connection,
    get_cached_project_root,
    resolve_project_root,
    get_return_statements,
    row_to_dict,
    rows_to_tuple,
    Result,
    VALID_STATUS_TYPES,
    VALID_TASK_TYPES,
    TASK_TABLE_MAP,
)


# ============================================================================
# Query utility helpers (thin wrappers for readability)
# ============================================================================

def _query_one(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
) -> Optional[Dict[str, Any]]:
    """Effect: Execute query and return first row as dict, or None."""
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    return row_to_dict(row) if row else None


def _query_all(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Execute query and return all rows as tuple of dicts."""
    cursor = conn.execute(sql, params)
    return rows_to_tuple(cursor.fetchall())


# ============================================================================
# Summary trimming helpers (keep current state lean — full detail on demand)
# ============================================================================

# Max length for description fields in summary mode. Full descriptions are
# always retrievable via get_task_context(task_id).
SUMMARY_DESC_LIMIT: int = 150


def _truncate(text: Any, limit: int = SUMMARY_DESC_LIMIT) -> Any:
    """Pure: Truncate a string to `limit` chars with an ellipsis; pass non-strings through."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[:limit].rstrip() + '…'


def _truncate_descriptions(obj: Any, limit: int = SUMMARY_DESC_LIMIT) -> Any:
    """Pure: Recursively truncate any 'description' string field in nested dicts/tuples/lists."""
    if isinstance(obj, dict):
        return {
            k: (_truncate(v, limit) if k == 'description' else _truncate_descriptions(v, limit))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(_truncate_descriptions(v, limit) for v in obj)
    return obj


def _compact_items(
    items: Tuple[Dict[str, Any], ...],
) -> Tuple[Dict[str, Any], ...]:
    """Pure: Reduce historical item rows to id/name/status only.

    Historical items are positional breadcrumbs ("what just happened"), not
    active work — names are enough. Full bodies via get_task_context(task_id).
    """
    return tuple(
        {'id': i.get('id'), 'name': i.get('name'), 'status': i.get('status')}
        for i in items
    )


# ============================================================================
# get_project_status
# ============================================================================

def get_project_status(
    type: str = "summary",
) -> Result:
    """
    Retrieve hierarchy-aware project status from project database.

    Returns data following the hierarchy:
    completion_path → milestone → task/subtask/sidequest → items

    Summary mode returns:
    - Aggregate counts
    - Active state (scoped to current position in hierarchy)
    - Historical context (positional — what just happened)
    - Files context (recent, active, reserved)
    - Blocked items
    - Nested tree

    Args:
        type: 'quick' (counts only), 'summary' (default), or 'detailed' (all history)

    Returns:
        Result with hierarchy-aware status data
    """
    if type not in VALID_STATUS_TYPES:
        return Result(
            success=False,
            error=f"Invalid type '{type}'. Valid: {sorted(VALID_STATUS_TYPES)}",
        )

    project_root = resolve_project_root()

    try:
        conn = _open_project_connection(project_root)
        try:
            # Counts (always fetched)
            counts = _get_work_counts(conn)

            if type == 'quick':
                # Quick: counts + current in_progress item only
                current_focus = _get_current_focus(conn)
                return Result(
                    success=True,
                    data={
                        'counts': counts,
                        'current_focus': current_focus,
                    },
                    return_statements=get_return_statements("get_project_status"),
                )

            if type == 'detailed':
                # Detailed: all records including full history
                records = _get_all_records(conn)
                tree = _build_tree(records)
                blocked = _get_blocked_items(conn)
                current_focus = _get_current_focus(conn)

                return Result(
                    success=True,
                    data={
                        'counts': counts,
                        'completion_paths': records['completion_paths'],
                        'milestones': records['milestones'],
                        'tasks': records['tasks'],
                        'subtasks': records['subtasks'],
                        'sidequests': records['sidequests'],
                        'blocked_items': blocked,
                        'current_focus': current_focus,
                        'tree': tree,
                    },
                    return_statements=get_return_statements("get_project_status"),
                )

            # Summary (default): hierarchy-aware context.
            # Descriptions are truncated for lean session state — full text is
            # always available via get_task_context(task_id).
            hierarchy = _truncate_descriptions(_get_hierarchy_context(conn))
            blocked = _truncate_descriptions(_get_blocked_items(conn))

            data = {
                'counts': counts,
                'hierarchy': hierarchy,
                'blocked_items': blocked,
            }

            return Result(
                success=True,
                data=data,
                return_statements=get_return_statements("get_project_status"),
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return Result(success=False, error=str(e))
    except Exception as e:
        return Result(success=False, error=f"Failed to get project status: {str(e)}")


# ============================================================================
# Hierarchy-aware context (summary mode)
# ============================================================================

def _get_hierarchy_context(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Effect: Get hierarchy-aware project context.

    Follows completion_path → milestone → task/subtask/sidequest → items.
    Includes positional historical context and files/reserved entities.
    """
    # 1. Active completion path (in_progress, or first pending)
    active_path = _query_one(
        conn,
        "SELECT * FROM completion_path WHERE status = 'in_progress' "
        "ORDER BY order_index LIMIT 1",
    )
    if not active_path:
        active_path = _query_one(
            conn,
            "SELECT * FROM completion_path WHERE status = 'pending' "
            "ORDER BY order_index LIMIT 1",
        )

    path_id = active_path['id'] if active_path else None

    # 2. Active milestone in that path (in_progress, or first pending)
    active_milestone = None
    if path_id:
        active_milestone = _query_one(
            conn,
            "SELECT * FROM milestones "
            "WHERE completion_path_id = ? AND status = 'in_progress' "
            "ORDER BY id LIMIT 1",
            (path_id,),
        )
        if not active_milestone:
            active_milestone = _query_one(
                conn,
                "SELECT * FROM milestones "
                "WHERE completion_path_id = ? AND status = 'pending' "
                "ORDER BY id LIMIT 1",
                (path_id,),
            )

    ms_id = active_milestone['id'] if active_milestone else None

    # 3. Active tasks in that milestone
    active_tasks = ()
    if ms_id:
        active_tasks = _query_all(
            conn,
            "SELECT * FROM tasks "
            "WHERE milestone_id = ? AND status IN ('in_progress', 'pending') "
            "ORDER BY priority DESC, id",
            (ms_id,),
        )

    # 4. Active subtasks for those tasks
    active_subtasks = ()
    if active_tasks:
        task_ids = tuple(t['id'] for t in active_tasks)
        placeholders = ','.join('?' * len(task_ids))
        active_subtasks = _query_all(
            conn,
            f"SELECT * FROM subtasks "
            f"WHERE parent_task_id IN ({placeholders}) "
            f"AND status IN ('in_progress', 'pending') "
            f"ORDER BY id",
            task_ids,
        )

    # 5. All active sidequests (global priority — always included)
    active_sidequests = _query_all(
        conn,
        "SELECT * FROM sidequests "
        "WHERE status IN ('in_progress', 'pending') "
        "ORDER BY id",
    )

    # 6. Current focus + items for it
    current_focus = _get_current_focus(conn)
    active_items = ()
    if current_focus:
        ref_table = {
            'task': 'tasks',
            'subtask': 'subtasks',
            'sidequest': 'sidequests',
        }.get(current_focus.get('item_type'))
        ref_id = current_focus.get('id')
        if ref_table and ref_id:
            active_items = _query_all(
                conn,
                "SELECT * FROM items "
                "WHERE reference_table = ? AND reference_id = ? "
                "ORDER BY id",
                (ref_table, ref_id),
            )

    # 7. Historical context (positional)
    historical = _get_historical_context(conn, ms_id, active_tasks)

    # 8. Files context
    recent_files = _query_all(
        conn,
        "SELECT * FROM files ORDER BY updated_at DESC LIMIT 5",
    )

    # Reserved entities (not yet finalized — reminder for reserve→finalize flow)
    reserved_files = _query_all(
        conn,
        "SELECT id, path, language FROM files WHERE is_reserved = 1",
    )
    reserved_functions = _query_all(
        conn,
        "SELECT id, name, file_id FROM functions WHERE is_reserved = 1",
    )

    return {
        'active_path': active_path,
        'active_milestone': active_milestone,
        'active_tasks': active_tasks,
        'active_subtasks': active_subtasks,
        'active_sidequests': active_sidequests,
        'current_focus': current_focus,
        'active_items': active_items,
        'historical': historical,
        'recent_files': recent_files,
        'reserved_entities': {
            'files': reserved_files,
            'functions': reserved_functions,
        },
    }


def _get_historical_context(
    conn: sqlite3.Connection,
    active_milestone_id: Optional[int],
    active_tasks: Tuple[Dict[str, Any], ...],
) -> Dict[str, Any]:
    """
    Effect: Get positional historical context.

    Provides:
    - Last completed task in active milestone + its last 3 items
    - If first task in milestone or no tasks yet: last completed milestone
      + last task from that milestone + its last 3 items
    """
    result = {}

    if not active_milestone_id:
        return result

    # Last completed task in active milestone
    last_task = _query_one(
        conn,
        "SELECT * FROM tasks "
        "WHERE milestone_id = ? AND status = 'completed' "
        "ORDER BY updated_at DESC LIMIT 1",
        (active_milestone_id,),
    )

    if last_task:
        result['last_completed_task'] = last_task
        result['last_completed_task_items'] = _compact_items(_query_all(
            conn,
            "SELECT id, name, status FROM items "
            "WHERE reference_table = 'tasks' AND reference_id = ? "
            "ORDER BY id DESC LIMIT 3",
            (last_task['id'],),
        ))

    # If no completed tasks in milestone (first task) or no active tasks yet:
    # provide previous milestone context
    has_completed_tasks = last_task is not None
    has_no_active_tasks = len(active_tasks) == 0

    if not has_completed_tasks or has_no_active_tasks:
        last_ms = _query_one(
            conn,
            "SELECT * FROM milestones WHERE status = 'completed' "
            "ORDER BY updated_at DESC LIMIT 1",
        )
        if last_ms:
            result['last_completed_milestone'] = last_ms
            prev_task = _query_one(
                conn,
                "SELECT * FROM tasks "
                "WHERE milestone_id = ? AND status = 'completed' "
                "ORDER BY updated_at DESC LIMIT 1",
                (last_ms['id'],),
            )
            if prev_task:
                result['last_task_in_prev_milestone'] = prev_task
                result['last_task_in_prev_milestone_items'] = _compact_items(_query_all(
                    conn,
                    "SELECT id, name, status FROM items "
                    "WHERE reference_table = 'tasks' AND reference_id = ? "
                    "ORDER BY id DESC LIMIT 3",
                    (prev_task['id'],),
                ))

    return result


# ============================================================================
# Shared helpers used by multiple modes
# ============================================================================

def _get_work_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Effect: Get aggregate counts for all work hierarchy levels."""
    counts = {}
    count_queries = {
        'completion_paths': "SELECT COUNT(*) as cnt FROM completion_path",
        'milestones': "SELECT COUNT(*) as cnt FROM milestones",
        'tasks': "SELECT COUNT(*) as cnt FROM tasks",
        'subtasks': "SELECT COUNT(*) as cnt FROM subtasks",
        'sidequests': "SELECT COUNT(*) as cnt FROM sidequests",
        'incomplete_tasks': "SELECT COUNT(*) as cnt FROM tasks WHERE status != 'completed'",
        'incomplete_subtasks': "SELECT COUNT(*) as cnt FROM subtasks WHERE status != 'completed'",
        'incomplete_sidequests': "SELECT COUNT(*) as cnt FROM sidequests WHERE status != 'completed'",
        'blocked_items': "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'blocked'",
        'modules': "SELECT COUNT(*) as cnt FROM modules",
    }
    for key, sql in count_queries.items():
        cursor = conn.execute(sql)
        row = cursor.fetchone()
        counts[key] = row['cnt'] if row else 0
    return counts


def _get_current_focus(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Effect: Get current in_progress work item (priority: sidequest > subtask > task)."""
    # Check sidequests first
    cursor = conn.execute(
        "SELECT *, 'sidequest' as item_type FROM sidequests "
        "WHERE status = 'in_progress' ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if row:
        return row_to_dict(row)

    # Then subtasks
    cursor = conn.execute(
        "SELECT *, 'subtask' as item_type FROM subtasks "
        "WHERE status = 'in_progress' ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if row:
        return row_to_dict(row)

    # Then tasks
    cursor = conn.execute(
        "SELECT *, 'task' as item_type FROM tasks "
        "WHERE status = 'in_progress' ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if row:
        return row_to_dict(row)

    return None


def _get_blocked_items(conn: sqlite3.Connection) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get all blocked work items across tables."""
    blocked = []

    cursor = conn.execute(
        "SELECT *, 'task' as item_type FROM tasks WHERE status = 'blocked'"
    )
    blocked.extend(row_to_dict(row) for row in cursor.fetchall())

    cursor = conn.execute(
        "SELECT *, 'subtask' as item_type FROM subtasks WHERE status = 'blocked'"
    )
    blocked.extend(row_to_dict(row) for row in cursor.fetchall())

    return tuple(blocked)


# ============================================================================
# Detailed mode helpers (full history)
# ============================================================================

def _get_all_records(
    conn: sqlite3.Connection,
) -> Dict[str, Tuple[Dict[str, Any], ...]]:
    """Effect: Get all work records including full completed history."""
    records = {}

    cursor = conn.execute("SELECT * FROM completion_path ORDER BY order_index, id")
    records['completion_paths'] = rows_to_tuple(cursor.fetchall())

    cursor = conn.execute("SELECT * FROM milestones ORDER BY completion_path_id, id")
    records['milestones'] = rows_to_tuple(cursor.fetchall())

    cursor = conn.execute("SELECT * FROM tasks ORDER BY milestone_id, id")
    records['tasks'] = rows_to_tuple(cursor.fetchall())

    cursor = conn.execute("SELECT * FROM subtasks ORDER BY parent_task_id, id")
    records['subtasks'] = rows_to_tuple(cursor.fetchall())

    cursor = conn.execute("SELECT * FROM sidequests ORDER BY id")
    records['sidequests'] = rows_to_tuple(cursor.fetchall())

    return records


def _build_tree(
    records: Dict[str, Tuple[Dict[str, Any], ...]],
) -> Dict[str, Any]:
    """Pure: Build nested tree from flat records."""
    # Index subtasks by task_id
    subtasks_by_task = {}
    for st in records.get('subtasks', ()):
        task_id = st.get('parent_task_id')
        if task_id not in subtasks_by_task:
            subtasks_by_task[task_id] = []
        subtasks_by_task[task_id].append(st)

    # Index tasks by milestone_id, embed subtasks
    tasks_by_milestone = {}
    for t in records.get('tasks', ()):
        ms_id = t.get('milestone_id')
        task_with_children = dict(t)
        task_with_children['subtasks'] = tuple(subtasks_by_task.get(t.get('id'), []))
        if ms_id not in tasks_by_milestone:
            tasks_by_milestone[ms_id] = []
        tasks_by_milestone[ms_id].append(task_with_children)

    # Index milestones by completion_path_id, embed tasks
    milestones_by_path = {}
    for m in records.get('milestones', ()):
        cp_id = m.get('completion_path_id')
        ms_with_children = dict(m)
        ms_with_children['tasks'] = tuple(tasks_by_milestone.get(m.get('id'), []))
        if cp_id not in milestones_by_path:
            milestones_by_path[cp_id] = []
        milestones_by_path[cp_id].append(ms_with_children)

    # Build completion paths with embedded milestones
    tree_paths = []
    for cp in records.get('completion_paths', ()):
        cp_with_children = dict(cp)
        cp_with_children['milestones'] = tuple(milestones_by_path.get(cp.get('id'), []))
        tree_paths.append(cp_with_children)

    return {
        'completion_paths': tuple(tree_paths),
        'sidequests': records.get('sidequests', ()),
    }


# ============================================================================
# get_task_context
# ============================================================================

def get_task_context(
    task_id: int,
    task_type: Optional[str] = None,
    include_interactions: bool = False,
    include_history: bool = False,
) -> Result:
    """
    Get complete context for resuming work on a specific task/subtask/sidequest.

    Single call retrieves the item + associated items + flows + files +
    functions, and optionally interactions and note history.

    Args:
        task_id: ID of the task/subtask/sidequest
        task_type: 'task', 'subtask', or 'sidequest' (auto-detected if omitted)
        include_interactions: Include function dependency interactions
        include_history: Include note history for task

    Returns:
        Result with data={
            task_item: dict,
            task_type: str,
            items: tuple,
            flows: tuple,
            files: tuple,
            functions: tuple,
            modules: tuple (distinct modules owning the task's files),
            interactions: tuple (if requested),
            notes: tuple (if requested)
        }
    """
    if task_type is not None and task_type not in VALID_TASK_TYPES:
        return Result(
            success=False,
            error=f"Invalid task_type '{task_type}'. "
                  f"Valid: {sorted(VALID_TASK_TYPES)}",
        )

    try:
        project_root = get_cached_project_root()
    except RuntimeError:
        return Result(
            success=False,
            error="Project root not cached. Call aimfp_init or aimfp_run first.",
        )

    try:
        conn = _open_project_connection(project_root)
        try:
            # Step 1: Get the task/subtask/sidequest
            if task_type is not None:
                # Explicit type — single table lookup
                table = TASK_TABLE_MAP[task_type]
                cursor = conn.execute(
                    f"SELECT * FROM {table} WHERE id = ?", (task_id,)
                )
                task_row = cursor.fetchone()
            else:
                # Auto-detect — search all tables
                task_row = None
                for candidate_type, table in TASK_TABLE_MAP.items():
                    cursor = conn.execute(
                        f"SELECT * FROM {table} WHERE id = ?", (task_id,)
                    )
                    task_row = cursor.fetchone()
                    if task_row is not None:
                        task_type = candidate_type
                        break

            if task_row is None:
                searched = f"'{task_type}'" if task_type else "tasks, subtasks, sidequests"
                return Result(
                    success=False,
                    error=f"No item with id {task_id} found in {searched}",
                )
            task_item = row_to_dict(task_row)

            # Step 2: Get associated items
            items = _get_items_for_task(conn, task_type, task_id)

            # Step 3: Get flows associated with this task's items
            flow_ids = _get_flow_ids_from_items(conn, items)
            flows = _get_flows_by_ids(conn, flow_ids)

            # Step 4: Get files from file_flows for those flows
            file_ids = _get_file_ids_from_flows(conn, flow_ids)
            files = _get_files_by_ids(conn, file_ids)

            # Step 5: Get functions for those files
            functions = _get_functions_for_files(conn, file_ids)

            # Step 6: Get module membership for files
            modules = _get_modules_for_files(conn, file_ids)

            data = {
                'task_item': task_item,
                'task_type': task_type,
                'items': items,
                'flows': flows,
                'files': files,
                'functions': functions,
                'modules': modules,
            }

            # Step 7 (optional): Interactions
            if include_interactions:
                func_ids = tuple(f.get('id') for f in functions if f.get('id'))
                data['interactions'] = _get_interactions_for_functions(conn, func_ids)

            # Step 8 (optional): Note history
            if include_history:
                data['notes'] = _get_notes_for_task(conn, task_type, task_id)

            return Result(
                success=True,
                data=data,
                return_statements=get_return_statements("get_task_context"),
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return Result(success=False, error=str(e))
    except Exception as e:
        return Result(success=False, error=f"Failed to get task context: {str(e)}")


# ============================================================================
# get_task_context sub-helpers
# ============================================================================

def _get_items_for_task(
    conn: sqlite3.Connection,
    task_type: str,
    task_id: int,
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get items linked to a task/subtask/sidequest."""
    ref_table = TASK_TABLE_MAP.get(task_type)
    if ref_table is None:
        return ()

    cursor = conn.execute(
        "SELECT * FROM items WHERE reference_table = ? AND reference_id = ?",
        (ref_table, task_id),
    )
    return rows_to_tuple(cursor.fetchall())


def _get_flow_ids_from_items(
    conn: sqlite3.Connection,
    items: Tuple[Dict[str, Any], ...],
) -> Tuple[int, ...]:
    """Pure: Extract unique flow IDs from items (via file_flows)."""
    file_ids = set()
    for item in items:
        fid = item.get('file_id')
        if fid:
            file_ids.add(fid)

    if not file_ids:
        return ()

    placeholders = ','.join('?' for _ in file_ids)
    cursor = conn.execute(
        f"SELECT DISTINCT flow_id FROM file_flows WHERE file_id IN ({placeholders})",
        tuple(file_ids)
    )
    return tuple(row['flow_id'] for row in cursor.fetchall())


def _get_flows_by_ids(
    conn: sqlite3.Connection,
    flow_ids: Tuple[int, ...],
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get flow records by IDs."""
    if not flow_ids:
        return ()
    placeholders = ','.join('?' for _ in flow_ids)
    cursor = conn.execute(
        f"SELECT * FROM flows WHERE id IN ({placeholders})", flow_ids
    )
    return rows_to_tuple(cursor.fetchall())


def _get_file_ids_from_flows(
    conn: sqlite3.Connection,
    flow_ids: Tuple[int, ...],
) -> Tuple[int, ...]:
    """Effect: Get unique file IDs from file_flows for given flow IDs."""
    if not flow_ids:
        return ()
    placeholders = ','.join('?' for _ in flow_ids)
    cursor = conn.execute(
        f"SELECT DISTINCT file_id FROM file_flows WHERE flow_id IN ({placeholders})",
        flow_ids
    )
    return tuple(row['file_id'] for row in cursor.fetchall())


def _get_files_by_ids(
    conn: sqlite3.Connection,
    file_ids: Tuple[int, ...],
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get file records by IDs."""
    if not file_ids:
        return ()
    placeholders = ','.join('?' for _ in file_ids)
    cursor = conn.execute(
        f"SELECT * FROM files WHERE id IN ({placeholders})", file_ids
    )
    return rows_to_tuple(cursor.fetchall())


def _get_functions_for_files(
    conn: sqlite3.Connection,
    file_ids: Tuple[int, ...],
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get all functions for given file IDs."""
    if not file_ids:
        return ()
    placeholders = ','.join('?' for _ in file_ids)
    cursor = conn.execute(
        f"SELECT * FROM functions WHERE file_id IN ({placeholders})", file_ids
    )
    return rows_to_tuple(cursor.fetchall())


def _get_modules_for_files(
    conn: sqlite3.Connection,
    file_ids: Tuple[int, ...],
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get distinct modules that own the given files via module_files."""
    if not file_ids:
        return ()
    placeholders = ','.join('?' for _ in file_ids)
    cursor = conn.execute(
        f"SELECT DISTINCT m.id, m.name, m.path, m.purpose "
        f"FROM modules m "
        f"JOIN module_files mf ON mf.module_id = m.id "
        f"WHERE mf.file_id IN ({placeholders})",
        file_ids
    )
    return rows_to_tuple(cursor.fetchall())


def _get_interactions_for_functions(
    conn: sqlite3.Connection,
    function_ids: Tuple[int, ...],
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get interactions involving any of the given function IDs."""
    if not function_ids:
        return ()
    placeholders = ','.join('?' for _ in function_ids)
    cursor = conn.execute(
        f"SELECT * FROM interactions "
        f"WHERE source_function_id IN ({placeholders}) "
        f"OR target_function_id IN ({placeholders})",
        function_ids + function_ids
    )
    return rows_to_tuple(cursor.fetchall())


def _get_notes_for_task(
    conn: sqlite3.Connection,
    task_type: str,
    task_id: int,
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get notes referencing a task/subtask/sidequest."""
    ref_table = TASK_TABLE_MAP.get(task_type)
    if ref_table is None:
        return ()

    cursor = conn.execute(
        "SELECT * FROM notes WHERE reference_table = ? AND reference_id = ? "
        "ORDER BY created_at DESC",
        (ref_table, task_id)
    )
    return rows_to_tuple(cursor.fetchall())
