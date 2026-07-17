"""
AIMFP Helper Functions - Project Modules

Module management operations (CRUD, queries, dependency analysis).
Modules are folder-based reusable code boundaries that abstract domain logic
and external dependencies away from orchestrator code.

File membership tracked explicitly via module_files junction table (DB is source of truth).
modules.path guides where files should go; module_files is the authoritative assignment.
Functions/types inherit module membership through their file_id.
Cross-module dependencies derived from existing interactions table.

All functions are pure FP - immutable data, explicit parameters, Result types.
Database operations isolated as effects with clear naming conventions.

Helpers in this file:
- add_module: Create a new module
- get_module_by_name: Get module by name
- get_module_by_path: Get module by path
- get_all_modules: Get all project modules
- update_module: Update module metadata
- delete_module: Delete module (cascade removes module_files entries)
- add_file_to_module: Assign a file to a module
- remove_file_from_module: Remove a file from a module
- add_files_to_module: Batch assign multiple files to modules
- remove_files_from_module: Batch remove multiple files from modules
- get_module_files: Get all files assigned to a module
- get_module_functions: Get all functions in module files
- get_module_types: Get all types in module files
- get_module_dependencies: Cross-module function interactions
- get_module_for_file: Reverse lookup — which module owns this file?
- get_unassigned_files: Find files not assigned to any module
- search_modules: Fuzzy search by name/purpose/description
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional, List, Tuple

from ..utils import get_return_statements
from ._common import (
    _open_project_connection,
    get_cached_project_root,
    _check_entity_exists,
    _check_file_exists,
    _create_deletion_note,
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class ModuleRecord:
    """Immutable module record from database."""
    id: int
    name: str
    path: str
    description: Optional[str]
    purpose: Optional[str]
    external_dependencies: Tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FileInModuleRecord:
    """Immutable file record for module queries."""
    id: int
    name: Optional[str]
    path: str
    language: Optional[str]


@dataclass(frozen=True)
class FunctionInModuleRecord:
    """Immutable function record for module queries."""
    id: int
    name: str
    file_id: int
    file_path: str
    purpose: Optional[str]


@dataclass(frozen=True)
class TypeInModuleRecord:
    """Immutable type record for module queries."""
    id: int
    name: str
    file_id: Optional[int]
    file_path: Optional[str]
    description: Optional[str]


@dataclass(frozen=True)
class ModuleDependencyRecord:
    """Cross-module dependency record."""
    source_module_id: int
    source_module_name: str
    target_module_id: int
    target_module_name: str
    interaction_count: int


@dataclass(frozen=True)
class ModuleQueryResult:
    """Result of module lookup operation."""
    success: bool
    module: Optional[ModuleRecord] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModulesQueryResult:
    """Result of modules list operation."""
    success: bool
    modules: Tuple[ModuleRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AddModuleResult:
    """Result of module creation operation."""
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UpdateModuleResult:
    """Result of module update operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeleteModuleResult:
    """Result of module deletion operation."""
    success: bool
    error: Optional[str] = None
    file_count: int = 0
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleFileResult:
    """Result of add/remove file-to-module operation."""
    success: bool
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleFilesResult:
    """Result of module files query."""
    success: bool
    files: Tuple[FileInModuleRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleFunctionsResult:
    """Result of module functions query."""
    success: bool
    functions: Tuple[FunctionInModuleRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleTypesResult:
    """Result of module types query."""
    success: bool
    types: Tuple[TypeInModuleRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleDependenciesResult:
    """Result of module dependencies query."""
    success: bool
    dependencies: Tuple[ModuleDependencyRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UnassignedFilesResult:
    """Result of unassigned files query."""
    success: bool
    files: Tuple[FileInModuleRecord, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleFilesBatchResult:
    """Result of batch add/remove file-to-module operations."""
    success: bool
    added_count: int = 0
    removed_count: int = 0
    skipped_count: int = 0
    empty_modules: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helper Functions
# ============================================================================

def row_to_module_record(row: sqlite3.Row) -> ModuleRecord:
    """
    Convert database row to immutable ModuleRecord.

    Pure function - deterministic mapping.

    Args:
        row: SQLite row object

    Returns:
        Immutable ModuleRecord
    """
    ext_deps_raw = row["external_dependencies"]
    ext_deps = tuple(json.loads(ext_deps_raw)) if ext_deps_raw else ()

    return ModuleRecord(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        description=row["description"],
        purpose=row["purpose"],
        external_dependencies=ext_deps,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def build_module_update_query(
    module_id: int,
    name: Optional[str],
    path: Optional[str],
    description: Optional[str],
    purpose: Optional[str],
    external_dependencies: Optional[List[str]],
) -> Tuple[str, Tuple]:
    """
    Build SQL UPDATE query for module with only non-NULL fields.

    Pure function - deterministic query building.

    Args:
        module_id: Module ID to update
        name: New name (None = don't update)
        path: New path (None = don't update)
        description: New description (None = don't update)
        purpose: New purpose (None = don't update)
        external_dependencies: New dependencies list (None = don't update)

    Returns:
        Tuple of (sql_query, parameters)
    """
    updates = []
    params_list: list = []

    if name is not None:
        updates.append("name = ?")
        params_list.append(name)

    if path is not None:
        updates.append("path = ?")
        params_list.append(path)

    if description is not None:
        updates.append("description = ?")
        params_list.append(description)

    if purpose is not None:
        updates.append("purpose = ?")
        params_list.append(purpose)

    if external_dependencies is not None:
        updates.append("external_dependencies = ?")
        params_list.append(json.dumps(external_dependencies))

    updates.append("updated_at = CURRENT_TIMESTAMP")

    sql = f"UPDATE modules SET {', '.join(updates)} WHERE id = ?"
    params_list.append(module_id)

    return (sql, tuple(params_list))


# ============================================================================
# Database Effect Functions
# ============================================================================

def _add_module_effect(
    conn: sqlite3.Connection,
    name: str,
    path: str,
    description: Optional[str],
    purpose: Optional[str],
    external_dependencies: Optional[List[str]],
) -> int:
    """Effect: Insert module into database."""
    ext_deps_json = json.dumps(external_dependencies) if external_dependencies else None
    cursor = conn.execute(
        "INSERT INTO modules (name, path, description, purpose, external_dependencies) VALUES (?, ?, ?, ?, ?)",
        (name, path, description, purpose, ext_deps_json),
    )
    conn.commit()
    return cursor.lastrowid


def _get_module_by_name_effect(conn: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
    """Effect: Query module by name."""
    cursor = conn.execute("SELECT * FROM modules WHERE name = ? LIMIT 1", (name,))
    return cursor.fetchone()


def _get_module_by_path_effect(conn: sqlite3.Connection, path: str) -> Optional[sqlite3.Row]:
    """Effect: Query module by path."""
    cursor = conn.execute("SELECT * FROM modules WHERE path = ? LIMIT 1", (path,))
    return cursor.fetchone()


def _get_all_modules_effect(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Effect: Query all modules."""
    cursor = conn.execute("SELECT * FROM modules ORDER BY name")
    return cursor.fetchall()


def _update_module_effect(conn: sqlite3.Connection, sql: str, params: Tuple) -> None:
    """Effect: Execute module update query."""
    conn.execute(sql, params)
    conn.commit()


def _delete_module_effect(conn: sqlite3.Connection, module_id: int) -> None:
    """Effect: Delete module (CASCADE removes module_files entries)."""
    conn.execute("DELETE FROM modules WHERE id = ?", (module_id,))
    conn.commit()


def _add_file_to_module_effect(conn: sqlite3.Connection, module_id: int, file_id: int) -> None:
    """Effect: Insert module_files junction entry."""
    conn.execute("INSERT INTO module_files (module_id, file_id) VALUES (?, ?)", (module_id, file_id))
    conn.commit()


def _remove_file_from_module_effect(conn: sqlite3.Connection, module_id: int, file_id: int) -> None:
    """Effect: Delete module_files junction entry."""
    conn.execute("DELETE FROM module_files WHERE module_id = ? AND file_id = ?", (module_id, file_id))
    conn.commit()


def _get_module_files_effect(conn: sqlite3.Connection, module_id: int) -> List[sqlite3.Row]:
    """Effect: Get files assigned to module via junction table."""
    cursor = conn.execute(
        """
        SELECT f.id, f.name, f.path, f.language
        FROM files f
        JOIN module_files mf ON f.id = mf.file_id
        WHERE mf.module_id = ?
        ORDER BY f.path
        """,
        (module_id,),
    )
    return cursor.fetchall()


def _get_module_functions_effect(conn: sqlite3.Connection, module_id: int) -> List[sqlite3.Row]:
    """Effect: Get functions in module files via junction table."""
    cursor = conn.execute(
        """
        SELECT fn.id, fn.name, fn.file_id, f.path as file_path, fn.purpose
        FROM functions fn
        JOIN files f ON fn.file_id = f.id
        JOIN module_files mf ON f.id = mf.file_id
        WHERE mf.module_id = ?
        ORDER BY f.path, fn.name
        """,
        (module_id,),
    )
    return cursor.fetchall()


def _get_module_types_effect(conn: sqlite3.Connection, module_id: int) -> List[sqlite3.Row]:
    """Effect: Get types in module files via junction table."""
    cursor = conn.execute(
        """
        SELECT t.id, t.name, t.file_id, f.path as file_path, t.description
        FROM types t
        JOIN files f ON t.file_id = f.id
        JOIN module_files mf ON f.id = mf.file_id
        WHERE mf.module_id = ?
        ORDER BY f.path, t.name
        """,
        (module_id,),
    )
    return cursor.fetchall()


def _get_module_dependencies_effect(conn: sqlite3.Connection, module_id: int) -> List[sqlite3.Row]:
    """Effect: Get cross-module dependencies via interactions + module_files."""
    cursor = conn.execute(
        """
        SELECT
            m_target.id as target_module_id,
            m_target.name as target_module_name,
            COUNT(*) as interaction_count
        FROM interactions i
        JOIN functions fn_src ON i.source_function_id = fn_src.id
        JOIN module_files mf_src ON fn_src.file_id = mf_src.file_id
        JOIN functions fn_tgt ON i.target_function_id = fn_tgt.id
        JOIN module_files mf_tgt ON fn_tgt.file_id = mf_tgt.file_id
        JOIN modules m_target ON mf_tgt.module_id = m_target.id
        WHERE mf_src.module_id = ?
          AND mf_tgt.module_id != ?
        GROUP BY m_target.id, m_target.name
        ORDER BY interaction_count DESC
        """,
        (module_id, module_id),
    )
    return cursor.fetchall()


def _get_module_for_file_effect(conn: sqlite3.Connection, file_id: int) -> Optional[sqlite3.Row]:
    """Effect: Find module that owns a file via junction table."""
    cursor = conn.execute(
        """
        SELECT m.* FROM modules m
        JOIN module_files mf ON m.id = mf.module_id
        WHERE mf.file_id = ?
        LIMIT 1
        """,
        (file_id,),
    )
    return cursor.fetchone()


def _get_unassigned_files_effect(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Effect: Get files not assigned to any module."""
    cursor = conn.execute(
        """
        SELECT f.id, f.name, f.path, f.language
        FROM files f
        LEFT JOIN module_files mf ON f.id = mf.file_id
        WHERE mf.file_id IS NULL
        ORDER BY f.path
        """,
    )
    return cursor.fetchall()


def _search_modules_effect(conn: sqlite3.Connection, search_string: str) -> List[sqlite3.Row]:
    """Effect: Search modules using FTS5, with LIKE fallback."""
    try:
        cursor = conn.execute(
            """
            SELECT m.* FROM modules m
            JOIN modules_fts fts ON m.id = fts.rowid
            WHERE modules_fts MATCH ?
            ORDER BY rank
            """,
            (search_string,),
        )
        return cursor.fetchall()
    except sqlite3.OperationalError:
        pattern = f"%{search_string}%"
        cursor = conn.execute(
            "SELECT * FROM modules WHERE name LIKE ? OR purpose LIKE ? OR description LIKE ? ORDER BY name",
            (pattern, pattern, pattern),
        )
        return cursor.fetchall()


def _count_module_files_effect(conn: sqlite3.Connection, module_id: int) -> int:
    """Effect: Count files assigned to module."""
    cursor = conn.execute(
        "SELECT COUNT(*) as cnt FROM module_files WHERE module_id = ?",
        (module_id,),
    )
    return cursor.fetchone()["cnt"]


def _check_module_file_exists(conn: sqlite3.Connection, module_id: int, file_id: int) -> bool:
    """Effect: Check if a module_files junction entry already exists."""
    cursor = conn.execute(
        "SELECT 1 FROM module_files WHERE module_id = ? AND file_id = ?",
        (module_id, file_id),
    )
    return cursor.fetchone() is not None


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def add_module(
    name: str,
    path: str,
    description: Optional[str] = None,
    purpose: Optional[str] = None,
    external_dependencies: Optional[List[str]] = None,
    project_root: Optional[str] = None
) -> AddModuleResult:
    """
    Create a new module (reusable code boundary).

    A module maps to a directory. Files are assigned to modules explicitly
    via add_file_to_module. Use external_dependencies to record which
    libraries/services this module wraps.

    Args:
        name: Module name (e.g., 'wallet_connection', 'validation')
        path: Directory path relative to source root (e.g., 'src/wallet_connection/')
        description: What this module does
        purpose: Domain concern this module owns (one sentence)
        external_dependencies: Libraries/services this module wraps (e.g., ['walletconnect', 'stripe'])

    Returns:
        AddModuleResult with created module ID
    """
    if not name or not name.strip():
        return AddModuleResult(success=False, error="Module name is required")

    if not path or not path.strip():
        return AddModuleResult(success=False, error="Module path is required")

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        module_id = _add_module_effect(conn, name.strip(), path.strip(), description, purpose, external_dependencies)

        return AddModuleResult(
            success=True,
            id=module_id,
            return_statements=get_return_statements("add_module"),
        )

    except sqlite3.IntegrityError as e:
        error_msg = str(e)
        if "UNIQUE" in error_msg and "name" in error_msg:
            return AddModuleResult(success=False, error=f"Module with name '{name}' already exists")
        if "UNIQUE" in error_msg and "path" in error_msg:
            return AddModuleResult(success=False, error=f"Module with path '{path}' already exists")
        return AddModuleResult(success=False, error=f"Integrity error: {error_msg}")

    except Exception as e:
        return AddModuleResult(success=False, error=f"Failed to create module: {str(e)}")

    finally:
        conn.close()


def get_module_by_name(name: str, project_root: Optional[str] = None) -> ModuleQueryResult:
    """
    Get module by name.

    Args:
        name: Module name to look up

    Returns:
        ModuleQueryResult with module record or None if not found
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        row = _get_module_by_name_effect(conn, name)
        if row is None:
            return ModuleQueryResult(success=True, module=None)
        return ModuleQueryResult(success=True, module=row_to_module_record(row))

    except Exception as e:
        return ModuleQueryResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def get_module_by_path(path: str, project_root: Optional[str] = None) -> ModuleQueryResult:
    """
    Get module by directory path.

    Args:
        path: Directory path to look up

    Returns:
        ModuleQueryResult with module record or None if not found
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        row = _get_module_by_path_effect(conn, path)
        if row is None:
            return ModuleQueryResult(success=True, module=None)
        return ModuleQueryResult(success=True, module=row_to_module_record(row))

    except Exception as e:
        return ModuleQueryResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def get_all_modules(project_root: Optional[str] = None) -> ModulesQueryResult:
    """
    Get all modules in the project.

    Returns:
        ModulesQueryResult with tuple of all module records
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_all_modules_effect(conn)
        modules = tuple(row_to_module_record(row) for row in rows)

        return ModulesQueryResult(
            success=True,
            modules=modules,
            return_statements=get_return_statements("get_all_modules"),
        )

    except Exception as e:
        return ModulesQueryResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def update_module(
    module_id: int,
    name: Optional[str] = None,
    path: Optional[str] = None,
    description: Optional[str] = None,
    purpose: Optional[str] = None,
    external_dependencies: Optional[List[str]] = None,
    project_root: Optional[str] = None
) -> UpdateModuleResult:
    """
    Update module metadata. Only non-None fields are updated.

    Args:
        module_id: Module ID to update
        name: New name (None = don't update)
        path: New path (None = don't update)
        description: New description (None = don't update)
        purpose: New purpose (None = don't update)
        external_dependencies: New dependencies list (None = don't update)

    Returns:
        UpdateModuleResult
    """
    if all(v is None for v in (name, path, description, purpose, external_dependencies)):
        return UpdateModuleResult(success=False, error="No fields to update")

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return UpdateModuleResult(success=False, error=f"Module {module_id} not found")

        sql, params = build_module_update_query(
            module_id, name, path, description, purpose, external_dependencies,
        )
        _update_module_effect(conn, sql, params)

        return UpdateModuleResult(success=True)

    except sqlite3.IntegrityError as e:
        return UpdateModuleResult(success=False, error=f"Integrity error: {str(e)}")

    except Exception as e:
        return UpdateModuleResult(success=False, error=f"Update failed: {str(e)}")

    finally:
        conn.close()


def delete_module(module_id: int, project_root: Optional[str] = None) -> DeleteModuleResult:
    """
    Delete a module. CASCADE removes module_files junction entries.
    The files themselves are NOT deleted.

    Args:
        module_id: Module ID to delete

    Returns:
        DeleteModuleResult with file_count showing files that were in the module
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return DeleteModuleResult(success=False, error=f"Module {module_id} not found")

        row = conn.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
        module_name = row["name"]
        module_path = row["path"]

        file_count = _count_module_files_effect(conn, module_id)
        _delete_module_effect(conn, module_id)

        _create_deletion_note(
            conn, "modules", module_id,
            f"Module '{module_name}' (path: {module_path}) deleted. {file_count} files were assigned.",
            "info", "ai", "entry_deletion",
        )

        return DeleteModuleResult(success=True, file_count=file_count)

    except Exception as e:
        return DeleteModuleResult(success=False, error=f"Delete failed: {str(e)}")

    finally:
        conn.close()


def add_file_to_module(
    file_id: int,
    module_id: int,
    project_root: Optional[str] = None
) -> ModuleFileResult:
    """
    Assign a file to a module.

    Creates a module_files junction entry. Same pattern as add_file_to_flow.

    Args:
        file_id: File ID to assign
        module_id: Module ID to assign to

    Returns:
        ModuleFileResult
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return ModuleFileResult(success=False, error=f"Module {module_id} not found")

        if not _check_file_exists(conn, file_id):
            return ModuleFileResult(success=False, error=f"File {file_id} not found")

        _add_file_to_module_effect(conn, module_id, file_id)

        return ModuleFileResult(
            success=True,
            return_statements=get_return_statements("add_file_to_module"),
        )

    except sqlite3.IntegrityError:
        return ModuleFileResult(success=False, error=f"File {file_id} is already assigned to module {module_id}")

    except Exception as e:
        return ModuleFileResult(success=False, error=f"Failed to assign file: {str(e)}")

    finally:
        conn.close()


def remove_file_from_module(
    file_id: int,
    module_id: int,
    project_root: Optional[str] = None
) -> ModuleFileResult:
    """
    Remove a file from a module.

    Deletes the module_files junction entry. The file itself is not deleted.
    Warns if this leaves the module empty.

    Args:
        file_id: File ID to remove
        module_id: Module ID to remove from

    Returns:
        ModuleFileResult
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return ModuleFileResult(success=False, error=f"Module {module_id} not found")

        _remove_file_from_module_effect(conn, module_id, file_id)

        # Check if module is now empty
        remaining = _count_module_files_effect(conn, module_id)
        stmts: Tuple[str, ...] = ()
        if remaining == 0:
            module_row = conn.execute("SELECT name FROM modules WHERE id = ?", (module_id,)).fetchone()
            stmts = (
                f"WARNING: Module '{module_row['name']}' (id={module_id}) now has 0 files. Consider deleting the empty module via delete_module or reassigning files.",
            )

        return ModuleFileResult(success=True, return_statements=stmts)

    except Exception as e:
        return ModuleFileResult(success=False, error=f"Failed to remove file: {str(e)}")

    finally:
        conn.close()


def get_module_files(module_id: int, project_root: Optional[str] = None) -> ModuleFilesResult:
    """
    Get all files assigned to a module via module_files junction.

    Args:
        module_id: Module ID

    Returns:
        ModuleFilesResult with tuple of file records
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return ModuleFilesResult(success=False, error=f"Module {module_id} not found")

        rows = _get_module_files_effect(conn, module_id)
        files = tuple(
            FileInModuleRecord(id=r["id"], name=r["name"], path=r["path"], language=r["language"])
            for r in rows
        )

        return ModuleFilesResult(success=True, files=files)

    except Exception as e:
        return ModuleFilesResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def get_module_functions(module_id: int, project_root: Optional[str] = None) -> ModuleFunctionsResult:
    """
    Get all functions in module files (via module_files junction).

    Args:
        module_id: Module ID

    Returns:
        ModuleFunctionsResult with tuple of function records
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return ModuleFunctionsResult(success=False, error=f"Module {module_id} not found")

        rows = _get_module_functions_effect(conn, module_id)
        functions = tuple(
            FunctionInModuleRecord(
                id=r["id"], name=r["name"], file_id=r["file_id"],
                file_path=r["file_path"], purpose=r["purpose"],
            )
            for r in rows
        )

        return ModuleFunctionsResult(success=True, functions=functions)

    except Exception as e:
        return ModuleFunctionsResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def get_module_types(module_id: int, project_root: Optional[str] = None) -> ModuleTypesResult:
    """
    Get all types in module files (via module_files junction).

    Args:
        module_id: Module ID

    Returns:
        ModuleTypesResult with tuple of type records
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return ModuleTypesResult(success=False, error=f"Module {module_id} not found")

        rows = _get_module_types_effect(conn, module_id)
        types = tuple(
            TypeInModuleRecord(
                id=r["id"], name=r["name"], file_id=r["file_id"],
                file_path=r["file_path"], description=r["description"],
            )
            for r in rows
        )

        return ModuleTypesResult(success=True, types=types)

    except Exception as e:
        return ModuleTypesResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def get_module_dependencies(module_id: int, project_root: Optional[str] = None) -> ModuleDependenciesResult:
    """
    Get cross-module dependencies for a module.

    Finds all interactions where a function in this module calls
    a function in a different module. Returns aggregated counts
    per target module.

    Args:
        module_id: Module ID

    Returns:
        ModuleDependenciesResult with dependency records
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_entity_exists(conn, "modules", module_id):
            return ModuleDependenciesResult(success=False, error=f"Module {module_id} not found")

        module_row = conn.execute("SELECT name FROM modules WHERE id = ?", (module_id,)).fetchone()
        rows = _get_module_dependencies_effect(conn, module_id)

        dependencies = tuple(
            ModuleDependencyRecord(
                source_module_id=module_id,
                source_module_name=module_row["name"],
                target_module_id=r["target_module_id"],
                target_module_name=r["target_module_name"],
                interaction_count=r["interaction_count"],
            )
            for r in rows
        )

        return ModuleDependenciesResult(success=True, dependencies=dependencies)

    except Exception as e:
        return ModuleDependenciesResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def get_module_for_file(file_id: int, project_root: Optional[str] = None) -> ModuleQueryResult:
    """
    Find which module a file belongs to (reverse lookup via junction table).

    Args:
        file_id: File ID to look up

    Returns:
        ModuleQueryResult with module record or None if file is not in any module
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        if not _check_file_exists(conn, file_id):
            return ModuleQueryResult(success=False, error=f"File {file_id} not found")

        row = _get_module_for_file_effect(conn, file_id)
        if row is None:
            return ModuleQueryResult(
                success=True,
                module=None,
                return_statements=get_return_statements("get_module_for_file"),
            )

        return ModuleQueryResult(success=True, module=row_to_module_record(row))

    except Exception as e:
        return ModuleQueryResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def get_unassigned_files(project_root: Optional[str] = None) -> UnassignedFilesResult:
    """
    Find files not assigned to any module.

    Useful for identifying tracking gaps — files that should
    belong to a module but haven't been assigned yet.

    Returns:
        UnassignedFilesResult with tuple of unassigned file records
    """
    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _get_unassigned_files_effect(conn)
        files = tuple(
            FileInModuleRecord(id=r["id"], name=r["name"], path=r["path"], language=r["language"])
            for r in rows
        )

        return UnassignedFilesResult(
            success=True,
            files=files,
            return_statements=get_return_statements("get_unassigned_files"),
        )

    except Exception as e:
        return UnassignedFilesResult(success=False, error=f"Query failed: {str(e)}")

    finally:
        conn.close()


def search_modules(search_string: str, project_root: Optional[str] = None) -> ModulesQueryResult:
    """
    Search modules by name, purpose, or description.

    Uses FTS5 ranked search with LIKE fallback for pre-migration DBs.

    Args:
        search_string: Search term

    Returns:
        ModulesQueryResult with matching modules
    """
    if not search_string or not search_string.strip():
        return ModulesQueryResult(success=False, error="Search string is required")

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        rows = _search_modules_effect(conn, search_string.strip())
        modules = tuple(row_to_module_record(row) for row in rows)

        return ModulesQueryResult(
            success=True,
            modules=modules,
            return_statements=get_return_statements("search_modules"),
        )

    except Exception as e:
        return ModulesQueryResult(success=False, error=f"Search failed: {str(e)}")

    finally:
        conn.close()


def add_files_to_module(
    links: List[Tuple[int, int]],
    project_root: Optional[str] = None
) -> ModuleFilesBatchResult:
    """
    Assign multiple files to modules in batch (module_files junction).

    More efficient than multiple single add_file_to_module calls.
    Skips duplicates silently. Fails fast on missing entities.

    Args:
        links: List of (file_id, module_id) tuples to create

    Returns:
        ModuleFilesBatchResult with added/skipped counts
    """
    if not links:
        return ModuleFilesBatchResult(
            success=False,
            error="Links list cannot be empty",
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        added = 0
        skipped = 0

        for file_id, module_id in links:
            if not _check_file_exists(conn, file_id):
                return ModuleFilesBatchResult(
                    success=False,
                    error=f"File with ID {file_id} not found",
                )

            if not _check_entity_exists(conn, "modules", module_id):
                return ModuleFilesBatchResult(
                    success=False,
                    error=f"Module with ID {module_id} not found",
                )

            if _check_module_file_exists(conn, module_id, file_id):
                skipped += 1
                continue

            _add_file_to_module_effect(conn, module_id, file_id)
            added += 1

        return ModuleFilesBatchResult(
            success=True,
            added_count=added,
            skipped_count=skipped,
            return_statements=get_return_statements("add_files_to_module"),
        )

    except Exception as e:
        return ModuleFilesBatchResult(
            success=False,
            error=f"Batch assign failed: {str(e)}",
        )

    finally:
        conn.close()


def remove_files_from_module(
    links: List[Tuple[int, int]],
    project_root: Optional[str] = None
) -> ModuleFilesBatchResult:
    """
    Remove multiple files from modules in batch (module_files junction).

    More efficient than multiple single remove_file_from_module calls.
    Skips entries that don't exist. Reports modules left empty.

    Args:
        links: List of (file_id, module_id) tuples to remove

    Returns:
        ModuleFilesBatchResult with removed/skipped counts and empty_modules warnings
    """
    if not links:
        return ModuleFilesBatchResult(
            success=False,
            error="Links list cannot be empty",
        )

    project_root = project_root or get_cached_project_root()
    conn = _open_project_connection(project_root)

    try:
        removed = 0
        skipped = 0
        affected_module_ids: set = set()

        for file_id, module_id in links:
            if not _check_entity_exists(conn, "modules", module_id):
                return ModuleFilesBatchResult(
                    success=False,
                    error=f"Module with ID {module_id} not found",
                )

            if not _check_module_file_exists(conn, module_id, file_id):
                skipped += 1
                continue

            _remove_file_from_module_effect(conn, module_id, file_id)
            removed += 1
            affected_module_ids.add(module_id)

        # Check for emptied modules
        empty_modules: list = []
        for mid in affected_module_ids:
            remaining = _count_module_files_effect(conn, mid)
            if remaining == 0:
                module_row = conn.execute(
                    "SELECT name FROM modules WHERE id = ?", (mid,)
                ).fetchone()
                if module_row:
                    empty_modules.append(
                        f"Module '{module_row['name']}' (id={mid}) now has 0 files"
                    )

        stmts = get_return_statements("remove_files_from_module")
        if empty_modules:
            stmts = tuple(empty_modules) + (
                "Consider deleting empty modules via delete_module or reassigning files.",
            ) + stmts

        return ModuleFilesBatchResult(
            success=True,
            removed_count=removed,
            skipped_count=skipped,
            empty_modules=tuple(empty_modules),
            return_statements=stmts,
        )

    except Exception as e:
        return ModuleFilesBatchResult(
            success=False,
            error=f"Batch remove failed: {str(e)}",
        )

    finally:
        conn.close()
