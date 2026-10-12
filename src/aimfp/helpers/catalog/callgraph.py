"""
AIMFP Helper Functions - Call Graph Extraction

Recovers the dependency graph of an already-cataloged codebase: which tracked
function calls which other tracked function.

Cataloging files and functions without interactions leaves the most valuable part
of the index missing — ``get_interactions_by_function`` is how a later session
traces impact without reading source. This module closes that gap.

**Conservative by design.** An edge is emitted only when the target resolves
unambiguously. Resolution runs in strict precedence:

1. the name was explicitly imported into this file (``from .mod import fn``)
2. the name is defined in this same file
3. the name is called through an imported module alias (``mod.fn()``)
4. the name is unique across the entire project — only for a file with a star
   import (``from x import *``), the one way Python reaches another module's
   function without naming it. Builtins and names the function binds itself
   (parameters, locals) never resolve to a project function.

Anything still ambiguous is dropped and counted in ``unresolved``. A wrong edge is
worse than a missing one: it sends future refactors chasing dependencies that do
not exist.

A tracked function may also be bound by assignment (``check = partial(_check,
kind='a')``, ``run = compose(f, g)``, an alias): when its name is tracked, the
assigned expression is treated as its body.

Audits need more than the edges, so build_call_edges also records evidence for
edges the code makes without a direct, resolvable call: self-recursion, names
that resolve ambiguously in a caller (loose_names), calls made through a value
(value_calls), and dispatch_evidence adds edges reached through module-level
tables and the record fields a function is stored in elsewhere.

Writes nothing. It returns tuples shaped for ``add_interactions``, so the existing
batch tool does the writing — cataloged rows are not special, and the linking
surface stays the ordinary one.
"""

import ast
import builtins
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from ..utils import get_return_statements, get_cached_project_root, _open_project_connection
from ...watchdog.config import detect_language
from .scan import _effect_read_source


# Receiver recorded for attribute calls on anything but a bare name.
NON_NAME_RECEIVER = '<expr>'

# Python builtins: a bare call to one of these is never a project function
# unless the file imports or defines that name itself.
BUILTIN_NAMES = frozenset(dir(builtins))

# Module-level assignment values that can bind a callable: a factory call
# (partial, compose, a decorator applied by hand), a lambda, or an alias.
CALLABLE_VALUES = (ast.Call, ast.Lambda, ast.Name, ast.Attribute)

# resolve_target reasons that leave open which function a name means. Any
# other unresolved reason is definite: the name is not a tracked function.
LOOSE_REASONS = frozenset({'ambiguous', 'ambiguous_import'})


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class CallGraphResult:
    """Result of a call graph scan."""
    success: bool
    interactions: Tuple[Tuple[Any, ...], ...] = ()
    edge_count: int = 0
    resolved_by: Optional[Dict[str, int]] = None
    unresolved_count: int = 0
    unresolved_sample: Tuple[str, ...] = ()
    files_analyzed: int = 0
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helpers
# ============================================================================

def module_to_relative_path(module: str, package_root: str) -> str:
    """
    Pure: Convert a dotted absolute module name to a project-relative .py path.

    Args:
        module: Dotted module name (e.g. 'aimfp.helpers.project.files_1')
        package_root: Project-relative directory the top package sits in
            (e.g. 'src' for a module named 'aimfp.*')

    Returns:
        Project-relative path with a .py suffix
    """
    return os.path.join(package_root, *module.split('.')) + '.py'


def resolve_relative_module(
    current_path: str,
    level: int,
    module: Optional[str],
) -> str:
    """
    Pure: Resolve a relative import to a project-relative module path.

    ``level`` is the leading-dot count: 1 means the current package, 2 the parent,
    and so on. The result carries no .py suffix, since the target may be either a
    module file or a package directory.

    Args:
        current_path: Project-relative path of the importing file
        level: Number of leading dots in the import
        module: Dotted module name after the dots, or None for `from . import x`

    Returns:
        Project-relative path prefix of the imported module
    """
    base = os.path.dirname(current_path)
    for _ in range(level - 1):
        base = os.path.dirname(base)
    return os.path.join(base, *module.split('.')) if module else base


def extract_import_bindings(tree: ast.Module, current_path: str) -> Dict[str, str]:
    """
    Pure: Map each name bound by an import to the module path it came from.

    Covers ``from .mod import fn`` (binding fn), ``from .mod import fn as alias``,
    ``import pkg.mod as alias`` (binding the module itself) and ``import pkg.mod``
    (binding pkg). Each ``from .mod import *`` is recorded under the key
    '*<module path>', so a lookup can follow star re-exports. Relative imports
    resolve to real project-relative paths; absolute imports are recorded as the
    dotted module path ('pkg/mod') and matched against project files by suffix at
    lookup (module_suffix_index), so any package root works (src/, the project
    root, a nested folder). Third-party modules never match a project file, which
    is the desired outcome — their calls are not project interactions.

    Args:
        tree: Parsed module AST
        current_path: Project-relative path of this file

    Returns:
        Dict of bound local name -> project-relative module path prefix
    """
    bindings: Dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                target = resolve_relative_module(current_path, node.level, node.module)
            elif node.module:
                target = os.path.join(*node.module.split('.'))
            else:
                continue
            for alias in node.names:
                if alias.name == '*':
                    bindings['*' + target] = target
                    continue
                # 'from . import mod' names a submodule of the package itself
                bindings[alias.asname or alias.name] = (
                    os.path.join(target, alias.name) if node.module is None else target
                )

        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = os.path.join(*alias.name.split('.'))
                else:
                    # 'import pkg.mod' binds pkg; pkg.mod.fn() resolves through it
                    root = alias.name.split('.')[0]
                    bindings.setdefault(root, root)

    return bindings


