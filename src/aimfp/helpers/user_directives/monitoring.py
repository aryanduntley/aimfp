"""
AIMFP Helper Functions - User Directive Monitoring (AI-Facing)

The read side of the hook execution model. The user's generated runner records
what happened through aimfp.hooks while no AI session is open; these tools are
how the AI sees it afterwards.

Everything here is a real MCP tool - the opposite of src/aimfp/hooks/, which is
the library surface outside code calls and the AI never invokes. The dependency
runs helpers -> hooks and never the reverse: this module reuses the hook
package's log configuration and filename constants so the reader cannot drift
from the writer, while hooks stays import-light and knows nothing about helpers.

WHY THERE IS NO PROCESS INSPECTION HERE. The original user_directive_monitor
workflow expected a supervisor: verify PIDs, check scheduler registration,
restart dead services. AIMFP has no such process. A dead runner is detected
instead by next_scheduled_time falling into the past - the runner declares when
it next expects to fire, and silence past that moment IS the evidence. That is
why the PID and APScheduler steps were removed rather than implemented.

SKIPS ARE THE OTHER SILENCE. A runner that is alive but never at the
scheduled moment keeps moving next_scheduled_time forward, so it is never
overdue, and a skip is neither an execution nor an error. The runner reports
each one through record_directive_skip, and consecutive_skip_count - reset by
every recorded execution - is what makes a directive 'skipping'.

All classification logic is pure: assess_directive_health takes rows, a
timestamp, and thresholds, so every verdict is testable without a database or
a clock.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from ...database.connection import (
    _close_connection,
    _get_table_info,
    _open_connection,
    database_exists,
    get_cached_project_root,
    get_user_directives_db_path,
)
from ...hooks.config import load_log_config, resolve_log_path
from ...hooks.logs import ERROR_LOG_NAME
from ..utils import get_return_statements

# Health states, ordered most to least actionable. assess_directive_health
# applies them in exactly this precedence: a directive already in error state
# is not better news for being on schedule.
HEALTH_ERROR = 'error'
HEALTH_OVERDUE = 'overdue'
HEALTH_SKIPPING = 'skipping'
HEALTH_DEGRADED = 'degraded'
HEALTH_NEVER_RUN = 'never_run'
HEALTH_OK = 'ok'

HEALTH_ORDER = (
    HEALTH_ERROR, HEALTH_OVERDUE, HEALTH_SKIPPING, HEALTH_DEGRADED,
    HEALTH_NEVER_RUN, HEALTH_OK)

# Optional directive_executions columns (user_directives schema 1.3). Read
# only when present, so monitoring works on a database not yet migrated.
_OPTIONAL_STAT_COLUMNS = (
    'skip_count', 'consecutive_skip_count', 'last_skip_time', 'last_skip_reason',
    'condition_latched', 'last_condition_eval_time')

_NO_DATABASE = (
    "No user_directives.db for this project. Directive monitoring is Use Case 2 "
    "only; a Use Case 1 project has no user directives to monitor."
)


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class DirectiveHealth:
    """One directive's health verdict. See HEALTH_* for the states."""
    directive_id: int
    name: str
    status: str
    health: str
    detail: str
    total_executions: int = 0
    error_count: int = 0
    error_rate: float = 0.0
    last_execution_time: Optional[str] = None
    next_scheduled_time: Optional[str] = None
    overdue_seconds: Optional[float] = None
    last_error_type: Optional[str] = None
    last_error_message: Optional[str] = None
    skip_count: int = 0
    consecutive_skip_count: int = 0
    last_skip_time: Optional[str] = None
    last_skip_reason: Optional[str] = None


@dataclass(frozen=True)
class ExecutionStatsResult:
    """Result of get_directive_execution_stats."""
    success: bool
    stats: Tuple[Dict[str, Any], ...] = ()
    total_count: int = 0
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ErrorLogResult:
    """Result of get_recent_directive_errors."""
    success: bool
    errors: Tuple[Dict[str, Any], ...] = ()
    total_count: int = 0
    malformed_lines: int = 0
    log_path: Optional[str] = None
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HealthResult:
    """Result of check_directive_health."""
    success: bool
    directives: Tuple[DirectiveHealth, ...] = ()
    summary: Optional[Dict[str, int]] = None
    needs_attention: bool = False
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Logic
# ============================================================================

