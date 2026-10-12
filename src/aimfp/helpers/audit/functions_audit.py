"""
AIMFP Helper Functions - Function, Type and File Audit

Checks that every in-scope file, function and type in the source is tracked,
that every tracked one still exists, and that what is recorded about it is
complete and matches the code.

Per file, by fidelity:
- reserved, never finalized: reserved_files (never reported as missing mid-flow)
- missing (tracked, gone from disk): missing_files
- untracked file: untracked_files (reconcile_paths catalogs it)
- full (Python, ast):
    missing_functions      public functions in the source, not tracked
    stale_functions        tracked functions the source no longer defines (a
                           module-level `f = partial(...)` / lambda / alias
                           binding of the name counts as a definition)
    private_tracked        tracked _private functions (informational only)
    incomplete_functions   purpose / parameters / returns not populated
    malformed_json         parameters / returns not stored as one JSON encoding
                           of a list / object (double-encoded is fixable)
    reserved_functions     still is_reserved: never finalized
    param_mismatch         recorded parameters disagree with the signature
                           (names, order, required vs default)
    missing_types          public data types in the source, not tracked
    stale_types            tracked types whose name the file defines nowhere
    incomplete_types       no description, or definition_json not an object
    missing_type_links     a tracked type named in a function's signature with
                           no types_functions link (role suggested)
    files_without_functions tracked files that define no functions (info)
- names_only (regex languages): names_only_missing, a warning, since regex
  extraction misses some forms. Nothing stale is ever claimed from it.

Untracked _private functions are never reported: tracking them is optional.
"""

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from ..utils import decode_json_layers, get_cached_project_root, get_return_statements
from ..catalog.callgraph import (
    assigned_callables, extract_import_bindings, extract_import_origins, module_suffix_index,
    resolution_context, resolve_in, resolve_target,
)
from ..catalog.extract import ExtractedFunction, parameter_mismatches
from .code_index import (
    CodeIndex, FileFacts, build_code_index, build_scope_request, scope_summary,
    tracked_index_from_snapshot, unchecked_files,
)
from .exemptions import exempt_index_findings
from .reports import AuditResult, finish_audit


_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_BOOLISH = frozenset({'bool', 'Result', 'Maybe'})


# ============================================================================
# Pure Helpers
# ============================================================================

def json_encoding_problem(raw: Optional[str], expect: str) -> Optional[Tuple[str, bool]]:
    """
    Pure: (problem, fixable) when a JSON column is not one encoding of the
    expected shape, else None. Only double encoding is mechanically fixable.

    Args:
        raw: Stored column text (None is incompleteness, not malformation)
        expect: 'list' or 'object'
    """
    if raw is None:
        return None
    try:
        first = json.loads(raw)
    except (TypeError, ValueError):
        return ('invalid JSON', False)
    if isinstance(first, str):
        inner = decode_json_layers(first)
        if not isinstance(inner, str):
            return ('double-encoded', True)
        return (f'stored as a JSON string, expected a JSON {expect}', False)
    if expect == 'list' and not isinstance(first, list):
        return ('not a JSON list', False)
    if expect == 'object' and not isinstance(first, dict):
        return ('not a JSON object', False)
    return None


def top_level_names(tree: Optional[ast.Module]) -> FrozenSet[str]:
    """Pure: Every name a module binds at top level (defs, classes, assignments, imports)."""
    if tree is None:
        return frozenset()
    names: set = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split('.')[0] for a in node.names)
        elif type(node).__name__ == 'TypeAlias':
            names.add(getattr(node.name, 'id', ''))
    return frozenset(names)


def suggest_role(in_params: bool, in_return: bool, return_annotation: Optional[str]) -> str:
    """
    Pure: The types_functions role a signature suggests: factory (returns it),
    transformer (takes and returns it), validator (takes it, returns a
    bool/Result), accessor (takes it).
    """
    if in_return:
        return 'transformer' if in_params else 'factory'
    returns = set(_IDENTIFIER.findall(return_annotation or ''))
    return 'validator' if returns & _BOOLISH else 'accessor'


def signature_type_names(fn: ExtractedFunction) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """Pure: (identifiers in parameter annotations, identifiers in the return annotation)."""
    in_params = frozenset(
        token for p in fn.parameters for token in _IDENTIFIER.findall(p.get('type') or '')
    )
    in_return = frozenset(_IDENTIFIER.findall((fn.returns or {}).get('type') or ''))
    return (in_params, in_return)


