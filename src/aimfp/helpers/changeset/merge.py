"""
AIMFP Helper Functions - Branch-ref Merge Orchestrator

`merge_worker_branch(branch, ...)` — one call that takes a BRANCH REF and runs the whole
documented integration sequence internally (export -> optional source text-merge -> apply),
returning only a small result/conflict summary. The ~8k-token changeset never crosses the
agent boundary, so a real fan-out no longer serializes on the master hand-carrying blobs.

`merge_worker_branches(branches, ...)` — integrate a list one at a time, each moving main
and re-minting against the prior.

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

Internally this is exactly the three steps the master used to drive by hand:
  1. export_state_changeset(base_commit, branch)  -> persisted changeset (+ id, summary)
  2. (source="auto") git-merge the branch's SOURCE into main, KEEPING main's project.db
     (the binary DB is never blob-merged — the changeset mechanism exists to avoid that)
  3. apply_state_changeset(changeset)             -> 3-way semantic merge onto main's DB

It is idempotent like apply_state_changeset, and conflicts are still returned as structured
data — no loss of the "never auto-guess" guarantee.

§5.5 bridge: AIMFP stays schema-clean. The result carries {worker_id, branch, status:
"merged"} so the master makes ONE intercomm_worktree_set_status call. AIMFP does NOT write
InterComm's DB (the tighter direct-write bridge is deliberately not implemented here — it
would couple AIMFP to InterComm's schema, which cannot be verified from this repo).
"""

import json
import os
import sqlite3
import subprocess
from typing import Dict, List, Optional, Any, Tuple

from ..utils import (
    Result,
    resolve_project_root,
    get_project_db_path,
    database_exists,
    _open_connection,
    get_return_statements,
)
from ._common import PROJECT_DB_REL_PATH, intercomm_present
from .export import export_state_changeset
from .apply import apply_state_changeset


# ============================================================================
# Git effects
# ============================================================================

