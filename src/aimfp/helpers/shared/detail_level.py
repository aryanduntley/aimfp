"""
AIMFP Helper Functions - Detail Levels

Tools that can return large records take one parameter, detail_level:

    'lean' (default) - what is needed to orient and pick the next call:
                       ids, names, status, paths; long text (descriptions,
                       signatures, purposes) dropped or limited to open work
    'full'           - every column, as stored

One name across tools so the AI learns it once. 'standard', the older default
of get_current_progress, is accepted as 'lean'.
"""

import dataclasses
from typing import Any, Dict, Final, Optional, Tuple


# ============================================================================
# Constants
# ============================================================================

DETAIL_LEAN: Final[str] = 'lean'
DETAIL_FULL: Final[str] = 'full'
DETAIL_LEVELS: Final[Tuple[str, ...]] = (DETAIL_LEAN, DETAIL_FULL)

_ALIASES: Final[Dict[str, str]] = {'standard': DETAIL_LEAN}


# ============================================================================
# Pure Functions
# ============================================================================

def normalize_detail_level(
    value: Optional[str],
    allowed: Tuple[str, ...] = DETAIL_LEVELS,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Pure: Validate a detail_level argument.

    Args:
        value: Caller's detail_level; None means 'lean'
        allowed: Levels this tool accepts

    Returns:
        (level, None) when valid, (None, error message) otherwise
    """
    level = _ALIASES.get(value, value) if value is not None else DETAIL_LEAN
    if level not in allowed:
        return (None, f"Invalid detail_level '{value}'. Valid: {', '.join(allowed)}")
    return (level, None)


def pick_fields(row: Any, keys: Tuple[str, ...]) -> Dict[str, Any]:
    """
    Pure: A row reduced to keys, in order. Accepts dicts and dataclass records;
    keys the row does not have are skipped.
    """
    source = dataclasses.asdict(row) if dataclasses.is_dataclass(row) and not isinstance(row, type) else row
    return {k: source[k] for k in keys if k in source}
