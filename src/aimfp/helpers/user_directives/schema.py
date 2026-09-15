"""
AIMFP Helper Functions - User Directives Schema

Database schema introspection and metadata extraction for user_directives database.
Provides information about tables, fields, types, and available parameters.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- get_user_custom_tables: List all tables in user_directives database
- get_user_custom_fields: Get field names and types for a specific table
- get_user_custom_schema: Get complete schema for user_directives database
- get_user_custom_json_parameters: Get available fields for table (for generic CRUD)
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple, Dict

from ..utils import get_return_statements

# Import common user_directives utilities (DRY principle)
from ._common import (
    get_cached_project_root,
    _open_directives_connection,
    _table_exists,
)


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
    """Result of get_user_custom_tables."""
    success: bool
    tables: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldsResult:
    """Result of get_user_custom_fields."""
    success: bool
    fields: Tuple[FieldInfo, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaResult:
    """Result of get_user_custom_schema."""
    success: bool
    schema: Dict[str, Tuple[FieldInfo, ...]] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class JsonParametersResult:
    """Result of get_user_custom_json_parameters."""
    success: bool
    parameters: Dict[str, str] = None
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
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
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

def get_user_custom_tables() -> TablesResult:
    """
    List all tables in user_directives database.

    Returns:
        TablesResult with table names

    Example:
        >>> result = get_user_custom_tables()
        >>> result.tables
        ('directive_dependencies', 'directive_executions', 'directive_helpers',
         'directive_implementations', 'directive_relationships', 'helper_functions',
         'logging_config', 'notes', 'schema_version', 'source_files', 'user_directives')
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        tables = _query_tables(conn)
        conn.close()

        return TablesResult(
            success=True,
            tables=tables,
            return_statements=get_return_statements("get_user_custom_tables")
        )

    except Exception as e:
        conn.close()
        return TablesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_user_custom_fields(table: str) -> FieldsResult:
    """
    Get field names and types for a specific table in user_directives.db.

    Args:
        table: Table name to get fields for

    Returns:
        FieldsResult with field information

    Example:
        >>> result = get_user_custom_fields("user_directives")
        >>> for field in result.fields:
        ...     print(f"{field.name}: {field.type}")
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
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
            fields=fields,
            return_statements=get_return_statements("get_user_custom_fields")
        )

    except Exception as e:
        conn.close()
        return FieldsResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_user_custom_schema() -> SchemaResult:
    """
    Get complete schema for user_directives database.

    Returns:
        SchemaResult with full schema (table_name -> fields mapping)

    Example:
        >>> result = get_user_custom_schema()
        >>> for table, fields in result.schema.items():
        ...     print(f"{table}: {len(fields)} fields")
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

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
            schema=schema,
            return_statements=get_return_statements("get_user_custom_schema")
        )

    except Exception as e:
        conn.close()
        return SchemaResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_user_custom_json_parameters(table: str) -> JsonParametersResult:
    """
    Get available fields for table to use with generic add/update operations.
    Filters out id, created_at, updated_at fields.

    Args:
        table: Table name to get available fields for

    Returns:
        JsonParametersResult with field_name -> type_hint mapping

    Example:
        >>> result = get_user_custom_json_parameters("user_directives")
        >>> result.parameters
        {'name': 'TEXT', 'description': 'TEXT', 'domain': 'TEXT', ...}
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Check if table exists
        if not _table_exists(conn, table):
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
            parameters=parameters,
            return_statements=get_return_statements("get_user_custom_json_parameters")
        )

    except Exception as e:
        conn.close()
        return JsonParametersResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
