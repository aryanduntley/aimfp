"""
AIMFP Helper Functions - Project Files (Part 1)

File reservation, finalization, and lookup operations for project database.
Implements reserve/finalize pattern for rename-proof ID-based file tracking.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- reserve_file: Reserve file ID before creation
- reserve_files: Reserve multiple file IDs (batch)
- finalize_file: Finalize reserved file after creation
- finalize_files: Finalize multiple files (batch)
- get_file_by_name: High-frequency name lookup
- get_file_by_path: Very high-frequency path lookup
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple, Union

from ..utils import get_return_statements

# Import common project utilities (DRY principle)
from .task_files import link_files_to_current_focus_effect
from .structure_links import (
    FILE_FLOWS,
    FLOWS_NOT_DECLARED,
    flow_choice_error,
    insert_links_effect,
    no_flow_reason_for,
    unique_ids,
    validate_link_targets,
)
from ._common import (
    _open_connection,
    get_cached_project_root,
    _open_project_connection,
    check_trackable_file_path,
)


# ============================================================================
# MCP Input Normalization (JSON sends dicts, functions expect tuples)
# ============================================================================

def _normalize_reserve_files_input(
    files: Union[List[tuple], List[Dict[str, Any]]]
) -> List[Tuple[str, str, str, bool, Tuple[int, ...], Optional[str]]]:
    """
    Pure: Normalize reserve_files input to (name, path, language, skip_id_naming,
    flow_ids, no_flow_reason) tuples. flow_ids keeps the caller's declaration as
    given (list, or None for "no flow"); an omitted declaration becomes
    FLOWS_NOT_DECLARED so flow_choice_error can refuse it.
    """
    def from_dict(f: Dict[str, Any]) -> tuple:
        return (f["name"], f["path"], f["language"], f.get("skip_id_naming", False),
                f.get("flow_ids", FLOWS_NOT_DECLARED), f.get("no_flow_reason"))

    def from_tuple(t: tuple) -> tuple:
        return (tuple(t) + (False, FLOWS_NOT_DECLARED, None)[max(0, len(t) - 3):])[:6]

    return [from_dict(f) if isinstance(f, dict) else from_tuple(f) for f in (files or [])]


def _normalize_finalize_files_input(
    files: Union[List[Tuple[int, str, str, str, bool]], List[Dict[str, Any]]]
) -> List[Tuple[int, str, str, str, bool]]:
    """Pure: Convert list of dicts to list of tuples for finalize_files."""
    if not files:
        return files
    if isinstance(files[0], dict):
        return [
            (
                f["file_id"],
                f["name"],
                f["path"],
                f.get("language", ""),
                f.get("skip_id_naming", False),
            )
            for f in files
        ]
    return files


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class FileRecord:
    """Immutable file record from database."""
    id: int
    name: str
    path: str
    language: str
    is_reserved: bool
    id_in_name: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReserveResult:
    """Result of file reservation operation."""
    success: bool
    id: Optional[int] = None
    is_reserved: Optional[bool] = None
    flow_ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class ReserveBatchResult:
    """Result of batch file reservation."""
    success: bool
    ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class FinalizeResult:
    """Result of file finalization operation."""
    success: bool
    file_id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class FinalizeBatchResult:
    """Result of batch file finalization."""
    success: bool
    finalized_ids: Tuple[int, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()  # AI guidance for next steps


@dataclass(frozen=True)
class FileQueryResult:
    """Result of file lookup operation (single file, e.g. by path)."""
    success: bool
    file: Optional[FileRecord] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FilesQueryResult:
    """Result of file lookup that may return multiple matches (e.g. by name)."""
    success: bool
    files: Tuple[FileRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helper Functions
# ============================================================================

def validate_file_id_in_name(name: str, file_id: int) -> bool:
    """
    Validate that file name contains _id_{file_id} pattern.

    Pure function - no side effects, deterministic.

    Args:
        name: File name to validate
        file_id: Expected file ID

    Returns:
        True if pattern found, False otherwise

    Example:
        >>> validate_file_id_in_name("calculator_id_42.py", 42)
        True
        >>> validate_file_id_in_name("calculator.py", 42)
        False
    """
    expected_pattern = f"_id_{file_id}"
    return expected_pattern in name


def row_to_file_record(row: sqlite3.Row) -> FileRecord:
    """
    Convert database row to immutable FileRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable FileRecord
    """
    return FileRecord(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        language=row["language"],
        is_reserved=bool(row["is_reserved"]),
        id_in_name=bool(row["id_in_name"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"]
    )


# ============================================================================
# Database Effect Functions
# ============================================================================

def _reserve_file_effect(
    conn: sqlite3.Connection,
    name: str,
    path: str,
    language: str,
    id_in_name: bool = True,
    flow_ids: Optional[List[int]] = None,
    no_flow_reason: Optional[str] = None
) -> int:
    """
    Effect: Insert reserved file and its file_flows links (no commit; caller commits).

    Args:
        conn: Database connection
        name: Preliminary file name
        path: File path relative to project root
        language: Programming language
        id_in_name: Whether filename will contain _id_XX pattern (default True)
        flow_ids: Flows the file implements, or None for no flow (validated by the caller)
        no_flow_reason: Explanation recorded when flow_ids is None

    Returns:
        Reserved file ID
    """
    cursor = conn.execute(
        """
        INSERT INTO files (name, path, language, is_reserved, id_in_name, no_flow_reason)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (name, path, language, 1 if id_in_name else 0, no_flow_reason_for(flow_ids, no_flow_reason))
    )
    insert_links_effect(conn, FILE_FLOWS, cursor.lastrowid, unique_ids(flow_ids))
    return cursor.lastrowid


