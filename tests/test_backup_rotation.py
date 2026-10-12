"""Backup rotation names what it deletes (CIAMstack feedback: rotated_count alone hid a deleted archive)."""
import os

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.orchestrators.backup import create_project_backup, rotation_victims, rotation_warning
from aimfp.helpers.orchestrators.entry_points import aimfp_init


@pytest.fixture
def root(tmp_path):
    clear_project_root_cache()
    assert aimfp_init(str(tmp_path), init_git=False).success
    yield str(tmp_path)
    clear_project_root_cache()


def test_rotation_victims_keeps_the_newest_archives_only():
    names = ("aimfp-backup-2026-09-01.zip", "aimfp-backup-2026-10-01.zip", "aimfp-backup-2026-08-01.zip",
             "project_pre_migration_v1.15_20261012.db", ".gitkeep")
    assert rotation_victims(names, 2) == ("aimfp-backup-2026-08-01.zip",)
    assert rotation_victims(names, 3) == ()
    assert rotation_victims(names, 0) == tuple(sorted(n for n in names if n.endswith(".zip"))[::-1])


def test_backup_result_names_rotated_archives_and_warns(root):
    backups = os.path.join(root, ".aimfp-project", "backups")
    os.makedirs(backups, exist_ok=True)
    for day in ("2026-01-01", "2026-02-01", "2026-03-01"):
        open(os.path.join(backups, f"aimfp-backup-{day}.zip"), "wb").close()
    result = create_project_backup(project_root=root)
    assert result.success, result.error
    assert result.data["rotated"] == ["aimfp-backup-2026-01-01.zip"] and result.data["rotated_count"] == 1
    assert result.data["warning"] == rotation_warning(("aimfp-backup-2026-01-01.zip",), 3)
    assert not os.path.exists(os.path.join(backups, "aimfp-backup-2026-01-01.zip"))


def test_backup_without_rotation_has_no_warning(root):
    result = create_project_backup(project_root=root)
    assert result.success and result.data["rotated"] == [] and "warning" not in result.data


def test_rotation_statement_only_when_archives_were_deleted(root):
    quiet = create_project_backup(project_root=root)
    assert not any("Rotation deleted" in s for s in quiet.return_statements)
    assert not any(s.lstrip().startswith("[when:") for s in quiet.return_statements)
