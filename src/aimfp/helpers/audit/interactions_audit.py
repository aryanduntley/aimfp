"""
AIMFP Helper Functions - Interaction Audit

Compares the interactions table with the call graph the code actually makes,
for the callers in scope, and says exactly what to change.

Expected edges come from catalog.callgraph.build_call_edges: calls and value
references, credited through untracked same-file helpers, resolved through
imports (aliases and re-exports included). Each tracked edge whose caller is
in scope then lands in one category:

- missing_interactions: the code makes it, the database lacks it
- stale_interactions: a 'call' edge the code provably no longer makes — every
  use of the target's name in the caller (and its untracked helpers)
  resolves to something else: another function, a local, a method on a
  value, or nothing at all
- repoint_interactions: a stale edge paired with a missing one that is the
  same dependency moved — the call moved to another function in the same
  file (repoint the source), or the target moved files under the same name
  (repoint the target). Reported as one change instead of a delete plus an
  add, which is what a split or move really is.
- unconfirmed_interactions: the code may still make the edge but the audit
  cannot prove it: the name resolves ambiguously, the caller calls through a
  record field the target is stored under elsewhere, the target is named in
  a string, or the target lives in a non-Python file. A warning: mark it
  indirect if it is deliberate, delete it if not.
- indirect_interactions: edges marked is_indirect (deliberate, never flagged)
- non_call_interactions: chain / compose / pipe / borrow edges, which describe
  data flow rather than a call and are never compared with the code. Both are
  informational, not findings.

Confirmed without being expected (never reported, never asked to be added):
self-recursion, an edge to a function held in a module-level table the
caller reads (dispatch), and a call to a non-Python function written as
``name(`` in one of the caller's string literals (SQL).

Only full-fidelity (Python) callers are compared. Everything else in scope is
listed in unchecked_files.
"""

