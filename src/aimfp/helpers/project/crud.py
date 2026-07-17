"""
AIMFP Helper Functions - Project CRUD

Generic CRUD operations for project database tables.
Provides flexible query and modification operations with workflow protection.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- get_from_project: Get records by ID(s)
- get_from_project_where: Flexible filtering with structured JSON conditions
- query_project: Execute complex SQL WHERE clause (advanced, rare use)
- add_project_entry: Add new entry with workflow protection
- update_project_entry: Update existing entry with reservation checks
- delete_project_entry: Smart delete with automatic routing
- delete_reserved: Delete abandoned reserved entries (escape hatch)
"""

import sqlite3
import json
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, List, Any

from ..utils import get_return_statements

# Import common project utilities (DRY principle)
from ._common import (
    _open_connection,
    get_cached_project_root,
    _open_project_connection,
    _check_entity_exists,
    _create_deletion_note
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class QueryResult:
    """Result of query operation."""
    success: bool
    records: Tuple[Dict[str, Any], ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class AddResult:
    """Result of add operation."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdateResult:
    """Result of update operation."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeleteResult:
    """Result of delete operation."""
    success: bool
    deleted_id: Optional[int] = None
    table: Optional[str] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Global Constants - Reserved Tables
# ============================================================================

from typing import Final, Optional

# Tables with reserve/finalize workflows
RESERVED_WORKFLOW_TABLES: Final[frozenset[str]] = frozenset([
    'files', 'functions', 'types'
])


# ============================================================================
# Effect Functions - Database Operations
# ============================================================================

def _query_by_ids(conn: sqlite3.Connection, table: str, ids: List[int]) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Query records by IDs.

    Args:
        conn: Database connection
        table: Table name
        ids: List of IDs

    Returns:
        Tuple of record dictionaries
    """
    placeholders = ','.join('?' * len(ids))
    query = f"SELECT * FROM {table} WHERE id IN ({placeholders})"
    cursor = conn.execute(query, ids)
    rows = cursor.fetchall()

    return tuple(dict(row) for row in rows)


def _query_where(
    conn: sqlite3.Connection,
    table: str,
    conditions: Dict[str, Any],
    limit: Optional[int],
    orderby: Optional[str]
) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Query records with WHERE conditions.

    Args:
        conn: Database connection
        table: Table name
        conditions: Field-value pairs (AND logic)
        limit: Optional result limit
        orderby: Optional sort order

    Returns:
        Tuple of record dictionaries
    """
    # Build WHERE clause
    where_clauses = []
    values = []
    for field, value in conditions.items():
        where_clauses.append(f"{field} = ?")
        values.append(value)

    query = f"SELECT * FROM {table}"
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    if orderby:
        query += f" ORDER BY {orderby}"

    if limit:
        query += f" LIMIT {limit}"

    cursor = conn.execute(query, values)
    rows = cursor.fetchall()

    return tuple(dict(row) for row in rows)


def _query_raw(conn: sqlite3.Connection, table: str, where_clause: str) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Query records with raw WHERE clause.

    Args:
        conn: Database connection
        table: Table name
        where_clause: Raw WHERE clause (without WHERE keyword)

    Returns:
        Tuple of record dictionaries
    """
    query = f"SELECT * FROM {table} WHERE {where_clause}"
    cursor = conn.execute(query)
    rows = cursor.fetchall()

    return tuple(dict(row) for row in rows)


def _insert_record(conn: sqlite3.Connection, table: str, data: Dict[str, Any]) -> int:
    """
    Effect: Insert record into table.

    Args:
        conn: Database connection
        table: Table name
        data: Field-value pairs

    Returns:
        New record ID
    """
    fields = list(data.keys())
    values = list(data.values())
    placeholders = ','.join('?' * len(values))

    query = f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})"
    cursor = conn.execute(query, values)
    conn.commit()
    return cursor.lastrowid


def _update_record(conn: sqlite3.Connection, table: str, id: int, data: Dict[str, Any]) -> None:
    """
    Effect: Update record in table.

    Args:
        conn: Database connection
        table: Table name
        id: Record ID
        data: Field-value pairs to update
    """
    set_clauses = []
    values = []
    for field, value in data.items():
        set_clauses.append(f"{field} = ?")
        values.append(value)

    values.append(id)

    query = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE id = ?"
    conn.execute(query, values)
    conn.commit()


def _delete_record(conn: sqlite3.Connection, table: str, id: int) -> None:
    """
    Effect: Delete record from table.

    Args:
        conn: Database connection
        table: Table name
        id: Record ID
    """
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (id,))
    conn.commit()


def _is_reserved(conn: sqlite3.Connection, table: str, id: int) -> bool:
    """
    Effect: Check if record is reserved.

    Args:
        conn: Database connection
        table: Table name
        id: Record ID

    Returns:
        True if reserved, False otherwise
    """
    cursor = conn.execute(f"SELECT is_reserved FROM {table} WHERE id = ?", (id,))
    row = cursor.fetchone()
    return bool(row['is_reserved']) if row else False


# ============================================================================
# Public Helper Functions
# ============================================================================

def get_from_project(table: str, id_array: List[int], project_root: Optional[str] = None) -> QueryResult:
    """
    Get records by ID(s) - EMPTY ARRAY NOT ALLOWED.

    Args:
        table: Table name
        id_array: List of IDs (must contain at least one ID)

    Returns:
        QueryResult with records (empty array if no matches)
    """
    if not id_array:
        return QueryResult(
            success=False,
            error="id_array must contain at least one ID"
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        records = _query_by_ids(conn, table, id_array)
        conn.close()

        return QueryResult(
            success=True,
            records=records
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_from_project_where(
    table: str,
    conditions: Dict[str, Any],
    limit: Optional[int] = None,
    orderby: Optional[str] = None,
    project_root: Optional[str] = None
) -> QueryResult:
    """
    Flexible filtering with structured JSON conditions.

    Args:
        table: Table name
        conditions: Field-value pairs (AND logic)
        limit: Optional maximum rows to return
        orderby: Optional sort order (e.g., "name ASC", "created_at DESC")

    Returns:
        QueryResult with records (empty array if no matches)
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        records = _query_where(conn, table, conditions, limit, orderby)
        conn.close()

        return QueryResult(
            success=True,
            records=records
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def query_project(table: str, query: str, project_root: Optional[str] = None) -> QueryResult:
    """
    Execute complex SQL WHERE clause (advanced, rare use).

    Args:
        table: Table name
        query: WHERE clause without "WHERE" keyword

    Returns:
        QueryResult with records (empty array if no matches)
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        records = _query_raw(conn, table, query)
        conn.close()

        return QueryResult(
            success=True,
            records=records
        )

    except Exception as e:
        conn.close()
        return QueryResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def add_project_entry(table: str, data: Dict[str, Any], project_root: Optional[str] = None) -> AddResult:
    """
    Add new entry to project database.

    CRITICAL WORKFLOW PROTECTION: Rejects inserts to tables with reserve/finalize workflows.
    Use reserve_file/reserve_function/reserve_type helpers instead.

    Args:
        table: Table name
        data: Field-value pairs

    Returns:
        AddResult with new ID on success
    """
    # Handle JSON string deserialization (MCP may send data as JSON string)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return AddResult(success=False, error="Invalid data: expected dict or JSON string")

    # Check for workflow protection
    if table in RESERVED_WORKFLOW_TABLES:
        helper_name = f"reserve_{table[:-1]}"  # files -> reserve_file
        return AddResult(
            success=False,
            error=f"Cannot insert directly into '{table}' table. Use {helper_name}() helper instead to maintain reserve/finalize workflow."
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        new_id = _insert_record(conn, table, data)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("add_project_entry")

        return AddResult(
            success=True,
            id=new_id,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return AddResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def update_project_entry(table: str, id: int, data: Dict[str, Any], project_root: Optional[str] = None) -> UpdateResult:
    """
    Update existing entry.

    CRITICAL WORKFLOW PROTECTION: Checks for reserved records before update.
    Use finalize_file/finalize_function/finalize_type for reserved records.

    Args:
        table: Table name
        id: Record ID
        data: Field-value pairs to update

    Returns:
        UpdateResult with success status
    """
    # Handle JSON string deserialization (MCP may send data as JSON string)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return UpdateResult(success=False, error="Invalid data: expected dict or JSON string")

    # Handle id as string (MCP may send as string)
    if isinstance(id, str):
        try:
            id = int(id)
        except (ValueError, TypeError):
            return UpdateResult(success=False, error=f"Invalid id: expected integer, got '{id}'")

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check record exists
        if not _check_entity_exists(conn, table, id):
            conn.close()
            return UpdateResult(
                success=False,
                error=f"Record ID {id} not found in table {table}"
            )

        # Check for reservation protection
        if table in RESERVED_WORKFLOW_TABLES:
            if _is_reserved(conn, table, id):
                helper_name = f"finalize_{table[:-1]}"  # files -> finalize_file
                conn.close()
                return UpdateResult(
                    success=False,
                    error=f"Cannot update reserved record. Use {helper_name}() helper to finalize the reservation first."
                )

        # Update record
        _update_record(conn, table, id, data)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("update_project_entry")

        return UpdateResult(
            success=True,
            id=id,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return UpdateResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def delete_project_entry(
    table: str,
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str,
    project_root: Optional[str] = None
) -> DeleteResult:
    """
    Smart delete with automatic routing to specialized functions when needed.

    Args:
        table: Table name
        id: Record ID
        note_reason: Deletion reason
        note_severity: Note severity ('info', 'warning', 'error')
        note_source: Note source ('ai' or 'user')
        note_type: Note type ('entry_deletion')

    Returns:
        DeleteResult with success status
    """
    # Map of tables to specialized delete helpers
    specialized_deletes = {
        'files': 'delete_file',
        'functions': 'delete_function',
        'types': 'delete_type',
        'themes': 'delete_theme',
        'flows': 'delete_flow',
        'completion_path': 'delete_completion_path',
        'milestones': 'delete_milestone',
        'tasks': 'delete_task',
        'subtasks': 'delete_subtask',
        'sidequests': 'delete_sidequest',
        'items': 'delete_item'
    }

    # Route to specialized helper if available
    if table in specialized_deletes:
        helper_name = specialized_deletes[table]
        return DeleteResult(
            success=False,
            error=f"Use {helper_name}() helper for proper validation and dependency checking"
        )

    # Generic delete for tables without specialized helpers
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check record exists
        if not _check_entity_exists(conn, table, id):
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Record ID {id} not found in table {table}"
            )

        # Create audit note
        _create_deletion_note(conn, table, id, note_reason, note_severity, note_source, note_type)

        # Delete record
        _delete_record(conn, table, id)
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("delete_project_entry")

        return DeleteResult(
            success=True,
            deleted_id=id,
            table=table,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return DeleteResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def delete_reserved(
    table: str,
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion",
    project_root: Optional[str] = None
) -> DeleteResult:
    """
    Delete abandoned reserved entries (escape hatch for cancelled reserve operations).

    PURPOSE: Provides escape hatch when user cancels after reserve but before finalize.
    SCENARIO: AI reserves file/function/type, user says 'cancel that', AI needs way to delete.

    Args:
        table: Table name (must be 'files', 'functions', or 'types')
        id: Reserved record ID to delete
        note_reason: Deletion reason (e.g., 'User cancelled operation')
        note_severity: Note severity ('info', 'warning', 'error')
        note_source: Note source ('ai' or 'user')
        note_type: Note type ('entry_deletion')

    Returns:
        DeleteResult with success status
    """
    # Validate table
    if table not in RESERVED_WORKFLOW_TABLES:
        return DeleteResult(
            success=False,
            error=f"delete_reserved only allowed for tables: {', '.join(RESERVED_WORKFLOW_TABLES)}"
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check record exists
        if not _check_entity_exists(conn, table, id):
            conn.close()
            return DeleteResult(
                success=False,
                error=f"Record ID {id} not found in table {table}"
            )

        # Check if reserved
        if not _is_reserved(conn, table, id):
            helper_name = f"delete_{table[:-1]}"  # files -> delete_file
            conn.close()
            return DeleteResult(
                success=False,
                error=f"not_reserved: Record is finalized. Use {helper_name}() instead"
            )

        # Check for dependencies (functions/types should have none while reserved)
        # For files: check if any functions or types reference this file
        if table == 'files':
            cursor = conn.execute("SELECT COUNT(*) as count FROM functions WHERE file_id = ?", (id,))
            row = cursor.fetchone()
            if row['count'] > 0:
                conn.close()
                return DeleteResult(
                    success=False,
                    error="dependencies_exist: Reserved file has functions - manual cleanup required"
                )

            cursor = conn.execute("SELECT COUNT(*) as count FROM types WHERE file_id = ?", (id,))
            row = cursor.fetchone()
            if row['count'] > 0:
                conn.close()
                return DeleteResult(
                    success=False,
                    error="dependencies_exist: Reserved file has types - manual cleanup required"
                )

        # Create audit note
        _create_deletion_note(conn, table, id, note_reason, note_severity, note_source, note_type)

        # Delete reserved record
        conn.execute(f"DELETE FROM {table} WHERE id = ? AND is_reserved = 1", (id,))
        conn.commit()
        conn.close()

        # Fetch return statements
        return_stmts = get_return_statements("delete_reserved")

        return DeleteResult(
            success=True,
            deleted_id=id,
            table=table,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return DeleteResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
