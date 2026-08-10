"""
AIMFP Helper Functions - Source Tree Scanning

Read-only inventory of an existing codebase, for adopting it into AIMFP tracking.

``scan_source_tree`` walks the project's ``source_directory``, applies the exact
same exclusion stack the watchdog uses, extracts signatures from each surviving
file, and reports what is already tracked versus what is not. It writes nothing —
the AI reviews the inventory, enriches purposes, and then calls the ``catalog_*``
tools to register.

The exclusion stack is deliberately shared with the watchdog rather than
reimplemented: a catalog that registered files the watchdog then flagged as
untracked, or skipped files the watchdog expected, would generate permanent
reconciliation noise. Both read the same three layers — built-in exclusions, the
``watchdog_excluded_*`` user settings, and the project-root ``.watchdogignore``.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ..utils import get_return_statements, get_cached_project_root, _open_project_connection
from ...watchdog.config import (
    build_exclusion_sets,
    detect_language,
    matches_ignore_patterns,
    should_exclude,
)
from ...watchdog.reconciliation import _read_user_exclusions, _read_watchdogignore
from .extract import ExtractedEntities, extract_entities


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class ScannedFile:
    """One source file found on disk, with whatever was extracted from it."""
    path: str
    language: Optional[str]
    tracked: bool
    file_id: Optional[int] = None
    function_count: int = 0
    type_count: int = 0
    fidelity: str = 'none'
    parse_error: Optional[str] = None
    entities: Optional[ExtractedEntities] = None


@dataclass(frozen=True)
class ScanResult:
    """Result of a source tree scan."""
    success: bool
    source_directory: Optional[str] = None
    scanned_path: Optional[str] = None
    files: Tuple[Dict[str, Any], ...] = ()
    total_files: int = 0
    untracked_files: int = 0
    total_functions: int = 0
    total_types: int = 0
    skipped_unsupported: Tuple[str, ...] = ()
    parse_failures: Tuple[str, ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_read_source(absolute_path: str) -> Optional[str]:
    """
    Effect: Read a source file as UTF-8, tolerating undecodable bytes.

    Args:
        absolute_path: Absolute path to the file

    Returns:
        File contents, or None when the file cannot be read
    """
    try:
        with open(absolute_path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError:
        return None


def _effect_tracked_file_ids(project_root: str) -> Dict[str, int]:
    """
    Effect: Map every tracked file path to its database ID.

    Args:
        project_root: Project root directory

    Returns:
        Dict of project-relative path -> file ID (empty when nothing is tracked)
    """
    conn = _open_project_connection(project_root)
    try:
        rows = conn.execute("SELECT id, path FROM files").fetchall()
        return {row["path"]: row["id"] for row in rows}
    finally:
        conn.close()


def _effect_source_directory(project_root: str) -> str:
    """
    Effect: Read source_directory from the infrastructure table.

    Args:
        project_root: Project root directory

    Returns:
        Project-relative source directory, or '' when unset
    """
    conn = _open_project_connection(project_root)
    try:
        row = conn.execute(
            "SELECT value FROM infrastructure WHERE type = ?", ('source_directory',)
        ).fetchone()
        return row["value"] if row and row["value"] else ''
    finally:
        conn.close()


def _effect_walk_source_files(
    scan_root: str,
    project_root: str,
    excluded_dirs: frozenset,
    excluded_extensions: frozenset,
    ignore_patterns: Tuple[str, ...],
) -> Tuple[str, ...]:
    """
    Effect: Walk a directory tree and return project-relative paths that survive
    the exclusion stack.

    Prunes excluded directories in place so excluded subtrees are never descended
    into, matching reconcile_unregistered_files' traversal exactly.

    Args:
        scan_root: Absolute directory to walk
        project_root: Absolute project root (paths are returned relative to it)
        excluded_dirs: Directory names to skip
        excluded_extensions: File extensions to skip
        ignore_patterns: .watchdogignore glob patterns

    Returns:
        Tuple of project-relative file paths, sorted
    """
    found: list = []

    for dirpath, dirnames, filenames in os.walk(scan_root):
        dirnames[:] = [
            d for d in dirnames
            if d not in excluded_dirs
            and not matches_ignore_patterns(
                os.path.relpath(os.path.join(dirpath, d), project_root),
                ignore_patterns,
            )
        ]

        for filename in filenames:
            relative_path = os.path.relpath(
                os.path.join(dirpath, filename), project_root
            )
            if should_exclude(
                relative_path, excluded_dirs, excluded_extensions, ignore_patterns
            ):
                continue
            found.append(relative_path)

    return tuple(sorted(found))


# ============================================================================
# Pure Helpers
# ============================================================================

def entities_to_dict(entities: ExtractedEntities, include_private: bool) -> Dict[str, Any]:
    """
    Pure: Render extracted entities into the JSON-serializable shape the tool returns.

    Args:
        entities: Extraction output for one file
        include_private: Whether underscore-prefixed entities are included

    Returns:
        Dict with 'functions' and 'types' lists ready for MCP serialization
    """
    functions = [
        {
            'name': fn.name,
            'line': fn.line,
            'purpose': fn.purpose,
            'parameters': list(fn.parameters),
            'returns': fn.returns,
            'is_async': fn.is_async,
            'is_private': fn.is_private,
            'is_effect': fn.is_effect,
            'is_nested': fn.is_nested,
        }
        for fn in entities.functions
        if include_private or not fn.is_private
    ]

    types = [
        {
            'name': tp.name,
            'line': tp.line,
            'kind': tp.kind,
            'definition': tp.definition,
            'description': tp.description,
            'is_private': tp.is_private,
        }
        for tp in entities.types
        if include_private or not tp.is_private
    ]

    return {'functions': functions, 'types': types}


def resolve_scan_root(
    project_root: str,
    source_directory: str,
    module_path: Optional[str],
) -> Tuple[str, str]:
    """
    Pure: Resolve which subtree to scan.

    A module_path narrows the scan so a large codebase can be adopted one module at
    a time instead of in a single overwhelming pass. It is interpreted relative to
    the project root, and may sit anywhere beneath it.

    Args:
        project_root: Absolute project root
        source_directory: Project-relative source directory
        module_path: Optional project-relative subtree to narrow to

    Returns:
        (absolute scan root, project-relative scanned path)
    """
    relative = module_path.strip('/') if module_path else source_directory.strip('/')
    if not relative:
        return (project_root, '.')
    return (os.path.join(project_root, relative), relative)


# ============================================================================
# Public Tool
# ============================================================================

def scan_source_tree(
    module_path: Optional[str] = None,
    include_private: bool = True,
    include_nested: bool = False,
    untracked_only: bool = False,
    project_root: Optional[str] = None,
) -> ScanResult:
    """
    Scan an existing codebase and report what could be cataloged.

    Read-only: writes nothing to the database. Applies the watchdog's exclusion
    stack (built-in exclusions, watchdog_excluded_* user settings, and
    .watchdogignore) so the catalog and the watchdog always agree on scope.

    Python files come back with full signatures; other supported languages come
    back with function names and line numbers only.

    Args:
        module_path: Project-relative subtree to narrow the scan to (e.g.
            'src/aimfp/helpers/project'). Defaults to the whole source_directory.
        include_private: Include underscore-prefixed functions and types
        include_nested: Include functions defined inside other functions
        untracked_only: Report only files with no existing database record
        project_root: Project root override (defaults to the cached root)

    Returns:
        ScanResult with a per-file inventory and aggregate counts
    """
    project_root = project_root or get_cached_project_root()

    try:
        source_directory = _effect_source_directory(project_root)
        scan_root, scanned_path = resolve_scan_root(
            project_root, source_directory, module_path
        )

        if not os.path.isdir(scan_root):
            return ScanResult(
                success=False,
                error=f"Scan path does not exist: {scanned_path}",
            )

        user_dirs, user_extensions = _read_user_exclusions(
            os.path.join(project_root, '.aimfp-project', 'user_preferences.db')
        )
        excluded_dirs, excluded_extensions = build_exclusion_sets(
            user_dirs, user_extensions
        )
        ignore_patterns = _read_watchdogignore(project_root)

        tracked = _effect_tracked_file_ids(project_root)
        relative_paths = _effect_walk_source_files(
            scan_root, project_root, excluded_dirs, excluded_extensions, ignore_patterns
        )

        files: list = []
        skipped: list = []
        parse_failures: list = []
        total_functions = 0
        total_types = 0

        for relative_path in relative_paths:
            is_tracked = relative_path in tracked
            if untracked_only and is_tracked:
                continue

            language = detect_language(relative_path)
            if language is None:
                skipped.append(relative_path)
                continue

            source = _effect_read_source(os.path.join(project_root, relative_path))
            if source is None:
                skipped.append(relative_path)
                continue

            entities = extract_entities(source, language)
            if entities.parse_error:
                parse_failures.append(relative_path)

            rendered = entities_to_dict(entities, include_private)
            if not include_nested:
                rendered['functions'] = [
                    fn for fn in rendered['functions'] if not fn['is_nested']
                ]

            total_functions += len(rendered['functions'])
            total_types += len(rendered['types'])

            files.append({
                'path': relative_path,
                'name': os.path.splitext(os.path.basename(relative_path))[0],
                'language': language,
                'tracked': is_tracked,
                'file_id': tracked.get(relative_path),
                'module_docstring': entities.module_docstring,
                'fidelity': entities.fidelity,
                'parse_error': entities.parse_error,
                'functions': rendered['functions'],
                'types': rendered['types'],
            })

        return ScanResult(
            success=True,
            source_directory=source_directory,
            scanned_path=scanned_path,
            files=tuple(files),
            total_files=len(files),
            untracked_files=sum(1 for f in files if not f['tracked']),
            total_functions=total_functions,
            total_types=total_types,
            skipped_unsupported=tuple(skipped),
            parse_failures=tuple(parse_failures),
            return_statements=get_return_statements("scan_source_tree"),
        )

    except Exception as exc:
        return ScanResult(success=False, error=f"Scan failed: {str(exc)}")
