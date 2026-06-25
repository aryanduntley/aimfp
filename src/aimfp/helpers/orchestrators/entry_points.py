"""
AIMFP Helper Functions - Orchestrator Entry Points

Cross-database orchestrators that coordinate multiple databases.

Helpers in this file:
- aimfp_init: Phase 1 mechanical setup (creates directories, databases, templates)
- aimfp_status: Comprehensive project state assembly
- aimfp_run: Gateway orchestrator for every AI interaction
- aimfp_end: Session termination audit data gathering

These are the only helpers with target_database='multi_db'.
All operate across project.db, user_preferences.db, and core.db.
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from ._common import (
    _open_project_connection,
    _open_directives_connection,
    get_project_db_path,
    get_user_preferences_db_path,
    get_user_directives_db_path,
    get_aimfp_project_dir,
    get_return_statements,
    database_exists,
    _get_table_names,
    row_to_dict,
    rows_to_tuple,
    Result,
    AIMFP_PROJECT_DIR,
    PROJECT_DB_NAME,
    USER_PREFERENCES_DB_NAME,
    BLUEPRINT_FILENAME,
    BACKUPS_DIR_NAME,
    VALID_STATUS_TYPES,
    # Project root cache
    set_project_root,
    get_cached_project_root,
    resolve_project_root,
    _discover_project_root,
)

from .backup import check_and_run_backup
from .migration import _check_pending_migrations
from .status import get_project_status
from ..project.metadata import reconcile_stored_source_directory


def _reconcile_stored_project_root(project_root: str) -> None:
    """
    Effect: Heal infrastructure.project_root to the LIVE resolved root.

    When the MCP server runs inside a linked git worktree, the committed
    project.db carries the main checkout's absolute path in
    infrastructure.project_root. After resolution binds the server to the
    worktree (see _discover_project_root), this rewrites the stored value so
    get_project_root()/aimfp_status report the worktree and the worktree's
    project.db is self-consistent. No-op when the values already match (the
    normal single-tree case). Non-fatal on any error — tracking still works off
    the cached live root regardless of the stored value.
    """
    try:
        db_path = get_project_db_path(project_root)
        if not database_exists(db_path):
            return
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM infrastructure WHERE type = 'project_root'"
            ).fetchone()
            stored = row[0] if row else None
            if stored != project_root:
                conn.execute(
                    "UPDATE infrastructure SET value = ? WHERE type = 'project_root'",
                    (project_root,)
                )
                conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ============================================================================
# aimfp_init
# ============================================================================

def aimfp_init(project_root: str) -> Result:
    """
    Phase 1 mechanical setup orchestrator for project initialization.

    Atomically creates directories, databases with schemas, and templates.
    No deep logic — pure mechanical operations. Cleans up on failure.

    Args:
        project_root: Absolute path to project root directory

    Returns:
        Result with data={
            success: bool,
            project_root: str,
            aimfp_dir: str,
            files_created: tuple,
            tables_created: {project_db: tuple, user_prefs_db: tuple},
            infrastructure_entries: int,
            next_phase: str
        }

    On error:
        Result with data={
            success: False,
            error: str,
            failed_step: int,
            cleanup_performed: bool
        }
    """
    aimfp_dir = get_aimfp_project_dir(project_root)
    project_db_path = get_project_db_path(project_root)
    prefs_db_path = get_user_preferences_db_path(project_root)
    blueprint_dest = os.path.join(aimfp_dir, BLUEPRINT_FILENAME)
    backups_dir = os.path.join(aimfp_dir, BACKUPS_DIR_NAME)
    step = 0

    try:
        # Step 1: Check if already initialized
        step = 1
        if database_exists(project_db_path):
            migration_data = _check_pending_migrations(project_root, AIMFP_PROJECT_DIR)
            return Result(
                success=False,
                data={
                    'success': False,
                    'error': f"Project already initialized: {project_db_path} exists",
                    'failed_step': step,
                    'cleanup_performed': False,
                    'migration': migration_data,
                },
                error="Project already initialized",
            )

        # Step 2: Create directories
        step = 2
        os.makedirs(aimfp_dir, exist_ok=True)
        os.makedirs(backups_dir, exist_ok=True)

        # Step 2.5: Initialize Git repository if not present (non-blocking)
        git_dir = os.path.join(project_root, '.git')
        git_status = 'git_unavailable'
        if os.path.isdir(git_dir):
            git_status = 'pre_existing'
        else:
            try:
                subprocess.run(
                    ['git', 'init'],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                git_status = 'created'
            except (FileNotFoundError, subprocess.CalledProcessError,
                    subprocess.TimeoutExpired, OSError):
                git_status = 'git_unavailable'

        # Step 3: Copy ProjectBlueprint_template.md
        step = 3
        template_path = _get_template_path()
        if not os.path.isfile(template_path):
            raise FileNotFoundError(
                f"ProjectBlueprint template not found: {template_path}"
            )
        shutil.copy2(template_path, blueprint_dest)

        # Step 4: Initialize project.db
        step = 4
        project_schema_path = _get_schema_path("project.sql")
        infra_sql_path = _get_initialization_path("standard_infrastructure.sql")

        conn = sqlite3.connect(project_db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Load and execute project schema
            with open(project_schema_path, 'r') as f:
                schema_sql = f.read()
            conn.executescript(schema_sql)

            # Load and execute standard infrastructure entries
            with open(infra_sql_path, 'r') as f:
                infra_sql = f.read()
            conn.executescript(infra_sql)

            # Populate project_root value in infrastructure
            conn.execute(
                "UPDATE infrastructure SET value = ? WHERE type = 'project_root'",
                (project_root,)
            )

            # Add init evolution note
            conn.execute(
                "INSERT INTO notes (content, note_type, source, directive_name, severity) "
                "VALUES (?, 'evolution', 'directive', 'aimfp_init', 'info')",
                ("ProjectBlueprint.md created at init. Needs to be populated with project "
                 "blueprint data by AI after discussing details of the project with user.",)
            )
            conn.commit()

            project_tables = _get_table_names(conn)
        finally:
            conn.close()

        # Step 5: Initialize user_preferences.db
        step = 5
        prefs_schema_path = _get_schema_path("user_preferences.sql")

        conn = sqlite3.connect(prefs_db_path)
        conn.row_factory = sqlite3.Row
        try:
            with open(prefs_schema_path, 'r') as f:
                schema_sql = f.read()
            conn.executescript(schema_sql)

            # Insert default tracking_settings (all disabled)
            conn.executescript(
                """
                INSERT OR IGNORE INTO tracking_settings
                    (feature_name, enabled, description, estimated_token_overhead)
                VALUES
                    ('fp_flow_tracking', 0,
                     'Track FP directive consultations',
                     '~2% per directive check'),
                    ('ai_interaction_log', 0,
                     'Log user corrections and feedback',
                     '~3% per interaction'),
                    ('helper_function_logging', 0,
                     'Log directive execution performance',
                     '~5% per file write'),
                    ('issue_reports', 0,
                     'Log errors and roadblocks',
                     '~1% when errors occur'),
                    ('compliance_checking', 0,
                     'Track FP compliance patterns',
                     '~5% per compliance check');
                """
            )

            # Load and execute standard user settings (backup defaults, etc.)
            user_settings_sql_path = _get_initialization_path("standard_user_settings.sql")
            with open(user_settings_sql_path, 'r') as f:
                user_settings_sql = f.read()
            conn.executescript(user_settings_sql)

            conn.commit()

            prefs_tables = _get_table_names(conn)
        finally:
            conn.close()

        # Step 6: Create .gitkeep files
        step = 6
        gitkeep_path = os.path.join(backups_dir, ".gitkeep")
        if not os.path.exists(gitkeep_path):
            with open(gitkeep_path, 'w') as f:
                pass

        # Step 6.5: Create .watchdogignore template at project root (user-editable,
        # gitignore-style patterns for files/dirs the watchdog should skip).
        from ...watchdog.config import (
            get_watchdogignore_path,
            DEFAULT_WATCHDOGIGNORE_CONTENT,
        )
        watchdogignore_path = get_watchdogignore_path(project_root)
        if not os.path.exists(watchdogignore_path):
            with open(watchdogignore_path, 'w') as f:
                f.write(DEFAULT_WATCHDOGIGNORE_CONTENT)

        # Step 7: Verify created files exist
        step = 7
        for expected_path in (project_db_path, prefs_db_path, blueprint_dest, backups_dir):
            if not os.path.exists(expected_path):
                raise RuntimeError(f"Post-init check failed: expected file not found: {expected_path}")

        # Step 8: Cache project root for helper functions (only if not already set)
        try:
            get_cached_project_root()
            # Already set — don't overwrite (prevents session hijacking)
        except RuntimeError:
            set_project_root(project_root)

        # Step 9: Return success
        files_created = (
            f'{AIMFP_PROJECT_DIR}/',
            f'{AIMFP_PROJECT_DIR}/{BACKUPS_DIR_NAME}/',
            f'{AIMFP_PROJECT_DIR}/{PROJECT_DB_NAME}',
            f'{AIMFP_PROJECT_DIR}/{USER_PREFERENCES_DB_NAME}',
            f'{AIMFP_PROJECT_DIR}/{BLUEPRINT_FILENAME}',
            '.watchdogignore',
        )

        # Auto-bundle init supportive context for discovery phase
        supportive_context_init = _get_supportive_context_safe('init')

        return Result(
            success=True,
            data={
                'success': True,
                'project_root': project_root,
                'aimfp_dir': aimfp_dir,
                'files_created': files_created,
                'tables_created': {
                    'project_db': project_tables,
                    'user_prefs_db': prefs_tables,
                },
                'infrastructure_entries': 8,
                'git_status': git_status,
                'next_phase': 'AI populates infrastructure and blueprint',
                'supportive_context_init': supportive_context_init,
            },
            return_statements=get_return_statements("aimfp_init"),
        )

    except Exception as e:
        # Cleanup: if we got past step 2, remove the directory
        cleanup_performed = False
        if step > 2 and os.path.isdir(aimfp_dir):
            try:
                shutil.rmtree(aimfp_dir)
                cleanup_performed = True
            except OSError:
                pass
        elif step == 2 and os.path.isdir(aimfp_dir):
            try:
                shutil.rmtree(aimfp_dir)
                cleanup_performed = True
            except OSError:
                pass

        return Result(
            success=False,
            data={
                'success': False,
                'error': str(e),
                'failed_step': step,
                'cleanup_performed': cleanup_performed,
            },
            error=str(e),
        )


def _get_template_path() -> str:
    """Pure: Get path to ProjectBlueprint template."""
    helpers_dir = Path(__file__).parent.parent  # src/aimfp/helpers/
    return str(helpers_dir.parent / "templates" / "ProjectBlueprint_template.md")


def _get_schema_path(schema_file: str) -> str:
    """Pure: Get path to a database schema file."""
    helpers_dir = Path(__file__).parent.parent  # src/aimfp/helpers/
    return str(helpers_dir.parent / "database" / "schemas" / schema_file)


def _get_initialization_path(init_file: str) -> str:
    """Pure: Get path to a database initialization file."""
    helpers_dir = Path(__file__).parent.parent  # src/aimfp/helpers/
    return str(helpers_dir.parent / "database" / "initialization" / init_file)


# ============================================================================
# aimfp_status
# ============================================================================

def aimfp_status(
    type: str = "summary",
) -> Result:
    """
    Status orchestrator that retrieves comprehensive project state.

    Gathers data from multiple tables and databases for AI to determine
    next steps. Coordinates project.db, user_preferences.db, and
    optionally user_directives.db.

    Args:
        type: 'quick', 'summary' (default), or 'detailed'

    Returns:
        Result with data={
            project_metadata: dict,
            infrastructure: tuple,
            work_hierarchy: dict (from get_project_status),
            user_directives_status: str or None,
            recent_notes: tuple,
            git_state: tuple,
            modules_summary: tuple (id, name, path, file_count per module;
                purpose omitted — fetch via get_module_by_name/path)
            modules_guidance: str (one-time note on retrieving module detail)
        }

    If not initialized:
        Result with data={initialized: False}
    """
    if type not in VALID_STATUS_TYPES:
        return Result(
            success=False,
            error=f"Invalid type '{type}'. Valid: {sorted(VALID_STATUS_TYPES)}",
        )

    try:
        project_root = resolve_project_root()
    except RuntimeError:
        return Result(
            success=True,
            data={
                'initialized': False,
                'supportive_context': _get_supportive_context_safe(),
            },
            return_statements=get_return_statements("aimfp_status"),
        )

    aimfp_dir = get_aimfp_project_dir(project_root)
    if not os.path.isdir(aimfp_dir):
        return Result(
            success=True,
            data={
                'initialized': False,
                'supportive_context': _get_supportive_context_safe(),
            },
            return_statements=get_return_statements("aimfp_status"),
        )

    try:
        # Work hierarchy (includes counts + tree)
        status_result = get_project_status(type)
        work_hierarchy = status_result.data if status_result.success else {}

        # Project metadata + infrastructure from project.db
        project_metadata = {}
        infrastructure = ()
        user_directives_status = None
        recent_notes = ()
        git_state = ()
        modules_summary = ()

        project_db_path = get_project_db_path(project_root)
        if database_exists(project_db_path):
            conn = _open_project_connection(project_root)
            try:
                # Project metadata
                cursor = conn.execute("SELECT * FROM project LIMIT 1")
                row = cursor.fetchone()
                if row:
                    project_metadata = row_to_dict(row)

                # Infrastructure
                cursor = conn.execute("SELECT * FROM infrastructure ORDER BY id")
                infrastructure = rows_to_tuple(cursor.fetchall())

                # User directives status
                user_directives_status = project_metadata.get(
                    'user_directives_status'
                )

                # Recent notes (last 10, metadata only, 7-day window)
                # Exclude noise types: deletion audit trails, already-handled notes
                cursor = conn.execute(
                    "SELECT id, note_type, reference_table, reference_id, "
                    "source, directive_name, severity, created_at "
                    "FROM notes "
                    "WHERE created_at >= datetime('now', '-7 days') "
                    "AND note_type NOT IN ('entry_deletion', 'completed', 'obsolete') "
                    "ORDER BY created_at DESC LIMIT 10"
                )
                recent_notes = rows_to_tuple(cursor.fetchall())

                # Git state
                cursor = conn.execute(
                    "SELECT * FROM work_branches ORDER BY id DESC"
                )
                git_state = rows_to_tuple(cursor.fetchall())

                # Modules summary (map only: id, name, path, file count).
                # Purpose is intentionally omitted to keep session state lean —
                # see modules_guidance below for on-demand retrieval.
                cursor = conn.execute(
                    "SELECT m.id, m.name, m.path, "
                    "COUNT(mf.file_id) AS file_count "
                    "FROM modules m "
                    "LEFT JOIN module_files mf ON mf.module_id = m.id "
                    "GROUP BY m.id ORDER BY m.name"
                )
                modules_summary = rows_to_tuple(cursor.fetchall())

            finally:
                conn.close()

        # If Use Case 2 + active/in_progress: query user_directives.db for counts
        user_directives_data = None
        if user_directives_status in ('in_progress', 'active'):
            directives_db_path = get_user_directives_db_path(project_root)
            if database_exists(directives_db_path):
                try:
                    conn = _open_directives_connection(project_root)
                    try:
                        cursor = conn.execute(
                            "SELECT COUNT(*) as cnt FROM user_directives "
                            "WHERE is_active = 1"
                        )
                        row = cursor.fetchone()
                        user_directives_data = {
                            'active_count': row['cnt'] if row else 0,
                        }
                    finally:
                        conn.close()
                except Exception:
                    user_directives_data = {'error': 'Could not access user_directives.db'}

        # Case 2 routing: determine next action based on status
        case_2_routing = None
        if user_directives_status is not None:
            case_2_routing = {
                'is_case_2': True,
                'status': user_directives_status,
                'phase': _get_case_2_phase(user_directives_status),
                'next_action': _get_case_2_next_action(user_directives_status),
            }

        # Supportive context (detailed FP examples, DRY, state DB, etc.)
        supportive_context = _get_supportive_context_safe()

        data = {
            'initialized': True,
            'project_metadata': project_metadata,
            'infrastructure': infrastructure,
            'work_hierarchy': work_hierarchy,
            'user_directives_status': user_directives_status,
            'user_directives_data': user_directives_data,
            'case_2_routing': case_2_routing,
            'recent_notes': recent_notes,
            'notes_guidance': (
                'Review note metadata (note_type, severity, reference_table, directive_name, date) '
                'to assess relevance to current work. Query full content with '
                'get_notes_comprehensive(note_id=X) for any note that may be useful.'
            ),
            'git_state': git_state,
            'modules_summary': modules_summary,
            'modules_guidance': (
                'modules_summary is a map only (id, name, path, file_count). '
                'A module\'s full purpose, files, functions, types, and '
                'dependencies are available on demand — query the db with '
                'get_module_by_name(name) / get_module_by_path(path) when you '
                'need detail for a specific module.'
            ),
            'supportive_context': supportive_context,
        }

        return Result(
            success=True,
            data=data,
            return_statements=get_return_statements("aimfp_status"),
        )

    except Exception as e:
        return Result(success=False, error=f"Status failed: {str(e)}")


# ============================================================================
# aimfp_run
# ============================================================================

def aimfp_run(is_new_session: bool = False) -> Result:
    """
    Main entry point orchestrator. Called on every AI interaction.

    When is_new_session=True, bundles comprehensive startup data including
    status (with infrastructure, supportive context, modules summary),
    user settings, guidance, watchdog, and deferred notes.

    FP directive index and all directive names are NOT bundled — available
    on demand via get_fp_directive_index() and search_directives().
    Modules summary is included within status (not duplicated at top level).

    When is_new_session=False, returns lightweight watchdog reminders only.

    Args:
        is_new_session: True for first interaction / new session / after breaks

    Returns:
        If is_new_session=True:
            Result with data={
                status: dict (from aimfp_status, includes infrastructure,
                    supportive_context, modules_summary, recent_notes),
                user_settings: dict,
                guidance: dict,
                watchdog: dict,
                case_2_context: dict or None,
                backup: dict,
                migration: dict,
                deferred_notes: tuple
            }

        If is_new_session=False:
            Result with data={
                guidance: dict,
                common_starting_points: tuple (includes get_supportive_context() reference)
            }
    """
    try:
        if not is_new_session:
            # Checkpoint call — only returns data when watchdog has reminders
            try:
                project_root = get_cached_project_root()
            except RuntimeError:
                return Result(
                    success=True,
                    data={},
                    return_statements=get_return_statements("aimfp_run"),
                )

            watchdog_data = _read_reminders(project_root)
            reminders = watchdog_data.get('reminders', ())

            if not reminders:
                return Result(
                    success=True,
                    data={},
                    return_statements=get_return_statements("aimfp_run"),
                )

            return Result(
                success=True,
                data={
                    'watchdog': watchdog_data,
                    'notice': (
                        'Watchdog reminders found. Review and handle actionable items, '
                        'then call clear_watchdog() to acknowledge and clear them.'
                    ),
                },
                return_statements=get_return_statements("aimfp_run"),
            )

        # Full session bundle — need project_root from core or environment
        # aimfp_run discovers project_root by scanning for .aimfp-project/
        project_root = _discover_project_root()

        if project_root is None:
            return Result(
                success=True,
                data={
                    'initialized': False,
                    'guidance': _get_guidance(),
                    'supportive_context': _get_supportive_context_safe(),
                    'message': 'No AIMFP project found. Run project_init to initialize.',
                },
                return_statements=get_return_statements("aimfp_run"),
            )

        # Cache project root for helper functions
        set_project_root(project_root)

        # Worktree self-heal: when running inside a linked git worktree, the
        # committed project.db still carries the MAIN checkout's paths in
        # infrastructure. Reconcile source_directory FIRST (it uses the still-
        # stored, possibly main-anchored project_root to relativize correctly),
        # then project_root, so get_project_root()/get_source_directory()/
        # aimfp_status and the watchdog all track the worktree and the worktree's
        # project.db is self-consistent. No-op for normal single-tree projects.
        reconcile_stored_source_directory(project_root)
        _reconcile_stored_project_root(project_root)

        # Watchdog: start subprocess first (skip reconciliation — we run it here),
        # then run reconciliation synchronously to eliminate race condition,
        # then read reminders (now includes reconciliation results).
        watchdog_start = _start_watchdog(project_root)
        _run_reconciliation_sync(project_root)
        watchdog_read = _read_reminders(project_root)
        watchdog_data = _reconcile_watchdog_status(watchdog_start, watchdog_read)

        # Bundle: status
        status_result = aimfp_status(type="summary")
        status_data = status_result.data if status_result.success else {}

        # Bundle: user settings
        user_settings = _get_user_settings_safe(project_root)

        # Note: fp_directive_index and all_directive_names removed from bundle —
        # available on demand via get_fp_directive_index() and search_directives()
        # Note: infrastructure already included via aimfp_status() — not duplicated here
        # Note: supportive_context is included via aimfp_status() — not called separately

        # Bundle: Case 2 context (if this is a Case 2 project)
        case_2_context = None
        user_directives_status = status_data.get('user_directives_status')
        if user_directives_status is not None:
            case_2_context = {
                'is_case_2': True,
                'status': user_directives_status,
                'phase': _get_case_2_phase(user_directives_status),
                'next_action': _get_case_2_next_action(user_directives_status),
                'pipeline': 'parse → validate → implement → approve → activate',
                'note': 'Implementation phase uses standard Case 1 development (file tracking, tasks, milestones)',
                'user_directive_names': (
                    'user_directive_parse', 'user_directive_validate',
                    'user_directive_implement', 'user_directive_approve',
                    'user_directive_activate', 'user_directive_monitor',
                    'user_directive_update', 'user_directive_deactivate',
                    'user_directive_status'
                ),
                'routing': status_data.get('case_2_routing'),
            }

        # Note: modules_summary already included via aimfp_status() — not duplicated here

        # Supportive context variants: auto-bundle based on project state
        # Core variant is already in status via aimfp_status().
        # Coding variant: always include for initialized projects (coding is imminent).
        # Case 2 variant: include when Case 2 is active.
        supportive_context_coding = _get_supportive_context_safe('coding')

        supportive_context_case2 = ''
        if user_directives_status is not None:
            supportive_context_case2 = _get_supportive_context_safe('case2')

        # Automated backup check: trigger if project inactive beyond threshold
        backup_data = check_and_run_backup()

        # Migration check: detect pending schema migrations
        migration_data = _check_pending_migrations(project_root, AIMFP_PROJECT_DIR)

        # Deferred notes: surface outstanding deferred work
        deferred_notes = _get_deferred_notes_summary(project_root)

        return Result(
            success=True,
            data={
                'project_root': project_root,
                'status': status_data,
                'user_settings': user_settings,
                'guidance': _get_guidance(),
                'supportive_context_coding': supportive_context_coding,
                'supportive_context_case2': supportive_context_case2 or None,
                'watchdog': watchdog_data,
                'case_2_context': case_2_context,
                'backup': backup_data,
                'migration': migration_data,
                'deferred_notes': deferred_notes,
            },
            return_statements=get_return_statements("aimfp_run"),
        )

    except Exception as e:
        return Result(success=False, error=f"aimfp_run failed: {str(e)}")


def _reconcile_watchdog_status(
    watchdog_start: Dict[str, Any],
    watchdog_read: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Pure: Merge the watchdog start result and reminder-read result into one
    coherent status the AI can act on.

    `_start_watchdog` knows whether the subprocess launched; `_read_reminders`
    only knows whether the PID file exists yet. Right after a fresh start the
    PID file usually is not there, so `_read_reminders` reports 'not_running'
    with a "restart it" notice. Emitting that next to started:true is
    self-contradictory and makes the AI either alarm the user or loop calling
    aimfp_run to "restart" the watchdog on every new session.

    Collapses to exactly one coherent state:
        - 'ok'       : running and healthy
        - 'starting' : just launched, PID not yet registered (no restart!)
        - 'failed'   : launch failed / subprocess exited (real error surfaced)
        - otherwise  : pass the read status/notice through unchanged

    Returns the watchdog_data dict for the session bundle.
    """
    started_ok = (
        watchdog_start.get('started') is True
        and not watchdog_start.get('error')
    )
    raw_status = watchdog_read.get('status', 'unknown')

    if not started_ok:
        wd_status = 'failed'
        wd_notice = (
            watchdog_start.get('error')
            or "Watchdog did not start; file change monitoring is inactive."
        )
    elif raw_status == 'ok':
        wd_status = 'ok'
        wd_notice = None
    elif raw_status in ('not_running', 'no_reminders_file'):
        # Just launched this call but the subprocess has not registered its
        # PID yet. It is initializing, not down. Do NOT tell the AI to
        # restart it (that causes restart loops on a fresh session).
        wd_status = 'starting'
        wd_notice = (
            "Watchdog was just started and is still initializing (PID not "
            "yet registered). This is expected at the start of a new "
            "session — file monitoring will be active momentarily. No "
            "action needed; do not restart it."
        )
    else:
        wd_status = raw_status
        wd_notice = watchdog_read.get('notice')

    return {
        'started': watchdog_start.get('started', False),
        'confirmed': watchdog_start.get('confirmed', False),
        'start_error': watchdog_start.get('error'),
        'status': wd_status,
        'reminders': watchdog_read.get('reminders', ()),
        'notice': wd_notice,
    }