def compute_error_rate(total_executions: int, error_count: int) -> float:
    """
    Pure: Error count over attempts, guarding division by zero.

    Attempts is the LARGER of executions and errors, not the execution count,
    because record_directive_error deliberately records an error without
    counting an execution. A directive that failed to start ten times has ten
    errors and zero executions - dividing by executions alone would be
    undefined exactly when the situation is worst, and would report 0% for a
    directive that has never once succeeded.

    Args:
        total_executions: Executions recorded
        error_count: Errors recorded, including out-of-band ones

    Returns:
        Rate between 0.0 and 1.0; 0.0 when there is nothing to divide
    """
    executions = max(total_executions, 0)
    errors = max(error_count, 0)
    # Errors can exceed executions when out-of-band errors were recorded, so
    # attempts is the larger of the two rather than the execution count.
    attempts = max(executions, errors)
    if attempts <= 0:
        return 0.0
    return errors / attempts


def assess_directive_health(
    rows: Tuple[Dict[str, Any], ...],
    now: str,
    error_rate_threshold: float = 0.5,
    skip_threshold: int = 3,
) -> Tuple[DirectiveHealth, ...]:
    """
    Pure: Classify every directive's health from observed rows.

    Takes `now` and the thresholds as parameters, never reading a clock or a
    database, so every classification is testable directly.

    Args:
        rows: Joined directive + execution-statistics rows
        now: ISO timestamp to evaluate overdue-ness against
        error_rate_threshold: Error rate at or above which a directive is
            reported degraded
        skip_threshold: Consecutive skips at or above which a directive is
            reported skipping

    Returns:
        One DirectiveHealth per row, in the order given
    """
    return tuple(
        _classify_one(row, now, error_rate_threshold, skip_threshold)
        for row in rows)


def _classify_one(
    row: Dict[str, Any],
    now: str,
    error_rate_threshold: float,
    skip_threshold: int,
) -> DirectiveHealth:
    """Pure: Apply the health precedence to one row."""
    total = row.get('total_executions') or 0
    errors = row.get('error_count') or 0
    rate = compute_error_rate(total, errors)
    overdue_seconds = _overdue_seconds(row.get('next_scheduled_time'), now)
    status = row.get('status') or 'unknown'
    consecutive_skips = row.get('consecutive_skip_count') or 0

    health, detail = _verdict(
        status=status,
        total=total,
        errors=errors,
        rate=rate,
        overdue_seconds=overdue_seconds,
        threshold=error_rate_threshold,
        last_error_type=row.get('last_error_type'),
        last_error_message=row.get('last_error_message'),
        skipping=_is_skipping(
            consecutive_skips,
            row.get('last_skip_time'),
            row.get('last_execution_time'),
            total,
            skip_threshold,
        ),
        consecutive_skips=consecutive_skips,
        last_skip_reason=row.get('last_skip_reason'),
    )

    return DirectiveHealth(
        directive_id=row.get('id') or row.get('directive_id'),
        name=row.get('name') or '',
        status=status,
        health=health,
        detail=detail,
        total_executions=total,
        error_count=errors,
        error_rate=rate,
        last_execution_time=row.get('last_execution_time'),
        next_scheduled_time=row.get('next_scheduled_time'),
        overdue_seconds=overdue_seconds,
        last_error_type=row.get('last_error_type'),
        last_error_message=row.get('last_error_message'),
        skip_count=row.get('skip_count') or 0,
        consecutive_skip_count=consecutive_skips,
        last_skip_time=row.get('last_skip_time'),
        last_skip_reason=row.get('last_skip_reason'),
    )


def _is_skipping(
    consecutive_skips: int,
    last_skip_time: Optional[str],
    last_execution_time: Optional[str],
    total: int,
    skip_threshold: int,
) -> bool:
    """
    Pure: True when skips are happening and runs are not.

    consecutive_skips is already reset by every recorded execution, so the
    timestamp comparison is a second guard for rows written by a runner that
    recorded executions some other way. An unparseable last_skip_time counts
    as newer: the counter is the primary evidence.
    """
    if consecutive_skips < max(skip_threshold, 1):
        return False
    if total <= 0 or not last_execution_time:
        return True
    skipped = _parse_iso(last_skip_time)
    executed = _parse_iso(last_execution_time)
    if skipped is None or executed is None:
        return True
    return skipped > executed


