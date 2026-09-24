"""
AIMFP Helper Utilities - Re-exports from Database Foundation Layer

This module re-exports everything from database/connection.py for
backward compatibility. All helper category files import from here.

DRY hierarchy:
    database/connection.py  (source of truth)
        └── helpers/utils.py (THIS FILE — re-exports)
            └── {category}/_common.py (category re-exports)
                └── {category}/{file}.py (individual helpers)

New code outside helpers/ should import directly from
aimfp.database.connection instead of going through this file.
"""

# Re-export everything from the foundation layer
from ..database.connection import (
    # Constants
    AIMFP_PROJECT_DIR,
    CORE_DB_NAME,
    PROJECT_DB_NAME,
    USER_PREFERENCES_DB_NAME,
    USER_DIRECTIVES_DB_NAME,
    MCP_RUNTIME_DB_NAME,
    # Result types
    Result,
    QueryResult,
    SchemaResult,
    # Path resolution — global
    get_core_db_path,
    get_mcp_runtime_db_path,
    # Path resolution — per-project
    get_aimfp_project_dir,
    get_project_db_path,
    get_user_preferences_db_path,
    get_user_directives_db_path,
    database_exists,
    # Project folder override (hook F)
    ProjectDirOverride,
    normalize_project_dir_path,
    set_project_dir_override,
    clear_project_dir_override,
    get_project_dir_override,
    get_project_dir_name,
    resolve_project_relative,
    # Project root cache
    set_project_root,
    get_cached_project_root,
    resolve_project_root,
    clear_project_root_cache,
    _discover_project_root,
    # Connection management
    DEFAULT_BUSY_TIMEOUT,
    _open_connection,
    _close_connection,
    _open_core_connection,
    _open_project_connection,
    _open_preferences_connection,
    _open_directives_connection,
    _open_mcp_runtime_connection,
    # Stateless query functions
    _effect_query_one,
    _effect_query_all,
    _effect_execute,
    # Row conversion
    row_to_dict,
    rows_to_tuple,
    # JSON parsing
    parse_json_field,
    json_to_tuple,
    # Schema introspection
    _get_table_names,
    _get_table_info,
    _get_table_sql,
    _parse_check_constraint,
    # Return statements
    get_return_statements,
)

__all__ = [
    # Constants
    'AIMFP_PROJECT_DIR',
    'CORE_DB_NAME',
    'PROJECT_DB_NAME',
    'USER_PREFERENCES_DB_NAME',
    'USER_DIRECTIVES_DB_NAME',
    'MCP_RUNTIME_DB_NAME',
    # Result types
    'Result',
    'QueryResult',
    'SchemaResult',
    # Path resolution
    'get_core_db_path',
    'get_mcp_runtime_db_path',
    'get_aimfp_project_dir',
    'get_project_db_path',
    'get_user_preferences_db_path',
    'get_user_directives_db_path',
    'database_exists',
    # Project folder override (hook F)
    'ProjectDirOverride',
    'normalize_project_dir_path',
    'set_project_dir_override',
    'clear_project_dir_override',
    'get_project_dir_override',
    'get_project_dir_name',
    'resolve_project_relative',
    # Project root cache
    'set_project_root',
    'get_cached_project_root',
    'resolve_project_root',
    'clear_project_root_cache',
    '_discover_project_root',
    # Connection management
    'DEFAULT_BUSY_TIMEOUT',
    '_open_connection',
    '_close_connection',
    '_open_core_connection',
    '_open_project_connection',
    '_open_preferences_connection',
    '_open_directives_connection',
    '_open_mcp_runtime_connection',
    # Stateless query functions
    '_effect_query_one',
    '_effect_query_all',
    '_effect_execute',
    # Row conversion
    'row_to_dict',
    'rows_to_tuple',
    # JSON parsing
    'parse_json_field',
    'json_to_tuple',
    # Schema introspection
    '_get_table_names',
    '_get_table_info',
    '_get_table_sql',
    '_parse_check_constraint',
    # Return statements
    'get_return_statements',
]
