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
  (``file(s)``) are never mentions. Python keywords and the vocabulary of
  languages the code generates or parses are known too: any identifier that
  is a whole string literal in the code (``'reference'``) or written as
  ``name(`` inside one (``'jsonencode('``, ``'parse_json('``); docstrings
  do not count.

unbaselined_descriptions (info) lists described functions with no hash yet —
written before schema 1.15 or by a path that could not read the code — whose
prose passes triage against the code; apply_audit_fixes(apply=
['unbaselined_descriptions']) baselines them in one call. Those that fail
triage go to unbaselined_needs_review (info) with the reasons, the recorded
prose and a code excerpt, sized for review in batches: fix the prose (writing
it records the hash) or baseline the ones that are fine
(baseline_hash_function_ids). stale_descriptions carry the same triage and
excerpt. Triage flags: recorded parameter names that differ from the code, a
recorded return type that differs from the annotation, a purpose of under
three words, and code-shaped names in the prose (snake_case, `backticked`)
that neither the function nor the rest of its file uses (names, imports,
string words; a plural or singular of one counts). Such a name found nowhere
in the repo (every file under the project root the exclusion stack keeps, any
language) is the strongest signal: those findings carry priority 'high' with
parameter and return mismatches, and every review list is sorted high first.
"""

import ast
import builtins
import functools
import keyword
import re
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from ..utils import decode_json_layers, get_cached_project_root, get_return_statements
from .code_index import (
    CodeIndex, _effect_repo_texts, build_code_index, build_scope_request, scope_summary, unchecked_files,
)
from .exemptions import exempt_index_findings
from .reports import AuditResult, finish_audit


_MENTION = re.compile(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\((\)|s\)|es\))?')
_CODE_SHAPED = re.compile(r'`([A-Za-z_][A-Za-z0-9_]*)`|(?<![\w.])([a-z][a-z0-9]*_[a-z0-9_]*[a-z0-9])(?![\w(])')
EXCERPT_LINES = 12
NOWHERE = 'found nowhere in the repo'
_CALL_IN_STRING = re.compile(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\s*\(')
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
STRUCTURAL_REASONS = ('parameters recorded as', 'returns recorded as')


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


def docstring_ids(tree: ast.Module) -> FrozenSet[int]:
    """Pure: ids of the docstring constants of a module and its classes and functions."""
    return frozenset(
        id(node.body[0].value) for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)
    )


def string_vocabulary(trees: Iterable[ast.Module]) -> FrozenSet[str]:
    """
    Pure: Identifiers the code writes inside string literals — a whole-string
    identifier (a dict key, a token: 'reference') or a call written in a
    string ('jsonencode(', 'parse_json('): the vocabulary of a language the
    module generates or parses. Docstrings are prose, not vocabulary.
    """
    words: set = set()
    for tree in trees:
        prose = docstring_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose:
                text = node.value.strip()
                if text.isidentifier():
                    words.add(text)
                words.update(_CALL_IN_STRING.findall(node.value))
    return frozenset(words)


def known_names(index: CodeIndex) -> FrozenSet[str]:
    """Pure: Names a description may legitimately mention as callables."""
    return (
        defined_and_imported_names(index.trees.values())
        | string_vocabulary(index.trees.values())
        | frozenset(fn['name'] for fn in index.snapshot.functions)
        | frozenset(ty['name'] for ty in index.snapshot.types)
        | frozenset(dir(builtins))
        | frozenset(keyword.kwlist) | frozenset(keyword.softkwlist)
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


def function_node(tree: Optional[ast.Module], name: str) -> Optional[ast.AST]:
    """Pure: The module's top-level def of `name`, or None."""
    return next((node for node in (tree.body if tree else ())
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name), None)


def code_tokens(node: ast.AST, prose: FrozenSet[int] = frozenset()) -> FrozenSet[str]:
    """
    Pure: Every name a function's (or module's) code uses: names, attributes,
    arguments, imports and the words of its strings, except the strings whose
    ids are in `prose` (docstrings).
    """
    tokens: set = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            tokens.add(inner.id)
        elif isinstance(inner, ast.Attribute):
            tokens.add(inner.attr)
        elif isinstance(inner, ast.arg):
            tokens.add(inner.arg)
        elif isinstance(inner, ast.keyword) and inner.arg:
            tokens.add(inner.arg)
        elif isinstance(inner, ast.alias):
            tokens.update(part for part in (*inner.name.split('.'), inner.asname) if part)
        elif isinstance(inner, ast.Constant) and isinstance(inner.value, str) and id(inner) not in prose:
            tokens.update(_WORD.findall(inner.value))
    return frozenset(tokens)


def prose_identifiers(text: str) -> Tuple[str, ...]:
    """Pure: Code-shaped words in prose: `backticked` names and snake_case words."""
    return tuple(dict.fromkeys(a or b for a, b in _CODE_SHAPED.findall(text or '')))


def word_forms(word: str) -> Tuple[str, ...]:
    """Pure: A word with its plural and singular forms (connectors -> connector, entry -> entries)."""
    forms = [word, word + 's', word + 'es']
    if word.endswith('ies'):
        forms.append(word[:-3] + 'y')
    if word.endswith('es'):
        forms.append(word[:-2])
    if word.endswith('s'):
        forms.append(word[:-1])
    if word.endswith('y'):
        forms.append(word[:-1] + 'ies')
    return tuple(forms)


def is_known_word(word: str, words: FrozenSet[str]) -> bool:
    """Pure: Whether `words` holds the word, its plural or its singular."""
    return any(form in words for form in word_forms(word))


def text_words(texts: Iterable[str]) -> FrozenSet[str]:
    """Pure: Every identifier-shaped word in raw text (any language)."""
    return frozenset(word for text in texts for word in _WORD.findall(text))


def triage_priority(triage: Tuple[str, ...]) -> str:
    """Pure: 'high' when the prose names something found nowhere or the signature disagrees, else 'low'."""
    return 'high' if any(r.startswith(STRUCTURAL_REASONS) or r.endswith(NOWHERE) for r in triage) else 'low'


def by_priority(entries: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], ...]:
    """Pure: Review entries, 'high' priority first (stable within a priority)."""
    return tuple(sorted(entries, key=lambda e: e.get('priority') != 'high'))


