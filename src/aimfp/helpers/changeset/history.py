"""
AIMFP Helper Functions - Merge History (provenance / idempotency)

`get_merge_history(branch=None, since=None)` — read tool over the existing merge_history
and work_branches tables. Lets the master answer "has this branch already been integrated?"
(idempotency / resume after interruption) and gives a human an audit trail.

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

Pure read — never mutates. Small surface over data merge_worker_branch already records.
"""

import sqlite3
from typing import Optional

from ..utils import (
    Result,
    resolve_project_root,
    get_project_db_path,
    database_exists,
    _open_connection,
    get_return_statements,
)


def _rows(conn: sqlite3.Connection, sql: str, params=()):
    """Effect: run a query tolerantly; return [] if the table is missing."""
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []


def get_merge_history(
    branch: Optional[str] = None,
    since: Optional[str] = None,
) -> Result:
    """
    Report integration history from project.db.

    Args:
        branch: Optional source-branch filter (exact match on merge_history.source_branch
            and work_branches.branch_name).
        since: Optional ISO timestamp lower bound on merge_history.merge_timestamp
            (e.g. "2026-06-26" or "2026-06-26T12:00:00").

    Returns:
        Result with data={merges[], branches[], already_merged}. `already_merged` is a
        convenience boolean: True iff `branch` was given and has at least one merge row
        OR a work_branches row with status='merged'.
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
        merge_sql = "SELECT * FROM merge_history"
        clauses, params = [], []
        if branch:
            clauses.append("source_branch = ?")
            params.append(branch)
        if since:
            clauses.append("merge_timestamp >= ?")
            params.append(since)
        if clauses:
            merge_sql += " WHERE " + " AND ".join(clauses)
        merge_sql += " ORDER BY merge_timestamp DESC"
        merges = _rows(conn, merge_sql, tuple(params))

        if branch:
            branches = _rows(conn, "SELECT * FROM work_branches WHERE branch_name = ?", (branch,))
        else:
            branches = _rows(conn, "SELECT * FROM work_branches ORDER BY created_at DESC")
    finally:
        conn.close()

    already_merged = bool(branch) and (
        len(merges) > 0 or any(b.get("status") == "merged" for b in branches)
    )

    return Result(
        success=True,
        data={
            "merges": merges,
            "branches": branches,
            "already_merged": already_merged,
        },
        return_statements=get_return_statements("get_merge_history"),
    )
