"""
AIMFP Helper Functions - Interaction Audit

Compares the interactions table with the call graph the code actually makes,
for the callers in scope, and says exactly what to change.

Expected edges come from catalog.callgraph.build_call_edges: calls and value
references, credited through untracked same-file helpers, resolved through
imports (aliases and re-exports included). Each tracked edge whose caller is
in scope then lands in one category:

- missing_interactions: the code makes it, the database lacks it
- stale_interactions: a 'call' edge whose target name the caller no longer
  references at all, directly or through its untracked helpers — certain
- repoint_interactions: a stale edge paired with a missing one that is the
  same dependency moved — the call moved to another function in the same
  file (repoint the source), or the target moved files under the same name
  (repoint the target). Reported as one change instead of a delete plus an
  add, which is what a split or move really is.
- unconfirmed_interactions: the target name still appears in the caller but
  the resolver cannot confirm this target (ambiguous name, dynamic dispatch).
  A warning: mark it indirect if it is deliberate, delete it if not.
- indirect_interactions: edges marked is_indirect and every non-'call' type
  (chain, compose, pipe, borrow). Never flagged; listed for completeness.

Only full-fidelity (Python) callers are compared. Everything else in scope is
listed in unchecked_files.
"""

from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from ..utils import get_cached_project_root, get_return_statements
from ..catalog.callgraph import (
    CallEdges, build_call_edges, extract_referenced_names, fold_untracked_helpers,
)
from .code_index import (
    CodeIndex, build_code_index, build_scope_request, scope_summary,
    tracked_index_from_snapshot, unchecked_files,
)
from .reports import AuditResult, finish_audit


# ============================================================================
# Pure Helpers
# ============================================================================

def caller_paths(index: CodeIndex) -> FrozenSet[str]:
    """Pure: In-scope tracked files whose calls can be checked (full fidelity, parsed)."""
    return frozenset(
        f.path for f in index.files
        if f.fidelity == 'full' and f.file_id is not None and f.path in index.trees
    )


def function_lookup(index: CodeIndex) -> Dict[int, Dict[str, Any]]:
    """Pure: function id -> {id, name, file_id, path}."""
    paths = {row['id']: row['path'] for row in index.snapshot.files}
    return {
        fn['id']: {'id': fn['id'], 'name': fn['name'], 'file_id': fn['file_id'],
                   'path': paths.get(fn['file_id'])}
        for fn in index.snapshot.functions
    }


def folded_references(index: CodeIndex, paths: FrozenSet[str]) -> Dict[str, Dict[str, FrozenSet[str]]]:
    """
    Pure: path -> {function name -> every name it references, including inside
    the untracked same-file helpers it uses}. The certainty test for stale edges.
    """
    tracked_by_file: Dict[int, set] = {}
    for fn in index.snapshot.functions:
        tracked_by_file.setdefault(fn['file_id'], set()).add(fn['name'])
    file_ids = {row['path']: row['id'] for row in index.snapshot.files}
    return {
        path: fold_untracked_helpers(
            extract_referenced_names(index.trees[path]),
            frozenset(tracked_by_file.get(file_ids.get(path), ())),
        )
        for path in paths
    }


def edge_endpoint(fn: Optional[Dict[str, Any]], prefix: str, function_id: int) -> Dict[str, Any]:
    """Pure: {<prefix>_function_id, <prefix>, <prefix>_path} for a finding."""
    return {
        f'{prefix}_function_id': function_id,
        prefix: fn['name'] if fn else None,
        f'{prefix}_path': fn['path'] if fn else None,
    }


