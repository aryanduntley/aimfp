"""
AIMFP Helper Functions - Summarize Semantic Changeset

`summarize_state_changeset(branch=None, base_commit=None, changeset_id=None)` — a small,
low-token counts view of one branch's changeset that the master can afford to read for
every pending branch before deciding apply order, WITHOUT pulling back the full ~8k-token
object. Complements detect_state_conflicts (cross-branch) with a per-branch view.

For use with InterCommAIMFP (multi-agent parallel merge). See docs/intercommaimfptools/.

Pure read — either loads an already-persisted changeset by handle, or re-derives one via
export_state_changeset (which itself only reads committed git blobs). Never mutates.
"""

from typing import Optional

from ..utils import Result, resolve_project_root, get_return_statements
from ._common import _effect_load_changeset, summarize_changeset
from .export import export_state_changeset


def summarize_state_changeset(
    branch: Optional[str] = None,
    base_commit: Optional[str] = None,
    changeset_id: Optional[str] = None,
) -> Result:
    """
    Return a cheap counts summary for a branch's changeset.

    Two ways to identify the changeset (in priority order):
      1. changeset_id — load the persisted artifact (no git work at all)
      2. branch + base_commit — re-export, then summarize

    Args:
        branch: Git ref whose changeset to summarize (needs base_commit)
        base_commit: 3-way base / branch point (needed with `branch`)
        changeset_id: handle from a prior export_state_changeset

    Returns:
        Result with data={summary, provenance, warnings} — no entities[]/references[].
    """
    changeset = None

    if changeset_id:
        try:
            project_root = resolve_project_root()
        except RuntimeError as e:
            return Result(success=False, error=str(e))
        changeset = _effect_load_changeset(project_root, changeset_id)
        if changeset is None:
            return Result(
                success=False,
                error=f"changeset_id '{changeset_id}' not found. Pass branch + base_commit "
                      f"to re-derive it.",
            )
    elif branch and base_commit:
        exported = export_state_changeset(base_commit, branch)
        if not exported.success:
            return Result(success=False, error=exported.error)
        changeset = exported.data
    else:
        return Result(
            success=False,
            error="provide changeset_id, or both branch and base_commit",
        )

    return Result(
        success=True,
        data={
            "summary": summarize_changeset(changeset),
            "provenance": changeset.get("provenance", {}),
            "warnings": changeset.get("warnings", []),
        },
        return_statements=get_return_statements("summarize_state_changeset"),
    )
