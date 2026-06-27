"""
AIMFP Helper Functions - Plan Disjoint Fan-out Partitions

`plan_disjoint_partitions(targets=None, n_workers=None)` — compute disjoint file/module
partitions so parallel workers don't overlap. Disjoint partitioning is "the cheap
complexity lever" (toolsneeded.md §7): it converts most semantic conflicts into non-events.
Only AIMFP can compute it well, because only AIMFP owns the function->function interaction
graph and the type-usage edges that define real dependency boundaries.

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

Pure read over project.db — never mutates. A planning aid: the master may adopt, tweak,
or override the proposed ownership sets.

Method:
  1. Resolve `targets` to a candidate file set (file paths, module names, or task/milestone
     slugs via their flows). No targets -> every tracked file.
  2. Build an undirected file-coupling graph: an edge between two files when a function in
     one interacts with a function in the other, when both share a type usage, or when both
     belong to the same module. Coupled files MUST land with the same worker.
  3. Connected components are the atomic ownership units (never split — splitting one is the
     only way to create a real conflict).
  4. Greedily bin-pack whole components into `n_workers` bins (largest first), balancing file
     count. Without `n_workers`, one partition per component.
"""

import sqlite3
from typing import Dict, List, Optional, Set, Any

from ..utils import (
    Result,
    resolve_project_root,
    get_project_db_path,
    database_exists,
    _open_connection,
    get_return_statements,
)


def _safe(conn: sqlite3.Connection, sql: str, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


class _DSU:
    """Disjoint-set union over file ids (connected-component finder)."""

    def __init__(self, ids):
        self._parent = {i: i for i in ids}

    def find(self, x):
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a, b):
        if a in self._parent and b in self._parent:
            self._parent[self.find(a)] = self.find(b)


def _resolve_target_files(conn, targets: Optional[List[str]]) -> Set[int]:
    """Effect: map targets (paths | module names | task/milestone slugs) to file ids."""
    all_files = {r["path"]: r["id"] for r in _safe(conn, "SELECT id, path FROM files")}
    if not targets:
        return set(all_files.values())

    selected: Set[int] = set()
    module_name_to_id = {r["name"]: r["id"] for r in _safe(conn, "SELECT id, name FROM modules")}

    for t in targets:
        if t in all_files:                       # exact file path
            selected.add(all_files[t])
            continue
        if t in module_name_to_id:               # module name -> its files
            for r in _safe(conn, "SELECT file_id FROM module_files WHERE module_id = ?",
                           (module_name_to_id[t],)):
                selected.add(r["file_id"])
            continue
        # task/milestone slug -> flows -> files (best-effort)
        flow_ids: Set[int] = set()
        for tbl in ("tasks", "sidequests"):
            rows = _safe(conn, f"SELECT flow_ids FROM {tbl} WHERE slug = ?", (t,))
            for r in rows:
                raw = r["flow_ids"]
                if raw:
                    try:
                        import json as _json
                        flow_ids.update(_json.loads(raw))
                    except (ValueError, TypeError):
                        pass
        for fid in flow_ids:
            for r in _safe(conn, "SELECT file_id FROM file_flows WHERE flow_id = ?", (fid,)):
                selected.add(r["file_id"])

    return selected


