"""
AIMFP Helper Functions - Audit Code Index

Everything an audit needs, gathered once: which files are in scope, what
project.db says about them (a read-only snapshot), what the source says about
them, and the parsed tree of every tracked Python file (call resolution follows
imports into files outside the scope).

Every audit tool builds one CodeIndex and works from it, so a combined audit
(audit_tracking) parses each file once and reads the database once.

Scope selectors (combined as a union; none at all means the whole project):
- file_ids: tracked file IDs
- paths: project-relative file paths
- since_commit: files changed since a git ref (committed, staged, unstaged,
  and new untracked files)
- task_id (+ task_type): files linked to a task, subtask or sidequest
- module_path: a project-relative subtree

Fidelity is per file and honest: 'full' (Python, via ast) supports every
check; 'names_only' (regex patterns from watchdog/config.py) supports only
low-confidence "missing function" findings; 'none' supports nothing and is
listed so the AI can verify by reading. 'missing' marks a tracked file gone
from disk.
"""

import ast
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from ..utils import get_user_preferences_db_path, _open_project_connection
from ...watchdog.config import build_exclusion_sets, detect_language, should_exclude
from ...watchdog.reconciliation import _read_user_exclusions, _read_watchdogignore
from ..catalog.extract import ExtractedEntities, extract_entities
from ..catalog.scan import _effect_read_source, _effect_walk_source_files, resolve_scan_root
from ..catalog.reconcile import normalize_reconcile_path
from ..catalog.callgraph import TrackedIndex, _effect_parse_python_files
from ..project.task_files import WorkItemRef, query_task_file_rows


# Work item tables a task_id selector may name, keyed by the task_type values accepted.
TASK_TYPE_TABLES: Dict[str, str] = {
    'task': 'tasks', 'tasks': 'tasks',
    'subtask': 'subtasks', 'subtasks': 'subtasks',
    'sidequest': 'sidequests', 'sidequests': 'sidequests',
}


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class ScopeRequest:
    """What the caller asked to audit. Empty selectors mean the whole project."""
    file_ids: Tuple[int, ...] = ()
    paths: Tuple[str, ...] = ()
    since_commit: Optional[str] = None
    task_id: Optional[int] = None
    task_type: str = 'task'
    module_path: Optional[str] = None


@dataclass(frozen=True)
class AuditScope:
    """The resolved set of files an audit covers."""
    paths: Tuple[str, ...]
    label: str
    whole_project: bool
    skipped: Tuple[Dict[str, str], ...] = ()


@dataclass(frozen=True)
class DbSnapshot:
    """Read-only copy of the tracking tables an audit compares against.

    has_source_hash / has_is_indirect are False on a database not yet migrated
    to schema 1.15; the columns then read as None / 0.
    """
    files: Tuple[Dict[str, Any], ...]
    functions: Tuple[Dict[str, Any], ...]
    types: Tuple[Dict[str, Any], ...]
    interactions: Tuple[Dict[str, Any], ...]
    types_functions: Tuple[Dict[str, Any], ...]
    has_source_hash: bool = False
    has_is_indirect: bool = False


@dataclass(frozen=True)
class FileFacts:
    """What the source says about one in-scope file."""
    path: str
    file_id: Optional[int]
    language: Optional[str]
    fidelity: str
    entities: ExtractedEntities = field(default_factory=ExtractedEntities)
    parse_error: Optional[str] = None


@dataclass(frozen=True)
class CodeIndex:
    """One audit's complete view of database and code."""
    project_root: str
    scope: AuditScope
    snapshot: DbSnapshot
    files: Tuple[FileFacts, ...]
    trees: Dict[str, ast.Module]


# ============================================================================
# Pure Helpers
# ============================================================================

def build_scope_request(
    file_ids: Optional[List[int]] = None,
    paths: Optional[List[str]] = None,
    since_commit: Optional[str] = None,
    task_id: Optional[int] = None,
    task_type: Optional[str] = None,
    module_path: Optional[str] = None,
) -> ScopeRequest:
    """Pure: Normalize tool arguments into a ScopeRequest."""
    return ScopeRequest(
        file_ids=tuple(file_ids or ()),
        paths=tuple(paths or ()),
        since_commit=(since_commit or '').strip() or None,
        task_id=task_id,
        task_type=(task_type or 'task').strip().lower(),
        module_path=(module_path or '').strip().strip('/') or None,
    )


def is_whole_project(request: ScopeRequest) -> bool:
    """Pure: Whether a request names no selector at all (audit everything)."""
    return not (request.file_ids or request.paths or request.since_commit
                or request.task_id is not None or request.module_path)


