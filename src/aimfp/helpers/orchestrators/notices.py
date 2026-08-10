"""
AIMFP Helper Functions - One-Time System Notices

Delivers release announcements to the AI at most once per project.

When a release changes behavior or introduces a setting, the user needs to hear
about it once — a changelog nobody reads is not delivery. Notices ship in the
read-only core database; the record of which have been seen lives per-project in
`user_preferences.acknowledged_notices`.

**Only unacknowledged notices are ever returned.** Filtering happens in SQL, so a
project that has seen everything gets an empty tuple and `aimfp_run` omits the key
entirely. The AI never queries these tables itself and never sees an acknowledged
notice again — a notice that kept reappearing would train the AI to ignore all of
them.

A notice can be pinned to a migration via `applies_to_db` + `applies_from_version`,
so it fires only once that database has actually reached the version the change
landed in. Left null, it applies to every project.
"""

import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ._common import (
    get_core_db_path,
    get_user_preferences_db_path,
    resolve_project_root,
    database_exists,
    Result,
)
from ..utils import get_return_statements


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class NoticesResult:
    """Result of a pending-notice lookup."""
    success: bool
    notices: Tuple[Dict[str, Any], ...] = ()
    count: int = 0
    action_required_count: int = 0
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helpers
# ============================================================================

def version_at_least(actual: str, required: str) -> bool:
    """
    Pure: Compare two dotted numeric version strings.

    Used to decide whether a migration-pinned notice has become applicable.
    Non-numeric or malformed versions compare as "not yet reached", so a notice
    stays hidden rather than firing against a database it was not written for.

    Args:
        actual: Version the database reports
        required: Minimum version the notice needs

    Returns:
        True when actual >= required
    """
    def parts(value: str) -> Optional[tuple]:
        try:
            return tuple(int(p) for p in str(value).strip().split('.'))
        except (ValueError, AttributeError):
            return None

    left, right = parts(actual), parts(required)
    if left is None or right is None:
        return False

    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left >= right


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_db_version(db_path: str) -> Optional[str]:
    """
    Effect: Read schema_version from a database.

    Args:
        db_path: Path to the database

    Returns:
        Version string, or None when unreadable
    """
    if not database_exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _effect_unacknowledged_notices(
    core_db_path: str,
    prefs_db_path: str,
) -> Tuple[Tuple[Dict[str, Any], ...], Optional[str]]:
    """
    Effect: Read notices with no acknowledgement row for this project.

    Filters in SQL by attaching the project's preferences database and
    anti-joining, so acknowledged notices never leave the database layer.

    Args:
        core_db_path: Path to aimfp_core.db
        prefs_db_path: Path to the project's user_preferences.db

    Returns:
        (notice dicts, error message or None)
    """
    try:
        conn = sqlite3.connect(core_db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("ATTACH DATABASE ? AS prefs", (prefs_db_path,))
            try:
                rows = conn.execute(
                    """
                    SELECT n.notice_key, n.title, n.message, n.severity,
                           n.action_required, n.applies_to_db,
                           n.applies_from_version, n.introduced_in
                    FROM system_notices n
                    LEFT JOIN prefs.acknowledged_notices a
                           ON a.notice_key = n.notice_key
                    WHERE a.notice_key IS NULL
                    ORDER BY n.created_at, n.notice_key
                    """
                ).fetchall()
            finally:
                conn.execute("DETACH DATABASE prefs")
        finally:
            conn.close()

        return (tuple(dict(row) for row in rows), None)

    except sqlite3.Error as exc:
        # A project that predates the notices tables simply has nothing pending.
        return ((), str(exc))


def _effect_acknowledge(prefs_db_path: str, notice_key: str, outcome: Optional[str]) -> None:
    """
    Effect: Record that a notice has been delivered.

    Args:
        prefs_db_path: Path to the project's user_preferences.db
        notice_key: Notice that was delivered
        outcome: Optional record of what the user decided
    """
    conn = sqlite3.connect(prefs_db_path)
    try:
        conn.execute(
            """
            INSERT INTO acknowledged_notices (notice_key, outcome)
            VALUES (?, ?)
            ON CONFLICT(notice_key) DO UPDATE SET
                acknowledged_at = CURRENT_TIMESTAMP,
                outcome = excluded.outcome
            """,
            (notice_key, outcome),
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# Public Tools
# ============================================================================

def get_pending_notices(project_root: Optional[str] = None) -> NoticesResult:
    """
    Get release notices this project has not yet been shown.

    Returns only unacknowledged notices, already filtered for applicability —
    a notice pinned to a schema version stays hidden until that database has
    actually reached it. An empty result is the normal steady state.

    Called automatically by aimfp_run(is_new_session=true); available standalone
    for hosts that want the check on demand.

    Args:
        project_root: Project root override (defaults to the discovered root)

    Returns:
        NoticesResult with the pending notices, newest schema first
    """
    try:
        project_root = project_root or resolve_project_root()
        core_db_path = get_core_db_path()
        prefs_db_path = get_user_preferences_db_path(project_root)

        if not database_exists(core_db_path) or not database_exists(prefs_db_path):
            return NoticesResult(success=True)

        candidates, error = _effect_unacknowledged_notices(core_db_path, prefs_db_path)
        if error:
            # Missing tables mean this project has not migrated yet. The pending
            # migration is already surfaced separately; notices resume after it.
            return NoticesResult(success=True)

        versions: Dict[str, Optional[str]] = {}
        applicable = []

        for notice in candidates:
            db_name = notice.get('applies_to_db')
            required = notice.get('applies_from_version')

            if db_name and required:
                if db_name not in versions:
                    db_file = os.path.join(
                        os.path.dirname(prefs_db_path), f"{db_name}.db"
                    )
                    versions[db_name] = _effect_db_version(db_file)
                actual = versions[db_name]
                if actual is None or not version_at_least(actual, required):
                    continue

            applicable.append({
                **notice,
                'action_required': bool(notice.get('action_required')),
            })

        return NoticesResult(
            success=True,
            notices=tuple(applicable),
            count=len(applicable),
            action_required_count=sum(1 for n in applicable if n['action_required']),
            return_statements=get_return_statements("get_pending_notices"),
        )

    except Exception as exc:
        return NoticesResult(success=False, error=f"Notice lookup failed: {str(exc)}")


def acknowledge_notice(
    notice_key: str,
    outcome: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Result:
    """
    Mark a notice as delivered so it never fires again for this project.

    Call this only AFTER acting on the notice — for an action_required notice,
    that means after the user has actually answered, not merely been asked.
    Acknowledging early loses the message permanently.

    Args:
        notice_key: Notice identifier from get_pending_notices
        outcome: Optional short record of what the user decided, kept for audit
        project_root: Project root override (defaults to the discovered root)

    Returns:
        Result confirming the acknowledgement
    """
    if not notice_key or not str(notice_key).strip():
        return Result(success=False, error="notice_key is required")

    try:
        project_root = project_root or resolve_project_root()
        prefs_db_path = get_user_preferences_db_path(project_root)

        if not database_exists(prefs_db_path):
            return Result(success=False, error="user_preferences.db not found")

        _effect_acknowledge(prefs_db_path, str(notice_key).strip(), outcome)

        return Result(
            success=True,
            data={'notice_key': notice_key, 'outcome': outcome},
        )

    except sqlite3.OperationalError as exc:
        return Result(
            success=False,
            error=(
                f"Could not acknowledge notice: {str(exc)}. "
                f"If acknowledged_notices is missing, run migrate_databases first."
            ),
        )
    except Exception as exc:
        return Result(success=False, error=f"Acknowledgement failed: {str(exc)}")