def classify_interactions(
    index: CodeIndex,
    expected: CallEdges,
) -> Dict[str, Tuple[Dict[str, Any], ...]]:
    """
    Pure: Sort every tracked edge of the in-scope callers, and every expected
    edge, into the audit categories (repoints not yet paired).

    Returns:
        {missing, stale, unconfirmed, indirect, orphaned} finding tuples
    """
    callers = caller_paths(index)
    functions = function_lookup(index)
    in_scope = {fid for fid, fn in functions.items() if fn['path'] in callers}
    references = folded_references(index, callers)

    expected_map = {
        (src, tgt): (desc, via)
        for (src, tgt, _kind, desc), via in zip(expected.edges, expected.edge_via or ('call',) * len(expected.edges))
        if src in in_scope
    }
    tracked = tuple(e for e in index.snapshot.interactions if e['source_function_id'] in in_scope)
    present = {(e['source_function_id'], e['target_function_id']) for e in tracked}

    missing = tuple(
        {**edge_endpoint(functions.get(src), 'source', src),
         **edge_endpoint(functions.get(tgt), 'target', tgt),
         'interaction_type': 'call', 'description': desc, 'via': via}
        for (src, tgt), (desc, via) in sorted(expected_map.items())
        if (src, tgt) not in present
    )

    stale: List[Dict[str, Any]] = []
    unconfirmed: List[Dict[str, Any]] = []
    indirect: List[Dict[str, Any]] = []
    orphaned: List[Dict[str, Any]] = []
    for edge in tracked:
        pair = (edge['source_function_id'], edge['target_function_id'])
        source = functions.get(pair[0])
        target = functions.get(pair[1])
        finding = {
            'interaction_id': edge['id'],
            **edge_endpoint(source, 'source', pair[0]),
            **edge_endpoint(target, 'target', pair[1]),
            'interaction_type': edge['interaction_type'],
            'description': edge['description'],
        }
        if edge['interaction_type'] != 'call' or edge['is_indirect']:
            indirect.append({'interaction_id': edge['id'], 'source': finding['source'],
                             'target': finding['target'], 'interaction_type': edge['interaction_type'],
                             'is_indirect': bool(edge['is_indirect'])})
            continue
        if pair in expected_map:
            continue
        body = references.get(source['path'], {}).get(source['name']) if source else None
        if body is None:
            # The caller itself is gone from the source: audit_functions reports
            # it as stale, and deleting it removes this edge with it.
            orphaned.append(finding)
        elif target is None or target['name'] not in body:
            stale.append(finding)
        else:
            unconfirmed.append({**finding, 'reason': (
                'target name still referenced but resolves elsewhere or ambiguously; '
                'mark is_indirect if deliberate, delete if not')})

    return {
        'missing': missing,
        'stale': tuple(stale),
        'unconfirmed': tuple(unconfirmed),
        'indirect': tuple(indirect),
        'orphaned': tuple(orphaned),
    }


def pair_repoints(
    stale: Tuple[Dict[str, Any], ...],
    missing: Tuple[Dict[str, Any], ...],
    source_moves: bool = True,
) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[Dict[str, Any], ...], Tuple[Dict[str, Any], ...]]:
    """
    Pure: Pair outdated edges with missing edges that are the same dependency moved.

    An outdated (S -> T) pairs with a missing (S' -> T) when S' lives in S's
    file (the call moved into another function: repoint the source), else with
    a missing (S -> T') when T' has T's name (the target moved: repoint the
    target). Each edge pairs at most once, lowest ids first, so applying every
    repoint plus the remaining missing and stale findings reproduces the code's
    graph exactly.

    Args:
        stale: Outdated edges (stale, or unconfirmed for target moves)
        missing: Edges the code makes that the database lacks
        source_moves: False pairs target moves only — for unconfirmed edges,
            whose caller still names the target (so the caller did not change)

    Returns:
        (repoints, outdated left, missing left)
    """
    used: set = set()
    repoints: List[Dict[str, Any]] = []
    left_stale: List[Dict[str, Any]] = []

    for edge in sorted(stale, key=lambda e: e['interaction_id']):
        source_match = next((
            i for i, m in enumerate(missing)
            if i not in used
            and m['target_function_id'] == edge['target_function_id']
            and m['source_path'] == edge['source_path']
        ), None) if source_moves else None
        target_match = None if source_match is not None else next((
            i for i, m in enumerate(missing)
            if i not in used
            and m['source_function_id'] == edge['source_function_id']
            and m['target'] == edge['target']
        ), None)
        match = source_match if source_match is not None else target_match
        if match is None:
            left_stale.append(edge)
            continue
        used.add(match)
        moved = missing[match]
        change = 'source' if source_match is not None else 'target'
        repoints.append({
            'interaction_id': edge['interaction_id'],
            'change': change,
            'from_function_id': edge[f'{change}_function_id'],
            'to_function_id': moved[f'{change}_function_id'],
            'source': edge['source'], 'target': edge['target'],
            f'new_{change}': moved[change],
            'path': moved[f'{change}_path'],
            'source_function_id': moved['source_function_id'],
            'target_function_id': moved['target_function_id'],
            'expect_source_function_id': edge['source_function_id'],
            'expect_target_function_id': edge['target_function_id'],
            'description': moved['description'],
        })

    left_missing = tuple(m for i, m in enumerate(missing) if i not in used)
    return (tuple(repoints), tuple(left_stale), left_missing)


