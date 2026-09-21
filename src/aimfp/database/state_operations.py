"""
AIMFP MCP Server - State Operations

Global state variable operations for the MCP server's own runtime state.
Uses mcp_runtime.db (located in this directory) as the backing store.

All MCP server code can import from here:
    from aimfp.database.state_operations import set_var, get_var, delete_var

This is the MCP server's equivalent of the per-project state_operations
template that AI creates for user projects — but note the difference in scope.
mcp_runtime.db lives in the INSTALLED PACKAGE directory, so a single file is
shared by every session on every project, which makes it the most contended
database AIMFP owns. Its connections therefore go through _open_connection
(WAL + explicit busy timeout) like any other real database. The two sibling
files named state_operations.py do not: .state/ is per-project and
templates/state_db/ is a template that AIMFP never executes.
"""

import json
import sqlite3
from typing import Optional, Any

from .connection import Result, get_mcp_runtime_db_path, _open_connection


# ============================================================================
# Core Variable Operations
# ============================================================================

def set_var(var_name: str, value: Any, var_type: Optional[str] = None) -> Result:
    """
    Effect: Store variable in MCP server state database.

    Args:
        var_name: Variable identifier (e.g., 'session_count', 'last_project_root')
        value: Value to store (will be JSON-serialized)
        var_type: Optional type hint ('int', 'str', 'bool', 'float', 'dict', 'list')

    Returns:
        Result with success status
    """
    conn = _open_connection(get_mcp_runtime_db_path())
    try:
        if isinstance(value, (dict, list)):
            serialized_value = json.dumps(value)
            inferred_type = 'dict' if isinstance(value, dict) else 'list'
        elif isinstance(value, bool):
            serialized_value = json.dumps(value)
            inferred_type = 'bool'
        elif isinstance(value, (int, float)):
            serialized_value = str(value)
            inferred_type = 'int' if isinstance(value, int) else 'float'
        else:
            serialized_value = str(value)
            inferred_type = 'str'

        conn.execute(
            """INSERT OR REPLACE INTO variables (var_name, var_value, var_type, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            (var_name, serialized_value, var_type or inferred_type)
        )
        conn.commit()
        return Result(success=True)
    except Exception as e:
        return Result(success=False, error=f"Failed to set variable: {str(e)}")
    finally:
        conn.close()


def get_var(var_name: str) -> Result:
    """
    Effect: Retrieve variable from MCP server state database.

    Args:
        var_name: Variable identifier

    Returns:
        Result with deserialized value in data field, or error
    """
    conn = _open_connection(get_mcp_runtime_db_path())
    try:
        row = conn.execute(
            "SELECT var_value, var_type FROM variables WHERE var_name=?",
            (var_name,)
        ).fetchone()

        if not row:
            return Result(success=False, error=f"Variable '{var_name}' not found")

        value_str, var_type = row[0], row[1]

        if var_type == 'int':
            value = int(value_str)
        elif var_type == 'float':
            value = float(value_str)
        elif var_type in ('bool', 'dict', 'list'):
            value = json.loads(value_str)
        else:
            value = value_str

        return Result(success=True, data=value)
    except Exception as e:
        return Result(success=False, error=f"Failed to get variable: {str(e)}")
    finally:
        conn.close()


def delete_var(var_name: str) -> Result:
    """
    Effect: Delete variable from MCP server state database.

    Args:
        var_name: Variable identifier

    Returns:
        Result with success status
    """
    conn = _open_connection(get_mcp_runtime_db_path())
    try:
        cursor = conn.execute("DELETE FROM variables WHERE var_name=?", (var_name,))
        conn.commit()

        if cursor.rowcount == 0:
            return Result(success=False, error=f"Variable '{var_name}' not found")

        return Result(success=True)
    except Exception as e:
        return Result(success=False, error=f"Failed to delete variable: {str(e)}")
    finally:
        conn.close()


def increment_var(var_name: str, amount: int = 1) -> Result:
    """
    Effect: Increment numeric variable (creates if doesn't exist).

    Args:
        var_name: Variable identifier
        amount: Amount to increment by (default 1)

    Returns:
        Result with new value in data field
    """
    result = get_var(var_name)

    if not result.success:
        current_value = 0
    else:
        current_value = result.data
        if not isinstance(current_value, (int, float)):
            return Result(success=False, error=f"Variable '{var_name}' is not numeric")

    new_value = current_value + amount
    set_result = set_var(var_name, new_value, 'int')

    if not set_result.success:
        return set_result

    return Result(success=True, data=new_value)


def list_vars(var_type: Optional[str] = None) -> Result:
    """
    Effect: List all variables (optionally filtered by type).

    Args:
        var_type: Optional type filter ('int', 'str', 'bool', 'float', 'dict', 'list')

    Returns:
        Result with list of variable names in data field
    """
    conn = _open_connection(get_mcp_runtime_db_path())
    try:
        if var_type:
            rows = conn.execute(
                "SELECT var_name FROM variables WHERE var_type=? ORDER BY var_name",
                (var_type,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT var_name FROM variables ORDER BY var_name"
            ).fetchall()

        var_names = tuple(row[0] for row in rows)
        return Result(success=True, data=var_names)
    except Exception as e:
        return Result(success=False, error=f"Failed to list variables: {str(e)}")
    finally:
        conn.close()