def _verdict(
    status: str,
    total: int,
    errors: int,
    rate: float,
    overdue_seconds: Optional[float],
    threshold: float,
    last_error_type: Optional[str],
    last_error_message: Optional[str],
    skipping: bool = False,
    consecutive_skips: int = 0,
    last_skip_reason: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Pure: Choose the health state and its one-line explanation.

    Precedence is most-actionable-first and deliberate: a directive already in
    error state is not better news for being on schedule.
    """
    if status == HEALTH_ERROR:
        message = last_error_message or 'no message recorded'
        return HEALTH_ERROR, f"directive status is error ({message})"

    if overdue_seconds is not None and overdue_seconds > 0:
        return HEALTH_OVERDUE, (
            f"overdue by {_humanize_seconds(overdue_seconds)} - expected to run "
            f"already; its runner may have died"
        )

    if skipping:
        since = "since its last run" if total > 0 else "and has never run"
        reason = last_skip_reason or 'no reason recorded'
        return HEALTH_SKIPPING, (
            f"skipped {consecutive_skips} occurrences {since} (last: {reason}) - "
            f"the host is probably not running at the scheduled time"
        )

    if total > 0 and rate >= threshold:
        return HEALTH_DEGRADED, (
            f"error rate {rate:.0%} at or above the {threshold:.0%} threshold"
        )

    if status == 'active' and total <= 0:
        if errors > 0:
            last = ': '.join(
                part for part in (last_error_type, last_error_message) if part)
            plural = 'error' if errors == 1 else 'errors'
            return HEALTH_NEVER_RUN, (
                f"active, never executed, {errors} dispatch {plural} "
                f"(last: {last or 'no detail recorded'}) - the runner is running "
                f"but refusing or failing to dispatch it"
            )
        return HEALTH_NEVER_RUN, (
            "active but never executed - the runner may not be deployed"
        )

    return HEALTH_OK, 'running normally'


def _overdue_seconds(next_scheduled_time: Optional[str], now: str) -> Optional[float]:
    """
    Pure: Seconds past the expected run time, None when not applicable.

    None means the question cannot be asked - no schedule was ever declared, or
    a timestamp is unparseable - which is different from being on time.
    """
    scheduled = _parse_iso(next_scheduled_time)
    current = _parse_iso(now)
    if scheduled is None or current is None:
        return None
    return (current - scheduled).total_seconds()


def _humanize_seconds(seconds: float) -> str:
    """Pure: Render a duration the way a status line should read."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Pure: Parse an ISO timestamp, None when absent or malformed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def summarize_health(verdicts: Tuple[DirectiveHealth, ...]) -> Dict[str, int]:
    """
    Pure: Count health verdicts by state.

    Returns states in HEALTH_ORDER with zero entries omitted, so the summary
    reads as a short sentence rather than a sparse table.

    Args:
        verdicts: Health verdicts from assess_directive_health

    Returns:
        Ordered mapping of health state to count
    """
    counts = {}
    for state in HEALTH_ORDER:
        count = sum(1 for v in verdicts if v.health == state)
        if count:
            counts[state] = count
    return counts


# ============================================================================
# Public API Functions (MCP Tools)
# ============================================================================

def get_directive_execution_stats(
    directive_id: Optional[int] = None,
    project_root: Optional[str] = None,
) -> ExecutionStatsResult:
    """
    Read execution statistics for one directive or all.

    The AI-facing read side of the hook execution model: the user's generated
    runner records executions through aimfp.hooks, and this is how the AI sees
    what happened while no session was open.

    Args:
        directive_id: One directive, or None for all
        project_root: Project root, defaults to the session root

    Returns:
        ExecutionStatsResult with a derived error_rate per row
    """
    root = project_root or get_cached_project_root()
    db_path = get_user_directives_db_path(root)
    if not database_exists(db_path):
        return ExecutionStatsResult(success=False, error=_NO_DATABASE)

    try:
        rows = _effect_read_stats(db_path, directive_id)
    except sqlite3.Error as exc:
        return ExecutionStatsResult(success=False, error=str(exc))

    stats = tuple(
        {**row, 'error_rate': compute_error_rate(
            row.get('total_executions') or 0, row.get('error_count') or 0)}
        for row in rows
    )
    return ExecutionStatsResult(
        success=True,
        stats=stats,
        total_count=len(stats),
        return_statements=get_return_statements('get_directive_execution_stats'),
    )


def get_recent_directive_errors(
    limit: int = 20,
    directive_id: Optional[int] = None,
    project_root: Optional[str] = None,
) -> ErrorLogResult:
    """
    Tail the JSON-lines error log, newest first.

    The database keeps only the last error per directive
    (store_last_error_only defaults to 1), so this file is the only place
    error history survives, under 90-day retention.

    Args:
        limit: Maximum records to return
        directive_id: Filter to one directive, or None for all
        project_root: Project root, defaults to the session root

    Returns:
        ErrorLogResult. malformed_lines counts unparseable records rather than
        hiding them - a truncated final line is normal when a run was
        interrupted mid-write.
    """
    if limit < 1:
        return ErrorLogResult(success=False, error="limit must be at least 1")

    root = project_root or get_cached_project_root()
    if not database_exists(get_user_directives_db_path(root)):
        return ErrorLogResult(success=False, error=_NO_DATABASE)

    config = load_log_config(root)
    log_path = os.path.join(
        resolve_log_path(root, config.error_log_dir), ERROR_LOG_NAME)

    records, malformed = _effect_read_error_log(log_path)

    if directive_id is not None:
        records = [r for r in records if r.get('directive_id') == directive_id]

    newest_first = tuple(reversed(records))
    return ErrorLogResult(
        success=True,
        errors=newest_first[:limit],
        total_count=len(newest_first),
        malformed_lines=malformed,
        log_path=log_path,
        return_statements=get_return_statements('get_recent_directive_errors'),
    )


def check_directive_health(
    project_root: Optional[str] = None,
    error_rate_threshold: float = 0.5,
    skip_threshold: int = 3,
) -> HealthResult:
    """
    Assess every directive and report which need attention.

    Overdue is derived from next_scheduled_time rather than from any process
    inspection - which is how a dead runner is detected without AIMFP ever
    knowing a process id.

    Args:
        project_root: Project root, defaults to the session root
        error_rate_threshold: Error rate at or above which a directive is
            reported degraded
        skip_threshold: Consecutive skipped occurrences at or above which a
            directive is reported skipping

    Returns:
        HealthResult. `summary` is designed to render as one sentence at
        session start; `needs_attention` is true when anything is not ok.
    """
    root = project_root or get_cached_project_root()
    db_path = get_user_directives_db_path(root)
    if not database_exists(db_path):
        return HealthResult(success=False, error=_NO_DATABASE)

    try:
        rows = _effect_read_stats(db_path, directive_id=None)
    except sqlite3.Error as exc:
        return HealthResult(success=False, error=str(exc))

    verdicts = assess_directive_health(
        rows, _now_iso(), error_rate_threshold, skip_threshold)
    summary = summarize_health(verdicts)

    return HealthResult(
        success=True,
        directives=verdicts,
        summary=summary,
        needs_attention=any(v.health != HEALTH_OK for v in verdicts),
        return_statements=get_return_statements('check_directive_health'),
    )


def _now_iso() -> str:
    """Effect: Current wall-clock time as an ISO string."""
    return datetime.now().isoformat(timespec='seconds')


# ============================================================================
# Effects
# ============================================================================

def _effect_read_stats(
    db_path: str,
    directive_id: Optional[int],
) -> Tuple[Dict[str, Any], ...]:
    """
    Effect: Read directives joined to their execution statistics.

    A LEFT JOIN, so a directive that has never run still appears with null
    counts - which is exactly the never_run case the health check reports.

    The schema 1.3 columns are selected only when the table has them; on an
    older database they are simply absent from the rows, and the pure
    classifiers treat absent as zero/None.
    """
    conn = None
    try:
        conn = _open_connection(db_path)
        present = {c['name'] for c in _get_table_info(conn, 'directive_executions')}
        optional = ''.join(
            f", de.{name}" for name in _OPTIONAL_STAT_COLUMNS if name in present)
        sql = f"""
            SELECT ud.id, ud.name, ud.status, ud.trigger_type,
                   COALESCE(de.total_executions, 0) AS total_executions,
                   COALESCE(de.success_count, 0)    AS success_count,
                   COALESCE(de.error_count, 0)      AS error_count,
                   de.last_execution_time, de.next_scheduled_time,
                   de.last_error_time, de.last_error_type, de.last_error_message,
                   de.avg_execution_time_ms, de.max_execution_time_ms{optional}
            FROM user_directives ud
            LEFT JOIN directive_executions de ON de.directive_id = ud.id
        """
        if directive_id is not None:
            sql += " WHERE ud.id = ?"
            rows = conn.execute(sql + " ORDER BY ud.id", (directive_id,)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY ud.id").fetchall()
        return tuple(dict(row) for row in rows)
    finally:
        if conn is not None:
            _close_connection(conn)


def _effect_read_error_log(log_path: str) -> Tuple[list, int]:
    """
    Effect: Parse the JSON-lines error log in file order.

    Returns (records, malformed_count). A missing log is an empty read, not an
    error: a project whose directives have never failed has no error log, and
    that is the good case.
    """
    if not os.path.isfile(log_path):
        return [], 0

    records = []
    malformed = 0
    try:
        with open(log_path, 'r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
                else:
                    malformed += 1
    except OSError:
        return records, malformed

    return records, malformed