def scope_label(request: ScopeRequest) -> str:
    """Pure: Short human description of a scope request."""
    if is_whole_project(request):
        return 'whole project'
    parts = (
        ([f"{request.task_type} {request.task_id}"] if request.task_id is not None else [])
        + ([f"since {request.since_commit}"] if request.since_commit else [])
        + ([f"module {request.module_path}"] if request.module_path else [])
        + ([f"{len(request.file_ids)} file id(s)"] if request.file_ids else [])
        + ([f"{len(request.paths)} path(s)"] if request.paths else [])
    )
    return ', '.join(parts)


def under_prefix(path: str, prefix: Optional[str]) -> bool:
    """Pure: Whether a project-relative path sits at or under a directory prefix."""
    if not prefix or prefix == '.':
        return True
    return path == prefix or path.startswith(prefix.rstrip('/') + '/')


def tracked_index_from_snapshot(snapshot: DbSnapshot) -> TrackedIndex:
    """
    Pure: The call graph's lookup tables, built from a snapshot instead of a
    second database read (same shape as callgraph.query_tracked_functions).
    """
    paths = {row['id']: row['path'] for row in snapshot.files}
    by_file: Dict[str, Dict[str, int]] = {}
    by_name: Dict[str, List[int]] = {}
    id_to_path: Dict[int, str] = {}
    for fn in snapshot.functions:
        path = paths.get(fn['file_id'])
        if path is None:
            continue
        by_file.setdefault(path, {})[fn['name']] = fn['id']
        by_name.setdefault(fn['name'], []).append(fn['id'])
        id_to_path[fn['id']] = path
    return (by_file, by_name, id_to_path, tuple(paths.values()))


def classify_scope_paths(
    candidates: Tuple[str, ...],
    explicit: FrozenSet[str],
    is_excluded: Callable[[str], bool],
    is_known: Callable[[str], bool],
) -> Tuple[Tuple[str, ...], Tuple[Dict[str, str], ...]]:
    """
    Pure (given the predicates): Split candidate paths into audited paths and
    skips with a reason.

    Unsupported languages are skipped silently unless the caller named the
    path explicitly; exclusions are always reported, since an excluded file
    the caller asked about deserves an answer.

    Args:
        candidates: Every path the selectors produced (deduplicated, ordered)
        explicit: Paths the caller named directly (paths / file_ids)
        is_excluded: Watchdog exclusion predicate
        is_known: Whether the path is tracked or exists on disk
    """
    audited: List[str] = []
    skipped: List[Dict[str, str]] = []
    for path in candidates:
        if is_excluded(path):
            skipped.append({'path': path, 'reason': 'excluded by watchdog exclusions'})
        elif detect_language(path) is None:
            if path in explicit:
                skipped.append({'path': path, 'reason': 'unsupported language'})
        elif not is_known(path):
            skipped.append({'path': path, 'reason': 'not tracked and not on disk'})
        else:
            audited.append(path)
    return (tuple(audited), tuple(skipped))


def file_facts_from_source(
    path: str,
    file_id: Optional[int],
    source: Optional[str],
) -> FileFacts:
    """
    Pure: Facts for one file from its text (None when it is gone or unreadable).

    Nested functions are dropped: AIMFP tracks module-level functions.
    """
    language = detect_language(path)
    if source is None:
        return FileFacts(path=path, file_id=file_id, language=language, fidelity='missing')
    entities = extract_entities(source, language)
    top_level = ExtractedEntities(
        functions=tuple(fn for fn in entities.functions if not fn.is_nested),
        types=entities.types,
        module_docstring=entities.module_docstring,
        fidelity=entities.fidelity,
        parse_error=entities.parse_error,
    )
    return FileFacts(
        path=path, file_id=file_id, language=language,
        fidelity=entities.fidelity, entities=top_level, parse_error=entities.parse_error,
    )


def scope_summary(index: CodeIndex) -> Dict[str, Any]:
    """Pure: The scope block of an audit result: what was covered, at what fidelity."""
    fidelity: Dict[str, int] = {}
    for facts in index.files:
        fidelity[facts.fidelity] = fidelity.get(facts.fidelity, 0) + 1
    return {
        'label': index.scope.label,
        'whole_project': index.scope.whole_project,
        'files_audited': len(index.files),
        'fidelity': fidelity,
        'skipped': len(index.scope.skipped),
        'schema_1_15': index.snapshot.has_source_hash and index.snapshot.has_is_indirect,
    }