def _normalized(annotation: Optional[str]) -> str:
    return re.sub(r'\s+', '', annotation or '').replace('typing.', '')


def description_triage(
    row: Dict[str, Any],
    code: Any,
    node: Optional[ast.AST],
    known: FrozenSet[str],
    file_words: FrozenSet[str] = frozenset(),
    repo_words: Callable[[], FrozenSet[str]] = frozenset,
) -> Tuple[str, ...]:
    """
    Pure: Reasons a function's recorded prose may not describe its code (empty
    = passes triage).

    Args:
        row: The tracked function row
        code: Its ExtractedFunction
        node: Its def node (for the names the code uses)
        known: Project-wide known names (tracked functions and types)
        file_words: Every name the function's file uses (code_tokens of the
            module, docstrings excluded)
        repo_words: () -> every word in the repo; called only when a prose name
            is unknown to the function and its file
    """
    reasons: List[str] = []
    params = decode_json_layers(row.get('parameters'))
    recorded = tuple(
        (p.get('name') if isinstance(p, dict) else str(p).split(':')[0]).strip().lstrip('*')
        for p in (params if isinstance(params, list) else ())
    )
    actual = tuple(p['name'].lstrip('*') for p in code.parameters)
    if recorded != actual:
        reasons.append(f"parameters recorded as ({', '.join(recorded)}), code has ({', '.join(actual)})")
    returns = decode_json_layers(row.get('returns'))
    recorded_type = returns.get('type') if isinstance(returns, dict) else None
    actual_type = (code.returns or {}).get('type')
    if recorded_type and actual_type and _normalized(recorded_type) != _normalized(actual_type):
        reasons.append(f"returns recorded as {recorded_type}, annotated {actual_type}")
    if len((row.get('purpose') or '').split()) < 3:
        reasons.append('purpose under three words')
    used = (code_tokens(node) if node is not None else frozenset()) | known | file_words | {row['name']}
    unused = tuple(dict.fromkeys(
        name for _, text in description_texts(row) for name in prose_identifiers(text)
        if not is_known_word(name, used)
    ))
    nowhere = tuple(name for name in unused if not is_known_word(name, repo_words())) if unused else ()
    elsewhere = tuple(name for name in unused if name not in nowhere)
    if nowhere:
        reasons.append(f"prose names {', '.join(nowhere)}, {NOWHERE}")
    if elsewhere:
        reasons.append(f"prose names {', '.join(elsewhere)}, which this file never uses (found elsewhere in the repo)")
    return tuple(reasons)


