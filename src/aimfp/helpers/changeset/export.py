"""
AIMFP Helper Functions - Export Semantic Changeset

`export_state_changeset(base_commit, branch, worker_id=None)` — pure, schema-aware diff of
project.db between two committed git states (the branch point and the branch tip), expressed
entirely in stable semantic keys (never branch-local integer PKs). This is the artifact
InterCommAIMFP's master applies to integrate a worker's project-state changes.

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

Pure read: both DB states are extracted from committed blobs via `git show` (immutable), so
this never mutates anything. Rows that still lack a slug surface as warnings (not failures),
directing the operator to run `backfill_semantic_keys` on main and re-commit.
"""

import json
from typing import Dict, List, Tuple, Any, Optional

from ..utils import (
    Result,
    resolve_project_root,
    get_return_statements,
)
from ._common import (
    _effect_extract_db_at_commit,
    _open_readonly,
    build_key_indexes,
    serialize_key,
    key_has_null,
    code_entity_key,
    _row_get,
    _safe_rows,
    changeset_id_for,
    _effect_persist_changeset,
    summarize_changeset,
)


# Entity kinds in dependency order (parents before children).
_ENTITY_KINDS: Tuple[str, ...] = (
    "files", "modules", "themes", "flows", "completion_path",
    "functions", "types",
    "milestones", "tasks", "subtasks", "sidequests", "items",
)


# ============================================================================
# Attribute extraction (mutable state per entity; PKs/FK-ints excluded)
# ============================================================================

def _flow_ids_to_names(raw: Optional[str], idx: Dict[str, Any]) -> List[str]:
    """Pure-ish: translate a JSON array of branch-local flow ids into flow names."""
    if not raw:
        return []
    try:
        ids = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    id2key = idx["flows"]["id2key"]
    names = []
    for fid in ids:
        k = id2key.get(fid)
        if k:
            names.append(k["name"])
    return names


