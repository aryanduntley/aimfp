"""
AIMFP Helper Functions - User Preferences Schema

Database schema introspection and metadata extraction for user_preferences database.
Provides information about tables, fields, types, and available parameters.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- get_settings_tables: List all tables in user_preferences database
- get_settings_fields: Get field names and types for a specific table
- get_settings_schema: Get complete schema for user_preferences database
- get_settings_json_parameters: Get available fields for table (for generic CRUD)
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple, Dict

from ..utils import get_return_statements

# Import common user_preferences utilities (DRY principle)
from ._common import _open_connection, get_cached_project_root, _open_preferences_connection


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
    """Result of get_settings_tables."""
    success: bool
    tables: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldsResult:
    """Result of get_settings_fields."""
    success: bool
    fields: Tuple[FieldInfo, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaResult:
    """Result of get_settings_schema."""
    success: bool
    schema: Dict[str, Tuple[FieldInfo, ...]] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class JsonParametersResult:
    """Result of get_settings_json_parameters."""
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

def get_settings_tables() -> TablesResult:
    """
    List all tables in user_preferences database.

    Returns:
        TablesResult with table names

    Example:
        >>> result = get_settings_tables()
        >>> result.tables
        ('ai_interaction_log', 'directive_preferences', 'fp_flow_tracking',
         'issue_reports', 'tracking_notes', 'tracking_settings', 'user_settings')
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

    try:
        tables = _query_tables(conn)
        conn.close()

        return TablesResult(
            success=True,
            tables=tables,
            return_statements=get_return_statements("get_settings_tables")
        )

    except Exception as e:
        conn.close()
        return TablesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_settings_fields(table: str) -> FieldsResult:
    """
    Get field names and types for a specific table.

    Args:
        table: Table name to get fields for

    Returns:
        FieldsResult with field information

    Example:
        >>> result = get_settings_fields("user_settings")
        >>> for field in result.fields:
        ...     print(f"{field.name}: {field.type}")
        setting_key: TEXT
        setting_value: TEXT
        description: TEXT
        scope: TEXT
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

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
            fields=fields,
            return_statements=get_return_statements("get_settings_fields")
        )

    except Exception as e:
        conn.close()
        return FieldsResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_settings_schema() -> SchemaResult:
    """
    Get complete schema for user_preferences database.

    Returns:
        SchemaResult with full schema (table_name -> fields mapping)

    Example:
        >>> result = get_settings_schema()
        >>> for table, fields in result.schema.items():
        ...     print(f"{table}: {len(fields)} fields")
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

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
            return_statements=get_return_statements("get_settings_schema")
        )

    except Exception as e:
        conn.close()
        return SchemaResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_settings_json_parameters(table: str) -> JsonParametersResult:
    """
    Get available fields for table to use with generic add/update operations.
    Filters out id, created_at, updated_at fields.

    Args:
        table: Table name to get available fields for

    Returns:
        JsonParametersResult with field_name -> type_hint mapping

    Example:
        >>> result = get_settings_json_parameters("directive_preferences")
        >>> result.parameters
        {'directive_name': 'TEXT', 'preference_key': 'TEXT',
         'preference_value': 'TEXT', 'active': 'BOOLEAN', 'description': 'TEXT'}
    """
    project_root = get_cached_project_root()
    conn = _open_preferences_connection(project_root)

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
            parameters=parameters,
            return_statements=get_return_statements("get_settings_json_parameters")
        )

    except Exception as e:
        conn.close()
        return JsonParametersResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
