"""
AIMFP Watchdog - File System Watcher

Orchestrates file watching by connecting the filesystem_observer wrapper
to the analyzer and reminder modules. Handles debouncing and event routing.
"""

import os
import time
import datetime
from typing import Dict, Optional, Pattern, Tuple, Any

from ..wrappers.filesystem_observer import (
    FileEvent,
    EVENT_MODIFIED,
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_MOVED,
    _effect_create_observer,
    _effect_stop_observer,
)
from ..wrappers.file_ops import _effect_read_file, _effect_file_mtime
from .config import (
    should_exclude,
    get_relative_path,
    detect_language,
    DEBOUNCE_SECONDS,
    REMINDER_AUDIT_DRIFT,
)
from .analyzers import (
    build_audit_drift_reminders,
    content_digest,
    generate_file_reminders,
    generate_delete_reminders,
    _effect_file_audit_blocking,
    _effect_get_all_known_file_paths,
    _effect_get_file_by_path,
    _effect_is_file_reserved,
    _effect_get_finalized_functions,
    _effect_update_file_timestamp,
)
from .reminders import _effect_append_reminders, _effect_replace_file_reminders


# ============================================================================
# Pure Functions
# ============================================================================

def should_debounce(
    file_path: str,
    last_events: Dict[str, float],
    current_time: float,
    debounce_seconds: float = DEBOUNCE_SECONDS,
) -> bool:
    """
    Pure: Check if an event for this file should be debounced.

    Returns True if the file was processed less than debounce_seconds ago.
    """
    last_time = last_events.get(file_path, 0.0)
    return (current_time - last_time) < debounce_seconds


