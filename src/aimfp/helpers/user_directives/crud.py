"""
AIMFP Helper Functions - User Directives CRUD

Generic Create, Read, Update, Delete operations for user_directives database.
Includes convenience queries for common directive lookups.
For specialized operations, see management.py.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- get_from_user_custom: Get records by ID(s)
- get_from_user_custom_where: Flexible filtering with structured conditions
- query_user_custom: Execute complex SQL WHERE clause
- add_user_custom_entry: Add entry to any table
- update_user_custom_entry: Update entry in any table
- delete_user_custom_entry: Delete entry from any table
- get_active_user_directives: Get all directives with status='active'
- search_user_directives: Search directives with multiple optional filters
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any, List

from ..utils import get_return_statements, rows_to_tuple

# Import common user_directives utilities (DRY principle)
from ._common import (
    get_cached_project_root,
    _open_directives_connection,
    _table_exists,
    _record_exists,
)

# The insert-time gate. trigger_config is the one column where a bad value
# fails SILENTLY - a schedule that never fires writes no log line and records
# no error - so it is checked before it is stored rather than at dispatch.
from .validation import (
    _reject_invalid_action_config,
    _reject_invalid_trigger_config,
)


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
# Public Helper Functions - Generic CRUD
# ============================================================================

def get_from_user_custom(
    table: str,
    id_array: List[int]
) -> QueryResult:
    """
    Get records by ID(s) from user_directives database.

    Args:
        table: Table name in user_directives.db
        id_array: List of IDs to fetch (MUST contain at least one ID)

    Returns:
        QueryResult with matching rows

    Note:
        Empty id_array returns error - use query_user_custom for fetching all records.
    """
    # Validate id_array is not empty
    if not id_array:
        return QueryResult(
            success=False,
            error="id_array must contain at least one ID"
        )

    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

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
            return_statements=get_return_statements("get_from_user_custom")
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_from_user_custom_where(
    table: str,
    conditions: Dict[str, Any]
) -> QueryResult:
    """
    Flexible filtering with structured JSON conditions.
    All conditions are ANDed together.

    Args:
        table: Table name in user_directives.db
        conditions: Field-value pairs (AND logic)

    Returns:
        QueryResult with matching rows

    Example:
        >>> result = get_from_user_custom_where(
        ...     "user_directives",
        ...     {"status": "active", "trigger_type": "time"}
        ... )
    """
    if not conditions:
        return QueryResult(
            success=False,
            error="conditions must not be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

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
            return_statements=get_return_statements("get_from_user_custom_where")
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def query_user_custom(
    table: str,
    where_clause: Optional[str] = None
) -> QueryResult:
    """
    Execute complex SQL WHERE clause (advanced, rare use).

    Args:
        table: Table name in user_directives.db
        where_clause: Optional SQL WHERE clause (without 'WHERE' keyword)

    Returns:
        QueryResult with matching rows

    Example:
        >>> result = query_user_custom(
        ...     "user_directives",
        ...     "status IN ('active', 'paused') AND domain = 'home_automation'"
        ... )
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

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
            return_statements=get_return_statements("query_user_custom")
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def add_user_custom_entry(
    table: str,
    data: Dict[str, Any]
) -> MutationResult:
    """
    Add entry to user_directives database table.

    Args:
        table: Table name in user_directives.db
        data: Field-value pairs to insert

    Returns:
        MutationResult with new record ID

    Example:
        >>> result = add_user_custom_entry(
        ...     "user_directives",
        ...     {"name": "lights_off", "trigger_type": "time", "action_type": "api_call",
        ...      "status": "pending_validation"}
        ... )
    """
    if not data:
        return MutationResult(
            success=False,
            error="data must not be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Gate: a non-conforming trigger_config must never reach the column.
        if table == 'user_directives':
            rejection = _reject_invalid_trigger_config(
                data.get('trigger_type'), data.get('trigger_config'))
            if rejection is None:
                rejection = _reject_invalid_action_config(
                    data.get('action_type'), data.get('action_config'))
            if rejection is not None:
                conn.close()
                return MutationResult(success=False, error=rejection)

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

        return_stmts = get_return_statements("add_user_custom_entry")

        return MutationResult(
            success=True,
            id=new_id,
            message=f"Entry added to {table}",
            return_statements=return_stmts
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


def _resolved_type(
    conn: sqlite3.Connection,
    data: Dict[str, Any],
    record_id: int,
    column: str
) -> Optional[str]:
    """
    Effect: The type an update should be validated against.

    A partial update that changes a config without restating its type must be
    checked against the type already stored, not waved through for lack of
    one - otherwise the easiest hole stays open: update just the config and
    the gate never fires.
    """
    if data.get(column) is not None:
        return data[column]
    stored = conn.execute(
        f"SELECT {column} FROM user_directives WHERE id = ?", (record_id,)
    ).fetchone()
    return stored[column] if stored else None


def update_user_custom_entry(
    table: str,
    record_id: int,
    data: Dict[str, Any]
) -> MutationResult:
    """
    Update entry in user_directives database table.

    Args:
        table: Table name in user_directives.db
        record_id: ID of record to update
        data: Field-value pairs to update

    Returns:
        MutationResult with success/error

    Example:
        >>> result = update_user_custom_entry(
        ...     "user_directives",
        ...     1,
        ...     {"status": "validated", "validated_content": "{...}"}
        ... )
    """
    if not data:
        return MutationResult(
            success=False,
            error="data must not be empty"
        )

    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

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

        # Same gate on update: turning a conforming config into a broken one
        # is the same silent failure as writing one. A partial update that
        # carries trigger_config without trigger_type is checked against the
        # trigger_type already stored, rather than being waved through.
        if table == 'user_directives':
            rejection = None
            if 'trigger_config' in data:
                rejection = _reject_invalid_trigger_config(
                    _resolved_type(conn, data, record_id, 'trigger_type'),
                    data.get('trigger_config'))
            if rejection is None and 'action_config' in data:
                rejection = _reject_invalid_action_config(
                    _resolved_type(conn, data, record_id, 'action_type'),
                    data.get('action_config'))
            if rejection is not None:
                conn.close()
                return MutationResult(success=False, error=rejection)

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
            return_statements=get_return_statements("update_user_custom_entry")
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


def delete_user_custom_entry(
    table: str,
    record_id: int
) -> MutationResult:
    """
    Delete entry from user_directives database table.

    Args:
        table: Table name in user_directives.db
        record_id: ID of record to delete

    Returns:
        MutationResult with success/error

    Example:
        >>> result = delete_user_custom_entry(
        ...     "user_directives",
        ...     5
        ... )
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

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
            return_statements=get_return_statements("delete_user_custom_entry")
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# Public Helper Functions - Convenience Queries
# ============================================================================

def get_active_user_directives() -> QueryResult:
    """
    Get all user directives with status='active'.
    Convenience query for checking active directive count.

    Returns:
        QueryResult with active directive rows

    Example:
        >>> result = get_active_user_directives()
        >>> len(result.rows)  # Number of active directives
        3
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        cursor = conn.execute(
            "SELECT * FROM user_directives WHERE status = 'active' ORDER BY name"
        )
        rows = rows_to_tuple(cursor.fetchall())
        conn.close()

        return_stmts = get_return_statements("get_active_user_directives")

        return QueryResult(
            success=True,
            rows=rows,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def search_user_directives(
    name: Optional[str] = None,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    trigger_type: Optional[str] = None,
    source_file: Optional[str] = None
) -> QueryResult:
    """
    Search user directives with optional filters. At least one filter required.

    Args:
        name: Filter by directive name (LIKE match, case-insensitive)
        domain: Filter by domain (exact match)
        status: Filter by status (exact match)
        trigger_type: Filter by trigger_type (exact match)
        source_file: Filter by source_file path (LIKE match)

    Returns:
        QueryResult with matching directive rows

    Example:
        >>> result = search_user_directives(
        ...     status="active",
        ...     trigger_type="time"
        ... )
    """
    # Require at least one filter
    if all(v is None for v in [name, domain, status, trigger_type, source_file]):
        return QueryResult(
            success=False,
            error="At least one filter parameter required"
        )

    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        where_parts = []
        values = []

        # LIKE filters (case-insensitive)
        if name is not None:
            where_parts.append("name LIKE ?")
            values.append(f"%{name}%")

        if source_file is not None:
            where_parts.append("source_file LIKE ?")
            values.append(f"%{source_file}%")

        # Exact match filters
        if domain is not None:
            where_parts.append("domain = ?")
            values.append(domain)

        if status is not None:
            where_parts.append("status = ?")
            values.append(status)

        if trigger_type is not None:
            where_parts.append("trigger_type = ?")
            values.append(trigger_type)

        where_clause = " AND ".join(where_parts)
        query = f"SELECT * FROM user_directives WHERE {where_clause} ORDER BY name"

        cursor = conn.execute(query, values)
        rows = rows_to_tuple(cursor.fetchall())
        conn.close()

        return_stmts = get_return_statements("search_user_directives")

        return QueryResult(
            success=True,
            rows=rows,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
