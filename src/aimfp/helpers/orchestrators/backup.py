"""
AIMFP Helper Functions - Automated Backup Sub-Helper

Creates zip backups of the .aimfp-project/ directory, triggered during
aimfp_run(is_new_session=true) when project inactivity exceeds the
configured backup_duration threshold.

Sub-helper: Not exposed as MCP tool. Called only by aimfp_run orchestrator.

Functions:
- create_project_backup: Main entry point — checks threshold, creates zip, rotates old backups
"""

import os
import zipfile
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from ._common import (
    _open_project_connection,
    _open_preferences_connection,
    get_project_db_path,
    get_user_preferences_db_path,
    get_aimfp_project_dir,
    resolve_project_root,
    database_exists,
    BACKUPS_DIR_NAME,
    Result,
)


# ============================================================================
# Public Entry Point (called by aimfp_run)
# ============================================================================

def check_and_run_backup(project_root: Optional[str] = None) -> Dict[str, Any]:
    """
    Effect: Check if backup should be triggered and run it if so.

    Called by aimfp_run when is_new_session=True.

    Args:
        project_root: Explicit root for embedding hosts; defaults to the
            cached/discovered session root (the MCP behavior)

    Returns:
        dict with {
            checked: True,
            triggered: bool,
            backup_result: dict or None,
            reason: str
        }
    """
    try:
        project_root = project_root or resolve_project_root()
        settings = _get_backup_settings_safe(project_root)
        backup_duration = int(settings.get('backup_duration', '30'))

        last_activity = _get_last_activity_timestamp(project_root)

        if not _should_trigger_backup(last_activity, backup_duration):
            return {
                'checked': True,
                'triggered': False,
                'backup_result': None,
                'reason': f'Last activity within {backup_duration}-day threshold',
            }

        backup_result = _create_project_backup(project_root)
        return {
            'checked': True,
            'triggered': True,
            'backup_result': backup_result,
            'reason': f'Project inactive for {backup_duration}+ days',
        }

    except Exception as e:
        return {
            'checked': True,
            'triggered': False,
            'backup_result': None,
            'reason': f'Backup check failed: {str(e)}',
        }


def check_backup_due(project_root: Optional[str] = None) -> Result:
    """
    Effect: Check whether the inactivity backup is due WITHOUT creating one.

    Public embedding API — the read-only half of check_and_run_backup, for
    hosts that want the check à la carte and control the backup themselves.

    Args:
        project_root: Explicit root for embedding hosts; defaults to the
            cached/discovered session root

    Returns:
        Result with data={
            due: bool,
            last_activity: str or None,
            backup_duration_days: int
        }
    """
    try:
        root = project_root or resolve_project_root()
        settings = _get_backup_settings_safe(root)
        backup_duration = int(settings.get('backup_duration', '30'))
        last_activity = _get_last_activity_timestamp(root)
        return Result(
            success=True,
            data={
                'due': _should_trigger_backup(last_activity, backup_duration),
                'last_activity': last_activity,
                'backup_duration_days': backup_duration,
            },
        )
    except Exception as e:
        return Result(success=False, error=f"Backup check failed: {str(e)}")


# ============================================================================
# Private Helpers
# ============================================================================

def _get_backup_settings_safe(project_root: str) -> Dict[str, str]:
    """
    Effect: Read backup settings from user_preferences.db.

    Returns dict with keys: backup_count, backup_duration, backup_path.
    Falls back to defaults if settings not found or DB inaccessible.
    """
    defaults = {
        'backup_count': '3',
        'backup_duration': '30',
        'backup_path': '.aimfp-project',
    }
    try:
        prefs_db_path = get_user_preferences_db_path(project_root)
        if not database_exists(prefs_db_path):
            return defaults
        conn = _open_preferences_connection(project_root)
        try:
            cursor = conn.execute(
                "SELECT setting_key, setting_value FROM user_settings "
                "WHERE setting_key IN ('backup_count', 'backup_duration', 'backup_path')"
            )
            for row in cursor.fetchall():
                defaults[row['setting_key']] = row['setting_value']
        finally:
            conn.close()
    except Exception:
        pass
    return defaults