def make_iso_timestamp() -> str:
    """Pure-ish: Generate current UTC ISO timestamp string."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ'
    )


# ============================================================================
# Event Processing (Effect Functions)
# ============================================================================

def _effect_process_file_event(
    event: FileEvent,
    source_dir: str,
    project_root: str,
    project_db_path: str,
    reminders_path: str,
    function_pattern: Optional[Pattern[str]],
    content_hashes: Optional[Dict[str, str]] = None,
) -> None:
    """
    Effect: Process a file modified/created event.

    1. Skip a byte-identical rewrite (content_hashes: path -> last seen digest)
    2. Check DB registration
    3. Sync timestamp if stale
    4. Run function diff (regex) for non-Python files; audit Python files at
       full fidelity and keep ONE audit_drift reminder per file
    5. Write reminders
    """
    relative_path = get_relative_path(event.src_path, project_root)

    # Check if file is reserved (mid-reserve-finalize flow) — skip
    if _effect_is_file_reserved(project_db_path, relative_path):
        return

    # Get file info from DB
    db_file_row = _effect_get_file_by_path(project_db_path, relative_path)

    # Get file content and mtime
    file_content = _effect_read_file(event.src_path)
    if file_content is None:
        return
    file_mtime = _effect_file_mtime(event.src_path)
    if file_mtime is None:
        return

    # A rewrite with identical bytes (a regenerated file) changed nothing
    if content_hashes is not None:
        digest = content_digest(file_content)
        if content_hashes.get(relative_path) == digest:
            return
        content_hashes[relative_path] = digest

    # Tracked Python files get the full-fidelity audit instead of the regex diff
    audit_python = db_file_row is not None and detect_language(relative_path) == 'python'

    # Get finalized functions if file is registered
    db_functions: Tuple[Dict[str, Any], ...] = ()
    if db_file_row is not None:
        file_id = db_file_row.get('id')
        if file_id is not None:
            db_functions = _effect_get_finalized_functions(project_db_path, file_id)

    # Generate reminders (pure)
    reminders = generate_file_reminders(
        relative_path=relative_path,
        file_content=file_content,
        db_file_row=db_file_row,
        db_functions=db_functions,
        function_pattern=None if audit_python else function_pattern,
        file_mtime=file_mtime,
    )

    # If timestamp was stale, update it in DB
    if db_file_row is not None:
        from .analyzers import check_timestamp_stale
        db_updated_at = db_file_row.get('updated_at')
        if check_timestamp_stale(db_updated_at, file_mtime):
            file_id = db_file_row.get('id')
            if file_id is not None:
                _effect_update_file_timestamp(
                    project_db_path, file_id, make_iso_timestamp()
                )

    # Write reminders if any
    if reminders:
        _effect_append_reminders(reminders_path, reminders)

    # Re-audit the file; its audit_drift reminder is replaced (or cleared when clean)
    if audit_python:
        blocking = _effect_file_audit_blocking(project_root, relative_path)
        if blocking is not None:
            _effect_replace_file_reminders(
                reminders_path, REMINDER_AUDIT_DRIFT, relative_path,
                build_audit_drift_reminders(relative_path, blocking),
            )


def _effect_seed_content_hashes(project_root: str, project_db_path: str) -> Dict[str, str]:
    """
    Effect: Digest of every tracked file as it is now, so the first rewrite
    after startup that changes nothing is recognised as a no-op too.
    """
    hashes: Dict[str, str] = {}
    for relative_path in _effect_get_all_known_file_paths(project_db_path):
        content = _effect_read_file(os.path.join(project_root, relative_path))
        if content is not None:
            hashes[relative_path] = content_digest(content)
    return hashes


def _effect_process_delete_event(
    event: FileEvent,
    source_dir: str,
    project_root: str,
    project_db_path: str,
    reminders_path: str,
) -> None:
    """Effect: Process a file deletion event."""
    relative_path = get_relative_path(event.src_path, project_root)
    db_file_row = _effect_get_file_by_path(project_db_path, relative_path)
    is_registered = db_file_row is not None

    reminders = generate_delete_reminders(relative_path, is_registered)
    if reminders:
        _effect_append_reminders(reminders_path, reminders)


# ============================================================================
# Main Watcher Lifecycle
# ============================================================================

def _effect_create_event_callback(
    source_dir: str,
    project_root: str,
    project_db_path: str,
    reminders_path: str,
    function_pattern: Optional[Pattern[str]],
    excluded_dirs: frozenset[str],
    excluded_extensions: frozenset[str],
    ignore_patterns: tuple[str, ...] = (),
) -> tuple:
    """
    Effect: Create the event callback and debounce state for the observer.

    Returns:
        (callback_function, debounce_dict) — debounce_dict is mutable state
        held only by the callback closure.
    """
    debounce_dict: Dict[str, float] = {}
    content_hashes: Dict[str, str] = _effect_seed_content_hashes(project_root, project_db_path)

    def on_event(event: FileEvent) -> None:
        # Exclusion check — pass the project-relative path so .watchdogignore
        # patterns anchor to the project root.
        relative_path = get_relative_path(event.src_path, project_root)
        if should_exclude(
            relative_path, excluded_dirs, excluded_extensions, ignore_patterns
        ):
            return

        # Debounce check
        now = time.time()
        if should_debounce(event.src_path, debounce_dict, now):
            return
        debounce_dict[event.src_path] = now

        # Route by event type
        if event.event_type in (EVENT_MODIFIED, EVENT_CREATED):
            _effect_process_file_event(
                event, source_dir, project_root, project_db_path,
                reminders_path, function_pattern, content_hashes,
            )
        elif event.event_type == EVENT_DELETED:
            _effect_process_delete_event(
                event, source_dir, project_root,
                project_db_path, reminders_path,
            )
        elif event.event_type == EVENT_MOVED:
            # Treat as delete + create
            _effect_process_delete_event(
                FileEvent(
                    event_type=EVENT_DELETED,
                    src_path=event.src_path,
                    is_directory=event.is_directory,
                    timestamp=event.timestamp,
                ),
                source_dir, project_root,
                project_db_path, reminders_path,
            )
            if event.dest_path is not None:
                _effect_process_file_event(
                    FileEvent(
                        event_type=EVENT_CREATED,
                        src_path=event.dest_path,
                        is_directory=event.is_directory,
                        timestamp=event.timestamp,
                    ),
                    source_dir, project_root, project_db_path,
                    reminders_path, function_pattern, content_hashes,
                )

    return (on_event, debounce_dict)


def _effect_start_watching(
    source_dir: str,
    project_root: str,
    project_db_path: str,
    reminders_path: str,
    function_pattern: Optional[Pattern[str]],
    excluded_dirs: frozenset[str],
    excluded_extensions: frozenset[str],
    ignore_patterns: tuple[str, ...] = (),
):
    """
    Effect: Start the file system observer.

    Returns the running Observer instance. Caller is responsible for
    stopping it via _effect_stop_observer.
    """
    on_event, _ = _effect_create_event_callback(
        source_dir, project_root, project_db_path, reminders_path,
        function_pattern, excluded_dirs, excluded_extensions, ignore_patterns,
    )
    return _effect_create_observer(source_dir, on_event, recursive=True)