def _git(project_root: str, args: List[str]) -> Tuple[int, str, str]:
    """Effect: run a git command in the project root; return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(["git", "-C", project_root, *args],
                           capture_output=True, text=True, check=False)
    except (FileNotFoundError, OSError) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _merge_base(project_root: str, branch: str) -> Optional[str]:
    rc, out, _ = _git(project_root, ["merge-base", "HEAD", branch])
    return out if rc == 0 and out else None


def _head_commit(project_root: str) -> Optional[str]:
    rc, out, _ = _git(project_root, ["rev-parse", "HEAD"])
    return out if rc == 0 and out else None


def _unmerged_paths(project_root: str) -> List[str]:
    rc, out, _ = _git(project_root, ["diff", "--name-only", "--diff-filter=U"])
    return [p for p in out.splitlines() if p] if rc == 0 else []


def _staged_paths(project_root: str) -> List[str]:
    rc, out, _ = _git(project_root, ["diff", "--cached", "--name-only"])
    return [p for p in out.splitlines() if p] if rc == 0 else []


def _effect_source_merge(project_root: str, branch: str) -> Dict[str, Any]:
    """
    Effect: text-merge `branch`'s source into the working tree, KEEPING main's project.db.

    Leaves the merge uncommitted (the master reviews + commits source together with the
    applied DB). Returns {merged_paths, conflicts, error?, in_progress}.
    """
    # The changeset is the ONLY thing allowed to move DB state, and the working project.db
    # IS current-main here. Reset it to HEAD first so byte-level drift left by other tools
    # opening it (e.g. SQLite WAL checkpoints) doesn't make git refuse the merge as dirty.
    _git(project_root, ["checkout", "HEAD", "--", PROJECT_DB_REL_PATH])

    rc, _out, err = _git(project_root, ["merge", "--no-commit", "--no-ff", branch])
    conflicts = _unmerged_paths(project_root)

    # Whatever happened to project.db in the merge, restore it to HEAD (main): the changeset
    # is the only thing allowed to move DB state. This also resolves a DB merge conflict.
    _git(project_root, ["checkout", "HEAD", "--", PROJECT_DB_REL_PATH])

    if rc != 0 and not conflicts:
        # Not a conflict — a genuine merge error (dirty tree, unknown ref, etc.). Abort to
        # leave the tree as we found it.
        _git(project_root, ["merge", "--abort"])
        return {"merged_paths": [], "conflicts": [], "error": err or "git merge failed",
                "in_progress": False}

    merged_paths = [p for p in _staged_paths(project_root)
                    if p != PROJECT_DB_REL_PATH and p not in conflicts]
    return {"merged_paths": merged_paths, "conflicts": conflicts, "in_progress": True}


# ============================================================================
# merge_history bookkeeping
# ============================================================================

def _parse_user_from_branch(branch: str) -> str:
    """Pure: best-effort 'user' from an aimfp-<user>-<n> branch name."""
    parts = (branch or "").split("-")
    if len(parts) >= 3 and parts[0] == "aimfp":
        return "-".join(parts[1:-1])
    return branch or "unknown"


def _effect_record_merge(project_root: str, branch: str, worker_id: Optional[str],
                         conflict_count: int, summary: Dict[str, Any]) -> None:
    """Effect: append a merge_history row and mark the work_branches row merged."""
    db_path = get_project_db_path(project_root)
    if not database_exists(db_path):
        return
    conn = _open_connection(db_path)
    try:
        try:
            conn.execute(
                "INSERT INTO merge_history (source_branch, target_branch, conflicts_detected, "
                "conflicts_manual_resolved, resolution_details, merged_by) "
                "VALUES (?, 'main', ?, ?, ?, ?)",
                (branch, conflict_count, conflict_count, json.dumps(summary),
                 worker_id or "intercomm-master"),
            )
        except sqlite3.OperationalError:
            pass

        try:
            cur = conn.execute(
                "UPDATE work_branches SET status='merged', merged_at=CURRENT_TIMESTAMP, "
                "merge_conflicts_count=? WHERE branch_name=?",
                (conflict_count, branch),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO work_branches (branch_name, user_name, purpose, status, "
                    "merged_at, merge_conflicts_count) "
                    "VALUES (?, ?, ?, 'merged', CURRENT_TIMESTAMP, ?)",
                    (branch, _parse_user_from_branch(branch),
                     worker_id or "InterCommAIMFP worker integration", conflict_count),
                )
        except sqlite3.OperationalError:
            pass

        conn.commit()
    finally:
        conn.close()


# ============================================================================
# Public tools
# ============================================================================

def merge_worker_branch(
    branch: str,
    base_commit: Optional[str] = None,
    worker_id: Optional[str] = None,
    source: str = "auto",
    on_conflict: str = "report",
) -> Result:
    """
    Integrate one worker branch into current main (export -> source merge -> apply).

    Args:
        branch: Git ref to integrate, e.g. "aimfp-alice-001".
        base_commit: 3-way base / branch point. Default = git merge-base(HEAD, branch).
        worker_id: Optional InterCommAIMFP worker identity (provenance / status hint).
        source: "auto" to also text-merge the branch's source into main (default),
            "skip" to merge only project-DB state (master merges source itself).
        on_conflict: "report" (default) to apply the DB even when source has conflicts,
            "abort" to stop before applying if the source merge conflicts.

    Returns:
        Result with data={applied[], conflicts[], minted_ids[], backup_path,
            base_main_commit, source_merge{merged_paths,conflicts}, changeset_id,
            summary, branch, worker_id, status, intercomm_present}.
        Idempotent; conflicts are structured data for review (never guessed).
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError as e:
        return Result(success=False, error=str(e))

    if not branch:
        return Result(success=False, error="branch is required")

    base_main_commit = _head_commit(project_root)
    if base_commit is None:
        base_commit = _merge_base(project_root, branch)
        if base_commit is None:
            return Result(
                success=False,
                error=f"Could not compute merge-base(HEAD, {branch}). Is the branch present "
                      f"and is this a git repo? Pass base_commit explicitly.",
            )

    # 1. export (persists server-side; we drive apply by the inline object we already hold)
    exported = export_state_changeset(base_commit, branch, worker_id)
    if not exported.success:
        return Result(success=False, error=f"export failed: {exported.error}")
    changeset = {k: exported.data[k] for k in ("provenance", "entities", "references", "warnings")}
    changeset_id = exported.data.get("changeset_id")
    summary = exported.data.get("summary")

    # 2. source text-merge (optional), keeping main's project.db
    source_merge: Dict[str, Any] = {"merged_paths": [], "conflicts": [], "in_progress": False}
    if source == "auto":
        source_merge = _effect_source_merge(project_root, branch)
        if source_merge.get("error"):
            return Result(success=False, error=f"source merge failed: {source_merge['error']}")
        if source_merge["conflicts"] and on_conflict == "abort":
            _git(project_root, ["merge", "--abort"])
            return Result(
                success=False,
                error="source merge has conflicts and on_conflict='abort'; aborted before "
                      "applying DB state.",
                data={"source_merge": source_merge, "changeset_id": changeset_id},
            )

    # 3. apply the changeset onto main's project.db
    applied = apply_state_changeset(changeset)
    if not applied.success:
        return Result(
            success=False,
            error=f"apply failed: {applied.error}",
            data={"source_merge": source_merge, "changeset_id": changeset_id,
                  "backup_path": (applied.data or {}).get("backup_path")},
        )

    conflict_count = len(applied.data.get("conflicts", [])) + len(source_merge.get("conflicts", []))
    _effect_record_merge(project_root, branch, worker_id, conflict_count, summary or {})

    # §5.5 no-coupling bridge: report the final lifecycle status using InterComm's own
    # worktree-status enum (src/types.ts WORKTREE_STATUSES) so the master can forward it
    # verbatim to intercomm_worktree_set_status. AIMFP never writes InterComm's DB.
    status = "conflict" if conflict_count else "merged"

    data = dict(applied.data)
    data.update({
        "source_merge": source_merge,
        "changeset_id": changeset_id,
        "summary": summary,
        "branch": branch,
        "worker_id": worker_id,
        "status": status,
        "intercomm_present": intercomm_present(project_root),
    })

    return Result(
        success=True,
        data=data,
        return_statements=get_return_statements("merge_worker_branch"),
    )


