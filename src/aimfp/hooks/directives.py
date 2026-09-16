"""
AIMFP Hooks - User Directive Runtime Activation API

The surface a generated Use Case 2 runner imports. AIMFP never executes a
user directive's action and never imports the user's code: run_directive is
higher-order, so the caller passes its own handler in.

    from aimfp.hooks.directives import get_due_directives, run_directive

    for directive in get_due_directives(project_root=ROOT).directives:
        run_directive(directive.directive_id, HANDLERS[directive.name], ROOT)

Nothing here is an MCP tool, and nothing here may become one. If the AI could
call run_directive, it would execute a user's automation inside an MCP session
at an arbitrary moment, with the AI's process as the runtime - the exact thing
the hook execution model exists to prevent. The AI's relationship to these
functions is that it WRITES CODE CALLING THEM during user_directive_implement.

The scheduler lives in the user's project, not here. AIMFP has no timer: it is
an MCP server answering tool calls, and no AI session is open at 3am when a
time-triggered directive fires. What AIMFP owns is the answer to "what is due"
and the record of "what happened".
"""

import json
import sqlite3
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..database.connection import (
    _close_connection,
    _open_connection,
    database_exists,
    get_user_directives_db_path,
)
from .config import load_log_config
from .logs import (
    append_error_log,
    append_execution_log,
    build_error_record,
    build_execution_record,
)
from .results import (
    DueDirective,
    DueDirectivesResult,
    ExecutionResult,
    ExecutionToken,
    HookMutationResult,
)

# Triggers AIMFP cannot evaluate - only the caller knows whether the event
# happened or the condition holds, so these are always handed back.
_CALLER_JUDGED_TRIGGERS = frozenset({'event', 'condition', 'manual'})

_NO_DATABASE = (
    "No user_directives.db for this project. Hooks are Use Case 2 only; "
    "a Use Case 1 project has no user directives to run."
)


# ============================================================================
# Pure Logic
# ============================================================================

def is_due(
    trigger_type: str,
    next_scheduled_time: Optional[str],
    now: str,
) -> bool:
    """
    Pure: Decide whether one directive is due.

    Time triggers are due when next_scheduled_time has passed. A time
    directive that has never been scheduled is due immediately - that is the
    first run, and withholding it would mean a directive that never starts.

    Event, condition, and manual triggers are always returned: AIMFP cannot
    evaluate them, so the judgment belongs to the caller.

    Args:
        trigger_type: 'time', 'event', 'condition', or 'manual'
        next_scheduled_time: ISO timestamp the runner last declared, if any
        now: ISO timestamp to evaluate against

    Returns:
        True if the caller should consider this directive for execution
    """
    if trigger_type in _CALLER_JUDGED_TRIGGERS:
        return True
    if trigger_type != 'time':
        return False
    if not next_scheduled_time:
        return True

    scheduled = _parse_iso(next_scheduled_time)
    current = _parse_iso(now)
    if scheduled is None or current is None:
        return True
    return scheduled <= current


