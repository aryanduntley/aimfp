"""
AIMFP Helper Functions - Description Audit

Prose drifts silently: a function is reworked and its purpose still describes
what it used to do. This audit finds two kinds of drift no field check can see.

- stale_descriptions: functions.source_hash records the code a description was
  written for (set whenever purpose/parameters/returns are written). When the
  function's current code hashes differently, the description predates the
  change and needs a look. Formatting, comments and docstring edits never
  change the hash.
- dangling_mentions: a description naming a callable that exists nowhere —
  ``per_file() adapts one-file parsers`` after per_file was removed. A mention
  is a name followed by '(' that either contains an underscore or is written
  '()'. Known names: every function/class defined or imported in any tracked
  Python file, every tracked function and type, and Python builtins.
  Attribute calls (``os.path.join()``), ALLCAPS (SQL) and plurals
  (``file(s)``) are never mentions.

unbaselined_descriptions (info) lists described functions with no hash yet —
written before schema 1.15 or by a path that could not read the code. Review
their prose, then apply_audit_fixes(apply=['unbaselined_descriptions']).
"""

import ast
import builtins
import re
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from ..utils import decode_json_layers, get_cached_project_root, get_return_statements
from .code_index import (
    CodeIndex, build_code_index, build_scope_request, scope_summary, unchecked_files,
)
from .reports import AuditResult, finish_audit


_MENTION = re.compile(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\((\)|s\)|es\))?')


# ============================================================================
# Pure Helpers
# ============================================================================

def mentioned_callables(text: Optional[str]) -> Tuple[str, ...]:
    """
    Pure: Callable names a piece of prose mentions, in order, deduplicated.

    A mention is ``name(`` not preceded by '.' or a word character, where the
    name contains an underscore or is written ``name()``. ALLCAPS names and
    plural markers (``file(s)``) are not mentions.
    """
    found: List[str] = []
    for match in _MENTION.finditer(text or ''):
        name, closer = match.group(1), match.group(2)
        if closer in ('s)', 'es)') or name.isupper():
            continue
        if '_' in name.strip('_') or closer == ')':
            found.append(name)
    return tuple(dict.fromkeys(found))


def defined_and_imported_names(trees: Iterable[ast.Module]) -> FrozenSet[str]:
    """
    Pure: Every callable name the given modules define, import, or call as an
    attribute (``sqlite3.connect(...)`` makes 'connect' known).
    """
    names: set = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.update(part for part in (alias.name.split('.')[-1], alias.asname) if part)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return frozenset(names)


def known_names(index: CodeIndex) -> FrozenSet[str]:
    """Pure: Names a description may legitimately mention as callables."""
    return (
        defined_and_imported_names(index.trees.values())
        | frozenset(fn['name'] for fn in index.snapshot.functions)
        | frozenset(ty['name'] for ty in index.snapshot.types)
        | frozenset(dir(builtins))
    )


def description_texts(row: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """Pure: (field, text) for a function's purpose and every parameter/returns description."""
    params = decode_json_layers(row.get('parameters'))
    returns = decode_json_layers(row.get('returns'))
    return (
        (('purpose', row['purpose']),) if row.get('purpose') else ()
    ) + tuple(
        (f"parameters.{p.get('name')}", p['description'])
        for p in (params if isinstance(params, list) else ())
        if isinstance(p, dict) and isinstance(p.get('description'), str)
    ) + (
        (('returns', returns['description']),)
        if isinstance(returns, dict) and isinstance(returns.get('description'), str) else ()
    )


def dangling_in(texts: Tuple[Tuple[str, str], ...], known: FrozenSet[str],
                own_parameters: FrozenSet[str]) -> Tuple[Dict[str, Any], ...]:
    """Pure: {field, mentions} for each mentioned callable that exists nowhere."""
    return tuple(
        {'field': field, 'mentions': name}
        for field, text in texts
        for name in mentioned_callables(text)
        if name not in known and name not in own_parameters
    )


def description_findings(index: CodeIndex) -> Dict[str, Tuple[Any, ...]]:
    """Pure: stale / unbaselined / dangling findings for the in-scope files."""
    known = known_names(index)
    in_scope = {f.file_id: f for f in index.files if f.fidelity == 'full' and f.file_id is not None}
    stale: List[Dict[str, Any]] = []
    unbaselined: List[Dict[str, Any]] = []
    dangling: List[Dict[str, Any]] = []

    for row in index.snapshot.functions:
        facts = in_scope.get(row['file_id'])
        if facts is None:
            continue
        code = next((fn for fn in facts.entities.functions if fn.name == row['name']), None)
        if code is None:
            continue    # gone from the source: audit_functions reports it as stale
        ident = {'function_id': row['id'], 'name': row['name'], 'path': facts.path}
        if (row['purpose'] or '').strip():
            if row['source_hash'] is None:
                if index.snapshot.has_source_hash:
                    unbaselined.append(ident)
            elif row['source_hash'] != code.body_hash:
                stale.append({**ident, 'recorded_hash': row['source_hash'], 'current_hash': code.body_hash})
        own = frozenset(p['name'].lstrip('*') for p in code.parameters)
        dangling.extend({**ident, **mention} for mention in dangling_in(description_texts(row), known, own))

    for row in index.snapshot.types:
        facts = in_scope.get(row['file_id'])
        if facts is None or not row.get('description'):
            continue
        dangling.extend(
            {'type_id': row['id'], 'name': row['name'], 'path': facts.path, **mention}
            for mention in dangling_in((('description', row['description']),), known, frozenset())
        )

    return {
        'stale_descriptions': tuple(stale),
        'dangling_mentions': tuple(dangling),
        'unbaselined_descriptions': tuple(unbaselined),
        'unchecked_files': tuple(u for u in unchecked_files(index) if u['file_id'] is not None),
        'skipped_paths': index.scope.skipped,
    }


# ============================================================================
# Public Tool
# ============================================================================

def run_description_audit(index: CodeIndex) -> Dict[str, Tuple[Any, ...]]:
    """Effect-free wrapper for audit_tracking: description findings for a built index."""
    return description_findings(index)


def audit_descriptions(
    file_ids: Optional[List[int]] = None,
    paths: Optional[List[str]] = None,
    since_commit: Optional[str] = None,
    task_id: Optional[int] = None,
    task_type: Optional[str] = None,
    module_path: Optional[str] = None,
    project_root: Optional[str] = None,
) -> AuditResult:
    """
    Audit function and type descriptions for drift from the code. Read-only.

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
        AuditResult: counts, samples, warn verdict, and report_path to the full report
    """
    root = project_root or get_cached_project_root()
    try:
        index, error = build_code_index(root, build_scope_request(
            file_ids, paths, since_commit, task_id, task_type, module_path))
        if index is None:
            return AuditResult(success=False, error=error)
        return finish_audit(
            root, 'audit_descriptions', scope_summary(index), description_findings(index),
            statements=get_return_statements("audit_descriptions"),
        )
    except Exception as exc:
        return AuditResult(success=False, error=f"Description audit failed: {exc}")
