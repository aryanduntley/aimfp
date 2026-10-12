"""Existing-tool fixes from the tracking-audit milestone (task 62)."""
import os
import sqlite3

import pytest

from aimfp.database.connection import clear_project_root_cache, set_project_root
from aimfp.helpers.catalog.reconcile import reconcile_paths
from aimfp.helpers.orchestrators.entry_points import (
    _link_changed_files_safe, aimfp_init, changed_tracked_paths,
)
from aimfp.helpers.project.functions_1 import get_function_by_name
from aimfp.helpers.project.functions_2 import delete_function, plan_edge_repoints
from aimfp.helpers.project.interactions import get_interactions_by_file, get_interactions_by_function
from aimfp.helpers.project.tasks import get_task_files
from aimfp.helpers.project.tasks import add_milestone, add_task
from aimfp.helpers.project.themes_flows_2 import add_completion_path


@pytest.fixture
def root(tmp_path):
    clear_project_root_cache()
    assert aimfp_init(str(tmp_path), init_git=False).success
    (tmp_path / "src").mkdir()
    set_project_root(str(tmp_path))
    yield str(tmp_path)
    clear_project_root_cache()


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _q(root, sql, params=()):
    conn = sqlite3.connect(os.path.join(root, ".aimfp-project", "project.db"))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture
def graph(root):
    _write(root, "src/a.py", "def helper():\n    return 1\n\n\ndef old(x):\n    return helper() + x\n\n\n"
                             "def new(x):\n    return helper() + x\n")
    _write(root, "src/b.py", "from .a import old\n\n\ndef caller():\n    return old(1)\n\n\ndef helper():\n    return 2\n")
    reconcile_paths(["src/a.py", "src/b.py"], project_root=root)
    ids = {(name, path): fid for fid, name, path in _q(
        root, "SELECT f.id, f.name, fi.path FROM functions f JOIN files fi ON fi.id = f.file_id")}
    return root, ids


# ============================================================================
# delete_function
# ============================================================================

def test_delete_function_refuses_and_lists_edges(graph):
    root, ids = graph
    r = delete_function(ids[("old", "src/a.py")], "gone", "info", "ai", project_root=root)
    assert not r.success and r.error.startswith("interactions_exist")
    assert r.interaction_count == 2 and len(r.interactions) == 2
    assert _q(root, "SELECT COUNT(*) FROM functions WHERE id = ?", (ids[("old", "src/a.py")],)) == [(1,)]


def test_delete_function_repoints_edges_to_successor(graph):
    root, ids = graph
    old, new = ids[("old", "src/a.py")], ids[("new", "src/a.py")]
    r = delete_function(old, "renamed to new", "info", "ai", repoint_to=new, project_root=root)
    assert r.success and r.interaction_count == 2
    edges = set(_q(root, "SELECT source_function_id, target_function_id FROM interactions"))
    assert (ids[("caller", "src/b.py")], new) in edges                       # caller -> new (moved)
    assert (new, ids[("helper", "src/a.py")]) in edges                       # new -> helper (already there: dropped dup)
    note, = _q(root, "SELECT content FROM notes WHERE reference_table = 'functions' AND reference_id = ?", (old,))[0]
    assert "Interactions repointed to" in note and "removed with the function" in note


def test_delete_function_confirm_cascade_records_rows(graph):
    root, ids = graph
    old = ids[("old", "src/a.py")]
    r = delete_function(old, "dead code", "info", "ai", confirm_cascade=True, project_root=root)
    assert r.success and len(r.interactions) == 2
    note, = _q(root, "SELECT content FROM notes WHERE reference_id = ? AND reference_table = 'functions'", (old,))[0]
    assert "removed with the function (full rows)" in note
    assert not delete_function(ids[("new", "src/a.py")], "x", "info", "ai", repoint_to=10_000,
                               project_root=root).success


def test_plan_edge_repoints():
    edges = ({"id": 1, "source_function_id": 5, "target_function_id": 7, "interaction_type": "call"},
             {"id": 2, "source_function_id": 9, "target_function_id": 5, "interaction_type": "call"},
             {"id": 3, "source_function_id": 6, "target_function_id": 5, "interaction_type": "call"})
    moves, drops = plan_edge_repoints(edges, 5, 6, frozenset({(9, 6, "call")}))
    assert [(m["id"], m["source_function_id"], m["target_function_id"]) for m in moves] == [(1, 6, 7)]
    assert [d["id"] for d in drops] == [2, 3]     # duplicate, then self-edge


# ============================================================================
# Qualified lookups and batch interaction reads
# ============================================================================

def test_get_function_by_name_narrows_by_file(graph):
    root, ids = graph
    assert get_function_by_name("helper", project_root=root).total_count == 2
    only_b = get_function_by_name("helper", file_path="./src/b.py", project_root=root)
    assert [f.id for f in only_b.functions] == [ids[("helper", "src/b.py")]]
    file_a = _q(root, "SELECT id FROM files WHERE path = 'src/a.py'")[0][0]
    assert [f.id for f in get_function_by_name("helper", file_id=file_a, project_root=root).functions] == [
        ids[("helper", "src/a.py")]]


def test_get_interactions_by_function_by_id_or_path(graph):
    root, ids = graph
    by_name = get_interactions_by_function("helper", project_root=root).interactions
    by_id = get_interactions_by_function(function_id=ids[("helper", "src/b.py")], project_root=root).interactions
    by_path = get_interactions_by_function("helper", file_path="src/a.py", project_root=root).interactions
    assert len(by_name) == 2 and by_id == () and len(by_path) == 2
    assert not get_interactions_by_function(project_root=root).success


def test_get_interactions_by_file_batch(graph):
    root, _ = graph
    a, b = (_q(root, "SELECT id FROM files WHERE path = ?", (p,))[0][0] for p in ("src/a.py", "src/b.py"))
    both = get_interactions_by_file(file_ids=[a, b], project_root=root).interactions
    assert len(both) == len({i.id for i in both}) == 3
    assert {(i.source_file_path, i.source_function_name, i.target_file_path, i.target_function_name) for i in both} >= {
        ("src/b.py", "caller", "src/a.py", "old")}
    assert len(get_interactions_by_file(b, project_root=root).interactions) == 1
    assert not get_interactions_by_file(project_root=root).success


# ============================================================================
# Checkpoint linking of changed files
# ============================================================================

def test_changed_tracked_paths():
    reminders = ({"type": "timestamp_synced", "file": "a.py"}, {"type": "new_file_detected", "file": "n.py"},
                 {"type": "audit_drift", "file": "a.py"}, {"type": "structure_gaps", "file": ""},
                 {"type": "missing_function", "file": "b.py"})
    assert changed_tracked_paths(reminders) == ("a.py", "b.py")


def test_checkpoint_links_changed_files_to_focus(graph):
    root, _ = graph
    path_id = add_completion_path("p", project_root=root).id
    task_id = add_task(add_milestone(path_id, "m", project_root=root).id, "t", status="in_progress",
                       project_root=root).id
    _link_changed_files_safe(root, ({"type": "timestamp_synced", "file": "src/b.py"},
                                    {"type": "timestamp_synced", "file": "untracked.py"}))
    assert [f.path for f in get_task_files(task_id, project_root=root).files] == ["src/b.py"]
