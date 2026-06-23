"""
AIMFP Helper Functions - Project Types (Part 1)

Type (ADT) reservation, finalization, update, and deletion operations.
Implements reserve/finalize pattern for rename-proof ID-based type tracking.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- reserve_type: Reserve type ID before creation
- reserve_types: Reserve multiple type IDs (batch)
- finalize_type: Finalize reserved type after creation
- finalize_types: Finalize multiple types (batch)
- update_type: Update type metadata
- delete_type: Delete type with validation
"""

import sqlite3
import json
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

# Import global utilities
from ..utils import get_return_statements
from ..shared.slugs import mint_slug

# Import update_file_timestamp from files_2
from ._common import _check_file_exists, _check_type_exists, _create_deletion_note, get_cached_project_root, _open_project_connection
from .files_2 import update_file_timestamp


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class TypeRecord:
    """Immutable type record from database."""
    id: int
    name: str
    file_id: Optional[int]
    definition_json: str  # JSON string
    description: Optional[str]
    links: Optional[str]  # JSON string
    is_reserved: bool
    id_in_name: bool
    file_name: Optional[str]  # From JOIN with files table
    file_path: Optional[str]  # From JOIN with files table
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReserveResult:
    """Result of type reservation operation."""
    success: bool
    id: Optional[int] = None
    is_reserved: Optional[bool] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class ReserveBatchResult:
    """Result of batch type reservation."""
    success: bool
    ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class FinalizeResult:
    """Result of type finalization operation."""
    success: bool
    type_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class FinalizeBatchResult:
    """Result of batch type finalization."""
    success: bool
    finalized_ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class UpdateResult:
    """Result of type update operation."""
    success: bool
    type_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class FunctionRelationship:
    """Function-type relationship."""
    function_id: int
    function_name: str
    role: str


@dataclass(frozen=True)
class TypeQueryResult:
    """Result of type lookup that may return multiple matches (e.g. by name)."""
    success: bool
    types: Tuple[TypeRecord, ...] = ()
    error: Optional[str] = None


@dataclass(frozen=True)
class DeleteResult:
    """Result of type deletion operation."""
    success: bool
    deleted_type_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    function_relationships: Tuple[FunctionRelationship, ...] = ()
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


# ============================================================================
# Pure Helper Functions
# ============================================================================

def validate_type_id_in_name(name: str, type_id: int) -> bool:
    """
    Validate that type name contains _id_{type_id} pattern.

    Pure function - no side effects, deterministic.

    Args:
        name: Type name to validate
        type_id: Expected type ID

    Returns:
        True if pattern found, False otherwise

    Example:
        >>> validate_type_id_in_name("Maybe_id_7", 7)
        True
        >>> validate_type_id_in_name("Maybe", 7)
        False
    """
    expected_pattern = f"_id_{type_id}"
    return expected_pattern in name


def serialize_definition(definition: Dict[str, Any]) -> str:
    """
    Serialize ADT definition to JSON string.

    Pure function - deterministic serialization.

    Args:
        definition: ADT definition object

    Returns:
        JSON string
    """
    return json.dumps(definition)


