"""
AIMFP Helper Functions - Project Schema

Database schema introspection and metadata extraction for project database.
Provides information about tables, fields, types, and available parameters.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- get_project_tables: List all tables in project database
- get_project_fields: Get field names and types for a specific table
- get_project_schema: Get complete schema for project database
- get_project_json_parameters: Get available fields for table (for generic CRUD)
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple, Dict

from ..utils import get_return_statements

# Import common project utilities (DRY principle)
from ._common import _open_connection, get_cached_project_root, _open_project_connection


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class FieldInfo:
    """Immutable field information."""
    name: str
    type: str
    nullable: bool
    default: Optional[str]
    pk: bool


@dataclass(frozen=True)
class TablesResult:
    """Result of get_project_tables."""
    success: bool
    tables: Tuple[str, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class FieldsResult:
    """Result of get_project_fields."""
    success: bool
    fields: Tuple[FieldInfo, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class SchemaResult:
    """Result of get_project_schema."""
    success: bool
    schema: Dict[str, Tuple[FieldInfo, ...]] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class JsonParametersResult:
    """Result of get_project_json_parameters."""
    success: bool
    parameters: Dict[str, str] = None
    error: Optional[str] = None


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


def _query_table_info(conn: sqlite3.Connection, table: str) -> Tuple[FieldInfo, ...]:
    """
    Effect: Query field information for a table.

    Args:
        conn: Database connection
        table: Table name

    Returns:
        Tuple of FieldInfo objects
    """
    cursor = conn.execute(f"PRAGMA table_info({table})")
    rows = cursor.fetchall()

    return tuple(
        FieldInfo(
            name=row['name'],
            type=row['type'],
            nullable=not bool(row['notnull']),
            default=row['dflt_value'],
            pk=bool(row['pk'])
        )
        for row in rows
    )


# ============================================================================
# Public Helper Functions
# ============================================================================

def get_project_tables(project_root: Optional[str] = None) -> TablesResult:
    """
    List all tables in project database.

    Returns:
        TablesResult with table names
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        tables = _query_tables(conn)
        conn.close()

        return TablesResult(
            success=True,
            tables=tables
        )

    except Exception as e:
        conn.close()
        return TablesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_project_fields(table: str, project_root: Optional[str] = None) -> FieldsResult:
    """
    Get field names and types for a specific table.

    Args:
        table: Table name to get fields for

    Returns:
        FieldsResult with field information
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if table exists
        tables = _query_tables(conn)
        if table not in tables:
            conn.close()
            return FieldsResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Get field info
        fields = _query_table_info(conn, table)
        conn.close()

        return FieldsResult(
            success=True,
            fields=fields
        )

    except Exception as e:
        conn.close()
        return FieldsResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_project_schema(project_root: Optional[str] = None) -> SchemaResult:
    """
    Get complete schema for project database.

    Returns:
        SchemaResult with full schema (table_name -> fields mapping)
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Get all tables
        tables = _query_tables(conn)

        # Build schema dictionary
        schema = {}
        for table in tables:
            fields = _query_table_info(conn, table)
            schema[table] = fields

        conn.close()

        return SchemaResult(
            success=True,
            schema=schema
        )

    except Exception as e:
        conn.close()
        return SchemaResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_project_json_parameters(table: str, project_root: Optional[str] = None) -> JsonParametersResult:
    """
    Get available fields for table to use with generic add/update operations.
    Filters out id, created_at, updated_at fields.

    Args:
        table: Table name to get available fields for

    Returns:
        JsonParametersResult with field_name -> type_hint mapping
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if table exists
        tables = _query_tables(conn)
        if table not in tables:
            conn.close()
            return JsonParametersResult(
                success=False,
                error=f"Table {table} not found"
            )

        # Get field info
        fields = _query_table_info(conn, table)
        conn.close()

        # Filter out auto-managed fields and build parameters dict
        excluded_fields = {'id', 'created_at', 'updated_at'}
        parameters = {
            field.name: field.type
            for field in fields
            if field.name not in excluded_fields
        }

        return JsonParametersResult(
            success=True,
            parameters=parameters
        )

    except Exception as e:
        conn.close()
        return JsonParametersResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
