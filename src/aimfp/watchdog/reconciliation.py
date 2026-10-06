"""
AIMFP Watchdog - Startup Reconciliation

Shared functions for reading watchdog configuration and running
startup reconciliation scans. Used by both aimfp_run (synchronous)
and the watchdog subprocess (fallback when started manually).
"""

import json
import os

from ..database.connection import _effect_query_one, _open_connection, get_project_dir_name
from ..helpers.project.structure_health import collect_structure_health
from ..wrappers.file_ops import _effect_read_file
from .config import (
    get_project_db_path,
    get_preferences_db_path,
    get_reminders_path,
    get_watchdogignore_path,
    build_exclusion_sets,
    parse_watchdogignore,
    project_folder_ignore_patterns,
)
from .analyzers import (
    _effect_get_all_finalized_file_paths,
    _effect_get_all_known_file_paths,
    reconcile_deleted_files,
    reconcile_unregistered_files,
)
from .reminders import (
    _effect_append_reminders,
    _effect_replace_structure_reminders,
    build_structure_reminders,
)


# ============================================================================
# Effect Functions - Config Reading
# ============================================================================

def _read_infrastructure_value(project_db_path: str, infra_type: str) -> str:
    """Effect: Read a single value from the infrastructure table."""
    row = _effect_query_one(
        project_db_path,
        "SELECT value FROM infrastructure WHERE type = ?",
        (infra_type,),
    )
    return row['value'] if row and row.get('value') else ''


def _read_user_exclusions(prefs_db_path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """
    Effect: Read user exclusion settings from user_preferences.db.

    Returns (excluded_dirs, excluded_extensions) as tuples of strings.
    """
    user_dirs: tuple[str, ...] = ()
    user_exts: tuple[str, ...] = ()

    if not os.path.isfile(prefs_db_path):
        return (user_dirs, user_exts)

    row = _effect_query_one(
        prefs_db_path,
        "SELECT setting_value FROM user_settings WHERE setting_key = ?",
        ('watchdog_excluded_dirs',),
    )
    if row and row.get('setting_value'):
        try:
            parsed = json.loads(row['setting_value'])
            if isinstance(parsed, list):
                user_dirs = tuple(str(d) for d in parsed)
        except json.JSONDecodeError:
            pass

    row = _effect_query_one(
        prefs_db_path,
        "SELECT setting_value FROM user_settings WHERE setting_key = ?",
        ('watchdog_excluded_extensions',),
    )
    if row and row.get('setting_value'):
        try:
            parsed = json.loads(row['setting_value'])
            if isinstance(parsed, list):
                user_exts = tuple(str(e) for e in parsed)
        except json.JSONDecodeError:
            pass

    return (user_dirs, user_exts)


def _read_watchdogignore(project_root: str) -> tuple[str, ...]:
    """
    Effect: Read and parse the project's .watchdogignore file.

    The root's own project folder is appended when it is a project-dir
    override (see project_folder_ignore_patterns), so every caller of this —
    watcher, reconciliation, catalog scan — skips it. Returns just those
    patterns if the file is absent or unreadable.
    """
    own_folder = project_folder_ignore_patterns(get_project_dir_name(project_root))
    path = get_watchdogignore_path(project_root)
    if not os.path.isfile(path):
        return own_folder
    content = _effect_read_file(path)
    if content is None:
        return own_folder
    return parse_watchdogignore(content) + own_folder


# ============================================================================
# Structural Reminders
# ============================================================================

def refresh_structure_reminders(project_root: str) -> int:
    """
    Effect: Recompute modularity-layer gaps (files without flow or module,
    flows without theme, ...) and replace the structure_* reminders with them.

    Runs at session start (run_startup_reconciliation) and at every
    aimfp_run(is_new_session=False) checkpoint. Never raises: a project.db
    that cannot be read leaves the reminders as they were.

    Returns the number of structural reminders now present.
    """
    project_db_path = get_project_db_path(project_root)
    if not os.path.isfile(project_db_path):
        return 0
    try:
        conn = _open_connection(project_db_path, readonly=True)
        try:
            _summary, health = collect_structure_health(conn)
        finally:
            conn.close()
    except Exception:
        return 0
    reminders = build_structure_reminders(health)
    _effect_replace_structure_reminders(get_reminders_path(project_root), reminders)
    return len(reminders)


# ============================================================================
# Startup Reconciliation
# ============================================================================

def run_startup_reconciliation(project_root: str) -> int:
    """
    Effect: Run startup reconciliation scans and write results to reminders.json.

    Detects:
    1. Files registered in DB but missing from disk (deleted between sessions)
    2. Files on disk but not registered in DB (created outside tracking)
    3. Structural gaps in the modularity layer (refresh_structure_reminders)

    Returns the number of reminders written.
    """
    project_db_path = get_project_db_path(project_root)
    if not os.path.isfile(project_db_path):
        return 0

    structure_count = refresh_structure_reminders(project_root)

    source_directory = _read_infrastructure_value(project_db_path, 'source_directory')
    if not source_directory:
        return structure_count

    if not os.path.isabs(source_directory):
        source_directory = os.path.join(project_root, source_directory)
    if not os.path.isdir(source_directory):
        return structure_count

    prefs_db_path = get_preferences_db_path(project_root)
    user_dirs, user_exts = _read_user_exclusions(prefs_db_path)
    excluded_dirs, excluded_extensions = build_exclusion_sets(user_dirs, user_exts)
    ignore_patterns = _read_watchdogignore(project_root)

    reminders_path = get_reminders_path(project_root)
    count = 0

    # Reconciliation: deleted files
    finalized_files = _effect_get_all_finalized_file_paths(project_db_path)
    if finalized_files:
        deletion_reminders = reconcile_deleted_files(finalized_files, project_root)
        if deletion_reminders:
            _effect_append_reminders(reminders_path, deletion_reminders)
            count += len(deletion_reminders)

    # Reconciliation: unregistered files
    all_db_paths = _effect_get_all_known_file_paths(project_db_path)
    unregistered_reminders = reconcile_unregistered_files(
        source_directory, project_root, all_db_paths,
        excluded_dirs, excluded_extensions, ignore_patterns,
    )
    if unregistered_reminders:
        _effect_append_reminders(reminders_path, unregistered_reminders)
        count += len(unregistered_reminders)

    return count + structure_count