def function_findings(
    facts: FileFacts,
    rows: Tuple[Dict[str, Any], ...],
    assigned: FrozenSet[str] = frozenset(),
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Pure: Function-level findings for one full-fidelity tracked file.

    A tracked function bound by assignment (``check = partial(_check, kind='a')``,
    a lambda, an alias) exists: it is never stale. It has no signature of its
    own to compare, so only its recorded fields are checked.

    Args:
        facts: The file as the source defines it
        rows: Tracked function rows of the file
        assigned: Module-level names the file binds to a callable-shaped value
            (callgraph.assigned_callables)
    """
    defined = {fn.name: fn for fn in facts.entities.functions}
    tracked = {row['name']: row for row in rows}
    where = {'file_id': facts.file_id, 'path': facts.path}
    out: Dict[str, List[Dict[str, Any]]] = {}

    def add(category: str, item: Dict[str, Any]) -> None:
        out.setdefault(category, []).append(item)

    for fn in facts.entities.functions:
        if fn.name not in tracked and not fn.is_private:
            add('missing_functions', {**where, 'name': fn.name, 'line': fn.line})

    for row in rows:
        ident = {'function_id': row['id'], 'name': row['name'], **where}
        if row['name'] not in defined and row['name'] not in assigned:
            add('stale_functions', ident)
            continue
        if row['name'].startswith('_'):
            add('private_tracked', ident)
        if row['is_reserved']:
            add('reserved_functions', ident)
        empty = tuple(
            name for name, value in (('purpose', row['purpose']), ('parameters', row['parameters']),
                                     ('returns', row['returns']))
            if value is None or (isinstance(value, str) and not value.strip())
        )
        if empty:
            add('incomplete_functions', {**ident, 'fields': list(empty)})
        for column, expect in (('parameters', 'list'), ('returns', 'object')):
            problem = json_encoding_problem(row[column], expect)
            if problem:
                add('malformed_json', {'table': 'functions', 'column': column, 'id': row['id'],
                                       'name': row['name'], 'path': facts.path,
                                       'problem': problem[0], 'fixable': problem[1]})
        if row['parameters'] is not None and row['name'] in defined:
            problems = parameter_mismatches(decode_json_layers(row['parameters']), defined[row['name']].parameters)
            if problems:
                add('param_mismatch', {**ident, 'problems': list(problems),
                                       'code': [p['name'] for p in defined[row['name']].parameters]})
    return out


def type_findings(
    facts: FileFacts,
    rows: Tuple[Dict[str, Any], ...],
    defined_names: FrozenSet[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Pure: Type-level findings for one full-fidelity tracked file.

    A tracked type is stale only when the file binds its name nowhere: type
    aliases and plain classes are not recognized as data types by extraction,
    yet are legitimately tracked.
    """
    tracked = {row['name'] for row in rows}
    where = {'file_id': facts.file_id, 'path': facts.path}
    out: Dict[str, List[Dict[str, Any]]] = {}

    def add(category: str, item: Dict[str, Any]) -> None:
        out.setdefault(category, []).append(item)

    for ty in facts.entities.types:
        if ty.name not in tracked and not ty.is_private:
            add('missing_types', {**where, 'name': ty.name, 'line': ty.line, 'kind': ty.kind})
    for row in rows:
        ident = {'type_id': row['id'], 'name': row['name'], **where}
        if row['name'] not in defined_names:
            add('stale_types', ident)
            continue
        problem = json_encoding_problem(row['definition_json'], 'object')
        if problem and problem[1]:
            add('malformed_json', {'table': 'types', 'column': 'definition_json', 'id': row['id'],
                                   'name': row['name'], 'path': facts.path,
                                   'problem': problem[0], 'fixable': True})
        empty = tuple(
            name for name, bad in (
                ('description', not (row['description'] or '').strip()),
                ('definition_json', problem is not None and not problem[1]),
            ) if bad
        )
        if empty:
            add('incomplete_types', {**ident, 'fields': list(empty)})
    return out


@dataclass(frozen=True)
class TypeLinkContext:
    """Lookup tables for resolving type names in signatures, built once per index."""
    type_by_file: Dict[str, Dict[str, int]]
    type_by_name: Dict[str, List[int]]
    type_names: Dict[int, str]
    linked: FrozenSet[Tuple[int, int]]
    bindings_index: Dict[str, Dict[str, str]]
    origins_index: Dict[str, Dict[str, str]]
    module_index: Dict[str, Tuple[str, ...]]


def type_link_context(index: CodeIndex) -> TypeLinkContext:
    """Pure: Every table type_link_findings needs, computed once for the whole audit."""
    paths = {row['id']: row['path'] for row in index.snapshot.files}
    type_by_file: Dict[str, Dict[str, int]] = {}
    type_by_name: Dict[str, List[int]] = {}
    for ty in index.snapshot.types:
        path = paths.get(ty['file_id'])
        if path:
            type_by_file.setdefault(path, {})[ty['name']] = ty['id']
        type_by_name.setdefault(ty['name'], []).append(ty['id'])
    return TypeLinkContext(
        type_by_file=type_by_file,
        type_by_name=type_by_name,
        type_names={ty['id']: ty['name'] for ty in index.snapshot.types},
        linked=frozenset((link['type_id'], link['function_id']) for link in index.snapshot.types_functions),
        bindings_index={p: extract_import_bindings(t, p) for p, t in index.trees.items()},
        origins_index={p: extract_import_origins(t) for p, t in index.trees.items()},
        module_index=module_suffix_index(tuple(paths.values())),
    )


def type_link_findings(
    context: TypeLinkContext,
    facts: FileFacts,
    function_rows: Tuple[Dict[str, Any], ...],
) -> List[Dict[str, Any]]:
    """
    Pure: missing_type_links for one file: each tracked function whose
    signature names a tracked type (resolved through this file's imports, the
    file itself, or a project-unique name) with no types_functions link.
    """
    if not context.type_by_name:
        return []
    defined = {fn.name: fn for fn in facts.entities.functions}
    found: List[Dict[str, Any]] = []
    for row in function_rows:
        fn = defined.get(row['name'])
        if fn is None:
            continue
        in_params, in_return = signature_type_names(fn)
        for token in sorted((in_params | in_return) & set(context.type_by_name)):
            type_id, _ = resolve_target(
                token, None, facts.path, context.bindings_index.get(facts.path, {}),
                context.type_by_file, context.type_by_name, context.bindings_index,
                origins=context.origins_index.get(facts.path, {}), origins_index=context.origins_index,
                module_index=context.module_index,
            )
            if type_id is None or (type_id, row['id']) in context.linked:
                continue
            found.append({
                'type_id': type_id, 'type': context.type_names[type_id],
                'function_id': row['id'], 'function': row['name'], 'path': facts.path,
                'role': suggest_role(token in in_params, token in in_return, (fn.returns or {}).get('type')),
            })
    return found


def audit_function_findings(index: CodeIndex) -> Dict[str, Tuple[Any, ...]]:
    """Pure: Every function/type/file category for an index."""
    functions_by_file: Dict[int, List[Dict[str, Any]]] = {}
    for row in index.snapshot.functions:
        functions_by_file.setdefault(row['file_id'], []).append(row)
    types_by_file: Dict[int, List[Dict[str, Any]]] = {}
    for row in index.snapshot.types:
        types_by_file.setdefault(row['file_id'], []).append(row)

    links = type_link_context(index)
    collected: Dict[str, List[Dict[str, Any]]] = {}

    def merge(found: Dict[str, List[Dict[str, Any]]]) -> None:
        for category, items in found.items():
            collected.setdefault(category, []).extend(items)

    reserved = {row['id'] for row in index.snapshot.files if row.get('is_reserved')}

    for facts in index.files:
        if facts.file_id in reserved:
            # Reserved but never finalized: not "missing" mid-flow, but unfinished tracking
            merge({'reserved_files': [{'file_id': facts.file_id, 'path': facts.path,
                                       'on_disk': facts.fidelity != 'missing'}]})
        if facts.fidelity == 'missing':
            if facts.file_id is not None and facts.file_id not in reserved:
                merge({'missing_files': [{'file_id': facts.file_id, 'path': facts.path}]})
            continue
        if facts.file_id is None:
            merge({'untracked_files': [{'path': facts.path, 'fidelity': facts.fidelity,
                                        'functions': len(facts.entities.functions)}]})
            continue
        function_rows = tuple(functions_by_file.get(facts.file_id, ()))
        if facts.fidelity == 'names_only':
            tracked = {row['name'] for row in function_rows}
            merge({'names_only_missing': [
                {'file_id': facts.file_id, 'path': facts.path, 'name': fn.name, 'line': fn.line}
                for fn in facts.entities.functions if fn.name not in tracked and not fn.is_private
            ]})
            continue
        if facts.fidelity != 'full':
            continue
        tree = index.trees.get(facts.path)
        merge(function_findings(facts, function_rows,
                                frozenset(assigned_callables(tree)) if tree is not None else frozenset()))
        merge(type_findings(facts, tuple(types_by_file.get(facts.file_id, ())),
                            top_level_names(index.trees.get(facts.path))))
        merge({'missing_type_links': type_link_findings(links, facts, function_rows)})
        if not facts.entities.functions and not function_rows:
            merge({'files_without_functions': [{'file_id': facts.file_id, 'path': facts.path}]})

    order = ('missing_files', 'reserved_files', 'untracked_files', 'missing_functions', 'stale_functions',
             'reserved_functions', 'incomplete_functions', 'malformed_json', 'param_mismatch',
             'missing_types', 'stale_types', 'incomplete_types', 'missing_type_links',
             'names_only_missing', 'private_tracked', 'files_without_functions')
    collected['stale_functions'] = list(with_move_targets(index, tuple(collected.get('stale_functions', ()))))
    return exempt_index_findings(index, {
        **{category: tuple(collected.get(category, ())) for category in order},
        'unchecked_files': tuple(u for u in unchecked_files(index) if u['file_id'] is not None),
        'skipped_paths': index.scope.skipped,
    })


def with_move_targets(index: CodeIndex, stale: Tuple[Dict[str, Any], ...]) -> Tuple[Dict[str, Any], ...]:
    """
    Pure: Stale functions, each that moved marked moved_to {function_id, path}:
    its old file now imports the name from a file where a function of that
    name is tracked. Applying stale_functions then moves its edges and type
    links to the successor (delete_function repoint_to) instead of dropping them.
    """
    if not stale:
        return stale
    context = resolution_context(tracked_index_from_snapshot(index.snapshot), index.trees)

    def successor(item: Dict[str, Any]) -> Optional[int]:
        found, reason = resolve_in(context, item['path'], item['name'], None)
        return found if found != item['function_id'] and reason in ('import', 'reexport') else None

    return tuple(
        {**item, 'moved_to': {'function_id': moved, 'path': context.id_to_path.get(moved)}}
        if (moved := successor(item)) is not None else item
        for item in stale
    )


def reconcile_targets(findings: Dict[str, Tuple[Any, ...]]) -> Tuple[str, ...]:
    """Pure: Paths reconcile_paths should be given to catalog what is missing."""
    return tuple(sorted({
        item['path']
        for category in ('untracked_files', 'missing_functions', 'missing_types')
        for item in findings.get(category, ())
    }))


# ============================================================================
# Public Tool
# ============================================================================

def run_function_audit(index: CodeIndex) -> Dict[str, Tuple[Any, ...]]:
    """Effect-free wrapper for audit_tracking: function findings for a built index."""
    return audit_function_findings(index)


def audit_functions(
    file_ids: Optional[List[int]] = None,
    paths: Optional[List[str]] = None,
    since_commit: Optional[str] = None,
    task_id: Optional[int] = None,
    task_type: Optional[str] = None,
    module_path: Optional[str] = None,
    project_root: Optional[str] = None,
) -> AuditResult:
    """
    Audit tracked files, functions and types against the source. Read-only.

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
        full report (pass it to apply_audit_fixes; its reconcile_paths list
        catalogs what is missing)
    """
    root = project_root or get_cached_project_root()
    try:
        index, error = build_code_index(root, build_scope_request(
            file_ids, paths, since_commit, task_id, task_type, module_path))
        if index is None:
            return AuditResult(success=False, error=error)
        findings = audit_function_findings(index)
        return finish_audit(
            root, 'audit_functions', scope_summary(index), findings,
            statements=get_return_statements("audit_functions"),
            extra={'reconcile_paths': list(reconcile_targets(findings))},
        )
    except Exception as exc:
        return AuditResult(success=False, error=f"Function audit failed: {exc}")