def _reserve_files_batch_effect(
    conn: sqlite3.Connection,
    files: List[Tuple[str, str, str, bool, Optional[List[int]], Optional[str]]]
) -> Tuple[int, ...]:
    """
    Effect: Insert multiple reserved files and their file_flows links in one transaction.

    Args:
        conn: Database connection
        files: List of (name, path, language, id_in_name, flow_ids, no_flow_reason) tuples

    Returns:
        Tuple of reserved file IDs in same order
    """
    try:
        ids = tuple(
            _reserve_file_effect(conn, name, path, language, id_in_name, flow_ids, reason)
            for name, path, language, id_in_name, flow_ids, reason in files
        )
        conn.commit()
        return ids

    except Exception as e:
        conn.rollback()
        raise e


def _finalize_file_effect(
    conn: sqlite3.Connection,
    file_id: int,
    name: str,
    path: str,
    language: str
) -> None:
    """
    Effect: Finalize reserved file in database.

    Args:
        conn: Database connection
        file_id: Reserved file ID
        name: Final file name with _id_xx suffix
        path: File path
        language: Programming language
    """
    conn.execute(
        """
        UPDATE files
        SET is_reserved = 0,
            name = ?,
            path = ?,
            language = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (name, path, language, file_id)
    )
    conn.commit()


def _finalize_files_batch_effect(
    conn: sqlite3.Connection,
    finalizations: List[Tuple[int, str, str, str]]
) -> None:
    """
    Effect: Finalize multiple files in transaction.

    Args:
        conn: Database connection
        finalizations: List of (file_id, name, path, language) tuples
    """
    cursor = conn.cursor()

    try:
        for file_id, name, path, language in finalizations:
            cursor.execute(
                """
                UPDATE files
                SET is_reserved = 0,
                    name = ?,
                    path = ?,
                    language = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name, path, language, file_id)
            )

        conn.commit()

    except Exception as e:
        conn.rollback()
        raise e


def _get_file_by_name_effect(
    conn: sqlite3.Connection,
    file_name: str
) -> List[sqlite3.Row]:
    """
    Effect: Query files by name (multiple files can share same name, e.g. __init__.py).

    Args:
        conn: Database connection
        file_name: File name to look up

    Returns:
        List of row objects (empty if none found)
    """
    cursor = conn.execute(
        "SELECT * FROM files WHERE name = ?",
        (file_name,)
    )
    return cursor.fetchall()