def plan_disjoint_partitions(
    targets: Optional[List[str]] = None,
    n_workers: Optional[int] = None,
) -> Result:
    """
    Propose disjoint per-worker file/module ownership sets.

    Args:
        targets: file paths, module names, or task/milestone slugs to partition. Omit to
            partition every tracked file.
        n_workers: number of partitions to pack components into. Omit for one partition
            per connected component.

    Returns:
        Result with data={partitions[], shared_files[], component_count, file_count, note?}.
        partitions[]: {worker_index, files[], modules[], file_count}.
        shared_files[]: files in >1 module (ownership ambiguity to resolve before fan-out).
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    db_path = get_project_db_path(project_root)
    if not database_exists(db_path):
        return Result(success=False, error="Project database not found")

    conn = _open_connection(db_path)
    try:
        file_ids = _resolve_target_files(conn, targets)
        if not file_ids:
            return Result(success=True, data={
                "partitions": [], "shared_files": [], "component_count": 0,
                "file_count": 0, "note": "No files matched the targets.",
            }, return_statements=get_return_statements("plan_disjoint_partitions"))

        id_to_path = {r["id"]: r["path"]
                      for r in _safe(conn, "SELECT id, path FROM files")
                      if r["id"] in file_ids}
        fn_to_file = {r["id"]: r["file_id"]
                      for r in _safe(conn, "SELECT id, file_id FROM functions")}

        dsu = _DSU(file_ids)

        # interaction edges: function->function lifted to file<->file
        for r in _safe(conn, "SELECT source_function_id, target_function_id FROM interactions"):
            a = fn_to_file.get(r["source_function_id"])
            b = fn_to_file.get(r["target_function_id"])
            if a in file_ids and b in file_ids and a != b:
                dsu.union(a, b)

        # type-usage edges: a type's file <-> each using function's file
        type_to_file = {r["id"]: r["file_id"]
                        for r in _safe(conn, "SELECT id, file_id FROM types")}
        for r in _safe(conn, "SELECT type_id, function_id FROM types_functions"):
            a = type_to_file.get(r["type_id"])
            b = fn_to_file.get(r["function_id"])
            if a in file_ids and b in file_ids and a != b:
                dsu.union(a, b)

        # module co-membership: files in the same module belong together
        mod_files: Dict[int, List[int]] = {}
        file_modules: Dict[int, Set[str]] = {}
        mod_id_to_name = {r["id"]: r["name"] for r in _safe(conn, "SELECT id, name FROM modules")}
        for r in _safe(conn, "SELECT module_id, file_id FROM module_files"):
            if r["file_id"] in file_ids:
                mod_files.setdefault(r["module_id"], []).append(r["file_id"])
                file_modules.setdefault(r["file_id"], set()).add(
                    mod_id_to_name.get(r["module_id"], str(r["module_id"])))
        for members in mod_files.values():
            for other in members[1:]:
                dsu.union(members[0], other)
    finally:
        conn.close()

    # group files into components
    components: Dict[int, List[int]] = {}
    for fid in file_ids:
        components.setdefault(dsu.find(fid), []).append(fid)
    comp_list = sorted(components.values(), key=len, reverse=True)

    # bin-pack components into n_workers bins (largest-first), or one per component
    n_bins = n_workers if (n_workers and n_workers > 0) else len(comp_list)
    n_bins = max(1, min(n_bins, len(comp_list)))
    bins: List[List[int]] = [[] for _ in range(n_bins)]
    for comp in comp_list:
        target = min(range(n_bins), key=lambda i: len(bins[i]))
        bins[target].extend(comp)

    partitions: List[Dict[str, Any]] = []
    for i, members in enumerate(bins):
        modules: Set[str] = set()
        for fid in members:
            modules.update(file_modules.get(fid, set()))
        partitions.append({
            "worker_index": i,
            "files": sorted(id_to_path.get(fid, str(fid)) for fid in members),
            "modules": sorted(modules),
            "file_count": len(members),
        })

    shared_files = sorted(
        id_to_path.get(fid, str(fid))
        for fid, mods in file_modules.items() if len(mods) > 1
    )

    note = None
    if len(comp_list) == 1 and len(file_ids) > 1:
        note = ("All target files are transitively coupled into ONE component — they cannot "
                "be split without creating cross-worker dependencies. Give this region to a "
                "single worker, or break the coupling first.")
    elif n_workers and n_workers > len(comp_list):
        note = (f"Requested {n_workers} workers but only {len(comp_list)} disjoint components "
                f"exist; produced {len(comp_list)} partitions (one per component).")

    return Result(
        success=True,
        data={
            "partitions": partitions,
            "shared_files": shared_files,
            "component_count": len(comp_list),
            "file_count": len(file_ids),
            "note": note,
        },
        return_statements=get_return_statements("plan_disjoint_partitions"),
    )
