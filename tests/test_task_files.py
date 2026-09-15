"""
task_files junction tests (project.db schema v1.12).

Covers:
- Schema: cascade on file delete, trigger cleanup on work-item delete
- Auto-linking from tracking helpers to the current in_progress work item
  (sidequest > subtask > task), and graceful no-op on a pre-v1.12 database
- link_files_to_task / unlink_files_from_task
- Readers: get_task_context, get_task_files, get_sidequest_files
- Semantic changeset export/apply of task_file edges
- Preflight numeric version comparison
"""
import os
import shutil
import sqlite3
import subprocess
import tempfile

import pytest

from aimfp.helpers.utils import set_project_root, clear_project_root_cache
from aimfp.helpers.project import tasks as T
from aimfp.helpers.project.files_1 import reserve_file, reserve_files
from aimfp.helpers.project.files_2 import update_file, update_file_timestamp
from aimfp.helpers.catalog.register import catalog_files, catalog_functions
from aimfp.helpers.project.task_files import (
    link_files_to_task,
    unlink_files_from_task,
    unique_file_ids,
)
from aimfp.helpers.project.tasks import get_task_files
from aimfp.helpers.project.subtasks_sidequests import get_sidequest_files
from aimfp.helpers.orchestrators.status import get_task_context, parse_flow_ids
from aimfp.helpers.changeset.export import export_state_changeset
from aimfp.helpers.changeset.apply import apply_state_changeset
from aimfp.helpers.changeset.preflight import version_tuple

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "aimfp", "database", "schemas", "project.sql"
)


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def project():
    """Temp project on the current schema with a milestone, flows, and three files."""
    root = tempfile.mkdtemp(prefix="aimfp_tf_test_")
    os.makedirs(os.path.join(root, ".aimfp-project"))
    db = os.path.join(root, ".aimfp-project", "project.db")
    conn = sqlite3.connect(db)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.execute("INSERT INTO completion_path (name, order_index) VALUES ('core', 1)")
    conn.execute("INSERT INTO flows (name, description) VALUES ('Flow A', 'a'), ('Flow B', 'b')")
    conn.executemany(
        "INSERT INTO files (path, name, language) VALUES (?, ?, 'python')",
        [("src/a.py", "a"), ("src/b.py", "b"), ("src/c.py", "c")],
    )
    conn.execute("INSERT INTO file_flows (file_id, flow_id) VALUES (2, 2)")
    conn.execute("INSERT INTO functions (file_id, name, purpose) VALUES (2, 'bee', 'b fn')")
    conn.commit()
    milestone = T._insert_milestone(conn, 1, "M", "in_progress", None)
    conn.close()
    clear_project_root_cache()
    set_project_root(root)
    yield root, db, milestone
    clear_project_root_cache()
    shutil.rmtree(root, ignore_errors=True)


