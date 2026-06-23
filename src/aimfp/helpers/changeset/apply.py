"""
AIMFP Helper Functions - Apply Semantic Changeset

`apply_state_changeset(changeset)` — 3-way semantic merge of a changeset (from
export_state_changeset) onto the CURRENT-MAIN project.db. This is how InterCommAIMFP's
master integrates a worker's project-state into main.

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

Model (decisions recorded in IMPLEMENTATION-PLAN.md §4):
- The working `.aimfp-project/project.db` IS current-main (master already text-merged source
  and checked out main). We mutate it in place after taking a backup.
- 3-way basis: base@provenance.base_main_commit (extracted via git show) vs branch intent
  (the changeset) vs current main (the working DB).
- Auto-apply non-overlapping changes; NEVER guess on a conflict — every conflict is returned
  as structured data for the master/AI to handle.
- New entities mint canonical IDs at apply; all references are rewritten to canonical IDs.
- A *failure* (exception) rolls back and restores the backup; a *conflict* is reported, not fatal.
"""

import json
import os
import shutil
import sqlite3
import tempfile
from typing import Dict, List, Any, Optional, Tuple

from ..utils import (
    Result,
    resolve_project_root,
    get_project_db_path,
    database_exists,
    _open_connection,
    get_return_statements,
)
from ._common import (
    _effect_extract_db_at_commit,
    _open_readonly,
    build_key_indexes,
    serialize_key,
)
from .export import _collect_entities, _ENTITY_KINDS


# Plain (non-relational) attribute -> column maps per kind.
_PLAIN_COLS: Dict[str, Dict[str, str]] = {
    "files": {"name": "name", "language": "language", "id_in_name": "id_in_name"},
    "modules": {"path": "path", "description": "description", "purpose": "purpose",
                "external_dependencies": "external_dependencies"},
    "themes": {"description": "description", "ai_generated": "ai_generated",
               "confidence_score": "confidence_score"},
    "flows": {"description": "description", "ai_generated": "ai_generated",
              "confidence_score": "confidence_score"},
    "completion_path": {"order_index": "order_index", "status": "status", "description": "description"},
    "functions": {"name": "name", "purpose": "purpose", "parameters": "parameters",
                  "returns": "returns", "id_in_name": "id_in_name"},
    "types": {"name": "name", "definition_json": "definition_json", "description": "description",
              "links": "links", "id_in_name": "id_in_name"},
    "milestones": {"name": "name", "status": "status", "description": "description"},
    "tasks": {"name": "name", "status": "status", "priority": "priority", "description": "description"},
    "subtasks": {"name": "name", "status": "status", "priority": "priority", "description": "description"},
    "sidequests": {"name": "name", "status": "status", "priority": "priority", "description": "description"},
    "items": {"name": "name", "status": "status", "description": "description"},
}

# Inbound-dependent (table, fk_column) per kind, for safe-delete checks.
_DEPENDENTS: Dict[str, List[Tuple[str, str]]] = {
    "files": [("functions", "file_id"), ("types", "file_id"),
              ("module_files", "file_id"), ("file_flows", "file_id")],
    "functions": [("interactions", "source_function_id"), ("interactions", "target_function_id"),
                  ("types_functions", "function_id")],
    "types": [("types_functions", "type_id")],
    "modules": [("module_files", "module_id")],
    "themes": [("flow_themes", "theme_id")],
    "flows": [("file_flows", "flow_id"), ("flow_themes", "flow_id")],
    "completion_path": [("milestones", "completion_path_id")],
    "milestones": [("tasks", "milestone_id")],
    "tasks": [("subtasks", "parent_task_id"), ("sidequests", "paused_task_id")],
    "subtasks": [("sidequests", "paused_subtask_id")],
}