def _collect_entities(conn, idx: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    Effect: Read every keyed entity from a project.db into
    ``{kind: {serialized_key: {"key": keydict, "attrs": {...}}}}``.

    Attributes are mutable state only; identity (key) and FK ints are excluded — FK parents
    are re-expressed as semantic-key refs inside attributes.
    """
    out: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in _ENTITY_KINDS}
    if conn is None:
        return out

    def add(kind, key, attrs):
        out[kind][serialize_key(key)] = {"key": key, "attrs": attrs}

    for r in _safe_rows(conn, "SELECT * FROM files"):
        add("files", {"path": r["path"]},
            {"name": r["name"], "language": r["language"], "id_in_name": r["id_in_name"]})

    for r in _safe_rows(conn, "SELECT * FROM modules"):
        add("modules", {"name": r["name"]},
            {"path": r["path"], "description": r["description"], "purpose": r["purpose"],
             "external_dependencies": r["external_dependencies"]})

    for r in _safe_rows(conn, "SELECT * FROM themes"):
        add("themes", {"name": r["name"]},
            {"description": r["description"], "ai_generated": r["ai_generated"],
             "confidence_score": r["confidence_score"]})

    for r in _safe_rows(conn, "SELECT * FROM flows"):
        add("flows", {"name": r["name"]},
            {"description": r["description"], "ai_generated": r["ai_generated"],
             "confidence_score": r["confidence_score"]})

    for r in _safe_rows(conn, "SELECT * FROM completion_path"):
        add("completion_path", {"name": r["name"]},
            {"order_index": r["order_index"], "status": r["status"], "description": r["description"]})

    files_id2key = idx["files"]["id2key"]

    def file_ref(file_id):
        return files_id2key.get(file_id) if file_id is not None else None

    for r in _safe_rows(conn, "SELECT * FROM functions"):
        fref = file_ref(r["file_id"])
        fpath = fref["path"] if fref else None
        add("functions", code_entity_key(fpath, r["name"], _row_get(r, "entity_key")),
            {"file": fpath, "name": r["name"], "purpose": r["purpose"],
             "parameters": r["parameters"], "returns": r["returns"], "id_in_name": r["id_in_name"]})

    for r in _safe_rows(conn, "SELECT * FROM types"):
        fref = file_ref(r["file_id"])
        fpath = fref["path"] if fref else None
        add("types", code_entity_key(fpath, r["name"], _row_get(r, "entity_key")),
            {"file": fpath, "name": r["name"], "definition_json": r["definition_json"],
             "description": r["description"], "links": r["links"], "id_in_name": r["id_in_name"]})

    cp_id2key = idx["completion_path"]["id2key"]
    for r in _safe_rows(conn, "SELECT * FROM milestones"):
        add("milestones", {"slug": r["slug"]},
            {"name": r["name"], "status": r["status"], "description": r["description"],
             "parent": cp_id2key.get(r["completion_path_id"])})

    ms_id2key = idx["milestones"]["id2key"]
    for r in _safe_rows(conn, "SELECT * FROM tasks"):
        add("tasks", {"slug": r["slug"]},
            {"name": r["name"], "status": r["status"], "priority": r["priority"],
             "description": r["description"], "flows": _flow_ids_to_names(r["flow_ids"], idx),
             "parent": ms_id2key.get(r["milestone_id"])})

    task_id2key = idx["tasks"]["id2key"]
    for r in _safe_rows(conn, "SELECT * FROM subtasks"):
        add("subtasks", {"slug": r["slug"]},
            {"name": r["name"], "status": r["status"], "priority": r["priority"],
             "description": r["description"], "parent": task_id2key.get(r["parent_task_id"])})

    subtask_id2key = idx["subtasks"]["id2key"]
    for r in _safe_rows(conn, "SELECT * FROM sidequests"):
        paused_sub = r["paused_subtask_id"]
        add("sidequests", {"slug": r["slug"]},
            {"name": r["name"], "status": r["status"], "priority": r["priority"],
             "description": r["description"], "flows": _flow_ids_to_names(r["flow_ids"], idx),
             "parent": task_id2key.get(r["paused_task_id"]),
             "paused_subtask": subtask_id2key.get(paused_sub) if paused_sub is not None else None})

    # items: polymorphic parent (reference_table in tasks/subtasks/sidequests)
    for r in _safe_rows(conn, "SELECT * FROM items"):
        rt = r["reference_table"]
        parent_key = idx.get(rt, {}).get("id2key", {}).get(r["reference_id"])
        add("items", {"slug": r["slug"]},
            {"name": r["name"], "status": r["status"], "description": r["description"],
             "parent": {"reference_table": rt, "key": parent_key}})

    return out


# ============================================================================
# Reference (edge) extraction
# ============================================================================

def _collect_references(conn, idx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Effect: Read every edge into ``{identity: refdict}``. Identity = the refdict minus
    its optional 'attributes' field, serialized. Endpoints are semantic keys.
    """
    refs: Dict[str, Dict[str, Any]] = {}
    if conn is None:
        return refs

    fn = idx["functions"]["id2key"]
    ty = idx["types"]["id2key"]
    fl = idx["flows"]["id2key"]
    fi = idx["files"]["id2key"]
    mo = idx["modules"]["id2key"]
    th = idx["themes"]["id2key"]

    def put(ref):
        identity = {k: v for k, v in ref.items() if k != "attributes"}
        refs[serialize_key(identity)] = ref

    for r in _safe_rows(conn, "SELECT * FROM interactions"):
        put({"kind": "interaction",
             "from": fn.get(r["source_function_id"]), "to": fn.get(r["target_function_id"]),
             "interaction_type": r["interaction_type"],
             "attributes": {"description": r["description"]}})

    for r in _safe_rows(conn, "SELECT * FROM types_functions"):
        put({"kind": "type_usage",
             "type": ty.get(r["type_id"]), "function": fn.get(r["function_id"]),
             "role": r["role"]})

    for r in _safe_rows(conn, "SELECT * FROM file_flows"):
        put({"kind": "file_flow", "file": fi.get(r["file_id"]), "flow": fl.get(r["flow_id"])})

    for r in _safe_rows(conn, "SELECT * FROM module_files"):
        put({"kind": "module_file", "module": mo.get(r["module_id"]), "file": fi.get(r["file_id"])})

    for r in _safe_rows(conn, "SELECT * FROM flow_themes"):
        put({"kind": "flow_theme", "flow": fl.get(r["flow_id"]), "theme": th.get(r["theme_id"])})

    return refs


# ============================================================================
# Diff
# ============================================================================

def _diff_entities(base, branch) -> Tuple[List[dict], List[str]]:
    """Pure: classify add/modify/delete by semantic key; collect null-key warnings."""
    out: List[dict] = []
    warnings: List[str] = []

    for kind in _ENTITY_KINDS:
        b = base.get(kind, {})
        h = branch.get(kind, {})
        for kser, ent in h.items():
            if kser not in b:
                op = "add"
            elif ent["attrs"] != b[kser]["attrs"]:
                op = "modify"
            else:
                continue
            out.append({"kind": kind, "semantic_key": ent["key"], "op": op,
                        "attributes": ent["attrs"]})
            if key_has_null(ent["key"]):
                warnings.append(f"{kind} {op} has an unresolved (null) semantic key: {ent['key']}")
        for kser, ent in b.items():
            if kser not in h:
                out.append({"kind": kind, "semantic_key": ent["key"], "op": "delete"})
                if key_has_null(ent["key"]):
                    warnings.append(f"{kind} delete has an unresolved (null) semantic key: {ent['key']}")

    return out, warnings


def _diff_references(base, branch) -> Tuple[List[dict], List[str]]:
    """Pure: edges added in branch (op=add) / missing from branch (op=remove)."""
    out: List[dict] = []
    warnings: List[str] = []

    for ident, ref in branch.items():
        if ident not in base:
            out.append({**ref, "op": "add"})
    for ident, ref in base.items():
        if ident not in branch:
            out.append({**ref, "op": "remove"})

    for ref in out:
        endpoints = [v for k, v in ref.items() if k not in ("kind", "op", "attributes",
                                                             "interaction_type", "role")]
        if any(ep is None or key_has_null(ep) for ep in endpoints if isinstance(ep, dict) or ep is None):
            warnings.append(f"{ref.get('kind')} {ref.get('op')} edge has an unresolved endpoint")

    return out, warnings


# ============================================================================
# Public tool
# ============================================================================

def export_state_changeset(
    base_commit: str,
    branch: str,
    worker_id: Optional[str] = None,
) -> Result:
    """
    Export a semantic changeset of project.db from `base_commit` to the tip of `branch`.

    Args:
        base_commit: Git commit the branch was based on (the 3-way base / branch point)
        branch: Git ref (branch name or commit) whose project.db is the branch intent
        worker_id: Optional InterCommAIMFP worker identity (master supplies it; default None)

    Returns:
        Result with data={provenance, entities[], references[], warnings[]}.
        Pure read — never mutates the repo or any DB.
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    base_path = _effect_extract_db_at_commit(project_root, base_commit)
    branch_path = _effect_extract_db_at_commit(project_root, branch)

    if branch_path is None:
        if base_path:
            import os
            try:
                os.remove(base_path)
            except OSError:
                pass
        return Result(
            success=False,
            error=f"Could not read project.db at branch '{branch}'. Is it committed there?",
        )

    base_conn = _open_readonly(base_path) if base_path else None
    branch_conn = _open_readonly(branch_path)
    warnings: List[str] = []
    if base_path is None:
        warnings.append(
            f"project.db not found at base_commit '{base_commit}' — treating base as empty "
            f"(all entities will appear as additions)."
        )

    try:
        base_idx = build_key_indexes(base_conn)
        branch_idx = build_key_indexes(branch_conn)
        base_entities = _collect_entities(base_conn, base_idx)
        branch_entities = _collect_entities(branch_conn, branch_idx)
        entities, ent_warn = _diff_entities(base_entities, branch_entities)
        base_refs = _collect_references(base_conn, base_idx)
        branch_refs = _collect_references(branch_conn, branch_idx)
        references, ref_warn = _diff_references(base_refs, branch_refs)
    finally:
        if base_conn is not None:
            base_conn.close()
        branch_conn.close()
        import os
        for p in (base_path, branch_path):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass

    warnings.extend(ent_warn)
    warnings.extend(ref_warn)
    if any("null" in w or "unresolved" in w for w in warnings):
        warnings.append(
            "Unresolved semantic keys detected. If these involve work-hierarchy rows, run "
            "backfill_semantic_keys on main and re-commit so every clone shares stable slugs."
        )

    changeset = {
        "provenance": {
            "worker_id": worker_id,
            "branch": branch,
            "base_main_commit": base_commit,
        },
        "entities": entities,
        "references": references,
        "warnings": warnings,
    }

    # §2.1 — persist the changeset server-side and return a small handle so the master
    # never has to re-transcribe the full object into apply_state_changeset. The id is a
    # pure function of (base_commit, branch), so re-exporting is idempotent. The full
    # object is STILL returned (back-compat / debugging); summary is the cheap counts block.
    changeset_id = changeset_id_for(base_commit, branch)
    backup_path = _effect_persist_changeset(project_root, changeset_id, changeset)
    summary = summarize_changeset(changeset)

    data = dict(changeset)
    data["changeset_id"] = changeset_id if backup_path else None
    data["summary"] = summary
    if backup_path is None:
        warnings.append(
            "Could not persist the changeset server-side; pass the inline object to "
            "apply_state_changeset (no changeset_id handle available)."
        )

    return Result(
        success=True,
        data=data,
        return_statements=get_return_statements("export_state_changeset"),
    )
