"""
AIMFP Watchdog - CLI Entry Point

Usage: python -m aimfp.watchdog <project_root>

Starts the file system watcher as a subprocess. Reads configuration
from project.db and user_preferences.db, then monitors the source
directory for changes.
"""

import os
import signal
import sys
import time

from ..wrappers.file_ops import _effect_write_text, _effect_ensure_dir
from .config import (
    get_watchdog_dir,
    get_reminders_path,
    get_pid_path,
    get_project_db_path,
    get_preferences_db_path,
    build_exclusion_sets,
    get_function_pattern,
)
from .watcher import _effect_start_watching
from .reconciliation import (
    _read_infrastructure_value,
    _read_user_exclusions,
    _read_watchdogignore,
    run_startup_reconciliation,
)
from ..wrappers.filesystem_observer import _effect_stop_observer


def main() -> None:
    """Effect: Main entry point for the watchdog subprocess."""
    if len(sys.argv) < 2:
        print("Usage: python -m aimfp.watchdog <project_root>", file=sys.stderr)
        sys.exit(1)

    project_root = sys.argv[1]
    project_db_path = get_project_db_path(project_root)

    if not os.path.isfile(project_db_path):
        print(f"Error: project.db not found at {project_db_path}", file=sys.stderr)
        sys.exit(1)

    # Read infrastructure
    source_directory = _read_infrastructure_value(project_db_path, 'source_directory')
    if not source_directory:
        print("Error: source_directory not set in infrastructure table", file=sys.stderr)
        sys.exit(1)

    # Resolve source_directory relative to project_root if not absolute
    if not os.path.isabs(source_directory):
        source_directory = os.path.join(project_root, source_directory)

    if not os.path.isdir(source_directory):
        print(f"Error: source directory does not exist: {source_directory}", file=sys.stderr)
        sys.exit(1)

    primary_language = _read_infrastructure_value(project_db_path, 'primary_language')

    # Read user exclusions
    prefs_db_path = get_preferences_db_path(project_root)
    user_dirs, user_exts = _read_user_exclusions(prefs_db_path)
    excluded_dirs, excluded_extensions = build_exclusion_sets(user_dirs, user_exts)

    # Read project-root .watchdogignore (gitignore-style path/glob patterns)
    ignore_patterns = _read_watchdogignore(project_root)

    # Function pattern for language
    function_pattern = get_function_pattern(primary_language) if primary_language else None

    # Setup watchdog directory
    watchdog_dir = get_watchdog_dir(project_root)
    _effect_ensure_dir(watchdog_dir)

    # Write PID file
    pid_path = get_pid_path(project_root)
    _effect_write_text(pid_path, str(os.getpid()))

    # Reminders file — do NOT clear on startup; reminders persist until
    # AI explicitly calls clear_watchdog() after handling them.
    reminders_path = get_reminders_path(project_root)

    # Reconciliation scan: skip if aimfp_run already ran it synchronously
    if '--skip-reconciliation' not in sys.argv:
        run_startup_reconciliation(project_root)

    # Start observer
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

    # Signal handler for graceful shutdown
    def handle_sigterm(signum, frame):
        _effect_stop_observer(observer)
        # Clean up PID file
        try:
            os.remove(pid_path)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # Run until killed
    try:
        while observer.is_alive():
            observer.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        _effect_stop_observer(observer)
        try:
            os.remove(pid_path)
        except OSError:
            pass


if __name__ == '__main__':
    main()