def _start_watchdog(project_root: str) -> Dict[str, Any]:
    """
    Effect: Start watchdog subprocess for the project.

    Kills any existing watchdog process, then starts a new watchdog
    subprocess. Does NOT clear reminders — that is handled by
    _read_and_clear_reminders after reading, ensuring persistence.

    Returns:
        dict with {started: bool, confirmed: bool, error: str or None}
        - started:   the subprocess was launched (Popen succeeded)
        - confirmed: the subprocess wrote its PID file (genuinely running)
                     within the wait window
        - error:     populated only on a real failure (launch failed, or the
                     subprocess exited during initialization)
    """
    import signal
    import subprocess
    import sys
    import time

    from ...watchdog.config import get_watchdog_dir, get_pid_path

    watchdog_dir = get_watchdog_dir(project_root)
    pid_path = get_pid_path(project_root)

    # Kill existing watchdog if running
    if os.path.isfile(pid_path):
        try:
            with open(pid_path, 'r') as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, signal.SIGTERM)
        except (ValueError, OSError, ProcessLookupError):
            pass
        try:
            os.remove(pid_path)
        except OSError:
            pass

    # Start new watchdog subprocess (inherits parent lifecycle)
    # Note: reminders are NOT cleared here — _read_and_clear_reminders handles that
    # after reading, so previous-session findings persist until consumed.
    try:
        os.makedirs(watchdog_dir, exist_ok=True)
        proc = subprocess.Popen(
            [sys.executable, '-m', 'aimfp.watchdog', project_root, '--skip-reconciliation'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {
            'started': False,
            'confirmed': False,
            'error': f"Watchdog failed to start: {str(e)}. "
                     "Verify the watchdog module is installed (aimfp.watchdog package).",
        }

    # Popen returns immediately, but the subprocess still has to boot the
    # interpreter, import its modules, and read project.db/user_preferences.db
    # before it writes its PID file. Callers that check the PID right away
    # would see a false 'not_running'. Wait (bounded) for the PID to appear,
    # and also catch an early exit (e.g. source_directory not configured) so
    # we report a real failure instead of a contradictory
    # "started but not running".
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if os.path.isfile(pid_path):
            return {'started': True, 'confirmed': True, 'error': None}
        exit_code = proc.poll()
        if exit_code is not None:
            return {
                'started': False,
                'confirmed': False,
                'error': (
                    f"Watchdog subprocess exited during initialization "
                    f"(exit code {exit_code}) before it began monitoring. "
                    "Common causes: source_directory not set in the project's "
                    "infrastructure table, or the aimfp.watchdog module is not "
                    "installed."
                ),
            }
        time.sleep(0.05)

    # Still alive but slow to register its PID (heavily loaded machine). It is
    # starting, not failed — callers must not instruct the AI to restart it.
    return {'started': True, 'confirmed': False, 'error': None}


def _run_reconciliation_sync(project_root: str) -> None:
    """
    Effect: Run watchdog reconciliation synchronously.

    Called from aimfp_run before reading reminders so that startup
    reconciliation results are immediately available — eliminates
    the race condition where the async subprocess writes reminders
    after aimfp_run has already read the file.
    """
    try:
        from ...watchdog.reconciliation import run_startup_reconciliation
        run_startup_reconciliation(project_root)
    except Exception:
        pass  # Non-critical — subprocess fallback will catch issues


def _read_reminders(project_root: str) -> Dict[str, Any]:
    """
    Effect: Read watchdog reminders WITHOUT clearing them.

    Reminders persist until explicitly cleared via clear_watchdog().
    This ensures findings survive across sessions even if the AI
    doesn't process them immediately.

    Always reads reminders.json if it exists, regardless of PID file
    status. The PID check only affects the status field — reconciliation
    may have written results before the subprocess creates its PID file.

    Returns:
        dict with {
            status: 'ok' | 'not_running' | 'no_reminders_file',
            reminders: tuple of reminder dicts,
            notice: str or None
        }
    """
    from ...watchdog.config import get_reminders_path, get_pid_path
    from ...watchdog.reminders import _effect_read_reminders

    pid_path = get_pid_path(project_root)
    reminders_path = get_reminders_path(project_root)

    is_running = os.path.isfile(pid_path)

    # Always read reminders if the file exists — reconciliation writes
    # results synchronously before the subprocess creates its PID file,
    # so gating on PID would drop those results.
    if not os.path.isfile(reminders_path):
        status = 'not_running' if not is_running else 'no_reminders_file'
        notice = (
            "Watchdog process is not running. File change monitoring is inactive. "
            "Call aimfp_run(is_new_session=true) to restart, or verify the "
            "watchdog module is installed."
        ) if not is_running else (
            "Watchdog PID file exists but reminders file is missing. "
            "Watchdog may have failed to initialize. Check "
            ".aimfp-project/watchdog/ directory."
        )
        return {
            'status': status,
            'reminders': (),
            'notice': notice,
        }

    reminders = _effect_read_reminders(reminders_path)

    if not is_running:
        return {
            'status': 'not_running',
            'reminders': reminders,
            'notice': "Watchdog process is not running. File change monitoring is inactive. "
                      "Call aimfp_run(is_new_session=true) to restart, or verify the "
                      "watchdog module is installed.",
        }

    return {
        'status': 'ok',
        'reminders': reminders,
        'notice': None,
    }



def _get_case_2_phase(status: str) -> str:
    """Pure: Get human-readable phase name for Case 2 status."""
    phases = {
        'pending_discovery': 'Discovery (Case 2 selected, defining project shape)',
        'pending_parse': 'Onboarding (waiting for directive files)',
        'in_progress': 'Implementation (building automation code)',
        'active': 'Execution (directives running)',
        'disabled': 'Paused (directives stopped)',
    }
    return phases.get(status, f'Unknown ({status})')


def _get_case_2_next_action(status: str) -> str:
    """Pure: Get recommended next action for Case 2 status."""
    actions = {
        'pending_discovery': 'Complete project discovery with automation context',
        'pending_parse': 'Discuss directive files with user - where are they or help create them',
        'in_progress': 'Continue building automation code or complete pending tasks',
        'active': 'Monitor execution, check health, handle any updates',
        'disabled': 'Offer to reactivate directives or discuss modifications',
    }
    return actions.get(status, 'Check status and determine next step')


def _get_guidance() -> Dict[str, Any]:
    """Pure: Return static guidance for AI behavior."""
    return {
        'directive_access': (
            "Directive names cached from is_new_session bundle. "
            "Call get_directive_by_name(name) for specific details."
        ),
        'when_to_use': (
            "Use AIMFP directives when coding or when project "
            "management action/reaction is needed."
        ),
        'assumption': (
            "Always assume AIMFP applies unless user explicitly rejects it."
        ),
        'session_refresh': (
            "Call aimfp_run(is_new_session=true) again if context feels stale "
            "or after extended work."
        ),
    }


def _get_user_settings_safe(project_root: str) -> Dict[str, Any]:
    """Effect: Get user settings, returning empty dict on failure."""
    try:
        from ..user_preferences.management import get_user_settings
        result = get_user_settings()
        if result.success:
            return result.settings if hasattr(result, 'settings') and result.settings else {}
        return {}
    except Exception:
        return {}


def _get_fp_directive_index_safe() -> Dict[str, Any]:
    """Effect: Get FP directive index, returning empty dict on failure."""
    try:
        from ..core.directives_1 import get_fp_directive_index
        result = get_fp_directive_index()
        if result.success:
            return result.index if hasattr(result, 'index') and result.index else {}
        return {}
    except Exception:
        return {}


def _get_all_directive_names_safe() -> Tuple[str, ...]:
    """Effect: Get all directive names, returning empty tuple on failure."""
    try:
        from ..core.directives_1 import get_all_directive_names
        result = get_all_directive_names()
        if result.success:
            return result.names if hasattr(result, 'names') and result.names else ()
        return ()
    except Exception:
        return ()


def _get_supportive_context_safe(variant: str = 'core') -> str:
    """Effect: Get supportive context content, returning empty string on failure."""
    try:
        from ..shared.supportive_context import get_supportive_context
        result = get_supportive_context(variant=variant)
        if result.success and result.data:
            return result.data.get('content', '')
        return ''
    except Exception:
        return ''


def _get_deferred_notes_summary(project_root: str) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get metadata for all deferred notes (excludes content for token savings)."""
    try:
        conn = _open_project_connection(project_root)
        try:
            cursor = conn.execute(
                "SELECT id, note_type, reference_table, reference_id, source, severity, "
                "directive_name, send_with_directive, created_at, updated_at "
                "FROM notes WHERE note_type = 'deferred' "
                "ORDER BY created_at DESC"
            )
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = {}
                for key in row.keys():
                    val = row[key]
                    if val is not None and val != '' and val != 0:
                        d[key] = val
                result.append(d)
            return tuple(result)
        finally:
            conn.close()
    except Exception:
        return ()


def _get_infrastructure_safe(project_root: str) -> Tuple[Dict[str, Any], ...]:
    """Effect: Get infrastructure data, returning empty tuple on failure."""
    try:
        from ..project.metadata import get_all_infrastructure
        result = get_all_infrastructure()
        if result.success:
            return result.infrastructure if hasattr(result, 'infrastructure') and result.infrastructure else ()
        return ()
    except Exception:
        return ()


# ============================================================================
# aimfp_end
# ============================================================================

def aimfp_end() -> Result:
    """
    Session termination orchestrator.

    Stops watchdog (if running) and delegates to get_project_status for
    project state. AI uses status data + conversation context to perform
    session audit, compliance checks, and summary generation.

    Returns:
        Result with data={
            success: bool,
            watchdog: {stopped: bool|None, final_reminders: list},
            project_state: dict (from get_project_status)
        }
    """
    try:
        project_root = resolve_project_root()
    except RuntimeError:
        return Result(
            success=False,
            data={'initialized': False},
            error="Project root not established. Call aimfp_init or aimfp_run first.",
        )

    aimfp_dir = get_aimfp_project_dir(project_root)
    if not os.path.isdir(aimfp_dir):
        return Result(
            success=False,
            data={'initialized': False},
            error="No .aimfp-project/ directory found",
        )

    # Step 1: Watchdog — stop process and read reminders
    watchdog_data = _stop_watchdog(aimfp_dir)

    # Step 2: Project state via existing status helper
    status_result = get_project_status("summary")
    project_state = status_result.data if status_result.success else {}

    return Result(
        success=True,
        data={
            'success': True,
            'watchdog': watchdog_data,
            'project_state': project_state,
        },
        return_statements=get_return_statements("aimfp_end"),
    )


def _stop_watchdog(aimfp_dir: str) -> Dict[str, Any]:
    """
    Effect: Stop watchdog process if running, read final reminders.

    Checks for PID file at .aimfp-project/watchdog/watchdog.pid.
    If found and process alive, kills it and reads reminders.json.
    If not found, returns stopped=None (watchdog not running).

    Args:
        aimfp_dir: Path to .aimfp-project/ directory

    Returns:
        dict with {stopped: bool|None, final_reminders: tuple}
    """
    import signal

    from ...watchdog.reminders import _effect_read_reminders, _effect_clear_reminders

    watchdog_dir = os.path.join(aimfp_dir, "watchdog")
    pid_file = os.path.join(watchdog_dir, "watchdog.pid")
    reminders_file = os.path.join(watchdog_dir, "reminders.json")

    if not os.path.isfile(pid_file):
        return {'stopped': None, 'final_reminders': ()}

    # Read reminders before killing process
    final_reminders = _effect_read_reminders(reminders_file)

    # Attempt to stop the watchdog process
    stopped = False
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        stopped = True
    except (ValueError, OSError, ProcessLookupError):
        stopped = True

    # Clean up PID file and reminders
    try:
        os.remove(pid_file)
    except OSError:
        pass
    _effect_clear_reminders(reminders_file)

    return {'stopped': stopped, 'final_reminders': final_reminders}


def clear_watchdog() -> Result:
    """
    Clear watchdog reminders after they have been reviewed and handled.

    Reminders persist in .aimfp-project/watchdog/reminders.json until
    this function is called. Call after reviewing and addressing all
    watchdog findings (file deletions, unregistered files, etc.).

    Returns:
        Result with data={cleared: bool, count: int}
    """
    from ...watchdog.config import get_reminders_path
    from ...watchdog.reminders import _effect_read_reminders, _effect_clear_reminders

    try:
        project_root = get_cached_project_root()
    except RuntimeError:
        return Result(
            success=False,
            data={'cleared': False, 'count': 0},
            error="Project root not established. Call aimfp_run first.",
        )

    reminders_path = get_reminders_path(project_root)

    if not os.path.isfile(reminders_path):
        return Result(
            success=True,
            data={'cleared': True, 'count': 0},
        )

    existing = _effect_read_reminders(reminders_path)
    count = len(existing)
    _effect_clear_reminders(reminders_path)

    return Result(
        success=True,
        data={'cleared': True, 'count': count},
        return_statements=get_return_statements("clear_watchdog"),
    )