def unchecked_files(index: CodeIndex, need: str = 'full') -> Tuple[Dict[str, Any], ...]:
    """
    Pure: In-scope files a full-fidelity check could not cover, so the AI knows
    what to verify by reading: names_only / none fidelity, or untracked
    (nothing in the database to compare).
    """
    return tuple(
        {'path': f.path, 'fidelity': f.fidelity, 'file_id': f.file_id,
         'reason': 'not tracked' if f.file_id is None else (
             f.parse_error or f"{f.fidelity} extraction: only full fidelity is checked")}
        for f in index.files
        if f.fidelity != 'missing' and (f.fidelity != need or f.file_id is None)
    )


# ============================================================================
# Effects
# ============================================================================

def _table_columns(conn: sqlite3.Connection, table: str) -> FrozenSet[str]:
    """Effect: Column names of a table (empty when the table is absent)."""
    return frozenset(row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def query_snapshot(conn: sqlite3.Connection) -> DbSnapshot:
    """
    Effect: Read the tracking tables into an immutable snapshot (SELECT only).

    Tolerates a pre-1.15 database: source_hash and is_indirect read as
    None / 0 when the columns do not exist yet.
    """
    function_cols = _table_columns(conn, 'functions')
    interaction_cols = _table_columns(conn, 'interactions')
    has_hash = 'source_hash' in function_cols
    has_indirect = 'is_indirect' in interaction_cols

    def rows(sql: str) -> Tuple[Dict[str, Any], ...]:
        return tuple(dict(row) for row in conn.execute(sql).fetchall())

    return DbSnapshot(
        files=rows("SELECT id, name, path, language, no_flow_reason, is_reserved, updated_at FROM files ORDER BY id"),
        functions=rows(
            "SELECT id, name, file_id, purpose, parameters, returns, is_reserved, updated_at, "
            + ("source_hash" if has_hash else "NULL AS source_hash")
            + " FROM functions ORDER BY id"
        ),
        types=rows(
            "SELECT id, name, file_id, definition_json, description, is_reserved, updated_at "
            "FROM types ORDER BY id"
        ),
        interactions=rows(
            "SELECT id, source_function_id, target_function_id, interaction_type, description, "
            + ("is_indirect" if has_indirect else "0 AS is_indirect")
            + " FROM interactions ORDER BY id"
        ),
        types_functions=rows("SELECT id, type_id, function_id, role FROM types_functions ORDER BY id"),
        has_source_hash=has_hash,
        has_is_indirect=has_indirect,
    )


def _effect_git_changed_paths(project_root: str, ref: str) -> Tuple[Tuple[str, ...], Optional[str]]:
    """
    Effect: Files changed since a git ref — committed after it, staged, unstaged,
    plus new untracked files — relative to the project root.

    Returns:
        (paths, error); error is set when git is unavailable or the ref is unknown
    """
    def run(args: Tuple[str, ...]) -> Tuple[bool, str]:
        try:
            done = subprocess.run(
                ('git',) + args, cwd=project_root, capture_output=True, text=True, check=True,
            )
            return (True, done.stdout)
        except FileNotFoundError:
            return (False, 'git command not found')
        except subprocess.CalledProcessError as exc:
            return (False, (exc.stderr or exc.stdout or '').strip())

    ok, changed = run(('diff', '--name-only', '--relative', ref))
    if not ok:
        return ((), f"git diff against '{ref}' failed: {changed}")
    ok, untracked = run(('ls-files', '--others', '--exclude-standard'))
    if not ok:
        return ((), f"git ls-files failed: {untracked}")
    lines = (changed + '\n' + untracked).splitlines()
    return (tuple(dict.fromkeys(line.strip() for line in lines if line.strip())), None)


def _effect_task_paths(
    conn: sqlite3.Connection,
    task_type: str,
    task_id: int,
) -> Tuple[Tuple[str, ...], Optional[str]]:
    """Effect: Paths of files linked to a work item (a task includes its subtasks')."""
    table = TASK_TYPE_TABLES.get(task_type)
    if table is None:
        return ((), f"Invalid task_type '{task_type}'. Use 'task', 'subtask' or 'sidequest'.")
    if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (task_id,)).fetchone() is None:
        return ((), f"{task_type} {task_id} not found")
    rows = query_task_file_rows(conn, WorkItemRef(table, task_id), include_subtasks=(table == 'tasks'))
    return (tuple(row['path'] for row in rows), None)