def extract_import_origins(tree: ast.Module) -> Dict[str, str]:
    """
    Pure: Map each name bound by ``from x import name as alias`` to ``name``.

    extract_import_bindings says which module a local name came from; this says
    what the name is called there. Without it ``_hook_fn()`` (imported as
    ``from .hooks import fn as _hook_fn``) is looked up as '_hook_fn' in the
    hooks module and never resolves.

    Args:
        tree: Parsed module AST

    Returns:
        Dict of alias -> original name, for aliased from-imports only
    """
    return {
        alias.asname: alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names if alias.asname
    }


def module_suffix_index(paths: Tuple[str, ...]) -> Dict[str, Tuple[str, ...]]:
    """
    Pure: Every module path suffix -> the project Python files it can name.

    'src/aimfp/helpers/x.py' is reachable as 'x', 'helpers/x', 'aimfp/helpers/x'
    and 'src/aimfp/helpers/x'; a package's '__init__.py' as its directory. An
    absolute import ('aimfp.helpers.x') resolves when exactly one file carries
    that suffix, wherever the package root sits.
    """
    index: Dict[str, List[str]] = {}
    for path in paths:
        if not path.endswith('.py'):
            continue
        stem = path[:-3]
        if stem.endswith('/__init__'):
            stem = stem[:-len('/__init__')]
        parts = stem.split('/')
        for start in range(len(parts)):
            index.setdefault('/'.join(parts[start:]), []).append(path)
    return {key: tuple(found) for key, found in index.items()}


def has_star_import(tree: ast.Module) -> bool:
    """Pure: Whether the module does ``from x import *`` anywhere."""
    return any(
        isinstance(node, ast.ImportFrom) and any(alias.name == '*' for alias in node.names)
        for node in ast.walk(tree)
    )


def top_level_function_nodes(tree: ast.Module) -> Tuple[ast.AST, ...]:
    """Pure: The module's top-level function definitions, in source order."""
    return tuple(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def assigned_callables(tree: ast.Module) -> Dict[str, ast.expr]:
    """
    Pure: Module-level names bound by assignment to a callable-shaped value
    (``check = partial(_check, kind='a')``, ``f = lambda x: x``, ``g = other``),
    mapped to that value. Whether a name is a function this way is the
    tracker's call: only names tracked as functions are treated as one.
    """
    found: Dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, CALLABLE_VALUES):
            found.update((t.id, node.value) for t in node.targets if isinstance(t, ast.Name))
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and isinstance(node.value, CALLABLE_VALUES)):
            found[node.target.id] = node.value
    return found


def top_level_callables(
    tree: ast.Module,
    assigned: FrozenSet[str] = frozenset(),
) -> Tuple[Tuple[str, ast.AST], ...]:
    """
    Pure: (name, body) for every top-level def, plus each name in `assigned`
    that the module binds by assignment to a callable (body = the assigned
    expression). Defs win over a same-named assignment.
    """
    defs = tuple((node.name, node) for node in top_level_function_nodes(tree))
    if not assigned:
        return defs
    defined = frozenset(name for name, _ in defs)
    return defs + tuple(
        (name, value) for name, value in assigned_callables(tree).items()
        if name in assigned and name not in defined
    )


def receiver_name(value: ast.expr) -> str:
    """
    Pure: The receiver of an attribute as written: 'mod' for ``mod.fn``,
    'pkg.mod' for ``pkg.mod.fn``, NON_NAME_RECEIVER when the chain does not
    start at a name (``", ".join``, ``rows[0].get``).
    """
    parts: List[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        return '.'.join([value.id] + parts[::-1])
    return NON_NAME_RECEIVER


def locally_bound_names(node: ast.AST) -> FrozenSet[str]:
    """
    Pure: Names a function binds itself — parameters, assignment, loop,
    comprehension, with/except targets, nested defs — which therefore never
    refer to a module-level function inside it.
    """
    bound: Set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.arg):
            bound.add(inner.arg)
        elif isinstance(inner, ast.Name) and isinstance(inner.ctx, (ast.Store, ast.Del)):
            bound.add(inner.id)
        elif isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not node:
            bound.add(inner.name)
        elif isinstance(inner, ast.ExceptHandler) and inner.name:
            bound.add(inner.name)
    return frozenset(bound)


def extract_function_references(
    tree: ast.Module,
    assigned: FrozenSet[str] = frozenset(),
) -> Dict[str, Tuple[Tuple[str, Optional[str]], ...]]:
    """
    Pure: Map each top-level function to the names it uses as VALUES, not calls.

    A function passed along (``map(fn, xs)``, ``partial(fn, 1)``,
    ``read_together(pairs, fn)``, ``{'a': mod.fn}`` built inside the body) is a
    dependency as real as a call. Recorded as (name, base) like
    extract_function_calls; names the function binds itself are excluded, and
    names in call position are left to extract_function_calls.

    Args:
        tree: Parsed module AST
        assigned: Names to treat as functions when bound by assignment
            (top_level_callables)

    Returns:
        Dict of enclosing function name -> tuple of (referenced name, base) pairs
    """
    references: Dict[str, Tuple[Tuple[str, Optional[str]], ...]] = {}

    for name, node in top_level_callables(tree, assigned):
        called = {
            id(inner.func) for inner in ast.walk(node) if isinstance(inner, ast.Call)
        }
        bound = locally_bound_names(node)
        found: List[Tuple[str, Optional[str]]] = []
        for inner in ast.walk(node):
            if id(inner) in called:
                continue
            if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load):
                if inner.id not in bound:
                    found.append((inner.id, None))
            elif isinstance(inner, ast.Attribute) and isinstance(inner.ctx, ast.Load):
                base = receiver_name(inner.value)
                if base != NON_NAME_RECEIVER:
                    found.append((inner.attr, base))
        references[name] = tuple(dict.fromkeys(found))

    return references