def merge_worker_branches(
    branches: List[Any],
    order: str = "as-listed",
    on_conflict: str = "stop",
) -> Result:
    """
    Integrate several worker branches one at a time, each moving main forward.

    Args:
        branches: list of branch names (strings) or {branch, base_commit?, worker_id?} dicts.
        order: "as-listed" (default) or "additive-first" (branches whose changeset has no
            delete/remove ops integrate first — they are the low-risk, purely-additive ones).
        on_conflict: "stop" (default) to halt the batch on the first branch that returns
            conflicts, or "continue" to integrate the rest regardless.

    Returns:
        Result with data={results[], integrated[], stopped_at?}. Each entry in results[] is
        a merge_worker_branch result (or an error stub).
    """
    if not isinstance(branches, list) or not branches:
        return Result(success=False, error="branches must be a non-empty list")

    norm: List[Dict[str, Any]] = []
    for b in branches:
        if isinstance(b, str):
            norm.append({"branch": b})
        elif isinstance(b, dict) and b.get("branch"):
            norm.append(b)
        else:
            return Result(success=False, error=f"invalid branch entry: {b!r}")

    if order == "additive-first":
        try:
            project_root = resolve_project_root()
        except RuntimeError as e:
            return Result(success=False, error=str(e))

        def _additive_rank(item: Dict[str, Any]) -> int:
            base = item.get("base_commit") or _merge_base(project_root, item["branch"])
            if not base:
                return 1
            ex = export_state_changeset(base, item["branch"], item.get("worker_id"))
            if not ex.success:
                return 1
            has_struct = any(e.get("op") == "delete" for e in ex.data.get("entities", [])) or \
                any(r.get("op") == "remove" for r in ex.data.get("references", []))
            return 1 if has_struct else 0

        norm.sort(key=_additive_rank)

    results: List[Dict[str, Any]] = []
    integrated: List[str] = []
    stopped_at: Optional[str] = None

    for item in norm:
        res = merge_worker_branch(
            item["branch"],
            base_commit=item.get("base_commit"),
            worker_id=item.get("worker_id"),
            on_conflict="report",
        )
        entry = {"branch": item["branch"], "success": res.success}
        if res.success:
            entry["result"] = res.data
            integrated.append(item["branch"])
            if res.data.get("conflicts") and on_conflict == "stop":
                stopped_at = item["branch"]
                results.append(entry)
                break
        else:
            entry["error"] = res.error
            entry["result"] = res.data
            if on_conflict == "stop":
                stopped_at = item["branch"]
                results.append(entry)
                break
        results.append(entry)

    return Result(
        success=True,
        data={"results": results, "integrated": integrated, "stopped_at": stopped_at},
        return_statements=get_return_statements("merge_worker_branches"),
    )
