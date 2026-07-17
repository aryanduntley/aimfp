"""
AIMFP Helper Functions - Project Themes & Flows (Part 1)

Theme and flow management operations (CRUD and queries).
Themes organize flows, and flows organize files for project structure.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- get_theme_by_name: Get theme by name
- get_flow_by_name: Get flow by name
- get_all_themes: Get all project themes
- get_all_flows: Get all project flows
- add_theme: Add project theme
- update_theme: Update theme metadata
- delete_theme: Delete theme with flow validation
- add_flow: Add project flow
- get_file_ids_from_flows: Get all file IDs associated with flows
- update_flow: Update flow metadata
- delete_flow: Delete flow with comprehensive validation
"""

import sqlite3
import json
from dataclasses import dataclass
from typing import Optional, List, Tuple

from ..utils import get_return_statements
from ._common import (
    _open_connection,
    get_cached_project_root,
    _open_project_connection,
    _check_theme_exists,
    _check_flow_exists,
    _create_deletion_note
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class ThemeRecord:
    """Immutable theme record from database."""
    id: int
    name: str
    description: Optional[str]
    ai_generated: bool
    confidence_score: Optional[float]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FlowRecord:
    """Immutable flow record from database."""
    id: int
    name: str
    description: Optional[str]
    ai_generated: bool
    confidence_score: Optional[float]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ThemeQueryResult:
    """Result of theme lookup operation."""
    success: bool
    theme: Optional[ThemeRecord] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FlowQueryResult:
    """Result of flow lookup operation."""
    success: bool
    flow: Optional[FlowRecord] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemesQueryResult:
    """Result of themes query operation."""
    success: bool
    themes: Tuple[ThemeRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FlowsQueryResult:
    """Result of flows query operation."""
    success: bool
    flows: Tuple[FlowRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AddThemeResult:
    """Result of theme addition operation."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdateThemeResult:
    """Result of theme update operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeleteThemeResult:
    """Result of theme deletion operation."""
    success: bool
    error: Optional[str] = None
    flows: Tuple[str, ...] = ()  # Flow names blocking deletion
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AddFlowResult:
    """Result of flow addition operation."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FileIdsResult:
    """Result of file IDs query."""
    success: bool
    file_ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdateFlowResult:
    """Result of flow update operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeleteFlowResult:
    """Result of flow deletion operation."""
    success: bool
    error: Optional[str] = None
    tasks: Tuple[str, ...] = ()  # Task names blocking deletion
    sidequests: Tuple[str, ...] = ()  # Sidequest names blocking deletion
    files: Tuple[str, ...] = ()  # File names blocking deletion
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helper Functions
# ============================================================================

def build_theme_update_query(
    theme_id: int,
    name: Optional[str],
    description: Optional[str],
    confidence_score: Optional[float]
) -> Tuple[str, Tuple]:
    """
    Build SQL UPDATE query for theme with only non-NULL fields.

    Pure function - deterministic query building.

    Args:
        theme_id: Theme ID to update
        name: New name (None = don't update)
        description: New description (None = don't update)
        confidence_score: New confidence score (None = don't update)

    Returns:
        Tuple of (sql_query, parameters)
    """
    updates = []
    params_list = []

    if name is not None:
        updates.append("name = ?")
        params_list.append(name)

    if description is not None:
        updates.append("description = ?")
        params_list.append(description)

    if confidence_score is not None:
        updates.append("confidence_score = ?")
        params_list.append(confidence_score)

    # Always update timestamp
    updates.append("updated_at = CURRENT_TIMESTAMP")

    # Build query
    sql = f"UPDATE themes SET {', '.join(updates)} WHERE id = ?"
    params_list.append(theme_id)

    return (sql, tuple(params_list))


def build_flow_update_query(
    flow_id: int,
    name: Optional[str],
    description: Optional[str],
    confidence_score: Optional[float]
) -> Tuple[str, Tuple]:
    """
    Build SQL UPDATE query for flow with only non-NULL fields.

    Pure function - deterministic query building.

    Args:
        flow_id: Flow ID to update
        name: New name (None = don't update)
        description: New description (None = don't update)
        confidence_score: New confidence score (None = don't update)

    Returns:
        Tuple of (sql_query, parameters)
    """
    updates = []
    params_list = []

    if name is not None:
        updates.append("name = ?")
        params_list.append(name)

    if description is not None:
        updates.append("description = ?")
        params_list.append(description)

    if confidence_score is not None:
        updates.append("confidence_score = ?")
        params_list.append(confidence_score)

    # Always update timestamp
    updates.append("updated_at = CURRENT_TIMESTAMP")

    # Build query
    sql = f"UPDATE flows SET {', '.join(updates)} WHERE id = ?"
    params_list.append(flow_id)

    return (sql, tuple(params_list))


def row_to_theme_record(row: sqlite3.Row) -> ThemeRecord:
    """
    Convert database row to immutable ThemeRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable ThemeRecord
    """
    return ThemeRecord(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        ai_generated=bool(row["ai_generated"]),
        confidence_score=row["confidence_score"],
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


def row_to_flow_record(row: sqlite3.Row) -> FlowRecord:
    """
    Convert database row to immutable FlowRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable FlowRecord
    """
    return FlowRecord(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        ai_generated=bool(row["ai_generated"]),
        confidence_score=row["confidence_score"],
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


# ============================================================================
# Database Effect Functions
# ============================================================================





def _get_theme_by_name_effect(
    conn: sqlite3.Connection,
    theme_name: str
) -> Optional[sqlite3.Row]:
    """
    Effect: Query theme by name.

    Args:
        conn: Database connection
        theme_name: Theme name to look up

    Returns:
        Row object or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM themes WHERE name = ? LIMIT 1",
        (theme_name,)
    )
    return cursor.fetchone()


def _get_flow_by_name_effect(
    conn: sqlite3.Connection,
    flow_name: str
) -> Optional[sqlite3.Row]:
    """
    Effect: Query flow by name.

    Args:
        conn: Database connection
        flow_name: Flow name to look up

    Returns:
        Row object or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM flows WHERE name = ? LIMIT 1",
        (flow_name,)
    )
    return cursor.fetchone()


def _get_all_themes_effect(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """
    Effect: Query all themes.

    Args:
        conn: Database connection

    Returns:
        List of row objects
    """
    cursor = conn.execute("SELECT * FROM themes")
    return cursor.fetchall()


def _get_all_flows_effect(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """
    Effect: Query all flows.

    Args:
        conn: Database connection

    Returns:
        List of row objects
    """
    cursor = conn.execute("SELECT * FROM flows")
    return cursor.fetchall()


def _add_theme_effect(
    conn: sqlite3.Connection,
    name: str,
    description: Optional[str],
    ai_generated: bool,
    confidence_score: Optional[float]
) -> int:
    """
    Effect: Insert theme into database.

    Args:
        conn: Database connection
        name: Theme name
        description: Theme description
        ai_generated: True if AI-generated
        confidence_score: Confidence score 0-1

    Returns:
        Inserted theme ID
    """
    cursor = conn.execute(
        """
        INSERT INTO themes (name, description, ai_generated, confidence_score)
        VALUES (?, ?, ?, ?)
        """,
        (name, description, 1 if ai_generated else 0, confidence_score)
    )
    conn.commit()
    return cursor.lastrowid


def _update_theme_effect(conn: sqlite3.Connection, sql: str, params: Tuple) -> None:
    """
    Effect: Execute theme update query.

    Args:
        conn: Database connection
        sql: UPDATE query
        params: Query parameters
    """
    conn.execute(sql, params)
    conn.commit()


def _get_flows_for_theme_effect(conn: sqlite3.Connection, theme_id: int) -> List[str]:
    """
    Effect: Get flow names for theme (blocking deletion check).

    Args:
        conn: Database connection
        theme_id: Theme ID

    Returns:
        List of flow names
    """
    cursor = conn.execute(
        """SELECT f.name FROM flows f
           JOIN flow_themes ft ON f.id = ft.flow_id
           WHERE ft.theme_id = ?""",
        (theme_id,)
    )
    return [row["name"] for row in cursor.fetchall()]




def _delete_theme_effect(conn: sqlite3.Connection, theme_id: int) -> None:
    """
    Effect: Delete theme from database.

    Args:
        conn: Database connection
        theme_id: Theme ID to delete
    """
    conn.execute("DELETE FROM themes WHERE id = ?", (theme_id,))
    conn.commit()


def _add_flow_effect(
    conn: sqlite3.Connection,
    name: str,
    description: Optional[str],
    ai_generated: bool,
    confidence_score: Optional[float]
) -> int:
    """
    Effect: Insert flow into database.

    Args:
        conn: Database connection
        name: Flow name
        description: Flow description
        ai_generated: True if AI-generated
        confidence_score: Confidence score 0-1

    Returns:
        Inserted flow ID
    """
    cursor = conn.execute(
        """
        INSERT INTO flows (name, description, ai_generated, confidence_score)
        VALUES (?, ?, ?, ?)
        """,
        (name, description, 1 if ai_generated else 0, confidence_score)
    )
    conn.commit()
    return cursor.lastrowid


def _get_file_ids_from_flows_effect(
    conn: sqlite3.Connection,
    flow_ids: List[int]
) -> List[int]:
    """
    Effect: Get file IDs associated with flows.

    Args:
        conn: Database connection
        flow_ids: List of flow IDs

    Returns:
        Deduplicated list of file IDs
    """
    if not flow_ids:
        return []

    placeholders = ','.join('?' * len(flow_ids))
    cursor = conn.execute(
        f"SELECT DISTINCT file_id FROM file_flows WHERE flow_id IN ({placeholders})",
        flow_ids
    )
    return [row["file_id"] for row in cursor.fetchall()]


def _update_flow_effect(conn: sqlite3.Connection, sql: str, params: Tuple) -> None:
    """
    Effect: Execute flow update query.

    Args:
        conn: Database connection
        sql: UPDATE query
        params: Query parameters
    """
    conn.execute(sql, params)
    conn.commit()


def _get_open_tasks_with_flow_effect(
    conn: sqlite3.Connection,
    flow_id: int
) -> List[str]:
    """
    Effect: Get open task names that reference flow_id.

    Args:
        conn: Database connection
        flow_id: Flow ID to check

    Returns:
        List of task names
    """
    cursor = conn.execute(
        """
        SELECT name FROM tasks
        WHERE status IN ('pending', 'in_progress', 'blocked')
        AND json_extract(flow_ids, '$') IS NOT NULL
        """,
    )

    task_names = []
    for row in cursor.fetchall():
        # Check if flow_id exists in JSON array (handled in Python for simplicity)
        task_name = row["name"]
        # Query the flow_ids for this specific task
        cursor2 = conn.execute(
            "SELECT flow_ids FROM tasks WHERE name = ?",
            (task_name,)
        )
        flow_ids_json = cursor2.fetchone()["flow_ids"]
        if flow_ids_json:
            flow_ids = json.loads(flow_ids_json)
            if flow_id in flow_ids:
                task_names.append(task_name)

    return task_names


def _get_open_sidequests_with_flow_effect(
    conn: sqlite3.Connection,
    flow_id: int
) -> List[str]:
    """
    Effect: Get open sidequest names that reference flow_id.

    Args:
        conn: Database connection
        flow_id: Flow ID to check

    Returns:
        List of sidequest names
    """
    cursor = conn.execute(
        """
        SELECT name FROM sidequests
        WHERE status IN ('pending', 'in_progress')
        AND json_extract(flow_ids, '$') IS NOT NULL
        """,
    )

    sidequest_names = []
    for row in cursor.fetchall():
        # Check if flow_id exists in JSON array
        sidequest_name = row["name"]
        cursor2 = conn.execute(
            "SELECT flow_ids FROM sidequests WHERE name = ?",
            (sidequest_name,)
        )
        flow_ids_json = cursor2.fetchone()["flow_ids"]
        if flow_ids_json:
            flow_ids = json.loads(flow_ids_json)
            if flow_id in flow_ids:
                sidequest_names.append(sidequest_name)

    return sidequest_names


def _get_files_for_flow_effect(conn: sqlite3.Connection, flow_id: int) -> List[str]:
    """
    Effect: Get file names linked to flow.

    Args:
        conn: Database connection
        flow_id: Flow ID

    Returns:
        List of file names
    """
    cursor = conn.execute(
        """
        SELECT f.name
        FROM file_flows ff
        JOIN files f ON ff.file_id = f.id
        WHERE ff.flow_id = ?
        """,
        (flow_id,)
    )
    return [row["name"] for row in cursor.fetchall()]


def _delete_flow_effect(conn: sqlite3.Connection, flow_id: int) -> None:
    """
    Effect: Delete flow from database.

    Args:
        conn: Database connection
        flow_id: Flow ID to delete
    """
    conn.execute("DELETE FROM flows WHERE id = ?", (flow_id,))
    conn.commit()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def get_theme_by_name(
    theme_name: str,
    project_root: Optional[str] = None
) -> ThemeQueryResult:
    """
    Get theme by name (fairly frequent lookup).

    Queries themes table for exact name match.

    Args:
        theme_name: Theme name to look up

    Returns:
        ThemeQueryResult with theme record or None if not found

    Example:
        >>> result = get_theme_by_name("Authentication")
        >>> result.success
        True
        >>> result.theme.id
        1
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        row = _get_theme_by_name_effect(conn, theme_name)

        if row is None:
            return ThemeQueryResult(
                success=True,
                theme=None
            )

        theme_record = row_to_theme_record(row)

        return ThemeQueryResult(
            success=True,
            theme=theme_record
        )

    except Exception as e:
        return ThemeQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_flow_by_name(
    flow_name: str,
    project_root: Optional[str] = None
) -> FlowQueryResult:
    """
    Get flow by name (fairly frequent lookup).

    Queries flows table for exact name match.

    Args:
        flow_name: Flow name to look up

    Returns:
        FlowQueryResult with flow record or None if not found

    Example:
        >>> result = get_flow_by_name("User Registration")
        >>> result.success
        True
        >>> result.flow.id
        5
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        row = _get_flow_by_name_effect(conn, flow_name)

        if row is None:
            return FlowQueryResult(
                success=True,
                flow=None
            )

        flow_record = row_to_flow_record(row)

        return FlowQueryResult(
            success=True,
            flow=flow_record
        )

    except Exception as e:
        return FlowQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_all_themes(project_root: Optional[str] = None) -> ThemesQueryResult:
    """
    Get all project themes.

    Queries all records from themes table.

    Returns:
        ThemesQueryResult with tuple of theme records

    Example:
        >>> result = get_all_themes()
        >>> result.success
        True
        >>> len(result.themes)
        5
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_all_themes_effect(conn)
        theme_records = tuple(row_to_theme_record(row) for row in rows)

        return ThemesQueryResult(
            success=True,
            themes=theme_records
        )

    except Exception as e:
        return ThemesQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_all_flows(project_root: Optional[str] = None) -> FlowsQueryResult:
    """
    Get all project flows.

    Queries all records from flows table.

    Returns:
        FlowsQueryResult with tuple of flow records

    Example:
        >>> result = get_all_flows()
        >>> result.success
        True
        >>> len(result.flows)
        15
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_all_flows_effect(conn)
        flow_records = tuple(row_to_flow_record(row) for row in rows)

        return FlowsQueryResult(
            success=True,
            flows=flow_records
        )

    except Exception as e:
        return FlowsQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def add_theme(
    name: str,
    description: Optional[str] = None,
    ai_generated: bool = True,
    confidence_score: Optional[float] = 0.0,
    project_root: Optional[str] = None
) -> AddThemeResult:
    """
    Add project theme.

    Creates new theme record in themes table.

    Args:
        name: Theme name
        description: Theme description (optional)
        ai_generated: True if AI-generated (default: True)
        confidence_score: Confidence score 0-1 (default: 0.0)

    Returns:
        AddThemeResult with success status and new theme ID

    Example:
        >>> result = add_theme(
        ...     "Authentication",
        ...     description="User authentication and authorization",
        ...     ai_generated=True,
        ...     confidence_score=0.95
        ... )
        >>> result.success
        True
        >>> result.id
        1
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        theme_id = _add_theme_effect(
            conn,
            name,
            description,
            ai_generated,
            confidence_score
        )

        return_statements = get_return_statements("add_theme")

        return AddThemeResult(
            success=True,
            id=theme_id,
            return_statements=return_statements
        )

    except Exception as e:
        return AddThemeResult(
            success=False,
            error=f"Failed to add theme: {str(e)}"
        )

    finally:
        conn.close()


def update_theme(
    theme_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    confidence_score: Optional[float] = None,
    project_root: Optional[str] = None
) -> UpdateThemeResult:
    """
    Update theme metadata.

    Only updates non-NULL parameters.

    Args:
        theme_id: Theme ID to update
        name: New name (None = don't update)
        description: New description (None = don't update)
        confidence_score: New confidence score (None = don't update)

    Returns:
        UpdateThemeResult with success status

    Example:
        >>> result = update_theme(1, confidence_score=0.98)
        >>> result.success
        True
    """
    # Validate at least one parameter is provided
    if all(v is None for v in [name, description, confidence_score]):
        return UpdateThemeResult(
            success=False,
            error="At least one parameter must be provided for update"
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_theme_exists(conn, theme_id):
            return UpdateThemeResult(
                success=False,
                error=f"Theme with ID {theme_id} not found"
            )

        sql, params = build_theme_update_query(theme_id, name, description, confidence_score)
        _update_theme_effect(conn, sql, params)

        return_statements = get_return_statements("update_theme")

        return UpdateThemeResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return UpdateThemeResult(
            success=False,
            error=f"Failed to update theme: {str(e)}"
        )

    finally:
        conn.close()


def delete_theme(
    theme_id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion",
    project_root: Optional[str] = None
) -> DeleteThemeResult:
    """
    Delete theme with flow validation.

    Validates no flows are linked to theme before deletion.

    Args:
        theme_id: Theme ID to delete
        note_reason: Deletion reason
        note_severity: 'info', 'warning', 'error'
        note_source: 'ai' or 'user'
        note_type: Note type (default: 'entry_deletion')

    Returns:
        DeleteThemeResult with success status or blocking flows list

    Example:
        >>> result = delete_theme(
        ...     1,
        ...     note_reason="Theme no longer relevant",
        ...     note_severity="info",
        ...     note_source="ai"
        ... )
        >>> result.success
        True
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_theme_exists(conn, theme_id):
            return DeleteThemeResult(
                success=False,
                error=f"Theme with ID {theme_id} not found"
            )

        # Check for flows linked to theme
        flow_names = _get_flows_for_theme_effect(conn, theme_id)

        if flow_names:
            return DeleteThemeResult(
                success=False,
                error="flows_exist",
                flows=tuple(flow_names)
            )

        # No blocking flows - proceed with deletion
        _create_deletion_note(
            conn,
            "themes",
            theme_id,
            note_reason,
            note_severity,
            note_source,
            note_type
        )

        _delete_theme_effect(conn, theme_id)

        return_statements = get_return_statements("delete_theme")

        return DeleteThemeResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return DeleteThemeResult(
            success=False,
            error=f"Failed to delete theme: {str(e)}"
        )

    finally:
        conn.close()


def add_flow(
    name: str,
    description: Optional[str] = None,
    ai_generated: bool = True,
    confidence_score: Optional[float] = 0.0,
    project_root: Optional[str] = None
) -> AddFlowResult:
    """
    Add project flow.

    Creates new flow record in flows table.

    Args:
        name: Flow name
        description: Flow description (optional)
        ai_generated: True if AI-generated (default: True)
        confidence_score: Confidence score 0-1 (default: 0.0)

    Returns:
        AddFlowResult with success status and new flow ID

    Example:
        >>> result = add_flow(
        ...     "User Registration",
        ...     description="Handle user signup and email verification",
        ...     ai_generated=True,
        ...     confidence_score=0.90
        ... )
        >>> result.success
        True
        >>> result.id
        5
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        flow_id = _add_flow_effect(
            conn,
            name,
            description,
            ai_generated,
            confidence_score
        )

        return_statements = get_return_statements("add_flow")

        return AddFlowResult(
            success=True,
            id=flow_id,
            return_statements=return_statements
        )

    except Exception as e:
        return AddFlowResult(
            success=False,
            error=f"Failed to add flow: {str(e)}"
        )

    finally:
        conn.close()


def get_file_ids_from_flows(
    flow_ids: List[int],
    project_root: Optional[str] = None
) -> FileIdsResult:
    """
    Get all file IDs associated with flows.

    Queries file_flows junction table and returns deduplicated file IDs.

    Args:
        flow_ids: List of flow IDs

    Returns:
        FileIdsResult with tuple of file IDs

    Example:
        >>> result = get_file_ids_from_flows([1, 3, 5])
        >>> result.success
        True
        >>> result.file_ids
        (42, 43, 44, 45)
    """
    if not flow_ids:
        return FileIdsResult(
            success=True,
            file_ids=()
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        file_ids = _get_file_ids_from_flows_effect(conn, flow_ids)

        return_statements = get_return_statements("get_file_ids_from_flows")

        return FileIdsResult(
            success=True,
            file_ids=tuple(file_ids),
            return_statements=return_statements
        )

    except Exception as e:
        return FileIdsResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def update_flow(
    flow_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    confidence_score: Optional[float] = None,
    project_root: Optional[str] = None
) -> UpdateFlowResult:
    """
    Update flow metadata.

    Only updates non-NULL parameters.

    Args:
        flow_id: Flow ID to update
        name: New name (None = don't update)
        description: New description (None = don't update)
        confidence_score: New confidence score (None = don't update)

    Returns:
        UpdateFlowResult with success status

    Example:
        >>> result = update_flow(5, confidence_score=0.95)
        >>> result.success
        True
    """
    # Validate at least one parameter is provided
    if all(v is None for v in [name, description, confidence_score]):
        return UpdateFlowResult(
            success=False,
            error="At least one parameter must be provided for update"
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_flow_exists(conn, flow_id):
            return UpdateFlowResult(
                success=False,
                error=f"Flow with ID {flow_id} not found"
            )

        sql, params = build_flow_update_query(flow_id, name, description, confidence_score)
        _update_flow_effect(conn, sql, params)

        return_statements = get_return_statements("update_flow")

        return UpdateFlowResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return UpdateFlowResult(
            success=False,
            error=f"Failed to update flow: {str(e)}"
        )

    finally:
        conn.close()


def delete_flow(
    flow_id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion",
    project_root: Optional[str] = None
) -> DeleteFlowResult:
    """
    Delete flow with comprehensive validation.

    Validates no open tasks/sidequests reference flow and no files are linked.

    Args:
        flow_id: Flow ID to delete
        note_reason: Deletion reason
        note_severity: 'info', 'warning', 'error'
        note_source: 'ai' or 'user'
        note_type: Note type (default: 'entry_deletion')

    Returns:
        DeleteFlowResult with success status or blocking entities lists

    Example:
        >>> result = delete_flow(
        ...     5,
        ...     note_reason="Flow no longer relevant",
        ...     note_severity="info",
        ...     note_source="ai"
        ... )
        >>> result.success
        True
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_flow_exists(conn, flow_id):
            return DeleteFlowResult(
                success=False,
                error=f"Flow with ID {flow_id} not found"
            )

        # Check for open tasks referencing flow
        task_names = _get_open_tasks_with_flow_effect(conn, flow_id)

        # Check for open sidequests referencing flow
        sidequest_names = _get_open_sidequests_with_flow_effect(conn, flow_id)

        # Check for files linked to flow
        file_names = _get_files_for_flow_effect(conn, flow_id)

        # If any blocking entities exist, return error
        if task_names or sidequest_names or file_names:
            return DeleteFlowResult(
                success=False,
                error="flow_in_use",
                tasks=tuple(task_names),
                sidequests=tuple(sidequest_names),
                files=tuple(file_names)
            )

        # No blocking entities - proceed with deletion
        _create_deletion_note(
            conn,
            "flows",
            flow_id,
            note_reason,
            note_severity,
            note_source,
            note_type
        )

        _delete_flow_effect(conn, flow_id)

        return_statements = get_return_statements("delete_flow")

        return DeleteFlowResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return DeleteFlowResult(
            success=False,
            error=f"Failed to delete flow: {str(e)}"
        )

    finally:
        conn.close()
