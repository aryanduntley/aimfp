"""
AIMFP Helper Functions - Single-Phase Catalog Registration

Registers code that **already exists on disk** into project.db.

Why this is not reserve/finalize: that protocol is two-phase because it serves
writing *new* code — reserve an ID, embed it in the filename, write the file, then
finalize. Code being adopted from an existing project has a fixed name that no ID
will ever be embedded into, and the file is already on disk before AIMFP ever sees
it. The reserve phase has nothing left to do, so these tools insert directly in the
finalized state (``is_reserved = 0``, ``id_in_name = 0``).

Everything downstream is unchanged: cataloged files and functions are ordinary rows,
and the existing batch tools (``add_files_to_module``, ``add_file_flows``,
``add_interactions``, ``add_types_functions``) compose on top exactly as they do for
hand-written code.

All three tools are **idempotent**. Re-cataloging a path or a function that is
already tracked updates it in place rather than creating a duplicate, so a scan can
be re-run after the source changes without corrupting the index. The counts returned
distinguish created from updated so the caller can see which happened.
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from ..utils import get_return_statements, get_cached_project_root, _open_project_connection
from ..shared.slugs import mint_slug


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class CatalogResult:
    """Result of a single-phase catalog registration."""
    success: bool
    ids: Tuple[int, ...] = ()
    created_count: int = 0
    updated_count: int = 0
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helpers
# ============================================================================

def normalize_records(records: Union[List[Dict[str, Any]], Tuple]) -> Tuple[Dict[str, Any], ...]:
    """
    Pure: Coerce incoming records to a tuple of dicts.

    MCP delivers arrays of objects, but direct Python callers may pass tuples of
    dicts. Anything that is not a dict is rejected by returning it unchanged for
    the caller's validation step to catch.

    Args:
        records: Sequence of record dicts

    Returns:
        Tuple of dicts in input order
    """
    return tuple(records) if records else ()


def serialize_json_field(value: Any) -> Optional[str]:
    """
    Pure: Serialize a parameters/returns/definition value to a JSON string.

    Values already stored as strings pass through untouched, so a caller echoing a
    previous scan result back in does not get double-encoded JSON.

    Args:
        value: List, dict, pre-encoded string, or None

    Returns:
        JSON string, or None when the value is absent
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def missing_required_field(record: Dict[str, Any], required: Tuple[str, ...]) -> Optional[str]:
    """
    Pure: Find the first required field a record is missing or leaves empty.

    Args:
        record: Record to check
        required: Field names that must be present and non-empty

    Returns:
        Name of the first missing field, or None when the record is complete
    """
    for field_name in required:
        if not record.get(field_name):
            return field_name
    return None


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_upsert_file(
    conn: sqlite3.Connection,
    name: str,
    path: str,
    language: Optional[str],
) -> Tuple[int, bool]:
    """
    Effect: Insert a finalized file row, or update the existing row for that path.

    Args:
        conn: Database connection
        name: File name without extension
        path: Project-relative path (the natural key)
        language: Detected language

    Returns:
        (file ID, True when newly created)
    """
    existing = conn.execute(
        "SELECT id FROM files WHERE path = ?", (path,)
    ).fetchone()

    if existing is not None:
        conn.execute(
            """
            UPDATE files
            SET name = ?, language = ?, is_reserved = 0, id_in_name = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (name, language, existing["id"]),
        )
        return (existing["id"], False)

    cursor = conn.execute(
        """
        INSERT INTO files (name, path, language, is_reserved, id_in_name)
        VALUES (?, ?, ?, 0, 0)
        """,
        (name, path, language),
    )
    return (cursor.lastrowid, True)


def _effect_upsert_function(
    conn: sqlite3.Connection,
    name: str,
    file_id: int,
    purpose: Optional[str],
    parameters_json: Optional[str],
    returns_json: Optional[str],
) -> Tuple[int, bool]:
    """
    Effect: Insert a finalized function row, or update the existing row for that
    (file_id, name) pair.

    Args:
        conn: Database connection
        name: Function name
        file_id: Owning file ID
        purpose: One-line purpose
        parameters_json: JSON-encoded parameter list
        returns_json: JSON-encoded return descriptor

    Returns:
        (function ID, True when newly created)
    """
    existing = conn.execute(
        "SELECT id FROM functions WHERE file_id = ? AND name = ?", (file_id, name)
    ).fetchone()

    if existing is not None:
        conn.execute(
            """
            UPDATE functions
            SET purpose = ?, parameters = ?, returns = ?, is_reserved = 0,
                id_in_name = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (purpose, parameters_json, returns_json, existing["id"]),
        )
        return (existing["id"], False)

    cursor = conn.execute(
        """
        INSERT INTO functions
            (entity_key, name, file_id, purpose, parameters, returns, is_reserved, id_in_name)
        VALUES (?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (mint_slug("fn", name), name, file_id, purpose, parameters_json, returns_json),
    )
    return (cursor.lastrowid, True)


def _effect_upsert_type(
    conn: sqlite3.Connection,
    name: str,
    file_id: Optional[int],
    definition_json: str,
    description: Optional[str],
) -> Tuple[int, bool]:
    """
    Effect: Insert a finalized type row, or update the existing row for that
    (file_id, name) pair.

    Args:
        conn: Database connection
        name: Type name
        file_id: Defining file ID
        definition_json: JSON-encoded type shape
        description: One-line description

    Returns:
        (type ID, True when newly created)
    """
    existing = conn.execute(
        "SELECT id FROM types WHERE file_id IS ? AND name = ?", (file_id, name)
    ).fetchone()

    if existing is not None:
        conn.execute(
            """
            UPDATE types
            SET definition_json = ?, description = ?, is_reserved = 0,
                id_in_name = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (definition_json, description, existing["id"]),
        )
        return (existing["id"], False)

    cursor = conn.execute(
        """
        INSERT INTO types
            (entity_key, name, file_id, definition_json, description, is_reserved, id_in_name)
        VALUES (?, ?, ?, ?, ?, 0, 0)
        """,
        (mint_slug("type", name), name, file_id, definition_json, description),
    )
    return (cursor.lastrowid, True)