def _get_file_by_path_effect(
    conn: sqlite3.Connection,
    file_path: str
) -> Optional[sqlite3.Row]:
    """
    Effect: Query file by path.

    Args:
        conn: Database connection
        file_path: File path to look up

    Returns:
        Row object or None if not found
    """
    cursor = conn.execute(
        "SELECT * FROM files WHERE path = ? LIMIT 1",
        (file_path,)
    )
    return cursor.fetchone()


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def reserve_file(
    name: str,
    path: str,
    language: str,
    flow_ids: Optional[List[int]],
    skip_id_naming: bool = False,
    no_flow_reason: Optional[str] = None,
    project_root: Optional[str] = None
) -> ReserveResult:
    """
    Reserve file ID for naming before creation, linked to its flow(s).

    Creates placeholder entry in files table with is_reserved=1, plus its file_flows
    rows, in one transaction. A file must declare its flows: a list of flow IDs, or
    an explicit None for a file that belongs to no flow (config, data). None is
    recorded on the file as no_flow_reason so structure checks skip it.
    Returns ID that should be embedded in filename: {name}_id_{id}.{ext}

    Args:
        name: Preliminary file name (will have _id_xxx appended unless skip_id_naming=True)
        path: File path relative to project root
        language: Programming language (e.g., 'python', 'javascript')
        flow_ids: Flow(s) this file implements, or None to declare it belongs to no flow
        skip_id_naming: If True, skip ID embedding (for __init__.py, .db files, MCP tools)
        no_flow_reason: Why the file belongs to no flow (only with flow_ids=None)

    Returns:
        ReserveResult with success status, reserved ID and linked flow IDs

    Example:
        >>> result = reserve_file("calculator", "src/calc.py", "python", flow_ids=[3])
        >>> result.success
        True
        >>> result.id
        42
        # Use result.id to create: calculator_id_42.py (unless skip_id_naming=True)
    """
    choice_error = flow_choice_error(flow_ids, no_flow_reason)
    if choice_error:
        return ReserveResult(success=False, error=choice_error)
    flows = unique_ids(flow_ids)

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if path already exists
        existing = _get_file_by_path_effect(conn, path)
        if existing is not None:
            return ReserveResult(
                success=False,
                error=f"File path already exists: {path}"
            )

        flow_error = validate_link_targets(conn, FILE_FLOWS, flows)
        if flow_error:
            return ReserveResult(success=False, error=flow_error)

        # Effect: reserve file with id_in_name flag and its flow links
        reserved_id = _reserve_file_effect(
            conn, name, path, language, not skip_id_naming, flow_ids, no_flow_reason)
        conn.commit()
        link_files_to_current_focus_effect(conn, (reserved_id,))

        # Success - fetch return statements from core database
        return_statements = get_return_statements("reserve_file")

        return ReserveResult(
            success=True,
            id=reserved_id,
            is_reserved=True,
            flow_ids=flows,
            return_statements=return_statements
        )

    finally:
        conn.close()


