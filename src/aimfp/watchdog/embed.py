"""
AIMFP Watchdog - In-Process Embedding API

Public API for embedding hosts that run the watcher inside their own
process instead of the detached `python -m aimfp.watchdog` subprocess.

Differences from the subprocess path (__main__.py), both deliberate:
- No PID file is written — the host owns the observer thread's lifetime.
- Startup reconciliation only runs when requested via run_reconciliation.

The subprocess path is unchanged and unaffected. Configuration assembly
(infrastructure, user exclusions, .watchdogignore) is identical.
"""

import os
from dataclasses import dataclass
from typing import Any, Optional

from .config import (
    get_watchdog_dir,
    get_reminders_path,
    get_project_db_path,
    get_preferences_db_path,
    build_exclusion_sets,
    get_function_pattern,
)
from .reconciliation import (
    _read_infrastructure_value,
    _read_user_exclusions,
    _read_watchdogignore,
    run_startup_reconciliation,
)
from .watcher import _effect_start_watching
from ..wrappers.file_ops import _effect_ensure_dir
from ..wrappers.filesystem_observer import _effect_stop_observer


# ============================================================================
# Data Structures (Immutable)
# ============================================================================

@dataclass(frozen=True)
class WatcherHandle:
    """
    Immutable handle for a running in-process watcher.

    Attributes:
        observer: Running observer thread (opaque — pass to stop_watcher)
        project_root: Project root the watcher is bound to
        source_directory: Absolute path being watched
        reminders_path: Path reminders are appended to
    """
    observer: Any
    project_root: str
    source_directory: str
    reminders_path: str


# ============================================================================
# Public API
# ============================================================================

def start_watcher(
    project_root: str,
    run_reconciliation: bool = False,
) -> WatcherHandle:
    """
    Effect: Start the file-system watcher in the current process.

    Reads configuration exactly like the `python -m aimfp.watchdog`
    subprocess: source_directory and primary_language from project.db
    infrastructure, exclusions from user_preferences.db, and the
    project-root .watchdogignore. No PID file is written.

    Args:
        project_root: Absolute path to the project root
        run_reconciliation: Run the startup reconciliation scan before
            watching (the subprocess path runs it unless aimfp_run
            already did; embedding hosts opt in explicitly)

    Returns:
        WatcherHandle carrying the running observer

    Raises:
        ValueError: project.db missing, source_directory unset, or
            source directory does not exist on disk
    """
    project_db_path = get_project_db_path(project_root)
    if not os.path.isfile(project_db_path):
        raise ValueError(f"project.db not found at {project_db_path}")

    source_directory = _read_infrastructure_value(project_db_path, 'source_directory')
    if not source_directory:
        raise ValueError("source_directory not set in infrastructure table")
    if not os.path.isabs(source_directory):
        source_directory = os.path.join(project_root, source_directory)
    if not os.path.isdir(source_directory):
        raise ValueError(f"source directory does not exist: {source_directory}")

    primary_language = _read_infrastructure_value(project_db_path, 'primary_language')

    prefs_db_path = get_preferences_db_path(project_root)
    user_dirs, user_exts = _read_user_exclusions(prefs_db_path)
    excluded_dirs, excluded_extensions = build_exclusion_sets(user_dirs, user_exts)

    ignore_patterns = _read_watchdogignore(project_root)

    function_pattern = get_function_pattern(primary_language) if primary_language else None

    _effect_ensure_dir(get_watchdog_dir(project_root))
    reminders_path = get_reminders_path(project_root)

    if run_reconciliation:
        run_startup_reconciliation(project_root)

    observer = _effect_start_watching(
        source_dir=source_directory,
        project_root=project_root,
        project_db_path=project_db_path,
        reminders_path=reminders_path,
        function_pattern=function_pattern,
        excluded_dirs=excluded_dirs,
        excluded_extensions=excluded_extensions,
        ignore_patterns=ignore_patterns,
    )

    return WatcherHandle(
        observer=observer,
        project_root=project_root,
        source_directory=source_directory,
        reminders_path=reminders_path,
    )


def stop_watcher(handle: WatcherHandle, timeout: float = 5.0) -> bool:
    """
    Effect: Stop a watcher started with start_watcher.

    Args:
        handle: WatcherHandle returned by start_watcher
        timeout: Seconds to wait for the observer thread to join

    Returns:
        True if the observer thread stopped within the timeout
    """
    return _effect_stop_observer(handle.observer, timeout=timeout)
