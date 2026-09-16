"""
AIMFP Hooks - Log Records, Rotation Policy, and Writers

AIMFP owns the log writer for every Use Case 2 project. That is deliberate:
if each generated runner wrote its own logging, the record shape would drift
per project and nothing could read them back reliably. One writer here means
the monitoring tools can parse what they find.

Rotation is a side effect of appending, not a step anyone performs. Under the
hook execution model nothing is running between executions to rotate a log, so
the only moment rotation can happen is when a line is about to be written.

The policy decision is separated from the writing. plan_rotation is pure: it
takes observed state and returns a RotationPlan, so retention and compression
can be tested at any timescale without touching disk or waiting days.

Every writer here returns bool and never raises. A logging failure must not
abort the caller's automation - losing a log line is bad, killing the user's
scheduled job because a disk filled up is worse.
"""

import gzip
import json
import os
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from .config import resolve_log_path
from .results import LogConfig, RotationPlan

# Filenames inside the configured log directories
EXECUTION_LOG_NAME = 'execution.jsonl'
ERROR_LOG_NAME = 'errors.jsonl'

_BYTES_PER_MB = 1024 * 1024
_STAMP_FORMAT = '%Y%m%d-%H%M%S'


# ============================================================================
# Record Builders (Pure)
# ============================================================================

