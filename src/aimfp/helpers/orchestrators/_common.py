"""
AIMFP Helper Functions - Orchestrators Common Utilities

Shared utilities used across orchestrator helper files.

DRY hierarchy:
    database/connection.py  (foundation — source of truth)
        └── helpers/utils.py (re-exports)
            └── orchestrators/_common.py (THIS FILE — orchestrator constants + re-exports)
                └── orchestrators/{file}.py (individual orchestrator helpers)

Orchestrator characteristics:
- Aggregate data from multiple tables (and sometimes multiple databases)
- Return structured data for AI to interpret — NO decision logic
- Entry points (aimfp_init, aimfp_run, aimfp_status) coordinate across databases
- Status/query helpers work within project.db only

This file provides:
- Re-exports from utils.py (which re-exports from database/connection.py)
- Orchestrator-specific constants (action maps, valid types, JOIN mappings)
"""

import sqlite3
from typing import Dict, Final

# Import from utils.py (which re-exports from database/connection.py)
from ..utils import (
    DEFAULT_BUSY_TIMEOUT,
    _open_connection,
    _close_connection,
    get_core_db_path,
    get_project_db_path,
    get_user_preferences_db_path,
    get_user_directives_db_path,
    get_aimfp_project_dir,
    get_return_statements,
    database_exists,
    _get_table_names,
    row_to_dict,
    rows_to_tuple,
    parse_json_field,
    json_to_tuple,
    Result,
    QueryResult,
    AIMFP_PROJECT_DIR,
    CORE_DB_NAME,
    PROJECT_DB_NAME,
    USER_PREFERENCES_DB_NAME,
    USER_DIRECTIVES_DB_NAME,
    # Project root cache (set by orchestrators, read by helpers)
    set_project_root,
    get_cached_project_root,
    resolve_project_root,
    clear_project_root_cache,
    _discover_project_root,
    # Connection openers now come from foundation layer
    _open_project_connection,
    _open_preferences_connection,
    _open_directives_connection,
)

# Re-export for convenience — orchestrator files import from _common only
__all__ = [
    # Global utilities (from database/connection.py via utils.py)
    'DEFAULT_BUSY_TIMEOUT',
    '_open_connection',
    '_close_connection',
    'get_core_db_path',
    'get_project_db_path',
    'get_user_preferences_db_path',
    'get_user_directives_db_path',
    'get_aimfp_project_dir',
    'get_return_statements',
    'database_exists',
    '_get_table_names',
    'row_to_dict',
    'rows_to_tuple',
    'parse_json_field',
    'json_to_tuple',
    'Result',
    'QueryResult',
    # Constants
    'AIMFP_PROJECT_DIR',
    'CORE_DB_NAME',
    'PROJECT_DB_NAME',
    'USER_PREFERENCES_DB_NAME',
    'USER_DIRECTIVES_DB_NAME',
    'BLUEPRINT_FILENAME',
    'BACKUPS_DIR_NAME',
    # Project root cache (set by orchestrators, read by helpers)
    'set_project_root',
    'get_cached_project_root',
    'resolve_project_root',
    'clear_project_root_cache',
    '_discover_project_root',
    # Connection openers (from foundation layer)
    '_open_project_connection',
    '_open_preferences_connection',
    '_open_directives_connection',
    # Orchestrator-specific constants
    'VALID_STATUS_TYPES',
    'VALID_TASK_TYPES',
    'TASK_TABLE_MAP',
    'VALID_STATE_ACTIONS',
    'ACTION_STATUS_MAP',
    'ACTION_TARGET_MAP',
    'VALID_QUERY_ENTITIES',
    'JOIN_MAPPINGS',
    'VALID_PROGRESS_SCOPES',
]


# ============================================================================
# Orchestrator Constants
# ============================================================================

# File/directory names within .aimfp-project/
BLUEPRINT_FILENAME: Final[str] = "ProjectBlueprint.md"
BACKUPS_DIR_NAME: Final[str] = "backups"

# Valid status type parameters for orchestrators
VALID_STATUS_TYPES: Final[frozenset[str]] = frozenset([
    'quick', 'summary', 'detailed'
])

# Valid task types (task, subtask, sidequest)
VALID_TASK_TYPES: Final[frozenset[str]] = frozenset([
    'task', 'subtask', 'sidequest'
])