def _effect_source_paths(conn: sqlite3.Connection, project_root: str, module_path: Optional[str],
                         exclusions: Tuple[frozenset, frozenset, Tuple[str, ...]]) -> Tuple[str, ...]:
    """Effect: On-disk source files under module_path (or source_directory), exclusions applied."""
    row = conn.execute("SELECT value FROM infrastructure WHERE type = 'source_directory'").fetchone()
    source_directory = row['value'] if row and row['value'] else ''
    scan_root, _ = resolve_scan_root(project_root, source_directory, module_path)
    if not os.path.isdir(scan_root):
        return ()
    excluded_dirs, excluded_exts, ignore_patterns = exclusions
    return _effect_walk_source_files(scan_root, project_root, excluded_dirs, excluded_exts, ignore_patterns)


def _effect_exclusions(project_root: str) -> Tuple[frozenset, frozenset, Tuple[str, ...]]:
    """Effect: The watchdog exclusion stack (built-ins, user settings, .watchdogignore)."""
    user_dirs, user_exts = _read_user_exclusions(get_user_preferences_db_path(project_root))
    excluded_dirs, excluded_exts = build_exclusion_sets(user_dirs, user_exts)
    return (excluded_dirs, excluded_exts, _read_watchdogignore(project_root))


def resolve_scope(
    conn: sqlite3.Connection,
    project_root: str,
    request: ScopeRequest,
    snapshot: DbSnapshot,
) -> Tuple[Optional[AuditScope], Optional[str]]:
    """
    Effect: Turn a ScopeRequest into the concrete paths to audit.

    Returns:
        (scope, None) or (None, error) when a selector cannot be resolved
    """
    exclusions = _effect_exclusions(project_root)
    excluded_dirs, excluded_exts, ignore_patterns = exclusions
    tracked_paths = tuple(row['path'] for row in snapshot.files)
    path_by_id = {row['id']: row['path'] for row in snapshot.files}

    candidates: List[str] = []
    explicit: List[str] = []
    refused: List[Dict[str, str]] = []

    for file_id in request.file_ids:
        if file_id in path_by_id:
            candidates.append(path_by_id[file_id])
            explicit.append(path_by_id[file_id])
        else:
            refused.append({'path': f"file_id {file_id}", 'reason': 'file_id not tracked'})

    for raw in request.paths:
        normalized, reason = normalize_reconcile_path(raw)
        if normalized is None:
            refused.append({'path': str(raw), 'reason': reason})
        else:
            candidates.append(normalized)
            explicit.append(normalized)

    if request.task_id is not None:
        task_paths, error = _effect_task_paths(conn, request.task_type, request.task_id)
        if error:
            return (None, error)
        candidates.extend(task_paths)

    if request.since_commit:
        changed, error = _effect_git_changed_paths(project_root, request.since_commit)
        if error:
            return (None, error)
        candidates.extend(changed)

    if request.module_path or is_whole_project(request):
        prefix = request.module_path
        candidates.extend(p for p in tracked_paths if under_prefix(p, prefix))
        candidates.extend(_effect_source_paths(conn, project_root, prefix, exclusions))

    tracked_set = frozenset(tracked_paths)
    audited, skipped = classify_scope_paths(
        tuple(dict.fromkeys(candidates)),
        frozenset(explicit),
        lambda p: should_exclude(p, excluded_dirs, excluded_exts, ignore_patterns),
        lambda p: p in tracked_set or os.path.isfile(os.path.join(project_root, p)),
    )
    return (
        AuditScope(
            paths=tuple(sorted(audited)),
            label=scope_label(request),
            whole_project=is_whole_project(request),
            skipped=tuple(refused) + skipped,
        ),
        None,
    )


def build_code_index(
    project_root: str,
    request: ScopeRequest,
) -> Tuple[Optional[CodeIndex], Optional[str]]:
    """
    Effect: Read the database once and the in-scope sources once, and parse
    every tracked Python file for call resolution.

    Returns:
        (index, None) or (None, error)
    """
    conn = _open_project_connection(project_root)
    try:
        snapshot = query_snapshot(conn)
        scope, error = resolve_scope(conn, project_root, request, snapshot)
    finally:
        conn.close()
    if scope is None:
        return (None, error)

    id_by_path = {row['path']: row['id'] for row in snapshot.files}
    files = tuple(
        file_facts_from_source(path, id_by_path.get(path),
                               _effect_read_source(os.path.join(project_root, path)))
        for path in scope.paths
    )
    trees = _effect_parse_python_files(project_root, tuple(id_by_path))
    return (CodeIndex(project_root=project_root, scope=scope, snapshot=snapshot,
                      files=files, trees=trees), None)
