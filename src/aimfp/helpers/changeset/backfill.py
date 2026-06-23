"""
AIMFP Helper Functions - Backfill Semantic Keys

`backfill_semantic_keys` — OPTIONAL one-time prep tool that mints stable slugs for any
work-hierarchy rows that predate the slug column (e.g. rows that came through the
recreate-and-copy migration with an empty slug).

When to run it (only one narrow case): a project that EXISTED before schema v1.10 AND is
about to start multi-agent parallel work with InterCommAIMFP. Run it once on `main` and
commit, so every clone inherits stable baseline slugs and `export_state_changeset` (which
reads committed, immutable DB state and therefore cannot mint) can key those rows.

When it's unnecessary:
- Solo / non-InterCommAIMFP projects — slugs are never read; empty is harmless.
- Projects started fresh on v1.10+ — every row already gets a slug at creation, so this
  is a no-op.

Idempotent and trivial (one unique slug per NULL row). For use with InterCommAIMFP.
"""

from ..utils import (
    Result,
    resolve_project_root,
    get_project_db_path,
    database_exists,
    _open_connection,
    get_return_statements,
)
from ._common import _effect_mint_missing_slugs


def backfill_semantic_keys() -> Result:
    """
    Mint stable slugs for any work-hierarchy rows (milestones, tasks, subtasks,
    sidequests, items) that are missing one. Idempotent — safe to run repeatedly.

    Returns:
        Result with data={'minted': {table: count}, 'total_minted': int}
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
        counts = _effect_mint_missing_slugs(conn)
    except Exception as e:
        conn.close()
        return Result(success=False, error=f"Backfill failed: {str(e)}")
    conn.close()

    return Result(
        success=True,
        data={"minted": counts, "total_minted": sum(counts.values())},
        return_statements=get_return_statements("backfill_semantic_keys"),
    )
