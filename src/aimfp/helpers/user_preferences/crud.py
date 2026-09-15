"""
AIMFP Helper Functions - User Preferences CRUD

Generic Create, Read, Update, Delete operations for user_preferences database.
These are generic table operations - for specialized operations, see management.py.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- get_from_settings: Get records by ID(s)
- get_from_settings_where: Flexible filtering with structured conditions
- query_settings: Execute complex SQL WHERE clause
- add_settings_entry: Add entry to any table
- update_settings_entry: Update entry in any table
- delete_settings_entry: Delete entry from any table
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, List

from ..utils import get_return_statements, rows_to_tuple

# Import common user_preferences utilities (DRY principle)
from ._common import _open_connection, get_cached_project_root, _open_preferences_connection


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class QueryResult:
    """Result of query operations."""
    success: bool
    rows: Tuple[Dict[str, Any], ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MutationResult:
    """Result of add/update/delete operations."""
    success: bool
    id: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Effect Functions - Database Operations
# ============================================================================

def _query_tables(conn: sqlite3.Connection) -> Tuple[str, ...]:
    """
    Effect: Query all table names from database.

    Args:
        conn: Database connection

    Returns:
        Tuple of table names
    """
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = cursor.fetchall()
    return tuple(row['name'] for row in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """
    Effect: Check if table exists.

    Args:
        conn: Database connection
        table: Table name

    Returns:
        True if exists, False otherwise
    """
    tables = _query_tables(conn)
    return table in tables


def _record_exists(conn: sqlite3.Connection, table: str, record_id: int) -> bool:
    """
    Effect: Check if record with ID exists.

    Args:
        conn: Database connection
        table: Table name
        record_id: Record ID

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (record_id,))
    return cursor.fetchone() is not None


# ============================================================================
# Public Helper Functions
# ============================================================================

def get_from_settings(
    table: str,
    id_array: List[int]
) -> QueryResult:
    """
    Get records by ID(s) from user_preferences database.

    Args:
        table: Table name
        id_array: List of IDs to fetch (MUST contain at least one ID)

    Returns:
        QueryResult with matching rows

    Note:
        Empty id_array returns error - use query_settings for fetching all records.
    """
    # Validate id_array is not empty
    if not id_array:
        return QueryResult(
            success=False,
            error="id_array must contain at least one ID"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
            conn.close()
            return QueryResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Build query with placeholders
        placeholders = ','.join('?' * len(id_array))
        cursor = conn.execute(
            f"SELECT * FROM {table} WHERE id IN ({placeholders})",
            id_array
        )
        rows = rows_to_tuple(cursor.fetchall())
        conn.close()

        return QueryResult(
            success=True,
            rows=rows,
            return_statements=get_return_statements("get_from_settings")
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_from_settings_where(
    table: str,
    conditions: Dict[str, Any]
) -> QueryResult:
    """
    Flexible filtering with structured JSON conditions.
    All conditions are ANDed together.

    Args:
        table: Table name
        conditions: Field-value pairs (AND logic)

    Returns:
        QueryResult with matching rows

    Example:
        >>> result = get_from_settings_where(
        ...     "directive_preferences",
        ...     {"directive_name": "project_file_write", "active": 1}
        ... )
    """
    if not conditions:
        return QueryResult(
            success=False,
            error="conditions must not be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
            conn.close()
            return QueryResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Build WHERE clause
        where_parts = []
        values = []
        for field, value in conditions.items():
            where_parts.append(f"{field} = ?")
            values.append(value)

        where_clause = " AND ".join(where_parts)
        query = f"SELECT * FROM {table} WHERE {where_clause}"

        cursor = conn.execute(query, values)
        rows = rows_to_tuple(cursor.fetchall())
        conn.close()

        return QueryResult(
            success=True,
            rows=rows,
            return_statements=get_return_statements("get_from_settings_where")
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def query_settings(
    table: str,
    where_clause: Optional[str] = None
) -> QueryResult:
    """
    Execute complex SQL WHERE clause (advanced, rare use).

    Args:
        table: Table name
        where_clause: Optional SQL WHERE clause (without 'WHERE' keyword)

    Returns:
        QueryResult with matching rows

    Example:
        >>> result = query_settings(
        ...     "directive_preferences",
        ...     "directive_name = 'project_file_write' AND active = 1"
        ... )
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
            conn.close()
            return QueryResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Build query
        if where_clause:
            query = f"SELECT * FROM {table} WHERE {where_clause}"
        else:
            query = f"SELECT * FROM {table}"

        cursor = conn.execute(query)
        rows = rows_to_tuple(cursor.fetchall())
        conn.close()

        return QueryResult(
            success=True,
            rows=rows,
            return_statements=get_return_statements("query_settings")
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def add_settings_entry(
    table: str,
    data: Dict[str, Any]
) -> MutationResult:
    """
    Add entry to user_preferences database table.

    Args:
        table: Table name
        data: Field-value pairs to insert

    Returns:
        MutationResult with new record ID

    Example:
        >>> result = add_settings_entry(
        ...     "user_settings",
        ...     {"setting_key": "theme", "setting_value": "dark", "scope": "project"}
        ... )
    """
    if not data:
        return MutationResult(
            success=False,
            error="data must not be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Build INSERT query
        fields = list(data.keys())
        placeholders = ','.join('?' * len(fields))
        fields_str = ','.join(fields)
        values = list(data.values())

        cursor = conn.execute(
            f"INSERT INTO {table} ({fields_str}) VALUES ({placeholders})",
            values
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        return MutationResult(
            success=True,
            id=new_id,
            message=f"Entry added to {table}",
            return_statements=get_return_statements("add_settings_entry")
        )

    except sqlite3.IntegrityError as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Integrity error: {str(e)}"
        )
    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_settings_entry(
    table: str,
    record_id: int,
    data: Dict[str, Any]
) -> MutationResult:
    """
    Update entry in user_preferences database table.

    Args:
        table: Table name
        record_id: ID of record to update
        data: Field-value pairs to update

    Returns:
        MutationResult with success/error

    Example:
        >>> result = update_settings_entry(
        ...     "user_settings",
        ...     1,
        ...     {"setting_value": "light"}
        ... )
    """
    if not data:
        return MutationResult(
            success=False,
            error="data must not be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Check if record exists
        if not _record_exists(conn, table, record_id):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Record with id {record_id} not found in {table}"
            )

        # Build UPDATE query
        set_parts = [f"{field} = ?" for field in data.keys()]
        set_clause = ', '.join(set_parts)
        values = list(data.values()) + [record_id]

        conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ?",
            values
        )
        conn.commit()
        conn.close()

        return MutationResult(
            success=True,
            id=record_id,
            message=f"Entry updated in {table}",
            return_statements=get_return_statements("update_settings_entry")
        )

    except sqlite3.IntegrityError as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Integrity error: {str(e)}"
        )
    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def delete_settings_entry(
    table: str,
    record_id: int
) -> MutationResult:
    """
    Delete entry from user_preferences database table.

    Args:
        table: Table name
        record_id: ID of record to delete

    Returns:
        MutationResult with success/error

    Example:
        >>> result = delete_settings_entry(
        ...     "tracking_notes",
        ...     42
        ... )
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Check if record exists
        if not _record_exists(conn, table, record_id):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Record with id {record_id} not found in {table}"
            )

        conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
        conn.commit()
        conn.close()

        return MutationResult(
            success=True,
            id=record_id,
            message=f"Entry deleted from {table}",
            return_statements=get_return_statements("delete_settings_entry")
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