def extract_function_calls(
    tree: ast.Module,
    assigned: FrozenSet[str] = frozenset(),
) -> Dict[str, Tuple[Tuple[str, Optional[str]], ...]]:
    """
    Pure: Map each top-level function to the calls made inside it.

    Each call is recorded as (name, base) — ``base`` is the receiver for
    attribute-style calls as written (``mod.fn()`` yields ('fn', 'mod'),
    ``pkg.mod.fn()`` yields ('fn', 'pkg.mod')), None for bare calls (``fn()``
    yields ('fn', None)), and NON_NAME_RECEIVER for a call on any other
    expression (``", ".join(x)``, ``rows[0].get()``), which is a call on a
    value, never on a project module.

    Args:
        tree: Parsed module AST
        assigned: Names to treat as functions when bound by assignment
            (top_level_callables)

    Returns:
        Dict of enclosing function name -> tuple of (called name, base) pairs
    """
    calls: Dict[str, Tuple[Tuple[str, Optional[str]], ...]] = {}

    for name, node in top_level_callables(tree, assigned):
        # A parameter or local the function binds itself (callback(), helper = make(); helper())
        # is never a module-level function
        bound = locally_bound_names(node)
        found: List[Tuple[str, Optional[str]]] = []
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Name):
                if func.id not in bound:
                    found.append((func.id, None))
            elif isinstance(func, ast.Attribute):
                found.append((func.attr, receiver_name(func.value)))

        calls[name] = tuple(dict.fromkeys(found))

    return calls


def extract_string_literals(
    tree: ast.Module,
    assigned: FrozenSet[str] = frozenset(),
) -> Dict[str, Tuple[str, ...]]:
    """
    Pure: Map each top-level function to the string literals in its body,
    docstrings excluded (f-string pieces included).

    The evidence for edges no Python call shows: a SQL function called in a
    query string (``"select * from dependents(%s)"``), a name looked up with
    ``getattr(mod, "fn")``.
    """
    strings: Dict[str, Tuple[str, ...]] = {}
    for name, node in top_level_callables(tree, assigned):
        body = getattr(node, 'body', None)
        first = body[0] if isinstance(body, list) and body else None
        docstring = (
            id(first.value) if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str) else None
        )
        strings[name] = tuple(dict.fromkeys(
            inner.value for inner in ast.walk(node)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and id(inner) != docstring
        ))
    return strings


def extract_bound_calls(
    tree: ast.Module,
    assigned: FrozenSet[str] = frozenset(),
) -> Dict[str, FrozenSet[str]]:
    """
    Pure: Map each top-level function to the names it calls that it binds
    itself — usually a callback parameter (``def read(d, environment_at):
    environment_at(d)``). Never a call edge on its own; evidence that a tracked
    edge to a function of that name is deliberate (the function is passed in).
    """
    found: Dict[str, FrozenSet[str]] = {}
    for name, node in top_level_callables(tree, assigned):
        bound = locally_bound_names(node)
        found[name] = frozenset(
            inner.func.id for inner in ast.walk(node)
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id in bound
        )
    return found


def extract_referenced_names(
    tree: ast.Module,
    assigned: FrozenSet[str] = frozenset(),
) -> Dict[str, FrozenSet[str]]:
    """
    Pure: Map each top-level function to every name it references — bare names,
    attribute names (``mod.fn`` gives 'fn'), and the original name behind any
    ``from x import fn as alias`` used inside it.

    Broader than extract_function_calls on purpose: a function passed as a
    callback (``map(fn, xs)``) is referenced without being called. A tracked
    call edge whose target name is absent from this set cannot still exist.

    Args:
        tree: Parsed module AST
        assigned: Names to treat as functions when bound by assignment
            (top_level_callables)
    """
    aliases: Dict[str, str] = {
        alias.asname: alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names if alias.asname
    }
    referenced: Dict[str, FrozenSet[str]] = {}
    for name, node in top_level_callables(tree, assigned):
        names = frozenset(
            inner.id if isinstance(inner, ast.Name) else inner.attr
            for inner in ast.walk(node)
            if isinstance(inner, (ast.Name, ast.Attribute))
        )
        referenced[name] = names | frozenset(aliases[n] for n in names if n in aliases)
    return referenced



def fold_untracked_helpers(
    referenced: Dict[str, FrozenSet[str]],
    tracked: FrozenSet[str],
) -> Dict[str, FrozenSet[str]]:
    """
    Pure: Each function's referenced names plus those of every same-file
    function it reaches only through functions nobody tracks.

    Calls made inside an untracked helper (usually a _private one) are
    recorded on the tracked function that uses it, so a check of that
    function's own body alone would call those edges stale. A tracked
    helper stops the walk: its calls are its own edges.

    Args:
        referenced: extract_referenced_names output for one file
        tracked: names of the file's functions that are tracked
    """
    reach = reachable_untracked_helpers(referenced, tracked)
    return {
        name: referenced[name].union(*(referenced[h] for h in reach[name]))
        for name in referenced
    }


