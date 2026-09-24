"""
AIMFP Helper Functions - Project Interactions

Function interaction/dependency management for project database.
Tracks relationships between functions (calls, compositions, pipes, etc.).

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- add_interaction: Add single function dependency/interaction (by function names)
- add_interactions: Add multiple interactions at once (by function IDs)
- update_interaction: Update interaction metadata
- delete_interaction: Delete interaction with audit trail
- get_interactions_by_function: Get all interactions for a function (both directions)
- get_interactions_by_file: Get all interactions for functions in a file
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

# Import global utilities
from ..utils import get_return_statements
from ._common import _check_function_exists, _create_deletion_note, get_cached_project_root, _open_project_connection


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class AddInteractionResult:
    """Result of adding single interaction."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    already_existed: bool = False  # the (source, target, type) edge was already tracked
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class AddInteractionsResult:
    """Result of adding multiple interactions."""
    success: bool
    ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    added_count: int = 0
    skipped_count: int = 0  # edges already tracked; their existing ids are in ids
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class UpdateInteractionResult:
    """Result of updating interaction."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class DeleteInteractionResult:
    """Result of deleting interaction."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class InteractionRecord:
    """Immutable interaction record from database with resolved function/file names."""
    id: int
    source_function_id: int
    source_function_name: Optional[str]
    source_file_name: Optional[str]
    source_file_path: Optional[str]
    target_function_id: int
    target_function_name: Optional[str]
    target_file_name: Optional[str]
    target_file_path: Optional[str]
    interaction_type: str
    description: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class InteractionQueryResult:
    """Result of interaction lookup that may return multiple matches."""
    success: bool
    interactions: Tuple[InteractionRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FunctionMatch:
    """A function match for disambiguation."""
    id: int
    name: str
    file_name: Optional[str]
    file_path: Optional[str]


# ============================================================================
# Pure Helper Functions
# ============================================================================

def validate_interaction_type(interaction_type: str) -> bool:
    """
    Validate that interaction type is one of the allowed values.

    Pure function - no side effects, deterministic.

    Args:
        interaction_type: Interaction type string to validate

    Returns:
        True if valid, False otherwise
    """
    valid_types = {'call', 'chain', 'borrow', 'compose', 'pipe'}
    return interaction_type in valid_types


def build_update_query(
    interaction_id: int,
    source_function_id: Optional[int],
    target_function_id: Optional[int],
    interaction_type: Optional[str],
    description: Optional[str]
) -> Tuple[str, Tuple]:
    """
    Build SQL UPDATE query with only non-NULL fields.

    Pure function - deterministic query building.

    Args:
        interaction_id: Interaction ID to update
        source_function_id: New source function ID (None = don't update)
        target_function_id: New target function ID (None = don't update)
        interaction_type: New interaction type (None = don't update)
        description: New description (None = don't update)

    Returns:
        Tuple of (sql_query, parameters)
    """
    updates = []
    params_list = []

    if source_function_id is not None:
        updates.append("source_function_id = ?")
        params_list.append(source_function_id)

    if target_function_id is not None:
        updates.append("target_function_id = ?")
        params_list.append(target_function_id)

    if interaction_type is not None:
        updates.append("interaction_type = ?")
        params_list.append(interaction_type)

    if description is not None:
        updates.append("description = ?")
        params_list.append(description)

    # Build query
    sql = f"UPDATE interactions SET {', '.join(updates)} WHERE id = ?"
    params_list.append(interaction_id)

    return (sql, tuple(params_list))


def row_to_interaction_record(row: sqlite3.Row) -> InteractionRecord:
    """
    Convert a database row to an immutable InteractionRecord.

    Pure function - deterministic conversion.

    Args:
        row: SQLite Row object from interaction query with JOINed function/file data

    Returns:
        InteractionRecord with all fields populated from row
    """
    return InteractionRecord(
        id=row["id"],
        source_function_id=row["source_function_id"],
        source_function_name=row["source_function_name"],
        source_file_name=row["source_file_name"],
        source_file_path=row["source_file_path"],
        target_function_id=row["target_function_id"],
        target_function_name=row["target_function_name"],
        target_file_name=row["target_file_name"],
        target_file_path=row["target_file_path"],
        interaction_type=row["interaction_type"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ============================================================================
# Database Effect Functions
# ============================================================================



def _check_interaction_exists(conn: sqlite3.Connection, interaction_id: int) -> bool:
    """
    Effect: Check if interaction ID exists in database.

    Args:
        conn: Database connection
        interaction_id: Interaction ID to check

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute("SELECT id FROM interactions WHERE id = ?", (interaction_id,))
    return cursor.fetchone() is not None


def _resolve_function_by_name(conn: sqlite3.Connection, function_name: str) -> Tuple[Optional[int], Tuple[FunctionMatch, ...]]:
    """
    Effect: Resolve function name to ID, with disambiguation for duplicates.

    Args:
        conn: Database connection
        function_name: Function name to look up

    Returns:
        Tuple of (function_id_or_None, matches):
        - If exactly 1 match: (id, ())
        - If >1 matches: (None, tuple_of_FunctionMatch)
        - If 0 matches: (None, ())
    """
    cursor = conn.execute(
        """SELECT f.id, f.name, fi.name AS file_name, fi.path AS file_path
        FROM functions f
        LEFT JOIN files fi ON f.file_id = fi.id
        WHERE f.name = ?""",
        (function_name,)
    )
    rows = cursor.fetchall()

    if len(rows) == 0:
        return (None, ())
    elif len(rows) == 1:
        return (rows[0]["id"], ())
    else:
        matches = tuple(
            FunctionMatch(
                id=row["id"],
                name=row["name"],
                file_name=row["file_name"],
                file_path=row["file_path"]
            )
            for row in rows
        )
        return (None, matches)


def _find_interaction_id(
    conn: sqlite3.Connection,
    source_function_id: int,
    target_function_id: int,
    interaction_type: str,
) -> Optional[int]:
    """Effect: ID of an existing (source, target, type) edge, or None."""
    row = conn.execute(
        """
        SELECT id FROM interactions
        WHERE source_function_id = ? AND target_function_id = ? AND interaction_type = ?
        ORDER BY id LIMIT 1
        """,
        (source_function_id, target_function_id, interaction_type)
    ).fetchone()
    return row[0] if row else None


def _insert_interaction_if_new(
    conn: sqlite3.Connection,
    source_function_id: int,
    target_function_id: int,
    interaction_type: str,
    description: Optional[str],
) -> Tuple[int, bool]:
    """
    Effect: Insert an edge unless the same (source, target, type) already exists.

    The interactions table has no UNIQUE constraint, and scan_call_graph re-emits
    the whole graph on every run, so a blind insert doubled the call graph each
    time. Does not commit.

    Returns:
        (interaction ID, True when newly inserted)
    """
    existing = _find_interaction_id(conn, source_function_id, target_function_id, interaction_type)
    if existing is not None:
        return (existing, False)
    cursor = conn.execute(
        """
        INSERT INTO interactions (source_function_id, target_function_id, interaction_type, description)
        VALUES (?, ?, ?, ?)
        """,
        (source_function_id, target_function_id, interaction_type, description)
    )
    return (cursor.lastrowid, True)


def _add_interaction_effect(
    conn: sqlite3.Connection,
    source_function_id: int,
    target_function_id: int,
    interaction_type: str,
    description: Optional[str] = None
) -> Tuple[int, bool]:
    """
    Effect: Insert a single interaction unless it is already tracked, and commit.

    Args:
        conn: Database connection
        source_function_id: Source function ID
        target_function_id: Target function ID
        interaction_type: Interaction type
        description: Optional description

    Returns:
        (interaction ID, True when newly inserted)
    """
    result = _insert_interaction_if_new(
        conn, source_function_id, target_function_id, interaction_type, description
    )
    conn.commit()
    return result


def _add_interactions_batch_effect(
    conn: sqlite3.Connection,
    interactions: List[Tuple[int, int, str, Optional[str]]]
) -> Tuple[Tuple[int, ...], int]:
    """
    Effect: Insert multiple interactions in one transaction, skipping edges
    already tracked (including repeats within the same batch).

    Args:
        conn: Database connection
        interactions: List of (source_function_id, target_function_id, interaction_type, description) tuples

    Returns:
        (interaction IDs in input order — existing IDs for skipped edges,
         number skipped)
    """
    try:
        results = tuple(
            _insert_interaction_if_new(conn, source_id, target_id, interaction_type, description)
            for source_id, target_id, interaction_type, description in interactions
        )
        conn.commit()
        return (
            tuple(interaction_id for interaction_id, _ in results),
            sum(1 for _, inserted in results if not inserted),
        )

    except Exception as e:
        conn.rollback()
        raise e


def _update_interaction_effect(conn: sqlite3.Connection, sql: str, params: Tuple) -> None:
    """
    Effect: Execute interaction update query.

    Args:
        conn: Database connection
        sql: UPDATE query
        params: Query parameters
    """
    conn.execute(sql, params)
    conn.commit()


def _delete_interaction_effect(
    conn: sqlite3.Connection,
    interaction_id: int,
) -> None:
    """
    Effect: Delete interaction from database.

    Args:
        conn: Database connection
        interaction_id: Interaction ID to delete
    """
    conn.execute("DELETE FROM interactions WHERE id = ?", (interaction_id,))
    conn.commit()


_INTERACTION_QUERY_BASE: str = """
    SELECT
        i.id, i.source_function_id, i.target_function_id,
        i.interaction_type, i.description, i.created_at, i.updated_at,
        sf.name AS source_function_name,
        sfi.name AS source_file_name,
        sfi.path AS source_file_path,
        tf.name AS target_function_name,
        tfi.name AS target_file_name,
        tfi.path AS target_file_path
    FROM interactions i
    LEFT JOIN functions sf ON i.source_function_id = sf.id
    LEFT JOIN files sfi ON sf.file_id = sfi.id
    LEFT JOIN functions tf ON i.target_function_id = tf.id
    LEFT JOIN files tfi ON tf.file_id = tfi.id
"""


def _get_interactions_by_function_effect(
    conn: sqlite3.Connection,
    function_name: str,
) -> List[sqlite3.Row]:
    """
    Effect: Query all interactions where function appears as source or target.

    Args:
        conn: Database connection
        function_name: Function name to look up

    Returns:
        List of Row objects with full interaction + function/file data
    """
    sql = _INTERACTION_QUERY_BASE + """
    WHERE sf.name = ? OR tf.name = ?
    ORDER BY i.created_at
    """
    cursor = conn.execute(sql, (function_name, function_name))
    return cursor.fetchall()


def _get_interactions_by_file_effect(
    conn: sqlite3.Connection,
    file_id: int,
) -> List[sqlite3.Row]:
    """
    Effect: Query all interactions involving functions in a specific file.

    Args:
        conn: Database connection
        file_id: File ID to look up interactions for

    Returns:
        List of Row objects with full interaction + function/file data
    """
    sql = _INTERACTION_QUERY_BASE + """
    WHERE sf.file_id = ? OR tf.file_id = ?
    ORDER BY i.created_at
    """
    cursor = conn.execute(sql, (file_id, file_id))
    return cursor.fetchall()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def add_interaction(
    source: str,
    target: str,
    interaction_type: str,
    project_root: Optional[str] = None
) -> AddInteractionResult:
    """
    Add function dependency/interaction (by function names).

    Creates interaction record in interactions table. Function names are
    resolved to IDs internally.

    Args:
        source: Source function name (the function making the call/reference)
        target: Target function name (the function being called/referenced)
        interaction_type: Interaction type ('call', 'chain', 'borrow', 'compose', 'pipe')

    Returns:
        AddInteractionResult with success status and the interaction ID.
        An edge already tracked with the same (source, target, type) is not
        duplicated: its existing ID comes back with already_existed=True.

    Example:
        >>> # Function 'process_data' calls 'validate_input'
        >>> result = add_interaction(
        ...     source="process_data_id_42",
        ...     target="validate_input_id_15",
        ...     interaction_type="call"
        ... )
        >>> result.success
        True
        >>> result.id
        1
    """
    # Validate interaction type
    if not validate_interaction_type(interaction_type):
        return AddInteractionResult(
            success=False,
            error=f"Invalid interaction type: {interaction_type}. Must be one of: call, chain, borrow, compose, pipe"
        )

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Resolve source function name to ID
        source_function_id, source_matches = _resolve_function_by_name(conn, source)
        if source_function_id is None and not source_matches:
            return AddInteractionResult(
                success=False,
                error=f"Source function not found: {source}"
            )
        if source_function_id is None and source_matches:
            match_list = ", ".join(
                f"[id={m.id}, file={m.file_path or 'unknown'}]" for m in source_matches
            )
            return AddInteractionResult(
                success=False,
                error=f"Ambiguous source function '{source}' — multiple matches found: {match_list}. "
                      f"Call again with the specific function ID using add_interactions instead."
            )

        # Resolve target function name to ID
        target_function_id, target_matches = _resolve_function_by_name(conn, target)
        if target_function_id is None and not target_matches:
            return AddInteractionResult(
                success=False,
                error=f"Target function not found: {target}"
            )
        if target_function_id is None and target_matches:
            match_list = ", ".join(
                f"[id={m.id}, file={m.file_path or 'unknown'}]" for m in target_matches
            )
            return AddInteractionResult(
                success=False,
                error=f"Ambiguous target function '{target}' — multiple matches found: {match_list}. "
                      f"Call again with the specific function ID using add_interactions instead."
            )

        # Effect: add interaction (an already-tracked edge is not duplicated)
        interaction_id, inserted = _add_interaction_effect(
            conn,
            source_function_id,
            target_function_id,
            interaction_type
        )

        # Success - fetch return statements from core database
        return_statements = get_return_statements("add_interaction")

        return AddInteractionResult(
            success=True,
            id=interaction_id,
            already_existed=not inserted,
            return_statements=return_statements
        )

    except Exception as e:
        return AddInteractionResult(
            success=False,
            error=f"Failed to add interaction: {str(e)}"
        )

    finally:
        conn.close()


def add_interactions(
    interactions: List[Tuple[int, int, str, Optional[str]]],
    project_root: Optional[str] = None
) -> AddInteractionsResult:
    """
    Add multiple interactions at once (by function IDs).

    More efficient than multiple single-interaction calls. Uses function IDs
    directly instead of names for better performance.

    Args:
        interactions: List of (source_function_id, target_function_id, interaction_type, description) tuples
            Interaction types: 'call', 'chain', 'borrow', 'compose', 'pipe'

    Returns:
        AddInteractionsResult with IDs in input order, added_count and
        skipped_count. Edges already tracked with the same (source, target,
        type) — including repeats within the batch — are skipped, and their
        existing IDs appear in ids, so re-running scan_call_graph output is safe.

    Example:
        >>> # Multiple function calls
        >>> interactions = [
        ...     (42, 15, 'call', 'Validates input'),
        ...     (42, 16, 'call', 'Transforms data'),
        ...     (42, 17, 'pipe', 'Pipes to formatter')
        ... ]
        >>> result = add_interactions(interactions)
        >>> result.success
        True
        >>> len(result.ids)
        3
    """
    # Validate input
    if not interactions:
        return AddInteractionsResult(
            success=False,
            error="Interactions list cannot be empty"
        )

    # Validate all interaction types
    for source_id, target_id, interaction_type, description in interactions:
        if not validate_interaction_type(interaction_type):
            return AddInteractionsResult(
                success=False,
                error=f"Invalid interaction type: {interaction_type}. Must be one of: call, chain, borrow, compose, pipe"
            )

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Validate all function IDs exist
        for source_id, target_id, interaction_type, description in interactions:
            if not _check_function_exists(conn, source_id):
                return AddInteractionsResult(
                    success=False,
                    error=f"Source function with ID {source_id} not found"
                )

            if not _check_function_exists(conn, target_id):
                return AddInteractionsResult(
                    success=False,
                    error=f"Target function with ID {target_id} not found"
                )

        # Effect: add all interactions in transaction, skipping tracked edges
        interaction_ids, skipped = _add_interactions_batch_effect(conn, interactions)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("add_interactions")

        return AddInteractionsResult(
            success=True,
            ids=interaction_ids,
            added_count=len(interaction_ids) - skipped,
            skipped_count=skipped,
            return_statements=return_statements
        )

    except Exception as e:
        return AddInteractionsResult(
            success=False,
            error=f"Batch add failed: {str(e)}"
        )

    finally:
        conn.close()


def update_interaction(
    interaction_id: int,
    source_function_id: Optional[int] = None,
    target_function_id: Optional[int] = None,
    interaction_type: Optional[str] = None,
    description: Optional[str] = None,
    project_root: Optional[str] = None
) -> UpdateInteractionResult:
    """
    Update interaction metadata.

    Only updates non-NULL parameters. Can change source, target, type, or description.

    Args:
        interaction_id: Interaction ID to update
        source_function_id: New source function ID (None = don't update)
        target_function_id: New target function ID (None = don't update)
        interaction_type: New interaction type (None = don't update)
        description: New description (None = don't update)

    Returns:
        UpdateInteractionResult with success status

    Example:
        >>> # Update interaction type from 'call' to 'compose'
        >>> result = update_interaction(1, interaction_type='compose')
        >>> result.success
        True

        >>> # Update description only
        >>> result = update_interaction(
        ...     1,
        ...     description="Composes validation with transformation"
        ... )
        >>> result.success
        True
    """
    # Validate at least one parameter is provided
    if all(v is None for v in [source_function_id, target_function_id, interaction_type, description]):
        return UpdateInteractionResult(
            success=False,
            error="At least one parameter must be provided for update"
        )

    # Validate interaction type if provided
    if interaction_type is not None and not validate_interaction_type(interaction_type):
        return UpdateInteractionResult(
            success=False,
            error=f"Invalid interaction type: {interaction_type}. Must be one of: call, chain, borrow, compose, pipe"
        )

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if interaction exists
        if not _check_interaction_exists(conn, interaction_id):
            return UpdateInteractionResult(
                success=False,
                error=f"Interaction with ID {interaction_id} not found"
            )

        # Validate new function IDs if provided
        if source_function_id is not None and not _check_function_exists(conn, source_function_id):
            return UpdateInteractionResult(
                success=False,
                error=f"Source function with ID {source_function_id} not found"
            )

        if target_function_id is not None and not _check_function_exists(conn, target_function_id):
            return UpdateInteractionResult(
                success=False,
                error=f"Target function with ID {target_function_id} not found"
            )

        # Pure: build update query
        sql, params = build_update_query(interaction_id, source_function_id, target_function_id, interaction_type, description)

        # Effect: execute update
        _update_interaction_effect(conn, sql, params)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("update_interaction")

        return UpdateInteractionResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return UpdateInteractionResult(
            success=False,
            error=f"Failed to update interaction: {str(e)}"
        )

    finally:
        conn.close()


def delete_interaction(
    interaction_id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion",
    project_root: Optional[str] = None
) -> DeleteInteractionResult:
    """
    Delete interaction (internal use, not exposed as MCP tool).

    Removes interaction from database and creates audit note.

    Args:
        interaction_id: Interaction ID to delete
        note_reason: Deletion reason
        note_severity: 'info', 'warning', 'error'
        note_source: 'ai' or 'user'
        note_type: Note type (default: 'entry_deletion')

    Returns:
        DeleteInteractionResult with success status

    Example:
        >>> result = delete_interaction(
        ...     1,
        ...     note_reason="Function dependency removed from code",
        ...     note_severity="info",
        ...     note_source="ai"
        ... )
        >>> result.success
        True
    """
    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if interaction exists
        if not _check_interaction_exists(conn, interaction_id):
            return DeleteInteractionResult(
                success=False,
                error=f"Interaction with ID {interaction_id} not found"
            )

        # Create note entry
        _create_deletion_note(
            conn,
            "interactions",
            interaction_id,
            note_reason,
            note_severity,
            note_source,
            note_type
        )

        # Delete interaction
        _delete_interaction_effect(conn, interaction_id)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("delete_interaction")

        return DeleteInteractionResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return DeleteInteractionResult(
            success=False,
            error=f"Failed to delete interaction: {str(e)}"
        )

    finally:
        conn.close()


def get_interactions_by_function(
    function_name: str,
    project_root: Optional[str] = None
) -> InteractionQueryResult:
    """
    Get all interactions for a function (both as source and target).

    Returns interactions where the named function is the caller (source) or
    the callee (target). Use to trace dependency graphs — what does this
    function call, and what calls it?

    Args:
        function_name: Function name to look up (e.g., 'validate_input_id_15')

    Returns:
        InteractionQueryResult with tuple of InteractionRecord objects including
        resolved function names and file paths for both source and target

    Example:
        >>> result = get_interactions_by_function("validate_input_id_15")
        >>> result.success
        True
        >>> for i in result.interactions:
        ...     print(f"{i.source_function_name} --{i.interaction_type}--> {i.target_function_name}")
        process_data_id_42 --call--> validate_input_id_15
        validate_input_id_15 --chain--> format_output_id_23
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_interactions_by_function_effect(conn, function_name)
        records = tuple(row_to_interaction_record(row) for row in rows)

        return InteractionQueryResult(
            success=True,
            interactions=records,
            return_statements=get_return_statements("get_interactions_by_function")
        )

    except Exception as e:
        return InteractionQueryResult(
            success=False,
            error=f"Query failed: {str(e)}",
        )

    finally:
        conn.close()


def get_interactions_by_file(
    file_id: int,
    project_root: Optional[str] = None
) -> InteractionQueryResult:
    """
    Get all interactions for functions in a file.

    Returns all interactions where any function belonging to the given file
    is the source or target. Use to understand a file's role in the broader
    dependency graph.

    Args:
        file_id: File ID to look up interactions for

    Returns:
        InteractionQueryResult with tuple of InteractionRecord objects including
        resolved function names and file paths for both source and target

    Example:
        >>> result = get_interactions_by_file(5)
        >>> result.success
        True
        >>> len(result.interactions)
        7
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_interactions_by_file_effect(conn, file_id)
        records = tuple(row_to_interaction_record(row) for row in rows)

        return InteractionQueryResult(
            success=True,
            interactions=records,
            return_statements=get_return_statements("get_interactions_by_file")
        )

    except Exception as e:
        return InteractionQueryResult(
            success=False,
            error=f"Query failed: {str(e)}",
        )

    finally:
        conn.close()