def reserve_files(
    files: Union[List[Tuple[str, str, str, bool]], List[Dict[str, Any]]],
    project_root: Optional[str] = None
) -> ReserveBatchResult:
    """
    Reserve multiple file IDs at once.

    Creates placeholder entries for multiple files in a single transaction.
    All reservations succeed or all fail (atomic operation).

    Args:
        files: List of dicts {name, path, language, flow_ids, skip_id_naming?, no_flow_reason?}
               or (name, path, language, skip_id_naming, flow_ids, no_flow_reason) tuples.
               Every file declares its flows: a list, or None for "no flow".

    Returns:
        ReserveBatchResult with success status and reserved IDs
        IDs correspond to input indices: files[0] -> ids[0], files[1] -> ids[1]

    Example:
        >>> files = [
        ...     ("calculator", "src/calc.py", "python", False, [3]),
        ...     ("__init__", "src/__init__.py", "python", True, None, "package marker")
        ... ]
        >>> result = reserve_files(files)
        >>> result.success
        True
        >>> result.ids
        (42, 43)
    """
    # Normalize input (MCP sends list of dicts, function expects list of tuples)
    files = _normalize_reserve_files_input(files)

    # Validate input
    if not files:
        return ReserveBatchResult(
            success=False,
            error="Files list cannot be empty"
        )
    choice_errors = tuple(
        f"{f[1]}: {err}" for f in files for err in (flow_choice_error(f[4], f[5]),) if err)
    if choice_errors:
        return ReserveBatchResult(success=False, error="; ".join(choice_errors))

    # Effect: open connection
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        # Check if any paths already exist
        for name, path, language, skip_id_naming, _flows, _reason in files:
            existing = _get_file_by_path_effect(conn, path)
            if existing is not None:
                return ReserveBatchResult(
                    success=False,
                    error=f"File path already exists: {path}"
                )

        flow_error = validate_link_targets(
            conn, FILE_FLOWS, unique_ids(fid for f in files for fid in (f[4] or ())))
        if flow_error:
            return ReserveBatchResult(success=False, error=flow_error)

        # Convert skip_id_naming to id_in_name for effect function
        files_with_id_in_name = [
            (name, path, language, not skip_id_naming, flows, reason)
            for name, path, language, skip_id_naming, flows, reason in files
        ]

        # Effect: reserve all files in transaction
        reserved_ids = _reserve_files_batch_effect(conn, files_with_id_in_name)
        link_files_to_current_focus_effect(conn, reserved_ids)

        # Success - fetch return statements from core database
        return_statements = get_return_statements("reserve_files")

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


def finalize_file(
    file_id: int,
    name: str,
    path: str,
    language: str,
    skip_id_naming: bool = False,
    project_root: Optional[str] = None
) -> FinalizeResult:
    """
    Finalize reserved file after creation.

    Verifies file exists on filesystem, updates database.
    Sets is_reserved=0 to mark file as finalized.

    Args:
        file_id: Reserved file ID
        name: Final file name with _id_xx suffix (unless skip_id_naming=True)
        path: File path (must exist on filesystem)
        language: Programming language
        skip_id_naming: If True, skip ID pattern validation (for __init__.py, .db files, MCP tools)

    Returns:
        FinalizeResult with success status and file_id

    Example:
        >>> # After creating calculator_id_42.py on filesystem
        >>> result = finalize_file(
        ...     42,
        ...     "calculator_id_42.py",
        ...     "src/calculator_id_42.py",
        ...     "python"
        ... )
        >>> result.success
        True
    """
    # Validate name contains _id_{file_id} pattern (unless skipped)
    if not skip_id_naming and not validate_file_id_in_name(name, file_id):
        return FinalizeResult(
            success=False,
            error=f"File name must contain '_id_{file_id}' pattern"
        )

    # Verify path is a regular file inside the project (root-relative paths
    # resolve against the project root — cwd is not the root when embedded)
    path_error = check_trackable_file_path(path, project_root)
    if path_error:
        return FinalizeResult(success=False, error=path_error)

    # Effect: open connection and finalize
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        _finalize_file_effect(conn, file_id, name, path, language)
        link_files_to_current_focus_effect(conn, (file_id,))

        # Success - fetch return statements from core database
        return_statements = get_return_statements("finalize_file")

        return FinalizeResult(
            success=True,
            file_id=file_id,
            return_statements=return_statements
        )

    except Exception as e:
        return FinalizeResult(
            success=False,
            error=f"Database finalization failed: {str(e)}"
        )

    finally:
        conn.close()