def reachable_untracked_helpers(
    referenced: Dict[str, FrozenSet[str]],
    tracked: FrozenSet[str],
) -> Dict[str, FrozenSet[str]]:
    """
    Pure: For each same-file function, the untracked same-file functions it
    reaches through untracked functions only (itself excluded).

    The shared walk behind crediting an untracked helper's calls and
    references to the tracked function that uses it: a tracked helper stops
    the walk, since its dependencies are its own edges.

    Args:
        referenced: extract_referenced_names output for one file
        tracked: names of the file's functions that are tracked
    """
    helpers = frozenset(referenced) - tracked

    def reach(name: str) -> FrozenSet[str]:
        seen, pending = {name}, [name]
        while pending:
            fresh = (referenced[pending.pop()] & helpers) - seen
            seen.update(fresh)
            pending.extend(fresh)
        return frozenset(seen - {name})

    return {name: reach(name) for name in referenced}


def resolve_target(
    name: str,
    base: Optional[str],
    current_path: str,
    bindings: Dict[str, str],
    by_file: Dict[str, Dict[str, int]],
    by_name: Dict[str, List[int]],
    bindings_index: Optional[Dict[str, Dict[str, str]]] = None,
    max_hops: int = 3,
    origins: Optional[Dict[str, str]] = None,
    origins_index: Optional[Dict[str, Dict[str, str]]] = None,
    defined_here: FrozenSet[str] = frozenset(),
    allow_unique_name: bool = True,
    module_index: Optional[Dict[str, Tuple[str, ...]]] = None,
) -> Tuple[Optional[int], str]:
    """
    Pure: Resolve a called name to a tracked function ID, or decline.

    Precedence is strictest-first so that a confident local answer always beats a
    project-wide name guess. Returns the reason alongside the ID so callers can
    report which strategy carried each edge.

    Re-exports are followed: a package that collects names into a ``utils.py`` or
    ``__init__.py`` and re-exports them is extremely common, and without following
    the hop every call routed through such a module falls through to an ambiguous
    project-wide name match.

    Args:
        name: Called function name
        base: Receiver name for attribute calls, else None
        current_path: Project-relative path of the calling file
        bindings: Import bindings for the calling file
        by_file: path -> {function name -> function id}
        by_name: function name -> list of every tracked id with that name
        bindings_index: path -> import bindings, for following re-export chains
        max_hops: Cap on re-export hops, so an import cycle cannot spin
        origins: alias -> original name for the calling file's aliased imports
        origins_index: path -> origins, for aliased re-exports along a chain
        defined_here: every top-level function name the calling file defines,
            tracked or not. A bare name defined here means the local function,
            so it never falls through to a project-wide name match.
        allow_unique_name: Whether the project-wide unique-name fallback may
            answer (callers enable it only for files with a star import, and
            never for value references).
        module_index: module_suffix_index of the project's files, so an
            absolute import resolves whatever the package root

    Returns:
        (function id or None, reason string). Unresolved reasons in
        LOOSE_REASONS ('ambiguous', 'ambiguous_import': the module path matches
        several project files) leave the meaning open; every other one is
        definite — the name is not a tracked function.
    """
    index = bindings_index or {}
    origin_tables = origins_index or {}
    modules = module_index or {}

    def candidates_for(path_prefix: str) -> Tuple[str, ...]:
        """Files a module path can mean: the exact path, else its unique suffix match."""
        exact = (path_prefix + '.py', os.path.join(path_prefix, '__init__.py'))
        if any(c in by_file or c in index for c in exact):
            return exact
        matched = modules.get(path_prefix, ())
        return matched if len(matched) == 1 else exact

    def lookup(path_prefix: str, symbol: str, hops: int = 0) -> Tuple[Optional[int], bool]:
        """Resolve symbol within a module path, following re-exports (and the
        aliases they rename through) and star re-exports. Returns (id,
        went_through_reexport)."""
        for candidate in candidates_for(path_prefix):
            table = by_file.get(candidate)
            if table and symbol in table:
                return (table[symbol], hops > 0)
            # The module exists but does not define the name — it may re-export it.
            if hops < max_hops and candidate in index:
                onward_bindings = index[candidate]
                onward = onward_bindings.get(symbol)
                routes = (
                    ((onward, origin_tables.get(candidate, {}).get(symbol, symbol)),)
                    if onward and onward != path_prefix else ()
                ) + tuple(
                    (target, symbol) for key, target in onward_bindings.items()
                    if key.startswith('*') and target != path_prefix
                )
                for route, onward_symbol in routes:
                    found, _ = lookup(route, onward_symbol, hops + 1)
                    if found is not None:
                        return (found, True)
        return (None, False)

    def miss_reason(paths: Tuple[str, ...]) -> str:
        """Why an imported name found no tracked function: definite ('external')
        unless a module path matches several project files."""
        def ambiguous(path_prefix: str) -> bool:
            exact = (path_prefix + '.py', os.path.join(path_prefix, '__init__.py'))
            return not any(c in by_file or c in index for c in exact) and len(modules.get(path_prefix, ())) > 1
        return 'ambiguous_import' if any(ambiguous(p) for p in paths) else 'external'

    if base is None and name in bindings:
        found, via_reexport = lookup(bindings[name], (origins or {}).get(name, name))
        if found is not None:
            return (found, 'reexport' if via_reexport else 'import')
        # Imported, so never a project-wide guess: a third-party name
        # (from urllib.parse import quote) or an untracked one
        return (None, miss_reason((bindings[name],)))

    if base is None:
        local = by_file.get(current_path, {})
        if name in local:
            return (local[name], 'local')
        if name in defined_here:
            return (None, 'untracked_local')
        if name in BUILTIN_NAMES:
            return (None, 'external')

    if base is not None:
        root, _, rest = base.partition('.')
        if root not in bindings:
            # A method-style call on a value (a local, a literal, an expression):
            # never a project function, so never a project-wide name guess.
            return (None, 'external')
        # 'from .pkg import mod' binds mod to pkg; mod may be pkg's submodule.
        # pkg.mod.fn() walks the rest of the dotted receiver as submodules.
        module = bindings[root]
        heads = (module, os.path.join(module, (origins or {}).get(root, root)))
        candidates = tuple(os.path.join(h, *rest.split('.')) if rest else h for h in heads)
        for candidate in candidates:
            found, via_reexport = lookup(candidate, name)
            if found is not None:
                return (found, 'reexport' if via_reexport else 'module_alias')
        return (None, miss_reason(candidates))

    candidates = by_name.get(name, [])
    if allow_unique_name and len(candidates) == 1:
        return (candidates[0], 'unique_name')

    return (None, 'ambiguous' if candidates else 'external')