class _Resolver:
    """Live semantic-key -> canonical-id maps, seeded from main and grown as we insert."""

    def __init__(self, conn: sqlite3.Connection):
        idx = build_key_indexes(conn)
        self._k2i: Dict[str, Dict[str, int]] = {k: dict(idx[k]["key2id"]) for k in idx}

    def get(self, kind: str, key: Optional[Dict[str, Any]]) -> Optional[int]:
        if key is None:
            return None
        return self._k2i.get(kind, {}).get(serialize_key(key))

    def put(self, kind: str, key: Dict[str, Any], row_id: int) -> None:
        self._k2i.setdefault(kind, {})[serialize_key(key)] = row_id


# ============================================================================
# Reference resolution helpers
# ============================================================================

def _flow_names_to_ids(names, resolver: _Resolver) -> List[int]:
    out = []
    for n in names or []:
        fid = resolver.get("flows", {"name": n})
        if fid is not None:
            out.append(fid)
    return out


def _resolve_parent_fk(kind: str, attrs: Dict[str, Any], resolver: _Resolver):
    """
    Resolve a kind's parent reference to (column, value) or signal a missing parent.

    Returns (column, value, None) on success, or (None, None, reason) if the parent
    cannot be resolved.
    """
    parent = attrs.get("parent")
    if kind == "milestones":
        pid = resolver.get("completion_path", parent)
        return ("completion_path_id", pid, None) if pid else (None, None, "parent completion_path not found")
    if kind == "tasks":
        pid = resolver.get("milestones", parent)
        return ("milestone_id", pid, None) if pid else (None, None, "parent milestone not found")
    if kind == "subtasks":
        pid = resolver.get("tasks", parent)
        return ("parent_task_id", pid, None) if pid else (None, None, "parent task not found")
    if kind == "sidequests":
        pid = resolver.get("tasks", parent)
        return ("paused_task_id", pid, None) if pid else (None, None, "paused task not found")
    if kind == "items":
        rt = (parent or {}).get("reference_table")
        pid = resolver.get(rt, (parent or {}).get("key")) if rt else None
        return ("reference_id", pid, None) if pid else (None, None, "item parent not found")
    return (None, None, None)


# ============================================================================
# Insert (add) — mint canonical id, resolve parents to canonical FKs
# ============================================================================