# ============================================================================
# Public Tools
# ============================================================================

def catalog_files(
    files: List[Dict[str, Any]],
    project_root: Optional[str] = None,
) -> CatalogResult:
    """
    Register existing files in project.db in a single phase.

    For code already on disk. Rows are written finalized (is_reserved=0) with ID
    naming off, because the filenames are fixed and cannot carry an _id_ suffix.
    Idempotent on path: an already-tracked path is updated, not duplicated.

    Args:
        files: Array of {name, path, language} objects. 'path' is
            project-relative and is the identity key.
        project_root: Project root override (defaults to the cached root)

    Returns:
        CatalogResult with IDs in input order and created/updated counts

    Example:
        >>> catalog_files([{"name": "extract", "path": "src/x/extract.py",
        ...                 "language": "python"}])      # doctest: +SKIP
    """
    records = normalize_records(files)
    if not records:
        return CatalogResult(success=False, error="Files list cannot be empty")

    for record in records:
        missing = missing_required_field(record, ('path',))
        if missing:
            return CatalogResult(
                success=False,
                error=f"File record missing required field '{missing}': {record}",
            )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        ids: list = []
        created = 0

        for record in records:
            file_id, was_created = _effect_upsert_file(
                conn,
                record.get('name') or record['path'].rsplit('/', 1)[-1],
                record['path'],
                record.get('language'),
            )
            ids.append(file_id)
            created += 1 if was_created else 0

        conn.commit()

        return CatalogResult(
            success=True,
            ids=tuple(ids),
            created_count=created,
            updated_count=len(ids) - created,
            return_statements=get_return_statements("catalog_files"),
        )

    except Exception as exc:
        conn.rollback()
        return CatalogResult(success=False, error=f"Catalog files failed: {str(exc)}")

    finally:
        conn.close()


def catalog_functions(
    functions: List[Dict[str, Any]],
    project_root: Optional[str] = None,
) -> CatalogResult:
    """
    Register existing functions in project.db in a single phase.

    Idempotent on (file_id, name): an already-tracked function is updated in place,
    so a re-scan after source changes refreshes signatures instead of duplicating
    them.

    Args:
        functions: Array of {name, file_id, purpose, parameters, returns} objects.
            'parameters' is a list and 'returns' a dict; both are JSON-encoded here.
        project_root: Project root override (defaults to the cached root)

    Returns:
        CatalogResult with IDs in input order and created/updated counts
    """
    records = normalize_records(functions)
    if not records:
        return CatalogResult(success=False, error="Functions list cannot be empty")

    for record in records:
        missing = missing_required_field(record, ('name', 'file_id'))
        if missing:
            return CatalogResult(
                success=False,
                error=f"Function record missing required field '{missing}': {record}",
            )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        ids: list = []
        created = 0

        for record in records:
            function_id, was_created = _effect_upsert_function(
                conn,
                record['name'],
                int(record['file_id']),
                record.get('purpose'),
                serialize_json_field(record.get('parameters')),
                serialize_json_field(record.get('returns')),
            )
            ids.append(function_id)
            created += 1 if was_created else 0

        conn.commit()

        return CatalogResult(
            success=True,
            ids=tuple(ids),
            created_count=created,
            updated_count=len(ids) - created,
            return_statements=get_return_statements("catalog_functions"),
        )

    except Exception as exc:
        conn.rollback()
        return CatalogResult(success=False, error=f"Catalog functions failed: {str(exc)}")

    finally:
        conn.close()


def catalog_types(
    types: List[Dict[str, Any]],
    project_root: Optional[str] = None,
) -> CatalogResult:
    """
    Register existing type definitions in project.db in a single phase.

    Idempotent on (file_id, name), like catalog_functions.

    Args:
        types: Array of {name, file_id, definition, description} objects.
            'definition' is a dict describing the type shape and is JSON-encoded here.
        project_root: Project root override (defaults to the cached root)

    Returns:
        CatalogResult with IDs in input order and created/updated counts
    """
    records = normalize_records(types)
    if not records:
        return CatalogResult(success=False, error="Types list cannot be empty")

    for record in records:
        missing = missing_required_field(record, ('name',))
        if missing:
            return CatalogResult(
                success=False,
                error=f"Type record missing required field '{missing}': {record}",
            )
        if record.get('definition') is None and record.get('definition_json') is None:
            return CatalogResult(
                success=False,
                error=f"Type record needs 'definition' or 'definition_json': {record}",
            )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        ids: list = []
        created = 0

        for record in records:
            definition_json = serialize_json_field(
                record.get('definition_json') or record.get('definition')
            )
            file_id = record.get('file_id')
            type_id, was_created = _effect_upsert_type(
                conn,
                record['name'],
                int(file_id) if file_id is not None else None,
                definition_json,
                record.get('description'),
            )
            ids.append(type_id)
            created += 1 if was_created else 0

        conn.commit()

        return CatalogResult(
            success=True,
            ids=tuple(ids),
            created_count=created,
            updated_count=len(ids) - created,
            return_statements=get_return_statements("catalog_types"),
        )

    except Exception as exc:
        conn.rollback()
        return CatalogResult(success=False, error=f"Catalog types failed: {str(exc)}")

    finally:
        conn.close()
