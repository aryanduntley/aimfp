"""
Embedding hooks contract tests (svamanas: docs/svamanas/aimfp-embedding-hooks.md).

Covers:
- Hook A  — project_root kwarg threading: two projects, one process, cache never set
- Hook A2 — root-relative path resolution when cwd != project root
- Hook B  — aimfp_init(init_git=False) subprocess opt-out
- Hook C  — in-process watcher (start_watcher/stop_watcher, no PID file)
- Hook D  — composable sub-steps (build_status_bundle, check_pending_migrations,
            check_backup_due)

All hooks are additive: every test here runs with the session cache unset and
the cwd pointed away from the project, the exact conditions MCP never hits.
"""
import json
import os
import sqlite3
import tempfile
import time

import pytest

from aimfp.database.connection import clear_project_root_cache
import aimfp.database.connection as connection
from aimfp.helpers.project.files_1 import reserve_file, finalize_file
from aimfp.helpers.project.themes_flows_1 import add_theme
from aimfp.helpers.orchestrators.entry_points import aimfp_init, build_status_bundle
from aimfp.helpers.orchestrators.migration import check_pending_migrations
from aimfp.helpers.orchestrators.backup import check_backup_due

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)


def _make_bare_project(tag: str) -> str:
    """A temp project with .aimfp-project/project.db on the current schema."""
    root = tempfile.mkdtemp(prefix=f"aimfp_hook_{tag}_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()
    return root


@pytest.fixture(autouse=True)
def _isolated_process_state(tmp_path, monkeypatch):
    """Unset cache + cwd outside any project, restored afterwards."""
    clear_project_root_cache()
    monkeypatch.chdir(tmp_path)
    yield
    clear_project_root_cache()


def _track_one_file(root: str, stem: str) -> None:
    r = reserve_file(name=stem, path=f"src/{stem}.py", language="python",
                     project_root=root)
    assert r.success, r.error
    final_rel = f"src/{stem}_id_{r.id}.py"
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, final_rel), "w") as f:
        f.write("# t\n")
    fr = finalize_file(file_id=r.id, name=f"{stem}_id_{r.id}.py", path=final_rel,
                       language="python", project_root=root)
    assert fr.success, fr.error


class TestHookA_MultiTenant:
    def test_two_projects_one_process_cache_never_set(self):
        roots = {tag: _make_bare_project(tag) for tag in ("alpha", "beta")}

        for tag, root in roots.items():
            _track_one_file(root, tag)
            t = add_theme(name=f"theme-{tag}", description="d", project_root=root)
            assert t.success, t.error

        assert connection._cached_project_root is None

        for tag, root in roots.items():
            conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
            files = [r[0] for r in conn.execute("SELECT name FROM files")]
            themes = [r[0] for r in conn.execute("SELECT name FROM themes")]
            conn.close()
            assert files == [f"{tag}_id_1.py"]
            assert themes == [f"theme-{tag}"]


class TestHookA2_RootRelativePaths:
    def test_finalize_resolves_relative_path_against_root(self):
        # cwd (tmp_path) has no such file; only <root>/src/... exists.
        # Pre-hook this failed with "File does not exist".
        root = _make_bare_project("a2")
        _track_one_file(root, "resolved")

    def test_finalize_missing_file_still_errors(self):
        root = _make_bare_project("a2miss")
        r = reserve_file(name="ghost", path="src/ghost.py", language="python",
                         project_root=root)
        fr = finalize_file(file_id=r.id, name=f"ghost_id_{r.id}.py",
                          path=f"src/ghost_id_{r.id}.py", language="python",
                          project_root=root)
        assert not fr.success
        assert "does not exist" in fr.error


class TestHookB_InitGitFlag:
    def test_init_git_false_skips_repo(self):
        root = tempfile.mkdtemp(prefix="aimfp_hook_nogit_")
        r = aimfp_init(root, init_git=False)
        assert r.success, r.error
        assert r.data["git_status"] == "skipped"
        assert not os.path.isdir(os.path.join(root, ".git"))

    def test_default_still_inits_repo(self):
        root = tempfile.mkdtemp(prefix="aimfp_hook_git_")
        r = aimfp_init(root)
        assert r.success, r.error
        assert r.data["git_status"] in ("created", "git_unavailable")
        if r.data["git_status"] == "created":
            assert os.path.isdir(os.path.join(root, ".git"))


class TestHookC_InProcessWatcher:
    def test_start_detect_stop_no_pidfile(self):
        from aimfp.watchdog import start_watcher, stop_watcher

        root = _make_bare_project("wd")
        os.makedirs(os.path.join(root, "src"))
        db = os.path.join(root, ".aimfp-project", "project.db")
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO infrastructure (type, value) "
                     "VALUES ('source_directory', 'src')")
        conn.execute("INSERT INTO infrastructure (type, value) "
                     "VALUES ('primary_language', 'python')")
        conn.commit()
        conn.close()

        handle = start_watcher(root)
        try:
            assert handle.source_directory == os.path.join(root, "src")
            pid_path = os.path.join(root, ".aimfp-project", "watchdog", "watchdog.pid")
            assert not os.path.exists(pid_path)

            with open(os.path.join(root, "src", "rogue.py"), "w") as f:
                f.write("def rogue():\n    return 1\n")

            deadline = time.time() + 10
            reminders = []
            while time.time() < deadline and not reminders:
                if os.path.exists(handle.reminders_path):
                    with open(handle.reminders_path) as f:
                        data = json.load(f)
                    reminders = data.get("reminders", [])
                time.sleep(0.2)
        finally:
            assert stop_watcher(handle)

        assert not handle.observer.is_alive()
        assert reminders, "no reminder produced for untracked file"

    def test_start_watcher_rejects_uninitialized_root(self):
        from aimfp.watchdog import start_watcher

        with pytest.raises(ValueError, match="project.db not found"):
            start_watcher(tempfile.mkdtemp(prefix="aimfp_hook_empty_"))


