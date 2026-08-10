"""
AIMFP Helper Functions - Backup Inspection & Restore

The read side of the backup system. `backup.py` writes zips; this module lists
them, reads their contents without extracting, and restores selected databases
back into `.aimfp-project/`.

**Restore is side-by-side, never in-place.** Each archived member is extracted to
a temporary file first and verified before anything live is touched. Only then is
the current file displaced — moved into `backups/` under a `replaced-<timestamp>-`
name — and the restored copy moved into position. A failed extraction therefore
cannot leave a half-written database where a working one used to be, and the file
being replaced is always recoverable afterward.

That displacement matters more than it looks: restoring is the one operation that
destroys current state on purpose, and it is usually run when something has already
gone wrong. Overwriting the live database would remove the only copy of whatever
the user was trying to rescue.

Selection is explicit. `databases` names which members to restore, so recovering a
corrupted `project.db` does not silently roll back user preferences that were fine.
"""

import os
import shutil
import zipfile
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ._common import (
    get_aimfp_project_dir,
    resolve_project_root,
    BACKUPS_DIR_NAME,
    Result,
)
from ..utils import get_return_statements


# Restorable members, keyed by the short name callers pass in `databases`.
RESTORE_TARGETS: Dict[str, str] = {
    'project': 'project.db',
    'user_preferences': 'user_preferences.db',
    'user_directives': 'user_directives.db',
    'blueprint': 'ProjectBlueprint.md',
}

BACKUP_PREFIX = 'aimfp-backup-'
REPLACED_PREFIX = 'replaced-'


# ============================================================================
# Immutable Records
# ============================================================================

@dataclass(frozen=True)
class BackupListResult:
    """Result of listing available backups."""
    success: bool
    backups: Tuple[Dict[str, Any], ...] = ()
    backups_dir: Optional[str] = None
    count: int = 0
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreResult:
    """Result of a restore operation."""
    success: bool
    backup_name: Optional[str] = None
    restored: Tuple[str, ...] = ()
    displaced: Tuple[Dict[str, str], ...] = ()
    skipped: Tuple[Dict[str, str], ...] = ()
    error: Optional[str] = None
    return_statements: Tuple[str, ...] = ()


# ============================================================================
# Pure Helpers
# ============================================================================

def resolve_requested_targets(databases: Optional[Any]) -> Tuple[Tuple[str, ...], Optional[str]]:
    """
    Pure: Normalize the `databases` argument into concrete member names.

    Accepts a list, a comma-separated string, 'all', or None (which means all) so
    the tool behaves the same whether it is called over MCP or directly.

    Args:
        databases: Requested targets, or None for every restorable member

    Returns:
        (tuple of RESTORE_TARGETS keys, error message or None)
    """
    if databases is None:
        return (tuple(RESTORE_TARGETS), None)

    if isinstance(databases, str):
        requested = [databases] if ',' not in databases else databases.split(',')
    elif isinstance(databases, (list, tuple)):
        requested = list(databases)
    else:
        return ((), f"databases must be a list or string, got {type(databases).__name__}")

    cleaned = [str(item).strip() for item in requested if str(item).strip()]
    if not cleaned:
        return ((), "databases was empty — omit it entirely to restore everything")

    if any(item.lower() == 'all' for item in cleaned):
        return (tuple(RESTORE_TARGETS), None)

    unknown = [item for item in cleaned if item not in RESTORE_TARGETS]
    if unknown:
        return ((), f"Unknown target(s) {unknown}. Valid: {sorted(RESTORE_TARGETS)} or 'all'")

    return (tuple(dict.fromkeys(cleaned)), None)


def displaced_filename(filename: str, stamp: str) -> str:
    """
    Pure: Build the name a displaced live file is archived under.

    Args:
        filename: Original file name (e.g. 'project.db')
        stamp: UTC timestamp string

    Returns:
        Name of the form 'replaced-<stamp>-<filename>'
    """
    return f"{REPLACED_PREFIX}{stamp}-{filename}"