# Task type to table name mapping
TASK_TABLE_MAP: Final[Dict[str, str]] = {
    'task': 'tasks',
    'subtask': 'subtasks',
    'sidequest': 'sidequests',
    'item': 'items',
}

# Valid actions for update_project_state
VALID_STATE_ACTIONS: Final[frozenset[str]] = frozenset([
    'start_task', 'complete_task', 'pause_task', 'resume_task',
    'block_task', 'unblock_task',
    'start_subtask', 'complete_subtask', 'pause_subtask', 'resume_subtask',
    'block_subtask', 'unblock_subtask',
    'start_sidequest', 'complete_sidequest', 'pause_sidequest', 'resume_sidequest',
    'start_item', 'complete_item',
    'start_milestone', 'complete_milestone',
    'start_path', 'complete_path',
])

# Action to status mapping
ACTION_STATUS_MAP: Final[Dict[str, str]] = {
    'start_task': 'in_progress',
    'complete_task': 'completed',
    'pause_task': 'paused',
    'resume_task': 'in_progress',
    'block_task': 'blocked',
    'unblock_task': 'in_progress',
    'start_subtask': 'in_progress',
    'complete_subtask': 'completed',
    'pause_subtask': 'paused',
    'resume_subtask': 'in_progress',
    'block_subtask': 'blocked',
    'unblock_subtask': 'in_progress',
    'start_sidequest': 'in_progress',
    'complete_sidequest': 'completed',
    'pause_sidequest': 'paused',
    'resume_sidequest': 'in_progress',
    'start_item': 'in_progress',
    'complete_item': 'completed',
    'start_milestone': 'in_progress',
    'complete_milestone': 'completed',
    'start_path': 'in_progress',
    'complete_path': 'completed',
}

# Action to target_type mapping (extract entity type from action name)
ACTION_TARGET_MAP: Final[Dict[str, str]] = {
    'start_task': 'tasks', 'complete_task': 'tasks',
    'pause_task': 'tasks', 'resume_task': 'tasks',
    'block_task': 'tasks', 'unblock_task': 'tasks',
    'start_subtask': 'subtasks', 'complete_subtask': 'subtasks',
    'pause_subtask': 'subtasks', 'resume_subtask': 'subtasks',
    'block_subtask': 'subtasks', 'unblock_subtask': 'subtasks',
    'start_sidequest': 'sidequests', 'complete_sidequest': 'sidequests',
    'pause_sidequest': 'sidequests', 'resume_sidequest': 'sidequests',
    'start_item': 'items', 'complete_item': 'items',
    'start_milestone': 'milestones', 'complete_milestone': 'milestones',
    'start_path': 'completion_path', 'complete_path': 'completion_path',
}

# Valid entity types for query_project_state
VALID_QUERY_ENTITIES: Final[frozenset[str]] = frozenset([
    'tasks', 'subtasks', 'sidequests', 'milestones', 'completion_path',
    'files', 'functions', 'types', 'interactions',
    'themes', 'flows', 'items', 'notes',
    'infrastructure', 'work_branches', 'merge_history',
])

# Predefined JOIN mappings for query_project_state
JOIN_MAPPINGS: Final[Dict[str, Dict[str, str]]] = {
    'tasks': {
        'milestones': 'LEFT JOIN milestones ON tasks.milestone_id = milestones.id',
    },
    'subtasks': {
        'tasks': 'LEFT JOIN tasks ON subtasks.task_id = tasks.id',
    },
    'milestones': {
        'completion_path': 'LEFT JOIN completion_path ON milestones.completion_path_id = completion_path.id',
    },
    'functions': {
        'files': 'LEFT JOIN files ON functions.file_id = files.id',
    },
    # NOTE: items uses polymorphic reference_table + reference_id (not FK columns).
    # Standard LEFT JOINs don't apply. Filter by reference_table in WHERE clause instead.
    # Example: SELECT * FROM items WHERE reference_table='tasks' AND reference_id = ?
}

# Valid scope values for get_current_progress
VALID_PROGRESS_SCOPES: Final[frozenset[str]] = frozenset([
    'tasks', 'milestones', 'completion_paths', 'files', 'functions',
    'flows', 'themes', 'infrastructure', 'all',
])
