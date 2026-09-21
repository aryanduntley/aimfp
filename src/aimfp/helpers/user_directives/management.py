"""
AIMFP Helper Functions - User Directives Management

Specialized operations for user_directives database.
These are typed, domain-specific operations for common directive workflows.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- init_user_directives_db: Initialize user_directives.db for Case 2 projects
- get_user_directive_by_name: Get single directive by unique name
- activate_user_directive: Set directive status to 'active' (DB only)
- deactivate_user_directive: Set directive status to 'paused' (DB only)
- add_user_directive_note: Add note to notes table
- get_user_directive_notes: Get notes with optional filters
- search_user_directive_notes: Search note content with optional filters
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from ..utils import get_return_statements, rows_to_tuple, row_to_dict

# Import common user_directives utilities (DRY principle)
from ._common import (
    _open_connection,
    get_cached_project_root,
    get_user_directives_db_path,
    _open_directives_connection,
    _directive_exists_by_id,
    _directive_is_approved,
    _validate_note_type,
    _validate_severity,
    VALID_NOTE_TYPES,
    VALID_SEVERITY_LEVELS
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class DirectiveResult:
    """Result of single directive lookup."""
    success: bool
    directive: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MutationResult:
    """Result of add/update operations."""
    success: bool
    id: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Note:
    """Immutable note record."""
    id: int
    content: str
    note_type: str
    severity: str
    reference_type: Optional[str] = None
    reference_name: Optional[str] = None
    reference_id: Optional[int] = None
    metadata_json: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True)
class NotesResult:
    """Result of get/search notes operations."""
    success: bool
    notes: Tuple[Note, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Public Helper Functions - Database Initialization
# ============================================================================

def _get_schema_path(schema_file: str) -> str:
    """Pure: Get path to a database schema file."""
    helpers_dir = Path(__file__).parent.parent  # src/aimfp/helpers/
    return str(helpers_dir.parent / "database" / "schemas" / schema_file)


def init_user_directives_db() -> MutationResult:
    """
    Initialize user_directives.db for Case 2 (custom directive automation) projects.

    Creates the user_directives database from schema. Call this when a project
    transitions to Case 2 (user_directives_status set to pending_discovery).
    Requires the project to be initialized first (.aimfp-project/ must exist).

    Returns:
        MutationResult with success status and table names created
    """
    project_root = get_cached_project_root()
    db_path = get_user_directives_db_path(project_root)

    # Check if already exists
    if Path(db_path).is_file():
        return MutationResult(
            success=False,
            error=f"user_directives.db already exists at {db_path}"
        )

    # Verify .aimfp-project/ directory exists
    aimfp_dir = Path(db_path).parent
    if not aimfp_dir.is_dir():
        return MutationResult(
            success=False,
            error=f"Project not initialized: {aimfp_dir} does not exist. Run aimfp_init first."
        )

    # Load schema
    schema_path = _get_schema_path("user_directives.sql")
    if not Path(schema_path).is_file():
        return MutationResult(
            success=False,
            error=f"Schema file not found: {schema_path}"
        )

    try:
        with open(schema_path, 'r') as f:
            schema_sql = f.read()

        conn = _open_connection(db_path)
        try:
            conn.executescript(schema_sql)
            conn.commit()

            # Get created table names
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            table_names = tuple(row['name'] for row in cursor.fetchall())
        finally:
            conn.close()

        return_stmts = get_return_statements("init_user_directives_db")

        return MutationResult(
            success=True,
            message=f"user_directives.db created with {len(table_names)} tables",
            return_statements=return_stmts
        )

    except Exception as e:
        # Clean up partial DB on failure
        if Path(db_path).is_file():
            Path(db_path).unlink()
        return MutationResult(
            success=False,
            error=f"Database initialization failed: {str(e)}"
        )


# ============================================================================
# Public Helper Functions - Directive Lookup
# ============================================================================

def get_user_directive_by_name(
    name: str
) -> DirectiveResult:
    """
    Get a single user directive by its unique name.

    Args:
        name: Directive name (exact match)

    Returns:
        DirectiveResult with directive record or None if not found

    Example:
        >>> result = get_user_directive_by_name(
        ...     "lights_off_at_midnight"
        ... )
        >>> result.directive['status']
        'active'
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        cursor = conn.execute(
            "SELECT * FROM user_directives WHERE name = ?",
            (name,)
        )
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return DirectiveResult(
                success=True,
                directive=None
            )

        return_stmts = get_return_statements("get_user_directive_by_name")

        return DirectiveResult(
            success=True,
            directive=row_to_dict(row),
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return DirectiveResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# Public Helper Functions - Status Transitions
# ============================================================================

def activate_user_directive(
    directive_id: int
) -> MutationResult:
    """
    Set directive status to 'active' and record activated_at timestamp.
    Does NOT deploy services - AI handles deployment via directive workflow.

    Args:
        directive_id: ID of the directive to activate

    Returns:
        MutationResult with success/error

    Note:
        Requires directive to be approved (approved=true).
        Only updates DB status. Actual deployment is AI's responsibility
        via the user_directive_activate directive workflow.
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Check if directive exists
        if not _directive_exists_by_id(conn, directive_id):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Directive with id {directive_id} not found"
            )

        # Check if directive is approved
        if not _directive_is_approved(conn, directive_id):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Directive {directive_id} is not approved. Run user_directive_approve first."
            )

        # Update status to active
        conn.execute(
            """
            UPDATE user_directives
            SET status = 'active', activated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (directive_id,)
        )
        conn.commit()
        conn.close()

        return_stmts = get_return_statements("activate_user_directive")

        return MutationResult(
            success=True,
            id=directive_id,
            message="Directive activated",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def deactivate_user_directive(
    directive_id: int
) -> MutationResult:
    """
    Set directive status to 'paused' and clear deployment info.
    Does NOT stop services - AI handles service shutdown via directive workflow.

    Args:
        directive_id: ID of the directive to deactivate

    Returns:
        MutationResult with success/error

    Note:
        Only updates DB status. Actual service shutdown is AI's responsibility
        via the user_directive_deactivate directive workflow.
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Check if directive exists
        if not _directive_exists_by_id(conn, directive_id):
            conn.close()
            return MutationResult(
                success=False,
                error=f"Directive with id {directive_id} not found"
            )

        # Update status to paused
        conn.execute(
            "UPDATE user_directives SET status = 'paused' WHERE id = ?",
            (directive_id,)
        )

        # Clear deployment info in directive_implementations
        conn.execute(
            """
            UPDATE directive_implementations
            SET deployed = 0, process_id = NULL
            WHERE directive_id = ?
            """,
            (directive_id,)
        )

        conn.commit()
        conn.close()

        return_stmts = get_return_statements("deactivate_user_directive")

        return MutationResult(
            success=True,
            id=directive_id,
            message="Directive paused",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


# ============================================================================
# Public Helper Functions - Notes
# ============================================================================

def add_user_directive_note(
    content: str,
    note_type: str,
    reference_type: Optional[str] = None,
    reference_name: Optional[str] = None,
    reference_id: Optional[int] = None,
    severity: str = 'info',
    metadata_json: Optional[str] = None
) -> MutationResult:
    """
    Add note to user_directives.db notes table for AI record-keeping.

    Args:
        content: Note content/message
        note_type: 'implementation', 'validation', 'execution', 'dependency',
                   'error', 'optimization', 'user_feedback', 'lifecycle',
                   'testing', 'general'
        reference_type: Type of reference ('directive', 'helper', 'dependency', 'file')
        reference_name: Name of referenced entity
        reference_id: Optional ID linking to user_directives, helper_functions, etc.
        severity: 'info', 'warning', 'error' (default 'info')
        metadata_json: Additional context as JSON string

    Returns:
        MutationResult with new note ID
    """
    # Validate note_type
    if not _validate_note_type(note_type):
        return MutationResult(
            success=False,
            error=f"Invalid note_type: {note_type}. Must be one of: {', '.join(sorted(VALID_NOTE_TYPES))}"
        )

    # Validate severity
    if not _validate_severity(severity):
        return MutationResult(
            success=False,
            error=f"Invalid severity: {severity}. Must be one of: {', '.join(sorted(VALID_SEVERITY_LEVELS))}"
        )

    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        cursor = conn.execute(
            """
            INSERT INTO notes (
                content, note_type, severity, reference_type,
                reference_name, reference_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (content, note_type, severity, reference_type,
             reference_name, reference_id, metadata_json)
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()

        return_stmts = get_return_statements("add_user_directive_note")

        return MutationResult(
            success=True,
            id=new_id,
            message="Note added",
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return MutationResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def get_user_directive_notes(
    note_type: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_name: Optional[str] = None,
    reference_id: Optional[int] = None,
    severity: Optional[str] = None,
    limit: int = 100,
    exclude_note_types: Optional[list] = None
) -> NotesResult:
    """
    Get notes from user_directives.db with optional filters.
    All filters are AND logic. No filters returns all notes.

    Args:
        note_type: Filter by note_type
        reference_type: Filter by reference_type
        reference_name: Filter by reference_name
        reference_id: Filter by reference_id
        severity: Filter by severity
        limit: Maximum number of notes to return (default 100)

    Returns:
        NotesResult with matching notes
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Build query with optional filters
        query = """
            SELECT id, content, note_type, severity, reference_type,
                   reference_name, reference_id, metadata_json, created_at
            FROM notes
        """

        where_parts = []
        values = []

        if note_type is not None:
            where_parts.append("note_type = ?")
            values.append(note_type)

        if reference_type is not None:
            where_parts.append("reference_type = ?")
            values.append(reference_type)

        if reference_name is not None:
            where_parts.append("reference_name = ?")
            values.append(reference_name)

        if reference_id is not None:
            where_parts.append("reference_id = ?")
            values.append(reference_id)

        if severity is not None:
            where_parts.append("severity = ?")
            values.append(severity)

        if exclude_note_types:
            placeholders = ", ".join(["?"] * len(exclude_note_types))
            where_parts.append(f"note_type NOT IN ({placeholders})")
            values.extend(exclude_note_types)

        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)

        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(limit)

        cursor = conn.execute(query, values)
        rows = cursor.fetchall()
        conn.close()

        notes = tuple(
            Note(
                id=row['id'],
                content=row['content'],
                note_type=row['note_type'],
                severity=row['severity'],
                reference_type=row['reference_type'],
                reference_name=row['reference_name'],
                reference_id=row['reference_id'],
                metadata_json=row['metadata_json'],
                created_at=row['created_at']
            )
            for row in rows
        )

        return_stmts = get_return_statements("get_user_directive_notes")

        return NotesResult(
            success=True,
            notes=notes,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return NotesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )


def search_user_directive_notes(
    search_string: str,
    note_type: Optional[str] = None,
    reference_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 100,
    exclude_note_types: Optional[list] = None
) -> NotesResult:
    """
    Search note content in user_directives.db with optional additional filters.

    Args:
        search_string: Search string for note content (case-insensitive LIKE match)
        note_type: Optional filter by note_type
        reference_type: Optional filter by reference_type
        severity: Optional filter by severity
        limit: Maximum number of notes to return (default 100)

    Returns:
        NotesResult with matching notes
    """
    project_root = get_cached_project_root()
    conn = _open_directives_connection(project_root)

    try:
        # Build query with search and optional filters
        query = """
            SELECT id, content, note_type, severity, reference_type,
                   reference_name, reference_id, metadata_json, created_at
            FROM notes
            WHERE content LIKE ?
        """

        values = [f"%{search_string}%"]

        if note_type is not None:
            query += " AND note_type = ?"
            values.append(note_type)

        if reference_type is not None:
            query += " AND reference_type = ?"
            values.append(reference_type)

        if severity is not None:
            query += " AND severity = ?"
            values.append(severity)

        if exclude_note_types:
            placeholders = ", ".join(["?"] * len(exclude_note_types))
            query += f" AND note_type NOT IN ({placeholders})"
            values.extend(exclude_note_types)

        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(limit)

        cursor = conn.execute(query, values)
        rows = cursor.fetchall()
        conn.close()

        notes = tuple(
            Note(
                id=row['id'],
                content=row['content'],
                note_type=row['note_type'],
                severity=row['severity'],
                reference_type=row['reference_type'],
                reference_name=row['reference_name'],
                reference_id=row['reference_id'],
                metadata_json=row['metadata_json'],
                created_at=row['created_at']
            )
            for row in rows
        )

        return_stmts = get_return_statements("search_user_directive_notes")

        return NotesResult(
            success=True,
            notes=notes,
            return_statements=return_stmts
        )

    except Exception as e:
        conn.close()
        return NotesResult(
            success=False,
            error=f"Database error: {str(e)}"
        )