def _get_last_activity_timestamp(project_root: str) -> Optional[str]:
    """
    Effect: Query the most recent timestamp across all project.db tables.

    Returns ISO datetime string of the most recent activity, or None if
    no activity found or project.db inaccessible.
    """
    project_db_path = get_project_db_path(project_root)
    if not database_exists(project_db_path):
        return None

    try:
        conn = _open_project_connection(project_root)
        try:
            cursor = conn.execute(
                """
                SELECT MAX(ts) as last_activity FROM (
                    SELECT MAX(COALESCE(updated_at, created_at)) as ts FROM notes
                    UNION ALL
                    SELECT MAX(COALESCE(updated_at, created_at)) as ts FROM files
                    UNION ALL
                    SELECT MAX(COALESCE(updated_at, created_at)) as ts FROM functions
                    UNION ALL
                    SELECT MAX(COALESCE(updated_at, created_at)) as ts FROM tasks
                    UNION ALL
                    SELECT MAX(COALESCE(updated_at, created_at)) as ts FROM milestones
                    UNION ALL
                    SELECT MAX(COALESCE(updated_at, created_at)) as ts FROM subtasks
                    UNION ALL
                    SELECT MAX(COALESCE(updated_at, created_at)) as ts FROM sidequests
                    UNION ALL
                    SELECT MAX(updated_at) as ts FROM project
                )
                """
            )
            row = cursor.fetchone()
            return row['last_activity'] if row and row['last_activity'] else None
        finally:
            conn.close()
    except Exception:
        return None


def _should_trigger_backup(last_activity: Optional[str], backup_duration_days: int) -> bool:
    """
    Pure: Determine if backup should be triggered based on inactivity.

    Args:
        last_activity: ISO datetime string of last project activity
        backup_duration_days: Number of days of inactivity before backup triggers

    Returns:
        True if backup should be triggered (last_activity older than threshold)
    """
    if last_activity is None:
        return False

    try:
        # Parse SQLite datetime (YYYY-MM-DD HH:MM:SS format, naive/UTC)
        activity_dt = datetime.strptime(last_activity, "%Y-%m-%d %H:%M:%S")
        # Use naive UTC for comparison (SQLite CURRENT_TIMESTAMP is UTC)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        delta = now - activity_dt
        return delta.days >= backup_duration_days
    except (ValueError, TypeError):
        return False