def _insert_entity(conn, resolver: _Resolver, kind: str, key: Dict[str, Any],
                   attrs: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Insert a new entity; return (canonical_id, None) or (None, conflict_reason)."""
    a = attrs or {}

    if kind == "files":
        cur = conn.execute(
            "INSERT INTO files (path, name, language, id_in_name) VALUES (?,?,?,?)",
            (key["path"], a.get("name"), a.get("language"), a.get("id_in_name", 1)))
    elif kind == "modules":
        cur = conn.execute(
            "INSERT INTO modules (name, path, description, purpose, external_dependencies) VALUES (?,?,?,?,?)",
            (key["name"], a.get("path"), a.get("description"), a.get("purpose"),
             a.get("external_dependencies")))
    elif kind == "themes":
        cur = conn.execute(
            "INSERT INTO themes (name, description, ai_generated, confidence_score) VALUES (?,?,?,?)",
            (key["name"], a.get("description"), a.get("ai_generated", 1), a.get("confidence_score", 0.0)))
    elif kind == "flows":
        cur = conn.execute(
            "INSERT INTO flows (name, description, ai_generated, confidence_score) VALUES (?,?,?,?)",
            (key["name"], a.get("description"), a.get("ai_generated", 1), a.get("confidence_score", 0.0)))
    elif kind == "completion_path":
        cur = conn.execute(
            "INSERT INTO completion_path (name, order_index, status, description) VALUES (?,?,?,?)",
            (key["name"], a.get("order_index", 0), a.get("status", "pending"), a.get("description")))
    elif kind == "functions":
        fpath = a.get("file", key.get("file"))
        file_id = resolver.get("files", {"path": fpath}) if fpath else None
        if fpath and file_id is None:
            return None, "parent file not found"
        cur = conn.execute(
            "INSERT INTO functions (entity_key, file_id, name, purpose, parameters, returns, id_in_name) VALUES (?,?,?,?,?,?,?)",
            (key.get("entity_key"), file_id, a.get("name", key.get("name")), a.get("purpose"),
             a.get("parameters"), a.get("returns"), a.get("id_in_name", 1)))
    elif kind == "types":
        fpath = a.get("file", key.get("file"))
        file_id = resolver.get("files", {"path": fpath}) if fpath else None
        if fpath and file_id is None:
            return None, "parent file not found"
        cur = conn.execute(
            "INSERT INTO types (entity_key, file_id, name, definition_json, description, links, id_in_name) VALUES (?,?,?,?,?,?,?)",
            (key.get("entity_key"), file_id, a.get("name", key.get("name")),
             a.get("definition_json", "{}"), a.get("description"), a.get("links"), a.get("id_in_name", 1)))
    elif kind == "milestones":
        col, pid, reason = _resolve_parent_fk(kind, a, resolver)
        if reason:
            return None, reason
        cur = conn.execute(
            "INSERT INTO milestones (slug, completion_path_id, name, status, description) VALUES (?,?,?,?,?)",
            (key["slug"], pid, a.get("name"), a.get("status", "pending"), a.get("description")))
    elif kind == "tasks":
        col, pid, reason = _resolve_parent_fk(kind, a, resolver)
        if reason:
            return None, reason
        flow_ids = _flow_names_to_ids(a.get("flows"), resolver)
        cur = conn.execute(
            "INSERT INTO tasks (slug, milestone_id, name, status, priority, description, flow_ids) VALUES (?,?,?,?,?,?,?)",
            (key["slug"], pid, a.get("name"), a.get("status", "pending"), a.get("priority", "medium"),
             a.get("description"), json.dumps(flow_ids) if flow_ids else None))
    elif kind == "subtasks":
        col, pid, reason = _resolve_parent_fk(kind, a, resolver)
        if reason:
            return None, reason
        cur = conn.execute(
            "INSERT INTO subtasks (slug, parent_task_id, name, status, priority, description) VALUES (?,?,?,?,?,?)",
            (key["slug"], pid, a.get("name"), a.get("status", "pending"), a.get("priority", "high"),
             a.get("description")))
    elif kind == "sidequests":
        col, pid, reason = _resolve_parent_fk(kind, a, resolver)
        if reason:
            return None, reason
        paused_sub = resolver.get("subtasks", a.get("paused_subtask")) if a.get("paused_subtask") else None
        flow_ids = _flow_names_to_ids(a.get("flows"), resolver)
        cur = conn.execute(
            "INSERT INTO sidequests (slug, paused_task_id, paused_subtask_id, name, status, priority, description, flow_ids) VALUES (?,?,?,?,?,?,?,?)",
            (key["slug"], pid, paused_sub, a.get("name"), a.get("status", "pending"),
             a.get("priority", "critical"), a.get("description"), json.dumps(flow_ids) if flow_ids else None))
    elif kind == "items":
        rt = (a.get("parent") or {}).get("reference_table")
        col, pid, reason = _resolve_parent_fk(kind, a, resolver)
        if reason:
            return None, reason
        cur = conn.execute(
            "INSERT INTO items (slug, reference_table, reference_id, name, status, description) VALUES (?,?,?,?,?,?)",
            (key["slug"], rt, pid, a.get("name"), a.get("status", "pending"), a.get("description")))
    else:
        return None, f"unknown kind: {kind}"

    new_id = cur.lastrowid
    resolver.put(kind, key, new_id)
    return new_id, None


# ============================================================================
# Modify — 3-way per field
# ============================================================================

def _field_to_set(conn, resolver: _Resolver, kind: str, field: str, value: Any):
    """Map a changed semantic field to (column, sql_value) or (None, reason)."""
    plain = _PLAIN_COLS.get(kind, {})
    if field in plain:
        return plain[field], value, None
    if field == "file":  # function/type move → resolve new file path to canonical file_id
        fid = resolver.get("files", {"path": value}) if value else None
        if value and fid is None:
            return None, None, "target file not found"
        return "file_id", fid, None
    if field == "parent":
        col, pid, reason = _resolve_parent_fk(kind, {"parent": value}, resolver)
        if reason:
            return None, None, reason
        return col, pid, None
    if field == "flows":
        return "flow_ids", json.dumps(_flow_names_to_ids(value, resolver)) or None, None
    if field == "paused_subtask":
        pid = resolver.get("subtasks", value) if value else None
        return "paused_subtask_id", pid, None
    return None, None, f"unmappable field: {field}"


def _apply_modify(conn, resolver, kind, key, branch_attrs, base_attrs, main_attrs):
    """
    3-way modify. Returns (status, reason): status in {'applied','skipped','conflict'}.

    For each field the branch changed (vs base):
      - main unchanged there  -> safe, stage the update
      - main already == branch -> no-op
      - main diverged          -> conflict (whole entity escalated, nothing applied)
    """
    sets, params = [], []
    for field, bval in (branch_attrs or {}).items():
        baseval = (base_attrs or {}).get(field)
        if bval == baseval:
            continue  # branch didn't change this field
        mainval = (main_attrs or {}).get(field)
        if mainval == bval:
            continue  # already applied in main
        if mainval != baseval:
            return "conflict", f"field '{field}' changed in both main and branch"
        col, sqlval, reason = _field_to_set(conn, resolver, kind, field, bval)
        if reason:
            return "conflict", reason
        sets.append(f"{col} = ?")
        params.append(sqlval)

    if not sets:
        return "skipped", "no-op"

    row_id = resolver.get(kind, key)
    if row_id is None:
        return "conflict", "entity modified in branch but absent in main (modify/delete)"
    params.append(row_id)
    conn.execute(f"UPDATE {kind} SET {', '.join(sets)} WHERE id = ?", params)
    return "applied", None


# ============================================================================
# Delete — first-class, safe-only
# ============================================================================

def _has_dependents(conn, kind, row_id) -> bool:
    for table, col in _DEPENDENTS.get(kind, []):
        try:
            if conn.execute(f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1", (row_id,)).fetchone():
                return True
        except sqlite3.OperationalError:
            pass
    if kind in ("tasks", "subtasks", "sidequests"):
        if conn.execute(
            "SELECT 1 FROM items WHERE reference_table = ? AND reference_id = ? LIMIT 1",
            (kind, row_id)).fetchone():
            return True
    return False


def _apply_delete(conn, resolver, kind, key, base_attrs, main_attrs):
    """Delete only if unchanged-since-base and free of dependents; else conflict."""
    row_id = resolver.get(kind, key)
    if row_id is None:
        return "skipped", "already absent"
    if main_attrs != base_attrs:
        return "conflict", "entity deleted in branch but modified in main (delete/modify)"
    if _has_dependents(conn, kind, row_id):
        return "conflict", "entity has inbound dependents in main (would orphan references)"
    conn.execute(f"DELETE FROM {kind} WHERE id = ?", (row_id,))
    return "applied", None


# ============================================================================
# References (edges)
# ============================================================================

def _edge_ids(resolver: _Resolver, ref: Dict[str, Any]):
    """Resolve an edge's endpoints to canonical ids. Returns (table, cols_values) or (None, reason)."""
    k = ref["kind"]
    if k == "interaction":
        s = resolver.get("functions", ref.get("from"))
        t = resolver.get("functions", ref.get("to"))
        if s is None or t is None:
            return None, "interaction endpoint function not found"
        return ("interactions",
                {"source_function_id": s, "target_function_id": t,
                 "interaction_type": ref["interaction_type"]},
                (ref.get("attributes") or {}).get("description"))
    if k == "type_usage":
        ty = resolver.get("types", ref.get("type"))
        fn = resolver.get("functions", ref.get("function"))
        if ty is None or fn is None:
            return None, "type_usage endpoint not found"
        return ("types_functions", {"type_id": ty, "function_id": fn, "role": ref.get("role")}, None)
    if k == "file_flow":
        fi = resolver.get("files", ref.get("file"))
        fl = resolver.get("flows", ref.get("flow"))
        if fi is None or fl is None:
            return None, "file_flow endpoint not found"
        return ("file_flows", {"file_id": fi, "flow_id": fl}, None)
    if k == "module_file":
        mo = resolver.get("modules", ref.get("module"))
        fi = resolver.get("files", ref.get("file"))
        if mo is None or fi is None:
            return None, "module_file endpoint not found"
        return ("module_files", {"module_id": mo, "file_id": fi}, None)
    if k == "flow_theme":
        fl = resolver.get("flows", ref.get("flow"))
        th = resolver.get("themes", ref.get("theme"))
        if fl is None or th is None:
            return None, "flow_theme endpoint not found"
        return ("flow_themes", {"flow_id": fl, "theme_id": th}, None)
    return None, f"unknown edge kind: {k}"


def _apply_reference(conn, resolver, ref):
    """Add or remove an edge by resolved canonical ids. Returns (status, reason)."""
    resolved = _edge_ids(resolver, ref)
    if resolved[0] is None:
        return "conflict", resolved[1]
    table, cols, description = resolved
    where = " AND ".join(f"{c} = ?" for c in cols)
    vals = tuple(cols.values())
    exists = conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", vals).fetchone()

    if ref["op"] == "add":
        if exists:
            return "skipped", "edge already present"
        columns = list(cols.keys())
        params = list(cols.values())
        if table == "interactions":
            columns.append("description")
            params.append(description)
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", params)
        return "applied", None

    if ref["op"] == "remove":
        if not exists:
            return "skipped", "edge already absent"
        conn.execute(f"DELETE FROM {table} WHERE {where}", vals)
        return "applied", None

    return "conflict", f"unknown edge op: {ref.get('op')}"


# ============================================================================
# Public tool
# ============================================================================

def apply_state_changeset(changeset: Dict[str, Any]) -> Result:
    """
    Apply a semantic changeset (from export_state_changeset) onto current-main project.db
    as a 3-way merge. Mutates the working DB in place after a backup.

    Args:
        changeset: dict with keys provenance{base_main_commit,...}, entities[], references[]

    Returns:
        Result with data={applied[], conflicts[], minted_ids[], backup_path, base_main_commit}.
        Non-overlapping changes auto-apply; conflicts are returned for review (never guessed).
        A genuine failure rolls back and restores the backup.
    """
    if not isinstance(changeset, dict):
        return Result(success=False, error="changeset must be an object")

    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    db_path = get_project_db_path(project_root)
    if not database_exists(db_path):
        return Result(success=False, error="Project database (current main) not found")

    provenance = changeset.get("provenance") or {}
    base_commit = provenance.get("base_main_commit")
    entities = changeset.get("entities") or []
    references = changeset.get("references") or []

    # Backup current main before mutating.
    fd, backup_path = tempfile.mkstemp(suffix=".db", prefix="aimfp_main_backup_")
    os.close(fd)
    shutil.copy2(db_path, backup_path)

    # Base state for 3-way (may be absent → empty).
    base_path = _effect_extract_db_at_commit(project_root, base_commit) if base_commit else None
    base_conn = _open_readonly(base_path) if base_path else None

    applied: List[dict] = []
    conflicts: List[dict] = []
    minted: List[dict] = []

    conn = _open_connection(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")  # we rewrite references ourselves
        base_idx = build_key_indexes(base_conn)
        base_entities = _collect_entities(base_conn, base_idx)
        main_entities = _collect_entities(conn, build_key_indexes(conn))
        resolver = _Resolver(conn)

        # group entity ops by kind, process in dependency order (parents before children)
        by_kind: Dict[str, List[dict]] = {k: [] for k in _ENTITY_KINDS}
        for e in entities:
            by_kind.setdefault(e.get("kind"), []).append(e)

        # adds + modifies first (dependency order); deletes after (reverse order)
        for kind in _ENTITY_KINDS:
            for e in by_kind.get(kind, []):
                op = e.get("op")
                key = e.get("semantic_key")
                kser = serialize_key(key)
                if op == "delete":
                    continue
                if op == "add":
                    if resolver.get(kind, key) is not None:
                        # already present in main — same entity if attrs match, else conflict
                        cur_attrs = main_entities.get(kind, {}).get(kser, {}).get("attrs")
                        if cur_attrs == e.get("attributes"):
                            applied.append({"kind": kind, "op": "add", "semantic_key": key,
                                            "note": "already present (identical)"})
                        else:
                            conflicts.append({"kind": kind, "op": "add", "semantic_key": key,
                                              "reason": "entity added in both with different attributes"})
                        continue
                    new_id, reason = _insert_entity(conn, resolver, kind, key, e.get("attributes") or {})
                    if reason:
                        conflicts.append({"kind": kind, "op": "add", "semantic_key": key, "reason": reason})
                    else:
                        applied.append({"kind": kind, "op": "add", "semantic_key": key, "id": new_id})
                        minted.append({"kind": kind, "semantic_key": key, "id": new_id})
                elif op == "modify":
                    status, reason = _apply_modify(
                        conn, resolver, kind, key, e.get("attributes") or {},
                        base_entities.get(kind, {}).get(kser, {}).get("attrs"),
                        main_entities.get(kind, {}).get(kser, {}).get("attrs"))
                    if status == "applied":
                        applied.append({"kind": kind, "op": "modify", "semantic_key": key})
                    elif status == "conflict":
                        conflicts.append({"kind": kind, "op": "modify", "semantic_key": key, "reason": reason})

        def _run_ref(ref):
            status, reason = _apply_reference(conn, resolver, ref)
            entry = {"kind": ref.get("kind"), "op": ref.get("op"), "reference": True}
            if status == "applied":
                applied.append(entry)
            elif status == "conflict":
                conflicts.append({**entry, "reason": reason})

        # reference REMOVES before entity deletes: clear edges into to-be-deleted nodes
        # first, so a self-consistent "remove edge + delete node" changeset applies cleanly
        # instead of self-conflicting. After this, any dependent that REMAINS at delete time
        # is a genuine block (a base edge the branch didn't remove, or concurrent new work).
        for ref in references:
            if ref.get("op") == "remove":
                _run_ref(ref)

        # deletes in reverse dependency order (children before parents)
        for kind in reversed(_ENTITY_KINDS):
            for e in by_kind.get(kind, []):
                if e.get("op") != "delete":
                    continue
                key = e.get("semantic_key")
                kser = serialize_key(key)
                status, reason = _apply_delete(
                    conn, resolver, kind, key,
                    base_entities.get(kind, {}).get(kser, {}).get("attrs"),
                    main_entities.get(kind, {}).get(kser, {}).get("attrs"))
                if status == "applied":
                    applied.append({"kind": kind, "op": "delete", "semantic_key": key})
                elif status == "conflict":
                    conflicts.append({"kind": kind, "op": "delete", "semantic_key": key, "reason": reason})

        # reference ADDS last: their endpoints (possibly newly-minted) now exist
        for ref in references:
            if ref.get("op") == "add":
                _run_ref(ref)

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        shutil.copy2(backup_path, db_path)  # restore
        if base_conn is not None:
            base_conn.close()
        if base_path:
            try:
                os.remove(base_path)
            except OSError:
                pass
        return Result(success=False, error=f"Apply failed, restored from backup: {str(e)}",
                      data={"backup_path": backup_path})
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if base_conn is not None:
        base_conn.close()
    if base_path:
        try:
            os.remove(base_path)
        except OSError:
            pass

    return Result(
        success=True,
        data={
            "applied": applied,
            "conflicts": conflicts,
            "minted_ids": minted,
            "backup_path": backup_path,
            "base_main_commit": base_commit,
        },
        return_statements=get_return_statements("apply_state_changeset"),
    )
