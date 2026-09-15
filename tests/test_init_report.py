"""
aimfp_init reporting and non-destructive behavior.

aimfp_init must report only files it actually wrote (files_created) and list
anything it found in place (files_already_present), never overwrite a surviving
ProjectBlueprint.md, and on failure remove only what it created.
"""
import os

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.orchestrators import entry_points
from aimfp.helpers.orchestrators.entry_points import (
    aimfp_init,
    partition_init_artifacts,
    cleanup_targets_for_failed_init,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_project_root_cache()
    yield
    clear_project_root_cache()


def _aimfp(root, *parts):
    return os.path.join(str(root), ".aimfp-project", *parts)


class TestPure:
    def test_partition_keeps_report_order(self):
        artifacts = (("a/", "/p/a"), ("b", "/p/b"), ("c", "/p/c"))
        assert partition_init_artifacts(artifacts, frozenset({"/p/b"})) == (("a/", "c"), ("b",))

    def test_cleanup_removes_whole_dir_when_init_created_it(self):
        assert cleanup_targets_for_failed_init("/p/.a", ("/p/.a/x",), frozenset()) == ("/p/.a",)

    def test_cleanup_spares_pre_existing_inner_paths(self):
        targets = cleanup_targets_for_failed_init(
            "/p/.a", ("/p/.a/backups", "/p/.a/project.db"), frozenset({"/p/.a", "/p/.a/backups"})
        )
        assert targets == ("/p/.a/project.db",)


class TestReport:
    def test_fresh_init_reports_everything_created(self, tmp_path):
        r = aimfp_init(str(tmp_path), init_git=False)
        assert r.success, r.error
        assert ".watchdogignore" in r.data["files_created"]
        assert ".aimfp-project/ProjectBlueprint.md" in r.data["files_created"]
        assert r.data["files_already_present"] == ()

    def test_pre_existing_watchdogignore_reported_and_untouched(self, tmp_path):
        ignore = tmp_path / ".watchdogignore"
        ignore.write_text("custom/\n")
        r = aimfp_init(str(tmp_path), init_git=False)
        assert r.success, r.error
        assert ".watchdogignore" in r.data["files_already_present"]
        assert ".watchdogignore" not in r.data["files_created"]
        assert ignore.read_text() == "custom/\n"

    def test_pre_existing_blueprint_not_overwritten(self, tmp_path):
        os.makedirs(_aimfp(tmp_path))
        blueprint = _aimfp(tmp_path, "ProjectBlueprint.md")
        with open(blueprint, "w") as f:
            f.write("# Real blueprint\n")
        r = aimfp_init(str(tmp_path), init_git=False)
        assert r.success, r.error
        assert ".aimfp-project/ProjectBlueprint.md" in r.data["files_already_present"]
        assert ".aimfp-project/" in r.data["files_already_present"]
        assert ".aimfp-project/project.db" in r.data["files_created"]
        with open(blueprint) as f:
            assert f.read() == "# Real blueprint\n"


class TestFailureCleanup:
    @staticmethod
    def _break_prefs_schema(monkeypatch):
        real = entry_points._get_schema_path
        monkeypatch.setattr(
            entry_points, "_get_schema_path",
            lambda name: "/nonexistent/prefs.sql" if name == "user_preferences.sql" else real(name),
        )

    def test_fresh_failure_removes_created_dir(self, tmp_path, monkeypatch):
        self._break_prefs_schema(monkeypatch)
        r = aimfp_init(str(tmp_path), init_git=False)
        assert not r.success
        assert r.data["cleanup_performed"] is True
        assert not os.path.exists(_aimfp(tmp_path))

    def test_failure_keeps_pre_existing_backups(self, tmp_path, monkeypatch):
        os.makedirs(_aimfp(tmp_path, "backups"))
        backup = _aimfp(tmp_path, "backups", "aimfp-backup-2026-01-01.zip")
        with open(backup, "wb") as f:
            f.write(b"zip")
        self._break_prefs_schema(monkeypatch)
        r = aimfp_init(str(tmp_path), init_git=False)
        assert not r.success
        assert r.data["cleanup_performed"] is True
        assert os.path.exists(backup)
        # project.db was created before the failure and must not linger, or the
        # next init would report "already initialized"
        assert not os.path.exists(_aimfp(tmp_path, "project.db"))
        assert not os.path.exists(_aimfp(tmp_path, "ProjectBlueprint.md"))