def update_running_average(
    previous_average: Optional[float],
    previous_count: int,
    new_duration_ms: float,
) -> float:
    """
    Pure: Fold one new duration into a running average.

    The schema deliberately stores no execution history
    (store_execution_history defaults to 0), so the average must be
    maintained incrementally from the previous average and count rather than
    recomputed from rows that do not exist.

    Args:
        previous_average: Average before this run, None on the first
        previous_count: Executions counted before this run
        new_duration_ms: Duration of the run just completed

    Returns:
        Updated average in milliseconds
    """
    if previous_average is None or previous_count <= 0:
        return float(new_duration_ms)
    total = previous_average * previous_count + new_duration_ms
    return total / (previous_count + 1)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Pure: Parse an ISO timestamp, None when absent or malformed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _parse_json_object(value: Optional[str]) -> Dict[str, Any]:
    """Pure: Parse a JSON column into a dict, empty when absent or malformed."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_due_directive(row: Any) -> DueDirective:
    """Pure: Map a joined directive/execution row onto DueDirective."""
    return DueDirective(
        directive_id=row['id'],
        name=row['name'],
        trigger_type=row['trigger_type'],
        trigger_config=_parse_json_object(row['trigger_config']),
        action_type=row['action_type'],
        action_config=_parse_json_object(row['action_config']),
        implementation_file_path=row['implementation_file_path'],
        next_scheduled_time=row['next_scheduled_time'],
        last_execution_time=row['last_execution_time'],
    )


def _now_iso() -> str:
    """Effect: Current wall-clock time as an ISO string."""
    return datetime.now().isoformat(timespec='seconds')


# ============================================================================
# Public Hooks - Dispatch
# ============================================================================

def get_due_directives(
    project_root: str,
    now: Optional[str] = None,
) -> DueDirectivesResult:
    """
    HOOK (library API, not an MCP tool).

    Return the active directives the caller's runner may execute now.

    now is a parameter so due-detection is testable without manipulating the
    system clock; it defaults to the current time.

    Args:
        project_root: Absolute path to the project root
        now: ISO timestamp to evaluate against, defaults to now

    Returns:
        DueDirectivesResult. A missing user_directives.db is a clean
        success=False, never an exception.
    """
    db_path = get_user_directives_db_path(project_root)
    if not database_exists(db_path):
        return DueDirectivesResult(success=False, error=_NO_DATABASE)

    evaluated_at = now or _now_iso()
    conn = None
    try:
        conn = _open_connection(db_path)
        rows = conn.execute(
            """
            SELECT ud.id, ud.name, ud.trigger_type, ud.trigger_config,
                   ud.action_type, ud.action_config, ud.implementation_file_path,
                   de.next_scheduled_time, de.last_execution_time
            FROM user_directives ud
            LEFT JOIN directive_executions de ON de.directive_id = ud.id
            WHERE ud.status = 'active'
            ORDER BY ud.priority DESC, ud.id ASC
            """
        ).fetchall()
    except sqlite3.Error as exc:
        return DueDirectivesResult(success=False, error=str(exc))
    finally:
        if conn is not None:
            _close_connection(conn)

    due = tuple(
        _row_to_due_directive(row) for row in rows
        if is_due(row['trigger_type'], row['next_scheduled_time'], evaluated_at)
    )
    return DueDirectivesResult(
        success=True,
        directives=due,
        total_count=len(rows),
    )


# ============================================================================
# Public Hooks - Execution Recording
# ============================================================================

def record_execution_start(
    directive_id: int,
    project_root: str,
) -> ExecutionToken:
    """
    HOOK (library API, not an MCP tool).

    Open an execution record and return a token for record_execution_end.

    Use the start/end pair when the work cannot be wrapped in a callable - a
    subprocess, a webhook, or a run spanning processes. When it can be
    wrapped, prefer run_directive.

    Args:
        directive_id: Directive about to execute
        project_root: Absolute path to the project root

    Returns:
        ExecutionToken. On failure, valid=False with error set; pass it to
        record_execution_end anyway and the failure surfaces there.
    """
    db_path = get_user_directives_db_path(project_root)
    started_monotonic = time.monotonic()
    started_at = _now_iso()

    if not database_exists(db_path):
        return ExecutionToken(
            directive_id=directive_id,
            project_root=project_root,
            started_at=started_at,
            started_monotonic=started_monotonic,
            valid=False,
            error=_NO_DATABASE,
        )

    return ExecutionToken(
        directive_id=directive_id,
        project_root=project_root,
        started_at=started_at,
        started_monotonic=started_monotonic,
        valid=True,
    )


def record_execution_end(
    token: ExecutionToken,
    success: bool,
    error: Optional[str] = None,
    traceback_text: Optional[str] = None,
) -> ExecutionResult:
    """
    HOOK (library API, not an MCP tool).

    Close the execution opened by record_execution_start.

    Computes duration from the token's monotonic start, writes the JSON-lines
    record, and folds the run into directive_executions: counts, running
    average, maximum, and the last_error fields on failure.

    This is the single place a failed execution is written to the error log,
    whichever path reached it - run_directive hands its caught traceback here
    rather than logging separately, so one failure produces one error line.

    Args:
        token: Token from record_execution_start
        success: Whether the caller's handler succeeded
        error: Error message when it did not
        traceback_text: Full traceback, when the caller has one. The database
            keeps only a brief message (store_last_error_only defaults to 1);
            the traceback belongs in the file, where retention is 90 days.

    Returns:
        ExecutionResult. success reflects whether AIMFP recorded the run;
        action_succeeded reflects the handler's own outcome.
    """
    duration_ms = (time.monotonic() - token.started_monotonic) * 1000.0

    if not token.valid:
        return ExecutionResult(
            success=False,
            directive_id=token.directive_id,
            action_succeeded=success,
            duration_ms=duration_ms,
            action_error=error,
            error=token.error,
        )

    config = load_log_config(token.project_root)
    directive_name = _effect_directive_name(token.project_root, token.directive_id)

    append_execution_log(
        token.project_root,
        config,
        build_execution_record(
            directive_id=token.directive_id,
            directive_name=directive_name,
            started_at=token.started_at,
            duration_ms=duration_ms,
            succeeded=success,
            error=error,
        ),
    )

    if not success:
        append_error_log(
            token.project_root,
            config,
            build_error_record(
                directive_id=token.directive_id,
                error_type=(
                    'handler_exception' if traceback_text else 'execution_error'),
                message=error or 'Execution failed without a message',
                occurred_at=_now_iso(),
                traceback_text=traceback_text,
            ),
        )

    recorded, record_error = _effect_record_execution(
        project_root=token.project_root,
        directive_id=token.directive_id,
        duration_ms=duration_ms,
        succeeded=success,
        error_type=_classify_error(success, traceback_text),
        error_message=error,
    )

    return ExecutionResult(
        success=recorded,
        directive_id=token.directive_id,
        action_succeeded=success,
        duration_ms=duration_ms,
        action_error=error,
        error=record_error,
    )


def run_directive(
    directive_id: int,
    action: Callable[[], Any],
    project_root: str,
) -> ExecutionResult:
    """
    HOOK (library API, not an MCP tool).

    The primary entry point for a generated runner: time the caller's handler,
    record the outcome, and never let its exception escape.

    Higher-order by design - the caller passes its own callable in, so AIMFP
    runs the automation without ever importing user code. A thin composition
    of record_execution_start and record_execution_end.

    An exception from action is caught, recorded with its traceback, and
    reported as action_succeeded=False. It is never re-raised: an unhandled
    exception would otherwise kill the caller's scheduler thread and silently
    stop every other directive.

    Args:
        directive_id: Directive being executed
        action: Zero-argument callable performing the actual work
        project_root: Absolute path to the project root

    Returns:
        ExecutionResult
    """
    token = record_execution_start(directive_id, project_root)

    try:
        action()
    except Exception as exc:  # noqa: BLE001 - deliberate: see docstring
        return record_execution_end(
            token,
            success=False,
            error=str(exc),
            traceback_text=''.join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    return record_execution_end(token, success=True)


# ============================================================================
# Public Hooks - Out-of-Band Recording
# ============================================================================

def record_directive_error(
    directive_id: int,
    error_type: str,
    message: str,
    project_root: str,
) -> HookMutationResult:
    """
    HOOK (library API, not an MCP tool).

    Record a failure that happened outside any execution - a trigger that
    misfired, a missing dependency, a scheduler that could not dispatch.

    Updates error_count and the last_error fields without counting an
    execution, so the error rate stays honest: a directive that failed to
    start ten times has ten errors and zero executions.

    Args:
        directive_id: Directive the error belongs to
        error_type: Short classification, e.g. 'dependency_missing'
        message: Brief error message
        project_root: Absolute path to the project root

    Returns:
        HookMutationResult
    """
    db_path = get_user_directives_db_path(project_root)
    if not database_exists(db_path):
        return HookMutationResult(
            success=False, directive_id=directive_id, error=_NO_DATABASE)

    config = load_log_config(project_root)
    append_error_log(
        project_root,
        config,
        build_error_record(
            directive_id=directive_id,
            error_type=error_type,
            message=message,
            occurred_at=_now_iso(),
        ),
    )

    recorded, record_error = _effect_record_execution(
        project_root=project_root,
        directive_id=directive_id,
        duration_ms=None,
        succeeded=False,
        error_type=error_type,
        error_message=message,
        count_execution=False,
    )
    return HookMutationResult(
        success=recorded, directive_id=directive_id, error=record_error)


def set_next_scheduled_time(
    directive_id: int,
    when: Optional[str],
    project_root: str,
) -> HookMutationResult:
    """
    HOOK (library API, not an MCP tool).

    Let the caller's scheduler declare when it next expects to fire.

    This is the field that makes overdue detection possible at all. Without
    it AIMFP cannot distinguish a directive that is idle by design from one
    whose runner has died - which is how the monitoring tools report a dead
    service without ever knowing a process id.

    Args:
        directive_id: Directive being scheduled
        when: ISO timestamp of the next expected run, None to clear
        project_root: Absolute path to the project root

    Returns:
        HookMutationResult
    """
    db_path = get_user_directives_db_path(project_root)
    if not database_exists(db_path):
        return HookMutationResult(
            success=False, directive_id=directive_id, error=_NO_DATABASE)

    conn = None
    try:
        conn = _open_connection(db_path)
        _effect_ensure_execution_row(conn, directive_id)
        conn.execute(
            "UPDATE directive_executions SET next_scheduled_time = ? "
            "WHERE directive_id = ?",
            (when, directive_id),
        )
        conn.commit()
        return HookMutationResult(success=True, directive_id=directive_id)
    except sqlite3.Error as exc:
        return HookMutationResult(
            success=False, directive_id=directive_id, error=str(exc))
    finally:
        if conn is not None:
            _close_connection(conn)


# ============================================================================
# Effects
# ============================================================================

def _effect_ensure_execution_row(
    conn: sqlite3.Connection,
    directive_id: int,
) -> None:
    """
    Effect: Guarantee exactly one directive_executions row for a directive.

    The schema carries no UNIQUE constraint on directive_id, so a blind
    INSERT would accumulate duplicate statistics rows for the same directive.
    """
    existing = conn.execute(
        "SELECT id FROM directive_executions WHERE directive_id = ?",
        (directive_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO directive_executions (directive_id) VALUES (?)",
            (directive_id,),
        )


def _effect_record_execution(
    project_root: str,
    directive_id: int,
    duration_ms: Optional[float],
    succeeded: bool,
    error_type: Optional[str],
    error_message: Optional[str],
    count_execution: bool = True,
) -> Tuple[bool, Optional[str]]:
    """
    Effect: Fold one outcome into the directive's statistics row.

    Reads the current counters, computes the new values with the pure
    update_running_average, and writes them back in one transaction.

    count_execution=False records an error without counting an execution,
    which is what record_directive_error needs.

    Returns:
        (recorded, error_message)
    """
    db_path = get_user_directives_db_path(project_root)
    conn = None
    try:
        conn = _open_connection(db_path)
        _effect_ensure_execution_row(conn, directive_id)

        current = conn.execute(
            "SELECT total_executions, success_count, error_count, "
            "avg_execution_time_ms, max_execution_time_ms "
            "FROM directive_executions WHERE directive_id = ?",
            (directive_id,),
        ).fetchone()

        updates, params = _build_statistics_update(
            current=current,
            duration_ms=duration_ms,
            succeeded=succeeded,
            error_type=error_type,
            error_message=error_message,
            count_execution=count_execution,
        )
        conn.execute(
            f"UPDATE directive_executions SET {', '.join(updates)} "
            "WHERE directive_id = ?",
            (*params, directive_id),
        )
        conn.commit()
        return True, None
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        if conn is not None:
            _close_connection(conn)


def _build_statistics_update(
    current: Any,
    duration_ms: Optional[float],
    succeeded: bool,
    error_type: Optional[str],
    error_message: Optional[str],
    count_execution: bool,
) -> Tuple[List[str], List[Any]]:
    """
    Pure: Build the SET clauses and parameters for a statistics update.

    Separated from the write so the counter arithmetic - especially the
    running average, which cannot be recomputed from history - is testable
    without a database.
    """
    previous_total = (current['total_executions'] or 0) if current else 0
    previous_avg = (current['avg_execution_time_ms'] if current else None)
    previous_max = (current['max_execution_time_ms'] or 0.0) if current else 0.0

    updates: List[str] = []
    params: List[Any] = []

    if count_execution:
        updates.append("total_executions = total_executions + 1")
        updates.append("last_execution_time = ?")
        params.append(_now_iso())

        if succeeded:
            updates.append("success_count = success_count + 1")

        if duration_ms is not None:
            updates.append("avg_execution_time_ms = ?")
            params.append(update_running_average(
                previous_avg, previous_total, duration_ms))
            updates.append("max_execution_time_ms = ?")
            params.append(max(previous_max, duration_ms))

    if not succeeded:
        updates.append("error_count = error_count + 1")
        updates.append("last_error_time = ?")
        params.append(_now_iso())
        updates.append("last_error_type = ?")
        params.append(error_type)
        updates.append("last_error_message = ?")
        params.append(error_message)

    return updates, params


def _effect_directive_name(project_root: str, directive_id: int) -> Optional[str]:
    """Effect: Look up a directive's name for the log record, None if unknown."""
    db_path = get_user_directives_db_path(project_root)
    conn = None
    try:
        conn = _open_connection(db_path)
        row = conn.execute(
            "SELECT name FROM user_directives WHERE id = ?", (directive_id,)
        ).fetchone()
        return row['name'] if row else None
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            _close_connection(conn)


def _classify_error(success: bool, traceback_text: Optional[str]) -> Optional[str]:
    """
    Pure: Name the error type stored on the statistics row.

    A traceback means the caller's handler raised; its absence means the
    caller reported failure itself. Kept identical to the classification
    written to the error log so the two never disagree.
    """
    if success:
        return None
    return 'handler_exception' if traceback_text else 'execution_error'
