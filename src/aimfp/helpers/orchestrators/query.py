"""
AIMFP Helper Functions - Orchestrator Query Helpers

Read-only query operations for the orchestrator module.

Helpers in this file:
- query_project_state: Flexible SQL-like query interface
- get_files_by_flow_context: Files + embedded functions for a flow

All helpers are CRUD read operations — no decision logic.
AI interprets returned data and decides next steps.
"""

import re
import sqlite3
from typing import Optional, Tuple, Dict, Any, List

from ._common import (
    _open_project_connection,
    resolve_project_root,
    get_return_statements,
    row_to_dict,
    rows_to_tuple,
    Result,
    VALID_QUERY_ENTITIES,
    JOIN_MAPPINGS,
)
from ..project.functions_1 import normalize_function_row


# ============================================================================
# query_project_state
# ============================================================================

def query_project_state(
    entity: str,
    filters: Optional[Dict[str, Any]] = None,
    joins: Optional[List[str]] = None,
    sort: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> Result:
    """
    Flexible SQL-like query interface for project database.

    Provides filtering, joining, sorting, and pagination without raw SQL.
    All values are parameterized to prevent SQL injection.

    Args:
        entity: Primary table to query (e.g., 'tasks', 'files', 'functions')
        filters: WHERE conditions — {field: value} or {field: {op: 'gt', value: 5}}
        joins: Relations to LEFT JOIN (e.g., ['milestones', 'files'])
        sort: ORDER BY clause (e.g., 'name ASC', 'id DESC')
        limit: Maximum results
        offset: Skip first N results

    Returns:
        Result with data=tuple of row dicts
    """
    if entity not in VALID_QUERY_ENTITIES:
        return Result(
            success=False,
            error=f"Unknown entity '{entity}'. Valid: {sorted(VALID_QUERY_ENTITIES)}",
        )

    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    try:
        conn = _open_project_connection(project_root)
        try:
            # Build query
            query_parts = [f"SELECT {entity}.*"]
            from_parts = [f"FROM {entity}"]
            where_parts = []
            params = []

            # Apply joins
            if joins:
                entity_joins = JOIN_MAPPINGS.get(entity, {})
                for join_name in joins:
                    join_sql = entity_joins.get(join_name)
                    if join_sql:
                        from_parts.append(join_sql)

            # Apply filters
            if filters:
                for field, condition in filters.items():
                    _safe_field = _sanitize_field_name(field)
                    if _safe_field is None:
                        return Result(
                            success=False,
                            error=f"Invalid field name: '{field}'",
                        )

                    if isinstance(condition, dict):
                        op = condition.get('op', 'eq')
                        value = condition.get('value')
                        sql_op = _op_to_sql(op)
                        if sql_op is None:
                            return Result(
                                success=False,
                                error=f"Unknown filter operator: '{op}'",
                            )
                        if op == 'in' and isinstance(value, (list, tuple)):
                            placeholders = ','.join('?' for _ in value)
                            where_parts.append(
                                f"{entity}.{_safe_field} IN ({placeholders})"
                            )
                            params.extend(value)
                        else:
                            where_parts.append(
                                f"{entity}.{_safe_field} {sql_op} ?"
                            )
                            params.append(value)
                    else:
                        # Simple equality
                        where_parts.append(f"{entity}.{_safe_field} = ?")
                        params.append(condition)

            # Assemble query
            sql = ' '.join(query_parts) + ' ' + ' '.join(from_parts)
            if where_parts:
                sql += ' WHERE ' + ' AND '.join(where_parts)

            # Apply sort
            if sort:
                safe_sort = _sanitize_sort(sort)
                if safe_sort:
                    sql += f' ORDER BY {safe_sort}'

            # Apply pagination
            if limit is not None:
                sql += ' LIMIT ?'
                params.append(limit)
            if offset is not None:
                sql += ' OFFSET ?'
                params.append(offset)

            cursor = conn.execute(sql, params)
            rows = rows_to_tuple(cursor.fetchall())

            return Result(
                success=True,
                data=rows,
                return_statements=get_return_statements("query_project_state"),
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return Result(success=False, error=str(e))
    except Exception as e:
        return Result(success=False, error=f"Query failed: {str(e)}")


def _sanitize_field_name(field: str) -> Optional[str]:
    """Pure: Validate field name contains only safe characters."""
    if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', field):
        return field
    return None


def _sanitize_sort(sort: str) -> Optional[str]:
    """Pure: Validate and sanitize ORDER BY clause."""
    parts = sort.strip().split()
    if len(parts) == 1:
        field = _sanitize_field_name(parts[0])
        return field
    elif len(parts) == 2:
        field = _sanitize_field_name(parts[0])
        direction = parts[1].upper()
        if field and direction in ('ASC', 'DESC'):
            return f"{field} {direction}"
    return None


def _op_to_sql(op: str) -> Optional[str]:
    """Pure: Convert filter operator to SQL operator."""
    mapping = {
        'eq': '=',
        'ne': '!=',
        'gt': '>',
        'lt': '<',
        'gte': '>=',
        'lte': '<=',
        'like': 'LIKE',
        'in': 'IN',
    }
    return mapping.get(op)


# ============================================================================
# get_files_by_flow_context
# ============================================================================

def get_files_by_flow_context(
    flow_id: int,
) -> Result:
    """
    Get all files for a flow with functions embedded in each file dict.

    Queries file_flows for the given flow_id, retrieves associated files,
    and embeds functions array in each file object.

    Args:
        flow_id: Flow ID to get files for

    Returns:
        Result with data=tuple of file dicts, each with 'functions' key
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    try:
        conn = _open_project_connection(project_root)
        try:
            # Step 1: Get file IDs for this flow
            cursor = conn.execute(
                "SELECT file_id FROM file_flows WHERE flow_id = ?",
                (flow_id,)
            )
            file_ids = tuple(row['file_id'] for row in cursor.fetchall())

            if not file_ids:
                return Result(
                    success=True,
                    data=(),
                    return_statements=get_return_statements("get_files_by_flow_context"),
                )

            # Step 2: Get file records
            placeholders = ','.join('?' for _ in file_ids)
            cursor = conn.execute(
                f"SELECT * FROM files WHERE id IN ({placeholders})",
                file_ids
            )
            files = [row_to_dict(row) for row in cursor.fetchall()]

            # Step 3: For each file, embed functions
            for file_dict in files:
                cursor = conn.execute(
                    "SELECT * FROM functions WHERE file_id = ?",
                    (file_dict['id'],)
                )
                file_dict['functions'] = tuple(normalize_function_row(row) for row in rows_to_tuple(cursor.fetchall()))

            return Result(
                success=True,
                data=tuple(files),
                return_statements=get_return_statements("get_files_by_flow_context"),
            )

        finally:
            conn.close()

    except FileNotFoundError as e:
        return Result(success=False, error=str(e))
    except Exception as e:
        return Result(success=False, error=f"Failed to get files by flow context: {str(e)}")