def serialize_links(links: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Serialize links to JSON string.

    Pure function - deterministic serialization.

    Args:
        links: Links object

    Returns:
        JSON string or None
    """
    if links is None:
        return None
    return json.dumps(links)


def build_update_query(
    type_id: int,
    name: Optional[str],
    file_id: Optional[int],
    definition_json: Optional[str],
    description: Optional[str]
) -> Tuple[str, Tuple]:
    """
    Build SQL UPDATE query with only non-NULL fields.

    Pure function - deterministic query building.

    Args:
        type_id: Type ID to update
        name: New name (None = don't update)
        file_id: New file_id (None = don't update)
        definition_json: New definition JSON (None = don't update)
        description: New description (None = don't update)

    Returns:
        Tuple of (sql_query, parameters)
    """
    updates = []
    params_list = []

    if name is not None:
        updates.append("name = ?")
        params_list.append(name)

    if file_id is not None:
        updates.append("file_id = ?")
        params_list.append(file_id)

    if definition_json is not None:
        updates.append("definition_json = ?")
        params_list.append(definition_json)

    if description is not None:
        updates.append("description = ?")
        params_list.append(description)

    # Always update timestamp
    updates.append("updated_at = CURRENT_TIMESTAMP")

    # Build query
    sql = f"UPDATE types SET {', '.join(updates)} WHERE id = ?"
    params_list.append(type_id)

    return (sql, tuple(params_list))


def row_to_type_record(
    row: sqlite3.Row,
    include_details: bool = True,
    details_only: bool = False
) -> TypeRecord:
    """
    Convert database row to immutable TypeRecord.

    Pure function - deterministic mapping. Handles optional file_name/file_path
    fields that are present when query includes JOIN with files table.

    Args:
        row: SQLite row object
        include_details: If False, omit definition_json/description (lightweight listing)
        details_only: If True, return only id/name + definition_json/description

    Returns:
        Immutable TypeRecord
    """
    keys = row.keys()

    if details_only:
        return TypeRecord(
            id=row["id"],
            name=row["name"],
            file_id=row["file_id"],
            definition_json=row["definition_json"],
            description=row["description"],
            links=row["links"],
            is_reserved=False,
            id_in_name=False,
            file_name=None,
            file_path=None,
            created_at="",
            updated_at=""
        )

    return TypeRecord(
        id=row["id"],
        name=row["name"],
        file_id=row["file_id"],
        definition_json=row["definition_json"] if include_details else None,
        description=row["description"] if include_details else None,
        links=row["links"] if include_details else None,
        is_reserved=bool(row["is_reserved"]),
        id_in_name=bool(row["id_in_name"]),
        file_name=row["file_name"] if "file_name" in keys else None,
        file_path=row["file_path"] if "file_path" in keys else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


# ============================================================================
# Database Effect Functions
# ============================================================================




def _check_is_reserved(conn: sqlite3.Connection, type_id: int) -> bool:
    """
    Effect: Check if type is reserved.

    Args:
        conn: Database connection
        type_id: Type ID to check

    Returns:
        True if reserved, False otherwise
    """
    cursor = conn.execute(
        "SELECT is_reserved FROM types WHERE id = ?",
        (type_id,)
    )
    row = cursor.fetchone()
    return bool(row["is_reserved"]) if row else False


def _get_type_file_id(conn: sqlite3.Connection, type_id: int) -> Optional[int]:
    """
    Effect: Get file_id for type.

    Args:
        conn: Database connection
        type_id: Type ID

    Returns:
        File ID or None if not found or NULL
    """
    cursor = conn.execute(
        "SELECT file_id FROM types WHERE id = ?",
        (type_id,)
    )
    row = cursor.fetchone()
    return row["file_id"] if row else None


def _reserve_type_effect(
    conn: sqlite3.Connection,
    name: str,
    definition_json: str,
    description: Optional[str],
    links_json: Optional[str],
    file_id: Optional[int],
    id_in_name: bool = True
) -> int:
    """
    Effect: Insert reserved type into database.

    Args:
        conn: Database connection
        name: Preliminary type name
        definition_json: ADT definition JSON string
        description: Type description
        links_json: Links JSON string
        file_id: File ID where type is defined
        id_in_name: Whether type name will contain _id_XX pattern (default True)

    Returns:
        Reserved type ID
    """
    cursor = conn.execute(
        """
        INSERT INTO types (entity_key, name, definition_json, description, links, file_id, is_reserved, id_in_name)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (mint_slug("ty", name), name, definition_json, description, links_json, file_id, 1 if id_in_name else 0)
    )
    conn.commit()
    return cursor.lastrowid


def _reserve_types_batch_effect(
    conn: sqlite3.Connection,
    types: List[Tuple[str, str, Optional[str], Optional[str], Optional[int], bool]]
) -> Tuple[int, ...]:
    """
    Effect: Insert multiple reserved types in transaction.

    Args:
        conn: Database connection
        types: List of (name, definition_json, description, links_json, file_id, id_in_name) tuples

    Returns:
        Tuple of reserved type IDs in same order
    """
    cursor = conn.cursor()
    ids = []

    try:
        for name, definition_json, description, links_json, file_id, id_in_name in types:
            cursor.execute(
                """
                INSERT INTO types (entity_key, name, definition_json, description, links, file_id, is_reserved, id_in_name)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (mint_slug("ty", name), name, definition_json, description, links_json, file_id, 1 if id_in_name else 0)
            )
            ids.append(cursor.lastrowid)

        conn.commit()
        return tuple(ids)

    except Exception as e:
        conn.rollback()
        raise e


def _finalize_type_effect(
    conn: sqlite3.Connection,
    type_id: int,
    name: str,
    definition_json: str,
    description: Optional[str],
    links_json: Optional[str],
    file_id: Optional[int]
) -> None:
    """
    Effect: Finalize reserved type in database.

    Args:
        conn: Database connection
        type_id: Reserved type ID
        name: Final type name with _id_xx suffix
        definition_json: ADT definition JSON string
        description: Type description
        links_json: Links JSON string
        file_id: File ID where type is defined
    """
    conn.execute(
        """
        UPDATE types
        SET is_reserved = 0,
            name = ?,
            definition_json = ?,
            description = ?,
            links = ?,
            file_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (name, definition_json, description, links_json, file_id, type_id)
    )
    conn.commit()


def _finalize_types_batch_effect(
    conn: sqlite3.Connection,
    finalizations: List[Tuple[int, str, str, Optional[str], Optional[str], Optional[int]]]
) -> None:
    """
    Effect: Finalize multiple types in transaction.

    Args:
        conn: Database connection
        finalizations: List of (type_id, name, definition_json, description, links_json, file_id) tuples
    """
    cursor = conn.cursor()

    try:
        for type_id, name, definition_json, description, links_json, file_id in finalizations:
            cursor.execute(
                """
                UPDATE types
                SET is_reserved = 0,
                    name = ?,
                    definition_json = ?,
                    description = ?,
                    links = ?,
                    file_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name, definition_json, description, links_json, file_id, type_id)
            )

        conn.commit()

    except Exception as e:
        conn.rollback()
        raise e


def _update_type_effect(conn: sqlite3.Connection, sql: str, params: Tuple) -> None:
    """
    Effect: Execute type update query.

    Args:
        conn: Database connection
        sql: UPDATE query
        params: Query parameters
    """
    conn.execute(sql, params)
    conn.commit()


def _get_function_relationships(
    conn: sqlite3.Connection,
    type_id: int
) -> Tuple[FunctionRelationship, ...]:
    """
    Effect: Query function relationships for type.

    Args:
        conn: Database connection
        type_id: Type ID

    Returns:
        Tuple of FunctionRelationship objects
    """
    cursor = conn.execute(
        """
        SELECT f.id, f.name, tf.role
        FROM types_functions tf
        JOIN functions f ON tf.function_id = f.id
        WHERE tf.type_id = ?
        """,
        (type_id,)
    )

    relationships = tuple(
        FunctionRelationship(
            function_id=row["id"],
            function_name=row["name"],
            role=row["role"]
        )
        for row in cursor.fetchall()
    )

    return relationships


def _delete_type_effect(
    conn: sqlite3.Connection,
    type_id: int,
) -> None:
    """
    Effect: Delete type from database.

    Args:
        conn: Database connection
        type_id: Type ID to delete
    """
    conn.execute("DELETE FROM types WHERE id = ?", (type_id,))
    conn.commit()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def reserve_type(
    name: str,
    definition_json: Dict[str, Any],
    description: Optional[str] = None,
    links: Optional[Dict[str, Any]] = None,
    file_id: Optional[int] = None,
    skip_id_naming: bool = False
) -> ReserveResult:
    """
    Reserve type ID for naming before creation.

    Creates placeholder entry in types table with is_reserved=1.
    Returns ID that should be embedded in type name: {TypeName}_id_{id}

    Args:
        name: Preliminary type name (will have _id_xxx appended unless skip_id_naming=True)
        definition_json: ADT definition (e.g., {'type': 'enum', 'variants': ['A', 'B']})
        description: Type description (optional)
        links: Links to related functions (optional)
        file_id: File ID where type is defined (optional)
        skip_id_naming: If True, skip ID embedding (for MCP tools that must have clean names)

    Returns:
        ReserveResult with success status and reserved ID

    Example:
        >>> result = reserve_type(
        ...     "Maybe",
        ...     {"type": "enum", "variants": ["Just", "Nothing"]},
        ...     description="Optional value type"
        ... )
        >>> result.success
        True
        >>> result.id
        7
        # Use result.id to create: Maybe_id_7 (unless skip_id_naming=True)
    """
    # Effect: open connection
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if file_id provided and exists
        if file_id is not None and not _check_file_exists(conn, file_id):
            return ReserveResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        # Pure: serialize definition and links
        definition_str = serialize_definition(definition_json)
        links_str = serialize_links(links)

        # Effect: reserve type with id_in_name flag
        reserved_id = _reserve_type_effect(
            conn,
            name,
            definition_str,
            description,
            links_str,
            file_id,
            not skip_id_naming
        )

        # Success - fetch return statements from core database
        return_statements = get_return_statements("reserve_type")

        return ReserveResult(
            success=True,
            id=reserved_id,
            is_reserved=True,
            return_statements=return_statements
        )

    finally:
        conn.close()


def reserve_types(
    types: List[Dict[str, Any]]
) -> ReserveBatchResult:
    """
    Reserve multiple type IDs at once.

    Creates placeholder entries for multiple types in a single transaction.
    All reservations succeed or all fail (atomic operation).

    Args:
        types: List of type objects with keys: name, definition_json, description, links, file_id, skip_id_naming
               skip_id_naming is optional (defaults to False) and controls per-type ID embedding

    Returns:
        ReserveBatchResult with success status and reserved IDs
        IDs correspond to input indices: types[0] -> ids[0], types[1] -> ids[1]

    Example:
        >>> types = [
        ...     {"name": "Maybe", "definition_json": {"type": "enum", "variants": ["Just", "Nothing"]}},
        ...     {"name": "ReserveResult", "definition_json": {...}, "skip_id_naming": True}  # MCP type
        ... ]
        >>> result = reserve_types(types)
        >>> result.success
        True
        >>> result.ids
        (7, 8)
    """
    # Validate input
    if not types:
        return ReserveBatchResult(
            success=False,
            error="Types list cannot be empty"
        )

    # Effect: open connection
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Validate all file IDs if provided
        for typ in types:
            file_id = typ.get("file_id")
            if file_id is not None and not _check_file_exists(conn, file_id):
                return ReserveBatchResult(
                    success=False,
                    error=f"File with ID {file_id} not found"
                )

        # Pure: prepare data for batch insert (with per-item skip_id_naming)
        batch_data = []
        for typ in types:
            name = typ.get("name", "")
            definition_json = typ.get("definition_json", {})
            description = typ.get("description")
            links = typ.get("links")
            file_id = typ.get("file_id")
            skip_id_naming = typ.get("skip_id_naming", False)
            id_in_name = not skip_id_naming

            definition_str = serialize_definition(definition_json)
            links_str = serialize_links(links)

            batch_data.append((name, definition_str, description, links_str, file_id, id_in_name))

        # Effect: reserve all types in transaction
        reserved_ids = _reserve_types_batch_effect(conn, batch_data)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("reserve_types")

        return ReserveBatchResult(
            success=True,
            ids=reserved_ids,
            return_statements=return_statements
        )

    except Exception as e:
        return ReserveBatchResult(
            success=False,
            error=f"Batch reservation failed: {str(e)}"
        )

    finally:
        conn.close()


def finalize_type(
    type_id: int,
    name: str,
    definition_json: Dict[str, Any],
    description: Optional[str] = None,
    links: Optional[Dict[str, Any]] = None,
    file_id: Optional[int] = None,
    skip_id_naming: bool = False
) -> FinalizeResult:
    """
    Finalize reserved type after creation.

    Sets is_reserved=0 to mark type as finalized.
    Automatically updates file timestamp if file_id provided.

    Args:
        type_id: Reserved type ID
        name: Final type name with _id_xx suffix (unless skip_id_naming=True)
        definition_json: ADT definition
        description: Type description (optional)
        links: Links to related functions (optional)
        file_id: File ID where type is defined (optional)
        skip_id_naming: If True, skip ID pattern validation (for MCP tools)

    Returns:
        FinalizeResult with success status and type_id

    Example:
        >>> # After writing Maybe_id_7 in code
        >>> result = finalize_type(
        ...     type_id=7,
        ...     name="Maybe_id_7",
        ...     definition_json={"type": "enum", "variants": ["Just", "Nothing"]},
        ...     file_id=42
        ... )
        >>> result.success
        True
    """
    # Validate name contains _id_{type_id} pattern (unless skipped)
    if not skip_id_naming and not validate_type_id_in_name(name, type_id):
        return FinalizeResult(
            success=False,
            error=f"Type name must contain '_id_{type_id}' pattern"
        )

    # Pure: serialize definition and links
    definition_str = serialize_definition(definition_json)
    links_str = serialize_links(links)

    # Effect: open connection
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if file_id provided and exists
        if file_id is not None and not _check_file_exists(conn, file_id):
            return FinalizeResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        # Validation gate: merge new values with existing DB values, reject if description still null
        row = conn.execute(
            "SELECT description FROM types WHERE id = ?",
            (type_id,)
        ).fetchone()
        if row is None:
            return FinalizeResult(
                success=False,
                error=f"Type with ID {type_id} not found"
            )

        final_description = description if description is not None else row[0]
        if final_description is None:
            return FinalizeResult(
                success=False,
                error=f"Cannot finalize type '{name}': description not populated. "
                      f"Set at reserve or pass to finalize."
            )

        # Effect: finalize type
        _finalize_type_effect(
            conn,
            type_id,
            name,
            definition_str,
            final_description,
            links_str,
            file_id
        )

        conn.close()

        # Effect: update file timestamp if file_id provided
        if file_id is not None:
            timestamp_result = update_file_timestamp(file_id)
            if not timestamp_result.success:
                return FinalizeResult(
                    success=False,
                    error=f"Finalized but timestamp update failed: {timestamp_result.error}"
                )

        # Success - fetch return statements from core database
        return_statements = get_return_statements("finalize_type")

        return FinalizeResult(
            success=True,
            type_id=type_id,
            file_id=file_id,
            return_statements=return_statements
        )

    except Exception as e:
        return FinalizeResult(
            success=False,
            error=f"Database finalization failed: {str(e)}"
        )

    finally:
        # Connection already closed before update_file_timestamp call
        pass


def finalize_types(
    types: List[Dict[str, Any]]
) -> FinalizeBatchResult:
    """
    Finalize multiple reserved types.

    Updates database in transaction. Groups types by file_id for efficient timestamp updates.
    All finalizations succeed or all fail (atomic operation).

    Args:
        types: List of type objects with keys: type_id, name, definition_json, description, links, file_id, skip_id_naming
               skip_id_naming is optional (defaults to False) and controls per-type validation

    Returns:
        FinalizeBatchResult with success status and finalized IDs

    Example:
        >>> types = [
        ...     {"type_id": 7, "name": "Maybe_id_7", "definition_json": {...}, "file_id": 42},
        ...     {"type_id": 8, "name": "ReserveResult", "definition_json": {...}, "file_id": 42, "skip_id_naming": True}
        ... ]
        >>> result = finalize_types(types)
        >>> result.success
        True
        >>> result.finalized_ids
        (7, 8)
    """
    # Validate input
    if not types:
        return FinalizeBatchResult(
            success=False,
            error="Types list cannot be empty"
        )

    # Validate all names and prepare finalization data
    finalizations = []
    file_ids = set()

    for typ in types:
        type_id = typ.get("type_id")
        name = typ.get("name", "")
        file_id = typ.get("file_id")
        skip_id_naming = typ.get("skip_id_naming", False)

        if type_id is None:
            return FinalizeBatchResult(
                success=False,
                error="All types must have type_id"
            )

        # Validate name pattern (unless skipped for this item)
        if not skip_id_naming and not validate_type_id_in_name(name, type_id):
            return FinalizeBatchResult(
                success=False,
                error=f"Type name '{name}' must contain '_id_{type_id}' pattern"
            )

        # Pure: serialize definition and links
        definition_json = typ.get("definition_json", {})
        definition_str = serialize_definition(definition_json)
        links_str = serialize_links(typ.get("links"))
        description = typ.get("description")

        finalizations.append((type_id, name, definition_str, description, links_str, file_id))
        if file_id is not None:
            file_ids.add(file_id)

    # Effect: open connection and finalize batch
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Validate all file IDs exist
        for file_id in file_ids:
            if not _check_file_exists(conn, file_id):
                return FinalizeBatchResult(
                    success=False,
                    error=f"File with ID {file_id} not found"
                )

        # Validation gate: merge new values with DB values, reject if description still null
        validated_finalizations = []
        for typ_id, typ_name, typ_def_str, typ_desc, typ_links_str, typ_file_id in finalizations:
            row = conn.execute(
                "SELECT description FROM types WHERE id = ?",
                (typ_id,)
            ).fetchone()
            if row is None:
                return FinalizeBatchResult(
                    success=False,
                    error=f"Type with ID {typ_id} not found"
                )

            final_description = typ_desc if typ_desc is not None else row[0]
            if final_description is None:
                return FinalizeBatchResult(
                    success=False,
                    error=f"Cannot finalize type '{typ_name}': description not populated. "
                          f"Set at reserve or pass to finalize."
                )

            validated_finalizations.append((typ_id, typ_name, typ_def_str, final_description, typ_links_str, typ_file_id))

        # Effect: finalize all types in transaction
        _finalize_types_batch_effect(conn, validated_finalizations)

        conn.close()

        # Effect: update timestamps for all affected files
        for file_id in file_ids:
            timestamp_result = update_file_timestamp(file_id)
            if not timestamp_result.success:
                return FinalizeBatchResult(
                    success=False,
                    error=f"Finalized but timestamp update failed for file {file_id}: {timestamp_result.error}"
                )

        # Success - fetch return statements from core database
        return_statements = get_return_statements("finalize_types")

        return FinalizeBatchResult(
            success=True,
            finalized_ids=tuple(f[0] for f in finalizations),
            return_statements=return_statements
        )

    except Exception as e:
        return FinalizeBatchResult(
            success=False,
            error=f"Batch finalization failed: {str(e)}"
        )

    finally:
        # Connection already closed before update_file_timestamp calls
        pass


def update_type(
    type_id: int,
    name: Optional[str] = None,
    file_id: Optional[int] = None,
    definition_json: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None
) -> UpdateResult:
    """
    Update type metadata.

    Only updates non-NULL parameters. Name changes must preserve _id_xxx suffix.
    Automatically updates file timestamp if file_id is in database.

    Args:
        type_id: Type ID to update
        name: New type name (None = don't update)
        file_id: New file_id (None = don't update)
        definition_json: New ADT definition (None = don't update)
        description: New description (None = don't update)

    Returns:
        UpdateResult with success status, type_id, and file_id

    Example:
        >>> # Update only the description
        >>> result = update_type(7, description="Optional value type")
        >>> result.success
        True

        >>> # Update name and definition
        >>> result = update_type(
        ...     7,
        ...     name="Maybe_id_7",
        ...     definition_json={"type": "enum", "variants": ["Just", "Nothing"]}
        ... )
        >>> result.success
        True
    """
    # Validate at least one parameter is provided
    if name is None and file_id is None and definition_json is None and description is None:
        return UpdateResult(
            success=False,
            error="At least one parameter (name, file_id, definition_json, description) must be provided"
        )

    # Effect: open connection
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if type exists
        if not _check_type_exists(conn, type_id):
            return UpdateResult(
                success=False,
                error=f"Type with ID {type_id} not found"
            )

        # Validate name pattern if name is being updated and entity uses id_in_name
        if name is not None:
            cursor = conn.execute("SELECT id_in_name FROM types WHERE id = ?", (type_id,))
            row = cursor.fetchone()
            if row and bool(row["id_in_name"]) and not validate_type_id_in_name(name, type_id):
                return UpdateResult(
                    success=False,
                    error=f"Type name must contain '_id_{type_id}' pattern"
                )

        # Validate new file_id if provided
        if file_id is not None and not _check_file_exists(conn, file_id):
            return UpdateResult(
                success=False,
                error=f"File with ID {file_id} not found"
            )

        # Get current file_id before update
        current_file_id = _get_type_file_id(conn, type_id)

        # Pure: serialize definition if provided
        definition_str = serialize_definition(definition_json) if definition_json is not None else None

        # Pure: build update query
        sql, params = build_update_query(type_id, name, file_id, definition_str, description)

        # Effect: execute update
        _update_type_effect(conn, sql, params)

        # Determine which file_id to use for timestamp update
        # Use new file_id if provided, otherwise use current file_id
        timestamp_file_id = file_id if file_id is not None else current_file_id

        conn.close()

        # Effect: update file timestamp if file_id exists
        if timestamp_file_id is not None:
            timestamp_result = update_file_timestamp(timestamp_file_id)
            if not timestamp_result.success:
                return UpdateResult(
                    success=False,
                    error=f"Updated but timestamp update failed: {timestamp_result.error}"
                )

        # Success - fetch return statements from core database
        return_statements = get_return_statements("update_type")

        return UpdateResult(
            success=True,
            type_id=type_id,
            file_id=timestamp_file_id,
            return_statements=return_statements
        )

    except Exception as e:
        return UpdateResult(
            success=False,
            error=f"Database update failed: {str(e)}"
        )

    finally:
        # Connection already closed before update_file_timestamp call
        pass


def delete_type(
    type_id: int,
    note_reason: str,
    note_severity: str,
    note_source: str,
    note_type: str = "entry_deletion"
) -> DeleteResult:
    """
    Delete type with relationship validation.

    Validates no function relationships exist before deletion. Requires manual unlinking
    from types_functions first.

    Args:
        type_id: Type ID to delete
        note_reason: Deletion reason
        note_severity: 'info', 'warning', 'error'
        note_source: 'ai' or 'user'
        note_type: Note type (default: 'entry_deletion')

    Returns:
        DeleteResult with success status or function relationships list

    Example:
        >>> result = delete_type(
        ...     7,
        ...     note_reason="Type no longer needed",
        ...     note_severity="info",
        ...     note_source="ai"
        ... )
        >>> result.success
        True
        >>> result.deleted_type_id
        7
        >>> result.file_id
        42
    """
    # Effect: open connection
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if type exists
        if not _check_type_exists(conn, type_id):
            return DeleteResult(
                success=False,
                error=f"Type with ID {type_id} not found"
            )

        # Check if type is reserved
        if _check_is_reserved(conn, type_id):
            return DeleteResult(
                success=False,
                error="type_reserved"
            )

        # Check for function relationships
        func_rels = _get_function_relationships(conn, type_id)

        # If function relationships exist, return error
        if func_rels:
            return DeleteResult(
                success=False,
                error="types_functions_exist",
                function_relationships=func_rels
            )

        # Get file_id before deletion
        file_id = _get_type_file_id(conn, type_id)

        # No blocking dependencies - proceed with deletion
        # Create note entry
        _create_deletion_note(
            conn,
            "types",
            type_id,
            note_reason,
            note_severity,
            note_source,
            note_type
        )

        # Delete type
        _delete_type_effect(conn, type_id)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("delete_type")

        return DeleteResult(
            success=True,
            deleted_type_id=type_id,
            file_id=file_id,
            return_statements=return_statements
        )

    except Exception as e:
        return DeleteResult(
            success=False,
            error=f"Database deletion failed: {str(e)}"
        )

    finally:
        conn.close()


# ============================================================================
# Type Lookup by Name
# ============================================================================

def _get_type_by_name_effect(conn: sqlite3.Connection, type_name: str) -> List[sqlite3.Row]:
    """
    Effect: Query types by name with file data via JOIN.

    Args:
        conn: Database connection
        type_name: Type name to search for

    Returns:
        List of matching rows (may be empty, one, or many)
    """
    cursor = conn.execute(
        """SELECT t.*, fi.name AS file_name, fi.path AS file_path
        FROM types t
        LEFT JOIN files fi ON t.file_id = fi.id
        WHERE t.name = ?""",
        (type_name,)
    )
    return cursor.fetchall()


def search_types(
    search_string: str,
    include_details: bool = True,
    details_only: bool = False
) -> TypeQueryResult:
    """
    Search types by name or description using FTS5 full-text search.

    Returns relevance-ranked results. Falls back to LIKE if FTS5
    table is not available (pre-migration databases).

    Args:
        search_string: Search string for type name or description

    Returns:
        TypeQueryResult with matching type records

    Example:
        >>> result = search_types("optional")
        >>> result.success
        True
        >>> [t.name for t in result.types]
        ['Maybe_id_7']
    """
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        try:
            # FTS5 path: relevance-ranked results
            cursor = conn.execute(
                """SELECT t.*, fi.name AS file_name, fi.path AS file_path
                FROM types t
                JOIN types_fts ON t.id = types_fts.rowid
                LEFT JOIN files fi ON t.file_id = fi.id
                WHERE types_fts MATCH ?
                ORDER BY types_fts.rank""",
                (search_string,)
            )
        except sqlite3.OperationalError:
            # Fallback: LIKE search
            like_pattern = f"%{search_string}%"
            cursor = conn.execute(
                """SELECT t.*, fi.name AS file_name, fi.path AS file_path
                FROM types t
                LEFT JOIN files fi ON t.file_id = fi.id
                WHERE t.name LIKE ? OR t.description LIKE ?
                ORDER BY t.name""",
                (like_pattern, like_pattern)
            )

        rows = cursor.fetchall()
        type_records = tuple(
            row_to_type_record(row, include_details=include_details, details_only=details_only)
            for row in rows
        )

        return TypeQueryResult(
            success=True,
            types=type_records
        )

    except Exception as e:
        return TypeQueryResult(
            success=False,
            error=f"Search failed: {str(e)}"
        )

    finally:
        conn.close()


def get_type_by_name(
    type_name: str,
    include_details: bool = True,
    details_only: bool = False
) -> TypeQueryResult:
    """
    Look up types by name.

    Returns all types matching the given name, with file metadata (name, path)
    included via JOIN. Multiple types may share a name across different files.

    Args:
        type_name: Type name to search for

    Returns:
        TypeQueryResult with tuple of matching TypeRecords

    Example:
        >>> result = get_type_by_name("Maybe_id_7")
        >>> result.success
        True
        >>> len(result.types)
        1
        >>> result.types[0].file_path
        'src/types.py'
    """
    # Effect: open connection
    project_root = get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Effect: query types by name
        rows = _get_type_by_name_effect(conn, type_name)

        if not rows:
            return TypeQueryResult(
                success=False,
                error=f"No types found with name: {type_name}"
            )

        # Pure: convert rows to records
        type_records = tuple(
            row_to_type_record(row, include_details=include_details, details_only=details_only)
            for row in rows
        )

        return TypeQueryResult(
            success=True,
            types=type_records
        )

    except Exception as e:
        return TypeQueryResult(
            success=False,
            error=f"Database query failed: {str(e)}"
        )

    finally:
        conn.close()