def interaction_findings(index: CodeIndex, expected: CallEdges) -> Dict[str, Tuple[Any, ...]]:
    """Pure: Every interaction audit category, repoints paired."""
    found = classify_interactions(index, expected)
    repoints, stale, missing = pair_repoints(found['stale'], found['missing'])
    # A target that moved files keeps its name in the caller, so its old edge
    # is unconfirmed rather than stale: pair those as target moves too
    moved, unconfirmed, missing = pair_repoints(found['unconfirmed'], missing, source_moves=False)
    return {
        'repoint_interactions': repoints + moved,
        'missing_interactions': missing,
        'stale_interactions': stale,
        'unconfirmed_interactions': unconfirmed,
        'indirect_interactions': found['indirect'],
        'unresolved_calls': tuple({'call': c} for c in expected.unresolved),
        'unchecked_files': unchecked_files(index),
        'skipped_paths': index.scope.skipped,
    }


# ============================================================================
# Effects
# ============================================================================

def expected_edges(index: CodeIndex) -> CallEdges:
    """Effect (reads no files: trees are already parsed): the code's graph for in-scope callers."""
    callers = caller_paths(index)
    return build_call_edges(
        index.project_root,
        tracked_index_from_snapshot(index.snapshot),
        lambda path: path in callers,
        trees=index.trees,
    )


def run_interaction_audit(index: CodeIndex) -> Dict[str, Tuple[Any, ...]]:
    """Effect: Interaction findings for an already-built index (used by audit_tracking)."""
    return interaction_findings(index, expected_edges(index))


# ============================================================================
# Public Tool
# ============================================================================

def audit_interactions(
    file_ids: Optional[List[int]] = None,
    paths: Optional[List[str]] = None,
    since_commit: Optional[str] = None,
    task_id: Optional[int] = None,
    task_type: Optional[str] = None,
    module_path: Optional[str] = None,
    project_root: Optional[str] = None,
) -> AuditResult:
    """
    Compare tracked interactions with the call graph the code makes. Read-only.

    Scope selectors combine as a union; with none, the whole project is audited.

    Args:
        file_ids: Tracked file IDs to audit
        paths: Project-relative file paths to audit
        since_commit: Audit files changed since this git ref (e.g. 'HEAD', 'main')
        task_id: Audit the files linked to this task (and its subtasks)
        task_type: 'task' (default), 'subtask' or 'sidequest' for task_id
        module_path: Audit every file under this project-relative directory
        project_root: Explicit root for embedding hosts (defaults to the session root)

    Returns:
        AuditResult: counts, samples, block/warn verdict, and report_path to the
        full report (pass it to apply_audit_fixes)
    """
    root = project_root or get_cached_project_root()
    try:
        index, error = build_code_index(root, build_scope_request(
            file_ids, paths, since_commit, task_id, task_type, module_path))
        if index is None:
            return AuditResult(success=False, error=error)
        expected = expected_edges(index)
        return finish_audit(
            root, 'audit_interactions', scope_summary(index),
            interaction_findings(index, expected),
            statements=get_return_statements("audit_interactions"),
            extra={'resolved_by': expected.resolved_by},
        )
    except Exception as exc:
        return AuditResult(success=False, error=f"Interaction audit failed: {exc}")