def finalize_files(
    files: Union[List[Tuple[int, str, str, str, bool]], List[Dict[str, Any]]],
    project_root: Optional[str] = None
) -> FinalizeBatchResult:
    """
    Finalize multiple reserved files.

    Verifies all files exist, updates database in transaction.
    All finalizations succeed or all fail (atomic operation).

    Args:
        files: List of (file_id, name, path, language, skip_id_naming) tuples or dicts
               skip_id_naming: If True for item, skip ID pattern validation for that file

    Returns:
        FinalizeBatchResult with success status and finalized IDs

    Example:
        >>> files = [
        ...     (42, "calculator_id_42.py", "src/calc_id_42.py", "python", False),
        ...     (43, "__init__.py", "src/__init__.py", "python", True)  # skip validation
        ... ]
        >>> result = finalize_files(files)
        >>> result.success
        True
        >>> result.finalized_ids
        (42, 43)
    """
    # Normalize input (MCP sends list of dicts, function expects list of tuples)
    files = _normalize_finalize_files_input(files)

    # Validate input
    if not files:
        return FinalizeBatchResult(
            success=False,
            error="Files list cannot be empty"
        )

    # Validate all names and check file existence
    finalizations = []

    for file_id, name, path, language, skip_id_naming in files:
        # Validate name pattern (unless skipped for this item)
        if not skip_id_naming and not validate_file_id_in_name(name, file_id):
            return FinalizeBatchResult(
                success=False,
                error=f"File name '{name}' must contain '_id_{file_id}' pattern"
            )

        # Check path is a regular file inside the project
        path_error = check_trackable_file_path(path, project_root)
        if path_error:
            return FinalizeBatchResult(
                success=False,
                error=f"File {file_id}: {path_error}"
            )

        finalizations.append((file_id, name, path, language))

    # Effect: open connection and finalize batch
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        _finalize_files_batch_effect(conn, finalizations)
        link_files_to_current_focus_effect(conn, tuple(f[0] for f in finalizations))

        # Success - fetch return statements from core database
        return_statements = get_return_statements("finalize_files")

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
        conn.close()


def get_file_by_name(
    file_name: str,
    project_root: Optional[str] = None
) -> FilesQueryResult:
    """
    Get files by name (high-frequency lookup).

    Queries files table for exact name match. Returns all matches since
    multiple files can share the same name (e.g., __init__.py in different directories).

    Args:
        file_name: File name to look up (e.g., 'calculator_id_42.py' or '__init__.py')

    Returns:
        FilesQueryResult with tuple of file records (empty if none found)

    Example:
        >>> result = get_file_by_name("__init__.py")
        >>> result.success
        True
        >>> len(result.files)
        3
        >>> result.files[0].path
        'src/aimfp/__init__.py'
    """
    # Effect: open connection and query
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_file_by_name_effect(conn, file_name)

        # Pure: convert rows to immutable records
        file_records = tuple(row_to_file_record(row) for row in rows)

        return FilesQueryResult(
            success=True,
            files=file_records,
            return_statements=get_return_statements("get_file_by_name")
        )

    except Exception as e:
        return FilesQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()


def get_file_by_path(
    file_path: str,
    project_root: Optional[str] = None
) -> FileQueryResult:
    """
    Get file by path (very high-frequency lookup).

    Queries files table for exact path match.
    Returns full file record with metadata.

    Args:
        file_path: File path to look up (e.g., 'src/calculator.py')

    Returns:
        FileQueryResult with file record or None if not found

    Example:
        >>> result = get_file_by_path("src/calculator_id_42.py")
        >>> result.success
        True
        >>> result.file.id
        42
        >>> result.file.name
        'calculator_id_42.py'
    """
    # Effect: open connection and query
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        row = _get_file_by_path_effect(conn, file_path)

        if row is None:
            return FileQueryResult(
                success=True,
                file=None,
                return_statements=get_return_statements("get_file_by_path")
            )

        # Pure: convert row to immutable record
        file_record = row_to_file_record(row)

        return FileQueryResult(
            success=True,
            file=file_record,
            return_statements=get_return_statements("get_file_by_path")
        )

    except Exception as e:
        return FileQueryResult(
            success=False,
            error=f"Query failed: {str(e)}"
        )

    finally:
        conn.close()
