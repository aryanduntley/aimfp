"""
AIMFP Helper Functions - Reconcile Changed Paths

reconcile_paths brings the tracking of a set of changed files up to date in
one call and one transaction — after a git pull, a model's edits, or anything
else that changed source outside the reserve/finalize flow. It replaces the
per-file sequence scan -> look up -> catalog -> list -> graph -> add.

Rules:
- Never overwrites prose. Functions and types already tracked are not
  touched; only ones newly present in the source are cataloged, with the
  signature extraction provides.
- Never deletes. Tracked functions/types no longer in the source, and tracked
  files gone from disk, are REPORTED for the AI to decide on.
- A tracked file gone from disk is renamed when the caller says so
  (renames=[{from, to}], e.g. from git diff -M), or else when exactly one
  untracked path in the same call has the same file name; its functions,
  flows and modules stay attached.
- A tracked 'call' edge whose target name no longer appears anywhere in the
  caller's body is REPORTED as stale (Python only), never deleted.
- dry_run=True runs everything and rolls back, so the report says exactly
  what a real run would do.
"""

import ast
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..utils import (
    get_cached_project_root,
    get_return_statements,
    get_user_preferences_db_path,
    _open_project_connection,
)
from ..project.interactions import _insert_interaction_if_new
from ..project.task_files import link_files_to_current_focus_effect
from ...watchdog.config import build_exclusion_sets, detect_language, should_exclude
from ...watchdog.reconciliation import _read_user_exclusions, _read_watchdogignore
from .callgraph import build_call_edges, extract_referenced_names, query_tracked_functions
from .extract import ExtractedEntities, extract_entities
from .register import (
    _effect_upsert_file,
    _effect_upsert_function,
    _effect_upsert_type,
    serialize_json_field,
    supplied_or_none,
)
from .scan import _effect_read_source


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class ReconcileResult:
    """What reconcile_paths did (or, with dry_run, would do)."""
    success: bool
    dry_run: bool = False
    files_cataloged: Tuple[Dict[str, Any], ...] = ()
    files_renamed: Tuple[Dict[str, Any], ...] = ()
    files_missing: Tuple[Dict[str, Any], ...] = ()
    functions_added: Tuple[Dict[str, Any], ...] = ()
    functions_missing_in_source: Tuple[Dict[str, Any], ...] = ()
    types_added: Tuple[Dict[str, Any], ...] = ()
    types_missing_in_source: Tuple[Dict[str, Any], ...] = ()
    interactions_added: int = 0
    interactions_skipped: int = 0
    stale_interactions: Tuple[Dict[str, Any], ...] = ()
    unresolved_calls: Tuple[str, ...] = ()
    skipped: Tuple[Dict[str, str], ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helpers
# ============================================================================

def normalize_reconcile_path(path: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Pure: (project-relative POSIX path, None) or (None, reason it was refused).

    Absolute paths, '..' and empty paths are refused: reconcile only works on
    files inside the project, named relative to its root.
    """
    if not isinstance(path, str) or not path.strip():
        return (None, "empty path")
    unified = path.strip().replace('\\', '/')
    if unified.startswith('/'):
        return (None, "absolute path; pass it relative to the project root")
    parts = tuple(p for p in unified.split('/') if p not in ('', '.'))
    if not parts:
        return (None, "does not name a file")
    if '..' in parts:
        return (None, "leaves the project ('..')")
    return ('/'.join(parts), None)


def pair_renames(
    dead_paths: Tuple[str, ...],
    new_paths: Tuple[str, ...],
) -> Tuple[Tuple[str, str], ...]:
    """
    Pure: (old, new) pairs where a vanished tracked path and an untracked new
    path share a file name, and that name is unique on both sides.
    """
    def by_name(paths: Tuple[str, ...]) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {}
        for p in paths:
            grouped.setdefault(os.path.basename(p), []).append(p)
        return grouped

    dead, new = by_name(dead_paths), by_name(new_paths)
    return tuple(
        (dead[name][0], new[name][0])
        for name in sorted(dead)
        if len(dead[name]) == 1 and len(new.get(name, ())) == 1
    )


def plan_explicit_renames(
    renames: Tuple[Tuple[Optional[str], Optional[str]], ...],
    is_tracked: Callable[[str], bool],
    exists: Callable[[str], bool],
) -> Tuple[Tuple[Tuple[str, str], ...], Tuple[Dict[str, str], ...]]:
    """
    Pure (given the two predicates): split caller-declared renames into
    applicable (from, to) pairs and refusals with a reason.

    Applicable when 'from' is tracked and gone from disk, and 'to' is on disk
    and not tracked yet. A path may take part in one rename only.
    """
    applied: List[Tuple[str, str]] = []
    refused: List[Dict[str, str]] = []
    used: set = set()
    for old, new in renames:
        label = f"{old} -> {new}"
        if not old or not new:
            refused.append({"path": label, "reason": "rename needs usable 'from' and 'to' paths"})
        elif old in used or new in used:
            refused.append({"path": label, "reason": "path already used by another rename"})
        elif not is_tracked(old):
            refused.append({"path": label, "reason": "'from' is not tracked"})
        elif exists(old):
            refused.append({"path": label, "reason": "'from' still exists on disk"})
        elif not exists(new):
            refused.append({"path": label, "reason": "'to' does not exist on disk"})
        elif is_tracked(new):
            refused.append({"path": label, "reason": "'to' is already tracked"})
        else:
            applied.append((old, new))
            used.update((old, new))
    return (tuple(applied), tuple(refused))


def find_stale_calls(
    edges: Tuple[Dict[str, Any], ...],
    referenced: Dict[str, Any],
) -> Tuple[Dict[str, Any], ...]:
    """
    Pure: 'call' edges whose target name the caller no longer references.

    Args:
        edges: {id, source_name, target_name, ...} rows for callers in one file
        referenced: extract_referenced_names output for that file

    Callers absent from the source are skipped: they are reported as
    functions_missing_in_source, and deleting them cascades their edges.
    """
    return tuple(
        edge for edge in edges
        if edge["source_name"] in referenced
        and edge["target_name"] not in referenced[edge["source_name"]]
    )


def select_entities(
    entities: ExtractedEntities,
    include_private: bool,
) -> ExtractedEntities:
    """Pure: Drop nested functions always, and private names unless asked for."""
    return ExtractedEntities(
        functions=tuple(
            f for f in entities.functions
            if not f.is_nested and (include_private or not f.is_private)
        ),
        types=tuple(t for t in entities.types if include_private or not t.is_private),
        module_docstring=entities.module_docstring,
        fidelity=entities.fidelity,
        parse_error=entities.parse_error,
    )


# ============================================================================
# Effects (all through the caller's connection; nothing commits here)
# ============================================================================

def _effect_tracked_file(conn: sqlite3.Connection, path: str) -> Optional[Dict[str, Any]]:
    """Effect: The files row for a path, or None."""
    row = conn.execute("SELECT id, name, language FROM files WHERE path = ?", (path,)).fetchone()
    return dict(row) if row else None


def _effect_names(conn: sqlite3.Connection, table: str, file_id: int) -> Dict[str, int]:
    """Effect: name -> id for the functions or types tracked in one file."""
    rows = conn.execute(f"SELECT id, name FROM {table} WHERE file_id = ?", (file_id,)).fetchall()
    return {row["name"]: row["id"] for row in rows}


def _effect_rename_file(conn: sqlite3.Connection, file_id: int, new_path: str) -> None:
    """Effect: Point a tracked file row at its new path (functions stay attached)."""
    conn.execute(
        "UPDATE files SET path = ?, name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_path, os.path.basename(new_path), file_id),
    )


def _effect_stale_calls(
    conn: sqlite3.Connection,
    file_id: int,
    path: str,
    source: str,
) -> Tuple[Dict[str, Any], ...]:
    """Effect: Stale 'call' edges from the functions of one Python file (report only)."""
    try:
        referenced = extract_referenced_names(ast.parse(source))
    except SyntaxError:
        return ()
    rows = conn.execute(
        """
        SELECT i.id, s.name AS source_name, t.name AS target_name, tf.path AS target_path
        FROM interactions i
        JOIN functions s ON s.id = i.source_function_id
        JOIN functions t ON t.id = i.target_function_id
        LEFT JOIN files tf ON tf.id = t.file_id
        WHERE i.interaction_type = 'call' AND s.file_id = ?
        ORDER BY i.id
        """,
        (file_id,),
    ).fetchall()
    return tuple(
        {**edge, "path": path}
        for edge in find_stale_calls(tuple(dict(r) for r in rows), referenced)
    )


def _effect_sync_file_entities(
    conn: sqlite3.Connection,
    file_id: int,
    path: str,
    entities: ExtractedEntities,
) -> Tuple[Tuple[Dict[str, Any], ...], ...]:
    """
    Effect: Catalog functions/types present in the source but not tracked, and
    list tracked ones the source no longer defines (full-fidelity parses only,
    since name-only extraction misses some forms).

    Returns:
        (functions_added, functions_missing, types_added, types_missing)
    """
    tracked_functions = _effect_names(conn, "functions", file_id)
    tracked_types = _effect_names(conn, "types", file_id)

    functions_added = tuple(
        {"id": _effect_upsert_function(
            conn, fn.name, file_id, supplied_or_none(fn.purpose),
            serialize_json_field(list(fn.parameters)),
            serialize_json_field(fn.returns),
        )[0], "name": fn.name, "path": path}
        for fn in entities.functions if fn.name not in tracked_functions
    )
    types_added = tuple(
        {"id": _effect_upsert_type(
            conn, ty.name, file_id, serialize_json_field(ty.definition),
            supplied_or_none(ty.description),
        )[0], "name": ty.name, "path": path}
        for ty in entities.types if ty.name not in tracked_types
    )

    if entities.fidelity != 'full':
        return (functions_added, (), types_added, ())
    in_source_functions = {fn.name for fn in entities.functions}
    in_source_types = {ty.name for ty in entities.types}
    functions_missing = tuple(
        {"id": fid, "name": name, "path": path}
        for name, fid in sorted(tracked_functions.items())
        if name not in in_source_functions
    )
    types_missing = tuple(
        {"id": tid, "name": name, "path": path}
        for name, tid in sorted(tracked_types.items())
        if name not in in_source_types
    )
    return (functions_added, functions_missing, types_added, types_missing)


# ============================================================================
# Public Tool
# ============================================================================

def reconcile_paths(
    paths: List[str],
    dry_run: bool = False,
    include_private: bool = True,
    renames: Optional[List[Dict[str, str]]] = None,
    project_root: Optional[str] = None,
) -> ReconcileResult:
    """
    Bring the tracking of changed files up to date in one transaction.

    For each project-relative path:
    - untracked file on disk: catalog the file, its functions and its types
    - tracked file on disk: catalog functions/types newly present in the source
      (existing rows are never touched); report tracked ones the source no
      longer defines
    - tracked file gone from disk: rename it as declared in renames, else to
      the one untracked path in this call with the same file name, else
      report it as missing
    Then add the call edges (Python) of every affected file, skipping edges
    already tracked, and report tracked call edges whose target the caller
    no longer references (stale_interactions; never deleted). Watchdog exclusions (.watchdogignore, excluded dirs and
    extensions) are honoured, and files in unsupported languages are skipped.

    Args:
        paths: Project-relative file paths that changed (e.g. from git diff)
        dry_run: Do everything, then roll back; the report is what a real run does
        include_private: Also catalog _private functions and types (default True,
            like scan_source_tree)
        renames: [{from, to}] moves to apply before anything else — needed when
            a file was renamed as well as moved (git diff -M --name-status R lines).
            Both paths are added to the set being reconciled.
        project_root: Explicit root for embedding hosts (defaults to the session root)

    Returns:
        ReconcileResult listing every file, function, type and edge touched,
        what is missing, and what was skipped with the reason
    """
    root = project_root or get_cached_project_root()
    declared = tuple(
        (normalize_reconcile_path(r.get("from"))[0], normalize_reconcile_path(r.get("to"))[0])
        if isinstance(r, dict) else (None, None)
        for r in (renames or ())
    )
    paths = list(paths or ()) + [p for pair in declared for p in pair if p]
    normalized = tuple(normalize_reconcile_path(p) for p in paths)
    refused = tuple(
        {"path": str(raw), "reason": reason}
        for raw, (_, reason) in zip(paths or (), normalized) if reason
    )
    wanted = tuple(dict.fromkeys(p for p, reason in normalized if p))
    if not wanted:
        return ReconcileResult(
            success=False, dry_run=dry_run, skipped=refused,
            error="No usable paths. Pass project-relative file paths, e.g. ['src/calc.py'].",
        )

    user_dirs, user_exts = _read_user_exclusions(get_user_preferences_db_path(root))
    excluded_dirs, excluded_exts = build_exclusion_sets(user_dirs, user_exts)
    ignore_patterns = _read_watchdogignore(root)
    excluded = tuple(
        p for p in wanted if should_exclude(p, excluded_dirs, excluded_exts, ignore_patterns)
    )
    candidates = tuple(p for p in wanted if p not in excluded)

    conn = _open_project_connection(root)
    try:
        tracked = {p: _effect_tracked_file(conn, p) for p in candidates}
        on_disk = {p: os.path.isfile(os.path.join(root, p)) for p in candidates}
        dead = tuple(p for p in candidates if tracked[p] and not on_disk[p])
        fresh = tuple(p for p in candidates if not tracked[p] and on_disk[p])
        unknown = tuple(p for p in candidates if not tracked[p] and not on_disk[p])

        explicit, rename_refusals = plan_explicit_renames(
            declared,
            lambda p: bool(tracked.get(p) or _effect_tracked_file(conn, p)),
            lambda p: os.path.isfile(os.path.join(root, p)),
        )
        claimed = {p for pair in explicit for p in pair}
        renames = explicit + pair_renames(
            tuple(p for p in dead if p not in claimed),
            tuple(p for p in fresh if p not in claimed),
        )
        rename_ids = {
            old: (tracked.get(old) or _effect_tracked_file(conn, old))["id"] for old, _ in renames
        }
        for old, new in renames:
            _effect_rename_file(conn, rename_ids[old], new)
        renamed_new = {new for _, new in renames}
        renamed_old = {old for old, _ in renames}

        unsupported = tuple(p for p in fresh if p not in renamed_new and detect_language(p) is None)
        cataloged = tuple(
            {"id": _effect_upsert_file(conn, None, p, detect_language(p))[0], "path": p}
            for p in fresh if p not in renamed_new and p not in unsupported
        )

        # Every affected file that exists and can be parsed: re-read its row
        # (renamed and new rows included — same transaction)
        affected = tuple(
            p for p in candidates
            if on_disk[p] and p not in unsupported and _effect_tracked_file(conn, p)
        )
        synced: List[Tuple[Tuple[Dict[str, Any], ...], ...]] = []
        stale: List[Dict[str, Any]] = []
        parse_skips: List[Dict[str, str]] = []
        for path in affected:
            row = _effect_tracked_file(conn, path)
            language = detect_language(path)
            source = _effect_read_source(os.path.join(root, path))
            if language is None or source is None:
                parse_skips.append({"path": path, "reason": "unreadable or unsupported language"})
                continue
            entities = select_entities(extract_entities(source, language), include_private)
            if entities.fidelity == 'none':
                parse_skips.append({"path": path, "reason": entities.parse_error or "could not parse"})
                continue
            synced.append(_effect_sync_file_entities(conn, row["id"], path, entities))
            if language == 'python':
                stale.extend(_effect_stale_calls(conn, row["id"], path, source))

        affected_set = set(affected)
        found = build_call_edges(root, query_tracked_functions(conn), lambda p: p in affected_set)
        inserted = tuple(
            _insert_interaction_if_new(conn, src, tgt, kind, desc)[1]
            for src, tgt, kind, desc in found.edges
        )

        skipped = (
            refused
            + rename_refusals
            + tuple({"path": p, "reason": "excluded by watchdog exclusions"} for p in excluded)
            + tuple({"path": p, "reason": "unsupported language"} for p in unsupported)
            + tuple({"path": p, "reason": "not tracked and not on disk"} for p in unknown)
            + tuple(parse_skips)
        )
        result = ReconcileResult(
            success=True,
            dry_run=dry_run,
            files_cataloged=cataloged,
            files_renamed=tuple(
                {"id": rename_ids[old], "from": old, "to": new} for old, new in renames
            ),
            files_missing=tuple(
                {"id": tracked[p]["id"], "path": p} for p in dead if p not in renamed_old
            ),
            functions_added=tuple(f for s in synced for f in s[0]),
            functions_missing_in_source=tuple(f for s in synced for f in s[1]),
            types_added=tuple(t for s in synced for t in s[2]),
            types_missing_in_source=tuple(t for s in synced for t in s[3]),
            interactions_added=sum(1 for new in inserted if new),
            interactions_skipped=sum(1 for new in inserted if not new),
            stale_interactions=tuple(stale),
            unresolved_calls=found.unresolved[:20],
            skipped=skipped,
            return_statements=get_return_statements("reconcile_paths"),
        )

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
            link_files_to_current_focus_effect(
                conn, tuple(c["id"] for c in cataloged) + tuple(r["id"] for r in result.files_renamed)
            )
        return result

    except Exception as exc:
        conn.rollback()
        return ReconcileResult(success=False, dry_run=dry_run, error=f"Reconcile failed: {exc}")

    finally:
        conn.close()