def code_excerpt(node: Optional[ast.AST], lines: int = EXCERPT_LINES) -> Optional[str]:
    """Pure: The function's code (docstring dropped), first `lines` lines."""
    if node is None:
        return None
    body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                             and isinstance(getattr(node.body[0], 'value', None), ast.Constant)
                             and isinstance(node.body[0].value.value, str)) else node.body
    whole = ast.unparse(node).splitlines()
    header = next(i for i, line in enumerate(whole) if line.lstrip().startswith(('def ', 'async def ')))
    kept = whole[:header + 1] + ['    ' + line for stmt in body for line in ast.unparse(stmt).splitlines()]
    return '\n'.join(kept[:lines] + (['...'] if len(kept) > lines else []))


def review_entry(row: Dict[str, Any], ident: Dict[str, Any], triage: Tuple[str, ...],
                 node: Optional[ast.AST]) -> Dict[str, Any]:
    """Pure: A finding sized for review: what is recorded, why it was flagged, and the code."""
    params = decode_json_layers(row.get('parameters'))
    return {
        **ident,
        'priority': triage_priority(triage),
        'triage': list(triage),
        'recorded': {'purpose': row.get('purpose'), 'parameters': params, 'returns': decode_json_layers(row.get('returns'))},
        'code': code_excerpt(node),
    }


def description_findings(
    index: CodeIndex,
    repo_texts: Callable[[], Iterable[str]] = tuple,
) -> Dict[str, Tuple[Any, ...]]:
    """
    Pure: stale / unbaselined / dangling findings for the in-scope files.

    Args:
        index: The audit's code index
        repo_texts: () -> the text of the repo's files outside index.trees
            (_effect_repo_texts); read at most once, and only when a prose name
            is unknown to its function and file
    """
    known = known_names(index)
    repo_words = functools.cache(lambda: frozenset().union(
        *(code_tokens(tree) for tree in index.trees.values())) | text_words(repo_texts()))
    in_scope = {f.file_id: f for f in index.files if f.fidelity == 'full' and f.file_id is not None}
    stale: List[Dict[str, Any]] = []
    unbaselined: List[Dict[str, Any]] = []
    needs_review: List[Dict[str, Any]] = []
    dangling: List[Dict[str, Any]] = []
    tracked_names = frozenset(fn['name'] for fn in index.snapshot.functions) | frozenset(
        ty['name'] for ty in index.snapshot.types)
    file_tokens = functools.cache(
        lambda path, tree: code_tokens(tree, docstring_ids(tree)) if tree is not None else frozenset())

    for row in index.snapshot.functions:
        facts = in_scope.get(row['file_id'])
        if facts is None:
            continue
        code = next((fn for fn in facts.entities.functions if fn.name == row['name']), None)
        if code is None:
            continue    # gone from the source: audit_functions reports it as stale
        ident = {'function_id': row['id'], 'name': row['name'], 'path': facts.path}
        if (row['purpose'] or '').strip():
            unhashed = row['source_hash'] is None
            if (unhashed and index.snapshot.has_source_hash) or (not unhashed and row['source_hash'] != code.body_hash):
                tree = index.trees.get(facts.path)
                node = function_node(tree, row['name'])
                triage = description_triage(row, code, node, tracked_names,
                                            file_tokens(facts.path, tree), repo_words)
                if not unhashed:
                    stale.append({**review_entry(row, ident, triage, node),
                                  'recorded_hash': row['source_hash'], 'current_hash': code.body_hash})
                elif triage:
                    needs_review.append(review_entry(row, ident, triage, node))
                else:
                    unbaselined.append(ident)
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

    return exempt_index_findings(index, {
        'stale_descriptions': by_priority(stale),
        'dangling_mentions': tuple(dangling),
        'unbaselined_descriptions': tuple(unbaselined),
        'unbaselined_needs_review': by_priority(needs_review),
        'unchecked_files': tuple(u for u in unchecked_files(index) if u['file_id'] is not None),
        'skipped_paths': index.scope.skipped,
    })


# ============================================================================
# Public Tool
# ============================================================================

def run_description_audit(index: CodeIndex) -> Dict[str, Tuple[Any, ...]]:
    """Effect: Description findings for a built index, reading repo text only if triage needs it."""
    return description_findings(
        index, lambda: _effect_repo_texts(index.project_root, frozenset(index.trees)))


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
            root, 'audit_descriptions', scope_summary(index), run_description_audit(index),
            statements=get_return_statements("audit_descriptions"),
        )
    except Exception as exc:
        return AuditResult(success=False, error=f"Description audit failed: {exc}")
