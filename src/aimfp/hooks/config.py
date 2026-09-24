"""
AIMFP Hooks - Database Seam and Logging Configuration

The hook package's only contact with database plumbing. Two jobs:

1. Answer whether this project even has user directives (Use Case 2), so a
   caller can branch without provoking an error. Most projects are Use Case 1
   and the honest answer is simply False.

2. Resolve the logging_config row into an immutable LogConfig once per hook
   call, falling back to schema defaults whenever anything is missing.

project_root is always an explicit parameter here, never the process-global
cache in database/connection.py. The caller is a scheduler firing from cron or
systemd with cwd pointing anywhere, so cwd-based discovery would resolve the
wrong project or none at all.

Import weight is part of the contract: this module reaches only into
database/connection.py, which is stdlib-only. Nothing here may import the
watchdog, the directive loader, or the MCP server.
"""

import sqlite3
from typing import Any, Optional

from ..database.connection import (
    _close_connection,
    _open_connection,
    database_exists,
    get_user_directives_db_path,
    resolve_project_relative,
)
from .results import LogConfig


# ============================================================================
# Availability
# ============================================================================

def hooks_available(project_root: str) -> bool:
    """
    Report whether this project has a user_directives.db at all.

    False for every Use Case 1 project, which is the normal case and not an
    error condition.

    Args:
        project_root: Absolute path to the project root

    Returns:
        True if user_directives.db exists for this project
    """
    return database_exists(get_user_directives_db_path(project_root))


# ============================================================================
# Logging Configuration
# ============================================================================

def load_log_config(project_root: str) -> LogConfig:
    """
    Read the single logging_config row into an immutable LogConfig.

    Resolved once per hook call rather than per write. Returns schema
    defaults when the row, the table, or the whole database is missing -
    logging must never be the reason an automation run fails.

    Args:
        project_root: Absolute path to the project root

    Returns:
        LogConfig, always usable, defaults when nothing could be read
    """
    row = _effect_read_log_config_row(project_root)
    if row is None:
        return LogConfig()
    return _row_to_log_config(row)


def _row_to_log_config(row: Any) -> LogConfig:
    """
    Pure: Map a logging_config row onto LogConfig.

    Every field falls back to the dataclass default when absent or NULL, so a
    schema that has gained columns ahead of this code still loads.
    """
    defaults = LogConfig()

    def pick(column: str, fallback: Any) -> Any:
        value = _row_value(row, column)
        return fallback if value is None else value

    return LogConfig(
        execution_logs_enabled=bool(pick(
            'execution_logs_enabled', defaults.execution_logs_enabled)),
        execution_log_rotation=str(pick(
            'execution_log_rotation', defaults.execution_log_rotation)),
        execution_log_retention_days=int(pick(
            'execution_log_retention_days', defaults.execution_log_retention_days)),
        execution_log_compress_after_days=int(pick(
            'execution_log_compress_after_days',
            defaults.execution_log_compress_after_days)),
        execution_log_max_size_mb=int(pick(
            'execution_log_max_size_mb', defaults.execution_log_max_size_mb)),
        error_logs_enabled=bool(pick(
            'error_logs_enabled', defaults.error_logs_enabled)),
        error_log_rotation=str(pick(
            'error_log_rotation', defaults.error_log_rotation)),
        error_log_retention_days=int(pick(
            'error_log_retention_days', defaults.error_log_retention_days)),
        error_log_max_size_mb=int(pick(
            'error_log_max_size_mb', defaults.error_log_max_size_mb)),
        error_log_format=str(pick(
            'error_log_format', defaults.error_log_format)),
        store_execution_statistics=bool(pick(
            'store_execution_statistics', defaults.store_execution_statistics)),
        store_last_error_only=bool(pick(
            'store_last_error_only', defaults.store_last_error_only)),
        execution_log_dir=str(pick(
            'execution_log_dir', defaults.execution_log_dir)),
        error_log_dir=str(pick(
            'error_log_dir', defaults.error_log_dir)),
        lifecycle_log_path=str(pick(
            'lifecycle_log_path', defaults.lifecycle_log_path)),
    )


def _row_value(row: Any, column: str) -> Optional[Any]:
    """Pure: Read one column from a sqlite3.Row, None when it has no such key."""
    try:
        return row[column]
    except (IndexError, KeyError):
        return None


def resolve_log_path(project_root: str, configured: str) -> str:
    """
    Effect: Resolve a configured log location against the project root.

    logging_config stores project-relative paths by default
    ('.aimfp-project/logs/execution/'), but an absolute path set by a user
    must be honored as given. The default folder re-roots onto the root's
    actual project folder, so a project-dir override holds
    (resolve_project_relative).

    Args:
        project_root: Absolute path to the project root
        configured: Path from logging_config, relative or absolute

    Returns:
        Absolute path
    """
    return resolve_project_relative(project_root, configured)


# ============================================================================
# Effects
# ============================================================================

def _effect_read_log_config_row(project_root: str) -> Optional[Any]:
    """
    Effect: Fetch the single logging_config row.

    Returns None - never raises - when the database is absent, the table does
    not exist, or the row was never inserted. Callers fall back to defaults.
    """
    db_path = get_user_directives_db_path(project_root)
    if not database_exists(db_path):
        return None

    conn = None
    try:
        conn = _open_connection(db_path)
        cursor = conn.execute("SELECT * FROM logging_config WHERE id = 1")
        return cursor.fetchone()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            _close_connection(conn)