# ============================================================================
# Effect Functions
# ============================================================================

TrackedIndex = Tuple[Dict[str, Dict[str, int]], Dict[str, List[int]], Dict[int, str], Tuple[str, ...]]


def query_tracked_functions(conn: sqlite3.Connection) -> TrackedIndex:
    """
    Effect: Load every tracked function and every tracked file path, indexed for
    resolution, through the caller's connection (so rows written earlier in the
    same, still-open transaction are included — reconcile_paths relies on this).

    All file paths are returned, not just those owning functions: re-export
    modules (a package's ``utils.py`` or ``__init__.py``) define no functions of
    their own, yet they are exactly the files an import chain has to pass through.
    Indexing only function-bearing files makes every call routed through a
    re-export unresolvable.

    Returns:
        (by_file, by_name, id_to_path, all_paths) lookup tables
    """
    rows = conn.execute(
        """
        SELECT fn.id, fn.name, f.path
        FROM functions fn JOIN files f ON f.id = fn.file_id
        """
    ).fetchall()
    path_rows = conn.execute("SELECT path FROM files").fetchall()

    by_file: Dict[str, Dict[str, int]] = {}
    by_name: Dict[str, List[int]] = {}
    id_to_path: Dict[int, str] = {}

    for row in rows:
        by_file.setdefault(row["path"], {})[row["name"]] = row["id"]
        by_name.setdefault(row["name"], []).append(row["id"])
        id_to_path[row["id"]] = row["path"]

    return (by_file, by_name, id_to_path, tuple(r["path"] for r in path_rows))


def _effect_tracked_functions(project_root: str) -> TrackedIndex:
    """Effect: query_tracked_functions on a fresh connection."""
    conn = _open_project_connection(project_root)
    try:
        return query_tracked_functions(conn)
    finally:
        conn.close()


