"""
AIMFP Helper Functions - Project Types (Part 2)

Type-function relationship management (types_functions junction table).
Manages the relationships between types and their associated functions.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- add_types_functions: Add type-function relationship(s)
- update_type_function_role: Update relationship role
- delete_type_function: Remove type-function relationship
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional, List, Tuple

from ..utils import get_return_statements
from ._common import _check_type_exists, _check_function_exists, _create_deletion_note, get_cached_project_root, _open_project_connection


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class AddRelationshipsResult:
    """Result of adding type-function relationships."""
    success: bool
    ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    added_count: int = 0
    skipped_count: int = 0  # links already tracked; their existing ids are in ids
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class UpdateRoleResult:
    """Result of updating relationship role."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class DeleteRelationshipResult:
    """Result of deleting type-function relationship."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


# ============================================================================
# Pure Helper Functions
# ============================================================================

def validate_role(role: str) -> bool:
    """
    Validate that role is one of the allowed values.

    Pure function - no side effects, deterministic.

    Args:
        role: Role string to validate

    Returns:
        True if valid, False otherwise
    """
    valid_roles = {
        'factory',
        'transformer',
        'operator',
        'pattern_matcher',
        'accessor',
        'validator',
        'combinator'
    }
    return role in valid_roles


# ============================================================================
# Database Effect Functions
# ============================================================================




def _check_relationship_exists(
    conn: sqlite3.Connection,
    relationship_id: int
) -> bool:
    """
    Effect: Check if types_functions relationship exists.

    Args:
        conn: Database connection
        relationship_id: Relationship ID to check

    Returns:
        True if exists, False otherwise
    """
    cursor = conn.execute(
        "SELECT id FROM types_functions WHERE id = ?",
        (relationship_id,)
    )
    return cursor.fetchone() is not None


def _insert_relationship_if_new(
    conn: sqlite3.Connection,
    type_id: int,
    function_id: int,
    role: str
) -> Tuple[int, bool]:
    """
    Effect: Insert a type-function link unless the same (type, function, role)
    exists. Does not commit.

    types_functions is UNIQUE on that triple, so a blind insert failed the whole
    batch on one repeat; skipping matches add_files_to_module / add_file_flows.

    Returns:
        (relationship ID, True when newly inserted)
    """
    row = conn.execute(
        "SELECT id FROM types_functions WHERE type_id = ? AND function_id = ? AND role = ?",
        (type_id, function_id, role)
    ).fetchone()
    if row is not None:
        return (row[0], False)
    cursor = conn.execute(
        """
        INSERT INTO types_functions (type_id, function_id, role)
        VALUES (?, ?, ?)
        """,
        (type_id, function_id, role)
    )
    return (cursor.lastrowid, True)


def _add_relationships_batch_effect(
    conn: sqlite3.Connection,
    relationships: List[Tuple[int, int, str]]
) -> Tuple[Tuple[int, ...], int]:
    """
    Effect: Insert type-function relationships in one transaction, skipping
    links already tracked (including repeats within the batch).

    Args:
        conn: Database connection
        relationships: List of (type_id, function_id, role) tuples

    Returns:
        (relationship IDs in input order — existing IDs for skipped links,
         number skipped)
    """
    try:
        results = tuple(
            _insert_relationship_if_new(conn, type_id, function_id, role)
            for type_id, function_id, role in relationships
        )
        conn.commit()
        return (
            tuple(relationship_id for relationship_id, _ in results),
            sum(1 for _, inserted in results if not inserted),
        )

    except Exception as e:
        conn.rollback()
        raise e


def _update_relationship_role_effect(
    conn: sqlite3.Connection,
    type_id: int,
    function_id: int,
    role: str
) -> None:
    """
    Effect: Update role for type-function relationship.

    Args:
        conn: Database connection
        type_id: Type ID
        function_id: Function ID
        role: New role
    """
    conn.execute(
        """
        UPDATE types_functions
        SET role = ?
        WHERE type_id = ? AND function_id = ?
        """,
        (role, type_id, function_id)
    )
    conn.commit()


def _delete_relationship_effect(
    conn: sqlite3.Connection,
    relationship_id: int,
) -> None:
    """
    Effect: Delete type-function relationship from database.

    Args:
        conn: Database connection
        relationship_id: Relationship ID to delete
    """
    conn.execute("DELETE FROM types_functions WHERE id = ?", (relationship_id,))
    conn.commit()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def add_types_functions(
    relationships: List[Tuple[int, int, str]],
    project_root: Optional[str] = None
) -> AddRelationshipsResult:
    """
    Add type-function relationship(s).

    Creates relationships in types_functions junction table. Roles help FP analysis
    understand type usage patterns.

    Args:
        relationships: List of (type_id, function_id, role) tuples
            Roles: 'factory', 'transformer', 'operator', 'pattern_matcher',
                   'accessor', 'validator', 'combinator'

    Returns:
        AddRelationshipsResult with IDs in input order, added_count and
        skipped_count. A link already tracked with the same (type, function,
        role) is skipped and its existing ID returned, instead of failing the
        batch on the UNIQUE constraint.

    Example:
        >>> # Single relationship
        >>> result = add_types_functions(
        ...     [(7, 99, 'factory')]  # Maybe_id_7, create_maybe_id_99
        ... )
        >>> result.success
        True
        >>> result.ids
        (1,)

        >>> # Multiple relationships
        >>> relationships = [
        ...     (7, 99, 'factory'),      # create_maybe
        ...     (7, 100, 'transformer'), # map_maybe
        ...     (7, 101, 'accessor')     # unwrap_maybe
        ... ]
        >>> result = add_types_functions(relationships)
        >>> result.success
        True
        >>> len(result.ids)
        3
    """
    # Validate input
    if not relationships:
        return AddRelationshipsResult(
            success=False,
            error="Relationships list cannot be empty"
        )

    # Validate all roles
    for type_id, function_id, role in relationships:
        if not validate_role(role):
            return AddRelationshipsResult(
                success=False,
                error=f"Invalid role: {role}. Must be one of: factory, transformer, operator, pattern_matcher, accessor, validator, combinator"
            )

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Validate all type_ids and function_ids exist
        for type_id, function_id, role in relationships:
            if not _check_type_exists(conn, type_id):
                return AddRelationshipsResult(
                    success=False,
                    error=f"Type with ID {type_id} not found"
                )

            if not _check_function_exists(conn, function_id):
                return AddRelationshipsResult(
                    success=False,
                    error=f"Function with ID {function_id} not found"
                )

        # Effect: add all relationships, skipping links already tracked
        relationship_ids, skipped = _add_relationships_batch_effect(conn, relationships)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("add_types_functions")

        return AddRelationshipsResult(
            success=True,
            ids=relationship_ids,
            added_count=len(relationship_ids) - skipped,
            skipped_count=skipped,
            return_statements=return_statements
        )

    except Exception as e:
        return AddRelationshipsResult(
            success=False,
            error=f"Failed to add relationships: {str(e)}"
        )

    finally:
        conn.close()


def update_type_function_role(
    type_id: int,
    function_id: int,
    role: str,
    project_root: Optional[str] = None
) -> UpdateRoleResult:
    """
    Update relationship role only (internal use, not exposed as MCP tool).

    Updates the role field for an existing type-function relationship.
    For type_id or function_id changes, delete the old relationship and add a new one.

    Args:
        type_id: Type ID
        function_id: Function ID
        role: New role ('factory', 'transformer', 'operator', 'pattern_matcher',
                       'accessor', 'validator', 'combinator')

    Returns:
        UpdateRoleResult with success status

    Example:
        >>> # Change role from 'factory' to 'transformer'
        >>> result = update_type_function_role(7, 99, 'transformer')
        >>> result.success
        True
    """
    # Validate role
    if not validate_role(role):
        return UpdateRoleResult(
            success=False,
            error=f"Invalid role: {role}. Must be one of: factory, transformer, operator, pattern_matcher, accessor, validator, combinator"
        )

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Validate type and function exist
        if not _check_type_exists(conn, type_id):
            return UpdateRoleResult(
                success=False,
                error=f"Type with ID {type_id} not found"
            )

        if not _check_function_exists(conn, function_id):
            return UpdateRoleResult(
                success=False,
                error=f"Function with ID {function_id} not found"
            )

        # Effect: update role
        _update_relationship_role_effect(conn, type_id, function_id, role)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("update_type_function_role")

        return UpdateRoleResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return UpdateRoleResult(
            success=False,
            error=f"Failed to update role: {str(e)}"
        )

    finally:
        conn.close()


def delete_type_function(
    id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion",
    project_root: Optional[str] = None
) -> DeleteRelationshipResult:
    """
    Remove type-function relationship (internal use, not exposed as MCP tool).

    Deletes relationship from types_functions table and creates audit note.

    Args:
        id: types_functions junction table ID
        note_reason: Deletion reason
        note_severity: 'info', 'warning', 'error'
        note_source: 'ai' or 'user'
        note_type: Note type (default: 'entry_deletion')

    Returns:
        DeleteRelationshipResult with success status

    Example:
        >>> result = delete_type_function(
        ...     1,
        ...     note_reason="Relationship no longer valid",
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
        # Check if relationship exists
        if not _check_relationship_exists(conn, id):
            return DeleteRelationshipResult(
                success=False,
                error=f"Relationship with ID {id} not found"
            )

        # Create note entry
        _create_deletion_note(
            conn,
            "types",
            id,
            note_reason,
            note_severity,
            note_source,
            note_type
        )

        # Delete relationship
        _delete_relationship_effect(conn, id)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("delete_type_function")

        return DeleteRelationshipResult(
            success=True,
            return_statements=return_statements
        )

    except Exception as e:
        return DeleteRelationshipResult(
            success=False,
            error=f"Failed to delete relationship: {str(e)}"
        )

    finally:
        conn.close()
