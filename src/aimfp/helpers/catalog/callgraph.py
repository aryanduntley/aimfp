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
4. the name is unique across the entire project

Anything still ambiguous is dropped and counted in ``unresolved``. A wrong edge is
worse than a missing one: it sends future refactors chasing dependencies that do
not exist.

Writes nothing. It returns tuples shaped for ``add_interactions``, so the existing
batch tool does the writing — cataloged rows are not special, and the linking
surface stays the ordinary one.
"""

import ast
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from ..utils import get_return_statements, get_cached_project_root, _open_project_connection
from ...watchdog.config import detect_language
from .scan import _effect_read_source


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
    and ``import pkg.mod as alias`` (binding the module itself). Absolute imports
    outside the project resolve to paths that simply never match a tracked file,
    which is the desired outcome — third-party calls are not project interactions.

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
                target = os.path.join('src', *node.module.split('.'))
            else:
                continue
            for alias in node.names:
                bindings[alias.asname or alias.name] = target

        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = os.path.join('src', *alias.name.split('.'))

    return bindings


def extract_function_calls(tree: ast.Module) -> Dict[str, Tuple[Tuple[str, Optional[str]], ...]]:
    """
    Pure: Map each top-level function to the calls made inside it.

    Each call is recorded as (name, base) — ``base`` is the receiver for
    attribute-style calls (``mod.fn()`` yields ('fn', 'mod')) and None for bare
    calls (``fn()`` yields ('fn', None)).

    Args:
        tree: Parsed module AST

    Returns:
        Dict of enclosing function name -> tuple of (called name, base) pairs
    """
    calls: Dict[str, Tuple[Tuple[str, Optional[str]], ...]] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        found: List[Tuple[str, Optional[str]]] = []
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Name):
                found.append((func.id, None))
            elif isinstance(func, ast.Attribute):
                base = func.value.id if isinstance(func.value, ast.Name) else None
                found.append((func.attr, base))

        calls[node.name] = tuple(dict.fromkeys(found))

    return calls


def resolve_target(
    name: str,
    base: Optional[str],
    current_path: str,
    bindings: Dict[str, str],
    by_file: Dict[str, Dict[str, int]],
    by_name: Dict[str, List[int]],
    bindings_index: Optional[Dict[str, Dict[str, str]]] = None,
    max_hops: int = 3,
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

    Returns:
        (function id or None, reason string)
    """
    index = bindings_index or {}

    def lookup(path_prefix: str, hops: int = 0) -> Tuple[Optional[int], bool]:
        """Resolve name within a module path, following re-exports. Returns
        (id, went_through_reexport)."""
        for candidate in (path_prefix + '.py', os.path.join(path_prefix, '__init__.py')):
            table = by_file.get(candidate)
            if table and name in table:
                return (table[name], hops > 0)
            # The module exists but does not define the name — it may re-export it.
            if hops < max_hops and candidate in index:
                onward = index[candidate].get(name)
                if onward and onward != path_prefix:
                    found, _ = lookup(onward, hops + 1)
                    if found is not None:
                        return (found, True)
        return (None, False)

    if base is None and name in bindings:
        found, via_reexport = lookup(bindings[name])
        if found is not None:
            return (found, 'reexport' if via_reexport else 'import')

    if base is None:
        local = by_file.get(current_path, {})
        if name in local:
            return (local[name], 'local')

    if base is not None and base in bindings:
        found, via_reexport = lookup(bindings[base])
        if found is not None:
            return (found, 'reexport' if via_reexport else 'module_alias')

    candidates = by_name.get(name, [])
    if len(candidates) == 1:
        return (candidates[0], 'unique_name')

    return (None, 'ambiguous' if candidates else 'external')


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_tracked_functions(project_root: str) -> Tuple[Dict[str, Dict[str, int]], Dict[str, List[int]], Dict[int, str], Tuple[str, ...]]:
    """
    Effect: Load every tracked function and every tracked file path, indexed for
    resolution.

    All file paths are returned, not just those owning functions: re-export
    modules (a package's ``utils.py`` or ``__init__.py``) define no functions of
    their own, yet they are exactly the files an import chain has to pass through.
    Indexing only function-bearing files makes every call routed through a
    re-export unresolvable.

    Args:
        project_root: Project root directory

    Returns:
        (by_file, by_name, id_to_path, all_paths) lookup tables
    """
    conn = _open_project_connection(project_root)
    try:
        rows = conn.execute(
            """
            SELECT fn.id, fn.name, f.path
            FROM functions fn JOIN files f ON f.id = fn.file_id
            """
        ).fetchall()
        path_rows = conn.execute("SELECT path FROM files").fetchall()
    finally:
        conn.close()

    by_file: Dict[str, Dict[str, int]] = {}
    by_name: Dict[str, List[int]] = {}
    id_to_path: Dict[int, str] = {}

    for row in rows:
        by_file.setdefault(row["path"], {})[row["name"]] = row["id"]
        by_name.setdefault(row["name"], []).append(row["id"])
        id_to_path[row["id"]] = row["path"]

    return (by_file, by_name, id_to_path, tuple(r["path"] for r in path_rows))


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
        by_file, by_name, id_to_path, all_paths = _effect_tracked_functions(project_root)
        if not by_file:
            return CallGraphResult(
                success=False,
                error="No tracked functions found. Run catalog_files and catalog_functions first.",
            )

        prefix = module_path.strip('/') if module_path else None
        edges: List[Tuple[Any, ...]] = []
        seen: Set[Tuple[int, int]] = set()
        reasons: Dict[str, int] = {}
        unresolved: List[str] = []

        # Pass 1: parse every tracked Python file and index its import bindings.
        # The whole index must exist before resolution, because following a
        # re-export needs the bindings of a file that may not be analyzed yet —
        # and re-export targets sit outside module_path more often than not.
        trees: Dict[str, ast.Module] = {}
        bindings_index: Dict[str, Dict[str, str]] = {}

        for path in sorted(all_paths):
            if detect_language(path) != 'python':
                continue
            source = _effect_read_source(os.path.join(project_root, path))
            if source is None:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            trees[path] = tree
            bindings_index[path] = extract_import_bindings(tree, path)

        # Pass 2: resolve calls, narrowed to module_path on the calling side only.
        analyzed = 0
        for path, tree in sorted(trees.items()):
            if prefix and not path.startswith(prefix):
                continue

            analyzed += 1
            bindings = bindings_index[path]
            local_functions = by_file.get(path, {})

            for caller, calls in extract_function_calls(tree).items():
                source_id = local_functions.get(caller)
                if source_id is None:
                    continue

                for name, base in calls:
                    target_id, reason = resolve_target(
                        name, base, path, bindings, by_file, by_name, bindings_index
                    )
                    if target_id is None:
                        if reason == 'ambiguous':
                            unresolved.append(f"{path}:{caller} -> {name}")
                        continue
                    if target_id == source_id or (source_id, target_id) in seen:
                        continue

                    seen.add((source_id, target_id))
                    reasons[reason] = reasons.get(reason, 0) + 1
                    edges.append((
                        source_id,
                        target_id,
                        'call',
                        f"{caller} calls {name} ({os.path.basename(id_to_path[target_id])})",
                    ))

        return CallGraphResult(
            success=True,
            interactions=tuple(edges),
            edge_count=len(edges),
            resolved_by=reasons,
            unresolved_count=len(unresolved),
            unresolved_sample=tuple(unresolved[:20]),
            files_analyzed=analyzed,
            return_statements=get_return_statements("scan_call_graph"),
        )

    except Exception as exc:
        return CallGraphResult(success=False, error=f"Call graph scan failed: {str(exc)}")