@dataclass(frozen=True)
class CallEdges:
    """Edges resolved for a set of calling files, before anything is written.

    edge_via is aligned with edges: how each one was found — 'call', 'reference'
    (the target is used as a value), each optionally suffixed with
    ' via <helper>' when it was made inside an untracked same-file helper.

    Evidence for audits, keyed by source function id (never written as edges):
    self_edges — functions that call themselves (recursion; edges never
    include a self-edge); loose_names — names the function uses that resolve
    ambiguously, so a tracked edge to any function of that name is
    unprovable rather than stale; value_calls — attribute names it calls on
    a value (``c.touches(...)`` gives 'touches'), not on a module.
    """
    edges: Tuple[Tuple[Any, ...], ...]
    resolved_by: Dict[str, int]
    unresolved: Tuple[str, ...]
    files_analyzed: int
    edge_via: Tuple[str, ...] = ()
    self_edges: FrozenSet[int] = frozenset()
    loose_names: Dict[int, FrozenSet[str]] = field(default_factory=dict)
    value_calls: Dict[int, FrozenSet[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolutionContext:
    """The lookup tables every resolution needs, built once from the parsed files."""
    by_file: Dict[str, Dict[str, int]]
    by_name: Dict[str, List[int]]
    id_to_path: Dict[int, str]
    bindings_index: Dict[str, Dict[str, str]]
    origins_index: Dict[str, Dict[str, str]]
    modules: Dict[str, Tuple[str, ...]]


def resolution_context(tracked: TrackedIndex, trees: Dict[str, ast.Module]) -> ResolutionContext:
    """Pure: Index import bindings, aliases and module suffixes of every parsed file."""
    by_file, by_name, id_to_path, all_paths = tracked
    return ResolutionContext(
        by_file=by_file,
        by_name=by_name,
        id_to_path=id_to_path,
        bindings_index={path: extract_import_bindings(tree, path) for path, tree in trees.items()},
        origins_index={path: extract_import_origins(tree) for path, tree in trees.items()},
        modules=module_suffix_index(tuple(all_paths)),
    )


def resolve_in(
    context: ResolutionContext,
    path: str,
    name: str,
    base: Optional[str],
    defined_here: FrozenSet[str] = frozenset(),
    allow_unique_name: bool = False,
) -> Tuple[Optional[int], str]:
    """Pure: resolve_target for a name used in file `path`, with a built context."""
    return resolve_target(
        name, base, path, context.bindings_index.get(path, {}), context.by_file, context.by_name,
        context.bindings_index, origins=context.origins_index.get(path, {}),
        origins_index=context.origins_index, defined_here=defined_here,
        allow_unique_name=allow_unique_name, module_index=context.modules,
    )


def is_value_receiver(base: Optional[str], bindings: Dict[str, str]) -> bool:
    """Pure: Whether an attribute call's receiver is a value (a local, a
    parameter, an expression) rather than an imported module."""
    return base is not None and base.partition('.')[0] not in bindings


def credited_dependencies(
    tree: ast.Module,
    tracked: FrozenSet[str],
) -> Dict[str, Tuple[Tuple[str, Optional[str], str], ...]]:
    """
    Pure: Every (name, base, via) dependency credited to each tracked top-level
    function: its own calls and value references, plus those made inside the
    untracked same-file helpers it reaches.

    via is 'call' or 'reference', with ' via <helper>' appended for a
    dependency that lives in an untracked helper. A call beats a reference for
    the same (name, base), and the function's own body beats a helper's.

    Args:
        tree: Parsed module AST
        tracked: names of the file's tracked functions
    """
    calls = extract_function_calls(tree, tracked)
    references = extract_function_references(tree, tracked)
    reach = reachable_untracked_helpers(extract_referenced_names(tree, tracked), tracked)

    def own(name: str, suffix: str) -> Tuple[Tuple[str, Optional[str], str], ...]:
        return (
            tuple((n, b, 'call' + suffix) for n, b in calls.get(name, ()))
            + tuple((n, b, 'reference' + suffix) for n, b in references.get(name, ()))
        )

    credited: Dict[str, Tuple[Tuple[str, Optional[str], str], ...]] = {}
    for name in calls:
        if name not in tracked:
            continue
        found = own(name, '') + tuple(
            dep for helper in sorted(reach.get(name, ())) for dep in own(helper, f' via {helper}')
        )
        first: Dict[Tuple[str, Optional[str]], str] = {}
        for dep_name, dep_base, via in found:
            first.setdefault((dep_name, dep_base), via)
        credited[name] = tuple((n, b, v) for (n, b), v in first.items())
    return credited


def build_call_edges(
    project_root: str,
    tracked: TrackedIndex,
    include_caller: Callable[[str], bool],
    trees: Optional[Dict[str, ast.Module]] = None,
    context: Optional[ResolutionContext] = None,
) -> CallEdges:
    """
    Effect: Resolve call edges for every tracked Python file accepted by
    include_caller (reads source files; writes nothing).

    Every tracked Python file is parsed for its import bindings — following a
    re-export needs the bindings of a file outside the caller set — but only
    accepted files contribute calling edges. A tracked function is credited
    with its calls, the functions it uses as values (resolved through imports
    and same-file definitions only, never a project-wide name guess), and both
    kinds made inside the untracked same-file helpers it reaches. Edges are
    unique per (source, target); self-edges and ambiguous targets are dropped
    (recorded as evidence instead: self_edges, loose_names, value_calls).

    trees: already-parsed tracked Python files (path -> module), when the
    caller has them (an audit's CodeIndex); otherwise they are parsed here.
    context: an already-built resolution_context over the same trees.
    """
    edges: List[Tuple[Any, ...]] = []
    edge_via: List[str] = []
    seen: Set[Tuple[int, int]] = set()
    reasons: Dict[str, int] = {}
    unresolved: List[str] = []
    self_edges: Set[int] = set()
    loose: Dict[int, Set[str]] = {}
    value_calls: Dict[int, Set[str]] = {}

    # Pass 1: parse every tracked Python file and index its import bindings.
    # The whole index must exist before resolution, because following a
    # re-export needs the bindings of a file that may not be analyzed yet —
    # and re-export targets sit outside the caller set more often than not.
    trees = trees if trees is not None else _effect_parse_python_files(project_root, tracked[3])
    context = context or resolution_context(tracked, trees)
    by_file, id_to_path = context.by_file, context.id_to_path

    # Pass 2: resolve dependencies, on the calling side only for accepted files.
    analyzed = 0
    for path, tree in sorted(trees.items()):
        if not include_caller(path):
            continue

        analyzed += 1
        local_functions = by_file.get(path, {})
        defined_here = frozenset(node.name for node in top_level_function_nodes(tree))
        star_import = has_star_import(tree)
        bindings = context.bindings_index.get(path, {})

        credited = credited_dependencies(tree, frozenset(local_functions))
        for caller, dependencies in credited.items():
            source_id = local_functions[caller]

            for name, base, via in dependencies:
                is_call = via.startswith('call')
                if is_call and is_value_receiver(base, bindings):
                    value_calls.setdefault(source_id, set()).add(name)
                target_id, reason = resolve_in(
                    context, path, name, base, defined_here=defined_here,
                    allow_unique_name=is_call and star_import,
                )
                if target_id is None:
                    if reason in LOOSE_REASONS:
                        loose.setdefault(source_id, set()).add(name)
                    if reason == 'ambiguous' and is_call:
                        unresolved.append(f"{path}:{caller} -> {name}")
                    continue
                if target_id == source_id:
                    if is_call:
                        self_edges.add(source_id)
                    continue
                if (source_id, target_id) in seen:
                    continue

                seen.add((source_id, target_id))
                reasons[reason] = reasons.get(reason, 0) + 1
                verb = 'calls' if is_call else 'references'
                helper = via.partition(' via ')[2]
                edges.append((
                    source_id,
                    target_id,
                    'call',
                    f"{caller} {verb} {name} ({os.path.basename(id_to_path[target_id])})"
                    + (f" via {helper}" if helper else ""),
                ))
                edge_via.append(via)

    return CallEdges(
        edges=tuple(edges),
        resolved_by=reasons,
        unresolved=tuple(unresolved),
        files_analyzed=analyzed,
        edge_via=tuple(edge_via),
        self_edges=frozenset(self_edges),
        loose_names={k: frozenset(v) for k, v in loose.items()},
        value_calls={k: frozenset(v) for k, v in value_calls.items()},
    )


def module_tables(tree: ast.Module) -> Dict[str, Tuple[Tuple[str, Optional[str]], ...]]:
    """
    Pure: Each module-level name bound by assignment, mapped to the (name,
    base) references inside its value — the functions a dispatch table
    (``COMMANDS = MappingProxyType({'init': _cmd_init})``, ``RENDERERS =
    (('a', egress), ...)``) or a record (``ADAPTER = Adapter(checks=(f,))``)
    holds.
    """
    tables: Dict[str, Tuple[Tuple[str, Optional[str]], ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = [t.id for t in node.targets if isinstance(t, ast.Name)], node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            targets, value = [node.target.id], node.value
        else:
            continue
        bound = locally_bound_names(value)
        held = tuple(dict.fromkeys(
            (inner.id, None) if isinstance(inner, ast.Name) else (inner.attr, receiver_name(inner.value))
            for inner in ast.walk(value)
            if (isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load) and inner.id not in bound)
            or (isinstance(inner, ast.Attribute) and isinstance(inner.ctx, ast.Load)
                and receiver_name(inner.value) != NON_NAME_RECEIVER)
        ))
        tables.update((target, held) for target in targets)
    return tables


def record_definitions(tree: ast.Module) -> Dict[str, Tuple[str, ...]]:
    """
    Pure: Record types the module defines, mapped to their field names in
    order: a class with annotated fields (NamedTuple, dataclass) or
    ``namedtuple('X', 'a b')`` / ``NamedTuple('X', [('a', T), ...])``.
    """
    def named_fields(spec: ast.expr) -> Tuple[str, ...]:
        if isinstance(spec, ast.Constant) and isinstance(spec.value, str):
            return tuple(spec.value.replace(',', ' ').split())
        if isinstance(spec, (ast.List, ast.Tuple)):
            return tuple(
                item.value if isinstance(item, ast.Constant) else item.elts[0].value
                for item in spec.elts
                if (isinstance(item, ast.Constant) and isinstance(item.value, str))
                or (isinstance(item, (ast.Tuple, ast.List)) and item.elts
                    and isinstance(item.elts[0], ast.Constant) and isinstance(item.elts[0].value, str))
            )
        return ()

    records: Dict[str, Tuple[str, ...]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            fields = tuple(s.target.id for s in node.body
                           if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name))
            if fields:
                records[node.name] = fields
        elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
              and len(node.value.args) >= 2):
            func = node.value.func
            factory = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
            if factory in ('namedtuple', 'NamedTuple'):
                fields = named_fields(node.value.args[1])
                records.update((t.id, fields) for t in node.targets if isinstance(t, ast.Name) and fields)
    return records


def value_fields_in(
    tree: ast.Module,
    record_fields: Callable[[str, Optional[str]], Tuple[str, ...]] = lambda name, base: (),
) -> Tuple[Tuple[str, str, Optional[str]], ...]:
    """
    Pure: (field, name, base) for every function-shaped value stored under a
    field name: a keyword argument (``Rec(touches=removed_connectors)``), a
    positional argument of a known record type (``Codec(split, decode)`` with
    record_fields('Codec', None) == ('split', 'decode')), or a string-keyed
    dict entry (``{'touches': removed_connectors}``). The field is what a
    caller later calls through (``c.touches(...)``). A call stored under a
    field holds its callee (a factory returning a closure) and its positional
    arguments (``secret_command=partial(secret_command, installed=x)``).

    Args:
        tree: Parsed module AST
        record_fields: (callee name, base) -> the record's field names, or ()
            when the callee is not a known record type
    """
    found: List[Tuple[str, str, Optional[str]]] = []

    def held(field_name: str, value: ast.expr) -> None:
        if isinstance(value, ast.Name):
            found.append((field_name, value.id, None))
        elif isinstance(value, ast.Attribute) and receiver_name(value.value) != NON_NAME_RECEIVER:
            found.append((field_name, value.attr, receiver_name(value.value)))
        elif isinstance(value, ast.Call):
            # partial(fn, ...) holds fn; a factory call (touching_files(...))
            # holds the closure it returns, so the factory itself
            for part in (value.func, *value.args):
                if not isinstance(part, ast.Call):
                    held(field_name, part)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg:
                    held(keyword.arg, keyword.value)
            if node.args and isinstance(node.func, (ast.Name, ast.Attribute)):
                callee = ((node.func.id, None) if isinstance(node.func, ast.Name)
                          else (node.func.attr, receiver_name(node.func.value)))
                for field_name, argument in zip(record_fields(*callee), node.args):
                    if isinstance(argument, ast.Starred):
                        break   # positions after *args are unknown
                    held(field_name, argument)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    held(key.value, value)
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True)
class DispatchEvidence:
    """Edges the code makes through data rather than by name.

    table_edges: (source, target) where the source reads a module-level table
    (same file, or imported) that holds the target. value_fields: target id ->
    the field names it is stored under anywhere in the project.
    """
    table_edges: FrozenSet[Tuple[int, int]] = frozenset()
    value_fields: Dict[int, FrozenSet[str]] = field(default_factory=dict)


def dispatch_evidence(
    context: ResolutionContext,
    trees: Dict[str, ast.Module],
    include_caller: Callable[[str], bool],
    max_depth: int = 3,
) -> DispatchEvidence:
    """
    Pure: Dispatch-table edges for accepted callers, and every function's
    record fields across all trees.

    A table read is credited like any reference (own body, or an untracked
    same-file helper it reaches); a table holding another table is followed
    up to max_depth. Table contents resolve in the table's own file, never by
    a project-wide name guess. Confirmation only: these are not expected
    edges, so an audit never asks for one to be added.
    """
    tables = {path: module_tables(tree) for path, tree in trees.items()}

    def table_file(path: str, name: str, base: Optional[str]) -> Optional[str]:
        """The file whose module-level table `name` (read in `path`) is, if any."""
        bindings = context.bindings_index.get(path, {})
        if base is None and name in tables.get(path, {}) and name not in bindings:
            return path
        module = bindings.get(name) if base is None else (
            bindings.get(base) if '.' not in base else None)
        if module is None:
            return None
        exact = (module + '.py', os.path.join(module, '__init__.py'))
        matched = tuple(c for c in exact if c in tables) or context.modules.get(module, ())
        return matched[0] if len(matched) == 1 and name in tables.get(matched[0], {}) else None

    def held_ids(path: str, name: str, depth: int, seen: Set[Tuple[str, str]]) -> Set[int]:
        if depth > max_depth or (path, name) in seen:
            return set()
        seen.add((path, name))
        ids: Set[int] = set()
        for held_name, held_base in tables[path][name]:
            target_id, _ = resolve_in(context, path, held_name, held_base)
            if target_id is not None:
                ids.add(target_id)
            inner = table_file(path, held_name, held_base)
            if inner is not None:
                ids |= held_ids(inner, held_name, depth + 1, seen)
        return ids

    table_edges: Set[Tuple[int, int]] = set()
    for path, tree in trees.items():
        if not include_caller(path):
            continue
        local_functions = context.by_file.get(path, {})
        for caller, dependencies in credited_dependencies(tree, frozenset(local_functions)).items():
            source_id = local_functions[caller]
            for name, base, via in dependencies:
                if not via.startswith('reference'):
                    continue
                where = table_file(path, name, base)
                if where is not None:
                    table_edges.update((source_id, t) for t in held_ids(where, name, 0, set()) if t != source_id)

    # Record types, resolved like functions (through imports and re-exports)
    records = {path: record_definitions(tree) for path, tree in trees.items()}
    record_keys = tuple((path, name) for path, defined in records.items() for name in defined)
    key_ids = {key: i for i, key in enumerate(record_keys)}
    record_context = ResolutionContext(
        by_file={path: {name: key_ids[(path, name)] for name in defined}
                 for path, defined in records.items() if defined},
        by_name={},
        id_to_path={},
        bindings_index=context.bindings_index,
        origins_index=context.origins_index,
        modules=context.modules,
    )

    def fields_of(path: str) -> Callable[[str, Optional[str]], Tuple[str, ...]]:
        def lookup(name: str, base: Optional[str]) -> Tuple[str, ...]:
            found, _ = resolve_in(record_context, path, name, base)
            return () if found is None else records[record_keys[found][0]][record_keys[found][1]]
        return lookup

    value_fields: Dict[int, Set[str]] = {}
    for path, tree in trees.items():
        for field_name, name, base in value_fields_in(tree, fields_of(path)):
            target_id, _ = resolve_in(context, path, name, base)
            if target_id is not None:
                value_fields.setdefault(target_id, set()).add(field_name)

    return DispatchEvidence(
        table_edges=frozenset(table_edges),
        value_fields={k: frozenset(v) for k, v in value_fields.items()},
    )


def _effect_parse_python_files(
    project_root: str,
    paths: Tuple[str, ...],
) -> Dict[str, ast.Module]:
    """Effect: Parse every readable Python file among paths (unparseable ones skipped)."""
    trees: Dict[str, ast.Module] = {}
    for path in sorted(paths):
        if detect_language(path) != 'python':
            continue
        source = _effect_read_source(os.path.join(project_root, path))
        if source is None:
            continue
        try:
            trees[path] = ast.parse(source)
        except SyntaxError:
            continue
    return trees


# ============================================================================
# Public Tool
# ============================================================================

def scan_call_graph(
    module_path: Optional[str] = None,
    project_root: Optional[str] = None,
) -> CallGraphResult:
    """
    Build the interaction graph for already-cataloged functions.

    Read-only. Returns tuples shaped for add_interactions — call that to write them.
    Only functions already present in project.db participate, so run this after
    catalog_files and catalog_functions.

    Edges are emitted only when the target resolves unambiguously; everything else
    is counted in unresolved_count rather than guessed at.

    Args:
        module_path: Project-relative subtree to limit analysis to. Callers outside
            it still resolve as targets — only the calling side is narrowed.
        project_root: Project root override (defaults to the cached root)

    Returns:
        CallGraphResult whose 'interactions' can be passed straight to
        add_interactions
    """
    project_root = project_root or get_cached_project_root()

    try:
        tracked = _effect_tracked_functions(project_root)
        if not tracked[0]:
            return CallGraphResult(
                success=False,
                error="No tracked functions found. Run catalog_files and catalog_functions first.",
            )

        prefix = module_path.strip('/') if module_path else None
        found = build_call_edges(
            project_root, tracked,
            lambda path: not prefix or path.startswith(prefix),
        )

        return CallGraphResult(
            success=True,
            interactions=found.edges,
            edge_count=len(found.edges),
            resolved_by=found.resolved_by,
            unresolved_count=len(found.unresolved),
            unresolved_sample=found.unresolved[:20],
            files_analyzed=found.files_analyzed,
            return_statements=get_return_statements("scan_call_graph"),
        )

    except Exception as exc:
        return CallGraphResult(success=False, error=f"Call graph scan failed: {str(exc)}")
