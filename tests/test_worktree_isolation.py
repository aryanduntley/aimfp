"""
Tests for git-worktree-aware project_root resolution.

Regression coverage for the worktree-isolation bug
(docs/intercommaimfptools/WORKTREE-ISOLATION-BUG.md): an MCP server launched with
cwd inside a linked git worktree must bind project.db to the WORKTREE's own
.aimfp-project, not the shared main checkout's. Previously _discover_project_root
returned the stored infrastructure.project_root (an absolute path to main), so all
worker tracking raced main's project.db.
"""

import os
import shutil
import sqlite3
import subprocess
import tempfile

import pytest

from aimfp.database.connection import (
    _discover_project_root,
    _git_toplevel,
    _has_project_db,
    clear_project_root_cache,
    set_project_root,
    get_project_db_path,
)
from aimfp.helpers.orchestrators.entry_points import _reconcile_stored_project_root
from aimfp.helpers.project.metadata import (
    get_project_root,
    get_source_directory,
    reconcile_stored_source_directory,
    _resolve_source_dir_abs,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)


def _schema() -> str:
    with open(SCHEMA_PATH) as f:
        return f.read()


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _make_project_db(root, stored_project_root):
    """Create .aimfp-project/project.db with an infrastructure project_root row."""
    os.makedirs(os.path.join(root, ".aimfp-project"), exist_ok=True)
    db = os.path.join(root, ".aimfp-project", "project.db")
    conn = sqlite3.connect(db)
    conn.executescript(_schema())
    conn.execute(
        "INSERT INTO infrastructure (type, value, description) "
        "VALUES ('project_root', ?, 'root')",
        (stored_project_root,),
    )
    conn.commit()
    conn.close()
    return db


def _stored_root(db):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT value FROM infrastructure WHERE type='project_root'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_infra(db, infra_type, value):
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM infrastructure WHERE type = ?", (infra_type,))
        conn.execute(
            "INSERT INTO infrastructure (type, value, description) VALUES (?, ?, 'x')",
            (infra_type, value),
        )
        conn.commit()
    finally:
        conn.close()


def _stored_source_dir(db):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT value FROM infrastructure WHERE type='source_directory'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@pytest.fixture
def main_repo():
    """A committed git repo R whose project.db stores R as project_root."""
    base = tempfile.mkdtemp(prefix="aimfp_wt_")
    R = os.path.join(base, "main")
    os.makedirs(R)
    _git(R, "init", "-q")
    _git(R, "config", "user.email", "t@t")
    _git(R, "config", "user.name", "tester")
    db = _make_project_db(R, R)
    _git(R, "add", "-A")
    _git(R, "commit", "-qm", "baseline")
    clear_project_root_cache()
    yield base, R, db
    clear_project_root_cache()
    shutil.rmtree(base, ignore_errors=True)


def test_discover_in_worktree_binds_to_worktree(main_repo, monkeypatch):
    """cwd inside a linked worktree resolves to the WORKTREE, not main."""
    base, R, _ = main_repo
    W = os.path.join(base, "wt1")
    _git(R, "worktree", "add", "--detach", W, "HEAD")

    monkeypatch.chdir(W)
    clear_project_root_cache()
    assert _discover_project_root() == W


def test_worker_writes_dont_touch_main(main_repo, monkeypatch):
    """A worker bound to the worktree writes its own db; main's is untouched."""
    base, R, R_db = main_repo
    W = os.path.join(base, "wt1")
    _git(R, "worktree", "add", "--detach", W, "HEAD")
    W_db = get_project_db_path(W)

    before = os.path.getmtime(R_db)
    r_bytes = open(R_db, "rb").read()

    monkeypatch.chdir(W)
    clear_project_root_cache()
    resolved = _discover_project_root()
    set_project_root(resolved)
    conn = sqlite3.connect(W_db)
    conn.execute(
        "INSERT INTO notes (content, note_type, source, severity) "
        "VALUES ('tracking','info','ai','info')"
    )
    conn.commit()
    conn.close()

    # main's project.db is byte-for-byte unchanged
    assert open(R_db, "rb").read() == r_bytes
    assert os.path.getmtime(R_db) == before


def test_reconcile_heals_stored_root_in_worktree(main_repo, monkeypatch):
    """get_project_root() returns the worktree after self-heal."""
    base, R, _ = main_repo
    W = os.path.join(base, "wt1")
    _git(R, "worktree", "add", "--detach", W, "HEAD")
    W_db = get_project_db_path(W)

    # committed worktree db still carries main's path
    assert _stored_root(W_db) == R

    monkeypatch.chdir(W)
    clear_project_root_cache()
    resolved = _discover_project_root()
    set_project_root(resolved)
    _reconcile_stored_project_root(resolved)

    assert _stored_root(W_db) == W
    assert get_project_root().data == W


def test_reconcile_is_noop_when_matching(main_repo, monkeypatch):
    """In the normal single-tree case nothing is rewritten."""
    base, R, R_db = main_repo
    before = os.path.getmtime(R_db)
    r_bytes = open(R_db, "rb").read()

    monkeypatch.chdir(R)
    clear_project_root_cache()
    resolved = _discover_project_root()
    assert resolved == R
    set_project_root(resolved)
    _reconcile_stored_project_root(resolved)

    assert open(R_db, "rb").read() == r_bytes
    assert os.path.getmtime(R_db) == before