class TestHookD_ComposableSubSteps:
    def test_status_bundle_migrations_backup(self):
        root = tempfile.mkdtemp(prefix="aimfp_hook_d_")
        assert aimfp_init(root, init_git=False).success
        clear_project_root_cache()

        b = build_status_bundle(root)
        assert b.success, b.error
        assert b.data["project_root"] == root
        assert b.data["status"].get("initialized") is True
        assert b.data["case_2_context"] is None
        assert "user_settings" in b.data
        assert "deferred_notes" in b.data
        # MCP-oriented extras stay out of the embedding bundle
        assert "guidance" not in b.data
        assert "watchdog" not in b.data

        m = check_pending_migrations(root)
        assert m.success, m.error
        assert m.data["checked"] is True
        assert m.data["pending"] == []

        bk = check_backup_due(root)
        assert bk.success, bk.error
        assert bk.data["due"] is False
        assert bk.data["backup_duration_days"] > 0

    def test_status_bundle_refuses_cross_root(self):
        root_a = tempfile.mkdtemp(prefix="aimfp_hook_da_")
        root_b = tempfile.mkdtemp(prefix="aimfp_hook_db_")
        assert aimfp_init(root_a, init_git=False).success  # binds cache to root_a
        assert aimfp_init(root_b, init_git=False).success  # cache stays root_a

        r = build_status_bundle(root_b)
        assert not r.success
        assert "bound to project root" in r.error


class TestHookA_TimestampThreading:
    """Regression: finalize/update of functions and types must thread the
    resolved root into the update_file_timestamp sub-helper. Pre-fix, these
    passed their own DB writes but died at the timestamp step with
    'Project root not established' whenever the cache was unset (or, worse,
    stamped a different project's DB when the cache was bound elsewhere)."""

    def test_function_lifecycle_cache_never_set(self):
        from aimfp.helpers.project.functions_1 import (
            finalize_function, finalize_functions, reserve_functions,
        )
        from aimfp.helpers.project.functions_2 import update_function

        root = _make_bare_project("tsfn")
        r = reserve_file(name="mod", path="src/mod.py", language="python",
                         skip_id_naming=True, project_root=root)
        assert r.success, r.error
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        with open(os.path.join(root, "src", "mod.py"), "w") as f:
            f.write("# t\n")
        fr = finalize_file(file_id=r.id, name="mod.py", path="src/mod.py",
                           language="python", skip_id_naming=True,
                           project_root=root)
        assert fr.success, fr.error

        rf = reserve_functions(
            [{"name": "fn_a", "file_id": r.id, "purpose": "p",
              "parameters": "none", "returns": "int", "skip_id_naming": True},
             {"name": "fn_b", "file_id": r.id, "purpose": "p",
              "parameters": "none", "returns": "int", "skip_id_naming": True}],
            project_root=root)
        assert rf.success, rf.error

        ff = finalize_functions(
            [{"function_id": rf.ids[0], "name": "fn_a", "file_id": r.id,
              "skip_id_naming": True}],
            project_root=root)
        assert ff.success, ff.error

        f1 = finalize_function(function_id=rf.ids[1], name="fn_b",
                               file_id=r.id, skip_id_naming=True,
                               project_root=root)
        assert f1.success, f1.error

        uf = update_function(function_id=rf.ids[0], purpose="p2",
                             project_root=root)
        assert uf.success, uf.error
        assert connection._cached_project_root is None

    def test_type_lifecycle_cache_never_set(self):
        from aimfp.helpers.project.types_1 import (
            finalize_type, reserve_type, update_type,
        )

        root = _make_bare_project("tsty")
        r = reserve_file(name="shapes", path="src/shapes.py", language="python",
                         skip_id_naming=True, project_root=root)
        assert r.success, r.error
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        with open(os.path.join(root, "src", "shapes.py"), "w") as f:
            f.write("# t\n")
        fr = finalize_file(file_id=r.id, name="shapes.py", path="src/shapes.py",
                           language="python", skip_id_naming=True,
                           project_root=root)
        assert fr.success, fr.error

        rt = reserve_type(name="Shape", definition_json={"kind": "record"},
                          description="d", file_id=r.id, skip_id_naming=True,
                          project_root=root)
        assert rt.success, rt.error

        ft = finalize_type(type_id=rt.id, name="Shape",
                           definition_json={"kind": "record"}, description="d",
                           file_id=r.id, skip_id_naming=True,
                           project_root=root)
        assert ft.success, ft.error

        ut = update_type(type_id=rt.id, description="d2", project_root=root)
        assert ut.success, ut.error
        assert connection._cached_project_root is None
