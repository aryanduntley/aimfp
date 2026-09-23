"""
AIMFP Helper Functions - Project Validation

Schema validation and CHECK constraint extraction for project database.
Parses schema SQL to extract allowed values from CHECK constraints.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- project_allowed_check_constraints: Get allowed values from CHECK constraint
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple
from ..utils import get_return_statements, _parse_check_constraint

# Import common project utilities (DRY principle)
from ._common import _open_connection, get_cached_project_root, _open_project_connection


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class CheckConstraintResult:
    """Result of CHECK constraint query."""
    success: bool
    values: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# CHECK parsing is shared: database/connection.py _parse_check_constraint.

# ============================================================================
# Effect Functions - Database Operations
# ============================================================================

def _get_table_schema(conn: sqlite3.Connection, table_name: str) -> Optional[str]:
    """
    Effect: Get CREATE TABLE SQL for a table.

    Args:
        conn: Database connection
        table_name: Table name

    Returns:
        CREATE TABLE SQL or None if table not found
    """
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    row = cursor.fetchone()
    return row['sql'] if row else None


def _check_field_exists(conn: sqlite3.Connection, table_name: str, field_name: str) -> bool:
    """
    Effect: Check if field exists in table.

    Args:
        conn: Database connection
        table_name: Table name
        field_name: Field name

    Returns:
        True if field exists, False otherwise
    """
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    rows = cursor.fetchall()
    field_names = [row['name'] for row in rows]
    return field_name in field_names


# ============================================================================
# Public Helper Functions
# ============================================================================

def project_allowed_check_constraints(
    table: str,
    field: str,
    project_root: Optional[str] = None
) -> CheckConstraintResult:
    """
    Returns list of allowed values for CHECK constraint enum fields in project.db.
    Parses schema to extract CHECK(field IN (...)) constraints.

    Args:
        table: Table name to check for CHECK constraints
        field: Field name to get allowed values for

    Returns:
        CheckConstraintResult with allowed values or error
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if table exists
        table_schema = _get_table_schema(conn, table)
        if table_schema is None:
            conn.close()
            return CheckConstraintResult(
                success=False,
                error=f"Table {table} not found in project database"
            )

        # Check if field exists
        if not _check_field_exists(conn, table, field):
            conn.close()
            return CheckConstraintResult(
                success=False,
                error=f"Field {field} not found in table {table}"
            )

        # Extract CHECK constraint values
        values = _parse_check_constraint(table_schema, field)
        if values is None:
            conn.close()
            return CheckConstraintResult(
                success=False,
                error=f"Field {field} does not have a CHECK constraint list"
            )

        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("project_allowed_check_constraints")

        return CheckConstraintResult(
            success=True,
            values=values,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return CheckConstraintResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