def _conn(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _add_task(db, milestone, name, status="in_progress", flow_ids=None):
    c = _conn(db)
    tid = T._insert_task(c, milestone, name, status, "medium", None, flow_ids)
    c.close()
    return tid


def _links(db):
    c = _conn(db)
    rows = sorted(tuple(r) for r in c.execute(
        "SELECT reference_table, reference_id, file_id FROM task_files"))
    c.close()
    return rows


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_unique_file_ids_drops_none_and_repeats():
    assert unique_file_ids([3, None, 1, 3]) == (3, 1)


def test_parse_flow_ids_tolerates_bad_input():
    assert parse_flow_ids("[1, 2]") == (1, 2)
    assert parse_flow_ids(None) == ()
    assert parse_flow_ids("not json") == ()
    assert parse_flow_ids('{"a": 1}') == ()


def test_version_tuple_numeric_compare():
    assert version_tuple("1.9") < version_tuple("1.12")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_file_delete_cascades_and_task_delete_triggers(project):
    root, db, m = project
    tid = _add_task(db, m, "t")
    c = _conn(db)
    c.executemany("INSERT INTO task_files (reference_table, reference_id, file_id) VALUES ('tasks', ?, ?)",
                  [(tid, 1), (tid, 3)])
    c.execute("DELETE FROM files WHERE id = 1")
    c.commit()
    assert [r[0] for r in c.execute("SELECT file_id FROM task_files")] == [3]
    c.execute("DELETE FROM tasks WHERE id = ?", (tid,))
    c.commit()
    assert c.execute("SELECT COUNT(*) FROM task_files").fetchone()[0] == 0
    c.close()


# ---------------------------------------------------------------------------
# Auto-linking
# ---------------------------------------------------------------------------

def test_reserve_links_to_in_progress_task(project):
    root, db, m = project
    tid = _add_task(db, m, "t")
    r = reserve_file(name="new", path="src/new.py", language="python", project_root=root)
    assert r.success, r.error
    assert _links(db) == [("tasks", tid, r.id)]


def test_no_focus_means_no_link(project):
    root, db, m = project
    _add_task(db, m, "t", status="pending")
    r = reserve_files([("x", "src/x.py", "python", True)], project_root=root)
    assert r.success, r.error
    assert _links(db) == []


def test_sidequest_outranks_subtask_outranks_task(project):
    root, db, m = project
    tid = _add_task(db, m, "t")
    c = _conn(db)
    c.execute("INSERT INTO subtasks (parent_task_id, name, status) VALUES (?, 's', 'in_progress')", (tid,))
    c.commit()
    c.close()
    update_file(file_id=1, language="python3", project_root=root)
    c = _conn(db)
    c.execute("INSERT INTO sidequests (paused_task_id, name, status) VALUES (?, 'q', 'in_progress')", (tid,))
    c.commit()
    c.close()
    update_file(file_id=3, language="python3", project_root=root)
    assert _links(db) == [("sidequests", 1, 3), ("subtasks", 1, 1)]


def test_existing_file_work_links_via_timestamp_and_catalog(project):
    """The reported gap: work on already-tracked files must link to the task."""
    root, db, m = project
    tid = _add_task(db, m, "t")
    assert update_file_timestamp(2, project_root=root).success  # function/type finalize/update path
    r = catalog_files([{"name": "c", "path": "src/c.py", "language": "python"}], project_root=root)
    assert r.success, r.error
    r = catalog_functions([{"name": "ay", "file_id": 1, "purpose": "a", "parameters": [], "returns": {}}],
                          project_root=root)
    assert r.success, r.error
    assert _links(db) == [("tasks", tid, 1), ("tasks", tid, 2), ("tasks", tid, 3)]


def test_pre_v112_database_tracking_still_succeeds(project):
    root, db, m = project
    _add_task(db, m, "t")
    c = _conn(db)
    c.executescript(
        "DROP TRIGGER delete_task_task_files; DROP TRIGGER delete_subtask_task_files; "
        "DROP TRIGGER delete_sidequest_task_files; DROP TABLE task_files;")
    c.close()
    r = reserve_file(name="new", path="src/new.py", language="python", project_root=root)
    assert r.success, r.error
    assert update_file_timestamp(1, project_root=root).success


# ---------------------------------------------------------------------------
# Explicit link tools
# ---------------------------------------------------------------------------

def test_link_and_unlink(project):
    root, db, m = project
    tid = _add_task(db, m, "t", status="completed")
    r = link_files_to_task(tid, [1, 2, 2], project_root=root)
    assert r.success and r.added_count == 2 and r.skipped_count == 0
    r = link_files_to_task(tid, [1, 3], project_root=root)
    assert r.added_count == 1 and r.skipped_count == 1
    r = unlink_files_from_task(tid, [1, 99], project_root=root)
    assert r.success and r.removed_count == 1 and r.skipped_count == 1
    assert _links(db) == [("tasks", tid, 2), ("tasks", tid, 3)]


@pytest.mark.parametrize("kwargs, fragment", [
    ({"task_id": 1, "file_ids": []}, "empty"),
    ({"task_id": 1, "file_ids": [1], "task_type": "epic"}, "Invalid task_type"),
    ({"task_id": 42, "file_ids": [1]}, "not found"),
    ({"task_id": 1, "file_ids": [1, 77]}, "77"),
])
def test_link_validation(project, kwargs, fragment):
    root, db, m = project
    _add_task(db, m, "t", status="pending")
    r = link_files_to_task(project_root=root, **kwargs)
    assert not r.success and fragment in r.error


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def test_task_context_returns_linked_files_functions_and_flows(project):
    root, db, m = project
    tid = _add_task(db, m, "t", status="pending", flow_ids=[1])
    c = _conn(db)
    c.execute("INSERT INTO subtasks (parent_task_id, name, status) VALUES (?, 's', 'pending')", (tid,))
    c.commit()
    c.close()
    link_files_to_task(tid, [3], project_root=root)
    link_files_to_task(1, [2], task_type="subtask", project_root=root)

    r = get_task_context(tid, task_type="task")
    assert r.success, r.error
    assert [f["path"] for f in r.data["files"]] == ["src/c.py", "src/b.py"]
    assert [fn["name"] for fn in r.data["functions"]] == ["bee"]
    assert [fl["name"] for fl in r.data["flows"]] == ["Flow A", "Flow B"]

    sub = get_task_context(1, task_type="subtask")
    assert [f["path"] for f in sub.data["files"]] == ["src/b.py"]

    files = get_task_files(tid, project_root=root)
    assert files.success and [f.path for f in files.files] == ["src/c.py", "src/b.py"]


def test_sidequest_files(project):
    root, db, m = project
    tid = _add_task(db, m, "t", status="pending")
    c = _conn(db)
    c.execute("INSERT INTO sidequests (paused_task_id, name, status) VALUES (?, 'q', 'pending')", (tid,))
    c.commit()
    c.close()
    link_files_to_task(1, [1], task_type="sidequest", project_root=root)
    r = get_sidequest_files(1, project_root=root)
    assert r.success and [f.path for f in r.files] == ["src/a.py"]
    assert get_task_files(tid, project_root=root).files == ()


# ---------------------------------------------------------------------------
# Changeset merge
# ---------------------------------------------------------------------------

def test_changeset_exports_and_applies_task_file_edges(project):
    root, db, m = project
    _add_task(db, m, "t", status="pending")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "tester")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    base = _git(root, "rev-parse", "HEAD")
    main = "master" if "master" in _git(root, "branch") else "main"

    _git(root, "checkout", "-qb", "work")
    c = _conn(db)
    c.execute("UPDATE tasks SET slug = COALESCE(slug, 'task-t-00000001')")
    c.commit()
    c.close()
    link_files_to_task(1, [2], project_root=root)
    _git(root, "commit", "-qam", "work")

    exported = export_state_changeset(base, "work", worker_id="w1")
    assert exported.success, exported.error
    edges = [r for r in exported.data["references"] if r["kind"] == "task_file"]
    assert len(edges) == 1 and edges[0]["op"] == "add"
    assert edges[0]["reference_table"] == "tasks" and edges[0]["file"] == {"path": "src/b.py"}
    assert exported.data["warnings"] == []

    _git(root, "checkout", "-q", main)
    assert _links(db) == []
    applied = apply_state_changeset(exported.data)
    assert applied.success, applied.error
    assert applied.data["conflicts"] == []
    assert _links(db) == [("tasks", 1, 2)]


# ---------------------------------------------------------------------------
# Return statements reach the AI through the MCP dispatch path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, args", [
    ("search_notes", {"search_string": "hash"}),
    ("get_task_files", {"task_id": 1}),
    ("get_sidequest_files", {"sidequest_id": 1}),
    ("link_files_to_task", {"task_id": 1, "file_ids": [1]}),
])
def test_return_statements_delivered_via_dispatch(project, name, args):
    import json
    from aimfp.mcp_server.server import handle_call_tool
    root, db, m = project
    tid = _add_task(db, m, "t", status="pending")
    c = _conn(db)
    c.execute("INSERT INTO sidequests (paused_task_id, name, status) VALUES (?, 'q', 'pending')", (tid,))
    c.execute("INSERT INTO notes (content, note_type) VALUES ('hash note', 'decision')")
    c.commit()
    c.close()
    payload = json.loads(handle_call_tool(1, {"name": name, "arguments": args})["result"]["content"][0]["text"])
    assert payload["success"], payload
    assert payload["return_statements"], f"{name} delivered no return statements"
