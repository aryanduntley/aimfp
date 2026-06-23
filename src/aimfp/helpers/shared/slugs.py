"""
AIMFP Helper Functions - Stable Slug Minting

Pure utilities for minting stable, globally-unique identity slugs for work-hierarchy
rows (milestones, tasks, subtasks, sidequests, items).

Why slugs exist: in multi-agent parallel work (InterCommAIMFP), each worker operates in
its own clone of project.db and independently mints autoincrement integer PKs starting
from the same committed base — so the same integer gets assigned to DIFFERENT new rows in
different clones. Integer PKs therefore CANNOT be the merge identity. Names are not unique
either. A slug minted at creation, carrying a uuid4 suffix, is globally unique across
independently-minting clones and stays stable for a given logical row across export/apply.

Format: ``<kind>-<slugified-name>-<uuid4 hex[:8]>``  e.g. ``task-implement-auth-9f3a1c20``
The name fragment keeps slugs human-readable in conflict reports; the uuid suffix
guarantees uniqueness.

Projects that never use InterCommAIMFP simply never need to read these — the column is
nullable and harmless when empty.

These functions are effect-bearing only in that ``mint_slug`` is non-deterministic
(uuid4); that non-determinism is the point. ``slugify`` is pure.
"""

import re
import uuid

_MAX_NAME_SLUG_LEN = 40


def slugify(name: str, max_len: int = _MAX_NAME_SLUG_LEN) -> str:
    """
    Pure: Convert a human name into a lowercase, hyphen-delimited fragment.

    Keeps only [a-z0-9] runs, collapses separators to single hyphens, trims, and
    truncates. Falls back to 'item' for empty/symbol-only names.

    Args:
        name: Human-readable entity name
        max_len: Maximum length of the returned fragment

    Returns:
        URL-safe slug fragment (never empty)
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "item"


def mint_slug(kind: str, name: str) -> str:
    """
    Mint a globally-unique stable slug for a work-hierarchy row.

    Non-deterministic by design (uuid4 suffix) so independently-minting clones never
    collide on a slug for different rows.

    Args:
        kind: Entity kind prefix (e.g. 'task', 'milestone', 'subtask', 'sidequest', 'item')
        name: Human-readable entity name (for the readable fragment)

    Returns:
        Slug of the form '<kind>-<slugified-name>-<uuid8>'

    Example:
        >>> mint_slug('task', 'Implement Auth')      # doctest: +SKIP
        'task-implement-auth-9f3a1c20'
    """
    return f"{kind}-{slugify(name)}-{uuid.uuid4().hex[:8]}"
