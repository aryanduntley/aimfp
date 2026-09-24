"""
Hook F — project-dir override (svamanas: docs/svamanas/project-dir-override-2026-09-23.md).

One root per process may keep its project databases under a relative folder
other than .aimfp-project (svamanas: '.svamanas/brain'). Covers:
- the slot: validation, set-twice semantics, independence from the root cache
- path resolution: every per-project path, stored '.aimfp-project' defaults
- discovery pinned to the override root (no parent walk, no fallback)
- init / backup / migration / watchdog / hook logs / changeset paths
- CLI: python -m aimfp --project-dir / --no-watchdog, and the watchdog daemon
- other roots in the same process keep .aimfp-project
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

import aimfp.database.connection as connection
from aimfp.database.connection import (
    AIMFP_PROJECT_DIR,
    clear_project_dir_override,
    clear_project_root_cache,
    extract_project_dir_arg,
    get_aimfp_project_dir,
    get_project_db_path,
    get_project_dir_name,
    get_project_dir_override,
    get_user_directives_db_path,
    get_user_preferences_db_path,
    normalize_project_dir_path,
    project_dir_cli_args,
    resolve_project_relative,
    set_project_dir_override,
)
from aimfp.helpers.orchestrators import entry_points
from aimfp.helpers.orchestrators.entry_points import (
    aimfp_init,
    aimfp_run,
    set_session_watchdog_enabled,
)

BRAIN = ".svamanas/brain"
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")


@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    """Empty slot, empty cache, daemon allowed, cwd outside any project."""
    clear_project_root_cache()
    clear_project_dir_override()
    set_session_watchdog_enabled(True)
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()
    clear_project_dir_override()
    set_session_watchdog_enabled(True)


def _brain_root(tmp_path, name="home"):
    root = tmp_path / name
    root.mkdir()
    set_project_dir_override(str(root), BRAIN)
    return str(root)


def _init(root):
    r = aimfp_init(root, init_git=False)
    assert r.success, r.error
    return r


def _set_source_dir(root, rel="src"):
    os.makedirs(os.path.join(root, rel), exist_ok=True)
    conn = connection._open_connection(get_project_db_path(root))
    try:
        conn.execute("UPDATE infrastructure SET value=? WHERE type='source_directory'", (rel,))
        conn.execute("UPDATE infrastructure SET value='python' WHERE type='primary_language'")
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# The slot
# ============================================================================

class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        (".svamanas/brain", ".svamanas/brain"),
        ("./.svamanas//brain/", ".svamanas/brain"),
        (".svamanas\\brain", ".svamanas/brain"),
        (".svamanas-brain", ".svamanas-brain"),
    ])
    def test_valid(self, raw, expected):
        assert normalize_project_dir_path(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", "   ", ".", "./", "/abs/brain", "C:\\brain", "../brain",
        "a/../b", ".git", ".git/brain",
    ])
    def test_invalid(self, raw):
        with pytest.raises(ValueError):
            normalize_project_dir_path(raw)


class TestSlot:
    def test_same_root_same_path_is_noop(self, tmp_path):
        root = _brain_root(tmp_path)
        set_project_dir_override(root, "./.svamanas/brain/")
        assert get_project_dir_override().rel_path == BRAIN

    def test_other_root_raises(self, tmp_path):
        _brain_root(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(ValueError, match="already set"):
            set_project_dir_override(str(other), BRAIN)

    def test_other_path_raises(self, tmp_path):
        root = _brain_root(tmp_path)
        with pytest.raises(ValueError, match="already set"):
            set_project_dir_override(root, ".svamanas-brain")

    def test_clear_then_set(self, tmp_path):
        _brain_root(tmp_path)
        clear_project_dir_override()
        other = tmp_path / "other"
        other.mkdir()
        set_project_dir_override(str(other), BRAIN)
        assert get_project_dir_override().root == str(other)

    def test_root_cache_clear_keeps_slot(self, tmp_path):
        root = _brain_root(tmp_path)
        clear_project_root_cache()
        assert get_project_dir_name(root) == BRAIN


class TestPaths:
    def test_override_root_paths(self, tmp_path):
        root = _brain_root(tmp_path)
        brain = os.path.join(root, ".svamanas", "brain")
        assert get_aimfp_project_dir(root) == brain
        assert get_project_db_path(root) == os.path.join(brain, "project.db")
        assert get_user_preferences_db_path(root) == os.path.join(brain, "user_preferences.db")
        assert get_user_directives_db_path(root) == os.path.join(brain, "user_directives.db")

    def test_other_root_keeps_default(self, tmp_path):
        _brain_root(tmp_path)
        other = str(tmp_path / "user_project")
        assert get_aimfp_project_dir(other) == os.path.join(other, AIMFP_PROJECT_DIR)

    def test_symlinked_root_matches(self, tmp_path):
        root = _brain_root(tmp_path)
        link = tmp_path / "link"
        link.symlink_to(root)
        assert get_project_dir_name(str(link)) == BRAIN

    def test_resolve_project_relative(self, tmp_path):
        root = _brain_root(tmp_path)
        brain = os.path.join(root, ".svamanas", "brain")
        assert resolve_project_relative(root, ".aimfp-project") == brain
        assert resolve_project_relative(root, ".aimfp-project/logs/errors/") == \
            os.path.join(brain, "logs/errors/")
        assert resolve_project_relative(root, "/var/log/x") == "/var/log/x"
        assert resolve_project_relative(root, "logs") == os.path.join(root, "logs")

    def test_resolve_project_relative_default_root(self, tmp_path):
        root = str(tmp_path)
        assert resolve_project_relative(root, ".aimfp-project/logs") == \
            os.path.join(root, ".aimfp-project", "logs")


# ============================================================================
# Discovery
# ============================================================================

class TestDiscovery:
    def test_pinned_to_override_root_no_fallback(self, tmp_path, monkeypatch):
        # A root that ALSO holds a default .aimfp-project (svamanas dev checkout)
        root = tmp_path / "home"
        root.mkdir()
        _init(str(root))
        assert (root / AIMFP_PROJECT_DIR / "project.db").exists()
        monkeypatch.chdir(root)

        set_project_dir_override(str(root), BRAIN)
        assert connection._discover_project_root() is None

        _init(str(root))
        assert connection._discover_project_root() == str(root)

    def test_no_parent_walk(self, tmp_path, monkeypatch):
        outer = tmp_path / "outer"
        outer.mkdir()
        subprocess.run(["git", "init", "-q", str(outer)], check=False)
        _init(str(outer))
        inner = outer / "home"
        inner.mkdir()
        monkeypatch.chdir(inner)
        assert connection._discover_project_root() == str(outer)

        set_project_dir_override(str(inner), BRAIN)
        assert connection._discover_project_root() is None


# ============================================================================
# Consumers of the resolver
# ============================================================================

class TestInit:
    def test_init_creates_brain_only(self, tmp_path):
        root = _brain_root(tmp_path)
        r = _init(root)
        assert os.path.isfile(os.path.join(root, BRAIN, "project.db"))
        assert os.path.isfile(os.path.join(root, BRAIN, "user_preferences.db"))
        assert not os.path.exists(os.path.join(root, AIMFP_PROJECT_DIR))
        assert f"{BRAIN}/project.db" in r.data["files_created"]

    def test_user_project_in_same_process_keeps_default(self, tmp_path):
        _brain_root(tmp_path)
        user = tmp_path / "calc"
        user.mkdir()
        _init(str(user))
        assert (user / AIMFP_PROJECT_DIR / "project.db").exists()
        assert not (user / ".svamanas").exists()

    def test_rollback_removes_leaf_only(self, tmp_path):
        root = _brain_root(tmp_path)
        os.makedirs(os.path.join(root, ".svamanas", "knowledge"))
        brain = get_aimfp_project_dir(root)
        doomed = entry_points.cleanup_targets_for_failed_init(brain, (), frozenset())
        assert doomed == (brain,)
        assert os.path.join(root, ".svamanas") not in doomed


class TestBackupAndMigration:
    def test_backup_lands_in_brain(self, tmp_path):
        from aimfp.helpers.orchestrators.backup import create_project_backup

        root = _brain_root(tmp_path)
        _init(root)
        r = create_project_backup(project_root=root)
        assert r.success, r.error
        assert os.path.dirname(r.data["backup_path"]) == os.path.join(root, BRAIN, "backups")
        assert not os.path.exists(os.path.join(root, AIMFP_PROJECT_DIR))

    def test_scheduled_backup_reads_brain(self, tmp_path):
        from aimfp.helpers.orchestrators.backup import (
            check_scheduled_backup_due, create_project_backup)

        root = _brain_root(tmp_path)
        _init(root)
        assert create_project_backup(project_root=root).success
        r = check_scheduled_backup_due(project_root=root)
        assert r.success and r.data["backup_count"] == 1

    def test_migration_check_reads_brain(self, tmp_path):
        from aimfp.helpers.orchestrators.migration import check_pending_migrations

        root = _brain_root(tmp_path)
        _init(root)
        r = check_pending_migrations(project_root=root)
        assert r.success
        assert {row["db_name"] for row in r.data["up_to_date"]} >= {"project", "user_preferences"}
        assert not os.path.exists(os.path.join(root, AIMFP_PROJECT_DIR))


class TestWatchdog:
    def test_watchdog_dir_and_ignore(self, tmp_path):
        from aimfp.watchdog.config import get_watchdog_dir, should_exclude
        from aimfp.watchdog.reconciliation import _read_watchdogignore

        root = _brain_root(tmp_path)
        assert get_watchdog_dir(root) == os.path.join(root, BRAIN, "watchdog")
        patterns = _read_watchdogignore(root)
        assert BRAIN in patterns
        assert should_exclude(f"{BRAIN}/project.db", frozenset(), frozenset(), patterns)
        assert not should_exclude(".svamanas/knowledge/x.py", frozenset(), frozenset(), patterns)

    def test_default_root_adds_no_pattern(self, tmp_path):
        from aimfp.watchdog.reconciliation import _read_watchdogignore
        assert _read_watchdogignore(str(tmp_path)) == ()

    def test_in_process_watcher_writes_into_brain(self, tmp_path):
        from aimfp.watchdog import start_watcher, stop_watcher

        root = _brain_root(tmp_path)
        _init(root)
        _set_source_dir(root)
        handle = start_watcher(root)
        try:
            assert handle.reminders_path == os.path.join(root, BRAIN, "watchdog", "reminders.json")
        finally:
            assert stop_watcher(handle)
        assert not os.path.exists(os.path.join(root, AIMFP_PROJECT_DIR))


class TestHookLogsAndChangesets:
    def test_log_paths(self, tmp_path):
        from aimfp.hooks.config import resolve_log_path
        from aimfp.hooks.results import LogConfig

        root = _brain_root(tmp_path)
        assert resolve_log_path(root, LogConfig().error_log_dir) == \
            os.path.join(root, BRAIN, "logs/errors/")

    def test_changeset_paths(self, tmp_path):
        from aimfp.helpers.changeset._common import _changeset_dir, project_db_rel_path

        root = _brain_root(tmp_path)
        assert project_db_rel_path(root) == f"{BRAIN}/project.db"
        assert _changeset_dir(root) == os.path.join(root, BRAIN, "changesets")
        assert project_db_rel_path(str(tmp_path)) == ".aimfp-project/project.db"


# ============================================================================
# CLI flags
# ============================================================================

class TestCliArgs:
    def test_extract(self):
        assert extract_project_dir_arg(()) is None
        assert extract_project_dir_arg(("--project-dir", BRAIN)) == BRAIN
        assert extract_project_dir_arg((f"--project-dir={BRAIN}",)) == BRAIN
        for bad in (("--project-dir",), ("--project-dir=",), ("--project-dir", "--no-watchdog")):
            with pytest.raises(ValueError):
                extract_project_dir_arg(bad)

    def test_child_args(self, tmp_path):
        root = _brain_root(tmp_path)
        assert project_dir_cli_args(root) == ("--project-dir", BRAIN)
        assert project_dir_cli_args(str(tmp_path)) == ()

    def test_apply_server_flags(self, tmp_path, monkeypatch):
        from aimfp.__main__ import _apply_server_flags

        monkeypatch.chdir(tmp_path)
        _apply_server_flags(("--project-dir", BRAIN, "--no-watchdog"))
        assert get_project_dir_override().root == str(tmp_path)
        assert not entry_points.session_watchdog_enabled()

    def test_no_flags_change_nothing(self, tmp_path):
        from aimfp.__main__ import _apply_server_flags

        _apply_server_flags(())
        assert get_project_dir_override() is None
        assert entry_points.session_watchdog_enabled()

    def test_bad_flag_exits(self, tmp_path):
        env = {**os.environ, "PYTHONPATH": os.path.abspath(SRC_DIR)}
        proc = subprocess.run(
            [sys.executable, "-m", "aimfp", "--project-dir", "../escape"],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 2
        assert ".." in proc.stderr


class TestNoWatchdog:
    def _home(self, tmp_path, monkeypatch):
        root = _brain_root(tmp_path)
        _init(root)
        _set_source_dir(root)
        with open(os.path.join(root, "src", "rogue.py"), "w") as f:
            f.write("def rogue():\n    return 1\n")
        monkeypatch.chdir(root)
        set_session_watchdog_enabled(False)
        return root

    def test_session_reports_disabled_with_reminders(self, tmp_path, monkeypatch):
        root = self._home(tmp_path, monkeypatch)
        r = aimfp_run(is_new_session=True)
        assert r.success, r.error
        wd = r.data["watchdog"]
        assert wd["status"] == "disabled"
        assert wd["disabled_by"] == "--no-watchdog"
        assert wd["start_error"] is None
        assert wd["reminders"], "reconciliation reminder for rogue.py not reported"
        assert not os.path.exists(os.path.join(root, BRAIN, "watchdog", "watchdog.pid"))
        assert not os.path.exists(os.path.join(root, AIMFP_PROJECT_DIR))

    def test_checkpoint_reports_disabled(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        assert aimfp_run(is_new_session=True).success
        r = aimfp_run(is_new_session=False)
        assert r.data["watchdog"]["status"] == "disabled"

    def test_explicit_false_still_external(self, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        set_session_watchdog_enabled(True)
        r = aimfp_run(is_new_session=True, start_watchdog=False)
        assert r.data["watchdog"]["status"] == "external"


class TestWatchdogDaemonCarriesOverride:
    def test_daemon_writes_pid_into_brain(self, tmp_path):
        root = _brain_root(tmp_path)
        _init(root)
        _set_source_dir(root)
        env = {**os.environ, "PYTHONPATH": os.path.abspath(SRC_DIR)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "aimfp.watchdog", root, "--skip-reconciliation",
             *project_dir_cli_args(root)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        pid_path = os.path.join(root, BRAIN, "watchdog", "watchdog.pid")
        try:
            deadline = time.time() + 15
            while time.time() < deadline and not os.path.exists(pid_path):
                assert proc.poll() is None, proc.stderr.read().decode()
                time.sleep(0.1)
            assert os.path.exists(pid_path)
            assert not os.path.exists(os.path.join(root, AIMFP_PROJECT_DIR))
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