def build_execution_record(
    directive_id: int,
    directive_name: Optional[str],
    started_at: str,
    duration_ms: Optional[float],
    succeeded: bool,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pure: Build the JSON-lines record for one directive execution.

    Separated from writing so the record shape is testable and stays
    identical across every Use Case 2 project.

    Args:
        directive_id: Directive that executed
        directive_name: Directive name, when known
        started_at: ISO timestamp the execution began
        duration_ms: Measured duration, None if the run never started
        succeeded: Whether the caller's handler succeeded
        error: Error message when it did not

    Returns:
        Dict ready to serialize as one JSON line
    """
    record = {
        'directive_id': directive_id,
        'directive_name': directive_name,
        'started_at': started_at,
        'duration_ms': duration_ms,
        'outcome': 'success' if succeeded else 'error',
    }
    if error:
        record['error'] = error
    return record


def build_error_record(
    directive_id: int,
    error_type: str,
    message: str,
    occurred_at: str,
    traceback_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pure: Build the JSON-lines record for one directive error.

    Used both by failed executions and by errors raised outside any run.

    Args:
        directive_id: Directive the error belongs to
        error_type: Short classification, e.g. 'handler_exception'
        message: Brief error message
        occurred_at: ISO timestamp
        traceback_text: Full traceback when available

    Returns:
        Dict ready to serialize as one JSON line
    """
    record = {
        'directive_id': directive_id,
        'error_type': error_type,
        'message': message,
        'occurred_at': occurred_at,
    }
    if traceback_text:
        record['traceback'] = traceback_text
    return record


# ============================================================================
# Rotation Policy (Pure)
# ============================================================================

def plan_rotation(
    policy: str,
    base_name: str,
    current_size_bytes: int,
    current_mtime: Optional[str],
    now: str,
    max_size_mb: int,
    retention_days: int,
    compress_after_days: int = 0,
    existing_archives: Tuple[Tuple[str, str], ...] = (),
) -> RotationPlan:
    """
    Pure: Decide what a log directory needs before the next append.

    Args:
        policy: 'daily', 'weekly', or 'size'
        base_name: Current log filename, e.g. 'execution.jsonl'
        current_size_bytes: Size of the current log, 0 if absent
        current_mtime: ISO mtime of the current log, None if absent
        now: ISO timestamp to evaluate against (injected, never read here)
        max_size_mb: Size threshold for the 'size' policy
        retention_days: Archives older than this are deleted
        compress_after_days: Archives older than this are gzipped, 0 disables
        existing_archives: (path, iso_mtime) pairs already in the directory

    Returns:
        RotationPlan describing rotation, compression, and deletion
    """
    now_dt = _parse_iso(now)

    return RotationPlan(
        should_rotate=_should_rotate(
            policy, current_size_bytes, current_mtime, now_dt, max_size_mb),
        archive_name=_archive_name(base_name, current_mtime, now),
        compress_paths=_archives_older_than(
            existing_archives, now_dt, compress_after_days, skip_compressed=True),
        delete_paths=_archives_older_than(
            existing_archives, now_dt, retention_days, skip_compressed=False),
    )


def _should_rotate(
    policy: str,
    current_size_bytes: int,
    current_mtime: Optional[str],
    now_dt: Optional[datetime],
    max_size_mb: int,
) -> bool:
    """Pure: Evaluate the rotation trigger for one policy."""
    if current_size_bytes <= 0:
        return False

    if policy == 'size':
        return current_size_bytes >= max(1, max_size_mb) * _BYTES_PER_MB

    mtime_dt = _parse_iso(current_mtime)
    if mtime_dt is None or now_dt is None:
        return False

    if policy == 'daily':
        return mtime_dt.date() < now_dt.date()
    if policy == 'weekly':
        return (now_dt - mtime_dt) >= timedelta(days=7)
    return False


def _archive_name(
    base_name: str,
    current_mtime: Optional[str],
    now: str,
) -> Optional[str]:
    """
    Pure: Name the archive a rotation would produce.

    Stamped with the current log's own mtime so the archive name describes the
    period it covers, falling back to now when the mtime is unreadable.
    """
    stamp_source = _parse_iso(current_mtime) or _parse_iso(now)
    if stamp_source is None:
        return None

    stem, _, extension = base_name.partition('.')
    stamp = stamp_source.strftime(_STAMP_FORMAT)
    return f"{stem}-{stamp}.{extension}" if extension else f"{stem}-{stamp}"


def _archives_older_than(
    existing_archives: Tuple[Tuple[str, str], ...],
    now_dt: Optional[datetime],
    threshold_days: int,
    skip_compressed: bool,
) -> Tuple[str, ...]:
    """
    Pure: Select archives older than a day threshold.

    threshold_days of 0 disables the rule entirely rather than selecting
    everything, matching how the schema treats a disabled policy.
    """
    if threshold_days <= 0 or now_dt is None:
        return ()

    cutoff = now_dt - timedelta(days=threshold_days)
    selected = []
    for path, mtime in existing_archives:
        if skip_compressed and path.endswith('.gz'):
            continue
        mtime_dt = _parse_iso(mtime)
        if mtime_dt is not None and mtime_dt < cutoff:
            selected.append(path)
    return tuple(selected)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Pure: Parse an ISO timestamp, None when absent or malformed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# ============================================================================
# Writers
# ============================================================================

def append_execution_log(
    project_root: str,
    config: LogConfig,
    record: Dict[str, Any],
) -> bool:
    """
    Effect: Append one JSON-lines record to the execution log.

    Applies the rotation and retention plan first. Never raises - returns
    False if logging is disabled or the write fails, because a logging
    failure must not abort the caller's automation.

    Args:
        project_root: Absolute path to the project root
        config: Resolved logging configuration
        record: Record dict from build_execution_record

    Returns:
        True if the line was written
    """
    if not config.execution_logs_enabled:
        return False

    return _effect_append_rotated(
        directory=resolve_log_path(project_root, config.execution_log_dir),
        base_name=EXECUTION_LOG_NAME,
        line=_encode_line(record),
        policy=config.execution_log_rotation,
        max_size_mb=config.execution_log_max_size_mb,
        retention_days=config.execution_log_retention_days,
        compress_after_days=config.execution_log_compress_after_days,
    )


def append_error_log(
    project_root: str,
    config: LogConfig,
    record: Dict[str, Any],
) -> bool:
    """
    Effect: Append one JSON-lines record to the error log.

    Applies rotation and the longer error retention first. Never raises.

    Args:
        project_root: Absolute path to the project root
        config: Resolved logging configuration
        record: Record dict from build_error_record

    Returns:
        True if the line was written
    """
    if not config.error_logs_enabled:
        return False

    return _effect_append_rotated(
        directory=resolve_log_path(project_root, config.error_log_dir),
        base_name=ERROR_LOG_NAME,
        line=_encode_line(record),
        policy=config.error_log_rotation,
        max_size_mb=config.error_log_max_size_mb,
        retention_days=config.error_log_retention_days,
        compress_after_days=0,
    )


def append_lifecycle_log(
    project_root: str,
    config: LogConfig,
    message: str,
) -> bool:
    """
    Effect: Append one human-readable line to the lifecycle log.

    The durable record of activation, deactivation, and health transitions.
    Never raises.

    Args:
        project_root: Absolute path to the project root
        config: Resolved logging configuration
        message: Human-readable message

    Returns:
        True if the line was written
    """
    path = resolve_log_path(project_root, config.lifecycle_log_path)
    directory, base_name = os.path.split(path)
    stamped = f"{_now_iso()}  {message}"

    return _effect_append_rotated(
        directory=directory,
        base_name=base_name,
        line=stamped,
        policy='weekly',
        max_size_mb=0,
        retention_days=90,
        compress_after_days=0,
    )


def _encode_line(record: Dict[str, Any]) -> str:
    """Pure: Serialize a record as one JSON line, never spanning lines."""
    return json.dumps(record, separators=(',', ':'), default=str)


def _now_iso() -> str:
    """Effect: Current wall-clock time as an ISO string."""
    return datetime.now().isoformat(timespec='seconds')


# ============================================================================
# Effects
# ============================================================================

def _effect_append_rotated(
    directory: str,
    base_name: str,
    line: str,
    policy: str,
    max_size_mb: int,
    retention_days: int,
    compress_after_days: int,
) -> bool:
    """
    Effect: Apply the rotation plan for a log directory, then append one line.

    Swallows every exception by design: a full disk, a read-only mount, or a
    permissions change must degrade to "no log line" rather than propagating
    into the caller's scheduler thread.
    """
    try:
        os.makedirs(directory, exist_ok=True)
        current_path = os.path.join(directory, base_name)

        plan = plan_rotation(
            policy=policy,
            base_name=base_name,
            current_size_bytes=_effect_size(current_path),
            current_mtime=_effect_mtime_iso(current_path),
            now=_now_iso(),
            max_size_mb=max_size_mb,
            retention_days=retention_days,
            compress_after_days=compress_after_days,
            existing_archives=_effect_list_archives(directory, base_name),
        )
        _effect_apply_plan(directory, current_path, plan)

        with open(current_path, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
        return True
    except (OSError, ValueError):
        return False


def _effect_apply_plan(
    directory: str,
    current_path: str,
    plan: RotationPlan,
) -> None:
    """
    Effect: Carry out a RotationPlan - rotate, compress, then delete.

    Each step is independent: a failure to compress one archive must not
    prevent deletion of another or block the append that follows.
    """
    if plan.should_rotate and plan.archive_name:
        _effect_move(current_path, os.path.join(directory, plan.archive_name))

    for path in plan.compress_paths:
        _effect_compress(path)

    for path in plan.delete_paths:
        _effect_remove(path)


def _effect_size(path: str) -> int:
    """Effect: File size in bytes, 0 when absent."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _effect_mtime_iso(path: str) -> Optional[str]:
    """Effect: File mtime as an ISO string, None when absent."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    except OSError:
        return None


def _effect_list_archives(
    directory: str,
    base_name: str,
) -> Tuple[Tuple[str, str], ...]:
    """
    Effect: List rotated archives beside the current log as (path, iso_mtime).

    Matches on the stem so the current log itself is never returned as its own
    archive, and unrelated files in the directory are ignored.
    """
    stem, _, _ = base_name.partition('.')
    archives = []
    try:
        for entry in os.listdir(directory):
            if entry == base_name or not entry.startswith(f"{stem}-"):
                continue
            path = os.path.join(directory, entry)
            mtime = _effect_mtime_iso(path)
            if mtime is not None:
                archives.append((path, mtime))
    except OSError:
        return ()
    return tuple(sorted(archives))


def _effect_move(source: str, destination: str) -> bool:
    """Effect: Rename the current log onto its archive path."""
    try:
        os.replace(source, destination)
        return True
    except OSError:
        return False


def _effect_compress(path: str) -> bool:
    """
    Effect: Gzip an archive and remove the original.

    The original is removed only after the compressed copy is fully written,
    so an interrupted compression loses nothing.
    """
    try:
        with open(path, 'rb') as source:
            with gzip.open(f"{path}.gz", 'wb') as target:
                shutil.copyfileobj(source, target)
        os.remove(path)
        return True
    except OSError:
        return False


def _effect_remove(path: str) -> bool:
    """Effect: Delete an expired archive."""
    try:
        os.remove(path)
        return True
    except OSError:
        return False