import ast
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from ..utils import get_cached_project_root, get_return_statements
from ...watchdog.config import detect_language
from ..catalog.callgraph import (
    CallEdges, DispatchEvidence, build_call_edges, dispatch_evidence, extract_bound_calls,
    extract_referenced_names, extract_string_literals, fold_untracked_helpers,
    reachable_untracked_helpers, resolution_context,
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


def tracked_names_by_path(index: CodeIndex, paths: FrozenSet[str]) -> Dict[str, FrozenSet[str]]:
    """Pure: path -> names of the file's tracked functions."""
    tracked_by_file: Dict[int, set] = {}
    for fn in index.snapshot.functions:
        tracked_by_file.setdefault(fn['file_id'], set()).add(fn['name'])
    file_ids = {row['path']: row['id'] for row in index.snapshot.files}
    return {path: frozenset(tracked_by_file.get(file_ids.get(path), ())) for path in paths}


def folded_references(index: CodeIndex, paths: FrozenSet[str]) -> Dict[str, Dict[str, FrozenSet[str]]]:
    """
    Pure: path -> {function name -> every name it references, including inside
    the untracked same-file helpers it uses}. A caller missing here is gone
    from the source (its edges are orphaned, not stale).
    """
    tracked = tracked_names_by_path(index, paths)
    return {
        path: fold_untracked_helpers(extract_referenced_names(index.trees[path], tracked[path]), tracked[path])
        for path in paths
    }


PerFunction = Callable[[ast.Module, FrozenSet[str]], Dict[str, Iterable[str]]]


def folded_per_function(
    index: CodeIndex,
    paths: FrozenSet[str],
    extract: PerFunction,
) -> Dict[str, Dict[str, FrozenSet[str]]]:
    """
    Pure: path -> {function name -> what `extract` finds in it and in the
    untracked same-file helpers it reaches}.

    Args:
        extract: (tree, tracked names) -> {function name -> items}, e.g.
            extract_string_literals or extract_bound_calls
    """
    tracked = tracked_names_by_path(index, paths)
    folded: Dict[str, Dict[str, FrozenSet[str]]] = {}
    for path in paths:
        tree, names = index.trees[path], tracked[path]
        found = extract(tree, names)
        reach = reachable_untracked_helpers(extract_referenced_names(tree, names), names)
        folded[path] = {
            name: frozenset(own).union(*(found.get(h, ()) for h in reach.get(name, ())))
            for name, own in found.items()
        }
    return folded


def calls_in_strings(name: str, strings: FrozenSet[str]) -> bool:
    """Pure: Whether any string calls `name` (``name(`` not preceded by a word or '.')."""
    pattern = re.compile(rf'(?<![\w.]){re.escape(name)}\s*\(')
    return any(pattern.search(text) for text in strings)


def is_python_path(path: Optional[str]) -> bool:
    """Pure: Whether a tracked file is Python (the only language calls are resolved in)."""
    return detect_language(path or '') == 'python'


def confirmed_without_call(
    pair: Tuple[int, int],
    target: Optional[Dict[str, Any]],
    expected: CallEdges,
    dispatch: DispatchEvidence,
    strings: FrozenSet[str],
) -> bool:
    """
    Pure: Whether the code makes a tracked edge that is not an expected edge:
    recursion, a dispatch table the caller reads, or a non-Python function
    the caller calls in a string literal (SQL).
    """
    if pair[0] == pair[1]:
        return pair[0] in expected.self_edges
    if pair in dispatch.table_edges:
        return True
    return target is not None and not is_python_path(target['path']) and calls_in_strings(target['name'], strings)


def unproven_reason(
    source_id: int,
    target: Dict[str, Any],
    expected: CallEdges,
    dispatch: DispatchEvidence,
    strings: FrozenSet[str],
    bound_calls: FrozenSet[str] = frozenset(),
) -> Optional[str]:
    """
    Pure: Why a tracked edge the resolver did not find may still be real, or
    None when it provably is not (stale).

    Args:
        strings: String literals of the caller (and its untracked helpers)
        bound_calls: Names the caller calls that it binds itself (callbacks)
    """
    name = target['name']
    if name in expected.loose_names.get(source_id, ()):
        return (f"the caller uses '{name}' but it resolves ambiguously (several modules match); "
                "mark is_indirect if deliberate, delete if not")
    if name in bound_calls:
        return (f"the caller calls a parameter named '{name}' (a callback the target is passed in as); "
                "mark is_indirect if deliberate, delete if not")
    if not is_python_path(target['path']):
        return (f"target is in a non-Python file and no string in the caller calls {name}(...); "
                "verify by reading, then mark is_indirect or delete")
    fields = dispatch.value_fields.get(target['id'], frozenset()) & expected.value_calls.get(source_id, frozenset())
    if fields:
        return (f"the caller calls {', '.join('.' + f + '()' for f in sorted(fields))} on a value, and the "
                "target is stored under that field elsewhere; mark is_indirect if deliberate")
    if name in strings:
        return (f"the caller names '{name}' in a string (getattr / registry lookup); "
                "mark is_indirect if deliberate, delete if not")
    return None


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
    dispatch: DispatchEvidence = DispatchEvidence(),
) -> Dict[str, Tuple[Dict[str, Any], ...]]:
    """
    Pure: Sort every tracked edge of the in-scope callers, and every expected
    edge, into the audit categories (repoints not yet paired).

    Returns:
        {missing, stale, unconfirmed, indirect, non_call, orphaned} finding tuples
    """
    callers = caller_paths(index)
    functions = function_lookup(index)
    in_scope = {fid for fid, fn in functions.items() if fn['path'] in callers}
    references = folded_references(index, callers)
    strings = folded_per_function(index, callers, extract_string_literals)
    bound_calls = folded_per_function(index, callers, extract_bound_calls)

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
    non_call: List[Dict[str, Any]] = []
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
            (indirect if edge['is_indirect'] else non_call).append({
                'interaction_id': edge['id'], 'source': finding['source'],
                'target': finding['target'], 'interaction_type': edge['interaction_type']})
            continue
        if pair in expected_map:
            continue
        body = references.get(source['path'], {}).get(source['name']) if source else None
        if body is None:
            # The caller itself is gone from the source: audit_functions reports
            # it as stale, and deleting it removes this edge with it.
            orphaned.append(finding)
            continue
        said = strings.get(source['path'], {}).get(source['name'], frozenset())
        if confirmed_without_call(pair, target, expected, dispatch, said):
            continue
        callbacks = bound_calls.get(source['path'], {}).get(source['name'], frozenset())
        reason = unproven_reason(pair[0], target, expected, dispatch, said, callbacks) if target else None
        if reason is None:
            stale.append(finding)
        else:
            unconfirmed.append({**finding, 'reason': reason})

    return {
        'missing': missing,
        'stale': tuple(stale),
        'unconfirmed': tuple(unconfirmed),
        'indirect': tuple(indirect),
        'non_call': tuple(non_call),
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


def interaction_findings(
    index: CodeIndex,
    expected: CallEdges,
    dispatch: DispatchEvidence = DispatchEvidence(),
) -> Dict[str, Tuple[Any, ...]]:
    """Pure: Every interaction audit category, repoints paired."""
    found = classify_interactions(index, expected, dispatch)
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
        'non_call_interactions': found['non_call'],
        'unresolved_calls': tuple({'call': c} for c in expected.unresolved),
        'unchecked_files': unchecked_files(index),
        'skipped_paths': index.scope.skipped,
    }


# ============================================================================
# Effects
# ============================================================================

@dataclass(frozen=True)
class CodeGraph:
    """The code's graph for the in-scope callers: resolved edges plus dispatch evidence."""
    edges: CallEdges
    dispatch: DispatchEvidence


def code_graph(index: CodeIndex) -> CodeGraph:
    """Effect (reads no files: trees are already parsed): the code's graph for in-scope callers."""
    callers = caller_paths(index)
    tracked = tracked_index_from_snapshot(index.snapshot)
    context = resolution_context(tracked, index.trees)

    def include(path: str) -> bool:
        return path in callers

    return CodeGraph(
        edges=build_call_edges(index.project_root, tracked, include, trees=index.trees, context=context),
        dispatch=dispatch_evidence(context, index.trees, include),
    )


def expected_edges(index: CodeIndex) -> CallEdges:
    """Effect (reads no files): the resolved edges of code_graph alone."""
    return code_graph(index).edges


def run_interaction_audit(index: CodeIndex) -> Dict[str, Tuple[Any, ...]]:
    """Effect: Interaction findings for an already-built index (used by audit_tracking)."""
    graph = code_graph(index)
    return interaction_findings(index, graph.edges, graph.dispatch)


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
        graph = code_graph(index)
        return finish_audit(
            root, 'audit_interactions', scope_summary(index),
            interaction_findings(index, graph.edges, graph.dispatch),
            statements=get_return_statements("audit_interactions"),
            extra={'resolved_by': graph.edges.resolved_by},
        )
    except Exception as exc:
        return AuditResult(success=False, error=f"Interaction audit failed: {exc}")