def utc_stamp() -> str:
    """
    Effect: Current UTC timestamp formatted for filenames.

    Returns:
        Timestamp string of the form YYYY-MM-DD-HHMMSS
    """
    return datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')


# ============================================================================
# Effect Functions
# ============================================================================

def _effect_backups_dir(project_root: str) -> str:
    """
    Effect: Resolve the backups directory for a project.

    Args:
        project_root: Project root directory

    Returns:
        Absolute path to .aimfp-project/backups/
    """
    return os.path.join(get_aimfp_project_dir(project_root), BACKUPS_DIR_NAME)


def _effect_list_backup_files(backups_dir: str) -> Tuple[str, ...]:
    """
    Effect: List backup archive names, newest first.

    Args:
        backups_dir: Directory holding backup zips

    Returns:
        Tuple of file names sorted newest-first (timestamped names sort correctly)
    """
    if not os.path.isdir(backups_dir):
        return ()
    names = [
        name for name in os.listdir(backups_dir)
        if name.startswith(BACKUP_PREFIX) and name.endswith('.zip')
    ]
    return tuple(sorted(names, reverse=True))


def _effect_read_archive_members(archive_path: str) -> Tuple[Tuple[str, ...], Optional[str]]:
    """
    Effect: List the member names inside a backup archive without extracting.

    Args:
        archive_path: Path to the backup zip

    Returns:
        (member names, error message or None)
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            return (tuple(archive.namelist()), None)
    except (zipfile.BadZipFile, OSError) as exc:
        return ((), str(exc))


def _effect_extract_member(archive_path: str, member: str, destination: str) -> None:
    """
    Effect: Extract one archive member to an exact destination path.

    Args:
        archive_path: Path to the backup zip
        member: Member name inside the archive
        destination: Full path to write the extracted bytes to
    """
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(member) as source, open(destination, 'wb') as target:
            shutil.copyfileobj(source, target)


# ============================================================================
# Public Tools
# ============================================================================

def list_project_backups(project_root: Optional[str] = None) -> BackupListResult:
    """
    List available backup archives, newest first.

    Read-only. Call before restore_project_backup to see what exists and which
    members each archive holds — a backup only contains databases that existed
    when it was taken, so an older archive may not carry every target.

    Args:
        project_root: Project root override (defaults to the discovered root)

    Returns:
        BackupListResult with one entry per archive: name, size, modified time,
        and the restorable targets it contains
    """
    try:
        project_root = project_root or resolve_project_root()
        backups_dir = _effect_backups_dir(project_root)
        names = _effect_list_backup_files(backups_dir)

        entries: List[Dict[str, Any]] = []
        for name in names:
            path = os.path.join(backups_dir, name)
            members, error = _effect_read_archive_members(path)
            available = sorted(
                key for key, filename in RESTORE_TARGETS.items() if filename in members
            )
            stat = os.stat(path)
            entries.append({
                'name': name,
                'size_bytes': stat.st_size,
                'modified': datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).strftime('%Y-%m-%d %H:%M:%S'),
                'restorable': available,
                'member_count': len(members),
                'unreadable': error,
            })

        return BackupListResult(
            success=True,
            backups=tuple(entries),
            backups_dir=backups_dir,
            count=len(entries),
            return_statements=get_return_statements("list_project_backups"),
        )

    except Exception as exc:
        return BackupListResult(success=False, error=f"Could not list backups: {str(exc)}")


def restore_project_backup(
    backup_name: Optional[str] = None,
    databases: Optional[Any] = None,
    project_root: Optional[str] = None,
) -> RestoreResult:
    """
    Restore selected databases from a backup archive, side-by-side.

    Extracts each requested member to a temporary file FIRST. Only once every
    extraction has succeeded is anything live touched: the current file is moved
    into backups/ as 'replaced-<timestamp>-<filename>', then the restored copy is
    moved into place. A failed extraction aborts before any live file is disturbed.

    Nothing is deleted. The displaced files stay in backups/ and are the way back
    if the restore turns out to be the wrong call.

    Args:
        backup_name: Archive to restore from. Defaults to the most recent.
        databases: Which members to restore — any of 'project', 'user_preferences',
            'user_directives', 'blueprint', or 'all'. Accepts a list or a
            comma-separated string. Defaults to everything the archive contains.
        project_root: Project root override (defaults to the discovered root)

    Returns:
        RestoreResult listing what was restored, what was displaced and where it
        went, and what was skipped with the reason

    Example:
        >>> restore_project_backup(databases=['project'])        # doctest: +SKIP
    """
    targets, error = resolve_requested_targets(databases)
    if error:
        return RestoreResult(success=False, error=error)

    try:
        project_root = project_root or resolve_project_root()
        aimfp_dir = get_aimfp_project_dir(project_root)
        backups_dir = _effect_backups_dir(project_root)

        available = _effect_list_backup_files(backups_dir)
        if not available:
            return RestoreResult(
                success=False,
                error=f"No backups found in {backups_dir}. Nothing to restore.",
            )

        chosen = backup_name or available[0]
        if chosen not in available:
            return RestoreResult(
                success=False,
                error=f"Backup '{chosen}' not found. Available: {list(available)}",
            )

        archive_path = os.path.join(backups_dir, chosen)
        members, read_error = _effect_read_archive_members(archive_path)
        if read_error:
            return RestoreResult(
                success=False,
                backup_name=chosen,
                error=f"Could not read archive '{chosen}': {read_error}",
            )

        present, skipped = [], []
        for key in targets:
            filename = RESTORE_TARGETS[key]
            if filename in members:
                present.append((key, filename))
            else:
                skipped.append({'target': key, 'reason': f"'{filename}' is not in this backup"})

        if not present:
            return RestoreResult(
                success=False,
                backup_name=chosen,
                skipped=tuple(skipped),
                error=(
                    f"Backup '{chosen}' contains none of the requested targets. "
                    f"Use list_project_backups to see what each archive holds."
                ),
            )

        stamp = utc_stamp()

        # Phase 1: extract everything to temporaries. Nothing live is touched yet,
        # so a failure here leaves the project exactly as it was.
        staged: List[Tuple[str, str, str]] = []
        try:
            for key, filename in present:
                temp_path = os.path.join(aimfp_dir, f".restore-{stamp}-{filename}")
                _effect_extract_member(archive_path, filename, temp_path)
                staged.append((key, filename, temp_path))
        except Exception as exc:
            for _, _, temp_path in staged:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            return RestoreResult(
                success=False,
                backup_name=chosen,
                error=f"Extraction failed, nothing was changed: {str(exc)}",
            )

        # Phase 2: displace the live files, then move the staged copies in.
        os.makedirs(backups_dir, exist_ok=True)
        restored, displaced = [], []
        for key, filename, temp_path in staged:
            live_path = os.path.join(aimfp_dir, filename)

            if os.path.exists(live_path):
                archived_name = displaced_filename(filename, stamp)
                archived_path = os.path.join(backups_dir, archived_name)
                shutil.move(live_path, archived_path)
                displaced.append({
                    'target': key,
                    'original': filename,
                    'moved_to': os.path.join(BACKUPS_DIR_NAME, archived_name),
                })

            shutil.move(temp_path, live_path)
            restored.append(key)

        return RestoreResult(
            success=True,
            backup_name=chosen,
            restored=tuple(restored),
            displaced=tuple(displaced),
            skipped=tuple(skipped),
            return_statements=get_return_statements("restore_project_backup"),
        )

    except Exception as exc:
        return RestoreResult(success=False, error=f"Restore failed: {str(exc)}")
