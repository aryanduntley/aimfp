"""
Stored-root rewrite reporting (task 36; svamanas item 6).

aimfp_run rewrites a stale infrastructure.project_root (and an absolute
source_directory) and now says so. aimfp_status and get_project_root stay
read-only and report the mismatch instead.
"""
import os
import shutil
import sqlite3
import subprocess

import pytest

from aimfp.database.connection import clear_project_root_cache
from aimfp.helpers.orchestrators.entry_points import aimfp_init, aimfp_run, aimfp_status
from aimfp.helpers.project.metadata import (
    get_project_root,
    is_linked_worktree,
    stored_root_mismatch,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_project_root_cache()
    yield
    clear_project_root_cache()


def _infra(root, key):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        return conn.execute("SELECT value FROM infrastructure WHERE type = ?", (key,)).fetchone()[0]
    finally:
        conn.close()


def _set_infra(root, key, value):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        conn.execute("UPDATE infrastructure SET value = ? WHERE type = ?", (value, key))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def original(tmp_path):
    root = tmp_path / "calc2"
    root.mkdir()
    assert aimfp_init(str(root), init_git=False).success
    (root / "src").mkdir()
    _set_infra(str(root), "source_directory", "src")
    return str(root)


def _open_in(monkeypatch, root):
    clear_project_root_cache()
    monkeypatch.chdir(root)


class TestCopiedProject:
    def test_status_and_get_project_root_report_without_writing(self, original, tmp_path, monkeypatch):
        copy = str(tmp_path / "calc3")
        shutil.copytree(original, copy)
        _open_in(monkeypatch, copy)

        status = aimfp_status()
        assert status.data["stored_root_mismatch"] == {"stored": original, "live": copy}
        root = get_project_root()
        assert root.data == original
        assert root.stored_root_mismatch == {"stored": original, "live": copy}
        assert _infra(copy, "project_root") == original  # read-only: nothing rewritten

    def test_run_rewrites_and_reports_copy(self, original, tmp_path, monkeypatch):
        copy = str(tmp_path / "calc3")
        shutil.copytree(original, copy)
        _open_in(monkeypatch, copy)

        r = aimfp_run(is_new_session=True, start_watchdog=False)
        assert r.success, r.error
        assert r.data["project_root_rewritten"] == {
            "from": original, "to": copy, "reason": "moved_or_copied", "original_exists": True,
        }
        assert _infra(copy, "project_root") == copy
        assert _infra(original, "project_root") == original  # the original is untouched
        assert aimfp_status().data["stored_root_mismatch"] is None
        assert get_project_root().stored_root_mismatch is None

    def test_run_reports_move(self, original, tmp_path, monkeypatch):
        moved = str(tmp_path / "moved")
        os.rename(original, moved)
        _open_in(monkeypatch, moved)

        r = aimfp_run(is_new_session=True, start_watchdog=False)
        rewritten = r.data["project_root_rewritten"]
        assert rewritten["reason"] == "moved_or_copied"
        assert rewritten["original_exists"] is False

    def test_second_run_reports_nothing(self, original, tmp_path, monkeypatch):
        copy = str(tmp_path / "calc3")
        shutil.copytree(original, copy)
        _open_in(monkeypatch, copy)
        assert aimfp_run(is_new_session=True, start_watchdog=False).data["project_root_rewritten"]
        clear_project_root_cache()
        assert aimfp_run(is_new_session=True, start_watchdog=False).data["project_root_rewritten"] is None


class TestNoFalseReports:
    def test_unmoved_project(self, original, monkeypatch):
        _open_in(monkeypatch, original)
        r = aimfp_run(is_new_session=True, start_watchdog=False)
        assert r.data["project_root_rewritten"] is None
        assert r.data["source_directory_rewritten"] is None
        assert aimfp_status().data["stored_root_mismatch"] is None

    def test_symlinked_root_is_not_a_move(self, original, tmp_path):
        link = tmp_path / "link"
        link.symlink_to(original)
        assert stored_root_mismatch(original, str(link)) is None
        assert stored_root_mismatch(None, original) is None


class TestSourceDirectory:
    def test_absolute_source_dir_is_reported(self, original, tmp_path, monkeypatch):
        copy = str(tmp_path / "calc3")
        shutil.copytree(original, copy)
        _set_infra(copy, "source_directory", os.path.join(original, "src"))
        _open_in(monkeypatch, copy)

        r = aimfp_run(is_new_session=True, start_watchdog=False)
        assert r.data["source_directory_rewritten"] == {
            "from": os.path.join(original, "src"), "to": "src",
        }
        assert _infra(copy, "source_directory") == "src"


class TestWorktreeLabel:
    def test_is_linked_worktree(self, tmp_path):
        main = tmp_path / "main"
        main.mkdir()
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git + ["init", "-q", str(main)], check=True)
        (main / "f.txt").write_text("x")
        subprocess.run(git + ["-C", str(main), "add", "."], check=True)
        subprocess.run(git + ["-C", str(main), "commit", "-qm", "init"], check=True)
        worktree = tmp_path / "wt"
        subprocess.run(git + ["-C", str(main), "worktree", "add", "-q", str(worktree)], check=True)

        assert is_linked_worktree(str(worktree)) is True
        assert is_linked_worktree(str(main)) is False
        assert is_linked_worktree(str(tmp_path)) is False
