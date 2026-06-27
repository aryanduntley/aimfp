"""
AIMFP Helper Functions - Global Supportive Context

Returns supportive context reference documents for AI sessions.
All variants auto-provided: core + coding by aimfp_run(is_new_session=true),
case2 by aimfp_run when Case 2 active, init by aimfp_init().
Also callable on demand to reload stale context.

All functions are pure FP - immutable data, explicit parameters, Result types.

Helpers in this file:
- get_supportive_context: Read and return supportive context content by variant
"""

from pathlib import Path
from typing import Tuple

# Import global utilities
from ..utils import get_return_statements, Result


# ============================================================================
# Constants
# ============================================================================

GUIDES_DIR: str = str(
    Path(__file__).parent.parent.parent / "reference" / "guides"
)

VALID_VARIANTS: Tuple[str, ...] = ('core', 'init', 'coding', 'case2')

VARIANT_FILES: dict = {
    'core': 'supportive_context.txt',
    'init': 'supportive_context_init.txt',
    'coding': 'supportive_context_coding.txt',
    'case2': 'supportive_context_case2.txt',
}

# Presence-gated one-liner appended to the core context ONLY when InterCommAIMFP is
# installed in this project (the .intercomm-aimfp/intercomm.db signal). InterCommAIMFP
# already auto-injects its full protocol via the MCP server `instructions` field on
# connect, so this is a lightweight cross-reference — it reminds an agent deep in AIMFP's
# work loop that the layer exists and gives the exact protocol tool name, without AIMFP
# duplicating or owning InterComm's protocol text.
INTERCOMM_CONTEXT_NOTE: str = (
    "\n\nINTERCOMMAIMFP DETECTED (addon — present only when InterCommAIMFP is installed)\n"
    "  InterCommAIMFP is a local-only coordination layer for multiple Claude Code instances on "
    "this project: one master delegates tasks to workers (git worktrees + a shared SQLite DB) and "
    "integrates their work via AIMFP semantic changesets.\n"
    "  - Protocol & roles: call intercomm_get_protocol (InterCommAIMFP's own tool).\n"
    "  - Prep a fan-out: verify_fanout_ready, then plan_disjoint_partitions.\n"
    "  - Integrate a worker branch's DB state into main: merge_worker_branch(branch) "
    "(or merge_worker_branches for a batch); forward the returned {worker_id, branch, status} to "
    "intercomm_worktree_set_status.\n"
)


# ============================================================================
# Internal
# ============================================================================

def _intercomm_detected() -> bool:
    """Effect: True if InterCommAIMFP is installed in the resolved project (presence-only).

    Lazy imports keep this file free of any import-time dependency on the changeset
    package; a project that isn't initialized yet (no resolvable root) is simply False.
    """
    try:
        from ..utils import resolve_project_root
        from ..changeset._common import intercomm_present
        return intercomm_present(resolve_project_root())
    except Exception:
        return False


# ============================================================================
# Public Helper Functions
# ============================================================================

def get_supportive_context(variant: str = 'core') -> Result:
    """
    Read and return a supportive context reference document.

    Variants (auto-provided by orchestrators, also callable on demand):
        'core'   — Full workflow, routing, ad-hoc rules, edge cases (auto: aimfp_run new session)
        'init'   — Discovery depth, initialization detail, post-completion paths (auto: aimfp_init)
        'coding' — File coding loop, DRY/modular reuse, interactions, types_functions (auto: aimfp_run if initialized)
        'case2'  — Use Case 2 pipeline, user directive system, preferences (auto: aimfp_run if Case 2 active)

    Args:
        variant: Which context to load. Default 'core'.

    Returns:
        Result with data={
            content: str (full text of the variant file),
            variant: str (which variant was loaded),
            token_estimate: int (approximate token count),
            source: str (file path)
        }

    On error:
        Result with error message if variant invalid or file not found.
    """
    if variant not in VALID_VARIANTS:
        return Result(
            success=False,
            error=f"Invalid variant '{variant}'. Valid: {VALID_VARIANTS}",
        )

    try:
        filename = VARIANT_FILES[variant]
        context_path = Path(GUIDES_DIR) / filename

        if not context_path.is_file():
            return Result(
                success=False,
                error=f"Supportive context file not found: {context_path}. "
                      f"Expected at src/aimfp/reference/guides/{filename}",
            )

        content = context_path.read_text(encoding="utf-8")

        # Presence-gated InterCommAIMFP cross-reference (core variant only — it is the one
        # always provided on session start by aimfp_run). Absent project / non-InterComm
        # project => no change, exactly as today.
        if variant == 'core' and _intercomm_detected():
            content = content + INTERCOMM_CONTEXT_NOTE

        # Rough token estimate: ~1 token per 4 characters
        token_estimate = len(content) // 4

        return Result(
            success=True,
            data={
                'content': content,
                'variant': variant,
                'token_estimate': token_estimate,
                'source': str(context_path),
            },
            return_statements=get_return_statements("get_supportive_context"),
        )

    except Exception as e:
        return Result(
            success=False,
            error=f"Error reading supportive context: {str(e)}",
        )