def test_plain_git_repo_resolves_to_root(main_repo, monkeypatch):
    """Non-worktree git repo resolves to its own top-level."""
    base, R, _ = main_repo
    monkeypatch.chdir(R)
    clear_project_root_cache()
    assert _discover_project_root() == R


def test_non_git_dir_falls_back_to_cwd(monkeypatch):
    """A non-git directory with .aimfp-project still resolves via cwd."""
    root = tempfile.mkdtemp(prefix="aimfp_nogit_")
    try:
        _make_project_db(root, root)
        monkeypatch.chdir(root)
        clear_project_root_cache()
        assert _git_toplevel(root) is None  # not a git repo
        assert _discover_project_root() == root
    finally:
        clear_project_root_cache()
        shutil.rmtree(root, ignore_errors=True)


def test_uninitialized_dir_returns_none(monkeypatch):
    """No .aimfp-project anywhere -> None (project not initialized)."""
    root = tempfile.mkdtemp(prefix="aimfp_empty_")
    try:
        monkeypatch.chdir(root)
        clear_project_root_cache()
        assert not _has_project_db(root)
        assert _discover_project_root() is None
    finally:
        clear_project_root_cache()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# source_directory worktree-awareness (RUN-3-AIMFP-FOLLOWUPS.md Issue 1)
# ---------------------------------------------------------------------------

def test_resolve_source_dir_abs_cases():
    """Pure resolver: relative joins; absolute-under-root kept; absolute-elsewhere re-anchored."""
    W = "/home/u/.worktrees/w1"
    R = "/home/u/main"
    # relative -> joined onto the live root
    assert _resolve_source_dir_abs("src", W, R) == "/home/u/.worktrees/w1/src"
    assert _resolve_source_dir_abs("src/app", W, R) == "/home/u/.worktrees/w1/src/app"
    # absolute already under the live root -> unchanged
    assert _resolve_source_dir_abs(W + "/src", W, R) == W + "/src"
    # absolute anchored to the MAIN checkout -> re-anchored onto the worktree
    assert _resolve_source_dir_abs(R + "/src", W, R) == W + "/src"
    # nested source dir under main, stored_root known -> structure preserved
    assert _resolve_source_dir_abs(R + "/pkg/src", W, R) == W + "/pkg/src"
    # absolute elsewhere with no usable stored_root -> basename fallback
    assert _resolve_source_dir_abs("/somewhere/else/src", W, None) == W + "/src"


def test_get_source_directory_in_worktree_returns_worktree(main_repo, monkeypatch):
    """get_source_directory() returns <worktree>/src, not the main checkout's src."""
    base, R, R_db = main_repo
    # main stored an ABSOLUTE source_directory at init (the bug's starting state)
    _set_infra(R_db, "source_directory", os.path.join(R, "src"))
    _git(R, "add", "-A"); _git(R, "commit", "-qm", "set src")

    W = os.path.join(base, "wt1")
    _git(R, "worktree", "add", "--detach", W, "HEAD")

    monkeypatch.chdir(W)
    clear_project_root_cache()
    resolved = _discover_project_root()
    set_project_root(resolved)

    res = get_source_directory()
    assert res.success, res.error
    assert res.data == os.path.join(W, "src")        # worktree, NOT main
    assert res.data != os.path.join(R, "src")


def test_reconcile_heals_absolute_source_dir_to_relative(main_repo, monkeypatch):
    """The aimfp_run heal rewrites an absolute (main-anchored) source_directory to relative."""
    base, R, R_db = main_repo
    _set_infra(R_db, "source_directory", os.path.join(R, "src"))
    _git(R, "add", "-A"); _git(R, "commit", "-qm", "set src")

    W = os.path.join(base, "wt1")
    _git(R, "worktree", "add", "--detach", W, "HEAD")
    W_db = get_project_db_path(W)
    assert _stored_source_dir(W_db) == os.path.join(R, "src")  # carries main's path

    monkeypatch.chdir(W)
    clear_project_root_cache()
    resolved = _discover_project_root()
    set_project_root(resolved)
    # source_directory heal runs BEFORE the project_root heal (uses still-stored main root)
    reconcile_stored_source_directory(resolved)
    _reconcile_stored_project_root(resolved)

    assert _stored_source_dir(W_db) == "src"          # now relative
    res = get_source_directory()
    assert res.data == os.path.join(W, "src")


def test_reconcile_source_dir_noop_when_relative(main_repo, monkeypatch):
    """Already-relative source_directory is not rewritten (normal single-tree case)."""
    base, R, R_db = main_repo
    _set_infra(R_db, "source_directory", "src")
    before = _stored_source_dir(R_db)

    monkeypatch.chdir(R)
    clear_project_root_cache()
    resolved = _discover_project_root()
    set_project_root(resolved)
    reconcile_stored_source_directory(resolved)

    assert _stored_source_dir(R_db) == before == "src"  # untouched
    res = get_source_directory()
    assert res.data == os.path.join(R, "src")
