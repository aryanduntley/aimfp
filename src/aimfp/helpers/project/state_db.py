"""
AIMFP Helper Functions - State Database

Creates the per-project state database (runtime.db).
The state database replaces mutable global variables with database-backed state,
maintaining FP compliance while supporting runtime mutable state needs.

The state database lives at <source-dir>/.state/runtime.db and is created
during project_discovery when source_directory is confirmed.

Helpers in this file:
- create_state_database: Create .state/ directory and runtime.db using SQL schema
- get_state_operations_template: Return the Python state_operations template content
"""

import os
import sqlite3
from pathlib import Path

from ..utils import get_return_statements
from ...database.connection import Result


# ============================================================================
# Pure Helper Functions
# ============================================================================

def _get_state_schema_path() -> str:
    """Pure: Get path to state_db.sql schema file."""
    return str(
        Path(__file__).parent.parent.parent / "database" / "initialization" / "state_db.sql"
    )


def _get_state_dir(source_directory: str) -> str:
    """Pure: Get .state/ directory path for a source directory."""
    return str(Path(source_directory) / ".state")


def _get_state_db_path(source_directory: str) -> str:
    """Pure: Get runtime.db path for a source directory."""
    return str(Path(source_directory) / ".state" / "runtime.db")


def _get_templates_dir() -> Path:
    """Pure: Get path to state_db templates directory."""
    return Path(__file__).parent.parent.parent / "templates" / "state_db"


# ============================================================================
# Public API Functions
# ============================================================================

def create_state_database(source_directory: str) -> Result:
    """
    Create the state database for a project.

    Creates <source-dir>/.state/ directory and runtime.db using the
    state_db.sql schema. Safe to call multiple times — returns success
    if database already exists.

    Args:
        source_directory: Absolute path to project source directory
            (e.g., the value from infrastructure.source_directory)

    Returns:
        Result with data={
            created: bool (True if newly created, False if already existed),
            db_path: str,
            state_dir: str
        }
    """
    state_dir = _get_state_dir(source_directory)
    db_path = _get_state_db_path(source_directory)

    # Already exists — idempotent success
    if os.path.exists(db_path):
        return Result(
            success=True,
            data={
                'created': False,
                'db_path': db_path,
                'state_dir': state_dir,
            },
            return_statements=get_return_statements("create_state_database"),
        )

    # Read schema SQL
    schema_path = _get_state_schema_path()
    if not os.path.isfile(schema_path):
        return Result(
            success=False,
            error=f"State database schema not found: {schema_path}",
        )

    try:
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
    except OSError as e:
        return Result(
            success=False,
            error=f"Failed to read schema: {str(e)}",
        )

    # Create directory and database
    try:
        os.makedirs(state_dir, exist_ok=True)

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(schema_sql)
        finally:
            conn.close()

        return Result(
            success=True,
            data={
                'created': True,
                'db_path': db_path,
                'state_dir': state_dir,
            },
            return_statements=get_return_statements("create_state_database"),
        )

    except Exception as e:
        return Result(
            success=False,
            error=f"Failed to create state database: {str(e)}",
        )


def get_state_operations_template() -> Result:
    """
    Return the Python state_operations.py template content.

    The template is a Python reference implementation of CRUD helpers for
    the state database (runtime.db). AI should use this as a reference to
    create a language-appropriate state_operations file in the project's
    <source-dir>/.state/ directory.

    No parameters — reads from the bundled template file.

    Returns:
        Result with data={
            template_content: str (full file content),
            functions: list[str] (exported function names),
            target_path: str (where AI should write the adapted file)
        }
    """
    template_path = _get_templates_dir() / "state_operations.py"

    if not template_path.is_file():
        return Result(
            success=False,
            error=f"State operations template not found: {template_path}",
        )

    try:
        content = template_path.read_text()

        return Result(
            success=True,
            data={
                'template_content': content,
                'functions': ['set_var', 'get_var', 'delete_var', 'increment_var', 'list_vars'],
                'target_path': '<source-dir>/.state/state_operations.<ext>',
            },
            return_statements=get_return_statements("get_state_operations_template"),
        )

    except OSError as e:
        return Result(
            success=False,
            error=f"Failed to read template: {str(e)}",
        )