def _create_backup_summary(project_root: str) -> str:
    """
    Effect: Generate a markdown status summary for inclusion in backup zip.

    Returns a markdown string summarizing project state at time of backup.
    """
    lines = [
        "# AIMFP Backup Summary",
        "",
        f"**Backup Date**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        f"**Project Root**: {project_root}",
        "",
    ]

    try:
        project_db_path = get_project_db_path(project_root)
        if database_exists(project_db_path):
            conn = _open_project_connection(project_root)
            try:
                # Project metadata
                cursor = conn.execute("SELECT name, purpose, status, version FROM project LIMIT 1")
                row = cursor.fetchone()
                if row:
                    lines.append("## Project")
                    lines.append(f"- **Name**: {row['name']}")
                    lines.append(f"- **Purpose**: {row['purpose']}")
                    lines.append(f"- **Status**: {row['status']}")
                    lines.append(f"- **Version**: {row['version']}")
                    lines.append("")

                # Counts
                count_queries = (
                    ("Files", "SELECT COUNT(*) as cnt FROM files WHERE is_reserved = 0"),
                    ("Functions", "SELECT COUNT(*) as cnt FROM functions WHERE is_reserved = 0"),
                    ("Tasks", "SELECT COUNT(*) as cnt FROM tasks"),
                    ("Milestones", "SELECT COUNT(*) as cnt FROM milestones"),
                    ("Notes", "SELECT COUNT(*) as cnt FROM notes"),
                    ("Completion Paths", "SELECT COUNT(*) as cnt FROM completion_path"),
                )
                lines.append("## Counts")
                for label, query in count_queries:
                    cursor = conn.execute(query)
                    row = cursor.fetchone()
                    count = row['cnt'] if row else 0
                    lines.append(f"- **{label}**: {count}")
                lines.append("")

                # Active milestone
                cursor = conn.execute(
                    "SELECT name, status FROM milestones "
                    "WHERE status = 'in_progress' LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    lines.append("## Current Focus")
                    lines.append(f"- **Active Milestone**: {row['name']}")

                    # Active task under this milestone
                    cursor = conn.execute(
                        "SELECT name, status FROM tasks "
                        "WHERE status = 'in_progress' LIMIT 1"
                    )
                    task_row = cursor.fetchone()
                    if task_row:
                        lines.append(f"- **Active Task**: {task_row['name']}")
                    lines.append("")

            finally:
                conn.close()
    except Exception:
        lines.append("*Could not read project state for summary.*")
        lines.append("")

    lines.append("---")
    lines.append("*Auto-generated by AIMFP backup system.*")
    return "\n".join(lines)


def _create_project_backup(project_root: str) -> Dict[str, Any]:
    """
    Effect: Create a zip backup of the .aimfp-project/ directory.

    Zips all contents of .aimfp-project/ except the backups/ directory itself.
    Includes an auto-generated backup_summary.md.
    Manages rotation: keeps only backup_count most recent backups.

    Returns:
        dict with {
            created: bool,
            backup_path: str or None,
            rotated_count: int,
            error: str or None
        }
    """
    try:
        settings = _get_backup_settings_safe(project_root)
        backup_count = int(settings.get('backup_count', '3'))
        backup_base = settings.get('backup_path', '.aimfp-project')

        aimfp_dir = get_aimfp_project_dir(project_root)
        backups_dir = os.path.join(project_root, backup_base, BACKUPS_DIR_NAME)

        # Ensure backups directory exists
        os.makedirs(backups_dir, exist_ok=True)

        # Generate backup filename
        now = datetime.now(timezone.utc)
        backup_name = f"aimfp-backup-{now.strftime('%Y-%m-%d')}.zip"
        backup_path = os.path.join(backups_dir, backup_name)

        # If same-day backup exists, use timestamped name
        if os.path.exists(backup_path):
            backup_name = f"aimfp-backup-{now.strftime('%Y-%m-%d-%H-%M')}.zip"
            backup_path = os.path.join(backups_dir, backup_name)

        # Generate backup summary
        summary_content = _create_backup_summary(project_root)

        # Create zip archive
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Walk .aimfp-project/ and add files, excluding backups/ and watchdog/
            for dirpath, dirnames, filenames in os.walk(aimfp_dir):
                # Filter out backups and watchdog from dirnames to prevent descending
                dirnames[:] = [
                    d for d in dirnames
                    if d != BACKUPS_DIR_NAME and d != 'watchdog'
                ]

                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    arcname = os.path.relpath(file_path, aimfp_dir)
                    zf.write(file_path, arcname)

            # Add backup summary
            zf.writestr("backup_summary.md", summary_content)

        # Rotate old backups: keep only backup_count most recent
        rotated_count = _rotate_backups(backups_dir, backup_count)

        return {
            'created': True,
            'backup_path': backup_path,
            'backup_name': backup_name,
            'rotated_count': rotated_count,
            'error': None,
        }

    except Exception as e:
        return {
            'created': False,
            'backup_path': None,
            'backup_name': None,
            'rotated_count': 0,
            'error': str(e),
        }


def _rotate_backups(backups_dir: str, max_count: int) -> int:
    """
    Effect: Remove oldest backup zip files, keeping only max_count most recent.

    Returns the number of backups deleted.
    """
    try:
        backup_files = sorted(
            (
                f for f in os.listdir(backups_dir)
                if f.startswith("aimfp-backup-") and f.endswith(".zip")
            ),
            reverse=True,  # Newest first (YYYY-MM-DD sorts correctly)
        )

        if len(backup_files) <= max_count:
            return 0

        to_delete = backup_files[max_count:]
        deleted = 0
        for filename in to_delete:
            try:
                os.remove(os.path.join(backups_dir, filename))
                deleted += 1
            except OSError:
                pass
        return deleted
    except Exception:
        return 0
